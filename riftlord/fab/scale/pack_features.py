# Generated from OMD2-Converter/thirdparty/scale-tools/pack_features.py by thirdparty/fabconvert/port_riftlord.py; edit the source.
"""Measure the same human-scale features in one Fab pack (editor packages, read-only).

    python3 -I pack_features.py <inventory/<pack>.json> <vault Content dir> <out.json>

Reads mesh bounds and roles from the survey inventory and LOD0 geometry from the vault
with survey-tools/meshgeo.py. Nothing in the vault is written.

* openings: every wall-like mesh (thin axis <= WALL_THIN_MAX) in the wall, door/window
  and structure roles is rasterised on its wall plane (features.opening);
* door leaves: door meshes with no enclosed opening -- the leaf is the opening it
  closes (inferred);
* stairs: stairs role, features.stairs;
* railings, walls, floors, columns, props: bounds by name family.
"""

import json
import os
import re
import statistics
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', 'survey-tools'))

from riftlord.fab.scale import features                                      # noqa: E402
from riftlord.fab.survey import meshgeo                                       # noqa: E402

CAPSULES = [(42, 96), (80, 180)]

#: A mesh is wall-like (worth rasterising on one plane) when its thinner horizontal
#: extent is at most this, uu. Whole buildings project their front and back walls on
#: top of each other and are skipped.
WALL_THIN_MAX = 120.0
#: ...and its wall plane is at least this big on both axes (no trims).
WALL_MIN_SPAN = 100.0
#: Meshes over this many triangles are skipped for rasterising (time), uu.
MAX_TRIS = 60000

OPEN_ROLES = ('wall', 'door_window_arch', 'structure')
DOOR_NAME_RX = r'(?i)door|gate|entrance|entrace|portal'
LEAF_EXCLUDE_RX = r'(?i)floor_door|cage|cupboard|cabinet|frame|trim|handle|knob|hinge|lock|arch'
RAILING_RX = r'(?i)rail|balust|fence|parapet|handrail'
RAILING_EXCLUDE_RX = r'(?i)stair|post$|pole'
PROP_FAMILIES = {
    'barrel': r'(?i)barrel(?!_box|_stand)',
    'crate': r'(?i)crate|(^|_)box(_|\d|$)',
    'chair': r'(?i)chair|stool',
    'table': r'(?i)table',
    'bench': r'(?i)bench',
}
PROP_EXCLUDE_RX = r'(?i)cup$|_cup|lid|broken|pack|stand|holder|bracket'
#: Prop size used to compare with OMD3: height for all except bench (height too, but
#: benches vary in back height; kept as height and noted).


def dist(vals):
    vals = sorted(float(v) for v in vals if v is not None)
    if not vals:
        return None
    q = lambda f: vals[min(len(vals) - 1, int(round(f * (len(vals) - 1))))]
    return {'n': len(vals), 'min': round(vals[0], 1), 'p25': round(q(0.25), 1),
            'median': round(statistics.median(vals), 1), 'p75': round(q(0.75), 1),
            'max': round(vals[-1], 1)}


def geometry(content, rel):
    g = meshgeo.read_static_mesh(os.path.join(content, rel))
    return np.asarray(g['positions'], float), np.asarray(g['tri_vertex'], int).reshape(-1, 3)


def name_of(a):
    return os.path.splitext(os.path.basename(a['path']))[0]


