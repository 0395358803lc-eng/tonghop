# TH Media — Báo cáo bàn giao công việc còn lại cho nhân viên kỹ thuật

**Ngày lập:** 24/09/2026  
**Dự án:** `C:\Users\Admin\Desktop\tonghop-main\tonghop-main`  
**Vai trò từ thời điểm này:** Nhân viên kỹ thuật trực tiếp triển khai. ChatGPT chỉ làm kiến trúc sư/QA, đọc diff, kiểm tra bằng chứng và nghiệm thu PASS/FAIL.  
**Nguyên tắc:** Không đánh dấu hoàn tất chỉ vì code build được. Mỗi hạng mục phải có bằng chứng runtime/thực tế theo Acceptance Criteria bên dưới.

---

## 1. Baseline đã được nghiệm thu

Các hạng mục dưới đây **không làm lại từ đầu** nếu không có regression:

- Frontend: `oxlint = 0 warning / 0 error`, production build PASS.
- Backend targeted regression mới nhất: **112/112 PASS**.
- Tauri Rust: **7/7 tests PASS**, `cargo check` PASS.
- Canonical Step 3 đã có:
  - realtime log,
  - Stop,
  - Retry failed,
  - gallery media thật,
  - bảng trạng thái từng resource: Generation / Provider / QC / File / Error,
  - export JSON chẩn đoán theo project,
  - mở `film_assets/<project_id>`,
  - resource không biến mất khi project chuyển sang `needs_repair`.
- Flow health không còn spam `/api/flow/test` mỗi 5 giây khi chưa đăng nhập.
- Narrator voice identity đã đọc calibration persistent; narrator canonical đã calibrated trên project acceptance.
- Session-expiry giữa job đã có runtime acceptance 409/401 đúng contract.
- Runtime source sau cleanup: mặc định mở **Chat AI**, một desktop + một backend, Flow Bridge lazy/on-demand.

### Quy tắc nguồn sự thật (chủ dự án chốt 24/09/2026)

Store của **sản phẩm** là `%LOCALAPPDATA%\TH Media\Desktop\Database\aihub.db` — bản cài đặt đang chạy.
`.data\aihub.db` trong cây dev chỉ là **scratch**. Mọi bảng số liệu trong báo cáo nghiệm thu phải có
cột "đo trên store nào", vì hai store đã phân kỳ thật sự (ngày 24/09 bản cài có project
`795f6d2b` *Đĩa than chiều muộn* `needs_repair`, bản dev thì không).

Số đo đã xác minh **khớp cả hai store** nên an toàn để trích dẫn: `film_render_jobs`
17 completed / 39 failed và `film_pipeline_runs` 9 completed / 24 failed. Số đo **khác nhau** giữa
hai store thì không được trích dẫn tự do: `film_pipeline_events` (21.108 dòng ở dev, 11.745 ở bản cài).

### Project acceptance quan trọng

**Project:** `273b8bf6-469e-45f5-994f-53598b126c9b` — *Đồng hồ dừng lúc bảy giờ*  
Đã có full Film Studio acceptance lịch sử: 15/15 scene APPROVED, 14/14 junction PASS, final master QC APPROVED, narrator calibrated.

**Project:** `795f6d2b-990d-41df-a80b-bc68a637fd59` — *Đĩa than chiều muộn*  
Hiện trạng DB: `status=needs_repair`, stage đang chờ Consistency V2 Rule + AI; DB vẫn giữ **8 canonical resources**. Không được xóa/recreate toàn bộ asset nếu resource hợp lệ vẫn còn.

---

## 2. Cảnh báo release hiện tại

Source đang mang version:

- `desktop/package.json = 0.1.2`
- `desktop/src-tauri/tauri.conf.json = 0.1.2`
- `desktop/src-tauri/Cargo.toml = 0.1.2`

Nhưng **artifact Setup 0.1.2 cũ đã stale so với source hiện tại**, vì sau khi artifact đó được build đã có thêm narrator API/calibration, canonical P1 UI, diagnostics, Tauri folder command, Flow polling fix và resource-state fix.

### Quy định release

**Không publish lại artifact 0.1.2 cũ làm bản cuối.**

