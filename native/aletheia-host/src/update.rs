use crate::{
    activate_release, gateway_healthy, gateway_ready, read_selection, resolve_release,
    wait_gateway_ready, HostPaths, PACKAGED_ENV, HOST_ROOT_ENV,
};
use anyhow::{anyhow, bail, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::fs::{self, File};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitStatus};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

#[derive(Debug, Clone, Deserialize)]
pub struct UpdateRequest {
    pub schema_version: u32,
    pub operation_id: String,
    pub action: String,
    pub candidate_release: String,
    pub previous_release: String,
    pub backup_path: Option<String>,
    #[serde(default)]
    pub was_paused: bool,
    #[serde(default = "default_health_soak")]
    pub health_soak_sec: u64,
    #[serde(default)]
    pub requested_at: Option<String>,
    #[serde(default)]
    pub manifest: Option<Value>,
}

fn default_health_soak() -> u64 {
    30
}

#[derive(Debug, Clone, Serialize)]
pub struct UpdateTransactionResult {
    pub operation_id: String,
    pub status: String,
    pub current_release: String,
    pub previous_release: Option<String>,
    pub rolled_back: bool,
    pub message: String,
}

fn now_isoish() -> String {
    let seconds = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    format!("unix:{seconds}")
}

fn atomic_json(path: &Path, value: &impl Serialize) -> Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| anyhow!("state path has no parent: {}", path.display()))?;
    fs::create_dir_all(parent)?;
    let temporary = parent.join(format!(
        ".{}.tmp-{}",
        path.file_name().and_then(|name| name.to_str()).unwrap_or("state"),
        std::process::id()
    ));
    let bytes = serde_json::to_vec_pretty(value)?;
    {
        let mut file = File::create(&temporary)?;
        file.write_all(&bytes)?;
        file.write_all(b"\n")?;
        file.sync_all()?;
    }
    fs::rename(&temporary, path)
        .with_context(|| format!("atomically replace {}", path.display()))?;
    #[cfg(unix)]
    File::open(parent)?.sync_all()?;
    Ok(())
}

fn write_state(
    paths: &HostPaths,
    request: &UpdateRequest,
    *,
    phase: &str,
    status: &str,
    message: &str,
    error: Option<&str>,
) -> Result<()> {
    let selection = read_selection(paths).ok();
    atomic_json(
        &paths.update_state_file,
        &serde_json::json!({
            "schema_version": 1,
            "mode": "packaged",
            "operation_id": request.operation_id,
            "phase": phase,
            "status": status,
            "current_release": selection.as_ref().map(|value| value.current.clone()),
            "previous_release": selection.and_then(|value| value.previous),
            "target_release": request.candidate_release,
            "message": message,
            "error": error,
            "updated_at": now_isoish(),
        }),
    )
}

pub fn pending_request_path(paths: &HostPaths) -> PathBuf {
    paths.root.join("update-request.json")
}

pub fn has_pending_request(paths: &HostPaths) -> bool {
    pending_request_path(paths).is_file()
}

pub fn read_pending_request(paths: &HostPaths) -> Result<UpdateRequest> {
    let path = pending_request_path(paths);
    let bytes = fs::read(&path).with_context(|| format!("read update request {}", path.display()))?;
    let request: UpdateRequest = serde_json::from_slice(&bytes)
        .with_context(|| format!("parse update request {}", path.display()))?;
    if request.schema_version != 1 {
        bail!("unsupported update request schema: {}", request.schema_version);
    }
    if request.operation_id.is_empty() {
        bail!("update request operation id is empty");
    }
    if !matches!(request.action.as_str(), "activate" | "rollback") {
        bail!("unsupported update action: {}", request.action);
    }
    resolve_release(paths, &request.candidate_release)?;
    resolve_release(paths, &request.previous_release)?;
    if let Some(path) = &request.backup_path {
        let backup = Path::new(path);
        if !backup.is_absolute() || !backup.is_file() {
            bail!("update backup path is missing or not absolute: {path}");
        }
    }
    Ok(request)
}

fn command_for_release(paths: &HostPaths, release_id: &str, args: &[&str]) -> Result<Command> {
    let release = resolve_release(paths, release_id)?;
    let mut command = Command::new(&release.executable);
    command.args(args);
    command.current_dir(&release.root);
    command.env(PACKAGED_ENV, "1");
    command.env(HOST_ROOT_ENV, &paths.root);
    Ok(command)
}

