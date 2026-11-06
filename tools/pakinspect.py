"""Summarise a mission pak (or a loose Missions build folder), or diff two.

    python3 pakinspect.py PAK [--against OTHER] [--json] [--installed] [--all-sizes]

What it reports:
  - the mod key; its tables (Tables/ and Extends/) with row names; OrcsMustUI files;
  - each level (.umap): RiftLordFx actors counted by effect, RiftLordSky pieces, and
    whether the sublevel has BuiltData (light maps);
  - LavaRock materials; minimap and MapData packages;
  - the enemy preview's creatures (the Enemies01 Blueprint's slots);
  - makemod.registry_problems (with --installed, also clashes with paks in ~mods).
--against prints what differs from OTHER (entries, rows, effect counts, creatures...).
Reads only. Exit 1 when registry problems were found.
"""

import argparse
import collections
import json
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from riftlord.build import makemod  # noqa: E402
from riftlord.core import paksource  # noqa: E402
from riftlord.formats.ue.uassetio import Package  # noqa: E402

#: Lifted actor names (riftlord/build/world/lavafx.py, riftlord/build/world/moons.py).
FX_PREFIX = 'RiftLordFx_'
SKY_PREFIX = 'RiftLordSky_'
EXTENDS_RE = re.compile(r'/Mods/([^/]+)/%s/([^/.]+)\.uasset$' % makemod.EXTENDS_DIR)
OMUI_RE = re.compile(r'/Mods/[^/]+/%s/[^/]+$' % makemod.OMUI_DIR)
#: Offsets in a 4.27 export / import entry (uassetio EXPORT_ENTRY / IMPORT_ENTRY).
EXPORT_CLASS_AT, EXPORT_OUTER_AT, EXPORT_NAME_AT = 0, 12, 16
IMPORT_NAME_AT = 20
#: Size changes smaller than this are name-length drift (the mod key's length), counted
#: rather than listed unless --all-sizes.
SIZE_NOISE_BYTES = 1024


class Source:
    """A pak or a loose build folder, read lazily by inner path (`OMD/Content/...`)."""

    def __init__(self, path):
        self.path = path
        self.loose = paksource.is_loose(path)
        if self.loose:
            self.names = paksource.names(path)
            self._pak = None
            self._keys = {}
        else:
            from riftlord.formats.ue import pak as _pak
            self._pak = _pak.Pak(path)
            self._keys = {(k[k.index('OMD/'):] if 'OMD/' in k else k): k for k in self._pak.files}
            self.names = sorted(self._keys)
        self.sizes = {}
        for n in self.names:
            if self.loose:
                self.sizes[n] = os.path.getsize(os.path.join(path, *n.split('/')))
            else:
                e = self._pak.files[self._keys[n]]
                if not isinstance(e, dict):              # v9+: an offset into the encoded index
                    e = self._pak.entry(e)
                self.sizes[n] = e.get('usize')

    def read(self, inner):
        if self.loose:
            return paksource.read(self.path, inner)
        return self._pak.read_file(self._keys[inner])

    def package(self, inner_uasset):
        base = inner_uasset.rsplit('.', 1)[0]
        ext = '.umap' if inner_uasset.endswith('.umap') else '.uasset'
        pkg = Package(self.read(base + ext), self.read(base + '.uexp'))
        return pkg


def _fname(names, index, number):
    text = names[index] if 0 <= index < len(names) else '?%d' % index
    return text if number == 0 else '%s_%d' % (text, number - 1)


def _object_name(pkg, names, ref):
    """The object name a package index (export > 0, import < 0) points at."""
    if ref > 0:
        meta = pkg.exports[ref - 1]['meta']
        return _fname(names, *struct.unpack_from('<ii', meta, EXPORT_NAME_AT))
    if ref < 0:
        entry = pkg.imports[-ref - 1]
        return _fname(names, *struct.unpack_from('<ii', entry, IMPORT_NAME_AT))
    return None


