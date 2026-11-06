"""A mission's Working Title: the name its level goes by in the game.

A shipped mission plays a level named like `Scenario_10`: the package
/Game/Maps/Campaign/Scenario_10/Scenario_10, its World, and its sublevels
`Scenario_10_<tag>`. The name players see is the mission row's MissionName FText
("Coastal Hallways"). A Rift Lord build names its level `<mod>_Map01`
(/Game/Mods/<mod>/Maps/<mod>_Map01/<mod>_Map01, sublevels `<mod>_Map01_<tag>`, with the
level's minimap and map data in the same folder). A Working Title (`scenario_fear_01`)
is that internal name; the display name stays the mission's title. A new mission is
given `RL_Scen_<ScenarioName>` (fresh), unique among the missions and shipped names with
a letter suffix; a mission with none stored keeps `<mod>_Map01`.

The loose build in Missions keeps `<mod>_Map01`; retitle() renames the level in the files a
pak is packed from (bucket._pack), so the build and every tool reading it are unchanged.
The mission's Guids, rows, mod folder and pak name stay as they are.

A title ending `_<digits>` without a leading zero (`scenario_fear_1`) is stored as the cooker
stores `Scenario_10`: an FName of the base string with number digits + 1, on every entry that
ends with the title (the package path, the World, the level's own object path, the ubergraph)
and on every reference to them (export and import names, the class's function map, the
level's soft references to its own actors, the mission rows' MissionLevel).

    python3 -m riftlord.build.leveltitle check TITLE
"""

import argparse
import re
import struct

#: A build's own level name, `<mod>_Map01`: the level of a mission with no Working Title
#: stored (every mission made before 2026-10-30).
DEFAULT = '%s_Map01'
#: A new mission's Working Title: `RL_Scen_<ScenarioName>`, the mission's title in
#: letters and digits (scenario_name).
SCENARIO = 'RL_Scen_%s'
#: The ScenarioName of a title with no letters or digits.
SCENARIO_BLANK = 'Mission'
#: Longest Working Title: as long as a mod key may be (bucket.KEY_RE).
MAX_LENGTH = 64
#: Letters, digits and underscores, starting with a letter.
PATTERN = re.compile(r'^[A-Za-z][A-Za-z0-9_]*$')
#: The sub-path every soft reference to one of the level's own actors starts with.
SELF_SUBPATH = b'PersistentLevel.'


def default(mod):
    return DEFAULT % mod


def letter(n):
    """The suffix of a name's `n`-th spelling: '' for the first, then B, C, ... Z, BA, BB...
    A letter, never `_<digits>`, which the cooker would store as an FName number."""
    if n <= 1:
        return ''
    out, k = '', n - 1
    while True:
        out = chr(ord('A') + k % 26) + out
        k //= 26
        if not k:
            return out


def scenario_name(title):
    """A mission title in asset-name characters: "North Wings" -> `NorthWings`; digits
    alone ("1984") follow SCENARIO_BLANK, so the name never ends `_<digits>`."""
    text = ''.join(c for c in str(title or '') if c.isascii() and c.isalnum())
    return text if text and not text.isdigit() else SCENARIO_BLANK + text


def fresh(title, taken=None, shipped=None):
    """`RL_Scen_<ScenarioName>` for a mission titled `title`, with a letter suffix
    (letter) past any name in `taken` ({lower-case name: ...}) or `shipped`
    (shipped_names(), read when None)."""
    shipped = shipped_names() if shipped is None else shipped
    taken = taken or {}
    n = 1
    while True:
        tail = letter(n)
        name = (SCENARIO % scenario_name(title))[:MAX_LENGTH - len(tail)] + tail
        if name.lower() not in taken and name.lower() not in shipped:
            return name
        n += 1


def fname_split(text):
    """(string, number) a cooked FName stores for `text` (apply.fname_split)."""
    from riftlord.build import apply
    return apply.fname_split(text)


