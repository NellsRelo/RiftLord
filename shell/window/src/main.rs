//! Rift Lord's application window.
//!
//! The editors are a Python server (`workshop`, frozen by PyInstaller) and web pages it
//! serves. This program is the app around them: it carries the frozen server inside
//! itself, unpacks it once, starts it with `--sidecar`, waits for the line naming the
//! address it listens on, and shows that address in a native window -- so Rift Lord is
//! one program with one window, never a tab in the user's browser. The server stops
//! when the window closes: it watches the pipe this program holds on its stdin, and
//! that pipe closes however this program ends, a crash included.
//!
//! The page asks the window for what a web page cannot do -- a native folder picker,
//! a save dialog -- through `window.ipc.postMessage`, and is answered through
//! `window.__riftlordReply(id, result)`.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs::{self, File};
use std::io::{self, BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use tao::dpi::LogicalSize;
use tao::event::{Event, WindowEvent};
use tao::event_loop::{ControlFlow, EventLoopBuilder, EventLoopProxy};
use tao::window::{Icon, ResizeDirection, Window, WindowBuilder};
use wry::WebViewBuilder;

/// The frozen server, built for this platform (see build.rs).
const SIDECAR: &[u8] = include_bytes!(concat!(env!("OUT_DIR"), "/sidecar.bin"));
const SIDECAR_HASH: &str = include_str!(concat!(env!("OUT_DIR"), "/sidecar.hash"));
const ICON_PNG: &[u8] = include_bytes!("../../icon.png");
/// How long the server gets to stop on its own once its stdin closes.
const STOP_GRACE: Duration = Duration::from_secs(4);
/// How long the server gets to say where it listens before the window gives up.
const START_TIMEOUT: Duration = Duration::from_secs(120);
/// Run in every page and frame: no context menu outside text fields, and no reload keys.
const NO_RELOAD_SCRIPT: &str = r#"(() => {
  const editable = (t) => t && t.closest &&
    t.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"]');
  window.addEventListener('contextmenu', (e) => {
    if (!editable(e.target)) e.preventDefault();
  }, true);
  window.addEventListener('keydown', (e) => {
    const k = (e.key || '').toLowerCase();
    if (k === 'f5' || ((e.ctrlKey || e.metaKey) && k === 'r')) {
      e.preventDefault();
      e.stopImmediatePropagation();
    }
  }, true);
})();"#;

enum UserEvent {
    Ready(String),
    Exited(String),
    Ipc(String),
    Music(String, String),
    StartTimedOut,
}

/// The Map Editor's music preview, played by this window rather than the page: WebKitGTK's
/// own audio path proved unreliable (silence, or tracks played garbled and too fast).
/// The page asks through IPC (`{"music": "play", "url": ...}` / `{"music": "stop"}`); a
/// thread of its own fetches the track from the loopback server, decodes it (Ogg
/// Vorbis, rodio + lewton), loops it through the system's audio output (cpal: ALSA on
/// Linux, WASAPI on Windows) and reports `loading`, `playing`, `stopped` or `error`.
mod music {
    use std::io::{Cursor, Read, Write};
    use std::net::TcpStream;
    use std::sync::mpsc::{channel, Receiver, Sender};
    use std::time::Duration;

    pub enum Command {
        Play(String),
        Stop,
    }

    /// How long a fetch may take: the first preview of a track is converted by the server.
    const FETCH_TIMEOUT: Duration = Duration::from_secs(120);
    /// Only the server's own music endpoint on this machine may be fetched.
    const PREFIX: &str = "http://127.0.0.1:";
    /// The music endpoint, as the Map Editor's page reaches it (under its prefix on the one
    /// listener) or a standalone Map Editor serves it.
    const PATHS: [&str; 2] = ["/mapeditor/api/music/", "/api/music/"];

    /// Start the player thread; `report(state, url)` is called on every change.
    pub fn start(report: impl Fn(&str, &str) + Send + 'static) -> Sender<Command> {
        let (tx, rx) = channel();
        std::thread::spawn(move || run(rx, report));
        tx
    }