Khuyến nghị tạo release tiếp theo là **0.1.3**. Nếu release manager chọn version khác thì phải đồng bộ version ở toàn bộ manifest trước build và tuyệt đối không overwrite release/tag đã tồn tại.

---

# 3. P0 — Công việc bắt buộc trước khi gọi là Production Release

## P0.1 — Đóng baseline Git sạch trước khi tiếp tục

### Nhân viên phải làm

1. Kiểm tra:
   - `git status --short`
   - `git diff --stat`
   - `git diff`
2. Phân loại:
   - source thật cần commit,
   - test mới,
   - tài liệu,
   - screenshot/evidence tạm không được commit,
   - runtime data / cookie / Flow profile / database không được commit.
3. Xóa các ảnh acceptance tạm còn sót trong `desktop/` nếu không phải evidence chính thức.
4. Kiểm tra `.gitignore` vẫn chặn:
   - database/runtime data,
   - Flow profile/session,
   - logs,
   - cookies/tokens,
   - build staging.
5. Chạy secret scan hiện có.
6. Commit thành các commit logic rõ ràng, không gom hàng trăm file không liên quan.

### Acceptance PASS

- `git status` sạch sau commit, ngoại trừ file runtime/build đã được ignore hợp lệ.
- Không có API key/token/cookie/password trong tracked files.
- Không mất các thay đổi narrator/canonical/P1 vừa nghiệm thu.
- Gửi lại:
  - commit hash,
  - `git status --short`,
  - `git log -5 --oneline`,
  - secret-scan result.

---

## P0.2 — Khôi phục project “Đĩa than chiều muộn” từ needs_repair

### Mục tiêu

Không để project có resource thật nhưng UI/workflow bị kẹt ở `needs_repair`.

### Nhân viên phải làm

1. Backup DB + project media trước sửa.
2. Đăng nhập Google Flow bằng chính UI/browser do ứng dụng mở; **không thu thập Google password**.
3. Chạy Consistency V2 **Rule + AI thật**.
4. Với lỗi được phát hiện:
   - dùng auto-repair có kiểm soát,
   - không phá Story Bible,
   - không thay ID character/location/prop,
   - không phá narrator/speaker calibration,
   - không sửa scene đã PASS nếu không cần.
5. Re-run consistency cho tới khi gate đạt.
6. Đọc 8 canonical resources hiện có.
7. Retry **chỉ resource failed/blocked/missing**.
8. Resource có file hợp lệ phải được reuse/dedupe; không tự động tạo lại chỉ vì retry batch.
9. Chạy canonical QC và lock khi đủ điều kiện.

### Acceptance PASS

- Project rời `needs_repair` theo đúng workflow.
- Consistency Rule + AI có bằng chứng request/runtime, không mock.
- Canonical resource count vẫn đúng Story Bible.
- Không duplicate generation vô ích.
- Không mất file đã lưu.
- Các resource cần cho render đạt QC/lock.
- Gửi:
  - before/after project status,
  - consistency report,
  - resource diagnostic JSON,
  - danh sách retry,
  - screenshot UI Step 3/4,
  - DB backup path + hash.

---

## P0.3 — Controlled AUTO PIPELINE live acceptance

**Đây là hạng mục có thể tiêu Flow/xKiro credit. Chỉ chạy sau khi chủ dự án xác nhận cho phép.**

### Phase A — Canary nhỏ

1. Tạo project mới 2–3 scene.
2. AI Analyze thật.
3. Canonical images thật.
4. QC + lock.
5. Tạo scene video.
6. Kiểm tra realtime log/media xuất hiện ngay.
7. Test Stop giữa job.
8. Resume/retry đúng phần lỗi.
9. Xác nhận one-run-per-project, không duplicate worker/job.

### Phase B — Pipeline đầy đủ

Sau Canary PASS:

1. Chạy project dài hơn.
2. Kiểm tra continuity N → N+1.
3. Audio/dialogue/TTS/speaker acceptance.
4. Junction QC.
5. Final Assembly.
6. Master QC.
7. Restart ứng dụng giữa vòng đời project rồi tiếp tục.
8. Xác nhận không orphan job.

### Acceptance PASS

