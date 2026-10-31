# Generated from OMD2-Converter/thirdparty/fabconvert/analyse.py by thirdparty/fabconvert/port_riftlord.py; edit the source.
"""Gameplay analysis of a converted mesh, in mesh-local space after scaling: role, bounds,
pivot, suggested trap grids, opening clearance, stairs, warnings.

Trap-grid facts this follows (thirdparty/PLAYABLE-AREA.md, measured; rounds gt1/gt1b/gt1c,
proven in game 2026-10-13/14):
* a grid box is a slab with local X along the surface normal, half-thickness 10, and Y / Z
  half-extents of whole 300 uu cells (A x 150, B x 150); its mid-plane lies on the surface;
* a separate BP_OMDTrapGrid_Blank_C actor at scale 1.0 makes any blocking surface trappable;
* TrapPlacementTypes bits: Floor 1, Wall 2, Ceiling 4, war machine 8 / 16 / 32; shipped floors
  use 9, walls 2, ceilings 4; sloped floors carry TrapWarMachineTypes 255.
"""

import math

import numpy as np

#: Roles whose surfaces are uneven by design (wider flatness allowance).
ORGANIC_ROLES = ('cliff', 'rock', 'foliage')
#: Trap cell edge and box half-thickness (uu), measured on every shipped grid.
CELL = 300.0
THIN = 10.0
#: Grids snap to half cells: OMD3 grids are laid in 150 uu steps (kit GridScale 150; RampMesh_05x3
#: is half a cell wide) and half-width traps (Window of Butterflies, Saw Blade Launcher: 150 x 300)
#: fit a half-cell strip (user, fc3: Docks door sides, City of Brass 405 uu wall).
UNIT = CELL / 2
#: A box spans at least this many half cells, and one side at least a whole cell (a half trap).
MIN_UNITS = 2
MIN_LONG_UNITS = 2
#: Ceilings take half-cell squares: the 150 x 150 ceiling traps (Auto-Ballista) fit one (user,
#: Docks showcase 2026-10-15).
MIN_UNITS_CEILING = 1
#: In-plane slack (uu) on each half-extent. 0: the one-cell grids that took only half traps were
#: offset by their actor's origin, not too tight (round fc3, see LIBRARY-FORMAT.md "Spawning").
EXTENT_PAD = 0.0
#: Placement bits.
FLOOR_BITS, WALL_BITS, CEILING_BITS = 9, 2, 4
SLOPE_WARMACHINE = 255
#: A face is flat (floor / ceiling) or vertical (wall) within this many degrees.
FLAT_TOL_DEG = 5.0
#: Steepest face still offered as a tilted floor; steeper up to SLOPE_WALL_MAX_DEG becomes a
#: tilted wall box. OMD3's agent slope limit is 45 (DefaultEngine.ini).
SLOPE_FLOOR_MAX_DEG = 45.0
SLOPE_WALL_MAX_DEG = 80.0
#: Coverage raster step (uu) and the share of a cell that must be surface.
SAMPLE = 25.0
CELL_COVER_MIN = 0.9
#: Origin offsets tried when laying the 300 uu lattice on a plane (uu).
OFFSET_STEP = 50.0
#: Rectangles taken per plane, most first.
MAX_BOXES_PER_PLANE = 12
#: Character envelopes (thirdparty/SCALE.md, measured): hero 195 tall / 84 wide; the largest
#: walking enemy (ogre, troll) capsule 360 tall / 80 radius.
HERO_HEIGHT, HERO_WIDTH = 195.0, 84.0
OGRE_HEIGHT, OGRE_WIDTH = 360.0, 160.0
#: Opening raster (uu) and the step a doorway sill may have (agent step, DefaultEngine.ini).
OPENING_RES = 8.0
DOOR_STEP_MAX = 48.0
#: Stairs: tread heightmap step, level merge, slope warning (OMD3 agent slope 45).
STAIR_RES = 8.0
STAIR_MERGE = 8.0
STAIR_MAX_DEG = 45.0
#: Warnings.
TRIS_WARN = 50000
SIZE_WARN = 4000.0
#: Role vocabulary of the catalogue, and the survey's role -> it (report.py ROLE_RULES names).
ROLES = ('floor', 'wall', 'column', 'ceiling', 'roof', 'stairs', 'arch', 'door', 'cliff', 'rock', 'foliage', 'prop')


def role_of(name, path, survey_role, ext):
    n = name.lower()
    if 'ceiling' in n or 'cieling' in n:
        return 'ceiling'
    if survey_role == 'door_window_arch':
        if 'door' in n and 'frame' not in n and 'way' not in n:
            return 'door'
        if 'window' in n:
            return 'wall'
        return 'arch'
    if survey_role == 'cliff_rock':
        return 'cliff' if 'cliff' in n else 'rock'
    if survey_role in ('foliage', 'floor', 'wall', 'column', 'roof', 'stairs'):
        return survey_role
    if survey_role == 'terrain':
        return 'floor' if ext[2] < 0.25 * max(ext[0], ext[1]) else 'rock'
    if survey_role == 'structure':
        lo = sorted(ext[:2])
        if ext[2] >= 200 and lo[0] < 0.25 * lo[1]:
            return 'wall'
        if ext[2] < 0.15 * lo[1]:
            return 'floor'
        return 'prop'
    return 'prop'


# -- rotations (UE FRotationMatrix / FMatrix::Rotator) ------------------------------------------------

def rotator_axes(pitch, yaw, roll):
    p, y, r = (math.radians(a) for a in (pitch, yaw, roll))
    sp, cp, sy, cy, sr, cr = math.sin(p), math.cos(p), math.sin(y), math.cos(y), math.sin(r), math.cos(r)
    X = np.array([cp * cy, cp * sy, sp])
    Y = np.array([sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp])
    Z = np.array([-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp])
    return X, Y, Z


def axes_rotator(X, Y, Z):
    pitch = math.degrees(math.atan2(X[2], math.hypot(X[0], X[1])))
    yaw = math.degrees(math.atan2(X[1], X[0]))
    _x, sy, _z = rotator_axes(pitch, yaw, 0.0)
    roll = math.degrees(math.atan2(float(np.dot(Z, sy)), float(np.dot(Y, sy))))
    return {'pitch': round(pitch, 4), 'yaw': round(yaw, 4), 'roll': round(roll, 4)}


# -- geometry helpers ---------------------------------------------------------------------------------

def faces(P, T):
    a, b, c = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
    cr = np.cross(c - a, b - a)                    # UE front face
    area = np.linalg.norm(cr, axis=1) / 2
    n = cr / np.maximum(2 * area, 1e-12)[:, None]
    return a, b, c, n, area


