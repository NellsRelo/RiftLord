"""Offline checks on the probe's level-build cache key (riftlord.mapeditor.probecache).

    python3 test_probecache.py

The key must change with every input of a level build and stay the same when only a
minion-only staged pak changes. A scratch staged folder holds a copy of a minion mod
(the pinned Wraiths_P.pak, tests/fixtures/fixturelibs.PINNED; those checks are skipped
without it) and a small pak the test writes that is not a minion mod. Also: a LevelCache entry round-trips the
build folder and report, and is not used when the output folder already exists. Exit 0
when every check passes.
"""

import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'fixtures'))

from riftlord.mapeditor import probecache  # noqa: E402
from riftlord.missions import modminions  # noqa: E402

MINION_PAK = 'Wraiths_P.pak'
SCRATCH_PARENT = '/mnt/2tb_pro_a/tmp' if os.path.isdir('/mnt/2tb_pro_a/tmp') else None

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))
    print('  %s  %s%s' % ('PASS' if ok else 'FAIL', name, (': ' + str(detail)) if detail else ''))


def main():
    scratch = tempfile.mkdtemp(prefix='probecache-test-', dir=SCRATCH_PARENT)
    try:
        return run(scratch)
    finally:
        modminions.ROOTS = None
        modminions._SCAN.clear()
        shutil.rmtree(scratch, ignore_errors=True)


def run(scratch):
    staged = os.path.join(scratch, 'staged')
    os.makedirs(staged)
    from riftlord.core import paksource
    other = os.path.join(scratch, 'ProbeCacheOther_P.pak')    # a small pak, not a minion mod
    paksource.write_pak(other, {'OMD/Content/Mods/ProbeCacheOther/Marker.uasset': b'other'})
    picture = os.path.join(scratch, 'picture.png')
    with open(picture, 'wb') as fh:
        fh.write(b'picture one')
    kwargs = {'out': os.path.join(scratch, 'out'), 'plan': {'level': 'Scenario_01', 'added': []},
              'title': 'Cache Test', 'guids': {'X_Mission01': b'\x01' * 16},
              'load_picture': {'path': picture, 'crop': [0, 0, 1, 1]}}
    modminions.ROOTS = [staged]
    modminions._SCAN.clear()
    base = probecache.key(['Scenario_01'], kwargs, staged)

    print('the key follows every input')
    check('the same inputs give the same key', probecache.key(['Scenario_01'], kwargs, staged) == base)
    check('another level', probecache.key(['Scenario_02'], kwargs, staged) != base)
    changed = dict(kwargs, plan={'level': 'Scenario_01', 'added': [{'kind': 'kit'}]})
    check('a changed plan', probecache.key(['Scenario_01'], changed, staged) != base)
    changed = dict(kwargs, guids={'X_Mission01': b'\x02' * 16})
    check('changed Guids', probecache.key(['Scenario_01'], changed, staged) != base)
    with open(picture, 'wb') as fh:
        fh.write(b'picture two')
    check('a picture file with new content', probecache.key(['Scenario_01'], kwargs, staged) != base)
    with open(picture, 'wb') as fh:
        fh.write(b'picture one')
    check('a module switch', probecache.key(['Scenario_01'], kwargs, staged,
                                           ['navregen.ENABLED=false']) != base)
    os.environ['RIFTLORD_PROBECACHE_TEST'] = '1'
    check('a RIFTLORD_ environment variable', probecache.key(['Scenario_01'], kwargs, staged) != base)
    del os.environ['RIFTLORD_PROBECACHE_TEST']
    shutil.copyfile(other, os.path.join(staged, os.path.basename(other)))
    with_other = probecache.key(['Scenario_01'], kwargs, staged)
    check('a staged pak that is not a minion mod', with_other != base, os.path.basename(other))
    os.utime(os.path.join(staged, os.path.basename(other)), ns=(1, 1))
    check('the same pak touched', probecache.key(['Scenario_01'], kwargs, staged) != with_other)

    import fixturelibs
    installed = fixturelibs.pinned_mod(MINION_PAK)
    if installed:
        print('a minion-only staged pak')
        from riftlord.core import paksource
        target = os.path.join(staged, MINION_PAK)
        shutil.copyfile(installed, target)
        modminions._SCAN.clear()
        check('it is recognised as minion-only',
              os.path.abspath(target) in probecache.minion_only_paks())
        before = probecache.key(['Scenario_01'], kwargs, staged)
        files = paksource.entries(target)
        #: texture bulk data, so the mod's tables and manifest still read
        name = sorted(n for n in files if n.endswith('.ubulk') and '/Tables/' not in n)[0]
        blob = bytearray(files[name])
        blob[len(blob) // 2] ^= 0xFF
        files[name] = bytes(blob)
        paksource.write(target, files, keep_omui=False)
        modminions._SCAN.clear()
        check('changing it leaves the key as it was (its minions are carried afresh)',
              probecache.key(['Scenario_01'], kwargs, staged) == before)
    else:
        print('  skipped: %s is not installed' % installed)

    print('an entry')
    cache = probecache.LevelCache(os.path.join(scratch, 'cache'))
    out = os.path.join(scratch, 'build')
    os.makedirs(os.path.join(out, 'OMD'))
    with open(os.path.join(out, 'OMD', 'a.bin'), 'wb') as fh:
        fh.write(b'level bytes')
    check('an entry is kept', cache.put('k1', out, {'written': 'x', 'n': (1, 2)}))
    check('an existing output folder is never overwritten', cache.get('k1', out) is None)
    shutil.rmtree(out)
    got = cache.get('k1', out)
    with open(os.path.join(out, 'OMD', 'a.bin'), 'rb') as fh:
        same = fh.read() == b'level bytes'
    check('a hit restores the build and the report', got == {'written': 'x', 'n': (1, 2)} and same)
    shutil.rmtree(out)
    check('another key misses', cache.get('k2', out) is None and not os.path.exists(out))

    failed = [c for c in checks if not c[1]]
    print('%d of %d checks passed' % (len(checks) - len(failed), len(checks)))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
