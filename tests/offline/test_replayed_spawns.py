"""Offline checks on a door whose host wave plays some spawn statements twice.

    python3 test_replayed_spawns.py [--keep]

Scenario_04's wave 5 runs twelve of door 2's spawn statements twice (56 spawns from 44
statements); Scenario08 and WC_16 run eleven of wave 7 door 3's twice. One statement holds
one row, so the writer used to put two rows into one statement and, given fewer rows, drop
statements other rows still sat in: Raid (built from Scenario_04) refused with "wave 5
spawn calls: 75 in the Blueprint, 86 authored". Rewriting such a door also moved the
waits inside the twice-run block beside other rows, and every rewritten door ran the
host's waits twice (the host's and the row's own).

Each coordinator is authored (wavespec.author) from its own read-out (read_onslaught) in
three cases: as read, the door's first 14 rows removed (the pattern that lost rows), and
6 rows added. Then:
  - an untouched door declares exactly the host's calls: spawns, waits and groups, in order
  - the edited door declares exactly its rows' spawns and waits, in order
  - the door reads back as authored, pauses included
Scenario_04's cases are also built through wavespec.build (check_built and flowsim). Exit 0
when every check passes.
"""

import argparse
import copy
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from riftlord.build import makelevel  # noqa: E402
from riftlord.onslaught import bppatch  # noqa: E402
from riftlord.onslaught import onslaught  # noqa: E402
from riftlord.onslaught import savetest  # noqa: E402
from riftlord.onslaught import wavespec  # noqa: E402

#: (coordinator, wave, door) whose declaration replays statements.
REPLAYED = (('BP_OMDOnslaughtCoordinator_Scenario04', 5, 2),
            ('BP_OMDOnslaughtCoordinator_Scenario08', 7, 3),
            ('BP_OMDOnslaughtCoordinator_WC_16', 7, 3))
#: The map Scenario04 belongs to, built in full.
HOST_MAP = 'Scenario_04'
#: Rows the 'fewer' case removes from the front, and the 'more' case adds at the end.
FEWER, MORE = 14, 6
SCRATCH_PARENT = '/mnt/2tb_pro_a/tmp' if os.path.isdir('/mnt/2tb_pro_a/tmp') else None

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))
    print('  %s  %s%s' % ('PASS' if ok else 'FAIL', name, (': ' + str(detail)) if detail else ''))


def calls(load, coord, wave, door):
    """A door's declared calls in order: ('spawn', unit), ('wait', s), ('group', share)."""
    pkg, names, resolve = load(coord)
    _w, mm = wavespec.ubergraph(pkg, names, resolve)
    entry = bppatch.entry_points(pkg, names, resolve)['Onslaught_Start']
    lookup = wavespec.unit_lookup(pkg, names, resolve)
    out = []
    for i in wavespec.declared(mm, entry, names, resolve, wave, door):
        node = mm[i][2]
        for fn, kind in (('OnslaughtStartGroup', 'group'), ('OnslaughtDelay', 'wait')):
            for call in bppatch.calls_named(node, fn, resolve):
                out.append((kind, round(wavespec._literal(call, 'FloatConst') or 0.0, 1)))
        for row in wavespec.door_rows(mm, [i], resolve, lookup):
            out.append(('spawn', row['unit']))
    return out


def pacing(seq):
    """Spawns and waits as they play: no zero groups, waits in a row added up, nothing
    after the last spawn (a host wait there belongs to no row)."""
    out = []
    for kind, v in seq:
        if kind == 'group' and not v:
            continue
        if kind == 'wait' and out and out[-1][0] == 'wait':
            out[-1] = ('wait', round(out[-1][1] + v, 1))
            continue
        out.append((kind, v))
    while out and out[-1][0] != 'spawn':
        out.pop()
    return out


def authored(rows):
    """What a door's rows declare, in pacing() form."""
    def pause(p):
        return [(p.get('kind') or 'wait', round(float(p['value']), 1))] \
            if p and p.get('value') else []
    out = []
    for r in rows:
        out += pause(r.get('before')) + [('spawn', r['unit'])] + pause(r.get('after'))
    return pacing(out)


def rows_of(spec, wave, door):
    """A door's rows as they play: units and counts, then pacing (a pause before a row
    and one after the row ahead of it are the same wait, and read back as the latter)."""
    rows = [r for r in (spec['waves'][wave - 1].get('spawns') or {}).get(str(door), [])
            if r.get('unit')]
    return [(r['unit'], r.get('count')) for r in rows], authored(rows)


