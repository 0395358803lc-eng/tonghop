#![windows_subsystem = "windows"]

use std::{
    env,
    fs::{self, OpenOptions},
    io::{self, BufRead, BufReader, Read, Write},
    net::{TcpListener, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

#[cfg(windows)]
use std::os::windows::process::CommandExt;
use tauri::{
    menu::{Menu, MenuItem},
    tray::{TrayIconBuilder, TrayIconEvent},
    Manager, RunEvent, WebviewUrl, WebviewWindowBuilder,
};
use uuid::Uuid;
#[cfg(windows)]
use windows_sys::Win32::UI::WindowsAndMessaging::{
    MessageBoxW, IDCANCEL, IDNO, IDOK, IDYES, MB_ICONERROR, MB_ICONINFORMATION,
    MB_ICONWARNING, MB_OK, MB_OKCANCEL, MB_SETFOREGROUND, MB_YESNOCANCEL,
};

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x08000000;
const SPEAKER_MODEL_FILE: &str = "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx";
const DEFAULT_LOG_MAX_BYTES: u64 = 10 * 1024 * 1024;
const DEFAULT_LOG_KEEP: usize = 7;

struct FlowLaunchSpec {
    port: u16,
    exe: PathBuf,
    envs: Vec<(&'static str, String)>,
    log_path: PathBuf,
}

struct FlowRuntimeManager {
    spec: Mutex<Option<FlowLaunchSpec>>,
    children: Arc<Mutex<Vec<Child>>>,
}

struct RuntimeBoot {
    backend_url: String,
    backend_port: u16,
    auth_token: String,
    flow_port: u16,
    flow_key: String,
    flow_spec: FlowLaunchSpec,
    children: Vec<Child>,
}

fn local_app_root() -> io::Result<PathBuf> {
    let base = env::var_os("LOCALAPPDATA")
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "LOCALAPPDATA is unavailable"))?;
    Ok(PathBuf::from(base).join("TH Media").join("Desktop"))
}

fn desktop_settings_path(root: &Path) -> PathBuf {
    root.join("desktop-settings.json")
}

fn default_desktop_settings(root: &Path) -> serde_json::Value {
    serde_json::json!({
        "media_dir": root.join("Media").to_string_lossy().to_string(),
        "legacy_media_dirs": [],
        "language": "vi",
        "backup_keep": 5,
        "minimize_to_tray": true,
        "notifications_enabled": true,
        "chrome_path": "",
        "temp_quota_gb": 10.0,
        "log_max_mb": 10,
        "log_keep": 7,
        "default_video_model": "",
        "default_video_aspect_ratio": "16:9",
        "default_video_resolution": "1080p"
    })
}

fn load_desktop_settings_value(root: &Path) -> serde_json::Value {
    let mut merged = default_desktop_settings(root);
    if let Ok(raw) = fs::read_to_string(desktop_settings_path(root)) {
        if let Ok(saved) = serde_json::from_str::<serde_json::Value>(&raw) {
            if let (Some(target), Some(source)) = (merged.as_object_mut(), saved.as_object()) {
                for (key, value) in source {
                    target.insert(key.clone(), value.clone());
                }
            }
        }
    }
    merged
}

fn save_desktop_settings_value(root: &Path, settings: &serde_json::Value) -> io::Result<()> {
    fs::create_dir_all(root)?;
    let path = desktop_settings_path(root);
    let temp = path.with_extension("json.tmp");
    let payload = serde_json::to_vec_pretty(settings)
        .map_err(|err| io::Error::new(io::ErrorKind::InvalidData, err.to_string()))?;
    fs::write(&temp, payload)?;
    let _ = fs::remove_file(&path);
    fs::rename(temp, path)?;
    Ok(())
}

fn restore_copy(source: &Path, destination: &Path) -> io::Result<bool> {
    if !source.is_file() {
        return Ok(false);
    }
    if let Some(parent) = destination.parent() {
        fs::create_dir_all(parent)?;
    }
    let temp = destination.with_extension("restore.tmp");
    fs::copy(source, &temp)?;
    let _ = fs::remove_file(destination);
    fs::rename(temp, destination)?;
    Ok(true)
}

fn apply_pending_restore(root: &Path) -> io::Result<bool> {
    let marker = root.join("pending-restore.json");
    if !marker.is_file() {
        return Ok(false);
    }
    let payload: serde_json::Value = serde_json::from_slice(&fs::read(&marker)?)
        .map_err(|err| io::Error::new(io::ErrorKind::InvalidData, err.to_string()))?;
    let backup_raw = payload
        .get("backup_dir")
        .and_then(|item| item.as_str())
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "pending restore thiếu backup_dir"))?;
    let backup = fs::canonicalize(PathBuf::from(backup_raw))?;
    let backup_root = fs::canonicalize(root.join("Backups"))?;
    if backup.parent() != Some(backup_root.as_path()) {
        return Err(io::Error::new(io::ErrorKind::PermissionDenied, "backup restore nằm ngoài thư mục Backups"));
    }
    let database_dir = root.join("Database");
    let db_source = backup.join("aihub.db");
    if !db_source.is_file() {
        return Err(io::Error::new(io::ErrorKind::NotFound, "backup không có aihub.db"));
    }
    restore_copy(&db_source, &database_dir.join("aihub.db"))?;
    for name in ["master.key.dpapi", "master.key", "flow_sessions.json"] {
        let _ = restore_copy(&backup.join(name), &database_dir.join(name))?;
    }
    for name in ["desktop-settings.json", "flow_bridge_config.json"] {
        let _ = restore_copy(&backup.join(name), &root.join(name))?;
    }
    fs::remove_file(marker)?;
    fs::write(
        root.join("restore-last.json"),
        serde_json::to_vec_pretty(&serde_json::json!({
            "backup_dir": backup.to_string_lossy().to_string(),
            "restored_at_unix": SystemTime::now().duration_since(UNIX_EPOCH).map(|value| value.as_secs()).unwrap_or(0)
        })).map_err(|err| io::Error::new(io::ErrorKind::InvalidData, err.to_string()))?,
    )?;
    Ok(true)
}

