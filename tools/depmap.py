"""Static dependency map of Rift Lord's Python side.

    python3 tools/depmap.py [--root TREE] [--json OUT.json] [--md OUT.md]

Parses every Python file under TREE (default: OMD2-Converter beside this folder),
follows imports (module level and inside functions, relative and absolute,
`importlib.import_module('...')` literals) from the app's entry points, and
classifies each module as needed (reached from the frozen app), dev (reached only
from build, test and probe tools) or not needed. Also lists third-party imports,
scripts run by path, and data files named by string literals.
"""

import argparse
import ast
import json
import os
import sys
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.join(os.path.dirname(os.path.dirname(HERE)), 'OMD2-Converter')

#: Folders the code puts on sys.path, in lookup order ('' is the tree root).
SEARCH = ['', 'UE Assets', 'MS-NRBF Reader', 'recastgen', 'hudart', 'VZone Reader',
          'navbuild', 'thirdparty/survey-tools', 'thirdparty/fabconvert']
#: Folders never parsed: build output, caches, dependencies, other projects' data.
SKIP_DIRS = {'node_modules', '__pycache__', '.buildenv', 'build', 'build-windows', 'dist',
             '.git', 'cache', 'bakes', 'previews', 'lightscene', 'lightmaps', 'patched',
             'probes', 'riftlord-plans', 'skinart', 'gamefiles'}
#: Data file extensions a string literal may name.
DATA_EXT = ('.json', '.png', '.npz', '.cpp', '.h', '.txt', '.yaml', '.yml', '.ini', '.csv',
            '.ico', '.svg', '.bin', '.pak', '.usmap')

#: The same for the package layout: the repo root, then the tools' and tests' folders.
SEARCH_PACKAGED = ['', 'tools', 'tests/offline']
#: The frozen app: its entry script, then what build_app.py names as hidden imports.
APP_ENTRIES = ['run_workshop.py']
APP_ENTRIES_PACKAGED = ['riftlord/__main__.py']
#: Build, test and probe tools in the package layout.
DEV_ENTRIES_PACKAGED = ['tools/build.py', 'tools/release_check.py', 'tools/testservers.py',
                        'tools/pakinspect.py', 'tools/make_icon.py', 'tools/gate.py',
                        'tools/buildcompare.py', 'native/recastgen/build.py',
                        'riftlord/mapeditor/selftest.py', 'riftlord/onslaught/selftest.py',
                        'riftlord/onslaught/savetest.py', 'riftlord/missions/records.py',
                        'riftlord/build/minimap/minimapscore.py',
                        'riftlord/mapeditor/__main__.py', 'riftlord/onslaught/__main__.py',
                        'tests/offline/test_missionproto.py',
                        'tests/offline/test_missionevents.py',
                        'tests/offline/test_modminions.py', 'tests/offline/test_imports.py',
                        'tests/offline/test_scramble_endless.py',
                        'tests/offline/test_trace_copies.py']
#: Build, test and probe tools.
DEV_ENTRIES = ['workshop/build_app.py', 'workshop/release_check.py', 'testservers.py',
               'pakinspect.py', 'riftlord/probe.py', 'riftlord/selftest.py',
               'riftlord/records.py', 'riftlord/minimapscore.py', 'onslaughtgui/selftest.py',
               'onslaughtgui/savetest.py', 'workshop/make_icon.py', 'recastgen/build.py',
               'run_riftlord.py', 'run_editor.py', 'riftlord/__main__.py',
               'onslaughtgui/__main__.py', 'onslaughtgui/build_app.py',
               'onslaughtgui/package.py', 'test_missionevents.py', 'test_missionproto.py',
               'test_modminions.py']


def walk_py(root):
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and (
            d not in SKIP_DIRS or os.path.isfile(os.path.join(base, d, '__init__.py'))))
        for f in sorted(files):
            if f.endswith('.py'):
                yield os.path.relpath(os.path.join(base, f), root)


def packaged(root):
    """True for a tree in the riftlord package layout (after tools/restructure.py)."""
    return os.path.isdir(os.path.join(root, 'riftlord', 'core'))


def module_index(root):
    """{module name: relative file} for every importable module on the search path."""
    index = {}
    for sp in (SEARCH_PACKAGED if packaged(root) else SEARCH):
        base = os.path.join(root, sp)
        if not os.path.isdir(base):
            continue
        for rel in walk_py(base):
            parts = rel[:-3].split(os.sep)
            if parts[-1] == '__init__':
                parts = parts[:-1]
            # a nested file is a module only when every folder above it is a package
            ok = all(os.path.exists(os.path.join(base, *parts[:i], '__init__.py'))
                     for i in range(1, len(parts)))
            if not parts or not ok:
                continue
            name = '.'.join(parts)
            index.setdefault(name, os.path.join(sp, rel) if sp else rel)
    return index


