use crate::{host_platform_id, read_selection, HostPaths, ReleaseManifest, HOST_ROOT_ENV, PACKAGED_ENV};
use anyhow::{anyhow, bail, Context, Result};
use std::env;
use std::fs;
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};

pub const BOOTSTRAP_ROOT_ENV: &str = "ALETHEIA_BOOTSTRAP_ROOT";
pub const BOOTSTRAP_PUBLIC_KEY_ENV: &str = "ALETHEIA_BOOTSTRAP_PUBLIC_KEY";
pub const BOOTSTRAP_FEED_URL_ENV: &str = "ALETHEIA_BOOTSTRAP_FEED_URL_FILE";

#[derive(Debug, Clone)]
pub struct BootstrapBundle {
    pub source_root: PathBuf,
    pub executable: PathBuf,
    pub public_key: Option<PathBuf>,
    pub feed_url_file: Option<PathBuf>,
}

fn contained_relative_path(value: &str) -> Result<PathBuf> {
    let path = Path::new(value);
    if path.is_absolute() || path.as_os_str().is_empty() {
        bail!("bootstrap runtime entrypoint must be a non-empty relative path");
    }
    if path
        .components()
        .any(|component| !matches!(component, Component::Normal(_)))
    {
        bail!("bootstrap runtime entrypoint escapes its bundle: {value:?}");
    }
    Ok(path.to_path_buf())
}

fn optional_absolute_file(env_name: &str, fallback: PathBuf) -> Result<Option<PathBuf>> {
    if let Some(value) = env::var_os(env_name).filter(|value| !value.is_empty()) {
        let path = PathBuf::from(value);
        if !path.is_absolute() || !path.is_file() {
            bail!("{env_name} must point to an existing absolute file");
        }
        return Ok(Some(path));
    }
    Ok(fallback.is_file().then_some(fallback))
}

pub fn bootstrap_bundle_from_root(source_root: &Path) -> Result<BootstrapBundle> {
    let source_root = source_root
        .canonicalize()
        .with_context(|| format!("resolve bootstrap runtime {}", source_root.display()))?;
    if !source_root.is_dir() {
        bail!("bootstrap runtime is not a directory: {}", source_root.display());
    }

    let manifest_path = source_root.join("release-manifest.json");
    let manifest: ReleaseManifest = serde_json::from_slice(
        &fs::read(&manifest_path)
            .with_context(|| format!("read bootstrap manifest {}", manifest_path.display()))?,
    )
    .with_context(|| format!("parse bootstrap manifest {}", manifest_path.display()))?;
    if manifest.schema_version != 1 || manifest.target != "packaged-runtime" {
        bail!("bootstrap bundle is not a supported packaged runtime");
    }
    if manifest.platform != host_platform_id() {
        bail!(
            "bootstrap runtime targets {}, but this host is {}",
            manifest.platform,
            host_platform_id()
        );
    }

    let requested_executable = source_root.join(contained_relative_path(&manifest.runtime_entrypoint)?);
    let executable = requested_executable
        .canonicalize()
        .with_context(|| format!("resolve bootstrap entrypoint {}", requested_executable.display()))?;
    if !executable.starts_with(&source_root) || !executable.is_file() {
        bail!("bootstrap entrypoint escapes its bundle or is not a file");
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if executable.metadata()?.permissions().mode() & 0o111 == 0 {
            bail!("bootstrap entrypoint is not executable: {}", executable.display());
        }
    }

    let resources = source_root
        .parent()
        .and_then(Path::parent)
        .map(Path::to_path_buf)
        .unwrap_or_else(|| source_root.clone());
    let public_key = optional_absolute_file(
        BOOTSTRAP_PUBLIC_KEY_ENV,
        resources.join("update-public-key.pem"),
    )?;
    let feed_url_file = optional_absolute_file(
        BOOTSTRAP_FEED_URL_ENV,
        resources.join("update-feed-url.txt"),
    )?;

    Ok(BootstrapBundle {
        source_root,
        executable,
        public_key,
        feed_url_file,
    })
}

