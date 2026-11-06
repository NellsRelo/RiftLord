"""A mission's place in Scramble (Random Mode) runs.

Scramble draws each tier's mission from that tier's MissionCollection in
/Game/Data/RandomModeBucketProtos: {Weight, Guid (the entry's own), Mission, Difficulty}.
A mission joins with an extension of that table inside its own pak,
/Game/Mods/<mod>/Extends/<mod>_RandomModeBucketProtos: a copy of each tier row it joins,
named exactly as the live row, whose MissionCollection holds one entry naming the
mission and every other list empty. ProtoRegistry appends the entry to the live tier
(OrcsMustUI Docs/Registry.md, extension tables).

The live table is the shipped one, or the one an installed pak in ~mods replaces it
with (a Remix pak may have 20 tiers under other names).

Tiers a mod adds: a pak's replacement table (OMD/Content/Data/RandomModeBucketProtos,
Campaign Scramble's route, enabled or `.pak.disabled`), or a registered table of bucket
rows (OMD/Content/Mods/<Key>/Tables/*), whose new row names ProtoRegistry registers as
tiers after the live ones. A mission may also join a tier by name that no installed mod
defines. ProtoRegistry leaves out an extension row that matches no tier, so such a row
stays dormant until a mod defines the tier, so the mods that define joined tiers are not
in the build report's `requires`.

A record keeps `scramble` as {tiers: [tier row name, ...], rows: {tier: {difficulty,
weight}}}: the tiers the mission joins, in order, any name accepted (a number is
Tier_NN), none until the designer names one; rows hold only values that differ from the
tier's default (difficulty: the one most of the tier's entries use; weight:
DEFAULT_WEIGHT). A record saved before `tiers` holds {tier: ALL or one tier's row name,
rows, extra: {tier: defining mod or ''}}: one tier is read as `tiers` [that tier]; ALL
is kept and joins every listed tier, extra's included. No `scramble`, or {on: false},
is a mission that does not join.

    python3 missionscramble.py                 # every known tier and its defaults
"""

import collections
import hashlib
import math
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

BUCKET_TABLE = '/Game/Data/RandomModeBucketProtos'
#: The bucket table's entry inside a pak.
BUCKET_ENTRY = 'OMD/Content/Data/RandomModeBucketProtos.uasset'
#: A record's tier choice, saved before `tiers`, that joins every listed tier.
ALL = 'All'
#: A joined tier's weight unless the record gives one (every vanilla mission's but three).
DEFAULT_WEIGHT = 10.0
#: The largest weight a record may give.
MAX_WEIGHT = 1000000.0
#: EGameDifficulty values a mission may be drawn at, with their labels.
DIFFICULTIES = (('Apprentice', 'Apprentice'), ('WarMage', 'War Mage'),
                ('RiftLord', 'Rift Lord'))
#: The difficulty of a tier whose live list is empty and follows no other tier.
FALLBACK_DIFFICULTY = 'WarMage'
ENUM = 'EGameDifficulty'
#: The bucket row's lists; only MissionCollection holds anything in an extension.
LISTS = ('BuffCollection', 'DebuffCollection', 'HiddenModifierCollection',
         'MissionCollection')
#: A pak that is installed but switched off.
DISABLED = '.pak.disabled'
#: A registered table's entry inside a pak: (mod key, table name).
TABLE_ENTRY = re.compile(r'^OMD/Content/Mods/([^/]+)/Tables/([^/]+)\.uasset$')
#: The row struct a bucket table's header names.
BUCKET_STRUCT = b'OMDRandomModeBucketProto'
#: The most tiers a record may add by name, and the most it may join.
MOST_EXTRA = 64
MOST_TIERS = 64


def endless(name):
    """True for a tier named Endless: never offered or joined. (Endless is a difficulty,
    EGameDifficulty::Endless; a Scramble entry plays at its own tier difficulty, so any
    mission, an Endless one included, joins Scramble as a round that ends.)"""
    return 'endless' in str(name or '').lower()


# -- the live table -------------------------------------------------------------

