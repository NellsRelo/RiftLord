"""The drawing style of a source level's own minimap, read off its shipped texture.

The shipped minimaps do not share one style. Measured on all 51 textures
(workshop/research/MINIMAP-STYLE.md): the plate alpha is 38 on the base game, 51 or 64
on Ice and Sand, 77 on Scenario_06; Sand_01's walls sit at 26 under a 51 floor; the
outer wall band is 12 px at 1024 on most maps, 6 px on Scenario_12, 15 and 18; stairs
are hatched grey (139) on Scenario_11, 13, 14, 17 and Other_02, white on Scenario_05,
10, 12, 15, 16 and Ice_01, and not at all on most DLC and Sand maps. Regenerate draws a
mission in its source level's style, so each is read from that level's texture rather
than kept by hand: {alpha, wall_alpha, band, hatch, ...} per MapData volume. The
measurements are written once into minimapstyletable.py (`--table`), which drawings read.
"""

import collections
import functools
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

#: Bump when a measurement changes, so cached styles are read again.
VERSION = 7
#: Share of the drawn pixels an alpha must cover to be the plate's (riftlord.
#: minimapredraw.PLATE_SHARE).
PLATE_SHARE = 0.05
#: Own plate: at least this share of the plate alpha (ghosts are at half or less).
OWN_SHARE = 0.7
#: Luminance: floor under DARK, wall over LIGHT, lines LINE_LO or more.
DARK = 64
LIGHT = 192
LINE_LO = 100
#: Outer band runs are read on every BAND_STEP-th row and column, at least BAND_MIN
#: px (shorter runs are antialiasing) and at most BAND_MAX px (longer are wall masses),
#: at 1024.
BAND_STEP = 4
BAND_MIN = 3
BAND_MAX = 40
#: Hatching is HATCH_LINES thin lines starting within HATCH_WINDOW px (period 10 at
#: 1024); a texture hatches its stairs when at least HATCH_SHARE of its plate is hatched.
HATCH_LINES = 3
HATCH_WINDOW = 31
HATCH_SHARE = 0.002
#: Hatch lines whose brightest pixels are under this are grey, else white.
HATCH_WHITE = 200
#: The grey and white hatch luminances drawn (Scenario_13's 139; white walls' 255).
HATCH_GREY_LUM = 139
HATCH_WHITE_LUM = 255
#: A texture whose walls cover at least this share of its own plate draws building
#: masses solid (Sand_01's upper floor 0.53, Sand_02 0.52; the rest 0.10-0.33).
MASSED_SHARE = 0.4
#: A texture is drawn square when at least SQUARE_SHARE of its floor outline lies on
#: straight runs reaching SQUARE_RUN px either way along an axis at 1024 (Scenario_02
#: 0.77, _13 0.72, DLC_02 0.73, _04 0.63, Ice_01 0.59; the caves and lawns: _09 0.47,
#: _06 0.41, Ice_03 0.38, _12 0.38, _15 0.36, _18 0.35, Sand_03 0.31).
SQUARE_RUN = 10
SQUARE_SHARE = 0.55
#: A texture draws ledge lines when its floor carries at least this many thin white line
#: pixels per thousand floor pixels, outside the stairs (Scenario_13 3.7, _08 1.4,
#: DLC_03 12-23; none at all on DLC_01/02/04/05, Scenario_01, 02, 04, 14, Other_02/03,
#: Sand_02/04/05; under 0.7 on Scenario_03, 07, Sand_01, Sand_03).
LEDGE_PER_MILLE = 1.0
#: Hatch line spacing is read from gaps of HATCH_GAPS px between line starts and lines at
#: most HATCH_THICK px thick, at 1024 (period 10 on all but Scenario_09's 7 and Ice_03's
#: 9; width 1 to 3).
HATCH_GAPS = (3, 20)
HATCH_THICK = 4
#: A texture whose own plate is at least GHOST_GREY_SHARE grey (luminance in GHOST_GREY)
#: draws the floors under it at its own alpha with their floor that grey.
GHOST_GREY = (150, 192)
GHOST_GREY_SHARE = 0.1
#: An alpha within STACK_SLACK of a lower plate alpha p laid over its own ghost (p over
#: p/2, Porter-Duff) is that stack, not a plate: Scenario_05's upper floor draws its floor
#: at 54 = 38 over 19 where it lies over the floor below (97,725 px) and at 38 elsewhere
#: (125,558 px); no other shipped texture stacks (2026-10-24, all 39 multi-floor textures).
STACK_SLACK = 1
#: A texture leaves its floor open onto the other floors' ghosted floor (no wall band
#: between) when at least SEAM_SHARE of its floor outline touches ghosted floor within
#: SEAM_PX px: ScenarioDLC_02 floor 1 0.13, DLC_03 floor 1 0.13, Ice_03 0.14 both floors,
#: Scenario_15 floor 2 0.09; every other multi-floor texture 0.01 or less (2026-10-24).
SEAM_SHARE = 0.05
SEAM_PX = 2
#: Defaults when a texture cannot be read: the base game's.
DEFAULT = {'alpha': 38, 'wall_alpha': 38, 'band': 12 / 1024.0, 'hatch': HATCH_GREY_LUM,
           'hatch_period': 10 / 1024.0, 'hatch_width': 2 / 1024.0,
           'massed': False, 'square': True, 'ledges': True, 'ghost_lum': None,
           'stacked': False, 'open_seams': False}