    fn run(rx: Receiver<Command>, report: impl Fn(&str, &str)) {
        // the output stream lives on this thread (it is not Send) and opens on first use
        let mut output: Option<(rodio::OutputStream, rodio::OutputStreamHandle)> = None;
        let mut sink: Option<rodio::Sink> = None;
        let mut next: Option<Command> = None;
        loop {
            let cmd = match next.take() {
                Some(c) => c,
                None => match rx.recv() {
                    Ok(c) => c,
                    Err(_) => return, // the window has gone
                },
            };
            if let Some(s) = sink.take() {
                s.stop();
            }
            let url = match cmd {
                Command::Stop => {
                    report("stopped", "");
                    continue;
                }
                Command::Play(url) => url,
            };
            report("loading", &url);
            let bytes = fetch(&url);
            // a newer request that came in while fetching wins
            let mut newer = None;
            while let Ok(c) = rx.try_recv() {
                newer = Some(c);
            }
            if newer.is_some() {
                next = newer;
                continue;
            }
            let played = bytes.and_then(|b| {
                if output.is_none() {
                    output = Some(rodio::OutputStream::try_default().map_err(|e| e.to_string())?);
                }
                let handle = &output.as_ref().unwrap().1;
                let s = rodio::Sink::try_new(handle).map_err(|e| e.to_string())?;
                let source = rodio::Decoder::new_looped(Cursor::new(b)).map_err(|e| e.to_string())?;
                s.append(source);
                Ok(s)
            });
            match played {
                Ok(s) => {
                    sink = Some(s);
                    report("playing", &url);
                }
                Err(e) => {
                    eprintln!("music: {}: {}", url, e);
                    report("error", &url);
                }
            }
        }
    }

    /// The body of a GET to the loopback server.
    fn fetch(url: &str) -> Result<Vec<u8>, String> {
        let rest = url.strip_prefix(PREFIX).ok_or("not a loopback url")?;
        let (port, path) = rest.split_at(rest.find('/').ok_or("no path")?);
        let port: u16 = port.parse().map_err(|_| "bad port")?;
        if !PATHS.iter().any(|p| path.starts_with(p))
            || path.contains(|c: char| c.is_whitespace() || c.is_control())
        {
            return Err("not a music url".into());
        }
        let mut conn = TcpStream::connect(("127.0.0.1", port)).map_err(|e| e.to_string())?;
        conn.set_read_timeout(Some(FETCH_TIMEOUT)).map_err(|e| e.to_string())?;
        write!(conn, "GET {} HTTP/1.0\r\nHost: 127.0.0.1:{}\r\nX-Rift-Lord: 1\r\n\r\n", path, port)
            .map_err(|e| e.to_string())?;
        let mut raw = Vec::new();
        conn.read_to_end(&mut raw).map_err(|e| e.to_string())?;
        let split = raw.windows(4).position(|w| w == b"\r\n\r\n").ok_or("no headers")?;
        let head = String::from_utf8_lossy(&raw[..split]);
        let status = head.split_whitespace().nth(1).unwrap_or("");
        if status != "200" {
            return Err(format!("HTTP {}", status));
        }
        Ok(raw[split + 4..].to_vec())
    }
}

/// Rift Lord's own folder in the user's cache: the unpacked server and its log.
fn cache_dir() -> PathBuf {
    dirs::cache_dir().unwrap_or_else(std::env::temp_dir).join("RiftLord")
}

fn server_name() -> &'static str {
    if cfg!(windows) { "RiftLordServer.exe" } else { "RiftLordServer" }
}

