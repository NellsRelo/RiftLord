"""Replace a level's SURFACE navgraph with one we generated.

    python3 navsurfinject.py --map Scenario_01 --mode regen --pak
    python3 navsurfinject.py --map Scenario_01 --mode grid --pak

The surface buffer sits **first** in the nav export, with the volumetric buffer after
it, so swapping it for one of a different length is a splice in the middle rather than
the tail splice `navvolinject.set_buffer_bytes` does for flight. The `NavMeshBufferSize`
property sits before both buffers, so rewriting its value does not move anything.

**Offline verification cannot prove this safe.** A stale summary offset parses perfectly
and crashes the game on start (see omd3-package-header-offsets), so every check here is
necessary and not sufficient: the package is re-read from scratch, both buffers must be
found by the ordinary reader, both checksums must verify, and the surface graph must
still parse record for record.

## The two modes, and why both are worth a pak

* **regen** rebuilds the graph from the level's OWN decoded polygons, through our writer
  -- our tree, our constants, no edge ids at all. It changes the serialisation and
  nothing about the geometry, which isolates "is our file acceptable" from "is our
  coverage adequate".
* **grid** throws the geometry away too and lays a flat lattice of quads over the
  walkable bbox. If a wave still walks, then a crude cover is enough and converted maps
  are unblocked, because the authored `OMDPathPoint` chain does the navigation.
"""

import argparse
import os
import struct
import sys
import tempfile
from riftlord.core import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))

import struct          # noqa: E402
from riftlord.build.navigation import navpatch  # noqa: E402
from riftlord.build.navigation import navsurf  # noqa: E402
from riftlord.build.navigation import navsurfgen  # noqa: E402
from riftlord.build.navigation import navvol  # noqa: E402
from riftlord.build.navigation import navvolinject  # noqa: E402
from riftlord.formats.ue import pakv4  # noqa: E402

from riftlord.formats.ue.uassetio import Package  # noqa: E402

OUT = gamepath.beside_code(gamepath.CODE_ROOT, 'patched/navsurfinject')

#: Where the game loads mod paks from.
MODS = os.path.abspath(os.path.join(
    gamepath.WIN64, '..', '..', 'Content', 'Paks', '~mods'))

#: One pak per question, and only ONE may be live: two paks overriding the same map is
#: a race, not a test.
PAKS = {
    'identity': ('721-SurfIdentity_P.pak',
                 "the SHIPPED buffer, byte for byte, through our injector -- is the "
                 "PACKAGING sound?"),
    'mytree': ('722-SurfMyTree_P.pak',
               'shipped records VERBATIM, our tree -- is our TREE acceptable?'),
    'regen': ('719-SurfRegen_P.pak',
              "the shipped polygons re-serialised -- are our RECORDS acceptable?"),
    'grid': ('720-SurfGrid_P.pak',
             'a crude heightfield cover -- is coverage alone enough?'),
}


