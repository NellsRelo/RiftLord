//! Bundle the app window and the DLLs it loads into the launcher.
//!
//! `RIFTLORD_PAYLOAD` lists them as `name=path;name=path` (workshop/build_app.py sets
//! it): the window `RiftLord.exe`, WebView2's `WebView2Loader.dll` and llvm-mingw's
//! `libunwind.dll`. Each is included with `include_bytes!`; their FNV-1a hash names the
//! folder they are unpacked into, so a new build never runs an old window.

use std::{env, fs, path::PathBuf};

fn main() {
    println!("cargo:rerun-if-env-changed=RIFTLORD_PAYLOAD");
    println!("cargo:rerun-if-changed=app.rc");
    println!("cargo:rerun-if-changed=../icon.ico");
    let out = PathBuf::from(env::var("OUT_DIR").unwrap());
    let payload = env::var("RIFTLORD_PAYLOAD").expect("set RIFTLORD_PAYLOAD (workshop/build_app.py does)");
    let mut hash: u64 = 0xcbf29ce484222325;
    let mut entries = String::from("const PAYLOAD: &[(&str, &[u8])] = &[\n");
    for item in payload.split(';').filter(|s| !s.is_empty()) {
        let (name, path) = item.split_once('=').expect("name=path");
        println!("cargo:rerun-if-changed={}", path);
        let bytes = fs::read(path).unwrap_or_else(|e| panic!("cannot read {}: {}", path, e));
        for b in name.as_bytes().iter().chain(bytes.iter()) {
            hash ^= *b as u64;
            hash = hash.wrapping_mul(0x100000001b3);
        }
        let copy = out.join(name);
        fs::write(&copy, &bytes).unwrap();
        entries.push_str(&format!("    ({:?}, include_bytes!({:?})),\n", name, copy.display().to_string()));
    }
    entries.push_str("];\n");
    entries.push_str(&format!("const PAYLOAD_HASH: &str = \"{:016x}\";\n", hash));
    fs::write(out.join("payload.rs"), entries).unwrap();
    if env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        embed_resource::compile("app.rc", embed_resource::NONE).manifest_optional().unwrap();
    }
}
