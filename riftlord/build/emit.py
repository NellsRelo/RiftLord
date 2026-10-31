"""Turn a Rift Lord plan into a custom-mission mod pak.

    python3 -m riftlord.emit --level Scenario_05 --name "Split Stairs Redux"
    python3 -m riftlord.emit --level Scenario_05 --install

**Nothing shipped is touched.** The mission gets its **own copy** of the level under
`/Game/Mods/<mod>/Maps/`, its own onslaught coordinator, and its own MissionProtos and
CampaignProtos cloned from the shipped tables and stripped to its own rows. The stock
tables are left alone, so a mod that patches them still works beside this one --
`makemod` does that part and has done since the converter needed it.

A mission built from a DLC map keeps that map's entitlement, so a copy of Canyon Keep
stays gated behind the DLC that ships Canyon Keep.
"""

import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

from riftlord.core import gamepath  # noqa: E402
from riftlord.build import makelevel  # noqa: E402
from riftlord.build import makemod  # noqa: E402
from riftlord.build import missionproto  # noqa: E402
from riftlord.build.navigation import navpatch
from riftlord.build.navigation import navvolinject  # noqa: E402
from riftlord.formats.ue import pakv4  # noqa: E402

from riftlord.mapeditor import leveldata  # noqa: E402
from riftlord.build.apply import used_spawner_indices  # noqa: E402

#: What the plan can ask for that the emitter does not do yet. Reported rather than
#: ignored: a plan that half-applied without saying so is worse than one that did not.
#: Nothing in a plan is left unapplied any more. Kept as the place to name anything that
#: becomes possible to author before it becomes possible to write.
NOT_YET = {}

#: Point the level copy's own MapData import at the mission's MapData copy too.
MAPDATA_LEVEL_RETARGET = False
#: Keep the source level's MapData for the mission (no copy) unless a redraw needs one.
MAPDATA_SHIPPED = False


def pak_stem(title):
    """A mission name as a file name: spaces become underscores, commas go.

    Anything else a file name cannot carry goes the same way as a comma, and a run of
    separators collapses, so "Split Stairs, Redux!" is `Split_Stairs_Redux`.
    """
    import re
    text = re.sub(r"[,'\"!?:;.]+", '', str(title or '').strip())
    text = re.sub(r'[^A-Za-z0-9]+', '_', text).strip('_')
    return text or 'RiftLordMission'


def mod_name(level_name, title=None):
    """The mod folder, and so the asset paths, for one mission.

    Taken from the mission's NAME, not from the level: derived from the level, two
    missions authored on the same map both land on `/Game/Mods/RiftLord<Level>/`, so the
    second pak installed silently replaces the first's map while both still sit in
    `~mods` looking installed.

    No underscores: `makemod.verify` recovers the mod from `mission_row.split('_')[0]`,
    so `RiftLord_Scenario05` reads back as `RiftLord` and the check looks for tables that
    are not there.
    """
    stem = pak_stem(title) if title else ''
    if not title or stem == 'RiftLordMission':
        stem = level_name
    return 'RiftLord%s' % stem.replace('_', '')


