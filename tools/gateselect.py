"""The dev gate's selection: a step runs when something it read changed since its last pass.

    import gateselect
    sel = gateselect.Selection(tree, digests, run)   # digests: the snapshot's {rel: sha256}
    why = sel.reason(kind, name, extra)              # None: unchanged since its last pass
    env = gateselect.recording_env(env, folder)      # the step's environment, recording
    sel.passed(kind, name, folder, started, seconds, extra)    # after exit 0 only
    sel.forget(kind, name)                           # failed or stopped: no pass is kept
    why = sel.ui_reason(case)                        # the same for a UI smoke case
    sel.ui_passed(case, seconds)

**Recorded steps** (offline tests, comparisons, certify) run with tools/gateinputs on
PYTHONPATH and RIFTLORD_GATE_INPUTS set, so every Python process of the step logs what it
reads (gateinputs/sitecustomize.py). Their record holds:
- `tree`: each file of the snapshot it read as data (data, fixtures, scripts run by path,
  modules read by anything but the import system), by the snapshot's sha256, and each
  folder of it listed as data, by its file names;
- `code`: each module of the snapshot it only imported, by code_digest: the module less
  its function bodies, and the bodies of the functions that ran (with what they nest), so
  a change to a function the step never ran does not rerun it;
- `missing`: each module it looked for and did not find, with the snapshot files that
  would have been found (folders listed only to find modules are not kept);
- `programs`: each tree file or folder named on the command line of a program it started
  (node and other non-Python programs, which log nothing themselves), by content;
- `files`: each file outside the tree it read (game paks, kits, caches on scratch), by size
  and mtime_ns, and each folder outside it listed, by its names;
- `shared`: the SHARED files' sha256 (below), and `extra` (the step's argv).
Left out: OUTSIDE_SKIP (Python's own files, the system, temp and run folders, lock
files, the source runs' derived caches), compiled modules (__pycache__), files the step
wrote, files gone or changed while it ran (its own temp files). A process that could not
log what ran (no sys.monitoring) counts every module it imported as read whole.
`signature()` digests the current state of everything a record holds (the certify step
keys its reused drawings by it).

**UI cases** are tagged by what they exercise (UI_TAGS: web folders and server packages,
TOOL_PATHS, plus case extras); a case runs when a file under its tags, a SHARED file or a
UI_WIDE file changed since its last pass.

**SHARED** (riftlord/core, the gate and test tools, the build scripts, the fixtures): any
change there runs every step. A record is written only after a step passed (exit 0) and
is deleted when it fails or is stopped, so a failure is never a pass. Records are kept in
RECORDS, one JSON file per step; deleting the folder runs everything.
"""

import ast
import hashlib
import json
import os
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
NEW = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import testservers                                    # noqa: E402

#: One record per step that passed: <kind>-<name>.json.
RECORDS = os.environ.get('RIFTLORD_GATE_RECORDS') or os.path.join(
    testservers.SCRATCH, 'editor-gate', 'records')
#: The record format; a change runs every step once.
VERSION = 1
#: The folder sitecustomize.py is loaded from.
HOOK = os.path.join(HERE, 'gateinputs')
ENV = 'RIFTLORD_GATE_INPUTS'
#: Files whose change runs every step (paths relative to the tree; a trailing / is a folder).
SHARED = ['riftlord/core/',
          # the gate and the test tools
          'tools/gate.py', 'tools/gateselect.py', 'tools/gateinputs/', 'tools/testservers.py',
          'tools/release_check.py', 'tools/buildcompare.py', 'tools/oldtree.py',
          'tools/pakinspect.py',
          # the build scripts
          'tools/build.py', 'tools/notices.py', 'tools/make_icon.py', 'tools/licenses/',
          'pyproject.toml', 'requirements.txt', 'requirements-build.txt',
          # the test missions and their libraries
          'tests/fixtures/']
#: Files whose change runs every UI case (the suite and its stand-ins).
UI_WIDE = ['tests/ui_smoke/']
#: What a UI case exercises of each tool it opens: its web folder, web/shared, the server
#: packages its routes use, and the app server every tool is started from.
TOOL_PATHS = {
    'riftlord': ['web/mapeditor/', 'web/shared/', 'riftlord/mapeditor/', 'riftlord/data/',
                 'riftlord/formats/ue/', 'riftlord/formats/omd3/'],
    'onslaught': ['web/onslaught/', 'web/shared/', 'riftlord/onslaught/'],
    'shell': ['web/shell/', 'web/shared/', 'riftlord/app/', 'riftlord/missions/'],
}
SERVER_START = ['riftlord/__init__.py', 'riftlord/__main__.py', 'riftlord/app/server.py',
                'riftlord/app/__main__.py', 'riftlord/app/__init__.py']
