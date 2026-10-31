"""Pull renderable geometry out of both games, for the assembly tool to draw.

    python3 meshhull.py --omd3 --out omd3-hulls.json
    python3 meshhull.py --omd2 --out omd2-hulls.json
    python3 meshhull.py --show RailingShortC

A visual editor needs shapes, not boxes. Cooked OMD3 render meshes are packed
vertex buffers and a serious job to decode, but **collision geometry is reachable
in both games** and is close enough to place things by.

## OMD3

`BodySetup.AggGeom` holds `ConvexElems`, and each `FKConvexElem` carries
`VertexData` and `IndexData`. Both are arrays of *tagged* properties, so
`VertexData` for eight vertices is 4 + 49 + 8 * 12 = 149 bytes -- the same 49-byte
inner tag that appears everywhere else in this format. Reading vertices at the
array's payload start gives the tag and decodes as zeros.

Measured over a 140-mesh sample: 107 meshes have a convex hull, 27 have no
collision geometry at all, 6 use primitives. Of the 107, 46 are eight vertices or
fewer -- a box -- and 61 are richer. So roughly three meshes in five gain a
recognisable silhouette.

## OMD2

Free, and better: `.vcolmesh` already parses to vertices and indices via
`omd2mesh.vind_records`. Every mesh has one.
"""

import argparse
import collections
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.core import gamefiles  # noqa: E402
from riftlord.build import levelbuild  # noqa: E402
from riftlord.formats.omd3 import meshcat  # noqa: E402
from riftlord.formats.ue.uassetio import Package  # noqa: E402

#: The inner tag on an array of structs.
INNER_TAG = 49


def _array_count(data, at):
    return struct.unpack_from('<i', data, at)[0]


def _walk_to_end(data, names, start, limit):
    """Tagged properties plus the offset just past the None that ends them.

    `meshcat._walk` returns only the properties, which is enough for one struct but
    not for walking a list of them: each element of an array of structs is its own
    property stream, and the next element begins wherever the last one stopped.
    """
    out = {}
    o = start
    while o + 8 <= limit:
        ni = struct.unpack_from('<i', data, o)[0]
        if not 0 <= ni < len(names):
            return out, o
        name = names[ni]
        if name == 'None':
            return out, o + 8
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
        out[name] = (kind, o + head, size)
        o += head + size
    return out, o


def convex_elems(package_path):
    """[{'verts', 'tris'}] for one OMD3 asset's convex collision, or [].

    Every element is read, not just the first. A hull built from several pieces --
    an archway is one -- loses most of its shape if the rest are dropped, and the
    loss is silent because one element still renders.
    """
    ua, ux = gamefiles.package(package_path)
    pkg = Package(ua, ux)
    pkg.load_payloads()
    names = [n for n, _h in pkg.names]
    for e in pkg.exports:
        cls = levelbuild.resolve(
            pkg, struct.unpack_from('<i', e['meta'], 0)[0])[1] or ''
        if cls != 'BodySetup':
            continue
        data = e['data']
        top = meshcat._walk(data, names, 0, len(data))
        if 'AggGeom' not in top:
            return []
        _k, at, size = top['AggGeom']
        agg = meshcat._walk(data, names, at, at + size)
        got = agg.get('ConvexElems')
        if not got:
            return []
        _ak, ia, asize = got
        count = _array_count(data, ia)
        if count <= 0:
            return []
        bodies = ia + 4 + INNER_TAG
        limit = ia + asize
        out = []
        for _ in range(count):
            if bodies >= limit:
                break
            el, bodies = _walk_to_end(data, names, bodies, limit)
            verts, tris = [], []
            if 'VertexData' in el:
                _vk, va, _vs = el['VertexData']
                n = _array_count(data, va)
                verts = [list(struct.unpack_from('<3f', data,
                                                 va + 4 + INNER_TAG + i * 12))
                         for i in range(n)]
            if 'IndexData' in el:
                _ik, ib, _is = el['IndexData']
                n = _array_count(data, ib)
                tris = list(struct.unpack_from('<%di' % n, data, ib + 4))
            if verts:
                out.append({'verts': [[round(v, 2) for v in p] for p in verts],
                            'tris': tris})
        return out
    return []