def path_checks(pak_path, map_name):
    """[{name, ok, detail}] on the path graph of a pak that has just been written.

    Read from the finished pak, because two faults here hang the game on the loading
    screen while every structural check passes:

    * **a duplicate `PointIndex`** -- a branch names its target by that number alone, so
      two points sharing one make the graph ambiguous;
    * **a route that never reaches an exit point**, whether it dead-ends or loops.

    Both happened: new points numbered from zero against North Wing's existing 1..6.
    """
    from collections import defaultdict

    from riftlord.build.navigation import navpatch
    from riftlord.onslaught import onslaught
    navpatch.open_pak(pak_path)
    try:
        rep = onslaught.map_report(map_name)
        pts, spawners = rep['points'], rep['spawners']
        from riftlord.mapeditor import leveldata
        rifts = leveldata.level(map_name).get('rifts') or []
    finally:
        navpatch.close_pak(pak_path)

    out = []
    seen = defaultdict(list)
    for name, rec in pts.items():
        if isinstance(rec.get('index'), int):
            seen[rec['index']].append(name)
    dupes = {i: v for i, v in seen.items() if len(v) > 1}
    #: A clash matters when WE would name that index, not whenever one exists.
    #:
    #: ScenarioDLC_04 ships two points both at index 8 and plays perfectly, so an
    #: empty plan could not be saved over a level the game is happy with. What the
    #: invariant protects is a BRANCH, which names its target by an index: ours are
    #: allocated above the level's highest, and the one shipped index we ever name is
    #: the exit's, because an added route hands off to it. So a clash is a refusal when
    #: it involves a point we added or the exit point, and a note otherwise.
    exit_indices = {r.get('index') for n, r in pts.items() if r.get('exit')}
    ours = {i: v for i, v in dupes.items()
            if i in exit_indices or any(str(n).startswith('RiftLord') for n in v)}
    theirs = {i: v for i, v in dupes.items() if i not in ours}
    out.append({'name': 'every path point has a unique index', 'ok': not ours,
                'detail': ('clashes on an index we name: %s' % ours) if ours
                          else '%d points%s' % (len(pts),
                               ', all distinct' if not theirs else
                               '; the level ships %s at one index, which no branch of '
                               'ours names' % sorted(theirs.values()))})

    # One `OMDAIPath` per level, listing every point. Northern Rampart -- the only
    # shipped level with a flyer route -- has one holding all 26 of its points, ground and
    # flyer together, and a branch's PointIndex is unique across the level. Splitting a
    # route onto a second path left its hand-off to the shared exit naming a point its own
    # path did not contain.
    from riftlord.onslaught import onslaught as _ons
    navpatch.open_pak(pak_path)
    try:
        info, uexp, hdr = _ons.load_map(map_name)
        paths = []
        for e in info['exports']:
            if 'AIPath' in str(e.get('class') or e.get('cls')):
                pr = {x[0]: x for x in _ons.read_props(info, uexp, hdr, e)}
                raw = pr.get('PathPoints')
                listed = struct.unpack_from('<i', raw[2], 0)[0] if raw else 0
                paths.append((str(e.get('name')), listed))
    finally:
        navpatch.close_pak(pak_path)
    #: SOME path actor must list every point -- not "there is exactly one path actor".
    #:
    #: Those are different claims, and only the first is the invariant. Scenario_15
    #: ships TWO path actors that each list all 33 of its points, so counting actors
    #: refused a level the game ships working. The failure this guards against is our
    #: points landing in a path of their own, and that still fails here: the level's own
    #: path would then list only the level's own points, ours would be extra, and NO
    #: single path would hold them all.
    biggest = max([n for _nm, n in paths] or [0])
    out.append({'name': 'some path actor holds every point',
                'ok': bool(paths) and biggest >= len(pts) - 1,
                'detail': '%d path(s): %s, biggest holds %d of %d points'
                          % (len(paths), ', '.join('%s=%d' % x for x in paths),
                             biggest, len(pts))})

    # Every shipped level has ONE rift and ONE exit point under it, inside the rift's
    # RiftEnterArea -- Ice_01's flyer route shares the ground routes' exit. A mission with
    # more rifts gives each its own exit point (planactors.rift_exits), so there are as many
    # exits as rifts in play.
    from riftlord.build.apply import PARKED
    from riftlord.mapeditor import planactors
    live = [r for r in rifts
            if not (r.get('loc') and abs(r['loc'][2] - PARKED[2]) < 1.0)]
    exits = [n for n, r in pts.items() if r.get('exit')]
    out.append({'name': 'each rift in play has one exit point',
                'ok': len(exits) == max(1, len(live)),
                'detail': '%d exit(s) for %d rift(s): %s'
                          % (len(exits), len(live), ', '.join(exits) or 'none')})
    #: With two rifts or more, each exit stands inside a rift of its own, or enemies on
    #: its routes stop short of any rift. One rift keeps it as a note.
    unpaired, taken = [], set()
    for n in exits:
        at = pts[n].get('location')
        hit = next((r.get('name') for r in live if r.get('name') not in taken and at
                    and planactors.in_rift(at, r['loc'])), None)
        if hit is None:
            unpaired.append(n)
        else:
            taken.add(hit)
    out.append({'name': 'each exit point stands inside a different rift' if len(live) > 1 else
                        'the exit point stands inside the rift (a note)',
                'ok': not unpaired or len(live) <= 1,
                'detail': ('outside every rift (or sharing one): %s' % ', '.join(unpaired))
                          if unpaired else '%d paired' % len(exits)})

    # The coordinator dispatches on indexInLevel, so two spawners sharing one means the
    # wrong spawner fires -- or both do.
    per_index = defaultdict(list)
    for sp in spawners:
        per_index[sp.get('index')].append(sp.get('export') or '?')
    clash = {i: v for i, v in per_index.items() if len(v) > 1}
    out.append({'name': 'every spawner has a unique indexInLevel', 'ok': not clash,
                'detail': ('clashes: %s' % clash) if clash
                          else '%d spawners: %s' % (len(spawners),
                                                   sorted(per_index))})

    # Some rift is in play. A parked one does not count: parking the host's rift and
    # placing your own is the supported way to move it, and the parked actor is still in
    # the level. Two or more each have their own exit point (checked above): the game
    # takes enemies in at any rift and counts every leak against one rift point pool.
    parked = len(rifts) - len(live)
    out.append({'name': 'a rift is in play', 'ok': len(live) >= 1,
                'detail': '%d in play%s: %s'
                          % (len(live), ' (%d parked)' % parked if parked else '',
                             ', '.join(r.get('name') or '?' for r in live) or 'none')})

    # Every soft reference a spawner holds must actually resolve. An FSoftObjectPath's
    # asset path is `<package>.<asset>`; the bare package path resolves to nothing, and
    # it does so SILENTLY -- the property reads as null, the controller falls back to
    # the level's first start point, and the unit sets off down the host map's route.
    # Every report we had showed only the sub-path, so this looked correctly wired
    # through three test builds while four hellbat wisps sat on the spawner.
    navpatch.open_pak(pak_path)
    try:
        info, uexp, hdr = _ons.load_map(map_name)
        exports = {str(e.get('name')) for e in info['exports']}
        bad = []
        n_refs = 0
        for e in info['exports']:
            if 'AISpawner' not in str(e.get('class') or e.get('cls')):
                continue
            pr = {x[0]: x for x in _ons.read_props(info, uexp, hdr, e)}
            for key in ('StartingDoor', 'StartingPathPoint',
                        'StartingFlyerPathPoint'):
                if key not in pr:
                    continue
                n_refs += 1
                asset, sub = _ons.soft_path(info, pr[key][2])
                where = '%s.%s' % (str(e.get('name')), key)
                tail = (asset or '').rsplit('/', 1)[-1]
                if '.' not in tail:
                    bad.append('%s: asset path has no .Asset suffix (%s)'
                               % (where, asset))
                elif not tail.startswith(map_name + '.'):
                    bad.append('%s: names %s, not this map' % (where, asset))
                target = (sub or '').split('.')[-1]
                if target and target not in exports:
                    bad.append('%s: sub-path names %s, which is not an export'
                               % (where, target))
    finally:
        navpatch.close_pak(pak_path)
    out.append({'name': "every spawner's soft references resolve",
                'ok': not bad,
                'detail': '; '.join(bad) if bad
                          else '%d reference(s), all naming this map' % n_refs})

    # A soft reference's target must survive being turned back into an FName.
    #
    # An FName is (string, number) and UE builds one from a soft path's TEXT by
    # splitting a trailing `_<digits>` into the number. So an export stored literally
    # as `RiftLordPoint_0_0` is looked up as (`RiftLordPoint_0`, 1) and never found --
    # the reference resolves to null with no error, the controller falls back to the
    # level's first start point, and the unit walks the host map's route. That is the
    # SECOND way this one property failed silently, after the missing `.Asset` suffix,
    # and neither was visible in any report because both spellings print identically.
    #
    # Shipped levels never name an actor this way: `OMDPathPoint20`, `Door01`,
    # `MobSpawnerDoor2` -- digits with no underscore before them.
    navpatch.open_pak(pak_path)
    try:
        info, uexp, hdr = _ons.load_map(map_name)
        table = set(info['names'])
        unsafe = []
        checked = 0
        for e in info['exports']:
            if 'AISpawner' not in str(e.get('class') or e.get('cls')):
                continue
            pr = {x[0]: x for x in _ons.read_props(info, uexp, hdr, e)}
            for key in ('StartingDoor', 'StartingPathPoint',
                        'StartingFlyerPathPoint'):
                if key not in pr:
                    continue
                _a, sub = _ons.soft_path(info, pr[key][2])
                target = (sub or '').split('.')[-1]
                if not target:
                    continue
                checked += 1
                head, sep, tail = target.rpartition('_')
                if sep and tail.isdigit() and target in table:
                    unsafe.append('%s.%s -> %s, which FName re-reads as (%s, %d)'
                                  % (str(e.get('name')), key, target,
                                     head, int(tail) + 1))
    finally:
        navpatch.close_pak(pak_path)
    out.append({'name': 'soft-referenced names survive FName splitting',
                'ok': not unsafe,
                'detail': '; '.join(unsafe) if unsafe
                          else '%d target(s), none ending in _<digits>' % checked})

    # A doorless spawner must switch spawn invulnerability off.
    #
    # `bInvulnerableOnSpawn` defaults to TRUE and is ended by the unit passing through
    # the spawner's door. With no door it never ends: traps do not register the unit
    # and the rift does not consume it. Exact across every shipped spawner, 98 of 98 --
    # the 87 with a door leave the default, the 11 without set it false.
    navpatch.open_pak(pak_path)
    try:
        info, uexp, hdr = _ons.load_map(map_name)
        bad_invuln = []
        for e in info['exports']:
            if 'AISpawner' not in str(e.get('class') or e.get('cls')):
                continue
            pr = {x[0]: x for x in _ons.read_props(info, uexp, hdr, e)}
            if 'StartingDoor' in pr:
                continue
            v = pr.get('bInvulnerableOnSpawn')
            if v is None or bool(v[2]):
                bad_invuln.append('%s has no door and %s'
                                  % (str(e.get('name')),
                                     'leaves bInvulnerableOnSpawn at its default'
                                     if v is None else 'sets it true'))
    finally:
        navpatch.close_pak(pak_path)
    out.append({'name': 'a doorless spawner is not invulnerable',
                'ok': not bad_invuln,
                'detail': '; '.join(bad_invuln) if bad_invuln
                          else 'every doorless spawner sets it false'})

    # EVERY ADDED ACTOR MUST SIT INSIDE THE NAVPOWER GRAPH.
    #
    # A copied level keeps the donor's navgraph -- North Wing's is 42,184 bytes and
    # covers min=(-4359, -1282, -529) max=(1407, 7810, 1176). NavPower moves, orients
    # and speed-scales anything inside that volume, for free, correctly. Outside it
    # there is no graph, so NavPowerMoveTo cannot path and nothing moves.
    #
    # A flyer spawner was placed at y=-5616, more than 4,000uu beyond the graph's
    # southern edge, and five of its nine points with it. Everything that followed --
    # the hand-written mover, the facing that would not hold, the speed scaling that
    # went wrong -- was a consequence of authoring outside coverage, not of the game
    # lacking a system.
    navpatch.open_pak(pak_path)
    try:
        from riftlord.build.navigation import navinfo
        graph = navinfo.omd3_map(map_name, gamepath.scratch('.navcheck', gamepath.CODE_ROOT))
        rep2 = _ons.map_report(map_name)
    finally:
        navpatch.close_pak(pak_path)
    if not graph:
        out.append({'name': 'added actors sit inside the navgraph', 'ok': False,
                    'detail': 'this level has no NavPower graph at all'})
        # Not reachable today -- every shipped level has a surface graph -- but if it
        # ever is, the same split applies: flight is authorable and ground is not.
    else:
        lo, hi = graph['min'], graph['max']

        def inside(p):
            return all(lo[i] - 1.0 <= p[i] <= hi[i] + 1.0 for i in range(3))
        # Ground and flight are no longer the same question.
        #
        # This invariant was written when NO navigation data could be authored, so
        # anything outside the shipped graph was unreachable and placing it was a
        # mistake. That is still true of a GROUND route: the walkable graph's record
        # layout is not cracked and cannot be generated.
        #
        # A FLYER route is now different. Flyers use the volumetric graph, not this
        # one, and a volumetric graph can be written -- fitted to an arbitrary route,
        # checksum and all (navvolinject.py). So a flyer point outside the surface
        # bbox is expected and must not block a build; blocking it stopped the one
        # kind of route this tool exists to add.
        #: ADDED actors only, which is what this check has always claimed to be.
        #:
        #: It iterated every actor in the level, so a SHIPPED point or spawner sitting
        #: outside the graph's bounding box refused the build -- on Scenario_07
        #: (OMDPathPoint8) and Scenario_13 (MobSpawnerDoor1) an EMPTY plan could not be
        #: saved at all, blamed on an actor the user never touched. The box is the
        #: graph's extent, not its shape, and a shipped actor just outside it is
        #: ordinary: those levels play. Everything this check was written for is
        #: something WE placed, and the emitter names all of that `RiftLord*`. A
        #: shipped actor outside the box is still reported, as a note that passes.
        mine = lambda nm: str(nm or '').startswith('RiftLord')
        ground, flight, theirs = [], [], []
        for n, r in rep2['points'].items():
            if r.get('location') and not inside(r['location']):
                if not mine(n):
                    theirs.append(n)
                else:
                    (flight if r.get('flyersOnly') else ground).append(n)
        for sp in rep2['spawners']:
            if sp.get('location') and not inside(sp['location']):
                if not mine(sp['export']):
                    theirs.append(sp['export'])
                else:
                    (flight if sp.get('flyer') else ground).append(sp['export'])
        # Exact: on a walkable polygon of the written graph, not inside its box.
        from riftlord.build.navigation import navsurf
        from riftlord.build.navigation import navsurfextend
        navpatch.open_pak(pak_path)
        try:
            surf = navsurf.buffer(map_name)
        finally:
            navpatch.close_pak(pak_path)
        floor = ([s['verts'] for s in navsurf.sections(surf, navsurf.graphs(surf)[0])]
                 if surf else [])
        ground = []
        for n, r in rep2['points'].items():
            if mine(n) and r.get('location') and not r.get('flyersOnly') \
                    and navsurfextend.on_surface(floor, r['location']) is None:
                ground.append('%s at (%.0f, %.0f, %.0f)' % ((n,) + tuple(r['location'])))
        for sp in rep2['spawners']:
            if mine(sp['export']) and sp.get('start') and sp.get('location') \
                    and navsurfextend.on_surface(floor, sp['location']) is None:
                ground.append('%s at (%.0f, %.0f, %.0f)'
                              % ((sp['export'],) + tuple(sp['location'])))
        out.append({
            'name': 'added GROUND actors sit inside the navgraph',
            'ok': not ground,
            'detail': ('off the walkable floor: %s. Enemies cannot walk there. Move '
                       'these onto floor the shipped level\'s enemies walk on, or onto the '
                       'route of a ground spawner so the floor can be extended to them'
                       % '; '.join(sorted(ground))) if ground
                      else '%d actor(s) checked against (%.0f %.0f %.0f)..'
                           '(%.0f %.0f %.0f)'
                           % (len(rep2['points']) + len(rep2['spawners']),
                              lo[0], lo[1], lo[2], hi[0], hi[1], hi[2])})
        if theirs:
            out.append({
                'name': "the level's shipped actors outside the graph box (expected)",
                'ok': True,
                'detail': ', '.join(sorted(theirs))})
        if flight:
            out.append({
                'name': 'flyer actors outside the surface graph (expected)',
                'ok': True,
                'detail': ('%s; their flight area is built from the route'
                           % ', '.join(sorted(flight)))})

    #: a point with no stored PointIndex has the class default, 0
    by_index = {}
    for name, rec in pts.items():
        index = rec['index'] if isinstance(rec.get('index'), int) else 0
        by_index.setdefault(index, name)
    for sp in spawners:
        for which in ('start', 'flyer'):
            ref = sp.get(which)
            if not ref:
                continue
            # Every branch is followed: a route may loop, as long as some way on
            # reaches the exit.
            first = ref.split('.')[-1]
            walked, todo, ok, why = [], [first], False, 'no branch leads to the exit'
            while todo:
                here = todo.pop(0)
                if here in walked:
                    continue
                walked.append(here)
                rec = pts.get(here)
                if rec is None:
                    if here == first:
                        why = '%s is not in the level' % here
                    continue
                if rec.get('exit'):
                    ok = True
                    why = 'exit within %d point(s)' % len(walked)
                    break
                todo.extend(n for n in (by_index.get(b.get('PointIndex'))
                                        for b in rec.get('next') or []) if n)
            #: Refuses only a route with a point we added; a wholly shipped route is
            #: reported as a note.
            mine = (str(sp.get('export') or '').startswith('RiftLord')
                    or any(str(n).startswith('RiftLord') for n in walked))
            out.append({'name': 'spawner %d %s route ends at an exit'
                                % (sp['index'], which) if mine else
                                "shipped spawner %d %s route (not ours)"
                                % (sp['index'], which),
                        'ok': ok or not mine,
                        'detail': why})
    return out