fn configured_media_paths(root: &Path) -> (PathBuf, Vec<PathBuf>) {
    let default_media = root.join("Media");
    let mut primary = default_media.clone();
    let mut legacy: Vec<PathBuf> = Vec::new();

    if let Ok(raw) = fs::read_to_string(desktop_settings_path(root)) {
        if let Ok(payload) = serde_json::from_str::<serde_json::Value>(&raw) {
            if let Some(value) = payload.get("media_dir").and_then(|item| item.as_str()) {
                let candidate = PathBuf::from(value);
                if candidate.is_absolute() {
                    primary = candidate;
                }
            }
            if let Some(items) = payload.get("legacy_media_dirs").and_then(|item| item.as_array()) {
                for item in items {
                    if let Some(value) = item.as_str() {
                        let candidate = PathBuf::from(value);
                        if candidate.is_absolute() && candidate != primary && !legacy.contains(&candidate) {
                            legacy.push(candidate);
                        }
                    }
                }
            }
        }
    }

    if primary != default_media && !legacy.contains(&default_media) {
        legacy.push(default_media);
    }
    if primary != root && !legacy.iter().any(|item| item == root) {
        legacy.push(root.to_path_buf());
    }
    (primary, legacy)
}
fn create_runtime_dirs(root: &Path) -> io::Result<()> {
    fs::create_dir_all(root)?;
    for name in [
        "Database",
        "Media",
        "Models",
        "TTS",
        "FlowProfile",
        "FlowSessions",
        "Logs",
        "Backups",
        "Temp",
    ] {
        fs::create_dir_all(root.join(name))?;
    }
    Ok(())
}

fn reserve_ports(count: usize) -> io::Result<Vec<u16>> {
    let mut listeners = Vec::with_capacity(count);
    for _ in 0..count {
        listeners.push(TcpListener::bind(("127.0.0.1", 0))?);
    }
    let ports = listeners
        .iter()
        .map(|listener| listener.local_addr().map(|addr| addr.port()))
        .collect::<io::Result<Vec<_>>>()?;
    drop(listeners);
    Ok(ports)
}

fn wait_for_port(port: u16, timeout: Duration) -> bool {
    let started = Instant::now();
    while started.elapsed() < timeout {
        if TcpStream::connect(("127.0.0.1", port)).is_ok() {
            return true;
        }
        thread::sleep(Duration::from_millis(150));
    }
    false
}
fn sidecar_exe(name: &str) -> io::Result<PathBuf> {
    let file_name = format!("{name}.exe");

    if let Some(root) = env::var_os("TH_MEDIA_SIDECAR_ROOT") {
        let path = PathBuf::from(root).join(name).join(&file_name);
        if path.exists() {
            return Ok(path);
        }
    }

    if let Ok(current) = env::current_exe() {
        if let Some(parent) = current.parent() {
            let path = parent.join("sidecars").join(name).join(&file_name);
            if path.exists() {
                return Ok(path);
            }
        }
    }

    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let desktop_root = manifest
        .parent()
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "desktop root not found"))?;
    let path = desktop_root.join("sidecars").join(name).join(file_name);
    if path.exists() {
        return Ok(path);
    }

    Err(io::Error::new(
        io::ErrorKind::NotFound,
        format!("Không tìm thấy sidecar {name}"),
    ))
}
fn log_rotation_settings() -> (u64, usize) {
    let max_mb = env::var("TH_MEDIA_LOG_MAX_MB")
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(DEFAULT_LOG_MAX_BYTES / (1024 * 1024));
    let keep = env::var("TH_MEDIA_LOG_KEEP")
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .filter(|value| *value >= 2)
        .map(|value| value.min(10))
        .unwrap_or(DEFAULT_LOG_KEEP);
    (max_mb * 1024 * 1024, keep)
}

fn rotate_log_if_needed(path: &Path, incoming: usize, max_bytes: u64, keep: usize) -> io::Result<()> {
    let current = fs::metadata(path).map(|meta| meta.len()).unwrap_or(0);
    if current + incoming as u64 <= max_bytes {
        return Ok(());
    }
    let oldest = PathBuf::from(format!("{}.{}", path.display(), keep));
    let _ = fs::remove_file(oldest);
    for index in (1..keep).rev() {
        let source = PathBuf::from(format!("{}.{}", path.display(), index));
        if source.exists() {
            let target = PathBuf::from(format!("{}.{}", path.display(), index + 1));
            let _ = fs::remove_file(&target);
            fs::rename(source, target)?;
        }
    }
    if path.exists() {
        let first = PathBuf::from(format!("{}.1", path.display()));
        let _ = fs::remove_file(&first);
        fs::rename(path, first)?;
    }
    Ok(())
}

fn spawn_log_pump<R: Read + Send + 'static>(reader: R, log_path: PathBuf, lock: Arc<Mutex<()>>) {
    let (max_bytes, keep) = log_rotation_settings();
    thread::spawn(move || {
        let mut reader = BufReader::new(reader);
        let mut buffer = Vec::with_capacity(4096);
        loop {
            buffer.clear();
            let read = match reader.read_until(b'\n', &mut buffer) {
                Ok(value) => value,
                Err(_) => break,
            };
            if read == 0 {
                break;
            }
            let Ok(_guard) = lock.lock() else {
                break;
            };
            if rotate_log_if_needed(&log_path, buffer.len(), max_bytes, keep).is_err() {
                continue;
            }
            if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(&log_path) {
                let _ = file.write_all(&buffer);
                let _ = file.flush();
            }
        }
    });
}

fn spawn_sidecar(exe: &Path, envs: &[(&str, String)], log_path: &Path) -> io::Result<Child> {
    if let Some(parent) = log_path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut command = Command::new(exe);
    command
        .current_dir(
            exe.parent()
                .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "sidecar dir not found"))?,
        )
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    for (key, value) in envs {
        command.env(key, value);
    }

    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);

    let mut child = command.spawn()?;
    let lock = Arc::new(Mutex::new(()));
    if let Some(stdout) = child.stdout.take() {
        spawn_log_pump(stdout, log_path.to_path_buf(), Arc::clone(&lock));
    }
    if let Some(stderr) = child.stderr.take() {
        spawn_log_pump(stderr, log_path.to_path_buf(), lock);
    }
    Ok(child)
}

#[tauri::command]
fn ensure_flow_runtime(state: tauri::State<'_, FlowRuntimeManager>) -> Result<serde_json::Value, String> {
    let guard = state
        .spec
        .lock()
        .map_err(|_| "Không thể khóa cấu hình Flow runtime.".to_string())?;
    let spec = guard
        .as_ref()
        .ok_or_else(|| "Flow runtime chưa được cấu hình.".to_string())?;

    if wait_for_port(spec.port, Duration::from_millis(120)) {
        return Ok(serde_json::json!({"ok": true, "started": false, "port": spec.port}));
    }

    let mut child = spawn_sidecar(&spec.exe, &spec.envs, &spec.log_path)
        .map_err(|err| format!("Không thể khởi động Flow Bridge: {err}"))?;
    if !wait_for_port(spec.port, Duration::from_secs(20)) {
        let _ = child.kill();
        let _ = child.wait();
        return Err("Flow Bridge sidecar không khởi động được.".to_string());
    }

    let pid = child.id();
    state
        .children
        .lock()
        .map_err(|_| "Không thể lưu Flow process handle.".to_string())?
        .push(child);
    Ok(serde_json::json!({"ok": true, "started": true, "port": spec.port, "pid": pid}))
}

fn stop_children(children: &Arc<Mutex<Vec<Child>>>) {
    if let Ok(mut guard) = children.lock() {
        for child in guard.iter_mut() {
            let _ = child.kill();
            let _ = child.wait();
        }
        guard.clear();
    }
}

