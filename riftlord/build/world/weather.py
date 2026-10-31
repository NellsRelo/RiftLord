"""Weather for a level: rain or snow effects over its play area, as editable effect items.

Shipped levels make weather out of ordinary placed effects: Cliffside, Scenario_08 and
Mage Tower stream a `_Rain` sublevel of Rain_Storm emitters about 400 uu over the
floor with Splashes_Particle on it, ~1,900 uu apart; the Ice levels place NS_Snow_Down
and NS_Snow_Angle (Niagara) at floor height 3,300-4,500 uu apart (measured
2026-10-19, riftlord.fxsurvey). `suggest` lays the same effects over a level's play
area -- every grid cell within reach of its routes, rifts and player starts, at the
height of the nearest of them -- as `added` fx items the designer edits like any other
(effects are designer data, never automatic). The level's own weather effects are
taken out through the plan's `removedFx` (Map Settings, Level Effects).

    python3 -m riftlord.weather Scenario_05 rain     # what suggest would place
"""

import math
import sys

#: kind -> [(effect key in lavafx.DONORS, spacing uu, height over the floor uu)].
PRESETS = {
    'rain': [('Rain_Storm', 1900.0, 400.0), ('Splashes_Particle', 1900.0, 0.0)],
    'snow': [('NS_Snow_Down', 3300.0, 150.0), ('NS_Snow_Angle', 4500.0, 0.0)],
}
LABELS = {'rain': 'Rain', 'snow': 'Snow'}
#: A grid cell gets weather when a route point, rift or player start lies this near (uu).
REACH = 2500.0
#: The most of one effect a suggestion places.
MAX_PER_EFFECT = 80
#: The effect groups (riftlord.fxsurvey) that count as the level's own weather.
WEATHER_GROUPS = ('Weather',)
#: An actor this far down is parked out of play (geomedits.PARKED is -50,000), not an anchor.
PARKED_BELOW = -40000.0


def _anchors(level, plan=None):
    """[(x, y, z)] of where play happens: route points, rifts, spawners, player starts --
    the level's own (not on a level from scratch, whose host's stand elsewhere) and the
    plan's, with the plan's moves."""
    from riftlord.mapeditor import leveldata
    from riftlord.mapeditor import scratch
    plan = plan or {}
    out = []
    for key in ('points', 'spawners', 'rifts', 'playerStarts', 'wiring'):
        for item in plan.get(key) or []:
            if isinstance(item, dict) and not item.get('gone') and \
                    isinstance(item.get('at'), (list, tuple)) and len(item['at']) == 3:
                out.append(tuple(float(v) for v in item['at']))
    if scratch.is_scratch(plan):
        return [a for a in out if a[2] > PARKED_BELOW]
    lv = leveldata.level(level)
    for p in (lv.get('points') or {}).values():
        if p.get('location'):
            out.append(tuple(p['location']))
    for r in lv.get('rifts') or []:
        if r.get('loc'):
            out.append(tuple(r['loc']))
    for s in lv.get('player_starts') or []:
        if s.get('location'):
            out.append(tuple(s['location']))
    for s in lv.get('spawners') or []:
        if s.get('location'):
            out.append(tuple(s['location']))
    return [a for a in out if a[2] > PARKED_BELOW]


def grid(anchors, spacing, reach=REACH):
    """[(x, y, z)] of grid points within `reach` of an anchor, at its height."""
    if not anchors:
        return []
    xs = [a[0] for a in anchors]
    ys = [a[1] for a in anchors]
    x0, x1 = min(xs) - reach, max(xs) + reach
    y0, y1 = min(ys) - reach, max(ys) + reach
    out = []
    nx = int((x1 - x0) // spacing) + 1
    ny = int((y1 - y0) // spacing) + 1
    for i in range(nx):
        for j in range(ny):
            x = x0 + (i + 0.5) * spacing
            y = y0 + (j + 0.5) * spacing
            best = min(anchors, key=lambda a: (a[0] - x) ** 2 + (a[1] - y) ** 2)
            if math.hypot(best[0] - x, best[1] - y) <= reach:
                out.append((x, y, best[2]))
    return out


def _thin(points, limit):
    """At most `limit` of `points`, spread evenly through the list."""
    if len(points) <= limit:
        return points
    step = len(points) / float(limit)
    return [points[int(k * step)] for k in range(limit)]


def suggest(level, kind, plan=None):
    """{'added': [fx items]} of `kind` weather over the play area of the level (and the
    plan's own actors)."""
    from riftlord.build.world import lavafx
    if kind not in PRESETS:
        raise SystemExit('no such weather: %r' % kind)
    anchors = _anchors(level, plan)
    added = []
    for fx, spacing, rise in PRESETS[kind]:
        if fx not in lavafx.DONORS:
            continue
        for x, y, z in _thin(grid(anchors, spacing), MAX_PER_EFFECT):
            added.append({'kind': 'fx', 'fx': fx, 'name': fx,
                          'matrix': lavafx.fx_matrix((round(x, 1), round(y, 1),
                                                      round(z + rise, 1)))})
    return {'added': added}


def choices():
    """[{kind, label, effects}] the Map Editor offers: presets whose effects have donors."""
    from riftlord.build.world import lavafx
    return [{'kind': k, 'label': LABELS.get(k, k),
             'effects': [fx for fx, _s, _r in v if fx in lavafx.DONORS]}
            for k, v in PRESETS.items()
            if any(fx in lavafx.DONORS for fx, _s, _r in v)]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    level, kind = argv[0], (argv[1] if len(argv) > 1 else 'rain')
    got = suggest(level, kind)['added']
    counts = {}
    for item in got:
        counts[item['fx']] = counts.get(item['fx'], 0) + 1
    print('%s %s: %s' % (level, kind, counts))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
