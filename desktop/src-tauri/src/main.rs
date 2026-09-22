#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::{WebviewUrl, WebviewWindowBuilder};

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let backend_base_url = std::env::var("TH_MEDIA_BACKEND_URL")
                .unwrap_or_else(|_| "http://127.0.0.1:8012".to_string());
            let auth_token = std::env::var("TH_MEDIA_AUTH_TOKEN").ok();

            let runtime = serde_json::json!({
                "backendBaseUrl": backend_base_url,
                "authToken": auth_token,
            });
            let init_script = format!(
                "window.__TH_MEDIA_RUNTIME__ = {};",
                runtime
            );

            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("TH Media")
                .inner_size(1440.0, 900.0)
                .min_inner_size(1100.0, 720.0)
                .center()
                .initialization_script(&init_script)
                .build()?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("failed to run TH Media desktop");
}
