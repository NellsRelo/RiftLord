"""Offline checks on the Fab Kits tab's server side (riftlord/app/fabkits.py) against the
stand-in converter (tests/ui_smoke/fakefab.py), in temporary data, config and cache folders:
which packs are listed (by `kind`, else by meshes), the convert queue (one at a time, in
order, reordered, cancelled while waiting, carried on by the server), progress from
`fraction` and `eta_s` and without them, a failed convert's short line with the converter's
words in the console only, and the Settings values (vault folder, memory limit, workers)
passed to the converter only when it reports them.

    python3 test_appfabkits.py [SCRATCH]
"""
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)

SCRATCH = sys.argv[1] if len(sys.argv) > 1 else None
ROOT = tempfile.mkdtemp(prefix='appfabkits-', dir=SCRATCH)
for name in ('XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME'):
    os.environ[name] = os.path.join(ROOT, name.split('_')[1].lower())
os.environ['RIFTLORD_FAKEFAB_STATE'] = os.path.join(ROOT, 'fakefab')
os.environ['RIFTLORD_FABCONVERT'] = os.path.join(REPO, 'tests', 'ui_smoke', 'fakefab.py')

from riftlord.app import fabkits as F  # noqa: E402
from riftlord.core import console  # noqa: E402

F.unavailable = lambda: None                       # collision cooking is not what is tested
LIB = os.path.join(ROOT, 'library')
F.library_root = lambda: LIB

CHEST, CASTLE, BRASS = 'Stylized0bce0f98767dV3', 'DesertCa3432654395afV1', 'CityofBra13001ef61fcV1'
NINJA, VOICE, SHARK = 'FluidNin01097781a25fV10', 'AmberNPC5ee3071625f9V1', 'BullShar77aa01c2d3e4V1'

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


def wait(test, timeout=60.0):
    t = time.time()
    while time.time() - t < timeout:
        got = test()
        if got:
            return got
        time.sleep(0.05)
    return None


def converter_args(cmd):
    """Every argument list the stand-in converter was run with for `cmd`."""
    with open(os.path.join(os.environ['RIFTLORD_FAKEFAB_STATE'], 'args.jsonl')) as fh:
        return [a for a in (json.loads(ln) for ln in fh) if a and a[0] == cmd]


def job(item):
    return F.jobs().get(item) or {}


