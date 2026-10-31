"""The level pieces a placed Crystal hides: what its mod's manifest says stands in its way.

The Crystal Defense pak carries `OMD/Content/Mods/CrystalDefense/RiftLord/Pieces.json`:

    {"objective_class": "/Game/...BP_Crystal.BP_Crystal_C",
     "pieces": [{"mesh": "<object path>", "location": [x, y, z], "rotation": [p, y, r],
                 "scale": [x, y, z]}],          # the crystal's own pieces, in actor space
     "park": {"radius": 40, "meshes": ["<object path>", ...], "chains_within": [x, y]},
     "foot": {"half_extent": [x, y, z]}}

On any level, a shipped piece whose mesh is one of `park.meshes` is parked (taken out of
play, as a removed piece is) when its pivot lies within `park.radius` of one of the
crystal's pieces (each moved by the placed crystal's matrix), or sideways within
`park.chains_within` (half extents in the crystal's own frame; without it, the foot's half
extent plus FOOT_MARGIN) of the crystal's origin. Other level pieces whose pivot stands
inside the foot (hitbox and obstruction) are reported (in_foot).
Nothing is listed here: meshes, radius and foot all come from the manifest of the mod pak
installed. The build adds the parked pieces to the plan's `removed` (with_parked); the
Map Editor hides them.
"""

import json
import math
import os

#: The manifest's place in the Crystal Defense pak.
MANIFEST = 'OMD/Content/Mods/CrystalDefense/RiftLord/Pieces.json'
#: Uu beyond the foot's half extent a park mesh is still in the crystal's way (the chains
#: anchored round the rift platform), when the manifest gives no `park.foot_margin`.
FOOT_MARGIN = 150.0

_CACHE = {}


def manifest():
    """The installed mod's manifest (dict), or None when the mod or its manifest is absent."""
    from riftlord.core import paksource
    from riftlord.mapeditor import crystal
    path = crystal.pak()
    if not path:
        return None
    st = os.stat(path)
    stamp = (path, st.st_size, st.st_mtime_ns)
    if _CACHE.get('stamp') != stamp:
        try:
            got = json.loads(paksource.read(path, MANIFEST).decode('utf-8'))
        except (Exception, SystemExit):              # noqa: BLE001
            got = None
        _CACHE.update(stamp=stamp, manifest=got)
    return _CACHE['manifest']


def _apply(m, p):
    """Point `p` (actor space) through the column-major plan matrix `m`."""
    x, y, z = (float(v) for v in p[:3])
    return (m[0] * x + m[4] * y + m[8] * z + m[12],
            m[1] * x + m[5] * y + m[9] * z + m[13],
            m[2] * x + m[6] * y + m[10] * z + m[14])


def _local(m, p):
    """World point `p` in the actor frame of `m` (rotation and translation; the crystal is
    never sized)."""
    d = [p[i] - m[12 + i] for i in range(3)]
    cols = [[m[c * 4 + r] for r in range(3)] for c in range(3)]
    out = []
    for col in cols:
        n = math.sqrt(sum(v * v for v in col)) or 1.0
        out.append(sum(d[r] * col[r] for r in range(3)) / (n * n))
    return out


def _leaf(path):
    return str(path).rsplit('.', 1)[-1].rsplit('/', 1)[-1]


def _package(path):
    return str(path).split('.', 1)[0]


def parked(level_name, matrix, man=None, plan=None):
    """[{id, mesh, at, why}] of the level's pieces a crystal at `matrix` parks, by the
    manifest `man` (default: the installed one). Pieces `plan` already removes are left out."""
    from riftlord.formats.omd3 import meshcat
    from riftlord.mapeditor import leveldata
    man = manifest() if man is None else man
    if not man or not matrix:
        return []
    park = man.get('park') or {}
    meshes = park.get('meshes') or []
    if not meshes:
        return []
    radius = float(park.get('radius') or 0.0)
    half = foot_half(man)
    box = park.get('chains_within') or [half[0] + FOOT_MARGIN, half[1] + FOOT_MARGIN]
    box = [float(v) for v in box][:2]
    spots = [_apply(matrix, p.get('location') or [0, 0, 0]) for p in man.get('pieces') or []]
    cat = meshcat.load()
    want = {}
    for path in meshes:
        want.setdefault(_leaf(path), set()).add(_package(path))
    gone = {int(r['id'] if isinstance(r, dict) else r) for r in (plan or {}).get('removed') or []}
    out = []
    for piece in leveldata.level(level_name).get('pieces') or []:
        leaf = piece.get('mesh')
        m = piece.get('matrix')
        if leaf not in want or not m or piece.get('id') in gone:
            continue
        info = cat.get(leaf) or {}
        if info.get('path'):
            pkg = '/Game' + info['path'].split('OMD/Content', 1)[-1]
            if pkg not in want[leaf]:
                continue
        at = (m[12], m[13], m[14])
        why = None
        if radius and any(math.dist(at, s) <= radius for s in spots):
            why = 'crystal piece'
        else:
            loc = _local(matrix, at)
            if abs(loc[0]) <= box[0] and abs(loc[1]) <= box[1]:
                why = 'round the crystal'
        if why:
            out.append({'id': piece['id'], 'mesh': leaf, 'at': [round(v, 1) for v in at],
                        'why': why})
    return sorted(out, key=lambda r: r['id'])


