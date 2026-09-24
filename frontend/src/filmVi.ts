export const FILM_STYLE_OPTIONS = [
  { value: 'Cinematic', label: 'Điện ảnh' },
  { value: 'Photorealistic', label: 'Siêu chân thực' },
  { value: 'Commercial', label: 'Quảng cáo thương mại' },
  { value: 'Documentary', label: 'Phim tài liệu' },
  { value: 'Anime', label: 'Anime' },
  { value: '3D Animation', label: 'Hoạt hình 3D' },
  { value: 'Product Advertising', label: 'Quảng cáo sản phẩm' },
] as const

const FILM_STATUS: Record<string, string> = {
  draft: 'Bản nháp',
  queued: 'Đang chờ phân tích',
  analyzing: 'Đang phân tích',
  ready: 'Sẵn sàng',
  needs_repair: 'Cần sửa trước khi tạo video',
  failed: 'Thất bại',
}

const RENDER_STATUS: Record<string, string> = {
  waiting: 'Đang chờ',
  preparing: 'Đang chuẩn bị',
  generating: 'Đang tạo video',
  completed: 'Hoàn tất',
  failed: 'Thất bại',
  paused: 'Tạm dừng',
  pending: 'Đang chờ',
  ready: 'Sẵn sàng',
  locked: 'Đã khóa',
  stale: 'Cần cập nhật',
  not_configured: 'Chưa cấu hình',
  not_run: 'Chưa kiểm tra',
  unsupported: 'Không hỗ trợ',
  processing: 'Đang xử lý',
}

const QC_STATUS: Record<string, string> = {
  pass: 'Đạt',
  passed: 'Đạt',
  failed: 'Không đạt',
  pending: 'Đang chờ',
  processing: 'Đang kiểm tra',
  completed: 'Hoàn tất',
  not_run: 'Chưa kiểm tra',
  not_evaluated: 'Chưa đánh giá',
  ready: 'Sẵn sàng',
  PASS: 'Nối cảnh đạt',
  FAIL: 'Nối cảnh lỗi',
  REPAIRING: 'Đang sửa nối cảnh',
  RUNNING: 'Đang QC nối cảnh',
  PENDING: 'Chờ QC nối cảnh',
}

const GATE_LABELS: Record<string, string> = {
  SOURCE_LOCK: 'Khóa nội dung gốc',
  SOURCE_FIDELITY: 'Trung thành với kịch bản gốc',
  CANON_LOCK: 'Khóa dữ liệu chuẩn',
  CHARACTER_LOCK: 'Khóa nhân vật',
  LOCATION_LOCK: 'Khóa bối cảnh',
  PROP_STATE: 'Trạng thái đạo cụ',
  OWNERSHIP: 'Quyền sở hữu đạo cụ',
  EVENT_ORDER: 'Thứ tự sự kiện',
  START_END: 'Nối trạng thái đầu/cuối cảnh',
  DIALOGUE: 'Lời thoại',
  VOICEOVER: 'Lời thuyết minh',
  REFERENTIAL_INTEGRITY: 'Tính toàn vẹn tham chiếu',
  DURATION_BUDGET: 'Ngân sách thời lượng',
  TIMELINE_ORDER: 'Thứ tự dòng thời gian',
  SHOT_PLAN: 'Kế hoạch cú máy',
  FLOW_PROMPT: 'Lệnh Google Flow',
  FLOW_PROMPT_FIDELITY: 'Độ chính xác lệnh Google Flow',
  DERIVED_COMPLETENESS: 'Dữ liệu sản xuất phát sinh',
  DETERMINISTIC_MERGE: 'Ghép dữ liệu có kiểm soát',
  BOUNDARY_CONTEXT: 'Ngữ cảnh nối giữa các đoạn',
}

