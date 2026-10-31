"""Game data shaped for the editor UI: maps, minimaps, doors and units.

A thin projection over the readers in the converter proper -- `mapdata` for the
minimap and its world-to-texture transform, `onslaught` for the actors. Nothing
here parses assets itself; it turns what those return into the flat, JSON-safe
records the form and the map panel consume.
"""

import base64
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

from riftlord.core import gamepath  # noqa: E402
from riftlord.build import makelevel  # noqa: E402
from riftlord.build.minimap import mapdata  # noqa: E402
from riftlord.build.navigation import navpatch  # noqa: E402
from riftlord.onslaught import onslaught  # noqa: E402

#: Campaign roots in the order the game presents them, for grouping the picker.
CAMPAIGNS = (
    ('/Game/Maps/Campaign/', 'Old Friends'),
    ('/Game/Maps/CampaignDLC/', 'Drastic Steps'),
    ('/Game/Maps/CampaignDLC_Ice/', 'Cold as Eyes'),
    ('/Game/Maps/CampaignDLC_Sand/', 'Tipping the Scales'),
    ('/Game/Maps/OtherScenarios/', 'Other'),
    # Missions installed in ~mods, which the editor reads out of their own paks.
    ('/Game/Mods/', 'Installed mods'),
)

#: How a known action name reads, and what sort of thing it is. An action the
#: coordinator does not already fire cannot simply be added: the handler and its
#: multicast live in the Blueprint, so only the names a map already uses work.
ACTION_LABELS = {
    'SpawnGhorbash': ('Spawn Ghorbash', 'boss'),
    'SpawnCragg': ('Spawn Cragg', 'boss'),
    'SpawnZelzadore': ('Spawn Zelzadore', 'boss'),
    'SpawnMongrifel': ('Spawn Mongrifel (Kobold King)', 'boss'),
    'BattleMusicChange': ('Change battle music', 'music'),
}

#: Every action name in the shipped coordinators, for reference.
ACTIONS = [{'name': n, 'label': l, 'kind': k}
           for n, (l, k) in ACTION_LABELS.items()]


def _action_label(name):
    if name in ACTION_LABELS:
        return ACTION_LABELS[name]
    if name.startswith('OpenRedRift_'):
        return ('Open red rift %d' % int(name.rsplit('_', 1)[1]), 'rift')
    if name.startswith('CloseRedRift_'):
        return ('Close red rift %d' % int(name.rsplit('_', 1)[1]), 'rift')
    return (_label(name), 'other')


def coordinator_actions(map_name):
    """Action names the map's coordinator actually fires.

    Only 19 of the 49 coordinators use any, and each uses its own: Scenario_09
    spawns Ghorbash, Scenario_18 opens the eleven red rifts, most of the rest
    only change music. The handler is a Blueprint event, so an action the
    coordinator does not already fire cannot be offered.
    """
    from riftlord.onslaught import bppatch
    from riftlord.formats.ue.kismet import find_bytecode
    _path, object_name = makelevel.coordinator_of(map_name)
    pkg, names, resolve = bppatch.load(object_name)
    export = next(x for x, n in bppatch.functions(pkg, names, resolve)
                  if n.startswith('ExecuteUbergraph'))
    _o, _m, _s, w = find_bytecode(export['data'], names, resolve)
    out = set()
    for node in w.nodes:
        for call in bppatch.calls_named(node, 'OnslaughtAction', resolve):
            for n in bppatch._flat(call):
                if n.name in ('StringConst', 'UnicodeStringConst'):
                    out.update(v for _k, v in n.ops if isinstance(v, str))
    return sorted(out)

_CACHE = {}


def _mod_title(name):
    """A readable name for an installed mod's map: `MyMod_Map01` -> `MyMod Map01`."""
    return name.replace('_', ' ')


#: {pak path: (mtime, {map name: mission name})}, so a picker that is rebuilt on every
#: request does not reparse every mod's tables each time.
_TITLES = {}


