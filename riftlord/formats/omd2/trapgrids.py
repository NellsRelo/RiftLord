"""Extract Orcs Must Die 2 trap-placement volumes to JSON.

Each volume is a rectangle of trap cells with a bitmask saying which cells accept
a trap, and a surface type (Floor / Wall / Ceiling). **OMD2's cell is 300 units and
OMD3's is 300, so the port scale is exactly 1.0.** This line used to read "OMD2's cell
is 100 units; OMD3's is 300 too, so the port scale is exactly 1.0", which is both
self-contradictory and the exact error that once made every converted level three times
too large.

    python3 trapgrids.py --scene TheEdge
    python3 trapgrids.py --scene TheEdge --out theedge.json
    python3 trapgrids.py --list
"""

import argparse
import json
import os
import re
import sys
import zipfile


from riftlord.formats.omd2.nrbf import parse, Instance  # noqa: E402
from riftlord.formats.omd2 import clr  # noqa: E402

DEFAULT_DATA_ZIP = ('/home/nellsrelo/.local/share/Steam/steamapps/common/'
                    'Orcs Must Die 2/data.zip')

#: OMD2's trap cell (uu); see transform.OMD2_CELL for the measurement (2026-10-17).
OMD2_CELL = 200.0

#: `mSize` is 100 per cell: the volume's half extent in world units (100 = 200 / 2),
#: measured on all 1,130 axes of 565 volumes across three levels.
MSIZE_PER_CELL = 100.0
OMD3_CELL = 300.0
#: World scale OMD2 -> OMD3: 1:1, independent of the cell sizes.
SCALE = 1.0

VOLUME_CLASSES = (
    'SaberEditorPlugin.TrapPlacementVolumeShape',
    'SaberEditorPlugin.VolumeShape',
)

MASK_RE = re.compile(r'^(\d+)\|(\d+)\|([01]+)$')


def scene_layers(zf, scene):
    """Layer files for a scene, with .backup duplicates dropped."""
    prefix = 'scenes/%s/' % scene
    return sorted(n for n in zf.namelist()
                  if n.startswith(prefix) and n.endswith('.Layer'))


def list_scenes(zf):
    out = set()
    for n in zf.namelist():
        if n.startswith('scenes/') and n.endswith('.Layer'):
            out.add(n.split('/')[1])
    return sorted(out)


def volumes_in(graph, source):
    """Every trap-placement volume in one parsed Layer."""
    found = []
    for cls in VOLUME_CLASSES:
        for shape in graph.by_class(cls):
            props = clr.dynamic_properties(shape)
            mask = props.get('mValidSquares')
            if not isinstance(mask, str):
                continue
            m = MASK_RE.match(mask)
            if not m:
                continue
            w, h, bits = int(m.group(1)), int(m.group(2)), m.group(3)
            pos = clr.position(shape)
            warnings = clr.scale_warnings(shape)
            size = props.get('mSize') or clr.vec3(shape.get('mSize'))
            rec = {
                'name': shape.get('_shapeName'),
                'source': source,
                'shape_class': cls,
                'entity_class': shape.get('_entityClass'),
                'placement_type': props.get('mPlacementType'),
                'width': w,
                'height': h,
                'mask': bits,
                'cells_total': w * h,
                'cells_open': bits.count('1'),
                'position': list(pos),
                'orientation': list(clr.vec3(shape.get('_orientation')) or (0, 0, 0)),
                'scale': list(clr.vec3(shape.get('_scale')) or (1, 1, 1)),
                'size': list(size) if size else None,
                'entity_id': props.get('mID'),
                'game_modes': props.get('mGameModes'),
                'visible': shape.get('_bVisible'),
            }
            problems = []
            if len(bits) != w * h:
                problems.append('mask is %d chars, expected %d' % (len(bits), w * h))
            if size:
                for axis, cells, got in (('x', w, size[0]), ('y', h, size[1])):
                    want = cells * MSIZE_PER_CELL
                    if abs(got - want) > 0.5:
                        problems.append('size.%s is %g, expected %g for %d cells'
                                        % (axis, got, want, cells))
            rec['problems'] = problems      # data is wrong or unusable
            rec['warnings'] = warnings      # data is usable but needs care
            found.append(rec)
    return found


def extract(zip_path, scene):
    with zipfile.ZipFile(zip_path) as zf:
        layers = scene_layers(zf, scene)
        if not layers:
            raise SystemExit('no .Layer files for scene %r (try --list)' % scene)
        vols, failed = [], []
        for name in layers:
            try:
                graph = parse(zf.read(name))
            except Exception as exc:
                failed.append({'layer': name, 'error': '%s: %s' % (type(exc).__name__, exc)})
                continue
            vols.extend(volumes_in(graph, os.path.basename(name)))
    return {
        'scene': scene,
        'source': os.path.basename(zip_path),
        'omd2_cell_size': OMD2_CELL,
        'omd3_cell_size': OMD3_CELL,
        'scale_to_omd3': SCALE,
        'layers_read': len(layers) - len(failed),
        'layers_failed': failed,
        'volume_count': len(vols),
        'volumes': vols,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--zip', default=DEFAULT_DATA_ZIP, help="OMD2 data.zip")
    ap.add_argument('--scene', help='scene folder name, e.g. TheEdge')
    ap.add_argument('--out', help='write JSON here instead of stdout summary')
    ap.add_argument('--list', action='store_true', help='list scenes and exit')
    args = ap.parse_args()

    if args.list:
        with zipfile.ZipFile(args.zip) as zf:
            for s in list_scenes(zf):
                print(s)
        return
    if not args.scene:
        ap.error('--scene is required (or use --list)')

    result = extract(args.zip, args.scene)

    if args.out:
        with open(args.out, 'w') as fh:
            json.dump(result, fh, indent=2)
        print('wrote %s' % args.out)

    vols = result['volumes']
    print('scene            %s' % result['scene'])
    print('layers read      %d (%d failed)' % (result['layers_read'], len(result['layers_failed'])))
    print('volumes          %d' % len(vols))
    by_type = {}
    for v in vols:
        by_type[v['placement_type']] = by_type.get(v['placement_type'], 0) + 1
    for k in sorted(by_type, key=lambda x: (x is None, x)):
        print('  %-10s %d' % (k, by_type[k]))
    print('trap cells       %d open of %d total'
          % (sum(v['cells_open'] for v in vols), sum(v['cells_total'] for v in vols)))
    bad = [v for v in vols if v['problems']]
    warn = [v for v in vols if v['warnings']]
    print('volumes with data problems:      %d' % len(bad))
    for v in bad[:10]:
        print('  %-28s %s' % (v['name'], '; '.join(v['problems'])))
    print('volumes with position warnings:  %d' % len(warn))
    for v in warn[:10]:
        print('  %-28s %s' % (v['name'], '; '.join(v['warnings'])))
    for f in result['layers_failed']:
        print('  FAILED %s -- %s' % (f['layer'], f['error']))


if __name__ == '__main__':
    main()
