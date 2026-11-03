"""Offline checks on a door whose host wave plays some spawn statements twice.

    python3 test_replayed_spawns.py [--keep]

Scenario_04's wave 5 runs twelve of door 2's spawn statements twice (56 spawns from 44
statements; Scenario08 and WC_16 wave 7 door 3 do the same). One statement holds one row,
so the writer used to put two rows into one statement and, given fewer rows, drop
statements other rows still sat in: Raid (built from Scenario_04) refused with "wave 5
spawn calls: 75 in the Blueprint, 86 authored".

Each case is built raw into a scratch folder (wavespec.build runs check_built and
flowsim) and read back (wavespec.read_onslaught): door 2's wave 5 must hold exactly the
authored units, in order, and every other door and wave as read. Cases: the wave as it
is, its first 14 rows removed (the pattern that lost rows), and 6 rows added. Exit 0 when
every check passes.
"""

import argparse
import collections
import copy
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from riftlord.onslaught import bppatch  # noqa: E402
from riftlord.onslaught import wavespec  # noqa: E402

HOST = 'Scenario_04'
#: The wave and door whose declaration replays statements.
WAVE, DOOR = 5, '2'
#: Rows the 'fewer' case removes from the front, and the 'more' case adds at the end.
FEWER, MORE = 14, 6
SCRATCH_PARENT = '/mnt/2tb_pro_a/tmp' if os.path.isdir('/mnt/2tb_pro_a/tmp') else None

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))
    print('  %s  %s%s' % ('PASS' if ok else 'FAIL', name, (': ' + str(detail)) if detail else ''))


def units(spec, wave, door):
    return [r['unit'] for r in (spec['waves'][wave - 1].get('spawns') or {}).get(door, [])
            if r.get('unit')]


def replayed(coord):
    """Spawn statements the host's wave runs more than once at the door."""
    pkg, names, resolve = bppatch.load(coord)
    _w, mm = wavespec.ubergraph(pkg, names, resolve)
    entry = bppatch.entry_points(pkg, names, resolve)['Onslaught_Start']
    idx = wavespec.declared(mm, entry, names, resolve, WAVE, int(DOOR))
    runs = collections.Counter(i for i, _c in wavespec.spawn_statements(mm, idx, resolve))
    return sum(1 for n in runs.values() if n > 1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--keep', action='store_true', help='keep the scratch folder')
    args = ap.parse_args(argv)
    scratch = tempfile.mkdtemp(prefix='replayed-spawns-test-', dir=SCRATCH_PARENT)
    try:
        return run(scratch)
    finally:
        if args.keep:
            print('kept %s' % scratch)
        else:
            shutil.rmtree(scratch, ignore_errors=True)


def run(scratch):
    from riftlord.build import makelevel
    from riftlord.onslaught import savetest
    _path, coord_name = makelevel.coordinator_of(HOST)
    twice = replayed(coord_name)
    check('the host replays spawn statements at wave %d door %s' % (WAVE, DOOR), twice,
          '%d statement(s)' % twice)
    base = wavespec.read_onslaught(HOST)

    def fewer(rows):
        del rows[:FEWER]

    def more(rows):
        rows += copy.deepcopy(rows[:MORE])

    for case, change in (('as read', None), ('%d fewer' % FEWER, fewer),
                         ('%d more' % MORE, more)):
        spec = copy.deepcopy(base)
        if change:
            change(spec['waves'][WAVE - 1]['spawns'][DOOR])
        out = os.path.join(scratch, 'out')
        shutil.rmtree(out, ignore_errors=True)
        try:
            res = wavespec.build(dict(spec, map=HOST, raw=True, out=out,
                                      title='Replayed Spawns Test'))
        except SystemExit as exc:
            check('%s: saves' % case, False, exc)
            continue
        bad = [c for c in res['checks'] if not c['ok']]
        check('%s: saves with every check passing' % case, not bad,
              '; '.join('%s: %s' % (c['name'], c['detail']) for c in bad))
        coord = res['report']['coordinator']
        leaf = os.path.basename(coord) + '.uasset'
        hits = [os.path.join(d, f) for d, _s, fs in os.walk(out) for f in fs if f == leaf]
        if not hits:
            check('%s: the build wrote its coordinator' % case, False, coord)
            continue
        with open(hits[0], 'rb') as a, open(hits[0][:-len('.uasset')] + '.uexp', 'rb') as b:
            ua, ux = a.read(), b.read()
        with savetest._coordinator_as(coord_name, ua, ux):
            back = wavespec.read_onslaught(HOST)
        want, got = units(spec, WAVE, DOOR), units(back, WAVE, DOOR)
        check('%s: wave %d door %s reads back as authored (%d rows)'
              % (case, WAVE, DOOR, len(want)), got == want,
              '' if got == want else 'got %d rows' % len(got))
        others = [(w + 1, d) for w in range(len(spec['waves']))
                  for d in sorted(set(spec['waves'][w].get('spawns') or {})
                                  | set(back['waves'][w].get('spawns') or {}))
                  if (w + 1, d) != (WAVE, DOOR)
                  and units(spec, w + 1, d) != units(back, w + 1, d)]
        check('%s: every other wave and door reads back unchanged' % case, not others,
              others[:4])

    failed = [c for c in checks if not c[1]]
    print('%d of %d checks passed' % (len(checks) - len(failed), len(checks)))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