_LIVE = {}


def _paks_folder(name):
    """A folder under the Paks folder Supported Mods reads (RIFTLORD_SUPPORTED_PAKS, else
    the game's), or None without one."""
    from riftlord.core import supportedmods
    root = supportedmods.paks_root()
    return os.path.join(root, name) if root else None


def live_source(mods=None):
    """The installed pak in ~mods (subfolders too) whose RandomModeBucketProtos is the one
    the game loads, or None for the shipped table. Of several, the last in path order."""
    from riftlord.formats.ue import pak
    root = mods or _paks_folder('~mods')
    if not root or not os.path.isdir(root):
        return None
    found = None
    for base, dirs, names in os.walk(root):
        dirs.sort()
        for n in sorted(names):
            if not n.endswith('.pak'):
                continue
            path = os.path.join(base, n)
            try:
                st = os.stat(path)
                key = (path, st.st_mtime, st.st_size)
                if key not in _LIVE:
                    _LIVE[key] = any(f.endswith(BUCKET_ENTRY) for f in pak.Pak(path).files)
                if _LIVE[key]:
                    found = path
            except (Exception, SystemExit):          # noqa: BLE001
                continue
    return found


def live_table(mods=None):
    """(missionproto.Table of the live bucket table, the pak it came from or None)."""
    from riftlord.missions import scrambletable
    source = live_source(mods)
    return scrambletable.bucket_table(source), source


def _difficulty(value):
    """'EGameDifficulty::RiftLord' -> 'RiftLord'."""
    return str(value or '').split('::')[-1]


def _table_rows(t):
    """[{name, form, guid, difficulty}] of a bucket table in row order: form is the row
    name's FName as stored (string, number); difficulty the one most of the row's entries
    use, else the previous row's, else FALLBACK_DIFFICULTY."""
    from riftlord.missions import scrambletable
    ua, ux = t.write()
    rows = scrambletable.read_table(ua, ux)
    out, last = [], FALLBACK_DIFFICULTY
    for r in t.rows:
        vals = rows.get(r['name']) or {}
        counts = collections.Counter(_difficulty(e.get('Difficulty'))
                                     for e in vals.get('MissionCollection') or [])
        known = [(n, d) for d, n in counts.items() if d in dict(DIFFICULTIES)]
        if known:
            last = max(known)[1]
        base = t.names[r['name_index']] if 0 <= r['name_index'] < len(t.names) else r['name']
        out.append({'name': r['name'], 'form': (base, r['name_number']),
                    'guid': str(vals.get('Guid') or '')[:32], 'difficulty': last})
    return out


def tier_defaults(t):
    """[(tier row name, default difficulty)] of a bucket table, in tier order."""
    return [(r['name'], r['difficulty']) for r in _table_rows(t)]


def fname_forms(name):
    """[(string, number)]: how an FName shown as `name` may be stored. UE splits a final
    '_' + digits (no leading 0 unless one digit, below 2^31) into a number + 1 when it
    makes the name from text; a table written without that split keeps number 0."""
    m = re.match(r'^(.+)_(0|[1-9][0-9]{0,9})$', name)
    if m and int(m.group(2)) < 2 ** 31 - 1:
        return [(name, 0), (m.group(1), int(m.group(2)) + 1)]
    return [(name, 0)]


def mod_label(path, key=None):
    """A tier source's name: the mod key of a registered table, else the pak's file name
    without its load-order number, '_P' and extension."""
    if key:
        return key
    stem = os.path.basename(path)
    for end in (DISABLED, '.pak'):
        if stem.endswith(end):
            stem = stem[:-len(end)]
            break
    stem = re.sub(r'^\d+[-_]', '', stem)
    return re.sub(r'_P$', '', stem) or os.path.basename(path)


def tier_paks(mods=None):
    """Every pak and switched-off pak that may define tiers, in path order: under `mods`,
    else the Paks folder's ~mods and LogicMods (subfolders too)."""
    roots = [mods] if mods else [_paks_folder('~mods'), _paks_folder('LogicMods')]
    out = []
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for base, dirs, names in os.walk(root):
            dirs.sort()
            out += [os.path.join(base, n) for n in sorted(names)
                    if n.endswith('.pak') or n.endswith(DISABLED)]
    return out


