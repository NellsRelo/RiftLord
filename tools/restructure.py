"""Phase (b): move the flat old-tree layout into the `riftlord` package, and rewrite the code.

    python3 tools/restructure.py plan          # print old -> new paths
    python3 tools/restructure.py apply         # move and rewrite this tree (once)
    python3 tools/restructure.py check FILE    # print FILE (an old-layout path) rewritten

LAYOUT maps every old-layout path to its new one. rewrite(old_path, text) is
deterministic, so tools/oldtree.py replays it on old-tree changes after the move:
- imports of tree modules become absolute `riftlord.*` imports, local names kept;
- `sys.path` setup statements go;
- `os.path.join(HERE|ROOT, '<data file>')` becomes `gamepath.data('<old path>')`;
- the folder given to `gamepath.scratch` / `gamepath.beside_code` becomes
  `gamepath.CODE_ROOT`.
Everything else that depends on a file's location is edited by hand after `apply`.
"""

import argparse
import ast
import io
import os
import re
import shutil
import sys
import tokenize

HERE = os.path.dirname(os.path.abspath(__file__))
NEW = os.path.dirname(HERE)

#: New package for each group of old modules (old path without .py).
GROUPS = {
    'riftlord/core': ['gamepath', 'console', 'buildstage', 'paksource', 'pkgcheck',
                      'gamefiles', 'uetags', 'transform'],
    'riftlord/app': ['workshop/__main__', 'workshop/server'],
    'riftlord/missions': ['bucket', 'customcampaign', 'sourcepictures', 'hudart/modeart',
                          'riftlord/records', 'riftlord/missionevents', 'riftlord/hostevents',
                          'modminions', 'missionscramble', 'scrambletable'],
    'riftlord/mapeditor': ['riftlord/__main__', 'riftlord/server', 'riftlord/leveldata',
                           'riftlord/scratch', 'riftlord/kitactors', 'riftlord/kitdonors',
                           'riftlord/compounds', 'riftlord/foliageadd', 'riftlord/volumes',
                           'riftlord/controllers', 'riftlord/envtraps', 'riftlord/fablibrary',
                           'riftlord/fabemit', 'riftlord/fxsurvey', 'riftlord/geomedits',
                           'riftlord/planactors', 'riftlord/mapinfo', 'riftlord/probe',
                           'riftlord/selftest', 'riftlord/trace', 'omd3serve', 'omd3thumb',
                           'kitgeom', 'tiler'],
    'riftlord/onslaught': ['onslaughtgui/__main__', 'onslaughtgui/server',
                           'onslaughtgui/gamedata', 'onslaughtgui/buildjob',
                           'onslaughtgui/wavespec', 'onslaughtgui/selftest',
                           'onslaughtgui/savetest', 'onslaught', 'flowsim', 'bppatch',
                           'featured', 'makepreview'],
    'riftlord/build': ['riftlord/emit', 'riftlord/apply', 'makemod', 'makelevel', 'levelbuild',
                       'pathemit', 'missionproto', 'modtable', 'protoreg', 'reconcile',
                       'hostpatch', 'authorlevel', 'texpatch', 'volwrite', 'waypoints',
                       'trapgrid', 'materialclone'],
    'riftlord/build/world': ['riftlord/levelfx', 'riftlord/lavafx', 'riftlord/liquidlights',
                             'riftlord/weather', 'riftlord/levelmusic', 'riftlord/levelintro',
                             'riftlord/storyvo', 'riftlord/riftwarning', 'riftlord/skyfog',
                             'riftlord/skymirror', 'riftlord/entitlements', 'loadscreen',
                             'hubportal'],
    'riftlord/build/navigation': ['navpatch', 'navsurf', 'navsurfadd', 'navsurfcarve',
                                  'navsurfextend', 'navsurffloors', 'navsurfgen',
                                  'navsurfinject', 'navvol', 'navvolclear', 'navvolfit',
                                  'navvolgen', 'navvolinject', 'navvoltree', 'navregen',
                                  'navislands', 'navobstruct', 'navinfo', 'navstrict',
                                  'doorsplice', 'hashtest', 'recastgen/recastgen'],
    'riftlord/build/minimap': ['mapdata', 'riftlord/mapdataemit', 'riftlord/minimapredraw',
                               'riftlord/minimapcut', 'riftlord/minimapsolid',
                               'riftlord/minimapstyle', 'riftlord/minimapscore', 'mapview'],
    'riftlord/build/lighting': ['riftlord/lightbake', 'lightbaker', 'lighttrace', 'lightscene',
                                'lightcodec', 'lightmaps', 'bcn'],
    'riftlord/formats/ue': ['UE Assets/kismet', 'UE Assets/lz4block', 'UE Assets/pak',
                            'UE Assets/pakv4', 'UE Assets/parseuasset', 'UE Assets/texture',
                            'UE Assets/uassetio', 'UE Assets/ue', 'UE Assets/uheader', 'usmap'],
    'riftlord/formats/omd3': ['omd3level', 'omd3mesh', 'omd3sm_format', 'omd3smwrite', 'omd3skm',
                              'omd3skm_format', 'omd3skmwrite', 'omd3cloth', 'omd3physx',
                              'omd3actors', 'omd3templates', 'omd3landscape', 'omd3matlook',
                              'omd3sky', 'meshcat', 'meshhull'],
    'riftlord/formats/vision': ['riftlord/trace_vision'],
    'riftlord/formats/omd2': ['omd2mesh', 'omd2model', 'omd2prefab', 'omd2vmesh',
                              'MS-NRBF Reader/nrbf', 'MS-NRBF Reader/clr',
                              'MS-NRBF Reader/trapgrids'],
    'riftlord/formats/omd1': ['riftlord/trace_omd1'],
    'riftlord/formats/omdu': ['riftlord/trace_omdu'],
    'riftlord/formats/omdd': ['riftlord/trace_omdd'],
    'riftlord/convert': ['emitplan', 'emitgeo', 'artgroup', 'meshmatch', 'meshpick', 'prefabmap',
                         'stairfit', 'meshconvert'],
}
#: Old paths that move whole (files or folders), not as modules.
MOVES = {
    'run_workshop.py': 'riftlord/__main__.py',
    'workshop/__init__.py': 'riftlord/app/__init__.py',
    'riftlord/__init__.py': 'riftlord/mapeditor/__init__.py',
    'onslaughtgui/__init__.py': 'riftlord/onslaught/__init__.py',
    'testservers.py': 'tools/testservers.py',
    'pakinspect.py': 'tools/pakinspect.py',
    'workshop/build_app.py': 'tools/build.py',
    'workshop/release_check.py': 'tools/release_check.py',
    'workshop/make_icon.py': 'tools/make_icon.py',
    'recastgen/build.py': 'native/recastgen/build.py',
    'recastgen/recastgen.cpp': 'native/recastgen/recastgen.cpp',
    'thirdparty/physx/pxcook.cpp': 'native/pxcook/pxcook.cpp',
    'test_missionevents.py': 'tests/offline/test_missionevents.py',
    'test_missionproto.py': 'tests/offline/test_missionproto.py',
    'test_modminions.py': 'tests/offline/test_modminions.py',
    'workshop/app': 'shell/window',
    'workshop/launcher': 'shell/launcher',
    'workshop/icon.ico': 'shell/icon.ico',
    'workshop/icon.png': 'shell/icon.png',
    'workshop/web': 'web/shell',
    'riftlord/web': 'web/mapeditor',
    'onslaughtgui/web': 'web/onslaught',
    'webshared': 'web/shared',
    'workshop/README.md': 'docs/APP.md',
    'workshop/SAVE-MODEL.md': 'docs/SAVE-MODEL.md',
    'riftlord/README.md': 'docs/MAP-EDITOR.md',
    'riftlord/PLAN-ACTORS.md': 'docs/PLAN-ACTORS.md',
    'onslaughtgui/README.md': 'docs/ONSLAUGHT-EDITOR.md',
    'MS-NRBF Reader/README.md': 'riftlord/formats/omd2/NRBF.md',
    'DECISIONS.md': 'docs/DECISIONS.md',
    'workshop/research': 'docs/research',
    'workshop/FEEDBACK-2026-10-12.md': 'docs/FEEDBACK-2026-10-12.md',
    'workshop/FEEDBACK-2026-10-16.md': 'docs/FEEDBACK-2026-10-16.md',
}
#: Old paths removed by the move: replaced by `python -m riftlord...` or not ours.
REMOVE = ['run_riftlord.py', 'run_editor.py', 'BPKIT.md']
#: Data files the code reads (tools/build.py DATA), under riftlord/data at the same path.
DATA_DIR = 'riftlord/data'
#: Packages that get an __init__.py.
PACKAGES = sorted({g for g in GROUPS} | {'riftlord', 'riftlord/formats'})


