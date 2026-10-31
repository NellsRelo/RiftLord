"""Offline checks on `probe --from-record`: a record copied read only, built with given waves.

    python3 test_probe_from_record.py [--keep]

A fixture Missions folder holds probe_fixture_record.json (Scenario_01, no edits). The
probe copies it under its own title, mod key, mission Guid and pak name, replaces the
onslaught with a one-wave spec (one row under an old name with its Guid), builds into a
scratch folder with every gate, and prints one JSON line. The fixture folder must be
byte for byte as it was, and nothing is installed. With --mods-dir, a staged copy of
the pinned Wraiths_P.pak (tests/fixtures/fixturelibs.PINNED; one carried texture byte
changed) is what is found, carried and checked (skipped when it is not pinned). With
--install into a stand-in Paks folder (RIFTLORD_SUPPORTED_PAKS) the pak lands in its ~mods
and the checklist is printed, written beside the pak and given in the JSON. Refusals:
--install with --no-install, a bad key, a key already used, a missing record. Exit 0 when
every check passes.
"""

import argparse
import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from riftlord.core import gamepath  # noqa: E402
from riftlord.mapeditor import probe  # noqa: E402
from riftlord.onslaught import onslaught  # noqa: E402
from riftlord.onslaught import wavespec  # noqa: E402

FIXTURE = os.path.join(HERE, 'probe_fixture_record.json')
SOURCE = '900_Fixture_Probe_P'
KEY = 'FixtureProbeKey'
TITLE = 'Fixture Probe Copy'
#: The installed minion mod a staged copy is made of, and the row the staged probe spawns.
STAGED_PAK = 'Wraiths_P.pak'
STAGED_ROW = 'Wraiths_ZombieOrcLight'
SCRATCH_PARENT = '/mnt/2tb_pro_a/tmp' if os.path.isdir('/mnt/2tb_pro_a/tmp') else None

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))
    print('  %s  %s%s' % ('PASS' if ok else 'FAIL', name, (': ' + str(detail)) if detail else ''))


def tree_digest(root):
    """{relative path: sha256} of every file under `root`."""
    out = {}
    for base, _dirs, names in os.walk(root):
        for n in names:
            p = os.path.join(base, n)
            with open(p, 'rb') as fh:
                out[os.path.relpath(p, root)] = hashlib.sha256(fh.read()).hexdigest()
    return out


def install_section(scratch, missions, base):
    """--install into a stand-in Paks folder (RIFTLORD_SUPPORTED_PAKS): the pak lands in its
    ~mods, and the checklist is printed, written beside the built pak and in the JSON."""
    print('an installed probe (into a stand-in Paks folder)')
    paks = os.path.join(scratch, 'Paks')
    os.makedirs(os.path.join(paks, '~mods'))
    old = os.environ.get('RIFTLORD_SUPPORTED_PAKS')
    os.environ['RIFTLORD_SUPPORTED_PAKS'] = paks
    try:
        code, text = run_probe(base + ['--key', KEY, '--install', '--check', 'two Light Orcs in wave 1',
                                       '--outcome', 'three orcs => the renamed row resolved',
                                       '--json'], missions)
    finally:
        if old is None:
            os.environ.pop('RIFTLORD_SUPPORTED_PAKS', None)
        else:
            os.environ['RIFTLORD_SUPPORTED_PAKS'] = old
    try:
        got = json.loads(text.strip().splitlines()[-1])
    except (IndexError, ValueError):
        got = {}
    installed = os.path.join(paks, '~mods', '900_%s_P.pak' % KEY)
    check('it builds and installs into the stand-in ~mods', code == 0 and got.get('ok') and
          os.path.isfile(installed) and probe._sha256(installed) == got.get('sha256'),
          'exit %s' % code)
    listed = got.get('checklist') or {}
    lines = listed.get('lines') or []
    check('the checklist names the pak, the game, the mission, the waves, the check and the '
          'outcome', any(installed in ln for ln in lines) and
          any(ln.startswith('  game ') for ln in lines) and
          any('Custom Maps > %s' % TITLE in ln for ln in lines) and
          any('--waves' in ln for ln in lines) and
          any('two Light Orcs in wave 1' in ln for ln in lines) and
          any('three orcs => the renamed row resolved' in ln for ln in lines), lines)
    check('the checklist is printed and written beside the built pak',
          'CHECKLIST  %s' % TITLE in text and os.path.isfile(listed.get('path') or '') and
          os.path.dirname(listed.get('path') or '') == os.path.dirname(got.get('pak') or ''),
          listed.get('path'))
    check('nothing went into the game\'s own ~mods', not os.path.exists(os.path.join(
        gamepath.paks() or '', '~mods', '900_%s_P.pak' % KEY)))


