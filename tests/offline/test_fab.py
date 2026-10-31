"""Offline checks on the Fab converter (riftlord/fab): the scale rule against the 15 decided
packs, the build-id logic hash, progress events, the per-item lock, vault discovery on a fake
vault, delete refusing a locked item, the vectorised depth map against the per-triangle loop,
the preflight facts, and the CLI's JSON. Needs no vault, no game and no pxcook.

    python3 test_fab.py
"""
import contextlib
import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from riftlord.fab import library  # noqa: E402
from riftlord.fab import preflight  # noqa: E402
from riftlord.fab import vault  # noqa: E402
from riftlord.fab.__main__ import main as fab_main  # noqa: E402

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


TMP = tempfile.mkdtemp(prefix='fab-test-')
library.cache_root = lambda: os.path.join(TMP, 'cache')       # every cache and lock in scratch

# 1. the scale rule reproduces every decided pack scale
records = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fab_scale_records.json')))
wrong = [(n, r['decided'], vault.choose(r)[0]) for n, r in sorted(records.items()) if vault.choose(r)[0] != r['decided']]
check('scale rule: %d decided packs reproduced' % len(records), not wrong, wrong)
check('scale rule: evidence names the choice', vault.choose(records['Medieval_Dock'])[1][-1] == 'chosen x1.5')

# 2. the build id follows logic, not imports, docstrings or path set-up
src = os.path.join(TMP, 'm1.py')
with open(src, 'w') as fh:
    fh.write('"""doc"""\nimport os\nHERE = os.path.dirname(__file__)\n\ndef f(x):\n    import json\n    return x + 1\n')
alt = os.path.join(TMP, 'm2.py')
with open(alt, 'w') as fh:
    fh.write('"""other doc"""\nfrom riftlord.core import gamepath\nHERE = "/elsewhere"\n\ndef f(x):\n    return x + 1\n')
changed = os.path.join(TMP, 'm3.py')
with open(changed, 'w') as fh:
    fh.write('import os\n\ndef f(x):\n    return x + 2\n')
check('logic hash: imports, docstrings and HERE ignored', library.logic_hash(src) == library.logic_hash(alt))
check('logic hash: a logic change moves it', library.logic_hash(src) != library.logic_hash(changed))

# 3. progress events: one JSON line per item, stages in order
class FakePool:
    def imap(self, fn, items, chunksize=1):
        return (fn(x) for x in items)

    def map(self, fn, items, chunksize=1):
        return [fn(x) for x in items]


err = io.StringIO()
with contextlib.redirect_stderr(err):
    got = library._pmap(FakePool(), lambda x: x * 2, [1, 2, 3], 'meshes', library.json_events)
events = [json.loads(l) for l in err.getvalue().splitlines()]
check('events: results in order', got == [2, 4, 6], got)
check('events: 0..3 of 3, stage meshes index %d of %d' % (library.STAGES.index('meshes'), len(library.STAGES)),
      [e['done'] for e in events] == [0, 1, 2, 3] and all(e['stage'] == 'meshes' and e['total'] == 3 for e in events)
      and events[0]['index'] == library.STAGES.index('meshes') and events[0]['stages'] == len(library.STAGES), events)
check('events: none without a callback', library._pmap(FakePool(), lambda x: x, [1], 'meshes', None) == [1])
err = io.StringIO()
expected = {s: 10.0 for s in library.STAGES}
prog = library._Progress(library.json_events, expected)
with contextlib.redirect_stderr(err):
    for s in library.STAGES:
        library._pmap(FakePool(), lambda x: x, [1, 2], s, prog)
events = [json.loads(l) for l in err.getvalue().splitlines()]
fr = [e['fraction'] for e in events]
check('events: fraction climbs 0 to 1 and every event has an eta',
      fr[0] == 0 and fr[-1] == 1 and fr == sorted(fr) and all(isinstance(e['eta_s'], int) and e['eta_s'] >= 0 for e in events), events)
prog.finish(TMP)
check('events: stage times recorded and expected next time',
      set(library._expected_seconds(TMP, 5)) == set(library.STAGES)
      and library._expected_seconds(os.path.join(TMP, 'nowhere'), 4)['meshes'] == library.STAGE_SECONDS_PER_MESH['meshes'] * 4)
