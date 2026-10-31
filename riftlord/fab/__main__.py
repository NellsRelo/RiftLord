# Generated from OMD2-Converter/fabconvert.py by thirdparty/fabconvert/port_riftlord.py; edit the source.
"""Convert a Fab art pack from the author's Epic vault into a cooked OMD3 asset library.

    python3 -I fabconvert.py convert <item | vault pack dir> [--out LIBRARY] [--jobs N] [--scale S]
                                     [--root VAULT] [--json] [--events]
    python3 -I fabconvert.py vault [--root VAULT] [--out LIBRARY] [--json]   # items + library status
    python3 -I fabconvert.py measure <item> [--root VAULT] [--json]          # suggested scale
    python3 -I fabconvert.py delete <item> [--root VAULT] [--out LIBRARY] [--json]
    python3 -I fabconvert.py packs                          # the known packs and their scales

An <item> is a vault item folder name (found under the vault roots, see vault.candidate_roots;
--root overrides) or a path. --events writes JSON-lines progress on stderr:
{stage, index, stages, done, total, msg}. A convert holds a per-item lock for its whole run
(exit 4 when another convert of that item holds it); `vault` reports it as `running`.

<vault pack dir> is the vault item folder (e.g. ~/Documents/EpicVault/dsa60895b3c928aV1), its
data/Content folder, or one pack folder under Content. The library defaults to the per-user
data folder (gamepath.user_data('FabLibrary'), i.e. ~/.local/share/RiftLord/FabLibrary); the
converted art belongs to the author and is never bundled with Rift Lord.

Output: <out>/<PackId>/<PackId>.pak, catalogue.json, thumbs/, report.json
(thirdparty/LIBRARY-FORMAT.md). Exit codes: 0 converted and every check clean, 1 converted
with problems (listed in report.json), 2 bad arguments or pack not found, 3 crash.
--json prints one JSON summary on stdout; progress goes to stderr.

The vault is only read. Run with `python3 -I` (vault data is untrusted).
"""

