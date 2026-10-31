"""The dev gate's selection (tools/gateselect.py, gate.Plan): a step whose recorded inputs
are unchanged is skipped; a changed module it imported, a changed data file it read, a
file added to a folder it listed, a changed folder it gave a program, or a changed file
outside the tree reruns it; a module it never read does not; in a module it imported, a
change to a function it ran or to the module's own code reruns it, and a change to a
function it never ran or to a comment does not; a module it looked for and did not find
that appears reruns it, and a module added beside the ones it imported does not; a
process that could not log what ran counts its modules as read whole; the certify step's
regenerate certifier still reruns on a change to the minimap code and no longer on one to
Crystal Defense (riftlord/mapeditor/crystal.py), and signature() changes only with what
the record holds; a change to SHARED code runs
every step; a step added to the offline steps file (gate.STEPS_FILE, outside SHARED) runs
alone, and a malformed one stops the gate; a failed or killed step is never kept as a pass; deleting the records runs
everything; the release gate runs everything; UI cases rerun by their tags, and every case
of the suite is tagged; a UI case that failed only on a timing limit while a heavy gate job
ran (the offline pool, a comparison, the certify step) is run once more, and only then.

    python3 tests/offline/test_gateselect.py
"""
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, 'tools'))

import gate        # noqa: E402
import gateselect  # noqa: E402
import testservers  # noqa: E402

TMP = tempfile.mkdtemp(prefix='gateselect-test-')
TREE = os.path.join(TMP, 'tree')
RUN = os.path.join(TMP, 'run')
RECORDS = os.path.join(TMP, 'records')
#: Outside the tree and the temp folder (whose files are never inputs): on scratch.
os.makedirs(testservers.SCRATCH, exist_ok=True)
BASE = tempfile.mkdtemp(prefix='gateselect-test-', dir=testservers.SCRATCH)
OUTSIDE = os.path.join(BASE, 'outside')
checks = []

#: The step: imports pkg.a (which imports pkg.b) and runs pkg.a.used (never pkg.a.unused),
#: looks for pkg.optional (absent), reads a data file and an outside file, lists pkg/data,
#: never reads pkg/c.py, and hands web/ui to a program (`true`).
STEP = r'''
import os, signal, subprocess, sys
sys.path.insert(0, os.getcwd())
import pkg.a
pkg.a.used()
try:
    import pkg.optional
except ImportError:
    pass
open(os.path.join('pkg', 'data', 'table.json')).read()
open(sys.argv[1]).read()
os.listdir(os.path.join('pkg', 'data'))
with open(os.path.join(sys.argv[2], 'made.txt'), 'w') as fh:
    fh.write('x')
open(os.path.join(sys.argv[2], 'made.txt')).read()
subprocess.run(['true', 'web/ui'])
if len(sys.argv) > 3:
    os.kill(os.getpid(), signal.SIGKILL) if sys.argv[3] == 'kill' else sys.exit(1)
'''
A = '''from pkg import b


def used():
    return b.X  # %s


def unused():
    return %d
'''
FILES = {'pkg/__init__.py': '', 'pkg/a.py': A % ('', 1), 'pkg/b.py': 'X = 1\n',
         'pkg/c.py': 'Y = 2\n', 'pkg/data/table.json': '{}', 'web/ui/page.js': 'let a = 1\n',
         'riftlord/core/shared.py': 'Z = 3\n', 'web/mapeditor/src/main.js': 'm\n',
         'web/onslaught/src/App.svelte': 'o\n', 'tests/offline/step.py': STEP}


def check(name, ok, detail=''):
    checks.append(ok)
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, (': %s' % (detail,)) if detail else ''))


