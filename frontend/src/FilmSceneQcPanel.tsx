import { qcDimensionVi, qcStatusVi } from './filmVi'
import type { FilmQcDimension, FilmSceneQcReport } from './types'

const ORDER = ['identity', 'wardrobe', 'location', 'prop', 'boundary', 'camera', 'lighting', 'audio', 'speech']

type Props = {
  qc?: FilmSceneQcReport | Record<string, unknown> | null
}

function asReport(qc?: FilmSceneQcReport | Record<string, unknown> | null): FilmSceneQcReport {
  if (!qc || typeof qc !== 'object') return {}
  const nested = (qc as { qc?: FilmSceneQcReport }).qc
  if (nested && typeof nested === 'object' && (nested.dimensions || nested.hard_gate)) return nested
  return qc as FilmSceneQcReport
}

function dimClass(item?: FilmQcDimension) {
  if (!item || item.status === 'not_evaluated' || item.passed == null) return 'not-evaluated'
  if (item.passed === true || item.status === 'passed') return 'pass'
  return 'fail'
}

export default function FilmSceneQcPanel({ qc }: Props) {
  const report = asReport(qc)
  const dims = report.dimensions || {}
  const names = Array.from(new Set([...ORDER, ...Object.keys(dims)]))
  if (!names.length) return <div className="film-ccc-empty">Chưa có báo cáo QC V2 cho scene này.</div>
  return (
    <div className="film-ccc-qc">
      <div className="film-ccc-qc-head">
        <strong>QC V2</strong>
        <span>{report.qc_complete === false ? 'Chưa đủ dimension' : qcStatusVi(report.qc_status)}</span>
        {report.consistency_score != null ? <em>{Math.round(Number(report.consistency_score))}/100</em> : null}
      </div>
      <div className="film-ccc-qc-grid">
        {names.map(name => {
          const item = dims[name]
          const state = dimClass(item)
          const label = state === 'not-evaluated' ? 'NOT EVALUATED' : state === 'pass' ? 'PASS' : 'FAIL'
          return (
            <article key={name} className={`film-ccc-qc-dim is-${state}`}>
              <span>{qcDimensionVi(name)}</span>
              <strong>{item?.score == null ? '—' : Math.round(Number(item.score))}{item?.threshold != null ? ` / ${item.threshold}` : ''}</strong>
              <em>{item?.hard ? 'HARD GATE' : 'SOFT'} · {label}</em>
            </article>
          )
        })}
      </div>
    </div>
  )
}
