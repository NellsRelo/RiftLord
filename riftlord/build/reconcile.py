"""Work out what every installed mod does to the shared campaign tables.

A pak mod replaces whole files. Two mods that each add a mission both ship their
own `CampaignProtos`, so the one the game loads last wins and the other's
missions are simply gone -- along with its rows in `MissionProtos`, its
registrations in `BP_OMDProtoManager` and its `EntitlementsProtos` edits.

Nothing here picks a winner. Each mod is diffed against the stock table to
recover *what it added*, and those additions are replayed onto stock together,
so N mods compose instead of clobbering.

    python3 reconcile.py --scan          # what each mod adds, and what collides
"""

import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.core import gamepath  # noqa: E402
from riftlord.build import missionproto  # noqa: E402
from riftlord.core import pkgcheck  # noqa: E402
from riftlord.build import protoreg  # noqa: E402
from riftlord.formats.ue.pak import Pak  # noqa: E402
from riftlord.formats.ue.uassetio import Package  # noqa: E402

#: The shipped tables a mission mod has to touch, and so the ones that collide.
CAMPAIGNS = missionproto.CAMPAIGN_TABLE
MISSIONS = missionproto.TABLE
ENTITLEMENTS = '/Game/Data/EntitlementsProtos'
MANAGER = protoreg.MANAGER

SHARED = (CAMPAIGNS, MISSIONS, ENTITLEMENTS, MANAGER)


def inside(game_path, ext):
    """The path a pak uses for a /Game asset, without the mount point."""
    return 'OMD/Content' + game_path[len('/Game'):] + ext


def readable(field):
    """The longest printable run in a text property -- its display string."""
    import re
    if field is None:
        return None
    runs = [b.decode('ascii', 'ignore').rstrip('\x00')
            for b in re.findall(rb'[ -~]{3,}', bytes(field.payload))]
    return runs[-1] if runs else None


def guid_of(table, row):
    f = table.field(row, 'Guid')
    return bytes(f.payload)[:16] if f else None


def list_guids(table, row, field_name):
    """The Guids in an array of proto pointers, in order."""
    f = table.field(row, field_name)
    if f is None:
        return []
    _n, _tag, items = table.struct_array(f)
    at = table.array_guid_offset(field_name, [g for g in (guid_of(table, r)
                                                          for r in table.rows)
                                              if g])
    if at is None:
        at = (len(items[0]) - 24) if items else 0
    return [bytes(e[at:at + 16]) for e in items]


class Source:
    """One pak, and whichever shared tables it carries."""

    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)
        self.pak = Pak(path)
        # the mount prefix varies, so match on the tail
        self.holds = {}
        for game_path in SHARED:
            for ext in ('.uasset', '.umap'):
                tail = inside(game_path, ext)
                hit = [n for n in self.pak.files if n.endswith(tail)]
                if hit:
                    self.holds[game_path] = hit[0]
                    break

    def table(self, game_path):
        """A shared DataTable out of this pak, parsed."""
        head = self.holds[game_path]
        body = head.rsplit('.', 1)[0] + '.uexp'
        return missionproto.Table(missionproto._loaded(
            self.pak.read_file(head), self.pak.read_file(body)))

    def package(self, game_path):
        head = self.holds[game_path]
        body = head.rsplit('.', 1)[0] + '.uexp'
        pkg = Package(self.pak.read_file(head), self.pak.read_file(body))
        pkg.load_payloads()
        return pkg


def import_path(pkg, index):
    """The /Game path an import refers to, via its outer package import."""
    import struct as _s
    names = [n for n, _h in pkg.names]
    if index >= 0:
        return None
    raw = pkg.imports[-index - 1]
    outer = _s.unpack('<iiiiiii', raw)[4]
    if outer < 0:
        return names[_s.unpack_from('<I', pkg.imports[-outer - 1], 20)[0]]
    return None


