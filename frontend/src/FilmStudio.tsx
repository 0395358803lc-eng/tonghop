import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, BookOpen, CheckCircle2, Clapperboard, Download, Film, Loader2, LockKeyhole, MapPin, Pause, Play, Plus, RotateCcw, Save, Sparkles, Trash2, WandSparkles } from 'lucide-react'
import { api } from './api'
import type { FilmProject, FilmProjectSummary, FilmRenderStatus, FilmScene, FilmSettings, Provider } from './types'

type Props = {
  providerId: string
  model: string
  provider?: Provider
  onOpenSettings: () => void
}

const DEFAULT_SETTINGS: FilmSettings = {
  scene_duration: 8,
  aspect_ratio: '16:9',
  resolution: '1080p',
  style: 'Cinematic',
  character_lock: true,
  location_lock: true,
  auto_continuity: true,
}

const jsonText = (value: unknown) => JSON.stringify(value ?? {}, null, 2)

export default function FilmStudio({ providerId, model, provider, onOpenSettings }: Props) {
  const [projects, setProjects] = useState<FilmProjectSummary[]>([])
  const [active, setActive] = useState<FilmProject | null>(null)
  const [activeSceneId, setActiveSceneId] = useState<string | null>(null)
  const [story, setStory] = useState('')
  const [name, setName] = useState('')
  const [settings, setSettings] = useState<FilmSettings>(DEFAULT_SETTINGS)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState('')
  const [projectTab, setProjectTab] = useState<'original' | 'settings' | 'story' | 'characters' | 'locations' | 'props' | 'master'>('story')
  const [draft, setDraft] = useState<Partial<FilmScene>>({})
  const [dialogueText, setDialogueText] = useState('[]')
  const [renderState, setRenderState] = useState<FilmRenderStatus | null>(null)
  const [renderBusy, setRenderBusy] = useState(false)
  const [selectedForRender, setSelectedForRender] = useState<string[]>([])

  const refreshProjects = async () => setProjects(await api.filmProjects())
  const refreshRender = async (projectId: string) => { const state = await api.filmRenderStatus(projectId); setRenderState(state); return state }

  useEffect(() => { refreshProjects().catch(() => undefined) }, [])

  useEffect(() => {
    if (!active || !['queued', 'analyzing'].includes(active.status)) return
    const timer = window.setInterval(async () => {
      try {
        const next = await api.filmProject(active.id)
        setActive(next)
        if (next.status === 'ready' && next.scenes.length) setActiveSceneId(prev => prev || next.scenes[0].id)
        if (next.status === 'failed') setError(next.error || 'Phân tích dự án thất bại')
        if (['ready', 'failed'].includes(next.status)) await refreshProjects()
      } catch (e) { setError((e as Error).message) }
    }, 1800)
    return () => window.clearInterval(timer)
  }, [active?.id, active?.status])

  useEffect(() => {
    if (!active || active.status !== 'ready') return
    let cancelled = false
    const sync = async () => {
      try {
        const state = await api.filmRenderStatus(active.id)
        if (cancelled) return
        setRenderState(state)
        if (state.jobs.some(job => ['preparing', 'generating', 'completed', 'failed'].includes(job.status))) {
          const project = await api.filmProject(active.id)
          if (!cancelled) setActive(project)
        }
      } catch (e) { if (!cancelled) setError((e as Error).message) }
    }
    sync()
    const timer = window.setInterval(sync, 2200)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [active?.id, active?.status])

  const selectedScene = useMemo(
    () => active?.scenes.find(scene => scene.id === activeSceneId) || active?.scenes[0] || null,
    [active, activeSceneId],
  )

  const latestRenderByScene = useMemo(() => {
    const map = new Map<string, NonNullable<FilmRenderStatus['jobs']>[number]>()
    for (const job of renderState?.jobs || []) map.set(job.scene_id, job)
    return map
  }, [renderState])

  useEffect(() => {
    if (!selectedScene) return
    setDraft({})
    setDialogueText(JSON.stringify(selectedScene.dialogue || [], null, 2))
  }, [selectedScene?.id])

  const createAndAnalyze = async () => {
    if (!story.trim() || creating) return
    if (!provider?.configured) {
      setError(`Hãy cấu hình API key cho ${provider?.name || 'nhà cung cấp'} trước.`)
      onOpenSettings()
      return
    }
    setCreating(true)
    setError('')
    try {
      const created = await api.createFilmProject(name.trim(), story.trim(), providerId, model, settings)
      const queued = await api.analyzeFilmProject(created.id)
      setActive(queued)
      await refreshProjects()
    } catch (e) { setError((e as Error).message) }
    finally { setCreating(false) }
  }

  const openProject = async (id: string) => {
    try {
      const project = await api.filmProject(id)
      setActive(project)
      setSettings(project.settings || DEFAULT_SETTINGS)
      setStory(project.original_text)
      setName(project.name)
      setActiveSceneId(project.scenes[0]?.id || null)
      setError('')
    } catch (e) { setError((e as Error).message) }
  }

  const newProject = () => {
    setActive(null)
    setActiveSceneId(null)
    setStory('')
    setName('')
    setSettings(DEFAULT_SETTINGS)
    setRenderState(null)
    setSelectedForRender([])
    setError('')
  }

  const removeProject = async (id: string) => {
    await api.deleteFilmProject(id)
    if (active?.id === id) newProject()
    await refreshProjects()
  }

  const setField = (key: keyof FilmScene, value: unknown) => setDraft(prev => ({ ...prev, [key]: value }))

  const saveScene = async () => {
    if (!active || !selectedScene) return
    try {
      let dialogue = selectedScene.dialogue || []
      try { dialogue = JSON.parse(dialogueText) } catch { throw new Error('Dialogue phải là JSON hợp lệ') }
      const updated = await api.updateFilmScene(active.id, selectedScene.id, { ...draft, dialogue })
      setActive(updated)
      setDraft({})
      setError('')
    } catch (e) { setError((e as Error).message) }
  }

  const checkContinuity = async () => {
    if (!active) return
    try {
      const updated = await api.checkFilmContinuity(active.id)
      setActive(updated)
      setError('')
    } catch (e) { setError((e as Error).message) }
  }

  const toggleRenderSelection = (sceneId: string) => {
    setSelectedForRender(prev => prev.includes(sceneId) ? prev.filter(id => id !== sceneId) : [...prev, sceneId])
  }

  const queueRender = async (sceneIds: string[]) => {
    if (!active || !sceneIds.length || renderBusy) return
    setRenderBusy(true)
    setError('')
    try {
      const state = await api.queueFilmRender(active.id, sceneIds)
      setRenderState(state)
      setSelectedForRender([])
      setActive(await api.filmProject(active.id))
      if (!state.adapter.configured) setError('Đã tạo Render Queue nhưng chưa có Video Render Adapter thật. Job sẽ giữ ở Waiting cho đến khi adapter được cấu hình.')
    } catch (e) { setError((e as Error).message) }
    finally { setRenderBusy(false) }
  }

  const pauseRender = async () => {
    if (!active) return
    try { setRenderState(await api.pauseFilmRender(active.id)); setError('') } catch (e) { setError((e as Error).message) }
  }

  const resumeRender = async () => {
    if (!active) return
    try { setRenderState(await api.resumeFilmRender(active.id)); setError('') } catch (e) { setError((e as Error).message) }
  }

  const retryRender = async (jobId: string) => {
    if (!active) return
    try { await api.retryFilmRender(jobId); await refreshRender(active.id); setError('') } catch (e) { setError((e as Error).message) }
  }

  const autoPipeline = async () => {
    if (!active || renderBusy) return
    setRenderBusy(true)
    setError('')
    try {
      const audited = await api.checkFilmContinuity(active.id)
      setActive(audited)
      const state = await api.queueFilmRender(audited.id, audited.scenes.map(scene => scene.id))
      setRenderState(state)
      if (!state.adapter.configured) setError('Continuity đã đạt điều kiện queue. AUTO PIPELINE đang dừng ở bước Render vì chưa cấu hình Video Render Adapter thật.')
    } catch (e) { setError((e as Error).message) }
    finally { setRenderBusy(false) }
  }

  const exportProject = () => {
    if (!active) return
    const blob = new Blob([JSON.stringify(active, null, 2)], { type: 'application/json' })
    const href = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = href
    a.download = `${active.name.replace(/[^a-z0-9-_]+/gi, '_') || 'film_project'}.json`
    a.click()
    URL.revokeObjectURL(href)
  }

  const exportFlowPrompts = () => {
    if (!active) return
    const payload = { project_id: active.id, project_name: active.name, master_prompt: active.master_prompt, visual_style: active.visual_style, scenes: active.scenes.map(scene => ({ scene_id: scene.id, flow_prompt: scene.flow_prompt })) }
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
    const href = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = href
    a.download = 'project_prompts.json'
    a.click()
    URL.revokeObjectURL(href)
  }


  const field = (key: keyof FilmScene) => String((draft[key] ?? selectedScene?.[key] ?? '') as string)

  return (
    <section className="film-workspace">
      {!active ? (
        <div className="film-start-layout">
          <aside className="film-recent">
            <div className="film-recent-head"><Film size={16} /><strong>Dự án gần đây</strong></div>
            <button className="film-new-project" onClick={newProject}><Plus size={14} /> Dự án mới</button>
            <div className="film-project-list">
              {projects.map(project => (
                <div className="film-project-row" key={project.id}>
                  <button onClick={() => openProject(project.id)}><strong>{project.name}</strong><span>{project.scene_count} scene · {project.status}</span></button>
                  <button className="film-project-trash" onClick={() => removeProject(project.id)}><Trash2 size={13} /></button>
                </div>
              ))}
              {!projects.length && <p>Chưa có dự án phim.</p>}
            </div>
          </aside>
          <div className="film-start-main">
            <div className="film-start-kicker"><Sparkles size={15} /> TH MEDIA · CINEMATIC STUDIO</div>
            <h1>Một câu chuyện.<br /><span>Một thế giới điện ảnh nhất quán.</span></h1>
            <p>AI đọc toàn bộ nội dung trước, xây Story Bible và Continuity rồi mới chia thành các scene có thể render độc lập.</p>
            <div className="film-story-input">
              <input value={name} onChange={e => setName(e.target.value)} placeholder="Tên dự án (không bắt buộc)" />
              <textarea value={story} onChange={e => setStory(e.target.value)} placeholder="Nội dung gốc / Story Input — dán kịch bản, truyện, bài viết, nội dung quảng cáo, review, phim ngắn..." />
              <div className="film-settings-grid">
                <label>Thời lượng scene<select value={settings.scene_duration} onChange={e => setSettings({ ...settings, scene_duration: Number(e.target.value) })}><option value={4}>4 giây</option><option value={6}>6 giây</option><option value={8}>8 giây</option><option value={10}>10 giây</option><option value={12}>12 giây</option></select></label>
                <label>Tỷ lệ<select value={settings.aspect_ratio} onChange={e => setSettings({ ...settings, aspect_ratio: e.target.value })}><option>16:9</option><option>9:16</option><option>1:1</option></select></label>
                <label>Độ phân giải<select value={settings.resolution} onChange={e => setSettings({ ...settings, resolution: e.target.value })}><option>720p</option><option>1080p</option><option>Highest</option></select></label>
                <label>Phong cách<select value={settings.style} onChange={e => setSettings({ ...settings, style: e.target.value })}><option>Cinematic</option><option>Photorealistic</option><option>Commercial</option><option>Documentary</option><option>Anime</option><option>3D Animation</option><option>Product Advertising</option></select></label>
              </div>
              <div className="film-lock-row">
                <button className={settings.character_lock ? 'on' : ''} onClick={() => setSettings({ ...settings, character_lock: !settings.character_lock })}><LockKeyhole size={13} /> Lock Character</button>
                <button className={settings.location_lock ? 'on' : ''} onClick={() => setSettings({ ...settings, location_lock: !settings.location_lock })}><MapPin size={13} /> Lock Location</button>
                <button className={settings.auto_continuity ? 'on' : ''} onClick={() => setSettings({ ...settings, auto_continuity: !settings.auto_continuity })}><WandSparkles size={13} /> Auto Continuity</button>
              </div>
              <button className="film-analyze-button" disabled={!story.trim() || !model || creating} onClick={createAndAnalyze}>{creating ? <Loader2 className="spin" size={17} /> : <Sparkles size={17} />} PHÂN TÍCH NỘI DUNG</button>
              {error && <div className="film-error"><AlertTriangle size={14} />{error}</div>}
            </div>
          </div>
        </div>
      ) : ['queued', 'analyzing'].includes(active.status) ? (
        <div className="film-processing">
          <div className="film-processing-icon"><Loader2 className="spin" size={28} /></div>
          <div className="film-kicker">STORY ARCHITECT</div>
          <h2>{active.name}</h2>
          <p>{active.stage}</p>
          <div className="film-progress"><div style={{ width: `${active.progress}%` }} /></div>
          <span>{active.progress}% · AI đang đọc toàn bộ câu chuyện trước khi chia cảnh</span>
          <button onClick={newProject}>Tạo dự án khác</button>
        </div>
      ) : active.status === 'failed' ? (
        <div className="film-processing film-failed">
          <AlertTriangle size={30} />
          <h2>Phân tích chưa hoàn tất</h2>
          <p>{active.error || 'Không rõ lỗi'}</p>
          <button onClick={async () => setActive(await api.analyzeFilmProject(active.id))}>Thử phân tích lại</button>
          <button onClick={newProject}>Dự án mới</button>
        </div>
      ) : (
        <div className="film-editor-shell">
          <div className="film-editor-head">
            <div><div className="film-kicker">FILM PROJECT</div><h2>{active.name}</h2><span>{active.scenes.length} scene · {active.settings.aspect_ratio} · {active.settings.resolution} · {active.settings.style}</span></div>
            <div className="film-head-actions"><button onClick={newProject}><Plus size={14} /> Dự án mới</button><button onClick={exportFlowPrompts}><Download size={14} /> Flow Prompts</button><button onClick={exportProject}><Download size={14} /> Export JSON</button></div>
          </div>
          {error && <div className="film-error editor-error"><AlertTriangle size={14} />{error}</div>}
          <div className="film-editor-grid">
            <aside className="film-project-panel">
              <div className="film-panel-title"><BookOpen size={14} /><strong>PROJECT</strong></div>
              <div className="film-project-tabs">
                <button className={projectTab === 'original' ? 'active' : ''} onClick={() => setProjectTab('original')}>Original Text</button>
                <button className={projectTab === 'settings' ? 'active' : ''} onClick={() => setProjectTab('settings')}>Project Settings</button>
                <button className={projectTab === 'story' ? 'active' : ''} onClick={() => setProjectTab('story')}>Story Bible</button>
                <button className={projectTab === 'characters' ? 'active' : ''} onClick={() => setProjectTab('characters')}>Character Bible <span>{active.characters.length}</span></button>
                <button className={projectTab === 'locations' ? 'active' : ''} onClick={() => setProjectTab('locations')}>Location Bible <span>{active.locations.length}</span></button>
                <button className={projectTab === 'props' ? 'active' : ''} onClick={() => setProjectTab('props')}>Prop Bible <span>{active.props.length}</span></button>
                <button className={projectTab === 'master' ? 'active' : ''} onClick={() => setProjectTab('master')}>Master Prompt</button>
              </div>
              <div className="film-bible-view">
                {projectTab === 'original' && <p className="film-original-text">{active.original_text}</p>}
                {projectTab === 'settings' && <pre>{jsonText(active.settings)}</pre>}
                {projectTab === 'story' && <pre>{jsonText(active.story_bible)}</pre>}
                {projectTab === 'characters' && <pre>{jsonText(active.characters)}</pre>}
                {projectTab === 'locations' && <pre>{jsonText(active.locations)}</pre>}
                {projectTab === 'props' && <pre>{jsonText(active.props)}</pre>}
                {projectTab === 'master' && <p>{active.master_prompt || 'Chưa có Master Prompt.'}</p>}
              </div>
              <div className="film-lock-summary"><span><LockKeyhole size={12} /> Character {active.settings.character_lock ? 'LOCKED' : 'OFF'}</span><span><MapPin size={12} /> Location {active.settings.location_lock ? 'LOCKED' : 'OFF'}</span><span><WandSparkles size={12} /> Auto Continuity {active.settings.auto_continuity ? 'ON' : 'OFF'}</span></div>
            </aside>

            <section className="film-storyboard-panel">
              <div className="film-panel-title"><Clapperboard size={14} /><strong>STORYBOARD</strong><span>{active.scenes.length} clips</span></div>
              <div className="film-scene-list">
                {active.scenes.map(scene => {
                  const renderJob = latestRenderByScene.get(scene.id)
                  const selected = selectedForRender.includes(scene.id)
                  return (
                    <button key={scene.id} className={selectedScene?.id === scene.id ? 'active' : ''} onClick={() => setActiveSceneId(scene.id)}>
                      <span className={`film-scene-select ${selected ? 'selected' : ''}`} onClick={e => { e.stopPropagation(); toggleRenderSelection(scene.id) }}>{selected ? '✓' : ''}</span>
                      <div className="film-scene-index">{String(scene.scene_index).padStart(2, '0')}</div>
                      <div><strong>{scene.title || scene.id}</strong><p>{scene.summary || scene.action || 'Chưa có mô tả'}</p><span>{scene.duration}s · {scene.location_id || 'No location'} · {scene.characters.join(', ') || 'No character'}</span></div>
                      <div className="film-scene-flags">
                        {!!scene.warnings.length && <AlertTriangle size={13} />}
                        {renderJob && <em className={`render-mini ${renderJob.status}`}>{renderJob.status}</em>}
                      </div>
                    </button>
                  )
                })}
              </div>
            </section>

            <section className="film-scene-editor">
              {selectedScene ? (<>
                <div className="film-scene-editor-head"><div><span>{selectedScene.id}</span><h3>{selectedScene.title}</h3></div><button onClick={saveScene}><Save size={14} /> Lưu scene</button></div>
                {!!selectedScene.warnings.length && <div className="continuity-warning"><AlertTriangle size={14} /><div><strong>CONTINUITY WARNING</strong>{selectedScene.warnings.map((w, i) => <p key={i}>{w}</p>)}</div></div>}
                <div className="film-editor-fields">
                  <label>Scene title<input value={field('title')} onChange={e => setField('title', e.target.value)} /></label>
                  <label>Original text<textarea value={field('source_text')} onChange={e => setField('source_text', e.target.value)} /></label>
                  <label>Summary<textarea value={field('summary')} onChange={e => setField('summary', e.target.value)} /></label>
                  <div className="film-two-cols"><label>Character IDs<input value={(draft.characters as string[] | undefined)?.join(', ') ?? selectedScene.characters.join(', ')} onChange={e => setField('characters', e.target.value.split(',').map(x => x.trim()).filter(Boolean))} /></label><label>Location ID<input value={field('location_id')} onChange={e => setField('location_id', e.target.value)} /></label></div>
                  <label>Action<textarea value={field('action')} onChange={e => setField('action', e.target.value)} /></label>
                  <div className="film-two-cols"><label>Camera<textarea value={field('camera')} onChange={e => setField('camera', e.target.value)} /></label><label>Duration<input type="number" min={1} max={60} value={Number(draft.duration ?? selectedScene.duration)} onChange={e => setField('duration', Number(e.target.value))} /></label></div>
                  <div className="film-two-cols"><label>Lighting<textarea value={field('lighting')} onChange={e => setField('lighting', e.target.value)} /></label><label>Atmosphere<textarea value={field('atmosphere')} onChange={e => setField('atmosphere', e.target.value)} /></label></div>
                  <label>Voiceover<textarea value={field('voiceover')} onChange={e => setField('voiceover', e.target.value)} /></label>
                  <label>Dialogue JSON<textarea className="film-code-area" value={dialogueText} onChange={e => setDialogueText(e.target.value)} /></label>
                  <label>Start state<textarea value={field('start_state')} onChange={e => setField('start_state', e.target.value)} /></label>
                  <label>End state<textarea value={field('end_state')} onChange={e => setField('end_state', e.target.value)} /></label>
                  <label>Visual Prompt<textarea className="film-prompt-area" value={field('visual_prompt')} onChange={e => setField('visual_prompt', e.target.value)} /></label>
                  <label>Google Flow Prompt<textarea className="film-prompt-area" value={field('flow_prompt')} onChange={e => setField('flow_prompt', e.target.value)} /></label>
                </div>
                <div className="film-scene-generate"><button disabled={renderBusy} onClick={() => queueRender([selectedScene.id])}>{renderBusy ? <Loader2 className="spin" size={14} /> : <Play size={14} />} Generate Scene</button><span>Render adapter: {renderState?.adapter.name || 'đang kiểm tra'} · {renderState?.adapter.configured ? 'READY' : 'NOT CONFIGURED'}</span>{selectedScene.result_url && <a href={selectedScene.result_url} target="_blank" rel="noreferrer">Mở video kết quả</a>}</div>
              </>) : <div className="film-empty-editor">Chọn một scene để chỉnh sửa.</div>}
            </section>
          </div>
          <div className="film-render-panel">
            <div className="film-render-head">
              <div><span>RENDER ENGINE</span><strong>{renderState?.adapter.name || 'Đang kiểm tra adapter'}</strong><em>{renderState?.adapter.configured ? 'Adapter thật đã sẵn sàng' : 'Chưa cấu hình Video Render Adapter'} · QC: {renderState?.qc_adapter.name || 'đang kiểm tra'}</em></div>
              <div className="film-render-controls">
                {renderState?.queue.paused ? <button onClick={resumeRender}><Play size={13} /> Resume</button> : <button onClick={pauseRender}><Pause size={13} /> Pause</button>}
                <button onClick={() => refreshRender(active.id)}><RotateCcw size={13} /> Refresh</button>
              </div>
            </div>
            {!renderState?.adapter.configured && <div className="render-adapter-note"><AlertTriangle size={14} /><div><strong>Queue hoạt động nhưng chưa có dịch vụ tạo video thật.</strong><p>Có thể cấu hình adapter HTTP bằng FILM_RENDER_ADAPTER=http_json và FILM_RENDER_API_URL. Hệ thống không đánh dấu Completed nếu chưa nhận video thật.</p></div></div>}
            <div className="film-render-jobs">
              {(renderState?.jobs || []).map(job => (
                <div className={`film-render-job ${job.status}`} key={job.id}>
                  <div className="render-job-main">
                    <div className="render-job-scene"><strong>{job.scene_id}</strong><span>Attempt {job.attempt + 1}</span></div>
                    <div className="render-job-progress"><div><i style={{ width: `${job.progress}%` }} /></div><span>{job.progress}%</span></div>
                    <b>{job.status}</b>
                    {job.status === 'completed' ? <CheckCircle2 size={15} /> : ['preparing','generating'].includes(job.status) ? <Loader2 className="spin" size={15} /> : job.status === 'failed' ? <AlertTriangle size={15} /> : <span />}
                  </div>
                  <div className="render-job-meta">
                    {job.reference?.previous_scene_id ? <span>Continuity từ {String(job.reference.previous_scene_id)}</span> : <span>Opening scene</span>}
                    {job.reference?.previous_last_frame_url ? <span>Last-frame reference: có</span> : <span>Last-frame reference: chưa có</span>}
                    <span>QC: {job.qc_status}{job.consistency_score != null ? ` · ${job.consistency_score}/100` : ''}</span>
                  </div>
                  {job.error && <p className="render-job-error">{job.error}</p>}
                  <div className="render-job-actions">
                    {job.result_url && <a href={job.result_url} target="_blank" rel="noreferrer">Mở video</a>}
                    {(job.status === 'failed' || job.qc_status === 'failed') && <button onClick={() => retryRender(job.id)}><RotateCcw size={12} /> {job.qc_status === 'failed' ? 'Regenerate scene' : 'Retry scene'}</button>}
                  </div>
                </div>
              ))}
              {!renderState?.jobs.length && <div className="render-empty">Chưa có render job. Chọn scene hoặc dùng Generate All.</div>}
            </div>
          </div>
          <div className="film-bottom-actions">
            <button onClick={async () => setActive(await api.analyzeFilmProject(active.id))}><Sparkles size={14} /> ANALYZE</button><button onClick={checkContinuity}><AlertTriangle size={14} /> CONTINUITY CHECK</button>
            <button disabled={renderBusy || (!selectedForRender.length && !activeSceneId)} onClick={() => queueRender(selectedForRender.length ? selectedForRender : (activeSceneId ? [activeSceneId] : []))}><Play size={14} /> GENERATE SELECTED</button>
            <button disabled={renderBusy} onClick={() => queueRender(active.scenes.map(scene => scene.id))}><Clapperboard size={14} /> GENERATE ALL</button>
            <button disabled={renderBusy} className="primary" onClick={autoPipeline}><WandSparkles size={14} /> AUTO PIPELINE</button>
            <button onClick={exportFlowPrompts}><Download size={14} /> EXPORT FLOW PROMPTS</button><button onClick={exportProject}><Download size={14} /> EXPORT PROJECT</button>
          </div>
        </div>
      )}
    </section>
  )
}
