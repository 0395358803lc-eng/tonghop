import { mediaRoleVi, qcStatusVi } from './filmVi'
import type { FilmGeneratedMedia, FilmPipelineCandidate } from './types'

type Props = {
  media: FilmGeneratedMedia[]
  candidates?: FilmPipelineCandidate[]
  selectedId?: string | null
}

export default function FilmSceneVersionHistory({ media, candidates = [], selectedId }: Props) {
  const versions = media.slice().sort((a, b) => (b.version || 0) - (a.version || 0) || String(b.created_at).localeCompare(String(a.created_at)))
  if (!versions.length && !candidates.length) return <div className="film-ccc-empty">Chưa có version media cho scene này.</div>
  return (
    <div className="film-ccc-versions">
      {versions.map(item => {
        const using = item.is_selected || item.id === selectedId
        return (
          <article key={item.id} className={`film-ccc-version ${using ? 'selected' : ''}`}>
            <strong>v{item.version} · {mediaRoleVi(item.role)}</strong>
            <span>QC {qcStatusVi(item.qc_status)}{item.qc_score != null ? ` · ${Math.round(item.qc_score)}` : ''}</span>
            <span>{item.provider || 'TH Media'}{item.model ? ` · ${item.model}` : ''} · {item.id.slice(0, 8)}</span>
            {using ? <em>ĐANG DÙNG</em> : null}
          </article>
        )
      })}
      {candidates.map(item => (
        <article key={item.id} className={`film-ccc-version ${item.is_best ? 'best' : ''}`}>
          <strong>Candidate lần {Number(item.attempt || 0) + 1}</strong>
          <span>Score {item.overall_score != null ? Math.round(Number(item.overall_score)) : '—'} · hard {item.hard_gates_passed ? 'PASS' : 'FAIL'}</span>
          <span>media {item.media_id ? item.media_id.slice(0, 8) : 'chưa gắn'}</span>
          {item.is_best ? <em>BEST</em> : null}
        </article>
      ))}
    </div>
  )
}
