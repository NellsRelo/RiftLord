"""Read any file out of the installed game, from whichever pak chunk holds it.

`mapdata._fetch` answers for a known `/Game/...` package. This answers for
anything: config files, levels, blueprints, the lot. It builds one index across
every pak chunk and caches it, because scanning sixty chunks takes long enough to
be worth doing once.

    python3 gamefiles.py --find Scenario_01 --ext .umap
    python3 gamefiles.py --cat ../../../OMD/Config/DefaultEngine.ini

The index maps a pak-internal path to the chunk that holds it. When two chunks
hold the same path the later one wins, which matches how the game mounts them.
"""

import argparse
import glob
import json
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.core import gamepath  # noqa: E402
from riftlord.formats.ue.pak import Pak  # noqa: E402

#: Where a rebuilt index is kept, so the scan happens once per install.
CACHE = 'gamefiles-index.json'

_index = None
_open = {}


def _root():
    got = gamepath.paks()
    return got if isinstance(got, str) else got[0]


def index(refresh=False):
    """{pak-internal path: chunk filename}, built once and cached."""
    global _index
    if _index is not None and not refresh:
        return _index
    root = _root()
    cache = os.path.join(gamepath.scratch('gamefiles', beside=gamepath.CODE_ROOT), CACHE)
    if os.path.exists(cache) and not refresh:
        with open(cache) as fh:
            _index = json.load(fh)
        return _index
    out = {}
    for path in sorted(glob.glob(os.path.join(root, '*.pak'))):
        try:
            p = Pak(path)
        except Exception:                            # noqa: BLE001
            continue
        chunk = os.path.basename(path)
        for name in p.files:
            out[name] = chunk
    try:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, 'w') as fh:
            json.dump(out, fh)
    except OSError:
        pass
    _index = out
    return out


#: Guards the reader cache: without it two threads opening the same chunk at once each
#: build a `Pak` and one is thrown away mid-use.
_OPENING = threading.Lock()


def read(name):
    """The bytes of one file, by its pak-internal path."""
    chunk = index()[name]
    with _OPENING:
        if chunk not in _open:
            _open[chunk] = Pak(os.path.join(_root(), chunk))
        pak = _open[chunk]
    return pak.read_file(name)


def find(*bits, ext=None):
    """Every path containing all of `bits`, optionally with an extension."""
    out = []
    for name in index():
        low = name.lower()
        if ext and not low.endswith(ext.lower()):
            continue
        if all(b.lower() in low for b in bits):
            out.append(name)
    return sorted(out)


def package(game_path):
    """(uasset, uexp) for a `/Game/...` package, or a pak path without extension."""
    stem = ('../../../OMD/Content' + game_path[len('/Game'):]
            if game_path.startswith('/Game/') else game_path)
    #: some packages are stored with a doubled slash before the file name (Ice maps)
    if stem + '.uasset' not in index() and stem + '.umap' not in index():
        head, _, tail = stem.rpartition('/')
        if head + '//' + tail + '.umap' in index() or head + '//' + tail + '.uasset' in index():
            stem = head + '//' + tail
    body = b''
    try:
        body = read(stem + '.uexp')
    except KeyError:
        pass
    head = read(stem + '.uasset') if stem + '.uasset' in index() \
        else read(stem + '.umap')
    return head, body


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--find', nargs='*', default=None, metavar='TEXT')
    ap.add_argument('--ext')
    ap.add_argument('--cat', metavar='PATH')
    ap.add_argument('--out', metavar='FILE')
    ap.add_argument('--refresh', action='store_true')
    args = ap.parse_args(argv)

    got = index(refresh=args.refresh)
    if args.find is not None:
        hits = find(*args.find, ext=args.ext)
        for h in hits[:200]:
            print('%-12s %s' % (got[h], h))
        print('%d match(es) of %d files' % (len(hits), len(got)))
        return 0
    if args.cat:
        data = read(args.cat)
        if args.out:
            with open(args.out, 'wb') as fh:
                fh.write(data)
            print('wrote %s (%d bytes)' % (args.out, len(data)))
        else:
            sys.stdout.write(data.decode('utf-8', 'replace'))
        return 0
    print('%d files across %d pak chunks' % (len(got), len(set(got.values()))))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