def _titles_in_pak(pak):
    """{map name: mission name} for the missions one pak defines privately."""
    import struct

    from riftlord.build import missionproto
    from riftlord.build.reconcile import readable

    out = {}
    for key, (_p, entry) in navpatch.pak_names(pak).items():
        if not key.endswith('_MissionProtos.uasset'):
            continue
        if key == '/Game/Data/Campaign/MissionProtos.uasset':
            continue                             # the shipped table, read elsewhere
        body = entry.rsplit('.', 1)[0] + '.uexp'
        try:
            table = missionproto.Table(missionproto._loaded(
                navpatch.read_from_pak(pak, entry),
                navpatch.read_from_pak(pak, body)))
        except (Exception, SystemExit):                        # noqa: BLE001
            continue
        for row in table.rows:
            field = table.field(row, 'MissionLevel')
            title = readable(table.field(row, 'MissionName'))
            if field is None or not title or len(field.payload) < 8:
                continue
            # MissionLevel is a SoftObjectProperty: its payload is an FName index into
            # this package's name table, never the path as text, so the map it names has
            # to be looked up rather than read off.
            at = struct.unpack_from('<i', bytes(field.payload), 0)[0]
            if not 0 <= at < len(table.names):
                continue
            asset = table.names[at].split('.')[0].rsplit('/', 1)[-1]
            # An Endless row repeats its mission's name, so the first row wins and a
            # map keeps the name of the mission proper.
            if asset:
                out.setdefault(asset, title)
    return out


def live_mission_names():
    """Mission names out of the MissionProtos the GAME would load.

    Overlaid on the shipped table rather than replacing it: a mod that adds rows to the
    shared table -- Endless War adds thirty -- is invisible in the base paks, while a mod
    whose copy of that table is behind the DLC would otherwise take names away.
    """
    idx = navpatch.live_index()
    key = '/Game/Data/Campaign/MissionProtos.uexp'
    if key not in idx:
        return {}
    pak, entry = idx[key]
    try:
        return mapdata.mission_names_in(navpatch.read_from_pak(pak, entry))
    except (Exception, SystemExit):                            # noqa: BLE001
        return {}


def map_data_for(name):
    """The MapData package that draws this map's minimap, or None.

    A map copied by Rift Lord has no MapData of its own -- that asset holds the minimap
    image, and a copy has no image under its own name -- so its mission row points at the
    source map's. Following the row is the only way to find it; deriving the name from the
    map gives `<copy>_MapData`, which does not exist, and the editor then reported a map
    with a perfectly good minimap as having none.
    """
    import glob
    import struct

    from riftlord.build import missionproto

    sibling = mapdata.map_path(name) + '_MapData'
    try:
        idx = navpatch.live_index()
    except (Exception, SystemExit):                                # noqa: BLE001
        idx = {}
    if sibling + '.uasset' in idx:
        return sibling
    want = navpatch.maps().get(name)
    if not want:
        return None
    paks = sorted(glob.glob(os.path.join(navpatch.mods_dir(), '*.pak')))
    for pak in paks + navpatch.opened_paks():
        try:
            entries = navpatch.pak_names(pak)
        except (Exception, SystemExit):                            # noqa: BLE001
            continue
        for key, (_p, entry) in entries.items():
            if not key.endswith('_MissionProtos.uasset'):
                continue
            try:
                table = missionproto.Table(missionproto._loaded(
                    navpatch.read_from_pak(pak, entry),
                    navpatch.read_from_pak(pak, entry.rsplit('.', 1)[0] + '.uexp')))
            except (Exception, SystemExit):                        # noqa: BLE001
                continue

            def named(row, field, table=table):
                got = table.field(row, field)
                if got is None or len(got.payload) < 4:
                    return ''
                at = struct.unpack_from('<i', bytes(got.payload), 0)[0]
                return table.names[at] if 0 <= at < len(table.names) else ''

            for row in table.rows:
                if named(row, 'MissionLevel').split('.')[0] != want:
                    continue
                md = named(row, 'MapData').split('.')[0]
                if md:
                    return md
    return level_map_data(name, idx)


#: The class of the MapData object a level imports.
MAP_DATA_CLASS = 'OMDMapData'


def level_map_data(name, idx=None):
    """The MapData package the level itself imports, or None.

    A level's world settings import the MapData its HUD draws, so a mod level built on
    shipped data (Endless War's `Scenario_V0` imports `Scenario_01_MapData`) names it in
    its own import table. Cached in `_CACHE` under `('mapdata', name)`.
    """
    import struct

    from riftlord.formats.ue import uassetio

    key = ('mapdata', name)
    if key in _CACHE:
        return _CACHE[key]
    found = None
    try:
        idx = idx if idx is not None else navpatch.live_index()
        hit = idx.get(navpatch.maps()[name] + '.umap')
        pkg = uassetio.Package(navpatch.read_from_pak(*hit)) if hit else None
    except (Exception, SystemExit):                                # noqa: BLE001
        pkg = None
    if pkg is not None:
        def field(entry, at):
            i = struct.unpack_from('<i', entry, at)[0]
            return pkg.names[i][0] if 0 <= i < len(pkg.names) else ''
        for entry in pkg.imports:
            if field(entry, 8) != MAP_DATA_CLASS:
                continue
            outer = struct.unpack_from('<i', entry, 16)[0]
            if outer >= 0 or -outer - 1 >= len(pkg.imports):
                continue
            path = field(pkg.imports[-outer - 1], 20)
            if path + '.uasset' in idx:
                found = path
                break
    _CACHE[key] = found
    return found


