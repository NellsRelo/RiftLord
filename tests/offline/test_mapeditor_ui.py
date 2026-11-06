"""Offline checks on the Map Editor's 2026-10-28 round: the level picker's campaigns (from
the game's campaign tables), the New Object shelf of weapons and trinkets, a blocking
volume's move, turn and resize written into a copy of a level and read back, and the New
Level dialog's game modes.

    python3 test_mapeditor_ui.py
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from riftlord.formats.omd3 import meshcat  # noqa: E402
from riftlord.formats.omd3 import omd3level  # noqa: E402
from riftlord.mapeditor import levelgroups  # noqa: E402
from riftlord.mapeditor import volumes  # noqa: E402

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


# 1. the picker's campaigns: each shipped level under its campaign's own CampaignName
from riftlord.missions import customcampaign  # noqa: E402
names = customcampaign.shipped_names()
got = levelgroups.groups()
want = {'Scenario_01': 'OMD3_Main_Story', 'Scenario_18': 'OMD3_Main_Story',
        'ScenarioDLC_03': 'DLC3_Main_Story', 'Scenario_Ice_02': 'DLC_Ice_Campaign',
        'Scenario_Sand_04': 'DLC_Sand_Campaign', 'Scenario_Other_02': 'OMD3_Endless'}
for level, row in want.items():
    have = (got.get(level) or {}).get('campaign')
    check('%s is under %s' % (level, names.get(row)), have and have == names.get(row), have)
order = [got[lv]['order'] for lv in ('Scenario_01', 'ScenarioDLC_01', 'Scenario_Ice_01',
                                     'Scenario_Sand_01', 'Scenario_Other_02')]
check('story campaigns before Endless, in the table\'s order', order == sorted(order), order)
check('Sand levels keep their campaign\'s order',
      got['Scenario_Sand_04']['index'] < got['Scenario_Sand_01']['index'],
      (got['Scenario_Sand_04']['index'], got['Scenario_Sand_01']['index']))
filed = levelgroups.file_levels([{'name': 'NoSuchMod_Map01', 'title': ''},
                                 {'name': 'Scenario_02', 'title': 'Great Room'}])
check('a level no campaign lists is under Custom Maps, last',
      [f['campaign'] for f in filed] == [names['OMD3_Main_Story'], levelgroups.CUSTOM_MAPS],
      [f['campaign'] for f in filed])

# 2. weapons and trinkets are on the trap shelf whatever their names say; their effects
#    are effects
for path, shelf in (('/Game/Weapons/Amulet_Ice/Mesh/Amulet_Ice', 'trap'),
                    ('/Game/Trinkets/EmptyRift/Mesh/Trinket_EmptyRift', 'trap'),
                    ('/Game/Proto/Weapons/BladeStaff/Mesh/BladeStaff', 'trap'),
                    ('/Game/Weapons/Longbow/Mesh/Bow_Magic_Limbs', 'trap'),
                    ('/Game/Trinkets/RingOfStorms/Mesh/RingofStorms_SM', 'trap'),
                    ('/Game/Trinkets/ReptilianCarapace/Mesh/ReptilianCarapace_SM', 'trap'),
                    ('/Game/Proto/Weapons/FlameBracers/FX/FireWall', 'effect'),
                    ('/Game/Environments/CastleOrder/Floors/Floor_4x4', 'castle')):
    have = meshcat.category_of(path)
    check('%s is %s' % (path.rsplit('/', 1)[-1], shelf), have == shelf, have)
from riftlord.mapeditor import leveldata  # noqa: E402
rows = {r['name']: r for r in leveldata.catalogue() if r['kind'] == 'mesh'}
for name in ('BladeStaff', 'Amulet_Ice', 'Bow_Magic_Flare', 'Trinket_EmptyRift',
             'ReptilianCarapace_SM', 'RingofStorms_SM'):
    check('New Object lists %s under trap' % name,
          (rows.get(name) or {}).get('category') == 'trap', (rows.get(name) or {}).get('category'))

# 3. a blocking volume moved, turned and resized, written into a copy of the level
LEVEL = 'Scenario_02'
vol = next((v for v in volumes.level_volumes(LEVEL) if v['cls'] == 'BlockingVolume'), None)
check('%s has a blocking volume' % LEVEL, vol, '')
if vol:
    loc = [vol['location'][0] + 300.0, vol['location'][1] - 150.0, vol['location'][2] + 75.0]
    rot = [10.0, 35.0, -5.0]
    scale = [1.5, 0.5, 2.0]
    matrix = [v for col in zip(*omd3level._matrix(loc, rot, scale)) for v in col]
    clean = volumes.clean([{'name': vol['name'], 'matrix': matrix},
                           {'name': 'mirrored', 'matrix': [-1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0,
                                                           0, 0, 0, 1]}])
    check('the save keeps a turned volume and drops a mirrored one',
          [e['name'] for e in clean] == [vol['name']] and 'matrix' in clean[0], clean)
    back = volumes.edited_transform(vol, clean[0])
    close = all(abs(a - b) < 1e-3 for a, b in zip(sum(back, []), loc + rot + scale))
    check('the edit reads back as its location, rotator and scale', close, back)

    from riftlord.build import levelbuild
    from riftlord.build.navigation import navpatch
    from riftlord.core import gamefiles
    from riftlord.formats.ue.uassetio import Package
    ua, ux = gamefiles.package(navpatch.map_path(LEVEL))
    builder = levelbuild.Builder.around(ua, ux, LEVEL)
    report = {'notes': [], 'skipped': []}
    volumes.apply(builder, {'volumes': clean}, LEVEL, report)
    ua2, ux2 = builder.write()
    pkg = Package(ua2, ux2)
    pkg.load_payloads()
    names_ = [n for n, _h in pkg.names]
    data = pkg.exports[vol['index'] - 1]['data']
    props = meshcat._walk(data, names_, 0, len(data))
    root = struct.unpack_from('<i', data, props['RootComponent'][1])[0]
    cdata = pkg.exports[root - 1]['data']
    cprops = meshcat._walk(cdata, names_, 0, len(cdata))

    def vec(field):
        return list(struct.unpack_from('<3f', cdata, cprops[field][1])) if field in cprops else None
    wrote = [vec('RelativeLocation'), vec('RelativeRotation'), vec('RelativeScale3D')]
    ok = all(w is not None for w in wrote) and all(
        abs(a - b) < 1e-2 for a, b in zip(sum(wrote, []), loc + rot + scale))
    check('the built level places, turns and sizes the volume', ok,
          '%s; %s' % (wrote, report['notes'][:1]))

# 3b. a blocking volume the plan adds: lifted from the donor, placed, turned and sized,
#     read back with its brush, and in the walkable-graph rebuild's blocking brushes
d = volumes.donor()
check('the donor volume is a centred box with the class-default profile',
      d and d['cls'] == 'BlockingVolume' and d['profile'] is None and d['stops'] == 'everyone'
      and all(abs(d['max'][a] + d['min'][a]) < 1 for a in range(3)), d and d['name'])
if d and vol:
    at = [vol['location'][0] + 500.0, vol['location'][1], vol['location'][2]]
    rot2 = [0.0, 30.0, 0.0]
    scale2 = [2.0, 0.5, 1.5]
    m2 = [v for col in zip(*omd3level._matrix(at, rot2, scale2)) for v in col]
    kept = volumes.clean([{'name': 'RiftLordVolume1', 'added': True, 'matrix': m2},
                          {'name': 'RiftLordVolume1', 'added': True, 'matrix': m2},
                          {'name': 'NotOurs', 'added': True, 'matrix': m2},
                          {'name': 'RiftLordVolume2', 'added': True}])
    check('the save keeps one added volume per name, with our prefix and a matrix',
          [e['name'] for e in kept] == ['RiftLordVolume1'] and kept[0]['added'], kept)
    ua, ux = gamefiles.package(navpatch.map_path(LEVEL))
    builder = levelbuild.Builder.around(ua, ux, LEVEL)
    report = {'notes': [], 'skipped': []}
    volumes.apply(builder, {'volumes': kept}, LEVEL, report)
    ua3, ux3 = builder.write()
    pkg = Package(ua3, ux3)
    pkg.load_payloads()
    names_ = [n for n, _h in pkg.names]
    new = [i for i, e in enumerate(pkg.exports) if levelbuild._name_of(pkg, i) == 'RiftLordVolume1']
    check('the built level has the added volume', len(new) == 1 and levelbuild.resolve(
        pkg, struct.unpack_from('<i', pkg.exports[new[0]]['meta'], 0)[0])[1] == 'BlockingVolume',
        (new, report['skipped']))
    if new:
        data = pkg.exports[new[0]]['data']
        props = meshcat._walk(data, names_, 0, len(data))
        root = struct.unpack_from('<i', data, props['RootComponent'][1])[0]
        cdata = pkg.exports[root - 1]['data']
        cprops = meshcat._walk(cdata, names_, 0, len(cdata))

        def vec3(field):
            return list(struct.unpack_from('<3f', cdata, cprops[field][1])) if field in cprops else None
        wrote = [vec3('RelativeLocation'), vec3('RelativeRotation'), vec3('RelativeScale3D')]
        check('it is placed, turned and sized', all(w is not None for w in wrote) and all(
            abs(a - b) < 1e-2 for a, b in zip(sum(wrote, []), at + rot2 + scale2)), wrote)
        outer = [struct.unpack_from('<i', e['meta'], levelbuild.OUTER_AT)[0] for e in pkg.exports]
        under = {i + 1 for i, o in enumerate(outer) if o == new[0] + 1}
        under |= {i + 1 for i, o in enumerate(outer) if o in under}
        kids = [levelbuild.resolve(pkg, struct.unpack_from('<i', pkg.exports[i - 1]['meta'], 0)[0])[1]
                for i in sorted(under)]
        setup = struct.unpack_from('<i', cdata, cprops['BrushBodySetup'][1])[0] \
            if 'BrushBodySetup' in cprops else 0
        brush = volumes._convex_points(pkg, names_, setup) if setup > 0 else []
        check('it brings its BrushComponent, Model and BodySetup with an 8-point brush',
              {'BrushComponent', 'Model', 'BodySetup'} <= set(kids) and len(brush) == 8,
              (kids, len(brush)))
        actors = builder.actor_list()
        check('the level lists it among its actors', new[0] + 1 in actors, '')
    from riftlord.build.navigation import navregen
    import numpy as np
    base = navregen.blocking_volumes(LEVEL, {})
    more = navregen.blocking_volumes(LEVEL, {'volumes': kept})
    extra = more[len(base):]
    pts = np.concatenate([t.reshape(-1, 3) for t in extra]) if extra else np.zeros((0, 3))
    want = np.array([[0, 0, 0]], dtype=float)
    if len(pts):
        inv = np.linalg.inv(np.array(omd3level._matrix(at, rot2, scale2)))
        local = (np.c_[pts, np.ones(len(pts))] @ inv.T)[:, :3]
        want = np.abs(local).max(axis=0)
    check('the walkable-graph rebuild blocks the added brush where the plan puts it',
          len(extra) > 0 and np.allclose(want, [d['max'][a] for a in range(3)], atol=0.5),
          (len(extra), want.tolist()))

# 3c. Blocks: players only (the shipped OMDPlayerBlockingVolume profile) or everyone
#     (BlockAll), for added volumes and the level's own box volumes
p_donor = volumes.donor('players')
check('the Players donor carries the shipped player-blocking profile, a centred box',
      p_donor and p_donor['profile'] == 'OMDPlayerBlockingVolume' and
      p_donor['stops'] == 'players' and p_donor['blocks'] == 'players', p_donor and p_donor['name'])
check('a shipped OMDPlayerBlockingVolume-profile BlockingVolume stops players only',
      next((v['stops'] for v in volumes.level_volumes('Scenario_04')
            if v['name'] == 'BlockingVolume10'), None) == 'players', '')
custom = {'Pawn': 'ECR_Ignore', 'Visibility': 'ECR_Ignore'}
check('a Custom one ignoring Pawn but not the player stops players only',
      volumes.custom_stops(custom) == 'players' and volumes.custom_stops({}) == 'everyone', '')


def body_of(pkg, names_, index):
    """(profile, {channel: response}) of an export's root component BodyInstance."""
    data = pkg.exports[index]['data']
    props = meshcat._walk(data, names_, 0, len(data))
    root = struct.unpack_from('<i', data, props['RootComponent'][1])[0]
    cdata = pkg.exports[root - 1]['data']
    cprops = meshcat._walk(cdata, names_, 0, len(cdata))
    if 'BodyInstance' not in cprops:
        return None, {}, cdata, cprops
    _k, at2, size2 = cprops['BodyInstance']
    body = meshcat._walk(cdata, names_, at2, at2 + size2)
    n = struct.unpack_from('<i', cdata, body['CollisionProfileName'][1])[0] \
        if 'CollisionProfileName' in body else None
    return (names_[n] if n is not None else None), volumes.responses_of(cdata, names_, body), \
        cdata, cprops


