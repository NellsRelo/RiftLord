"""Write a Rift Lord plan into a copy of a level.

This is the step that makes the tool worth anything: without it a build produces a
copy of the shipped map and none of the authoring.

    python3 -m riftlord.apply --level Scenario_05        # dry run, reports what it did

## What goes in

A rift, a spawner and a door are lifted whole from a shipped level and placed --
`levelbuild.lift_actor` copies the export and everything it depends on, which is why a
Blueprint actor arrives working rather than as an empty shell.

A route is an `OMDAIPath` owning a list of `OMDPathPoint`s, each carrying its scalars and
an array of `OMDPathBranch` structs. The branch length is the distance between the two
points, computed here, because the author sets weights and never a length.

A spawner names its route through **soft** references -- `StartingPathPoint`,
`StartingFlyerPathPoint`, `StartingDoor` -- which are an FName holding the level's own
package path followed by an FString sub-path like `PersistentLevel.PathPoint_1`. So they
can only be written once the level's new name is known, which is why this runs on the
copy rather than on the original.

The level's own gameplay is **parked**, never deleted: removing an export renumbers every
reference to it in the package.
"""

import argparse
import math
import os
import struct
import sys
from riftlord.core import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

from riftlord.core import gamefiles  # noqa: E402
from riftlord.build import levelbuild
from riftlord.formats.omd3 import omd3level  # noqa: E402
from riftlord.formats.omd3 import omd3actors  # noqa: E402
from riftlord.build import pathemit  # noqa: E402
from riftlord.formats.ue.uassetio import Package  # noqa: E402

from riftlord.mapeditor import leveldata  # noqa: E402

#: The airspace a flyer route needs. A level with no flyer route ships none of these, and
#: a flyer in a level without one spawns and cannot move.
FLIGHT_VOLUME = 'NavPowerFlightVolume'

#: Far enough below any level that nothing reaches it, and finite.
PARKED = (0.0, 0.0, -50000.0)


def donor_for(class_name, cache={}):
    """(package, export index) to lift one class from, using the actor catalogue.

    `omd3actors` already records a level and an export for all 714 placeable classes,
    so any of them can be lifted without a hand-written donor list.
    """
    if class_name in cache:
        return cache[class_name]
    info = omd3actors.load().get(class_name)
    if not info:
        cache[class_name] = None
        return None
    try:
        ua, ux = gamefiles.package(info['level'])
        pkg = Package(ua, ux)
        pkg.load_payloads()
    except Exception:                                # noqa: BLE001
        cache[class_name] = None
        return None
    # VERIFY the recorded export really is that class, and rescan if not. The catalogue
    # had `NavPowerFlightVolume` at an export that is a `NavPowerExclusionVolume`, so a
    # lift would silently have produced the wrong actor.
    index = info['export']
    if _class_at(pkg, index) != class_name:
        found = next((i for i in range(len(pkg.exports))
                      if _class_at(pkg, i) == class_name), None)
        if found is None:
            cache[class_name] = None
            return None
        index = found
    cache[class_name] = (pkg, index)
    return cache[class_name]


def _class_at(pkg, index):
    """The class name of one export, by the catalogue's own index.

    **ZERO-based** -- the convention `omd3actors` records and `levelbuild.lift_actor` and
    `_name_of` take. Reading `exports[index - 1]`, the 1-based convention
    `levelbuild.resolve` uses for a package index, made every class look wrong: the lookup
    rescanned and handed back a 1-based index that `lift_actor` read as 0-based, so the
    spawner came out as the level's onslaught coordinator and never spawned anything.
    Both conventions live in this codebase; check which one a function wants.

    Measured: `exports[83]` of Scenario_01 is `BP_OMDAISpawner` of class
    `BP_OMDAISpawner_C`, which is exactly what the catalogue records.
    """
    if not 0 <= index < len(pkg.exports):
        return None
    try:
        return levelbuild.resolve(
            pkg, struct.unpack_from('<i', pkg.exports[index]['meta'], 0)[0])[1]
    except Exception:                                # noqa: BLE001
        return None


def asset_path(level_path):
    """`/Game/.../Foo` -> `/Game/.../Foo.Foo`, the form an FSoftObjectPath needs.

    An FSoftObjectPath's asset path is `<package>.<asset>`, not the package path on
    its own. Writing the bare package path produces a reference that resolves to
    NOTHING at runtime, silently: the spawner's StartingFlyerPathPoint reads as
    null, the controller falls back to the level's first start point, and every
    flyer sets off down the host map's ground chain. Four hellbat wisps sat on the
    spawner for three test builds because of the missing four characters.

    The level copier already writes the dotted form when it retargets a reference
    the donor had, which is why the host's own spawner was wired correctly and only
    the spawner Rift Lord added was broken.
    """
    tail = level_path.rsplit('/', 1)[-1]
    return level_path if '.' in tail else '%s.%s' % (level_path, tail)


def fname_safe(name):
    """`name` with any trailing `_<digits>` closed up, so FName cannot re-split it.

    An FName is (string, number), and UE builds one from a soft path's TEXT by
    splitting a trailing `_<digits>` into the number. An export stored literally as
    `RiftLordPoint_0_0` is therefore looked up as (`RiftLordPoint_0`, 1) and never
    found: the spawner's `StartingFlyerPathPoint` resolves to null, the controller
    falls back to the level's first start point, and every unit from that spawner
    walks the host map's route instead of its own.

    It fails silently and it is invisible in a report, because ("RiftLordPoint_0", 1)
    and ("RiftLordPoint_0_0", 0) both PRINT as RiftLordPoint_0_0. This was the second
    silent failure of the same property, after the missing `.Asset` suffix.

    No shipped level names an actor with a trailing `_<digits>`: `OMDPathPoint20`,
    `Door01`, `MobSpawnerDoor2`. `emit.path_checks` enforces it.
    """
    out = name
    while True:
        head, sep, tail = out.rpartition('_')
        if not sep or not tail.isdigit() or not head:
            return out
        out = head + tail


def soft_ref(builder, package_index, field, level_path, sub):
    """Set a SoftObjectProperty to `<level path>` + `PersistentLevel.<sub>`.

    The payload is an FName (index and number) then an FString: the asset path and the
    object's path inside it. Written by hand because the builder's copiers only ever
    retarget a soft reference that was already there.
    """
    from riftlord.build import makelevel
    base, number = makelevel.world_name(builder.pkg)
    path = level_path.split('.', 1)[0]
    if number:
        # the copy keeps a split-number World name (`Scenario` + 17): name it as it is
        name_index = builder.pkg.add_name('%s.%s' % (path, base))
    else:
        name_index = builder.pkg.add_name(asset_path(level_path))
    text = 'PersistentLevel.%s' % sub
    payload = (struct.pack('<ii', name_index, number)
               + struct.pack('<i', len(text) + 1)
               + text.encode('ascii') + b'\x00')
    builder.set_property(package_index, field, 'SoftObjectProperty', payload)


def _distance(a, b):
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def next_point_index(level_name):
    """One past the highest `PointIndex` the level already uses.

    A level's path graph is keyed by `PointIndex`, and a branch names its target by that
    number alone -- so a new point reusing one makes the graph ambiguous. Numbering from
    zero against North Wing's existing 1..6 produced six duplicate indices and a branch
    that pointed at the level's own second point, closing a loop through the graph: the
    map hung on the loading screen with every offline check passing.
    """
    try:
        from riftlord.onslaught import onslaught
        used = [rec.get('index') for rec in
                onslaught.map_report(level_name)['points'].values()]
        return 1 + max([i for i in used if isinstance(i, int)] or [0])
    except Exception:                                # noqa: BLE001
        # Better to start absurdly high than to collide: the index only has to be
        # unique within the level and the game never enumerates the gap.
        return 1000


def level_path_index(builder):
    """The package index of the level's own `OMDAIPath`, or None.

    A level has one, and every point belongs to it.
    """
    from riftlord.build import levelbuild as _lb
    for i, e in enumerate(builder.pkg.exports):
        cls = _lb.resolve(builder.pkg, struct.unpack_from('<i', e['meta'], 0)[0])[1]
        if cls == 'OMDAIPath':
            return i + 1
    return None


