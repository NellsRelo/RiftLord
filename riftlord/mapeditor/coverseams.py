"""Cover seams: the seams between kit pieces that shipped levels hide with a static mesh,
found on an open map, each with the cover the shipped levels use for it.

    python3 -m riftlord.mapeditor.coverseams --record 900_Arena_P.json [--json OUT]
    python3 -m riftlord.mapeditor.coverseams --level Scenario_01

A seam is an opening between two kit pieces, measured on their real vertices in the frame
of the junction (docs/research/kit-placement-census.md sections 3 to 5; the same finders
as its junctions.py):

- `wall_top`: an upper floor whose edge stops short of the top of the wall under it;
- `wall_face`: a floor whose edge stops short of the foot of the wall standing on it
  (the floor stops short, or the wall's skirting is buried in the floor);
- `skirting`: a wall standing on a floor with its skirting above the floor's surface;
- `wall_end`: a wall end short of the column or the wall it ends against;
- `floor_seam`: two floors of different families edge to edge whose surfaces do not meet.

A seam already covered by another piece or mesh (one whose box spans the opening over at
least half the run, as the census counts covers) is left alone. For the rest, `propose`
places the shipped cover for the seam's type and style set from
`riftlord/data/seam-covers.json` (mined from the 33 shipped levels by
/mnt/2tb_pro_a/tmp/coverseams/covermine.py), as an `added` mesh item with collision off,
stretched along the run in pieces no longer than the shipped ones, and checks it against
every trap cell. The Map Editor's Cover Seams command (POST /api/coverseams/<level>) shows
the proposals; an accepted one is an ordinary added mesh.
"""
import argparse
import functools
import json
import math
import sys

import numpy as np

from riftlord.core import gamepath
from riftlord.formats.omd3 import meshcat, omd3level, omd3mesh

#: An opening wider than this, in uu, is open (census sections 3 and 4).
OPEN = 0.5
#: Shared edge two grids need to form a junction, in uu (census junctions.py RUN).
RUN = 150.0
#: uu either side of a trap surface that count as that surface (census kc.FACE).
FACE = 8.0
#: A grid normal whose |z| is above this is a floor or a ceiling (census kc.VERTICAL).
VERTICAL = 0.7
#: A matrix entry this close to 0 or +-1 is a quarter turn (census kc.axis_aligned).
ALIGNED = 0.01
#: Mesh catalogue categories that never cover a seam (census junctions.py NOCOVER_CATS).
NOCOVER_CATS = ('terrain', 'foliage', 'effect')
#: A box this long on two axes is a sky or a ground, never a cover (census junctions.py).
HUGE = 3000.0
#: A cover covers a seam when it spans the opening within this, in uu (census covers tol).
SPAN_TOL = 1.0
#: ... and runs along at least this share of it (census covers).
COVERED_SHARE = 0.5

# junction windows, in uu, as the census measures them (junctions.py wall_floor, wall_wall)
#: a floor in front of a wall: its near edge from the wall plane, its plane under the wall
#: grid's bottom, and how far it runs into the room
FRONT_EDGE = (-600.0, 300.0)
FRONT_DROP = (-150.0, 300.0)
FRONT_DEPTH = 150.0
#: the foot band over the floor's plane a wall's skirting is measured in
FOOT_BAND = (-15.0, 40.0)
#: an upper floor behind a wall: its edge from the wall plane, its surface over the wall's
#: mesh top (census section 4 "upper floors proper")
UPPER_EDGE = (-150.0, 75.0)
UPPER_RISE = (-10.0, 300.0)
#: the band under a wall's mesh top that is its top
TOP_BAND = 60.0
#: a column or wall a wall end meets: its plane within this of the wall's, and its near face
#: this far from the wall's grid end (census wall_wall)
END_PLANE = 60.0
END_GAP = (-150.0, 200.0)
#: the mid band a wall end and a column shaft are measured in, this far in from each end
SHAFT_IN = 100.0
#: two floors' grids edge to edge within this, in uu (census ff "-1..1")
EDGE_TO_EDGE = 1.0
#: coplanar floors: planes within this, in uu (census ff)
COPLANAR = 25.0
#: a cover's depth band around a floor seam and a wall top, in uu (census covers)
FLOOR_BAND = 30.0
TOP_COVER_BAND = (30.0, 10.0)
WALL_FACE_BAND = (10.0, 30.0)

#: The trap-cell slab: a cover that enters it takes the cell (the trap-grid audit,
#: /mnt/2tb_pro_a/tmp/tg900/tg.py: SLAB_NEAR, SLAB_FAR, INSET and the 300 uu cell).
CELL = 300.0
SLAB_NEAR = 15.0
SLAB_FAR = 75.0
INSET = 30.0
#: contact the audit forgives, in uu (tg.py sat)
SLAB_CONTACT = 1.0

#: what a seam type is called in the editor
LABELS = {
    'wall_top': 'Wall top',
    'wall_face': 'Wall foot',
    'skirting': 'Skirting',
    'wall_end': 'Wall end',
    'floor_seam': 'Floor join',
}
#: why a seam has no proposal, as the editor says it, most telling first
WHY = {
    'traps': 'A cover would block trap cells',
    'open': 'No cover closes it',
    'wide': 'Too wide to cover',
    'none': 'No cover for this seam',
}
#: what is wrong with a seam no shipped cover suits, by type: what the designer changes
NO_COVER = {
    'wall_top': 'Floor stops short of the wall',
    'wall_face': 'Floor stops short of the wall',
    'skirting': 'Wall stands above the floor',
    'wall_end': 'Wall ends short of its neighbour',
    'floor_seam': 'Floor surfaces do not meet',
}
CATALOGUE = 'seam-covers.json'
#: A cover family needs this many shipped cases on this many levels (census section 9);
#: the miner applies it, the messages quote it.
MIN_CASES, MIN_LEVELS = 10, 3


# ------------------------------------------------------------------ names
def family(cls):
    """A class name without its cell size: Floor_RB_3x3_C -> Floor_RB (census kc.family)."""
    import re
    n = cls[:-2] if cls.endswith('_C') else cls
    n = re.sub(r'_(\d+(?:-\d+)?)[xX](\d+(?:-\d+)?)(?:[xX](\d+(?:-\d+)?))?', '', n)
    return re.sub(r'_\d+_\d+(?:_\d+)?(?=_|$)', '', n)


