"""Offline checks that laid Fab grids keep their pieces through Map Editor edits (marker:
fabgrids). In the editor (web/mapeditor/src/fabgrids.js, run under node): deleting,
reordering, pasting and undoing pieces keep each grid's `pieces` on the same entries, and a
grid whose last piece is deleted goes with it. On save (leveldata.save_plan with
fabemit.mend_grids): indices follow the save's own cleaning of `added`, an index past the
end or at a piece that is not a Fab piece is dropped, a grid left with none is pointed at
the Fab pieces under it, and one with nothing under it is dropped.

    python3 test_fabgrids.py
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from riftlord.mapeditor import fabemit  # noqa: E402
from riftlord.mapeditor import leveldata  # noqa: E402

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


def at(x, y=0.0, z=0.0):
    return [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, x, y, z, 1.0]


def fab(x, name='SM_tile'):
    return {'kind': 'fab', 'name': 'fab/P/' + name, 'pack': 'P', 'object': '/Game/' + name,
            'source': name, 'role': 'floor', 'grids': [], 'matrix': at(x)}


def mesh(x):
    return {'kind': 'mesh', 'name': '1M_Cube', 'matrix': at(x)}


def grid(gid, pieces, x=0.0, half=300.0):
    return {'id': gid, 'center': [x, 0.0, 0.0],
            'axes': {'x': [0.0, 0.0, 1.0], 'y': [1.0, 0.0, 0.0], 'z': [0.0, 1.0, 0.0]},
            'extent': [10.0, half, 300.0], 'bits': 9, 'warmachine': None, 'pieces': pieces}


# 1. the editor: fabgrids.js under node, one scenario per line of output
JS = os.path.join(ROOT, 'web', 'mapeditor', 'src', 'fabgrids.js')
SCRIPT = r"""
import * as fg from %s
const out = {}
const P = (n) => ({ kind: 'fab', name: 'p' + n })
const M = (n) => ({ kind: 'mesh', name: 'm' + n })
const G = (id, pieces) => ({ id, pieces })
function run (name, plan, edit) {
  const owners = fg.bind(plan)
  edit(plan, owners)
  const dropped = fg.sync(plan, owners)
  out[name] = { grids: plan.fabGrids.map(g => [g.id, g.pieces]), dropped }
}
// a mesh before the grid's pieces is deleted: the indices shift down with them
run('delete before', { added: [M(0), P(1), P(2), P(3)], fabGrids: [G(1, [1, 2])] },
    (p) => { p.added = p.added.filter(e => e.name !== 'm0') })
// one of two pieces deleted: the grid keeps the other
run('delete one', { added: [P(0), P(1), P(2)], fabGrids: [G(1, [0, 1])] },
    (p) => { p.added = p.added.filter(e => e.name !== 'p0') })
// both deleted: the grid goes; a grid opened with no piece stays for the save to mend
run('delete all', { added: [P(0), P(1), P(2)], fabGrids: [G(1, [0, 1]), G(2, [])] },
    (p) => { p.added = p.added.filter(e => e.name === 'p2') })
// reversed order: the grid follows its entries
run('reorder', { added: [P(0), P(1), P(2)], fabGrids: [G(1, [0])] },
    (p) => { p.added.reverse() })
// a paste adds copies: the grid stays on the originals
run('paste', { added: [P(0), P(1)], fabGrids: [G(1, [0, 1])] },
    (p) => { p.added.unshift(...p.added.map(e => ({ ...e }))) })
