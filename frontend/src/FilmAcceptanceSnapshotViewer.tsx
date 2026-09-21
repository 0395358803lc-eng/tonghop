import { jsonTextVi } from './filmVi'
import type { FilmAcceptanceSnapshot } from './types'

type Props = {
  snapshots?: FilmAcceptanceSnapshot[]
  loading?: boolean
}

export default function FilmAcceptanceSnapshotViewer({ snapshots = [], loading }: Props) {
  const latest = snapshots[0]
  const payload = latest?.payload || {}
  if (loading) return <div className="film-ccc-empty">Đang tải snapshot…</div>
  if (!latest) return <div className="film-ccc-empty">Chưa có Acceptance Snapshot. Chỉ scene APPROVED hợp lệ mới được backfill.</div>
  return (
    <div className="film-snapshot-viewer">
      <dl className="film-ccc-meta">
        <div><dt>Snapshot</dt><dd>{latest.id}</dd></div>
        <div><dt>Hash</dt><dd>{latest.snapshot_hash}</dd></div>
        <div><dt>Tạo lúc</dt><dd>{latest.created_at || '—'}</dd></div>
        <div><dt>Media version</dt><dd>v{payload.selected_media_version ?? payload.scene_version ?? '—'}</dd></div>
        <div><dt>Selected media</dt><dd>{payload.selected_media_id || '—'}</dd></div>
        <div><dt>Voice profiles</dt><dd>{(payload.voice_requirements || []).length}</dd></div>
      </dl>
      <h4>Canonical versions</h4>
      <pre>{jsonTextVi(payload.canonical || [])}</pre>
      <h4>Audio contract</h4>
      <pre>{jsonTextVi(payload.audio_requirements || {})}</pre>
      <h4>QC summary</h4>
      <pre>{jsonTextVi(payload.qc || {})}</pre>
      <h4>Ledger</h4>
      <pre>{jsonTextVi(payload.ledger || {})}</pre>
      {snapshots.length > 1 ? (
        <>
          <h4>Lịch sử snapshot</h4>
          <ul className="film-ccc-versions">
            {snapshots.map(item => (
              <li key={item.id} className="film-ccc-version">
                <strong>{item.snapshot_hash?.slice(0, 12)}</strong>
                <span>{item.created_at}</span>
                <em>v{item.payload?.selected_media_version ?? '—'}</em>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </div>
  )
}