const ERROR_CODES: Record<string, string> = {
  SOURCE_LOCK_FAILED: 'Nội dung gốc đã bị thay đổi sau khi khóa.',
  SOURCE_HASH_INVALID: 'Mã kiểm tra nội dung gốc không còn hợp lệ.',
  SOURCE_SNAPSHOT_MISSING: 'Thiếu bản chụp dữ liệu nguồn để đối chiếu.',
  SOURCE_FIELD_MUTATED: 'Một trường thuộc nội dung gốc đã bị thay đổi.',
  SOURCE_EXCERPT_NOT_VERBATIM: 'Nội dung phân cảnh không còn nguyên văn từ kịch bản gốc.',
  SOURCE_EXCERPT_NOT_FOUND: 'Không tìm thấy đoạn nguồn tương ứng trong kịch bản.',
  SOURCE_SPAN_MISSING: 'Thiếu vị trí của phân cảnh trong kịch bản gốc.',
  SOURCE_SPAN_STALE: 'Vị trí phân cảnh trong kịch bản cần được cập nhật.',
  TIMELINE_SOURCE_REORDERED: 'Thứ tự nội dung đã bị đảo so với kịch bản gốc.',
  UNKNOWN_CHARACTER: 'Phân cảnh tham chiếu một nhân vật không tồn tại trong dữ liệu chuẩn.',
  UNKNOWN_LOCATION: 'Phân cảnh tham chiếu một bối cảnh không tồn tại trong dữ liệu chuẩn.',
  UNKNOWN_PROP: 'Phân cảnh tham chiếu một đạo cụ không tồn tại trong dữ liệu chuẩn.',
  UNKNOWN_PROP_TRANSFER: 'Có chuyển giao đạo cụ không hợp lệ.',
  UNKNOWN_PROP_OWNER: 'Chủ sở hữu đạo cụ không hợp lệ.',
  PROP_OWNER_MISMATCH: 'Chủ sở hữu đạo cụ không khớp với trạng thái trước đó.',
  PROP_OWNER_MISMATCH_BATCH: 'Chủ sở hữu đạo cụ ở đoạn mới không khớp lịch sử trước đó.',
  DUPLICATE_PROP_TRANSFER: 'Một lần chuyển giao đạo cụ bị lặp lại.',
  DUPLICATE_EVENT: 'Một sự kiện đã xảy ra bị lặp lại.',
  DUPLICATE_SOURCE_BEAT: 'Một hành động trong kịch bản bị lặp lại.',
  DUPLICATE_CHUNK_SOURCE: 'Đoạn kịch bản này đã được xử lý ở phần trước.',
  INVALID_PROP_STATE_TRANSITION: 'Đạo cụ chuyển trạng thái không hợp lý.',
  START_END_PROP_OWNER_MISMATCH: 'Chủ sở hữu đạo cụ không nối khớp giữa hai cảnh.',
  START_END_PROP_STATE_MISMATCH: 'Trạng thái đạo cụ không nối khớp giữa hai cảnh.',
  PREVIOUS_SCENE_LINK: 'Liên kết với phân cảnh trước không đúng.',
  LOCATION_TELEPORT: 'Nhân vật đổi bối cảnh nhưng thiếu hành động di chuyển hợp lý.',
  WEATHER_REGRESSION: 'Thời tiết thay đổi ngược mà kịch bản không có sự kiện giải thích.',
  DIALOGUE_UNKNOWN_SPEAKER: 'Không xác định được nhân vật nói câu thoại.',
  DIALOGUE_SPEAKER_OFFSCREEN: 'Nhân vật đang nói nhưng không có mặt trong phân cảnh.',
  DIALOGUE_EMPTY: 'Có mục lời thoại nhưng nội dung đang trống.',
  DIALOGUE_NOT_IN_SOURCE_EXCERPT: 'Lời thoại không khớp đoạn kịch bản gốc.',
  VOICEOVER_TOO_LONG: 'Lời thuyết minh dài hơn thời lượng cho phép.',
  VOICEOVER_NOT_IN_SOURCE_EXCERPT: 'Lời thuyết minh không khớp kịch bản gốc.',
  DURATION_BUDGET_EXCEEDED: 'Nội dung vượt quá thời lượng của phân cảnh.',
  SHOT_PLAN_STALE: 'Kế hoạch cú máy không còn đồng bộ với phân cảnh.',
  SHOT_LEVEL_RENDER_REQUIRED: 'Phân cảnh cần được tạo theo từng cú máy thay vì một video duy nhất.',
  FLOW_PROMPT_STALE: 'Lệnh Google Flow chưa đồng bộ với dữ liệu phân cảnh hiện tại.',
  FLOW_PROMPT_HASH_MISMATCH: 'Lệnh Google Flow đã thay đổi ngoài bộ biên dịch chuẩn.',
  FLOW_PROMPT_FIDELITY: 'Lệnh Google Flow đang thiếu dữ liệu bắt buộc của phân cảnh.',
  SHOT_PROMPT_STALE: 'Lệnh tạo video của một cú máy cần được cập nhật.',
  DERIVED_CAMERA_MISSING: 'Thiếu thông tin góc máy/chuyển động máy.',
  DERIVED_LIGHTING_MISSING: 'Thiếu thông tin ánh sáng.',
  DERIVED_ATMOSPHERE_MISSING: 'Thiếu thông tin không khí/bầu không khí.',
  CONSISTENCY_GATE_BLOCKED: 'Dự án chưa đạt kiểm tra tính nhất quán bằng Rule + AI.',
  CONSISTENCY_GATE_CHANGED: 'Dữ liệu đã thay đổi sau lần kiểm tra tính nhất quán; cần kiểm tra lại.',
  CONSISTENCY_NOT_VERIFIED: 'Chưa chạy kiểm tra tính nhất quán Rule + AI.',
  PRODUCTION_GATE_NOT_VERIFIED: 'Chưa chạy kiểm tra trước khi tạo video.',
  PRODUCTION_GATE_FAILED: 'Dự án chưa đạt kiểm tra trước khi tạo video.',
  PRODUCTION_GATE_CHANGED: 'Dữ liệu đã thay đổi sau lần kiểm tra trước khi tạo video.',
  INTEGRITY_NOT_VERIFIED: 'Chưa xác minh tính toàn vẹn dữ liệu.',
  BIBLE_ASSET_MISSING: 'Thiếu tài nguyên tham chiếu bắt buộc từ dữ liệu chuẩn.',
  RESOURCE_LOCK_INCOMPLETE: 'Thiếu ảnh canonical bắt buộc để khóa nhân vật, bối cảnh hoặc đạo cụ.',
  CANONICAL_ASSET_LOCKED: 'Tài nguyên hình ảnh chuẩn đã khóa và không thể thay đổi trong lúc sản xuất.',
  CANONICAL_QC_FAILED: 'Ảnh chuẩn không vượt qua Canonical Vision QC sau các lần thử cho phép.',
  CANONICAL_BATCH_BLOCKED: 'Đã dừng các ảnh còn lại vì lỗi phụ thuộc Google Flow ảnh hưởng toàn bộ batch.',
  CANONICAL_RUN_INTERRUPTED: 'Tiến trình tạo ảnh chuẩn trước đã bị gián đoạn; trạng thái đã được khôi phục an toàn.',
  SESSION_EXPIRED: 'Phiên Google Flow đã hết hoặc mất đăng nhập. Hãy đăng nhập lại Flow rồi thử lại các ảnh lỗi.',
  REAUTH_REQUIRED: 'Google Flow yêu cầu xác thực lại tài khoản trước khi tiếp tục.',
  BRIDGE_AUTH_ERROR: 'Cầu nối Google Flow không xác thực được. Hãy kiểm tra phiên/cấu hình bridge.',
  PROJECT_NOT_FOUND: 'Không tìm thấy dự án Google Flow đã lưu.',
  FLOW_PROJECT_NOT_FOUND: 'Dự án Google Flow không còn truy cập được hoặc URL dự án không hợp lệ.',
  FLOW_UI_CHANGED: 'Giao diện Google Flow đã thay đổi nên bộ tự động hóa không tìm thấy thành phần cần thiết.',
  FLOW_NAVIGATION_LOST: 'Google Flow đã rời khỏi trang dự án trong lúc xử lý.',
  FLOW_GENERATE_BUTTON_NOT_FOUND: 'Không tìm thấy nút Generate của Google Flow sau khi trang tải xong.',
  CAPABILITY_MISMATCH: 'Model hoặc cấu hình yêu cầu không phù hợp khả năng hiện tại của Google Flow.',
  FLOW_CREDITS_INSUFFICIENT: 'Tài khoản Google Flow không còn đủ credit cho thao tác này.',
  FLOW_POLICY_BLOCKED: 'Google Flow từ chối yêu cầu theo chính sách của nhà cung cấp.',
  IMAGE_GENERATION_TIMEOUT: 'Quá thời gian chờ nhà cung cấp tạo ảnh.',
  IMAGE_CREATE_HTTP_: 'Nhà cung cấp ảnh trả lỗi HTTP khi bắt đầu tạo ảnh.',
  NARRATOR_TTS_PROVIDER_NOT_CONFIGURED: 'Chưa cấu hình nhà cung cấp TTS narrator.',
  NARRATOR_SCENES_MISSING: 'Dự án không có scene chỉ dùng narrator để tạo preview giọng.',
  NARRATOR_TTS_PREVIEW_NOT_READY: 'Preview narrator chưa đạt nên chưa thể khóa giọng.',
  NARRATOR_TTS_PIPELINE_ACTIVE: 'Pipeline đang chạy. Hãy dừng hoặc chờ pipeline trước khi thay narrator.',
  NARRATOR_TTS_EXECUTION_LEASE_ACTIVE: 'Dự án đang có execution lease hoạt động; chưa thể thay narrator.',
  NARRATOR_TTS_SPEAKER_ACCEPTANCE_FAILED: 'Giọng narrator cố định chưa vượt qua speaker acceptance.',
  NARRATOR_BASE_STT_NOT_VERIFIED: 'Âm thanh narrator gốc chưa được STT xác minh nên không thể thay an toàn.',
  NARRATOR_TTS_STT_FAILED: 'Narrator mới không vượt qua kiểm tra STT sau khi ghép.',
  NARRATOR_TTS_AUDIO_INVALID: 'Audio narrator mới không hợp lệ hoặc bị im lặng.',
  NARRATOR_TTS_FINAL_NOT_APPROVED: 'Sau khi thay narrator, bản final chưa đạt trạng thái APPROVED.',
  CONTINUITY_REFERENCE_PENDING: 'Đang chờ cảnh trước đạt kiểm tra chất lượng và có khung hình cuối hợp lệ.',
  QC_FAILED: 'Video không vượt qua kiểm tra chất lượng hình ảnh bắt buộc.',
  QC_ERROR: 'Không thể hoàn tất kiểm tra chất lượng; pipeline đã dừng để tránh dùng video chưa kiểm định.',
  PRODUCTION_GATE_V2_BLOCKED: 'Cổng sản xuất V2 chưa đạt. Không thể bắt đầu pipeline tuần tự.',
  PIPELINE_QUEUE_CONTRACT: 'Pipeline chỉ được xếp đúng một cảnh mỗi lần.',
  SCENE_STATUS_TRANSITION_DENIED: 'Không được phép chuyển trạng thái cảnh này.',
  VISUAL_REFERENCE_EVIDENCE_MISSING: 'Thiếu bằng chứng hình ảnh để kiểm tra Visual Continuity Lock.',
}

