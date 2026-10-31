"""NavPower obstruction boxes over the walls a Map Editor plan places and the floor it
removes, so ground enemies route around them.

Each box is a plain `Actor` whose root is a `NavPowerObstructionComponent`, the
component doors and barricades carry. It cuts the walkable graph at runtime (BeginPlay
calls CreateObstruction); the cooked graph is left as shipped. Flyers (volumetric
graph) and players (not pathed) ignore it, and it has no collision.

Boxes go over: added and moved wall-like pieces (WALL_NAME, at least WALL_MIN_HEIGHT
tall), and removed floor that nothing in the final level covers (navsurfcarve's
cut_boxes). A box is left out, with a note, when it touches no walkable floor, holds a
route point, spawner or door, or cuts a ground route leg.

    python3 navobstruct.py --record <mission id>        # dry run on a Missions record

API: `boxes_for(level_name, plan)` -> [box]; `place(uasset, uexp, level_name, plan)` ->
(uasset, uexp, notes).
"""

import argparse
import heapq
import math
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.build.navigation import navsurf  # noqa: E402
from riftlord.build.navigation import navsurfcarve  # noqa: E402

#: Builds place obstruction boxes only when this is True.
ENABLED = False

#: Mesh or kit names that are walls or solid like them (rocks are not: their boxes are
#: far larger than the rock).
WALL_NAME = re.compile(r'wall|column|pillar|portcullis_solid|railing', re.I)
#: ... except these, which have an opening or are trim on another piece.
NOT_WALL = re.compile(r'arch|door|trim|sill|floor|stair|torch|fx', re.I)
#: A wall-like piece must be at least this tall to block, in uu (the agent steps 48).
WALL_MIN_HEIGHT = 90.0
#: A wall's box reaches this far below the piece, onto the floor it stands on, in uu.
WALL_FOOT = 50.0
#: A removed-floor box reaches this far below the removed floor's top, in uu.
HOLE_BELOW = 100.0
#: ... and this far above it, in uu (navsurfcarve.SUPPORT_BELOW plus the agent height).
HOLE_ABOVE = 320.0
#: A route point or spawner closer than this to a box (in xy) keeps it out, in uu (the
#: agent radius plus a margin).
KEEP_CLEAR = 60.0
#: ... and a door this close (its centre inside the box), in uu.
DOOR_CLEAR = 0.0
#: A walkable polygon counts as cut by a box when this share of its area lies under it.
CUT_SHARE = 0.5
#: Below this share a box is taken to touch no walkable floor.
TOUCH_SHARE = 0.05

#: PassthroughFlags of a placed box: the shipped interior door's (blocks every mover
#: whose obstacle mask has bit 4, which all ground AI masks do).
PASSTHROUGH = 4
#: ObstructionFlags of a placed box (CreateObstruction overwrites it with PASSTHROUGH).
OBSTRUCTION = 4
#: Leave the flags at the class defaults, as the shipped exterior gate's box does.
SHIPPED_DEFAULTS = False
#: Turn the box's collision off (players and projectiles pass through it).
NO_COLLISION = True

#: The donor of a bare Actor with one instance component: (level, actor name).
DONOR = ('../../../OMD/Content/Maps/UI/Levels/MainMenu_V2', 'EnemiesSpawnRoot')
#: Name stem of the placed actors.
ACTOR_STEM = 'RiftLordObstruct'


# -- which boxes ----------------------------------------------------------------------

