"""Trims the Fab converter's working cache (library.cache_root()): the working folder of a
pack whose last convert began or ended more than a day ago is emptied, with the extracted zip
it was converted from. Nothing in it is an output: libraries live in the Fab library and the
terrain kits' folder, and only the converter reads its cache (a re-convert rebuilds it).

    python3 -m riftlord.fab.cachetrim [--dry-run] [--hours H] [--json]

    from riftlord.fab import cachetrim
    report = cachetrim.trim(busy={'Docks'}, log=print)

What a pack's working folder holds (library.py, vaulttex.py, micwrite.py):
    src/                decoded vault textures (vaulttex), read by bakes
    bake/, img/         baked variants and the images they name (one unit: a bake whose image
                        is gone breaks the texture step, so both go together)
    tex/, tex-<enc>/    encoded textures, per BC encoder
    mesh/, grid/        written meshes and their grid searches
    stage/              the last convert's loose packages; emptied when a convert starts
    stage_seconds.json  what each stage took (the next convert's ETA): kept
Shared folders (locks, vaultinfo, measure) are small and kept; imports/<zip>-<digest> is a
loose pack's extracted zip and goes with its pack.

When a pack was last used: the newest modification time of its folder and the entries in it.
A convert empties stage/ at its start and writes stage_seconds.json at its end, so that time
is the pack's last convert. Access times are not used: /home is mounted noatime, and cache
hits never write.

A pack is left alone while it is busy: named by the caller (running or queued in the app),
or its convert lock (vault.lock_path) or terrain kit lock is held. While a pack is emptied
its locks are held, so no convert of it can start; a held lock that names no known pack stops
the whole trim. Emptied entries are moved into .trash under those locks and deleted after.
"""

import argparse
import json
import os
import re
import shutil
import sys
import time

#: A pack's working folder not used for this many seconds is emptied.
MAX_AGE_S = 24 * 3600
#: Folders of the cache root that are not a pack's working folder.
SHARED = ('locks', 'vaultinfo', 'measure', 'imports')
#: Files of a pack's working folder that are kept (tiny records, not cache).
KEEP = ('stage_seconds.json',)
#: Emptied entries wait here (same disk: a rename) until they are deleted.
TRASH = '.trash'
#: One trim at a time per cache root.
TRIM_LOCK = '.trim.lock'
#: A terrain kit's pack id is this plus its map (riftlord.terrainkit.build.PACK_PREFIX).
TERRAIN_PREFIX = 'Terrain_'


def cache_root():
    """library.cache_root(), without loading the converter."""
    if os.name == 'nt':
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
        return os.path.join(base, 'rift-lord', 'fabconvert')
    base = os.environ.get('XDG_CACHE_HOME') or os.path.join(os.path.expanduser('~'), '.cache')
    return os.path.join(base, 'rift-lord', 'fabconvert')


def terrain_locks():
    """The terrain kit builder's lock folder (riftlord.terrainkit.sources.cache_dir()/locks)."""
    base = os.environ.get('LOCALAPPDATA') if os.name == 'nt' else (
        os.environ.get('XDG_CACHE_HOME') or os.path.join(os.path.expanduser('~'), '.cache'))
    return os.path.join(base or os.path.expanduser('~'), 'rift-lord', 'terrainkit', 'locks')


def sanitize(s):
    """library.sanitize."""
    return re.sub(r'[^A-Za-z0-9_]', '_', s)


def lock_path(item, root=None):
    """vault.lock_path of a vault item or loose pack name, without creating anything."""
    return os.path.join(root or cache_root(), 'locks', '%s.lock' % sanitize(item))


def loose_pack_id(name):
    """packs.identity's pack id of a loose pack named `name` (its zip or folder name)."""
    from riftlord.fab import packs
    if name in packs.PACKS:
        return packs.PACKS[name][0]
    return ''.join(w[:1].upper() + w[1:] for w in re.split(r'[^A-Za-z0-9]+', name) if w)[:40] or 'Pack'


