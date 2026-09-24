# TH Media Desktop — Acceptance Report
Date: 2026-09-24
Project: C:\Users\Admin\Desktop\tonghop-main\tonghop-main

> SUPERSEDED for release status by `RELEASE_EVIDENCE_0.1.2_2026-09-24.md`.
> P2.34 (restart) and P2.35 (update A -> B) below are NOT reproducible from this
> repository: their measurement helpers lived in gitignored `desktop/.build/`,
> were never tracked, and are gone. They must be re-run with the rebuilt helpers
> in `desktop/scripts/acceptance/` before being counted again. 0.1.1 never
> finished a Windows Full Release and no GitHub Release existed for it.

## Executive status
Release candidate source is technically clean and production installer 0.1.0 builds successfully.
All automated regression, restart, update, performance, security and uninstall acceptance items executed on the current Windows server pass.
One strict acceptance item remains external: full AI/Flow/image/video/QC/final workflow on a truly clean Windows VM or physical machine that has never had the source tree or development toolchain.

## P2.29 — Resource management — PASS
- Resource preflight wired before render.
- Temp quota cleanup implemented.
- Selected/final media protected.
- Media root can move to another drive.
- Legacy media roots remain readable.
- Regression coverage added.

## P2.30 — Desktop Settings — PASS
- Autostart.
- Minimize to tray.
- Vietnamese language setting.
- Media location.
- Temp quota.
- Log size/retention.
- Backup retention.
- Chrome executable.
- Flow profile/session paths.
- Default video model/aspect ratio/resolution.
- Advanced runtime ports and diagnostics.
- Backup/restore with staged restore before sidecar startup.

## P2.31 — Offline/online handling — PASS
- Local project/database access remains available offline.
- Remote AI/Flow/video routes return INTERNET_OFFLINE before mutating remote state.
- Pipeline pauses on network loss without consuming retry.
- Resume clears offline error and continues after network recovery.
- UI shows Internet state and distinguishes offline from Flow/Chrome errors.

## P2.32 — Multi-project isolation — PASS
- New media layout: Media\projects\<project_id>\...
- Flow images/videos, thumbnails, assets and final outputs are project-scoped.
- Legacy paths remain readable.
- Project deletion offers keep-media / delete-media behavior.
- Isolation regression proves deleting project A does not remove project B media.

## P2.33 — Clean-machine acceptance — PARTIAL / EXTERNAL FINAL CHECK REQUIRED
Automated clean-room acceptance on the server:
- PATH sanitized: python=false, node=false, npm=false.
- Isolated LOCALAPPDATA.
- Main desktop EXE starts.
- Packaged backend sidecar starts.
- SQLite database is created.
- Backend binds loopback only.
- FFmpeg/ffprobe are bundled.
- Flow and Chrome remain lazy and do not start at idle.
- Latest clean-room startup: ~2.54 s.
- Latest clean-room idle working set: ~130 MB.

Additional installed-package evidence:
- Packaged Flow sidecar authenticated successfully against an isolated cloned Flow profile/session.
- Installed package preserved provider configuration and Flow session in A -> B update acceptance.

Not yet executed:
- A complete new-project external workflow on a separate truly clean Windows VM/physical machine:
  AI provider -> analysis -> image generation -> scene video generation -> QC -> Final Assembly -> Master QC.
Reason: Windows Sandbox is disabled on this server; enabling it requires OS feature change/restart and could disrupt other deployed services.

## P2.34 — Restart acceptance — PASS
Two real restart cycles:
- Stable durable fingerprint across both restarts.
- Project IDs preserved.
- Selected media preserved.
- Render queues preserved.
- Final renders preserved.
- Flow session preserved.
- Flow remained authenticated.
- Graceful shutdown passed both cycles.
- Runtime Flow secret rotation was identified and correctly excluded from durable-state fingerprint.

## P2.35 — Update A -> B acceptance — PASS
Test versions:
- A = 0.1.0
- B = 0.1.1
- Same isolated install location and cloned real user data.

Verified unchanged across update:
- Database durable fingerprint.
- Provider-key encrypted hash/metadata.
- Project state.
- 34 selected media.
- 4 final renders.
- Flow saved session.
- Flow authenticated after B.
- Narrator/TTS artifacts.
- Speaker calibration artifacts and 3 voice profiles.
- Final-film artifacts.

Backward-compatibility issue caught during acceptance:
- Historical media under legacy data-root directories was not readable in the older A installer.
- Current source already contains legacy-root fallback regression.
- Fixed B returned HTTP 200 for the same legacy media file.
- Rust regression: configured_media_paths_include_historical_data_root_without_settings PASS.

