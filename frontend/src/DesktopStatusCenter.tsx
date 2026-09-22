import { useEffect, useState, type ReactNode } from 'react'
import { Activity, Bot, CircleAlert, Database, Gauge, HardDrive, Mic2, Workflow } from 'lucide-react'
import { api } from './api'
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
  const [unreachable, setUnreachable] = useState(false)

  useEffect(() => {
    if (!desktopMode) return

    let cancelled = false
    const refresh = async () => {
      try {
        const next = await api.ready()
        if (!cancelled) {
          setStatus(next)
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

  if (!desktopMode) return null

  const checks = status?.checks
  const storage = status?.storage
  const storageLow = typeof storage?.free_gb === 'number' && storage.free_gb < 10
  const activePipelines = status?.runtime?.active_pipelines || 0
  const aiReady = Boolean(provider?.configured)
  const overallState = unreachable ? 'error' : status?.ready ? 'ok' : status ? 'warn' : 'muted'
  const overallLabel = unreachable ? 'Mất kết nối' : status?.ready ? 'Sẵn sàng' : status ? 'Cần kiểm tra' : 'Đang kiểm tra'

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
        icon={<Database size={12} />}
        label="Database"
        value={checks?.db ? 'Sẵn sàng' : status ? 'Lỗi' : '...'}
        state={checks?.db ? 'ok' : status ? 'error' : 'muted'}
      />
      <StatusRow
        icon={<Workflow size={12} />}
        label="Google Flow"
        value={checks?.flow_authenticated ? 'Đã đăng nhập' : checks?.flow_configured ? 'Cần đăng nhập' : 'Chưa sẵn sàng'}
        state={checks?.flow_authenticated ? 'ok' : status ? 'warn' : 'muted'}
      />
      <StatusRow
        icon={<Bot size={12} />}
        label="AI"
        value={aiReady ? provider?.name || 'Đã cấu hình' : 'Chưa có key'}
        state={aiReady ? 'ok' : 'warn'}
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
        icon={<HardDrive size={12} />}
        label="Ổ đĩa"
        value={storage ? `${storage.free_gb} GB trống` : '...'}
        state={storage ? (storageLow ? 'warn' : 'ok') : 'muted'}
        title={storage ? `Đã dùng ${storage.used_percent}% dung lượng ổ đĩa` : undefined}
      />

      {activePipelines > 0 && (
        <div className="desktop-status-pipeline">
          <CircleAlert size={12} />
          <span>{activePipelines} pipeline đang chạy</span>
        </div>
      )}
    </section>
  )
}