def set_surface_bytes(uasset, uexp, buf):
    """Install a surface buffer, replacing the one the level ships.

    Returns (uasset, uexp, [(check, ok)]).
    """
    pkg = Package(uasset, uexp)
    before_meta = [bytes(e['meta']) for e in pkg.exports]
    found = navvolinject.nav_export_index(pkg)
    if not found:
        return uasset, uexp, [('a nav-data export with a buffer exists', False)]
    idx, _mesh, props = found
    at = {nm: (st, en) for nm, _t, st, en in props}
    if 'NavMeshBufferSize' not in at:
        return uasset, uexp, [('the export declares NavMeshBufferSize', False)]
    data = bytearray(pkg.exports[idx]['data'])
    mesh_end = at['NavMeshBufferSize'][1]
    old_mesh = struct.unpack_from('<I', data, mesh_end - 4)[0]
    old_vol = 0
    if 'NavVolumesBufferSize' in at:
        old_vol = struct.unpack_from('<I', data, at['NavVolumesBufferSize'][1] - 4)[0]
    #: surface first, then volumes, both at the very end of the export
    start = len(data) - old_vol - old_mesh
    if start < 0:
        return uasset, uexp, [('the stated buffer sizes fit the export', False)]
    data[start:start + old_mesh] = buf
    struct.pack_into('<I', data, mesh_end - 4, len(buf))
    pkg.exports[idx]['data'] = bytes(data)
    pkg.exports[idx]['size'] = len(data)
    out_a, out_x = pkg.write()

    fresh = Package(out_a, out_x)
    meta_ok = True
    for was, now in zip(before_meta, [bytes(e['meta']) for e in fresh.exports]):
        a, b = bytearray(was), bytearray(now)
        for off in (28, 36):                      #: SerialSize, SerialOffset
            a[off:off + 8] = b'\0' * 8
            b[off:off + 8] = b'\0' * 8
        if bytes(a) != bytes(b):
            meta_ok = False
    d = tempfile.mkdtemp(prefix='surfcheck')
    ua, ux = os.path.join(d, 'c.umap'), os.path.join(d, 'c.uexp')
    with open(ua, 'wb') as fh:
        fh.write(out_a)
    with open(ux, 'wb') as fh:
        fh.write(out_x)
    nb = navpatch.nav_buffers(ua, ux)
    got = out_x[nb[1]:nb[1] + nb[2]] if nb else b''
    heads = navsurf.graphs(got) if got else []
    recs_ok = bool(heads)
    for h in heads:
        secs = navsurf.sections(got, h)
        if navsurf.check_sections(got, h, secs) or navsurf.check_tree(
                got, h, secs, navsurf.tree(got, h)):
            recs_ok = False
    vol_ok = True
    if nb and nb[4]:
        vol = out_x[nb[3]:nb[3] + nb[4]]
        vol_ok = navvol.verify(vol)
    return out_a, out_x, [
        ('no export metadata changed except size/offset', meta_ok),
        ('the patched package re-reads', nb is not None),
        ('the surface buffer is found and intact', got == buf),
        ('its checksum verifies', bool(got) and navvol.verify(got)),
        ('both graphs parse record for record', recs_ok),
        ('the volumetric graph still verifies', vol_ok),
    ]


def polygons_for(map_name, mode, cell=600.0):
    """(polygons, bbox, edge ids or None) for the chosen mode."""
    buf = navsurf.buffer(map_name)
    if not buf:
        raise SystemExit('%s ships no surface buffer' % map_name)
    head = navsurf.graphs(buf)[0]
    bbox = list(head['bbox'])
    if mode == 'regen':
        #: The level's own polygons AND its own edge ids, re-serialised by our writer.
        #: Reusing the ids is the point: a long shipped edge is split by its neighbours
        #: so their corners do not coincide, and re-deriving adjacency from geometry
        #: recovers only a fraction of it. Passing the ids through makes `regen` a
        #: near-exact re-serialisation, which is what isolates "is our file acceptable"
        #: from every question about coverage.
        secs = navsurf.sections(buf, head)
        #: the flags byte is still unidentified, so for a re-serialisation it is copied
        #: rather than derived -- that leaves our writer's own choices as the only
        #: variable, which is what makes this the decisive test
        return ([list(s['verts']) for s in secs], bbox,
                [list(s['edge_ids']) for s in secs],
                [int(s['flags'][-2:], 16) for s in secs])
    if mode == 'grid':
        #: A HEIGHTFIELD, not a flat plane. Scenario_01 spans z -529..1176, so one plane
        #: would leave every upper floor with no surface under it and the test would
        #: fail for a reason that says nothing about generation. Each cell takes the
        #: height of the nearest shipped polygon, and a cell with none nearby is left
        #: out -- so the cover follows the real floor while keeping none of its shape,
        #: none of its vertex positions and no connectivity at all.
        secs = navsurf.sections(buf, head)
        #: ids are allocated from the lattice's own shared edges, which on a grid is
        #: exact -- every neighbouring pair shares two corners precisely
        #: The LAYERED CONTAINMENT cover is the best measured: 88% of walkable area in
        #: one component at 300uu, with the whole 7-point route connected. A
        #: corner-sampled sloped version was tried and is worse -- requiring all four
        #: corners to have floor under them drops most cells, leaving 12 polygons at
        #: 600uu -- and an overlap-based one is worse again (44-59%), because a
        #: polygon's bbox overlaps cells its surface never reaches and the spurious
        #: heights fragment the graph.
        return heightfield_cover(secs, bbox, cell=cell), bbox, None, None
    raise SystemExit('unknown mode %s' % mode)


def _inside(poly, x, y):
    """Point in polygon, on the xy projection."""
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i][0], poly[i][1]
        x2, y2 = poly[(i + 1) % n][0], poly[(i + 1) % n][1]
        if (y1 > y) != (y2 > y):
            t = (y - y1) / (y2 - y1) if y2 != y1 else 0.0
            if x < x1 + t * (x2 - x1):
                inside = not inside
    return inside


