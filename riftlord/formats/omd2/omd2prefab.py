"""Expand OMD2 prefabs, which hold geometry the Layer files do not.

    python3 omd2prefab.py --scene UpAndDown
    python3 omd2prefab.py --file "prefabs/Act2/WallSupport.prefab"

A `PrefabShape` in a Layer carries a `_fileName` and **no children** -- the shapes
live in the `.prefab` file, which the Layer never expands. Servant Entrance places
166 of them from 24 distinct files: `WallSupport` x23, `2WideArch` x20,
`StairsRailingRight` x13. All of that is real level geometry, and none of it was
being drawn or converted, which is why walls had gaps in them.

Unlike everything else in OMD2's scene data, a `.prefab` is **XML**, not MS-NRBF:

    <shape class="Group3DShape">
      <property name="Position" value="0,0,0" />
      <shape class="StaticMeshShape">
        <property name="MeshFileName" value="art\\Act2\\Archs\\wallarch_base.vmesh" />
        <property name="Position" value="0,0,0" />
        <property name="Orientation" value="89.99998,0,0" />
        <property name="Scaling" value="1.5,1.5,1.5" />

Groups nest, so a child's transform is composed with every group above it. The
result is returned as a matrix rather than as angles: composing two rotations does
not give a rotation whose Euler angles are the sum of the parts, and rebuilding
angles from the matrix only to turn them back into a matrix would be a lossy round
trip for no gain.
"""

import argparse
import collections
import math
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))


def _triple(text, default=(0.0, 0.0, 0.0)):
    if not text:
        return list(default)
    parts = [p for p in re.split(r'[,\s]+', text.strip()) if p]
    try:
        out = [float(p) for p in parts[:3]]
    except ValueError:
        return list(default)
    while len(out) < 3:
        out.append(default[len(out)])
    return out


def identity():
    return [[1.0 if r == c else 0.0 for c in range(4)] for r in range(4)]


def matmul(a, b):
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)]
            for r in range(4)]