fn run_release(paths: &HostPaths, release_id: &str, args: &[&str]) -> Result<ExitStatus> {
    command_for_release(paths, release_id, args)?
        .status()
        .with_context(|| format!("run release {release_id} with arguments {args:?}"))
}

fn require_success(status: ExitStatus, operation: &str) -> Result<()> {
    if status.success() {
        Ok(())
    } else {
        bail!("{operation} failed with status {status}")
    }
}

fn stop_release(paths: &HostPaths, release_id: &str) {
    let _ = run_release(paths, release_id, &["gateway", "stop"]);
}

fn pause_release(paths: &HostPaths, release_id: &str) -> Result<()> {
    if !gateway_ready() {
        return Ok(());
    }
    require_success(
        run_release(paths, release_id, &["gateway", "pause"] )?,
        "pause current runtime",
    )
}

fn start_release(paths: &HostPaths, release_id: &str) -> Result<()> {
    require_success(
        run_release(
            paths,
            release_id,
            &["gateway", "start", "--no-browser"],
        )?,
        "start selected runtime",
    )?;
    if !wait_gateway_ready(Duration::from_secs(45)) {
        bail!("selected runtime did not become ready within 45 seconds");
    }
    if !gateway_healthy() {
        bail!("selected runtime became ready but failed business health");
    }
    Ok(())
}

fn resume_release(paths: &HostPaths, release_id: &str) -> Result<()> {
    require_success(
        run_release(paths, release_id, &["gateway", "resume"] )?,
        "resume selected runtime",
    )
}

fn validate_real_data(paths: &HostPaths, release_id: &str) -> Result<()> {
    require_success(
        run_release(paths, release_id, &["__packaged_update_validate"] )?,
        "apply and validate candidate migrations",
    )
}

fn restore_backup(paths: &HostPaths, release_id: &str, backup: &Path) -> Result<()> {
    let backup_text = backup
        .to_str()
        .ok_or_else(|| anyhow!("update backup path is not UTF-8"))?;
    require_success(
        run_release(
            paths,
            release_id,
            &["__packaged_update_restore", backup_text],
        )?,
        "restore pre-update backup",
    )
}

fn soak_health(seconds: u64) -> Result<()> {
    let bounded = seconds.min(120);
    let deadline = Instant::now() + Duration::from_secs(bounded);
    while Instant::now() < deadline {
        if !gateway_healthy() {
            bail!("runtime health failed during post-update soak");
        }
        thread::sleep(Duration::from_secs(1));
    }
    Ok(())
}

fn recover_previous(
    paths: &HostPaths,
    request: &UpdateRequest,
    failure: &str,
) -> Result<UpdateTransactionResult> {
    let _ = write_state(
        paths,
        request,
        phase: "rolling_back",
        status: "running",
        message: "New runtime failed; restoring previous release",
        error: Some(failure),
    );
    stop_release(paths, &request.candidate_release);
    activate_release(paths, &request.previous_release)?;
    if let Some(backup_path) = &request.backup_path {
        restore_backup(paths, &request.previous_release, Path::new(backup_path))?;
    }
    start_release(paths, &request.previous_release)?;
    if !request.was_paused {
        resume_release(paths, &request.previous_release)?;
    }
    write_state(
        paths,
        request,
        phase: "rolled_back",
        status: "rolled_back",
        message: "Update failed and the previous release was restored",
        error: Some(failure),
    )?;
    pending_request_path(paths).unlink_if_exists()?;
    let selection = read_selection(paths)?;
    Ok(UpdateTransactionResult {
        operation_id: request.operation_id.clone(),
        status: "rolled_back".into(),
        current_release: selection.current,
        previous_release: selection.previous,
        rolled_back: true,
        message: "Update failed and the previous release was restored".into(),
    })
}

trait UnlinkIfExists {
    fn unlink_if_exists(&self) -> Result<()>;
}

impl UnlinkIfExists for PathBuf {
    fn unlink_if_exists(&self) -> Result<()> {
        match fs::remove_file(self) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(error.into()),
        }
    }
}