const JSON_KEYS: Record<string, string> = {
  id: 'Mã',
  name: 'Tên',
  title: 'Tên dự án',
  theme: 'Chủ đề',
  genre: 'Thể loại',
  purpose: 'Mục đích',
  audience: 'Đối tượng khán giả',
  atmosphere: 'Bầu không khí',
  synopsis: 'Tóm tắt nội dung',
  synopsis_delta: 'Bổ sung tóm tắt',
  language: 'Ngôn ngữ',
  visual_style: 'Phong cách hình ảnh',
  appearance: 'Ngoại hình',
  gender: 'Giới tính',
  age: 'Tuổi',
  clothing: 'Trang phục',
  accessories: 'Phụ kiện',
  signature_traits: 'Đặc điểm nhận diện',
  personality: 'Tính cách',
  movement: 'Đặc trưng chuyển động',
  voice: 'Giọng nói',
  type: 'Loại',
  architecture: 'Kiến trúc',
  space: 'Không gian',
  interior: 'Nội thất',
  objects: 'Đồ vật',
  colors: 'Màu sắc',
  lighting: 'Ánh sáng',
  time_of_day: 'Thời điểm trong ngày',
  weather: 'Thời tiết',
  layout: 'Bố cục',
  description: 'Mô tả',
  canonical_description: 'Mô tả chuẩn',
  initial_owner: 'Chủ sở hữu ban đầu',
  initial_state: 'Trạng thái ban đầu',
  owner_initial: 'Chủ sở hữu ban đầu',
  state_initial: 'Trạng thái ban đầu',
  canonical_version: 'Phiên bản dữ liệu chuẩn',
  canonical_locked: 'Đã khóa dữ liệu chuẩn',
  identity_lock: 'Khóa nhận diện',
  layout_lock: 'Khóa bố cục',
  source_locked: 'Đã khóa nội dung gốc',
  compiler: 'Bộ biên dịch',
  scene_duration: 'Thời lượng phân cảnh',
  aspect_ratio: 'Tỷ lệ khung hình',
  resolution: 'Độ phân giải',
  style: 'Phong cách',
  character_lock: 'Khóa nhân vật',
  location_lock: 'Khóa bối cảnh',
  auto_continuity: 'Tự động nối cảnh',
  require_provider_assets: 'Bắt buộc tài nguyên tham chiếu',
  flow_project_id: 'Dự án Google Flow',
  flow_model: 'Mô hình Google Flow',
  character_id: 'Nhân vật',
  text: 'Nội dung',
  emotion: 'Cảm xúc',
  shot_max_seconds: 'Thời lượng tối đa mỗi cú máy',
  provider_shot_max_seconds: 'Giới hạn cú máy của nhà cung cấp',
}

