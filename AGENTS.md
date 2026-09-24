# AGENTS.md — TH Media (`tonghop-main`)

Windows-only monorepo: Tauri 2 desktop shell + FastAPI backend sidecar + React/Vite frontend + Flow Bridge sidecar.

## Layout (real entrypoints)

- `backend/app/main.py` — FastAPI app (`/api/*`, serves `frontend/dist` when built). Router: `backend/app/api.py`.
- `backend/app/config.py` — all paths resolve **at import time** from env (`TH_MEDIA_ROOT/DATA_DIR/MEDIA_DIR/TEMP_DIR/DB_PATH/KEY_PATH/FRONTEND_DIST`). Default store is `<repo>/.data/aihub.db` (dev scratch).
- `backend/flow_bridge/app.py` — Flow Bridge sidecar (lazy/on-demand; must be 0 processes at idle).
- `frontend/src/main.tsx`, `api.ts`, `runtime.ts` — UI entry; API base comes from `window.__TH_MEDIA_RUNTIME__`, auth via `X-TH-Media-Token` header.
- `desktop/backend_sidecar.py` / `desktop/flow_bridge_sidecar.py` — PyInstaller entrypoints for the two sidecars.
- `desktop/src-tauri/tauri.conf.json` — `frontendDist: ../../frontend/dist`, `beforeBuildCommand: scripts/prepare_release.ps1`.
- `desktop/scripts/` — only supported way to test/build/package (do not hand-roll PyInstaller/Tauri commands).

## Commands (use these exactly)

```powershell
# Backend — NEVER run `python -m unittest` directly (see DB guard below).
# From repo root; gate auto-discovers app.test_* + flow_bridge.test_* when -Modules omitted:
powershell -NoProfile -ExecutionPolicy Bypass -File desktop\scripts\backend_test_gate.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File desktop\scripts\backend_test_gate.ps1 -Modules app.test_film_pipeline,app.test_film_media

# Frontend (must stay 0 warning / 0 error; linter is oxlint, not eslint)
npm --prefix frontend run lint
npm --prefix frontend run build   # = `tsc -b && vite build`

# Rust shell (run in desktop\src-tauri; create desktop\runtime + desktop\sidecars dirs first if missing)
cargo test --quiet
cargo check --quiet
```

CI order (`windows-ci.yml`): backend gate → frontend build+lint → `test_runtime_dependency_resolution.ps1` + `test_portable_paths.py` + `test_migrate_legacy_data.py` + `test_acceptance_fingerprints.py` → `cargo test` + `cargo check`. Release (`windows-release.yml`) is tag-driven: git tag `vX.Y.Z` **must equal** `tauri.conf.json` version.

## Critical gotchas (agent mistakes to avoid)

- **DB isolation is load-bearing.** `config.py` pins `DB_PATH` at import, so an unisolated test run appends rows to the real store and still reports green (95%+ of `film_pipeline_events` in `.data/aihub.db` was once such pollution). Always go through `backend_test_gate.ps1`, which sets `TH_MEDIA_REQUIRE_DB_ISOLATION=1` (makes `db.connect()` refuse the two production paths: `<repo>/.data/aihub.db` and `%LOCALAPPDATA%\TH Media\Desktop\Database\aihub.db`) and hash-checks both stores before/after (`PROD_DB_UNTOUCHED` + `BACKEND_TEST_GATE` must both PASS). Keep that env **step-scoped** — never job-level, or the installed-app smoke steps can't open their own DB.
- **Two divergent stores.** Product truth = `%LOCALAPPDATA%\TH Media\Desktop\Database\aihub.db`; `.data\aihub.db` is scratch. Never quote a metric without stating which store it was measured on.
- **Backend auth/CORS.** `/api/*` (non-OPTIONS) requires `X-TH-Media-Token` vs `TH_MEDIA_AUTH_TOKEN` (hmac compare; empty expected ⇒ non-desktop-mode passes). CORS allowlist is `tauri://localhost` + `http(s)://tauri.localhost`, plus vite dev origins only when `TH_MEDIA_DESKTOP_MODE=1` + `TH_MEDIA_DEV_SERVER=1`. Every response carries `X-Request-ID`.
- **Version bump = 4 files in lockstep:** `desktop/package.json`, `desktop/src-tauri/tauri.conf.json`, `desktop/src-tauri/Cargo.toml` (+ `Cargo.lock`). Never overwrite an existing release/tag. Current line: `0.1.2`.
- **One-run-per-project invariant.** Canonical generation and pipeline each allow max one active run per project; stop/retry must reuse valid assets, never duplicate workers/jobs or regenerate valid files. Test stop-mid-job, resume/retry, and restart-mid-lifecycle with no orphan jobs.
- **Flow/Chrome idle rule.** No Flow Bridge or Chrome processes at idle; bridge starts lazily. Offline `/api/*` routes requiring internet return `503 INTERNET_OFFLINE`; local project/data routes must keep working.
- **No live credit without owner approval.** Không chạy live Flow/xKiro generation (canonical, Consistency AI Semantic Reviewer, canary/full pipeline) nếu chưa được chủ dự án duyệt credit/provider/model/calls.
- **No direct production DB edits.** Không sửa trực tiếp production DB (`%LOCALAPPDATA%\TH Media\Desktop\Database\aihub.db`); recovery/test phải dùng isolated copy + SQLite backup API, giữ backup path + DB hash + media inventory + timestamp.
- **No media_dir moves.** Không tự di chuyển/xóa `media_dir`. `C:\Users\Admin\Desktop\New folder (2)` hiện là media root chính của bản cài, không coi là legacy.
- **Active phase + release status.** R2 hiện là phase active. Chưa bump 0.1.3 (giữ 0.1.2 trong 4 files lockstep cho tới R5). Chưa có Clean Windows VM. Chưa có OV/EV certificate (không self-signed rồi báo production signed; production tag/release bắt buộc SIGNED).

## Conventions

- Film domain tables live in `backend/app/db.py` (`SCHEMA` + `PIPELINE_SCHEMA` + `BATCH3_SCHEMA` + `BATCH4_SCHEMA`); schema changes require `*_MIGRATIONS` + backward-compat handling (see `init_db()` and `test_migrate_legacy_data.py`), never a destructive rewrite.
- Backend tests are `backend/app/test_*.py` + `backend/flow_bridge/test_*.py`. Tất cả backend tests phải chạy qua `desktop\scripts\backend_test_gate.ps1`. Targeted test cũng phải dùng `-Modules`; không chạy unittest trực tiếp vì có thể ghi vào DB production.
- Frontend: React 19, no test runner; verification is `lint` + `build`. Realtime canonical logs are SSE parsed in `api.ts` (`streamCanonicalEvents`); keep `Accept: text/event-stream` + `data:` block parsing intact.
- Secrets/keys/cookies/tokens, `*.db`, `.data/`, `desktop/{runtime,sidecars,.build,.cleanroom,.e2e-test}/`, acceptance `desktop/*.png` are git-ignored — never commit them, never print them in logs/reports/CI output. `REPORT_*.md` is delivery evidence and stays tracked.
- Live-provider runs burn Flow/xKiro credit and Google Flow login must be done by the user in the app-opened browser (never collect passwords): get explicit approval before any live pipeline/canary run. Per-batch reports go in `REPORT_<BATCH>_<YYYY-MM-DD>.md` with commit hash, commands run, PASS/FAIL + runtime evidence; no evidence = not accepted.
