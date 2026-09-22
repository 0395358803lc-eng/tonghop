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
  require_provider_assets?: boolean
  flow_project_id?: string | null
  flow_model?: string | null
  shot_max_seconds?: number
  provider_shot_max_seconds?: number
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
  source_hash?: string | null
  source_span?: { start?: number; end?: number; hash?: string }
  duration_budget?: {
    declared_seconds?: number
    required_seconds?: number
    provider_shot_max_seconds?: number
    shot_count?: number
    passed?: boolean
    error_code?: string | null
  }
  shots?: Array<{
    id: string
    scene_id: string
    shot_index: number
    duration: number
    action?: string
    dialogue?: Array<{ character_id?: string; text?: string; emotion?: string }>
    voiceover?: string
    flow_prompt?: string
    flow_prompt_meta?: Record<string, unknown>
  }>
  flow_prompt_meta?: Record<string, unknown>
  merge_audit?: Record<string, unknown>
  gate?: Record<string, unknown>
  render_status: string
  result_url?: string | null
}

export type FilmConsistencyIssue = {
  scene?: string
  scene_id?: string
  code?: string
  detail?: string
  evidence?: string
  confidence?: number
  repairability?: string
  suggested_patch_type?: string
  entity_id?: string | null
  dimension?: string | null
  actual_value?: unknown
  expected_value?: unknown
  evidence_verified?: boolean | null
  verification_note?: string
}

export type FilmConsistencyReport = {
  version?: string
  evaluated_at?: string
  status?: 'PASS' | 'FAIL' | 'REPAIRABLE' | 'REVIEW_REQUIRED' | string
  final_gate?: boolean
  state_model?: string
  deterministic?: {
    final_gate?: boolean
    gates?: Record<string, boolean>
    error_count?: number
    warning_count?: number
    errors?: FilmConsistencyIssue[]
    warnings?: FilmConsistencyIssue[]
  }
  semantic_review?: {
    available?: boolean
    provider?: string
    requested_model?: string
    model?: string | null
    models_used?: string[]
    fallback_used?: boolean
    tried_models?: string[]
    failover_events?: Array<Record<string, unknown>>
    verdict?: string
    raw_verdict?: string
    summary?: string
    error?: string
    status_code?: number | null
    retry_after_seconds?: number | null
    issues?: FilmConsistencyIssue[]
    rejected_findings?: FilmConsistencyIssue[]
    recheck_performed?: boolean
    verdict_normalized_by_evidence_verifier?: boolean
  }
  rejected_ai_findings?: FilmConsistencyIssue[]
  validator_conflicts?: Array<Record<string, unknown>>
  effective_errors?: FilmConsistencyIssue[]
  repairable_items?: FilmConsistencyIssue[]
  review_items?: FilmConsistencyIssue[]
  source_sensitive_items?: FilmConsistencyIssue[]
  scene_results?: Array<{ scene_id?: string; passed?: boolean; repairable?: boolean; issues?: FilmConsistencyIssue[] }>
  summary?: {
    total_scenes?: number
    passed_scenes?: number
    deterministic_gate?: boolean
    ai_available?: boolean
    ai_verdict?: string
    conflict_count?: number
  }
}

export type FilmProductionGate = {
  version?: string
  evaluated_at?: string
  final_gate?: boolean
  status?: 'READY_TO_RENDER' | 'NEEDS_REPAIR' | string
  gates?: Record<string, boolean>
  error_count?: number
  warning_count?: number
  errors?: Array<{ scene?: string; code?: string; detail?: string }>
  warnings?: Array<{ scene?: string; code?: string; detail?: string }>
  repairable_errors?: Array<{ scene?: string; code?: string; detail?: string }>
  nonrepairable_errors?: Array<{ scene?: string; code?: string; detail?: string }>
  scene_results?: Array<{ scene_id?: string; passed?: boolean; repairable?: boolean; issues?: Array<Record<string, unknown>> }>
}