fn read_http_response(stream: TcpStream) -> io::Result<(u16, Vec<u8>)> {
    let mut reader = BufReader::new(stream);
    let mut status_line = String::new();
    reader.read_line(&mut status_line)?;
    let status = status_line
        .split_whitespace()
        .nth(1)
        .and_then(|value| value.parse::<u16>().ok())
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "invalid HTTP status line"))?;

    let mut content_length = None::<usize>;
    loop {
        let mut line = String::new();
        if reader.read_line(&mut line)? == 0 {
            break;
        }
        if line == "\r\n" || line == "\n" {
            break;
        }
        if let Some((name, value)) = line.split_once(':') {
            if name.trim().eq_ignore_ascii_case("content-length") {
                content_length = value.trim().parse::<usize>().ok();
            }
        }
    }

    let mut body = Vec::new();
    if let Some(length) = content_length {
        body.resize(length, 0);
        reader.read_exact(&mut body)?;
    } else {
        reader.read_to_end(&mut body)?;
    }
    Ok((status, body))
}

fn stop_flow_chrome(control: &Arc<Mutex<Option<(u16, String)>>>) {
    let value = control.lock().ok().and_then(|guard| guard.clone());
    let Some((port, key)) = value else {
        return;
    };

    let address = format!("127.0.0.1:{port}");
    let Ok(mut stream) = TcpStream::connect(address) else {
        return;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
    let request = format!(
        "POST /v1/runtime/stop-chrome HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Bearer {key}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_ok() {
        let _ = read_http_response(stream);
    }
}

fn backend_json(
    control: &Arc<Mutex<Option<(u16, String)>>>,
    method: &str,
    path: &str,
) -> Option<serde_json::Value> {
    let (port, token) = control.lock().ok().and_then(|guard| guard.clone())?;
    let mut stream = TcpStream::connect(("127.0.0.1", port)).ok()?;
    let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
    let request = format!(
        "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nX-TH-Media-Token: {token}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
    );
    stream.write_all(request.as_bytes()).ok()?;
    let (status, body) = read_http_response(stream).ok()?;
    if status != 200 {
        return None;
    }
    serde_json::from_slice(&body).ok()
}

#[cfg(windows)]
fn wide(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(std::iter::once(0)).collect()
}

#[cfg(windows)]
fn shutdown_choice(active_count: u64, already_waiting: bool) -> i32 {
    let title = wide("TH Media");
    let message = if already_waiting {
        format!(
            "TH Media đang đợi cảnh hiện tại hoàn tất trước khi thoát.\n\nĐang có {active_count} pipeline hoạt động.\n\nYes: tiếp tục chờ\nNo: checkpoint và thoát ngay\nCancel: quay lại ứng dụng"
        )
    } else {
        format!(
            "Đang có {active_count} pipeline tạo phim hoạt động.\n\nYes: dừng sau cảnh hiện tại rồi tự thoát\nNo: checkpoint và thoát ngay\nCancel: không đóng ứng dụng"
        )
    };
    let message = wide(&message);
    unsafe {
        MessageBoxW(
            std::ptr::null_mut(),
            message.as_ptr(),
            title.as_ptr(),
            MB_YESNOCANCEL | MB_ICONWARNING | MB_SETFOREGROUND,
        )
    }
}

#[cfg(windows)]
fn backend_unavailable_choice() -> i32 {
    let title = wide("TH Media");
    let message = wide(
        "Backend TH Media không phản hồi nên không thể xác nhận trạng thái pipeline.\n\nOK: thoát ngay\nCancel: giữ ứng dụng mở",
    );
    unsafe {
        MessageBoxW(
            std::ptr::null_mut(),
            message.as_ptr(),
            title.as_ptr(),
            MB_OKCANCEL | MB_ICONERROR | MB_SETFOREGROUND,
        )
    }
}

#[cfg(windows)]
fn tray_info(message: &str) {
    let title = wide("TH Media");
    let message = wide(message);
    unsafe {
        MessageBoxW(
            std::ptr::null_mut(),
            message.as_ptr(),
            title.as_ptr(),
            MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND,
        );
    }
}

#[cfg(windows)]
fn pipeline_status_text(status: &serde_json::Value) -> String {
    let active_count = status
        .get("active_count")
        .and_then(|value| value.as_u64())
        .unwrap_or(0);
    if active_count == 0 {
        return "Không có pipeline tạo phim đang chạy.".to_string();
    }
    let first = status
        .get("active_runs")
        .and_then(|value| value.as_array())
        .and_then(|runs| runs.first());
    let scene = first
        .and_then(|run| run.get("current_scene_id"))
        .and_then(|value| value.as_str())
        .unwrap_or("chưa xác định");
    let state = first
        .and_then(|run| run.get("status"))
        .and_then(|value| value.as_str())
        .unwrap_or("running");
    format!("Có {active_count} pipeline đang hoạt động.\n\nCảnh hiện tại: {scene}\nTrạng thái: {state}")
}

fn terminate_runtime(
    flow_control: &Arc<Mutex<Option<(u16, String)>>>,
    children: &Arc<Mutex<Vec<Child>>>,
) -> ! {
    stop_flow_chrome(flow_control);
    stop_children(children);
    std::process::exit(0);
}

fn show_main_window(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

fn open_media_folder() {
    let Ok(root) = local_app_root() else {
        return;
    };
    let (path, _) = configured_media_paths(&root);
    let _ = fs::create_dir_all(&path);
    let mut command = Command::new("explorer.exe");
    command.arg(path);
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    let _ = command.spawn();
}

fn valid_project_id(value: &str) -> bool {
    let safe = value.trim();
    !safe.is_empty()
        && safe.len() <= 128
        && safe.chars().all(|ch| ch.is_ascii_alphanumeric() || ch == '-' || ch == '_')
}

#[tauri::command]
fn open_project_canonical_folder(project_id: String) -> Result<String, String> {
    let safe = project_id.trim();
    if !valid_project_id(safe) {
        return Err("Project ID không hợp lệ.".to_string());
    }
    let root = local_app_root().map_err(|err| err.to_string())?;
    let path = root.join("film_assets").join(safe);
    fs::create_dir_all(&path).map_err(|err| format!("Không thể tạo thư mục ảnh chuẩn: {err}"))?;
    let mut command = Command::new("explorer.exe");
    command.arg(&path);
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    command.spawn().map_err(|err| format!("Không thể mở File Explorer: {err}"))?;
    Ok(path.to_string_lossy().to_string())
}

#[tauri::command]
fn set_media_directory(path: String) -> Result<String, String> {
    let root = local_app_root().map_err(|err| err.to_string())?;
    create_runtime_dirs(&root).map_err(|err| err.to_string())?;
    let candidate = PathBuf::from(path);
    if !candidate.is_absolute() {
        return Err("Thư mục Media phải là đường dẫn tuyệt đối.".to_string());
    }
    fs::create_dir_all(&candidate).map_err(|err| format!("Không thể tạo thư mục Media: {err}"))?;
    let probe = candidate.join(format!(".th-media-write-test-{}", Uuid::new_v4().simple()));
    fs::write(&probe, b"ok").map_err(|err| format!("Không có quyền ghi vào thư mục Media: {err}"))?;
    let _ = fs::remove_file(&probe);

    let (current, mut legacy) = configured_media_paths(&root);
    if current != candidate && !legacy.contains(&current) {
        legacy.push(current);
    }
    legacy.retain(|item| item != &candidate);
    let mut payload = load_desktop_settings_value(&root);
    payload["media_dir"] = serde_json::Value::String(candidate.to_string_lossy().to_string());
    payload["legacy_media_dirs"] = serde_json::json!(
        legacy.iter().map(|item| item.to_string_lossy().to_string()).collect::<Vec<_>>()
    );
    save_desktop_settings_value(&root, &payload)
        .map_err(|err| format!("Không thể lưu cài đặt Media: {err}"))?;
    Ok(candidate.to_string_lossy().to_string())
}

#[tauri::command]
fn get_desktop_settings() -> Result<serde_json::Value, String> {
    let root = local_app_root().map_err(|err| err.to_string())?;
    create_runtime_dirs(&root).map_err(|err| err.to_string())?;
    let mut payload = load_desktop_settings_value(&root);
    let (media, legacy) = configured_media_paths(&root);
    payload["media_dir"] = serde_json::Value::String(media.to_string_lossy().to_string());
    payload["legacy_media_dirs"] = serde_json::json!(
        legacy.iter().map(|item| item.to_string_lossy().to_string()).collect::<Vec<_>>()
    );
    payload["paths"] = serde_json::json!({
        "data_root": root.to_string_lossy().to_string(),
        "database_path": root.join("Database").join("aihub.db").to_string_lossy().to_string(),
        "flow_profile_dir": root.join("FlowProfile").to_string_lossy().to_string(),
        "flow_sessions_dir": root.join("FlowSessions").to_string_lossy().to_string(),
        "temp_dir": root.join("Temp").to_string_lossy().to_string(),
        "logs_dir": root.join("Logs").to_string_lossy().to_string(),
        "backups_dir": root.join("Backups").to_string_lossy().to_string()
    });
    Ok(payload)
}

#[tauri::command]
fn save_desktop_settings(settings: serde_json::Value) -> Result<serde_json::Value, String> {
    let root = local_app_root().map_err(|err| err.to_string())?;
    create_runtime_dirs(&root).map_err(|err| err.to_string())?;
    let source = settings.as_object().ok_or_else(|| "Cài đặt Desktop không hợp lệ.".to_string())?;
    let mut merged = load_desktop_settings_value(&root);

    if let Some(value) = source.get("language") {
        let language = value.as_str().ok_or_else(|| "language phải là chuỗi.".to_string())?;
        if language != "vi" {
            return Err("Bản TH Media hiện tại chỉ hỗ trợ giao diện Tiếng Việt.".to_string());
        }
        merged["language"] = serde_json::Value::String("vi".to_string());
    }

    if let Some(value) = source.get("backup_keep") {
        let keep = value.as_u64().ok_or_else(|| "backup_keep phải là số nguyên.".to_string())?;
        if !(1..=30).contains(&keep) {
            return Err("Số backup giữ lại phải trong khoảng 1–30.".to_string());
        }
        merged["backup_keep"] = serde_json::json!(keep);
    }

    for key in ["minimize_to_tray", "notifications_enabled"] {
        if let Some(value) = source.get(key) {
            let parsed = value.as_bool().ok_or_else(|| format!("{key} phải là true/false."))?;
            merged[key] = serde_json::Value::Bool(parsed);
        }
    }

    if let Some(value) = source.get("chrome_path") {
        let raw = value.as_str().ok_or_else(|| "chrome_path phải là chuỗi.".to_string())?.trim();
        if raw.is_empty() {
            merged["chrome_path"] = serde_json::Value::String(String::new());
        } else {
            let path = PathBuf::from(raw);
            if !path.is_absolute() || !path.is_file() {
                return Err("Chrome path phải trỏ tới file chrome.exe tồn tại.".to_string());
            }
            merged["chrome_path"] = serde_json::Value::String(path.to_string_lossy().to_string());
        }
    }

    if let Some(value) = source.get("temp_quota_gb") {
        let quota = value.as_f64().ok_or_else(|| "temp_quota_gb phải là số.".to_string())?;
        if !(0.5..=500.0).contains(&quota) {
            return Err("Temp quota phải trong khoảng 0.5–500 GB.".to_string());
        }
        merged["temp_quota_gb"] = serde_json::json!(quota);
    }

    if let Some(value) = source.get("log_max_mb") {
        let max_mb = value.as_u64().ok_or_else(|| "log_max_mb phải là số nguyên.".to_string())?;
        if !(1..=2048).contains(&max_mb) {
            return Err("Log max phải trong khoảng 1–2048 MB.".to_string());
        }
        merged["log_max_mb"] = serde_json::json!(max_mb);
    }

    if let Some(value) = source.get("log_keep") {
        let keep = value.as_u64().ok_or_else(|| "log_keep phải là số nguyên.".to_string())?;
        if !(2..=30).contains(&keep) {
            return Err("Số file log giữ lại phải trong khoảng 2–30.".to_string());
        }
        merged["log_keep"] = serde_json::json!(keep);
    }

    if let Some(value) = source.get("default_video_model") {
        let model = value.as_str().ok_or_else(|| "default_video_model phải là chuỗi.".to_string())?.trim();
        if model.len() > 200 {
            return Err("Tên model mặc định quá dài.".to_string());
        }
        merged["default_video_model"] = serde_json::Value::String(model.to_string());
    }

    if let Some(value) = source.get("default_video_aspect_ratio") {
        let ratio = value.as_str().ok_or_else(|| "Tỷ lệ video không hợp lệ.".to_string())?;
        if !["16:9", "9:16", "1:1"].contains(&ratio) {
            return Err("Tỷ lệ video mặc định không được hỗ trợ.".to_string());
        }
        merged["default_video_aspect_ratio"] = serde_json::Value::String(ratio.to_string());
    }

    if let Some(value) = source.get("default_video_resolution") {
        let resolution = value.as_str().ok_or_else(|| "Độ phân giải video không hợp lệ.".to_string())?;
        if !["720p", "1080p", "Highest"].contains(&resolution) {
            return Err("Độ phân giải mặc định không được hỗ trợ.".to_string());
        }
        merged["default_video_resolution"] = serde_json::Value::String(resolution.to_string());
    }

    save_desktop_settings_value(&root, &merged).map_err(|err| err.to_string())?;
    get_desktop_settings()
}

#[tauri::command]
fn restart_th_media(app: tauri::AppHandle) {
    app.restart();
}

fn open_diagnostics_folder() {
    let Ok(root) = local_app_root() else {
        return;
    };
    let path = root.join("Diagnostics");
    let _ = fs::create_dir_all(&path);
    let mut command = Command::new("explorer.exe");
    command.arg(path);
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    let _ = command.spawn();
}

fn write_crash_report(kind: &str, message: &str) {
    let Ok(root) = local_app_root() else {
        return;
    };
    let logs = root.join("Logs");
    if fs::create_dir_all(&logs).is_err() {
        return;
    }
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_secs())
        .unwrap_or(0);
    let safe_message = message.replace('\r', " ").replace('\n', " ");
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(logs.join("desktop-crash.log")) {
        let _ = writeln!(file, "{stamp}\t{kind}\t{safe_message}");
        let _ = file.flush();
    }
}

fn install_panic_hook() {
    let default_hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        write_crash_report("panic", &info.to_string());
        default_hook(info);
    }));
}