if p_donor and vol:
    swap = next((v for v in volumes.level_volumes(LEVEL) if v['blocks'] == 'everyone'), None)
    at4 = [vol['location'][0] - 500.0, vol['location'][1], vol['location'][2]]
    m4 = [v for col in zip(*omd3level._matrix(at4, (0.0, 0.0, 0.0), (6.0, 0.5, 3.0)))
          for v in col]
    plan4 = {'volumes': [{'name': 'RiftLordVolume1', 'added': True, 'matrix': m4,
                          'blocks': 'players'},
                         {'name': swap['name'], 'blocks': 'players'}]}
    kept4 = volumes.clean(plan4['volumes'])
    check('the save keeps Blocks on an added and a level volume',
          [e.get('blocks') for e in kept4] == ['players', 'players'], kept4)
    ua, ux = gamefiles.package(navpatch.map_path(LEVEL))
    builder = levelbuild.Builder.around(ua, ux, LEVEL)
    report = {'notes': [], 'skipped': []}
    volumes.apply(builder, plan4, LEVEL, report)
    ua4, ux4 = builder.write()
    pkg = Package(ua4, ux4)
    pkg.load_payloads()
    names_ = [n for n, _h in pkg.names]
    by_name = {levelbuild._name_of(pkg, i): i for i in range(len(pkg.exports))}
    for label, export, want_size in (
            ('the added Players volume', 'RiftLordVolume1', [1200.0, 100.0, 600.0]),
            ('the level volume made Players', volumes.SWAPPED_PREFIX + str(swap['index']),
             [(swap['max'][a] - swap['min'][a]) * swap['scale'][a] for a in range(3)])):
        i = by_name.get(export)
        if i is None:
            check('%s is built' % label, False, report['skipped'])
            continue
        profile, resp, cdata, cprops = body_of(pkg, names_, i)
        scale_w = list(struct.unpack_from('<3f', cdata, cprops['RelativeScale3D'][1])) \
            if 'RelativeScale3D' in cprops else [1.0, 1.0, 1.0]
        size_w = [(p_donor['max'][a] - p_donor['min'][a]) * scale_w[a] for a in range(3)]
        check('%s has the player-blocking profile (enemies ignored) at its size' % label,
              profile == 'OMDPlayerBlockingVolume' and resp.get('Pawn') == 'ECR_Ignore' and
              resp.get('OMDPlayerCharacter') != 'ECR_Ignore' and
              all(abs(a - b) < 0.5 for a, b in zip(size_w, want_size)),
              (profile, resp.get('Pawn'), size_w, want_size))
    _p, _r, cdata, cprops = body_of(pkg, names_, swap['index'] - 1)
    parked = struct.unpack_from('<3f', cdata, cprops['RelativeLocation'][1])
    check('the level volume made Players is parked', abs(parked[2] - volumes.PARKED[2]) < 1,
          parked)
    from riftlord.build.navigation import navregen
    tris = lambda got: sum(len(t) for t in got)  # noqa: E731
    base = tris(navregen.blocking_volumes(LEVEL, {}))
    without = tris(navregen.blocking_volumes(LEVEL, {'volumes': [{'name': swap['name'],
                                                                  'gone': True}]}))
    four = tris(navregen.blocking_volumes(LEVEL, plan4))
    check('the walkable-graph rebuild blocks neither Players volume',
          without < base and four == without, (base, without, four))

