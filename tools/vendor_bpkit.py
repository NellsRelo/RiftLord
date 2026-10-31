"""Refresh Rift Lord's own copy of bpkit (vendor/bpkit) from a bpkit folder, on purpose only.

    python3 tools/vendor_bpkit.py --from PATH [--dry-run]   # copy PATH's bpkit; print what changed
    python3 tools/vendor_bpkit.py --check                   # the copy against VENDORED.md

Rift Lord uses only vendor/bpkit (riftlord.core.bpkitlink); it never follows another bpkit
folder by itself. The copy holds bpkit's files less SKIP; vendor/VENDORED.md records where it
came from, when, and DIGEST of the copy.
"""

import argparse
import difflib
import fnmatch
import hashlib
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
#: The vendored package folder, and the note beside it.
VENDOR = os.path.join(ROOT, 'vendor', 'bpkit')
NOTE = os.path.join(ROOT, 'vendor', 'VENDORED.md')
#: Names never copied: caches, compiled modules, bpkit's example mods.
SKIP = ('__pycache__', '*.pyc', 'examples', 'cache')


def files(folder):
    """{relative path: sha256} of `folder`'s files less SKIP."""
    out = {}
    for base, dirs, names in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not any(fnmatch.fnmatch(d, p) for p in SKIP))
        for n in sorted(names):
            if any(fnmatch.fnmatch(n, p) for p in SKIP):
                continue
            path = os.path.join(base, n)
            with open(path, 'rb') as fh:
                out[os.path.relpath(path, folder).replace(os.sep, '/')] = \
                    hashlib.sha256(fh.read()).hexdigest()
    return out


def digest(found):
    """DIGEST: sha256 of the sorted `relative path sha256` lines of files()."""
    return hashlib.sha256(''.join('%s %s\n' % kv for kv in sorted(found.items()))
                          .encode()).hexdigest()


def recorded():
    """The digest VENDORED.md records, or None."""
    try:
        with open(NOTE) as fh:
            for line in fh:
                if line.startswith('- Digest: `'):
                    return line.split('`')[1]
    except OSError:
        pass
    return None


def changes(old, new, src):
    """Lines saying what a copy from `src` changes: each file added, removed or changed
    (with its changed line count)."""
    out = []
    for rel in sorted(set(old) | set(new)):
        if rel not in new:
            out.append('removed  %s' % rel)
        elif rel not in old:
            out.append('added    %s' % rel)
        elif old[rel] != new[rel]:
            with open(os.path.join(VENDOR, rel), errors='replace') as a, \
                    open(os.path.join(src, rel), errors='replace') as b:
                diff = [l for l in difflib.unified_diff(a.readlines(), b.readlines(), n=0)
                        if l[:1] in '+-' and l[:3] not in ('+++', '---')]
            out.append('changed  %s  (+%d -%d lines)' % (
                rel, sum(l[0] == '+' for l in diff), sum(l[0] == '-' for l in diff)))
    return out


def write_note(src, found):
    with open(NOTE, 'w') as fh:
        fh.write('# Vendored bpkit\n\n'
                 '`vendor/bpkit` is Rift Lord\'s own frozen copy of bpkit, the Blueprint toolkit. '
                 'Rift Lord\nuses only this copy (`riftlord.core.bpkitlink`), in source runs and '
                 'in the built app.\nIt changes only through `tools/vendor_bpkit.py --from PATH`; '
                 'never edit it by hand.\n\n'
                 '- Source: `%s`\n- Copied: %s\n- Files: %d (less %s)\n- Digest: `%s`\n'
                 % (src, time.strftime('%Y-%m-%d %H:%M'), len(found), ', '.join(SKIP),
                    digest(found)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--from', dest='src', metavar='PATH', help='the bpkit folder to copy')
    ap.add_argument('--dry-run', action='store_true', help='with --from: print, copy nothing')
    ap.add_argument('--check', action='store_true', help='the copy against VENDORED.md')
    args = ap.parse_args(argv)
    if args.check:
        got, want = digest(files(VENDOR)), recorded()
        print('vendor/bpkit %s VENDORED.md (%s)' % ('matches' if got == want else
                                                  'DIFFERS from', got[:16]))
        return 0 if got == want else 1
    if not args.src:
        ap.error('--from PATH or --check')
    src = os.path.abspath(args.src)
    if not os.path.isfile(os.path.join(src, '__init__.py')):
        raise SystemExit('no bpkit package at %s' % src)
    old, new = files(VENDOR) if os.path.isdir(VENDOR) else {}, files(src)
    lines = changes(old, new, src)
    print('\n'.join(lines) if lines else 'no change')
    if args.dry_run or not lines:
        return 0
    shutil.rmtree(VENDOR, ignore_errors=True)
    shutil.copytree(src, VENDOR, ignore=shutil.ignore_patterns(*SKIP))
    write_note(src, new)
    print('vendor/bpkit: %d files from %s, digest %s' % (len(new), src, digest(new)[:16]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
