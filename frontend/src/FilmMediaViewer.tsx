import { useEffect, useMemo, useState } from 'react'
import { ChevronLeft, ChevronRight, X } from 'lucide-react'
import type { FilmGeneratedMedia } from './types'
import { api } from './api'
import { mediaRoleVi, qcStatusVi } from './filmVi'

type Props = {
  items: FilmGeneratedMedia[]
  activeId: string
  onClose: () => void
  onSelect?: (item: FilmGeneratedMedia) => void
}

const dimLabel: Record<string, string> = {
  identity: 'Nhân vật',
  wardrobe: 'Trang phục',
  location: 'Bối cảnh',
  prop: 'Đạo cụ',
  boundary: 'Nối cảnh',
  story_alignment: 'Khớp kịch bản',
  identity_attributes: 'Nhận diện',
  face_visibility: 'Khuôn mặt',
  reference_quality: 'Chất lượng tham chiếu',
  single_subject: 'Một chủ thể',
  geometry_layout: 'Bố cục',
  fixed_details: 'Chi tiết cố định',
  lighting_time: 'Ánh sáng / thời gian',
  no_people: 'Không người',
  object_identity: 'Nhận diện vật',
  material_color: 'Chất liệu / màu',
  state_details: 'Trạng thái',
  isolated_object: 'Vật thể tách nền',
}

export default function FilmMediaViewer({ items, activeId, onClose, onSelect }: Props) {
  const index = Math.max(0, items.findIndex(item => item.id === activeId))
  const [cursor, setCursor] = useState(index)
  const [versions, setVersions] = useState<FilmGeneratedMedia[]>([])
  const [zoom, setZoom] = useState(1)
  const [preview, setPreview] = useState<FilmGeneratedMedia | null>(null)
  const item = preview || items[cursor] || items[0]
  const qc = (item?.qc || {}) as Record<string, unknown>
  const gate = (qc.hard_gate || {}) as Record<string, unknown>
  const dimensions = (gate.dimensions || {}) as Record<string, Record<string, unknown>>
  const audio = (qc.audio_check || null) as Record<string, unknown> | null
  const speech = (qc.speech_check || null) as Record<string, unknown> | null

  useEffect(() => { setCursor(Math.max(0, items.findIndex(entry => entry.id === activeId))); setZoom(1); setPreview(null) }, [activeId, items])
  useEffect(() => {
    if (!item || item.id.startsWith('live-')) { setVersions([]); return }
    api.filmMediaVersions(item.id).then(data => setVersions(data.versions || [])).catch(() => setVersions([]))
  }, [item?.id])

  const neighbors = useMemo(() => ({ prev: cursor > 0, next: cursor < items.length - 1 }), [cursor, items.length])
  if (!item) return null

  return (
    <div className="film-media-lightbox" onClick={onClose}>
      <div className="film-media-viewer" onClick={e => e.stopPropagation()}>
        <header>
          <div>
            <span>{mediaRoleVi(item.role)}</span>
            <strong>{item.entity_id || item.scene_id || 'Media'} · v{item.version}</strong>
          </div>
          <button onClick={onClose}><X size={16} /></button>
        </header>
        <div className="film-media-viewer-body">
          <div className="film-media-stage">
            {neighbors.prev && <button className="nav prev" onClick={() => { setPreview(null); setCursor(cursor - 1) }}><ChevronLeft size={18} /></button>}
            {item.media_type === 'video' && item.file_url ? (
              <video key={item.id} src={item.file_url} controls playsInline preload="metadata" />
            ) : item.file_url ? (
              <img
                src={item.file_url}
                alt={item.entity_id || item.scene_id || 'media'}
                className={zoom > 1 ? 'zoomed' : ''}
                style={{ transform: `scale(${zoom})` }}
                onClick={() => setZoom(z => z >= 2 ? 1 : Number((z + 0.5).toFixed(1)))}
              />
            ) : <p>Chưa có file để xem.</p>}
            {item.media_type === 'image' && item.file_url && (
              <div className="film-media-zoom">
                <button onClick={() => setZoom(z => Math.min(3, Number((z + 0.5).toFixed(1))))}>Phóng to</button>
                <button onClick={() => setZoom(1)}>Vừa màn hình</button>
                <span>{Math.round(zoom * 100)}%</span>
              </div>
            )}
            {neighbors.next && <button className="nav next" onClick={() => { setPreview(null); setCursor(cursor + 1) }}><ChevronRight size={18} /></button>}
          </div>
          <aside>
            <dl>
              <div><dt>Scene</dt><dd>{item.scene_id || '—'}</dd></div>
              <div><dt>Resource</dt><dd>{item.entity_id || '—'}</dd></div>
              <div><dt>Provider</dt><dd>{item.provider || '—'}</dd></div>
              <div><dt>Model</dt><dd>{item.model || '—'}</dd></div>
              <div><dt>Thời lượng</dt><dd>{item.duration_seconds ? `${item.duration_seconds}s` : '—'}</dd></div>
              <div><dt>Kích thước</dt><dd>{item.width && item.height ? `${item.width}×${item.height}` : '—'}</dd></div>
              <div><dt>Tỉ lệ</dt><dd>{item.width && item.height ? `${item.width}:${item.height}` : '—'}</dd></div>
              <div><dt>QC</dt><dd>{qcStatusVi(item.qc_status)}{item.qc_score != null ? ` · ${Math.round(item.qc_score)}/100` : ''}</dd></div>
              <div><dt>Version</dt><dd>v{item.version}{item.is_selected ? ' · đang dùng' : ''}</dd></div>
              <div><dt>Tạo lúc</dt><dd>{item.created_at}</dd></div>
            </dl>
            {Object.keys(dimensions).length > 0 && (
              <div className="film-media-qc-grid">
                {Object.entries(dimensions).map(([key, value]) => (
                  <span key={key} className={value.passed === true ? 'pass' : 'fail'}>
                    {dimLabel[key] || key}: {typeof value.score === 'number' ? Math.round(value.score) : '—'}{value.passed === true ? ' ✓' : ' ✕'}
                  </span>
                ))}
              </div>
            )}
            {audio && <p>Âm thanh: {audio.present === true ? 'Có' : 'Không'} · {audio.non_silent === true ? 'Có tín hiệu' : 'Im lặng'}</p>}
            {speech && <p>Lời thoại: {speech.passed === true ? 'Đạt' : 'Không đạt'}{typeof speech.transcript === 'string' ? ` · ${speech.transcript}` : ''}</p>}
            {versions.length > 1 && (
              <div className="film-media-versions">
                <span>LỊCH SỬ</span>
                {versions.map(version => (
                  <button key={version.id} className={version.id === item.id ? 'active' : ''} onClick={() => { const idx = items.findIndex(entry => entry.id === version.id); setZoom(1); if (idx >= 0) { setPreview(null); setCursor(idx) } else setPreview(version) }}>
                    v{version.version}{version.is_selected ? ' · đang dùng' : ''}
                  </button>
                ))}
              </div>
            )}
            {item.file_url && <a className="film-media-download" href={item.file_url} download={item.download_name || undefined}>Tải file</a>}
            {!item.is_selected && item.status === 'completed' && item.qc_status === 'passed' && item.role !== 'repair_candidate' && !item.id.startsWith('live-') && (
              <button className="film-media-select" onClick={() => onSelect?.(item)}>Dùng bản này</button>
            )}
          </aside>
        </div>
      </div>
    </div>
  )
}