# 3d. a level's own OMDPlayerBlockingVolume made to stop everyone blocks the rebuild
pbv = next((v for v in volumes.level_volumes('Scenario_09')
            if v['cls'] == 'OMDPlayerBlockingVolume' and v['blocks'] == 'players'), None)
check('a player blocking volume offers Blocks', pbv, '')
if pbv:
    from riftlord.build.navigation import navregen
    n0 = sum(len(t) for t in navregen.blocking_volumes('Scenario_09', {}))
    n1 = sum(len(t) for t in navregen.blocking_volumes(
        'Scenario_09', {'volumes': [{'name': pbv['name'], 'blocks': 'everyone'}]}))
    check('made Players and Enemies, it blocks the walkable-graph rebuild', n1 > n0, (n0, n1))

# 3e. a Fab piece its catalogue marks `collision.collides` false (a sky) is placed with
#     collision off (BodyInstance CollisionEnabled and profile NoCollision, as the shipped
#     liquid planes), and the walkable-graph rebuild and the collision check leave it out
from riftlord.mapeditor import fabcollision  # noqa: E402
from riftlord.mapeditor import fabemit  # noqa: E402
from riftlord.mapeditor import fablibrary  # noqa: E402
FAB_PACK = 'Orasot'
try:
    by_object = fablibrary.catalogue(FAB_PACK)['_by_object']