def mod_mission_names():
    """{map name: mission name} for missions that mods define in their own tables.

    A mod's mission row is not in the shipped MissionProtos -- it sits in a private
    `<Mod>_MissionProtos` that the patched ProtoManager loads -- so `mission_names()`
    never sees it and a custom mission reads as its asset name, `RiftLordFoo Map01`
    instead of the name its author gave it.
    """
    import glob
    out = {}
    paks = sorted(glob.glob(os.path.join(navpatch.mods_dir(), '*.pak')))
    for pak in paks + navpatch.opened_paks():
        try:
            stamp = os.path.getmtime(pak)
        except OSError:
            continue
        hit = _TITLES.get(pak)
        if not hit or hit[0] != stamp:
            try:
                hit = (stamp, _titles_in_pak(pak))
            except (Exception, SystemExit):                    # noqa: BLE001
                hit = (stamp, {})
            _TITLES[pak] = hit
        out.update(hit[1])
    return out


def _campaign_of(path):
    for root, label in CAMPAIGNS:
        if path.startswith(root):
            return label
    return 'Other'


def maps():
    """[{name, title, campaign, installed, pak}] for the map picker.

    Installed mods are listed beside the shipped campaigns: the editor reads a map out
    of whichever pak holds it, so a custom mission is no different to work on. A pak
    opened by name gets a group of its own, named after the file, so it is obvious which
    entries came from it.
    """
    titles = mapdata.mission_names()
    titles.update(live_mission_names())       # rows a mod added to the shared table
    titles.update(mod_mission_names())        # and the ones it keeps in its own
    mods = set(navpatch.mod_maps())
    #: Narrower than `installed`: an onslaught can only be bundled into a pak under
    #: /Game/Mods/, and `mod_maps` also reports Dungeons, which is base-game content
    #: living in /Game/Maps/CustomMaps/.
    from riftlord.onslaught import wavespec
    bundleable = wavespec.bundleable_maps()
    opened = navpatch.opened_maps()
    out = []
    for name, path in navpatch.maps().items():
        pak = opened.get(name)
        # Grouped by where it came FROM, not by where its assets sit: Endless War adds
        # thirty maps under the base game's own campaign folder, and listing those inside
        # Old Friends beside the eighteen shipped ones says they are something they
        # are not.
        if pak and _in_missions(pak):
            group = MISSIONS_GROUP
        elif pak:
            group = OPENED_GROUP % os.path.basename(pak)
        elif name in mods:
            group = MOD_GROUP
        else:
            group = _campaign_of(path)
        out.append({'name': name,
                    'title': titles.get(name) or _mod_title(name),
                    'campaign': group,
                    'installed': name in mods,
                    'bundle': name in bundleable,
                    'pak': os.path.basename(pak) if pak else None})
    # A pak just opened first, then what is installed, then the shipped campaigns. The
    # mod groups used to sort last, which in a native dropdown of sixty-odd entries means
    # scrolling to find them -- and a group you have to go looking for is one a person
    # reasonably reports as not being there.
    order = [label for _r, label in CAMPAIGNS]

    def rank(m):
        if m['pak']:
            return -2
        if m['campaign'] == MOD_GROUP:
            return -1
        return order.index(m['campaign']) if m['campaign'] in order else 99

    out.sort(key=lambda m: (rank(m), m['campaign'], m['name']))
    return out


#: How a group of maps from an explicitly opened pak is titled in the picker.
OPENED_GROUP = 'Opened: %s'

#: The group for maps of missions in Rift Lord's Missions folder.
MISSIONS_GROUP = 'Custom Missions'


def _in_missions(pak):
    """True for a build in Rift Lord's Missions folder while Rift Lord runs."""
    from riftlord.missions import bucket
    if not bucket.ACTIVE:
        return False
    return os.path.dirname(os.path.abspath(pak).rstrip('/\\')) == os.path.abspath(bucket.root())

#: The group every map a mod adds is listed under, whatever folder its assets live in.
MOD_GROUP = dict(CAMPAIGNS)['/Game/Mods/']