def owners(root=None, rows=None, library=None):
    """{pack id: {lock paths}}: the locks a convert or terrain build of each pack holds. From the
    pack registry, the vault listing's `rows` ({item, pack_id}), the loose packs recorded in the
    `library` folder's imports.json, and the terrain packs present."""
    from riftlord.fab import packs
    root = root or cache_root()
    out = {}

    def add(pid, item):
        if pid and item:
            out.setdefault(pid, set()).add(lock_path(os.path.basename(os.path.normpath(item)), root))

    for item, rec in packs.PACKS.items():
        add(rec[0], item)
    for row in rows or []:
        add(row.get('pack_id'), row.get('item'))
    if library:
        try:
            with open(os.path.join(library, 'imports.json')) as fh:
                for path, rec in (json.load(fh) or {}).items():
                    add(rec.get('pack_id'), path)
        except (OSError, ValueError, AttributeError):
            pass
    try:
        names = os.listdir(root)
    except OSError:
        names = []
    for name in names:
        if name.startswith(TERRAIN_PREFIX):
            out.setdefault(name, set()).add(os.path.join(terrain_locks(), name[len(TERRAIN_PREFIX):] + '.lock'))
    return out


def last_used(path):
    """The newest modification time of `path` and the entries directly in it (0 when gone)."""
    try:
        newest = os.lstat(path).st_mtime
        with os.scandir(path) as it:
            for e in it:
                try:
                    newest = max(newest, e.stat(follow_symlinks=False).st_mtime)
                except OSError:
                    pass
        return newest
    except OSError:
        return 0.0


def disk_bytes(path):
    """Bytes `path` takes on disk (a file, or a folder and everything in it)."""
    def one(p):
        st = os.lstat(p)
        return st.st_blocks * 512 if hasattr(st, 'st_blocks') else st.st_size
    try:
        if not os.path.isdir(path) or os.path.islink(path):
            return one(path)
    except OSError:
        return 0
    total = 0
    for dp, _dn, fns in os.walk(path):
        for f in fns:
            try:
                total += one(os.path.join(dp, f))
            except OSError:
                pass
    return total


def _lock(fh):
    """Takes an open file's lock without waiting (vault._try_lock); False when held."""
    if os.name == 'nt':
        import msvcrt
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _try_lock(path, create=False):
    """An open file holding `path`'s lock, None when another process holds it, or False when
    the lock file is missing and not created."""
    if not create and not os.path.exists(path):
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fh = open(path, 'a+')
    if not _lock(fh):
        fh.close()
        return None
    return fh


def held_locks(root=None):
    """The lock files another process holds (Fab converts and terrain kit builds)."""
    out = []
    for folder in (os.path.join(root or cache_root(), 'locks'), terrain_locks()):
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for name in names:
            if not name.endswith('.lock'):
                continue
            got = _try_lock(os.path.join(folder, name))
            if got is None:
                out.append(os.path.join(folder, name))
            elif got:
                got.close()
    return out