def file_module(rel, index):
    for name, f in index.items():
        if f == rel:
            return name
    return None


class Imports(ast.NodeVisitor):
    """Each import in a file: (target, line, lazy, optional)."""

    def __init__(self):
        self.found = []
        self.strings = []
        self.depth = 0
        self.guard = 0

    def visit_FunctionDef(self, node):
        self.depth += 1
        self.generic_visit(node)
        self.depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Try(self, node):
        catches = any(h.type is None or any(
            isinstance(n, ast.Name) and n.id in ('ImportError', 'ModuleNotFoundError', 'Exception')
            for n in ast.walk(h.type)) for h in node.handlers)
        self.guard += catches
        for n in node.body:
            self.visit(n)
        self.guard -= catches
        for n in node.handlers + node.orelse + node.finalbody:
            self.visit(n)

    visit_TryStar = visit_Try

    def add(self, name, line, names=()):
        self.found.append((name, line, self.depth > 0, self.guard > 0, list(names)))

    def visit_Import(self, node):
        for a in node.names:
            self.add(a.name, node.lineno)

    def visit_ImportFrom(self, node):
        self.add(('.' * node.level) + (node.module or ''), node.lineno,
                 [a.name for a in node.names])

    def visit_Call(self, node):
        f = node.func
        fname = f.attr if isinstance(f, ast.Attribute) else getattr(f, 'id', '')
        if fname == 'import_module' and node.args and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str):
            self.add(node.args[0].value, node.lineno)
        self.generic_visit(node)

    def visit_Constant(self, node):
        if isinstance(node.value, str) and 0 < len(node.value) < 200:
            self.strings.append((node.value, node.lineno))


def resolve(target, names, mod, rel, index):
    """Tree modules an import statement reaches."""
    if target.startswith('.'):
        level = len(target) - len(target.lstrip('.'))
        pkg = mod.split('.') if rel.endswith('__init__.py') else mod.split('.')[:-1]
        pkg = pkg[:len(pkg) - (level - 1)]
        target = '.'.join(pkg + ([target.lstrip('.')] if target.lstrip('.') else []))
    out = []
    parts = target.split('.')
    for i in range(len(parts), 0, -1):
        cand = '.'.join(parts[:i])
        if cand in index:
            out.append(cand)
            break
    for n in names:
        sub = target + '.' + n if target else n
        if sub in index:
            out.append(sub)
    return out, target


def analyse(root):
    index = module_index(root)
    by_file = {f: m for m, f in index.items()}
    stdlib = set(sys.stdlib_module_names)
    files = {}
    data_names = {}
    for base, dirs, fs in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ('node_modules', '__pycache__', '.buildenv',
                                                'build', 'build-windows', 'dist', '.git')]
        for f in fs:
            if f.endswith(DATA_EXT):
                data_names.setdefault(f, []).append(
                    os.path.relpath(os.path.join(base, f), root))
    top_dirs = {d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))}
    for rel in sorted(set(index.values())):
        try:
            tree = ast.parse(open(os.path.join(root, rel), 'rb').read(), rel)
        except SyntaxError as exc:
            files[rel] = {'error': str(exc)}
            continue
        v = Imports()
        v.visit(tree)
        mod = by_file[rel]
        edges, external, scripts, data, dirs_named = [], {}, set(), set(), set()
        for target, line, lazy, optional, names in v.found:
            mods, absolute = resolve(target, names, mod, rel, index)
            if mods:
                for m in mods:
                    if m != mod:
                        edges.append({'to': m, 'line': line, 'lazy': lazy, 'optional': optional})
            elif absolute:
                top = absolute.split('.')[0]
                if top and top not in stdlib:
                    external.setdefault(top, []).append(
                        {'line': line, 'lazy': lazy, 'optional': optional})
        here = os.path.dirname(rel)
        for s, line in v.strings:
            leaf = s.replace('\\', '/').split('/')[-1]
            if leaf.endswith('.py'):
                cand = [p for p in (os.path.normpath(os.path.join(here, s)), os.path.normpath(s))
                        if p in by_file]
                cand += [f for f in by_file if f.endswith('/' + leaf) or f == leaf] if not cand else []
                if cand and cand[0] != rel:
                    scripts.add(cand[0])
            elif leaf.endswith(DATA_EXT) and leaf in data_names:
                near = [h for h in data_names[leaf]
                        if os.path.dirname(h) in (here, '', 'cache', 'gamefiles', 'previews')]
                data.update(near[:3])
            elif s in top_dirs and s not in ('workshop', 'riftlord', 'onslaughtgui', 'bpkit'):
                dirs_named.add(s)
        files[rel] = {'module': mod, 'edges': edges, 'external': external,
                      'scripts': sorted(scripts), 'data': sorted(data),
                      'dirs': sorted(dirs_named), 'lines': sum(1 for _ in open(
                          os.path.join(root, rel), 'rb'))}
    return index, files