// a grid laid after opening is tied with own()
run('laid', { added: [P(0), P(1), P(2)], fabGrids: [] }, (p, owners) => {
  const g = G(5, [1, 2]); p.fabGrids.push(g); fg.own(owners, g, [p.added[1], p.added[2]])
  p.added.splice(0, 1)
})
// undo: a snapshot taken after sync, parsed back and bound, points at the same pieces
{
  const plan = { added: [M(0), P(1), P(2)], fabGrids: [G(1, [2])] }
  let owners = fg.bind(plan)
  plan.added.shift()
  fg.sync(plan, owners)
  const back = JSON.parse(JSON.stringify(plan))
  owners = fg.bind(back)
  out.undo = { name: owners.get(back.fabGrids[0])[0].name, pieces: back.fabGrids[0].pieces }
}
console.log(JSON.stringify(out))
""" % json.dumps('file://' + JS)
try:
    got = subprocess.run(['node', '--input-type=module', '-e', SCRIPT], capture_output=True,
                         text=True, timeout=60)
    res = json.loads(got.stdout) if got.returncode == 0 else {}
    detail = got.stderr[-400:]
except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
    res, detail = {}, str(exc)
want = {
    'delete before': {'grids': [[1, [0, 1]]], 'dropped': 0},
    'delete one': {'grids': [[1, [0]]], 'dropped': 0},
    'delete all': {'grids': [[2, []]], 'dropped': 1},
    'reorder': {'grids': [[1, [2]]], 'dropped': 0},
    'paste': {'grids': [[1, [2, 3]]], 'dropped': 0},
    'laid': {'grids': [[5, [0, 1]]], 'dropped': 0},
    'undo': {'name': 'p2', 'pieces': [1]},
}
for name, w in want.items():
    check('editor: %s' % name, res.get(name) == w, res.get(name) if res else detail)

# 2. main.js keeps the indices in step wherever the plan is stored, shown or replaced
src = open(os.path.join(ROOT, 'web', 'mapeditor', 'src', 'main.js')).read()
check('main.js syncs before a snapshot, a save, the panel and the grid layer',
      src.count('fabGrids.sync(plan, fabOwners)') >= 4, src.count('fabGrids.sync('))
check('main.js rebinds after undo and opening a saved plan, and ties a laid grid',
      src.count('fabOwners = fabGrids.bind(plan)') >= 2 and 'fabGrids.own(' in src, '')

# 3. mend_grids, with the Fab pieces under a grid given directly (no library needed)
real_resolve = fabemit.resolve
BOUNDS = {'min': [0.0, 0.0, -80.0], 'max': [600.0, 600.0, 0.0]}


def fake_resolve(plan):
    return [(n, e, {'bounds': BOUNDS}, e['matrix']) for n, e in fabemit.pieces(plan)], []


fabemit.resolve = fake_resolve
try:
    added = [mesh(0), fab(-300), fab(5000)]
    notes = []
    got = fabemit.mend_grids([grid(1, [1, 0, 9]), grid(2, [0]), grid(3, [], x=8000.0)],
                             added, None, notes)
    check('indices past the end and at non-Fab pieces are dropped',
          got and got[0]['id'] == 1 and got[0]['pieces'] == [1], got)
    check('a grid left with no piece is pointed at the Fab piece under it',
          len(got) > 1 and got[1]['id'] == 2 and got[1]['pieces'] == [1], got)
    check('a grid with nothing under it is dropped, with a note',
          [g['id'] for g in got] == [1, 2] and len(notes) == 1, (got, notes))
    got = fabemit.mend_grids([grid(1, [0, 2])], added, [None, 1, 2], [])
    check('indices follow the save: a raw item it dropped no longer counts',
          got and got[0]['pieces'] == [2], got)
finally:
    fabemit.resolve = real_resolve

# 4. a save: a raw item the save cannot keep sits before the grid's pieces
leveldata.PLANS = tempfile.mkdtemp(prefix='fabgrids-plans-')
leveldata.save_plan('Scenario_01', {
    'added': [{'kind': 'mesh', 'name': 'broken'}, mesh(0), fab(0), fab(600)],
    'fabGrids': [grid(1, [2, 3]), grid(2, [3, 7])]})
with open(os.path.join(leveldata.PLANS, os.listdir(leveldata.PLANS)[0])) as fh:
    saved = json.load(fh)
laid = saved.get('fabGrids') or []
check('a save re-indexes laid grids past the item it dropped',
      [(g['id'], g['pieces']) for g in laid] == [(1, [1, 2]), (2, [2])], laid)

bad = [c for c in checks if not c[1]]
for name, ok, detail in checks:
    print('%s %s%s' % ('ok  ' if ok else 'FAIL', name, '' if ok else ': %s' % (detail,)))
print('%d of %d checks passed' % (len(checks) - len(bad), len(checks)))
sys.exit(1 if bad else 0)
