"""Offline checks on a spec that authors fewer waves than its host coordinator.

    python3 test_fewer_waves.py [--keep]

Scenario_06 ships five waves (its close is wave 6, wave 5 holds the OrcBoss). A spec of
its first two waves is built raw into a scratch folder, and the built coordinator is run
through the simulator (flowsim.step) for every wave and spawner: two waves spawn, every
spawner reaches OnslaughtEnd at wave 3 and none before, nothing past wave 2 spawns,
breaks, pays coin or fires an event, and the function holds no spawn call the authored
waves do not reach. The read-back (wavespec.read_onslaught) gives two waves. Exit 0 when
every check passes.
"""

import argparse
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from riftlord.onslaught import bppatch  # noqa: E402
from riftlord.onslaught import flowsim  # noqa: E402
from riftlord.onslaught import wavespec  # noqa: E402

HOST = 'Scenario_06'
WANT = 2
#: Waves and spawners simulated: past the host's close, and every spawner index it uses.
WAVES, SPAWNERS = 9, 4
#: Calls nothing past the last authored wave may make.
AFTER_LAST = ('OnslaughtSpawnEntities', 'OnslaughtAction', 'OnslaughtGiveCoin') \
    + wavespec.BREAK_FNS
SCRATCH_PARENT = '/mnt/2tb_pro_a/tmp' if os.path.isdir('/mnt/2tb_pro_a/tmp') else None

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))
    print('  %s  %s%s' % ('PASS' if ok else 'FAIL', name, (': ' + str(detail)) if detail else ''))


def simulate(ua, ux):
    """({(wave, spawner): {call: count}}, statements reached, spawn statements in the
    function) of a coordinator held in memory."""
    pkg, names, resolve = flowsim._from_blobs(ua, ux)
    _w, mm = wavespec.ubergraph(pkg, names, resolve)
    entry = bppatch.entry_points(pkg, names, resolve)['Onslaught_Start']
    out, reached = {}, set()
    for wave in range(1, WAVES + 1):
        for spawner in range(1, SPAWNERS + 1):
            _how, path = flowsim.step(mm, entry, {'Wave': wave, 'Spawner': spawner},
                                      names, resolve)
            got = {}
            for i in path:
                node = mm[i][2]
                if node.name in ('FinalFunction', 'LocalFinalFunction'):
                    fn = str(resolve(node.ops[0][1]))
                    if fn.startswith('Onslaught'):
                        got[fn] = got.get(fn, 0) + 1
                if wave <= WANT:
                    reached.add(i)
            out[(wave, spawner)] = got
    spawning = {i for i, (_m, _s, n) in enumerate(mm)
                if bppatch.calls_named(n, 'OnslaughtSpawnEntities', resolve)}
    return out, reached, spawning


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--keep', action='store_true', help='keep the scratch folder')
    args = ap.parse_args(argv)
    scratch = tempfile.mkdtemp(prefix='fewer-waves-test-', dir=SCRATCH_PARENT)
    try:
        return run(scratch)
    finally:
        if args.keep:
            print('kept %s' % scratch)
        else:
            shutil.rmtree(scratch, ignore_errors=True)


def run(scratch):
    from riftlord.build import makelevel
    _path, coord_name = makelevel.coordinator_of(HOST)
    sh = wavespec.shape(coord_name)
    check('the host has more waves than the spec (%s, close %d)' % (sh['waves'], sh['close']),
          len(sh['waves']) > WANT)
    spec = wavespec.read_onslaught(HOST)
    spec['waves'] = spec['waves'][:WANT]
    out = os.path.join(scratch, 'out')
    notes = []
    res = wavespec.build(dict(spec, map=HOST, raw=True, out=out, title='Fewer Waves Test'),
                         notes.append)
    coord = res['report']['coordinator']
    leaf = os.path.basename(coord) + '.uasset'
    hits = [os.path.join(d, f) for d, _s, fs in os.walk(out) for f in fs if f == leaf]
    check('the build wrote its coordinator', hits, coord)
    if not hits:
        return 1
    with open(hits[0], 'rb') as a, open(hits[0][:-len('.uasset')] + '.uexp', 'rb') as b:
        ua, ux = a.read(), b.read()
    calls, reached, spawning = simulate(ua, ux)

    spawned = sorted({w for (w, _s), got in calls.items() if got.get('OnslaughtSpawnEntities')})
    check('the simulator finds exactly the authored waves spawning', spawned == list(range(1, WANT + 1)),
          spawned)
    early = sorted(k for k, got in calls.items() if k[0] <= WANT and got.get('OnslaughtEnd'))
    check('no spawner ends the onslaught before the last authored wave', not early, early)
    ends = sorted(s for (w, s), got in calls.items() if w == WANT + 1 and got.get('OnslaughtEnd'))
    check('every spawner ends the onslaught at wave %d' % (WANT + 1),
          ends == list(range(1, SPAWNERS + 1)), ends)
    late = sorted('wave %d spawner %d: %s' % (w, s, ', '.join(sorted(
        f for f in got if f in AFTER_LAST))) for (w, s), got in calls.items()
        if w > WANT and any(f in AFTER_LAST for f in got))
    check('nothing past wave %d spawns, breaks, pays coin or fires an event' % WANT,
          not late, late[:3])
    dead = sorted(spawning - reached)
    check('every spawn call left in the function belongs to an authored wave',
          not dead, '%d unreached spawn statement(s)' % len(dead))
    check('the build notes the dropped host waves',
          any('host wave' in n and 'dropped' in n for n in notes),
          [n for n in notes if 'host wave' in n][:3])

    from riftlord.onslaught import savetest
    with savetest._coordinator_as(coord_name, ua, ux):
        back = wavespec.read_onslaught(HOST)
    check('the read-back has %d waves' % WANT, len(back['waves']) == WANT, len(back['waves']))

    failed = [c for c in checks if not c[1]]
    print('%d of %d checks passed' % (len(checks) - len(failed), len(checks)))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