def _plane_z(poly, x, y):
    """The height of a polygon's own plane at (x, y), or its centroid z if degenerate."""
    import math as _m
    n = len(poly)
    nx = ny = nz = 0.0
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        nx += (a[1] - b[1]) * (a[2] + b[2])
        ny += (a[2] - b[2]) * (a[0] + b[0])
        nz += (a[0] - b[0]) * (a[1] + b[1])
    if abs(nz) < 1e-6:
        return sum(v[2] for v in poly) / float(n)
    d = nx * poly[0][0] + ny * poly[0][1] + nz * poly[0][2]
    return (d - nx * x - ny * y) / nz


def sloped_cover(secs, bbox, cell=600.0, step=None):
    """A grid cover whose quads SLOPE, sampled at the cell corners.

    A cover of flat cells cannot represent a ramp: Scenario_01's spawner sits at z=260
    and its route floor at z=30, and flattening each cell makes the ramp between them a
    staircase of ~115uu steps, every one of them beyond the 48uu an agent can climb. So
    the spawner ends up in its own component and a wave can never leave it -- measured,
    and the one thing still cutting the route when every other point was connected.

    Sampling at the CORNERS fixes it by construction: neighbouring cells share two
    corners, so they get identical heights there and their shared edge matches in full
    3D rather than merely in plan.
    """
    step = step if step is not None else navsurfgen.STEP_HEIGHT
    #: height samples per corner, one per floor the corner belongs to
    corners = {}
    xs = []
    x = bbox[0]
    while x < bbox[3] + cell / 2.0:
        xs.append(min(x, bbox[3]))
        x += cell
    ys = []
    y = bbox[1]
    while y < bbox[4] + cell / 2.0:
        ys.append(min(y, bbox[4]))
        y += cell
    for cx in xs:
        for cy in ys:
            zs = sorted(_plane_z(s['verts'], cx, cy)
                        for s in secs if _inside(s['verts'], cx, cy))
            corners[(cx, cy)] = _bands(zs, step)
    out = []
    for i in range(len(xs) - 1):
        for j in range(len(ys) - 1):
            x0, x1 = xs[i], xs[i + 1]
            y0, y1 = ys[j], ys[j + 1]
            at = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            have = [corners.get(c) or [] for c in at]
            if not all(have):
                continue              #: a corner with no floor under it: no quad
            #: pair the corners up by floor, nearest height wins
            for z0 in have[0]:
                quad = [(at[0][0], at[0][1], z0)]
                ok = True
                for c, zs in zip(at[1:], have[1:]):
                    near = min(zs, key=lambda z: abs(z - z0))
                    if abs(near - z0) > step * 4.0:
                        ok = False
                        break
                    quad.append((c[0], c[1], near))
                if ok:
                    out.append(quad)
    return out


#: A cell must be at least this covered by real walkable surface to be emitted. A
#: centre test is not enough: 31 of 181 quads passed it while less than 75% of their
#: area sat over real ground, and those overhangs are surface the graph promises and the
#: level does not have. In game the orcs took them, walked off the bridge and fell into
#: the lava at z = -650.
MIN_CELL_COVERAGE = 0.75

#: How many points to sample per cell when measuring that coverage.
COVERAGE_SAMPLES = 25


def _coverage(secs, x0, y0, x1, y1, z, step):
    """What fraction of a cell's area sits over walkable surface near height `z`."""
    n = int(COVERAGE_SAMPLES ** 0.5)
    hits = tries = 0
    for i in range(n):
        for j in range(n):
            px = x0 + (x1 - x0) * (i + 0.5) / n
            py = y0 + (y1 - y0) * (j + 0.5) / n
            tries += 1
            for s in secs:
                if abs(s['centre'][2] - z) <= max(step, 60.0) and \
                        _inside(s['verts'], px, py):
                    hits += 1
                    break
    return hits / float(tries or 1)