def module_layout():
    """{old path: new path} for every module."""
    out = {}
    for pkg, mods in GROUPS.items():
        for old in mods:
            out[old + '.py'] = '%s/%s.py' % (pkg, old.rsplit('/', 1)[-1])
    return out


def data_files():
    """Old-layout data paths (the frozen app's DATA list)."""
    src = open(os.path.join(NEW, 'workshop', 'build_app.py') if os.path.exists(
        os.path.join(NEW, 'workshop', 'build_app.py')) else os.path.join(NEW, 'tools', 'build.py')).read()
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', '') == 'DATA':
            return ast.literal_eval(node.value)
    raise SystemExit('no DATA list')


_LAYOUT = None


def layout():
    """{old path: new path} for everything that moves."""
    global DATA, _LAYOUT
    if _LAYOUT is None:
        if DATA is None:
            DATA = data_files()
        out = dict(module_layout())
        out.update(MOVES)
        for rel in DATA:
            out[rel] = '%s/%s' % (DATA_DIR, rel)
        _LAYOUT = out
    return _LAYOUT


DATA = None


def _old_module_names():
    """{old import name: new dotted module} for top-level modules and old packages."""
    names = {}
    for old, new in module_layout().items():
        dotted = new[:-3].replace('/', '.')
        stem = old[:-3]
        if '/' in stem:
            folder, leaf = stem.split('/', 1)
            if folder in ('riftlord', 'onslaughtgui', 'workshop'):
                names['%s.%s' % (folder, leaf)] = dotted
            else:
                names[leaf] = dotted              # UE Assets, MS-NRBF Reader, recastgen, hudart
        else:
            names[stem] = dotted
    names.update({'riftlord': 'riftlord.mapeditor', 'onslaughtgui': 'riftlord.onslaught',
                  'workshop': 'riftlord.app'})
    return names


