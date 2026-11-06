# Rift Lord

Add spawners, doors, AI routes and onslaught waves to a level OMD3 already ships.

    python3 run_riftlord.py          # http://127.0.0.1:8850

Separate from the assembly tool on purpose. That one converts OMD2 maps and most of
it is about OMD2's formats; this one starts from a working OMD3 level, which is a
different job with different risks.

## What it does

* Draws a shipped level in 3D from its own packages, textured. Split Stairs is 1,256
  placements of 106 distinct meshes, drawn one `InstancedMesh` per mesh.
* Shows the level's existing spawners, doors, path points and routes in cool
  colours; anything added is warm, so a new route never blurs into an old one.
* Places a spawner, a door and a chain of path points by clicking, with a
  move/rotate gizmo (`G` and `R`) and numeric fields for the cases the gizmo cannot
  reach.
* Adds onslaught waves per spawner, and shows what the mission hub's enemy diorama
  becomes -- a level with no flyers in it becomes a level with flyers, and the
  preview has to say so.

## It never edits a level

The tool holds a **plan** in `riftlord-plans/<level>.json`: the spawners, doors and
points to add, and the waves to give them. An emitter applies a plan to a copy. A
change buried in a 3,884-export package cannot be reviewed; a plan can be read,
diffed and kept.

## It shows where navigation reaches

The **navigation coverage** layer (off by default -- it boxes in the whole play area)
draws two very different kinds of answer, and deliberately does not blur them:

* **Flight is exact.** The volumetric navgraph is decoded, so every convex volume and
  every portal joining two of them is a real boundary, drawn as geometry. A portal is
  literally a face of the volume beside it.
* **Surface is a bounding box and nothing more.** The walkable graph's record layout is
  not cracked, so the honest statement is the world box it covers. It is drawn faint
  and labelled as an extent, never as a floor.

The check that earns its keep while authoring: **a flyer path point outside every
flight volume is marked red**, because that is a route into airspace the graph does not
cover. Every flyer point on both shipped flight levels falls inside a volume, which is
how the decode was cross-checked.

The panel says what it could not read. On `Scenario_Sand_01` only 3 of 9 volumes decode,
so it says so -- **absence of a volume in the drawing is not evidence** there.

## A flyer route gets its navgraph automatically

Flyers move on NavPower's **volumetric** navgraph, and a level that ships none cannot
move them however well the route is authored -- that was the dead end for months. The
emitter now generates one from the plan's own route and installs it into the copied
level, so **placing a flyer spawner and a chain of points just works**.

Nothing to configure. The build report says what it made:

    flight: ['added a 1432 byte flight graph: 6 volumes (0 kept from the level, 6 authored), 5 portals']

The graph is **generated from nothing** (`navvolgen.py`), not edited into a copy of
some shipped map's buffer. The whole volumetric format is decoded -- a strict
sequential reader consumes all ten shipped graphs and re-emits them byte for byte --
so there is no donor, no volume-count cap and no inherited shape. Routes of any
length and any topology work; `navvolgen.py --selftest` is the regression test.

Four things decide whether such a graph actually flies, all settled in game and all
handled here (see the **omd3-navpower-format** skill for the evidence):

* the kd-tree that indexes the volumes is **built for the volumes it ships with** --
  a stale one leaves the runtime unable to resolve a point to a volume, and flyers
  stall at the first node;
* the **degree word is an array length**, not a count: it is followed by that many
  `(portalIndex, faceIndex)` pairs. Writing a degree without the pairs desyncs every
  later record, which is why hand-built graphs used to hang and then crash. Both are
  now written together, so any topology is fine -- including forks;
* a portal spans the **whole cross-section athwart the route**. A shipped portal is
  small because it is a doorway in a wall; between two slices of the same open
  corridor there is no wall, and a small opening makes a flyer detour to it; and
* the corridor is **wide** (1200uu laterally, 700 vertically) and consecutive volumes
  **overlap rather than tile**. A flyer that breaks off to fight has to stay inside a
  volume or NavPower hauls it back instead of letting it act.

The route is read from the plan, not from the level, because at build time the level
does not exist yet -- plus the level's own **exit point**, which an added route ends
by branching to and which is therefore never in the plan.

Every flyer spawner's route goes into **one** graph, because a level has exactly one
volumetric buffer. The level's own volumes are kept, so adding a route to a map that
already ships flight does not delete the routes it came with; anything the emitter
would generate itself is regenerated rather than merged, so saving repeatedly is
idempotent. A bad graph **blocks the build** -- it is verified against its own
invariants and by reading the written package back, because the game's only way of
complaining is to hang and crash.

## Several flyer spawners, and routes that meet

Every flyer spawner's route goes into **one** graph, because a level has exactly one
volumetric buffer. Routes that share waypoints **share volumes**: they are split at
their junctions into legs, each unique leg is generated once, and the graph gets a
real merge where they join.

That matters more than it sounds, because two spawners always converge -- every added
route ends by branching to the level's one exit point. Generating a corridor per route
produced two heavily overlapping parallel corridors, and since the kd-tree is a
partition a flyer in the shared region is resolved into exactly one of them, possibly
the other spawner's, whose volumes run the other way. Nothing crashes; the flyer just
goes wrong.

A route with no sharing is a single leg, so its graph is byte-identical to generating
it directly -- which is the shape confirmed working in game.

Adding a route to a map that **already ships flight** works too: the installer
replaces the buffer rather than refusing, and the level's own volumes are kept so its
shipped flyer routes still work. Where an authored volume shares airspace with one of
the level's own, that is reported and not enforced -- it is ordinary map design, and a
false alarm in a tool people drive by hand costs more than the risk.

## The onslaught is NOT generated

Flight navigation is automatic. The **coordinator arm is not**: a plan's new spawner
ships with no arm and therefore spawns nothing, and its waves are authored in the
Onslaught Editor afterwards. That is deliberate, not an omission -- emitting an arm
hangs the game on load, bisected one launch per variant:

| variant | result |
|---|---|
| level copy alone | loads |
| + spawner, 9 points, branches | loads |
| + one arm and a close chain | **hangs** |
| + four arms, no close chain | **hangs** |

An idle spawner is a supported design and nothing offline flags it, so measure it:
`flowsim.calls_run(coordinator, waves, spawners)` simulates the real dispatch, and an
unreached spawner shows as **zero calls for its index in every wave**.

A save **preserves** a coordinator already authored for this mod, because waves live
in the mod's own coordinator and `keep_coordinator` would otherwise copy the shipped
level's ladder over them on every rebuild. Reuse is allowed only while every spawner
index the old coordinator targets still exists -- it dispatches on index. Adding a
spawner is fine (it is simply idle); removing one rebuilds instead and says where the
authored waves still are. The build report carries `coordinator`; `fresh_waves=True`
forces a rebuild.

## Mission events

`plan['events']` (and the Onslaught Editor spec's `events`, mirrored into the plan by
`bucket.record`) holds the mission's scripted events as designer data
(`workshop/research/MISSION-SPECIAL-EVENTS.md`):

| kind | fields | build |
|---|---|---|
| `boss` | `wave, unit, door` (spawner index)`, count, reaction` | always a spawn row; `reaction` also fires the host's `Spawn<Boss>` action when the level hears it for that unit |
| `music` | `wave, track` | `BattleMusicChange` at that wave; the `_Audio` copy's second loop set to `track` (`levelmusic.retarget(change=)`), the opening loop stays `world.music` |
| `rift` | `wave, rift, open` | `OpenRedRift_NN` / `CloseRedRift_NN` (Scenario_18 hosts) |

`hostevents.capabilities(map)` reads what a host supports from its cooked coordinator
(the `DoOnslaughtAction` switch and its delegates), the level script's
`AddMulticastDelegate` binds, and the `_Audio` sublevel's two loops (opening loop =
the cue spawned on `[MUSIC] Go Break Music Stop`, second = the one `ChangeMusic`
spawns). `wavespec.with_events` writes events into the waves (a spec without `events`
has them read out first, `split_events`); `event_checks` is the gate: every fired
action has a handler and a bound listener, every asked-for action is fired. Each event
is reported `applied`, `applied without reaction` or `not possible on this host`; the
probe prints the same. `save_plan` keeps `events` (`hostevents.clean`).

The coordinator rename keeps the bind: the level's `AddMulticastDelegate` names the
delegate on the mod coordinator's class (checked offline on Host Events A).

### Mission Events piece

