import { useEffect, useState } from 'react'
import { api } from './api'
import type { FilmRecoveryStatus } from './types'

type Props = { projectId: string }

export default function FilmRecoveryStatusPanel({ projectId }: Props) {
  const [data, setData] = useState<FilmRecoveryStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const load = async () => {
    try {
      setData(await api.filmRecovery(projectId))
      setError('')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Không tải recovery')
    }
  }

  useEffect(() => {
    let cancelled = false
    const tick = async () => {
      try {
        const next = await api.filmRecovery(projectId)
        if (!cancelled) { setData(next); setError('') }
      } catch (exc) {
        if (!cancelled) setError(exc instanceof Error ? exc.message : 'Không tải recovery')
      }
    }
    tick()
    const timer = window.setInterval(tick, 5000)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [projectId])

  const reconcile = async () => {
    setBusy(true)
    try {
      await api.reconcileFilmRecovery(projectId)
      await load()
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Reconcile thất bại')
    } finally {
      setBusy(false)
    }
  }

  const lease = data?.lease
  const orphans = data?.orphans || []

  return (
    <section className="film-junctions film-recovery">
      <div className="film-junctions-head">
        <div>
          <strong>Recovery / Lease</strong>
          <span>{data?.recovery_state_clean ? 'Trạng thái sạch' : 'Cần reconcile'} · orphan {orphans.length}</span>
        </div>
        <button type="button" disabled={busy} onClick={reconcile}>{busy ? 'Đang reconcile…' : 'RECONCILE'}</button>
      </div>
      {error ? <p className="film-ccc-empty">{error}</p> : null}
      <dl className="film-ccc-meta">
        <div><dt>Worker</dt><dd>{lease?.worker_id || '—'}</dd></div>
        <div><dt>Lease until</dt><dd>{lease?.lease_until || '—'}</dd></div>
        <div><dt>Heartbeat</dt><dd>{lease?.heartbeat_at || '—'}</dd></div>
        <div><dt>Event store</dt><dd>{data?.event_store_available ? 'OK' : 'DOWN'}</dd></div>
      </dl>
      {orphans.length ? (
        <ul className="film-pipeline-errors">
          {orphans.map(item => <li key={item.id}>ORPHAN {item.scene_id} · {item.status} · {item.id.slice(0, 8)}</li>)}
        </ul>
      ) : <p className="film-ccc-empty">Không có orphan job.</p>}
    </section>
  )
}
