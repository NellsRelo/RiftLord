# Generated from OMD2-Converter/thirdparty/fabconvert/vault.py by thirdparty/fabconvert/port_riftlord.py; edit the source.
"""The author's Epic vault as the Rift Lord Fab Kits tab sees it: where it is, what is in it,
which packs are converted, the scale a pack not in the registry should take, and the lock that
tells whether a convert of a pack is running.

A vault item is an Epic VaultCache folder: <item>/vault.json (title), manifest.json and
data/Content/<pack folders>. Vault data is untrusted: callers run under `python3 -I`.
"""

import json
import math
import os
import shutil
import sys

from riftlord.fab import library
from riftlord.fab import packs as PK

#: Vault roots looked at, in order, when none is given (RIFTLORD_FAB_VAULT first). Epic's
#: launcher keeps downloaded Fab/Marketplace packs in its VaultCache; under Wine that lies in the
#: prefix's ProgramData.
VAULT_ENV = 'RIFTLORD_FAB_VAULT'
EPIC_VAULT_REL = os.path.join('ProgramData', 'Epic', 'EpicGamesLauncher', 'VaultCache')
LINUX_PREFIX_GLOBS = ('~/Games/Heroic/Prefixes/*/pfx/drive_c', '~/Games/Heroic/Prefixes/*/drive_c',
                      '~/.wine/drive_c', '~/Games/epic-games-store/drive_c', '~/Games/*/drive_c',
                      '~/.local/share/lutris/runners/wine/*/drive_c')
LINUX_ROOTS = ('~/Documents/EpicVault', '~/EpicVault')

#: Scale measurement (user rule, 2026-10-14: the pack's module lands on whole trap cells, the
#: larger end; char scale from doorways and stair risers). Candidates the tab can end on.
MEASURE_CANDIDATES = (1.0, 1.5, 2.0, 3.0)
#: Candidates considered when the pack's character scale is known: within this factor of it.
#: 1.6 keeps the user's grid-fit choices for City of Brass and Sky Dungeon (s_char 0.97 / 0.95,
#: x1.5 for their 400 uu module) and excludes x3 for Desert Castle (s_char 1.23).
CHAR_RANGE = 1.6
#: A grid-fit share counts only when this many building pieces were tested (Orasot has 3).
ARCH_MIN = 5
#: Without a character scale (no doors or stairs) a pack stays near its authored size.
NO_CHAR_CANDIDATES = (1.0, 1.5)
#: Grid-fit shares closer than this count as a tie (then: nearer the character scale, else 1.0).
FIT_TIE = 0.02
#: Measure outputs are cached per pack fingerprint under the converter cache.
MEASURE_VERSION = 'measure-1'


# -- where the vault is --------------------------------------------------------------------------------

def candidate_roots():
    """Every folder that may hold vault items, in search order."""
    import glob
    out = []
    if os.environ.get(VAULT_ENV):
        out.append(os.environ[VAULT_ENV])
    if os.name == 'nt':
        pd = os.environ.get('ProgramData', r'C:\ProgramData')
        out.append(os.path.join(pd, 'Epic', 'EpicGamesLauncher', 'VaultCache'))
        out.append(os.path.join(os.path.expanduser('~'), 'Documents', 'EpicVault'))
    else:
        out += [os.path.expanduser(p) for p in LINUX_ROOTS]
        for g in LINUX_PREFIX_GLOBS:
            out += [os.path.join(c, EPIC_VAULT_REL) for c in sorted(glob.glob(os.path.expanduser(g)))]
    seen, uniq = set(), []
    for p in out:
        p = os.path.abspath(p)
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def is_item(path):
    return os.path.isdir(os.path.join(path, 'data', 'Content'))


def find_roots(root=None):
    """(vault roots holding at least one item, every root searched)."""
    searched = [os.path.abspath(root)] if root else candidate_roots()
    found = [r for r in searched if os.path.isdir(r) and any(is_item(os.path.join(r, d)) for d in os.listdir(r))]
    return found, searched


# -- what is in it ---------------------------------------------------------------------------------------

