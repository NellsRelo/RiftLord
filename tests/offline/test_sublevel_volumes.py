"""Offline checks on the volumes stored in a level's sublevels (marker: datafix): read from
every package with '<sublevel>|<export>' names beside the persistent level's own export
names, a plan's removal, move, kill switch and Blocks change written into the sublevel copy
(the stand-in lifted into the persistent level), an in-the-way sublevel volume a ground
route crosses parked in its own package, the walkable-graph rebuild reading sublevel
blocking volumes, and the probe's kill-switch gate reading sublevel copies.

    python3 test_sublevel_volumes.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from riftlord.core import gamefiles  # noqa: E402
from riftlord.build import apply  # noqa: E402
from riftlord.build import levelbuild  # noqa: E402
from riftlord.build.navigation import navpatch  # noqa: E402
from riftlord.build.navigation import navregen  # noqa: E402
from riftlord.formats.ue.uassetio import Package  # noqa: E402
from riftlord.mapeditor import controllers  # noqa: E402
from riftlord.mapeditor import leveldata  # noqa: E402
from riftlord.mapeditor import volumes  # noqa: E402

#: Repaired Mine's level: 16 trap blocking volumes in its Castle sublevel
ICE = 'Scenario_Ice_02'
#: 46 in its sublevels: 30 camera, 12 players, 4 kill (Castle|513..516)
BESET = 'Scenario_09'
KILL = 'Castle|513'
#: a player blocking volume in Scenario_12's Castle sublevel
PLAYERS_LEVEL, PLAYERS = 'Scenario_12', 'Castle|877'
#: 17 BlockingVolumes stopping everyone, all in Scenario_18's Castle sublevel
WAR = 'Scenario_18'

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


def sub_of(level, tag):
    """(uasset, uexp) of one of the level's sublevels as shipped."""
    return gamefiles.package(leveldata.level(level)['subPackages'][tag])


def read_back(ua, ux, tag):
    pkg = Package(ua, ux)
    pkg.load_payloads()
    return {v['name']: v for v in volumes.volumes_of(pkg, tag)}


# 1. read from every package
ice = volumes.level_volumes(ICE)
castle = [v for v in ice if v['sub'] == 'Castle']
check('Repaired Mine: 16 trap blocking volumes read from its Castle sublevel',
      len(castle) == 16 and all(v['stops'] == 'traps' for v in castle), len(castle))
check('a sublevel volume is named <sublevel>|<export> and titled by its export name',
      all(v['name'] == 'Castle|%d' % v['index'] and v['title'].startswith('OMDTrapBlockingVolume')
          for v in castle), castle[0]['name'] if castle else None)
beset = volumes.level_volumes(BESET)
persistent = [v for v in beset if v['sub'] == volumes.PERSISTENT]
check('the persistent level\'s volumes come first, named by export name as before',
      beset[:len(persistent)] == persistent and persistent == volumes.package_volumes(BESET)
      and all('|' not in v['name'] and v['name'] == v['title'] for v in persistent), '')
subs = [v for v in beset if v['sub'] != volumes.PERSISTENT]
kinds = sorted(v['stops'] for v in subs)
check('Beset: 46 sublevel volumes (30 camera, 4 kill, 12 players)',
      len(subs) == 46 and kinds.count('camera') == 30 and kinds.count('kill') == 4
      and kinds.count('players') == 12, len(subs))
check('/api/level carries them', len([v for v in leveldata.level(ICE)['volumes']
                                      if v['sub'] == 'Castle']) == 16, '')
check('a plan names them', volumes.clean([{'name': KILL, 'gone': True}]) ==
      [{'name': KILL, 'gone': True}], '')

# 2. a plan's edits reach the sublevel copy
kill = next(v for v in beset if v['name'] == KILL)
moved = [kill['location'][0] + 300.0, kill['location'][1], kill['location'][2]]
plan = {'level': BESET, 'volumes': [
    {'name': KILL, 'at': moved, 'killPlayers': False},
    {'name': 'Castle|514', 'gone': True}]}