except (Exception, SystemExit):                      # noqa: BLE001
    by_object = {}
sky_obj = next((k for k, e in by_object.items() if e.get('name') == 'SM_SkyBoxTransperent'
                and e.get('status') == 'converted'), None)
solid_obj = next((k for k, e in by_object.items() if e.get('status') == 'converted'
                  and e.get('name') != 'SM_SkyBoxTransperent' and fablibrary.collides(e)
                  and e.get('role') not in ('foliage',)), None)
if sky_obj and solid_obj and vol:
    real_entry_for = fablibrary.entry_for

    def entry_for(pack, obj, source=None):
        got = real_entry_for(pack, obj, source)
        if obj == sky_obj and got:
            got = dict(got, collision={'collides': False, 'source': 'none', 'boxes': 0,
                                       'convex': 0, 'trace': 'simple_as_complex'})
        return got
    fablibrary.entry_for = entry_for
    try:
        here = [vol['location'][0], vol['location'][1], vol['location'][2] + 800.0]
        flat = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0]
        fab_plan = {'added': [
            {'kind': 'fab', 'pack': FAB_PACK, 'object': sky_obj, 'matrix': flat + here + [1.0]},
            {'kind': 'fab', 'pack': FAB_PACK, 'object': solid_obj,
             'matrix': flat + [here[0] + 600.0, here[1], here[2], 1.0]}]}
        check('the sky piece reads as not colliding, the other as colliding',
              not fablibrary.collides(fablibrary.entry_for(FAB_PACK, sky_obj)) and
              fablibrary.collides(fablibrary.entry_for(FAB_PACK, solid_obj)), '')
        ua, ux = gamefiles.package(navpatch.map_path(LEVEL))
        builder = levelbuild.Builder.around(ua, ux, LEVEL)
        report = {'notes': [], 'skipped': []}
        got = fabemit.apply(builder, fab_plan, report)
        ua5, ux5 = builder.write()
        pkg = Package(ua5, ux5)
        pkg.load_payloads()
        names_ = [n for n, _h in pkg.names]
        placed = [i for i in range(len(pkg.exports))
                  if levelbuild._name_of(pkg, i).startswith(fabemit.PIECE_NAME.split('%')[0])]
        bodies = []
        for i in placed:
            data = pkg.exports[i]['data']
            props = meshcat._walk(data, names_, 0, len(data))
            if 'RootComponent' not in props:
                continue
            root = struct.unpack_from('<i', data, props['RootComponent'][1])[0]
            cdata = pkg.exports[root - 1]['data']
            cprops = meshcat._walk(cdata, names_, 0, len(cdata))
            fields = {}
            if 'BodyInstance' in cprops:
                _k, at5, size5 = cprops['BodyInstance']
                for name, kind, off, size in volumes._tags(cdata, names_, at5, at5 + size5)[0]:
                    if size == 8:
                        n = struct.unpack_from('<i', cdata, off)[0]
                        fields[name] = names_[n] if 0 <= n < len(names_) else n
            bodies.append(fields)
        off = [b for b in bodies if b.get('CollisionEnabled') == 'ECollisionEnabled::NoCollision'
               and b.get('CollisionProfileName') == 'NoCollision']
        check('the sky piece is placed with collision off, the other is not',
              got.get('noCollision') == 1 and len(bodies) == 2 and len(off) == 1, bodies)
        from riftlord.build.navigation import navregen
        names_fab = [navregen.fab_mesh_name(a) for a in fab_plan['added']]
        check('the walkable-graph rebuild leaves the sky piece out',
              names_fab[0] is None and names_fab[1] is not None, names_fab)
        real_measure = fabcollision.measure
        measured = []
        fabcollision.measure = lambda pack, entry: measured.append(entry['name']) or {
            'proudArea': 0.0, 'maxRise': 0.0, 'trace': 'default'}
        try:
            fabcollision.problems(fab_plan)
        finally:
            fabcollision.measure = real_measure
        check('the collision check leaves the sky piece out',
              'SM_SkyBoxTransperent' not in measured and len(measured) == 1, measured)
    finally:
        fablibrary.entry_for = real_entry_for