fn update_recovery_paths(root: &Path) -> (PathBuf, PathBuf, PathBuf) {
    let updates = root.join("Updates");
    (
        updates.join("pending-healthcheck"),
        updates.join("rollback-previous.exe"),
        updates.join("rollback-in-progress"),
    )
}

fn launch_update_rollback(reason: &str) -> bool {
    let Ok(root) = local_app_root() else { return false; };
    let (pending, previous, in_progress) = update_recovery_paths(&root);
    if !pending.is_file() || !previous.is_file() || in_progress.is_file() { return false; }
    write_crash_report("update-rollback", reason);
    let _ = fs::write(&in_progress, b"rollback");
    let mut command = Command::new(previous);
    command.args(["/S", "/UPDATE", "/R"]);
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    match command.spawn() {
        Ok(_) => true,
        Err(err) => {
            let _ = fs::remove_file(&in_progress);
            write_crash_report("update-rollback-launch-failed", &err.to_string());
            false
        }
    }
}

fn mark_update_healthy() {
    let Ok(root) = local_app_root() else { return; };
    let (pending, _, in_progress) = update_recovery_paths(&root);
    let _ = fs::remove_file(pending);
    let _ = fs::remove_file(in_progress);
}

fn shared_runtime_root() -> io::Result<PathBuf> {
    if let Some(value) = env::var_os("TH_MEDIA_RUNTIME_ROOT") {
        let path = PathBuf::from(value);
        if path.is_dir() {
            return Ok(path);
        }
    }
    if let Ok(exe) = env::current_exe() {
        if let Some(parent) = exe.parent() {
            let path = parent.join("runtime");
            if path.is_dir() {
                return Ok(path);
            }
        }
    }
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let desktop_root = manifest.parent().ok_or_else(|| {
        io::Error::new(io::ErrorKind::NotFound, "desktop root not found")
    })?;
    let path = desktop_root.join("runtime");
    if path.is_dir() {
        return Ok(path);
    }
    Err(io::Error::new(
        io::ErrorKind::NotFound,
        "TH Media runtime dependencies chưa được chuẩn bị",
    ))
}