def fingerprint(table, row):
    """Every value in a row, with each FName resolved to its string.

    Comparing raw bytes across packages is useless -- the same value has a
    different index in each -- and comparing only the fields one thinks of is
    how a SoftObjectProperty full of name indices gets missed. This resolves
    everything, so a lifted row can be checked against its original whole.
    """
    names = table.names

    def name_at(buf, at):
        i = struct.unpack_from('<i', buf, at)[0]
        return names[i] if 0 <= i < len(names) else 'OUT-OF-RANGE(%d)' % i

    def stream(buf, base, limit):
        # 24 bytes of tag and then HasPropertyGuid: the smallest header is 25.
        # Reading it as 24 walks one byte short of the next tag, which stops the
        # walk early and quietly compares only the first property of a struct.
        out, o = [], base
        while o + 8 <= limit:
            nm = name_at(buf, o)
            if nm.startswith('OUT-OF-RANGE') or nm == 'None':
                break
            kind = name_at(buf, o + 8)
            size = struct.unpack_from('<i', buf, o + 16)[0]
            head, sub = 24 + 1, None
            if kind == 'BoolProperty':
                head, size = 26, 0
            elif kind == 'StructProperty':
                sub, head = name_at(buf, o + 24), 24 + 8 + 17
            elif kind in ('ArrayProperty', 'ByteProperty', 'EnumProperty'):
                sub, head = name_at(buf, o + 24), 24 + 8 + 1
            out.append((nm, kind, sub, value(buf, o + head, size, kind)))
            o += head + size
        return out

    def value(buf, at, size, kind):
        if size <= 0:
            return b''
        chunk = bytes(buf[at:at + size])
        if kind in ('NameProperty', 'SoftObjectProperty', 'SoftClassProperty'):
            return name_at(buf, at)
        if kind in ('ByteProperty', 'EnumProperty') and size == 8:
            return name_at(buf, at)
        if kind == 'StructProperty':
            return stream(buf, at, at + size) \
                if table._looks_tagged(chunk, names) else chunk
        if kind == 'ArrayProperty':
            count = struct.unpack_from('<i', buf, at)[0]
            if size < 4 + table.INNER_TAG:
                return (count, [])
            # The element tag is compared even when the array is empty: it names
            # the element type, and it travels between packages like anything
            # else.
            tag = (name_at(buf, at + 4), name_at(buf, at + 12),
                   name_at(buf, at + 28))
            if count <= 0:
                return (tag, [])
            body = at + 4 + table.INNER_TAG
            each = (size - 4 - table.INNER_TAG) // count
            items = []
            for k in range(count):
                start = body + each * k
                blob = bytes(buf[start:start + each])
                items.append(stream(buf, start, start + each)
                             if table._looks_tagged(blob, names) else blob)
            return (tag, items)
        return chunk

    out = {}
    for f in row['fields']:
        if f.kind == 'BoolProperty':
            out[f.name] = bool(f.bool_value)
        else:
            out[f.name] = value(bytearray(f.payload), 0, len(f.payload), f.kind)
    return out


def stock_tables():
    return {CAMPAIGNS: missionproto.read(CAMPAIGNS),
            MISSIONS: missionproto.read(MISSIONS),
            ENTITLEMENTS: missionproto.read(ENTITLEMENTS)}


def mission_index(table, origin):
    """Guid -> what we can say about a mission row."""
    out = {}
    for row in table.rows:
        g = guid_of(table, row)
        if not g:
            continue
        war = table.field(row, 'bIsWarScenario')
        gated = table.field(row, 'bRequiresEntitlement')
        out[g] = {'row': row['name'],
                  'title': readable(table.field(row, 'MissionName')) or row['name'],
                  'war': bool(war and war.bool_value),
                  'gated': bool(gated and gated.bool_value),
                  'source': origin}
    return out