def open_pak(path):
    """Read a pak from anywhere on disk and return the maps it brought in.

    The picker otherwise only reaches paks already sitting in `~mods`, so a mission that
    has been built but not installed -- or one somebody sent over -- could not be opened
    at all.
    """
    if not path:
        raise SystemExit('no pak given')
    full = os.path.abspath(os.path.expanduser(path))
    found = navpatch.open_pak(full)
    _CACHE.clear()
    every = maps()
    return {'pak': os.path.basename(full), 'path': full,
            'maps': [m for m in every if m['name'] in found]}


def rescan():
    """Forget every cached map, title and minimap and read the paks again (inside Rift
    Lord, the Missions folder too). Returns the fresh map list."""
    from riftlord.missions import bucket
    navpatch._MAPS = None
    _CACHE.clear()
    _TITLES.clear()
    if bucket.ACTIVE:
        bucket.refresh()
    return maps()


def close_pak(path=None):
    """Forget an opened pak, so its maps leave the picker."""
    navpatch.close_pak(os.path.abspath(os.path.expanduser(path)) if path else None,
                       every=True)
    _CACHE.clear()
    return {'opened': [os.path.basename(p) for p in navpatch.opened_paks()]}


#: Where a pak chosen through the file dialog is kept. A browser hands over the bytes and
#: a bare name, never a path, so the file has to be written somewhere the readers can
#: open it -- they work on a pak in place, not on a stream.
UPLOAD_DIR = os.path.join(gamepath.beside_code(gamepath.CODE_ROOT, 'cache'), 'opened-paks')


def save_pak(name, data):
    """Write a pak the author picked, and return where it went.

    Named after what they chose so the picker's listing reads like their own file. A
    second pick of the same file overwrites it rather than piling up copies.
    """
    leaf = os.path.basename(str(name or 'chosen.pak').replace('\\', '/'))
    leaf = ''.join(c if (c.isalnum() or c in '._- ') else '_' for c in leaf)
    if not leaf.lower().endswith('.pak'):
        leaf += '.pak'
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    full = os.path.join(UPLOAD_DIR, leaf)
    with open(full, 'wb') as fh:
        fh.write(data)
    return full


def open_uploaded(name, data):
    """Save a picked pak and open it. Same answer as `open_pak`."""
    if not data:
        raise SystemExit('that file is empty')
    return open_pak(save_pak(name, data))


def pak_places():
    """Folders worth starting the pak browser in, those that exist.

    `~mods` first: a pak is usually either installed there already or about to be.
    """
    paks = os.path.dirname(navpatch.PAK_DIR.rstrip(os.sep)) if navpatch.PAK_DIR else ''
    home = os.path.expanduser('~')
    want = [(os.path.join(navpatch.PAK_DIR, '~mods'), 'Installed mods'),
            (navpatch.PAK_DIR, 'Game paks'),
            (gamepath.scratch('emit-out', gamepath.CODE_ROOT), 'Built here'),
            (os.path.join(home, 'Downloads'), 'Downloads'),
            (home, 'Home')]
    out, seen = [], set()
    for path, label in want:
        if not path or path in seen or not os.path.isdir(path):
            continue
        seen.add(path)
        out.append({'path': path, 'label': label})
    return out


def browse(where=None):
    """What a folder holds, narrowed to subfolders and paks.

    The browser is server-side because the page cannot see a real path: a file input
    hands over the bytes and a made-up name, and the reader needs the file where it is.
    """
    places = pak_places()
    start = where or (places[0]['path'] if places else os.path.expanduser('~'))
    here = os.path.abspath(os.path.expanduser(start))
    if not os.path.isdir(here):
        here = os.path.expanduser('~')
    dirs, paks = [], []
    try:
        entries = sorted(os.scandir(here), key=lambda e: e.name.lower())
    except OSError as exc:
        return {'dir': here, 'error': str(exc), 'dirs': [], 'paks': [],
                'places': places, 'up': os.path.dirname(here) or None}
    for entry in entries:
        if entry.name.startswith('.') and entry.name != '.':
            continue
        try:
            if entry.is_dir():
                dirs.append({'name': entry.name, 'path': entry.path})
            elif entry.name.lower().endswith('.pak'):
                paks.append({'name': entry.name, 'path': entry.path,
                             'size': entry.stat().st_size})
        except OSError:
            continue
    parent = os.path.dirname(here)
    return {'dir': here, 'up': parent if parent and parent != here else None,
            'dirs': dirs, 'paks': paks, 'places': places,
            'opened': [{'name': os.path.basename(p), 'path': p}
                       for p in navpatch.opened_paks()]}


#: Proto name prefixes, longest first so ElementalFire loses to Elemental only
#: after the more specific families have had their turn.
FAMILIES = ('Cyclops', 'Elemental', 'FireFiend', 'Gnoll', 'Hellbat', 'Kobold',
            'Lizardman', 'Ogre', 'Orc', 'Troll', 'Wraith')