MODULES = _old_module_names()
#: Names a file's old location gave HERE and ROOT, by old folder.
OLD_FOLDERS = ('riftlord', 'onslaughtgui', 'workshop', 'UE Assets', 'MS-NRBF Reader',
               'recastgen', 'hudart')


def _old_package(old_rel):
    """The old package an old-layout module belonged to ('' for top level)."""
    folder = old_rel.split('/', 1)[0] if '/' in old_rel else ''
    return folder if folder in ('riftlord', 'onslaughtgui', 'workshop') else ''


def _render_import(node, old_rel, indent):
    """Replacement source lines for an import node, or None to leave it."""
    if isinstance(node, ast.ImportFrom):
        mod = node.module or ''
        if node.level:
            pkg = _old_package(old_rel)
            if not pkg:
                return None
            mod = pkg + ('.' + mod if mod else '')
        if mod in ('riftlord', 'onslaughtgui', 'workshop'):
            # from riftlord import server as x: each name is a module of the old package
            lines = []
            for a in node.names:
                full = '%s.%s' % (mod, a.name)
                if full not in MODULES:
                    return None
                new = MODULES[full]
                parent, leaf = new.rsplit('.', 1)
                local = a.asname or a.name
                lines.append('%sfrom %s import %s%s' % (indent, parent, leaf,
                                                       '' if local == leaf else ' as ' + local))
            return lines
        if mod in MODULES:
            names = ', '.join(a.name + (' as ' + a.asname if a.asname else '') for a in node.names)
            return ['%sfrom %s import %s' % (indent, MODULES[mod], names)]
        return None
    if not all(a.name in MODULES for a in node.names):
        if any(a.name in MODULES for a in node.names):
            raise SystemExit('%s:%d mixes tree and other imports' % (old_rel, node.lineno))
        return None
    lines = []
    for a in node.names:
        new = MODULES[a.name]
        parent, leaf = new.rsplit('.', 1)
        local = a.asname or a.name.split('.')[-1]
        if '.' in a.name and not a.asname:
            raise SystemExit('%s:%d `import %s` without as' % (old_rel, node.lineno, a.name))
        lines.append('%sfrom %s import %s%s' % (indent, parent, leaf,
                                               '' if local == leaf else ' as ' + local))
    return lines


