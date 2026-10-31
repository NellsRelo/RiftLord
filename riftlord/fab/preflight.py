# Generated from OMD2-Converter/thirdparty/rounds/mat2/preflight.py by thirdparty/fabconvert/port_riftlord.py; edit the source.
"""Pre-flight: compare a staged pak's structure with the PROVEN mat1 pak (702, loaded in game
2026-10-13) and fail loudly on any difference that matters to mounting or loading.

    python3 -I thirdparty/rounds/mat2/preflight.py PAK [LUA_STATIONS]

Checks, each against the reference pak where a reference exists:
  footer       encrypted-index flag, magic, version, index hash (SHA1 of the index)
  index        mount point string, entry count, no duplicate paths, ASCII paths
  entries      the in-file header copy == the index record; compression 0, flags 0, block
               size 0, size == uncompressed size, SHA1 of the payload == the recorded hash,
               payloads inside the file and not overlapping
  paths        every path under OMD/Content/Mods/<one root>/, every .uasset has a .uexp
  packages     summary tag, file/licensee versions, custom versions, package flags equal the
               reference's (per export class); the export named like the package exists
               (moved-package-keeps-object-name trap) and the summary's package name is it
  Lua          every /Game/Mods/... object path in Stations.lua names an export in the pak
Exit 1 with every failure listed.
"""

import collections
import hashlib
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
sys.path[:0] = [REPO, os.path.join(REPO, 'UE Assets')]

from riftlord.formats.ue.uassetio import Package                         # noqa: E402

#: The packs proven in game: mat1's MICs and textures, sm1's static meshes.
REFERENCES = [os.path.join(REPO, 'thirdparty', 'rounds', 'mat1', '702-MatProbe_Swatches_P.pak'),
              os.path.join(REPO, 'thirdparty', 'rounds', 'sm1', '701-SMProbe_DocksMeshes_P.pak')]
#: What the references prove, without their art: pak footer/mount values and the package
#: summary per export class. Used when the reference paks are not there (Rift Lord's copy never
#: ships them: they hold converted pack art). Written by `preflight.py --write-facts`.
FACTS = os.path.join(HERE, 'data', 'preflight-facts.json')
MAGIC = 0x5A6F12E1
FOOTER = 45
ENTRY = 53


def fstr(d, o):
    n = struct.unpack_from('<i', d, o)[0]
    o += 4
    if n < 0:
        return d[o:o - 2 * n].decode('utf-16-le').rstrip('\0'), o - 2 * n
    return d[o:o + n].decode('latin1').rstrip('\0'), o + n


def read_pak(path):
    d = open(path, 'rb').read()
    f = d[-FOOTER:]
    enc, magic, ver, ioff, isz = struct.unpack_from('<BIIqq', f)
    ihash = f[25:45]
    idx = d[ioff:ioff + isz]
    mount, o = fstr(idx, 0)
    n = struct.unpack_from('<i', idx, o)[0]
    o += 4
    ents = []
    for _ in range(n):
        name, o = fstr(idx, o)
        rec = idx[o:o + ENTRY]
        off, size, usize, comp = struct.unpack_from('<qqqI', rec)
        sha = rec[28:48]
        flags, block = struct.unpack_from('<BI', rec, 48)
        ents.append({'name': name, 'rec': rec, 'off': off, 'size': size, 'usize': usize, 'comp': comp,
                     'sha': sha, 'flags': flags, 'block': block})
        o += ENTRY
    return {'data': d, 'enc': enc, 'magic': magic, 'ver': ver, 'ioff': ioff, 'isz': isz, 'ihash': ihash,
            'index_ok': hashlib.sha1(idx).digest() == ihash, 'mount': mount, 'entries': ents,
            'index_tail': len(idx) - o}


def payload(p, e):
    return p['data'][e['off'] + ENTRY:e['off'] + ENTRY + e['size']]


def summary(ua):
    """The fixed head of FPackageFileSummary (cooked, 4.27)."""
    tag, legacy, legacy_ue3, ue4, lic = struct.unpack_from('<Iiiii', ua, 0)
    o = 20
    ncv = struct.unpack_from('<i', ua, o)[0]
    o += 4
    cvs = tuple(sorted((ua[o + 20 * k:o + 20 * k + 16].hex(), struct.unpack_from('<i', ua, o + 20 * k + 16)[0])
                       for k in range(ncv)))
    o += 20 * ncv
    total = struct.unpack_from('<i', ua, o)[0]
    folder, o2 = fstr(ua, o + 4)
    flags = struct.unpack_from('<I', ua, o2)[0]
    return {'tag': tag, 'legacy': legacy, 'ue4': ue4, 'licensee': lic, 'custom_versions': cvs,
            'folder': folder, 'flags': flags}


def package_facts(p):
    files = {e['name']: payload(p, e) for e in p['entries']}
    out = {}
    for name, ua in files.items():
        if not name.endswith('.uasset'):
            continue
        stem = name[:-len('.uasset')]
        ux = files.get(stem + '.uexp')
        s = summary(ua)
        pkg = Package(ua, ux) if ux is not None else None
        names = [n for n, _h in pkg.names] if pkg else []
        exports = []
        for e in (pkg.exports if pkg else []):
            ci = struct.unpack_from('<i', e['meta'], 0)[0]
            cls = names[struct.unpack('<7i', pkg.imports[-ci - 1])[5]] if ci < 0 else '?'
            exports.append((names[struct.unpack_from('<i', e['meta'], 16)[0]], cls))
        out[stem] = {'summary': s, 'exports': exports, 'has_uexp': ux is not None,
                     'game_path': '/Game/' + stem.split('OMD/Content/', 1)[1]}
    return out


