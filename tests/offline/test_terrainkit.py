"""Offline checks on terrain kits (riftlord/terrainkit) and their Fab Kits rows:

* tiles: a flat tile on Floor_4x4's footprint (X/Y 0..1200, top Z 0, slab to -80) whose relief
  stays within the grid level and whose Fab grid analysis gives one 4 x 4 floor grid where
  Floor_4x4 has its; a ramp rising 600 toward -Y with one 4 x 4 slope grid; no stepped
  tiles; two tiles meeting on the lattice share their edge heights and
  paint; the window search over a synthetic ground; whole texture repeats (Front Lawn's 0.35);
* paint: the measured blend order of Master_Enviro_MeshPaint_Terrain and its weights;
* the library fingerprint: changes with a source, not with their order;
* full single-layer fills, plain layer names, base-layer texture repeats;
* Setup's terrain step against the stand-in builder (tests/ui_smoke/faketerrain.py): its own
  folder, needed, running with progress, done, listed by the Map Editor (unfinished ones not),
  needed again on a code change or a removed kit, a failure in the Setup pane, Retry;
* the Map Editor files a terrain library's pieces under the `terrain` kind;
* with the game installed: Front Lawn's ground found, its tiles' grids; the Sand and Ice
  landscapes found and cut, the Ice one not.

    python3 test_terrainkit.py [SCRATCH]
"""
import json
import math
import os
import shutil
import sys
import tempfile
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)

SCRATCH = sys.argv[1] if len(sys.argv) > 1 else None
ROOT = tempfile.mkdtemp(prefix='terrainkit-', dir=SCRATCH)
for name in ('XDG_DATA_HOME', 'XDG_CONFIG_HOME'):
    os.environ[name] = os.path.join(ROOT, name.split('_')[1].lower())
os.environ['RIFTLORD_FAKEFAB_STATE'] = os.path.join(ROOT, 'fakefab')
os.environ['RIFTLORD_FAKETERRAIN_STATE'] = os.path.join(ROOT, 'faketerrain')
os.environ['RIFTLORD_FABCONVERT'] = os.path.join(REPO, 'tests', 'ui_smoke', 'fakefab.py')
os.environ['RIFTLORD_TERRAINKIT'] = os.path.join(REPO, 'tests', 'ui_smoke', 'faketerrain.py')

from riftlord.fab import analyse as AN  # noqa: E402
from riftlord.terrainkit import build, paint, tiles  # noqa: E402

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


def wait(test, timeout=60.0):
    t = time.time()
    while time.time() - t < timeout:
        got = test()
        if got:
            return got
        time.sleep(0.05)
    return None


def grids(desc, role, coll=None):
    coll = dict(coll or {'boxes': [], 'convex': [], 'trace': 'complex_as_simple', 'double_sided': False})
    coll.pop('source', None)
    coll['collides'] = True
    return AN.analyse({'positions': desc['positions'], 'triangles': desc['triangles']}, role, coll, 1.0)['grids']


def window(seed=1, bumps=80.0):
    """A synthetic source window: heights (n, n) with relief, paint (n*n, 4)."""
    n = tiles.SAMPLES + 1
    rng = np.random.default_rng(seed)
    xs = np.arange(n) * tiles.step()
    gx, gy = np.meshgrid(xs, xs)
    z = 900 + 0.05 * gx + bumps * np.sin(gx / 200.0) * np.cos(gy / 260.0)
    plane = np.linalg.lstsq(np.c_[gx.ravel(), gy.ravel(), np.ones(n * n)], z.ravel(), rcond=None)[0]
    rgba = rng.integers(0, 256, (n * n, 4)).astype(np.float64)
    return z, rgba, plane


def edge(desc, axis, value):
    P = np.asarray(desc['positions'], np.float64)
    sel = np.isclose(P[:, axis], value) & (P[:, 2] > -tiles.SLAB + 1)
    return P[sel], np.asarray(desc['colors'])[sel]