export function filmStatusVi(value?: string | null) {
  return FILM_STATUS[String(value || '')] || String(value || 'Chưa xác định')
}

export function renderStatusVi(value?: string | null) {
  return RENDER_STATUS[String(value || '')] || String(value || 'Chưa xác định')
}

export function qcStatusVi(value?: string | null) {
  return QC_STATUS[String(value || '').toLowerCase()] || renderStatusVi(value)
}

export function gateLabelVi(value?: string | null) {
  return GATE_LABELS[String(value || '')] || String(value || '')
}

export function errorCodeVi(code?: string | null, detail?: string | null) {
  if (!code) return detail || 'Chưa xác định lỗi.'
  const exact = ERROR_CODES[code]
  const prefix = Object.entries(ERROR_CODES).find(([key]) => String(code).startsWith(key))?.[1]
  if (exact || prefix) return exact || prefix || ''
  return detail || 'Có lỗi kỹ thuật cần kiểm tra.'
}

export function styleLabelVi(value?: string | null) {
  return FILM_STYLE_OPTIONS.find(item => item.value === value)?.label || String(value || '')
}

export function adapterNameVi(value?: string | null) {
  const text = String(value || '')
  if (!text) return 'Chưa xác định'
  return text
    .replace('Local Session Bridge', 'Cầu nối phiên đăng nhập cục bộ')
    .replace('Flow Bridge', 'Cầu nối Google Flow')
    .replace('HTTP JSON Render Adapter', 'Bộ máy tạo video HTTP JSON')
    .replace('Vision QC', 'Kiểm tra hình ảnh bằng thị giác máy')
    .replace('Built-in', 'Tích hợp sẵn')
}

