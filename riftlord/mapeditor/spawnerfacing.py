"""Which way a spawner Rift Lord adds faces: toward its route's first point, yaw only.

    facing(sp, nodes, level)   the yaw an added spawner is written with, or None
    read_back(source)          [{name, index, at, yaw, target, off}] for a built mission
    warnings(source)           [text] for added spawners facing more than WARN_OFF away

Measured on the 33 shipped levels' 98 spawners (each yaw against the direction to its
start point): flyer spawners 0.1 to 1.3 degrees off, Scenario_18's red rifts median 8.5 and
at most 89.7, ground spawners median 19.7 (8 of 71, all 1000 to 3000 uu behind their doors,
over 100); one spawner of 98 has a pitch (Scenario_06, 4 degrees), none a roll, and no
SpawnDirection component is turned. A unit spawned facing away from its travel walks
backwards (movement scales speed and animation by that angle, 0.7x past 100 degrees).

A spawner the designer turned by hand carries `turned` in the plan and keeps its rotation.
The level's own spawners are not touched.
"""

import math
import os
import sys
import tempfile

#: A first point this close to its spawner (horizontally) is passed at once, so the facing
#: is taken from the point after it: the point's own reach radius, or the class default.
DEFAULT_REACH = 300.0

#: Below this horizontal distance (uu) there is no direction to face.
MIN_LEG = 1.0

#: Degrees between an added spawner's yaw and its first leg over which a build warns.
#: Past 100 degrees the game moves a unit backwards at 0.7x.
WARN_OFF = 100.0

#: The export names the build gives added spawners (planactors.emit).
ADDED_PREFIX = 'RiftLordSpawner'


def route_keys(sp):
    """The route references to aim at, in order: a flyer spawner's flyer route first."""
    return ('flyerStart', 'start') if sp.get('kind') == 'flyer' else ('start', 'flyerStart')


def heading(frm, to):
    """Yaw in degrees from `frm` to `to` (Unreal: 0 along +x, 90 along +y), or None when
    they are under MIN_LEG apart horizontally."""
    dx, dy = float(to[0]) - float(frm[0]), float(to[1]) - float(frm[1])
    if math.hypot(dx, dy) < MIN_LEG:
        return None
    return round(math.degrees(math.atan2(dy, dx)), 1)


def aim(at, first, reach, after):
    """(yaw, target): toward `first`, or toward `after` when `first` is within `reach`
    horizontally of `at`. (None, None) when neither gives a direction."""
    dist = math.hypot(float(first[0]) - float(at[0]), float(first[1]) - float(at[1]))
    if dist < reach and after is not None:
        yaw = heading(at, after)
        if yaw is not None:
            return yaw, tuple(after)
    yaw = heading(at, first)
    return (yaw, tuple(first)) if yaw is not None else (None, None)


def off(yaw, toward):
    """Unsigned degrees between two yaws, 0 to 180."""
    return abs((float(toward) - float(yaw) + 540.0) % 360.0 - 180.0)


def facing(sp, nodes, level):
    """The yaw an added spawner `sp` (a plan spawner) is written with: its rotation when
    `turned`, else toward its route's first point (planactors.graph `nodes`, `level` from
    planactors.level_actors), else its rotation."""
    if sp.get('turned'):
        return sp.get('rotation')
    for key in route_keys(sp):
        ref = sp.get(key)
        if ref not in nodes:
            continue
        node = nodes[ref]
        if node['kind'] == 'plan':
            reach = float(node['point'].get('reachRadius') or DEFAULT_REACH)
        else:
            reach = float((level['points'].get(ref) or {}).get('reachRadius') or DEFAULT_REACH)
        nxt = node['next'][0]['to'] if node['next'] else None
        after = nodes[nxt]['at'] if nxt in nodes else None
        yaw, _target = aim(sp['at'], node['at'], reach, after)
        if yaw is not None:
            return yaw
    return sp.get('rotation')


# -- a built mission, read back ----------------------------------------------------------

def _umap(source):
    """(uasset, uexp) bytes of a written mission's level (leveltitle.level_files), or None."""
    from riftlord.build import leveltitle
    level = leveltitle.level_files(source)
    if not level:
        return None
    umap = next(n for n in level if n.endswith('.umap'))
    return level[umap], level[umap[:-5] + '.uexp']


def read_back(source):
    """read_level for a written mission (a loose build folder or a pak); [] without a
    level."""
    got = _umap(source)
    return read_level(*got) if got else []


def read_level(ua, ux):
    """[{name, index, at, yaw, pitch, roll, target, want, off}] for each added spawner of a
    written level (uasset and uexp bytes) that has a route: its root rotation, and the
    first-leg target `aim` picks from the written points (of its ground and flyer routes,
    the one nearer its yaw). `off` is unsigned degrees between them."""
    from riftlord.formats.ue.uheader import parse as parse_header
    from riftlord.onslaught import onslaught
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'level.umap')
        with open(path, 'wb') as fh:
            fh.write(ua)
        info = parse_header(path)
    loaded = (info, ux, len(ua))
    rep = onslaught.map_report('built', loaded=loaded)
    points = rep['points']
    by_index = {p['index'] if isinstance(p['index'], int) else 0: p for p in points.values()}
    exports = {e['name']: e for e in info['exports']}
    out = []
    for s in rep['spawners']:
        if not str(s['export']).startswith(ADDED_PREFIX) or not s.get('location'):
            continue
        xf = onslaught.actor_transform(info, ux, len(ua), exports[s['export']], {})
        pitch, yaw, roll = xf['rotation']
        best = None
        for ref in (s.get('start'), s.get('flyer')):
            pt = points.get((ref or '').split('.')[-1])
            if not pt or not pt.get('location'):
                continue
            nxt = by_index.get((pt['next'] or [{}])[0].get('PointIndex'))
            after = nxt['location'] if nxt and nxt.get('location') else None
            want, target = aim(s['location'], pt['location'],
                               float(pt.get('reachRadius') or DEFAULT_REACH), after)
            if want is None:
                continue
            row = {'name': s['export'], 'index': s['index'],
                   'at': [round(v, 1) for v in s['location']],
                   'yaw': round(yaw, 2), 'pitch': round(pitch, 2), 'roll': round(roll, 2),
                   'target': [round(v, 1) for v in target], 'want': want,
                   'off': round(off(yaw, want), 1)}
            if best is None or row['off'] < best['off']:
                best = row
        if best:
            out.append(best)
    return out


def warnings(source):
    """level_warnings for a written mission (a loose build folder or a pak)."""
    got = _umap(source)
    return level_warnings(*got) if got else []


def level_warnings(ua, ux):
    """[text] for each added spawner of a written level facing more than WARN_OFF degrees
    away from its route's first leg."""
    return ['added spawner %s (spawner %s) faces %.0f degrees away from its route\'s first '
            'point; units it sends walk backwards' % (r['name'], r['index'], r['off'])
            for r in read_level(ua, ux) if r['off'] > WARN_OFF]


def main(argv=None):
    import json
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print('usage: python3 -m riftlord.mapeditor.spawnerfacing <built mission folder or pak>')
        return 2
    rows = read_back(argv[0])
    print(json.dumps(rows, indent=1))
    for line in warnings(argv[0]):
        print('warning: ' + line)
    return 0


if __name__ == '__main__':
    sys.exit(main())