def stacked_over(p):
    """The alpha of plate alpha `p` laid over its own half-alpha ghost."""
    a, b = p / 255.0, p / 510.0
    return int(round(255.0 * (1.0 - (1.0 - a) * (1.0 - b))))


def plate_of(counts, total):
    """(plate alpha, stacked) from {alpha: px} of a texture's `total` drawn px: the highest
    alpha on at least PLATE_SHARE of them that is not a lower one stacked over its ghost."""
    big = [v for v, n in counts.items() if n >= PLATE_SHARE * total]
    if not big:
        return int(counts.most_common(1)[0][0]), False
    stacks = {v for v in big
              if any(abs(v - stacked_over(p)) <= STACK_SLACK for p in big if p < v)}
    plain = [v for v in big if v not in stacks]
    return int(max(plain or big)), bool(stacks and plain)


def _cache_path():
    from riftlord.core import gamepath
    return os.path.join(gamepath.scratch('cache', gamepath.CODE_ROOT), 'minimap-style.json')


def _load_cache():
    try:
        with open(_cache_path()) as fh:
            got = json.load(fh)
        return got if got.get('version') == VERSION else {'version': VERSION}
    except (OSError, ValueError):
        return {'version': VERSION}


def hatched(lines):
    """Bool mask of hatching: pixels with at least HATCH_LINES thin lines starting within
    HATCH_WINDOW px along a row or a column of `lines`."""
    import numpy as np
    out = np.zeros_like(lines)
    h = HATCH_WINDOW // 2
    for axis in (0, 1):
        prev = np.roll(lines, 1, axis=axis)
        if axis == 0:
            prev[0, :] = False
        else:
            prev[:, 0] = False
        c = np.cumsum((lines & ~prev).astype(np.int32), axis=axis)
        pad = [(0, 0), (0, 0)]
        pad[axis] = (h + 1, h)
        c = np.pad(c, pad, mode='edge')
        if axis == 0:
            c[:h + 1, :] = 0
            n = c[2 * h + 1:, :] - c[:-2 * h - 1, :]
        else:
            c[:, :h + 1] = 0
            n = c[:, 2 * h + 1:] - c[:, :-2 * h - 1]
        out |= n >= HATCH_LINES
    return out


