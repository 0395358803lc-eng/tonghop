#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    env,
    fs::{self, OpenOptions},
    io::{self, Read, Write},
    net::{TcpListener, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{Arc, Mutex},
    thread,
    time::{Duration, Instant},
};

#[cfg(windows)]
use std::os::windows::process::CommandExt;
use tauri::{RunEvent, WebviewUrl, WebviewWindowBuilder};
use uuid::Uuid;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x08000000;

struct RuntimeBoot {
    backend_url: String,
    auth_token: String,
    flow_port: u16,
    flow_key: String,
    children: Vec<Child>,
}

fn local_app_root() -> io::Result<PathBuf> {
    let base = env::var_os("LOCALAPPDATA")
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "LOCALAPPDATA is unavailable"))?;
    Ok(PathBuf::from(base).join("TH Media"))
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
fn spawn_sidecar(exe: &Path, envs: &[(&str, String)], log_path: &Path) -> io::Result<Child> {
    let stdout = OpenOptions::new()
        .create(true)
        .append(true)
        .open(log_path)?;
    let stderr = stdout.try_clone()?;

    let mut command = Command::new(exe);
    command
        .current_dir(
            exe.parent()
                .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "sidecar dir not found"))?,
        )
        .stdin(Stdio::null())
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr));

    for (key, value) in envs {
        command.env(key, value);
    }

    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);

    command.spawn()
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

fn stop_flow_chrome(control: &Arc<Mutex<Option<(u16, String)>>>) {
    let value = control.lock().ok().and_then(|guard| guard.clone());
    let Some((port, key)) = value else {
        return;
    };

    let address = format!("127.0.0.1:{port}");
    let Ok(mut stream) = TcpStream::connect(address) else {
        return;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(15)));
    let request = format!(
        "POST /v1/runtime/stop-chrome HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Bearer {key}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_ok() {
        let mut response = String::new();
        let _ = stream.read_to_string(&mut response);
    }
}
fn boot_runtime() -> io::Result<RuntimeBoot> {
    let root = local_app_root()?;
    create_runtime_dirs(&root)?;

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
    let logs_dir = root.join("Logs");
    let flow_profile = root.join("FlowProfile");
    let flow_sessions = root.join("FlowSessions");
    let flow_registry = database_dir.join("flow_sessions.json");

    let flow_exe = sidecar_exe("th-media-flow-bridge")?;
    let backend_exe = sidecar_exe("th-media-backend")?;
    let root_s = root.to_string_lossy().into_owned();
    let flow_profile_s = flow_profile.to_string_lossy().into_owned();
    let flow_sessions_s = flow_sessions.to_string_lossy().into_owned();
    let flow_registry_s = flow_registry.to_string_lossy().into_owned();

    let flow_env = vec![
        ("TH_MEDIA_DATA_DIR", root_s.clone()),
        ("TH_MEDIA_FLOW_PROFILE_DIR", flow_profile_s),
        ("TH_MEDIA_FLOW_SESSIONS_DIR", flow_sessions_s),
        ("TH_MEDIA_FLOW_REGISTRY_PATH", flow_registry_s),
        ("TH_MEDIA_FLOW_BRIDGE_HOST", "127.0.0.1".to_string()),
        ("TH_MEDIA_FLOW_BRIDGE_PORT", flow_port.to_string()),
        ("FLOW_BRIDGE_API_KEY", flow_key.clone()),
        ("FLOW_CDP_URL", cdp_url),
    ];

    let mut flow_child = spawn_sidecar(&flow_exe, &flow_env, &logs_dir.join("flow-bridge.log"))?;

    if !wait_for_port(flow_port, Duration::from_secs(20)) {
        let _ = flow_child.kill();
        let _ = flow_child.wait();
        return Err(io::Error::new(
            io::ErrorKind::TimedOut,
            "Flow Bridge sidecar không khởi động được",
        ));
    }
    let backend_env = vec![
        ("TH_MEDIA_DATA_DIR", root_s),
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
        ("TH_MEDIA_AUTH_TOKEN", auth_token.clone()),
        ("TH_MEDIA_FLOW_BRIDGE_URL", flow_url),
        ("FLOW_BRIDGE_API_KEY", flow_key.clone()),
    ];

    let mut backend_child =
        match spawn_sidecar(&backend_exe, &backend_env, &logs_dir.join("backend.log")) {
            Ok(child) => child,
            Err(err) => {
                let _ = flow_child.kill();
                let _ = flow_child.wait();
                return Err(err);
            }
        };

    if !wait_for_port(backend_port, Duration::from_secs(30)) {
        let _ = backend_child.kill();
        let _ = backend_child.wait();
        let _ = flow_child.kill();
        let _ = flow_child.wait();
        return Err(io::Error::new(
            io::ErrorKind::TimedOut,
            "Backend sidecar không khởi động được",
        ));
    }
    Ok(RuntimeBoot {
        backend_url,
        auth_token,
        flow_port,
        flow_key,
        children: vec![flow_child, backend_child],
    })
}

fn main() {
    let children = Arc::new(Mutex::new(Vec::<Child>::new()));
    let flow_control = Arc::new(Mutex::new(None::<(u16, String)>));
    let setup_children = Arc::clone(&children);
    let setup_flow_control = Arc::clone(&flow_control);

    let app = tauri::Builder::default()
        .setup(move |app| {
            let mut boot = boot_runtime()?;

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
                        return Err(Box::new(err));
                    }
                };

            let app_handle = app.handle().clone();
            window.on_window_event(move |event| {
                if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                    app_handle.exit(0);
                }
            });

            if let Ok(mut guard) = setup_flow_control.lock() {
                *guard = Some((boot.flow_port, boot.flow_key.clone()));
            }
            if let Ok(mut guard) = setup_children.lock() {
                guard.extend(boot.children);
            }
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