export type FilmProject = {
  id: string
  name: string
  original_text: string
  provider: string
  model: string
  status: 'draft' | 'queued' | 'analyzing' | 'ready' | 'needs_repair' | 'failed'
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
  source_manifest?: Record<string, unknown>
  integrity?: Record<string, unknown>
  production_gate?: FilmProductionGate
  consistency_report?: FilmConsistencyReport
  repair_log?: Array<Record<string, unknown>>
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
  provider_error_code?: string | null
  qc_status: string
  consistency_score?: number | null
  qc: Record<string, unknown>
  created_at: string
  updated_at: string
}

export type FlowProject = {
  id: string
  href: string
  modified_label?: string | null
}

export type FlowVideoCapabilities = {
  project_id: string
  modes: string[]
  models: string[]
  aspect_ratios: string[]
  resolutions: string[]
  durations: number[]
  output_counts: number[]
}

export type FlowImageCapabilities = {
  project_id?: string | null
  modes: string[]
  models: string[]
  aspect_ratios: string[]
  output_counts: number[]
}

export type FlowMetrics = {
  jobs_total: number
  jobs_by_status: Record<string, number>
  active_jobs: number
  video_files: number
  video_bytes: number
  image_files?: number
  image_bytes?: number
  media_bytes?: number
  latest_job_updated_at?: string | null
}

export type FlowStatus = {
  configured: boolean
  enabled: boolean
  bridge_url?: string | null
  masked_key?: string | null
}

export type FlowSavedSession = {
  id: string
  name: string
  account_hint?: string | null
  saved_at?: string | null
  last_used_at?: string | null
}

export type FlowSessionList = {
  ok: boolean
  active_id?: string | null
  active_account?: string | null
  authenticated?: boolean
  sessions: FlowSavedSession[]
  message?: string
}

export type FilmProviderResource = {
  id: string
  project_id: string
  provider: string
  resource_type: 'character' | 'location' | 'prop'
  entity_id: string
  fingerprint: string
  status: 'pending' | 'ready' | 'locked' | 'stale' | 'error' | 'retired'
  provider_ref?: string | null
  local_path?: string | null
  error?: string | null
  metadata: Record<string, unknown>
  created_at: string
  updated_at: string
}

export type FilmRenderStatus = {
  adapter: { id: string; name: string; configured: boolean; supports_reference_frame: boolean; contract?: string | null }
  qc_adapter: { id: string; name: string; configured: boolean; min_score: number; contract?: string | null }
  queue: { project_id: string; adapter: string; paused: boolean; created_at?: string; updated_at?: string }
  jobs: FilmRenderJob[]
  resources?: { provider: string; total: number; by_status: Record<string, number>; all_ready: boolean; locked?: number; qc_passed?: number; qc_required?: number }
}

export type FilmGeneratedMedia = {
  id: string
  project_id: string
  scene_id?: string | null
  resource_type?: string | null
  entity_id?: string | null
  output_key: string
  media_type: 'image' | 'video'
  role: 'canonical_image' | 'scene_image' | 'scene_video' | 'final_video' | 'repair_candidate'
  status: 'pending' | 'processing' | 'completed' | 'failed' | 'retired'
  provider?: string | null
  model?: string | null
  provider_job_id?: string | null
  file_url?: string | null
  thumbnail_url?: string | null
  download_name?: string | null
  mime_type?: string | null
  file_size?: number | null
  width?: number | null
  height?: number | null
  duration_seconds?: number | null
  version: number
  is_selected: boolean
  qc_status: string
  qc_score?: number | null
  qc: Record<string, unknown>
  metadata: Record<string, unknown>
  created_at: string
  updated_at: string
}

export type FilmMediaList = {
  project_id: string
  scene_id?: string
  count: number
  media: FilmGeneratedMedia[]
}

export type FilmMediaVersions = {
  media_id: string
  output_key: string
  count: number
  versions: FilmGeneratedMedia[]
}

export type FilmPipelineGateError = {
  scene: string
  code: string
  detail: string
}

export type FilmQcDimension = {
  score?: number | null
  threshold?: number
  status?: 'passed' | 'failed' | 'not_evaluated' | string
  passed?: boolean | null
  hard?: boolean
  evidence?: unknown
  issue?: string | null
}