def build_omd3(out_path, limit=None):
    """Hulls for every mesh in the static mesh catalogue."""
    cat = meshcat.load()
    names = sorted(cat)
    if limit:
        names = names[:limit]
    out, why = {}, collections.Counter()
    for name in names:
        try:
            elems = convex_elems(cat[name]['path'])
        except Exception as exc:                     # noqa: BLE001
            why[type(exc).__name__] += 1
            continue
        if not elems:
            why['no convex collision'] += 1
            continue
        out[name] = {'parts': elems, 'box': cat[name]['size'],
                     'path': cat[name]['path']}
        total = sum(len(p['verts']) for p in elems)
        if len(elems) > 1:
            why['(hull in %d parts)' % min(len(elems), 9)] += 1
        if total <= 8:
            why['(box-shaped hull)'] += 1
    with open(out_path, 'w') as fh:
        json.dump({'hulls': out, 'notes': dict(why)}, fh)
    return out, why


def build_omd2(out_path, scenes=None):
    """Hulls for every OMD2 mesh a level uses, straight from its `.vcolmesh`."""
    from riftlord.formats.omd2 import omd2mesh
    from riftlord.formats.omd2 import trapgrids as T
    import zipfile
    lib = omd2mesh.Library()
    with zipfile.ZipFile(T.DEFAULT_DATA_ZIP) as zf:
        all_scenes = [s for s in T.list_scenes(zf)
                      if not s.endswith('Perf') and s != 'TheCrossingBlue']
    wanted = set()
    for scene in (scenes or all_scenes):
        try:
            for i in omd2mesh.scene_meshes(scene):
                wanted.add(i['mesh'])
        except Exception:                            # noqa: BLE001
            continue
    out, why = {}, collections.Counter()
    for mesh in sorted(wanted):
        name = lib._entry(mesh)
        if not name:
            why['no .vcolmesh'] += 1
            continue
        try:
            buf = lib._zf.read(name)
        except Exception:                            # noqa: BLE001
            why['unreadable'] += 1
            continue
        verts, tris = [], []
        for v, count in omd2mesh.vind_records(buf):
            verts = [[round(x, 2) for x in p] for p in v]
            body = 8 + len(v) * 12
            tris = list(struct.unpack_from('<%di' % count, buf,
                                           _vind_body(buf) + body))
            break
        if not verts:
            why['no geometry'] += 1
            continue
        out[str(mesh).replace('\\', '/').lower()] = {'verts': verts, 'tris': tris}
    with open(out_path, 'w') as fh:
        json.dump({'hulls': out, 'notes': dict(why)}, fh)
    return out, why


def _vind_body(buf):
    """Offset of the accepted VIND record's body, for reading its indices."""
    for off in range(0, len(buf) - 8, 4):
        if buf[off:off + 4] != b'DNIV':
            continue
        size, = struct.unpack_from('<I', buf, off + 4)
        body = off + 8
        if size < 8 or body + size > len(buf):
            continue
        count, indices = struct.unpack_from('<II', buf, body)
        if count > 0 and 8 + count * 12 + indices * 4 == size:
            return body
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--omd3', action='store_true')
    ap.add_argument('--omd2', action='store_true')
    ap.add_argument('--show')
    ap.add_argument('--out')
    ap.add_argument('--limit', type=int)
    args = ap.parse_args(argv)

    if args.show:
        cat = meshcat.load()
        info = cat.get(args.show)
        if not info:
            print('%s is not in the catalogue' % args.show)
            return 1
        elems = convex_elems(info['path'])
        if not elems:
            print('%s: no convex collision' % args.show)
            return 1
        pts = [p for e in elems for p in e['verts']]
        lo = [min(p[i] for p in pts) for i in range(3)]
        hi = [max(p[i] for p in pts) for i in range(3)]
        print('%s: %d part(s), %d vert(s), %d triangle(s)\n   bbox %s .. %s'
              % (args.show, len(elems), len(pts),
                 sum(len(e['tris']) for e in elems) // 3,
                 [round(v) for v in lo], [round(v) for v in hi]))
        return 0

    if args.omd3:
        out, why = build_omd3(args.out or 'omd3-hulls.json', args.limit)
        print('OMD3: %d hull(s) written' % len(out))
        for k, n in why.most_common(6):
            print('   %5d  %s' % (n, k))
        return 0

    if args.omd2:
        out, why = build_omd2(args.out or 'omd2-hulls.json')
        print('OMD2: %d hull(s) written' % len(out))
        for k, n in why.most_common(6):
            print('   %5d  %s' % (n, k))
        return 0

    ap.error('need --omd3, --omd2 or --show')


if __name__ == '__main__':
    raise SystemExit(main())
