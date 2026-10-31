"""Offline checks on the Onslaught Editor's map pane for maps of several floors
(riftlord/onslaught/gamedata.py map_floors, map_detail) and its music preview endpoint
(riftlord/onslaught/server.py api/music/<track>).

    python3 test_onslaught_floors.py

Shipped maps only: Scenario_15 (4 MapVolumes), Scenario_11 (3), Scenario_16 (2, the host of
the user's Acid Factory) and Scenario_02 (1). Each multi-floor map lists its floors lowest
first, every one with a drawing and a frame; every door, rift and route point carries the
floor holding it and its uv on each floor, and that floor's minimap is served. A one-floor
map lists none and its points carry neither.
"""
import os
import sys
import tempfile
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

#: an empty Missions folder, so nothing of the user's is read
os.environ['OMD3_BUCKET'] = tempfile.mkdtemp(prefix='Missions-onslaught-floors-')

from riftlord.build.minimap import mapdata  # noqa: E402
from riftlord.onslaught import gamedata, server  # noqa: E402

#: map -> the MapVolumes it ships
FLOORED = {'Scenario_15': 4, 'Scenario_11': 3, 'Scenario_16': 2}
#: the maps whose doors, rifts and routes are on more than one floor (Scenario_16's are all
#: on its upper floor)
SPREAD = ('Scenario_15', 'Scenario_11')
ONE_FLOOR = 'Scenario_02'
#: a music track (levelmusic.TRACKS) and the header of an Ogg stream
TRACK = 'Battle_06'
OGG = b'OggS'

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


def points(d):
    """Every uv point the pane draws: doors, rifts, spawners and route ends."""
    out = [x['at'] for x in d['doors']] + list(d['rifts']) + [s['at'] for s in d['spawners']]
    for s in d['spawners']:
        for seg in s['route']:
            out += [seg['from'], seg['to']]
    return [p for p in out if p]


for name, count in FLOORED.items():
    d = gamedata.map_detail(name)
    floors = d.get('floors') or []
    md = mapdata.read(name, data_path=gamedata.map_data_for(name))
    zs = [md['volumes'][f['volume']]['translation'][2] for f in floors]
    check('%s: %d floors, lowest first' % (name, count),
          d['volumes'] == count and len(floors) == count and zs == sorted(zs)
          and sorted(f['volume'] for f in floors) == list(range(count)), (d['volumes'], zs))
    check('%s: every floor has a drawing frame' % name,
          all(f['bbox'] and f['bbox']['u1'] > f['bbox']['u0'] for f in floors), floors)
    pts = points(d)
    bad = [p for p in pts if not (0 <= p.get('f', -1) < count and len(p.get('p') or []) == count)]
    check('%s: %d points carry their floor and uv on each floor' % (name, len(pts)),
          pts and not bad, bad[:3])
    on = [p for p in pts if p['f'] == d['volume']]
    check('%s: a point on the opening floor keeps its uv there' % name,
          on and all(abs(p['p'][d['volume']][0] - p['u']) < 1e-4 for p in on), on[:2])
    used = {p['f'] for p in pts}
    if name in SPREAD:
        check('%s: points on more than one floor (%s)' % (name, sorted(used)), len(used) > 1, used)
    for f in floors:
        png = gamedata.minimap_png(name, f['volume'])
        check('%s: floor volume %d minimap served' % (name, f['volume']),
              png and png[:4] == b'\x89PNG', len(png or b''))

d = gamedata.map_detail(ONE_FLOOR)
check('%s: one floor, no floor list, no per-floor uv' % ONE_FLOOR,
      d['volumes'] == 1 and d['floors'] == [] and not any('f' in p or 'p' in p for p in points(d)),
      d['floors'])

httpd = server.serve(0)
base = 'http://127.0.0.1:%d/api/music/' % httpd.port
try:
    with urllib.request.urlopen(base + TRACK, timeout=120) as r:
        body, ctype = r.read(), r.headers.get('Content-Type')
    check('api/music/%s: an Ogg stream' % TRACK, body[:4] == OGG and ctype == 'audio/ogg',
          (ctype, body[:8]))
except urllib.error.URLError as exc:
    check('api/music/%s: an Ogg stream' % TRACK, False, exc)
try:
    urllib.request.urlopen(base + 'NoSuchTrack', timeout=30)
    check('api/music of an unknown track: 404', False, 'answered')
except urllib.error.HTTPError as exc:
    check('api/music of an unknown track: 404', exc.code == 404, exc.code)
httpd.shutdown()

for name, ok, detail in checks:
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, '' if ok else ': %s' % (detail,)))
failed = [c for c in checks if not c[1]]
print('%d checks, %d failed' % (len(checks), len(failed)))
sys.exit(1 if failed else 0)