#: Protos the campaign spawns through a named action instead of a spawner. They
#: still work as ordinary spawns, so they stay selectable.
BOSS_UNITS = ('OrcBoss', 'WraithBoss', 'HellbatBoss', 'KoboldKing')


def _label(name):
    """CamelCase proto name as words: OrcArcherTNT -> Orc Archer TNT."""
    out, run = [], ''
    for ch in name.replace('_', ' '):
        if ch.isupper() and run and not run[-1].isupper():
            out.append(run)
            run = ch
        else:
            run += ch
    out.append(run)
    return ' '.join(w.strip() for w in out if w.strip())


def units():
    """[{name, label, family, boss, flyer, scramble, guid}] -- every proto.

    `label` is the proto's own `DisplayName` -- what a player sees, which is
    often nothing like the row name: `HellbatBoss` is Chromatica, `OgreBasher`
    is Ghorbash, `Kobold` is a Kobold Runner. `flyer` and `boss` likewise come
    from `bIsFlyer` and `bIsBoss` rather than from the name, so a new flyer that
    is not a Hellbat still reads correctly.
    """
    flags = onslaught.proto_flags()
    from riftlord.missions import modminions
    try:
        mods = modminions.mods()
    except (Exception, SystemExit):                  # noqa: BLE001
        mods = {}
    out = []
    for n, g in sorted(onslaught.protos().items()):
        f = flags.get(n, {})
        #: a mod's minions are grouped under their Minions.yaml Family, else the mod's name
        mod = f.get('mod')
        if mod:
            row = ((mods.get(mod) or {}).get('manifest') or {}).get(n) or {}
            family = row.get('Family') or (mods[mod]['label'] if mod in mods else mod)
        else:
            family = next((fam for fam in FAMILIES if n.startswith(fam)), 'Other')
        out.append({'name': n, 'label': f.get('name') or _label(n),
                    'family': family,
                    'mod': mod,
                    'boss': bool(f.get('boss')),
                    'flyer': bool(f.get('flyer')),
                    'scramble': n.endswith('_Scramble'),
                    'guid': list(g)})
    return out


def _route_edges(points, start_sub, limit=200):
    """[(from export, to export, weight)] for every branch a spawn can take.

    Every fork is followed, not just the heaviest: BranchWeight is a probability
    and both ways see traffic, so a single chain would draw a route the enemies
    only sometimes use.
    """
    by_index = {}
    for export, rec in points.items():
        if rec.get('index') is not None:
            by_index.setdefault(rec['index'], export)
    # StartingPathPoint is a sub-path, "PersistentLevel.OMDPathPoint3"
    start = start_sub.split('.')[-1] if start_sub else None
    if start not in points:
        return []
    edges, seen, queue = [], {start}, [start]
    while queue and len(seen) <= limit:
        here = queue.pop(0)
        rec = points[here]
        if rec.get('exit'):
            continue
        for br in rec.get('next') or []:
            nxt = by_index.get(br.get('PointIndex'))
            if nxt is None or nxt not in points:
                continue
            edges.append((here, nxt, br.get('BranchWeight', 1.0)))
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return edges


def capabilities(map_name):
    """What the map's coordinator can be made to do.

    `waves` is what it ships with; more can be authored, and fewer.

    `unitStyle` is how the coordinator names its enemies. Most paste the proto
    into each spawn call, but Scenario_Ice_01 declares one Blueprint variable
    per enemy and references it from all 188 calls -- an authoring convenience,
    not a defect. Such a map can still be authored, by pointing each call at a
    variable and rewriting spare variables' defaults, but it can field only as
    many DISTINCT units as it declares variables.
    """
    from riftlord.onslaught import wavespec
    _path, coord = makelevel.coordinator_of(map_name)
    # a Map Editor mission saves from its source level's coordinator (wavespec.bundle)
    source = wavespec.source_coordinator(map_name) or coord
    try:
        sh = wavespec.shape(source)
    except (Exception, SystemExit) as exc:                # noqa: BLE001
        return {'authorable': False, 'reason': str(exc), 'waves': 0,
                'coordinator': coord}
    editable = bool(sh['spawners'])
    return {
        'authorable': editable,
        'reason': ('' if editable else
                   'This map\'s onslaught sends no enemies at all, so there is no '
                   'spawn to copy and its waves cannot be edited. Edit the waves of '
                   'the level it was made from instead.'),
        'coordinator': coord,
        'waves': len(sh['waves']),
        'spawners': sh['spawners'],
        'breaks': {str(k): v for k, v in sh['breaks'].items()},
        'unitStyle': sh.get('unit_style', 'literal'),
        'maxUnits': sh.get('max_units'),
        'entitlement': entitlement_of(map_name),
    }


