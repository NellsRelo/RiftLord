"""Measure an Orcs Must Die 2 mesh: its bounding box, in OMD2 units.

    python3 omd2mesh.py --mesh art/Act2/floors/FloorMain1.vmesh
    python3 omd2mesh.py --survey
    python3 omd2mesh.py --scene TwistedHalls

## Why this exists

A level's geometry is `StaticMeshShape` entities, each naming a `.vmesh` and
carrying a position, orientation and scale. Converting one to an OMD3 kit piece
needs the mesh's **footprint in cells**, and that cannot be guessed from the
name: `Thickness2` and `Wall1_1` are both walls and are not the same size.

## The format

`.vcolmesh` is the collision hull, and it is the right source: what a converted
level needs is the shape you can stand on and walk into, not the silhouette.
It is a `VBIN` container -- magic, 8 version bytes, then `4CC + uint32 size`
chunks with the tag stored reversed. The chunks are **not a flat sequence**:
`FloorMain1.vcolmesh` holds a 12-byte `COLM` at offset 12, four bytes of padding,
an empty `COLM`, and only then the record that matters, `VIND` at offset 44 --
so it is located by scanning for the tag rather than by walking sizes:

    uint32  vertex count
    uint32  index count
    float32 vertices[3 * vertex count]
    uint32  indices[index count]

A found record proves itself: the declared size must equal
`8 + 12 * vertices + 4 * indices`. On `FloorMain1.vcolmesh` that is 8 + 96 + 144 =
248, exactly what its header says, and the sentinel `VIND` the file ends with
(size 0xffffffff) fails the check rather than being read as geometry.

## What the measurements say

The kit is modular on a **400-unit** tile, with walls 600 tall:

| mesh | bounds | cells | anchor |
|---|---|---|---|
| `Act2/floors/FloorMain1` | (-200,-200,-100)..(200,200,0) | 4 x 4 x 1 | centre |
| `Act2/Ceilings/ceiling` | (-200,-200,-100)..(200,200,3.8147e-06) | 4 x 4 x 1 | centre |
| `Act2/Wall/Wall1_1` | (-100,0,0)..(1.31134e-05,400,600) | 1 x 4 x 6 | other/corner |

So a floor tile is 4 x 4 cells, a ceiling the same, and a wall panel is
4 cells long, 6 tall and 1 thick.

Two independent measurements agree on that 400: these bounds, and the spacing of
the instances themselves (768 ceilings and 678 floors sit on a 400-unit lattice,
walls step 600 in Z).

**The origin convention is per-mesh and must be read, not assumed.** A floor is
centred on its tile; a wall is anchored at its corner. Assuming either one
universally offsets half the level by half a tile, which is the kind of error
that looks like a physics bug rather than a conversion bug.
"""

import argparse
import collections
import json
import math
import os
import struct
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.core import transform  # noqa: E402

#: OMD2's modular kit tile, measured from both collision bounds and the spacing
#: of placed instances.
OMD2_TILE = 400.0

#: How close two copies of one mesh must be to count as the same object.
_DUPE = 30.0


def _mesh_key(mesh):
    return str(mesh).replace('\\', '/').lower()


#: OMD2's trap-grid cell. Taken from `transform` rather than restated, because it
#: was restated here as 100 and stayed 100 after the measurement corrected it to
#: 300 -- so this module's reports disagreed with the converter's own arithmetic.
OMD3_CELL = transform.OMD3_CELL


def _default_zip():
    from riftlord.formats.omd2 import trapgrids
    return trapgrids.DEFAULT_DATA_ZIP


def vind_records(buf):
    """Every self-consistent `VIND` record in a VBIN file, as (vertices, count).

    Found by scanning 4-byte-aligned offsets for the tag -- stored reversed, so
    `VIND` appears as `DNIV` -- because the container's chunks are not laid out
    contiguously and a size-walk stops at the first padding.

    A candidate is accepted only if it proves itself: the size the header declares
    must equal the size its own counts imply. That rejects padding, and it rejects
    the sentinel `VIND` the files end with, whose size is 0xffffffff.
    """
    if buf[:4] != b'VBIN':
        return
    for off in range(0, len(buf) - 8, 4):
        if buf[off:off + 4] != b'DNIV':
            continue
        size, = struct.unpack_from('<I', buf, off + 4)
        body = off + 8
        if size < 8 or body + size > len(buf):
            continue
        count, indices = struct.unpack_from('<II', buf, body)
        if count <= 0:
            continue
        if 8 + count * 12 + indices * 4 != size:
            continue
        yield [struct.unpack_from('<3f', buf, body + 8 + i * 12)
               for i in range(count)], indices


