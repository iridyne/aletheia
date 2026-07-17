use anyhow::{anyhow, bail, Context, Result};
use directories::BaseDirs;
use serde::{Deserialize, Serialize};
use std::env;
use std::ffi::OsStr;
use std::fs::{self, File};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Component, Path, PathBuf};
use std::process::{Command, ExitStatus, Stdio};
use std::thread;
use std::time::{Duration, Instant};

pub const DASHBOARD_URL: &str = "http://127.0.0.1:8787";
pub const READY_PATH: &str = "/_infra/readyz";
pub const HEALTH_PATH: &str = "/api/health";
pub const PACKAGED_SMOKE_ENV: &str = "ALETHEIA_PACKAGED_SMOKE";
pub const SMOKE_DASHBOARD_PORT_ENV: &str = "ALETHEIA_SMOKE_DASHBOARD_PORT";
pub const HOST_ROOT_ENV: &str = "ALETHEIA_HOST_ROOT";
pub const DATA_DIR_ENV: &str = "ALETHEIA_DATA_DIR";
pub const PACKAGED_ENV: &str = "ALETHEIA_PACKAGED";
pub const HOST_BIN_ENV: &str = "ALETHEIA_HOST_BIN";

fn dashboard_port_from_values(smoke: Option<&str>, override_port: Option<&str>) -> u16 {
    if smoke != Some("1") {
        return 8787;
    }
    override_port
        .and_then(|value| value.parse::<u16>().ok())
        .filter(|port| *port != 0)
        .unwrap_or(8787)
}

fn dashboard_port() -> u16 {
    dashboard_port_from_values(
        env::var(PACKAGED_SMOKE_ENV).ok().as_deref(),
        env::var(SMOKE_DASHBOARD_PORT_ENV).ok().as_deref(),
    )
}

pub fn dashboard_url() -> String {
    format!("http://127.0.0.1:{}", dashboard_port())
}

#[derive(Debug, Clone)]
pub struct HostPaths {
    pub root: PathBuf,
    pub releases: PathBuf,
    pub downloads: PathBuf,
    pub current_file: PathBuf,
    pub update_state_file: PathBuf,
}

impl HostPaths {
    pub fn discover() -> Result<Self> {
        if let Some(root) = env::var_os(HOST_ROOT_ENV).filter(|value| !value.is_empty()) {
            return Ok(Self::from_root(PathBuf::from(root)));
        }
        let base = BaseDirs::new().ok_or_else(|| anyhow!("unable to resolve the user data directory"))?;
        Ok(Self::from_root(base.data_local_dir().join("Aletheia")))
    }

    pub fn from_root(root: PathBuf) -> Self {
        Self {
            releases: root.join("releases"),
            downloads: root.join("downloads"),
            current_file: root.join("current.json"),
            update_state_file: root.join("update-state.json"),
            root,
        }
    }

