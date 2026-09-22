# TH Media — Production Acceptance & Operations Handoff

Ngày nghiệm thu: 22/09/2026
Project ID: `273b8bf6-469e-45f5-994f-53598b126c9b`

## 1. Trạng thái production hiện tại

Film Studio đã đạt production acceptance:

- Scene: **15/15 APPROVED**
- Junction: **14/14 PASS**
- Snapshot fingerprint mismatch: **0**
- Recovery state: **clean**
- Orphan jobs: **0**
- Stuck jobs: **0**
- Duplicate risk: **false**
- Final Assembly: **APPROVED**
- Final hiện hành: **v4**
- Master QC: **PASS — 96.3/100**
- Final video: `final_v4.mp4`
- Resolution: **1280×720**
- Frame rate: **24 fps**
- Video codec: **H.264**
- Audio codec: **AAC**
- Duration: **120.042667 giây**
- File size: **46,673,067 bytes**
- Black frames: **0.0 giây**
- Raw detected silence: **0.414 giây**
- Long/suspicious audio gap: **0.0 giây**
- Long-gap threshold: **0.75 giây**

Khoảng im 0.414 giây là pause ngắn tự nhiên trong narrator TTS, không phải mất audio. Master QC lưu riêng `raw_total`, `max_silence` và `suspicious_total` để không nhầm pause thoại với lỗi audio gap.

Final manifest chứa đúng **15 scene theo thứ tự** và sử dụng selected media hiện hành.

## 2. Speaker / Voice acceptance

Có **8 scene có speech**.

Trạng thái hiện hành:

- Policy accepted: **true**
- Fully verified: **true**
- Passed: **true**
- Unverified: **0**
- Failed: **0**
- Blocked: **0**
- 5 scene được speaker-match với reference.
- 3 scene đóng vai trò enrolled/reference baseline.

### Narrator deterministic voice

Narrator không còn phụ thuộc vào giọng Veo tự sinh.

Voice contract hiện hành:

- Provider: **xKiro**
- Model: **xkiro-voice**
- Voice ID: **confident-male-vietnamese**
- Language: **vi**
- Reference narrator: **SCENE_007**
- SCENE_015 narrator similarity: khoảng **0.686118**
- Project-calibrated threshold: khoảng **0.476098**
- SCENE_015 narrator: **PASS**

Benchmark trước khi áp dụng cho thấy cùng voice ID trên hai câu narrator có similarity khoảng **0.818454**, trong khi cross-voice benchmark chỉ khoảng **0.13–0.14**.

Narrator audio được synthesize server-side bằng một voice ID cố định rồi overlay vào scene. Video stream được giữ bit-identical; chỉ audio lane thay đổi. STT và speaker verification phải PASS trước khi media overlay được chấp nhận.

## 3. SCENE_015 — lịch sử xử lý

Attempt history không bị reset:

- Attempt 0: media cũ từng QC pass nhưng dùng prompt/speaker contract cũ.
- Attempt 1: Flow render thành công, QC không đạt.
- Attempt 2: Flow UI báo policy false-positive liên quan public-figure policy; detector cũ bỏ lỡ error tile và timeout.
- Attempt 3: render thành công bằng `Veo 3.1 - Lite [Lower Priority]`, runtime prompt có narrator lock + fictional-character context; QC scene PASS.
- P2 Narrator TTS: không render lại hình ảnh bằng Flow. Audio narrator của SCENE_007 và SCENE_015 được thay bằng cùng xKiro voice ID cố định, sau đó STT + acoustic verification lại.

SCENE_015 vẫn giữ attempt **3**. TTS overlay là hậu kỳ audio deterministic, không tạo attempt Flow mới.

## 4. Hardening đã triển khai

### Flow Bridge

- Selector video/model có fallback.
- Detect `flow-error-tile` mới sau mỗi request.
- Policy tile → `FLOW_POLICY_BLOCKED`.
- Policy block là terminal render error, không auto-retry mù.
- Runtime prompt có `[FICTIONAL_CHARACTER_CONTEXT]`.
- Flow job history và idempotency key được giữ nguyên.

### Pipeline

- STALE scene không auto-APPROVE selected media cũ.
- Retry từ BLOCKED có `operator_force_generation`.
- Attempt tăng đơn điệu.
- Retry không xóa job/media/candidate history.
- Existing completed provider result có thể re-QC mà không submit generation mới.

### Snapshot / migration

- Chỉ `SNAPSHOT_FINGERPRINT_MISMATCH` đủ điều kiện deterministic recovery.
- Recovery bắt buộc media selected/QC/evidence hợp lệ.
- Scene stale vì nguyên nhân khác không được auto-approve.
- Fingerprint voice chỉ chứa identity ổn định; runtime calibration timestamp/path không làm scene stale giả.

### Speaker verification

- Speaker calibration fail/ambiguous không được giải quyết bằng cách hạ threshold để ép PASS.
- `calibration_ambiguous/pending` được tách khỏi hard failure khi chưa có đủ evidence.
- Với narrator hiện tại, deterministic xKiro voice tạo calibration đủ phân biệt nên `fully_verified=true`.
- `run_project_speaker_acceptance(..., recalibrate=False)` đọc calibration đã persist, tránh false-negative summary.

### Narrator TTS