export function booleanVi(value: unknown) {
  return value === true ? 'Có' : value === false ? 'Không' : value
}

function localizeJsonValue(key: string, value: unknown): unknown {
  if (typeof value === 'boolean') return booleanVi(value)
  if (key === 'style' && typeof value === 'string') return styleLabelVi(value)
  if (key === 'status' && typeof value === 'string') return filmStatusVi(value)
  if (Array.isArray(value)) return value.map(item => localizeJsonObject(item))
  if (value && typeof value === 'object') return localizeJsonObject(value)
  return value
}

export function localizeJsonObject(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(item => localizeJsonObject(item))
  if (!value || typeof value !== 'object') return value
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>).map(([key, item]) => [
      JSON_KEYS[key] || key,
      localizeJsonValue(key, item),
    ]),
  )
}

export function jsonTextVi(value: unknown) {
  return JSON.stringify(localizeJsonObject(value ?? {}), null, 2)
}


export function stageVi(value?: string | null) {
  let text = String(value || '')
  const replacements: Array<[string, string]> = [
    ['Production Gate PASS · READY TO RENDER', 'Kiểm tra trước khi tạo video: Đạt · Sẵn sàng tạo video'],
    ['Production Gate FAIL · cần Auto Repair/kiểm tra', 'Kiểm tra trước khi tạo video: Chưa đạt · cần tự động sửa hoặc kiểm tra'],
    ['Production Gate stale · cần kiểm tra lại', 'Dữ liệu đã thay đổi · cần kiểm tra lại trước khi tạo video'],
    ['Production Gate PASS · Auto Repair verified', 'Tự động sửa hoàn tất · đủ điều kiện tạo video'],
    ['Auto Repair xong · vẫn còn lỗi không thể tự sửa', 'Tự động sửa hoàn tất · vẫn còn lỗi cần kiểm tra thủ công'],
    ['Continuity Gate PASS · Source locked', 'Kiểm tra tính nhất quán: Đạt · nội dung gốc đã khóa'],
    ['Continuity đã được kiểm tra', 'Đã kiểm tra tính nhất quán'],
    ['READY TO RENDER', 'SẴN SÀNG TẠO VIDEO'],
    ['NEEDS REPAIR', 'CẦN SỬA'],
  ]
  for (const [from, to] of replacements) text = text.replace(from, to)
  return text
}

