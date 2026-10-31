"""Campaign Index and Working Title.

Campaign Index: a mission's place in a campaign's MissionList (customcampaign.place `index`,
move, index_problem, choices' size/first/index) on a copy of the fixture campaign; a shipped
campaign's list with a mission inserted (missionproto.add_mission `at`), its own missions
kept byte for byte and in order, its first mission kept first (SHIPPED_FIRST); the Missions
records' rules (bucket.update_settings, shipped_index).

Working Title: leveltitle.problems (characters, length, shipped names, other missions'
levels), and retitle on a built mission -- a plain title and one ending `_<digits>` (stored
as the cooker stores Scenario_10: base string plus FName number) -- read back: level entry,
World, self-references, function map, MissionLevel, every import resolving, no old name
left, Guids unchanged.

    python3 tests/offline/test_campaignindex.py [BUILT_MISSION]

BUILT_MISSION is a built mission (a pak, or a loose build folder; only read); default: the
pinned baseline build of the test mission 900_Fixture_Edit_P (buildcompare.baseline_pak).
Scratch Missions folders go in the temporary folder; the user's Missions are never read.
"""
import os
import shutil
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))

FIXTURES = os.path.join(ROOT, 'tests', 'fixtures', 'missions')
checks = []


def check(name, ok, detail=''):
    checks.append(bool(ok))
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, (': %s' % (detail,)) if detail else ''))


def refused(fn, *args, **kwargs):
    """The SystemExit text `fn` raises, or None."""
    try:
        fn(*args, **kwargs)
    except SystemExit as exc:
        return str(exc)
    return None


def custom_campaigns(tmp):
    from riftlord.missions import customcampaign as C
    shutil.copy(os.path.join(FIXTURES, '900_FixtureCampaign_Campaign_P.pak'), tmp)
    have = C.installed(tmp)
    check('the fixture campaign reads', len(have) == 1, [c['key'] for c in have])
    key = have[0]['key']
    first = bytes.fromhex(have[0]['missions'][0])
    a, b, c = (bytes([0xa0 + i]) * 16 for i in range(3))
    for g in (a, b):
        C.place(g, key, mods=tmp)
    order = lambda: [bytes.fromhex(g) for g in C.installed(tmp)[0]['missions']]  # noqa: E731
    check('no index adds a mission last', order() == [first, a, b])
    got = C.place(c, key, mods=tmp, index=2)
    check('index 2 puts a new mission second', order() == [first, c, a, b], got)
    check('place reports the index', got.get('campaign_index') == 2, got.get('campaign_index'))
    C.place(c, key, mods=tmp)
    check('placing again with no index keeps its place', order() == [first, c, a, b])
    C.move(key, c, 4, mods=tmp)
    check('move to 4: the missions after it move up', order() == [first, a, b, c])
    C.move(key, first, 3, mods=tmp)
    check('move the first to 3', order() == [a, b, first, c])
    for bad in (0, 5, '', 'x', '2.5', -1, True):
        why = refused(C.move, key, c, bad, mods=tmp)
        check('index %r refused with its reason' % (bad,),
              why == 'Campaign Index is a whole number from 1 to 4.', why)
    check('a list is unchanged by a refused move', order() == [a, b, first, c])
    why = refused(C.place, b'\xee' * 16, key, mods=tmp, index=6)
    check('a new mission past the end is refused before anything is written',
          why == 'Campaign Index is a whole number from 1 to 5.' and len(order()) == 4, why)
    why = refused(C.move, key, b'\xee' * 16, 1, mods=tmp)
    check('move refuses a mission the campaign does not list', why and 'does not list' in why, why)
    check('index_problem on a one-mission list', C.index_problem(2, 1) == 'Campaign Index is 1.')
    check('index_problem takes digits with spaces', C.index_problem(' 3 ', 4) == '')
    picks = {x['kind'] + ':' + x['label']: x for x in C.choices(shipped=False, mods=tmp, guid=b)}
    camp = next(v for k, v in picks.items() if k.startswith('custom:'))
    check('choices give a listed mission its index and the list size',
          camp['index'] == 2 and camp['size'] == 4 and camp['first'] == 1, camp)
    camp = next(x for x in C.choices(shipped=False, mods=tmp, guid=b'\xee' * 16)
                if x['kind'] == 'custom')
    check('choices give an unlisted mission the size with it in',
          camp['index'] is None and camp['size'] == 5, camp)
    check('Custom Maps and a new campaign take no index range',
          picks['none:Custom Maps']['size'] == 0 and picks['new:New Campaign']['size'] == 1)
    #: the rewrite keeps the campaign's other bytes (_check), and its pak reads
    from riftlord.core import pkgcheck, paksource
    files = paksource.entries(C.installed(tmp)[0]['pak'])
    tables = [k for k in files if k.endswith('_CampaignProtos.uasset')]
    bad = pkgcheck.problems(files[tables[0]], files[tables[0][:-7] + '.uexp'], 'campaign', 'table')
    check('the rewritten campaign table passes pkgcheck', tables and not bad, bad[:2])
    return key, C.installed(tmp)[0]