def shipped_names():
    """{lower-case name: name} of every shipped level and sublevel package and every
    shipped mission row."""
    from riftlord.build.navigation import navpatch
    from riftlord.build import missionproto
    out = {}
    try:
        for key in navpatch._index():
            if key.endswith('.umap'):
                leaf = key[:-len('.umap')].rsplit('/', 1)[-1]
                out.setdefault(leaf.lower(), leaf)
    except (Exception, SystemExit):                  # noqa: BLE001
        pass
    try:
        for row in missionproto.read().rows:
            out.setdefault(str(row['name']).lower(), str(row['name']))
    except (Exception, SystemExit):                  # noqa: BLE001
        pass
    return out


def problems(text, taken=None, shipped=None):
    """Why `text` cannot be a Working Title ([] when it can; '' is the default name).
    `taken` is {lower-case name: mission title} of the other missions' level names;
    `shipped` is shipped_names() (read when None)."""
    text = str(text or '')
    if not text:
        return []
    out = []
    if len(text) > MAX_LENGTH:
        out.append('Working Title is at most %d characters.' % MAX_LENGTH)
    if not PATTERN.match(text):
        out.append('Working Title takes letters, digits and underscores, starting with a '
                   'letter.')
    if text.lower() == 'none':
        out.append('Working Title cannot be None.')
    shipped = shipped_names() if shipped is None else shipped
    if text.lower() in shipped:
        out.append('%s is a shipped level.' % shipped[text.lower()])
    if text.lower() in (taken or {}):
        out.append('%s is the level of %s.' % (text, taken[text.lower()]))
    return out


#: A build's own persistent level by its shape, whatever its name (`<mod>_Map01` or a
#: Working Title): OMD/Content/Mods/<mod>/Maps/<name>/<name>.umap.
LEVEL_RE = re.compile(r'(?:^|/)OMD/Content/Mods/([^/]+)/Maps/([^/]+)/\2\.umap$')


def is_level(name):
    """Whether entry `name` is a build's own persistent level: by its shape (LEVEL_RE),
    whatever its Working Title names it, or `<x>_Map01.umap` as a gate's check level is
    written (crystal, kitactors, chandelier: `Maps/<x>_Map01.umap`)."""
    return bool(LEVEL_RE.search(name)) or name.endswith('_Map01.umap')


def level_entry(files, mod=None):
    """The pak entry of a build's own level (`OMD/Content/Mods/<mod>/Maps/<name>/<name>.umap`,
    as navpatch.maps_in_pak finds a map), or None. `files` is a build's entry names (or
    {name: bytes}); `mod` picks one mission's level from a pak holding several (a campaign's).
    A build's level is found by its shape, never by `_Map01` alone: a packed build's is named
    by its Working Title. Without that shape, a gate's check level named `<x>_Map01.umap`."""
    for name in sorted(files):
        m = LEVEL_RE.search(name)
        if m and (mod is None or m.group(1) == mod):
            return name
    return next((n for n in sorted(files) if n.endswith('_Map01.umap')
                 and (mod is None or '/Mods/%s/' % mod in n)), None)


def level_maps(files, mod=None):
    """The entries of a build's own level and its sublevels (`<name>_<tag>.umap` beside it),
    level first; [] when it has none."""
    level = level_entry(files, mod)
    if level is None:
        return []
    folder, stem = level.rsplit('/', 1)[0] + '/', level.rsplit('/', 1)[1][:-len('.umap')]
    subs = sorted(n for n in files if n.startswith(folder) and n.endswith('.umap')
                  and n.rsplit('/', 1)[1].startswith(stem + '_'))
    return [level] + subs


def sublevel_entry(files, tag, mod=None):
    """The entry of a build's sublevel `<level>_<tag>.umap`, or None."""
    level = level_entry(files, mod)
    if level is None:
        return None
    name = level[:-len('.umap')] + '_%s.umap' % tag
    return name if name in files else None


def level_files(source, mod=None):
    """{entry: bytes} of a built mission's own level, its .umap and .uexp: from `source`, a
    pak or loose build folder (paksource), or {entry: bytes} already read. {} when none."""
    if isinstance(source, dict):
        names, read = source, source.__getitem__
    else:
        from riftlord.core import paksource
        names = paksource.names(source)

        def read(name):
            return paksource.read(source, name)
    umap = level_entry(names, mod)
    if umap is None or umap[:-5] + '.uexp' not in names:
        return {}
    return {umap: read(umap), umap[:-5] + '.uexp': read(umap[:-5] + '.uexp')}