## P2.36 — Performance — PASS
Three launch runs:
- Backend ready: 1828 ms / 1515 ms / 1510 ms.
- Average: 1618 ms.
- Maximum: 1828 ms.
- Max idle working set in performance suite: 441.4 MB.
- Flow child count at idle: 0.
- Chrome child count at idle: 0.
- FilmStudio and VideoAnalyzer remain separate lazy frontend chunks.
- Speaker ONNX extractor remains lazy-loaded.

## P2.37 — Security acceptance — PASS
- Desktop backend binds 127.0.0.1 only.
- Missing token -> 401.
- Wrong token -> 401.
- Valid runtime token -> 200.
- Desktop CORS limited to Tauri internal origins.
- Runtime token generated randomly.
- Tracked-source secret scan: 0 hits across 176 tracked files.
- Frontend bundle secret scan: 0 hits.
- Runtime log Cookie/Bearer-header scan: 0 hits.
- Acceptance data directories are Git-ignored.

## P1.27 — Production Authenticode code signing — BLOCKED BY EXTERNAL CREDENTIAL/TOOLING
Current production artifacts are not Authenticode-signed:
- th-media-desktop.exe: NotSigned.
- th-media-backend.exe: NotSigned.
- th-media-flow-bridge.exe: NotSigned.
- TH Media_0.1.0_x64-setup.exe: NotSigned.

Signing scripts are implemented, but the current server has:
- no trusted OV/EV code-signing certificate with private key in CurrentUser/LocalMachine certificate stores,
- no TH_MEDIA code-signing environment configuration,
- no signtool.exe installed.

Do not substitute a self-signed certificate and call it production signing. Final public release signing requires a trusted code-signing certificate and Windows SDK Signing Tools.

## P1.28 — Windows Defender/AV compatibility — PASS
- Microsoft Defender Antivirus enabled.
- Real-time protection enabled.
- Production NSIS installer scanned.
- Backend sidecar directory scanned.
- Flow Bridge sidecar directory scanned.
- No new threat detections.
- UPX disabled.

## P2.38 — Release Definition of Done — RELEASE CANDIDATE / EXTERNAL RELEASE GATES OPEN
Verified:
- Production NSIS installer builds: TH Media_0.1.0_x64-setup.exe.
- Installer installs successfully with exit code 0.
- Desktop app does not require npm/python/uvicorn to run.
- Runtime ports are dynamic and internal.
- No Cloudflare/ngrok is required for local operation.
- Flow session capability is proven in packaged sidecar acceptance.
- Real project evidence shows consistency/production gate PASS.
- Real pipeline evidence includes completed pipeline runs.
- Real final evidence includes final render v4 APPROVED.
- Restart recovery PASS.
- A -> B update PASS.
- Uninstall PASS.
- Backend/frontend/Rust regression clean.
- Security/leak audit clean.

Latest full regression:
- Python backend + Flow Bridge: 261/261 tests PASS.
- Frontend build PASS.
- Frontend lint: 0 warnings / 0 errors.
- Rust unit tests: 5/5 PASS.
- cargo check PASS.

Uninstall default Keep Data acceptance:
- Production uninstall exit code 0.
- TH Media registry entry removed.
- Desktop executable/uninstaller removed.
- Data root and aihub.db preserved.
- Test dataset contained a real DB copy plus representative Flow/media/narrator/speaker/final files.
- Pre/post file count: 9 -> 9.
- Pre/post tree SHA-256 identical:
  edc05e328487dce657e70babdeb4679e6976375873a3c2d8d589daa0f82787be

Real project evidence at acceptance time:
- film_projects: 2.
- film_generated_media: 52.
- selected media: 34.
- film_render_jobs: 56.
- film_pipeline_runs: 33.
- film_final_renders: 4.
- latest relevant pipeline run: completed.
- main project final_gate: true.
- main project gate_status: READY_TO_RENDER.
- latest final render v4: APPROVED.
- provider configured count: 1.
- Flow saved session count: 1.

## Remaining release gates
### Gate A — Clean Windows external E2E
Run one full end-to-end acceptance on a separate clean Windows VM/physical machine:
1. Install TH Media Setup.exe.
2. Configure/test AI provider.
3. Login Google Flow.
4. Create a new small test project.
5. Analyze script with real AI.
6. Generate at least one image.
7. Generate at least one scene video.
8. Run QC.
9. Final Assembly.
10. Master QC.
11. Restart and verify state remains.
12. Uninstall with Keep Data and verify project remains.

### Gate B — Production Authenticode signing
1. Install Windows SDK Signing Tools / signtool.exe.
2. Import or connect a trusted OV/EV code-signing certificate with private key.
3. Configure the TH_MEDIA signing thumbprint/timestamp settings used by the existing signing scripts.
4. Sign and verify:
   - TH Media installer,
   - th-media-desktop.exe,
   - th-media-backend.exe,
   - th-media-flow-bridge.exe.
5. Re-run Defender acceptance on signed artifacts.

Do not mark the full release as final-production PASS until both external gates succeed.