#: {pak path: ((size, mtime_ns), [{kind, mod, table, rows}])}
_PAK_TIERS = {}


def _read_pak_tiers(path):
    """[{kind: 'replace'|'add', mod, table, rows: _table_rows}] of the bucket tables in one
    pak: its replacement table, and each registered table of bucket rows."""
    from riftlord.build import missionproto
    from riftlord.formats.ue import pak
    from riftlord.formats.ue.uassetio import Package
    p = pak.Pak(path)
    by_inner = {(k[k.index('OMD/'):] if 'OMD/' in k else k): k for k in p.files}
    found = []
    if BUCKET_ENTRY in by_inner:
        found.append(('replace', None, BUCKET_TABLE, BUCKET_ENTRY))
    for inner in sorted(by_inner):
        m = TABLE_ENTRY.match(inner)
        if m:
            found.append(('add', m.group(1), '/Game/Mods/%s/Tables/%s' % m.groups(), inner))
    out = []
    for kind, key, table, inner in found:
        uexp = inner[:-len('.uasset')] + '.uexp'
        if uexp not in by_inner:
            continue
        ua = p.read_file(by_inner[inner])
        if kind == 'add' and BUCKET_STRUCT not in ua:
            continue
        pkg = Package(ua, p.read_file(by_inner[uexp]))
        pkg.load_payloads()
        out.append({'kind': kind, 'mod': mod_label(path, key), 'table': table,
                    'rows': _table_rows(missionproto.Table(pkg))})
    return out


def pak_tiers(path):
    """_read_pak_tiers, cached by the pak's size and modification time; [] for a pak
    that does not read."""
    try:
        st = os.stat(path)
    except OSError:
        _PAK_TIERS.pop(path, None)
        return []
    stamp = (st.st_size, st.st_mtime_ns)
    hit = _PAK_TIERS.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        got = _read_pak_tiers(path)
    except (Exception, SystemExit):                  # noqa: BLE001
        got = []
    _PAK_TIERS[path] = (stamp, got)
    return got


def _clean_extra(raw):
    """{tier: defining mod or ''} from a record's or page's `extra`."""
    out = {}
    if isinstance(raw, dict):
        items = raw.items()
    elif isinstance(raw, (list, tuple)):
        items = ((n, '') for n in raw)
    else:
        items = ()
    for name, mod in items:
        name = ' '.join(str(name or '').split())[:200]
        if not name or name == ALL or endless(name) or name in out:
            continue
        out[name] = ' '.join(str(mod or '').split())[:200]
        if len(out) >= MOST_EXTRA:
            break
    return out


