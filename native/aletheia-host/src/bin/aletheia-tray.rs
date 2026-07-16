use aletheia_host::{
    ensure_gateway_started, gateway_healthy, open_dashboard, run_gateway_action, HostPaths,
};
use anyhow::{Context, Result};
use std::env;
use std::process;
use std::time::{Duration, Instant};
use tray_icon::menu::{Menu, MenuEvent, MenuItem, PredefinedMenuItem};
use tray_icon::{Icon, TrayIconBuilder};
use winit::event::Event;
use winit::event_loop::{ControlFlow, EventLoop};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum TrayState {
    Starting,
    Healthy,
    Degraded,
    Stopped,
}

impl TrayState {
    fn label(self) -> &'static str {
        match self {
            Self::Starting => "Aletheia: starting",
            Self::Healthy => "Aletheia: running",
            Self::Degraded => "Aletheia: degraded",
            Self::Stopped => "Aletheia: stopped",
        }
    }

    fn rgba(self) -> [u8; 4] {
        match self {
            Self::Starting => [48, 132, 255, 255],
            Self::Healthy => [40, 190, 95, 255],
            Self::Degraded => [240, 160, 32, 255],
            Self::Stopped => [130, 130, 130, 255],
        }
    }
}

fn icon_for(state: TrayState) -> Result<Icon> {
    const SIZE: u32 = 22;
    let mut rgba = vec![0_u8; (SIZE * SIZE * 4) as usize];
    let center = (SIZE as f32 - 1.0) / 2.0;
    let radius = center - 2.0;
    let color = state.rgba();
    for y in 0..SIZE {
        for x in 0..SIZE {
            let dx = x as f32 - center;
            let dy = y as f32 - center;
            let offset = ((y * SIZE + x) * 4) as usize;
            if dx * dx + dy * dy <= radius * radius {
                rgba[offset..offset + 4].copy_from_slice(&color);
            }
        }
    }
    Icon::from_rgba(rgba, SIZE, SIZE).context("create tray status icon")
}

fn observed_state() -> TrayState {
    if aletheia_host::gateway_ready() {
        if gateway_healthy() {
            TrayState::Healthy
        } else {
            TrayState::Degraded
        }
    } else {
        TrayState::Stopped
    }
}

fn smoke_test(paths: &HostPaths) -> Result<()> {
    ensure_gateway_started(paths, Duration::from_secs(30))?;
    if !gateway_healthy() {
        anyhow::bail!("packaged gateway reached readiness but failed business health");
    }
    println!("Aletheia tray smoke passed: {}", aletheia_host::DASHBOARD_URL);
    Ok(())
}

fn real_main() -> Result<()> {
    let paths = HostPaths::discover()?;
    paths.ensure()?;
    if env::args().any(|arg| arg == "--smoke-test") {
        return smoke_test(&paths);
    }

    let event_loop = EventLoop::new().context("create tray event loop")?;
    let menu = Menu::new();
    let status_item = MenuItem::new(TrayState::Starting.label(), false, None);
    let open_item = MenuItem::new("Open Aletheia", true, None);
    let restart_item = MenuItem::new("Restart Runtime", true, None);
    let stop_item = MenuItem::new("Stop Runtime", true, None);
    let quit_item = MenuItem::new("Quit Aletheia", true, None);
    menu.append(&status_item)?;
    menu.append(&PredefinedMenuItem::separator())?;
    menu.append(&open_item)?;
    menu.append(&restart_item)?;
    menu.append(&stop_item)?;
    menu.append(&PredefinedMenuItem::separator())?;
    menu.append(&quit_item)?;

    let mut state = TrayState::Starting;
    let tray = TrayIconBuilder::new()
        .with_menu(Box::new(menu))
        .with_tooltip(state.label())
        .with_icon(icon_for(state)?)
        .build()
        .context("create Aletheia tray icon")?;

    let _ = ensure_gateway_started(&paths, Duration::from_secs(30));
    let menu_events = MenuEvent::receiver();
    let mut next_poll = Instant::now();

    event_loop
        .run(move |event, event_loop| {
            event_loop.set_control_flow(ControlFlow::WaitUntil(next_poll));

            while let Ok(menu_event) = menu_events.try_recv() {
                if menu_event.id == open_item.id() {
                    let _ = open_dashboard();
                } else if menu_event.id == restart_item.id() {
                    state = TrayState::Starting;
                    let _ = status_item.set_text(state.label());
                    let _ = tray.set_tooltip(Some(state.label()));
                    let _ = tray.set_icon(Some(icon_for(state).ok()).flatten());
                    let _ = run_gateway_action(&paths, "restart");
                } else if menu_event.id == stop_item.id() {
                    let _ = run_gateway_action(&paths, "stop");
                } else if menu_event.id == quit_item.id() {
                    let _ = run_gateway_action(&paths, "stop");
                    event_loop.exit();
                }
            }

            if matches!(event, Event::AboutToWait) && Instant::now() >= next_poll {
                let observed = observed_state();
                if observed != state {
                    state = observed;
                    let _ = status_item.set_text(state.label());
                    let _ = tray.set_tooltip(Some(state.label()));
                    if let Ok(icon) = icon_for(state) {
                        let _ = tray.set_icon(Some(icon));
                    }
                }
                next_poll = Instant::now() + Duration::from_secs(2);
                event_loop.set_control_flow(ControlFlow::WaitUntil(next_poll));
            }
        })
        .context("run Aletheia tray event loop")?;
    Ok(())
}

fn main() {
    if let Err(error) = real_main() {
        eprintln!("aletheia-tray: {error:#}");
        process::exit(1);
    }
}
