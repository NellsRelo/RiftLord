"""Score Regenerate against the shipped minimaps: every shipped level, every floor.

    python3 -m riftlord.build.minimap.minimapscore                 # all levels, both cuts
    python3 -m riftlord.build.minimap.minimapscore Scenario_13 --cut three
    python3 -m riftlord.build.minimap.minimapscore --warm          # solid grids, once
    python3 -m riftlord.build.minimap.minimapscore --gate          # drop past the baseline fails
    python3 -m riftlord.build.minimap.minimapscore --accept        # this run is the baseline
    python3 -m riftlord.build.minimap.minimapscore Scenario_14 --set \\
        riftlord.build.minimap.minimapredraw.GROW_MAX=300          # a sweep, own folder
    python3 -m riftlord.build.minimap.minimapscore sheet Scenario_14 Scenario_16 \\
        --before DIR --after DIR [--cut art] [--record 900_Beset_Alpha_3_P]
    python3 -m riftlord.build.minimap.minimapscore inspect Scenario_14 --floor 1 \\
        [--at U,V] [--box U0,V0,U1,V1] [--cut whole]

Levels are drawn WORKERS at a time, each in its own process (`--workers`).

Incremental: `--out` keeps `state.json` (every drawing's scores and the digest of the
drawing code it came from); a run redraws only drawings whose digest changed (and
rescores, without drawing, when only the scoring changed), saving after each level, so
an interrupted run resumes. Each run appends a line to `runs.log` (mean score, how many
drawings reach TARGET, change against the baseline) and writes `worst.png`: the worst
WORST_N drawings (shipped | regenerated | difference) and the largest changes against
the baseline. The baseline is BASELINE, updated only with --accept.

Each level is regenerated from its own unedited geometry in its own shipped frame
(riftlord/minimapredraw.py) and compared with the shipped texture at 1024 px. Per floor
it writes `cmp_<level>_v<k>_<cut>.png` (shipped | regenerated | difference) and a
JSON line; `summary.json` and `summary.md` collect them. Levels are drawn one at a time.

Cuts: `doors` stops at the doors to the floor the rift reaches; `reached` keeps all the
walkable floor the spawners, route points, rifts and player starts stand on (the
approaches); both are mission cuts, geometry only, and TRACKED per floor in the baseline
and the best so far (`--cut mission`, the default). `whole` keeps all the floor the
routes, rifts and player starts reach; `all` draws every polygon of the walkable graph;
`art` draws inside the region the shipped art draws (a comparison only); `--cut three` is
the tracked cuts and `art`.

Measures, all at 1024 px on the floor's own plate (alpha at least OWN_SHARE of its plate
alpha; fainter pixels are other floors' ghosts):

* plate IoU and floor IoU (floor: luminance under DARK);
* edge: mean distance between the two floor outlines, both ways, in px (capped);
* stray: white line pieces Regenerate draws over the shipped floor, and missing: the
  shipped art's white lines over Regenerate's floor, each counted as connected pieces of
  at least LINE_PIECE px lying more than LINE_SLACK px from the other's light pixels;
* stairs IoU (hatched regions: runs of thin parallel lines, grey or white) and hatch:
  the share of the shipped hatch lines within one pixel of a regenerated one, inside
  the stairs both draw.
"""

import argparse
import json
import os
import sys
import time
from riftlord.core import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

#: Default output folder.
OUT = '/mnt/2tb_pro_a/tmp/minimapstyle2/inc'
#: A development copy of this folder: `python3 <copy>/devrun.py ...` runs the harness on
#: it (its own digests, its output in DEV_OUT, never --accept), the tree untouched.
DEV_ENV = 'MINIMAP_DEV'
DEV_OUT = '/mnt/2tb_pro_a/tmp/minimapstyle2/dev'
if os.environ.get(DEV_ENV):
    OUT = DEV_OUT
#: Comparison size, in px.
SIZE = 1024
#: A pixel is on the floor's own plate at this share of its plate alpha or more (ghosts
#: are at half or less; DXT5 block edges at 40-43 over 38 stay in).
OWN_SHARE = 0.7
#: Luminance under this is floor, over LIGHT is wall; lines (walls, hatching, grey 139 or
#: white) are HATCH_LO or more.
DARK = 64
LIGHT = 192
HATCH_LO = 100
#: Outline distances are capped at this many px.
EDGE_CAP = 40
#: Line pieces: at least this many px, more than this far from the other's light pixels.
LINE_PIECE = 8
LINE_SLACK = 3
#: Hatching: at least HATCH_LINES thin lines starting within HATCH_WINDOW px (the
#: shipped period is 10 px); the hatched pixels grown by STAIR_FILL px make the stairs.
HATCH_LINES = 3
HATCH_WINDOW = 31
#: Hatch lines are at most this many px across (2 px lines at 1024, 4-5 px on Ice_01, with
#: their antialiased edges); thicker light runs are walls and do not count.
THIN_MAX = 6
#: ... and run at least this many px along their length (the shortest shipped flight is
#: about 20 px across at 1024).
LINE_LONG = 12
STAIR_FILL = 4
#: A hatched region under this many px is not a flight. The 112 hatched regions of the 49
#: shipped floors (2026-10-24) are either at most 227 px (ledge stacks and trims read as
#: hatching: Scenario_18 161, ScenarioDLC_03 floor 0 59 + 27, Scenario_13 6 x 99) or at
#: least 828 px (the smallest real flight, Scenario_Ice_03 floor 0); 400 lies in that gap
#: (its geometric middle is 433).
STAIR_MIN = 400
#: Composite score weights: floor IoU, plate IoU, edge (as 1 - edge / EDGE_CAP), stairs.
WEIGHTS = (0.35, 0.15, 0.15, 0.15, 0.2)
#: A thin white line counts as matched within this many px of the other's white
#: (user, 2026-10-23: stray lines are what reads wrong most).
LINE_FIT = 2
#: The lines share is taken over at least this many thin line px: the median of the 31
#: shipped floors that draw thin lines outside their stairs (18 of 49 draw none), 2026-10-23.
LINE_NORM = 3000
#: Visualisation gain on alpha.
GAIN = 5.0
#: Every drawing's score must reach this (user: 0.95 on 2026-10-22, raised to 0.98 on
#: 2026-10-23; then a human review).
TARGET = 0.98
#: The saved baseline score table (per floor, the best cut), and how far a drawing's
#: score may fall below it before --gate fails.
BASELINE = os.path.join(gamepath.REPO_ROOT, 'docs', 'research', 'minimap-baseline.json')
GATE_TOLERANCE = 0.01
#: Per floor, the best score any promoted round reached ({key: {score, when}}): promote
#: refuses a floor more than GATE_TOLERANCE under it unless allowed, so drops inside the
#: tolerance cannot pile up round after round.
BEST = os.path.join(gamepath.REPO_ROOT, 'docs', 'research', 'minimap-best.json')
#: Levels drawn at once, each in its own process: about 1 GB each (16 cores, 31 GB, much
#: of it taken by other applications).
WORKERS = 4
#: Tile size of a before/after sheet, in px.
SHEET_TILE = 384
#: Rows in the worst-first sheet, and of changes against the baseline; thumbnail px.
WORST_N = 12
CHANGED_N = 8
THUMB = 256
#: The harness keeps every level's solid grids here (riftlord.minimapsolid.CACHE_ENV),
#: up to SOLID_BYTES in all.
SOLID_CACHE = '/mnt/2tb_pro_a/tmp/minimapstyle2/solid'
SOLID_BYTES = 30 * 1024 ** 3
SOLID_KEEP = 400
#: Files whose code makes a drawing, and those that score one.
DRAW_FILES = ('riftlord/build/minimap/minimapredraw.py', 'riftlord/build/minimap/minimapsolid.py',
              'riftlord/build/minimap/minimapstyle.py', 'riftlord/build/minimap/mapdata.py',
              'riftlord/build/minimap/minimapstyletable.py')