fn runtime_dependency_paths(data_root: &Path) -> io::Result<(PathBuf, PathBuf, PathBuf, PathBuf, PathBuf)> {
    let runtime_root = shared_runtime_root()?;
    let bin_dir = runtime_root.join("bin");
    let model_dir = runtime_root.join("models");
    let ffmpeg = bin_dir.join("ffmpeg.exe");
    let ffprobe = bin_dir.join("ffprobe.exe");
    let local_speaker = data_root.join("Models").join("speaker").join(SPEAKER_MODEL_FILE);
    let bundled_speaker = model_dir.join("speaker").join(SPEAKER_MODEL_FILE);
    let speaker = if local_speaker.is_file() { local_speaker } else { bundled_speaker };

    for (label, path) in [("ffmpeg", &ffmpeg), ("ffprobe", &ffprobe), ("speaker model", &speaker)] {
        if !path.is_file() {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                format!("Thiếu dependency {label}: {}", path.display()),
            ));
        }
    }
    Ok((bin_dir, model_dir, ffmpeg, ffprobe, speaker))
}

/// Chromium shipped inside the package as the last-resort Flow browser. Optional
/// on purpose: machines with Chrome or Edge must not need it, and a missing
/// bundle must not stop the desktop from booting.
fn bundled_browser_in(runtime_root: &Path) -> Option<PathBuf> {
    let candidate = runtime_root
        .join("browser")
        .join("chromium")
        .join("chrome.exe");
    candidate.is_file().then_some(candidate)
}

fn bundled_browser_path() -> Option<PathBuf> {
    bundled_browser_in(&shared_runtime_root().ok()?)
}