try:
    border = np.array([250.0, 240.0, 170.0, 230.0])
    uvk = 2.857 / tiles.TILE
    # 1. the flat tile
    z, rgba, plane = window()
    flat = tiles.surface_tile('flat', z, rgba, plane, border, uvk)
    P = np.asarray(flat['positions'], np.float64)
    check('flat: Floor_4x4 footprint X/Y 0..1200, slab to -80',
          np.allclose(P.min(0)[:2], 0) and np.allclose(P.max(0)[:2], tiles.TILE) and
          np.isclose(P[:, 2].min(), -tiles.SLAB), (P.min(0), P.max(0)))
    tops = P[P[:, 2] > -tiles.SLAB + 1]
    check('flat: top at most 0 and relief within RELIEF_DEPTH (< analyse.LEVEL_TOL)',
          tops[:, 2].max() <= 1e-4 and tops[:, 2].min() >= -tiles.RELIEF_DEPTH - 1e-4
          and tiles.RELIEF_DEPTH < AN.LEVEL_TOL, (tops[:, 2].min(), tops[:, 2].max()))
    rim = np.minimum.reduce([tops[:, 0], tiles.TILE - tops[:, 0], tops[:, 1], tiles.TILE - tops[:, 1]]) < 1e-3
    check('flat: every edge at Z 0', np.allclose(tops[rim][:, 2], 0.0), tops[rim][:, 2].min())
    g = grids(flat, 'floor')
    check('flat: one 4 x 4 floor grid where Floor_4x4 has its, at (600, 600, 0)',
          len(g) == 1 and g[0]['kind'] == 'floor' and g[0]['cells'] == [4.0, 4.0]
          and np.allclose(g[0]['center'], [600, 600, 0], atol=0.5), [(x['kind'], x['cells'], x['center']) for x in g])
    T = np.asarray(flat['triangles'])
    fn = np.cross(P[T[:, 2]] - P[T[:, 0]], P[T[:, 1]] - P[T[:, 0]])
    check('flat: the top faces up and nothing faces down (no underside grids)',
          (fn[:, 2] > 0).sum() == 2 * tiles.SAMPLES ** 2 and not (fn[:, 2] < -1e-6).any())
    # 2. tiles meet: a second tile beside the first shares its edge heights and paint
    z2, rgba2, plane2 = window(seed=7, bumps=150.0)
    other = tiles.surface_tile('flat', z2, rgba2, plane2, border, uvk)
    a, ca = edge(flat, 0, tiles.TILE)
    b, cb = edge(other, 0, 0.0)
    oa, ob = np.lexsort((a[:, 2], a[:, 1])), np.lexsort((b[:, 2], b[:, 1]))
    check('two flat tiles side by side: the same edge heights and edge paint',
          np.allclose(a[oa][:, 1:], b[ob][:, 1:]) and np.allclose(ca[oa], cb[ob], atol=1),
          (len(a), len(b)))
    uv = np.asarray(flat['uvs'][0])
    check('UV0: whole texture repeats per tile at TileSize 0.35',
          abs(uv[:, 0].max() * 0.35 - round(uv[:, 0].max() * 0.35)) < 0.01, uv[:, 0].max())
    # 3. the ramp
    ramp = tiles.surface_tile('ramp', z, rgba, plane, border, uvk)
    lo, _c = edge(ramp, 1, tiles.TILE)
    hi, _c = edge(ramp, 1, 0.0)
    check('ramp: Z 0 at Y 1200 and Z 600 at Y 0 (climbs toward -Y like Stairs_4x_4_SB)',
          np.isclose(lo[:, 2].max(), 0) and np.isclose(hi[:, 2].max(), tiles.RISE), (lo[:, 2].max(), hi[:, 2].max()))
    g = grids(ramp, 'floor')
    sg = [x for x in g if x['kind'] == 'slope_floor']
    steep = tiles.ramp_steepest_deg()
    check('ramp: one 4 x 4 slope grid, tilted as the ramp middle',
          len(sg) == 1 and sg[0]['cells'] == [4.0, 4.0] and abs(sg[0]['tilt_deg'] - steep) < 0.5,
          [(x['kind'], x['cells'], x.get('tilt_deg')) for x in g])
    # 3b. rounded ends: level with the floor at the foot and the top, joins flush, grid fit, walkable
    yy = np.linspace(0.0, tiles.TILE, 4801)
    prof = tiles.ramp_profile(yy)
    slope = np.gradient(prof, yy)
    check('ramp profile: 0 at the foot, RISE at the top, slope 0 at both (tangent to the floor)',
          np.isclose(prof[0], 0) and np.isclose(prof[-1], tiles.RISE) and abs(slope[0]) < 0.01
          and abs(slope[-1]) < 0.01 and tiles.RAMP_FILLET > 0, (prof[0], prof[-1], slope[0], slope[-1]))
    check('ramp profile: smooth (no kink) and never steeper than its middle',
          np.abs(np.diff(slope)).max() < 0.01 and slope.max() <= math.tan(math.radians(steep)) + 1e-6,
          (np.abs(np.diff(slope)).max(), slope.max()))
    flat_ramp = tiles.surface_tile('ramp', z, None, plane, None, uvk, relief_depth=0.0)
    Pr = np.asarray(flat_ramp['positions'], np.float64)
    Nr = np.asarray(flat_ramp['normals'], np.float64)
    top = Pr[Nr[:, 2] > 0.5]
    near_foot = top[top[:, 1] >= tiles.TILE - tiles.RAMP_ROW - 1e-3]
    near_top = top[top[:, 1] <= tiles.RAMP_ROW + 1e-3]
    in_row = float(tiles.ramp_profile(tiles.RAMP_ROW))
    check('ramp mesh: the first row in from each end sits on the profile, under a fifth of an unrounded '
          "ramp's height there (it leaves the floor level)",
          np.allclose(near_foot[:, 2][near_foot[:, 1] < tiles.TILE - 1e-3], in_row, atol=1e-3)
          and np.allclose(tiles.RISE - near_top[:, 2][near_top[:, 1] > 1e-3], in_row, atol=1e-3)
          and in_row < 0.2 * tiles.RISE * tiles.RAMP_ROW / tiles.TILE, in_row)
    ea, _c = edge(ramp, 0, tiles.TILE)
    eb, _c = edge(tiles.surface_tile('ramp', z2, rgba2, plane2, border, uvk), 0, 0.0)
    oa, ob = np.lexsort((ea[:, 2], ea[:, 1])), np.lexsort((eb[:, 2], eb[:, 1]))
    check('two ramps side by side: the same side-edge profile',
          len(ea) == len(eb) and np.allclose(ea[oa][:, 1:], eb[ob][:, 1:], atol=1e-3)
          and np.allclose(ea[:, 2][ea[:, 2] > 0], tiles.ramp_profile(tiles.TILE - ea[:, 1][ea[:, 2] > 0]), atol=1e-3),
          (len(ea), len(eb)))
    cells, gap = tiles.slope_grid_gap(flat_ramp)
    check('ramp: its 4 x 4 slope grid lies within RAMP_GRID_TOL (analyse.LEVEL_TOL) of the top',
          cells == [4.0, 4.0] and gap <= tiles.RAMP_GRID_TOL and tiles.RAMP_GRID_TOL == AN.LEVEL_TOL, (cells, gap))
    from riftlord.build.navigation import recastgen
    check('ramp: steepest slope walkable (analyse slope grids and AgentMaxSlope 45, recastgen 60) '
          'and steeper than the shipped 1 in 2 only by the rounding',
          steep <= AN.SLOPE_FLOOR_MAX_DEG and steep <= recastgen.MAX_SLOPE_DEG and 26.57 < steep < 30.0, steep)
    check('no stepped tiles: the tile kinds are flat and ramp only',
          sorted(build.ROLES) == ['flat', 'ramp'] and not hasattr(tiles, 'stair_tile'))
    # 5. the window search
    n = 65
    xs = np.arange(n) * tiles.step()
    gx, gy = np.meshgrid(xs, xs)
    Pg = np.c_[gx.ravel(), gy.ravel(), (20 * np.sin(gx / 400.0)).ravel()]
    idx = np.arange(n * n).reshape(n, n)
    Tg = np.concatenate([np.c_[idx[:-1, :-1].ravel(), idx[1:, :-1].ravel(), idx[:-1, 1:].ravel()],
                         np.c_[idx[:-1, 1:].ravel(), idx[1:, :-1].ravel(), idx[1:, 1:].ravel()]])
    Cg = np.zeros((n * n, 4))
    Cg[:, 0] = (gx.ravel() > 2400) * 255
    r = tiles.raster(Pg, Tg, Cg)
    ws = tiles.windows(r)
    span = (n - 1) * tiles.step()
    want = int((span - tiles.TILE) / tiles.WINDOW_STRIDE + 1) ** 2
    check('windows: every tile-sized window of a 4800 uu ground, every 300 uu', len(ws) == want, (len(ws), want))
    layers = [{'name': 'Grass', 'channel': None}, {'name': 'Dirt', 'channel': 0},
              {'name': 'Overgrown', 'channel': 1}, {'name': 'Rock', 'channel': 2}, {'name': 'Grass', 'channel': 3}]
    feat = lambda w: paint.weights(tiles.window_paint(r, w), layers).mean(0)  # noqa: E731
    got = tiles.choose(ws, 2, feat, 0)
    check('choose: the second window differs in paint and does not overlap the first',
          feat(got[1])[1] > 0.5 and feat(got[0])[1] < 0.5, [feat(w).round(2).tolist() for w in got])
    # 6. texture repeats
    span, seam = tiles.uv_span(4.0, [0.35] * 8)
    check("uv_span: Front Lawn's 4 UV per tile at TileSize 0.35 -> 1/0.35, no seam",
          abs(span - 1 / 0.35) < 1e-6 and seam < 1e-9, (span, seam))
    span, seam = tiles.uv_span(4.0, [0.1, 0.1, 0.35, 0.35])
    check('uv_span: TileSizes 0.1 and 0.35 (no whole repeats of both in range) -> the nearest '
          'whole repeat of one, its seam reported',
          abs(span - 1 / 0.35) < 1e-6 and abs(seam - abs(span * 0.1 - round(span * 0.1))) < 1e-9, (span, seam))
    # 7. paint
    check('paint: the measured blend of Master_Enviro_MeshPaint_Terrain (BC_1; R BC_2; G BC_3; B BC_4; A BC_1 tinted)',
          paint.BLENDS[paint.MASTER] == (('BC_1', None, None), ('BC_2', 0, 'BC_2_Color'), ('BC_3', 1, 'BC_3_Color'),
                                         ('BC_4', 2, 'BC_4_Color'), ('BC_1', 3, 'BC_1_Color')))
    w = paint.weights(np.array([[0, 0, 0, 0], [255, 0, 0, 0], [255, 255, 0, 0], [0, 0, 255, 255]]), layers)
    check('paint: weights of the successive lerp',
          np.allclose(w, [[1, 0, 0, 0, 0], [0, 1, 0, 0, 0], [0, 0, 1, 0, 0], [0, 0, 0, 0, 1]]), w)
    check('paint: a layer name from its texture', paint.layer_name('/Game/X/Set1_Overgrown_Wet_01_BC') == 'Overgrown Wet')
    # 8. fingerprint
    orig = build.sources.fingerprint_parts
    build.sources.fingerprint_parts = lambda s: s['bytes']
    a1 = build.fingerprint([{'package': 'A', 'component': 1, 'bytes': 'x'}, {'package': 'B', 'component': 2, 'bytes': 'y'}])
    a2 = build.fingerprint([{'package': 'B', 'component': 2, 'bytes': 'y'}, {'package': 'A', 'component': 1, 'bytes': 'x'}])
    a3 = build.fingerprint([{'package': 'A', 'component': 1, 'bytes': 'x'}, {'package': 'B', 'component': 2, 'bytes': 'z'}])
    build.sources.fingerprint_parts = orig
    check('fingerprint: the same for the same sources in any order, another when one changes',
          a1 == a2 and a1 != a3)
    # 9. the Map Editor's kinds
    from riftlord.mapeditor import fablibrary
    check('Map Editor: a terrain library piece is filed under terrain only',
          fablibrary.kinds_of({'role': 'floor'}, 'terrain') == ['terrain'] and 'terrain' in fablibrary.KINDS
          and 'architecture' in fablibrary.kinds_of({'role': 'floor'}))
    # 10. full single-layer tiles and plain names
    fills = build.full_fills(layers)
    check('full tiles: one fill per layer name, the topmost (Grass by A), each alone at weight 1',
          [n for n, _c in fills] == ['Dirt', 'Overgrown', 'Rock', 'Grass']
          and all(np.allclose(paint.weights(np.array([c]), layers).sum(0)[[i for i, l in enumerate(layers)
                                                                          if l['name'] == n]].sum(), 1.0)
                  for n, c in fills), fills)
    check('layer names: Lava Pits and Ice textures read plainly',
          [paint.layer_name(t) for t in ('TEX_LSC_Ground_SandA_col', 'Dirt_RedRock_01_BC', 'Snow_02_BC')]
          == ['Sand', 'Dirt Red Rock', 'Snow'])
    span, seam = tiles.uv_span(12.0, [0.05, 0.04, 0.15])
    check('uv_span: when no span repeats every layer whole, the base layer (first) repeats whole',
          abs(span * 0.05 - round(span * 0.05)) < tiles.UV_WHOLE_TOL, (span, seam))
    from riftlord.mapeditor import scratch
    check('Scratch levels walk a terrain ramp as a slope and a terrain flat as floor',
          scratch._walkable(scratch._fab_row({'role': 'floor', 'geometry': {'terrain': {'kind': 'ramp'}}})) == 'slope'
          and scratch._walkable(scratch._fab_row({'role': 'floor', 'geometry': {'terrain': {'kind': 'flat'}}})) == 'floor'
          and scratch._fab_row(None) is None)
    # 11. Setup's terrain step (riftlord.terrainkit.setup) with the stand-in builder
    from riftlord.terrainkit import setup as tsetup
    from riftlord.types import setup as types_setup
    os.environ['RIFTLORD_TERRAIN_LIBRARY'] = TLIB = os.path.join(ROOT, 'terrainkits')
    # the type-data steps read a folder of the test's own: never the gate's prepared data,
    # nor the user's cache, so no failure another run left reaches the pane
    os.environ[types_setup.ENV] = os.path.join(ROOT, 'types')
    types_setup._NEEDED['at'] = 0.0
    # the Setup pane's line when the type data cannot be built here (no game or UE 4.27)
    types_missing = types_setup.missing_message()

    def pane_shows(st, line):
        """`st` is Setup failed with `line` as its error, its type-data steps not failed;
        or, when the type data cannot be built here, Setup unavailable."""
        if any(s['state'] == 'failed' for s in st['steps'][:-1]):
            return False
        if types_missing:
            return st['state'] == 'unavailable'
        return st['state'] == 'failed' and bool(line) and st['error'] == line
    os.makedirs(TLIB, exist_ok=True)
    check('Setup: the terrain kits live in their own folder, not the Fab library',
          fablibrary.terrain_root() == TLIB and fablibrary.root() != TLIB)
    check('Setup: before the first build the step is needed and waiting',
          tsetup.needed(fresh=True) and tsetup.step()['state'] == 'waiting')
    tsetup.start()
    run = wait(lambda: tsetup.step()['state'] == 'running' and tsetup.step(), 10)
    check('Setup: the step runs in its own process with progress', run and 0 <= run['progress'] <= 1, run)
    done = wait(lambda: not tsetup.running() and tsetup.step()['state'] == 'done' and tsetup.step())
    check('Setup: the step ends done', done, tsetup.step())
    libs = {l['id']: l['status'] for l in fablibrary.libraries()}
    check('Map Editor: the finished terrain kits are listed beside the Fab ones',
          all(libs.get('Terrain_' + m) == 'ready' for m in ('Scenario_09', 'ScenarioDLC_03', 'Scenario_18')), libs)
    os.makedirs(os.path.join(TLIB, 'Terrain_Unfinished'))
    check('Map Editor: a terrain kit not built yet is not listed',
          'Terrain_Unfinished' not in {l['id'] for l in fablibrary.libraries()})
    check('Setup: once built, not needed', not tsetup.needed(fresh=True))
    orig_hash = build.code_hash
    build.code_hash = lambda: 'changed'
    check('Setup: a change to the terrain kit code makes it needed again', tsetup.needed(fresh=True))
    build.code_hash = orig_hash
    shutil.rmtree(os.path.join(TLIB, 'Terrain_Scenario_09'))
    check('Setup: a removed kit makes it needed again', tsetup.needed(fresh=True))
    os.environ['RIFTLORD_FAKETERRAIN_FAIL'] = '1'
    tsetup.start()
    wait(lambda: not tsetup.running() and tsetup.step()['state'] == 'failed')
    st = types_setup.status()
    row = st['steps'][-1]
    check('Setup pane: a failed terrain build shows as Setup failed with its line, Terrain last',
          row['label'] == 'Terrain' and row['state'] == 'failed' and
          (tsetup.failed() or '').startswith('Terrain') and pane_shows(st, tsetup.failed()),
          (st['state'], st['error'], st['steps']))
    os.environ.pop('RIFTLORD_FAKETERRAIN_FAIL')
    tsetup.start()
    check('Setup: Retry builds again and ends done',
          wait(lambda: not tsetup.running() and tsetup.step()['state'] == 'done'), tsetup.step())
    orig_key = tsetup.key

    def broken_key():
        raise FileNotFoundError('sources.py is not in the bundle')
    tsetup.key = broken_key
    tsetup.needed(fresh=True)
    row, st = tsetup.step(), types_setup.status()
    check('Setup: a key() failure shows Terrain failed with the reason, and Setup failed, not waiting',
          row['state'] == 'failed' and 'not in the bundle' in (tsetup.failed() or '') and
          pane_shows(st, tsetup.failed()),
          (row, st['state'], st['error'], st['steps']))
    tsetup.key = orig_key
    check('Setup: once key() works again the row is done',
          tsetup.needed(fresh=True) is False and tsetup.step()['state'] == 'done' and tsetup.failed() is None,
          (tsetup.step(), tsetup.failed()))
    # 12. with the game: Front Lawn, Lava Pits, Desert Wall's sand, Frostbitten's snow
    try:
        from riftlord.core import gamefiles
        gamefiles.index()
        have_game = True
    except Exception:                                # noqa: BLE001
        have_game = False
    if have_game:
        from riftlord.terrainkit import sources
        src = sources.discover_map('Scenario_09')
        check("Front Lawn: one painted ground mesh (Scenario_09_Terrain, 5708 colours)",
              [(s['mesh'], s['vertices']) for s in src] == [('Scenario_09_Terrain', 5708)], src)
        made = build.pieces(src)
        kinds = [p['geometry']['terrain']['kind'] for p in made]
        check('Front Lawn: 6 flat, 2 ramp and 4 full tiles (Dirt, Overgrown, Rock, Grass), no stairs',
              kinds.count('flat') == 6 and kinds.count('ramp') == 2 and kinds.count('full') == 4
              and len(kinds) == 12, [p['display_name'] for p in made])
        check('Front Lawn: every tile offers one full 4 x 4 grid on its top',
              all(tiles.one_full_grid(p['desc'], 'ramp' if p['geometry']['terrain']['kind'] == 'ramp'
                                      else 'flat') for p in made))
        gaps = [tiles.slope_grid_gap(p['desc']) for p in made if p['geometry']['terrain']['kind'] == 'ramp']
        check('Front Lawn: each rounded ramp, relief included, lies within RAMP_GRID_TOL of its 4 x 4 slope grid',
              gaps and all(c == [4.0, 4.0] and g <= tiles.RAMP_GRID_TOL for c, g in gaps), gaps)
        lava = build.pieces(sources.discover_map('Scenario_06'))
        names = sorted(p['display_name'] for p in lava)
        check("Lava Pits: its own red dirt and sand layers (LavaDesertTest1_MAT), full tiles of each",
              'Full Dirt Red Ground 4x4' in names and 'Full Sand Ground 4x4' in names
              and all(p['materials'][0]['path'].endswith('LavaDesertTest1_MAT') for p in lava), names)
        sand = [s for s in sources.discover_map('Scenario_Sand_01') if s['kind'] == 'landscape']
        snow = [s for s in sources.discover_map('Scenario_Ice_03') if s['kind'] == 'landscape']
        check('Sand and Frostbitten ice landscapes found as sources',
              len(sand) == 1 and len(snow) == 2, (sand, snow))
        cut = build.pieces(sand)
        look = [p for p in cut if 'mic' not in p['materials'][0]]
        paintable = [p for p in cut if p['materials'][0].get('mic') == 'MI_Terrain_Sand']
        check('Desert sand: Sand Ground and Sand Ramp tiles with the landscape material, no vertex colours',
              sorted(p['display_name'] for p in look) == ['Sand Ground 4x4', 'Sand Ground 4x4 B',
                                                          'Sand Ramp 4x4', 'Sand Ramp 4x4 B']
              and all(p['desc']['colors'] is None for p in look), [p['display_name'] for p in look])
        check('Desert sand: paintable tiles on MI_Terrain_Sand, vertex colours, a full tile per layer',
              len(paintable) == 8 and all(p['desc']['colors'] is not None for p in paintable)
              and {'Full Sand Detail Ground 4x4', 'Full Dirt Sand Ground 4x4', 'Full Rock Ground 4x4'}
              <= {p['display_name'] for p in paintable}, [p['display_name'] for p in paintable])
        frost = build.pieces(snow)
        check('Frostbitten: snow tiles on MI_Terrain_Snow, none on the ice landscape material',
              frost and all(p['materials'][0].get('mic') == 'MI_Terrain_Snow' for p in frost)
              and 'Snow Ground 4x4' in {p['display_name'] for p in frost}, [p['display_name'] for p in frost])
        check('Frostbitten: its library carries MI_Terrain_Snow only; the build id counts it',
              [m['name'] for m in build.carried_specs(snow)] == ['MI_Terrain_Snow']
              and build.build_id(snow) != build.build_id([]), build.carried_specs(snow))
        from riftlord.fab import library
        from riftlord.fab import micwrite
        from riftlord.terrainkit import mics
        own = '/Game/Mods/Fab/Terrain_Test/B00000000'
        for name in sorted(mics.INSTANCES):
            path = '%s/Materials/%s' % (own, name)
            blobs, expect = library.write_carried_mic(mics.spec(name), path)
            got = micwrite.mic_values(blobs, path)
            check('%s: written by ExpressionGUID, reads back with every GUID and texture' % name,
                  not library.carried_mic_problems(blobs, path, expect, own)
                  and got['textures']['BC_1'] == mics.INSTANCES[name]['BC_1']['BC']
                  and got['textures']['BC_4'] == mics.INSTANCES[name]['BC_4']['BC']
                  and got['parent'] == paint.MASTER, got)
        wrong = dict(mics.spec('MI_Terrain_Snow'))
        wrong['textures'] = [('MRH', g, t) if k == 0 else (n, g, t) for k, (n, g, t) in enumerate(wrong['textures'])]
        try:
            library.write_carried_mic(wrong, own + '/Materials/Wrong')
            refused = False
        except SystemExit as e:
            refused = 'ExpressionGUID' in str(e)
        check('write_carried_mic: a name that disagrees with its ExpressionGUID is refused', refused)
    else:
        print('SKIP  the game is not installed: map checks')
finally:
    try:
        from riftlord.app import fabkits as F
        F.stop_all()
    except Exception:                                # noqa: BLE001
        pass
    time.sleep(0.2)
    shutil.rmtree(ROOT, ignore_errors=True)

bad = [c for c in checks if not c[1]]
for name, ok, detail in checks:
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, '' if ok else '  [%s]' % (detail,)))
print('%d/%d passed' % (len(checks) - len(bad), len(checks)))
sys.exit(1 if bad else 0)