SCORE_FILES = ('riftlord/build/minimap/minimapscore.py',)


#: The digests a run starts with: code edited during a run must not label drawings made
#: with the code that was loaded.
_DIGESTS = {}


def _digest(files):
    if files in _DIGESTS:
        return _DIGESTS[files]
    import hashlib
    h = hashlib.sha1()
    for f in files:
        try:
            # the files beside this one: a development copy run through dev overlay
            # (DEV_ENV) digests its own code
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   os.path.basename(f)), 'rb') as fh:
                h.update(fh.read())
        except OSError:
            pass
    _DIGESTS[files] = h.hexdigest()[:16]
    return _DIGESTS[files]


def _use_harness_cache():
    from riftlord.build.minimap import minimapsolid
    os.environ[minimapsolid.CACHE_ENV] = SOLID_CACHE
    minimapsolid.GRID_BYTES = SOLID_BYTES
    minimapsolid.GRID_KEEP = SOLID_KEEP


#: With --align (ALIGN_ENV), the floors whose shipped art is drawn off its MapData frame
#: (minimapredraw.ART_ALIGN, with its evidence) are scored against their art laid on the
#: frame; the drawing itself stays in the MapData frame, which places the markers.
ALIGN_ENV = 'MINIMAP_ALIGN'


def _align_table():
    from riftlord.build.minimap import minimapredraw as mm
    return mm.ART_ALIGN


def align_art(a, how):
    """The shipped RGBA array `a` ([v][u][4] at SIZE) laid on the MapData frame by `how`
    ({scale, shift}); pixels read from off the texture are clear."""
    import numpy as np
    n = a.shape[0]
    c = n / 2.0
    s = float(how['scale'])
    du, dv = (float(x) * n / SIZE for x in how['shift'])
    ys, xs = np.mgrid[0:n, 0:n].astype(np.float32) + 0.5
    ix = np.floor(c + s * (xs - c) + du).astype(int)
    iy = np.floor(c + s * (ys - c) + dv).astype(int)
    ok = (ix >= 0) & (ix < n) & (iy >= 0) & (iy < n)
    out = np.zeros_like(a)
    out[..., :3] = 255
    out[ok] = a[iy[ok], ix[ok]]
    return out


def _grow(m, n=1):
    from riftlord.build.minimap.minimapredraw import _grow as g
    return g(m, n, True)


def _dist(mask, cap):
    """Chessboard distance to the nearest True of `mask`, capped."""
    import numpy as np
    d = np.full(mask.shape, cap, dtype=np.int32)
    cur = mask.copy()
    d[cur] = 0
    for k in range(1, cap):
        nxt = _grow(cur)
        d[nxt & ~cur] = k
        cur = nxt
        if cur.all():
            break
    return d


def _pieces(mask, min_px):
    from riftlord.build.minimap.minimapredraw import _components
    return [c for c in _components(mask) if c[0] >= min_px]


def load(img, plate):
    """int array [v][u][4] at SIZE of an RGBA image."""
    import numpy as np
    from PIL import Image
    if img.size[0] != SIZE:
        img = img.resize((SIZE, SIZE), Image.BOX)
    return np.asarray(img.convert('RGBA')).astype(np.int32)


def hatched(lines):
    """Bool mask of the hatched stairs: pixels with at least HATCH_LINES thin lines
    starting within HATCH_WINDOW px along a row or a column."""
    import numpy as np
    out = np.zeros_like(lines)
    for axis in (0, 1):
        prev = np.roll(lines, 1, axis=axis)
        if axis == 0:
            prev[0, :] = False
        else:
            prev[:, 0] = False
        rise = (lines & ~prev).astype(np.int32)
        c = np.cumsum(rise, axis=axis)
        h = HATCH_WINDOW // 2
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


def thin_lines(lines):
    """`lines` pixels on a run of at most THIN_MAX px across the row or column: hatching
    and ledge lines, not wall bands, pillars or masses."""
    import numpy as np
    out = np.zeros_like(lines)
    for axis in (0, 1):
        # a pixel on a run longer than THIN_MAX lies in some window of THIN_MAX + 1 line px
        run = np.zeros_like(lines)
        for lo in range(-THIN_MAX, 1):
            win = np.ones_like(lines)
            for d in range(lo, lo + THIN_MAX + 1):
                win &= np.roll(lines, d, axis=axis)
            run |= win
        # ... and long the other way: a hatch line spans its flight, a jag in a band
        # outline does not
        thin = lines & ~run
        other = 1 - axis
        long_ = np.zeros_like(lines)
        for lo in range(-LINE_LONG + 1, 1):
            win = np.ones_like(lines)
            for d in range(lo, lo + LINE_LONG):
                win &= np.roll(thin, d, axis=other)
            long_ |= win
        out |= long_
    return out


def classes(a, plate):
    al, lum = a[..., 3], a[..., :3].mean(-1)
    own = al >= OWN_SHARE * plate
    light = own & (lum > LIGHT)
    lines = own & (lum >= HATCH_LO)
    stairs = _grow(hatched(thin_lines(lines)), STAIR_FILL) & own
    for area, pix in _pieces(stairs, 1):
        if area < STAIR_MIN:
            stairs.ravel()[pix] = False
    return {'plate': own, 'floor': own & (lum < DARK), 'light': light, 'lines': lines,
            'stairs': stairs, 'any': al > 0}


def iou(x, y):
    u = float((x | y).sum())
    return float((x & y).sum()) / u if u else 1.0


def score(shipped, regen, plate):
    """{plate, floor, edge, stray, missing, stairs, hatch, score} and the masks."""
    import numpy as np
    s, r = classes(shipped, plate), classes(regen, plate)
    out = {'plate': iou(s['plate'], r['plate']), 'floor': iou(s['floor'], r['floor'])}
    be_s = s['floor'] & _grow(~s['floor'])
    be_r = r['floor'] & _grow(~r['floor'])
    if be_s.any() and be_r.any():
        ds, dr = _dist(be_s, EDGE_CAP), _dist(be_r, EDGE_CAP)
        out['edge'] = float((dr[be_s].mean() + ds[be_r].mean()) / 2.0)
    else:
        out['edge'] = float(EDGE_CAP)
    near_s = _grow(s['light'], LINE_SLACK)
    near_r = _grow(r['light'], LINE_SLACK)
    outside = ~(s['stairs'] | r['stairs'])
    stray = r['light'] & ~near_s & _grow(s['floor'], 1) & outside
    missing = s['light'] & ~near_r & _grow(r['floor'], 1) & outside
    out['stray'] = len(_pieces(stray, LINE_PIECE))
    out['missing'] = len(_pieces(missing, LINE_PIECE))
    st_s, st_r = s['stairs'], r['stairs']
    out['stairs'] = iou(st_s, st_r) if (st_s.any() or st_r.any()) else 1.0
    both = st_s & st_r
    hs = s['lines'] & both
    out['hatch'] = (float(_grow(r['lines'] & both, 1)[hs].mean()) if hs.any() else
                    (1.0 if not st_s.any() else 0.0))
    # thin white lines (ledges, outlines, railings) away from the other's white, outside
    # the stairs: what reads as a stray or a missing line
    thin_s = thin_lines(s['light']) & outside
    thin_r = thin_lines(r['light']) & outside
    bad = (thin_r & ~_grow(s['light'], LINE_FIT)).sum() + \
        (thin_s & ~_grow(r['light'], LINE_FIT)).sum()
    # over at least LINE_NORM px: on a floor whose art draws no thin lines a few stray
    # pixels took the share to nothing (ScenarioDLC_04: 45 px, 1.0 -> 0.21)
    total = max(int(thin_r.sum() + thin_s.sum()), LINE_NORM)
    out['lines'] = 1.0 - float(bad) / float(total)
    w = WEIGHTS
    out['score'] = (w[0] * out['floor'] + w[1] * out['plate']
                    + w[2] * max(0.0, 1.0 - out['edge'] / EDGE_CAP) + w[3] * out['stairs']
                    + w[4] * out['lines'])
    out = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out.items()}
    return out, s, r, stray, missing


