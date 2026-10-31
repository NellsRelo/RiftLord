"""Hero and item unlocks: what beating a mission grants, written into its MissionProtos row
the way the shipped missions write theirs.

Measured on the shipped MissionProtos (2026-10-31):
- `ItemUnlocks` is TArray<FOMDMissionItemElement>: a tagged struct {bIsPreMission,
  bIsHidden, Item (FOMDSoftProtoPtr: one Guid)}, 182 bytes, the Guid at 150. 17 campaign
  rows use it (Scenario_02 grants Tar Trap: both flags false).
- `CharacterUnlocks` is TArray<FOMDMissionCharacterElement>: {bIsPreMission, Character},
  156 bytes, the Guid at 124. Only Scenario_18_WS uses it (Cygnus, Vorwick, Gabriella,
  Max), and its ItemUnlocks lists their four primary weapons with bIsHidden set: a hero
  comes with its PrimaryWeapon, not shown as a reward of its own.
- bIsPreMission is set on one shipped element (Scenario_01 hands out the Longbow before the
  mission); a grant here is always after it.
- Endless rows (the `_End` twins) grant nothing; neither does a mission placed in Endless here.

Grantable, from the shipped tables and every installed mod's own tables
(OMD/Content/Mods/<key>/Tables, in ~mods and LogicMods, subfolders too):
- items: rows of an OMDTrapProto, OMDWeaponProto or OMDTrinketProto table (FOMDItemProto;
  guardians are traps); heroes: rows of an OMDPlayerCharacterProto table. A mod's
  Extends tables re-declare shipped rows (skins), so they add no choice.
- left out: an item with bRequiresEntitlement or bDevelopmentOnly, one AquiredBy Internal
  (feature flags such as RiftLordUnlock, environment traps, debug rows), one an
  entitlement row's ItemEntitlements lists (a DLC's; shipped EntitlementsProtos, a mod's
  copy of it, or a mod's entitlement tables), and any row with no DisplayName. Every
  shipped item row writes AquiredBy, none as Both, so a row without it is Both (allowed).
  A grant (ItemGrants, CharacterGrants) gates nothing: DLC3 also grants the Crossbow and
  Gabriella, which Scenario_18_WS hands out free.

The list is kept per process and on disk, keyed by the stamps (size, mtime) of the game's
paks and of every installed mod pak, so it is read again when a pak changes. A build
checks each chosen Guid against the list as it is then: one left out is never written,
and one whose mod is not installed is a warning and left out.

    python3 -m riftlord.missions.missionunlocks             # the choices, and what is left out
    python3 -m riftlord.missions.missionunlocks --pak PAK   # what a built mission grants
"""

import argparse
import json
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

#: Item row structs, and the group each is listed under.
ITEM_STRUCTS = {'OMDTrapProto': 'Trap', 'OMDWeaponProto': 'Weapon',
                'OMDTrinketProto': 'Trinket'}
HERO_STRUCT = 'OMDPlayerCharacterProto'
ENTITLEMENT_STRUCT = 'OMDEntitlementProto'
#: The shipped tables, by row struct.
SHIPPED = {'/Game/Data/TrapProtos': 'OMDTrapProto',
           '/Game/Data/WeaponProtos': 'OMDWeaponProto',
           '/Game/Data/TrinketProtos': 'OMDTrinketProto',
           '/Game/Data/PlayerCharacterProtos': HERO_STRUCT}
ENTITLEMENTS = '/Game/Data/EntitlementsProtos'
#: The entitlement array that gates an item: owning the entitlement is the only way to it.
GATE_FIELD = 'ItemEntitlements'
#: The two arrays a grant is written into.
ITEM_FIELD, HERO_FIELD = 'ItemUnlocks', 'CharacterUnlocks'
#: Shipped elements each new one is copied from: (row, array, index).
ITEM_TEMPLATE = ('Scenario_02', ITEM_FIELD, 0)
HERO_TEMPLATE = ('Scenario_18_WS', HERO_FIELD, 0)
#: An AquiredBy value that is never the player's to have.
INTERNAL = 'Internal'
#: AquiredBy when a row does not write it (see the module notes).
AQUIRED_DEFAULT = 'Both'
#: A table of a mod's own, inside its pak: (mod key, table leaf).
TABLE_RE = re.compile(r'^OMD/Content/Mods/([^/]+)/Tables/([^/.]+)\.uasset$')
#: Entitlement tables a mod may carry: its Extends tables (ProtoRegistry 1.6, a Steam<AppId>
#: folder too) and a copy of the shipped table.
ENTITLEMENT_RE = re.compile(r'^OMD/Content/(Mods/[^/]+/Extends/.*_EntitlementsProtos'
                            r'|Data/EntitlementsProtos)\.uasset$')
