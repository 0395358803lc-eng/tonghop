# TH Media Desktop 0.1.1

Bản Windows self-contained hơn cho TH Media.

## Đã đóng gói trong Setup.exe

- Python 3.12 runtime và toàn bộ backend/Flow sidecar dependencies.
- FFmpeg + FFprobe.
- Speaker identity ONNX model, xác minh SHA-256 trong release build.
- Faster-Whisper Base model local để Video Analyzer và Film QC không phải tải model từ Hugging Face ở lần chạy đầu.
- WebView2 Offline Installer để cài được UI runtime khi máy chưa có WebView2 mà không cần tải bootstrapper lúc cài.
- TH Media frontend/Tauri runtime và local SQLite storage.

## Flow browser

- Ưu tiên Google Chrome nếu có.
- Tự động fallback Microsoft Edge nếu Chrome không được cài.
- Vẫn dùng profile Flow riêng và CDP local loopback như trước.

## GitHub release acceptance

Workflow Windows Release phải PASS trước khi asset được phát hành:

- Backend + Flow unit tests.
- Frontend build + lint.
- Rust tests + cargo check.
- Runtime dependency staging.
- Tauri executable build.
- NSIS bundle.
- WebView2 offline mode verification.
- Clean-machine runtime smoke.
- Installed Setup.exe smoke: cài silent, kiểm tra sidecars/FFmpeg/speaker/Whisper, mở app với LOCALAPPDATA sạch, tạo DB/backend, rồi uninstall.
- SHA-256 cho Setup.exe.

## Những phần vẫn cần Internet theo bản chất dịch vụ

- OpenAI / Google / Claude / xKiro / OpenRouter API cần Internet và API key của người dùng.
- Google Flow cần Internet và người dùng đăng nhập tài khoản Google.
- Phân tích URL YouTube/TikTok/Facebook cần truy cập Internet tới nền tảng nguồn.

## Authenticode

Workflow hỗ trợ ký Authenticode nếu repository được cấu hình hai GitHub Secrets:

- TH_MEDIA_CODE_SIGN_PFX_B64
- TH_MEDIA_CODE_SIGN_PFX_PASSWORD

Nếu chưa có certificate code-signing tin cậy, Setup.exe vẫn được build/release nhưng trạng thái là unsigned. Không dùng self-signed certificate để giả production signing.
