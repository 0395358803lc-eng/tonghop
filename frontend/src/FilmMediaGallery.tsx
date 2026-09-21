import { useMemo, useState } from 'react'
import type { FilmGeneratedMedia } from './types'
import FilmMediaCard from './FilmMediaCard'
import FilmMediaViewer from './FilmMediaViewer'

type Tab = 'all' | 'canonical' | 'scene' | 'final'

type Props = {
  items: FilmGeneratedMedia[]
  jobsProcessing?: boolean
  onRefresh: () => Promise<void>
  onSelectVersion: (item: FilmGeneratedMedia) => Promise<void>
}

export default function FilmMediaGallery({ items, jobsProcessing, onRefresh, onSelectVersion }: Props) {
  const [tab, setTab] = useState<Tab>('all')
  const [sceneFilter, setSceneFilter] = useState('')
  const [qcFilter, setQcFilter] = useState('')
  const [providerFilter, setProviderFilter] = useState('')
  const [viewerId, setViewerId] = useState<string | null>(null)
  const [historyId, setHistoryId] = useState<string | null>(null)

  const scenes = useMemo(() => Array.from(new Set(items.map(item => item.scene_id).filter(Boolean))) as string[], [items])
  const providers = useMemo(() => Array.from(new Set(items.map(item => item.provider).filter(Boolean))) as string[], [items])
  const filtered = items.filter(item => {
    if (tab === 'canonical' && !(item.role === 'canonical_image' || (item.role === 'repair_candidate' && item.resource_type))) return false
    if (tab === 'scene' && !(item.role === 'scene_video' || item.role === 'scene_image' || (item.role === 'repair_candidate' && item.scene_id))) return false
    if (tab === 'final' && item.role !== 'final_video') return false
    if (sceneFilter && item.scene_id !== sceneFilter) return false
    if (qcFilter && item.qc_status !== qcFilter) return false
    if (providerFilter && item.provider !== providerFilter) return false
    return true
  })
  const viewerItems = historyId ? items.filter(item => item.output_key === items.find(entry => entry.id === historyId)?.output_key) : filtered
  const activeId = viewerId || historyId

  return (
    <section className="film-media-gallery">
      <div className="film-media-gallery-head">
        <div>
          <span>KẾT QUẢ HÌNH ẢNH & VIDEO</span>
          <strong>{items.length} file local · không dùng mock</strong>
        </div>
        <div className="film-media-tabs">
          {([['all', 'Tất cả'], ['canonical', 'Ảnh chuẩn'], ['scene', 'Video phân cảnh'], ['final', 'Phim cuối']] as Array<[Tab, string]>).map(([id, label]) => (
            <button key={id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>{label}</button>
          ))}
        </div>
      </div>
      <div className="film-media-filters">
        <select value={sceneFilter} onChange={e => setSceneFilter(e.target.value)}>
          <option value="">Mọi phân cảnh</option>
          {scenes.map(scene => <option key={scene} value={scene}>{scene}</option>)}
        </select>
        <select value={qcFilter} onChange={e => setQcFilter(e.target.value)}>
          <option value="">Mọi QC</option>
          <option value="passed">QC đạt</option>
          <option value="failed">QC không đạt</option>
          <option value="pending">Đang QC</option>
        </select>
        <select value={providerFilter} onChange={e => setProviderFilter(e.target.value)}>
          <option value="">Mọi provider</option>
          {providers.map(provider => <option key={provider} value={provider}>{provider}</option>)}
        </select>
        <button onClick={() => onRefresh()}>Làm mới media</button>
      </div>
      {jobsProcessing && <p className="film-media-live">Đang tạo / kiểm tra media thật — gallery tự cập nhật.</p>}
      <div className="film-media-grid">
        {filtered.map(item => (
          <FilmMediaCard key={item.id} item={item} onOpen={entry => setViewerId(entry.id)} onHistory={entry => { setHistoryId(entry.id); setViewerId(entry.id) }} />
        ))}
        {!filtered.length && <div className="film-media-empty">Chưa có ảnh/video hoàn tất để xem. Khi Flow/xKiro tạo xong và file đã lưu local, thẻ sẽ xuất hiện tại đây.</div>}
      </div>
      {activeId && (
        <FilmMediaViewer
          items={viewerItems.length ? viewerItems : items}
          activeId={activeId}
          onClose={() => { setViewerId(null); setHistoryId(null) }}
          onSelect={async entry => {
            await onSelectVersion(entry)
            setViewerId(entry.id)
          }}
        />
      )}
    </section>
  )
}
