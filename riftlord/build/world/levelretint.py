"""The level's own effects as other particle systems.

Plan data, keyed '<sublevel tag>|<actor export>' like `actorMoves` and `removedFx`:

* `fxSwaps` {key: template}: the Emitter actor's ParticleSystemComponent plays another
  shipped Cascade system (a surveyed effect, riftlord.mapeditor.fxsurvey).
* `lightColours` {key: '#rrggbb'} was the level's lights' colours until 2026-11-05; it is
  now `lights` {key: {'LightColor': '#rrggbb', ...}} (riftlord.build.world.lighting), and
  clean_colours reads the old field for riftlord.build.world.lightmigrate.

    python3 -m riftlord.build.world.levelretint Scenario_16 PLAN.json   # what a plan writes
"""

import functools
import json
import re
import struct
import sys

#: A colour as the plan stores it.
HEX = re.compile(r'^#[0-9a-f]{6}$')
#: An actor key ('<sublevel tag>|<actor export, 1-based>').
ACTOR_KEY = re.compile(r'^[^|]+\|[1-9][0-9]*$')
#: The actor class whose particle system can be swapped.
SWAP_CLASS = 'Emitter'
#: The preload group a placed emitter lists its Template in (apply.CREATE_BEFORE_SERIALIZE).
TEMPLATE_GROUP = 1


def hex_rgb(value):
    """'#rrggbb' -> [r, g, b] (0-1, sRGB byte / 255)."""
    return [int(value[i:i + 2], 16) / 255.0 for i in (1, 3, 5)]


def clean_colours(raw):
    """An old plan's `lightColours`, checked: {key: '#rrggbb'} ({} for none)."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        value = str(value or '').strip().lower()
        if ACTOR_KEY.match(str(key)) and HEX.match(value):
            out[str(key)] = value
    return out


@functools.lru_cache(maxsize=1)
def swap_templates():
    """{template: actor class} of the shipped Cascade effects an emitter may play."""
    from riftlord.mapeditor import fxsurvey
    return {t: rec.get('actorClass') for t, rec in fxsurvey.load().items()
            if rec.get('actorClass') == SWAP_CLASS}


def clean_swaps(raw):
    """The plan's `fxSwaps`, checked: {key: template}. Raises ValueError on a template no
    shipped level plays from an Emitter."""
    out = {}
    if not isinstance(raw, dict):
        return out
    known = swap_templates()
    for key, value in raw.items():
        if not ACTOR_KEY.match(str(key)) or not value:
            continue
        if str(value) not in known:
            raise ValueError('no such effect for an emitter: %r' % (value,))
        out[str(key)] = str(value)
    return out


def swap_edits(level, plan):
    """{sublevel tag: {particle component export: template}} the plan's `fxSwaps` write."""
    want = clean_swaps((plan or {}).get('fxSwaps'))
    if not want:
        return {}
    from riftlord.mapeditor import leveldata
    out = {}
    for fx in leveldata.level(level).get('vfx') or []:
        if fx.get('actorClass') != SWAP_CLASS or not fx.get('actor') or not fx.get('export'):
            continue
        tag = fx.get('sub') or 'persistent'
        template = want.get('%s|%d' % (tag, fx['actor']))
        if template and template != fx.get('template'):
            out.setdefault(tag, {})[int(fx['export'])] = template
    return out


@functools.lru_cache(maxsize=None)
def template_path(template):
    """The /Game package path of a shipped particle system, by its asset name."""
    from riftlord.core import gamefiles
    hits = [k for k in gamefiles.index()
            if k.endswith('/' + template + '.uasset') and k.startswith('../../../OMD/Content/')]
    if len(hits) != 1:
        raise SystemExit('particle system %s: %d assets of that name' % (template, len(hits)))
    return '/Game/' + hits[0][len('../../../OMD/Content/'):-len('.uasset')]


def swap(builder, swaps, report):
    """Point each {particle component export: template}'s Template at that particle system,
    and its preload list at the new import in place of the old. Returns a count."""
    from riftlord.build import apply as _apply
    from riftlord.build import levelbuild
    pkg = builder.pkg
    done = 0
    for export, template in (swaps or {}).items():
        data = pkg.exports[export - 1]['data']
        span = builder._tag_span(data, 'Template')
        if not span:
            from riftlord.core import buildmessage
            report['skipped'].append(buildmessage.left_out(
                'the colour of one of the level\'s effects',
                'effect export %d names no particle system' % export))
            continue
        old = struct.unpack_from('<i', data, span[2])[0]
        path = template_path(template)
        owner = builder._import('/Script/CoreUObject', 'Package', 0, path)
        name, number = _apply.fname_split(template)
        new = builder._import('/Script/Engine', 'ParticleSystem', owner, name, number)
        builder.set_object(export, 'Template', new)
        _replace_preload(builder, export, old, new)
        report['notes'].append('%s plays %s' % (levelbuild._name_of(pkg, export - 1), template))
        done += 1
    return done


def _replace_preload(builder, export, old, new):
    """`old` in the export's preload groups becomes `new` (added to TEMPLATE_GROUP when the
    export lists no `old`)."""
    from riftlord.build import apply as _apply
    pkg = builder.pkg
    meta = bytearray(pkg.exports[export - 1]['meta'])
    first = struct.unpack_from('<i', meta, 84)[0]
    counts = list(struct.unpack_from('<4i', meta, 88))
    if first < 0 or old not in pkg.preload[first:first + sum(counts)]:
        _apply.add_preload(builder, export, TEMPLATE_GROUP, new)
        return
    groups, at = [], first
    for n in counts:
        groups.append([new if p == old else p for p in pkg.preload[at:at + n]])
        at += n
    #: the export's slice is re-appended, so no other export's slice moves
    struct.pack_into('<i', meta, 84, len(pkg.preload))
    for g in groups:
        pkg.preload.extend(g)
    pkg.preload_count = len(pkg.preload)
    pkg.exports[export - 1]['meta'] = bytes(meta)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2:
        print(__doc__)
        return 2
    with open(argv[1], encoding='utf-8') as fh:
        plan = json.load(fh)
    plan = plan.get('plan', plan)
    for label, got in (('effects', swap_edits(argv[0], plan)),):
        for tag, rows in sorted(got.items()):
            print('%s %s: %d' % (label, tag, len(rows)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