def _is_sys_path(node):
    """True for a statement that only changes sys.path (insert, append, slice assignment,
    or a for/if around only such statements)."""
    def target(n):
        return isinstance(n, ast.Attribute) and n.attr == 'path' and \
            isinstance(n.value, ast.Name) and n.value.id == 'sys'
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        f = node.value.func
        return isinstance(f, ast.Attribute) and f.attr in ('insert', 'append') and target(f.value)
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        t = node.targets[0]
        return isinstance(t, ast.Subscript) and target(t.value)
    if isinstance(node, (ast.For, ast.If)):
        return bool(node.body) and all(_is_sys_path(n) for n in node.body) and \
            not getattr(node, 'orelse', None)
    return False


#: A sys.path statement putting a code folder on the path (removed); others, such as
#: the light baker's unpacked packages, stay.
CODE_PATH = re.compile(r"\bHERE\b|\bROOT\b|__file__|'UE Assets'|'MS-NRBF Reader'|'\.'|"
                       r"\bhudart\b|\broot\b|'recastgen'")


def _line_offsets(text):
    offs, n = [0], 0
    for line in text.splitlines(True):
        n += len(line)
        offs.append(n)
    return offs


def _comment_after(text, offs, line):
    """The trailing `# ...` of a source line (1-based), if any."""
    src = text[offs[line - 1]:offs[line]]
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError):
        return ''
    return next((t.string for t in toks if t.type == tokenize.COMMENT), '')


def _data_arg(old_rel, call):
    """The old data path an `os.path.join(HERE|ROOT, 'a', 'b.json')` call names, or None."""
    f = call.func
    if not (isinstance(f, ast.Attribute) and f.attr == 'join' and
            isinstance(f.value, ast.Attribute) and f.value.attr == 'path'):
        return None
    if not call.args or not isinstance(call.args[0], ast.Name) or \
            call.args[0].id not in ('HERE', 'ROOT'):
        return None
    parts = []
    for a in call.args[1:]:
        if not (isinstance(a, ast.Constant) and isinstance(a.value, str)):
            return None
        parts.append(a.value)
    folder = os.path.dirname(old_rel)
    base = folder if call.args[0].id == 'HERE' else os.path.dirname(folder)
    rel = '/'.join([p for p in [base] if p] + parts)
    return rel if rel in DATA else None


