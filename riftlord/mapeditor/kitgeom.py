"""Where an OMD3 kit piece's footprint sits relative to its own origin.

    python3 kitgeom.py --piece Floor_4x4
    python3 kitgeom.py --build          # writes kit-geometry.json
    python3 kitgeom.py --check

## The problem this solves

`kit-catalogue.json` records each trap grid's `extent` and `cells` but not its
**offset**, and without the offset a piece cannot be placed. `Floor_4x4` has a box
of half-extent 600 whose `RelativeLocation` is (600, 600, 0), so the footprint
spans 0..1200 -- the origin is a corner, not the centre. Placing it as though it
were centred moves it half a piece.

Nor is there a rule to assume instead. Measured:

| piece | half-extent | offset | footprint in actor space |
|---|---|---|---|
| `Floor_4x4` | 600 | (600, 600, 0) | X 0..1200, Y 0..1200 |
| `Floor_FS_2x4` | 300, 600 | (600, 300, 0) | X 0..1200, Y 0..600 |
| `Wall_FB_4x4` | 600 | (600, 0, 600) | X 0..1200, Z 0..1200 |
| `Floor_8x4_Wood` | 600, 1200 | (1200, -600, 0) | X 0..2400, Y **-1200..0** |

`Floor_8x4_Wood` extends the other way, so "+half on each axis" is not a rule
either. The offset has to be read per piece, which is what this does.

## Reading it

The grid is an `OMDTrapGridBoxComponent` inside the piece's Blueprint, carrying
`BoxExtent` (half-extents), `RelativeLocation` (in the actor's space) and
`RelativeRotation`. Its own rotation permutes which half-extent lies along which
actor axis, so the box is rotated into actor space before being combined with the
offset -- reading `BoxExtent` positionally gives the right numbers on the wrong
axes, which looks plausible and tiles wrongly.
"""

import argparse
import json
import math
import os
import struct
import sys
from riftlord.core import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.core import gamefiles  # noqa: E402
from riftlord.build import levelbuild  # noqa: E402
from riftlord.formats.ue.uassetio import Package  # noqa: E402

CATALOGUE = gamepath.data('kit-catalogue.json')
GEOMETRY = gamepath.data('kit-geometry.json')

#: One OMD3 trap cell, in Unreal units.
CELL = 300.0


def _rotate(vec, pitch, yaw, roll):
    """Unreal's rotation of a vector: roll about X, pitch about Y, yaw about Z."""
    x, y, z = vec
    cr, sr = math.cos(math.radians(roll)), math.sin(math.radians(roll))
    y, z = y * cr - z * sr, y * sr + z * cr
    cp, sp = math.cos(math.radians(pitch)), math.sin(math.radians(pitch))
    x, z = x * cp + z * sp, -x * sp + z * cp
    cy, sy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    x, y = x * cy - y * sy, x * sy + y * cy
    return (x, y, z)


def _vectors(pkg, export):
    """Every three-float property of one export, by name."""
    names = [n for n, _h in pkg.names]
    data = export['data']
    out = {}
    o = 0
    while o + 8 <= len(data):
        ni = struct.unpack_from('<i', data, o)[0]
        if not 0 <= ni < len(names):
            break
        name = names[ni]
        if name == 'None':
            break
        ti = struct.unpack_from('<i', data, o + 8)[0]
        kind = names[ti] if 0 <= ti < len(names) else ''
        size = struct.unpack_from('<i', data, o + 16)[0]
        head = 24 + 1
        if kind == 'BoolProperty':
            head, size = 26, 0
        elif kind == 'StructProperty':
            head = 24 + 8 + 17
        elif kind in ('ArrayProperty', 'ByteProperty', 'EnumProperty'):
            head = 24 + 8 + 1
        if kind == 'StructProperty' and size == 12:
            out[name] = struct.unpack_from('<3f', data, o + head)
        o += head + size
    return out


def grids(asset_path):
    """Every trap grid box of one kit Blueprint, in the actor's own space.

    Each is {'min', 'max', 'cells', 'extent', 'offset', 'rotation'}, where min and
    max bound the box on the actor's axes.
    """
    ua, ux = gamefiles.package(asset_path)
    pkg = Package(ua, ux)
    pkg.load_payloads()
    out = []
    for e in pkg.exports:
        cls = levelbuild.resolve(
            pkg, struct.unpack_from('<i', e['meta'], 0)[0])[1] or ''
        if 'TrapGridBox' not in cls:
            continue
        got = _vectors(pkg, e)
        extent = got.get('BoxExtent')
        if not extent:
            continue
        offset = got.get('RelativeLocation', (0.0, 0.0, 0.0))
        pitch, yaw, roll = got.get('RelativeRotation', (0.0, 0.0, 0.0))
        # The box's own rotation decides which half-extent lies along which actor
        # axis. Rotating the extent vector and taking magnitudes does that without
        # having to case on the angles.
        spun = _rotate(extent, pitch, yaw, roll)
        half = tuple(abs(v) for v in spun)
        lo = tuple(offset[i] - half[i] for i in range(3))
        hi = tuple(offset[i] + half[i] for i in range(3))
        out.append({'min': lo, 'max': hi, 'extent': list(extent),
                    'offset': list(offset), 'rotation': [pitch, yaw, roll],
                    'cells': [round((hi[i] - lo[i]) / CELL, 4) for i in range(3)]})
    return out