def heightfield_cover(secs, bbox, cell=600.0, step=None):
    """A LAYERED grid cover that follows the real floors.

    For each cell, the heights come from the shipped polygons whose footprint actually
    contains the cell centre, and one quad is emitted per distinct floor. Two earlier
    versions were worse and the numbers say why, on Scenario_01:

    | cover | largest component, by walkable area |
    |---|---|
    | one flat plane at the median height | leaves every upper floor unsupported |
    | each cell at its NEAREST polygon's height | 18% at 600uu, 40% at 400uu |
    | layered, by containment | see below |

    "Nearest polygon centre" is what breaks the middle one: on a multi-storey castle the
    nearest centre jumps between floors, so adjacent cells land more than a step apart
    and their shared edge is correctly refused.
    """
    step = step if step is not None else navsurfgen.STEP_HEIGHT
    out = []
    x = bbox[0]
    while x < bbox[3] - 1.0:
        x2 = min(x + cell, bbox[3])
        y = bbox[1]
        while y < bbox[4] - 1.0:
            y2 = min(y + cell, bbox[4])
            cx, cy = (x + x2) / 2.0, (y + y2) / 2.0
            zs = sorted(s['centre'][2] for s in secs if _inside(s['verts'], cx, cy))
            #: one quad per floor: cluster the heights that are within a step of each
            #: other, since those are the same walkable surface
            for z in _bands(zs, step):
                #: and only where the cell really is floor -- see MIN_CELL_COVERAGE
                if _coverage(secs, x, y, x2, y2, z, step) < MIN_CELL_COVERAGE:
                    continue
                out.append([(x, y, z), (x2, y, z), (x2, y2, z), (x, y2, z)])
            y = y2
        x = x2
    return out


def _bands(zs, step):
    """Representative heights, one per cluster of values within `step` of each other."""
    out = []
    for z in zs:
        if not out or z - out[-1][-1] > step:
            out.append([z])
        else:
            out[-1].append(z)
    return [sum(g) / len(g) for g in out]