def _read_json(path):
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _assets(content):
    n = 0
    for _dp, _dn, fns in os.walk(content):
        n += sum(1 for f in fns if f.endswith(('.uasset', '.umap')))
    return n


#: Bytes read for a package's summary, names, imports and exports, tried in turn (the whole file
#: only when the header fits none): a listing must not read every texture's bulk data. 1 MB
#: first read 17 GB for a 44-item vault (62 s cold).
HEADER_READS = (1 << 16, 1 << 20)


def _header(path):
    """pkgread.Package of a file's header, or None when it does not parse."""
    from riftlord.fab.survey import pkgread
    try:
        with open(path, 'rb') as fh:
            for n in HEADER_READS:
                fh.seek(0)
                data = fh.read(n)
                try:
                    return pkgread.Package(path, data)
                except Exception:                        # noqa: BLE001 - header longer than the read
                    if len(data) < n:
                        return None
            fh.seek(0)
            return pkgread.Package(path, fh.read())
    except Exception:                                    # noqa: BLE001 - not a package we read
        return None


#: Header reads run on this many threads: a cold scan is seek-bound (about 4 ms per file, 18,561
#: files for a 44-item vault), and the disk serves parallel reads. The user's quarter-of-the-CPUs
#: rule (library.CPU_SHARE), at least 2: serial 59.8 s, 8 threads 28.6 s on this vault.
SCAN_THREADS = max(2, int((os.cpu_count() or 2) * library.CPU_SHARE))


#: Main-export classes a listing counts (Niagara and Cascade both as particles).
COUNTED = {'StaticMesh': 'static', 'SkeletalMesh': 'skeletal', 'AnimSequence': 'animations',
           'ParticleSystem': 'particles', 'NiagaraSystem': 'particles'}
#: Item kinds by the share of its static meshes named as level art (survey-tools report.role:
#: building parts, terrain, cliffs and rocks, foliage). Measured over the 48-item vault
#: 2026-10-28: props packs 0-0.18 (City of Brass Props 0.18), building packs 0.60-0.99,
#: packs of both 0.22-0.46 (Low Poly Tropical, Medieval Dungeon, Tavern, Storage House, Wild
#: West, Docks, Edith Finch); each limit is the middle of a gap.
PROPS_BELOW = 0.20
BUILDING_FROM = 0.53
LEVEL_ROLES = ('floor', 'wall', 'stairs', 'structure', 'roof', 'column', 'door_window_arch',
               'terrain', 'cliff_rock', 'foliage')


def _classify(path):
    """(counted class, 'UE4' / 'UE5') of a package's first counted main export; ('unread', None)
    when its header does not parse (a newer engine's package); else None."""
    pk = _header(path)
    if pk is None:
        return 'unread', None
    for e in pk.exports:
        if e['outer'] == 0 and pk.class_of(e) in COUNTED:
            return COUNTED[pk.class_of(e)], 'UE5' if pk.ue5 else 'UE4'
    return None


def item_kind(counts, level_share):
    """'creature' (characters with animation, at most a few props), 'none' (nothing to place,
    or an effects pack), else 'building' / 'mixed' / 'props' by level_share."""
    static, skel = counts.get('static', 0), counts.get('skeletal', 0)
    if skel and (static <= skel or counts.get('animations', 0) > static):
        return 'creature'
    if not static or counts.get('particles', 0) > static:
        return 'none'
    if level_share >= BUILDING_FROM:
        return 'building'
    return 'props' if level_share < PROPS_BELOW else 'mixed'


def _info_path(item):
    d = os.path.join(library.cache_root(), 'vaultinfo')
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, '%s.json' % library.sanitize(item))


def item_fingerprint(item, path, proots):
    """library.fingerprint of an item's pack roots, cached while the item's manifest.json (which
    the Epic launcher rewrites on every download or update) keeps its size and mtime."""
    man = os.path.join(path, 'manifest.json')
    st = os.stat(man) if os.path.exists(man) else os.stat(path)
    key = [st.st_size, st.st_mtime_ns, sorted(proots)]
    cache = _info_path(item + '.fp')
    got = _read_json(cache)
    if got.get('key') == key:
        return got['fingerprint']
    fp = library.fingerprint(proots)
    with open(cache, 'w') as fh:
        json.dump({'key': key, 'fingerprint': fp}, fh)
    return fp


