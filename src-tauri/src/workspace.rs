//! Workspace path resolution and validation.

use std::path::PathBuf;
use std::sync::OnceLock;
use tauri::Manager;

static WORKSPACE: OnceLock<PathBuf> = OnceLock::new();

fn resolve_workspace() -> PathBuf {
    if let Ok(p) = std::env::var("WORKSPACE_PATH") {
        let path = PathBuf::from(p);
        if path.is_absolute() {
            return path;
        }
    }
    if let Ok(cwd) = std::env::current_dir() {
        return cwd.join("workspace");
    }
    PathBuf::from("workspace")
}

pub fn workspace_path() -> PathBuf {
    WORKSPACE.get().cloned().unwrap_or_else(resolve_workspace)
}

pub fn init(_app: &tauri::AppHandle) {
    if WORKSPACE.get().is_some() {
        return;
    }
    let path = resolve_workspace();
    if let Ok(resource) = _app.path().resource_dir() {
        let candidate = resource.join("workspace");
        if candidate.exists() {
            let _ = WORKSPACE.set(candidate);
            return;
        }
    }
    let _ = WORKSPACE.set(path);
}

pub fn ensure_within_workspace(path: &str) -> Result<PathBuf, String> {
    let ws = workspace_path();
    let canonical_ws = ws
        .canonicalize()
        .map_err(|e| format!("Workspace not accessible: {}", e))?;
    let target = std::path::Path::new(path);
    let canonical_target = if target.is_absolute() {
        target
            .canonicalize()
            .map_err(|e| format!("Path not accessible: {}", e))?
    } else {
        canonical_ws.join(target).canonicalize().map_err(|e| {
            format!("Path not accessible: {}", e)
        })?
    };
    if !canonical_target.starts_with(&canonical_ws) {
        return Err(format!(
            "Path '{}' is outside workspace '{}'",
            canonical_target.display(),
            canonical_ws.display()
        ));
    }
    Ok(canonical_target)
}
