"""Extract Orcs Must Die 2's AI routes: the waypoint graph a level ships.

    python3 waypoints.py --scene TheEdge
    python3 waypoints.py --survey

OMD2 stores routing as `waypoint` entities in the main layer, and the shape is
close enough to OMD3's that the conversion is mostly renaming:

| OMD2 | OMD3 |
|---|---|
| `mID` | `AOMDPathPoint.PointIndex` |
| `mNextWaypoints` (`"5,7,"`) | `NextPoints` -> `FOMDPathBranch.PointIndex` |
| `mWaypointGroupName` | which `AOMDAIPath` owns the point |
| `position` | the actor's transform |

What OMD2 does **not** carry is a weight per branch. `FOMDPathBranch` has
`BranchWeight` and `BranchLength`, and the shipped OMD3 maps use them
deliberately -- weights sum to 1.0 at every fork and favour the longer route as
often as not. A converted level has to invent them, and the honest default is an
even split, which is a routing decision rather than a conversion one.

The other entities in the same layer matter too: `playerstart` is where the
player begins and `onslaught` names the wave table, so a converted level knows
where its rift and its spawns belong.
"""

import argparse
import collections
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.formats.omd2 import clr  # noqa: E402
from riftlord.formats.omd2.nrbf import parse  # noqa: E402
from riftlord.formats.omd2 import trapgrids as T  # noqa: E402
from riftlord.core import transform  # noqa: E402

#: Entities worth pulling out of a level, by their `mProtoName`, **compared
#: case-insensitively**. The campaign is not consistent: TheEdge writes
#: `waypoint`, `playerstart`, `onslaught`, while Sludge Hole writes `Waypoint`,
#: `PlayerStart`, `Rift`. An exact match silently reports five levels as having
#: no routes at all, which looks like a level that simply has none.
WANTED = ('waypoint', 'playerstart', 'player2start', 'onslaught',
          'onslaughtdoor', 'rift', 'levelspawnrift', 'spawner', 'spawnpoint',
          'respawn', 'levelexitdoor')


def _props(shape):
    got = clr.dynamic_properties(shape)
    return {k: v for k, v in got.items() if v is not None}


def entities_in(graph, source):
    """Every game entity in one parsed Layer, with its dynamic properties."""
    out = []
    for shape in graph.by_class('VisionEditorPlugin.Shapes.EntityShape'):
        props = _props(shape)
        proto = props.get('mProtoName')
        if not proto:
            continue
        pos = clr.vec3(shape.get('_position')) or (0.0, 0.0, 0.0)
        ori = clr.vec3(shape.get('_orientation')) or (0.0, 0.0, 0.0)
        out.append({'proto': str(proto), 'source': source,
                    'position': list(pos), 'orientation': list(ori),
                    'props': props})
    return out


def next_ids(text):
    """`mNextWaypoints` is a comma-separated id list, often with a trailing comma."""
    if not text:
        return []
    return [int(bit) for bit in str(text).replace(' ', '').split(',') if bit]


def extract(zip_path, scene):
    """{'waypoints': [...], 'entities': [...]} for one OMD2 level."""
    with zipfile.ZipFile(zip_path) as zf:
        found = []
        for name in T.scene_layers(zf, scene):
            try:
                graph = parse(zf.read(name))
            except Exception:                        # noqa: BLE001
                continue
            found.extend(entities_in(graph, name.rsplit('/', 1)[-1]))

    waypoints = []
    for e in found:
        kind = e['proto'].lower()
        # Two kinds of route, and they are separate graphs. `flightwaypoint` is
        # how OMD2 routes flyers, which is `bFlyersOnly` and `FlightHeight` on an
        # OMD3 path point -- so a level with flyers has two paths to convert, not
        # one, and merging them would send ground troops over a chasm.
        if kind not in ('waypoint', 'flightwaypoint'):
            continue
        p = e['props']
        waypoints.append({
            'id': int(p.get('mID', 0) or 0),
            'group': str(p.get('mWaypointGroupName', '') or ''),
            'next': next_ids(p.get('mNextWaypoints')),
            'position': e['position'],
            'flyers': kind == 'flightwaypoint',
            'source': e['source'],
        })
    others = [e for e in found
              if e['proto'].lower() not in ('waypoint', 'flightwaypoint')
              and e['proto'].lower() in WANTED]
    return {'scene': scene, 'waypoints': waypoints, 'entities': others}


def graph_shape(waypoints):
    """Facts about the route graph: groups, fan-out, starts and exits."""
    by_id = {w['id']: w for w in waypoints}
    incoming = collections.Counter()
    for w in waypoints:
        for n in w['next']:
            incoming[n] += 1
    groups = collections.Counter(w['group'] for w in waypoints)
    fanout = collections.Counter(len(w['next']) for w in waypoints)
    starts = [w['id'] for w in waypoints if incoming[w['id']] == 0]
    exits = [w['id'] for w in waypoints if not w['next']]
    dangling = sorted({n for w in waypoints for n in w['next']
                       if n not in by_id})
    return {'groups': groups, 'fanout': fanout, 'starts': starts,
            'exits': exits, 'dangling': dangling, 'count': len(waypoints),
            'flyers': sum(1 for w in waypoints if w.get('flyers'))}


