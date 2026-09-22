import { useEffect, useState } from 'react'
import { api } from './api'
import type { FilmCapabilityMatrix } from './types'

export default function FilmCapabilityPanel() {
  const [data, setData] = useState<FilmCapabilityMatrix | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const load = async () => {
    try {
      setData(await api.filmCapabilityMatrix())
      setError('')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Không tải capability matrix')
    }
  }

  useEffect(() => {
    let cancelled = false
    api.filmCapabilityMatrix().then(next => {
      if (!cancelled) {
        setData(next)
        setError('')
      }
    }).catch(exc => {
      if (!cancelled) setError(exc instanceof Error ? exc.message : 'Không tải capability matrix')
    })
    return () => { cancelled = true }
  }, [])

  const refresh = async () => {
    setBusy(true)
    try {
      await api.refreshFilmCapabilityMatrix()
      await load()
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Refresh capability thất bại')
    } finally {
      setBusy(false)
    }
  }

  const items = data?.items || []

  return (
    <section className="film-junctions film-capability">
      <div className="film-junctions-head">
        <div>
          <strong>Flow Capability Matrix</strong>
          <span>{data?.fresh ? 'Fresh' : 'Stale / empty'} · {items.length} model · không silent downgrade</span>
        </div>
        <button type="button" disabled={busy} onClick={refresh}>{busy ? 'Đang refresh…' : 'REFRESH MATRIX'}</button>
      </div>
      {error ? <p className="film-ccc-empty">{error}</p> : null}
      <div className="film-capability-list">
        {items.map(item => (
          <article key={item.id || item.model}>
            <strong>{item.model}</strong>
            <span>{item.media_type} · max refs {item.max_references ?? '—'}</span>
            <span>{(item.resolutions || []).join(', ') || '—'}</span>
            <span>dur {(item.durations || []).join(', ') || '—'}</span>
            <em>{item.checked_at ? String(item.checked_at).replace('T', ' ').slice(0, 19) : '—'}</em>
          </article>
        ))}
        {!items.length ? <p className="film-ccc-empty">Chưa có matrix. Refresh trước production run.</p> : null}
      </div>
    </section>
  )
}
