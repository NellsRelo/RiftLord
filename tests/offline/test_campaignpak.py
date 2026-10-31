"""A custom campaign is one pak.

On a scratch Missions folder holding the test missions' pinned builds (buildcompare
baseline_pak) and the fixture campaign, with a stand-in Paks folder (RIFTLORD_SUPPORTED_PAKS)
whose ~mods is where installs go:

- Fixture Edit joins the fixture campaign (which lists Fixture Scratch) with a Working Title.
  With the campaign pak not installed, one mission installs alone. Install Campaign (both)
  writes the campaign's ONE pak (bucket._units, _pack_campaign) and removes the old ones: both missions'
  files as each is packed alone (its own mod folder, its level by its Working Title), the
  campaign's table listing both in its order, and one CustomMaps.yaml, in the campaign's
  folder, with every mission's notes and the campaign's (ProtoRegistry copies OrcsMustUI's
  files by pak and file name, so a pak carries each once). Missions shows the campaign and
  both missions Installed; installs.json keeps the campaign, not the missions.
- A changed Working Title makes the campaign Out of date; installing again packs again and
  bakes nothing.
- A mission placed in Endless (Campaign Endless) leaves the campaign's list and pak and is a
  pak of its own; another campaign takes it out of Endless again.
- Fixture Edit leaves the campaign: installing it writes its own pak and packs the campaign
  again without it (_moved_from).
- Package of a campaign mission zips the one campaign pak. A mission in a shipped campaign
  stays a pak of its own (it patches the game's campaign table).
- A campaign left with no missions (renamed .disabled) comes back when a mission is listed
  there again.

    python3 tests/offline/test_campaignpak.py

Scratch folders go in the temporary folder; the user's Missions and ~mods are never read.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))

FIXTURES = os.path.join(ROOT, 'tests', 'fixtures', 'missions')
EDIT, SCRATCH = '900_Fixture_Edit_P', '900_Fixture_Scratch_P'
CAMPAIGN = '900_FixtureCampaign_Campaign_P'
checks = []


def check(name, ok, detail=''):
    checks.append(bool(ok))
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, (': %s' % (detail,)) if detail else ''))


def setup(tmp):
    """The scratch Missions folder and ~mods; False when a pinned build is missing."""
    import buildcompare
    paks = os.path.join(tmp, 'Paks')
    os.makedirs(os.path.join(paks, '~mods'))
    os.environ['RIFTLORD_SUPPORTED_PAKS'] = paks
    missions = os.path.join(tmp, 'missions')
    os.makedirs(missions)
    os.environ['OMD3_BUCKET'] = missions
    shutil.copytree(os.path.join(FIXTURES, '.riftlord'), os.path.join(missions, '.riftlord'))
    shutil.copy(os.path.join(FIXTURES, CAMPAIGN + '.pak'), missions)
    pak = buildcompare.baseline_pak(EDIT)
    check('the pinned build of %s (buildcompare --pin)' % EDIT, pak, pak)
    if not pak:
        return False
    shutil.copy(pak, os.path.join(missions, EDIT + '.pak'))
    return scratch_build(os.path.join(missions, SCRATCH + '.pak'))


def scratch_build(out):
    """Fixture Scratch's build, taken out of its pinned campaign pak (a custom campaign is one
    pak): every file but the campaign's, with its notes' Maps entries in its own folder."""
    import buildcompare
    from riftlord.build import makemod
    from riftlord.core import paksource
    from riftlord.missions import customcampaign
    base = buildcompare.BASELINES.get(SCRATCH) or {}
    pak = os.path.join(buildcompare.PINNED, SCRATCH, base.get('digest') or 'none', CAMPAIGN + '.pak')
    check('the pinned campaign pak of %s (buildcompare --pin)' % SCRATCH, os.path.isfile(pak), pak)
    if not os.path.isfile(pak):
        return False
    files = paksource.entries(pak)
    notes = customcampaign.parse_notes(files[makemod.omui_inner(
        'FixtureCampaign_Campaign', customcampaign.NOTES_NAME)].decode('utf-8'))
    files = {k: v for k, v in files.items() if '/Mods/FixtureCampaign_Campaign/' not in k}
    makemod.add_omui_file(files, 'RiftLordFixtureScratch', customcampaign.NOTES_NAME,
                          customcampaign.notes_text({'Maps': notes['Maps']}))
    paksource.write(out, files)
    return True


def current(ident):
    """Stamp a record as built from what it holds: its campaign is a bake input (the loading
    screen's line), and these tests pack the pinned builds rather than baking."""
    from riftlord.missions import bucket
    rec = bucket.load(ident)
    rec['baked'] = bucket.bake_inputs(rec)
    bucket._write_json(bucket._record_path(ident), rec)


def inside(pak):
    """({mod folder}, {OrcsMustUI file name: [inner paths]}) of a pak."""
    from riftlord.build import makemod
    from riftlord.core import paksource
    names = paksource.names(pak)
    mods = {n.split('/')[3] for n in names if n.startswith('OMD/Content/Mods/') and n.count('/') > 3}
    omui = {}
    for n in names:
        m = makemod.OMUI_FILE_RE.search(n)
        if m and not m.group(2).endswith(('.uasset', '.uexp', '.ubulk')):
            omui.setdefault(m.group(2), []).append(n)
    return mods, omui


def listed(pak):
    """The campaign rows' MissionLists in a pak: {row: [Guid hex]}."""
    from riftlord.core import paksource
    from riftlord.missions import customcampaign
    files = paksource.entries(pak)
    out = {}
    for k in files:
        if k.endswith('_CampaignProtos.uasset'):
            t = customcampaign._table(files[k], files[k[:-7] + '.uexp'])
            for r in t.rows:
                out[r['name']] = [g.hex() for g in customcampaign._guids(t, r)]
    return out


def main_flow(tmp):
    from riftlord.build import makemod
    from riftlord.build.navigation import navpatch
    from riftlord.core import gamepath, paksource
    from riftlord.missions import bucket, customcampaign
    bucket.activate()
    mods = gamepath.mods_dir()
    camp = next(c for c in customcampaign.installed(bucket.root()))
    #: Fixture Edit joins the campaign, second, with a Working Title
    bucket.update_settings(EDIT, placement=customcampaign.CUSTOM + camp['key'],
                           working_title='scenario_fear_01')
    current(EDIT)
    edit, scratch = bucket.load(EDIT), bucket.load(SCRATCH)
    g_edit = edit['guids']['RiftLordFixtureEdit_Mission01'][:32]
    g_scratch = scratch['guids']['RiftLordFixtureScratch_Mission01'][:32]
    data = bucket.items()
    check('Missions list both in the campaign, Not installed',
          {i['id']: (i['campaign'], i['installed']) for i in data['items']} ==
          {EDIT: (camp['key'], 'no'), SCRATCH: (camp['key'], 'no')})
    baked = []
    bucket._bake = lambda ident, note=None: baked.append(ident)
    #: the campaign pak not installed: one mission installs alone, as before
    check('one campaign mission is a pak of its own while its campaign is not installed',
          bucket._units([EDIT]) == [(None, [EDIT])])
    got = bucket.export([SCRATCH], note=lambda _s: None)
    check('so it installs alone, and reads Installed',
          got['paks'] == [SCRATCH + '.pak'] and sorted(os.listdir(mods)) == [SCRATCH + '.pak'] and
          {i['id']: i['installed'] for i in bucket.items()['items']}[SCRATCH] == 'installed',
          got['paks'])
    shutil.copy(os.path.join(bucket.root(), EDIT + '.pak'), os.path.join(mods, EDIT + '.pak'))
    units = bucket._units([SCRATCH, EDIT])
    check('Install Campaign (all its missions) is the campaign\'s unit, with both',
          len(units) == 1 and units[0][0]['key'] == camp['key'] and units[0][1] == [SCRATCH, EDIT],
          [(c and c['key'], u) for c, u in units])
    got = bucket.export([SCRATCH, EDIT], note=lambda _s: None)
    check('one pak written, the campaign\'s', got['paks'] == [CAMPAIGN + '.pak'], got['paks'])
    check('the missions\' own paks are removed', sorted(os.listdir(mods)) == [CAMPAIGN + '.pak'],
          sorted(os.listdir(mods)))
    pak = os.path.join(mods, CAMPAIGN + '.pak')
    folders, omui = inside(pak)
    check('it holds each mission\'s mod folder and the campaign\'s',
          {'RiftLordFixtureEdit', 'RiftLordFixtureScratch', 'FixtureCampaign_Campaign'} <= folders,
          sorted(folders))
    check('each mission\'s level by its Working Title',
          sorted(navpatch.maps_in_pak(pak)) == ['RiftLordFixtureScratch_Map01', 'scenario_fear_01'],
          sorted(navpatch.maps_in_pak(pak)))
    files = paksource.entries(pak)
    for ident in (EDIT, SCRATCH):
        alone = bucket._packed_files(bucket._source(ident), rec=bucket.load(ident))
        kept = {k: v for k, v in alone.items() if k not in makemod.omui_files(alone).values()}
        same = all(files.get(k) == v for k, v in kept.items())
        check('%s: its files as packed alone, byte for byte (%d)' % (ident, len(kept)), same)
    check('the campaign lists both, in its order',
          listed(pak).get(camp['row']) == [g_scratch, g_edit], listed(pak))
    check('each OrcsMustUI file once, in the campaign\'s folder',
          omui and all(len(v) == 1 and '/FixtureCampaign_Campaign/' in v[0] for v in omui.values()),
          omui)
    notes = customcampaign.build_notes(pak)
    rows = {e['Mission'] for e in notes['Maps']}
    check('its notes hold every mission\'s rows and the campaign',
          {'RiftLordFixtureEdit_Mission01', 'RiftLordFixtureScratch_Mission01'} <= rows and
          [e['Campaign'] for e in notes['Campaigns']] == [camp['row']], (sorted(rows), notes['Campaigns']))
    check('OrcsMustUI\'s rules on its files hold', not makemod.omui_problems(files),
          makemod.omui_problems(files)[:2])
    data = bucket.items()
    states = {i['id']: i['installed'] for i in data['items']}
    entry = next(c for c in data['campaigns'] if c['key'] == camp['key'])
    check('Missions show the campaign and both missions Installed',
          states == {EDIT: 'installed', SCRATCH: 'installed'} and entry['installed'] == 'installed'
          and entry['members'] == [SCRATCH, EDIT], (states, entry.get('installed')))
    installs = bucket._installs()
    check('installs.json keeps the campaign, not its missions',
          CAMPAIGN in installs and EDIT not in installs and SCRATCH not in installs and
          sorted(installs[CAMPAIGN]['members']) == [EDIT, SCRATCH])
    #: a changed Working Title: Out of date, packed again, nothing baked
    bucket.update_settings(EDIT, working_title='scenario_fear_02')
    check('a member\'s Working Title makes the campaign Out of date',
          {i['installed'] for i in bucket.items()['items']} == {'changed'})
    bucket.export([SCRATCH], note=lambda _s: None)
    check('installing again packs again and bakes nothing',
          not baked and 'scenario_fear_02' in navpatch.maps_in_pak(pak) and
          {i['installed'] for i in bucket.items()['items']} == {'installed'}, baked)
    #: an old install note (before the campaign was one pak) reads Out of date
    old = bucket._installs()
    del old[CAMPAIGN]['members']
    bucket._write_json(bucket._meta('installs.json'), old)
    check('a campaign installed before it was one pak is Out of date',
          next(c for c in bucket.items()['campaigns'] if c['key'] == camp['key'])['installed']
          == 'changed')
    #: Package zips the one campaign pak
    #: installed, the campaign pak takes in a mission installed on its own: never both
    units = bucket._units([EDIT], dest=gamepath.mods_dir())
    check('with the campaign pak installed, one mission\'s Install packs the campaign again',
          len(units) == 1 and units[0][0] is not None and units[0][1] == [SCRATCH, EDIT])
    got = bucket.export([EDIT], note=lambda _s: None)
    check('and no mission is both alone and in it', sorted(os.listdir(mods)) == [CAMPAIGN + '.pak']
          and got['paks'] == [CAMPAIGN + '.pak'], sorted(os.listdir(mods)))
    name, blob = bucket.package([SCRATCH])
    check('Package of one campaign mission zips it alone',
          zipfile.ZipFile(io.BytesIO(blob)).namelist() == [SCRATCH + '.pak'], name)
    name, blob = bucket.package([SCRATCH, EDIT])
    zipped = zipfile.ZipFile(io.BytesIO(blob)).namelist()
    check('Package Campaign zips the one campaign pak',
          zipped == [CAMPAIGN + '.pak'] and name == CAMPAIGN + '.zip', (name, zipped))
    return camp, g_edit, g_scratch


def variant(camp, g_edit, g_scratch):
    """Fixture Edit placed in Endless (Campaign Endless): out of the campaign's list and its
    pak, a pak of its own. Its pinned build is no Endless one; the record is stamped current
    to pack it."""
    from riftlord.missions import bucket, customcampaign
    bucket.update_settings(EDIT, placement=customcampaign.ENDLESS)
    rec = bucket.load(EDIT)
    check('Campaign Endless takes it out of the campaign\'s list',
          rec.get('endlessOnly') is True and
          next(c for c in customcampaign.installed(bucket.root()) if c['key'] == camp['key'])['missions']
          == [g_scratch], rec.get('placement'))
    check('Settings and the editors show it as Campaign Endless',
          bucket.settings(EDIT, views=False)['placement'] == customcampaign.ENDLESS and
          bucket.item(EDIT)['placement'] == customcampaign.ENDLESS)
    current(EDIT)
    item = next(i for i in bucket.items()['items'] if i['id'] == EDIT)
    check('Missions list it in no campaign, and it is a pak of its own',
          item['campaign'] is None and item['endlessOnly'] and
          bucket._units([EDIT]) == [(None, [EDIT])] and
          bucket.campaign_members(camp) == [SCRATCH], item['campaign'])
    bucket.update_settings(EDIT, placement=customcampaign.CUSTOM + camp['key'])
    check('another campaign takes it out of Endless, and lists it',
          'endlessOnly' not in bucket.load(EDIT) and
          next(c for c in customcampaign.installed(bucket.root()) if c['key'] == camp['key'])['missions']
          == [g_scratch, g_edit])
    current(EDIT)


def leaving(camp, g_edit, g_scratch):
    from riftlord.build import makemod
    from riftlord.core import gamepath
    from riftlord.missions import bucket
    bucket.update_settings(EDIT, placement=makemod.CUSTOM_MAPS)
    current(EDIT)
    units = bucket._units([EDIT])
    check('out of the campaign, a mission is a pak of its own', units == [(None, [EDIT])], units)
    got = bucket.export([EDIT], note=lambda _s: None)
    mods = gamepath.mods_dir()
    pak = os.path.join(mods, CAMPAIGN + '.pak')
    folders, _o = inside(pak)
    check('installed alone, and the campaign pak packed again without it',
          sorted(os.listdir(mods)) == [CAMPAIGN + '.pak', EDIT + '.pak'] and
          'RiftLordFixtureEdit' not in folders and listed(pak).get(camp['row']) == [g_scratch] and
          sorted(got['paks']) == [CAMPAIGN + '.pak', EDIT + '.pak'], (sorted(os.listdir(mods)), got['paks']))
    states = {i['id']: i['installed'] for i in bucket.items()['items']}
    check('both read Installed', states == {EDIT: 'installed', SCRATCH: 'installed'}, states)
    listing = [{'id': 'x', 'campaign': None, 'placement': 'OMD3_Main_Story', 'guids': [],
                'endlessOnly': False}]
    check('a mission in a shipped campaign is a pak of its own',
          bucket._units(['x'], listing) == [(None, ['x'])])


def revive(tmp):
    """A campaign whose last mission left it is renamed .disabled; listing a mission there
    again brings it back (customcampaign._revive), as moving a mission back does."""
    from riftlord.missions import customcampaign as C
    folder = os.path.join(tmp, 'revive')
    os.makedirs(folder)
    shutil.copy(os.path.join(FIXTURES, CAMPAIGN + '.pak'), folder)
    camp = C.installed(folder)[0]
    only = bytes.fromhex(camp['missions'][0])
    C.place(only, None, mods=folder)
    check('a campaign left with no missions is renamed .disabled',
          not C.installed(folder) and os.path.exists(camp['pak'] + '.disabled'), os.listdir(folder))
    got = C.place(only, camp['key'], mods=folder)
    check('listing a mission there again brings it back, listing it',
          [c['missions'] for c in C.installed(folder)] == [[only.hex()]] and
          got.get('campaign') == camp['title'], os.listdir(folder))


def main():
    tmp = tempfile.mkdtemp(prefix='rl-campaignpak-')
    try:
        if setup(tmp):
            camp, g_edit, g_scratch = main_flow(tmp)
            variant(camp, g_edit, g_scratch)
            leaving(camp, g_edit, g_scratch)
        revive(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('%d of %d checks passed' % (sum(checks), len(checks)))
    sys.exit(0 if all(checks) else 1)


if __name__ == '__main__':
    main()
