"""A stand-in Fab converter for the UI smoke suite's `fab` case: the converter's JSON
command line (vault / measure / convert / delete) over seven made-up vault packs, with
progress events (`fraction` and `eta_s` included), pack kinds, the convert limits in
`vault`'s `settings`, a per-item lock and a worker child, writing small libraries.

    RIFTLORD_FABCONVERT=tests/ui_smoke/fakefab.py python3 -m riftlord ...

Its state (measures, locks, the seeded libraries' marker, each command's arguments in
args.jsonl) is kept in $RIFTLORD_FAKEFAB_STATE, else $XDG_CACHE_HOME/fakefab. Libraries go
to --out. With --root, the vault holds the packs only when that folder exists.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

STAGES = ('scan', 'materials', 'bake', 'textures', 'meshes', 'pack', 'checks')
#: The vault: item -> (title, engine, assets, meshes, pack id, registry scale or None).
ITEMS = {
    'AmberNPC5ee3071625f9V1': ('Amber - NPC Character Voice Pack', None, 2832, 0, 'NPCAmberVP', None),
    'Stylized0bce0f98767dV3': ('Stylized Wooden Chest', 'UE4', 20, 2, 'WoodenChest', None),
    'DesertCa3432654395afV1': ('Desert Castle', 'UE5', 212, 97, 'DesertCastle', 1.5),
    'CityofBra13001ef61fcV1': ('City of Brass: Environments', 'UE4', 749, 360, 'CityOfBrass', 1.5),
    'FluidNin01097781a25fV10': ('Fluid Ninja VFX Tools', 'UE4', 1428, 29, 'FluidNinja', None),
    'Spaceshi4ce763912c34V1': ('Spaceship Interior Environment Set', 'UE4', 387, 43,
                               'SpaceshipInterior', None),
    'BullShar77aa01c2d3e4V1': ('Bull Shark', 'UE4', 14, 1, 'BullShark', None),
}
#: Each item's kind (`kind` in vault rows).
KINDS = {'AmberNPC5ee3071625f9V1': 'none', 'Stylized0bce0f98767dV3': 'props',
         'DesertCa3432654395afV1': 'building', 'CityofBra13001ef61fcV1': 'building',
         'FluidNin01097781a25fV10': 'mixed', 'Spaceshi4ce763912c34V1': 'building',
         'BullShar77aa01c2d3e4V1': 'creature'}
#: The convert limits' defaults `vault` reports.
SETTINGS = {'memory_mb': 8192, 'workers': 2, 'cpu_share': 0.25}
#: Seconds a convert of each item is estimated to take, for `eta_s`.
ETA_TOTAL = 30.0
#: Libraries present before the first command: pack id -> status.
SEEDED = {'DesertCastle': 'current', 'CityOfBrass': 'stale', 'FluidNinja': 'failed'}
#: Seconds between progress events, by item (others FAST).
FAST, SLOW = 0.12, 1.5
DELAY = {'Spaceshi4ce763912c34V1': SLOW}
#: Meshes every loose pack (a .zip or folder given by path) converts.
LOOSE_MESHES = 3
#: Seconds a vault scan takes per item: the first scan, and every later one.
SCAN_COLD, SCAN_WARM = 1.2, 0.03
#: Problems a convert of an item reports (exit 1).
PROBLEMS = {'CityofBra13001ef61fcV1': ['SM_Wall_04: collision hull not cooked',
                                      'M_Brass_Trim: base colour texture missing']}
#: Converts that crash after the bake stage (exit 3) with the converter's words.
CRASH = {'FluidNin01097781a25fV10': 'MemoryError: worker 3 resident 19500 MB over its 5632 MB guard'}
MEASURE = {'scale': 1.5, 's_char': 1.08,
           'evidence': ['doorways 301 uu tall (median) as authored',
                        'character scale 1.08 (doorways and risers against OMD3)',
                        'x1: 12% of building pieces on whole trap cells, doorways 301 uu',
                        'x1.5: 31% of building pieces on whole trap cells, doorways 452 uu',
                        'chosen x1.5']}
ROOTS = ['/fake/EpicVault', '/fake/home/EpicVault']


def state_dir():
    base = os.environ.get('RIFTLORD_FAKEFAB_STATE') or os.path.join(
        os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache'), 'fakefab')
    os.makedirs(os.path.join(base, 'locks'), exist_ok=True)
    os.makedirs(os.path.join(base, 'measure'), exist_ok=True)
    return base


def out_dir(a):
    if not a.out:
        raise SystemExit('fakefab: --out is required')
    os.makedirs(a.out, exist_ok=True)
    return a.out


def write_library(out, pid, title, scale, status, problems=()):
    lib = os.path.join(out, pid)
    new = lib + '.new'
    shutil.rmtree(new, ignore_errors=True)
    os.makedirs(os.path.join(new, 'thumbs'))
    cat = {'format': 'rift-lord-fab-library', 'format_version': 1, 'pak': pid + '.pak',
           'build': 'fake-%s' % status, 'pack': {'id': pid, 'title': title, 'scale': scale},
           'meshes': []}
    with open(os.path.join(new, 'catalogue.json'), 'w') as fh:
        json.dump(cat, fh)
    open(os.path.join(new, pid + '.pak'), 'wb').close()
    with open(os.path.join(new, 'report.json'), 'w') as fh:
        json.dump({'ok': status != 'failed', 'problems': list(problems)}, fh)
    with open(os.path.join(new, 'fake.json'), 'w') as fh:
        json.dump({'status': status, 'problems': len(problems)}, fh)
    shutil.rmtree(lib, ignore_errors=True)
    os.replace(new, lib)


def seed(out):
    import hashlib
    mark = os.path.join(state_dir(), 'seeded-' + hashlib.sha1(
        os.path.abspath(out).encode('utf-8')).hexdigest()[:16])
    if os.path.exists(mark):
        return
    for item, (title, _e, _a, _m, pid, scale) in ITEMS.items():
        if pid in SEEDED:
            write_library(out, pid, title, scale or 1.0, SEEDED[pid],
                          ['a seeded problem'] if SEEDED[pid] == 'failed' else ())
    open(mark, 'w').close()


def lock_path(item):
    return os.path.join(state_dir(), 'locks', item + '.lock')


def acquire(item):
    import fcntl
    fh = open(lock_path(item), 'a+')
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def running(item):
    fh = acquire(item)
    if fh is None:
        return True
    fh.close()
    return False


def measured(item):
    try:
        with open(os.path.join(state_dir(), 'measure', item + '.json')) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def library(out, pid):
    try:
        with open(os.path.join(out, pid, 'fake.json')) as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        return {'status': 'none', 'build': None, 'converted': 0, 'problems': 0, 'pak_bytes': 0}
    return {'status': meta['status'], 'build': 'fake', 'converted': 1,
            'problems': meta.get('problems', 0), 'pak_bytes': 0}


def imports():
    """{source path: {pack_id, title, scale, stat}} of loose packs converted before."""
    try:
        with open(os.path.join(state_dir(), 'imports.json')) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def write_imports(rec):
    with open(os.path.join(state_dir(), 'imports.json'), 'w') as fh:
        json.dump(rec, fh, indent=1, sort_keys=True)


def source_stat(path):
    st = os.stat(path)
    return [st.st_size, st.st_mtime_ns]


def key(item):
    """The lock and measure name of an item: a loose pack's file or folder name."""
    return os.path.basename(item.rstrip('/')) if os.path.isabs(item) else item