def _vis(a):
    import numpy as np
    al = np.clip(a[..., 3:4] / 255.0 * GAIN, 0, 1)
    return (a[..., :3] * al + 110.0 * (1 - al)).astype('uint8')


def sheet(shipped, regen, s, r, stray, missing, label, path):
    import numpy as np
    from PIL import Image, ImageDraw
    diff = np.full((SIZE, SIZE, 3), 110, 'uint8')
    diff[s['any'] | r['any']] = (80, 80, 80)
    diff[s['plate'] & r['plate']] = (60, 60, 60)
    diff[s['floor'] & r['floor']] = (20, 20, 20)
    diff[s['floor'] & ~r['floor']] = (230, 40, 40)
    diff[r['floor'] & ~s['floor']] = (40, 220, 230)
    diff[_grow(stray, 1)] = (255, 230, 0)
    diff[_grow(missing, 1)] = (255, 0, 255)
    for m, colour in ((s['stairs'], (60, 255, 60)), (r['stairs'], (255, 140, 0))):
        diff[m & _grow(~m, 1)] = colour
    out = Image.new('RGB', (SIZE * 3 + 20, SIZE + 30), (30, 30, 30))
    for k, img in enumerate((_vis(shipped), _vis(regen), diff)):
        out.paste(Image.fromarray(img), (k * (SIZE + 10), 30))
    ImageDraw.Draw(out).text((6, 8), label + '   shipped | regenerated | diff: red shipped '
                             'floor only, cyan regenerated only, yellow stray line, '
                             'magenta missing line, green shipped stairs, orange regenerated stairs', fill=(255, 255, 0))
    out.save(path)


def level_names():
    from riftlord.build.minimap import mapdata
    return sorted(mapdata.maps(include_mods=False))


def run_level(name, cuts, out_dir, log=print, rescore=False, state=None):
    """[result dict] for every floor of `name` under each cut. With `rescore` the
    regenerated pictures already in `out_dir` are scored again, not drawn. With `state`
    ({key: result} and the draw / score digests) drawings whose digests match are
    reused as they are, and those whose drawing matches are only rescored."""
    from riftlord.build.minimap import mapdata
    from riftlord.build.minimap import minimapredraw as mm
    vols, why = mm.sources(name)
    if vols is None:
        log('%s: %s' % (name, why))
        return []
    from riftlord.build.minimap import minimapstyle
    groups = mm.floor_groups(vols)
    bands = mm.floor_bands(vols, groups)
    vstyles = minimapstyle.level_style(vols)
    styles = [vstyles[ks[0]] for ks in groups]
    plan = {'level': name, 'minimap': {'mode': 'regenerate'}}
    t0 = time.time()
    draw_d, score_d = _digest(DRAW_FILES), _digest(SCORE_FILES)
    known = (state or {}).get('results') or {}

    def keyof(k, cut):
        return '%s|%d|%s' % (name, k, cut)

    def image_of(k, cut):
        return os.path.join(out_dir, 'regen_%s_v%d_%s.png' % (name, k, cut))
    reuse = {}
    for cut in cuts:
        for ks in groups:
            got = known.get(keyof(ks[0], cut))
            if got and got.get('draw') == draw_d and os.path.exists(image_of(ks[0], cut)):
                reuse[keyof(ks[0], cut)] = got
    if state is not None and len(reuse) == len(cuts) * len(groups) and \
            all(r.get('scored') == score_d for r in reuse.values()):
        log('%s unchanged' % name)
        return [dict(r) for r in reuse.values()]
    if state is not None and len(reuse) == len(cuts) * len(groups):
        rescore = True
    fl0 = None if rescore else mm.floor(name, plan)
    results = []
    for cut in cuts:
        if fl0 is None or cut == 'doors':
            fl = fl0
        elif cut == 'whole':
            fl = dict(fl0, doors=[])
        elif cut == 'art':
            # the region the shipped art draws, the drawing inside it from the geometry
            # (a comparison only: missions never read the art)
            fl = dict(fl0, doors=[], art_region=True)
        elif cut == 'reached':
            # all walkable floor the spawners, route points, rifts and player starts
            # stand on, no door cut: the mission cut that shows the approaches
            fl = mm.floor_reached(name, fl0)
        else:
            # every polygon of the level's graph, reached or not
            fl = dict(fl0, doors=[], polys=fl0['every'], keep_all=True,
                      poly_floors=mm.source_floors(name, fl0['every']))
        cache = {}
        for g, ks in enumerate(groups):
            k = ks[0]
            src = vols[k]
            alpha = mm.plate_alpha(src)
            ship_img = mapdata.minimap_image(src)
            t = time.time()
            tag = '%s_v%d_%s' % (name, k, cut)
            path = os.path.join(out_dir, 'regen_%s.png' % tag)
            if rescore:
                from PIL import Image
                if not os.path.exists(path):
                    continue
                regen_img = Image.open(path)
            else:
                regen_img = mm.render_floors(name, fl, bands, src, SIZE, alpha, g, cache, styles)
                regen_img.save(path)
            secs = time.time() - t
            shipped, regen = load(ship_img, alpha), load(regen_img, alpha)
            aligned = os.environ.get(ALIGN_ENV) and '%s|%d' % (name, k) in _align_table()
            if aligned:
                shipped = align_art(shipped, _align_table()['%s|%d' % (name, k)])
            res, s, r, stray, missing = score(shipped, regen, alpha)
            label = '%s floor %d/%d (%s) %s' % (name, g + 1, len(groups), cut, json.dumps(res))
            sheet(shipped, regen, s, r, stray, missing, label,
                  os.path.join(out_dir, 'cmp_%s.png' % tag))
            res.update(level=name, volume=k, volumes=ks, floors=len(groups), cut=cut,
                       secs=round(secs, 1), alpha=alpha, draw=draw_d, scored=score_d)
            if aligned:
                res['aligned'] = True
            results.append(res)
            if state is not None:
                state.setdefault('results', {})[keyof(k, cut)] = res
            log(json.dumps(res))
    log('%s done in %.1fs' % (name, time.time() - t0))
    return results