def level_point_indices(level_name):
    """{export name: PointIndex} for the points the level already has.

    A branch may name one of these instead of a planned point, which is how an added route
    joins the level's own -- the exit point especially, since a level has exactly one and
    it is already there.
    """
    try:
        from riftlord.onslaught import onslaught
        return {nm: rec['index'] for nm, rec
                in onslaught.map_report(level_name)['points'].items()
                if isinstance(rec.get('index'), int)}
    except Exception:                                # noqa: BLE001
        return {}


def exit_point_index(level_name):
    """The `PointIndex` of the level's own exit point, or None.

    **Every shipped level has exactly one exit point** -- Scenario_01, Scenario_05 and
    Scenario_Ice_01 all have one, and Ice_01's flyer route ends on the same one its ground
    routes use. So an added route must END BY POINTING AT IT rather than declaring an exit
    of its own: two exit points is the same ambiguity as two rifts.
    """
    try:
        from riftlord.onslaught import onslaught
        for rec in onslaught.map_report(level_name)['points'].values():
            if rec.get('exit') and isinstance(rec.get('index'), int):
                return rec['index']
    except Exception:                                # noqa: BLE001
        pass
    return None


def used_spawner_indices(level_name):
    """Every `indexInLevel` the level already uses."""
    try:
        from riftlord.onslaught import onslaught
        return {sp['index'] for sp in onslaught.map_report(level_name)['spawners']
                if isinstance(sp.get('index'), int)}
    except Exception:                                # noqa: BLE001
        return set()