def write(rel, text):
    path = os.path.join(TREE, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as fh:
        fh.write(text)


def digests():
    out = {}
    for base, dirs, files in os.walk(TREE):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for f in files:
            path = os.path.join(base, f)
            with open(path, 'rb') as fh:
                out[os.path.relpath(path, TREE)] = hashlib.sha256(fh.read()).hexdigest()
    return out


def selection():
    return gateselect.Selection(TREE, digests(), RUN, records=RECORDS)


def run_step(name, plan, *how):
    """Run the step as the gate does (recording), keep or drop its record. Its exit code."""
    folder = plan.folder('offline', name)
    shutil.rmtree(folder, ignore_errors=True)
    scratch = tempfile.mkdtemp(dir=BASE)
    env = plan.env(dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), folder)
    t = time.time()
    code = subprocess.call([sys.executable, '-B', 'tests/offline/step.py',
                            os.path.join(OUTSIDE, 'input.txt'), scratch] + list(how),
                           cwd=TREE, env=env)
    plan.done('offline', name, code == 0, folder, t, {'argv': ['step']})
    shutil.rmtree(scratch, ignore_errors=True)
    return code


def reason(name='step'):
    return selection().reason('offline', name, {'argv': ['step']})


#: The regenerate certifier's imports (minimapscore, the drawing code, and Crystal Defense
#: as minimapredraw.floor reaches it: leveldata.level -> omd3level.read ->
#: onslaught.map_report -> onslaught.gamemode), then drawing code it runs.
CERTIFY_IMPORTS = '''
import os, sys
sys.path.insert(0, os.getcwd())
from riftlord.build.minimap import minimapscore, minimapredraw  # noqa: F401
from riftlord.onslaught import gamemode  # noqa: F401
'''
CERTIFY_STEP = CERTIFY_IMPORTS + '''
import numpy as np
m = np.zeros((9, 9), bool)
m[3:6, 3:6] = True
minimapredraw._components(minimapredraw._grow(m, 1))
'''


def edit_function(rel, name, tree):
    """Add a line at the end of the body of function `name` (module level) in `rel`."""
    path = os.path.join(tree, rel)
    with open(path) as fh:
        lines = fh.read().split('\n')
    at = next(i for i, ln in enumerate(lines) if ln.startswith('def %s(' % name)) + 1
    while at < len(lines) and (not lines[at].strip() or lines[at][0] in ' \t'):
        at += 1
    while not lines[at - 1].strip():
        at -= 1
    lines.insert(at, '    _gate_test = 1  # noqa: F841')
    with open(path, 'w') as fh:
        fh.write('\n'.join(lines))


def certify_selection():
    """The regenerate certifier's real modules, recorded as the gate records a step: a
    change to a minimap function it ran reruns it; one to a Crystal Defense function (its
    build problems) does not, though crystal.py is imported."""
    tree = os.path.join(TMP, 'certify-tree')
    got = subprocess.run([sys.executable, '-B', '-c', CERTIFY_IMPORTS + '''
print('\\n'.join(sorted(m.__file__ for m in list(sys.modules.values())
                        if (getattr(m, '__file__', None) or '').startswith(os.getcwd() + '/'))))
'''], cwd=ROOT, capture_output=True, text=True,
                         env=dict((k, v) for k, v in os.environ.items() if k != gateselect.ENV))
    for path in got.stdout.splitlines():
        rel = os.path.relpath(path, ROOT)
        os.makedirs(os.path.dirname(os.path.join(tree, rel)), exist_ok=True)
        shutil.copy(path, os.path.join(tree, rel))
    with open(os.path.join(tree, 'step.py'), 'w') as fh:
        fh.write(CERTIFY_STEP)
    if not os.path.exists(os.path.join(tree, 'riftlord', 'mapeditor', 'crystal.py')):
        check('the certifier\'s imports reach crystal.py', False, got.stderr[-300:])
        return

    def sel():
        out = {}
        for base, dirs, files in os.walk(tree):
            dirs[:] = [d for d in dirs if d != '__pycache__']
            for f in files:
                with open(os.path.join(base, f), 'rb') as fh:
                    out[os.path.relpath(os.path.join(base, f), tree)] = \
                        hashlib.sha256(fh.read()).hexdigest()
        return gateselect.Selection(tree, out, RUN, records=RECORDS)
    plan = gate.Plan(sel(), False, RUN)
    folder = plan.folder('certify', 'test')
    t = time.time()
    code = subprocess.call([sys.executable, '-B', 'step.py'], cwd=tree,
                           env=plan.env(dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), folder))
    plan.done('certify', 'test', code == 0, folder, t)
    rec = sel().load('certify', 'test') or {}
    crystal = (rec.get('code') or {}).get('riftlord/mapeditor/crystal.py')
    check('certifier: crystal.py is imported, and none of its functions ran',
          code == 0 and crystal is not None and crystal[1] == [], (code, crystal))
    gate.CERTIFY_CACHE = os.path.join(TMP, 'certify-cache')
    out = os.path.join(TMP, 'certify-out')
    os.makedirs(out)
    with open(os.path.join(out, 'state.json'), 'w') as fh:
        fh.write('{}')

    def key():
        return gate.certify_key(gate.Plan(sel(), False, RUN), 'test', ['argv'])
    gate.certify_keep('test', key(), out)
    check('certify cache: kept under its key and found by it',
          gate.certify_cached('test', key()) == os.path.join(gate.CERTIFY_CACHE, 'test') and
          os.path.exists(os.path.join(gate.CERTIFY_CACHE, 'test', 'state.json')))
    check('certify cache: not found under another key, nor without a record',
          gate.certify_cached('test', 'other') is None and
          gate.certify_key(gate.Plan(None, False, RUN), 'test', ['argv']) is None)
    edit_function('riftlord/mapeditor/crystal.py', 'problems', tree)
    check('certifier: a change to Crystal Defense\'s build problems does not rerun it',
          sel().reason('certify', 'test') is None, sel().reason('certify', 'test'))
    check('certify cache: ... and keeps its drawings', bool(gate.certify_cached('test', key())))
    edit_function('riftlord/build/minimap/minimapredraw.py', '_grow', tree)
    check('certifier: a change to a minimap function it ran reruns it',
          sel().reason('certify', 'test') == 'changed: riftlord/build/minimap/minimapredraw.py',
          sel().reason('certify', 'test'))
    check('certify cache: ... and its drawings are not reused',
          gate.certify_cached('test', key()) is None)


