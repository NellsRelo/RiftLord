"""Offline checks on vertex paint (riftlord/build/world/vertexpaint.py) and the viewport's
weathering descriptions (riftlord/mapeditor/surfacelook.py): a level placement painted by
the plan carries one colour on every LOD of its mesh, its lightmap Guid kept; a placement
that shipped painted is repainted; foliage and removed pieces are left alone; the plan
keeps the paint when saved; the rock, wall and prop masters are described as their
shaders blend.

    python3 test_vertexpaint.py
"""
import os
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from riftlord.build import apply as applier  # noqa: E402
from riftlord.build.navigation import navpatch  # noqa: E402
from riftlord.build.world import vertexpaint  # noqa: E402
from riftlord.formats.omd3 import omd3level  # noqa: E402
from riftlord.formats.ue.uassetio import Package  # noqa: E402
from riftlord.mapeditor import leveldata, surfacelook  # noqa: E402
from riftlord.terrainkit import sources  # noqa: E402

LAVA, ICE = 'Scenario_06', 'Scenario_Ice_02'
#: The Lava Pits: an unpainted Big_Stone_03_A in _Environment, and a foliage piece
ROCK, FOLIAGE = 1835, 2318
BLUE = [0.1, 0.3, 1.0, 1.0]
checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


def tail_of(level_pkg, export):
    info, uexp, hdr = omd3level._load(level_pkg)
    a, b = sources._tail(info, uexp, hdr, export - 1)
    return uexp[a:b]


rock = next(p for p in leveldata.level(LAVA)['pieces'] if p['id'] == ROCK)
counts = vertexpaint.lod_vertices(rock['mesh'])
check('Big_Stone_03_A has four LODs', counts == [3302, 1779, 973, 541], counts)

env = leveldata.level(LAVA)['subPackages']['Environment']
old = tail_of(env, rock['export'])
have, end = vertexpaint.entries(old)
check('the shipped placement carries LOD 0 only, unpainted',
      len(have) == 1 and have[0][1] & 1 and end == len(old), have)
new = vertexpaint.painted_tail(old, counts, BLUE)
got, end2 = vertexpaint.entries(new)
check('painted, it carries every LOD, its LOD 0 Guid kept, the rest zero',
      len(got) == 4 and got[0][2] == have[0][2] and all(g[2] == b'\0' * 16 for g in got[1:])
      and end2 == len(new), [(g[0], g[1]) for g in got])
cols = sources.override_colours(new, 0, len(new))
check('LOD 0 reads back blue on every vertex',
      cols is not None and len(cols) == 3302 and (cols == [26, 76, 255, 255]).all(),
      None if cols is None else cols[:2].tolist())

ice_pkg = leveldata.level(ICE)['subPackages']['persistent']
ice_rock = next(p for p in leveldata.level(ICE)['pieces']
                if p['mesh'] == 'Big_Stone_03_A' and p.get('sub') == 'persistent')
shipped = tail_of(ice_pkg, ice_rock['export'])
again = vertexpaint.painted_tail(shipped, counts, BLUE)
check('a placement that shipped painted is repainted, same size',
      len(again) == len(shipped) and
      (sources.override_colours(again, 0, len(again)) == [26, 76, 255, 255]).all())

plan = {'level': LAVA, 'removed': [], 'look': {'vertexPaint': {
    str(ROCK): BLUE, str(FOLIAGE): BLUE, '999999': BLUE}}}
edits = vertexpaint.level_edits(LAVA, plan)
check('the plan paints the rock in _Environment, not the foliage',
      edits == {'Environment': {rock['export']: (BLUE, 'Big_Stone_03_A')}}, edits)
check('a removed rock is not painted',
      vertexpaint.level_edits(LAVA, dict(plan, removed=[ROCK])) == {})
sub = applier.sublevel_edits(LAVA, plan)
check('the sublevel edits carry it', sub.get('Environment', {}).get('vertexPaint')
      == {rock['export']: (BLUE, 'Big_Stone_03_A')}, sorted(sub))

files = navpatch.fetch_map(env, tempfile.mkdtemp(prefix='vpaint-', dir='/mnt/2tb_pro_a/tmp'))
with open(files['.umap'], 'rb') as fh:
    ua = fh.read()
with open(files['.uexp'], 'rb') as fh:
    ux = fh.read()
out_a, out_x, report = applier.apply_to_sublevel(ua, ux, 'Test_Environment', sub['Environment'])
check('the sublevel build notes it', any('vertex painted' in n for n in report['notes'])
      and not report['skipped'], report)
pkg = Package(out_a, out_x)
pkg.load_payloads()
data = bytes(pkg.exports[rock['export'] - 1]['data'])
before = Package(ua, ux)
before.load_payloads()
grown = len(data) - len(bytes(before.exports[rock['export'] - 1]['data']))
check('the written package grows by the four colour buffers',
      grown == sum(counts) * 4 + 3 * (2 + 16 + 1 + 2 + 16) + (1 + 2 + 16), grown)
check('no other export changed size',
      all(len(bytes(a['data'])) == len(bytes(b['data']))
          for i, (a, b) in enumerate(zip(pkg.exports, before.exports)) if i != rock['export'] - 1))

was = leveldata.PLANS
leveldata.PLANS = tempfile.mkdtemp(prefix='vpaint-plans-', dir='/mnt/2tb_pro_a/tmp')
try:
    leveldata.save_plan(LAVA, dict(leveldata.empty_plan(LAVA), look={'vertexPaint': {
        str(ROCK): [2, 0.5, -1, 1], 'x': 'bad'}}, added=[
        {'kind': 'mesh', 'name': 'Big_Stone_03_A', 'matrix': rock['matrix'], 'vertexPaint': BLUE}]))
    saved = leveldata.load_plan(LAVA)
finally:
    leveldata.PLANS = was
check('a saved plan keeps the paint, clamped',
      saved['look'].get('vertexPaint') == {str(ROCK): [1.0, 0.5, 0.0, 1.0]}
      and saved['added'][0].get('vertexPaint') == BLUE, (saved['look'], saved['added']))

d = surfacelook.describe('BigStone_03A_Lava_WS_INST')
check('a lava rock weathers as rock: snow on top and all over (1 - B), sand tinted by '
      'its own SandTint', d and d['family'] == 'Master_Terrain_Rock' and d['snow']['all'] == '1-b'
      and d['snow']['power'] == 10.0 and d['sand']['mpcColour'] is None, d)
d = surfacelook.describe('ColumnA_CastStone_INST')
check('a wall weathers with stone colour and sand by SandBlend', d and d['stone']
      and d['sand']['up'] == 'blend', d)
paint = surfacelook.paint(ICE)
check('Icebound Mine\'s paint reads for thousands of placements', len(paint) > 3000, len(paint))

bad = [c for c in checks if not c[1]]
for name, ok, detail in checks:
    print('%s %s%s' % ('ok  ' if ok else 'FAIL', name, '' if ok else ': %s' % (detail,)))
print('%d/%d' % (len(checks) - len(bad), len(checks)))
sys.exit(1 if bad else 0)
