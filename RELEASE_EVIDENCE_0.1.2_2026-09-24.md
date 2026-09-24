# TH Media 0.1.2 — Release Evidence

Ngày: 2026-09-24
Repo: `0395358803lc-eng/tonghop` · Nhánh `main` · Tag `v0.1.2` → commit `9b5db19`
Tài liệu này thay thế các kết luận release trong `DESKTOP_ACCEPTANCE_REPORT_2026-09-24.md` (xem mục "Hạ cấp kết luận cũ").

## Kết luận hiện tại

**RELEASE CANDIDATE.** Mọi gate kỹ thuật của Windows Full Release đã PASS trên runner sạch; duy nhất bước phát hành GitHub Release fail vì cấu hình quyền của repository, không phải vì sản phẩm.

## Bằng chứng từ CI

### Windows Full Release `v0.1.2` — run 35946753112

https://github.com/0395358803lc-eng/tonghop/actions/runs/35946753112

| Step | Kết quả |
| --- | --- |
| 1-9 Checkout → regression suite | success |
| 10 Optional Authenticode certificate | success (`UNSIGNED_NO_CERTIFICATE`) |
| 12 Build Tauri executable and release resources | **success** |
| 13 Sign before bundle | skipped — không có certificate |
| 14 Bundle NSIS with offline WebView2 | **success** |
| 15 Sign installer | skipped — không có certificate |
| 16 Verify self-contained release resources | **success** |
| 17 Clean-machine runtime smoke | **success** |
| 18 Installed Setup.exe smoke | **success** |
| 19 Create release hashes and status | success |
| 20 Upload workflow artifact | success |
| 21 Require every release gate to pass | success (4/4 gate PASS) |
| 22 Publish or refresh GitHub Release | **failure — `HTTP 403: Resource not accessible by integration`** |

Bốn gate ghi nhận: `TH_MEDIA_GATE_REGRESSION`, `TH_MEDIA_GATE_SELF_CONTAINED`, `TH_MEDIA_GATE_CLEANROOM`, `TH_MEDIA_GATE_INSTALLED` = PASS.

### Windows CI

| Run | Commit | Kết quả |
| --- | --- | --- |
| 35944182676 | `32c64b4` | failure @ step 10 → phơi bày lỗi route-rewrite ở `migrate_legacy_data.py` |
| 35944823134 | `11b6b06` | success |
| 35946118762 | `9b5db19` | success |
| 35947935601 | `4c64dd9` | success |

Số đo được trên runner sạch (`35944823134`, `35947935601` cùng bộ test):

- Backend + Flow Bridge: **272 tests OK**
- Release-script regression (PowerShell): **17/17 PASS**
- Legacy migration: **5 tests OK**
- Portable paths: **1 test OK**
- Acceptance measurement helpers: **10 tests OK**
- Rust: **5 passed**, `cargo check` OK
- Frontend build OK, lint **0 warnings / 0 errors**

## Artifact đã build

Workflow artifact `TH-Media-0.1.2-Windows` (id 10787628030), zip 566.861.407 byte, gồm đúng ba file:

- `TH Media_0.1.2_x64-setup.exe` — **566.707.072 byte**
- `TH Media_0.1.2_x64-setup.exe.sha256`
- `SIGNING_STATUS.txt`

SHA-256 tính lại độc lập từ file tải xuống, khớp với file `.sha256`:

```
b817f68ec21f8fde8dffeb6e8572226ae8f4a79db1ae2669f339782bdf46e5c0
```

`SIGNING_STATUS.txt`:

```
TH Media 0.1.2
Authenticode=UNSIGNED_NO_CERTIFICATE
WebView2=offlineInstaller
WhisperBase=bundled
FlowBrowser=Chrome_or_Edge
SHA256=b817f68ec21f8fde8dffeb6e8572226ae8f4a79db1ae2669f339782bdf46e5c0
```

## Nguyên nhân gốc của chuỗi release fail trước đây

`prepare_runtime_dependencies.ps1` được gọi hai lần trong một lần build (`build_backend.ps1` rồi `build_flow_bridge.ps1`). Lần hai chỉ còn đúng một speaker-model candidate, và `@(...) | Where-Object` trả về **String vô hướng**, nên `$SpeakerCandidates[0]` cho ra ký tự `'C'`:

