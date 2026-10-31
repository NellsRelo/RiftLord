"""The gate reads no installed mod of the game's: with RIFTLORD_SUPPORTED_PAKS set (as the gate
sets it for every step: the fixture Paks folder), nothing opens or lists the game's
Paks/~mods or Paks/LogicMods.

    python3 tests/offline/test_no_user_mods.py

- Every path that finds installed mods (gamepath.mods_dir and mod_paks, supportedmods,
  modminions, makemod.installed_tables, customcampaign.installed, probecache.pak_stamps,
  reconcile.scan, missionscramble.tier_paks, missionunlocks.scan, navpatch.mod_maps and
  live_index), run under an
  audit hook against an empty stand-in Paks folder, opens and lists nothing in the game's
  mod folders, and finds what the stand-in holds.
- No gate, release-check or test file builds a path to the game's ~mods or LogicMods
  (GAME_PATTERNS) other than fixturelibs' `--pin` step and the release check's isolation
  check (which only watches ~mods for changes).
- The gate gives its steps the stand-in (gate.step_environment) and fails a step whose
  record read the game's mod folders (gateselect.game_mod_reads).
"""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))

import gate  # noqa: E402
import gateselect  # noqa: E402

#: How code builds a path to the game's mod folders.
GAME_PATTERNS = [r"'Content',\s*'Paks',\s*'(~mods|LogicMods)'", r'Content/Paks/(~mods|LogicMods)',
                 r"PAK_DIR,\s*'(~mods|LogicMods)'", r"paks\(\),\s*'(~mods|LogicMods)'"]
#: Files allowed to name them: fixturelibs' `--pin` step (a person's), and this test; and
#: lines: the release check's isolation check, which only stats ~mods to see it unchanged.
ALLOWED = ['tests/fixtures/fixturelibs.py', 'tests/offline/test_no_user_mods.py']
ALLOWED_LINES = [('tools/release_check.py', 'MODS = os.path.normpath(')]
SCANNED = ['tools/gate.py', 'tools/gateselect.py', 'tools/buildcompare.py',
           'tools/release_check.py', 'tools/testservers.py']
SCANNED_DIRS = ['tests/offline', 'tests/ui_smoke', 'tests/fixtures']

PROBE = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
watch = tuple(json.loads(sys.argv[2]))
touched = []
def hook(event, args):
    if event in ('open', 'os.listdir', 'os.scandir') and args and isinstance(args[0], str):
        p = os.path.realpath(os.path.abspath(args[0]))
        if (p + '/').startswith(watch):
            touched.append('%s %s' % (event, p))
sys.addaudithook(hook)
found, failed = {}, {}
def run(name, fn):
    try:
        found[name] = fn()
    except (Exception, SystemExit) as exc:
        failed[name] = '%s: %s' % (type(exc).__name__, exc)
from riftlord.core import gamepath, supportedmods
from riftlord.missions import modminions, customcampaign, missionscramble, missionunlocks
from riftlord.build import makemod, reconcile
from riftlord.build.navigation import navpatch
from riftlord.mapeditor import probecache
run('mods_dir', lambda: gamepath.mods_dir())
run('mod_paks', lambda: gamepath.mod_paks())
run('supportedmods', lambda: sorted(m['id'] for m in supportedmods.detect()
                                    if m.get('status') == supportedmods.INSTALLED))
run('modminions', lambda: sorted(modminions.mods()))
run('installed_tables', lambda: sorted(makemod.installed_tables()))
run('customcampaign', lambda: len(customcampaign.installed()))
run('pak_stamps', lambda: len(probecache.pak_stamps()))
run('reconcile', lambda: len(reconcile.scan()['mods']))
run('tier_paks', lambda: len(missionscramble.tier_paks()))
run('missionunlocks', lambda: sum(r['mod'] is not None for side in ('heroes', 'items', 'excluded')
                                  for r in missionunlocks.scan()[side]))