def hidden_imports(root):
    """The build script's HIDDEN, DATA and BAKER_SOURCES, read without running it."""
    path = os.path.join(root, 'tools', 'build.py') if packaged(root) else \
        os.path.join(root, 'workshop', 'build_app.py')
    src = open(path).read()
    tree = ast.parse(src)
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and \
                node.targets[0].id in ('HIDDEN', 'DATA', 'BAKER_SOURCES'):
            out[node.targets[0].id] = ast.literal_eval(node.value)
    return out


def reach(seeds, index, files):
    """{module: chain of modules from a seed} for everything reachable."""
    parent = {}
    q = deque()
    for s in seeds:
        if s in index and s not in parent:
            parent[s] = None
            q.append(s)
    while q:
        m = q.popleft()
        info = files.get(index[m], {})
        nxt = [e['to'] for e in info.get('edges', [])]
        for script in info.get('scripts', []):
            for name, f in index.items():
                if f == script:
                    nxt.append(name)
        for n in nxt:
            if n not in parent:
                parent[n] = m
                q.append(n)
    chains = {}
    for m in parent:
        chain, cur = [], m
        while cur is not None:
            chain.append(cur)
            cur = parent[cur]
        chains[m] = list(reversed(chain))
    return chains


def build_map(root):
    index, files = analyse(root)
    hb = hidden_imports(root)
    new = packaged(root)
    entry_seeds = [file_module(e, index) for e in (APP_ENTRIES_PACKAGED if new else APP_ENTRIES)]
    hidden_seeds = [h for h in hb.get('HIDDEN', []) if h in index]
    if not new:
        hidden_seeds += [s[:-3] for s in hb.get('BAKER_SOURCES', [])]
    dev_seeds = [file_module(e, index) for e in (DEV_ENTRIES_PACKAGED if new else DEV_ENTRIES)
                 if e in index.values()]
    app = reach([s for s in entry_seeds if s], index, files)
    for name, chain in reach(hidden_seeds, index, files).items():
        app.setdefault(name, ['HIDDEN'] + chain)
    dev = reach([s for s in dev_seeds if s], index, files)
    modules = {}
    for name, rel in sorted(index.items()):
        if name in app:
            cls, chain = 'needed', app[name]
        elif name in dev:
            cls, chain = 'dev', dev[name]
        else:
            cls, chain = 'not needed', []
        info = files.get(rel, {})

        def lazy(a, b):
            edges = [e for e in files.get(index[a], {}).get('edges', []) if e['to'] == b]
            return bool(edges) and all(e['lazy'] for e in edges)
        steps = [c for c in chain if c in index]
        modules[name] = {'file': rel, 'class': cls, 'chain': chain,
                         'lines': info.get('lines', 0),
                         'lazy_only': any(lazy(a, b) for a, b in zip(steps, steps[1:]))}
    external = {}
    for name, m in modules.items():
        if m['class'] == 'not needed':
            continue
        for pkg, uses in files.get(m['file'], {}).get('external', {}).items():
            e = external.setdefault(pkg, {'needed': [], 'dev': [], 'optional': True})
            e[m['class']].append(name)
            e['optional'] = e['optional'] and all(u['optional'] for u in uses)
    data = {}
    for name, m in modules.items():
        if m['class'] == 'not needed':
            continue
        for d in files.get(m['file'], {}).get('data', []):
            data.setdefault(d, {'needed': [], 'dev': []})[m['class']].append(name)
    dirs = {}
    for name, m in modules.items():
        if m['class'] == 'not needed':
            continue
        for d in files.get(m['file'], {}).get('dirs', []):
            dirs.setdefault(d, []).append(name)
    return {'root': root, 'modules': modules, 'external': external, 'data': data,
            'dirs_named': dirs, 'hidden': hb,
            'hidden_missing': [h for h in hb.get('HIDDEN', []) if h not in index]}


