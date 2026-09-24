# TH Media — Google Flow Session Bridge Integration

## Mục tiêu
Tích hợp pipeline tạo phim TH Media với Google Flow bằng persistent Chrome profile đã đăng nhập thủ công, không yêu cầu Gemini/Vertex API key và không phụ thuộc cookie export định kỳ.

## Nguyên tắc
- Không lưu cookie Google trong SQLite hoặc frontend.
- Nguồn session chính là dedicated persistent Chrome profile của Flow Bridge.
- Cookie export không còn thuộc runtime production; importer cũ đã chuyển vào .data devtools chỉ để lưu dấu lịch sử.
- Khi Google yêu cầu xác thực lại, hệ thống mở Chrome profile để người dùng đăng nhập thủ công.
- TH Media chỉ gọi bridge qua localhost và API key nội bộ.
- Không bypass CAPTCHA/anti-abuse; trang Flow tự xử lý cơ chế bảo vệ trong browser session thật.
- Flow là render provider; Story Bible, Scene, Continuity và QC vẫn do TH Media quản lý.

## A — Connection & Configuration
- [x] Secure Flow Bridge settings trong secure_settings.
- [x] Bridge URL chỉ cho phép loopback/local.
- [x] Bridge API key được mã hóa bằng crypto hiện tại.
- [x] GET/PUT/DELETE /api/flow.
- [x] POST /api/flow/test.
- [x] UI Settings cho Flow Bridge.
- [x] Frontend production build PASS.
- [x] Flow Project + Flow Model được chọn trong Film Studio.
- [x] Capability thật điều khiển duration/aspect/resolution/model trong UI.

## B — Flow Bridge Runtime
- [x] Service riêng backend/flow_bridge.
- [x] Bearer authentication cho toàn bộ bridge API.
- [x] GET /v1/health và GET /v1/session.
- [x] Dedicated Chrome profile + CDP 127.0.0.1:9223.
- [x] Flow Bridge 127.0.0.1:8765.
- [x] Dedicated persistent Chrome profile là nguồn session chính.
- [x] Legacy cookie importer đã được loại khỏi runtime production và archive vào .data devtools.
- [x] POST /v1/session/open-login mở đúng Chrome profile foreground để đăng nhập thủ công.
- [x] POST /api/flow/open-login proxy từ main backend.
- [x] UI có nút Open Flow Login + trạng thái Authenticated / Expired.
- [x] start_runtime.ps1 tự mở login window nếu session ở trạng thái REAUTH_REQUIRED.
- [x] Không expose cookie/session secret qua API/log/database.
- [x] Persist bridge jobs vào .data/flow_bridge_jobs.json.
- [x] Job persisted vẫn truy vấn được sau restart bridge.
- [x] Job bị gián đoạn khi restart được recovery thành failed thay vì biến mất.
- [x] start_runtime.ps1 chỉ khởi động thành phần thiếu và không chiếm/kill port khác.
- [x] Windows Startup entry THMediaFlowRuntime.cmd.
- [x] Chrome Flow auto-start minimized khi session hợp lệ; tự mở foreground login window khi cần re-authentication.

## C — Flow Project / Asset Adapter
- [x] Liệt kê Flow projects của phiên thật.
- [x] Đọc capability video theo Flow project.
- [x] Chọn/mapping flow_project_id vào TH Media project settings.
- [x] Asset picker và native file chooser đã được xác minh.
- [x] Upload reference image vào Flow.
- [x] Reuse asset cùng filename nếu đã tồn tại trong picker.
- [x] Last-frame local có thể được chọn làm ingredient/reference cho scene sau.
- [x] Chuẩn hóa Flow asset ID vào provider_resources (`flow_media_id -> provider_ref` khi có ID thật).
- [x] Mapping Character Bible/Location Bible/Prop Bible thành reusable canonical asset set.
- [x] Asset fingerprint/hash cache theo nội dung ảnh chuẩn hóa JPEG; upload trùng nội dung tái sử dụng file/version hiện tại.