def apply_plan(uasset, uexp, level_name, level_path, object_name, plan=None):
    """(uasset, uexp, report) for the copy, with the plan written into it."""
    plan = plan if plan is not None else leveldata.load_plan(level_name)
    first_point = next_point_index(level_name)
    taken_indices = used_spawner_indices(level_name)
    #: a level from scratch frees the indices of the host spawners it removes
    from riftlord.mapeditor import scratch as _scratch
    if _scratch.is_scratch(plan):
        taken_indices = _scratch.taken_indices(level_name, plan)
    # Filled from the level's own exports below, before anything is added.
    taken_names = set()

    def free_name(base):
        """`base`, or the first `baseB`, `baseC` no export already answers to.

        Two exports with one name make every reference to it ambiguous. It matters
        because Rift Lord can be pointed at its OWN output -- a map the Onslaught Editor
        copied -- and then `RiftLordSpawner0` already exists.

        The suffix is a LETTER, never `_2`, for the reason in `fname_safe`: a trailing
        `_<digits>` is re-read by FName as a number and the name stops resolving.
        """
        name, n = fname_safe(base), 1
        while name in taken_names:
            n += 1
            name = '%s%s' % (fname_safe(base), chr(ord('A') + n - 2))
        assert name == fname_safe(name), name
        taken_names.add(name)
        return name

    def free_index():
        """The lowest `indexInLevel` no spawner is using, and reserve it."""
        n = 1
        while n in taken_indices:
            n += 1
        taken_indices.add(n)
        return n
    builder = levelbuild.Builder.around(uasset, uexp, name=object_name)
    from riftlord.mapeditor import geomedits as _geomedits
    visibility_snap = _geomedits.snapshot(builder)
    #: 'added' is a COUNT of geometry added from the plan. Notes go in 'notes': a
    #: note list was briefly written under 'added' too, which made every build with a
    #: doorless spawner die on `'int' object has no attribute 'append'`.
    report = {'rifts': 0, 'doors': 0, 'spawners': 0, 'points': 0, 'branches': 0,
              'added': 0, 'parked': 0, 'moved': 0, 'skipped': [], 'notes': []}

    # -- the level's own gameplay, moved or parked -------------------------
    # Keyed by BOTH spellings of every export's name. An FName is a string plus a
    # number, and number N renders as `_N-1`: the actor everything calls `BP_OMDRift_2`
    # is stored as `BP_OMDRift` with number 3, so the string `BP_OMDRift_2` is nowhere in
    # the name table. `_name_of` returns the bare string, so matching on the name the UI
    # shows found nothing and `wiring` could never move or park the level's own rift or
    # spawner -- it reported "not in the level" and carried on. `Door01` worked only
    # because its digits are part of the string.
    by_name = {}
    for i, e in enumerate(builder.pkg.exports):
        bare = levelbuild._name_of(builder.pkg, i)
        if not bare:
            continue
        number = struct.unpack_from('<i', e['meta'], levelbuild.NAME_AT + 4)[0]
        shown = bare if number == 0 else '%s_%d' % (bare, number - 1)
        by_name[bare] = i + 1
        by_name[shown] = i + 1
        taken_names.add(bare)
        taken_names.add(shown)
    # The level's own gameplay actors are moved, turned and parked by
    # `planactors.emit`, below, with the rest of the plan's actors.
    if _scratch.is_scratch(plan):
        _scratch.park_host(builder, plan, level_name, by_name, report)

    # -- the level's own GEOMETRY, moved or taken out ----------------------
    #
    # Most of a level's geometry streams from sublevels, and `sublevel_edits` hands
    # those to `apply_to_sublevel`. A handful of pieces sit in the PERSISTENT level
    # itself -- 9 of Scenario_05's 1,987, among them both portcullises and the liquid
    # planes -- and only this function can reach those. `emit` skips the `persistent`
    # tag with a comment saying it is handled here, and it was not: moving or removing
    # one of those pieces was dropped with `moved: 0`, nothing in `not_applied` and no
    # word to the user, which is the worst way for an editor to be wrong.
    mine = (sublevel_edits(level_name, plan) or {}).get('persistent') or {}
    from riftlord.mapeditor import geomedits
    geomedits.apply_edits(builder, mine, report)
    if mine.get('lights'):
        from riftlord.build.world import liquidlights
        liquidlights.paint(builder, mine['lights'], report)
    retint(builder, mine, report)
    #: the level's own torches' wall plates (riftlord/controllers.py)
    from riftlord.mapeditor import controllers
    controllers.apply_package(builder, mine, report)

    # -- what the plan adds ------------------------------------------------
    for n, rift in enumerate(plan.get('rifts') or []):
        got = _add(builder, 'BP_OMDRift_C', rift['at'],
                   free_name('RiftLordRift%d' % n),
                   rift.get('rotation'), report)
        if got:
            report['rifts'] += 1

    door_index = {}
    for n, door in enumerate(plan.get('doors') or []):
        cls = door.get('cls') or 'BP_Door_Interior_3W_C'
        name = free_name('RiftLordDoor%d' % n)
        got = _add(builder, cls, door['at'], name, door.get('rotation'), report)
        if got:
            door_index[door['id']] = name
            report['doors'] += 1
            reset_component_transforms(builder, got)
            for field, value in (('Health', door.get('health')),
                                 ('SlotSpacing', door.get('slotSpacing'))):
                if value:
                    builder.set_property(got, field, 'FloatProperty',
                                         struct.pack('<f', float(value)))
            #: ANY door class may carry a size, written to its `DoorMesh` component.
            #:
            #: This was the force field's alone, guarded by the class name, because the
            #: default scale used to be a force field's and writing it unconditionally
            #: stretched every placed portcullis. The default is 1 now and only a force
            #: field is given `FORCEFIELD_SCALE`, so the guard was protecting against a
            #: bug that no longer exists -- and it meant a portcullis could not be
            #: fitted to its archway. A scale of exactly 1 is still never written, so
            #: an untouched door is byte for byte what it was.
            scale = tuple(float(door.get(k) or 1.0)
                          for k in ('scaleX', 'scaleY', 'scaleZ'))
            if scale != (1.0, 1.0, 1.0):
                mesh = _child_named(builder, got, 'DoorMesh')
                if mesh is None:
                    report['skipped'].append(
                        '%s has no DoorMesh component, so it cannot be resized'
                        % name)
                else:
                    final = tuple(a * b for a, b in
                                  zip(authored_door_scale(cls), scale))
                    builder.set_vector(mesh, 'RelativeScale3D', final)
                    report['notes'].append(
                        '%s DoorMesh scaled to %g, %g, %g' % ((name,) + final))

    # -- path points, spawners, player starts, and the level's own actors ----
    from riftlord.mapeditor import planactors
    planactors.migrate(plan)
    ctx = {'report': report, 'by_name': by_name, 'door_index': door_index,
           'free_name': free_name, 'free_index': free_index,
           'taken_indices': taken_indices, 'first_point': first_point,
           'add': _add, 'soft_ref': soft_ref, 'add_preload': add_preload,
           'CREATE_BEFORE_SERIALIZE': CREATE_BEFORE_SERIALIZE,
           'place_whole': place_whole, 'donor_for': donor_for,
           'branch_body': pathemit.branch_body, 'level_path_index': level_path_index,
           'FLIGHT_VOLUME': FLIGHT_VOLUME}
    planactors.emit(builder, plan, level_name, level_path, ctx)
    planactors.emit_player_starts(builder, plan, ctx)

    # -- geometry the plan adds --------------------------------------------
    added = plan.get('added') or []
    if added:
        from riftlord.formats.omd3 import meshcat
        from riftlord.mapeditor import kitactors
        cat = meshcat.load()
        actor_donor = donor_for('StaticMeshActor')
        #: added teleporters by their plan id, to link once both ends exist
        teleporters = {}
        #: {index in `added`: placed actor} for the materials painted on them
        placed = {}
        for n, item in enumerate(added):
            if item.get('kind') in ('fab', 'fx', 'events', 'foliage', 'script'):
                continue                     # fabemit / lavafx / missionevents / foliageadd / missionscript
            if item.get('kind') == 'crystal':
                #: the Crystal Defense crystal, after the spawners are numbered
                #: (riftlord/mapeditor/crystal.py)
                from riftlord.mapeditor import crystal
                if crystal.piece(plan) is not item:
                    report['skipped'].append('a second Crystal is not placed')
                    continue
                got = crystal.place(builder, item, free_name(crystal.PLACED_NAME), plan,
                                    report)
                if got:
                    report['added'] += 1
                    placed[n] = got
                continue
            if item.get('kind') == 'kit' and kitactors.handles(item.get('name')):
                #: chests and pickups: classes no shipped level places (riftlord/kitactors.py)
                got = kitactors.place(builder, item, 'RiftLordKit_%d' % n, plan, report)
                if got:
                    report['added'] += 1
                    placed[n] = got
                continue
            if item.get('kind') == 'kit':
                # A kit piece is a Blueprint, so it lifts like a rift or a door and
                # arrives with all of its own meshes, placed with the whole transform.
                where, rot, scale = transform_of(item['matrix'])
                #: an actor with settings of its own lifts from a matching placement
                #: (riftlord/controllers.py)
                from riftlord.mapeditor import controllers
                got = _add(builder, item['name'], where, 'RiftLordKit_%d' % n, rot, report,
                           scale=scale, donor=controllers.donor(item) or _compound_donor(item))
                if got:
                    controllers.after_add(builder, got, item, report)
                    _collision_off(builder, got, item, report)
                    report['added'] += 1
                    report['scales_carried'] = (report.get('scales_carried', 0)
                                                + carry_scale(builder, got, item['name']))
                    if item.get('tid') is not None:
                        teleporters[item['tid']] = (got, item)
                    placed[n] = got
                continue
            info = cat.get(item['name'])
            if not info or not actor_donor:
                report['skipped'].append('no way to place %s' % item['name'])
                continue
            path = info['path'].replace('../../../OMD/Content', '/Game')
            idx, why = add_mesh(builder, actor_donor, path, item['name'],
                                item['matrix'], 'RiftLordMesh_%d' % n)
            if idx is None:
                report['skipped'].append(why or ('could not place %s' % item['name']))
            else:
                _collision_off(builder, idx, item, report)
                report['added'] += 1
                placed[n] = idx
        link_teleporters(builder, teleporters, report)
        #: materials painted on added meshes and kit pieces (riftlord/lavafx.py)
        from riftlord.build.world import lavafx as _paints
        _paints.paint_added(builder, added, placed, report)

    #: the Mission Events piece (riftlord/missionevents.py), on any level
    from riftlord.missions import missionevents
    report['added'] += missionevents.apply(builder, level_name, plan, report)
    #: the mission's own scripts (riftlord/missions/missionscript.py), on any level
    from riftlord.missions import missionscript
    report['added'] += missionscript.apply(builder, level_name, plan, report)
    #: effects: the plan's `fx` items (riftlord/lavafx.py), on any level, a new one too
    from riftlord.build.world import lavafx
    report['added'] += lavafx.apply(builder, level_name, plan, report)
    #: the chosen sky's moon, from its home level (riftlord/skyfog.py)
    from riftlord.build.world import skyfog
    try:
        report['added'] += skyfog.lift_extras(builder, level_name, plan, report)
    except Exception as exc:                         # noqa: BLE001
        report['skipped'].append('sky pieces not placed: %s: %s'
                                 % (type(exc).__name__, exc))

    #: Fab library pieces and trap grids (riftlord/fabemit.py), only when the plan has them
    from riftlord.mapeditor import fabemit
    if fabemit.uses_fab(plan):
        report['added'] += fabemit.apply(builder, plan, report)['pieces']
    #: instanced foliage the plan adds (riftlord/foliageadd.py)
    from riftlord.mapeditor import foliageadd
    report['added'] += foliageadd.apply(builder, plan, report, free_name)

    #: Last, so it paints whatever ended up in the level -- including anything the
    #: plan added -- rather than only what the level shipped with.
    try:
        paint_look(builder, plan, report,
                   planes=plane_exports(level_name, plan).get('persistent'))
    except Exception as exc:                         # noqa: BLE001
        report['skipped'].append('could not repaint the look: %s: %s'
                                 % (type(exc).__name__, exc))

    #: the plan's world settings (kill height, War Scenario flag)
    try:
        from riftlord.mapeditor import mapinfo
        mapinfo.apply_world(builder, plan, report)
    except Exception as exc:                         # noqa: BLE001
        report['skipped'].append('could not write the world settings: %s: %s'
                                 % (type(exc).__name__, exc))

    #: blocking and kill volumes: the plan's edits, and any a ground route crosses
    if not _scratch.is_scratch(plan):
        try:
            from riftlord.mapeditor import volumes
            volumes.apply(builder, plan, level_name, report)
        except Exception as exc:                     # noqa: BLE001
            report['skipped'].append('could not write the volumes: %s: %s'
                                     % (type(exc).__name__, exc))

    #: scene settings and the intro filter, where the persistent level holds them;
    #: sublevels get theirs in apply_to_sublevel (riftlord.levelfx)
    from riftlord.build.world import levelfx
    if not _scratch.is_scratch(plan):
        try:
            from riftlord.build.world import skyfog
            scene_world = skyfog.world_with_sky(plan)
            levelfx.apply(builder, scene_world, report)
            said, skipped = levelfx.notes(level_name, scene_world)
            report['notes'] += said
            report['skipped'] += skipped
        except Exception as exc:                     # noqa: BLE001
            report['skipped'].append('could not write the scene settings: %s: %s'
                                     % (type(exc).__name__, exc))

    #: story dialogue (riftlord.storyvo), the intro card (riftlord.levelintro) and the
    #: rift warning box (riftlord.riftwarning)
    from riftlord.build.world import storyvo
    from riftlord.build.world import riftwarning
    from riftlord.build.world import levelintro
    for label, module in (('story dialogue', storyvo), ('intro card', levelintro),
                          ('rift warning', riftwarning)):
        try:
            module.apply(builder, plan, report)
        except Exception as exc:                     # noqa: BLE001
            report['skipped'].append('could not write the %s: %s: %s'
                                     % (label, type(exc).__name__, exc))

    #: moved and added primitives drop out of the level's precomputed visibility
    report['visibility_reset'] = _geomedits.forget_moved_visibility(builder, visibility_snap)
    #: removed or moved geometry: the level's precomputed visibility cells go too
    if _geomedits.visibility_is_stale(plan):
        try:
            report['visibility_cells_dropped'] = _geomedits.forget_precomputed_visibility(
                builder)
        except ValueError as exc:
            report['skipped'].append('could not drop the precomputed visibility: %s' % exc)

    #: every listed actor in the Level export's create-before-serialization group
    report['level_preloads_added'] = builder.preload_listed_actors()
    gaps = builder.level_preload_gaps()
    if gaps:
        raise SystemExit('%d listed actors are not preloaded by the Level' % len(gaps))
    report['preload_dropped'] = [(index, name) for index, name in builder.preload_dropped]
    bad = null_preloads(builder)
    if bad:
        raise SystemExit('null preload dependency on %s' % ', '.join(sorted(set(bad))))
    ua, ux = builder.write()
    return ua, ux, report