pub fn process_pending_update(paths: &HostPaths) -> Result<Option<UpdateTransactionResult>> {
    if !has_pending_request(paths) {
        return Ok(None);
    }
    let request = read_pending_request(paths)?;
    let current = read_selection(paths)?;
    if current.current != request.previous_release && current.current != request.candidate_release {
        bail!(
            "update request expected current release {} or {}, found {}",
            request.previous_release,
            request.candidate_release,
            current.current
        );
    }

    let transaction = (|| -> Result<UpdateTransactionResult> {
        if current.current == request.previous_release {
            write_state(
                paths,
                &request,
                phase: "quiescing",
                status: "running",
                message: "Pausing autonomous work and stopping the current runtime",
                error: None,
            )?;
            if !request.was_paused {
                pause_release(paths, &request.previous_release)?;
            }
            stop_release(paths, &request.previous_release);
            activate_release(paths, &request.candidate_release)?;
        } else {
            stop_release(paths, &request.candidate_release);
        }

        write_state(
            paths,
            &request,
            phase: "migrating",
            status: "running",
            message: "Applying preflighted migrations to durable runtime data",
            error: None,
        )?;
        validate_real_data(paths, &request.candidate_release)?;

        write_state(
            paths,
            &request,
            phase: "starting",
            status: "running",
            message: "Starting the selected runtime",
            error: None,
        )?;
        start_release(paths, &request.candidate_release)?;

        write_state(
            paths,
            &request,
            phase: "soaking",
            status: "running",
            message: "Observing the new runtime before committing the update",
            error: None,
        )?;
        soak_health(request.health_soak_sec)?;
        if !request.was_paused {
            resume_release(paths, &request.candidate_release)?;
        }

        write_state(
            paths,
            &request,
            phase: "committed",
            status: "success",
            message: "Update installed successfully",
            error: None,
        )?;
        pending_request_path(paths).unlink_if_exists()?;
        let selection = read_selection(paths)?;
        Ok(UpdateTransactionResult {
            operation_id: request.operation_id.clone(),
            status: "success".into(),
            current_release: selection.current,
            previous_release: selection.previous,
            rolled_back: false,
            message: "Update installed successfully".into(),
        })
    })();

    match transaction {
        Ok(result) => Ok(Some(result)),
        Err(error) => {
            let failure = format!("{error:#}");
            recover_previous(paths, &request, &failure).map(Some)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{host_platform_id, ReleaseManifest, ReleaseSelection};
    use std::os::unix::fs::PermissionsExt;
    use tempfile::TempDir;

    fn fake_release(paths: &HostPaths, id: &str) {
        let root = paths.releases.join(id);
        fs::create_dir_all(&root).unwrap();
        let executable = root.join("aletheia-core");
        fs::write(&executable, b"#!/bin/sh\nexit 0\n").unwrap();
        let mut permissions = executable.metadata().unwrap().permissions();
        permissions.set_mode(0o755);
        fs::set_permissions(&executable, permissions).unwrap();
        let manifest = ReleaseManifest {
            schema_version: 1,
            target: "packaged-runtime".into(),
            platform: host_platform_id(),
            version: id.into(),
            git_sha: id.into(),
            runtime_entrypoint: "aletheia-core".into(),
            dashboard_url: None,
            runtime_data_contract: None,
        };
        fs::write(
            root.join("release-manifest.json"),
            serde_json::to_vec_pretty(&manifest).unwrap(),
        )
        .unwrap();
    }

    #[test]
    fn request_rejects_missing_or_relative_backup() {
        let temp = TempDir::new().unwrap();
        let paths = HostPaths::from_root(temp.path().join("host"));
        paths.ensure().unwrap();
        fake_release(&paths, "old");
        fake_release(&paths, "new");
        atomic_json(
            &paths.current_file,
            &ReleaseSelection::initial("old"),
        )
        .unwrap();
        atomic_json(
            &pending_request_path(&paths),
            &serde_json::json!({
                "schema_version": 1,
                "operation_id": "update-test",
                "action": "activate",
                "candidate_release": "new",
                "previous_release": "old",
                "backup_path": "relative.tar.gz",
                "was_paused": false,
                "health_soak_sec": 0
            }),
        )
        .unwrap();
        assert!(read_pending_request(&paths).is_err());
    }
}
