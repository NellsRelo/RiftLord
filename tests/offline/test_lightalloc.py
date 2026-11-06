"""Light maps for added pieces and the Static lights added actors bring
(riftlord/build/lighting/lightalloc.py), offline, without baking.

    python3 tests/offline/test_lightalloc.py

- pack() puts every square in its atlas, none overlapping, the same way twice.
- receivers(): an added mesh is one receiver (its root); a kit piece one per static mesh
  part; Fab pieces, effects and kitactors classes none; ids are stable.
- extend() on Scenario_01's persistent BuiltData: atlas pairs copied from the level's own,
  blank, each new texture loading as the shipped ones do (preload) and the registry
  depending on it; one registry entry per receiver, its rectangle inside its atlas and
  apart from the others; LightBuildData for each light as the shipped Static lights have
  it; the package writes and reads back.
- apply_plan on Scenario_01 with a bake under way: the added mesh and every kit part named
  by a receiver keep Static and name their receiver's id in every LODData entry; an added
  PointLight and torch carry the LightGuids added_lights bakes them under, at the place the
  build puts them. Without a bake, the added mesh is Movable with no LODData, as before.
"""

import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

import numpy as np  # noqa: E402

from riftlord.build.lighting import lightalloc  # noqa: E402
from riftlord.build.lighting import lightbake  # noqa: E402
from riftlord.build.lighting import lightcodec  # noqa: E402
from riftlord.build.lighting import lightmaps  # noqa: E402

LEVEL = 'Scenario_01'
PERSISTENT = '/Game/Maps/Campaign/Scenario_01/Scenario_01'


def matrix(x, y, z, yaw=0.0):
    c, s = np.cos(np.radians(yaw)), np.sin(np.radians(yaw))
    return [c, s, 0.0, 0.0, -s, c, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, x, y, z, 1.0]


PLAN = {'added': [
    {'kind': 'mesh', 'name': 'Floor_4x4', 'matrix': matrix(100.0, 200.0, 0.0)},
    {'kind': 'kit', 'name': 'Wall_RB_MT_v2_2x3_C', 'matrix': matrix(600.0, 0.0, 0.0, 90.0)},
    {'kind': 'kit', 'name': 'PointLight', 'matrix': matrix(0.0, 0.0, 300.0)},
    {'kind': 'kit', 'name': 'BP_OrcTorch_LargeRadius_C', 'matrix': matrix(-400.0, 0.0, 0.0)},
]}
#: PLAN with pieces that receive nothing: a Fab piece and an effect.
WITH_OTHERS = dict(PLAN, added=PLAN['added'] + [
    {'kind': 'fab', 'name': 'fab/x', 'matrix': matrix(0.0, 0.0, 0.0)},
    {'kind': 'fx', 'name': 'P_Acid_Bubbles', 'matrix': matrix(0.0, 0.0, 0.0)}])

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok)))
    print('  %s  %s%s' % ('PASS' if ok else 'FAIL', name, (': %s' % (detail,)) if detail else ''))


def overlaps(rects):
    by = {}
    for atlas, x, y, s in rects:
        by.setdefault(atlas, []).append((x, y, s))
    for rs in by.values():
        for i, (x0, y0, s0) in enumerate(rs):
            for x1, y1, s1 in rs[i + 1:]:
                if x0 < x1 + s1 and x1 < x0 + s0 and y0 < y1 + s1 and y1 < y0 + s0:
                    return True
    return False