def removed_fx(plan):
    """{sublevel tag: [actor export (1-based)]} of the level's own effects the plan
    removes (`removedFx`, keys '<sub>|<actor export>' as /api/level's `vfx` gives them)."""
    out = {}
    for key in (plan or {}).get('removedFx') or []:
        tag, _bar, export = str(key).rpartition('|')
        if tag and export.isdigit() and int(export) > 0:
            out.setdefault(tag, [])
            if int(export) not in out[tag]:
                out[tag].append(int(export))
    return out


def sublevel_edits(level_name, plan=None):
    """Which streamed sublevels the plan touches, and what it does to each.

    Gameplay all lives in the persistent level -- measured across eight levels: 248 path
    points, 31 spawners, 8 rifts, 32 doors and 24 player starts, none of them in a
    sublevel -- so only geometry needs this. A moved or removed wall is in whichever
    sublevel the level streams it from, usually `_Castle`.

    Returns {sublevel tag: {'moved': [(export, matrix)], 'parked': [export]}}.
    """
    plan = plan if plan is not None else leveldata.load_plan(level_name)
    removed = set(plan.get('removed') or [])
    moved = {e['id']: e['matrix'] for e in (plan.get('edits') or [])}
    look = plan.get('look') or {}
    planes = plane_exports(level_name, plan)
    #: sublevels holding scene settings the plan changes (riftlord.levelfx)
    from riftlord.build.world import levelfx
    from riftlord.mapeditor import scratch as _scratch_mod
    #: the plan's world settings, with the chosen sky's fog colours where it sets none
    from riftlord.build.world import skyfog
    scene_world = skyfog.world_with_sky(plan)
    filter_tags = ([] if _scratch_mod.is_scratch(plan)
                   else levelfx.sublevel_tags(level_name, scene_world))
    #: the level's own effects the plan takes out: {sublevel tag: [actor export]}
    gone_fx = removed_fx(plan)
    #: the level's own lights and effects the plan moves (riftlord.leveldata.clean_actor_moves)
    shifted = leveldata.actor_moves(plan)
    #: the level's own torches' wall plates (riftlord/controllers.py)
    from riftlord.mapeditor import controllers
    plates = controllers.plate_edits(plan)
    #: the level's own lights recoloured and effects swapped (riftlord.build.world.levelretint)
    from riftlord.build.world import levelretint
    tints = levelretint.light_edits(level_name, plan)
    swaps = levelretint.swap_edits(level_name, plan)
    if not removed and not moved and not planes and not filter_tags and not gone_fx \
            and not shifted and not plates and not tints and not swaps \
            and not (look.get('sky') or look.get('liquid')):
        return {}
    out = {}
    for tag, by_actor in plates.items():
        out.setdefault(tag, {'moved': [], 'parked': []})[controllers.PLATES_KEY] = by_actor
    for tag, actors in gone_fx.items():
        out.setdefault(tag, {'moved': [], 'parked': []})['parked'].extend(actors)
    #: the level's own moon, when the plan's sky comes from another home (skyfog)
    if not _scratch_mod.is_scratch(plan):
        for tag, exports in skyfog.own_extras(level_name, plan).items():
            rows = out.setdefault(tag, {'moved': [], 'parked': []})['parked']
            rows.extend(e for e in exports if e not in rows)
    for tag, rows in shifted.items():
        out.setdefault(tag, {'moved': [], 'parked': []})['moved'].extend(rows)
    for tag in filter_tags:
        out.setdefault(tag, {'moved': [], 'parked': []})['scene'] = scene_world
    #: lights glowing off repainted liquid planes (riftlord.liquidlights)
    if not _scratch_mod.is_scratch(plan):
        from riftlord.build.world import liquidlights
        for tag, lights in liquidlights.edits(level_name, plan).items():
            out.setdefault(tag, {'moved': [], 'parked': []})['lights'] = lights
    for tag, lights in tints.items():
        out.setdefault(tag, {'moved': [], 'parked': []})['lightColours'] = lights
    for tag, by_export in swaps.items():
        out.setdefault(tag, {'moved': [], 'parked': []})['fxSwaps'] = by_export
    #: per-plane liquid materials, by the sublevel that holds each plane
    for tag, by_export in planes.items():
        if tag == 'persistent':
            continue
        out.setdefault(tag, {'moved': [], 'parked': []})
        out[tag]['look'] = look
        out[tag]['planes'] = by_export
    #: A LOOK OVERRIDE reaches into sublevels. The sky lives in `_Lighting` and the
    #: liquid surfaces usually in `_Environment`, never in the persistent level, so
    #: repainting them means copying those sublevels even when no geometry moved --
    #: without this the repaint ran against the persistent level alone and found
    #: nothing to paint.
    if look.get('sky') or look.get('liquid'):
        for piece in leveldata.level(level_name)['pieces']:
            tag = piece.get('sub') or 'persistent'
            if tag == 'persistent':
                continue
            wants = ((look.get('sky') and piece['mesh'] in omd3level.SKYBOX_MESHES)
                     or (look.get('liquid') and piece.get('liquid')))
            if wants:
                out.setdefault(tag, {'moved': [], 'parked': []})
                out[tag]['look'] = look
    # BY EXPORT, not by piece, and a removal beats a move.
    #
    # A drawable piece is finer than an export: a Blueprint kit piece -- a wall, an
    # archway -- is ONE actor in the level carrying up to 28 drawn parts, so 146 of
    # Scenario_05's 1,370 exports answer to several pieces. Appending per piece wrote
    # the same export up to 28 times and inflated the report by as much; worse, an
    # export both parked and moved was parked first and then moved back, so removing
    # one part of a wall while moving another quietly un-removed it. Last move wins and
    # a removal wins outright, which is the rule the UI now matches by selecting a
    # whole assembly at once.
    #: an instanced component's pieces are its instances, in id order
    instance_of, seen = {}, {}
    for piece in leveldata.level(level_name)['pieces']:
        if piece.get('instanced'):
            key = (piece.get('sub') or 'persistent', piece['export'])
            instance_of[piece['id']] = seen.get(key, 0)
            seen[key] = instance_of[piece['id']] + 1
    for piece in leveldata.level(level_name)['pieces']:
        tag = piece.get('sub') or 'persistent'
        if piece['id'] in instance_of and (piece['id'] in removed or piece['id'] in moved):
            rows = out.setdefault(tag, {'moved': [], 'parked': []}).setdefault(
                'instances', {}).setdefault(piece['export'], {'parked': [], 'moved': []})
            which = 'parked' if piece['id'] in removed else 'moved'
            rows[which].append((instance_of[piece['id']], piece['matrix']))
            continue
        if piece['id'] in removed:
            out.setdefault(tag, {'moved': [], 'parked': []})
            if piece['export'] not in out[tag]['parked']:
                out[tag]['parked'].append(piece['export'])
        elif piece['id'] in moved:
            out.setdefault(tag, {'moved': [], 'parked': []})
            #: the SHIPPED matrix travels with the new one, so the emitter can write
            #: only what actually changed
            out[tag]['moved'].append(
                (piece['export'], moved[piece['id']], piece.get('matrix')))
    for tag, edits in out.items():
        gone = set(edits['parked'])
        last = {}
        for row in edits['moved']:
            export = row[0]
            if export not in gone:
                last[export] = row
        edits['moved'] = list(last.values())
    return out


def retint(builder, edits, report):
    """The plan's light colours (after the liquid lights', so they win) and effect swaps
    for one package (riftlord.build.world.levelretint)."""
    from riftlord.build.world import levelretint
    if edits.get('lightColours'):
        levelretint.paint(builder, edits['lightColours'], report)
    if edits.get('fxSwaps'):
        levelretint.swap(builder, edits['fxSwaps'], report)


