export type Provider = {
  id: string
  name: string
  kind: string
  accent: string
  base_url: string
  configured: boolean
  masked_key?: string | null
  custom_base_url?: string | null
}

export type Message = {
  id: string
  chat_id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  created_at: string
}

export type Chat = {
  id: string
  title: string
  provider: string
  model: string
  created_at: string
  updated_at: string
  messages?: Message[]
}

export type VideoJob = {
  id: string
  url: string
  platform: 'youtube' | 'tiktok' | 'facebook'
  provider: string
  model: string
  status: 'queued' | 'processing' | 'completed' | 'failed'
  stage: string
  progress: number
  title?: string | null
  thumbnail?: string | null
  duration?: number | null
  channel?: string | null
  transcript_source?: string | null
  transcript?: string | null
  report?: string | null
  error?: string | null
  vision_status?: 'pending' | 'processing' | 'completed' | 'unsupported' | 'failed' | null
  vision_model?: string | null
  vision_note?: string | null
  visual_summary?: string | null
  keyframes?: Array<{ index: number; timestamp: number; filename: string; url: string }>
  created_at: string
  updated_at: string
}

export type VideoProxyStatus = {
  configured: boolean
  masked_proxy?: string | null
}

export type VideoProxyTest = {
  proxy_ok: boolean
  exit_ip?: string | null
  proxy?: string | null
  youtube_ok: boolean
  youtube_title?: string | null
  youtube_error?: string | null
}


export type FilmSettings = {
  scene_duration: number
  aspect_ratio: string
  resolution: string
  style: string
  character_lock: boolean
  location_lock: boolean
  auto_continuity: boolean
}

export type FilmScene = {
  id: string
  project_id: string
  scene_index: number
  title?: string | null
  source_text?: string | null
  summary?: string | null
  duration: number
  characters: string[]
  location_id?: string | null
  action?: string | null
  camera?: string | null
  lighting?: string | null
  atmosphere?: string | null
  voiceover?: string | null
  dialogue: Array<{ character_id?: string; text?: string; emotion?: string }>
  start_state?: string | null
  end_state?: string | null
  continuity: Record<string, unknown>
  visual_prompt?: string | null
  flow_prompt?: string | null
  warnings: string[]
  render_status: string
  result_url?: string | null
}

export type FilmProject = {
  id: string
  name: string
  original_text: string
  provider: string
  model: string
  status: 'draft' | 'queued' | 'analyzing' | 'ready' | 'failed'
  stage: string
  progress: number
  settings: FilmSettings
  master_prompt?: string | null
  story_bible: Record<string, unknown>
  characters: Array<Record<string, unknown>>
  locations: Array<Record<string, unknown>>
  props: Array<Record<string, unknown>>
  visual_style?: string | null
  timeline: Array<Record<string, unknown>>
  scenes: FilmScene[]
  error?: string | null
  created_at: string
  updated_at: string
}

export type FilmProjectSummary = Pick<FilmProject, 'id' | 'name' | 'provider' | 'model' | 'status' | 'stage' | 'progress' | 'error' | 'created_at' | 'updated_at'> & {
  scene_count: number
}

export type FilmRenderJob = {
  id: string
  project_id: string
  scene_id: string
  scene_index: number
  adapter: string
  status: 'waiting' | 'preparing' | 'generating' | 'completed' | 'failed' | 'paused'
  progress: number
  attempt: number
  prompt: string
  reference: Record<string, unknown>
  provider_job_id?: string | null
  result_url?: string | null
  first_frame_url?: string | null
  last_frame_url?: string | null
  error?: string | null
  qc_status: string
  consistency_score?: number | null
  qc: Record<string, unknown>
  created_at: string
  updated_at: string
}

export type FilmRenderStatus = {
  adapter: { id: string; name: string; configured: boolean; supports_reference_frame: boolean; contract?: string | null }
  qc_adapter: { id: string; name: string; configured: boolean; min_score: number; contract?: string | null }
  queue: { project_id: string; adapter: string; paused: boolean; created_at?: string; updated_at?: string }
  jobs: FilmRenderJob[]
}