fn bootstrap_candidates() -> Result<Vec<PathBuf>> {
    if let Some(explicit) = env::var_os(BOOTSTRAP_ROOT_ENV).filter(|value| !value.is_empty()) {
        let path = PathBuf::from(explicit);
        if !path.is_absolute() {
            bail!("{BOOTSTRAP_ROOT_ENV} must be absolute");
        }
        return Ok(vec![path]);
    }

    let executable = env::current_exe().context("resolve Aletheia host executable")?;
    let executable_dir = executable
        .parent()
        .ok_or_else(|| anyhow!("Aletheia host executable has no parent directory"))?;
    let mut candidates = Vec::new();

    // macOS app bundle: Contents/MacOS/aletheia[-tray] -> Contents/Resources/runtime/aletheia-core
    if let Some(contents) = executable_dir.parent() {
        candidates.push(contents.join("Resources/runtime/aletheia-core"));
        candidates.push(contents.join("runtime/aletheia-core"));
    }
    // Portable development/install layouts used by smoke tests and future Windows packages.
    candidates.push(executable_dir.join("runtime/aletheia-core"));
    candidates.push(executable_dir.join("aletheia-core"));
    candidates.dedup();
    Ok(candidates)
}

pub fn discover_bootstrap_bundle() -> Result<BootstrapBundle> {
    let candidates = bootstrap_candidates()?;
    let mut failures = Vec::new();
    for candidate in candidates {
        if !candidate.exists() {
            continue;
        }
        match bootstrap_bundle_from_root(&candidate) {
            Ok(bundle) => return Ok(bundle),
            Err(error) => failures.push(format!("{}: {error:#}", candidate.display())),
        }
    }
    if failures.is_empty() {
        bail!(
            "no bundled Aletheia runtime was found; reinstall the application or set {BOOTSTRAP_ROOT_ENV}"
        );
    }
    bail!("no valid bundled Aletheia runtime was found: {}", failures.join("; "))
}

pub fn ensure_bootstrapped(paths: &HostPaths) -> Result<()> {
    paths.ensure()?;
    if paths.current_file.exists() {
        read_selection(paths)?;
        return Ok(());
    }

    let bundle = discover_bootstrap_bundle()?;
    let mut command = Command::new(&bundle.executable);
    command
        .arg("__packaged_bootstrap")
        .arg("--host-root")
        .arg(&paths.root)
        .arg("--source-root")
        .arg(&bundle.source_root)
        .env(PACKAGED_ENV, "1")
        .env(HOST_ROOT_ENV, &paths.root)
        .stdin(Stdio::null());
    if let Some(public_key) = &bundle.public_key {
        command.arg("--public-key").arg(public_key);
    }
    if let Some(feed_url_file) = &bundle.feed_url_file {
        command.arg("--feed-url-file").arg(feed_url_file);
    }
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }

    let output = command.output().context("install bundled Aletheia runtime")?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        bail!(
            "bundled runtime bootstrap failed with {}: {}",
            output.status,
            stderr.trim()
        );
    }
    read_selection(paths).context("bootstrap completed without a valid current.json")?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    fn write_fake_bundle(root: &Path, entrypoint: &str, platform: &str) {
        fs::create_dir_all(root).unwrap();
        let executable = root.join(entrypoint);
        fs::write(&executable, b"fake").unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut permissions = executable.metadata().unwrap().permissions();
            permissions.set_mode(0o755);
            fs::set_permissions(&executable, permissions).unwrap();
        }
        fs::write(
            root.join("release-manifest.json"),
            serde_json::to_vec_pretty(&serde_json::json!({
                "schema_version": 1,
                "target": "packaged-runtime",
                "platform": platform,
                "version": "0.5.0",
                "git_sha": "a".repeat(40),
                "runtime_entrypoint": entrypoint,
                "runtime_data_contract": "per-user-dot-aletheia"
            }))
            .unwrap(),
        )
        .unwrap();
    }

    #[test]
    fn bundled_runtime_manifest_is_platform_bound_and_contained() {
        let temp = TempDir::new().unwrap();
        let root = temp.path().join("Resources/runtime/aletheia-core");
        write_fake_bundle(&root, "aletheia-core", &host_platform_id());
        let bundle = bootstrap_bundle_from_root(&root).unwrap();
        assert_eq!(bundle.source_root, root.canonicalize().unwrap());
        assert!(bundle.executable.starts_with(&bundle.source_root));
    }

    #[test]
    fn bundled_runtime_rejects_entrypoint_escape() {
        let temp = TempDir::new().unwrap();
        let root = temp.path().join("runtime");
        write_fake_bundle(&root, "../outside", &host_platform_id());
        assert!(bootstrap_bundle_from_root(&root).is_err());
    }

    #[test]
    fn bundled_runtime_rejects_other_platform() {
        let temp = TempDir::new().unwrap();
        let root = temp.path().join("runtime");
        write_fake_bundle(&root, "aletheia-core", "other-platform");
        assert!(bootstrap_bundle_from_root(&root).is_err());
    }
}