def flow_checks(pak_path, coord_object):
    """[{name, ok, detail}] -- does the coordinator terminate for every (wave, spawner)?

    Simulated, not inferred. The structural checks all passed on a coordinator whose
    cloned block ran straight back into the chain that dispatched to it, and the game hung
    on the loading screen with the CPU pinned. `flowsim` steps the bytecode the way the
    game does and says where it loops.
    """
    from riftlord.build.navigation import navpatch
    from riftlord.onslaught import flowsim
    navpatch.open_pak(pak_path)
    try:
        bad = flowsim.survey(coord_object)
    except Exception as exc:                             # noqa: BLE001
        return [{'name': 'coordinator terminates for every spawner', 'ok': False,
                 'detail': 'could not be simulated: %s: %s'
                           % (type(exc).__name__, exc)}]
    finally:
        navpatch.close_pak(pak_path)
    return [{'name': 'coordinator terminates for every spawner', 'ok': not bad,
             'detail': ('loops at ' + ', '.join('wave %d spawner %d' % (w, s)
                                                for w, s, _h, _n, _t in bad[:4]))
                       if bad else 'every wave and spawner ends'}]


def closes_checks(level_name, files, report):
    """[{name, ok, detail}] for the close being reachable from every spawner."""
    coord = report.get('coordinator')
    if not coord:
        return []
    try:
        from riftlord.onslaught import wavespec
        from riftlord.onslaught import onslaught
        spawners = {sp['index'] for sp in onslaught.map_report(level_name)['spawners']}
        for sp in (report.get('spawner_arms') or {}):
            spawners.add(int(sp))
        return wavespec.closes_for_every_spawner(files, coord, spawners)
    except Exception as exc:                             # noqa: BLE001
        return [{'name': 'close reachable from every spawner', 'ok': False,
                 'detail': 'could not be checked: %s: %s' % (type(exc).__name__, exc)}]


