import { useState } from 'react'

type Frame = { label: string; url?: string | null }

type Props = {
  frames: Frame[]
}

function urlOf(value: unknown): string {
  if (!value) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'object' && value && 'url' in value) return String((value as { url?: string }).url || '')
  return ''
}

export default function FilmSceneEvidenceViewer({ frames }: Props) {
  const [full, setFull] = useState<Frame | null>(null)
  const items = frames.filter(item => urlOf(item.url))
  if (!items.length) return <div className="film-ccc-empty">Chưa có continuity evidence (canonical / last frame / QC frames).</div>
  return (
    <div className="film-ccc-evidence">
      {items.map(item => (
        <button key={`${item.label}-${item.url}`} type="button" className="film-ccc-evidence-card" onClick={() => setFull(item)}>
          <img src={urlOf(item.url)} alt={item.label} />
          <span>{item.label}</span>
        </button>
      ))}
      {full ? (
        <div className="film-media-lightbox" onClick={() => setFull(null)}>
          <figure className="film-ccc-evidence-full" onClick={e => e.stopPropagation()}>
            <img src={urlOf(full.url)} alt={full.label} />
            <figcaption>{full.label}<button type="button" onClick={() => setFull(null)}>Đóng</button></figcaption>
          </figure>
        </div>
      ) : null}
    </div>
  )
}
