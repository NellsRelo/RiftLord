"""Environmental traps as New Object items: cauldrons, falling boom barrels, grinder pits.

Research: workshop/research/LEVEL-CONTROLLERS-AND-PATTERNS.md section 1.3. 17 shipped
levels place them (controller-donors.json `envtraps`, riftlord.controllers --survey). Each
is lifted whole from a shipped placement, as doors are (apply._add), so it arrives with
its ability system, perception, map marker and status effects.

* **Cauldron** (`BP_Cauldron_C`): an added one carries `pour`, the pour template's name.
  A cauldron's drop is three settings that travel together -- the pour template, the
  TriggerBox (extent z = half the drop, sitting under the spout) and the floor splash
  (P_CauldronFloor, at the drop) -- so the donor is the shipped placement whose drop is
  the median for that template, and it brings all three.
* **Falling boom barrel** (`BP_FallingBoomBarrel_C`): its actor origin is the top of the
  barrel (FallingBoomBarrel_SKM spans z -149..10 at component scale 1.25), the point a
  hanging chain's lower end meets: HANG_ANCHOR.
* **Grinder pits** (`BP_GrinderPit_3X_C`, `_4X_C`, `BP_Grinders_3X_C`): a pit insert
  (GrinderPit_3X_SKM spans z -300..15 over 604 x 904 uu); shipped ones sit in floors and,
  turned 90 degrees, in walls.
"""

import functools
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

#: The New Object group the traps are listed under (leveldata.ACTOR_GROUPS).
GROUP = 'Environmental Traps'
CLASS_PATTERN = r'^BP_(FallingBoomBarrel|Cauldron|GrinderPit_[34]X|Grinders_3X)_C$'

CAULDRON = 'BP_Cauldron_C'
BARREL = 'BP_FallingBoomBarrel_C'
GRINDERS = ('BP_GrinderPit_3X_C', 'BP_GrinderPit_4X_C', 'BP_Grinders_3X_C')

#: A cauldron that stores no pour template uses the class's (Default__BP_Cauldron_C:
#: `P_CauldronPour`; Scenario_Sand_01's three).
DEFAULT_POUR = 'P_CauldronPour'
POUR_PREFIX = 'P_CauldronPour_'
#: Plan key of an added cauldron's pour template.
POUR_KEY = 'pour'

#: Where a hanging chain's lower end meets a falling boom barrel, in actor space: the
#: actor origin, the barrel's top.
HANG_ANCHOR = (0.0, 0.0, 0.0)


def _drop(rec):
    """A cauldron's drop (uu): how far below it its floor splash sits."""
    loc = rec.get('P_CauldronFloor.RelativeLocation')
    return -loc[2] if loc else None


@functools.lru_cache(maxsize=1)
def pours():
    """[{key, label, drop, count, donor}] of the shipped pour templates, shortest first."""
    from riftlord.mapeditor import controllers
    rows = (controllers.load().get('envtraps') or {}).get(CAULDRON) or []
    by = {}
    for r in rows:
        key = (r.get('pour') or DEFAULT_POUR).rsplit('.', 1)[-1]
        by.setdefault(key, []).append(r)
    out = []
    for key, recs in by.items():
        drops = [d for d in (_drop(r) for r in recs) if d is not None]
        mid = statistics.median(drops) if drops else None
        donor = min(recs, key=lambda r: abs((_drop(r) or 0) - (mid or 0)))
        label = key[len(POUR_PREFIX):] if key.startswith(POUR_PREFIX) else 'Standard'
        out.append({'key': key, 'label': label, 'drop': round(mid, 1) if mid else None,
                    'count': len(recs), 'donor': [donor['package'], donor['export']]})
    return sorted(out, key=lambda o: (o['drop'] or 0, o['key']))


def default_pour():
    """The pour a new cauldron gets: the most-placed template."""
    got = pours()
    return max(got, key=lambda o: o['count'])['key'] if got else None


def donor(item):
    """(package, export) to lift an added environmental trap from, or None for the
    class's catalogue donor."""
    if item.get('name') != CAULDRON:
        return None
    want = item.get(POUR_KEY) or default_pour()
    row = next((p for p in pours() if p['key'] == want), None)
    if row is None:
        return None
    from riftlord.mapeditor import controllers
    return controllers.package_donor(*row['donor'])


def clean(item, raw):
    """Copy an added trap's own settings from `raw` into the saved `item`."""
    if item.get('name') == CAULDRON and raw.get(POUR_KEY):
        if raw[POUR_KEY] not in {p['key'] for p in pours()}:
            raise ValueError('no such cauldron pour: %r' % (raw[POUR_KEY],))
        item[POUR_KEY] = str(raw[POUR_KEY])
    return item


def choices():
    """What the Map Editor offers for environmental traps."""
    return {'group': GROUP, 'pours': pours(), 'defaultPour': default_pour(),
            'hangAnchor': list(HANG_ANCHOR), 'barrel': BARREL, 'cauldron': CAULDRON,
            'grinders': list(GRINDERS)}
