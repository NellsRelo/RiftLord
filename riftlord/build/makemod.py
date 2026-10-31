"""Build a mod pak that adds a custom mission without modifying any shipped table.

The mod ships its own MissionProtos and CampaignProtos -- cloned from the
shipped ones, stripped to its own rows and renamed -- plus a BP_OMDProtoManager
whose registry lists them. The stock tables are untouched, so a mod that patches
them (EndlessWar does) still works alongside this one.

    python3 makemod.py --name "My Custom Map" --level /Game/Maps/Custom/MyMap/MyMap \\
        --mod MyMod --out mymod.pak
"""

import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.build.minimap import mapdata  # noqa: E402
from riftlord.build import missionproto  # noqa: E402
from riftlord.build import modtable  # noqa: E402
from riftlord.build import protoreg  # noqa: E402
import re                                           # noqa: E402
from riftlord.formats.ue import pakv4  # noqa: E402
from riftlord.core import pkgcheck  # noqa: E402
from riftlord.formats.ue.uassetio import Package  # noqa: E402

#: The retired custom-content slots of OrcsMustUI's registry (slotreg.py): a pak built
#: before 2026-10-16 holds its tables at SLOT_ROOT/SlotNNN. Kept only so those paks still
#: read (customcampaign.installed, previous_guids, set_endless) until they are rebuilt;
#: no build writes there any more.
SLOT_ROOT = '/Game/CustomMaps'
SLOTS = 256
#: Where every mod's proto tables go: MOD_ROOT/<mod>/Tables/<mod>_<table>. OrcsMustUI's
#: ProtoRegistry finds every table directly in a Tables folder of any mod pak and adds it
#: to the game's registry as it starts -- no slots, no list, nothing beside the pak (the
#: contract with OrcsMustUI's owner, 2026-10-16). The only scheme.
TABLE_SCHEME = 'mod'
#: Kept for callers that name it; maps use the one scheme too.
MAP_TABLE_SCHEME = TABLE_SCHEME
MOD_ROOT = '/Game/Mods'
#: The retired list of a pak's tables that sat beside it; removed wherever found.
REGISTRY_SIDECAR = '.Registry.yaml'
#: The folder inside a mod's own folder that holds the files OrcsMustUI reads:
#: OMD/Content/Mods/<mod>/OrcsMustUI/<File> (OrcsMustUI Docs/Registry.md).
OMUI_DIR = 'OrcsMustUI'
#: A file OrcsMustUI reads out of a pak: its name, and the most it reads.
OMUI_NAME_RE = re.compile(r'^[A-Za-z0-9_]+\.(?:yaml|yml|json|png|jpg|jpeg)$')
OMUI_MAX_BYTES = 8 * 1024 * 1024
#: Cooked package files: a Blueprint OrcsMustUI loads (MinionInfo, HeroInfo) may sit in a
#: mod's OrcsMustUI folder; it is a package, not a file OrcsMustUI reads.
COOKED_RE = re.compile(r'\.(?:uasset|uexp|ubulk|uptnl|umap)$')
#: An OrcsMustUI file inside a pak: (mod key, file name), by inner or /Game path.
OMUI_FILE_RE = re.compile(r'/Mods/([^/]+)/' + OMUI_DIR
                          + r'/((?![^/]+\.(?:uasset|uexp|ubulk|uptnl|umap)$)[^/]+)$')
#: The files that used to sit beside a pak as `<stem><suffix>`, by the name each has
#: inside the pak now.
LOOSE_SIDECARS = {'.CustomMaps.yaml': 'CustomMaps.yaml', '.CustomMaps.json': 'CustomMaps.json',
                  '.Scramble.yaml': 'Scramble.yaml', '.Scramble.json': 'Scramble.json',
                  '.Heroes.yaml': 'Heroes.yaml', '.Achievements.yaml': 'Achievements.yaml',
                  '.Tab.png': 'Tab.png', '.Tab.jpg': 'Tab.jpg', '.Tab.jpeg': 'Tab.jpeg',
                  '.Card.png': 'Card.png', '.CardHovered.png': 'CardHovered.png'}
#: A build's mission table, in either scheme.
MISSION_TABLE_RE = r'/(?:CustomMaps/Slot(\d{3})/Slot\1_MissionProtos|Mods/([^/]+)/Tables/\2_MissionProtos)\.uasset$'
#: What the authoring tools call "no campaign: a custom map, in its slot".
CUSTOM_MAPS = 'CustomMaps'


def _table(slot, leaf):
    """A table's package path: in a mod's own folder, `slot` being the mod's key; or, to
    read a pak built before slots were retired, in numbered slot `slot`."""
    if isinstance(slot, str):
        #: `<mod>_<leaf>`: the endings the tools already recognise a table by
        return '%s/%s/Tables/%s_%s' % (MOD_ROOT, slot, slot, leaf)
    return '%s/Slot%03d/Slot%03d_%s' % (SLOT_ROOT, slot, slot, leaf)


def mission_table_key(files):
    """(inner path, slot) of a build's one mission table; slot is a number or a mod key."""
    found = [(k, re.search(MISSION_TABLE_RE, k)) for k in files]
    found = [(k, m) for k, m in found if m]
    if len(found) != 1:
        return None, None
    k, m = found[0]
    return k, (int(m.group(1)) if m.group(1) else m.group(2))


def is_table(path):
    """Whether a package path or pak entry is a mod's proto table, in either scheme."""
    return '/CustomMaps/Slot' in path or re.search(r'/Mods/[^/]+/Tables/', path) is not None


def row_prefix(slot):
    """What a mod's row names start with: `<mod>_` in its own table folder (row names
    must be unique across mods there), nothing in a numbered slot."""
    return '%s_' % slot if isinstance(slot, str) else ''


def registry_tables(names):
    """Object paths of the mod-folder tables among a pak's inner names: what OrcsMustUI's
    ProtoRegistry registers from it."""
    out = []
    for n in names:
        m = re.search(r'(?:^|/)Content(/Mods/[^/]+/Tables/([^/.]+))\.uasset$', n)
        if m:
            out.append('/Game%s.%s' % (m.group(1), m.group(2)))
    return sorted(out)


def pak_mod_key(names):
    """The one mod folder a pak's tables are in, or None (no table, or several folders).
    A mission that also carries other mods' tables (a kit, a mod's minions) is keyed by
    the folder holding its mission table."""
    keys = {m.group(1) for n in names for m in [MOD_TABLE_RE.search(n)] if m}
    if len(keys) > 1:
        keys = {m.group(1) for n in names for m in [MOD_TABLE_RE.search(n)]
                if m and m.group(2) == '%s_MissionProtos' % m.group(1)}
    return keys.pop() if len(keys) == 1 else None


def omui_inner(key, name):
    """Where OrcsMustUI file `name` of mod `key` sits inside a pak."""
    return 'OMD/Content/Mods/%s/%s/%s' % (key, OMUI_DIR, name)


def omui_files(names):
    """{file name: inner path} of the OrcsMustUI files among a pak's inner names."""
    out = {}
    for n in names:
        m = OMUI_FILE_RE.search(n)
        if m:
            out[m.group(2)] = n
    return out


def add_omui_file(files, key, name, data):
    """Put OrcsMustUI file `name` (`data`: bytes or text) in a pak's `files` under mod
    `key`'s folder; its inner path. Refuses a name or size OrcsMustUI would skip."""
    if not key:
        raise SystemExit('%s: the pak has no mod folder (no table in /Game/Mods/<mod>/Tables) '
                         'to put it in' % name)
    if not OMUI_NAME_RE.match(name):
        raise SystemExit('%s: OrcsMustUI reads only [A-Za-z0-9_] names ending .yaml, .yml, '
                         '.json, .png, .jpg or .jpeg' % name)
    if isinstance(data, str):
        data = data.encode('utf-8')
    if len(data) > OMUI_MAX_BYTES:
        raise SystemExit('%s: %d bytes; OrcsMustUI reads at most %d'
                         % (name, len(data), OMUI_MAX_BYTES))
    inner = omui_inner(key, name)
    files[inner] = bytes(data)
    return inner


def loose_sidecars(pak_path):
    """{inner file name: loose path} of the old sidecar files sitting beside a build."""
    base = pak_path.rstrip('/\\')
    base = base[:-4] if base.endswith('.pak') else base
    return {name: base + suffix for suffix, name in LOOSE_SIDECARS.items()
            if os.path.exists(base + suffix)}


def embed_loose_sidecars(pak_path, files, key=None):
    """Copy the old sidecar files beside build `pak_path` into `files`, under mod `key`
    (default: the pak's own mod folder), unless the pak already holds that file. A notes
    file's `Banner: <stem>.Tab.png` becomes `Banner: Tab.png`, the name it has beside the
    notes inside the pak. Returns the names embedded."""
    key = key or pak_mod_key(files)
    have = omui_files(files)
    stem = os.path.basename(pak_path.rstrip('/\\'))
    stem = stem[:-4] if stem.endswith('.pak') else stem
    done = []
    for name, path in sorted(loose_sidecars(pak_path).items()):
        if name in have:
            continue
        with open(path, 'rb') as fh:
            data = fh.read()
        if name.startswith('CustomMaps.'):
            for image in ('Tab.', 'Card.', 'CardHovered.'):
                data = data.replace((stem + '.' + image).encode('utf-8'), image.encode('utf-8'))
        add_omui_file(files, key, name, data)
        done.append(name)
    return done


def remove_loose_sidecars(pak_path):
    """Delete every old sidecar beside a pak or build: the retired Registry.yaml and the
    OrcsMustUI files that now live inside the pak. The paths removed."""
    base = pak_path.rstrip('/\\')
    base = base[:-4] if base.endswith('.pak') else base
    gone = []
    for suffix in (REGISTRY_SIDECAR,) + tuple(LOOSE_SIDECARS):
        if os.path.exists(base + suffix):
            os.remove(base + suffix)
            gone.append(base + suffix)
    return gone


def write_registry_sidecar(pak_path, _names=None):
    """Retired (2026-10-16): removes a leftover Registry.yaml beside the pak; None."""
    path = os.path.splitext(pak_path)[0] + REGISTRY_SIDECAR
    if os.path.exists(path):
        os.remove(path)
    return None


def omui_problems(files):
    """Problems with the OrcsMustUI files in a pak's `files`; [] when none."""
    bad = []
    keys = {m.group(1) for k in files for m in [MOD_TABLE_RE.search(k)] if m}
    for k in sorted(files):
        if k.endswith(REGISTRY_SIDECAR) or k.endswith('/Registry.yaml'):
            bad.append('%s: Registry.yaml is retired; tables are found by their folder' % k)
        m = OMUI_FILE_RE.search(k)
        if not m:
            if '/%s/' % OMUI_DIR in k and '/Mods/' in k and not COOKED_RE.search(k):
                bad.append('%s: OrcsMustUI reads files directly in Mods/<mod>/%s only'
                           % (k, OMUI_DIR))
            continue
        key, name = m.groups()
        # a Blueprint package OrcsMustUI loads by path (HeroInfo's <Row>_Info,
        # MinionInfo's <Row>_Minion), not a data file ProtoRegistry copies
        if name.endswith(('.uasset', '.uexp', '.ubulk')):
            continue
        blob = files[k]
        size = len(blob) if isinstance(blob, (bytes, bytearray)) else os.path.getsize(blob)
        if not OMUI_NAME_RE.match(name):
            bad.append('%s: not a name OrcsMustUI reads' % k)
        if size > OMUI_MAX_BYTES:
            bad.append('%s: %d bytes, over %d' % (k, size, OMUI_MAX_BYTES))
        if keys and key not in keys:
            bad.append('%s: in mod folder %s, but the tables are in %s'
                       % (k, key, ', '.join(sorted(keys))))
    return bad


#: A mod table inside a pak: (mod key, table leaf), by inner or /Game path.
MOD_TABLE_RE = re.compile(r'/Mods/([^/]+)/Tables/([^/.]+)\.uasset$')
#: A retired slot table inside a pak.
SLOT_TABLE_RE = re.compile(r'/CustomMaps/Slot(\d{3})/')


