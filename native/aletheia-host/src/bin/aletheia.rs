use aletheia_host::{
    activate_release, current_release, gateway_ready, open_dashboard, payload_command,
    read_selection, rollback_release, run_payload, wait_gateway_ready, HostPaths,
};
use anyhow::{bail, Context, Result};
use std::env;
use std::process::{self, Command};
use std::time::Duration;

fn print_host_help() {
    eprintln!(
        "Internal packaged-host commands:\n\
         aletheia __host status\n\
         aletheia __host activate <release-id>\n\
         aletheia __host rollback\n\
         aletheia __host ensure-started\n\
         aletheia __host open-dashboard\n\
         aletheia __host smoke-test"
    );
}

fn ensure_started_without_browser(paths: &HostPaths) -> Result<()> {
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
    if !wait_gateway_ready(Duration::from_secs(30)) {
        bail!("packaged gateway did not become ready within 30 seconds");
    }
    Ok(())
}

fn run_internal(paths: &HostPaths, args: &[String]) -> Result<i32> {
    match args.first().map(String::as_str) {
        Some("status") => {
            let selection = read_selection(paths)?;
            let release = current_release(paths)?;
            println!(
                "{}",
                serde_json::json!({
                    "schema_version": selection.schema_version,
                    "current": selection.current,
                    "previous": selection.previous,
                    "runtime_entrypoint": release.executable,
                    "platform": release.manifest.platform,
                    "version": release.manifest.version,
                    "git_sha": release.manifest.git_sha,
                })
            );
            Ok(0)
        }
        Some("activate") => {
            let id = args.get(1).context("activate requires a release id")?;
            let selection = activate_release(paths, id)?;
            println!("selected release {}", selection.current);
            Ok(0)
        }
        Some("rollback") => {
            let selection = rollback_release(paths)?;
            println!("rolled back to release {}", selection.current);
            Ok(0)
        }
        Some("ensure-started") => {
            ensure_started_without_browser(paths)?;
            println!("Aletheia gateway is ready");
            Ok(0)
        }
        Some("open-dashboard") => {
            open_dashboard()?;
            Ok(0)
        }
        Some("smoke-test") => {
            let release = current_release(paths)?;
            let version_status = Command::new(&release.executable)
                .arg("version")
                .current_dir(&release.root)
                .env(aletheia_host::PACKAGED_ENV, "1")
                .env(aletheia_host::HOST_ROOT_ENV, &paths.root)
                .status()
                .context("run packaged version smoke")?;
            if !version_status.success() {
                bail!("packaged version smoke failed with status {version_status}");
            }
            ensure_started_without_browser(paths)?;
            println!("packaged CLI and gateway smoke passed");
            Ok(0)
        }
        _ => {
            print_host_help();
            Ok(2)
        }
    }
}

fn forward_to_payload(paths: &HostPaths, args: &[String]) -> Result<i32> {
    let mut command = payload_command(paths, args)?;

    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        let error = command.exec();
        Err(error).context("replace stable CLI launcher with selected Aletheia payload")
    }

    #[cfg(not(unix))]
    {
        let status = command.status().context("run selected Aletheia payload")?;
        Ok(status.code().unwrap_or(1))
    }
}

fn real_main() -> Result<i32> {
    let paths = HostPaths::discover()?;
    let args: Vec<String> = env::args().skip(1).collect();
    if args.first().map(String::as_str) == Some("__host") {
        return run_internal(&paths, &args[1..]);
    }
    forward_to_payload(&paths, &args)
}

fn main() {
    match real_main() {
        Ok(code) => process::exit(code),
        Err(error) => {
            eprintln!("aletheia: {error:#}");
            process::exit(1);
        }
    }
}