#: Each UI case (tests/ui_smoke/ui_smoke.js CASES): the tools it opens, then extra paths.
UI_TAGS = {
    'onslaught': (['onslaught'], []),
    'mapeditor': (['riftlord'], []),
    'reference': (['riftlord'], ['riftlord/formats/', 'riftlord/convert/']),
    'preview': (['onslaught'], []),
    'rotation': (['riftlord'], []),
    'flip': (['riftlord'], []),
    'collides': (['riftlord'], []),
    'coverseams': (['riftlord'], []),
    'gridsize': (['riftlord'], []),
    'lattice': (['riftlord'], []),
    'effects': (['riftlord'], ['riftlord/build/world/']),
    'events': (['riftlord'], ['riftlord/missions/missionevents.py',
                              'riftlord/missions/hostevents.py']),
    'saveas': (['riftlord'], ['riftlord/missions/']),
    'controllers': (['riftlord'], []),
    'compounds': (['riftlord'], []),
    'compoundbrowser': (['riftlord', 'onslaught'], []),
    'compoundparams': (['riftlord'], []),
    'decor': (['riftlord'], []),
    'titlebar': (['shell'], []),
    'campaign': (['shell'], []),
    'maptoggles': (['riftlord'], ['riftlord/build/minimap/']),
    'minimapdoors': (['riftlord'], ['riftlord/build/minimap/']),
    'cutaway': (['riftlord'], ['riftlord/build/minimap/']),
    'addlook': (['riftlord'], []),
    'pickups': (['riftlord'], []),
    'crystal': (['riftlord'], []),
    'crystalhealth': (['riftlord', 'onslaught'], []),
    'gamemode': (['onslaught'], []),
    'crystallevel': (['riftlord'], []),
    'supportedmods': (['riftlord', 'shell'], []),
    'modunits': (['onslaught'], ['riftlord/missions/modminions.py']),
    'scramble': (['shell'], []),
    'scrambletiers': (['shell'], []),
    'unlocks': (['shell'], ['riftlord/missions/missionunlocks.py',
                            'tests/ui_smoke/fakeunlocks.py']),
    'header': (['riftlord'], ['riftlord/missions/']),
    'fab': (['riftlord', 'shell'], ['riftlord/fab/']),
    'setup': (['shell'], ['riftlord/types/', 'riftlord/terrainkit/']),
    'appshell': (['riftlord', 'shell'], []),
    'mapeditorui': (['riftlord'], ['riftlord/types/']),
    'spawnerfacing': (['riftlord'], []),
    'multirift': (['riftlord'], []),
    'openwarn': (['riftlord', 'onslaught', 'shell'], []),
    'diffpauses': (['onslaught'], []),
    'onslaughtfloors': (['onslaught'], ['riftlord/build/minimap/mapdata.py',
                                        'riftlord/build/world/levelmusic.py']),
    'campaignindex': (['shell'], ['riftlord/missions/customcampaign.py',
                                  'riftlord/build/leveltitle.py']),
    'samename': (['riftlord', 'shell'], ['riftlord/build/leveltitle.py']),
    'retint': (['riftlord'], ['riftlord/build/world/levelretint.py',
                              'riftlord/build/world/lavafx.py',
                              'riftlord/build/world/lighting.py']),
    'lighting': (['riftlord'], ['riftlord/build/world/', 'riftlord/build/lighting/',
                                'riftlord/missions/bucket.py']),
    'moons': (['riftlord'], ['riftlord/build/world/moons.py', 'riftlord/build/world/lighting.py',
                             'riftlord/build/world/skyfog.py', 'riftlord/missions/bucket.py']),
    'campaignpak': (['shell'], ['riftlord/missions/customcampaign.py']),
    'dialogue': (['riftlord', 'onslaught'], ['riftlord/missions/missionevents.py',
                                             'riftlord/missions/hostevents.py',
                                             'riftlord/build/world/captions.py']),
    'floorsnap': (['riftlord'], ['riftlord/build/navigation/riftfloor.py',
                                 'riftlord/build/navigation/navsurfcarve.py',
                                 'riftlord/build/navigation/navsurffloors.py',
                                 'tests/fixtures/missions/']),
}
#: Snapshot names never recorded (as gate.SKIP_NAMES leaves them out).
SKIP_PARTS = ('__pycache__', 'node_modules', 'dist', 'target', '.pytest_cache')
_HOME = os.path.expanduser('~')
#: Folders outside the tree whose files are never inputs.
OUTSIDE_SKIP = [sys.prefix, sys.base_prefix, '/usr', '/etc', '/proc', '/dev', '/sys', '/run',
                '/tmp', '/var', tempfile.gettempdir(),
                os.path.join(_HOME, '.cache', 'rift-lord', 'editor-buildenv'),
                os.path.join(_HOME, '.cache', 'rift-lord', 'heavy'),
                os.path.join(_HOME, '.cache', 'rift-lord', 'editing'),
                os.path.join(_HOME, '.cache', 'rift-lord', 'testservers'),
                os.path.join(_HOME, '.cache', 'rift-lord', 'gpu.lock'),
                # derived caches of source runs (gamepath.SOURCE_CACHE): rebuilt by any run
                os.path.join(_HOME, '.cache', 'rift-lord', 'editor-source'),
                os.path.join(testservers.SCRATCH, 'editor-gate'),
                os.path.join(testservers.SCRATCH, 'editor-release-check'),
                RECORDS]


