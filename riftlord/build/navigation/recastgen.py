"""Walkable polygons from UE 4.27's own Recast, run offline on a level's final geometry.

An alternative to navregen's own heightfield-to-polygon pipeline. Recast's polygon mesh
shares exact vertices between neighbours, so shared edges meet in 3D and carry no
T-junctions by construction: the seam faults navregen welds and mends after the fact.

The binary is built by native/recastgen/build.py: from the engine's Recast sources
(`recastgen` in ~/.cache/rift-lord/recastgen) or from recastnavigation (`--upstream`:
`recastgen-upstream`, which the frozen app carries in native/). Geometry and agents are
navregen's.

    python3 recastgen/recastgen.py Scenario_02 [plan.json]
"""

import os
import struct
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

from riftlord.build.navigation import navregen  # noqa: E402
from riftlord.build.navigation import navsurfgen  # noqa: E402

#: The Recast build: UE 4.27's own ('ue') or open-source recastnavigation ('upstream').
SOURCE = 'ue'
BINARIES = {'ue': os.path.expanduser('~/.cache/rift-lord/recastgen/recastgen'),
            'upstream': os.path.expanduser('~/.cache/rift-lord/recastgen/recastgen-upstream')}
#: When no recastgen binary can be found.
UNAVAILABLE = 'walkable-graph regeneration is unavailable in this build: no recastgen %s (%s)'


def binary(source=None):
    """The recastgen build for `source` (default SOURCE): the frozen app's own copy of
    recastgen-upstream, else the ~/.cache build. SystemExit with UNAVAILABLE when there is
    none."""
    source = source or SOURCE
    base = getattr(sys, '_MEIPASS', None)
    if base and source == 'upstream':
        path = os.path.join(base, 'native', 'recastgen-upstream' +
                            ('.exe' if os.name == 'nt' else ''))
        if os.path.isfile(path):
            return path
    path = BINARIES[source]
    if not os.path.isfile(path):
        raise SystemExit(UNAVAILABLE % (source, path))
    return path


def available():
    """None when the recastgen build navregen.GENERATOR uses can run here, else the
    UNAVAILABLE message."""
    try:
        binary('upstream' if navregen.GENERATOR == 'recast-upstream' else 'ue')
    except SystemExit as exc:
        return str(exc)
    return None


#: Where the binary's input and output files go (disk, not the RAM-backed /tmp).
WORK_DIR = '/mnt/2tb_pro_a/tmp' if os.path.isdir('/mnt/2tb_pro_a/tmp') else None