def _oriented(local, rot, t, name, why):
    """A box dict from a local (lo, hi), a rows rotation with scale, and a translation."""
    lo, hi = local
    cols = [(rot[0][j], rot[1][j], rot[2][j]) for j in range(3)]
    scale = [math.sqrt(sum(c * c for c in col)) or 1.0 for col in cols]
    units = [tuple(c / s for c in col) for col, s in zip(cols, scale)]
    mid = [(lo[k] + hi[k]) / 2 for k in range(3)]
    half = [(hi[k] - lo[k]) / 2 * scale[k] for k in range(3)]
    centre = [sum(rot[r][k] * mid[k] for k in range(3)) + t[r] for r in range(3)]
    corners = []
    for dx in (-1, 1):
        for dy in (-1, 1):
            for dz in (-1, 1):
                c = (lo[0] if dx < 0 else hi[0], lo[1] if dy < 0 else hi[1],
                     lo[2] if dz < 0 else hi[2])
                corners.append([sum(rot[r][k] * c[k] for k in range(3)) + t[r]
                                for r in range(3)])
    wlo = [min(c[k] for c in corners) for k in range(3)]
    whi = [max(c[k] for c in corners) for k in range(3)]
    upright = abs(units[0][2]) < 0.02 and abs(units[1][2]) < 0.02
    if upright:
        yaw = math.degrees(math.atan2(units[0][1], units[0][0]))
    else:
        # tilted: the world box, unrotated
        yaw = 0.0
        centre = [(wlo[k] + whi[k]) / 2 for k in range(3)]
        half = [(whi[k] - wlo[k]) / 2 for k in range(3)]
    return {'name': name, 'why': why, 'centre': centre, 'half': half, 'yaw': yaw,
            'lo': wlo, 'hi': whi}


def _grow_down(box, by):
    box['centre'][2] -= by / 2
    box['half'][2] += by / 2
    box['lo'][2] -= by
    return box


def is_wall(name, lo, hi):
    """True for a wall-like piece standing at least WALL_MIN_HEIGHT tall."""
    return (bool(WALL_NAME.search(name or '')) and not NOT_WALL.search(name or '')
            and hi[2] - lo[2] >= WALL_MIN_HEIGHT)


def wall_boxes(level_name, plan, cat=None):
    """Boxes over wall-like pieces the plan adds or moves."""
    from riftlord.formats.omd3 import meshcat
    from riftlord.mapeditor import leveldata
    from riftlord.mapeditor import scratch
    cat = cat or meshcat.load()
    out = []
    for item in plan.get('added') or []:
        name = item.get('name') or item.get('cls') or ''
        if not item.get('matrix'):
            continue
        try:
            if item.get('kind') == 'kit':
                got = scratch._piece_local(item, cat)
                if got is None:
                    continue
                local, rot, t = got
            else:
                info = cat.get(name)
                if not info:
                    continue
                o, e = info['origin'], info['extent']
                local = ([o[k] - e[k] for k in range(3)], [o[k] + e[k] for k in range(3)])
                rot, t = scratch._piece_matrix(item)
        except (Exception, SystemExit):              # noqa: BLE001
            continue
        box = _oriented(local, rot, t, name, 'added wall')
        if is_wall(name, box['lo'], box['hi']):
            out.append(_grow_down(box, WALL_FOOT))
    pieces = None
    for edit in plan.get('edits') or []:
        if not isinstance(edit, dict) or 'matrix' not in edit:
            continue
        name = edit.get('mesh')
        if name is None:
            if pieces is None:
                pieces = {p.get('id', i): p for i, p in
                          enumerate(leveldata.level(level_name).get('pieces') or [])}
            name = (pieces.get(int(edit['id'])) or {}).get('mesh')
        info = cat.get(name)
        if not info:
            continue
        o, e = info['origin'], info['extent']
        local = ([o[k] - e[k] for k in range(3)], [o[k] + e[k] for k in range(3)])
        rot, t = scratch._piece_matrix(edit)
        box = _oriented(local, rot, t, name, 'moved wall')
        if is_wall(name, box['lo'], box['hi']):
            out.append(_grow_down(box, WALL_FOOT))
    return out