def apply_to_sublevel(uasset, uexp, object_name, edits):
    """(uasset, uexp, report) for one sublevel with its geometry edits written in."""
    from riftlord.mapeditor import geomedits
    builder = levelbuild.Builder.around(uasset, uexp, name=object_name)
    report = {'moved': 0, 'parked': 0, 'notes': [], 'skipped': []}
    snap = geomedits.snapshot(builder)
    geomedits.apply_edits(builder, edits, report)
    report['visibility_reset'] = geomedits.forget_moved_visibility(builder, snap)
    if edits.get('look') or edits.get('planes'):
        try:
            paint_look(builder, {'look': edits.get('look') or {}}, report,
                       planes=edits.get('planes'))
        except Exception as exc:                     # noqa: BLE001
            report['skipped'].append('could not repaint %s: %s: %s'
                                     % (object_name, type(exc).__name__, exc))
    if edits.get('scene'):
        from riftlord.build.world import levelfx
        levelfx.apply(builder, edits['scene'], report)
    if edits.get('lights'):
        from riftlord.build.world import liquidlights
        liquidlights.paint(builder, edits['lights'], report)
    retint(builder, edits, report)
    #: wall plates (riftlord/controllers.py)
    from riftlord.mapeditor import controllers
    controllers.apply_package(builder, edits, report)
    ua, ux = builder.write()
    return ua, ux, report


def mesh_import(builder, mesh_path, mesh_name):
    """Import one static mesh so this level can reference it. Returns its index.

    Two imports: the package named by its full path, then the mesh object with that
    package as its outer. Cached per package path and name, or a level that places the
    same wall twice imports it twice; by name alone, two meshes of one name in different
    folders shared the first one's import.
    """
    cache = builder.__dict__.setdefault('_rl_mesh_imports', {})
    key = (mesh_path, mesh_name)
    if key in cache:
        return cache[key]
    package = _numbered_import(builder.pkg, '/Script/CoreUObject', 'Package', 0, mesh_path)
    obj = _numbered_import(builder.pkg, '/Script/Engine', 'StaticMesh', package, mesh_name)
    cache[key] = obj
    return obj


def fname_split(text):
    """(string, number) a cooked FName stores for `text`: a trailing `_<digits>` without a
    leading zero is number digits + 1 (`Grass_Clumped_1` is 'Grass_Clumped', 2). A name
    table entry is loaded unsplit (UE 4.27 FNameHelper::MakeFromLoaded), so an import
    spelled whole does not match the asset's own split name."""
    base, sep, digits = str(text).rpartition('_')
    if sep and base and digits.isdigit() and (digits == '0' or digits[0] != '0'):
        return base, int(digits) + 1
    return str(text), 0


def _numbered_import(pkg, class_package, class_name, outer, object_name):
    """`Package.add_import` with the object name stored split (`fname_split`)."""
    name, number = fname_split(object_name)
    rec = struct.pack('<iiiiiii', pkg.add_name(class_package), 0, pkg.add_name(class_name), 0,
                      outer, pkg.add_name(name), number)
    pkg.imports.append(rec)
    return -len(pkg.imports)


def _properties(data, names):
    """Tagged properties of one export: (name, kind, payload offset, size)."""
    o = 0
    while o + 8 <= len(data):
        ni = struct.unpack_from('<i', data, o)[0]
        if not 0 <= ni < len(names):
            return
        name = names[ni]
        if name == 'None':
            return
        ti = struct.unpack_from('<i', data, o + 8)[0]
        kind = names[ti] if 0 <= ti < len(names) else ''
        size = struct.unpack_from('<i', data, o + 16)[0]
        head = 24 + 1
        if kind == 'BoolProperty':
            head, size = 26, 0
        elif kind == 'StructProperty':
            head = 24 + 8 + 17
        elif kind in ('ArrayProperty', 'ByteProperty', 'EnumProperty'):
            head = 24 + 8 + 1
        yield name, kind, o + head, size
        o += head + size


#: An added mesh is Movable with no LODData and no donor texture-streaming data; False
#: keeps the donor's (as before 2026-10-21).
MESH_FORGET_LIGHTING = True
#: An added mesh reaching further than this (uu: largest catalogue half-extent times its
#: scale) stays Static with its LODData cleared instead of turning Movable; None turns
#: every added mesh Movable.
MOVABLE_MESH_MAX_EXTENT = None


def _mesh_reach(mesh_name, matrix):
    """Largest half-extent of a catalogue mesh under `matrix`'s scale, or 0."""
    from riftlord.formats.omd3 import meshcat
    info = meshcat.load().get(mesh_name) or {}
    extent = info.get('extent') or [0.0, 0.0, 0.0]
    scale = transform_of(matrix)[2]
    return max(abs(e * s) for e, s in zip(extent, scale))


def clear_lod_data(builder, component):
    """Drop a static mesh component's LODData (its donor's lightmap); mobility unchanged.
    Returns how many entries there were."""
    from riftlord.mapeditor import fabemit
    pkg = builder.pkg
    data = bytearray(pkg.exports[component - 1]['data'])
    at, count = fabemit.lod_data(data, builder._terminator(data) + 8)
    del data[at + 4:]
    struct.pack_into('<i', data, at, 0)
    pkg.exports[component - 1]['data'] = bytes(data)
    pkg.exports[component - 1]['size'] = len(data)
    return count


def _collision_off(builder, actor, item, report):
    """The added piece's Collision switch (riftlord/mapeditor/piececollision.py)."""
    from riftlord.mapeditor import piececollision
    if not piececollision.is_off(item):
        return
    done = piececollision.switch_off(builder, actor)
    report['collision_off'] = report.get('collision_off', 0) + 1
    if not done:
        report['skipped'].append('%s has no static mesh to switch collision off on'
                                 % item.get('name'))


def add_mesh(builder, donor, mesh_path, mesh_name, matrix, rename):
    """Place one static mesh: a lifted `StaticMeshActor` pointed at a different mesh.

    A level has no actor for a mesh it never placed, so one is lifted and repointed. The
    component's `StaticMesh` is an object reference, so swapping it is a matter of
    importing the mesh and writing the new index -- which is also how the mesh reaches the
    level's import table, where the game looks for it.
    """
    pkg, index = donor
    actor = builder.lift_actor(pkg, index, rename=rename)
    ours = mesh_import(builder, mesh_path, mesh_name)
    data = builder.pkg.exports[actor - 1]['data']
    names = [n for n, _h in builder.pkg.names]
    root = None
    for name, _kind, at, size in _properties(data, names):
        if name == 'RootComponent' and size == 4:
            root = struct.unpack_from('<i', data, at)[0]
            break
    if not root or root <= 0:
        return None, 'lifted %s has no RootComponent' % rename
    builder.set_object(root, 'StaticMesh', ours)
    #: Movable with no LODData and no donor texture-streaming data, as a Fab piece
    #: (fabemit.forget_lighting): the donor's MapBuildDataId drew the DONOR's lightmap
    #: through this mesh's lightmap UVs (the StaticMeshActor donor is Scenario_01's, so
    #: every piece added to a Scenario_01 level did: Dome_Half, Dome_3x3_Borders_RB).
    if MESH_FORGET_LIGHTING:
        from riftlord.mapeditor import fabemit
        for field in fabemit.CLEARED_ON_PIECE:
            builder.clear_property(root, field)
        if _mesh_reach(mesh_name, matrix) > (MOVABLE_MESH_MAX_EXTENT or float('inf')):
            clear_lod_data(builder, root)
        else:
            fabemit.forget_lighting(builder, root)
    #: The WHOLE transform, not just the position: a mesh the editor turned or resized
    #: used to arrive unturned and at scale 1.
    where, rot, scale = transform_of(matrix)
    place_whole(builder, actor, location=where, rotation=rot, scale=scale)
    return actor, None