def additions(source, stock):
    """What one pak adds, per shared table, relative to stock.

    Campaign lists are compared as ordered Guid lists: anything present that
    stock does not have is this mod's, whatever position it sits in.
    """
    out = {'pak': source.name, 'campaigns': {}, 'missions': {},
           'chains': {}, 'entitlements': {}, 'tables': [], 'table_paths': [],
           'defines_shipped': 0, 'touches': []}

    for game_path in source.holds:
        out['touches'].append(game_path)

    if CAMPAIGNS in source.holds:
        mine = source.table(CAMPAIGNS)
        base = stock[CAMPAIGNS]
        for row in mine.rows:
            b = base.row(row['name'])
            if b is None:
                continue
            was = list_guids(base, b, 'MissionList')
            now = list_guids(mine, row, 'MissionList')
            extra = [g for g in now if g not in was]
            if extra:
                out['campaigns'][row['name']] = extra

    if MISSIONS in source.holds:
        mine = source.table(MISSIONS)
        base = stock[MISSIONS]
        known = {r['name'] for r in base.rows}
        fresh = {r['name'] for r in mine.rows if r['name'] not in known}
        out['missions'] = {g: v for g, v in mission_index(mine, source.name).items()
                           if v['row'] in fresh}
        # rows added to the SHIPPED table, which is the part that cannot be
        # lifted into another mod's copy
        out['defines_shipped'] = len(fresh)
        # unlock chain entries this mod grafted onto shipped missions
        for row in mine.rows:
            b = base.row(row['name'])
            if b is None:
                continue
            was = list_guids(base, b, 'MissionUnlocks')
            now = list_guids(mine, row, 'MissionUnlocks')
            extra = [g for g in now if g not in was]
            if extra:
                out['chains'][row['name']] = extra

    if ENTITLEMENTS in source.holds:
        mine = source.table(ENTITLEMENTS)
        base = stock[ENTITLEMENTS]
        for row in mine.rows:
            b = base.row(row['name'])
            if b is None:
                continue
            per = {}
            for fld in ('MissionEntitlements', 'MissionUnlocks'):
                was = list_guids(base, b, fld)
                now = list_guids(mine, row, fld)
                extra = [g for g in now if g not in was]
                if extra:
                    per[fld] = extra
            if per:
                out['entitlements'][row['name']] = per

    if MANAGER in source.holds:
        pkg = source.package(MANAGER)
        try:
            _cdo, _idx, _field, entries = protoreg.find_array(pkg)
            out['tables'] = [protoreg.resolve(pkg, i) for i in entries]
            out['table_paths'] = [import_path(pkg, i) for i in entries]
        except (Exception, SystemExit):                                  # noqa: BLE001
            out['tables'] = []

    # A mod built by the onslaught editor keeps its mission rows in its OWN
    # table and registers it, rather than adding them to the shipped one. Those
    # rows are the missions its campaign entries point at, so without reading
    # them the entries have no name to show.
    out['missions'].update(private_missions(source))
    return out


def private_missions(source):
    """Mission rows held in the pak's own tables rather than the shipped one."""
    found = {}
    for name in source.pak.files:
        if not name.endswith('_MissionProtos.uasset'):
            continue
        if name.endswith(inside(MISSIONS, '.uasset')):
            continue                                        # the shipped one
        body = name.rsplit('.', 1)[0] + '.uexp'
        if body not in source.pak.files:
            continue
        try:
            table = missionproto.Table(missionproto._loaded(
                source.pak.read_file(name), source.pak.read_file(body)))
        except (Exception, SystemExit):                                   # noqa: BLE001
            continue
        found.update(mission_index(table, source.name))
    return found