run('mod_maps', lambda: len(navpatch.mod_maps()))
run('live_index', lambda: len(navpatch.live_index()))
print(json.dumps({'touched': touched, 'found': found, 'failed': failed}, default=str))
'''

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


def scanned_files():
    out = [os.path.join(ROOT, f) for f in SCANNED]
    for d in SCANNED_DIRS:
        for base, dirs, names in os.walk(os.path.join(ROOT, d)):
            dirs[:] = [x for x in dirs if x not in ('__pycache__', 'node_modules', 'missions')]
            out += [os.path.join(base, n) for n in names if n.endswith(('.py', '.js'))]
    return sorted(out)


hits = []
for path in scanned_files():
    rel = os.path.relpath(path, ROOT)
    if rel in ALLOWED:
        continue
    with open(path, encoding='utf-8', errors='replace') as fh:
        for n, line in enumerate(fh, 1):
            if any(re.search(p, line) for p in GAME_PATTERNS) and \
                    not any(rel == f and text in line for f, text in ALLOWED_LINES):
                hits.append('%s:%d' % (rel, n))
check('no gate, release-check or test file builds a path to the game\'s ~mods or LogicMods',
      not hits, hits[:6])

folders = gateselect.game_mod_folders()
if folders:
    with tempfile.TemporaryDirectory(prefix='no-user-mods-') as tmp:
        stand_in = os.path.join(tmp, 'Paks')
        for sub in ('~mods', 'LogicMods'):
            os.makedirs(os.path.join(stand_in, sub))
        env = dict(os.environ, RIFTLORD_SUPPORTED_PAKS=stand_in, OMD3_BUCKET=os.path.join(
            tmp, 'Missions'), XDG_CACHE_HOME=os.path.join(tmp, 'cache'), TMPDIR=tmp)
        os.makedirs(env['OMD3_BUCKET'])
        got = subprocess.run([sys.executable, '-B', '-c', PROBE, ROOT,
                              json.dumps([f + '/' for f in folders])],
                             cwd=tmp, env=env, capture_output=True, text=True, timeout=1800)
        try:
            res = json.loads(got.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            res = None
        check('the mod-finding paths ran', res is not None and not res['failed'],
              res['failed'] if res else got.stderr[-400:])
        if res:
            check('they open and list nothing in the game\'s ~mods or LogicMods',
                  not res['touched'], res['touched'][:5])
            found = res['found']
            check('they use the stand-in Paks folder', found.get('mod_paks') == stand_in and
                  found.get('mods_dir') == os.path.join(stand_in, '~mods'), found)
            check('the stand-in is empty, and so is what they find',
                  found.get('supportedmods') == [] and found.get('modminions') == [] and
                  found.get('installed_tables') == [] and found.get('customcampaign') == 0 and
                  found.get('mod_maps') == 0 and found.get('tier_paks') == 0 and
                  found.get('missionunlocks') == 0, found)
else:
    check('SKIP no game install: the mod-finding paths are not run', True)

import inspect  # noqa: E402
env = inspect.getsource(gate.step_environment)
check('the gate gives its steps the stand-in Paks folder and its own type data',
      'RIFTLORD_SUPPORTED_PAKS' in env and 'RIFTLORD_TYPES' in env)
if folders:
    rec = {'files': {os.path.join(folders[0], 'X_P.pak'): [1, 1], '/elsewhere/a.pak': [1, 1]},
           'listed_outside': {folders[-1]: 'x'}}
    check('a step whose record read the game\'s mod folders is caught',
          gateselect.game_mod_reads(rec) == sorted([os.path.join(folders[0], 'X_P.pak'),
                                                    folders[-1]]), gateselect.game_mod_reads(rec))

for name, ok, detail in checks:
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, '' if ok else '  (%s)' % (detail,)))
print('%d checks, %d failed' % (len(checks), sum(not ok for _n, ok, _d in checks)))
raise SystemExit(0 if all(ok for _n, ok, _d in checks) else 1)