const MEDIA_ROLES: Record<string, string> = {
  canonical_image: 'Ảnh chuẩn',
  scene_image: 'Ảnh phân cảnh',
  scene_video: 'Video phân cảnh',
  final_video: 'Phim cuối',
  repair_candidate: 'Bản QC chưa đạt',
}

export function mediaRoleVi(value?: string | null) {
  return MEDIA_ROLES[String(value || '')] || value || 'Media'
}

const PIPELINE_STATUS: Record<string, string> = {
  LOCKED: 'Đã khóa',
  WAITING_REFERENCE: 'Chờ reference',
  QUEUED: 'Trong hàng đợi',
  GENERATING: 'Đang tạo',
  QC_RUNNING: 'Đang QC',
  QC_FAILED: 'QC thất bại',
  REGENERATING: 'Đang tạo lại',
  APPROVED: 'Đã duyệt',
  BLOCKED: 'Bị chặn',
  STALE: 'Lỗi thời',
  running: 'Đang chạy',
  paused: 'Tạm dừng',
  stopping: 'Đang dừng',
  stopped: 'Đã dừng',
  completed: 'Hoàn tất',
  failed: 'Lỗi',
  idle: 'Chưa chạy',
  PENDING: 'Chờ QC nối cảnh',
  RUNNING: 'Đang QC nối cảnh',
  PASS: 'Nối cảnh đạt',
  FAIL: 'Nối cảnh lỗi',
  REPAIRING: 'Đang sửa nối cảnh',
  ASSEMBLY_PENDING: 'Chờ ghép phim',
  ASSEMBLING: 'Đang ghép phim',
  ASSEMBLY_FAILED: 'Ghép phim lỗi',
  QC_PENDING: 'Chờ Master QC',
}

export function pipelineStatusVi(value?: string | null) {
  const key = String(value || '')
  return PIPELINE_STATUS[key] || PIPELINE_STATUS[key.toUpperCase()] || key || 'Chưa chạy'
}

const CANONICAL_STAGE: Record<string, string> = {
  queued: 'Đang xếp hàng',
  starting: 'Đang bắt đầu',
  opening_flow_project: 'Đang mở project Google Flow',
  awaiting_generation: 'Google Flow đang tạo ảnh',
  downloading_result: 'Đang tải ảnh kết quả',
  downloaded: 'Đã tải ảnh · chờ QC',
  qc_running: 'Vision QC đang kiểm tra',
  regenerating: 'Đang tạo lại theo QC',
  succeeded: 'Hoàn tất',
  failed: 'Thất bại',
  blocked: 'Bị chặn bởi lỗi Flow',
  stopped: 'Đã dừng',
  stopping: 'Đang dừng',
  completed: 'Hoàn tất',
  failed_partial: 'Hoàn tất một phần · còn lỗi',
  idle: 'Chưa chạy',
}

export function canonicalStageVi(value?: string | null) {
  const key = String(value || '').toLowerCase()
  return CANONICAL_STAGE[key] || String(value || 'Chưa chạy')
}