def raster_cover(tri2d, lo, shape, step):
    """Boolean grid (shape) of sample centres lo + (i+0.5)*step covered by any 2D triangle."""
    cov = np.zeros(shape, bool)
    for t in tri2d:
        mn, mx = t.min(0), t.max(0)
        i0 = max(int((mn[0] - lo[0]) / step - 0.5), 0)
        i1 = min(int((mx[0] - lo[0]) / step + 0.5) + 1, shape[0])
        j0 = max(int((mn[1] - lo[1]) / step - 0.5), 0)
        j1 = min(int((mx[1] - lo[1]) / step + 0.5) + 1, shape[1])
        if i1 <= i0 or j1 <= j0:
            continue
        xs = lo[0] + (np.arange(i0, i1) + 0.5) * step
        ys = lo[1] + (np.arange(j0, j1) + 0.5) * step
        X, Y = np.meshgrid(xs, ys, indexing='ij')
        (x0, y0), (x1, y1), (x2, y2) = t
        den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(den) < 1e-9:
            continue
        l0 = ((y1 - y2) * (X - x2) + (x2 - x1) * (Y - y2)) / den
        l1 = ((y2 - y0) * (X - x2) + (x0 - x2) * (Y - y2)) / den
        eps = -1e-6
        inside = (l0 >= eps) & (l1 >= eps) & (1 - l0 - l1 >= eps)
        cov[i0:i1, j0:j1] |= inside
    return cov


def largest_rect(valid):
    """(i, j, h, w) of the largest all-True rectangle (histogram method), or None."""
    H, W = valid.shape
    best, heights = None, np.zeros(W, int)
    for i in range(H):
        heights = np.where(valid[i], heights + 1, 0)
        stack = []
        for j in range(W + 1):
            hgt = heights[j] if j < W else 0
            start = j
            while stack and stack[-1][1] >= hgt:
                s, sh = stack.pop()
                area = sh * (j - s)
                if sh and (best is None or area > best[0]):
                    best = (area, i - sh + 1, s, sh, j - s)
                start = s
            stack.append((start, hgt))
    return None if best is None else best[1:]


# -- trap grids -----------------------------------------------------------------------------------------

def plane_axes(kind, n):
    up = np.array([0.0, 0.0, 1.0])
    if kind in ('floor',):
        n = up
        u = np.array([1.0, 0.0, 0.0])
    elif kind == 'ceiling':
        n = -up
        u = np.array([1.0, 0.0, 0.0])
    else:
        h = np.cross(up, n)
        if np.linalg.norm(h) < 1e-6:
            h = np.array([1.0, 0.0, 0.0])
        u = h / np.linalg.norm(h)
        if kind == 'wall':
            n = np.array([n[0], n[1], 0.0])
            n /= np.linalg.norm(n)
            u = np.cross(up, n)
            u /= np.linalg.norm(u)
    v = np.cross(n, u)
    return n, u, v


#: Axis-aligned surfaces (kit floors, walls, ceilings) are found on depth maps seen along each
#: of the six axes: a sample counts as surface when the frontmost geometry there faces the
#: viewer within FACING_MAX_DEG; a cell is offered when that covers CELL_COVER_MIN of it and
#: its frontmost depths stay within FLAT_RANGE (the slab is +-THIN), whatever relief (planks,
#: beams, tiles) lies in between.
FACING_MAX_DEG = 35.0
#: Coverage counts any front-facing frontmost sample (round fc2: angled brace faces had left the
#: Docks wall one column short).
COVER_FACING_MIN = 0.05
#: Share of a cell that must have the piece's simple collision behind it.
COLLISION_BACKED_MIN = 0.98
FLAT_RANGE = {'kit': 20.0, 'organic': 40.0}
#: FLAT_RANGE of a kit piece's vertical walls: the most relief a shipped kit wall leaves under
#: one of its wall grids (89 grids on Wall_* Blueprints, max 36.1 uu, Wall_Half_FB_3Trap; median
#: 12.8; user 2026-10-28: Medieval Dungeon's pilastered walls took too few grids at 20).
WALL_FLAT_RANGE = 36.0
#: A ceiling box this close to the mesh's lowest point is its underside, not a ceiling (uu).
BOTTOM_SKIP = 2 * THIN
#: Cells of one box keep their frontmost depths within a band of twice this.
LEVEL_TOL = 15.0      # user, fc3: City of Brass's 12 uu pilaster relief is one surface (pack units)
AXIS_DIRS = ((2, 1, 'floor'), (2, -1, 'ceiling'), (0, 1, 'wall'), (0, -1, 'wall'), (1, 1, 'wall'), (1, -1, 'wall'))
#: A face counts as axis-aligned (found on the depth maps) within this many degrees.
AXIS_TOL_DEG = 5.0


#: Tilted and off-axis surfaces: dominant face directions from an area histogram (tilt and
#: azimuth bins), each then mapped like the axes. A direction needs this much facing area.
DIR_TILT_BIN = 5.0
DIR_AZ_BIN = 10.0
MAX_DIRS = 8
DIR_MERGE_DEG = 6.0


#: User 2026-10-14 (round fc1): floor traps on a stair incline make no sense (its sides take wall
#: grids as usual), and a steep roof slope offers no sensible trap spot.
STAIRS_INCLINE_GRID = False
#: User 2026-10-15 (Docks showcase): wall traps go on diagonal surfaces, roof slopes and their
#: undersides included, so no role drops steep slopes any more.
NO_STEEP_SLOPE_ROLES = ()
#: Half-cell strips across a curved roof are no sensible trap spot (user, fc1 P04): roof SLOPES get
#: whole cells only, on top; undersides take half cells between their rafters (user, Docks
#: showcase 2026-10-15: most of the roof undersides took no wall traps); a flat roof top takes half cells like a floor (user, Docks showcase: the
#: shelter tops SM_wood_roof_01 / SM_wood_street_roof_01 should take floor traps). Door leaves take
#: no traps.
WHOLE_CELL_ROLES = ('roof',)
WHOLE_CELL_KINDS = ('slope_floor', 'slope_wall', 'slope_ceiling')
#: Pieces whose underside is a ceiling although it is their lowest face: floors stand raised on
#: posts or other pieces (user, Docks showcase: pier undersides should take ceiling traps).
UNDERSIDE_ROLES = ('floor',)
NO_GRID_ROLES = ('door',)
#: Stairs offer no floor traps on their incline, treads or ramp-shaped collision (user, fc1). A
#: flat landing or platform takes floor traps as a floor does (user 2026-10-28: Medieval Village
#: SM_central_stairs and SM_stairs_02 took too little grid; shipped stairs pieces carry wall
#: grids). A tread never holds a flat box: across half a cell (150 uu) a 30-45 deg ramp
#: collision rises 87-150 uu, far outside the flatness allowance.
NO_TREAD_ROLES = ('stairs',)


def collision_mesh(coll):
    """(points, triangles) of the simple collision the game tests a trap against: boxes and the
    convex hulls PhysX builds (scaled, as written)."""
    Ps, Ts, base = [], [], 0
    for b in (coll or {}).get('boxes', []):
        from riftlord.formats.omd3 import omd3smwrite as W
        c = np.asarray(b['center'], np.float64)
        e = np.asarray(b['size'], np.float64) * 0.5
        R = W.rotator_matrix(b.get('rotation', (0, 0, 0)))
        corners = np.array([R @ (np.array([x, y, z]) * e) + c for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)])
        quads = ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3))
        Ps.append(corners)
        Ts += [(base + q[0], base + q[1], base + q[2]) for q in quads] + [(base + q[0], base + q[2], base + q[3]) for q in quads]
        base += 8
    for h in (coll or {}).get('convex', []):
        from riftlord.formats.omd3 import omd3smwrite as W
        v, t, _polys = W.hull_triangles(np.asarray(h, np.float32))
        Ps.append(np.asarray(v, np.float64).reshape(-1, 3))
        Ts += [tuple(int(i) + base for i in tri) for tri in t]
        base += len(v)
    if not Ps:
        return None
    return np.concatenate(Ps), np.array(Ts, np.int64).reshape(-1, 3)


