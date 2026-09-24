import type {
  Chat,
  DesktopReadyStatus,
  FilmConsistencyReport,
  FilmCanonicalGenerationStatus,
  FilmCanonicalRun,
  FilmGeneratedMedia,
  FilmMediaList,
  FilmMediaVersions,
  FilmPipelineCandidate,
  FilmAudioRequirements,
  FilmJunction,
  FilmPipelineGate,
  FilmVoiceProfile,
  FilmSpeakerAcceptance,
  FilmSpeakerCalibrationReport,
  FilmSpeakerStatus,
  FilmNarratorUpgradePreview,
  FilmNarratorUpgradeResult,
  FilmAcceptanceSnapshot,
  FilmCapabilityMatrix,
  FilmFinalStatus,
  FilmPipelineEvent,
  FilmPipelineMetrics,
  FilmPipelineStatus,
  FilmRecoveryStatus,
  FilmProductionGate,
  FilmProject,
  FilmProjectSummary,
  FilmProviderResource,
  FilmRenderJob,
  FilmRenderStatus,
  FilmScene,
  FilmSceneLedger,
  FilmScenePipelineState,
  FilmSettings,
  FlowImageCapabilities,
  FlowMetrics,
  FlowProject,
  FlowSessionList,
  FlowStatus,
  FlowVideoCapabilities,
  Provider,
  VideoJob,
  VideoProxyStatus,
  VideoProxyTest,
} from './types'
import { getRuntimeConfig, resolveApiUrl } from './runtime'

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const runtime = getRuntimeConfig()
  const response = await fetch(resolveApiUrl(url), {
    headers: {
      'Content-Type': 'application/json',
      ...(runtime.authToken ? { 'X-TH-Media-Token': runtime.authToken } : {}),
      ...(options?.headers || {}),
    },
    ...options,
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(payload.detail || 'Yêu cầu thất bại')
  }
  return response.json()
}

async function streamCanonicalEvents(
  projectId: string,
  onEvent: (event: FilmPipelineEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const runtime = getRuntimeConfig()
  const response = await fetch(resolveApiUrl(`/api/film/projects/${projectId}/resources/generation/events`), {
    headers: {
      Accept: 'text/event-stream',
      ...(runtime.authToken ? { 'X-TH-Media-Token': runtime.authToken } : {}),
    },
    signal,
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(payload.detail || 'Không mở được log realtime ảnh chuẩn')
  }
  if (!response.body) throw new Error('Trình duyệt không hỗ trợ stream log realtime')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let boundary = buffer.indexOf('\n\n')
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary).replace(/\r/g, '')
      buffer = buffer.slice(boundary + 2)
      const data = block.split('\n')
        .filter(line => line.startsWith('data:'))
        .map(line => line.slice(5).trimStart())
        .join('\n')
      if (data && data !== '{}') {
        try { onEvent(JSON.parse(data) as FilmPipelineEvent) } catch { /* ignore malformed event */ }
      }
      boundary = buffer.indexOf('\n\n')
    }
  }
}

