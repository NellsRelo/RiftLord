"""How far Setup's type data agrees with the type data it replaces, field by field.

    db      the merged typedb (riftlord.types.merge) against bpkit's old typedb.json (built
            from the UHT dump, the engine headers, Mappings.usmap and the Blueprints)
    cxx     the exe's reflection (game.json) against UE4SS's CXXHeaderDump (offsets, sizes)
    sizes   the exe's struct sizes against bpkit.structsize.measure (the exe scanned by name)

    python3 -m riftlord.types.agree db OLD_TYPEDB [NEW_TYPEDB] [-v FIELD]
    python3 -m riftlord.types.agree cxx CXXHeaderDump [GAME_JSON]
    python3 -m riftlord.types.agree sizes [GAME_JSON]

Each report is {field: {'same': n, 'differ': n, 'case': n, 'examples': [...]}}; 'case' counts
the differences that are spelling case alone (FNames compare without case).
"""

import collections
import json
import os
import re
import sys

#: Fields Setup adds that the old database never had.
ADDED = {'from', 'interface'}
#: Old-database fields worked out by rules the exe makes unnecessary.
OLD_ONLY = {'virtual_from', 'names_from', 'aliases'}
EXAMPLES = 12
#: The flags a Blueprint override copies from the function it overrides (bpkit.script:
#: FUNC_FuncInherit, Public, Protected, Private, BlueprintPure).
OVERRIDE_MASK = (0x200 | 0x800 | 0x4000000 | 0x8000000 | 0x4 | 0x8 | 0x40000000 | 0x20000 |
                 0x80000 | 0x40000 | 0x10000000)


def _opcode(fn, db):
    """The opcode bpkit emits to call `fn` (bpkit.calls.opcode)."""
    from riftlord.core import bpkitlink
    bpkitlink.install()
    from bpkit import calls
    return calls.opcode(fn, db)


class Report:
    def __init__(self):
        self.rows = collections.defaultdict(lambda: {'same': 0, 'differ': 0, 'case': 0,
                                                     'examples': []})

    def add(self, field, old, new, where):
        row = self.rows[field]
        if old == new:
            row['same'] += 1
            return
        if json.dumps(old, sort_keys=True).lower() == json.dumps(new, sort_keys=True).lower():
            row['case'] += 1
            return
        row['differ'] += 1
        if len(row['examples']) < EXAMPLES:
            row['examples'].append([where, old, new])

    def data(self):
        return {k: dict(v) for k, v in sorted(self.rows.items())}

    def text(self, verbose=None):
        out = []
        for field, row in sorted(self.rows.items()):
            out.append('%-48s same %6d  case %5d  differ %5d' % (
                field, row['same'], row['case'], row['differ']))
            if verbose and verbose in field:
                out += ['    %s' % json.dumps(e)[:300] for e in row['examples']]
        return '\n'.join(out)


def _scope(old, table, entry):
    if entry.get('source') == 'blueprint':
        return 'blueprint'
    if table == 'functions':
        module = (old['classes'].get(entry.get('owner')) or {}).get('module')
    else:
        module = entry.get('module')
    return 'engine' if module in ('Engine', 'CoreUObject') else 'game'


def compare_db(old, new):
    """Report of every old entry's every field against the new database."""
    r = Report()
    for table in ('classes', 'structs', 'enums', 'functions', 'delegates', 'usmap_only'):
        for key, e in old[table].items():
            scope = _scope(old, table, e)
            tag = '%s %s' % (scope, table)
            n = new[table].get(key)
            if n is None:
                low = {k.lower(): k for k in new[table]} if table == 'functions' else {}
                n = new[table].get(low.get(key.lower(), ''))
                r.add(tag + ' present', True, 'case' if n is not None else False, key)
                if n is None:
                    continue
            else:
                r.add(tag + ' present', True, True, key)
            for field in sorted((set(e) | set(n)) - ADDED - OLD_ONLY):
                a, b = e.get(field), n.get(field)
                if field == 'params':
                    r.add(tag + ' param count', len(a), len(b), key)
                    if len(a) == len(b):
                        for pf in ('name', 'type', 'out', 'ref', 'const', 'return', 'default',
                                   'wildcard'):
                            r.add(tag + ' param ' + pf, [p.get(pf) for p in a],
                                  [p.get(pf) for p in b], key)
                elif field == 'function_flags':
                    r.add(tag + ' function_flags', a, b, key)
                    r.add(tag + ' call opcode', _opcode(e, old), _opcode(n, new), key)
                    r.add(tag + ' override flags', a & OVERRIDE_MASK, b & OVERRIDE_MASK, key)
                    for bit in range(32):
                        if (a | b) & 1 << bit:
                            r.add(tag + ' flag %#x' % (1 << bit), bool(a & 1 << bit),
                                  bool(b & 1 << bit), key)
                elif field in ('properties', 'cpp') and isinstance(a, dict):
                    b = b or {}
                    r.add('%s %s names' % (tag, field), sorted(a, key=str.lower),
                          sorted(b, key=str.lower), key)
                    low = {k.lower(): k for k in b}
                    for k, v in a.items():
                        got = b.get(k, b.get(low.get(k.lower(), ''), None))
                        r.add('%s %s values' % (tag, field), v, got, [key, k])
                elif field == 'usmap_properties':
                    r.add('%s %s' % (tag, field), sorted(a or [], key=str.lower),
                          sorted(b or [], key=str.lower), key)
                else:
                    r.add('%s %s' % (tag, field), a, b, key)
    for table in ('classes', 'structs', 'enums', 'functions', 'delegates', 'usmap_only'):
        extra = [k for k in new[table] if k not in old[table]]
        r.rows['%s only in the new database' % table]['differ'] = len(extra)
        r.rows['%s only in the new database' % table]['examples'] = extra[:EXAMPLES]
    return r