#: A piece whose geometry dips at most this far below its pivot (uu, written scale) stands on its
#: pivot: what lies below z 0 is sunk into the level's floor, and no wall grid reaches below it
#: (Rift Lord in game 2026-10-15: Docks SM_wall_module_03 dips 6.41 uu; its grid started 3.2 uu
#: under the floor and the bottom row refused every trap).
BASE_SINK_MAX = 20.0


def base_plane(P):
    """z 0 when the piece stands on its pivot with a little geometry sunk below it, else None."""
    zmin = float(P[:, 2].min())
    return 0.0 if -BASE_SINK_MAX <= zmin < 0.0 else None


def suggest_grids(P, T, role, coll=None, relief_scale=1.0, two=None):
    """Suggested trap grid boxes in mesh-local space. With `coll` (the written simple collision)
    a box also sits in front of the collision and every cell must have collision behind it: the
    game tests a trap against the collision, not the drawn mesh (round fc2: P09's collision box
    stood 5-9 uu proud of the painted faces, P03's lintel box covered part of its cell)."""
    if role == 'foliage' or not len(T):
        return []
    if role in WHOLE_CELL_ROLES:
        relief_scale = 1.0          # curved roofs pass a looser flatness test (fc1 P04: no sensible spot)
    # complex-as-simple: the drawn mesh is the collision, nothing more to check
    CM = collision_mesh(coll) if coll and coll.get('trace') != 'complex_as_simple' else None
    style = 'organic' if role in ORGANIC_ROLES or role == 'roof' else 'kit'
    A, B, C, fn, area = faces(P, T)
    out = []
    e = np.eye(3)
    frames = []
    for axis, sign, kind in AXIS_DIRS:
        n = e[axis] * sign
        frames.append((kind, n))
    if two is not None:                 # a two-sided face faces both ways
        dirs = dominant_dirs(np.concatenate([fn, -fn[two]]), np.concatenate([area, area[two]]))
    else:
        dirs = dominant_dirs(fn, area)
    for kind, n in dirs:
        frames.append((kind, n))
    base = base_plane(P)
    for kind, n in frames:
        out += _depth_boxes(P, T, fn, area, kind, n, style, CM, relief_scale, base, two,
                            underside=role in UNDERSIDE_ROLES, behind_beams=role in BEHIND_BEAM_ROLES)
    if role in NO_GRID_ROLES:
        return []
    if role in NO_STEEP_SLOPE_ROLES:
        out = [g for g in out if g['kind'] != 'slope_wall']
    if role in WHOLE_CELL_ROLES:
        out = [g for g in out if g['kind'] not in WHOLE_CELL_KINDS or g['axes']['x'][2] < 0 or min(g['cells']) >= 1.0]
    if role in NO_TREAD_ROLES:
        out = [g for g in out if g['kind'] != 'slope_floor']
    out.sort(key=lambda g: (-g['cells'][0] * g['cells'][1], g['kind'], g['center']))
    for k, g in enumerate(out):
        g['id'] = 'G%02d' % k
    return out


def dominant_dirs(fn, area):
    """(kind, normal) of the tilted or off-axis surfaces that hold at least a cell of area."""
    tilt = np.degrees(np.arccos(np.clip(fn[:, 2], -1, 1)))
    az = np.degrees(np.arctan2(fn[:, 1], fn[:, 0]))
    vertical = np.abs(tilt - 90) <= FLAT_TOL_DEG
    axis_az = np.abs(((az + 45) % 90) - 45) <= AXIS_TOL_DEG
    up = (tilt > FLAT_TOL_DEG) & (tilt < SLOPE_WALL_MAX_DEG)
    under = (tilt > 180.0 - SLOPE_WALL_MAX_DEG) & (tilt < 180.0 - FLAT_TOL_DEG)    # roof undersides, overhangs
    sel = ((up | under) & ~vertical) | (vertical & ~axis_az)
    if not sel.any():
        return []
    tb = np.round(tilt[sel] / DIR_TILT_BIN).astype(int)
    ab = np.round(az[sel] / DIR_AZ_BIN).astype(int) % int(360 / DIR_AZ_BIN)
    idx = np.nonzero(sel)[0]
    bins = {}
    for t, a_, f in zip(tb, ab, idx):
        bins.setdefault((t, a_), []).append(f)
    cand = []
    for key, fs in bins.items():
        fs = np.array(fs)
        ar = area[fs].sum()
        if ar < CELL * CELL * CELL_COVER_MIN:
            continue
        n = (fn[fs] * area[fs, None]).sum(0)
        n /= np.linalg.norm(n)
        cand.append((ar, key, n))
    cand.sort(key=lambda c: (-c[0], c[1]))
    out = []
    for ar, key, n in cand:
        if any(math.degrees(math.acos(max(-1.0, min(1.0, float(n @ m))))) < DIR_MERGE_DEG for _k, m in out):
            continue
        t = math.degrees(math.acos(max(-1.0, min(1.0, n[2]))))
        kind = 'wall' if abs(t - 90) <= FLAT_TOL_DEG else 'slope'
        out.append((kind, n))
        if len(out) >= MAX_DIRS:
            break
    return out


#: Each triangle is grown by this much (uu) when sampled, so seams between planks up to twice
#: this wide read as surface (Docks SM_wall_module_half_02: 1 uu seams every 37.5 uu fell on the
#: 25 uu sample centres, and a solid plank wall read one third empty; user, Docks showcase).
SEAM_GROW = 2.0


#: (triangle, sample) pairs depth_map evaluates at once.
DEPTH_CHUNK_PAIRS = 1 << 22
#: A piece whose two largest bounds span more SAMPLE samples than this takes no trap grids: its
#: depth maps would not fit a worker. Measured: CityOfBrass City_Walls_PalaceDome01 (13.2 M,
#: the largest piece of 3094 in 23 libraries bar one) analyses within the 5.6 GB worker share;
#: Orasot SM_SkyBoxTransperent (65 M, a 2 km sky sphere) passed 12 GB and ran over 80 minutes.
DEPTH_MAX_SAMPLES = 1 << 24