def build(catalogue=CATALOGUE, out_path=GEOMETRY):
    """Measure every piece in the catalogue and write kit-geometry.json."""
    with open(catalogue) as fh:
        kit = json.load(fh)
    out, failed = {}, {}
    for name, info in sorted(kit.items()):
        path = (info.get('path') or '').replace('/Game/', '../../../OMD/Content/')
        if not path:
            failed[name] = 'no path'
            continue
        try:
            found = grids(path)
        except Exception as exc:                     # noqa: BLE001
            failed[name] = str(exc)
            continue
        if not found:
            failed[name] = 'no trap grid box component'
            continue
        out[name] = {'path': info['path'], 'grids': found}
    with open(out_path, 'w') as fh:
        json.dump({'pieces': out, 'unreadable': failed}, fh, indent=1,
                  sort_keys=True)
    return out, failed


def load(path=GEOMETRY):
    with open(path) as fh:
        return json.load(fh)


def footprints(geometry=None, surface_of=None):
    """{(cols, rows): [(piece, grid index)]} for whole-cell footprints.

    The two largest axes of a box are its face; the smallest is its thickness.
    """
    geometry = geometry or load()['pieces']
    out = {}
    for name, info in geometry.items():
        for i, g in enumerate(info['grids']):
            cells = g['cells']
            order = sorted(range(3), key=lambda k: cells[k])
            a, b = cells[order[1]], cells[order[2]]
            if abs(a - round(a)) > 0.02 or abs(b - round(b)) > 0.02:
                continue
            a, b = int(round(a)), int(round(b))
            if a < 1 or b < 1:
                continue
            out.setdefault((a, b), []).append((name, i))
            if a != b:
                out.setdefault((b, a), []).append((name, i))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--piece')
    ap.add_argument('--build', action='store_true')
    ap.add_argument('--check', action='store_true')
    args = ap.parse_args(argv)

    if args.piece:
        with open(CATALOGUE) as fh:
            kit = json.load(fh)
        info = kit.get(args.piece)
        if not info:
            print('%s is not in the catalogue' % args.piece)
            return 1
        path = info['path'].replace('/Game/', '../../../OMD/Content/')
        for g in grids(path):
            print('%s\n   extent   %s\n   offset   %s\n   rotation %s'
                  % (args.piece, g['extent'], g['offset'], g['rotation']))
            print('   box      X %.0f..%.0f  Y %.0f..%.0f  Z %.0f..%.0f'
                  % (g['min'][0], g['max'][0], g['min'][1], g['max'][1],
                     g['min'][2], g['max'][2]))
            print('   cells    %s' % g['cells'])
        return 0

    if args.build:
        out, failed = build()
        print('measured %d piece(s); %d unreadable' % (len(out), len(failed)))
        fp = footprints(out)
        print('distinct whole-cell footprints: %d' % len(fp))
        import collections
        reasons = collections.Counter(failed.values())
        for r, n in reasons.most_common(5):
            print('   %4d  %s' % (n, r[:90]))
        return 0

    if args.check:
        geo = load()['pieces']
        bad = []
        for name, info in geo.items():
            for g in info['grids']:
                if all(abs(v) < 1e-6 for v in g['offset']):
                    continue
        # Floor_4x4 is the reference: 4x4 cells spanning 0..1200 on two axes.
        ref = geo.get('Floor_4x4')
        if not ref:
            bad.append('Floor_4x4 missing from the geometry table')
        else:
            g = ref['grids'][0]
            span = [(g['min'][i], g['max'][i]) for i in range(3)]
            want = [(0.0, 1200.0), (0.0, 1200.0)]
            got = [s for s in span if abs(s[1] - s[0]) > 100]
            if [(round(a), round(b)) for a, b in got] != want:
                bad.append('Floor_4x4 spans %s, expected %s' % (got, want))
        print('complaints: %d' % len(bad))
        for b in bad:
            print('   ' + b)
        return 1 if bad else 0

    ap.error('need --piece, --build or --check')


if __name__ == '__main__':
    raise SystemExit(main())
