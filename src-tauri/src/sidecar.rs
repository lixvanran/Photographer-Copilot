//! Python sidecar lifecycle management.
//!
//! - `ensure_started` spawns the Python sidecar as a child process
//! - `sidecar_url` returns the sidecar's HTTP base URL (reads from a port file
//!   the sidecar writes on startup)
//! - On app exit, the sidecar is terminated

use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

use tauri::{AppHandle, Manager};

use crate::workspace::workspace_path;

static SIDECAR: Mutex<Option<Child>> = Mutex::new(None);

/// Project root: the parent of the data workspace dir.
fn project_root() -> PathBuf {
    let ws = workspace_path();
    ws.parent()
        .map(|p| p.to_path_buf())
        .unwrap_or_else(|| ws.clone())
}

/// Find the path to the Python interpreter to use for the sidecar.
fn python_executable() -> PathBuf {
    let root = project_root();
    let venv_python = if cfg!(target_os = "windows") {
        root.join("sidecar").join(".venv").join("Scripts").join("python.exe")
    } else {
        root.join("sidecar").join(".venv").join("bin").join("python")
    };
    if venv_python.exists() {
        return venv_python;
    }
    for name in &["python3", "python"] {
        if let Ok(p) = which(name) {
            return p;
        }
    }
    PathBuf::from("python3")
}

fn which(name: &str) -> Result<PathBuf, ()> {
    let path_var = std::env::var("PATH").unwrap_or_default();
    for dir in std::env::split_paths(&path_var) {
        let candidate = dir.join(name);
        if candidate.is_file() {
            return Ok(candidate);
        }
        #[cfg(target_os = "windows")]
        {
            let candidate_exe = dir.join(format!("{}.exe", name));
            if candidate_exe.is_file() {
                return Ok(candidate_exe);
            }
        }
    }
    Err(())
}

/// Ensure the sidecar is running. Starts it if not.
pub async fn ensure_started(_app: &AppHandle) -> Result<u16, String> {
    if let Ok(port) = read_sidecar_port() {
        return Ok(port);
    }

    let root = project_root();
    let sidecar_dir = root.join("sidecar");
    if !sidecar_dir.exists() {
        return Err(format!("Sidecar dir not found: {}", sidecar_dir.display()));
    }

    let python = python_executable();
    let cmd = Command::new(&python);
    cmd.arg("-m")
        .arg("agent.main")
        .current_dir(&sidecar_dir)
        .env("PYTHONUNBUFFERED", "1")
        .env("WORKSPACE_PATH", workspace_path())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    for var in &["M3_BASE_URL", "M3_API_KEY", "M3_MODEL", "LOG_LEVEL"] {
        if let Ok(v) = std::env::var(var) {
            cmd.env(var, v);
        }
    }

    tracing::info!("Spawning sidecar: python={:?}, cwd={:?}", python, sidecar_dir);
    let child = cmd
        .spawn()
        .map_err(|e| format!("Failed to spawn sidecar (python={:?}): {}", python, e))?;

    {
        let mut guard = SIDECAR.lock().unwrap();
        *guard = Some(child);
    }

    for _ in 0..150 {
        tokio::time::sleep(Duration::from_millis(100)).await;
        if let Ok(port) = read_sidecar_port() {
            return Ok(port);
        }
    }

    Err("Sidecar did not write port file within 15s".to_string())
}

fn read_sidecar_port() -> Result<u16, String> {
    let port_file = workspace_path().join(".sidecar-port");
    if !port_file.exists() {
        return Err("port file not yet written".to_string());
    }
    let s = std::fs::read_to_string(&port_file)
        .map_err(|e| format!("read port file: {}", e))?;
    let n: u16 = s.trim().parse().map_err(|e| format!("parse port: {}", e))?;
    Ok(n)
}

pub fn sidecar_url() -> Result<String, String> {
    let port = read_sidecar_port()?;
    Ok(format!("http://127.0.0.1:{}", port))
}

/// Stop the sidecar if it's running.
pub fn stop() {
    let mut guard = SIDECAR.lock().unwrap();
    if let Some(mut child) = guard.take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}