check('vault: kinds', [vault.item_kind(c, l) for c, l in (
    ({'static': 1, 'skeletal': 1}, 0.0), ({'static': 81, 'skeletal': 4, 'animations': 39}, 0.95),
    ({'static': 4, 'particles': 61}, 0.25), ({}, 0.0), ({'static': 121}, 0.18), ({'static': 197}, 0.44),
    ({'static': 212}, 0.84))] == ['creature', 'building', 'none', 'none', 'props', 'mixed', 'building'])

# 4. the per-item lock
hold = vault.acquire('ItemA')
check('lock: taken', hold is not None)
check('lock: a second taker is refused', vault.acquire('ItemA') is None)
check('lock: running while held', vault.is_running('ItemA'))
hold.close()
check('lock: free once released', not vault.is_running('ItemA') and vault.acquire('ItemA') is not None)

# 5. vault discovery on a fake vault: one item, not an art pack, no library yet
vroot = os.path.join(TMP, 'vault')
item = os.path.join(vroot, 'FakePack0123V1')
os.makedirs(os.path.join(item, 'data', 'Content', 'Fake_Pack', 'Meshes'))
with open(os.path.join(item, 'vault.json'), 'w') as fh:
    json.dump({'title': 'Fake Pack'}, fh)
with open(os.path.join(item, 'manifest.json'), 'w') as fh:
    fh.write('{}')
os.environ[vault.VAULT_ENV] = vroot
check('vault: env root searched first', vault.candidate_roots()[0] == os.path.abspath(vroot))
roots, rows = vault.items(vroot, out_dir=os.path.join(TMP, 'lib'))
row = rows[0] if rows else {}
check('vault: the item is listed', len(rows) == 1 and row.get('title') == 'Fake Pack', rows)
check('vault: unknown pack, default scale 1.0, no meshes, no library',
      row.get('known') is False and row.get('scale') == 1.0 and row.get('scale_source') == 'default'
      and row.get('meshes') == 0 and row.get('library', {}).get('status') == 'none', row)
check('vault: the fingerprint is cached', os.path.exists(vault._info_path('FakePack0123V1.fp')))
_r, none = vault.find_roots(os.path.join(TMP, 'nowhere'))
check('vault: a missing root is reported as searched', not _r and none == [os.path.join(TMP, 'nowhere')])

# 6. delete refuses an item being converted
hold = vault.acquire('FakePack0123V1')
try:
    vault.delete('FakePack0123V1', vroot, out_dir=os.path.join(TMP, 'lib'))
    refused = False
except RuntimeError:
    refused = True
hold.close()
check('delete: refused while locked', refused)
gone = vault.delete('FakePack0123V1', vroot, out_dir=os.path.join(TMP, 'lib'))
check('delete: allowed once free', gone['item'] == 'FakePack0123V1' and gone['pack_id'] == 'FakePack')

# 6b. loose packs converted from a file stay listed (source 'file'), also after their source moves
loose = os.path.join(TMP, 'loose_pack')
os.makedirs(loose)
with open(os.path.join(loose, 'piece.fbx'), 'wb') as fh:
    fh.write(b'not read by this test')
lib = os.path.join(TMP, 'lib')
os.makedirs(os.path.join(lib, 'LoosePack'))
with open(os.path.join(lib, 'LoosePack', 'catalogue.json'), 'w') as fh:
    json.dump({'build': 'x', 'meshes': [{'status': 'converted'}], 'pak': 'LoosePack.pak'}, fh)
vault.record_import(loose, {'pack': {'id': 'LoosePack', 'title': 'Loose Pack', 'scale': 1.0}, 'fingerprint': 'f'}, lib)
rows = [r for r in vault.import_rows(lib) if r['pack_id'] == 'LoosePack']
check('imports: listed with source file and its library',
      len(rows) == 1 and rows[0]['source'] == 'file' and rows[0]['exists'] and rows[0]['library']['converted'] == 1, rows)
