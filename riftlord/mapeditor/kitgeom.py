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

A grid's `RelativeLocation` and `RelativeRotation` are relative to the component it is
attached to (its Simple Construction Script node's parent), and a grid may be declared
by an ancestor class and only inherited: `Trapezoid_Landing` and `Stairs_MultiTiered`
hang theirs off turned and lifted parts, `Wall_RB_MT_v3_1x4_Angle` inherits its one.
`grids` merges the components of the class and its ancestors by name (leaf wins, field
by field) and walks each grid's parent chain up to the actor's root component.
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
#: Largest difference from the identity (matrix entry, scale) a parent chain may have
#: and still leave a grid's own transform as stored.
SAME = 1e-4


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


#: Component name suffix the cooker adds to a component promoted to a variable.
GEN_SUFFIX = '_GEN_VARIABLE'


def _fname(pkg, data, at):
    i = struct.unpack_from('<i', data, at)[0]
    return pkg.names[i][0] if 0 <= i < len(pkg.names) else None


def _components(pkg):
    """({component name: {cls, loc?, rot?, scale?, extent?}}, {component name: parent
    name}, [root node component names]) of one Blueprint package: its component
    templates (its own and the inherited ones it overrides) and its Simple Construction
    Script's tree. A root node's parent is the component it names, if any."""
    from riftlord.formats.omd3 import meshcat
    names = [n for n, _h in pkg.names]
    comps, nodes = {}, {}
    roots = []

    def name_of(i):
        name = levelbuild._name_of(pkg, i)
        return name[:-len(GEN_SUFFIX)] if name.endswith(GEN_SUFFIX) else name
    for i, e in enumerate(pkg.exports):
        cls = levelbuild.resolve(pkg, struct.unpack_from('<i', e['meta'], 0)[0])[1] or ''
        data = e['data']
        if cls == 'SCS_Node' or cls == 'SimpleConstructionScript':
            props = meshcat._walk(data, names, 0, len(data))
            nodes[i + 1] = (cls, data, props)
            continue
        if 'Component' not in cls or not cls.endswith('Component'):
            continue
        rec = {'cls': cls}
        vec = _vectors(pkg, e)
        for field, key in (('RelativeLocation', 'loc'), ('RelativeRotation', 'rot'),
                           ('RelativeScale3D', 'scale'), ('BoxExtent', 'extent')):
            if field in vec:
                rec[key] = list(vec[field])
        comps.setdefault(name_of(i), {}).update(rec)
    parents = {}

    def objects(data, props, field):
        if field not in props:
            return []
        _k, at, _size = props[field]
        count = struct.unpack_from('<i', data, at)[0]
        return [struct.unpack_from('<i', data, at + 4 + 4 * k)[0] for k in range(max(count, 0))]

    def template(node):
        _c, data, props = nodes[node]
        if 'ComponentTemplate' not in props:
            return None
        ref = struct.unpack_from('<i', data, props['ComponentTemplate'][1])[0]
        return name_of(ref - 1) if 0 < ref <= len(pkg.exports) else None
    for node, (cls, data, props) in nodes.items():
        if cls == 'SimpleConstructionScript':
            for root in objects(data, props, 'RootNodes'):
                if root in nodes and template(root):
                    roots.append(template(root))
            continue
        me = template(node)
        if not me:
            continue
        if 'ParentComponentOrVariableName' in props:
            parent = _fname(pkg, data, props['ParentComponentOrVariableName'][1])
            if parent and parent != 'None':
                parents.setdefault(me, parent[:-len(GEN_SUFFIX)]
                                   if parent.endswith(GEN_SUFFIX) else parent)
        for child in objects(data, props, 'ChildNodes'):
            if child in nodes and template(child):
                parents[template(child)] = me
    return comps, parents, roots


def _class_chain(asset_path):
    """Package paths of the Blueprint at `asset_path` and its Blueprint ancestors, leaf
    first."""
    from riftlord.formats.omd3 import omd3actors
    leaf = asset_path.rsplit('/', 1)[-1]
    chain = [asset_path]
    for cls in omd3actors.ancestry(leaf + '_C')[1:]:
        path = omd3actors._asset_for(cls)
        if path:
            chain.append(path)
    return chain


def grids(asset_path):
    """Every trap grid box of one kit Blueprint, in the actor's own space.

    Each is {'min', 'max', 'cells', 'extent', 'offset', 'rotation'}, where min and
    max bound the box on the actor's axes. A grid attached to a component other than
    the root gets its transform composed up the attachment chain: `offset` and
    `rotation` are then the box's centre and turn in actor space, and `extent` its
    half-extent under the chain's scale.
    """
    from riftlord.build import apply as _apply
    from riftlord.formats.omd3 import omd3level
    comps, parents, root = {}, {}, None
    for path in reversed(_class_chain(asset_path)):   # ancestor first, leaf wins
        ua, ux = gamefiles.package(path)
        pkg = Package(ua, ux)
        pkg.load_payloads()
        got, up, roots = _components(pkg)
        for name, rec in got.items():
            comps.setdefault(name, {}).update(rec)
        parents.update(up)
        if root is None and roots:
            #: the most ancestral class's first root node is the actor's root component
            root = next((r for r in roots if r not in parents), None)

    def local(name):
        rec = comps.get(name) or {}
        return omd3level._matrix(rec.get('loc') or (0.0, 0.0, 0.0),
                                 rec.get('rot') or (0.0, 0.0, 0.0),
                                 rec.get('scale') or (1.0, 1.0, 1.0))

    def chain(name):
        """The transform of `name`'s parent in actor space, or None for the root."""
        out, seen, cur = None, {name}, parents.get(name)
        while cur and cur != root and cur in comps and cur not in seen:
            seen.add(cur)
            out = local(cur) if out is None else omd3level._matmul(local(cur), out)
            cur = parents.get(cur)
        return out

    out = []
    for name, rec in comps.items():
        if 'TrapGridBox' not in rec['cls'] or not rec.get('extent'):
            continue
        extent = tuple(rec['extent'])
        offset = tuple(rec.get('loc') or (0.0, 0.0, 0.0))
        pitch, yaw, roll = rec.get('rot') or (0.0, 0.0, 0.0)
        above = chain(name)
        if above is not None and all(abs(above[r][c] - (1.0 if r == c else 0.0)) < SAME
                                     for r in range(3) for c in range(4)):
            above = None
        if above is not None or any(abs(v - 1.0) > SAME for v in rec.get('scale') or ()):
            #: composed up the attachment chain, under every scale on the way
            m = local(name) if above is None else omd3level._matmul(above, local(name))
            loc, rot, scale = _apply.transform_of(
                [m[r][c] for c in range(4) for r in range(4)])
            offset = tuple(round(v, 4) for v in loc)
            pitch, yaw, roll = (round(v, 4) for v in rot)
            extent = tuple(round(abs(extent[a] * scale[a]), 4) for a in range(3))
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


def placed_classes(catalogue):
    """{piece: {'path'}} for the Blueprint classes the shipped levels place
    (omd3actors.load) that are not in `catalogue`."""
    from riftlord.formats.omd3 import omd3actors
    have = {name.lower() for name in catalogue}
    out = {}
    for cls in omd3actors.load():
        name = cls[:-2] if cls.endswith('_C') else cls
        if name.lower() in have:
            continue
        path = omd3actors._asset_for(cls)
        if path and path.startswith('../../../OMD/Content/'):
            out[name] = {'path': '/Game/' + path[len('../../../OMD/Content/'):],
                         'placed': True}
    return out


def build(catalogue=CATALOGUE, out_path=GEOMETRY):
    """Measure every piece in the catalogue, and every other Blueprint class the shipped
    levels place that has a trap grid, and write kit-geometry.json."""
    with open(catalogue) as fh:
        kit = json.load(fh)
    kit.update(placed_classes(kit))
    out, failed = {}, {}
    for name, info in sorted(kit.items()):
        path = (info.get('path') or '').replace('/Game/', '../../../OMD/Content/')
        if not path:
            failed[name] = 'no path'
            continue
        try:
            found = grids(path)
        except Exception as exc:                     # noqa: BLE001
            if not info.get('placed'):
                failed[name] = str(exc)
            continue
        if not found:
            if not info.get('placed'):
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
