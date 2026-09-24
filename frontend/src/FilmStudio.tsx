import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, ArrowRight, BookOpen, CheckCircle2, ChevronDown, ChevronUp, Clapperboard, Download, FileJson, Film, FolderOpen, Loader2, LockKeyhole, MapPin, Pause, Play, Plus, RotateCcw, Save, Sparkles, Trash2, WandSparkles } from 'lucide-react'
import { api } from './api'
import FilmContinuityControlCenter from './FilmContinuityControlCenter'
import FilmMediaGallery from './FilmMediaGallery'
import FilmSceneMedia from './FilmSceneMedia'
import { notifyDesktop, onDesktopNotificationAction } from './desktopNotifications'
import { openProjectCanonicalFolder, type DesktopSettings } from './desktopSettings'
import { adapterNameVi, canonicalStageVi, errorCodeVi, eventTypeVi, FILM_STYLE_OPTIONS, filmStatusVi, jsonTextVi, qcStatusVi, renderStatusVi, stageVi, styleLabelVi, uiErrorVi } from './filmVi'
import type { FilmCanonicalGenerationStatus, FilmFinalStatus, FilmGeneratedMedia, FilmPipelineEvent, FilmPipelineStatus, FilmProject, FilmProjectSummary, FilmProviderResource, FilmRenderJob, FilmRenderStatus, FilmScene, FilmSettings, FlowImageCapabilities, FlowProject, FlowVideoCapabilities, Provider } from './types'

type Props = {
  providerId: string
  model: string
  provider?: Provider
  onOpenSettings: () => void
  desktopSettings?: DesktopSettings | null
}

const DEFAULT_SETTINGS: FilmSettings = {
  scene_duration: 8,
  aspect_ratio: '16:9',
  resolution: '1080p',
  style: 'Cinematic',
  character_lock: true,
  location_lock: true,
  auto_continuity: true,
  require_provider_assets: true,
}

const desktopFilmDefaults = (desktop?: DesktopSettings | null): FilmSettings => ({
  ...DEFAULT_SETTINGS,
  aspect_ratio: desktop?.default_video_aspect_ratio || DEFAULT_SETTINGS.aspect_ratio,
  resolution: desktop?.default_video_resolution || DEFAULT_SETTINGS.resolution,
  flow_model: desktop?.default_video_model || null,
})

const audioQcSummary = (job: FilmRenderJob) => {
  const audio = (job.qc?.audio_check || null) as Record<string, unknown> | null
  if (!audio) return ''
  const present = audio.present === true
  const nonSilent = audio.non_silent === true
  const peak = typeof audio.max_volume_db === 'number' ? ' · mức đỉnh ' + audio.max_volume_db + ' dB' : ''
  return 'Âm thanh: ' + (present ? 'Có' : 'Không có') + ' · ' + (nonSilent ? 'Có tín hiệu âm thanh' : 'Im lặng') + peak
}

const speechQcSummary = (job: FilmRenderJob) => {
  const speech = (job.qc?.speech_check || null) as Record<string, unknown> | null
  if (!speech) return ''
  const passed = speech.passed === true
  const score = typeof speech.match_score === 'number' ? ' · ' + Math.round(speech.match_score * 100) + '%' : ''
  const transcript = typeof speech.transcript === 'string' ? speech.transcript.trim() : ''
  const clipped = transcript.length > 90 ? transcript.slice(0, 87) + '...' : transcript
  return 'Lời thoại: ' + (passed ? 'Đạt' : 'Không đạt') + score + (clipped ? ' · ' + clipped : '')
}

const visualQcSummary = (job: FilmRenderJob) => {
  const gate = (job.qc?.hard_gate || null) as Record<string, unknown> | null
  const dimensions = (gate?.dimensions || null) as Record<string, Record<string, unknown>> | null
  if (!dimensions) return ''
  const names: Record<string, string> = {
    identity: 'Nhân vật',
    wardrobe: 'Trang phục',
    location: 'Bối cảnh',
    prop: 'Đạo cụ',
    boundary: 'Nối cảnh',
  }
  const parts = Object.entries(dimensions).map(([key, value]) => {
    const score = typeof value.score === 'number' ? Math.round(value.score) : '?'
    return `${names[key] || key}: ${score}/100${value.passed === true ? ' ✓' : ' ✕'}`
  })
  return 'Visual Lock: ' + parts.join(' · ')
}