def hole_boxes(level_name, plan):
    """Boxes over removed floor that no floor of the final level covers."""
    gone, cover = navsurfcarve.floor_boxes(level_name, plan)
    out = []
    for lo, hi in navsurfcarve.cut_boxes(gone, cover):
        top = hi[2]
        blo = [lo[0], lo[1], top - HOLE_BELOW]
        bhi = [hi[0], hi[1], top + HOLE_ABOVE]
        out.append({'name': 'removed floor', 'why': 'removed floor',
                    'centre': [(blo[k] + bhi[k]) / 2 for k in range(3)],
                    'half': [(bhi[k] - blo[k]) / 2 for k in range(3)], 'yaw': 0.0,
                    'lo': blo, 'hi': bhi})
    return out


def boxes_for(level_name, plan):
    """Every candidate box, walls first."""
    return wall_boxes(level_name, plan) + hole_boxes(level_name, plan)


# -- gates ----------------------------------------------------------------------------

def _footprint(box):
    """The box's xy outline, counter-clockwise."""
    c, h, a = box['centre'], box['half'], math.radians(box['yaw'])
    ux, uy = math.cos(a), math.sin(a)
    vx, vy = -uy, ux
    return [(c[0] + sx * h[0] * ux + sy * h[1] * vx, c[1] + sx * h[0] * uy + sy * h[1] * vy)
            for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]


def _clip_convex(poly, clip):
    """Polygon `poly` (xy) clipped by convex counter-clockwise `clip`."""
    out = list(poly)
    n = len(clip)
    for k in range(n):
        if not out:
            break
        ax, ay = clip[k]
        bx, by = clip[(k + 1) % n]

        def side(p):
            return (bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax)
        src, out = out, []
        for j in range(len(src)):
            p, q = src[j], src[(j + 1) % len(src)]
            sp, sq = side(p), side(q)
            if sp >= 0:
                out.append(p)
            if (sp >= 0) != (sq >= 0):
                t = sp / (sp - sq)
                out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return out


def _area2(poly):
    return abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1]
                   - poly[(i + 1) % len(poly)][0] * poly[i][1]
                   for i in range(len(poly)))) / 2 if len(poly) >= 3 else 0.0


def share_under(poly, box):
    """Share of walkable polygon `poly`'s area under `box`, when its height is inside."""
    z = sum(v[2] for v in poly) / len(poly)
    if not box['lo'][2] <= z <= box['hi'][2]:
        return 0.0
    flat = [(v[0], v[1]) for v in poly]
    total = _area2(flat)
    if total <= 0:
        return 0.0
    return _area2(_clip_convex(flat, _footprint(box))) / total


def _near(box, p, margin):
    """True when point p lies within the box grown by `margin` in xy, at its height."""
    if not box['lo'][2] - margin <= p[2] <= box['hi'][2] + margin:
        return False
    a = math.radians(box['yaw'])
    dx, dy = p[0] - box['centre'][0], p[1] - box['centre'][1]
    lx = dx * math.cos(a) + dy * math.sin(a)
    ly = -dx * math.sin(a) + dy * math.cos(a)
    return abs(lx) <= box['half'][0] + margin and abs(ly) <= box['half'][1] + margin


