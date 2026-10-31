//! RiftLord.exe on Windows.
//!
//! The app window is built with the MinGW (llvm-mingw) toolchain, so it loads two DLLs
//! that Windows does not provide: WebView2's loader and llvm-mingw's libunwind. A
//! program cannot carry a DLL it imports, so this launcher carries the window and both
//! DLLs, unpacks them once into `%LOCALAPPDATA%\RiftLord\app\<hash>\`, starts the
//! window there (Windows looks beside a program for its DLLs first) and exits. Rift
//! Lord still ships as this one file.

#![windows_subsystem = "windows"]

use std::env;
use std::fs;
use std::io;
use std::path::PathBuf;
use std::process::Command;

include!(concat!(env!("OUT_DIR"), "/payload.rs"));

/// The window program among the payload.
const WINDOW: &str = "RiftLord.exe";

fn app_dir() -> PathBuf {
    let base = env::var_os("LOCALAPPDATA").map(PathBuf::from).unwrap_or_else(env::temp_dir);
    base.join("RiftLord").join("app")
}

/// Unpack what is missing or incomplete, and remove other builds' folders.
fn unpack() -> io::Result<PathBuf> {
    let root = app_dir();
    let dir = root.join(PAYLOAD_HASH);
    fs::create_dir_all(&dir)?;
    for (name, bytes) in PAYLOAD {
        let path = dir.join(name);
        let whole = fs::metadata(&path).map(|m| m.len() == bytes.len() as u64).unwrap_or(false);
        if !whole {
            let part = dir.join(format!("{}.part", name));
            fs::write(&part, bytes)?;
            fs::rename(&part, &path)?;
        }
    }
    if let Ok(entries) = fs::read_dir(&root) {
        for entry in entries.flatten() {
            if entry.file_name() != PAYLOAD_HASH {
                let _ = fs::remove_dir_all(entry.path()); // a running copy keeps its own
            }
        }
    }
    Ok(dir)
}

fn tell(text: &str) {
    use windows_sys::Win32::UI::WindowsAndMessaging::{MessageBoxW, MB_ICONERROR, MB_OK};
    let wide = |s: &str| s.encode_utf16().chain(Some(0)).collect::<Vec<u16>>();
    let (body, title) = (wide(text), wide("Rift Lord"));
    unsafe { MessageBoxW(std::ptr::null_mut(), body.as_ptr(), title.as_ptr(), MB_OK | MB_ICONERROR) };
}

fn main() {
    let started = unpack().and_then(|dir| {
        Command::new(dir.join(WINDOW)).args(env::args_os().skip(1)).current_dir(&dir).spawn()
    });
    if let Err(e) = started {
        tell(&format!("Rift Lord could not start.\n\n{}\n\nFolder: {}", e, app_dir().display()));
    }
}