def to_omd3(waypoint_list, origin=(0.0, 0.0, 0.0)):
    """Turn an OMD2 waypoint graph into OMD3 path points.

    Ground and flight routes are kept as **separate paths**, because they are
    separate graphs in OMD2 and merging them would send infantry over a chasm.

    Three things have to be invented, and it is worth being explicit about which:

    * **`BranchWeight`.** OMD2 has no weight per branch; OMD3's sums to 1.0 at
      every fork and the shipped maps use it deliberately, favouring the longer
      route as often as not. An even split is the only honest default -- it
      preserves "these routes exist" without inventing an intent OMD2 never
      recorded. It is a routing decision, and a converted level should expect to
      have it tuned.
    * **`BranchLength`.** Straight-line distance between the two points, scaled by
      `transform.SCALE`. This used to multiply by a hard-coded 3.0, which was the
      old assumed scale and is now 1.0 -- a stale constant of that kind is exactly
      what made every converted level three times too large.
    * **start and exit flags.** OMD2 does not mark them; they fall out of the
      graph -- nothing points at a start, and an exit points at nothing.

    Dangling next-ids are dropped. Thirteen shipped OMD2 levels reference
    waypoints that no longer exist, with ids below the range of anything in the
    level -- deletions whose survivors were never cleaned up. Carrying them into
    OMD3 would produce a path point that routes nowhere.
    """
    import math
    paths = collections.defaultdict(list)
    for w in waypoint_list:
        key = ('flight' if w.get('flyers') else 'ground', w['group'])
        paths[key].append(w)

    out = []
    for (kind, group), points in sorted(paths.items()):
        points = sorted(points, key=lambda w: w['id'])
        index = {w['id']: i for i, w in enumerate(points)}
        incoming = collections.Counter()
        for w in points:
            for n in w['next']:
                if n in index:
                    incoming[n] += 1
        made, dropped = [], 0
        for i, w in enumerate(points):
            valid = [n for n in w['next'] if n in index]
            dropped += len(w['next']) - len(valid)
            weight = (1.0 / len(valid)) if valid else 0.0
            branches = []
            for n in valid:
                other = points[index[n]]
                branches.append({
                    'PointIndex': index[n],
                    'BranchWeight': weight,
                    'BranchLength': math.dist(w['position'], other['position'])
                                    * transform.SCALE,
                })
            made.append({
                'PointIndex': i,
                'omd2_id': w['id'],
                'position': w['position'],
                'NextPoints': branches,
                'bIsStartPoint': incoming[w['id']] == 0,
                'bIsExitPoint': not valid,
                'bFlyersOnly': kind == 'flight',
                'bGroundOnly': kind == 'ground',
            })
        out.append({'group': group, 'kind': kind, 'points': made,
                    'dropped_branches': dropped})
    return out


def check_paths(paths):
    """Complaints about a converted route graph. Empty means it is usable."""
    bad = []
    for path in paths:
        n = len(path['points'])
        label = '%s/%s' % (path['kind'], path['group'] or '(unnamed)')
        if not n:
            bad.append('%s: no points' % label)
            continue
        for p in path['points']:
            for b in p['NextPoints']:
                if not 0 <= b['PointIndex'] < n:
                    bad.append('%s: point %d branches to %d, out of range'
                               % (label, p['PointIndex'], b['PointIndex']))
            total = sum(b['BranchWeight'] for b in p['NextPoints'])
            if p['NextPoints'] and abs(total - 1.0) > 1e-6:
                bad.append('%s: point %d weights sum to %.3f, not 1.0'
                           % (label, p['PointIndex'], total))
        if not any(p['bIsStartPoint'] for p in path['points']):
            bad.append('%s: no start point -- every point is reachable, so the '
                       'graph has a cycle and no entrance' % label)
        if not any(p['bIsExitPoint'] for p in path['points']):
            bad.append('%s: no exit point' % label)
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--zip', default=T.DEFAULT_DATA_ZIP)
    ap.add_argument('--scene')
    ap.add_argument('--survey', action='store_true')
    ap.add_argument('--limit', type=int, default=12)
    args = ap.parse_args(argv)

    if args.survey:
        with zipfile.ZipFile(args.zip) as zf:
            scenes = T.list_scenes(zf)
        print('%-28s %6s %7s %7s %7s %7s %6s  %s'
              % ('scene', 'points', 'flyers', 'groups', 'starts', 'exits',
                 'broken', 'fan-out'))
        totals = collections.Counter()
        for scene in scenes:
            try:
                data = extract(args.zip, scene)
            except Exception as exc:                 # noqa: BLE001
                print('%-28s  could not read: %s' % (scene, exc))
                continue
            s = graph_shape(data['waypoints'])
            totals.update(s['fanout'])
            note = ' '.join('%d:%d' % kv for kv in sorted(s['fanout'].items()))
            print('%-28s %6d %7d %7d %7d %7d %6d  %s'
                  % (scene, s['count'], s['flyers'], len(s['groups']),
                     len(s['starts']), len(s['exits']), len(s['dangling']),
                     note))
        print('\nfan-out across the campaign: %s'
              % ' '.join('%d next:%d' % kv for kv in sorted(totals.items())))
        return 0

    if args.scene:
        data = extract(args.zip, args.scene)
        s = graph_shape(data['waypoints'])
        print('%s: %d waypoint(s) in %d group(s)'
              % (args.scene, s['count'], len(s['groups'])))
        for g, n in s['groups'].most_common():
            print('   group %-24s %d point(s)' % (repr(g), n))
        print('   starts %s' % s['starts'][:8])
        print('   exits  %s' % s['exits'][:8])
        print('   fan-out %s' % dict(s['fanout']))
        if s['dangling']:
            print('   DANGLING next-ids (no such waypoint): %s' % s['dangling'])
        print('\n   other entities:')
        for e in data['entities'][:args.limit]:
            print('      %-14s at (%8.0f,%8.0f,%7.0f)'
                  % (e['proto'], *e['position']))
        return 0

    ap.error('need --scene NAME or --survey')


if __name__ == '__main__':
    raise SystemExit(main())