def best_of(results):
    """Per floor, the cut whose drawn region (plate) matches the shipped one best, as
    rows with cut 'best' and `chosen` naming it: vanilla draws the approaches outside
    the doors on some maps and not on others, and a shipped-map comparison is made in
    the region the shipped art shows."""
    by = {}
    for r in results:
        if r['cut'] in ('doors', 'whole', 'all', 'art', 'reached'):
            by.setdefault((r['level'], r['volume']), []).append(r)
    out = []
    for _key, rows in sorted(by.items()):
        if len(rows) < 2:
            continue
        pick = max(rows, key=lambda r: r['plate'])
        out.append(dict(pick, cut='best', chosen=pick['cut']))
    return out


TABLE_COLS = ('score', 'floor', 'plate', 'edge', 'lines', 'stray', 'missing', 'stairs',
              'hatch')


def table(results, cut):
    rows = [r for r in results if r['cut'] == cut]
    rows.sort(key=lambda r: (r['level'], r['volume']))
    cols = TABLE_COLS + ('secs',)
    out = ['| level | floor | ' + ' | '.join(cols) + ' |',
           '|---|---|' + '---|' * len(cols)]
    for r in rows:
        out.append('| %s | %d | %s |' % (r['level'], r['volume'],
                                         ' | '.join(str(r.get(c)) for c in cols)))
    if rows:
        mean = {c: sum(r.get(c) or 0 for r in rows) / len(rows) for c in cols}
        out.append('| **mean** | %d | %s |' % (len(rows), ' | '.join(
            '%.3f' % mean[c] if isinstance(mean[c], float) else str(mean[c])
            for c in cols)))
    return '\n'.join(out)


#: A floor whose draw time grew past SLOWER_SHARE times the last promoted round's, and by
#: at least SLOWER_SECS, is named at promote time (times are wall clock: load skews them).
SLOWER_SHARE = 1.5
SLOWER_SECS = 20.0


def draw_times(rows):
    """{level|floor: seconds} drawing each floor took, summed over the cuts, from result
    rows (their `secs`)."""
    out = {}
    for r in rows:
        if r.get('cut') in ('doors', 'whole', 'art', 'reached') and r.get('secs') is not None:
            key = '%s|%d' % (r['level'], r['volume'])
            out[key] = out.get(key, 0.0) + float(r['secs'])
    return out


def time_note(rows, before_rows=None):
    """One line: the total draw time, the slowest floor, and the floors that grew slower
    than in `before_rows` (SLOWER_SHARE, SLOWER_SECS)."""
    now = draw_times(rows)
    if not now:
        return ''
    slow = max(now, key=now.get)
    line = 'draw %.0f s, slowest %s %.0f s' % (sum(now.values()), slow, now[slow])
    if before_rows:
        then = draw_times(before_rows)
        worse = sorted((k for k in now if k in then and now[k] > SLOWER_SHARE * then[k]
                        and now[k] - then[k] >= SLOWER_SECS),
                       key=lambda k: then[k] - now[k])
        if worse:
            line += '; slower: ' + ', '.join('%s %.0f->%.0f s' % (k, then[k], now[k])
                                             for k in worse[:6])
    return line


def _load_json(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _save_json(path, data):
    tmp = path + '.part'
    with open(tmp, 'w') as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, path)


def _best_rows(results):
    return {'%s|%d' % (r['level'], r['volume']): r for r in results if r['cut'] == 'best'}


#: The cuts a mission can draw (geometry only, the same pipeline for every mission), each
#: tracked per floor in the baseline and the best so far ('level|k|cut'): `reached` (all
#: reachable floor: what missions draw, user 2026-10-24; its score is the finish line) and
#: `doors` (a mission's Cut at Doors toggle). A drop on either fails the gate and promote.
#: The art cut and the best of the cuts are diagnostics.
TRACKED = ('reached', 'doors')


def _tracked_rows(results):
    """{level|k|cut: row} of the TRACKED cuts among result rows."""
    return {'%s|%d|%s' % (r['level'], r['volume'], r['cut']): r for r in results
            if r.get('cut') in TRACKED}


def _means(rows):
    """'doors 0.686 (0 at >= 0.98), reached ...' of {level|k|cut: row}."""
    out = []
    for cut in TRACKED:
        got = [r['score'] for k, r in rows.items() if k.endswith('|' + cut)]
        if got:
            out.append('%s %.3f (%d at >= %.2f)' % (cut, sum(got) / len(got),
                                                     sum(1 for s in got if s >= TARGET), TARGET))
    return ', '.join(out)


def compare(best, baseline):
    """[(key, before, after)] of every floor in both, worst drop first."""
    rows = [(k, baseline[k]['score'], best[k]['score']) for k in best if k in baseline]
    return sorted(rows, key=lambda r: r[2] - r[1])


