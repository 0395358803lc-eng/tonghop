import { useEffect, useMemo, useState } from 'react'
import { api } from './api'
import { eventTypeVi, pipelineStatusVi } from './filmVi'
import type { FilmPipelineEvent, FilmPipelineMetrics } from './types'

type Props = { projectId: string }

export default function FilmEventLogPanel({ projectId }: Props) {
  const [events, setEvents] = useState<FilmPipelineEvent[]>([])
  const [metrics, setMetrics] = useState<FilmPipelineMetrics | null>(null)
  const [scene, setScene] = useState('')
  const [severity, setSeverity] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const query: { scene_id?: string; severity?: string } = {}
        if (scene) query.scene_id = scene
        if (severity) query.severity = severity
        const [log, stats] = await Promise.all([
          api.filmEvents(projectId, query),
          api.filmMetrics(projectId),
        ])
        if (!cancelled) {
          setEvents(log.events || [])
          setMetrics(stats)
          setError('')
        }
      } catch (exc) {
        if (!cancelled) setError(exc instanceof Error ? exc.message : 'Không tải được event log')
      }
    }
    load()
    const timer = window.setInterval(load, 4000)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [projectId, scene, severity])

  const scenes = useMemo(() => Array.from(new Set(events.map(item => item.scene_id).filter(Boolean))) as string[], [events])
  const latest = events.slice(-40).reverse()

  return (
    <section className="film-junctions film-event-log">
      <div className="film-junctions-head">
        <div>
          <strong>Activity / Event Log</strong>
          <span>{metrics?.event_count || 0} events · retry {metrics?.retry_count || 0} · timeout {metrics?.timeout_count || 0}</span>
        </div>
        <div className="film-event-filters">
          <select value={scene} onChange={e => setScene(e.target.value)}>
            <option value="">Mọi scene</option>
            {scenes.map(id => <option key={id} value={id}>{id}</option>)}
          </select>
          <select value={severity} onChange={e => setSeverity(e.target.value)}>
            <option value="">Mọi mức</option>
            <option value="INFO">INFO</option>
            <option value="WARN">WARN</option>
            <option value="ERROR">ERROR</option>
          </select>
        </div>
      </div>
      {error ? <p className="film-ccc-empty">{error}</p> : null}
      <dl className="film-ccc-meta">
        <div><dt>Scene TB</dt><dd>{metrics?.scene_render_duration_avg_sec ?? '—'}s</dd></div>
        <div><dt>Flow wait</dt><dd>{metrics?.flow_wait_duration_avg_sec ?? '—'}s</dd></div>
        <div><dt>QC TB</dt><dd>{metrics?.qc_duration_avg_sec ?? '—'}s</dd></div>
        <div><dt>Thành công</dt><dd>{metrics?.success_count ?? 0}</dd></div>
      </dl>
      <ul className="film-event-list">
        {latest.map(item => (
          <li key={item.id} className={`is-${String(item.severity || 'INFO').toLowerCase()}`}>
            <em>{String(item.created_at || '').replace('T', ' ').slice(0, 19)}</em>
            <strong>{eventTypeVi(item.event_type)}</strong>
            <span>{item.scene_id || '—'}</span>
            <span>{pipelineStatusVi(item.severity)}</span>
            <span>job {item.job_id ? item.job_id.slice(0, 8) : '—'}</span>
            <span>{String((item.payload || {}).error || (item.payload || {}).detail || '')}</span>
          </li>
        ))}
        {!latest.length ? <li className="film-ccc-empty">Chưa có event. Pipeline sẽ ghi log tại đây.</li> : null}
      </ul>
    </section>
  )
}
