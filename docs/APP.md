# Rift Lord

The **Map Editor** and the **Onslaught Editor** in one application, with **Missions**:
everything they build, kept unpacked until it is installed into the game or packaged
to share.

Naming: the app is Rift Lord. Its code lives in `workshop/` because it was built before
it was named, and the Map Editor's package is `riftlord/` because that tool was called
Rift Lord before it became part of this app.

    python3 workshop/build_app.py             # this OS: dist/RiftLord[.exe]
    python3 workshop/build_app.py --windows   # on Linux: dist/RiftLord.exe, under Wine
    python3 -m workshop                       # from source, in a browser (development)

## The two halves

- **The server** (`workshop/`, Python, frozen by PyInstaller into `RiftLordServer`):
  one process, three loopback servers -- the shell page (8870), the Map Editor (8871)
  and the Onslaught Editor (8872), each falling back to a free port. The shell page
  shows each editor's own page in a frame, so neither editor was rewritten to live
  here.
- **The window** (`workshop/app/`, Rust: tao + wry). `RiftLord` carries the frozen
  server inside itself (`build.rs` includes it), unpacks it once into the user's cache
  (`RiftLord/runtime/<hash>/`), starts it with `--sidecar`, and shows the address the
  server prints (`RIFTLORD_READY <url>`). Closing the window closes the server's stdin,
  which is how the server knows to stop -- a crash closes it too -- and the window
  kills it if it has not stopped within four seconds. The server's output goes to
  `RiftLord/server.log` in the cache. `RIFTLORD_DEVTOOLS=1` enables the web inspector.
  On Windows the server watches that pipe with `PeekNamedPipe` rather than a blocking
  read: a thread blocked reading it stopped other threads loading DLLs, and the first
  numpy import in a request hung (`workshop/__main__.py`, `_peek_until_closed`).
  - **The light baker** rides inside the server: `RiftLordServer --lightbake ...` runs
    `lightbaker.main` (run_workshop.py), and `riftlord.lightbake` starts it that way, at
    low priority, when frozen. Mitsuba/Dr.Jit (OptiX) and moderngl (OpenGL fallback) go
    in as one zip (`baker-packages.zip`, only the `cuda_ad_rgb` and `scalar_rgb`
    variants), unpacked on the first bake into the cache's `RiftLord/baker/<digest>/`.
    The build Python needs them installed; without them the app builds anyway and its
    missions keep the shipped light maps (`dist/RiftLordServer[.exe].baker.txt` says
    which). Windows has no CUDA under Wine, so a Windows bake is unproven there.
  - The page asks the window for native dialogs through `window.ipc.postMessage`
    (`workshop/web/src/lib/native.js`); the window answers through
    `window.__riftlordReply`. On Linux the dialogs are GTK's own, opened on the window's
    thread: rfd's GTK backend deadlocks there (it waits on the thread GTK runs on).
  - The Map Editor's music preview is played by the window (`main.rs`, `music`: rodio
    with lewton for Ogg Vorbis, output through cpal, so ALSA on Linux and WASAPI on
    Windows; libasound is the only library it needs). WebKitGTK's own audio played
    silence or garbled, too-fast audio. The Map Editor's frame asks the shell page
    (`riftlord-music` messages), the shell asks the window (`{"music": "play", "url"}` /
    `{"music": "stop"}`), and the window's reports (`window.__riftlordMusic`) go back to
    the frames as `riftlord-music-state`. The window only fetches
    `http://127.0.0.1:<port>/api/music/...`.
  - Linux: WebKitGTK, with `WEBKIT_DISABLE_DMABUF_RENDERER=1` (its DMA-BUF renderer
    draws blank on some drivers); the machine needs WebKitGTK 4.1 and GTK 3, which
    desktop distributions ship. Windows: Microsoft Edge WebView2, which Windows 11 and
    current Windows 10 include; without it the window says so and links the download.

## Building