def route_legs(level_name, plan):
    """([(a, b)] every ground route leg in the final level, [(point, margin)] to keep
    clear of boxes)."""
    from riftlord.build.navigation import navsurfextend
    from riftlord.mapeditor import planactors
    level = planactors.level_actors(level_name)
    nodes, _removed, _p = planactors.graph(plan, level)
    starts = []
    for sp in plan.get('spawners') or []:
        if sp.get('start') in nodes:
            starts.append((tuple(sp['at']), sp['start']))
    wiring = {w['name']: w for w in plan.get('wiring') or []}
    for name, sp in level['spawners'].items():
        w = wiring.get(name) or {}
        if w.get('gone'):
            continue
        start = w['start'] if 'start' in w else sp.get('start')
        if start in nodes:
            starts.append((tuple(w.get('at') or sp['location']), start))
    legs, keep, seen = [], [], set()
    for at, first in starts:
        floor = (at[0], at[1], at[2] - navsurfextend.SPAWNER_ABOVE_FLOOR)
        keep.append((floor, KEEP_CLEAR))
        legs.append((floor, nodes[first]['at']))
        todo = [first]
        while todo:
            cur = todo.pop()
            if cur in seen:
                continue
            seen.add(cur)
            node = nodes[cur]
            keep.append((node['at'], KEEP_CLEAR))
            if node['kind'] == 'plan' and node['point'].get('flyersOnly'):
                continue
            for b in node['next']:
                if b['to'] in nodes:
                    legs.append((node['at'], nodes[b['to']]['at']))
                    todo.append(b['to'])
    for door in plan.get('doors') or []:
        keep.append((tuple(door['at']), DOOR_CLEAR))
    for name, door in (level.get('doors') or {}).items():
        w = wiring.get(name) or {}
        loc = w.get('at') or (door.get('location') if isinstance(door, dict) else None)
        if loc and not w.get('gone'):
            keep.append((tuple(loc), DOOR_CLEAR))
    return legs, keep


def _walker(buf):
    """walk(a, b, blocked) -> True when the first agent's floor joins a and b."""
    from riftlord.build.navigation import navsurfextend
    head = navsurf.graphs(buf)[0]
    secs = navsurf.sections(buf, head)
    polys = [list(s['verts']) for s in secs]
    adj = navsurf.adjacency(buf, head, secs)

    def walk(a, b, blocked):
        sa, sb = navsurfextend.on_surface(polys, a), navsurfextend.on_surface(polys, b)
        if sa is None or sb is None:
            return None
        seen, todo = {sa}, [sa]
        while todo:
            i = todo.pop()
            if i == sb:
                return True
            for j in adj[i]:
                if j not in seen and j not in blocked:
                    seen.add(j)
                    todo.append(j)
        return False
    return polys, walk


def choose(buf, boxes, legs, keep):
    """(kept boxes, notes): boxes past the gates, in order, each tested with those
    already kept."""
    polys, walk = _walker(buf)
    open_legs = [(a, b) for a, b in legs if walk(a, b, set())]
    kept, notes, blocked = [], [], set()
    for box in boxes:
        label = '%s %s at (%.0f, %.0f, %.0f)' % ((box['why'], box['name']) + tuple(box['centre']))
        shares = [share_under(p, box) for p in polys]
        if max(shares or [0.0]) < TOUCH_SHARE:
            continue
        hit = next((p for p, margin in keep if _near(box, p, margin)), None)
        if hit is not None:
            notes.append('no obstruction over %s: a route point, spawner or door is at '
                         '(%.0f, %.0f, %.0f)' % ((label,) + tuple(hit)))
            continue
        cut = {i for i, s in enumerate(shares) if s >= CUT_SHARE}
        trial = blocked | cut
        broken = next(((a, b) for a, b in open_legs
                       if not walk(a, b, trial)), None)
        if broken is not None:
            notes.append('no obstruction over %s: it would cut the ground route between '
                         '(%.0f, %.0f, %.0f) and (%.0f, %.0f, %.0f)'
                         % ((label,) + tuple(broken[0]) + tuple(broken[1])))
            continue
        blocked = trial
        kept.append(box)
    return kept, notes


# -- writing --------------------------------------------------------------------------

def _donor():
    from riftlord.core import gamefiles
    from riftlord.build import levelbuild
    from riftlord.formats.ue.uassetio import Package
    ua, ux = gamefiles.package(DONOR[0])
    src = Package(ua, ux)
    src.load_payloads()
    for i in range(len(src.exports)):
        if levelbuild._name_of(src, i) == DONOR[1]:
            return src, i
    raise SystemExit('obstruction donor %s not found in %s' % (DONOR[1], DONOR[0]))