#: Why a row is left out, as the build's warnings and the command line say it.
WHY = {'entitlement': 'needs an entitlement (bRequiresEntitlement)',
       'dlc': 'is DLC content (an entitlement lists it)',
       'development': 'is development only',
       'internal': 'is internal to the game (AquiredBy Internal)',
       'unnamed': 'has no display name'}
#: Which reasons are a gate: a hero's weapon is still granted when it is only Internal.
GATES = ('entitlement', 'dlc', 'development')
#: Bump when what a scan stores changes, so an old disk cache is not used.
CACHE_VERSION = 1


# -- reading ----------------------------------------------------------------------------

def _guid(table, row):
    f = table.field(row, 'Guid')
    return bytes(f.payload)[:16].hex() if f is not None and len(f.payload) >= 16 else None


def _flag(table, row, name):
    f = table.field(row, name)
    return bool(f is not None and f.bool_value)


def _ptr_guid(field):
    """The Guid (hex) an FOMDSoftProtoPtr field names, or None: a tagged Guid struct and
    its None terminator, the Guid 24 bytes from the end."""
    if field is None or len(field.payload) < 24:
        return None
    g = bytes(field.payload)[-24:-8]
    return g.hex() if any(g) else None


def table_rows(table, struct_name):
    """[row record] of an item or hero table: {guid, row, name, kind, why?, weapon?}."""
    from riftlord.missions import modminions
    out = []
    for r in table.rows:
        guid = _guid(table, r)
        if guid is None:
            continue
        text = table.field(r, 'DisplayName')
        name = modminions.text_of(text.payload) if text is not None else None
        rec = {'guid': guid, 'row': r['name'], 'name': name}
        if struct_name == HERO_STRUCT:
            rec.update(kind='Hero', weapon=_ptr_guid(table.field(r, 'PrimaryWeapon')))
        else:
            rec['kind'] = ITEM_STRUCTS[struct_name]
            aq = modminions._enum(table, table.field(r, 'AquiredBy')) or AQUIRED_DEFAULT
            if _flag(table, r, 'bRequiresEntitlement'):
                rec['why'] = 'entitlement'
            elif _flag(table, r, 'bDevelopmentOnly'):
                rec['why'] = 'development'
            elif aq == INTERNAL:
                rec['why'] = 'internal'
        if not (name or '').strip() and 'why' not in rec:
            rec['why'] = 'unnamed'
        out.append(rec)
    return out


def gated_guids(table):
    """Every Guid (hex) an entitlement table's rows list in ItemEntitlements."""
    out = set()
    for r in table.rows:
        f = table.field(r, GATE_FIELD)
        if f is None:
            continue
        for e in table.struct_array(f)[2]:
            if len(e) >= 24:
                out.add(bytes(e[-24:-8]).hex())
    return out


def _table(ua, ux):
    from riftlord.build import missionproto
    from riftlord.formats.ue.uassetio import Package
    pkg = Package(ua, ux)
    pkg.load_payloads()
    return missionproto.Table(pkg)


_PAK_CACHE = {}


def _scan_pak(path, size, mtime):
    """{'tables': [(mod key, struct, [row record])], 'gated': [guid]} of one mod pak, kept
    until the file changes."""
    hit = _PAK_CACHE.get(path)
    if hit and hit[0] == (size, mtime):
        return hit[1]
    from riftlord.missions import modminions
    got = {'tables': [], 'gated': []}
    try:
        src = modminions.Source(path)
    except (Exception, SystemExit):                  # noqa: BLE001
        _PAK_CACHE[path] = ((size, mtime), got)
        return got
    for inner in sorted(src.keys):
        m = TABLE_RE.match(inner)
        ent = ENTITLEMENT_RE.match(inner)
        if not (m or ent) or inner[:-7] + '.uexp' not in src:
            continue
        try:
            t = _table(src.read(inner), src.read(inner[:-7] + '.uexp'))
            rs = modminions.row_struct(t)
        except (Exception, SystemExit):              # noqa: BLE001
            continue
        if rs == ENTITLEMENT_STRUCT:
            got['gated'] += sorted(gated_guids(t))
        elif m and (rs in ITEM_STRUCTS or rs == HERO_STRUCT):
            got['tables'].append((m.group(1), rs, table_rows(t, rs)))
    _PAK_CACHE[path] = ((size, mtime), got)
    return got


