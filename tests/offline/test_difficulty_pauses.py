"""Offline checks on the per-difficulty pauses some coordinators wait on.

    python3 test_difficulty_pauses.py

Seven coordinators (Scenario11, Scenario13, Ice_01, Sand_01, Sand_03..05) wait with
`OnslaughtDelay(Delay)`, `Delay` set in each case of a Switch on GetGameDifficulty and, in
the Endless case, again on the Endless loop. ScenarioDLC_02 selects on the loop alone.
read_onslaught reads each as a pause with a value per difficulty, and the writer puts the
values back into those statements. Checks:
  - Sand_01 reads 12 such pauses, 32 / 30 / 28 s, Endless 16 s then 0 from loop 2;
    Sand_04's Endless value changes from loop 3; ScenarioDLC_02 reads 3
  - authored as read, Sand_01 is byte-identical to the shipped coordinator
  - an edited pause is written into the host's statements (no new wait) and reads back
    with its new values; every other pause and door reads back as the host's
  - a pause whose row moved becomes a flat wait, and its statements wait no more
  - an appended wave copied from one holding the Switch keeps every SwitchValue's case
    chain inside its own statement
Exit 0 when every check passes.
"""

import copy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from riftlord.build import makelevel  # noqa: E402
from riftlord.formats.ue.kismet import code_offsets, node_sizes  # noqa: E402
from riftlord.build.navigation.navpatch import read_from_pak  # noqa: E402
from riftlord.onslaught import bppatch  # noqa: E402
from riftlord.onslaught import onslaught  # noqa: E402
from riftlord.onslaught import savetest  # noqa: E402
from riftlord.onslaught import wavespec  # noqa: E402

HOST = 'Scenario_Sand_01'
#: Sand_01's pause, as every one of its 12 reads.
SAND_01 = {'apprentice': 32.0, 'warMage': 30.0, 'riftLord': 28.0, 'endless': 16.0,
           'endlessLater': 0.0}
#: The values the edit case writes.
EDITED = {'apprentice': 12.0, 'warMage': 11.0, 'riftLord': 10.0, 'endless': 9.0,
          'endlessLater': 2.5}

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))
    print('  %s  %s%s' % ('PASS' if ok else 'FAIL', name, (': ' + str(detail)) if detail else ''))


def pauses(spec):
    """[(wave, door, row, side, pause)] of every per-difficulty pause in a spec."""
    out = []
    for w, wave in enumerate(spec['waves']):
        for door, rows in sorted(wave['spawns'].items()):
            for k, r in enumerate(rows):
                for side in ('before', 'after'):
                    p = r.get(side)
                    if p and p.get('byDifficulty'):
                        out.append((w + 1, door, k, side, p))
    return out


def shipped(map_name):
    """(uasset, uexp) of a map's coordinator as the game ships it."""
    path, coord = makelevel.coordinator_of(map_name)
    idx = bppatch.live_index()
    key = bppatch.coordinators()[coord]
    return path, coord, tuple(read_from_pak(*idx[key + ext]) for ext in ('.uasset', '.uexp'))


def author(map_name, spec):
    path, coord = makelevel.coordinator_of(map_name)
    return makelevel.build_coordinator(coord, path, wavespec.author(
        spec, wavespec.shape(coord), onslaught.protos()))


def read_built(map_name, ua, ux):
    _p, coord = makelevel.coordinator_of(map_name)
    with savetest._coordinator_as(coord, ua, ux):
        return wavespec.read_onslaught(map_name)


def delay_calls(ua, ux):
    """How many OnslaughtDelay calls a built coordinator holds."""
    pkg, names, resolve = savetest._load_from(ua, ux)
    _w, mm = wavespec.ubergraph(pkg, names, resolve)
    return sum(len(bppatch.calls_named(n, 'OnslaughtDelay', resolve)) for _m, _s, n in mm)