def shipped_campaign():
    from riftlord.build import makemod, missionproto
    from riftlord.missions import customcampaign as C
    stock = missionproto.read(missionproto.CAMPAIGN_TABLE)
    row = stock.row('OMD3_Main_Story')
    _n, _t, items = stock.struct_array(stock.field(row, 'MissionList'))
    ours = b'\xb1' * 16
    n = stock.add_mission(row, ours, at=3)
    _n, _t, after = stock.struct_array(stock.field(row, 'MissionList'))
    check('a shipped list gains one mission at place 3', n == len(items) + 1 and
          after[2][-24:-8] == ours, n)
    check('its own missions are kept byte for byte, in order', after[:2] + after[3:] == items)
    ua, ux = stock.write()
    from riftlord.core import pkgcheck
    bad = pkgcheck.problems(ua, ux, 'CampaignProtos', 'table')
    check('the shipped table with ours inserted passes pkgcheck', not bad, bad[:2])
    size = len(makemod.campaign_order('OMD3_Main_Story')) + 1
    why = C.index_problem(1, size, C.SHIPPED_FIRST)
    check('index 1 in a shipped campaign is refused (its first mission stays first)',
          why == 'Campaign Index is a whole number from 2 to %d.' % size, why)
    check('the last place in a shipped campaign is taken', not C.index_problem(size, size, 2))
    picks = [x for x in C.choices(shipped=True, mods=tempfile.mkdtemp())
             if x['kind'] == 'shipped']
    check('shipped choices start at SHIPPED_FIRST and end one past their list',
          picks and all(x['first'] == 2 and x['size'] == len(x['missions']) + 1 for x in picks),
          [(x['name'], x['first'], x['size']) for x in picks])