def _full(name, number):
    return name if not number else '%s_%d' % (name, number - 1)


def _renumber(pkg, indices, number):
    """Give every reference to name entries `indices` that carries number 0 the FName
    number `number`: import and export names, function map entries (name, then the
    export it names), and soft references to the level's own actors (name, number,
    sub-path `PersistentLevel.`...). Returns how many were changed."""
    changed = 0
    for i, rec in enumerate(pkg.imports):
        fields = list(struct.unpack('<iiiiiii', rec))
        for at in (0, 2, 5):
            if fields[at] in indices and fields[at + 1] == 0:
                fields[at + 1] = number
                changed += 1
        pkg.imports[i] = struct.pack('<iiiiiii', *fields)
    named = {}
    for k, e in enumerate(pkg.exports):
        idx, num = struct.unpack_from('<ii', e['meta'], 16)
        if idx in indices and num == 0:
            named[k + 1] = idx
    for e in pkg.exports:
        data = e.get('data')
        if not data:
            continue
        buf = bytearray(data)
        for idx in indices:
            head = struct.pack('<ii', idx, 0)
            at = buf.find(head)
            while at >= 0:
                follow = struct.unpack_from('<i', buf, at + 8)[0] if at + 12 <= len(buf) else 0
                self_ref = (0 < follow < 512 and
                            buf[at + 12:at + 12 + len(SELF_SUBPATH)] == SELF_SUBPATH)
                if self_ref or named.get(follow) == idx:
                    struct.pack_into('<i', buf, at + 4, number)
                    changed += 1
                at = buf.find(head, at + 1)
        if bytes(buf) != data:
            e['data'] = bytes(buf)
    for k, e in enumerate(pkg.exports):
        if k + 1 in named:
            meta = bytearray(e['meta'])
            struct.pack_into('<i', meta, 20, number)
            e['meta'] = bytes(meta)
            changed += 1
    return changed


def _rename_names(pkg, word, title, base, number):
    """Rename name entries matching `word` to `title`; with a split title (`number`),
    the entries now ending with it end with `base` instead. Returns (renamed count,
    indices of the split ones)."""
    from riftlord.formats.ue.uassetio import fname_hashes
    renamed, split = 0, set()
    for i, (s, _h) in enumerate(list(pkg.names)):
        new = word.sub(title, s)
        if new == s:
            continue
        if number and new.endswith(title) and fname_split(s)[1] == 0 \
                and (len(new) == len(title) or not new[-len(title) - 1].isalnum()):
            new = new[:-len(title)] + base
            split.add(i)
        pkg.names[i] = (new, fname_hashes(new))
        renamed += 1
    return renamed, split


def retitle(files, mod, title):
    """(files, report): a build's files ({inner path: bytes}) with its level `<mod>_Map01`
    named `title` -- package paths, file names, the World, sublevels, the level script's
    names and the mission rows' MissionLevel -- in the packages under its own mod folder.
    Unchanged when `title` is empty or the default. Checked before it returns; SystemExit
    names what did not read back."""
    from riftlord.build import missionproto
    from riftlord.formats.ue.uassetio import Package
    old = default(mod)
    if not title or title == old:
        return files, {}
    bad = problems(title, shipped={})
    if bad:
        raise SystemExit(bad[0])
    word = re.compile(r'(?<![A-Za-z0-9])%s(?![A-Za-z0-9])' % re.escape(old))
    own = 'OMD/Content/Mods/%s/' % mod
    if '%sMaps/%s/%s.umap' % (own, old, old) not in files:
        raise SystemExit('%s has no level (%s) to give a Working Title'
                         % (mod, old))
    base, number = fname_split(title)
    out, report = {}, {'title': title, 'from': old, 'packages': 0, 'renumbered': 0,
                       'split': bool(number)}
    for name, data in files.items():
        stem, ext = name.rsplit('.', 1) if '.' in name else (name, '')
        if not name.startswith(own) or ext not in ('uasset', 'umap') \
                or stem + '.uexp' not in files:
            continue
        #: a BuiltData's textures record absolute offsets, rewritten by its own writer
        built = None
        if stem.endswith('_BuiltData'):
            from riftlord.build.lighting import lightmaps
            built = lightmaps.BuiltData(data, files[stem + '.uexp'], files.get(stem + '.ubulk', b''))
            pkg = built.pkg
        else:
            pkg = Package(data, files[stem + '.uexp'])
            pkg.load_payloads()
        renamed, split = _rename_names(pkg, word, title, base, number)
        if not renamed:
            continue
        if built is not None:
            if split:
                report['renumbered'] += _renumber(pkg, split, number)
            out[name], out[stem + '.uexp'], ubulk = built.write()
            if ubulk != files.get(stem + '.ubulk', b''):
                raise SystemExit('%s: its .ubulk moved when the level was named %s'
                                 % (stem.rsplit('/', 1)[-1], title))
            report['packages'] += 1
            continue
        table = None
        if split and name.endswith('_MissionProtos.uasset'):
            table = missionproto.Table(pkg)
            for row in table.rows:
                f = table.field(row, 'MissionLevel')
                if f is None or len(f.payload) < 8:
                    continue
                idx, num = struct.unpack_from('<ii', bytes(f.payload), 0)
                if idx in split and num == 0:
                    payload = bytearray(f.payload)
                    struct.pack_into('<i', payload, 4, number)
                    f.payload = payload
                    report['renumbered'] += 1
        elif split:
            report['renumbered'] += _renumber(pkg, split, number)
        ua, ux = table.write() if table is not None else pkg.write()
        out[name], out[stem + '.uexp'] = ua, ux
        report['packages'] += 1
    moved = {}
    for name, data in files.items():
        data = out.get(name, data)
        moved[word.sub(title, name) if name.startswith(own) else name] = data
    _check(moved, mod, old, title, word)
    return moved, report


