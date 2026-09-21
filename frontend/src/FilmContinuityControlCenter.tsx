import { useEffect, useMemo, useState } from 'react'
import { X } from 'lucide-react'
import { api } from './api'
import FilmAcceptanceSnapshotViewer from './FilmAcceptanceSnapshotViewer'
import FilmCapabilityPanel from './FilmCapabilityPanel'
import FilmEventLogPanel from './FilmEventLogPanel'
import FilmRecoveryStatusPanel from './FilmRecoveryStatusPanel'
import FilmAudioContinuityPanel from './FilmAudioContinuityPanel'
import FilmDialoguePanel from './FilmDialoguePanel'
import FilmFinalAssemblyPanel from './FilmFinalAssemblyPanel'
import FilmJunctionCard from './FilmJunctionCard'
import FilmJunctionQcPanel from './FilmJunctionQcPanel'
import FilmMasterQcPanel from './FilmMasterQcPanel'
import FilmPipelineControls from './FilmPipelineControls'
import FilmPipelineRunSummary from './FilmPipelineRunSummary'
import FilmRepairHistory from './FilmRepairHistory'
import FilmSceneEvidenceViewer from './FilmSceneEvidenceViewer'
import FilmSceneProductionCard from './FilmSceneProductionCard'
import FilmSceneQcPanel from './FilmSceneQcPanel'
import FilmSceneVersionHistory from './FilmSceneVersionHistory'
import FilmSpeakerIdentityPanel from './FilmSpeakerIdentityPanel'
import { jsonTextVi, pipelineStatusVi } from './filmVi'
import type { FilmAcceptanceSnapshot, FilmAudioRequirements, FilmFinalStatus, FilmGeneratedMedia, FilmJunction, FilmPipelineCandidate, FilmPipelineGate, FilmPipelineStatus, FilmScene, FilmSceneQcReport, FilmVoiceProfile } from './types'

type Filter = 'ALL' | 'APPROVED' | 'GENERATING' | 'QC_FAILED' | 'REGENERATING' | 'WAITING_REFERENCE' | 'BLOCKED' | 'STALE'

type Props = {
  projectId: string
  projectName?: string
  scenes: FilmScene[]
  media?: FilmGeneratedMedia[]
  onRefresh?: () => void
}

const FILTERS: Array<[Filter, string]> = [
  ['ALL', 'Tất cả'],
  ['APPROVED', 'Đã duyệt'],
  ['GENERATING', 'Đang tạo'],
  ['QC_FAILED', 'QC thất bại'],
  ['REGENERATING', 'Đang tạo lại'],
  ['WAITING_REFERENCE', 'Chờ reference'],
  ['BLOCKED', 'Bị chặn'],
  ['STALE', 'Lỗi thời'],
]

function asQc(value: unknown): FilmSceneQcReport {
  if (!value || typeof value !== 'object') return {}
  const rec = value as FilmSceneQcReport & { qc?: FilmSceneQcReport }
  if (rec.dimensions || rec.evidence) return rec
  if (rec.qc && typeof rec.qc === 'object') return rec.qc
  return rec
}

function frameUrl(value: unknown): string {
  if (!value) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'object' && value && 'url' in value) return String((value as { url?: string }).url || '')
  return ''
}