def build(map_name, mode, out_dir, cell=600.0):
    work = os.path.join(out_dir, mode)
    if os.path.isdir(work):
        import shutil
        shutil.rmtree(work)
    os.makedirs(work)
    game_path = navpatch.map_path(map_name)
    files = navpatch.fetch_map(game_path, work)
    if mode == 'identity':
        #: The control that should have come FIRST. Every pak so far changed the graph
        #: AND exercised the injector, so a fault in the injector would look exactly
        #: like a fault in the graph -- and three content fixes in a row changed nothing
        #: about the symptom, which is what that confusion looks like.
        buf = navsurf.buffer(map_name)
        with open(files['.umap'], 'rb') as fh:
            ua = fh.read()
        with open(files['.uexp'], 'rb') as fh:
            ux = fh.read()
        out_a, out_x, checks = set_surface_bytes(ua, ux, buf)
        with open(files['.umap'], 'wb') as fh:
            fh.write(out_a)
        with open(files['.uexp'], 'wb') as fh:
            fh.write(out_x)
        same = (out_a == ua and out_x == ux)
        checks = checks + [('the package is byte-identical to the shipped one', same)]
        return {'files': files, 'game_path': game_path, 'checks': checks,
                'polys': 0, 'bytes': len(buf), 'mode': mode,
                'was': len(buf)}
    #: A LADDER, each rung changing one thing more than the last. Built after four
    #: in-game runs that each varied the graph AND exercised the injector together, so a
    #: fault in either looked like a fault in the other.
    if mode in ('mytree', 'regen'):
        ship = navsurf.buffer(map_name)
        heads = navsurf.graphs(ship)
        per_agent = []
        for h in heads:
            secs = navsurf.sections(ship, h)
            polys = [list(s['verts']) for s in secs]
            if mode == 'mytree':
                #: the shipped record bytes, untouched -- only the tree is ours
                per_agent.append((polys, None, None,
                                  ship[h['records_at']:
                                       h['records_at'] + h['records_bytes']]))
            else:
                #: the shipped pointer fields too -- `+40` and the vertex slots -- so
                #: that the only thing left differing is float rounding in the centre
                #: and radius. Both were missing from the first regen and both matter.
                head_ptrs = [struct.unpack_from('<Q', ship, s['at'] + 40)[0]
                             for s in secs]
                nbrs = [list(s['neighbours']) for s in secs]
                per_agent.append((polys, [list(s['edge_ids']) for s in secs],
                                  [int(s['flags'][-2:], 16) for s in secs], None,
                                  head_ptrs, nbrs))
        buf = navsurfgen.buffer_per_agent(per_agent, list(heads[0]['bbox']))
        with open(files['.umap'], 'rb') as fh:
            ua = fh.read()
        with open(files['.uexp'], 'rb') as fh:
            ux = fh.read()
        out_a, out_x, checks = set_surface_bytes(ua, ux, buf)
        with open(files['.umap'], 'wb') as fh:
            fh.write(out_a)
        with open(files['.uexp'], 'wb') as fh:
            fh.write(out_x)
        return {'files': files, 'game_path': game_path, 'checks': checks,
                'polys': sum(len(p[0]) for p in per_agent), 'bytes': len(buf),
                'mode': mode, 'was': len(ship)}
    got = polygons_for(map_name, mode, cell)
    polys, bbox, edge_ids = got[0], got[1], got[2]
    flags = got[3] if len(got) > 3 else None
    ids = edge_ids
    if ids is None:
        #: The AGENT'S OWN step height, not a generous one. A 260uu tolerance was tried
        #: so that Scenario_01's spawner ledge would link down to its route floor, and
        #: in game it linked a great deal else besides: orcs walked off the bridge and
        #: fell into the lava. A cover may not promise a traversal the agent cannot make.
        #: The ledge needs a RAMP in the cover, which a flat-cell approximation of an
        #: existing level cannot build -- and which a converted level supplies directly,
        #: because its ramps are real geometry.
        ids = navsurfgen.allocate_edge_ids(polys)
    buf = navsurfgen.buffer(polys, bbox, edge_ids=ids, flags=flags)
    with open(files['.umap'], 'rb') as fh:
        ua = fh.read()
    with open(files['.uexp'], 'rb') as fh:
        ux = fh.read()
    out_a, out_x, checks = set_surface_bytes(ua, ux, buf)
    with open(files['.umap'], 'wb') as fh:
        fh.write(out_a)
    with open(files['.uexp'], 'wb') as fh:
        fh.write(out_x)
    return {'files': files, 'game_path': game_path, 'checks': checks,
            'polys': len(polys), 'bytes': len(buf), 'mode': mode,
            'was': len(navsurf.buffer(map_name) or b'')}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--map', default='Scenario_01')
    ap.add_argument('--mode',
                    choices=('identity', 'mytree', 'regen', 'grid'), default='regen')
    ap.add_argument('--cell', type=float, default=600.0)
    ap.add_argument('--pak', nargs='?', const='auto')
    ap.add_argument('--install', choices=sorted(PAKS))
    ap.add_argument('--uninstall', action='store_true')
    ap.add_argument('--out', default=OUT)
    args = ap.parse_args(argv)
    from riftlord.core import pakguard
    if args.uninstall:
        for _m, (name, _d) in sorted(PAKS.items()):
            live = os.path.join(MODS, name)
            if os.path.isfile(live):
                pakguard.remove(live)
                print('removed %s' % live)
        print('the shipped navgraph is back')
        return 0
    if args.install:
        src = os.path.join(args.out, 'paks', PAKS[args.install][0])
        if not os.path.isfile(src):
            raise SystemExit('%s is not built -- run with --pak first'
                             % PAKS[args.install][0])
        for mode, (name, _d) in sorted(PAKS.items()):
            live = os.path.join(MODS, name)
            if mode == args.install:
                pakguard.copy(src, live)
                print('installed  %s' % live)
            elif os.path.isfile(live):
                pakguard.remove(live)
                print('removed    %s' % live)
        print()
        print('%s is live. Load Scenario_01 and watch the first wave.' % PAKS[args.install][0])
        print('   %s' % PAKS[args.install][1])
        return 0
    rep = build(args.map, args.mode, args.out, args.cell)
    print('%s %s: %d polygon(s), buffer %d bytes (shipped %d)'
          % (args.map, rep['mode'], rep['polys'], rep['bytes'], rep['was']))
    for label, ok in rep['checks']:
        print('   %-48s %s' % (label, 'ok' if ok else 'FAILED'))
    name = args.pak
    if name == 'auto':
        name = PAKS[args.mode][0]
    if name and all(ok for _l, ok in rep['checks']):
        inner = rep['game_path'].replace('/Game/', 'OMD/Content/')
        dest = os.path.join(args.out, 'paks')
        os.makedirs(dest, exist_ok=True)
        info = pakv4.write(os.path.join(dest, name), {
            inner + '.umap': rep['files']['.umap'],
            inner + '.uexp': rep['files']['.uexp'],
        })
        pakv4.read(info['pak'])
        print('   pak -> %s' % info['pak'])
    return 0 if all(ok for _l, ok in rep['checks']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