check('imports: another library does not list it', not vault.import_rows(os.path.join(TMP, 'other')))
os.rename(loose, loose + '_moved')
rows = vault.import_rows(lib)
check('imports: a moved source stays listed, exists false', len(rows) == 1 and rows[0]['exists'] is False, rows)
gone = vault.delete(loose, out_dir=lib)
check('imports: delete by the old path removes library and record',
      gone['pack_id'] == 'LoosePack' and not os.path.isdir(os.path.join(lib, 'LoosePack')) and not vault.import_rows(lib), gone)
check('imports: the record lives beside the library', os.path.exists(os.path.join(lib, vault.IMPORTS_FILE)))
old = os.path.join(library.cache_root(), vault.IMPORTS_FILE)
with open(old, 'w') as fh:
    json.dump({'/old/pack.zip': {'pack_id': 'OldPack', 'title': 'Old', 'scale': 1.0, 'fingerprint': 'f',
                                 'stat': [1, 1], 'library': os.path.abspath(lib)}}, fh)
check('imports: an old cache copy moves into the library on first read',
      '/old/pack.zip' in vault.imports(lib) and not os.path.exists(old))

# 6c. the vectorised depth map equals the per-triangle loop bit for bit: plain, two-sided,
#     on another map's frame, and peeled behind a cap
import numpy as np  # noqa: E402
from riftlord.fab import analyse  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fab_depth_reference as ref  # noqa: E402

rng = np.random.default_rng(7)
ctr = rng.uniform(0, 600, (300, 3))
Q = np.concatenate([ctr + rng.normal(0, s, (300, 3)) for s in (40, 40, 40)])
T = np.arange(900).reshape(3, 300).T.copy()
Q[T[:20, 1]] = Q[T[:20, 0]]                                 # degenerate triangles
two = rng.random(300) < 0.3
cases = [dict(), dict(two=two, with_raw=True), dict(frame=((-50.0, -50.0), (30, 30)), two=two)]
first = ref.depth_map(Q, T, 25.0, two=two, with_raw=True)
cases.append(dict(frame=(first[2], first[0].shape), two=two, cap=first[0], with_raw=True))
bad = []
for kw in cases:
    for a, b in zip(ref.depth_map(Q, T, 25.0, **kw), analyse.depth_map(Q, T, 25.0, **kw)):
        if not np.array_equal(a, b):
            bad.append(sorted(kw))
check('depth map: vectorised equals the loop in %d cases' % len(cases), not bad, bad)
check('depth map: seam and peel constants match the reference',
      (analyse.SEAM_GROW, analyse.PEEL_EPS) == (ref.SEAM_GROW, ref.PEEL_EPS))

# 6d. the worker guard: a dead worker or one over its memory share ends the wait with an error
def _sleep(x):
    import time
    time.sleep(x)
    return x


def _hog(mb):
    import time
    block = bytearray(mb << 20)
    for k in range(0, len(block), 4096):
        block[k] = 1
    time.sleep(5)
    return len(block)


if os.name != 'nt':
    import multiprocessing
    import signal as _signal
    import time as _time
    for name, fn, arg, limit, want in (('a dead worker', _sleep, 6, None, RuntimeError),
                                       ('a worker over its share', _hog, 400, 200, MemoryError)):
        got, t0 = None, _time.time()
        with multiprocessing.get_context('fork').Pool(1) as pool:
            guard = library._Guard(pool, limit)
            try:
                res = pool.map_async(fn, [arg])
                if fn is _sleep:
                    _time.sleep(0.5)
                    os.kill(pool._pool[0].pid, _signal.SIGKILL)
                res.get(30)
            except Exception as e:                          # noqa: BLE001
                got = e
            finally:
                guard.close()
        check('guard: %s raises %s promptly' % (name, want.__name__),
              isinstance(got, want) and _time.time() - t0 < 10, (type(got).__name__, got, round(_time.time() - t0, 1)))

# 6e. a piece without authored collision keeps its box unless the box would stand proud of the
#     drawing (a hanging brazier: a plate on a tall chain), then it collides as drawn
import numpy as np  # noqa: E402

from riftlord.fab import meshconvert  # noqa: E402


def _quads(*quads):
    P = np.array([p for q in quads for p in q], np.float64)
    T = np.array([(4 * k, 4 * k + 1, 4 * k + 2) for k in range(len(quads))]
                 + [(4 * k, 4 * k + 2, 4 * k + 3) for k in range(len(quads))], np.int64)
    return {'positions': P, 'triangles': T}


