"""WRITE a volumetric (flight) NavPower navgraph from nothing but boxes and adjacency.

This replaces the donor-editing path in ``navvolinject``. That path worked but
inherited a shipped map's shape: it capped a route at seven spans, forced the
topology to be a chain (so the fatal degree word was never rewritten), and could
only ever reshape volumes a shipped level already had. Nothing here is borrowed.

The payload is fully decoded -- a strict sequential reader consumes every byte of
all ten shipped graphs, and re-emitting them reproduces all ten byte for byte::

    container 24 bytes      0, version 2, payloadSize, checksum, 0, 0x01000001
    body:
      +0   0x00020000       constant on all ten
      +4   payloadSize - 12 the size of everything from +12 on
      +8   1                constant
      +12  6                constant
      +16  1                constant
      +20  world bbox, six floats -- EXACTLY the union of the volume boxes
      +44  volume count
      +48  portal count
      then volume records, then portal records, then the kd-tree tail.

    volume record
      0xffffffff, 1.0f, 0       a three-word preamble, identical on all 43 records
      centre 3f, radius 1f      midpoint and half-diagonal of the box
      numPlanes u32 (always 6)
      degree u32                the LENGTH OF THE LIST BELOW, not a cached count
      numPlanes * (nx,ny,nz,d)  with  n . p == d
      degree * (portalIndex u32, faceIndex u32)

    portal record
      volA i32, volB i32, centre 3f, vertexCount u32, vertexCount * 3f

The ``degree`` word is what made every earlier hand-built graph fail. It is an
array length: writing a degree without writing that many incidence pairs desyncs
every record after it, which is why the donor path survived only by never touching
it. ``faceIndex`` indexes into THAT VOLUME'S OWN plane list -- Scenario_Sand_01
orders its faces ``+y +x -y -x +z -z`` where the other nine start at ``-x``, so the
index has to be computed from geometry and can never be assumed.

Measured degrees of freedom, so callers are not boxed in by shipped habits:

* Portal vertex winding is arbitrary. All eight orderings seen across the shipped
  portals are the same quad as a proper cycle, from different start corners and in
  both orientations, so the loader reads a rectangle and not a winding.
* Volumes need not abut exactly. Scenario_Sand_03's overlap by 0.4 to 2.7uu and
  its portals are that overlap rectangle collapsed to zero thickness.
* Topology is free. Scenario_Ice_01 ships a branching graph, not a chain.

What the game validates, so what this must get right: the container checksum (a
wrong one strands every agent using that buffer), and the kd-tree, which answers
"which volume holds this point" and therefore decides whether a flyer can be
placed on the graph at all.
"""

import math
import struct

from riftlord.build.navigation import navvoltree

CONTAINER = 24
FLAGS = 0x01000001
PREAMBLE = (0xffffffff, 1.0, 0)
HEAD = (0x00020000, None, 1, 6, 1)      # [1] is filled in with payloadSize - 12

# Our canonical face order. Any consistent order works -- the shipped maps use two
# different rotations of this same cycle -- but faceIndex must match whatever we write.
FACES = [(0, -1), (1, 1), (0, 1), (1, -1), (2, 1), (2, -1)]


def planes_of(box):
    """The six (nx, ny, nz, d) planes of an axis-aligned box, in FACES order."""
    lo, hi = box
    out = []
    for axis, sign in FACES:
        n = [0.0, 0.0, 0.0]
        n[axis] = float(sign)
        d = (hi[axis] if sign > 0 else -lo[axis]) * 1.0
        out.append((n[0], n[1], n[2], d))
    return out


def centre_of(box):
    lo, hi = box
    return tuple((lo[k] + hi[k]) / 2.0 for k in range(3))


def radius_of(box):
    lo, hi = box
    return math.sqrt(sum(((hi[k] - lo[k]) / 2.0) ** 2 for k in range(3)))


def shared_face(a, b, tolerance=64.0, axis=None):
    """The portal rectangle between boxes `a` and `b`, or None when they do not meet.

    Three things here were measured rather than assumed, and each one dropped real
    portals when I guessed instead:

    * The boxes need not touch. Shipped pairs run from 5uu of interpenetration to a
      10uu gap, so a strict overlap test rejects a third of ScenarioDLC_01's portals
      and most of Scenario_Sand_01's. `tolerance` is how far apart they may sit.
    * The flat axis is the one they share least -- smallest overlap, or largest gap --
      unless `axis` names it, which a generated corridor must do because its equal
      padding makes two axes tie.
    * **The plane lies on `a`'s own face**, never midway between the two. That holds
      on all 75 shipped portals, and `a` means volA, so the argument order matters.
    """
    lo = [max(a[0][k], b[0][k]) for k in range(3)]
    hi = [min(a[1][k], b[1][k]) for k in range(3)]
    #: `axis` forces the portal's plane normal. Inferring it from the thinnest overlap
    #: is right for shipped graphs, which tile, but a generated corridor pads every
    #: axis by the same amount, so two axes tie and the tie-break is arbitrary. A
    #: caller who knows the direction of travel should say so.
    thin = axis if axis is not None else min(range(3), key=lambda k: hi[k] - lo[k])
    if hi[thin] - lo[thin] < -tolerance:
        return None                       # too far apart to be neighbours
    other = [k for k in range(3) if k != thin]
    if any(hi[k] - lo[k] <= 1e-4 for k in other):
        return None                       # degenerate: an edge or a corner, not a face
    # a's face on the thin axis, whichever side b lies on
    centre_b = (b[0][thin] + b[1][thin]) / 2.0
    centre_a = (a[0][thin] + a[1][thin]) / 2.0
    coord = a[1][thin] if centre_b >= centre_a else a[0][thin]
    lo[thin] = hi[thin] = coord
    return thin, lo, hi