export const api = {
  ready: () => request<DesktopReadyStatus>('/api/ready'),
  desktopDiagnostics: () => request<Record<string, unknown>>('/api/desktop/diagnostics'),
  exportDesktopDiagnostics: () => request<{ filename: string; size_bytes: number }>('/api/desktop/diagnostics/export', { method: 'POST' }),
  desktopNetwork: () => request<{ ok: boolean; online: boolean; forced?: boolean; checked_at: string; latency_ms: number; probe?: string | null; message: string }>('/api/desktop/network'),
  desktopResources: () => request<{ ok: boolean; can_render: boolean; warnings: string[]; blockers: string[]; cpu: { logical_cores: number; physical_cores: number; usage_percent: number }; memory: { total_gb: number; available_gb: number; used_percent: number }; storage: { media_dir: string; total_gb: number; free_gb: number; used_percent: number }; temp: { dir: string; size_gb: number; quota_gb: number }; gpus: Array<{ name: string; memory_total_mb: number; memory_free_mb: number; utilization_percent: number }> }>('/api/desktop/resources'),
  cleanupDesktopTemp: () => request<{ ok: boolean; quota_gb: number; before_gb: number; after_gb: number; deleted_files: number; deleted_gb: number; temp_dir: string }>('/api/desktop/resources/cleanup-temp', { method: 'POST' }),
  desktopBackups: () => request<{ ok: boolean; backups: Array<{ name: string; kind: string; created_at?: string | null; database: boolean; settings: boolean }> }>('/api/desktop/backups'),
  createDesktopBackup: () => request<{ prepared: boolean; backup_name?: string; backup_dir?: string; database_backup?: string | null; active_pipelines?: number; reason?: string }>('/api/desktop/backups', { method: 'POST' }),
  stageDesktopRestore: (backupName: string) => request<{ staged: boolean; backup_name?: string; marker?: string; active_pipelines?: number; reason?: string }>(`/api/desktop/backups/${encodeURIComponent(backupName)}/restore-stage`, { method: 'POST' }),
  desktopUpdateStatus: () => request<{ ok: boolean; can_update: boolean; active_pipelines: number }>('/api/desktop/update/status'),
  prepareDesktopUpdate: () => request<{ prepared: boolean; backup_dir: string; database_backup?: string | null }>('/api/desktop/update/prepare', { method: 'POST' }),
  flowStatus: () => request<FlowStatus>('/api/flow'),
  flowMetrics: () => request<FlowMetrics>('/api/flow/metrics'),
  saveFlow: (bridge_url: string, api_key: string, enabled = true) => request<FlowStatus>('/api/flow', {
    method: 'PUT', body: JSON.stringify({ bridge_url, api_key, enabled }),
  }),
  testFlow: () => request<{ ok: boolean; authenticated: boolean; bridge: Record<string, unknown>; session: Record<string, unknown> }>('/api/flow/test', { method: 'POST' }),
  openFlowLogin: () => request<{ ok: boolean; profile_dir: string; cdp_url: string; message: string }>('/api/flow/open-login', { method: 'POST' }),
  flowSessions: () => request<FlowSessionList>('/api/flow/sessions'),
  saveFlowSession: (name?: string) => request<FlowSessionList>('/api/flow/sessions/save', {
    method: 'POST', body: JSON.stringify({ name: name || null }),
  }),
  newFlowSession: (save_current = true, name?: string) => request<FlowSessionList>('/api/flow/sessions/new', {
    method: 'POST', body: JSON.stringify({ save_current, name: name || null }),
  }),
  restoreFlowSession: (id: string) => request<FlowSessionList>(`/api/flow/sessions/${id}/restore`, { method: 'POST' }),
  deleteFlowSession: (id: string) => request<FlowSessionList>(`/api/flow/sessions/${id}`, { method: 'DELETE' }),
  removeFlow: () => request<{ ok: boolean }>('/api/flow', { method: 'DELETE' }),
  flowProjects: () => request<{ projects: FlowProject[]; count: number }>('/api/flow/projects'),
  flowVideoCapabilities: (projectId?: string) => request<FlowVideoCapabilities>(`/api/flow/capabilities/video${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`),
  flowImageCapabilities: (projectId?: string) => request<FlowImageCapabilities>(`/api/flow/capabilities/image${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`),
  providers: () => request<Provider[]>('/api/providers'),
  models: (provider: string) => request<{ models: string[]; configured: boolean }>(`/api/providers/${provider}/models`),
  saveProvider: (provider: string, api_key: string, base_url?: string) => request<{ ok: boolean; verified?: boolean }>(`/api/providers/${provider}`, {
    method: 'PUT', body: JSON.stringify({ api_key, base_url: base_url || null }),
  }),
  testProvider: (provider: string) => request<{ ok: boolean; verified: boolean; provider: string }>(`/api/providers/${provider}/test`, { method: 'POST' }),
  removeProvider: (provider: string) => request(`/api/providers/${provider}`, { method: 'DELETE' }),
  chats: () => request<Chat[]>('/api/chats'),
  chat: (id: string) => request<Chat>(`/api/chats/${id}`),
  createChat: (provider: string, model: string) => request<Chat>('/api/chats', {
    method: 'POST', body: JSON.stringify({ provider, model }),
  }),
  updateChat: (id: string, values: Partial<Pick<Chat, 'provider' | 'model' | 'title'>>) => request<Chat>(`/api/chats/${id}`, {
    method: 'PATCH', body: JSON.stringify(values),
  }),
  send: (id: string, content: string) => request<Chat>(`/api/chats/${id}/messages`, {
    method: 'POST', body: JSON.stringify({ content }),
  }),
  deleteChat: (id: string) => request(`/api/chats/${id}`, { method: 'DELETE' }),
  filmProjects: () => request<FilmProjectSummary[]>('/api/film/projects'),
  filmProject: (id: string) => request<FilmProject>(`/api/film/projects/${id}`),
  createFilmProject: (name: string, original_text: string, provider: string, model: string, settings: FilmSettings) => request<FilmProject>('/api/film/projects', {
    method: 'POST', body: JSON.stringify({ name: name || null, original_text, provider, model, settings }),
  }),
  analyzeFilmProject: (id: string) => request<FilmProject>(`/api/film/projects/${id}/analyze`, { method: 'POST' }),
  checkFilmContinuity: (id: string) => request<FilmProject>(`/api/film/projects/${id}/continuity-check`, { method: 'POST' }),
  filmConsistency: (id: string) => request<FilmConsistencyReport>(`/api/film/projects/${id}/consistency`),
  repairFilmConsistency: (id: string) => request<{ project: FilmProject; consistency_report: FilmConsistencyReport; repair_log: Record<string, unknown> }>(`/api/film/projects/${id}/consistency/repair`, { method: 'POST' }),
  filmProductionGate: (id: string) => request<FilmProductionGate>(`/api/film/projects/${id}/production-gate`),
  runFilmProductionGate: (id: string) => request<{ production_gate: FilmProductionGate; project: FilmProject }>(`/api/film/projects/${id}/production-gate`, { method: 'POST' }),
  autoRepairFilm: (id: string) => request<FilmProject>(`/api/film/projects/${id}/auto-repair`, { method: 'POST' }),
  updateFilmProject: (id: string, values: { name?: string; settings?: FilmSettings }) => request<FilmProject>(`/api/film/projects/${id}`, {
    method: 'PATCH', body: JSON.stringify(values),
  }),
  updateFilmScene: (projectId: string, sceneId: string, values: Partial<FilmScene>) => request<FilmProject>(`/api/film/projects/${projectId}/scenes/${sceneId}`, {
    method: 'PATCH', body: JSON.stringify(values),
  }),
  deleteFilmProject: (id: string, deleteMedia = false) => request<{ ok: boolean; delete_media: boolean; media?: { removed_files: number; removed_dirs: number } | null }>(`/api/film/projects/${id}?delete_media=${deleteMedia ? 'true' : 'false'}`, { method: 'DELETE' }),
  filmRenderStatus: (id: string) => request<FilmRenderStatus>(`/api/film/projects/${id}/render`),
  filmResources: (id: string) => request<{ provider: string; resources: FilmProviderResource[] }>(`/api/film/projects/${id}/resources`),
  syncFilmResources: (id: string) => request<{ provider: string; resources: FilmProviderResource[] }>(`/api/film/projects/${id}/resources/sync`, { method: 'POST' }),
  generateFilmResources: (id: string, resource_type?: string, entity_ids?: string[], provider?: 'xkiro' | 'flow', model?: string) => request<{ accepted: number; provider: string; model: string; run?: FilmCanonicalRun | null; resources: FilmProviderResource[] }>(`/api/film/projects/${id}/resources/generate`, {
    method: 'POST', body: JSON.stringify({ resource_type: resource_type || null, entity_ids: entity_ids?.length ? entity_ids : null, provider: provider || null, model: model || null }),
  }),
  canonicalGenerationStatus: (id: string) => request<FilmCanonicalGenerationStatus>(`/api/film/projects/${id}/resources/generation/status`),
  streamCanonicalGenerationEvents: (id: string, onEvent: (event: FilmPipelineEvent) => void, signal?: AbortSignal) => streamCanonicalEvents(id, onEvent, signal),
  stopCanonicalGeneration: (id: string) => request<{ requested: boolean; reason?: string; run?: FilmCanonicalRun | null; status: FilmCanonicalGenerationStatus }>(`/api/film/projects/${id}/resources/generation/stop`, { method: 'POST' }),
  qcFilmResources: (id: string, resource_type?: string, entity_ids?: string[], auto_repair = false, repair_provider: 'xkiro' | 'flow' = 'flow', repair_model?: string) => request<{ accepted: boolean; auto_repair: boolean; repair_provider: string; repair_model: string; resources: FilmProviderResource[] }>(`/api/film/projects/${id}/resources/qc`, {
    method: 'POST', body: JSON.stringify({ resource_type: resource_type || null, entity_ids: entity_ids?.length ? entity_ids : null, auto_repair, repair_provider, repair_model: repair_model || null }),
  }),
  uploadFilmResourceAsset: (id: string, resourceType: string, entityId: string, data_url: string, filename?: string) => request<FilmProviderResource>(`/api/film/projects/${id}/resources/${resourceType}/${entityId}/asset`, {
    method: 'PUT', body: JSON.stringify({ data_url, filename: filename || null }),
  }),
  lockFilmResources: (id: string) => request<{ provider: string; locked: number; missing: FilmProviderResource[]; resources: FilmProviderResource[] }>(`/api/film/projects/${id}/resources/lock`, { method: 'POST' }),
  queueFilmRender: (id: string, scene_ids?: string[]) => request<FilmRenderStatus>(`/api/film/projects/${id}/render/queue`, {
    method: 'POST', body: JSON.stringify({ scene_ids: scene_ids?.length ? scene_ids : null }),
  }),
  pauseFilmRender: (id: string) => request<FilmRenderStatus>(`/api/film/projects/${id}/render/pause`, { method: 'POST' }),
  resumeFilmRender: (id: string) => request<FilmRenderStatus>(`/api/film/projects/${id}/render/resume`, { method: 'POST' }),
  retryFilmRender: (jobId: string) => request<FilmRenderJob>(`/api/film/render/jobs/${jobId}/retry`, { method: 'POST' }),
  filmPipelineStatus: (id: string) => request<FilmPipelineStatus>(`/api/film/projects/${id}/pipeline`),
  startFilmPipeline: (id: string, opts?: { from_scene_id?: string; scene_limit?: number }) => request<FilmPipelineStatus>(`/api/film/projects/${id}/pipeline/start`, {
    method: 'POST', body: JSON.stringify({ from_scene_id: opts?.from_scene_id || null, scene_limit: opts?.scene_limit || null }),
  }),
  pauseFilmPipeline: (id: string) => request<FilmPipelineStatus>(`/api/film/projects/${id}/pipeline/pause`, { method: 'POST' }),
  resumeFilmPipeline: (id: string) => request<FilmPipelineStatus>(`/api/film/projects/${id}/pipeline/resume`, { method: 'POST' }),
  stopFilmPipeline: (id: string) => request<FilmPipelineStatus>(`/api/film/projects/${id}/pipeline/stop`, { method: 'POST' }),
  retryFilmPipeline: (id: string, sceneId: string) => request<FilmPipelineStatus>(`/api/film/projects/${id}/pipeline/retry/${encodeURIComponent(sceneId)}`, { method: 'POST' }),
  filmPipelineGate: (id: string) => request<FilmPipelineGate>(`/api/film/projects/${id}/pipeline/gate`),
  filmPipelineLedger: (id: string) => request<{ project_id: string; ledgers: FilmSceneLedger[]; scenes: FilmScenePipelineState[] }>(`/api/film/projects/${id}/pipeline/ledger`),
  filmAudioRequirements: (id: string) => request<{ project_id: string; scenes: FilmAudioRequirements[]; speech_required_count: number }>(`/api/film/projects/${id}/audio/requirements`),
  filmVoiceProfiles: (id: string) => request<{ project_id: string; profiles: FilmVoiceProfile[] }>(`/api/film/projects/${id}/voice-profiles`),
  filmSpeakerStatus: () => request<FilmSpeakerStatus>('/api/film/speaker/status'),
  filmSpeakerCalibration: (id: string) => request<{ project_id: string; calibration: FilmSpeakerCalibrationReport }>(`/api/film/projects/${id}/speaker/calibration`),
  runFilmSpeakerAcceptance: (id: string) => request<FilmSpeakerAcceptance>(`/api/film/projects/${id}/speaker/acceptance`, { method: 'POST' }),
  previewFilmNarratorUpgrade: (id: string, voice_id: string, speed = 1) => request<FilmNarratorUpgradePreview>(`/api/film/projects/${id}/narrator/preview`, {
    method: 'POST', body: JSON.stringify({ voice_id, speed }),
  }),
  applyFilmNarratorUpgrade: (id: string, voice_id: string, speed = 1) => request<FilmNarratorUpgradeResult>(`/api/film/projects/${id}/narrator/apply`, {
    method: 'POST', body: JSON.stringify({ voice_id, speed }),
  }),
  filmJunctions: (id: string) => request<{ project_id: string; junctions: FilmJunction[]; counts: Record<string, number> }>(`/api/film/projects/${id}/junctions`),
  checkFilmJunctions: (id: string) => request<{ project_id: string; junctions: FilmJunction[] }>(`/api/film/projects/${id}/junctions/check`, { method: 'POST' }),
  retryFilmJunction: (id: string, junctionId: string) => request<FilmJunction>(`/api/film/projects/${id}/junctions/${encodeURIComponent(junctionId)}/retry`, { method: 'POST' }),
  filmFinalStatus: (id: string) => request<FilmFinalStatus>(`/api/film/projects/${id}/final/status`),
  assembleFilmFinal: (id: string) => request<FilmFinalStatus>(`/api/film/projects/${id}/final/assemble`, { method: 'POST' }),
  runFilmMasterQc: (id: string) => request<FilmFinalStatus>(`/api/film/projects/${id}/final/qc`, { method: 'POST' }),
  filmMasterQc: (id: string) => request<FilmFinalStatus>(`/api/film/projects/${id}/final/qc`),
  filmAcceptanceSnapshots: (id: string, sceneId: string) => request<{ project_id: string; scene_id: string; snapshots: FilmAcceptanceSnapshot[] }>(`/api/film/projects/${id}/scenes/${encodeURIComponent(sceneId)}/acceptance-snapshots`),
  filmAcceptanceSnapshot: (id: string, sceneId: string, snapshotId: string) => request<FilmAcceptanceSnapshot>(`/api/film/projects/${id}/scenes/${encodeURIComponent(sceneId)}/acceptance-snapshots/${encodeURIComponent(snapshotId)}`),
  backfillAcceptanceSnapshots: (id: string) => request<{ project_id: string; created_count: number; skipped_count: number; blocked_count: number }>(`/api/film/projects/${id}/acceptance-snapshots/backfill`, { method: 'POST' }),
  filmEvents: (id: string, query?: { scene_id?: string; event_type?: string; severity?: string; run_id?: string }) => {
    const params = new URLSearchParams()
    if (query?.scene_id) params.set('scene_id', query.scene_id)
    if (query?.event_type) params.set('event_type', query.event_type)
    if (query?.severity) params.set('severity', query.severity)
    if (query?.run_id) params.set('run_id', query.run_id)
    const suffix = params.toString() ? `?${params}` : ''
    return request<{ project_id: string; events: FilmPipelineEvent[]; count: number }>(`/api/film/projects/${id}/events${suffix}`)
  },
  filmMetrics: (id: string, runId?: string) => request<FilmPipelineMetrics>(`/api/film/projects/${id}/metrics${runId ? `?run_id=${encodeURIComponent(runId)}` : ''}`),
  filmRecovery: (id: string) => request<FilmRecoveryStatus>(`/api/film/projects/${id}/recovery`),
  reconcileFilmRecovery: (id: string) => request<Record<string, unknown>>(`/api/film/projects/${id}/recovery/reconcile`, { method: 'POST' }),
  filmCapabilityMatrix: (mediaType?: string) => request<FilmCapabilityMatrix>(`/api/film/capabilities/matrix${mediaType ? `?media_type=${encodeURIComponent(mediaType)}` : ''}`),
  refreshFilmCapabilityMatrix: (projectId?: string) => request<Record<string, unknown>>(`/api/film/capabilities/matrix${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`, { method: 'POST' }),
  filmPipelineCandidates: (id: string, sceneId?: string) => {
    const suffix = sceneId ? `?scene_id=${encodeURIComponent(sceneId)}` : ''
    return request<{ project_id: string; scene_id?: string; candidates: FilmPipelineCandidate[] }>(`/api/film/projects/${id}/pipeline/candidates${suffix}`)
  },
  filmMedia: (id: string, query?: { scene_id?: string; media_type?: string; role?: string; status?: string; selected_only?: boolean }) => {
    const params = new URLSearchParams()
    if (query?.scene_id) params.set('scene_id', query.scene_id)
    if (query?.media_type) params.set('media_type', query.media_type)
    if (query?.role) params.set('role', query.role)
    if (query?.status) params.set('status', query.status)
    if (query?.selected_only) params.set('selected_only', 'true')
    const suffix = params.toString() ? `?${params}` : ''
    return request<FilmMediaList>(`/api/film/projects/${id}/media${suffix}`)
  },
  filmSceneMedia: (projectId: string, sceneId: string) => request<FilmMediaList>(`/api/film/projects/${projectId}/scenes/${sceneId}/media`),
  filmMediaDetail: (mediaId: string) => request<FilmGeneratedMedia>(`/api/film/media/${mediaId}`),
  selectFilmMedia: (mediaId: string) => request<FilmGeneratedMedia>(`/api/film/media/${mediaId}/select`, { method: 'POST' }),
  filmMediaVersions: (mediaId: string) => request<FilmMediaVersions>(`/api/film/media/${mediaId}/versions`),
  videoProxy: () => request<VideoProxyStatus>('/api/video/proxy'),
  saveVideoProxy: (proxy_url: string) => request<VideoProxyStatus & { ok: boolean }>('/api/video/proxy', {
    method: 'PUT', body: JSON.stringify({ proxy_url }),
  }),
  testVideoProxy: () => request<VideoProxyTest>('/api/video/proxy/test', { method: 'POST' }),
  removeVideoProxy: () => request<{ ok: boolean }>('/api/video/proxy', { method: 'DELETE' }),
  videoJobs: () => request<VideoJob[]>('/api/video/jobs'),
  videoJob: (id: string) => request<VideoJob>(`/api/video/jobs/${id}`),
  createVideoJob: (url: string, provider: string, model: string) => request<VideoJob>('/api/video/jobs', {
    method: 'POST', body: JSON.stringify({ url, provider, model }),
  }),
  deleteVideoJob: (id: string) => request(`/api/video/jobs/${id}`, { method: 'DELETE' }),
}
