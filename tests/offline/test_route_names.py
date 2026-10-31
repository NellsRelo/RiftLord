"""A ground route the build cannot join names the object at fault, as the Map Editor labels
it: ground_names labels an added spawner (at its floor), an added path point and a level
point, and navsurfextend's reasons name a labelled point beside its coordinates.

    python3 tests/offline/test_route_names.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from riftlord.build.navigation import navsurfextend  # noqa: E402
from riftlord.mapeditor import planactors  # noqa: E402

checks = []


def check(name, ok, detail=''):
    checks.append(bool(ok))
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, (': %s' % (detail,)) if detail else ''))


def main():
    level = planactors.level_actors('Scenario_02')
    plan = {'spawners': [{'id': 4, 'at': [-2956, -4870, 407], 'start': 1}],
            'points': [{'id': 1, 'at': [-2000, -4800, 308],
                        'next': [{'to': 'rift', 'weight': 1.0}]}]}
    names = planactors.ground_names(plan, level)
    chain = planactors.ground_chains(plan, level)[0]
    labels = [names.get(tuple(p)) for p in chain[:2]]
    check('the chain\'s spawner and point carry their editor labels',
          labels == ['Spawner 4', 'Point 1'], labels)
    level_point = next(iter(level['points']))
    check('a level point is labelled by its name',
          level_point in names.values(), level_point)
    navsurfextend._NAMES.update({navsurfextend._key(chain[0]): [(chain[0][2], 'Spawner 4'),
                                                                 (2000.0, 'Point 9')]})
    try:
        #: the build drops a point to its floor before joining it, so its height differs
        got = navsurfextend._fmt((-2956.3, -4870.2, 158.0))
    finally:
        navsurfextend._NAMES.clear()
    check('a reason names the point at that plan position, the nearest in height',
          got == 'Spawner 4 at (-2956, -4870, 158)', got)
    check('an unlabelled point keeps its coordinates',
          navsurfextend._fmt((1, 2, 3)) == '(1, 2, 3)', navsurfextend._fmt((1, 2, 3)))
    print('%d checks, %d failed' % (len(checks), checks.count(False)))
    return 0 if all(checks) else 1


if __name__ == '__main__':
    raise SystemExit(main())