def scan(mods_dir=None):
    """Every mod pak, what it adds, and where they collide."""
    mods_dir = mods_dir or gamepath.mods_dir()
    if not mods_dir or not os.path.isdir(mods_dir):
        raise SystemExit('no mods directory found')
    stock = stock_tables()

    # the stock picture, for naming missions the mods refer to
    names = mission_index(stock[MISSIONS], 'Orcs Must Die! 3')

    found, failed, clashes = [], [], {}
    for f in sorted(os.listdir(mods_dir)):
        if not f.lower().endswith('.pak'):
            continue
        if f == DEFAULT_PAK:
            # Our own output from last time. It holds every mod's rows already,
            # so reading it as a mod would make each of those rows look like a
            # second definition and every merge after the first would fail with
            # conflicts against itself.
            continue
        try:
            src = Source(os.path.join(mods_dir, f))
        except Exception as exc:                           # noqa: BLE001
            failed.append((f, '%s: %s' % (type(exc).__name__, exc)))
            continue
        if not src.holds:
            continue                                       # not a campaign mod
        try:
            got = additions(src, stock)
        except Exception as exc:                           # noqa: BLE001
            failed.append((f, '%s: %s' % (type(exc).__name__, exc)))
            continue
        for guid, rec in got['missions'].items():
            # Two mods defining a row with the SAME Guid is a genuine clash:
            # one mission cannot be two things, and whichever loads last wins.
            # Recorded rather than resolved, because which one to keep -- or
            # whether to give one a fresh Guid -- is the player's call.
            if guid in names and names[guid]['source'] != got['pak']:
                clashes.setdefault(guid, [names[guid]['source']])
                clashes[guid].append(got['pak'])
            names[guid] = rec
        found.append(got)

    # The same private mission ROW defined by more than one pak, each with its own Guid.
    # Worse than a Guid clash and previously unreported: only one pak's private table is
    # loaded, so a campaign carrying a different pak's Guid lists a mission that has no
    # row anywhere -- in the hub that is an entry with Play greyed out and no preview.
    # Three Rift Lord builds did this before its mod folder was derived from the mission
    # name rather than the level.
    by_row = {}
    for got in found:
        for guid, rec in (got.get('missions') or {}).items():
            by_row.setdefault(rec['row'], []).append((got['pak'], guid))
    row_clashes = {row: who for row, who in by_row.items()
                   if len({pak for pak, _g in who}) > 1}

    # a file two mods both replace is a file only one of them keeps
    owners = {}
    for got in found:
        for game_path in got['touches']:
            owners.setdefault(game_path, []).append(got['pak'])
    collisions = {p: who for p, who in owners.items() if len(who) > 1}

    # Any other shipped file two mods both carry collides the same way, and
    # Weaver has no strategy for those -- a custom minion mod replacing a
    # character table cannot be merged by appending Guids. They are reported so
    # the choice is the player's rather than an accident of load order.
    every = {}
    shared_space = {}
    for f in os.listdir(mods_dir):
        if not f.lower().endswith('.pak'):
            continue
        try:
            held = Pak(os.path.join(mods_dir, f)).files
        except (Exception, SystemExit):                                  # noqa: BLE001
            continue
        for name in held:
            if name.endswith(('.uexp', '.ubulk')):
                continue                                   # the header speaks
            key = name.split('OMD/Content/', 1)[-1]
            if key.startswith('Mods/'):
                # A mod's own space is only its own while one mod claims it. Two paks
                # writing the same `Mods/<name>/...` shadow each other by load order,
                # which is how a mission's private table came from an older build than
                # the campaign row pointing at it.
                shared_space.setdefault(key, []).append(f)
                continue
            every.setdefault(key, []).append(f)
    merged = {inside(p, '')[len('OMD/Content'):].lstrip('/') for p in SHARED}
    opaque = {k: who for k, who in every.items()
              if len(who) > 1 and not any(k.startswith(m) for m in merged)}

    # A mod's own missions are gated by the entries IT adds, so the stock table
    # alone tints only the shipped ones.
    gates = entitlement_of_missions(stock)
    for got in found:
        for row_name, per in got['entitlements'].items():
            for g in per.get('MissionEntitlements', []):
                gates.setdefault(g, row_name)
    for guid, rec in names.items():
        row = gates.get(guid)
        label, tint = DLC_LOOK.get(row, (None, None))
        rec['dlc'] = label
        rec['tint'] = tint

    shadowed = {k: who for k, who in shared_space.items() if len(set(who)) > 1}
    return {'mods_dir': mods_dir, 'mods': found, 'names': names,
            'clashes': clashes, 'row_clashes': row_clashes, 'shadowed': shadowed,
            'collisions': collisions, 'opaque': opaque,
            'outranks': later_than_weaver(mods_dir),
            'unreadable': failed, 'stock': stock}