export default function FilmContinuityControlCenter({ projectId, projectName, scenes, media = [], onRefresh }: Props) {
  const [status, setStatus] = useState<FilmPipelineStatus | null>(null)
  const [liveGate, setLiveGate] = useState<FilmPipelineGate | null>(null)
  const [filter, setFilter] = useState<Filter>('ALL')
  const [query, setQuery] = useState('')
  const [openId, setOpenId] = useState<string | null>(null)
  const [candidates, setCandidates] = useState<FilmPipelineCandidate[]>([])
  const [now, setNow] = useState(Date.now())
  const [junctions, setJunctions] = useState<FilmJunction[]>([])
  const [openJunctionId, setOpenJunctionId] = useState<string | null>(null)
  const [audioReqs, setAudioReqs] = useState<FilmAudioRequirements[]>([])
  const [profiles, setProfiles] = useState<FilmVoiceProfile[]>([])
  const [junctionBusy, setJunctionBusy] = useState(false)
  const [finalStatus, setFinalStatus] = useState<FilmFinalStatus | null>(null)
  const [finalBusy, setFinalBusy] = useState(false)
  const [masterBusy, setMasterBusy] = useState(false)
  const [snapshots, setSnapshots] = useState<FilmAcceptanceSnapshot[]>([])
  const [snapshotLoading, setSnapshotLoading] = useState(false)

  useEffect(() => {
    let cancelled = false
    const tick = async () => {
      try {
        const next = await api.filmPipelineStatus(projectId)
        if (!cancelled) {
          setStatus(next)
          setNow(Date.now())
        }
      } catch {
        if (!cancelled) setStatus(null)
      }
    }
    tick()
    const id = window.setInterval(tick, 3000)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [projectId])

  useEffect(() => {
    let cancelled = false
    let inFlight = false
    const tick = async () => {
      if (inFlight) return
      inFlight = true
      try {
        const gate = await api.filmPipelineGate(projectId)
        if (!cancelled) setLiveGate(gate)
      } catch {
        if (!cancelled) setLiveGate(null)
      } finally {
        inFlight = false
      }
    }
    tick()
    const id = window.setInterval(tick, 10000)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [projectId])

  useEffect(() => {
    let cancelled = false
    const tick = async () => {
      try {
        const [junctionData, audioData, voiceData, finalData] = await Promise.all([
          api.filmJunctions(projectId),
          api.filmAudioRequirements(projectId),
          api.filmVoiceProfiles(projectId),
          api.filmFinalStatus(projectId),
        ])
        if (!cancelled) {
          setJunctions(junctionData.junctions || [])
          setAudioReqs(audioData.scenes || [])
          setProfiles(voiceData.profiles || [])
          setFinalStatus(finalData)
        }
      } catch {
        if (!cancelled) {
          setJunctions([])
          setAudioReqs([])
        }
      }
    }
    tick()
    const id = window.setInterval(tick, 8000)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [projectId, status?.counts?.approved])

  useEffect(() => {
    if (!openId) {
      setCandidates([])
      return
    }
    let cancelled = false
    api.filmPipelineCandidates(projectId, openId).then(data => {
      if (!cancelled) setCandidates(data.candidates || [])
    }).catch(() => {
      if (!cancelled) setCandidates([])
    })
    return () => { cancelled = true }
  }, [projectId, openId, status?.run?.updated_at, status?.counts?.approved, status?.counts?.qc_failed])

  useEffect(() => {
    if (!openId) {
      setSnapshots([])
      return
    }
    let cancelled = false
    setSnapshotLoading(true)
    api.filmAcceptanceSnapshots(projectId, openId).then(data => {
      if (!cancelled) setSnapshots(data.snapshots || [])
    }).catch(() => {
      if (!cancelled) setSnapshots([])
    }).finally(() => {
      if (!cancelled) setSnapshotLoading(false)
    })
    return () => { cancelled = true }
  }, [projectId, openId, status?.run?.updated_at, status?.counts?.approved])

  const reloadJunctions = async () => {
    const listed = await api.filmJunctions(projectId)
    setJunctions(listed.junctions || [])
  }

  const checkJunctions = async () => {
    if (junctionBusy) return
    setJunctionBusy(true)
    try {
      await api.checkFilmJunctions(projectId)
      await reloadJunctions()
    } finally {
      setJunctionBusy(false)
    }
  }

  const assembleFinal = async () => {
    if (finalBusy) return
    setFinalBusy(true)
    try {
      const data = await api.assembleFilmFinal(projectId)
      setFinalStatus(data)
      onRefresh?.()
    } catch {
      try {
        setFinalStatus(await api.filmFinalStatus(projectId))
      } catch {
        /* gate vẫn hiện từ poll */
      }
    } finally {
      setFinalBusy(false)
    }
  }

  const retryJunctionPolicy = async (junctionId: string) => {
    if (junctionBusy) return
    setJunctionBusy(true)
    try {
      const updated = await api.retryFilmJunction(projectId, junctionId)
      setJunctions(prev => prev.map(item => item.id === updated.id ? { ...item, ...updated } : item))
      setOpenJunctionId(updated.id)
      setOpenId(updated.repair_target_scene || updated.qc?.repair?.scene_id || null)
      try {
        setStatus(await api.filmPipelineStatus(projectId))
      } catch {
        /* pipeline poll sẽ bắt trạng thái REGENERATING */
      }
      onRefresh?.()
    } finally {
      setJunctionBusy(false)
    }
  }

  const runMasterQc = async () => {
    if (masterBusy) return
    setMasterBusy(true)
    try {
      setFinalStatus(await api.runFilmMasterQc(projectId))
      onRefresh?.()
    } catch {
      try {
        setFinalStatus(await api.filmFinalStatus(projectId))
      } catch {
        /* poll */
      }
    } finally {
      setMasterBusy(false)
    }
  }

  const states = status?.scenes || []
  const ledgers = status?.ledgers || []
  const counts = status?.counts || {
    total: scenes.length,
    approved: states.filter(s => s.status === 'APPROVED').length,
    generating: states.filter(s => ['GENERATING', 'QC_RUNNING', 'REGENERATING'].includes(s.status)).length,
    qc_failed: states.filter(s => s.status === 'QC_FAILED').length,
    blocked: states.filter(s => s.status === 'BLOCKED').length,
    waiting: states.filter(s => ['LOCKED', 'WAITING_REFERENCE', 'QUEUED'].includes(s.status)).length,
    stale: states.filter(s => s.status === 'STALE').length,
  }
  const ordered = useMemo(() => scenes.slice().sort((a, b) => a.scene_index - b.scene_index), [scenes])
  const visible = ordered.filter(scene => {
    const state = states.find(item => item.scene_id === scene.id)
    const st = state?.status || 'LOCKED'
    if (filter === 'GENERATING' && !['GENERATING', 'QC_RUNNING'].includes(st)) return false
    if (filter !== 'ALL' && filter !== 'GENERATING' && st !== filter) return false
    const blob = `${scene.id} ${scene.title || ''} ${(scene.characters || []).join(' ')} ${scene.location_id || ''}`.toLowerCase()
    return !query || blob.includes(query.trim().toLowerCase())
  })
  const openScene = ordered.find(scene => scene.id === openId)
  const openState = states.find(item => item.scene_id === openId)
  const openLedger = ledgers.find(item => item.scene_id === openId)
  const prevScene = openScene ? ordered.find(item => item.scene_index === openScene.scene_index - 1) : undefined
  const prevLedger = prevScene ? ledgers.find(item => item.scene_id === prevScene.id) : undefined
  const sceneMedia = media.filter(item => item.scene_id === openId && (item.role === 'scene_video' || item.role === 'repair_candidate' || item.role === 'scene_image'))
  const selected = sceneMedia.find(item => item.id === openState?.selected_media_id) || sceneMedia.find(item => item.is_selected)
  const qc = asQc(openState?.qc)
  const evidence = qc.evidence || {}
  const canonical = media.filter(item => item.role === 'canonical_image' && (
    (openScene?.characters || []).includes(item.entity_id || '') || item.entity_id === openScene?.location_id
  ))
  const sampled = (evidence.sampled_video_frames || []).map((frame, index) => ({ label: `QC frame ${index + 1}`, url: frameUrl(frame) }))
  const frames = [
    ...canonical.map(item => ({ label: `Canonical ${item.entity_id || item.id.slice(0, 8)}`, url: item.file_url || item.thumbnail_url })),
    { label: 'Previous accepted last frame', url: evidence.previous_accepted_last_frame || prevLedger?.accepted_last_frame },
    { label: 'Current first frame', url: evidence.current_first_frame || openLedger?.accepted_first_frame || selected?.thumbnail_url },
    { label: 'Current last frame', url: evidence.current_last_frame || openLedger?.accepted_last_frame },
    ...sampled,
  ]
  const openAudio = audioReqs.find(item => item.scene_id === openId)
  const openJunction = junctions.find(item => item.id === openJunctionId)
  const relatedJunction = junctions.find(item => item.previous_scene_id === openId || item.next_scene_id === openId)
  const junctionPass = junctions.filter(item => item.status === 'PASS').length

  return (
    <section className="film-ccc">
      <header className="film-ccc-head">
        <div>
          <span>CONTINUITY CONTROL CENTER</span>
          <strong>{projectName || 'Dự án phim'}</strong>
        </div>
        <ul>
          <li><b>{counts.total}</b> Scenes</li>
          <li><b>{counts.approved}</b> Approved</li>
          <li><b>{counts.generating}</b> Generating</li>
          <li><b>{counts.qc_failed}</b> Failed</li>
          <li><b>{counts.blocked}</b> Blocked</li>
          <li><b>{counts.waiting}</b> Waiting</li>
          <li><b>{counts.stale}</b> Stale</li>
        </ul>
      </header>
      <FilmPipelineRunSummary status={status} liveGate={liveGate} now={now} />
      <FilmPipelineControls projectId={projectId} status={status} scenes={scenes} onStatus={setStatus} onRefresh={onRefresh} />
      <FilmEventLogPanel projectId={projectId} />
      <FilmRecoveryStatusPanel projectId={projectId} />
      <FilmCapabilityPanel />
      <FilmSpeakerIdentityPanel projectId={projectId} />
      <section className="film-junctions">
        <div className="film-junctions-head">
          <div>
            <strong>Junction QC</strong>
            <span>{junctionPass}/{junctions.length} PASS · chỉ scene APPROVED + selected media</span>
          </div>
          <button type="button" disabled={junctionBusy} onClick={checkJunctions}>
            {junctionBusy ? 'Đang QC nối cảnh…' : 'Chạy Junction QC (scene đã duyệt)'}
          </button>
        </div>
        <p className="film-ccc-empty">Retry FAIL sẽ REGENERATING scene sau, tạo media version mới, rồi QC và recheck junction.</p>
        <div className="film-junction-list">
          {junctions.map(item => (
            <FilmJunctionCard
              key={item.id}
              item={item}
              onOpen={() => { setOpenJunctionId(item.id); setOpenId(null) }}
              onRetry={item.status === 'FAIL' ? () => retryJunctionPolicy(item.id) : undefined}
            />
          ))}
          {!junctions.length ? <div className="film-ccc-empty">Chưa có junction. Mở trang này sẽ tạo cặp PENDING theo thứ tự scene.</div> : null}
        </div>
      </section>
      <FilmFinalAssemblyPanel status={finalStatus} busy={finalBusy} onAssemble={assembleFinal} />
      <FilmMasterQcPanel status={finalStatus} busy={masterBusy} onRunQc={runMasterQc} />
      <div className="film-ccc-toolbar">
        <div className="film-media-tabs">
          {FILTERS.map(([id, label]) => (
            <button key={id} type="button" className={filter === id ? 'active' : ''} onClick={() => setFilter(id)}>{label}</button>
          ))}
        </div>
        <input value={query} onChange={e => setQuery(e.target.value)} placeholder="Tìm scene id, title, nhân vật, bối cảnh" />
      </div>
      <div className="film-ccc-grid">
        {visible.map(scene => {
          const state = states.find(item => item.scene_id === scene.id)
          return (
            <FilmSceneProductionCard
              key={scene.id}
              scene={scene}
              state={state}
              ledger={ledgers.find(item => item.scene_id === scene.id)}
              media={media.filter(item => item.scene_id === scene.id)}
              current={status?.run?.current_scene_id === scene.id}
              onOpen={() => setOpenId(scene.id)}
            />
          )
        })}
        {!visible.length ? <div className="film-ccc-empty">Không có scene khớp bộ lọc.</div> : null}
      </div>
      {openScene && openId ? (
        <div className="film-media-lightbox" onClick={() => setOpenId(null)}>
          <aside className="film-ccc-drawer" onClick={e => e.stopPropagation()}>
            <header>
              <div>
                <span>{openScene.id} · {pipelineStatusVi(openState?.status)}</span>
                <strong>{openScene.title || openScene.id}</strong>
              </div>
              <button type="button" onClick={() => setOpenId(null)}><X size={16} /></button>
            </header>
            <div className="film-ccc-drawer-body">
              {selected?.file_url && selected.media_type === 'video' ? (
                <video src={selected.file_url} controls preload="metadata" />
              ) : selected?.file_url ? (
                <img src={selected.file_url} alt={openScene.id} />
              ) : <div className="film-ccc-empty">Chưa có video đang dùng.</div>}
              <FilmSceneQcPanel qc={openState?.qc} />
              <h4>Dialogue</h4>
              <FilmDialoguePanel requirements={openAudio} />
              <h4>Audio continuity</h4>
              <FilmAudioContinuityPanel requirements={openAudio} profiles={profiles} />
              <h4>Junction QC</h4>
              <FilmJunctionQcPanel item={relatedJunction} />
              <h4>Continuity evidence</h4>
              <FilmSceneEvidenceViewer frames={frames} />
              <h4>Version history</h4>
              <FilmSceneVersionHistory media={sceneMedia} candidates={candidates} selectedId={openState?.selected_media_id} />
              <h4>Repair history</h4>
              <FilmRepairHistory repairs={openState?.repair} />
              <h4>State ledger</h4>
              <pre>{jsonTextVi(openLedger || openState?.snapshot || {})}</pre>
              <h4>Acceptance Snapshot</h4>
              <FilmAcceptanceSnapshotViewer snapshots={snapshots} loading={snapshotLoading} />
              <dl className="film-ccc-meta">
                <div><dt>Prompt</dt><dd>{String(openState?.snapshot?.prompt || openScene.flow_prompt || '—')}</dd></div>
                <div><dt>Provider</dt><dd>{String(openState?.snapshot?.provider || selected?.provider || '—')}</dd></div>
                <div><dt>Model</dt><dd>{selected?.model || '—'}</dd></div>
                <div><dt>Attempt</dt><dd>{Number(openState?.attempt || 0) + 1}</dd></div>
                <div><dt>Media</dt><dd>{openState?.selected_media_id || selected?.id || '—'}</dd></div>
                <div><dt>Job</dt><dd>{openState?.current_job_id || '—'}</dd></div>
              </dl>
            </div>
          </aside>
        </div>
      ) : null}
      {openJunction && !openId ? (
        <div className="film-media-lightbox" onClick={() => setOpenJunctionId(null)}>
          <aside className="film-ccc-drawer" onClick={e => e.stopPropagation()}>
            <header>
              <div>
                <span>JUNCTION · {pipelineStatusVi(openJunction.status)}</span>
                <strong>{openJunction.previous_scene_id} → {openJunction.next_scene_id}</strong>
              </div>
              <button type="button" onClick={() => setOpenJunctionId(null)}><X size={16} /></button>
            </header>
            <div className="film-ccc-drawer-body">
              <FilmJunctionQcPanel item={openJunction} />
              {openJunction.qc?.repair ? (
                <p>Repair target: ưu tiên {openJunction.qc.repair.prefer || 'next'} · scene {openJunction.qc.repair.scene_id}. Nút retry sẽ start pipeline scene_limit=1.</p>
              ) : null}
              {openJunction.status === 'FAIL' ? (
                <button type="button" className="film-junction-retry" disabled={junctionBusy} onClick={() => retryJunctionPolicy(openJunction.id)}>Retry scene sau</button>
              ) : null}
            </div>
          </aside>
        </div>
      ) : null}
    </section>
  )
}
