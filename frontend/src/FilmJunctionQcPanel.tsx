import { pipelineStatusVi, qcDimensionVi } from './filmVi'
import type { FilmJunction } from './types'

type Props = {
  item?: FilmJunction | null
}

export default function FilmJunctionQcPanel({ item }: Props) {
  if (!item) return <div className="film-ccc-empty">Chọn một junction để xem evidence.</div>
  const report = item.qc || {}
  const dims = report.dimensions || {}
  const evidence = report.evidence || {}
  return (
    <div className="film-junction-qc">
      <div className="film-ccc-qc-head">
        <strong>{item.previous_scene_id} → {item.next_scene_id}</strong>
        <span>{pipelineStatusVi(item.status)}</span>
        {item.score != null ? <em>{Math.round(Number(item.score))}/100</em> : null}
      </div>
      <div className="film-ccc-qc-grid">
        {Object.keys(dims).map(name => {
          const dim = dims[name]
          const pass = dim?.passed === true
          const uneval = dim?.status === 'not_evaluated' || dim?.passed == null
          return (
            <article key={name} className={`film-ccc-qc-dim is-${uneval ? 'not-evaluated' : pass ? 'pass' : 'fail'}`}>
              <span>{qcDimensionVi(name)}</span>
              <strong>{dim?.score == null ? '—' : Math.round(Number(dim.score))}</strong>
              <em>{dim?.hard ? 'HARD GATE' : 'SOFT'} · {uneval ? 'NOT EVALUATED' : pass ? 'PASS' : 'FAIL'}</em>
            </article>
          )
        })}
      </div>
      <div className="film-ccc-evidence">
        {evidence.previous_last_frame ? <img src={String(evidence.previous_last_frame)} alt="Last frame cảnh trước" /> : <div className="film-ccc-empty">Chưa có last frame.</div>}
        {evidence.next_first_frame ? <img src={String(evidence.next_first_frame)} alt="First frame cảnh sau" /> : <div className="film-ccc-empty">Chưa có first frame.</div>}
      </div>
      <dl className="film-ccc-meta">
        <div><dt>Vision</dt><dd>{String(evidence.vision_provider || report.vision?.provider || '—')} / {String(evidence.vision_model || report.vision?.model || '—')}</dd></div>
        <div><dt>Repair</dt><dd>{report.repair?.scene_id || item.repair_target_scene || '—'}</dd></div>
      </dl>
      {item.error ? <p>{item.error}</p> : null}
    </div>
  )
}