def vertices(buf):
    """Every vertex of a `.vcolmesh`, as (x, y, z) in OMD2 units."""
    out = []
    for verts, _indices in vind_records(buf):
        out.extend(verts)
    return out


def bounds(buf):
    """(min, max) of a `.vcolmesh`, or None if it carries no geometry."""
    verts = vertices(buf)
    if not verts:
        return None
    lo = tuple(min(v[i] for v in verts) for i in range(3))
    hi = tuple(max(v[i] for v in verts) for i in range(3))
    return lo, hi


def anchor(lo, hi, tolerance=1.0):
    """Where the mesh's own origin sits in its box: 'centre', 'corner' or 'other'.

    A floor is centred and a wall is corner-anchored, so a converter that assumes
    one convention misplaces the other by half a tile.
    """
    out = []
    for i in range(2):                               # X and Y; Z is thickness
        mid = (lo[i] + hi[i]) / 2.0
        if abs(mid) <= tolerance:
            out.append('centre')
        elif abs(lo[i]) <= tolerance:
            out.append('corner')
        else:
            out.append('other')
    if out[0] == out[1]:
        return out[0]
    return '%s/%s' % tuple(out)


def cells(lo, hi, scale=(1.0, 1.0, 1.0)):
    """A mesh's extent in OMD3 trap cells, as exact floats.

    The worlds are 1:1 (transform.WORLD_SCALE), so this is the extent over OMD3's
    300uu cell. Returned unrounded, because a mesh that comes out
    at 4.5 cells is telling us something a rounded 4 would hide.
    """
    return tuple((hi[i] - lo[i]) * scale[i] / OMD3_CELL for i in range(3))


class Library:
    """Bounding boxes for OMD2's meshes, read from data.zip on demand."""

    def __init__(self, zip_path=None):
        self.zip_path = zip_path or _default_zip()
        self._zf = zipfile.ZipFile(self.zip_path)
        self._names = {n.lower(): n for n in self._zf.namelist()}
        self._cache = {}

    def _entry(self, mesh):
        """data.zip uses forward slashes; the Layer files use backslashes."""
        key = str(mesh).replace('\\', '/').lower()
        if key.endswith('.vmesh'):
            key = key[:-len('.vmesh')] + '.vcolmesh'
        return self._names.get(key)

    def bounds(self, mesh):
        """(min, max) for one mesh, or None if it has no collision hull."""
        if mesh in self._cache:
            return self._cache[mesh]
        name = self._entry(mesh)
        got = None
        if name:
            try:
                got = bounds(self._zf.read(name))
            except Exception:                        # noqa: BLE001
                got = None
        self._cache[mesh] = got
        return got

    def footprint(self, mesh, scale=(1.0, 1.0, 1.0)):
        """{'cells', 'anchor', 'min', 'max'} for a mesh, or None."""
        got = self.bounds(mesh)
        if not got:
            return None
        lo, hi = got
        return {'min': lo, 'max': hi, 'anchor': anchor(lo, hi),
                'cells': cells(lo, hi, scale)}


