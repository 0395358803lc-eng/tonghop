import { errorCodeVi, gateCheckVi, pipelineStatusVi } from './filmVi'
import type { FilmPipelineGate, FilmPipelineGateError, FilmPipelineStatus } from './types'

type Props = {
  status: FilmPipelineStatus | null
  liveGate?: FilmPipelineGate | null
  now?: number
}

function elapsed(started?: string | null, now = Date.now()) {
  if (!started) return '—'
  const ms = now - Date.parse(started)
  if (!Number.isFinite(ms) || ms < 0) return '—'
  const s = Math.floor(ms / 1000)
  const mm = String(Math.floor(s / 60)).padStart(2, '0')
  const ss = String(s % 60).padStart(2, '0')
  return `${mm}:${ss}`
}

function asGate(value: unknown): FilmPipelineGate | null {
  if (!value || typeof value !== 'object') return null
  return value as FilmPipelineGate
}

const PRIMARY = ['story_bible_locked', 'canonical_complete', 'canonical_qc_pass', 'flow_authenticated', 'flow_video_available', 'qc_provider_available', 'scene_ordering_valid', 'scene_dependencies_valid']

function GateRow({ title, gate, empty }: { title: string; gate: FilmPipelineGate | null; empty: string }) {
  const checks = gate?.checks || {}
  const errors: FilmPipelineGateError[] = gate?.errors || []
  const keys = Object.keys(checks).length ? Array.from(new Set([...PRIMARY, ...Object.keys(checks)])) : []
  return (
    <div className="film-ccc-gate-block">
      <span className="film-ccc-gate-label">{title}</span>
      {keys.length ? (
        <div className="film-ccc-gate">
          {keys.map(key => (
            <span key={`${title}-${key}`} className={checks[key] ? 'pass' : 'fail'}>{gateCheckVi(key)} · {checks[key] ? 'PASS' : 'FAIL'}</span>
          ))}
        </div>
      ) : <p className="film-ccc-empty">{empty}</p>}
      {errors.length ? (
        <ul className="film-pipeline-errors">
          {errors.slice(0, 8).map((item, index) => (
            <li key={`${title}-${item.scene}-${item.code}-${index}`}>{item.scene}: {errorCodeVi(item.code, item.detail)}</li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

export default function FilmPipelineRunSummary({ status, liveGate, now }: Props) {
  const run = status?.run
  const snapshot = asGate(status?.run_gate) || asGate(status?.gate) || asGate((run?.gate as FilmPipelineGate) || null)
  const live = asGate(liveGate)
  const scene = status?.scenes.find(item => item.scene_id === run?.current_scene_id)
  return (
    <div className="film-ccc-run">
      <div className="film-ccc-run-meta">
        <span>Pipeline: <strong>{pipelineStatusVi(run?.status || 'idle')}</strong></span>
        <span>Current: <strong>{run?.current_scene_id || '—'}</strong></span>
        <span>Stage: <strong>{pipelineStatusVi(scene?.status || run?.status)}</strong></span>
        <span>Attempt: <strong>{scene ? `${Number(scene.attempt || 0) + 1}/${Number(scene.max_retries || 2) + 1}` : '—'}</strong></span>
        <span>Elapsed: <strong>{elapsed(run?.started_at, now)}</strong></span>
      </div>
      <GateRow title="LIVE PREFLIGHT" gate={live} empty="Đang đọc Production Gate sống." />
      <GateRow title="LAST RUN GATE SNAPSHOT" gate={snapshot} empty="Chưa có snapshot gate của run. Snapshot chỉ dùng để audit." />
    </div>
  )
}