def level_actors(pkg):
    """({effect: count} of RiftLordFx actors, [RiftLordSky piece names]) in a level."""
    names = [s for s, _h in pkg.names]
    fx, sky = collections.Counter(), []
    for e in pkg.exports:
        meta = e['meta']
        name = _fname(names, *struct.unpack_from('<ii', meta, EXPORT_NAME_AT))
        outer = struct.unpack_from('<i', meta, EXPORT_OUTER_AT)[0]
        if _object_name(pkg, names, outer) != 'PersistentLevel':
            continue                                 # components sit inside their actor
        if name.startswith(FX_PREFIX):
            fx[re.sub(r'_\d+$', '', name[len(FX_PREFIX):])] += 1
        elif name.startswith(SKY_PREFIX):
            sky.append(name[len(SKY_PREFIX):])
    return dict(sorted(fx.items())), sorted(sky)


def table_rows(src, inner):
    try:
        return [r for r, _g in makemod._table_rows(src.read(inner), src.read(inner[:-7] + '.uexp'))]
    except Exception as exc:                         # noqa: BLE001
        return ['<unreadable: %s>' % exc]


def preview_creatures(src, inner):
    from riftlord.onslaught import makepreview
    try:
        pkg = src.package(inner)
        return [c for _e, c, _m, _a in makepreview.slot_exports(pkg)]
    except Exception as exc:                         # noqa: BLE001
        return ['<unreadable: %s>' % exc]


def inspect(path, installed=False):
    src = Source(path)
    names = src.names
    key = makemod.pak_mod_key(names)
    out = {'path': os.path.abspath(path), 'entries': len(names),
           'bytes': sum(v for v in src.sizes.values() if v), 'mod': key,
           'tables': {}, 'extends': {}, 'omui': [], 'levels': {}, 'lavarock': [],
           'minimap': [], 'mapdata': [], 'preview': {}, 'registry_problems': []}
    for n in names:
        m = makemod.MOD_TABLE_RE.search(n)
        if m:
            out['tables'][m.group(2)] = table_rows(src, n)
        m = EXTENDS_RE.search(n)
        if m:
            out['extends'][m.group(2)] = table_rows(src, n)
        if OMUI_RE.search(n):
            out['omui'].append('%s (%d bytes)' % (n.rsplit('/', 1)[-1], src.sizes.get(n) or 0))
        if '/LavaRock/' in n and n.endswith('.uasset'):
            out['lavarock'].append(n.rsplit('/', 1)[-1][:-7])
        if n.endswith('_Minimap.uasset'):
            out['minimap'].append(n.rsplit('/', 1)[-1][:-7])
        if n.endswith('_MapData.uasset'):
            out['mapdata'].append(n.rsplit('/', 1)[-1][:-7])
        if n.endswith('_Enemies01.uasset'):
            out['preview'][n.rsplit('/', 1)[-1][:-7]] = preview_creatures(src, n)
    built = {n[:-len('_BuiltData.uasset')] for n in names if n.endswith('_BuiltData.uasset')}
    for n in names:
        if not n.endswith('.umap'):
            continue
        stem = n[:-5]
        lv = {'builtData': stem in built,
              'builtDataBytes': (src.sizes.get(stem + '_BuiltData.uexp') or 0) if stem in built else 0}
        try:
            fx, sky = level_actors(src.package(n))
            lv.update(fx=fx, fxTotal=sum(fx.values()), sky=sky)
        except Exception as exc:                     # noqa: BLE001
            lv['error'] = str(exc)
        out['levels'][stem.rsplit('/', 1)[-1]] = lv
    # registry_problems reads only tables and OrcsMustUI files; the rest need only a name
    files = {}
    for n in names:
        wanted = (makemod.MOD_TABLE_RE.search(n) or EXTENDS_RE.search(n) or OMUI_RE.search(n)
                  or (n.endswith('.uexp') and ('/Tables/' in n or '/%s/' % makemod.EXTENDS_DIR in n)))
        files[n] = src.read(n) if wanted else b''
    try:
        out['registry_problems'] = makemod.registry_problems(
            files, pak_name=os.path.basename(path) if not src.loose else None, installed=installed)
    except Exception as exc:                         # noqa: BLE001
        out['registry_problems'] = ['registry check failed: %s' % exc]
    out['_names'] = names
    out['_sizes'] = src.sizes
    return out