def depth_map(Q, T, step, frame=None, two=None, cap=None, with_raw=False):
    """Q: points in (u, v, depth) coordinates. Frontmost depth per (u, v) sample and the facing
    (normal . +depth) of the triangle that put it there; a triangle flagged in `two` (drawn
    two-sided) faces the viewer from either side. frame=(lo, shape) samples on another map's
    lattice. cap: per sample, only geometry at least PEEL_EPS behind this depth (depth peeling).
    with_raw: also the unsigned-free facing and whether the sample's triangle is two-sided.
    Every (triangle, sample) pair of a chunk is evaluated at once; a sample keeps the frontmost
    pair, the lowest triangle index on equal depth."""
    if frame is not None:
        lo, shape = np.asarray(frame[0], np.float64), tuple(frame[1])
    else:
        lo = Q[:, :2].min(0)
        hi = Q[:, :2].max(0)
        shape = (max(1, int(math.ceil((hi[0] - lo[0]) / step))), max(1, int(math.ceil((hi[1] - lo[1]) / step))))
    depth = np.full(shape, -np.inf)
    facing = np.zeros(shape)
    tri = Q[T]
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    n = np.cross(c - a, b - a)
    nl = np.linalg.norm(n, axis=1)
    face = n[:, 2] / np.maximum(nl, 1e-12)
    raw_face = face
    if two is not None:
        face = np.where(two, np.abs(face), face)
    rawmap = np.zeros(shape)
    twomap = np.zeros(shape, bool)
    t2 = tri[..., :2] - lo
    d3 = tri[..., 2]
    mn, mx = t2.min(1) - SEAM_GROW, t2.max(1) + SEAM_GROW
    i0 = np.clip(np.floor(mn[:, 0] / step - 0.5).astype(int), 0, shape[0])
    i1 = np.clip(np.floor(mx[:, 0] / step - 0.5).astype(int) + 1, 0, shape[0])
    j0 = np.clip(np.floor(mn[:, 1] / step - 0.5).astype(int), 0, shape[1])
    j1 = np.clip(np.floor(mx[:, 1] / step - 0.5).astype(int) + 1, 0, shape[1])
    x0, y0 = t2[:, 0, 0], t2[:, 0, 1]
    x1, y1 = t2[:, 1, 0], t2[:, 1, 1]
    x2, y2 = t2[:, 2, 0], t2[:, 2, 1]
    den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
    ks = np.nonzero((i1 > i0) & (j1 > j0) & (np.abs(face) > 1e-3) & ~(np.abs(den) < 1e-9))[0]
    if not len(ks):
        return (depth, facing, lo, rawmap, twomap) if with_raw else (depth, facing, lo)
    e = np.linalg.norm(t2[:, [1, 2, 0]] - t2[:, [2, 0, 1]], axis=2)
    slack = SEAM_GROW * e / np.maximum(np.abs(den), 1e-9)[:, None]
    dmin, dmax = d3.min(1), d3.max(1)
    nj_all = (j1 - j0)
    cnt = (i1 - i0)[ks] * nj_all[ks]
    best_z = depth.ravel()                    # views: written in place
    best_k = np.full(best_z.shape, -1, np.int64)
    capf = None if cap is None else cap.ravel()
    ends = np.cumsum(cnt)
    start = 0
    while start < len(ks):
        stop = int(np.searchsorted(ends, (ends[start - 1] if start else 0) + DEPTH_CHUNK_PAIRS, 'right'))
        stop = max(stop, start + 1)
        kc = ks[start:stop]
        cc = cnt[start:stop]
        k = np.repeat(kc, cc)
        q = np.arange(int(cc.sum())) - np.repeat(np.cumsum(cc) - cc, cc)
        nj = nj_all[k]
        ii = i0[k] + q // nj
        jj = j0[k] + q % nj
        X = (ii + 0.5) * step
        Y = (jj + 0.5) * step
        X2, Y2 = x2[k], y2[k]
        dk = den[k]
        l0 = ((y1[k] - Y2) * (X - X2) + (X2 - x1[k]) * (Y - Y2)) / dk
        l1 = ((Y2 - y0[k]) * (X - X2) + (x0[k] - X2) * (Y - Y2)) / dk
        l2 = 1 - l0 - l1
        sk = slack[k]
        ins = (l0 >= -sk[:, 0]) & (l1 >= -sk[:, 1]) & (l2 >= -sk[:, 2])
        z = np.clip(l0 * d3[k, 0] + l1 * d3[k, 1] + l2 * d3[k, 2], dmin[k], dmax[k])
        s = ii * shape[1] + jj
        if capf is not None:
            ins &= z < capf[s] - PEEL_EPS
        k, z, s = k[ins], z[ins], s[ins]
        if len(s):
            order = np.lexsort((k, -z, s))
            s, z, k = s[order], z[order], k[order]
            first = np.ones(len(s), bool)
            first[1:] = s[1:] != s[:-1]
            s, z, k = s[first], z[first], k[first]
            win = z > best_z[s]               # later chunks hold higher triangle indices
            best_z[s[win]] = z[win]
            best_k[s[win]] = k[win]
        start = stop
    hit = best_k >= 0
    facing.ravel()[hit] = face[best_k[hit]]
    if with_raw:
        rawmap.ravel()[hit] = raw_face[best_k[hit]]
        if two is not None:
            twomap.ravel()[hit] = np.asarray(two, bool)[best_k[hit]]
        return depth, facing, lo, rawmap, twomap
    return depth, facing, lo


def _flat_range(style, kind):
    return WALL_FLAT_RANGE if style == 'kit' and kind == 'wall' else FLAT_RANGE[style]


def _level_tol(style, kind):
    """LEVEL_TOL; on a kit wall, half WALL_FLAT_RANGE (a shipped wall grid spans up to that much)."""
    return WALL_FLAT_RANGE / 2 if style == 'kit' and kind == 'wall' else LEVEL_TOL


