"""Offline checks on dropping a level's precomputed visibility (geomedits).

    python3 test_precomputed_visibility.py

- Every shipped persistent level's `FPrecomputedVisibilityHandler` reads where
  `ULevel::Serialize` puts it and ends just before the empty
  `PrecomputedVolumeDistanceField` (VOLUME_FIELD bytes), so the reader finds it on any host.
- On The Basement (Scenario_16): `forget_precomputed_visibility` leaves an empty handler,
  the rest of the Level export (properties, actor list, the distance field) unchanged, and
  the package reads back; a second call takes nothing out.
- `visibility_is_stale`: a plan that removes or moves shipped pieces, or a level from
  scratch, drops it; a plan that only adds pieces, or does nothing, keeps it.

Reads the game's packages only. Exit 0 when every check passes.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from riftlord.build import levelbuild  # noqa: E402
from riftlord.core import gamefiles  # noqa: E402
from riftlord.mapeditor import geomedits  # noqa: E402
from riftlord.mapeditor import leveldata  # noqa: E402

LEVEL = 'Scenario_16'
#: Bytes of an empty `FPrecomputedVolumeDistanceField`: float VolumeMaxDistance, FBox
#: VolumeBox (two FVectors and a byte), int32 VolumeSizeX/Y/Z, TArray<FColor> count.
VOLUME_FIELD = 4 + 25 + 12 + 4

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))
    print('  %s  %s%s' % ('PASS' if ok else 'FAIL', name, (': ' + str(detail)) if detail else ''))


def shipped(level):
    """A Builder around the shipped persistent level, or None when the game has none."""
    want = '/%s.umap' % level
    hits = [h for h in gamefiles.find(want, ext='.umap') if h.endswith(want)]
    if not hits:
        return None
    base = hits[0][:-len('.umap')]
    return levelbuild.Builder.around(gamefiles.read(base + '.umap'),
                                     gamefiles.read(base + '.uexp'), name=level)


def run():
    read, wrong = 0, []
    for row in leveldata.levels():
        builder = shipped(row['name'])
        if builder is None:
            continue
        try:
            li, _start, end = geomedits.visibility_span(builder)
            if len(builder.pkg.exports[li]['data']) - end != VOLUME_FIELD:
                wrong.append(row['name'])
        except ValueError as exc:
            wrong.append('%s (%s)' % (row['name'], exc))
        read += 1
    check('every shipped level\'s visibility handler reads (%d levels)' % read,
          read >= 30 and not wrong, wrong)

    builder = shipped(LEVEL)
    li, start, end = geomedits.visibility_span(builder)
    before = bytes(builder.pkg.exports[li]['data'])
    actors = builder.actor_list()
    check('The Basement has precomputed visibility', end - start > 100000, end - start)
    taken = geomedits.forget_precomputed_visibility(builder)
    check('it is dropped', taken == end - start - len(geomedits.EMPTY_VISIBILITY), taken)
    after = bytes(builder.pkg.exports[li]['data'])
    check('only the handler changed', after[:start] == before[:start]
          and after[start + len(geomedits.EMPTY_VISIBILITY):] == before[end:])
    check('the export size follows', builder.pkg.exports[li]['size'] == len(after))
    ua, ux = builder.write()
    again = levelbuild.Builder.around(ua, ux, name=LEVEL)
    li2, start2, end2 = geomedits.visibility_span(again)
    check('the written level reads back with an empty handler',
          bytes(again.pkg.exports[li2]['data'][start2:end2]) == geomedits.EMPTY_VISIBILITY)
    check('the actor list is kept', again.actor_list() == actors)
    check('a second drop takes nothing', geomedits.forget_precomputed_visibility(again) == 0)

    check('removed pieces make it stale', geomedits.visibility_is_stale({'removed': [3]}))
    check('moved pieces make it stale', geomedits.visibility_is_stale(
        {'edits': [{'id': 3, 'matrix': [1.0] * 16}]}))
    check('a level from scratch makes it stale', geomedits.visibility_is_stale(
        {'scratch': {'base': 'Scenario_01'}}))
    check('added pieces alone keep it', not geomedits.visibility_is_stale(
        {'added': [{'kind': 'mesh', 'name': 'Wall_1_3_V2_RB'}], 'removed': [], 'edits': []}))
    check('an empty plan keeps it', not geomedits.visibility_is_stale({}))
    check('no plan keeps it', not geomedits.visibility_is_stale(None))

    bad = [c for c in checks if not c[1]]
    print('%d of %d checks pass' % (len(checks) - len(bad), len(checks)))
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(run())
