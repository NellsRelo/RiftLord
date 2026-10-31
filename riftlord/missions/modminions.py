"""Custom minions from installed mods: found, listed, carried inside a mission, checked.

The contract is the one Mods/ZombieOrcs/CONTRACT.md states for any mod (Zombie Orcs is
its worked example; nothing here knows about zombies):

- found: every pak in Content/Paks/~mods and LogicMods, subfolders too (`.disabled` files
  are not paks); its DataTables at OMD/Content/Mods/<ModKey>/Tables/ whose RowStruct
  import is the ScriptStruct OMDAICharacterProto. Rows are read as the shipped
  /Game/Data/AICharacterProtos is.
- carried: a mission that spawns a mod row carries the packages the mod's
  OMD/Content/Mods/<ModKey>/OrcsMustUI/Minions.yaml lists for it, byte for byte from the
  installed pak, union over every row it spawns, re-copied on every build. A mod with no
  manifest falls back to a closure walk from its tables, with a warning.
- checked: gates() compares a mission's carried copy with the installed mod, names every
  spawned Guid, and resolves the carried packages' imports.
- listed: a Minions.yaml row's optional `Family:` (e.g. `Wraith`) is the family the
  Onslaught Editor lists it under, beside the shipped protos of that family; without it
  the row is listed under the mod's name.
- previewed: a row's optional `Preview:` block gives its hub-diorama art: `Mesh` and
  `Anim` (object paths, shipped or listed in the row's Packages), `Scale`, `Radius`,
  `Height` (uu at Scale), `StandIn` (a shipped row shown when the art cannot be). Its
  optional effect keys add one live particle system to the creature in the diorama:
    `Effect`        a /Game ParticleSystem (Cascade) path, shipped or listed in the row's
                    Packages, e.g. /Game/Proto/FX/Particles/StatusFX/P_Fire.P_Fire
    `EffectSocket`  a bone or socket of the Preview Mesh (or its skeleton); default the
                    mesh root
    `EffectOffset`  "x y z" in that bone's space, in mesh units (scaled with the mesh by
                    Scale); default "0 0 0"
    `EffectRotation` "pitch yaw roll" in degrees, relative to that bone; default "0 0 0"
    `EffectScale`   uniform scale on top of the mesh's; default 1
  The effect auto-activates, as the shipped diorama effects do. An Effect that is not a
  /Game path, not shipped or carried, or not a ParticleSystem, and a socket the mesh
  does not have, are build warnings: the creature is shown without the effect (or at the
  mesh root).

    python3 modminions.py                  # the mod minions installed now
    python3 modminions.py --mission PAK    # what a built mission carries, and its gates
"""

import argparse
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

#: The row struct a minion table imports.
ROW_STRUCT = 'OMDAICharacterProto'
#: A mod's manifest, in its OrcsMustUI folder.
MANIFEST = 'Minions.yaml'
#: The files one package may have in a pak.
EXTS = ('.uasset', '.umap', '.uexp', '.ubulk', '.uptnl')
#: A table entry inside a pak: (mod key, table leaf).
TABLE_RE = re.compile(r'^OMD/Content/Mods/([^/]+)/Tables/([^/.]+)\.uasset$')
#: A mod's manifest entry inside a pak.
MANIFEST_RE = re.compile(r'^OMD/Content/Mods/([^/]+)/OrcsMustUI/%s$' % re.escape(MANIFEST))
#: The mod folder an entry is in.
FOLDER_RE = re.compile(r'^OMD/Content/Mods/([^/]+)/')
#: The shipped minion table.
SHIPPED_TABLE = '/Game/Data/AICharacterProtos'
#: EX_IntConst: a Guid written into bytecode is four of these.
INT_CONST = b'\x1d'
#: Mission files larger than this are not searched for spawned Guids (levels, textures).
SCAN_MAX = 8 << 20
#: Roots scanned for mods; None means the game's ~mods and LogicMods. Tests set their own.
ROOTS = None


# -- reading ----------------------------------------------------------------------------