def keep_coordinator(pkg, names, resolve):
    """A mutate that changes nothing, so the coordinator is copied as it stands.

    Rift Lord must pass SOMETHING: given `mutate=None`, `makemod.build` falls back to
    `makelevel.author_waves(fill='Kobold', last='LizardmanRanger')` and rewrites every
    unit in the onslaught. Measured -- a mission emitted with no coordinator edit played
    22/27/33/39 **Kobolds** and a fifth wave of 39 Lizardman Rangers instead of North
    Wing's own waves.
    """
    return None


def spawner_arms(level_name, plan, only_waves=None):
    """(a mutate for the coordinator, what it will add) for the plan's new spawners.

    (None, {}) when the plan adds none, so a plan that only moves geometry leaves the
    coordinator byte-for-byte as the source's.
    """
    wanted = {}
    for sp in plan.get('spawners') or []:
        index = sp.get('index')
        if not index:
            continue
        wanted[int(index)] = 'flyer' if sp.get('kind') == 'flyer' else 'ground'
    if not wanted:
        return None, {}
    from riftlord.onslaught import wavespec
    from riftlord.onslaught import onslaught
    _path, object_name = makelevel.coordinator_of(level_name)
    have = set(wavespec.shape(object_name)['spawners'])
    fresh = {i: k for i, k in wanted.items() if i not in have}
    if not fresh:
        return None, {}
    return (wavespec.add_spawner_arms(object_name, fresh, onslaught.protos(),
                                      only_waves=only_waves),
            {str(i): k for i, k in sorted(fresh.items())})


def native_campaign_for(level_name):
    """The campaign row that already lists this map's mission, or None.

    Wrapped so a map with no shipped mission (nothing to inherit a campaign from) falls
    back to the mod's own campaign rather than failing the build.
    """
    try:
        return makemod.native_campaign_for(level_name)
    except Exception:                                # noqa: BLE001
        return None


def war_scenario(level_name):
    """Whether the source map's own mission is flagged a War Scenario."""
    try:
        table = missionproto.read()
    except Exception:                                # noqa: BLE001
        return False
    want = level_name.replace('_', '')
    for row in table.rows:
        if row['name'].replace('_', '').startswith(want) and '_WS' in row['name']:
            return True
    return False


def plan_war(plan, level_name):
    """The mission's War Scenario flag: the plan's `world.warScenario` when it states
    one, else the source map's."""
    want = ((plan or {}).get('world') or {}).get('warScenario')
    return war_scenario(level_name) if want is None else bool(want)


def set_war_flag(files, war):
    """`files` with bIsWarScenario set on the build's mission row. Its Endless twin is
    always cleared: no shipped Endless row is a War Scenario."""
    import re
    key, _slot = makemod.mission_table_key(files)
    if key is None:
        return files
    from riftlord.missions.customcampaign import _table
    t = _table(files[key], files[key[:-7] + '.uexp'])
    for row in t.rows:
        field = t.field(row, 'bIsWarScenario')
        if field is not None:
            t.set_bool(field, war and not row['name'].endswith(makemod.ENDLESS_TWIN))
    t.export['data'] = t.serialise()
    out = dict(files)
    out[key], out[key[:-7] + '.uexp'] = t.pkg.write()
    return out