def decompose(matrix):
    """(orientation, scale) for a composed 4x4, in OMD2's own convention.

    A prefab child's transform arrives composed, and used to be reported as
    `orientation (0, 0, 0)` and `scale (1, 1, 1)` with the truth left in the matrix.
    Anything drawing from the matrix was then right and everything testing the
    angles was wrong -- `meshconvert.is_axis_aligned` passed every prefab child, so
    a wall turned forty-five degrees inside a prefab was rasterised as the
    axis-aligned slab of its bounding box. On TheEdge that is 195 of 294 shapes.

    The rotation is read back in the order the rest of the tooling composes it,
    Rz(yaw) . Ry(pitch) . Rx(roll). A mirrored transform has no decomposition into a
    rotation and positive scales, so the mirror is carried on X as a negative scale.
    """
    cols = [[matrix[r][c] for r in range(3)] for c in range(3)]
    scale = [math.sqrt(sum(v * v for v in col)) or 1.0 for col in cols]
    det = (cols[0][0] * (cols[1][1] * cols[2][2] - cols[1][2] * cols[2][1])
           - cols[1][0] * (cols[0][1] * cols[2][2] - cols[0][2] * cols[2][1])
           + cols[2][0] * (cols[0][1] * cols[1][2] - cols[0][2] * cols[1][1]))
    if det < 0:
        scale[0] = -scale[0]
    r = [[cols[c][row] / (scale[c] or 1.0) for c in range(3)] for row in range(3)]
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, -r[2][0]))))
    yaw = math.degrees(math.atan2(r[1][0], r[0][0]))
    roll = math.degrees(math.atan2(r[2][1], r[2][2]))
    return (yaw, pitch, roll), tuple(scale)