def _shipped():
    """{'tables': [(None, struct, rows)], 'gated': [guid]} of the game's own tables."""
    from riftlord.build import missionproto
    got = {'tables': [], 'gated': sorted(gated_guids(missionproto.read(ENTITLEMENTS)))}
    for path, rs in SHIPPED.items():
        got['tables'].append((None, rs, table_rows(missionproto.read(path), rs)))
    return got


def _base_stamp():
    """[(pak, size, mtime)] of the game's own paks."""
    import glob
    from riftlord.build.navigation import navpatch
    out = []
    for p in sorted(glob.glob(os.path.join(navpatch.PAK_DIR or '', '*.pak'))):
        try:
            st = os.stat(p)
        except OSError:
            continue
        out.append((os.path.basename(p), st.st_size, st.st_mtime_ns))
    return out


def _cache_file():
    from riftlord.core import gamepath
    return os.path.join(gamepath.scratch('missionunlocks', HERE), 'choices.json')


_SCAN = {}


def scan():
    """{'heroes': [rec], 'items': [rec], 'excluded': [rec with why]} over the shipped tables
    and every installed mod pak; read again when a pak is added, removed or rewritten.

    rec: {guid (32 hex, as stored), row, name, kind (Hero, Trap, Weapon, Trinket), mod
    (key, None when shipped), label (the mod as words, None when shipped), pak (basename,
    None when shipped)}; a hero's also has weapon (its PrimaryWeapon's Guid). A mod's row is
    read from its own pak (named after its key), else from a pak carrying a copy of it.
    The first row of a Guid is the one listed: shipped, then mods by pak path."""
    from riftlord.missions import modminions
    mods = modminions._paks()
    stamp = [CACHE_VERSION, _base_stamp(), [list(p) for p in mods]]
    if _SCAN.get('stamp') == stamp:
        return _SCAN['value']
    path = _cache_file()
    try:
        with open(path) as fh:
            disk = json.load(fh)
        if disk.get('stamp') == stamp:
            _SCAN.update(stamp=stamp, value=disk['value'])
            return disk['value']
    except (OSError, ValueError, AttributeError):
        pass
    sources = [(None, _shipped())]
    own, copies = [], []
    for p, size, mtime in mods:
        got = _scan_pak(p, size, mtime)
        for key, rs, rows in got['tables']:
            (own if modminions.pak_key(p) == key else copies).append((p, key, rs, rows))
        sources.append((p, {'tables': [], 'gated': got['gated']}))
    gated = set()
    for _p, got in sources:
        gated.update(got['gated'])
    seen, heroes, items, excluded = set(), [], [], []
    tables = [(None, key, rs, rows) for _p, got in sources[:1]
              for key, rs, rows in got['tables']] + own + copies
    for p, key, rs, rows in tables:
        for r in rows:
            if r['guid'] in seen:
                continue
            seen.add(r['guid'])
            r = dict(r, mod=key, label=modminions.label(key) if key else None,
                     pak=os.path.basename(p) if p else None)
            if 'why' not in r and r['kind'] != 'Hero' and r['guid'] in gated:
                r['why'] = 'dlc'
            if 'why' in r:
                excluded.append(r)
            else:
                (heroes if r['kind'] == 'Hero' else items).append(r)
    order = {'Hero': 0, 'Trap': 1, 'Weapon': 2, 'Trinket': 3}
    key_of = lambda r: (order[r['kind']], r['mod'] is not None, (r['name'] or '').lower())  # noqa: E731
    value = {'heroes': sorted(heroes, key=key_of), 'items': sorted(items, key=key_of),
             'excluded': sorted(excluded, key=key_of)}
    _SCAN.update(stamp=stamp, value=value)
    try:
        with open(path + '.tmp', 'w') as fh:
            json.dump({'stamp': stamp, 'value': value}, fh)
        os.replace(path + '.tmp', path)
    except OSError:
        pass
    return value


def index():
    """{guid: rec} of every row scan() read, the left-out ones (with why) included."""
    got = scan()
    return {r['guid']: r for r in got['heroes'] + got['items'] + got['excluded']}