touched = apply.sublevel_edits(BESET, plan)
rows = (touched.get('Castle') or {}).get('volumes') or []
check('sublevel_edits hands the Castle copy both edits',
      sorted(v['name'] for v, _e in rows) == [KILL, 'Castle|514'], [v['name'] for v, _e in rows])
check('no persistent rows for sublevel edits', 'volumes' not in (touched.get('persistent') or {}), '')
ua, ux = sub_of(BESET, 'Castle')
ua2, ux2, report = apply.apply_to_sublevel(ua, ux, BESET + '_Castle', touched['Castle'])
back = read_back(ua2, ux2, 'Castle')
check('the moved kill volume reads back where the plan put it',
      all(abs(a - b) < 0.01 for a, b in zip(back[KILL]['location'], moved)), back[KILL]['location'])
check('its Kills heroes switch reads back off, Kills enemies on',
      back[KILL].get('killPlayers') is False and back[KILL].get('killMinions') is True,
      (back[KILL].get('killPlayers'), back[KILL].get('killMinions')))
check('the removed one is parked', abs(back['Castle|514']['location'][2] - volumes.PARKED[2]) < 1,
      back['Castle|514']['location'])
check('the copy\'s report says so, nothing skipped',
      not report['skipped'] and any('Castle|514 removed' in n for n in report['notes']), report)

# 3. the probe's kill-switch gate reads the sublevel copy
mod = 'OMD/Content/Mods/DatafixProbe/Maps/DatafixProbe_Map01/DatafixProbe_Map01'
pa, px = gamefiles.package(navpatch.map_path(BESET))
files = {mod + '.umap': pa, mod + '.uexp': px, mod + '_Castle.umap': ua2, mod + '_Castle.uexp': ux2}
gate = [g for g in controllers.gates(files, plan) if g[0] == 'kill volume switches written']
check('the kill-switch gate passes on the written copy', gate and gate[0][1], gate)
files_bare = dict(files, **{mod + '_Castle.umap': ua, mod + '_Castle.uexp': ux})
gate = [g for g in controllers.gates(files_bare, plan) if g[0] == 'kill volume switches written']
check('and fails on the shipped sublevel', gate and not gate[0][1], gate)

# 4. Blocks: the sublevel volume parked, its stand-in lifted into the persistent level
players = next(v for v in volumes.level_volumes(PLAYERS_LEVEL) if v['name'] == PLAYERS)
swap = {'level': PLAYERS_LEVEL, 'volumes': [{'name': PLAYERS, 'blocks': 'everyone'}]}
touched = apply.sublevel_edits(PLAYERS_LEVEL, swap)
ua, ux = sub_of(PLAYERS_LEVEL, 'Castle')
ua3, ux3, _r = apply.apply_to_sublevel(ua, ux, PLAYERS_LEVEL + '_Castle', touched['Castle'])
check('a Blocks change parks the sublevel volume',
      abs(read_back(ua3, ux3, 'Castle')[PLAYERS]['location'][2] - volumes.PARKED[2]) < 1, '')
pa, px = gamefiles.package(navpatch.map_path(PLAYERS_LEVEL))
builder = levelbuild.Builder.around(pa, px)
report = {'notes': [], 'skipped': []}
volumes.apply(builder, swap, PLAYERS_LEVEL, report)
stand = '%sCastle%d' % (volumes.SWAPPED_PREFIX, players['index'])
pkg = builder.pkg
got = next((v for v in volumes.volumes_of(pkg) if v['title'] == stand), None)
check('its stand-in stops everyone in the persistent level, where the volume stood',
      got and got['stops'] == 'everyone'
      and all(abs(a - b) < 0.5 for a, b in zip(got['worldMin'], players['worldMin'])),
      (got and got['worldMin'], players['worldMin']))

