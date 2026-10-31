"""The gate and the release check never read the user's Missions: their records are the test
missions (tests/fixtures/missions).

    python3 tests/offline/test_no_user_missions.py

- No gate, comparison, release-check, test-server or test file names the user's Missions
  folder (USER_PATTERNS).
- The test servers, the comparisons and the release check copy records from the test
  missions; every record and Missions item the gate and the release check name is there.
- `buildcompare.py RECORD --kinds` for the gate's records, run with an audit hook, opens
  no file under the user's Missions folder.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TOOLS = os.path.join(ROOT, 'tools')
sys.path.insert(0, ROOT)
sys.path.insert(0, TOOLS)

import buildcompare  # noqa: E402
import gate  # noqa: E402
import release_check  # noqa: E402
import testservers  # noqa: E402

#: How code names the user's Missions folder.
USER_PATTERNS = [r'USER_MISSIONS', r"'RiftLord',\s*'Missions'", r'RiftLord/Missions',
                 r"user_data\(\s*['\"]Missions['\"]\s*\)"]
#: The files the gate and the release check run, and the tests they run.
SCANNED = ['tools/gate.py', 'tools/buildcompare.py', 'tools/release_check.py',
           'tools/testservers.py']
SCANNED_DIRS = ['tests/offline', 'tests/ui_smoke', 'tests/fixtures']
#: The user's Missions folder.
USER = os.path.join(os.path.expanduser('~'), '.local', 'share', 'RiftLord', 'Missions')

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


me = os.path.abspath(__file__)
hits = []
for path in scanned_files():
    if os.path.abspath(path) == me:
        continue
    with open(path, encoding='utf-8', errors='replace') as fh:
        for n, line in enumerate(fh, 1):
            if any(re.search(p, line) for p in USER_PATTERNS):
                hits.append('%s:%d' % (os.path.relpath(path, ROOT), n))
check('no gate, release-check or test file names the user\'s Missions', not hits, hits[:6])

fixtures = os.path.join(ROOT, 'tests', 'fixtures', 'missions')


def default(fn, name):
    import inspect
    return inspect.signature(fn).parameters[name].default


check('the test servers copy from the test missions',
      os.path.samefile(testservers.FIXTURE_MISSIONS, fixtures) and
      default(testservers.copy_mission, 'source') == testservers.FIXTURE_MISSIONS and
      default(testservers.TestServer.__init__, 'mission_source') == testservers.FIXTURE_MISSIONS)
check('the comparisons copy from the test missions', buildcompare.MISSIONS ==
      testservers.FIXTURE_MISSIONS)
records = os.path.join(fixtures, '.riftlord', 'records')
named = list(gate.RECORDS) + [gate.UI_RECORD, release_check.DEFAULT_RECORD]
check('every record the gate and the release check name is a test mission',
      all(os.path.isfile(os.path.join(records, r + '.json')) for r in named), named)
items = [i for extra in gate.RECORDS.values() for i in extra]
check('every Missions item the comparisons copy in is a test mission',
      all(os.path.exists(os.path.join(fixtures, i)) or
          any(n.startswith(i + '.') for n in os.listdir(fixtures)) for i in items), items)
check('every compared record has a baseline entry', all(r in buildcompare.BASELINES
                                                        for r in gate.RECORDS))

HOOK = r'''
import runpy, sys
opened = []
sys.addaudithook(lambda e, a: opened.append(a[0]) if e == 'open' and a and isinstance(a[0], str) else None)
sys.argv = [sys.argv[1]] + sys.argv[2:]
try:
    runpy.run_path(sys.argv[0], run_name='__main__')
except SystemExit:
    pass
finally:
    print('OPENED', '\n'.join(opened), file=sys.stderr)
'''
got = subprocess.run([sys.executable, '-B', '-c', HOOK, os.path.join(TOOLS, 'buildcompare.py'),
                      ','.join(gate.RECORDS), '--kinds'], cwd=ROOT, capture_output=True,
                     text=True, timeout=600)
opened = [ln for ln in got.stderr.split('OPENED', 1)[-1].splitlines() if ln.strip()]
user = [p for p in opened if os.path.abspath(p).startswith(USER + os.sep)]
check('the comparisons\' record reading opens nothing in the user\'s Missions',
      got.returncode == 0 and not user and opened, (got.returncode, user[:3],
                                                     got.stdout.strip()[-200:]))

for name, ok, detail in checks:
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, '' if ok else '  (%s)' % (detail,)))
print('%d checks, %d failed' % (len(checks), sum(not ok for _n, ok, _d in checks)))
raise SystemExit(0 if all(ok for _n, ok, _d in checks) else 1)