# -- the setting ------------------------------------------------------------------------

def _guid_text(g):
    text = str(g or '').strip().lower()
    return text if re.fullmatch(r'[0-9a-f]{32}', text) else None


def clean(unlocks, known=None, previous=None):
    """A stored `unlocks`: {'heroes': [entry], 'items': [entry]}, or None for none.

    `unlocks` holds Guids (32 hex, or entries with a `guid`). An entry is {guid, row, name,
    kind, mod}: what the list says now, else what `previous` stored (a mod not installed
    now keeps its choice and its name). A Guid neither knows is dropped; one the list leaves
    out is refused (SystemExit), so a stored setting never names gated content."""
    if not isinstance(unlocks, dict):
        return None
    known = index() if known is None else known
    before = {}
    for side in ('heroes', 'items'):
        for e in ((previous or {}).get(side) or []):
            if isinstance(e, dict) and _guid_text(e.get('guid')):
                before[_guid_text(e['guid'])] = e
    out = {}
    for side, hero in (('heroes', True), ('items', False)):
        got, seen = [], set()
        for g in unlocks.get(side) or []:
            g = _guid_text(g.get('guid') if isinstance(g, dict) else g)
            if g is None or g in seen:
                continue
            seen.add(g)
            rec = known.get(g)
            if rec is not None and rec.get('why'):
                raise SystemExit('%s cannot be unlocked: it %s.'
                                 % (rec.get('name') or rec['row'], WHY[rec['why']]))
            if rec is not None and (rec['kind'] == 'Hero') != hero:
                continue
            src = rec or before.get(g)
            if src is None:
                continue
            got.append({k: src.get(k) for k in ('guid', 'row', 'name', 'kind', 'mod')})
        if got:
            out[side] = got
    return out or None


def of_record(rec):
    """A Missions record's `unlocks`: its Onslaught Editor spec's for a mission that editor
    builds, else the record's own (as missionscramble.of_record)."""
    rec = rec or {}
    spec = rec.get('spec') if isinstance(rec.get('spec'), dict) else None
    map_editor = bool(rec.get('level') and isinstance(rec.get('plan'), dict))
    if not map_editor and spec is not None and 'unlocks' in spec:
        return spec['unlocks'] or None
    return rec.get('unlocks') or None


def value(stored):
    """`stored` as the Mission Settings dialog shows it: {'heroes': [view], 'items': [view]}
    with each view {guid, name, kind, mod, label, installed}."""
    from riftlord.missions import modminions
    known = index()
    out = {'heroes': [], 'items': []}
    for side in out:
        for e in (stored or {}).get(side) or []:
            rec = known.get(e.get('guid'))
            mod = (rec or e).get('mod')
            out[side].append({'guid': e['guid'], 'name': (rec or e).get('name') or e.get('row'),
                              'kind': (rec or e).get('kind'), 'mod': mod,
                              'label': modminions.label(mod) if mod else None,
                              'installed': rec is not None and not rec.get('why')})
    return out


def view():
    """The choices the dialog offers: {'heroes': [choice], 'items': [choice]}, each
    {guid, name, kind, mod, label}."""
    got = scan()
    keep = ('guid', 'name', 'kind', 'mod', 'label')
    return {side: [{k: r.get(k) for k in keep} for r in got[side]]
            for side in ('heroes', 'items')}


# -- the build ----------------------------------------------------------------------------

def resolve(unlocks, known=None):
    """([(array, guid hex, hidden)], [warning]) of what `unlocks` (stored) writes: each hero
    in CharacterUnlocks with its weapon hidden in ItemUnlocks (as Scenario_18_WS), each item
    in ItemUnlocks. A Guid the list leaves out, or no installed pak defines, is a warning."""
    known = index() if known is None else known
    heroes, items, warnings = [], [], []
    for side in ('heroes', 'items'):
        for e in (unlocks or {}).get(side) or []:
            g = _guid_text(e.get('guid') if isinstance(e, dict) else e)
            if g is None:
                continue
            rec = known.get(g)
            name = (rec or (e if isinstance(e, dict) else {})).get('name') or g
            if rec is None:
                mod = e.get('mod') if isinstance(e, dict) else None
                warnings.append('%s not unlocked: %s' % (name, (
                    'its mod %s is not installed' % mod) if mod else 'no installed pak has it'))
            elif rec.get('why'):
                warnings.append('%s not unlocked: it %s' % (name, WHY[rec['why']]))
            elif rec['kind'] == 'Hero':
                heroes.append(rec)
            else:
                items.append(rec)
    out, seen = [], set()
    for rec in items:
        if rec['guid'] not in seen:
            seen.add(rec['guid'])
            out.append((ITEM_FIELD, rec['guid'], False))
    for rec in heroes:
        if rec['guid'] in seen:
            continue
        seen.add(rec['guid'])
        out.append((HERO_FIELD, rec['guid'], False))
        weapon = known.get(rec.get('weapon') or '')
        if weapon is not None and weapon['guid'] not in seen \
                and weapon.get('why') not in GATES:
            seen.add(weapon['guid'])
            out.append((ITEM_FIELD, weapon['guid'], True))
    return out, warnings


