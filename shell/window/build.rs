//! Bundle the Python server into the executable, and give the Windows build its icon.
//!
//! `RIFTLORD_SIDECAR` names the frozen server built for the same platform
//! (`workshop/dist/RiftLordServer[.exe]`); it is copied into OUT_DIR and included with
//! `include_bytes!`, so Rift Lord ships as one file. Its FNV-1a hash names the folder it
//! is unpacked into, so a new build never runs an old server.

use std::{env, fs, path::PathBuf};

fn main() {
    println!("cargo:rerun-if-env-changed=RIFTLORD_SIDECAR");
    println!("cargo:rerun-if-changed=app.rc");
    println!("cargo:rerun-if-changed=../icon.ico");
    let out = PathBuf::from(env::var("OUT_DIR").unwrap());
    let sidecar = env::var("RIFTLORD_SIDECAR")
        .expect("set RIFTLORD_SIDECAR to the frozen server (workshop/build_app.py does)");
    println!("cargo:rerun-if-changed={}", sidecar);
    let bytes = fs::read(&sidecar).unwrap_or_else(|e| panic!("cannot read {}: {}", sidecar, e));
    let mut hash: u64 = 0xcbf29ce484222325;
    for b in &bytes {
        hash ^= *b as u64;
        hash = hash.wrapping_mul(0x100000001b3);
    }
    fs::write(out.join("sidecar.bin"), &bytes).unwrap();
    fs::write(out.join("sidecar.hash"), format!("{:016x}", hash)).unwrap();
    if env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        embed_resource::compile("app.rc", embed_resource::NONE).manifest_optional().unwrap();
    }
}
