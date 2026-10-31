"""Where a Fab piece's simple collision stands proud of what it draws: an invisible block a
hero walks into or onto.

A library mesh collides with its BodySetup's convex elements and boxes unless it collides as
drawn (CTF_UseComplexAsSimple: its cooked trimesh is the render mesh). Both are rasterised
top-down in the mesh's own frame on CELL cells: the drawn top is the highest render triangle
over each cell (the mesh's lowest z where nothing is drawn), the collision top the highest
simple-shape triangle. A cell is proud where the collision top is more than STEP over the
drawn top; a piece is flagged when its proud cells cover PROUD_AREA or more and its shapes
are the converter's own (GATE_SOURCES: a stair ramp, a box round the mesh). A pack's authored
hulls are listed by the command line but are the pack's design.

    python3 -m riftlord.mapeditor.fabcollision PACK [--all] [--png DIR]
"""

import argparse
import math
import struct
import threading

import numpy as np

from riftlord.mapeditor import fablibrary

#: Raster cell edge (uu).
CELL = 25.0
#: Collision this far over the drawn top is proud (uu). Measured over the 23 installed
#: libraries' stair ramps: a ramp over straight treads stands at most 103 uu over them
#: (WinterCity SM_Steps_6; most 48-85); ramps hulled across open space 135-1114 uu.
STEP = 120.0
#: Proud area that flags a piece (uu^2): a hero's footprint (catalogue conventions hero width 84).
PROUD_AREA = 84.0 * 84.0
#: Most cells a mesh is rasterised on, per axis; larger meshes use larger cells.
MAX_CELLS = 1024
#: Most sample points per triangle edge.
MAX_STEPS = 600

#: Pictures are enlarged to at least this many pixels high.
PNG_HEIGHT = 240
#: Collision sources (catalogue `collision.source`) the gate checks: shapes the converter made.
GATE_SOURCES = ('stair_ramp', 'box_fallback')

_CACHE = {}
_LOCK = threading.Lock()