def build(level_name, mod=None, title=None, campaign='Custom Maps', par=600.0,
          plan=None, arms=False, arm_waves=None, into_campaign=False,
          placement=None, guids=None, endless=False, load_picture=None,
          campaign_name=None, portal_picture=None, scramble=None, into_index=None,
          endless_only=False, unlocks=None):
    """(files for the pak, a report) for one plan. Nothing shipped is modified.

    Its mission table always goes in its own table folder (makemod.slot_for), which
    OrcsMustUI registers from the pak itself. By default a custom map, in no campaign, reached from
    OrcsMustUI's Custom Maps tab, so the pak patches no file another mod also
    ships. `placement` is a customcampaign placement: a custom campaign is listed
    by write(), after the pak exists; a shipped campaign row patches the shipped
    CampaignProtos. `into_campaign` means the source map's own campaign.
    `guids` keeps a previous build's mission Guids (makemod.previous_guids).
    `load_picture` is the loading-screen picture, {'path', 'crop'} (loadscreen.py).
    `campaign_name` names a new campaign `placement` creates, for the loading screen.
    `portal_picture` is the hub gate's picture, {'path', 'crop'} (hubportal.py).
    `into_index` is the mission's place in a shipped campaign (makemod.build).
    `scramble` is the Scramble tiers the mission joins (missionscramble.clean).
    `unlocks` is the heroes and items beating it grants (missionunlocks.clean).
    `endless_only` lists the mission in Endless alone (makemod.build)."""
    plan = plan if plan is not None else leveldata.load_plan(level_name)
    title = title or ('%s (Rift Lord)' % level_name)
    mod = (mod or mod_name(level_name, title)).replace('_', '')
    # Only the PERSISTENT level is copied, and its sublevel references are left
    # alone so the art streams from the shipped packages. That is not a shortcut: a
    # level's gameplay actors -- rift, spawners, doors, path points -- all live in the
    # persistent level, so our copy owns everything Rift Lord edits while the castle,
    # lighting and foliage stay shared with the original.
    from riftlord.build import apply as applier
    #: a level from scratch (riftlord/scratch.py)
    from riftlord.mapeditor import scratch as scratchmod
    new_level = scratchmod.is_scratch(plan)
    if new_level:
        plan = scratchmod.prepare(plan)
    #: the mission's own assets (its lava rock materials, riftlord.lavafx; the Mission
    #: Events piece, riftlord.missionevents) live under its mod folder
    plan = dict(plan, _modKey=mod)
    #: the level pieces a placed Crystal stands in place of, parked as removed pieces
    #: (riftlord.mapeditor.crystalpark; a plan without a Crystal is returned as it is)
    from riftlord.mapeditor import crystalpark
    plan = crystalpark.with_parked(level_name, plan)
    md_path = tex_path = None
    #: a redrawn minimap (riftlord/minimapredraw.py): {volume, reframe, ...} once drawn
    redraw = {}

    def write_plan_in(uasset, uexp, level_path, object_name):
        from riftlord.core import buildstage
        buildstage.stage('Level')
        out_a, out_x, got = applier.apply_plan(
            uasset, uexp, level_name, level_path, object_name, plan)
        if new_level:
            out_a, out_x, got['scratch'] = scratchmod.install_surface(out_a, out_x, plan)
            out_a, out_x, _n = scratchmod.retarget_map_data(
                out_a, out_x, level_name, md_path, tex_path)
            # the HUD places its dots by the level's map volume, so it frames the
            # same square the minimap is drawn on
            out_a, out_x, _n = scratchmod.frame_volume_actors(
                out_a, out_x, level_name, scratchmod.volume_for(plan))
        elif redraw or (MAPDATA_LEVEL_RETARGET and md_path):
            out_a, out_x, _n = scratchmod.retarget_map_data(
                out_a, out_x, level_name, md_path, tex_path)
            if redraw.get('reframe'):
                out_a, out_x, got['minimapActors'] = scratchmod.frame_volume_actors(
                    out_a, out_x, level_name, redraw.get('volumes') or redraw['volume'])
        # A FLYER ROUTE GETS ITS NAVGRAPH AUTOMATICALLY.
        #
        # Flyers use the volumetric graph, and a level that ships none cannot move
        # them however well the route is authored -- that was the measured dead end
        # for months. The graph is now GENERATED FROM NOTHING (`navvolgen`) rather
        # than edited into a donor buffer, so the route can be any length and any
        # shape and nothing is inherited from some shipped map's bounds.
        #
        # ONE call, not one per spawner. A level has exactly one volumetric buffer,
        # so installing a graph per flyer spawner kept only the first and silently
        # dropped the rest -- and the second install failed outright, because the
        # old installer refused a level that already had a buffer.
        #
        # `install_flight` also keeps the level's OWN volumes, so adding a route to a
        # map that ships flight does not delete the routes it came with, and it
        # regenerates rather than merges anything it would have written itself, which
        # is what makes saving repeatedly idempotent.
        # The level's own exit point, so the corridor covers the route's LAST leg.
        # An added route ends by branching to the level's exit rather than declaring
        # one of its own, so that leg is never in the plan -- and a graph built only
        # from the plan's points can stop short of where the flyer is actually sent.
        exit_at = None
        try:
            want = applier.exit_point_index(level_name)
            if want is not None:
                for rec in (leveldata.level(level_name).get('points')
                            or {}).values():
                    if rec.get('index') == want and rec.get('location'):
                        exit_at = tuple(rec['location'])
                        break
        except Exception:                            # noqa: BLE001
            exit_at = None
        routes = navvolinject.route_from_plan(plan, exit_at=exit_at)
        got['flight'] = []
        got['flight_failed'] = []
        try:
            out_a, out_x, rows, note = navvolinject.install_flight(
                out_a, out_x, routes, clear=(level_name, plan))
        except Exception as exc:                     # noqa: BLE001
            got['flight'].append('no flight graph: %s: %s'
                                 % (type(exc).__name__, exc))
            if routes:
                got['flight_failed'].append('no flight area for the flyer route: %s' % exc)
        else:
            got['flight'].append(note)
            got['flight_failed'] += [n for n, ok in rows if not ok]
        # Ground routes off the walkable floor get floor added under them.
        from riftlord.mapeditor import planactors
        from riftlord.build.navigation import navsurfextend
        try:
            chains = planactors.ground_chains(
                plan, planactors.level_actors(level_name))
        except ValueError as exc:
            raise SystemExit('ground route: %s' % exc)
        # The walkable graph rebuilt from the final geometry (navregen.ENABLED).
        carved = []
        from riftlord.build.navigation import navregen
        regenerated = False
        if not new_level and navregen.wanted(level_name, plan):
            buildstage.stage('Navmesh')
            try:
                out_a, out_x, carved = navregen.install(
                    out_a, out_x, level_name, plan, log=lambda *_a: None)
            except ValueError as exc:
                raise SystemExit('walkable graph: %s' % exc)
            regenerated = True
        # Walkable floor under removed or moved floor pieces is cut out first.
        from riftlord.build.navigation import navsurfcarve
        if navsurfcarve.active() and not new_level and not regenerated:
            try:
                out_a, out_x, carved = navsurfcarve.carve_level(
                    out_a, out_x, level_name, plan, chains)
            except ValueError as exc:
                raise SystemExit('removed floor: %s' % exc)
        try:
            out_a, out_x, walk_notes = navsurfextend.extend(
                out_a, out_x, chains,
                names=planactors.ground_names(plan, planactors.level_actors(level_name)))
        except ValueError as exc:
            raise SystemExit('ground route: %s' % exc)
        got['walkable'] = carved + walk_notes
        # NavPower obstructions over placed walls and removed floor (navobstruct.py).
        from riftlord.build.navigation import navobstruct
        if not new_level:
            out_a, out_x, blocked = navobstruct.place(out_a, out_x, level_name, plan)
            got['walkable'] += blocked
        #: the Crystal's hitbox fitted to the final walkable graph, with the build's
        #: warnings (riftlord/mapeditor/crystalfit.py)
        from riftlord.mapeditor import crystalfit
        out_a, out_x = crystalfit.install(out_a, out_x, plan, got, object_name)
        return (out_a, out_x), got

    # Only the sublevels the plan actually edits are copied. Copying all of them would
    # double a level's worth of packages into every mod for no reason; copying none of
    # them is why moving a wall could not be applied at all.
    touched = {} if new_level else applier.sublevel_edits(level_name, plan)
    #: sublevels whose baked lighting the build writes (riftlord.lightbake)
    from riftlord.build.lighting import lightbake
    lightbake.decide(plan)
    if lightbake.active() and not new_level:
        from riftlord.core import buildstage
        buildstage.stage('Lightmap')
        lightbake.prepare(level_name, plan)          # riftlord.lightbake: BAKE only
        for tag in lightbake.sublevel_tags(level_name):
            touched.setdefault(tag, {'moved': [], 'parked': []})['lightbake'] = True
    #: `.ubulk` payloads of copied packages, by package path (makemod packs two blobs)
    bulk_files = {}
    root = '/Game/Mods/%s' % mod
    base = navpatch.map_path(level_name)
    remap, extra_files, sub_report = {}, {}, {}
    sub_packages = leveldata.level(level_name).get('subPackages') or {} if touched else {}
    #: the music the plan chooses: a copy of the `_Audio` sublevel (riftlord.levelmusic);
    #: edits to that sublevel's own sounds are written into the same copy
    music = None
    if not new_level:
        from riftlord.build.world import levelmusic
        music = levelmusic.copy(level_name, mod, plan)
    for tag, edits in sorted(touched.items()):
        if tag == 'persistent':
            continue                     # the persistent level is handled above
        old_path = sub_packages.get(tag) or '%s_%s' % (base, tag)
        new_path = '%s/Maps/%s_Map01/%s_Map01_%s' % (root, mod, mod, tag)
        music_report = {}
        if music is not None and tag == levelmusic.TAG:
            (sub_a, sub_x), music_report = music[2], music[3]
            music = None
        else:
            files_in = navpatch.fetch_map(old_path,
                                          gamepath.scratch('.subcopy', gamepath.CODE_ROOT))
            with open(files_in['.umap'], 'rb') as fh:
                sub_a = fh.read()
            with open(files_in['.uexp'], 'rb') as fh:
                sub_x = fh.read()
        out_a, out_x, got = applier.apply_to_sublevel(
            sub_a, sub_x, new_path.rsplit('/', 1)[-1], edits)
        if edits.get('lightbake'):
            out_a, out_x, _n = lightbake.retarget_level(out_a, out_x, old_path, new_path)
            bd_path, bd_blobs, got['lightbake'] = lightbake.copy_built_data(
                old_path, new_path)
            if not lightbake.imports_resolve((out_a, out_x), bd_blobs, new_path):
                raise SystemExit('%s does not import the built data copied for it'
                                 % new_path.rsplit('/', 1)[-1])
            extra_files[bd_path] = (bd_blobs[:2], '.uasset')
            bulk_files[bd_path] = bd_blobs[2]
        remap[old_path] = new_path
        extra_files[new_path] = ((out_a, out_x), '.umap')
        sub_report[tag] = got
    #: the music copy, when no edit to the `_Audio` sublevel took it above
    if music is not None:
        old_path, new_path, blobs, sub_report[levelmusic.TAG] = music
        remap[old_path] = new_path
        extra_files[new_path] = (blobs, '.umap')
    #: the Mission Events piece's Blueprint (riftlord/missionevents.py), on any level
    from riftlord.missions import missionevents
    for path, blobs in missionevents.package(level_name, mod, plan).items():
        extra_files[path] = (blobs, '.uasset')
    #: the mission's own scripts' Blueprints (riftlord/missions/missionscript.py)
    from riftlord.missions import missionscript
    for path, blobs in missionscript.package(level_name, mod, plan).items():
        extra_files[path] = (blobs, '.uasset')
    #: the mission's lava rock materials, their overlay band fitted to its rocks
    if not new_level:
        from riftlord.build.world import lavafx
        for path, blobs in lavafx.rock_materials(level_name, plan, mod).items():
            extra_files[path] = ((blobs['.uasset'], blobs['.uexp']), '.uasset')
    keep_streams = None
    if new_level:
        more, keep_streams, own, sub_report['scratch'] = scratchmod.streams(level_name, mod,
                                                                            plan)
        remap.update(more)
        extra_files.update(own)

    # A custom map by default, in a slot (see `build`). With `into_campaign`: listed
    # in the SOURCE MAP'S OWN campaign, not in a private one of its own.
    #
    # A mod with its own CampaignProtos row is invisible to Weaver -- Weaver reconciles
    # what mods add to the SHARED campaigns, and a wholly private campaign adds nothing
    # to them -- and the mission is off in a campaign of one rather than in the list a
    # player browses. It is also what keeps entitlement honest: a mission built from
    # Canyon Keep lands in Drastic Steps and stays gated behind the DLC that ships it,
    # which is the rule about not moving missions between campaigns.
    # The mission's own MapData, so the minimap marks the rift and doors the PLAN has
    # rather than the ones the shipped level had. Pointing the row at the source map's
    # drew the right picture with the wrong markers, and a door the plan added drew
    # nothing at all.
    from riftlord.build.minimap import mapdataemit
    from riftlord.build.minimap import minimapredraw
    level_path = '%s/Maps/%s_Map01/%s_Map01' % (root, mod, mod)
    # Named after the SOURCE map: OrcsMustUI picks the hub's backdrop from the
    # MissionLevel and MapData paths (ScenarioDLC_, Scenario_Ice, Scenario_Sand),
    # and the level copy's own path names only the mod.
    md_path = '%s/%s_MapData' % (level_path.rsplit('/', 1)[0], level_name)
    md_files, md_report = mapdataemit.build(level_name, md_path, plan)
    if MAPDATA_SHIPPED and not new_level and not minimapredraw.wanted(plan):
        md_files, md_path = None, None
    if new_level:
        if md_files is None:
            raise SystemExit('%s has no MapData for a new level to start from' % level_name)
        tex_path, tex_obj = minimapredraw.texture_ref(level_path.rsplit('/', 1)[0], level_name)
        # drawn by Regenerate's renderer and rules (riftlord/minimapredraw.py), on the
        # frame the level's map volume gets (scratch.volume_for)
        s_volume, s_painter, _sfl = minimapredraw.scratch_minimap(level_name, plan)
        tex_files, md_report['minimap'] = scratchmod.minimap(
            plan, level_name, '%s.%s' % (tex_path, tex_obj), volume=s_volume,
            painter=s_painter)
        md_files = scratchmod.set_volume(md_files, md_report['minimap']['volume'],
                                         '%s.%s' % (tex_path, tex_obj))
        extra_files[tex_path] = (tex_files, '.uasset')
    elif md_files is not None and minimapredraw.wanted(plan):
        from riftlord.core import buildstage
        buildstage.stage('Minimap')
        redraw.update(minimapredraw.prepare(level_name, plan))
        tex_path, tex_obj = minimapredraw.texture_ref(level_path.rsplit('/', 1)[0], level_name)
        if len(redraw.get('painters') or []) > 1:
            # Regenerate on a multi-floor level: one texture per floor drawing, each
            # MapData element framed on its own floor's volume and showing its texture
            # named as the source floors' textures: the level copy imports each by the
            # source's object name, and retarget_map_data swaps only the package path
            folder = level_path.rsplit('/', 1)[0]
            refs = [minimapredraw.texture_ref(folder, level_name, ks[0])
                    for ks in redraw['groups']]
            paths = [r[0] for r in refs]
            reports = []
            for g, painter in enumerate(redraw['painters']):
                files_g, rep = scratchmod.minimap(
                    plan, level_name, '%s.%s' % refs[g], volume=redraw['volume'],
                    painter=painter, donor=redraw['donor'])
                extra_files[paths[g]] = (files_g, '.uasset')
                reports.append(rep)
            md_report['minimap'] = dict(reports[0], floors=reports)
            md_report['minimap'].update(mode=minimapredraw.mode(plan),
                                        reframe=redraw['reframe'], notes=redraw['notes'])
            tex_path = [paths[g] for g in redraw['element_group']]
            md_files = scratchmod.set_volume(md_files, redraw['volumes'],
                                             ['%s.%s' % refs[g] for g in redraw['element_group']])
        else:
            # Cut (the shipped picture trimmed) or Regenerate
            tex_files, md_report['minimap'] = scratchmod.minimap(
                plan, level_name, '%s.%s' % (tex_path, tex_obj), volume=redraw['volume'],
                painter=redraw['painter'], donor=redraw['donor'])
            md_report['minimap'].update(mode=minimapredraw.mode(plan), reframe=redraw['reframe'],
                                        notes=redraw['notes'])
            md_files = scratchmod.set_volume(md_files, redraw['volume'],
                                             '%s.%s' % (tex_path, tex_obj))
            extra_files[tex_path] = (tex_files, '.uasset')
    if md_files is not None:
        extra_files[md_path] = (md_files, '.uasset')

    # An arm in the coordinator for every spawner the plan adds. A coordinator branches
    # on a spawner index, so a spawner that exists only in the level is unreachable: the
    # Onslaught Editor found its spawns had nowhere to go, and a mission never opened
    # there would ship a spawner that never fires. Emitted here so the mission plays as
    # built, and the editor can redesign the wave afterwards.
    # DEFAULT OFF, because an arm hangs the game on load and the level additions do not.
    # Measured by bisection, one game launch per variant:
    #
    #   level copy alone                     loads
    #   + spawner, 9 points, branches        loads   <- everything on the LEVEL side
    #   + one arm and a close chain          hangs
    #   + four arms, no close chain          hangs
    #
    # So `add_spawner_arms` is wrong in a way no offline check here catches, and until it
    # is understood Rift Lord emits the spawner WITHOUT touching the coordinator. The
    # spawner then sits idle -- a supported design -- and its waves are authored in the
    # Onslaught Editor, whose `author` rewrites the existing ladder instead of appending
    # chains to it.
    mutate, report_arms = (spawner_arms(level_name, plan, only_waves=arm_waves)
                           if arms else (None, {}))
    # Never None: that is makemod's signal to author its own default waves.
    mutate = mutate or keep_coordinator

    from riftlord.missions import customcampaign
    if into_campaign:
        into = native_campaign_for(level_name)
    else:
        into, _choice = customcampaign.resolve(placement)
    #: the loading screen's campaign line for a custom campaign (loadscreen.campaign_line)
    line = None
    if not into_campaign and isinstance(_choice, (str, dict)):
        from riftlord.build.world import loadscreen
        if isinstance(_choice, dict):
            name = (_choice.get('name') or campaign_name or '').strip()
        else:
            name = next((c['title'] for c in customcampaign.installed()
                         if c['key'] == _choice), '')
        line = loadscreen.campaign_line(name=name or None,
                                        key='%s_Campaign' % mod if name else None)
    files, report = makemod.build(
        mod=mod, title=title, level=None, par=par,
        war=plan_war(plan, level_name),
        campaign_title=None, into=into,
        endless=bool(endless), endless_only=bool(endless_only),
        slot=makemod.slot_for(mod), guids=guids,
        with_level=level_name, apply_to_level=write_plan_in,
        sublevels=remap or None, extra_files=extra_files or None,
        keep_sublevels=keep_streams,
        map_data=md_path if md_files is not None else None,
        mutate=mutate, load_picture=load_picture, campaign_line=line,
        portal_picture=portal_picture, scramble=scramble, unlocks=unlocks,
        into_index=into_index if into else None)
    for path, blob in bulk_files.items():
        if blob:
            files['OMD/Content' + path[len('/Game'):] + '.ubulk'] = blob
    if lightbake.active() and not new_level:      # riftlord.lightbake: persistent copy
        lightbake.persistent_files(files, level_name, mod, sub_report)
    if ((plan or {}).get('world') or {}).get('warScenario') is not None:
        files = set_war_flag(files, plan_war(plan, level_name))
    #: the placed Fab pieces' package closure, copied from their libraries (riftlord.fabemit)
    from riftlord.mapeditor import fabemit
    if fabemit.uses_fab(plan):
        files = fabemit.add_closure(files, plan, report)
    #: the Chandelier's kit, carried byte for byte (riftlord/mapeditor/chandelier.py)
    from riftlord.mapeditor import chandelier
    files = chandelier.add_kit(files, plan, report)
    #: the supported mods the placed chests, potions and Crystal need, nothing copied
    #: (riftlord.core.supportedmods)
    from riftlord.core import supportedmods
    supportedmods.note_build(plan, report)
    #: the Crystal's game type (riftlord.mapeditor.crystal)
    from riftlord.mapeditor import crystal
    crystal.note_build(plan, report)
    report['map_data'] = md_report
    report['spawner_arms'] = report_arms
    report['into_campaign'] = into
    report['sublevels'] = sub_report
    report['mod'] = mod
    report['title'] = title
    report['source_level'] = level_name
    report['war'] = plan_war(plan, level_name)
    applied = report.get('applied') or {}
    report['applied'] = applied
    # Only what really is still outstanding: geometry lives in the streamed sublevels,
    # which the mod does not copy, so moving or removing a wall cannot apply yet.
    report['not_applied'] = [NOT_YET[key] + ' x%d' % len(plan.get(key) or [])
                             for key in NOT_YET if plan.get(key)]
    report['not_applied'] += applied.get('skipped') or []
    return files, report


