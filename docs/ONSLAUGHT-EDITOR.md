# Onslaught Editor

A desktop tool for authoring OMD3 onslaughts. It ships separately from the OMD2
porting project, because it is useful to any mod author with no OMD2 level
involved.

## Building a shippable binary

```
python3 onslaughtgui/build_app.py
```

That builds the UI and freezes everything into one executable in
`onslaughtgui/dist` -- about 20 MB, and the author needs no Python, no pip and
no Node. **PyInstaller does not cross-compile**, so the Windows `.exe` has to be
built by a Windows Python and the Linux binary by a Linux one; the command is the
same on both, which is why `.github/workflows/build-editor.yml` just runs it on
two runners. `--skip-ui` reuses the last UI build, `--onedir` makes a folder that
starts faster than the single file.

A Windows Python under **Wine** counts, which is how the shipped `.exe` is built
here without a second machine:

```
export WINEPREFIX=/path/to/prefix
wine python-3.12.8-amd64.exe /quiet InstallAllUsers=1 PrependPath=1 \
    Include_test=0 Include_doc=0 Include_launcher=0
wine "C:\Program Files\Python312\python.exe" -m pip install pyinstaller pillow
wine "C:\Program Files\Python312\python.exe" onslaughtgui/build_app.py --skip-ui
```

`build_app.py` needs no changes: it branches on `os.name`, which is `nt` under
Wine, so the `--add-data` separator comes out right. Exercise the result under
Wine before shipping it -- that is evidence, not a Windows smoke test.

## Releasing

```
python3 onslaughtgui/package.py --check
```

writes `onslaughtgui/dist/OnslaughtEditor-<version>.zip` from whichever binaries
are in `dist`, and `--check` unpacks it somewhere empty and drives a real export
through the packed copy. The version is `__version__` in `onslaughtgui/__init__.py`
and nothing else keeps a second copy -- the zip name, the shipped README, the
`--version` flag, the startup line and the UI status bar all read it from there.

The zip's `README.md` is built from **`README-release.md`**, which is the file an
author reads; this one is for whoever works on the tool.

Building needs `pyinstaller` and `pillow`. On a distro whose Python is
externally managed, use a virtualenv:

```
python3 -m venv .buildenv && .buildenv/bin/pip install pyinstaller pillow
.buildenv/bin/python onslaughtgui/build_app.py
```

## Running it from source

```
cd web && npm install && npm run build && cd ..
python3 -m onslaughtgui
```

`python3 -m onslaughtgui --no-window` serves without opening a window and prints
the address, which is what the screenshots and any scripted checks use.
`--browser` forces the default browser over the system webview.

## Finding the game

