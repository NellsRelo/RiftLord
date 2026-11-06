"""Offline checks on the kit classes' trap grids in actor space (kit-geometry.json, served by
/api/kit; marker: datafix): a grid attached to a turned or lifted part is placed through
its attachment chain, a class whose name differs in case from its asset finds its grid and
parts, the Blueprint classes the levels place outside the kit catalogue are measured, and
the data file is what `kitgeom --build` writes.

    python3 test_kitgeom_grids.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from riftlord.core import gamepath  # noqa: E402
from riftlord.mapeditor import kitgeom  # noqa: E402
from riftlord.mapeditor import leveldata  # noqa: E402

#: Grid centres in actor space read from shipped placements (kit placement census,
#: docs/research/kit-placement-census.md: each level's own OMDTrapGridBoxComponent exports
#: walked up their AttachParent chain), Scenario_18's Castle sublevel: Trapezoid_Landing
#: export 10586 (36 grids), Stairs_MultiTiered export 3385 (8 grids).
PLACED = {
    'Trapezoid_Landing_C': (36, [(-2637.9, -150.0, -1335.5), (-2550.0, -150.0, -360.0),
                                 (-2504.4, 138.2, -1130.0)]),
    'Stairs_MultiTiered_C': (8, [(-1055.0, -300.0, -655.0), (-945.0, -1130.0, 145.0),
                                 (-945.0, 370.0, -205.0)]),
}
#: Placement tolerance of a grid centre (uu).
NEAR = 0.5
#: A class whose name differs in case from its asset (Wall_RB_MT_v3_1x4_angle.uasset).
ANGLE = 'Wall_RB_MT_v3_1x4_Angle_C'

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


for cls, (count, centres) in PLACED.items():
    grids = leveldata.kit_grids(cls)
    origins = [g['origin'] for g in grids]
    missing = [c for c in centres
               if not any(all(abs(o[k] - c[k]) < NEAR for k in range(3)) for o in origins)]
    check('%s: %d grids, each where the shipped placement has it' % (cls, count),
          len(grids) == count and not missing, (len(grids), missing))

angle = leveldata.kit_parts(ANGLE)
check('%s finds its grid and its parts' % ANGLE,
      len(angle['grids']) == 1 and len(angle['parts']) > 0,
      (len(angle['grids']), len(angle['parts'])))

with open(gamepath.data('kit-geometry.json')) as fh:
    data = json.load(fh)
check('a grid-only Blueprint the levels place outside the catalogue is measured',
      'Scenario_03_Ground1' in data['pieces'] and 'BP_OMDTrapGrid_Blank' in data['pieces'], '')
for name in ('Trapezoid_Landing', 'Stairs_MultiTiered', 'Floor_4x4'):
    got = kitgeom.grids(data['pieces'][name]['path'].replace('/Game/', '../../../OMD/Content/'))
    check('kit-geometry.json holds what kitgeom.grids reads for %s' % name,
          json.loads(json.dumps(got)) == data['pieces'][name]['grids'], '')

bad = [c for c in checks if not c[1]]
for name, ok, detail in checks:
    print('%s %s%s' % ('ok  ' if ok else 'FAIL', name, '' if ok else ': %s' % (detail,)))
print('%d of %d checks passed' % (len(checks) - len(bad), len(checks)))
sys.exit(1 if bad else 0)