def item_info(item, content, proots, fp=None):
    """{engine: UE4|UE5, meshes: static meshes the converter would convert, assets, counts
    (COUNTED, and 'unread': packages whose header does not parse), kind (item_kind)}, read from the package headers once per pack fingerprint
    (cached)."""
    from riftlord.fab.survey import report
    fp = fp or library.fingerprint(proots)
    got = _read_json(_info_path(item))
    if got.get('fingerprint') == fp and 'unread' in (got.get('counts') or {}):
        return got
    from concurrent.futures import ThreadPoolExecutor
    paths = sorted(os.path.join(dp, f) for root in proots for dp, _dn, fns in os.walk(root)
                   for f in fns if f.endswith('.uasset'))
    with ThreadPoolExecutor(SCAN_THREADS) as pool:
        found = list(pool.map(_classify, paths))
    meshes = [p for p, k in zip(paths, found) if k and k[0] == 'static']
    counts = {c: sum(1 for k in found if k and k[0] == c) for c in sorted(set(COUNTED.values()) | {'unread'})}
    engine = next((k[1] for k in found if k and k[0] == 'static'), None) or next((k[1] for k in found if k and k[1]), None)
    level = sum(1 for p in meshes if report.role(p) in LEVEL_ROLES) / max(len(meshes), 1)
    out = {'fingerprint': fp, 'engine': engine, 'meshes': len(meshes), 'assets': _assets(content),
           'counts': counts, 'kind': item_kind(counts, level)}
    with open(_info_path(item), 'w') as fh:
        json.dump(out, fh)
    return out


def library_status(pid, roots, scale, out_dir=None, fp=None):
    """{status: none|current|stale|failed, build, converted, problems, pak_bytes} of a pack's
    library. `stale`: a convert now would give another build (converter code, pack files or
    scale changed)."""
    lib = os.path.join(out_dir or library.default_library(), pid)
    cat = _read_json(os.path.join(lib, 'catalogue.json'))
    rep = _read_json(os.path.join(lib, 'report.json'))
    if not cat:
        return {'status': 'failed' if rep else 'none', 'build': None, 'converted': 0,
                'problems': len(rep.get('problems') or []), 'pak_bytes': 0}
    want = library.build_id(fp or library.fingerprint(roots), scale) if scale else None
    pak = os.path.join(lib, cat.get('pak') or '')
    st = 'current' if want and cat.get('build') == want else 'stale'
    if rep and not rep.get('ok', True):
        st = 'failed'
    return {'status': st, 'build': cat.get('build'),
            'converted': sum(1 for m in cat.get('meshes') or [] if m.get('status') == 'converted'),
            'problems': len(rep.get('problems') or []),
            'pak_bytes': os.path.getsize(pak) if os.path.isfile(pak) else 0}


def vault_events(done, total, msg=''):
    """A `vault` progress event as a JSON line on stderr: {stage: 'vault', index 0, stages 1,
    done, total, msg} (the item being read)."""
    sys.stderr.write(json.dumps({'stage': 'vault', 'index': 0, 'stages': 1, 'done': done, 'total': total,
                                 'msg': msg}) + '\n')
    sys.stderr.flush()