#: Why a module the app never reaches exists, by file prefix (first match wins).
REASONS = [
    ('assemblygui/', 'OMD2 assembly tool (converter GUI)'),
    ('weaver/', 'Weaver (mission placement tool, its own app)'),
    ('omd3to2/', 'OMD3-to-OMD2 export'),
    ('thirdparty/fabconvert/', 'Fab asset-pack conversion (fabconvert.py)'),
    ('thirdparty/survey-tools/', 'Fab asset-pack survey'),
    ('navbuild/', 'OMD2 navgraph build research'),
    ('VZone Reader/', 'OMD2 zone reader'),
    ('MS-NRBF Reader/', 'NRBF reader CLIs (dump, validate)'),
    ('hudart/', 'OrcsMustUI HUD art mods'),
    ('bpkit/check_', 'bpkit offline checks (Blueprint mod builds)'),
    ('weapon', 'weapon mod pipeline'),
    ('modifiermod', 'Scramble modifier mod pipeline'),
    ('omd3cloth_', 'cloth writer acceptance tests'),
    ('omd3skm_acceptance', 'skeletal mesh writer acceptance test'),
    ('ship', 'OMD2 conversion (ship* emitters/viewers)'),
    ('slot', 'OMD2 conversion (slots)'),
    ('omd2', 'OMD2 readers and conversion'),
    ('', 'OMD2 conversion, research or probe script'),
]


def reason(rel):
    return next(r for p, r in REASONS if rel.replace(os.sep, '/').startswith(p))


#: The line after which --doc rewrites a document.
DOC_MARK = '<!-- generated by tools/depmap.py: everything below is rewritten -->'


def markdown(m):
    lines = []
    counts = {}
    for v in m['modules'].values():
        counts[v['class']] = counts.get(v['class'], 0) + 1
    lines.append('Modules: %s.' % ', '.join('%d %s' % (n, c) for c, n in sorted(counts.items())))
    for cls in ('needed', 'dev'):
        lines += ['', '### Python modules: %s' % cls, '',
                  '| module | file | lines | import chain |', '|---|---|---|---|']
        for name, v in sorted(m['modules'].items(), key=lambda kv: kv[1]['file']):
            if v['class'] == cls:
                lines.append('| `%s` | %s | %d | %s%s |' % (
                    name, v['file'], v['lines'], ' > '.join(v['chain']),
                    ' (lazy)' if v['lazy_only'] else ''))
    lines += ['', '### Python modules: not needed', '',
              '| file | lines | what it is for |', '|---|---|---|']
    for name, v in sorted(m['modules'].items(), key=lambda kv: kv[1]['file']):
        if v['class'] == 'not needed':
            lines.append('| %s | %d | %s |' % (v['file'], v['lines'], reason(v['file'])))
    lines += ['', '### Third-party Python packages', '',
              '| package | optional | needed by | dev-only users |', '|---|---|---|---|']
    for pkg, e in sorted(m['external'].items()):
        lines.append('| `%s` | %s | %s | %s |' % (
            pkg, 'yes' if e['optional'] else 'no', ', '.join(sorted(e['needed'])[:6]) +
            (' +%d' % (len(e['needed']) - 6) if len(e['needed']) > 6 else ''),
            ', '.join(sorted(e['dev'])[:4])))
    lines += ['', '### Data files named in code', '',
              '| file | read by (needed) | read by (dev) |', '|---|---|---|']
    for d, e in sorted(m['data'].items()):
        lines.append('| %s | %s | %s |' % (d, ', '.join(sorted(e['needed'])[:5]),
                                           ', '.join(sorted(e['dev'])[:3])))
    return '\n'.join(lines) + '\n'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--root', default=DEFAULT_ROOT)
    ap.add_argument('--json')
    ap.add_argument('--md')
    ap.add_argument('--doc', help='rewrite this document below its DOC_MARK line')
    args = ap.parse_args(argv)
    m = build_map(os.path.abspath(args.root))
    if args.json:
        with open(args.json, 'w') as fh:
            json.dump(m, fh, indent=1)
    if args.md:
        with open(args.md, 'w') as fh:
            fh.write(markdown(m))
    if args.doc:
        with open(args.doc) as fh:
            head = fh.read().split(DOC_MARK)[0]
        with open(args.doc, 'w') as fh:
            fh.write(head + DOC_MARK + '\n\n' + markdown(m))
    counts = {}
    for v in m['modules'].values():
        counts[v['class']] = counts.get(v['class'], 0) + 1
    print(counts, 'external:', sorted(m['external']), 'hidden not found:', m['hidden_missing'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