def scene_meshes(scene, zip_path=None, prefabs=True):
    """Every static mesh instance in one OMD2 level.

    Includes the meshes its **prefabs** place. A `PrefabShape` carries a file name
    and no children, so the Layer alone is a fraction of the level: Servant
    Entrance has 1,883 mesh shapes in its Layers and 3,121 more inside its
    prefabs, and those include `Ground4x4`, `Ceiling4x4` and `WallBase4_6` -- the
    structure, not the trimmings. Reading only the Layers is why converted levels
    had holes in their walls.

    A prefab child arrives with a composed `matrix` instead of angles, because
    composing two rotations does not give a rotation whose Euler angles are the
    sum of the parts. Consumers should prefer `matrix` when it is present.
    """
    from riftlord.formats.omd2 import clr
    from riftlord.formats.omd2 import trapgrids as T
    from riftlord.formats.omd2.nrbf import parse
    zip_path = zip_path or _default_zip()
    out = []
    with zipfile.ZipFile(zip_path) as zf:
        for name in T.scene_layers(zf, scene):
            try:
                graph = parse(zf.read(name))
            except Exception:                        # noqa: BLE001
                continue
            for sh in graph.by_class(
                    'VisionEditorPlugin.Shapes.StaticMeshShape'):
                out.append({
                    'mesh': str(sh.get('_meshFile') or ''),
                    'layer': name.rsplit('/', 1)[-1],
                    'position': clr.vec3(sh.get('_position')) or (0.0, 0.0, 0.0),
                    'orientation': clr.vec3(sh.get('_orientation')) or (0.0, 0.0, 0.0),
                    'scale': clr.vec3(sh.get('_scale')) or (1.0, 1.0, 1.0),
                    'collides': bool(sh.get('_iCollisionBitmask')),
                    'visible': bool(sh.get('_bVisible')),
                    'key': str(sh.get('_objectKey') or ''),
                    'name': str(sh.get('_shapeName') or ''),
                })
    if prefabs:
        from riftlord.formats.omd2 import omd2prefab

        # Some prefab contents are ALSO present in the Layer, expanded when the
        # designer broke a copy apart. Drawing both puts two identical meshes in
        # one place, which reads as a doubled wall or a second floor. Measured at
        # 3-12% of prefab instances, so worth removing and not worth agonising
        # over: same mesh, same spot, one of them goes.
        placed = collections.defaultdict(list)
        for inst in out:
            px, py, pz = inst['position']
            placed[(_mesh_key(inst['mesh']),
                    round(px / _DUPE), round(py / _DUPE),
                    round(pz / _DUPE))].append(inst['position'])

        def already_there(mesh, at):
            key = _mesh_key(mesh)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        cell = (key, round(at[0] / _DUPE) + dx,
                                round(at[1] / _DUPE) + dy,
                                round(at[2] / _DUPE) + dz)
                        for other in placed.get(cell, ()):
                            if (abs(other[0] - at[0]) < _DUPE
                                    and abs(other[1] - at[1]) < _DUPE
                                    and abs(other[2] - at[2]) < _DUPE):
                                return True
            return False

        for part in omd2prefab.instances_for(scene, zip_path):
            m = part['matrix']
            here = (m[0][3], m[1][3], m[2][3])
            if already_there(part['mesh'], here):
                continue
            # The angles and scales are read back out of the matrix rather than
            # left at their defaults, so a consumer that tests them sees the same
            # transform as one that draws from it.
            orientation, scale = decompose(m)
            out.append({
                'mesh': part['mesh'],
                'layer': part['prefab'].rsplit('\\', 1)[-1],
                'position': (m[0][3], m[1][3], m[2][3]),
                'orientation': orientation,
                'scale': scale,
                'matrix': m,
                'collides': part['collides'],
                'visible': True,
                'key': part.get('key', ''),
                'name': part['name'],
                'prefab': part['prefab'],
                'copy': part.get('copy'),
                'at': part.get('at'),
                'local': part.get('local'),
            })
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--mesh')
    ap.add_argument('--scene')
    ap.add_argument('--survey', action='store_true')
    ap.add_argument('--limit', type=int, default=20)
    args = ap.parse_args(argv)
    lib = Library()

    if args.mesh:
        got = lib.footprint(args.mesh)
        if not got:
            print('%s: no collision hull' % args.mesh)
            return 1
        print('%s\n   min %s\n   max %s\n   cells %s\n   anchor %s'
              % (args.mesh,
                 tuple(round(v, 1) for v in got['min']),
                 tuple(round(v, 1) for v in got['max']),
                 tuple(round(v, 2) for v in got['cells']), got['anchor']))
        return 0

    if args.scene:
        insts = scene_meshes(args.scene)
        by_mesh = collections.Counter(i['mesh'] for i in insts)
        have = miss = 0
        print('%s: %d instance(s), %d distinct mesh(es)'
              % (args.scene, len(insts), len(by_mesh)))
        print('%-44s %5s %-14s %-13s %s'
              % ('mesh', 'count', 'cells', 'anchor', 'collides'))
        for mesh, n in by_mesh.most_common(args.limit):
            got = lib.footprint(mesh)
            coll = sum(1 for i in insts if i['mesh'] == mesh and i['collides'])
            if got:
                have += n
                print('%-44s %5d %-14s %-13s %d'
                      % (mesh.replace('\\', '/')[4:], n,
                         '%.2f x %.2f x %.2f' % got['cells'],
                         got['anchor'], coll))
            else:
                miss += n
                print('%-44s %5d %-14s %-13s %d'
                      % (mesh.replace('\\', '/')[4:], n, '(no hull)', '-', coll))
        total_have = sum(n for m, n in by_mesh.items() if lib.footprint(m))
        print('\n%d of %d instance(s) have a measured hull (%.0f%%)'
              % (total_have, len(insts), 100.0 * total_have / max(len(insts), 1)))
        return 0

    if args.survey:
        from riftlord.formats.omd2 import trapgrids as T
        with zipfile.ZipFile(lib.zip_path) as zf:
            scenes = T.list_scenes(zf)
        print('%-28s %7s %7s %7s %7s  %s'
              % ('scene', 'meshes', 'kinds', 'hulls', 'collide', 'tile-aligned'))
        for scene in scenes:
            try:
                insts = scene_meshes(scene)
            except Exception as exc:                 # noqa: BLE001
                print('%-28s  could not read: %s' % (scene, exc))
                continue
            kinds = {i['mesh'] for i in insts}
            hulls = sum(1 for i in insts if lib.bounds(i['mesh']))
            coll = sum(1 for i in insts if i['collides'])
            aligned = 0
            for i in insts:
                got = lib.footprint(i['mesh'], i['scale'])
                if got and all(abs(c - round(c)) < 0.02 for c in got['cells'][:2]):
                    aligned += 1
            print('%-28s %7d %7d %7d %7d  %d (%.0f%%)'
                  % (scene, len(insts), len(kinds), hulls, coll, aligned,
                     100.0 * aligned / max(len(insts), 1)))
        return 0

    ap.error('need --mesh, --scene or --survey')


if __name__ == '__main__':
    raise SystemExit(main())