def tiers(mods=None, extra=None):
    """Every tier a mission can join, in order: the live table's, the shipped table's
    the live one lacks, each mod's (replacement tables not live, then registered tables,
    in pak order), then `extra` ({tier: mod or ''}) names none of those hold. Each is
    {name, difficulty, mod (None for a shipped name), enabled (the defining mod's pak is
    on), live (the game rolls it now), forms [(string, number)], guid, clash [mods whose
    registered table also names a live tier]}. Endless tiers are left out."""
    from riftlord.missions import scrambletable
    t, source = live_table(mods)
    shipped = _table_rows(scrambletable.bucket_table()) if source else _table_rows(t)
    shipped_names = {r['name'] for r in shipped}
    out, by_name = [], {}

    def add(row, mod, enabled, live):
        name = row['name']
        if endless(name):
            return
        got = by_name.get(name)
        if got is None:
            got = by_name[name] = {'name': name, 'difficulty': row['difficulty'],
                                   'mod': None if name in shipped_names else mod,
                                   'shipped': name in shipped_names,
                                   'enabled': enabled, 'live': live, 'forms': [],
                                   'guid': row.get('guid') or '', 'clash': []}
            out.append(got)
        elif live and not got['live']:
            got.update(mod=got['mod'] and mod, enabled=enabled, live=True,
                       guid=row.get('guid') or got['guid'])
        if row['form'] not in got['forms']:
            got['forms'].append(row['form'])

    live_label = mod_label(source) if source else None
    for row in _table_rows(t) if source else shipped:
        add(row, live_label, True, True)
    for row in shipped:
        add(row, None, True, False)
    found = [(path, table) for path in tier_paks(mods) for table in pak_tiers(path)]
    for path, table in found:
        if table['kind'] == 'replace' and path != source:
            for row in table['rows']:
                add(row, table['mod'], path.endswith('.pak'), False)
    for path, table in found:
        if table['kind'] != 'add':
            continue
        on = path.endswith('.pak')
        for row in table['rows']:
            got = by_name.get(row['name'])
            if got is not None and got['live'] and on:
                got['clash'].append(table['mod'])
            add(row, table['mod'], on, on)
    for name, mod in _clean_extra(extra).items():
        if name not in by_name:
            add({'name': name, 'form': fname_forms(name)[0], 'guid': '',
                 'difficulty': FALLBACK_DIFFICULTY}, mod or None, False, False)
            by_name[name]['forms'] = fname_forms(name)
    return out


def view(mods=None):
    """What the Details dialog suggests: {tiers: [{name, difficulty, mod, enabled, live}],
    difficulties: [{value, label}], weight, fallback, source (the replacing pak's file
    name or None)}."""
    _t, source = live_table(mods)
    return {'tiers': [{k: tier[k] for k in ('name', 'difficulty', 'mod', 'enabled', 'live')}
                      for tier in tiers(mods)],
            'difficulties': [{'value': v, 'label': lab} for v, lab in DIFFICULTIES],
            'weight': DEFAULT_WEIGHT, 'fallback': FALLBACK_DIFFICULTY,
            'source': os.path.basename(source) if source else None}


# -- the record's value ----------------------------------------------------------

def tier_name(text):
    """A typed tier: spaces collapsed, at most 200 characters; digits only are Tier_NN
    (at least two digits)."""
    name = ' '.join(str(text or '').split())[:200]
    return 'Tier_%s' % name.zfill(2) if name.isdigit() else name


def clean(raw):
    """A record's `scramble` from what a page sent: {tiers, rows}, or {tier: ALL, rows,
    extra} for a value saved with ALL, or None when the mission does not join (also for a
    saved single tier that is Endless: Scramble never offers one). `tiers` leaves out
    empty, repeated, ALL and Endless names; rows are kept only for joined tiers."""
    if not isinstance(raw, dict) or not raw.get('on', True):
        return None
    if isinstance(raw.get('tiers'), (list, tuple)):
        names = []
        for text in raw['tiers']:
            name = tier_name(text)
            if name and name != ALL and not endless(name) and name not in names:
                names.append(name)
            if len(names) >= MOST_TIERS:
                break
        rows = _clean_rows(raw.get('rows'))
        return {'tiers': names, 'rows': {n: r for n, r in rows.items() if n in names}}
    tier = ' '.join(str(raw.get('tier') or ALL).split())[:200] or ALL
    if endless(tier):
        return None
    rows = _clean_rows(raw.get('rows'))
    if tier != ALL:
        return {'tiers': [tier], 'rows': {n: r for n, r in rows.items() if n == tier}}
    out = {'tier': ALL, 'rows': rows}
    extra = _clean_extra(raw.get('extra'))
    if extra:
        out['extra'] = extra
    return out


def _clean_rows(raw):
    """{tier: {difficulty?, weight?}} of a page's or record's rows: only values that differ
    from the tier's default, no Endless tier."""
    rows = {}
    for name, got in raw.items() if isinstance(raw, dict) else ():
        if not isinstance(got, dict) or endless(name):
            continue
        row = {}
        if got.get('difficulty') in dict(DIFFICULTIES):
            row['difficulty'] = got['difficulty']
        try:
            weight = float(got.get('weight'))
        except (TypeError, ValueError):
            weight = None
        if weight is not None and math.isfinite(weight) and 0 < weight <= MAX_WEIGHT \
                and weight != DEFAULT_WEIGHT:
            row['weight'] = weight
        if row:
            rows[str(name)[:200]] = row
    return rows


