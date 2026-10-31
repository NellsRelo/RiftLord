"""Offline checks on mod minions in missions (modminions): found, built, carried, refused.

    python3 test_modminions.py [--keep]

Needs a mod with minions: the pinned copy of Wraiths_P.pak (tests/fixtures/fixturelibs.PINNED;
rows Wraiths_ZombieOrcLight and Wraiths_ZombieOrcMedium). It is copied into a scratch mods
folder that modminions reads instead of the game's; a scratch Missions bucket builds
Scenario_01 with wave 1 sending 2 Light and 1 Medium through Rift Lord's bake; and
variants of the scratch mod (a package missing, a manifest entry missing, no manifest, a
changed package) are checked against that build. The user's Missions are only read; the
game's ~mods never. Exit 0 when every check passes.
"""

import argparse
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'fixtures'))

from riftlord.missions import modminions  # noqa: E402

MOD_PAK = 'Wraiths_P.pak'
KEY = 'Wraiths'
LIGHT, MEDIUM = 'Wraiths_ZombieOrcLight', 'Wraiths_ZombieOrcMedium'
LEVEL = 'Scenario_01'
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


def spec_for(level):
    from riftlord.onslaught import wavespec
    spec = wavespec.read_onslaught(level)
    row = {'promote': 0.5, 'difficulty': 5, 'addCount': 0, 'before': None, 'after': None}
    spec['waves'][0]['spawns'] = {'1': [dict(row, unit=LIGHT, count=2),
                                        dict(row, unit=MEDIUM, count=1)]}
    return spec


def variant(src_files, dest, drop=(), manifest=None, change=None):
    """A copy of the mod pak at `dest`: entries under `drop` prefixes removed, the
    manifest replaced by `manifest` (False: removed), `change` entry with one byte
    flipped."""
    from riftlord.core import paksource
    files = {}
    inner = 'OMD/Content/Mods/%s/OrcsMustUI/%s' % (KEY, modminions.MANIFEST)
    for k, v in src_files.items():
        if any(k.startswith(d) for d in drop):
            continue
        files[k] = v
    if manifest is False:
        files.pop(inner, None)
    elif manifest is not None:
        files[inner] = manifest.encode('utf-8')
    if change:
        blob = bytearray(files[change])
        blob[-1] ^= 0xFF
        files[change] = bytes(blob)
    paksource.write(dest, files, keep_omui=False)
    return dest


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--keep', action='store_true', help='keep the scratch folder')
    args = ap.parse_args(argv)
    import fixturelibs
    installed = fixturelibs.pinned_mod(MOD_PAK)
    if not installed:
        print('skipped: no pinned %s (tests/fixtures/fixturelibs.py --pin)' % MOD_PAK)
        return 0
    scratch = tempfile.mkdtemp(prefix='modminions-test-', dir=SCRATCH_PARENT)
    try:
        return run(installed, scratch)
    finally:
        if args.keep:
            print('kept %s' % scratch)
        else:
            shutil.rmtree(scratch, ignore_errors=True)