#: cos(pitch) below which a piece counts as standing straight up or down.
#:
#: MEASURED, not chosen: the two branches fail in opposite directions. Above this,
#: yaw comes from an `atan2` whose terms both carry cos(pitch), so the matrix's own
#: rounding is divided by it; below it, folding roll into yaw is wrong by about
#: cos(pitch) itself. Sweeping the threshold across five decades against a dense sweep
#: of near-vertical transforms puts the crossover here, at 0.057 degrees of vertical,
#: with a worst normalised error of 6.6e-3 for a piece squashed 20:1 and 5e-7 for the
#: angles the kit actually uses.
def place_whole(builder, index, location=None, rotation=None, scale=None):
    """`place`, and ADD whatever transform property the export does not already have.

    The cooker writes `RelativeRotation` and `RelativeScale3D` only when they differ
    from their defaults, so anything that shipped unturned at scale 1 has no such
    property at all -- and `place` edits a property in place and cannot create one.
    Measured on an emitted static mesh: the location and the rotation landed and
    `RelativeScale3D` was simply absent, so every resize was dropped without a word.
    `set_vector` splices the tag in ahead of the export's None terminator, the same
    thing a lifted trap grid already needs for its own extent.

    `RelativeRotation` is a ROTATOR, not a Vector. Both are three floats, so the wrong
    struct name reads back perfectly in every check here and only the engine, which
    resolves the struct by that name, would have cared.
    """
    wrote = builder.place(index, location=location, rotation=rotation, scale=scale)
    target = builder._root_component(index) or index
    for field, value, kind, default in (
            ('RelativeLocation', location, 'Vector', None),
            ('RelativeRotation', rotation, 'Rotator', (0.0, 0.0, 0.0)),
            ('RelativeScale3D', scale, 'Vector', (1.0, 1.0, 1.0))):
        if value is None or ('%s on export %d' % (field, target)) in wrote:
            continue
        #: NEVER ADD A PROPERTY TO SAY WHAT THE DEFAULT ALREADY SAYS.
        #:
        #: The cooker omits a transform property precisely when it equals its default,
        #: so an absent `RelativeScale3D` already means (1, 1, 1). Writing that back
        #: splices a tag into a cooked export for no gain -- on one build of The Cellar
        #: that was 45 insertions, three of them into shipped sublevel packages, every
        #: one of them a no-op. Growing an export is the one kind of damage this
        #: project's offline checks cannot see (pkgcheck validates the stream, not the
        #: engine's preload dependencies), so it is now done only when the value
        #: actually differs from the default and the property really is needed.
        if default is not None and all(
                abs(float(v) - float(dv)) < 1e-6 for v, dv in zip(value, default)):
            continue
        builder.set_vector(target, field, value, struct_name=kind)
        wrote.append('%s added to export %d' % (field, target))
    return wrote


GIMBAL_COS = 1e-3


def transform_of(matrix):
    """(location, rotator, scale) from a column-major 4x4, back in Unreal's terms.

    The exact inverse of `omd3level._matrix`, which is Unreal's own `FRotationMatrix`
    transposed with the scale applied along each column. So column `c` of the 3x3 part
    is `scale[c]` times row `c` of that rotation matrix -- the scale is each column's
    length and the rotation is the columns normalised.

    Needed because the editor hands back a MATRIX. Writing only `matrix[12..14]` --
    which is all this did -- silently dropped every rotation and every resize, so the
    gizmo's turn and size handles moved things on screen and emitted a level where
    nothing had turned. A rotator is (pitch, yaw, roll) in degrees, which is the order
    `RelativeRotation` stores.
    """
    cols = [[matrix[c * 4 + r] for r in range(3)] for c in range(3)]
    scale = [math.sqrt(sum(v * v for v in col)) for col in cols]
    rows = [[v / scale[c] if scale[c] > 1e-8 else (1.0 if r == c else 0.0)
             for r, v in enumerate(col)] for c, col in enumerate(cols)]
    #: A mirrored piece has a negative determinant, which column lengths cannot see.
    #: Unreal's own FMatrix-to-FTransform negates the FIRST axis in that case, so this
    #: does too -- any single axis reproduces the matrix, and agreeing with the engine
    #: means a round trip through the editor leaves a mirrored kit piece alone.
    det = (rows[0][0] * (rows[1][1] * rows[2][2] - rows[1][2] * rows[2][1])
           - rows[1][0] * (rows[0][1] * rows[2][2] - rows[0][2] * rows[2][1])
           + rows[2][0] * (rows[0][1] * rows[1][2] - rows[0][2] * rows[1][1]))
    if det < 0:
        scale[0] = -scale[0]
        rows[0] = [-v for v in rows[0]]
    # rows[0] is (cos p cos y, cos p sin y, sin p); rows[1][2] is -sin r cos p and
    # rows[2][2] is cos r cos p.
    sp = max(-1.0, min(1.0, rows[0][2]))
    pitch = math.degrees(math.asin(sp))
    #: How near vertical is near enough to treat as vertical.
    #:
    #: Yaw comes out of `atan2` over two terms that both carry a factor of cos(pitch),
    #: so the matrix's own rounding error is divided by it. Measured: at six decimals
    #: the error stays under 1e-4 as long as cos(pitch) is above this, and inside the
    #: band the roll/yaw split is not defined anyway -- so the vertical case is used
    #: there and reproduces the matrix exactly.
    cp = math.sqrt(max(0.0, 1.0 - sp * sp))
    if cp < GIMBAL_COS:
        #: Straight up or down: yaw and roll turn about the same axis and only their
        #: sum is defined. All of it goes to yaw, which is what Unreal reports too.
        yaw = math.degrees(math.atan2(-rows[1][0], rows[1][1]))
        roll = 0.0
    else:
        yaw = math.degrees(math.atan2(rows[0][1], rows[0][0]))
        roll = math.degrees(math.atan2(-rows[1][2], rows[2][2]))
    return ((matrix[12], matrix[13], matrix[14]), (pitch, yaw, roll),
            (scale[0], scale[1], scale[2]))


#: |cos| between two axes of a matrix above which it holds a shear an Unreal transform
#: cannot store.
SHEAR_COS = 1e-3


def unsheared(matrix):
    """`matrix` (column-major 4x4) as the game builds it: unchanged when its axes are
    square to each other, else rebuilt from transform_of (location, rotator, scale)."""
    cols = [matrix[c * 4:c * 4 + 3] for c in range(3)]
    lengths = [math.sqrt(sum(v * v for v in col)) for col in cols]
    if min(lengths) < 1e-9:
        return list(matrix)
    worst = max(abs(sum(a * b for a, b in zip(cols[i], cols[j]))) / lengths[i] / lengths[j]
                for i, j in ((0, 1), (0, 2), (1, 2)))
    if worst <= SHEAR_COS:
        return list(matrix)
    return omd3level._flat(omd3level._matrix(*transform_of(matrix)))


def moved_transform(matrix, original):
    """(location, rotation, scale) to write for a moved piece: only what CHANGED.

    A drawable piece is a COMPONENT, and its matrix is the component's world
    transform -- but the emitter can only place the ACTOR. For a Blueprint assembly the
    component sits at its own offset, rotation and scale inside the actor, so handing
    the actor a component's rotation or scale is simply wrong: a wall whose component
    is authored at 0.95 on z came out at 0.95 twice over.

    Compared against the piece's own shipped matrix instead. Anything the user did not
    touch is passed as None and left exactly as the level has it -- which also means no
    property is spliced into a cooked export to restate a value that was already there.
    """
    want = transform_of(matrix)
    if not original or len(original) != 16:
        return want
    was = transform_of(original)
    same = lambda a, b: all(abs(float(x) - float(y)) < 1e-4 for x, y in zip(a, b))
    return (want[0],
            None if same(want[1], was[1]) else want[1],
            None if same(want[2], was[2]) else want[2])


def _level_point_at(level_name, export, cache={}):
    """Where one of the level's own points sits, for a branch's length."""
    if level_name not in cache:
        try:
            from riftlord.onslaught import onslaught
            cache[level_name] = {nm: rec.get('location') for nm, rec
                                 in onslaught.map_report(level_name)['points'].items()}
        except Exception:                            # noqa: BLE001
            cache[level_name] = {}
    return cache[level_name].get(export)


