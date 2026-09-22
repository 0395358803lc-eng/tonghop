# TH Media — Production Acceptance & Operations Handoff

Ngày nghiệm thu: 22/09/2026
Project ID: `273b8bf6-469e-45f5-994f-53598b126c9b`

## 1. Trạng thái nghiệm thu hiện tại

Hệ thống Film Studio đã đạt trạng thái production acceptance cho pipeline hiện tại:

- Scene: **15/15 APPROVED**
- Junction: **14/14 PASS**
- Snapshot fingerprint mismatch: **0**
- Recovery state: **clean**
- Orphan jobs: **0**
- Stuck jobs: **0**
- Duplicate risk: **false**
- Final Assembly: **APPROVED**
- Master QC: **PASS — 96.3/100**
- Final video: `final_v3.mp4`
- Kích thước: **1280×720**
- Frame rate: **24 fps**
- Video codec: **H.264**
- Audio codec: **AAC**
- Thời lượng: **120.042667 giây**
- Black frames: **0.0 giây**
- Audio gaps: **0.0 giây**
- File size: **46,581,990 bytes**

Final manifest chứa đúng **15 scene theo thứ tự**, sử dụng selected media hiện hành của từng scene.

## 2. Speaker / Voice acceptance

Có **8 scene có speech**.

Kết quả speaker acceptance hiện tại:

- Character speaker đã verified: **4 scene**
- Acoustic reference baseline: **3 scene**
- Unverified: **1 scene — SCENE_015 / NARRATOR**
- Failed: **0**
- Blocked: **0**
- `policy_accepted = true`
- `fully_verified = false`

### Narrator limitation

NARRATOR xuất hiện ở SCENE_007 và SCENE_015. Speaker embedding hiện không tạo được khoảng phân biệt đáng tin cậy:

- narrator calibration status: `ambiguous`
- threshold: **không enforce**
- SCENE_015 raw similarity với narrator reference khoảng **0.149**
- calibration gap narrator âm, nên không được phép hạ threshold để ép PASS.

Quy tắc hiện tại:

- `failed`, `error`, `not_evaluated` → block.
- `calibration_ambiguous`, `calibration_pending` → **UNVERIFIED warning**, không hard-fail scene.
- Không được báo cáo voice continuity là 100% verified khi narrator vẫn UNVERIFIED.

## 3. SCENE_015 — lịch sử sửa lỗi

SCENE_015 được xử lý theo attempt monotonic, không reset history:

- Attempt 0: media cũ đã từng QC pass nhưng prompt policy/speaker contract cũ.
- Attempt 1: Flow render thành công, QC không đạt.
- Attempt 2: Flow UI báo policy false-positive liên quan public figure; detector cũ bỏ lỡ error tile và cuối cùng timeout.
- Attempt 3: render thành công với `Veo 3.1 - Lite [Lower Priority]`; runtime prompt có narrator lock và fictional-character context; sau khi speaker contract được sửa đúng, re-QC PASS **94/100**.

Selected media hiện tại của SCENE_015 là media từ attempt 3.

## 4. Các hardening quan trọng đã triển khai

### Flow Bridge

- Selector video/model có fallback, không phụ thuộc một selector brittle duy nhất.
- Detect `flow-error-tile` mới sau mỗi request.
- Policy tile được classify thành `FLOW_POLICY_BLOCKED`.
- Policy block là terminal render error, không auto-retry mù.
- Runtime provider prompt có `[FICTIONAL_CHARACTER_CONTEXT]` để nói rõ nhân vật là nhân vật hư cấu nguyên bản.
- Flow job history và idempotency key được giữ nguyên.

### Pipeline

- STALE scene không được auto-APPROVE media cũ.
- BLOCKED operator retry có `operator_force_generation`.
- Attempt tăng đơn điệu; không reset attempt.
- Retry không xóa job/media/candidate history.
- Existing completed provider result có thể re-QC mà không submit generation mới.

### Snapshot / migration

- Chỉ `SNAPSHOT_FINGERPRINT_MISMATCH` đủ điều kiện recovery deterministic.
- Recovery yêu cầu media selected, QC và evidence hợp lệ.
- Scene stale vì nguyên nhân khác không được auto-approve.
- Prompt policy sử dụng `policy_accepted` riêng với `fully_verified`, tránh rerender vô hạn khi acoustic calibration chỉ ở trạng thái UNVERIFIED.

### Junction

