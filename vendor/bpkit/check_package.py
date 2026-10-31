"""Gate A for bpkit.package: every package in the game must round-trip exactly.

Reads each cooked package into the model and writes it back with every offset,
count and size recomputed from the model. Any byte of difference is a field the
model does not yet understand. Also checks the things the writer derives
against what the file said, so a coincidental match cannot hide a wrong rule.

    python3 bpkit/check_package.py            # every package and level in the game
    python3 bpkit/check_package.py --limit 500
"""

import argparse
import collections
import multiprocessing as mp
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'UE Assets'))

import gamefiles                                   # noqa: E402
from bpkit.package import Package                  # noqa: E402

#: Offsets the writer derives, compared against what the file stored.
DERIVED = ('total_header_size', 'name_offset', 'import_offset', 'export_offset',
           'depends_offset', 'asset_registry_offset', 'world_tile_offset',
           'preload_count', 'preload_offset', 'bulk_start')


def check(stem):
    """(verdict, detail) for one package stem (path without extension)."""
    os.chdir(ROOT)
    try:
        ua = gamefiles.read(stem + '.uasset')
    except KeyError:
        try:
            ua = gamefiles.read(stem + '.umap')
        except KeyError:
            return 'skip', 'no .uasset or .umap'
    try:
        ux = gamefiles.read(stem + '.uexp')
    except KeyError:
        ux = b''
    try:
        p = Package.read(ua, ux)
    except Exception as ex:
        return 'refused', repr(ex)[:100]
    o = p.layout()
    read = p.summary.read_offsets
    wrong = [k for k in DERIVED if o[k] != read[k]]
    head, body = p.write()
    if head != ua or (ux and body != ux):
        at = next((i for i, (a, b) in enumerate(zip(head, ua)) if a != b), min(len(head), len(ua)))
        where = 'header byte %d of %d/%d' % (at, len(head), len(ua)) if head != ua else 'body'
        return 'differs', '%s; derived offsets wrong: %s' % (where, ','.join(wrong) or 'none')
    if wrong:
        return 'differs', 'bytes match but derived %s' % ','.join(wrong)
    return 'ok', None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--limit', type=int)
    args = ap.parse_args()
    os.chdir(ROOT)
    stems = sorted(k.rsplit('.', 1)[0] for k in gamefiles.index()
                   if k.endswith(('.uasset', '.umap')))
    if args.limit:
        stems = stems[:args.limit]
    verdicts = collections.Counter()
    samples = collections.defaultdict(list)
    with mp.Pool(12) as pool:
        for stem, (verdict, detail) in zip(stems, pool.imap(check, stems, chunksize=64)):
            verdicts[verdict] += 1
            if detail and len(samples[(verdict, detail[:60])]) < 2:
                samples[(verdict, detail[:60])].append((stem, detail))
    print(dict(verdicts))
    for (verdict, _), items in sorted(samples.items())[:25]:
        for stem, detail in items:
            print('  %-8s %s\n           %s' % (verdict, stem.split('/Content/')[-1], detail))
    return 0 if verdicts['ok'] == len(stems) - verdicts['skip'] else 1


if __name__ == '__main__':
    sys.exit(main())