#: What each entitlement row is called in the store.
ENTITLEMENT_LABELS = {'DLC3': 'Drastic Steps', 'DLC3_Bundle': 'Drastic Steps',
                      'DLC4': 'Cold as Eyes', 'DLC5': 'Tipping The Scales'}


def coordinator_settings(map_name):
    """{'values': {setting: the source coordinator's own value}, 'fields': [{key, kind,
    min, max}]} for the Timeline's Mission Settings (wavespec.SETTINGS), or None."""
    from riftlord.onslaught import bppatch
    from riftlord.onslaught import wavespec
    _path, coord = makelevel.coordinator_of(map_name)
    source = wavespec.source_coordinator(map_name) or coord
    try:
        pkg, names, resolve = bppatch.load(source)
        cdo_at, _cdo = wavespec.class_default(pkg, names, resolve)
        if cdo_at is None:
            return None
        values = wavespec.coordinator_settings(pkg, names, cdo_at)
    except (Exception, SystemExit):                  # noqa: BLE001
        return None
    return {'values': values,
            'fields': [{'key': k, 'kind': 'int' if kind == 'IntProperty' else 'float',
                        'min': lo, 'max': hi}
                       for k, (_p, kind, _d, lo, hi) in wavespec.SETTINGS.items()]}


def entitlement_of(map_name):
    """{rows, label} for the DLC a map belongs to, or None for a base map."""
    from riftlord.build import makemod
    rows = makemod.entitlements_for(map_name)
    if not rows:
        return None
    return {'rows': rows,
            'label': ENTITLEMENT_LABELS.get(rows[0], rows[0])}


def install_status():
    """Where the game is, how it was found, and which build is asking."""
    from . import __version__
    d = gamepath.describe()
    return {'install': d['install'], 'found': bool(d['install']),
            'version': __version__,
            'frozen': gamepath.frozen(),
            'besideUs': d['beside_us'], 'saved': d['saved'],
            'settingsFile': d['settings_file'],
            'libraries': d['steam_libraries']}


def set_install(path):
    """Point the tools at an install and remember it. Returns the status."""
    path = (path or '').strip()
    if not gamepath.is_install(path):
        return {'ok': False,
                'error': '%s is not an Orcs Must Die 3 install -- it needs an '
                         'OMD/Content/Paks folder with .pak files in it'
                         % (path or '(nothing)')}
    gamepath.save_install(path)
    navpatch.PAK_DIR = gamepath.paks(path)
    navpatch._MAPS = None                     # the map list was for the old one
    _CACHE.clear()
    return {'ok': True, **install_status()}


def campaign_names():
    """Campaign row -> the name the game shows, from its own CampaignName."""
    from riftlord.missions import customcampaign
    return customcampaign.shipped_names()


def campaigns(mission=None):
    """[{name, label, kind, missions, size, first, index}] -- where a mission can be put:
    no campaign (a custom map, the default), a new or installed custom campaign, or a
    shipped campaign, with each one's Campaign Index range for Missions' `mission`
    (bucket.choices_for)."""
    from riftlord.missions import bucket
    return bucket.choices_for(mission or None)


def _parked(location):
    """True for an actor the Map Editor removed (riftlord.planactors.PARKED)."""
    from riftlord.mapeditor.planactors import PARKED
    return bool(location) and abs(float(location[2]) - PARKED[2]) < 1.0


def _map_par(name):
    from riftlord.onslaught import wavespec
    return wavespec.map_par(name)


def map_detail(name):
    """Everything the editor needs about one map.

    Doors carry the spawners that feed them, because a door is what the UI
    tabs on while a spawner is what the coordinator addresses. World positions
    come through as texture uv against the chosen minimap volume, so the panel
    can draw without knowing the transform.

    `mode` is the mission's game mode (gamemode.of_map), read again on every call.
    """
    from riftlord.onslaught import gamemode
    if name not in _CACHE:
        _CACHE[name] = _map_detail(name)
    out = dict(_CACHE[name])
    pieces = out.pop('pieces')
    out['mode'] = gamemode.of_map(name, pieces)
    #: the Crystal's groups as the Onslaught Editor edits them, from the plan or the level
    out['riftTear'] = gamemode.tear_of_map(name, pieces)
    return out