## D — Video Generation Contract
- [x] POST /v1/generations/video hoạt động thật.
- [x] GET /v1/jobs/{job_id} hoạt động thật.
- [x] Async submit -> poll -> result.
- [x] Text-to-video PASS.
- [x] Reference-image-to-video PASS.
- [x] Capability normalization cho model/aspect/resolution/duration.
- [x] Video card mới được nhận diện bằng DOM/media src.
- [x] Download action cấp video card được xác định.
- [x] 720p original download trả MP4 thật.
- [x] Cache MP4 về .data/flow_downloads/{job_id}.
- [x] Tự extract first-frame + last-frame bằng ffmpeg.
- [x] TH Media media routes trả video/mp4 và image/jpeg thật.
- [x] Flow render adapter đã nối vào main film render queue.
- [x] Idempotency key theo TH Media render job; retry/network timeout không submit duplicate.
- [x] Provider error classification: SESSION_EXPIRED / REFERENCE_ERROR / GENERATION_TIMEOUT / DOWNLOAD_ERROR / CAPABILITY_MISMATCH / FLOW_UI_CHANGED / FLOW_RUNTIME_ERROR.
- [x] Dialogue + native audio live acceptance: 8/8 speech-required scenes có audio present/non-silent và Vietnamese STT match PASS.

## E — Film Pipeline
- [x] Scene 1 render thật trên Flow project test.
- [x] Scene 1 trả result.mp4 + first-frame + last-frame.
- [x] Scene 2 dùng last-frame Scene 1 làm ingredient/reference.
- [x] Scene 2 continuity generation PASS thật.
- [x] Chỉ chuyển job tiếp khi generation hiện tại kết thúc.
- [x] Không đánh dấu Completed nếu chưa có media thật.
- [x] Lock Character/Location/Wardrobe bằng reusable canonical references xuyên toàn phim.
- [x] Voice identity/dialogue continuity xuyên cảnh — verifier acoustic thật dùng sherpa-onnx ERes2Net + Whisper word timestamps + Silero VAD + longest clean speech chunk. Project 273b8bf6-469e-45f5-994f-53598b126c9b hiện có CHAR_001, CHAR_002 và NARRATOR đều calibrated; narrator canonical voice được lưu bằng voice profile + acoustic identity và UI đọc lại từ calibration persistent.
  - [x] CHAR_001: threshold project-calibrated ~0.236; SCENE_005 và SCENE_011 PASS so với reference SCENE_003.
  - [x] CHAR_002: threshold project-calibrated ~0.328; SCENE_008 và SCENE_012 PASS so với reference SCENE_004.
  - [x] NARRATOR: SCENE_007 ↔ SCENE_015 hiện calibrate PASS; similarity tối thiểu 0.686118, threshold project-calibrated 0.476098, margin 0.420041. Voice profile dùng xKiro voice `confident-male-vietnamese` và acoustic identity 512-d được lưu bền vững.
  - [x] Audio QC dùng threshold thật từ speaker verifier; thiếu/ambiguous evidence giữ trạng thái not_evaluated/blocked, không tự gán điểm continuity.
- [x] Vision QC thật sau render + junction/boundary QC.
- [x] Auto-regenerate khi QC fail theo policy.
- [x] Acceptance full Film Studio queue: 15/15 scene APPROVED, 14/14 junction PASS, final master QC PASS.

## F — Database & Observability
- [x] Main DB lưu provider_job_id/result_url/first_frame_url/last_frame_url.
- [x] Flow Bridge có persistent job store.
- [x] Flow Bridge health độc lập với TH Media backend.
- [x] Main backend proxy đọc Flow projects/capabilities.
- [x] Provider resource mapping table `film_provider_resources` cho Flow assets.
- [x] Idempotency key theo internal render job ID.
- [x] Provider error code được lưu vào film_render_jobs.provider_error_code.
- [x] Bridge metrics: jobs_total/jobs_by_status/active_jobs/video_files/video_bytes.
- [x] /api/flow/metrics proxy + hiển thị metrics cơ bản trong Settings.
- [x] Job store retention tối đa 200 record, ưu tiên giữ active jobs.
- [x] Runtime log rotation giữ 3 phiên out/err gần nhất.