def roots():
    """The folders whose paks are scanned: ~mods and LogicMods of the Paks folder Supported
    Mods reads (RIFTLORD_SUPPORTED_PAKS, else the game's)."""
    if ROOTS is not None:
        return list(ROOTS)
    from riftlord.core import supportedmods
    paks = supportedmods.paks_root()
    if not paks:
        return []
    return [os.path.join(paks, '~mods'), os.path.join(paks, 'LogicMods')]


def pak_key(path):
    """A pak's mod key: its name without the priority prefix and `_P`."""
    stem = os.path.basename(path.rstrip('/\\'))
    stem = stem[:-4] if stem.endswith('.pak') else stem
    stem = re.sub(r'^\d+[-_]', '', stem)
    return stem[:-2] if stem.endswith('_P') else stem


def _paks():
    """[(path, size, mtime)] of every pak under roots(), sorted by path."""
    out = []
    for root in roots():
        if not root or not os.path.isdir(root):
            continue
        for base, dirs, names in os.walk(root):
            dirs.sort()
            for n in sorted(names):
                if not n.endswith('.pak'):
                    continue
                p = os.path.join(base, n)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                out.append((p, st.st_size, st.st_mtime_ns))
    return sorted(out)


class Source:
    """One pak (or loose build) read by inner path (`OMD/Content/...`)."""

    def __init__(self, path):
        from riftlord.core import paksource
        self.path = path
        self._pak = None
        if paksource.is_loose(path):
            self.keys = {n: n for n in paksource.names(path)}
        else:
            from riftlord.formats.ue import pak as _pak
            self._pak = _pak.Pak(path)
            self.keys = {(k[k.index('OMD/'):] if 'OMD/' in k else k): k for k in self._pak.files}

    def __contains__(self, inner):
        return inner in self.keys

    def read(self, inner):
        if self._pak is None:
            from riftlord.core import paksource
            return paksource.read(self.path, inner)
        return self._pak.read_file(self.keys[inner])


def _import_name(pkg, index):
    if index >= 0 or -index > len(pkg.imports):
        return None
    return pkg.names[struct.unpack_from('<i', pkg.imports[-index - 1], 20)[0]][0]


def row_struct(table):
    """The name of a missionproto.Table's RowStruct import, or None."""
    data, o = table.export['data'], 0
    while True:
        f, ln = table._read_field(data, o)
        o += ln
        if f is None:
            return None
        if f.name == 'RowStruct' and f.kind == 'ObjectProperty' and len(f.payload) >= 4:
            return _import_name(table.pkg, struct.unpack_from('<i', bytes(f.payload), 0)[0])


def _fstring(data, o):
    n = struct.unpack_from('<i', data, o)[0]
    o += 4
    if n == 0:
        return '', o
    if n < 0:
        raw = data[o:o - 2 * n]
        return raw.decode('utf-16-le', 'ignore').rstrip('\x00'), o - 2 * n
    return data[o:o + n].decode('utf-8', 'ignore').rstrip('\x00'), o + n


def text_of(payload):
    """The source string of an FText payload (flags, history type, namespace, key, source)."""
    data = bytes(payload)
    try:
        if len(data) >= 5 and data[4] == 0:
            o = 5
            for _ in range(2):
                _s, o = _fstring(data, o)
            return _fstring(data, o)[0] or None
        if len(data) >= 9 and data[4] == 0xFF and data[5:9] != b'\0\0\0\0':
            return _fstring(data, 9)[0] or None
    except (struct.error, IndexError):
        pass
    runs = [b.decode('ascii', 'ignore') for b in re.findall(rb'[ -~]{2,}', data)]
    return runs[-1] if runs else None


def _soft_path(table, field):
    if field is None or len(field.payload) < 8:
        return None
    i = struct.unpack_from('<i', bytes(field.payload), 0)[0]
    name = table.names[i] if 0 <= i < len(table.names) else None
    return None if name in (None, 'None') else name


def _enum(table, field):
    if field is None or len(field.payload) < 8:
        return None
    i = struct.unpack_from('<i', bytes(field.payload), 0)[0]
    name = table.names[i] if 0 <= i < len(table.names) else None
    return name.split('::')[-1] if name else None


