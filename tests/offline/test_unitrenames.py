"""Offline checks on wave specs whose unit rows were renamed (wavespec.resolve_units).

    python3 test_unitrenames.py [--keep]

A spec row names its unit by row name and, once saved, by Guid. A row whose name no
longer exists but whose Guid does builds as that row, with the rename noted once; an
unknown name with no matching Guid is refused by name; saving stamps every row's Guid
and its current name. The renamed row is a shipped one (OrcLight under an old name), so
no mod needs to be installed. Builds Scenario_01 raw into a scratch folder. Exit 0 when
every check passes.
"""

import argparse
import copy
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from riftlord.onslaught import onslaught  # noqa: E402
from riftlord.onslaught import wavespec  # noqa: E402

LEVEL = 'Scenario_01'
UNIT = 'OrcLight'
OLD = 'OldMod_OrcLight'
UNKNOWN = 'NoSuchMod_Nobody'
SCRATCH_PARENT = '/mnt/2tb_pro_a/tmp' if os.path.isdir('/mnt/2tb_pro_a/tmp') else None

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))
    print('  %s  %s%s' % ('PASS' if ok else 'FAIL', name, (': ' + str(detail)) if detail else ''))


def refused(fn):
    """The SystemExit text `fn` raises, or None."""
    try:
        fn()
    except SystemExit as exc:
        return str(exc)
    return None


def spec_with(rows):
    """Scenario_01's own onslaught with wave 1 on spawner 1 replaced by `rows`."""
    spec = wavespec.read_onslaught(LEVEL)
    base = {'promote': 0.5, 'difficulty': wavespec.ALL, 'addCount': 0, 'before': None,
            'after': None, 'count': 1}
    spec['waves'][0]['spawns'] = {'1': [dict(base, **r) for r in rows]}
    return spec


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--keep', action='store_true', help='keep the scratch folder')
    args = ap.parse_args(argv)
    scratch = tempfile.mkdtemp(prefix='unitrenames-test-', dir=SCRATCH_PARENT)
    try:
        return run(scratch)
    finally:
        if args.keep:
            print('kept %s' % scratch)
        else:
            shutil.rmtree(scratch, ignore_errors=True)


def run(scratch):
    protos = onslaught.protos()
    guid = wavespec.guid_hex(protos[UNIT])
    other = next(n for n in sorted(protos) if n != UNIT)

    print('resolving')
    spec = {'waves': [{'spawns': {'1': [{'unit': OLD, 'guid': guid, 'count': 2},
                                        {'unit': OLD, 'guid': guid.upper(), 'count': 1},
                                        {'unit': other, 'count': 1}]}}],
            'events': [{'kind': 'boss', 'wave': 1, 'unit': OLD, 'guid': guid}]}
    before = copy.deepcopy(spec)
    notes = []
    got = wavespec.resolve_units(spec, protos, notes.append)
    rows = got['waves'][0]['spawns']['1']
    check('a renamed row with a matching Guid takes the current name',
          [r['unit'] for r in rows] == [UNIT, UNIT, other] and got['events'][0]['unit'] == UNIT,
          [r['unit'] for r in rows])
    check('the rename is noted once', notes == ['%s is now %s' % (OLD, UNIT)], notes)
    check('the spec passed in is left as it was', spec == before)
    check('without stamp, no Guid is added', 'guid' not in rows[2])
    stamped = wavespec.resolve_units(spec, protos, stamp=True)
    srows = stamped['waves'][0]['spawns']['1']
    check('a save stamps every row with its current name and Guid',
          all(r.get('guid') == wavespec.guid_hex(protos[r['unit']]) for r in srows)
          and srows[0]['unit'] == UNIT
          and stamped['events'][0].get('guid') == guid, srows)
    plain = {'waves': [{'spawns': {'1': [{'unit': UNIT, 'count': 1}]}}]}
    check('an old spec without Guids resolves by name, unchanged',
          wavespec.resolve_units(plain, protos) == plain)
    nomatch = {'waves': [{'spawns': {'1': [{'unit': UNKNOWN, 'guid': '0' * 32}]}}]}
    check('an unknown name whose Guid matches nothing is left for validation to refuse',
          wavespec.resolve_units(nomatch, protos) == nomatch)
    from riftlord.missions import hostevents
    kept = hostevents.clean([{'kind': 'boss', 'wave': 1, 'unit': UNIT, 'guid': guid}])
    check('a boss event keeps its Guid in the plan', kept and kept[0].get('guid') == guid, kept)

    print('building %s' % LEVEL)
    out = os.path.join(scratch, 'out')
    notes = []
    renamed = spec_with([{'unit': OLD, 'guid': guid, 'count': 2},
                         {'unit': OLD, 'guid': guid, 'count': 1}])
    res = {}
    err = refused(lambda: res.update(wavespec.build(dict(renamed, map=LEVEL, raw=True, out=out,
                                                         title='Rename Test'), notes.append)))
    check('a spec naming a renamed row with a matching Guid builds', err is None, err)
    sent = next((c for c in res.get('checks') or [] if c.get('name') == 'wave 1 units'), {})
    check('its wave 1 sends the current row', sent.get('ok') and UNIT in sent.get('detail', ''),
          sent.get('detail'))
    check('the build notes the rename once',
          [n for n in notes if n.endswith(' is now %s' % UNIT)] == ['%s is now %s' % (OLD, UNIT)],
          [n for n in notes if ' is now ' in n])
    shutil.rmtree(out, ignore_errors=True)
    unknown = spec_with([{'unit': UNKNOWN, 'guid': '0' * 32}])
    err = refused(lambda: wavespec.build(dict(unknown, map=LEVEL, raw=True, out=out,
                                              title='Rename Test')))
    check('an unknown name with no Guid match is refused, naming it',
          err is not None and UNKNOWN in err, err)
    unknown = spec_with([{'unit': UNKNOWN}])
    err = refused(lambda: wavespec.build(dict(unknown, map=LEVEL, raw=True, out=out,
                                              title='Rename Test')))
    check('an unknown name with no Guid is refused, naming it',
          err is not None and UNKNOWN in err, err)

    failed = [c for c in checks if not c[1]]
    print('%d of %d checks passed' % (len(checks) - len(failed), len(checks)))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