- 14/14 junction hiện PASS.
- Junction stale do snapshot propagation chỉ được phục hồi deterministic khi:
  - hai scene đều APPROVED,
  - selected media IDs không đổi,
  - media QC vẫn hợp lệ,
  - boundary evidence tồn tại,
  - QC version/evidence cũ vẫn PASS.
- Visual dimensions dùng Vision; audio/dialogue dùng ledger/evidence tương ứng.

## 5. Dịch vụ vận hành

Các port quan trọng của TH Media:

- Backend: `127.0.0.1:8012`
- Flow Bridge: `127.0.0.1:8765`
- Flow Chrome CDP: `127.0.0.1:9223`

Không được kill/restart theo tên process rộng như `python`, `node`, `chrome`.

Trước khi restart phải xác định đúng PID theo port.

Các port deploy khác **không được tự ý đụng tới**:

- 8000
- 8080
- 8081
- 8088

### Health acceptance

`GET /api/ready` hiện trả:

- DB: healthy
- Event store: healthy
- Flow configured: true
- Flow authenticated: true
- Capability matrix fresh: true
- Speaker verifier ready: true
- ready: true

## 6. Flow troubleshooting

Khi Flow lỗi:

1. Kiểm tra session state trước.
2. Phân biệt:
   - `SESSION_EXPIRED`
   - `PROJECT_NOT_FOUND`
   - `CAPABILITY_MISMATCH`
   - `FLOW_UI_CHANGED`
   - `FLOW_POLICY_BLOCKED`
   - `GENERATION_TIMEOUT`
3. Không kết luận prompt gây lỗi nếu automation chưa qua model/settings selector.
4. Với policy tile: ghi nhận đúng lỗi, không bypass policy.
5. Với restart Flow Bridge: persisted active job phải reconcile về terminal/recovered state, không duplicate provider job.
6. Không restart Chrome CDP 9223 trừ khi session thực sự cần phục hồi.

## 7. Recovery invariants

Sau restart/recovery phải kiểm tra:

- `recovery_state_clean = true`
- `orphans = []`
- `stuck = []`
- `duplicate_risk = false`
- không active lease còn sót
- không tạo duplicate render/media
- scene attempt không giảm
- selected media không đổi ngoài đường selection chính thức.

## 8. Backup quan trọng

Các checkpoint cần giữ:

- `.data/backup_post_acceptance_v3_20260922_064659.db`
- `.data/backup_pre_final_v3_20260922_064229.db`
- `.data/backup_pre_scene15_attempt3_20260922_061824.db`
- `.data/backup_pre_junction_014_015_recheck_20260922_063807.db`

Không xóa final media, acceptance snapshots, job history, failure history hoặc speaker calibration evidence khi cleanup.

## 9. Regression / build

Acceptance gần nhất:

- Backend unittest discovery: **199/199 PASS**
- Frontend `npm run build`: **PASS**
- Secret/leak audit: **clean**
- Git working tree: **clean**
- GitHub: local HEAD và `origin/main` cùng commit `bda7c4f` trước khi thêm tài liệu này.

Frontend hiện có một warning không-blocking: main JS chunk sau minify khoảng **544 KB**. Đây là hạng mục tối ưu P2, không phải release blocker.

## 10. Definition of Done

Production pipeline hiện đạt:

- [x] 15/15 scenes APPROVED
- [x] 14/14 Junction PASS
- [x] 15 selected scene videos
- [x] Final Assembly PASS
- [x] Master QC PASS
- [x] Final MP4 có video + audio
- [x] Không missing scene
- [x] Không black-frame gap đáng kể
- [x] Không audio gap đáng kể
- [x] Recovery sạch
- [x] Không orphan/duplicate risk
- [x] Backend regression PASS
- [x] Frontend build PASS
- [x] Health/ready PASS
- [x] Post-acceptance backup tồn tại
- [ ] Narrator acoustic identity fully verified

## 11. Công việc P2 còn lại

Không ảnh hưởng acceptance hiện tại:

1. Nâng narrator voice continuity từ UNVERIFIED sang fully verified bằng nguồn voice/reference ổn định hơn hoặc provider voice ID cố định.
2. Code-split frontend để giảm warning chunk ~544 KB.
3. Gom các diagnostic script trong `.data` vào archive theo ngày; không xóa evidence.
4. Duy trì regression + leak audit trước mỗi release mới.

---
Tài liệu này không chứa API key, token, cookie hoặc thông tin xác thực.