def plan(root=None, now=None, max_age=MAX_AGE_S, busy=(), rows=None, library=None, sizes=False, only=None):
    """What a trim would empty: {'packs': [{pack, path, last_used, entries, bytes?}], 'trash':
    [left-over trash], 'kept': [{pack, last_used, why}], 'stopped': why the whole trim stops, or
    None}. `busy`: pack ids not to touch; `sizes`: count each pack's bytes (a walk); `only`:
    the pack ids it may empty (None: every pack)."""
    root = root or cache_root()
    now = time.time() if now is None else now
    own = owners(root, rows, library)
    lock_pack = {p: pid for pid, paths in own.items() for p in paths}
    out = {'root': root, 'packs': [], 'kept': [], 'trash': [], 'stopped': None}
    held = held_locks(root)
    unknown = [p for p in held if p not in lock_pack]
    if unknown:
        out['stopped'] = 'a convert of a pack not known here is running (%s)' % ', '.join(
            os.path.basename(p) for p in unknown)
        return out
    busy = set(busy or ()) | {lock_pack[p] for p in held}
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    trash = os.path.join(root, TRASH)
    if os.path.isdir(trash):
        out['trash'] = [os.path.join(trash, n) for n in sorted(os.listdir(trash))]
    imports = os.path.join(root, 'imports')
    by_pack = {}
    if os.path.isdir(imports):
        for name in sorted(os.listdir(imports)):
            stem = re.sub(r'-[0-9a-f]+(\.part)?$', '', name)
            by_pack.setdefault(loose_pack_id(stem), []).append((stem, os.path.join(imports, name)))
    packs = [n for n in names if n not in SHARED and not n.startswith('.') and os.path.isdir(os.path.join(root, n))]
    for pid in sorted(set(packs) | set(by_pack)):
        if only is not None and pid not in only:
            continue
        path = os.path.join(root, pid)
        extracted = [p for _s, p in by_pack.get(pid, [])]
        used = max([last_used(path)] + [last_used(p) for p in extracted])
        if pid in busy:
            out['kept'].append({'pack': pid, 'last_used': used, 'why': 'busy'})
            continue
        if now - used < max_age:
            out['kept'].append({'pack': pid, 'last_used': used, 'why': 'used within %g h' % (max_age / 3600.0)})
            continue
        entries = []
        if os.path.isdir(path):
            entries = [os.path.join(path, n) for n in sorted(os.listdir(path)) if n not in KEEP]
        entries += extracted
        rec = {'pack': pid, 'path': path, 'last_used': used, 'entries': entries,
               'locks': sorted(own.get(pid, set()) | {lock_path(s + ext, root) for s, _p in by_pack.get(pid, [])
                                                   for ext in ('.zip', '.ZIP')})}
        if sizes:
            rec['bytes'] = sum(disk_bytes(e) for e in entries)
        if entries or (os.path.isdir(path) and not os.listdir(path)):
            out['packs'].append(rec)
    if sizes:
        out['trash_bytes'] = sum(disk_bytes(p) for p in out['trash'])
    return out


def _move_out(rec, trash, now, max_age):
    """Moves a pack's entries into `trash` holding its locks; [(trash path, what it was)], or
    None when a lock is held or the pack was used meanwhile."""
    holds = []
    try:
        for p in rec['locks']:
            got = _try_lock(p, create=os.path.isdir(os.path.dirname(p)))
            if got is None:
                return None
            if got:
                holds.append(got)
        if now - max([last_used(rec['path'])] + [last_used(e) for e in rec['entries']
                                                  if not e.startswith(rec['path'] + os.sep)]) < max_age:
            return None
        os.makedirs(trash, exist_ok=True)
        moved = []
        for e in rec['entries']:
            if not os.path.lexists(e):
                continue
            dest = os.path.join(trash, '%s-%d-%d-%s' % (rec['pack'], os.getpid(), len(moved), os.path.basename(e)))
            try:
                os.rename(e, dest)
            except OSError:
                continue                 # in use (Windows) or gone: left for the next trim
            moved.append((dest, e))
        try:
            if os.path.isdir(rec['path']) and not os.listdir(rec['path']):
                os.rmdir(rec['path'])
        except OSError:
            pass
        return moved
    finally:
        for fh in holds:
            fh.close()


def _delete(path):
    """Deletes a file or folder; the bytes it took."""
    n = disk_bytes(path)
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path, ignore_errors=True)
    else:
        try:
            os.remove(path)
        except OSError:
            pass
    return n if not os.path.lexists(path) else 0


def human(n):
    for unit, size in (('GB', 1 << 30), ('MB', 1 << 20), ('KB', 1 << 10)):
        if n >= size:
            return '%.1f %s' % (n / size, unit)
    return '%d bytes' % n