def find(item, missing_ok=False):
    """(title, engine, assets, meshes, pack id, registry scale) of a vault item or loose pack."""
    if os.path.isabs(item):
        rec = imports().get(item)
        if rec and (missing_ok or os.path.exists(item)):
            return rec['title'], None, None, LOOSE_MESHES, rec['pack_id'], rec['scale']
        if not (os.path.isdir(item) or (os.path.isfile(item) and item.endswith('.zip'))):
            raise FileNotFoundError('no loose model pack at %s' % item)
        title = key(item)[:-4] if item.endswith('.zip') else key(item)
        return title, None, None, LOOSE_MESHES, 'File' + ''.join(c for c in title if c.isalnum())[:32], None
    if item not in ITEMS:
        raise FileNotFoundError('no vault item %s' % item)
    return ITEMS[item]


def import_rows(out):
    rows = []
    for path, rec in sorted(imports().items()):
        exists = os.path.exists(path)
        lib = library(out, rec['pack_id'])
        if exists and source_stat(path) != rec['stat'] and lib['status'] == 'current':
            lib['status'] = 'stale'
        rows.append({'item': path, 'path': path, 'title': rec['title'], 'engine': None, 'assets': None,
                     'meshes': lib['converted'], 'known': False, 'pack_id': rec['pack_id'],
                     'scale': rec['scale'], 'scale_source': 'registry', 'source': 'file',
                     'exists': exists, 'running': running(key(path)), 'library': lib})
    return rows


