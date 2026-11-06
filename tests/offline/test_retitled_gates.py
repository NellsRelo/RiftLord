"""The probe gates on a build whose level has a Working Title.

Install and Package rename a mission's level from `<mod>_Map01` to its Working Title
(leveltitle.retitle), and a new mission's is `RL_Scen_<Name>`, so a gate that reads a packed
build finds the level by its shape (leveltitle.level_entry, level_files, is_level), never by
`_Map01`. The test missions' pinned builds (Fixture Edit; Fixture Scratch, taken out of its
pinned campaign pak) are retitled to `RL_Scen_...`, and each gate the probe runs on a built
pak gives the same rows on both: kit actors, Crystal, Chandelier, controllers, foliage, Fab,
the rift floor and spawner heights, vertex fans, the walkable graph read (navstrict),
spawner facings and every BuiltData reading back (its textures' absolute offsets).

    python3 tests/offline/test_retitled_gates.py

Scratch folders go in the temporary folder; the Fab library and Paks folder are the test
missions' own (buildcompare.fixture_env), unless the gate gives them.
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))

RECORDS = os.path.join(ROOT, 'tests', 'fixtures', 'missions', '.riftlord', 'records')
CAMPAIGN = '900_FixtureCampaign_Campaign_P'
checks = []


def check(name, ok, detail=''):
    checks.append(bool(ok))
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, (': %s' % (detail,)) if detail else ''))


def builds():
    """{record id: {inner path: bytes}} of the pinned test missions."""
    import buildcompare
    from riftlord.core import paksource
    out = {}
    edit = buildcompare.baseline_pak('900_Fixture_Edit_P')
    if edit:
        out['900_Fixture_Edit_P'] = paksource.entries(edit)
    base = buildcompare.BASELINES.get('900_Fixture_Scratch_P') or {}
    camp = os.path.join(buildcompare.PINNED, '900_Fixture_Scratch_P', base.get('digest') or 'none',
                        CAMPAIGN + '.pak')
    if os.path.isfile(camp):
        out['900_Fixture_Scratch_P'] = {k: v for k, v in paksource.entries(camp).items()
                                        if '/Mods/FixtureCampaign_Campaign/' not in k}
    check('the pinned builds of both test missions (buildcompare --pin)', len(out) == 2, sorted(out))
    return out


def rows(got, names):
    """Gate rows as (name, ok), with the level's names made one."""
    out = []
    for row in got or []:
        name, ok = (row[0], row[1]) if isinstance(row, (tuple, list)) else (row.get('name'), row.get('ok'))
        for a in names:
            name = str(name).replace(a, '<level>')
        out.append((name, bool(ok)))
    return out


def run_gates(files, src, rec):
    """{gate: its rows or result} on one build (`files`, and `src` the same as a pak)."""
    from riftlord.build.navigation import navfan, navstrict, riftfloor
    from riftlord.mapeditor import (chandelier, controllers, crystal, fabemit, foliageadd,
                                    kitactors, spawnerfacing)
    plan, level = rec.get('plan') or {}, rec.get('level')
    out = {
        'kit actors': kitactors.gates(files, plan),
        'kit actors: the level': kitactors._level_of(files)[0] is not None,
        'Crystal': crystal.gates(files, plan),
        'Chandelier': chandelier.gates(files, plan),
        'controllers': controllers.gates(files, plan),
        'rift floor': riftfloor.gates(files, plan, level),
        'rift floor: the level read': bool(riftfloor._level_files(src)),
        'spawner heights': riftfloor.spawner_warnings(src, plan),
        'vertex fans (pak)': navfan.build_problems(src),
        'vertex fans (files)': navfan.files_problems(files),
        'walkable graph read (navstrict)': len(list(navstrict.pak_buffers(src))),
        'spawner facings: the level read': spawnerfacing._umap(src) is not None,
    }
    from riftlord.build.lighting import lightmaps
    built = sorted(n[:-len('.uasset')] for n in files if n.endswith('_BuiltData.uasset'))
    read = 0
    for stem in built:
        try:
            lightmaps.BuiltData(files[stem + '.uasset'], files[stem + '.uexp'],
                                files.get(stem + '.ubulk', b''))
            read += 1
        except (Exception, SystemExit):              # noqa: BLE001
            pass
    out['baked lighting reads back (%d BuiltData)' % len(built)] = read == len(built)
    if foliageadd.items(plan):
        out['foliage'] = foliageadd.gates(files, plan)
    if fabemit.uses_fab(plan) or any(n.startswith('OMD/Content/Mods/Fab/') for n in files):
        out['Fab'] = fabemit.gates(files, plan)
    return out


def main():
    import buildcompare
    #: the test missions' Fab library and Paks folder (fixturelibs), as their builds use
    for key, value in buildcompare.fixture_env().items():
        os.environ.setdefault(key, value)
    from riftlord.build import leveltitle
    from riftlord.core import paksource
    tmp = tempfile.mkdtemp(prefix='rl-retitled-gates-')
    try:
        for ident, files in builds().items():
            with open(os.path.join(RECORDS, ident + '.json')) as fh:
                rec = json.load(fh)
            mod = rec['mod']
            title = 'RL_Scen_' + mod[len('RiftLord'):]
            renamed, _report = leveltitle.retitle(dict(files), mod, title)
            check('%s: retitled to %s' % (ident, title),
                  leveltitle.level_entry(renamed, mod).endswith('/%s.umap' % title) and
                  not any('_Map01' in k for k in renamed if '/Mods/%s/' % mod in k))
            got = {}
            for side, fs in (('Map01', files), ('retitled', renamed)):
                src = os.path.join(tmp, '%s-%s.pak' % (ident, side))
                paksource.write(src, fs)
                got[side] = run_gates(fs, src, rec)
            names = ['%s_Map01' % mod, title]
            for gate in got['Map01']:
                a, b = got['Map01'][gate], got['retitled'][gate]
                if isinstance(a, (list, tuple)) and a and isinstance(a[0], (tuple, list, dict)):
                    a, b = rows(a, names), rows(b, names)
                found = bool(a) if isinstance(a, bool) else True
                check('%s: %s gives the same on the retitled build' % (ident, gate),
                      a == b and found, (a, b) if a != b else '')
            check('%s: the level is found on the retitled build by every reader' % ident,
                  all(got['retitled'][g] for g in got['retitled'] if g.endswith('the level')
                      or g.endswith('level read')) and
                  got['retitled']['walkable graph read (navstrict)'] == 1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('%d of %d checks passed' % (sum(checks), len(checks)))
    sys.exit(0 if all(checks) else 1)


if __name__ == '__main__':
    main()
