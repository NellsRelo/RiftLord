"""What a light bake read, recorded by the baker and checked before a cached bake is reused.

    from riftlord.build.lighting import bakeinputs
    bakeinputs.watch(skip=[out_dir])          # in the baker, before the bake
    bakeinputs.write(path)                    # after it: the inputs as JSON
    ok, why = bakeinputs.unchanged(json.load(fh))

The record holds:
- `code`: every module of the riftlord package the process loaded, every file of
  riftlord/build/lighting, and every other file it opened beside the package (data
  tables), by path relative to the package's parent and sha256;
- `files`: every file it opened outside the skipped folders (game paks, Fab libraries,
  mappings) and every native library it loaded (extension modules, ctypes), by absolute
  path, size and mtime_ns.
Skipped from `files`: Python's own prefix, /proc, /dev, /sys, the temp folder, the user's
cache folder (derived data; native libraries loaded from it are still recorded), each
folder in `skip` and in LIGHTBAKE_INPUTS_SKIP (os.pathsep list), and IGNORED_NAMES.
"""

import hashlib
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
#: The riftlord package's parent: `code` paths are relative to it.
CODE_PARENT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
PACKAGE = os.path.join(CODE_PARENT, 'riftlord')
#: Version of the record; a change misses every cached bake.
VERSION = 1
#: Folders skipped by the caller, in addition to `skip` (os.pathsep list).
SKIP_ENV = 'LIGHTBAKE_INPUTS_SKIP'
#: Suffixes of native libraries.
NATIVE = ('.so', '.pyd', '.dll', '.dylib')
#: File names left out of `files`: Steam's library list (it only locates the game, whose
#: paks are recorded).
IGNORED_NAMES = ('libraryfolders.vdf',)

_OPENED = set()
_LOADED = set()
_SKIP = []
_HOOKED = []


def _skip_roots(extra):
    roots = [sys.prefix, sys.base_prefix, '/proc', '/dev', '/sys',
             tempfile.gettempdir(),
             os.environ.get('XDG_CACHE_HOME') or os.path.join(os.path.expanduser('~'), '.cache')]
    roots += [p for p in (os.environ.get(SKIP_ENV) or '').split(os.pathsep) if p]
    roots += list(extra or ())
    return tuple(os.path.realpath(r).rstrip('/') + '/' for r in roots if r)


def _audit(event, args):
    if event == 'open' and args and isinstance(args[0], str):
        _OPENED.add(args[0])
    elif event == 'ctypes.dlopen' and args and isinstance(args[0], str):
        _LOADED.add(args[0])


def watch(skip=()):
    """Start recording what this process opens and loads (once per process)."""
    _SKIP[:] = list(skip)
    if not _HOOKED:
        sys.addaudithook(_audit)
        _HOOKED.append(True)


def _sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for block in iter(lambda: fh.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def _stamp(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return [st.st_size, st.st_mtime_ns]


def _lighting_files():
    """[relative path] of every .py file of riftlord/build/lighting ([] when not on disk)."""
    try:
        names = os.listdir(HERE)
    except OSError:
        return []
    return [os.path.relpath(os.path.join(HERE, n), CODE_PARENT) for n in names
            if n.endswith('.py')]


def code_files():
    """[relative path] of the loaded riftlord modules on disk and every lighting file."""
    out = set(_lighting_files())
    package = os.path.realpath(PACKAGE) + '/'
    for mod in list(sys.modules.values()):
        f = getattr(mod, '__file__', None)
        if f and os.path.isfile(f) and os.path.realpath(f).startswith(package):
            out.add(os.path.relpath(os.path.realpath(f), os.path.realpath(CODE_PARENT)))
    return sorted(out)


def record():
    """{version, code: {path: sha256}, files: {path: [size, mtime_ns]}} of this process."""
    skip = _skip_roots(_SKIP)
    tree = os.path.realpath(CODE_PARENT) + '/'
    code, files = set(code_files()), set()
    for path in list(_OPENED):
        real = os.path.realpath(path)
        if not os.path.isfile(real) or '/__pycache__/' in real:
            continue
        if real.startswith(tree):
            code.add(os.path.relpath(real, tree))
        elif not real.startswith(skip) and os.path.basename(real) not in IGNORED_NAMES:
            files.add(real)
    for mod in list(sys.modules.values()):
        f = getattr(mod, '__file__', None)
        if f and f.endswith(NATIVE):
            files.add(os.path.realpath(f))
    for path in list(_LOADED):
        if os.path.isfile(path):
            files.add(os.path.realpath(path))
    return {'version': VERSION,
            'code': {rel: _sha(os.path.join(CODE_PARENT, rel)) for rel in sorted(code)},
            'files': {p: _stamp(p) for p in sorted(files)}}


def write(path):
    """Write record() as JSON to `path` (atomically)."""
    import json
    with open(path + '.part', 'w') as fh:
        json.dump(record(), fh, indent=0, sort_keys=True)
    os.replace(path + '.part', path)


def unchanged(recorded):
    """(True, '') when every recorded input is as it was, else (False, what changed)."""
    if not isinstance(recorded, dict) or recorded.get('version') != VERSION:
        return False, 'recorded by another version'
    for rel, sha in sorted((recorded.get('code') or {}).items()):
        path = os.path.join(CODE_PARENT, rel)
        if not os.path.isfile(path) or _sha(path) != sha:
            return False, 'code changed: %s' % rel
    new = sorted(set(_lighting_files()) - set(recorded.get('code') or {}))
    if new:
        return False, 'code added: %s' % new[0]
    for path, stamp in sorted((recorded.get('files') or {}).items()):
        if _stamp(path) != stamp:
            return False, 'file changed: %s' % path
    return True, ''


def load(path):
    """The record at `path`, or None."""
    import json
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None
