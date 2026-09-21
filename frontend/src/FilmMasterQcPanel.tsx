import { pipelineStatusVi, qcDimensionVi } from './filmVi'
import type { FilmFinalStatus } from './types'

type Props = {
  status?: FilmFinalStatus | null
  busy?: boolean
  onRunQc: () => void
}

const MASTER_DIMS = [
  'missing_scene',
  'scene_order',
  'stale_scene',
  'black_frames',
  'audio_missing',
  'audio_gaps',
  'audio_clipping',
  'duration_integrity',
  'identity_continuity',
  'prop_continuity',
  'location_continuity',
  'dialogue_continuity',
  'broken_file',
]

export default function FilmMasterQcPanel({ status, busy, onRunQc }: Props) {
  const current = status?.current
  const media = current?.media
  const qc = current?.qc || {}
  const dims = qc.dimensions || {}
  const issues = qc.issues || []
  const hard = qc.hard_gate
  const filmStatus = current?.status || ''
  const canRun = filmStatus === 'QC_PENDING' || filmStatus === 'QC_FAILED'
  return (
    <section className="film-junctions film-master-qc">
      <div className="film-junctions-head">
        <div>
          <strong>Master QC</strong>
          <span>Final video chỉ được select khi Master QC PASS</span>
        </div>
        <button type="button" disabled={busy || !canRun} onClick={onRunQc}>
          {busy ? 'Đang Master QC…' : 'CHẠY MASTER QC'}
        </button>
      </div>
      <dl className="film-ccc-meta">
        <div><dt>Trạng thái</dt><dd>{current ? pipelineStatusVi(filmStatus) : 'Chưa có bản ghép'}</dd></div>
        <div><dt>Điểm tổng</dt><dd>{qc.overall_score == null ? '—' : Number(qc.overall_score).toFixed(1)}</dd></div>
        <div><dt>Hard gate</dt><dd>{hard ? (hard.passed ? 'PASS' : `FAIL · ${(hard.failed || []).join(', ') || '—'}`) : '—'}</dd></div>
        <div><dt>Selected</dt><dd>{media?.is_selected ? 'Đang dùng' : 'Chưa select'}</dd></div>
        <div><dt>QC media</dt><dd>{media?.qc_status || 'pending'}</dd></div>
        <div><dt>Version</dt><dd>{current?.version ?? '—'}</dd></div>
      </dl>
      <div className="film-ccc-qc-grid">
        {MASTER_DIMS.map(name => {
          const dim = dims[name]
          const pass = dim?.passed === true
          const uneval = !dim || dim?.status === 'not-evaluated' || dim?.status === 'not_evaluated' || dim?.passed == null
          return (
            <article key={name} className={`film-ccc-qc-dim is-${uneval ? 'not-evaluated' : pass ? 'pass' : 'fail'}`}>
              <span>{qcDimensionVi(name)}</span>
              <strong>{dim?.score == null ? '—' : Math.round(Number(dim.score))}</strong>
              <em>{dim?.hard ? 'HARD GATE' : 'SOFT'} · {uneval ? 'NOT EVALUATED' : pass ? 'PASS' : 'FAIL'}</em>
            </article>
          )
        })}
      </div>
      {issues.length ? (
        <ul className="film-pipeline-errors">
          {issues.slice(0, 12).map((item: any, index: number) => (
            <li key={`${item.code || item.type || index}`}>{String(item.code || item.type || 'ISSUE')}{item.detail ? ` · ${item.detail}` : ''}</li>
          ))}
        </ul>
      ) : null}
      {media?.file_url ? <video src={media.file_url} controls preload="metadata" /> : <p className="film-ccc-empty">Chưa có final_video để QC.</p>}
    </section>
  )
}