def run_probe(argv, missions):
    """(exit code, stdout) of probe.main(argv) against the Missions folder `missions`."""
    old = os.environ.get('OMD3_BUCKET')
    os.environ['OMD3_BUCKET'] = missions
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            try:
                code = probe.main(argv)
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
                print(exc.code if not isinstance(exc.code, int) else '')
    finally:
        if old is None:
            os.environ.pop('OMD3_BUCKET', None)
        else:
            os.environ['OMD3_BUCKET'] = old
    return code, buf.getvalue()


def staged_section(scratch, missions, base, before):
    """--mods-dir: a staged copy of an installed minion mod, one carried texture byte
    changed, is what the probe finds, carries and checks."""
    from riftlord.core import paksource
    from riftlord.core import supportedmods
    from riftlord.missions import modminions
    print('a staged mod (--mods-dir)')
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'fixtures'))
    import fixturelibs
    installed = fixturelibs.pinned_mod(STAGED_PAK)
    if not installed:
        print('  skipped: no pinned %s (tests/fixtures/fixturelibs.py --pin)' % STAGED_PAK)
        return
    pinned = os.path.join(scratch, 'pinned-mods')     # the pinned pak alone, for the scan
    os.makedirs(pinned)
    os.symlink(installed, os.path.join(pinned, STAGED_PAK))
    modminions.ROOTS = [pinned]
    modminions._SCAN.clear()
    try:
        rows = modminions.rows()
        if STAGED_ROW not in rows:
            print('  skipped: %s is not a row of the pinned %s' % (STAGED_ROW, STAGED_PAK))
            return
        carried = {'OMD/Content' + p[len('/Game'):]
                   for p in modminions.carried_packages(STAGED_ROW)}
    finally:
        modminions.ROOTS = None
        modminions._SCAN.clear()
    files = paksource.entries(installed)
    bulk = sorted(n for n in files if n.endswith('.ubulk') and n[:-len('.ubulk')] in carried)
    if not bulk:
        print('  skipped: no carried .ubulk to change')
        return
    changed = bulk[0]
    blob = bytearray(files[changed])
    blob[len(blob) // 2] ^= 0xFF
    files[changed] = bytes(blob)
    staged = os.path.join(scratch, 'staged')
    os.makedirs(staged)
    paksource.write(os.path.join(staged, STAGED_PAK), files, keep_omui=False)
    spec = {'waves': [{'breakKind': 'wave', 'breakSeconds': 10, 'coin': 1500, 'actions': [],
                       'spawns': {'1': [{'unit': STAGED_ROW, 'count': 1, 'promote': 0.5,
                                         'difficulty': wavespec.ALL, 'addCount': 0,
                                         'before': None, 'after': None}]}}]}
    spec_path = os.path.join(scratch, 'staged-waves.json')
    with open(spec_path, 'w', encoding='utf-8') as fh:
        json.dump(spec, fh)
    out = os.path.join(scratch, 'out-staged')
    argv = [a if a != base[base.index('--waves') + 1] else spec_path for a in base]
    argv[argv.index('--out') + 1] = out
    code, text = run_probe(argv + ['--key', KEY + 'Staged', '--mods-dir', staged, '--json'],
                           missions)
    try:
        got = json.loads(text.strip().splitlines()[-1])
    except (IndexError, ValueError):
        got = {}
    bad = [g for g in got.get('gates') or [] if not g['ok']]
    check('it builds with the staged mod, every gate passing', code == 0 and got.get('ok'),
          'exit %s; %s' % (code, '; '.join('%s: %s' % (g['name'], g['detail']) for g in bad)))
    src = {(s['kind'], s['name']): s for s in got.get('modSources') or []}
    mine = src.get(('minions', modminions.pak_key(STAGED_PAK)))
    check('the minion mod is read from the staged pak, and says so',
          mine and mine['where'] == 'staged' and mine['pak'] == os.path.join(staged, STAGED_PAK)
          and 'staged' in text, mine)
    omui = src.get(('supported', 'OrcsMustUI'))
    check('Supported Mods not staged are read from the installed folders',
          omui is None or omui['where'] == 'installed', omui)
    pak = got.get('pak')
    if pak and os.path.exists(pak):
        built = paksource.read(pak, changed)
        check('the carried copy is the staged bytes, not the installed ones',
              built == files[changed] and built != paksource.read(installed, changed), changed)
    check('modminions and Supported Mods are back on the installed folders after the run',
          modminions.ROOTS is None and supportedmods.STAGED_DIR is None)
    check('the fixture Missions folder is as it was', tree_digest(missions) == before)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--keep', action='store_true', help='keep the scratch folder')
    args = ap.parse_args(argv)
    scratch = tempfile.mkdtemp(prefix='probe-from-record-test-', dir=SCRATCH_PARENT)
    try:
        return run(scratch)
    finally:
        if args.keep:
            print('kept %s' % scratch)
        else:
            shutil.rmtree(scratch, ignore_errors=True)


def run(scratch):
    missions = os.path.join(scratch, 'Missions')
    records = os.path.join(missions, '.riftlord', 'records')
    os.makedirs(records)
    shutil.copyfile(FIXTURE, os.path.join(records, SOURCE + '.json'))
    before = tree_digest(missions)
    guid = wavespec.guid_hex(onslaught.protos()['OrcLight'])
    row = {'promote': 0.5, 'difficulty': wavespec.ALL, 'addCount': 0, 'before': None,
           'after': None}
    spec = {'waves': [{'breakKind': 'wave', 'breakSeconds': 10, 'coin': 1500, 'actions': [],
                       'spawns': {'1': [dict(row, unit='OrcLight', count=2),
                                        dict(row, unit='OldMod_OrcLight', guid=guid,
                                             count=1)]}}]}
    spec_path = os.path.join(scratch, 'waves.json')
    with open(spec_path, 'w', encoding='utf-8') as fh:
        json.dump(spec, fh)
    out = os.path.join(scratch, 'out')
    base = ['--from-record', SOURCE, '--waves', spec_path, '--title', TITLE, '--out', out]

    print('a probe from the fixture record')
    code, text = run_probe(base + ['--key', KEY, '--no-install', '--json'], missions)
    lines = text.strip().splitlines()
    try:
        got = json.loads(lines[-1])
    except (IndexError, ValueError):
        got = {}
    bad = [g for g in got.get('gates') or [] if not g['ok']]
    check('it builds, every gate passing (%d)' % len(got.get('gates') or []),
          code == 0 and got.get('ok') and not bad,
          'exit %s; %s' % (code, '; '.join('%s: %s' % (g['name'], g['detail']) for g in bad)
                           or (lines[-3:] if not got else '')))
    pak = os.path.join(out, '900_%s_P.pak' % KEY)
    check('the pak is 900_<key>_P.pak in --out', got.get('pak') == pak and os.path.exists(pak),
          got.get('pak'))
    check('the JSON line gives its sha256',
          os.path.exists(pak) and got.get('sha256') == probe._sha256(pak), got.get('sha256'))
    names = [g['name'] for g in got.get('gates') or []]
    for want in ('texture mip sizes', 'under one FName spelling',
                 'every import names an export'):
        check('gate run: %s' % want, any(want in n for n in names))
    check('its own mod key and mission Guid',
          got.get('mod') == KEY
          and '%s_Mission01' % KEY in (got.get('guids') or {})
          and '0123456789abcdef0123456789abcdef' not in (got.get('guids') or {}).values(),
          got.get('guids'))
    if os.path.exists(pak):
        from riftlord.core import paksource
        files = paksource.names(pak)
        check('its tables are in Mods/<key>/Tables',
              'OMD/Content/Mods/%s/Tables/%s_MissionProtos.uasset' % (KEY, KEY) in files)
        check('nothing of the source mod is in it',
              not any('/Mods/RiftLordFixtureProbe/' in n for n in files))
    check('the renamed row was resolved by its Guid', 'OldMod_OrcLight is now OrcLight' in text)
    check('the fixture Missions folder is byte for byte as it was',
          tree_digest(missions) == before,
          sorted(set(tree_digest(missions).items()) ^ set(before.items()))[:3])
    mods = gamepath.mods_dir()
    check('nothing installed', not mods or not os.path.exists(
        os.path.join(mods, '900_%s_P.pak' % KEY)))
    lines = run_probe(base + ['--key', KEY], missions)[1].strip().splitlines()
    check('without --json the last line is PASS|FAIL pak PATH sha256 HEX',
          lines and lines[-1].split()[:3] == ['PASS', 'pak', pak]
          and lines[-1].split()[3:4] == ['sha256'], lines[-1:])

    staged_section(scratch, missions, base, before)

    install_section(scratch, missions, base)

    print('refusals')
    code, _t = run_probe(base + ['--key', KEY, '--install', '--no-install'], missions)
    check('--install with --no-install is refused', code != 0, code)
    code, text = run_probe(base + ['--key', 'Bad_Key'], missions)
    check('a key that is not letters and digits is refused, naming it',
          code != 0 and 'Bad_Key' in text, text.strip().splitlines()[-1:])
    code, text = run_probe(base + ['--key', 'RiftLordFixtureProbe'], missions)
    check('a key the source already uses is refused', code != 0 and 'already used' in text,
          text.strip().splitlines()[-1:])
    code, text = run_probe(['--from-record', '900_No_Such_P', '--waves', spec_path, '--title',
                            TITLE, '--key', KEY, '--out', out], missions)
    check('a missing record is refused, naming it', code != 0 and '900_No_Such_P' in text,
          text.strip().splitlines()[-1:])
    check('the fixture Missions folder is still as it was', tree_digest(missions) == before)

    failed = [c for c in checks if not c[1]]
    print('%d of %d checks passed' % (len(checks) - len(failed), len(checks)))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