def rewrite(old_rel, text):
    """The new-layout source of the old-layout file `old_rel`."""
    global DATA
    if DATA is None:
        DATA = data_files()
    tree = ast.parse(text)
    offs = _line_offsets(text)
    edits = []                                   # (start, end, replacement)
    parents = {}
    for p in ast.walk(tree):
        for c in ast.iter_child_nodes(p):
            parents[c] = p
    need_gamepath = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            indent = ' ' * node.col_offset
            lines = _render_import(node, old_rel, indent)
            if lines is None:
                continue
            note = _comment_after(text, offs, node.end_lineno)
            if note:
                lines[-1] = lines[-1] + '  ' + note
            edits.append((offs[node.lineno - 1], offs[node.end_lineno],
                          '\n'.join(lines) + '\n'))
        elif isinstance(node, ast.stmt) and _is_sys_path(node) and \
                CODE_PATH.search(ast.get_source_segment(text, node) or ''):
            parent = parents.get(node)
            body = next((getattr(parent, f) for f in ('body', 'orelse', 'finalbody', 'handlers')
                         if node in (getattr(parent, f, None) or [])), [])
            keep = [n for n in body if not _is_sys_path(n)]
            repl = '' if keep else ' ' * node.col_offset + 'pass\n'
            edits.append((offs[node.lineno - 1], offs[node.end_lineno], repl))
        elif isinstance(node, ast.Call):
            rel = _data_arg(old_rel, node)
            if rel:
                start = offs[node.lineno - 1] + node.col_offset
                end = offs[node.end_lineno - 1] + node.end_col_offset
                edits.append((start, end, 'gamepath.data(%r)' % rel))
                need_gamepath = True
                continue
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr in ('scratch', 'beside_code') and \
                    isinstance(f.value, ast.Name) and f.value.id == 'gamepath':
                args = list(node.args) + [k.value for k in node.keywords if k.arg == 'beside']
                for a in args:
                    if isinstance(a, ast.Name) and a.id in ('HERE', 'ROOT'):
                        start = offs[a.lineno - 1] + a.col_offset
                        end = offs[a.end_lineno - 1] + a.end_col_offset
                        edits.append((start, end, 'gamepath.CODE_ROOT'))
    edits = _drop_nested(edits)
    out = text
    for start, end, repl in sorted(edits, key=lambda e: e[0], reverse=True):
        out = out[:start] + repl + out[end:]
    if need_gamepath and not re.search(r'^(from riftlord\.core import gamepath|import gamepath)\b',
                                       out, re.M):
        out = _add_import(out, 'from riftlord.core import gamepath')
    if old_rel == 'gamepath.py':
        out = out.replace("CODE_ROOT = os.path.dirname(os.path.abspath(__file__))",
                          "CODE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))")
    return out


def _drop_nested(edits):
    """Edits inside another edit's span are dropped (a removed block wins)."""
    edits = sorted(set(edits), key=lambda e: (e[0], -e[1]))
    out = []
    for e in edits:
        if out and e[0] < out[-1][1]:
            if e[1] <= out[-1][1]:
                continue
            raise SystemExit('overlapping rewrite edits at %d' % e[0])
        out.append(e)
    return out


def _add_import(text, line):
    """`line` after the module's last top-level import (or its docstring)."""
    tree = ast.parse(text)
    offs = _line_offsets(text)
    last = None
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            last = node
        elif last is not None:
            break
    if last is None:
        first = tree.body[0] if tree.body else None
        at = offs[first.end_lineno] if first is not None and isinstance(first, ast.Expr) and \
            isinstance(getattr(first, 'value', None), ast.Constant) else 0
    else:
        at = offs[last.end_lineno]
    return text[:at] + line + '\n' + text[at:]


#: A relative import of the shared web components, one folder shallower after the move.
WEBSHARED_IMPORT = re.compile(r"((?:\.\./)+)webshared/")


def dest_of(old_rel):
    """The new path of an old-layout file: its own LAYOUT entry, else under a moved folder."""
    lay = layout()
    if old_rel in lay:
        return lay[old_rel]
    for old, new in sorted(lay.items(), key=lambda kv: -len(kv[0])):
        if old_rel.startswith(old + '/'):
            return new + old_rel[len(old):]
    if old_rel in REMOVE or old_rel.endswith('.py') and old_rel.count('/') <= 1:
        return None                                  # removed, or a module with no place
    return old_rel                                   # stays where it is (tests/ui_smoke)