#: Recast parameters, in uu unless noted. Cell sizes are navregen's.
CELL = navregen.CELL
CELL_HEIGHT = navregen.CELL_HEIGHT
#: Steepest walkable triangle (degrees). NavPower's own builder default (OMD2 3.2.3's fill:
#: maxWalkableSlope 60), not the ini's Recast 45: shipped graphs carry many polygons at 45-60
#: (Front Lawn 42, Scenario_12 60), e.g. up the small stones by both Front Lawn spawners.
MAX_SLOPE_DEG = 60.0
#: Contour simplification error, in cells (navregen's MAX_ERROR / CELL).
MAX_ERROR_CELLS = navregen.MAX_ERROR / navregen.CELL
MAX_EDGE = navregen.MAX_EDGE
#: Regions smaller than this are dropped, in cells (navregen's MIN_ISLAND_AREA).
MIN_REGION_CELLS = navregen.MIN_ISLAND_AREA / (navregen.CELL * navregen.CELL)
#: Regions smaller than this merge into a neighbour, in cells.
MERGE_REGION_CELLS = 400
#: Most vertices per polygon (shipped maximum 12; records hold 15).
VERTS_PER_POLY = 12
#: Detail mesh: height sample spacing and the error it may leave (uu). Recast's polygons
#: are flat; over rolling ground one sat 136 uu above the terrain (Front Lawn), so a
#: polygon whose detail surface strays from its plane by more than FLAT_TOL is replaced
#: by its detail triangles, merged back into flat convex pieces.
DETAIL_DIST = 300.0
DETAIL_ERROR = 8.0
FLAT_TOL = 32.0
#: Largest distance of a corner from its polygon's plane before the polygon is fanned (uu).
PLANAR_EPS = navregen.PLANAR_EPS
#: Rasterise a mesh's simple collision (omd3-hulls.json convex parts) where it has one,
#: its render triangles otherwise: for every piece (True), only for OBSTACLE_ONLY
#: components ('obstacles'), only for small pieces ('props', HULL_PROP_SIZE), or never (False).
USE_HULLS = False
#: With USE_HULLS 'props': only pieces whose drawn footprint is at most this wide (uu) use their
#: hull. Hulls for every piece buried Front Lawn's whole fort floor (a large piece's convex hull
#: spans space its mesh does not), while the clutter by its spawners needs them.
HULL_PROP_SIZE = 1000.0
#: Meshes whose name has one of these words are ramps (stairs): their floor is a Recast area of
#: its own (walk 2, recastgen.cpp), so each flight gets polygons of its own, and its spans
#: skip Recast's steep-slope test (recastgen.cpp filterLedges: a tread one cell deep between
#: two risers). Empty: off.
RAMP_WORDS = ('stairs',)
#: ...unless the name also has one of these (railings, side walls and caps beside a flight).
RAMP_SKIP_WORDS = ('railing', 'rails', 'wall', 'side', 'cap')
#: A ramp polygon's area in the binary's output (RC_WALKABLE_AREA; plain floor is 1).
RAMP_AREA = 63
#: FLAT_TOL for a ramp's polygon (uu): the agent's step, the tallest riser a flight can have.
RAMP_FLAT_TOL = 48.0
#: Rasterise a ramp's simple collision (a wedge with a 10 uu lip) instead of its render steps.
#: Off: Recast's ledge filter cut Beset's Stairs_4x_2_SB off the floor at its foot.
RAMP_HULLS = False
#: Treat navregen.OBSTACLE_ONLY components as never floor.
OBSTACLE_ONLY = True
#: Added to the agent's step before Recast rounds it down to whole cells (uu): with 0,
#: a 48 uu step became 45 and a 48 uu rise blocked a lane NavPower walks (Front Lawn).
CLIMB_PAD = 0.0
#: OBSTACLE_ONLY (decorative) triangles within this distance of an authored route leg are
#: left out, so decoration never cuts a lane: on Front Lawn a stone's edge 48 uu proud of
#: the ground cut both lanes, which shipped NavPower walks straight over (uu; 0: off). Off
#: since R7: it also opened floor inside tall stones (R6 jammed there); DECOR_MODE replaces it.
ROUTE_CLEAR = 0.0
#: Carve decor (OBSTACLE_ONLY) after erosion instead of rasterising it with the floor (see
#: recastgen.cpp): NavPower keeps its own clearance from decor and never puts floor on it.
DECOR_MODE = True
#: Decor blocks floor where it rises more than this above it (uu): shipped floor stops where
#: Front Lawn's stones leave the ground (R6 put floor on stone 98-224 uu proud and jammed).
DECOR_PROUD = CELL_HEIGHT
#: Clearance from decor per agent radius (uu), measured on Front Lawn's right-lane stones:
#: shipped floor starts -24..+4 uu (42 agent) and 26..64 uu (80 agent) from the stone.
DECOR_CLEAR = {42.0: 0.0, 80.0: 50.0}
#: Share of the agent radius Recast erodes walkable floor by (1: the whole radius).
ERODE_SCALE = 1.0
#: A Recast polygon edge with no neighbour.
NULL_IDX = 0xFFFF
#: navregen.AGENTS in a designer's words.
AGENT_WORDS = ('ordinary', 'large')
#: Clean the polygons before edge ids (navfan.clean); False writes them as Recast gives them.
FAN_CLEAN = True


def to_recast(p):
    """UE's Unreal2RecastPoint: (-X, Z, -Y)."""
    return np.c_[-p[:, 0], p[:, 2], -p[:, 1]]


def _near_routes(points, segs, reach):
    """Mask of points (n x 3) within `reach` of any route segment, in xy."""
    near = np.zeros(len(points), bool)
    p = points[:, :2]
    for a, b in segs:
        ab = b - a
        t = np.clip(((p - a) @ ab) / max(float(ab @ ab), 1e-9), 0.0, 1.0)
        near |= np.linalg.norm(p - (a + t[:, None] * ab), axis=1) <= reach
    return near