- Không có 2 canonical run/pipeline run active cùng project.
- Stop không tạo duplicate.
- Retry không tạo lại asset đã hợp lệ.
- Session hết hạn phải fail/pause bằng mã lỗi có nghĩa.
- Final render APPROVED.
- Media/log/state tồn tại sau restart.
- Gửi:
  - project ID,
  - run IDs,
  - start/end timestamps,
  - before/after process tree,
  - event log,
  - selected media IDs,
  - final file + ffprobe summary,
  - ảnh UI các bước chính.

---

## P0.4 — Build release mới, khuyến nghị 0.1.3

### Bắt buộc đồng bộ version

Đồng bộ ít nhất:

- `desktop/package.json`
- `desktop/src-tauri/tauri.conf.json`
- `desktop/src-tauri/Cargo.toml`
- `Cargo.lock` nếu Cargo cập nhật metadata.

### Runtime phải được đóng gói

Kiểm theo script hiện có, không copy thủ công thiếu kiểm soát:

- desktop EXE,
- backend sidecar,
- Flow Bridge sidecar,
- FFmpeg + ffprobe,
- browser fallback theo contract Chrome → Edge → bundled browser,
- Whisper model,
- speaker model,
- WebView2 offline installer,
- PoT provider cho yt-dlp,
- frontend dist,
- mọi DLL/runtime dependency cần thiết.

### Script có sẵn

- `desktop/scripts/prepare_runtime_dependencies.ps1`
- `desktop/scripts/build_backend.ps1`
- `desktop/scripts/build_flow_bridge.ps1`
- `desktop/scripts/prepare_release.ps1`
- `desktop/scripts/finalize_release.ps1`
- `desktop/scripts/installed_package_smoke.ps1`
- `desktop/scripts/clean_machine_smoke.ps1`

### Acceptance PASS

- Setup chạy không cần Python/Node/npm trên máy đích.
- Backend bind loopback.
- Không khởi động Flow/Chrome lúc idle.
- FFmpeg/ffprobe chạy từ thư mục cài.
- Model offline tồn tại.
- Import audit backend/Flow = 0 failed.
- Installer và các binary có SHA-256.
- Release notes phản ánh đúng thay đổi mới.

---

## P0.5 — Clean Windows external E2E

Đây là release gate bắt buộc và phải chạy trên **VM hoặc máy Windows sạch thật**, không có source tree/toolchain dev.

### Kịch bản bắt buộc

1. Cài Setup mới.
2. Xác nhận không cần Python/Node/npm.
3. Cấu hình AI provider và test thật.
4. Đăng nhập Google Flow.
5. Tạo project nhỏ mới.
6. AI Analyze thật.
7. Tạo tối thiểu 1 canonical image.
8. Tạo tối thiểu 1 scene video.
9. Chạy QC.
10. Chạy Final Assembly.
11. Chạy Master QC.
12. Test audio/TTS/speaker.
13. Restart Windows/app và xác nhận state còn.
14. Uninstall với **Keep Data**, cài lại và xác nhận project còn.
15. Nếu có thể, chạy bằng Windows username có dấu tiếng Việt.

### WebView2/offline sub-gate

Dùng một máy không có WebView2 sẵn:
- ngắt mạng,
- cài Setup,
- WebView2 offline installer phải hoàn tất,
- TH Media phải mở được.

### Acceptance PASS

Không được dùng bất kỳ file nào từ source tree dev để “cứu” runtime.

---

## P0.6 — Installed-package Audio/TTS/Speaker acceptance

Unit test không đủ cho release gate này.

### Nhân viên phải chứng minh trên bản cài

- narrator TTS tạo audio thật,
- mux audio/video thành công,
- lip/dialogue timing trong giới hạn contract,
- STT xác nhận transcript,
- speaker similarity/calibration PASS,
- cùng narrator phải dùng cùng voice contract,
- không silent audio,
- restart vẫn đọc voice profile/calibration cũ.

### Acceptance PASS

- Không dùng audio mock.
- Có media file thật + ffprobe.
- Speaker acceptance và narrator status có evidence.

---

## P0.7 — Restart acceptance + Upgrade 0.1.0 → release mới

Dùng helper đã rebuild trong:

- `desktop/scripts/restart_acceptance.ps1`
- `desktop/scripts/restart_acceptance.py`
- `desktop/scripts/update_acceptance.ps1`
- `desktop/scripts/acceptance/`

### Acceptance PASS

Qua restart/update phải giữ:

- DB durable fingerprint,
- project IDs,
- selected media,
- queues,
- final renders,
- provider config encrypted metadata,
- Flow saved session,
- narrator/TTS artifacts,
- speaker calibration,
- legacy media readability.

Không chấp nhận “mở app được” là đủ.

---

## P0.8 — Authenticode production signing

Hiện production signing vẫn BLOCKED nếu chưa có certificate thật.

### Yêu cầu

1. Cài Windows SDK Signing Tools / `signtool.exe`.
2. Dùng **trusted OV/EV code-signing certificate có private key**.
3. Không dùng self-signed để gọi là production.
4. Cấu hình biến môi trường/thumbprint/timestamp theo script có sẵn.
5. Sign và verify:
   - installer,
   - `th-media-desktop.exe`,
   - `th-media-backend.exe`,
   - `th-media-flow-bridge.exe`.

### Script

- `signing_preflight.ps1`
- `sign_windows.ps1`
- `sign_windows_artifacts.ps1`
- `sign_windows_release.ps1`
- `build_signed_release.ps1`

### Acceptance PASS

`Get-AuthenticodeSignature` cho toàn bộ artifact = Valid và signer chain hợp lệ.

---

## P0.9 — Defender/AV acceptance sau khi ký

Phải chạy **sau Authenticode**, không tái sử dụng kết quả scan unsigned.

Script:

- `scan_windows_defender.ps1`
- `defender_acceptance.ps1`

### Acceptance PASS

- Defender enabled.
- Real-time protection enabled.
- Installer + sidecars + installed directory scan sạch.
- Không thêm exclusion để “lách” scan.

---

## P0.10 — GitHub CI/Release phải xanh và publish được

### Blocker hiện biết

Workflow publish trước đây bị 403 do repository:

`default_workflow_permissions = read`

### Nhân viên phải làm

1. GitHub → Settings → Actions → General.
2. Cho workflow quyền phù hợp để tạo Release/artifact, tối thiểu theo workflow contract.
3. Không cấp quyền rộng hơn cần thiết.
4. Fix CI dependency FFmpeg để không lệ thuộc ngẫu nhiên vào Chocolatey V2 feed:
   - ưu tiên URL/version pinned hoặc cache deterministic,
   - fail-fast với thông báo rõ.
5. Run:
   - `.github/workflows/windows-ci.yml`
   - `.github/workflows/windows-release.yml`
6. Publish release mới.
7. Download artifact **từ GitHub Release**, không dùng file local thay thế cho acceptance release.

### Acceptance PASS

- Tất cả required jobs xanh.
- Release có Setup, checksum, release notes, signing status.
- Download lại artifact từ GitHub và hash phải đúng.

---

# 4. P1 — Nên hoàn tất để bản phát hành “đầy đủ nhất”

## P1.1 — Auto Update

Hiện trạng lịch sử: `createUpdaterArtifacts: false`, chưa có `latest.json`.

Nếu mục tiêu là bản desktop đầy đủ nhất, nhân viên phải:

1. Bật updater artifacts theo Tauri contract.
2. Quản lý updater signing key an toàn.
3. Dùng:
   - `prepare_update_release.ps1`
   - `generate_update_manifest.ps1`
   - `sign_updater_artifact.ps1`
   - `rotate_updater_signing_key.ps1`
4. Tạo `latest.json` và updater artifact.
5. Test release A → B thực tế.

### PASS

- App phát hiện update.
- Download/verify signature.
- Update không mất dữ liệu.
- Roll-forward không cần source/dev tools.

---

## P1.2 — Các edge-case clean-machine còn thiếu

Bắt buộc ghi kết quả riêng cho:

- WebView2 offline trên máy chưa có WebView2.
- Whisper/STT thật khi mất mạng.
- Windows username có dấu tiếng Việt.
- Uninstall “Delete All Data” riêng biệt với Keep Data.
- Scan runtime/install logs để chắc không có Cookie/Bearer/API key.