## G — Acceptance
- [x] Sai bridge key trả HTTP 401.
- [x] Logged-out session trả authenticated=false.
- [x] Logged-in session trả authenticated=true.
- [x] 21 Flow projects được đọc từ phiên thật.
- [x] Capability thật đọc được model/aspect/resolution/duration/output count.
- [x] Prompt dry-run: editor nhận prompt và Generate enable/disable đúng.
- [x] Video download 720p original + ffmpeg boundary frames PASS.
- [x] Live generation Scene 1: Lite Lower Priority · 720p · 4s · x1 PASS.
- [x] Live continuity Scene 2 bằng last-frame Scene 1 PASS.
- [x] Main backend TestClient: flow/projects/capabilities/video/frame routes đều HTTP 200.
- [x] Frontend npm run build PASS.
- [x] Backend compileall PASS.
- [x] Persistence sau restart bridge PASS.
- [x] Live 3-scene continuity chain PASS: Scene 1 -> Scene 2 -> Scene 3 bằng last-frame reference.
- [x] Dialogue + audio acceptance trên phim 15 cảnh: 8/8 speech-required scenes PASS audio + STT.
- [x] Pause/resume/retry acceptance qua Film Studio queue: resume/pause live giữ nguyên 15 completed jobs; retry live đã được chứng minh bởi các scene nhiều attempt và cuối cùng QC PASS.
- [x] Session expiry giữa job acceptance — 24/09/2026 đã chạy runtime acceptance cô lập bằng backend sidecar EXE thật: job được nhận rồi poll giữa job trả 409/401; 409 được phân loại SESSION_EXPIRED, 401 thành BRIDGE_AUTH_ERROR, run kết thúc failed, resource error, failure event tồn tại và active=false. Không chủ động đăng xuất phiên Google thật để tránh phá session người dùng.
- [x] UI nghiệm thu trực tiếp trước khi bật AUTO PIPELINE rộng — 24/09/2026 nghiệm thu trên Tauri source + backend sidecar thật: Step 3 realtime log/gallery/stop/retry hiển thị đúng; Voice Identity đọc calibration persistent và narrator hiển thị ĐÃ CALIBRATE. Không bấm Preview/Apply TTS và không dùng thêm Flow/xKiro credit trong UI acceptance.

## Trạng thái hiện tại — 24/09/2026
Flow session hoạt động thật qua Bridge 0.4.0 tại 127.0.0.1:8765 và Chrome CDP 127.0.0.1:9223. Batch 4 full Film Studio acceptance đã PASS trên project 273b8bf6-469e-45f5-994f-53598b126c9b: 15/15 scene APPROVED, 14/14 junction PASS, 15 acceptance snapshots, final master QC 96.3/100 và final_v2.mp4 dài 120.042667 giây có H.264 + AAC. Recovery state clean, không có orphan job.

Canonical asset layer hiện đã có Story Bible resource mapping, content SHA-256 dedupe cache, Flow media ID normalization vào provider_ref, canonical Vision QC, visual lock và reusable references đưa vào render manifest. Acoustic speaker layer hiện dùng sherpa-onnx ERes2Net 512-d trên CPU, Whisper word timestamps + Silero VAD + longest-clean-chunk, lưu embedding reference ngoài DB và chỉ lưu hash/path/provenance trong voice profile. Project calibration tách riêng từng speaker; fallback official 0.60 không được hard-enforce khi calibration ambiguous. Film Studio đã có panel Voice Identity QC để chạy acceptance theo yêu cầu và hiển thị threshold/margin/blocked scenes.

Speaker state hiện tại trên project 273b8bf6-469e-45f5-994f-53598b126c9b: character_status=calibrated, CHAR_001 threshold ~0.237, CHAR_002 threshold ~0.329; narrator_status=calibrated với SCENE_007/015 similarity tối thiểu 0.686118, threshold 0.476098 và gap 0.420041. Narrator voice profile là xKiro `confident-male-vietnamese`; Film Studio đã sửa để đọc calibration/voice profile persistent ngay khi mở dự án thay vì phụ thuộc acceptance request realtime.

Regression mới nhất 24/09/2026: backend targeted regression 112/112 PASS; narrator API + narrator TTS + canonical P0 + Flow client + Batch 4 + pipeline + desktop runtime + resource cache + media đều xanh. Frontend oxlint 0 warning / 0 error và production build PASS. Tauri Rust 7/7 tests PASS và cargo check PASS.

Ba gate trước AUTO PIPELINE rộng đã đóng: canonical narrator voice, session expiry giữa job và UI acceptance trực tiếp đều PASS. P1 Bước 3 ngày 24/09/2026 cũng đã hoàn tất: bảng trạng thái từng canonical resource theo Generation/Provider/QC/File/Error; xuất JSON chẩn đoán theo project không chứa API key; command Tauri mở `film_assets/<project_id>` có chặn path traversal; resource đã tạo vẫn hiển thị khi project chuyển sang `needs_repair`; Flow health không còn retry mỗi 5 giây khi chưa đăng nhập. Bước tiếp theo là mở AUTO PIPELINE rộng theo controlled acceptance, vẫn giữ one-run-per-project, stop/retry và evidence gate trước khi phát hành.
