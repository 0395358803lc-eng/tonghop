import { pipelineStatusVi } from './filmVi'
import type { FilmFinalStatus } from './types'

type Props = {
  status?: FilmFinalStatus | null
  busy?: boolean
  onAssemble: () => void
}

const ERROR_VI: Record<string, string> = {
  FINAL_ASSEMBLY_BLOCKED: 'Chưa đủ điều kiện ghép phim cuối',
  SCENE_NOT_APPROVED: 'Scene chưa APPROVED',
  SCENE_STALE: 'Scene lỗi thời',
  SCENE_BLOCKED: 'Scene bị chặn',
  SELECTED_MEDIA_REQUIRED: 'Thiếu selected scene video',
  SELECTED_MEDIA_INVALID: 'Selected media chưa completed/QC passed',
  SELECTED_MEDIA_FILE_MISSING: 'File local của selected media không tồn tại',
  JUNCTION_NOT_PASS: 'Junction chưa PASS',
  JUNCTION_STALE: 'Junction lỗi thời',
  NO_SCENES: 'Chưa có scene',
  SCENE_ORDER_INVALID: 'Thứ tự scene không hợp lệ',
}

export default function FilmFinalAssemblyPanel({ status, busy, onAssemble }: Props) {
  const gate = status?.gate
  const current = status?.current
  const media = current?.media
  const errors = gate?.errors || []
  return (
    <section className="film-junctions">
      <div className="film-junctions-head">
        <div>
          <strong>Final Assembly</strong>
          <span>{gate?.approved_count || 0}/{gate?.scene_count || 0} scene APPROVED · junction phải PASS · selected media only</span>
        </div>
        <button type="button" disabled={busy || !gate?.passed} onClick={onAssemble}>
          {busy ? 'Đang ghép…' : gate?.passed ? 'Ghép phim cuối' : 'Chưa đủ điều kiện ghép'}
        </button>
      </div>
      <dl className="film-ccc-meta">
        <div><dt>Gate</dt><dd>{gate?.passed ? 'Sẵn sàng ghép' : (ERROR_VI[gate?.code || ''] || gate?.code || 'FINAL_ASSEMBLY_BLOCKED')}</dd></div>
        <div><dt>Trạng thái</dt><dd>{current ? pipelineStatusVi(current.status) : 'Chưa ghép'}</dd></div>
        <div><dt>Version</dt><dd>{current?.version ?? '—'}</dd></div>
        <div><dt>Hash</dt><dd>{current?.manifest_hash || current?.manifest?.manifest_hash || '—'}</dd></div>
        <div><dt>Scene trong manifest</dt><dd>{current?.manifest?.scene_count ?? '—'}</dd></div>
        <div><dt>QC</dt><dd>{media?.qc_status || 'pending sau Master QC'}</dd></div>
      </dl>
      {errors.length ? (
        <ul className="film-pipeline-errors">
          {errors.slice(0, 12).map((item, index) => (
            <li key={`${item.code}-${item.scene_id || item.next_scene_id || index}`}>
              {ERROR_VI[item.code] || item.code}{item.scene_id ? ` · ${item.scene_id}` : ''}{item.previous_scene_id ? ` · ${item.previous_scene_id}→${item.next_scene_id}` : ''}
            </li>
          ))}
        </ul>
      ) : null}
      {media?.file_url ? (
        <video src={media.file_url} controls preload="metadata" />
      ) : <p className="film-ccc-empty">Chưa có final_video. Master QC (Phase C) mới được select.</p>}
    </section>
  )
}