def items(root=None, out_dir=None, events=None):
    """({vault roots found, roots searched}, [item rows]) for the tab. events(done, total, msg)
    reports each item as it is read (a cold scan reads every package header once)."""
    found, searched = find_roots(root)
    rows = []
    todo = [(r, d) for r in found for d in sorted(os.listdir(r)) if is_item(os.path.join(r, d))]
    for k, (r, d) in enumerate(todo):
        if events:
            events(k, len(todo), d)
        path = os.path.join(r, d)
        meta = _read_json(os.path.join(path, 'vault.json'))
        try:
            item, content, proots = PK.locate(path)
        except FileNotFoundError:
            continue
        ident = PK.identity(item, proots)
        scale, source = ident['scale'], 'registry' if ident['known'] else 'default'
        fp = item_fingerprint(d, path, proots)
        measured = cached_measure(d, proots, fp)
        if not ident['known'] and measured:
            scale, source = measured['scale'], 'measured'
        info = item_info(d, content, proots, fp)
        rows.append({'item': d, 'path': path, 'title': meta.get('title') or ident['title'],
                     'engine': info['engine'], 'assets': info['assets'], 'meshes': info['meshes'],
                     'kind': info['kind'], 'counts': info['counts'],
                     'known': ident['known'], 'source': 'vault', 'exists': True,
                     'pack_id': ident['id'], 'scale': scale, 'scale_source': source,
                     'running': is_running(d),
                     'library': library_status(ident['id'], proots, scale, out_dir, fp)})
    if events:
        events(len(todo), len(todo))
    return {'found': found, 'searched': searched}, rows + import_rows(out_dir)


# -- loose packs converted from a file (zip or folder outside the vault) -----------------------------

#: The record of a library's loose packs lives beside them (a cleared cache must not orphan
#: converted packs from the tab); earlier builds kept one in the converter cache.
IMPORTS_FILE = 'imports.json'


def _out(out_dir):
    return os.path.abspath(out_dir or library.default_library())


def _imports_path(out_dir):
    return os.path.join(_out(out_dir), IMPORTS_FILE)


def _write_imports(rec, out_dir):
    os.makedirs(_out(out_dir), exist_ok=True)
    tmp = _imports_path(out_dir) + '.tmp'
    with open(tmp, 'w') as fh:
        json.dump(rec, fh, indent=1, sort_keys=True)
    os.replace(tmp, _imports_path(out_dir))


def imports(out_dir=None):
    """{source path: {pack_id, title, scale, fingerprint, stat}} of the loose packs converted into
    this library. Entries for it in the old cache copy move here on first read."""
    rec = _read_json(_imports_path(out_dir))
    old_path = os.path.join(library.cache_root(), IMPORTS_FILE)
    old = _read_json(old_path)
    mine = {k: v for k, v in old.items() if v.get('library', _out(None)) == _out(out_dir)}
    if mine:
        for k, v in mine.items():
            rec.setdefault(k, {kk: vv for kk, vv in v.items() if kk != 'library'})
        _write_imports(rec, out_dir)
        rest = {k: v for k, v in old.items() if k not in mine}
        if rest:
            with open(old_path, 'w') as fh:
                json.dump(rest, fh, indent=1, sort_keys=True)
        else:
            os.remove(old_path)
    return rec


def record_import(path, report, out_dir=None):
    """Remember a converted loose pack (convert calls this after a run on a zip or folder) in
    the library it was converted into."""
    rec = imports(out_dir)
    st = os.stat(path)
    rec[os.path.abspath(path)] = {'pack_id': report['pack']['id'], 'title': report['pack']['title'],
                                  'scale': report['pack']['scale'], 'fingerprint': report['fingerprint'],
                                  'stat': [st.st_size, st.st_mtime_ns]}
    _write_imports(rec, out_dir)


def forget_import(path, out_dir=None):
    rec = imports(out_dir)
    if rec.pop(os.path.abspath(path), None) is not None:
        _write_imports(rec, out_dir)


