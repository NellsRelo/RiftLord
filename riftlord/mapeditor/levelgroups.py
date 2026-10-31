"""The campaign each level the Map Editor lists is filed under, from the game's own tables.

A level goes under the campaign whose MissionList names a mission that plays on it
(MissionProtos `MissionLevel`, matched by Guid), named by that campaign's CampaignName:

  1. the shipped story campaigns (customcampaign.OFFERED), in the table's order, as the
     game loads the table (a mod that lists its mission in one patches it);
  2. the custom campaigns installed in ~mods (customcampaign.installed);
  3. any other campaign of the loaded table (Endless, Weekly Challenges), in its order;
  4. else CUSTOM_MAPS: OrcsMustUI's Custom Maps tab, where a mission no campaign lists is.

Missions are the loaded MissionProtos rows (with the rows mods add to it) and each ~mods
pak's own `<Mod>_MissionProtos` tables.

    groups()  {level: {campaign, order, index, title}}
"""

import glob
import os
import struct

#: The group of a level no campaign lists.
CUSTOM_MAPS = 'Custom Maps'


def _loaded_table(path):
    """The table at `path` (no extension) as the game loads it: ~mods over the base paks."""
    from riftlord.build import missionproto
    from riftlord.build.navigation import navpatch
    idx = navpatch.live_index()
    ua, ux = path + '.uasset', path + '.uexp'
    if ua in idx and ux in idx:
        try:
            return missionproto.Table(missionproto._loaded(
                navpatch.read_from_pak(*idx[ua]), navpatch.read_from_pak(*idx[ux])))
        except (Exception, SystemExit):              # noqa: BLE001
            pass
    return missionproto.read(path)


def _rows(table):
    """[(guid bytes, level name, mission title)] of a MissionProtos table's rows."""
    from riftlord.build.reconcile import readable
    out = []
    for row in table.rows:
        guid = table.field(row, 'Guid')
        level = table.field(row, 'MissionLevel')
        if guid is None or level is None or len(level.payload) < 8:
            continue
        at = struct.unpack_from('<i', bytes(level.payload), 0)[0]
        if not 0 <= at < len(table.names):
            continue
        name = table.names[at]
        name = name[0] if isinstance(name, (tuple, list)) else name
        asset = str(name).split('.')[0].rsplit('/', 1)[-1]
        if asset and asset != 'None':
            out.append((bytes(guid.payload)[-16:], asset,
                        readable(table.field(row, 'MissionName')) or ''))
    return out


def _mod_rows():
    """[(guid, level, title)] of the missions ~mods paks keep in tables of their own."""
    from riftlord.build import missionproto
    from riftlord.build.navigation import navpatch
    out = []
    paks = sorted(glob.glob(os.path.join(navpatch.mods_dir(), '*.pak')))
    for pak in paks + list(navpatch.opened_paks()):
        try:
            names = navpatch.pak_names(pak)
        except (Exception, SystemExit):              # noqa: BLE001
            continue
        for key, (_p, entry) in names.items():
            if not key.endswith('_MissionProtos.uasset') or key.startswith('/Game/Data/'):
                continue
            body = names.get(key[:-7] + '.uexp')
            if not body:
                continue
            try:
                table = missionproto.Table(missionproto._loaded(
                    navpatch.read_from_pak(pak, entry), navpatch.read_from_pak(pak, body[1])))
            except (Exception, SystemExit):          # noqa: BLE001
                continue
            out += _rows(table)
    return out


def _campaigns():
    """[(name, [guid bytes])] in filing order (the module docstring's 1 to 3)."""
    from riftlord.build import missionproto
    from riftlord.missions import customcampaign
    table = _loaded_table(missionproto.CAMPAIGN_TABLE)
    story, other = [], []
    for row in table.rows:
        got = (customcampaign._text(table, row, 'CampaignName') or row['name'],
               customcampaign._guids(table, row))
        (story if row['name'] in customcampaign.OFFERED else other).append(got)
    custom = []
    try:
        custom = [(c['title'], [bytes.fromhex(g) for g in c['missions']])
                  for c in customcampaign.installed()]
    except (Exception, SystemExit):                  # noqa: BLE001
        pass
    return story + custom + other


def groups():
    """{level: {campaign, order, index, title}}: the campaign a level is filed under, that
    campaign's place among the groups (CUSTOM_MAPS last), the level's place in its
    campaign's list, and the name of the mission that plays on it ('' when unnamed)."""
    from riftlord.build import missionproto
    rows = _rows(_loaded_table(missionproto.TABLE))
    try:
        rows += _mod_rows()
    except Exception:                                # noqa: BLE001
        pass
    by_guid = {}
    titles = {}
    for guid, level, title in rows:
        by_guid.setdefault(guid, level)
        if title:
            titles.setdefault(level, title)
    out = {}
    campaigns = _campaigns()
    for order, (name, guids) in enumerate(campaigns):
        for index, guid in enumerate(guids):
            level = by_guid.get(guid)
            if level and level not in out:
                out[level] = {'campaign': name, 'order': order, 'index': index,
                              'title': titles.get(level, '')}
    for level in set(by_guid.values()) - set(out):
        out[level] = {'campaign': CUSTOM_MAPS, 'order': len(campaigns), 'index': 0,
                      'title': titles.get(level, '')}
    return out


def file_levels(levels):
    """`levels` ([{name, title}]) each given `campaign`, `order` and `index` (a level no
    table knows goes under CUSTOM_MAPS), a missing title from its mission, sorted for the
    picker."""
    try:
        known = groups()
    except (Exception, SystemExit):                  # noqa: BLE001
        known = {}
    last = 1 + max([g['order'] for g in known.values()] or [0])
    out = []
    for lv in levels:
        g = known.get(lv['name']) or {'campaign': CUSTOM_MAPS, 'order': last, 'index': 0,
                                      'title': ''}
        if g['campaign'] == CUSTOM_MAPS:
            g = dict(g, order=last)
        out.append(dict(lv, title=lv.get('title') or g['title'], campaign=g['campaign'],
                        order=g['order'], index=g['index']))
    out.sort(key=lambda lv: (lv['order'], lv['index'], lv['name']))
    return out


def main():
    """Print each level the Map Editor lists with the campaign it is filed under."""
    from riftlord.mapeditor import leveldata
    for lv in leveldata.levels():
        print('%-28s %-34s %s' % (lv['campaign'], lv['name'], lv['title']))


if __name__ == '__main__':
    main()