def _row_owned(key, name):
    """Whether row `name` in a table of mod `key` is named as the registry needs: the
    key itself or `<key>_...`; entitlement declarations (`<shipped row>__<unique>`) and
    the ENDLESS_EXTENSION row keep the names OrcsMustUI reads them by."""
    return (name == key or name.startswith(key + '_') or ENTITLEMENT_JOIN in name
            or name == ENDLESS_EXTENSION)


_SHIPPED_GUIDS = None


def shipped_proto_guids():
    """{16-byte Guid: 'Table:Row'} over every shipped proto table (read once)."""
    global _SHIPPED_GUIDS
    if _SHIPPED_GUIDS is None:
        from riftlord.core import gamefiles
        out = {}
        for key in gamefiles.index():
            if not key.endswith('.uasset') or '/Content/Data/' not in key or 'Proto' not in key:
                continue
            path = '/Game/' + key.split('/Content/', 1)[1][:-len('.uasset')]
            try:
                t = missionproto.read(path)
            except Exception:                        # noqa: BLE001
                continue
            for r in t.rows:
                f = t.field(r, 'Guid')
                if f is not None and f.kind == 'StructProperty':
                    out[bytes(f.payload)[:16]] = '%s:%s' % (path.rsplit('/', 1)[-1], r['name'])
        _SHIPPED_GUIDS = out
    return _SHIPPED_GUIDS


def _table_rows(ua, ux):
    """[(row name, 16-byte Guid or None)] of a cooked proto table."""
    pkg = Package(ua, ux)
    pkg.load_payloads()
    t = missionproto.Table(pkg)
    out = []
    for r in t.rows:
        f = t.field(r, 'Guid')
        out.append((r['name'], bytes(f.payload)[:16] if f is not None else None))
    return out


#: {installed_tables key: the pak's path}, from the last scan.
INSTALLED_PATHS = {}


def installed_tables(exclude=(), mods=None):
    """{pak file name: ({mod keys and Slot### its tables use}, {Guid: row})} for every
    installed pak in ~mods (subfolders too) with proto tables, other than the file names
    in `exclude`. Without `mods`, LogicMods is read too (keys 'LogicMods/<name>'):
    ProtoRegistry registers tables from both."""
    from riftlord.core import gamepath
    roots = [(mods, '')] if mods else [(gamepath.mods_dir(), '')]
    if not mods and gamepath.mod_paks():
        roots.append((os.path.join(gamepath.mod_paks(), 'LogicMods'), 'LogicMods/'))
    out = {}
    cache = _scan_cache()
    for root, prefix in roots:
        out.update(_installed_in(root, prefix, exclude, cache))
    _save_scan_cache(cache)
    return out


#: Version of the registry scan cache's entries; a change drops every entry.
SCAN_CACHE_VERSION = 1


def _scan_cache_path():
    from riftlord.core import gamepath
    return os.path.join(gamepath.scratch('registry-scan', beside=gamepath.CODE_ROOT),
                        'tables-v%d.json' % SCAN_CACHE_VERSION)