def records(tmp):
    """bucket's rules, on a scratch copy of the fixture Missions."""
    os.environ['OMD3_BUCKET'] = tmp
    from riftlord.missions import bucket
    from riftlord.build import makemod
    bucket.ACTIVE = True
    shutil.copytree(os.path.join(FIXTURES, '.riftlord'), os.path.join(tmp, '.riftlord'))
    shutil.copy(os.path.join(FIXTURES, '900_FixtureCampaign_Campaign_P.pak'), tmp)
    ident = '900_Fixture_Edit_P'
    view = bucket.settings(ident, views=False)
    check('Settings offer a Working Title for a Map Editor mission: a mission with none '
          'stored keeps its level\'s name, and RL_Scen_<Name> is the default',
          view['workingTitle'] and view['workingTitle']['default'] == 'RL_Scen_FixtureEdit'
          and view['workingTitle']['value'] == 'RiftLordFixtureEdit_Map01',
          view['workingTitle'] and (view['workingTitle']['value'],
                                    view['workingTitle']['default']))
    check('Settings list the other missions\' levels as taken',
          'riftlordfixturescratch_map01' in view['workingTitle']['taken'], sorted(view['workingTitle']['taken']))
    why = refused(bucket.update_settings, ident, working_title='RiftLordFixtureScratch_Map01')
    check('a Working Title another mission\'s level has is refused',
          why == 'RiftLordFixtureScratch_Map01 is the level of Fixture Scratch.', why)
    got = bucket.update_settings(ident, working_title='scenario_fear_01')
    rec = bucket.load(ident)
    check('a Working Title is kept in the record', rec.get('workingTitle') == 'scenario_fear_01'
          and got.get('workingTitle') == 'scenario_fear_01', got)
    why = refused(bucket.update_settings, '900_Fixture_Scratch_P', working_title='SCENARIO_FEAR_01')
    check('Working Titles are unique whatever their case', why and 'Fixture Edit' in why, why)
    bucket.update_settings(ident, working_title='RiftLordFixtureEdit_Map01')
    check('<mod>_Map01 clears it', 'workingTitle' not in bucket.load(ident))
    bucket.update_settings(ident, working_title='')
    check('an empty Working Title takes the default, RL_Scen_<Name>',
          bucket.load(ident).get('workingTitle') == 'RL_Scen_FixtureEdit',
          bucket.load(ident).get('workingTitle'))
    bucket.update_settings(ident, working_title='RiftLordFixtureEdit_Map01')
    #: into the fixture campaign at 1, then moved to 2
    key = bucket.settings(ident, views=False)['choices']
    camp = next(c for c in key if c['kind'] == 'custom')
    got = bucket.update_settings(ident, placement=camp['name'], campaign_index=1)
    items = {i['id']: i for i in bucket.items()['items']}
    check('Settings list a mission at the index chosen',
          items[ident]['campaignIndex'] == 1 and items['900_Fixture_Scratch_P']['campaignIndex'] == 2,
          (items[ident]['campaignIndex'], items['900_Fixture_Scratch_P']['campaignIndex']))
    check('a custom campaign\'s index is not kept in the record',
          'campaignIndex' not in bucket.load(ident) and
          'campaignIndex' not in (bucket.load(ident).get('details') or {}))
    bucket.set_campaign_index(camp['name'][len('custom:'):], ident, 2)
    items = {i['id']: i for i in bucket.items()['items']}
    check('the Missions row\'s index moves it',
          items[ident]['campaignIndex'] == 2 and items['900_Fixture_Scratch_P']['campaignIndex'] == 1)
    why = refused(bucket.update_settings, ident, campaign_index=3)
    check('Settings refuse an index past the end', why == 'Campaign Index is a whole number from 1 to 2.', why)
    #: a shipped campaign's index is the record's, a bake input
    baked = bucket.bake_inputs(bucket.load(ident))
    bucket.update_settings(ident, placement='OMD3_Main_Story', campaign_index=4)
    rec = bucket.load(ident)
    check('a shipped campaign\'s index is kept in the record', rec.get('campaignIndex') == 4,
          rec.get('campaignIndex'))
    check('it is a bake input', bucket.bake_inputs(rec) != baked)
    why = refused(bucket.update_settings, ident, campaign_index=1)
    check('index 1 in a shipped campaign is refused', why and 'from 2 to' in why, why)
    size = len(makemod.campaign_order('OMD3_Main_Story')) + 1
    check('shipped_index takes the last place', bucket.shipped_index('OMD3_Main_Story', size) == size)
    bucket.update_settings(ident, placement=makemod.CUSTOM_MAPS)
    check('leaving the shipped campaign drops its index', 'campaignIndex' not in bucket.load(ident))
    rec = dict(bucket.load(ident))
    del rec['placement']
    check('a record with no index digests as before the field',
          'campaignIndex' not in rec and bucket.bake_inputs(rec) == bucket.bake_inputs(dict(rec, campaignIndex=None)))