```
Resolve-Path : Cannot find path '...\tonghop-main\C' because it does not exist.
```

Bốn run `v0.1.1` (35937389303, 35938171573, 35938917552, 35940001001) đều chết ở step 12 và **skip 9 step phía sau**, nên chưa từng có GitHub Release nào cho 0.1.1. Bản vá chuyển logic chọn candidate sang `runtime_dependency_resolution.ps1` và trả kết quả bằng `return ,$found` để luôn là array thật; regression 17 case chứng minh nó bắt đúng lỗi cũ (mutation test: bỏ sửa → `type=String`, `[0] = C`).

## Những lỗi thật khác đã sửa trong đợt này

Mỗi mục dưới đây đều do CI hoặc một phép đo thật tìm ra, không phải đọc code suy đoán:

1. **Staging không idempotent** — như trên.
2. **Whisper bundle thiếu tokenizer/vocabulary vẫn lọt** — build gate và installed smoke nay bắt buộc `model.bin` + `config.json` + `tokenizer.json` + `vocabulary*`.
3. **Bản cài có thể tự tải model từ Hugging Face** — `whisper_model_path()` trả tên `"base"` vô hướng khi không thấy model cục bộ; sidecar đóng gói (`sys.frozen`) nay báo lỗi rõ thay vì tải âm thầm lúc chạy.
4. **`migrate_legacy_data.py` không rewrite đường dẫn ghi theo tên ngắn** — `migrate()` resolve root rồi so với chuỗi trong DB; trên runner `tempfile` trả `C:\Users\RUNNER~1\...` nên `replace()` không khớp và **0 dòng được cập nhật trong khi migration vẫn báo thành công**. Nay giữ cả chuôi chưa resolve làm alias. CI run 35944182676 là bằng chứng.
5. **Clean-machine smoke chắc chắn fail ở step 17** — script đòi `target/release/runtime` và `target/release/sidecars`, nhưng `tauri build --no-bundle` không sinh ra hai thư mục đó (kiểm tra trên bản build thật). Workflow giờ stage một release root rồi truyền `-ReleaseRoot`.
6. **Step smoke có thể đỏ mà step vẫn xanh** — các bước `powershell -File ...` không kiểm tra `$LASTEXITCODE`. Nay kiểm tra tường minh, cộng thêm gate "Require every release gate to pass".
7. **Installed smoke ghi vào hồ sơ người dùng thật** — cài và gỡ Setup trước khi cô lập `%LOCALAPPDATA%`, khiến hook NSIS chép bản sao installer vào `TH Media\Desktop` đang dùng. Đã đảo thứ tự.
8. **Flow probe tìm listener sai process** — PyInstaller launcher có thể trao socket cho process con. Probe nay quét cả cây process.
9. **Test PortablePaths đỏ từ trước** — `performance_acceptance.ps1` và `security_runtime_acceptance.ps1` hardcode `C:\Users\Admin\Desktop\tonghop-main\...`; đã chuyển về `$PSScriptRoot`. Hai test `desktop/test_*.py` trước đây chưa từng được CI chạy; nay nằm trong cả hai workflow.
10. **Thiết bị đo nghiệm thu đã mất** — `restart_acceptance.ps1` và `update_acceptance.ps1` đòi ba helper trong `desktop/.build/` (thư mục gitignore) chưa từng được track và không có gì sinh ra chúng. Đã dựng lại trong `desktop/scripts/acceptance/` kèm 10 test. Khi viết test lập tức phát hiện lỗi thứ hai: in JSON qua stdout cp1252 **crash trên Windows tiếng Việt** vì tên project/stage có dấu; cả ba helper nay emit UTF-8.
11. **Quyền publish chỉ lộ ra sau 55 phút build** — thêm step 10 "Verify release publishing is permitted" dùng POST không `tag_name` (422 = được phép, 403 = không) để fail sau 2 phút. Đã kiểm chứng semantics: token có quyền ghi trả về 422.