def _map_detail(name):
    """map_detail without `mode`; `pieces` are the game-type pieces its level places."""
    report = onslaught.map_report(name)
    md = (mapdata.read(name, data_path=map_data_for(name))
          or {'rifts': [], 'doors': [], 'volumes': []})
    volumes = md.get('volumes') or []

    # the floor the panel opens on: the one holding the rift
    rifts = md.get('rifts') or []
    vi = mapdata.pick_volume(volumes, rifts[0]) if (volumes and rifts) else 0
    project = mapdata.projector(volumes[vi]) if volumes else None
    projectors = [mapdata.projector(v) for v in volumes]

    def uv(loc):
        """A world point as texture uv on the opening floor, with `f` (the volume holding
        it, mapdata.pick_volume) and, on a map of several floors, `p` (its uv on each
        volume, by volume index)."""
        if not loc or project is None:
            return None
        u, v = project(loc)
        out = {'u': round(u, 5), 'v': round(v, 5), 'z': round(loc[2], 1)}
        if len(volumes) > 1:
            out['f'] = mapdata.pick_volume(volumes, loc)
            out['p'] = [[round(c, 5) for c in pr(loc)] if pr else [out['u'], out['v']]
                        for pr in projectors]
        return out

    # A spawner points at a walking start point, a flyer start point, or both,
    # and that decides what can come through it. Measured over the shipped
    # coordinators the rule has no exceptions: a flyer-only spawner never sends
    # a ground unit, and a walk-only spawner never sends a flyer.
    pathing = {}
    for sp in report['spawners']:
        walk, fly = bool(sp.get('start')), bool(sp.get('flyer'))
        pathing[sp['index']] = ('both' if (walk and fly) else
                                'walk' if walk else 'fly' if fly else 'none')

    doors = []
    for export, rec in sorted(report['doors'].items()):
        doors.append({'export': export, 'at': uv(rec.get('location')),
                      'spawners': []})
    by_export = {d['export']: d for d in doors}

    spawners = []
    for sp in sorted(report['spawners'], key=lambda s: s['index']):
        # a spawner the Map Editor removed is parked, and has no waves to edit
        if _parked(sp.get('location')):
            continue
        # StartingDoor is a sub-path; the doors are keyed by bare export name
        door = (sp.get('door') or '').split('.')[-1] or None
        pts = report['points']
        # BOTH starts. A spawner carries a walking route and a flyer one and may have
        # either or both, so tracing only `StartingPathPoint` drew nothing at all for a
        # flyer-only spawner -- which is every spawner Rift Lord adds for Hellbats.
        route = []
        for start, kind in ((sp.get('start'), 'walk'), (sp.get('flyer'), 'fly')):
            if not start:
                continue
            # the route begins at the spawner, then runs through its start point
            first = pts.get(start.split('.')[-1])
            ps, pf = uv(sp.get('location')), uv((first or {}).get('location'))
            if ps and pf:
                route.append({'from': ps, 'to': pf, 'weight': 1.0, 'kind': kind})
            for a, b, weight in _route_edges(pts, start):
                pa, pb = uv(pts[a].get('location')), uv(pts[b].get('location'))
                if pa and pb:
                    route.append({'from': pa, 'to': pb, 'weight': weight,
                                  'kind': kind})
        rec = {'index': sp['index'], 'export': sp['export'],
               'cls': sp.get('cls'),
               'door': door, 'at': uv(sp.get('location')),
               'maxRow': sp.get('maxRow'), 'route': route,
               'walks': bool(sp.get('start')), 'flies': bool(sp.get('flyer')),
               'pathing': pathing.get(sp['index'], 'none')}
        spawners.append(rec)
        if door in by_export:
            by_export[door]['spawners'].append(sp['index'])

    # A spawner with no door of its own still needs a tab, so it stands as its own
    # entry -- and what KIND of entry depends on the spawner's class, not on the
    # missing door. `BP_OMDAISpawner_VorwickRift_C` is an unstable red rift, which
    # appears in Order Temple and nowhere else; all eleven of its doorless spawners are
    # that class. A plain `BP_OMDAISpawner_C` without a door is just a spawner with
    # nothing to break through, which no shipped map has and an authored one may.
    loose = [s for s in spawners if s['door'] not in by_export]
    for s in loose:
        red = 'VorwickRift' in str(s.get('cls') or '')
        doors.append({'export': None, 'at': s['at'], 'spawners': [s['index']],
                      'unstable': True, 'red': red})

    actions = []
    for nm in coordinator_actions(name):
        label, kind = _action_label(nm)
        actions.append({'name': nm, 'label': label, 'kind': kind})

    # a stable identity per tab. The coordinator addresses spawners by index,
    # so that is what the form carries; the label is the author's name for it.
    doors = [d for d in doors if d['spawners']]
    seq = {'door': 0, 'rift': 0, 'spawner': 0}
    labels = {'door': 'Door %d', 'rift': 'Unstable Rift %d',
              'spawner': 'Spawner %d, no door'}
    for d in doors:
        if not d.get('unstable'):
            kind = 'door'
        else:
            kind = 'rift' if d.get('red') else 'spawner'
        seq[kind] += 1
        d['kind'] = kind
        d['ordinal'] = seq[kind]
        d['key'] = '%s-%d' % (kind, min(d['spawners']))
        #: an Unstable Rift is numbered by its indexInLevel, as the Map Editor numbers it
        number = min(d['spawners']) if kind == 'rift' else seq[kind]
        d['short'] = str(number)
        d['label'] = labels[kind] % number
        kinds = {pathing.get(i, 'none') for i in d['spawners']}
        d['pathing'] = ('both' if kinds == {'both'} or len(kinds) > 1
                        else kinds.pop() if kinds else 'none')

    #: what the host can do for each mission event kind (riftlord.hostevents)
    try:
        from riftlord.missions import hostevents
        from riftlord.build.world import levelmusic
        caps = hostevents.capabilities(name)
        events = {'reactions': caps['reactions'], 'music': caps['music'],
                  'rift': caps['rift'],
                  'tracks': [{'key': c['key'], 'label': c['label']}
                             for c in levelmusic.choices()]}
    except (Exception, SystemExit):                  # noqa: BLE001
        events = None

    out = {'map': name,
           'title': mapdata.mission_names().get(name) or name,
           'actions': actions,
           'events': events,
           'settings': coordinator_settings(name),
           #: the mission's par time in seconds (wavespec.map_par), or None
           'par': _map_par(name),
           'bbox': content_bbox(name, vi) if volumes else None,
           'can': capabilities(name),
           'doors': doors,
           'spawners': spawners,
           'rifts': [uv(r) for r in rifts],
           'volume': vi, 'volumes': len(volumes),
           'floors': map_floors(name, volumes),
           'pieces': report.get('pieces') or []}
    return out