def preserve_coordinator(pak_path, files, mod, level_name, plan):
    """Reuse the coordinator from an existing build of this mod, if it is safe to.

    Rift Lord copies the SOURCE level's coordinator on every save
    (`keep_coordinator`), which is right the first time and destructive afterwards:
    waves authored in the Onslaught Editor live in the MOD's coordinator, so the next
    save silently replaced them with the shipped level's ladder. That is how a flyer
    spawner which had been given a wave-1 onslaught went back to being idle, with
    every check passing.

    The coordinator dispatches on SPAWNER INDEX, so reusing one is safe exactly when
    every index it was written against still exists in the new level. Adding a new
    spawner is fine and expected -- the old coordinator simply has no arm for it, so
    it sits idle until the Onslaught Editor gives it one, which is the documented
    design because emitting an arm here hangs the game on load. Removing one is not
    fine: the old arm would dispatch to a spawner that is gone, so the fresh
    coordinator is kept instead and the author is told where their waves still are.

    Returns (files, note).
    """
    if not pak_path or not os.path.exists(pak_path):
        return files, None
    base = 'OMD/Content/Mods/%s/%s' % (mod, mod)
    wanted = ['%s_Onslaught01.uasset' % base, '%s_Onslaught01.uexp' % base,
              '%s_Enemies01.uasset' % base, '%s_Enemies01.uexp' % base]
    if any(k not in files for k in wanted):
        return files, None

    from riftlord.core import paksource
    from riftlord.onslaught import onslaught as _ons
    try:
        inner = set(paksource.names(pak_path))
        if any(k not in inner for k in wanted):
            return files, None               # not a previous build of this mod
        got = {k: paksource.read(pak_path, k) for k in wanted}
    except Exception as exc:                         # noqa: BLE001
        return files, ('waves rebuilt: the previous build did not read (%s: %s)'
                       % (type(exc).__name__, exc))

    if all(got[k] == files[k] for k in wanted):
        return files, None                   # nothing was authored since last time

    navpatch.open_pak(pak_path)
    try:
        before = {sp['index']
                  for sp in _ons.map_report('%s_Map01' % mod)['spawners']
                  if isinstance(sp.get('index'), int)}
    except Exception:                                # noqa: BLE001
        before = None
    finally:
        navpatch.close_pak(pak_path)
    if before is None:
        return files, 'waves rebuilt: the previous level did not read'

    now = set(used_spawner_indices(level_name))
    now |= {sp.get('index') for sp in (plan.get('spawners') or [])
            if isinstance(sp.get('index'), int)}
    missing = sorted(i for i in before - now if i is not None)
    if missing:
        return files, ('waves rebuilt: spawner(s) %s were removed'
                       % ', '.join(str(i) for i in missing))

    files = dict(files)
    for k in wanted:
        files[k] = got[k]
    return files, 'kept the waves from the previous build'