else:
    print('  (no %s library with a converted sky piece: the Fab NoCollision checks are skipped)'
          % FAB_PACK)

# 4. the New Level dialog's game modes: Rift Defense first and the default; Crystal Defense
#    exactly while its mod is detected
from riftlord.core import supportedmods  # noqa: E402
from riftlord.mapeditor import crystal  # noqa: E402
from riftlord.mapeditor import scratch  # noqa: E402
modes = scratch.game_modes()
check('Rift Defense first and the default',
      modes and modes[0]['gameType'] == '' and modes[0]['default'], modes)
has = any(m['gameType'] == crystal.GAME_TYPE for m in modes)
check('Crystal Defense offered exactly while detected',
      has == supportedmods.available(crystal.FEATURE), modes)

# 5. a ground spawner placed or moved stands SPAWNER_ABOVE_FLOOR over its floor; a built
#    one standing higher than any shipped one is warned about, naming it
from riftlord.build.navigation import navsurfextend  # noqa: E402
from riftlord.build.navigation import riftfloor  # noqa: E402
snapped = riftfloor.snap_spawner('Scenario_02', [2150.0, 6950.0, 700.0])
check('a ground spawner well above its floor is put SPAWNER_ABOVE_FLOOR over it',
      snapped['at'] and abs(snapped['at'][2] - snapped['floor'] -
                            navsurfextend.SPAWNER_ABOVE_FLOOR) < 0.11, snapped)