- **On Windows** (`python workshop\build_app.py`, with Rust's default MSVC toolchain):
  wry links WebView2's loader statically, so `RiftLord.exe` is the window itself.
- **On Linux** (`--windows`): the server is frozen by a Windows Python under Wine
  (`~/.cache/rift-lord/winebuild`), and the window is cross-compiled for `x86_64-pc-windows-gnullvm` with
  llvm-mingw (`~/.cache/rift-lord/crossbuild`, downloaded on first use; open source, no Microsoft SDK
  licence). A MinGW-built window imports `WebView2Loader.dll` and `libunwind.dll`,
  which Windows does not have, so what ships as `RiftLord.exe` is `workshop/launcher/`:
  it carries the window and both DLLs, unpacks them once into
  `%LOCALAPPDATA%\RiftLord\app\<hash>\` and starts the window there. Still one file.
  - Rust's own windows-gnu toolchain under Wine cannot build the window: the `windows`
    crates need a `dlltool` it does not ship.
  - Linking WebView2's static loader into a MinGW build fails: it is built against the
    MSVC C++ runtime (`__security_cookie`, `_Init_thread_*`, MSVC `operator new`).

## Missions

- `bucket.activate()` points `gamepath.TARGET` at the Missions folder, so both editors'
  builds, notes and campaigns land there instead of `~mods` -- **loose**: each mission
  is a folder of cooked files (`<stem>/OMD/Content/...`), never a pak. `paksource`
  reads and writes a folder exactly as it does a pak, and `navpatch` opens either, so
  everything downstream works unchanged.
- **Install** packs missions into `~mods/<stem>.pak` with their notes; **Package**
  packs them into a zip to share.
- **A custom campaign is one pak** (2026-10-30). A custom campaign Rift Lord made is packed
  with every mission it lists into ONE pak,
  `<campaign stem>.pak` (`bucket._units`, `campaign_members`, `_pack_campaign`): the
  campaign's table; each mission's files exactly as it is packed alone (`_packed_files`:
  its own mod folder, rows and Guids, its level by its Working Title), so a mission rebuilt
  is only packed again, never another one rebuilt; and ONE `CustomMaps.yaml` in the
  campaign's folder with every mission's notes and the campaign's, beside the banner and
  cards. ProtoRegistry registers the tables of every `Mods/<Key>/Tables` in a pak, but
  copies OrcsMustUI's files as `ModData/<pak>.<file>` (`CREATE_NEW`), so a pak carries each
  file once. Two missions carrying one path with different bytes (a kit, carried
  minions) refuse the pack. Installing takes over from the missions' own paks in `~mods`
  (removed), and a campaign pak there that still holds a mission now packed elsewhere is
  packed again without it, or removed with its campaign (`_moved_from`). Missions shows the
  campaign as the installed unit: its group has the Game state, Install Campaign and
  Package Campaign. Each mission's row keeps Install and Package (user, 2026-10-31): while
  the campaign pak is not installed, Install installs that mission alone and Package zips it
  alone; once it is installed, Install packs the campaign pak again from the builds as they
  are, so a mission is never both alone and in it (`_units` `dest`; only the missions asked
  for must be current);
  `installs.json` keeps the campaign with each member's build digest and level
  (`_campaign_install_state`; an install noted before reads Out of date). Missions not in
  a custom campaign stay one pak each, a mission placed in Endless among them,
  and so does a mission in a shipped campaign (it patches the game's CampaignProtos). A
  custom campaign Rift Lord did not make is packed as before, its table alone. Tests:
  `tests/offline/test_campaignpak.py`; UI smoke case `campaignpak`.
- **Paks and the running game** (`riftlord/core/pakguard.py`). A running game rereads each
  mounted pak by name with the index it read at mount (UE 4.27
  `FPakFile::GetSharedReader`), so a pak replaced, removed or renamed under it crashes it.
  Every write of a `.pak` in the game's Paks folder goes through the guard (`pakv4.write`,
  `paksource.write`/`remove`, `unrealpak.write`, probe `--install`, the entitlements merge,
  a campaign pak disabled, the navigation dev tools): written as `<name>.pak.part` and
  renamed in; a NEW pak goes in at once; replacing, removing or renaming an existing one
  while the game runs (`gamepath.game_running`: a process whose first argument, or a path
  among the next two, is `OMD-Win64-Shipping.exe`) waits until it closes and then completes
  by itself. A CLI waits in place and prints `Waiting for the game to close to replace
  <pak>`; the app's Install hands such an install to a background thread and returns at
  once, and the mission reads "Waiting for the game to close" until it is done.
  `RIFTLORD_GAME_FLAG` (a file: present means running) stands in for the game in tests
  (`tests/offline/test_pakguard.py`).
- **Campaign Index** is a mission's place in its campaign's MissionList, which is the
  order a campaign tab shows (OrcsMustUI sorts only the Endless and Custom Maps lists;
  HubBar.SortList). It is a text field, 1 to the campaign's mission count, with its red
  reason (`customcampaign.index_problem`): in the Details dialog of all three pages
  wherever the campaign is chosen (each choice carries `size`, `first` and the mission's
  `index`, `customcampaign.choices` / `bucket.choices_for`; `/api/campaigns?mission=`),
  and on each row of a custom campaign's group on the Missions page (rows in the
  campaign's order; `POST /api/bucket/campaignindex`, `customcampaign.move`). A custom
  campaign's list is rewritten at once (`place(index=)`, `_rewrite` and its `_check`; the
  missions between move along); the index is never kept in a record, so a rebuild keeps
  the order the campaign has. A shipped campaign's index goes into the build: kept as
  `rec['campaignIndex']` (a bake input) or the onslaught spec's `campaignIndex`, and
  `makemod.build(into_index=)` inserts the mission there in the patched CampaignProtos
  (`missionproto.add_mission(at=)`; `verify_into` checks the shipped missions are kept
  byte for byte, in order). It runs from 2 (`SHIPPED_FIRST`): the game unlocks a
  campaign's first mission without a save entry, so the shipped first mission stays first.
  OrcsMustUI's own extension route (Extends tables) only appends, so this needs the
  patched table Rift Lord already ships for a shipped placement.
- **Working Title** (Mission Settings) is the name a mission's level goes by in the game,
  as the shipped `Scenario_10` is the level behind "Coastal Hallways": the level package
  `/Game/Mods/<mod>/Maps/<title>/<title>`, its World, sublevels `<title>_<tag>`, level
  script names, and the mission rows' MissionLevel. A new mission (a save, Save As, a
  copy) is given `RL_Scen_<ScenarioName>`, its title in letters and digits ("North Wings"
  -> `RL_Scen_NorthWings`; `leveltitle.fresh`, `bucket.fresh_working_title`), with a letter
  suffix (B, C, ...; never `_<digits>`) past the other missions' levels and shipped names.
  A mission with none stored (every mission made before 2026-10-30) keeps `<mod>_Map01`,
  and so does one given `<mod>_Map01`: its installed level is not renamed by this change.
  The field shows the level's name; an empty field saves the `RL_Scen_` default, which is
  its placeholder. The Missions page's Working Title column shows it (`items()`
  `workingTitle`), which tells two missions with one title apart. Validated in
  the field (letters, digits, underscores, starting with a letter; at most 64; not a
  shipped level, sublevel or mission row name; not another mission's level, whatever the
  case; `leveltitle.problems`). Kept as `rec['workingTitle']`; the loose build keeps
  `<mod>_Map01`, and Install and Package rename it as they pack (`bucket._pack`,
  `leveltitle.retitle`, checked by read-back: pkgcheck, self-references, MissionLevel, no old
  name left). Mod folder, rows, Guids and pak name are unchanged, so a renamed level
  replaces the installed pak in place; a changed title makes the installed mission Out of
  date (`installs.json` keeps the title it was packed with) without a rebuild. A title
  ending `_<digits>` without a leading zero (`scenario_fear_1`) is written as the cooker
  writes `Scenario_10`: the base string with FName number digits + 1 on the package path,
  object path, World and ubergraph entries, and on their references (export and import
  names, the class function map, the level's soft references to its own actors,
  MissionLevel). A copy of Scenario_10..18 keeps its World's shipped split name, as before.
  `python3 -m riftlord.build.leveltitle check TITLE`. Tests:
  `tests/offline/test_campaignindex.py`; UI smoke case `campaignindex`.
- **Add to Endless** (Details, Mission Settings, and Settings' defaults) gives a mission its
  Endless twin and, in its own slot's campaign table, a single `OMD3_Endless` row listing it
  (`makemod.ENDLESS_EXTENSION`), which OrcsMustUI merges into the Endless tab. The
  author-facing contract is in `Mods/OrcsMustUI/Docs/Campaigns.md`, "Adding maps to Endless".
- **Campaign Endless** (Map Editor missions; `customcampaign.ENDLESS`, kept in the record
  as `rec['endlessOnly']`, a bake input digested only when set; user, 2026-10-31): the
  mission is listed in the Endless hub alone. Its own row is the Endless row
  (DefaultCharacters emptied as a twin's), listed by its `OMD3_Endless` row, with no twin
  and no campaign row (`makemod.build(endless_only=)`, `verify`); it is taken out of every
  campaign's list at once and ships as a pak of its own (`_place_variant`,
  `customcampaign.finish`). Add to Endless and Campaign Index are hidden while it is chosen;
  choosing any other campaign takes it out of Endless. OrcsMustUI counts a mission any
  campaign row lists, an `OMD3_Endless` row included, as no custom map
  (`ModActor.ListedMissions`), so it shows in Endless and not under Custom Maps. For a map
  whose Endless version cannot be the same map: the campaign mission without Add to Endless,
  and a second mission with the same title in Campaign Endless. The Missions page lists such
  missions in an Endless group. A record placed this way before 2026-10-31 reads as Campaign
  Endless and ships alone.
- **Unlocks** (Mission Settings; `riftlord/missions/missionunlocks.py`): the heroes and
  items beating the mission grants, written into its MissionProtos row as the shipped rows
  write theirs: `CharacterUnlocks` (FOMDMissionCharacterElement, 156 bytes, Guid at 124)
  and `ItemUnlocks` (FOMDMissionItemElement, 182 bytes, Guid at 150; bIsPreMission false,
  bIsHidden false). A hero also brings its PrimaryWeapon as a hidden item, as
  Scenario_18_WS does. Elements are copied from Scenario_02 and Scenario_18_WS (byte for
  byte, `tests/offline/test_missionunlocks.py`). The choices are the shipped Trap, Weapon,
  Trinket and PlayerCharacter tables plus every installed mod's own tables of those row
  structs (`Mods/<key>/Tables`, ~mods and LogicMods), without rows that have
  bRequiresEntitlement, bDevelopmentOnly, AquiredBy Internal or no DisplayName, or that an
  entitlement's ItemEntitlements lists (shipped, a mod's copy, a mod's Extends tables).
  The list is cached by pak stamps (memory and `missionunlocks/choices.json`). Kept as
  `rec['unlocks']` (and the Onslaught Editor spec's `unlocks`), a bake input digested only
  when set; a current build is rewritten in place (`bucket._reunlock`). A build checks every
  Guid against the list again: a gated one is never written, one whose mod is not installed
  is a warning and left out. Hidden for Campaign Endless, whose row grants nothing; the
  Endless twin never grants. `python3 -m riftlord.missions.missionunlocks [--pak PAK]`.
  UI smoke case `unlocks`.
- **Same title.** Two missions may share a title. A new mission's id, mod and Working Title
  come from its title with a letter suffix past any taken in Missions, whatever the case
  (`bucket._fresh_identity`: `900_North_Wings_B_P`, `RiftLordNorthWingsB`); its Guids are
  its own. The Onslaught Editor's Save as copy no longer refuses a title Missions has
  (`buildjob.title_free`), and its new missions outside Missions take letters too
  (`wavespec.fresh_names`). Tests: `tests/offline/test_samename.py`; UI smoke case
  `samename`.
- Bookkeeping is in `<Missions>/.riftlord/`: a record per mission (`records/`, what
  reopening it needs: the Map Editor's plan, the onslaught spec, the details) and
  `installs.json` (what was installed from what, by content hash, which is how a
  mission reads Installed, Out of date or Not installed). Backups of rewritten builds go
  to `<Missions>/.backups/`.
- The folder is `RiftLord/Missions` in the user's data folder unless the author picks
  another (clicking the path); a folder with no missions in it receives the current
  ones. `OMD3_BUCKET` overrides it, for testing.

Run standalone, the editors behave as before: `gamepath.TARGET` is unset, builds go to
`~mods` as paks, and `bucket.record` does nothing.

## Fab Kits

The rail's Fab Kits tab (`web/shell/src/lib/FabKits.svelte`) lists the packs in the
user's own Epic vault and converts art packs into Fab libraries in
`fablibrary.root()` (`~/.local/share/RiftLord/FabLibrary`, `RIFTLORD_FAB_LIBRARY`
overrides), which the Map Editor's Fab kits list.

- Server side: `riftlord/app/fabkits.py`, shell routes `GET /api/fab/state` (jobs,
  availability), `GET /api/fab/vault[?fresh=1]`, `POST /api/fab/convert {item, scale}`,
  `/api/fab/measure {item}`, `/api/fab/cancel {item}`, `/api/fab/delete {item}`.
  `/api/tools` `native.fab` is `fabkits.unavailable()`.
- The converter (`riftlord.fab`, its JSON command line) runs as a child process: from
  source `python3 -I` with this tree on `sys.path`, frozen `RiftLordServer --fab ARGS`;
  `RIFTLORD_FABCONVERT=<script>` runs that script instead (development, the UI smoke
  suite's `tests/ui_smoke/fakefab.py`). Each run is its own process group under
  `nice -n 10` / `ionice -c 3` (below-normal priority on Windows) with the converter's
  default workers (a quarter of the CPUs, as many as the free memory holds); on Linux
  it dies with the job thread that started it, and the server kills every group when it
  stops. Cancel kills the group.
- A convert or measure is a job kept by item while the server lives, so a reloaded page
  picks it up again; one convert at a time. A finished convert or a delete drops the
  server's open pak, catalogue and meshes of that library (`fablibrary.forget`) and tells
  the editors' frames (`riftlord-fab-changed`), whose Fab browser reads its list again.
- No converter, or no collision cooking (`omd3physx.available()`): the tab says "Fab
  conversion is unavailable in this build" and offers nothing.
- The converter's working cache (`~/.cache/rift-lord/fabconvert/<PackId>`: decoded
  sources, bakes, encoded textures, written meshes, the last stage) holds no output. At
  app start and after each convert, `fabkits.trim_cache()` runs `riftlord.fab.cachetrim`
  in a thread: a pack whose last convert began or ended more than 24 hours ago is
  emptied (its `stage_seconds.json` kept, its extracted zip in `imports/` removed with
  it); running and queued packs, and packs whose convert or terrain lock is held, are
  left alone; converts wait until the folders are moved aside. What it frees goes to the
  console under Fab Kits. By hand: `python3 -m riftlord.fab.cachetrim --dry-run`.

## Testing the window without a desktop

gamescope's headless backend gives it a display of its own; force X11 so GTK does not
find the desktop's Wayland socket, and capture with plain `XGetImage` (gamescope's X
server refuses MIT-SHM, so ffmpeg's x11grab and ImageMagick cannot):

    env -u WAYLAND_DISPLAY gamescope --backend headless -W 1500 -H 920 -- ./script.sh
    # inside: GDK_BACKEND=x11; xdotool drives it

## Automated checks

### Test servers (`testservers.py`, repo root)

Every suite that needs a running server goes through this, so none of them touches the
user's data, guesses ports or leaves processes behind.

    import testservers
    with testservers.TestServer('workshop', frozen=True,          # or 'riftlord' / 'onslaught';
                                missions=['900_Beset_Alpha_3_P']) as srv:   # frozen=False: source
        srv.urls                    # {'shell', 'riftlord', 'onslaught'}: the ports it really bound
        srv.get('/api/tools')       # GET with the right guard header (tool='shell' by default)
        srv.post('/api/plan/Scenario_09', plan, tool='riftlord')
        srv.dirs['Missions']        # its scratch Missions root (also data, cache, config)
        prof = testservers.browser_profile()   # a Chrome profile folder, removed afterwards

- Each server gets fresh `XDG_DATA_HOME`, `XDG_CACHE_HOME`, `XDG_CONFIG_HOME`, `TMPDIR` and
  `OMD3_BUCKET` folders under `/mnt/2tb_pro_a/tmp/rift-lord-tests/`
  (`RIFTLORD_TEST_SCRATCH` overrides). `missions` copies those records, their pictures and
  built folders from `~/.local/share/RiftLord/Missions`, which is only read.
- It runs at `nice 10` / `ionice idle` in a systemd scope capped at 20 GB, in its own
  process group. `stop()` closes its stdin (how the app window stops it), waits 15 s, then
  kills the group (bakers included). `stop()` runs on leaving the `with`, at exit, and on
  SIGTERM/SIGHUP once `install_signal_handlers()` is called.
- Each server and profile is recorded in `~/.cache/rift-lord/testservers/` while it lives.
  `python3 testservers.py cleanup` stops what a crashed run left behind (only groups whose
  leader's PID and start time match) and removes its folders; `list` shows the registry.
- From the shell: `python3 testservers.py run [--kind K] [--frozen [BIN]] [--mission ID]
  -- COMMAND...` starts a server, runs COMMAND with `RL_SHELL`, `RL_RIFTLORD`,
  `RL_ONSLAUGHT`, `RL_MISSIONS` set, and stops the server.

### Release check (`workshop/release_check.py`)

    python3 workshop/release_check.py                    # build Linux, check it
    python3 workshop/release_check.py --windows --install    # both platforms; install if all pass
    python3 workshop/release_check.py --skip-build       # check what is in workshop/dist now
    #   --bake RECORD  also build RECORD in the frozen server and from source; diff the paks
    #   --saves        also run the onslaught save suite (onslaughtgui.selftest --saves)
    #   --record ID    the record the checks use (default 900_Beset_Alpha_3_P); --no-ui

It builds with `build_app.py --dist <staging>` (never straight into `dist`), starts the
frozen server through `testservers.py`, and checks: the Onslaught Editor's first load
(its first requests arrive together), `/api/tools`, catalogue fx rows, materials, lava
suggest, a plan round trip keeping fx items and `look.paints`, the settings preview and
a live preview POST, the music endpoint (an Ogg `ffprobe` reads, cached), the UI smoke
suite, a clean stop when stdin closes, and that the user's `~/.local/share/RiftLord` and
`~mods` did not change. `--windows` repeats the endpoint checks on the `.exe` under Wine
(its own prefix, `~/.cache/rift-lord/winetest`; the build prefix's Python starts it with
stdin a Windows pipe, as the window does) and checks it stops when the pipe closes.
`--install` keeps each old binary as `<name>-<its date><ext>` and installs the new one,
only when every check passed. Logs, `report.json` and the staged build go to
`/mnt/2tb_pro_a/tmp/rift-lord-tests/release-check/<time>/`.

### UI smoke suite (`tests/ui_smoke/ui_smoke.js`)

    node tests/ui_smoke/ui_smoke.js --shell http://127.0.0.1:PORT [--mission ID] [--cases onslaught,mapeditor,preview] [--json FILE]
    python3 testservers.py run --frozen --mission 900_Beset_Alpha_3_P -- node tests/ui_smoke/ui_smoke.js

Headless Chromium (`/usr/bin/chromium`, ANGLE on the GPU: software WebGL takes minutes
to draw a level; `RL_CHROME_FLAGS` overrides) drives: the Onslaught Editor with the
mission (`+ Wave` adds one; a door tab, then Timeline), the Map Editor with the mission
(the level loads; Music goes play, spinner, stop, play on the browser path, and the
dropdown and button keep their widths), and the Onslaught Editor's Enemy Preview tab for
the mission (it shows an image). Uncaught page errors fail a case; console and HTTP
errors are listed with it. Exit 0 all passed, 1 a case failed, 2 it could not run.
The `fab` case (Fab Kits) runs in full only on a server started with
`RIFTLORD_FABCONVERT=tests/ui_smoke/fakefab.py` (it inherits the variable, so
`RIFTLORD_FABCONVERT=... python3 tools/gate.py --ui fab` works); with any other converter
it checks only the tab's state.
puppeteer-core 23.10.4 lives outside the repo in `~/.cache/rift-lord/uitest/node_modules`
(`RL_UITEST_MODULES` overrides); to reinstall:
`mkdir -p ~/.cache/rift-lord/uitest && cd ~/.cache/rift-lord/uitest && npm install puppeteer-core@23.10.4`.
Its browser profiles go on scratch and are recorded for `testservers.py cleanup`.

### Pak inspector (`pakinspect.py`, repo root)

    python3 pakinspect.py PAK_OR_BUILD_FOLDER [--against OTHER] [--json] [--installed] [--all-sizes]

Mod key; tables (`Tables/`, `Extends/`) with row names; OrcsMustUI files; per level the
RiftLordFx actors by effect, the RiftLordSky pieces and BuiltData; LavaRock materials;
minimap and MapData; the enemy preview's creatures (Enemies01 slots); and
`makemod.registry_problems`. `--against` diffs two builds with each mod key folded to
`<mod>`; size changes under 1 KB (name lengths) are counted, not listed.

### Missions records (`python3 -m riftlord.records`)

    python3 -m riftlord.records list
    python3 -m riftlord.records show ID [--path plan.look]
    python3 -m riftlord.records check ID
    python3 -m riftlord.records backup ID | backups [ID]
    python3 -m riftlord.records diff ID [BACKUP | --against FILE] [--full]
    python3 -m riftlord.records restore ID BACKUP
    python3 -m riftlord.records set ID plan.world.music='"War_02"' [--dry-run]

`--root` (or `RIFTLORD_RECORDS_ROOT`) picks the Missions folder; by default it is the one
the app uses. `check` warns about effects saved without `kind: fx`, paints on missing or
removed pieces or with unknown materials, waves at spawners the plan lacks, and a build
that is stale. `set` and `restore` back the record and its pictures up first (to
`~/.cache/rift-lord/record-backups/`, or `--backups`), `set` runs the Map Editor's save
rules on the plan and bumps `updated`. Close the mission in the app before writing.