#: What each DLC row is called where a player would recognise it, and the
#: colour a mission from it is tinted so its origin is visible at a glance.
DLC_LOOK = {
    'DLC3': ('Drastic Steps', 'jungle'),
    'DLC3_Bundle': ('Drastic Steps', 'jungle'),
    'DLC4': ('Cold as Eyes', 'teal'),
    'DLC5': ('Tipping The Scales', 'sand'),
}

#: A mission belongs to the campaign it was published in and stays there.
#: Moving one between campaigns is how a player would reach content they have
#: not bought, so the tool does not offer it -- a mission can be taken out of
#: its campaign and put back, and nowhere else.
LOCKED_TO_CAMPAIGN = True


def entitlement_of_missions(stock):
    """Guid -> the entitlement row that gates it, from the shipped table."""
    ent = stock[ENTITLEMENTS]
    out = {}
    for row in ent.rows:
        for g in list_guids(ent, row, 'MissionEntitlements'):
            out.setdefault(g, row['name'])
    return out


def later_than_weaver(mods_dir):
    """Paks that load after Weaver's AND replace a file Weaver writes.

    Load order is the filename, so anything sorting above ours wins -- but only
    where the two carry the same file. A minion mod named with a letter outranks
    `9999-` and overrides nothing, and warning about it would be noise.
    """
    mine = {inside(p, ext) for p in SHARED for ext in ('.uasset', '.umap')}
    out = []
    for f in sorted(os.listdir(mods_dir)):
        if not f.lower().endswith('.pak') or f == DEFAULT_PAK or f <= DEFAULT_PAK:
            continue
        try:
            held = Pak(os.path.join(mods_dir, f)).files
        except (Exception, SystemExit):                                  # noqa: BLE001
            continue
        clash = sorted({t.rsplit('/', 1)[-1] for t in mine
                        for n in held if n.endswith(t)})
        if clash:
            out.append({'pak': f, 'files': clash})
    return out