#: Style sets: a class name's material tokens, first match wins.
STYLE_SETS = (('Sand', ('SAND', 'Sand', 'Arabic')), ('Dry', ('Dry',)),
              ('Wood', ('WD', 'WDMT', 'WDMTL', 'Wood', 'Plank')),
              ('Stone', ('RB', 'FB', 'SB', 'FS', 'MT', 'Trim', 'Sewer', 'CS', 'RBFB')))


def style_set(cls):
    """The style set of a kit class: Sand, Dry, Wood or Stone; '' when its name says none."""
    import re
    toks = set(re.split(r'[_\-]', family(cls or '')))
    for name, words in STYLE_SETS:
        if toks & set(words):
            return name
    return ''


def kind_of(cls, grids):
    """'wall', 'floor', 'ceiling', 'column', 'stair' or 'other' (census kc.kind_of)."""
    n = cls.lower()
    if 'stair' in n:
        return 'stair'
    if 'column' in n or 'pillar' in n:
        return 'column'
    if 'ceiling' in n:
        return 'ceiling'
    if 'floor' in n or 'platform' in n or 'ground' in n or 'landing' in n:
        return 'floor'
    if 'wall' in n or 'archway' in n or 'tower' in n:
        return 'wall'
    s = {g['surface'] for g in grids}
    if s == {'Floor'}:
        return 'floor'
    if 'Wall' in s:
        return 'wall'
    return 'ceiling' if s == {'Ceiling'} else 'other'


# ------------------------------------------------------------------ geometry
@functools.lru_cache(maxsize=None)
def _mesh_verts(mesh):
    """A mesh's vertices in its own space as an (n, 3) array; None when unreadable."""
    try:
        got = omd3mesh.read(mesh, meshcat.load())
    except Exception:                                # noqa: BLE001
        return None
    v = (got or {}).get('verts')
    return np.array(v, float) if v else None


def mesh_verts(mesh):
    return _mesh_verts(mesh)


@functools.lru_cache(maxsize=256)
def mesh_tris(mesh):
    """A mesh's triangles in its own space as an (n, 3, 3) array; None when unreadable."""
    try:
        got = omd3mesh.read(mesh, meshcat.load())
    except Exception:                                # noqa: BLE001
        return None
    v, t = (got or {}).get('verts'), (got or {}).get('tris')
    if not v or not t:
        return None
    return np.array(v, float)[np.array(t, int).reshape(-1, 3)]


#: Points sampled across an opening for closes(): this far apart along the run, uu ...
CLOSE_STEP = 25.0
#: ... this many across the gap ...
CLOSE_ACROSS = 5
#: ... and this far in from the opening's edges and the run's ends, uu.
CLOSE_IN = 0.25
#: A cover blocks a sightline when it crosses it within this of the seam's depth band, uu.
CLOSE_REACH = 150.0
#: A cover is proposed only when it closes this share of its seam's opening.
CLOSE_SHARE = 0.95


def closes(seam, placed):
    """The share of `seam`'s opening that the meshes `placed` ([(mesh, world 4x4)]) close:
    sightlines along the seam's depth axis through points across its gap and along its open
    runs, blocked when a cover triangle crosses one within CLOSE_REACH of its depth band.
    A box around the opening is not enough: a trim turned 45 degrees spans the opening
    with its box and covers none of it."""
    g, d, r = seam['gap_axis'], seam['depth_axis'], seam['run_axis']
    glo, ghi = sorted(seam['gap_world'])
    inset = min(CLOSE_IN * (ghi - glo), 2.0)
    across = np.linspace(glo + inset, ghi - inset, CLOSE_ACROSS)
    pts = []
    for a, b in seam.get('open_runs') or [seam['run']]:
        n = max(2, int((b - a) / CLOSE_STEP))
        for x in np.linspace(a + CLOSE_STEP / 2, b - CLOSE_STEP / 2, n):
            pts.extend((x, y) for y in across)
    if not pts:
        return 1.0
    P = np.array(pts)
    hit = np.zeros(len(P), bool)
    dlo, dhi = seam['depth_band']
    for mesh, m in placed:
        T = mesh_tris(mesh)
        if T is None:
            continue
        W = T @ np.asarray(m)[:3, :3].T + np.asarray(m)[:3, 3]
        keep = (W[:, :, d].max(1) >= dlo - CLOSE_REACH) & (W[:, :, d].min(1) <= dhi + CLOSE_REACH)
        W = W[keep]
        if not len(W):
            continue
        A, B, C = W[:, 0][:, [r, g]], W[:, 1][:, [r, g]], W[:, 2][:, [r, g]]
        lo = np.minimum(np.minimum(A, B), C)
        hi = np.maximum(np.maximum(A, B), C)
        for k in np.where(~hit)[0]:
            p = P[k]
            near = np.where((lo[:, 0] <= p[0]) & (hi[:, 0] >= p[0]) &
                            (lo[:, 1] <= p[1]) & (hi[:, 1] >= p[1]))[0]
            if not len(near):
                continue
            a, b, c = A[near], B[near], C[near]
            def side(u, v):
                return (v[:, 0] - u[:, 0]) * (p[1] - u[:, 1]) - (v[:, 1] - u[:, 1]) * (p[0] - u[:, 0])
            s1, s2, s3 = side(a, b), side(b, c), side(c, a)
            if np.any(((s1 >= 0) & (s2 >= 0) & (s3 >= 0)) | ((s1 <= 0) & (s2 <= 0) & (s3 <= 0))):
                hit[k] = True
    return float(hit.mean())


def plan_matrix(flat):
    """A plan entry's column-major 16 floats as a 4x4 (column-vector) matrix."""
    return np.array(flat, float).reshape(4, 4).T


def flat_matrix(m):
    """A 4x4 as a plan entry's column-major 16 floats."""
    return [round(float(x), omd3level.MATRIX_DECIMALS) for x in np.asarray(m).T.reshape(16)]


def aligned(m, tol=ALIGNED):
    r = m[:3, :3] / np.maximum(np.linalg.norm(m[:3, :3], axis=0), 1e-9)
    return bool(np.all((np.abs(r) < tol) | (np.abs(np.abs(r) - 1) < tol)))


