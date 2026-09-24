# TH Media Desktop 0.1.2

Bản Windows self-contained đầu tiên vượt đủ Windows Full Release. 0.1.1 chưa từng được phát hành vì build fail ở bước staging runtime.

## Thay đổi so với bản 0.1.1 chưa phát hành

- `prepare_runtime_dependencies.ps1` chạy được hai lần trong một lần build. Bản cũ trả về một chuỗi vô hướng khi chỉ còn một speaker-model candidate, nên ký tự đầu của đường dẫn được dùng làm đường dẫn và toàn bộ release dừng ở bước "Build Tauri executable and release resources".
- Whisper base model giờ bắt buộc đủ `model.bin`, `config.json`, `tokenizer.json` và `vocabulary*` ngay trong staging; thiếu một trong bốn là build fail thay vì đóng gói một model không dùng được.
- Bản cài đặt không còn âm âm tải model từ Hugging Face ở lần chạy đầu. Sidecar đã đóng gói sẽ báo lỗi rõ khi model đi kèm không có trong package.
- Migration dữ liệu legacy sửa được đường dẫn ghi theo dạng tên ngắn kiểu `C:\Users\RUNNER~1\...`. Bản cũ resolve đường dẫn rồi so sánh với chuỗi đã lưu trong database, nên các dòng không khớp bị bỏ qua và migration vẫn báo thành công.
- Flow browser có regression cố định thứ tự ưu tiên: executable người dùng chọn, rồi Google Chrome, rồi Microsoft Edge, và thông báo lỗi nêu cả hai khi máy không có browser nào.
- Installed-package smoke không còn cài và gỡ Setup trước khi cô lập `%LOCALAPPDATA%`, nên không ghi file của lần test vào hồ sơ người dùng thật.
- Windows Full Release không còn "xanh mà không kiểm chứng": clean-machine smoke chạy trên một release root thật, mọi smoke step kiểm tra exit code, và bước publish từ chối phát hành nếu một trong bốn gate (regression, self-contained resources, clean-machine, installed package) chưa báo PASS.

## Đã đóng gói trong Setup.exe

- Python 3.12 runtime và toàn bộ backend/Flow sidecar dependencies.
- FFmpeg + FFprobe.
- Speaker identity ONNX model, xác minh SHA-256 trong release build.
- Faster-Whisper Base model local để Video Analyzer và Film QC không phải tải model từ Hugging Face ở lần chạy đầu.
- WebView2 Offline Installer nhúng thẳng trong Setup.exe, để cài được UI runtime trên máy chưa có WebView2 mà không cần Internet lúc cài.
- Chromium dự phòng cho Google Flow (`runtime\browser\chromium`), chỉ dùng khi máy không có Chrome cũng không có Edge. Làm Setup.exe nặng hơn khoảng 180-200 MB.
- TH Media frontend/Tauri runtime và local SQLite storage.

## Flow browser

- Ưu tiên executable người dùng chọn trong Cài đặt.
- Ưu tiên Google Chrome nếu có.
- Tự động fallback Microsoft Edge nếu Chrome không được cài; người dùng không bắt buộc phải cài thêm Chrome.
- Fallback cuối là Chromium đi kèm trong `runtime\browser\chromium\chrome.exe`, được tải qua đúng phiên bản Playwright mà app bundle, nên Flow vẫn chạy được trên máy không có Chrome lẫn Edge (Windows LTSC, máy đã gỡ Edge).
- Thứ tự trên có regression cho cả bốn tình huống; browser bundle không thay thế trình duyệt người dùng đang dùng.
- Vẫn dùng profile Flow riêng và CDP local loopback như trước.

## GitHub release acceptance

Workflow Windows Release phải PASS trước khi asset được phát hành:

- Backend + Flow unit tests.
- Frontend build + lint.
- Rust tests + cargo check.
- Release script regression (candidate resolution 0 / 1 / nhiều phần tử).
- Runtime dependency staging.
- Tauri executable build.
- NSIS bundle.
- WebView2 offline mode verification.
- Clean-machine runtime smoke.
- Installed Setup.exe smoke: cài silent trong `%LOCALAPPDATA%` cô lập với PATH không có python/node/npm, kiểm tra sidecars/FFmpeg/speaker/Whisper, mở app, tạo DB, dựng Flow sidecar, rồi uninstall và xác nhận Keep Data giữ nguyên database.
- SHA-256 cho Setup.exe.
- Require every release gate to pass.

## Những phần vẫn cần Internet theo bản chất dịch vụ

- OpenAI / Google / Claude / xKiro / OpenRouter API cần Internet và API key của người dùng.
- Google Flow cần Internet và người dùng đăng nhập tài khoản Google.
- Phân tích URL YouTube/TikTok/Facebook cần truy cập Internet tới nền tảng nguồn.

## Authenticode

Workflow hỗ trợ ký Authenticode nếu repository được cấu hình hai GitHub Secrets:

- TH_MEDIA_CODE_SIGN_PFX_B64
- TH_MEDIA_CODE_SIGN_PFX_PASSWORD

Nếu chưa có certificate code-signing tin cậy, Setup.exe vẫn được build/release nhưng trạng thái là `UNSIGNED_NO_CERTIFICATE`. Không dùng self-signed certificate để giả production signing.