def map_floors(name, volumes):
    """The map's floors, lowest first: [{volume, bbox}], `volume` the MapVolumes index and
    `bbox` its drawing's extent (content_bbox). Empty for a map of one volume or none."""
    if len(volumes) < 2:
        return []
    order = sorted(range(len(volumes)),
                   key=lambda i: ((volumes[i].get('translation') or (0, 0, 0))[2], i))
    return [{'volume': i, 'bbox': content_bbox(name, i)} for i in order]


def content_bbox(name, volume=None):
    """The floor plan's extent within the texture, as uv {u0, v0, u1, v1}.

    A volume maps to the whole square texture while the drawing occupies only
    part of it, so framing on the texture leaves the map small and adrift. The
    alpha channel is the floor silhouette -- it never approaches 255, so it is
    normalised by its own maximum before thresholding.
    """
    md = mapdata.read(name, data_path=map_data_for(name))
    if not md or not md.get('volumes'):
        return None
    volumes = md['volumes']
    if volume is None:
        rifts = md.get('rifts') or []
        volume = mapdata.pick_volume(volumes, rifts[0]) if rifts else 0
    volume = max(0, min(volume, len(volumes) - 1))
    img = mapdata.minimap_image(volumes[volume])
    if img is None:
        return None
    alpha = img.getchannel('A')
    peak = alpha.getextrema()[1]
    if not peak:
        return None
    mask = alpha.point(lambda v: 255 if v > peak * 0.05 else 0)
    box = mask.getbbox()
    if not box:
        return None
    w, h = img.size
    return {'u0': box[0] / w, 'v0': box[1] / h,
            'u1': box[2] / w, 'v1': box[3] / h}


def minimap_png(name, volume=None):
    """The map's minimap as PNG bytes, or None if it has no MapData."""
    md = mapdata.read(name, data_path=map_data_for(name))
    if not md or not md.get('volumes'):
        return None
    volumes = md['volumes']
    if volume is None:
        rifts = md.get('rifts') or []
        volume = mapdata.pick_volume(volumes, rifts[0]) if rifts else 0
    volume = max(0, min(volume, len(volumes) - 1))
    img = mapdata.minimap_image(volumes[volume])
    if img is None:
        return None
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return buf.getvalue()


def minimap_data_uri(name, volume=None):
    png = minimap_png(name, volume)
    if png is None:
        return None
    return 'data:image/png;base64,' + base64.b64encode(png).decode('ascii')
