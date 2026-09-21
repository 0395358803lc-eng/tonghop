import { pipelineStatusVi } from './filmVi'
import type { FilmJunction } from './types'

type Props = {
  item: FilmJunction
  onOpen: () => void
  onRetry?: () => void
}

export default function FilmJunctionCard({ item, onOpen, onRetry }: Props) {
  const status = item.status || 'PENDING'
  const score = item.score
  return (
    <article className={`film-junction-card is-${String(status).toLowerCase()}`}>
      <button type="button" onClick={onOpen}>
        <span>{item.previous_scene_id} → {item.next_scene_id}</span>
        <strong>{pipelineStatusVi(status)}</strong>
        <em>{score != null ? Math.round(Number(score)) : '—'}</em>
      </button>
      {status === 'FAIL' && onRetry ? (
        <button type="button" className="film-junction-retry" onClick={onRetry}>Retry scene sau</button>
      ) : null}
    </article>
  )
}