def write_boxes(b, boxes):
    """Add one obstruction actor per box to levelbuild.Builder `b`; their names."""
    from riftlord.build import levelbuild
    src, actor_index = _donor()
    taken = {levelbuild._name_of(b.pkg, i) for i in range(len(b.pkg.exports))}
    cls = b._import('/Script/CoreUObject', 'Class',
                    b._import('/Script/CoreUObject', 'Package', 0, '/Script/NavPower'),
                    'NavPowerObstructionComponent')
    pkg_idx = b._import('/Script/CoreUObject', 'Package', 0, '/Script/NavPower')
    tmpl = b._import('/Script/NavPower', 'NavPowerObstructionComponent', pkg_idx,
                     'Default__NavPowerObstructionComponent')
    names = []
    for n, box in enumerate(boxes):
        name = '%s%s' % (ACTOR_STEM, _letters(n))
        while name in taken:
            name += 'X'
        taken.add(name)
        actor = b.lift_actor(src, actor_index, rename=name)
        comp = b._root_component(actor)
        if not comp:
            raise SystemExit('obstruction donor has no root component')
        e = b.pkg.exports[comp - 1]
        meta = bytearray(e['meta'])
        old_cls, _sup, old_tmpl = struct.unpack_from('<3i', meta, 0)
        struct.pack_into('<i', meta, levelbuild.CLASS_AT, cls)
        struct.pack_into('<i', meta, 8, tmpl)
        first = struct.unpack_from('<i', meta, 84)[0]
        count = sum(struct.unpack_from('<4i', meta, 88))
        for k in range(first, first + count if first >= 0 else first):
            v = b.pkg.preload[k]
            b.pkg.preload[k] = cls if v == old_cls else tmpl if v == old_tmpl else v
        e['meta'] = bytes(meta)
        b.set_property(comp, 'RelativeLocation', 'StructProperty',
                       struct.pack('<3f', *box['centre']), 'Vector')
        b.set_property(comp, 'RelativeRotation', 'StructProperty',
                       struct.pack('<3f', 0.0, box['yaw'], 0.0), 'Rotator')
        b.set_property(comp, 'BoxExtent', 'StructProperty',
                       struct.pack('<3f', *box['half']), 'Vector')
        if not SHIPPED_DEFAULTS:
            b.set_property(comp, 'PassthroughFlags', 'ByteProperty', bytes([PASSTHROUGH]))
            b.set_property(comp, 'ObstructionFlags', 'UInt32Property',
                           struct.pack('<I', OBSTRUCTION))
        if NO_COLLISION:
            b.set_bool(comp, 'bGenerateOverlapEvents', False)
            b.set_property(comp, 'BodyInstance', 'StructProperty', _no_collision(b),
                           'BodyInstance')
        names.append(name)
    return names


def _no_collision(b):
    """A BodyInstance body with collision off (the NoCollision profile)."""
    def fname(text):
        return struct.pack('<ii', b.pkg.add_name(text), 0)
    out = bytearray()
    out += fname('CollisionEnabled') + fname('ByteProperty') + struct.pack('<ii', 8, 0)
    out += fname('ECollisionEnabled') + b'\0' + fname('ECollisionEnabled::NoCollision')
    out += fname('CollisionProfileName') + fname('NameProperty') + struct.pack('<ii', 8, 0)
    out += b'\0' + fname('NoCollision')
    out += fname('None')
    return bytes(out)


def _letters(n):
    """A, B, ... Z, BA, BB ...: a suffix FName never splits into a number."""
    out = ''
    while True:
        out = chr(ord('A') + n % 26) + out
        n //= 26
        if not n:
            return out