On a host whose level script hears no event (19 of 33 maps), the Map Editor's
**Mission Events** button (under New Object) places one piece per level: plan
`added` item `{kind: 'events', name, matrix}` (kept by `save_plan`, one only).
`riftlord/missionevents.py` builds a per-mission bpkit actor
(`/Game/Mods/<mod>/<mod>_MissionEvents`) and places it in the level copy (lifted from
MainMenu_V2's `EnemiesSpawnRoot`, class and default object in serialize-before-create).
It reads the game state, not the coordinator: a **music change** swaps the playing
`MX_OMD3_*_LP_SC` loop's cue (`SetSound`) once `CurrentWave` reaches the event's wave,
no break is active and enemies have spawned; a **boss reaction** plays the boss's
shipped reaction cues when `OnBossSpawned` fires in or after the event's wave. Only
events the host cannot do itself go to the piece (`missionevents.duties`).
`hostevents.with_piece` adds those kinds to the capabilities (built files with the
piece, `bucket.item`'s `eventCaps` for the Onslaught Editor); `apply_events` reports
them `applied` "by Mission Events" and writes no action. Probe gates:
`missionevents.check_files`.

**Dialogue lines** (set on the Onslaught Editor's Timeline, kind `dialogue`) are always the
piece's. Each is a silent SoundWave at `/Game/Mods/<mod>/Dialogue/DX_<mod>_Line<n>` carrying
one subtitle cue, "<Speaker>: <line>" (`riftlord/build/world/captions.py`). The piece plays
it with `SpawnSound2D` at its wave's start, in the break after its wave, or after the line
before it, each after its delay (`K2_SetTimer`). It does not play while the player's Scenario
Dialogue setting is off, or in Endless or Scramble: the server decides once from
`GetGameDifficulty` and `OMDGameInstance.IsInRandomModeCampaign` and replicates the decision
(`Talk`), so a piece with lines replicates (always relevant). The game's closed captions draw it, with Closed Captioning on
(docs/research/CLOSED-CAPTIONS.md). Gate: `missionevents.dialogue_checks`.

The piece works on levels from scratch too (`apply.apply_plan` and `emit.build` place
and build it on any level; a scratch level keeps its host's `_Audio`, so its music
change swaps that loop).

**Unstable rifts on any host.** New Object's gameplay card **Unstable Rift** places a
spawner with `unstable: true` (plan `spawners[]`; `planactors.clean` keeps the flag and
makes it a doorless ground spawner). It is lifted as Scenario_18's red rift spawner
class `BP_OMDAISpawner_VorwickRift_C` with `bIsActive` false and its own index and ground
route. Events `{kind: 'rift', wave, rift: <spawner index>, open}` (Onslaught Editor
Events list) go to the piece: on an open event's wave, after the break (or once enemies
spawn), it finds the rift by `indexInLevel` and calls the class's own `Open Rift`
(effects, sounds, minimap ping, local camera shake, `bIsActive` on the server); a close
event calls `Close Rift` as soon as its wave is reached. The onslaught sends units through
the rift's index like any added spawner. Gates: `missionevents.rift_problems` (piece
present, every rift opens, closes after it opens, no wave spawns through it while
closed; also refuses the bake), `check_files` (rift placed closed at its index, lifted
exports read back, the Blueprint names `Open Rift`/`Close Rift`), probe
`rift_flow_checks` (flowsim: the coordinator sends each rift exactly the waves the spec
asks). Offline checks: `python3 test_missionevents.py`.

**The Crystal (Crystal Defense).** New Object's gameplay card **Crystal** places
`BP_Crystal_C` (Mods/CrystalDefense, a child of the trap base) once per mission, on the
rift's base (the top of the rift-category pieces within 450 uu of the rift, else the exit
point, else 210.3 uu under the rift), turned to face the mean direction of the route
points that lead into the exit (`GET /api/crystal/<level>?rift=x,y,z`,
`riftlord/mapeditor/crystal.py`). It draws as its own model (`crystal.model_parts`: the
pieces its manifest brings, then `SM_Crystal` from the mod pak scaled into the floating
crystal's box; a stand-in until they load; `web/src/crystalui.js`); it is moved and turned,
never sized. Its panel holds only its **Rift Groups**: add, rename and delete a group, add
and remove its Unstable Rifts (the plan's, and a built level's own). Health, triggers,
enemy rows and chances are the Onslaught Editor's (`riftlord/mapeditor/rifttear.py`). Plan
item `{kind: 'crystal', name, matrix, crystal: {mapDifficulty, [pointHealth],
[pointDifficultyShare], riftGap, waveCount, groups}}`; `difficultyHealth`,
`healthPerRiftPoint` and `minHealth` of older plans are dropped on load and save. A built level that places a Crystal (opened with no record) opens with it as the
plan's item (`crystal.adopt`: transform and groups read back from its arrays, its rifts as
the level's own, `-indexInLevel`, and `level` naming it); the build then writes that actor in
place. A property is written only when it differs from the class default. The build copies
nothing of the mod (the mission requires `~mods/CrystalDefense_P.pak`; the class is read
from it, and the card shows only while Supported Mods detects it, see below), and writes
`GameType: CrystalDefense` on the mission's CustomMaps.yaml entry (read back by
`customcampaign.finish`; a details edit keeps it). A rift the Crystal opens needs no
Mission Events piece and no open event (`missionevents.rift_problems`).
**Hitbox** (`riftlord/mapeditor/crystalfit.py`, 2026-10-30): the panel's HitX / HitY are the
hitbox and NavPower obstruction half extents and HitDrop how far both reach below the actor's
root (blank: found at build; placeholder: the value found on the level's own floor,
`GET /api/crystal/<level>/fit?matrix=`; a HitDrop of 0 is the user's own). After the walkable
graph is final the build writes, where the user set none, HitDrop = the root's height over the
highest walkable floor under the box (North Wings: 56.1, the Crystal on a rift base that is not
floor), and the largest box (from the mode's 486 x 549 down to the foot art's 386 x 449) whose
four sides have reachable floor 50 uu outside them. It warns about sides past the floor, a
Crystal too big for its platform, a user value that does not fit, a user HitDrop deeper than the
box is tall (407.9), and no walkable floor within that depth under the Crystal. Gates:
`crystal.gates` (probe); offline `python3 -m riftlord.mapeditor.crystal --check DIR`,
`python3 tests/offline/test_crystal.py` and `tests/offline/test_gamemode.py`; UI smoke
cases `crystal`, `crystallevel`.

### The Mission Events list

The Map Editor's **Mission Events** button opens the list of `plan.events` in the right
sidebar (`web/src/eventsui.js`): add or remove a music change (wave, track), a boss (wave,
unit, spawner, reaction) or an unstable rift opening or closing (wave, rift). The choices
come from `GET /api/events/<level>?rifts=<plan's unstable rift indices>`
(`missionevents.editor_choices`: the host's own capabilities, the same with the piece,
the tracks and the bosses). When an event needs the Mission Events piece (the host
cannot do it itself, `eventsui.needsPiece` = `missionevents.duties`), the piece is placed
near the view, once per level. A boss row's other fields (`count`, `row`) are kept as
the Onslaught Editor wrote them. The same list shows in the piece's own panel.

### Mission scripts

A one-off event of one mission (North Wings' campaign opening, 2026-10-30) is a bpkit script
of its own, carried in the record: plan `added` item `{kind: 'script', name, matrix, source,
settings, actor}` (`riftlord/missions/missionscript.py`; `save_plan` keeps it whole). The
build compiles it into `/Game/Mods/<mod>/<mod>_<name>` (a child of Actor) and places it in the
level copy as the Mission Events piece is placed (`missionevents.place_actor`). `settings`
replace the script's top-level defaults (`Name: type = value`) by name, so a timing is tuned in
the record; a setting the script does not declare is refused. `actor` takes bpkit's actor keys
(`replicates`, `always_relevant`, ...) and `tick`. The Map Editor draws nothing for it and keeps
it through a save. Edit a record's scripts with
`python3 -m riftlord.missions.missionscript put|remove|show RECORD ...` (the record is backed
up first; close the mission in the app); `check SCRIPT.py` compiles one alone. Probe gates:
`missionscript.check_files` (in the pak, imports every asset it names, placed with its class
preloaded). Offline: `python3 tests/offline/test_missionscript.py`.

## Painted materials

`plan.look.paints` paints the level's own pieces, `added[n].paints` the plan's added
meshes and kit pieces. A value is one material instance name (slot 0, which is every
slot a one-slot mesh has) or `{slot: material}` per material slot ("0", "2": slots left
out keep the mesh's own material, written as a null OverrideMaterials entry):

| where | key | value |
|---|---|---|
| `look.paints` | level piece id (a `component` piece, or a Blueprint part with its own component export, `piece.component`) | `"Mat"` or `{"0": "Mat", "2": "Other"}` |
| `added[n].paints` | part: `""` for an added mesh's own component, else a kit piece's component name (`/api/kit` `parts[].component`) | `"Mat"` or `{slot: material}` |

`leveldata.save_plan` checks every name against `leveldata.materials()`;
`lavafx._rock_paints` / `apply.paint_look` write `look.paints` (lava rock materials still
become the mission's own copies), `lavafx.paint_added` writes `added[n].paints` on the
placed actor's components. The Map Editor's Material section shows one field per part
and slot for one piece (up to 24), one field for everything when several pieces are
picked, and only when every picked thing takes a material.

## Collides

The Selected panel's **Collision** section has a **Collides** toggle for added kit and
mesh pieces (`added[n].collision: false`) and for the level's own pieces, moved or not
(`edits[n].collision`: true or false, stored only while it differs from the shipped
state; an edit may carry it with the piece's shipped matrix, and switching back drops an
edit that holds nothing else). It is hidden for Blueprint parts, foliage and instanced
pieces, and meshes with no collision shape (no simple collision element and not
complex-as-simple: `riftlord/data/omd3-mesh-collision.json`, rebuilt with
`python3 -m riftlord.mapeditor.piececollision --meshes`).

A level piece's shipped state (`collides` on `/api/level` pieces) is read as the engine
resolves it: with `bUseDefaultCollision` (stored, else true on a StaticMeshActor's
component) the mesh's own profile, else the component's `BodyInstance`. The build writes
off as shipped decor is switched off (`bUseDefaultCollision` false, BodyInstance
NoCollision); on as a colliding StaticMeshActor is (both properties removed, so the mesh's
profile applies), or `bUseDefaultCollision` false with a BodyInstance naming BlockAll
when the mesh's own profile is NoCollision. The walkable-graph rebuild takes switched
pieces in or out (`navregen.placed_collides`); an edit that only switches collision is
not a move for precomputed visibility or the light bake (`piececollision.moving_edits`).

The rebuild resolves every other mesh component by the same rule
(`navregen.component_flags` takes `piececollision.level_collides`, which applies
`piececollision.resolved` to every mesh component of every package). Before 2026-11-05 it
read the BodyInstance alone, so 135 pieces on 18 shipped levels that collide in game (a
StaticMeshActor whose BodyInstance says NoCollision while `bUseDefaultCollision`, unstored,
takes the mesh's BlockAll) were left out of regenerated graphs. Taking them in changes the
regenerated graph of those levels (`navregen --level`; polygons of agent 0, then agent 1):

| level | pieces | polygons before | after |
|---|---|---|---|
| Scenario_06 | 4 | 769 / 670 | 804 / 675 |
| Scenario_08 | 5 | 672 / 613 | 633 / 574 |
| Scenario_Other_02 | 4 | 306 / 287 | 307 / 304 |
| ScenarioDLC_01 | 3 | 196 / 189 | 204 / 192 |
| Scenario_Ice_02 | 10 | 377 / 288 | 382 / 290 |
| Scenario_18 | 15 | 1,802 / 1,512 | 1,795 / 1,512 |
| ScenarioDLC_04, Scenario_04, 07, 10, Ice_01, Ice_03 | 1 to 28 | | 1 to 3 polygons either way |
| ScenarioDLC_02, Scenario_01, 09, Other_03 | 1 to 11 | | unchanged |
| Scenario_Sand_01, Sand_02 | 1, 19 | no polygons | no polygons (both rebuild since 2026-11-05: "What the walkable-graph rebuild reads") |

No route leg is joined or cut by it on any of them, nor on the Missions records on those
levels (Repaired Mine, Raid, Pier Pressure, Beset; North Wings does not regenerate).

## Volumes in sublevels

The level's blocking, kill, camera and trap volumes are read from every package of the level
(`volumes.level_volumes`: the persistent level, then each sublevel of `omd3level.packages`).
141 of the shipped levels' volumes sit in sublevels, on 15 levels: 45 stop players, 17
stop everyone (all on Scenario_18), 30 the camera, 43 trap placement and 6 kill
(Scenario_09 has 46, Repaired Mine's Scenario_Ice_02 16 trap blocking volumes). A
persistent-level volume keeps its export name as its plan name; a sublevel's is
`'<sublevel tag>|<export index>'` (as the level's own lights and effects are keyed), and
`/api/level` volumes carry `sub` (the package tag) and `title` (the export name, which the
volume's card shows).

They are shown, moved, turned, resized, removed and given Blocks or kill switches like the
persistent level's. `volumes.writes` sorts the plan's edits, and the in-the-way volumes a
ground route crosses, by the package holding each: `volumes.apply` writes the persistent
level's, and `apply.sublevel_edits` hands each sublevel's rows (`'volumes'`) to its copy,
where `volumes.write_package` writes them (`apply.apply_to_sublevel`). A sublevel volume
given the other Blocks is parked in its copy and its stand-in lifted into the persistent
level as `RiftLordVolumeR<tag><export index>`. The walkable-graph rebuild reads the blocking
volumes of every package (`navregen.blocking_volumes`) and leaves out those the plan
removes or the build parks for a route. The probe's kill-switch gate reads the sublevel
copies (`controllers.gates`). A level from scratch keeps none of its host's volumes, in any
package. Test: `tests/offline/test_sublevel_volumes.py`.

What blocks players at a shipped decor piece with collision off (2026-11-05, 33 shipped
levels, 42,057 visible non-foliage pieces whose mesh has a collision shape): 36,269 stand
nowhere near the walkable floor enemies and players use; of the 5,788 that do, 3,778 sit
at least half inside colliding pieces (the walls and column blocks they decorate), 172
inside blocking volumes, 126 inside hidden colliding pieces, 1,245 partly inside, and 467
in nothing (flush floor tiles, sunk stones and crystals). Blocking volumes are area
walls, not per-piece: 7 of 839 (players or everyone) are filled at least half by one
piece, none of them decor with collision off. So a moved decor piece leaves nothing
invisible behind; at its new place it blocks only with Collides on.

## What the walkable-graph rebuild reads

The rebuild (`navregen.graphs_for`, Recast through `recastgen`) takes the level's pieces
after the plan (`navregen.final_pieces`), its blocking volumes (`navregen.blocking_volumes`)
and its box components that stop walkers (`navregen.collision_boxes`), inside the gathering
region (`navregen.gather_region`). What it got wrong before 2026-11-05:

* **Geometry far outside the region.** A piece reaching into the region was taken whole,
  and Recast's grid spans every vertex it is given. Untrained Grounds' (Scenario_Sand_01)
  landscape reaches 201,600 uu from the origin (a 20,160-cell square grid) and Sand Sea
  Castle's (Sand_02) as far; Recast returned no polygons and exited cleanly, and the build
  stopped on `min() iterable argument is empty`. Triangles wholly outside the region are
  now left out (`recastgen.inside_region`, blocking volumes and boxes too). 23 of the 33
  levels have such triangles.
* **Landscapes.** A landscape is taken by its own settings (`navregen.component_flags`,
  `navregen.landscape_flag`): left out when its `BodyInstance` collides with nothing (Sand
  Sea Castle: NoCollision), left out of the walkable geometry when `bUsedForNavigation` is
  false (Untrained Grounds; `NOT_NAVIGATION`), floor otherwise (Desert Wall, Cliffside,
  Aqueducts, Order Enclave, Frostbitten: OMDBlockAllNoTrap or the default). Measured
  against the shipped graphs (IoU of agent 0): Untrained Grounds 0.16 with its landscape
  as floor, 0.72 as an obstacle (leg 11 and MobSpawnerDoor2's walk cut), 0.75 left out
  (every leg joined); Sand Sea Castle 0.12 as floor, 0.81 / 0.80; Desert Wall 0.41 as
  floor, 0.28 / 0.32; Cliffside 0.76 as floor, 0.49 / 0.49.
* **Box components.** Sandstorm Shelter (Sand_04) and Oasis Outpost (Sand_05) have no kit
  floors: what enemies stand on is `Box_FloorCollision_*` and `Box_FloorRampCollision_*`
  box components on `BP_Scenario_DLC_Sand_0x_TrapGrid`, a level Blueprint with no mesh.
  `omd3level.boxes` reads every box component of a package (its half size and BodyInstance
  from the placement, else the class template, `omd3templates` fields `extent` and `body`,
  else UBoxComponent's 32 uu and UShapeComponent's OverlapAllDynamic), and a box stops
  walkers when its collision profile has query collision and Blocks the Pawn channel
  (`navregen.walker_profiles`, from the engine's and the game's `+Profiles` and
  `+EditProfiles`): BlockAll, OMDBlockAllNoTrap, OMDTrapGridMesh, OMDTrapMesh do;
  OMDCameraBlockingVolume, OMDTrap, OMDTriggerVolume, OverlapAllDynamic and NoCollision do
  not. A box goes with its actor: removed when the plan removes every piece of that actor
  or wires it gone, moved by its first moved piece's move. Door actors' boxes are left out,
  as their meshes are (`navregen.SKIP_ACTOR_PREFIXES`). The 25,292 `OMDTrapGridBoxComponent`
  trap-grid boxes state no profile and are taken as OverlapAllDynamic; their native class
  default is not readable offline.
* **Hidden pieces.** A piece hidden in game (`hidden`: a Blueprint part with bVisible or
  bHiddenInGame off) was always left out, though hiding does not turn collision off. Now
  one stays in when its collision stops walkers (`navregen.hidden_walls`: the component's
  profile from the placement, else the class template, else the mesh's own for a
  component taking its mesh's collision, else UStaticMeshComponent's BlockAllDynamic; a
  mesh with a collision shape). On the 33 levels that is 121 pieces: the
  `Castle_2x2_Short_Tower` collision of the castle tower kits (OMDTrapGridMesh),
  `Cube1` of the grinder pits (the mesh's BlockAll) and Desert Wall's
  `Scenario_Sand_03_Ground` (OMDTrapGridMesh); the lightning spheres and a brazier
  (NoCollision) stay out. It joins Desert Wall's leg 23 and moves agent 0's IoU by 0.01 or
  less elsewhere.
* **Exclusion volumes in sublevels.** `NavPowerExclusionVolume`s were read from the
  persistent level only; `navregen.exclusion_volumes` reads every package (18 more: 5 on
  Front Lawn, 10 on Canyon Keep, 2 on Northern Rampart, 1 on Icebound Mine). No shipped
  polygon has its centre inside any of them.

Rebuilt with an empty plan, then the user's Missions records (read only), against each
level's shipped graph (polygons of agent 0 / agent 1; IoU of agent 0, `navregen.coverage`;
legs: route legs and spawner-to-exit walks the shipped graph joins, after the corridors
`navsurfextend` lays under off-floor routes). Before is the code as it was with the
previous worker's changes of the same day:

| level | polygons before | after | shipped | IoU before | after | legs joined before | after |
|---|---|---|---|---|---|---|---|
| Scenario_Sand_01 | none (no polygons) | 459 / 420 | 636 / 275 | - | 0.74 | no graph | all |
| Scenario_Sand_02 | none (no polygons) | 360 / 349 | 300 / 165 | - | 0.80 | no graph | all |
| Scenario_Sand_03 | none (no polygons) | 2,723 / 2,151 | 2,316 / 954 | - | 0.42 | no graph | 6 legs, 2 spawner walks cut |
| Scenario_Sand_04 | 68 / 73 | 171 / 174 | 208 / 113 | 0.37 | 0.87 | 9 legs, 3 spawner walks cut | all |
| Scenario_Sand_05 | 55 / 51 | 193 / 221 | 454 / 185 | 0.24 | 0.67 | 7 legs, 2 spawner walks cut | all |
| Scenario_01 | 209 / 152 | 199 / 144 | 117 / 65 | 0.67 | 0.67 | 1 leg, 1 spawner walk cut | 1 leg, 1 spawner walk cut |
| Scenario_02 | 200 / 183 | 183 / 152 | 404 / 174 | 0.66 | 0.68 | all | all |
| Scenario_03 | 383 / 326 | 373 / 306 | 521 / 214 | 0.60 | 0.60 | 1 leg, 1 spawner walk cut | 1 leg, 1 spawner walk cut |
| Scenario_04 | 321 / 290 | 324 / 292 | 553 / 249 | 0.67 | 0.67 | all | all |
| Scenario_05 | 526 / 421 | 514 / 401 | 473 / 154 | 0.84 | 0.84 | all | all |
| Scenario_06 | 804 / 675 | 800 / 643 | 1,003 / 401 | 0.63 | 0.63 | all | all |
| Scenario_07 | 416 / 376 | 354 / 316 | 240 / 143 | 0.87 | 0.87 | 1 leg, 2 spawner walks cut | all |
| Scenario_08 | 633 / 574 | 643 / 554 | 564 / 201 | 0.78 | 0.76 | all | all |
| Scenario_09 | 1,362 / 1,333 | 1,354 / 1,317 | 1,028 / 473 | 0.71 | 0.71 | all | all |
| Scenario_10 | 735 / 635 | 667 / 598 | 510 / 271 | 0.83 | 0.82 | all | all |
| Scenario_11 | 544 / 497 | 492 / 407 | 593 / 250 | 0.86 | 0.86 | all | all |
| Scenario_12 | 1,466 / 1,206 | 1,368 / 1,131 | 1,986 / 675 | 0.74 | 0.74 | all | all |
| Scenario_13 | 451 / 429 | 419 / 395 | 459 / 235 | 0.87 | 0.86 | all | all |
| Scenario_14 | 418 / 342 | 411 / 339 | 521 / 248 | 0.56 | 0.56 | all | all |
| Scenario_15 | 1,925 / 1,511 | 1,897 / 1,498 | 2,404 / 1,126 | 0.65 | 0.65 | all | all |
| Scenario_16 | 571 / 520 | 488 / 430 | 755 / 321 | 0.87 | 0.87 | all | all |
| Scenario_17 | 895 / 765 | 726 / 598 | 873 / 455 | 0.71 | 0.71 | all | all |
| Scenario_18 | 1,795 / 1,512 | 1,686 / 1,426 | 2,448 / 1,254 | 0.71 | 0.71 | 5 legs, 2 spawner walks cut | 5 legs, 2 spawner walks cut |
| ScenarioDLC_01 | 204 / 192 | 200 / 184 | 292 / 119 | 0.70 | 0.70 | all | all |
| ScenarioDLC_02 | 215 / 184 | 211 / 165 | 515 / 189 | 0.52 | 0.52 | 4 legs, 2 spawner walks cut | 4 legs, 2 spawner walks cut |
| ScenarioDLC_03 | 1,329 / 1,086 | 1,375 / 1,095 | 2,019 / 666 | 0.62 | 0.69 | 3 legs, 1 spawner walk cut | 3 legs, 1 spawner walk cut |
| ScenarioDLC_04 | 359 / 336 | 338 / 306 | 478 / 155 | 0.75 | 0.75 | 2 legs, 2 spawner walks cut | 2 legs, 2 spawner walks cut |
| ScenarioDLC_05 | 455 / 408 | 377 / 328 | 342 / 176 | 0.82 | 0.82 | all | all |
| Scenario_Ice_01 | 509 / 407 | 388 / 333 | 395 / 194 | 0.80 | 0.81 | all | all |
| Scenario_Ice_02 | 382 / 290 | 379 / 288 | 301 / 130 | 0.26 | 0.26 | all | all |
| Scenario_Ice_03 | 1,567 / 986 | 1,523 / 1,134 | 2,995 / 1,476 | 0.18 | 0.19 | 9 legs, 3 spawner walks cut | 7 legs, 3 spawner walks cut |
| Scenario_Other_02 | 307 / 304 | 305 / 305 | 377 / 130 | 0.89 | 0.89 | all | all |
| Scenario_Other_03 | 149 / 145 | 140 / 132 | 138 / 81 | 0.65 | 0.65 | 1 leg, 1 spawner walk cut | 1 leg, 1 spawner walk cut |
| 900_Arena_P (Scenario_01), a level from scratch: not rebuilt in its build | 481 / 458 | 466 / 455 | 117 / 65 | 0.00 | 0.00 | all | all |
| 900_Beset_Alpha_3_P (Scenario_09) | 395 / 378 | 424 / 356 | 1,028 / 473 | 0.20 | 0.20 | all | all |
| 900_Cellar_Alpha_1_P (Scenario_16) | 591 / 538 | 522 / 466 | 755 / 321 | 0.73 | 0.73 | all | all |
| 900_North_Wings_P (Scenario_01), not rebuilt in its build | 209 / 152 | 199 / 144 | 117 / 65 | 0.67 | 0.67 | 1 leg, 1 spawner walk cut | 1 leg, 1 spawner walk cut |
| 900_Pier_Pressure_Noboss_P (Scenario_06) | 410 / 341 | 414 / 326 | 1,003 / 401 | 0.31 | 0.32 | all | all |
| 900_Pier_Pressure_P (Scenario_06) | 410 / 341 | 414 / 326 | 1,003 / 401 | 0.31 | 0.32 | all | all |
| 900_Raid_P (Scenario_04) | 310 / 306 | 359 / 338 | 553 / 249 | 0.57 | 0.58 | all | all |
| 900_Repaired_Mine_P (Scenario_Ice_02) | 339 / 298 | 333 / 303 | 301 / 130 | 0.24 | 0.24 | all | all |
| 900_The_Hallway_Aligned_P (Scenario_01), a level from scratch: not rebuilt in its build | 475 / 363 | 461 / 360 | 117 / 65 | 0.32 | 0.32 | all | all |
| 900_The_Hallway_P (Scenario_01), a level from scratch: not rebuilt in its build | 441 / 328 | 419 / 320 | 117 / 65 | 0.31 | 0.31 | all | all |
| 900_Fixture_Edit_P (Scenario_01) | 209 / 152 | 199 / 144 | 117 / 65 | 0.67 | 0.67 | 1 leg, 1 spawner walk cut | 1 leg, 1 spawner walk cut |

Far from the shipped graph after this, all as before it: Icebound Mine (Scenario_Ice_02,
0.26): 70% of its shipped floor lies outside the gathering region (to y -20,547 where the
NavMeshBoundsVolume ends at -9,200); against the shipped floor inside it, IoU 0.80.
Frostbitten (Scenario_Ice_03, 0.19): the shipped graph has floor over the water at
z -1,471 west of x -11,000 and over areas east of x 6,000 that no piece Rift Lord reads
covers (its two landscapes span x -4,650..2,790 and -22,525..-13,150); 7 legs and 3
spawner walks the shipped graph joins stay cut. Desert Wall (Scenario_Sand_03, 0.42): its
landscape crosses the hidden `Scenario_Sand_03_Ground` slope and rises 60 uu in 50 uu
along x -12,600 (beyond the 48 uu step), which cuts legs 1, 4, 5, 7, 16 and 20 and two
spawner walks. Records on these levels: Repaired Mine (Icebound Mine) joins every leg it
joined before. Test: `tests/offline/test_sandnav.py`.

### When a rebuild cannot finish

`navregen.install` sorts a failed rebuild in two:

* **The map stops it**: no ground spawner, door or route point stands on floor an enemy
  can walk (`recastgen.graphs_for`, after the polygons no route reaches are dropped). The
  build stops with `navregen.RebuildFailed` (a `buildmessage.Problem`), naming the places
  and what to change: "The walkable floor for Sandstorm Shelter (Scenario_Sand_04) could
  not be rebuilt: Spawner MobSpawnerDoor1 at (-1690, -3850, -200), ... and 3 more stand on
  no floor the ordinary enemies can walk. Your plan is unchanged; move them onto the
  floor, or put floor under them." (`navregen.places` names three, then counts the rest.)
* **Anything else** (Recast finds no floor at all, the navigation builder is missing or
  stops, the rebuilt graph fails `navregen.check` or cannot be written, any other error,
  a level with no NavMeshBoundsVolume): the build goes on with the level's shipped walkable
  graph (`navregen.kept`, then the corridors `navsurfextend` lays under off-floor routes)
  and says so as a console warning and a build note: "The walkable floor for <title>
  (<level>) could not be rebuilt, so enemies walk the floor the level shipped with, plus
  the floor laid under their routes, and floor the plan removes, moves or adds does not
  change where they walk." (with no NavMeshBoundsVolume: "<title> (<level>) has no
  navigation area, so its walkable floor cannot be rebuilt; enemies walk ..."). The
  technical detail goes to the console at debug level.

No message sends the designer elsewhere. Test: `tests/offline/test_sandnav.py`.

## Piece ids

A level piece's `id` is its place in the level's piece list (`omd3level.read`), and plans
store ids (`removed`, `edits`, `look.paints`). Pieces a later reader adds go after the rest
(`late`): further instances of an instanced component, spline meshes, landscapes, extra
sublevels, and the parts of a class whose asset differs from it in case
(`omd3actors.found_by_case_only`, `late_parts`). Sludge Shelves' (Scenario_14) eight
`Wall_RB_MT_v3_1x4_Angle_C` walls (asset `Wall_RB_MT_v3_1x4_angle`) were missing from the
editor because the stored part index (`riftlord/data/omd3-actor-parts.json`) was built with
a case-sensitive lookup; their 40 parts are now read and appended, so every id read before
keeps its piece. Of the 124 Blueprint classes the 33 shipped levels place with no stored
parts, this is the only one with parts (the rest are spawners, rifts, coordinators,
level scripts, lights and grid-only actors), and the stored parts of the other 516 classes
match a fresh read. No Missions record is on Scenario_14. Test: `tests/offline/test_sandnav.py`.

## The level's own lights and effects

The effects and lights layers are off when a level opens; placing an effect (New Object, or
a compound kit with one) turns Effects on. With the lights or effects layer on, the level's light and effect markers are picked by click and box like pieces.
An effect's pick target is an upright cylinder over its drawn cloud (at most 240 uu wide
and 600 uu tall); a marker behind the geometry nearest the click lets the click through. One that belongs to a Blueprint drawn as level pieces (a
torch, a brazier) picks that whole assembly; a light or emitter actor of its own is
moved with the gizmo (`plan.actorMoves`, `{'<sublevel>|<actor export>': [dx, dy, dz]}`,
written by `apply.sublevel_edits` as a move of the actor's root) and removed with Delete
(`plan.removedFx`, the same keys, parked by the build). Effects draw as drifting point
clouds in their group's colour and shape; glow meshes (light beams, rays, glow cones:
`leveldata.GLOW_MESH`) draw additive and soft. The build bakes the light maps when they are
stale (`lightbake.stale`: a sky, liquid or plane look, a Sky Light value, a Static light
changed, moved or removed, or pieces removed, moved or added as meshes or kit pieces, on any
level, one from scratch too). Only the Missions page's builds bake (Build, Install,
Package); an editor's build leaves it pending (docs/SAVE-MODEL.md). The build's console
line `lightbake: <kind of build>: ...` says whether it bakes and why.

A picked light (a PointLight, SpotLight or RectLight actor of the level's own, or an added
light actor) has a **Light** section (see Lighting below) and, for a level light, **Select
All N**, which picks every light still in the level shipped in the same colour and kind, so
one change sets them all. A picked level emitter (an `Emitter` actor)
has an **Effect** list of the surveyed Cascade systems (`fx-donors.json` rows whose donor is
an Emitter): the build points its ParticleSystemComponent's `Template`, and the same entry of
its preload list, at the chosen system (`plan.fxSwaps`, `{key: template}`), and the viewport
draws the cloud as that system's group. One picked level effect also offers **Select All N**
and **Delete All N** (every placement of its system). Test:
`tests/offline/test_levelretint.py`, UI case `retint`.

An effect's particle system is the placement's `Template` (Niagara: `Asset`), else the one
its Blueprint class's component names (`omd3templates` field `particle`): an effect
Blueprint such as `BP_LavaBurst_01_C` states none per placement, and was listed as
`particles` and drawn as fog before 2026-10-28.

The level's ambient sounds (`/api/level` `sounds`: `[{loc, cue, export, actor, actorName,
actorClass, sub}]`, `omd3level.sounds_of`) come from every package the editor reads and from
the `_Audio` sublevel, which is read for its sounds only. One entry per AmbientSound whose
AudioComponent names a Sound; LevelMusic and the MIN_GRP_* sounds (no Sound of their own,
played by the level script) are left out. They draw in the effects layer as the diamond an
added AmbientSound draws as, named by their cue (`AmbEmitter_LavaBubbles_LP_SC` is "Lava
Bubbles"), and are picked, moved and removed like an effect, with the same keys
('Audio|<actor export>'). The build parks a removed sound and silences it
(`geomedits.silence`: every AudioComponent of a parked actor gets `bAutoActivate` false and
no `Sound`), since a sound with no attenuation is heard wherever it stands. When the plan
also chooses a track, the edits are written into the music copy of `_Audio` (`emit.py`).

A liquid surface is not in front of a marker: a click on a light, effect or sound under a
pool picks it (the marker click lets through only markers behind solid geometry).

A level's moon is the Moon section of Map Settings (see Moons, under Lighting): a sky no
longer brings or parks one. It stands far beyond the editor's camera range (Scenario_06's
1,229,626 uu out, 891,226 uu up), so the view draws it on the sky around the camera.

## Lighting

A map's lighting is the designer's to set, the same on an edited shipped level and a level
from scratch (`riftlord/build/world/lighting.py`, `web/src/lightingui.js`). Map Settings
shows it with the engine's names, at the level's real values (a level from scratch: its
light rig's), each field with Reset once the plan stores it; a value set back to the
shipped one stores nothing. A section the level has no component for is not shown.

The sections sit in one collapsible **Lighting** section of Map Settings (`#lightingbox`,
`mapsettings.js`): Sun, Moon, Sky Light, Atmospheric Fog, Height Fog, Post Process. It is
closed when the editor starts and stays as the designer left it while Map Settings redraws.

| section | fields | plan data |
|---|---|---|
| Sun (one per DirectionalLight; Add Sun, Delete) | Light Color, Intensity, Pitch, Yaw (its RelativeRotation), Cast Shadows | `lights['<sublevel>\|<actor export>']`; an added sun is an `added` kit item `DirectionalLight` with its `light`; a deleted level sun goes into `removedFx` and the build turns it off (`bVisible` false) |
| Sky Light | Light Color, Lower Hemisphere Color, Intensity, Indirect Lighting Intensity, Lower Hemisphere Is Black | `lighting.skyLight` |
| Atmospheric Fog (where the level has one) | Default Light Color, Default Brightness, Sun Multiplier, Fog Multiplier, Density Multiplier, Density Offset | `lighting.atmosphere` |
| Height Fog | Fog Inscattering Color, Directional Inscattering Color, Fog Density, Fog Height Falloff, Fog Max Opacity, Start Distance, Fog Height (the components' Z) | `lighting.heightFog` |
| Post Process (the unbound volumes' Settings) | Exposure Compensation, Bloom Intensity, Saturation, Contrast, Gamma, Gain (R, G, B, Y) | `lighting.postProcess`; a value turns its `bOverride_` on |
| Moon (one per moon; Add Moon, Delete; after Sun) | Pitch, Yaw, Distance (its RelativeLocation's direction and length from the level's origin), Scale (RelativeScale3D), Material | `moons['<sublevel>\|<actor export>']`; an added moon is an entry of `addedMoons`; a deleted level moon goes into `removedFx` and the build parks it (see Moons) |
| Light (a picked light) | Light Color, Intensity, Attenuation Radius, Inner and Outer Cone Angle (spot), Mobility, Cast Shadows | `lights[key]`, or an added actor's `light` |

Values are engine property names: numbers, bools, `'#rrggbb'` for an FColor (sRGB),
`[r, g, b, a]` for a LinearColor, `[x, y, z, w]` for a Vector4, `[pitch, yaw, roll]` for a
Rotator, enum names; `null` removes the property (the class default). Every component of
a section's class in the level takes the section's values; every light component of an
actor takes its `lights` values. Mobility offers Static (baked) and Movable (drawn every
frame); a level light shipped Movable or Stationary has no light build data, so it may only
become Movable. Selecting a sun's name shows its **direction handle** in the viewport: a
ball on a sphere around where the camera looks with an arrow the way the sun shines;
dragging the ball turns the sun (`web/src/sunhandle.js`).

**A sky is the sky dome's material only** (since 2026-11-05). Before, choosing a sky
brought its home level's fog colours, suns, sky light, atmosphere and colour grade
(`skyfog.world_with_sky`, `skymirror`), and Map Settings showed the level's own fog while
the build wrote the home's. The home's moon came with its sky (`skyfog.lift_extras`) and
the level's own was parked (`skyfog.own_extras`) until the same day; moons are now their
own plan data (Moons, below).

**Older plans** (no `lighting`) are migrated on load, on save, in the Missions page's
editor view and in the build (`riftlord/build/world/lightmigrate.py`): the old writers run
over the level's packages in memory and every property they changed becomes an explicit
value (`lighting` sections and sun `lights`); the old `world` keys fogDensity, fogFalloff,
fogOpacity, fogStart, fogColour, fogSunColour, bloom, exposure and `lightColours` go.
A value no field shows (an older plan's film curve, say) is listed under its section by
its engine name, with Reset. `python3 -m riftlord.build.world.lightmigrate RECORD.json`
prints what a record migrates to and checks that the values write the old lighting. Every
plan saved since stores `lighting` (empty when nothing is set).

**The view** previews the suns (the brightest with shadows, up to five more without), the
sky light as the ambient (its colour, lower hemisphere and intensity), the height fog
(colour, density, falloff, opacity, start, height) and each light's colour, intensity and
radius as you edit them.

**The bake** takes the plan's Sky Light values (`bake_spec` `sky_light_edit`) and its lights'
values, moves and removals by actor key (`light_edits`, `light_moves`, `light_removed`,
over the liquid lights' colours) into `lightscene.sky_light` and `lightscene.lights`; a light
made Movable leaves the bake. Suns, fog and post process are drawn every frame and never
baked, so they do not make the light maps stale. Test: `tests/offline/test_lighting.py`,
UI case `lighting`.

### Moons

A moon is a `StaticMeshActor` whose root `StaticMeshComponent` draws
`/Engine/BasicShapes/Plane` (100 uu square) with a moon material, far out in the sky with
its face toward the level (`riftlord/build/world/moons.py`, `web/mapeditor/src/moonsui.js`).
Measured on every shipped level (2026-11-05): 11 have one, each a single component with
`StaticMesh`, `OverrideMaterials` (one material), `VisibilityId` and the transform; no
`Mobility` stored (Static, the class default); `bNeverDistanceCull` on Scenario_06,
Scenario_Ice_01 and Scenario_Sand_05; `ForcedLodModel` 1 on Scenario_08, Scenario_Ice_01
and Scenario_Sand_05. "Face off" is the angle between the plane's face (its Z axis) and
the way to the level's origin; "across" is the plane's width seen from the origin.

| level | key | material | pitch | yaw | distance (uu) | scale | across | face off |
|---|---|---|---|---|---|---|---|---|
| Scenario_03 | Lighting\|782 | Moon_01_Inst | 40.61 | -116.29 | 1,499,463 | 5000 | 18.93 deg | 12.65 deg |
| Scenario_06 | Lighting\|1179 | Moon_Lava_01_Inst | 46.45 | 95.44 | 1,229,626 | 5000 | 22.98 deg | 8.65 deg |
| Scenario_07 | persistent\|431 | Moon_01_Inst | 83.50 | 176.89 | 982,323 | 5000 | 28.56 deg | 6.50 deg |
| Scenario_08 | Lighting\|684 | Moon_01_Inst | 79.56 | 76.30 | 939,539 | 6000 | 35.42 deg | 10.44 deg |
| Scenario_12 | Lighting\|2547 | Moon_Lava_01_Inst | 46.45 | 95.44 | 1,229,626 | 5000 | 22.98 deg | 8.65 deg |
| Scenario_14 | Lighting\|685 | Moon_01_Inst | 83.86 | 139.83 | 981,627 | 5000 | 28.58 deg | 6.14 deg |
| Scenario_16 | Environment\|191 | Moon_01_Inst | 83.50 | 176.89 | 982,323 | 5000 | 28.56 deg | 6.50 deg |
| Scenario_Ice_01 | Environment\|694 | Moon_Snow_01_Inst | 16.54 | 95.49 | 911,634 | 4000 | 24.75 deg | 17.40 deg |
| Scenario_Sand_05 | Lighting\|869 | Moon_Snow_01_Inst | 19.20 | 71.01 | 306,084 | 1500 | 27.54 deg | 26.03 deg |
| Scenario_Other_02 | Environment\|181 | Moon_01_Inst | 69.11 | -107.36 | 1,044,643 | 5000 | 26.92 deg | 7.76 deg |
| Scenario_Other_03 | Lighting\|660 | Moon_Lava_01_Inst | 41.09 | 18.45 | 1,930,005 | 5000 | 14.76 deg | 2.34 deg |

The moon materials are four `MaterialInstanceConstant`s of `Master_SkyObjects` in
`/Game/Environments/Sky/Mat`, all over the texture `Sky_Moon2_df` (a full disc with a glow),
differing in `EM_Color` (linear) and `EM_Brightness`: Moon_01_Inst (0.048, 0.352, 0.515) 15,
Moon_Lava_01_Inst (0.133, 0.366, 0.791) 15, Moon_Snow_01_Inst (0.063, 0.297, 0.250) 25,
Moon_MainUI_Inst (0.045, 0.132, 0.791) 15 (placed by no level). The sky dome around them
is `SM_Skybox_Mesh` (extent 4,096 uu) at scale 5,000 on 10 of the 11, a 20,480,000 uu
radius, which is Distance's limit; Scenario_Ice_01's is `SM_SkySphere` at scale 400.

**Plan data.** `moons` {'<sublevel>|<actor export>': {Property: value}} holds what the
plan changes on the level's own moons; `addedMoons` [{Property: value}] the moons it adds;
properties `RelativeLocation`, `RelativeRotation`, `RelativeScale3D` and `Material`. An
added moon is lifted from Scenario_12's `Plane_Moon2` (`moons.DONOR`, the moon a sky
brought until 2026-11-05) as `RiftLordSky_Plane_Moon2` (then `...A`, `...B`), made Movable
(a lifted mesh keeps its donor's MapBuildDataId) and given `VisibilityId` -1; a property
its entry does not set is the donor's, so Add Moon starts as that moon. A level moon the
plan moves gets `VisibilityId` -1 and keeps its mobility. Every plan saved since stores
`moons` (empty when nothing is set). The same on an edited shipped level and a level from
scratch: a level from scratch has no moon of its own (its light rig has none; a host's
persistent moon is parked with its decoration) and takes added ones in its persistent
level.

**The fields.** Pitch and Yaw are the direction of `RelativeLocation` from the level's
origin (`FVector::Rotation`), Distance its length, Scale `RelativeScale3D` on every axis
(the shipped value shows X). A Pitch, Yaw or Distance change also stores the
`RelativeRotation` that keeps the face the moon showed the level: its turn carried by the
least rotation from its old direction to the new one (`moons.carried_rotation`; the build
does the same for a plan that sets a location alone). Pitch, Yaw and Distance set back to
what the shipped moon shows store nothing. Selecting a moon's name shows its direction
handle, the sun's in pale blue with its ball the way the moon is; dragging it moves the
moon round the sky at its distance. The view draws each moon on the sky around the camera
(280,000 uu out, inside the view's 300,000 uu sky dome) at the width it spans from the
level, its texture tinted by `EM_Color` times `EM_Brightness`, added.

**Older plans** (no `moons`) are migrated with the lighting (`lighting.migrated`, so on
load, on save, in the Missions page's editor view and in the build): when the plan's sky
came from another home, that home's moons become `addedMoons` entries (the values that
differ from the donor's) and, on an edited shipped level whose own sky is another, the
level's own moons go into `removedFx`. The user's records on 2026-11-05: Beset Alpha 3
(Scenario_09 under Night_Sky_Lava_INST1, home Scenario_12) gets one added moon, `{}`;
Cellar Alpha 1 (Scenario_16, Dawn) gets `Environment|191` in `removedFx`; Pier Pressure and
Pier Pressure Noboss (Scenario_06, Stormy) get `Lighting|1179`; the others have no moon to
bring or park. Their persistent levels and sublevels (apply.apply_plan, apply_to_sublevel)
built byte for byte the same as the old code's, checked 2026-11-05. Test: `tests/offline/test_moons.py`, UI
case `moons`.

## Placeable actors

New Object's **actors** filter lists shipped actors beyond the kit
(`leveldata.ACTOR_GROUPS`): lights (PointLight, SpotLight, RectLight, DirectionalLight), torches, braziers
and chandeliers, banners, AmbientSound, the environmental traps (cauldron, falling boom
barrel, grinder pits) and the rain-level lightning.
They are plan `added` items of kind `kit`, lifted whole from a level that places them
(`apply._add`), drawn as their Blueprint's meshes, or as a marker when they have none.
Every light in them except the SpotLight (Stationary) is Static as shipped, so it lights the
level only through a bake.

### Light maps for added pieces

A build that bakes gives every added mesh and kit piece light maps of its own
(`riftlord/build/lighting/lightalloc.py`), on a shipped level and a level from scratch
alike: each static mesh part (`lightalloc.receivers`: an added mesh's root, a kit piece's
parts by component name) keeps Static mobility and its LODData names a MapBuildDataId of
its own; the bake adds atlas pairs to the persistent level's BuiltData, copied blank from
the level's own first pair (Scenario_01: 1024), one LightMapResolution square per part
(`lightalloc.pack`), and bakes each part there as a receiver and an occluder. A part whose
lifted component states another mobility, or whose LODData carries painted vertex colours,
keeps what it was lifted with. The Static light components of added actors (PointLight,
RectLight, torches, braziers, chandeliers) get LightGuids of their own and a LightBuildData
entry, and light the bake where the build places them (`lightalloc.added_lights`). Fab pieces
stay Movable with no light map. Without a bake (an editor's build) added meshes are Movable
with no LODData, as before. A level from scratch bakes its persistent level only: its host's
geometry sublevels are not streamed, its host pieces are parked, and its light rig's sky is
Movable, so no host light, piece, lava or sky goes into the bake
(`scratch.streamed_scene`). Test: `tests/offline/test_lightalloc.py`.

### Controller settings

`riftlord/controllers.py` and `riftlord/envtraps.py` (research:
`workshop/research/LEVEL-CONTROLLERS-AND-PATTERNS.md` section 1). Choices and donors come
from `controller-donors.json` (`python3 -m riftlord.controllers --survey`); `GET
/api/controllers` serves them; `web/src/controllers.js` draws the panel sections.

| setting | plan data | build |
|---|---|---|
| Wind (Map Settings): Speed, Strength, Min/Max Gust Amount | `world.windSpeed`, `windStrength`, `windMinGust`, `windMaxGust` | `levelfx` SCENE_FIELDS; written into the `WindDirectionalSourceComponent`, added at the class default where a level stores the component but not the setting |
| Kill volume: Kills enemies, Kills heroes | `volumes[]` `killMinions`, `killPlayers` | `volumes.py`: false stored, true removed (as shipped) |
| Wall Plate on the level's own wall torch | `wallPlates` {'<sublevel>\|<export>': bool} | off: both plate properties null and the components' meshes null; on: the plate components copied from a plated shipped placement |
| Wall Plate on an added torch | `added[].wallPlate` | lifted from a plated or bare shipped placement |
| Sound on an added AmbientSound | `added[].cue` | lifted from a shipped placement of that cue, set to play from the start |
| Retrigger Time, Glow Color on added lightning | `added[].retrigger` [min, max], `glow` [r, g, b, a] | written on the bolt; its scale is relative to the shipped bolt's |
| Pour Length on an added cauldron | `added[].pour` | lifted from the shipped placement with that template's median drop (template, trigger box and floor splash together) |

New Object's **Environmental Traps** group holds the cauldron, falling boom barrel and
grinder pits; **Weather** holds the rain-level lightning. A falling boom barrel hangs by
its actor origin (`envtraps.HANG_ANCHOR`); `controllers.js` `barrelUnder(point)` is the
entry that hangs one from a chain's lower end. `python3 -m riftlord.controllers --check
DIR` builds every setting into a copy of Scenario_03 offline and runs the gates
`riftlord.probe` runs (`controllers.gates`).

### Treasure chests and pickups

`riftlord/kitactors.py`: classes no shipped level places.

| item | New Object group | plan data | build |
|---|---|---|---|
| Treasure Chest (`BP_TreasureChest_C`, Mods/TreasureChests) | Environmental Traps | `added[].chest` {group, chance, coin [min, max], coinChance, skulls [min, max], skullChance} | the catalogue's cauldron placement retargeted to the chest: the cauldron's own components left out, the interactable base's kept, native templates naming the base's default object; the Blueprint variables (Group, Chance, CoinMin/Max, CoinChance, SkullMin/Max, SkullChance) written from the group's first spot |
| Stamina Potion (`/Game/Mods/OrcsMustUI/BP_Pickup_StaminaPotion_C`, inside OrcsMustUI) | Pickups | none | written from the class (`_place_pickup`), `Expires` false; read from the installed LogicMods/OrcsMustUI.pak, never copied; removes itself while OrcsMustUI's Enable Stamina is off |
| Coin, Large Coin, Molten Gold Coin, Large Molten Gold Coin, Skull, Health Potion, Mana Potion (`/Game/Pickups/BP`) | Pickups | none | written from the class; ProjectileMovement `Velocity` 0; the potions `DestroyTimer` 0 |

In the editor (`kitactors.view_parts`, `/api/kit/<class>`) the chest and the stamina potion
draw their meshes; a shipped pickup draws its particle system (`pickup_visual`): the
potions' mesh emitter (`/Game/FX/Meshes/Jar` at its StartSize) in their PointLight's colour,
the coins and the skull as a camera-facing sprite of their material's texture
(`/api/pickup/sprite`), StartSize wide, tinted ColorOverLife x EmissiveColor. Builds,
minimaps and outlines use `kitactors.parts`, which has no particles. Every New Object card
has a preview (`/api/thumb/kit:<class>`, `kitactors.thumbnail`).

Spots sharing a group are one chest: the panel writes every edit to all of them and Add
Spot places another. Nothing of either mod is copied into a mission: the chest's class is
read from the installed `LogicMods/TreasureChests.pak` and the potion's from
`LogicMods/OrcsMustUI.pak` (`kitactors.INSTALLED_KITS`, found through Supported Mods), and
a mission placing one requires that mod. Missions built before 2026-10-24 carry a copy of
the chest kit: Rebuild them. Kit meshes are served as
`/api/mesh/kit/<Kit>/<path>`. `python3 -m riftlord.kitactors --check DIR` builds every
class into a copy of Scenario_02 offline and runs `kitactors.gates`.

### Supported Mods

`riftlord/core/supportedmods.py` declares the partner mods whose pieces Rift Lord places,
and detects each by a file at a fixed place under `OMD/Content/Paks` (no hash):

| mod | file | feature | classes (package in the pak's index) |
|---|---|---|---|
| OrcsMustUI | `LogicMods/OrcsMustUI.pak` | Stamina Potion | `/Game/Mods/OrcsMustUI/BP_Pickup_StaminaPotion.BP_Pickup_StaminaPotion_C` |
| Crystal Defense | `~mods/CrystalDefense_P.pak` | Crystal | `/Game/Mods/CrystalDefense/BP_Crystal.BP_Crystal_C` |
| Treasure Chests | `LogicMods/TreasureChests.pak` | Treasure Chest | `/Game/Mods/TreasureChests/BP_TreasureChest.BP_TreasureChest_C` |

A file present whose index lacks a feature's class package, or whose class package header
does not parse or export the class, is "Installed (incompatible version)" with the reason
under Missing (logged once), and that feature stays off; nothing that reads the kit
(catalogue, parts, previews, the Crystal's defaults) opens such a package
(`kitactors.kit_problem`). Readings are cached by size and modification time;
detection runs at app start, on every `GET /api/supportedmods` (Settings' Refresh, New
Object opening) and on every build. New Object shows a gated card (catalogue rows carry
`feature`; the Crystal gameplay card) only while its feature is on. A plan that already
holds such a piece keeps it, drawn as its stand-in, and its build is refused with one line
naming the mod (`supportedmods.build_problems`, in `bucket.bake` and the standalone build).
`probe.import_problems` resolves imports against the detected paks. `RIFTLORD_SUPPORTED_PAKS`
points detection at another folder (tests: `tests/ui_smoke/fakemods.py` writes stand-in
paks). Offline: `python3 tests/offline/test_supportedmods.py`; UI smoke case
`supportedmods`.

### Game modes in the New Level dialog

`GET /api/scratch/modes` (`scratch.game_modes`) lists the game modes of
`riftlord/onslaught/gamemode.py` MODES whose piece's supported mod is detected now: Rift
Defense always (first, the default), Crystal Defense while Crystal Defense is detected.
The New Level dialog shows a Game Mode dropdown only with more than one. The new level opens
with its rift, exit and player starts as before, and the chosen mode's piece placed as its
New Object card places it (Crystal Defense: the Crystal on the rift, `main.js MODE_PIECES`).

## More than one rift

No shipped level has two rifts, but the game's code handles any number
(docs/research/MULTI-RIFT.md): an enemy enters whichever rift its route ends at, every leak
comes off the one rift point pool, and each rift has its own map marker and warning box.

New Object's gameplay card **Rift** places a rift (`plan.rifts`) with its own **exit point**
(`exit`, an added point) `planactors.RIFT_OVER_EXIT` (258.2 uu) under it, dropped onto the
walkable floor like a ground point; it is drawn as an exit point. Moving the rift moves its
exit point; deleting it deletes the exit point, and branches into it end at the level's rift
again. The rift's panel has **Exit Point**: another added point with no branches. An exit
point's panel names its rift in place of Goes Next To. A plan from before this, whose rift
named no exit point, takes the added point with no branches inside the rift
(`rift_exits`, written into `exit` when the plan opens).

A route ends at a rift by a branch to it: Goes Next To lists **Rifts** (the level's rift as
**Original Rift**, which `"rift"` and an empty branch list still mean, and each placed rift
as **Rift N**). A spawner's panel shows **Ends At**, the rifts its routes can end at (red
"no rift" when none), and the Routes list in a rift's panel says the same per route.

The build writes each placed rift's exit point with `bIsExitPoint`. The save refuses a rift
with no exit point, an exit that is not an added point and two rifts sharing one, and warns
about an exit outside the rift's `RiftEnterArea` (`planactors.check`). The build's gates
(`emit.path_checks`): a rift in play, as many exit points as rifts in play, and with two or
more, each exit point inside a different rift. The walkable graph checks each spawner's walk
to every exit its route can reach (`navregen.route_legs`); flyer routes end at whichever exit
(`planactors.flyer_routes`); the minimap marks every rift (`mapdataemit.markers_for`) and the
rift warning size goes on every rift (`riftwarning`). A Crystal is placed on the selected rift
(else the level's). Offline checks: `python3 tests/offline/test_multirift.py`; UI smoke case
`multirift`.

## Ground spawner and path point height

A plan ground spawner (not an unstable rift) is put `navsurfextend.SPAWNER_ABOVE_FLOOR`
(99 uu, the shipped median) over the walkable floor under it when it is placed, when its
kind is set to ground, when a move ends and when a Position field is typed (not with shift
held): `POST /api/riftfloor/<level>` with `{at, for: 'spawner', plan}` (`riftfloor.snap_spawner`,
the unstable rift's floor lookup, reaching `SPAWNER_REACH` 1000 uu down). A plan ground path point (not flyers only, no spawner's flyer start, no flyers-only
point branching to it) is put `navsurfextend.POINT_ABOVE_FLOOR` (10.8 uu, the shipped
ground points' median over graph 0) over the floor polygon `navsurfextend.on_surface` puts
it on, at the same moments: `for: 'point'` (`riftfloor.snap_point`). That is the floor a
build treats it as standing on (-100 to +263 uu of the plane; shipped ground points stand
-49 to 196 uu over theirs, one at 866), so a point higher over a gap is left where it is
and the build adds floor under it as before. Saved heights are not changed when a mission
opens.

All three snaps (ground spawner, ground path point, unstable rift) look on the floor the
open plan leaves, graph 0 of `riftfloor.plan_floor`: on a **scratch level**, the plan's own
floor and nothing of the host's -- its walkable kit, Fab and terrain pieces and grinder pits
as the build's graph has them (`scratch.floor_quads`, the quads `scratch.cover` starts from,
before walls take cells away: a wall that costs a cell its graph polygon does not change the
floor's height); on a **shipped level**, the level's graph less the polygons standing on
floor the plan removes or moves away (`navsurfcarve.doomed`), plus the same plan floor and
the floor-class meshes (`navsurfcarve.is_floor`) the plan adds or moves. A
plan that changes no floor uses the level's graph itself, as before.

A flat floor piece (kit, Fab or mesh) is floor at the height a walker stands on it, not at its
box top (`navregen.walk_top`, `scratch.walk_box`): the highest upward collision face (convex
hulls, else render triangles) over each `navregen.CELL` (20 uu) column, then the
`CELL_HEIGHT` (5 uu) span holding the most columns, and its top. Crenellations, trim and rails raise a box: Name TBD's
`BP_Scenario_06_CornerFloor` has its box top 117 uu over its slabs (z 267 against 153). Kept
per piece and turn (`navregen.item_walk_top`).

A floor kit piece with stair parts (`recastgen.is_ramp`: the rift platforms, Scenario_06's
bridge) is its flat top (the XY box of its upward faces within `CELL_HEIGHT` of the walk height)
plus one slope per stair flight rising along one axis (the box of the flight's upward faces;
its rise read from their plane), so the cover joins it to the floor through its stairs
(`scratch._walk_parts`; corner flights, leaning off one axis by more than `RAMP_AXIS_LEAN`, are
left to the floor around them). Before 2026-11-05 such a piece was one flat box at its top:
Proving Grounds' rift platform, 90 uu over the floor, joined it only where
`navsurfextend.bridge` happened to find facing edges, and moving the map by (72, -66) cut it off.
The cover's lattice lines are every floor edge plus `GRID_CELL` steps from the floors' own phase
(`scratch._phase`: the edge the most floor edges share an offset on the 300 uu lattice with), and
a turned floor's cells run from its own origin, so moving a whole map moves its cover with it
and joins nothing new and cuts nothing (`tests/offline/test_scratch_translate.py`).

On a shipped level the regenerated walkable graph takes added kit pieces as geometry, each
static mesh at its part transform and then the piece's, as the level's own Blueprint actors
(`navregen.added_meshes`); before 2026-10-30 it left them out, so added kit floor was walkable
only along the corridor `navsurfextend` lays under a route. Kit actors Rift Lord places with
their own build (chests, pickups, the chandelier) and doors add no geometry. A plan that adds
only kit or Fab pieces now regenerates the graph (`navregen.wanted`). Offline:
`tests/offline/test_floorbuild.py`. Fab pieces whose
library is not installed are left out, so the rest of the floor still snaps. The floor is kept per
plan floor (the plan's `scratch`, `added`, `removed` and `edits`; the last
`PLAN_FLOORS_KEPT` 4), so only the first snap after a piece edit builds it: over HTTP on a
476-piece scratch level, 90 to 126 ms (median 95), then 4 to 5 ms. The plain `GET /api/riftfloor/<level>?at=x,y,z&for=`
still answers on the level's own graph. Offline: `tests/offline/test_riftfloor.py` (section
10); UI smoke case `floorsnap`.

A build warns in the console (not a failure) for each added ground spawner standing
more than `riftfloor.SPAWNER_DROP_MAX` (206.6 uu, the shipped maximum) over graph 0 of the
written level's walkable graph (`riftfloor.spawner_warnings`, in `bucket.bake`), named as
the editor names it ("Spawner 30 stands 240 units above the floor, higher than any shipped
ground spawner (207 units). Lower it toward the floor, or test it in game.").

## Unstable rift height

A new Unstable Rift is put `riftfloor.SHIPPED_OFFSET` (90.7 uu) over the walkable floor
under it; a move or a typed position leaves it where it is put. A build
(`riftfloor.build_report`, in `bucket.bake`; probe gate `riftfloor.gates`) refuses an
unstable rift with no walkable floor under it ("Unstable Rift 7 at (x, y, z) has no floor
enemies can walk under it. Move it over floor, or put a floor piece under it.", "... has no
floor under it that large enemies can walk on ..." when only graph 1 lacks it, "... is 40
units under the floor enemies walk on. Raise it above the floor." when sunk into it) or with
a solid level piece between it and that floor ("... Move the piece or the rift, or turn the
piece's Collides off."). One higher over its floor than `DROP_LIMITS` (116 / 132 uu, the
shipped highest `SHIPPED_DROP_MAX` 102.8 / 118.3 plus 13) builds with a console warning:
"Unstable Rift 7 stands 163 units above the floor, higher than any shipped rift (103 units).
Its enemies should still land; test it in game, or lower it.". BP_Crystal spawns a rift's units at the rift's own location
(SpawnEntityForEntity, AdjustIfPossibleButAlwaysSpawn), so no height refuses a spawn. The
probe gate's third row, `unstable rift heights over their floor (a note)`, always passes
and carries the warnings.

## Checks

Under Save Mission, **Checks** lists what the open plan needs before it builds
(`POST /api/checks/<level>` with `{plan, mission}`, `riftlord/mapeditor/checks.py`;
`web/mapeditor/src/checks.js`). It is asked again 800 ms after each change (`snapshot`),
on undo, on opening a level and after a save, and hidden when empty. Rows, in order:

- **error**: `planactors.check` errors, the Crystal (`crystal.problems`), unstable rift
  events (`missionevents.rift_problems`), supported mods not detected;
- **build**: what the last build of the open mission stopped on (the record's
  `buildChecks`, else its `bakeError`);
- **warning**: `planactors.check` warnings and a spawner turned by hand more than
  `spawnerfacing.WARN_OFF` degrees from its route;
- **fixed**: what the build will fix itself, such as an added spawner's taken index.

Each row names the piece, route point or spawner as the editor shows it. Clicking a row
selects that thing (a plan marker, the level's own marker, or an added piece) and frames
it. `checks.locate` finds the target from the text: "Path Point N", "Spawner N", "Unstable
Rift N" (by index in level), "Rift N", "Door N", or a level actor's name. The save notice
no longer lists the plan's warnings; Checks does.

## Spawner facing

A plan spawner (ground, flyer or unstable rift) faces its route's first point, yaw only:
its `start` (a flyer spawner: its `flyerStart` first), or the point after that one when
the first point is within its reach radius horizontally (`DEFAULT_REACH`, 300 uu, when the
point has none). The Map Editor sets it on every redraw of the plan, so placing the
spawner, setting or moving its first point and moving the spawner all re-aim it
(`actors.js spawnerFacing`, `main.js aimSpawners`). A spawner with no route keeps its yaw.
A turn by hand (the gizmo's turn, or the yaw field) sets `turned` in the plan, and that
spawner keeps its yaw from then on (undo takes it back). Spawner markers carry a cone
along their facing (`SPAWNER_NOSE`), the level's own spawners too.

The build writes an added spawner without `turned` facing its first point
(`spawnerfacing.facing` in `planactors.emit`, on the plan's final path graph, so a moved
level point counts), so rebuilding an older mission re-aims its spawners; the report counts
them as `spawners_faced`. The level's own spawners keep their rotation. A build warns in
the console (not a failure) for each added spawner facing more than
`spawnerfacing.WARN_OFF` (100 degrees, where the game starts moving a unit backwards at
0.7x) from its first leg, naming it (`spawnerfacing.warnings`, in `bucket.bake`, read back
from the written level). `python3 -m riftlord.mapeditor.spawnerfacing <pak or folder>`
prints each added spawner's yaw against its first leg.

Measured on the 33 shipped levels' 98 spawners, yaw against the direction to the start
point: flyer spawners (16) median 1 degree, at most 57; Scenario_18's red rifts (11) median
8.5, at most 89.7; ground spawners (71) median 19.7, 8 over 100 (each 1000 to 3000 uu
behind its door). One has a pitch (Scenario_06 `BP_OMDAISpawner_2`, 4 degrees), none a
roll, and no placed spawner turns its `SpawnDirection` arrow. Offline checks:
`python3 tests/offline/test_spawnerfacing.py`; UI smoke case `spawnerfacing`.

## The level picker

`/api/levels` files each level under a campaign from the game's own tables
(`riftlord/mapeditor/levelgroups.py`): the campaign whose MissionList names a mission
(MissionProtos `MissionLevel`, matched by Guid) that plays on it, by its CampaignName. The
shipped story campaigns come first in the loaded CampaignProtos' order (a mod that lists its
mission in one patches that table), then the custom campaigns installed in ~mods, then the
loaded table's other campaigns (Endless, Weekly Challenges); a level no campaign lists is
under Custom Maps, last. Missions are the loaded MissionProtos' rows and each ~mods pak's
own `_MissionProtos` tables (also the source of a mod level's title). Within a campaign the
levels keep its list's order. The user's Missions records stay first, as Custom Missions.
`python3 -m riftlord.mapeditor.levelgroups` prints the filing.

## New Object shelves

A mesh's shelf is `meshcat.category_of(path, name)`, worked out when the catalogue is
served. Assets under a `/Weapons/` or `/Trinkets/` folder (the player's arsenal, in
`/Game/` or `/Game/Proto/`) are `trap`, or `effect` under an `/FX/` folder, before any name
rule. The `effects` filter lists effect meshes with the placeable effects. A filter with
nothing under it is hidden, in New Object and in the Fab browser.

### Card previews are textured

`riftlord/mapeditor/omd3thumb.py` draws every mesh, kit, compound, chest, pickup and
Crystal card on the CPU: a three-quarter orthographic view, each pixel coloured from its
section's base-colour texture at UV0 (wrapped, nearest texel) times its material's tint,
shaded by its face's angle to the light. Materials resolve as the viewport resolves them
(`omd3thumb.surfaces`): a part's override material paints its whole mesh; a Fab or kit mesh,
or a mesh of several sections with a slot texture, colours each section by its slot; else
the mesh's own texture. An `_EM` texture is unshaded. A section with no texture takes its
material's tint as its colour, else the old flat tan. In the viewport an added kit piece's
parts wear their override materials the same way: `/api/kit/<class>` gives each part with a
`material` its `materialTexture` and `materialTint` (main.js `partMesh`).

- **Mips.** Each surface reads the smallest mip whose edge gives a texel per drawn pixel (from
  its triangles' screen and UV areas), 16 to 512 texels, inline mips before the `.ubulk`
  (`texture.parse(want=)`); decoded mips are kept in memory (128).
- **Models.** `omd3thumb.place(mesh, matrix, material=)` and `merge` build a textured model
  from many meshes: `kitdonors.model`, `compounds.model`, `crystal.preview_model` and
  kitactors' parts use them, so every member keeps its own materials.
- **Cache.** `omd3thumb.STYLE` is in every preview's cache name and in the Map Editor's preview
  URLs (`main.js THUMB_STYLE`, `?v=`; the browser keeps a preview a day): change both together
  when the drawing changes, and every preview is drawn again.
- **Review sheets.** `riftlord/mapeditor/compoundreview.py`: `pair(d, idx, cat)` draws a
  compound's textured default beside its textured shipped spot (its own pieces seen through
  what hides them, the rest blended toward blue-grey), from the front and from behind;
  `python3 -m riftlord.mapeditor.compoundreview OUT [KEY ...]` writes contact sheets.
- **Fab pieces** keep the thumbnails the Fab converter makes (`fablibrary.thumbnail`).

`tests/offline/test_textured_previews.py` (gate step "textured previews") checks it.

## Snapping a move

**Pieces with a trap grid** (kit pieces, Fab pieces with suggested grids) snap by their
grid, on every map, when placed and when moved with Snap to Grid on (`web/mapeditor/src/
kitplace.js` `latticeDelta`; main.js `landing`, `latticeBegin`/`latticeMove`). The rules
are the shipped levels', as the kit placement census measured them
(`docs/research/kit-placement-census.md`). Snapping only ever moves the piece being placed
or moved: it never changes a piece already in a plan or the level, and nothing a build
reads or checks depends on it.

| piece | x, y | z |
|---|---|---|
| floor | grid start on the lattice | grid plane on a 50 uu step |
| ceiling | grid start on the lattice | grid plane 20 over a 50 uu step (50 over a wall grid's top) |
| wall, column | grid start along it on the lattice; its line across it on the lattice | grid bottom 20 over a 50 uu step |

Each wall grid's rules come from its own class's meshes (`/api/kit` grid `fit`, measured by
`riftlord/mapeditor/kitfit.py` on the real vertices, as the census measures them):

- **Skirted** (meshes reaching 20 uu or more below the grid's bottom: `_MT`, `_Trim`,
  `_WDMT`, `_WD`, `_MetalTrim`): stands 20 over the floor under it; its line is its foot
  (the actor origin); the floor in front stops at the origin when the skirting reaches it
  (within 0.5 uu), else at the skirting's reach rounded down to 2.5 uu (37.5 in front of
  the face for `Wall_RB_WDMTL_v1_1x2`, 40 for `Wall_RB_WDMT_v1_2x2` and `_2x3`, 42.5 for
  `_2x4`).
- **Plain** (nothing that far below: `Wall_RB`, `Wall_FB`, `_Dry`, columns): stands on the
  floor with its grid's bottom; its line is its face (the grid's plane), and the floor in
  front stops on the face.
- **Grid only** (the actor origin behind the face: towers, archways, `Castle_*`): its face
  on the lattice; no floor joins.

The lattice is the room's under or around the piece, else the 150 uu world lattice (and
multiples of 50 in height). A room is floor grids chained edge to edge, as the research
counts them (kitplace.js `rooms`): planes within 25 uu, facing edges within 1 uu, 150 uu
or more of shared edge, three grids or more. Its phase is measured from its own grids:
on x and y the value mod 150 that most of its grid edges lie within 1 uu of, on z its
middle grid's plane mod 50, so a floor dropped on or beside it takes its plane. The room
is the one with a grid within 1,200 uu (four trap cells, the neighbour reach) of the
piece's main grid, nearest across the plan, then nearest in height (`roomAt`). The level's
own floors, the plan's and Fab grids all count; the piece being placed or moved does not.

Within 37.5 uu of a neighbour's grid the join wins over the lattice:

- grids edge to edge or lined up; coplanar floors and walls;
- a wall's stop on the edge of the floor in front of it, its grid bottom its lift over that
  floor's plane (also over a floor running under it); a floor's edge on the stop of a wall
  standing on it;
- an upper floor (its plane -10 to 300 over the top of a wall's meshes, behind the wall):
  its edge on the wall's origin line;
- a column (wall grids facing three ways or more) at a wall's end: its grid facing the
  same way edge to edge with the wall's grid, on a skirted wall's origin line, 50 in front
  of a plain wall's face.

A Fab grid has no fit data and keeps the rules from before fit data (20 over a floor, the
actor origin as its line and stop). A move snaps only the axes being dragged, by the picked
floor (else the first picked piece with a grid), and only a piece turned by quarter turns.
A level's own piece off the world lattice moves on its own (its offset at the start of the
move is kept), so a move never jumps it off it; any other piece moves on the lattice of the
room it starts in. A move that ends by a different room takes that room's lattice. The
joined neighbour is outlined during the move.

**Other pieces** move in quarter-cell steps (75 uu) counted from where the move began, and
snap to a neighbour's face or edge within 37.5 uu (`edgeSnapMove`, `edgeGaps`). Faces that
meet are measured on the real triangles: each side's surface facing the other, clipped to
the other side's extent across the move, so the empty corners of a turned or hollow piece's
box never count; lining edges up uses the box around the real triangles. Placed, a floor
tile, stair or crenellation shelf mesh (catalogue `lattice`, `meshcat.FOOTPRINT_LATTICE`:
the `CastleOrder/Floors` and `Stairs` folders and `Cren_Shelf_1x1`) lands with its footprint
on the lattice (`footprintDelta`; the room's or the world's, as above); any other mesh
(trims, wall sections, columns, beams) lands against a neighbour's face or edge across the
plan within 37.5 uu, else where it is dropped; its height as clicked. Decor (meshes on New
Object's decor shelf, catalogue category `decoration`) lands where it is dropped.

Shift held while placing, moving, turning or sizing takes every snap away; alt takes the
neighbour joins and edge snap away. A new scratch level's ground grid, rift, exit point and
player starts are on the lattice at ground height 0 (`tests/offline/test_kit_snap.py`, with
a case per census rule on the class geometry it relies on; UI smoke case `lattice`, which
also drags a plain wall, a short-skirted wall and a column to their joins, places a ceiling,
builds an off-lattice room of three floors and drops a floor, a floor tile mesh, two column
meshes and a decor mesh by it, and drags a floor to it).

A kit class's trap grids (`/api/kit` `grids`, from `riftlord/data/kit-geometry.json`, written
by `python3 -m riftlord.mapeditor.kitgeom --build`) are in the actor's space: each grid's
transform is composed up its Simple Construction Script parents to the root component, over
the class and its Blueprint ancestors merged by component name (`kitgeom.grids`). Before
2026-11-05 a grid's own RelativeLocation was taken alone, which put 32 grids of
`Trapezoid_Landing` and `Stairs_MultiTiered` 170 to 2,676 uu off. The table also measures the
Blueprint classes the shipped levels place outside the kit catalogue (grid-only actors:
`BP_OMDTrapGrid_Blank`, `Scenario_03_Ground1..4`), and a class is looked up whatever the case
of its name (`Wall_RB_MT_v3_1x4_Angle_C` is the asset `Wall_RB_MT_v3_1x4_angle`). Checked
against the kit placement census (`docs/research/kit-placement-census.md`): 11,872 placements
agree on count, centre, normal and size; 41 state their own grid transform; the 8
`Wall_RB_MT_v3_1x4_Angle` placements on Scenario_14 differ only because the census read
them without their class template (the same case mismatch). Test:
`tests/offline/test_kitgeom_grids.py`.

## Cover seams

Map Settings' **Seams** section has **Cover Seams**: it finds the seams of the open map (an
edited shipped level or a scratch map, the same way) that shipped levels hide with a static
mesh, and proposes that mesh for each (`riftlord/mapeditor/coverseams.py`, `POST
/api/coverseams/<level>` with the plan; `web/mapeditor/src/coverseams.js`). Shipped levels
hide seams with geometry, not by stretching pieces: walls carry their own skirting, and
trims, columns and wall sections straddle the rest (research:
`docs/research/kit-placement-census.md`, `kit-seams-and-trap-grids.md` section 3).

A seam is an opening between two kit pieces turned by quarter turns, measured on their real
vertices with the census's junction windows (its `junctions.py`):

| seam | what is open |
|---|---|
| Wall top | an upper floor's surface edge short of the back face of the wall top under it (the hole seen from above; a floor edge over the wall top is the shipped ledge, not a seam) |
| Wall foot | a floor's surface edge short of the foot of the wall standing on it: the floor stops short, or the wall's skirting is buried |
| Skirting | a wall standing on a floor with its lowest part above the floor's surface |
| Wall end | a wall's mesh end short of the column, or the wall beside it on the same plane, that it ends against (measured in the band 100 uu in from each end) |
| Floor join | two floors of different families edge to edge whose surfaces do not meet |

A seam another piece or mesh already covers (its box spans the opening and runs along at
least half the seam, as the census counts covers) is left alone, and so is one shipped levels
leave open: of the 10 shipped seams of its type nearest it in width (`shipped_widths` in the
data file), more open than covered (the 1 to 2 uu slivers between plain walls and their
floors, for one).

**The panel.** Under Cover Seams, the counts on a line of their own (`N to cover · N accepted
· N not placed`), Accept All, then one row per proposal: the seam's kind and width
(`Wall end · 4 uu`) with Accept and Skip. Picking a row draws its cover over the level and
looks at it; the picked row names the cover mesh and how many pieces. The seams no cover
suits are under **Not Placed** (collapsed): each row the seam's kind and width, and under it
what stops it, one sentence: what is wrong with the seam when no shipped cover suits it
(`Floor stops short of the wall`, `Wall stands above the floor`, `Wall ends short of its
neighbour`, `Floor surfaces do not meet`), or `A cover would block trap cells`, `No cover
closes it`, `Too wide to cover`. Picking one looks at it, so it can be fixed by hand.

An accepted cover is an ordinary added mesh (`added[n]`, `kind: mesh`) with
`collision: false`, as shipped decor: it can be moved, resized or deleted like any other, and
the build reads it like any other added mesh. Nothing happens at build time and no hidden data
is kept.

**The covers** are `riftlord/data/seam-covers.json`, mined from the 33 shipped levels with the
same seam finders (`/mnt/2tb_pro_a/tmp/coverseams/calibrate.py`, then `covermine2.py`). Each
covered shipped seam's main cover (of the meshes with an axis along the seam, the one running
furthest along it, then the tightest across it) counts only when it closes the seam alone
(below). A cover entry is one **placement**: one cover family (the mesh name without its
length: `ColumnTrim_CastStone_600W/900W/1800W` are one family) turned one way in the seam's
frame, turns that differ only by the mesh's own symmetry counted as one. Its box offsets from
the seam (median, plus every offset a case sat at) and its scale across come from that
placement's own cases; its stretch along the seam is the family's, which no turn changes. A
placement is a cover when it has 10 shipped cases on 3 levels (the census's rule, section 9).

Counting a family across its placements (as the first catalogue did) put one placement's turn
at another's offsets: on Proving Grounds the wall-top trims came out turned 45 degrees and
beside the opening. By placement the shipped data has covers for Stone wall ends only:

| seam | style | cover | turn (rows: run, gap, depth) | cases | levels | stretch along |
|---|---|---|---|---|---|---|
| Wall end | Stone | Column_Sec_RB (1_2, 1_4) | [0,0,1] [-1,0,0] [0,-1,0] | 18 | 9 | 1.0 to 1.25 |
| Wall end | Stone | Column_Sec_RB | [0,0,1] [0,-1,0] [-1,0,0] | 13 | 8 | 1.0 to 1.25 |
| Wall end | Stone | Column_Sec_RB | [0,0,1] [-1,0,0] [0,1,0] | 12 | 6 | 1.0 to 1.25 |
| Wall end | Stone | ColumnTrim_CastStone (600W to 1800W) | [0,0,1] [1,0,0] [0,-1,0] | 10 | 6 | 0.77 to 1.5 |
| Wall end | Stone | Column_Sec_RB | [0,0,1] [0,-1,0] [1,0,0] | 10 | 7 | 1.0 to 1.25 |

Below the rule (the file's `below`): every Wall top placement (the 45-degree
`ColumnTrim_CastStone` on the wall top is 7 cases, all on Scenario_14; shipped wall-top seams
are 33 on 8 levels, median 12 uu wide, because shipped upper floors end on the wall's origin
line), every Wall foot, Skirting and Floor join placement, and the other trim placements at
wall ends (9, 8, 6 cases).

**Closing.** A cover is proposed only when it closes 95% of its opening (`closes()`):
sightlines along the seam's depth axis (the way one looks through it: down through a wall top,
wall foot or floor join, across through a wall end or a skirting) at points 25 uu apart along
each open stretch and 5 across the gap, each closed when a cover triangle crosses it within
150 uu of the seam's depth band. Boxes are not enough: a trim turned 45 degrees spans the
opening with its box and covers none of it. Measured on the shipped levels' own covered seams
(508): all their covers together close 95% in 93% of wall tops, 84% of wall feet, 78% of wall
ends; the 95% bar is what the catalogue's cases met alone.

**Placing a cover.** For each open stretch of a seam, the family's member and count that keep
every piece within the shipped stretch (fewest pieces, then the stretch nearest 1), laid end to
end, turned and scaled across as the placement was shipped, its box at the placement's offset
from the seam (the median, else the nearest other offset of the placement that still spans the
opening); a stretch shorter than the shortest member at its least stretch takes that member
squeezed to fit. Placements are tried most cases first; one is skipped when the seam is wider
than any it covered, when it does not close the opening, or when it would enter a trap cell.
Trap cells are checked as the trap-grid audit checks a blocker
(`/mnt/2tb_pro_a/tmp/tg900/tg.py`): every 300 uu cell of every kit and Fab grid, its slab 15 to
75 uu in front of the grid inset 30 uu, against the cover's box by separating axes.

Proving Grounds (900_Arena_P, a copy; 2026-11-05, by placement): 1 proposal (the Wall end
`#207`/`#69`, 4.4 uu, a `ColumnTrim_CastStone_600W` upright in the corner, closing all of it),
25 not placed: the 7 Wall tops (`#421/#495/#496/#497` under floors `#500/#503/#502/#507`,
38.7 uu, and `#252/#413/#414` under `#198/#253/#255`, 74.1 uu: `Floor stops short of the
wall`), the 3 Skirtings of `#625` to `#627` (7.5 uu), 4 Wood wall ends, and 11 Wall ends where
every placement would take a trap cell. On the 33 shipped levels unedited: 672 seams, 11
proposals (all Stone wall ends: 8 trims, 3 column sections; none on 25 levels, at most 2),
105 not placed. Offline: `python3 tests/offline/test_coverseams.py`; UI
smoke case `coverseams`.

## Compound kits, decoration tools and added foliage

`riftlord/compounds.py` (research: `workshop/research/LEVEL-CONTROLLERS-AND-PATTERNS.md`
parts 2 and 3; offsets measured by `/mnt/2tb_pro_a/tmp/levelpatterns/q4_compounds.py`,
brush statistics in `decor-brush.json` from `q5_brushdata.py`). `GET /api/decor` serves
the definitions; `web/src/compounds.js` and `web/src/decortools.js` place them.

* **Compound** (New Object filter): Archway Gate with Door (2- and 3-wide), Column Stack
  A/B (Mid Tiles), Hanging Chain (Links, Hung Item), Arched Window (small, large), window
  walls and a pointed window with shutters, and 13 wall assemblies measured over the
  shipped levels (`/mnt/2tb_pro_a/tmp/levelpatterns/q8_star_assemblies.py`: pieces at one
  transform in an anchor's frame on 30%+ of its placements in 2+ levels, 6+ copies;
  crenellated and spired columns, the Sand arcade column, pointed column cap, octagonal
  tower top and stepped crenellation, round tower with turret, square turret with wood
  top, wall section with stepped crenellation, slanted arched window, pointed tower top
  with shutters, gold column on block). Such a compound's frame is upright at the anchor's
  origin and its anchor carries `r`, its own shipped rotation-and-scale rows (compounds.js
  `anchorTurn`). Members are ordinary `added` items sharing
  `group`; the anchor also has `compound`, `anchor` and `params`. A click on a member
  picks the group; the Selected panel's Group section rebuilds it from new params or
  ungroups it. Each New Object card previews the whole assembly at its default params
  (`/api/thumb/compound:<key>`, `compounds.thumbnail`). Offline:
  `python3 tests/offline/test_compounds.py`.
* **Provenance**: every compound, hand-made or mined, carries `source`, a real shipped
  occurrence: the level, the sublevel and actor name of each piece (anchor first) and the
  anchor's world transform; its default layout equals that occurrence piece for piece within
  3 uu and 0.01 (`compoundmine.within`). `copies` and `levels` count every place the default
  occurs whole. A parametric compound carries `seen`, its param's values on the levels
  (stack mid tiles, chain links, run sections); its range is exactly that span. The gate
  (`test_compounds.py`) re-finds each named actor in the level scan and checks the layout
  against it; `compoundmine --check` keeps compounds.json (mined under `compounds`,
  hand-made under `hand`) equal to the levels.
* **Blueprint beam walls** carry plain New Object names (`leveldata.kit_label`): Stone Wall
  with Top Beam (Wall_RB_WDMT*), Stone Wall with Beams / Spiked Beams (Wall_RB_WD_v1/v2),
  Braced Timber Wall A/B (v3/v4), `, Dry` for the desert art, `, B` for the second beam
  layout, Column Beam Collar (Trim_Column_WDMT); search "beam" lists them.
* **Mined compounds** (292, `riftlord/data/compounds.json`, loaded by `compounds.MINED`):
  written by `python3 -m riftlord.mapeditor.compoundmine --write` from every shipped level
  and sublevel (static meshes, Blueprint actors by their root, effect emitters; the scan is
  cached per level in `.compoundmine` under the source cache, keyed by the base paks).
  Pieces whose boxes touch are put in each other's frame, clustered within 3 uu / 0.01,
  and closed sets of those transforms over an anchor's placements become candidates;
  non-maximum suppression gives each shipped unit to one candidate. A candidate needs 12
  copies with 2 pieces, 5 with 3, else 3 (the counts at which keys shuffled within role
  find ~10% as many; `--null` prints the table); one structural role in quarter turns is a
  lattice and is dropped. A definition's members are its copy nearest the others, as
  shipped. Each candidate was judged by eye from contact sheets of the default beside the
  shipped spot it was cut from, and its decision kept in
  `riftlord/data/compound-curation.json` (a label and a group, or a reason: fragment,
  slice of a level, duplicate, weak, existing, `audit: ...`); `--sheet DIR` draws the ones
  without a decision. `--check` (gate step "compound mining") rebuilds and compares.
  The Blueprint beam walls also give every candidate on 3+ of their placements (most are
  lattice neighbours or building chunks; four read as patterns: Stone Wall with Top Beam
  between Pillars, with Top Beam and Twin Columns, with Beams and Crenellation, Dry Pillar
  with Beam Collar).
  **Dressed walls** (`compoundmine.dressed`, `"dressed": true`): wood against walls is placed
  freely along the face and stretched to length (1,247 walls carry wood in 1,107 different
  layouts), so a wall and its wood count as copies when each piece has the same key, turn,
  10 uu depth band and third of the wall's height; the compound is the copy nearest the
  others. Two passed the eye check (Braced Stone Wall (Ice), Pillar Cap with Iron Ring);
  stone walls with wooden beams in the game are the Blueprint beam walls above.
  **Walls with wood and iron** (`riftlord/mapeditor/compoundwalls.py`, `"family": "dressed
  wall"`, 34): 1,424 upright shipped walls carry dressing (beams, posts, braces, beam ends,
  planks, scaffolding, brackets, bands, plates). Each piece gets a role and a face in its
  wall's frame; walls are pooled by look (wall class and the main roles on the dressed
  face and top; place along the wall, length and count free) and `shown` counts the walls
  and levels of each look. The compound is one real wall of the look with its dressing as
  shipped (the commonest counts, nothing past the wall). Where a beam, post, brace or
  scaffold passes the wall by more than a tip (50 uu, measured), the default is the whole
  real assembly: the walls, towers and columns the dressing runs onto and its supports down
  to where they stand; a tip or a fitting past the wall stays on the one wall. A look whose dressing is
  one beam has Beam Length, the beam stretched about its centre over the lengths that
  look's walls show (`seen`) and that fit the face; the gate checks no piece floats and
  every length keeps the beam on the face. Decisions are in the curation under
  `family: "dressed wall"` (77 looks on 2+ walls drawn beside their shipped spots).
* **Runs** (`type: "pattern"`, `riftlord/mapeditor/compoundfamilies.py`): a piece repeated
  at one step with the pieces each element carries (arcades, roofs, crenellation runs,
  trims, beams, pipes); `Sections` defaults to the median shipped chain and runs from the
  shortest to the longest one. A part's values are numbers or `[param, factor, offset]`.
  New Object files every compound under a Group (role) and an Art Set (Castle, Sand, Ice):
  two dropdowns under the compound filter offering only values some compound has; cards
  are drawn a page at a time as the list scrolls, compound previews when in view.
* A new **gate** turns its +x side to the viewer: shipped gate doors have their spawner
  on the gate's -x side (34 of 34) and the rift on +x (32 of 34; q6_gate_facing.py).
* **Grinder Pit**: BP_GrinderPit_3X and the Front Lawn kill volume under it (lifted from
  that one, `compounds.donor`, so brush and scale are the shipped pit's), as on Front Lawn;
  floor the opening around it with kit floor pieces. On a scratch level the pit is
  walkable floor (`compounds.pit_floors`). Leave the floor under it open.
* **Hanging Chain** hangs a chandelier, a small chandelier or a falling boom barrel
  (`controllers.js barrelUnder`), its origin at the shipped height over the floor.
* **Chandelier Trap** (Environmental Traps; `riftlord/mapeditor/chandelier.py`): the
  Hanging Chain with the Chandelier, an environmental trap that falls when used or shot (on
  it or its chain), deals physical and fire damage where it lands, its candles go out, and
  it fades and never returns. It is SM_Chandelier_01 at three times its size; its hook is its
  origin, at the boom barrels' height (1130.5; it clears the tallest enemy from 743.1). Shots
  reach it because its body and chain box use the trap mesh profile (OMDTrapMesh) and bind
  their hits to the trap base's `OnHit`, as the base binds its own mesh's.
  The build writes its `ChainLength` from its group's post (a chandelier moved off its chain
  gets none) and carries its kit (`/Game/Mods/RiftLordChandelier`: Blueprint, TrapProtos
  row, fade materials) byte for byte; probe gates `chandelier.gates`,
  `python3 -m riftlord.mapeditor.chandelier --check DIR`, `tests/offline/test_chandelier.py`.
* Copy and paste keep a whole copied group a group (fresh id, its door copied too); box
  select picks whole groups, and added foliage while the Foliage layer shows.
* **Gate doors** are `doors` entries with the gate's `group`. Moving or turning the
  group moves the door with it (`compounds.follow`); the door can be selected and moved
  alone; deleting every member of the group deletes its door (`removeWith`), Ungroup
  unlinks it. On a scratch level the floor a plan door's opening crosses stays walkable
  (`compounds.door_openings`, `scratch.cover`), or the archway's frame parts close it.
* **Decorate** (Level Assets): Wall Run, Hang, Rock Scatter, Foliage, Beam, Railing Run,
  each with editable measured defaults. One placement is one group (`compound` names the
  tool).
* **Added foliage**: `added` items `{'kind': 'foliage', 'name': mesh, 'matrix'}`, one per
  instance, drawn with the Foliage layer. `riftlord/foliageadd.py` writes them into
  `FoliageInstancedStaticMeshComponent`s of InstancedFoliageActors lifted from Scenario_01,
  one component per mesh: Movable, no lightmap, VisibilityId -1, cull distances per mesh
  from shipped foliage (`foliage-cull.json`, `--survey`). The probe gates
  `foliageadd.gates` read every instance back.
* Mesh imports store a trailing `_<number>` as the FName number (`apply.fname_split`):
  `Grass_Clumped_1` is 'Grass_Clumped' number 2, as the cooker writes it.

## Save As

`POST /api/saveas/<level>` `{mission, title, placement, details}` keeps the open mission
as a new Missions record: `bucket.copy` (new id, mod, Guids; waves copied) when it was
opened from an editable mission, then `bucket.save_map` with the page's plan. The copy
gets a Working Title of its own, `RL_Scen_<Name>` (each level name is one mission's), and
may keep the title of the mission it was copied from (docs/APP.md, Same title).

The Details dialog's Campaign Index (`details.campaignIndex`, sent with a save and then
cleared) places the mission in its campaign (docs/APP.md, Missions); the campaign list
comes from `api/campaigns?mission=<id>`. A mission's level is `<mod>_Map01` in Missions and
in the editors; its Working Title is applied when it is packed.

## Probe builds from a record

```
python3 -m riftlord.mapeditor.probe --from-record 900_Pier_Pressure_P \
    --waves SPEC.json --title "Wraiths Probe (Pier Pressure)" --key WraithsPier \
    --out /mnt/2tb_pro_a/tmp/wraiths/pier [--no-install] [--json]
```

- **Source**: the Missions record is read only. Its files are copied into
  `<out>/.probe-<key>` (a scratch Missions folder, emptied each run); the user's Missions
  folder is never written.
- **Identity**: `--key` is letters and digits. It names the mod folder (`Mods/<key>`), the
  mission row (`<key>_Mission01`) and the pak (`900_<key>_P.pak`). The mission Guids are
  new. The probe is listed under Custom Maps. A key already used in that Missions folder
  is refused; one whose mod folder another pak in `~mods` uses fails the registry gate.
- **Waves**: `--waves` replaces the record's onslaught, in the Onslaught Editor's format:
  `{"waves": [{"breakKind": "wave", "breakSeconds": 10, "coin": 1500, "actions": [],
  "spawns": {"<spawner index>": [{"unit": "<row>", "guid": "<32 hex, optional>",
  "count": 2, "promote": 0.5, "difficulty": 5, "addCount": 0, "before": null,
  "after": null}]}}], "events": []}`. A unit resolves by row name, else by `guid`
  (`wavespec.resolve_units`: a rename prints `<old> is now <new>`). The source's other
  onslaught settings (par, details) are kept. The waves' `events` replace the plan's.
- **Gates**: every probe gate, including the mod minion carry (byte for byte from the
  installed mod), level and carried imports, trailers, one FName spelling per package
  path (the pak and each carried mod's pak), and texture mip sizes. Exit 1 on any failure.
- **Output**: the whole run holds a light slot (`riftlord.core.heavyslot`, light lane,
  docs/HEAVY-JOBS.md); a light bake inside it takes a heavy slot for that step. Each wait
  prints once `waiting for a light|heavy slot (held by <holders>)`. The last line is
  `PASS|FAIL pak <path> sha256 <hex>`, or with `--json` one JSON object
  `{ok, pak, sha256, mission, mod, guids, modSources, gates [{name, ok, detail, seconds}],
  timings, within, levelCache, checklist}`.
- **Install and checklist**: `--install` (with or without `--from-record`) puts the pak
  into ~mods through `riftlord.core.pakguard`. It is written as `.part` and renamed in.
  Replacing a pak the running game has mounted waits until the game closes. The probe then
  prints its checklist and writes it beside the built pak as `CHECKLIST-<pak>.md`
  (`checklist` in the JSON). The checklist gives:
  - the pak, and whether it went in at once or after the game closed;
  - whether to restart the game;
  - where to find the mission (Custom Maps > title);
  - what the build changes (`--set`, `--switch`, `--waves`);
  - what to look at (`--check TEXT`, repeatable);
  - what each outcome means (`--outcome "SEEN => MEANS"`, repeatable);
  - the other Rift Lord missions in ~mods.
- **Timings**: `timings` covers the whole process in order (start-up and imports,
  arguments and set-up, waiting for a heavy slot, mod discovery, clear the probe folder,
  record, bake, pack, checks, sha256, then `other` and `total`); `within` is the time
  inside the costly functions those phases call (level build, waves, minion carry and
  gates, registry scan, import resolution); each gate carries its own `seconds`.
- **Kept between rounds** of one `--key` from one record: the mission Guids (so a
  round replaces the same mission), and the level build (`riftlord/mapeditor/probecache.py`,
  in `<out>/.probe-cache-<key>`): a round whose level-build inputs are unchanged copies the
  cached build and builds only the waves. The key covers the call's arguments (files by
  content), the code (riftlord, bpkit, recastgen, ISPC) by content, every pak under Paks
  and the staged folder by size, mtime, ctime and inode (minion-only paks excepted: the
  waves step carries them afresh and the carry gate compares), the switches and the
  RIFTLORD_/OMD3_ environment. A miss prints which parts changed. Fab plans are never
  cached; `--no-cache` builds afresh. The registry scan of installed paks is cached per
  pak by path, size, mtime, ctime and inode (`makemod.installed_tables`).
- **Never installed**: `--install` is refused with `--from-record`.
- **Staged mods**: `--mods-dir DIR` builds against paks not yet installed. For that run,
  mod minions are found, carried and gated from DIR only (`modminions.ROOTS = [DIR]`).
  Imports resolve from the pak itself, then DIR, then the installed Supported Mods, then
  the game. Supported Mods are read from DIR when a pak of theirs is there (at its path
  under Paks or by file name), else from the installed folders. The registry gate checks
  against the installed paks with DIR's in place of same-named ones. The run starts by
  printing which pak each mod was read from (`staged` or `installed`), also in the JSON
  as `modSources`.

Test: `tests/offline/test_probe_from_record.py` (fixture `probe_fixture_record.json`).

## Intro card

`plan['world']['intro'] = 'remove'` (Map Settings, Intro section, "Intro" On/Off; shown
only when the level has one) removes the scenario title card `BP_PlayIntro` shows at
mission start (`UBP_ScenarioIntro_NN` / `UBP_GenericScenarioIntro`: ten seconds, input
UI only, Skip button). 23 of 33 shipped levels have one; Ice, Sand and Other levels
have none. `riftlord/levelintro.py` replaces the widget block in the level script with
a push of its `AnimationFinished` handler's entry, at the same size, so the handler
(story dialogue start, the 06/09 tint swap) runs at once; on war levels the
`MusicIntroStart` block still runs first. Story dialogue keeps its own switch and both
combine (tested on all 23). Probe gate: `levelintro.check_files` reads the built level
back against the shipped one (card gone, handler entry pushed in place, pop kept,
handler stub unchanged). `save_plan` keeps the key (`mapinfo.clean_world`).

## Why flyers first

A ground route has to lie on floor the level's own NavPower graph already covers,
and authoring new navigation data is unsolved -- OMD3 rejects graphs built for it
from OMD2 data. A **flyer ignores the navmesh entirely**, so the only thing that has
to be right is the chain of path points. That makes a flyer spawner the one kind we
can add today and be confident about, which is why it is the default.

The shape to copy is in the Ice DLC. `Scenario_Ice_01`'s second spawner sits at
z=1950 with a door beside it while the ground route runs at -250..350, carries no
`StartingPathPoint` at all, and its `StartingFlyerPathPoint` begins a four-point
chain that cruises at height, drops to 900, and joins **the same exit point** the
ground routes use. Flyers are Hellbats; `bIsFlyer` in `AICharacterProtos` is what
decides, and guessing from the name only works today because every flyer happens to
be one.

## Heights are per level

Nothing here carries a default height. Split Stairs climbs from z=1080 at the foot
of the stairs to a rift at 3610, where the Ice level's flyer route cruises at 1950,
so a constant copied from one level puts points underground in another. The height
field is set from the level's own rift when it opens.

## Layout

| file | what it is |
|---|---|
| `server.py` | stdlib loopback server, `X-Rift-Lord` on every write, nothing cached |
| `leveldata.py` | the level, the units, the roster and the plan store |
| `web/` | the UI: Vite and three.js |
| `trace.py` + `web/src/traceui.js` | the Trace tool (left sidebar): another game's level drawn as a ghost in the 3D view, to rebuild by hand; a dev tool, not in the plan (see below) |
| `trace_omd1.py`, `trace_omdu.py` | the Trace tool's OMD1 (Vision zips) and Unchained (UE3 packages) readers |
| `../omd3level.py` | reads a shipped level's geometry and wiring |
| `../omd3serve.py` | mesh, texture and preview serving, shared with the assembly tool |

## Trace: another game's level as a ghost

Levels from other games are converted by hand. The left sidebar's **Trace** section
(shown while a level is open) picks a game and one of its levels and draws that level's
geometry as a translucent ghost over the open level, never picked and never built:

| game | source | reader |
|---|---|---|
| OMD3 | a shipped level, as the editor draws it (no foliage, no sky) | `leveldata.level` + `omd3serve` |
| OMD2 | a scene from `Orcs Must Die 2/data.zip`, Layers and prefabs, mirrored into Unreal handedness | `omd2mesh.scene_meshes` + `omd2vmesh` |
| OMD1 | a scene from `Orcs Must Die!`'s seven zips | `trace_omd1.py` |
| Unchained | a map's persistent and streamed UE3 packages | `trace_omdu.py` |
| Deathtrap | listed when installed, greyed: no reader (`workshop/research/TRACE-SOURCES.md`) | |

Only installed games are listed. Display: Ghost (faces coloured by slope: floors blue,
walls orange), Wireframe, or X-ray (drawn over the level). X, Y, Height, Turn and Scale
place it; **Fit** puts the source's floor (the low edge of its placements' 2%..98%
band) at the open level's lowest floor, centred on its play area, at scale 1 -- or
exactly where it is, for the open level itself, or lined up by `scratch.source.shift`
for a level `omd2record` converted from that scene.

A level opens with its trace hidden: the kept source and placement are loaded, and the
ghost is fetched and drawn when Show is turned on.

The tool's state is not mission data: `trace.json` in Rift Lord's data folder
(`RiftLord/Trace/trace.json`) keeps it per `mission:<id>` or `level:<name>`:
`{source: {game, name}, at, rotation, scale, opacity, visible, mode}`.

Endpoints: `GET /api/trace/sources` (installed games and their levels),
`GET /api/trace/geometry/<game>/<name>` (the packed ghost, built once and cached in the
`trace` scratch folder), `GET`/`POST /api/trace/state/<key>`. The file is one JSON
header and then float32 positions, uint32 indices and float32 instance matrices per
part: a mesh placed once, or whose placements total at most 20,000 triangles, is baked
into one merged part, and the rest are drawn instanced (`trace.py`'s docstring).
`python3 -m riftlord.trace GAME NAME` builds one and prints its counts.

Coplanar copies (an OMD2 art piece on the exporter's merged floor, two placements of one
piece) are left out of the merged part (`trace.drop_covered`, meta `removed`): a triangle
whose centre and three inner points lie on a larger (or as large, earlier) triangle of
another placement in its plane. Drawing (`traceui.js`): each part's depth pass is a copy of
its own colour material (same shader program) behind it in depth, the colour pass a margin
nearer, so it always passes against its pre-pass; the stencil lets each pixel take one
colour fragment. The Cutaway slider's range takes in the ghost's heights while it is
shown, and its plane clips the ghost. Unchained: a parameter holding an ambient occlusion
map or a mask is never the base colour (the castle kits' `BaseAOMap` holds a flat grey
`<piece>_df`); their DetailDiffuseMap is drawn at its world tiling (`DetailScale`).

`python3 -m riftlord.mapeditor.trace check --textures` (a gate step) reads Unchained
PvE_TheWall and OMD2 TheCrunch: why each unread texture failed (format, mip table, .tfc,
LZO, short data, decode), the small, flat and flat mid-grey textures read (a mid grey is
flagged), and the coplanar copies removed, left in the baked part (flagged) and among every
placement. `--game G --level L` checks other levels.

## Doors draw as their real mesh

A door used to be a box marker. That says where it is and nothing about **which way
it faces**, which is the whole question for a door, so the portcullis is now drawn --
for the level's doors and the plan's -- in a `door meshes` layer you can switch off.
The marker stays as the click target and draws with depth testing off, so a door is
still selectable through the mesh wrapped around it.

Getting there needed Blueprint inheritance, and it is per component **and** per
property:

* `BP_Door_Interior_3W_C`'s `DoorMesh` sets only `AttachParent`, so its mesh *and* its
  transform come from `BP_Door_Interior_Base_C`. Read alone the class has no meshes at
  all, which is why a door drew as nothing.
* `BP_Door_Exterior_3W_C` overrides `DoorMesh`'s *mesh* but not its transform, and its
  force-field plane's *location and scale* but not its mesh or rotation.

So appending a child's components to its parent's duplicates them, and replacing them
wholesale loses whichever half the child did not state. `omd3actors.parts_of` merges
by component name, field by field, ancestor first, and `parent_of` reads the generated
class export's `SuperIndex` -- the two cheaper guesses both made the force-field door a
child of `BP_OMDGameState_C`.

Rebuild `omd3-actor-parts.json` after changing any of that, or `kit_parts` keeps
serving the cached pre-inheritance answer.
