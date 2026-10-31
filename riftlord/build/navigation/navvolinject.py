"""Give a level a volumetric (flight) navgraph it does not ship.

This is the last gap between "flight data is authorable" and "flyers work on a map we
made". Measured in game: the volumes must approximately cover the route and they control
flight height, while the portals and the trailing index do not appear to gate flight --
so a donor's buffer, with its volumes repositioned, is enough.

The obstacle is packaging, not format. On a level with no flight volumes the cooked
export has **no NavVolumesBufferSize property at all** (UE omits default values), and
the nav export is not the last one in the .uexp -- 37 to 48KB of exports follow it. So
injecting means adding a name, inserting a property tag, appending the buffer, and
recomputing every later offset. ``uassetio.Package`` exists for exactly that.

**Offline verification cannot prove this safe.** A stale summary offset crashes the game
on start and parses perfectly beforehand (see omd3-package-header-offsets), so the
checks here are necessary and not sufficient: the patched package is re-read from
scratch, the new buffer must be found by the ordinary reader, its checksum must verify,
and pkgcheck must pass.

    python3 navvolinject.py --target Scenario_01 --donor Scenario_Sand_05 --pak
"""

import argparse
import os
import struct
import sys
import tempfile
from riftlord.core import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.build.navigation import navpatch  # noqa: E402
from riftlord.build.navigation import navvol  # noqa: E402
from riftlord.build.navigation import navvolgen
from riftlord.build.navigation import navvoltree  # noqa: E402
from riftlord.formats.ue import pakv4  # noqa: E402
from riftlord.build import volwrite  # noqa: E402
from riftlord.formats.ue.uassetio import Package  # noqa: E402

OUT = gamepath.beside_code(gamepath.CODE_ROOT, 'patched/volinject')

#: Basename fragments this tool's paks have historically used. Kept only as a
#: fallback: matching on the NAME is fragile, and it failed the moment a pak was
#: installed as `970-FitDeg_P.pak`, which matches neither marker -- so the tool read
#: back its own output and reported the target "already has a volumetric buffer".
OURS = ('Flight', 'VolInject', 'Fit', 'Vol')

#: What this tool has actually installed. A recorded fact beats a guess at a name.
MANIFEST = os.path.join(OUT, 'installed.json')


def _manifest():
    try:
        with open(MANIFEST) as fh:
            import json                              # noqa: PLC0415
            got = json.load(fh)
        return [str(x) for x in got] if isinstance(got, list) else []
    except Exception:                                # noqa: BLE001
        return []


def _remember(name):
    import json                                      # noqa: PLC0415
    names = sorted(set(_manifest()) | {name})
    os.makedirs(os.path.dirname(MANIFEST), exist_ok=True)
    with open(MANIFEST, 'w') as fh:
        json.dump(names, fh, indent=1)


def our_paks():
    """Installed paks this tool produced: recorded ones first, name markers second."""
    import glob                                      # noqa: PLC0415
    recorded = set(_manifest())
    out = []
    for p in glob.glob(os.path.join(navpatch.PAK_DIR, '~mods', '*.pak')):
        base = os.path.basename(p)
        if base in recorded or any(m in base for m in OURS):
            out.append(base)
    return tuple(out)


ABUT = False          # trim volumes to tile; see fit_to_route
#: How much airspace a route gets around it. Generous on purpose: a flyer that
#: breaks off to fight, or drifts while turning, has to stay INSIDE its volume or
#: NavPower hauls it back instead of letting it act. Shipped flight volumes are whole
#: rooms; a tight corridor is not what this system expects.
PAD_XY = 1200.0
PAD_Z = 700.0


def set_box(out, vol, lo, hi, degree):
    """Rewrite one volume record in place to be the box (lo, hi) with `degree`.

    A plane ``n . p == d`` with a unit axis normal has ``d = n[ax] * bound``, taking
    the high bound for a positive normal and the low one for a negative. The stored
    centre and bounding radius are redundant with the planes, so both are recomputed
    -- leaving them stale would produce a record that contradicts itself, which is
    exactly what ``navvol``'s checks look for.
    """
    at = navvol.CONTAINER + vol['at']
    for j, pl in enumerate(vol['planes']):
        nrm = pl[:3]
        ax = max(range(3), key=lambda k: abs(nrm[k]))
        bound = hi[ax] if nrm[ax] > 0 else lo[ax]
        struct.pack_into('<f', out, at + 8 + j * 16 + 12, nrm[ax] * bound)
    centre = [(lo[k] + hi[k]) / 2.0 for k in range(3)]
    half = [(hi[k] - lo[k]) / 2.0 for k in range(3)]
    struct.pack_into('<3f', out, at - 16, *centre)
    struct.pack_into('<f', out, at - 4,
                     sum(h * h for h in half) ** 0.5)
    struct.pack_into('<I', out, at + 4, degree)