def place(uasset, uexp, level_name, plan):
    """(uasset, uexp, notes) with obstruction actors over the plan's walls and removed
    floor. Reads the level's walkable graph from the package itself."""
    from riftlord.build import levelbuild
    from riftlord.build.navigation import navsurfextend
    if not ENABLED:
        return uasset, uexp, []
    boxes = boxes_for(level_name, plan)
    if not boxes:
        return uasset, uexp, []
    buf = navsurfextend._surface_of(uasset, uexp)
    legs, keep = route_legs(level_name, plan)
    kept, notes = choose(buf, boxes, legs, keep)
    if not kept:
        return uasset, uexp, notes
    b = levelbuild.Builder.around(uasset, uexp, name=level_name)
    write_boxes(b, kept)
    walls = sum(1 for k in kept if k['why'] != 'removed floor')
    notes.append('ground enemies route around %d placed wall(s) and %d removed-floor '
                 'area(s) (NavPower obstructions)' % (walls, len(kept) - walls))
    ua, ux = b.pkg.write()
    return ua, ux, notes


def check_level(uasset, uexp):
    """(obstruction actors found, problems) in a written level: each is in the level's
    actor list, its root is a NavPowerObstructionComponent listed in InstanceComponents,
    with the box properties set and preload dependencies that resolve."""
    from riftlord.build import levelbuild
    from riftlord.core import uetags
    b = levelbuild.Builder.around(uasset, uexp)
    names = [n for n, _h in b.pkg.names]
    listed = set(b.actor_list())
    found, problems = 0, []
    for i in range(len(b.pkg.exports)):
        name = levelbuild._name_of(b.pkg, i)
        if not name.startswith(ACTOR_STEM):
            continue
        found += 1
        if i + 1 not in listed:
            problems.append('%s is not in the level\'s actor list' % name)
        comp = b._root_component(i + 1)
        if not comp:
            problems.append('%s has no root component' % name)
            continue
        e = b.pkg.exports[comp - 1]
        kind = levelbuild.resolve(b.pkg, struct.unpack_from('<i', e['meta'], 0)[0])[1]
        tmpl = levelbuild.resolve(b.pkg, struct.unpack_from('<i', e['meta'], 8)[0])[1]
        if kind != 'NavPowerObstructionComponent' or \
                tmpl != 'Default__NavPowerObstructionComponent':
            problems.append('%s root is %s (template %s)' % (name, kind, tmpl))
        if b.object_array(i + 1, 'InstanceComponents') != [comp]:
            problems.append('%s does not list its component' % name)
        props, _end = uetags.read(e['data'], names, 0)
        got = {p.name: p for p in props}
        wanted = ['RelativeLocation', 'RelativeRotation', 'BoxExtent', 'CreationMethod']
        if not SHIPPED_DEFAULTS:
            wanted += ['PassthroughFlags', 'ObstructionFlags']
        if NO_COLLISION:
            wanted.append('BodyInstance')
        for want in wanted:
            if want not in got:
                problems.append('%s component lacks %s' % (name, want))
        first = struct.unpack_from('<i', e['meta'], 84)[0]
        count = sum(struct.unpack_from('<4i', e['meta'], 88))
        for v in b.pkg.preload[first:first + count] if first >= 0 else ():
            if levelbuild.resolve(b.pkg, v)[0] == 'null':
                problems.append('%s component has a null preload dependency' % name)
    return found, problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--record', required=True, help='a Missions record id')
    args = ap.parse_args(argv)
    from riftlord.missions import bucket
    rec = bucket.load(args.record)
    level, plan = rec['level'], rec['plan']
    boxes = boxes_for(level, plan)
    buf = navsurf.buffer(level)
    legs, keep = route_legs(level, plan)
    kept, notes = choose(buf, boxes, legs, keep)
    print('%d candidate box(es), %d kept, %d ground leg(s)' % (len(boxes), len(kept), len(legs)))
    for box in kept:
        print('  %-14s %-34s centre (%.0f, %.0f, %.0f) half (%.0f, %.0f, %.0f) yaw %.0f'
              % ((box['why'], box['name']) + tuple(box['centre']) + tuple(box['half'])
                 + (box['yaw'],)))
    for line in notes:
        print('  note:', line)


if __name__ == '__main__':
    main()
