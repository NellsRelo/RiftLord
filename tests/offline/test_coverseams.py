"""Offline checks on the Map Editor's Cover Seams command (riftlord/mapeditor/coverseams.py,
docs/MAP-EDITOR.md "Cover seams"): its seam finders on pieces laid out as Proving Grounds has
them, the covers it proposes from riftlord/data/seam-covers.json, and the rules a cover keeps
(one shipped placement with 10 cases on 3 levels, an ordinary added mesh with collision off,
stretched no further than shipped, closing its opening by its triangles, off every trap
cell; a seam it cannot cover said in plain words).

    python3 test_coverseams.py
"""
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from riftlord.core import gamepath  # noqa: E402
from riftlord.formats.omd3 import meshcat  # noqa: E402
from riftlord.mapeditor import coverseams as cs  # noqa: E402


def entry(name, x, y, z, yaw90=False):
    """An added kit entry at (x, y, z), turned a quarter turn when `yaw90`."""
    rot = [0.0, 1.0, 0.0, 0.0, -1.0, 0.0, 0.0, 0.0] if yaw90 else [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    return {'kind': 'kit', 'name': name, 'matrix': rot + [0.0, 0.0, 1.0, 0.0, x, y, z, 1.0]}


#: Proving Grounds (record 900_Arena_P) added #495, #496, #502, #503: two lower-storey walls
#: and the upper floors over them, whose edges stop 38.7 uu short of the walls' back faces.
WALL_TOP = [entry('Wall_RB_MT_v2_2x3_C', 7319.3784, -9292.1519, 1386.8),
            entry('Wall_RB_MT_v2_2x3_C', 8198.067, -9292.1519, 1386.8),
            entry('Floor_RB_3x3_C', 8099.9977, -10350.003, 1999.9977),
            entry('Floor_RB_3x3_C', 7200.0037, -10350.003, 1999.9977)]
#: #625 and #521: a wall standing 43 uu over the floor's grid, its skirting above the floor.
SKIRTING = [entry('Wall_RB_MT_v2_2x3_C', 9829.2757, -12189.0949, 2023.236, yaw90=True),
            entry('Floor_RB_3x3_C', 9000.0022, -12149.9999, 1999.9977)]
#: #207 and #69: a wall ending 4.4 uu short of a column.
WALL_END = [{'name': 'Wall_RB_MT_v2_2x3_C', 'kind': 'kit', 'matrix': [-0.0, -1.0, -0.0, 0.0, 1.0, -0.0, 0.0, 0.0,
             -0.0, -0.0, 1.0, 0.0, 5388.8914, -5667.2955, 2670.8212, 1.0]},
            {'name': 'Column_Sec_1_1_FB_C', 'kind': 'kit', 'matrix': [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
             0.0, 0.0, 1.0, 0.0, 5170.8185, -5361.4384, 2744.3402, 1.0]}]
#: the opening Proving Grounds measures at #500 and #502 (kitseams cracks.py), in uu
WALL_TOP_WIDTH = (37.5, 40.0)
#: the skirting's height over the floor surface at #625, in uu
SKIRTING_HEIGHT = (5.0, 10.0)

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


def scene(entries, **plan):
    pieces, statics = cs.level_scene('Scenario_01', dict(plan, scratch={'name': 'test'}, added=entries))
    return cs.Scene(pieces, statics)


cat = cs.catalogue()
check('seam-covers.json lists covers and the families it is too thin for',
      cat.get('covers') and 'thin' in cat, sorted(cat))
rule_ok = all(e['cases'] >= 10 and e['levels'] >= 3 for e in cat['covers'])
check('every cover has 10 shipped cases on 3 levels', rule_ok,
      [(e['type'], e['family'], e['cases'], e['levels']) for e in cat['covers'] if e['cases'] < 10 or e['levels'] < 3])
known = meshcat.load()
missing = [m for e in cat['covers'] for m in e['members'] if m not in known]
check('every cover mesh is in the mesh catalogue', not missing, missing)

# --- the wall top
S = scene(WALL_TOP)
seams = cs.find(S)
top = [s for s in seams if s['type'] == 'wall_top']
check('two floors short of two wall tops are two wall_top seams', len(top) == 2,
      [(s['type'], s['labels']) for s in seams])
check('each opening is as wide as Proving Grounds measures it',
      all(WALL_TOP_WIDTH[0] <= s['width'] <= WALL_TOP_WIDTH[1] for s in top), [s['width'] for s in top])
got = cs.propose(S, seams)
said = [p for p in got['problems'] if p['seam']['type'] == 'wall_top']
check('no shipped placement suits these wall tops: none is proposed',
      not [p for p in got['proposals'] if p['seam']['type'] == 'wall_top'], got['proposals'])
check('each is said with what is wrong and an id to look at it by',
      len(said) == 2 and all(p['why'] == cs.NO_COVER['wall_top'] and p['id'] for p in said), said)
check('no cover family is counted across placements: every entry is one turn',
      all(len(e['turn']) == 3 for e in cat['covers']) and len({json.dumps([e['type'], e['style'], e['family'], e['turn']])
                                                               for e in cat['covers']}) == len(cat['covers']))

# --- closes() measures the opening by triangles, not boxes
sm = top[0]
check('nothing placed closes none of an opening', cs.closes(sm, []) == 0.0)
slab = np.eye(4)
v = cs.mesh_verts('Floor_Squarebrick_2x2')
c = (v.min(0) + v.max(0)) / 2
at = np.array(cs._where(sm), float)
slab[:3, 3] = at - c
slab[2, 3] = sm['depth_band'][1] + 5.0 - v[:, 2].min()
check('a slab over an opening closes it', cs.closes(dict(sm, open_runs=[[at[sm['run_axis']] - 200,
                                                                         at[sm['run_axis']] + 200]]),
                                                    [('Floor_Squarebrick_2x2', slab)]) >= cs.CLOSE_SHARE)
turned = slab.copy()
r = np.radians(45.0)
axis = sm['run_axis']
rot = np.eye(3)
o = [k for k in range(3) if k != axis]
rot[o[0], o[0]], rot[o[0], o[1]], rot[o[1], o[0]], rot[o[1], o[1]] = np.cos(r), -np.sin(r), np.sin(r), np.cos(r)
far = slab.copy()
far[sm['gap_axis'], 3] += 2000.0
check('the same slab beside the opening closes none of it',
      cs.closes(dict(sm, open_runs=[[at[axis] - 200, at[axis] + 200]]), [('Floor_Squarebrick_2x2', far)]) == 0.0)

# --- a wall end short of a column gets the shipped placement, and it closes the gap
S = scene(WALL_END)
seams = cs.find(S)
ends = [s for s in seams if s['type'] == 'wall_end']
check('a wall ending short of a column is a wall_end seam', len(ends) == 1, [(s['type'], s['width']) for s in seams])
got = cs.propose(S, ends)
props = got['proposals']
check('it gets one proposal', len(props) == 1, (props, got['problems']))
items = [i for p in props for i in p['items']]
check('a cover is an added mesh with collision off',
      items and all(i['kind'] == 'mesh' and i['collision'] is False and len(i['matrix']) == 16 for i in items),
      items[:1])
placed = [(i['name'], cs.plan_matrix(i['matrix'])) for i in items]
check('the cover closes the opening by its triangles',
      ends and cs.closes(ends[0], placed) >= cs.CLOSE_SHARE and props[0]['cover']['closes'] >= cs.CLOSE_SHARE,
      props[0]['cover'] if props else None)
fam = [e for e in cat['covers'] if e['type'] == 'wall_end' and e['family'] == (props[0]['cover']['family'] if props else '')]
within = True
for it in items:
    m = cs.plan_matrix(it['matrix'])
    ok = False
    for e in fam:
        k = e['run_local']
        stretch = np.linalg.norm(m[:3, :3], axis=0)[k]
        ok |= e['stretch'][0] - 1e-3 <= stretch <= e['stretch'][1] + 1e-3
        #: a run shorter than the family's shortest member at its least stretch takes that
        #: member squeezed to fit (pick_member)
        short = min(e['members'].values())
        ok |= (len(items) == 1 and it['name'] == min(e['members'], key=e['members'].get) and
               ends[0]['run'][1] - ends[0]['run'][0] < short * e['stretch'][0])
    within &= ok
check('every cover is stretched within the shipped stretch, or is the shortest squeezed to a shorter run',
      fam and within)
check('no cover is turned off the quarter turns', all(cs.aligned(m) for _, m in placed))
slabs = cs.cell_slabs(S)
taken = [it['name'] for it in items if cs.takes_cells(it['name'], cs.plan_matrix(it['matrix']), slabs)]
check('no cover enters a trap cell', not taken, taken)
after = scene(WALL_END + items)
check('with the cover accepted the wall_end seam is covered',
      not [s for s in cs.open_seams(cs.find(after)) if s['type'] == 'wall_end'])

# --- a cover that would take a trap cell is said in plain words, not placed
sm = ends[0]
d = sm['depth_axis']
lo, hi = cs._box(items[0]['name'], cs.plan_matrix(items[0]['matrix']))
mid = (lo + hi) / 2
#: a wall grid facing along the seam's depth, its cell slab over the cover's near face
over = dict(n=np.eye(3)[d], na=d, s=1.0, aligned=True, c=mid.copy())
over['c'][d] = lo[d] - cs.SLAB_NEAR - 1.0
half = np.full(3, 1.5 * cs.CELL)
half[d] = 0.0
over['lo'], over['hi'] = over['c'] - half, over['c'] + half
over['ext'] = half
got = cs.propose(S, [sm], extra_grids=[('fab|0', over)])
check('a cover that would enter a trap cell is not proposed, and is said',
      not got['proposals'] and got['problems'] and got['problems'][0]['why'] == cs.WHY['traps'],
      (got['proposals'][:1], got['problems']))

# --- skirting above the floor
seams = cs.find(scene(SKIRTING))
sk = [s for s in seams if s['type'] == 'skirting']
check('a wall standing over its floor is a skirting seam', len(sk) == 1, [(s['type'], s['width']) for s in seams])
check('its skirting is as high over the floor as Proving Grounds has it',
      sk and SKIRTING_HEIGHT[0] <= sk[0]['width'] <= SKIRTING_HEIGHT[1], [s['width'] for s in sk])
got = cs.propose(scene(SKIRTING))
said = [p for p in got['problems'] if p['seam']['type'] == 'skirting']
proposed = [p for p in got['proposals'] if p['seam']['type'] == 'skirting']
check('a seam no shipped placement suits is said with what is wrong, not covered',
      not proposed and said and said[0]['why'] == cs.NO_COVER['skirting'], (proposed, said))

# --- the endpoint's shape
got = cs.for_plan('Scenario_01', {'scratch': {'name': 'test'}, 'added': WALL_TOP})
check('for_plan answers proposals, problems and a seam count',
      set(got) >= {'proposals', 'problems', 'seams'} and json.dumps(got), sorted(got))

with open(gamepath.data(cs.CATALOGUE)) as fh:
    check('seam-covers.json reads as JSON', json.load(fh) == cat)

bad = [c for c in checks if not c[1]]
for name, ok, detail in checks:
    print('%s %s%s' % ('ok  ' if ok else 'FAIL', name, '' if ok else ': %s' % (detail,)))
print('%d of %d checks passed' % (len(checks) - len(bad), len(checks)))
sys.exit(1 if bad else 0)
