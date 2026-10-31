"""The Crystal's hitbox against the walkable floor round it: HitX / HitY / HitDrop, and warnings.

The Crystal (Crystal Defense, a trap) is attacked at its hitbox, a box round the actor's origin
(its base at the origin) of half extents HitX x HitY x Z along the actor's axes; a NavPower
obstruction of the same box carves the walkable graph, so paths end at the box's edge and the
enemies stand outside it to hit it. The mode's own box (the manifest's foot half extent,
486 x 549: the foot art SM_Crystal_Cluster 772 x 898 and 100 uu each side) suits a large rift
platform; on a smaller one its sides stand past the floor and the enemies stop out of reach.

`fit` measures each side on the final walkable graph: a side fits when the band just outside it
(STANDOFF beyond the edge, the whole length of the side) has floor an attacker standing there
reaches the box from (its capsule overlaps the box's height). A side that does not fit shrinks
its axis, in STEP steps, down to the foot art's half size (FOOT_ART_HALF: a smaller box lets
enemies walk into the visible cluster); still not fitting there, the Crystal is too big for its
platform. A user's HitX / HitY (plan `crystal.hitX` / `hitY`) wins over the fitted value
(crystal.place writes it); the build writes the fitted value only where the user gave none
(`install`).

HitDrop (agreed with Crystal Defense, 2026-10-30) is how far the hitbox and obstruction reach
below the actor's root: a Crystal standing on a rift base that is not walkable floor (North
Wings: its root 56 uu over the floor) reaches down to the floor round it. The build writes
max(0, root z - the highest walkable floor under the box) where the user set none (`floor_drop`);
floor deeper than DROP_LIMIT is not the Crystal's own floor, and the build warns instead.
"""

import math

#: The foot art's half size, SM_Crystal_Cluster 772 x 898 (Crystal Defense dist/report.json):
#: the smallest box the fit writes.
FOOT_ART_HALF = (386.0, 449.0)
#: The mode's box when the manifest gives none (Pieces.json foot.half_extent, 2026-10-30).
DEFAULT_HALF = (486.0, 549.0, 203.95)
#: Where an attacker stands, beyond the box's edge: the agent radius (DefaultEngine.ini
#: AgentRadius 42) and 8 uu so the sample falls clear of the edge itself.
STANDOFF = 50.0
#: An attacker's capsule height (DefaultEngine.ini AgentHeight 192): floor this far under the
#: box's bottom still reaches it.
AGENT_HEIGHT = 192.0
#: Spacing of the samples along a side, and of the fit's shrink steps (uu).
SAMPLE = 25.0
STEP = 5.0
#: A height a fitted or user half extent may take (uu).
RANGE = (FOOT_ART_HALF[0], 5000.0)
#: The plan fields and the class variables they write.
FIELDS = (('hitX', 'HitX', 0), ('hitY', 'HitY', 1))
DROP_KEY, DROP_VAR = 'hitDrop', 'HitDrop'
#: The deepest HitDrop the build sets or takes without a warning: the box's own height
#: (2 x 203.95). A floor further down is not the platform the Crystal stands on, and a box
#: reaching further below its root than it stands above it no longer frames the art.
DROP_LIMIT = 2 * DEFAULT_HALF[2]
#: A user HitDrop the save takes (uu).
DROP_RANGE = (0.0, 5000.0)