def proven_facts(refs):
    """{'pak': footer/mount values, 'classes': {export class: summary}} from the reference paks,
    else from FACTS."""
    if not refs:
        import json
        with open(FACTS) as fh:
            got = json.load(fh)
        for s in got['classes'].values():
            s['custom_versions'] = tuple(tuple(cv) for cv in s['custom_versions'])
        return got
    proven = {}
    for r in refs:
        for stem, f in package_facts(r).items():
            for _n, cls in f['exports']:
                proven.setdefault(cls, f['summary'])
    return {'pak': {k: refs[0][k] for k in ('enc', 'magic', 'ver', 'mount')}, 'classes': proven}


def main(argv):
    if argv[:1] == ['--write-facts']:
        import json
        with open(FACTS, 'w') as fh:
            json.dump(proven_facts([read_pak(r) for r in REFERENCES]), fh, indent=1, sort_keys=True)
        print(FACTS)
        return 0
    pak = argv[0]
    lua = argv[1] if len(argv) > 1 else None
    fails, notes = [], []
    p = read_pak(pak)
    refs = [read_pak(r) for r in REFERENCES if os.path.exists(r)]
    facts = proven_facts(refs)
    for k in ('enc', 'magic', 'ver', 'mount'):
        if p[k] != facts['pak'][k]:
            fails.append('footer/index %s = %r, proven 702 has %r' % (k, p[k], facts['pak'][k]))
    if not p['index_ok']:
        fails.append('index SHA1 in the footer does not match the index')
    if p['index_tail']:
        fails.append('%d stray bytes after the index records' % p['index_tail'])
    names = [e['name'] for e in p['entries']]
    dup = [n for n, c in collections.Counter(names).items() if c > 1]
    if dup:
        fails.append('duplicate paths: %s' % dup[:5])
    roots = set()
    spans = []
    for e in p['entries']:
        n = e['name']
        if not n.isascii() or ' ' in n:
            fails.append('non-ASCII or spaced path %r' % n)
        m = re.match(r'^OMD/Content/Mods/([A-Za-z0-9_]+)/', n)
        if not m:
            fails.append('path outside OMD/Content/Mods/<root>/: %s' % n)
        else:
            roots.add(m.group(1))
        if (e['comp'], e['flags'], e['block']) != (0, 0, 0) or e['size'] != e['usize']:
            fails.append('%s: compression %d flags %d block %d size %d/%d (702: all 0, sizes equal)'
                         % (n, e['comp'], e['flags'], e['block'], e['size'], e['usize']))
        head = p['data'][e['off']:e['off'] + ENTRY]
        want = struct.pack('<qqqI', 0, e['size'], e['usize'], 0) + e['sha'] + struct.pack('<BI', 0, 0)
        if head != want:
            fails.append('%s: in-file header differs from the index record (offset field 0 in-file)' % n)
        if hashlib.sha1(payload(p, e)).digest() != e['sha']:
            fails.append('%s: payload SHA1 mismatch' % n)
        if e['off'] + ENTRY + e['size'] > p['ioff']:
            fails.append('%s: payload runs into the index' % n)
        spans.append((e['off'], e['off'] + ENTRY + e['size']))
    spans.sort()
    for (a0, a1), (b0, _b1) in zip(spans, spans[1:]):
        if b0 < a1:
            fails.append('overlapping payloads at %d' % b0)
            break
    if len(roots) != 1:
        fails.append('expected one mount folder under Mods/, found %s' % sorted(roots))
    for r in refs:                                  # the reference must pass its own entry check
        hd = r['data'][r['entries'][0]['off']:r['entries'][0]['off'] + ENTRY]
        if hd[:8] != b'\0' * 8:
            notes.append('reference %s stores the offset in-file: in-file header rule not proven' % r['mount'])
    # packages vs the proven ones, per export class
    ours = package_facts(p)
    proven = facts['classes']
    for stem, f in sorted(ours.items()):
        leaf = stem.rsplit('/', 1)[-1]
        if not f['has_uexp']:
            fails.append('%s: .uasset without .uexp' % stem)
            continue
        exp = dict(f['exports'])
        if leaf not in exp:
            fails.append('%s: no export named %s (exports %s)' % (stem, leaf, list(exp)[:4]))
            continue
        cls = exp[leaf]
        ref_s = proven.get(cls)
        if ref_s is None:
            fails.append('%s: main export class %s was never proven in game (701/702)' % (stem, cls))
            continue
        for k in ('tag', 'legacy', 'ue4', 'licensee', 'custom_versions', 'flags'):
            if f['summary'][k] != ref_s[k]:
                fails.append('%s: summary %s %r, proven %s has %r' % (stem, k, f['summary'][k], cls, ref_s[k]))
        if f['summary']['folder'] not in ('None', ''):
            if f['summary']['folder'] != ref_s['folder']:
                notes.append('%s: summary folder %r (proven %r)' % (stem, f['summary']['folder'], ref_s['folder']))
    # Lua object paths
    if lua:
        objs = {f['game_path'] + '.' + e for f in ours.values() for e, _c in f['exports']}
        for path in re.findall(r'"(/Game/Mods/[^"]+)"', open(lua).read()):
            if path not in objs:
                fails.append('Stations.lua names %s, which the pak does not export' % path)
    print('pak %s: %d entries, mount %r, v%d, %d packages, root %s'
          % (os.path.basename(pak), len(names), p['mount'], p['ver'], len(ours), sorted(roots)))
    print('reference: %s' % (', '.join(os.path.basename(r) for r in REFERENCES if os.path.exists(r))
                             or os.path.basename(FACTS)))
    for n in notes:
        print('note:', n)
    for f in fails:
        print('FAIL:', f)
    print('PREFLIGHT', 'FAILED (%d)' % len(fails) if fails else 'PASSED')
    return 1 if fails else 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