def is_ramp(name):
    """True when a mesh is a ramp by its name (RAMP_WORDS)."""
    words = set(navregen._words(name))
    return bool(words & set(RAMP_WORDS)) and not words & set(RAMP_SKIP_WORDS)


def placed_winding(tri, M):
    """A mesh's triangles as wound once placed by `M` (row-vector 4x4): reversed under a
    mirror (negative determinant), so a mirrored piece's floors still face up for Recast."""
    return tri[:, [0, 2, 1]] if np.linalg.det(np.asarray(M)[:3, :3]) < 0 else tri


def inside_region(w, tri, lo, hi):
    """(vertices, triangles) of one placed mesh less the triangles wholly outside the box
    `lo`..`hi`, with the vertices only those leave out dropped; the mesh unchanged when none
    is outside (docs/MAP-EDITOR.md, What the walkable-graph rebuild reads)."""
    t = w[tri]
    out = np.any(t.max(axis=1) < lo, axis=1) | np.any(t.min(axis=1) > hi, axis=1)
    if not out.any():
        return w, tri
    tri = tri[~out]
    used = np.unique(tri)
    remap = np.full(len(w), -1, np.int64)
    remap[used] = np.arange(len(used))
    return w[used], remap[tri]


def geometry(level_name, plan):
    """(vertices, triangles, walkable per triangle) in UE space: navregen's final pieces
    and blocking volumes, inside its gathering region. Obstacle-only components
    (navregen.OBSTACLE_ONLY) block but are never floor."""
    lo, hi = navregen.gather_region(level_name, plan)
    verts, tris, walk, n = [], [], [], 0
    segs = []
    if ROUTE_CLEAR:
        legs, _exits = navregen.route_legs(level_name, plan)
        segs = [(np.asarray(a, np.float64)[:2], np.asarray(b, np.float64)[:2]) for _l, a, b in legs]
    hulls = navregen._hulls() if (USE_HULLS or RAMP_HULLS) else {}
    for name, M, walkable in navregen.final_pieces(level_name, plan, walk=True):
        obstacle = not walkable
        walkable = walkable or not OBSTACLE_ONLY
        ramp = is_ramp(name)
        ramp_hull = ramp and RAMP_HULLS
        hull = hulls.get(name) if (ramp_hull or USE_HULLS is True
                                   or (USE_HULLS == 'obstacles' and obstacle)
                                   or (USE_HULLS == 'props' and not obstacle)) else None
        if hull and USE_HULLS == 'props' and not ramp_hull:
            got = navregen._mesh(name)
            if got is None:
                hull = None
            else:
                foot = (np.c_[got[0], np.ones(len(got[0]))] @ M)[:, :2]
                if np.ptp(foot, axis=0).max() > HULL_PROP_SIZE:
                    hull = None
        if hull and hull.get('parts') and name in _HULL_MESHES:
            got = _HULL_MESHES[name]
        elif hull and hull.get('parts'):
            v = np.vstack([np.asarray(part['verts'], np.float64) for part in hull['parts']])
            base, tri = 0, []
            for part in hull['parts']:
                t = np.asarray(part['tris'], np.int64).reshape(-1, 3) if part.get('tris') \
                    else _hull_tris(np.asarray(part['verts'], np.float64))
                tri.append(t + base)
                base += len(part['verts'])
            got = _HULL_MESHES[name] = (v, np.vstack(tri))
        else:
            got = navregen._mesh(name)
        if got is None:
            continue
        v, tri = got
        w = (np.c_[v, np.ones(len(v))] @ M)[:, :3]
        if np.any(w.max(0) < lo) or np.any(w.min(0) > hi):
            continue
        w, tri = inside_region(w, tri, lo, hi)
        if not len(tri):
            continue
        tri = placed_winding(tri, M)
        if obstacle and segs:
            centre = w[tri].mean(axis=1)
            tri = tri[~_near_routes(centre, segs, ROUTE_CLEAR)]
            if not len(tri):
                continue
        verts.append(w)
        tris.append(tri + n)
        #: 2: a ramp's floor, in an area of its own (recastgen.cpp)
        walk.append(np.full(len(tri), (2 if ramp else 1) if walkable else 0, np.uint8))
        n += len(w)
    for tri in navregen.blocking_volumes(level_name, plan) + \
            navregen.collision_boxes(level_name, plan):
        t, idx = inside_region(np.asarray(tri, dtype=np.float64).reshape(-1, 3),
                               np.arange(len(tri) * 3).reshape(-1, 3), lo, hi)
        if not len(idx):
            continue
        verts.append(t)
        tris.append(idx + n)
        walk.append(np.ones(len(idx), np.uint8))
        n += len(t)
    if not verts:
        return np.zeros((0, 3)), np.zeros((0, 3), np.int32), np.zeros(0, np.uint8)
    return np.vstack(verts), np.vstack(tris).astype(np.int32), np.concatenate(walk)