def _check(files, mod, old, title, word):
    """SystemExit unless the retitled files read back as `title`."""
    from riftlord.build import makelevel, missionproto
    from riftlord.core import pkgcheck
    from riftlord.formats.ue.uassetio import Package
    own = 'OMD/Content/Mods/%s/' % mod
    path = '/Game/Mods/%s/Maps/%s/%s' % (mod, title, title)
    entry = 'OMD/Content' + path[len('/Game'):] + '.umap'
    bad = []
    if entry not in files:
        bad.append('the level is not at %s' % entry)
    for name, data in files.items():
        stem, ext = name.rsplit('.', 1) if '.' in name else (name, '')
        if not name.startswith(own):
            continue
        if word.search(name):
            bad.append('%s keeps the old name' % name)
        if ext not in ('uasset', 'umap') or stem + '.uexp' not in files:
            continue
        pkg = Package(data, files[stem + '.uexp'])
        pkg.load_payloads()
        left = [s for s, _h in pkg.names if word.search(s)]
        if left:
            bad.append('%s still names %s' % (stem.rsplit('/', 1)[-1], left[0]))
        kind = 'table' if name.endswith('Protos.uasset') else 'object'
        bad += pkgcheck.problems(data, files[stem + '.uexp'], stem.rsplit('/', 1)[-1], kind)
        if name == entry:
            if makelevel.self_ref_problems(pkg, path):
                bad.append('the level\'s references to its actors do not name its World')
            #: a copy of Scenario_10..18 keeps its World's shipped split name
            world = _full(*makelevel.world_name(pkg))
            if world != title and not makelevel.world_name(pkg)[1]:
                bad.append('the World is named %s' % world)
        if name.endswith('_MissionProtos.uasset'):
            names = [s for s, _h in pkg.names]
            table = missionproto.Table(pkg)
            for row in table.rows:
                f = table.field(row, 'MissionLevel')
                if f is None or len(f.payload) < 8:
                    continue
                idx, num = struct.unpack_from('<ii', bytes(f.payload), 0)
                got = _full(names[idx], num)
                if not got.startswith(path + '.'):
                    bad.append('%s plays %s' % (row['name'], got))
    if bad:
        raise SystemExit('Working Title %s not applied: %s' % (title, '; '.join(bad[:3])))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('check', help='why a Working Title cannot be used')
    c.add_argument('title')
    args = ap.parse_args()
    if args.cmd == 'check':
        bad = problems(args.title)
        print('\n'.join(bad) if bad else 'ok (stored as %s, number %d)' % fname_split(args.title))
        raise SystemExit(1 if bad else 0)


if __name__ == '__main__':
    main()
