"""Every particle effect the shipped levels place, as donors the Map Editor can add.

The effects New Object offers are shipped actors lifted from a level that places them
(riftlord.lavafx: an Emitter, a Niagara actor or an effect Blueprint, moved where the plan
says). The lava ones were picked by hand; this surveys every playable level's packages
for the rest, one donor per particle system, and writes them to fx-donors.json, which
lavafx reads beside its own DONORS.

    python3 -m riftlord.fxsurvey              # rebuild riftlord/fx-donors.json
    python3 -m riftlord.fxsurvey --show rain  # what it found, by name
"""

import argparse
import json
import math
import os
import re
import sys
from riftlord.core import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

#: Where the survey is kept.
OUT = gamepath.data('riftlord/fx-donors.json')
#: Format of fx-donors.json; lavafx ignores a file of another version.
VERSION = 1
#: Actor classes lifted as effects. An effect Blueprint (BP_LavaBurst_01_C) is included
#: when its class name says it is one; any other Blueprint owning a particle system is a
#: trap, a door or a character whose effect is not the point.
ACTOR_CLASSES = ('Emitter', 'NiagaraActor')
FX_BLUEPRINT_WORDS = ('Burst', 'FX', 'Fx', 'VFX', 'Smoke', 'Fire', 'Torch', 'Rain',
                      'Lightning', 'Waterfall', 'Fog', 'Steam')
#: Templates whose names say they belong to a cinematic or a menu, never to a level.
SKIP_WORDS = ('Cinematic', 'MainMenu', 'UI_', 'Intro', 'EndGame')
#: Groups for the Map Editor's labels, first match wins (case-insensitive).
GROUPS = (
    ('Weather', r'^rain|snow|lightning|blizzard'),
    ('Water', r'water|drip|steam|bubble|fountain|foam|splash'),
    ('Acid', r'acid'),
    ('Fog and dust', r'fog|mist|dust|cloud|haze|mote|wind'),
    ('Lava and ash', r'lava|ember|ash|magma'),
    ('Fire and smoke', r'fire|flame|torch|candle|brazier|smoke|burn|spark'),
    ('Magic', r'arcane|magic|rift|portal|glow|crystal|rune|energy|beam|orb|wisp'),
    ('Nature', r'leaf|leaves|butterfl|firefl|bird|bat|insect|pollen|petal'),
)
OTHER_GROUP = 'Other'
#: Donors come only from the game's own levels, never from an installed mod's.
SHIPPED_ROOT = '/Game/Maps/'


def _label(template):
    """'P_AshEmbers_Long_10' -> 'Ash Embers Long 10'."""
    name = re.sub(r'^(P|NS|PS|FX|VFX)_', '', template)
    name = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', name.replace('_', ' '))
    return ' '.join(name.split()) or template


def _group(template):
    for group, words in GROUPS:
        if re.search(words, template, re.I):
            return group
    return OTHER_GROUP


def _spacing(points):
    """Median nearest-neighbour distance between placements in the plane (uu), or None."""
    if len(points) < 2:
        return None
    near = []
    for i, a in enumerate(points):
        near.append(min(math.hypot(a[0] - b[0], a[1] - b[1])
                        for j, b in enumerate(points) if j != i))
    near.sort()
    return round(near[len(near) // 2], 1)


def survey(log=lambda _s: None):
    """{template: record} over every playable level (see module docstring)."""
    from riftlord.build.navigation import navpatch
    from riftlord.formats.omd3 import omd3level
    found = {}
    for level in sorted(navpatch.maps()):
        packages = omd3level.sublevels(level) + omd3level.extra_sublevels(level)
        for path in packages:
            if not path.startswith(SHIPPED_ROOT):
                continue
            try:
                _pieces, _lights, vfx, _actors = omd3level.read_package(path)
            except (Exception, SystemExit) as exc:   # noqa: BLE001
                log('%s: %s' % (path, exc))
                continue
            for fx in vfx:
                template = fx.get('template')
                cls = fx.get('actorClass') or ''
                if not template or template == 'particles' or not fx.get('actorName'):
                    continue
                if any(w in template for w in SKIP_WORDS):
                    continue
                if cls not in ACTOR_CLASSES and not (
                        cls.endswith('_C') and any(w in cls for w in FX_BLUEPRINT_WORDS)):
                    continue
                rec = found.setdefault(template, {
                    'template': template, 'label': _label(template), 'group': _group(template),
                    'actorClass': cls, 'donor': [path, fx['actorName']],
                    'levels': [], 'count': 0, 'placements': {}})
                rec['count'] += 1
                if level not in rec['levels']:
                    rec['levels'].append(level)
                rec['placements'].setdefault(path, []).append(fx['loc'])
        log('%s: %d effects so far' % (level, len(found)))
    for rec in found.values():
        #: the package placing it most is where its spacing is measured
        path, points = max(rec['placements'].items(), key=lambda kv: len(kv[1]))
        rec['spacing'] = _spacing(points)
        rec['heights'] = [round(min(p[2] for p in points), 1),
                          round(max(p[2] for p in points), 1)]
        rec['measuredIn'] = path
        del rec['placements']
    return found


def write(found, path=OUT):
    with open(path, 'w') as fh:
        json.dump({'version': VERSION, 'effects': found}, fh, indent=1, sort_keys=True)


def load(path=OUT):
    """{template: record} from fx-donors.json, or {} when it is missing or stale."""
    try:
        with open(path) as fh:
            got = json.load(fh)
    except (OSError, ValueError):
        return {}
    return got.get('effects') or {} if got.get('version') == VERSION else {}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--show', help='list the effects whose name holds this ("" lists all)')
    args = ap.parse_args(argv)
    if args.show is not None:
        for t, rec in sorted(load().items()):
            if args.show.lower() in t.lower():
                print('%-40s %-14s %4d  %s  spacing %s  z %s  %s' % (
                    t, rec['group'], rec['count'], rec['actorClass'], rec.get('spacing'),
                    rec.get('heights'), ', '.join(rec['levels'][:4])))
        return 0
    found = survey(log=print)
    write(found)
    print('wrote %s: %d effects' % (OUT, len(found)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