def value(stored):
    """A record's `scramble` as the Details dialog edits it: {on, tiers, rows}, or
    {on, tier: ALL, rows, extra} for a value saved with ALL (the dialog lists every tier
    it joins)."""
    got = clean(stored)
    return dict(got, on=True) if got else {'on': False, 'tiers': [], 'rows': {}}


def of_record(rec):
    """A Missions record's `scramble`: its Onslaught Editor spec's for a mission that
    editor builds, else the record's own."""
    rec = rec or {}
    spec = rec.get('spec') if isinstance(rec.get('spec'), dict) else None
    map_editor = bool(rec.get('level') and isinstance(rec.get('plan'), dict))
    if not map_editor and spec is not None and 'scramble' in spec:
        return clean(spec['scramble'])
    return clean(rec.get('scramble'))


def _listed(scramble, known):
    """{tier: default difficulty} in order: `known` ([(name, difficulty)] or tiers()
    dicts), then the record's extra tiers and joined tiers that `known` lacks."""
    out = {}
    for t in known:
        name, difficulty = (t['name'], t['difficulty']) if isinstance(t, dict) else t
        if not endless(name):
            out.setdefault(name, difficulty)
    for name in list(scramble.get('extra') or {}) + list(scramble.get('tiers') or []):
        if name != ALL and not endless(name):
            out.setdefault(name, FALLBACK_DIFFICULTY)
    return out


def joined(scramble, known):
    """([(tier, difficulty, weight)], [rows the record sets for tiers it does not join])
    for a record's `scramble` against `known` (tiers(), or [(name, default difficulty)]).
    Each of `tiers` is joined whether or not a mod defines it (its row stays dormant until
    one does); a value saved with ALL joins every listed tier."""
    scramble = clean(scramble)
    if scramble is None:
        return [], []
    listed = _listed(scramble, known)
    names = list(listed) if scramble.get('tier') == ALL else scramble['tiers']
    out = []
    for name in names:
        row = scramble['rows'].get(name) or {}
        out.append((name, row.get('difficulty') or listed[name],
                    float(row.get('weight') or DEFAULT_WEIGHT)))
    unused = sorted(n for n in scramble['rows'] if n not in names)
    return out, unused


# -- the extension table ---------------------------------------------------------

def extension_path(mod):
    return '/Game/Mods/%s/Extends/%s_RandomModeBucketProtos' % (mod, mod)


def _entry_key(mod):
    """The extension's .uasset entry inside a pak."""
    return 'OMD/Content' + extension_path(mod)[len('/Game'):] + '.uasset'


def entry_guid(mod, tier):
    """A joined tier's entry Guid, stable for its mod and tier: 16 bytes as stored."""
    digest = hashlib.md5(('scramble-mission:%s:%s' % (mod, tier)).lower().encode()).digest()
    return struct.pack('<4I', *struct.unpack('>4I', digest))


def row_guid(mod, tier, info):
    """The Guid an extension row for `tier` carries, 16 bytes as stored: the tier row's
    own when a known table defines it, else one stable for the mod and tier name (never
    another tier's)."""
    if len((info or {}).get('guid') or '') == 32:
        return bytes.fromhex(info['guid'])
    digest = hashlib.md5(('scramble-tier-row:%s:%s' % (mod, tier)).lower().encode()).digest()
    return struct.pack('<4I', *struct.unpack('>4I', digest))


def _shape(t):
    """A MissionCollection element of `t` to copy, or None when every list is empty."""
    for r in t.rows:
        f = t.field(r, 'MissionCollection')
        if f is not None:
            items = t.struct_array(f)[2]
            if items:
                return items[0]
    return None


