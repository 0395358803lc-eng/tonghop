import { useMemo, useState } from 'react'
import { Play } from 'lucide-react'
import type { FilmGeneratedMedia } from './types'
import FilmMediaViewer from './FilmMediaViewer'
import { qcStatusVi } from './filmVi'

type Props = {
  sceneId: string
  items: FilmGeneratedMedia[]
  onSelectVersion: (item: FilmGeneratedMedia) => Promise<void>
}

export default function FilmSceneMedia({ sceneId, items, onSelectVersion }: Props) {
  const [openId, setOpenId] = useState<string | null>(null)
  const sceneItems = items.filter(item => item.scene_id === sceneId && (item.media_type === 'video' || item.role === 'scene_image' || item.role === 'repair_candidate'))
  const selected = sceneItems.find(item => item.is_selected && item.status === 'completed') || sceneItems.find(item => item.status === 'completed')
  const versions = useMemo(() => {
    const key = selected?.output_key
    return key ? sceneItems.filter(item => item.output_key === key).sort((a, b) => b.version - a.version) : sceneItems
  }, [sceneItems, selected?.output_key])
  const processing = sceneItems.some(item => item.status === 'processing' || item.status === 'pending')
  const failed = sceneItems.find(item => item.status === 'failed')

  return (
    <div className="film-scene-media">
      <div className="film-scene-media-head">KẾT QUẢ CỦA PHÂN CẢNH</div>
      {selected?.file_url ? (
        <button className="film-scene-media-hero" onClick={() => setOpenId(selected.id)}>
          {selected.thumbnail_url ? <img src={selected.thumbnail_url} alt={sceneId} /> : <span className="film-media-placeholder">VIDEO</span>}
          <i><Play size={16} /></i>
          <div>
            <strong>{sceneId} · {selected.duration_seconds || '?'} giây</strong>
            <span>QC {qcStatusVi(selected.qc_status)}{selected.qc_score != null ? ` · ${Math.round(selected.qc_score)}/100` : ''} · v{selected.version}</span>
          </div>
        </button>
      ) : processing ? (
        <div className="film-scene-media-hero loading"><strong>{sceneId}</strong><span>ĐANG TẠO VIDEO...</span></div>
      ) : failed ? (
        <div className="film-scene-media-hero fail"><strong>TẠO VIDEO THẤT BẠI</strong><span>{failed.metadata?.error as string || 'Không có file local.'}</span></div>
      ) : (
        <div className="film-scene-media-hero empty">Chưa có video cho phân cảnh này.</div>
      )}
      {versions.length > 0 && (
        <p className="film-scene-media-versions">
          Bản đang dùng: v{selected?.version || '—'} · Lịch sử: {versions.map(item => `v${item.version}`).join(' · ')}
        </p>
      )}
      {openId && selected && (
        <FilmMediaViewer
          items={versions.length ? versions : sceneItems}
          activeId={openId}
          onClose={() => setOpenId(null)}
          onSelect={onSelectVersion}
        />
      )}
    </div>
  )
}