def measure(img):
    """{alpha, wall_alpha, band (share of the width), hatch (luminance or None)} of a
    minimap image."""
    import numpy as np
    from PIL import Image
    if img.size[0] != 1024:
        img = img.resize((1024, 1024), Image.BOX)
    a = np.asarray(img.convert('RGBA')).astype(np.int32)
    al, lum = a[..., 3], a[..., :3].mean(-1)
    drawn = al[al > 0]
    if not drawn.size:
        return dict(DEFAULT)
    counts = collections.Counter(drawn.tolist())
    alpha, stacked = plate_of(counts, drawn.size)
    # walls may sit under the floor's alpha (Sand_01: 26 under 51)
    light_al = al[(al > 0) & (lum > LIGHT)]
    wc = collections.Counter(light_al.tolist())
    wall_big = [v for v, n in wc.items() if n >= PLATE_SHARE * max(1, light_al.size)]
    wall_alpha = int(max(wall_big)) if wall_big else alpha
    own = al >= OWN_SHARE * min(alpha, wall_alpha)
    floor = own & (lum < DARK)
    light = own & (lum > LIGHT)
    # outer band: from the clear outside to the first floor pixel, along rows and columns
    runs = collections.Counter()
    for arr_f, arr_l, arr_o in ((floor, light, own), (floor.T, light.T, own.T)):
        for y in range(0, 1024, BAND_STEP):
            f, l, o = arr_f[y], arr_l[y], arr_o[y]
            for direction in (1, -1):
                xs = range(1024) if direction > 0 else range(1023, -1, -1)
                n, started = 0, False
                for x in xs:
                    if not started:
                        if o[x]:
                            started = True
                        else:
                            continue
                    if f[x]:
                        break
                    if not (l[x] or o[x]):
                        n = 0
                        break
                    n += 1
                if BAND_MIN <= n <= BAND_MAX:
                    runs[n] += 1
    band = (runs.most_common(1)[0][0] / 1024.0) if runs else DEFAULT['band']
    lines = own & (lum >= LINE_LO)
    h = hatched(lines) & lines
    hatch = None
    period, width = DEFAULT['hatch_period'], DEFAULT['hatch_width']
    if h.sum() >= HATCH_SHARE * max(1, own.sum()):
        top = float(np.percentile(lum[h], 90))
        hatch = HATCH_WHITE_LUM if top >= HATCH_WHITE else HATCH_GREY_LUM
        period, width = _hatch_spacing(h, period, width)
    massed = bool(light.sum() >= MASSED_SHARE * max(1, own.sum()))
    # other floors drawn grey on the own plate (Sand_01's upper floor: 0.26 of it at 191;
    # antialiasing elsewhere under 0.05)
    grey = own & (lum >= GHOST_GREY[0]) & (lum < GHOST_GREY[1])
    ghost_lum = None
    if grey.sum() >= GHOST_GREY_SHARE * max(1, own.sum()):
        ghost_lum = int(collections.Counter(lum[grey].astype(int).tolist()).most_common(1)[0][0])
    # drawn square: most of the floor's outline runs straight along an axis
    edge = floor & ~(np.roll(floor, 1, 0) & np.roll(floor, -1, 0) & np.roll(floor, 1, 1)
                     & np.roll(floor, -1, 1))
    straight = np.zeros_like(edge)
    for axis in (0, 1):
        run = edge.copy()
        for d in range(1, SQUARE_RUN + 1):
            run &= np.roll(edge, d, axis) & np.roll(edge, -d, axis)
        straight |= run
    square = bool(straight.sum() >= SQUARE_SHARE * max(1, edge.sum()))
    # ledge lines: thin white lines on the floor, outside the stairs
    from riftlord.build.minimap.minimapscore import thin_lines, _grow
    stairs = _grow(hatched(lines), 4) & own
    thin = thin_lines(light) & ~stairs & _grow(floor, 1)
    ledges = bool(thin.sum() >= LEDGE_PER_MILLE / 1000.0 * max(1, floor.sum()))
    # floor open onto the other floors' ghosted floor, no band between
    gfloor = (al > 0) & (al < OWN_SHARE * alpha) & (lum < DARK)
    pfloor = (al >= OWN_SHARE * alpha) & (lum < DARK)
    p_edge = pfloor & _grow(~pfloor, 1)
    seam = pfloor & _grow(gfloor, SEAM_PX)
    open_seams = bool(gfloor.any() and seam.sum() >= SEAM_SHARE * max(1, p_edge.sum()))
    return {'alpha': alpha, 'wall_alpha': wall_alpha, 'band': band, 'hatch': hatch,
            'hatch_period': period, 'hatch_width': width,
            'massed': massed, 'square': square, 'ledges': ledges, 'ghost_lum': ghost_lum,
            'stacked': stacked, 'open_seams': open_seams}


def _hatch_spacing(h, period, width):
    """(period, width) as shares of the width: the commonest gap between the starts of
    neighbouring hatch lines (HATCH_GAPS px) and the commonest line thickness (at most
    HATCH_THICK px) in the hatched pixels `h` at 1024; `period` and `width` when none."""
    import numpy as np
    gaps, widths = collections.Counter(), collections.Counter()
    for arr in (h, h.T):
        for row in arr:
            xs = np.flatnonzero(row)
            if len(xs) < 2:
                continue
            cut = np.diff(xs) > 1
            starts, ends = xs[np.r_[True, cut]], xs[np.r_[cut, True]]
            gaps.update(int(g) for g in np.diff(starts) if HATCH_GAPS[0] <= g <= HATCH_GAPS[1])
            widths.update(int(n) for n in ends - starts + 1 if n <= HATCH_THICK)
    if gaps:
        period = gaps.most_common(1)[0][0] / 1024.0
    if widths:
        width = widths.most_common(1)[0][0] / 1024.0
    return period, width