def _depth_boxes(P, T, fn, area, kind, n, style, C=None, relief_scale=1.0, base=None, two=None, underside=False,
                 behind_beams=False):
    """Boxes on the surfaces seen frontmost along direction n; with `base`, no box on a face that
    is not a floor reaches below world z `base`; with `behind_beams`, an underside's boxes go on
    the planking behind the beams in front of it (_behind_beams)."""
    n, Yax, Zax = plane_axes(kind, n)
    Q = np.stack([P @ Yax, P @ Zax, P @ n], 1)
    Qc = None if C is None else np.stack([C[0] @ Yax, C[0] @ Zax, C[0] @ n], 1)
    ext = Q[:, :2].max(0) - Q[:, :2].min(0)
    ceiling_like = n[2] <= -math.cos(math.radians(SLOPE_FLOOR_MAX_DEG))
    long_side = UNIT if ceiling_like else CELL          # the smallest box: a half-cell square on a ceiling
    if max(ext) < long_side * CELL_COVER_MIN or min(ext) < UNIT * CELL_COVER_MIN:
        return []
    cosf = math.cos(math.radians(FACING_MAX_DEG))
    tri_face = fn @ n
    if two is not None:
        tri_face = np.where(two, np.abs(tri_face), tri_face)
    if (area * np.clip(tri_face, 0, 1))[tri_face >= cosf].sum() < long_side * UNIT * CELL_COVER_MIN:
        return []
    depth, facing, lo = depth_map(Q, T, SAMPLE, two=two)
    flat = _flat_range(style, kind) * relief_scale     # relief tolerances are in pack units
    level_tol = _level_tol(style, kind) * relief_scale
    if Qc is not None:
        # a sample counts only where the collision surface lies within the flatness range of the
        # drawn one: collision far behind leaves the trap unsupported, and a hull face far in front
        # (a convex hull over a curved roof) is no surface a player sees
        cdepth, cface, _lo = depth_map(Qc, C[1], SAMPLE, frame=(lo, depth.shape))
        cdepth = np.where(np.abs(cface) > 0, cdepth, -np.inf)
        with np.errstate(invalid='ignore'):
            backed = np.isfinite(cdepth) & np.isfinite(depth) & (np.abs(cdepth - depth) <= flat)
        depth = np.where(backed, np.fmax(depth, cdepth), depth)     # the frontmost of drawn and collision
    else:
        backed = np.ones(depth.shape, bool)
    # UE winding: the true front normal is (p2-p0) x (p1-p0); depth_map's cross is (c-a) x (b-a)
    # in (Y, Z, n) coordinates, which is the same handedness because Z = n x Y
    # a sample is surface when the frontmost geometry there faces the viewer at all: relief such as
    # diagonal braces (Docks SM_wall_module_03) is surface too, the flatness test bounds its depth
    good = facing >= COVER_FACING_MIN
    if base is not None and kind not in ('floor', 'ceiling'):
        ys = lo[0] + (np.arange(depth.shape[0]) + 0.5) * SAMPLE
        zs = lo[1] + (np.arange(depth.shape[1]) + 0.5) * SAMPLE
        wz = Yax[2] * ys[:, None] + Zax[2] * zs[None, :] + n[2] * np.nan_to_num(depth, posinf=0.0, neginf=0.0)
        good &= wz >= base
    layers = [(depth, good, backed)]
    if behind_beams and Qc is None and n[2] < 0:            # undersides, diagonal ones included
        bb = _behind_beams(Q, T, two, depth, facing, good, lo, flat)
        if bb is not None:
            layers = [(bb[0], bb[1], backed)]
    if Qc is None:
        layers += _interior_layers(Q, T, two, depth, lo, base, kind, Yax, Zax, n)
    boxes = []
    for depth, good, backed in layers:
        per = int(round(UNIT / SAMPLE))
        steps = int(round(UNIT / OFFSET_STEP))
        flat = _flat_range(style, kind) * relief_scale     # relief tolerances are in pack units
        level_tol = _level_tol(style, kind) * relief_scale
        best = None
        for oi in range(steps):
            for oj in range(steps):
                si, sj = int(round(oi * OFFSET_STEP / SAMPLE)), int(round(oj * OFFSET_STEP / SAMPLE))
                ni, nj = (depth.shape[0] - si) // per, (depth.shape[1] - sj) // per
                if ni < 1 or nj < 1:
                    continue
                g = good[si:si + ni * per, sj:sj + nj * per].reshape(ni, per, nj, per)
                bk = backed[si:si + ni * per, sj:sj + nj * per].reshape(ni, per, nj, per).mean((1, 3))
                d = depth[si:si + ni * per, sj:sj + nj * per].reshape(ni, per, nj, per)
                cov = g.mean((1, 3))
                dd = np.where(g, d, np.nan).transpose(0, 2, 1, 3).reshape(ni, nj, -1)
                with np.errstate(all='ignore'), warnings_off():
                    top = np.nanmax(dd, 2)
                    rng = top - np.nanmin(dd, 2)
                valid = (cov >= CELL_COVER_MIN) & (rng <= flat) & (bk >= COLLISION_BACKED_MIN)
                score = int(valid.sum())
                if best is None or score > best[0]:
                    best = (score, si, sj, ni, nj, valid, cov, top)
        tilt = math.degrees(math.acos(max(-1.0, min(1.0, n[2]))))
        if kind == 'floor':
            bits, warm, label = FLOOR_BITS, None, 'floor'
        elif kind == 'ceiling':
            bits, warm, label = CEILING_BITS, None, 'ceiling'
        elif kind == 'wall':
            bits, warm, label = WALL_BITS, None, 'wall'
        elif tilt <= SLOPE_FLOOR_MAX_DEG:
            bits, warm, label = FLOOR_BITS, SLOPE_WARMACHINE, 'slope_floor'
        elif tilt >= 180.0 - SLOPE_FLOOR_MAX_DEG:
            bits, warm, label = CEILING_BITS, None, 'slope_ceiling'
        else:
            bits, warm, label = WALL_BITS, None, 'slope_wall'        # diagonal undersides included
        min_units = MIN_UNITS_CEILING if label in ('ceiling', 'slope_ceiling') else MIN_UNITS
        min_long = MIN_UNITS_CEILING if label in ('ceiling', 'slope_ceiling') else MIN_LONG_UNITS
        if best is None or best[0] < min_units:
            continue
        _s, si, sj, ni, nj, valid, cov, med = best
        work = valid.copy()
        raw = []
        for _ in range(MAX_BOXES_PER_PLANE * 2):
            if not work.any():
                break
            # the band of 2 x level_tol, starting at a cell's depth, that holds the most cells
            starts = np.unique(med[work])
            held = [int((work & (med >= t) & (med <= t + 2 * level_tol)).sum()) for t in starts]
            t = starts[int(np.argmax(held))]
            mask = work & (med >= t) & (med <= t + 2 * level_tol)
            r = largest_rect(mask)
            if r is None:
                work &= ~mask
                continue
            i, j, h, w = r
            if h * w < min_units:
                break
            work[i:i + h, j:j + w] = False
            if max(h, w) < min_long:
                continue
            dep = float(np.max(med[i:i + h, j:j + w]))     # the frontmost surface: nothing pokes through the slab
            if kind == 'ceiling' and not underside and -dep <= P[:, 2].min() + BOTTOM_SKIP:
                continue                        # the piece's own underside: it rests on the ground
            yc = lo[0] + (si + (i + h / 2) * per) * SAMPLE
            zc = lo[1] + (sj + (j + w / 2) * per) * SAMPLE
            raw.append([i, j, h, w, dep, yc, zc])
        # centre a box in its surface run only when it is alone on its plane: boxes sharing a plane
        # keep one common lattice (user, fc3: P03's door-side strips sat off the lintel strip's lines)
        for k, bx in enumerate(raw if len(raw) == 1 else []):
            i, j, h, w, dep, yc, zc = bx
            taken = np.zeros(depth.shape, bool)
            for m, other in enumerate(raw):
                if m != k:
                    taken |= _footprint(depth.shape, lo, other[5], other[6], other[2] * UNIT, other[3] * UNIT)
            bx[5], bx[6] = _centre_in_surface(depth, good & ~taken, backed, lo, yc, zc, h * UNIT, w * UNIT, dep, flat)
        # then every box onto the exact surface edges (a sampled edge can leave a box hanging past the
        # wall), unless that would make it overlap a neighbour on the plane
        for k, bx in enumerate(raw):
            i, j, h, w, dep, yc, zc = bx
            floor_z = None if kind in ('floor', 'ceiling') else base
            ny, nz = _centre_exact(Q, T, yc, zc, h * UNIT, w * UNIT, dep, flat, nudge_only=len(raw) > 1,
                                   axes_z=(Yax[2], Zax[2]), base=floor_z)

            def clash(ty, tz):
                return any(abs(ty - o[5]) < (h + o[2]) * UNIT / 2 - 1e-6 and abs(tz - o[6]) < (w + o[3]) * UNIT / 2 - 1e-6
                           for m, o in enumerate(raw) if m != k)
            if not clash(ny, bx[6]):                 # each direction on its own: a vertical clash must not
                bx[5] = ny                           # cost the sideways correction
            if not clash(bx[5], nz):
                bx[6] = nz
        for i, j, h, w, dep, yc, zc in raw:
            dep = _top_under(Q, T, yc, zc, h * UNIT / 2, w * UNIT / 2, dep, flat)
            if Qc is not None:
                dep = _top_under(Qc, C[1], yc, zc, h * UNIT / 2, w * UNIT / 2, dep, flat)
            centre = Yax * yc + Zax * zc + n * dep
            rng = float(np.max(med[i:i + h, j:j + w]) - np.min(med[i:i + h, j:j + w]))
            boxes.append({'kind': label, 'center': [round(float(x), 2) for x in centre],
                          'axes': {'x': [round(float(x), 5) for x in n], 'y': [round(float(x), 5) for x in Yax],
                                   'z': [round(float(x), 5) for x in Zax]},
                          'rotation': axes_rotator(n, Yax, Zax),
                          'extent': [THIN, h * UNIT / 2 + EXTENT_PAD, w * UNIT / 2 + EXTENT_PAD],
                          'cells': [float(h * UNIT / CELL), float(w * UNIT / CELL)], 'tilt_deg': round(tilt, 2), 'bits': bits, 'warmachine': warm,
                          'cover': round(float(cov[i:i + h, j:j + w].mean()), 3), 'max_offset': round(rng / 2, 2)})
    return boxes


