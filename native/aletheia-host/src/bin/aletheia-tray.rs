use aletheia_host::{
    gateway_healthy, gateway_ready, open_dashboard, run_gateway_action, run_payload, update,
    wait_gateway_ready, HostPaths,
};
use anyhow::{bail, Context, Result};
use std::env;
use std::process;
use std::thread;
use std::time::{Duration, Instant};
use tray_icon::menu::{Menu, MenuEvent, MenuId, MenuItem, PredefinedMenuItem};
use tray_icon::{Icon, MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent};
use winit::application::ApplicationHandler;
use winit::event::{StartCause, WindowEvent};
use winit::event_loop::{ActiveEventLoop, ControlFlow, EventLoop};
use winit::window::WindowId;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum TrayState {
    Starting,
    Healthy,
    Updating,
    Degraded,
    Stopped,
}

impl TrayState {
    fn label(self) -> &'static str {
        match self {
            Self::Starting => "Aletheia: starting",
            Self::Healthy => "Aletheia: running",
            Self::Updating => "Aletheia: updating",
            Self::Degraded => "Aletheia: degraded",
            Self::Stopped => "Aletheia: stopped",
        }
    }

    fn rgba(self) -> [u8; 4] {
        match self {
            Self::Starting => [48, 132, 255, 255],
            Self::Healthy => [40, 190, 95, 255],
            Self::Updating => [242, 190, 30, 255],
            Self::Degraded => [230, 90, 60, 255],
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
    if gateway_ready() {
        if gateway_healthy() {
            TrayState::Healthy
        } else {
            TrayState::Degraded
        }
    } else {
        TrayState::Stopped
    }
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

fn restart_without_browser(paths: &HostPaths) -> Result<()> {
    let status = run_payload(
        paths,
        &["gateway".into(), "restart".into(), "--no-browser".into()],
    )?;
    if !status.success() {
        bail!("packaged gateway restart failed with status {status}");
    }
    Ok(())
}

fn stage_update(paths: &HostPaths) -> Result<bool> {
    let status = run_payload(paths, &["__packaged_update_stage".into()])?;
    if !status.success() {
        bail!("signed update staging failed with status {status}");
    }
    Ok(update::has_pending_request(paths))
}

fn smoke_test(paths: &HostPaths) -> Result<()> {
    ensure_started_without_browser(paths)?;
    if !gateway_healthy() {
        bail!("packaged gateway reached readiness but failed business health");
    }
    println!(
        "Aletheia tray smoke passed: {}",
        aletheia_host::DASHBOARD_URL
    );
    Ok(())
}

#[derive(Debug)]
enum UserEvent {
    Menu(MenuEvent),
    Tray(TrayIconEvent),
    RuntimeStartFinished(Result<(), String>),
    RuntimeRestartFinished(Result<(), String>),
    UpdateStageFinished(Result<bool, String>),
    UpdateTransactionFinished(Result<Option<update::UpdateTransactionResult>, String>),
}

struct TrayApplication {
    paths: HostPaths,
    proxy: winit::event_loop::EventLoopProxy<UserEvent>,
    tray: Option<TrayIcon>,
    status_item: Option<MenuItem>,
    open_id: Option<MenuId>,
    update_id: Option<MenuId>,
    restart_id: Option<MenuId>,
    stop_id: Option<MenuId>,
    quit_id: Option<MenuId>,
    state: TrayState,
    update_in_progress: bool,
    next_poll: Instant,
}

impl TrayApplication {
    fn new(paths: HostPaths, proxy: winit::event_loop::EventLoopProxy<UserEvent>) -> Self {
        Self {
            paths,
            proxy,
            tray: None,
            status_item: None,
            open_id: None,
            update_id: None,
            restart_id: None,
            stop_id: None,
            quit_id: None,
            state: TrayState::Starting,
            update_in_progress: false,
            next_poll: Instant::now(),
        }
    }

    fn create_tray(&mut self) -> Result<()> {
        let menu = Menu::new();
        let status_item = MenuItem::new(self.state.label(), false, None);
        let open_item = MenuItem::new("Open Aletheia", true, None);
        let update_item = MenuItem::new("Check & Install Update", true, None);
        let restart_item = MenuItem::new("Restart Runtime", true, None);
        let stop_item = MenuItem::new("Stop Runtime", true, None);
        let quit_item = MenuItem::new("Quit Aletheia", true, None);
        menu.append(&status_item)?;
        menu.append(&PredefinedMenuItem::separator())?;
        menu.append(&open_item)?;
        menu.append(&update_item)?;
        menu.append(&PredefinedMenuItem::separator())?;
        menu.append(&restart_item)?;
        menu.append(&stop_item)?;
        menu.append(&PredefinedMenuItem::separator())?;
        menu.append(&quit_item)?;

        self.open_id = Some(open_item.id().clone());
        self.update_id = Some(update_item.id().clone());
        self.restart_id = Some(restart_item.id().clone());
        self.stop_id = Some(stop_item.id().clone());
        self.quit_id = Some(quit_item.id().clone());
        self.status_item = Some(status_item);
        self.tray = Some(
            TrayIconBuilder::new()
                .with_menu(Box::new(menu))
                .with_menu_on_left_click(false)
                .with_menu_on_right_click(true)
                .with_tooltip(self.state.label())
                .with_icon(icon_for(self.state)?)
                .build()
                .context("create Aletheia tray icon")?,
        );
        Ok(())
    }

    fn set_state(&mut self, next: TrayState) {
        if self.state == next {
            return;
        }
        self.state = next;
        if let Some(status_item) = &self.status_item {
            status_item.set_text(next.label());
        }
        if let Some(tray) = &self.tray {
            let _ = tray.set_tooltip(Some(next.label()));
            if let Ok(icon) = icon_for(next) {
                let _ = tray.set_icon(Some(icon));
            }
        }
    }

    fn spawn_start(&self) {
        let paths = self.paths.clone();
        let proxy = self.proxy.clone();
        thread::spawn(move || {
            let result =
                ensure_started_without_browser(&paths).map_err(|error| format!("{error:#}"));
            let _ = proxy.send_event(UserEvent::RuntimeStartFinished(result));
        });
    }

    fn spawn_restart(&self) {
        let paths = self.paths.clone();
        let proxy = self.proxy.clone();
        thread::spawn(move || {
            let result = restart_without_browser(&paths).map_err(|error| format!("{error:#}"));
            let _ = proxy.send_event(UserEvent::RuntimeRestartFinished(result));
        });
    }

    fn spawn_update_stage(&mut self) {
        if self.update_in_progress {
            return;
        }
        self.update_in_progress = true;
        self.set_state(TrayState::Updating);
        let paths = self.paths.clone();
        let proxy = self.proxy.clone();
        thread::spawn(move || {
            let result = stage_update(&paths).map_err(|error| format!("{error:#}"));
            let _ = proxy.send_event(UserEvent::UpdateStageFinished(result));
        });
    }

    fn spawn_update_transaction(&mut self) {
        if self.update_in_progress {
            return;
        }
        self.update_in_progress = true;
        self.set_state(TrayState::Updating);
        let paths = self.paths.clone();
        let proxy = self.proxy.clone();
        thread::spawn(move || {
            let result =
                update::process_pending_update(&paths).map_err(|error| format!("{error:#}"));
            let _ = proxy.send_event(UserEvent::UpdateTransactionFinished(result));
        });
    }

    fn continue_update_transaction(&mut self) {
        // Stage completion already owns the in-progress flag. Transfer ownership to the
        // short activation worker without opening a window where polling can start it twice.
        let paths = self.paths.clone();
        let proxy = self.proxy.clone();
        thread::spawn(move || {
            let result =
                update::process_pending_update(&paths).map_err(|error| format!("{error:#}"));
            let _ = proxy.send_event(UserEvent::UpdateTransactionFinished(result));
        });
    }

    fn handle_menu(&mut self, event: MenuEvent, event_loop: &ActiveEventLoop) {
        if self.open_id.as_ref() == Some(&event.id) {
            let _ = open_dashboard();
        } else if self.update_id.as_ref() == Some(&event.id) {
            self.spawn_update_stage();
        } else if self.restart_id.as_ref() == Some(&event.id) && !self.update_in_progress {
            self.set_state(TrayState::Starting);
            self.spawn_restart();
        } else if self.stop_id.as_ref() == Some(&event.id) && !self.update_in_progress {
            let paths = self.paths.clone();
            thread::spawn(move || {
                let _ = run_gateway_action(&paths, "stop");
            });
        } else if self.quit_id.as_ref() == Some(&event.id) && !self.update_in_progress {
            let _ = run_gateway_action(&self.paths, "stop");
            event_loop.exit();
        }
    }

    fn handle_tray(&self, event: TrayIconEvent) {
        match event {
            TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            }
            | TrayIconEvent::DoubleClick {
                button: MouseButton::Left,
                ..
            } => {
                let _ = open_dashboard();
            }
            _ => {}
        }
    }
}

impl ApplicationHandler<UserEvent> for TrayApplication {
    fn resumed(&mut self, _event_loop: &ActiveEventLoop) {}

    fn window_event(
        &mut self,
        _event_loop: &ActiveEventLoop,
        _window_id: WindowId,
        _event: WindowEvent,
    ) {
    }

    fn new_events(&mut self, event_loop: &ActiveEventLoop, cause: StartCause) {
        if cause == StartCause::Init {
            if let Err(error) = self.create_tray() {
                eprintln!("aletheia-tray: {error:#}");
                event_loop.exit();
                return;
            }
            if update::has_pending_request(&self.paths) {
                self.spawn_update_transaction();
            } else {
                self.spawn_start();
            }
            self.next_poll = Instant::now() + Duration::from_secs(2);
            event_loop.set_control_flow(ControlFlow::WaitUntil(self.next_poll));
        }
    }

    fn user_event(&mut self, _event_loop: &ActiveEventLoop, event: UserEvent) {
        match event {
            UserEvent::Menu(event) => self.handle_menu(event, _event_loop),
            UserEvent::Tray(event) => self.handle_tray(event),
            UserEvent::RuntimeStartFinished(result) | UserEvent::RuntimeRestartFinished(result) => {
                match result {
                    Ok(()) => self.set_state(observed_state()),
                    Err(error) => {
                        eprintln!("aletheia-tray: {error}");
                        self.set_state(TrayState::Degraded);
                    }
                }
            }
            UserEvent::UpdateStageFinished(result) => match result {
                Ok(true) => self.continue_update_transaction(),
                Ok(false) => {
                    self.update_in_progress = false;
                    self.set_state(observed_state());
                }
                Err(error) => {
                    eprintln!("aletheia-tray: {error}");
                    self.update_in_progress = false;
                    self.set_state(TrayState::Degraded);
                }
            },
            UserEvent::UpdateTransactionFinished(result) => {
                self.update_in_progress = false;
                match result {
                    Ok(Some(outcome)) => {
                        if let Some(warning) = &outcome.resume_warning {
                            eprintln!("aletheia-tray: {warning}");
                        }
                        self.set_state(observed_state());
                    }
                    Ok(None) => self.set_state(observed_state()),
                    Err(error) => {
                        eprintln!("aletheia-tray: {error}");
                        self.set_state(TrayState::Degraded);
                    }
                }
            }
        }
    }

    fn about_to_wait(&mut self, event_loop: &ActiveEventLoop) {
        if Instant::now() >= self.next_poll {
            if !self.update_in_progress && update::has_pending_request(&self.paths) {
                self.spawn_update_transaction();
            } else if !self.update_in_progress {
                self.set_state(observed_state());
            }
            self.next_poll = Instant::now() + Duration::from_secs(2);
        }
        event_loop.set_control_flow(ControlFlow::WaitUntil(self.next_poll));
    }
}

fn real_main() -> Result<()> {
    let paths = HostPaths::discover()?;
    paths.ensure()?;
    if env::args().any(|arg| arg == "--smoke-test") {
        return smoke_test(&paths);
    }

    let event_loop = EventLoop::<UserEvent>::with_user_event()
        .build()
        .context("create tray event loop")?;
    let proxy = event_loop.create_proxy();
    let menu_proxy = proxy.clone();
    MenuEvent::set_event_handler(Some(move |event| {
        let _ = menu_proxy.send_event(UserEvent::Menu(event));
    }));
    let tray_proxy = proxy.clone();
    TrayIconEvent::set_event_handler(Some(move |event| {
        let _ = tray_proxy.send_event(UserEvent::Tray(event));
    }));

    let mut app = TrayApplication::new(paths, proxy);
    event_loop
        .run_app(&mut app)
        .context("run Aletheia tray event loop")?;
    Ok(())
}

fn main() {
    if let Err(error) = real_main() {
        eprintln!("aletheia-tray: {error:#}");
        process::exit(1);
    }
}