def working_titles(path):
    from riftlord.build import leveltitle, makelevel, missionproto
    from riftlord.core import paksource
    from riftlord.formats.ue.uassetio import Package
    from riftlord.mapeditor import probe
    shipped = leveltitle.shipped_names()
    check('shipped names include levels and sublevels',
          {'scenario_10', 'scenario_10_castle', 'scenario_ice_01'} <= set(shipped), len(shipped))
    for text, want in (('scenario_fear_01', None), ('scenario_fear_1', None),
                       ('Scenario_10', 'Scenario_10 is a shipped level.'),
                       ('scenario_10', 'Scenario_10 is a shipped level.'),
                       ('fear itself', 'letters, digits and underscores'),
                       ('1fear', 'letters, digits and underscores'),
                       ('a' * 65, 'at most 64 characters'), ('None', 'cannot be None'),
                       ('MyLevel', None)):
        got = leveltitle.problems(text, taken={'mylevel': 'Other'} if text == 'MyLevel' else {},
                                  shipped=shipped)
        if text == 'MyLevel':
            want = 'MyLevel is the level of Other.'
        ok = (not got) if want is None else any(want in g for g in got)
        check('Working Title %r: %s' % (text[:20], want or 'taken'), ok, got)
    check('a built mission to retitle (the test mission\'s pinned build: buildcompare --pin)',
          path and os.path.exists(path), path)
    if not (path and os.path.exists(path)):
        return
    files = paksource.entries(path)
    level = leveltitle.level_entry(files)
    mod = level.split('/')[3]

    def mission_rows(fs):
        key = next(k for k in fs if k.endswith('_MissionProtos.uasset'))
        pkg = Package(fs[key], fs[key[:-7] + '.uexp'])
        pkg.load_payloads()
        t = missionproto.Table(pkg)
        names = [s for s, _h in pkg.names]
        out = {}
        for r in t.rows:
            idx, num = struct.unpack_from('<ii', bytes(t.field(r, 'MissionLevel').payload), 0)
            out[r['name']] = (bytes(t.field(r, 'Guid').payload), names[idx], num)
        return out

    old_rows = mission_rows(files)
    for title, base, number in (('scenario_fear_01', 'scenario_fear_01', 0),
                                ('scenario_fear_1', 'scenario_fear', 2)):
        out, report = leveltitle.retitle(dict(files), mod, title)
        entry = 'OMD/Content/Mods/%s/Maps/%s/%s.umap' % (mod, title, title)
        check('%s: the level is at Maps/%s/%s' % (title, title, title),
              leveltitle.level_entry(out) == entry, leveltitle.level_entry(out))
        check('%s: the build\'s other files are kept' % title, len(out) == len(files))
        pkg = Package(out[entry], out[entry[:-5] + '.uexp'])
        pkg.load_payloads()
        world = makelevel.world_name(pkg)
        check('%s: the World is %r number %d' % (title, base, number), world == (base, number), world)
        path_ = entry[len('OMD/Content'):-len('.umap')]
        check('%s: the level\'s references to its own actors name its World' % title,
              makelevel.self_ref_problems(pkg, '/Game' + path_) == 0)
        names = [s for s, _h in pkg.names]
        uber = 'ExecuteUbergraph_%s' % base
        check('%s: the ubergraph is %s number %d' % (title, uber, number),
              any(names[struct.unpack_from('<i', e['meta'], 16)[0]] == uber and
                  struct.unpack_from('<i', e['meta'], 20)[0] == number for e in pkg.exports))
        rows = mission_rows(out)
        want = '/Game%s.%s' % (path_, base)
        check('%s: every mission row plays the level, Guid unchanged' % title,
              rows.keys() == old_rows.keys() and
              all(rows[r] == (old_rows[r][0], want, number) for r in rows),
              {r: v[1:] for r, v in rows.items()})
        left = [k for k in out if '%s_Map01' % mod in k]
        check('%s: no file keeps the old name' % title, not left, left[:2])
        checked, missing = probe.import_problems(out, entry)
        check('%s: every level import names an export, string and number (%d)' % (title, checked),
              not missing, missing[:3])
        check('%s: reported as %s' % (title, 'split' if number else 'plain'),
              report['split'] == bool(number) and (report['renumbered'] > 0) == bool(number), report)
    #: Install and Package pack through bucket._pack, which applies the record's title
    from riftlord.missions import bucket
    from riftlord.build.navigation import navpatch
    dest = tempfile.mkdtemp(prefix='rl-retitle-pack-')
    try:
        pak = bucket._pack(path, dest, rec={'mod': mod, 'workingTitle': 'scenario_fear_01'})
        found = navpatch.maps_in_pak(pak)
        check('a packed pak names its level by the Working Title, the pak by the mission',
              list(found) == ['scenario_fear_01'] and
              os.path.basename(pak) == paksource.stem(path) + '.pak', (list(found), pak))
    finally:
        shutil.rmtree(dest, ignore_errors=True)
    same, report = leveltitle.retitle(dict(files), mod, '')
    check('no Working Title leaves the files alone', same == files and report == {})
    why = refused(leveltitle.retitle, dict(files), mod, 'bad name')
    check('retitle refuses a bad name', why and 'letters, digits' in why, why)


def main():
    import buildcompare
    path = sys.argv[1] if len(sys.argv) > 1 else buildcompare.baseline_pak('900_Fixture_Edit_P')
    tmp = tempfile.mkdtemp(prefix='rl-campaignindex-')
    try:
        os.makedirs(os.path.join(tmp, 'mods'))
        custom_campaigns(os.path.join(tmp, 'mods'))
        shipped_campaign()
        records(os.path.join(tmp, 'missions'))
        working_titles(path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('%d of %d checks passed' % (sum(checks), len(checks)))
    sys.exit(0 if all(checks) else 1)


if __name__ == '__main__':
    main()