The editor reads the game's own paks, so it has to find the install. In order:
`OMD3_INSTALL` in the environment, the folder the author chose last (remembered
in the platform's config directory), the install this binary sits inside, then
every Steam library from `libraryfolders.vdf` -- registry-located on Windows,
and the usual roots on Linux including the Flatpak one. If none of that finds
it, the editor opens on a panel asking for the folder rather than failing.

Caches go beside the code when run from a checkout, and to the user's cache
directory when frozen -- a one-file build unpacks to a temporary directory that
is thrown away each launch, so nothing durable can live there.

## Shape

The UI is a thin client. Every asset operation stays in the converter's Python,
which is the code already proven in game -- `bppatch` for coordinator bytecode,
`mapdata` for minimaps, `makepreview` for the hub diorama, `pakv4` for writing.
The frontend never parses a `.uasset`.

```
__main__.py    launch: start the API, show a window
server.py      stdlib HTTP -- JSON API plus the built UI, loopback only
gamedata.py    maps, minimaps, doors, routes and units, shaped for the UI
buildjob.py    background builds, polled by the UI
wavespec.py    turns an authored spec into a coordinator
web/           Svelte 5 + Vite, built to web/dist
```

### Why not SvelteKit, Electron or Tauri

Python has to ship whatever the frontend is, because the asset logic is Python.
So "no dependencies for the author" is solved by freezing Python, not by the
frontend choice -- and once Python is frozen and serving a local API, Electron
would mean shipping a second runtime for around 200 MB to get a window the
system webview already provides for about 15 MB. The author's own browser is the
fallback, so a missing webview cannot break the tool.

SvelteKit's router and SSR earn nothing here; the tool is one page. Plain Svelte
with Vite is the same component language and builds to static files that the
Python server hands out directly.

## The layout

Minimap on the left, the wave form on the right.

- **Tabs are doors.** Each door on the map is a tab, and an unstable rift -- a
  spawner with no door, which is how the war scenarios do it -- gets one too.
- **Waves are sections** down the form, so a door's whole onslaught reads top to
  bottom. `Jump to` scrolls between them and `+ wave` appends.
- **The Timeline tab holds what belongs to the wave rather than the door**: how
  each wave ends, and the events the coordinator fires by name. Bosses live
  there because the campaign spawns them with `OnslaughtAction`, not from a
  spawner. A Music change event has a play/stop button, the same one as the Map Editor's
  Music field (`web/shared/musicpreview.js`). Inside Rift Lord the app window plays it
  through the shell (`riftlord-music`), fetching the Map Editor's `/mapeditor/api/music/`
  (the only paths the window fetches, `shell/window` main.rs `music::PATHS`); in a plain
  browser the page plays its own server's `api/music/<track>` (`levelmusic.preview`).
- **Dialogue** is a Timeline section of its own. It holds captioned lines in order: plan and
  spec `events` of kind `dialogue`, `{wave, when, delay, speaker, text, duration}`, checked
  by `hostevents.clean_line`. Each line has:
  - **When:** Wave Start, Break After Wave (not the last wave), or After Previous Line (not
    the first line; it takes the wave of the line before it);
  - **Delay** (0-300 s), **Speaker**, **Length** and the **Line** itself;
  - Length is 0.7-20 s. Left blank, it comes from the text, using the fit of the 261 shipped
    captioned lines (`captions.derived_duration`, `spec.js` lineLength); the field shows it as
    its placeholder.

  Lines are added with **+ Line** and reordered with ↑ ↓. A bad field is marked red and listed
  as an error, which blocks the save. The section works only with the Mission Events piece
  placed (`caps.dialogue`); without it, it shows why and **+ Line** is disabled. The build
  writes one silent captioned SoundWave per line, and the piece plays it through the game's own
  closed captions (docs/research/CLOSED-CAPTIONS.md). Lines play from a campaign or Custom Maps,
  never in Endless or Scramble. A changed line marks the mission's build
  stale, since the piece is made in the bake. In the Map Editor's Mission Events list, lines
  are only counted. UI smoke case `dialogue`; offline `tests/offline/test_missionevents.py`.
- **The map tracks the tab.** The active door is fully opaque with its route
  lit; the others are dimmed but still visible, because the author needs to see
  where they are. On the Timeline tab no door is the relevant one, so all read
  at full strength. Clicking a door on the map selects its tab.
- **Floors.** A map whose MapData has several MapVolumes gets a **Floor** dropdown
  (Floor 1 is the lowest volume, `gamedata.map_floors`). Every point the server sends
  carries `f`, the volume holding it (`mapdata.pick_volume`), and `p`, its uv on each
  volume; the pane draws the chosen floor's minimap and fades marks and routes on other
  floors. A door's tab shows the door's floor, a Rift Group's tab the floor most of its
  rifts are on; the Timeline keeps the floor shown. The frame covers every floor, so
  changing floors does not move the map; every floor's minimap is fetched when the map
  opens, so a floor change draws at once. Offline `tests/offline/test_onslaught_floors.py`,
  UI smoke case `onslaughtfloors`.

**A spawn is dragged by its grip**, and can be dropped between rows of its own
wave or into another wave on the same door tab. `web/src/lib/drag.svelte.js`
holds the move; `test_drag.mjs` exercises it without a browser
(`node test_drag.mjs onslaughtgui/web/src/lib/drag.svelte.js`), because a
headless browser cannot drive HTML5 drag and drop.

**Choosing a map clears the old one first** and covers the page while the new
one is read -- the previous minimap staying up made a slow read look like
nothing had happened.

`?map=Scenario_13&tab=door-2` reopens a map and tab directly; `&theme=light`
picks a theme.

**The game mode** shows as a badge in the header (`riftlord/onslaught/gamemode.py`),
keyed by the GameType the build writes to CustomMaps.yaml: **Rift Defense** (no
GameType, the game's own) or **Crystal Defense** (`CrystalDefense`, a Crystal placed).
It comes from the mission's plan (`bucket.item`'s `mode`, or the Missions record whose
build holds the map), else from the built level's placed Crystal (`map_report`'s
`pieces`), so a pak opened without its record reads the same, Rift Groups included
(`rifttear.level_view`). In Crystal Defense:

- the Timeline has no Rift points (`MODES[...]['hide']`) and has **Crystal Health** per
  difficulty; each wave lists every Rift Group's Unstable Rifts under "<group> · Pick
  Chance (%)", each available or not and its chance of being picked when the group opens
  that wave (MemberChance; 0 = not available);
- each **Rift Group** has a tab named after it (`lib/RiftGroup.svelte`): **Opens** (the
  group's lock and its triggers, `lib/Trigger.svelte`, each on two lines: **When** [Wave n |
  The Wave After Crystal Health ≤ x % | From Wave n After Crystal Health ≤ x %] (`wave`,
  `at`, both with `both`), then **Opens At** [Seconds Into the Wave n s | Enemies Left ≤ x %]
  (`launchAfter`, `launchAlive`, at most 75), **Open** n Rifts (`open`, at most the group's
  rifts) and **Repeat Every** n Waves (blank = never); Wave and Open out of range show red
  with the reason and refuse the save (`review`, `rifttear.spec_problems`). `alive` is
  never written; an older trigger is converted when it loads (`spec.js` convertTrigger: its
  Enemies Left becomes Opens At Enemies Left, from Wave 1 without Wave or Health; Seconds
  wins over Enemies Left when both were set; Health or Wave becomes two triggers)),
  **Unstable Rifts** (each one's lock; their pick chances are on the Timeline), then its
  enemy rows wave by wave with a spawn row's controls; a wave with no rows reads "Doesn't
  open", and from wave 2 on **Copy Previous Wave** replaces the wave's rows with copies of
  the wave before's. While editing, a group's rows live in each wave's spawns under `g<index>` and a
  rift's chance in `wave.riftChance` (`spec.js` injectTear / tearForSave);
- the Unstable Rifts the Crystal opens have no tabs of their own; picking one on the map
  opens its group's tab, which lights the group's rifts and their routes;
- the map draws the mode's own minimap icon (`T_CrystalDefense_MapIcon`, tinted) on the
  level's rift; a wave sent through a rift the Crystal opens is an error, and the build
  refuses it.

A save of a map with no Map Editor mission in Missions keeps its waves and warns that the
Crystal's settings were not saved. Tabs that do not fit sit behind a **…** tab
(`lib/TabStrip.svelte`). A new game type adds a `MODES` row and its piece to `PIECES`. UI
smoke cases `gamemode`, `crystalhealth`; offline `tests/offline/test_gamemode.py`.

## What the editor knows

Rules it applies for the author, all of them established by the tooling rather
than guessed:

- The final wave carries no break, because a break terminates a wave. The
  Timeline says so instead of offering a control that would be dropped.
- A spawn gated to one difficulty is invisible on the others. Difficulty
  defaults to 5, which applies on all of them, and any other value is marked in
  the row and called out before building.
- A wave that sends nothing never ends, so it is an error rather than a warning.

The mapping of difficulty bytes 0 to 4 onto the game's named difficulties is
**not confirmed**, so the UI shows the raw number rather than inventing a label.

## Building

**Build pak** posts the spec, and `wavespec.build()` writes a verified pak into
`Content/Paks/~mods`. **Save spec** writes the same JSON to a file.

`wavespec` maps the spec onto the coordinator model:

- **Spawns** are read from the DECLARATION, never from the wave ladder's block.
  An action-prefixed wave declares its spawns outside the block entirely --
  Scenario_05's fifth wave and Scenario_09's both do -- so a block reader finds
  them empty and silently writes nothing.
- **Surplus spawn calls are removed**, not hidden behind a difficulty that does
  not apply. Hiding them brings them back on that difficulty.
- **Extra waves** are cloned from a wave that carries a break and dispatched by
  an appended test chain, which needs no edit to the existing ladder whatever
  its shape. Chains are appended in increasing wave order so the highest is
  tested first, and one per spawner falls back to the previous.
- **A door a wave never used** is opened the same way, with its chain's closer
  rung pointed back at the ladder so it decides nothing.
- **A missing break import is added.** Half the coordinators call only one kind
  of break and import only that one, so Scenario_09 has nothing to point a
  WaveBreak at; a sibling Onslaught import gives the class and outer.
- **Wave events** are cloned `OnslaughtAction` statements. An event the author
  did not ask for is dropped, so a map's own boss does not turn up uninvited.

Every build re-reads the finished coordinator and checks it declares what was
asked for -- wave count, the actual units each wave sends (whichever way the
coordinator names them), spawn calls per wave, break kinds, events, and that
`OnslaughtEnd` sits exactly one past the last wave. That last one is what stops
the wave enumeration; get it wrong and the map hangs while loading. None of the
table checks touch bytecode, and a structurally perfect edit can still be
semantically wrong, so this readback is the check that matters. A failure
refuses to write a pak.

**All 33 maps are authorable**, measured by building a three-wave onslaught on
every one.

Two of them name their units differently and the builder handles both.
Scenario_Ice_01 -- alone among the 48 coordinators -- declares 22
`OMDSoftProtoPtr` Blueprint variables, one per enemy, and references a variable
from each of its 188 spawn calls rather than pasting the proto struct in. It is
a designer's palette, not a defect: five of the variables are never used, and
the names are the designer's rather than the proto rows' (`KoboldKamikaze`
holds `KoboldTNT`). Such a map is authored by pointing each call at a variable
and rewriting spare variables' defaults in the class default object, which caps
it at **22 distinct units** -- reported in the map facts and checked before
building.

## Not finished

Packaging (PyInstaller plus pywebview) is not set up, so the tool still needs a
Python with Pillow. Nothing built through the editor has been confirmed in game
yet.

## Bundling into an installed map's pak

`spec.bundle` writes the authored onslaught **into the chosen map's own mod pak**, so
the author ships one file rather than a map pak plus an onslaught pak.

The ordinary build copies the chosen level into a mission of its own. For an installed
map that means a second copy of the map and a second mission -- the map shows up twice
in the menu. Bundling instead rewrites the map's existing mod: its coordinator is
replaced with the authored one and its hub preview regenerated, and everything else in
the pak is carried across untouched, **including the level and its flight navgraph**.
Nothing new is registered, because that mod already has its mission and campaign rows,
so its title, campaign and unlock chain are left alone. A par time set on the Timeline
(`spec.par`) is the one row field a bundle writes: every row of the mod's mission table
(`wavespec.set_par`, read back by `par_checks`).

**Always, for a map under `/Game/Mods/`, and never for any other.** There is no switch:
the page sends `bundle` for every installed mod map (inside Rift Lord the mission being
saved decides, `buildjob.placed`). Writing into a shipped map's pak would mean
redistributing base-game level assets, so the server refuses it even if the request is
made directly.
`wavespec.bundleable_maps()` is the one place that rule lives, and `gamedata.maps()`
reports it per row as `bundle`. `navpatch.mod_maps()` on its own is too loose: it also
reports `Dungeons`, which is base content living under `/Game/Maps/CustomMaps/`.