def cmd_vault(a):
    out = out_dir(a)
    seed(out)
    # the first scan reads every item cold; later ones are quick
    cold = os.path.join(state_dir(), 'scanned')
    delay = SCAN_WARM if os.path.exists(cold) else SCAN_COLD
    rows = []
    found = [a.root] if a.root and os.path.isdir(a.root) else ([] if a.root else ROOTS[:1])
    searched = [a.root] if a.root else ROOTS
    todo = sorted(ITEMS.items()) if found else []
    if a.events:
        vault_event(0, len(todo), '')
    for k, (item, (title, engine, assets, meshes, pid, scale)) in enumerate(todo):
        time.sleep(delay)
        if a.events:
            vault_event(k + 1, len(todo), item)
        source = 'registry' if scale else 'default'
        got = measured(item)
        if not scale and got:
            scale, source = got['scale'], 'measured'
        rows.append({'item': item, 'path': os.path.join(found[0], item), 'title': title,
                     'engine': engine, 'assets': assets, 'meshes': meshes, 'known': source == 'registry',
                     'kind': KINDS[item], 'pack_id': pid, 'scale': scale or 1.0, 'scale_source': source,
                     'source': 'vault', 'exists': True,
                     'running': running(item), 'library': library(out, pid)})
    open(cold, 'w').close()
    return {'ok': True, 'roots': {'found': found, 'searched': searched}, 'items': rows + import_rows(out),
            'library': out, 'settings': SETTINGS}


def vault_event(done, total, msg):
    sys.stderr.write(json.dumps({'stage': 'vault', 'index': 0, 'stages': 1, 'done': done,
                                 'total': total, 'msg': msg}) + '\n')
    sys.stderr.flush()


def cmd_measure(a):
    find(a.item)
    got = measured(key(a.item))
    if not got:
        time.sleep(1.0)
        got = dict(MEASURE, item=a.item, version='measure-1', fingerprint='fake')
        with open(os.path.join(state_dir(), 'measure', key(a.item) + '.json'), 'w') as fh:
            json.dump(got, fh)
    return dict(got, ok=True)


def cmd_delete(a):
    _t, _e, _as, _m, pid, _s = find(a.item, missing_ok=True)
    hold = acquire(key(a.item))
    if hold is None:
        raise RuntimeError('%s is being converted; stop that first' % a.item)
    try:
        rec = imports()
        if rec.pop(a.item, None) is not None:
            write_imports(rec)
        lib = os.path.join(out_dir(a), pid)
        removed = []
        if os.path.isdir(lib):
            shutil.rmtree(lib)
            removed.append(lib)
        return {'ok': True, 'item': a.item, 'pack_id': pid, 'removed': removed}
    finally:
        hold.close()