def trim(root=None, now=None, max_age=MAX_AGE_S, busy=(), rows=None, library=None, dry_run=False,
         log=None, on_moved=None, only=None):
    """Empties the working folders of packs not used for `max_age` seconds (plan(); `only`
    limits it to those pack ids). `log(text)`
    gets one line per pack emptied and a total; `on_moved()` runs once the folders are moved
    aside (converts may start again) and before they are deleted. Returns {'freed', 'packs':
    [{pack, bytes, last_used}], 'stopped'} (with dry_run the plan's sizes, nothing touched)."""
    root = root or cache_root()
    now = time.time() if now is None else now
    log = log or (lambda _t: None)
    report = {'root': root, 'freed': 0, 'packs': [], 'stopped': None, 'dry_run': bool(dry_run)}
    os.makedirs(root, exist_ok=True)
    gate = open(os.path.join(root, TRIM_LOCK), 'a+')
    try:
        if not _lock(gate):
            report['stopped'] = 'another trim is running'
            if on_moved:
                on_moved()
            return report
        try:
            got = plan(root, now, max_age, busy, rows, library, sizes=dry_run, only=only)
            report['stopped'] = got['stopped']
            if dry_run:
                report['packs'] = [{'pack': r['pack'], 'bytes': r['bytes'], 'last_used': r['last_used'],
                                    'entries': r['entries']} for r in got['packs']]
                report['kept'] = got['kept']
                report['trash'] = got['trash']
                report['freed'] = sum(r['bytes'] for r in got['packs']) + got.get('trash_bytes', 0)
                return report
            trash = os.path.join(root, TRASH)
            moved = []
            for rec in got['packs']:
                m = _move_out(rec, trash, now, max_age)
                if m is not None:
                    moved.append((rec, m))
        finally:
            if on_moved:
                on_moved()
        for path in got['trash']:
            report['freed'] += _delete(path)
        for rec, m in moved:
            n = sum(_delete(dest) for dest, _was in m)
            report['freed'] += n
            report['packs'].append({'pack': rec['pack'], 'bytes': n, 'last_used': rec['last_used']})
            if n:
                log('Freed %s of converter cache from %s (last converted %s)'
                    % (human(n), rec['pack'], time.strftime('%Y-%m-%d %H:%M', time.localtime(rec['last_used']))))
        try:
            os.rmdir(trash)
        except OSError:
            pass
        if report['freed']:
            log('Fab converter cache: freed %s in all' % human(report['freed']))
        return report
    finally:
        gate.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--dry-run', action='store_true', help='list what would be freed; touch nothing')
    ap.add_argument('--hours', type=float, default=MAX_AGE_S / 3600.0, help='age in hours (default 24)')
    ap.add_argument('--root', help='cache folder (default: the converter cache)')
    ap.add_argument('--library', help='Fab library folder, for loose packs (default: none)')
    ap.add_argument('--only', help='empty only these packs (PACK,PACK,...; default: every pack)')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    only = None if a.only is None else {p.strip() for p in a.only.split(',') if p.strip()}
    rep = trim(a.root, max_age=a.hours * 3600.0, library=a.library, dry_run=a.dry_run,
               log=None if a.json else print, only=only)
    if a.json:
        print(json.dumps(rep, sort_keys=True))
        return 0
    if rep['stopped']:
        print('stopped: %s' % rep['stopped'])
    if a.dry_run:
        for r in sorted(rep['packs'], key=lambda r: -r['bytes']):
            print('%-26s %10s  last converted %s' % (r['pack'], human(r['bytes']),
                                                    time.strftime('%Y-%m-%d %H:%M', time.localtime(r['last_used']))))
        for k in rep.get('kept') or []:
            print('%-26s %10s  kept: %s' % (k['pack'], '', k['why']))
        print('would free %s' % human(rep['freed']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