# 5. a ground route through a sublevel volume parks it in its own package
cam = next(v for v in subs if v['stops'] == 'camera')
mid = [(cam['worldMin'][k] + cam['worldMax'][k]) / 2 for k in range(3)]
chain = [(mid[0] - 2000.0, mid[1], mid[2] - 100.0), (mid[0] + 2000.0, mid[1], mid[2] - 100.0)]
rows = volumes.writes(BESET, {'level': BESET}, [chain])
crossed = [v['name'] for tag, r in rows.items() for v, e in r if e.get('crossed')]
check('a route through a camera blocking volume in Castle crosses it',
      cam['name'] in crossed and all(tag != volumes.PERSISTENT or not r for tag, r in rows.items()
                                     if any(v['name'] == cam['name'] for v, _e in r)), crossed)
ua, ux = sub_of(BESET, 'Castle')
builder = levelbuild.Builder.around(ua, ux)
report = {'notes': [], 'skipped': []}
volumes.write_package(builder, [r for r in rows.get('Castle') or [] if r[0]['name'] == cam['name']],
                      report)
ua4, ux4 = builder.write()
check('it is parked in the Castle copy',
      abs(read_back(ua4, ux4, 'Castle')[cam['name']]['location'][2] - volumes.PARKED[2]) < 1,
      report)
check('a shipped level\'s own routes cross no sublevel volume',
      not apply.sublevel_edits(ICE, {'level': ICE}).get('Castle', {}).get('volumes'), '')

# 6. a plan made before: a persistent volume's edit stays in the persistent level
own = persistent[0]
old = {'level': BESET, 'volumes': [{'name': own['name'], 'gone': True}]}
rows = volumes.writes(BESET, old, None)
check('a persistent volume\'s edit is written into the persistent level only',
      list(rows) == [volumes.PERSISTENT] and rows[volumes.PERSISTENT][0][0]['name'] == own['name'],
      list(rows))
pa, px = gamefiles.package(navpatch.map_path(BESET))
builder = levelbuild.Builder.around(pa, px)
report = {'notes': [], 'skipped': []}
volumes.apply(builder, old, BESET, report)
check('and parked there as before', '%s removed (%s)' % (own['name'], own['stopsText'])
      in report['notes'] and not report['skipped'], report)

# 7. the walkable-graph rebuild reads sublevel blocking volumes
everyone = [v for v in volumes.level_volumes(WAR) if v['stops'] == 'everyone']
in_castle = [v for v in everyone if v['sub'] == 'Castle']
tris = navregen.blocking_volumes(WAR, {'level': WAR})
check('Scenario_18: the rebuild takes its 17 Castle blocking volumes',
      len(in_castle) == 17 and len(tris) == len(everyone), (len(tris), len(everyone)))
gone = navregen.blocking_volumes(WAR, {'level': WAR, 'volumes': [
    {'name': in_castle[0]['name'], 'gone': True}]})
check('and leaves out one the plan removes', len(gone) == len(tris) - 1, len(gone))
wall = in_castle[1]
mid = [(wall['worldMin'][k] + wall['worldMax'][k]) / 2 for k in range(3)]
real_chains = volumes._chains
volumes._chains = lambda _level, _plan: [[(mid[0] - 3000.0, mid[1], mid[2] - 100.0),
                                          (mid[0] + 3000.0, mid[1], mid[2] - 100.0)]]
try:
    parked = navregen.blocking_volumes(WAR, {'level': WAR})
finally:
    volumes._chains = real_chains
check('and one a ground route crosses, which the build parks', len(parked) < len(tris),
      (len(parked), len(tris)))

# 8. the Map Editor names a volume by its export
src = open(os.path.join(ROOT, 'web', 'mapeditor', 'src', 'volumes.js')).read()
check('the volume card shows its export name', 'escape(v.title || d.name)' in src, '')

bad = [c for c in checks if not c[1]]
for name, ok, detail in checks:
    print('%s %s%s' % ('ok  ' if ok else 'FAIL', name, '' if ok else ': %s' % (detail,)))
print('%d of %d checks passed' % (len(checks) - len(bad), len(checks)))
sys.exit(1 if bad else 0)