@functools.lru_cache(maxsize=64)
def of_texture(texture):
    """The style of one shipped minimap texture (game path) from the measured table
    (minimapstyletable.STYLES, written by `minimapstyle --table`), else DEFAULT. Drawings
    never read a shipped texture (user rule 2026-10-24: every mission takes the same
    pipeline; the host's art only measures rules)."""
    from riftlord.build.minimap import minimapstyletable
    got = minimapstyletable.STYLES.get(texture)
    if not got and texture:
        # a mission's copy of a shipped texture keeps the source's object name (Beset's
        # `.../RiftLordBesetAlpha3_Map01/Scenario_09_01_Minimap.Scenario_09_01_Minimap`)
        obj = texture.rsplit('.', 1)[-1]
        same = [k for k in minimapstyletable.STYLES if k.rsplit('.', 1)[-1] == obj]
        if len(same) == 1:
            got = minimapstyletable.STYLES[same[0]]
    return dict(DEFAULT, **got) if got else dict(DEFAULT)


def measured(texture):
    """The style measured off one shipped minimap texture (game path), cached on disk:
    for writing the table, never for drawing."""
    from riftlord.build.minimap import mapdata
    cache = _load_cache()
    if texture in cache:
        return dict(cache[texture])
    try:
        img = mapdata.minimap_image({'texture': texture})
    except Exception:                                # noqa: BLE001
        img = None
    got = measure(img) if img is not None else dict(DEFAULT)
    cache = _load_cache()
    cache[texture] = got
    try:
        os.makedirs(os.path.dirname(_cache_path()), exist_ok=True)
        tmp = _cache_path() + '.part'
        with open(tmp, 'w') as fh:
            json.dump(cache, fh, indent=1)
        os.replace(tmp, _cache_path())
    except OSError:
        pass
    return dict(got)


def of_volume(volume):
    """The style of a MapData volume's texture."""
    tex = (volume.get('texture') or '').split('.')[0]
    return of_texture(volume.get('texture') or '') if tex else dict(DEFAULT)


def level_style(volumes):
    """One style for a level's drawings: each floor's own alpha and band, the level's
    hatching (any floor that hatches), as [{...}] per volume."""
    styles = [of_volume(v) for v in volumes]
    first = next((s for s in styles if s['hatch'] is not None), None)
    bands = [s['band'] for s in styles]
    for s in styles:
        own = s['hatch']
        s['hatch'] = first['hatch'] if first else None
        if first and own is None:
            # a floor without hatching of its own hatches as the level's first hatched floor
            s['hatch_period'], s['hatch_width'] = first['hatch_period'], first['hatch_width']
        # a floor too small to read keeps the level's commonest band
        s['band'] = collections.Counter(bands).most_common(1)[0][0] if bands else s['band']
    return styles


#: The measured style table's module, beside this one.
TABLE_MODULE = os.path.join(HERE, 'minimapstyletable.py')


def write_table(path=TABLE_MODULE):
    """Measure every shipped level's minimap textures and write {texture: style} as a
    module (minimapstyletable.STYLES). Returns the number of textures."""
    from riftlord.build.minimap import mapdata
    styles = {}
    for name in sorted(mapdata.maps(include_mods=False)):
        for v in (mapdata.read(name) or {}).get('volumes') or []:
            tex = v.get('texture') or ''
            if tex.split('.')[0] and tex not in styles:
                styles[tex] = measured(tex)
    lines = ['"""The drawing style of every shipped minimap texture, measured off the textures',
             'by `python3 -m riftlord.build.minimap.minimapstyle --table` (minimapstyle.measure).',
             'Drawings read this table, never a texture. Written by that command: do not edit."""',
             '', 'STYLES = {']
    for tex in sorted(styles):
        s = styles[tex]
        lines.append('    %r: {' % tex)
        for k in sorted(s):
            v = s[k]
            lines.append('        %r: %r,' % (k, round(v, 9) if isinstance(v, float) else v))
        lines.append('    },')
    lines.append('}')
    tmp = path + '.part'
    with open(tmp, 'w') as fh:
        fh.write('\n'.join(lines) + '\n')
    os.replace(tmp, path)
    return len(styles)


def main():
    from riftlord.build.minimap import mapdata
    if sys.argv[1:] == ['--table']:
        print('%d textures -> %s' % (write_table(), TABLE_MODULE))
        return
    for name in sys.argv[1:] or sorted(mapdata.maps(include_mods=False)):
        for k, v in enumerate((mapdata.read(name) or {}).get('volumes') or []):
            s = of_volume(v)
            print('%-18s v%d alpha %3d wall %3d band %4.1f px hatch %s massed %s'
                  % (name, k, s['alpha'], s['wall_alpha'], s['band'] * 1024, s['hatch'],
                     s.get('massed')))


if __name__ == '__main__':
    main()
