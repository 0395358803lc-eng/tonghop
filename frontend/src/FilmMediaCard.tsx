import { Download, History, Play } from 'lucide-react'
import type { FilmGeneratedMedia } from './types'
import { mediaRoleVi, qcStatusVi } from './filmVi'

type Props = {
  item: FilmGeneratedMedia
  onOpen: (item: FilmGeneratedMedia) => void
  onHistory: (item: FilmGeneratedMedia) => void
}

const titleOf = (item: FilmGeneratedMedia) => item.entity_id || item.scene_id || item.download_name || item.id.slice(0, 8)

export default function FilmMediaCard({ item, onOpen, onHistory }: Props) {
  const score = item.qc_score != null ? Math.round(item.qc_score) : null
  const passed = item.qc_status === 'passed'
  return (
    <article className={`film-media-card ${item.media_type} ${item.status} ${item.is_selected ? 'selected' : ''}`}>
      <button className="film-media-thumb" onClick={() => item.status === 'completed' && item.file_url && onOpen(item)}>
        {item.thumbnail_url || (item.media_type === 'image' && item.file_url) ? (
          <img src={item.thumbnail_url || item.file_url || ''} alt={titleOf(item)} />
        ) : item.status === 'processing' || item.status === 'pending' ? (
          <span className="film-media-placeholder">ĐANG TẠO...</span>
        ) : item.status === 'failed' ? (
          <span className="film-media-placeholder fail">TẠO THẤT BẠI</span>
        ) : (
          <span className="film-media-placeholder">Chưa có xem trước</span>
        )}
        {item.media_type === 'video' && item.status === 'completed' && <i className="film-media-play"><Play size={18} /></i>}
        {item.is_selected && <em className="film-media-badge use">BẢN ĐANG DÙNG</em>}
      </button>
      <div className="film-media-card-body">
        <strong>{titleOf(item)}</strong>
        <span>{mediaRoleVi(item.role)} · {item.provider || 'TH Media'}{item.model ? ` · ${item.model}` : ''}</span>
        <span className={passed ? 'qc-pass' : item.qc_status === 'failed' ? 'qc-fail' : 'qc-pending'}>
          QC {qcStatusVi(item.qc_status)}{score != null ? ` ${score}/100` : ''} · v{item.version}
        </span>
        {item.duration_seconds ? <small>{item.duration_seconds} giây</small> : null}
        {typeof item.metadata?.progress === 'number' && item.status === 'processing' ? <small>{item.metadata.progress}%</small> : null}
      </div>
      <div className="film-media-card-actions">
        <button disabled={!item.file_url} onClick={() => onOpen(item)}>Xem</button>
        {item.file_url && <a href={item.file_url} download={item.download_name || undefined}><Download size={12} /> Tải</a>}
        <button onClick={() => onHistory(item)}><History size={12} /> Lịch sử</button>
      </div>
    </article>
  )
}
