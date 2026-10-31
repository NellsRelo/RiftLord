"""The probe tool's cache of a level build (emit.write), between rounds that change only the
waves and the minion mod.

    from riftlord.mapeditor import probecache
    cache = probecache.LevelCache(folder)
    with cache.wrapping(expected_out, staged=DIR, switches=[...], note=print) as stats:
        bucket.bake(ident)                    # emit.write answered from the cache on a hit

A hit copies the cached loose build into the bake's output folder, which must not exist
yet, and returns the cached report. The key is a digest of:
- the exact arguments of the call (level, out, plan, title, Guids, details, pictures, ...),
  each argument that names a file replaced by that file's content digest;
- the code: every file of the riftlord package and of the vendored bpkit package, by
  content, and the native helpers it runs (recastgen, the ISPC texture compressor);
- every `.pak` under the game's Paks folder (subfolders: ~mods, LogicMods) and under the
  staged folder, by path, size, mtime, ctime and inode, except a pak holding nothing but
  the folders of minion mods it provides (modminions): what it adds reaches the mission
  only through modminions.carry, which the waves step runs again on the restored build
  (dropping and re-copying every carried mod folder), and the carry gate compares;
- the module switches set for the run and the RIFTLORD_* / OMD3_* environment.
Plans that place Fab library pieces are never cached (their kits live outside the key).
One entry per folder; a new key replaces it. docs/MAP-EDITOR.md, "Probe builds from a
record".
"""

import contextlib
import hashlib
import json
import os
import pickle
import shutil
import time

#: Version of the key and entry layout; a change misses every entry.
VERSION = 1
#: Environment variables that are not inputs (set by the run itself).
ENV_IGNORED = ('RIFTLORD_HEAVY_HELD', 'RIFTLORD_LIGHT_HELD')


def _file_digest(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for block in iter(lambda: fh.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _tree_digest(root, digest):
    """Every file under `root` but __pycache__, by relative path and content."""
    if not root or not os.path.isdir(root):
        digest.update(b'no tree %s\n' % str(root).encode())
        return
    for base, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d != '__pycache__')
        for n in sorted(names):
            if n.endswith(('.pyc', '.pyo')):
                continue
            p = os.path.join(base, n)
            digest.update(('%s\n%s\n' % (os.path.relpath(p, root), _file_digest(p))).encode())


def code_digest():
    """The riftlord package, the vendored bpkit package and the native helpers, by content."""
    from riftlord.core import bpkitlink
    from riftlord.core import gamepath
    from riftlord.build.navigation import recastgen
    from riftlord.formats.ue import ispctex
    digest = hashlib.sha256()
    _tree_digest(gamepath.CODE_ROOT, digest)
    _tree_digest(bpkitlink.VENDORED, digest)
    for path in sorted(recastgen.BINARIES.values()) + sorted(
            p for p in [os.environ.get('RIFTLORD_ISPC_TEXCOMP')] + ispctex.SEARCH if p):
        got = _file_digest(path) if os.path.isfile(path) else 'missing'
        digest.update(('%s\n%s\n' % (path, got)).encode())
    return digest.hexdigest()


def _inner(name):
    return name[name.index('OMD/'):] if 'OMD/' in name else name


def minion_only_paks():
    """Paths of the minion mod paks (modminions.mods(), own) holding nothing outside the
    folders of the mods they provide."""
    from riftlord.core import paksource
    from riftlord.missions import modminions
    by_pak = {}
    for key, mod in modminions.mods().items():
        if mod['own']:
            by_pak.setdefault(os.path.abspath(mod['pak']), set()).add(key)
    out = set()
    for pak, keys in by_pak.items():
        try:
            names = [_inner(n) for n in paksource.names(pak)]
        except (Exception, SystemExit):              # noqa: BLE001
            continue
        prefixes = tuple('OMD/Content/Mods/%s/' % k for k in keys)
        if names and all(n.startswith(prefixes) for n in names):
            out.add(pak)
    return out


def pak_stamps(staged=None):
    """[[path, size, mtime, ctime, inode]] of every `.pak` under the Paks folder (its ~mods
    and LogicMods those of gamepath.mod_paks()) and `staged`, minion-only paks left out."""
    from riftlord.core import gamepath
    skip = minion_only_paks()
    out = []
    shipped, mods = gamepath.paks(), gamepath.mod_paks()
    roots = [(shipped, mods != shipped)] + [(os.path.join(mods, n), False) for n in
                                            ('~mods', 'LogicMods') if mods and mods != shipped]
    for root, own_mods in roots + [(staged, False)]:
        if not root or not os.path.isdir(root):
            continue
        for base, dirs, names in os.walk(root):
            if own_mods and base == root:
                dirs[:] = [d for d in dirs if d not in ('~mods', 'LogicMods')]
            dirs.sort()
            for n in sorted(names):
                if not n.endswith('.pak'):
                    continue
                p = os.path.abspath(os.path.join(base, n))
                if p in skip:
                    continue
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                out.append([p, st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino])
    return out


def _canonical(value):
    """`value` as JSON-ready data: bytes and files named by a string as content digests."""
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, (bytes, bytearray)):
        return {'bytes': hashlib.sha256(bytes(value)).hexdigest()}
    if isinstance(value, str) and len(value) < 4096 and os.path.isfile(value):
        return {'file': value, 'sha256': _file_digest(value)}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return {'repr': repr(value)}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def key_parts(args, kwargs, staged=None, switches=()):
    """{part: digest} of one emit.write call: version, args, each `kwargs.<name>`, code,
    paks, switches, env."""
    env = {k: v for k, v in sorted(os.environ.items())
           if k.startswith(('RIFTLORD_', 'OMD3_')) and k not in ENV_IGNORED}
    parts = {'version': _digest(VERSION), 'args': _digest(_canonical(list(args))),
             'code': code_digest(), 'paks': _digest(pak_stamps(staged)),
             'switches': _digest(list(switches)), 'env': _digest(env)}
    for name, value in kwargs.items():
        parts['kwargs.%s' % name] = _digest(_canonical(value))
    return parts