def import_rows(out_dir=None):
    """Rows like items() for every recorded loose pack, `source: 'file'`; `exists` false when
    the zip or folder has moved (status still shown, nothing to convert from)."""
    rows = []
    for path, r in sorted(imports(out_dir).items()):
        exists = os.path.exists(path)
        # the source as converted (size, mtime): a changed zip or folder makes the library stale
        # without re-reading it on every listing
        changed = exists and [os.stat(path).st_size, os.stat(path).st_mtime_ns] != r.get('stat')
        status = library_status(r['pack_id'], [], r['scale'], out_dir, r.get('fingerprint') or 'unknown')
        if changed and status['status'] == 'current':
            status['status'] = 'stale'
        # a loose pack is listed from its library: the catalogue's roles stand in for the names
        cat = _read_json(os.path.join(_out(out_dir), r['pack_id'], 'catalogue.json'))
        roles = [m.get('role') for m in cat.get('meshes') or [] if m.get('status') == 'converted']
        level = sum(1 for x in roles if x != 'prop') / max(len(roles), 1)
        rows.append({'item': path, 'path': path, 'title': r['title'], 'engine': None, 'assets': None,
                     'kind': item_kind({'static': len(roles)}, level), 'counts': {'static': len(roles)},
                     'meshes': status['converted'], 'known': r['pack_id'] in {v[0] for v in PK.PACKS.values()},
                     'pack_id': r['pack_id'], 'scale': r['scale'], 'scale_source': 'registry',
                     'running': is_running(os.path.basename(path)), 'source': 'file', 'exists': exists,
                     'library': status})
    return rows


def locate_item(name, root=None):
    """A vault item folder by its folder name or a path; or a loose model pack (a folder of FBX
    files and images, or a zip of one) by its path."""
    if os.path.isdir(name) and is_item(name):
        return os.path.abspath(name)
    if (os.path.isfile(name) and name.lower().endswith('.zip')) or (os.path.isdir(name) and PK._has_models(name)):
        return os.path.abspath(name)
    found, searched = find_roots(root)
    for r in found:
        if is_item(os.path.join(r, name)):
            return os.path.join(r, name)
    raise FileNotFoundError('no vault item %s in %s' % (name, ', '.join(searched) or 'no vault root'))


# -- one convert at a time per item ----------------------------------------------------------------------

def lock_path(item):
    d = os.path.join(library.cache_root(), 'locks')
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, '%s.lock' % library.sanitize(item))


def _try_lock(fh):
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


def acquire(item):
    """An open, locked file held for the life of this process (the OS drops it when the process
    ends, however it ends); None when another process holds it."""
    fh = open(lock_path(item), 'a+')
    if not _try_lock(fh):
        fh.close()
        return None
    fh.seek(0)
    fh.truncate()
    fh.write('%d\n' % os.getpid())
    fh.flush()
    return fh


def is_running(item):
    path = lock_path(item)
    if not os.path.exists(path):
        return False
    with open(path, 'a+') as fh:
        if _try_lock(fh):
            return False                # nobody held it (released when this handle closes)
    return True


def delete(item, root=None, out_dir=None, cache=True):
    """Remove a pack's library (and its converter cache); refused while a convert runs. A loose
    pack is named by its source path, which may have moved since (its import record names the
    library)."""
    known = imports(out_dir).get(os.path.abspath(item)) if not os.path.isdir(os.path.join(item, 'data')) else None
    path = os.path.abspath(item) if known else locate_item(item, root)
    name = os.path.basename(path)
    hold = acquire(name)
    if hold is None:
        raise RuntimeError('%s is being converted; stop that first' % name)
    try:
        if known:
            pid = known['pack_id']
        else:
            _i, _c, proots = PK.locate(path)
            pid = PK.identity(_i, proots)['id']
        forget_import(path, out_dir)
        lib = os.path.join(out_dir or library.default_library(), pid)
        removed = []
        if os.path.isdir(lib):
            shutil.rmtree(lib)
            removed.append(lib)
        if cache and os.path.isdir(os.path.join(library.cache_root(), pid)):
            shutil.rmtree(os.path.join(library.cache_root(), pid))
            removed.append(os.path.join(library.cache_root(), pid))
        return {'item': name, 'pack_id': pid, 'removed': removed}
    finally:
        hold.close()


# -- the scale a pack not in the registry takes ---------------------------------------------------------

def _tools():
    here = os.path.dirname(os.path.abspath(__file__))
    for sub in ('survey-tools', 'scale-tools'):
        p = os.path.join(here, '..', sub)
        if p not in sys.path:
            sys.path.insert(0, p)
    return os.path.join(here, 'data')