def _walk(buf, names, base=0, end=None, out=None):
    """{property name: (tag offset, payload offset)} of a tagged element stream, nested
    struct streams included (a Guid struct is 16 raw bytes, not a stream)."""
    out = {} if out is None else out
    o, end = base, len(buf) if end is None else end
    while o + 8 <= end:
        nm = names[struct.unpack_from('<i', buf, o)[0]]
        if nm == 'None':
            return out
        kind = names[struct.unpack_from('<i', buf, o + 8)[0]]
        size = struct.unpack_from('<i', buf, o + 16)[0]
        if kind == 'BoolProperty':
            out[nm] = (o, o + 24)
            o += 26
            continue
        head = 24 + 8 + 17 if kind == 'StructProperty' else 25
        out[nm] = (o, o + head)
        if kind == 'StructProperty' and names[struct.unpack_from('<i', buf, o + 24)[0]] != 'Guid':
            _walk(buf, names, o + head, o + head + size, out)
        o += head + size
    return out


def element(target, shipped, template, guid_hex, hidden=False):
    """One new element for `target`'s (a missionproto.Table) array: the shipped element
    `template` (row, array, index) of `shipped`, its names moved into `target`'s, both
    flags set (bIsPreMission false; bIsHidden as asked, items only), and the Guid replaced."""
    row, field, i = template
    buf = bytearray(shipped.struct_array(shipped.field(shipped.row(row), field))[2][i])
    target.remap_stream(buf, shipped.names)
    target.names = [s for s, _h in target.pkg.names]
    at = _walk(buf, target.names)
    buf[at['bIsPreMission'][1]] = 0
    if 'bIsHidden' in at:
        buf[at['bIsHidden'][1]] = 1 if hidden else 0
    g = at['Guid'][1]
    buf[g:g + 16] = bytes.fromhex(guid_hex)
    return bytes(buf)


def main_row(table):
    """The mission row of a build's table: the one that is not its Endless twin."""
    from riftlord.build import makemod
    rows = [r for r in table.rows if not r['name'].endswith(makemod.ENDLESS_TWIN)]
    return rows[0] if rows else None


def apply(files, unlocks, endless_only=False, known=None):
    """(files, report) with the build's mission row granting `unlocks` (stored): its
    ItemUnlocks and CharacterUnlocks replaced, the Endless twin's left empty. An Endless
    Variant grants nothing. report: unlocks [[array, guid, hidden]], unlocks_row,
    unlocks_warnings."""
    from riftlord.build import makemod, missionproto
    report = {'unlocks': [], 'unlocks_row': None, 'unlocks_warnings': []}
    key, _slot = makemod.mission_table_key(files)
    if key is None:
        if unlocks:
            report['unlocks_warnings'].append('no mission table to write unlocks into')
        return files, report
    t = _table(files[key], files[key[:-7] + '.uexp'])
    row = main_row(t)
    if row is None:
        return files, report
    report['unlocks_row'] = row['name']
    writes, warnings = resolve(None if endless_only else unlocks, known)
    report['unlocks_warnings'] = warnings
    had = [bytes(f.payload) for f in (t.field(row, ITEM_FIELD), t.field(row, HERO_FIELD))
           if f is not None]
    t.clear_array(row, ITEM_FIELD)
    t.clear_array(row, HERO_FIELD)
    if writes:
        shipped = missionproto.read()
        for field, guid, hidden in writes:
            template = HERO_TEMPLATE if field == HERO_FIELD else ITEM_TEMPLATE
            t.append_struct(t.field(row, field), element(t, shipped, template, guid, hidden))
    report['unlocks'] = [[f, g, h] for f, g, h in writes]
    now = [bytes(f.payload) for f in (t.field(row, ITEM_FIELD), t.field(row, HERO_FIELD))
           if f is not None]
    if now == had:
        return files, report
    out = dict(files)
    out[key], out[key[:-7] + '.uexp'] = t.write()
    return out, report