fn boot_runtime() -> io::Result<RuntimeBoot> {
    let root = local_app_root()?;
    create_runtime_dirs(&root)?;
    let desktop_settings = load_desktop_settings_value(&root);
    if let Some(value) = desktop_settings.get("log_max_mb").and_then(|item| item.as_u64()) {
        env::set_var("TH_MEDIA_LOG_MAX_MB", value.to_string());
    }
    if let Some(value) = desktop_settings.get("log_keep").and_then(|item| item.as_u64()) {
        env::set_var("TH_MEDIA_LOG_KEEP", value.to_string());
    }
    let (runtime_bin, runtime_models, ffmpeg, ffprobe, speaker_model) = runtime_dependency_paths(&root)?;
    let runtime_bin_s = runtime_bin.to_string_lossy().into_owned();
    let runtime_models_s = runtime_models.to_string_lossy().into_owned();
    let ffmpeg_s = ffmpeg.to_string_lossy().into_owned();
    let ffprobe_s = ffprobe.to_string_lossy().into_owned();
    let speaker_model_s = speaker_model.to_string_lossy().into_owned();

    let ports = reserve_ports(3)?;
    let backend_port = ports[0];
    let flow_port = ports[1];
    let cdp_port = ports[2];

    let auth_token = env::var("TH_MEDIA_AUTH_TOKEN").unwrap_or_else(|_| {
        format!(
            "thmedia_{}_{}",
            Uuid::new_v4().simple(),
            Uuid::new_v4().simple()
        )
    });
    let flow_key = env::var("FLOW_BRIDGE_API_KEY").unwrap_or_else(|_| {
        format!(
            "thflow_{}_{}",
            Uuid::new_v4().simple(),
            Uuid::new_v4().simple()
        )
    });

    let backend_url = format!("http://127.0.0.1:{backend_port}");
    let flow_url = format!("http://127.0.0.1:{flow_port}");
    let cdp_url = format!("http://127.0.0.1:{cdp_port}");

    let database_dir = root.join("Database");
    let (media_dir, legacy_media_dirs) = configured_media_paths(&root);
    fs::create_dir_all(&media_dir)?;
    let logs_dir = root.join("Logs");
    let flow_profile = root.join("FlowProfile");
    let flow_sessions = root.join("FlowSessions");
    let flow_registry = database_dir.join("flow_sessions.json");

    let flow_exe = sidecar_exe("th-media-flow-bridge")?;
    let backend_exe = sidecar_exe("th-media-backend")?;
    let root_s = root.to_string_lossy().into_owned();
    let media_s = media_dir.to_string_lossy().into_owned();
    let legacy_media_s = legacy_media_dirs
        .iter()
        .map(|item| item.to_string_lossy().to_string())
        .collect::<Vec<_>>()
        .join(";");
    let flow_profile_s = flow_profile.to_string_lossy().into_owned();
    let flow_sessions_s = flow_sessions.to_string_lossy().into_owned();
    let flow_registry_s = flow_registry.to_string_lossy().into_owned();
    let chrome_path_s = desktop_settings
        .get("chrome_path")
        .and_then(|item| item.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    let temp_quota_s = desktop_settings
        .get("temp_quota_gb")
        .and_then(|item| item.as_f64())
        .unwrap_or(10.0)
        .to_string();

    let mut flow_env = vec![
        ("TH_MEDIA_DATA_DIR", root_s.clone()),
        ("TH_MEDIA_MEDIA_DIR", media_s.clone()),
        ("TH_MEDIA_LEGACY_MEDIA_DIRS", legacy_media_s.clone()),
        ("TH_MEDIA_FLOW_PROFILE_DIR", flow_profile_s),
        ("TH_MEDIA_FLOW_SESSIONS_DIR", flow_sessions_s),
        ("TH_MEDIA_FLOW_REGISTRY_PATH", flow_registry_s),
        ("TH_MEDIA_FLOW_BRIDGE_HOST", "127.0.0.1".to_string()),
        ("TH_MEDIA_FLOW_BRIDGE_PORT", flow_port.to_string()),
        ("FLOW_BRIDGE_API_KEY", flow_key.clone()),
        ("FLOW_CDP_URL", cdp_url),
        ("TH_MEDIA_RUNTIME_BIN_DIR", runtime_bin_s.clone()),
        ("TH_MEDIA_FFMPEG_PATH", ffmpeg_s.clone()),
        ("TH_MEDIA_FFPROBE_PATH", ffprobe_s.clone()),
    ];
    if !chrome_path_s.is_empty() {
        flow_env.push(("TH_MEDIA_CHROME_PATH", chrome_path_s));
    }
    if let Some(browser) = bundled_browser_path() {
        flow_env.push(("TH_MEDIA_BUNDLED_BROWSER_PATH", browser.to_string_lossy().into_owned()));
    }

    let flow_spec = FlowLaunchSpec {
        port: flow_port,
        exe: flow_exe,
        envs: flow_env,
        log_path: logs_dir.join("flow-bridge.log"),
    };

    let backend_env = vec![
        ("TH_MEDIA_DATA_DIR", root_s),
        ("TH_MEDIA_MEDIA_DIR", media_s),
        ("TH_MEDIA_LEGACY_MEDIA_DIRS", legacy_media_s),
        (
            "TH_MEDIA_DB_PATH",
            database_dir.join("aihub.db").to_string_lossy().into_owned(),
        ),
        (
            "TH_MEDIA_KEY_PATH",
            database_dir
                .join("master.key")
                .to_string_lossy()
                .into_owned(),
        ),
        ("TH_MEDIA_BACKEND_HOST", "127.0.0.1".to_string()),
        ("TH_MEDIA_BACKEND_PORT", backend_port.to_string()),
        ("TH_MEDIA_DESKTOP_MODE", "1".to_string()),
        (
            "TH_MEDIA_DEV_SERVER",
            if cfg!(debug_assertions) { "1" } else { "0" }.to_string(),
        ),
        ("TH_MEDIA_TEMP_QUOTA_GB", temp_quota_s),
        ("TH_MEDIA_FLOW_BRIDGE_PORT", flow_port.to_string()),
        ("TH_MEDIA_CDP_PORT", cdp_port.to_string()),
        ("TH_MEDIA_APP_VERSION", env!("CARGO_PKG_VERSION").to_string()),
        ("TH_MEDIA_AUTH_TOKEN", auth_token.clone()),
        ("TH_MEDIA_FLOW_BRIDGE_URL", flow_url),
        ("FLOW_BRIDGE_API_KEY", flow_key.clone()),
        ("TH_MEDIA_RUNTIME_BIN_DIR", runtime_bin_s),
        ("TH_MEDIA_RUNTIME_MODEL_DIR", runtime_models_s),
        ("TH_MEDIA_FFMPEG_PATH", ffmpeg_s),
        ("TH_MEDIA_FFPROBE_PATH", ffprobe_s),
        ("FILM_SPEAKER_MODEL_PATH", speaker_model_s),
    ];

    let mut backend_child = spawn_sidecar(&backend_exe, &backend_env, &logs_dir.join("backend.log"))?;

    if !wait_for_port(backend_port, Duration::from_secs(30)) {
        let _ = backend_child.kill();
        let _ = backend_child.wait();
        return Err(io::Error::new(
            io::ErrorKind::TimedOut,
            "Backend sidecar không khởi động được",
        ));
    }
    Ok(RuntimeBoot {
        backend_url,
        backend_port,
        auth_token,
        flow_port,
        flow_key,
        flow_spec,
        children: vec![backend_child],
    })
}

fn main() {
    install_panic_hook();
    let children = Arc::new(Mutex::new(Vec::<Child>::new()));
    let flow_control = Arc::new(Mutex::new(None::<(u16, String)>));
    let backend_control = Arc::new(Mutex::new(None::<(u16, String)>));
    let safe_shutdown_waiting = Arc::new(AtomicBool::new(false));
    let setup_children = Arc::clone(&children);
    let setup_flow_control = Arc::clone(&flow_control);
    let setup_backend_control = Arc::clone(&backend_control);
    let setup_safe_shutdown_waiting = Arc::clone(&safe_shutdown_waiting);
    let flow_runtime_manager = FlowRuntimeManager {
        spec: Mutex::new(None),
        children: Arc::clone(&children),
    };

    let app = tauri::Builder::default()
        .manage(flow_runtime_manager)
        .plugin(tauri_plugin_autostart::Builder::new().args(["--autostart"]).app_name("TH Media").build())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            show_main_window(app);
        }))
        .invoke_handler(tauri::generate_handler![ensure_flow_runtime, get_desktop_settings, save_desktop_settings, set_media_directory, open_project_canonical_folder, restart_th_media])
        .setup(move |app| {
            if let Ok(resource_dir) = app.path().resource_dir() {
                let bundled_sidecars = resource_dir.join("sidecars");
                let bundled_runtime = resource_dir.join("runtime");
                if bundled_sidecars.is_dir() {
                    env::set_var("TH_MEDIA_SIDECAR_ROOT", bundled_sidecars);
                }
                if bundled_runtime.is_dir() {
                    env::set_var("TH_MEDIA_RUNTIME_ROOT", bundled_runtime);
                }
            }
            let desktop_root = local_app_root()?;
            create_runtime_dirs(&desktop_root)?;
            let _ = apply_pending_restore(&desktop_root)?;
            let desktop_settings = load_desktop_settings_value(&desktop_root);
            let minimize_to_tray = desktop_settings
                .get("minimize_to_tray")
                .and_then(|value| value.as_bool())
                .unwrap_or(true);
            let started_from_autostart = env::args().any(|arg| arg == "--autostart");

            let mut boot = match boot_runtime() {
                Ok(boot) => boot,
                Err(err) => {
                    let reason = err.to_string();
                    write_crash_report("startup", &reason);
                    let _ = launch_update_rollback(&reason);
                    return Err(Box::new(err));
                }
            };

            let runtime = serde_json::json!({
                "backendBaseUrl": boot.backend_url,
                "authToken": boot.auth_token,
            });
            let init_script = format!("window.__TH_MEDIA_RUNTIME__ = {};", runtime);

            let window =
                match WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                    .title("TH Media")
                    .inner_size(1440.0, 900.0)
                    .min_inner_size(1100.0, 720.0)
                    .center()
                    .initialization_script(&init_script)
                    .build()
                {
                    Ok(window) => window,
                    Err(err) => {
                        for child in boot.children.iter_mut() {
                            let _ = child.kill();
                            let _ = child.wait();
                        }
                        let reason = err.to_string();
                        write_crash_report("window-startup", &reason);
                        let _ = launch_update_rollback(&reason);
                        return Err(Box::new(err));
                    }
                };

            if started_from_autostart && minimize_to_tray {
                let _ = window.hide();
            }

            if let Ok(mut guard) = app.state::<FlowRuntimeManager>().spec.lock() {
                *guard = Some(boot.flow_spec);
            }
            if let Ok(mut guard) = setup_flow_control.lock() {
                *guard = Some((boot.flow_port, boot.flow_key.clone()));
            }
            if let Ok(mut guard) = setup_backend_control.lock() {
                *guard = Some((boot.backend_port, boot.auth_token.clone()));
            }
            if let Ok(mut guard) = setup_children.lock() {
                guard.extend(boot.children);
            }

            let tray_open = MenuItem::with_id(app, "tray_open", "Mở TH Media", true, None::<&str>)?;
            let tray_status = MenuItem::with_id(app, "tray_status", "Trạng thái pipeline", true, None::<&str>)?;
            let tray_pause = MenuItem::with_id(app, "tray_pause", "Dừng sau cảnh hiện tại", true, None::<&str>)?;
            let tray_media = MenuItem::with_id(app, "tray_media", "Mở thư mục video", true, None::<&str>)?;
            let tray_diagnostics = MenuItem::with_id(app, "tray_diagnostics", "Xuất gói chẩn đoán", true, None::<&str>)?;
            let tray_exit = MenuItem::with_id(app, "tray_exit", "Thoát", true, None::<&str>)?;
            let tray_menu = Menu::with_items(app, &[&tray_open, &tray_status, &tray_pause, &tray_media, &tray_diagnostics, &tray_exit])?;
            let tray_backend_control = Arc::clone(&setup_backend_control);
            let mut tray_builder = TrayIconBuilder::with_id("th-media-tray")
                .menu(&tray_menu)
                .show_menu_on_left_click(false)
                .tooltip("TH Media")
                .on_menu_event(move |app, event| match event.id().as_ref() {
                    "tray_open" => show_main_window(app),
                    "tray_status" => {
                        if let Some(status) = backend_json(&tray_backend_control, "GET", "/api/desktop/shutdown/status") {
                            #[cfg(windows)]
                            tray_info(&pipeline_status_text(&status));
                        } else {
                            #[cfg(windows)]
                            tray_info("Không thể đọc trạng thái pipeline từ backend.");
                        }
                    }
                    "tray_pause" => {
                        let requested = backend_json(&tray_backend_control, "POST", "/api/desktop/shutdown/request-safe").is_some();
                        #[cfg(windows)]
                        tray_info(if requested {
                            "Đã yêu cầu pipeline dừng an toàn sau cảnh hiện tại."
                        } else {
                            "Không thể gửi yêu cầu dừng pipeline."
                        });
                    }
                    "tray_media" => open_media_folder(),
                    "tray_diagnostics" => {
                        let exported = backend_json(&tray_backend_control, "POST", "/api/desktop/diagnostics/export");
                        #[cfg(windows)]
                        tray_info(if exported.is_some() {
                            "Đã tạo gói chẩn đoán. Thư mục Diagnostics sẽ được mở."
                        } else {
                            "Không thể tạo gói chẩn đoán từ backend."
                        });
                        if exported.is_some() {
                            open_diagnostics_folder();
                        }
                    }
                    "tray_exit" => {
                        show_main_window(app);
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.close();
                        }
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if matches!(event, TrayIconEvent::DoubleClick { .. }) {
                        show_main_window(tray.app_handle());
                    }
                });
            if let Some(icon) = app.default_window_icon().cloned() {
                tray_builder = tray_builder.icon(icon);
            }
            let _tray = tray_builder.build(app)?;

            let close_children = Arc::clone(&setup_children);
            let close_flow_control = Arc::clone(&setup_flow_control);
            let close_backend_control = Arc::clone(&setup_backend_control);
            let close_safe_shutdown_waiting = Arc::clone(&setup_safe_shutdown_waiting);
            let close_window = window.clone();
            let close_minimize_to_tray = minimize_to_tray;
            window.on_window_event(move |event| {
                if matches!(event, tauri::WindowEvent::Resized(_)) {
                    if close_minimize_to_tray && close_window.is_minimized().unwrap_or(false) {
                        let _ = close_window.hide();
                    }
                    return;
                }
                let tauri::WindowEvent::CloseRequested { api, .. } = event else {
                    return;
                };
                api.prevent_close();

                let Some(status) = backend_json(
                    &close_backend_control,
                    "GET",
                    "/api/desktop/shutdown/status",
                ) else {
                    if backend_unavailable_choice() == IDOK {
                        terminate_runtime(&close_flow_control, &close_children);
                    }
                    return;
                };

                if status
                    .get("safe_to_exit")
                    .and_then(|value| value.as_bool())
                    .unwrap_or(false)
                {
                    terminate_runtime(&close_flow_control, &close_children);
                }

                let active_count = status
                    .get("active_count")
                    .and_then(|value| value.as_u64())
                    .unwrap_or(1);
                let already_waiting = close_safe_shutdown_waiting.load(Ordering::SeqCst);
                let choice = shutdown_choice(active_count, already_waiting);

                if choice == IDCANCEL {
                    return;
                }

                if choice == IDNO {
                    if backend_json(
                        &close_backend_control,
                        "POST",
                        "/api/desktop/shutdown/prepare-force",
                    )
                    .is_some()
                    {
                        terminate_runtime(&close_flow_control, &close_children);
                    }
                    return;
                }

                if choice != IDYES || already_waiting {
                    return;
                }

                if backend_json(
                    &close_backend_control,
                    "POST",
                    "/api/desktop/shutdown/request-safe",
                )
                .is_none()
                {
                    return;
                }

                close_safe_shutdown_waiting.store(true, Ordering::SeqCst);
                let _ = close_window.set_title("TH Media — Đang dừng sau cảnh hiện tại...");

                let wait_backend = Arc::clone(&close_backend_control);
                let wait_flow = Arc::clone(&close_flow_control);
                let wait_children = Arc::clone(&close_children);
                let wait_flag = Arc::clone(&close_safe_shutdown_waiting);
                thread::spawn(move || loop {
                    thread::sleep(Duration::from_secs(1));
                    match backend_json(&wait_backend, "GET", "/api/desktop/shutdown/status") {
                        Some(value)
                            if value
                                .get("safe_to_exit")
                                .and_then(|item| item.as_bool())
                                .unwrap_or(false) =>
                        {
                            terminate_runtime(&wait_flow, &wait_children);
                        }
                        Some(_) => {}
                        None => {
                            wait_flag.store(false, Ordering::SeqCst);
                            break;
                        }
                    }
                });
            });
            mark_update_healthy();
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build TH Media desktop");

    let shutdown_children = Arc::clone(&children);
    let shutdown_flow_control = Arc::clone(&flow_control);
    app.run(move |_app_handle, event| {
        if matches!(event, RunEvent::Exit) {
            stop_flow_chrome(&shutdown_flow_control);
            stop_children(&shutdown_children);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn log_rotation_respects_retention_limit() {
        let dir = env::temp_dir().join(format!("th-media-log-test-{}", Uuid::new_v4()));
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join("backend.log");
        fs::write(&path, vec![b'a'; 32]).unwrap();
        fs::write(PathBuf::from(format!("{}.1", path.display())), b"one").unwrap();
        fs::write(PathBuf::from(format!("{}.2", path.display())), b"two").unwrap();
        fs::write(PathBuf::from(format!("{}.3", path.display())), b"three").unwrap();

        rotate_log_if_needed(&path, 1, 16, 3).unwrap();

        assert!(!path.exists());
        assert!(PathBuf::from(format!("{}.1", path.display())).exists());
        assert!(PathBuf::from(format!("{}.2", path.display())).exists());
        assert!(PathBuf::from(format!("{}.3", path.display())).exists());
        assert!(!PathBuf::from(format!("{}.4", path.display())).exists());
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn configured_media_paths_preserve_previous_root() {
        let dir = env::temp_dir().join(format!("th-media-settings-test-{}", Uuid::new_v4()));
        fs::create_dir_all(&dir).unwrap();
        let default_media = dir.join("Media");
        let custom_media = dir.join("D-drive-media");
        fs::create_dir_all(&default_media).unwrap();
        fs::create_dir_all(&custom_media).unwrap();
        let payload = serde_json::json!({
            "media_dir": custom_media.to_string_lossy(),
            "legacy_media_dirs": [],
        });
        fs::write(desktop_settings_path(&dir), serde_json::to_vec(&payload).unwrap()).unwrap();

        let (primary, legacy) = configured_media_paths(&dir);
        assert_eq!(primary, custom_media);
        assert!(legacy.contains(&default_media));
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn configured_media_paths_include_historical_data_root_without_settings() {
        let dir = env::temp_dir().join(format!("th-media-settings-legacy-root-test-{}", Uuid::new_v4()));
        fs::create_dir_all(&dir).unwrap();
        let (primary, legacy) = configured_media_paths(&dir);
        assert_eq!(primary, dir.join("Media"));
        assert!(legacy.contains(&dir));
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn desktop_settings_merge_keeps_defaults() {
        let dir = env::temp_dir().join(format!("th-media-settings-merge-test-{}", Uuid::new_v4()));
        fs::create_dir_all(&dir).unwrap();
        fs::write(
            desktop_settings_path(&dir),
            serde_json::to_vec(&serde_json::json!({"temp_quota_gb": 22.0})).unwrap(),
        ).unwrap();
        let loaded = load_desktop_settings_value(&dir);
        assert_eq!(loaded.get("temp_quota_gb").and_then(|v| v.as_f64()), Some(22.0));
        assert_eq!(loaded.get("minimize_to_tray").and_then(|v| v.as_bool()), Some(true));
        assert_eq!(loaded.get("default_video_aspect_ratio").and_then(|v| v.as_str()), Some("16:9"));
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn pending_restore_replaces_database_and_settings_once() {
        let dir = env::temp_dir().join(format!("th-media-restore-test-{}", Uuid::new_v4()));
        let backup = dir.join("Backups").join("user-test");
        let database = dir.join("Database");
        fs::create_dir_all(&backup).unwrap();
        fs::create_dir_all(&database).unwrap();
        fs::write(database.join("aihub.db"), b"old-db").unwrap();
        fs::write(backup.join("aihub.db"), b"new-db").unwrap();
        fs::write(backup.join("desktop-settings.json"), br#"{"minimize_to_tray":false}"#).unwrap();
        fs::write(
            dir.join("pending-restore.json"),
            serde_json::to_vec(&serde_json::json!({"backup_dir": backup.to_string_lossy()})).unwrap(),
        ).unwrap();

        assert!(apply_pending_restore(&dir).unwrap());
        assert_eq!(fs::read(database.join("aihub.db")).unwrap(), b"new-db");
        assert!(dir.join("desktop-settings.json").is_file());
        assert!(!dir.join("pending-restore.json").exists());
        assert!(!apply_pending_restore(&dir).unwrap());
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn project_id_validation_blocks_path_traversal() {
        assert!(valid_project_id("273b8bf6-469e-45f5-994f-53598b126c9b"));
        assert!(valid_project_id("PROJECT_001"));
        assert!(!valid_project_id(""));
        assert!(!valid_project_id("../film_assets"));
        assert!(!valid_project_id("a/b"));
        assert!(!valid_project_id("project id"));
    }

    #[test]
    fn bundled_browser_is_only_reported_when_the_binary_exists() {
        let nonce = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!("th-media-browser-{nonce}"));
        let chrome = root.join("browser").join("chromium").join("chrome.exe");

        assert_eq!(bundled_browser_in(&root), None);

        fs::create_dir_all(chrome.parent().unwrap()).unwrap();
        fs::write(&chrome, b"chrome").unwrap();
        assert_eq!(bundled_browser_in(&root), Some(chrome));

        let _ = fs::remove_dir_all(root);
    }
}