def transform(old_rel, data):
    """(new path, new bytes) of an old-layout file's contents."""
    global DATA
    if DATA is None:
        DATA = data_files()
    dest = dest_of(old_rel)
    if old_rel.endswith('.py'):
        return dest, rewrite(old_rel, data.decode('utf-8')).encode('utf-8')
    if old_rel.endswith(('.js', '.mjs', '.svelte')):
        text = WEBSHARED_IMPORT.sub(lambda m: m.group(1)[3:] + 'shared/', data.decode('utf-8'))
        text = text.replace('OMD2-Converter/webshared', 'web/shared').replace(
            '(webshared/', '(web/shared/')
        return dest, text.encode('utf-8')
    return dest, data


def cmd_plan(args):
    for old, new in sorted(layout().items()):
        print('%-45s -> %s' % (old, new))
    return 0


def cmd_check(args):
    print(rewrite(args.file, open(os.path.join(NEW, args.file)).read()))
    return 0


SKIP_PARTS = ('node_modules', 'dist', 'target', '__pycache__')


def old_files():
    """Every old-layout file LAYOUT moves (folders walked), as relative paths."""
    out = []
    for old in layout():
        p = os.path.join(NEW, old)
        if os.path.isdir(p):
            for base, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if d not in SKIP_PARTS]
                out += [os.path.relpath(os.path.join(base, f), NEW).replace(os.sep, '/')
                        for f in files]
        elif os.path.exists(p):
            out.append(old)
    return sorted(out)


def cmd_apply(args):
    marker = os.path.join(NEW, 'riftlord', 'core')
    if os.path.isdir(marker):
        raise SystemExit('already applied (%s exists)' % marker)
    missing = [o for o in layout() if not os.path.exists(os.path.join(NEW, o))]
    if missing:
        raise SystemExit('missing in this tree: %s' % ', '.join(missing))
    stage = os.path.join(NEW, '.restructure-stage')
    shutil.rmtree(stage, ignore_errors=True)
    moved = {}
    for old in old_files():
        with open(os.path.join(NEW, old), 'rb') as fh:
            dest, data = transform(old, fh.read())
        out = os.path.join(stage, dest)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, 'wb') as fh:
            fh.write(data)
        shutil.copymode(os.path.join(NEW, old), out)
        moved[old] = dest
    for pkg in PACKAGES:
        init = os.path.join(stage, pkg, '__init__.py')
        if not os.path.exists(init):
            os.makedirs(os.path.dirname(init), exist_ok=True)
            open(init, 'w').close()
    for old in list(layout()) + REMOVE:
        p = os.path.join(NEW, old)
        if os.path.isdir(p):
            shutil.rmtree(p)
        elif os.path.exists(p):
            os.remove(p)
    for base, dirs, files in os.walk(stage):
        for f in files:
            src = os.path.join(base, f)
            dst = os.path.join(NEW, os.path.relpath(src, stage))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.replace(src, dst)
    shutil.rmtree(stage)
    for old in ('workshop', 'onslaughtgui', 'UE Assets', 'MS-NRBF Reader', 'recastgen',
                'hudart', 'thirdparty/physx', 'thirdparty'):
        p = os.path.join(NEW, old)
        if os.path.isdir(p) and not any(os.scandir(p)):
            os.rmdir(p)
    print('moved %d files' % len(moved))
    return 0


def main(argv=None):
    global DATA
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('plan')
    sub.add_parser('apply')
    c = sub.add_parser('check')
    c.add_argument('file')
    args = ap.parse_args(argv)
    DATA = data_files()
    return {'plan': cmd_plan, 'apply': cmd_apply, 'check': cmd_check}[args.cmd](args)


if __name__ == '__main__':
    raise SystemExit(main())