export default function FilmStudio({ providerId, model, provider, onOpenSettings, desktopSettings }: Props) {
  const [projects, setProjects] = useState<FilmProjectSummary[]>([])
  const [active, setActive] = useState<FilmProject | null>(null)
  const [activeSceneId, setActiveSceneId] = useState<string | null>(null)
  const [story, setStory] = useState('')
  const [name, setName] = useState('')
  const [settings, setSettings] = useState<FilmSettings>(() => desktopFilmDefaults(desktopSettings))
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState('')
  const [projectTab, setProjectTab] = useState<'original' | 'settings' | 'story' | 'characters' | 'locations' | 'props' | 'master'>('story')
  const [draft, setDraft] = useState<Partial<FilmScene>>({})
  const [dialogueText, setDialogueText] = useState('[]')
  const [renderState, setRenderState] = useState<FilmRenderStatus | null>(null)
  const [resources, setResources] = useState<FilmProviderResource[]>([])
  const [resourceBusy, setResourceBusy] = useState<string>('')
  const [renderBusy, setRenderBusy] = useState(false)
  const [productionBusy, setProductionBusy] = useState(false)
  const [consistencyBusy, setConsistencyBusy] = useState(false)
  const [selectedForRender, setSelectedForRender] = useState<string[]>([])
  const [flowProjects, setFlowProjects] = useState<FlowProject[]>([])
  const [flowCaps, setFlowCaps] = useState<FlowVideoCapabilities | null>(null)
  const [flowImageCaps, setFlowImageCaps] = useState<FlowImageCapabilities | null>(null)
  const [canonicalProvider, setCanonicalProvider] = useState<'flow' | 'xkiro'>('flow')
  const [canonicalModel, setCanonicalModel] = useState('Nano Banana 2')
  const [flowLoading, setFlowLoading] = useState(false)
  const [flowAuthenticated, setFlowAuthenticated] = useState<boolean | null>(null)
  const [flowSessionHint, setFlowSessionHint] = useState('')
  const [mediaItems, setMediaItems] = useState<FilmGeneratedMedia[]>([])
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [wizardPipelineStartedProject, setWizardPipelineStartedProject] = useState<string | null>(null)
  const [pipelineState, setPipelineState] = useState<FilmPipelineStatus | null>(null)
  const [finalState, setFinalState] = useState<FilmFinalStatus | null>(null)
  const [wizardBusy, setWizardBusy] = useState(false)
  const [canonicalRunState, setCanonicalRunState] = useState<FilmCanonicalGenerationStatus | null>(null)
  const [canonicalEvents, setCanonicalEvents] = useState<FilmPipelineEvent[]>([])
  const [canonicalStopping, setCanonicalStopping] = useState(false)
  const notificationProjectRef = useRef('')
  const notificationsReadyRef = useRef(false)
  const lastCanonicalLockedRef = useRef(false)
  const lastFlowAuthenticatedRef = useRef<boolean | null>(null)
  const lastFinalApprovedRef = useRef(false)
  const lastSceneStatusRef = useRef<Map<string, string>>(new Map())

  const refreshProjects = useCallback(async () => {
    setProjects(await api.filmProjects())
  }, [])
  const refreshRender = useCallback(async (projectId: string) => {
    const state = await api.filmRenderStatus(projectId)
    setRenderState(state)
    return state
  }, [])
  const refreshResources = useCallback(async (projectId: string) => {
    const data = await api.syncFilmResources(projectId)
    setResources(data.resources || [])
    return data.resources || []
  }, [])
  const refreshMedia = useCallback(async (projectId: string) => {
    const data = await api.filmMedia(projectId)
    setMediaItems(data.media || [])
    return data.media || []
  }, [])
  const refreshCanonicalRuntime = useCallback(async (projectId: string) => {
    const status = await api.canonicalGenerationStatus(projectId)
    setCanonicalRunState(status)
    setResources(previous => status.resources?.length ? status.resources : previous)
    setCanonicalEvents(status.events || [])
    return status
  }, [])
  const refreshWizardProgress = useCallback(async (projectId: string) => {
    const [pipeline, final] = await Promise.all([
      api.filmPipelineStatus(projectId),
      api.filmFinalStatus(projectId),
    ])
    setPipelineState(pipeline)
    setFinalState(final)
    return { pipeline, final }
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => { void refreshProjects() }, 0)
    return () => window.clearTimeout(timer)
  }, [refreshProjects])

  const refreshFlowProduction = useCallback(async (projectId?: string | null) => {
    setFlowLoading(true)
    try {
      const session = await api.testFlow()
      setFlowAuthenticated(session.authenticated)
      const sessionUrl = String((session.session as Record<string, unknown> | undefined)?.url || '')
      if (!session.authenticated) {
        setFlowProjects([])
        setFlowCaps(null)
        setFlowImageCaps(null)
        setFlowSessionHint(
          sessionUrl.includes('accounts.google.com')
            ? 'Trình duyệt Flow đang ở trang đăng nhập Google. Hãy hoàn tất đăng nhập, rồi bấm Làm mới danh sách mô hình.'
            : sessionUrl.includes('/about')
              ? 'Trình duyệt mới mở trang giới thiệu Flow, chưa vào workspace. Bấm Mở đăng nhập để vào tài khoản.'
              : 'Phiên Flow chưa sẵn sàng. Bấm Mở đăng nhập, đăng nhập Google, rồi tải lại mô hình.'
        )
        return false
      }
      setFlowSessionHint('')
      const projectsData = await api.flowProjects()
      const imageCaps = await api.flowImageCapabilities(projectId || undefined)
      const caps = await api.flowVideoCapabilities(projectId || undefined)
      setFlowProjects(projectsData.projects)
      setFlowImageCaps(imageCaps)
      setFlowCaps(caps)
      if (canonicalProvider === 'flow' && imageCaps.models.length) {
        setCanonicalModel(prev => (
          imageCaps.models.includes(prev)
            ? prev
            : (imageCaps.models.includes('Nano Banana 2') ? 'Nano Banana 2' : imageCaps.models[0])
        ))
      }
      return true
    } catch (error) {
      const message = (error as Error).message || ''
      setFlowAuthenticated(false)
      setFlowProjects([])
      setFlowCaps(null)
      setFlowImageCaps(null)
      setFlowSessionHint(
        message.includes('Chưa kết nối Internet')
          ? 'Chưa kết nối Internet. Dự án local vẫn sử dụng được; Flow sẽ hoạt động lại khi mạng trở lại.'
          : 'Không kết nối được trình duyệt Flow. Bấm Mở đăng nhập để khởi động lại phiên.'
      )
      return false
    } finally {
      setFlowLoading(false)
    }
  }, [canonicalProvider])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void refreshFlowProduction(settings.flow_project_id)
    }, 0)
    return () => window.clearTimeout(timer)
  }, [refreshFlowProduction, settings.flow_project_id])

  useEffect(() => {
    if (flowAuthenticated !== true) return
    const timer = window.setInterval(() => {
      refreshFlowProduction(settings.flow_project_id).catch(() => undefined)
    }, 30000)
    return () => window.clearInterval(timer)
  }, [flowAuthenticated, refreshFlowProduction, settings.flow_project_id])

  const activeId = active?.id || ''
  const activeStatus = active?.status || ''

  useEffect(() => {
    if (!activeId || !['queued', 'analyzing'].includes(activeStatus)) return
    const timer = window.setInterval(async () => {
      try {
        const next = await api.filmProject(activeId)
        setActive(next)
        if (next.status === 'ready' && next.scenes.length) {
          setActiveSceneId(prev => prev || next.scenes[0].id)
          if (desktopSettings?.notifications_enabled !== false) {
            void notifyDesktop('TH Media · Phân tích hoàn tất', `${next.name}: ${next.scenes.length} phân cảnh đã sẵn sàng.`, { project_id: next.id })
          }
        }
        if (next.status === 'failed') setError(next.error || 'Phân tích dự án thất bại')
        if (['ready', 'failed'].includes(next.status)) await refreshProjects()
      } catch (e) { setError(uiErrorVi((e as Error).message)) }
    }, 1800)
    return () => window.clearInterval(timer)
  }, [activeId, activeStatus, desktopSettings?.notifications_enabled, refreshProjects])

  useEffect(() => {
    if (!activeId) return
    const timer = window.setTimeout(() => {
      void refreshCanonicalRuntime(activeId)
      void refreshMedia(activeId)
      if (activeStatus === 'ready') void refreshWizardProgress(activeId)
    }, 0)
    return () => window.clearTimeout(timer)
  }, [activeId, activeStatus, refreshCanonicalRuntime, refreshMedia, refreshWizardProgress])

  useEffect(() => {
    if (!activeId || activeStatus !== 'ready') return
    const running = pipelineState?.worker_active || ['running', 'paused', 'stopping'].includes(String(pipelineState?.run?.status || ''))
    const finalBusy = ['ASSEMBLING', 'QC_PENDING', 'QC_RUNNING'].includes(String(finalState?.current?.status || ''))
    if (!running && !finalBusy && wizardPipelineStartedProject !== activeId) return
    const timer = window.setInterval(() => {
      void refreshWizardProgress(activeId).catch(() => undefined)
      void refreshMedia(activeId).catch(() => undefined)
    }, 2500)
    return () => window.clearInterval(timer)
  }, [activeId, activeStatus, finalState?.current?.status, pipelineState?.run?.status, pipelineState?.worker_active, refreshMedia, refreshWizardProgress, wizardPipelineStartedProject])

  useEffect(() => {
    if (!activeId || activeStatus !== 'ready') return
    let cancelled = false
    const sync = async () => {
      try {
        const [state, canonical] = await Promise.all([
          api.filmRenderStatus(activeId),
          api.canonicalGenerationStatus(activeId),
        ])
        if (cancelled) return false
        setRenderState(state)
        setCanonicalRunState(canonical)
        setCanonicalEvents(canonical.events || [])
        setResources(previous => canonical.resources?.length ? canonical.resources : previous)
        const mediaBusy = canonical.active || (canonical.resources || []).some(item => String(item.metadata?.canonical_qc_status || '') === 'running') || state.jobs.some(job => ['waiting', 'preparing', 'generating'].includes(job.status))
        await refreshMedia(activeId)
        if (state.jobs.some(job => ['preparing', 'generating', 'completed', 'failed'].includes(job.status))) {
          const project = await api.filmProject(activeId)
          if (!cancelled) setActive(project)
        }
        return mediaBusy || resourceBusy || renderBusy
      } catch (e) {
        if (!cancelled) setError(uiErrorVi((e as Error).message))
        return false
      }
    }
    let timer: number | undefined
    const tick = async () => {
      const busy = await sync()
      if (!cancelled && busy) timer = window.setTimeout(tick, 2200)
    }
    tick()
    return () => { cancelled = true; if (timer) window.clearTimeout(timer) }
  }, [activeId, activeStatus, refreshMedia, renderBusy, resourceBusy])

  useEffect(() => {
    if (!activeId || activeStatus !== 'ready' || !canonicalRunState?.active || !canonicalRunState.run?.id) return
    const controller = new AbortController()
    let refreshTimer: number | undefined
    const scheduleRefresh = () => {
      if (refreshTimer) window.clearTimeout(refreshTimer)
      refreshTimer = window.setTimeout(() => {
        void refreshCanonicalRuntime(activeId).catch(() => undefined)
        void refreshMedia(activeId).catch(() => undefined)
      }, 180)
    }
    void api.streamCanonicalGenerationEvents(activeId, event => {
      setCanonicalEvents(previous => {
        if (previous.some(item => item.id === event.id)) return previous
        return [...previous, event].slice(-100)
      })
      if (/CANONICAL_(RESOURCE|RUN)_(PROGRESS|DOWNLOADED|QC_STARTED|QC_PASSED|QC_FAILED|COMPLETED|FAILED|STOPPED)$/.test(event.event_type)) {
        scheduleRefresh()
      }
    }, controller.signal).catch(error => {
      if (!controller.signal.aborted) console.warn('Canonical SSE fallback to polling:', error)
    })
    return () => {
      controller.abort()
      if (refreshTimer) window.clearTimeout(refreshTimer)
    }
  }, [activeId, activeStatus, canonicalRunState?.active, canonicalRunState?.run?.id, refreshCanonicalRuntime, refreshMedia])

  const selectedScene = useMemo(
    () => active?.scenes.find(scene => scene.id === activeSceneId) || active?.scenes[0] || null,
    [active, activeSceneId],
  )
  const consistencyReady = active?.consistency_report?.final_gate === true
  const consistencyStatus = active?.consistency_report?.status || 'CHƯA KIỂM TRA'
  const consistencyRepairableCount = active?.consistency_report?.repairable_items?.length || 0
  const canAutoRepairConsistency = !consistencyReady
    && (consistencyStatus === 'REPAIRABLE' || consistencyRepairableCount > 0)
  const consistencyIssues = [
    ...(active?.consistency_report?.effective_errors || []),
    ...(active?.consistency_report?.review_items || []),
  ]
  const productionGateReady = active?.production_gate?.final_gate === true
  const productionReady = consistencyReady && productionGateReady
  const productionErrors = active?.production_gate?.errors || []
  const visualAssetsRequired = renderState?.adapter.id === 'flow_bridge' || settings.require_provider_assets === true
  const canonicalGenerating = canonicalRunState?.active === true || resources.some(item => ['queued', 'starting', 'generating', 'regenerating', 'qc_running', 'stopping'].includes(String(item.metadata?.generation_status || '')))
  const canonicalQcRunning = resources.some(item => String(item.metadata?.canonical_qc_status || '') === 'running')
  const canonicalProviderReady = canonicalProvider === 'xkiro' || flowAuthenticated === true
  const activeCanonicalResources = resources.filter(item => item.status !== 'retired')
  const canonicalReady = activeCanonicalResources.length > 0
    && activeCanonicalResources.every(item => {
      const qc = item.metadata?.canonical_qc as Record<string, unknown> | undefined
      const gate = qc?.hard_gate as Record<string, unknown> | undefined
      return ['ready', 'locked'].includes(item.status) && gate?.passed === true
    })
  const canonicalLocked = !visualAssetsRequired || (activeCanonicalResources.length > 0
    && activeCanonicalResources.every(item => item.status === 'locked')
    && canonicalReady)
  const renderGateReady = productionReady && (!visualAssetsRequired || canonicalReady)
  const pipelineTotal = pipelineState?.counts?.total || active?.scenes.length || 0
  const pipelineApproved = pipelineState?.counts?.approved || 0
  const scenesApproved = pipelineTotal > 0 && pipelineApproved === pipelineTotal
  const finalApproved = finalState?.current?.status === 'APPROVED'
  const wizardStep = !canonicalLocked ? 3 : !productionReady ? 4 : !scenesApproved ? 5 : 6
  const wizardSteps = [
    { id: 1, label: 'Nhập kịch bản', done: true },
    { id: 2, label: 'Phân tích', done: active?.status === 'ready' },
    { id: 3, label: 'Tạo ảnh chuẩn', done: canonicalLocked },
    { id: 4, label: 'Kiểm tra nhất quán', done: productionReady },
    { id: 5, label: 'Tạo phim', done: scenesApproved },
    { id: 6, label: 'Ghép & Master QC', done: finalApproved },
  ]

  useEffect(() => {
    if (!activeId) {
      notificationProjectRef.current = ''
      notificationsReadyRef.current = false
      lastSceneStatusRef.current = new Map()
      return
    }
    const sceneStatuses = new Map((pipelineState?.scenes || []).map(scene => [scene.scene_id, scene.status]))
    if (notificationProjectRef.current !== activeId) {
      notificationProjectRef.current = activeId
      lastCanonicalLockedRef.current = canonicalLocked
      lastFlowAuthenticatedRef.current = flowAuthenticated
      lastFinalApprovedRef.current = finalApproved
      lastSceneStatusRef.current = sceneStatuses
      const timer = window.setTimeout(() => { notificationsReadyRef.current = true }, 0)
      return () => window.clearTimeout(timer)
    }
    if (!notificationsReadyRef.current) return
    if (desktopSettings?.notifications_enabled === false) {
      lastCanonicalLockedRef.current = canonicalLocked
      lastFlowAuthenticatedRef.current = flowAuthenticated
      lastFinalApprovedRef.current = finalApproved
      lastSceneStatusRef.current = sceneStatuses
      return
    }

    if (!lastCanonicalLockedRef.current && canonicalLocked) {
      void notifyDesktop('TH Media · Ảnh chuẩn hoàn tất', `${active?.name || 'Dự án'}: bộ ảnh chuẩn đã QC và khóa.`, { project_id: activeId })
    }
    if (lastFlowAuthenticatedRef.current === true && flowAuthenticated === false) {
      void notifyDesktop('TH Media · Google Flow cần đăng nhập lại', `${active?.name || 'Dự án'}: phiên Google Flow đã hết hoặc mất kết nối.`, { project_id: activeId, open_advanced: true })
    }
    if (!lastFinalApprovedRef.current && finalApproved) {
      void notifyDesktop('TH Media · Phim đã hoàn thành', `${active?.name || 'Dự án'}: Final Film đã ghép và Master QC đạt.`, { project_id: activeId })
    }

    const previous = lastSceneStatusRef.current
    for (const [sceneId, status] of sceneStatuses) {
      const before = previous.get(sceneId)
      if (!before || before === status) continue
      if (status === 'APPROVED') {
        void notifyDesktop('TH Media · Phân cảnh hoàn tất', `${sceneId} đã tạo xong và QC đạt.`, { project_id: activeId, scene_id: sceneId })
      } else if (status === 'BLOCKED' || status === 'QC_FAILED' || status === 'FAILED') {
        void notifyDesktop('TH Media · Phân cảnh cần xử lý', `${sceneId} dừng ở trạng thái ${status}.`, { project_id: activeId, scene_id: sceneId, open_advanced: true })
      }
    }

    lastCanonicalLockedRef.current = canonicalLocked
    lastFlowAuthenticatedRef.current = flowAuthenticated
    lastFinalApprovedRef.current = finalApproved
    lastSceneStatusRef.current = sceneStatuses
  }, [active?.name, activeId, canonicalLocked, desktopSettings?.notifications_enabled, finalApproved, flowAuthenticated, pipelineState?.scenes])

  const latestRenderByScene = useMemo(() => {
    const map = new Map<string, NonNullable<FilmRenderStatus['jobs']>[number]>()
    for (const job of renderState?.jobs || []) map.set(job.scene_id, job)
    return map
  }, [renderState])
  const galleryItems = useMemo(() => {
    if (!active) return mediaItems
    const live: FilmGeneratedMedia[] = []
    for (const job of renderState?.jobs || []) {
      if (!['waiting', 'preparing', 'generating', 'failed'].includes(job.status)) continue
      const hasFile = mediaItems.some(item => item.scene_id === job.scene_id && item.file_url)
      if (job.status === 'failed' && hasFile) continue
      if (['waiting', 'preparing', 'generating'].includes(job.status) && hasFile) continue
      live.push({
        id: `live-job-${job.id}`,
        project_id: active.id,
        scene_id: job.scene_id,
        output_key: `scene_video:${job.scene_id}`,
        media_type: 'video',
        role: 'scene_video',
        status: job.status === 'failed' ? 'failed' : 'processing',
        version: 0,
        is_selected: false,
        qc_status: job.qc_status || 'pending',
        qc: job.qc || {},
        metadata: { progress: job.progress, error: job.error || '' },
        created_at: job.created_at,
        updated_at: job.updated_at,
      })
    }
    for (const resource of resources) {
      const gen = String(resource.metadata?.generation_status || '')
      if (!['queued', 'starting', 'opening_flow_project', 'awaiting_generation', 'downloading_result', 'downloaded', 'generating', 'regenerating', 'qc_running', 'stopping'].includes(gen)) continue
      if (mediaItems.some(item => item.entity_id === resource.entity_id && item.file_url)) continue
      live.push({
        id: `live-res-${resource.id}`,
        project_id: active.id,
        resource_type: resource.resource_type,
        entity_id: resource.entity_id,
        output_key: `canonical:${resource.resource_type}:${resource.entity_id}`,
        media_type: 'image',
        role: 'canonical_image',
        status: 'processing',
        version: 0,
        is_selected: false,
        qc_status: String(resource.metadata?.canonical_qc_status || 'pending'),
        qc: {},
        metadata: {},
        created_at: resource.created_at,
        updated_at: resource.updated_at,
      })
    }
    return [...live, ...mediaItems]
  }, [active, mediaItems, renderState, resources])
  const canonicalGalleryItems = useMemo(
    () => galleryItems.filter(item => item.role === 'canonical_image' || (item.role === 'repair_candidate' && !!item.resource_type)),
    [galleryItems],
  )
  const canonicalFailedCount = activeCanonicalResources.filter(item => item.status === 'error' || ['failed', 'blocked'].includes(String(item.metadata?.generation_status || ''))).length
  const canonicalCounts = canonicalRunState?.counts

  const selectedSceneId = selectedScene?.id || ''
  const selectedDialogueText = useMemo(
    () => JSON.stringify(selectedScene?.dialogue || [], null, 2),
    [selectedScene?.dialogue],
  )

  useEffect(() => {
    if (!selectedSceneId) return
    const timer = window.setTimeout(() => {
      setDraft({})
      setDialogueText(selectedDialogueText)
    }, 0)
    return () => window.clearTimeout(timer)
  }, [selectedDialogueText, selectedSceneId])

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
    } catch (e) { setError(uiErrorVi((e as Error).message)) }
    finally { setCreating(false) }
  }

  const openProject = useCallback(async (id: string) => {
    try {
      const project = await api.filmProject(id)
      let canonical: FilmCanonicalGenerationStatus | null = null
      let syncedResources: FilmProviderResource[] = []
      if (project.status === 'ready') {
        const synced = await api.syncFilmResources(project.id)
        syncedResources = synced.resources || []
      }
      canonical = await api.canonicalGenerationStatus(project.id)
      if (!syncedResources.length && canonical.resources?.length) {
        syncedResources = canonical.resources
      }
      setActive(project)
      setSettings(project.settings || desktopFilmDefaults(desktopSettings))
      setStory(project.original_text)
      setName(project.name)
      setActiveSceneId(project.scenes[0]?.id || null)
      setShowAdvanced(false)
      setWizardPipelineStartedProject(null)
      setPipelineState(null)
      setFinalState(null)
      setCanonicalRunState(canonical)
      setResources(canonical?.resources?.length ? canonical.resources : syncedResources)
      setCanonicalEvents(canonical?.events || [])
      setError('')
    } catch (e) { setError(uiErrorVi((e as Error).message)) }
  }, [desktopSettings])

  useEffect(() => {
    let disposed = false
    let unregister: (() => Promise<void>) | null = null
    void onDesktopNotificationAction(async extra => {
      const projectId = String(extra.project_id || '')
      const sceneId = String(extra.scene_id || '')
      if (!projectId) return
      await openProject(projectId)
      if (sceneId) setActiveSceneId(sceneId)
      if (extra.open_advanced === true) setShowAdvanced(true)
    }).then(listener => {
      if (!listener) return
      if (disposed) void listener.unregister()
      else unregister = () => listener.unregister()
    })
    return () => {
      disposed = true
      if (unregister) void unregister()
    }
  }, [openProject])

  const newProject = () => {
    setActive(null)
    setActiveSceneId(null)
    setStory('')
    setName('')
    setSettings(desktopFilmDefaults(desktopSettings))
    setRenderState(null)
    setResources([])
    setMediaItems([])
    setResourceBusy('')
    setSelectedForRender([])
    setPipelineState(null)
    setFinalState(null)
    setWizardPipelineStartedProject(null)
    setShowAdvanced(false)
    setError('')
  }

  const removeProject = async (id: string) => {
    const project = projects.find(item => item.id === id)
    if (!window.confirm(`Xóa dự án “${project?.name || id}”? Thao tác này sẽ xóa dữ liệu dự án khỏi TH Media.`)) return
    const deleteMedia = window.confirm('Bạn có muốn xóa luôn toàn bộ media của dự án này không?\n\nOK = Xóa cả media\nCancel = Giữ media trên ổ đĩa')
    try {
      await api.deleteFilmProject(id, deleteMedia)
      if (active?.id === id) newProject()
      await refreshProjects()
      setError('')
    } catch (e) {
      setError(uiErrorVi((e as Error).message))
    }
  }

  const saveActiveSettings = async (patch: Partial<FilmSettings>) => {
    if (!active) return
    const next = { ...settings, ...patch }
    setSettings(next)
    try {
      const updated = await api.updateFilmProject(active.id, { settings: next })
      setActive(updated)
      setError('')
    } catch (e) { setError(uiErrorVi((e as Error).message)) }
  }

  const setField = (key: keyof FilmScene, value: unknown) => setDraft(prev => ({ ...prev, [key]: value }))

  const saveScene = async () => {
    if (!active || !selectedScene) return
    try {
      let dialogue = selectedScene.dialogue || []
      try { dialogue = JSON.parse(dialogueText) } catch { throw new Error('Lời thoại phải có định dạng JSON hợp lệ') }
      const updated = await api.updateFilmScene(active.id, selectedScene.id, { ...draft, dialogue })
      setActive(updated)
      setDraft({})
      setError('')
    } catch (e) { setError(uiErrorVi((e as Error).message)) }
  }

  const checkContinuity = async () => {
    if (!active || consistencyBusy) return
    setConsistencyBusy(true)
    setError('')
    try {
      const updated = await api.checkFilmContinuity(active.id)
      setActive(updated)
      const report = updated.consistency_report
      if (!report?.final_gate) {
        const first = report?.effective_errors?.[0] || report?.review_items?.[0]
        setError(first?.detail || (report?.status === 'REPAIRABLE'
          ? 'Phát hiện lỗi nhất quán có thể tự động sửa.'
          : 'Kiểm tra tính nhất quán chưa đạt. Hãy xem báo cáo chi tiết.'))
      }
    } catch (e) { setError(uiErrorVi((e as Error).message)) }
    finally { setConsistencyBusy(false) }
  }

  const repairConsistency = async () => {
    if (!active || consistencyBusy) return
    setConsistencyBusy(true)
    setError('')
    try {
      const result = await api.repairFilmConsistency(active.id)
      setActive(result.project)
      if (!result.consistency_report?.final_gate) {
        const first = result.consistency_report?.effective_errors?.[0] || result.consistency_report?.review_items?.[0]
        setError(first?.detail || 'Đã sửa dữ liệu có thể sửa tự động nhưng vẫn còn mục cần kiểm tra.')
      }
    } catch (e) { setError(uiErrorVi((e as Error).message)) }
    finally { setConsistencyBusy(false) }
  }

  const runProductionGate = async () => {
    if (!active || productionBusy) return
    setProductionBusy(true)
    setError('')
    try {
      const result = await api.runFilmProductionGate(active.id)
      setActive(result.project)
      if (!result.production_gate.final_gate) {
        const first = result.production_gate.errors?.[0]
        setError(first?.code ? errorCodeVi(first.code, first.detail) : 'Kiểm tra trước khi tạo video chưa đạt.')
      }
    } catch (e) { setError(uiErrorVi((e as Error).message)) }
    finally { setProductionBusy(false) }
  }

  const autoRepairDerived = async () => {
    if (!active || productionBusy) return
    setProductionBusy(true)
    setError('')
    try {
      const updated = await api.autoRepairFilm(active.id)
      setActive(updated)
      if (!updated.production_gate?.final_gate) {
        const first = updated.production_gate?.errors?.[0]
        setError(first?.code ? errorCodeVi(first.code, first.detail) : 'Tự động sửa đã hoàn tất nhưng dự án vẫn chưa đủ điều kiện tạo video.')
      }
    } catch (e) { setError(uiErrorVi((e as Error).message)) }
    finally { setProductionBusy(false) }
  }

  const toggleRenderSelection = (sceneId: string) => {
    setSelectedForRender(prev => prev.includes(sceneId) ? prev.filter(id => id !== sceneId) : [...prev, sceneId])
  }

  const queueRender = async (sceneIds: string[]) => {
    if (!active || !sceneIds.length || renderBusy) return
    if (!renderGateReady) {
      setError(!productionReady
        ? 'Dự án chưa đạt kiểm tra trước khi tạo video. Hãy kiểm tra toàn bộ hoặc tự động sửa dữ liệu phát sinh trước khi tạo video.'
        : 'Canonical Visual QC chưa đạt cho toàn bộ tài nguyên bắt buộc. Hãy chạy QC + tự sửa trước khi tạo video.')
      return
    }
    setRenderBusy(true)
    setError('')
    try {
      const state = await api.queueFilmRender(active.id, sceneIds)
      setRenderState(state)
      setSelectedForRender([])
      setActive(await api.filmProject(active.id))
      if (!state.adapter.configured) setError('Đã tạo hàng đợi nhưng chưa có bộ máy tạo video thật. Tác vụ sẽ giữ ở trạng thái Đang chờ cho đến khi dịch vụ được cấu hình.')
    } catch (e) { setError(uiErrorVi((e as Error).message)) }
    finally { setRenderBusy(false) }
  }

  const pauseRender = async () => {
    if (!active) return
    try { setRenderState(await api.pauseFilmRender(active.id)); setError('') } catch (e) { setError(uiErrorVi((e as Error).message)) }
  }

  const resumeRender = async () => {
    if (!active) return
    try { setRenderState(await api.resumeFilmRender(active.id)); setError('') } catch (e) { setError(uiErrorVi((e as Error).message)) }
  }

  const retryRender = async (jobId: string) => {
    if (!active) return
    try { await api.retryFilmRender(jobId); await refreshRender(active.id); setError('') } catch (e) { setError(uiErrorVi((e as Error).message)) }
  }

  const autoPipeline = async () => {
    if (!active || renderBusy || productionBusy || consistencyBusy) return
    setRenderBusy(true)
    setError('')
    try {
      let project = await api.checkFilmContinuity(active.id)

      if (!project.consistency_report?.final_gate) {
        if (project.consistency_report?.status !== 'REPAIRABLE') {
          const first = project.consistency_report?.effective_errors?.[0] || project.consistency_report?.review_items?.[0]
          throw new Error(first?.detail || 'Quy trình tạo phim tự động dừng vì kiểm tra tính nhất quán Rule + AI chưa đạt.')
        }
        const fixed = await api.repairFilmConsistency(project.id)
        project = fixed.project
      }

      if (!project.consistency_report?.final_gate) {
        throw new Error('Tự động sửa đã chạy nhưng kiểm tra tính nhất quán vẫn chưa PASS.')
      }

      if (!project.production_gate?.final_gate) {
        const gate = await api.runFilmProductionGate(project.id)
        project = gate.project
      }
      if (!project.production_gate?.final_gate) {
        project = await api.autoRepairFilm(project.id)
        project = await api.checkFilmContinuity(project.id)
        const gate = await api.runFilmProductionGate(project.id)
        project = gate.project
      }

      setActive(project)
      if (!project.consistency_report?.final_gate || !project.production_gate?.final_gate) {
        throw new Error('Quy trình tạo phim tự động chưa vượt qua đầy đủ Consistency V2 và Production Gate.')
      }

      await api.startFilmPipeline(project.id)
      await refreshMedia(project.id)
    } catch (e) { setError(uiErrorVi((e as Error).message)) }
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

  const exportCanonicalDiagnostics = () => {
    if (!active) return
    const payload = {
      schema: 'th-media-canonical-diagnostics-v1',
      captured_at: new Date().toISOString(),
      project: { id: active.id, name: active.name, status: active.status, stage: active.stage },
      run: canonicalRunState?.run || null,
      active: canonicalRunState?.active || false,
      counts: canonicalRunState?.counts || null,
      by_type: canonicalRunState?.by_type || null,
      resources: activeCanonicalResources.map(item => ({
        id: item.id,
        resource_type: item.resource_type,
        entity_id: item.entity_id,
        status: item.status,
        local_path: item.local_path || null,
        error: item.error || null,
        generation_status: item.metadata?.generation_status || null,
        provider_stage: item.metadata?.provider_stage || null,
        provider_progress: item.metadata?.provider_progress ?? null,
        provider_error_code: item.metadata?.provider_error_code || null,
        canonical_qc_status: item.metadata?.canonical_qc_status || null,
        canonical_qc: item.metadata?.canonical_qc || null,
        generation_provider: item.metadata?.generation_provider || null,
        generation_model: item.metadata?.generation_model || item.metadata?.model || null,
        updated_at: item.updated_at,
      })),
      events: canonicalEvents.slice(-100),
      media: canonicalGalleryItems.map(item => ({
        id: item.id,
        role: item.role,
        status: item.status,
        provider: item.provider || null,
        model: item.model || null,
        file_url: item.file_url || null,
        qc_status: item.qc_status,
        qc_score: item.qc_score ?? null,
        version: item.version,
        is_selected: item.is_selected,
      })),
    }
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
    const href = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = href
    a.download = `TH_Media_${active.id.slice(0, 8)}_canonical_diagnostics_${new Date().toISOString().replace(/[:.]/g, '-')}.json`
    a.click()
    URL.revokeObjectURL(href)
  }

  const openCanonicalAssetsFolder = async () => {
    if (!active) return
    try {
      await openProjectCanonicalFolder(active.id)
    } catch (e) {
      setError(uiErrorVi((e as Error).message))
    }
  }

  const generateCanonicalAssets = async (resourceType?: FilmProviderResource['resource_type'], entityIds?: string[]) => {
    if (!active || canonicalGenerating) return
    setResourceBusy(resourceType ? `ai-${resourceType}` : 'ai-all')
    setError('')
    try {
      const result = await api.generateFilmResources(active.id, resourceType, entityIds, canonicalProvider, canonicalModel)
      setResources(result.resources || [])
      await refreshCanonicalRuntime(active.id)
      await refreshMedia(active.id)
      if (!result.accepted) {
        setError('Không có tài nguyên nào cần AI tạo mới.')
      }
    } catch (e) {
      setError(uiErrorVi((e as Error).message))
      await refreshCanonicalRuntime(active.id).catch(() => undefined)
    } finally {
      setResourceBusy('')
    }
  }

  const stopCanonicalAssets = async () => {
    if (!active || canonicalStopping) return
    setCanonicalStopping(true)
    setError('')
    try {
      const result = await api.stopCanonicalGeneration(active.id)
      setCanonicalRunState(result.status)
      setResources(result.status.resources || [])
      setCanonicalEvents(result.status.events || [])
      if (!result.requested) setError('Không có tiến trình tạo ảnh chuẩn nào đang chạy.')
    } catch (e) {
      setError(uiErrorVi((e as Error).message))
    } finally {
      setCanonicalStopping(false)
    }
  }

  const retryFailedCanonicalAssets = async () => {
    if (!active || canonicalGenerating) return
    const failedIds = activeCanonicalResources
      .filter(item => item.status === 'error' || ['failed', 'blocked', 'stopped'].includes(String(item.metadata?.generation_status || '')))
      .map(item => item.entity_id)
    if (!failedIds.length) {
      setError('Không có ảnh lỗi nào cần thử lại.')
      return
    }
    await generateCanonicalAssets(undefined, failedIds)
  }

  const qcCanonicalAssets = async (resourceType?: FilmProviderResource['resource_type'], entityIds?: string[], autoRepair = true) => {
    if (!active || canonicalGenerating || canonicalQcRunning) return
    setResourceBusy(resourceType ? `qc-${resourceType}` : 'qc-all')
    setError('')
    try {
      const result = await api.qcFilmResources(
        active.id,
        resourceType,
        entityIds,
        autoRepair,
        canonicalProvider,
        canonicalModel,
      )
      setResources(result.resources || [])
    } catch (e) {
      setError(uiErrorVi((e as Error).message))
    } finally {
      setResourceBusy('')
    }
  }

  const uploadCanonicalAsset = async (resource: FilmProviderResource, file?: File) => {
    if (!active || !file) return
    if (resource.status === 'locked') {
      setError('Tài nguyên này đã khóa cho quá trình render. Không thể thay ảnh canonical giữa phim.')
      return
    }
    setResourceBusy(resource.id)
    setError('')
    try {
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => resolve(String(reader.result || ''))
        reader.onerror = () => reject(new Error('Không đọc được file ảnh.'))
        reader.readAsDataURL(file)
      })
      await api.uploadFilmResourceAsset(active.id, resource.resource_type, resource.entity_id, dataUrl, file.name)
      await refreshResources(active.id)
      await refreshRender(active.id)
    } catch (e) {
      setError(uiErrorVi((e as Error).message))
    } finally {
      setResourceBusy('')
    }
  }

  const lockVisualResources = async () => {
    if (!active) return
    setResourceBusy('lock-all')
    setError('')
    try {
      const result = await api.lockFilmResources(active.id)
      setResources(result.resources || [])
      await refreshRender(active.id)
      if (result.missing.length) {
        setError(`Còn ${result.missing.length} tài nguyên chưa có ảnh canonical. Hệ thống chưa cho phép tạo video cho các cảnh phụ thuộc vào chúng.`)
      }
    } catch (e) {
      setError(uiErrorVi((e as Error).message))
    } finally {
      setResourceBusy('')
    }
  }

  const prepareWizardProductionGate = async () => {
    if (!active) return false
    let project = await api.checkFilmContinuity(active.id)
    if (!project.consistency_report?.final_gate && project.consistency_report?.status === 'REPAIRABLE') {
      project = (await api.repairFilmConsistency(project.id)).project
    }
    if (!project.consistency_report?.final_gate) {
      setActive(project)
      const first = project.consistency_report?.effective_errors?.[0] || project.consistency_report?.review_items?.[0]
      throw new Error(first?.detail || 'Kiểm tra tính nhất quán chưa đạt. Mở Nâng cao để xem chi tiết.')
    }
    let gateResult = await api.runFilmProductionGate(project.id)
    project = gateResult.project
    if (!project.production_gate?.final_gate) {
      project = await api.autoRepairFilm(project.id)
      project = await api.checkFilmContinuity(project.id)
      gateResult = await api.runFilmProductionGate(project.id)
      project = gateResult.project
    }
    setActive(project)
    if (!project.consistency_report?.final_gate || !project.production_gate?.final_gate) {
      throw new Error('Dự án vẫn còn hạng mục cần xử lý trước khi tạo video. Mở Nâng cao để xem lỗi cụ thể.')
    }
    return true
  }

  const wizardContinue = async () => {
    if (!active || wizardBusy) return
    setWizardBusy(true)
    setError('')
    try {
      if (wizardStep === 3) {
        const missingAssets = activeCanonicalResources.some(item => !item.local_path)
        if (missingAssets || !activeCanonicalResources.length) {
          await generateCanonicalAssets()
          return
        }
        if (!canonicalReady) {
          await qcCanonicalAssets(undefined, undefined, true)
          return
        }
        if (!canonicalLocked) {
          await lockVisualResources()
          return
        }
      }
      if (wizardStep === 4) {
        await prepareWizardProductionGate()
        return
      }
      if (wizardStep === 5) {
        await prepareWizardProductionGate()
        const state = await api.startFilmPipeline(active.id)
        setPipelineState(state)
        setWizardPipelineStartedProject(active.id)
        return
      }
      if (wizardStep === 6 && !finalApproved) {
        const assembled = await api.assembleFilmFinal(active.id)
        setFinalState(assembled)
        if (assembled.current?.status === 'QC_PENDING' || assembled.current?.status === 'ASSEMBLY_COMPLETE') {
          setFinalState(await api.runFilmMasterQc(active.id))
        }
      }
    } catch (e) {
      setError(uiErrorVi((e as Error).message))
    } finally {
      setWizardBusy(false)
      await refreshWizardProgress(active.id).catch(() => undefined)
      await refreshResources(active.id).catch(() => undefined)
      await refreshMedia(active.id).catch(() => undefined)
    }
  }

  const wizardActionLabel = wizardStep === 3
    ? activeCanonicalResources.some(item => !item.local_path) || !activeCanonicalResources.length
      ? 'Tạo ảnh chuẩn'
      : !canonicalReady ? 'Kiểm tra ảnh chuẩn' : 'Khóa ảnh chuẩn'
    : wizardStep === 4 ? 'Kiểm tra & sửa tự động'
      : wizardStep === 5 ? (pipelineState?.worker_active ? 'Đang tạo phim...' : 'Bắt đầu tạo phim')
        : finalApproved ? 'Phim đã hoàn tất' : 'Ghép phim & Master QC'

  const resourcePanel = (type: FilmProviderResource['resource_type'], bible: Array<Record<string, unknown>>) => {
    const rows = resources.filter(item => item.resource_type === type && item.status !== 'retired')
    const byId = new Map(rows.map(item => [item.entity_id, item]))
    return (
      <div className="visual-resource-panel">
        <div className="visual-resource-head">
          <div><strong>TÀI NGUYÊN HÌNH ẢNH CHUẨN</strong><span>Ảnh này được dùng để khóa hình ảnh xuyên suốt các phân cảnh.</span></div>
          <div className="visual-resource-head-actions">
            <button disabled={!canonicalProviderReady || canonicalGenerating || canonicalQcRunning || resourceBusy === `ai-${type}`} onClick={() => generateCanonicalAssets(type)}><Sparkles size={12} /> AI tạo nhóm</button>
            <button disabled={!canonicalProviderReady || canonicalGenerating || canonicalQcRunning || resourceBusy === `qc-${type}`} onClick={() => qcCanonicalAssets(type, undefined, true)}><CheckCircle2 size={12} /> QC + tự sửa</button>
            <button disabled={canonicalGenerating || canonicalQcRunning || resourceBusy === 'lock-all'} onClick={lockVisualResources}><LockKeyhole size={12} /> Khóa tài nguyên</button>
          </div>
        </div>
        {bible.map((entity, index) => {
          const entityId = String(entity.id || entity.character_id || entity.location_id || entity.prop_id || `${type}_${index + 1}`)
          const resource = byId.get(entityId)
          if (!resource) return <div className="visual-resource-card missing" key={entityId}><strong>{entityId}</strong><span>Chưa đồng bộ resource record.</span></div>
          const rawAssetUrl = String(resource.metadata?.canonical_asset_url || '')
          const assetVersion = Number(resource.metadata?.asset_version || 0)
          const assetUrl = rawAssetUrl ? `${rawAssetUrl}?v=${assetVersion}` : ''
          const locked = resource.status === 'locked'
          const qc = resource.metadata?.canonical_qc as Record<string, unknown> | undefined
          const hardGate = qc?.hard_gate as Record<string, unknown> | undefined
          const qcPassed = hardGate?.passed === true
          const qcScore = typeof qc?.overall_score === 'number' ? Math.round(qc.overall_score as number) : null
          const qcStatus = String(resource.metadata?.canonical_qc_status || (qcPassed ? 'passed' : 'not_checked'))
          const generationStatus = String(resource.metadata?.generation_status || '')
          const providerProgressRaw = resource.metadata?.provider_progress
          const providerProgress = typeof providerProgressRaw === 'number' ? Math.max(0, Math.min(100, Math.round(providerProgressRaw))) : null
          return (
            <div className={`visual-resource-card ${resource.status}`} key={resource.id}>
              <div className="visual-resource-preview">
                {assetUrl ? <img src={assetUrl} alt={entityId} /> : <span>CHƯA CÓ ẢNH</span>}
              </div>
              <div className="visual-resource-info">
                <strong>{entityId}</strong>
                <span>Trạng thái: {renderStatusVi(resource.status)}</span>
                <span className={qcPassed ? 'canonical-qc-pass' : qcStatus === 'failed' || qcStatus === 'error' ? 'canonical-qc-fail' : 'canonical-qc-pending'}>Canonical QC: {qcStatusVi(qcStatus)}{qcScore !== null ? ` · ${qcScore}/100` : ''}{qcPassed ? ' ✓' : ''}</span>
                {!!generationStatus && <span>AI: {canonicalStageVi(generationStatus)}{providerProgress !== null ? ` · ${providerProgress}%` : ''} · {String(resource.metadata.generation_provider || '').toUpperCase()} · {String(resource.metadata.generation_model || resource.metadata.model || '')}</span>}
                <small>{String(entity.name || entity.title || entity.description || '').slice(0, 120)}</small>
                <div className="visual-resource-row-actions">
                  <button disabled={locked || !canonicalProviderReady || canonicalGenerating || canonicalQcRunning} onClick={() => generateCanonicalAssets(type, [entityId])}><Sparkles size={11} /> {assetUrl ? 'AI tạo lại' : 'AI tạo ảnh'}</button>
                  <button disabled={!assetUrl || !canonicalProviderReady || canonicalGenerating || canonicalQcRunning} onClick={() => qcCanonicalAssets(type, [entityId], true)}><CheckCircle2 size={11} /> QC + tự sửa</button>
                  <label className={locked ? 'disabled' : ''}>
                  {resourceBusy === resource.id ? 'Đang tải...' : locked ? 'Đã khóa' : 'Chọn ảnh canonical'}
                    <input
                      type="file"
                      accept="image/png,image/jpeg,image/webp"
                      disabled={locked || resourceBusy === resource.id}
                      onChange={e => uploadCanonicalAsset(resource, e.target.files?.[0])}
                    />
                  </label>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    )
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
                  <button onClick={() => openProject(project.id)}><strong>{project.name}</strong><span>{project.scene_count} phân cảnh · {filmStatusVi(project.status)}</span></button>
                  <button className="film-project-trash" onClick={() => removeProject(project.id)}><Trash2 size={13} /></button>
                </div>
              ))}
              {!projects.length && <p>Chưa có dự án phim.</p>}
            </div>
          </aside>
          <div className="film-start-main">
            <div className="film-start-kicker"><Sparkles size={15} /> TH MEDIA · XƯỞNG SẢN XUẤT PHIM ĐIỆN ẢNH</div>
            <h1>Một câu chuyện.<br /><span>Một thế giới điện ảnh nhất quán.</span></h1>
            <p>AI đọc toàn bộ nội dung, xây dữ liệu chuẩn và kiểm tra tính nhất quán trước khi chia thành các phân cảnh có thể tạo video độc lập.</p>
            <div className="film-story-input">
              <input value={name} onChange={e => setName(e.target.value)} placeholder="Tên dự án (không bắt buộc)" />
              <textarea value={story} onChange={e => setStory(e.target.value)} placeholder="Nội dung gốc — dán kịch bản, truyện, bài viết, nội dung quảng cáo, đánh giá, phim ngắn..." />
              <div className="film-settings-grid">
                <label>Thời lượng phân cảnh<select value={settings.scene_duration} onChange={e => setSettings({ ...settings, scene_duration: Number(e.target.value) })}>{(flowCaps?.durations?.length ? flowCaps.durations : [4, 6, 8, 10, 12]).map(value => <option key={value} value={value}>{value} giây</option>)}</select></label>
                <label>Tỷ lệ<select value={settings.aspect_ratio} onChange={e => setSettings({ ...settings, aspect_ratio: e.target.value })}>{(flowCaps?.aspect_ratios?.length ? flowCaps.aspect_ratios : ['16:9', '9:16', '1:1']).map(value => <option key={value}>{value}</option>)}</select></label>
                <label>Độ phân giải<select value={settings.resolution} onChange={e => setSettings({ ...settings, resolution: e.target.value })}>{(flowCaps?.resolutions?.length ? flowCaps.resolutions : ['720p', '1080p', 'Highest']).map(value => <option key={value} value={value}>{value === 'Highest' ? 'Cao nhất' : value}</option>)}</select></label>
                <label>Phong cách<select value={settings.style} onChange={e => setSettings({ ...settings, style: e.target.value })}>{FILM_STYLE_OPTIONS.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
                <label>Dự án Google Flow<select value={settings.flow_project_id || ''} onChange={e => setSettings({ ...settings, flow_project_id: e.target.value || null, flow_model: null })}><option value="">Tự dùng dự án Flow mới nhất</option>{flowProjects.map(project => <option key={project.id} value={project.id}>{project.modified_label || 'Dự án Flow'} · {project.id.slice(0, 8)}</option>)}</select></label>
              </div>
              <div className="film-lock-row">
                <button className={settings.character_lock ? 'on' : ''} onClick={() => setSettings({ ...settings, character_lock: !settings.character_lock })}><LockKeyhole size={13} /> Khóa nhân vật</button>
                <button className={settings.location_lock ? 'on' : ''} onClick={() => setSettings({ ...settings, location_lock: !settings.location_lock })}><MapPin size={13} /> Khóa bối cảnh</button>
                <button className={settings.auto_continuity ? 'on' : ''} onClick={() => setSettings({ ...settings, auto_continuity: !settings.auto_continuity })}><WandSparkles size={13} /> Tự động nối cảnh</button>
                <button className={settings.require_provider_assets ? 'on' : ''} onClick={() => setSettings({ ...settings, require_provider_assets: !settings.require_provider_assets })}><LockKeyhole size={13} /> Bắt buộc tài nguyên tham chiếu</button>
              </div>
              <button className="film-analyze-button" disabled={!story.trim() || !model || creating} onClick={createAndAnalyze}>{creating ? <Loader2 className="spin" size={17} /> : <Sparkles size={17} />} PHÂN TÍCH NỘI DUNG</button>
              {error && <div className="film-error"><AlertTriangle size={14} />{error}</div>}
            </div>
          </div>
        </div>
      ) : ['queued', 'analyzing'].includes(active.status) ? (
        <div className="film-processing">
          <div className="film-processing-icon"><Loader2 className="spin" size={28} /></div>
          <div className="film-kicker">KIẾN TRÚC SƯ CÂU CHUYỆN</div>
          <h2>{active.name}</h2>
          <p>{stageVi(active.stage)}</p>
          <div className="film-progress"><div style={{ width: `${active.progress}%` }} /></div>
          <span>{active.progress}% · AI đang đọc toàn bộ câu chuyện trước khi chia cảnh</span>
          <button onClick={newProject}>Tạo dự án khác</button>
        </div>
      ) : active.status === 'failed' ? (
        <div className="film-processing film-failed">
          <AlertTriangle size={30} />
          <h2>Phân tích chưa hoàn tất</h2>
          <p>{uiErrorVi(active.error || 'Không rõ lỗi')}</p>
          <button onClick={async () => setActive(await api.analyzeFilmProject(active.id))}>Thử phân tích lại</button>
          <button onClick={newProject}>Dự án mới</button>
        </div>
      ) : (
        <div className="film-editor-shell">
          <div className="film-editor-head">
            <div><div className="film-kicker">DỰ ÁN PHIM</div><h2>{active.name}</h2><span>{active.scenes.length} phân cảnh · {active.settings.aspect_ratio} · {active.settings.resolution === 'Highest' ? 'Cao nhất' : active.settings.resolution} · {styleLabelVi(active.settings.style)}</span></div>
            <div className="film-head-actions">
              <button onClick={newProject}><Plus size={14} /> Dự án mới</button>
              <button className={showAdvanced ? 'active' : ''} onClick={() => setShowAdvanced(value => !value)}>
                {showAdvanced ? <ChevronUp size={14} /> : <ChevronDown size={14} />} {showAdvanced ? 'Ẩn nâng cao' : 'Nâng cao'}
              </button>
            </div>
          </div>
          {error && <div className="film-error editor-error"><AlertTriangle size={14} />{error}</div>}
          <section className="film-wizard" aria-label="Quy trình tạo phim đơn giản">
            <div className="film-wizard-steps">
              {wizardSteps.map(step => (
                <div key={step.id} className={`film-wizard-step ${step.done ? 'done' : step.id === wizardStep ? 'current' : ''}`}>
                  <span>{step.done ? <CheckCircle2 size={15} /> : step.id}</span>
                  <div><strong>Bước {step.id}</strong><em>{step.label}</em></div>
                </div>
              ))}
            </div>
            <div className="film-wizard-main">
              <div>
                <span className="film-wizard-kicker">BƯỚC {wizardStep}/6</span>
                <h3>{wizardSteps.find(item => item.id === wizardStep)?.label}</h3>
                <p>{wizardStep === 3
                  ? canonicalGenerating ? 'AI đang tạo bộ ảnh nhân vật, bối cảnh và đạo cụ chuẩn.' : canonicalQcRunning ? 'Vision QC đang kiểm tra và tự sửa bộ ảnh chuẩn.' : canonicalReady && !canonicalLocked ? 'Ảnh chuẩn đã đạt QC. Khóa chúng trước khi tạo video.' : 'Tạo và kiểm tra bộ ảnh chuẩn để giữ nhân vật, bối cảnh và đạo cụ nhất quán.'
                  : wizardStep === 4
                    ? 'Hệ thống tự chạy Rule + AI, Evidence Verifier và Production Gate. Các lỗi an toàn sẽ được tự sửa.'
                    : wizardStep === 5
                      ? `Pipeline tạo phim tuần tự: ${pipelineApproved}/${pipelineTotal} phân cảnh đã được duyệt.`
                      : finalApproved ? 'Final Film đã được ghép và Master QC đạt.' : 'Toàn bộ phân cảnh đã đạt. Ghép Final Film và chạy Master QC cuối cùng.'}</p>
                <div className="film-wizard-status">
                  <span>Ảnh chuẩn <b>{canonicalLocked ? 'ĐẠT' : 'CHƯA XONG'}</b></span>
                  <span>Nhất quán <b>{productionReady ? 'ĐẠT' : 'CHƯA XONG'}</b></span>
                  <span>Phân cảnh <b>{pipelineApproved}/{pipelineTotal}</b></span>
                  <span>Final <b>{finalApproved ? 'ĐẠT' : 'CHƯA XONG'}</b></span>
                </div>
              </div>
              <div className="film-wizard-actions">
                <button className="film-wizard-next" disabled={wizardBusy || finalApproved || canonicalGenerating || canonicalQcRunning || (wizardStep === 5 && pipelineState?.worker_active)} onClick={wizardContinue}>
                  {wizardBusy || canonicalGenerating || canonicalQcRunning || (wizardStep === 5 && pipelineState?.worker_active) ? <Loader2 className="spin" size={18} /> : <ArrowRight size={18} />}
                  <span><strong>{finalApproved ? 'Hoàn tất' : 'Tiếp tục'}</strong><em>{wizardActionLabel}</em></span>
                </button>
                {wizardStep === 3 && (
                  <div className="film-wizard-run-controls">
                    <button className="danger" disabled={!canonicalRunState?.active || canonicalStopping} onClick={stopCanonicalAssets}>
                      {canonicalStopping ? <Loader2 className="spin" size={13} /> : <Pause size={13} />} Dừng
                    </button>
                    <button disabled={canonicalGenerating || canonicalFailedCount === 0} onClick={retryFailedCanonicalAssets}>
                      <RotateCcw size={13} /> Thử lại lỗi ({canonicalFailedCount})
                    </button>
                    <button disabled={!active} onClick={async () => { if (active) { await refreshCanonicalRuntime(active.id); await refreshMedia(active.id) } }}>
                      <RotateCcw size={13} /> Làm mới
                    </button>
                    <button disabled={!active} onClick={exportCanonicalDiagnostics}>
                      <FileJson size={13} /> Xuất log chẩn đoán
                    </button>
                    <button disabled={!active} onClick={openCanonicalAssetsFolder}>
                      <FolderOpen size={13} /> Mở thư mục ảnh chuẩn
                    </button>
                  </div>
                )}
              </div>
            </div>
            {wizardStep === 3 && (
              <div className="canonical-live-panel">
                <div className="canonical-live-summary">
                  <div><span>TIẾN TRÌNH ẢNH CHUẨN</span><strong>{canonicalStageVi(canonicalRunState?.run?.status || 'idle')}</strong></div>
                  <span>Đã có ảnh <b>{canonicalCounts?.with_file || canonicalGalleryItems.filter(item => !!item.file_url).length}/{canonicalCounts?.total || activeCanonicalResources.length}</b></span>
                  <span>QC đạt <b>{canonicalCounts?.completed || 0}</b></span>
                  <span>Lỗi <b>{canonicalCounts?.failed || 0}</b></span>
                  <span>Đang chạy <b>{canonicalCounts?.running || 0}</b></span>
                  {canonicalRunState?.run?.current_entity_id && <span>Hiện tại <b>{canonicalRunState.run.current_entity_id}</b></span>}
                </div>
                <div className="canonical-resource-table-wrap">
                  <div className="canonical-resource-table-head">
                    <div><span>TRẠNG THÁI TỪNG RESOURCE</span><strong>{activeCanonicalResources.length} tài nguyên chuẩn</strong></div>
                    <span>Generation · Provider · QC · File · Error</span>
                  </div>
                  <div className="canonical-resource-table">
                    <div className="canonical-resource-row header">
                      <span>Loại / ID</span><span>Trạng thái</span><span>AI / tiến độ</span><span>QC</span><span>File</span><span>Lỗi</span>
                    </div>
                    {activeCanonicalResources.map(resource => {
                      const generationStatus = String(resource.metadata?.generation_status || '')
                      const progressRaw = resource.metadata?.provider_progress
                      const progress = typeof progressRaw === 'number' ? Math.max(0, Math.min(100, Math.round(progressRaw))) : null
                      const qcStatus = String(resource.metadata?.canonical_qc_status || '')
                      const errorCode = String(resource.metadata?.provider_error_code || '')
                      const errorText = uiErrorVi(resource.error || errorCode)
                      const typeLabel = resource.resource_type === 'character' ? 'Nhân vật' : resource.resource_type === 'location' ? 'Bối cảnh' : 'Đạo cụ'
                      return (
                        <div key={resource.id} className={`canonical-resource-row ${resource.status === 'error' || errorText ? 'is-error' : resource.status === 'locked' || resource.status === 'ready' ? 'is-ok' : ''}`}>
                          <span><b>{typeLabel}</b><em>{resource.entity_id}</em></span>
                          <span>{renderStatusVi(resource.status)}</span>
                          <span>{generationStatus ? canonicalStageVi(generationStatus) : 'Chưa chạy'}{progress !== null ? ` · ${progress}%` : ''}</span>
                          <span>{qcStatus ? qcStatusVi(qcStatus) : 'Chưa kiểm tra'}</span>
                          <span>{resource.local_path ? 'Đã lưu' : 'Chưa có'}</span>
                          <span title={errorText}>{errorText || '—'}</span>
                        </div>
                      )
                    })}
                    {!activeCanonicalResources.length && <div className="canonical-resource-empty">Chưa có resource canonical. Đồng bộ hoặc bắt đầu tạo ảnh chuẩn để xem trạng thái từng tài nguyên.</div>}
                  </div>
                </div>
                <div className="canonical-live-grid">
                  <section className="canonical-live-log">
                    <div className="canonical-live-head">
                      <div><span>LOG REALTIME</span><strong>{canonicalEvents.length} sự kiện gần nhất</strong></div>
                      <span>{canonicalRunState?.active ? 'Đang theo dõi' : 'Đã đồng bộ'}</span>
                    </div>
                    <div className="canonical-log-lines">
                      {canonicalEvents.slice(-50).map(event => {
                        const payload = event.payload || {}
                        const message = uiErrorVi(String(payload.message || event.event_type))
                        const entity = String(payload.entity_id || '')
                        const time = event.created_at ? new Date(event.created_at).toLocaleTimeString('vi-VN') : '--:--:--'
                        return (
                          <div key={event.id} className={'canonical-log-line ' + String(event.severity || 'INFO').toLowerCase()}>
                            <time>{time}</time><b>{event.severity}</b><span>{entity || eventTypeVi(event.event_type)}</span><em>{message}</em>
                          </div>
                        )
                      })}
                      {!canonicalEvents.length && <div className="canonical-log-empty">Chưa có log tạo ảnh chuẩn. Khi bắt đầu, từng bước Flow / tải ảnh / QC sẽ xuất hiện tại đây.</div>}
                    </div>
                  </section>
                  <section className="canonical-live-results">
                    <FilmMediaGallery
                      items={canonicalGalleryItems}
                      jobsProcessing={canonicalGenerating || canonicalQcRunning}
                      onRefresh={async () => { if (active) { await refreshCanonicalRuntime(active.id); await refreshMedia(active.id) } }}
                      onSelectVersion={async item => { if (item.id.startsWith('live-')) return; await api.selectFilmMedia(item.id); if (active) await refreshMedia(active.id) }}
                    />
                  </section>
                </div>
              </div>
            )}
          </section>
          <div className={`film-editor-grid ${showAdvanced ? '' : 'film-advanced-hidden'}`}>
            <aside className="film-project-panel">
              <div className="film-panel-title"><BookOpen size={14} /><strong>DỰ ÁN</strong></div>
              <div className="film-project-tabs">
                <button className={projectTab === 'original' ? 'active' : ''} onClick={() => setProjectTab('original')}>Kịch bản gốc</button>
                <button className={projectTab === 'settings' ? 'active' : ''} onClick={() => setProjectTab('settings')}>Cài đặt dự án</button>
                <button className={projectTab === 'story' ? 'active' : ''} onClick={() => setProjectTab('story')}>Nội dung & quy tắc phim</button>
                <button className={projectTab === 'characters' ? 'active' : ''} onClick={() => setProjectTab('characters')}>Dữ liệu nhân vật <span>{active.characters.length}</span></button>
                <button className={projectTab === 'locations' ? 'active' : ''} onClick={() => setProjectTab('locations')}>Dữ liệu bối cảnh <span>{active.locations.length}</span></button>
                <button className={projectTab === 'props' ? 'active' : ''} onClick={() => setProjectTab('props')}>Dữ liệu đạo cụ <span>{active.props.length}</span></button>
                <button className={projectTab === 'master' ? 'active' : ''} onClick={() => setProjectTab('master')}>Chỉ dẫn tổng thể</button>
              </div>
              <div className="film-bible-view">
                {projectTab === 'original' && <p className="film-original-text">{active.original_text}</p>}
                {projectTab === 'settings' && (
                  <div>
                    <div className="film-settings-grid">
                      <label>Dự án Google Flow<select value={settings.flow_project_id || ''} onChange={e => saveActiveSettings({ flow_project_id: e.target.value || null, flow_model: null })}><option value="">Tự dùng dự án Flow mới nhất</option>{flowProjects.map(project => <option key={project.id} value={project.id}>{project.modified_label || 'Dự án Flow'} · {project.id.slice(0, 8)}</option>)}</select></label>
                      <label>Thời lượng phân cảnh<select value={settings.scene_duration} onChange={e => saveActiveSettings({ scene_duration: Number(e.target.value) })}>{(flowCaps?.durations?.length ? flowCaps.durations : [4, 6, 8, 10, 12]).map(value => <option key={value} value={value}>{value} giây</option>)}</select></label>
                      <label>Tỷ lệ<select value={settings.aspect_ratio} onChange={e => saveActiveSettings({ aspect_ratio: e.target.value })}>{(flowCaps?.aspect_ratios?.length ? flowCaps.aspect_ratios : ['16:9', '9:16', '1:1']).map(value => <option key={value}>{value}</option>)}</select></label>
                      <label>Độ phân giải<select value={settings.resolution} onChange={e => saveActiveSettings({ resolution: e.target.value })}>{(flowCaps?.resolutions?.length ? flowCaps.resolutions : ['720p', '1080p', 'Highest']).map(value => <option key={value} value={value}>{value === 'Highest' ? 'Cao nhất' : value}</option>)}</select></label>
                      <label>Phong cách<select value={settings.style} onChange={e => saveActiveSettings({ style: e.target.value })}>{FILM_STYLE_OPTIONS.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
                    </div>
                    <div className="film-lock-row">
                      <button className={visualAssetsRequired ? 'on' : ''} disabled={renderState?.adapter.id === 'flow_bridge'} onClick={() => saveActiveSettings({ require_provider_assets: !settings.require_provider_assets })}><LockKeyhole size={13} /> Bắt buộc tài nguyên tham chiếu{renderState?.adapter.id === 'flow_bridge' ? ' · Flow' : ''}</button>
                    </div>
                    {flowCaps && <p>Khả năng Google Flow: {flowCaps.models.length} mô hình · {flowCaps.resolutions.join('/')} · {flowCaps.durations.join('/')} giây</p>}
                    <pre>{jsonTextVi(active.settings)}</pre>
                  </div>
                )}
                {projectTab === 'story' && <pre>{jsonTextVi(active.story_bible)}</pre>}
                {projectTab === 'characters' && resourcePanel('character', active.characters)}
                {projectTab === 'locations' && resourcePanel('location', active.locations)}
                {projectTab === 'props' && resourcePanel('prop', active.props)}
                {projectTab === 'master' && <p>{active.master_prompt || 'Chưa có chỉ dẫn tổng thể.'}</p>}
              </div>
              <div className="film-lock-summary"><span><LockKeyhole size={12} /> Nhân vật {active.settings.character_lock ? 'ĐÃ KHÓA' : 'TẮT'}</span><span><MapPin size={12} /> Bối cảnh {active.settings.location_lock ? 'ĐÃ KHÓA' : 'TẮT'}</span><span><WandSparkles size={12} /> Nối cảnh tự động {active.settings.auto_continuity ? 'BẬT' : 'TẮT'}</span><span><LockKeyhole size={12} /> Tài nguyên tham chiếu {visualAssetsRequired ? 'BẮT BUỘC' : 'KHÔNG BẮT BUỘC'}</span></div>
            </aside>

            <section className="film-storyboard-panel">
              <div className="film-panel-title"><Clapperboard size={14} /><strong>BẢNG PHÂN CẢNH</strong><span>{active.scenes.length} phân cảnh</span></div>
              <div className="film-scene-list">
                {active.scenes.map(scene => {
                  const renderJob = latestRenderByScene.get(scene.id)
                  const selected = selectedForRender.includes(scene.id)
                  return (
                    <button key={scene.id} className={selectedScene?.id === scene.id ? 'active' : ''} onClick={() => setActiveSceneId(scene.id)}>
                      <span className={`film-scene-select ${selected ? 'selected' : ''}`} onClick={e => { e.stopPropagation(); toggleRenderSelection(scene.id) }}>{selected ? '✓' : ''}</span>
                      <div className="film-scene-index">{String(scene.scene_index).padStart(2, '0')}</div>
                      <div><strong>{scene.title || scene.id}</strong><p>{scene.summary || scene.action || 'Chưa có mô tả'}</p><span>{scene.duration} giây · {scene.location_id || 'Chưa xác định bối cảnh'} · {scene.characters.join(', ') || 'Chưa xác định nhân vật'}</span></div>
                      <div className="film-scene-flags">
                        {!!scene.warnings.length && <AlertTriangle size={13} />}
                        {renderJob && <em className={`render-mini ${renderJob.status}`}>{renderStatusVi(renderJob.status)}</em>}
                      </div>
                    </button>
                  )
                })}
              </div>
            </section>

            <section className="film-scene-editor">
              {selectedScene ? (<>
                <div className="film-scene-editor-head"><div><span>{selectedScene.id}</span><h3>{selectedScene.title}</h3></div><button onClick={saveScene}><Save size={14} /> Lưu phân cảnh</button></div>
                {!!selectedScene.warnings.length && <div className="continuity-warning"><AlertTriangle size={14} /><div><strong>CẢNH BÁO TÍNH NHẤT QUÁN</strong>{selectedScene.warnings.map((w, i) => <p key={i}>{w}</p>)}</div></div>}
                <div className="film-editor-fields">
                  <label>Tên phân cảnh<input value={field('title')} onChange={e => setField('title', e.target.value)} /></label>
                  <label>Nội dung gốc {selectedScene.source_hash && <small>· Đã khóa theo kịch bản gốc</small>}<textarea readOnly={!!selectedScene.source_hash} value={field('source_text')} onChange={e => setField('source_text', e.target.value)} /></label>
                  <label>Tóm tắt phân cảnh<textarea value={field('summary')} onChange={e => setField('summary', e.target.value)} /></label>
                  <div className="film-two-cols"><label>Nhân vật trong cảnh<input disabled={!!selectedScene.source_hash} value={(draft.characters as string[] | undefined)?.join(', ') ?? selectedScene.characters.join(', ')} onChange={e => setField('characters', e.target.value.split(',').map(x => x.trim()).filter(Boolean))} /></label><label>Bối cảnh<input disabled={!!selectedScene.source_hash} value={field('location_id')} onChange={e => setField('location_id', e.target.value)} /></label></div>
                  <label>Hành động {selectedScene.source_hash && <small>· Đã khóa</small>}<textarea readOnly={!!selectedScene.source_hash} value={field('action')} onChange={e => setField('action', e.target.value)} /></label>
                  <div className="film-two-cols"><label>Góc máy / Chuyển động máy<textarea value={field('camera')} onChange={e => setField('camera', e.target.value)} /></label><label>Thời lượng<input disabled={!!selectedScene.source_hash} type="number" min={1} max={60} value={Number(draft.duration ?? selectedScene.duration)} onChange={e => setField('duration', Number(e.target.value))} /></label></div>
                  <div className="film-two-cols"><label>Ánh sáng<textarea value={field('lighting')} onChange={e => setField('lighting', e.target.value)} /></label><label>Bầu không khí<textarea value={field('atmosphere')} onChange={e => setField('atmosphere', e.target.value)} /></label></div>
                  <label>Lời thuyết minh {selectedScene.source_hash && <small>· Đã khóa</small>}<textarea readOnly={!!selectedScene.source_hash} value={field('voiceover')} onChange={e => setField('voiceover', e.target.value)} /></label>
                  <label>Lời thoại nhân vật {selectedScene.source_hash && <small>· Đã khóa theo kịch bản gốc</small>}<textarea readOnly={!!selectedScene.source_hash} className="film-code-area" value={selectedScene.source_hash ? jsonTextVi(selectedScene.dialogue) : dialogueText} onChange={e => setDialogueText(e.target.value)} /></label>
                  <label>Trạng thái đầu cảnh<textarea value={field('start_state')} onChange={e => setField('start_state', e.target.value)} /></label>
                  <label>Trạng thái cuối cảnh<textarea value={field('end_state')} onChange={e => setField('end_state', e.target.value)} /></label>
                  <label>Mô tả hình ảnh <small>· Nội dung kỹ thuật cho AI</small><textarea className="film-prompt-area" value={field('visual_prompt')} onChange={e => setField('visual_prompt', e.target.value)} /></label>
                  <label>Lệnh tạo video Google Flow <small>· Giữ nguyên nội dung kỹ thuật để gửi Google Flow</small><textarea className="film-prompt-area" value={field('flow_prompt')} onChange={e => setField('flow_prompt', e.target.value)} /></label>
                </div>
                <FilmSceneMedia sceneId={selectedScene.id} items={galleryItems} onSelectVersion={async item => { if (item.id.startsWith('live-')) return; await api.selectFilmMedia(item.id); if (active) await refreshMedia(active.id) }} />
                <div className="film-scene-generate"><button disabled={renderBusy || !renderGateReady} onClick={() => queueRender([selectedScene.id])}>{renderBusy ? <Loader2 className="spin" size={14} /> : <Play size={14} />} Tạo video phân cảnh</button><span>Kiểm tra trước khi tạo: {productionReady ? 'ĐẠT' : 'ĐANG CHẶN'} · Bộ máy tạo video: {renderState?.adapter.name ? adapterNameVi(renderState.adapter.name) : 'đang kiểm tra'} · {renderState?.adapter.configured ? 'SẴN SÀNG' : 'CHƯA CẤU HÌNH'}</span>{selectedScene.result_url && <a href={selectedScene.result_url} target="_blank" rel="noreferrer">Mở video kết quả</a>}</div>
              </>) : <div className="film-empty-editor">Chọn một phân cảnh để chỉnh sửa.</div>}
            </section>
          </div>
          <div className={`film-render-panel ${showAdvanced ? '' : 'film-advanced-hidden'}`}>
            <div className="film-render-head">
              <div><span>HỆ THỐNG TẠO VIDEO</span><strong>{renderState?.adapter.name ? adapterNameVi(renderState.adapter.name) : 'Đang kiểm tra bộ máy tạo video'}</strong><em>{renderState?.adapter.configured ? 'Bộ máy tạo video đã sẵn sàng' : 'Chưa cấu hình bộ máy tạo video'} · Kiểm tra chất lượng: {renderState?.qc_adapter.name ? adapterNameVi(renderState.qc_adapter.name) : 'đang kiểm tra'}{renderState?.resources ? ` · Canonical QC: ${renderState.resources.qc_passed || 0}/${renderState.resources.total} đạt · Khóa: ${renderState.resources.locked || 0}/${renderState.resources.total} · ${renderState.resources.qc_required || 0} cần QC` : ''}</em></div>
              <div className="film-render-controls">
                <button disabled={!canonicalProviderReady || canonicalGenerating || canonicalQcRunning || !activeCanonicalResources.some(item => !!item.local_path)} onClick={() => qcCanonicalAssets(undefined, undefined, true)}>{canonicalQcRunning ? <Loader2 className="spin" size={13} /> : <CheckCircle2 size={13} />} {canonicalQcRunning ? 'Vision QC đang chạy...' : canonicalReady ? 'Canonical QC đã đạt' : 'QC + tự sửa toàn bộ ảnh'}</button>
                <button disabled={!canonicalProviderReady || canonicalGenerating || canonicalQcRunning || canonicalReady} onClick={() => generateCanonicalAssets()}>{canonicalGenerating ? <Loader2 className="spin" size={13} /> : <Sparkles size={13} />} {canonicalGenerating ? 'AI đang tạo ảnh...' : canonicalReady ? 'Ảnh chuẩn đã sẵn sàng' : canonicalProvider === 'flow' && flowAuthenticated !== true ? 'Đăng nhập Flow để tạo ảnh' : 'AI tạo toàn bộ ảnh chuẩn'}</button>
                {renderState?.queue.paused ? <button onClick={resumeRender}><Play size={13} /> Tiếp tục</button> : <button onClick={pauseRender}><Pause size={13} /> Tạm dừng</button>}
                <button onClick={() => refreshRender(active.id)}><RotateCcw size={13} /> Làm mới</button>
              </div>
            </div>
            <div className="film-production-config">
              <div className="production-flow-state">
                <span>GOOGLE FLOW</span>
                <strong>{flowAuthenticated === true ? 'Đã đăng nhập' : flowAuthenticated === false ? 'Chưa vào workspace Flow' : 'Đang kết nối phiên...'}</strong>
                <em>{flowAuthenticated === true
                  ? (settings.flow_project_id ? `Dự án ${settings.flow_project_id.slice(0, 8)}` : 'Tự động: dự án Flow mới nhất')
                  : (flowSessionHint || 'Email trên thanh trên chỉ là phiên đã lưu. Cần đăng nhập xong trong Chrome/Edge mới chọn được mô hình.')}</em>
              </div>
              <label>
                Nguồn tạo ảnh chuẩn
                <select
                  value={canonicalProvider}
                  disabled={canonicalGenerating}
                  onChange={e => {
                    const next = e.target.value as 'flow' | 'xkiro'
                    setCanonicalProvider(next)
                    if (next === 'flow') {
                      const models = flowImageCaps?.models || []
                      setCanonicalModel(models.includes('Nano Banana 2') ? 'Nano Banana 2' : (models[0] || 'Nano Banana 2'))
                    } else {
                      setCanonicalModel('sensenova/sensenova-u1.5-lite')
                    }
                  }}
                >
                  <option value="flow">Google Flow</option>
                  <option value="xkiro">xKiro</option>
                </select>
              </label>
              <label>
                Mô hình tạo ảnh chuẩn
                <select
                  value={canonicalModel}
                  disabled={canonicalGenerating || (canonicalProvider === 'flow' && flowAuthenticated !== true)}
                  onChange={e => setCanonicalModel(e.target.value)}
                >
                  {canonicalProvider === 'flow'
                    ? (flowImageCaps?.models?.length
                        ? flowImageCaps.models.map(value => <option key={value}>{value}</option>)
                        : <option>Nano Banana 2</option>)
                    : <>
                        <option value="sensenova/sensenova-u1.5-lite">SenseNova U1.5 Lite · Free</option>
                        <option value="gpt-image">GPT Image · Paid</option>
                      </>}
                </select>
              </label>
              <label>
                Mô hình tạo video
                <select
                  value={settings.flow_model || ''}
                  disabled={flowAuthenticated !== true || flowLoading || !flowCaps?.models?.length}
                  onChange={e => saveActiveSettings({ flow_model: e.target.value || null })}
                >
                  <option value="">
                    {flowAuthenticated !== true
                      ? (flowLoading ? 'Đang mở trình duyệt Flow...' : 'Chưa kết nối được phiên Flow')
                      : flowLoading
                        ? 'Đang đọc mô hình từ Flow...'
                        : 'Giữ mô hình hiện tại của Flow'}
                  </option>
                  {(flowCaps?.models || []).map(value => <option key={value}>{value}</option>)}
                </select>
              </label>
              <div className="production-flow-meta">
                {flowCaps
                  ? <span>{flowCaps.models.length} mô hình · {flowCaps.resolutions.join('/')} · {flowCaps.durations.join('/')} giây</span>
                  : <span>Chưa tải thông tin khả năng từ Flow</span>}
                {flowAuthenticated !== true && (
                  <button disabled={flowLoading} onClick={async () => {
                    await api.openFlowLogin()
                    await refreshFlowProduction(settings.flow_project_id)
                  }}>
                    Mở đăng nhập
                  </button>
                )}
                <button disabled={flowLoading} onClick={() => refreshFlowProduction(settings.flow_project_id)}>
                  <RotateCcw size={12} className={flowLoading ? 'spin' : ''} /> Làm mới danh sách mô hình
                </button>
              </div>
            </div>
            <div className={`production-gate-card ${consistencyReady ? 'pass' : 'fail'}`}>
              <div className="production-gate-summary">
                {consistencyReady ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
                <div>
                  <span>KIỂM TRA TÍNH NHẤT QUÁN RULE + AI · {active.consistency_report?.version || 'chưa kiểm tra'}</span>
                  <strong>
                    {consistencyReady
                      ? `${active.consistency_report?.summary?.passed_scenes || active.scenes.length}/${active.consistency_report?.summary?.total_scenes || active.scenes.length} PHÂN CẢNH ĐẠT TÍNH NHẤT QUÁN`
                      : consistencyStatus === 'REPAIRABLE'
                        ? 'PHÁT HIỆN LỖI CÓ THỂ TỰ ĐỘNG SỬA'
                        : consistencyStatus === 'REVIEW_REQUIRED'
                          ? 'CẦN KIỂM TRA THÊM'
                          : 'CHƯA ĐẠT TÍNH NHẤT QUÁN'}
                  </strong>
                  <em>
                    Rule Engine: {active.consistency_report?.deterministic?.final_gate ? 'ĐẠT' : 'CHƯA ĐẠT'}
                    {' · '}AI: {active.consistency_report?.semantic_review?.available
                      ? `${active.consistency_report.semantic_review.verdict || 'Đang đánh giá'}${active.consistency_report.semantic_review.fallback_used ? ' · đã dùng model dự phòng' : ''}`
                      : active.consistency_report?.semantic_review?.status_code === 429
                        ? 'TẠM GIỚI HẠN TỐC ĐỘ'
                        : 'CHƯA KHẢ DỤNG'}
                    {active.consistency_report?.semantic_review?.model
                      ? ` · Model kiểm tra: ${active.consistency_report.semantic_review.model}`
                      : ''}
                    {active.consistency_report?.semantic_review?.retry_after_seconds
                      ? ` · Thử lại sau khoảng ${active.consistency_report.semantic_review.retry_after_seconds} giây`
                      : ''}
                    {' · '}{active.consistency_report?.summary?.passed_scenes || 0}/{active.consistency_report?.summary?.total_scenes || active.scenes.length} cảnh đạt
                    {' · '}Evidence bác {active.consistency_report?.rejected_ai_findings?.length || 0} finding sai
                  </em>
                  {!consistencyReady && consistencyIssues[0] && (
                    <p className="gate-issue">{consistencyIssues[0].scene || consistencyIssues[0].scene_id || 'DỰ ÁN'} · {consistencyIssues[0].detail || consistencyIssues[0].evidence || 'Cần kiểm tra thêm.'}</p>
                  )}
                  {consistencyReady && (active.consistency_report?.rejected_ai_findings?.length || 0) > 0 && (
                    <p className="gate-note">AI nêu {active.consistency_report?.rejected_ai_findings?.length} nhận xét, nhưng Evidence Verifier đã bác vì không khớp sổ cái đạo cụ. Kết luận cuối: ĐẠT. Không có mục nào cần tự động sửa.</p>
                  )}
                  {!consistencyReady && active.consistency_report?.semantic_review?.summary && (
                    <p className="gate-issue">{active.consistency_report.semantic_review.summary}</p>
                  )}
                  {!!active.consistency_report?.semantic_review?.tried_models?.length && (
                    <p className="gate-meta">Model AI đã thử: {active.consistency_report.semantic_review.tried_models.join(' → ')}</p>
                  )}
                </div>
              </div>
              <div className="production-gate-actions">
                <button disabled={consistencyBusy} onClick={checkContinuity}>
                  {consistencyBusy ? <Loader2 className="spin" size={12} /> : <CheckCircle2 size={12} />} Kiểm tra bằng Rule + AI
                </button>
                <button
                  disabled={consistencyBusy || !canAutoRepairConsistency}
                  title={canAutoRepairConsistency
                    ? 'Sửa ' + (consistencyRepairableCount || 'các') + ' lỗi nhất quán có thể tự động xử lý'
                    : consistencyReady
                      ? 'Đã đạt. Không có lỗi để tự sửa.'
                      : 'Chưa có lỗi nào được Evidence Verifier xác nhận là có thể tự động sửa.'}
                  onClick={repairConsistency}
                >
                  <WandSparkles size={12} /> Tự động sửa lỗi nhất quán
                </button>
              </div>
            </div>
            <div className={`production-gate-card ${productionGateReady ? 'pass' : 'fail'}`}>
              <div className="production-gate-summary">
                {productionGateReady ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
                <div>
                  <span>KIỂM TRA TRƯỚC KHI TẠO VIDEO · {active.production_gate?.version || 'chưa kiểm tra'}</span>
                  <strong>{productionGateReady ? 'ĐỦ ĐIỀU KIỆN TẠO VIDEO' : 'CẦN SỬA TRƯỚC KHI TẠO VIDEO'}</strong>
                  <em>
                    {active.production_gate?.gates
                      ? `${Object.values(active.production_gate.gates).filter(Boolean).length}/${Object.keys(active.production_gate.gates).length} hạng mục đạt`
                      : 'Chưa có báo cáo kiểm tra trước khi tạo video'}
                    {' · '}{active.production_gate?.error_count || 0} lỗi
                  </em>
                  {!productionGateReady && productionErrors[0] && (
                    <p>{productionErrors[0].scene ? `${productionErrors[0].scene} · ` : ''}{errorCodeVi(productionErrors[0].code, productionErrors[0].detail)}</p>
                  )}
                </div>
              </div>
              <div className="production-gate-actions">
                <button disabled={productionBusy} onClick={runProductionGate}>
                  {productionBusy ? <Loader2 className="spin" size={12} /> : <CheckCircle2 size={12} />} Kiểm tra toàn bộ trước khi tạo
                </button>
                <button disabled={productionBusy} onClick={autoRepairDerived}>
                  <WandSparkles size={12} /> Tự động sửa dữ liệu phát sinh
                </button>
              </div>
            </div>
            {!renderState?.adapter.configured && <div className="render-adapter-note"><AlertTriangle size={14} /><div><strong>Hàng đợi đã hoạt động nhưng chưa có dịch vụ tạo video thật.</strong><p>Hãy cấu hình dịch vụ tạo video trong phần cài đặt hệ thống. Hệ thống chỉ đánh dấu hoàn tất khi thực sự nhận được video kết quả.</p></div></div>}
            <FilmContinuityControlCenter projectId={active.id} projectName={active.name} scenes={active.scenes} media={galleryItems} onRefresh={async () => { if (active) await refreshMedia(active.id) }} />
            <FilmMediaGallery items={galleryItems} jobsProcessing={canonicalGenerating || canonicalQcRunning || (renderState?.jobs || []).some(job => ['waiting', 'preparing', 'generating'].includes(job.status))} onRefresh={async () => { if (active) await refreshMedia(active.id) }} onSelectVersion={async item => { if (item.id.startsWith('live-')) return; await api.selectFilmMedia(item.id); if (active) await refreshMedia(active.id) }} />
            <div className="film-render-jobs">
              {(renderState?.jobs || []).map(job => (
                <div className={`film-render-job ${job.status}`} key={job.id}>
                  <div className="render-job-main">
                    <div className="render-job-scene"><strong>{job.scene_id}</strong><span>Lần thử {job.attempt + 1}</span></div>
                    <div className="render-job-progress"><div><i style={{ width: `${job.progress}%` }} /></div><span>{job.progress}%</span></div>
                    <b>{renderStatusVi(job.status)}</b>
                    {job.status === 'completed' ? <CheckCircle2 size={15} /> : ['preparing','generating'].includes(job.status) ? <Loader2 className="spin" size={15} /> : job.status === 'failed' ? <AlertTriangle size={15} /> : <span />}
                  </div>
                  <div className="render-job-meta">
                    {job.reference?.previous_scene_id ? <span>Nối tiếp từ {String(job.reference.previous_scene_id)}</span> : <span>Cảnh mở đầu</span>}
                    {job.reference?.previous_last_frame_url ? <span>Khung hình cuối cảnh trước: Có</span> : <span>Khung hình cuối cảnh trước: Chưa có</span>}
                    <span>Kiểm tra chất lượng: {qcStatusVi(job.qc_status)}{job.consistency_score != null ? ` · ${job.consistency_score}/100` : ''}</span>
                    {visualQcSummary(job) && <span title={visualQcSummary(job)}>{visualQcSummary(job)}</span>}
                    {audioQcSummary(job) && <span>{audioQcSummary(job)}</span>}
                    {speechQcSummary(job) && <span title={speechQcSummary(job)}>{speechQcSummary(job)}</span>}
                  </div>
                  {job.error && <p className="render-job-error">{job.provider_error_code ? errorCodeVi(job.provider_error_code, job.error) : uiErrorVi(job.error)}</p>}
                  <div className="render-job-actions">
                    {job.result_url && <a href={job.result_url} target="_blank" rel="noreferrer">Mở video</a>}
                    {(job.status === 'failed' || job.qc_status === 'failed') && <button onClick={() => retryRender(job.id)}><RotateCcw size={12} /> {job.qc_status === 'failed' ? 'Tạo lại phân cảnh' : 'Thử lại phân cảnh'}</button>}
                  </div>
                </div>
              ))}
              {!renderState?.jobs.length && <div className="render-empty">Chưa có tác vụ tạo video. Chọn phân cảnh hoặc dùng “Tạo toàn bộ phân cảnh”.</div>}
            </div>
          </div>
          <div className={`film-bottom-actions ${showAdvanced ? '' : 'film-advanced-hidden'}`}>
            <button onClick={async () => setActive(await api.analyzeFilmProject(active.id))}><Sparkles size={14} /> PHÂN TÍCH KỊCH BẢN</button>
            <button disabled={consistencyBusy} onClick={checkContinuity}>{consistencyBusy ? <Loader2 className="spin" size={14} /> : <AlertTriangle size={14} />} {consistencyBusy ? 'ĐANG KIỂM TRA RULE + AI' : 'KIỂM TRA TÍNH NHẤT QUÁN'}</button>
            <button disabled={consistencyBusy || !canAutoRepairConsistency} onClick={repairConsistency}><WandSparkles size={14} /> TỰ ĐỘNG SỬA LỖI NHẤT QUÁN</button>
            <button disabled={renderBusy || !renderGateReady || (!selectedForRender.length && !activeSceneId)} onClick={() => queueRender(selectedForRender.length ? selectedForRender : (activeSceneId ? [activeSceneId] : []))}><Play size={14} /> TẠO CÁC CẢNH ĐÃ CHỌN</button>
            <button disabled={renderBusy || !renderGateReady} onClick={() => queueRender(active.scenes.map(scene => scene.id))}><Clapperboard size={14} /> TẠO TOÀN BỘ PHÂN CẢNH</button>
            <button disabled={renderBusy || productionBusy || consistencyBusy} className="primary" onClick={autoPipeline}><WandSparkles size={14} /> TẠO PHIM TỰ ĐỘNG</button>
            <button onClick={exportFlowPrompts}><Download size={14} /> XUẤT LỆNH GOOGLE FLOW</button><button onClick={exportProject}><Download size={14} /> XUẤT DỰ ÁN</button>
          </div>
        </div>
      )}
    </section>
  )
}