def main():
    for rel, text in FILES.items():
        write(rel, text)
    os.makedirs(OUTSIDE)
    with open(os.path.join(OUTSIDE, 'input.txt'), 'w') as fh:
        fh.write('outside')
    os.makedirs(RUN)
    time.sleep(0.05)                                  # the inputs are older than the step

    # a pass is kept, and nothing changed: skipped
    check('no record: the step runs', reason() == 'no record of a pass', reason())
    code = run_step('step', gate.Plan(selection(), False, RUN))
    rec = selection().load('offline', 'step')
    check('a pass is kept', code == 0 and rec is not None, code)
    tree = (rec or {}).get('tree') or {}
    code = (rec or {}).get('code') or {}
    check('the record holds the data file it read and the script it ran, whole',
          {'pkg/data/table.json', 'tests/offline/step.py'} <= set(tree), sorted(tree))
    check('... and the modules it imported, with the functions that ran',
          {'pkg/a.py', 'pkg/b.py', 'pkg/__init__.py'} <= set(code) and
          code['pkg/a.py'][1] == ['used'] and not set(code) & set(tree), code)
    check('... and the module it looked for and did not find',
          ((rec or {}).get('missing') or {}).get('pkg.optional') == [],
          (rec or {}).get('missing'))
    check('a module it never read is not in it', 'pkg/c.py' not in tree and 'pkg/c.py' not in code)
    check('a file it wrote is not in it, nor its scratch folder',
          not [p for p in (rec or {}).get('files') or {} if 'made.txt' in p],
          sorted((rec or {}).get('files') or {}))
    check('the outside file it read is kept by size and time',
          os.path.realpath(os.path.join(OUTSIDE, 'input.txt')) in ((rec or {}).get('files') or {}))
    check('the folder given to a program is kept', 'web/ui' in ((rec or {}).get('programs') or {}),
          (rec or {}).get('programs'))
    check('unchanged inputs: skipped', reason() is None, reason())

    # the offline steps' list (gate.STEPS_FILE) is data outside SHARED: an entry added to
    # it runs alone, and a malformed list stops the gate naming the file
    write(gate.STEPS_FILE, json.dumps([['step', ['step']], ['new', ['tests/offline/new.py']]]))
    got = {n: selection().reason('offline', n, {'argv': a}) for n, a in gate.load_steps(TREE)}
    check('a step added to the steps file runs alone',
          got == {'step': None, 'new': 'no record of a pass'}, got)
    bad = []
    for text in ('[["step", ["step"]', '{"step": ["step"]}', '[["step", "step"]]',
                 '[["step", ["step"]], ["step", ["other"]]]'):
        write(gate.STEPS_FILE, text)
        try:
            gate.load_steps(TREE)
            bad.append(text)
        except SystemExit as exc:
            if gate.STEPS_FILE not in str(exc):
                bad.append(text)
    os.remove(os.path.join(TREE, gate.STEPS_FILE))
    check('a malformed or missing steps file stops the gate, naming it', not bad, bad)
    live = gate.load_steps(ROOT)
    gone = [a[0] for _n, a in live if a[0].endswith('.py') and not os.path.exists(
        os.path.join(ROOT, a[0]))]
    check('every script the live steps file names exists (%d steps)' % len(live), not gone, gone)

    # changes it did not read: still skipped
    write('pkg/c.py', 'Y = 3\n')
    check('a module it never read changed: still skipped', reason() is None, reason())
    write('web/mapeditor/src/main.js', 'm2\n')
    check('a web file it never read changed: still skipped', reason() is None, reason())
    write('pkg/a.py', A % ('', 2))
    check('a function it never ran changed: still skipped', reason() is None, reason())
    write('pkg/a.py', A % ('a comment', 1))
    check('a comment in a function it ran changed: still skipped', reason() is None, reason())
    write('pkg/a.py', A.replace('b.X', 'b.X + 1') % ('', 1))
    check('a function it ran changed: rerun', reason() == 'changed: pkg/a.py', reason())
    write('pkg/a.py', A.replace('def unused():', 'def unused(x=1):') % ('', 1))
    check('the module\'s own code changed (a default argument): rerun',
          reason() == 'changed: pkg/a.py', reason())
    write('pkg/a.py', A % ('', 1))
    write('pkg/d.py', 'W = 1\n')
    check('a module added beside the ones it imported: still skipped', reason() is None,
          reason())
    os.remove(os.path.join(TREE, 'pkg/d.py'))
    write('pkg/optional.py', 'V = 1\n')
    check('a module it looked for and did not find appears: rerun',
          reason() == 'a module it looked for changed: pkg.optional (pkg/optional.py)', reason())
    os.remove(os.path.join(TREE, 'pkg/optional.py'))
    check('gone again: skipped', reason() is None, reason())
    sig = selection().signature('offline', 'step')
    sig_left = selection().signature('offline', 'step', ['pkg/data/table.json'])
    write('pkg/a.py', A % ('', 3))
    check('signature: unchanged by a function the step never ran',
          sig and selection().signature('offline', 'step') == sig)
    write('pkg/data/table.json', '{"b": 1}')
    check('signature: changed by a data file it read',
          selection().signature('offline', 'step') != sig)
    check('signature: unchanged when that file is left out',
          selection().signature('offline', 'step', ['pkg/data/table.json']) == sig_left)
    write('pkg/data/table.json', '{}')
    write('pkg/a.py', A % ('', 1))
    log = os.path.join(TMP, 'unmonitored')
    os.makedirs(log)
    with open(os.path.join(log, '1-1.log'), 'w') as fh:
        fh.write('i\t%s\nL\t%s\n' % (os.path.join(TREE, 'pkg', 'b.py'), os.path.join(TREE, 'pkg')))
    got = selection().collect(log, time.time())
    check('a process that logged no code ran: its modules read whole, its listings kept',
          'pkg/b.py' in got['tree'] and not got['code'] and 'pkg' in got['listed'], got)
    certify_selection()

    # changes it read: rerun
    write('pkg/b.py', 'X = 2\n')
    check('a module it imported (through another) changed: rerun',
          reason() == 'changed: pkg/b.py', reason())
    write('pkg/b.py', 'X = 1\n')
    check('changed back: skipped again', reason() is None, reason())
    write('pkg/data/table.json', '{"a": 1}')
    check('a data file it read changed: rerun', reason() == 'changed: pkg/data/table.json',
          reason())
    write('pkg/data/table.json', '{}')
    write('pkg/data/new.json', '{}')
    check('a file added to a folder it listed: rerun',
          (reason() or '').startswith('files added or removed in pkg/data'), reason())
    os.remove(os.path.join(TREE, 'pkg/data/new.json'))
    write('web/ui/page.js', 'let a = 2\n')
    check('a folder it gave a program changed: rerun',
          reason() == 'changed: web/ui (given to a program)', reason())
    write('web/ui/page.js', 'let a = 1\n')
    with open(os.path.join(OUTSIDE, 'input.txt'), 'w') as fh:
        fh.write('outside, changed')
    check('an outside file it read changed: rerun',
          (reason() or '').startswith('changed: ' + os.path.realpath(OUTSIDE)), reason())
    run_step('step', gate.Plan(selection(), False, RUN))
    check('passed again: skipped', reason() is None, reason())
    check('the step\'s own argv changed: rerun',
          (selection().reason('offline', 'step', {'argv': ['other']}) or '').startswith(
              'the step changed'))

    # shared code runs everything
    write('riftlord/core/shared.py', 'Z = 4\n')
    check('shared code changed: rerun though it never read it',
          reason() == 'shared code changed: riftlord/core/shared.py', reason())
    sel = selection()
    sel.ui_passed('onslaught', 5)
    check('shared code changed: a UI case passed since is skipped',
          sel.ui_reason('onslaught') is None, sel.ui_reason('onslaught'))
    write('riftlord/core/shared.py', 'Z = 5\n')
    check('shared code changed again: the UI case reruns',
          (selection().ui_reason('onslaught') or '').startswith('shared code changed'))
    run_step('step', gate.Plan(selection(), False, RUN))
    check('passed after the shared change: skipped', reason() is None, reason())

    # a failure is never a pass
    write('pkg/b.py', 'X = 9\n')
    code = run_step('step', gate.Plan(selection(), False, RUN), 'fail')
    check('a failed step leaves no record', code != 0 and
          selection().load('offline', 'step') is None, code)
    write('pkg/b.py', 'X = 1\n')
    check('a failed step reruns even when its inputs go back',
          reason() == 'no record of a pass', reason())
    run_step('step', gate.Plan(selection(), False, RUN))
    code = run_step('step', gate.Plan(selection(), False, RUN), 'kill')
    check('a killed step leaves no record', code == -signal.SIGKILL and
          selection().load('offline', 'step') is None, code)
    run_step('step', gate.Plan(selection(), False, RUN))
    check('passed again', reason() is None, reason())

    # deleting the records, the release gate
    plan = gate.Plan(selection(), True, RUN)
    check('the release gate runs a step with an unchanged record',
          plan.why('offline', 'step', {'argv': ['step']}) == 'release gate')
    plan = gate.Plan(selection(), False, RUN)
    check('the dev gate skips it, and says so',
          plan.why('offline', 'step', {'argv': ['step']}) is None and
          [s['name'] for s in plan.skipped] == ['step'])
    shutil.rmtree(RECORDS)
    check('records deleted: the step runs', reason() == 'no record of a pass', reason())

    # UI cases by their tags
    sel = selection()
    for case in ('mapeditor', 'onslaught'):
        sel.ui_passed(case, 10)
    write('web/mapeditor/src/main.js', 'm3\n')
    sel = selection()
    check('a Map Editor file changed: the Map Editor case reruns',
          sel.ui_reason('mapeditor') == 'changed: web/mapeditor/src/main.js',
          sel.ui_reason('mapeditor'))
    check('... and the Onslaught case does not', sel.ui_reason('onslaught') is None,
          sel.ui_reason('onslaught'))
    sel.ui_passed('mapeditor', 10)
    write('web/mapeditor/src/new.js', 'n\n')
    check('a file added under its tags: rerun',
          selection().ui_reason('mapeditor') == 'added: web/mapeditor/src/new.js',
          selection().ui_reason('mapeditor'))
    check('an untagged case always runs',
          selection().ui_reason('nosuchcase') == 'not tagged in gateselect.UI_TAGS')
    cases = gateselect.ui_cases(ROOT)
    untagged = [c for c in cases if c not in gateselect.UI_TAGS]
    check('every case of the suite is tagged (%d cases)' % len(cases), cases and not untagged,
          untagged)
    stale = [c for c in gateselect.UI_TAGS if c not in cases]
    check('every tagged case is in the suite', not stale, stale)
    missing = sorted({p for c in cases for p in gateselect.ui_paths(c) or []
                      if not os.path.exists(os.path.join(ROOT, p.rstrip('/')))})
    check('every tagged path exists', not missing, missing)
    missing = [p for p in gateselect.SHARED if not os.path.exists(os.path.join(ROOT, p.rstrip('/')))]
    check('every SHARED path exists', not missing, missing)

    # the end-of-run failure summary
    log = os.path.join(TMP, 'step.log')
    with open(log, 'w') as fh:
        fh.write('PASS  one\nFAIL  two: the second check\nTraceback (most recent call last):\n'
                 '  File "x.py"\nValueError: bad\n2 checks, 1 failed\n')
    crash = os.path.join(TMP, 'crash.log')
    with open(crash, 'w') as fh:
        fh.write('starting\nTraceback (most recent call last):\n  File "x.py"\nKeyError: \'k\'\n')
    got = gate.failures([{'step': 'offline: a', 'ok': False, 'log': log, 'tail': []},
                         {'step': 'offline: b', 'ok': True, 'log': log, 'tail': []},
                         {'step': 'offline: c', 'ok': False, 'log': crash, 'tail': []}])
    check('the failure summary names each failed step with what failed and its log',
          [(f['step'], f['what'], f['log']) for f in got] ==
          [('offline: a', 'FAIL  two: the second check', log), ('offline: c', "KeyError: 'k'", crash)],
          got)

    # a UI case that failed only on a timing limit under a heavy gate job runs once more
    t = {'what': 'first 8 compound previews in 44494 ms', 'seconds': 44.5, 'limit': 15.7}
    info = {'ui_timing_only': {'compoundbrowser': dict(t, started=100.0),
                               'certified': dict(t, started=900.0),
                               'late': dict(t, started=3000.0)}}
    pool = {'job': 'offline pool', 'start': 0.0, 'end': 200.0}
    load = [pool, {'job': 'compare: 900_Fixture_Edit_P', 'start': 0.0, 'end': 400.0},
            {'job': 'certify', 'start': 400.0, 'end': 2700.0}]
    got = gate.load_reruns(info, load)
    check('a timing-only failure under any heavy gate job is run again, naming the jobs; '
          'one after them all is not',
          [(c, t_['under']) for c, t_ in got] ==
          [('certified', ['certify']),
           ('compoundbrowser', ['offline pool', 'compare: 900_Fixture_Edit_P'])], got)
    check('one after the pool but under the certify step is run again (2026-10-30)',
          [c for c, _t in gate.load_reruns(info, [pool, load[2]])] == ['certified', 'compoundbrowser'])
    check('no heavy job ran: nothing is run again', gate.load_reruns(info, []) == [])
    check('a failure that is not on a timing limit is never run again',
          gate.load_reruns({'ui_timing_only': {}}, load) == [])
    spans = []
    with gate.under_load(spans, 'offline pool'):
        pass
    try:
        with gate.under_load(spans, 'certify'):
            raise RuntimeError
    except RuntimeError:
        pass
    check('a heavy job\'s span is kept, also when it fails',
          [s['job'] for s in spans] == ['offline pool', 'certify'] and
          all(s['start'] <= s['end'] for s in spans), spans)
    checks_ = [{'check': 'ui: compoundbrowser', 'ok': False}, {'check': 'ui: mapeditor', 'ok': True}]
    check('the release check passes when its only failure passed on the rerun',
          gate.release_rescued(checks_, {'compoundbrowser'}))
    check('a second failure stands', not gate.release_rescued(checks_, set()))
    check('another failure beside it stands', not gate.release_rescued(
        checks_ + [{'check': 'ui: crystal', 'ok': False}], {'compoundbrowser'}))
    check('so does a failed endpoint check', not gate.release_rescued(
        checks_ + [{'check': 'map editor: materials', 'ok': False}], {'compoundbrowser'}))

    shutil.rmtree(TMP, ignore_errors=True)
    shutil.rmtree(BASE, ignore_errors=True)
    print('%d of %d passed' % (sum(checks), len(checks)))
    return 0 if all(checks) else 1


if __name__ == '__main__':
    raise SystemExit(main())