def world_grid(g, actor):
    """One kit-geometry grid (leveldata.kit_grids) under actor matrix `actor`."""
    o = actor @ np.append(np.array(g['origin'], float), 1.0)
    axes = [actor[:3, :3] @ np.array(a, float) for a in g['axes']]
    sc = [np.linalg.norm(a) for a in axes]
    ax = [a / s if s else a for a, s in zip(axes, sc)]
    ext = np.array(g['extent'], float) * np.array(sc)
    c = o[:3]
    corners = np.array([c + ax[0] * ext[0] * a + ax[1] * ext[1] * b + ax[2] * ext[2] * d
                        for a in (-1, 1) for b in (-1, 1) for d in (-1, 1)])
    n = ax[0]
    na = int(np.abs(n).argmax())
    surface = 'Floor' if n[2] > VERTICAL else 'Ceiling' if n[2] < -VERTICAL else 'Wall'
    ok = all(abs(abs(x) - 1) < ALIGNED or abs(x) < ALIGNED for a in ax for x in a)
    return dict(c=c, n=n, ext=ext, lo=corners.min(0), hi=corners.max(0), surface=surface,
                na=na, s=float(np.sign(n[na])) or 1.0, aligned=ok)


class Piece:
    """A kit piece: its class, world trap grids and the world vertices of its drawn parts."""

    def __init__(self, key, cls, actor, parts, grids, label=None):
        self.key, self.cls, self.label = key, cls, label or key
        self.fam = family(cls)
        self.style = style_set(cls)
        self.A = actor
        self.grids = grids
        self.kind = kind_of(cls, grids)
        self.aligned = aligned(actor)
        vs, boxes = [], []
        for mesh, w in parts:
            v = mesh_verts(mesh)
            if v is None:
                continue
            wv = v @ w[:3, :3].T + w[:3, 3]
            vs.append(wv)
            boxes.append((mesh, wv.min(0), wv.max(0), w))
        self.V = np.concatenate(vs) if vs else np.zeros((0, 3))
        self.boxes = boxes
        if len(self.V):
            self.lo, self.hi = self.V.min(0), self.V.max(0)
        elif grids:
            self.lo = np.min([g['lo'] for g in grids], 0)
            self.hi = np.max([g['hi'] for g in grids], 0)
        else:
            self.lo = self.hi = actor[:3, 3].copy()


class Scene:
    """Kit pieces and static meshes of one map, and the boxes that can cover a seam."""

    def __init__(self, pieces, statics):
        self.pieces = [p for p in pieces if p.grids]
        cat = meshcat.load()
        names, lo, hi, owner, mats = [], [], [], [], []
        for key, mesh, w in statics:
            if (cat.get(mesh) or {}).get('category') in NOCOVER_CATS:
                continue
            v = mesh_verts(mesh)
            if v is None:
                continue
            wv = v @ w[:3, :3].T + w[:3, 3]
            a, b = wv.min(0), wv.max(0)
            if ((b - a) > HUGE).sum() >= 2:
                continue
            names.append(mesh); lo.append(a); hi.append(b); owner.append(key); mats.append(w)
        for p in pieces:
            for mesh, a, b, w in p.boxes:
                if ((b - a) > HUGE).sum() >= 2:
                    continue
                names.append(mesh); lo.append(a); hi.append(b); owner.append(p.key); mats.append(w)
        self.cn = names
        self.cw = mats
        self.clo = np.array(lo).reshape(-1, 3)
        self.chi = np.array(hi).reshape(-1, 3)
        self.cown = owner

    def covers(self, seam, own):
        """Indices of boxes, not of the `own` piece keys, that span the seam's opening
        across its gap axis within SPAN_TOL, overlap its depth band and its run."""
        if not len(self.cn):
            return []
        lo, hi = self.clo, self.chi
        g, d, r = seam['gap_axis'], seam['depth_axis'], seam['run_axis']
        glo, ghi = sorted(seam['gap_world'])
        m = (lo[:, g] <= glo + SPAN_TOL) & (hi[:, g] >= ghi - SPAN_TOL)
        dlo, dhi = seam['depth_band']
        m &= (lo[:, d] <= dhi) & (hi[:, d] >= dlo)
        m &= (np.minimum(hi[:, r], seam['run'][1]) - np.maximum(lo[:, r], seam['run'][0])) > 0
        return [i for i in np.where(m)[0] if self.cown[i] not in own]


# ------------------------------------------------------------------ building a scene
def _kit_parts(cls):
    from riftlord.mapeditor import leveldata
    return leveldata.kit_parts(cls)


def _kit_grids(cls):
    from riftlord.mapeditor import leveldata
    return leveldata.kit_grids(cls)


def _part_local(part):
    return np.array(omd3level._matrix(part.get('loc') or (0, 0, 0), part.get('rot') or (0, 0, 0),
                                      part.get('scale') or (1, 1, 1)), float)


def added_piece(n, entry):
    """A kit piece for plan entry `added[n]`."""
    cls = entry['name']
    actor = plan_matrix(entry['matrix'])
    parts = [(p['mesh'], actor @ _part_local(p)) for p in (_kit_parts(cls).get('parts') or [])
             if not p.get('skeletal') and not p.get('hidden')]
    grids = [world_grid(g, actor) for g in _kit_grids(cls) if g.get('extent')]
    return Piece('added|%d' % n, cls, actor, parts, grids, label='#%d %s' % (n, cls[:-2]))


