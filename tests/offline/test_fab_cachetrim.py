"""Offline checks on the Fab converter cache trim (riftlord/fab/cachetrim.py) in a made-up cache:
its paths match the converter's, a pack converted more than a day ago is emptied (its ETA record
kept, its extracted zip removed with it), a fresh, busy or locked pack is left alone, shared
folders stay, a held lock of an unknown pack stops the trim, a dry run touches nothing, a pack
used between the plan and the move is kept; and the Fab Kits server runs it in a thread that
holds the queue until the folders are moved aside, logging under Fab Kits.

    python3 test_fab_cachetrim.py [SCRATCH]
"""
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)

SCRATCH = sys.argv[1] if len(sys.argv) > 1 else None
ROOT = tempfile.mkdtemp(prefix='fabtrim-', dir=SCRATCH)
for name in ('XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME'):
    os.environ[name] = os.path.join(ROOT, name.split('_')[1].lower())

from riftlord.fab import cachetrim as T  # noqa: E402

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


NOW = time.time()
OLD = NOW - 2 * 24 * 3600
CACHE = T.cache_root()


def put(path, size=1000, when=OLD):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as fh:
        fh.write(b'x' * size)
    os.utime(path, (when, when))


def age(folder, when):
    """Every folder under `folder` (and it) modified at `when`, deepest first."""
    for dp, _dn, _fn in sorted(os.walk(folder), key=lambda w: -w[0].count(os.sep)):
        os.utime(dp, (when, when))


def pack(pid, when=OLD):
    """A working folder as a convert leaves it."""
    base = os.path.join(CACHE, pid)
    for sub in ('src', 'bake', 'img', 'tex-ispc_1', 'mesh', 'grid', 'stage/OMD/Content'):
        put(os.path.join(base, sub, 'ab', 'f.bin'), when=when)
    put(os.path.join(base, 'stage_seconds.json'), 10, when)
    age(base, when)
    return base


def build():
    shutil.rmtree(CACHE, ignore_errors=True)
    pack('Old')
    pack('Fresh', NOW - 3600)
    pack('Busy')
    pack('Docks')                                     # its convert lock is held below
    pack('Terrain_Scenario_06')                       # its terrain build lock is held below
    pack('Mediterranean')                             # converted from the zip extracted in imports/
    put(os.path.join(CACHE, 'imports', 'mediterranean_asset_pack-0cac139b89', 'a.fbx'))
    age(os.path.join(CACHE, 'imports'), OLD)
    os.makedirs(os.path.join(CACHE, 'empty'))
    os.utime(os.path.join(CACHE, 'empty'), (OLD, OLD))
    for shared in ('vaultinfo/x.json', 'measure/x/measure.json', 'locks/dsa60895b3c928aV1.lock'):
        put(os.path.join(CACHE, shared))
    put(os.path.join(T.terrain_locks(), 'Scenario_06.lock'), 0)


def hold(path):
    fh = open(path, 'a+')
    T._lock(fh)
    return fh


def gone(base):
    return sorted(os.listdir(base)) if os.path.isdir(base) else None