def event(stage, done, total, msg=''):
    fraction = (STAGES.index(stage) + done / max(1, total)) / len(STAGES)
    sys.stderr.write(json.dumps({'stage': stage, 'index': STAGES.index(stage), 'stages': len(STAGES),
                                 'done': done, 'total': total, 'msg': msg, 'fraction': round(fraction, 4),
                                 'eta_s': round(ETA_TOTAL * (1 - fraction), 1)}) + '\n')
    sys.stderr.flush()


def cmd_convert(a):
    title, _e, _as, meshes, pid, scale = find(a.item)
    out = out_dir(a)
    hold = acquire(key(a.item))
    if hold is None:
        return {'ok': False, 'exit': 4, 'error': '%s is already being converted' % a.item}, 4
    worker = subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(600)'])
    try:
        log = lambda m: (sys.stderr.write('[fabconvert] %s\n' % m), sys.stderr.flush())  # noqa: E731
        log('%s (%s): scale %.2f, library %s' % (title, pid, a.scale or scale or 1.0,
                                                  os.path.join(out, pid)))
        log('worker pid %d' % worker.pid)
        delay = DELAY.get(a.item, FAST)
        for stage in STAGES:
            log('%s' % stage)
            total = 3
            for done in range(total + 1):
                event(stage, done, total)
                time.sleep(delay)
            if stage == 'bake' and a.item in CRASH:
                log('Traceback (most recent call last):')
                log(CRASH[a.item])
                return {'ok': False, 'exit': 3, 'error': CRASH[a.item]}, 3
        problems = PROBLEMS.get(a.item, [])
        write_library(out, pid, title, a.scale or scale or 1.0,
                      'failed' if problems else 'current', problems)
        if os.path.isabs(a.item):
            rec = imports()
            rec[a.item] = {'pack_id': pid, 'title': title, 'scale': a.scale or scale or 1.0,
                           'stat': source_stat(a.item)}
            write_imports(rec)
        code = 1 if problems else 0
        return {'ok': not problems, 'exit': code, 'library': os.path.join(out, pid),
                'pak': os.path.join(out, pid, pid + '.pak'), 'problems': problems,
                'problem_count': len(problems), 'counts': {'meshes': meshes}, 'sizes': {},
                'seconds': 1.0}, code
    finally:
        worker.kill()
        worker.wait()
        hold.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd')
    c = sub.add_parser('convert')
    c.add_argument('pack'); c.add_argument('--out'); c.add_argument('--scale', type=float)
    c.add_argument('--json', action='store_true'); c.add_argument('--events', action='store_true')
    c.add_argument('--root'); c.add_argument('--jobs', type=int)
    c.add_argument('--memory-mb', type=int); c.add_argument('--workers', type=int)
    v = sub.add_parser('vault')
    v.add_argument('--root'); v.add_argument('--out'); v.add_argument('--json', action='store_true')
    v.add_argument('--events', action='store_true')
    m = sub.add_parser('measure')
    m.add_argument('item'); m.add_argument('--root'); m.add_argument('--json', action='store_true')
    d = sub.add_parser('delete')
    d.add_argument('item'); d.add_argument('--root'); d.add_argument('--out')
    d.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    with open(os.path.join(state_dir(), 'args.jsonl'), 'a') as fh:
        fh.write(json.dumps(sys.argv[1:] if argv is None else list(argv)) + '\n')
    code = 0
    try:
        if a.cmd == 'convert':
            a.item = a.pack
            out, code = cmd_convert(a)
        else:
            out = {'vault': cmd_vault, 'measure': cmd_measure, 'delete': cmd_delete}[a.cmd](a)
            code = 0 if out.get('ok') else 2
    except FileNotFoundError as e:
        out, code = {'ok': False, 'error': str(e)}, 2
    except RuntimeError as e:
        out, code = {'ok': False, 'error': str(e)}, 4
    out['exit'] = code
    print(json.dumps(out, sort_keys=True), flush=True)
    return code


if __name__ == '__main__':
    sys.exit(main())