#: Triangulated hull meshes by mesh name.
_HULL_MESHES = {}
#: A hull face keeps every other vertex within this distance on its inner side (uu).
HULL_EPS = 0.01


def _hull_tris(v):
    """Triangles covering the convex hull of a convex part given as vertices only (most of
    omd3-hulls.json: 2,187 of 2,664 parts carry no triangles, so they used to add nothing):
    every vertex triple whose plane has all vertices on one side gives a face plane, and each
    plane's vertices are fanned in angular order."""
    import itertools
    n = len(v)
    if n < 3:
        return np.zeros((0, 3), np.int64)
    t = np.array(list(itertools.combinations(range(n), 3)), np.int64)
    nrm = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    size = np.linalg.norm(nrm, axis=1)
    keep = size > 1e-6
    t, nrm, size = t[keep], nrm[keep], size[keep]
    out = []
    for lo in range(0, len(t), 20000):
        tt, nn, ss = t[lo:lo + 20000], nrm[lo:lo + 20000], size[lo:lo + 20000]
        d = ((v[None, :, :] - v[tt[:, 0]][:, None, :]) * nn[:, None, :]).sum(2) / ss[:, None]
        face = (d <= HULL_EPS).all(1) | (d >= -HULL_EPS).all(1)
        out.append(tt[face])
    if not out or not len(np.vstack(out)):
        return np.zeros((0, 3), np.int64)
    #: one fan per face plane: the plane's vertices in angular order round their centre
    faces = np.vstack(out)
    planes = {}
    for f in faces:
        nn = np.cross(v[f[1]] - v[f[0]], v[f[2]] - v[f[0]])
        nn = nn / np.linalg.norm(nn)
        if nn[np.argmax(np.abs(nn))] < 0:
            nn = -nn
        key = tuple(np.round(np.r_[nn, nn @ v[f[0]]], 2))
        planes.setdefault(key, set()).update(f.tolist())
    tris = []
    centre = v.mean(0)
    for key, idx in planes.items():
        idx = sorted(idx)
        if len(idx) < 3:
            continue
        nn = np.array(key[:3])
        c = v[idx].mean(0)
        a = v[idx[0]] - c
        a = a - (a @ nn) * nn
        if np.linalg.norm(a) < 1e-9:
            a = v[idx[1]] - c
        a /= np.linalg.norm(a)
        b = np.cross(nn, a)
        ang = np.arctan2((v[idx] - c) @ b, (v[idx] - c) @ a)
        ring = [idx[k] for k in np.argsort(ang)]
        #: the ring runs counter-clockwise round nn; wound as UE render meshes are (clockwise
        #: seen from outside), which to_recast's mirror turns into Recast's walkable side up.
        #: Tested on nn, not on the ring's first three corners, which may be collinear.
        if nn @ (c - centre) > 0:
            ring = ring[::-1]
        tris += [[ring[0], ring[k], ring[k + 1]] for k in range(1, len(ring) - 1)]
    return np.asarray(tris, np.int64).reshape(-1, 3)