    pub fn ensure(&self) -> Result<()> {
        fs::create_dir_all(&self.releases)
            .with_context(|| format!("create releases directory {}", self.releases.display()))?;
        fs::create_dir_all(&self.downloads)
            .with_context(|| format!("create downloads directory {}", self.downloads.display()))?;
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ReleaseSelection {
    pub schema_version: u32,
    pub current: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub previous: Option<String>,
}

impl ReleaseSelection {
    pub fn initial(current: impl Into<String>) -> Self {
        Self {
            schema_version: 1,
            current: current.into(),
            previous: None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReleaseManifest {
    pub schema_version: u32,
    pub target: String,
    pub platform: String,
    pub version: String,
    pub git_sha: String,
    pub runtime_entrypoint: String,
    #[serde(default)]
    pub dashboard_url: Option<String>,
    #[serde(default)]
    pub runtime_data_contract: Option<String>,
}

#[derive(Debug, Clone)]
pub struct ResolvedRelease {
    pub id: String,
    pub root: PathBuf,
    pub executable: PathBuf,
    pub manifest: ReleaseManifest,
}

pub fn host_platform_id() -> String {
    let os = match env::consts::OS {
        "macos" => "darwin",
        other => other,
    };
    let arch = match env::consts::ARCH {
        "aarch64" => "arm64",
        "x86_64" => "x86_64",
        other => other,
    };
    format!("{os}-{arch}")
}

fn validate_release_id(id: &str) -> Result<()> {
    if id.is_empty() || id == "." || id == ".." || id.contains('/') || id.contains('\\') {
        bail!("invalid release id: {id:?}");
    }
    if !id
        .chars()
        .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '.' | '-' | '_' | '+'))
    {
        bail!("release id contains unsupported characters: {id:?}");
    }
    Ok(())
}

fn contained_relative_path(value: &str) -> Result<PathBuf> {
    let path = Path::new(value);
    if path.is_absolute() || path.as_os_str().is_empty() {
        bail!("runtime entrypoint must be a non-empty relative path");
    }
    for component in path.components() {
        if !matches!(component, Component::Normal(_)) {
            bail!("runtime entrypoint escapes release directory: {value:?}");
        }
    }
    Ok(path.to_path_buf())
}

pub fn read_selection(paths: &HostPaths) -> Result<ReleaseSelection> {
    let bytes = fs::read(&paths.current_file)
        .with_context(|| format!("read release selection {}", paths.current_file.display()))?;
    let selection: ReleaseSelection = serde_json::from_slice(&bytes)
        .with_context(|| format!("parse release selection {}", paths.current_file.display()))?;
    if selection.schema_version != 1 {
        bail!("unsupported release selection schema: {}", selection.schema_version);
    }
    validate_release_id(&selection.current)?;
    if let Some(previous) = &selection.previous {
        validate_release_id(previous)?;
    }
    Ok(selection)
}

pub fn resolve_release(paths: &HostPaths, id: &str) -> Result<ResolvedRelease> {
    validate_release_id(id)?;
    let managed_root = paths
        .releases
        .canonicalize()
        .with_context(|| format!("resolve managed release root {}", paths.releases.display()))?;
    let requested_root = paths.releases.join(id);
    let root = requested_root
        .canonicalize()
        .with_context(|| format!("resolve release directory {}", requested_root.display()))?;
    if !root.starts_with(&managed_root) {
        bail!("release {id} resolves outside the managed release root");
    }

    let manifest_path = root.join("release-manifest.json");
    let manifest_bytes = fs::read(&manifest_path)
        .with_context(|| format!("read release manifest {}", manifest_path.display()))?;
    let manifest: ReleaseManifest = serde_json::from_slice(&manifest_bytes)
        .with_context(|| format!("parse release manifest {}", manifest_path.display()))?;
    if manifest.schema_version != 1 || manifest.target != "packaged-runtime" {
        bail!("release {id} is not a supported packaged runtime");
    }
    if manifest.platform != host_platform_id() {
        bail!(
            "release {id} targets {}, but this host is {}",
            manifest.platform,
            host_platform_id()
        );
    }
    let relative = contained_relative_path(&manifest.runtime_entrypoint)?;
    let requested_executable = root.join(relative);
    let executable = requested_executable
        .canonicalize()
        .with_context(|| format!("resolve release runtime entrypoint {}", requested_executable.display()))?;
    if !executable.starts_with(&root) || !executable.is_file() {
        bail!("release runtime entrypoint escapes or is not a file: {}", executable.display());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if executable.metadata()?.permissions().mode() & 0o111 == 0 {
            bail!("release runtime entrypoint is not executable: {}", executable.display());
        }
    }
    Ok(ResolvedRelease {
        id: id.to_string(),
        root,
        executable,
        manifest,
    })
}

pub fn current_release(paths: &HostPaths) -> Result<ResolvedRelease> {
    let selection = read_selection(paths)?;
    resolve_release(paths, &selection.current)
}

fn atomic_write_json<T: Serialize>(path: &Path, value: &T) -> Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| anyhow!("atomic JSON path has no parent: {}", path.display()))?;
    fs::create_dir_all(parent)?;
    let temporary = parent.join(format!(
        ".{}.tmp-{}",
        path.file_name().and_then(OsStr::to_str).unwrap_or("state"),
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

pub fn activate_release(paths: &HostPaths, id: &str) -> Result<ReleaseSelection> {
    paths.ensure()?;
    resolve_release(paths, id)?;
    let next = if paths.current_file.exists() {
        let current = read_selection(paths)?;
        if current.current == id {
            current
        } else {
            ReleaseSelection {
                schema_version: 1,
                current: id.to_string(),
                previous: Some(current.current),
            }
        }
    } else {
        ReleaseSelection::initial(id)
    };
    atomic_write_json(&paths.current_file, &next)?;
    Ok(next)
}

pub fn rollback_release(paths: &HostPaths) -> Result<ReleaseSelection> {
    let current = read_selection(paths)?;
    let previous = current
        .previous
        .clone()
        .ok_or_else(|| anyhow!("no previous release is available"))?;
    resolve_release(paths, &previous)?;
    let rolled_back = ReleaseSelection {
        schema_version: 1,
        current: previous,
        previous: Some(current.current),
    };
    atomic_write_json(&paths.current_file, &rolled_back)?;
    Ok(rolled_back)
}

pub fn payload_command(paths: &HostPaths, args: &[String]) -> Result<Command> {
    let release = current_release(paths)?;
    let mut command = Command::new(&release.executable);
    command.args(args);
    command.current_dir(&release.root);
    command.env(PACKAGED_ENV, "1");
    command.env(HOST_ROOT_ENV, &paths.root);
    if let Ok(host_bin) = env::current_exe() {
        command.env(HOST_BIN_ENV, host_bin);
    }
    Ok(command)
}

pub fn run_payload(paths: &HostPaths, args: &[String]) -> Result<ExitStatus> {
    payload_command(paths, args)?
        .status()
        .with_context(|| format!("run selected Aletheia payload with arguments {args:?}"))
}

pub fn run_gateway_action(paths: &HostPaths, action: &str) -> Result<ExitStatus> {
    run_payload(paths, &["gateway".into(), action.into()])
}

fn probe_http(path: &str, timeout: Duration) -> bool {
    let port = dashboard_port();
    let address: SocketAddr = match format!("127.0.0.1:{port}").parse() {
        Ok(value) => value,
        Err(_) => return false,
    };
    let mut stream = match TcpStream::connect_timeout(&address, timeout) {
        Ok(value) => value,
        Err(_) => return false,
    };
    let _ = stream.set_read_timeout(Some(timeout));
    let _ = stream.set_write_timeout(Some(timeout));
    let request = format!(
        "GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut response = [0_u8; 128];
    let count = match stream.read(&mut response) {
        Ok(value) => value,
        Err(_) => return false,
    };
    let head = String::from_utf8_lossy(&response[..count]);
    head.starts_with("HTTP/1.1 200") || head.starts_with("HTTP/1.0 200")
}

pub fn gateway_ready() -> bool {
    probe_http(READY_PATH, Duration::from_secs(1))
}

pub fn gateway_healthy() -> bool {
    gateway_ready() && probe_http(HEALTH_PATH, Duration::from_secs(1))
}

pub fn wait_gateway_ready(timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if gateway_ready() {
            return true;
        }
        thread::sleep(Duration::from_millis(250));
    }
    false
}

pub fn ensure_gateway_started(paths: &HostPaths, timeout: Duration) -> Result<()> {
    if gateway_ready() {
        return Ok(());
    }
    let status = run_payload(
        paths,
        &["gateway".into(), "start".into(), "--no-browser".into()],
    )?;
    if !status.success() {
        bail!("packaged gateway start failed with status {status}");
    }
    if !wait_gateway_ready(timeout) {
        bail!("packaged gateway did not become ready within {timeout:?}");
    }
    Ok(())
}

pub fn open_dashboard() -> Result<()> {
    let url = dashboard_url();
    #[cfg(target_os = "macos")]
    let mut command = {
        let mut value = Command::new("open");
        value.arg(&url);
        value
    };
    #[cfg(target_os = "windows")]
    let mut command = {
        let mut value = Command::new("cmd");
        value.args(["/C", "start", "", url.as_str()]);
        value
    };
    #[cfg(all(unix, not(target_os = "macos")))]
    let mut command = {
        let mut value = Command::new("xdg-open");
        value.arg(&url);
        value
    };
    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .context("open Aletheia dashboard in the default browser")?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    fn fake_release(paths: &HostPaths, id: &str, entrypoint: &str) -> PathBuf {
        let root = paths.releases.join(id);
        fs::create_dir_all(&root).unwrap();
        let executable = root.join(entrypoint);
        fs::write(&executable, b"fake").unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut permissions = executable.metadata().unwrap().permissions();
            permissions.set_mode(0o755);
            fs::set_permissions(&executable, permissions).unwrap();
        }
        let manifest = ReleaseManifest {
            schema_version: 1,
            target: "packaged-runtime".into(),
            platform: host_platform_id(),
            version: id.into(),
            git_sha: "test".into(),
            runtime_entrypoint: entrypoint.into(),
            dashboard_url: Some(DASHBOARD_URL.into()),
            runtime_data_contract: Some("per-user-dot-aletheia".into()),
        };
        fs::write(
            root.join("release-manifest.json"),
            serde_json::to_vec_pretty(&manifest).unwrap(),
        )
        .unwrap();
        root
    }

    #[test]
    fn smoke_port_override_is_explicit_and_fail_closed() {
        assert_eq!(dashboard_port_from_values(None, Some("18787")), 8787);
        assert_eq!(dashboard_port_from_values(Some("0"), Some("18787")), 8787);
        assert_eq!(dashboard_port_from_values(Some("1"), Some("18787")), 18787);
        assert_eq!(dashboard_port_from_values(Some("1"), Some("invalid")), 8787);
        assert_eq!(dashboard_port_from_values(Some("1"), Some("0")), 8787);
    }

    #[test]
    fn activation_and_rollback_are_atomic_pointer_changes() {
        let temp = TempDir::new().unwrap();
        let paths = HostPaths::from_root(temp.path().join("host"));
        paths.ensure().unwrap();
        fake_release(&paths, "1.0.0", "aletheia-core");
        fake_release(&paths, "1.1.0", "aletheia-core");

        assert_eq!(activate_release(&paths, "1.0.0").unwrap().current, "1.0.0");
        let upgraded = activate_release(&paths, "1.1.0").unwrap();
        assert_eq!(upgraded.current, "1.1.0");
        assert_eq!(upgraded.previous.as_deref(), Some("1.0.0"));

        let rolled_back = rollback_release(&paths).unwrap();
        assert_eq!(rolled_back.current, "1.0.0");
        assert_eq!(rolled_back.previous.as_deref(), Some("1.1.0"));
        assert_eq!(read_selection(&paths).unwrap(), rolled_back);
    }

    #[test]
    fn release_paths_cannot_escape_the_release_root() {
        let temp = TempDir::new().unwrap();
        let paths = HostPaths::from_root(temp.path().join("host"));
        paths.ensure().unwrap();
        fake_release(&paths, "safe", "aletheia-core");
        assert!(resolve_release(&paths, "../safe").is_err());

        let root = paths.releases.join("bad");
        fs::create_dir_all(&root).unwrap();
        fs::write(root.join("release-manifest.json"), format!(
            "{{\"schema_version\":1,\"target\":\"packaged-runtime\",\"platform\":\"{}\",\"version\":\"bad\",\"git_sha\":\"test\",\"runtime_entrypoint\":\"../aletheia-core\"}}",
            host_platform_id()
        )).unwrap();
        assert!(resolve_release(&paths, "bad").is_err());
    }

    #[test]
    fn malformed_current_pointer_fails_closed_and_is_not_overwritten() {
        let temp = TempDir::new().unwrap();
        let paths = HostPaths::from_root(temp.path().join("host"));
        paths.ensure().unwrap();
        fake_release(&paths, "safe", "aletheia-core");
        let malformed = b"{\"schema_version\":1,\"current\":\"../../repo\"}";
        fs::write(&paths.current_file, malformed).unwrap();
        assert!(read_selection(&paths).is_err());
        assert!(activate_release(&paths, "safe").is_err());
        assert_eq!(fs::read(&paths.current_file).unwrap(), malformed);
    }
}
