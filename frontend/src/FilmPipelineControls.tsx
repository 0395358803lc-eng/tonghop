import { useEffect, useState } from 'react'
import { Loader2, Pause, Play, RotateCcw, Square } from 'lucide-react'
import { api } from './api'
import { pipelineStatusVi, uiErrorVi } from './filmVi'
import type { FilmPipelineStatus, FilmScene } from './types'

type Props = {
  projectId: string
  status?: FilmPipelineStatus | null
  scenes?: FilmScene[]
  onStatus?: (next: FilmPipelineStatus) => void
  onRefresh?: () => void
}

export default function FilmPipelineControls({ projectId, status: statusProp, scenes, onStatus, onRefresh }: Props) {
  const [local, setLocal] = useState<FilmPipelineStatus | null>(statusProp || null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [fromScene, setFromScene] = useState('')
  const [retryId, setRetryId] = useState('')
  const status = statusProp ?? local

  useEffect(() => {
    if (statusProp !== undefined) {
      setLocal(statusProp)
      return
    }
    let cancelled = false
    const tick = async () => {
      try {
        const next = await api.filmPipelineStatus(projectId)
        if (!cancelled) setLocal(next)
      } catch {
        if (!cancelled) setLocal(null)
      }
    }
    tick()
    const id = window.setInterval(tick, 3000)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [projectId, statusProp])

  async function apply(next: FilmPipelineStatus) {
    setLocal(next)
    onStatus?.(next)
    onRefresh?.()
  }

  async function run(action: 'start' | 'pause' | 'resume' | 'stop' | 'continue' | 'retry') {
    setBusy(true)
    setError('')
    try {
      let next: FilmPipelineStatus
      if (action === 'start') next = await api.startFilmPipeline(projectId)
      else if (action === 'pause') next = await api.pauseFilmPipeline(projectId)
      else if (action === 'resume') next = await api.resumeFilmPipeline(projectId)
      else if (action === 'stop') next = await api.stopFilmPipeline(projectId)
      else if (action === 'continue') {
        if (!fromScene) throw new Error('Chọn scene để tiếp tục.')
        next = await api.startFilmPipeline(projectId, { from_scene_id: fromScene })
      } else {
        if (!retryId) throw new Error('Chọn scene QC thất bại để retry.')
        next = await api.retryFilmPipeline(projectId, retryId)
      }
      await apply(next)
    } catch (e) {
      setError(uiErrorVi((e as Error).message))
    } finally {
      setBusy(false)
    }
  }

  const runState = status?.run?.status || 'idle'
  const pipelineScenes = status?.scenes || []
  const retryable = pipelineScenes.filter(scene => {
    if (scene.status === 'QC_FAILED') return true
    const blob = `${scene.error || ''} ${scene.blocked_reason || ''}`
    return scene.status === 'BLOCKED' && (blob.includes('QC V2 failed') || blob.includes('QC_FAILED') || blob.includes('hard='))
  })
  const continueScenes = (scenes || []).slice().sort((a, b) => a.scene_index - b.scene_index)

  return (
    <section className="film-pipeline-panel film-ccc-controls">
      <div className="film-pipeline-head">
        <div>
          <h3>Điều khiển pipeline</h3>
          <p>Backend điều phối tuần tự. Pause không kill job đang chạy. Stop chỉ dừng sau scene hiện tại.</p>
        </div>
        <div className="film-pipeline-actions">
          <button type="button" className="primary" disabled={busy || runState === 'running'} onClick={() => run('start')}>
            {busy ? <Loader2 className="spin" size={16} /> : <Play size={16} />} Bắt đầu
          </button>
          <button type="button" disabled={busy || runState !== 'running'} onClick={() => run('pause')}>
            <Pause size={16} /> Tạm dừng
          </button>
          <button type="button" disabled={busy || runState !== 'paused'} onClick={() => run('resume')}>
            <Play size={16} /> Tiếp tục
          </button>
          <button type="button" disabled={busy || (runState !== 'running' && runState !== 'paused')} onClick={() => run('stop')}>
            <Square size={16} /> Dừng sau cảnh hiện tại
          </button>
        </div>
      </div>
      <div className="film-ccc-control-row">
        <label>
          Tiếp tục từ scene
          <select value={fromScene} onChange={e => setFromScene(e.target.value)}>
            <option value="">Chọn scene</option>
            {continueScenes.map(scene => (
              <option key={scene.id} value={scene.id}>{scene.id} · {scene.title || 'Không tiêu đề'}</option>
            ))}
          </select>
        </label>
        <button type="button" disabled={busy || !fromScene || runState === 'running'} onClick={() => run('continue')}>Continue from scene</button>
        <label>
          Retry scene lỗi QC
          <select value={retryId} onChange={e => setRetryId(e.target.value)}>
            <option value="">Chọn scene</option>
            {retryable.map(scene => (
              <option key={scene.scene_id} value={scene.scene_id}>{scene.scene_id} · {pipelineStatusVi(scene.status)}</option>
            ))}
          </select>
        </label>
        <button type="button" disabled={busy || !retryId} onClick={() => run('retry')}><RotateCcw size={14} /> Retry failed scene</button>
      </div>
      {error ? <div className="film-error">{error}</div> : null}
    </section>
  )
}
