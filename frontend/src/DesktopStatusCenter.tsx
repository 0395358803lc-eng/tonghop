import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Activity, Bot, CircleAlert, Clapperboard, Cpu, Database, FolderOpen, Gauge, HardDrive, Image as ImageIcon, MemoryStick, Mic2, Trash2, Wifi, WifiOff, Workflow } from 'lucide-react'
import { invoke } from '@tauri-apps/api/core'
import { open } from '@tauri-apps/plugin-dialog'
import { api } from './api'
import { check, type Update } from '@tauri-apps/plugin-updater'
import { getRuntimeConfig } from './runtime'
import type { DesktopReadyStatus, Provider } from './types'

type Props = {
  provider?: Provider
  model?: string
}

type StatusRowProps = {
  icon: ReactNode
  label: string
  value: string
  state: 'ok' | 'warn' | 'error' | 'muted'
  title?: string
}

function formatModels(models?: string[]) {
  const items = (models || []).filter(Boolean)
  if (!items.length) return 'Chưa có'
  if (items.length <= 2) return items.join(', ')
  return `${items.slice(0, 2).join(', ')} +${items.length - 2}`
}

function StatusRow({ icon, label, value, state, title }: StatusRowProps) {
  return (
    <div className="desktop-status-row" title={title}>
      <span className="desktop-status-icon">{icon}</span>
      <span className="desktop-status-label">{label}</span>
      <span className={`desktop-status-value ${state}`}>{value}</span>
    </div>
  )
}