def _strip_mod(name, key):
    return name.replace(key, '<mod>') if key else name


def diff(a, b):
    """What differs from `b` to `a`, with each pak's mod key folded to <mod> so two
    builds of one record under different keys compare by layout."""
    ka, kb = a['mod'], b['mod']
    na = {_strip_mod(n, ka) for n in a['_names']}
    nb = {_strip_mod(n, kb) for n in b['_names']}
    out = {'only_in_this': sorted(na - nb), 'only_in_other': sorted(nb - na)}
    sizes = []
    sa = {_strip_mod(n, ka): v for n, v in a['_sizes'].items()}
    sb = {_strip_mod(n, kb): v for n, v in b['_sizes'].items()}
    for n in sorted(na & nb):
        if sa.get(n) is not None and sb.get(n) is not None and sa[n] != sb[n]:
            sizes.append((n, sb[n], sa[n]))
    out['size_changed'] = [x for x in sizes if abs(x[2] - x[1]) >= SIZE_NOISE_BYTES]
    out['size_drift'] = [x for x in sizes if abs(x[2] - x[1]) < SIZE_NOISE_BYTES]
    for field in ('tables', 'extends'):
        ta = {_strip_mod(k, ka): [_strip_mod(r, ka) for r in v] for k, v in a[field].items()}
        tb = {_strip_mod(k, kb): [_strip_mod(r, kb) for r in v] for k, v in b[field].items()}
        out[field] = {k: {'this': ta.get(k), 'other': tb.get(k)}
                      for k in sorted(set(ta) | set(tb)) if ta.get(k) != tb.get(k)}
    la = {_strip_mod(k, ka): v for k, v in a['levels'].items()}
    lb = {_strip_mod(k, kb): v for k, v in b['levels'].items()}
    lv = {}
    for k in sorted(set(la) | set(lb)):
        x, y = la.get(k) or {}, lb.get(k) or {}
        d = {f: {'this': x.get(f), 'other': y.get(f)}
             for f in ('fx', 'sky', 'builtData') if x.get(f) != y.get(f)}
        if d:
            lv[k] = d
    out['levels'] = lv
    for field in ('lavarock', 'minimap', 'mapdata', 'omui', 'registry_problems'):
        xa = sorted(_strip_mod(s, ka) for s in a[field])
        xb = sorted(_strip_mod(s, kb) for s in b[field])
        if field == 'omui':                         # sizes vary with notes text
            xa = [s.split(' (')[0] for s in xa]
            xb = [s.split(' (')[0] for s in xb]
        if xa != xb:
            out[field] = {'added': sorted(set(xa) - set(xb)), 'removed': sorted(set(xb) - set(xa))}
    pa = sorted(c for v in a['preview'].values() for c in v)
    pb = sorted(c for v in b['preview'].values() for c in v)
    if pa != pb:
        out['preview'] = {'this': pa, 'other': pb}
    out['same_layout'] = not (out['only_in_this'] or out['only_in_other'])
    return out