def run(installed, scratch):
    from riftlord.core import paksource
    mods = os.path.join(scratch, 'mods')
    os.makedirs(os.path.join(mods, 'sub'))
    mod_pak = os.path.join(mods, 'sub', MOD_PAK)
    shutil.copyfile(installed, mod_pak)
    shutil.copyfile(installed, os.path.join(mods, 'Other_P.pak.disabled'))
    modminions.ROOTS = [mods]
    mod_files = paksource.entries(mod_pak)

    print('discovery')
    got = modminions.mods()
    check('one mod found, from its own pak in a subfolder (.disabled skipped)',
          list(got) == [KEY] and got[KEY]['own'] and got[KEY]['pak'] == mod_pak,
          ', '.join('%s %s' % (k, m['pak']) for k, m in got.items()))
    rows = modminions.rows()
    check('rows found by the OMDAICharacterProto import, prefixed',
          LIGHT in rows and MEDIUM in rows and all(r.startswith(KEY + '_') for r in rows),
          ', '.join(sorted(rows)))
    from riftlord.onslaught import onslaught
    check('onslaught.protos() and proto_flags() list them',
          LIGHT in onslaught.protos() and onslaught.proto_flags()[LIGHT]['mod'] == KEY)
    from riftlord.onslaught import gamedata
    units = {u['name']: u for u in gamedata.units()}
    family = (rows[LIGHT]['entry'] or {}).get('Family') or got[KEY]['label']
    check('the Onslaught Editor groups them under their Family (else the mod), labelled by '
          'DisplayName',
          all(units[r]['family'] == family for r in rows if r in units)
          and units[LIGHT]['label'] == rows[LIGHT]['name'],
          '%s / %s' % (units[LIGHT]['family'], units[LIGHT]['label']))
    manifest = {r: set(modminions.carried_packages(r)) for r in (LIGHT, MEDIUM)}
    check('each row has a manifest entry', all(manifest.values()),
          ', '.join('%s %d' % (r, len(p)) for r, p in manifest.items()))

    print('a mission built through Rift Lord\'s bake')
    os.environ['OMD3_BUCKET'] = os.path.join(scratch, 'Missions')
    os.makedirs(os.path.join(scratch, 'Missions', '.riftlord', 'records'))
    from riftlord.missions import bucket
    bucket.activate()
    ident = bucket.save_map(LEVEL, {'level': LEVEL}, title='Minion Test')
    ident = ident['mission'] if isinstance(ident, dict) else ident
    rec = bucket.load(ident)
    rec['spec'] = spec_for(LEVEL)
    bucket._write_json(bucket._record_path(ident), rec)
    t = time.time()
    err = refused(lambda: bucket.bake(ident))
    check('builds (%.0f s)' % (time.time() - t), err is None, err)
    if err:
        return 1
    out = os.path.join(bucket.root(), ident)
    files = paksource.entries(out)
    carried = {k for k in files if modminions.folder(k) == KEY}
    want = set()
    for gp in manifest[LIGHT] | manifest[MEDIUM]:
        stem = 'OMD/Content' + gp[len('/Game'):]
        want |= {stem + e for e in modminions.EXTS if stem + e in mod_files}
    check('carries exactly the manifest\'s packages (%d files)' % len(carried),
          carried == want, 'missing %s; extra %s' % (sorted(want - carried)[:2],
                                                      sorted(carried - want)[:2]))
    check('byte for byte the installed pak',
          all(files[k] == mod_files[k] for k in carried & want))
    check('the manifest itself is not carried',
          not any(k.endswith('/' + modminions.MANIFEST) for k in carried))
    check('it spawns the two rows', modminions.used(files) == sorted([LIGHT, MEDIUM]),
          modminions.used(files))
    rows_g = modminions.gates(files)
    check('gates pass (%d)' % len(rows_g), rows_g and all(ok for _n, ok, _d in rows_g),
          '; '.join('%s: %s' % (n, d) for n, ok, d in rows_g if not ok))
    from riftlord.build import makemod
    bad = makemod.registry_problems(files, ident + '.pak', mods=mods)
    check('registry gate: the carried tables match the installed mod byte for byte', not bad,
          '; '.join(bad[:2]))
    check('the mission keeps its own mod key', makemod.pak_mod_key(files) != KEY,
          makemod.pak_mod_key(files))
    again, _rep = modminions.carry(files)
    check('carrying again changes nothing', again == files)

    print('variants of the installed mod')
    table = 'OMD/Content/Mods/%s/Tables/' % KEY
    heavy_only = sorted(p for p in manifest[LIGHT] if '/Heavy/' in p)[0]
    gone = 'OMD/Content' + heavy_only[len('/Game'):]
    variant(mod_files, mod_pak, drop=(gone + '.',))
    err = refused(lambda: modminions.carry(files))
    check('a listed package missing: refused, naming the row and package',
          err and (LIGHT in err or MEDIUM in err) and heavy_only in err, err)

    text = mod_files['OMD/Content/Mods/%s/OrcsMustUI/%s' % (KEY, modminions.MANIFEST)].decode()
    cut = text.index('  - Row: %s' % LIGHT)
    rest = text.find('\n  - Row:', cut + 1)
    variant(mod_files, mod_pak, manifest=text[:cut] + (text[rest + 1:] if rest >= 0 else ''))
    err = refused(lambda: modminions.carry(files))
    check('a row with no manifest entry: refused, naming it', err and LIGHT in err, err)

    variant(mod_files, mod_pak, manifest=False)
    notes = []
    walked, rep = modminions.carry(files, note=notes.append)
    walked_set = {k for k in walked if modminions.folder(k) == KEY}
    check('no manifest: the closure walk carries the same files, behind a warning',
          walked_set == want and any(n.startswith('warning') for n in notes),
          'walk %d, manifest %d; %s' % (len(walked_set), len(want), notes[:1]))

    changed = sorted(k for k in carried if k.endswith('.uexp') and table not in k)[0]
    variant(mod_files, mod_pak, change=changed)
    rows_g = modminions.gates(files)
    stale = [d for n, ok, d in rows_g if not ok]
    check('the mod changed: the built mission fails the byte-for-byte gate',
          any('differ' in d for d in stale), stale[:1])
    fresh, _rep = modminions.carry(files)
    check('a rebuild re-copies the changed file', fresh[changed] != files[changed]
          and fresh[changed] == paksource.read(mod_pak, changed))

    os.remove(mod_pak)
    rows_g = modminions.gates(files)
    check('the mod not installed: the carried copy is kept and the gates pass',
          rows_g and all(ok for _n, ok, _d in rows_g),
          '; '.join('%s: %s' % (n, d) for n, ok, d in rows_g if not ok))
    err = refused(lambda: modminions.carry(files))
    check('the mod not installed: a rebuild is refused, naming the row',
          err and (LIGHT in err or MEDIUM in err), err)

    failed = [c for c in checks if not c[1]]
    print('%d of %d checks passed' % (len(checks) - len(failed), len(checks)))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