def run(verts, tris, walk, agent):
    """Recast's polygon mesh for one agent (radius, step, height):
    (polygons as UE-space corner lists, counter-clockwise from above)."""
    radius, step, height = agent
    prm = [CELL, CELL_HEIGHT, MAX_SLOPE_DEG, height, step + CLIMB_PAD, radius * ERODE_SCALE, MAX_ERROR_CELLS,
           MAX_EDGE, MIN_REGION_CELLS, MERGE_REGION_CELLS, DETAIL_DIST, DETAIL_ERROR,
           1.0 if DECOR_MODE else 0.0, DECOR_PROUD, DECOR_CLEAR.get(float(radius), 0.0)]
    rv = to_recast(verts).astype(np.float32)
    exe = binary()
    with tempfile.TemporaryDirectory(dir=WORK_DIR) as d:
        src, dst = os.path.join(d, 'in.bin'), os.path.join(d, 'out.bin')
        with open(src, 'wb') as fh:
            fh.write(struct.pack('<ii', len(rv), len(tris)))
            fh.write(struct.pack('<%df' % len(prm), *prm))
            fh.write(struct.pack('<i', VERTS_PER_POLY))
            fh.write(rv.tobytes())
            fh.write(tris.astype('<i4').tobytes())
            fh.write(walk.astype(np.uint8).tobytes())
        done = subprocess.run([exe, src, dst], capture_output=True, text=True)
        if done.returncode:
            raise RuntimeError('recastgen failed: %s'
                               % (done.stderr.strip() or 'exit code %d' % done.returncode))
        data = open(dst, 'rb').read()
    nv, npoly, nvp = struct.unpack_from('<iii', data, 0)
    bmin = struct.unpack_from('<3f', data, 12)
    cs, ch = struct.unpack_from('<ff', data, 24)
    o = 32
    cv = np.frombuffer(data, '<u2', nv * 3, o).reshape(-1, 3).astype(np.float64)
    o += nv * 6
    pp = np.frombuffer(data, '<u2', npoly * nvp * 2, o).reshape(-1, nvp * 2)
    o += npoly * nvp * 4 + npoly * 2
    nmesh, ndv, ndt = struct.unpack_from('<iii', data, o)
    o += 12
    meshes = np.frombuffer(data, '<u4', nmesh * 4, o).reshape(-1, 4)
    o += nmesh * 16
    dv = np.frombuffer(data, '<f4', ndv * 3, o).reshape(-1, 3).astype(np.float64)
    o += ndv * 12
    dt = np.frombuffer(data, 'u1', ndt * 4, o).reshape(-1, 4)
    o += ndt * 4
    #: polygon areas, when the binary writes them (RAMP_AREA: a ramp's polygon)
    areas = np.frombuffer(data, 'u1', npoly, o) if len(data) >= o + npoly else None
    due = np.c_[-dv[:, 0], -dv[:, 2], dv[:, 1]]
    polys = []
    for i, row in enumerate(pp):
        n = sum(1 for k in row[:nvp] if k != NULL_IDX)
        vb, vc, tb, tc = (int(x) for x in meshes[i])
        pts = [tuple(float(c) for c in due[vb + k]) for k in range(vc)]
        tris = [[int(t) for t in dt[tb + k][:3]] for k in range(tc)]
        ramp = areas is not None and int(areas[i]) == RAMP_AREA
        polys.extend(_flat_pieces(pts, n, tris, RAMP_FLAT_TOL if ramp else FLAT_TOL))
    return polys


def _deviation(poly, pts):
    """Largest vertical distance of `pts` from the plane through `poly`."""
    p = np.asarray(poly)
    n = np.zeros(3)
    for k in range(1, len(p) - 1):
        n += np.cross(p[k] - p[0], p[k + 1] - p[0])
    if abs(n[2]) < 1e-9:
        return float('inf')
    q = np.asarray(pts)
    z = p[0][2] - (n[0] * (q[:, 0] - p[0][0]) + n[1] * (q[:, 1] - p[0][1])) / n[2]
    return float(np.max(np.abs(q[:, 2] - z)))


def _ccw(poly):
    return poly if _area_xy(poly) >= 0 else poly[::-1]


def _flat_pieces(pts, n, tris, tol=FLAT_TOL):
    """One Recast polygon as flat convex pieces: its own corners (detail heights) when its
    detail surface lies within `tol` of their plane, else its detail triangles merged."""
    corners = [pts[k] for k in range(n)]
    if _deviation(corners, pts) <= tol:
        return [_ccw(corners)]
    pieces = [list(t) for t in tris]
    merged = True
    while merged:
        merged = False
        for a in range(len(pieces)):
            for b in range(a + 1, len(pieces)):
                got = _join(pieces[a], pieces[b], pts)
                if got:
                    pieces[a] = got
                    del pieces[b]
                    merged = True
                    break
            if merged:
                break
    return [_ccw([pts[k] for k in piece]) for piece in pieces]