def granted(files):
    """{'row': name, ItemUnlocks: [(guid, pre, hidden)], CharacterUnlocks: [(guid, pre)],
    'twin': {array: count}} of a build's mission table, read back; None without one."""
    from riftlord.build import makemod
    key, _slot = makemod.mission_table_key(files)
    if key is None:
        return None
    t = _table(files[key], files[key[:-7] + '.uexp'])
    row = main_row(t)
    if row is None:
        return None
    out = {'row': row['name'], 'twin': {}, 'sizes': {}}
    for field in (ITEM_FIELD, HERO_FIELD):
        got = []
        f = t.field(row, field)
        elements = t.struct_array(f)[2] if f is not None else []
        for e in elements:
            at = _walk(e, t.names)
            g = at['Guid'][1]
            got.append((e[g:g + 16].hex(), bool(e[at['bIsPreMission'][1]]))
                       + ((bool(e[at['bIsHidden'][1]]),) if 'bIsHidden' in at else ()))
        out[field] = got
        out['sizes'][field] = sorted({len(e) for e in elements})
    for r in t.rows:
        if r is not row:
            out['twin'] = {fld: t.struct_array(t.field(r, fld))[0]
                           for fld in (ITEM_FIELD, HERO_FIELD) if t.field(r, fld) is not None}
    return out


#: Element sizes the shipped arrays use (struct_array over every shipped row).
SHIPPED_SIZES = {ITEM_FIELD: 182, HERO_FIELD: 156}


def checks(files, report):
    """[(name, ok, detail)]: the build's mission row grants what the report says and nothing
    else, in elements the size the shipped ones are, and its Endless twin grants nothing."""
    if 'unlocks' not in report:
        return []
    got = granted(files)
    if got is None:
        return [('the mission table is there to grant unlocks', False, '')]
    want_items = [(g, False, h) for f, g, h in report['unlocks'] if f == ITEM_FIELD]
    want_heroes = [(g, False) for f, g, _h in report['unlocks'] if f == HERO_FIELD]
    out = [('%s grants the chosen items' % got['row'], got[ITEM_FIELD] == want_items,
            got[ITEM_FIELD]),
           ('%s grants the chosen heroes' % got['row'], got[HERO_FIELD] == want_heroes,
            got[HERO_FIELD])]
    for field, size in SHIPPED_SIZES.items():
        if got['sizes'][field]:
            out.append(('%s elements are %d bytes, as shipped' % (field, size),
                        got['sizes'][field] == [size], got['sizes'][field]))
    if got['twin']:
        out.append(('the Endless twin grants nothing', not any(got['twin'].values()),
                    got['twin']))
    return out


def describe(report):
    """[(warn, line)] for the console: what the build grants, and what it left out."""
    out = [(True, w) for w in report.get('unlocks_warnings') or []]
    if report.get('unlocks'):
        known = index()
        names = []
        for field, g, hidden in report['unlocks']:
            if hidden:
                continue
            names.append((known.get(g) or {}).get('name') or g)
        out.append((False, 'unlocks on completion: %s' % ', '.join(names)))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--pak', help='a built mission (a pak or a loose build): what it grants')
    a = ap.parse_args(argv)
    if a.pak:
        from riftlord.core import paksource
        got = granted(paksource.entries(a.pak))
        known = index()
        if got is None:
            raise SystemExit('%s has no mission table' % a.pak)
        print(got['row'])
        for g, pre, hidden in got[ITEM_FIELD]:
            print('  item  %s %s%s' % (g, (known.get(g) or {}).get('name'),
                                       ' (hidden)' if hidden else ''))
        for g, pre in got[HERO_FIELD]:
            print('  hero  %s %s' % (g, (known.get(g) or {}).get('name')))
        return 0
    got = scan()
    for side in ('heroes', 'items', 'excluded'):
        print('%s (%d)' % (side, len(got[side])))
        for r in got[side]:
            print('  %-8s %-32s %-28s %s%s' % (r['kind'], r['name'], r['row'],
                                               r['label'] or 'shipped',
                                               '  -- %s' % WHY[r['why']] if r.get('why') else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