---

## P1.3 — Dependency/reproducibility

### Việc phải làm

- Pin `bgutil-ytdlp-pot-provider` về version release đã kiểm nghiệm; không để dev/CI chạy version lệch nhau.
- Loại bỏ phụ thuộc download không deterministic trong release CI.
- Re-run import audit:
  - backend,
  - Flow Bridge,
  - yt-dlp plugin discovery.
- Rà PyInstaller warnings; chỉ đóng warning sau khi chứng minh optional/unused hoặc runtime import PASS.

---

## P1.4 — Performance release gate

Trên clean runner/máy sạch:

- backend/runtime ready mục tiêu < 5 giây theo baseline CI hiện có,
- idle RAM không tăng bất thường,
- Flow child count idle = 0,
- Chrome child count idle = 0,
- không leak process sau app close.

Không tối ưu dựa trên máy dev đang đầy ổ đĩa rồi coi đó là production metric.

---

# 5. P2 — Bảo trì sau release

1. Mỗi release phải chạy full regression + leak audit, không chỉ targeted tests.
2. Khi thêm narrator scene mới:
   - dùng cùng provider voice ID,
   - hoặc có migration rõ ràng cho voice contract.
3. Không thay schema/media layout mà không có migration + backward compatibility test.
4. Duy trì tests cho:
   - one-run-per-project,
   - pause/resume,
   - retry,
   - session expiry,
   - media isolation,
   - legacy path fallback,
   - uninstall/update/restart.

---

# 6. Bộ regression tối thiểu trước mỗi lần gửi nghiệm thu

## Frontend

```powershell
npm --prefix frontend run lint
npm --prefix frontend run build
```

PASS hiện tại phải giữ: **0 warning / 0 error**.

## Tauri/Rust

```powershell
cd desktop\src-tauri
cargo test --quiet
cargo check --quiet
```

Baseline hiện tại: **7/7 tests PASS**.

## Backend targeted baseline