def cases(rows):
    yield 'as read', rows
    yield '%d fewer' % FEWER, rows[FEWER:]
    yield '%d more' % MORE, rows + copy.deepcopy(rows[:MORE])


def read_spec(coord):
    """The coordinator's own onslaught as the editor opens it."""
    real = makelevel.coordinator_of
    makelevel.coordinator_of = lambda _m: ('/Game/Data/Onslaughts/' + coord, coord)
    try:
        return wavespec.read_onslaught('replayed-test')
    finally:
        makelevel.coordinator_of = real


def run_coordinator(coord, wave, door):
    base = read_spec(coord)
    host = {(w + 1, int(d)): calls(bppatch.load, coord, w + 1, int(d))
            for w in range(len(base['waves'])) for d in base['waves'][w]['spawns']}
    sh = wavespec.shape(coord)
    protos = onslaught.protos()
    for case, rows in cases(base['waves'][wave - 1]['spawns'][str(door)]):
        label = '%s wave %d door %d, %s' % (coord.split('Coordinator_')[-1], wave, door,
                                             case)
        spec = copy.deepcopy(base)
        spec['waves'][wave - 1]['spawns'][str(door)] = copy.deepcopy(rows)
        try:
            ua, ux = makelevel.build_coordinator(
                coord, '/Game/Data/Onslaughts/' + coord,
                wavespec.author(spec, sh, protos))
        except SystemExit as exc:
            check('%s: authors' % label, False, exc)
            continue

        def load(_n, ua=ua, ux=ux):
            return savetest._load_from(ua, ux)
        got = calls(load, coord, wave, door)
        if case == 'as read':
            check('%s: the host\'s exact calls' % label, got == host[(wave, door)],
                  '%d calls, host %d' % (len(got), len(host[(wave, door)])))
        else:
            want = authored(rows)
            check('%s: its rows\' spawns and waits, in order' % label,
                  pacing(got) == want,
                  '' if pacing(got) == want else next(
                      ('at %d: %s, wanted %s' % (k, a, b) for k, (a, b)
                       in enumerate(zip(pacing(got), want)) if a != b),
                      '%d calls, wanted %d' % (len(pacing(got)), len(want))))
        others = [k for k in host if k != (wave, door) and calls(load, coord, *k) != host[k]]
        check('%s: every other door keeps the host\'s exact calls' % label, not others,
              others[:4])
        with savetest._coordinator_as(coord, ua, ux):
            back = read_spec(coord)
        check('%s: reads back as authored, pauses included' % label,
              rows_of(back, wave, door) == rows_of(spec, wave, door),
              '%d rows read back, %d authored' % (len(rows_of(back, wave, door)[0]),
                                                  len(rows_of(spec, wave, door)[0])))


def run_build(scratch):
    """Scenario_04's cases through the full build: check_built and flowsim."""
    _c, wave, door = REPLAYED[0]
    base = wavespec.read_onslaught(HOST_MAP)
    for case, rows in cases(base['waves'][wave - 1]['spawns'][str(door)]):
        spec = copy.deepcopy(base)
        spec['waves'][wave - 1]['spawns'][str(door)] = copy.deepcopy(rows)
        out = os.path.join(scratch, 'out')
        shutil.rmtree(out, ignore_errors=True)
        try:
            res = wavespec.build(dict(spec, map=HOST_MAP, raw=True, out=out,
                                      title='Replayed Spawns Test'))
        except SystemExit as exc:
            check('%s build, %s: saves' % (HOST_MAP, case), False, exc)
            continue
        bad = [c for c in res['checks'] if not c['ok']]
        check('%s build, %s: saves with every check passing' % (HOST_MAP, case), not bad,
              '; '.join('%s: %s' % (c['name'], c['detail']) for c in bad))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--keep', action='store_true', help='keep the scratch folder')
    args = ap.parse_args(argv)
    scratch = tempfile.mkdtemp(prefix='replayed-spawns-test-', dir=SCRATCH_PARENT)
    try:
        for coord, wave, door in REPLAYED:
            run_coordinator(coord, wave, door)
        run_build(scratch)
    finally:
        if args.keep:
            print('kept %s' % scratch)
        else:
            shutil.rmtree(scratch, ignore_errors=True)
    failed = [c for c in checks if not c[1]]
    print('%d of %d checks passed' % (len(checks) - len(failed), len(checks)))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