def describe(report):
    """A plain report of the scan, for the console."""
    out = []
    out.append('%d mod pak(s) touch the shared tables, in %s'
               % (len(report['mods']), report['mods_dir']))
    for got in report['mods']:
        out.append('')
        out.append('  %s' % got['pak'])
        out.append('    replaces: %s'
                   % ', '.join(p.rsplit('/', 1)[-1] for p in got['touches']))
        for campaign, guids in sorted(got['campaigns'].items()):
            shown = [report['names'].get(g, {}).get('title', g[:8].hex())
                     for g in guids]
            out.append('    %-22s +%d: %s' % (campaign, len(shown),
                                              ', '.join(shown[:6])
                                              + (' …' if len(shown) > 6 else '')))
        if got['missions']:
            out.append('    defines %d new mission row(s)' % len(got['missions']))
        if got['chains']:
            out.append('    grafts %d unlock chain entr(ies) onto shipped missions'
                       % sum(len(v) for v in got['chains'].values()))
        if got['entitlements']:
            out.append('    edits %d entitlement row(s)' % len(got['entitlements']))
        if got['tables']:
            out.append('    registers %d proto table(s)' % len(got['tables']))
    if report.get('row_clashes'):
        out.append('')
        out.append('  BROKEN -- the same mission row from several paks, each with a')
        out.append('  different Guid. Only one pak\'s table loads, so a campaign carrying a')
        out.append('  different Guid lists a mission with no row: in the hub that is an')
        out.append('  entry with Play greyed out and no preview. Keep ONE and delete')
        out.append('  the rest, then merge again:')
        for row, who in sorted(report['row_clashes'].items()):
            out.append('    %s' % row)
            for pak, guid in who:
                out.append('        %-40s guid %s' % (pak, guid[:8].hex()))
    if report.get('shadowed'):
        out.append('')
        out.append('  Mod folders more than one pak writes to -- a mod folder is safe')
        out.append('  only while one pak claims it, and load order decides:')
        for path, who in sorted(report['shadowed'].items()):
            out.append('    %-52s %s' % (path[len('Mods/'):][:52], ', '.join(sorted(set(who)))))
    if report['collisions']:
        out.append('')
        out.append('  COLLISIONS -- only the last pak loaded keeps these:')
        for game_path, who in sorted(report['collisions'].items()):
            out.append('    %-34s %s' % (game_path.rsplit('/', 1)[-1],
                                         ', '.join(who)))
    if report.get('opaque'):
        out.append('')
        out.append('  Also collide, and Weaver cannot merge these -- pick one:')
        for path, who in sorted(report['opaque'].items()):
            out.append('    %-40s %s' % (path[:40], ', '.join(who)))
    if report.get('outranks'):
        out.append('')
        out.append('  Weaver detected a mod with a higher load order priority. '
                   'This may override Weaver\'s patching.')
        for got in report['outranks']:
            out.append('    %-34s replaces %s'
                       % (got['pak'], ', '.join(got['files'])))
    for f, why in report['unreadable']:
        out.append('  unreadable: %s (%s)' % (f, why))
    return '\n'.join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--scan', action='store_true')
    ap.add_argument('--mods')
    args = ap.parse_args(argv)
    print(describe(scan(args.mods)))
    return 0


# ---------------------------------------------------------------------------
# Merging
#
# Three of the four shared tables are merged by appending Guids, which needs no
# knowledge of the source package: an entry is an existing element with its
# sixteen Guid bytes replaced. Only MissionProtos can carry whole new ROWS, and
# a row cannot be lifted between packages without remapping every FName it
# references -- EndlessWar's name table diverges from stock at index 459, so
# indices do not carry across.
#
# So the mission table is not rebuilt from stock: the mod that defines rows
# becomes the base, and everyone else's chain entries are replayed onto it.
# That composes every real case, because a mod built by the onslaught editor
# keeps its rows in its own registered table and adds none here. Two mods that
# both define rows in the shipped table cannot be composed this way, and that is
# reported rather than silently resolved.