def _element(t, shape, guid, mission_guid, difficulty, weight):
    """A FOMDRandomModeLevelEntry: `shape` with its Guid, Mission, Difficulty and Weight."""
    from riftlord.missions import scrambletable
    element = bytearray(shape)
    index = t.pkg.add_name('%s::%s' % (ENUM, difficulty))
    t.names = [s for s, _h in t.pkg.names]
    for f, at in scrambletable._sub_fields(t, bytes(element))[0]:
        start = at + len(f.header)
        if f.name == 'Guid':
            element[start:start + 16] = guid
        elif f.name == 'Mission':
            for g, gat in scrambletable._sub_fields(t, bytes(f.payload))[0]:
                if g.name == 'Guid':
                    o = start + gat + len(g.header)
                    element[o:o + 16] = mission_guid
        elif f.name == 'Difficulty':
            struct.pack_into('<ii', element, start, index, 0)
        elif f.name == 'Weight':
            struct.pack_into('<f', element, start, float(weight))
    return bytes(element)


def _row(t, src, form):
    """A copy of row `src` of `t` named by `form` (string, number)."""
    from riftlord.build import missionproto
    index = t.pkg.add_name(form[0])
    t.names = [s for s, _h in t.pkg.names]
    return {'name': form[0] if not form[1] else '%s_%d' % (form[0], form[1] - 1),
            'name_index': index, 'name_number': form[1],
            'fields': [missionproto.Field(f.name, f.kind, f.sub, bytes(f.header),
                                          bytes(f.payload), f.bool_value)
                       for f in src['fields']]}


def _form_of(t, r):
    """A row's name as stored: (string, number)."""
    return (t.names[r['name_index']], r['name_number'])


def tier_report(entries, known):
    """{tier: {mod, shipped, enabled, live, forms, guid, clash}} for joined `entries`,
    from `known` (tiers())."""
    by_name = {t['name']: t for t in known}
    out = {}
    for tier, _d, _w in entries:
        got = by_name.get(tier) or {'mod': None, 'shipped': False, 'enabled': False,
                                    'live': False, 'forms': fname_forms(tier), 'guid': '',
                                    'clash': []}
        out[tier] = {'mod': got['mod'], 'shipped': got['shipped'], 'enabled': got['enabled'],
                     'live': got['live'], 'forms': [list(f) for f in got['forms']],
                     'guid': got['guid'], 'clash': list(got['clash'])}
    return out


def extension(mod, mission_guid, scramble, mods=None):
    """({pak entry: bytes}, report) of the extension that joins mission `mission_guid`
    (16 bytes) to the tiers `scramble` names; no files when it joins none. A tier gets
    one row per FName form it is stored as (two for a typed name UE may split). The
    report holds scramble_path, scramble [(tier, difficulty, weight)], scramble_tiers
    (tier_report) and scramble_source."""
    from riftlord.build import modtable
    report = {'scramble_path': extension_path(mod), 'scramble': [], 'scramble_tiers': {},
              'scramble_source': None}
    got = clean(scramble)
    if got is None:
        return {}, report
    t, source = live_table(mods)
    known = tiers(mods, got.get('extra'))
    entries, _unused = joined(got, known)
    report.update(scramble=[list(e) for e in entries],
                  scramble_tiers=tier_report(entries, known),
                  scramble_source=os.path.basename(source) if source else None)
    if not entries:
        return {}, report
    shape = _shape(t)
    if shape is None:
        from riftlord.missions import scrambletable
        shipped = scrambletable.bucket_table()
        t = shipped if [r['name'] for r in shipped.rows] == [r['name'] for r in t.rows] else t
        shape = _shape(t)
    if shape is None:
        raise SystemExit('the live RandomModeBucketProtos lists no mission to copy an entry from')
    template = t.rows[0]
    rows = []
    for tier, difficulty, weight in entries:
        info = report['scramble_tiers'][tier]
        for form in [tuple(f) for f in info['forms']]:
            src = next((r for r in t.rows if _form_of(t, r) == form), template)
            row = _row(t, src, form)
            for name in LISTS:
                field = t.field(row, name)
                if field is None:
                    continue
                _n, tag, _items = t.struct_array(field)
                items = [_element(t, shape, entry_guid(mod, tier), bytes(mission_guid)[:16],
                                  difficulty, weight)] if name == 'MissionCollection' else []
                field.payload = t._pack_array(tag, items)
            guid = t.field(row, 'Guid')
            if src is template and guid is not None:
                guid.payload = bytearray(row_guid(mod, tier, info))
            rows.append(row)
    t.rows = rows
    path = extension_path(mod)
    modtable.rename_package(t.pkg, BUCKET_TABLE, BUCKET_TABLE.rsplit('/', 1)[-1],
                            path, path.rsplit('/', 1)[-1])
    ua, ux = t.write()
    stem = 'OMD/Content' + path[len('/Game'):]
    return {stem + '.uasset': ua, stem + '.uexp': ux}, report


