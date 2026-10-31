"""Emit OMD3 AI routes, rifts, spawners and a player start into a converted level.

    python3 pathemit.py --scene Passages

## Why a converted level needs its own

Until now a converted mission borrowed Scenario_01's gameplay wholesale: its rift,
its spawners, its routes and its player start, with only the castle sublevel
replaced. That is why the maps were awkward in ways that had nothing to do with
the geometry -- the player appeared wherever North Wing puts them, and Servant
Entrance sat half under a kill plane that belonged to another map. A level that
brings its own stops caring where the host put anything.

## The shapes, read out of Scenario_01

`OMDAIPath` owns the route and holds `PathPoints`, an array of object references.
Each `OMDPathPoint` carries `ParentPath`, `ReachRadius` (300 in the shipped data),
`bIsStartPoint`, and `NextPoints` -- an array of `OMDPathBranch` structs.

A branch is three tagged properties and a terminator:

    PointIndex    IntProperty     4
    BranchWeight  FloatProperty   4
    BranchLength  FloatProperty   4
    None

which is 95 bytes, so an array of one is 4 + 49 + 95 = 148 -- exactly the size
Scenario_01's own path points carry. The 49 is the inner tag, and **its size field
covers the element bodies only**; getting that wrong is what once crashed the game
with "corrupt data found".

OMD2 supplies everything but the weights: `waypoints.to_omd3` splits them evenly
and computes lengths, and notes that this is a routing decision rather than a
conversion one.
"""

import argparse
import collections
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.build import levelbuild  # noqa: E402
from riftlord.core import transform  # noqa: E402

#: The level every gameplay actor is lifted from. It is the smallest shipped map
#: that has one of each.
DONOR_MAP = '../../../OMD/Content/Maps/Campaign/Scenario_01/Scenario_01'

#: What the shipped path points use.
REACH_RADIUS = 300.0

#: The classes we need one of each of.
WANTED = ('OMDAIPath', 'OMDPathPoint', 'OMDPlayerStart',
          'BP_OMDRift_C', 'BP_OMDAISpawner_C')


def donors(path=DONOR_MAP):
    """{class name: (package, export index)} for the gameplay actors."""
    from riftlord.core import gamefiles
    from riftlord.formats.ue.uassetio import Package
    ua, ux = gamefiles.package(path)
    pkg = Package(ua, ux)
    pkg.load_payloads()
    found = {}
    for i, e in enumerate(pkg.exports):
        cls = levelbuild.resolve(
            pkg, struct.unpack_from('<i', e['meta'], 0)[0])[1] or ''
        if cls in WANTED and cls not in found:
            found[cls] = (pkg, i)
    return found


def branch_body(builder, branch):
    """One `OMDPathBranch` element: three tags and a terminator, 95 bytes."""
    return builder.tagged([
        ('PointIndex', 'IntProperty', struct.pack('<i', branch['PointIndex'])),
        ('BranchWeight', 'FloatProperty',
         struct.pack('<f', branch['BranchWeight'])),
        ('BranchLength', 'FloatProperty',
         struct.pack('<f', branch['BranchLength'])),
    ])


def emit_paths(builder, found, paths, locate):
    """Add every converted route to the level. Returns a report.

    `locate` turns a point's OMD2 position into an Unreal location, so this does
    not need to know about the level's origin or its anchor.
    """
    if 'OMDAIPath' not in found or 'OMDPathPoint' not in found:
        return {'paths': 0, 'points': 0,
                'why': 'the donor map has no OMDAIPath/OMDPathPoint'}
    path_pkg, path_i = found['OMDAIPath']
    point_pkg, point_i = found['OMDPathPoint']

    made_paths = made_points = branches = 0
    for p, path in enumerate(paths):
        if not path['points']:
            continue
        path_index = builder.lift_actor(path_pkg, path_i,
                                        rename='AIPath_%d' % p)
        point_indices = []
        for point in path['points']:
            idx = builder.lift_actor(point_pkg, point_i,
                                     rename='PathPoint_%d_%d'
                                     % (p, point['PointIndex']))
            builder.place(idx, location=locate(point['position']))
            bodies = [branch_body(builder, b) for b in point['NextPoints']]
            builder.struct_array(idx, 'NextPoints', 'OMDPathBranch', bodies)
            builder.set_bool(idx, 'bIsStartPoint', point['bIsStartPoint'])
            builder.set_property(idx, 'ReachRadius', 'FloatProperty',
                                 struct.pack('<f', REACH_RADIUS))
            builder.set_object(idx, 'ParentPath', path_index)
            point_indices.append(idx)
            branches += len(point['NextPoints'])
            made_points += 1
        builder.set_object_array(path_index, 'PathPoints', point_indices)
        made_paths += 1
    return {'paths': made_paths, 'points': made_points, 'branches': branches}


def emit_actor(builder, found, cls, location, rename, rotation=None):
    """Lift one gameplay actor and put it somewhere. Returns its index or None."""
    if cls not in found:
        return None
    pkg, index = found[cls]
    idx = builder.lift_actor(pkg, index, rename=rename)
    builder.place(idx, location=location, rotation=rotation)
    return idx



# -- furnishing the mission's own persistent level ---------------------------