# -- CXXHeaderDump ------------------------------------------------------------------------

CXX_HEAD = re.compile(r'^(class|struct) ([AUFI])(\w+)(?: : public [AUFI](\w+))?\s*$')
CXX_MEMBER = re.compile(r'^\s+(.+?)\s+(\w+)(?:\s*:\s*1)?;\s*// 0x([0-9A-F]+) \(size: 0x([0-9A-F]+)\)')
CXX_END = re.compile(r'^\}; // Size: 0x([0-9A-F]+)')


def read_cxx(folder):
    """{name: {'kind', 'super', 'size', 'members': {name: (offset, size)}}} of every class and
    struct in a CXXHeaderDump folder's module headers (Blueprint headers left out)."""
    out = {}
    for f in sorted(os.listdir(folder)):
        if not f.endswith('.hpp') or f.endswith('_enums.hpp'):
            continue
        cur = None
        with open(os.path.join(folder, f), encoding='utf-8', errors='replace') as fh:
            for line in fh:
                m = CXX_HEAD.match(line)
                if m:
                    cur = {'kind': m.group(1), 'super': m.group(4), 'members': {},
                           'file': f}
                    out.setdefault(m.group(3), cur)
                    continue
                if cur is None:
                    continue
                m = CXX_MEMBER.match(line)
                if m:
                    cur['members'][m.group(2)] = (int(m.group(3), 16), int(m.group(4), 16))
                    continue
                m = CXX_END.match(line)
                if m:
                    cur['size'] = int(m.group(1), 16)
                    cur = None
    return out


def compare_cxx(game, cxx):
    """Report of the exe's classes and structs against the CXXHeaderDump."""
    r = Report()
    for table in ('classes', 'structs'):
        for name, e in game[table].items():
            c = cxx.get(name)
            r.add('%s in the CXX dump' % table, True, c is not None, name)
            if c is None:
                continue
            r.add('%s super' % table, c['super'], e['super'], name)
            names = [p['name'] for p in e['properties']]
            r.add('%s property names' % table, sorted(c['members'], key=str.lower),
                  sorted(names, key=str.lower), name)
            for p in e['properties']:
                m = c['members'].get(p['name'])
                if m is None or p['kind'] == 'Bool':
                    continue
                r.add('%s property offsets' % table, m[0], p['offset'], [name, p['name']])
            if table == 'structs' and 'size' in c:
                r.add('struct sizes', c['size'], e['size'], name)
    return r


def compare_sizes(game, exe_path):
    """Report of the exe's FStructParams sizes against bpkit.structsize.measure."""
    from riftlord.core import bpkitlink
    bpkitlink.install()
    from bpkit import structsize
    data = open(exe_path, 'rb').read()
    base, secs = structsize.sections(data)
    r = Report()
    for name, s in sorted(game['structs'].items()):
        found = structsize.measure(data, base, secs, name)
        r.add('struct size (structsize.measure)', found[0][0] if len(found) == 1 else None,
              s['size'], name)
    return r


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('what', choices=['db', 'cxx', 'sizes'])
    ap.add_argument('paths', nargs='*')
    ap.add_argument('-v', dest='verbose', help='show examples of fields containing this')
    ap.add_argument('--json', help='write the report here')
    args = ap.parse_args(argv)
    from riftlord.types import setup
    if args.what == 'db':
        old = json.load(open(args.paths[0]))
        new = json.load(open(args.paths[1] if len(args.paths) > 1 else
                             os.path.join(setup.folder(), 'typedb.json')))
        rep = compare_db(old, new)
    elif args.what == 'cxx':
        game = json.load(open(args.paths[1] if len(args.paths) > 1 else
                              os.path.join(setup.folder(), 'game.json')))
        rep = compare_cxx(game, read_cxx(args.paths[0]))
    else:
        game = json.load(open(args.paths[0] if args.paths else
                              os.path.join(setup.folder(), 'game.json')))
        rep = compare_sizes(game, setup.exe_path())
    print(rep.text(args.verbose))
    if args.json:
        with open(args.json, 'w') as fh:
            json.dump(rep.data(), fh, indent=1)


if __name__ == '__main__':
    main()