def level_scene(level_name, plan=None):
    """(pieces, statics) of a shipped level as the plan leaves it: the plan's removed pieces
    gone, its edits' matrices on the rest, then the plan's added kit and meshes."""
    plan = plan or {}
    pieces, statics = [], []
    if not (plan.get('scratch')):
        got = omd3level.read(level_name)
        gone = set()
        for r in plan.get('removed') or []:
            gone.add(int(r['id'] if isinstance(r, dict) else r))
        moved = {int(e['id']): e.get('matrix') for e in plan.get('edits') or [] if e.get('matrix')}
        actors = {}
        for p in got.get('pieces') or []:
            if p.get('id') in gone or p.get('foliage') or p.get('hidden'):
                continue
            w = plan_matrix(moved.get(p.get('id')) or p['matrix'])
            if p.get('source') == 'blueprint' and p.get('actor'):
                actors.setdefault((p.get('sub') or 'persistent', p['export']), []).append((p, w))
            elif p.get('source') in ('component', 'spline'):
                statics.append(('level|%s' % p.get('id'), p['mesh'], w))
        for (sub, export), kin in actors.items():
            cls = kin[0][0]['actor']
            grids = _kit_grids(cls)
            if not grids:
                for p, w in kin:
                    statics.append(('level|%s' % p.get('id'), p['mesh'], w))
                continue
            cparts = _kit_parts(cls).get('parts') or []
            once = [q for q in kin if sum(1 for c in cparts if c.get('mesh') == q[0]['mesh']) == 1]
            p0, w0 = (once or kin)[0]
            part = next((c for c in cparts if c.get('mesh') == p0['mesh']), None)
            if part is None:
                continue
            actor = w0 @ np.linalg.inv(_part_local(part))
            pieces.append(Piece('level|%s|%s' % (sub, export), cls, actor,
                                [(q['mesh'], w) for q, w in kin],
                                [world_grid(g, actor) for g in grids if g.get('extent')],
                                label='%s %s' % (cls[:-2], export)))
    for n, e in enumerate(plan.get('added') or []):
        if e.get('kind') == 'kit':
            pieces.append(added_piece(n, e))
        elif e.get('kind', 'mesh') == 'mesh' and e.get('matrix'):
            statics.append(('added|%d' % n, e['name'], plan_matrix(e['matrix'])))
    return pieces, statics


# ------------------------------------------------------------------ seams
def _surface(p, g):
    """Vertices of piece `p` on grid `g`'s trap surface: within FACE of the most proud face
    near the plane (census junctions.surface_verts)."""
    V = p.V
    if not len(V):
        return V
    t = (V - g['c']) @ g['n']
    near = np.abs(t) <= 40
    if not near.any():
        return V[:0]
    return V[np.abs(t - t[near].max()) <= FACE]


def _seam(kind, pieces, run_axis, run, gap_axis, gap_sign, gap, depth_axis, depth_sign,
          depth_ref, gap_ref, depth_band, **extra):
    """A seam: an opening `gap` (signed coordinates along gap_axis, low to high) over `run`
    (world, along run_axis), with the references its cover is placed from."""
    gw = sorted([gap[0] * gap_sign, gap[1] * gap_sign])
    return dict(type=kind, pieces=[p.key for p in pieces], labels=[p.label for p in pieces],
                classes=[p.cls for p in pieces], style=pieces[0].style, family=pieces[0].fam,
                run_axis=run_axis, run=[float(run[0]), float(run[1])],
                gap_axis=gap_axis, gap_sign=gap_sign, gap=[float(gap[0]), float(gap[1])],
                gap_world=gw, width=float(gap[1] - gap[0]),
                depth_axis=depth_axis, depth_sign=depth_sign, depth_ref=float(depth_ref),
                gap_ref=float(gap_ref), depth_band=sorted(float(x) for x in depth_band), **extra)


def _walls(scene):
    return [(p, g) for p in scene.pieces if p.aligned and p.kind == 'wall'
            for g in p.grids if g['aligned'] and g['surface'] == 'Wall' and g['na'] in (0, 1)]


def _floors(scene):
    return [(p, g) for p in scene.pieces if p.aligned and p.kind in ('floor', 'ceiling')
            for g in p.grids if g['aligned'] and g['surface'] == 'Floor']


def _overlap(a, b, o):
    return min(a['hi'][o], b['hi'][o]) - max(a['lo'][o], b['lo'][o])


def wall_floor_seams(scene):
    """wall_top, wall_face and skirting seams."""
    out = []
    W, F = _walls(scene), _floors(scene)
    for pw, gw in W:
        na, o = gw['na'], 1 - gw['na']
        s, p = gw['s'], gw['c'][na]
        need = min(RUN, 0.5 * (gw['hi'][o] - gw['lo'][o]))
        # a floor in front, the wall standing on it
        best = None
        for pf, gf in F:
            if pf is pw or _overlap(gw, gf, o) < need:
                continue
            far = gf['hi'][na] if s > 0 else gf['lo'][na]
            near = gf['lo'][na] if s > 0 else gf['hi'][na]
            if (far - p) * s < FRONT_DEPTH:
                continue
            dpl, dz = (near - p) * s, gw['lo'][2] - gf['c'][2]
            if FRONT_EDGE[0] <= dpl <= FRONT_EDGE[1] and FRONT_DROP[0] <= dz <= FRONT_DROP[1]:
                key = (abs(dz), abs(dpl))
                if best is None or key < best[0]:
                    best = (key, pf, gf)
        if best is not None:
            out.extend(_front(pw, gw, best[1], best[2]))
        # an upper floor behind, over the wall's top
        best = None
        for pf, gf in F:
            if pf is pw or _overlap(gw, gf, o) < need or (gf['c'][na] - p) * s >= 0:
                continue
            edge = gf['hi'][na] if s > 0 else gf['lo'][na]
            dg, dzt = (edge - p) * s, gf['c'][2] - gw['hi'][2]
            if UPPER_EDGE[0] <= dg <= UPPER_EDGE[1] and -200 <= dzt <= 400:
                key = (abs(dzt), abs(dg))
                if best is None or key < best[0]:
                    best = (key, pf, gf)
        if best is not None:
            got = _upper(pw, gw, best[1], best[2])
            if got:
                out.append(got)
    return out