def _print_summary(r):
    print('%s' % r['path'])
    print('  %d entries, %.1f MB; mod %s' % (r['entries'], r['bytes'] / 1e6, r['mod']))
    for field, label in (('tables', 'table'), ('extends', 'extends')):
        for t, rows in sorted(r[field].items()):
            print('  %-8s %s: %s' % (label, t, ', '.join(rows) or '(no rows)'))
    print('  OrcsMustUI: %s' % (', '.join(r['omui']) or 'none'))
    for name, lv in sorted(r['levels'].items()):
        fx = ', '.join('%s %d' % kv for kv in lv.get('fx', {}).items()) or 'none'
        print('  level %s: BuiltData %s; fx %d (%s); sky %s%s' % (
            name, ('%.1f MB' % (lv['builtDataBytes'] / 1e6)) if lv['builtData'] else 'none',
            lv.get('fxTotal', 0), fx, ', '.join(lv.get('sky', [])) or 'none',
            ('; ERROR ' + lv['error']) if lv.get('error') else ''))
    print('  LavaRock materials (%d): %s' % (len(r['lavarock']), ', '.join(r['lavarock']) or 'none'))
    print('  minimap: %s; MapData: %s' % (', '.join(r['minimap']) or 'none', ', '.join(r['mapdata']) or 'none'))
    for bp, creatures in r['preview'].items():
        print('  enemy preview %s (%d): %s' % (bp, len(creatures), ', '.join(creatures)))
    if not r['preview']:
        print('  enemy preview: none')
    print('  registry problems: %s' % ('none' if not r['registry_problems'] else ''))
    for p in r['registry_problems']:
        print('    - %s' % p)


def _print_diff(d, other, all_sizes=False):
    print('\nagainst %s:' % other)
    if d['same_layout'] and not any(d.get(k) for k in ('size_changed', 'tables', 'extends', 'levels',
                                                        'lavarock', 'minimap', 'mapdata', 'omui',
                                                        'registry_problems', 'preview')):
        print('  same entries, sizes, rows, effects and creatures')
        return
    for k, label in (('only_in_this', 'only here'), ('only_in_other', 'only there')):
        for n in d[k]:
            print('  %-10s %s' % (label, n))
    for n, was, now in d['size_changed'] + (d['size_drift'] if all_sizes else []):
        print('  size       %s: %d -> %d (%+d)' % (n, was, now, now - was))
    if d['size_drift'] and not all_sizes:
        print('  size       %d entries differ by under %d bytes (name lengths; --all-sizes lists them)'
              % (len(d['size_drift']), SIZE_NOISE_BYTES))
    for field in ('tables', 'extends'):
        for t, v in d[field].items():
            print('  %-10s %s: there %s / here %s' % (field[:-1], t, v['other'], v['this']))
    for lv, fields in d['levels'].items():
        for f, v in fields.items():
            print('  level      %s %s: there %s / here %s' % (lv, f, v['other'], v['this']))
    for field in ('lavarock', 'minimap', 'mapdata', 'omui', 'registry_problems'):
        if field in d:
            print('  %-10s +%s -%s' % (field, d[field]['added'], d[field]['removed']))
    if 'preview' in d:
        print('  preview    there %s / here %s' % (d['preview']['other'], d['preview']['this']))


def public(r):
    return {k: v for k, v in r.items() if not k.startswith('_')}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('pak', help='a .pak, or a loose Missions build folder')
    ap.add_argument('--against', metavar='OTHER', help='diff against this pak or folder')
    ap.add_argument('--json', action='store_true', help='print JSON instead of text')
    ap.add_argument('--all-sizes', action='store_true',
                    help='list every size difference, not only those of %d bytes or more'
                    % SIZE_NOISE_BYTES)
    ap.add_argument('--installed', action='store_true',
                    help='also check clashes with paks installed in ~mods')
    args = ap.parse_args(argv)
    r = inspect(args.pak, args.installed)
    d = None
    if args.against:
        o = inspect(args.against, args.installed)
        d = diff(r, o)
    if args.json:
        out = public(r)
        if d is not None:
            out = {'this': out, 'other': public(o), 'diff': d}
        print(json.dumps(out, indent=1))
    else:
        _print_summary(r)
        if d is not None:
            _print_diff(d, args.against, args.all_sizes)
    return 1 if r['registry_problems'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