def plane_exports(level_name, plan):
    """{sublevel tag: {component export (1-based): material}} for `look.planes`.

    `look.planes` maps a liquid piece's id (as /api/level numbers it) to a material.
    Only a `component` piece is its own export and can be painted alone.
    """
    planes = ((plan or {}).get('look') or {}).get('planes') or {}
    out = {}
    #: rocks near lava take their lava material (riftlord.lavafx), painted as planes are
    try:
        from riftlord.build.world import lavafx
        from riftlord.mapeditor import scratch as _scratch_mod
        if not _scratch_mod.is_scratch(plan):
            for tag, rocks in lavafx.rock_paints(level_name, plan).items():
                out.setdefault(tag, {}).update(rocks)
    except Exception:                                # noqa: BLE001
        pass
    if not planes:
        return out
    pieces = leveldata.level(level_name)['pieces']
    for key, material in planes.items():
        try:
            piece = pieces[int(key)]
        except (ValueError, IndexError):
            continue
        if piece.get('source') != 'component' or not piece.get('liquid'):
            continue
        tag = piece.get('sub') or 'persistent'
        out.setdefault(tag, {})[piece['export']] = material
    return out


def paint_look(builder, plan, report, planes=None):
    """Repaint the level's sky and liquid surfaces with the plan's chosen materials.

    An override, so it wins over whatever the level itself uses. Written as
    `OverrideMaterials` on each matching component -- the same property the shipped
    levels use to paint a skybox per level -- so the game reads it the ordinary way
    and nothing about the mesh changes. `planes` ({component export: material}) gives
    single liquid planes a material of their own, ahead of the level-wide one.
    """
    look = (plan or {}).get('look') or {}
    want_sky, want_liquid = look.get('sky'), look.get('liquid')
    planes = planes or {}
    if not want_sky and not want_liquid and not planes:
        return
    from riftlord.formats.omd3 import omd3actors
    from riftlord.formats.omd3 import omd3level
    from riftlord.build.navigation import navvolinject
    pkg = builder.pkg

    def material_index(name):
        """An import for a material instance, by its asset name or its full /Game path
        (a material the mission ships, such as its lava rock: riftlord.lavafx)."""
        if name.startswith('/Game/'):
            owner = builder._import('/Script/CoreUObject', 'Package', 0, name)
            return builder._import('/Script/Engine', 'MaterialInstanceConstant',
                                   owner, name.rsplit('/', 1)[-1])
        path = omd3actors._asset_for(name)
        game = (path or '').replace('../../../OMD/Content/', '/Game/')
        if not game.startswith('/Game/'):
            report['skipped'].append('no asset for the material %r' % name)
            return None
        owner = builder._import('/Script/CoreUObject', 'Package', 0, game)
        return builder._import('/Script/Engine', 'MaterialInstanceConstant',
                               owner, name)

    sky_ref = material_index(want_sky) if want_sky else None
    liquid_ref = material_index(want_liquid) if want_liquid else None
    painted_sky = painted_liquid = 0
    plane_refs = {}
    for export_index, name in planes.items():
        #: one material for the piece, or {slot: material} with the other slots left to
        #: the mesh's own (a null entry)
        if isinstance(name, dict):
            slots = {int(k): material_index(v) for k, v in name.items() if v}
            if slots and all(r is not None for r in slots.values()):
                refs = [slots.get(k, 0) for k in range(max(slots) + 1)]
                plane_refs[int(export_index)] = (refs, ', '.join(
                    '%s (slot %d)' % (name[str(k)], k) for k in sorted(slots)))
            continue
        ref = material_index(name)
        if ref is not None:
            plane_refs[int(export_index)] = ([ref], name)
    for export_index, (refs, name) in plane_refs.items():
        builder.set_object_array(export_index, 'OverrideMaterials', refs)
        report['notes'].append('%s painted with %s'
                               % (levelbuild._name_of(pkg, export_index - 1), name))
    for i, export in enumerate(pkg.exports):
        if i + 1 in plane_refs:
            continue
        try:
            props = navvolinject.walk_properties(export['data'], pkg.names)
        except Exception:                            # noqa: BLE001
            continue
        found = {nm: end for nm, _t, _s, end in props}
        if 'StaticMesh' not in found:
            continue
        ref = struct.unpack_from('<i', export['data'], found['StaticMesh'] - 4)[0]
        if ref >= 0:
            continue
        mesh = levelbuild._import_name(pkg, -ref - 1)
        target = None
        if sky_ref is not None and mesh in omd3level.SKYBOX_MESHES:
            target, which = sky_ref, 'sky'
        elif liquid_ref is not None and omd3level.is_liquid(mesh):
            target, which = liquid_ref, 'liquid'
        if target is None:
            continue
        builder.set_object_array(i + 1, 'OverrideMaterials', [target])
        if which == 'sky':
            painted_sky += 1
        else:
            painted_liquid += 1
    if painted_sky:
        report['notes'].append('sky repainted with %s on %d component(s)'
                               % (want_sky, painted_sky))
    if painted_liquid:
        report['notes'].append('liquid repainted with %s on %d component(s)'
                               % (want_liquid, painted_liquid))
    #: Nothing is reported for a package that simply does not hold one of them.
    #: The sky is in `_Lighting` and the liquids in `_Environment`, so each package
    #: legitimately paints one and not the other, and saying so for both every time
    #: filled the build report with failures that were not failures.


def authored_door_scale(cls):
    """The scale a door class holds its own `DoorMesh` at, or (1, 1, 1).

    Both exterior classes hold theirs at (1, 1.5, 1), so the plan's size has to
    MULTIPLY this rather than replace it -- writing the plan's scale over the top
    squashed the door by a third the moment anyone touched the size handle.
    """
    try:
        from riftlord.formats.omd3 import omd3actors
        for part in omd3actors.parts_of(cls) or []:
            if part.get('component') == 'DoorMesh':
                return tuple(float(v) for v in (part.get('scale') or (1.0, 1.0, 1.0)))
    except Exception:                                # noqa: BLE001
        pass
    return (1.0, 1.0, 1.0)


def link_teleporters(builder, teleporters, report):
    """Point each added teleporter's LinkedTeleporter at the one its plan item links to.

    `teleporters` is {plan id: (package index, plan item)}. A lifted teleporter arrives
    with its link nulled (its donor's partner is not lifted with it), so an unlinked one
    sends nowhere. A link to an end that was not placed is reported and left null."""
    from riftlord.mapeditor import geomedits
    linked = 0
    for tid, (index, item) in sorted(teleporters.items()):
        other = item.get('link')
        if other is None:
            report['notes'].append('teleporter %s is not linked to another, so it sends '
                                   'players nowhere' % tid)
            continue
        if other not in teleporters:
            report['skipped'].append('teleporter %s links to teleporter %s, which is not '
                                     'placed' % (tid, other))
            continue
        partner = teleporters[other][0]
        builder.set_object(index, geomedits.TELEPORT_LINK, partner)
        add_preload(builder, index, CREATE_BEFORE_SERIALIZE, partner)
        linked += 1
    if linked:
        report['notes'].append('%d teleporter link(s) written' % linked)
    return linked


def reset_component_transforms(builder, actor_index):
    """Drop the location, rotation and scale a donor instance set on its child components.

    The children then sit where the class puts them, which is what the editor draws
    (`leveldata.kit_parts`). The force field donor (Scenario_06) is the one shipped
    force field of 23 whose DoorMesh overrides the class rotation (-90, 0, 90) with
    (-90, 0, 0), drops the pane 185 uu and sizes it (12, 30, 1), so a lifted copy faced
    90 degrees off. The root component keeps its transform; it is the actor's placement.
    """
    root = builder._root_component(actor_index)
    pkg, cleared = builder.pkg, []
    for i, e in enumerate(pkg.exports):
        if i + 1 == root:
            continue
        if struct.unpack_from('<i', e['meta'], levelbuild.OUTER_AT)[0] != actor_index:
            continue
        for field in ('RelativeLocation', 'RelativeRotation', 'RelativeScale3D'):
            if builder.clear_property(i + 1, field):
                cleared.append((levelbuild._name_of(pkg, i), field))
    return cleared


#: The four preload groups an export lists, in file order.
SERIALIZE_BEFORE_SERIALIZE, CREATE_BEFORE_SERIALIZE, SERIALIZE_BEFORE_CREATE, \
    CREATE_BEFORE_CREATE = range(4)


