"""Missions that share a title, and Campaign Endless.

Two missions saved under one title on a scratch Missions folder: each gets an id, mod, Guids
and Working Title of its own (bucket._fresh_identity, leveltitle.fresh: a letter suffix,
never `_<digits>`), whatever the case of the title. The second is placed in Endless
(bucket.update_settings endless_only): out of any campaign, out of date, its own row listed
by its ENDLESS_EXTENSION row with no twin and no DefaultCharacters (makemod.build
endless_only). Both are built (an empty plan on North Wing) and packaged: two paks, two
levels by their Working Titles, no pak entry in both. The first's Endless twin is then
turned off in place (makemod.set_endless). What OrcsMustUI shows is read from the paks the
way its hub reads the tables (Blueprint/HubBar.py FindCustoms, ExtendEndless; ModActor
ListedMissions): the first under Custom Maps, the second in Endless, both by one title.

Also: leveltitle.letter, scenario_name and fresh; the Onslaught Editor's fresh names;
buildjob.title_free taking a title Missions has; a mission with no Working Title stored
keeping `<mod>_Map01`; a record not placed in Endless digesting as before the field.

    python3 tests/offline/test_samename.py

Scratch folders go in the temporary folder; the user's Missions and ~mods are never read.
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

FIXTURES = os.path.join(ROOT, 'tests', 'fixtures', 'missions')
LEVEL = 'Scenario_01'
TITLE = 'Twin Test'
checks = []


def check(name, ok, detail=''):
    checks.append(bool(ok))
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, (': %s' % (detail,)) if detail else ''))


def names():
    from riftlord.build import leveltitle as L
    check('letter: none for the first, then B..Z, BA',
          [L.letter(n) for n in (1, 2, 3, 26, 27)] == ['', 'B', 'C', 'Z', 'BA'])
    for title, want in (('North Wings', 'NorthWings'), ('Split Stairs, Redux!', 'SplitStairsRedux'),
                        ('1984', 'Mission1984'), ('!!', 'Mission'), ('Café 2', 'Caf2')):
        check('scenario_name(%r) is %s' % (title, want), L.scenario_name(title) == want,
              L.scenario_name(title))
    taken = {'rl_scen_northwings': 'North Wings'}
    got = L.fresh('North Wings', taken=taken, shipped={})
    check('a taken RL_Scen_ name takes a letter suffix', got == 'RL_Scen_NorthWingsB', got)
    check('whatever the case', L.fresh('north wings', taken=taken, shipped={}) == 'RL_Scen_northwingsB')
    long = L.fresh('x' * 80, taken={('RL_Scen_' + 'x' * 56).lower(): 1}, shipped={})
    check('a long name is cut to fit its suffix', len(long) == L.MAX_LENGTH and long.endswith('B'),
          long)
    for name in (got, long, L.fresh('1984', shipped={})):
        check('%s is a valid Working Title, with no FName number' % name[:24],
              not L.problems(name, shipped={}) and L.fname_split(name)[1] == 0)
    #: the Onslaught Editor's new missions outside Missions (wavespec.fresh_names)
    from riftlord.core import gamepath
    from riftlord.onslaught import wavespec
    folder = tempfile.mkdtemp(prefix='rl-samename-ons-')
    saved, taken_names = gamepath.TARGET, wavespec._taken_names
    try:
        gamepath.TARGET = folder
        wavespec._taken_names = lambda _f: ({'901-twin_test_p'}, {'twintest'})
        got = wavespec.fresh_names({'title': TITLE})
        check('the Onslaught Editor names a second mission with a letter',
              got['modName'] == 'TwinTestB' and os.path.basename(got['out']).startswith('901-Twin_Test_B_P'),
              (got['modName'], os.path.basename(got['out'])))
    finally:
        gamepath.TARGET, wavespec._taken_names = saved, taken_names
        shutil.rmtree(folder, ignore_errors=True)


def table_rows(path):
    """{row: (Guid hex, DefaultCharacters count)} of a build's mission table, and the
    Guids its ENDLESS_EXTENSION row lists, and the mod folder."""
    from riftlord.build import makemod, missionproto
    from riftlord.core import paksource
    from riftlord.formats.ue.uassetio import Package
    files = paksource.entries(path)

    def table(suffix):
        key = next((k for k in files if k.endswith(suffix)), None)
        if key is None:
            return None
        pkg = Package(files[key], files[key[:-7] + '.uexp'])
        pkg.load_payloads()
        return missionproto.Table(pkg)
    m = table('_MissionProtos.uasset')
    rows = {}
    for r in m.rows:
        chars = m.field(r, 'DefaultCharacters')
        rows[r['name']] = (bytes(m.field(r, 'Guid').payload)[:16].hex(),
                           m.struct_array(chars)[0] if chars is not None else 0)
    c = table('_CampaignProtos.uasset')
    listed = {}
    for r in (c.rows if c else []):
        _n, _t, items = c.struct_array(c.field(r, 'MissionList'))
        listed[r['name']] = [bytes(i[-24:-8]).hex() for i in items]
    mods = {k.split('/')[3] for k in files if k.startswith('OMD/Content/Mods/')}
    return rows, listed.get(makemod.ENDLESS_EXTENSION, []), listed, mods


def hub_view(paks):
    """What OrcsMustUI's hub shows for these paks: (Custom Maps, Endless extras), each
    [(row, title)]. A mission any campaign row lists (an ENDLESS_EXTENSION row included) is
    not a custom map (ModActor.ListedMissions); an ENDLESS_EXTENSION row's missions join
    Endless (HubBar.ExtendEndless)."""
    from riftlord.build import makemod
    from riftlord.missions import bucket
    listed, extras, missions = set(), [], []
    for pak in paks:
        rows, _ext, campaigns, _mods = table_rows(pak)
        titles = {r: t for r, t, _g in bucket._mission_rows(pak)}
        for name, guids in campaigns.items():
            listed |= set(guids)
            if name == makemod.ENDLESS_EXTENSION:
                extras += guids
        missions += [(g, r, titles.get(r)) for r, (g, _c) in rows.items()]
    customs = [(r, t) for g, r, t in missions if g not in listed]
    endless = [(r, t) for g, r, t in missions if g in extras]
    return customs, endless


def same_title(tmp):
    os.environ['OMD3_BUCKET'] = tmp
    from riftlord.core import gamepath, paksource
    from riftlord.missions import bucket
    from riftlord.onslaught import buildjob
    bucket.activate()
    #: a mission made before Working Titles: none stored, so it keeps `<mod>_Map01`
    shutil.copytree(os.path.join(FIXTURES, '.riftlord'), os.path.join(tmp, '.riftlord'))
    legacy = bucket.load('900_Fixture_Edit_P')
    check('a mission with no Working Title stored keeps <mod>_Map01',
          'workingTitle' not in legacy and bucket.level_name(legacy) == 'RiftLordFixtureEdit_Map01',
          bucket.level_name(legacy))
    check('and, not placed in Endless, it digests as before the field',
          bucket.bake_inputs(legacy) == bucket.bake_inputs(dict(legacy, endlessOnly=False)))
    plan = {'level': LEVEL}
    a = bucket.save_map(LEVEL, dict(plan), title=TITLE, details={'endless': True})
    b = bucket.save_map(LEVEL, dict(plan), title=TITLE, details={'endless': True})
    c = bucket.save_map(LEVEL, dict(plan), title=TITLE.lower(), details={'endless': False})
    ra, rb, rc = (bucket.load(x['mission']) for x in (a, b, c))
    check('three missions saved under one title', a['title'] == b['title'] == TITLE and
          c['title'] == TITLE.lower())
    check('each has its own id, with a letter suffix',
          [r['id'] for r in (ra, rb, rc)] == ['900_Twin_Test_P', '900_Twin_Test_B_P',
                                             '900_twin_test_C_P'],
          [r['id'] for r in (ra, rb, rc)])
    check('and its own mod, whatever the case',
          [r['mod'] for r in (ra, rb, rc)] == ['RiftLordTwinTest', 'RiftLordTwinTestB',
                                              'RiftLordtwintestC'],
          [r['mod'] for r in (ra, rb, rc)])
    check('and its own Working Title, RL_Scen_<Name> with a letter suffix',
          [r['workingTitle'] for r in (ra, rb, rc)] == ['RL_Scen_TwinTest', 'RL_Scen_TwinTestB',
                                                       'RL_Scen_twintestC'],
          [r['workingTitle'] for r in (ra, rb, rc)])
    every = [g for r in (ra, rb, rc) for g in r['guids'].values()]
    check('and Guids of its own', len(set(every)) == len(every) == 6)
    check('a copy may keep a title Missions has', buildjob.title_free(TITLE) is None)
    copied = bucket.load(bucket.copy(a['mission'], TITLE))
    check('a copy under the same title is a mission of its own',
          copied['id'] == '900_Twin_Test_D_P' and copied['workingTitle'] == 'RL_Scen_TwinTestD'
          and not set(copied['guids'].values()) & set(every),
          (copied['id'], copied['workingTitle']))
    view = bucket.settings(rb['id'], views=False)
    check('Settings show the level\'s name and offer Campaign Endless',
          view['workingTitle']['value'] == 'RL_Scen_TwinTestB' and view['canEndlessOnly'] and
          not view['endlessOnly'] and any(c['name'] == 'Endless' for c in view['choices']),
          view['workingTitle']['value'])
    why = None
    try:
        bucket.update_settings(rb['id'], working_title='RL_Scen_TwinTest')
    except SystemExit as exc:
        why = str(exc)
    check('another mission\'s Working Title is refused', why == 'RL_Scen_TwinTest is the level of Twin Test.', why)
    for x in (c['mission'], copied['id']):
        bucket.remove([x])
    #: the second is placed in Endless (Campaign Endless)
    baked = bucket.bake_inputs(rb)
    done = bucket.update_settings(rb['id'], placement='Endless')
    rb = bucket.load(rb['id'])
    check('Campaign Endless is kept in the record', rb.get('endlessOnly') is True and
          done.get('endlessOnly') is True and done.get('placement') == 'Endless' and
          bucket.settings(rb['id'], views=False)['placement'] == 'Endless', done)
    check('and it is a bake input', bucket.bake_inputs(rb) != baked)
    why = None
    try:
        bucket.update_settings('900_Fixture_Scratch_P', endless_only=True)
        bucket.update_settings('900_Fixture_Scratch_P', endless_only=False)
    except SystemExit as exc:
        why = str(exc)
    check('a Map Editor mission may be one', why is None, why)
    for ident in (ra['id'], rb['id']):
        bucket._bake(ident, None)
    pa, pb = bucket._source(ra['id']), bucket._source(rb['id'])
    rows_a, ext_a, _ca, mods_a = table_rows(pa)
    rows_b, ext_b, _cb, mods_b = table_rows(pb)
    check('the first builds its row and Endless twin',
          sorted(rows_a) == ['RiftLordTwinTest_Endless01', 'RiftLordTwinTest_Mission01'], sorted(rows_a))
    check('its Endless row lists its twin',
          ext_a == [rows_a['RiftLordTwinTest_Endless01'][0]], ext_a)
    check('the Endless mission builds its own row alone, with no DefaultCharacters',
          list(rows_b) == ['RiftLordTwinTestB_Mission01'] and rows_b['RiftLordTwinTestB_Mission01'][1] == 0,
          rows_b)
    check('its Endless row lists that row', ext_b == [rows_b['RiftLordTwinTestB_Mission01'][0]], ext_b)
    check('the builds keep the records\' Guids',
          rows_a['RiftLordTwinTest_Mission01'][0] == ra['guids']['RiftLordTwinTest_Mission01'][:32]
          and rows_b['RiftLordTwinTestB_Mission01'][0] == rb['guids']['RiftLordTwinTestB_Mission01'][:32])
    check('each build has one mod folder, its own', mods_a == {'RiftLordTwinTest'} and
          mods_b == {'RiftLordTwinTestB'}, (mods_a, mods_b))
    items = {i['id']: i for i in bucket.items()['items']}
    check('Missions list both by one title, told apart by Working Title',
          items[ra['id']]['title'] == items[rb['id']]['title'] == TITLE and
          (items[ra['id']]['workingTitle'], items[rb['id']]['workingTitle']) ==
          ('RL_Scen_TwinTest', 'RL_Scen_TwinTestB') and items[rb['id']]['endlessOnly'] and
          not items[ra['id']]['endlessOnly'] and
          {items[x]['state'] for x in (ra['id'], rb['id'])} == {'built'},
          [(items[x]['workingTitle'], items[x]['state']) for x in (ra['id'], rb['id'])])
    #: the campaign map plays no Endless of its own: the second does
    bucket.update_settings(ra['id'], endless=False)
    rows_a, ext_a, _ca, _m = table_rows(pa)
    check('Endless None takes the twin out of a current build in place',
          list(rows_a) == ['RiftLordTwinTest_Mission01'] and not ext_a and
          bucket.build_state(bucket.load(ra['id']), pa) == 'built', (list(rows_a), ext_a))
    dest = os.path.join(tmp, 'package')
    os.makedirs(dest)
    got = bucket.export([ra['id'], rb['id']], dest=dest, note=lambda _s: None)
    paks = [os.path.join(dest, p) for p in got['paks']]
    check('packaged as two paks named by their ids',
          sorted(got['paks']) == ['900_Twin_Test_B_P.pak', '900_Twin_Test_P.pak'], got['paks'])
    from riftlord.build.navigation import navpatch
    levels = [sorted(navpatch.maps_in_pak(p)) for p in sorted(paks)]
    check('each pak\'s level is its Working Title', levels == [['RL_Scen_TwinTestB'],
                                                            ['RL_Scen_TwinTest']], levels)
    entries = [set(paksource.names(p)) for p in paks]
    check('no pak entry is in both', not (entries[0] & entries[1]),
          sorted(entries[0] & entries[1])[:3])
    customs, endless = hub_view(paks)
    check('OrcsMustUI lists the first under Custom Maps, and only it',
          customs == [('RiftLordTwinTest_Mission01', TITLE)], customs)
    check('and the Endless mission in Endless, under the same title',
          endless == [('RiftLordTwinTestB_Mission01', TITLE)], endless)
    #: back to Custom Maps: out of Endless, and it needs a build
    bucket.update_settings(rb['id'], placement='CustomMaps')
    check('Campaign Custom Maps takes it out of Endless and leaves it out of date',
          'endlessOnly' not in bucket.load(rb['id']) and
          bucket.build_state(bucket.load(rb['id']), pb) == 'stale')
    gamepath.TARGET = None


def main():
    tmp = tempfile.mkdtemp(prefix='rl-samename-')
    try:
        names()
        same_title(os.path.join(tmp, 'missions'))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('%d of %d checks passed' % (sum(checks), len(checks)))
    sys.exit(0 if all(checks) else 1)


if __name__ == '__main__':
    main()