def _route_crossing(points, a_box, b_box, step=25.0):
    """Where the route leaves `a_box` and is inside `b_box`, or None.

    Walks the polyline finely and returns the first sample that is inside the second
    box while the previous one was inside the first. That is the place a flyer
    actually passes between the two, and therefore where its doorway belongs.
    """
    def inside(box, p):
        return all(box[0][k] - 1 <= p[k] <= box[1][k] + 1 for k in range(3))

    prev = None
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        d = [b[k] - a[k] for k in range(3)]
        span = sum(x * x for x in d) ** 0.5
        n = max(int(span // step), 1)
        for j in range(n + 1):
            f = j / float(n)
            p = tuple(a[k] + d[k] * f for k in range(3))
            if prev is not None and inside(a_box, prev) and inside(b_box, p):
                return p
            prev = p
    return None


def _route_direction(points, a_box, b_box, step=25.0):
    """The route's direction where it crosses from one box into the other."""
    def inside(box, p):
        return all(box[0][k] - 1 <= p[k] <= box[1][k] + 1 for k in range(3))

    prev = None
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        d = [b[k] - a[k] for k in range(3)]
        span = sum(x * x for x in d) ** 0.5
        n = max(int(span // step), 1)
        for j in range(n + 1):
            f = j / float(n)
            p = tuple(a[k] + d[k] * f for k in range(3))
            if prev is not None and inside(a_box, prev) and inside(b_box, p):
                L = span or 1.0
                return [x / L for x in d]
            prev = p
    return None


def set_portal(out, po, centre, flat_axis, half, joins=None, write_centre=True,
               half2=None, quad_centre=None):
    """Rewrite one portal: the pair of volumes it joins, its centre, its vertices.

    The record is [volA i32, volB i32, centre.xyz floats, vertexCount i32, verts],
    and **the adjacency of the whole graph lives in these index pairs** -- a volume's
    degree word is only a cached count of how many portals name it. Writing the
    vertices alone leaves the graph claiming the DONOR's adjacency, which is why a
    reshaped graph stranded flyers at the first point.
    """
    if 'head' in po:
        h = navvol.CONTAINER + po['head']
        if joins is not None:
            struct.pack_into('<2i', out, h, int(joins[0]), int(joins[1]))
        if write_centre:
            # The record carries a centre AND the vertices, and they need not agree:
            # the vertices say where the opening IS, the centre says where to aim.
            struct.pack_into('<3f', out, h + 8, *[float(c) for c in centre])
    at = navvol.CONTAINER + po['at'] + 4
    other = [k for k in range(3) if k != flat_axis]
    #: A rectangle, not a square: the interface between two corridor volumes is as
    #: wide and as tall as they share, and those are rarely equal.
    ha = half
    hb = half if half2 is None else half2
    quad = list(centre if quad_centre is None else quad_centre)
    corners = [(-1, -1), (-1, 1), (1, 1), (1, -1)]
    for j, (sa, sb) in enumerate(corners[:len(po['verts'])]):
        v = list(quad)
        v[other[0]] += sa * ha
        v[other[1]] += sb * hb
        struct.pack_into('<3f', out, at + j * 12, *v)


def spawn_anchors(map_name):
    """Where a flyer actually appears: its spawner, and that spawner's door.

    The route's first point is where a flyer is told to go, NOT where it starts. On
    the custom map the flyer spawner sits 800uu behind the first route point, so a
    corridor fitted to the route alone left the spawn outside the graph -- NavPower
    then had nowhere to place the flyer and it never left the door. The spawn has to
    be inside the first volume.
    """
    from riftlord.mapeditor import leveldata  # noqa: PLC0415
    wiring = leveldata.onslaughts(map_name)
    out = []
    doors = wiring.get('doors') or {}
    for sp in wiring['spawners']:
        if not sp.get('flyer') or not sp.get('location'):
            continue
        out.append(tuple(float(c) for c in sp['location']))
        #: ONLY the door this spawner names. Treating "names no door" as "match any
        #: door" folded another spawner's doorway into the flight corridor and routed
        #: flyers into it -- measured on the custom map, whose flyer spawner has no
        #: door at all while the ground spawner's sits 246uu from the flyer route.
        want = str(sp.get('door') or '').rsplit('.', 1)[-1]
        if not want:
            continue
        for d in doors.values():
            if d.get('location') and d['export'] == want:
                out.append(tuple(float(c) for c in d['location']))
    return out


def doors_near_route(map_name, route, within=600.0):
    """Doors close enough to the flyer route to interfere, with distances.

    A door carries a UNavPowerObstructionComponent, so a route threading one that
    belongs to a different spawner puts flyers inside an obstruction. Reported rather
    than worked around: it is an authoring problem and the fix belongs in the level.
    """
    import math                                      # noqa: PLC0415
    from riftlord.mapeditor import leveldata  # noqa: PLC0415
    wiring = leveldata.onslaughts(map_name)
    mine = set()
    for sp in wiring['spawners']:
        if sp.get('flyer') and sp.get('door'):
            mine.add(str(sp['door']).rsplit('.', 1)[-1])
    out = []
    for d in (wiring.get('doors') or {}).values():
        at = d.get('location')
        if not at:
            continue
        near = min(math.dist(at, p) for p in route)
        if near <= within and d['export'] not in mine:
            out.append((d['export'], round(near)))
    return sorted(out, key=lambda t: t[1])


def fit_to_route(buf, points, touch_degrees=True, touch_portals=True,
                 park_spares=True, anchors=(), write_pairs=True,
                 write_centres=True):
    """Reposition a donor flight graph onto `points`, a route in travel order.

    One volume per segment, padded so consecutive boxes overlap, which is what the
    in-game measurements say actually matters: the volumes must approximately cover
    the route, and because the mover SNAPS the flyer onto the graph they also set the
    flight height. Portals and the trailing index were measured not to gate flight,
    so they are placed plausibly rather than exactly.

    Spare donor records are parked INSIDE the first volume, never left where the
    donor had them: a stray volume elsewhere in the world is somewhere a flyer could
    be snapped to.
    """
    out = bytearray(buf)
    parsed = navvol.parse(bytes(buf))
    vols = parsed['volumes']
    ports = parsed['portals']
    nseg = len(points) - 1
    if nseg < 1:
        raise SystemExit('need at least two route points')
    if len(vols) < nseg + 1:
        raise SystemExit('donor has %d volumes, need %d (one per segment plus a '
                         'spare to park)' % (len(vols), nseg + 1))
    if len(ports) < nseg:
        raise SystemExit('donor has %d portals, need %d' % (len(ports), nseg))

    if len(vols) < nseg + (1 if park_spares else 0):
        pass
    #: EVERY volume goes on the route, by cutting the polyline into as many
    #: equal-length spans as there are volumes -- not one per segment with the
    #: remainder parked.
    #:
    #: Parked spares caused trouble twice: stacked on one spot they were coincident
    #: boxes a kd-tree cannot separate, and spread past the route end they dangled a
    #: dead-end volume BELOW the rift for a flyer to be offered, which is what made
    #: them reverse course and stick near the end. Using them all on the route
    #: removes the question entirely.
    nvol = len(vols)
    seglen = [sum((points[i + 1][k] - points[i][k]) ** 2
                  for k in range(3)) ** 0.5 for i in range(nseg)]
    total = sum(seglen) or 1.0

    def at_dist(dist):
        # the point `dist` along the polyline
        for i, L in enumerate(seglen):
            if dist <= L or i == nseg - 1:
                f = (dist / L) if L else 0.0
                f = min(max(f, 0.0), 1.0)
                return tuple(points[i][k] + (points[i + 1][k] - points[i][k]) * f
                             for k in range(3))
            dist -= L
        return points[-1]

    boxes = []
    for i in range(nvol):
        d0, d1 = total * i / nvol, total * (i + 1) / nvol
        held = [at_dist(d0), at_dist(d1)]
        # every authored point inside this span, so a corner is never cut off
        run = 0.0
        for j in range(nseg + 1):
            if d0 - 1 <= run <= d1 + 1:
                held.append(points[j])
            if j < nseg:
                run += seglen[j]
        if i == 0:
            held += [tuple(x) for x in anchors]
        pad = (PAD_XY, PAD_XY, PAD_Z)
        lo = [min(p[k] for p in held) - pad[k] for k in range(3)]
        hi = [max(p[k] for p in held) + pad[k] for k in range(3)]
        boxes.append((lo, hi))

    #: Trim consecutive boxes so they ABUT instead of overlapping.
    #:
    #: Shipped volumes tile: across the ten flight levels almost every pair has zero
    #: overlap, and the few that do overlap by a few cubic uu. That matters because
    #: the kd-tree is a PARTITION -- it resolves a point to exactly one leaf. With
    #: boxes overlapping by hundreds of uu, an agent in the shared region can be
    #: resolved into the volume BEHIND it, and on any re-decision it heads back the
    #: way it came. That is what made wisps hesitate at junctions and hellbats walk
    #: the wrong way after breaking off a fight.
    #: OFF by default. Tiling is what shipped graphs do, but trimming the generated
    #: boxes to abut made BOTH wisps and hellbats double back and stick, where the
    #: overlapping version at least flew the route unobstructed. The route still
    #: passes through the trimmed volumes in perfect order (0->1->...->6, no gaps, no
    #: backward steps), so whatever the trim costs is not route coverage -- and until
    #: that is understood from the game rather than guessed at, the better-behaving
    #: shape is the default.
    for i in range(len(boxes) - 1 if ABUT else 0):
        a_lo, a_hi = boxes[i]
        b_lo, b_hi = boxes[i + 1]
        ca = [(a_lo[k] + a_hi[k]) / 2.0 for k in range(3)]
        cb = [(b_lo[k] + b_hi[k]) / 2.0 for k in range(3)]
        ax = max(range(3), key=lambda k: abs(cb[k] - ca[k]))
        mid = (ca[ax] + cb[ax]) / 2.0
        if ca[ax] <= cb[ax]:
            a_hi[ax], b_lo[ax] = mid, mid
        else:
            a_lo[ax], b_hi[ax] = mid, mid
    nseg = nvol          # from here on every volume is a route volume

    #: The chain: ends have one neighbour, the middle two. The first volume gains one
    #: more for the parked volume hanging off it, so sum(degree)/2 still equals the
    #: edge count the header already states.
    for i, (lo, hi) in enumerate(boxes):
        deg = (2 if 0 < i < nseg - 1 else 1) + (1 if i == 0 else 0) \
            if touch_degrees else vols[i]['flags']
        set_box(out, vols[i], lo, hi, deg)

    #: Spares are parked in a LINE past the end of the route, each in its own box --
    #: never stacked on one spot.
    #:
    #: Parking them all at the same point made several identical boxes, which a
    #: kd-tree cannot separate and which no shipped graph contains. With four of them
    #: the level crashed outright. Spread along the last segment's direction they
    #: stay distinct, separable, and out of the way.
    parked = []
    if park_spares and len(vols) > nseg:
        a, b = points[-2], points[-1]
        d = [b[k] - a[k] for k in range(3)]
        span = max(sum(x * x for x in d) ** 0.5, 1.0)
        step = [x / span * 400.0 for x in d]
        for j, v in enumerate(vols[nseg:]):
            c = [b[k] + step[k] * (j + 1) for k in range(3)]
            lo = [c[k] - 150.0 for k in range(3)]
            hi = [c[k] + 150.0 for k in range(3)]
            #: Where this spare meets the box before it -- half a step back from its
            #: own centre. The park portals used to be centred at the route START,
            #: while the volumes they join sit past its END, so one portal lay inside
            #: neither.
            parked.append((lo, hi, [c[k] - step[k] * 0.5 for k in range(3)]))
            set_box(out, v, lo, hi, 1 if touch_degrees else v['flags'])

    #: The adjacency, written in full.
    #:
    #: Volumes 0..nseg-1 form the route chain, so the junctions are (i, i+1). Every
    #: parked spare is linked to volume 0 so that no volume is orphaned -- a volume
    #: no portal names has degree 0, which no shipped graph has. Any portal left over
    #: repeats the last edge rather than being left pointing at the donor's volumes.
    if touch_portals:
        edges, centres, axes = [], [], []
        #: Placeholders taken from the BOXES, not from the route points.
        #:
        #: There is one volume per span now, and the span count is the donor's volume
        #: count -- not the number of authored points. Indexing `points[j + 1]` here
        #: therefore ran off the end whenever a route had fewer points than the donor
        #: had volumes. Both values are replaced below by ones derived from where the
        #: two boxes actually meet; these only matter if that fails.
        for j in range(nseg - 1):
            ca = [(boxes[j][0][k] + boxes[j][1][k]) / 2.0 for k in range(3)]
            cb = [(boxes[j + 1][0][k] + boxes[j + 1][1][k]) / 2.0 for k in range(3)]
            edges.append((j, j + 1))
            centres.append([(ca[k] + cb[k]) / 2.0 for k in range(3)])
            axes.append(max(range(3), key=lambda k: abs(cb[k] - ca[k])))
        #: Spares hang off the route in a CHAIN, not all off volume 0. Linking every
        #: spare to volume 0 made it a degree-5 hub; no shipped graph has a degree
        #: above 2, so a chain keeps the emitted graph the same shape as a real one.
        #: Each spare hangs off the one before it, starting from the LAST route
        #: volume, and each link's portal sits where those two boxes actually meet.
        prev = nseg - 1
        for j, vi in enumerate(range(nseg, len(vols))):
            edges.append((prev, vi))
            centres.append(parked[j][2] if j < len(parked) else points[-1])
            axes.append(max(range(3),
                            key=lambda k: abs(points[-1][k] - points[-2][k])))
            prev = vi
        while edges and len(edges) < len(ports):
            edges.append(edges[-1]); centres.append(centres[-1]); axes.append(axes[-1])

        #: SORTED, because every shipped graph is -- all ten maps have their portal
        #: pairs strictly ascending with volA < volB, without exception. Writing the
        #: chain junctions and then the park links left the sequence restarting
        #: (0,1),(1,2)...(4,5),(0,6), which is not ascending, and the level hung and
        #: crashed. The runtime evidently relies on the order, which is what a
        #: per-volume contiguous range or a binary search over this table would need.
        order = sorted(range(len(edges)),
                       key=lambda i: (min(edges[i]), max(edges[i])))
        edges = [(min(edges[i]), max(edges[i])) for i in order]
        centres = [centres[i] for i in order]
        axes = [axes[i] for i in order]

        #: Every portal is placed in the GEOMETRIC OVERLAP of the two volumes it
        #: joins: centred on it, flat across its thinnest axis, and sized to fit
        #: inside it. Hand-picked centres kept landing outside one of the pair --
        #: first the junction portals, then the parked ones - because a centre that
        #: suits one box need not lie in the other. Derived from the boxes, it cannot.
        allbox = boxes + [(p0, p1) for p0, p1, _c in parked]
        for po, pair, c, ax in zip(ports, edges, centres, axes):
            a_box = allbox[pair[0]] if pair[0] < len(allbox) else None
            b_box = allbox[pair[1]] if pair[1] < len(allbox) else None
            half = 60.0
            half2 = None
            quad_centre = None
            if a_box and b_box:
                # The region the two volumes share. `>=` because tiling neighbours
                # meet in a zero-thickness face, and that face is where a shipped
                # portal sits -- Scenario_Sand_05's is at y = -7350, exactly the
                # boundary between its first two volumes.
                lo = [max(a_box[0][k], b_box[0][k]) for k in range(3)]
                hi = [min(a_box[1][k], b_box[1][k]) for k in range(3)]
                if all(hi[k] >= lo[k] for k in range(3)):
                    # Centre it where the ROUTE crosses, not in the middle of the
                    # shared region.
                    #
                    # Measured in game: a wisp already past the boundary, sitting in
                    # the overlap of volumes 0 and 1, was resolved into volume 0 and
                    # flew BACKWARD (+x) to reach a portal placed at the overlap's
                    # centre, 330uu behind where its route actually crossed.
                    # IsBackPathing, IsOffPath and GetIsStuck were all false --
                    # NavPower was steering it to the doorway on purpose. Put the
                    # doorway on the path and there is nothing to detour to.
                    ext = [hi[k] - lo[k] for k in range(3)]
                    #: The portal's plane is the cross-section of the CORRIDOR, so
                    #: its normal is the direction of travel where the route crosses
                    #: -- not the thinnest axis of the shared region. With a wide
                    #: corridor the shared region is a fat box whose thinnest axis
                    #: can point anywhere, and a portal lying across the route
                    #: instead of athwart it is a wall, not a doorway.
                    cdir = _route_direction(points, a_box, b_box)
                    ax = (max(range(3), key=lambda k: abs(cdir[k])) if cdir
                          else min(range(3), key=lambda k: ext[k]))
                    other = [k for k in range(3) if k != ax]
                    #: THE WHOLE SHARED FACE, not a small quad in the middle of it.
                    #:
                    #: A shipped portal is a real doorway -- Scenario_Sand_05's is
                    #: 500x500 inside a face 2000x1200, because a wall with a hole in
                    #: it separates two rooms. Between two artificial corridor
                    #: volumes there is no wall, so the entire interface is passable.
                    #: A small quad makes the flyer aim at an arbitrary point on an
                    #: open boundary, which is a detour wherever it is put -- and
                    #: moving it around is what the last several attempts did.
                    #: The opening is the whole face; the AIM POINT is where the
                    #: route crosses it.
                    #:
                    #: With the quad centred on the face, a flyer still steers at the
                    #: face's middle -- and volume 0 is stretched back to the spawner,
                    #: so its face with volume 1 is lopsided and that middle sits off
                    #: the route. That is the small backwards wobble left at the door.
                    #: Vertices and centre are separate fields, so the opening can
                    #: span the face while the aim point sits on the path.
                    face = [(lo[k] + hi[k]) / 2.0 for k in range(3)]
                    cross = _route_crossing(points, a_box, b_box)
                    c = face if cross is None else \
                        [min(max(cross[k], lo[k]), hi[k]) for k in range(3)]
                    quad_centre = face
                    half = max(ext[other[0]] / 2.0, 1.0)
                    half2 = max(ext[other[1]] / 2.0, 1.0)
            set_portal(out, po, c, ax, half, half2=half2,
                       quad_centre=quad_centre,
                       joins=pair if write_pairs else None,
                       write_centre=write_centres)

        #: The degree word is a cached count of the portals naming each volume, so it
        #: has to be recomputed from the edges actually written. Writing one without
        #: the other is what made the graph contradict itself.
        if touch_degrees:
            deg = [0] * len(vols)
            for a, b in edges[:len(ports)]:
                deg[a] += 1
                deg[b] += 1
            for i, v in enumerate(vols):
                struct.pack_into('<I', out, navvol.CONTAINER + v['at'] + 4, deg[i])

    #: The bbox must enclose the parked spares too, or a volume sits outside the
    #: graph's own bounds.
    allb = boxes + [(p0, p1) for p0, p1, _c in parked]
    lo = [min(b[0][k] for b in allb) for k in range(3)]
    hi = [max(b[1][k] for b in allb) for k in range(3)]
    base = navvol.CONTAINER + navvol.OFF_BBOX
    struct.pack_into('<3f', out, base, *lo)
    struct.pack_into('<3f', out, base + 12, *hi)
    return navvol.reseal(bytes(out)), boxes


def walk_properties(data, names):
    """[(name, type, tag_start, record_end)] for one export's property block."""
    out = []
    o = 0
    n = len(data)
    while o + 24 <= n:
        start = o
        ni = struct.unpack_from('<i', data, o)[0]
        nm = names[ni][0] if 0 <= ni < len(names) else ''
        if nm == 'None':
            out.append(('None', '', start, o + 8))
            break
        ti = struct.unpack_from('<i', data, o + 8)[0]
        tp = names[ti][0] if 0 <= ti < len(names) else ''
        size = struct.unpack_from('<i', data, o + 16)[0]
        o += 24
        if tp == 'BoolProperty':
            o += 2
            out.append((nm, tp, start, o))
            continue
        if tp == 'StructProperty':
            o += 8 + 17
        elif tp in ('ArrayProperty', 'ByteProperty', 'EnumProperty'):
            o += 8 + 1
        else:
            o += 1
        o += size
        out.append((nm, tp, start, o))
    return out


def uint32_property(pkg, name, value):
    """A cooked UInt32Property tag and payload: 24-byte tag, guid flag, 4-byte value."""
    ni = pkg.add_name(name)
    ti = pkg.add_name('UInt32Property')
    return (struct.pack('<iiiiii', ni, 0, ti, 0, 4, 0)
            + b'\x00' + struct.pack('<I', value))


def nav_export_index(pkg):
    """The index of the primary nav-data export: the one with the biggest buffer."""
    best = None
    for i, e in enumerate(pkg.exports):
        props = walk_properties(e['data'], pkg.names)
        got = {nm: (s, en) for nm, _t, s, en in props}
        if 'NavMeshBufferSize' not in got:
            continue
        s, en = got['NavMeshBufferSize']
        size = struct.unpack_from('<I', e['data'], en - 4)[0]
        if best is None or size > best[1]:
            best = (i, size, props)
    return best


def rebuild_tree(buf):
    """Replace the buffer's kd-tree with one built from its CURRENT volume boxes.

    The tail is the structure that answers "which volume holds this point", so a
    graph whose volumes have moved needs it rebuilt or nothing can be placed on it.
    Its length depends only on the volume count, which never changes here, so this
    is an in-place splice and the buffer stays the same size.
    """
    parsed = navvol.parse(buf)
    boxes = [v['box'] for v in parsed['volumes'] if v['box']]
    if len(boxes) != parsed['nvolumes'] or len(boxes) < 2:
        raise SystemExit('cannot rebuild the tree: %d of %d volumes decode'
                         % (len(boxes), parsed['nvolumes']))
    #: Located by SIZE, not by the last portal. The tail is always
    #: 44 + 16*(nvolumes-1) bytes, so it can be found from the end of the buffer --
    #: whereas portal parsing fails exactly when volumes have moved and the donor's
    #: portals fall outside the new bbox, which is the case this has to handle.
    tail_len = navvoltree.HDR + 16 * (parsed['nvolumes'] - 1)
    start = len(buf) - tail_len
    old_tail = buf[start:]
    tree = navvoltree.build(boxes)
    tail = navvoltree.emit(tree, parsed['bbox'])
    if len(tail) != len(old_tail):
        raise SystemExit('rebuilt tree is %d bytes, the old one was %d'
                         % (len(tail), len(old_tail)))
    out = bytearray(buf)
    out[start:] = tail
    return navvol.reseal(bytes(out))


def route_of(map_name):
    """The flyer chain of a level, in travel order, as [(x, y, z)].

    Starts at whatever the flyer-only spawner names as its StartingFlyerPathPoint and
    follows the first branch of each point. The tail of the chain is usually not
    flagged flyersOnly -- it joins the ground route at the rift -- so the walk follows
    the links rather than the flag.
    """
    from riftlord.mapeditor import leveldata  # noqa: PLC0415
    wiring = leveldata.onslaughts(map_name)
    pts = wiring['points']
    byidx = {p['index']: p for p in pts.values() if p.get('index') is not None}
    starts = []
    for sp in wiring['spawners']:
        f = sp.get('flyer')
        if f:
            starts.append(str(f).rsplit('.', 1)[-1])
    if not starts:
        raise SystemExit('%s has no flyer spawner, so there is no route to cover'
                         % map_name)
    out = []
    seen = set()
    cur = starts[0]
    while cur and cur in pts and cur not in seen:
        seen.add(cur)
        p = pts[cur]
        if p.get('location'):
            out.append(tuple(float(c) for c in p['location']))
        nxt = p.get('next') or []
        cur = (byidx[nxt[0]['PointIndex']]['export']
               if nxt and nxt[0]['PointIndex'] in byidx else None)
    return out


def inject(target, donor, out_dir, reseal=True, route=None, mode='fit'):
    work = os.path.join(out_dir, target)
    os.makedirs(work, exist_ok=True)
    tmp = os.path.join(work, '.donor')
    os.makedirs(tmp, exist_ok=True)

    dfiles = navpatch.fetch_map(navpatch.map_path(donor), tmp,
                                exclude=our_paks())
    db = navpatch.nav_buffers(dfiles['.umap'], dfiles['.uexp'])
    if not db or not db[4]:
        raise SystemExit('%s ships no volumetric buffer to donate' % donor)
    buf = open(dfiles['.uexp'], 'rb').read()[db[3]:db[3] + db[4]]
    #: The donor as shipped, to compare against. For a rigid transform the invariant
    #: is that the TOPOLOGY is unchanged -- same volumes found, same degrees, same
    #: header counts. Requiring absolute self-consistency instead just measures how
    #: much of the donor this scanner can decode, which fails on donors that are
    #: only partly decodable and says nothing about the edit.
    donor_before = navvol.parse(buf)
    boxes = None
    if route and mode == 'fitsafe':
        #: The minimal step from `grow`, which reshaped every volume on Slag Field
        #: and did NOT crash, towards a route-fitted graph. `fit` additionally
        #: rewrote the degree words, moved the portals and parked the spare volumes;
        #: this changes only what `grow` changed -- planes, centre, radius -- plus
        #: the bbox, which `displace` already rewrote safely.
        #: Spares ARE parked inside the first volume. Leaving one where the donor
        #: had it strands a volume in the donor level's airspace, and because the
        #: mover snaps a flyer onto the graph that is somewhere a flyer can be
        #: pulled to. Parking only rewrites planes, centre and radius -- the same
        #: edit `grow` survived -- so it costs nothing in risk.
        anchors = spawn_anchors(target)
        for name, dist in doors_near_route(target, route):
            print('WARNING  door %s is %duu from the flyer route and belongs to '
                  'another spawner -- its obstruction will catch flyers'
                  % (name, dist))
        if anchors:
            print('anchors  %d spawn point(s)/door(s) folded into the first volume'
                  % len(anchors))
        buf, boxes = fit_to_route(buf, route, touch_degrees=False,
                                  touch_portals=False, park_spares=True,
                                  anchors=anchors)
    elif route and mode == 'fitadj':
        #: Volumes on the route, the adjacency written into the portal records, and
        #: the tree rebuilt -- but the DEGREE WORDS LEFT ALONE.
        #:
        #: `fittree` (volumes + tree) loads and now carries flyers past the door into
        #: the second volume, so the tree is both safe and necessary. What still
        #: stops them is the adjacency, which is the donor's. `fitdeg` proved writing
        #: the degree word is fatal, and `fitfull` wrote it too. Since the degree is
        #: only a cached count of portal references, this writes the references and
        #: not the cache.
        anchors = spawn_anchors(target)
        buf, boxes = fit_to_route(buf, route, touch_degrees=False,
                                  touch_portals=True, park_spares=True,
                                  anchors=anchors)
        buf = rebuild_tree(buf)
    elif route and mode == 'fittree':
        #: fitsafe PLUS the rebuilt tree, and nothing else.
        #:
        #: fitsafe loads. fitfull (volumes + pairs + centres + degrees + tree)
        #: crashes. The tree builder reproduces the shipped trees' shape, axes and
        #: clips, so it is probably innocent -- but "probably" has cost two launches
        #: already, so it gets tested alone.
        anchors = spawn_anchors(target)
        buf, boxes = fit_to_route(buf, route, touch_degrees=False,
                                  touch_portals=False, park_spares=True,
                                  anchors=anchors)
        buf = rebuild_tree(buf)
    elif route and mode == 'fitfull':
        #: Everything, consistently: volumes on the route, the adjacency written into
        #: the portal records, degrees recomputed from it, AND the kd-tree rebuilt to
        #: match. Every earlier attempt changed the topology while leaving the tree
        #: describing the donor, which is what crashed.
        anchors = spawn_anchors(target)
        buf, boxes = fit_to_route(buf, route, touch_degrees=True,
                                  touch_portals=True, park_spares=True,
                                  anchors=anchors)
        buf = rebuild_tree(buf)
    elif route and mode == 'chain':
        #: The topology we need, BORROWED instead of written.
        #:
        #: Every attempt to author adjacency crashes -- writing the degree word alone
        #: is fatal, because the trailing kd-tree is indexed by it. But moving volumes
        #: and portal vertices is measured to load. So pick a donor whose adjacency is
        #: ALREADY a chain, lay volume i onto route segment i in the donor's own
        #: order, and the adjacency is right by construction without a byte of it
        #: being touched.
        p0 = navvol.parse(buf)
        nv = len(p0['volumes'])
        pairs = [po.get('volumes') for po in p0['portals']]
        want = [(i, i + 1) for i in range(nv - 1)]
        if nv != p0['nvolumes'] or pairs != want:
            raise SystemExit(
                'donor %s is not a pure chain (%d volumes, pairs %s) -- chain mode '
                'needs one, e.g. ScenarioDLC_02' % (donor, nv, pairs[:4]))
        if nv < len(route) - 1:
            raise SystemExit('donor has %d volumes, the route needs %d'
                             % (nv, len(route) - 1))
        anchors = spawn_anchors(target)
        buf, boxes = fit_to_route(
            buf, route, anchors=anchors, park_spares=True, touch_portals=True,
            touch_degrees=False, write_pairs=False, write_centres=False)
    elif route and mode in ('fitpair', 'fitcentre', 'fitdeg'):
        #: ONE variable each, on top of fitportal (which loads).
        #:
        #: fitgraph writes portal pairs, portal centres and the degree words all at
        #: once and crashes; fitportal writes none of them and loads. Guessing which
        #: of the three it is has already cost two launches, so each is built alone
        #: and the first crash names it.
        anchors = spawn_anchors(target)
        buf, boxes = fit_to_route(
            buf, route, anchors=anchors, park_spares=True, touch_portals=True,
            touch_degrees=(mode == 'fitdeg'),
            write_pairs=(mode == 'fitpair'),
            write_centres=(mode == 'fitcentre'))
    elif route and mode == 'fitgraph':
        #: The complete, self-consistent graph: volumes reshaped onto the route AND
        #: the adjacency rewritten -- portal pairs, portal centres, and the degree
        #: words recomputed from them.
        anchors = spawn_anchors(target)
        buf, boxes = fit_to_route(buf, route, touch_degrees=False,
                                  touch_portals=True, park_spares=True,
                                  anchors=anchors)
    elif route and mode == 'fitportal':
        #: fitsafe PLUS the portals, and nothing else.
        #:
        #: Bisecting the old `fit` crash. `fit` changed three things at once --
        #: degrees, portals and parking. Parking is already in `fitsafe`, which
        #: loads, so it is innocent. This adds the portals and still leaves the
        #: degree words alone, so a crash here names the portals and a clean load
        #: names the degrees.
        #:
        #: It is also the fix for the measured symptom: with the donor's portals left
        #: behind, out of the new bbox, a flyer reaches the first point and cannot
        #: cross into the next volume -- it stalls there and jitters, which is a path
        #: search failing every tick.
        anchors = spawn_anchors(target)
        buf, boxes = fit_to_route(buf, route, touch_degrees=False,
                                  touch_portals=True, park_spares=True,
                                  anchors=anchors)
    elif route and mode == 'fit':
        buf, boxes = fit_to_route(buf, route, anchors=spawn_anchors(target))
    elif route and mode == 'best':
        #: The best RIGID placement, searched over translations. Rigid keeps the
        #: trailing index internally consistent -- merely offset -- which is the
        #: configuration the in-game `displace` test showed does not crash, where
        #: reshaping the volumes does.
        from riftlord.build.navigation import navvolfit  # noqa: PLC0415
        parsed = navvol.parse(buf)
        bx = [v['box'] for v in parsed['volumes'] if v['box']]
        pts = navvolfit.sample(route)
        delta, hit = navvolfit.best_delta(bx, pts)
        buf = navvol.reseal(volwrite.displace(buf, delta))
        print('fit      %s shifted %s covers %.0f%% of the sampled route'
              % (donor, ['%.0f' % x for x in delta], 100.0 * hit / len(pts)))
    elif route and mode == 'move':
        #: Rigid translation only, which keeps the donor's internal geometry -- and
        #: therefore its trailing index -- self-consistent. The in-game `displace`
        #: test moved a whole graph 20000uu and the level still loaded, so a rigid
        #: move is known not to be fatal, where reshaping may be.
        parsed = navvol.parse(buf)
        bb = parsed['bbox']
        centre = [(bb[k] + bb[k + 3]) / 2.0 for k in range(3)]
        want = [sum(pt[k] for pt in route) / len(route) for k in range(3)]
        buf = volwrite.displace(buf, [want[k] - centre[k] for k in range(3)])
        buf = navvol.reseal(buf)
    elif reseal:
        buf = navvol.reseal(buf)
    if not navvol.verify(buf):
        raise SystemExit('the donor buffer fails its checksum')

    game_path = navpatch.map_path(target)
    skip = our_paks()
    tfiles = navpatch.fetch_map(game_path, work, exclude=skip)
    pkg = Package(open(tfiles['.umap'], 'rb').read(),
                  open(tfiles['.uexp'], 'rb').read())
    #: Snapshot the export table BEFORE anything is edited. Taken afterwards it
    #: compares the damage against itself and passes no matter what.
    original_meta = [bytes(e['meta']) for e in pkg.exports]

    found = nav_export_index(pkg)
    if not found:
        raise SystemExit('no nav-data export with a buffer in %s' % target)
    idx, mesh_size, props = found
    have = {nm for nm, _t, _s, _e in props}
    if 'NavVolumesBufferSize' in have:
        raise SystemExit(
            '%s already has a volumetric buffer. If this tool installed one, that is '
            'a self-read: it was skipping %s and still found one.'
            % (target, list(skip) or 'nothing'))

    data = bytearray(pkg.exports[idx]['data'])
    before = len(data)
    # The property goes directly after NavMeshBufferSize, which is where every
    # shipped flight level puts it.
    at = next(en for nm, _t, _s, en in props if nm == 'NavMeshBufferSize')
    tag = uint32_property(pkg, 'NavVolumesBufferSize', len(buf))
    data[at:at] = tag
    # The buffers sit at the end of the export, surface FIRST then volumes.
    data += buf
    #: Only the payload is set. Package.write() recomputes SerialSize and
    #: SerialOffset for every export from the payload lengths, so touching the export
    #: metadata here is unnecessary -- and writing the size at the wrong offset
    #: silently overwrote TemplateIndex and OuterIndex, which crashed the game on
    #: load while every offline check passed.
    pkg.exports[idx]['data'] = bytes(data)
    pkg.exports[idx]['size'] = len(data)

    uasset, uexp = pkg.write()
    open(tfiles['.umap'], 'wb').write(uasset)
    open(tfiles['.uexp'], 'wb').write(uexp)

    #: Re-read from scratch with the ORDINARY reader. Checking the object we just
    #: built would only confirm it agrees with itself.
    nb = navpatch.nav_buffers(tfiles['.umap'], tfiles['.uexp'])
    raw = open(tfiles['.uexp'], 'rb').read()
    #: The check that would have caught the crash: we changed one export's payload,
    #: so NO export's metadata may differ except its size and offset fields. Nothing
    #: else here reads the export table, which is how corrupt references survived
    #: nine passing checks and pkgcheck.
    meta_bad = []
    from riftlord.formats.ue import uassetio as _ua
    fresh = Package(uasset, uexp)
    for i, (was, now) in enumerate(zip(original_meta,
                                       [bytes(e['meta']) for e in fresh.exports])):
        a, b2 = bytearray(was), bytearray(now)
        for at in (_ua.SERIAL_SIZE_AT, _ua.SERIAL_OFFSET_AT):
            a[at:at + 8] = b'\0' * 8
            b2[at:at + 8] = b'\0' * 8
        if bytes(a) != bytes(b2):
            meta_bad.append(i)

    checks = [('the export grew by the tag plus the buffer',
               len(data) - before == len(tag) + len(buf)),
              ('no export metadata changed except size/offset (%d bad)'
               % len(meta_bad), not meta_bad),
              ('the patched package re-reads', nb is not None)]
    if nb:
        checks.append(('surface buffer size unchanged', nb[2] == mesh_size))
        checks.append(('volumetric buffer found', nb[4] == len(buf)))
        got = raw[nb[3]:nb[3] + nb[4]]
        checks.append(('volumetric buffer byte-identical', got == buf))
        checks.append(('its checksum verifies', navvol.verify(got)))
        p = navvol.parse(got)
        checks.append(('volumes decode', len(p['volumes']) > 0))
        checks.append(('every volume self-consistent',
                       all(v['centre_ok'] and v['radius_ok'] for v in p['volumes'])))
        surf = raw[nb[1]:nb[1] + nb[2]]
        checks.append(('the surface graph still verifies', navvol.verify(surf)))
        if mode in ('fittree', 'fitfull', 'fitadj'):
            tl = navvoltree.HDR + 16 * (p['nvolumes'] - 1)
            for nm, okk in navvoltree.check(
                    got[len(got) - tl:], p['nvolumes'],
                    [v['box'] for v in p['volumes'] if v['box']]):
                checks.append(('tree: ' + nm, okk))
        if route:
            from riftlord.build.navigation import navvolfit  # noqa: PLC0415
            bb = [v['box'] for v in p['volumes'] if v['box']]
            covered = sum(1 for pt in route
                          if any(all(lo[k] - 1 <= pt[k] <= hi[k] + 1
                                     for k in range(3)) for lo, hi in bb))
            pts = navvolfit.sample(route)
            along = navvolfit.covered(bb, pts)
            if mode in ('fit', 'fitsafe'):
                anc = spawn_anchors(target)
                got = sum(1 for at in anc
                          if any(all(lo[k] - 1 <= at[k] <= hi[k] + 1
                                     for k in range(3)) for lo, hi in bb))
                checks.append(('the flyer SPAWN is inside a volume (%d/%d anchors)'
                               % (got, len(anc)), got == len(anc) and bool(anc)))
            checks.append(('authored points covered %d/%d, sampled route %.0f%%'
                           % (covered, len(route), 100.0 * along / len(pts)),
                           covered == len(route) if mode == 'fit' else along > 0))
            # Consecutive boxes must overlap or the corridor has a hole in it.
            gaps = 0
            for i in range(len(boxes) - 1 if boxes else 0):
                a, b2 = boxes[i], boxes[i + 1]
                if any(a[1][k] < b2[0][k] or b2[1][k] < a[0][k] for k in range(3)):
                    gaps += 1
            if boxes:
                #: Abutting, not overlapping: shipped graphs tile, and the kd-tree
                #: resolves a point to exactly one volume.
                over = 0
                for i in range(len(boxes) - 1):
                    a, b2 = boxes[i], boxes[i + 1]
                    vol = 1.0
                    for k in range(3):
                        vol *= max(0.0, min(a[1][k], b2[1][k])
                                   - max(a[0][k], b2[0][k]))
                    if vol > 1.0:
                        over += 1
                checks.append(('consecutive volumes abut without overlapping '
                               '(%d gap(s), %d overlap(s))' % (gaps, over),
                               gaps == 0 and (over == 0 or not ABUT)))
            #: Portal and adjacency checks belong to the modes that WRITE portals.
            #: `fittree` leaves them as the donor's, which after moving the volumes
            #: puts them outside the new bbox and makes them unreadable -- expected,
            #: not a defect, and asserting otherwise refuses to ship the experiment.
            if mode in ('fit', 'fitportal', 'fitgraph', 'fitpair',
                        'fitcentre', 'fitdeg', 'chain', 'fitfull', 'fitadj'):
                #: Every portal must lie inside the two volumes it joins, and inside
                #: the graph bbox. A portal left outside the bbox is the state that
                #: stranded flyers at the first point.
                bb2 = [v['box'] for v in p['volumes'] if v['box']]
                bx = p['bbox']
                inb = sum(1 for po in p['portals']
                          for v in [po['verts']]
                          if all(bx[k] - 1 <= q[k] <= bx[k + 3] + 1
                                 for q in v for k in range(3)))
                joins = 0
                for po in p['portals']:
                    touch = sum(1 for lo, hi in bb2
                                if all(lo[k] - 1 <= q[k] <= hi[k] + 1
                                       for q in po['verts'] for k in range(3)))
                    if touch >= 2:
                        joins += 1
                checks.append(('portals read back (%d) and are in bounds (%d)'
                               % (len(p['portals']), inb),
                               bool(p['portals']) and inb == len(p['portals'])))
                checks.append(('each portal lies inside the two volumes it joins '
                               '(%d/%d)' % (joins, len(p['portals'])),
                               joins == len(p['portals']) and bool(p['portals'])))
                #: The adjacency itself: pairs valid, degrees agreeing with them, and
                #: the graph CONNECTED. A graph whose volumes cover the route but
                #: whose portals name the donor's volumes strands a flyer at the
                #: first point -- covered, and unable to leave.
                pairs = [po.get('volumes') for po in p['portals']]
                nv = len(p['volumes'])
                okpair = sum(1 for q in pairs
                             if q and q[0] != q[1]
                             and 0 <= q[0] < nv and 0 <= q[1] < nv)
                checks.append(('every portal names two valid distinct volumes '
                               '(%d/%d)' % (okpair, len(pairs)),
                               okpair == len(pairs) and bool(pairs)))
                ref = [0] * nv
                for q in pairs:
                    if q and 0 <= q[0] < nv and 0 <= q[1] < nv:
                        ref[q[0]] += 1
                        ref[q[1]] += 1
                degs = [v['flags'] for v in p['volumes']]
                #: A bisect build changes ONE of pairs/degrees on purpose, so the two
                #: are meant to disagree; demanding agreement would refuse to ship the
                #: very experiment that tells them apart.
                if mode not in ('fitpair', 'fitcentre', 'fitdeg', 'fitadj'):
                    checks.append(('degree words equal the portal reference counts',
                                   degs == ref))
                elif mode == 'fitadj':
                    checks.append(('degrees deliberately left as the donor wrote '
                                   'them (%s refcounts)'
                                   % ('==' if degs == ref else '!='), True))
                else:
                    checks.append(('bisect: degrees %s refcounts (expected)'
                                   % ('==' if degs == ref else '!='), True))
                adj = {i: set() for i in range(nv)}
                for q in pairs:
                    if q and 0 <= q[0] < nv and 0 <= q[1] < nv:
                        adj[q[0]].add(q[1])
                        adj[q[1]].add(q[0])
                seen, stack = {0}, [0]
                while stack:
                    cur = stack.pop()
                    for nxt in adj[cur]:
                        if nxt not in seen:
                            seen.add(nxt)
                            stack.append(nxt)
                need = set(range(min(len(boxes or []), nv))) if boxes else set(range(nv))
                checks.append(('the volume graph is connected (%d/%d reachable from '
                               'volume 0)' % (len(seen), nv), need <= seen))
                #: Measured on all ten shipped maps without exception.
                clean = [q for q in pairs if q]
                if mode == 'chain':
                    before_pairs = [q.get('volumes') for q in donor_before['portals']]
                    checks.append(
                        ('chain: portal pairs untouched (the field whose rewrite '
                         'crashes)', pairs == before_pairs))
                    checks.append(
                        ('chain: degree words untouched',
                         [v['flags'] for v in p['volumes']]
                         == [v['flags'] for v in donor_before['volumes']]))
                checks.append(('portal pairs ascending, as every shipped graph is',
                               (clean == sorted(clean) and bool(clean))
                               if mode != 'fitcentre' else True))
                checks.append(('every pair has volA < volB',
                               all(a < b for a, b in clean) and bool(clean)))
                if mode in ('fitfull',):
                    tbuf = got[len(got) - (navvoltree.HDR
                                               + 16 * (p['nvolumes'] - 1)):]
                    for nm, okk in navvoltree.check(tbuf, p['nvolumes'], bb):
                        checks.append(('tree: ' + nm, okk))
            if mode == 'fit':
                checks.append(('degrees sum to twice the edge count', p['edges_ok']))
                checks.append(('volume count matches the header', p['complete']))
            else:
                checks.append(
                    ('topology unchanged: %d volumes before and after'
                     % len(donor_before['volumes']),
                     len(p['volumes']) == len(donor_before['volumes'])))
                # Only a mode that does NOT touch the adjacency may claim this.
                # fitgraph rewrites the degrees on purpose, and its own invariant --
                # degrees equal the portal reference counts -- is the stronger one.
                if mode not in ('fit', 'fitportal', 'fitgraph', 'fitdeg',
                                'fitfull', 'fitadj'):
                    checks.append(
                        ('degrees unchanged',
                         [v['flags'] for v in p['volumes']]
                         == [v['flags'] for v in donor_before['volumes']]))
                checks.append(
                    ('header counts unchanged',
                     (p['nvolumes'], p['count'])
                     == (donor_before['nvolumes'], donor_before['count'])))
                moved = sum(1 for a, b in zip(donor_before['volumes'], p['volumes'])
                            if a['box'] != b['box'])
                checks.append(('every volume actually moved (%d/%d)'
                               % (moved, len(p['volumes'])),
                               moved == len(p['volumes']) if mode != 'raw'
                               else moved == 0))
    return {'files': tfiles, 'game_path': game_path, 'checks': checks,
            'buffer': len(buf), 'mesh': mesh_size, 'donor': donor,
            'grew': len(data) - before}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--target', required=True)
    ap.add_argument('--donor', default='Scenario_Sand_05')
    ap.add_argument('--mode', choices=('raw', 'move', 'fit', 'fitsafe', 'fitportal', 'fitgraph', 'fitpair', 'fitcentre', 'fitdeg', 'chain', 'fittree', 'fitadj', 'fitfull', 'best'), default='fit',
                    help="raw: inject the donor graph untouched, the control that "
                         'shows whether injection itself is sound. move: translate it '
                         'rigidly onto the route, which keeps its trailing index '
                         'self-consistent. fit: reshape one volume per segment, which '
                         'covers the route but leaves the index describing the donor '
                         'and CRASHES. best: search translations for the placement '
                         'that covers the most route while staying rigid.')
    ap.add_argument('--route', action='store_true',
                    help="reposition the donor's volumes onto the target's flyer "
                         'chain, which is what makes the graph useful rather than '
                         'merely present')
    ap.add_argument('--pak', metavar='NAME', nargs='?',
                    const='730-VolInject_P.pak')
    ap.add_argument('--install', metavar='NAME', nargs='?', const=True,
                    help='write the pak straight into Content/Paks/~mods/; takes an '
                         'optional name, which must sort AFTER any mod pak that '
                         'already serves this level')
    a = ap.parse_args()

    route = route_of(a.target) if a.route else None
    if route:
        print('route    %d flyer point(s), %d segment(s)' % (len(route), len(route) - 1))
        for pt in route:
            print('           %8.0f %8.0f %8.0f' % pt)
    r = inject(a.target, a.donor, OUT, route=route, mode=a.mode)
    print('mode     %s' % a.mode)
    print('target   %s' % a.target)
    print('donor    %s  (%d byte flight graph)' % (r['donor'], r['buffer']))
    print('export   grew %d bytes (property tag + buffer)' % r['grew'])
    bad = [lbl for lbl, ok in r['checks'] if not ok]
    for lbl, ok in r['checks']:
        print('   %-42s %s' % (lbl, 'ok' if ok else 'FAILED'))
    if bad:
        raise SystemExit('refusing to ship: %s' % bad)

    from riftlord.core import pkgcheck
    found = pkgcheck.problems(open(r['files']['.umap'], 'rb').read(),
                              open(r['files']['.uexp'], 'rb').read(),
                              label=a.target, kind='level')
    print('pkgcheck %s' % ('no problems' if not found else found))
    if found:
        raise SystemExit('pkgcheck found problems')

    if a.pak or a.install:
        inner = r['game_path'].replace('/Game/', 'OMD/Content/')
        name = (a.install if isinstance(a.install, str) else None) \
            or a.pak or '730-VolInject_P.pak'
        #: Load order decides this. When the target level is itself served from a mod
        #: pak, ours has to be mounted later or the original map simply wins and the
        #: injected graph is never seen -- a silent no-op that looks like the graph
        #: being rejected.
        #: live_index, not overrides: a brand-new map is not an override of a base
        #: game asset, so overrides() does not list it and the check silently passed.
        #: The SAME exclusion the read used, or the note names a pak that was
        #: skipped and misreports where the target actually came from.
        got = navpatch.live_index(
            exclude=tuple(set(our_paks()) | {name})).get(r['game_path'] + '.umap')
        src = got[0] if got else None
        if src:
            ours = os.path.basename(name)
            print('note     the target comes from %s' % src)
            if ours <= os.path.basename(src):
                print('WARNING  %r sorts at or before %r, so the original may win. '
                      'Give this pak a higher prefix.' % (ours, os.path.basename(src)))
            else:
                print('         %r sorts after it, so this pak takes precedence' % ours)
        dest = os.path.join(navpatch.PAK_DIR, '~mods', name) if a.install \
            else os.path.join(OUT, name)
        info = pakv4.write(dest, {inner + '.umap': r['files']['.umap'],
                                  inner + '.uexp': r['files']['.uexp']})
        pakv4.read(info['pak'])
        if a.install:
            _remember(os.path.basename(info['pak']))
            # One of this tool's paks at a time. Two copies of the same map both
            # installed is a race decided by filename, not a test.
            for other in our_paks():
                if other == os.path.basename(info['pak']):
                    continue
                stale = os.path.join(navpatch.PAK_DIR, '~mods', other)
                from riftlord.core import pakguard
                pakguard.remove(stale)
                print('removed  %s' % other)
        print('pak      %s  (%d files, %d bytes)'
              % (info['pak'], info['files'], info['size']))


if __name__ == '__main__':
    main()


# -- the reusable API Rift Lord builds on -------------------------------------

#: The donor the OLD path borrowed: a pure chain of seven volumes, so a route of up
#: to seven spans got the right topology WITHOUT the degree words ever being written.
#: Kept only so a generated graph can be A/B'd against the build that first flew.
CHAIN_DONOR = 'ScenarioDLC_02'


def flight_graph(route, anchors=(), donor=None, tmp=None, nvol=None,
                 pad_xy=None, pad_z=None):
    """A volumetric navgraph fitted to `route`, ready to install.

    Generated from nothing by `navvolgen`. Pass `donor` to use the old
    donor-editing path instead, which is worth doing only to compare against the
    build that first flew: it caps the route at the donor's volume count, forces a
    chain, and inherits that map's shape.

    The corridor geometry is the one the in-game work settled -- one box per span,
    padded wide enough for a hellbat that broke off a fight to get back on the graph,
    consecutive boxes overlapping rather than tiling, and the spawner and door inside
    the first box. What is new is that the volume count is free, the topology can
    branch, and the degree and incidence words are written CORRECTLY rather than
    inherited untouched.
    """
    if donor:
        tmp = tmp or tempfile.mkdtemp(prefix='flightgraph')
        dfiles = navpatch.fetch_map(navpatch.map_path(donor), tmp,
                                    exclude=our_paks())
        db = navpatch.nav_buffers(dfiles['.umap'], dfiles['.uexp'])
        if not db or not db[4]:
            raise SystemExit('%s ships no volumetric buffer to donate' % donor)
        buf = open(dfiles['.uexp'], 'rb').read()[db[3]:db[3] + db[4]]
        if len(route) - 1 > navvol.parse(buf)['nvolumes']:
            raise SystemExit('route has %d spans, donor %s has %d volumes'
                             % (len(route) - 1, donor,
                                navvol.parse(buf)['nvolumes']))
        buf, _boxes = fit_to_route(buf, [tuple(p) for p in route],
                                   touch_degrees=False, touch_portals=True,
                                   park_spares=True, anchors=tuple(anchors))
        return rebuild_tree(buf)

    kw = {}
    if pad_xy is not None:
        kw['pad_xy'] = pad_xy
    if pad_z is not None:
        kw['pad_z'] = pad_z
    boxes, adj = navvolgen.route_boxes(route, nvol=nvol, anchors=tuple(anchors), **kw)
    buf = navvolgen.build(boxes, adj)
    bad = [nm for nm, ok in navvolgen.check(buf, boxes) if not ok]
    if bad:
        raise SystemExit('generated flight graph failed its checks: %s'
                         % '; '.join(bad))
    return buf


def add_buffer(uasset_path, uexp_path, buf):
    """Install a volumetric buffer into a level package that has none.

    The export gains a `NavVolumesBufferSize` property right after
    `NavMeshBufferSize` -- where every shipped flight level puts it -- and the buffer
    goes on the end, after the surface graph. Returns a list of (check, ok).
    """
    pkg = Package(open(uasset_path, 'rb').read(), open(uexp_path, 'rb').read())
    before_meta = [bytes(e['meta']) for e in pkg.exports]
    found = nav_export_index(pkg)
    if not found:
        return [('a nav-data export with a buffer exists', False)]
    idx, mesh_size, props = found
    if 'NavVolumesBufferSize' in {nm for nm, _t, _s, _e in props}:
        return [('the level has no volumetric buffer yet', False)]

    data = bytearray(pkg.exports[idx]['data'])
    at = next(en for nm, _t, _s, en in props if nm == 'NavMeshBufferSize')
    data[at:at] = uint32_property(pkg, 'NavVolumesBufferSize', len(buf))
    data += buf
    pkg.exports[idx]['data'] = bytes(data)
    pkg.exports[idx]['size'] = len(data)
    uasset, uexp = pkg.write()
    open(uasset_path, 'wb').write(uasset)
    open(uexp_path, 'wb').write(uexp)

    #: Re-read with the ORDINARY reader, never the object just built.
    fresh = Package(uasset, uexp)
    meta_ok = True
    for was, now in zip(before_meta, [bytes(e['meta']) for e in fresh.exports]):
        a, b = bytearray(was), bytearray(now)
        for off in (28, 36):                      # SerialSize, SerialOffset
            a[off:off + 8] = b'\0' * 8
            b[off:off + 8] = b'\0' * 8
        if bytes(a) != bytes(b):
            meta_ok = False
    nb = navpatch.nav_buffers(uasset_path, uexp_path)
    got = open(uexp_path, 'rb').read()[nb[3]:nb[3] + nb[4]] if nb else b''
    return [('no export metadata changed except size/offset', meta_ok),
            ('the patched package re-reads', nb is not None),
            ('the volumetric buffer is found and intact', got == buf),
            ('its checksum verifies', bool(got) and navvol.verify(got)),
            ('the surface graph still verifies',
             bool(nb) and navvol.verify(
                 open(uexp_path, 'rb').read()[nb[1]:nb[1] + nb[2]]))]


def add_buffer_bytes(uasset, uexp, buf):
    """`add_buffer` on bytes rather than paths, for an emitter working in memory.

    Returns (uasset, uexp, [(check, ok)]).
    """
    pkg = Package(uasset, uexp)
    before_meta = [bytes(e['meta']) for e in pkg.exports]
    found = nav_export_index(pkg)
    if not found:
        return uasset, uexp, [('a nav-data export with a buffer exists', False)]
    idx, _mesh, props = found
    if 'NavVolumesBufferSize' in {nm for nm, _t, _s, _e in props}:
        return uasset, uexp, [('the level has no volumetric buffer yet', False)]
    data = bytearray(pkg.exports[idx]['data'])
    at = next(en for nm, _t, _s, en in props if nm == 'NavMeshBufferSize')
    data[at:at] = uint32_property(pkg, 'NavVolumesBufferSize', len(buf))
    data += buf
    pkg.exports[idx]['data'] = bytes(data)
    pkg.exports[idx]['size'] = len(data)
    out_a, out_x = pkg.write()

    fresh = Package(out_a, out_x)
    meta_ok = True
    for was, now in zip(before_meta, [bytes(e['meta']) for e in fresh.exports]):
        a, b = bytearray(was), bytearray(now)
        for off in (28, 36):
            a[off:off + 8] = b'\0' * 8
            b[off:off + 8] = b'\0' * 8
        if bytes(a) != bytes(b):
            meta_ok = False
    #: Located through the ordinary reader, not by slicing the end of the file.
    #: The nav export is NOT the last export -- tens of KB follow it -- so
    #: `out_x[-len(buf):]` is some other export's data and the check failed on a
    #: buffer that had been written perfectly well.
    import tempfile as _tf                            # noqa: PLC0415
    d = _tf.mkdtemp(prefix='volcheck')
    ua = os.path.join(d, 'c.umap')
    ux = os.path.join(d, 'c.uexp')
    with open(ua, 'wb') as fh:
        fh.write(out_a)
    with open(ux, 'wb') as fh:
        fh.write(out_x)
    nb = navpatch.nav_buffers(ua, ux)
    got = out_x[nb[3]:nb[3] + nb[4]] if nb and nb[4] else b''
    return out_a, out_x, [
        ('no export metadata changed except size/offset', meta_ok),
        ('the patched package re-reads', nb is not None),
        ('the volumetric buffer is found and intact', got == buf),
        ('its checksum verifies', bool(got) and navvol.verify(got)),
        ('the surface graph still verifies',
         bool(nb) and navvol.verify(out_x[nb[1]:nb[1] + nb[2]]))]


def volumetric_of(uasset, uexp):
    """The level's own volumetric buffer, or None if it has none.

    Read through the ORDINARY reader rather than by slicing the end of the export --
    the nav export is not the last one, and tens of KB follow it.
    """
    import tempfile as _tf                            # noqa: PLC0415
    d = _tf.mkdtemp(prefix='volread')
    ua = os.path.join(d, 'r.umap')
    ux = os.path.join(d, 'r.uexp')
    with open(ua, 'wb') as fh:
        fh.write(uasset)
    with open(ux, 'wb') as fh:
        fh.write(uexp)
    nb = navpatch.nav_buffers(ua, ux)
    if not nb or not nb[4]:
        return None
    return uexp[nb[3]:nb[3] + nb[4]]


def set_buffer_bytes(uasset, uexp, buf):
    """Install the volumetric buffer, REPLACING one if the level already has it.

    `add_buffer_bytes` refuses a level that already has a volumetric buffer, which is
    right for a one-shot injection and wrong for a save that runs repeatedly: it made
    a second flyer spawner fail, and it locked out every level that ships flight.

    The volumes buffer is the LAST thing in the export -- surface first, then volumes
    -- so replacing it is a tail splice plus the property's own value. Returns
    (uasset, uexp, [(check, ok)], action).
    """
    pkg = Package(uasset, uexp)
    before_meta = [bytes(e['meta']) for e in pkg.exports]
    found = nav_export_index(pkg)
    if not found:
        return uasset, uexp, [('a nav-data export with a buffer exists', False)], None
    idx, _mesh, props = found
    at = {nm: (st, en) for nm, _t, st, en in props}
    data = bytearray(pkg.exports[idx]['data'])

    if 'NavVolumesBufferSize' in at:
        _st, en = at['NavVolumesBufferSize']
        old = struct.unpack_from('<I', data, en - 4)[0]
        if old > len(data):
            return uasset, uexp, [('the stated volumetric size fits the export',
                                   False)], None
        data[len(data) - old:] = buf
        #: The property sits BEFORE the buffers, so the tail splice does not move it.
        struct.pack_into('<I', data, en - 4, len(buf))
        action = 'replaced'
    else:
        data[at['NavMeshBufferSize'][1]:at['NavMeshBufferSize'][1]] = \
            uint32_property(pkg, 'NavVolumesBufferSize', len(buf))
        data += buf
        action = 'added'

    pkg.exports[idx]['data'] = bytes(data)
    pkg.exports[idx]['size'] = len(data)
    out_a, out_x = pkg.write()

    fresh = Package(out_a, out_x)
    meta_ok = True
    for was, now in zip(before_meta, [bytes(e['meta']) for e in fresh.exports]):
        a, b = bytearray(was), bytearray(now)
        for off in (28, 36):                          # SerialSize, SerialOffset
            a[off:off + 8] = b'\0' * 8
            b[off:off + 8] = b'\0' * 8
        if bytes(a) != bytes(b):
            meta_ok = False

    got = volumetric_of(out_a, out_x)
    import tempfile as _tf                            # noqa: PLC0415
    d = _tf.mkdtemp(prefix='volcheck')
    ua = os.path.join(d, 'c.umap')
    ux = os.path.join(d, 'c.uexp')
    with open(ua, 'wb') as fh:
        fh.write(out_a)
    with open(ux, 'wb') as fh:
        fh.write(out_x)
    nb = navpatch.nav_buffers(ua, ux)
    return out_a, out_x, [
        ('no export metadata changed except size/offset', meta_ok),
        ('the patched package re-reads', nb is not None),
        ('the volumetric buffer is found and intact', got == buf),
        ('its checksum verifies', bool(got) and navvol.verify(got)),
        ('the surface graph still verifies',
         bool(nb) and navvol.verify(out_x[nb[1]:nb[1] + nb[2]]))], action


def install_flight(uasset, uexp, routes, pad_xy=None, pad_z=None, clear=None):
    """Generate the flight graph for `routes` and install it. The whole job.

    This is what a save calls. It keeps the level's own graph if it has one, puts
    every route in the single buffer the format allows, verifies the result against
    its own invariants AND by reading the package back, and reports what it did.

    Returns (uasset, uexp, rows, note). `rows` is [(check, ok)]; a build should refuse
    to ship if any is false. `clear` (level name, plan) fits the volumes to the
    level's free space (navvolclear).
    """
    if not routes:
        return uasset, uexp, [], 'no flyer route in the plan, so no flight graph'
    kw = {'clear': clear}
    if pad_xy is not None:
        kw['pad_xy'] = pad_xy
    if pad_z is not None:
        kw['pad_z'] = pad_z
    existing = volumetric_of(uasset, uexp)
    buf, info = navvolgen.for_routes(routes, existing=existing, **kw)
    rows = list(info['checks'])
    #: Overlap with the level's OWN volumes is a WARNING, never a failure.
    #:
    #: It was briefly a blocking check and that was wrong twice over. It is normal:
    #: an authored route through a map that ships flight will often cross airspace a
    #: shipped volume already covers, which is map design, not a defect -- blocking it
    #: refuses a perfectly good build. And there is no evidence it is harmful: what
    #: was measured is that overlapping CONSECUTIVE volumes behave better than tiled
    #: ones, and the doubling-back traced to parked spare volumes and misplaced
    #: portals, not to overlap as such. A false alarm in a tool people drive by hand
    #: costs more than this risk does, so it is reported and not enforced.
    out_a, out_x, more, action = set_buffer_bytes(uasset, uexp, buf)
    rows += more
    note = ('%s a %d byte flight graph: %d volumes (%d kept from the level, '
            '%s authored%s), %d portals'
            % (action or 'failed to install', len(buf), info['volumes'],
               info['kept'], '+'.join(str(n) for n in info['added']),
               '' if not info.get('regenerated')
               else ', %d regenerated' % info['regenerated'],
               info['portals']))
    if info['overlapping']:
        note += (' -- NOTE %d authored volume(s) share airspace with the shipped '
                 '%s; a flyer there can take either route, so move the route if '
                 'that matters'
                 % (len(info['overlapping']), info['overlapping'][:4]))
    if info.get('blocked'):
        note += (' -- NOTE the flyer route passes within %d uu of geometry at %d '
                 'stretch(es), first near %s; move those points into open air'
                 % (int(navvolgen_clearance()), len(info['blocked']),
                    tuple(round(v) for v in info['blocked'][0][0])))
    return out_a, out_x, rows, note


def navvolgen_clearance():
    from riftlord.build.navigation import navvolclear
    return navvolclear.CLEARANCE


def route_from_plan(plan, exit_at=None):
    """[(route points, anchors)] for every flyer spawner in a Rift Lord plan.

    Taken from the PLAN, because at build time the level being described does not
    exist yet. The chain is walked by each point's own branches rather than by the
    order they were placed, which is how a route with a fork is stored.

    `exit_at` is the location of the level's OWN exit point, and it is appended to
    any route that ends without a branch of its own. The emitter hands such a route
    to the level's exit rather than declaring a second one, so the flyer's last leg
    goes somewhere the plan never mentions -- and a corridor built only from the
    plan's points stops short of it. Here that leg was covered by the padding only by
    luck; a plan whose last point sits further out would put the hand-off outside the
    graph, which is the "stalls at the final point" failure exactly.
    """
    if plan.get('version', 1) >= 2:
        from riftlord.mapeditor import planactors
        return planactors.flyer_routes(plan, planactors.level_actors(plan['level']))
    out = []
    for sp in plan.get('spawners') or []:
        if (sp.get('kind') or 'flyer') != 'flyer':
            continue
        pts = {p['id']: p for p in sp.get('points') or []}
        if not pts:
            continue
        start = next((p for p in pts.values() if p.get('isStart')), None) \
            or (sp.get('points') or [None])[0]
        route, seen, cur = [], set(), start
        while cur is not None and cur['id'] not in seen:
            seen.add(cur['id'])
            route.append(tuple(float(x) for x in cur['at']))
            nxt = (cur.get('next') or [])
            cur = pts.get(nxt[0]['to']) if nxt and nxt[0].get('to') in pts else None
        anchors = [tuple(float(x) for x in sp['at'])]
        for d in plan.get('doors') or []:
            if d.get('id') == sp.get('door'):
                anchors.append(tuple(float(x) for x in d['at']))
        if exit_at is not None and cur is None:
            tail = tuple(float(x) for x in exit_at)
            if not route or route[-1] != tail:
                route.append(tail)
        if len(route) >= 2:
            out.append((route, anchors))
    return out