def face_index(box, flat_axis, coord):
    """Which of `box`'s faces (in FACES order) the plane `flat_axis == coord` is.

    The portal plane sits on volA's face exactly, but on volB it can be up to the
    gap distance away from either face, so this picks the nearer one.
    """
    lo, hi = box
    sign = 1 if abs(coord - hi[flat_axis]) <= abs(coord - lo[flat_axis]) else -1
    return FACES.index((flat_axis, sign))


def quad(flat_axis, lo, hi):
    """The rectangle's four corners as a proper cycle: LL, HL, HH, LH."""
    o = [k for k in range(3) if k != flat_axis]
    out = []
    for a, b in ((0, 0), (1, 0), (1, 1), (0, 1)):
        p = [0.0, 0.0, 0.0]
        p[flat_axis] = lo[flat_axis]
        p[o[0]] = (hi if a else lo)[o[0]]
        p[o[1]] = (hi if b else lo)[o[1]]
        out.append(tuple(p))
    return out


def _inside(box, p, slack=1.0):
    return all(box[0][k] - slack <= p[k] <= box[1][k] + slack for k in range(3))


def crossing(points, a_box, b_box, step=25.0):
    """(point, direction) where the polyline `points` passes from `a_box` into
    `b_box`, or (None, None): the first sample inside `b_box` whose predecessor is
    inside `a_box`, and the unit direction of the segment it lies on."""
    prev = None
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        d = [b[k] - a[k] for k in range(3)]
        span = math.sqrt(sum(x * x for x in d))
        n = max(int(span // step), 1)
        for j in range(n + 1):
            p = tuple(a[k] + d[k] * j / float(n) for k in range(3))
            if prev is not None and _inside(a_box, prev) and _inside(b_box, p):
                return p, [x / (span or 1.0) for x in d]
            prev = p
    return None, None


def link(boxes, a, b, path):
    """The adjacency entry (a, b, axis, aim) for a portal the route `path` crosses:
    the axis is the route's direction there and the aim is the crossing itself."""
    at, d = crossing(path, boxes[a], boxes[b])
    if at is None:
        ca, cb = centre_of(boxes[a]), centre_of(boxes[b])
        return (a, b, max(range(3), key=lambda k: abs(cb[k] - ca[k])))
    return (a, b, max(range(3), key=lambda k: abs(d[k])), at)


def build(boxes, adjacency):
    """A complete volumetric navgraph buffer.

    `boxes` is a list of ((minx,miny,minz), (maxx,maxy,maxz)); `adjacency` a list of
    (i, j[, axis[, aim]]) entries for volumes that should be joined by a portal. Pairs
    whose boxes do not share a face are dropped, and the pairs actually used are
    available from ``plan`` if the caller needs to know. `aim` is where a flyer steers
    through the portal (the portal record's centre); the vertices span the whole
    shared face either way.
    """
    boxes = [(tuple(float(x) for x in lo), tuple(float(x) for x in hi))
             for lo, hi in boxes]
    if len(boxes) < 2:
        raise ValueError('a volumetric graph needs at least two volumes')

    # ---- portals, and each volume's incidence list -------------------------------
    # Every shipped graph lists its portal pairs strictly ascending with volA < volB,
    # and an unsorted table hung the game.
    ordered = {}
    for entry in adjacency:
        a, b = entry[0], entry[1]
        if a > b:
            entry = (b, a) + tuple(entry[2:])
        ordered.setdefault((entry[0], entry[1]), entry)
    adjacency = [ordered[k] for k in sorted(ordered)]
    portals = []
    inc = [[] for _ in boxes]
    for entry in adjacency:
        a, b = entry[0], entry[1]
        axis = entry[2] if len(entry) > 2 else None
        if a == b or not (0 <= a < len(boxes)) or not (0 <= b < len(boxes)):
            continue
        face = shared_face(boxes[a], boxes[b], axis=axis)
        if face is None:
            continue
        flat, lo, hi = face
        pi = len(portals)
        verts = quad(flat, lo, hi)
        centre = tuple((lo[k] + hi[k]) / 2.0 for k in range(3))
        if len(entry) > 3 and entry[3] is not None:
            shared_lo = [max(boxes[a][0][k], boxes[b][0][k]) for k in range(3)]
            shared_hi = [min(boxes[a][1][k], boxes[b][1][k]) for k in range(3)]
            centre = tuple(min(max(float(entry[3][k]), min(shared_lo[k], shared_hi[k])),
                               max(shared_lo[k], shared_hi[k])) for k in range(3))
        portals.append({
            'a': a, 'b': b,
            'centre': centre,
            'verts': verts,
        })
        inc[a].append((pi, face_index(boxes[a], flat, lo[flat])))
        inc[b].append((pi, face_index(boxes[b], flat, lo[flat])))

    # ---- body --------------------------------------------------------------------
    bbox = tuple([min(b[0][k] for b in boxes) for k in range(3)] +
                 [max(b[1][k] for b in boxes) for k in range(3)])

    body = bytearray()
    body += struct.pack('<I', HEAD[0])
    body += struct.pack('<I', 0)                      # patched below
    body += struct.pack('<3I', HEAD[2], HEAD[3], HEAD[4])
    body += struct.pack('<6f', *bbox)
    body += struct.pack('<2I', len(boxes), len(portals))

    for i, box in enumerate(boxes):
        body += struct.pack('<I', PREAMBLE[0])
        body += struct.pack('<f', PREAMBLE[1])
        body += struct.pack('<I', PREAMBLE[2])
        body += struct.pack('<3f', *centre_of(box))
        body += struct.pack('<f', radius_of(box))
        pl = planes_of(box)
        body += struct.pack('<2I', len(pl), len(inc[i]))
        for p in pl:
            body += struct.pack('<4f', *p)
        for pi, fi in inc[i]:
            body += struct.pack('<2I', pi, fi)

    for p in portals:
        body += struct.pack('<2i', p['a'], p['b'])
        body += struct.pack('<3f', *p['centre'])
        body += struct.pack('<I', len(p['verts']))
        for v in p['verts']:
            body += struct.pack('<3f', *v)

    body += navvoltree.emit(navvoltree.build(boxes), bbox)

    struct.pack_into('<I', body, 4, len(body) - 12)
    n = len(body) // 4
    chk = sum(struct.unpack_from('<%dI' % n, bytes(body), 0)) & 0xFFFFFFFF
    buf = struct.pack('<6I', 0, 2, len(body), chk, 0, FLAGS) + bytes(body)
    return buf


def plan(boxes, adjacency):
    """The (i, j) pairs `build` would actually portal, in portal-index order."""
    out = []
    for entry in adjacency:
        a, b = entry[0], entry[1]
        axis = entry[2] if len(entry) > 2 else None
        if a == b or not (0 <= a < len(boxes)) or not (0 <= b < len(boxes)):
            continue
        if shared_face(tuple(map(tuple, boxes[a])), tuple(map(tuple, boxes[b])),
                       axis=axis):
            out.append((a, b))
    return out


def corridor(points, half_xy, half_z, overlap=0.0):
    """Boxes and adjacency for a chain of waypoints -- one box per span, each span's
    box grown by `half_xy` / `half_z` about the segment, consecutive spans joined.

    `overlap` extends each box along its own long axis so neighbours interpenetrate
    rather than merely touch, which is what Scenario_Sand_03 ships and what keeps a
    portal from landing exactly on a shared plane.
    """
    boxes = []
    for p, q in zip(points, points[1:]):
        lo = [min(p[k], q[k]) for k in range(3)]
        hi = [max(p[k], q[k]) for k in range(3)]
        long_axis = max(range(3), key=lambda k: hi[k] - lo[k])
        for k in range(3):
            pad = half_z if k == 2 else half_xy
            lo[k] -= pad
            hi[k] += pad
        lo[long_axis] -= overlap
        hi[long_axis] += overlap
        boxes.append((tuple(lo), tuple(hi)))
    adj = [(i, i + 1) for i in range(len(boxes) - 1)]
    return boxes, adj


# The corridor half-widths the in-game work settled on. Narrower than this and
# hellbats that broke off a fight could not get back on the graph; these are the
# values the working build shipped with.
#: How a generated graph is fitted to the level's free space (with `clear`): 'airspace' tiles
#: the open air around the routes with a few thick boxes, as shipped graphs are built
#: (navvolclear.airspace); 'corridor' fits thin boxes along each route leg (the old way,
#: whose gaps and thin spots turned flyers back on Cellar and Beset).
FLIGHT_FIT = 'airspace'
PAD_XY = 1200.0
PAD_Z = 700.0
#: The least a volume keeps around its own stretch of route (uu) when it is trimmed
#: away from a volume it is not joined to: about the margin shipped flight volumes
#: leave around their route points.
MIN_PAD_XY = 400.0
MIN_PAD_Z = 300.0


def route_boxes(points, nvol=None, anchors=(), pad_xy=PAD_XY, pad_z=PAD_Z):
    """Boxes and adjacency for a flight route, in travel order.

    The polyline is cut into `nvol` equal-arc-length spans and each span gets one
    box, padded by `pad_xy` / `pad_z`. Consecutive boxes therefore OVERLAP rather
    than tile. That is deliberate and was measured: trimming them to abut made both
    wisps and hellbats double back and stick, where the overlapping shape flew the
    route cleanly, so the better-behaving geometry is the default.

    Every authored point that falls inside a span is included before padding, so a
    corner is never cut off, and `anchors` (a spawner, a door) go in the first box so
    a flyer is on the graph the moment it appears.

    Unlike the donor path this replaces, `nvol` is free: there is no seven-span cap
    and no shipped map's shape to inherit.
    """
    points = [tuple(float(x) for x in p) for p in points]
    nseg = len(points) - 1
    if nseg < 1:
        raise ValueError('a route needs at least two points')
    nvol = nvol or nseg
    if nvol < 2:
        nvol = 2

    seglen = [math.sqrt(sum((points[i + 1][k] - points[i][k]) ** 2
                            for k in range(3))) for i in range(nseg)]
    total = sum(seglen) or 1.0

    def at_dist(dist):
        for i, L in enumerate(seglen):
            if dist <= L or i == nseg - 1:
                f = min(max((dist / L) if L else 0.0, 0.0), 1.0)
                return tuple(points[i][k] + (points[i + 1][k] - points[i][k]) * f
                             for k in range(3))
            dist -= L
        return points[-1]

    boxes = []
    for i in range(nvol):
        d0, d1 = total * i / nvol, total * (i + 1) / nvol
        held = [at_dist(d0), at_dist(d1)]
        run = 0.0
        for j in range(nseg + 1):
            if d0 - 1 <= run <= d1 + 1:
                held.append(points[j])
            if j < nseg:
                run += seglen[j]
        if i == 0:
            held += [tuple(float(x) for x in a) for a in anchors]
        pad = (pad_xy, pad_xy, pad_z)
        boxes.append((tuple(min(p[k] for p in held) - pad[k] for k in range(3)),
                      tuple(max(p[k] for p in held) + pad[k] for k in range(3))))

    # Each junction's portal is cut athwart the direction of travel where the route
    # crosses into the next box, and aims at that crossing.
    adj = [link(boxes, i, i + 1, points) for i in range(nvol - 1)]
    return boxes, adj


def for_route(points, anchors=(), nvol=None, pad_xy=PAD_XY, pad_z=PAD_Z):
    """A complete flight navgraph for one route. The whole buffer, from nothing."""
    boxes, adj = route_boxes(points, nvol=nvol, anchors=anchors,
                             pad_xy=pad_xy, pad_z=pad_z)
    return build(boxes, adj)


# ---------------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------------

def read(buf):
    """Strict sequential decode -- the reader that proves the format is complete.

    Every byte is consumed in order, with no searching and no signature matching, so
    reaching the end IS the proof that nothing is unaccounted for. Run over the ten
    shipped graphs it consumes all ten exactly, leaving only the kd-tree tail, whose
    length is independently `44 + (volumes - 1) * 16`.
    """
    body = buf[CONTAINER:]
    o = [0]

    def take(fmt):
        v = struct.unpack_from('<' + fmt, body, o[0])
        o[0] += struct.calcsize('<' + fmt)
        return v

    g = {'head': take('5I'), 'bbox': take('6f')}
    nv, ne = take('2I')
    g['nvols'], g['nedges'] = nv, ne
    g['volumes'] = []
    for _ in range(nv):
        pre = take('I') + take('f') + take('I')
        centre = take('3f')
        radius = take('f')[0]
        np_, deg = take('2I')
        planes = [take('4f') for _ in range(np_)]
        g['volumes'].append({'pre': pre, 'centre': centre, 'radius': radius,
                             'planes': planes, 'deg': deg,
                             'inc': [take('2I') for _ in range(deg)]})
    g['portals'] = []
    for _ in range(ne):
        a, b = take('2i')
        centre = take('3f')
        vc = take('I')[0]
        g['portals'].append({'a': a, 'b': b, 'centre': centre,
                             'verts': [take('3f') for _ in range(vc)]})
    g['tree_at'] = o[0]
    g['tail'] = len(body) - o[0]
    return g


def check(buf, boxes=None):
    """Invariants a generated buffer must satisfy. Returns a list of (name, ok)."""
    out = []
    try:
        g = read(buf)
    except Exception as exc:
        return [('the buffer decodes sequentially', False), (str(exc), False)]
    out.append(('the buffer decodes sequentially', True))

    w = struct.unpack_from('<6I', buf, 0)
    body = buf[CONTAINER:]
    n = len(body) // 4
    out.append(('container payload size', w[2] + CONTAINER == len(buf)))
    out.append(('container flags', w[5] == FLAGS))
    out.append(('container checksum',
                w[3] == (sum(struct.unpack_from('<%dI' % n, body, 0)) & 0xFFFFFFFF)))
    out.append(('head[1] == payload - 12', g['head'][1] == len(body) - 12))
    out.append(('tail is exactly the kd-tree',
                g['tail'] == navvoltree.HDR + (g['nvols'] - 1) * 16))

    # bbox is the union of the volume boxes
    bb = []
    for v in g['volumes']:
        lo, hi = [None] * 3, [None] * 3
        for nx, ny, nz, d in v['planes']:
            nrm = (nx, ny, nz)
            ax = max(range(3), key=lambda k: abs(nrm[k]))
            val = d / nrm[ax]
            if nrm[ax] > 0:
                hi[ax] = val
            else:
                lo[ax] = val
        bb.append((lo, hi))
    union = [min(b[0][k] for b in bb) for k in range(3)] + \
            [max(b[1][k] for b in bb) for k in range(3)]
    out.append(('bbox is the union of the volumes',
                all(abs(union[k] - g['bbox'][k]) < 0.01 for k in range(6))))

    # centre and radius are redundant with the planes, so they can be checked
    cr = sum(1 for v, b in zip(g['volumes'], bb)
             if all(abs((b[0][k] + b[1][k]) / 2.0 - v['centre'][k]) < 0.01
                    for k in range(3))
             and abs(math.sqrt(sum(((b[1][k] - b[0][k]) / 2.0) ** 2
                                   for k in range(3))) - v['radius']) < 0.01)
    out.append(('every centre and radius matches its planes (%d/%d)'
                % (cr, g['nvols']), cr == g['nvols']))

    # the incidence lists and the portals must agree in both directions
    bad = 0
    seen = {}
    for vi, v in enumerate(g['volumes']):
        if v['deg'] != len(v['inc']):
            bad += 1
        for pidx, flag in v['inc']:
            if not (0 <= pidx < g['nedges']) or flag >= len(v['planes']):
                bad += 1
                continue
            p = g['portals'][pidx]
            if vi not in (p['a'], p['b']):
                bad += 1
            seen.setdefault(pidx, []).append(vi)
    out.append(('degree equals the incidence-list length, and every entry '
                'names the volume it belongs to', bad == 0))
    both = sum(1 for pidx, p in enumerate(g['portals'])
               if sorted(seen.get(pidx, [])) == sorted([p['a'], p['b']]))
    out.append(('every portal is named by exactly its two volumes (%d/%d)'
                % (both, g['nedges']), both == g['nedges']))
    pairs = [(p['a'], p['b']) for p in g['portals']]
    out.append(('portal pairs strictly ascending with volA < volB',
                all(a < b for a, b in pairs)
                and all(x < y for x, y in zip(pairs, pairs[1:]))))

    # the flag must index a plane of that volume facing that portal
    axok = 0
    axtot = 0
    for vi, v in enumerate(g['volumes']):
        for pidx, flag in v['inc']:
            if not (0 <= pidx < g['nedges']) or flag >= len(v['planes']):
                continue
            vs = g['portals'][pidx]['verts']
            flat = [k for k in range(3)
                    if max(x[k] for x in vs) - min(x[k] for x in vs) < 0.01]
            nrm = v['planes'][flag][:3]
            ax = max(range(3), key=lambda k: abs(nrm[k]))
            axtot += 1
            if flat and ax == flat[0]:
                axok += 1
    out.append(('every faceIndex names a plane athwart its portal (%d/%d)'
                % (axok, axtot), axok == axtot))

    tree = navvoltree.check(buf[CONTAINER + g['tree_at']:], g['nvols'], boxes)
    out.extend(tree)
    return out


def main():
    import argparse
    import os
    import tempfile

    ap = argparse.ArgumentParser(description='Generate and verify flight navgraphs.')
    ap.add_argument('--selftest', action='store_true',
                    help='round-trip the shipped graphs and regenerate them')
    ap.add_argument('--route', metavar='N', type=int,
                    help='generate a graph for an N-point synthetic route')
    a = ap.parse_args()

    if a.route:
        pts = [(i * 1500.0, (i % 3) * 900.0, 400.0 + (i % 2) * 250.0)
               for i in range(a.route)]
        buf = for_route(pts)
        g = read(buf)
        print('%d-point route -> %d bytes, %d volumes, %d portals'
              % (a.route, len(buf), g['nvols'], g['nedges']))
        boxes, _adj = route_boxes(pts)
        for nm, ok in check(buf, boxes):
            print('  %-4s %s' % ('ok' if ok else 'FAIL', nm))
        return

    if a.selftest:
        from riftlord.build.navigation import navpatch
        from riftlord.build.navigation import navvol
        tmp = tempfile.mkdtemp(prefix='navvolgen')
        names = sorted(navpatch.maps(include_mods=False))
        print('%-18s %7s %7s %8s %9s %9s' % ('level', 'bytes', 'rebuilt',
                                             'identical', 'regen', 'checks'))
        worst = []
        for name in names:
            d = navvol.for_map(name, tmp)
            if not d:
                continue
            files = navpatch.fetch_map(navpatch.map_path(name), tmp)
            nb = navpatch.nav_buffers(files['.umap'], files['.uexp'])
            buf = open(files['.uexp'], 'rb').read()[nb[3]:nb[3] + nb[4]]
            g = read(buf)

            # 1. re-emit the shipped payload verbatim: the decode gate
            re = bytearray()
            for x in g['head']:
                re += struct.pack('<I', x)
            re += struct.pack('<6f', *g['bbox'])
            re += struct.pack('<2I', g['nvols'], g['nedges'])
            for v in g['volumes']:
                re += struct.pack('<I', v['pre'][0]) + struct.pack('<f', v['pre'][1])
                re += struct.pack('<I', v['pre'][2])
                re += struct.pack('<3f', *v['centre']) + struct.pack('<f', v['radius'])
                re += struct.pack('<2I', len(v['planes']), v['deg'])
                for p in v['planes']:
                    re += struct.pack('<4f', *p)
                for pi, fi in v['inc']:
                    re += struct.pack('<2I', pi, fi)
            for p in g['portals']:
                re += struct.pack('<2i', p['a'], p['b'])
                re += struct.pack('<3f', *p['centre'])
                re += struct.pack('<I', len(p['verts']))
                for v in p['verts']:
                    re += struct.pack('<3f', *v)
            re += buf[CONTAINER + g['tree_at']:]
            nw = len(re) // 4
            chk = sum(struct.unpack_from('<%dI' % nw, bytes(re), 0)) & 0xFFFFFFFF
            rebuilt = struct.pack('<6I', 0, 2, len(re), chk, 0, FLAGS) + bytes(re)

            # 2. regenerate from geometry alone: the emitter gate
            boxes = []
            for v in g['volumes']:
                lo, hi = [None] * 3, [None] * 3
                for nx, ny, nz, dd in v['planes']:
                    nrm = (nx, ny, nz)
                    ax = max(range(3), key=lambda k: abs(nrm[k]))
                    val = dd / nrm[ax]
                    if nrm[ax] > 0:
                        hi[ax] = val
                    else:
                        lo[ax] = val
                boxes.append((tuple(lo), tuple(hi)))
            gen = build(boxes, [(p['a'], p['b']) for p in g['portals']])
            checks = check(gen, boxes)
            bad = [nm for nm, ok in checks if not ok]
            if bad:
                worst.append((name, bad))
            print('%-18s %7d %7d %8s %9s %9s'
                  % (name, len(buf), len(rebuilt),
                     'YES' if rebuilt == buf else 'no',
                     'same size' if len(gen) == len(buf) else 'SIZE %d' % len(gen),
                     'ok' if not bad else 'FAIL'))
        for name, bad in worst:
            print('  %s: %s' % (name, '; '.join(bad)))
        return

    ap.print_help()


if __name__ == '__main__':
    main()


def boxes_of(buf):
    """(boxes, adjacency) of an existing buffer -- what `build` needs to remake it.

    Decoding a shipped graph back to boxes and feeding it through `build` reproduces
    it at the same byte length with every invariant passing, so this is how an
    authored route is ADDED to a map that already has flight rather than replacing it.
    """
    g = read(buf)
    boxes = []
    for v in g['volumes']:
        lo, hi = [None] * 3, [None] * 3
        for nx, ny, nz, d in v['planes']:
            nrm = (nx, ny, nz)
            ax = max(range(3), key=lambda k: abs(nrm[k]))
            val = d / nrm[ax]
            if nrm[ax] > 0:
                hi[ax] = val
            else:
                lo[ax] = val
        if None in lo or None in hi:
            raise ValueError('volume %d is not an axis-aligned box' % len(boxes))
        boxes.append((tuple(lo), tuple(hi)))
    return boxes, [(p['a'], p['b']) for p in g['portals']]


def overlaps(a, b):
    """Do two boxes share volume? The kd-tree is a PARTITION, so they should not."""
    return all(min(a[1][k], b[1][k]) - max(a[0][k], b[0][k]) > 1.0 for k in range(3))


def same_box(a, b, tol=0.5):
    return all(abs(a[0][k] - b[0][k]) <= tol and abs(a[1][k] - b[1][k]) <= tol
               for k in range(3))


def _nodes_of(routes, tol=1.0):
    """Route point sequences as shared node ids: the same waypoint in two routes
    becomes the SAME node, which is what lets converging routes share volumes."""
    pos = []
    seqs = []
    for pts, _anchors in routes:
        seq = []
        for p in pts:
            p = tuple(float(x) for x in p)
            hit = next((i for i, q in enumerate(pos)
                        if all(abs(q[k] - p[k]) <= tol for k in range(3))), None)
            if hit is None:
                hit = len(pos)
                pos.append(p)
            seq.append(hit)
        seqs.append(seq)
    return pos, seqs


def legs_of(routes, tol=1.0):
    """Routes split into unique LEGS at their junction waypoints.

    Two spawners whose routes converge -- which they always do when both end at the
    level's exit point -- must share the volumes along the common part. Generating a
    corridor per route instead produces two heavily overlapping parallel corridors
    (measured: 22 overlapping pairs for one pair of converging routes), and because
    the kd-tree is a PARTITION a flyer in the shared region is resolved into exactly
    one of them -- possibly the other spawner's, whose volume order runs the other
    way. Nothing offline flags it and nothing crashes; the flyer just goes wrong.

    A node is a junction when it does not have exactly one edge in and one edge out
    across all routes, which is precisely where routes meet, split, start or end.
    Each maximal run between junctions is a leg, and identical legs collapse to one.

    Returns (legs, joins, anchors_for) where `legs` is a list of point lists, `joins`
    is [(leg a, leg b)] where a's end is b's start, and `anchors_for` maps a leg index
    to the anchors that belong in its first volume.

    A single route with no sharing has no interior junction, so it comes back as ONE
    leg and its geometry is **identical** to generating it directly -- which matters,
    because that is the shape confirmed working in game.
    """
    pos, seqs = _nodes_of(routes, tol=tol)
    outs, ins = {}, {}
    for seq in seqs:
        for a, b in zip(seq, seq[1:]):
            outs.setdefault(a, set()).add(b)
            ins.setdefault(b, set()).add(a)
    def junction(n):
        return len(outs.get(n, ())) != 1 or len(ins.get(n, ())) != 1

    legs, joins, anchors_for = [], set(), {}
    index = {}
    for seq, (_pts, anchors) in zip(seqs, routes):
        cut = [0] + [i for i in range(1, len(seq) - 1) if junction(seq[i])] \
              + [len(seq) - 1]
        cut = sorted(set(cut))
        chain = []
        for a, b in zip(cut, cut[1:]):
            key = tuple(seq[a:b + 1])
            if key not in index:
                index[key] = len(legs)
                legs.append([pos[n] for n in key])
            chain.append(index[key])
        for x, y in zip(chain, chain[1:]):
            joins.add((x, y))
        if chain and anchors:
            anchors_for.setdefault(chain[0], [])
            for a in anchors:
                a = tuple(float(x) for x in a)
                if a not in anchors_for[chain[0]]:
                    anchors_for[chain[0]].append(a)
    return legs, sorted(joins), anchors_for


def for_routes(routes, existing=None, pad_xy=PAD_XY, pad_z=PAD_Z, nvol=None,
               share=True, clear=None):
    """One graph covering every route, keeping `existing`'s own volumes if given.

    `routes` is [(points, anchors)] -- one entry per flyer spawner. There is exactly
    ONE volumetric buffer per level, so every route goes in the same graph;
    installing one per spawner kept only the first.

    Routes that **share waypoints share volumes** (see `legs_of`): converging routes
    become a graph with a merge, not two overlapping corridors. Pass `share=False`
    for the old per-route behaviour, which is only useful for comparison.

    `existing` is the level's own graph, preserved so that adding a route to a map
    that already ships flight does not delete the routes it came with. A volume in
    `existing` that this call would generate anyway is REGENERATED, not kept, so
    saving repeatedly is idempotent whichever package it is handed.

    `clear` is (level name, Rift Lord plan) or a `navvolclear.Grid`: volumes are then
    fitted to the level's free space (navvolclear) instead of padded boxes.

    Returns (buffer, info).
    """
    kw = {'pad_xy': pad_xy, 'pad_z': pad_z}
    pads = (pad_xy, pad_xy, pad_z)
    least = (MIN_PAD_XY, MIN_PAD_XY, MIN_PAD_Z)
    fitter = grid = None
    if clear is not None:
        from riftlord.build.navigation import navvolclear
        grid = clear if isinstance(clear, navvolclear.Grid) else \
            navvolclear.Grid.for_plan(clear[0], clear[1],
                                      navvolclear.region_of(routes, pad_xy, pad_z))
        fitter = navvolclear.Fitter(grid, pad_xy, pad_z)
        if FLIGHT_FIT == 'airspace':
            boxes, adjacency, blocked, gaps = navvolclear.airspace(grid, routes)
            buf, info = _assemble(boxes, adjacency, [], existing)
            info['blocked'] = blocked
            info['gaps'] = gaps
            info['geometry'] = navvolclear.check_clear(grid, boxes)
            info['missing_meshes'] = sorted(set(grid.missing))
            return buf, info
    authored, links, cores = _authored(routes, nvol, share, kw, fitter=fitter)
    if cores is None:
        cores = [(tuple(lo[k] + max(0.0, pads[k] - least[k]) for k in range(3)),
                  tuple(hi[k] - max(0.0, pads[k] - least[k]) for k in range(3)))
                 for lo, hi in authored]
    authored, tangled = separate(authored, [(a, b) for a, b, _p in links], cores)
    adj_a = [link(authored, a, b, path) for a, b, path in links]
    # Volumes whose own stretches of route lie too close to part are joined instead,
    # so a flyer placed in either has a direct way into the other, aimed at the middle
    # of the airspace both routes keep.
    for a, b in tangled:
        ca = centre_of((tuple(max(cores[a][0][k], cores[b][0][k]) for k in range(3)),
                        tuple(min(cores[a][1][k], cores[b][1][k]) for k in range(3))))
        d = [centre_of(authored[b])[k] - centre_of(authored[a])[k] for k in range(3)]
        adj_a.append((a, b, max(range(3), key=lambda k: abs(d[k])), ca))
    buf, info = _assemble(authored, adj_a, tangled, existing)
    if grid is not None:
        info['blocked'] = fitter.blocked
        info['geometry'] = navvolclear.check_clear(grid, authored)
        info['missing_meshes'] = sorted(set(grid.missing))
    return buf, info


def _authored(routes, nvol, share, kw, split=1, fitter=None):
    """(boxes, links, cores) for `routes`, each leg cut into `split` times its usual
    span count, or fitted to free space by `fitter`. A link is (box a, box b, route
    through both); `cores` is None unless fitted."""
    authored, links = [], []
    cores = [] if fitter is not None else None

    def boxes_for(points, anchors, count):
        if fitter is None:
            return route_boxes(points, nvol=count, anchors=anchors, **kw)
        b, c = fitter.leg(points, anchors)
        cores.extend(c)
        return b, [(j, j + 1) for j in range(len(b) - 1)]

    if share:
        legs, joins, anchors_for = legs_of(routes)
        spans = []
        for i, leg in enumerate(legs):
            b, a = boxes_for(leg, anchors_for.get(i, ()), (nvol or len(leg) - 1) * split)
            spans.append((len(authored), len(b)))
            links += [(e[0] + len(authored), e[1] + len(authored), leg) for e in a]
            authored += b
        # Every pair of legs meeting at a node is joined there: a leg into the next,
        # and siblings that fork from or merge into the same node.
        ends = {}
        for i, leg in enumerate(legs):
            ends.setdefault(_key(leg[0]), []).append((i, 'start'))
            ends.setdefault(_key(leg[-1]), []).append((i, 'end'))
        joined = set()
        for touching in ends.values():
            for u in range(len(touching)):
                for v in range(u + 1, len(touching)):
                    (x, sx), (y, sy) = touching[u], touching[v]
                    if x == y:
                        continue
                    if sx == 'start' and sy == 'end':
                        (x, sx), (y, sy) = (y, sy), (x, sx)
                    bx = spans[x][0] + (spans[x][1] - 1 if sx == 'end' else 0)
                    by = spans[y][0] + (spans[y][1] - 1 if sy == 'end' else 0)
                    if bx == by or (min(bx, by), max(bx, by)) in joined:
                        continue
                    joined.add((min(bx, by), max(bx, by)))
                    into = legs[x] if sx == 'end' else legs[x][::-1]
                    out = legs[y] if sy == 'start' else legs[y][::-1]
                    links.append((bx, by, list(into) + list(out[1:])))
    else:
        for pts, anchors in routes:
            b, a = boxes_for(pts, anchors, (nvol or len(pts) - 1) * split)
            links += [(e[0] + len(authored), e[1] + len(authored), pts) for e in a]
            authored += b
    return authored, links, cores


def _assemble(authored, adj_a, tangled, existing):
    """The buffer and its report: `existing`'s volumes kept, then the authored ones."""
    boxes, adj, kept, regenerated = [], [], 0, 0
    if existing:
        kb, ka = boxes_of(existing)
        keep = [i for i, b in enumerate(kb)
                if not any(same_box(b, ab) for ab in authored)]
        regenerated = len(kb) - len(keep)
        remap = {old: new for new, old in enumerate(keep)}
        boxes = [kb[i] for i in keep]
        adj = [(remap[a], remap[b]) for a, b in ka if a in remap and b in remap]
        kept = len(boxes)

    off = len(boxes)
    boxes += authored
    adj += [(e[0] + off, e[1] + off) + tuple(e[2:]) for e in adj_a]

    bad = []
    for i in range(kept):
        for j in range(kept, len(boxes)):
            if overlaps(boxes[i], boxes[j]):
                bad.append((i, j))
    buf = build(boxes, adj)
    info = {'volumes': len(boxes), 'kept': kept, 'added': [len(authored)],
            'regenerated': regenerated, 'portals': read(buf)['nedges'],
            'overlapping': bad, 'tangled': [(a + off, b + off) for a, b in tangled],
            'checks': check(buf, boxes)}
    return buf, info


def _key(p, grid=1.0):
    return tuple(round(float(x) / grid) for x in p)


def separate(boxes, joined, cores):
    """(boxes, tangled): `boxes` trimmed so no two volumes that are not joined by a
    portal share airspace, each keeping at least its `cores` box.

    The kd-tree resolves a point to ONE volume, so a point in two unjoined volumes can
    be placed on a later stretch of the route. Each overlapping unjoined pair is split
    on the axis that removes the least volume; `tangled` lists pairs whose cores
    themselves overlap, which no trim can part.
    """
    boxes = [(list(lo), list(hi)) for lo, hi in boxes]
    pairs = {(min(a, b), max(a, b)) for a, b in joined}
    tangled = []

    def vol(lo, hi):
        return max(0.0, hi[0] - lo[0]) * max(0.0, hi[1] - lo[1]) * max(0.0, hi[2] - lo[2])

    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if (i, j) in pairs or not overlaps(boxes[i], boxes[j]):
                continue
            best = None
            for k in range(3):
                for low, high in ((i, j), (j, i)):
                    # `low` keeps the side below the cut, `high` the side above
                    floor, ceil = cores[low][1][k], cores[high][0][k]
                    if floor > ceil:
                        continue
                    mid = (boxes[high][0][k] + boxes[low][1][k]) / 2.0
                    cut = min(max(mid, floor), ceil)
                    lo_l, hi_l = list(boxes[low][0]), list(boxes[low][1])
                    lo_h, hi_h = list(boxes[high][0]), list(boxes[high][1])
                    hi_l[k] = min(hi_l[k], cut)
                    lo_h[k] = max(lo_h[k], cut)
                    cost = (vol(*boxes[low]) - vol(lo_l, hi_l)
                            + vol(*boxes[high]) - vol(lo_h, hi_h))
                    if best is None or cost < best[0]:
                        best = (cost, low, high, (lo_l, hi_l), (lo_h, hi_h))
            if best is None:
                tangled.append((i, j))
                continue
            _c, low, high, bl, bh = best
            boxes[low], boxes[high] = bl, bh
    return [(tuple(lo), tuple(hi)) for lo, hi in boxes], tangled