class Floor:
    """The walkable polygons of one agent's graph, with a coarse grid to find them by."""

    def __init__(self, polys, cell=500.0):
        self.polys = polys
        self.cell = cell
        self.grid = {}
        for i, p in enumerate(polys):
            xs, ys = [v[0] for v in p], [v[1] for v in p]
            for gx in range(int(min(xs) // cell), int(max(xs) // cell) + 1):
                for gy in range(int(min(ys) // cell), int(max(ys) // cell) + 1):
                    self.grid.setdefault((gx, gy), []).append(i)

    def heights(self, x, y):
        """[z] of each polygon under (x, y)."""
        from riftlord.build.navigation import navsurf, navsurfgen
        out = []
        for i in self.grid.get((int(x // self.cell), int(y // self.cell)), ()):
            p = self.polys[i]
            if navsurf._inside_xy(p, x, y):
                out.append(navsurfgen.plane_z(p, x, y))
        return out


def floor_of(buf):
    """The first agent's walkable floor in a surface buffer."""
    from riftlord.build.navigation import navsurf
    head = navsurf.graphs(buf)[0]
    return Floor([list(s['verts']) for s in navsurf.sections(buf, head)])


def frame(matrix):
    """(origin, yaw degrees) of an actor matrix (row vectors, translation last)."""
    return (matrix[12], matrix[13], matrix[14]), math.degrees(math.atan2(matrix[1], matrix[0]))


def _world(origin, yaw, lx, ly):
    c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    return origin[0] + lx * c - ly * s, origin[1] + lx * s + ly * c


def _span(length):
    n = max(1, int(math.ceil(2 * length / SAMPLE)))
    return [-length + 2 * length * k / n for k in range(n + 1)]


def side_floor(floor, matrix, half, side, drop=0.0):
    """(samples with reachable floor, samples) along one side ('+x', '-x', '+y', '-y') of a
    box of half extents `half` (x, y, z) at `matrix`, reaching `drop` below its root,
    STANDOFF outside its edge."""
    origin, yaw = frame(matrix)
    bottom, top = origin[2] - drop, origin[2] + 2 * half[2]
    axis, sign = (0 if side[1] == 'x' else 1), (1.0 if side[0] == '+' else -1.0)
    out = sign * (half[axis] + STANDOFF)
    along = _span(half[1 - axis])
    hit = 0
    for t in along:
        lx, ly = (out, t) if axis == 0 else (t, out)
        x, y = _world(origin, yaw, lx, ly)
        if any(bottom - AGENT_HEIGHT < z < top for z in floor.heights(x, y)):
            hit += 1
    return hit, len(along)


SIDES = ('+x', '-x', '+y', '-y')


def fit(floor, matrix, half=None, drop=0.0):
    """{'half': [x, y] that fits (floored at FOOT_ART_HALF), 'default': [x, y, z],
    'sides': {side: (with floor, samples)} at the default, 'short': {side: uu the default
    stands past the floor (0 fits)}, 'tooBig': [sides that do not fit even at the foot]}."""
    half = list(half or DEFAULT_HALF)
    sides = {s: side_floor(floor, matrix, half, s, drop) for s in SIDES}

    def fits(hx, hy):
        box = [hx, hy, half[2]]
        return all((lambda r: r[0] == r[1])(side_floor(floor, matrix, box, s, drop))
                   for s in SIDES)

    def steps(axis):
        out, h = [], half[axis]
        while h > FOOT_ART_HALF[axis] + 1e-6:
            out.append(round(h, 1))
            h -= STEP
        return out + [FOOT_ART_HALF[axis]]
    got = [half[0], half[1]]
    if not fits(*got):
        #: the largest box that fits, each axis from the mode's down to the foot art; a side's
        #: corners belong to both axes, so the two are searched together
        best = None
        ys = steps(1)
        for hx in steps(0):
            for hy in ys:
                if fits(hx, hy):
                    if best is None or hx * hy > best[0] * best[1]:
                        best = (hx, hy)
                    break
        got = list(best) if best else list(FOOT_ART_HALF)
    box = got + [half[2]]
    too_big = [s for s in SIDES
               if (lambda r: r[0] < r[1])(side_floor(floor, matrix, box, s, drop))]
    #: how far each side that stood past the floor at the mode's size moved in
    short = {s: round(half[0 if s[1] == 'x' else 1] - got[0 if s[1] == 'x' else 1], 1)
             if sides[s][0] < sides[s][1] else 0.0 for s in SIDES}
    return {'half': got, 'default': half, 'sides': sides, 'short': short, 'tooBig': too_big}


def floor_drop(floor, matrix, half=None, limit=DROP_LIMIT):
    """(HitDrop: uu from the root down to the highest walkable floor under the box's footprint,
    0 when floor lies at or above the root inside the box; that floor's z), sampled over the
    footprint, the floor no deeper than `limit` under the root; (None, None) without any."""
    half = list(half or DEFAULT_HALF)
    origin, yaw = frame(matrix)
    root, top = origin[2], origin[2] + 2 * half[2]
    under = []
    for lx in _span(half[0]):
        for ly in _span(half[1]):
            x, y = _world(origin, yaw, lx, ly)
            under += [z for z in floor.heights(x, y) if root - limit <= z < top]
    if not under:
        return None, None
    high = max(under)
    return round(max(0.0, root - high), 1), round(high, 1)


def user_half(item):
    """{class variable: value} of the user's own HitX / HitY (above 0) and HitDrop (0 too) on a
    crystal item."""
    raw = (item or {}).get('crystal') or {}
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)  # noqa: E731
    out = {prop: float(raw[key]) for key, prop, _a in FIELDS if num(raw.get(key)) and raw[key] > 0}
    if num(raw.get(DROP_KEY)) and raw[DROP_KEY] >= 0:
        out[DROP_VAR] = float(raw[DROP_KEY])
    return out


def clean(raw, label='Hitbox'):
    """A plan HitX / HitY value: None (automatic) or a number in RANGE."""
    if raw is None or raw == '' or raw == 0:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        raise ValueError('%s must be a number' % label)
    if v != v or not RANGE[0] <= v <= RANGE[1]:
        raise ValueError('%s must be between %g and %g (the foot art is %g x %g)'
                         % (label, RANGE[0], RANGE[1], FOOT_ART_HALF[0], FOOT_ART_HALF[1]))
    return round(v, 1)


def clean_drop(raw, label=DROP_VAR):
    """A plan HitDrop: None (automatic) or a number in DROP_RANGE (0 is the user's own)."""
    if raw is None or raw == '':
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        raise ValueError('%s must be a number' % label)
    if v != v or not DROP_RANGE[0] <= v <= DROP_RANGE[1]:
        raise ValueError('%s must be between %g and %g' % (label, DROP_RANGE[0], DROP_RANGE[1]))
    return round(v, 1)


def warnings(result, drop, user):
    """[text] for the build console: sides past the floor and the fitted HitX / HitY, a
    Crystal too big for its platform, a user value that does not fit, no walkable floor within
    DROP_LIMIT under the Crystal (`drop` None, the build sets no HitDrop), and a user HitDrop
    past DROP_LIMIT."""
    out = []
    names = {'+x': 'front (+X)', '-x': 'back (-X)', '+y': 'left (+Y)', '-y': 'right (-Y)'}
    short = [s for s in SIDES if result['short'][s] > 0 or s in result['tooBig']]
    if short:
        out.append('Crystal hitbox stands past the walkable floor on its %s side%s: HitX %g, '
                   'HitY %g fit (the default is %g x %g)%s' % (
                       ', '.join(names[s] for s in short), 's' * (len(short) > 1),
                       result['half'][0], result['half'][1], result['default'][0],
                       result['default'][1],
                       '; the user\'s %s win%s' % (', '.join('%s %g' % kv for kv in user.items()),
                                                   's' * (len(user) == 1)) if user else ''))
    if result['tooBig']:
        out.append('Crystal is too big for its platform: even at the foot art\'s size (%g x %g) '
                   'its %s side%s %s no floor beside %s' % (
                       FOOT_ART_HALF[0], FOOT_ART_HALF[1],
                       ', '.join(names[s] for s in result['tooBig']),
                       's' * (len(result['tooBig']) > 1),
                       'have' if len(result['tooBig']) > 1 else 'has',
                       'them' if len(result['tooBig']) > 1 else 'it'))
    for prop, v in sorted(user.items()):
        axis = 0 if prop == 'HitX' else 1
        if v > result['half'][axis] + 1e-6 and result['short'][('+x', '+y')[axis]] + \
                result['short'][('-x', '-y')[axis]] > 0:
            out.append('Crystal %s %g (set on the piece) stands past the walkable floor; %g fits'
                       % (prop, v, result['half'][axis]))
    if DROP_VAR in user:
        if user[DROP_VAR] > DROP_LIMIT:
            out.append('Crystal HitDrop %g (set on the piece) reaches deeper than the box is tall '
                       '(%g)' % (user[DROP_VAR], DROP_LIMIT))
    elif drop is None:
        out.append('Crystal has no walkable floor within %g uu under it: no HitDrop is set, and '
                   'its hitbox does not reach the floor' % DROP_LIMIT)
    return out


def notes(drop, floor_z, root_z, user):
    """[text], for information: the HitDrop the Crystal is built with, and from what."""
    if DROP_VAR in user:
        return ['Crystal HitDrop %g (set on the piece)' % user[DROP_VAR]]
    if drop:
        return ['Crystal HitDrop %g: its root z %g stands over the walkable floor at z %g'
                % (drop, root_z, floor_z)]
    return []


def to_write(result, user, half, drop=None):
    """{class variable: value} the build writes after the fit: each fitted half extent that
    differs from the mode's, on an axis the user did not set, and the found HitDrop above 0
    unless the user set one (crystal.place wrote the user's)."""
    out = {prop: result['half'][axis] for _k, prop, axis in FIELDS
           if prop not in user and abs(result['half'][axis] - half[axis]) > 1e-6}
    if DROP_VAR not in user and drop:
        out[DROP_VAR] = drop
    return out


def resolve(floor, matrix, half, user):
    """(fit result, found HitDrop or None, its floor z, the HitDrop the box is built with)."""
    drop, floor_z = floor_drop(floor, matrix, half)
    used = user.get(DROP_VAR, drop or 0.0)
    return fit(floor, matrix, half, used), drop, floor_z, used


def install(uasset, uexp, plan, report, object_name='level'):
    """(uasset, uexp) of a built level with the Crystal's fitted HitX / HitY written where the
    user set none, measured on the level's final walkable graph; the result and the warnings
    go in `report`."""
    from riftlord.build import levelbuild
    from riftlord.build.navigation import navsurfextend
    from riftlord.mapeditor import crystal, crystalpark
    import struct
    item = crystal.piece(plan)
    placed = ((report or {}).get('crystal') or {}).get('placed')
    if item is None or not placed:
        return uasset, uexp
    try:
        half = crystalpark.foot_half() or list(DEFAULT_HALF)
    except (Exception, SystemExit):                  # noqa: BLE001
        half = list(DEFAULT_HALF)
    floor = floor_of(navsurfextend._surface_of(uasset, uexp))
    user = user_half(item)
    result, drop, floor_z, _used = resolve(floor, item['matrix'], half, user)
    write = to_write(result, user, half, drop)
    report.setdefault('crystal', {})['hitbox'] = {
        'default': half[:2], 'fitted': result['half'], 'drop': drop, 'user': user,
        'written': write, 'short': result['short'], 'tooBig': result['tooBig']}
    report.setdefault('warnings', []).extend(warnings(result, drop, user))
    report.setdefault('notes', []).extend(notes(drop, floor_z, round(item['matrix'][14], 1), user))
    if not write:
        return uasset, uexp
    b = levelbuild.Builder.around(uasset, uexp, name=object_name)
    actor = next(i + 1 for i, e in enumerate(b.pkg.exports)
                 if b.pkg.names[struct.unpack_from('<I', e['meta'], 16)[0]][0] == placed)
    for prop, v in write.items():
        b.set_property(actor, prop, 'FloatProperty', struct.pack('<f', v))
    return b.write()


def gate(uasset, uexp, item, vals):
    """(name, ok, detail): the built Crystal's HitX / HitY / HitDrop are the user's, else the
    fit on the built level's own walkable graph (unset where it keeps the mode's box)."""
    from riftlord.build.navigation import navsurfextend
    from riftlord.mapeditor import crystalpark
    try:
        half = crystalpark.foot_half() or list(DEFAULT_HALF)
    except (Exception, SystemExit):                  # noqa: BLE001
        half = list(DEFAULT_HALF)
    user = user_half(item)
    result, drop, _fz, _used = resolve(floor_of(navsurfextend._surface_of(uasset, uexp)),
                                       item['matrix'], half, user)
    bad = []
    shown = []
    rows = [(prop, result['half'][axis], half[axis]) for _k, prop, axis in FIELDS]
    rows.append((DROP_VAR, drop or 0.0, 0.0))
    for prop, fitted, default in rows:
        want = user.get(prop, fitted)
        got = vals.get(prop, default)
        shown.append('%s %g%s' % (prop, got, ' (user)' if prop in user else
                                  '' if prop in vals else ' (mode default)'))
        if abs(got - want) > 0.05:
            bad.append('%s %g, want %g' % (prop, got, want))
    return ('Crystal hitbox: %s' % ', '.join(shown), not bad, '; '.join(bad))


def editor_fit(level_name, matrix):
    """{'half': fitted [x, y], 'default': [x, y], 'short': {...}, 'tooBig': [...],
    'drop': the HitDrop found, 'floorZ': z} on the level's own walkable graph (the build fits again on
    the final one), for the Crystal panel's placeholders."""
    from riftlord.build.navigation import navsurf
    from riftlord.mapeditor import crystalpark
    try:
        half = crystalpark.foot_half() or list(DEFAULT_HALF)
    except (Exception, SystemExit):                  # noqa: BLE001
        half = list(DEFAULT_HALF)
    floor = floor_of(navsurf.buffer(level_name))
    result, drop, floor_z, _used = resolve(floor, matrix, half, {})
    return {'half': result['half'], 'default': half[:2], 'short': result['short'],
            'tooBig': result['tooBig'], 'drop': drop or 0.0, 'floorZ': floor_z,
            'dropLimit': DROP_LIMIT, 'dropRange': list(DROP_RANGE),
            'footArt': list(FOOT_ART_HALF), 'range': list(RANGE)}
