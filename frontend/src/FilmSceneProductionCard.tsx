import { pipelineStatusVi, qcStatusVi } from './filmVi'
import type { FilmGeneratedMedia, FilmScene, FilmSceneLedger, FilmScenePipelineState } from './types'

type Props = {
  scene: FilmScene
  state?: FilmScenePipelineState
  ledger?: FilmSceneLedger
  media?: FilmGeneratedMedia[]
  current?: boolean
  onOpen: () => void
}

function qcOf(state?: FilmScenePipelineState) {
  const raw = state?.qc
  if (!raw || typeof raw !== 'object') return { score: state?.best_score, identity: null as number | null, boundary: null as number | null, speech: null as number | null, status: '' }
  const report = ('dimensions' in raw ? raw : (raw as { qc?: Record<string, unknown> }).qc) as Record<string, unknown> | undefined
  const dims = (report?.dimensions || {}) as Record<string, { score?: number | null }>
  return {
    score: (report?.consistency_score as number | undefined) ?? state?.best_score,
    identity: dims.identity?.score ?? null,
    boundary: dims.boundary?.score ?? null,
    speech: dims.speech?.score ?? null,
    status: String(report?.qc_status || ''),
  }
}

export default function FilmSceneProductionCard({ scene, state, ledger, media = [], current, onOpen }: Props) {
  const selected = media.find(item => item.id === state?.selected_media_id) || media.find(item => item.is_selected)
  const qc = qcOf(state)
  const status = state?.status || 'LOCKED'
  return (
    <button type="button" className={`film-ccc-card is-${status.toLowerCase()} ${current ? 'current' : ''}`} onClick={onOpen}>
      <header>
        <span>{scene.id}</span>
        <strong>{pipelineStatusVi(status)}</strong>
      </header>
      <b>{scene.title || `Cảnh ${scene.scene_index}`}</b>
      <div className="film-ccc-card-thumb">
        {selected?.thumbnail_url || (selected?.media_type === 'image' && selected.file_url) ? (
          <img src={selected.thumbnail_url || selected.file_url || ''} alt={scene.id} />
        ) : <em>Chưa có video đang dùng</em>}
      </div>
      <dl>
        <div><dt>QC</dt><dd>{qc.score != null ? Math.round(Number(qc.score)) : '—'} {qc.status ? `· ${qcStatusVi(qc.status)}` : ''}</dd></div>
        <div><dt>Identity</dt><dd>{qc.identity != null ? Math.round(Number(qc.identity)) : '—'}</dd></div>
        <div><dt>Boundary</dt><dd>{qc.boundary != null ? Math.round(Number(qc.boundary)) : '—'}</dd></div>
        <div><dt>Speech</dt><dd>{qc.speech != null ? Math.round(Number(qc.speech)) : '—'}</dd></div>
        <div><dt>Retry</dt><dd>{Number(state?.attempt || 0)}/{Number(state?.max_retries || 2)}</dd></div>
        <div><dt>Best</dt><dd>{state?.best_media_id ? state.best_media_id.slice(0, 8) : '—'}</dd></div>
        <div><dt>Warnings</dt><dd>{scene.warnings?.length || 0}</dd></div>
        <div><dt>Last frame</dt><dd>{ledger?.accepted_last_frame ? 'Có' : 'Chưa'}</dd></div>
      </dl>
      {state?.blocked_reason || state?.error ? <p>{state?.blocked_reason || state?.error}</p> : null}
    </button>
  )
}