try:
    # 1. the converter's own paths
    from riftlord.fab import library, vault
    check('cache root is the converter\'s', T.cache_root() == library.cache_root(), (T.cache_root(), library.cache_root()))
    check('lock paths are the converter\'s', T.lock_path('EF_Lewis') == vault.lock_path('EF_Lewis')
          and T.lock_path('m pack.zip') == vault.lock_path('m pack.zip'))
    from riftlord.fab import packs
    check('a loose pack\'s id as packs.identity names it',
          T.loose_pack_id('mediterranean_asset_pack') == 'Mediterranean'
          and T.loose_pack_id('my_loose-pack') == packs.identity('my_loose-pack', ['/x/my_loose-pack'])['id'])
    from riftlord.terrainkit import build as tbuild, sources
    check('terrain locks and prefix are the builder\'s',
          T.terrain_locks() == os.path.join(sources.cache_dir(), 'locks') and T.TERRAIN_PREFIX == tbuild.PACK_PREFIX)

    # 2. a dry run lists and touches nothing
    build()
    docks = hold(os.path.join(CACHE, 'locks', 'dsa60895b3c928aV1.lock'))
    terrain = hold(os.path.join(T.terrain_locks(), 'Scenario_06.lock'))
    rep = T.trim(busy={'Busy'}, dry_run=True)
    listed = sorted(r['pack'] for r in rep['packs'])
    check('dry run: the old packs listed', listed == ['Mediterranean', 'Old', 'empty'], listed)
    check('dry run: sizes counted', rep['freed'] >= 6 * 1000 * 2 + 1000, rep['freed'])
    check('dry run: nothing touched', gone(os.path.join(CACHE, 'Old')) == [
        'bake', 'grid', 'img', 'mesh', 'src', 'stage', 'stage_seconds.json', 'tex-ispc_1'])
    kept = {k['pack']: k['why'] for k in rep['kept']}
    check('busy, locked and fresh packs kept', kept.get('Busy') == 'busy' and kept.get('Docks') == 'busy'
          and kept.get('Terrain_Scenario_06') == 'busy' and kept.get('Fresh', '').startswith('used within'), kept)

    # 3. the trim
    lines, moved = [], []
    rep = T.trim(busy={'Busy'}, log=lines.append, on_moved=lambda: moved.append(1))
    check('old pack emptied, its ETA record kept', gone(os.path.join(CACHE, 'Old')) == ['stage_seconds.json'])
    check('its extracted zip removed with its pack', gone(os.path.join(CACHE, 'imports')) == []
          and gone(os.path.join(CACHE, 'Mediterranean')) == ['stage_seconds.json'])
    check('an empty folder removed', gone(os.path.join(CACHE, 'empty')) is None)
    check('fresh, busy and locked packs untouched', all(len(gone(os.path.join(CACHE, p))) == 8 for p in
                                                        ('Fresh', 'Busy', 'Docks', 'Terrain_Scenario_06')))
    check('shared folders untouched', all(os.path.exists(os.path.join(CACHE, p)) for p in
                                          ('vaultinfo/x.json', 'measure/x/measure.json', 'locks/dsa60895b3c928aV1.lock')))
    check('trash gone', not os.path.exists(os.path.join(CACHE, T.TRASH)))
    check('freed counted and logged', rep['freed'] > 0 and lines and lines[-1].startswith('Fab converter cache: freed')
          and any('from Old' in ln for ln in lines), (rep['freed'], lines))
    check('on_moved once', moved == [1], moved)
    again = T.trim(busy={'Busy'}, log=lines.append)
    check('a second trim frees nothing', again['freed'] == 0 and not again['packs'], again)
    docks.close()
    terrain.close()

    # 3b. --only: just the named packs
    build()
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        T.main(['--only', 'Old, empty'])
    check('--only: the named packs emptied', gone(os.path.join(CACHE, 'Old')) == ['stage_seconds.json']
          and gone(os.path.join(CACHE, 'empty')) is None)
    check('--only: other old packs untouched', len(gone(os.path.join(CACHE, 'Mediterranean'))) == 8
          and gone(os.path.join(CACHE, 'imports')) == ['mediterranean_asset_pack-0cac139b89']
          and len(gone(os.path.join(CACHE, 'Busy'))) == 8)
    with contextlib.redirect_stdout(io.StringIO()) as out:
        T.main(['--only', 'Mediterranean', '--dry-run'])
    check('--only with --dry-run: lists the named pack only', 'Mediterranean' in out.getvalue()
          and 'Busy' not in out.getvalue() and len(gone(os.path.join(CACHE, 'Mediterranean'))) == 8, out.getvalue())

    # 4. a lock of an unknown pack stops the trim
    build()
    stranger = hold(os.path.join(CACHE, 'locks', 'SomeoneElsesPack.lock'))
    rep = T.trim()
    check('unknown held lock: stopped, nothing touched', rep['stopped'] and 'SomeoneElsesPack' in rep['stopped']
          and len(gone(os.path.join(CACHE, 'Old'))) == 8, rep)
    stranger.close()
    os.remove(os.path.join(CACHE, 'locks', 'SomeoneElsesPack.lock'))
    check('a listed vault item maps its lock', T.owners(rows=[{'item': 'SomeoneElsesPack', 'pack_id': 'Old'}])
          ['Old'] >= {T.lock_path('SomeoneElsesPack')})

    # 5. a pack used between the plan and the move is kept
    got = T.plan(now=NOW)
    rec = next(r for r in got['packs'] if r['pack'] == 'Old')
    os.makedirs(os.path.join(CACHE, 'Old', 'stage'), exist_ok=True)
    os.utime(os.path.join(CACHE, 'Old', 'stage'))
    check('used meanwhile: kept', T._move_out(rec, os.path.join(CACHE, T.TRASH), NOW, T.MAX_AGE_S) is None
          and len(gone(os.path.join(CACHE, 'Old'))) == 8)

    # 6. the Fab Kits server: a thread, logged under Fab Kits, the queue held until moved aside
    build()
    from riftlord.app import fabkits as F
    from riftlord.core import console
    F.library_root = lambda: os.path.join(ROOT, 'library')
    started = []
    F._start = lambda item, *a, **kw: started.append(item)
    F._TRIM['thread'] = object()
    F.QUEUE.append({'item': 'X', 'title': 'X', 'scale': None, 'packId': 'Old', 'added': NOW})
    F._advance()
    check('server: the queue waits while a trim runs', not started and len(F.QUEUE) == 1)
    F._TRIM['thread'] = None
    before = console.since(0)['next']
    F.QUEUE[:] = []
    check('server: a trim starts', F.trim_cache())
    t0 = time.time()
    while F._trimming() and time.time() - t0 < 30:
        time.sleep(0.05)
    t0 = time.time()
    while not any('freed' in e['text'] for e in console.since(before)['lines']) and time.time() - t0 < 30:
        time.sleep(0.05)
    lines = [e for e in console.since(before)['lines'] if e['source'] == console.FAB_KITS]
    check('server: freed logged under Fab Kits', any(e['text'].startswith('Fab converter cache: freed') for e in lines),
          [e['text'] for e in lines])
    check('server: old pack emptied', gone(os.path.join(CACHE, 'Old')) == ['stage_seconds.json'])
    build()
    F.QUEUE.append({'item': 'X', 'title': 'X', 'scale': None, 'packId': 'Old', 'added': NOW})
    F._TRIM['thread'] = object()                     # keeps the queue still while the trim is asked for
    check('server: no second trim while one runs', not F.trim_cache())
    F._TRIM['thread'] = None
    started[:] = []
    F._advance = lambda: None                        # the queued pack stays queued for the busy check
    check('server: a queued pack is busy', F.trim_cache())
    t0 = time.time()
    while F._trimming() and time.time() - t0 < 30:
        time.sleep(0.05)
    check('server: the queued pack kept', len(gone(os.path.join(CACHE, 'Old'))) == 8)
finally:
    shutil.rmtree(ROOT, ignore_errors=True)

bad = [c for c in checks if not c[1]]
for name, ok, detail in checks:
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, '' if ok else '  [%s]' % (detail,)))
print('%d/%d passed' % (len(checks) - len(bad), len(checks)))
sys.exit(1 if bad else 0)