def _join(pa, pb, pts):
    """pa and pb (vertex index loops) joined across one shared edge, if the result is
    convex, flat within PLANAR_EPS and at most VERTS_PER_POLY corners; else None."""
    for i in range(len(pa)):
        u, v = pa[i], pa[(i + 1) % len(pa)]
        for j in range(len(pb)):
            if pb[j] == v and pb[(j + 1) % len(pb)] == u:
                loop = pa[:i + 1] + [pb[(j + 2 + k) % len(pb)] for k in range(len(pb) - 2)] \
                    + pa[i + 1:]
                if len(loop) > VERTS_PER_POLY or len(set(loop)) != len(loop):
                    return None
                poly = [pts[k] for k in loop]
                if not _convex_xy(poly) or _deviation(poly, poly) > PLANAR_EPS:
                    return None
                return loop
    return None


def _convex_xy(poly):
    sign = 0
    m = len(poly)
    for k in range(m):
        a, b, c = poly[k], poly[(k + 1) % m], poly[(k + 2) % m]
        cr = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if abs(cr) < 1e-6:
            continue
        if sign == 0:
            sign = 1 if cr > 0 else -1
        elif (cr > 0) != (sign > 0):
            return False
    return True


def _area_xy(poly):
    return 0.5 * sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, poly[1:] + poly[:1]))


def _planar(poly):
    p = np.asarray(poly)
    if len(p) <= 3:
        return True
    n = np.cross(p[1] - p[0], p[2] - p[0])
    for k in range(2, len(p) - 1):
        n = n + np.cross(p[k] - p[0], p[k + 1] - p[0])
    norm = np.linalg.norm(n)
    if norm < 1e-9:
        return False
    return bool(np.all(np.abs((p - p[0]) @ (n / norm)) <= PLANAR_EPS))


def _slope_deg(tri):
    n = np.cross(np.subtract(tri[1], tri[0]), np.subtract(tri[2], tri[0]))
    size = np.linalg.norm(n)
    return 90.0 if size < 1e-9 else float(np.degrees(np.arccos(min(1.0, abs(n[2]) / size))))


def _fan(poly):
    """A convex polygon as a fan of triangles from the corner whose steepest triangle is
    least steep."""
    m = len(poly)
    best = None
    for a in range(m):
        tris = [[poly[a], poly[(a + k) % m], poly[(a + k + 1) % m]] for k in range(1, m - 1)]
        worst = max(_slope_deg(t) for t in tris)
        if best is None or worst < best[0]:
            best = (worst, tris)
    return best[1]


def planar(polys):
    """Polygons with every non-planar one fanned into triangles (`_fan`). The outline is
    unchanged, so edges shared with neighbours stay exact."""
    out = []
    for poly in polys:
        if _planar(poly):
            out.append(poly)
        else:
            out.extend(_fan(poly))
    return out


#: Floor a ground route walks over stays inside a NavPowerExclusionVolume when the floor
#: outside the volumes does not join that route leg. The volumes describe the shipped play
#: area (Scenario_Ice_02's NavPowerExclusionVolume2 takes every floor above z 900), and a
#: plan's route is the designer saying where enemies walk, as with the blocking volumes a
#: route crosses (riftlord/mapeditor/volumes.py). Only the polygons on the leg's walk come
#: back: ignoring the volumes outright gave Repaired Mine 815 polygons against 324.
ROUTES_CROSS_EXCLUSIONS = True


def outside_exclusions(polys, level_name, legs=(), step=None):
    """Polygons whose centre is outside every NavPowerExclusionVolume of every package of
    the level (navregen.exclusion_volumes), plus, with ROUTES_CROSS_EXCLUSIONS, those on
    the walk of each route leg in `legs` ([(label, a, b)], navregen.route_legs) that the
    floor outside the volumes does not join. `step`: the agent's step, for the edge ids the
    walk follows."""
    vols = navregen.exclusion_volumes(level_name)
    if not vols or not polys:
        return polys
    c = np.array([np.mean(p, axis=0) for p in polys]) + np.array([0.0, 0.0, 1.0])
    keep = np.ones(len(polys), bool)
    for v in vols:
        keep &= ~navregen.inside_volume(v, c)
    if ROUTES_CROSS_EXCLUSIONS and legs and not keep.all():
        keep |= route_walks(polys, keep, legs, step)
    return [p for p, k in zip(polys, keep) if k]