cube = _quads(*[[tuple(np.roll(np.array([s * 50.0, u, v]), k)) for u, v in ((-50, -50), (50, -50), (50, 50), (-50, 50))]
                for k in range(3) for s in (-1, 1)])
hanging = _quads([(-150, -150, 0), (150, -150, 0), (150, 150, 0), (-150, 150, 0)],
                 [(-2, 0, 0), (2, 0, 0), (2, 0, 600), (-2, 0, 600)])
for name, desc, want in (('a crate keeps its box', cube, ('box_fallback', 'default')),
                         ('a hanging piece collides as drawn', hanging, ('drawn', 'complex_as_simple'))):
    _c, got = meshconvert.collision({'collision': {}}, 1.0, desc, 'prop')
    check('collision: %s' % name, (got['source'], got['trace']) == want and (got['boxes'] == 0) == (want[0] == 'drawn'), got)
# foliage without authored shapes collides as shipped foliage: a tree by its trunk, a bush not at
# all; sky never (no shapes, no trimesh, no grids)
pole = _quads(*[[(x0, y0, 0), (x1, y1, 0), (x1, y1, 900), (x0, y0, 900)]
                for (x0, y0), (x1, y1) in (((-20, -20), (20, -20)), ((20, -20), (20, 20)), ((20, 20), (-20, 20)), ((-20, 20), (-20, -20)))])
canopy = _quads([(-400, -400, 600), (400, -400, 600), (400, 400, 900), (-400, 400, 900)])
tree = {'positions': np.concatenate([pole['positions'], canopy['positions']]),
        'triangles': np.concatenate([pole['triangles'], canopy['triangles'] + len(pole['positions'])])}
bush = _quads([(-150, -150, 0), (150, -150, 0), (150, 150, 250), (-150, 150, 250)],
              [(-150, 150, 0), (150, 150, 0), (150, -150, 250), (-150, -150, 250)])
_c, got = meshconvert.collision({'collision': {}}, 1.0, tree, 'foliage')
stem = np.asarray(_c['convex'][0]) if _c['convex'] else np.zeros((1, 3))
check('collision: a tree collides by its trunk, up to an ogre',
      got['source'] == 'trunk' and np.ptp(stem[:, :2], 0).max() <= 41 and stem[:, 2].max() == meshconvert.OGRE_HEIGHT, (got, stem))
_c, got = meshconvert.collision({'collision': {}}, 1.0, bush, 'foliage')
check('collision: a bush has no simple shapes', got['source'] == 'no_simple' and not _c['convex'] and not _c['boxes'], got)
_c, got = meshconvert.collision({'collision': {}}, 1.0, cube, 'prop', name='SM_SkyBoxTransperent')
check('collision: sky collides with nothing', got['source'] == 'none' and _c['trace'] == 'simple_as_complex'
      and _c['collides'] is False and not _c['boxes'] and not _c['convex'], got)
_c, got = meshconvert.collision({'collision': {'sphyls': 1, 'capsules': [
    {'center': [0, 0, 400], 'rotation': [0, 0, 0], 'radius': 30.0, 'length': 800.0}]}}, 2.0, tree, 'foliage')
cap = np.asarray(_c['convex'][0]) if _c['convex'] else np.zeros((1, 3))
check('collision: an authored capsule is written as a hull (scaled), not ignored',
      got['source'] == 'authored' and got['sphyls_ignored'] == 0 and np.isclose(cap[:, 2].max(), 1660)
      and np.isclose(np.ptp(cap[:, 0]), 120), (got, np.ptp(cap, 0)))
check('collision: sky by name words', [meshconvert.is_sky(n) for n in ('SM_sky', 'SM_Cloud_1', 'Skyline', 'SM_SkyDungeon_Wall')]
      == [True, True, False, False])

# 6e2. a kit wall takes one grid over relief a shipped wall grid spans (a window frame 18 uu proud
#      across the middle of a 600 x 150 wall; the 15 uu level buckets once split it)
from riftlord.fab import analyse  # noqa: E402