def merge(report, selected=None, order=None, reguid=None):
    """Rebuild the shared tables on VANILLA, with the chosen mods replayed.

    `selected` names the paks to include; everything installed is used when it
    is None. `order` optionally gives {campaign_row: [guid, ...]} to place the
    added missions deliberately; anything it omits keeps first-seen order.

    The base is always the game's own table. Nothing here reads a mod's copy as
    a starting point, so a mod's unrelated edits are never inherited -- its
    additions are lifted out and replayed, and a row that came from another
    package has every FName it references remapped on the way in.
    """
    order = order or {}
    # {(pak, old guid): new guid} -- one mod's copy of a clashing mission gets
    # a fresh identity so both can exist. Never inferred: see the UI.
    reguid = reguid or {}
    mods = [m for m in report['mods']
            if selected is None or m['pak'] in set(selected)]
    notes, conflicts = [], []
    files = {}

    def known_guids(table):
        return [g for g in (guid_of(table, r) for r in table.rows) if g]

    # -- campaigns ----------------------------------------------------------
    camp = missionproto.read(CAMPAIGNS)
    stock_camp = missionproto.read(CAMPAIGNS)
    for row in camp.rows:
        vanilla = list_guids(stock_camp, stock_camp.row(row['name']),
                             'MissionList')
        offered, seen, dups = [], set(vanilla), 0
        for got in mods:
            for g in got['campaigns'].get(row['name'], []):
                g = reguid.get((got['pak'], g), g)
                if g in seen:
                    dups += 1                    # the same mission listed twice
                    continue
                seen.add(g)
                offered.append(g)
        if dups:
            notes.append('%s: dropped %d duplicate listing(s)'
                         % (row['name'], dups))

        if row['name'] in order:
            # An arrangement is the campaign's WHOLE list, in the order given.
            # Two things are enforced here rather than trusted: nothing the game
            # ships may be dropped, and nothing may be placed that no chosen mod
            # offers for this campaign. The second is what stops a mission being
            # moved into a campaign it was never published in, which is how a
            # player would reach content they have not bought.
            allowed = set(vanilla) | set(offered)
            want, taken, refused = [], set(), 0
            for g in order[row['name']]:
                if g in allowed and g not in taken:
                    taken.add(g)
                    want.append(g)
                elif g not in allowed:
                    refused += 1
            missing = [g for g in vanilla if g not in taken]
            if missing:
                conflicts.append('%s: an arrangement cannot drop the %d mission'
                                 '(s) the game ships' % (row['name'],
                                                         len(missing)))
                continue
            if refused:
                notes.append('%s: refused %d entr(ies) no chosen mod offers'
                             % (row['name'], refused))
            left_out = len(offered) - (len(want) - len(vanilla))
            if left_out > 0:
                notes.append('%s: left out %d mission(s)'
                             % (row['name'], left_out))
            if want != vanilla:
                camp.set_missions(row, want)
                notes.append('%s: %d mission(s), %d added'
                             % (row['name'], len(want), len(want) - len(vanilla)))
            continue

        if not offered:
            continue
        for g in offered:
            camp.add_mission(row, g)
        notes.append('%s: +%d mission(s)' % (row['name'], len(offered)))
    files[CAMPAIGNS] = camp.write()

    # -- missions: vanilla, plus every chosen mod's rows lifted in ----------
    miss = missionproto.read(MISSIONS)
    have = {r['name'] for r in miss.rows}
    owner, lifted = {}, 0
    for got in mods:
        if MISSIONS not in got['touches'] or not got['defines_shipped']:
            continue
        src = Source(os.path.join(report['mods_dir'], got['pak']))
        mine = src.table(MISSIONS)
        checks = []
        for row in mine.rows:
            if row['name'] in have:
                if row['name'] in owner and owner[row['name']] != got['pak']:
                    conflicts.append('%s and %s both define the mission row %s'
                                     % (owner[row['name']], got['pak'],
                                        row['name']))
                continue
            fresh = miss.adopt(mine, row)
            was = guid_of(mine, row)
            new_guid = reguid.get((got['pak'], was)) if was else None
            if new_guid:
                field = miss.field(fresh, 'Guid')
                field.payload = bytearray(new_guid
                                          + bytes(field.payload)[16:])
                notes.append('%s: %s rebuilt under a fresh UUID'
                             % (got['pak'], row['name']))
            have.add(row['name'])
            owner[row['name']] = got['pak']
            checks.append((row, fresh['name'], mine))
            lifted += 1
        # a lifted row is only useful if it still says what it said
        for row, name, table in checks:
            before = fingerprint(table, row)
            after = fingerprint(miss, miss.row(name))
            bad = [k for k in before if before[k] != after.get(k)]
            if bad:
                conflicts.append('%s: %s did not survive being lifted (%s)'
                                 % (got['pak'], name, ', '.join(bad[:3])))
    if lifted:
        notes.append('lifted %d mission row(s) onto the vanilla table' % lifted)

    # unlock chains every chosen mod grafts onto shipped missions
    for got in mods:
        for row_name, guids in got['chains'].items():
            row = miss.row(row_name)
            if row is None:
                continue
            already = set(list_guids(miss, row, 'MissionUnlocks'))
            for g in guids:
                if g not in already:
                    miss.add_guid(row, 'MissionUnlocks', g,
                                  known=known_guids(miss))
                    already.add(g)
                    notes.append('%s unlocks +1 (%s)' % (row_name, got['pak']))
    files[MISSIONS] = miss.write()

    # -- entitlements -------------------------------------------------------
    if any(m['entitlements'] for m in mods):
        ent = missionproto.read(ENTITLEMENTS)
        for got in mods:
            for row_name, per in got['entitlements'].items():
                row = ent.row(row_name)
                if row is None:
                    continue
                for fld, guids in per.items():
                    already = set(list_guids(ent, row, fld))
                    for g in guids:
                        if g not in already:
                            ent.add_guid(row, fld, g, known=known_guids(ent))
                            already.add(g)
                    notes.append('%s %s +%d (%s)'
                                 % (row_name, fld, len(guids), got['pak']))
        files[ENTITLEMENTS] = ent.write()

    # -- the proto manager: the union of every registered table -------------
    pkg = protoreg.load()
    _cdo, _at, _head, entries = protoreg.find_array(pkg)
    have_tables = {protoreg.resolve(pkg, e) for e in entries}
    added = 0
    for got in mods:
        for path in got.get('table_paths', []):
            if not path or path.rsplit('/', 1)[-1] in have_tables:
                continue
            protoreg.add_table(pkg, path)
            have_tables.add(path.rsplit('/', 1)[-1])
            added += 1
    if added:
        notes.append('proto manager: +%d table(s)' % added)
        files[MANAGER] = pkg.write()

    conflicts += bad_packages(files)

    return {'files': files, 'notes': notes, 'conflicts': conflicts,
            'lifted': lifted, 'mods': [m['pak'] for m in mods]}


