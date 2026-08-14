//! Tauri entry point.
//!
//! Wires up:
//! - System tray
//! - Commands exposed to the frontend
//! - Python sidecar lifecycle
//! - App-level event broadcasting

mod commands;
mod sidecar;
mod workspace;

use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Emitter, Manager,
};
use tracing::info;
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt, EnvFilter};

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // Init logging: file (workspace/.logs/rust.log) + stderr
    let env_filter = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new("info,photographer_copilot_lib=debug"));

    let logs_dir = workspace::workspace_path().join(".logs");
    let _ = std::fs::create_dir_all(&logs_dir);
    let log_file = logs_dir.join("rust.log");
    let file_appender = tracing_appender::rolling::daily(&logs_dir, "rust.log");
    let (non_blocking, _guard) = tracing_appender::non_blocking(file_appender);

    tracing_subscriber::registry()
        .with(env_filter)
        .with(tracing_subscriber::fmt::layer().with_writer(non_blocking))
        .with(tracing_subscriber::fmt::layer().with_writer(std::io::stderr))
        .init();

    info!("Starting Photographer Copilot...");

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            commands::sidecar_health,
            commands::get_config,
            commands::list_input_folders,
            commands::rename_to_in,
            commands::start_grade_task,
            commands::start_cull_task,
            commands::get_task,
            commands::set_photo_feedback,
            commands::send_chat,
        ])
        .setup(|app| {
            // ---- Tray icon ----
            let show_item = MenuItem::with_id(app, "show", "显示主窗口", true, None::<&str>)?;
            let pause_item = MenuItem::with_id(app, "pause", "暂停监听", true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show_item, &pause_item, &quit_item])?;

            let _tray = TrayIconBuilder::with_id("main")
                .tooltip("摄影师助手")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                    "pause" => {
                        let _ = app.emit("system:message", serde_json::json!({
                            "level": "info",
                            "text": "监听已暂停"
                        }));
                    }
                    "quit" => {
                        app.exit(0);
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        let app = tray.app_handle();
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                })
                .build(app)?;

            // ---- Start Python sidecar ----
            let app_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                match sidecar::ensure_started(&app_handle).await {
                    Ok(port) => {
                        info!("Sidecar running on port {}", port);
                        let _ = app_handle.emit(
                            "system:message",
                            serde_json::json!({
                                "level": "info",
                                "text": format!("Sidecar 已就绪 (port {})", port)
                            }),
                        );
                    }
                    Err(e) => {
                        tracing::error!("Failed to start sidecar: {}", e);
                        let _ = app_handle.emit(
                            "system:message",
                            serde_json::json!({
                                "level": "error",
                                "text": format!("Sidecar 启动失败:{}", e)
                            }),
                        );
                    }
                }
            });

            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                // Hide instead of quit
                let _ = window.hide();
                api.prevent_close();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