def rows_of(table):
    """[{row, guid (32 hex), name, flyer, boss, runner, barricades, weight, cls, icon}] of
    a minion table. An absent flag is false: a cooked row omits class defaults."""
    out = []
    for r in table.rows:
        def flag(name):
            f = table.field(r, name)
            return bool(f is not None and f.bool_value)
        g = table.field(r, 'Guid')
        if g is None or len(g.payload) < 16:
            continue
        text = table.field(r, 'DisplayName')
        out.append({'row': r['name'], 'guid': bytes(g.payload)[:16].hex(),
                    'name': text_of(text.payload) if text is not None else None,
                    'flyer': flag('bIsFlyer'), 'boss': flag('bIsBoss'),
                    'runner': flag('bHasRunnerMovement'),
                    'barricades': flag('bIsBarricadeDestroyer'),
                    'weight': _enum(table, table.field(r, 'WeightClass')),
                    'cls': _soft_path(table, table.field(r, 'AICharacterClass')),
                    'icon': _soft_path(table, table.field(r, 'UIIcon'))})
    return out


def guid4(hex_guid):
    """32 hex digits (stored bytes) as the four int32 onslaught.protos() uses."""
    return struct.unpack('<4i', bytes.fromhex(hex_guid))


def parse_manifest(text):
    """{row: entry} of a Minions.yaml; SystemExit naming what does not read."""
    import yaml
    try:
        got = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise SystemExit('%s does not read as YAML: %s' % (MANIFEST, exc))
    out = {}
    for entry in (got.get('Minions') if isinstance(got, dict) else None) or []:
        if isinstance(entry, dict) and entry.get('Row'):
            out[str(entry['Row'])] = entry
    return out


_PAK_CACHE = {}


def _scan_pak(path, size, mtime):
    """{'tables': [(key, game path, rows)], 'manifests': {key: text}} of one pak, kept
    until the file changes."""
    hit = _PAK_CACHE.get(path)
    if hit and hit[0] == (size, mtime):
        return hit[1]
    from riftlord.build import missionproto
    from riftlord.formats.ue.uassetio import Package
    got = {'tables': [], 'manifests': {}}
    try:
        src = Source(path)
    except (Exception, SystemExit):                  # noqa: BLE001
        _PAK_CACHE[path] = ((size, mtime), got)
        return got
    for inner in sorted(src.keys):
        m = TABLE_RE.match(inner)
        if m and inner[:-7] + '.uexp' in src:
            try:
                pkg = Package(src.read(inner), src.read(inner[:-7] + '.uexp'))
                pkg.load_payloads()
                table = missionproto.Table(pkg)
                if row_struct(table) != ROW_STRUCT:
                    continue
                got['tables'].append((m.group(1), '/Game' + inner[len('OMD/Content'):-7],
                                      rows_of(table)))
            except (Exception, SystemExit):          # noqa: BLE001
                continue
        m = MANIFEST_RE.match(inner)
        if m:
            try:
                got['manifests'][m.group(1)] = src.read(inner).decode('utf-8', 'replace')
            except (Exception, SystemExit):          # noqa: BLE001
                continue
    _PAK_CACHE[path] = ((size, mtime), got)
    return got


_SCAN = {}


def scan():
    """{'mods': {key: mod}, 'rows': {row: rec}} over every installed pak, read again when
    a pak is added, removed or rewritten.

    A mod is read from its own pak: the one holding its Minions.yaml, else the one named
    after its key. A pak that only carries a copy (a mission) is used when no own pak is
    installed, and marked `own` False. mod: {key, label, pak, own, manifest ({row: entry}
    or None), problems, tables}. rec: rows_of's fields plus {mod, table, pak, own, entry}.
    """
    stamp = tuple(_paks())
    if _SCAN.get('stamp') == stamp:
        return _SCAN['value']
    mods, rows = {}, {}
    for path, size, mtime in stamp:
        got = _scan_pak(path, size, mtime)
        keys = {k for k, _g, _r in got['tables']}
        for key in sorted(keys):
            own = key in got['manifests'] or pak_key(path) == key
            have = mods.get(key)
            if have is not None and (have['own'] or not own):
                continue
            manifest, problems = None, []
            if key in got['manifests']:
                try:
                    manifest = parse_manifest(got['manifests'][key])
                except SystemExit as exc:
                    problems.append(str(exc))
                    manifest = {}
            mods[key] = {'key': key, 'label': label(key), 'pak': path, 'own': own,
                         'manifest': manifest, 'problems': problems,
                         'tables': [g for k, g, _r in got['tables'] if k == key],
                         'rows': [(r, g) for k, g, rs in got['tables'] if k == key
                                  for r in rs]}
    for key, mod in sorted(mods.items()):
        for r, table in mod.pop('rows'):
            entry = (mod['manifest'] or {}).get(r['row'])
            rows.setdefault(r['row'], dict(r, mod=key, table=table, pak=mod['pak'],
                                           own=mod['own'], entry=entry))
    value = {'mods': mods, 'rows': rows}
    _SCAN.update(stamp=stamp, value=value)
    return value