def apply(files, mod, mission_guid, scramble, mods=None):
    """(`files` with the mod's extension replaced by one for `scramble`, or taken out,
    report): what a changed setting writes into a built pak."""
    key = _entry_key(mod)
    out = {k: v for k, v in files.items() if k not in (key, key[:-len('.uasset')] + '.uexp')}
    got, report = extension(mod, mission_guid, scramble, mods)
    out.update(got)
    return out, report


def stored_forms(ua, ux):
    """[(string, number)] of a built table's row names, in row order."""
    from riftlord.build import missionproto
    from riftlord.formats.ue.uassetio import Package
    pkg = Package(ua, ux)
    pkg.load_payloads()
    t = missionproto.Table(pkg)
    return [_form_of(t, r) for r in t.rows]


def problems(files, mod, mission_guid, entries, tier_info=None):
    """What is wrong with the extension in `files`: it must read back holding exactly the
    rows of `entries` [(tier, difficulty, weight)], each with one entry naming the
    mission at that difficulty and weight under its own Guid, every other list empty;
    and it must be absent when `entries` is empty. With `tier_info` (tier_report), the
    rows' stored names must be each tier's forms, in order."""
    from riftlord.missions import scrambletable
    from riftlord.core import pkgcheck
    key = _entry_key(mod)
    uexp = key[:-len('.uasset')] + '.uexp'
    if not entries:
        return ['%s is in the pak, though the mission joins no tier' % extension_path(mod)] \
            if key in files else []
    if key not in files or uexp not in files:
        return ['%s is not in the pak' % extension_path(mod)]
    bad = pkgcheck.problems(files[key], files[uexp], key.rsplit('/', 1)[-1][:-len('.uasset')],
                            'table')
    got = scrambletable.read_table(files[key], files[uexp])
    if list(got) != [tier for tier, _d, _w in entries]:
        bad.append('rows %s, not %s' % (', '.join(got), ', '.join(e[0] for e in entries)))
    if tier_info is not None:
        want = [tuple(f) for tier, _d, _w in entries
                for f in (tier_info.get(tier) or {}).get('forms') or [(tier, 0)]]
        have = stored_forms(files[key], files[uexp])
        if have != want:
            bad.append('row names stored as %s, not %s' % (have, want))
    want_mission = bytes(mission_guid)[:16].hex()
    registered = sorted(k for k in files if TABLE_ENTRY.match(k) and
                        BUCKET_STRUCT in files[k])
    if registered:
        bad.append('the pak registers new tier rows (%s)' % ', '.join(registered))
    for tier, difficulty, weight in entries:
        lists = got.get(tier) or {}
        if tier_info is not None and str(lists.get('Guid') or '')[:32] != \
                row_guid(mod, tier, tier_info.get(tier)).hex():
            bad.append('%s carries Guid %s, not its tier row\'s' % (tier, lists.get('Guid')))
        for name in LISTS:
            have = lists.get(name) or []
            if name != 'MissionCollection':
                if have:
                    bad.append('%s.%s is not empty' % (tier, name))
                continue
            want = [{'Guid': entry_guid(mod, tier).hex(), 'Mission': {'Guid': want_mission},
                     'Difficulty': '%s::%s' % (ENUM, difficulty), 'Weight': float(weight)}]
            have = [{'Guid': e.get('Guid'), 'Mission': e.get('Mission'),
                     'Difficulty': e.get('Difficulty'), 'Weight': float(e.get('Weight') or 0)}
                    for e in have]
            if have != want:
                bad.append('%s.MissionCollection holds %r' % (tier, have))
    return bad