def _front(pw, gw, pf, gf):
    """The wall_face or skirting seam of wall grid `gw` standing on floor grid `gf`."""
    na = gw['na']
    o, s, p = 1 - na, gw['s'], gw['c'][na]
    a0, a1 = max(gw['lo'][o], gf['lo'][o]), min(gw['hi'][o], gf['hi'][o])
    fz = gf['c'][2]
    sv = _surface(pf, gf)
    sv = sv[(sv[:, o] >= a0) & (sv[:, o] <= a1)] if len(sv) else sv
    if not len(sv):
        return []
    fedge = float(((sv[:, na] - p) * s).min())
    fsurf = float(sv[:, 2].max())
    V = pw.V
    inrun = V[(V[:, o] >= a0) & (V[:, o] <= a1)] if len(V) else V
    if not len(inrun):
        return []
    t = (inrun[:, na] - p) * s
    band = inrun[(inrun[:, 2] >= fz + FOOT_BAND[0]) & (inrun[:, 2] <= fz + FOOT_BAND[1])]
    if not len(band):
        return []
    reach = float(((band[:, na] - p) * s).max())
    front = inrun[t >= -5.0]
    wbot = float(front[:, 2].min()) if len(front) else None
    off = p * s
    out = []
    if fedge - reach > OPEN:
        buried = wbot is not None and wbot < fsurf - OPEN and _skirted(pw)
        out.append(_seam('wall_face', [pw, pf], o, (a0, a1), na, s, (reach + off, fedge + off),
                         2, 1.0, fsurf, reach + off, (fz - WALL_FACE_BAND[0], fz + WALL_FACE_BAND[1]),
                         cause='buried skirting' if buried else 'floor stops short'))
    elif wbot is not None and wbot - fsurf > OPEN:
        foot = front[front[:, 2] <= wbot + (FOOT_BAND[1] - FOOT_BAND[0])]
        tf = (foot[:, na] - p) * s
        out.append(_seam('skirting', [pw, pf], o, (a0, a1), 2, 1.0, (fsurf, wbot), na, s,
                         float(tf.max()) + off, fsurf,
                         sorted([float(tf.min()) * s + p, float(tf.max()) * s + p])))
    return out


def _skirted(p):
    """Whether a wall class reaches 20 uu or more below its grid (Snapping a move: skirted)."""
    try:
        from riftlord.mapeditor import kitfit
        return any(f and (f.get('below') or 0) >= 20 for f in kitfit.fits(p.cls))
    except Exception:                                # noqa: BLE001
        return False


def _upper(pw, gw, pf, gf):
    """The wall_top seam of wall grid `gw` under upper floor grid `gf`, or None."""
    na = gw['na']
    o, s, p = 1 - na, gw['s'], gw['c'][na]
    a0, a1 = max(gw['lo'][o], gf['lo'][o]), min(gw['hi'][o], gf['hi'][o])
    V = pw.V
    vr = V[(V[:, o] >= a0) & (V[:, o] <= a1)] if len(V) else V
    sv = _surface(pf, gf)
    sv = sv[(sv[:, o] >= a0) & (sv[:, o] <= a1)] if len(sv) else sv
    if not len(vr) or not len(sv):
        return None
    mtop = float(vr[:, 2].max())
    top = vr[vr[:, 2] >= mtop - TOP_BAND]
    tt = (top[:, na] - p) * s
    tfront, tback = float(tt.max()), float(tt.min())
    fedge = float(((sv[:, na] - p) * s).max())
    fsurf = float(sv[:, 2].max())
    if not UPPER_RISE[0] <= fsurf - mtop <= UPPER_RISE[1]:
        return None
    #: the hole seen from above: the floor's edge short of the wall's back face (census
    #: section 4 "plan gap"); a floor edge over the wall's top is the shipped ledge
    edge = tback
    if edge - fedge <= OPEN:
        return None
    off = p * s
    return _seam('wall_top', [pw, pf], o, (a0, a1), na, s, (fedge + off, edge + off), 2, 1.0,
                 mtop, edge + off, (mtop - TOP_COVER_BAND[0], max(mtop, fsurf) + TOP_COVER_BAND[1]),
                 cause='floor stops short of the wall', surface=fsurf)


def wall_end_seams(scene):
    """wall_end seams: a wall's mesh end short of the column or the wall it ends against."""
    out = []
    W = _walls(scene)
    cols = [p for p in scene.pieces if p.kind == 'column' and p.aligned and len(p.V)]
    #: walls side by side: another wall with a grid facing the same way on the same plane
    side = list({id(q): q for q, _ in W}.values())
    seen = set()
    for pw, gw in W:
        na = gw['na']
        o, s, p = 1 - na, gw['s'], gw['c'][na]
        z0, z1 = gw['lo'][2], gw['hi'][2]
        for d in (-1, 1):
            E = gw['hi'][o] if d > 0 else gw['lo'][o]
            best = None
            for c in cols + side:
                if c is pw or min(c.hi[2], z1) - max(c.lo[2], z0) < RUN:
                    continue
                if c.lo[na] > p + END_PLANE or c.hi[na] < p - END_PLANE:
                    continue
                if c.kind != 'column' and not any(
                        g['surface'] == 'Wall' and g['aligned'] and g['na'] == na and g['s'] == s and
                        abs(g['c'][na] - p) <= END_PLANE and (g['c'][o] - E) * d > 0 for g in c.grids):
                    continue
                near = c.lo[o] if d > 0 else c.hi[o]
                gap = (near - E) * d
                if END_GAP[0] <= gap <= END_GAP[1] and (best is None or abs(gap) < abs(best[0])):
                    best = (gap, c)
            if best is None:
                continue
            c = best[1]
            zm0, zm1 = max(z0, c.lo[2]) + SHAFT_IN, min(z1, c.hi[2]) - SHAFT_IN
            if zm1 <= zm0:
                continue
            cv = c.V[(c.V[:, 2] >= zm0) & (c.V[:, 2] <= zm1)]
            wv = pw.V[(pw.V[:, 2] >= zm0) & (pw.V[:, 2] <= zm1)]
            # only the partner's vertices in front of the wall's end, across the wall
            if not len(cv) or not len(wv):
                continue
            wt = (wv[:, na] - p) * s
            tlo, thi = float(wt.min()), float(wt.max())
            ct = (cv[:, na] - p) * s
            cv = cv[(ct >= tlo - END_PLANE) & (ct <= thi + END_PLANE)]
            ahead = cv[(cv[:, o] - E) * d >= -(SHAFT_IN)]
            if not len(ahead):
                continue
            shaft = float(((ahead[:, o] - E) * d).min())
            wend = float(((wv[:, o] - E) * d).max())
            if shaft - wend <= OPEN:
                continue
            key = (frozenset((pw.key, c.key)), round(E + d * (wend + shaft) / 2), round(zm0))
            if key in seen:
                continue
            seen.add(key)
            out.append(_seam('wall_end', [pw, c], 2, (zm0 - SHAFT_IN, zm1 + SHAFT_IN), o, float(d),
                             (wend + E * d, shaft + E * d), na, s, thi + p * s, wend + E * d,
                             sorted([tlo * s + p, thi * s + p]),
                             cause='wall end short of a %s' % ('column' if c.kind == 'column' else 'wall')))
    return out