export type FilmQcEvidence = {
  canonical?: unknown
  previous_accepted_last_frame?: string | null
  current_first_frame?: string | null
  current_last_frame?: string | null
  sampled_video_frames?: Array<string | { url?: string; timestamp?: number }>
  audio?: unknown
  speech?: unknown
}

export type FilmSceneQcReport = {
  version?: string
  qc_status?: string
  passed?: boolean | null
  consistency_score?: number | null
  identity_independent_fail?: boolean
  hard_gate?: { passed?: boolean; failed?: string[]; dimensions?: Record<string, FilmQcDimension> }
  dimensions?: Record<string, FilmQcDimension>
  dimensions_passed?: number
  dimensions_total?: number
  incomplete_dimensions?: string[]
  qc_complete?: boolean
  evidence?: FilmQcEvidence
  issues?: unknown[]
  provider?: string
  model?: string
}

export type FilmRepairAttempt = {
  attempt?: number
  failed?: string[]
  failed_dimensions?: string[]
  lines?: string[]
  instruction?: string
  prompt?: string
  qc_status?: string
  media_id?: string | null
}

export type FilmScenePipelineState = {
  project_id?: string
  scene_id: string
  scene_index?: number
  status: string
  attempt?: number
  max_retries?: number
  current_run_id?: string | null
  current_job_id?: string | null
  selected_media_id?: string | null
  best_media_id?: string | null
  best_score?: number | null
  best_rank?: Record<string, unknown>
  error?: string | null
  blocked_reason?: string | null
  qc?: FilmSceneQcReport | Record<string, unknown>
  repair?: FilmRepairAttempt[] | unknown[]
  snapshot?: Record<string, unknown>
  updated_at?: string
}

export type FilmSceneLedger = {
  project_id?: string
  scene_id: string
  selected_media_id?: string | null
  accepted_first_frame?: string | null
  accepted_last_frame?: string | null
  character_positions?: Record<string, unknown>
  character_pose?: string | null
  character_wardrobe?: string | null
  character_visibility?: Record<string, unknown>
  prop_owner?: Record<string, unknown>
  prop_holder?: Record<string, unknown>
  prop_location?: Record<string, unknown>
  prop_state?: Record<string, unknown>
  location_id?: string | null
  time_of_day?: string | null
  lighting_state?: string | null
  camera_direction?: string | null
  dialogue_state?: string | null
  audio_state?: string | null
  snapshot?: Record<string, unknown>
  updated_at?: string
}

export type FilmPipelineCandidate = {
  id: string
  project_id?: string
  scene_id: string
  run_id?: string | null
  job_id?: string | null
  media_id?: string | null
  attempt?: number
  hard_gates_passed?: boolean
  dimensions_passed?: number
  overall_score?: number | null
  qc?: Record<string, unknown>
  is_best?: boolean
  created_at?: string
}

export type FilmPipelineGate = {
  version?: string
  evaluated_at?: string
  final_gate?: boolean
  status?: string
  checks?: Record<string, boolean>
  errors?: FilmPipelineGateError[]
  warnings?: FilmPipelineGateError[]
  flow?: Record<string, unknown>
  qc?: Record<string, unknown>
  v1?: Record<string, unknown>
}

export type FilmPipelineRun = {
  id?: string
  project_id?: string
  status: string
  current_scene_id?: string | null
  current_scene_index?: number | null
  from_scene_id?: string | null
  scene_limit?: number | null
  stop_after_current?: boolean
  gate?: FilmPipelineGate | Record<string, unknown> | null
  log?: Array<Record<string, unknown>>
  error?: string | null
  started_at?: string | null
  updated_at?: string | null
}

export type FilmPipelineCounts = {
  total: number
  approved: number
  generating: number
  qc_failed: number
  blocked: number
  waiting: number
  stale: number
  by_status?: Record<string, number>
}

