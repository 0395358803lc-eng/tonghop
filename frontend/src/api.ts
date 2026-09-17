import type { Chat, Provider, VideoJob, VideoProxyStatus, VideoProxyTest, FilmProject, FilmProjectSummary, FilmSettings, FilmScene, FilmRenderStatus, FilmRenderJob } from './types'

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, { headers: { 'Content-Type': 'application/json', ...(options?.headers || {}) }, ...options })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(payload.detail || 'Yêu cầu thất bại')
  }
  return response.json()
}

export const api = {
  providers: () => request<Provider[]>('/api/providers'),
  models: (provider: string) => request<{ models: string[]; configured: boolean }>(`/api/providers/${provider}/models`),
  saveProvider: (provider: string, api_key: string, base_url?: string) => request(`/api/providers/${provider}`, {
    method: 'PUT', body: JSON.stringify({ api_key, base_url: base_url || null }),
  }),
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
  updateFilmProject: (id: string, values: { name?: string; settings?: FilmSettings }) => request<FilmProject>(`/api/film/projects/${id}`, {
    method: 'PATCH', body: JSON.stringify(values),
  }),
  updateFilmScene: (projectId: string, sceneId: string, values: Partial<FilmScene>) => request<FilmProject>(`/api/film/projects/${projectId}/scenes/${sceneId}`, {
    method: 'PATCH', body: JSON.stringify(values),
  }),
  deleteFilmProject: (id: string) => request<{ ok: boolean }>(`/api/film/projects/${id}`, { method: 'DELETE' }),
  filmRenderStatus: (id: string) => request<FilmRenderStatus>(`/api/film/projects/${id}/render`),
  queueFilmRender: (id: string, scene_ids?: string[]) => request<FilmRenderStatus>(`/api/film/projects/${id}/render/queue`, {
    method: 'POST', body: JSON.stringify({ scene_ids: scene_ids?.length ? scene_ids : null }),
  }),
  pauseFilmRender: (id: string) => request<FilmRenderStatus>(`/api/film/projects/${id}/render/pause`, { method: 'POST' }),
  resumeFilmRender: (id: string) => request<FilmRenderStatus>(`/api/film/projects/${id}/render/resume`, { method: 'POST' }),
  retryFilmRender: (jobId: string) => request<FilmRenderJob>(`/api/film/render/jobs/${jobId}/retry`, { method: 'POST' }),
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