framed = _quads([(0, 0, 0), (0, 150, 0), (0, 150, 150), (0, 0, 150)], [(18, 150, 0), (18, 450, 0), (18, 450, 150), (18, 150, 150)],
                [(0, 450, 0), (0, 600, 0), (0, 600, 150), (0, 450, 150)])
got = analyse.suggest_grids(framed['positions'], framed['triangles'], 'wall', {'trace': 'complex_as_simple'},
                            two=np.ones(len(framed['triangles']), bool))
check('grids: a framed wall takes one grid along its length',
      sorted(g['cells'] for g in got if g['kind'] == 'wall') == [[2.0, 0.5], [2.0, 0.5]], [(g['kind'], g['cells']) for g in got])

# 6f. piece libraries (write_pieces): thumbnails blend vertex-colour layers; the ETA expects
#     only the stages a piece library runs; pieces that share a name or source are refused
from riftlord.fab import thumbs  # noqa: E402

wts = thumbs.lerp_weights(np.array([[0, 0, 0, 0], [255, 0, 0, 0], [255, 255, 0, 0], [128, 0, 0, 0]]), (0, 1))
check('pieces: successive lerp weights', np.allclose(wts, [[1, 0, 0], [0, 1, 0], [0, 0, 1], [127 / 255, 128 / 255, 0]]), wts)
flat = _quads([(-100, -100, 0), (100, -100, 0), (100, 100, 0), (-100, 100, 0)])
flat.update(uvs=[np.zeros((4, 2))], materials=np.zeros(2, np.int64))
red, blue = (np.full((4, 4, 4), c, np.uint8) for c in ((255, 0, 0, 255), (0, 0, 255, 255)))
red[..., 1:3], blue[..., :2] = 0, 0
shot = {k: np.asarray(thumbs.render(flat, {0: {'layers': [red, blue], 'weights': np.tile(w, (4, 1))}}))
        for k, w in (('red', [1, 0]), ('blue', [0, 1]))}
mid = thumbs.SIZE // 2
check('pieces: a layered thumbnail draws the weighted layer',
      shot['red'][mid, mid, 0] > 100 and shot['red'][mid, mid, 2] == 0 and shot['blue'][mid, mid, 2] > 100
      and shot['blue'][mid, mid, 0] == 0, (shot['red'][mid, mid], shot['blue'][mid, mid]))
exp = library._expected_seconds(os.path.join(TMP, 'nocache'), 10, library.PIECE_STAGES)
check('pieces: the ETA expects only mesh, pak and check stages',
      {s for s, v in exp.items() if v} == set(library.PIECE_STAGES), exp)
try:
    library.write_pieces('Dup', 'Dup', [{'name': 'A_1', 'source': 'a'}, {'name': 'A_01', 'source': 'b'}],
                         os.path.join(TMP, 'pieces'), kind='terrain', fingerprint='x')
    refused = False
except ValueError:
    refused = True
check('pieces: names equal after fname_safe are refused', refused and not os.path.exists(os.path.join(TMP, 'pieces')))

# 7. preflight checks against shipped facts, never against pack art
facts = preflight.proven_facts([])
check('preflight: facts load without reference paks',
      facts['pak']['mount'] == '../../../' and 'StaticMesh' in facts['classes'], facts.get('pak'))

# 8. the CLI: vault JSON and exit codes
out = io.StringIO()
with contextlib.redirect_stdout(out):
    code = fab_main(['vault', '--json', '--root', vroot, '--out', os.path.join(TMP, 'lib')])
got = json.loads(out.getvalue())
check('cli: vault --json exits 0 with the item',
      code == 0 and got['ok'] and len([r for r in got['items'] if r['source'] == 'vault']) == 1, code)
out = io.StringIO()
with contextlib.redirect_stdout(out):
    code = fab_main(['delete', 'NoSuchItem', '--root', vroot, '--json'])
check('cli: an unknown item exits 2', code == library.EXIT_USAGE and not json.loads(out.getvalue())['ok'], code)

for name, ok, detail in checks:
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, '' if ok else ': %s' % (detail,)))
failed = [c for c in checks if not c[1]]
print('%d checks, %d failed' % (len(checks), len(failed)))
sys.exit(1 if failed else 0)
