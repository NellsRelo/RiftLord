"""Offline checks on the surface weathering (riftlord/build/world/surfaces.py): every value
of MPC_Enviro_Level a master material reads is offered on every level, at the level's
value; a value the level's script sets as a constant is rewritten in a copy of the script;
any other is set by the surfaces actor, a bpkit Blueprint built per mission. Icebound Mine
(Scenario_Ice_02) sets snow and ice; The Lava Pits (Scenario_06) sets nothing; Hidden Dock
(Scenario_04) works out its stone colour and desaturation as it plays.

    python3 test_surfaces.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from riftlord.build.minimap import mapdata  # noqa: E402
from riftlord.build.world import surfaces  # noqa: E402
from riftlord.core import gamefiles  # noqa: E402
from riftlord.formats.ue.uassetio import Package  # noqa: E402
from riftlord.mapeditor import mapinfo  # noqa: E402

ICE, BARE, DOCK = 'Scenario_Ice_02', 'Scenario_06', 'Scenario_04'
MOD = 'RiftLordSurfacesTest'
checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


def fresh(level):
    pkg = Package(*gamefiles.package(mapdata.map_path(level)))
    pkg.load_payloads()
    return pkg


def plan(level, values):
    return {'level': level, '_modKey': MOD, 'world': {'surfaces': values}}


got = surfaces.survey(ICE)
check('Icebound Mine sets snow and ice as constants',
      got['set'] == {'UseSnowCastle': 1.0, 'UseIceCastle': 1.0}, got)
shown = surfaces.shown(ICE)
check('Icebound Mine offers every value, its own and the defaults',
      shown['UseSnowCastle'] == 1.0 and shown['UseSandRock'] == 0.0
      and shown['ColorSnow'] == [0.9216, 0.9529, 1.0, 1.0]
      and 'ColorStoneCastle' not in shown, sorted(shown))
bare = surfaces.shown(BARE)
check('a level that sets nothing offers all %d values at their defaults' % len(surfaces.PARAMS),
      len(bare) == len(surfaces.PARAMS) and bare['UseSnowCastle'] == 0.0, sorted(bare))
check('no value nothing reads is offered (UseRainFoliage)',
      'UseRainFoliage' not in surfaces.BY_NAME)
dock = surfaces.survey(DOCK)
check('Hidden Dock works out its stone colour and desaturation',
      set(dock['worked']) == {'ColorStoneCastle', 'DesaturateCastleStone'}
      and 'DesaturateCastleStone' not in surfaces.shown(DOCK), dock)

check('clean keeps known values, clamps, drops the rest',
      surfaces.clean({'UseSnowCastle': 0, 'UseIceCastle': 3, 'Bogus': 1, 'UseRainRock': True,
                      'ColorSand': [2, 0.5, -1, 1]})
      == {'UseSnowCastle': 0.0, 'UseIceCastle': 1.0, 'ColorSand': [1.0, 0.5, 0.0, 1.0]})
check('clean of nothing is None', surfaces.clean({}) is None and surfaces.clean(None) is None)
check('the world block keeps surfaces',
      mapinfo.clean_world({'surfaces': {'UseSnowCastle': 0}}) == {'surfaces': {'UseSnowCastle': 0.0}})

values = {'UseSnowCastle': 0.0, 'UseIceCastle': 0.25, 'UseSandRock': 1.0,
          'ColorSand': [0.5, 0.4, 0.3, 1.0]}
patched, actor, worked = surfaces.split(ICE, plan(ICE, values))
check('Icebound Mine: snow and ice patched, sand to the actor',
      set(patched) == {'UseSnowCastle', 'UseIceCastle'}
      and set(actor) == {'UseSandRock', 'ColorSand'} and not worked, (patched, actor, worked))
_p, _a, worked = surfaces.split(DOCK, plan(DOCK, {'ColorStoneCastle': [1, 0, 0, 0]}))
check('Hidden Dock: a stone colour it works out is reported, not written', worked == ['ColorStoneCastle'])

pkg = fresh(ICE)
sizes = [e['size'] for e in pkg.exports]
done, missing = surfaces.write(pkg, patched)
check('both calls rewritten', done == {'UseSnowCastle': 1, 'UseIceCastle': 1}, done)
check('no export changes size', [e['size'] for e in pkg.exports] == sizes)
uasset, uexp = pkg.write()
again = Package(uasset, uexp)
again.load_payloads()
check('the written package reads back with the new values',
      surfaces.scan(again)['set'] == {'UseSnowCastle': 0.0, 'UseIceCastle': 0.25},
      surfaces.scan(again))
check('only the four value bytes of each call differ',
      len(uexp) == len(fresh(ICE).write()[1])
      and sum(a != b for a, b in zip(uexp, fresh(ICE).write()[1])) <= 8)

text = surfaces.script(actor)
check('the actor sets the sand at BeginPlay',
      'def ReceiveBeginPlay' in text and '"UseSandRock", 1.0' in text
      and 'MakeColor(0.5, 0.4, 0.3, 1.0)' in text and 'UseSnowCastle' not in text, text)
check('no actor when the script sets every value',
      surfaces.package(ICE, MOD, plan(ICE, {'UseSnowCastle': 0})) == {})
blobs = surfaces.package(ICE, MOD, plan(ICE, values))
path = surfaces.package_path(MOD)
check('the actor Blueprint is built', list(blobs) == [path], list(blobs))
names = [n for n, _h in Package(blobs[path][0]).names]
check('it imports the collection and the library calls, and names its values',
      all(w in names for w in ('MPC_Enviro_Level', 'KismetMaterialLibrary', 'SetScalarParameterValue',
                               'SetVectorParameterValue', 'UseSandRock', 'ColorSand',
                               surfaces.class_name(MOD) + '_C')), names)


try:
    from riftlord.build import levelbuild
    b = levelbuild.Builder.around(*gamefiles.package(mapdata.map_path(ICE)), name=ICE)
except Exception as exc:                             # noqa: BLE001
    b = None
    check('a level builder for Icebound Mine', False, '%s: %s' % (type(exc).__name__, exc))
if b is not None:
    report = {'notes': [], 'skipped': []}
    surfaces.apply(b, plan(ICE, values), report, level=ICE)
    check('apply notes snow, ice and sand', len(report['notes']) == 1 and not report['skipped'],
          report)
    check('apply patched the level script', surfaces.scan(b.pkg)['set']['UseIceCastle'] == 0.25)
    ua, ux = b.pkg.write()
    files = {'OMD/Content' + path[len('/Game'):] + '.uasset': blobs[path][0],
             'OMD/Content' + path[len('/Game'):] + '.uexp': blobs[path][1],
             'OMD/Content/Mods/%s/Maps/Test.umap' % MOD: ua,
             'OMD/Content/Mods/%s/Maps/Test.uexp' % MOD: ux}
    rows = surfaces.check_files(files, plan(ICE, values), ICE)
    check('the probe gates pass: Blueprint in the pak, placed with its class preloaded',
          len(rows) == 2 and all(r['ok'] for r in rows), rows)

bad = [c for c in checks if not c[1]]
for name, ok, detail in checks:
    print('%s %s%s' % ('ok  ' if ok else 'FAIL', name, '' if ok else ': %s' % (detail,)))
print('%d/%d' % (len(checks) - len(bad), len(checks)))
sys.exit(1 if bad else 0)