def route_walks(polys, keep, legs, step=None):
    """Mask of the polygons not in `keep` that a route leg walks over, for each leg the
    polygons in `keep` do not join."""
    kw = {} if step is None else {'step': step}
    ids = navsurfgen.allocate_edge_ids(polys, tol=CELL * 0.25, **kw)
    inside = [i for i in range(len(polys)) if keep[i]]
    walk_in = navregen.walker([polys[i] for i in inside], [ids[i] for i in inside])
    walk_all = navregen.walker(polys, ids)
    out = np.zeros(len(polys), bool)
    for _label, a, b in legs:
        if walk_in(a, b) is not None:
            continue
        for i in walk_all.path(a, b) or ():
            out[i] = not keep[i]
    return out


def graphs_for(level_name, plan=None, log=print):
    """[(polys, ids, tags, flags)] per agent, as navregen.graphs_for returns."""
    plan = plan or {}
    if navregen.region(level_name) is None:
        raise navregen.NoArea('%s has no NavMeshBoundsVolume' % level_name)
    verts, tris, walk = geometry(level_name, plan)
    if not len(tris):
        raise ValueError('no geometry inside the gathering region')
    log('  %d triangles' % len(tris))
    legs, _exits = navregen.route_legs(level_name, plan)
    anchors = [p for _l, a, b in legs for p in (a, b)]
    anchors += [at for _l, at in navregen.gameplay_points(level_name, plan)]
    out = []
    for g, agent in enumerate(navregen.AGENTS):
        raw = run(verts, tris, walk, agent)
        if not raw:
            raise ValueError('Recast found no floor for agent %r' % (agent,))
        polys = planar(outside_exclusions(raw, level_name, legs, agent[1]))
        #: detail edge samples on a split polygon's side go into its unsplit neighbour's edge
        polys = navregen._mended(polys)
        #: one shared edge per pair of neighbours, no degenerate polygons: NavPower's
        #: vertex-fan walk never ends otherwise (riftlord.build.navigation.navfan)
        if FAN_CLEAN:
            from riftlord.build.navigation import navfan
            polys, notes = navfan.clean(polys)
            for line in notes:
                log('  agent r%.0f: %s' % (agent[0], line))
        ids = navsurfgen.allocate_edge_ids(polys, tol=CELL * 0.25, step=agent[1])
        tags, flags = [None] * len(polys), [navregen.DEFAULT_FLAGS] * len(polys)
        if not navregen.KEEP_UNANCHORED:
            chosen = navregen.anchored(polys, ids, anchors)
            polys, ids, tags, flags = [[x[i] for i in chosen]
                                       for x in (polys, ids, tags, flags)]
        if not polys:
            raise navregen.Cause(
                '%s stand%s on no floor the %s enemies can walk'
                % (navregen.places(level_name, plan),
                   's' if len(navregen.gameplay_points(level_name, plan)) == 1 else '',
                   AGENT_WORDS[g]),
                'move them onto the floor, or put floor under them',
                '%d anchors, none on any of %d polygons' % (len(anchors), len(raw)))
        polys, ids, tags, flags = navregen.wall_last(polys, ids, tags, flags)
        log('  agent r%.0f: %d polygons' % (agent[0], len(polys)))
        out.append((polys, ids, tags, flags))
    return out


def buffer_for(level_name, plan=None, log=print):
    got = graphs_for(level_name, plan, log)
    items = [(polys, ids, flags, None, None, None, tags) for polys, ids, tags, flags in got]
    return navsurfgen.buffer_per_agent(items, navsurfgen.bbox_of(got[0][0]))


if __name__ == '__main__':
    import json
    level = sys.argv[1]
    plan = json.load(open(sys.argv[2])) if len(sys.argv) > 2 else None
    buf = buffer_for(level, plan)
    print('buffer %d bytes, checks: %s' % (len(buf), '; '.join(navregen.check(buf)[:4]) or 'ok'))