- Module: `film_narrator_tts_service.py`
- TTS cache theo provider/model/voice/text/speed.
- Chỉ scene có single speaker `NARRATOR` mới được xử lý.
- Speech window lấy từ STT hiện có.
- Audio Veo được mute trong speech window rồi mix TTS cố định.
- Video stream hash trước/sau phải giống nhau.
- Overlay phải qua STT, audio metrics và speaker embedding.
- Voice profile narrator lưu `provider_voice_id=confident-male-vietnamese`.

### Junction

- **14/14 PASS**.
- Junction stale chỉ được deterministic restore khi:
  - hai scene APPROVED,
  - selected media IDs hợp lệ,
  - media QC hợp lệ,
  - boundary evidence tồn tại,
  - prior QC/evidence đúng version và PASS.

### Master QC audio-gap

Master QC không còn coi tổng mọi pause ngắn là một audio gap.

- Vẫn lưu tổng silence để audit.
- Chỉ báo `AUDIO_GAP` khi có một khoảng silence liên tục >= **0.75 giây**.
- Pause narrator hiện tại max **0.414 giây**, nên không bị false-positive.

## 5. Dịch vụ vận hành

Port TH Media:

- Backend: `127.0.0.1:8012`
- Flow Bridge: `127.0.0.1:8765`
- Flow Chrome CDP: `127.0.0.1:9223`

Không kill/restart rộng theo tên process `python`, `node` hoặc `chrome`. Luôn xác định PID theo port.

Không tự ý đụng các port deploy khác:

- 8000
- 8080
- 8081
- 8088

### Health acceptance

`GET /api/ready` phải giữ:

- DB healthy
- Event store healthy
- Flow configured
- Flow authenticated
- Capability matrix fresh
- Speaker verifier ready
- `ready=true`

## 6. Flow troubleshooting

Khi Flow lỗi, phân biệt rõ:

- `SESSION_EXPIRED`
- `PROJECT_NOT_FOUND`
- `CAPABILITY_MISMATCH`
- `FLOW_UI_CHANGED`
- `FLOW_POLICY_BLOCKED`
- `GENERATION_TIMEOUT`

Không kết luận prompt lỗi khi automation chưa qua selector/model/settings. Với policy tile, ghi nhận đúng lỗi và không bypass policy. Restart Flow Bridge phải reconcile persisted job thành terminal/recovered state, không duplicate provider job.

## 7. Recovery invariants

Sau restart/recovery phải kiểm tra:

- `recovery_state_clean = true`
- `orphans = []`
- `stuck = []`
- `duplicate_risk = false`
- không active lease còn sót
- không duplicate job/media
- scene attempt không giảm
- selected media chỉ đổi qua đường selection chính thức.

## 8. Backup quan trọng

Giữ các checkpoint:

- `.data/backup_post_narrator_tts_v4_20260922_172842.db`
- `.data/backup_pre_final_v4_qc_reconcile_20260922_172358.db`
- `.data/backup_post_acceptance_v3_20260922_064659.db`
- `.data/backup_pre_final_v3_20260922_064229.db`
- `.data/backup_pre_scene15_attempt3_20260922_061824.db`
- `.data/backup_pre_junction_014_015_recheck_20260922_063807.db`

Không xóa final media, acceptance snapshots, render failure history, calibration evidence hoặc TTS evidence.

## 9. Regression / build

Acceptance gần nhất:

- Backend unittest discovery: **207/207 PASS**
- Narrator/Master-QC targeted regression: **21/21 PASS**
- Frontend `npm run build`: **PASS**
- Frontend lint: **0 warnings / 0 errors**.
- Secret/leak audit gần nhất trước release: **clean**

### Frontend code splitting

Film Studio và Video Analyzer được lazy-load theo app mode.

Build sau tối ưu:

- Initial JS: **410.82 KB** (gzip 125.50 KB)
- Film Studio chunk: **121.95 KB** (gzip 31.81 KB)
- Video Analyzer chunk: **14.42 KB** (gzip 4.90 KB)
- CSS: **65.67 KB**

Warning main chunk >500 KB trước đây đã được loại bỏ.

## 10. Diagnostic archive

Các script chẩn đoán/acceptance tạm không bị xóa.

- **127 script** đã được chuyển vào:
  `.data/diagnostics_archive/20260922`
- Giữ tại `.data` root để vận hành:
  - `audit_recovery_compact.py`
  - `probe_flow_bridge_runtime.py`
  - `git_leak_audit.py`
  - `scan_source_secrets.py`

## 11. Definition of Done

- [x] 15/15 scenes APPROVED
- [x] 14/14 Junction PASS
- [x] 15 selected scene videos
- [x] Final Assembly PASS
- [x] Master QC PASS
- [x] Final MP4 có video + audio
- [x] Không missing scene
- [x] Không black-frame gap đáng kể
- [x] Không suspicious audio gap
- [x] Recovery sạch
- [x] Không orphan/duplicate risk
- [x] Backend regression PASS
- [x] Frontend build PASS
- [x] Health/ready PASS
- [x] Post-acceptance backup
- [x] Narrator acoustic identity fully verified
- [x] Frontend main bundle <500 KB
- [x] Frontend lint 0 warnings / 0 errors
- [x] Diagnostic scripts archived, evidence preserved

## 12. P2 còn lại

Không phải production blocker:

1. Duy trì full regression + leak audit trước mỗi release.
2. Khi thêm narrator scene mới, bắt buộc dùng cùng provider voice ID hoặc tạo migration rõ ràng cho voice contract.

---
Tài liệu này không chứa API key, token, cookie hoặc thông tin xác thực.