def _top(tris, lo, cell, shape):
    """Highest z over each cell of the triangles (n, 3, 3); -inf where none."""
    top = np.full(shape, -np.inf)
    if not len(tris):
        return top
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    edge = np.maximum(np.maximum(np.linalg.norm((b - a)[:, :2], axis=1),
                                 np.linalg.norm((c - b)[:, :2], axis=1)),
                      np.linalg.norm((a - c)[:, :2], axis=1))
    steps = np.clip(np.ceil(edge / (cell / 2.0)), 1, MAX_STEPS).astype(int)
    for n in np.unique(steps):
        pick = steps == n
        i, j = np.meshgrid(np.arange(n + 1), np.arange(n + 1), indexing='ij')
        keep = i + j <= n
        u, v = i[keep] / n, j[keep] / n
        aa, bb, cc = a[pick], b[pick], c[pick]
        for k in range(0, len(aa), max(1, 2000000 // len(u))):
            sl = slice(k, k + max(1, 2000000 // len(u)))
            p = (aa[sl, None, :] + (bb[sl, None, :] - aa[sl, None, :]) * u[None, :, None]
                 + (cc[sl, None, :] - aa[sl, None, :]) * v[None, :, None]).reshape(-1, 3)
            ix = np.clip(((p[:, 0] - lo[0]) / cell).astype(int), 0, shape[0] - 1)
            iy = np.clip(((p[:, 1] - lo[1]) / cell).astype(int), 0, shape[1] - 1)
            np.maximum.at(top, (ix, iy), p[:, 2])
    return top


def _rotator(pitch, yaw, roll):
    """Unreal's FRotationMatrix rows-as-axes (column vectors: x, y, z axes)."""
    sp, cp = math.sin(math.radians(pitch)), math.cos(math.radians(pitch))
    sy, cy = math.sin(math.radians(yaw)), math.cos(math.radians(yaw))
    sr, cr = math.sin(math.radians(roll)), math.cos(math.radians(roll))
    x = (cp * cy, cp * sy, sp)
    y = (sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp)
    z = (-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp)
    return np.array([x, y, z]).T


#: A box's 12 triangles over its corners (index = 4x + 2y + z, each 0 or 1).
BOX_TRIS = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
                     [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])


def simple_shapes(pack, entry):
    """[(vertices (n, 3), triangles (m, 3))] of a library mesh's simple collision in its own
    frame, or None when it collides as drawn (complex as simple)."""
    from riftlord.core import uetags
    from riftlord.formats.omd3 import omd3smwrite
    if (entry.get('collision') or {}).get('trace') == 'complex_as_simple':
        return None
    blobs = fablibrary.read_package(pack, entry['package'])
    t = omd3smwrite.Template(entry['package'], {'.uasset': blobs['.uasset'],
                                                '.uexp': blobs['.uexp']})
    bs = t.body_setup()
    agg = uetags.get(bs['props'], 'AggGeom') if bs else None
    out = []
    if agg is None:
        return out
    conv = uetags.get(agg.value, 'ConvexElems')
    for e in (conv.value[2] if conv else []):
        vd = uetags.get(e, 'VertexData').value[2]
        idx = uetags.get(e, 'IndexData')
        pts = np.frombuffer(b''.join(vd), '<f4').reshape(-1, 3).astype(np.float64)
        tri = np.asarray(idx.value if idx else [], np.int64).reshape(-1, 3)
        if len(pts) and len(tri):
            out.append((pts, tri))
    boxes = uetags.get(agg.value, 'BoxElems')
    for e in (boxes.value[2] if boxes else []):
        def val(name, default):
            p = uetags.get(e, name)
            return p.value if p is not None else default
        centre = val('Center', b'\0' * 12)
        centre = np.array(struct.unpack('<3f', centre) if isinstance(centre, bytes) else centre)
        rot = val('Rotation', b'\0' * 12)
        rot = struct.unpack('<3f', rot) if isinstance(rot, bytes) else rot
        size = np.array([val('X', 0.0), val('Y', 0.0), val('Z', 0.0)], np.float64)
        axes = _rotator(*rot)
        corners = np.array([centre + axes @ ((np.array([x, y, z]) - 0.5) * size)
                            for x in (0, 1) for y in (0, 1) for z in (0, 1)])
        out.append((corners, BOX_TRIS))
    return out


def measure(pack, entry):
    """{'trace', 'proudArea' (uu^2), 'maxRise' (uu), 'cell', 'lo', 'drawn', 'collision'} for
    one library mesh, cached per library build; 'proudArea' 0 for a piece that collides as
    drawn."""
    key = (pack, entry['package'], fablibrary.stamp(pack))
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
    trace = (entry.get('collision') or {}).get('trace')
    shapes = simple_shapes(pack, entry)
    got = {'trace': trace, 'proudArea': 0.0, 'maxRise': 0.0}
    if shapes:
        m = fablibrary.mesh(fablibrary.mesh_key(entry['package']))
        P = np.asarray(m['verts'], np.float64).reshape(-1, 3)
        T = np.asarray(m['tris'], np.int64).reshape(-1, 3)
        hulls = np.vstack([v[t] for v, t in shapes])
        lo = np.minimum(P.min(0), hulls.reshape(-1, 3).min(0))
        hi = np.maximum(P.max(0), hulls.reshape(-1, 3).max(0))
        cell = max(CELL, float((hi - lo)[:2].max()) / MAX_CELLS)
        shape = (int((hi[0] - lo[0]) / cell) + 1, int((hi[1] - lo[1]) / cell) + 1)
        drawn = _top(P[T], lo, cell, shape)
        coll = _top(hulls, lo, cell, shape)
        floor = np.where(np.isfinite(drawn), drawn, P[:, 2].min())
        rise = np.where(np.isfinite(coll), coll - floor, 0.0)
        proud = rise > STEP
        got.update({'proudArea': float(proud.sum() * cell * cell),
                    'maxRise': float(rise.max()) if rise.size else 0.0,
                    'cell': cell, 'lo': lo.tolist(), 'drawn': drawn, 'collision': coll})
    with _LOCK:
        _CACHE[key] = got
    return got


def proud(got):
    return got['proudArea'] >= PROUD_AREA


def flagged(got, entry):
    """Proud, with shapes the converter made (GATE_SOURCES)."""
    return proud(got) and ((entry or {}).get('collision') or {}).get('source') in GATE_SOURCES


def describe(entry, got):
    return ('%s: its simple collision stands up to %.0f uu over what it draws across %.1f m^2 '
            '(collision %s, trace %s)' % (entry.get('name'), got['maxRise'], got['proudArea'] / 1e4,
                                         (entry.get('collision') or {}).get('source'), got['trace']))


def problems(plan):
    """[line] for each placed Fab mesh (once per mesh) whose collision is proud of its drawing."""
    from riftlord.mapeditor import fabemit
    placed, _problems = fabemit.resolve(plan)
    out, seen = [], set()
    from riftlord.mapeditor import fablibrary
    for _n, e, entry, _m in placed:
        #: a piece placed with collision off has no collision to stand proud
        if entry['package'] in seen or not fablibrary.collides(entry):
            continue
        seen.add(entry['package'])
        got = measure(e['pack'], entry)
        if flagged(got, entry):
            out.append(describe(entry, got))
    return out


def gate(plan):
    """(name, ok, detail) for the probe: no placed Fab mesh has collision proud of its drawing."""
    bad = problems(plan)
    return ('Fab: simple collision follows what each piece draws (no invisible blocks over %.0f uu)'
            % STEP, not bad, '; '.join(bad[:3]) if bad else 'none proud')


def png(pack, entry, path):
    """Drawn top, collision top and the proud cells side by side, as a PNG at `path`."""
    from PIL import Image
    got = measure(pack, entry)
    if 'drawn' not in got:
        return None
    d, c = got['drawn'], got['collision']
    finite = np.concatenate([d[np.isfinite(d)], c[np.isfinite(c)]])
    z0, z1 = float(finite.min()), float(finite.max()) or 1.0

    def grey(a):
        g = np.where(np.isfinite(a), (a - z0) / max(z1 - z0, 1e-6) * 200 + 55, 0)
        return np.stack([g, g, g], -1).astype(np.uint8)
    floor = np.where(np.isfinite(d), d, z0)
    rise = np.where(np.isfinite(c), c - floor, 0)
    proud = np.zeros(d.shape + (3,), np.uint8)
    proud[rise > STEP] = (230, 60, 50)
    proud[(rise <= STEP) & np.isfinite(c)] = (70, 160, 90)
    gap = np.full((4, d.shape[1], 3), 255, np.uint8)
    #: x across, y up: the three pictures stacked along x, then turned to image rows
    row = np.concatenate([grey(d), gap, grey(c), gap, proud], axis=0)
    image = Image.fromarray(np.flipud(np.transpose(row, (1, 0, 2))))
    grow = max(1, int(math.ceil(PNG_HEIGHT / image.height)))
    image.resize((image.width * grow, image.height * grow), Image.NEAREST).save(path)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('pack')
    ap.add_argument('--all', action='store_true', help='list every mesh, not only flagged ones')
    ap.add_argument('--authored', action='store_true',
                    help='also list proud authored shapes (the hulls that came with the pack)')
    ap.add_argument('--png', help='write a drawn / collision / proud picture per listed mesh here')
    args = ap.parse_args(argv)
    import os
    cat = fablibrary.catalogue(args.pack)
    count = 0
    for entry in cat.get('meshes') or []:
        if entry.get('status') != 'converted':
            continue
        got = measure(args.pack, entry)
        bad = flagged(got, entry)
        shown = bad or args.all or (args.authored and proud(got))
        count += bad
        if shown:
            print('%s  %s' % ('PROUD' if bad else 'proud' if proud(got) else 'ok   ',
                              describe(entry, got)))
            if args.png and proud(got):
                os.makedirs(args.png, exist_ok=True)
                png(args.pack, entry, os.path.join(args.png, '%s.png' % entry['name']))
    print('%d meshes whose converter-made collision is proud of their drawing' % count)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
