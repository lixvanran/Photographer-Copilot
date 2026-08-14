//! Tauri commands exposed to the frontend.
//!
//! All file/mutating operations go through here. Each command is a thin
//! wrapper that:
//! 1. Validates paths via `workspace::ensure_within_workspace`
//! 2. Calls the sidecar HTTP API
//! 3. Returns the result to the frontend
//!
//! Long-running operations (grade/cull) run in the background; the
//! frontend subscribes to events via Tauri event system.

use std::path::PathBuf;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager};

use crate::sidecar;
use crate::workspace;


/// Make an HTTP GET to the sidecar.
async fn http_get<T: for<'de> Deserialize<'de>>(path: &str) -> Result<T, String> {
    let url = format!("{}{}", sidecar::sidecar_url()?, path);
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(30))
        .build()
        .map_err(|e| e.to_string())?;
    let resp = client.get(&url).send().await.map_err(|e| e.to_string())?;
    let status = resp.status();
    let text = resp.text().await.map_err(|e| e.to_string())?;
    if !status.is_success() {
        return Err(format!("sidecar GET {}: {} {}", path, status, text));
    }
    serde_json::from_str(&text).map_err(|e| format!("decode JSON: {} (body: {})", e, &text[..text.len().min(200)]))
}

async fn http_post<B: Serialize, T: for<'de> Deserialize<'de>>(path: &str, body: B) -> Result<T, String> {
    let url = format!("{}{}", sidecar::sidecar_url()?, path);
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(60))
        .build()
        .map_err(|e| e.to_string())?;
    let resp = client.post(&url).json(&body).send().await.map_err(|e| e.to_string())?;
    let status = resp.status();
    let text = resp.text().await.map_err(|e| e.to_string())?;
    if !status.is_success() {
        return Err(format!("sidecar POST {}: {} {}", path, status, text));
    }
    serde_json::from_str(&text).map_err(|e| format!("decode JSON: {} (body: {})", e, &text[..text.len().min(200)]))
}

#[derive(Serialize)]
struct GradeRequest {
    folder_name: String,
    scene_hint: Option<String>,
}

#[derive(Deserialize)]
struct ChatChunk {
    event: String,
    #[serde(default)]
    payload: serde_json::Value,
}

#[tauri::command]
pub async fn sidecar_health(app: AppHandle) -> Result<serde_json::Value, String> {
    crate::workspace::init(&app);
    sidecar::ensure_started(&app).await?;
    http_get("/health").await
}

#[tauri::command]
pub async fn get_config(app: AppHandle) -> Result<serde_json::Value, String> {
    crate::workspace::init(&app);
    sidecar::ensure_started(&app).await?;
    http_get("/config").await
}

#[tauri::command]
pub async fn list_input_folders(app: AppHandle) -> Result<serde_json::Value, String> {
    crate::workspace::init(&app);
    sidecar::ensure_started(&app).await?;
    http_get("/input/folders").await
}

#[tauri::command]
pub async fn rename_to_in(app: AppHandle, folder_name: String) -> Result<serde_json::Value, String> {
    crate::workspace::init(&app);
    let folder_path = workspace::workspace_path().join("input").join(&folder_name);
    if !folder_path.exists() {
        return Err(format!("Folder not found: {}", folder_name));
    }
    let _ = workspace::ensure_within_workspace(folder_path.to_str().unwrap())?;
    sidecar::ensure_started(&app).await?;
    http_post("/input/rename", serde_json::json!({"folder_name": folder_name})).await
}

#[tauri::command]
pub async fn start_grade_task(
    app: AppHandle,
    folder_name: String,
    scene_hint: Option<String>,
) -> Result<serde_json::Value, String> {
    crate::workspace::init(&app);
    let folder_path = workspace::workspace_path().join("input").join(&folder_name);
    let _ = workspace::ensure_within_workspace(folder_path.to_str().unwrap())?;
    sidecar::ensure_started(&app).await?;
    let req = GradeRequest { folder_name, scene_hint };
    let task: serde_json::Value = http_post("/tasks/grade", &req).await?;

    if let Some(task_id) = task.get("task_id").and_then(|v| v.as_str()) {
        let app_clone = app.clone();
        let task_id_owned = task_id.to_string();
        tauri::async_runtime::spawn(async move {
            forward_task_events(app_clone, task_id_owned).await;
        });
    }
    Ok(task)
}