def compose(position, orientation, scale):
    """vForge's transform: scale, then roll-pitch-yaw, then translate."""
    yaw, pitch, roll = orientation
    cr, sr = math.cos(math.radians(roll)), math.sin(math.radians(roll))
    cp, sp = math.cos(math.radians(pitch)), math.sin(math.radians(pitch))
    cy, sy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    rx = [[1, 0, 0, 0], [0, cr, -sr, 0], [0, sr, cr, 0], [0, 0, 0, 1]]
    ry = [[cp, 0, sp, 0], [0, 1, 0, 0], [-sp, 0, cp, 0], [0, 0, 0, 1]]
    rz = [[cy, -sy, 0, 0], [sy, cy, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    sc = [[scale[0], 0, 0, 0], [0, scale[1], 0, 0], [0, 0, scale[2], 0],
          [0, 0, 0, 1]]
    move = [[1, 0, 0, position[0]], [0, 1, 0, position[1]],
            [0, 0, 1, position[2]], [0, 0, 0, 1]]
    return matmul(move, matmul(rz, matmul(ry, matmul(rx, sc))))


def _props(shape):
    out = {}
    for p in shape.findall('property'):
        out[p.get('name')] = p.get('value')
    return out


#: How deep a prefab may nest before we assume it references itself.
MAX_DEPTH = 8


def _walk(shape, parent, out, library=None, depth=0, seen=()):
    """Collect static meshes, following nested prefabs.

    A `.prefab` may place other prefabs -- `Tower2x3` places `StainedGlass1` --
    and a nested `PrefabShape` carries a `Filename` and **no inline children**,
    exactly like one in a Layer. Without following them their contents are simply
    absent: windows, candles and the like.

    `seen` guards against a prefab that reaches itself, which would otherwise
    recurse until the stack gives out.
    """
    p = _props(shape)
    local = compose(_triple(p.get('Position')),
                    _triple(p.get('Orientation')),
                    _triple(p.get('Scaling'), (1.0, 1.0, 1.0)))
    here = matmul(parent, local)
    kind = shape.get('class')

    if kind == 'StaticMeshShape':
        mesh = p.get('MeshFileName')
        if mesh and p.get('Visible', 'True') != 'False':
            out.append({
                'mesh': mesh,
                'matrix': here,
                'collides': p.get('CollisionBitmask', '0') not in ('0', '', None),
                'name': p.get('ShapeName') or '',
                'key': p.get('ObjectKey') or '',
            })
    elif kind == 'PrefabShape' and library is not None:
        name = p.get('Filename') or p.get('FileName')
        key = str(name).replace('\\', '/').lower()
        if (name and p.get('Visible', 'True') != 'False'
                and depth < MAX_DEPTH and key not in seen):
            for part in library.contents(name, depth + 1, seen + (key,)):
                out.append({
                    'mesh': part['mesh'],
                    'matrix': matmul(here, part['matrix']),
                    'collides': part['collides'],
                    'name': part['name'],
                    'key': part.get('key', ''),
                })

    for child in shape.findall('shape'):
        _walk(child, here, out, library, depth, seen)


def expand(data, library=None, depth=0, seen=()):
    """Every static mesh in one `.prefab`, with its transform relative to the prefab."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    out = []
    for shape in root.findall('shape'):
        _walk(shape, identity(), out, library, depth, seen)
    return out


class Library:
    """Prefab contents, read out of data.zip on demand and cached."""

    def __init__(self, zip_path=None):
        from riftlord.formats.omd2 import trapgrids as T
        self.zf = zipfile.ZipFile(zip_path or T.DEFAULT_DATA_ZIP)
        self.names = {n.replace('\\', '/').lower(): n for n in self.zf.namelist()}
        self._cache = {}

    def contents(self, file_name, depth=0, seen=()):
        key = str(file_name).replace('\\', '/').lower()
        # Only the top-level read is cached: a nested expansion carries the chain
        # that got there, and caching it under the same key would leak one
        # prefab's ancestry into another's.
        if depth == 0 and key in self._cache:
            return self._cache[key]
        real = self.names.get(key)
        got = []
        if real:
            try:
                got = expand(self.zf.read(real), self, depth, seen)
            except Exception:                        # noqa: BLE001
                got = []
        if depth == 0:
            self._cache[key] = got
        return got


def instances_for(scene, zip_path=None):
    """Every mesh a scene's prefabs place, already in the scene's own space.

    Each part names the PLACEMENT that put it there (`copy`) as well as the prefab file
    (`prefab`), and carries that placement's own transform (`at`). Without the placement
    id the parts of one archway are indistinguishable from the parts of the next, and a
    prefab is the unit a converter wants: TheCrunch places 17 distinct prefabs, and
    60% of its 8,541 mesh instances come out of them. Converting `2WideArch.prefab` to
    an OMD3 archway once beats matching its five block meshes 440 times.
    """
    from riftlord.formats.omd2 import clr
    from riftlord.formats.omd2 import trapgrids as T
    from riftlord.formats.omd2.nrbf import parse
    lib = Library(zip_path)
    out = []
    seen = {}
    with zipfile.ZipFile(zip_path or T.DEFAULT_DATA_ZIP) as zf:
        for name in T.scene_layers(zf, scene):
            try:
                graph = parse(zf.read(name))
            except Exception:                        # noqa: BLE001
                continue
            for sh in graph.by_class('VisionEditorPlugin.Shapes.PrefabShape'):
                if sh.get('_bVisible') is False:
                    continue
                world = compose(clr.vec3(sh.get('_position')) or (0, 0, 0),
                                clr.vec3(sh.get('_orientation')) or (0, 0, 0),
                                clr.vec3(sh.get('_scale')) or (1, 1, 1))
                file_name = str(sh.get('_fileName') or '')
                #: count placements under the SAME key the id is built from. Keyed on
                #: the raw name instead, two spellings of one prefab each started at
                #: zero and both produced `#0`, fusing two placements a level apart
                #: into one "copy" -- which measured WallCandle1 as 4,213uu wide.
                key = file_name.replace('\\', '/').lower()
                seen[key] = seen.get(key, -1) + 1
                copy = '%s#%d' % (key, seen[key])
                for part in lib.contents(sh.get('_fileName')):
                    out.append({
                        'mesh': part['mesh'],
                        'matrix': matmul(world, part['matrix']),
                        'collides': part['collides'],
                        'name': part['name'],
                        'key': part.get('key', ''),
                        'prefab': file_name,
                        'copy': copy,
                        'at': world,
                        'local': part['matrix'],
                    })
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--scene')
    ap.add_argument('--file')
    args = ap.parse_args(argv)

    if args.file:
        lib = Library()
        got = lib.contents(args.file)
        print('%s: %d mesh(es)' % (args.file, len(got)))
        for part in got[:8]:
            m = part['matrix']
            print('   %-44s at (%.0f, %.0f, %.0f)'
                  % (part['mesh'], m[0][3], m[1][3], m[2][3]))
        return 0

    if args.scene:
        got = instances_for(args.scene)
        by = collections.Counter(p['prefab'] for p in got)
        print('%s: %d mesh instance(s) from %d prefab file(s)'
              % (args.scene, len(got), len(by)))
        for f, n in by.most_common(8):
            print('   %4d  %s' % (n, f))
        meshes = collections.Counter(p['mesh'] for p in got)
        print('   commonest meshes: %s'
              % [m.rsplit('\\', 1)[-1] for m, _n in meshes.most_common(5)])
        return 0

    ap.error('need --scene or --file')


if __name__ == '__main__':
    raise SystemExit(main())
