"""Copy Rift Lord out of OMD2-Converter, and track what changes there afterwards.

    python3 tools/oldtree.py copy [--force]        # first copy; writes .sync/manifest.json
    python3 tools/oldtree.py status [--json]       # what changed in either tree since the copy
    python3 tools/oldtree.py diff FILE             # unified diff, old tree against this tree
    python3 tools/oldtree.py take FILE... [--force]   # bring old-tree changes over
    python3 tools/oldtree.py take --all-clean      # every file changed only in the old tree
    python3 tools/oldtree.py take --merged FILE    # merged by hand: record it as in step
    python3 tools/oldtree.py merge FILE...         # 3-way merge (diff3) for "both changed"
    python3 tools/oldtree.py drop FILE... --why W  # no longer needed here

The old tree is only read. The manifest keeps, per copied file, its hash in the old tree
at copy (or last take) time and its hash here at that moment, so status can tell an
old-tree change from a local edit and from both. It also lists new files in the watched
old-tree folders, and modules the app now reaches that were never copied (tools/depmap.py).
"""

import argparse
import difflib
import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
NEW = os.path.dirname(HERE)
OLD = os.path.join(os.environ.get('RIFTLORD_WIN64') or os.path.dirname(NEW), 'OMD2-Converter')
MANIFEST = os.path.join(NEW, '.sync', 'manifest.json')
#: Each tracked file as last taken from the old tree: the merge base, and the old code
#: as copied (tools/buildcompare.py builds from it).
BASE = os.path.join(NEW, '.sync', 'base')

sys.path.insert(0, HERE)
import depmap                                         # noqa: E402

#: Files copied besides the Python modules depmap classes as needed or dev.
EXTRA_FILES = [
    'workshop/README.md', 'workshop/SAVE-MODEL.md', 'workshop/icon.ico', 'workshop/icon.png',
    'riftlord/README.md', 'riftlord/PLAN-ACTORS.md', 'onslaughtgui/README.md',
    'MS-NRBF Reader/README.md', 'BPKIT.md', 'DECISIONS.md',
    'recastgen/recastgen.cpp', 'thirdparty/physx/pxcook.cpp',
    'tests/ui_smoke/ui_smoke.js', 'tests/ui_smoke/testservers.js',
]
#: Folders copied whole, less EXCLUDE.
EXTRA_TREES = ['workshop/app', 'workshop/launcher', 'workshop/web', 'riftlord/web',
               'onslaughtgui/web', 'webshared']