rift = riftfloor.snap('Scenario_02', [2150.0, 6950.0, 460.0])
check('the same floor as an unstable rift', rift['floor'] == snapped['floor'], (rift, snapped))
floor = [(-200.0, -200.0, 0.0), (200.0, -200.0, 0.0), (200.0, 200.0, 0.0), (-200.0, 200.0, 0.0)]
spawners = [('RiftLordSpawner1', 3, (0.0, 0.0, 98.0)), ('RiftLordSpawner2', 4, (0.0, 0.0, 206.0)),
            ('RiftLordSpawner3', 5, (0.0, 0.0, 240.0)), ('RiftLordSpawner4', 6, (900.0, 0.0, 999.0))]
warned = riftfloor.spawner_heights([floor], spawners, riftfloor.spawner_labels(
    {'spawners': [{'id': 30, 'index': 5}]}))
check('only the spawner over 206.6 uu is warned about, by its Map Editor label',
      warned == ['Spawner 30 stands 240 units above the floor, higher than any shipped '
                 'ground spawner (207 units). Lower it toward the floor, or test it in '
                 'game.'], warned)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'fixtures'))
import fixturelibs  # noqa: E402
SPAWNER_PAKS = [p for p in (fixturelibs.pinned_mod(n) for n in ('900_Beset_Alpha_3_P.pak',)) if p]
for pak in SPAWNER_PAKS:
    try:
        lines = riftfloor.spawner_warnings(pak)
        check('a built mission is measured (%s): %d warning(s)' % (os.path.basename(pak),
                                                                   len(lines)), True, lines)
        print('  ' + '\n  '.join(lines or ['no ground spawner over the limit']))
    except Exception as exc:                         # noqa: BLE001
        check('a built mission is measured (%s)' % os.path.basename(pak), False, exc)

bad = [c for c in checks if not c[1]]
for name, ok, detail in checks:
    print('%s %s%s' % ('ok  ' if ok else 'FAIL', name, '' if ok else ': %s' % (detail,)))
print('%d of %d checks passed' % (len(checks) - len(bad), len(checks)))
sys.exit(1 if bad else 0)