#: Yaws, in degrees, a crystal is tried at to line its pieces up with the level's own.
QUARTERS = (0.0, 90.0, 180.0, 270.0)


def aligned_yaw(level_name, at, yaw, man=None):
    """The yaw a crystal at `at` takes: of QUARTERS and `yaw` (the routes' approach), the one
    that stands the most of its pieces within `park.radius` of a level piece of the same
    mesh, then the nearest to them, then the nearest `yaw`; `yaw` when the manifest has no
    pieces or none line up."""
    from riftlord.mapeditor import crystal
    from riftlord.mapeditor import leveldata
    man = manifest() if man is None else man
    pieces = (man or {}).get('pieces') or []
    radius = float(((man or {}).get('park') or {}).get('radius') or 0.0)
    if not pieces or not radius:
        return yaw
    by_mesh = {}
    for p in leveldata.level(level_name).get('pieces') or []:
        if p.get('matrix'):
            by_mesh.setdefault(p.get('mesh'), []).append(p['matrix'][12:15])

    def score(y):
        """(pieces lined up, minus their summed distance to the level's)."""
        m = crystal.yaw_matrix(at, y)
        hit, off = 0, 0.0
        for piece in pieces:
            w = _apply(m, piece.get('location') or [0, 0, 0])
            near = min((math.dist(w, s) for s in by_mesh.get(_leaf(piece.get('mesh')), ())),
                       default=None)
            if near is not None and near <= radius:
                hit += 1
                off += near
        return hit, -round(off, 1)

    def turn(a, b):
        return abs((a - b + 180.0) % 360.0 - 180.0)
    best = max([yaw] + list(QUARTERS), key=lambda y: score(y) + (-turn(y, yaw),))
    return best if score(best)[0] else yaw


def foot_half(man=None):
    """The crystal foot's half extent [x, y, z] (hitbox and obstruction, round the actor's
    origin, its base at it) from the manifest, or None."""
    man = manifest() if man is None else man
    half = ((man or {}).get('foot') or {}).get('half_extent')
    if half is None:
        return None
    return [float(half)] * 3 if isinstance(half, (int, float)) else [float(v) for v in half]


#: The floor slab a piece pivot may sit in under the foot without standing in it, uu.
FLOOR_SLACK = 10.0


def in_foot(level_name, matrix, plan=None, man=None):
    """[{id, mesh, at}] of the level's pieces, not parked or removed, whose pivot stands
    inside the crystal's foot (its hitbox and obstruction) above the floor."""
    from riftlord.mapeditor import leveldata
    man = manifest() if man is None else man
    half = foot_half(man)
    if not half or not matrix:
        return []
    skip = {r['id'] for r in parked(level_name, matrix, man, plan)}
    skip |= {int(r['id'] if isinstance(r, dict) else r) for r in (plan or {}).get('removed') or []}
    out = []
    for piece in leveldata.level(level_name).get('pieces') or []:
        m = piece.get('matrix')
        if not m or piece.get('id') in skip:
            continue
        loc = _local(matrix, (m[12], m[13], m[14]))
        if abs(loc[0]) <= half[0] and abs(loc[1]) <= half[1] and \
                FLOOR_SLACK < loc[2] <= 2 * half[2]:
            out.append({'id': piece['id'], 'mesh': piece.get('mesh'),
                        'at': [round(v, 1) for v in (m[12], m[13], m[14])]})
    return out


def plan_parked(level_name, plan):
    """parked() for the plan's crystal, or []."""
    from riftlord.mapeditor import crystal
    item = crystal.piece(plan)
    return parked(level_name, item['matrix'], plan=plan) if item else []


def with_parked(level_name, plan, report=None):
    """`plan`, or a copy whose `removed` also holds the pieces its crystal parks (listed in
    `report['crystalParked']`)."""
    got = plan_parked(level_name, plan) if plan else []
    if report is not None:
        report['crystalParked'] = got
    if not got:
        return plan
    out = dict(plan)
    out['removed'] = sorted({int(r['id'] if isinstance(r, dict) else r)
                             for r in plan.get('removed') or []} | {r['id'] for r in got})
    return out


def gates(level_name, plan):
    """[(name, ok, detail)]: the pieces the crystal parks, listed (the manifest present
    when the mod is)."""
    from riftlord.mapeditor import crystal
    if not crystal.piece(plan):
        return []
    man = manifest()
    if man is None:
        return [('Crystal manifest %s in the mod pak' % MANIFEST, False, 'missing')]
    got = plan_parked(level_name, plan)
    inside = in_foot(level_name, crystal.piece(plan)['matrix'], plan)
    return [('Crystal parks %d level piece(s)' % len(got), True,
             ', '.join('%d %s (%s)' % (r['id'], r['mesh'], r['why']) for r in got)),
            ('warning: %d level piece(s) stand inside the Crystal\'s foot' % len(inside)
             if inside else 'no level piece stands inside the Crystal\'s foot', True,
             ', '.join('%d %s at %s' % (r['id'], r['mesh'], r['at']) for r in inside[:8]))]