def packing():
    sizes = [512, 32, 64, 64, 128, 256, 32, 48, 512, 512, 100] * 7
    got = lightalloc.pack(sizes, 1024, 512)
    rects = [(a, x, y, lightalloc._pow2(s)) for (a, x, y), s in zip(got, sizes)]
    check('every square fits its atlas',
          all(0 <= x and x + s <= 1024 and 0 <= y and y + s <= 512 for _a, x, y, s in rects))
    check('no two squares overlap', not overlaps(rects))
    check('packing is the same twice', got == lightalloc.pack(sizes, 1024, 512))
    used = sum(lightalloc._pow2(s) ** 2 for s in sizes)
    atlases = 1 + max(a for a, _x, _y in got)
    check('atlases are filled before a new one starts',
          atlases == -(-used // (1024 * 512)), '%d atlases for %d texels' % (atlases, used))


def receivers():
    rs = lightalloc.receivers(WITH_OTHERS)
    check('Fab pieces and effects receive nothing', rs == lightalloc.receivers(PLAN))
    items = sorted({r['item'] for r in rs})
    check('the mesh and the wall receive; lights, Fab and effects do not',
          items == [0, 1] or items == [0, 1, 3], items)
    mesh = [r for r in rs if r['item'] == 0]
    check('an added mesh is one receiver, its root, at its placement',
          len(mesh) == 1 and mesh[0]['component'] is None
          and np.allclose(np.array(mesh[0]['matrix'])[:3, 3], (100.0, 200.0, 0.0)), mesh)
    wall = [r for r in rs if r['item'] == 1]
    from riftlord.mapeditor import leveldata
    parts = [p for p in leveldata.kit_parts('Wall_RB_MT_v2_2x3_C')['parts'] if not p.get('skeletal')]
    check('a kit piece is one receiver per static mesh part',
          len(wall) == len({p['component'] for p in parts}), '%d of %d' % (len(wall), len(parts)))
    check('receiver ids are stable and distinct',
          rs == lightalloc.receivers(PLAN) and len({r['guid'] for r in rs}) == len(rs))
    return rs


def extending(rs):
    bd = lightmaps.BuiltData(*lightmaps.fetch(PERSISTENT + '_BuiltData'))
    before = len(bd.pkg.exports)
    lights = lightalloc.added_lights(PLAN)
    comps = lightalloc.extend(bd, PERSISTENT, rs, lights)
    check('every receiver whose mesh reads gets a light map', len(comps) == len(rs),
          '%d of %d' % (len(comps), len(rs)))
    new = list(range(before, len(bd.pkg.exports)))
    check('atlases come in HQ/LQ pairs of the level\'s own size', len(new) % 2 == 0 and all(
        bd.textures[i]['formats'][0]['data']['size'] == bd.textures[0]['formats'][0]['data']['size']
        for i in new))
    pkg = bd.pkg
    template = pkg.exports[0]['meta']
    first = struct.unpack_from('<i', template, 84)[0]
    want = pkg.preload[first:first + sum(struct.unpack_from('<4i', template, 88))]
    ok = True
    for i in new:
        m = pkg.exports[i]['meta']
        f, counts = struct.unpack_from('<i', m, 84)[0], struct.unpack_from('<4i', m, 88)
        ok &= counts == struct.unpack_from('<4i', template, 88) and \
            pkg.preload[f:f + sum(counts)] == want
    check('each new atlas loads as the shipped ones do', ok)
    reg = pkg.exports[bd.registry_index]['meta']
    f, counts = struct.unpack_from('<i', reg, 84)[0], struct.unpack_from('<4i', reg, 88)
    before_ser = pkg.preload[f + counts[0]:f + counts[0] + counts[1]]
    check('the registry is created after every atlas', all(i + 1 in before_ser for i in new))
    dims = bd.textures[0]['formats'][0]['data']['mips'][0]['dims']
    rects = []
    for c in comps:
        lm = c['lightmap']
        x0, y0, x1, y1 = lightcodec.allocation_rect(lm['coord_scale'], lm['coord_bias'],
                                                    dims[0], dims[1] // 2)
        rects.append((lm['textures'][0], x0, y0, x1 - x0))
    check('each receiver\'s rectangle is square, padded and inside its atlas', all(
        s >= lightalloc.MIN_RESOLUTION and x >= 0 and y >= 0 and x + s <= dims[0]
        and y + s <= dims[1] // 2 for _t, x, y, s in rects))
    check('no two receivers share texels', not overlaps(rects))
    env = lightmaps.BuiltData(*lightmaps.fetch(PERSISTENT + '_Environment_BuiltData'))
    shipped = env.registry['lights'][0][1]
    ours = [d for g, d in bd.registry['lights']
            if g in {bytes.fromhex(l['guid']) for l in lights}]
    check('each added Static light has LightBuildData as the shipped ones',
          len(ours) == len(lights) and all(d == shipped for d in ours), len(ours))
    blobs = bd.write()
    back = lightmaps.BuiltData(*blobs)
    check('the BuiltData writes and reads back',
          len(back.registry['meshes']) == len(bd.registry['meshes'])
          and len(back.textures) == len(bd.textures), len(back.registry['meshes']))
    return lights


def applying(rs, lights):
    from riftlord.build import apply
    from riftlord.build import levelbuild
    from riftlord.core import gamefiles
    from riftlord.mapeditor import fabemit
    ua, ux = gamefiles.package(PERSISTENT)
    was = lightbake._bake_now
    lightbake._bake_now = True
    try:
        out_a, out_x, report = apply.apply_plan(ua, ux, LEVEL, '/Game/Mods/T/Maps/T', 'T', PLAN)
    finally:
        lightbake._bake_now = was
    b = levelbuild.Builder.around(out_a, out_x, name='T')
    pkg = b.pkg
    want = {bytes.fromhex(r['guid']) for r in rs}
    named = {}
    for i, e in enumerate(pkg.exports):
        if apply._class_at(pkg, i) != 'StaticMeshComponent':
            continue
        data = e['data']
        at, count = fabemit.lod_data(data, b._terminator(data) + 8)
        ids = {bytes(data[at + 4 + k * 18 + 2:at + 4 + k * 18 + 18]) for k in range(count)}
        if ids & want:
            named[i] = ids
    check('every receiver id is named by a component of the level copy',
          len(named) == len(want) and all(len(v) == 1 for v in named.values()),
          '%d of %d' % (len(named), len(want)))
    check('the report counts them', report.get('light_receivers') == len(named),
          report.get('light_receivers'))
    statics = [i for i in named if lightalloc._mobility(b, i + 1) not in (None, lightalloc.STATIC)]
    check('none of them is Movable', not statics, statics)
    got = {}
    for i, e in enumerate(pkg.exports):
        if apply._class_at(pkg, i) in lightalloc.LIGHT_CLASSES:
            span = b._tag_span(bytearray(e['data']), 'LightGuid')
            if span:
                got[bytes(e['data'][span[2]:span[2] + 16])] = i
    check('each light added_lights bakes is a light of the level copy',
          lights and all(bytes.fromhex(l['guid']) in got for l in lights), len(lights))
    point = [l for l in lights if l['export'] == 3]
    check('the added PointLight bakes where it is placed',
          len(point) == 1 and np.allclose(point[0]['position'], (0.0, 0.0, 300.0), atol=1.0),
          point and point[0]['position'])
    out_a, out_x, _report = apply.apply_plan(ua, ux, LEVEL, '/Game/Mods/T/Maps/T', 'T', PLAN)
    b = levelbuild.Builder.around(out_a, out_x, name='T')
    root = b._root_component(b.index_of('RiftLordMesh_0'))
    data = b.pkg.exports[root - 1]['data']
    _at, count = fabemit.lod_data(data, b._terminator(data) + 8)
    check('without a bake an added mesh is Movable with no LODData, as before',
          count == 0 and lightalloc._mobility(b, root) == fabemit.PIECE_MOBILITY,
          (count, lightalloc._mobility(b, root)))


def run():
    packing()
    rs = receivers()
    lights = extending(rs)
    applying(rs, lights)
    bad = [n for n, ok in checks if not ok]
    print('%d of %d checks passed' % (len(checks) - len(bad), len(checks)))
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(run())