def _measure_dir(item):
    return os.path.join(library.cache_root(), 'measure', library.sanitize(item))


def cached_measure(item, proots, fp=None):
    got = _read_json(os.path.join(_measure_dir(item), 'measure.json'))
    if not got:
        return None
    if got.get('version') == MEASURE_VERSION and got.get('fingerprint') == (fp or library.fingerprint(proots)):
        return got
    return None


def choose(rec):
    """(scale, [evidence lines]) from one scale_report pack record."""
    s_char = rec.get('s_char')
    share = {float(s): ((c['grid_fit']['share'] or 0.0) if (c['grid_fit']['tested'] or 0) >= ARCH_MIN else 0.0)
             for s, c in rec['checks'].items()}
    if s_char:
        pool = [s for s in MEASURE_CANDIDATES if 1 / CHAR_RANGE <= s / s_char <= CHAR_RANGE]
        pool = pool or [min(MEASURE_CANDIDATES, key=lambda s: abs(math.log(s / s_char)))]
        near = lambda s: abs(math.log(s / s_char))            # noqa: E731
    else:
        pool = list(NO_CHAR_CANDIDATES)
        near = lambda s: abs(math.log(s))                       # noqa: E731
    best = max(share.get(s, 0.0) for s in pool)
    tied = [s for s in pool if share.get(s, 0.0) >= best - FIT_TIE]
    scale = min(tied, key=near)
    lines = []
    f = rec['features']
    if f.get('door_height'):
        lines.append('doorways %.0f uu tall (median) as authored' % f['door_height']['median'])
    if f.get('riser'):
        lines.append('stair risers %.1f uu as authored' % f['riser']['median'])
    lines.append('character scale %s' % ('%.2f (doorways and risers against OMD3)' % s_char if s_char
                                          else 'unknown (no doorways or stairs)'))
    for s in sorted(share):
        if s in MEASURE_CANDIDATES:
            c = rec['checks'][str(s) if str(s) in rec['checks'] else repr(s)]
            extra = ', doorways %.0f uu' % c['door_height'] if c.get('door_height') else ''
            lines.append('x%g: %.0f%% of building pieces on whole trap cells%s'
                         % (s, 100 * share[s], extra))
    lines.append('chosen x%g' % scale)
    return scale, lines


def measure(item, root=None):
    """{scale, evidence, s_char, fingerprint} for a vault item, measured and cached."""
    path = locate_item(item, root)
    name = os.path.basename(path)
    _i, content, proots = PK.locate(path)
    got = cached_measure(name, proots)
    if got:
        return got
    ref = _tools()
    from riftlord.fab.survey import inventory
    from riftlord.fab.scale import pack_features
    from riftlord.fab.survey import report
    from riftlord.fab.scale import scale_report
    d = _measure_dir(name)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    raw, rep, feat = (os.path.join(d, x) for x in ('inventory.json', 'report.json', 'pack-%s.json' % name))
    with open(os.devnull, 'w') as quiet:
        old, sys.stdout = sys.stdout, quiet
        try:
            inventory.run(path, raw)
            report.main(raw, rep, name, 1)
            pack_features.main(rep, content, feat)
            for x in ('omd3-features.json', 'characters.json'):
                shutil.copy(os.path.join(ref, x), d)
            saved = scale_report.CANDIDATES
            scale_report.CANDIDATES = tuple(sorted(set(saved) | set(MEASURE_CANDIDATES)))
            try:
                scale_report.main(d)
            finally:
                scale_report.CANDIDATES = saved
        finally:
            sys.stdout = old
    rec = _read_json(os.path.join(d, 'scale-summary.json'))['packs'][name]
    scale, lines = choose(rec)
    out = {'version': MEASURE_VERSION, 'item': name, 'fingerprint': library.fingerprint(proots),
           'scale': scale, 's_char': rec.get('s_char'), 'evidence': lines}
    with open(os.path.join(d, 'measure.json'), 'w') as fh:
        json.dump(out, fh, indent=1)
    return out
