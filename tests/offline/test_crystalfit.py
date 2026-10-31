"""Offline checks on the Crystal's hitbox fit (riftlord/mapeditor/crystalfit.py) on made-up
floors: the mode's box fits a large floor, shrinks to a smaller platform, stops at the foot art
and warns when the platform is smaller still, a user's HitX / HitY wins, and a Crystal standing
above the floor under it is warned about.

    python3 test_crystalfit.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from riftlord.mapeditor import crystal  # noqa: E402
from riftlord.mapeditor import crystalfit as F  # noqa: E402

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


def rect(x0, y0, x1, y1, z):
    """A floor polygon, counter-clockwise."""
    return [(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)]


def at(x, y, z, yaw=0.0):
    import math
    c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    return [c, s, 0.0, 0.0, -s, c, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, x, y, z, 1.0]


HALF = list(F.DEFAULT_HALF)

# 1. a large floor: the mode's box fits, nothing to write, no warning
big = F.Floor([rect(-3000, -3000, 3000, 3000, 0.0)])
r = F.fit(big, at(0, 0, 0), HALF)
check('a large floor fits the mode\'s box', r['half'] == HALF[:2] and not r['tooBig'], r)
check('nothing is written for it', F.to_write(r, {}, HALF) == {})
drop, fz = F.floor_drop(big, at(0, 0, 0), HALF)
check('a Crystal on the floor needs no HitDrop', drop == 0.0, (drop, fz))
check('no warning', F.warnings(r, drop, {}) == [], F.warnings(r, drop, {}))

# 2. a platform 1000 x 3000 uu (half 500 x 1500), a drop round it: X shrinks, Y keeps
plat = F.Floor([rect(-500, -1500, 500, 1500, 0.0), rect(-3000, -3000, 3000, -1500, -400.0),
                rect(-3000, 1500, 3000, 3000, -400.0), rect(-3000, -1500, -500, 1500, -400.0),
                rect(500, -1500, 3000, 1500, -400.0)])
r = F.fit(plat, at(0, 0, 0), HALF)
want_x = 500.0 - F.STANDOFF
check('a narrower platform shrinks HitX to fit it (%g)' % want_x,
      abs(r['half'][0] - want_x) <= F.STEP and r['half'][0] <= want_x, r['half'])
check('and keeps HitY', r['half'][1] == HALF[1], r['half'])
check('the side that stood past the floor is named', r['short']['+x'] > 0 and
      r['short']['-x'] > 0 and r['short']['+y'] == 0, r['short'])
write = F.to_write(r, {}, HALF)
check('the build writes HitX only', set(write) == {'HitX'}, write)
lines = F.warnings(r, 0.0, {})
check('the warning names the sides and the HitX / HitY that fit',
      len(lines) == 1 and 'front (+X)' in lines[0] and 'HitX %g' % r['half'][0] in lines[0],
      lines)

# 3. turned 90 degrees, the same platform shrinks the Crystal's Y instead
r90 = F.fit(plat, at(0, 0, 0, 90.0), HALF)
check('at 90 degrees the fit follows the actor\'s axes', r90['half'][0] == HALF[0] and
      r90['half'][1] < HALF[1], r90['half'])

# 4. a platform smaller than the foot art: floored there, too big
tiny = F.Floor([rect(-300, -300, 300, 300, 0.0)])
r = F.fit(tiny, at(0, 0, 0), HALF)
check('a platform smaller than the foot stops at the foot art', r['half'] == list(F.FOOT_ART_HALF),
      r['half'])
check('and the Crystal is too big for it', set(r['tooBig']) == set(F.SIDES), r['tooBig'])
lines = F.warnings(r, 0.0, {})
check('with a too-big warning', any('too big for its platform' in l for l in lines), lines)

# 5. a user's HitX wins: not written by the fit, and a value past the floor is warned about
item = {'kind': 'crystal', 'matrix': at(0, 0, 0), 'crystal': {'hitX': 480.0}}
user = F.user_half(item)
r = F.fit(plat, item['matrix'], HALF)
check('the user\'s HitX is theirs', user == {'HitX': 480.0}, user)
check('the fit writes nothing on the user\'s axis', 'HitX' not in F.to_write(r, user, HALF))
lines = F.warnings(r, 0.0, user)
check('a user value past the floor is warned about, with the one that fits',
      any('HitX 480 (set on the piece)' in l for l in lines), lines)
piece = crystal.clean_piece({'crystal': {'hitX': 480, 'hitY': ''}}, at(0, 0, 0))
check('the save keeps a user HitX and drops a blank HitY',
      piece['crystal'].get('hitX') == 480.0 and 'hitY' not in piece['crystal'], piece['crystal'])
try:
    crystal.clean_piece({'crystal': {'hitY': 100}}, at(0, 0, 0))
    check('a HitY under the foot art is refused', False)
except ValueError:
    check('a HitY under the foot art is refused', True)

# 6. a Crystal on a raised rift base, its root 56 uu over the floor (North Wings): HitDrop 56
raised = at(0, 0, 56.0)
drop, fz = F.floor_drop(big, raised, HALF)
check('a root 56 uu over the floor finds HitDrop 56', drop == 56.0 and fz == 0.0, (drop, fz))
r, found, _fz, used = F.resolve(big, raised, HALF, {})
check('the build writes it', F.to_write(r, {}, HALF, found) == {'HitDrop': 56.0},
      F.to_write(r, {}, HALF, found))
check('with a note, and no warning', F.warnings(r, found, {}) == [] and
      'HitDrop 56' in F.notes(found, fz, 56.0, {})[0])
check('a floor on the root writes no HitDrop', 'HitDrop' not in F.to_write(
    F.fit(big, at(0, 0, 0), HALF), {}, HALF, F.floor_drop(big, at(0, 0, 0), HALF)[0]))
# a raised root and a narrow platform: the fit measures from the dropped bottom
step = F.Floor([rect(-500, -3000, 500, 3000, 0.0), rect(-3000, -3000, -500, 3000, -300.0),
                rect(500, -3000, 3000, 3000, -300.0)])
r, found, _fz, used = F.resolve(step, at(0, 0, 150.0), HALF, {})
check('a root 150 over a platform takes HitDrop 150 and fits its width',
      found == 150.0 and r['half'][0] <= 500.0 - F.STANDOFF, (found, r['half']))

# 7. the user's HitDrop wins, 0 too; past the limit it is warned about
user = F.user_half({'crystal': {'hitDrop': 0}})
check('a user HitDrop of 0 is theirs', user == {'HitDrop': 0.0}, user)
r, found, _fz, used = F.resolve(big, raised, HALF, user)
check('and the build writes no HitDrop over it', 'HitDrop' not in F.to_write(r, user, HALF, found)
      and used == 0.0)
lines = F.warnings(r, found, {'HitDrop': 500.0})
check('a user HitDrop past %g is warned about' % F.DROP_LIMIT, any('HitDrop 500' in l for l in lines),
      lines)
piece = crystal.clean_piece({'crystal': {'hitDrop': 0}}, at(0, 0, 0))
check('the save keeps a user HitDrop of 0', piece['crystal'].get('hitDrop') == 0.0, piece['crystal'])
try:
    crystal.clean_piece({'crystal': {'hitDrop': -5}}, at(0, 0, 0))
    check('a negative HitDrop is refused', False)
except ValueError:
    check('a negative HitDrop is refused', True)

# 8. floor deeper than the limit under the root: no HitDrop, a warning
deep = F.Floor([rect(-3000, -3000, 3000, 3000, -1000.0)])
drop, fz = F.floor_drop(deep, at(0, 0, 0), HALF)
check('floor %g under the root is out of reach' % 1000, drop is None)
lines = F.warnings(F.fit(deep, at(0, 0, 0), HALF), drop, {})
check('and the build warns that no HitDrop is set',
      any('no walkable floor within' in l for l in lines), lines)

bad = [c for c in checks if not c[1]]
for name, ok, detail in checks:
    print('%s %s%s' % ('PASS' if ok else 'FAIL', name,
                       ('  -- %s' % (detail,))[:300] if not ok and detail else ''))
print('%d/%d passed' % (len(checks) - len(bad), len(checks)))
sys.exit(1 if bad else 0)