def _pak_stamp(path):
    """A pak's cache key: its absolute path, size, mtime, ctime and inode; None when it
    cannot be read."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return '%s|%d|%d|%d|%d' % (os.path.abspath(path), st.st_size, st.st_mtime_ns,
                               st.st_ctime_ns, st.st_ino)


def _scan_cache():
    """{pak stamp: [places, {Guid hex: row}]} of earlier scans, {} when there is none."""
    import json
    try:
        with open(_scan_cache_path(), encoding='utf-8') as fh:
            got = json.load(fh)
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_scan_cache(cache):
    """Keep `cache` on disk, without entries for paks gone or changed since."""
    import json
    keep = {k: v for k, v in cache.items() if _pak_stamp(k.split('|', 1)[0]) == k}
    path = _scan_cache_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        part = '%s.%d.part' % (path, os.getpid())
        with open(part, 'w', encoding='utf-8') as fh:
            json.dump(keep, fh)
        os.replace(part, path)
    except OSError:
        pass


def _installed_in(mods, prefix, exclude, cache=None):
    """installed_tables() of one folder. `cache` ({pak stamp: [places, {Guid hex: row}]})
    answers for a pak whose path, size, mtime, ctime and inode are unchanged, and takes
    the paks read."""
    from riftlord.build.navigation import navpatch
    out = {}
    if not mods or not os.path.isdir(mods):
        return out
    cache = {} if cache is None else cache
    for base, _dirs, names in os.walk(mods):
        for n in sorted(names):
            if not n.endswith('.pak') or n in exclude:
                continue
            p = os.path.join(base, n)
            stamp = _pak_stamp(p)
            hit = cache.get(stamp) if stamp else None
            if hit is not None:
                places = set(hit[0])
                guids = {bytes.fromhex(g): name for g, name in hit[1].items()}
            else:
                try:
                    index = navpatch.pak_names(p)
                except (Exception, SystemExit):      # noqa: BLE001
                    continue
                places, guids = _tables_in(p, index)
                if stamp and _pak_stamp(p) == stamp:
                    cache[stamp] = [sorted(places), {g.hex(): name for g, name in guids.items()}]
            if places:
                key = prefix + os.path.relpath(p, mods)
                out[key] = (places, guids)
                INSTALLED_PATHS[key] = p
    return out


def _tables_in(p, index):
    """({mod keys and Slot### the pak's tables use}, {Guid: row}) of one pak."""
    from riftlord.build.navigation import navpatch
    places, guids = set(), {}
    for key in index:
        m, slot = MOD_TABLE_RE.search(key), SLOT_TABLE_RE.search(key)
        if not (m or slot) or not key.endswith('.uasset'):
            continue
        places.add(m.group(1) if m else 'Slot' + slot.group(1))
        uexp = key[:-len('.uasset')] + '.uexp'
        if uexp not in index:
            continue
        try:
            rows = _table_rows(navpatch.read_from_pak(p, index[key][1]),
                               navpatch.read_from_pak(p, index[uexp][1]))
        except (Exception, SystemExit):              # noqa: BLE001
            continue
        for name, g in rows:
            if g is not None:
                guids[g] = name
    return places, guids


def _identical_tables(files, other, mods=None):
    """The table entries (`OMD/Content/.../X.uasset`) of `files` that installed pak
    `other` (an installed_tables key) holds byte for byte, .uexp too."""
    from riftlord.build.navigation import navpatch
    path = INSTALLED_PATHS.get(other) or os.path.join(mods or '', other)
    try:
        index = navpatch.pak_names(path)
    except (Exception, SystemExit):                  # noqa: BLE001
        return set()
    same = set()
    for k in files:
        if not MOD_TABLE_RE.search(k) or not k.endswith('.uasset'):
            continue
        uexp = k[:-len('.uasset')] + '.uexp'
        game = '/Game' + k[len('OMD/Content'):]
        if game not in index or game[:-len('.uasset')] + '.uexp' not in index:
            continue
        try:
            if navpatch.read_from_pak(path, index[game][1]) == files[k] and \
                    navpatch.read_from_pak(path, index[game[:-len('.uasset')] + '.uexp'][1]) \
                    == files.get(uexp):
                same.add(k)
        except (Exception, SystemExit):              # noqa: BLE001
            continue
    return same


def _folder_identical(files, folder, same):
    """Whether every table `files` holds in mod folder `folder` is in `same`."""
    tables = [t for t in files if t.endswith('.uasset') and MOD_TABLE_RE.search(t)
              and MOD_TABLE_RE.search(t).group(1) == folder]
    return bool(tables) and all(t in same for t in tables)


def _installed_key(other, mods=None):
    """The mod folder of installed pak `other`'s mission table (pak_mod_key), or None."""
    from riftlord.build.navigation import navpatch
    path = INSTALLED_PATHS.get(other) or os.path.join(mods or '', other)
    try:
        return pak_mod_key(navpatch.pak_names(path))
    except (Exception, SystemExit):                  # noqa: BLE001
        return None


def registry_problems(files, pak_name=None, installed=True, mods=None, others=None,
                      notes=None):
    """Problems that stop a pak's tables registering cleanly beside other mods; [] when
    none. `files` is {inner path: bytes}. The rules (OrcsMustUI Docs/Registry.md): no
    retired slot tables; each table at /Game/Mods/<key>/Tables/<key>_<leaf>; row names
    prefixed with the key; every row Guid its own -- not a shipped row's, not another
    row's in the pak and, with `installed`, not one in another installed pak, which also
    must not use the same mod folder. `pak_name` is this pak's file name in ~mods, which
    a reinstall replaces and so is not a clash. `others` is installed_tables() when the
    caller checks several paks against one scan. The OrcsMustUI files the pak holds are
    checked too (omui_problems). A mod folder that both this pak and an installed
    mission carry (neither's own: a mod's minions) is not a clash; when their copies
    differ, a line naming the mission to rebuild goes into `notes`."""
    bad = omui_problems(files)
    own = pak_mod_key(files)
    slots = sorted({m.group(1) for k in files for m in [SLOT_TABLE_RE.search(k)] if m})
    if slots:
        bad.append('retired slot tables (Slot%s): rebuild in the mod folder form'
                   % ', Slot'.join(slots))
    keys, seen, seen_at = set(), {}, {}
    shipped = shipped_proto_guids()
    for k in sorted(files):
        m = MOD_TABLE_RE.search(k)
        if not m:
            continue
        key, table = m.groups()
        keys.add(key)
        if not table.startswith(key + '_'):
            bad.append('%s: a table of mod %s must be named %s_<table>' % (table, key, key))
        uexp = k[:-len('.uasset')] + '.uexp'
        if uexp not in files:
            bad.append('%s has no .uexp' % table)
            continue
        try:
            rows = _table_rows(files[k], files[uexp])
        except (Exception, SystemExit) as exc:       # noqa: BLE001
            bad.append('%s does not read as a proto table: %s' % (table, exc))
            continue
        for name, g in rows:
            if not _row_owned(key, name):
                bad.append('%s: row %s is not prefixed %s_' % (table, name, key))
            if g is None:
                continue
            if g in shipped:
                bad.append('%s: row %s has the Guid of shipped %s' % (table, name, shipped[g]))
            if g in seen:
                bad.append('%s: row %s has the Guid of %s' % (table, name, seen[g]))
            seen[g] = '%s:%s' % (table, name)
            seen_at[g] = k
    if installed:
        others = installed_tables((), mods) if others is None else others
        for other, (places, guids) in sorted(others.items()):
            if os.path.basename(other) == pak_name:
                continue
            shared = sorted(keys & places)
            clashing = shared or any(g in guids for g in seen)
            #: a table the installed pak holds byte for byte is the same table shipped
            #: twice (a mission carrying a mod's kit), not a clash
            same = _identical_tables(files, other, mods) if clashing else set()
            other_own = _installed_key(other, mods) if clashing else None
            carried = {k for k in keys if k != own and other_own and k != other_own}
            stale = sorted(k for k in shared
                           if k in carried and not _folder_identical(files, k, same))
            if stale and notes is not None:
                notes.append('installed %s carries another copy of %s: rebuild it after '
                             'installing the mod' % (other, ', '.join(stale)))
            shared = [k for k in shared
                      if k not in carried and not _folder_identical(files, k, same)]
            if shared:
                bad.append('mod folder %s is also used by installed %s' % (', '.join(shared), other))
            for g, where in sorted(seen.items(), key=lambda kv: kv[1]):
                folder = MOD_TABLE_RE.search(seen_at[g]).group(1)
                if g in guids and seen_at[g] not in same and folder not in carried:
                    bad.append('%s has the Guid of row %s in installed %s' % (where, guids[g], other))
    return bad


#: A retired slot table's entry inside a pak: (prefix, slot, leaf).
SLOT_ENTRY_RE = re.compile(r'^(.*)/CustomMaps/Slot(\d{3})/Slot\2_([A-Za-z0-9]+)\.uasset$')


def move_slot_tables(files, mod):
    """(files, [(old path, new path)]): a pak built before slots were retired, with each
    slot table moved to mod `mod`'s own table folder (_table) and any row the registry
    would not accept as the mod's renamed `<mod>_<row>` (Guids kept, so nothing that
    names a row by Guid changes). Refused when anything else in the pak still names a
    slot path."""
    out, moved = {}, []
    for k, data in files.items():
        m = SLOT_ENTRY_RE.match(k)
        if not m:
            if not (SLOT_TABLE_RE.search(k) and k.endswith('.uexp')):
                out[k] = data
            continue
        prefix, number, leaf = m.groups()
        old, new = _table(int(number), leaf), _table(mod, leaf)
        pkg = Package(data, files[k[:-len('.uasset')] + '.uexp'])
        pkg.load_payloads()
        modtable.rename_package(pkg, old, old.rsplit('/', 1)[-1], new, new.rsplit('/', 1)[-1])
        t = missionproto.Table(pkg)
        for row in t.rows:
            if not _row_owned(mod, row['name']):
                row['name'] = '%s_%s' % (mod, row['name'])
                row['name_index'] = pkg.add_name(row['name'])
        inner = '%s/Mods/%s/Tables/%s' % (prefix, mod, new.rsplit('/', 1)[-1])
        out[inner + '.uasset'], out[inner + '.uexp'] = t.write()
        moved.append((old, new))
    left = [k for k, v in out.items() if b'/CustomMaps/Slot' in bytes(v)]
    if left:
        raise SystemExit('still naming a slot path: %s' % ', '.join(left[:3]))
    return out, moved


def slot_path(slot):
    """The mission table path of a mod (`slot` = slot_for(mod))."""
    return _table(slot, 'MissionProtos')


#: Custom hero skins: each hero's skin list in OrcsMustUI's registry names
#: SKINS_PER_HERO reserved skin Guids (skin_guid); a skin mod's row takes one,
#: in the mod's skin table (skin_slot_path).
SKINS_PER_HERO = 64
CHARACTER_TABLE = '/Game/Data/PlayerCharacterProtos'
SKIN_TABLE = '/Game/Data/PlayerSkinProtos'


def skin_slot_path(slot):
    """The skin table path of a mod: its custom hero skins."""
    return _table(slot, 'PlayerSkinProtos')


def skin_guid(hero_guid, index):
    """The 16-byte payload of a hero's reserved skin Guid number `index`:
    stable, so a skin mod built today keeps its place."""
    import hashlib
    return hashlib.md5(b'OrcsMustUI.skin.' + bytes(hero_guid) + b'.%d' % index).digest()


def campaign_slot_path(slot):
    """The campaign table path of a mod: its campaigns, each a hub tab."""
    return _table(slot, 'CampaignProtos')


#: Content tables per mod: its own proto tables of any row type (traps,
#: abilities, upgrades, status effects, enemies, modifiers...), one type each.
CONTENT_TABLES = 8


def content_slot_path(slot, index):
    """Content table `index` of a mod: any proto row type, one type per table."""
    if not 0 <= index < CONTENT_TABLES:
        raise SystemExit('a content table is 0 to %d' % (CONTENT_TABLES - 1))
    return _table(slot, 'Content%d' % index)


def slot_for(mod, scheme=None):
    """Where a mod's tables go: its own table folder, named by the mod's key. `scheme`
    is accepted for old callers; only TABLE_SCHEME exists."""
    if scheme not in (None, TABLE_SCHEME):
        raise SystemExit('table scheme %r is retired: every mod\'s tables go in '
                         '%s/<mod>/Tables' % (scheme, MOD_ROOT))
    return mod


MISSION_TEMPLATE = 'Scenario_01'
CAMPAIGN_TEMPLATE = 'OMD3_Main_Story'

#: A row of this name in a slot campaign table does not make a campaign of its own:
#: OrcsMustUI adds the missions it lists to the game's Endless tab. It is how a mod puts
#: its Endless twin in Endless without patching the shipped CampaignProtos, which every
#: mod doing so ships and only one of wins. The row has a Guid of its own -- never the
#: shipped Endless campaign's, which would collide with it in the game's proto index.
ENDLESS_EXTENSION = 'OMD3_Endless'
#: The Endless twin's row name: the mission's with this in place of `_Mission01`.
ENDLESS_TWIN = '_Endless01'


def endless_extension_table(path, guid_payloads):
    """(uasset, uexp) of a slot campaign table at `path` holding one ENDLESS_EXTENSION
    row that lists `guid_payloads` -- the missions' Endless twins."""
    c = missionproto.read(missionproto.CAMPAIGN_TABLE)
    row = c.clone(ENDLESS_CAMPAIGN, '%s_Extension' % ENDLESS_EXTENSION)
    row['name'] = ENDLESS_EXTENSION
    row['name_index'] = c.pkg.add_name(ENDLESS_EXTENSION)
    c.set_missions(row, list(guid_payloads))
    c.set_guid(c.field(row, 'Guid'))
    for flag in ('bUseLevelIntros', 'bUseLevelCinematics', 'bUseNPE', 'bRequiresEntitlement'):
        f = c.field(row, flag)
        if f is not None:
            c.set_bool(f, False)
    c.rows = [row]
    modtable.rename_package(c.pkg, missionproto.CAMPAIGN_TABLE, 'CampaignProtos',
                            path, path.rsplit('/', 1)[-1])
    c.export['data'] = c.serialise()
    return c.pkg.write()


def set_endless(files, on):
    """A mission build's files with its Endless twin and ENDLESS_EXTENSION row added
    (`on`) or taken out -- what the Missions page's Endless (Same Map or None) changes without
    rebuilding the mission. `files` is {inner path: bytes} of one build in a slot."""
    import re
    key, slot = mission_table_key(files)
    if key is None:
        raise SystemExit('this build has no mission table, so Endless cannot be changed here')
    pkg = Package(files[key], files[key[:-7] + '.uexp'])
    pkg.load_payloads()
    t = missionproto.Table(pkg)
    twins = [r for r in t.rows if r['name'].endswith(ENDLESS_TWIN)]
    base = next(r for r in t.rows if not r['name'].endswith(ENDLESS_TWIN))
    out = dict(files)
    ckey = 'OMD/Content' + campaign_slot_path(slot)[len('/Game'):] + '.uasset'
    if on and not twins:
        name = base['name'].rsplit('_', 1)[0] + ENDLESS_TWIN
        twin = t.clone(base['name'], name)
        for f in ('DefaultCharacters', 'MissionUnlocks', 'ItemUnlocks', 'SlotUnlocks',
                  'CharacterUnlocks'):
            t.clear_array(twin, f)
        t.set_guid(t.field(twin, 'Guid'))
        twins = [twin]
    elif not on:
        t.rows = [r for r in t.rows if not r['name'].endswith(ENDLESS_TWIN)]
        twins = []
    t.export['data'] = t.serialise()
    out[key], out[key[:-7] + '.uexp'] = t.pkg.write()
    out.pop(ckey, None)
    out.pop(ckey[:-7] + '.uexp', None)
    if twins:
        out[ckey], out[ckey[:-7] + '.uexp'] = endless_extension_table(
            campaign_slot_path(slot), [bytes(t.field(r, 'Guid').payload) for r in twins])
    ekey = 'OMD/Content' + entitlement_path(slot, slot)[len('/Game'):] + '.uasset'
    if ekey not in out and isinstance(slot, int):
        ekey = 'OMD/Content' + content_slot_path(slot, ENTITLEMENT_CONTENT)[len('/Game'):] + '.uasset'
    if ekey in out:
        out[ekey], out[ekey[:-7] + '.uexp'] = relist_entitlements(
            out[ekey], out[ekey[:-7] + '.uexp'],
            [bytes(t.field(r, 'Guid').payload)[:16] for r in t.rows])
    return out


def mission_row_for(map_name):
    """The MissionProtos row a map's own mission uses, or None.

    Two naming quirks: the DLC campaign keys its rows `DLCScenario_NN` while its
    folders are `ScenarioDLC_NN`, and a war scenario's row takes a `_WS` suffix
    (`Scenario_09_WS`, `DLCScenario_03_WS`). A `_End` row is the post-mission
    screen and is never the mission itself.
    """
    from riftlord.build import missionproto
    t = missionproto.read()
    stems = [map_name]
    if map_name.startswith('ScenarioDLC_'):
        stems.append('DLCScenario_' + map_name[len('ScenarioDLC_'):])
    for stem in stems:
        for name in (stem, stem + '_WS'):
            if t.row(name):
                return name
    return None


#: The campaign row Endless mode enumerates. Its missions are the _End twins,
#: never the campaign rows themselves.
ENDLESS_CAMPAIGN = 'OMD3_Endless'

#: One row per purchasable entitlement, each listing the missions it covers.
ENTITLEMENTS_TABLE = '/Game/Data/EntitlementsProtos'
#: The content table of a mission's slot that declares its entitlements to OrcsMustUI.
ENTITLEMENT_CONTENT = 7
#: Joins the game's entitlement row and the mod in a declaration row's name.
ENTITLEMENT_JOIN = '__'
#: The shipped entitlement row a declaration is cloned from: every field empty.
ENTITLEMENT_BLANK = 'OMDD'
#: A declaration row's own arrays, all empty but MissionEntitlements.
ENTITLEMENT_ARRAYS = ('CampaignEntitlements', 'ItemEntitlements', 'MissionEntitlements',
                      'SkinEntitlements', 'RandomModeModifierEntitlements',
                      'CharacterGrants', 'ItemGrants', 'SkinGrants', 'MissionUnlocks')


def known_guids():
    """Every shipped mission Guid, for locating a Guid inside an array element."""
    from riftlord.build import missionproto
    m = missionproto.read()
    out = []
    for r in m.rows:
        f = m.field(r, 'Guid')
        if f is not None:
            out.append(bytes(f.payload))
    return out


def campaign_order(campaign):
    """The mission row names a campaign lists, in order."""
    from riftlord.build import missionproto
    m = missionproto.read()
    c = missionproto.read(missionproto.CAMPAIGN_TABLE)
    row = c.row(campaign)
    if row is None:
        return []
    by_guid = {}
    for r in m.rows:
        f = m.field(r, 'Guid')
        if f is not None:
            by_guid[bytes(f.payload)] = r['name']
    _n, _t, items = c.struct_array(c.field(row, 'MissionList'))
    out = []
    for it in items:
        for g, nm in by_guid.items():
            if g in it:
                out.append(nm)
                break
    return out


def entitlements_for(map_name):
    """The entitlement rows that cover a map's own mission.

    `bRequiresEntitlement` on a mission does not name which entitlement; the
    game answers that with `GetEntitlementsForMission`, which searches every
    FOMDEntitlementProto's `MissionEntitlements` for the mission's Guid. A
    mission in none of them requires an entitlement that cannot be found, so it
    reads as locked however much of the game the player owns.
    """
    from riftlord.build import missionproto
    row = mission_row_for(map_name)
    if row is None:
        return []
    m = missionproto.read()
    guid = bytes(m.field(m.row(row), 'Guid').payload)
    t = missionproto.read(ENTITLEMENTS_TABLE)
    out = []
    for r in t.rows:
        f = t.field(r, 'MissionEntitlements')
        if f is None:
            continue
        _n, _tag, items = t.struct_array(f)
        if any(guid in it for it in items):
            out.append(r['name'])
    return out


def native_campaign_for(map_name):
    """The campaign row that natively lists a map's mission, or None.

    Entitlement is expressed twice and must agree: a DLC campaign carries
    `bRequiresEntitlement` and so does each of its missions. A DLC mission
    listed in a base campaign keeps its own gate but lands somewhere that names
    no entitlement, and it shows up locked even to a player who owns the DLC.
    """
    from riftlord.build import missionproto
    row = mission_row_for(map_name)
    if row is None:
        return None
    m = missionproto.read()
    guid = bytes(m.field(m.row(row), 'Guid').payload)
    c = missionproto.read(missionproto.CAMPAIGN_TABLE)
    for r in c.rows:
        _n, _t, items = c.struct_array(c.field(r, 'MissionList'))
        if any(guid in it for it in items):
            return r['name']
    return None


def campaign_is_entitled(campaign):
    """True when a campaign row carries its own entitlement gate."""
    from riftlord.build import missionproto
    c = missionproto.read(missionproto.CAMPAIGN_TABLE)
    r = c.row(campaign)
    f = c.field(r, 'bRequiresEntitlement') if r else None
    return bool(f and f.bool_value)


def previous_guids(pak_path):
    """{row name: 16-byte Guid payload} of the mission rows in a pak this tool
    wrote earlier, or {} when there is none to read."""
    if not pak_path or not os.path.exists(pak_path):
        return {}
    from riftlord.build.navigation import navpatch
    out = {}
    try:
        names = navpatch.pak_names(pak_path)
        for key in names:
            if not key.endswith('_MissionProtos.uasset'):
                continue
            ua = navpatch.read_from_pak(pak_path, names[key][1])
            ux = navpatch.read_from_pak(pak_path, names[key[:-7] + '.uexp'][1])
            pkg = Package(ua, ux)
            pkg.load_payloads()
            t = missionproto.Table(pkg)
            for r in t.rows:
                f = t.field(r, 'Guid')
                if f is not None:
                    out[r['name']] = bytes(f.payload)
    except (Exception, SystemExit):                  # noqa: BLE001
        return {}
    return out


#: Where a mod's extension tables live: rows named like the game's own, whose lists
#: ProtoRegistry appends to the game's rows (OrcsMustUI Docs/Registry.md, extensions).
EXTENDS_DIR = 'Extends'


def entitlement_path(mod, slot=None):
    """The extension table declaring a mission mod's missions under the game's
    entitlement rows: /Game/Mods/<mod>/Extends/<mod>_EntitlementsProtos. A pak built
    with numbered slots kept them in its content table ENTITLEMENT_CONTENT."""
    key = slot if slot is not None else mod
    if isinstance(key, int):
        return content_slot_path(key, ENTITLEMENT_CONTENT)
    return '%s/%s/%s/%s_EntitlementsProtos' % (MOD_ROOT, key, EXTENDS_DIR, key)


def entitlement_guid(row_name):
    """A declaration row's own Guid: stable for its name, never a game row's."""
    import hashlib
    import uuid
    return uuid.UUID(bytes=hashlib.md5(b'RiftLord.entitlement.' + row_name.encode('utf-8'))
                     .digest())


def entitle(mod, path, rows, guid_payloads, report):
    """(uasset, uexp) of an OMDEntitlementProto extension table at `path` declaring the
    missions under each game entitlement row in `rows`, or None when `rows` is empty.

    One row per game row, a copy of it under its exact name, with MissionEntitlements
    holding only our missions and every other list empty. ProtoRegistry appends them to
    the game's row before the index is built and refuses any Guid that is not a mod
    row's (OrcsMustUI Docs/Registry.md, extensions). A gate, never a grant."""
    if not rows:
        return None
    et = missionproto.read(ENTITLEMENTS_TABLE)
    known = known_guids()
    out = []
    for name in rows:
        row = et.row(name)
        if row is None:
            raise SystemExit('no entitlement row %r' % name)
        for array in ENTITLEMENT_ARRAYS:
            et.clear_array(row, array)
        for g in guid_payloads:
            et.add_guid(row, 'MissionEntitlements', g, known=known)
        out.append(row)
    et.rows = out
    modtable.rename_package(et.pkg, ENTITLEMENTS_TABLE, 'EntitlementsProtos',
                            path, path.rsplit('/', 1)[-1])
    report['entitlement_path'] = path
    report['entitlement_rows'] = [r['name'] for r in out]
    return et.write()


def entitlement_entry(files, path, blobs):
    """{inner path: bytes} of the declaration table, refusing a content table the
    pak already uses for another row type."""
    inside = 'OMD/Content' + path[len('/Game'):]
    if inside + '.uasset' in files:
        raise SystemExit('%s is already in the pak; entitlements need content table %d '
                         'of the mod free' % (path, ENTITLEMENT_CONTENT))
    return {inside + '.uasset': blobs[0], inside + '.uexp': blobs[1]}


def relist_entitlements(ua, ux, guid_payloads):
    """(uasset, uexp) of a declaration table with every row's MissionEntitlements
    set to exactly `guid_payloads`."""
    pkg = Package(ua, ux)
    pkg.load_payloads()
    t = missionproto.Table(pkg)
    for row in t.rows:
        field = t.field(row, 'MissionEntitlements')
        _n, tag, items = t.struct_array(field)
        out = []
        for g in guid_payloads:
            element = bytearray(items[0])
            element[-24:-8] = g
            out.append(bytes(element))
        field.payload = t._pack_array(tag, out)
    return t.write()


def entitlement_checks(files, report, mission_guids, gated):
    """Checks on a build's entitlement declarations against its missions' Guids."""
    checks = [('shipped EntitlementsProtos is not in the pak',
               not any('Data/EntitlementsProtos' in k for k in files), '')]
    path = report.get('entitlement_path')
    if not gated:
        checks.append(('mission needs no entitlement', path is None,
                       'bRequiresEntitlement=0'))
        return checks
    key = 'OMD/Content' + path[len('/Game'):] if path else ''
    if key + '.uasset' not in files:
        checks.append(('gated mission is declared under an entitlement', False, 'NONE'))
        return checks
    pkg = Package(files[key + '.uasset'], files[key + '.uexp'])
    pkg.load_payloads()
    t = missionproto.Table(pkg)
    stock = missionproto.read(ENTITLEMENTS_TABLE)
    game_guids = {bytes(stock.field(r, 'Guid').payload)[:16] for r in stock.rows}
    names = [r['name'] for r in t.rows]
    checks.append(('extension rows are named exactly like game entitlement rows',
                   bool(names) and all(stock.row(n) is not None for n in names),
                   ', '.join(names)))
    checks.append(('entitlements are an Extends table',
                   ('/%s/' % EXTENDS_DIR) in path, path))
    listed_ok, empty_ok = True, True
    for r in t.rows:
        _n, _t, items = t.struct_array(t.field(r, 'MissionEntitlements'))
        listed_ok &= sorted(it[-24:-8] for it in items) == sorted(mission_guids)
        empty_ok &= all(t.struct_array(t.field(r, a))[0] == 0
                        for a in ENTITLEMENT_ARRAYS if a != 'MissionEntitlements')
    checks.append(('declarations list exactly our missions', listed_ok,
                   '%d mission(s)' % len(mission_guids)))
    checks.append(('declarations grant nothing (other arrays empty)', empty_ok, ''))
    return checks


#: Point a level copy at the source level's own coordinator instead of a copy of it.
SHIPPED_COORDINATOR = False


def build(mod, title, level, par, war, campaign_title, into=None,
          endless=True, with_level=None, fill_unit='Kobold',
          last_unit='LizardmanRanger', waves=None, new_wave=None,
          mutate=None, roster=None, cinematic=False, drop_entitlement=False,
          sublevels=None, keep_sublevels=None, extra_files=None,
          apply_to_level=None, map_data=None, kill_z=None, registry=True, slot=None,
          guids=None, load_picture=None, campaign_line=None, portal_picture=None,
          scramble=None, into_index=None, endless_only=False, unlocks=None):
    """(files for the pak, a report). Nothing shipped is modified.

    `endless_only` places the mission in Endless alone: its own row is the Endless row
    (DefaultCharacters emptied too, as for a twin), listed by the slot's ENDLESS_EXTENSION
    row, with no twin. Listed there, OrcsMustUI shows it in the Endless tab and not
    under Custom Maps. It needs a slot and no campaign.

    `scramble` is the record's Scramble setting (missionscramble.clean): the mission
    joins those tiers of the live RandomModeBucketProtos through an Extends table.

    `unlocks` is the record's Unlocks setting (missionunlocks.clean): the heroes and items
    beating the mission grants, written into its row's CharacterUnlocks and ItemUnlocks.

    `into_index` is the mission's place in shipped campaign `into`'s MissionList (from
    customcampaign.SHIPPED_FIRST to its length + 1); None lists it last.

    `guids` maps a row name to the 16-byte Guid payload a previous build gave it
    (previous_guids). A campaign lists its missions by Guid, so a rebuild that
    drew fresh ones would leave every campaign naming this mission pointing at
    nothing.

    `load_picture` is {'path', 'crop'}: the mission's loading-screen picture
    (loadscreen.build). `campaign_line` is the loading screen's campaign line
    (loadscreen.campaign_line); by default it follows `into` or `campaign_title`.
    `portal_picture` is {'path', 'crop'}: the hub gate's picture (hubportal.build).

    `registry` False leaves BP_OMDProtoManager out of the pak: OrcsMustUI adds
    the mod's tables to the registry as the game starts, so two map mods no
    longer ship rival copies of it, the last of which hides the other's maps.

    `slot` is the mod's key (slot_for): the mission table goes in the mod's own table
    folder (slot_path), the campaign table, if any, beside it (campaign_slot_path), and
    no registry ships -- OrcsMustUI registers them from their folder in the pak. With
    no campaign the mission is a custom map; with one, OrcsMustUI's hub gives the
    campaign a tab."""
    if slot is not None and not isinstance(slot, str):
        raise SystemExit('numbered slots are retired: pass slot=slot_for(mod)')
    if endless_only and (into or campaign_title is not None or slot is None):
        raise SystemExit('a mission placed in Endless is listed there alone: it takes a slot '
                         'and no campaign')
    if endless_only:
        endless = False
    root = '/Game/Mods/%s' % mod
    mission_path = '%s/%s_MissionProtos' % (root, mod)
    campaign_path = '%s/%s_CampaignProtos' % (root, mod)
    if slot is not None:
        mission_path = slot_path(slot)
        campaign_path = campaign_slot_path(slot)
        registry = False
        report_slot = slot
    else:
        report_slot = None
    row = '%s_Mission01' % mod
    erow = '%s_Endless01' % mod
    crow = '%s_Campaign' % mod
    report = {'mission_path': mission_path, 'campaign_path': campaign_path, 'slot': report_slot}
    extra = {}
    featured_class = None

    # 0. our own copy of a shipped level, pointed at our own coordinator, so the
    # mission plays waves we authored rather than the source map's
    map_data_of = None
    if with_level:
        from riftlord.build import makelevel
        coord_path = '%s/%s_Onslaught01' % (root, mod)
        level_path = '%s/Maps/%s_Map01/%s_Map01' % (root, mod, mod)
        source_coord, _obj = makelevel.coordinator_of(with_level)
        # the final wave's first unit is the headline, so it takes the back
        # centre; the rest of the roster follows in order of first appearance
        if roster is None:
            tail = list(new_wave or [last_unit])
            roster = []
            for unit in tail + [u for _w, v in sorted((waves or {}).items())
                                for u in v] + [fill_unit]:
                if unit not in roster:
                    roster.append(unit)
        if SHIPPED_COORDINATOR:
            coord_path = source_coord
        cfiles = None if SHIPPED_COORDINATOR else makelevel.build_coordinator(
            source_coord.rsplit('/', 1)[-1], coord_path,
            mutate if mutate is not None
            else makelevel.author_waves(fill=fill_unit, last=last_unit,
                                        waves=waves, new_wave=new_wave))
        lfiles, renamed = makelevel.build_level(
            with_level, level_path, coord_path, coord_path.rsplit('/', 1)[-1],
            sublevels=sublevels, keep_sublevels=keep_sublevels)
        # A caller that authored something into the level gets to write it in, on the
        # copy and after the retarget, because a spawner's route is named by a soft
        # reference that has to hold the copy's own package path.
        if apply_to_level is not None:
            lfiles, applied = apply_to_level(
                lfiles[0], lfiles[1], level_path,
                level_path.rsplit('/', 1)[-1])
            report['applied'] = applied
        # The host's KillZ is its own map's floor, not ours: Scenario_01 kills below
        # -800 while a converted level can reach -2475. Lowered on the COPY.
        if kill_z is not None:
            from riftlord.build import hostpatch
            body, was = hostpatch.set_kill_z(lfiles[0], lfiles[1], kill_z)
            lfiles = (lfiles[0], body)
            report['kill_z'] = was
        extra[level_path] = (lfiles, '.umap')
        if cfiles is not None:
            extra[coord_path] = (cfiles, '.uasset')
        level = level_path
        map_data_of = '%s_MapData' % mapdata.map_path(with_level)
        report['level_from'] = '%s (%d names retargeted)' % (with_level, renamed)
        report['coordinator'] = coord_path
        # The hub's enemy preview is generated from the same roster the waves
        # use, so what a player sees before pressing play is what the mission
        # actually spawns. If a creature has no mesh or idle under the expected
        # name, fall back to the closest shipped diorama.
        from riftlord.onslaught import makepreview
        from riftlord.onslaught import featured
        preview_path = '%s/%s_Enemies01' % (root, mod)
        pfiles, prep = makepreview.build(roster, preview_path)
        # generate with whatever resolves; a unit that appears in no shipped
        # diorama simply cannot be shown, which is not a reason to give up the
        # ones that can
        if pfiles is not None and prep['showing']:
            extra[preview_path] = (pfiles, '.uasset')
            featured_class = '%s.%s_C' % (preview_path,
                                          preview_path.rsplit('/', 1)[-1])
            report['featured'] = 'generated from %s: %s%s' % (
                prep['donor'],
                '; '.join('%s at %s' % (c, w) for c, w in prep['showing']),
                '  (no diorama art for %s)' % ', '.join(prep['unresolved'])
                if prep['unresolved'] else '')
            #: Preview effects added, and effect keys that could not be used
            if prep.get('effects'):
                report['featured'] += '; effects %s' % ', '.join(
                    '%s %s%s' % (c, p.rsplit('/', 1)[-1], ' at %s' % s if s else '')
                    for c, p, s in prep['effects'])
            report['preview_warnings'] = list(prep.get('warnings') or [])
        else:
            picks = featured.best(roster)
            if picks:
                path, shown, extras = picks[0]
                featured_class = '%s.%s_C' % (path, path.rsplit('/', 1)[-1])
                report['featured'] = 'fell back to %s (shows %s, plus %d other%s)' % (
                    path.rsplit('/', 1)[-1], ', '.join(shown), extras,
                    '' if extras == 1 else 's')

    # 1. our mission, in our own table
    m = missionproto.read()
    # Clone the SOURCE map's own row, not a fixed one: the row carries the
    # mission's thumbnail, the gate's PortalDisplayClass, its screenshot and
    # its loading screen, and a mission cloned from Scenario_01 wears North
    # Wing's gate whatever level it actually loads.
    template = (mission_row_for(with_level) if with_level else None) \
        or MISSION_TEMPLATE
    report['row_from'] = template
    new = m.clone(template, row)
    # A cloned row carries the template's rewards: the traps, the loadout slot
    # and the next story mission that finishing it hands out. Those belong to
    # the mission it was copied from, not to ours -- leaving them in would mean
    # beating a custom map advanced the player's campaign and unlocked the
    # template's traps. Emptied, our mission grants nothing.
    #
    # Nothing then names our Guid in any unlock chain, and that is exactly what
    # makes it playable: EndlessWar adds 36 missions this way and not one of
    # them appears in another mission's MissionUnlocks or in an entitlement's,
    # yet they are all enterable. A mission no chain mentions is unlocked.
    strip = ['MissionUnlocks', 'ItemUnlocks', 'SlotUnlocks', 'CharacterUnlocks']
    # Listed in Endless directly, this row IS the Endless row, and all 26
    # shipped ones pin no characters: the player brings their own.
    if into == ENDLESS_CAMPAIGN or endless_only:
        strip.append('DefaultCharacters')
    cleared = [f for f in strip if m.clear_array(new, f)]
    if cleared:
        report['rewards'] = 'cleared %s inherited from %s' % (
            ', '.join(cleared), template)
    # A custom onslaught is not a story beat, so it does not replay the source
    # mission's intro -- Scenario_01's is the game's opening cutscene.
    if not cinematic:
        pre = m.field(new, 'PreMissionCinematic')
        if pre is not None and m.set_struct_soft_path(pre, 'Cinematic', None):
            report['cinematic'] = 'intro cleared'
    # A DLC map's row is entitled to that DLC, and the flag travels with the
    # clone: the mission then shows up locked. The author's own mission is not
    # DLC content, so the gate goes -- though a player still needs the DLC
    # installed for the level itself, which is the source map's.
    # The gate is kept: it belongs to the map the mission is built on. What it
    # needs is a campaign that names the same entitlement, or the player is
    # locked out of content they own.
    # The gate belongs to the map the mission is built on, so it stays. What it
    # needs is for the mission to be findable under an entitlement -- otherwise
    # the requirement names nothing and the mission reads as locked to everyone.
    ent = m.field(new, 'bRequiresEntitlement')
    entitle_rows = []
    if ent is not None and ent.bool_value:
        if drop_entitlement:
            m.set_bool(ent, False)
            report['entitlement'] = 'dropped by request (was set by %s)' % template
        else:
            entitle_rows = entitlements_for(with_level) if with_level else []
            if not entitle_rows:
                m.set_bool(ent, False)
                report['entitlement'] = (
                    'dropped: %s is gated but belongs to no entitlement, so the '
                    'gate could never be satisfied' % template)
            else:
                report['entitlement'] = 'kept, registered under %s' % \
                    ', '.join(entitle_rows)
    m.set_text(m.field(new, 'MissionName'), 'MissionProtos', '%s_Name' % row, title)
    # The loading screen: a per-map widget showing the source map's title is
    # copied into the mod and retitled, showing `load_picture` if given
    # (loadscreen.py); the Endless twin below inherits it.
    ls_field = m.field(new, 'MissionLoadScreen')
    if ls_field is not None:
        from riftlord.build.world import loadscreen
        ls_at = struct.unpack_from('<i', ls_field.payload, 0)[0]
        ls_files, ls_class, report['load_screen'] = loadscreen.build(
            m.names[ls_at] if 0 <= ls_at < len(m.names) else None,
            '%s/%s_LoadScreen' % (root, mod), '%s_Name' % row, title,
            picture=load_picture, picture_path='%s/%s_LoadPicture' % (root, mod),
            subtitle=campaign_line or loadscreen.campaign_line(
                shipped=into, name=campaign_title, key='%s_Name' % crow))
        if ls_files is not None:
            m.set_soft_path(ls_field, ls_class)
            for ls_path, ls_blobs in ls_files.items():
                extra[ls_path] = (ls_blobs, '.uasset')
    # The hub gate's picture (hubportal.py); the Endless twin inherits it too.
    portal_field = m.field(new, 'PortalDisplayClass')
    if portal_picture and portal_field is not None:
        from riftlord.build.world import hubportal
        pd_at = struct.unpack_from('<i', portal_field.payload, 0)[0]
        pd_files, pd_class, report['portal'] = hubportal.build(
            m.names[pd_at] if 0 <= pd_at < len(m.names) else None, root, mod,
            portal_picture)
        if pd_files is not None:
            m.set_soft_path(portal_field, pd_class)
            for pd_path, pd_blobs in pd_files.items():
                extra[pd_path] = (pd_blobs, '.uasset')
        # the same picture is the mission-list thumbnail
        thumb_field = m.field(new, 'ThumbnailTexture')
        if thumb_field is not None:
            th_files, th_object, report['thumbnail'] = hubportal.thumbnail(
                root, mod, portal_picture)
            m.set_soft_path(thumb_field, th_object)
            for th_path, th_blobs in th_files.items():
                extra[th_path] = (th_blobs, '.uasset')
    m.set_soft_path(m.field(new, 'MissionLevel'),
                    '%s.%s' % (level, level.rsplit('/', 1)[-1]))
    # MapData stays with the source map unless the caller brought its own: a copied
    # level has no MapData, and that asset is what draws the minimap. A caller that
    # emits one -- Rift Lord does, so the rift and door markers follow its plan --
    # passes its path and the row points there instead.
    md = map_data or map_data_of or (level + '_MapData')
    m.set_soft_path(m.field(new, 'MapData'), '%s.%s' % (md, md.rsplit('/', 1)[-1]))
    if par:
        m.set_float(m.field(new, 'ParTimeSeconds'), par)
    if war:
        m.set_bool(m.field(new, 'bIsWarScenario'), True)
    if featured_class:
        m.set_soft_path(m.field(new, 'FeaturedMinionsClass'), featured_class)
    guids = guids or {}
    if guids.get(row):
        m.field(new, 'Guid').payload = bytearray(guids[row])
        report['guid'] = 'kept from the previous build'
    else:
        m.set_guid(m.field(new, 'Guid'))
    guid_payload = bytes(m.field(new, 'Guid').payload)
    report['mission_guid'] = guid_payload[:16].hex()

    # Endless mode does not list the campaign mission: it lists a separate row,
    # which is why every shipped map has a Scenario_NN_End twin. Measured over
    # all 26 of them, the twin is the campaign row with a fresh Guid and its
    # five arrays empty -- same level, same map data, same art.
    endless_guid = None
    keep = {row}
    if endless and into != ENDLESS_CAMPAIGN:
        twin = m.clone(row, erow)
        for f in ('DefaultCharacters', 'MissionUnlocks', 'ItemUnlocks',
                  'SlotUnlocks', 'CharacterUnlocks'):
            m.clear_array(twin, f)
        m.set_text(m.field(twin, 'MissionName'), 'MissionProtos',
                   '%s_Name' % erow, title)
        if guids.get(erow):
            m.field(twin, 'Guid').payload = bytearray(guids[erow])
        else:
            m.set_guid(m.field(twin, 'Guid'))
        endless_guid = bytes(m.field(twin, 'Guid').payload)
        keep.add(erow)
        report['endless_row'] = erow
    m.rows = [r for r in m.rows if r['name'] in keep]
    modtable.rename_package(m.pkg, missionproto.TABLE, 'MissionProtos',
                            mission_path, mission_path.rsplit('/', 1)[-1])
    m.export['data'] = m.serialise()
    mission_files = m.pkg.write()
    report['mission_row'] = row

    # 2a. put the mission in a SHIPPED campaign. That patches a stock table, so
    # it is not additive -- but a campaign row is the only place the game looks
    # for missions to show, and our own table is not enumerated.
    if into:
        stock = missionproto.read(missionproto.CAMPAIGN_TABLE)
        crow_stock = stock.row(into)
        if crow_stock is None:
            raise SystemExit('no campaign row %r' % into)
        #: the campaign's own first mission, before ours goes in (the unlock below)
        first = stock.struct_array(stock.field(crow_stock, 'MissionList'))[2][0]
        if into_index is not None:
            from riftlord.missions import customcampaign
            into_index = customcampaign.campaign_index(
                into_index, stock.struct_array(stock.field(crow_stock, 'MissionList'))[0] + 1,
                customcampaign.SHIPPED_FIRST)
        n = stock.add_mission(crow_stock, guid_payload, at=into_index)
        report['listed_in'] = '%s (now %d missions)' % (into, n)
        report['listed_at'] = into_index or n

        # Endless mode reads its own campaign row, so the twin is listed there
        # and nowhere else. `into` may already BE that row, in which case the
        # mission above is the Endless entry and there is no twin.
        if endless_guid is not None:
            erow_stock = stock.row(ENDLESS_CAMPAIGN)
            if erow_stock is None:
                raise SystemExit('no campaign row %r' % ENDLESS_CAMPAIGN)
            n = stock.add_mission(erow_stock, endless_guid)
            report['endless_listed_in'] = '%s (now %d missions)' % (
                ENDLESS_CAMPAIGN, n)

        # 2b. the entitlement gate: the mission and its Endless twin are declared
        # under each game row covering the source map, in the slot's content table.
        ent_path = entitlement_path(mod, slot)
        ent_files = entitle(mod, ent_path, entitle_rows,
                            [guid_payload] + ([endless_guid] if endless_guid else []),
                            report)
        gated = ent_files is not None

        # 2c. the unlock. `UOMDSaveGame.UnlockedMissions` is an explicit
        # allowlist -- 75 of this game's 78 mission Guids are in a played save,
        # the absentees being the two dev rows and Scenario_01, the campaign's
        # implicit first. A new mission is in nobody's save, so something has to
        # name it or it shows up locked.
        #
        # The lever is another mission's MissionUnlocks, and it applies
        # RETROACTIVELY: EndlessWar's five _WS_End rows are chained off already
        # completed war scenarios and never granted by an entitlement, and all
        # five are in this save; the one row it neither chained nor granted,
        # DLCScenario_03_WS_End, is not. So chaining reaches a save that was
        # written before the mod existed.
        #
        # Chained off the campaign's FIRST mission, which anyone playing that
        # campaign has cleared. An entitlement's MissionUnlocks would work too,
        # but it is a purchase grant: a base-game map has no entitlement to
        # hang off, and borrowing one would both gate the mission behind DLC the
        # map does not need and put our Guid in a list that exists to say what a
        # payment buys. This adds one entry, ours, to one shipped mission row,
        # and unlocks nothing else.
        unlock_files = None
        opener = first[-24:-8]
        mt = missionproto.read()
        host = next((r for r in mt.rows
                     if bytes(mt.field(r, 'Guid').payload)[:16] == opener), None)
        if host is None:
            raise SystemExit('cannot find %s\'s first mission to chain from' % into)
        for g in [guid_payload] + ([endless_guid] if endless_guid else []):
            n = mt.add_guid(host, 'MissionUnlocks', g, known=known_guids())
        unlock_files = mt.write()
        report['unlock_host'] = host['name']
        report['unlocked'] = (
            'clearing %s, the first mission of %s, unlocks it -- that is the '
            'only row touched, and it gains nothing but ours'
            % (host['name'], into))
        if gated:
            report['unlocked'] += ('. The %s gate still applies'
                                   % ', '.join(entitle_rows))

        stock_files = stock.write()
        manager_files = None
        report['runtime_registry'] = not registry
        if registry:
            pkg = protoreg.load()
            protoreg.add_table(pkg, mission_path)
            report['registry_size'] = len(protoreg.find_array(pkg)[3])
            manager_files = pkg.write()

        def entry(path, blobs, ext='.uasset'):
            inside = 'OMD/Content' + path[len('/Game'):]
            return {inside + ext: blobs[0], inside + '.uexp': blobs[1]}
        files = {}
        files.update(entry(mission_path, mission_files))
        files.update(entry(missionproto.CAMPAIGN_TABLE, stock_files))
        if unlock_files is not None:
            files.update(entry(missionproto.TABLE, unlock_files))
        if manager_files is not None:
            files.update(entry(protoreg.MANAGER, manager_files))
        for path, (blobs, ext) in extra.items():
            files.update(entry(path, blobs, ext))
        # Packages the caller built itself -- a custom geometry sublevel, say.
        # They go in as they are: makemod does not know what is in them and does
        # not need to, because `sublevels` has already pointed the level at them.
        for path, (blobs, ext) in (extra_files or {}).items():
            files.update(entry(path, blobs, ext))
        if ent_files is not None:
            files.update(entitlement_entry(files, ent_path, ent_files))
        files = scramble_entry(files, mod, guid_payload, scramble, report)
        files = unlocks_entry(files, unlocks, endless_only or into == ENDLESS_CAMPAIGN,
                              report)
        report['paths'] = sorted(files)
        report['campaign_row'] = into
        return files, report

    # 2. our campaign, listing only our mission -- or none: a mission no campaign
    # lists is a custom map, which OrcsMustUI's hub shows in its Custom Maps tab
    campaign_files = None
    report['campaign_row'] = None
    if campaign_title is None and slot is not None and endless_guid is not None:
        # In Endless through OrcsMustUI: the twin is listed by an ENDLESS_EXTENSION
        # row in the slot's campaign table, which no other mod ships
        campaign_files = endless_extension_table(campaign_path, [endless_guid])
        report['endless_extension'] = campaign_path
    elif endless_only:
        # placed in Endless: the mission itself is the Endless entry
        campaign_files = endless_extension_table(campaign_path, [guid_payload])
        report['endless_extension'] = campaign_path
        report['endless_only'] = True
    if campaign_title is not None:
        c = missionproto.read(missionproto.CAMPAIGN_TABLE)
        cnew = c.clone(CAMPAIGN_TEMPLATE, crow)
        c.set_text(c.field(cnew, 'CampaignName'), 'CampaignProtos',
                   '%s_Name' % crow, campaign_title)
        c.set_missions(cnew, [guid_payload])
        c.set_guid(c.field(cnew, 'Guid'))
        # A campaign with mission unlocks on gates its missions behind an unlock
        # chain, and nothing unlocks a newly added one -- it shows up locked. The
        # intro and cinematic flags expect per-mission assets a custom map has not
        # got, so they come off too.
        for flag, value in (('bUseMissionUnlocks', False), ('bUseLevelIntros', False),
                            ('bUseLevelCinematics', False), ('bUseNPE', False)):
            field = c.field(cnew, flag)
            if field is not None:
                c.set_bool(field, value)
        report['campaign_flags'] = 'unlocks/intros/cinematics/NPE off'
        c.rows = [r for r in c.rows if r['name'] == crow]
        modtable.rename_package(c.pkg, missionproto.CAMPAIGN_TABLE, 'CampaignProtos',
                                campaign_path, campaign_path.rsplit('/', 1)[-1])
        c.export['data'] = c.serialise()
        campaign_files = c.pkg.write()
        report['campaign_row'] = crow

    # 2b. the entitlement gate, as for a mission in a shipped campaign.
    ent_path = entitlement_path(mod, slot)
    ent_files = entitle(mod, ent_path, entitle_rows,
                        [guid_payload] + ([endless_guid] if endless_guid else []),
                        report)

    # 3. register both with the proto manager -- or leave that to OrcsMustUI
    manager_files = None
    report['runtime_registry'] = not registry
    if registry:
        pkg = protoreg.load()
        n = protoreg.add_table(pkg, mission_path)
        if campaign_files is not None:
            n = protoreg.add_table(pkg, campaign_path)
        manager_files = pkg.write()
        report['registry_size'] = n

    def entry(path, blobs, ext='.uasset'):
        inside = 'OMD/Content' + path[len('/Game'):]
        return {inside + ext: blobs[0], inside + '.uexp': blobs[1]}

    files = {}
    files.update(entry(mission_path, mission_files))
    if campaign_files is not None:
        files.update(entry(campaign_path, campaign_files))
    if manager_files is not None:
        files.update(entry(protoreg.MANAGER, manager_files))
    # The level copy, its coordinator and the hub preview. These were merged only in
    # the variant that lists the mission in a shipped campaign, so a mod with its own
    # campaign built the level and then shipped a pak without it -- the mission existed
    # and pointed at a map that was not there.
    for path, (blobs, ext) in extra.items():
        files.update(entry(path, blobs, ext))
    for path, (blobs, ext) in (extra_files or {}).items():
        files.update(entry(path, blobs, ext))
    if ent_files is not None:
        files.update(entitlement_entry(files, ent_path, ent_files))
    files = scramble_entry(files, mod, guid_payload, scramble, report)
    files = unlocks_entry(files, unlocks, endless_only, report)
    report['paths'] = sorted(files)
    return files, report


def unlocks_entry(files, unlocks, endless_only, report):
    """`files` with the mission row granting `unlocks` (missionunlocks.apply); its report
    fields go into `report`. A mission placed in Endless grants nothing."""
    from riftlord.missions import missionunlocks
    out, got = missionunlocks.apply(files, unlocks, endless_only=endless_only)
    report.update(got)
    return out


def scramble_entry(files, mod, guid_payload, scramble, report):
    """`files` with the mission's Scramble extension (missionscramble.apply) for
    `scramble`; its report fields go into `report`. The mods that define its tiers are
    not added to `requires`: without them its rows are dormant."""
    from riftlord.missions import missionscramble
    out, got = missionscramble.apply(files, mod, guid_payload, scramble)
    report.update(got)
    return out


def verify_into(files, report):
    """Checks for the variant that lists the mission in a shipped campaign."""
    checks = []
    mod = report['mission_row'].split('_')[0]

    def load(stem):
        p = Package(files['OMD/Content' + stem + '.uasset'],
                    files['OMD/Content' + stem + '.uexp'])
        p.load_payloads()
        return p

    mp = load(report['mission_path'][len('/Game'):])
    t = missionproto.Table(mp)
    mine = [report['mission_row']]
    if report.get('endless_row'):
        mine.append(report['endless_row'])
    checks.append(("mod table holds only this mod's rows",
                   [r['name'] for r in t.rows] == mine,
                   ', '.join(r['name'] for r in t.rows)))
    guid = bytes(t.field(t.row(report['mission_row']), 'Guid').payload)
    eguid = (bytes(t.field(t.row(report['endless_row']), 'Guid').payload)
             if report.get('endless_row') else None)
    # Beating our mission must reward nothing the template rewarded: those
    # unlocks belong to the mission it was copied from. What the author chose
    # (missionunlocks) is checked against the report by missionunlocks.checks.
    chosen = report.get('unlocks_row')
    for r in t.rows:
        fields = ('MissionUnlocks', 'SlotUnlocks') if r['name'] == chosen else \
            ('MissionUnlocks', 'ItemUnlocks', 'SlotUnlocks', 'CharacterUnlocks')
        empty = [f for f in fields if t.struct_array(t.field(r, f))[0] == 0]
        checks.append(('%s grants no unlocks' % r['name'] if r['name'] != chosen else
                       '%s grants only the unlocks chosen in its settings' % r['name'],
                       len(empty) == len(fields), ', '.join(empty)))
    if eguid is not None:
        checks.append(('the Endless twin has a separate Guid', eguid != guid, ''))
        base, twin = t.row(report['mission_row']), t.row(report['endless_row'])
        same = all(bytes(t.field(base, f).payload)
                   == bytes(t.field(twin, f).payload)
                   for f in ('MissionLevel', 'MapData'))
        checks.append(('the twin plays the same level and map data', same, ''))

    cp = load(missionproto.CAMPAIGN_TABLE[len('/Game'):])
    ct = missionproto.Table(cp)
    stock = missionproto.read(missionproto.CAMPAIGN_TABLE)

    # One campaign row gains the mission, and -- when a twin was built -- the
    # Endless row gains that. Both are checked the same way: exactly one new
    # entry, ours, with everything already there byte-identical.
    touched = {report['campaign_row']: guid}
    if eguid is not None:
        touched[ENDLESS_CAMPAIGN] = eguid
    for name, want in touched.items():
        row = ct.row(name)
        srow = stock.row(name)
        cnt, _tag, items = ct.struct_array(ct.field(row, 'MissionList'))
        scnt, _t2, sitems = stock.struct_array(stock.field(srow, 'MissionList'))
        checks.append(('%s gained one mission' % name, cnt == scnt + 1,
                       '%d -> %d' % (scnt, cnt)))
        #: where ours went: the place asked for in the campaign row, last in Endless
        at = (report.get('listed_at') or cnt) if name == report['campaign_row'] else cnt
        checks.append(('%s: its existing missions are untouched, in order' % name,
                       items[:at - 1] + items[at:] == sitems, ''))
        checks.append(('%s: entry %d is our guid' % (name, at),
                       0 < at <= cnt and items[at - 1][-24:-8] == want[:16], ''))
        # The unlock chain is read, never written: the campaign keeps gating
        # exactly as it shipped, and our mission is playable because no chain
        # names it rather than because a gate came off.
        gate = ct.field(row, 'bUseMissionUnlocks').bool_value
        checks.append(("%s: its unlock flag is untouched" % name,
                       gate == stock.field(srow, 'bUseMissionUnlocks').bool_value,
                       'bUseMissionUnlocks=%s' % bool(gate)))
    checks.append(('every other campaign row untouched',
                   all([f.bytes() for f in a['fields']] == [f.bytes() for f in b['fields']]
                       for a, b in zip(stock.rows, ct.rows)
                       if a['name'] not in touched), ''))

    # The unlock chain: exactly one shipped mission gains our Guid (and the
    # twin's), and every other row keeps the chain it shipped with.
    mstem = missionproto.TABLE[len('/Game'):]
    if 'OMD/Content' + mstem + '.uasset' in files:
        before = missionproto.read()
        after = missionproto.Table(load(mstem))
        want = [guid[:16]] + ([eguid[:16]] if eguid is not None else [])
        grew, wrong = [], []
        for r in after.rows:
            rb = before.row(r['name'])
            _na, _ta, ia = after.struct_array(after.field(r, 'MissionUnlocks'))
            _nb, _tb, ib = before.struct_array(before.field(rb, 'MissionUnlocks'))
            if ia == ib:
                continue
            grew.append(r['name'])
            # MissionUnlocks holds 156-byte progression elements, so the Guid
            # is not where an FOMDSoftProtoPtr array puts it -- ask. Asked
            # table-wide, because an Endless row's own array is empty.
            at = before.array_guid_offset('MissionUnlocks', known_guids())
            got = [bytes(e[at:at + 16]) for e in ia[len(ib):]] if at is not None \
                else None
            if ia[:len(ib)] != ib or got != want:
                wrong.append(r['name'])
        checks.append(('exactly one mission chains to ours',
                       len(grew) == 1 and not wrong,
                       ', '.join(grew) or 'nothing chains to it'))
        checks.append(('it is the first mission of %s' % report['campaign_row'],
                       bool(grew) and grew[0] == report.get('unlock_host'),
                       grew[0] if grew else '-'))

    # if a preview was generated, every slot must name a mesh and an idle
    prev_stem = '/Mods/%s/%s_Enemies01' % (mod, mod)
    if 'OMD/Content' + prev_stem + '.uasset' in files:
        from riftlord.onslaught import makepreview
        pv = load(prev_stem)
        pnames = [s for s, _h in pv.names]

        def pres(i):
            if i < 0:
                return pnames[struct.unpack_from('<I', pv.imports[-i - 1], 20)[0]]
            if i > 0:
                return pnames[struct.unpack_from('<I', pv.exports[i - 1]['meta'], 16)[0]]
            return 'null'
        slots = makepreview.slot_exports(pv)
        shown = []
        for e, _c, m, a in slots:
            z = None
            for pn, _k, _s, at, size in makepreview.walk(e['data'], pnames):
                if pn == 'RelativeLocation' and size == 12:
                    z = struct.unpack_from('<3f', e['data'], at)[2]
            if z is not None and z < -1000:
                continue                       # a surplus slot, hidden below
            shown.append((pres(struct.unpack_from('<i', e['data'], m)[0]),
                          pres(struct.unpack_from('<i', e['data'], a)[0])))
        good = all(mesh and anim and mesh != 'null' and anim != 'null'
                   for mesh, anim in shown)
        checks.append(('preview: every visible slot has a mesh and animation',
                       good, '%d of %d slots visible' % (len(shown), len(slots))))
        raw = files['OMD/Content' + prev_stem + '.uasset']
        body = files['OMD/Content' + prev_stem + '.uexp']
        checks.append(('preview: header consistent',
                       pv.summary_tail_parsed and pv.total_header_size == len(raw)
                       and (not pv.bulk_start_off or
                            pv.bulk_start_off == pv.total_header_size + len(body) - 4), ''))

    runtime = report.get('runtime_registry')
    if not runtime:
        mgr = load(protoreg.MANAGER[len('/Game'):])
        _c, _a, _h, entries = protoreg.find_array(mgr)
        resolved = [protoreg.resolve(mgr, e) for e in entries]
    # the row carries the hub's artwork: a mission cloned from the wrong row
    # shows another level's gate and can replay its intro cutscene
    if report.get('level_from'):
        import struct as _struct
        stem = report['mission_path'][len('/Game'):]
        mt = missionproto.Table(load(stem))
        mrow = mt.rows[0] if mt.rows else None
        source = report['level_from'].split(' ')[0]
        if mrow is not None:
            def soft(field):
                if field is None or len(field.payload) < 4:
                    return ''
                i = _struct.unpack_from('<I', bytes(field.payload), 0)[0]
                return mt.names[i] if i < len(mt.names) else ''
            # The artwork belongs to the row the mission was CLONED FROM, which for a
            # custom level is not the level it plays: a Rift Lord copy of North Wing
            # plays `RiftLordFoo_Map01` and correctly keeps North Wing's gate and
            # thumbnail, since there is no artwork under its own name. Comparing against
            # the level failed every derived mission for inheriting exactly what it
            # should. Either name passes, and both fields must agree, which is what
            # actually catches a row cloned from the wrong mission.
            artwork = report.get('row_from') or source
            seen = {}
            for fname in ('ThumbnailTexture', 'PortalDisplayClass'):
                got = soft(mt.field(mrow, fname))
                seen[fname] = got
                checks.append(('%s comes from %s' % (fname, artwork),
                               artwork in got or source in got,
                               got.rsplit('/', 1)[-1][:46]))
            def whose(text):
                """Which map an artwork asset belongs to, taken from its FOLDER.

                It used to come from the asset's own leaf name, which is only a naming
                convention -- and Scenario_06 breaks it. Its thumbnail really is called
                `ICON_MapThumbnail_Scenario_`, with the number left off by whoever made
                it, beside a portal called `BP_UI_Portal_Scenario_06`. Both sit in
                `/Game/Maps/Campaign/Scenario_06/`, so they do name one map and only
                the file name is careless -- but comparing leaves read that as a
                mismatch and refused to build the level at all. The folder is what
                actually says whose an asset is. The leaf stays as the fallback for a
                path that is not under /Game/Maps.
                """
                parts = [p for p in text.split('/') if p]
                if len(parts) >= 4 and parts[0] == 'Game' and parts[1] == 'Maps':
                    return parts[3]
                leaf = text.rsplit('/', 1)[-1].split('.')[0]
                for prefix in ('ICON_MapThumbnail_', 'BP_UI_Portal_'):
                    if leaf.startswith(prefix):
                        return leaf[len(prefix):]
                return leaf
            pair = {whose(v) for v in seen.values() if v}
            checks.append(('the gate and the thumbnail name one map',
                           len(pair) <= 1, ', '.join(sorted(pair))))
            pre = mt.field(mrow, 'PreMissionCinematic')
            cine = ''
            if pre is not None:
                for sub, at in mt.struct_sub_fields(pre):
                    if sub.name == 'Cinematic':
                        i = _struct.unpack_from('<I', bytes(pre.payload), at)[0]
                        cine = mt.names[i] if i < len(mt.names) else ''
            checks.append(('no intro cutscene inherited',
                           cine in ('', 'None'), cine.rsplit('/', 1)[-1][:46]))
            ent = mt.field(mrow, 'bRequiresEntitlement')
            checks += entitlement_checks(
                files, report, [bytes(mt.field(r, 'Guid').payload)[:16] for r in mt.rows],
                bool(ent and ent.bool_value))

    # Every patch to a shipped table must be strictly additive, and the only
    # thing added may be our own mission. Anything else would hand a player
    # content they have not bought.
    ours = set()
    stem_m = report['mission_path'][len('/Game'):]
    if 'OMD/Content' + stem_m + '.uasset' in files:
        mt0 = missionproto.Table(load(stem_m))
        # our mission and, when one was built, its Endless twin
        ours = {bytes(mt0.field(r, 'Guid').payload)[:16] for r in mt0.rows}

    def additive(path, table_name, array_field):
        """Shipped rows keep every entry they had, and gain only our mission.

        `path` is the asset path; the key inside the pak drops the /Game root.
        """
        stem = path[len('/Game'):]
        if 'OMD/Content' + stem + '.uasset' not in files:
            return False
        before = missionproto.read(path)
        after = missionproto.Table(load(stem))
        bad = []
        for r in after.rows:
            fa = after.field(r, array_field)
            rb = before.row(r['name'])
            fb = before.field(rb, array_field) if rb else None
            if fa is None or fb is None:
                continue
            _na, _ta, ia = after.struct_array(fa)
            _nb, _tb, ib = before.struct_array(fb)
            if ia[:len(ib)] != ib:
                bad.append('%s reordered or lost entries' % r['name'])
                continue
            # table-wide: a row with an empty array cannot say where the Guid
            # sits, and an Endless mission's MissionUnlocks ships empty
            at = before.array_guid_offset(array_field, known_guids())
            for extra in ia[len(ib):]:
                if not ours:
                    bad.append('%s gained an entry and we have no Guid to '
                               'compare' % r['name'])
                elif at is None or extra[at:at + 16] not in ours:
                    # searching the whole element would pass a Guid written at
                    # the wrong offset, which is how a bad writer slipped by
                    bad.append('%s gained an entry whose Guid is not ours at '
                               'offset %s' % (r['name'], at))
        checks.append(('%s %s gains only our mission'
                       % (table_name, array_field), not bad,
                       '; '.join(bad[:2]) or 'unchanged otherwise'))
        return True

    # An entitlement's MissionUnlocks is a GRANT: an entry there hands the
    # holder a mission for free. We write gates, never grants, so this array is
    # required to come out byte-identical rather than merely additive.
    ent_stem = ENTITLEMENTS_TABLE[len('/Game'):]
    if 'OMD/Content' + ent_stem + '.uasset' in files:
        before = missionproto.read(ENTITLEMENTS_TABLE)
        after = missionproto.Table(load(ent_stem))
        moved = [r['name'] for r in after.rows
                 if before.row(r['name']) is not None
                 and bytes(after.field(r, 'MissionUnlocks').payload)
                 != bytes(before.field(before.row(r['name']),
                                       'MissionUnlocks').payload)]
        checks.append(('no entitlement grants anything new', not moved,
                       ', '.join(moved) or 'MissionUnlocks byte-identical'))

    touched = 0
    for path, label, field_name in (
            (ENTITLEMENTS_TABLE, 'entitlements:', 'MissionEntitlements'),
            (ENTITLEMENTS_TABLE, 'entitlements:', 'MissionUnlocks'),
            (missionproto.TABLE, 'shipped missions:', 'MissionUnlocks'),
            (missionproto.CAMPAIGN_TABLE, 'campaigns:', 'MissionList')):
        touched += 1 if additive(path, label, field_name) else 0
    # a patched shipped table with no additive check run would go unexamined
    patched = sum(1 for k in files
                  if k.endswith('.uasset') and '/Mods/' not in k and '/CustomMaps/' not in k)
    checks.append(('every patched shipped table was checked',
                   touched >= patched - 1,
                   '%d shipped files, %d array checks' % (patched, touched)))

    if runtime:
        checks.append(('the registry is left to OrcsMustUI: no BP_OMDProtoManager in the pak',
                       not any('BP_OMDProtoManager' in k for k in files), ''))
    else:
        checks.append(('mod mission table registered',
                       '%s_MissionProtos' % mod in resolved, '%d tables' % len(entries)))
        checks.append(('it is a preload dependency', entries[-1] in mgr.preload, ''))
    assets = [(report['mission_path'][len('/Game'):], 'mission table'),
              (missionproto.CAMPAIGN_TABLE[len('/Game'):], 'campaign table')]
    if not runtime:
        assets.append((protoreg.MANAGER[len('/Game'):], 'proto manager'))
    for stem, label in assets:
        p = load(stem)
        raw = files['OMD/Content' + stem + '.uasset']
        body = files['OMD/Content' + stem + '.uexp']
        checks.append(('%s: header consistent' % label,
                       p.summary_tail_parsed and p.total_header_size == len(raw)
                       and (not p.preload_off or
                            p.preload_off + p.preload_count * 4 == p.total_header_size)
                       and (not p.bulk_start_off or
                            p.bulk_start_off == p.total_header_size + len(body) - 4), ''))
    checks.append(read_back(files))
    return checks


def read_back(files):
    """Parse every built package the way the engine does, not the way we wrote it.

    The header checks above compare our own numbers against our own numbers. This
    reads the property stream instead and insists each tag consumed exactly the
    size it declared, which is the check the game performs and the only one that
    sees a size field left stale by an edit.
    """
    bad = []
    for key in sorted(files):
        if not key.endswith('.uasset') or key[:-7] + '.uexp' not in files:
            continue
        name = key[:-7].rsplit('/', 1)[-1]
        table = name.endswith('Protos') or name[:-1].endswith('_Content')
        bad += pkgcheck.problems(files[key], files[key[:-7] + '.uexp'], name,
                                 'table' if table else 'object')
    return ('every package reads back as the game reads it', not bad,
            bad[0] if bad else '')


def verify(files, report):
    """Read every asset back out of the built set and check it."""
    checks = []

    def ck(name, ok, detail=''):
        checks.append((name, ok, detail))

    def load(stem):
        a = files['OMD/Content' + stem + '.uasset']
        b = files['OMD/Content' + stem + '.uexp']
        p = Package(a, b)
        p.load_payloads()
        return p

    from riftlord.missions import missionscramble, missionunlocks
    if report.get('listed_in'):
        return verify_into(files, report) + registry_checks(files) + \
            missionscramble.checks(files, report) + missionunlocks.checks(files, report)
    mod = report['mission_row'].split('_')[0]
    mp = load(report['mission_path'][len('/Game'):])
    t = missionproto.Table(mp)
    # The mod's own rows and no others. An Endless variation is a second
    # mission in its own right, so when one was asked for it belongs here too --
    # the check used to name only the first and refused every build that had a
    # twin.
    mine = [report['mission_row']]
    if report.get('endless_row'):
        mine.append(report['endless_row'])
    ck("mod mission table holds only this mission's row(s)",
       [r['name'] for r in t.rows] == mine,
       '%d row(s): %s' % (len(t.rows), ', '.join(r['name'] for r in t.rows)))
    ck('mission table renamed',
       [s for s, _h in mp.names][struct.unpack_from('<I', mp.exports[0]['meta'], 16)[0]]
       == report['mission_path'].rsplit('/', 1)[-1])

    own_campaign = report['campaign_row'] is not None
    if own_campaign:
        cp = load(report['campaign_path'][len('/Game'):])
        ct = missionproto.Table(cp)
        ck("mod campaign table holds only this campaign's row",
           [r['name'] for r in ct.rows] == [report['campaign_row']])
        crow = ct.rows[0]
        cnt, _tag, items = ct.struct_array(ct.field(crow, 'MissionList'))
        ck('campaign lists one mission', cnt == 1, '%d entries' % cnt)
        ck('and it is our mission guid',
           items[0][-24:-8] == bytes(t.field(t.rows[0], 'Guid').payload))
    else:
        if report.get('endless_extension'):
            ep = load(report['endless_extension'][len('/Game'):])
            et = missionproto.Table(ep)
            twin = t.row(report.get('endless_row') or '')
            _n, _t, listed = et.struct_array(et.field(et.rows[0], 'MissionList')) \
                if et.rows else (0, None, [])
            ck('the Endless extension holds one %s row' % ENDLESS_EXTENSION,
               [r['name'] for r in et.rows] == [ENDLESS_EXTENSION])
            stock = missionproto.read(missionproto.CAMPAIGN_TABLE)
            shipped = bytes(stock.field(stock.row(ENDLESS_CAMPAIGN), 'Guid').payload)[:16]
            # OrcsMustUI skips a row carrying the game's own Endless Guid, and its
            # missions then appear nowhere
            ck('and it has a new Guid, not the shipped Endless one',
               bool(et.rows) and bytes(et.field(et.rows[0], 'Guid').payload)[:16] != shipped)
            #: a mission placed in Endless: its own row is its Endless entry
            if report.get('endless_only'):
                twin = t.row(report['mission_row'])
                ck('a mission placed in Endless has no twin', not report.get('endless_row'))
                chars = t.field(twin, 'DefaultCharacters') if twin is not None else None
                ck('and plays with no DefaultCharacters',
                   twin is not None and (chars is None or t.struct_array(chars)[0] == 0))
            ck('and it lists the Endless %s' % ('row' if report.get('endless_only')
                                                else 'twin'),
               twin is not None and [it[-24:-8] for it in listed]
               == [bytes(t.field(twin, 'Guid').payload)[:16]])
        else:
            ck('no campaign table in the pak',
               not any(k.endswith('_CampaignProtos.uasset') for k in files))

    # header integrity: a stale offset here crashes the game on start rather
    # than failing to load, so it is checked on every asset the mod ships
    runtime = report.get('runtime_registry')
    assets = [((report['mission_path'][len('/Game'):]), 'mission table')]
    if not runtime:
        assets.append((protoreg.MANAGER[len('/Game'):], 'proto manager'))
    if own_campaign:
        assets.insert(1, (report['campaign_path'][len('/Game'):], 'campaign table'))
    for stem, label in assets:
        p = load(stem)
        raw = files['OMD/Content' + stem + '.uasset']
        body = files['OMD/Content' + stem + '.uexp']
        ck('%s: summary tail parses' % label, p.summary_tail_parsed)
        ck('%s: header size matches the file' % label,
           p.total_header_size == len(raw))
        ck('%s: preload table ends at the header end' % label,
           not p.preload_off or
           p.preload_off + p.preload_count * 4 == p.total_header_size)
        ck('%s: bulk offset matches header plus exports' % label,
           not p.bulk_start_off or
           p.bulk_start_off == p.total_header_size + len(body) - 4)

    if runtime:
        ck('the registry is left to OrcsMustUI: no BP_OMDProtoManager in the pak',
           not any('BP_OMDProtoManager' in k for k in files))
    else:
        mgr = load(protoreg.MANAGER[len('/Game'):])
        _c, _a, _h, entries = protoreg.find_array(mgr)
        resolved = [protoreg.resolve(mgr, e) for e in entries]
        ours = 2 if own_campaign else 1
        ck('registry grew by %d' % ours, len(entries) == 34 + ours, '%d tables' % len(entries))
        ck('mod tables registered',
           '%s_MissionProtos' % mod in resolved and
           (not own_campaign or '%s_CampaignProtos' % mod in resolved))
        ck('stock tables still registered',
           'MissionProtos' in resolved and 'CampaignProtos' in resolved)
        ck('mod tables are preload dependencies of the CDO',
           all(e in mgr.preload for e in entries[-ours:]))
    if own_campaign:
        ck('our campaign does not gate missions behind unlocks',
           ct.field(crow, 'bUseMissionUnlocks').bool_value == 0)
    ck('shipped CampaignProtos is not in the pak',
       not any('Data/Campaign/CampaignProtos' in k for k in files))
    ck('shipped MissionProtos is not in the pak',
       not any('Data/Campaign/MissionProtos' in k for k in files))
    gate = t.field(t.rows[0], 'bRequiresEntitlement') if t.rows else None
    checks += entitlement_checks(files, report,
                                 [bytes(t.field(r, 'Guid').payload)[:16] for r in t.rows],
                                 bool(gate and gate.bool_value))
    ck(*read_back(files))
    return checks + registry_checks(files) + missionscramble.checks(files, report) + \
        missionunlocks.checks(files, report)


def registry_checks(files):
    """registry_problems within the pak, as verify's (name, ok, detail) rows; the
    installed-pak clash test needs the pak's name, so the install steps run it."""
    bad = registry_problems(files, installed=False)
    return [('tables registrable: mod folder form, prefixed rows, unique Guids',
             not bad, '; '.join(bad[:3]))]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--mod', default='MyMod')
    ap.add_argument('--name', default='My Custom Map')
    ap.add_argument('--campaign-name', default='Custom Maps')
    ap.add_argument('--level', required=True, metavar='/Game/Maps/...')
    ap.add_argument('--par', type=float, default=600.0)
    ap.add_argument('--war', action='store_true')
    ap.add_argument('--with-level', metavar='MAP',
                    help='copy this level and give it a new coordinator, '
                         'so the mission plays waves we authored')
    ap.add_argument('--fill-unit', default='Kobold',
                    help='unit every existing wave is filled with')
    ap.add_argument('--last-unit', default='LizardmanRanger',
                    help='unit the appended final wave spawns')
    ap.add_argument('--wave', action='append', default=[],
                    metavar='N:UNIT[,UNIT...]',
                    help='repeatable; the units an existing wave spawns')
    ap.add_argument('--new-wave', metavar='UNIT[,UNIT...]',
                    help='the units the appended final wave spawns')
    ap.add_argument('--no-endless', action='store_true',
                    help='do not also add the mission to Endless mode')
    ap.add_argument('--no-campaign', action='store_true',
                    help='list the mission in no campaign: a custom map, shown in '
                         "OrcsMustUI's Custom Maps tab (implies --no-endless)")
    ap.add_argument('--slot', metavar='auto',
                    help="a custom map: the mission table in the mod's table folder, "
                         'which OrcsMustUI registers from the pak, in no campaign, '
                         'and no BP_OMDProtoManager in the pak (numbered slots are retired)')
    ap.add_argument('--campaign-tab', action='store_true',
                    help='with --slot: keep the campaign, in the mod\'s table folder, '
                         "which OrcsMustUI's hub shows as a separate tab")
    ap.add_argument('--runtime-registry', action='store_true',
                    help='ship no BP_OMDProtoManager: OrcsMustUI registers the '
                         "mod's tables as the game starts, so map mods stop "
                         'replacing each other\'s registry')
    ap.add_argument('--into-campaign', metavar='ROW',
                    help='list the mission in a SHIPPED campaign instead of a new '
                         'one; patches that stock table, so not additive')
    ap.add_argument('--out', default='900-CustomMission_P.pak')
    args = ap.parse_args()

    slot = None
    if args.slot is not None:
        if args.slot != 'auto':
            raise SystemExit('numbered slots are retired; use --slot auto (the mod\'s '
                             'table folder)')
        slot = slot_for(args.mod)
        if args.campaign_tab:
            args.no_endless = True
        else:
            args.no_campaign = True
    elif args.campaign_tab:
        raise SystemExit('--campaign-tab needs --slot')
    files, report = build(args.mod, args.name, args.level, args.par, args.war,
                          None if args.no_campaign else args.campaign_name,
                          args.into_campaign,
                          not (args.no_endless or args.no_campaign),
                          args.with_level, args.fill_unit, args.last_unit,
                          {int(w.split(':')[0]): w.split(':')[1].split(',')
                           for w in args.wave},
                          args.new_wave.split(',') if args.new_wave else None,
                          registry=not args.runtime_registry, slot=slot)
    print('mission row   %s' % report['mission_row'])
    if report.get('slot') is not None:
        print('tables        %s  (%s)' % (report['slot'], report['mission_path']))
    print('campaign row  %s' % report['campaign_row'])
    if report.get('listed_in'):
        print('listed in     %s' % report['listed_in'])
    if report.get('unlocked'):
        print('unlocked      %s' % report['unlocked'])
    if report.get('level_from'):
        print('level copy    %s' % report['level_from'])
        print('coordinator   %s' % report['coordinator'])
    if report.get('featured'):
        print('hub preview   %s' % report['featured'])
    if report.get('runtime_registry'):
        print('registry      left to OrcsMustUI (no BP_OMDProtoManager in the pak)')
    else:
        print('registry      %d tables' % report['registry_size'])
    for p in report['paths']:
        print('   %s  (%d bytes)' % (p, len(files[p])))

    print()
    failures = 0
    for name, ok, detail in verify(files, report):
        print('  %-4s %-46s %s' % ('OK' if ok else 'FAIL', name, detail))
        failures += 0 if ok else 1
    if failures:
        raise SystemExit('%d check(s) failed; not writing a pak' % failures)

    pakv4.write(args.out, files)
    print('\nwrote %s (%d bytes)' % (args.out, os.path.getsize(args.out)))
    for gone in remove_loose_sidecars(args.out):
        print('removed old sidecar %s' % gone)


if __name__ == '__main__':
    main()