def ui_cases(tree):
    """The UI smoke cases, in the suite's order (ui_smoke.js CASES)."""
    with open(os.path.join(tree, 'tests', 'ui_smoke', 'ui_smoke.js')) as fh:
        text = fh.read()
    body = re.search(r'^const CASES = \{(.*?)\}', text, re.S | re.M).group(1)
    return [item.split(':')[0].strip() for item in body.split(',') if item.strip()]


def ui_paths(case):
    """The tree paths a UI case is tagged with, or None for an untagged case."""
    if case not in UI_TAGS:
        return None
    tools, extra = UI_TAGS[case]
    out = list(SERVER_START)
    for tool in tools:
        out += TOOL_PATHS[tool]
    return sorted(set(out + extra))


def recording_env(env, folder):
    """`env` for a step whose Python processes log what they read into `folder`."""
    path = [HOOK] + [p for p in (env.get('PYTHONPATH') or '').split(os.pathsep) if p]
    return dict(env, PYTHONPATH=os.pathsep.join(path), **{ENV: folder})


def _matches(rel, patterns):
    return any(rel.startswith(p) if p.endswith('/') else rel == p for p in patterns)


def _digest(items):
    return hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()[:24]


def _stamp(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return [st.st_size, st.st_mtime_ns]


def _names(folder):
    try:
        return _digest(sorted(n for n in os.listdir(folder) if n not in SKIP_PARTS))
    except OSError:
        return None


def slug(kind, name):
    return re.sub(r'[^A-Za-z0-9]+', '-', '%s-%s' % (kind, name)).strip('-')


#: A module's parts by the file's sha256: (digest of the module less its function bodies,
#: {function qualname: digest of its definitions}), or (None, None) when it does not parse.
_PARTS = {}


def _parts(text, sha):
    """A module's parts (see _PARTS). Its functions are the defs not inside another def
    (at module level, in classes, under if and try); each keeps its decorators, arguments
    and docstring in the module part, and its body (with every def, lambda and class it
    nests) in its own. Comments and layout count for nothing (ast.dump)."""
    if sha in _PARTS:
        return _PARTS[sha]
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        _PARTS[sha] = (None, None)
        return _PARTS[sha]
    funcs = {}

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                q = prefix + child.name
                funcs[q] = hashlib.sha256((funcs.get(q, '') + ast.dump(child)).encode()
                                          ).hexdigest()[:24]
                doc = child.body[0] if child.body and isinstance(child.body[0], ast.Expr) and \
                    isinstance(child.body[0].value, ast.Constant) and \
                    isinstance(child.body[0].value.value, str) else None
                child.body = [doc] if doc else [ast.Pass()]
            elif isinstance(child, ast.ClassDef):
                walk(child, prefix + child.name + '.')
            elif not isinstance(child, ast.Lambda):
                walk(child, prefix)
    walk(tree, '')
    _PARTS[sha] = (hashlib.sha256(ast.dump(tree).encode()).hexdigest()[:24], funcs)
    return _PARTS[sha]


def ran_functions(text, sha, qualnames):
    """The module's functions (see _parts) that the code objects named `qualnames` belong
    to: a nested function, lambda or class belongs to the function around it; class
    bodies, module bodies and lambdas outside any function belong to the module part."""
    funcs = _parts(text, sha)[1] or {}
    return sorted({q.split('.<locals>.')[0] for q in qualnames} & set(funcs))


def code_digest(text, sha, ran):
    """The digest of a module as far as a step that ran the functions `ran` (ran_functions)
    can tell: the module less its function bodies, and those functions' bodies. A module
    that does not parse is its sha256."""
    module, funcs = _parts(text, sha)
    if module is None:
        return 'sha256:' + sha
    return _digest([module] + [[q, funcs.get(q)] for q in sorted(ran)])


class Selection:
    """Selection against one snapshot: `tree` its root, `digests` {rel: sha256} of its files,
    `run` the gate's run folder (left out of the inputs)."""

    def __init__(self, tree, digests, run, records=RECORDS):
        self.tree, self.digests, self.run, self.records = tree, digests, run, records
        self.roots = [os.path.realpath(tree)]
        if os.path.realpath(NEW) not in self.roots:
            self.roots.append(os.path.realpath(NEW))
        self.skip = tuple(os.path.realpath(p).rstrip('/') + '/' for p in
                          OUTSIDE_SKIP + [run] if p)
        self.children = {}
        for rel in digests:
            parts = rel.split('/')
            for i in range(len(parts)):
                self.children.setdefault('/'.join(parts[:i]), set()).add(parts[i])
        self.shared = {r: s for r, s in digests.items() if _matches(r, SHARED)}
        self.ui_shared = {r: s for r, s in digests.items() if _matches(r, SHARED + UI_WIDE)}

    # -- records ----------------------------------------------------------------------
    def _path(self, kind, name):
        return os.path.join(self.records, slug(kind, name) + '.json')

    def load(self, kind, name):
        try:
            with open(self._path(kind, name)) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def _save(self, kind, name, rec):
        os.makedirs(self.records, exist_ok=True)
        path = self._path(kind, name)
        with open(path + '.part', 'w') as fh:
            json.dump(rec, fh, indent=0, sort_keys=True)
        os.replace(path + '.part', path)

    def forget(self, kind, name):
        try:
            os.remove(self._path(kind, name))
        except OSError:
            pass

    # -- the tree ---------------------------------------------------------------------
    def tree_rel(self, path):
        """`path` relative to the tree (the snapshot, or the live tree's snapshot items),
        or None when it is outside."""
        real = os.path.realpath(path)
        for i, root in enumerate(self.roots):
            if real == root or real.startswith(root + '/'):
                rel = os.path.relpath(real, root)
                rel = '' if rel == '.' else rel
                if i and rel.split('/')[0] not in self.children.get('', ()):
                    return None                          # the live tree's dist/, caches
                return rel
        return None

    def listing(self, rel):
        return _digest(sorted(self.children.get(rel, ())))

    def subtree(self, rel):
        if rel in self.digests:
            return self.digests[rel]
        return _digest(sorted((r, s) for r, s in self.digests.items()
                              if r.startswith(rel + '/')))

    def _outside_ok(self, real):
        return not real.startswith(self.skip) and real + '/' not in self.skip

    def _text(self, rel):
        try:
            with open(os.path.join(self.tree, rel), 'rb') as fh:
                return fh.read()
        except OSError:
            return None

    def code(self, rel, ran):
        """The module `rel` of the snapshot as far as a step that ran its functions `ran`
        can tell (code_digest), or None when it is gone."""
        sha = self.digests.get(rel)
        text = None if sha is None else self._text(rel)
        return None if text is None else code_digest(text, sha, ran)

    def found(self, name):
        """The snapshot files the import system could find for the module `name` from any
        folder of the tree: <name>.py, <name>/__init__.py, or a folder <name> (a namespace
        package), each also under any folder."""
        if not hasattr(self, '_modules'):
            self._modules = {}
            for rel in list(self.digests) + [r for r in self.children if r]:
                parts = rel.split('/')
                if rel in self.digests:
                    if not rel.endswith('.py'):
                        continue
                    parts = parts[:-1] if parts[-1] == '__init__.py' else \
                        parts[:-1] + [parts[-1][:-3]]
                for i in range(len(parts)):
                    self._modules.setdefault('.'.join(parts[i:]), set()).add(rel)
        return sorted(self._modules.get(name, ()))

    # -- recorded steps ---------------------------------------------------------------
    def collect(self, folder, started):
        """What the step logging into `folder` read (the record's inputs). `started`: the
        step's start, time.time()."""
        reads, writes, listed, programs = set(), set(), set(), []
        imported, ran, missing = set(), {}, set()
        try:
            logs = sorted(os.listdir(folder))
        except OSError:
            logs = []
        for name in logs:
            with open(os.path.join(folder, name), encoding='utf-8',
                      errors='surrogateescape') as fh:
                lines = [line.rstrip('\n').partition('\t')[::2] for line in fh]
            # a process that logged no code ran: its modules count as read whole
            monitored = ('M', '1') in lines
            for kind, text in lines:
                if kind == 'r' or (kind in ('i', 'm') and not monitored):
                    reads.add(text)
                elif kind in ('i', 'm'):
                    imported.add(text)
                elif kind == 'e':
                    path, _tab, qualname = text.rpartition('\t')
                    ran.setdefault(os.path.realpath(path), set()).add(qualname)
                elif kind == 'n':
                    missing.add(text)
                elif kind == 'w':
                    writes.add(os.path.realpath(text))
                elif kind == 'l' or (kind == 'L' and not monitored):
                    listed.add(text)
                elif kind == 'x':
                    try:
                        programs.append(json.loads(text))
                    except ValueError:
                        pass
        code = {}
        for path in imported:
            real = os.path.realpath(path)
            rel = self.tree_rel(real)
            if rel is None or not rel.endswith('.py') or real in writes:
                reads.add(path)                         # outside the tree, or not source
                continue
            if any(p in SKIP_PARTS for p in rel.split('/')):
                continue
            text = self._text(rel)
            if text is None or rel not in self.digests:
                reads.add(path)
                continue
            funcs = ran_functions(text, self.digests[rel], ran.get(real, ()))
            code[rel] = [code_digest(text, self.digests[rel], funcs), funcs]
        since = int(started * 1e9)
        tree, listed_tree, progs, files, listed_out = {}, {}, {}, {}, {}
        for path in reads:
            real = os.path.realpath(path)
            if real in writes:
                continue
            rel = self.tree_rel(real)
            if rel is not None:
                if rel and not any(p in SKIP_PARTS for p in rel.split('/')) and \
                        not rel.endswith('.pyc'):
                    tree[rel] = self.digests.get(rel)
                    code.pop(rel, None)                 # read whole: by its sha256
            elif self._outside_ok(real) and '/__pycache__/' not in real:
                st = _stamp(real)                       # a .pyc: its source is recorded
                if st is not None and os.path.isfile(real) and st[1] < since:
                    files[real] = st                    # else made or changed while it ran
        for path in listed:
            real = os.path.realpath(path)
            rel = self.tree_rel(real)
            if rel is not None:
                if not any(p in SKIP_PARTS for p in rel.split('/')):
                    listed_tree[rel] = self.listing(rel)
            elif self._outside_ok(real) and (_stamp(real) or [0, since])[1] < since and \
                    os.path.isdir(real):
                listed_out[real] = _names(real)
        for exe, argv, cwd in programs:
            for arg in list(argv[1:]):
                if not isinstance(arg, str) or not arg or arg.startswith('-'):
                    continue
                rel = self.tree_rel(os.path.join(cwd or self.tree, arg))
                if rel and (rel in self.digests or rel in self.children):
                    progs[rel] = self.subtree(rel)
        return {'tree': tree, 'code': code, 'missing': {n: self.found(n) for n in missing},
                'listed': listed_tree, 'programs': progs, 'files': files,
                'listed_outside': listed_out}

    def reason(self, kind, name, extra=None):
        """Why the step must run (a sentence), or None when its last pass still holds."""
        rec = self.load(kind, name)
        if rec is None:
            return 'no record of a pass'
        if rec.get('version') != VERSION:
            return 'recorded by another version of the gate'
        shared = self.ui_shared if kind == 'ui' else self.shared
        for rel in sorted(set(shared) | set(rec.get('shared') or {})):
            if shared.get(rel) != (rec.get('shared') or {}).get(rel):
                return 'shared code changed: %s' % rel
        if (rec.get('extra') or {}) != (extra or {}):
            return 'the step changed: %s' % ', '.join(sorted(
                k for k in set(rec.get('extra') or {}) | set(extra or {})
                if (rec.get('extra') or {}).get(k) != (extra or {}).get(k)))
        for rel, sha in sorted((rec.get('tree') or {}).items()):
            if self.digests.get(rel) != sha:
                return 'changed: %s' % rel
        for rel, (d, funcs) in sorted((rec.get('code') or {}).items()):
            if self.code(rel, funcs) != d:
                return 'changed: %s' % rel
        for name, found in sorted((rec.get('missing') or {}).items()):
            if self.found(name) != found:
                return 'a module it looked for changed: %s (%s)' % (
                    name, ', '.join(sorted(set(self.found(name)) ^ set(found))))
        for rel, d in sorted((rec.get('listed') or {}).items()):
            if self.listing(rel) != d:
                return 'files added or removed in %s/' % rel
        for rel, d in sorted((rec.get('programs') or {}).items()):
            if self.subtree(rel) != d:
                return 'changed: %s (given to a program)' % rel
        for path, st in sorted((rec.get('files') or {}).items()):
            if _stamp(path) != st:
                return 'changed: %s' % path
        for path, d in sorted((rec.get('listed_outside') or {}).items()):
            if _names(path) != d:
                return 'files added or removed in %s/' % path
        return None

    def signature(self, kind, name, leave_out=()):
        """A digest of the current state of everything the step's record holds, less the
        tree files `leave_out`; None without a record. The same digest as at its pass: none
        of it changed since."""
        rec = self.load(kind, name)
        if rec is None or rec.get('version') != VERSION:
            return None
        return _digest([
            [[r, self.digests.get(r)] for r in sorted(rec.get('tree') or {}) if r not in leave_out],
            [[r, self.code(r, f)] for r, (_d, f) in sorted((rec.get('code') or {}).items())
             if r not in leave_out],
            [[n, self.found(n)] for n in sorted(rec.get('missing') or {})],
            [[r, self.listing(r)] for r in sorted(rec.get('listed') or {})],
            [[r, self.subtree(r)] for r in sorted(rec.get('programs') or {})],
            [[p, _stamp(p)] for p in sorted(rec.get('files') or {})],
            [[p, _names(p)] for p in sorted(rec.get('listed_outside') or {})]])

    def passed(self, kind, name, folder, started, seconds, extra=None):
        """Keep the record of a step that passed, from what it logged into `folder`."""
        rec = dict(self.collect(folder, started), version=VERSION, kind=kind, name=name,
                   extra=extra or {}, shared=self.shared, run=self.run,
                   passed=time.strftime('%Y-%m-%d %H:%M:%S'), seconds=round(seconds))
        self._save(kind, name, rec)
        return rec

    # -- UI cases ---------------------------------------------------------------------
    def ui_files(self, case):
        paths = ui_paths(case)
        return {r: s for r, s in self.digests.items() if _matches(r, paths)}

    def ui_reason(self, case):
        if ui_paths(case) is None:
            return 'not tagged in gateselect.UI_TAGS'
        why = self.reason('ui', case)
        if why:
            return why
        rec = self.load('ui', case)
        now, then = self.ui_files(case), rec.get('ui') or {}
        for rel in sorted(set(now) | set(then)):
            if now.get(rel) != then.get(rel):
                return '%s: %s' % ('changed' if rel in now and rel in then else
                                   'added' if rel in now else 'removed', rel)
        return None

    def ui_passed(self, case, seconds):
        self._save('ui', case, {'version': VERSION, 'kind': 'ui', 'name': case,
                                'shared': self.ui_shared, 'ui': self.ui_files(case),
                                'tags': ui_paths(case), 'run': self.run,
                                'passed': time.strftime('%Y-%m-%d %H:%M:%S'),
                                'seconds': round(seconds or 0)})

    def seconds(self, kind, name):
        """Seconds the step took when it last passed, or None."""
        rec = self.load(kind, name)
        return rec.get('seconds') if rec else None


def game_mod_folders():
    """The game's own mod folders (Paks/~mods and Paks/LogicMods, real paths), [] when the
    game is not found: no gate step may read them (the fixture Paks folder stands in)."""
    sys.path.insert(0, NEW)
    from riftlord.core import gamepath
    paks = gamepath.paks()
    return [os.path.realpath(os.path.join(paks, n)) for n in ('~mods', 'LogicMods')] if paks else []


def game_mod_reads(rec):
    """The files and folders of the game's mod folders a record says its step read."""
    roots = tuple(r + '/' for r in game_mod_folders())
    return sorted(p for p in list(rec.get('files') or {}) + list(rec.get('listed_outside') or {})
                  if roots and (p + '/').startswith(roots))