def write(level_name, out=None, install=False, details=None, **kwargs):
    """Build and write the pak. Returns the report, with where it went.

    `details` is what OrcsMustUI shows and where the mission is listed
    (customcampaign.finish): {author, version, description, newCampaign}."""
    from riftlord.missions import customcampaign
    fresh_waves = kwargs.pop('fresh_waves', False)
    if out is None:
        # `900_<mission name>_P`: the 900 keeps it loading after the shipped paks, and
        # the name the author gave the mission is what identifies it on disk. A pak in
        # ~mods, or a loose folder in Rift Lord's bucket.
        out = gamepath.path_in(gamepath.target_dir() if install else
                               gamepath.scratch('emit-out', gamepath.CODE_ROOT),
                               '900_%s_P' % pak_stem(
                                   kwargs.get('title') or '%s (Map Editor)' % level_name))
    placement = kwargs.get('placement') or makemod.CUSTOM_MAPS
    _into, choice = customcampaign.resolve(placement, details)
    if isinstance(choice, dict) and not (choice.get('name') or '').strip():
        raise SystemExit('name the new campaign')
    # A rebuild keeps the mission's Guids: a campaign lists its missions by Guid.
    kwargs.setdefault('guids', makemod.previous_guids(out))
    kwargs.setdefault('endless', bool((details or {}).get('endless')))
    kwargs.setdefault('endless_only', bool((details or {}).get('endlessOnly')))
    if isinstance(choice, dict):
        kwargs.setdefault('campaign_name', choice.get('name'))
    files, report = build(level_name, **kwargs)
    failures = [(name, detail) for name, ok, detail
                in makemod.verify(files, report) if not ok]
    # Can every spawner in the level reach the close? `makemod.verify` says nothing about
    # bytecode, and a coordinator that cannot terminate for one spawner hangs the game on
    # the loading screen with every other check passing -- which is exactly what an
    # emitted arm did. An idle spawner counts: the game enumerates it too.
    # `closes_checks` is NOT consulted any more. It asserted that every spawner must
    # reach `OnslaughtEnd`, which measurement disproved: a level carrying a spawner with no
    # coordinator arm at all loads and runs. What actually matters is whether the dispatch
    # TERMINATES, and `flow_checks` simulates that below.
    report['checks_failed'] = ['%s: %s' % (n, d) for n, d in failures]
    # A BAD FLIGHT GRAPH BLOCKS THE BUILD.
    #
    # `install_flight` verifies the graph against its own invariants and then by
    # reading the written package back, so a failure here means the buffer really is
    # wrong -- and the game's only way of complaining about that is to hang and then
    # crash, with every other check passing.
    flight_bad = (report.get('applied') or {}).get('flight_failed') or []
    report['checks_failed'] += ['flight graph: %s' % n for n in flight_bad]
    if failures or flight_bad:
        # The same rule the converter follows: a package that does not read back is
        # not written, because the game's only way of complaining is to crash.
        report['written'] = None
        return report
    os.makedirs(os.path.dirname(out), exist_ok=True)
    # Authored waves live in the MOD's coordinator, so a rebuild must not discard them.
    if not kwargs.get('arms') and not fresh_waves:
        files, kept = preserve_coordinator(
            out, files, report.get('mod'), level_name,
            kwargs.get('plan') if kwargs.get('plan') is not None
            else leveldata.load_plan(level_name))
        report['coordinator'] = kept or 'rebuilt from %s' % level_name
    #: mod minions the waves spawn, re-copied from the installed mod (modminions)
    from riftlord.missions import modminions
    try:
        files, report['modMinions'] = modminions.carry(files, report.get('mod'))
    except SystemExit as exc:
        report['checks_failed'] = ['mod minions: %s' % exc]
        report['written'] = None
        return report
    from riftlord.core import paksource
    paksource.write(out, files)
    # The path graph can only be read from a finished pak, so it is checked after the
    # write and the pak is REMOVED if it fails: a level whose routes loop or share an
    # index hangs the game on load, and leaving it on disk invites exactly that.
    mapname = '%s_Map01' % report.get('mod')
    graph = path_checks(out, mapname)
    graph += flow_checks(out, '%s_Onslaught01' % report.get('mod'))
    report['path_checks'] = graph
    broken = ['%s: %s' % (c['name'], c['detail']) for c in graph if not c['ok']]
    if broken:
        paksource.remove(out)
        report['checks_failed'] = broken
        report['written'] = None
        return report
    report['written'] = out
    report['bytes'] = paksource.size(out)
    placed = customcampaign.finish(out, report, placement, details)
    report['campaign'] = placed.get('campaign')
    report['campaign_pak'] = placed.get('campaign_pak')
    report['notes'] = placed.get('notes')
    report['placement'] = placed.get('placement')
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--level', required=True)
    ap.add_argument('--mod')
    ap.add_argument('--name')
    ap.add_argument('--campaign', default='Custom Maps')
    ap.add_argument('--par', type=float, default=600.0)
    ap.add_argument('--out')
    ap.add_argument('--install', action='store_true',
                    help="write straight into the game's ~mods folder")
    args = ap.parse_args(argv)
    report = write(args.level, out=args.out, install=args.install, mod=args.mod,
                   title=args.name, campaign=args.campaign, par=args.par)
    print('mod          %s' % report['mod'])
    print('mission      %s' % report.get('mission_row'))
    print('campaign     %s' % report.get('campaign_row'))
    print('level copy   %s' % report.get('level_from'))
    print('war scenario %s' % report['war'])
    if report['checks_failed']:
        for line in report['checks_failed']:
            print('   FAILED  %s' % line)
        return 1
    print('wrote        %s (%d bytes)' % (report['written'], report['bytes']))
    if report['not_applied']:
        print('\nnot applied to the level yet:')
        for line in report['not_applied']:
            print('   %s' % line)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