def worst_sheet(results, baseline, out_dir, path):
    """One review image: the WORST_N lowest-scoring drawings of the TRACKED cuts as
    shipped | regenerated | difference, then the largest changes against the baseline."""
    from PIL import Image, ImageDraw
    best = _tracked_rows(results)
    worst = sorted(best.values(), key=lambda r: r['score'])[:WORST_N]
    changed = [c for c in compare(best, baseline) if abs(c[2] - c[1]) > 1e-4]
    changed = (changed[:CHANGED_N // 2] + changed[-(CHANGED_N - CHANGED_N // 2):]
               if len(changed) > CHANGED_N else changed)
    rows = [('worst', r) for r in worst] + [('changed', best[c[0]]) for c in changed]
    h = THUMB + 22
    out = Image.new('RGB', (THUMB * 3 + 20, max(1, len(rows)) * h + 30), (30, 30, 30))
    pen = ImageDraw.Draw(out)
    n_ok = sum(1 for r in best.values() if r['score'] >= TARGET)
    pen.text((6, 8), 'worst %d of %d drawings (%s), then changes against the baseline; '
             '%d at >= %.2f' % (len(worst), len(best), '/'.join(TRACKED), n_ok, TARGET),
             fill=(255, 255, 0))
    for i, (kind, r) in enumerate(rows):
        y = 30 + i * h
        cmp_path = os.path.join(out_dir, 'cmp_%s_v%d_%s.png' % (r['level'], r['volume'],
                                                                 r['cut']))
        key = '%s|%d|%s' % (r['level'], r['volume'], r['cut'])
        was = baseline.get(key, {}).get('score')
        pen.text((6, y + 4), '%s  %s floor %d (%s)  score %.3f%s  floor %.3f plate %.3f edge '
                 '%.1f stray %d missing %d stairs %.2f' % (
                     kind, r['level'], r['volume'], r['cut'], r['score'],
                     '' if was is None else ' (baseline %.3f)' % was, r['floor'], r['plate'],
                     r['edge'], r['stray'], r['missing'], r['stairs']), fill=(230, 230, 230))
        try:
            im = Image.open(cmp_path).convert('RGB')
            im = im.crop((0, 30, im.size[0], im.size[1])).resize((THUMB * 3 + 20, THUMB))
            out.paste(im, (0, y + 20))
        except OSError:
            pass
    out.save(path)


def apply_sets(sets):
    """Set module constants from 'package.module.NAME=VALUE' strings (VALUE read as JSON,
    else as text), for a sweep. Returns [(target, value)]."""
    import importlib
    done = []
    for spec in sets or []:
        target, _eq, raw = spec.partition('=')
        module, _dot, name = target.rpartition('.')
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw
        mod = importlib.import_module(module)
        if not hasattr(mod, name):
            raise SystemExit('%s has no %s' % (module, name))
        setattr(mod, name, value)
        done.append((target, value))
    return done


def _level_job(job):
    """One level in a worker process: (name, results, the state entries it wrote)."""
    name, cuts, out_dir, rescore, state, sets = job
    os.nice(0)
    _use_harness_cache()
    apply_sets(sets)
    mine = {'results': {k: v for k, v in (state.get('results') or {}).items()
                        if k.startswith(name + '|')}}
    lines = []
    try:
        res = run_level(name, cuts, out_dir, log=lines.append, rescore=rescore, state=mine)
    except (Exception, SystemExit) as exc:            # noqa: BLE001
        lines.append('%s failed: %s: %s' % (name, type(exc).__name__, exc))
        res = []
    return name, res, mine.get('results') or {}, lines


def run_levels(names, cuts, out_dir, rescore, state, sets, workers, save):
    """Every level's results, levels drawn WORKERS at a time in their own processes (each
    about 1 GB), state saved after each level."""
    results = []
    jobs = [(n, cuts, out_dir, rescore, state, sets) for n in names]
    if workers <= 1 or len(names) <= 1:
        done = map(_level_job, jobs)
        pool = None
    else:
        import multiprocessing
        pool = multiprocessing.get_context('fork').Pool(min(workers, len(names)))
        done = pool.imap_unordered(_level_job, jobs)
    try:
        for name, res, entries, lines in done:
            for line in lines:
                print(line, flush=True)
            results += res
            state.setdefault('results', {}).update(entries)
            save()
    finally:
        if pool is not None:
            pool.close()
            pool.join()
    return results


def _sheet_tile(path, size):
    from PIL import Image
    import numpy as np
    if not path or not os.path.exists(path):
        return Image.new('RGB', (size, size), (40, 40, 40))
    a = np.asarray(Image.open(path).convert('RGBA').resize((size, size), Image.BOX)).astype(float)
    al = np.clip(a[..., 3:4] / 255.0 * GAIN, 0, 1)
    return Image.fromarray((a[..., :3] * al + 110.0 * (1 - al)).astype('uint8'))


def _scores_in(out_dir):
    state = _load_json(os.path.join(out_dir, 'state.json'), {}) if out_dir else {}
    best = _best_rows(best_of(list((state.get('results') or {}).values())))
    return best


def make_sheet(levels, before, after, cut='best', size=SHEET_TILE, record=None, out=None):
    """One labelled image: per floor of each level, vanilla | before | after (the
    regenerated pictures in the BEFORE and AFTER run folders, `cut` or each floor's best
    cut there); with `record`, first that mission's Cut | Regenerate in Cut's frame."""
    from PIL import Image, ImageDraw
    from riftlord.build.minimap import mapdata
    from riftlord.build.minimap import minimapredraw as mm
    rows = []
    if record:
        rows.append(_record_row(record, size))
    sb, sa = _scores_in(before), _scores_in(after)
    for name in levels:
        vols, _why = mm.sources(name)
        for ks in mm.floor_groups(vols or []):
            k = ks[0]
            key = '%s|%d' % (name, k)
            tiles, labels = [], []
            tmp = os.path.join(after or before or OUT, '_vanilla_%s_v%d.png' % (name, k))
            mapdata.minimap_image(vols[k]).save(tmp)
            tiles.append(_sheet_tile(tmp, size))
            labels.append('%s floor %d vanilla' % (name, k))
            for tag, d, sc in (('before', before, sb), ('after', after, sa)):
                if not d:
                    continue
                c = cut if cut != 'best' else (sc.get(key) or {}).get('chosen', 'doors')
                tiles.append(_sheet_tile(os.path.join(d, 'regen_%s_v%d_%s.png' % (name, k, c)), size))
                s = (sc.get(key) or {}).get('score')
                labels.append('%s (%s)%s' % (tag, c, '' if s is None else ' %.3f' % s))
            rows.append((tiles, labels))
    cols = max(len(t) for t, _l in rows) if rows else 1
    img = Image.new('RGB', (cols * (size + 6), max(1, len(rows)) * (size + 20)), (30, 30, 30))
    pen = ImageDraw.Draw(img)
    for r, (tiles, labels) in enumerate(rows):
        for c, (tile, label) in enumerate(zip(tiles, labels)):
            x, y = c * (size + 6), r * (size + 20)
            img.paste(tile, (x, y + 18))
            pen.text((x + 2, y + 3), label, fill=(255, 255, 0))
    path = out or os.path.join(after or before or OUT, 'sheet_%s.png' % '_'.join(
        ([record] if record else []) + list(levels))[:80])
    img.save(path)
    return path


def _record_row(record, size):
    """(tiles, labels): a mission's Cut and its Regenerate drawn in Cut's frame."""
    import base64
    import io
    from PIL import Image
    from riftlord.build.minimap import minimapredraw as mm
    from riftlord.build.minimap import minimapstyle
    from riftlord.core import gamepath as gp
    path = os.path.join(gp.user_data('Missions'), '.riftlord', 'records', record + '.json')
    rec = _load_json(path, {})
    plan = rec.get('plan') or {}
    level = rec.get('level') or plan.get('level')
    cut = mm._draw_preview(level, dict(plan, minimap={'mode': 'cut'}), size)
    if cut.get('error'):
        raise SystemExit(cut['error'])
    cimg = Image.open(io.BytesIO(base64.b64decode(cut['images'][0].split(',', 1)[1])))
    rplan = dict(plan, minimap=dict(mm.clean(plan.get('minimap')), mode='regenerate'))
    fl = mm.floor(level, rplan)
    if not mm.cut_at_doors(rplan):
        fl = mm.floor_reached(level, fl)
    vols, _why = mm.sources(level)
    groups = mm.floor_groups(vols)
    styles = [minimapstyle.level_style(vols)[ks[0]] for ks in groups]
    frame = dict(vols[0], translation=tuple(cut['volumes'][0]['translation']),
                 extent=tuple(cut['volumes'][0]['extent']))
    rimg = mm._shown(mm.render_floors(level, fl, mm.floor_bands(vols, groups), frame, size,
                                      styles[0]['alpha'], 0, {}, styles), size)
    tiles = []
    for im in (cimg, rimg):
        bg = Image.new('RGBA', im.size, (62, 79, 96, 255))
        bg.alpha_composite(im.convert('RGBA'))
        tiles.append(bg.convert('RGB'))
    return tiles, ['%s Cut' % record, '%s Regenerate (Cut frame)' % record]


def inspect(level, floor=0, at=None, box=None, cut='whole', out_dir=OUT):
    """Print what the drawing of `level` floor `floor` holds at texture pixels (1024):
    graph plane height, final height and floor / hole / void / stairs / wall flags at
    `at` (u, v) or along the middle row and column of `box` (u0, v0, u1, v1), and save
    inspect_<level>_v<floor>.png (heights and flags | vanilla) over the box."""
    import numpy as np
    from PIL import Image
    from riftlord.build.minimap import mapdata
    from riftlord.build.minimap import minimapredraw as mm
    vols, _why = mm.sources(level)
    groups = mm.floor_groups(vols)
    g = next(i for i, ks in enumerate(groups) if floor in ks)
    plan = {'level': level, 'minimap': {'mode': 'regenerate'}}
    fl0 = mm.floor(level, plan)
    fl0 = fl0 if cut == 'doors' else dict(fl0, doors=[], art_region=(cut == 'art'))
    fl = mm.on_floor(fl0, *mm.floor_bands(vols, groups)[g], g=g)
    seen = {}
    real = mm._walls

    def spy(idx, heights, grid, size, reach):
        w, t = real(idx, heights, grid, size, reach)
        seen['plane'] = np.where(idx > 0, heights, np.nan)
        seen['walls'] = w
        return w, t
    mm._walls = spy
    try:
        lay = mm.layers(fl, vols[floor], SIZE)
    finally:
        mm._walls = real
    z, plane = lay['z'], seen.get('plane')

    def row(u, v):
        f = bool(lay['floor'][v, u])
        return ('(%d,%d) floor %s plane %s z %s hole %s void %s stairs %s wall %s' % (
            u, v, f, None if plane is None or np.isnan(plane[v, u]) else int(plane[v, u]),
            int(z[v, u]) if f else None, bool(lay['holes'][v, u]), bool(lay['voids'][v, u]),
            bool(lay['stairs'][v, u]), bool(seen.get('walls', np.zeros((1, 1), bool))[v, u]
                                           if 'walls' in seen else False)))
    points = []
    if at:
        points = [tuple(at)]
    if box:
        u0, v0, u1, v1 = box
        vc, uc = (v0 + v1) // 2, (u0 + u1) // 2
        step = max(1, (u1 - u0) // 24)
        points += [(u, vc) for u in range(u0, u1, step)]
        points += [(uc, v) for v in range(v0, v1, max(1, (v1 - v0) // 24))]
    for u, v in points:
        print(row(u, v))
    u0, v0, u1, v1 = box or (0, 0, SIZE, SIZE)
    zz = np.where(lay['floor'], z, np.nan)
    lo, hi = np.nanmin(zz), np.nanmax(zz)
    t = np.clip((zz - lo) / max(1.0, hi - lo), 0, 1)
    rgb = np.zeros((SIZE, SIZE, 3), np.uint8)
    ok = ~np.isnan(zz)
    rgb[..., 0][ok] = (40 + 200 * t[ok]).astype(np.uint8)
    rgb[..., 1][ok] = (200 - 150 * t[ok]).astype(np.uint8)
    rgb[..., 2][ok] = 90
    if 'walls' in seen:
        rgb[seen['walls']] = (255, 0, 0)
    rgb[lay['holes']] = (255, 255, 255)
    rgb[lay['voids']] = (60, 60, 160)
    rgb[lay['stairs'] & lay['floor']] = (rgb[lay['stairs'] & lay['floor']] // 2 + 100)
    a = np.asarray(mapdata.minimap_image(vols[floor]).convert('RGBA').resize((SIZE, SIZE), Image.BOX)).astype(float)
    al = np.clip(a[..., 3:4] / 255.0 * GAIN, 0, 1)
    van = (a[..., :3] * al + 110 * (1 - al)).astype(np.uint8)
    scale = max(1, 600 // max(u1 - u0, v1 - v0))
    left = Image.fromarray(rgb[v0:v1, u0:u1]).resize(((u1 - u0) * scale, (v1 - v0) * scale), Image.NEAREST)
    right = Image.fromarray(van[v0:v1, u0:u1]).resize(left.size, Image.NEAREST)
    img = Image.new('RGB', (left.size[0] * 2 + 10, left.size[1]), (30, 30, 30))
    img.paste(left, (0, 0))
    img.paste(right, (left.size[0] + 10, 0))
    path = os.path.join(out_dir, 'inspect_%s_v%d.png' % (level, floor))
    img.save(path)
    print('z range %s..%s; picture %s (heights: green low to red high; red walls, white '
          'holes, blue voids, light stairs | vanilla)' % (lo, hi, path))
    return path


def _ints(text):
    return [int(v) for v in text.split(',')] if text else None


def subcommand(argv):
    """`sheet` and `inspect` (see the module docstring)."""
    ap = argparse.ArgumentParser(prog='minimapscore ' + argv[0])
    ap.add_argument('levels', nargs='*')
    ap.add_argument('--out', default=OUT)
    if argv[0] == 'sheet':
        ap.add_argument('--before')
        ap.add_argument('--after')
        ap.add_argument('--cut', default='best')
        ap.add_argument('--record', help='a Missions record: its Cut | Regenerate first')
        args = ap.parse_args(argv[1:])
        print(make_sheet(args.levels, args.before, args.after or args.out, args.cut,
                         record=args.record))
        return 0
    ap.add_argument('--floor', type=int, default=0)
    ap.add_argument('--at')
    ap.add_argument('--box')
    ap.add_argument('--cut', default='whole', choices=('doors', 'whole', 'art'))
    args = ap.parse_args(argv[1:])
    _use_harness_cache()
    for name in args.levels:
        inspect(name, args.floor, _ints(args.at), _ints(args.box), args.cut, args.out)
    return 0


def main(argv=None):
    """The harness, inside one of the machine's heavy-job slots (docs/HEAVY-JOBS.md)."""
    from riftlord.core import heavyslot
    with heavyslot.slot('minimapscore'):
        return _main(argv)


def _main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == 'promote':
        ap = argparse.ArgumentParser(prog='minimapscore promote')
        ap.add_argument('--note', required=True)
        ap.add_argument('--allow', default='', help='floors (level|k) whose drop is accepted')
        ap.add_argument('--seed-best', action='append', default=[], metavar='SUMMARY_JSON',
                        help="an earlier run's summary.json whose scores count toward each "
                             "floor's best so far (BEST)")
        ap.add_argument('--rescore', action='append', default=[], metavar='DIR',
                        help='the scorer changed: score the baseline (the tree\'s pictures) '
                             'again, and rebuild the best so far from those and the pictures '
                             'in each DIR')
        ap.add_argument('--reset', action='store_true',
                        help='a new measure: this run becomes the baseline and the best so far '
                             '(every floor under every TRACKED cut); no drop checks')
        args = ap.parse_args(argv[1:])
        return promote(args.note, tuple(a for a in args.allow.split(',') if a),
                       tuple(args.seed_best), tuple(args.rescore), args.reset)
    if argv and argv[0] in ('sheet', 'inspect'):
        return subcommand(argv)
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('levels', nargs='*')
    ap.add_argument('--cut', choices=('doors', 'whole', 'all', 'art', 'reached', 'both',
                                      'mission', 'three'),
                    default='mission',
                    help='mission: the TRACKED cuts (doors, reached); three: those and the '
                         'art cut (a comparison in the region vanilla draws)')
    ap.add_argument('--out', default=OUT)
    ap.add_argument('--rescore', action='store_true',
                    help='score the regenerated pictures already in --out again')
    ap.add_argument('--force', action='store_true', help='redraw every drawing')
    ap.add_argument('--warm', action='store_true',
                    help="build every level's solid grids for the harness, then stop")
    ap.add_argument('--gate', action='store_true',
                    help='fail when a floor scores more than GATE_TOLERANCE under the baseline')
    ap.add_argument('--accept', action='store_true', help='this run becomes the baseline')
    ap.add_argument('--note', default='', help='a word on what changed, for runs.log')
    ap.add_argument('--set', action='append', default=[], metavar='MODULE.NAME=VALUE',
                    help='a sweep: set a constant; drawn fresh into a separate folder')
    ap.add_argument('--workers', type=int, default=WORKERS,
                    help='levels drawn at once, each in a separate process (~1 GB each)')
    ap.add_argument('--align', action='store_true',
                    help='score the floors in minimapredraw.ART_ALIGN against their art laid on the MapData '
                         'frame; into a separate folder, never the baseline')
    args = ap.parse_args(argv)
    if args.accept and os.environ.get(DEV_ENV):
        raise SystemExit('a development copy cannot --accept: copy it into the tree first')
    if args.align:
        if args.accept:
            raise SystemExit('--align scores against moved art: it cannot --accept')
        os.environ[ALIGN_ENV] = '1'
        args.out = os.path.join(args.out, 'aligned')
        print('aligned art on %s -> %s' % (', '.join(sorted(_align_table())), args.out))
    if args.set:
        if args.accept or args.gate:
            raise SystemExit('--set is a sweep: it cannot --accept or --gate')
        import hashlib
        tag_ = hashlib.sha1('\n'.join(sorted(args.set)).encode()).hexdigest()[:8]
        args.out = os.path.join(args.out, 'sweep-%s' % tag_)
        print('sweep %s -> %s' % (', '.join(args.set), args.out))
    os.makedirs(args.out, exist_ok=True)
    _use_harness_cache()
    apply_sets(args.set)
    # the code digests are fixed here, before any worker starts
    _digest(DRAW_FILES), _digest(SCORE_FILES)
    cuts = {'both': ('doors', 'whole'), 'mission': TRACKED,
            'three': TRACKED + ('art',)}.get(args.cut, (args.cut,))
    names = args.levels or level_names()
    if args.warm:
        return warm(names)
    state_path = os.path.join(args.out, 'state.json')
    state = {} if args.force else _load_json(state_path, {})
    results = run_levels(names, cuts, args.out, args.rescore, state, args.set, args.workers,
                         lambda: _save_json(state_path, state))
    results += best_of(results)
    if any(r['cut'] == 'best' for r in results):
        cuts = tuple(cuts) + ('best',)
    tag = '' if not args.levels else '_' + '_'.join(args.levels)[:60]
    _save_json(os.path.join(args.out, 'summary%s.json' % tag), results)
    with open(os.path.join(args.out, 'summary%s.md' % tag), 'w') as fh:
        for cut in cuts:
            fh.write('## cut: %s\n\n%s\n\n' % (cut, table(results, cut)))
    for cut in cuts:
        print('\ncut:', cut)
        print(table(results, cut))
    baseline = _load_json(BASELINE, {})
    best = _tracked_rows(results)
    worst_sheet(results, baseline, args.out, os.path.join(args.out, 'worst%s.png' % tag))
    drops = [c for c in compare(best, baseline) if c[2] < c[1] - GATE_TOLERANCE]
    floors = len({k.rsplit('|', 1)[0] for k in best})
    timing = time_note([r for r in results if r.get('cut') != 'best'])
    line = '%s  floors %d  %s  below baseline %d%s%s%s' % (
        time.strftime('%Y-%m-%d %H:%M'), floors, _means(best), len(drops),
        ('  worst drop %s %.3f->%.3f' % drops[0]) if drops else '',
        ('  [%s]' % timing) if timing else '',
        ('  -- ' + args.note) if args.note else '')
    with open(os.path.join(args.out, 'runs.log'), 'a') as fh:
        fh.write(line + '\n')
    print('\n' + line)
    if args.accept:
        if args.levels:
            baseline.update({k: {kk: r[kk] for kk in TABLE_COLS} for k, r in best.items()})
        else:
            baseline = {k: {kk: r[kk] for kk in TABLE_COLS} for k, r in best.items()}
        _save_json(BASELINE, baseline)
        print('baseline written: %s (%d drawings)' % (BASELINE, len(baseline)))
    if args.gate and drops:
        for k, before, after in drops:
            print('BELOW BASELINE %s %.3f -> %.3f' % (k, before, after))
        return 1
    return 0


#: Where a promotion puts the tree's harness state (the tree's own OUT).
TREE_OUT = '/mnt/2tb_pro_a/tmp/minimapstyle2/inc'
#: Files of the development copy that stay there.
DEV_ONLY = ('devrun.py',)


def promote(note, allow=(), seed=(), rescore=(), reset=False):
    """Install this development copy in the tree as a whole accepted round: its last
    full run (DEV_OUT, made by devrun.py with this copy's own code digests) must cover
    every floor and cut with the current digests and sit no floor under the tree's
    baseline except those in `allow`. The images go first; then the code files, the
    baseline and the state are written under temporary names and renamed into place one
    after another, so the tree's code and its baseline never disagree for longer than
    those renames."""
    import shutil
    if not os.environ.get(DEV_ENV):
        raise SystemExit('promote runs from a development copy (devrun.py promote ...)')
    here = os.path.dirname(os.path.abspath(__file__))
    tree = os.path.join(gamepath.REPO_ROOT, 'riftlord', 'build', 'minimap')
    if os.path.realpath(here) == os.path.realpath(tree):
        raise SystemExit('this is the tree itself')
    state = _load_json(os.path.join(DEV_OUT, 'state.json'), {})
    rows = list((state.get('results') or {}).values())
    draw_d, score_d = _digest(DRAW_FILES), _digest(SCORE_FILES)
    stale = [r for r in rows if r.get('draw') != draw_d or r.get('scored') != score_d]
    if not rows or stale:
        raise SystemExit('the last development run is not of this code (%d of %d drawings '
                         'stale): run devrun.py --cut three first' % (len(stale), len(rows)))
    best = _tracked_rows(rows)
    baseline = _load_json(BASELINE, {})
    if reset:
        # a new measure (user rule 2026-10-24, geometry only, the TRACKED mission cuts):
        # this run becomes the baseline and the best so far; every floor and cut it holds
        want = {'%s|%d|%s' % (r['level'], r['volume'], c) for r in rows for c in TRACKED}
        lacking = sorted(want - set(best))
        if lacking or len(best) < len(TRACKED) * len({k.rsplit('|', 1)[0] for k in baseline
                                                       if k.count('|') == 1} or [0]):
            raise SystemExit('a reset needs every floor under every tracked cut (%s): '
                             'lacking %s' % ('/'.join(TRACKED), ', '.join(lacking[:6]) or
                                             'floors of the old baseline'))
        baseline, record = {}, {}
    missing = sorted(set(baseline) - set(best))
    if missing:
        raise SystemExit('the development run lacks %d drawing(s): %s' % (
            len(missing), ', '.join(missing[:6])))
    record = {} if reset else _load_json(BEST, {})
    if rescore:
        # the scorer changed: the baseline's drawings (the tree's own pictures) and the
        # best-so-far's (those and the folders named) are scored again with this scorer
        old_base, old_record = baseline, record
        base_rows = rescore_dir(TREE_OUT, print)
        lost = sorted(set(old_base) - set(base_rows))
        if lost:
            raise SystemExit('the tree has no pictures for %d floor(s): %s' % (
                len(lost), ', '.join(lost[:6])))
        baseline = {k: {kk: r[kk] for kk in TABLE_COLS} for k, r in base_rows.items()}
        record = {k: {'score': r['score'], 'when': 'baseline (rescored)'}
                  for k, r in base_rows.items()}
        for d in rescore:
            for k, r in rescore_dir(d, print).items():
                if k not in record or r['score'] > record[k]['score']:
                    record[k] = {'score': r['score'],
                                 'when': '%s (rescored)' % os.path.basename(d.rstrip('/'))}
        lines = ['| floor | baseline | rescored | best | rescored |', '|---|---|---|---|---|']
        for k in sorted(set(old_base) | set(baseline)):
            lines.append('| %s | %s | %s | %s | %s |' % (
                k, (old_base.get(k) or {}).get('score'), (baseline.get(k) or {}).get('score'),
                (old_record.get(k) or {}).get('score'), (record.get(k) or {}).get('score')))
        with open(os.path.join(DEV_OUT, 'rescore.md'), 'w') as fh:
            fh.write('\n'.join(lines) + '\n')
        print('rescored baseline and best so far: %s' % os.path.join(DEV_OUT, 'rescore.md'))
    drops = [c for c in compare(best, baseline) if c[2] < c[1] - GATE_TOLERANCE]
    # each floor's best promoted score so far: the record, the baseline, and any earlier
    # runs' summaries given to seed it
    tops = {k: dict(v) for k, v in record.items()}
    for k, v in baseline.items():
        if k not in tops or v['score'] > tops[k]['score']:
            tops[k] = {'score': v['score'], 'when': 'baseline'}
    for path in seed:
        for k, r in _tracked_rows(_load_json(path, [])).items():
            if k not in tops or r['score'] > tops[k]['score']:
                tops[k] = {'score': r['score'], 'when': os.path.basename(os.path.dirname(path))}
    under_best = sorted(((k, tops[k]['score'], best[k]['score']) for k in best if k in tops
                         and best[k]['score'] < tops[k]['score'] - GATE_TOLERANCE),
                        key=lambda c: c[2] - c[1])
    drops += [c for c in under_best if c[0] not in {d[0] for d in drops}]
    unlisted = [c for c in drops if c[0] not in allow]
    if unlisted:
        raise SystemExit('below the baseline or its best so far, and not accepted: ' +
                         ', '.join('%s %.3f->%.3f' % c for c in unlisted))
    drift = sorted(((k, tops[k]['score'], best[k]['score']) for k in best if k in tops
                    and best[k]['score'] < tops[k]['score'] - 0.0005),
                   key=lambda c: c[2] - c[1])
    stamp = time.strftime('%Y-%m-%d %H:%M')
    new_best = dict(tops)
    for k, r in best.items():
        if k not in new_best or r['score'] > new_best[k]['score']:
            new_best[k] = {'score': r['score'], 'when': stamp}
    new_base = {k: {kk: r[kk] for kk in TABLE_COLS} for k, r in best.items()}
    before_rows = list((_load_json(os.path.join(TREE_OUT, 'state.json'), {})
                        .get('results') or {}).values())
    timing = time_note(rows, before_rows)
    os.makedirs(TREE_OUT, exist_ok=True)
    for name in os.listdir(DEV_OUT):
        if name.endswith('.png') and name.startswith(('regen_', 'cmp_')):
            shutil.copy2(os.path.join(DEV_OUT, name), os.path.join(TREE_OUT, name))
    moves = []
    for name in sorted(os.listdir(here)):
        if not name.endswith('.py') or name in DEV_ONLY:
            continue
        src, dst = os.path.join(here, name), os.path.join(tree, name)
        with open(src, 'rb') as fh:
            data = fh.read()
        if os.path.exists(dst):
            with open(dst, 'rb') as fh:
                if fh.read() == data:
                    continue
        tmp = os.path.join(tree, '.%s.promote' % name)
        with open(tmp, 'wb') as fh:
            fh.write(data)
        moves.append((tmp, dst))
    for path, payload in ((BASELINE, new_base), (BEST, new_best),
                          (os.path.join(TREE_OUT, 'state.json'), state)):
        tmp = path + '.promote'
        with open(tmp, 'w') as fh:
            json.dump(payload, fh, indent=1)
        moves.append((tmp, path))
    for tmp, dst in moves:
        os.replace(tmp, dst)
    floors = len({k.rsplit('|', 1)[0] for k in best})
    line = '%s  floors %d  %s  below baseline %d  [%s]  -- promoted%s: %s%s' % (
        time.strftime('%Y-%m-%d %H:%M'), floors, _means(best), len(drops), timing,
        ' (baseline and best so far reset)' if reset else '', note,
        ('; drops accepted: ' + ', '.join('%s %.3f->%.3f' % c for c in drops)) if drops
        else '')
    drift_line = ('drift under best: %d floor(s), %s' % (len(drift), ', '.join(
        '%s %.4f->%.4f (%+.4f)' % (k, b, a, a - b) for k, b, a in drift)) if drift
        else 'drift under best: none')
    for out in (TREE_OUT, DEV_OUT):
        with open(os.path.join(out, 'runs.log'), 'a') as fh:
            fh.write(line + '\n    ' + drift_line + '\n')
    print(line)
    print(drift_line)
    for tmp, dst in moves:
        print('installed %s' % dst)
    return 0


def rescore_dir(d, log=None):
    """{level|k|cut: row} of the TRACKED cuts' regenerated pictures in folder `d` scored
    with this scorer, without drawing: for a scorer change, the baseline's and the
    best-so-far's drawings scored as the new run is."""
    import re
    from PIL import Image
    from riftlord.build.minimap import mapdata
    from riftlord.build.minimap import minimapredraw as mm
    pat = re.compile(r'^regen_(.+)_v(\d+)_(%s)\.png$' % '|'.join(TRACKED))
    found = {}
    for name in sorted(os.listdir(d)):
        m = pat.match(name)
        if m:
            found.setdefault(m.group(1), []).append((int(m.group(2)), m.group(3), name))
    rows = []
    for level, items in sorted(found.items()):
        vols, _why = mm.sources(level)
        if not vols:
            continue
        for k, cut, name in items:
            if k >= len(vols):
                continue
            alpha = mm.plate_alpha(vols[k])
            shipped = load(mapdata.minimap_image(vols[k]), alpha)
            res = score(shipped, load(Image.open(os.path.join(d, name)), alpha), alpha)[0]
            res.update(level=level, volume=k, cut=cut)
            rows.append(res)
        if log:
            log('%s rescored in %s' % (level, d))
    return _tracked_rows(rows)


def gate_from_state(out_dir=OUT):
    """(ok, detail, fresh) of the last harness run against the baseline, without drawing:
    `fresh` is False when the drawing or scoring code changed since that run (its
    scores no longer describe the code), and the check then says so instead of passing."""
    state = _load_json(os.path.join(out_dir, 'state.json'), {})
    rows = list((state.get('results') or {}).values())
    if not rows:
        return False, 'no harness run in %s' % out_dir, False
    draw_d, score_d = _digest(DRAW_FILES), _digest(SCORE_FILES)
    best = _tracked_rows(rows)
    fresh = all(r.get('draw') == draw_d and r.get('scored') == score_d for r in best.values())
    baseline = _load_json(BASELINE, {})
    drops = [c for c in compare(best, baseline) if c[2] < c[1] - GATE_TOLERANCE]
    detail = '%d floors: %s; %d below the baseline%s%s' % (
        len({k.rsplit('|', 1)[0] for k in best}), _means(best), len(drops),
        ('; worst %s %.3f->%.3f' % drops[0]) if drops else '',
        '' if fresh else '; the code changed since the last harness run')
    return (not drops) and fresh, detail, fresh


def warm(names):
    """Every level's solid grid at the harness's drawing size, each floor's frame."""
    from riftlord.build.minimap import mapdata
    from riftlord.build.minimap import minimapredraw as mm
    for name in names:
        t = time.time()
        try:
            vols, _why = mm.sources(name)
            if not vols:
                continue
            fl = mm.floor(name, {'level': name, 'minimap': {'mode': 'regenerate'}})
            n = SIZE * mm.SUPERSAMPLE
            for vol in vols:
                mm._solid_grid(fl, mapdata.projector(vol), n, 1.0)
            print('%s warm in %.1fs' % (name, time.time() - t), flush=True)
        except (Exception, SystemExit) as exc:        # noqa: BLE001
            print('%s failed: %s' % (name, exc), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
