"""The lights that glow off a level's liquid, and their colour for a new liquid material.

A light belongs to a liquid plane when it is a RectLight (or a PointLight, over lava
and acid only), stands over the
plane's footprint (within MATCH_MARGIN), sits between MATCH_BELOW under and MATCH_ABOVE
over the surface, is saturated (MIN_SATURATION), and its hue is within HUE_TOLERANCE of
its liquid kind's measured hue. The colour each liquid material gives its lights is
measured from the shipped levels that use it (`liquid-lights.json`).

    python3 -m riftlord.liquidlights --build      # measure, write liquid-lights.json
    python3 -m riftlord.liquidlights --show Scenario_06
"""

import argparse
import colorsys
import functools
import json
import math
import os
import statistics
import struct
import sys
from riftlord.core import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

#: The measured colours, written by --build.
CATALOGUE = gamepath.data('liquid-lights.json')

#: Light kinds that glow off a liquid.
LIGHT_KINDS = ('RectLight', 'PointLight')
#: Liquid kinds whose PointLights are matched too. Water and arcane glow blue, the
#: same hue as the blue fill PointLights every level hangs everywhere, so for those
#: only RectLights count.
POINT_LIGHT_LIQUIDS = ('lava', 'acid')
#: How far outside a plane's footprint a light may stand, in uu.
MATCH_MARGIN = 150.0
#: How far under the surface a light may sit, in uu.
MATCH_BELOW = 700.0
#: How far over the surface a light may sit, in uu.
MATCH_ABOVE = 1000.0
#: Lowest HSV saturation of a liquid's light; white and pale fills are not. Front Lawn's two
#: water RectLights inside the fort sit at 0.48 and stayed blue under Beset's lava at 0.5.
MIN_SATURATION = 0.45
#: Largest hue difference from the liquid kind's hue, in degrees.
HUE_TOLERANCE = 40.0
#: Each liquid kind's light hue in degrees, the median over the shipped levels' matches.
KIND_HUE = {'lava': 31.0, 'acid': 125.0, 'arcane': 210.0, 'water': 196.0}


def _hsv(colour):
    h, s, v = colorsys.rgb_to_hsv(*colour[:3])
    return h * 360.0, s, v


def _hue_gap(a, b):
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def kind_of(plane):
    """A liquid plane's kind: from its material's name, else its mesh's."""
    from riftlord.formats.omd3 import omd3level
    return (omd3level.liquid_kind(plane.get('material') or '')
            or omd3level.liquid_kind(plane.get('mesh') or '')
            or plane.get('liquidKind'))


def _footprint(piece):
    """(lo, hi) world box of a liquid piece, from the mesh catalogue's bounds."""
    from riftlord.formats.omd3 import meshcat
    info = meshcat.load().get(piece['mesh']) or {}
    o = info.get('origin') or [0.0, 0.0, 0.0]
    e = info.get('extent') or [0.0, 0.0, 0.0]
    m = piece['matrix']
    pts = []
    for sx in (-1, 1):
        for sy in (-1, 1):
            for sz in (-1, 1):
                v = (o[0] + sx * e[0], o[1] + sy * e[1], o[2] + sz * e[2])
                pts.append([m[r] * v[0] + m[4 + r] * v[1] + m[8 + r] * v[2] + m[12 + r]
                            for r in range(3)])
    return ([min(p[i] for p in pts) for i in range(3)],
            [max(p[i] for p in pts) for i in range(3)])


def match(level, points=False):
    """{plane piece id: [light]} for one level's visible liquid planes. A light is
    leveldata's record ({kind, loc, colour, intensity, export, sub}); each goes to the
    nearest plane that accepts it. `points` accepts PointLights over every liquid kind,
    not only POINT_LIGHT_LIQUIDS."""
    from riftlord.mapeditor import leveldata
    lv = leveldata.level(level)
    planes = [(p, _footprint(p), kind_of(p)) for p in lv['pieces']
              if p.get('liquid') and not p.get('hidden')]
    out = {}
    for light in lv.get('lights') or []:
        if light.get('kind') not in LIGHT_KINDS or light.get('export') is None:
            continue
        hue, sat, _v = _hsv(light.get('colour') or (1, 1, 1))
        if sat < MIN_SATURATION:
            continue
        best = None
        for piece, (lo, hi), kind in planes:
            if kind not in KIND_HUE or _hue_gap(hue, KIND_HUE[kind]) > HUE_TOLERANCE:
                continue
            if light['kind'] == 'PointLight' and kind not in POINT_LIGHT_LIQUIDS \
                    and not points:
                continue
            x, y, z = light['loc']
            off = math.hypot(max(lo[0] - x, 0.0, x - hi[0]), max(lo[1] - y, 0.0, y - hi[1]))
            rise = z - (lo[2] + hi[2]) / 2.0
            if off > MATCH_MARGIN or not -MATCH_BELOW <= rise <= MATCH_ABOVE:
                continue
            score = off + abs(rise)
            if best is None or score < best[0]:
                best = (score, piece['id'])
        if best:
            out.setdefault(best[1], []).append(light)
    return out