/// Unpack the server, once per build of it, into a folder named by its hash; older
/// builds' folders are removed.
fn unpack() -> io::Result<PathBuf> {
    let runtime = cache_dir().join("runtime");
    let dir = runtime.join(SIDECAR_HASH);
    let path = dir.join(server_name());
    let whole = fs::metadata(&path).map(|m| m.len() == SIDECAR.len() as u64).unwrap_or(false);
    if !whole {
        fs::create_dir_all(&dir)?;
        let part = dir.join(format!("{}.part", server_name()));
        fs::write(&part, SIDECAR)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&part, fs::Permissions::from_mode(0o755))?;
        }
        fs::rename(&part, &path)?;
    }
    if let Ok(entries) = fs::read_dir(&runtime) {
        for entry in entries.flatten() {
            if entry.file_name() != SIDECAR_HASH {
                let _ = fs::remove_dir_all(entry.path()); // in use by another copy: left
            }
        }
    }
    Ok(path)
}

struct Server {
    child: Arc<Mutex<Child>>,
    stdin: Option<ChildStdin>,
}

impl Server {
    /// Start the server and report on `proxy`: its address once it listens, and its end.
    fn start(proxy: EventLoopProxy<UserEvent>) -> io::Result<Server> {
        let exe = unpack()?;
        fs::create_dir_all(cache_dir())?;
        let log = Arc::new(Mutex::new(File::create(cache_dir().join("server.log"))?));
        let mut cmd = Command::new(&exe);
        cmd.arg("--sidecar")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }
        let mut child = cmd.spawn()?;
        let stdin = child.stdin.take();
        let stdout = child.stdout.take().expect("piped stdout");
        let stderr = child.stderr.take().expect("piped stderr");

        let (out_log, out_proxy) = (log.clone(), proxy.clone());
        thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                let _ = writeln!(out_log.lock().unwrap(), "{}", line);
                if let Some(url) = line.strip_prefix("RIFTLORD_READY ") {
                    let _ = out_proxy.send_event(UserEvent::Ready(url.trim().to_string()));
                }
            }
        });
        let err_log = log.clone();
        thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                let _ = writeln!(err_log.lock().unwrap(), "{}", line);
            }
        });

        let child = Arc::new(Mutex::new(child));
        let watched = child.clone();
        thread::spawn(move || loop {
            if let Ok(Some(status)) = watched.lock().unwrap().try_wait() {
                let _ = proxy.send_event(UserEvent::Exited(status.to_string()));
                return;
            }
            thread::sleep(Duration::from_millis(250));
        });
        Ok(Server { child, stdin })
    }

    /// Close the server's stdin, which is how it is asked to stop; kill it if it has
    /// not stopped within STOP_GRACE.
    fn stop(&mut self) {
        drop(self.stdin.take());
        let until = Instant::now() + STOP_GRACE;
        while Instant::now() < until {
            if let Ok(Some(_)) = self.child.lock().unwrap().try_wait() {
                return;
            }
            thread::sleep(Duration::from_millis(100));
        }
        let _ = self.child.lock().unwrap().kill();
    }
}

fn window_icon() -> Option<Icon> {
    let decoder = png::Decoder::new(ICON_PNG);
    let mut reader = decoder.read_info().ok()?;
    let mut buf = vec![0; reader.output_buffer_size()];
    let info = reader.next_frame(&mut buf).ok()?;
    if info.color_type != png::ColorType::Rgba {
        return None;
    }
    buf.truncate(info.buffer_size());
    Icon::from_rgba(buf, info.width, info.height).ok()
}