#: The tables are read row by row; anything else is read as a single object.
_TABLES = (CAMPAIGNS, MISSIONS, ENTITLEMENTS)


def bad_packages(files):
    """Every merged package that the game would reject, read back from bytes.

    The merge patches bytes in place and the readers here skip by the size
    fields, so a reader cannot see damage it caused -- it steps over it. This
    parses the result instead, which is what the game does, and is the only
    thing between a wrong offset and a crash on start.
    """
    out = []
    for game_path, (uasset, uexp) in files.items():
        out += pkgcheck.problems(
            uasset, uexp, game_path.rsplit('/', 1)[-1],
            'table' if game_path in _TABLES else 'object')
    return out


#: Loaded after everything it reconciles -- pak priority is the filename.
DEFAULT_PAK = '9999-Weaver_Patched_P.pak'


def install(merged, out=None, mods_dir=None):
    """Write the merged tables as one pak that loads after the rest."""
    from riftlord.formats.ue import pakv4
    bad = bad_packages(merged['files'])
    if bad:
        raise SystemExit('the merged tables do not read back:\n  '
                         + '\n  '.join(bad))
    mods_dir = mods_dir or gamepath.mods_dir()
    out = out or os.path.join(mods_dir, DEFAULT_PAK)
    files = {}
    for game_path, blobs in merged['files'].items():
        stem = 'OMD/Content' + game_path[len('/Game'):]
        files[stem + '.uasset'] = blobs[0]
        files[stem + '.uexp'] = blobs[1]
    pakv4.write(out, files)
    return {'pak': out, 'files': len(files),
            'size': os.path.getsize(out)}


# The entry point belongs at the END of the file. It sat in the middle, above
# `DEFAULT_PAK` and everything else in the merging half, so running this module at all
# raised `NameError: DEFAULT_PAK` from inside `scan` -- every mod was invisible because
# the scan could not start.
if __name__ == '__main__':
    raise SystemExit(main())