#: Never copied: build output, dependencies, caches.
EXCLUDE = ['node_modules', 'dist', 'target', '__pycache__', '*.pyc', '.vite']
#: Dev modules depmap reaches that stay behind (the standalone Onslaught Editor's
#: packaging, which ships a product of its own).
SKIP_MODULE_FILES = ['onslaughtgui/build_app.py', 'onslaughtgui/package.py']
#: Old-tree folders watched for new files (top level: '' watches *.py and *.json only).
WATCH = ['', 'workshop', 'riftlord', 'onslaughtgui', 'bpkit', 'UE Assets', 'MS-NRBF Reader',
         'recastgen', 'hudart', 'webshared', 'tests', 'workshop/app', 'workshop/launcher',
         'workshop/web', 'riftlord/web', 'onslaughtgui/web']


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for block in iter(lambda: fh.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def excluded(rel):
    return any(fnmatch.fnmatch(part, pat) for part in rel.split('/') for pat in EXCLUDE)


def tree_files(rel_dir):
    out = []
    for base, dirs, files in os.walk(os.path.join(OLD, rel_dir)):
        dirs[:] = sorted(d for d in dirs if not excluded(d))
        for f in sorted(files):
            rel = os.path.relpath(os.path.join(base, f), OLD).replace(os.sep, '/')
            if not excluded(rel):
                out.append(rel)
    return out


def wanted():
    """Relative paths to copy, with why: 'needed', 'dev', 'data' or 'extra'."""
    m = depmap.build_map(OLD)
    out = {}
    for v in m['modules'].values():
        if v['class'] in ('needed', 'dev') and v['file'] not in SKIP_MODULE_FILES:
            out[v['file'].replace(os.sep, '/')] = v['class']
    for rel in m['hidden'].get('DATA', []) + m['hidden'].get('BAKER_SOURCES', []):
        out.setdefault(rel, 'data')
    for rel in EXTRA_FILES:
        out.setdefault(rel, 'extra')
    for d in EXTRA_TREES:
        for rel in tree_files(d):
            out.setdefault(rel, 'extra')
    return out, m


def load():
    with open(MANIFEST) as fh:
        return json.load(fh)


def save(man):
    os.makedirs(os.path.dirname(MANIFEST), exist_ok=True)
    part = MANIFEST + '.part'
    with open(part, 'w') as fh:
        json.dump(man, fh, indent=1, sort_keys=True)
    os.replace(part, MANIFEST)


def copy_one(rel, base_only=False):
    """Copy `rel` from the old tree into BASE and, unless base_only, into this tree.
    Returns the hash of what was copied."""
    src = os.path.join(OLD, rel)
    for root in ([BASE] if base_only else [BASE, NEW]):
        dst = os.path.join(root, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    return sha(os.path.join(BASE, rel))


def base_path(rel):
    return os.path.join(BASE, rel)


def cmd_copy(args):
    if os.path.exists(MANIFEST) and not args.force:
        raise SystemExit('already copied (%s); use status / take, or --force' % MANIFEST)
    files, m = wanted()
    man = {'old_root': OLD, 'copied_at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'files': {}}
    missing = []
    for rel, why in sorted(files.items()):
        if not os.path.isfile(os.path.join(OLD, rel)):
            missing.append(rel)
            continue
        before = sha(os.path.join(OLD, rel))
        got = copy_one(rel)
        if got != before or sha(os.path.join(OLD, rel)) != before:
            got = copy_one(rel)                       # changed while copying: once more
            before = sha(os.path.join(OLD, rel))
        st = os.stat(os.path.join(OLD, rel))
        man['files'][rel] = {'why': why, 'old': before, 'new': got, 'size': st.st_size,
                             'old_mtime': int(st.st_mtime)}
    man['not_reached'] = sorted(k for k, v in m['modules'].items() if v['class'] == 'not needed')
    save(man)
    n = len(man['files'])
    print('copied %d files (%.1f MB) into %s' % (n, sum(f['size'] for f in man['files'].values())
                                                 / 1e6, NEW))
    if missing:
        print('missing in the old tree: %s' % ', '.join(missing))
    return 0


def restructured():
    """True once tools/restructure.py has moved this tree into the riftlord package."""
    return os.path.isdir(os.path.join(NEW, 'riftlord', 'core'))


def here_path(rel):
    """Where the old-tree file `rel` lives in this tree."""
    if restructured():
        import restructure
        return restructure.dest_of(rel)
    return rel


def as_here(rel, data):
    """Old-tree bytes of `rel` as this tree holds them (rewritten after the move)."""
    if restructured():
        import restructure
        return restructure.transform(rel, data)[1]
    return data


def read(path):
    with open(path, 'rb') as fh:
        return fh.read()


def expected(rel):
    """This tree's `rel` as it would be with no local edit: BASE, rewritten."""
    return as_here(rel, read(base_path(rel)))


def local_edit(rel):
    """True when this tree's file differs from the old tree's last-taken version."""
    n = os.path.join(NEW, here_path(rel))
    return not os.path.isfile(n) or read(n) != expected(rel)


def classify(man):
    rows = {'old changed': [], 'local edit': [], 'both changed': [], 'old deleted': [],
            'local deleted': [], 'new in old tree': [], 'newly reached': []}
    for rel, f in sorted(man['files'].items()):
        o, n = os.path.join(OLD, rel), os.path.join(NEW, here_path(rel))
        if not os.path.isfile(o):
            rows['old deleted'].append(rel)
        elif not os.path.isfile(n):
            rows['local deleted'].append(rel)
        else:
            oc, nc = sha(o) != f['old'], local_edit(rel)
            if oc and nc:
                rows['both changed'].append(rel)
            elif oc:
                rows['old changed'].append(rel)
            elif nc:
                rows['local edit'].append(rel)
    known = set(man['files']) | set(man.get('dropped', {}))
    since = time.mktime(time.strptime(man['copied_at'], '%Y-%m-%dT%H:%M:%S'))
    for d in WATCH:
        base = os.path.join(OLD, d)
        if not os.path.isdir(base):
            continue
        if d in EXTRA_TREES or d == 'tests':
            names = tree_files(d)
        else:
            names = [(d + '/' + f if d else f) for f in os.listdir(base)
                     if os.path.isfile(os.path.join(base, f)) and not excluded(f)
                     and (d or f.endswith(('.py', '.json')))]
        for rel in names:
            if rel not in known and rel not in rows['new in old tree'] and \
                    os.path.getmtime(os.path.join(OLD, rel)) > since:
                rows['new in old tree'].append(rel)
    m = depmap.build_map(OLD)
    for v in m['modules'].values():
        rel = v['file'].replace(os.sep, '/')
        if v['class'] in ('needed', 'dev') and rel not in known and \
                rel not in SKIP_MODULE_FILES:
            rows['newly reached'].append('%s (%s: %s)' % (rel, v['class'], ' > '.join(v['chain'])))
    return rows


def cmd_status(args):
    man = load()
    rows = classify(man)
    if args.json:
        print(json.dumps(rows, indent=1))
        return 0
    print('copied %s from %s; %d files tracked' % (man['copied_at'], man['old_root'],
                                                   len(man['files'])))
    for k, v in rows.items():
        print('\n%s (%d)%s' % (k, len(v), '' if v else ': none'))
        for rel in v:
            print('  ' + rel + ('' if here_path(rel) in (rel, None) or ' ' in rel and '(' in rel
                                else '  -> ' + here_path(rel)))
    return 0


def cmd_diff(args):
    for rel in args.files:
        a = as_here(rel, read(os.path.join(OLD, rel))).decode('utf-8', 'replace').splitlines(True)
        here = here_path(rel)
        b = read(os.path.join(NEW, here)).decode('utf-8', 'replace').splitlines(True)
        sys.stdout.writelines(difflib.unified_diff(b, a, 'here/' + here, 'old-tree/' + rel))
    return 0


def _record(man, rel):
    """Mark `rel` in step with the old tree: BASE gets its current old-tree copy."""
    copy_one(rel, base_only=True)
    o = os.path.join(OLD, rel)
    st = os.stat(o)
    man['files'][rel] = dict(man['files'].get(rel) or {'why': 'taken'}, old=sha(o),
                             size=st.st_size, old_mtime=int(st.st_mtime))
    man['files'][rel].pop('new', None)


def cmd_take(args):
    man = load()
    files = list(args.files)
    if args.all_clean:
        files += classify(man)['old changed']
    done, refused = [], []
    for rel in files:
        rel = rel.replace(os.sep, '/')
        if not os.path.isfile(os.path.join(OLD, rel)):
            refused.append(rel + ' (not in the old tree)')
            continue
        here = here_path(rel)
        if here is None:
            refused.append(rel + ' (no place in tools/restructure.py GROUPS)')
            continue
        if not args.merged:
            if rel in man['files'] and local_edit(rel) and not args.force:
                refused.append(rel)
                continue
            dst = os.path.join(NEW, here)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, 'wb') as fh:
                fh.write(as_here(rel, read(os.path.join(OLD, rel))))
        _record(man, rel)
        done.append(rel)
    save(man)
    print('%s %d: %s' % ('marked merged' if args.merged else 'took', len(done),
                         ', '.join(done) or '-'))
    if refused:
        print('refused (edited here: `merge FILE`, or resolve by hand and `take --merged FILE`; '
              '`--force` overwrites the local edit): %s' % ', '.join(refused))
    return 0 if not refused else 1


def cmd_merge(args):
    """Three-way merge (diff3 -m) of each file: this tree's version, BASE and the old
    tree's, the last two rewritten as this tree holds them. A clean merge is recorded;
    conflicts are left marked in the file."""
    import tempfile
    man = load()
    clean, conflicted = [], []
    with tempfile.TemporaryDirectory(dir=NEW + '/.sync') as tmp:
        for rel in args.files:
            n = os.path.join(NEW, here_path(rel))
            b, o = os.path.join(tmp, 'base'), os.path.join(tmp, 'old')
            with open(b, 'wb') as fh:
                fh.write(expected(rel))
            with open(o, 'wb') as fh:
                fh.write(as_here(rel, read(os.path.join(OLD, rel))))
            got = subprocess.run(['diff3', '-m', '-L', 'here', '-L', 'base', '-L', 'old-tree',
                                  n, b, o], capture_output=True)
            if got.returncode not in (0, 1):
                raise SystemExit('diff3 failed on %s: %s' % (rel, got.stderr.decode()[-300:]))
            with open(n, 'wb') as fh:
                fh.write(got.stdout)
            if got.returncode == 0:
                _record(man, rel)
                clean.append(rel)
            else:
                conflicted.append(rel)
    save(man)
    print('merged cleanly: %s' % (', '.join(clean) or '-'))
    if conflicted:
        print('conflicts marked (resolve, then `take --merged FILE`): %s' % ', '.join(conflicted))
    return 0 if not conflicted else 1


#: The edits phase (a) made to files that later changed in the old tree too, as
#: (file, edited text, copy-time text): what init-base reverts to recover the copy.
PHASE_A_EDITS = [
    ('riftlord/leveldata.py', "    else gamepath.beside_code(ROOT, 'riftlord-plans'))",
     "    else os.path.join(ROOT, 'riftlord-plans'))"),
    ('onslaughtgui/gamedata.py',
     "UPLOAD_DIR = os.path.join(gamepath.beside_code(ROOT, 'cache'), 'opened-paks')",
     "UPLOAD_DIR = os.path.join(ROOT, 'cache', 'opened-paks')"),
]


def cmd_init_base(args):
    """Write BASE for a copy made before BASE existed: each file from this tree when
    untouched, else from the old tree when unchanged there, else this tree's file with
    PHASE_A_EDITS reverted; every one checked against the manifest's copy-time hash."""
    man = load()
    bad = []
    reverts = {f: (a, b) for f, a, b in PHASE_A_EDITS}
    for rel, f in man['files'].items():
        here, there, dst = os.path.join(NEW, rel), os.path.join(OLD, rel), base_path(rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.isfile(here) and sha(here) == f['old']:
            shutil.copy2(here, dst)
        elif os.path.isfile(there) and sha(there) == f['old']:
            shutil.copy2(there, dst)
        elif rel in reverts:
            text = open(here, encoding='utf-8').read().replace(*reverts[rel])
            with open(dst, 'w', encoding='utf-8') as fh:
                fh.write(text)
            shutil.copystat(here, dst)
        if not os.path.isfile(dst) or sha(dst) != f['old']:
            bad.append(rel)
    print('base: %d files, %d not recovered%s' % (len(man['files']), len(bad),
                                                 (': ' + ', '.join(bad)) if bad else ''))
    return 0 if not bad else 1


def cmd_drop(args):
    """Remove files this tree no longer needs; status ignores their old-tree changes, and
    lists them under 'newly reached' if the app reaches them again."""
    man = load()
    dropped = man.setdefault('dropped', {})
    for rel in args.files:
        f = man['files'].pop(rel, None)
        if f is None:
            raise SystemExit('%s is not tracked' % rel)
        dropped[rel] = dict(f, why=args.why, dropped_at=time.strftime('%Y-%m-%dT%H:%M:%S'))
        path = os.path.join(NEW, here_path(rel) or rel)
        if os.path.exists(path):
            os.remove(path)
    save(man)
    print('dropped %d: %s' % (len(args.files), ', '.join(args.files)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('copy')
    c.add_argument('--force', action='store_true')
    s = sub.add_parser('status')
    s.add_argument('--json', action='store_true')
    d = sub.add_parser('diff')
    d.add_argument('files', nargs='+')
    t = sub.add_parser('take')
    t.add_argument('files', nargs='*')
    t.add_argument('--all-clean', action='store_true')
    t.add_argument('--force', action='store_true', help='overwrite a local edit')
    t.add_argument('--merged', action='store_true',
                   help='the old-tree change is merged here by hand: record both hashes')
    g = sub.add_parser('merge')
    g.add_argument('files', nargs='+')
    sub.add_parser('init-base')
    r = sub.add_parser('drop')
    r.add_argument('files', nargs='+')
    r.add_argument('--why', required=True)
    args = ap.parse_args(argv)
    return {'copy': cmd_copy, 'status': cmd_status, 'diff': cmd_diff, 'take': cmd_take,
            'merge': cmd_merge, 'init-base': cmd_init_base, 'drop': cmd_drop}[args.cmd](args)


if __name__ == '__main__':
    raise SystemExit(main())