/// What the window shows until the server answers, and if it cannot start.
fn notice(title: &str, body: &str) -> String {
    format!(
        r#"<!doctype html><meta charset="utf-8"><style>
html,body{{margin:0;height:100%;background:#10131a;color:#e6eaf2;
font:14px/1.6 'Segoe UI',system-ui,sans-serif;display:grid;place-items:center}}
main{{max-width:34rem;text-align:center;padding:24px}}
h1{{font-size:18px;font-weight:600;margin:0 0 8px}} h1 span{{color:#c48ade}}
p{{color:#8e97a6;margin:0;white-space:pre-wrap}}</style>
<main><h1>{}</h1><p>{}</p></main>"#,
        title, body
    )
}

fn escape_html(text: &str) -> String {
    text.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;")
}

/// A native dialog the page asked for: `pickFolder`, `pickFile` or `saveFile`.
struct DialogRequest {
    cmd: String,
    title: String,
    start: String,
    name: String,
    extension: String,
}

/// Show a dialog and return the chosen path. Runs on the event loop's thread, which on
/// Linux is the thread GTK runs on and so the one its dialogs must open on.
#[cfg(target_os = "linux")]
fn run_dialog(req: &DialogRequest, parent: &tao::window::Window) -> Option<String> {
    use gtk::prelude::*;
    use tao::platform::unix::WindowExtUnix;
    let (action, accept) = match req.cmd.as_str() {
        "pickFolder" => (gtk::FileChooserAction::SelectFolder, "Choose"),
        "pickFile" => (gtk::FileChooserAction::Open, "Open"),
        "saveFile" => (gtk::FileChooserAction::Save, "Save"),
        _ => return None,
    };
    let dialog = gtk::FileChooserDialog::with_buttons(
        Some(req.title.as_str()),
        Some(parent.gtk_window()),
        action,
        &[("Cancel", gtk::ResponseType::Cancel), (accept, gtk::ResponseType::Accept)],
    );
    if !req.start.is_empty() && PathBuf::from(&req.start).is_dir() {
        dialog.set_current_folder(&req.start);
    }
    if action == gtk::FileChooserAction::Save {
        dialog.set_do_overwrite_confirmation(true);
        dialog.set_current_name(&req.name);
    }
    if action != gtk::FileChooserAction::SelectFolder && !req.extension.is_empty() {
        let filter = gtk::FileFilter::new();
        filter.add_pattern(&format!("*.{}", req.extension));
        filter.set_name(Some(&req.extension.to_uppercase()));
        dialog.add_filter(filter);
    }
    let chosen = if dialog.run() == gtk::ResponseType::Accept {
        dialog.filename().map(|p| p.display().to_string())
    } else {
        None
    };
    unsafe { dialog.destroy() };
    chosen
}

#[cfg(not(target_os = "linux"))]
fn run_dialog(req: &DialogRequest, parent: &tao::window::Window) -> Option<String> {
    let mut dialog = rfd::FileDialog::new().set_title(&req.title).set_parent(parent);
    match req.cmd.as_str() {
        "pickFolder" => {
            if !req.start.is_empty() && PathBuf::from(&req.start).is_dir() {
                dialog = dialog.set_directory(&req.start);
            }
            dialog.pick_folder().map(|p| p.display().to_string())
        }
        "pickFile" => {
            if !req.start.is_empty() && PathBuf::from(&req.start).is_dir() {
                dialog = dialog.set_directory(&req.start);
            }
            if !req.extension.is_empty() {
                dialog = dialog.add_filter(req.extension.to_uppercase(), &[req.extension.as_str()]);
            }
            dialog.pick_file().map(|p| p.display().to_string())
        }
        "saveFile" => {
            dialog = dialog.set_file_name(&req.name);
            if !req.extension.is_empty() {
                dialog = dialog.add_filter(req.extension.to_uppercase(), &[req.extension.as_str()]);
            }
            dialog.save_file().map(|p| p.display().to_string())
        }
        _ => None,
    }
}

/// Carry out a request about the window itself, from Rift Lord's own titlebar. True
/// if `cmd` was one.
fn window_command(msg: &serde_json::Value, window: &Window) -> bool {
    let cmd = msg.get("cmd").and_then(|v| v.as_str()).unwrap_or("");
    match cmd {
        "drag" => {
            let _ = window.drag_window();
        }
        "minimize" => window.set_minimized(true),
        "toggleMaximize" => window.set_maximized(!window.is_maximized()),
        "resize" => {
            let direction = match msg.get("edge").and_then(|v| v.as_str()).unwrap_or("") {
                "n" => ResizeDirection::North,
                "s" => ResizeDirection::South,
                "e" => ResizeDirection::East,
                "w" => ResizeDirection::West,
                "ne" => ResizeDirection::NorthEast,
                "nw" => ResizeDirection::NorthWest,
                "se" => ResizeDirection::SouthEast,
                "sw" => ResizeDirection::SouthWest,
                _ => return true,
            };
            let _ = window.drag_resize_window(direction);
        }
        _ => return false,
    }
    true
}

/// Answer one request from the page: (its id, the result to hand back).
fn answer(request: &str, parent: &tao::window::Window) -> Option<(u64, serde_json::Value)> {
    let msg: serde_json::Value = serde_json::from_str(request).ok()?;
    let id = msg.get("id")?.as_u64()?;
    let text = |key: &str| msg.get(key).and_then(|v| v.as_str()).unwrap_or("").to_string();
    let req = DialogRequest {
        cmd: text("cmd"),
        title: text("title"),
        start: text("start"),
        name: text("name"),
        extension: text("extension"),
    };
    let result = run_dialog(&req, parent);
    Some((id, result.map(serde_json::Value::String).unwrap_or(serde_json::Value::Null)))
}

fn main() -> wry::Result<()> {
    // WebKitGTK's DMA-BUF renderer draws a blank window on some drivers (NVIDIA's
    // among them); the shared-memory path it falls back to draws everywhere.
    #[cfg(target_os = "linux")]
    if std::env::var_os("WEBKIT_DISABLE_DMABUF_RENDERER").is_none() {
        std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
    }

    let event_loop = EventLoopBuilder::<UserEvent>::with_user_event().build();
    let proxy = event_loop.create_proxy();
    let window = WindowBuilder::new()
        .with_title("Rift Lord")
        // Rift Lord draws its own titlebar -- the app icon over the sidebar, the
        // window buttons on the right -- and asks for drags and resizes (window_command)
        .with_decorations(false)
        .with_inner_size(LogicalSize::new(1500.0, 920.0))
        .with_min_inner_size(LogicalSize::new(1100.0, 680.0))
        .with_window_icon(window_icon())
        .build(&event_loop)
        .expect("cannot open a window");

    let ipc_proxy = proxy.clone();
    let devtools = std::env::var_os("RIFTLORD_DEVTOOLS").is_some();
    let builder = WebViewBuilder::new()
        .with_html(notice("Rift <span>Lord</span>", "Starting\u{2026}"))
        .with_background_color((16, 19, 26, 255))
        .with_devtools(devtools)
        .with_ipc_handler(move |req| {
            let _ = ipc_proxy.send_event(UserEvent::Ipc(req.body().clone()));
        });
    // every frame, so the editors' pages are covered too
    let builder = if devtools {
        builder
    } else {
        builder.with_initialization_script_for_main_only(NO_RELOAD_SCRIPT, false)
    };
    #[cfg(target_os = "windows")]
    let builder = {
        use wry::WebViewBuilderExtWindows;
        builder.with_browser_accelerator_keys(devtools)
    };
    #[cfg(any(target_os = "windows", target_os = "macos"))]
    let webview = match builder.build(&window) {
        Ok(w) => w,
        Err(e) => {
            // On Windows the page is drawn by Microsoft Edge WebView2, which Windows 11
            // and current Windows 10 include; an older install may not have it.
            let _ = rfd::MessageDialog::new()
                .set_level(rfd::MessageLevel::Error)
                .set_title("Rift Lord")
                .set_description(format!(
                    "Rift Lord needs the Microsoft Edge WebView2 Runtime, which this \
                     computer does not seem to have.\n\nInstall it from \
                     https://developer.microsoft.com/microsoft-edge/webview2/ and start \
                     Rift Lord again.\n\n({})",
                    e
                ))
                .show();
            return Err(e);
        }
    };
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    let webview = {
        use tao::platform::unix::WindowExtUnix;
        use wry::WebViewBuilderExtUnix;
        builder.build_gtk(window.default_vbox().unwrap())?
    };

    let mut server = match Server::start(proxy.clone()) {
        Ok(s) => Some(s),
        Err(e) => {
            let _ = webview.load_html(&notice(
                "Rift Lord could not start",
                &escape_html(&format!("Its editors could not be started: {}", e)),
            ));
            None
        }
    };
    let timer = proxy.clone();
    thread::spawn(move || {
        thread::sleep(START_TIMEOUT);
        let _ = timer.send_event(UserEvent::StartTimedOut);
    });
    let mut ready = false;
    let music_proxy = proxy.clone();
    let player = music::start(move |state, url| {
        let _ = music_proxy.send_event(UserEvent::Music(state.to_string(), url.to_string()));
    });

    event_loop.run(move |event, _, control_flow| {
        *control_flow = ControlFlow::Wait;
        match event {
            Event::UserEvent(UserEvent::Ready(url)) => {
                ready = true;
                let _ = webview.load_url(&url);
            }
            Event::UserEvent(UserEvent::Ipc(body)) => {
                let msg: serde_json::Value = serde_json::from_str(&body).unwrap_or_default();
                if msg.get("cmd").and_then(|v| v.as_str()) == Some("close") {
                    if let Some(s) = server.as_mut() {
                        s.stop();
                    }
                    *control_flow = ControlFlow::Exit;
                    return;
                }
                if window_command(&msg, &window) {
                    return;
                }
                match msg.get("music").and_then(|v| v.as_str()) {
                    Some("play") => {
                        let url = msg.get("url").and_then(|v| v.as_str()).unwrap_or("");
                        let _ = player.send(music::Command::Play(url.to_string()));
                        return;
                    }
                    Some(_) => {
                        let _ = player.send(music::Command::Stop);
                        return;
                    }
                    None => {}
                }
                if let Some((id, result)) = answer(&body, &window) {
                    let _ = webview.evaluate_script(&format!(
                        "window.__riftlordReply && window.__riftlordReply({}, {})",
                        id, result
                    ));
                }
            }
            Event::UserEvent(UserEvent::Music(state, url)) => {
                let _ = webview.evaluate_script(&format!(
                    "window.__riftlordMusic && window.__riftlordMusic({}, {})",
                    serde_json::Value::String(state),
                    serde_json::Value::String(url)
                ));
            }
            Event::UserEvent(UserEvent::Exited(status)) => {
                if ready {
                    // Quit from the sidebar stops the server; the window goes with it.
                    *control_flow = ControlFlow::Exit;
                } else {
                    let log = cache_dir().join("server.log");
                    let _ = webview.load_html(&notice(
                        "Rift Lord could not start",
                        &escape_html(&format!(
                            "Its editors stopped before they were ready ({}).\nDetails are in {}",
                            status,
                            log.display()
                        )),
                    ));
                    server = None;
                }
            }
            Event::UserEvent(UserEvent::StartTimedOut) => {
                if !ready && server.is_some() {
                    let _ = webview.load_html(&notice(
                        "Rift Lord is taking too long to start",
                        &escape_html(&format!(
                            "Its editors have not answered. Details are in {}",
                            cache_dir().join("server.log").display()
                        )),
                    ));
                }
            }
            Event::WindowEvent { event: WindowEvent::Resized(_), .. } => {
                // the titlebar shows restore or maximize to match
                let _ = webview.evaluate_script(&format!(
                    "window.__riftlordWindow && window.__riftlordWindow({{maximized: {}}})",
                    window.is_maximized()
                ));
            }
            Event::WindowEvent { event: WindowEvent::CloseRequested, .. } => {
                if let Some(s) = server.as_mut() {
                    s.stop();
                }
                *control_flow = ControlFlow::Exit;
            }
            Event::LoopDestroyed => {
                if let Some(s) = server.as_mut() {
                    s.stop();
                }
            }
            _ => {}
        }
    });
}