def main(inv_path, content, out_path):
    inv = json.load(open(inv_path))
    meshes = [a for a in inv['assets'] if a['cls'] == 'StaticMesh' and a.get('dims')]
    res = {'generated_by': 'scale-tools/pack_features.py', 'pack': inv['summary'].get('title'),
           'inventory': os.path.basename(inv_path)}
    opens, leaves, stairs, errors = [], [], [], []
    for a in meshes:
        n = name_of(a)
        d = a['dims']
        thin = min(d[0], d[1])
        wide = max(d[0], d[1])
        if a['role'] == 'stairs':
            try:
                V, T = geometry(content, a['path'])
                s = features.stairs(V, T)
            except Exception as ex:                   # noqa: BLE001
                errors.append([n, repr(ex)[:120]])
                s = None
            if s:
                s['source'] = n
                stairs.append(s)
            continue
        if a['role'] in OPEN_ROLES and thin <= WALL_THIN_MAX and wide >= WALL_MIN_SPAN \
                and d[2] >= WALL_MIN_SPAN and a.get('tris', 0) <= MAX_TRIS:
            try:
                V, T = geometry(content, a['path'])
                o = features.opening(V, T, CAPSULES)
            except Exception as ex:                   # noqa: BLE001
                errors.append([n, repr(ex)[:120]])
                o = None
            if o:
                o['source'] = n
                opens.append(o)
                continue
        if re.search(DOOR_NAME_RX, n) and not re.search(LEAF_EXCLUDE_RX, n) and thin <= WALL_THIN_MAX / 2:
            leaves.append({'source': n, 'width': round(wide, 1), 'height': round(d[2], 1),
                           'how': 'leaf bounds (inferred opening)'})
    res['openings'] = opens
    res['door_leaves'] = leaves
    res['stairs'] = stairs
    res['errors'] = errors
    doors = [o for o in opens if o['kind'] == 'door']
    wins = [o for o in opens if o['kind'] == 'window']
    res['door_height'] = dist([o['height'] for o in doors] + [l['height'] for l in leaves])
    res['door_width'] = dist([o['width_min_lower_half'] for o in doors] + [l['width'] for l in leaves])
    res['door_height_frames_only'] = dist([o['height'] for o in doors])
    res['door_height_leaves_only'] = dist([l['height'] for l in leaves])
    res['window_height'] = dist([o['height'] for o in wins])
    res['window_width'] = dist([o['width_min_lower_half'] for o in wins])
    res['window_sill'] = dist([o['sill'] for o in wins])
    good = [s for s in stairs if s['axis_corr'] >= 0.95]
    res['riser'] = dist([s['riser_median'] for s in good])
    res['going'] = dist([s['going_median'] for s in good])

    def fam(pred, key):
        return {name_of(a): round(key(a['dims']), 1) for a in meshes if pred(a)}

    rails = fam(lambda a: re.search(RAILING_RX, name_of(a)) and not re.search(RAILING_EXCLUDE_RX, name_of(a))
                and a['role'] not in ('foliage', 'sky_fx'), lambda d: d[2])
    res['railings'] = rails
    res['railing_height'] = dist(rails.values())
    walls = fam(lambda a: a['role'] == 'wall' and max(a['dims'][0], a['dims'][1]) >= WALL_MIN_SPAN
                and not re.search(RAILING_RX, name_of(a)), lambda d: d[2])
    res['wall_height'] = dist(walls.values())
    floors = fam(lambda a: a['role'] == 'floor' and min(a['dims'][0], a['dims'][1]) >= WALL_MIN_SPAN
                 and a['dims'][2] < min(a['dims'][0], a['dims'][1]) / 2, lambda d: d[2])
    res['floor_thickness'] = dist(floors.values())
    cols = fam(lambda a: a['role'] == 'column' and a['dims'][2] > 2 * max(a['dims'][0], a['dims'][1]),
               lambda d: min(d[0], d[1]))
    res['column_diameter'] = dist(cols.values())
    props = {}
    for fam_name, rx in PROP_FAMILIES.items():
        got = {name_of(a): [round(x, 1) for x in a['dims']] for a in meshes
               if re.search(rx, name_of(a)) and not re.search(PROP_EXCLUDE_RX, name_of(a))
               and a['role'] in ('prop', 'structure')}
        props[fam_name] = {'meshes': got, 'height': dist([v[2] for v in got.values()])}
    res['props'] = props
    # architecture footprints, for the cell-fit measure in scale_report.py
    ARCH = ('floor', 'wall', 'stairs', 'structure', 'roof', 'column', 'door_window_arch')
    res['arch_footprints'] = {name_of(a): [round(a['dims'][0], 1), round(a['dims'][1], 1), round(a['dims'][2], 1)]
                              for a in meshes if a['role'] in ARCH}
    json.dump(res, open(out_path, 'w'), indent=1, default=float)
    for k in ('door_height', 'door_height_frames_only', 'door_height_leaves_only', 'door_width',
              'window_height', 'window_sill', 'riser', 'going', 'railing_height', 'wall_height',
              'floor_thickness', 'column_diameter'):
        print('%-24s %s' % (k, res.get(k)))
    for f, p in props.items():
        print('  prop %-8s %s' % (f, p['height']))
    print('errors', len(errors))


if __name__ == '__main__':
    main(*sys.argv[1:4])