def label(key):
    """A mod key as words: ZombieOrcs -> Zombie Orcs."""
    spaced = re.sub(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])', ' ', key)
    return spaced.replace('_', ' ').strip()


def mods():
    return scan()['mods']


def rows():
    return scan()['rows']


def guids():
    """{row: four int32}, as onslaught.protos() gives the shipped rows."""
    return {n: guid4(r['guid']) for n, r in rows().items()}


def flags():
    """{row: flags}, as onslaught.proto_flags() gives the shipped rows, plus the mod."""
    return {n: {'flyer': r['flyer'], 'boss': r['boss'], 'runner': r['runner'],
                'barricades': r['barricades'], 'name': r['name'], 'weight': r['weight'],
                'mod': r['mod']} for n, r in rows().items()}


def preview_hint(row):
    """The row's manifest Preview block ({Mesh, Anim, Scale, StandIn, Radius, Height,
    Effect, EffectSocket, EffectOffset, EffectScale}), or None."""
    rec = rows().get(row)
    entry = (rec or {}).get('entry') or {}
    got = entry.get('Preview')
    return got if isinstance(got, dict) else None


def preview_effect(row):
    """(effect, warnings) of the row's Preview Effect keys. effect: {path ('/Game/A/P_X'
    or '/Game/A/P_X.P_X'), socket (str or None), offset (x, y, z), rotation (pitch, yaw,
    roll), scale}, or None when the row has no usable Effect. Each key that does not read
    is a warning line, never an error: a bad EffectSocket, EffectOffset, EffectRotation or
    EffectScale falls back to the mesh root, no offset, no rotation and 1."""
    hint = preview_hint(row) or {}
    raw = hint.get('Effect')
    if raw in (None, ''):
        return None, []
    warns = []
    if not isinstance(raw, str) or not raw.strip().startswith('/Game/'):
        return None, ['%s: Preview Effect %r is not a /Game particle system path; the preview '
                      'shows it without an effect' % (row, raw)]
    socket = hint.get('EffectSocket')
    if socket in (None, ''):
        socket = None
    elif not isinstance(socket, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', socket.strip()):
        warns.append('%s: Preview EffectSocket %r is not a bone or socket name; the effect '
                     'sits at the mesh root' % (row, socket))
        socket = None
    else:
        socket = socket.strip()
    triples = {}
    for key, shape, what in (('EffectOffset', 'x y z', 'no offset'),
                             ('EffectRotation', 'pitch yaw roll', 'no rotation')):
        triples[key] = (0.0, 0.0, 0.0)
        got = hint.get(key)
        if got in (None, ''):
            continue
        parts = got.split() if isinstance(got, str) else got if isinstance(got, list) else None
        try:
            if parts is None or len(parts) != 3 or any(isinstance(p, bool) for p in parts):
                raise ValueError
            triples[key] = tuple(float(p) for p in parts)
        except (TypeError, ValueError):
            warns.append('%s: Preview %s %r is not "%s"; %s' % (row, key, got, shape, what))
    scale = 1.0
    got = hint.get('EffectScale')
    if got not in (None, ''):
        try:
            if isinstance(got, bool) or float(got) <= 0:
                raise ValueError
            scale = float(got)
        except (TypeError, ValueError):
            warns.append('%s: Preview EffectScale %r is not a positive number; scale 1'
                         % (row, got))
    return {'path': raw.strip(), 'socket': socket, 'offset': triples['EffectOffset'],
            'rotation': triples['EffectRotation'], 'scale': scale}, warns


def carried_packages(row):
    """The /Game package paths a mission spawning `row` carries, from its manifest, or
    None when the mod has no entry for it."""
    entry = (rows().get(row) or {}).get('entry')
    if not entry:
        return None
    return sorted({_package(p) for p in entry.get('Packages') or []})


_SHIPPED = {}


def shipped_rows():
    """{row: rec} of the shipped minion table (rows_of), read once."""
    if not _SHIPPED:
        from riftlord.core import gamefiles
        from riftlord.build import missionproto
        from riftlord.formats.ue.uassetio import Package
        pkg = Package(*gamefiles.package(SHIPPED_TABLE))
        pkg.load_payloads()
        _SHIPPED.update({r['row']: r for r in rows_of(missionproto.Table(pkg))})
    return _SHIPPED


def stand_in(row):
    """The shipped row a mod row is shown as when its own art cannot be: the manifest's
    Preview StandIn, else the shipped row whose UIIcon is the same texture; or None."""
    hint = preview_hint(row) or {}
    shipped = shipped_rows()
    if hint.get('StandIn') in shipped:
        return hint['StandIn']
    icon = (rows().get(row) or {}).get('icon')
    if icon:
        same = sorted(n for n, r in shipped.items() if r.get('icon') == icon)
        if same:
            return min(same, key=len)
    return None


# -- what a mission uses --------------------------------------------------------------

def _package(path):
    """'/Game/A/B.B' or '/Game/A/B' -> '/Game/A/B'."""
    head, _, leaf = str(path).strip().rpartition('/')
    return head + '/' + leaf.split('.')[0]


def _inner(game_path):
    return 'OMD/Content' + game_path[len('/Game'):]


def folder(inner):
    m = FOLDER_RE.match(inner)
    return m.group(1) if m else None


def patterns(hex_guid):
    """The two ways a Guid sits in a package: its 16 bytes together (a table, a CDO
    default), or as four IntConsts in bytecode (a spawn call)."""
    g = bytes.fromhex(hex_guid)
    return g, b''.join(INT_CONST + g[i:i + 4] for i in range(0, 16, 4))


def carried_rows(files, own=None):
    """{row: rec} of the minion tables `files` carries in mod folders other than `own`
    (rows_of's fields plus {mod, table, pak None, own False, entry None})."""
    from riftlord.build import missionproto
    from riftlord.formats.ue.uassetio import Package
    out = {}
    for k in sorted(files):
        m = TABLE_RE.match(k)
        if not m or m.group(1) == own:
            continue
        uexp = k[:-7] + '.uexp'
        if uexp not in files:
            continue
        try:
            pkg = Package(_bytes(files[k]), _bytes(files[uexp]))
            pkg.load_payloads()
            table = missionproto.Table(pkg)
            if row_struct(table) != ROW_STRUCT:
                continue
            for r in rows_of(table):
                out.setdefault(r['row'], dict(r, mod=m.group(1),
                                              table='/Game' + k[len('OMD/Content'):-7],
                                              pak=None, own=False, entry=None))
        except (Exception, SystemExit):              # noqa: BLE001
            continue
    return out


def carried_folders(files, own=None):
    """Mod folders other than `own` that `files` carries minion tables for."""
    return {r['mod'] for r in carried_rows(files, own).values()}


def known_rows(files, own=None):
    """{row: rec}: the installed mods' minion rows, and those only `files` carries."""
    out = carried_rows(files, own)
    out.update(rows())
    return out


def _bytes(blob):
    if isinstance(blob, (bytes, bytearray)):
        return bytes(blob)
    with open(blob, 'rb') as fh:
        return fh.read()


def used(files, own=None, known=None):
    """Sorted mod rows (`known`, default known_rows) whose Guid a mission's own packages
    name (its coordinator's spawn calls or Blueprint variables). Tables and the mod
    folders it carries are not read."""
    known = known_rows(files, own) if known is None else known
    skip = (set(mods()) | {r['mod'] for r in known.values()}) - {own}
    pats = {n: patterns(r['guid']) for n, r in known.items() if r['mod'] != own}
    found = set()
    for k, blob in files.items():
        if not k.endswith(('.uexp', '.uasset', '.umap')) or '/Tables/' in k:
            continue
        if folder(k) in skip:
            continue
        blob = _bytes(blob)
        if len(blob) > SCAN_MAX:
            continue
        for n, (raw, code) in pats.items():
            if n not in found and (raw in blob or code in blob):
                found.add(n)
    return sorted(found)


# -- carrying -------------------------------------------------------------------------

def walk(src, key, start):
    """Package paths reached from `start` through import and name-table paths under
    /Game/Mods/<key>/ that `src` holds: the fallback for a mod with no manifest."""
    from riftlord.formats.ue.uassetio import Package
    prefix = '/Game/Mods/%s/' % key
    seen, todo = set(), list(start)
    while todo:
        gp = _package(todo.pop())
        if gp in seen:
            continue
        stem = _inner(gp)
        head = stem + '.uasset' if stem + '.uasset' in src else \
            stem + '.umap' if stem + '.umap' in src else None
        if head is None:
            continue
        seen.add(gp)
        try:
            pkg = Package(src.read(head))
        except (Exception, SystemExit):              # noqa: BLE001
            continue
        todo += [n for n, _h in pkg.names if n.startswith(prefix)]
    return sorted(seen)


def plan(rows_used):
    """{mod key: {'mod', 'rows', 'packages', 'walked'}} for the mod rows a mission spawns:
    the union of their manifest packages plus each row's table. SystemExit naming the row
    when its mod is only carried by another pak, when the manifest has no entry for it or
    a different Guid, or names a package outside its mod folder."""
    have = rows()
    out = {}
    for row in rows_used:
        if row not in have:
            raise SystemExit('%s: no installed pak holds mod %s; install the mod to build '
                             'this mission' % (row, row.split('_')[0]))
        rec = have[row]
        mod = mods()[rec['mod']]
        if not mod['own']:
            raise SystemExit('%s: mod %s is not installed (only %s carries a copy of it); '
                             'install the mod to build this mission'
                             % (row, rec['mod'], os.path.basename(mod['pak'])))
        if mod['problems']:
            raise SystemExit('%s: %s' % (row, '; '.join(mod['problems'])))
        got = out.setdefault(rec['mod'], {'mod': mod, 'rows': [], 'packages': set(),
                                           'walked': False})
        got['rows'].append(row)
        if mod['manifest'] is None:
            got['walked'] = True
            continue
        entry = rec['entry']
        if not entry:
            raise SystemExit('%s: the Minions.yaml of mod %s has no entry for it'
                             % (row, rec['mod']))
        if str(entry.get('Guid', '')).lower().replace('-', '') != rec['guid']:
            raise SystemExit('%s: its Minions.yaml Guid %s is not the row\'s %s'
                             % (row, entry.get('Guid'), rec['guid']))
        for p in entry.get('Packages') or []:
            gp = _package(p)
            if not gp.startswith('/Game/'):
                raise SystemExit('%s: Minions.yaml lists %s, which is not a /Game package'
                                 % (row, p))
            if gp.startswith('/Game/Mods/') and not gp.startswith('/Game/Mods/%s/' % rec['mod']):
                raise SystemExit('%s: Minions.yaml lists %s, outside /Game/Mods/%s/'
                                 % (row, gp, rec['mod']))
            if gp.startswith('/Game/Mods/'):
                got['packages'].add(gp)
        got['packages'].add(rec['table'])
    return out


def carry(files, own=None, note=lambda _s: None):
    """(files, report): a mission's `files` ({inner path: bytes}) with every mod minion
    it spawns carried, copied byte for byte from the installed mod, and any copy an
    earlier build carried removed first. `own` is the mission's mod key (default: the
    folder of its mission table). report: {'rows', 'mods': {key: {pak, rows, packages,
    files, bytes, walked}}, 'notes'}. SystemExit naming the row when a listed package is
    missing, or when the mission already holds other bytes at a path it would write."""
    from riftlord.build import makemod
    files = dict(files)
    own = own or makemod.pak_mod_key(files)
    known = known_rows(files, own)
    old = carried_folders(files, own)
    for k in [k for k in files if folder(k) in old]:
        del files[k]
    report = {'rows': used(files, own, known), 'mods': {}, 'notes': []}
    for key, got in sorted(plan(report['rows']).items()):
        mod = got['mod']
        src = Source(mod['pak'])
        packages = set(got['packages'])
        if got['walked']:
            #: from every table of the mod (an ability table is reached by Guid only) and
            #: every package in its OrcsMustUI folder (MinionInfo: loaded by a built path)
            tables = ['/Game' + k[len('OMD/Content'):-7] for k in src.keys
                      if k.endswith('.uasset') and (
                          k.startswith('OMD/Content/Mods/%s/Tables/' % key)
                          or k.startswith('OMD/Content/Mods/%s/OrcsMustUI/' % key))]
            packages |= set(walk(src, key, tables))
            line = ('warning: mod %s ships no %s; its packages were found by following '
                    'its tables\' references, which can miss a package loaded by a built '
                    'path' % (key, MANIFEST))
            report['notes'].append(line)
            note(line)
        size, added = 0, []
        for gp in sorted(packages):
            stem = _inner(gp)
            have = [stem + e for e in EXTS if stem + e in src]
            if not any(h.endswith(('.uasset', '.umap')) for h in have):
                rows_named = ', '.join(r for r in got['rows']
                                       if gp in (carried_packages(r) or []) or got['walked'])
                raise SystemExit('%s: package %s is not in %s'
                                 % (rows_named or ', '.join(got['rows']), gp,
                                    os.path.basename(mod['pak'])))
            for inner in have:
                blob = src.read(inner)
                if inner in files and _bytes(files[inner]) != blob:
                    raise SystemExit('%s: the mission already holds other bytes at %s'
                                     % (', '.join(got['rows']), inner))
                files[inner] = blob
                size += len(blob)
                added.append(inner)
        report['mods'][key] = {'pak': mod['pak'], 'rows': got['rows'],
                               'packages': len(packages), 'files': len(added), 'bytes': size,
                               'walked': got['walked']}
        line = ('mod minions: %s carried from %s (%d packages, %.1f MB)'
                % (', '.join(got['rows']), os.path.basename(mod['pak']), len(packages),
                   size / 1e6))
        report['notes'].append(line)
        note(line)
    return files, report


# -- gates ----------------------------------------------------------------------------

def spawned_guids(files):
    """{coordinator inner path: {hex guid: count}} over every spawn call of every
    coordinator in `files`, along the real flow (flowsim.units_run) of each wave and
    spawner."""
    from riftlord.onslaught import flowsim
    out = {}
    skip = carried_folders(files)
    for k in sorted(files):
        if not k.endswith('.uasset') or 'Onslaught' not in k.rsplit('/', 1)[-1]:
            continue
        ux = k[:-7] + '.uexp'
        if ux not in files or folder(k) in skip:
            continue
        try:
            got = flowsim.units_run(blobs=(_bytes(files[k]), _bytes(files[ux])), named=False)
        except (Exception, SystemExit):              # noqa: BLE001
            continue
        tally = {}
        for units in got.values():
            for g in units:
                tally[g] = tally.get(g, 0) + 1
        out[k] = tally
    return out


def gates(files, own=None):
    """[(name, ok, detail)] for a built mission's mod minions; [] when it spawns and
    carries none."""
    from riftlord.build import makemod
    own = own or makemod.pak_mod_key(files)
    carried = carried_folders(files, own)
    have = known_rows(files, own)
    try:
        wanted = used(files, own, have)
    except (Exception, SystemExit) as exc:           # noqa: BLE001
        return [('mod minions read', False, str(exc))]
    if not carried and not wanted:
        return []
    out = []
    missing = sorted(r for r in wanted if have[r]['mod'] not in carried)
    out.append(('every mod minion spawned is carried (%s)' % (', '.join(wanted) or 'none'),
                not missing, ', '.join(missing)))
    for key in sorted(carried):
        mod = mods().get(key)
        mine = sorted(k for k in files if folder(k) == key)
        if not mod or not mod['own']:
            out.append(('mod %s: carried copy kept (%d files); the mod is not installed'
                        % (key, len(mine)), True, ''))
            continue
        try:
            fresh, _rep = carry({k: v for k, v in files.items() if folder(k) != key}, own)
        except SystemExit as exc:
            out.append(('mod %s: carried as the installed mod lists it' % key, False, str(exc)))
            continue
        want = sorted(k for k in fresh if folder(k) == key)
        extra, lacking = sorted(set(mine) - set(want)), sorted(set(want) - set(mine))
        differ = sorted(k for k in set(mine) & set(want) if _bytes(files[k]) != fresh[k])
        bad = (['missing %s' % ', '.join(lacking[:3])] if lacking else []) + \
              (['not listed %s' % ', '.join(extra[:3])] if extra else []) + \
              (['differ from %s: %s' % (os.path.basename(mod['pak']), ', '.join(differ[:3]))]
               if differ else [])
        out.append(('mod %s: %d files, byte for byte the installed %s'
                    % (key, len(mine), os.path.basename(mod['pak'])), not bad, '; '.join(bad)))
    from riftlord.onslaught import onslaught
    known = {struct.pack('<4i', *g).hex() for g in onslaught.shipped_protos().values()}
    by_guid = {r['guid']: r for r in have.values()}
    for coord, tally in sorted(spawned_guids(files).items()):
        unknown = sorted(g for g in tally if g not in known and g not in by_guid)
        loose = sorted(by_guid[g]['row'] for g in tally
                       if g in by_guid and by_guid[g]['mod'] not in carried | {own})
        out.append(('%s: every spawn is a shipped or carried minion (%d calls)'
                    % (coord.rsplit('/', 1)[-1][:-7], sum(tally.values())),
                    not unknown and not loose,
                    '; '.join((['unknown Guid %s' % ', '.join(unknown[:3])] if unknown else [])
                              + (['not carried %s' % ', '.join(loose)] if loose else []))))
    from riftlord.mapeditor import probe
    cache, checked, bad = {}, 0, []
    needles = [('/Game/Mods/%s/' % key).encode() for key in carried]
    for k in sorted(files):
        if not k.endswith(('.uasset', '.umap')):
            continue
        #: the carried packages, and the mission's own that name them (its hub preview)
        if folder(k) not in carried:
            head = _bytes(files[k])
            if len(head) > SCAN_MAX or not any(n in head for n in needles):
                continue
        n, problems = probe.import_problems(files, k, cache=cache)
        checked += n
        bad += problems
    if carried:
        out.append(('carried packages, and what names them, import only what ships or is '
                    'carried (%d)' % checked, not bad, '; '.join(sorted(set(bad))[:3])))
    #: the hub preview's Preview effects (makepreview.effect_gates)
    from riftlord.onslaught import makepreview
    out += makepreview.effect_gates(files)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--mission', help='a built mission pak or loose build to check')
    args = ap.parse_args(argv)
    if args.mission:
        from riftlord.core import paksource
        files = paksource.entries(args.mission)
        print('spawns: %s' % (', '.join(used(files)) or 'no mod minion'))
        bad = 0
        for name, ok, detail in gates(files):
            bad += 0 if ok else 1
            print('  %s  %s%s' % ('PASS' if ok else 'FAIL', name, (': ' + detail) if detail else ''))
        return 1 if bad else 0
    for key, mod in sorted(mods().items()):
        print('%s (%s)%s  %s' % (mod['label'], key, '' if mod['own'] else ' [carried copy]',
                                 mod['pak']))
        if mod['manifest'] is None:
            print('  no %s: a mission would carry a closure walk' % MANIFEST)
        for p in mod['problems']:
            print('  problem: %s' % p)
        for n, r in sorted(rows().items()):
            if r['mod'] != key:
                continue
            packages = carried_packages(n)
            print('  %-34s %s  %-22s %s%s  %s' % (
                n, r['guid'], r['name'] or '-', 'flyer' if r['flyer'] else 'walker',
                ' boss' if r['boss'] else '',
                '%d packages' % len(packages) if packages is not None else 'no manifest entry'))
            said = (r['entry'] or {}).get('Flyer')
            if isinstance(said, bool) and said != r['flyer']:
                print('    note: Minions.yaml Flyer %s, the table\'s bIsFlyer %s (Rift Lord and '
                      'the game use the table)' % (said, r['flyer']))
            eff, warns = preview_effect(n)
            if eff:
                print('    preview effect %s%s' % (eff['path'], ' at %s' % eff['socket']
                                                   if eff['socket'] else ''))
            for line in warns:
                print('    warning: %s' % line)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