#: Which OMD2 entity proto becomes which OMD3 actor class. OMD2's names vary in
#: case between levels, so these are compared lower-cased.
#: `respawn` is deliberately absent. Mapping it to an `OMDPlayerStart` gave the
#: level a third start 2,350 units under the floor, and the game is free to pick
#: any of them -- which is why Servant Entrance spawned the player into nothing.
ENTITY_ACTOR = {
    'playerstart': 'OMDPlayerStart',
    'player2start': 'OMDPlayerStart',
    'rift': 'BP_OMDRift_C',
    'levelspawnrift': 'BP_OMDRift_C',
    'onslaught': 'BP_OMDAISpawner_C',
    'onslaughtdoor': 'BP_OMDAISpawner_C',
    'spawner': 'BP_OMDAISpawner_C',
    'spawnpoint': 'BP_OMDAISpawner_C',
}


def actors_by_class(builder):
    """{class name: [package index]} for everything already in a level."""
    out = collections.defaultdict(list)
    for i, e in enumerate(builder.pkg.exports):
        cls = levelbuild.resolve(
            builder.pkg, struct.unpack_from('<i', e['meta'], 0)[0])[1] or ''
        if cls:
            out[cls].append(i + 1)
    return out


def furnish(builder, entities, paths, locate, found=None, ground=None,
            rift_hover=0.0):
    """Give a mission's own persistent level OMD2's gameplay, in place.

    The host's rift, spawner and player start are **moved** rather than
    duplicated. A second rift in a level is not a converted level with a rift, it
    is a level with two, and the game has no way to know which one is meant.
    Anything OMD2 has more of than the host is lifted from the donor.

    The host's own AI path is emptied for the same reason: its points describe
    North Wing's corridors, which are not there any more.
    """
    found = found or donors()
    present = actors_by_class(builder)
    report = collections.Counter()

    # The host's route describes a level that is no longer here. Emptying the path
    # is not quite enough on its own: its points survive, still carrying their own
    # `NextPoints` and possibly `bIsStartPoint`, so they are parked below the map
    # as well. Parking rather than deleting, because removing an export renumbers
    # every reference in the package.
    for path_index in present.get('OMDAIPath', []):
        builder.set_object_array(path_index, 'PathPoints', [])
        report['host path emptied'] += 1
    for point_index in present.get('OMDPathPoint', []):
        builder.set_object_array(point_index, 'NextPoints', [])
        builder.set_bool(point_index, 'bIsStartPoint', False)
        builder.place(point_index, location=(0.0, 0.0, -50000.0))
        report['host path point parked'] += 1

    wanted = collections.defaultdict(list)
    for e in entities:
        cls = ENTITY_ACTOR.get(e['proto'].lower())
        if cls:
            wanted[cls].append(e)

    for cls, want in sorted(wanted.items()):
        spare = list(present.get(cls, []))
        for n, e in enumerate(want):
            where = locate(e['position'])
            if ground is not None:
                # A spawner sits outside the map by design, so it is allowed to
                # look further afield for a floor to take its height from.
                floor = ground(where, 12 if 'Spawner' in cls else 0)
                if floor is not None:
                    lift = rift_hover if cls == 'BP_OMDRift_C' else 0.0
                    where = (where[0], where[1], floor + lift)
                    report['%s seated on the floor' % cls] += 1
                else:
                    report['%s has no floor under it' % cls] += 1
            if spare:
                builder.place(spare.pop(0), location=where)
                report['%s moved' % cls] += 1
            elif cls in found:
                pkg, index = found[cls]
                idx = builder.lift_actor(pkg, index,
                                         rename='%s_%d' % (cls.strip('_C'), n))
                builder.place(idx, location=where)
                report['%s added' % cls] += 1
            else:
                report['%s wanted but no donor' % cls] += 1
        # A host actor OMD2 has no use for is pushed far below the map rather than
        # deleted, because removing an export renumbers every reference. Player
        # starts are the exception: the game may choose any of them, so a surplus
        # one is stacked on the real start instead of parked somewhere lethal.
        home = locate(want[0]['position']) if want else None
        if home is not None and ground is not None:
            floor = ground(home)
            if floor is not None:
                home = (home[0], home[1], floor)
        for leftover in spare:
            if cls == 'OMDPlayerStart' and home is not None:
                builder.place(leftover, location=home)
                report['%s surplus stacked on the start' % cls] += 1
            else:
                builder.place(leftover, location=(0.0, 0.0, -50000.0))
                report['%s surplus parked' % cls] += 1

    got = emit_paths(builder, found, paths, locate)
    report['paths'] = got.get('paths', 0)
    report['path points'] = got.get('points', 0)
    report['branches'] = got.get('branches', 0)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--scene', required=True)
    args = ap.parse_args(argv)
    from riftlord.build import waypoints as WP
    from riftlord.mapeditor import tiler
    got = donors()
    print('donor actors found: %s' % sorted(got))
    routes = WP.extract(tiler.omd2_scenes()[0], args.scene)
    paths = WP.to_omd3(routes['waypoints'])
    print('%s: %d path(s), %d point(s)'
          % (args.scene, len(paths), sum(len(p['points']) for p in paths)))
    kinds = collections.Counter(e['proto'].lower() for e in routes['entities'])
    print('   gameplay entities: %s' % dict(kinds.most_common(10)))
    for complaint in WP.check_paths(paths):
        print('   PROBLEM %s' % complaint)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