def key(args, kwargs, staged=None, switches=()):
    """The cache key (hex) of one emit.write call; see the module docstring."""
    return _digest(key_parts(args, kwargs, staged, switches))


def changed_parts(old, new):
    """The part names whose digests differ between two key_parts results."""
    return sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))


class LevelCache:
    """One cached level build in `folder`: key.txt, report.pickle, build/."""

    def __init__(self, folder):
        self.folder = folder

    def _paths(self):
        return (os.path.join(self.folder, 'key.txt'), os.path.join(self.folder, 'report.pickle'),
                os.path.join(self.folder, 'build'))

    def get(self, want, out):
        """The cached report, its build copied to `out`, when the entry's key is `want`;
        else None."""
        key_path, report_path, build = self._paths()
        try:
            with open(key_path, encoding='utf-8') as fh:
                have = fh.read().strip()
        except OSError:
            return None
        if have != want or not os.path.isdir(build) or os.path.exists(out):
            return None
        try:
            with open(report_path, 'rb') as fh:
                report = pickle.load(fh)
        except (OSError, pickle.UnpicklingError, EOFError):
            return None
        shutil.copytree(build, out)
        return report

    def parts(self):
        """The key_parts of the cached entry, {} without one."""
        try:
            with open(os.path.join(self.folder, 'parts.json'), encoding='utf-8') as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def put(self, want, out, report, parts=None):
        """Keep `out` and `report` under key `want` (and its key_parts), replacing the
        entry. False when the report cannot be kept."""
        key_path, report_path, build = self._paths()
        try:
            blob = pickle.dumps(report)
        except Exception:                            # noqa: BLE001
            return False
        if os.path.exists(key_path):
            os.remove(key_path)
        shutil.rmtree(build, ignore_errors=True)
        shutil.rmtree(build + '.part', ignore_errors=True)
        os.makedirs(self.folder, exist_ok=True)
        shutil.copytree(out, build + '.part')
        os.replace(build + '.part', build)
        with open(report_path, 'wb') as fh:
            fh.write(blob)
        with open(os.path.join(self.folder, 'parts.json'), 'w', encoding='utf-8') as fh:
            json.dump(parts or {}, fh, indent=1, sort_keys=True)
        with open(key_path + '.part', 'w', encoding='utf-8') as fh:
            fh.write(want + '\n')
        os.replace(key_path + '.part', key_path)
        return True

    @contextlib.contextmanager
    def wrapping(self, expected_out, staged=None, switches=(), note=print):
        """emit.write answered from this cache, while the block runs, for its call that
        writes `expected_out`. Yields stats {'hit', 'key', 'seconds' (keying), 'saved',
        'changed' (on a miss: the key parts that differ from the cached entry's)}."""
        from riftlord.build import emit
        real = emit.write
        stats = {'hit': None, 'key': None, 'seconds': 0.0, 'saved': False, 'changed': None}

        def cached(level_name, out=None, *a, **kw):
            plan = kw.get('plan') or {}
            from riftlord.mapeditor import fabemit
            if (out is None or os.path.abspath(out) != os.path.abspath(expected_out)
                    or a or fabemit.uses_fab(plan)):
                return real(level_name, out, *a, **kw)
            t = time.time()
            parts = key_parts([level_name], dict(kw, out=out), staged, switches)
            want = _digest(parts)
            stats['key'], stats['seconds'] = want, time.time() - t
            report = self.get(want, out)
            if report is not None:
                stats['hit'] = True
                note('level build: from the probe cache (key %s)' % want[:12])
                return report
            stats['hit'] = False
            old = self.parts()
            stats['changed'] = changed_parts(old, parts) if old else ['no entry']
            note('level build: not cached (changed: %s)' % ', '.join(stats['changed']))
            report = real(level_name, out, **kw)
            if (report or {}).get('written') and os.path.isdir(out):
                stats['saved'] = self.put(want, out, report, parts)
            return report
        emit.write = cached
        try:
            yield stats
        finally:
            emit.write = real