const QC_DIMENSIONS: Record<string, string> = {
  identity: 'Nhận diện nhân vật',
  wardrobe: 'Trang phục',
  location: 'Bối cảnh',
  prop: 'Đạo cụ',
  boundary: 'Ranh giới cảnh',
  camera: 'Máy quay',
  lighting: 'Ánh sáng',
  audio: 'Âm thanh',
  speech: 'Lời thoại',
  dialogue_presence: 'Có thoại',
  speaker_correctness: 'Đúng người nói',
  voice_continuity: 'Giọng nhân vật',
  ambient_continuity: 'Âm nền',
  sfx_presence: 'Hiệu ứng âm thanh',
  music_continuity: 'Nhạc nền',
  audio_clipping: 'Clipped audio',
  audio_gap: 'Khoảng lặng',
  character_position: 'Vị trí nhân vật',
  body_orientation: 'Hướng cơ thể',
  prop_owner: 'Chủ đạo cụ',
  prop_holder: 'Người cầm đạo cụ',
  prop_state: 'Trạng thái đạo cụ',
  location_geometry: 'Hình học bối cảnh',
  motion_direction: 'Hướng chuyển động',
  camera_direction: 'Hướng máy',
  audio_transition: 'Chuyển âm thanh',
  dialogue_transition: 'Chuyển thoại',
  missing_scene: 'Thiếu scene',
  scene_order: 'Thứ tự scene',
  stale_scene: 'Scene lỗi thời',
  black_frames: 'Khung đen',
  audio_missing: 'Thiếu audio',
  audio_gaps: 'Khoảng lặng audio',
  duration_integrity: 'Toàn vẹn thời lượng',
  identity_continuity: 'Liên tục nhân vật',
  prop_continuity: 'Liên tục đạo cụ',
  location_continuity: 'Liên tục bối cảnh',
  dialogue_continuity: 'Liên tục thoại',
  broken_file: 'File hỏng',
}

export function qcDimensionVi(value?: string | null) {
  const key = String(value || '')
  return QC_DIMENSIONS[key] || key || 'Chiều QC'
}

const GATE_V2_CHECKS: Record<string, string> = {
  story_bible_locked: 'Story Bible',
  scenes_locked: 'Khóa phân cảnh',
  canonical_complete: 'Canonical Assets',
  canonical_qc_pass: 'Canonical QC',
  canonical_selected_valid: 'Bản canonical đang dùng',
  no_stale_canonical: 'Canonical không lỗi thời',
  flow_authenticated: 'Flow Auth',
  flow_video_available: 'Flow Video',
  qc_provider_available: 'QC Provider',
  reference_manifest_complete: 'Reference manifest',
  scene_ordering_valid: 'Scene Ordering',
  scene_dependencies_valid: 'Dependencies',
  no_unresolved_continuity: 'Continuity',
  v1_final_gate: 'Production Gate V1',
}

export function gateCheckVi(value?: string | null) {
  const key = String(value || '')
  return GATE_V2_CHECKS[key] || key || 'Hạng mục'
}