export type FilmPipelineStatus = {
  project_id: string
  run?: FilmPipelineRun | null
  scenes: FilmScenePipelineState[]
  ledgers?: FilmSceneLedger[]
  gate?: FilmPipelineGate | { errors?: FilmPipelineGateError[]; final_gate?: boolean; checks?: Record<string, boolean> } | null
  run_gate?: FilmPipelineGate | Record<string, unknown> | null
  worker_active?: boolean
  counts?: FilmPipelineCounts
}

export type FilmDialogueLine = {
  speaker_character_id?: string | null
  text?: string
  delivery?: string
  language?: string
  timing?: { start?: number | null; end?: number | null }
  visible_speaker_required?: boolean
  voice_profile_id?: string | null
  error?: string | null
}

export type FilmAudioCue = {
  type?: string
  description?: string
  continuity_group?: string
  time?: number
  text?: string
}

export type FilmAudioRequirements = {
  scene_id?: string
  speech_required?: boolean
  speech_status?: 'required' | 'not_required' | string
  dialogue?: FilmDialogueLine[]
  voiceover?: FilmAudioCue[]
  ambient?: FilmAudioCue[]
  sfx?: FilmAudioCue[]
  music?: FilmAudioCue[]
  speakers?: string[]
  visible_speakers?: string[]
  voice_profile_ids?: string[]
  errors?: Array<{ code: string; detail?: string }>
  language?: string
}

export type FilmVoiceProfile = {
  id: string
  project_id: string
  character_id: string
  profile: Record<string, unknown>
  provider?: string | null
  provider_voice_id?: string | null
}

export type FilmSpeakerStatus = {
  enabled: boolean
  required: boolean
  model_path: string
  model_exists: boolean
  threshold: number
  sample_rate: number
  min_seconds: number
  backend: string
  embedding_strategy?: string
}

export type FilmSpeakerCalibration = {
  speaker_id: string
  positive_pair_count?: number
  negative_pair_count?: number
  min_required_gap?: number
  positive_min?: number | null
  positive_avg?: number | null
  positive_max?: number | null
  negative_min?: number | null
  negative_avg?: number | null
  negative_max?: number | null
  gap?: number | null
  status: string
  threshold?: number | null
  reason?: string
}

export type FilmSpeakerAcceptanceItem = {
  scene_id: string
  media_id?: string | null
  expected_speakers?: string[]
  speaker_character_id?: string | null
  status: string
  passed?: boolean | null
  speaker_similarity?: number | null
  speaker_similarity_percent?: number | null
  raw_similarity?: number | null
  threshold?: number | null
  threshold_source?: string | null
  calibrated?: boolean
  reference_scene_id?: string | null
  reference_created?: boolean | null
  stt_passed?: boolean
  stt_match_score?: number | null
  language?: string | null
  speech_segments?: number
  word_count?: number
  error?: string | null
}

export type FilmSpeakerAcceptance = {
  project_id: string
  calibration?: {
    status?: string | null
    character_status?: string | null
    narrator_status?: string | null
    model?: string | null
    embedding_strategy?: string | null
    threshold?: number | null
    positive_min?: number | null
    negative_max?: number | null
    gap?: number | null
    sample_count?: number
    speaker_count?: number
    speaker_calibrations?: Record<string, FilmSpeakerCalibration>
    skipped?: Array<Record<string, unknown>>
  }
  speech_scenes: number
  enrolled_references: number
  verified_scenes: number
  failed_scenes: number
  blocked_scenes: number
  min_similarity?: number | null
  max_similarity?: number | null
  average_similarity?: number | null
  passed: boolean
  items: FilmSpeakerAcceptanceItem[]
}