try:
    # 1. which packs make level pieces
    check('kinds: buildings, props and mixed listed; creature and none not',
          [F.makes_pieces({'kind': k, 'meshes': 5}) for k in
           ('building', 'props', 'mixed', 'creature', 'none')] == [True, True, True, False, False])
    check('no kind (an older converter): listed by having meshes',
          F.makes_pieces({'meshes': 3}) and not F.makes_pieces({'meshes': 0}))
    check('a loose pack is always listed', F.makes_pieces({'source': 'file', 'kind': 'none'}))
    data = F.vault(max_age=None)
    rows = {r['item']: r for r in data['items']}
    listed = sorted(i for i, r in rows.items() if F.makes_pieces(r))
    check('the stand-in vault: the voice pack and the shark are not listed',
          VOICE not in listed and SHARK not in listed and CHEST in listed, listed)
    try:
        F.convert(SHARK)
        check('a creature pack is refused', False)
    except SystemExit as exc:
        check('a creature pack is refused', 'no level pieces' in str(exc), str(exc))

    # 2. the queue: one at a time, in order; reordered; a waiting pack cancelled
    F.set_settings({'memoryMb': 6144, 'workers': 2})
    F.convert(CHEST)
    F.convert(CASTLE)
    F.convert(BRASS, 1.5)
    q = [e['item'] for e in F.queue_view()]
    check('the first pack runs, the others wait in order',
          job(CHEST).get('state') == 'running' and q == [CASTLE, BRASS], (job(CHEST).get('state'), q))
    try:
        F.convert(CASTLE)
        check('a queued pack cannot be queued twice', False)
    except SystemExit as exc:
        check('a queued pack cannot be queued twice', 'already queued' in str(exc), str(exc))
    q = [e['item'] for e in F.move(BRASS, 0)]
    check('a waiting pack moved to the front', q == [BRASS, CASTLE], q)
    F.cancel(CASTLE)
    q = [e['item'] for e in F.queue_view()]
    check('cancel takes a waiting pack off the queue', q == [BRASS] and not job(CASTLE), q)

    # 3. progress from fraction and eta_s
    seen = wait(lambda: job(CHEST).get('eta') is not None and job(CHEST).get('percent', 0) > 0
                and job(CHEST))
    check('progress and time left from the converter\'s fraction and eta_s',
          seen and 0 < seen['percent'] <= 100 and seen['eta'] >= 0, seen and (seen['percent'], seen['eta']))
    done = wait(lambda: job(CHEST).get('state') not in (None, 'running') and job(CHEST))
    check('the first pack converted', done and done['state'] == 'done', done and done['state'])
    nxt = wait(lambda: job(BRASS).get('state') == 'running' and job(BRASS))
    check('the next pack started by itself', bool(nxt) and not F.queue_view())
    probs = wait(lambda: job(BRASS).get('state') not in (None, 'running') and job(BRASS))
    args = converter_args('convert')
    chest_args = next((a for a in args if a[1] == CHEST), [])
    brass_args = next((a for a in args if a[1] == BRASS), [])
    check('Settings\' limits passed to a converter that reports them',
          '--memory-mb' in chest_args and chest_args[chest_args.index('--memory-mb') + 1] == '6144'
          and '--workers' in chest_args and chest_args[chest_args.index('--workers') + 1] == '2',
          chest_args)
    check('the scale passed with the queued pack', '--scale' in brass_args and
          brass_args[brass_args.index('--scale') + 1] == '1.5', brass_args)
    check('problems reported', probs and probs['state'] == 'problems' and probs['problemCount'] == 2,
          probs and probs['state'])

    # 4. a failed convert: a short line on the tab, the converter's words in the console
    before = console.since(0)['next']
    F.convert(NINJA)
    failed = wait(lambda: job(NINJA).get('state') not in (None, 'running') and job(NINJA))
    view = json.dumps({k: v for k, v in (failed or {}).items() if k != 'log'})
    lines = console.since(before)['lines']
    check('a crash shows "Conversion failed"; the job carries none of the converter\'s words '
          'outside its log tail',
          failed and failed['failure'] == 'Conversion failed' and 'MemoryError' not in view
          and 'guard' not in view, failed and failed.get('failure'))
    check('the converter\'s words go to the console, under Fab Kits',
          any(ln['source'] == 'Fab Kits' and ln['level'] == 'error' and 'MemoryError' in ln['text']
              for ln in lines), [ln['text'] for ln in lines][-4:])

    # 5. progress without fraction or eta_s (an older converter)
    old = F.Job('x', 'convert', [])
    old._event({'stage': 'bake', 'index': 2, 'stages': 7, 'done': 1, 'total': 2})
    check('no fraction: progress from stage and count, no time left',
          abs(old.percent - 100.0 * 2.5 / 7) < 0.01 and old.eta is None, (old.percent, old.eta))
    old._event({'stage': 'bake', 'index': 2, 'stages': 7, 'done': 2, 'total': 2, 'fraction': 0.5,
                'eta_s': 12.4})
    check('fraction and eta_s used when given', old.percent == 50.0 and old.eta == 12.4,
          (old.percent, old.eta))

    # 6. limits only for a converter that reports them
    with F._VAULT_LOCK:
        keep = F._VAULT['data'].get('settings')
        F._VAULT['data']['settings'] = None
    check('no `settings` from the converter: no limit arguments', F._limit_args() == [])
    with F._VAULT_LOCK:
        F._VAULT['data']['settings'] = keep
    try:
        F.set_settings({'workers': F.max_workers() + 1})
        check('more workers than the limit refused', False)
    except SystemExit as exc:
        check('more workers than the limit refused', 'Workers' in str(exc), str(exc))
    got = F.set_settings({'memoryMb': None, 'workers': ''})
    check('limits reset to the converter\'s defaults', got['memoryMb'] is None and got['workers'] is None
          and got['defaults'] == {'memory_mb': 8192, 'workers': 2, 'cpu_share': 0.25}, got)

    # 7. the vault folder
    try:
        F.set_settings({'vaultRoot': os.path.join(ROOT, 'nowhere')})
        check('a missing vault folder refused', False)
    except SystemExit as exc:
        check('a missing vault folder refused', 'not a folder' in str(exc), str(exc))
    chosen = os.path.join(ROOT, 'MyVault')
    os.makedirs(chosen)
    got = F.set_settings({'vaultRoot': chosen})
    check('a vault folder kept, the old listing dropped', got['vaultRoot'] == chosen and not got['listed'],
          got)
    data = F.vault(max_age=None)
    check('the listing reads the chosen folder', data['roots'] == {'found': [chosen], 'searched': [chosen]}
          and converter_args('vault')[-1][-2:] == ['--root', chosen], data['roots'])
    F.set_settings({'vaultRoot': ''})
    data = F.vault(max_age=None)
    check('Reset: the converter searches again', '--root' not in converter_args('vault')[-1]
          and data['roots']['found'] == ['/fake/EpicVault'], data['roots'])

    # 8. stopping the server empties the queue
    F.convert(CHEST)
    F.convert(CASTLE)
    F.stop_all()
    wait(lambda: job(CHEST).get('state') != 'running')
    time.sleep(0.3)
    check('stop_all: the queue emptied and nothing started after it',
          not F.queue_view() and job(CASTLE).get('state') != 'running', F.queue_view())
finally:
    F.stop_all()
    time.sleep(0.2)
    shutil.rmtree(ROOT, ignore_errors=True)

bad = [c for c in checks if not c[1]]
for name, ok, detail in checks:
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, '' if ok else '  [%s]' % (detail,)))
print('%d/%d passed' % (len(checks) - len(bad), len(checks)))
sys.exit(1 if bad else 0)
