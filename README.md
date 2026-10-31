# Rift Lord

Rift Lord is a mission maker for **Orcs Must Die! 3**. Start from one of the game's levels,
or from an empty grid, and make it yours:

- **Map Editor**: move, add and remove level pieces, place spawners, doors, routes and the
  rift, change the lighting, sky, fog and minimap, and decorate.
- **Onslaught Editor**: write the waves: which enemies come, when, and through which door.
- **Missions**: everything you make in one list. Build a mission, install it into the game
  to play it, or package it as a zip to share.
- **Fab Kits**: turn art packs from your Epic Games vault into level pieces for the Map
  Editor.

Rift Lord is a fan-made tool. It is not made by or affiliated with Robot Entertainment or
Epic Games.

## Requirements

- **Orcs Must Die! 3**, installed through Steam.
- **Linux or Windows.**
  - Linux: WebKitGTK 4.1 and GTK 3 (most desktop distributions already have them).
  - Windows: Microsoft Edge WebView2 (Windows 11 and current Windows 10 already have it).
- **Unreal Engine 4.27**, for Setup (a one-time step on first launch) and for converting
  Fab packs. On Windows, install it from the Epic Games Launcher. On Linux, use a source
  build with its `Setup.sh` run.
- **A C++ compiler**, for converting Fab packs: Visual Studio Build Tools with the C++
  workload on Windows; the engine's toolchain or clang++ on Linux.
- **OrcsMustUI** (a mod) in the game, to play missions listed under Custom Maps or in a
  new campaign.

## Installation and use

1. Download Rift Lord for your system and run it: `RiftLord` on Linux, `RiftLord.exe` on
   Windows. There is nothing else to install.
2. Rift Lord finds the game through Steam. If it can't, open the Onslaught Editor and
   choose the game's folder.
3. On first launch, Setup reads the game and your Unreal Engine 4.27 install. It runs once,
   and again only when one of them changes.
4. Make a mission:
   - In the **Map Editor**, choose a mission or a level and press **Open**, or press
     **New Level** to start from an empty grid. Press **Save Mission** when you are done.
   - In the **Onslaught Editor**, write the waves.
5. In **Missions**, press **Install** to put a mission into the game, or **Package** to make
   a zip you can share.
6. To use your Fab art, open **Fab Kits**, press **Convert** on a pack, and find its pieces
   under **New Object** in the Map Editor.

Your missions are kept in `~/.local/share/RiftLord/Missions` on Linux and
`%APPDATA%\RiftLord\Missions` on Windows. **Settings** can move them, and also sets the
author name new missions start with, the Epic vault folder, and how much memory and how
many workers a Fab conversion may use.

## For developers

Rift Lord is a Python server (`riftlord/`) with Svelte pages (`web/`) in a native window
(`shell/`, Rust). From a checkout:

```
python3 -m venv ~/.cache/rift-lord/editor-buildenv
~/.cache/rift-lord/editor-buildenv/bin/pip install -r requirements-build.txt
python3 tools/build.py              # the app: dist/RiftLord (--windows: dist/RiftLord.exe too)
python3 -m riftlord --no-window     # from source, in a browser
python3 tools/gate.py               # offline tests, release check, build comparisons
```

Building also needs Node 24 with npm and Rust. The Blueprint toolkit, **bpkit**, is a frozen
copy in `vendor/bpkit` (source and digest in `vendor/VENDORED.md`); it changes only through
`tools/vendor_bpkit.py --from PATH`. Build caches live in `~/.cache/rift-lord/`, never in the
game folder.

More in `docs/`:
- `APP.md`: how the window, server and pages fit together.
- `PLAN.md` and `DECISIONS.md`: status and decisions.
- `MAP-EDITOR.md`, `ONSLAUGHT-EDITOR.md` and `SAVE-MODEL.md`: the editors and how missions
  are saved and built.
- `HEAVY-JOBS.md`: how builds, bakes and tests share the machine.
- `DEPENDENCIES.md`: every dependency, and what each is for.

## Licensing

- Rift Lord does not have a licence yet.
- **Third-party components** that ship with Rift Lord are listed with their licences in
  `docs/THIRD-PARTY-NOTICES.txt`, written at build time; the build stops on a licence that
  is not on its allowed list.
- **No Unreal Engine code ships with Rift Lord.** The parts that need the engine (Setup's
  type data, and the collision cooker Fab Kits uses) are made on your machine from your
  Unreal Engine 4.27 install.
- **Orcs Must Die! 3 and its content** belong to Robot Entertainment. Rift Lord reads them
  from your installed game; missions are built from them on your machine.
- **Fab Kits** are converted from packs in your Epic vault and stay in your Rift Lord
  folder; Rift Lord never ships them. A mission that uses Fab Kit pieces carries that art
  in its files, so check the pack's Fab licence before you share the mission.
- **Supported mods** (OrcsMustUI, Crystal Defense, Treasure Chests) are read from the
  installed mods and never copied into a mission. A mission that uses one needs that mod
  installed to play.