export type FilmJunction = {
  id: string
  project_id: string
  previous_scene_id: string
  next_scene_id: string
  status: 'PENDING' | 'RUNNING' | 'PASS' | 'FAIL' | 'REPAIRING' | 'BLOCKED' | 'STALE' | string
  selected_previous_media_id?: string | null
  selected_next_media_id?: string | null
  qc?: {
    passed?: boolean
    blocked?: boolean
    overall_score?: number | null
    dimensions?: Record<string, FilmQcDimension>
    hard_gate?: { passed?: boolean; failed?: string[] }
    issues?: Array<Record<string, unknown>>
    evidence?: {
      previous_last_frame?: string | null
      next_first_frame?: string | null
      selected_previous_media_id?: string | null
      selected_next_media_id?: string | null
      vision_provider?: string | null
      vision_model?: string | null
    }
    vision?: { provider?: string | null; model?: string | null }
    repair?: { scene_id?: string; reason?: string; prefer?: string }
  }
  score?: number | null
  attempt?: number
  error?: string | null
  repair_target_scene?: string | null
  pipeline_run_id?: string | null
}

export type FilmFinalRender = {
  id: string
  project_id: string
  version: number
  status: string
  media_id?: string | null
  manifest?: {
    scene_count?: number
    version?: number
    manifest_hash?: string
    items?: Array<{ scene_id: string; scene_index: number; media_id: string; version: number }>
  }
  manifest_hash?: string | null
  error?: string | null
  media?: FilmGeneratedMedia | null
  qc?: Record<string, any>
}

export type FilmAcceptanceSnapshot = {
  id: string
  project_id: string
  scene_id: string
  snapshot_hash: string
  created_at?: string | null
  payload?: Record<string, any>
}

export type FilmFinalStatus = {
  project_id: string
  gate: {
    passed: boolean
    code?: string | null
    errors?: Array<{ code: string; scene_id?: string; previous_scene_id?: string; next_scene_id?: string; status?: string; detail?: string }>
    scene_count?: number
    approved_count?: number
  }
  current?: FilmFinalRender | null
  history?: FilmFinalRender[]
}

export type FilmPipelineEvent = {
  id: string
  project_id: string
  run_id?: string | null
  scene_id?: string | null
  job_id?: string | null
  event_type: string
  severity: string
  created_at?: string | null
  payload?: Record<string, any>
}

export type FilmPipelineMetrics = {
  project_id: string
  run_id?: string | null
  event_count: number
  retry_count?: number
  success_count?: number
  fail_count?: number
  timeout_count?: number
  scene_render_duration_avg_sec?: number | null
  flow_wait_duration_avg_sec?: number | null
  qc_duration_avg_sec?: number | null
  junction_qc_duration_avg_sec?: number | null
  by_type?: Record<string, number>
}

export type FilmRecoveryStatus = {
  project_id: string
  lease?: {
    worker_id?: string | null
    run_id?: string | null
    scene_id?: string | null
    lease_until?: string | null
    heartbeat_at?: string | null
  } | null
  orphans: Array<{ id: string; scene_id?: string | null; status?: string | null }>
  recovery_state_clean: boolean
  event_store_available: boolean
}

export type FilmCapabilityItem = {
  id: string
  provider: string
  model: string
  media_type: string
  max_references?: number | null
  resolutions?: string[]
  durations?: Array<number | string>
  aspect_ratios?: string[]
  checked_at?: string | null
}

export type FilmCapabilityMatrix = {
  provider: string
  fresh: boolean
  items: FilmCapabilityItem[]
  count: number
}

export type DesktopReadyStatus = {
  ok: boolean
  ready: boolean
  health: boolean
  checks: {
    db: boolean
    event_store: boolean
    flow_configured: boolean
    flow_authenticated: boolean
    capability_matrix_fresh: boolean
    speaker_verifier_ready: boolean
  }
  flow?: {
    ok?: boolean
    authenticated?: boolean
    session?: {
      authenticated?: boolean
      account_authenticated?: boolean
      page_usable?: boolean
      project_usable?: boolean
      state?: string
    }
    [key: string]: unknown
  } | null
  speaker_identity?: {
    enabled?: boolean
    required?: boolean
    model_exists?: boolean
    provider?: string
    embedding_strategy?: string
    [key: string]: unknown
  } | null
  runtime?: {
    desktop_mode?: boolean
    active_pipelines?: number
    configured_providers?: string[]
    configured_provider_count?: number
  }
  storage?: {
    total_bytes: number
    used_bytes: number
    free_bytes: number
    free_gb: number
    used_percent: number
  } | null
}