#: Interior surfaces (user, Docks showcase 2026-10-15: SM_side_cran_house_01's door wall took
#: traps outside only; from inside, every view direction first met the opposite outer wall).
#: Behind the frontmost surface, up to INTERIOR_LAYERS more surfaces are peeled; one counts
#: where it faces the viewer, the surface in front of it is the back of a wall (or a two-sided
#: sheet) so air lies between, and that air is at least INTERIOR_CLEAR deep.
INTERIOR_LAYERS = 3
INTERIOR_CLEAR = UNIT
#: A peeled layer starts at least this far (uu) behind the one before (coplanar copies skipped).
PEEL_EPS = 1.0


#: Roles whose undersides take grids on the planking behind interior beams and trusses (user
#: 2026-10-24: Docks hip roof SM_roof_cran_house_02 and spire SM_roof_spier_01 stayed bare,
#: trusses stood 80-1200 uu in front of the planks in every cell).
BEHIND_BEAM_ROLES = ('roof',)


def _behind_beams(Q, T, two, depth0, facing0, good0, lo, flat):
    """(depth, good): the frontmost map with every sample where something narrow stands in front
    of the planking moved back onto it. The planking's depth at a sample is the median of the
    facing samples in its cell-sized block (beams are narrow; per block, the median follows a
    curved roof; memory stays that of the depth map: a sliding window copied 144 values per
    sample, 15 GB on City of Brass's palace domes); a sample more than `flat` in front of it
    moves to the first peeled facing layer within `flat` of it. None when no sample moved."""
    per = int(round(CELL / SAMPLE))
    d = np.where(good0 & np.isfinite(depth0), depth0, np.nan)
    ni, nj = -(-d.shape[0] // per), -(-d.shape[1] // per)
    blocks = np.full((ni * per, nj * per), np.nan)
    blocks[:d.shape[0], :d.shape[1]] = d
    with warnings_off():
        med = np.nanmedian(blocks.reshape(ni, per, nj, per).transpose(0, 2, 1, 3).reshape(ni, nj, per * per), -1)
    plank = np.repeat(np.repeat(med, per, 0), per, 1)[:d.shape[0], :d.shape[1]]
    del blocks
    with np.errstate(invalid='ignore'):
        blocked = np.isfinite(depth0) & np.isfinite(plank) & (depth0 > plank + flat)
    if not blocked.any():
        return None
    out, good = depth0.copy(), good0.copy()
    moved = np.zeros(depth0.shape, bool)
    prev = depth0
    for _ in range(INTERIOR_LAYERS):
        dl, fl, _lo = depth_map(Q, T, SAMPLE, frame=(lo, depth0.shape), two=two, cap=prev)
        if not np.isfinite(dl).any():
            break
        with np.errstate(invalid='ignore'):
            take = blocked & ~moved & (fl >= COVER_FACING_MIN) & np.isfinite(dl) & (np.abs(dl - plank) <= flat)
        out[take] = dl[take]
        moved |= take
        prev = dl
    if not moved.any():
        return None
    good[moved] = True
    return out, good


def _interior_layers(Q, T, two, depth0, lo, base, kind, Yax, Zax, n):
    """[(depth, good, backed)] of the interior surfaces behind the frontmost map depth0."""
    _d, _f, _lo, raw, tw = depth_map(Q, T, SAMPLE, frame=(lo, depth0.shape), two=two, with_raw=True)
    prev, prev_raw, prev_two = depth0, raw, tw
    out = []
    for _ in range(INTERIOR_LAYERS):
        d, f, _lo, raw, tw = depth_map(Q, T, SAMPLE, frame=(lo, depth0.shape), two=two, cap=prev, with_raw=True)
        if not np.isfinite(d).any():
            break
        with np.errstate(invalid='ignore'):
            air = ((prev_raw < 0) | prev_two) & np.isfinite(prev) & np.isfinite(d) & (prev - d >= INTERIOR_CLEAR)
        good = (f >= COVER_FACING_MIN) & air
        if base is not None and kind not in ('floor', 'ceiling'):
            ys = lo[0] + (np.arange(d.shape[0]) + 0.5) * SAMPLE
            zs = lo[1] + (np.arange(d.shape[1]) + 0.5) * SAMPLE
            wz = Yax[2] * ys[:, None] + Zax[2] * zs[None, :] + n[2] * np.nan_to_num(d, posinf=0.0, neginf=0.0)
            good &= wz >= base
        if good.any():
            out.append((d, good, np.ones(d.shape, bool)))
        prev, prev_raw, prev_two = d, raw, tw
    return out


def _footprint(shape, lo, yc, zc, ly, lz):
    """Sample mask of a box footprint (centre yc, zc; side lengths ly, lz)."""
    out = np.zeros(shape, bool)
    i0 = max(0, int(round((yc - ly / 2 - lo[0]) / SAMPLE))); i1 = min(shape[0], int(round((yc + ly / 2 - lo[0]) / SAMPLE)))
    j0 = max(0, int(round((zc - lz / 2 - lo[1]) / SAMPLE))); j1 = min(shape[1], int(round((zc + lz / 2 - lo[1]) / SAMPLE)))
    if i1 > i0 and j1 > j0:
        out[i0:i1, j0:j1] = True
    return out


def _centre_in_surface(depth, good, backed, lo, yc, zc, ly, lz, dep, flat):
    """Slide a box (centre yc, zc; side lengths ly, lz) to the middle of the surface run it lies
    on, along each axis. The lattice search packs boxes against an edge, and a trap whose edge
    lies on the surface's edge is refused: a one-cell-wide grid flush with an edge took only half
    traps (user, fc3: City of Brass wall P09, the stairs' sides)."""
    surf = good & backed & np.isfinite(depth) & (np.abs(depth - dep) <= flat)
    n0, n1 = surf.shape

    def run(line, a, b):
        # the covered run of samples containing [a, b), as sample indices
        a, b = max(0, a), min(len(line), b)
        if a >= b or not line[a:b].all():
            return None
        while a > 0 and line[a - 1]:
            a -= 1
        while b < len(line) and line[b]:
            b += 1
        return a, b
    i0 = int(round((yc - ly / 2 - lo[0]) / SAMPLE)); i1 = int(round((yc + ly / 2 - lo[0]) / SAMPLE))
    j0 = int(round((zc - lz / 2 - lo[1]) / SAMPLE)); j1 = int(round((zc + lz / 2 - lo[1]) / SAMPLE))
    cols = surf[:, max(0, j0):min(n1, j1)]
    if cols.size:
        r = run(cols.mean(1) >= CELL_COVER_MIN, i0, i1)
        if r:
            yc = lo[0] + (r[0] + r[1]) / 2 * SAMPLE
    rows = surf[max(0, i0):min(n0, i1), :]
    if rows.size:
        r = run(rows.mean(0) >= CELL_COVER_MIN, j0, j1)
        if r:
            zc = lo[1] + (r[0] + r[1]) / 2 * SAMPLE
    return yc, zc


def _centre_exact(Q, T, yc, zc, ly, lz, dep, flat, nudge_only=False, axes_z=(0.0, 0.0), base=None):
    """Re-centre a box on the exact extent of the surface it covers, along each axis. The sampled
    run is only good to SAMPLE: City of Brass P09's grid at x1.5 hung 1 uu past the wall's edge,
    so every trap position touching that edge had a corner off the wall (round fc3)."""
    tri = Q[T]
    near = (tri[..., 2].max(1) >= dep - flat) & (tri[..., 2].max(1) <= dep + 1.0)
    for axis in (0, 1):
        c, L = (yc, ly) if axis == 0 else (zc, lz)
        oc, oL = (zc, lz) if axis == 0 else (yc, ly)
        other = 1 - axis
        over = near & (tri[..., other].max(1) > oc - oL / 2) & (tri[..., other].min(1) < oc + oL / 2)
        # only the surface within a sample of the box's current span: never jump a hole
        reach = (tri[..., axis].max(1) > c - L / 2 - SAMPLE) & (tri[..., axis].min(1) < c + L / 2 + SAMPLE)
        sel = over & reach
        if not sel.any():
            continue
        a = max(float(tri[sel][..., axis].min()), c - L / 2 - SAMPLE)
        b = min(float(tri[sel][..., axis].max()), c + L / 2 + SAMPLE)
        k = axes_z[axis]
        if base is not None and abs(k) >= VERTICAL_AXIS_MIN:     # this axis runs up the wall
            if k > 0:
                a = max(a, base / k)
            else:
                b = min(b, base / k)
        if b - a >= L:
            if not nudge_only:
                c = (a + b) / 2
            elif c - L / 2 < a:                  # boxes sharing a plane keep their common lattice and
                c = a + L / 2                    # only move back inside an edge they overhang
            elif c + L / 2 > b:
                c = b - L / 2
        if axis == 0:
            yc = c
        else:
            zc = c
    return yc, zc


#: A box axis counts as running up a wall when its world-z component is at least this.
VERTICAL_AXIS_MIN = 0.99


def _top_under(Q, T, yc, zc, hy, hz, dep, flat):
    """The highest geometry under a box's footprint, up to `flat` above the sampled surface. Thin
    relief (trim strips narrower than SAMPLE) slips between depth samples, and a trap is refused
    when anything rises through the slab above the box's mid-plane (round fc1, measured)."""
    tri = Q[T]
    mn, mx = tri[..., :2].min(1), tri[..., :2].max(1)
    over = (mx[:, 0] > yc - hy) & (mn[:, 0] < yc + hy) & (mx[:, 1] > zc - hz) & (mn[:, 1] < zc + hz)
    top = tri[..., 2].max(1)
    cand = top[over & (top > dep) & (top <= dep + flat)]
    return float(cand.max()) if len(cand) else dep


class warnings_off:
    def __enter__(self):
        import warnings
        self._c = warnings.catch_warnings()
        self._c.__enter__()
        warnings.simplefilter('ignore')

    def __exit__(self, *a):
        self._c.__exit__(*a)


# -- openings -------------------------------------------------------------------------------------------

def opening(P, T):
    """Clear doorway through the mesh's thin horizontal axis: the widest run of columns whose
    clear height (from the floor, sill up to DOOR_STEP_MAX ignored) is bounded above by the mesh
    (a lintel), and the best window for a hero and an ogre."""
    lo, hi = P.min(0), P.max(0)
    ext = hi - lo
    thin = 0 if ext[0] < ext[1] else 1
    across = 1 - thin
    w = int(math.ceil(ext[across] / OPENING_RES)) + 1
    h = int(math.ceil(ext[2] / OPENING_RES)) + 1
    if w < 3 or h < 3:
        return None
    t2 = np.stack([P[T][..., across] - lo[across], P[T][..., 2] - lo[2]], -1)
    occ = raster_cover(t2, np.zeros(2), (w, h), OPENING_RES)
    # edges of thin triangles (frames seen edge-on) count as occupied
    step0 = int(DOOR_STEP_MAX / OPENING_RES)
    clear = np.zeros(w)
    lintel = np.zeros(w, bool)
    for i in range(w):
        col = occ[i, step0:]
        k = np.nonzero(col)[0]
        if len(k):
            clear[i] = (k[0]) * OPENING_RES + DOOR_STEP_MAX
            lintel[i] = True
            if occ[i, :step0 + 1].all() and k[0] == 0:
                clear[i] = 0.0
        else:
            clear[i] = 0.0                               # open to the sky: not a doorway
    best = None
    i = 0
    while i < w:
        if lintel[i] and clear[i] >= HERO_HEIGHT:
            j = i
            while j < w and lintel[j] and clear[j] >= HERO_HEIGHT:
                j += 1
            if 0 < i and j < w:                          # bounded on both sides
                if best is None or (j - i) > best[1] - best[0]:
                    best = (i, j)
            i = j
        else:
            i += 1
    if best is None:
        return {'has_opening': False}
    i, j = best
    cols = clear[i:j]
    width = (j - i) * OPENING_RES

    def window_height(win):
        n = int(math.ceil(win / OPENING_RES))
        if n > len(cols):
            return 0.0
        return float(max(cols[k:k + n].min() for k in range(len(cols) - n + 1)))
    ogre_h = window_height(OGRE_WIDTH)
    return {'has_opening': True, 'axis': 'xy'[across], 'clear_width': round(width, 1),
            'clear_height': round(float(cols.max()), 1), 'clear_height_min': round(float(cols.min()), 1),
            'center_offset': round(float(lo[across] + (i + j) / 2 * OPENING_RES), 1),
            'hero_passable': window_height(HERO_WIDTH) >= HERO_HEIGHT,
            'ogre_clear_height': round(ogre_h, 1),
            'ogre_passable': bool(width >= OGRE_WIDTH and ogre_h >= OGRE_HEIGHT)}


# -- stairs ----------------------------------------------------------------------------------------------

def stairs(P, T):
    """Riser, going and slope from the top-down heightmap of up-facing faces (SCALE.md method)."""
    A, B, C, fn, area = faces(P, T)
    up = fn[:, 2] >= 0.8
    if not up.any():
        return None
    lo, hi = P.min(0), P.max(0)
    w = int(math.ceil((hi[0] - lo[0]) / STAIR_RES)) + 1
    h = int(math.ceil((hi[1] - lo[1]) / STAIR_RES)) + 1
    top = np.full((w, h), -np.inf)
    for a, b, c in zip(A[up], B[up], C[up]):
        t = np.array([a[:2], b[:2], c[:2]]) - lo[:2]
        cov = raster_cover(t[None], np.zeros(2), (w, h), STAIR_RES)
        z = (a[2] + b[2] + c[2]) / 3
        top = np.where(cov, np.maximum(top, z), top)
    zs = top[np.isfinite(top)]
    if len(zs) < 4:
        return None
    levels = []
    for z in np.sort(np.unique(np.round(zs, 1))):
        if levels and z - levels[-1][-1] <= STAIR_MERGE:
            levels[-1].append(z)
        else:
            levels.append([z])
    lv = []
    for grp in levels:
        m = (top >= grp[0] - 0.05) & (top <= grp[-1] + 0.05)
        if m.sum() * STAIR_RES ** 2 >= 0.3 * (STAIR_RES ** 2) * max(1, np.isfinite(top).sum()) / max(len(levels), 1):
            ii, jj = np.nonzero(m)
            lv.append((float(np.mean(grp)), float(ii.mean() * STAIR_RES), float(jj.mean() * STAIR_RES)))
    if len(lv) < 3:
        return {'steps': len(lv)}
    z = np.array([x[0] for x in lv])
    xy = np.array([[x[1], x[2]] for x in lv])
    axis = 0 if np.ptp(xy[:, 0]) >= np.ptp(xy[:, 1]) else 1
    order = np.argsort(z)
    risers = np.diff(z[order])
    goings = np.abs(np.diff(xy[order, axis]))
    riser = float(np.median(risers))
    going = float(np.median(goings)) if len(goings) else 0.0
    angle = math.degrees(math.atan2(riser, max(going, 1e-6)))
    climb = np.sign(xy[order[-1], axis] - xy[order[0], axis])
    # incline through the tread centres: z = z0 + k * (coordinate along the climb axis)
    coord = xy[:, axis] + lo[axis]
    k, z0 = np.polyfit(coord, z, 1) if np.ptp(coord) > 1e-6 else (0.0, float(z.mean()))
    across = 1 - axis
    incline = {'axis': axis, 'k': float(k), 'z0': float(z0), 'run': [float(coord.min()), float(coord.max())],
               'across': [float(lo[across]), float(hi[across])]}
    return {'_incline': incline,'steps': len(lv), 'riser': round(riser, 1), 'going': round(going, 1), 'angle_deg': round(angle, 1),
            'climb': ('+' if climb >= 0 else '-') + 'xy'[axis], 'rise': round(float(z.max() - z.min()), 1)}


def stairs_grid(inc, st):
    """One tilted floor box on the stairs' incline (shipped StairGroup / Ramp pieces carry
    sloped boxes), when the flight is walkable and at least one cell each way."""
    if not inc or not st or st.get('angle_deg') is None or st['angle_deg'] > SLOPE_FLOOR_MAX_DEG:
        return None
    ax, ac = inc['axis'], 1 - inc['axis']
    run = inc['run'][1] - inc['run'][0]
    slope_len = run * math.sqrt(1 + inc['k'] ** 2)
    width = inc['across'][1] - inc['across'][0]
    A, B = int(width // CELL), int(slope_len // CELL)
    if A < 1 or B < 1:
        return None
    e_ax = np.zeros(3)
    e_ax[ax] = 1.0
    e_ac = np.zeros(3)
    e_ac[ac] = 1.0
    v = e_ax + np.array([0, 0, inc['k']])
    v /= np.linalg.norm(v)                                 # up the slope
    u = e_ac
    n = np.cross(u, v)
    if n[2] < 0:
        u = -u
        n = np.cross(u, v)
    c_ax = (inc['run'][0] + inc['run'][1]) / 2
    centre = e_ax * c_ax + e_ac * (inc['across'][0] + inc['across'][1]) / 2
    centre[2] = inc['z0'] + inc['k'] * c_ax
    tilt = math.degrees(math.acos(max(-1.0, min(1.0, n[2]))))
    return {'kind': 'slope_floor', 'center': [round(float(x), 2) for x in centre],
            'axes': {'x': [round(float(x), 5) for x in n], 'y': [round(float(x), 5) for x in u],
                     'z': [round(float(x), 5) for x in v]},
            'rotation': axes_rotator(n, u, v), 'extent': [THIN, A * CELL / 2, B * CELL / 2], 'cells': [A, B],
            'tilt_deg': round(tilt, 2), 'bits': FLOOR_BITS, 'warmachine': SLOPE_WARMACHINE,
            'cover': None, 'max_offset': round(st['riser'] / 2, 2), 'source': 'stairs incline'}


# -- the whole record --------------------------------------------------------------------------------------

def pivot_of(lo, hi):
    def where(a, b):
        if abs(a) <= 1 and abs(b) <= 1:
            return 'edge'
        if a <= -1 and b >= 1 and abs(a + b) <= 0.02 * (b - a) + 1:
            return 'centre'
        if a <= 1 < b or a < -1 <= b:
            return 'inside'
        return 'outside'
    return {'x': where(lo[0], hi[0]), 'y': where(lo[1], hi[1]),
            'z': 'bottom' if abs(lo[2]) <= 1 else ('top' if abs(hi[2]) <= 1 else
                                                   ('centre' if abs(lo[2] + hi[2]) <= 2 else 'other'))}


def analyse(desc, role, coll=None, scale=1.0, two_sided=None):
    """two_sided: per triangle, drawn (and colliding) from both faces."""
    P = np.asarray(desc['positions'], np.float64)
    T = np.asarray(desc['triangles'], np.int64)
    two = None if two_sided is None or not np.any(two_sided) else np.asarray(two_sided, bool)
    lo, hi = P.min(0), P.max(0)
    rec = {'bounds': {'min': [round(float(x), 2) for x in lo], 'max': [round(float(x), 2) for x in hi],
                      'size': [round(float(x), 2) for x in hi - lo]},
           'pivot': pivot_of(lo, hi), 'warnings': []}
    span = np.sort(hi - lo)[::-1]
    if coll is not None and coll.get('collides') is False:
        rec['grids'] = []                       # nothing to hold a trap
    elif span[0] * span[1] / (SAMPLE * SAMPLE) > DEPTH_MAX_SAMPLES:
        rec['grids'] = []
        rec['warnings'].append('%.0f x %.0f uu: too large for trap grids' % (span[0], span[1]))
    else:
        rec['grids'] = suggest_grids(P, T, role, coll, max(1.0, scale), two)
    if role in ('door', 'arch', 'wall'):
        op = opening(P, T)
        if op and op.get('has_opening'):
            rec['opening'] = op
            if role in ('door', 'arch') and not op['ogre_passable']:
                rec['warnings'].append('opening %.0f wide x %.0f clear: an ogre (160 x 360) does not pass'
                                       % (op['clear_width'], op['ogre_clear_height']))
        elif role in ('door', 'arch'):
            rec['opening'] = op or {'has_opening': False}
    if role == 'stairs':
        st = stairs(P, T)
        inc = st.pop('_incline', None) if st else None
        rec['stairs'] = st
        if STAIRS_INCLINE_GRID:
            g = stairs_grid(inc, st)
            if g:
                g['id'] = 'G%02d' % len(rec['grids'])
                rec['grids'].append(g)
        if st and st.get('angle_deg') is not None and st['angle_deg'] > STAIR_MAX_DEG:
            rec['warnings'].append('stairs climb %.1f deg, steeper than the 45 deg agent slope: keep off enemy routes'
                                   % st['angle_deg'])
    if len(T) > TRIS_WARN:
        rec['warnings'].append('%d triangles (above %d)' % (len(T), TRIS_WARN))
    if (hi - lo).max() > SIZE_WARN:
        rec['warnings'].append('%.0f uu across: a backdrop or landmark piece' % (hi - lo).max())
    return rec