import argparse
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, 'thirdparty', 'fabconvert'), HERE]
# one math-library thread per process, set before numpy loads: the worker pool is the
# parallelism (a quarter of the CPUs); 16 OpenMP/BLAS threads per worker broke that
for _var in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ.setdefault(_var, '1')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd')
    c = sub.add_parser('convert', help='convert one vault pack')
    c.add_argument('pack', help='vault pack folder')
    c.add_argument('--out', help='library folder (default: the per-user FabLibrary)')
    c.add_argument('--jobs', '--workers', dest='jobs', type=int, default=None,
                   help='worker processes (default: a quarter of the CPUs, as many as the memory holds)')
    c.add_argument('--memory-mb', type=int, default=None,
                   help='memory the convert may use (MB; default: the free memory less 4 GB)')
    c.add_argument('--scale', type=float, default=None, help='override the pack scale')
    c.add_argument('--foliage-master', choices=('alphamask', 'foliage'), default='alphamask',
                   help='masked two-sided materials: Master_Trap_AlphaMask + duplicated faces (proven '
                        'pieces, default) or Master_Foliage (clip decoded offline; round fc1 retests it)')
    c.add_argument('--only', action='append', help='convert only meshes whose path contains this (repeatable)')
    c.add_argument('--material', action='append', default=[], metavar='SOURCE=SHIPPED',
                   help='draw slots using a source material with a shipped OMD3 material instead of converting '
                        'or dropping them (repeatable; package paths, e.g. '
                        '/Game/MagicPotion/Materials/M_MagicPotionGlass=/Game/FX/Materials/Glass_Inst)')
    c.add_argument('--json', action='store_true', help='print a JSON summary on stdout')
    c.add_argument('--events', action='store_true', help='JSON-lines progress events on stderr')
    c.add_argument('--root', help='vault root (default: searched)')
    v = sub.add_parser('vault', help='list vault items and their library status')
    v.add_argument('--root'); v.add_argument('--out'); v.add_argument('--json', action='store_true')
    v.add_argument('--events', action='store_true', help='JSON-lines progress (one per item) on stderr')
    m = sub.add_parser('measure', help='measure the scale a pack should take')
    m.add_argument('item'); m.add_argument('--root'); m.add_argument('--json', action='store_true')
    d = sub.add_parser('delete', help="remove a pack's library and converter cache")
    d.add_argument('item'); d.add_argument('--root'); d.add_argument('--out'); d.add_argument('--json', action='store_true')
    sub.add_parser('packs', help='list the known packs')
    a = ap.parse_args(argv)
    from riftlord.fab import library
    if a.cmd in ('vault', 'measure', 'delete'):
        return _vault_cmd(a)
    if a.cmd == 'packs':
        from riftlord.fab import packs
        for k, (pid, title, scale, prio) in sorted(packs.PACKS.items(), key=lambda kv: (kv[1][3], kv[1][0])):
            print('%-24s %-18s %-40s scale %s' % (k, pid, title, 'deferred' if scale is None else scale))
        return library.EXIT_OK
    if a.cmd != 'convert':
        ap.print_help()
        return library.EXIT_USAGE
    from riftlord.fab import vault
    hold = None
    try:
        bad = [m for m in a.material if '=' not in m]
        if bad:
            raise ValueError('--material wants SOURCE=SHIPPED, got %s' % ', '.join(bad))
        path = vault.locate_item(a.pack, a.root)
        hold = vault.acquire(os.path.basename(path))
        if hold is None:
            msg = '%s is already being converted' % os.path.basename(path)
            print(json.dumps({'ok': False, 'exit': EXIT_LOCKED, 'error': msg}) if a.json else msg)
            return EXIT_LOCKED
        scale = a.scale
        if scale is None:
            from riftlord.fab import packs
            item, _c, proots = packs.locate(path)
            if not packs.identity(item, proots)['known']:
                got = vault.cached_measure(os.path.basename(path), proots)
                scale = got['scale'] if got else None
        rep = library.convert(path, a.out, jobs=a.jobs, memory_mb=a.memory_mb, scale=scale, foliage_master=a.foliage_master,
                              only=a.only, materials=dict(m.split('=', 1) for m in a.material),
                              events=library.json_events if a.events else None)
        if not vault.is_item(path):
            vault.record_import(path, rep, a.out)          # a loose pack: list it in `vault` from now on
    except (FileNotFoundError, ValueError) as e:
        if a.json:
            print(json.dumps({'ok': False, 'exit': library.EXIT_USAGE, 'error': str(e)}))
        else:
            sys.stderr.write('fabconvert: %s\n' % e)
        return library.EXIT_USAGE
    except (Exception, SystemExit) as e:                 # noqa: BLE001 - the GUI needs an exit code
        if a.json:
            print(json.dumps({'ok': False, 'exit': library.EXIT_CRASH, 'error': '%s: %s' % (type(e).__name__, e),
                              'trace': traceback.format_exc(limit=8)}))
        else:
            traceback.print_exc()
        return library.EXIT_CRASH
    finally:
        if hold is not None:
            hold.close()
    code =library.EXIT_OK if rep['ok'] else library.EXIT_PROBLEMS
    summary = {'ok': rep['ok'], 'exit': code, 'library': rep['library'], 'pak': rep['pak'],
               'counts': rep['counts'], 'sizes': rep['sizes'], 'problems': rep['problems'][:50],
               'problem_count': len(rep['problems']), 'seconds': rep['seconds']}
    if a.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(json.dumps(summary, indent=1, sort_keys=True))
    return code


#: Exit code: another convert of the item holds its lock.
EXIT_LOCKED = 4


def _vault_cmd(a):
    from riftlord.fab import library
    from riftlord.fab import vault
    try:
        if a.cmd == 'vault':
            roots, rows = vault.items(a.root, a.out, vault.vault_events if a.events else None)
            out = {'ok': bool(roots['found']), 'roots': roots, 'items': rows,
                   'library': a.out or library.default_library(),
                   'settings': {'memory_mb': library.memory_budget_mb(), 'workers': library.default_jobs(),
                                'cpu_share': library.CPU_SHARE, 'worker_mb': library.WORKER_MB,
                                'reserve_mb': library.RESERVE_MB}}
            if not roots['found']:
                out['error'] = 'no Epic vault found; searched: %s' % ', '.join(roots['searched'])
        elif a.cmd == 'measure':
            out = dict(vault.measure(a.item, a.root), ok=True)
        else:
            out = dict(vault.delete(a.item, a.root, a.out), ok=True)
    except FileNotFoundError as e:
        out, code = {'ok': False, 'error': str(e)}, library.EXIT_USAGE
    except RuntimeError as e:
        out, code = {'ok': False, 'error': str(e)}, EXIT_LOCKED
    except (Exception, SystemExit) as e:                 # noqa: BLE001 - the GUI needs an exit code
        out, code = ({'ok': False, 'error': '%s: %s' % (type(e).__name__, e),
                      'trace': traceback.format_exc(limit=8)}, library.EXIT_CRASH)
    else:
        code = library.EXIT_OK if out['ok'] else library.EXIT_USAGE
    out['exit'] = code
    print(json.dumps(out, sort_keys=True) if a.json else json.dumps(out, indent=1, sort_keys=True))
    return code


if __name__ == '__main__':
    sys.exit(main())