def floor_seams(scene):
    """floor_seam seams: floors of different families edge to edge, surfaces apart."""
    out = []
    F = _floors(scene)
    seen = set()
    for i, (pa, ga) in enumerate(F):
        for j, (pb, gb) in enumerate(F):
            if j <= i or pa is pb or pa.fam == pb.fam or (pa.key, pb.key) in seen:
                continue
            if abs(ga['c'][2] - gb['c'][2]) > COPLANAR:
                continue
            for t in (0, 1):
                e = 1 - t
                A, GA, B, GB = (pa, ga, pb, gb) if ga['c'][t] <= gb['c'][t] else (pb, gb, pa, ga)
                if abs(GB['lo'][t] - GA['hi'][t]) > EDGE_TO_EDGE:
                    continue
                e0, e1 = max(ga['lo'][e], gb['lo'][e]), min(ga['hi'][e], gb['hi'][e])
                if e1 - e0 < RUN:
                    continue
                sa, sb = _surface(A, GA), _surface(B, GB)
                sa = sa[(sa[:, e] >= e0) & (sa[:, e] <= e1)] if len(sa) else sa
                sb = sb[(sb[:, e] >= e0) & (sb[:, e] <= e1)] if len(sb) else sb
                if not len(sa) or not len(sb):
                    continue
                ea, eb = float(sa[:, t].max()), float(sb[:, t].min())
                if eb - ea <= OPEN:
                    continue
                zs = max(float(sa[:, 2].max()), float(sb[:, 2].max()))
                seen.add((pa.key, pb.key))
                out.append(_seam('floor_seam', [A, B], e, (e0, e1), t, 1.0, (ea, eb), 2, 1.0, zs,
                                 (ea + eb) / 2, (zs - FLOOR_BAND, zs + FLOOR_BAND)))
    return out


def find(scene):
    """Every seam of `scene`, each with `covered_by` (cover box names) and `open_runs`
    ([[a, b]] of the run no cover box spans)."""
    seams = wall_floor_seams(scene) + wall_end_seams(scene) + floor_seams(scene)
    for sm in seams:
        hits = scene.covers(sm, set(sm['pieces']))
        sm['covered_by'] = sorted({scene.cn[i] for i in hits})
        r0, r1 = sm['run']
        spans = sorted((max(r0, scene.clo[i][sm['run_axis']]), min(r1, scene.chi[i][sm['run_axis']]))
                       for i in hits)
        covered, free, at = 0.0, [], r0
        for a, b in spans:
            if b <= at:
                continue
            if a > at:
                free.append([at, a])
            covered += b - max(a, at)
            at = b
        if at < r1:
            free.append([at, r1])
        sm['covered_share'] = covered / (r1 - r0) if r1 > r0 else 1.0
        sm['open_runs'] = free
        sm['_cover_hits'] = hits
    return seams


def open_seams(seams):
    """The seams no cover box covers over COVERED_SHARE of their run."""
    return [s for s in seams if s['covered_share'] < COVERED_SHARE]


# ------------------------------------------------------------------ frames and covers
def frame(seam):
    """3x3 whose columns are the world directions of a seam's run (+ world), gap and depth."""
    f = np.zeros((3, 3))
    f[seam['run_axis'], 0] = 1.0
    f[seam['gap_axis'], 1] = seam['gap_sign']
    f[seam['depth_axis'], 2] = seam['depth_sign']
    return f


#: A mesh's own length in its name: ColumnTrim_CastStone_600W, Column_Sec_1_4_RB,
#: Floor_Squarebrick_1x4 (one cover family, several lengths).
_LENGTH = (r'_\d+W(?=_|$)', r'_\d+_\d+(?=_|$)', r'_\d+[xX]\d+(?=_|$)')
#: A frame entry this close to +-1 lines a mesh axis up with the seam's run.
RUN_ALIGNED = 0.02


def mesh_family(mesh):
    """A mesh name without its length: ColumnTrim_CastStone_900W -> ColumnTrim_CastStone."""
    import re
    out = mesh
    for pat in _LENGTH:
        out = re.sub(pat, '', out)
    return out


def cover_measure(seam, mesh, w):
    """How mesh `mesh` at world matrix `w` sits on `seam`, in the seam's frame, or None when
    none of its axes runs along the seam: {mesh, family, turn (its rotation in the frame,
    3x3), scale [3], run_local (its axis along the run), raw (its own length along it),
    gap and depth ([lo, hi] of its box from the seam's gap and depth references)}."""
    v = mesh_verts(mesh)
    if v is None:
        return None
    sc = np.linalg.norm(w[:3, :3], axis=0)
    rot = w[:3, :3] / np.maximum(sc, 1e-9)
    rf = frame(seam).T @ rot
    k = int(np.abs(rf[0]).argmax())
    if abs(abs(rf[0, k]) - 1) > RUN_ALIGNED:
        return None
    fv = (v @ w[:3, :3].T + w[:3, 3]) @ frame(seam)
    lo, hi = fv.min(0), fv.max(0)
    return dict(mesh=mesh, family=mesh_family(mesh), turn=np.round(rf, 3).tolist(),
                scale=[round(float(x), 4) for x in sc], run_local=k,
                raw=float((v.max(0) - v.min(0))[k]),
                gap=[float(lo[1] - seam['gap_ref']), float(hi[1] - seam['gap_ref'])],
                depth=[float(lo[2] - seam['depth_ref']), float(hi[2] - seam['depth_ref'])])