def add_preload(builder, package_index, group, target):
    """Add `target` to one preload group of an export, first in that group.

    The export's slice is re-appended at the end of the package's table, so no
    other export's slice moves.
    """
    pkg = builder.pkg
    meta = bytearray(pkg.exports[package_index - 1]['meta'])
    first = struct.unpack_from('<i', meta, 84)[0]
    counts = list(struct.unpack_from('<4i', meta, 88))
    groups, at = [], first
    for n in counts:
        groups.append(list(pkg.preload[at:at + n]) if first >= 0 else [])
        at += n
    if target in groups[group]:
        return
    groups[group].insert(0, target)
    struct.pack_into('<i', meta, 84, len(pkg.preload))
    for g in groups:
        pkg.preload.extend(g)
    struct.pack_into('<4i', meta, 88, *[len(g) for g in groups])
    pkg.preload_count = len(pkg.preload)
    pkg.exports[package_index - 1]['meta'] = bytes(meta)


def null_preloads(builder):
    """Names of the exports whose preload list holds a null entry.

    No shipped level has one (134,992 entries over 31 maps), and the loader indexes
    its node table with every entry.
    """
    pkg, bad = builder.pkg, []
    for i, e in enumerate(pkg.exports):
        first = struct.unpack_from('<i', e['meta'], 84)[0]
        n = sum(struct.unpack_from('<4i', e['meta'], 88))
        if first >= 0 and 0 in pkg.preload[first:first + n]:
            bad.append(levelbuild._name_of(pkg, i))
    return bad


def _child_named(builder, actor_index, want):
    """The package index of the component called `want` under this actor.

    Needed because a force field door's size is NOT on the actor: it is
    `RelativeScale3D` on its `DoorMesh` component. `builder.place(scale=...)` cannot
    be used for it, because that writes the scale to the actor's root AND to every
    component that has the property -- and a child's scale multiplies its parent's,
    so the mesh would come out squared.
    """
    pkg = builder.pkg
    for i, e in enumerate(pkg.exports):
        if struct.unpack_from('<i', e['meta'], levelbuild.OUTER_AT)[0] != actor_index:
            continue
        ni = struct.unpack_from('<i', e['meta'], 16)[0]
        name = pkg.names[ni][0] if 0 <= ni < len(pkg.names) else ''
        if name == want:
            return i + 1
    return None


#: Root scale axes that differ by more than this count as a non-uniform scale.
NONUNIFORM_SCALE = 1e-4
#: A component scale closer than this to the one it already has is left alone.
SCALE_WRITE_TOLERANCE = 1e-5


def carry_scale(builder, actor, cls):
    """Give each component of a placed Blueprint the world scale the Map Editor draws.

    The editor composes a piece's parts as matrices (root times part); the engine
    composes FTransforms, whose scale multiplies per axis in each component's own frame.
    Under a non-uniformly scaled root a turned part is scaled along the wrong axes
    (ArchwayGate_1x1x4_RB_NoColumn_C at (1, 0.667, 1): top, brackets and sill 0.333 out).
    Each component's RelativeScale3D becomes its matrix-composed world scale over its
    parent's, sign kept. Does nothing for a uniform root scale. Returns how many were set.
    """
    from riftlord.formats.omd3 import omd3level
    from riftlord.formats.omd3 import omd3templates
    root = builder._root_component(actor)
    if not root:
        return 0
    pkg = builder.pkg
    names = [n for n, _h in pkg.names]

    def fields(index):
        d = pkg.exports[index - 1]['data']
        return d, {n: (k, at, sz) for n, k, at, sz in _properties(d, names)}

    def vec(d, p, field):
        return list(struct.unpack_from('<3f', d, p[field][1])) if field in p else None

    try:
        templates = omd3templates.templates(cls)
    except Exception:                                # noqa: BLE001
        templates = {}
    comps = {}
    for i, e in enumerate(pkg.exports):
        if struct.unpack_from('<i', e['meta'], levelbuild.OUTER_AT)[0] != actor:
            continue
        d, p = fields(i + 1)
        if not any(f in p for f in ('AttachParent', 'RelativeLocation')) and i + 1 != root:
            continue
        name = levelbuild._name_of(pkg, i).replace(omd3templates.GEN_SUFFIX, '')
        t = templates.get(name) or {}
        parent = (struct.unpack_from('<i', d, p['AttachParent'][1])[0]
                  if 'AttachParent' in p and p['AttachParent'][2] == 4 else 0)
        comps[i + 1] = {'parent': parent,
                        'loc': vec(d, p, 'RelativeLocation') or t.get('loc') or [0.0] * 3,
                        'rot': vec(d, p, 'RelativeRotation') or t.get('rot') or [0.0] * 3,
                        'scale': vec(d, p, 'RelativeScale3D') or t.get('scale') or [1.0] * 3}
    if root not in comps:
        return 0
    rs = [abs(v) for v in comps[root]['scale']]
    if max(rs) - min(rs) <= NONUNIFORM_SCALE:
        return 0
    world, lengths = {}, {}

    def resolve(k, depth=0):
        if k in world:
            return world[k]
        c = comps[k]
        own = omd3level._matrix(c['loc'], c['rot'], c['scale'])
        up = c['parent'] if k != root and c['parent'] in comps and depth < 32 else 0
        world[k] = omd3level._matmul(resolve(up, depth + 1), own) if up else own
        lengths[k] = [math.sqrt(sum(world[k][r][col] ** 2 for r in range(3)))
                      for col in range(3)]
        return world[k]

    written = 0
    for k in comps:
        resolve(k)
    for k, c in comps.items():
        up = c['parent']
        if k == root or up not in comps:
            continue
        want = [math.copysign(lengths[k][a] / lengths[up][a], c['scale'][a])
                if lengths[up][a] > 1e-9 else c['scale'][a] for a in range(3)]
        if all(abs(w - s) <= SCALE_WRITE_TOLERANCE for w, s in zip(want, c['scale'])):
            continue
        builder.set_vector(k, 'RelativeScale3D', want)
        written += 1
    return written


def _compound_donor(item):
    """A compound member's own donor (riftlord/compounds.py), or None."""
    from riftlord.mapeditor import compounds
    return compounds.donor(item)


def _add(builder, cls, at, rename, rotation, report, scale=None, donor=None):
    """Lift one actor and place it. Returns its package index, or None.

    `rotation` is a yaw in degrees or a full (pitch, yaw, roll) rotator; `scale`, when
    given, is written to the root too. `donor` (package, export) replaces the class's
    catalogue donor."""
    donor = donor or donor_for(cls)
    if not donor:
        report['skipped'].append('no donor for %s' % cls)
        return None
    try:
        index = builder.lift_actor(donor[0], donor[1], rename=rename)
    except Exception as exc:                         # noqa: BLE001
        report['skipped'].append('%s could not be lifted: %s' % (cls, exc))
        return None
    # `place_whole`, because a donor root that shipped unturned has no
    # RelativeRotation to overwrite (interior door, spawner, rift).
    rotator = (tuple(float(v) for v in rotation)
               if isinstance(rotation, (list, tuple))
               else (0.0, float(rotation or 0.0), 0.0))
    place_whole(builder, index, location=tuple(at), rotation=rotator,
                scale=tuple(float(v) for v in scale) if scale is not None else None)
    return index


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--level', required=True)
    args = ap.parse_args(argv)
    from riftlord.build.navigation import navpatch
    files = navpatch.fetch_map(navpatch.map_path(args.level),
                               gamepath.beside_code(gamepath.CODE_ROOT, '.applytest'))
    with open(files['.umap'], 'rb') as fh:
        ua = fh.read()
    with open(files['.uexp'], 'rb') as fh:
        ux = fh.read()
    out_a, out_x, report = apply_plan(
        ua, ux, args.level, '/Game/Mods/Try/Maps/TryMap', 'TryMap')
    print('wrote %d + %d bytes' % (len(out_a), len(out_x)))
    for key in ('rifts', 'doors', 'spawners', 'points', 'branches', 'added',
                'moved', 'parked'):
        print('   %-10s %d' % (key, report[key]))
    for line in report.get('notes') or []:
        print('   note: %s' % line)
    for line in report['skipped']:
        print('   skipped: %s' % line)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