def build(out_path=CATALOGUE):
    """Measure every shipped level; write {materials, kinds, levels}."""
    from riftlord.mapeditor import leveldata
    per_material, per_kind, levels = {}, {}, {}
    for row in leveldata.levels():
        name = row['name']
        if not name.startswith('Scenario'):
            continue
        try:
            got = match(name)
            pieces = leveldata.level(name)['pieces']
        except BaseException:                        # noqa: BLE001
            continue
        levels[name] = {'planes': len(got), 'lights': sum(len(v) for v in got.values())}
        for pid, lights in got.items():
            plane = pieces[pid]
            mat = plane.get('material') or plane['mesh']
            for light in lights:
                for table, key in ((per_material, mat), (per_kind, kind_of(plane))):
                    table.setdefault(key, {}).setdefault(light['kind'], []).append(light)

    def summary(table):
        out = {}
        for key, by_kind in table.items():
            out[key] = {}
            for kind, lights in by_kind.items():
                out[key][kind] = {
                    'colour': [round(statistics.median(l['colour'][c] for l in lights), 4)
                               for c in range(3)],
                    'intensity': round(statistics.median(l['intensity'] for l in lights), 3),
                    'count': len(lights)}
        return out
    data = {'materials': summary(per_material), 'kinds': summary(per_kind),
            'levels': levels}
    with open(out_path, 'w') as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    return data


@functools.lru_cache(maxsize=1)
def catalogue():
    try:
        with open(CATALOGUE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {'materials': {}, 'kinds': {}}


def colour_for(material, light_kind):
    """[r, g, b] a liquid material gives a light of `light_kind`, or None."""
    from riftlord.formats.omd3 import omd3level
    cat = catalogue()
    for table, key in ((cat.get('materials') or {}, material),
                       (cat.get('kinds') or {}, omd3level.liquid_kind(material or ''))):
        entry = (table.get(key) or {}) if key else {}
        hit = entry.get(light_kind) or next(iter(entry.values()), None)
        if hit:
            return hit['colour']
    return None


def edits(level, plan):
    """{sublevel tag: {light export: [r, g, b]}} for lights over planes the plan repaints."""
    from riftlord.mapeditor import leveldata
    look = (plan or {}).get('look') or {}
    level_wide = look.get('liquid')
    planes = look.get('planes') or {}
    if not level_wide and not planes:
        return {}
    from riftlord.formats.omd3 import omd3level
    pieces = leveldata.level(level)['pieces']
    plain, with_points = match(level), match(level, points=True)
    out = {}
    for pid in set(plain) | set(with_points):
        material = planes.get(str(pid)) or planes.get(pid) or level_wide
        if not material or material == pieces[pid].get('material'):
            continue
        #: a plane that changes KIND (water to lava) takes its blue PointLights along: the
        #: fill-light caution behind POINT_LIGHT_LIQUIDS does not hold once the liquid
        #: under them glows another colour (Beset kept 25 blue PointLights over its lava)
        changes_kind = omd3level.liquid_kind(material) != kind_of(pieces[pid])
        for light in (with_points if changes_kind else plain).get(pid, []):
            rgb = colour_for(material, light['kind'])
            if rgb is None:
                continue
            out.setdefault(light.get('sub') or 'persistent', {})[light['export']] = rgb
    return out


def paint(builder, lights, report):
    """Write `LightColor` on each {export: [r, g, b]} that stores one. Returns a count."""
    done = 0
    for export, rgb in (lights or {}).items():
        data = bytearray(builder.pkg.exports[export - 1]['data'])
        span = builder._tag_span(data, 'LightColor')
        if not span or span[1] - span[2] != 4:
            from riftlord.core import buildmessage
            report['skipped'].append(buildmessage.left_out(
                'one light by the liquid', 'light export %d has no LightColor' % export,
                effect='it keeps the colour it ships with', verb='repaint'))
            continue
        b, g, r = (max(0, min(255, int(round(v * 255)))) for v in (rgb[2], rgb[1], rgb[0]))
        alpha = data[span[2] + 3]
        struct.pack_into('<4B', data, span[2], b, g, r, alpha)
        builder.pkg.exports[export - 1]['data'] = bytes(data)
        done += 1
    if done:
        report['notes'].append('%d liquid light(s) recoloured' % done)
    return done


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--build', action='store_true')
    ap.add_argument('--show')
    args = ap.parse_args(argv)
    if args.build:
        data = build()
        for name, row in sorted(data['levels'].items()):
            print('%-20s %3d planes with lights, %4d lights' % (name, row['planes'],
                                                                 row['lights']))
        for key, row in sorted(data['kinds'].items()):
            print(key, row)
    if args.show:
        for pid, lights in sorted(match(args.show).items()):
            print(pid, [(l['kind'], l['export'], l['sub'], l['colour']) for l in lights])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