@functools.lru_cache(maxsize=1)
def catalogue():
    """seam-covers.json: {'covers': [entry], 'thin': [...]}; empty when missing."""
    try:
        with open(gamepath.data(CATALOGUE)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {'covers': [], 'thin': []}


def entries_for(seam, cat=None):
    """The catalogue entries for a seam's type and style set, most shipped cases first."""
    cat = cat or catalogue()
    return sorted([e for e in cat.get('covers') or [] if e['type'] == seam['type'] and
                   e['style'] == seam['style']], key=lambda e: -e['cases'])


def thin_reason(seam, cat=None):
    """Why no cover is proposed for a seam no shipped cover suits: what is wrong with it."""
    return NO_COVER.get(seam['type'], WHY['none'])


def left_open(seam, cat=None):
    """Whether shipped levels leave seams like this one open: of the MIN_CASES shipped seams
    of its type nearest it in width, more open than covered."""
    rows = (cat or catalogue()).get('shipped_widths', {}).get(seam['type']) or []
    if len(rows) < MIN_CASES:
        return False
    near = sorted(rows, key=lambda r: abs(r[0] - seam['width']))[:MIN_CASES]
    return sum(r[1] for r in near) * 2 < len(near)


def open_pair(seam, cat=None):
    """The shipped counts when shipped levels leave this seam's pair of families open more
    often than they cover it ({covered, open}), else None."""
    cat = cat or catalogue()
    fams = sorted(family(c) for c in seam['classes'])
    for row in cat.get('open_pairs') or []:
        if row['type'] == seam['type'] and sorted(row['families']) == fams:
            return row
    return None


def spans(seam, entry, lo=None):
    """Whether `entry`'s cover, its box `lo` (default the shipped median) past the seam's gap
    reference, spans its opening."""
    lo = entry['gap'][0] if lo is None else lo
    ref, wide = seam['gap_ref'], entry['gap'][1] - entry['gap'][0]
    return (ref + lo <= seam['gap'][0] + SPAN_TOL and
            ref + lo + wide >= seam['gap'][1] - SPAN_TOL)


def offsets(seam, entry):
    """The gap offsets to try for `entry`'s cover on `seam`: the shipped median first, then
    every offset a shipped cover of the family sat at, nearest the median first; only those
    that span the opening."""
    seen = [entry['gap'][0]] + sorted(entry.get('gap_seen') or [],
                                      key=lambda g: abs(g - entry['gap'][0]))
    return [g for g in seen if spans(seam, entry, g)]


def pick_member(entry, length):
    """(mesh, pieces) of `entry`'s family for a run `length` long: the fewest pieces each
    stretched no further than the shipped stretch, then the stretch nearest 1."""
    best = None
    lo, hi = entry['stretch']
    for mesh, raw in entry['members'].items():
        if raw <= 0:
            continue
        n = max(1, int(math.ceil(length / (raw * hi) - 1e-6)))
        each = length / n / raw
        if each < lo:
            continue
        key = (n, abs(each - 1.0))
        if best is None or key < best[0]:
            best = (key, mesh, n)
    if best is None:
        #: shorter than the shortest member at its least stretch: one of the shortest
        mesh = min(entry['members'], key=lambda m: entry['members'][m])
        return mesh, 1
    return best[1], best[2]


def place(seam, entry, r0, r1, lo=None):
    """[(mesh, world matrix)] of `entry`'s cover over run [r0, r1] of `seam`: the family's
    member and count that keep each piece within the shipped stretch, laid end to end, each
    turned and scaled across as shipped, its box at the shipped gap and depth offsets."""
    mesh, pieces = pick_member(entry, r1 - r0)
    v = mesh_verts(mesh)
    if v is None:
        return []
    turn = np.array(entry['turn'], float)
    k = entry['run_local']
    raw = entry['members'][mesh]
    step = (r1 - r0) / pieces
    f = frame(seam)
    out = []
    for i in range(pieces):
        a = r0 + i * step
        sc = np.array(entry['scale'], float)
        sc[k] = step / raw
        m = np.eye(4)
        m[:3, :3] = (f @ turn) * sc[None, :]
        fv = (v @ m[:3, :3].T) @ f
        want = np.array([a, seam['gap_ref'] + (entry['gap'][0] if lo is None else lo),
                         seam['depth_ref'] + entry['depth'][0]])
        m[:3, 3] = f @ (want - fv.min(0))
        out.append((mesh, m))
    return out


def fab_grids(plan):
    """[(key, grid)] of the plan's Fab grids (fabemit.world_grids) in world_grid's shape,
    the ones turned by quarter turns."""
    try:
        from riftlord.mapeditor import fabemit
        placed, _ = fabemit.resolve(plan)
        got = fabemit.world_grids(plan, placed)
    except (Exception, SystemExit):                  # noqa: BLE001
        return []
    out = []
    for i, g in enumerate(got):
        ax = [np.array(a, float) for a in g['axes']]
        ext = np.array(g['extent'], float)
        c = np.array(g['center'], float)
        if not all(abs(abs(x) - 1) < ALIGNED or abs(x) < ALIGNED for a in ax for x in a):
            continue
        corners = np.array([c + ax[0] * ext[0] * a + ax[1] * ext[1] * b + ax[2] * ext[2] * d
                            for a in (-1, 1) for b in (-1, 1) for d in (-1, 1)])
        na = int(np.abs(ax[0]).argmax())
        out.append(('fab|%d' % i, dict(c=c, n=ax[0], ext=ext, lo=corners.min(0), hi=corners.max(0),
                                       na=na, s=float(np.sign(ax[0][na])) or 1.0, aligned=True)))
    return out


def cell_slabs(scene, extra=()):
    """Each trap cell's slab (owner key, centre, half sizes), as the trap-grid audit cuts
    them, of the scene's kit grids and `extra` [(key, grid)] (Fab grids)."""
    out = []
    every = [(p.key, g) for p in scene.pieces for g in p.grids] + list(extra)
    for key, g in every:
        if not g['aligned']:
            continue
        n, na = g['n'], g['na']
        ax = [a for a in range(3) if a != na]
        nu, nv = [int(math.floor((g['hi'][a] - g['lo'][a]) / CELL + 1e-3)) for a in ax]
        half = np.zeros(3)
        half[na] = (SLAB_FAR - SLAB_NEAR) / 2
        half[ax[0]] = half[ax[1]] = CELL / 2 - INSET
        for i in range(nu):
            for j in range(nv):
                c = np.zeros(3)
                c[ax[0]] = g['lo'][ax[0]] + CELL / 2 + CELL * i
                c[ax[1]] = g['lo'][ax[1]] + CELL / 2 + CELL * j
                c[na] = g['c'][na] + n[na] * (SLAB_NEAR + SLAB_FAR) / 2
                out.append((key, c, half))
    return out


def obb(mesh, m):
    """(centre, axes (3 unit rows), half sizes) of mesh `mesh`'s own box under matrix `m`,
    as the trap-grid audit boxes a piece (tg.py obb)."""
    v = mesh_verts(mesh)
    lo, hi = v.min(0), v.max(0)
    c = m @ np.append((lo + hi) / 2, 1.0)
    axes, half = [], []
    for k in range(3):
        col = m[:3, k]
        n = np.linalg.norm(col)
        axes.append(col / n if n else np.eye(3)[k])
        half.append(abs(hi[k] - lo[k]) / 2 * n)
    return c[:3], np.array(axes), np.array(half)


def _apart(a, b):
    """Whether two boxes (centre, axes, half) are apart on some separating axis by more than
    SLAB_CONTACT (tg.py sat)."""
    ca, aa, ha = a
    cb, ab, hb = b
    t = cb - ca
    for axis in list(aa) + list(ab) + [np.cross(x, y) for x in aa for y in ab]:
        n = np.linalg.norm(axis)
        if n < 1e-6:
            continue
        axis = axis / n
        ra = np.sum(ha * np.abs(aa @ axis))
        rb = np.sum(hb * np.abs(ab @ axis))
        if abs(t @ axis) > ra + rb - SLAB_CONTACT:
            return True
    return False


def takes_cells(mesh, m, slabs):
    """The piece keys whose trap cells mesh `mesh` at matrix `m` enters: its box against each
    cell's slab, by separating axes as the trap-grid audit tests a blocker."""
    box = obb(mesh, m)
    reach = np.sum(box[2] * np.abs(box[1]), 0)
    got = set()
    for key, c, half in slabs:
        if key in got or np.any(np.abs(c - box[0]) > half + reach):
            continue
        if not _apart(box, (c, np.eye(3), half)):
            got.add(key)
    return got


def propose(scene, seams=None, cat=None, extra_grids=()):
    """{'proposals': [...], 'problems': [...], 'seams': n} for the open seams of `scene`.

    A proposal is {id, seam: {type, label, pieces, names, width, run, where}, items: [added
    mesh entries], cover: {mesh, family, closes}}: a cover is proposed only when it closes
    CLOSE_SHARE of the opening (closes()) and takes no trap cell. A problem is {id, seam,
    why}, `why` one of WHY's sentences."""
    cat = cat or catalogue()
    seams = find(scene) if seams is None else seams
    slabs = cell_slabs(scene, extra_grids)
    proposals, problems = [], []
    #: open seams shipped levels leave open too
    left = 0
    for sm in open_seams(seams):
        where = _where(sm)
        sid = '%s|%s|%d' % (sm['type'], '|'.join(sm['pieces']), int(sm['run'][0]))
        info = dict(type=sm['type'], label=LABELS[sm['type']], pieces=sm['pieces'],
                    names=sm['labels'], width=round(sm['width'], 1),
                    run=round(sm['run'][1] - sm['run'][0], 1), where=where,
                    cause=sm.get('cause'))
        if open_pair(sm, cat) or left_open(sm, cat):
            left += 1
            continue
        why = set()
        done = None
        for entry in entries_for(sm, cat):
            if sm['width'] > entry['widths'][1] + SPAN_TOL:
                why.add('wide')
                continue
            tries = offsets(sm, entry)
            if not tries:
                why.add('wide')
                continue
            for lo in tries:
                placed = [pm for a, b in sm['open_runs'] for pm in place(sm, entry, a, b, lo)]
                if not placed:
                    why.add('open')
                    continue
                share = closes(sm, placed)
                if share < CLOSE_SHARE:
                    why.add('open')
                    continue
                blocked = set()
                for mesh, m in placed:
                    blocked |= takes_cells(mesh, m, slabs)
                if blocked:
                    why.add('traps')
                    continue
                done = (entry, placed, share)
                break
            if done:
                break
        if done is None:
            code = next((k for k in WHY if k in why), 'none')
            problems.append(dict(id=sid, seam=info,
                                 why=thin_reason(sm, cat) if code == 'none' else WHY[code]))
            continue
        entry, placed, share = done
        items = [{'kind': 'mesh', 'name': mesh, 'matrix': flat_matrix(m), 'collision': False}
                 for mesh, m in placed]
        proposals.append(dict(id=sid, seam=info, items=items,
                              cover=dict(mesh=items[0]['name'], family=entry['family'],
                                         closes=round(share, 3))))
    return {'proposals': proposals, 'problems': problems, 'seams': len(seams), 'left_open': left}


def _label(scene, key):
    for p in scene.pieces:
        if p.key == key:
            return p.label
    return key


def _box(mesh, m):
    v = mesh_verts(mesh)
    wv = v @ m[:3, :3].T + m[:3, 3]
    return wv.min(0), wv.max(0)


def _where(sm):
    """The seam's middle, in world coordinates."""
    at = [0.0, 0.0, 0.0]
    at[sm['run_axis']] = (sm['run'][0] + sm['run'][1]) / 2
    at[sm['gap_axis']] = (sm['gap_world'][0] + sm['gap_world'][1]) / 2
    at[sm['depth_axis']] = (sm['depth_band'][0] + sm['depth_band'][1]) / 2
    return [round(x, 1) for x in at]


def for_plan(level_name, plan):
    """propose() on the map `plan` makes of `level_name`."""
    pieces, statics = level_scene(level_name, plan)
    return propose(Scene(pieces, statics), extra_grids=fab_grids(plan))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--record', help='a Missions record .json (read only)')
    ap.add_argument('--level', help='a shipped level, unedited')
    ap.add_argument('--json', help='write the result here')
    args = ap.parse_args(argv)
    if args.record:
        rec = json.load(open(args.record))
        got = for_plan(rec.get('level'), rec['plan'])
    elif args.level:
        got = for_plan(args.level, {})
    else:
        ap.error('--record or --level')
    print('%d seams, %d proposals, %d not placed' % (got['seams'], len(got['proposals']),
                                                     len(got['problems'])))
    for p in got['proposals']:
        print('  %s %s %.0f uu x %.0f: %s x%d' % (p['seam']['label'], ' / '.join(p['seam']['names']),
                                                 p['seam']['width'], p['seam']['run'],
                                                 p['cover']['mesh'], len(p['items'])))
    for p in got['problems']:
        print('  NOT PLACED %s %s %.0f uu: %s' % (p['seam']['label'], ' / '.join(p['seam']['names']),
                                                 p['seam']['width'], p['why']))
    if args.json:
        json.dump(got, open(args.json, 'w'), indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main())