def stray_cases(ua, ux):
    """SwitchValue case offsets that leave their own statement."""
    pkg, names, resolve = savetest._load_from(ua, ux)
    _w, mm = wavespec.ubergraph(pkg, names, resolve)
    bad = []
    for mem, _s, top in mm:
        end = mem + node_sizes(top)[1]
        for node, j in code_offsets([top]):
            if node.name == 'SwitchValue' and not mem < node.ops[j][1] <= end:
                bad.append(mem)
    return bad


def main():
    spec = wavespec.read_onslaught(HOST)
    got = pauses(spec)
    check('Sand_01 reads 12 per-difficulty pauses', len(got) == 12, len(got))
    check('each is 32 / 30 / 28 s, Endless 16 then 0 from loop 2',
          all(p['byDifficulty'] == SAND_01 and p.get('endlessLaterFrom') == 2
              for *_r, p in got), [p['byDifficulty'] for *_r, p in got][:1])
    sand4 = pauses(wavespec.read_onslaught('Scenario_Sand_04'))
    check('Sand_04 reads 7, Endless changing from loop 3',
          len(sand4) == 7 and all(p.get('endlessLaterFrom') == 3 for *_r, p in sand4),
          len(sand4))
    dlc = pauses(wavespec.read_onslaught('ScenarioDLC_02'))
    check('ScenarioDLC_02 reads 3, one value then another from Endless loop 2',
          len(dlc) == 3 and all(set(p['byDifficulty']) == {'all', 'endlessLater'}
                                for *_r, p in dlc),
          [p['byDifficulty'] for *_r, p in dlc])

    _path, _coord, host = shipped(HOST)
    ua, ux = author(HOST, spec)
    check('authored as read, Sand_01 is the shipped coordinator byte for byte',
          (ua, ux) == host, '%d / %d bytes, shipped %d / %d'
          % (len(ua), len(ux), len(host[0]), len(host[1])))

    # the edit: the first pause's values, and its door's first row one more unit
    edited = copy.deepcopy(spec)
    w, door, k, side, p = pauses(edited)[0]
    p['byDifficulty'] = dict(EDITED)
    edited['waves'][w - 1]['spawns'][door][0]['count'] += 1
    ua, ux = author(HOST, edited)
    back = read_built(HOST, ua, ux)
    after = pauses(back)
    mine = [q for (w2, d2, k2, s2, q) in after if (w2, d2, k2, s2) == (w, door, k, side)]
    check('the edited pause reads back with its new values',
          mine and mine[0]['byDifficulty'] == EDITED, mine[:1])
    check('the other 11 read back as the host\'s',
          sorted(str(q['byDifficulty']) for *_r, q in after if q is not (mine or [None])[0])
          == [str(SAND_01)] * 11)
    check('no wait was added: the values went into the host\'s statements',
          delay_calls(ua, ux) == delay_calls(*host),
          '%d delay calls, host %d' % (delay_calls(ua, ux), delay_calls(*host)))
    issues = savetest.compare(wavespec.settle_pauses(edited), back)
    check('the edit reads back as authored', not issues, issues[:3])

    # a moved pause: the row in front of it removed
    moved = copy.deepcopy(spec)
    rows = moved['waves'][w - 1]['spawns'][door]
    del rows[0]
    ua, ux = author(HOST, moved)
    back = read_built(HOST, ua, ux)
    check('a moved pause leaves 11 per-difficulty pauses', len(pauses(back)) == 11,
          len(pauses(back)))
    issues = savetest.compare(wavespec.settle_pauses(moved), back)
    check('and reads back as a flat wait where it went', not issues, issues[:3])

    # an appended wave copies the last; every case chain stays in its own statement
    more = copy.deepcopy(spec)
    more['waves'].append(copy.deepcopy(more['waves'][-1]))
    ua, ux = author(HOST, more)
    check('an appended wave keeps every SwitchValue case chain in its statement',
          not stray_cases(ua, ux), stray_cases(ua, ux)[:4])

    failed = [c for c in checks if not c[1]]
    print('%d of %d checks passed' % (len(checks) - len(failed), len(checks)))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