def checks(files, report):
    """makemod.verify's (name, ok, detail) rows for a build's Scramble extension."""
    if 'scramble' not in report:
        return []
    mod = report['mission_row'].rsplit('_', 1)[0]
    guid = bytes.fromhex(report['mission_guid'])
    entries = [tuple(e) for e in report['scramble']]
    bad = problems(files, mod, guid, entries, report.get('scramble_tiers')) + \
        endless_problems(files, report) + tier_problems(report)
    name = ('joins Scramble %s as an Extends table' % ', '.join(e[0] for e in entries)
            if entries else 'joins no Scramble tier')
    return [(name, not bad, '; '.join(bad[:3]))]


def tier_problems(report):
    """What is wrong with where a build's joined tiers come from: each must be in the
    report's scramble_tiers (live, defined by a known mod, or dormant)."""
    if 'scramble_tiers' not in report:
        return []
    info = report['scramble_tiers']
    return ['%s is joined but not reported' % t for t, _d, _w in report.get('scramble') or []
            if t not in info]


def endless_problems(files, report):
    """What would make a Scramble round endless: a joined Endless tier, or an entry at a
    difficulty that is not a tier difficulty (EGameDifficulty::Endless)."""
    entries = [tuple(e) for e in report.get('scramble') or []]
    bad = ['joins the Endless tier %s' % t for t, _d, _w in entries if endless(t)]
    bad += ['%s plays at %s, not a tier difficulty' % (t, d) for t, d, _w in entries
            if d not in dict(DIFFICULTIES)]
    return bad


def describe(report):
    """[(is a warning, console line)] for a build's Scramble report."""
    info = report.get('scramble_tiers') or {}
    lines = []
    for tier, d, w in report.get('scramble') or []:
        got = info.get(tier) or {}
        lines.append((False, 'Scramble: %s at %s, weight %g%s' % (
            tier, dict(DIFFICULTIES).get(d, d), w,
            ' (tier from %s)' % got['mod'] if got.get('mod') else '')))
    for tier, got in info.items():
        if got.get('clash'):
            lines.append((True, 'Scramble: %s is also a tier of %s, so this mission is left out '
                          'of that tier. Pick another tier in its Scramble settings.'
                          % (tier, ', '.join(got['clash']))))
        elif not got.get('live') and got.get('mod'):
            lines.append((True, 'Scramble: %s waits for %s, which is %s. The mission joins '
                          'that tier once %s is in use.' % (
                              tier, got['mod'], 'switched off' if not got.get('enabled')
                              else 'not the tier table in use', got['mod'])))
        elif not got.get('live') and got.get('shipped'):
            lines.append((True, 'Scramble: %s is not in the tier table in use (from %s). The '
                          'mission joins that tier once the game\'s shipped table is in use.'
                          % (tier, report.get('scramble_source'))))
        elif not got.get('live'):
            lines.append((True, 'Scramble: %s waits for a tier mod; no installed mod defines '
                          'it%s. Install the mod that adds it, or pick another tier.'
                          % (tier, ' (the tier table in use is from %s)'
                             % report['scramble_source'] if report.get('scramble_source')
                             else '')))
    return lines


def main():
    v = view()
    print('live table: %s' % (v['source'] or 'shipped'))
    for tier in v['tiers']:
        print('  %-20s %-10s %s' % (tier['name'], tier['difficulty'], ' '.join(filter(None, (
            tier['mod'], 'live' if tier['live'] else 'waiting',
            '' if tier['enabled'] else 'off')))))


if __name__ == '__main__':
    main()