const EVENT_TYPES: Record<string, string> = {
  PIPELINE_RUN_STARTED: 'Pipeline bắt đầu',
  PIPELINE_RUN_PAUSED: 'Pipeline tạm dừng',
  PIPELINE_RUN_RESUMED: 'Pipeline tiếp tục',
  PIPELINE_RUN_STOP_REQUESTED: 'Yêu cầu dừng',
  PIPELINE_RUN_COMPLETED: 'Pipeline xong',
  PIPELINE_RUN_FAILED: 'Pipeline lỗi',
  SCENE_WAITING: 'Scene chờ',
  SCENE_QUEUED: 'Scene vào hàng đợi',
  SCENE_GENERATING: 'Scene đang tạo',
  SCENE_QC_RUNNING: 'Scene đang QC',
  SCENE_QC_FAILED: 'Scene QC thất bại',
  SCENE_REGENERATING: 'Scene tạo lại',
  SCENE_APPROVED: 'Scene đã duyệt',
  SCENE_BLOCKED: 'Scene bị chặn',
  SCENE_STALE: 'Scene lỗi thời',
  FLOW_JOB_CREATED: 'Flow job tạo',
  FLOW_JOB_STARTED: 'Flow job chạy',
  FLOW_JOB_COMPLETED: 'Flow job xong',
  FLOW_JOB_FAILED: 'Flow job lỗi',
  FLOW_JOB_TIMEOUT: 'Flow timeout',
  VIDEO_QC_STARTED: 'Video QC bắt đầu',
  VIDEO_QC_PASSED: 'Video QC PASS',
  VIDEO_QC_FAILED: 'Video QC FAIL',
  JUNCTION_QC_STARTED: 'Junction QC bắt đầu',
  JUNCTION_QC_PASSED: 'Junction PASS',
  JUNCTION_QC_FAILED: 'Junction FAIL',
  JUNCTION_REPAIR_STARTED: 'Junction repair',
  JUNCTION_REPAIR_COMPLETED: 'Junction repair xong',
  FINAL_ASSEMBLY_STARTED: 'Ghép phim bắt đầu',
  FINAL_ASSEMBLY_COMPLETED: 'Ghép phim xong',
  MASTER_QC_STARTED: 'Master QC bắt đầu',
  MASTER_QC_PASSED: 'Master QC PASS',
  MASTER_QC_FAILED: 'Master QC FAIL',
  SNAPSHOT_CREATED: 'Snapshot tạo',
  SNAPSHOT_BACKFILLED: 'Snapshot backfill',
  SNAPSHOT_STALE: 'Snapshot stale',
  LEASE_RECLAIMED: 'Thu hồi lease',
  ORPHAN_JOB_DETECTED: 'Phát hiện orphan',
  RECOVERY_RECONCILED: 'Recovery',
  CAPABILITY_REFRESHED: 'Capability refresh',
  CAPABILITY_BLOCKED: 'Capability chặn',
  CANONICAL_RUN_QUEUED: 'Ảnh chuẩn · đã xếp hàng',
  CANONICAL_RUN_STARTED: 'Ảnh chuẩn · bắt đầu',
  CANONICAL_RUN_STOP_REQUESTED: 'Ảnh chuẩn · yêu cầu dừng',
  CANONICAL_RUN_STOPPED: 'Ảnh chuẩn · đã dừng',
  CANONICAL_RUN_COMPLETED: 'Ảnh chuẩn · hoàn tất',
  CANONICAL_RUN_FAILED: 'Ảnh chuẩn · có lỗi',
  CANONICAL_RESOURCE_QUEUED: 'Tài nguyên · xếp hàng',
  CANONICAL_RESOURCE_STARTED: 'Tài nguyên · bắt đầu',
  CANONICAL_RESOURCE_PROGRESS: 'Tài nguyên · tiến trình',
  CANONICAL_RESOURCE_DOWNLOADED: 'Tài nguyên · đã tải ảnh',
  CANONICAL_RESOURCE_QC_STARTED: 'Tài nguyên · bắt đầu QC',
  CANONICAL_RESOURCE_QC_PASSED: 'Tài nguyên · QC đạt',
  CANONICAL_RESOURCE_QC_FAILED: 'Tài nguyên · QC chưa đạt',
  CANONICAL_RESOURCE_COMPLETED: 'Tài nguyên · hoàn tất',
  CANONICAL_RESOURCE_FAILED: 'Tài nguyên · lỗi',
  CANONICAL_RESOURCE_STOPPED: 'Tài nguyên · đã dừng',
}

export function eventTypeVi(value?: string | null) {
  const key = String(value || '')
  return EVENT_TYPES[key] || key || 'Event'
}

export function uiErrorVi(message?: string | null) {
  const text = String(message || '')
  if (!text) return ''
  const leading = text.match(/^([A-Z][A-Z0-9_]+):\s*/)?.[1]
  if (leading && (ERROR_CODES[leading] || Object.keys(ERROR_CODES).some(key => leading.startsWith(key)))) {
    if (leading === 'CANONICAL_BATCH_BLOCKED') {
      const nestedText = text.replace(/^CANONICAL_BATCH_BLOCKED:\s*/, '')
      const nested = nestedText.match(/^([A-Z][A-Z0-9_]+):\s*/)?.[1]
      return nested && ERROR_CODES[nested]
        ? `${errorCodeVi(leading)} ${errorCodeVi(nested)}`
        : errorCodeVi(leading)
    }
    return errorCodeVi(leading)
  }
  const code = Object.keys(ERROR_CODES).find(key => text.includes(key))
  if (code) return errorCodeVi(code)
  return text
    .replaceAll('Production Gate', 'Kiểm tra trước khi tạo video')
    .replaceAll('Auto Repair Derived', 'Tự động sửa dữ liệu phát sinh')
    .replaceAll('Render Queue', 'Hàng đợi tạo video')
    .replaceAll('Video Render Adapter', 'Bộ máy tạo video')
    .replaceAll('Waiting', 'Đang chờ')
    .replaceAll('Generate', 'Tạo video')
}