export default function DesktopStatusCenter({ provider, model }: Props) {
  const desktopMode = Boolean(getRuntimeConfig().backendBaseUrl)
  const [status, setStatus] = useState<DesktopReadyStatus | null>(null)
  const [network, setNetwork] = useState<Awaited<ReturnType<typeof api.desktopNetwork>> | null>(null)
  const [resources, setResources] = useState<Awaited<ReturnType<typeof api.desktopResources>> | null>(null)
  const [unreachable, setUnreachable] = useState(false)
  const [diagnosticsBusy, setDiagnosticsBusy] = useState(false)
  const [diagnosticsMessage, setDiagnosticsMessage] = useState('')
  const [tempBusy, setTempBusy] = useState(false)
  const [tempMessage, setTempMessage] = useState('')
  const [mediaBusy, setMediaBusy] = useState(false)
  const [mediaMessage, setMediaMessage] = useState('')
  const [updateBusy, setUpdateBusy] = useState(false)
  const [updateMessage, setUpdateMessage] = useState('')
  const [updateVersion, setUpdateVersion] = useState('')
  const [updateNotes, setUpdateNotes] = useState('')
  const updateRef = useRef<Update | null>(null)

  useEffect(() => {
    if (!desktopMode) return

    let cancelled = false
    const refresh = async () => {
      try {
        const [next, networkStatus, resourceStatus] = await Promise.all([api.ready(), api.desktopNetwork(), api.desktopResources()])
        if (!cancelled) {
          setStatus(next)
          setNetwork(networkStatus)
          setResources(resourceStatus)
          setUnreachable(false)
        }
      } catch {
        if (!cancelled) setUnreachable(true)
      }
    }

    const initialTimer = window.setTimeout(() => void refresh(), 0)
    const interval = window.setInterval(() => void refresh(), 15000)
    return () => {
      cancelled = true
      window.clearTimeout(initialTimer)
      window.clearInterval(interval)
    }
  }, [desktopMode])

  useEffect(() => () => {
    if (updateRef.current) void updateRef.current.close().catch(() => undefined)
  }, [])

  if (!desktopMode) return null

  const checks = status?.checks
  const storage = resources?.storage || status?.storage
  const storageLow = typeof storage?.free_gb === 'number' && storage.free_gb < 15
  const cpu = resources?.cpu
  const memory = resources?.memory
  const gpu = resources?.gpus?.[0]
  const temp = resources?.temp
  const activePipelines = status?.runtime?.active_pipelines || 0
  const aiReady = Boolean(provider?.configured)
  const flowProjectUsable = Boolean(status?.flow?.session?.project_usable)
  const videoModels = status?.capabilities?.video_models || []
  const imageModels = status?.capabilities?.image_models || []
  const networkOnline = network?.online !== false
  const overallState = unreachable ? 'error' : network?.online === false ? 'warn' : status?.ready ? 'ok' : status ? 'warn' : 'muted'
  const overallLabel = unreachable ? 'Mất backend' : network?.online === false ? 'Offline · Local OK' : status?.ready ? 'Sẵn sàng' : status ? 'Cần kiểm tra' : 'Đang kiểm tra'

  const cleanupTemp = async () => {
    if (tempBusy) return
    setTempBusy(true)
    setTempMessage('')
    try {
      const result = await api.cleanupDesktopTemp()
      setTempMessage(`Đã dọn ${result.deleted_files} file · giải phóng ${result.deleted_gb} GB.`)
      setResources(await api.desktopResources())
    } catch (error) {
      setTempMessage(`Không thể dọn temp: ${(error as Error).message}`)
    } finally {
      setTempBusy(false)
    }
  }

  const chooseMediaDirectory = async () => {
    if (mediaBusy || activePipelines > 0) return
    setMediaBusy(true)
    setMediaMessage('')
    try {
      const selected = await open({ directory: true, multiple: false, title: 'Chọn thư mục Media cho TH Media' })
      if (!selected || Array.isArray(selected)) return
      const saved = await invoke<string>('set_media_directory', { path: selected })
      setMediaMessage(`Đã lưu Media tại ${saved}. TH Media sẽ khởi động lại để áp dụng.`)
      window.setTimeout(() => { void invoke('restart_th_media') }, 700)
    } catch (error) {
      setMediaMessage(`Không thể đổi thư mục Media: ${(error as Error).message}`)
    } finally {
      setMediaBusy(false)
    }
  }

  const exportDiagnostics = async () => {
    if (diagnosticsBusy) return
    setDiagnosticsBusy(true)
    setDiagnosticsMessage('')
    try {
      const result = await api.exportDesktopDiagnostics()
      const sizeMb = (result.size_bytes / (1024 * 1024)).toFixed(2)
      setDiagnosticsMessage(`Đã tạo ${result.filename} (${sizeMb} MB) trong thư mục Diagnostics.`)
    } catch (error) {
      setDiagnosticsMessage(`Không thể xuất chẩn đoán: ${(error as Error).message}`)
    } finally {
      setDiagnosticsBusy(false)
    }
  }

  const checkForUpdate = async () => {
    if (updateBusy) return
    setUpdateBusy(true)
    setUpdateMessage('')
    try {
      const gate = await api.desktopUpdateStatus()
      if (!gate.can_update) {
        setUpdateMessage(`Đang có ${gate.active_pipelines} pipeline hoạt động. Hãy chờ hoàn tất trước khi cập nhật.`)
        return
      }
      if (updateRef.current) await updateRef.current.close().catch(() => undefined)
      const update = await check({ timeout: 15000 })
      updateRef.current = update
      if (!update) {
        setUpdateVersion('')
        setUpdateNotes('')
        setUpdateMessage('TH Media đang ở phiên bản mới nhất.')
        return
      }
      setUpdateVersion(update.version)
      setUpdateNotes(update.body || '')
      setUpdateMessage(`Có phiên bản ${update.version} sẵn sàng.`)
    } catch (error) {
      setUpdateMessage(`Không thể kiểm tra cập nhật: ${(error as Error).message}`)
    } finally {
      setUpdateBusy(false)
    }
  }

  const installUpdate = async () => {
    const update = updateRef.current
    if (!update || updateBusy) return
    setUpdateBusy(true)
    setUpdateMessage('Đang chuẩn bị backup trước cập nhật...')
    try {
      await api.prepareDesktopUpdate()
      let downloaded = 0
      let total = 0
      await update.download(event => {
        if (event.event === 'Started') total = event.data.contentLength || 0
        if (event.event === 'Progress') downloaded += event.data.chunkLength
        if (event.event === 'Progress' && total > 0) {
          setUpdateMessage(`Đang tải bản ${update.version}: ${Math.min(100, Math.round((downloaded / total) * 100))}%`)
        }
      })
      setUpdateMessage('Đã tải xong. TH Media sẽ cài đặt và khởi động lại.')
      await update.install({ restartAfterInstall: true })
    } catch (error) {
      setUpdateMessage(`Cập nhật thất bại: ${(error as Error).message}`)
      setUpdateBusy(false)
    }
  }

  return (
    <section className="desktop-status-center" aria-label="Trạng thái hệ thống Desktop">
      <div className="desktop-status-head">
        <span><Activity size={12} /> HỆ THỐNG DESKTOP</span>
        <b className={overallState}>{overallLabel}</b>
      </div>

      <StatusRow
        icon={<Gauge size={12} />}
        label="Backend"
        value={unreachable ? 'Mất kết nối' : status?.health ? 'Hoạt động' : 'Đang kiểm tra'}
        state={unreachable ? 'error' : status?.health ? 'ok' : 'muted'}
      />
      <StatusRow
        icon={network?.online === false ? <WifiOff size={12} /> : <Wifi size={12} />}
        label="Internet"
        value={network ? (network.online ? 'Đã kết nối' : 'Chưa kết nối Internet') : 'Đang kiểm tra'}
        state={network ? (network.online ? 'ok' : 'warn') : 'muted'}
        title={network ? `${network.probe || 'không có probe'} · ${network.latency_ms} ms` : undefined}
      />
      <StatusRow
        icon={<Database size={12} />}
        label="Database"
        value={checks?.db ? 'Sẵn sàng' : status ? 'Lỗi' : '...'}
        state={checks?.db ? 'ok' : status ? 'error' : 'muted'}
      />
      <StatusRow
        icon={<Workflow size={12} />}
        label="Google Flow"
        value={!networkOnline
          ? 'Chưa kết nối Internet'
          : checks?.flow_authenticated
            ? (flowProjectUsable ? 'Đã đăng nhập · Project OK' : 'Đã đăng nhập · Project chưa sẵn sàng')
            : checks?.flow_configured ? 'Cần đăng nhập' : 'Chưa sẵn sàng'}
        state={networkOnline && checks?.flow_authenticated && flowProjectUsable ? 'ok' : status ? 'warn' : 'muted'}
      />
      <StatusRow
        icon={<Bot size={12} />}
        label="AI"
        value={!networkOnline ? (aiReady ? 'Chờ Internet' : 'Chưa có key') : aiReady ? provider?.name || 'Đã cấu hình' : 'Chưa có key'}
        state={networkOnline && aiReady ? 'ok' : 'warn'}
        title={model ? `${provider?.name || 'AI'} · ${model}` : undefined}
      />
      <StatusRow
        icon={<Mic2 size={12} />}
        label="Speaker"
        value={checks?.speaker_verifier_ready ? 'Sẵn sàng' : status ? 'Chưa sẵn sàng' : '...'}
        state={checks?.speaker_verifier_ready ? 'ok' : status ? 'warn' : 'muted'}
      />
      <StatusRow
        icon={<Activity size={12} />}
        label="Capability"
        value={checks?.capability_matrix_fresh ? 'Mới' : status ? 'Cần làm mới' : '...'}
        state={checks?.capability_matrix_fresh ? 'ok' : status ? 'warn' : 'muted'}
      />
      <StatusRow
        icon={<Clapperboard size={12} />}
        label="Video model"
        value={formatModels(videoModels)}
        state={checks?.capability_matrix_fresh && videoModels.length ? 'ok' : status ? 'warn' : 'muted'}
        title={videoModels.length ? videoModels.join(' · ') : undefined}
      />
      <StatusRow
        icon={<ImageIcon size={12} />}
        label="Image model"
        value={formatModels(imageModels)}
        state={imageModels.length ? 'ok' : status ? 'warn' : 'muted'}
        title={imageModels.length ? imageModels.join(' · ') : undefined}
      />
      <StatusRow
        icon={<HardDrive size={12} />}
        label="Ổ Media"
        value={storage ? `${storage.free_gb} GB trống` : '...'}
        state={storage ? (storageLow ? 'warn' : 'ok') : 'muted'}
        title={resources?.storage ? `${resources.storage.media_dir} · đã dùng ${resources.storage.used_percent}%` : storage ? `Đã dùng ${storage.used_percent}% dung lượng ổ đĩa` : undefined}
      />
      <StatusRow
        icon={<Cpu size={12} />}
        label="CPU"
        value={cpu ? `${cpu.usage_percent}% · ${cpu.logical_cores} luồng` : '...'}
        state={cpu ? (cpu.usage_percent >= 95 ? 'warn' : 'ok') : 'muted'}
      />
      <StatusRow
        icon={<MemoryStick size={12} />}
        label="RAM"
        value={memory ? `${memory.available_gb} / ${memory.total_gb} GB trống` : '...'}
        state={memory ? (memory.available_gb < 4 ? 'warn' : 'ok') : 'muted'}
        title={memory ? `Đang dùng ${memory.used_percent}% RAM` : undefined}
      />
      <StatusRow
        icon={<Gauge size={12} />}
        label="GPU"
        value={gpu ? `${gpu.name} · ${gpu.utilization_percent}%` : 'Không phát hiện NVIDIA GPU'}
        state={gpu ? 'ok' : 'muted'}
        title={gpu ? `${Math.round(gpu.memory_free_mb)} / ${Math.round(gpu.memory_total_mb)} MB VRAM trống` : undefined}
      />
      <StatusRow
        icon={<Trash2 size={12} />}
        label="Temp"
        value={temp ? `${temp.size_gb} / ${temp.quota_gb} GB` : '...'}
        state={temp ? (temp.size_gb >= temp.quota_gb ? 'warn' : 'ok') : 'muted'}
        title={temp?.dir}
      />

      {(resources?.warnings?.length || resources?.blockers?.length) ? (
        <div className={`desktop-status-resource-alert ${resources?.blockers?.length ? 'error' : 'warn'}`}>
          <CircleAlert size={12} />
          <span>{[...(resources?.blockers || []), ...(resources?.warnings || [])].join(' · ')}</span>
        </div>
      ) : null}

      <div className="desktop-status-resource-actions">
        <button disabled={mediaBusy || unreachable || activePipelines > 0} onClick={chooseMediaDirectory}>
          <FolderOpen size={11} /> {mediaBusy ? 'Đang chọn thư mục...' : 'Đổi thư mục Media'}
        </button>
        <button disabled={tempBusy || unreachable} onClick={cleanupTemp}>
          <Trash2 size={11} /> {tempBusy ? 'Đang dọn temp...' : 'Dọn bộ nhớ tạm'}
        </button>
        {mediaMessage && <p>{mediaMessage}</p>}
        {tempMessage && <p>{tempMessage}</p>}
      </div>

      {activePipelines > 0 && (
        <div className="desktop-status-pipeline">
          <CircleAlert size={12} />
          <span>{activePipelines} pipeline đang chạy</span>
        </div>
      )}

      <div className="desktop-status-update">
        <div className="desktop-status-update-actions">
          <button disabled={updateBusy || unreachable} onClick={checkForUpdate}>
            {updateBusy && !updateVersion ? 'Đang kiểm tra...' : 'Kiểm tra cập nhật'}
          </button>
          {updateVersion && (
            <button className="primary" disabled={updateBusy || activePipelines > 0} onClick={installUpdate}>
              {updateBusy ? 'Đang cập nhật...' : `Cập nhật ${updateVersion}`}
            </button>
          )}
        </div>
        {updateMessage && <p>{updateMessage}</p>}
        {updateNotes && <details><summary>Ghi chú phát hành</summary><pre>{updateNotes}</pre></details>}
      </div>

      <div className="desktop-status-diagnostics">
        <button disabled={diagnosticsBusy || unreachable} onClick={exportDiagnostics}>
          {diagnosticsBusy ? 'Đang tạo gói chẩn đoán...' : 'Xuất gói chẩn đoán'}
        </button>
        {diagnosticsMessage && <p>{diagnosticsMessage}</p>}
      </div>
    </section>
  )
}