#[tauri::command]
pub async fn start_cull_task(
    app: AppHandle,
    folder_name: String,
    scene_hint: Option<String>,
) -> Result<serde_json::Value, String> {
    crate::workspace::init(&app);
    let folder_path = workspace::workspace_path().join("input").join(&folder_name);
    let _ = workspace::ensure_within_workspace(folder_path.to_str().unwrap())?;
    sidecar::ensure_started(&app).await?;
    let req = GradeRequest { folder_name, scene_hint };
    let task: serde_json::Value = http_post("/tasks/cull", &req).await?;

    if let Some(task_id) = task.get("task_id").and_then(|v| v.as_str()) {
        let app_clone = app.clone();
        let task_id_owned = task_id.to_string();
        tauri::async_runtime::spawn(async move {
            forward_task_events(app_clone, task_id_owned).await;
        });
    }
    Ok(task)
}

#[tauri::command]
pub async fn get_task(app: AppHandle, task_id: String) -> Result<serde_json::Value, String> {
    crate::workspace::init(&app);
    sidecar::ensure_started(&app).await?;
    let path = format!("/tasks/{}", task_id);
    http_get(&path).await
}

#[tauri::command]
pub async fn set_photo_feedback(
    app: AppHandle,
    photo_id: i64,
    feedback: String,
) -> Result<serde_json::Value, String> {
    crate::workspace::init(&app);
    sidecar::ensure_started(&app).await?;
    let path = format!("/photos/{}/feedback", photo_id);
    http_post(&path, serde_json::json!({"feedback": feedback})).await
}

#[tauri::command]
pub async fn send_chat(app: AppHandle, message: String) -> Result<(), String> {
    crate::workspace::init(&app);
    sidecar::ensure_started(&app).await?;
    let url = sidecar::sidecar_url()? + "/chat";
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(120))
        .build()
        .map_err(|e| e.to_string())?;
    let resp = client
        .post(&url)
        .json(&serde_json::json!({"message": message}))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if !resp.status().is_success() {
        return Err(format!("chat HTTP {}", resp.status()));
    }
    let mut stream = resp.bytes_stream();
    use futures::StreamExt;
    while let Some(item) = stream.next().await {
        let bytes = item.map_err(|e| e.to_string())?;
        let text = String::from_utf8_lossy(&bytes);
        for line in text.lines() {
            if let Some(rest) = line.strip_prefix("data: ") {
                if let Ok(parsed) = serde_json::from_str::<ChatChunk>(rest) {
                    let _ = app.emit("chat:chunk", parsed.payload);
                    if parsed.event == "done" {
                        return Ok(());
                    }
                }
            }
        }
    }
    let _ = app.emit("chat:chunk", serde_json::json!({"done": true}));
    Ok(())
}

/// Subscribe to sidecar SSE for a task, forward events to frontend as `task:{id}`.
async fn forward_task_events(app: AppHandle, task_id: String) {
    let url = match sidecar::sidecar_url() {
        Ok(u) => format!("{}/events/{}", u, task_id),
        Err(e) => {
            tracing::error!("Cannot get sidecar url: {}", e);
            return;
        }
    };

    let client = match reqwest::Client::builder()
        .timeout(Duration::from_secs(60 * 30))
        .build()
    {
        Ok(c) => c,
        Err(e) => {
            tracing::error!("Cannot build http client: {}", e);
            return;
        }
    };

    let resp = match client.get(&url).send().await {
        Ok(r) => r,
        Err(e) => {
            tracing::error!("Task events stream failed: {}", e);
            return;
        }
    };

    use futures::StreamExt;
    let mut stream = resp.bytes_stream();
    let mut buffer = String::new();
    let event_name = format!("task:{}", task_id);
    while let Some(item) = stream.next().await {
        let bytes = match item {
            Ok(b) => b,
            Err(e) => {
                tracing::warn!("Stream error: {}", e);
                break;
            }
        };
        buffer.push_str(&String::from_utf8_lossy(&bytes));
        while let Some(idx) = buffer.find("\n\n") {
            let frame = buffer[..idx].to_string();
            buffer = buffer[idx + 2..].to_string();
            for line in frame.lines() {
                if let Some(rest) = line.strip_prefix("data: ") {
                    if let Ok(payload) = serde_json::from_str::<serde_json::Value>(rest) {
                        let _ = app.emit(&event_name, payload);
                    }
                }
            }
        }
    }
}