## Bằng chứng cục bộ bổ sung

Chạy trên máy Windows thật, không dùng cache của developer (`LOCALAPPDATA`/`USERPROFILE` trỏ vào thư mục rỗng):

- `prepare_runtime_dependencies.ps1` chạy hai lần liên tiếp: lần 1 exit 0 (72 s, tải speaker 39.593.761 B + Whisper base), lần 2 exit 0 (3 s). Cây staged: `bin/ffmpeg.exe` 227.398.656 B, `bin/ffprobe.exe` 227.193.344 B, speaker ONNX, `whisper/base/{model.bin 145.217.532 B, config.json, tokenizer.json, vocabulary.txt}`.
- Script bản cũ chạy trên đúng trạng thái đó: **exit 1**, `Resolve-Path : Cannot find path '...\C'`.
- `installed_package_smoke.ps1` trên installer thật: `TOOLS_VISIBLE={"python":false,"node":false,"npm":false}`, backend port 58799 HTTP 401, **Flow sidecar tự khởi động** port 58825 HTTP 401, `WHISPER_MB=138.5`, uninstall Keep Data `TREE_STABLE=True`, `DB_STABLE=True`, `INSTALLER_SMOKE=PASS`.
- Thăm dò uninstall độc lập: cài 1138 file / 1.130,9 MB → `uninstall.exe /S` exit 0 → **thư mục cài bị xoá hoàn toàn**.
- NSIS sinh thật trên máy: `!define INSTALLWEBVIEW2MODE "offlineInstaller"` và `WEBVIEW2INSTALLERPATH` trỏ tới `MicrosoftEdgeWebView2RuntimeInstallerX64.exe` được nhúng; kích thước Setup tăng 403.954.833 → 639.879.099 byte so với bản `downloadBootstrapper`. File `.nsi` cũ ghi `downloadBootstrapper` là artifact sinh **trước** commit thêm `offlineInstaller` (05:37 so với 06:32), không phải lỗi cấu hình.
- Repo hygiene: 224 file được track, **0** file `.exe/.dll/.onnx/.bin/.pfx/.p12/.key/.pem/.zip`; secret scan 10 họ mẫu trả 3 hit, tất cả là token GUID sinh lúc chạy test.

## Hạ cấp kết luận cũ

`DESKTOP_ACCEPTANCE_REPORT_2026-09-24.md` ghi `P2.34 Restart acceptance — PASS` và `P2.35 Update A -> B acceptance — PASS`. Với trạng thái repository hiện tại, **hai kết luận đó không tái lập được**: công cụ đo của chúng chưa từng nằm trong git và đã biến mất cùng `desktop/.build/`. Từ nay thiết bị đo đã được dựng lại và có test, nên hai gate này phải chạy lại và tính là số mới, không phải số lịch sử.

Báo cáo cũ cũng mô tả 0.1.1 như một bản sắp phát hành; thực tế 0.1.1 chưa bao giờ build xong trên CI và chưa từng có Release.

## Gate còn mở

| Gate | Trang thái | Chặn ở đâu |
| --- | --- | --- |
| Publish GitHub Release 0.1.2 | NOT RUN | `default_workflow_permissions = "read"` → 403. Cần đổi sang "Read and write permissions" ở Settings → Actions → General. |
| Authenticode production | NOT RUN | Chưa có certificate OV/EV. Self-signed không được tính. |
| Defender scan sau ký | NOT RUN | Phụ thuộc gate ký. |
| Cài WebView2 offline, ngắt mạng | NOT RUN | Cần Windows VM/máy sạch không có WebView2. |
| Full E2E trên Windows sạch | NOT RUN | Cần máy sạch + API key thật + tài khoản Google Flow. |
| Update 0.1.0 → 0.1.2 | NOT RUN | Hết chặn dụng cụ (helper đã dựng lại), cần artifact 0.1.2 từ Release và một Flow session đã đăng nhập. |
| Restart acceptance | NOT RUN | Như trên, cần chạy lại bằng helper mới. |

Không tự gọi đây là bản production hoàn chỉnh cho tới khi các dòng trên có bằng chứng runtime.