Chạy qua gate cách ly DB (thay cho `python -m unittest` trực tiếp — xem ghi chú bên dưới):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File desktop\scripts\backend_test_gate.ps1 -Modules app.test_narrator_api,app.test_film_narrator_tts,app.test_canonical_p0,app.test_flow_bridge_client,app.test_film_batch4,app.test_film_pipeline,app.test_desktop_runtime,app.test_film_resource_cache,app.test_film_media
```

Baseline hiện tại: **112/112 PASS** + `PROD_DB_UNTOUCHED=PASS` + `BACKEND_TEST_GATE=PASS`.

Gate này tồn tại vì `backend/app/config.py` chốt đường dẫn DB **lúc import**, nên trước khi có nó,
mọi lệnh `python -m unittest app.test_*` đều ghi thêm dòng vào `film_pipeline_events` của DB thật:
kiểm kê 24/09 đo được **20.181/21.108 dòng (95,6%)** tham chiếu project không còn tồn tại.
Gate bật `TH_MEDIA_REQUIRE_DB_ISOLATION=1` để `db.connect()` từ chối mở store production
(`app/test_db_isolation_guard.py` chứng minh cả hai chiều: chặn path thật, và vẫn cho phép
test cách ly đúng cách chạy), rồi hash lại DB trước/sau để không thể "xanh" mà vẫn làm nhiễm dữ liệu.
Trong CI, biến env đặt ở **cấp step** — đặt ở cấp job sẽ làm smoke test của bản cài thất bại,
vì sản phẩm phải được mở chính database của nó.

## Release scripts

Tùy batch cần chạy các script tương ứng trong `desktop/scripts/`; không được bỏ qua installed-package smoke, clean-machine smoke, security, Defender, restart/update khi chuẩn bị release.

---

# 7. Mẫu báo cáo bắt buộc nhân viên gửi lại sau mỗi batch

Mỗi batch phải tạo file:

`REPORT_<BATCH>_<YYYY-MM-DD>.md`

với cấu trúc:

```markdown
# Batch
## Mục tiêu
## Commit hash
## File đã thay đổi
## Logic đã thay đổi
## Migration/data impact
## Lệnh test đã chạy
## Kết quả PASS/FAIL
## Runtime evidence
## Screenshot / log / artifact path
## SHA-256 artifact
## Lỗi còn lại
## Rủi ro / rollback
## Việc đề nghị QA nghiệm thu
```

### Không được báo cáo kiểu

- “đã sửa xong” nhưng không có test,
- “PASS” nhưng không có output,
- screenshot UI không có backend evidence,
- dùng mock rồi gọi là live acceptance,
- bỏ qua lỗi provider bằng cách hardcode trạng thái PASS.

---

# 8. Quy tắc thao tác trên máy chủ

1. **Không kill process theo tên chung** nếu chưa xác định PID/process tree của TH Media.
2. Không đụng port/app deploy khác trên máy.
3. Không Docker hóa lại dự án nếu không có yêu cầu mới.
4. Không xóa DB/media thật để test.
5. Trước migration phải backup.
6. Không đưa API key/token/cookie/password vào:
   - source,
   - markdown,
   - screenshot,
   - log,
   - GitHub Actions output.
7. Google Flow login phải do người dùng tự đăng nhập trong browser session.
8. Tác vụ tiêu Flow/xKiro credit phải ghi rõ estimated scope và xin chủ dự án cho phép trước.
9. Không overwrite release/tag/artifact cũ; release mới phải có version/hash mới.
10. Nếu acceptance FAIL: giữ evidence, rollback nếu cần, báo nguyên nhân; không che lỗi.

---

# 9. Thứ tự triển khai đề xuất cho nhân viên

**Batch R1 — Git/Source Hygiene**  
Đóng working tree sạch, secret scan, commit baseline hiện tại.

**Batch R2 — Project Recovery**  
Khôi phục “Đĩa than chiều muộn” + canonical retry có chọn lọc.

**Batch R3 — AUTO PIPELINE Canary**  
2–3 scene, live provider, stop/retry/recovery.

**Batch R4 — AUTO PIPELINE Full Acceptance**  
Pipeline đầy đủ + final/master QC + restart evidence.

**Batch R5 — Release 0.1.3 Packaging**  
Version bump, runtime dependencies, Setup, installed smoke.

**Batch R6 — Clean Windows External E2E**  
WebView2/offline/STT/audio/TTS/full film workflow.

**Batch R7 — Upgrade + Restart**  
0.1.0 → 0.1.3 + durable-state fingerprint.

**Batch R8 — Authenticode + Defender**  
Trusted OV/EV signing rồi AV scan.

**Batch R9 — GitHub Release**  
CI xanh, permissions đúng, artifact publish/download/hash acceptance.

**Batch R10 — Auto Update / P1 Edge Cases**  
Updater artifacts + remaining clean-machine edge cases.

---

# 10. Definition of Done cuối cùng

Chỉ gọi **TH Media Production Release hoàn chỉnh** khi:

- source Git sạch và release commit/tag xác định,
- AUTO PIPELINE live PASS,
- clean Windows full E2E PASS,
- audio/TTS/speaker installed-package PASS,
- restart PASS,
- upgrade PASS,
- WebView2 offline PASS,
- secret/log scan PASS,
- Authenticode Valid trên tất cả production binaries,
- Defender post-sign PASS,
- GitHub CI/Release xanh,
- artifact tải từ Release có SHA-256 đúng,
- Setup mới không phụ thuộc dev machine,
- dữ liệu user sống qua restart/update/uninstall Keep Data,
- không orphan process,
- release notes + acceptance report được đóng băng cùng tag.

---

## Vai trò QA của ChatGPT từ đây

Nhân viên kỹ thuật thực hiện code/build/deploy/test.  
ChatGPT **không tự triển khai tiếp** trừ khi chủ dự án thay đổi chỉ đạo. Khi nhân viên gửi report/commit/artifact, ChatGPT sẽ:

1. đọc diff,
2. đối chiếu checklist,
3. chạy/đọc acceptance độc lập nếu cần,
4. kiểm tra evidence,
5. kết luận từng gate **PASS / FAIL / NEEDS EVIDENCE**,
6. chỉ cho phép chuyển batch khi gate hiện tại đạt.

**Không có evidence = chưa nghiệm thu.**
