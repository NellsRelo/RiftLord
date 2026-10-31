"""A Map Editor mission's minimap: the source level's, cut, or regenerated.

Plan block `minimap`:

* absent or {}: **Source Level**, the source level's shipped minimap, unchanged.
* {"mode": "cut"}: **Cut**, the shipped minimap cut at the doors to the floor the rift
  reaches (riftlord/minimapcut.py).
* {"mode": "regenerate"}: **Regenerate**, drawn at build from the mission's final
  geometry in the shipped minimaps' style, cut at the doors to the floor the rift
  reaches, framed on what is left and turned as the source level's frame.
  `area` [x0, y0, x1, y1] (world uu) keeps only the floor inside it.

Records written before the modes carry {redraw, reframe, cut, area}: `cut` reads as Cut,
`redraw` alone as Regenerate.

The style constants below were measured on the shipped minimaps; the catalogue and the
measurements are in workshop/research/MINIMAP-STYLE.md.
"""

import base64
import copy
import functools
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

#: The three modes, as saved in `plan.minimap.mode` (Source Level is the absent block).
MODES = ('source', 'cut', 'regenerate')

# -- geometry ----------------------------------------------------------------------

#: A piece holds up walkable floor whose height is within this far above its top, in uu.
SUPPORT_BELOW = 120.0
#: ... or within this far below its top, in uu.
SUPPORT_ABOVE = 60.0
#: An anchor (route point, rift, player start) stands on a polygon whose floor is at
#: most this far below it, in uu.
ANCHOR_ABOVE = 400.0
#: ... or this far above it, in uu.
ANCHOR_BELOW = 60.0
#: An anchor off the drawn floor joins the floor within this distance, in uu.
ANCHOR_REACH = 150.0

# -- the shipped style, measured (workshop/research/MINIMAP-STYLE.md) -----------------
# Widths are shares of the texture width, measured on the base game's 1024 textures
# (each the 2048 source art box-filtered to half, which is where their one-pixel
# antialiased edge comes from).

#: Outer wall band: 12 px at 1024 on 10 of the 13 single- and multi-floor base-game
#: minimaps whose band could be read (Scenario_02/04/07/08/09/10/11/13/14/16), 24 px on
#: the 2048 Scenario_Other_02/03.
BAND = 12 / 1024.0
#: Ledge line (a drop between two floors with no wall): one white pixel and one
#: antialiased (172) at 1024 along Scenario_13's sunken hall, so 1.5 px.
LEDGE = 1.5 / 1024.0
#: Stair outline: one full white pixel and one antialiased (lum 170-189) at 1024 on
#: Scenario_13's stairs, so 1.5 px.
STAIR_EDGE = 1.5 / 1024.0
#: Stair hatching: 2 px lines every 10 px at 1024 (Scenario_13: 678 gaps of 10;
#: Scenario_14, _11, _17 and Other_02 the same).
HATCH = 2 / 1024.0
HATCH_PERIOD = 10 / 1024.0
#: Each flight's hatching is centred along its climb, at least this far clear of each
#: end: the first and last lines sit 6-10 px from the stair outline at 1024 (Scenario_13,
#: _11, _14, _17), the span between them a whole number of periods.
HATCH_INSET = 5 / 1024.0
#: Hatch lines from end to end of each flight: the first and last at its ends, the period
#: stretched to fit (HATCH_INSET unused). Shipped flights put their outer lines at the
#: flight's ends: one line more than Regenerate's on most flights (Scenario_05, 13, 14,
#: 16, 17), periods 10.0-10.5 px (2026-10-24).
HATCH_ENDS = True
#: Hatching luminance: 139 on Scenario_13, _14 and _11 (RGB grey).
HATCH_GREY = 139
#: Floor RGB 0 and walls RGB 255 on every shipped minimap.
FLOOR_RGB = 0
WALL_RGB = 255
#: Plate alpha when the source minimap cannot be read: 38, the base game's (the Ice and
#: Sand DLCs use 51 or 64, Scenario_06 77, Scenario_18 64; a build reads the source's).
PLATE_ALPHA = 38
#: Share of a minimap's drawn pixels an alpha must cover to be its plate: the own plate
#: covers 14% or more of every shipped floor (Scenario_15's third floor, the least),
#: DXT5 block-edge alphas (40, 43) about 1%.
PLATE_SHARE = 0.05
#: Multi-floor levels draw the other floors faintly under each floor's own drawing, at
#: this share of the plate alpha per floor of distance: 19 under 38 one floor away
#: (Scenario_10/11/14/15/16/17, ScenarioDLC_02/03/05, Scenario_Other_02), 10 two floors
#: away (Scenario_11's third floor, Scenario_15's second from the top).
GHOST_SHARE = 0.5
#: Levels whose ghost share differs, measured the same way: Scenario_Ice_03's upper
#: floor draws the lower at 26 under 64.
GHOST_SHARES = {'Scenario_Ice_03': 26 / 64.0}
#: Levels whose lower floors carry the upper floor's ghost instead of the upper carrying
#: the lower's (Scenario_16's and _17's upper floors draw no ghost, their lower floors a
#: 19 one).
GHOST_ABOVE = ('Scenario_16', 'Scenario_17')
#: A ghost is a copy of the other floor's own drawing: measured on all 39 shipped
#: multi-floor textures, floor RGB 0, walls RGB 255 as thick as that floor's own, its
#: hatching, all at the ghost alpha; where both floors draw, the own floor wins.
#: Styles whose own floor opens onto the ghosted floor (minimapstyle open_seams) leave
#: out their band where it lies over the ghost's floor at the own floor's height (within
#: LEDGE_DRAWN): the ghost shows there.
SEAM_OPEN = True
#: The HUD shows the MapData floor whose volume holds the player, whose location is the
#: middle of the capsule: floor stands on a MapData floor when its height plus this is in
#: the band, in uu. The player's capsule half height: ACharacter's 88 (Character.cpp
#: InitCapsuleSize(34, 88)); no hero Blueprint overrides it (BP_Hero, BP_Hero_Egan and
#: BP_Hero_Cygnus name no CapsuleHalfHeight, CapsuleRadius or scale; measured
#: 2026-10-25). A native AOMDPlayerCharacter constructor could still change it.
FLOOR_PAWN = 88.0
#: Long-axis margin of the frame, as a share of the texture: the median of the
#: single-floor base-game minimaps whose drawing stays inside the square (Scenario_01
#: 0.046, _08 0.029, _13 0.048, _18 0.037).
FRAME_MARGIN = 0.04
#: The art is drawn at this many times the texture size and box-filtered down, as the
#: shipped 1024 textures were from their 2048 source art.
SUPERSAMPLE = 2

# -- reading the geometry the shipped way --------------------------------------------

#: A walkable polygon steeper than this (rise over run) is drawn as stairs: the
#: emptiest grade band between floor and stairs on Scenario_13 (0.10-0.15 holds 0.1% of
#: the walkable area) and Scenario_01 (0.15-0.20 holds 0.2%).
STAIR_GRADE = 0.15
#: Neighbouring floor whose heights differ by more than this is a ledge, in uu: the
#: walkable graph's agent step height (navsurf.emit_graph's agent tuple).
LEDGE_STEP = 48.0
#: Neighbouring floor more than this far apart in height is drawn with a ledge line, in
#: uu: a single kit step (`Stairs_4x_05_SB`, 93 uu) round Scenario_16's upper dais is not
#: drawn, Scenario_13's sunken hall (900 uu) is.
LEDGE_DRAWN = 150.0
#: A kit flight whose footprint is at least this much walkable floor is drawn at its
#: whole footprint (the walkable graph cuts Scenario_16's corner flights on the diagonal).
STAIR_FILL = 0.4
#: A ledge line needs level floor this far to either side, as a share of the texture
#: (8 px at 1024: Scenario_04's steps 4-6 px apart and DLC_02's paired kerbs drew stacks
#: of lines vanilla leaves out).
LEDGE_SIDE = 8 / 1024.0
#: Kit stair pieces rising less than STEP_RISE are single steps, never hatched (the `_05`
#: pieces rise 93 uu); those rising less than STAIR_RISE (the 162 uu one-cell pieces) are
#: hatched only when chained end to end with another stair piece: their top edge within
#: CHAIN_GAP of its bottom edge and their heights within CHAIN_Z (uu). Measured on 104 such
#: pieces of Scenario_09, 12, 15, 16, 17, Ice_01 and Ice_03: chained 18 hatched / 5 plain,
#: unchained 77 plain / 4 hatched (the bridges between Scenario_09's pits are unchained).
STEP_RISE = 120.0
STAIR_RISE = 240.0
CHAIN_GAP = 160.0
CHAIN_Z = 40.0
#: A ledge line shorter than this is left out, in uu (one 300 uu kit cell): the graph's
#: own polygon seams, not drops.
LEDGE_LENGTH = 300.0
#: A hole in the floor smaller than this is a prop (barrel, statue) and is drawn as
#: floor, in uu squared: a 150 uu square. Scenario_13's smallest drawn pillars are a
#: 300 uu cell.
PROP_AREA = 150.0 * 150.0
#: A hole in the drawn floor is solid white (a pillar, a building mass: Scenario_01's
#: inner block, Scenario_07's masses) when at least this share of it lies under level
#: pieces rising above the floor beside it, and clear inside its wall band otherwise (an
#: open courtyard: Scenario_04's).
SOLID_COVER = 0.5
#: ... but always clear when at least this share of it is walkable floor of the level
#: at another height or out of reach (Scenario_08's sunken yards).
VOID_WALKABLE = 0.25
#: A level piece no thicker than this is a liquid surface (water, lava), in uu:
#: Scenario_09's water planes are 0 uu thick. A hole at least SOLID_COVER under one is
#: clear inside its wall band, as Scenario_09's moat is.
LIQUID_THICK = 1.0
#: Jagged floor edges are smoothed over this distance, in uu.
SMOOTH = 30.0
#: A gap narrower than this between floors of different heights is a drop, drawn as
#: floor up to a ledge line, in uu (one 300 uu kit cell; the graph leaves 226 uu along
#: Scenario_13's sunken hall). A gap between floors at one height stays a wall.
DROP_GAP = 300.0
#: Stairs smaller than this are left as plain floor, in uu squared: one 300 uu kit cell
#: (the smallest stair kit piece, Stairs_3x_2_SB, is 600 x 900 uu); smaller sloped
#: patches are seams in the walkable graph.
STAIR_AREA = 300.0 * 300.0
#: Level pieces with a footprint larger than this are not read as pillars or stairs, in
#: uu squared: a 2400 uu square (eight kit cells; Scenario_13's largest stair piece is
#: 1200 uu square).
PIECE_AREA = 2400.0 * 2400.0
#: A piece standing on the floor is a pillar when at least this share of its footprint
#: is off the walkable floor (the graph walks round it).
PILLAR_BLOCKED = 0.5
#: A piece is a flight of stairs when at least this share of its footprint is floor
#: steeper than STAIR_GRADE.
STAIR_COVER = 0.5
#: meshcat categories never read as pillars or stairs: the shipped minimaps leave out
#: plants, props, effects and traps.
UNDRAWN_CATEGORIES = ('foliage', 'decoration', 'effect', 'trap')
#: meshcat categories read as pillars (None: a piece the catalogue does not know, such
#: as a Fab mesh). Rock and terrain keep the walkable graph's own outline.
PILLAR_CATEGORIES = ('castle', None)

#: Image size, in pixels, of the pass that finds the playable area's extent.
BOUNDS_SIZE = 512
#: Image size, in pixels, of the panel's preview, and of its quick first pass (drawn
#: while the full one is made, from the walkable graph alone: no meshes read).
PREVIEW_SIZE = 512
PREVIEW_QUICK = 192
#: The panel's preview reads the solid on a grid this many times coarser than its drawing
#: (a build: SOLID_COARSE): 256 px for the 512 px preview, which samples a level in a few
#: seconds the first time.
PREVIEW_SOLID_COARSE = 4
#: Alpha the preview draws the plate at, so it reads on the panel.
PREVIEW_ALPHA = 255
#: Preview marker colours (rift, route points, doors) and radius in pixels.
PREVIEW_RIFT = (60, 140, 255, 255)
PREVIEW_POINT = (255, 160, 60, 255)
PREVIEW_DOOR = (226, 199, 9, 255)
PREVIEW_DOT = 4

#: A minimap texture to clone when the source level's streams its mips from a .ubulk,
#: which a mod pak here cannot carry: a base-game DXT5 1024 with every mip inline.
FALLBACK_TEXTURE = '/Game/Maps/Campaign/Scenario_01/Images/Scenario_01_Minimap'


def clean(raw):
    """The plan block as saved: {} (Source Level), {mode: 'cut'} or
    {mode: 'regenerate'[, area][, cutAtDoors: true]}. Older blocks: `cut` reads as Cut,
    `redraw` alone as Regenerate."""
    if not isinstance(raw, dict):
        return {}
    mode = raw.get('mode')
    if mode not in MODES:
        mode = ('cut' if raw.get('cut') else 'regenerate' if raw.get('redraw')
                else 'source')
    if mode == 'source':
        return {}
    out = {'mode': mode}
    area = raw.get('area')
    if mode == 'regenerate' and isinstance(area, (list, tuple)) and len(area) == 4:
        x0, y0, x1, y1 = (float(v) for v in area)
        out['area'] = [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
    if mode == 'regenerate' and raw.get('cutAtDoors') is True:
        out['cutAtDoors'] = True
    return out


def cut_at_doors(plan):
    """True when the plan's Regenerate minimap stops at the doors (Map Settings' Cut at
    Doors); by default it draws all reachable floor (user 2026-10-24)."""
    return bool(clean((plan or {}).get('minimap')).get('cutAtDoors'))


def mode(plan):
    """'source', 'cut' or 'regenerate'."""
    return clean((plan or {}).get('minimap')).get('mode', 'source')


def _inside(area, x, y):
    return area is None or (area[0] <= x <= area[2] and area[1] <= y <= area[3])


def wanted(plan):
    """The plan's block when the build makes its own minimap picture (Cut or
    Regenerate), else {}; a scratch level always draws its own and answers {}."""
    from riftlord.mapeditor import scratch
    if scratch.is_scratch(plan):
        return {}
    return clean(plan.get('minimap'))


def _volumes(level_name):
    """(volumes, None) of the level's MapData, or (None, reason)."""
    from riftlord.build.minimap import mapdata
    try:
        got = mapdata.read(level_name) or {}
    except (Exception, SystemExit) as exc:          # noqa: BLE001
        return None, '%s\'s minimap could not be read: %s' % (level_name, exc)
    vols = got.get('volumes') or []
    if not vols:
        return None, '%s has no minimap' % level_name
    return vols, None


def source(level_name):
    """(volume, None) of the level's single minimap floor, or (None, reason) when it
    cannot be regenerated."""
    vols, why = _volumes(level_name)
    if vols is None:
        return None, why
    if len(vols) > 1:
        return None, ('%s has %d minimap floors; regenerating a level with more than one '
                      'is not supported' % (level_name, len(vols)))
    if not (vols[0].get('texture') or '').split('.')[0]:
        return None, '%s names no minimap image' % level_name
    return vols[0], None


def sources(level_name):
    """(volumes, None) of every minimap floor of the level, each naming its image, or
    (None, reason)."""
    vols, why = _volumes(level_name)
    if vols is None:
        return None, why
    if not all((v.get('texture') or '').split('.')[0] for v in vols):
        return None, '%s names no minimap image for every floor' % level_name
    return vols, None


def band_of(volume):
    """(z low, z high) of a MapData floor: its translation plus and minus its Z extent."""
    z, ez = float(volume['translation'][2]), abs(float(volume['extent'][2]))
    return (z - ez, z + ez)


def floor_groups(volumes):
    """[[volume index]] of the floors that each texture draws, lowest band first: two
    volumes naming one texture (Scenario_12's) are one drawing over both bands."""
    by_tex = {}
    for k, v in enumerate(volumes):
        by_tex.setdefault((v.get('texture') or '').split('.')[0] or k, []).append(k)
    return sorted(by_tex.values(), key=lambda ks: min(band_of(volumes[k])[0] for k in ks))


def ghosts(level_name, count, g):
    """[(group index, alpha share)] of the other floors drawn faintly under floor group
    `g` of `count`: those below it (those above on GHOST_ABOVE levels), at GHOST_SHARE
    per floor of distance, nearest first."""
    from riftlord.build.minimap import mapdata
    host = (mapdata.host_of(level_name) if level_name else None) or level_name
    step = -1 if host in GHOST_ABOVE else 1
    share = GHOST_SHARES.get(host, GHOST_SHARE)
    got = [(j, share ** ((g - j) * step)) for j in range(count) if (g - j) * step > 0]
    return sorted(got, key=lambda js: -js[1])


def group_band(volumes, ks):
    """(z low, z high) the volumes `ks` cover together."""
    return (min(band_of(volumes[k])[0] for k in ks), max(band_of(volumes[k])[1] for k in ks))


def floor_bands(volumes, groups):
    """[(z low, z high)] per floor group, the lowest open below and the highest above
    (the HUD keeps the nearest floor for a player outside every band; a single floor
    draws everything)."""
    import math
    bands = [list(group_band(volumes, ks)) for ks in groups]
    # volumes overlapping in height (Scenario_05: 1,776-1,930 uu; Scenario_Ice_03: 555-757)
    # split the overlap where the nearer volume centre changes, as mapdata.pick_volume
    # places a point that two volumes hold
    for j in range(len(bands) - 1):
        lo, hi = bands[j + 1][0], bands[j][1]
        if lo < hi:
            c0, c1 = sum(bands[j]) / 2.0, sum(bands[j + 1]) / 2.0
            cut = min(hi, max(lo, (c0 + c1) / 2.0))
            bands[j][1], bands[j + 1][0] = cut, cut
    if bands:
        bands[0][0] = -math.inf
        bands[-1][1] = math.inf
    return [tuple(b) for b in bands]


def render_floors(level_name, fl, bands, frame, size, alpha, g, cache=None, styles=None):
    """RGBA minimap of floor group `g` (of the (lo, hi) `bands`) at `size` pixels in
    `frame`, the other floors' ghosts beneath it, each floor in its `styles[j]`
    (riftlord.minimapstyle) with the floor alpha `alpha`. `cache` {group: image} keeps
    each floor's plain drawing between calls."""
    import numpy as np
    from PIL import Image
    cache = {} if cache is None else cache
    styles = styles or [None] * len(bands)

    def plain(j):
        if j not in cache:
            part = on_floor(fl, *bands[j], g=j)
            got = {}
            if part['polys'] or part['rects']:
                try:
                    cache[j] = np.array(render(part, frame, size, 255, styles[j], got))
                except SystemExit:
                    cache[j] = np.array(_clear(size))
            else:
                cache[j] = np.array(_clear(size))
            cache[('z', j)] = got.get('z')
        return cache[j]
    out = plain(g).copy()
    a = out[..., 3].astype(np.float32) * (alpha / 255.0)
    style = styles[g] or {}
    #: a style drawing the other floors grey on its own plate (Sand_01's upper floor)
    grey = style.get('ghost_lum')
    lum_g = out[..., :3].mean(-1)
    own_drawn = out[..., 3] > 0
    own_floor = own_drawn & (lum_g < FLOOR_DARK)
    own_band = own_drawn & (lum_g > 192)
    width = max(1, int(round((style.get('band') or BAND) * size * _line_scale(fl, frame))))
    seam_z = None
    if SEAM_OPEN and style.get('open_seams') and cache.get(('z', g)) is not None:
        # the own floor's height carried out across its band
        seam_z = _spread_height(cache[('z', g)], own_drawn, width + 2)
    seen = np.zeros(own_drawn.shape, bool)
    for j, share in ghosts(level_name, len(bands), g):
        pic = plain(j)
        if grey is not None:
            pic = pic.copy()
            dark = pic[..., :3].mean(-1) < FLOOR_DARK
            pic[..., :3][dark] = grey
            share = 1.0
        drawn_j = pic[..., 3] > 0
        floor_j = drawn_j & (pic[..., :3].mean(-1) < FLOOR_DARK)
        take = (out[..., 3] == 0) & drawn_j
        region = _art_region_cached(level_name, j) if level_name else None
        if region is not None:
            # a ghost only inside the outline of the region that floor's own art draws
            # (Scenario_14's upper floor showed the approaches its lower floor's art
            # leaves out)
            from riftlord.build.minimap import mapdata
            inside = ~_outside(_art_mask(region, mapdata.projector(frame), size))
            take &= inside
        if seam_z is not None and cache.get(('z', j)) is not None:
            # own band over this floor's floor at the own floor's height: an open seam,
            # the ghost shows (DLC_02's and DLC_03's upper floors where they step down)
            zj = cache[('z', j)]
            near = np.abs(np.nan_to_num(seam_z, nan=1e9) - np.nan_to_num(zj, nan=-1e9))
            seam = own_band & ~own_floor & floor_j & (near <= LEDGE_DRAWN) & ~seen
            if seam.any():
                out[..., :3][seam] = pic[..., :3][seam]
                a[seam] = pic[..., 3][seam].astype(np.float32) * (alpha * share / 255.0)
                seen |= seam
        if style.get('stacked'):
            # own floor laid over the ghost (Scenario_05's upper floor: 38 over 19 = 54)
            both = own_drawn & ~seen & drawn_j & (inside if region is not None else True)
            if both.any():
                ao = a[both] / 255.0
                ag = pic[..., 3][both].astype(np.float32) * (alpha * share / 255.0) / 255.0
                at = ao + ag * (1.0 - ao)
                co = out[..., :3][both].astype(np.float32)
                cg = pic[..., :3][both].astype(np.float32)
                mix = (co * ao[:, None] + cg * (ag * (1.0 - ao))[:, None]) / at[:, None]
                out[..., :3][both] = np.clip(np.round(mix), 0, 255).astype(np.uint8)
                a[both] = at * 255.0
                seen |= both
        out[..., :3][take] = pic[..., :3][take]
        a[take] = pic[..., 3][take].astype(np.float32) * (alpha * share / 255.0)
        out[..., 3][take] = 1
        seen |= take
    out[..., 3] = np.round(np.clip(a, 0, 255)).astype(np.uint8)
    return Image.fromarray(out, 'RGBA')


#: Draw from the geometry alone, never reading the shipped art (the source level's own
#: minimap) at draw time: every _art_* reader and the per-polygon floor votes answer None.
#: Styles still come from minimapstyle. User rule (2026-10-24): every mission, scratch or
#: built on a shipped level, goes through the same pipeline, so the host's shipped art is
#: never an input; it only measures rules in the harness. ART_FREE_ENV ('0' or '1')
#: overrides it for a harness diagnostic.
ART_FREE = True
ART_FREE_ENV = 'RIFTLORD_MINIMAP_ART_FREE'


def art_free():
    """True when drawings may not read the shipped art (ART_FREE, or ART_FREE_ENV)."""
    env = os.environ.get(ART_FREE_ENV)
    if env in ('0', '1'):
        return env == '1'
    return bool(ART_FREE)


def source_floors(level_name, polys):
    """[set of floor group indices or None] per walkable polygon: the floors whose
    shipped minimap draws floor under it (the source level's own art says which floor a
    part belongs to; MapData's height bands alone put Scenario_14's room and
    Scenario_Other_02's upper landing on the wrong floor). None where no shipped floor
    draws it (added floor, approaches the art leaves out): the band decides."""
    import numpy as np
    from riftlord.build.minimap import mapdata
    vols, _why = sources(level_name)
    if not vols or len(vols) < 2 or art_free():
        return [None] * len(polys)
    groups = floor_groups(vols)
    bands = floor_bands(vols, groups)
    pics = []
    for ks in groups:
        vol = vols[ks[0]]
        try:
            img = mapdata.minimap_image(vol)
        except Exception:                            # noqa: BLE001
            img = None
        if img is None:
            return [None] * len(polys)
        a = np.asarray(img.convert('RGBA')).astype(np.int32)
        own = a[..., 3] >= OWN_SHARE * plate_alpha(vol)
        # floor, and grey hatching on it (a flight drawn on this floor)
        pics.append((own & (a[..., :3].mean(-1) < FLOOR_VOTE_LUM),
                     _art_projector(level_name, ks[0], vol), a.shape[0]))
    out = []
    for q in polys:
        cx = sum(v[0] for v in q) / len(q)
        cy = sum(v[1] for v in q) / len(q)
        pts = [(cx, cy)] + [((cx + v[0]) / 2.0, (cy + v[1]) / 2.0) for v in q]
        got = set()
        for g, (dark, project, n) in enumerate(pics):
            hits = 0
            for x, y in pts:
                u, v = project((x, y))
                iu, iv = int(u * n), int(v * n)
                if 0 <= iu < n and 0 <= iv < n and dark[iv, iu]:
                    hits += 1
            if hits >= FLOOR_VOTE * len(pts):
                got.add(g)
        if len(got) > 1:
            # floors stacked over one another: the voted floors nearest its height
            z0, z1 = min(v[2] for v in q), max(v[2] for v in q)
            gap = {g: max(0.0, bands[g][0] - z1, z0 - bands[g][1]) for g in got}
            best = min(gap.values())
            got = {g for g in got if gap[g] <= best + FLOOR_NEAR}
        out.append(got or None)
    return out


#: Source textures drawn off their MapData frame ({level|volume: {scale, shift}}): the
#: art pixel at frame pixel q is centre + scale * (q - centre) + shift (shift in px at
#: 1024). Everything read off such art is read through this fit, so the drawing stays in
#: the MapData frame that places the markers. Measured as the best fit of the shipped
#: plate on the regenerated whole-cut plate, searched at 512 px (round 12): Scenario_07's
#: plate IoU 0.763 -> 0.839 (the art cut's region gives 1.0525, (-4, -13)); control
#: Scenario_13 fits at 1.0025, (0, -1); every other floor at 1.00 within 0.02 and 1 px at
#: 256 (2026-10-24).
ART_ALIGN = {
    'Scenario_07|0': {'scale': 1.0575, 'shift': (-5.0, -15.0)},
}


def _art_projector(level_name, k, vol):
    """world (x, y) -> (u, v) on volume `k`'s source texture: MapData's projector, through
    ART_ALIGN's fit where the art is drawn off that frame."""
    from riftlord.build.minimap import mapdata
    project = mapdata.projector(vol)
    how = ART_ALIGN.get('%s|%d' % (level_name, k))
    if not how:
        return project
    s = float(how['scale'])
    du, dv = (float(x) / 1024.0 for x in how['shift'])

    def aligned(p):
        u, v = project(p)
        return 0.5 + s * (u - 0.5) + du, 0.5 + s * (v - 0.5) + dv
    return aligned


#: A pixel of a source minimap is its own floor at this share of its plate alpha (other
#: floors' ghosts are at half or less) and luminance under FLOOR_DARK.
OWN_SHARE = 0.7
FLOOR_DARK = 64
#: A polygon is on a source floor when that floor's art draws floor under at least this
#: share of its sample points: floor, or the grey hatching of a flight (139; white walls
#: and white hatching are over FLOOR_VOTE_LUM).
FLOOR_VOTE = 0.5
FLOOR_VOTE_LUM = 200
#: Of several floors whose art draws a polygon, those within this many uu of the
#: nearest band to its height keep it (MapData's bands are drawn a step or two off the
#: art: Scenario_14's room sits 40 uu under its floor's band).
FLOOR_NEAR = 200.0


@functools.lru_cache(maxsize=16)
def _art_floor_cached(level_name, g):
    if art_free():
        return None
    import numpy as np
    from riftlord.build.minimap import mapdata
    vols, _why = sources(level_name)
    if not vols:
        return None
    groups = floor_groups(vols)
    g = g or 0
    if g >= len(groups):
        return None
    vol = vols[groups[g][0]]
    try:
        img = mapdata.minimap_image(vol)
    except Exception:                                # noqa: BLE001
        return None
    if img is None:
        return None
    a = np.asarray(img.convert('RGBA')).astype(np.int32)
    dark = (a[..., 3] >= OWN_SHARE * plate_alpha(vol)) & (a[..., :3].mean(-1) < FLOOR_DARK)
    return dark, _art_projector(level_name, groups[g][0], vol), a.shape[0]


#: The source art's region is its own plate grown by this share of the texture (half the
#: outer band: the floor stops at the band's inner edge).
REGION_GROW = 6 / 1024.0


def _art_region_cached(level_name, g):
    if art_free():
        return None
    return _art_region_read(level_name, g)


@functools.lru_cache(maxsize=16)
def _art_region_read(level_name, g):
    import numpy as np
    from riftlord.build.minimap import mapdata
    vols, _why = sources(level_name)
    if not vols:
        return None
    groups = floor_groups(vols)
    g = g or 0
    if g >= len(groups):
        return None
    vol = vols[groups[g][0]]
    try:
        img = mapdata.minimap_image(vol)
    except Exception:                                # noqa: BLE001
        return None
    if img is None:
        return None
    a = np.asarray(img.convert('RGBA')).astype(np.int32)
    own = a[..., 3] >= OWN_SHARE * plate_alpha(vol)
    n = a.shape[0]
    return (_grow(own, max(1, int(round(REGION_GROW * n))), True),
            _art_projector(level_name, groups[g][0], vol), n)


def _art_region(level_name, g, wanted):
    """(mask, projector, size) of the region floor group `g` of the source level's
    minimap draws, when `wanted`, else None. Only the harness's art cut wants it (a
    comparison in the region vanilla draws); it is read even when art_free()."""
    if not wanted or not level_name:
        return None
    return _art_region_read(level_name, g)


@functools.lru_cache(maxsize=16)
def _art_light_cached(level_name, g):
    if art_free():
        return None
    import numpy as np
    from riftlord.build.minimap import mapdata
    vols, _why = sources(level_name)
    if not vols:
        return None
    groups = floor_groups(vols)
    if g >= len(groups):
        return None
    vol = vols[groups[g][0]]
    try:
        img = mapdata.minimap_image(vol)
    except Exception:                                # noqa: BLE001
        return None
    if img is None:
        return None
    a = np.asarray(img.convert('RGBA')).astype(np.int32)
    light = (a[..., 3] >= OWN_SHARE * plate_alpha(vol)) & (a[..., :3].mean(-1) > 192)
    return light, _art_projector(level_name, groups[g][0], vol), a.shape[0]


@functools.lru_cache(maxsize=16)
def _art_walls_cached(level_name, g):
    """(white mask, region mask, projector, size) of floor group `g` of the source level's
    minimap with its walls counted at the style's own wall alpha (Sand_01's walls at 26
    under a 51 plate), or None where the walls are not fainter than the plate."""
    if art_free():
        return None
    import numpy as np
    from riftlord.build.minimap import mapdata
    from riftlord.build.minimap import minimapstyle
    vols, _why = sources(level_name)
    if not vols:
        return None
    groups = floor_groups(vols)
    if g >= len(groups):
        return None
    vol = vols[groups[g][0]]
    plate = plate_alpha(vol)
    wall = minimapstyle.of_volume(vol).get('wall_alpha') or plate
    if wall >= plate:
        return None
    try:
        img = mapdata.minimap_image(vol)
    except Exception:                                # noqa: BLE001
        return None
    if img is None:
        return None
    a = np.asarray(img.convert('RGBA')).astype(np.int32)
    lum = a[..., :3].mean(-1)
    light = (a[..., 3] >= OWN_SHARE * wall) & (a[..., 3] < OWN_SHARE * plate) & (lum > 192)
    light |= (a[..., 3] >= OWN_SHARE * plate) & (lum > 192)
    own = (a[..., 3] >= OWN_SHARE * plate) | light
    n = a.shape[0]
    return (light, _grow(own, max(1, int(round(REGION_GROW * n))), True),
            _art_projector(level_name, groups[g][0], vol), n)


@functools.lru_cache(maxsize=16)
def _art_open_cached(level_name, g):
    """(mask, projector, size) of the openings floor group `g` of the source level's
    minimap leaves inside its plate: clear or ghosted-floor pixels off its own plate that
    the outside does not reach, or None."""
    if art_free():
        return None
    import numpy as np
    from riftlord.build.minimap import mapdata
    vols, _why = sources(level_name)
    if not vols:
        return None
    groups = floor_groups(vols)
    if g >= len(groups):
        return None
    vol = vols[groups[g][0]]
    try:
        img = mapdata.minimap_image(vol)
    except Exception:                                # noqa: BLE001
        return None
    if img is None:
        return None
    a = np.asarray(img.convert('RGBA')).astype(np.int32)
    own = a[..., 3] >= OWN_SHARE * plate_alpha(vol)
    # clear, or another floor's ghosted floor (its ghosted walls are band, not opening:
    # Scenario_05's upper courtyards)
    seen = (a[..., 3] == 0) | (a[..., :3].mean(-1) < FLOOR_DARK)
    return (~own & seen & ~_outside(own), _art_projector(level_name, groups[g][0], vol),
            a.shape[0])


@functools.lru_cache(maxsize=16)
def _art_hatch_cached(level_name, g):
    if art_free():
        return None
    import numpy as np
    from riftlord.build.minimap import mapdata
    from riftlord.build.minimap import minimapstyle
    vols, _why = sources(level_name)
    if not vols:
        return None
    groups = floor_groups(vols)
    if g >= len(groups):
        return None
    vol = vols[groups[g][0]]
    try:
        img = mapdata.minimap_image(vol)
    except Exception:                                # noqa: BLE001
        return None
    if img is None:
        return None
    if img.size[0] != 1024:
        # hatching is found at 1024, where its period and window were measured (a 2048
        # texture's lines are twice as far apart)
        from PIL import Image
        img = img.convert('RGBA').resize((1024, 1024), Image.BOX)
    a = np.asarray(img.convert('RGBA')).astype(np.int32)
    own = a[..., 3] >= OWN_SHARE * plate_alpha(vol)
    lines = own & (a[..., :3].mean(-1) >= minimapstyle.LINE_LO)
    hatched = _grow(minimapstyle.hatched(lines), ART_HATCH_GROW, True) & own
    return hatched, _art_projector(level_name, groups[g][0], vol), a.shape[0]


def _art_hatch(level_name, g):
    """(hatched-stairs mask, projector, size) of floor group `g` of the source level's own
    minimap, or None."""
    if not level_name:
        return None
    return _art_hatch_cached(level_name, g or 0)


#: Graph slopes beside kit flights are hatched where at least ART_HATCH_SHARE of them lies
#: on the source art's hatching, grown ART_HATCH_GROW px (the scorer's STAIR_FILL).
ART_HATCH_SHARE = 0.5
ART_HATCH_GROW = 4
#: Geometry only, on a level of several floors: tilted floor pieces laid as a flight beside
#: a floor are drawn on it (Scenario_16's corner flights). Kit flights left off a floor are
#: not: drawing those too cost Scenario_05 floor 0 0.11 and Scenario_17 floor 0 0.09.
TILTED_BESIDE_GEO = False
#: Geometry only: beside kit flights, the graph's own slopes of a flight's size are hatched
#: too (no art to ask).
SLOPE_HATCH_GEO = False
#: A flight at least ART_PLAIN_INSIDE inside the source art's plate with under
#: ART_PLAIN_HATCH of it on that art's hatching is drawn as plain floor.
ART_PLAIN_INSIDE = 0.5
ART_PLAIN_HATCH = 0.2
#: An opening at least OPEN_SHARE open in the source art is drawn with that art's ring.
OPEN_SHARE = 0.5
#: On a level of several floors, a kit flight left off a floor beside that floor's floor
#: is drawn there where the floor's art draws at least CHAIN_ART of its footprint.
CHAIN_ART = 0.25
#: A room of the source art's floor no walkable polygon reaches is drawn when the solid
#: holds a surface near the floor's height under at least ISLAND_HELD of it.
ISLAND_HELD = 0.5
#: ... read first on about this many of the room's pixels.
ISLAND_SAMPLE = 2000
#: ... its surface: the highest with headroom from SNAP under the floor's main height to
#: ISLAND_ABOVE over it, in uu (Sand_04's raised block platform tops out at 376 uu over
#: a 5 uu main height, its hollow blocks' bottoms at -304; the art draws its top). As
#: PLATFORM_RISE.
ISLAND_ABOVE = 600.0


def _art_light(level_name, g):
    """(own-wall mask, projector, size) of floor group `g` of the source level's own
    minimap, or None."""
    if not level_name:
        return None
    return _art_light_cached(level_name, g or 0)


def _art_floor(level_name, g):
    """(own-floor mask, projector, size) of floor group `g` of the source level's own
    minimap, or None (unreadable, or no source level)."""
    if not level_name:
        return None
    return _art_floor_cached(level_name, g or 0)


def _art_mask(art, project, size):
    """Bool [v][u] at the drawing's `size`: the source art's own floor `art` there."""
    import numpy as np
    dark, aproj, an = art
    line = np.arange(size, dtype=np.float32) + 0.5
    inv = _unproject(project, size)
    wx, wy = inv(line[None, :], line[:, None])
    au, av = aproj((wx, wy))
    iu = (np.asarray(au) * an).astype(np.int64)
    iv = (np.asarray(av) * an).astype(np.int64)
    inside = (iu >= 0) & (iu < an) & (iv >= 0) & (iv < an)
    out = np.zeros((size, size), bool)
    out[inside] = dark[iv[inside], iu[inside]]
    return out


def on_floor(fl, lo, hi, g=None):
    """`floor()`'s answer for floor group `g`, between heights `lo` and `hi` (uu):
    polygons the source art puts on it (else reaching into the band), pieces, doors and
    seeds in the band."""
    which = fl.get('poly_floors') or [None] * len(fl['polys'])

    def poly_in(k, q):
        if g is not None and which[k] is not None:
            return g in which[k]
        zs = [v[2] + FLOOR_PAWN for v in q]
        return min(zs) < hi and max(zs) >= lo

    def box_in(lo_, hi_):
        return lo_[2] < hi + SUPPORT_ABOVE and hi_[2] >= lo - SUPPORT_BELOW

    def point_in(p):
        return lo - ANCHOR_BELOW <= p[2] < hi + ANCHOR_BELOW
    out = dict(fl)
    out['polys'] = [q for k, q in enumerate(fl['polys']) if poly_in(k, q)]
    keep = [k for k, r in enumerate(fl['rects']) if lo <= r[1][2] < hi]
    out['rects'] = [fl['rects'][k] for k in keep]
    if fl.get('rect_polys'):
        out['rect_polys'] = [fl['rect_polys'][k] for k in keep]
    out['erase'] = [e for e in fl['erase'] if box_in(e[0], e[1])]
    out['block'] = [b for b in fl['block'] if box_in(b[0], b[1])]
    out['knocked'] = [b for b in fl.get('knocked') or [] if box_in(b[0], b[1])]
    out['pieces'] = [p for p in fl['pieces'] if box_in(p[0], p[1])]
    out['cover'] = [c for c in fl['cover'] if box_in(c[0], c[1])]
    out['planes'] = [c for c in fl['planes'] if box_in(c[0], c[1])]
    out['flyer_legs'] = [leg for leg in fl.get('flyer_legs') or []
                         if lo <= min(leg[0][2], leg[1][2]) + FLOOR_PAWN < hi]
    out['flora'] = [b for b in fl.get('flora') or [] if box_in(b[0], b[1])]
    out['rocks'] = [r for r in fl.get('rocks') or []
                    if box_in((0, 0, r[1]), (0, 0, r[2]))]
    out['shaped'] = [x for x in fl.get('shaped') or []
                     if box_in((0, 0, x[1]['z'][0]), (0, 0, x[1]['z'][1]))]
    # ... and the kit flights continuing those, which may be drawn where this floor's art
    # draws them (layers)
    kept = [x[1] for x in out['shaped'] if x[0] == 'stairs']
    out['shaped'] += [x for x in fl.get('shaped') or []
                      if x[0] == 'stairs' and x not in out['shaped']
                      and _chained(x[1], [('stairs', k, None) for k in kept])]
    out['doors'] = [d for d in fl['doors'] if point_in((d[0], d[1], d[4]))]
    out['band'] = (lo, hi)
    out['art_floor'] = g
    out['seeds'] = ([r for r in fl['rifts'] if point_in(r)]
                    or [a for a in fl['anchors'] if point_in(a)])
    # the floor under the flyers' routes is kept even where ground cannot reach it
    out['seeds'] += [a for a in fl.get('flyers') or [] if point_in(a)]
    return out


def texture_path(folder, level_name, k=0):
    """Game path of the mission's copy of the source level's floor `k` minimap texture:
    in `folder`, under the SOURCE texture's own name. The level copy imports the texture
    by the source's object name and retarget_map_data swaps only the package path, so a
    copy named anything else (`Scenario_09_Minimap` for Front Lawn's
    `Scenario_09_01_Minimap`) leaves that import naming no export."""
    return texture_ref(folder, level_name, k)[0]


def texture_ref(folder, level_name, k=0):
    """(package path, object name) of that copy: both the source texture's own
    (Scenario_18's package is `Scenario_18_01`, its object `Scenario_18`)."""
    vols, _why = _volumes(level_name)
    full = ((vols or [{}])[min(k, len(vols or [{}]) - 1)].get('texture') or '')
    pkg, _dot, obj = full.partition('.')
    leaf = pkg.rsplit('/', 1)[-1] if pkg else '%s_Minimap' % level_name
    return '%s/%s' % (folder, leaf), (obj or leaf)


def donor(level_name):
    """Game path of the minimap texture a build clones for the mission's own: the
    source level's first floor's, or FALLBACK_TEXTURE when that one streams from a
    .ubulk."""
    from riftlord.build.minimap import mapdata
    vols, _why = _volumes(level_name)
    for vol in vols or []:
        tex = (vol.get('texture') or '').split('.')[0]
        if not tex:
            continue
        got = mapdata._fetch(tex, exts=('.uasset', '.uexp', '.ubulk'))
        if '.uasset' in got and '.ubulk' not in got:
            return tex
    return FALLBACK_TEXTURE


def plate_alpha(volume):
    """The source minimap's plate alpha from the measured style table (minimapstyle: the
    highest alpha drawn on at least PLATE_SHARE of its drawn pixels, leaving out a lower
    plate stacked over its ghost, Scenario_05's 54), or PLATE_ALPHA."""
    from riftlord.build.minimap import minimapstyle
    return int(minimapstyle.of_volume(volume).get('alpha') or PLATE_ALPHA)


# -- the walkable floor -------------------------------------------------------------

#: Kit stairs, by mesh name (`Stairs_4x_4_SB`, `SM_MERGED_WoodStairs_3x_4`): their
#: footprints are the shipped stair rectangles on Scenario_13. Railings, side walls and
#: end caps of stairs are not stairs.
STAIR_WORDS = ('stairs',)
STAIR_NOT = ('railing', 'wall_', 'endcap', 'side')
#: Raised platforms (`SM_MERGED_Platform_300x600` inside Scenario_13's stairs): floor
#: drawn with an outline, not a solid block.
PLATFORM_WORDS = ('platform',)
#: ... standing at most this far above the floor round it, in uu (Scenario_13's stair
#: platform tops out 250-450 uu over the graph plane beside it; one on a roof is not
#: drawn).
PLATFORM_RISE = 600.0


def piece_kind(mesh):
    """'stairs', 'platform' or None for a mesh name."""
    m = (mesh or '').lower()
    if any(w in m for w in STAIR_WORDS) and not any(w in m for w in STAIR_NOT):
        return 'stairs'
    if any(w in m for w in PLATFORM_WORDS) and 'rift' not in m:
        return 'platform'
    return None


def _tilted(matrix):
    """True when a piece's matrix pitches or rolls it more than TILT (its local z off
    the world vertical)."""
    if not matrix:
        return False
    import math
    zx, zy, zz = matrix[8], matrix[9], matrix[10]
    n = math.sqrt(zx * zx + zy * zy + zz * zz) or 1.0
    return abs(zz) / n < math.cos(math.radians(TILT))


#: Flights of one stairs component keep each named piece's own climb (Ice_03's bottom
#: side flights, hatched across the main flight in its art).
FLIGHT_OWN_CLIMB = True
#: Only tilted floor pieces lay a flight (Scenario_16's `Floor_Squarebrick_1x4`): tilted
#: rocks and column trims over a sloped graph polygon (Front Lawn's) are not stairs.
TILTED_FLIGHT_WORDS = ('floor',)
#: A piece pitched or rolled more than this many degrees is tilted (a flight of tilted
#: floor tiles: Scenario_16's stairs).
TILT = 5.0


def _footprint(mesh, matrix, cat):
    """{corners [(x, y)] x4, z (lo, hi), climb (x, y)} of a piece's turned box, or None.
    Kit stairs climb along their local -y (stairfit.py)."""
    info = cat.get(mesh)
    if not info or not matrix:
        return None
    o, e = info['origin'], info['extent']
    m = matrix

    def at(c):
        return [m[r] * c[0] + m[4 + r] * c[1] + m[8 + r] * c[2] + m[12 + r] for r in range(3)]
    corners = [at((o[0] + sx * e[0], o[1] + sy * e[1], o[2])) for sx, sy in
               ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    zs = [at((o[0], o[1], o[2] + sz * e[2]))[2] for sz in (-1, 1)]
    return {'corners': [(c[0], c[1]) for c in corners], 'z': (min(zs), max(zs)),
            'climb': (-m[4], -m[5])}

def _piece_box(piece, matrix, cat):
    """World (lo, hi) of one level piece's mesh under `matrix`, or None."""
    info = cat.get(piece.get('mesh'))
    if not info:
        return None
    o, e = info['origin'], info['extent']
    m = matrix
    pts = []
    for dx in (-1, 1):
        for dy in (-1, 1):
            for dz in (-1, 1):
                c = (o[0] + dx * e[0], o[1] + dy * e[1], o[2] + dz * e[2])
                pts.append([m[r] * c[0] + m[4 + r] * c[1] + m[8 + r] * c[2] + m[12 + r]
                            for r in range(3)])
    return ([min(p[k] for p in pts) for k in range(3)],
            [max(p[k] for p in pts) for k in range(3)])


def _graph_polys(level_name, plan):
    """(walkable polygons [[(x, y, z)]], their edge ids, ground chains, notes): the
    level's graph with the corridors a build adds under the plan's ground routes."""
    from riftlord.build.navigation import navsurf
    from riftlord.build.navigation import navsurfextend
    from riftlord.mapeditor import planactors
    buf = navsurf.buffer(level_name)
    if not buf:
        return [], [], [], ['%s has no walkable graph' % level_name]
    heads = navsurf.graphs(buf)
    secs = navsurf.sections(buf, heads[0])
    polys = [list(s['verts']) for s in secs]
    ids = [list(s['edge_ids']) for s in secs]
    notes = ['%d polygon(s) of the level\'s walkable graph' % len(polys)]
    try:
        chains = planactors.ground_chains(copy.deepcopy(plan),
                                          planactors.level_actors(level_name))
    except Exception as exc:                         # noqa: BLE001
        return polys, ids, [], notes + ['the routes could not be read: %s' % exc]
    if chains and (navsurfextend.needs_extending(buf, chains)
                   or navsurfextend._bridgeable(buf, chains)):
        try:
            per_agent, _n, _h = navsurfextend.plan_corridors(buf, chains)
        except ValueError as exc:
            return polys, ids, chains, notes + [
                'a ground route\'s floor could not be added: %s' % exc]
        added = len(per_agent[0][0]) - len(polys)
        polys = [list(q) for q in per_agent[0][0]]
        ids = [list(r) for r in per_agent[0][1]]
        notes.append('%d polygon(s) a build adds under ground routes' % added)
    return polys, ids, chains, notes


def _anchors(level_name, plan, level, chains):
    """World points the mission is played around: route points, the ground chains'
    points, rifts and player starts."""
    from riftlord.build.minimap import mapdataemit
    from riftlord.mapeditor import planactors
    out = [tuple(p) for chain in chains for p in chain]
    try:
        nodes, _r, _p = planactors.graph(copy.deepcopy(plan),
                                         planactors.level_actors(level_name))
        out += [tuple(n['at']) for n in nodes.values() if n.get('at')]
    except Exception:                                # noqa: BLE001
        out += [tuple(p['at']) for p in plan.get('points') or [] if p.get('at')]
    out += [tuple(r) for r in mapdataemit.markers_for(level_name, plan, level=level)[0]]
    gone = {w.get('name') for w in plan.get('wiring') or [] if w.get('gone')}
    out += [tuple(s['location']) for s in level.get('player_starts') or []
            if s.get('export') not in gone and s.get('location')]
    out += [tuple(s['at']) for s in plan.get('playerStarts') or [] if s.get('at')]
    return out


def _played(polys, ids, anchors):
    """The polygons in a walkable component some anchor stands on."""
    from riftlord.build.navigation import navsurf
    from riftlord.build.navigation import navsurfextend
    from riftlord.build.navigation import navsurfgen
    comp = navsurfextend._components(ids)
    keep = set()
    for p in anchors:
        for i, verts in enumerate(polys):
            if comp[i] in keep or not navsurf._inside_xy(verts, p[0], p[1]):
                continue
            dz = p[2] - navsurfgen.plane_z(verts, p[0], p[1])
            if -ANCHOR_BELOW <= dz <= ANCHOR_ABOVE:
                keep.add(comp[i])
    return [q for i, q in enumerate(polys) if comp[i] in keep]


#: Plants are never drawn: a hole in the drawn floor where nothing but plants stands,
#: lying at least FLORA_SHARE under plants' boxes, is floor. A hole lying at least ROCK_SHARE under rocks' collision outlines
#: is one solid light area (Front Lawn's courtyard rock, as its shipped art draws it), not
#: a ring round a void (user, 2026-10-28). A rock: a terrain piece named by ROCK_WORDS,
#: not a cliff, at most ROCK_SIDE across (the 95th percentile of the 13,627 such pieces on
#: the shipped maps: median 722 uu; cliffs start at 8,000). Clearing every rock's outline
#: instead punched holes in floors and walls (Front Lawn 0.788 -> 0.566): most rocks are
#: sunk into walls and floor edges, and the shipped art draws 60% of their area as floor.
NATURE_DRAWN = True
FLORA_SHARE = 0.5
ROCK_SHARE = 0.5
ROCK_WORDS = ('stone', 'rock', 'boulder', 'pebble', 'crystal')
ROCK_NOT = ('cliff', 'mountain')
ROCK_SIDE = 3332.0


def _category(cat, mesh):
    """A mesh's meshcat category, or the one its name gives (a mesh the catalogue does not
    know, such as a Fab one: meshcat.category_of)."""
    got = (cat.get(mesh) or {}).get('category')
    if got:
        return got
    from riftlord.formats.omd3 import meshcat
    return meshcat.category_of(mesh or '', mesh or '')


@functools.lru_cache(maxsize=1)
def _hulls():
    import json
    from riftlord.core import gamepath
    try:
        with open(gamepath.data('omd3-hulls.json')) as fh:
            return json.load(fh).get('hulls') or {}
    except (OSError, ValueError):
        return {}


def _nature(mesh, matrix, box, cat, flora, rocks):
    """Add a piece to `flora` ((lo, hi)) when it is a plant, or to `rocks` ((XY collision
    outline, z low, z high)) when it is a rock."""
    if not NATURE_DRAWN or not mesh or not matrix or not box:
        return
    kind = _category(cat, mesh)
    low = mesh.lower()
    if kind == 'foliage':
        flora.append((box[0], box[1]))
        return
    if kind != 'terrain' or not any(w in low for w in ROCK_WORDS) or \
            any(w in low for w in ROCK_NOT):
        return
    if max(box[1][0] - box[0][0], box[1][1] - box[0][1]) > ROCK_SIDE:
        return
    m = matrix
    parts = (_hulls().get(mesh) or {}).get('parts') or []
    outs = []
    for part in parts:
        pts = [(m[0] * v[0] + m[4] * v[1] + m[8] * v[2] + m[12],
                m[1] * v[0] + m[5] * v[1] + m[9] * v[2] + m[13]) for v in part.get('verts') or []]
        if len(pts) >= 3:
            outs.append(_hull2d(pts))
    if not outs:
        foot = _footprint(mesh, matrix, cat)
        if foot:
            outs.append(list(foot['corners']))
    rocks.extend((q, box[0][2], box[1][2]) for q in outs)


def _hull2d(pts):
    """Convex hull of XY points (monotone chain)."""
    pts = sorted(set(pts))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


#: Flyer routes are sampled this often along each leg, in uu.
FLYER_STEP = 100.0
#: The walkable floor under a flyer route that no ground route reaches is drawn when its
#: walkable piece covers at most this, in uu^2: on the 21 such pieces of the shipped maps,
#: those the art draws as floor cover 8-61 m^2 (ScenarioDLC_01's islands, DLC_05's), the
#: one it leaves out 526 m^2 (Sand_05); 180 m^2 is about their geometric middle.
FLYER_ISLAND_AREA = 180.0 * 100.0 * 100.0
#: Where a flyer route crosses what the drawing leaves empty (a pit, off the floor), a band
#: FLYER_BAND wide along it in grey FLYER_GREY at the plate alpha, where no walkable floor
#: of any height lies (and not over the drawing): Scenario_Ice_02's flyer approach, read
#: on its 2048 texture (6.348 uu a px): 117 px across (743 uu) in every column, RGB 121 on
#: 83,962 of its 84,750 grey pixels, at its plate alpha 64, centred on the spawner's leg.
#: The only such band on the 49 shipped floors (Sand_05 draws its flyers' pit room as faint
#: floor instead).
FLYER_BAND = 117 * 2 * 6500.0244 / 2048
FLYER_GREY = 121
#: The band stops at walkable pieces of at least this area, in uu squared: one 300 uu kit
#: cell. Smaller pieces are pillar and wall tops, not floor (beside North Wing's added
#: flyer route 0.1-4.6 m^2, which notched the band; beside ScenarioDLC_01's 0.0-2.1 m^2,
#: which its art leaves empty).
FLYER_FLOOR_AREA = 300.0 * 300.0


def _flyer_starts(plan, actors):
    """[(spawner location, flyer start node)] of every spawner after the plan: the
    level's own (less those the plan removes, moved and re-routed by its wiring) and the
    plan's added ones. Nodes are planactors.graph's."""
    edits = {w['name']: w for w in plan.get('wiring') or []
             if w.get('name') in actors['spawners']}
    out = []
    for name, sp in actors['spawners'].items():
        w = edits.get(name) or {}
        if w.get('gone'):
            continue
        start = w['flyerStart'] if 'flyerStart' in w else sp.get('flyerStart')
        out.append((w.get('at') or sp.get('location'), start))
    for sp in plan.get('spawners') or []:
        out.append((sp.get('at'), sp.get('flyerStart')))
    return [(tuple(float(x) for x in at), start) for at, start in out
            if at and start is not None]


def _flyer_routes(level_name, plan, from_spawner=False):
    """[((x, y, z), (x, y, z))] legs of every flyer route after the plan: each spawner's
    flyer start chain (StartingFlyerPathPoint), the level's own and the plan's added
    spawners alike, from its first point to the route's end (the leg out of the spawner
    crosses its spawn area, which the shipped art leaves out: Sand_05)."""
    from riftlord.mapeditor import planactors
    plan = plan or {}
    try:
        actors = planactors.level_actors(level_name)
        nodes, _removed, _p = planactors.graph(copy.deepcopy(plan), actors)
    except Exception:                                # noqa: BLE001
        return []
    legs, drawn = [], set()
    for at, start in _flyer_starts(plan, actors):
        seen = set()
        todo = [(at if from_spawner else None, start)]
        while todo:
            a, n = todo.pop()
            if n not in nodes or not nodes[n].get('at'):
                continue
            p = tuple(float(x) for x in nodes[n]['at'])
            # every leg into a point, once: two branches meeting again draw both
            if a is not None and (a, p) not in drawn:
                drawn.add((a, p))
                legs.append((a, p))
            if n in seen:
                continue
            seen.add(n)
            for b in nodes[n]['next']:
                todo.append((p, b['to']))
    return legs


def _piece_areas(polys, ids):
    """[uu squared] per polygon: the area of its walkable piece (`ids`: polygons sharing
    an edge)."""
    from riftlord.build.navigation import navsurfextend
    comp = navsurfextend._components(ids)
    size_of = {}
    for k, q in enumerate(polys):
        a2 = abs(sum(q[i][0] * q[(i + 1) % len(q)][1] - q[(i + 1) % len(q)][0] * q[i][1]
                     for i in range(len(q)))) / 2.0
        size_of[comp[k]] = size_of.get(comp[k], 0.0) + a2
    return [size_of[comp[k]] for k in range(len(polys))]


def _pieces_of_area(polys, ids, least):
    """The polygons whose walkable piece covers at least `least` uu squared."""
    return [q for q, a in zip(polys, _piece_areas(polys, ids)) if a >= least]


def _flyer_anchors(level_name, plan, polys, ids=None):
    """[(x, y, z)] points on the walkable floor under the flyer routes: along each leg,
    every FLYER_STEP, the highest walkable polygon under the flyer, where that polygon's
    walkable piece (`ids`: polygons sharing an edge) covers at most FLYER_ISLAND_AREA."""
    import math
    from riftlord.build.navigation import navsurf
    from riftlord.build.navigation import navsurfgen
    legs = _flyer_routes(level_name, plan)
    if not legs or not polys:
        return []
    small = None
    if ids is not None:
        small = [a <= FLYER_ISLAND_AREA for a in _piece_areas(polys, ids)]
    boxes = [(min(v[0] for v in q), min(v[1] for v in q), max(v[0] for v in q),
              max(v[1] for v in q)) for q in polys]
    out = []
    for p0, p1 in legs:
        n = max(1, int(math.dist(p0[:2], p1[:2]) // FLYER_STEP))
        for t in range(n + 1):
            x = p0[0] + (p1[0] - p0[0]) * t / n
            y = p0[1] + (p1[1] - p0[1]) * t / n
            z = p0[2] + (p1[2] - p0[2]) * t / n
            best = None
            for k, (q, b) in enumerate(zip(polys, boxes)):
                if not (b[0] <= x <= b[2] and b[1] <= y <= b[3]):
                    continue
                if navsurf._inside_xy(q, x, y):
                    zq = navsurfgen.plane_z(q, x, y)
                    if zq <= z and (best is None or zq > best[0]):
                        best = (zq, k)
            if best is not None and (small is None or small[best[1]]):
                out.append((x, y, best[0]))
    return out


def floor_reached(level_name, fl):
    """`floor()`'s answer `fl` with no door cut and all the walkable floor the level's
    spawners, route points, rifts and player starts stand on (their walkable graph
    components): what a mission draws unless it cuts at the doors (cut_at_doors)."""
    if 'reached_polys' not in fl:
        return dict(fl, doors=[])
    polys = fl['reached_polys']
    return dict(fl, doors=[], polys=polys, poly_floors=source_floors(level_name, polys),
                reached=True, anchors=fl['reached_anchors'])


def floor(level_name, plan, level=None):
    """{polys, rects, erase, block, anchors, rifts, doors, notes}: what the minimap
    draws as floor, after the plan.

    `polys` are walkable polygons [(x, y, z)], `rects` the plan's added kit floors
    (lo, hi), and `erase` [(lo, hi, [keeper (lo, hi)])]: footprints of removed or moved
    pieces that held up walkable floor, each less the kept pieces at the same height
    over it. `block` are added or moved pieces standing on the floor, `anchors` the
    points (routes, rifts, player starts) whose connected floor is drawn, `rifts` the
    rifts and `doors` [(x, y, yaw degrees, width uu)] the doors after the plan.
    """
    import numpy as np
    from riftlord.formats.omd3 import meshcat
    from riftlord.mapeditor import leveldata
    from riftlord.build.minimap import mapdataemit
    from riftlord.build.minimap import minimapsolid
    from riftlord.mapeditor import scratch
    if scratch.is_scratch(plan):
        return _scratch_floor(level_name, plan)
    level = level if level is not None else leveldata.level(level_name)
    polys, ids, chains, notes = _graph_polys(level_name, plan)
    every = list(polys)
    flyer_floor = _pieces_of_area(polys, ids, FLYER_FLOOR_AREA)
    area = wanted(plan).get('area')
    if area is not None:
        keep = [k for k, q in enumerate(polys)
                if _inside(area, sum(v[0] for v in q) / len(q), sum(v[1] for v in q) / len(q))]
        if len(keep) < len(polys):
            notes.append('%d polygon(s) outside the map area left out' % (len(polys) - len(keep)))
        polys, ids = [polys[k] for k in keep], [ids[k] for k in keep]
    anchors = _anchors(level_name, plan, level, chains)
    if area is not None:
        anchors = [a for a in anchors if _inside(area, a[0], a[1])]
    # the walkable floor under the flyers' routes (islands and pit floors only flyers
    # cross: ScenarioDLC_01's islands, Sand_05's flyer rooms), which the shipped art draws
    flyers = [p for p in _flyer_anchors(level_name, plan, polys, ids)
              if _inside(area, p[0], p[1])]
    anchors = anchors + flyers
    played = _played(polys, ids, anchors)
    # ... and with the spawners too: the floor of the reached cut (floor_reached)
    spawns = [tuple(s['location']) for s in level.get('spawners') or [] if s.get('location')
              and _inside(area, s['location'][0], s['location'][1])]
    reached = _played(polys, ids, anchors + spawns) or list(polys)
    if played and len(played) < len(polys):
        notes.append('%d polygon(s) no route, rift or player start reaches left out'
                     % (len(polys) - len(played)))
        polys = played
    rects, rect_polys = [], []
    for r, q in zip(*_floor_rects(plan)):
        if _inside(area, (r[0][0] + r[1][0]) / 2.0, (r[0][1] + r[1][1]) / 2.0):
            rects.append(r)
            rect_polys.append(q)
    if rects:
        notes.append('%d kit or Fab floor(s) the plan adds' % len(rects))
    cat = meshcat.load()
    pieces = level.get('pieces') or []
    removed = {int(i) for i in plan.get('removed') or []}
    moved = {int(e['id']): e['matrix'] for e in plan.get('edits') or []
             if isinstance(e, dict) and 'id' in e}
    gone, kept, placed = [], [], []
    #: (lo, hi) of the removed or moved pieces outside UNDRAWN_CATEGORIES, held floor or not
    knocked = []
    #: (lo, hi, mesh) of every piece standing after the plan
    after = []
    #: (mesh, matrix) of the pieces whose surfaces are the solid (riftlord.minimapsolid)
    solid = []
    #: (kind, footprint, matrix) of the stair and platform pieces (piece_kind)
    shaped = []
    #: the solid of the level as shipped, which an edited level's grid is built from
    solid_base = []
    #: (lo, hi) of the plants, and the collision outlines of the rocks (_nature)
    flora, rocks = [], []
    for i, piece in enumerate(pieces):
        pid = piece.get('id', i)
        box0 = _piece_box(piece, piece['matrix'], cat)
        if box0 and minimapsolid.drawn(piece.get('mesh'), (cat.get(piece.get('mesh')) or {})
                                       .get('category'), box0[0], box0[1]):
            solid_base.append((piece.get('mesh'), piece['matrix']))
        if pid in removed or pid in moved:
            box = box0
            if box:
                gone.append(box)
                if (cat.get(piece.get('mesh')) or {}).get('category') not in UNDRAWN_CATEGORIES:
                    knocked.append(box)
        if pid not in removed:
            matrix = moved.get(pid, piece['matrix'])
            box = _piece_box(piece, matrix, cat)
            if box:
                kept.append(box)
                after.append((box[0], box[1], piece.get('mesh'), _tilted(matrix)))
                if pid in moved:
                    placed.append(box)
                if minimapsolid.drawn(piece.get('mesh'), (cat.get(piece.get('mesh')) or {})
                                      .get('category'), box[0], box[1]):
                    solid.append((piece.get('mesh'), matrix))
                kind = piece_kind(piece.get('mesh'))
                if kind:
                    foot = _footprint(piece.get('mesh'), matrix, cat)
                    if foot is not None and pid in moved:
                        foot = dict(foot, moved=True)
                    shaped.append((kind, foot, matrix))
                _nature(piece.get('mesh'), matrix, box, cat, flora, rocks)
    for item in plan.get('added') or []:
        if item.get('kind') in ('kit', 'fab'):
            box = scratch._piece_box(item, cat)
        else:
            box = _piece_box({'mesh': item.get('name')}, item.get('matrix'), cat)
            if box and minimapsolid.drawn(item.get('name'), (cat.get(item.get('name')) or {})
                                          .get('category'), box[0], box[1]):
                solid.append((item.get('name'), item.get('matrix')))
        if box:
            placed.append(box)
            after.append((box[0], box[1], item.get('name') or item.get('mesh'),
                          _tilted(item.get('matrix'))))
            if item.get('kind') not in ('kit', 'fab'):
                _nature(item.get('name'), item.get('matrix'), box, cat, flora, rocks)
    erase, block = [], []
    if (gone or placed) and polys:
        plo = np.array([[min(v[0] for v in q), min(v[1] for v in q)] for q in polys])
        phi = np.array([[max(v[0] for v in q), max(v[1] for v in q)] for q in polys])
        pz = np.array([sum(v[2] for v in q) / len(q) for q in polys])
        klo = np.array([b[0] for b in kept]) if kept else np.zeros((0, 3))
        khi = np.array([b[1] for b in kept]) if kept else np.zeros((0, 3))
        for lo, hi in gone:
            top = hi[2]
            holds = ((plo[:, 0] < hi[0]) & (phi[:, 0] > lo[0]) & (plo[:, 1] < hi[1])
                     & (phi[:, 1] > lo[1]) & (pz - SUPPORT_BELOW <= top)
                     & (top <= pz + SUPPORT_ABOVE))
            if not holds.any():
                continue
            near = ((klo[:, 0] < hi[0]) & (khi[:, 0] > lo[0]) & (klo[:, 1] < hi[1])
                    & (khi[:, 1] > lo[1]) & (np.abs(khi[:, 2] - top) <= SUPPORT_BELOW))
            erase.append((lo, hi, [(klo[k].tolist(), khi[k].tolist())
                                   for k in np.nonzero(near)[0]]))
        if erase:
            notes.append('floor over %d removed or moved piece(s) taken out' % len(erase))
        for lo, hi in placed:
            stands = ((plo[:, 0] < hi[0]) & (phi[:, 0] > lo[0]) & (plo[:, 1] < hi[1])
                      & (phi[:, 1] > lo[1]) & (lo[2] < pz + scratch.OBSTACLE_BELOW)
                      & (hi[2] > pz + scratch.OBSTACLE_ABOVE))
            if stands.any():
                block.append((lo, hi))
        if block:
            notes.append('%d added or moved piece(s) stand on the floor as walls' % len(block))
    rifts = [tuple(r) for r in mapdataemit.markers_for(level_name, plan, level=level)[0]]
    doors = _mission_doors(level_name, plan, level)
    pieces_after = [(lo, hi, mesh, (cat.get(mesh) or {}).get('category'), tilt)
                    for lo, hi, mesh, tilt in after
                    if PROP_AREA <= (hi[0] - lo[0]) * (hi[1] - lo[1]) <= PIECE_AREA]
    pieces_after = [p for p in pieces_after if p[3] not in UNDRAWN_CATEGORIES]
    cover, planes = [], []
    for lo, hi, mesh, _tilt in after:
        if (cat.get(mesh) or {}).get('category') in UNDRAWN_CATEGORIES:
            continue
        foot = (hi[0] - lo[0]) * (hi[1] - lo[1])
        if hi[2] - lo[2] <= LIQUID_THICK and foot >= PROP_AREA:
            planes.append((lo, hi))
        elif foot <= PIECE_AREA:
            cover.append((lo, hi))
    return {'polys': polys, 'rects': rects, 'rect_polys': rect_polys, 'erase': erase, 'block': block,
            'knocked': knocked,
            'anchors': anchors, 'rifts': rifts, 'doors': doors, 'pieces': pieces_after,
            'every': every, 'cover': cover, 'planes': planes, 'solid': solid,
            'solid_base': solid_base,
            'poly_floors': source_floors(level_name, polys), 'level_name': level_name,
            'reached_polys': reached, 'reached_anchors': anchors + spawns, 'flyers': flyers,
            'flyer_legs': _flyer_routes(level_name, plan, from_spawner=True),
            'every_rects': list(zip(rects, rect_polys)), 'flyer_floor': flyer_floor,
            'flora': flora, 'rocks': rocks,
            'shaped': [x for x in shaped if x[1] is not None],
            'notes': notes}


def _added_solid(item, cat):
    """[(mesh, row-vector 4x4 flat)] of one added piece's meshes: a mesh as placed, a kit
    piece's parts (leveldata.kit_parts) under the piece's own transform."""
    from riftlord.formats.omd3 import omd3level
    from riftlord.mapeditor import leveldata
    from riftlord.mapeditor import scratch
    if item.get('kind') == 'mesh' or (item.get('kind') not in ('kit', 'fab')
                                      and item.get('matrix')):
        return [(item.get('name'), item.get('matrix'))] if item.get('matrix') else []
    if item.get('kind') != 'kit':
        return []
    rot, t = scratch._piece_matrix(item)
    out = []
    for part in leveldata.kit_parts(item['name']).get('parts') or []:
        if not cat.get(part.get('mesh')):
            continue
        L = omd3level._matrix(part.get('loc') or (0, 0, 0), part.get('rot') or (0, 0, 0),
                              part.get('scale') or (1, 1, 1))
        RL = [[sum(rot[i][k] * L[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
        tw = [sum(rot[i][k] * L[k][3] for k in range(3)) + t[i] for i in range(3)]
        flat = [RL[0][0], RL[1][0], RL[2][0], 0.0,
                RL[0][1], RL[1][1], RL[2][1], 0.0,
                RL[0][2], RL[1][2], RL[2][2], 0.0,
                tw[0], tw[1], tw[2], 1.0]
        out.append((part.get('mesh'), flat))
    return out


def _floor_rects(plan):
    """([(lo, hi)], [XY outline or None]) of the plan's added walkable kit and Fab floors:
    each piece's box, and its own turned outline (a floor laid at an angle is drawn at its
    true footprint, not as the grid cells scratch.floor_boxes counts for walking: The
    Hallway's turned rooms came out stair-stepped)."""
    from riftlord.formats.omd3 import meshcat
    from riftlord.mapeditor import leveldata
    from riftlord.mapeditor import scratch
    from riftlord.mapeditor import compounds
    cat = meshcat.load()
    rows = {r['name']: r for r in leveldata.catalogue()}
    rects, polys = [], []
    for item in plan.get('added') or []:
        if item.get('kind') == 'fab':
            entry = scratch._fab_entry(item)
            row = entry and {'kind': 'fab', 'role': entry.get('role')}
        else:
            row = rows.get(item.get('name'))
        if not row or row.get('kind') not in ('kit', 'fab') or not scratch._walkable(row):
            continue
        box = scratch._piece_box(item, cat)
        if box is None:
            continue
        try:
            outline = scratch._footprint(item, cat, 0.0)
        except Exception:                                # noqa: BLE001
            outline = None
        rects.append((list(box[0]), list(box[1])))
        polys.append([tuple(p) for p in outline] if outline else None)
    for lo, hi, _s, _y in compounds.pit_floors(plan):
        rects.append((list(lo), list(hi)))
        polys.append(None)
    return rects, polys


def _scratch_floor(level_name, plan):
    """`floor()` for a scratch level: its added kit and Fab floors, the solid of every
    added piece, and its rift, doors and route points (riftlord/scratch.py)."""
    from riftlord.formats.omd3 import meshcat
    from riftlord.build.minimap import minimapsolid
    from riftlord.mapeditor import scratch
    cat = meshcat.load()
    base = (plan.get('scratch') or {}).get('base') or level_name
    rects, rect_polys = _floor_rects(plan)
    solid, shaped, flora, rocks = [], [], [], []
    for item in plan.get('added') or []:
        for mesh, flat in _added_solid(item, cat):
            box = _piece_box({'mesh': mesh}, flat, cat)
            _nature(mesh, flat, box, cat, flora, rocks)
            if box and minimapsolid.drawn(mesh, (cat.get(mesh) or {}).get('category'),
                                          box[0], box[1]):
                solid.append((mesh, flat))
            kind = piece_kind(mesh)
            if kind:
                foot = _footprint(mesh, flat, cat)
                if foot is not None:
                    shaped.append((kind, foot, flat))
    view = scratch.view(base)
    rifts, doors, points = _markers(base, plan, view)
    starts = [tuple(s['at']) for s in plan.get('playerStarts') or [] if s.get('at')]
    door_rows = [(d['at'][0], d['at'][1], float(d.get('rotation') or 0.0),
                  DOOR_WIDTHS.get(d.get('cls') or 'BP_Door_Interior_3W_C', 900.0),
                  d['at'][2] if len(d['at']) > 2 else 0.0)
                 for d in plan.get('doors') or [] if d.get('at')]
    notes = ['%d floor(s) and %d piece(s) of the scratch level' % (len(rects), len(solid))]
    return {'polys': [], 'rects': rects, 'erase': [], 'block': [], 'anchors':
            [tuple(p) for p in points] + starts, 'rifts': [tuple(r) for r in rifts],
            'doors': door_rows, 'pieces': [], 'every': [], 'cover': [], 'planes': [],
            'solid': solid, 'solid_base': None, 'poly_floors': [], 'shaped': shaped,
            'notes': notes, 'scratch_base': base, 'rect_polys': rect_polys,
            'flora': flora, 'rocks': rocks,
            'flyer_legs': _flyer_routes(base, plan, from_spawner=True),
            'every_rects': list(zip(rects, rect_polys))}


#: Width across a door of each class, in uu (3W = three 300 uu cells). A force field's is
#: its DoorMesh plane (100 uu) times its larger scale.
DOOR_WIDTHS = {'BP_Door_Interior_3W_C': 900.0, 'BP_Door_Exterior_3W_C': 900.0,
               'BP_Door_Exterior_3W_SAND_C': 900.0, 'BP_Door_Player_3W_C': 900.0,
               'BP_Door_Exterior_2W_C': 600.0, 'BP_Door_Interior_4W_C': 1200.0}
FORCEFIELD_PLANE = 100.0
#: A door's cut runs this far past each jamb, in uu, so it meets the walls either side.
CUT_OVERLAP = 150.0
#: A passage narrower than this does not carry the playable area past it, in uu: one
#: 300 uu kit cell (the narrowest door is 600 uu). Gaps beside a door's frame in the
#: shipped walkable graph are narrower. Geometry-only fit (2026-10-25, reached cut, trained
#: on 14 levels, checked on the other 19): 200 -> +0.0036 train, +0.0035 held out with
#: OUTLINE_EPS 3; 450 -> -0.011.
PASSAGE = 200.0
#: Thickness of a door's cut, in uu: the line the approved cut mode drew on Beset, the
#: shipped band of Scenario_09 (12 px at 22.1 uu per pixel).
CUT_WIDTH = 265.0
#: The cut starts DOOR_CUT_INSET inside the door line and runs CUT_WIDTH outward (away
#: from the nearest rift): over the 56 shipped doors where the art stops near the door its
#: floor ends a median 51 uu before the door line (quartiles 80 and 18); a centred cut
#: ended Regenerate's floor 130 uu before every door (median of 47).
DOOR_CUT_OUTSIDE = True
DOOR_CUT_INSET = 51.0


def _mission_doors(level_name, plan, level):
    """[(x, y, yaw degrees, width uu, z)] of the doors after the plan."""
    gone = {w.get('name') for w in plan.get('wiring') or [] if w.get('gone')}
    out = []
    for d in (level.get('doors') or {}).values():
        if d['export'] in gone:
            continue
        rot = d.get('rotation')
        yaw = rot[1] if isinstance(rot, (list, tuple)) else float(rot or 0.0)
        scale = (d.get('components') or {}).get('DoorMesh', {}).get('scale') or (1, 1, 1)
        width = DOOR_WIDTHS.get(d['cls'], FORCEFIELD_PLANE * max(scale[0], scale[1]))
        loc = d['location']
        out.append((loc[0], loc[1], yaw, width, loc[2] if len(loc) > 2 else 0.0))
    for d in plan.get('doors') or []:
        cls = d.get('cls') or 'BP_Door_Interior_3W_C'
        width = DOOR_WIDTHS.get(cls, FORCEFIELD_PLANE * max(float(d.get('scaleX') or 1),
                                                            float(d.get('scaleY') or 1)))
        at = d['at']
        out.append((at[0], at[1], float(d.get('rotation') or 0.0), width,
                    at[2] if len(at) > 2 else 0.0))
    return out


# -- pixel helpers ------------------------------------------------------------------

#: The eight neighbour offsets.
_NEIGHBOURS = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1))


def _shifted(a, dy, dx):
    """`a` moved by (dy, dx), the uncovered edge filled with zero / False."""
    out = __import__('numpy').zeros_like(a)
    h, w = a.shape
    ys, yd = (slice(0, h - dy), slice(dy, h)) if dy >= 0 else (slice(-dy, h), slice(0, h + dy))
    xs, xd = (slice(0, w - dx), slice(dx, w)) if dx >= 0 else (slice(-dx, w), slice(0, w + dx))
    out[yd, xd] = a[ys, xs]
    return out


def _grow(mask, steps, square=False):
    """`mask` (bool array) dilated by `steps` pixels: an octagon, or a square."""
    out = mask.copy()
    for k in range(int(steps)):
        m = out.copy()
        m[1:, :] |= out[:-1, :]
        m[:-1, :] |= out[1:, :]
        m[:, 1:] |= out[:, :-1]
        m[:, :-1] |= out[:, 1:]
        if square or k % 2:
            m[1:, 1:] |= out[:-1, :-1]
            m[:-1, :-1] |= out[1:, 1:]
            m[1:, :-1] |= out[:-1, 1:]
            m[:-1, 1:] |= out[1:, :-1]
        out = m
    return out


def _shrink(mask, steps, square=False):
    return ~_grow(~mask, steps, square)


def _spread(lab, steps, within=None):
    """Integer labels `lab` (0 = none) spread into unlabelled pixels `steps` times
    through the eight neighbours, only into `within` when given. Within a step the
    neighbours are taken in _NEIGHBOURS' order, each from the labels as the ones before
    it left them."""
    import numpy as np
    out = lab.copy()
    empty = out == 0
    if within is not None:
        empty &= within
    empty = np.ascontiguousarray(empty)
    if int(steps) < 1 or not empty.any():
        return out
    # the first step over the whole picture
    before = empty.copy()
    for dy, dx in _NEIGHBOURS:
        src = _shifted(out, dy, dx)
        take = empty & (src > 0)
        out[take] = src[take]
        empty &= ~take
    # each later step looks only round the pixels the step before labelled (a pixel
    # still unlabelled had no labelled neighbour before that step), and round those this
    # step has labelled so far
    h, w = out.shape
    flat_out = out.reshape(-1)
    flat_empty = empty.reshape(-1)
    front = np.flatnonzero(before & ~empty)
    for _ in range(int(steps) - 1):
        added = []
        for dy, dx in _NEIGHBOURS:
            srcs = np.concatenate([front] + added)
            ny, nx = srcs // w + dy, srcs % w + dx
            inside = (ny >= 0) & (ny < h) & (nx >= 0) & (nx < w)
            pix = ny[inside] * w + nx[inside]
            keep = flat_empty[pix]
            pix, srcs = pix[keep], srcs[inside][keep]
            flat_out[pix] = flat_out[srcs]
            flat_empty[pix] = False
            added.append(pix)
        front = np.concatenate(added)
        if not len(front):
            break
    return out


def _components(mask):
    """[(area, [flat indices])] of `mask`'s 4-connected components."""
    import numpy as np
    h, w = mask.shape
    flat = mask.ravel().copy()
    out = []
    for start in np.flatnonzero(flat):
        if not flat[start]:
            continue
        flat[start] = False
        stack, got = [int(start)], []
        while stack:
            i = stack.pop()
            got.append(i)
            y, x = divmod(i, w)
            for j, ok in ((i - w, y > 0), (i + w, y < h - 1), (i - 1, x > 0),
                          (i + 1, x < w - 1)):
                if ok and flat[j]:
                    flat[j] = False
                    stack.append(j)
        out.append((len(got), got))
    return out


def _reach(mask, seeds):
    """Pixels of `mask` 4-connected to the True pixels of `seeds` through `mask`: the
    row runs the seeds touch, the column runs those cross, the row runs those cross, ...
    each round looking only at the runs the round before reached."""
    import numpy as np
    h, w = mask.shape
    if not mask.any():
        return np.zeros(mask.shape, bool)

    def runs(m):
        # (run id per pixel in m's row order, 0 off m; each run's first pixel; its end)
        flat = np.ascontiguousarray(m).reshape(-1)
        n = m.shape[1]
        start = flat.copy()
        start[1:] &= ~flat[:-1]
        start[::n] = flat[::n]
        end = flat.copy()
        end[:-1] &= ~flat[1:]
        end[n - 1::n] = flat[n - 1::n]
        return (np.where(flat, np.cumsum(start), 0),
                np.concatenate([[0], np.flatnonzero(start)]),
                np.concatenate([[0], np.flatnonzero(end) + 1]))
    rows, r0, r1 = runs(mask)
    cols_t, c0, c1 = runs(mask.T)
    # each pixel's column run in row order, and its row run in column order
    cols = cols_t.reshape(w, h).T.reshape(-1)
    rows_t = rows.reshape(h, w).T.reshape(-1)

    def pixels(lo, hi):
        # every index of the runs [lo, hi)
        n = hi - lo
        return np.repeat(lo - np.cumsum(n) + n, n) + np.arange(int(n.sum()))

    def new(ids, seen):
        # the runs named in `ids` not yet `seen`, marked seen
        got = np.zeros(len(seen), bool)
        got[ids] = True
        got[0] = False
        got &= ~seen
        seen |= got
        return np.flatnonzero(got)
    row_seen = np.zeros(len(r0), bool)
    col_seen = np.zeros(len(c0), bool)
    got = new(rows[(seeds & mask).reshape(-1)], row_seen)
    while len(got):
        got = new(cols[pixels(r0[got], r1[got])], col_seen)
        got = new(rows_t[pixels(c0[got], c1[got])], row_seen)
    return row_seen[rows].reshape(h, w) & mask


def _outside(mask):
    """Pixels of ~mask connected to the image border through ~mask."""
    import numpy as np
    free = ~mask
    edge = np.zeros_like(mask)
    edge[0, :] = edge[-1, :] = edge[:, 0] = edge[:, -1] = True
    return _reach(free, edge & free)


def _floor_near(mask, u, v, reach):
    """(u, v) of the `mask` pixel nearest (u, v) within `reach` pixels, or None."""
    import numpy as np
    h, w = mask.shape
    u0, u1 = max(0, u - reach), min(w, u + reach + 1)
    v0, v1 = max(0, v - reach), min(h, v + reach + 1)
    if u0 >= u1 or v0 >= v1:
        return None
    ys, xs = np.nonzero(mask[v0:v1, u0:u1])
    if not len(xs):
        return None
    k = int(np.argmin(np.maximum(np.abs(xs + u0 - u), np.abs(ys + v0 - v))))
    return (int(xs[k] + u0), int(ys[k] + v0))


def _door_bounds(doors, rifts):
    """[x0, y0, x1, y1] (world uu, +-inf where open) of the play area the doors bound,
    or None. A door bounds the axis its offset from the rifts' centre mostly runs
    along; on an axis with such doors on both sides, each side ends at the outermost
    one, CUT_WIDTH / 2 past its line."""
    import math
    if not doors or not rifts:
        return None
    centre = (sum(r[0] for r in rifts) / float(len(rifts)),
              sum(r[1] for r in rifts) / float(len(rifts)))
    out = [-math.inf, -math.inf, math.inf, math.inf]
    pad = CUT_WIDTH / 2.0
    for k in (0, 1):
        along = [d[k] for d in doors
                 if abs(d[k] - centre[k]) > abs(d[1 - k] - centre[1 - k])]
        low = [v for v in along if v < centre[k]]
        high = [v for v in along if v > centre[k]]
        if low and high:
            out[k] = min(low) - pad
            out[k + 2] = max(high) + pad
    return out if any(math.isfinite(v) for v in out) else None


def _unproject(project, size):
    """(u_px, v_px) arrays -> world (x, y) arrays: the inverse of `project` at `size`."""
    import numpy as np
    o = np.array(project((0.0, 0.0)))
    m = np.array([np.array(project((1.0, 0.0))) - o, np.array(project((0.0, 1.0))) - o]).T
    inv = np.linalg.inv(m)

    def back(u, v):
        du, dv = u / size - o[0], v / size - o[1]
        return inv[0, 0] * du + inv[0, 1] * dv, inv[1, 0] * du + inv[1, 1] * dv
    return back


def _cover_rects(boxes, project, size):
    """(u0, u1, v0, v1, z0, z1) arrays of world boxes' footprints in pixels."""
    import numpy as np
    if not boxes:
        return None
    o = np.array(project((0.0, 0.0)))
    ex = np.array(project((1.0, 0.0))) - o
    ey = np.array(project((0.0, 1.0))) - o
    lo = np.array([b[0] for b in boxes], float)
    hi = np.array([b[1] for b in boxes], float)
    us, vs = [], []
    for x in (lo[:, 0], hi[:, 0]):
        for y in (lo[:, 1], hi[:, 1]):
            us.append((o[0] + x * ex[0] + y * ey[0]) * size)
            vs.append((o[1] + x * ex[1] + y * ey[1]) * size)
    us, vs = np.array(us), np.array(vs)
    return (us.min(0), us.max(0), vs.min(0), vs.max(0), lo[:, 2], hi[:, 2])


#: An opening filling at least this share of its bounding box is drawn as that box.
VOID_SQUARE = 0.8
#: A liquid opening takes its plane's square footprint when the plane is at most this
#: many times the opening's area.
LIQUID_FIT = 2.0
#: Holes up to this size are pillars drawn at their pieces' footprints, in uu squared
#: (a 600 uu square: the kit's largest columns).
PILLAR_MAX_AREA = 600.0 * 600.0
#: ... when the pieces' footprints match the hole this well (IoU).
PILLAR_FIT = 0.5


def _pillar_footprint(pix, size, cover, z, flo):
    """(window slices, bool mask) of the union of the standing pieces' footprints that
    make up the hole `pix`, or None when they do not match it."""
    import numpy as np
    if cover is None:
        return None
    rows, cols = pix // size, pix % size
    r0, r1, c0, c1 = rows.min(), rows.max() + 1, cols.min(), cols.max() + 1
    u0, u1, v0, v1, z0, z1 = cover
    pad = 3
    ring = flo[max(0, r0 - pad):r1 + pad, max(0, c0 - pad):c1 + pad]
    if not ring.any():
        return None
    fz = float(np.median(z[max(0, r0 - pad):r1 + pad, max(0, c0 - pad):c1 + pad][ring]))
    near = ((u1 > c0) & (u0 < c1) & (v1 > r0) & (v0 < r1) & (z0 < fz + WALL_CUT)
            & (z1 > fz + WALL_LOW))
    if not near.any():
        return None
    # a window round the hole and every candidate piece
    w0 = max(0, min(r0, int(v0[near].min())) - 1)
    w1 = min(size, max(r1, int(np.ceil(v1[near].max()))) + 1)
    x0 = max(0, min(c0, int(u0[near].min())) - 1)
    x1 = min(size, max(c1, int(np.ceil(u1[near].max()))) + 1)
    hole = np.zeros((w1 - w0, x1 - x0), bool)
    hole[rows - w0, cols - x0] = True
    out = np.zeros_like(hole)
    for k in np.flatnonzero(near):
        a0, a1 = int(round(u0[k])) - x0, int(round(u1[k])) - x0
        b0, b1 = int(round(v0[k])) - w0, int(round(v1[k])) - w0
        a0, b0 = max(0, a0), max(0, b0)
        if a1 <= a0 or b1 <= b0:
            continue
        # a piece mostly inside the hole: part of the pillar, not a wall beside it
        if hole[b0:b1, a0:a1].mean() >= 0.5:
            out[b0:b1, a0:a1] = True
    union = (out | hole).sum()
    if not union or (out & hole).sum() / float(union) < PILLAR_FIT:
        return None
    return (slice(w0, w1), slice(x0, x1)), out


def _covered(pix, flo, z, cover, above=True):
    """Share of the hole `pix` (flat indices) under the `cover` pieces: with `above`,
    only those rising above the floor around it."""
    import numpy as np
    if cover is None:
        return 0.0
    h, w = flo.shape
    rows, cols = pix // w, pix % w
    r0, r1, c0, c1 = rows.min(), rows.max() + 1, cols.min(), cols.max() + 1
    u0, u1, v0, v1, z0, z1 = cover
    near = (u1 > c0) & (u0 < c1) & (v1 > r0) & (v0 < r1)
    if above:
        pad = 3
        ring = flo[max(0, r0 - pad):r1 + pad, max(0, c0 - pad):c1 + pad]
        if not ring.any():
            return 0.0
        fz = float(np.median(z[max(0, r0 - pad):r1 + pad, max(0, c0 - pad):c1 + pad][ring]))
        near &= z1 > fz + LEDGE_STEP
    if not near.any():
        return 0.0
    hit = np.zeros((r1 - r0, c1 - c0), dtype=bool)
    for k in np.flatnonzero(near):
        a0, a1 = int(max(c0, u0[k])) - c0, int(min(c1, np.ceil(u1[k]))) - c0
        b0, b1 = int(max(r0, v0[k])) - r0, int(min(r1, np.ceil(v1[k]))) - r0
        if a1 > a0 and b1 > b0:
            hit[b0:b1, a0:a1] = True
    return float(hit[rows - r0, cols - c0].mean())


# -- the solid geometry (riftlord/minimapsolid.py) -----------------------------------

#: Geometry standing between WALL_LOW and WALL_CUT above the floor is wall, in uu: the
#: floor plan's cut. WALL_LOW is the walkable graph's step height (LEDGE_STEP); the cut
#: passes through railings (about 100 uu) and under arches and beams.
WALL_LOW = LEDGE_STEP
WALL_CUT = 150.0
#: A surface within this far below the floor (or up to WALL_LOW above) holds it up, in
#: uu: a floor mesh's top sits a few uu under the walkable graph.
SUPPORT_DOWN = 60.0
#: The walkable floor is grown back toward the walls at most this far, in uu; past the
#: agent radius only over a surface that holds it up.
GROW_MAX = 150.0
#: Shares of the agent radius and of GROW_MAX the walkable graph's floor is grown back by.
#: None: the shipped art draws the walkable graph's own floor, its edge an agent radius
#: off the walls (49 floors, 2026-10-23: 0 / 0 scored 0.797 against 0.762 at 1 / 1, 45
#: floors better, 3 worse; 0.5 / 0.5 and 0 / 1 sat between).
AGENT_GROW = 0.0
GROW_SHARE = 0.0
#: Past GROW_MAX the floor grows on, at most this far, only where the source level's own
#: art draws floor, in uu (Scenario_11's and _14's player-only galleries, 1,000-2,500 uu
#: from the walkable graph).
GROW_ART = 3000.0
#: Geometry only (art_free): the floor grows on over the solid at most this far past the
#: walkable graph, in uu, at the floor's own heights (0: not at all).
GEO_GROW = 0.0
#: A piece of floor no rift or route reaches is drawn when at least this share of it is
#: floor in the source level's own art for the floor, and it covers ART_KEEP_AREA uu²
#: (one 600 uu kit tile square) or more.
ART_KEEP = 0.5
ART_KEEP_AREA = 600.0 ** 2
#: ART_KEEP_AREA is measured on the piece grown back over the PASSAGE shrink.
ART_KEEP_FULL = True
#: Sloped graph polygons and kit flights are drawn on a floor only within this share of
#: the width of that floor's own art floor (4 px at 1024: over the white hatch lines of
#: Scenario_16's corner flights).
CROSS_ART_SLACK = 4 / 1024.0
#: On a sloped graph plane the cut and the support are widened by this much, in uu: half
#: the riser of Scenario_16's flights of floor tiles (150 uu).
SLOPE_SLACK = 80.0
#: A grown pixel follows the surface up to this far above its neighbour's floor, in uu
#: (a stair tread a pixel on).
GROW_RISE = 30.0
#: A floor pixel's height is the highest surface within SNAP of its graph plane, in uu,
#: that has SNAP_HEAD of clear space above it: the walkable graph's polygons are flat
#: over whole flights (Scenario_13's top stairs read 1,077 uu where the treads are at
#: 1,200-1,500), so the plane only says which surface. SNAP_TRIES surfaces are tried
#: from the top down before the plane is kept.
SNAP = 350.0
SNAP_HEAD = WALL_CUT
SNAP_TRIES = 8
#: A gap between floors is a drop (closed up to a ledge line) only when at least this
#: share of it has a surface between the two floors' heights.
DROP_HELD = 0.5
#: The solid is sampled on a grid this many times coarser than the drawing (the
#: drawing is SUPERSAMPLE times the texture; the solid at the texture's own size).
SOLID_COARSE = SUPERSAMPLE


def _solid_grid(fl, project, size, uu):
    """A riftlord.minimapsolid Grid of the floor's pieces at size / SOLID_COARSE, or None
    when the plan names no solid."""
    from riftlord.build.minimap import minimapsolid
    if not fl.get('solid'):
        return None
    n = max(1, size // (fl.get('solid_coarse') or SOLID_COARSE))
    key = (id(fl['solid']), len(fl['solid']), tuple(project((0.0, 0.0))),
           tuple(project((1000.0, 1000.0))), n)
    if key not in _GRIDS:
        if len(_GRIDS) >= GRID_CACHE:
            _GRIDS.pop(next(iter(_GRIDS)))
        _GRIDS[key] = minimapsolid.grid(fl['solid'], project, n, base=fl.get('solid_base'))
    got = _GRIDS[key]
    return None if got.empty else got


#: Solid grids kept between drawings of one floor list (one build or preview draws the
#: same frame for every floor), oldest dropped first.
GRID_CACHE = 2
_GRIDS = {}


def _coarse(grid, size, ys, xs):
    """Flat grid indices of drawing pixels (ys, xs)."""
    import numpy as np
    n = grid.size
    return (ys.astype(np.int64) * n // size) * n + (xs.astype(np.int64) * n // size)


def _grow_floor(idx, H, planes, grid, size, free, most, sloped=None, allow=None,
                zband=None):
    """(labels, heights): plane labels `idx` and floor heights `H` (nan where the plane
    stands in) grown into unlabelled pixels (only `allow`ed ones, when given): `free`
    steps where no wall stands above the neighbour's floor, then up to `most` steps
    where also a surface holds it up. Each grown pixel takes the surface within a step
    of its neighbour's height, and only one within `zband` (lo, hi uu) when given. Each
    step looks only round the pixels the last one added, so a long growth costs its
    front, not the picture."""
    import numpy as np
    a, b, c = planes
    out = idx.copy()
    H = H.copy()
    h, w = out.shape
    flat_out = out.ravel()
    flat_H = H.ravel()
    lab0 = out > 0
    edge = lab0 & ~_shrink(lab0, 1, True)
    front = np.flatnonzero(edge)
    offs = [(dy, dx) for dy, dx in _NEIGHBOURS]
    allow_flat = allow.ravel() if allow is not None else None
    for step in range(int(most)):
        if not len(front):
            break
        fy, fx = front // w, front % w
        cand_pix, cand_src = [], []
        for dy, dx in offs:
            ny, nx = fy + dy, fx + dx
            inb = (ny >= 0) & (ny < h) & (nx >= 0) & (nx < w)
            npix = ny[inb] * w + nx[inb]
            src = front[inb]
            keep = flat_out[npix] == 0
            if allow_flat is not None:
                keep &= allow_flat[npix]
            cand_pix.append(npix[keep])
            cand_src.append(src[keep])
        cp = np.concatenate(cand_pix)
        cs = np.concatenate(cand_src)
        if not len(cp):
            break
        cp, first = np.unique(cp, return_index=True)
        cs = cs[first]
        ys, xs = cp // w, cp % w
        lab = flat_out[cs]
        z = flat_H[cs].copy()
        plane = np.isnan(z) if sloped is None else (np.isnan(z) | sloped[lab])
        z[plane] = (a[lab] * xs + b[lab] * ys + c[lab])[plane]
        at = _coarse(grid, size, ys, xs)
        # on a sloped plane (a flight laid as steps) the treads stand up to half a
        # riser off it: the cut and the support are measured with SLOPE_SLACK
        slack = (np.where(sloped[lab], SLOPE_SLACK, 0.0) if sloped is not None
                 else np.zeros(len(lab)))
        ok = ~grid.between(at, z + WALL_LOW + slack, z + WALL_CUT + slack)
        if step >= free:
            ok &= grid.between(at, z - SUPPORT_DOWN - slack, z + WALL_LOW + slack)
        if not ok.any():
            break
        got = grid.highest(at[ok], z[ok] - LEDGE_STEP, z[ok] + GROW_RISE)
        zk = np.where(np.isnan(got) | (slack[ok] > 0), z[ok], got)
        pick = cp[ok]
        lab_k = lab[ok]
        if zband is not None:
            inside = (zk >= zband[0]) & (zk < zband[1])
            pick, lab_k, zk = pick[inside], lab_k[inside], zk[inside]
            if not len(pick):
                break
        flat_out[pick] = lab_k
        flat_H[pick] = zk
        front = pick
    return out, H


def _spread_height(H, floor, steps):
    """Heights `H` spread into the `floor` pixels that have none, `steps` times: each
    step a pixel takes the height of its first neighbour (in _NEIGHBOURS' order) that
    had one before the step, and off the picture's edge counts as height 0."""
    import numpy as np
    out = H.copy()
    need = np.ascontiguousarray(floor & np.isnan(out))
    if int(steps) < 1 or not need.any():
        return out
    h, w = out.shape
    # the first step over the whole picture
    filled = np.nan_to_num(out, nan=-1e30)
    got = np.zeros(out.shape, bool)
    for dy, dx in _NEIGHBOURS:
        ys, yd = (slice(0, h - dy), slice(dy, h)) if dy >= 0 else (slice(-dy, h), slice(0, h + dy))
        xs, xd = (slice(0, w - dx), slice(dx, w)) if dx >= 0 else (slice(-dx, w), slice(0, w + dx))
        src = filled[ys, xs]
        sub = need[yd, xd]
        take = sub & (src > -1e29)
        out[yd, xd][take] = src[take]
        got[yd, xd] |= take
        sub &= ~take
        # the edge the shift leaves uncovered
        edge = np.ones(out.shape, bool)
        edge[yd, xd] = False
        take = need & edge
        out[take] = 0.0
        got |= take
        need &= ~take
    # each later step looks only round the pixels the step before filled: a pixel still
    # without a height had no neighbour with one before that step (or it would have
    # taken it then), and the edge's pixels all took theirs in the first
    flat_out = out.reshape(-1)
    flat_need = need.reshape(-1)
    front = np.flatnonzero(got)
    for _ in range(int(steps) - 1):
        cand, src = [], []
        fy, fx = front // w, front % w
        for dy, dx in _NEIGHBOURS:
            ny, nx = fy + dy, fx + dx
            inside = (ny >= 0) & (ny < h) & (nx >= 0) & (nx < w)
            pix = ny[inside] * w + nx[inside]
            keep = flat_need[pix]
            cand.append(pix[keep])
            src.append(front[inside][keep])
        front = np.concatenate(cand)
        if not len(front):
            break
        # each pixel from its first neighbour in _NEIGHBOURS' order
        front, first = np.unique(front, return_index=True)
        flat_out[front] = flat_out[np.concatenate(src)[first]]
        flat_need[front] = False
    return out


#: Walkable floor more than this far above a floor's main height (its area-weighted
#: median) is a roof walk, not drawn, in uu: Scenario_13's roof polygons at 2,900 uu
#: over its stairs, its floor 594-1,844 around a 1,500 uu main height.
ROOF_ABOVE = 1000.0
#: ... drawn after all where the source art draws at least ART_KEEP of one as floor and
#: under ROOF_UNDER of it lies over this floor's own polygons.
ROOF_ART = True
ROOF_UNDER = 0.1
#: Geometry only: on a level of one floor, a walkable polygon over ROOF_ABOVE is drawn when
#: under ROOF_UNDER of it lies over the floor's other polygons (no art to ask).
ROOF_GEO = False


def _main_height(polys):
    """Area-weighted median height of walkable polygons, in uu."""
    import numpy as np
    if not polys:
        return 0.0
    zs, ws = [], []
    for q in polys:
        x = np.array([v[0] for v in q])
        y = np.array([v[1] for v in q])
        ws.append(abs(float(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))) / 2.0)
        zs.append(sum(v[2] for v in q) / len(q))
    order = np.argsort(zs)
    cum = np.cumsum(np.array(ws)[order])
    k = int(np.searchsorted(cum, cum[-1] / 2.0)) if cum[-1] > 0 else len(zs) // 2
    return float(np.array(zs)[order][min(k, len(zs) - 1)])


def _surface(grid, at, z, above=SNAP):
    """Float per grid index `at`: the highest surface with headroom from SNAP under plane
    heights `z` to `above` over them, or nan."""
    import numpy as np
    out = np.full(len(at), np.nan, np.float32)
    lo = z - SNAP
    hi = z + above
    todo = np.arange(len(at))
    for _ in range(SNAP_TRIES):
        if not len(todo):
            break
        top = grid.highest(at[todo], lo[todo], hi[todo])
        found = ~np.isnan(top)
        todo, top = todo[found], top[found]
        clear = ~grid.between(at[todo], top + Z_CLEAR, top + SNAP_HEAD)
        out[todo[clear]] = top[clear]
        todo, top = todo[~clear], top[~clear]
        hi[todo] = top - Z_CLEAR
    return out


#: Headroom is looked for from this far above a surface, in uu (the solid's height step).
Z_CLEAR = 8.0


def _stair_ends(foot):
    """((x, y) of the bottom edge's middle, (x, y) of the top edge's middle) of a kit
    stair footprint."""
    import numpy as np
    cs = np.array(foot['corners'], float)[:, :2]
    d = np.array(foot['climb'], float)
    along = cs @ d
    mid = cs.mean(0)
    run = float(along.max() - along.min())
    return mid - d * run / 2.0, mid + d * run / 2.0


def _chained(foot, shaped):
    """True when another kit stair piece in `shaped` continues `foot`'s flight end to end
    (CHAIN_GAP, CHAIN_Z)."""
    import math
    lo_a, hi_a = _stair_ends(foot)
    for kind, other, _m in shaped:
        if kind != 'stairs' or other is foot:
            continue
        lo_b, hi_b = _stair_ends(other)
        if (math.hypot(*(hi_a - lo_b)) < CHAIN_GAP
                and abs(foot['z'][1] - other['z'][0]) < CHAIN_Z):
            return True
        if (math.hypot(*(lo_a - hi_b)) < CHAIN_GAP
                and abs(foot['z'][0] - other['z'][1]) < CHAIN_Z):
            return True
    return False


def _walls(idx, heights, grid, size, reach):
    """(walls, tested) bool [v][u]: off-floor pixels within `reach` of the floor where
    geometry stands in the cut above the nearest floor (`heights` [v][u] on the floor),
    and the pixels looked at."""
    import numpy as np
    none = np.zeros(idx.shape, bool)
    if grid is None:
        return none, none
    flo = idx > 0
    zs = _spread_height(np.where(flo, heights, np.nan).astype(np.float32),
                        np.ones(idx.shape, bool), reach)
    ys, xs = np.nonzero(~np.isnan(zs) & ~flo)
    tested = none.copy()
    tested[ys, xs] = True
    z = zs[ys, xs]
    hit = grid.between(_coarse(grid, size, ys, xs), z + WALL_LOW, z + WALL_CUT)
    walls = none.copy()
    walls[ys[hit], xs[hit]] = True
    return walls, tested


# -- the drawing --------------------------------------------------------------------

def layers(fl, volume, size):
    """The playable floor seen through `volume` at `size` pixels: {floor, z, stairs,
    across, holes, voids, uu} (bool / float arrays [v][u]; `across` is True where the
    stairs climb along u; `uu` world units per pixel), or raises SystemExit."""
    import math
    import numpy as np
    from riftlord.build.minimap import mapdata
    from PIL import Image, ImageDraw
    from riftlord.mapeditor import scratch
    project = mapdata.projector(volume)
    half = max(abs(volume['extent'][0]), abs(volume['extent'][1]))
    uu = 2.0 * half / size

    def px(p):
        u, v = project((p[0], p[1]))
        return (u * size, v * size)

    def box(lo, hi):
        return [px((lo[0], lo[1])), px((hi[0], lo[1])), px((hi[0], hi[1])),
                px((lo[0], hi[1]))]
    # each polygon's plane in pixels, z = a*u + b*v + c; index 0 is no floor
    planes = [(0.0, 0.0, 0.0)]
    idx_img = Image.new('I', (size, size), 0)
    pen = ImageDraw.Draw(idx_img)
    # where walkable floors overlap, the one nearest the floor's main height is drawn
    # (Scenario_13's roof walks at 2,900 uu over its stairs at 1,200-1,800)
    main = _main_height(fl['polys'])
    #: (lowest, highest) vertex of each label's polygon (0 and added floors: none)
    spans = [None]
    # walkable roofs and ledges far above the floor are not drawn
    polys = [q for q in fl['polys'] if sum(v[2] for v in q) / len(q) <= main + ROOF_ABOVE]
    high = [q for q in fl['polys'] if sum(v[2] for v in q) / len(q) > main + ROOF_ABOVE]
    one_floor = not any(math.isfinite(b) for b in (fl.get('band') or ()))
    art_hi = (_art_floor(fl.get('level_name'), fl.get('art_floor'))
              if high and ROOF_ART and one_floor and fl.get('level_name') else None)
    if art_hi is not None or (high and ROOF_GEO and one_floor):
        # ... on a level of one floor, unless the source art draws them as floor and no
        # floor of this one lies under them (Ice_01's upper room at 900 uu over a -218 main
        # height; Scenario_13's roof walks lie over its stairs; on Scenario_16's lower
        # floor its upper floor's polygons came in)
        under = Image.new('L', (size, size), 0)
        upen = ImageDraw.Draw(under)
        for q in polys:
            upen.polygon([px(v) for v in q], fill=255)
        under = np.asarray(under) > 0
        # geometry only (ROOF_GEO): no art, so every such polygon counts as drawn
        drawn = (_art_mask(art_hi, project, size) if art_hi is not None
                 else np.ones((size, size), bool))
        for q in high:
            pts = [px(v) for v in q]
            u0 = max(0, int(min(p[0] for p in pts)))
            v0 = max(0, int(min(p[1] for p in pts)))
            u1 = min(size, int(max(p[0] for p in pts)) + 2)
            v1 = min(size, int(max(p[1] for p in pts)) + 2)
            if u1 <= u0 or v1 <= v0:
                continue
            r = Image.new('L', (u1 - u0, v1 - v0), 0)
            ImageDraw.Draw(r).polygon([(x - u0, y - v0) for x, y in pts], fill=255)
            m = np.asarray(r) > 0
            if not m.any():
                continue
            if (drawn[v0:v1, u0:u1][m].mean() >= ART_KEEP
                    and under[v0:v1, u0:u1][m].mean() < ROOF_UNDER):
                polys.append(q)
    for q in sorted(polys, key=lambda q: -abs(sum(v[2] for v in q) / len(q) - main)):
        pts = [px(v) for v in q]
        m = np.array([[p[0], p[1], 1.0] for p in pts])
        z = np.array([v[2] for v in q], float)
        planes.append(tuple(np.linalg.lstsq(m, z, rcond=None)[0]))
        spans.append((float(z.min()), float(z.max())))
        pen.polygon(pts, fill=len(planes) - 1)
    idx = np.array(idx_img, dtype=np.int32)
    if fl['erase']:
        cut = np.zeros((size, size), dtype=bool)
        for lo, hi, keepers in fl['erase']:
            # drawn in an image the size of the footprint, then placed
            pts = box(lo, hi)
            u0 = max(0, int(min(p[0] for p in pts)) - 1)
            v0 = max(0, int(min(p[1] for p in pts)) - 1)
            u1 = min(size, int(max(p[0] for p in pts)) + 2)
            v1 = min(size, int(max(p[1] for p in pts)) + 2)
            if u1 <= u0 or v1 <= v0:
                continue
            hole = Image.new('L', (u1 - u0, v1 - v0), 0)
            hpen = ImageDraw.Draw(hole)
            hpen.polygon([(x - u0, y - v0) for x, y in pts], fill=255)
            for klo, khi in keepers:
                hpen.polygon([(x - u0, y - v0) for x, y in box(klo, khi)], fill=0)
            cut[v0:v1, u0:u1] |= np.asarray(hole) > 0
        idx[cut] = 0
    shapes = fl.get('rect_polys') or [None] * len(fl['rects'])
    for (lo, hi), q in zip(fl['rects'], shapes):
        planes.append((0.0, 0.0, float(hi[2])))
        r = Image.new('L', (size, size), 0)
        ImageDraw.Draw(r).polygon([px(v) for v in q] if q else box(lo, hi), fill=255)
        idx[np.asarray(r) > 0] = len(planes) - 1
    if fl.get('block'):
        r = Image.new('L', (size, size), 0)
        rpen = ImageDraw.Draw(r)
        for lo, hi in fl['block']:
            rpen.polygon(box(lo, hi), fill=255)
        idx[np.asarray(r) > 0] = 0
    # the graph keeps an agent radius off every wall; grow it back
    raw = idx > 0
    a, b, c = (np.array(col, dtype=np.float32) for col in zip(*planes))
    vv, uu_ = np.mgrid[0:size, 0:size].astype(np.float32)
    grid = _solid_grid(fl, project, size, uu)
    #: the floor's own height per pixel where the solid gives it (nan elsewhere): a graph
    #: polygon's plane spans flat floor and whole flights (Scenario_13's top stairs read
    #: 1,077 uu where the treads are at 1,200-1,500)
    H = np.full(idx.shape, np.nan, np.float32)
    #: this floor's own art floor, grown CROSS_ART_SLACK, on a level of several floors
    art_drawn = None
    #: (area px, height) of the rooms seeded from the solid where the art draws floor
    islands = []
    #: per plane label, True where the plane is steeper than STAIR_GRADE (none without a solid)
    sloped = None
    if grid is None:
        idx = _spread(idx, round(scratch.AGENT_RADIUS / uu))
    else:
        ys, xs = np.nonzero(raw)
        zp = a[idx[ys, xs]] * xs + b[idx[ys, xs]] * ys + c[idx[ys, xs]]
        # the graph's flat planes are only a guide to the surface (they span whole
        # flights); its sloped ones follow a flight and are kept; a scratch level's
        # floors are kit floors at their own height
        sloped = np.hypot(a, b) / uu > STAIR_GRADE
        if fl['polys']:
            got = _surface(grid, _coarse(grid, size, ys, xs), zp)
            keep = sloped[idx[ys, xs]]
            got[keep] = zp[keep]
            H[ys, xs] = got
        else:
            H[ys, xs] = zp
        idx, H = _grow_floor(idx, H, (a, b, c), grid, size,
                             round(AGENT_GROW * scratch.AGENT_RADIUS / uu),
                             round(GROW_SHARE * GROW_MAX / uu), sloped)
        art = _art_floor(fl.get('level_name'), fl.get('art_floor')) if fl['polys'] else None
        if art is None and GEO_GROW > 0 and fl['polys']:
            # geometry only: floor the walkable graph lacks (player-only galleries:
            # Scenario_11's third floor) grows on over the solid, up to GEO_GROW, at this
            # floor's heights (its band, else ROOF_ABOVE either side of its main height)
            band_ = fl.get('band') or (-math.inf, math.inf)
            zband = ((band_[0] - FLOOR_PAWN, band_[1] - FLOOR_PAWN)
                     if any(math.isfinite(v) for v in band_)
                     else (main - ROOF_ABOVE, main + ROOF_ABOVE))
            idx, H = _grow_floor(idx, H, (a, b, c), grid, size, 0, round(GEO_GROW / uu),
                                 sloped, zband=zband)
        if art is not None:
            # floor the walkable graph lacks (player-only galleries: Scenario_11's third
            # floor) grows on over the solid where the source art draws floor
            allow = _art_mask(art, project, size)
            idx, H = _grow_floor(idx, H, (a, b, c), grid, size, 0, round(GROW_ART / uu),
                                 sloped, allow=allow)
            # rooms of the art's floor the graph does not reach at all (Scenario_11's side
            # rooms off its middle floor): seeded where the solid holds a surface near the
            # floor's height under most of the room, then grown over it
            seeded = False
            for area, pix in _components(allow & (idx == 0)):
                if area * uu * uu < ART_KEEP_AREA:
                    continue
                pix = np.array(pix)
                # a sample first: most such rooms fail (approaches, rooms of other heights)
                few = pix[::max(1, len(pix) // ISLAND_SAMPLE)]
                got = _surface(grid, _coarse(grid, size, few // size, few % size),
                               np.full(len(few), main, np.float32), ISLAND_ABOVE)
                if (~np.isnan(got)).mean() < ISLAND_HELD:
                    continue
                ys_i, xs_i = pix // size, pix % size
                got = _surface(grid, _coarse(grid, size, ys_i, xs_i),
                               np.full(len(pix), main, np.float32), ISLAND_ABOVE)
                held = ~np.isnan(got)
                if held.mean() < ISLAND_HELD:
                    continue
                zi = float(np.median(got[held]))
                on = held & (np.abs(got - zi) <= LEDGE_STEP)
                planes.append((0.0, 0.0, zi))
                idx.ravel()[pix[on]] = len(planes) - 1
                H.ravel()[pix[on]] = got[on]
                islands.append((int(area), round(zi)))
                seeded = True
            if seeded:
                a, b, c = (np.array(col, dtype=np.float32) for col in zip(*planes))
                sloped = np.hypot(a, b) / uu > STAIR_GRADE
                spans.extend([None] * (len(planes) - len(spans)))
                idx, H = _grow_floor(idx, H, (a, b, c), grid, size, 0,
                                     round(GROW_ART / uu), sloped, allow=allow)
        if fl.get('band') and np.isfinite(fl['band']).any():
            # a flight between two floors is drawn on each up to the floors' boundary,
            # cut straight across as the shipped art cuts it (Scenario_16's corner
            # flights), not to the walkable graph's diagonal polygon edge
            lo_b, hi_b = fl['band']
            zz = a[idx] * uu_ + b[idx] * vv + c[idx]
            # only a polygon that runs across the floors' boundary is cut at it (one the
            # source art puts wholly on this floor stays: Scenario_15's)
            crosses = np.zeros(len(planes), bool)
            for k, sp in enumerate(spans):
                if sp is not None and ((sp[0] < lo_b < sp[1]) or (sp[0] < hi_b < sp[1])):
                    crosses[k] = True
            off = (idx > 0) & sloped[idx] & crosses[idx] & ((zz < lo_b) | (zz >= hi_b))
            # ... except where the source level's own art for this floor draws it
            # (Scenario_15's tower flights run through its thin top band)
            art = _art_floor(fl.get('level_name'), fl.get('art_floor'))
            if art is not None:
                # a sloped polygon only where that art draws floor: the foot of a slope up
                # to the next floor is that floor's (Scenario_14's wedge in the wall
                # between its second and third corridors, the top of its flights up to the
                # middle room)
                art_drawn = _grow(_art_mask(art, project, size),
                                  max(1, int(round(CROSS_ART_SLACK * size))), True)
                off |= (idx > 0) & sloped[idx] & ~art_drawn
            if art is not None and off.any():
                dark, aproj, an = art
                ys_o, xs_o = np.nonzero(off)
                inv = _unproject(project, size)
                wx, wy = inv(xs_o + 0.5, ys_o + 0.5)
                au, av = aproj((wx, wy))
                iu = np.clip((np.asarray(au) * an).astype(int), 0, an - 1)
                iv = np.clip((np.asarray(av) * an).astype(int), 0, an - 1)
                drawn = dark[iv, iu]
                off[ys_o[drawn], xs_o[drawn]] = False
            idx[off] = 0
            H[off] = np.nan

    def heights():
        """Floor heights: the solid's where known, else the graph planes'."""
        z = a[idx] * uu_ + b[idx] * vv + c[idx]
        known = ~np.isnan(H) & (idx > 0)
        z[known] = H[known]
        return z

    def rect(lo, hi, pad=0):
        """Pixel (rows, cols) slices of a world box's footprint, or None off the image."""
        corners = [px((x, y)) for x in (lo[0], hi[0]) for y in (lo[1], hi[1])]
        u0 = max(0, int(round(min(p[0] for p in corners))) - pad)
        u1 = min(size, int(round(max(p[0] for p in corners))) + pad)
        v0 = max(0, int(round(min(p[1] for p in corners))) - pad)
        v1 = min(size, int(round(max(p[1] for p in corners))) + pad)
        return (slice(v0, v1), slice(u0, u1)) if u1 > u0 and v1 > v0 else None
    # pillars: pieces standing on the floor that the graph walks round, at their own size
    flo0 = idx > 0
    z0 = heights()
    ring = int(round(scratch.AGENT_RADIUS / uu)) + 2
    solid = np.zeros_like(flo0)
    used = {'pillars': [], 'stairs': [], 'islands': islands}
    for lo, hi, mesh, kind, _tilt in fl.get('pieces') or []:
        if kind not in PILLAR_CATEGORIES or grid is not None:
            continue
        r, around = rect(lo, hi), rect(lo, hi, ring)
        if r is None or not flo0[around].any():
            continue
        fz = float(np.median(z0[around][flo0[around]]))
        if not (lo[2] < fz + scratch.OBSTACLE_BELOW and hi[2] > fz + scratch.OBSTACLE_ABOVE):
            continue
        if 1.0 - raw[r].mean() >= PILLAR_BLOCKED:
            solid[r] = True
            used['pillars'].append((mesh, lo, hi))
    idx[solid] = 0
    # standing geometry off the floor: walls, railings, pillars (none without a solid)
    g = max(1, int(round(DROP_GAP / 2.0 / uu)))
    walls, tested = _walls(idx, heights(), grid, size, g)
    # drops: a narrow gap between floors of different heights is closed
    flo1 = idx > 0
    gap = _shrink(_grow(flo1, g, True), g, True) & ~flo1 & ~solid & ~walls
    if gap.any():
        # the lowest and highest floor next to each pixel
        z1 = heights()
        lo_n = np.full(z1.shape, np.inf, dtype=np.float32)
        hi_n = np.full(z1.shape, -np.inf, dtype=np.float32)
        for dy, dx in _NEIGHBOURS:
            f = _shifted(flo1, dy, dx)
            zz = _shifted(z1, dy, dx)
            lo_n = np.where(f, np.minimum(lo_n, zz), lo_n)
            hi_n = np.where(f, np.maximum(hi_n, zz), hi_n)
        fill = _spread(idx, g, within=gap)
        drop = np.zeros_like(gap)
        for _area, pix in _components(gap):
            pix = np.array(pix)
            zlo, zhi = float(lo_n.ravel()[pix].min()), float(hi_n.ravel()[pix].max())
            if zhi - zlo <= LEDGE_STEP:
                continue
            if grid is not None:
                # an open shaft between floors is not a drop: a drop's gap has a
                # surface at the floors' heights (Scenario_14's stacked corridors)
                ys, xs = np.divmod(pix, size)
                held = grid.between(_coarse(grid, size, ys, xs),
                                    np.full(len(pix), zlo - SUPPORT_DOWN),
                                    np.full(len(pix), zhi + WALL_LOW))
                if held.mean() < DROP_HELD:
                    continue
            drop.ravel()[pix] = True
        idx[drop] = fill[drop]
    # a wall across every door, then only the floor the rifts reach
    band = max(2, int(round(BAND * size)))
    if fl.get('doors'):
        cut = Image.new('L', (size, size), 0)
        cpen = ImageDraw.Draw(cut)
        rifts = fl.get('rifts') or []
        for x, y, yaw, width, _z in fl['doors']:
            h2 = width / 2.0 + CUT_OVERLAP
            dx, dy = math.cos(math.radians(yaw)) * h2, math.sin(math.radians(yaw)) * h2
            if DOOR_CUT_OUTSIDE and rifts:
                # the cut from DOOR_CUT_INSET inside the door line outward, away from the
                # nearest rift
                rx, ry = min(rifts, key=lambda r: math.hypot(r[0] - x, r[1] - y))[:2]
                nx, ny = -math.sin(math.radians(yaw)), math.cos(math.radians(yaw))
                if (rx - x) * nx + (ry - y) * ny > 0:
                    nx, ny = -nx, -ny
                off = CUT_WIDTH / 2.0 - DOOR_CUT_INSET
                x, y = x + nx * off, y + ny * off
            cpen.line([px((x - dx, y - dy)), px((x + dx, y + dy))], fill=255,
                      width=max(2, int(round(CUT_WIDTH / uu))))
        idx[np.asarray(cut) > 0] = 0
    bound = _door_bounds(fl.get('doors') or [], fl.get('rifts') or [])
    if bound is not None:
        line = np.arange(size, dtype=np.float32) + 0.5
        inv = _unproject(project, size)
        wx, wy = inv(line[None, :], line[:, None])
        idx[(wx < bound[0]) | (wx > bound[2]) | (wy < bound[1]) | (wy > bound[3])] = 0
        del wx, wy
    region = _art_region(fl.get('level_name'), fl.get('art_floor'), fl.get('art_region'))
    if region is not None:
        # the region the source level's own minimap draws (its plate, grown a little):
        # which parts of a level the artists mapped is read off their art
        mask, rproj, rn = region
        line = np.arange(size, dtype=np.float32) + 0.5
        inv = _unproject(project, size)
        wx, wy = inv(line[None, :], line[:, None])
        ru, rv = rproj((wx, wy))
        iu = (np.asarray(ru) * rn).astype(np.int64)
        iv = (np.asarray(rv) * rn).astype(np.int64)
        inside = (iu >= 0) & (iu < rn) & (iv >= 0) & (iv < rn)
        keep = np.zeros(idx.shape, bool)
        keep[inside] = mask[iv[inside], iu[inside]]
        idx[~keep] = 0
        del wx, wy, ru, rv, iu, iv
    narrow = max(1, int(round(PASSAGE / 2.0 / uu)))
    wide = _shrink(idx > 0, narrow, True)
    reach = int(round(ANCHOR_REACH / uu)) + band + narrow
    starts = np.zeros_like(wide)
    groups = ((fl['seeds'],) if 'seeds' in fl
              else (fl.get('rifts') or [], fl.get('anchors') or []))
    for seeds in groups:
        for p in seeds:
            u, v = px(p)
            at = _floor_near(wide, int(u), int(v), reach)
            if at is not None:
                starts[at[1], at[0]] = True
        if starts.any():
            break
    if fl.get('keep_all') or ('seeds' in fl and not starts.any()):
        # a floor no rift or route point stands on: all of its played floor
        starts = wide.copy()
    kept = _reach(wide, starts)
    if not kept.any():
        raise SystemExit('no floor found at the rift')
    extra = None
    art = (_art_floor(fl.get('level_name'), fl.get('art_floor'))
           if fl['polys'] and not fl.get('doors') else None)
    if art is not None:
        # floor no rift or route reaches is kept where the source art draws it on this
        # floor (Scenario_14's upper room, reached only by a flight from the floor below)
        allow = _grow(_art_mask(art, project, size), narrow + 1, True)
        extra = np.zeros_like(wide)
        least = ART_KEEP_AREA / uu ** 2
        kept_back = None
        for area, pix in _components(wide & ~kept):
            share = allow.ravel()[pix].mean()
            if share < ART_KEEP:
                continue
            if area < least and not ART_KEEP_FULL:
                continue
            if area < least:
                # a flat room's area before the PASSAGE shrink (Sand_04's platform: 6,600
                # px at 1024, 1,200 px after it); not a piece of a flight (a sliver of
                # Scenario_16's corner flight took the flight away)
                pa = np.array(pix)
                if sloped is not None and sloped[idx.ravel()[pa]].mean() >= 0.5:
                    continue
                ys_k, xs_k = pa // size, pa % size
                pad = narrow + 2
                v0_, v1_ = max(0, ys_k.min() - pad), min(size, ys_k.max() + pad + 1)
                u0_, u1_ = max(0, xs_k.min() - pad), min(size, xs_k.max() + pad + 1)
                m = np.zeros((v1_ - v0_, u1_ - u0_), bool)
                m[ys_k - v0_, xs_k - u0_] = True
                if kept_back is None:
                    kept_back = _grow(kept, narrow + 1, True)
                # ... not counting the reached floor beside it
                full = int((_grow(m, narrow + 1, True) & (idx[v0_:v1_, u0_:u1_] > 0)
                            & ~kept_back[v0_:v1_, u0_:u1_]).sum())
                if full < least:
                    continue
            extra.ravel()[pix] = True
            pa = np.array(pix)
            used.setdefault('art_kept', []).append(
                (area, round(float(share), 2), int((pa % size).min()), int((pa // size).min())))
        extra &= allow
    # back out to the walls, then only what joins the reached floor
    kept = _reach(_grow(kept, narrow + 1, True) & (idx > 0), starts)
    if extra is not None and extra.any():
        kept |= _grow(extra, narrow + 1, True) & (idx > 0) & allow
    idx[~kept] = 0
    # smooth jagged edges; the pixels filled take their neighbour's plane
    s = max(1, int(round(SMOOTH / uu)))
    closed = _shrink(_grow(idx > 0, s, True), s, True) & ~walls
    idx = _spread(idx, s, within=closed)
    idx[~closed] = 0
    # holes: props become floor; a hole over other walkable floor is a void, the rest solid
    flo = idx > 0
    holes = ~flo & ~_outside(flo)
    voids = np.zeros_like(flo)
    props = np.zeros_like(flo)
    below = cover = zc = lower = None
    pillar_fix = []
    #: holes under a liquid plane, squared to the planes below
    liquid_holes = []
    liquids = _cover_rects(fl.get('planes') or [], project, size)
    plants = _boxes_mask(fl.get('flora'), project, size) if fl.get('flora') else None
    plant_reach = 0
    stones = None
    if fl.get('rocks'):
        img = Image.new('L', (size, size), 0)
        rpen = ImageDraw.Draw(img)
        for q, _z0, _z1 in fl['rocks']:
            rpen.polygon([px(v) for v in q], fill=255)
        stones = np.asarray(img) > 0
    for area, pix in _components(holes):
        if area * uu * uu < PROP_AREA:
            props.ravel()[pix] = True
            continue
        if stones is not None and stones.ravel()[pix].mean() >= ROCK_SHARE:
            # a rock standing in the floor: one solid light area over it, not a ring
            continue
        pix_a = np.array(pix)
        if liquids is not None and _covered(pix_a, flo, None, liquids, above=False) \
                >= SOLID_COVER:
            voids.ravel()[pix_a] = True
            liquid_holes.append(pix_a)
            continue
        if grid is not None:
            # open over or under walkable floor well off this one's height (Scenario_16's
            # upper courtyards over the lower floor, its lower hall under the upper
            # room): a void the other floor's ghost shows through
            if lower is None:
                img = Image.new('L', (size, size), 0)
                bpen = ImageDraw.Draw(img)
                for q in fl.get('every') or []:
                    if abs(sum(v[2] for v in q) / len(q) - main) > LEDGE_DRAWN:
                        bpen.polygon([px(v) for v in q], fill=255)
                lower = np.asarray(img) > 0
            if lower.ravel()[pix_a].mean() >= VOID_WALKABLE:
                voids.ravel()[pix_a] = True
                continue
            # standing geometry where the hole meets the floor: a pillar, a mass
            seen = tested.ravel()[pix_a]
            if not seen.any() or walls.ravel()[pix_a][seen].mean() < SOLID_COVER:
                if plants is not None and plants.ravel()[pix_a].mean() >= FLORA_SHARE:
                    # nothing stands there but plants, which are never drawn: floor
                    props.ravel()[pix_a] = True
                    plant_reach = max(plant_reach, int(math.sqrt(area)) + 2)
                    continue
                voids.ravel()[pix_a] = True
                continue
            # a pillar at its pieces' own footprint, so a row of columns comes out even
            # (Other_02, Scenario_16), not as the floor's ragged edge round them
            if area * uu * uu <= PILLAR_MAX_AREA:
                if cover is None:
                    cover = _cover_rects(fl.get('cover') or [], project, size)
                    zc = heights()
                squared = _pillar_footprint(pix_a, size, cover, zc, flo)
                if squared is not None:
                    pillar_fix.append((pix_a, squared))
            continue
        if below is None:
            img = Image.new('L', (size, size), 0)
            bpen = ImageDraw.Draw(img)
            for q in fl.get('every') or []:
                bpen.polygon([px(v) for v in q], fill=255)
            below = _grow(np.asarray(img) > 0, round(scratch.AGENT_RADIUS / uu), True)
        if below.ravel()[pix].mean() >= VOID_WALKABLE:
            voids.ravel()[pix] = True
            continue
        if cover is None:
            cover = _cover_rects(fl.get('cover') or [], project, size)
            zc = heights()
        if _covered(np.array(pix), flo, zc, cover) < SOLID_COVER:
            if plants is not None and plants.ravel()[pix].mean() >= FLORA_SHARE:
                props.ravel()[pix] = True
                plant_reach = max(plant_reach, int(math.sqrt(area)) + 2)
                continue
            voids.ravel()[pix] = True
    # a lava or water opening at its liquid plane's own square footprint, not the
    # graph's ragged edge round it (Front Lawn's lava pits); walkable floor over it (a
    # bridge) stays floor
    if liquid_holes and liquids is not None:
        lu0, lu1, lv0, lv1, _lz0, _lz1 = liquids
        for pix_a in liquid_holes:
            rows, cols = pix_a // size, pix_a % size
            near = ((lu1 > cols.min()) & (lu0 < cols.max() + 1) & (lv1 > rows.min())
                    & (lv0 < rows.max() + 1))
            for k in np.flatnonzero(near):
                a0, a1 = max(0, int(round(lu0[k]))), min(size, int(round(lu1[k])))
                b0, b1 = max(0, int(round(lv0[k]))), min(size, int(round(lv1[k])))
                if a1 <= a0 or b1 <= b0:
                    continue
                # a plane the size of the opening, not a lake under the level
                if (a1 - a0) * (b1 - b0) > LIQUID_FIT * len(pix_a):
                    continue
                win = (slice(b0, b1), slice(a0, a1))
                sq = ~raw[win]
                voids[win] |= sq
                holes[win] |= sq
                idx[win][sq] = 0
    # an opening that is nearly a rectangle is drawn as one (Front Lawn's lava pits,
    # cut by kit floors on the 300 uu lattice; the graph leaves their edges ragged)
    for area, pix in _components(voids):
        if area * uu * uu < PROP_AREA:
            continue
        pix_v = np.array(pix)
        rows, cols = pix_v // size, pix_v % size
        b0, b1, a0, a1 = rows.min(), rows.max() + 1, cols.min(), cols.max() + 1
        if area < VOID_SQUARE * (b1 - b0) * (a1 - a0):
            continue
        win = (slice(b0, b1), slice(a0, a1))
        sq = ~raw[win]
        voids[win] |= sq
        holes[win] |= sq
        idx[win][sq] = 0
    for pix_a, (win, squared) in pillar_fix:
        holes.ravel()[pix_a] = False
        holes[win] |= squared
        idx[win][squared] = 0
    if props.any():
        idx = _spread(idx, max(1, int(round(math.sqrt(PROP_AREA) / uu)), plant_reach),
                      within=props)
    # raised platforms standing in a hole or on the floor: floor at their top, so a
    # ledge line outlines them
    shaped = fl.get('shaped') or []
    platform = np.zeros(idx.shape, bool)
    flights = None
    for kind, foot, _m in shaped:
        if kind != 'platform':
            continue
        r = Image.new('L', (size, size), 0)
        ImageDraw.Draw(r).polygon([px(c) for c in foot['corners']], fill=255)
        m = (np.asarray(r) > 0) & (holes | (idx > 0))
        if not m.any():
            continue
        # only a platform standing in a flight (Scenario_13's); a dais on a floor is not
        # outlined (Scenario_04's draws nothing)
        if flights is None:
            fr = Image.new('L', (size, size), 0)
            fpen = ImageDraw.Draw(fr)
            for k2, f2, _m2 in shaped:
                if k2 == 'stairs':
                    fpen.polygon([px(c) for c in f2['corners']], fill=255)
            flights = _grow(np.asarray(fr) > 0, 2, True)
        if not (m & flights).any():
            continue
        # only a platform standing on this floor: its top a little above the floor round it
        top = float(foot['z'][1])
        ring = _grow(m, 3, True) & ~m & (idx > 0)
        if ring.any():
            zr = heights()[ring]
            near = float(np.median(zr))
        else:
            near = None
        if near is None or not (LEDGE_STEP / 2.0 < top - near <= PLATFORM_RISE):
            continue
        planes.append((0.0, 0.0, top))
        a, b, c = (np.array(col, dtype=np.float32) for col in zip(*planes))
        idx[m] = len(planes) - 1
        H[m] = top
        platform |= m
    flo = idx > 0
    holes &= ~flo
    if grid is not None:
        # floor filled after the growth (drops, smoothing, props) takes its neighbour's
        H = _spread_height(H, flo, int(round(PROP_AREA ** 0.5 / uu)) + g)
    z = heights()
    grade = np.hypot(a, b) / uu
    slope = flo & (grade[idx] > STAIR_GRADE)
    # stairs: pieces mostly covered by sloped floor, at their own footprint; sloped
    # floor no such piece covers keeps the graph's shape
    stairs = np.zeros_like(flo)
    #: climb axis of named kit stairs: 1 along u, 2 along v, 0 unknown
    climb_axis = np.zeros(flo.shape, np.int8)
    #: footprints of kit stair pieces too low to hatch: never hatched from the graph's slopes
    plain = np.zeros_like(flo)
    def lay_flight(full, add):
        """Floor over `add` (part of the flight `full`), from the floor beside it."""
        nonlocal z
        ys, xs = np.nonzero(full)
        y0, y1 = max(0, ys.min() - 2), min(size, ys.max() + 3)
        x0, x1 = max(0, xs.min() - 2), min(size, xs.max() + 3)
        win = (slice(y0, y1), slice(x0, x1))
        steps = max(y1 - y0, x1 - x0)
        sub = _spread(idx[win], steps, within=add[win] | flo[win])
        sub[~(add[win] | flo[win])] = 0
        idx[win] = sub
        H[win] = _spread_height(H[win], add[win] & (sub > 0), steps)
        newly = add & (idx > 0)
        flo[newly] = True
        holes[newly] = False
        z = heights()
        return newly

    def climb_of(foot):
        cx, cy = foot['climb']
        p0, p1 = px((0.0, 0.0)), px((cx, cy))
        return 1 if abs(p1[0] - p0[0]) >= abs(p1[1] - p0[1]) else 2
    #: flights left off this floor: (footprint, pixels), for the chained pass below
    left_off = []
    for kind, foot, _m in shaped:
        if kind != 'stairs':
            continue
        r = Image.new('L', (size, size), 0)
        ImageDraw.Draw(r).polygon([px(c) for c in foot['corners']], fill=255)
        full = (np.asarray(r) > 0) & ~platform
        m = full & flo
        z0, z1 = foot['z']
        if z1 - z0 < STEP_RISE or (z1 - z0 < STAIR_RISE and not _chained(foot, shaped)):
            if m.any():
                plain |= full
            continue
        on = m & (z >= z0 - SNAP) & (z <= z1 + SNAP)
        if not on.any():
            if full.any():
                left_off.append((foot, full))
            continue
        if on.sum() >= STAIR_FILL * full.sum():
            # the whole flight at its kit footprint: the walkable graph cuts corner
            # flights on the diagonal (Scenario_16's), the kit piece is square
            add = full & ~flo
            if art_drawn is not None:
                # on a level of several floors, only the part this floor's art draws
                add &= art_drawn
            if add.any():
                on |= lay_flight(full, add)
        else:
            left_off.append((foot, full))
        stairs |= on
        climb_axis[on] = climb_of(foot)
        used['stairs'].append(('named', foot['z']))
    if art_drawn is not None:
        # on a level of several floors, a flight left off this floor beside its floor is
        # drawn where this floor's art draws it (Scenario_14's flight down from its upper
        # room: the lower piece's floor belongs to the floor below)
        for foot, full in left_off:
            if not (_grow(full, 2, True) & flo).any():
                continue
            if art_drawn[full].mean() < CHAIN_ART:
                continue
            add = full & art_drawn & ~flo
            on = (lay_flight(full, add) if add.any() else np.zeros_like(flo)) | \
                (full & art_drawn & flo)
            if on.any():
                stairs |= on
                climb_axis[on] = climb_of(foot)
                used['stairs'].append(('chained', foot['z']))
    if slope.any():
        for lo, hi, mesh, _kind, tilt in fl.get('pieces') or []:
            # a flat piece under a sloped graph polygon is a floor the polygon also
            # spans (Scenario_13's landing above its stairs); tilted floor pieces laid
            # as a flight are stairs (Scenario_16's)
            if not tilt or not any(w in (mesh or '').lower() for w in TILTED_FLIGHT_WORDS):
                continue
            r = rect(lo, hi)
            if r is None or slope[r].mean() < STAIR_COVER:
                continue
            zs = z[r][slope[r]]
            if lo[2] <= float(zs.max()) + LEDGE_STEP and hi[2] >= float(zs.min()) - LEDGE_STEP:
                stairs[r] |= flo[r] & ~platform[r] & ~plain[r]
                used['stairs'].append((mesh, lo, hi))
        if not stairs.any():
            # no kit or tilted stairs on this floor: the graph's own slopes
            for area, pix in _components(slope & ~plain):
                if area * uu * uu >= STAIR_AREA:
                    stairs.ravel()[pix] = True
        else:
            # beside kit flights, the graph's own slopes where the source art hatches
            # them (Scenario_Other_02's upper side flights have no stair piece)
            hatch = _art_hatch(fl.get('level_name'), fl.get('art_floor'))
            if hatch is not None:
                drawn = _art_mask(hatch, project, size)
                for area, pix in _components(slope & ~plain & ~stairs):
                    if (area * uu * uu >= STAIR_AREA
                            and drawn.ravel()[pix].mean() >= ART_HATCH_SHARE):
                        stairs.ravel()[pix] = True
                        used['stairs'].append(('art', area))
            elif SLOPE_HATCH_GEO:
                # geometry only: every such slope of a flight's size is hatched
                for area, pix in _components(slope & ~plain & ~stairs):
                    if area * uu * uu >= STAIR_AREA:
                        stairs.ravel()[pix] = True
                        used['stairs'].append(('slope', area))
    tilt_drawn = art_drawn
    if tilt_drawn is None and TILTED_BESIDE_GEO and fl.get('band') and \
            np.isfinite(fl['band']).any():
        # geometry only: all of them, no art to ask
        tilt_drawn = np.ones_like(flo)
    if tilt_drawn is not None:
        # on a level of several floors, tilted floor pieces laid as a flight beside this
        # floor are drawn where this floor's art draws them, piece by piece from the floor
        # (Scenario_16's corner flights, whose floor belongs to the floor above)
        tilted = []
        for lo, hi, mesh, _kind, tilt in fl.get('pieces') or []:
            if not tilt or not any(w in (mesh or '').lower() for w in TILTED_FLIGHT_WORDS):
                continue
            r = rect(lo, hi)
            if r is not None and not stairs[r].any():
                tilted.append((lo, hi, r))
        laid = []
        grown = True
        while grown and tilted:
            grown = False
            for item in list(tilted):
                lo, hi, r = item
                full = np.zeros_like(flo)
                full[r] = True
                full &= ~platform
                if not full.any() or tilt_drawn[full].mean() < CHAIN_ART:
                    tilted.remove(item)
                    continue
                if not (_grow(full, 2, True) & (flo | stairs)).any():
                    continue
                add = full & tilt_drawn & ~flo
                on = (lay_flight(full, add) if add.any() else np.zeros_like(flo)) | \
                    (full & tilt_drawn & flo)
                tilted.remove(item)
                if on.any():
                    stairs |= on
                    laid.append((lo, hi, on))
                    grown = True
        # each flight climbs from its lowest piece's centre to its highest's; a flight of
        # one piece across that piece's short side
        whole = np.zeros_like(flo)
        for _lo, _hi, on in laid:
            whole |= on
        for _area, pix in _components(whole):
            mine = [t for t in laid if t[2].ravel()[pix].any()]
            ends = sorted(mine, key=lambda t: t[0][2])
            (lo_a, hi_a, _o), (lo_b, hi_b, _o2) = ends[0], ends[-1]
            pa = px(((lo_a[0] + hi_a[0]) / 2.0, (lo_a[1] + hi_a[1]) / 2.0))
            pb = px(((lo_b[0] + hi_b[0]) / 2.0, (lo_b[1] + hi_b[1]) / 2.0))
            du, dv = pb[0] - pa[0], pb[1] - pa[1]
            if len(mine) < 2:
                q = box(lo_a, hi_a)
                du = max(p[1] for p in q) - min(p[1] for p in q)
                dv = max(p[0] for p in q) - min(p[0] for p in q)
            climb_axis.ravel()[pix] = 1 if abs(du) >= abs(dv) else 2
            used['stairs'].append(('tilted beside', len(mine)))
    # a flight inside the source art's own plate that the art leaves unhatched is plain
    # floor (Scenario_15's tilted-floor ramps, Ice_03's side flights), unless the plan
    # moved or added a piece over it
    if stairs.any() and fl['polys']:
        hatch = _art_hatch(fl.get('level_name'), fl.get('art_floor'))
        region = (_art_region_cached(fl['level_name'], fl.get('art_floor') or 0)
                  if fl.get('level_name') else None)
        if hatch is not None and region is not None:
            drawn = _art_mask(hatch, project, size)
            inside = _art_mask(region, project, size)
            img =Image.new('L', (size, size), 0)
            epen = ImageDraw.Draw(img)
            for kind, foot, _m in shaped:
                if kind == 'stairs' and foot.get('moved'):
                    epen.polygon([px(c) for c in foot['corners']], fill=255)
            for lo, hi in list(fl['rects']) + list(fl.get('block') or []):
                epen.polygon(box(lo, hi), fill=255)
            edited = np.asarray(img) > 0
            for area, pix in _components(stairs):
                pix = np.array(pix)
                if edited.ravel()[pix].any():
                    continue
                share = float(drawn.ravel()[pix].mean())
                if inside.ravel()[pix].mean() >= ART_PLAIN_INSIDE and share < ART_PLAIN_HATCH:
                    stairs.ravel()[pix] = False
                    used['stairs'].append(('plain', int(area), round(share, 2)))
    # each flight hatched one way: across its mean climb
    across = np.zeros_like(flo)
    for area, pix in _components(stairs):
        if area * uu * uu < STAIR_AREA:
            stairs.ravel()[pix] = False
            continue
        pix = np.array(pix)
        named = climb_axis.ravel()[pix]
        if (named > 0).any():
            most = (named == 1).sum() >= (named == 2).sum()
            if FLIGHT_OWN_CLIMB:
                # each named flight across its own climb, the rest of the component as most
                # of it (Ice_03's side flights climb across its main flight)
                across.ravel()[pix] = np.where(named > 0, named == 1, most)
            else:
                across.ravel()[pix] = most
            continue
        climb = slope.ravel()[pix]
        sel = idx.ravel()[pix[climb]] if climb.any() else idx.ravel()[pix]
        across.ravel()[pix] = abs(float(a[sel].mean())) >= abs(float(b[sel].mean()))
    # the rift's platform drawn solid where the source art draws it so (Scenario_02, 05,
    # 07; the other levels draw it as floor)
    light = _art_light(fl.get('level_name'), fl.get('art_floor'))
    if light is not None:
        lmask, lproj, ln = light
        for lo, hi, mesh, kind, _tilt in fl.get('pieces') or []:
            if kind != 'rift':
                continue
            cx, cy = (lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0
            lu, lv = lproj((cx, cy))
            iu, iv = int(lu * ln), int(lv * ln)
            if not (0 <= iu < ln and 0 <= iv < ln and lmask[iv, iu]):
                continue
            r = rect(lo, hi)
            if r is None:
                continue
            plat = np.zeros_like(flo)
            plat[r] = True
            plat &= flo | holes
            flo &= ~plat
            holes |= plat
            stairs &= ~plat
    # an opening the source art leaves open up to a thin line (Front Lawn's lava pits)
    # keeps only the white the art draws round it, not the whole band; openings the plan
    # changed keep the band
    void_clear = None
    opens = (_art_open_cached(fl['level_name'], fl.get('art_floor') or 0)
             if fl.get('level_name') and fl['polys'] and voids.any() else None)
    if opens is not None:
        art_open = _art_mask(opens, project, size)
        img = Image.new('L', (size, size), 0)
        epen = ImageDraw.Draw(img)
        for lo, hi, _keep in fl['erase']:
            epen.polygon(box(lo, hi), fill=255)
        for lo, hi in (list(fl['rects']) + list(fl.get('block') or [])
                       + list(fl.get('knocked') or [])):
            epen.polygon(box(lo, hi), fill=255)
        edited = np.asarray(img) > 0
        light = _art_light(fl.get('level_name'), fl.get('art_floor'))
        art_solid = _art_mask(light, project, size) if light is not None else None
        void_clear = np.zeros_like(voids)
        for area, pix in _components(voids & ~flo):
            pix = np.array(pix)
            if edited.ravel()[pix].any():
                continue
            if art_solid is not None and art_solid.ravel()[pix].mean() >= OPEN_SHARE:
                # an opening the art draws solid white (Ice_02's courtyard) is a mass
                voids.ravel()[pix] = False
                holes.ravel()[pix] = True
                continue
            if art_open.ravel()[pix].mean() < OPEN_SHARE:
                continue
            void_clear.ravel()[pix] = art_open.ravel()[pix]
    band_img = None
    if fl.get('flyer_legs'):
        band_img = Image.new('L', (size, size), 0)
        bpen = ImageDraw.Draw(band_img)
        wide = max(1, int(round(FLYER_BAND / uu)))
        ends = {p1 for _p0, p1 in fl['flyer_legs']}
        for p0, p1 in fl['flyer_legs']:
            bpen.line([px(p0), px(p1)], fill=255, width=wide)
            if p0 in ends:
                # a turn in the route: the band carries on round the corner
                x, y = px(p0)
                bpen.ellipse([x - wide / 2.0, y - wide / 2.0, x + wide / 2.0, y + wide / 2.0],
                             fill=255)
        band_img = np.asarray(band_img) > 0
        # only over what holds no walkable floor at any height (a pit, off the level):
        # floor a door cut away or another floor's floor is not the flyers' alone, nor
        # are the plan's added floors (all of a scratch level's floor)
        walk = Image.new('L', (size, size), 0)
        wpen = ImageDraw.Draw(walk)
        for q in fl.get('flyer_floor') or []:
            wpen.polygon([px(v) for v in q], fill=255)
        for (lo, hi), q in fl.get('every_rects') or []:
            wpen.polygon([px(v) for v in q] if q else box(lo, hi), fill=255)
        band_img &= ~_grow(np.asarray(walk) > 0, max(1, int(round(scratch.AGENT_RADIUS / uu))),
                           True)
    return {'floor': flo, 'z': z, 'stairs': stairs, 'across': across, 'used': used,
            'holes': holes & ~voids, 'voids': voids, 'uu': uu, 'void_clear': void_clear,
            'flyer_band': band_img}


def styled(lay, alpha, style=None):
    """RGBA image of `layers()` in the shipped style at its own size. `style` is the
    source level's (riftlord.minimapstyle): {alpha, wall_alpha, band, hatch}; `alpha`
    is the floor's, the walls' scaled with it."""
    import numpy as np
    from PIL import Image
    style = style or {}
    flo = lay['floor']
    size = flo.shape[0]
    hatch_lum = style.get('hatch', HATCH_GREY) if style else HATCH_GREY
    # at most 255: drawn at full alpha for a floor whose walls sit over its plate (43
    # over 38 on Scenario_11's and _14's lower floors), 288 wrapped to 32 and the walls
    # came out faint
    wall_alpha = min(255, int(round(alpha * style.get('wall_alpha', 1) / style.get('alpha', 1)))) \
        if style.get('alpha') else alpha

    def w(share):
        return max(1, int(round(share * size * style.get('scale', 1.0))))
    band = _grow(flo, w(style.get('band', BAND))) & ~flo
    plate = flo | band | lay['holes']
    if lay.get('mass') is not None:
        plate |= lay['mass']
    if lay.get('void_clear') is not None:
        plate &= ~(lay['void_clear'] & ~flo)
    rgb = np.full((size, size), WALL_RGB, dtype=np.uint8)
    rgb[flo] = FLOOR_RGB
    # ledges: neighbouring floor more than a step apart
    z = lay['z']
    edge = np.zeros_like(flo)
    k = w(LEDGE_SIDE)
    # a style without ledge lines draws none (riftlord.minimapstyle: most maps)
    for dy, dx in (((0, 1), (1, 0)) if style.get('ledges', True) else ()):
        both = flo & _shifted(flo, dy, dx)
        step = np.abs(z - _shifted(z, dy, dx)) > LEDGE_DRAWN
        # a drop between two floors, each level for LEDGE_SIDE either side: not the
        # slivers a trim or a kerb leaves (Scenario_04's walkway edge drew three lines)
        level = ((np.abs(z - _shifted(z, -k * dy, -k * dx)) <= LEDGE_STEP)
                 & (np.abs(_shifted(z, dy, dx) - _shifted(z, (k + 1) * dy, (k + 1) * dx))
                    <= LEDGE_STEP))
        # not between two stair pixels: a flight's own steps are hatched, not lined
        edge |= both & step & level & ~(lay['stairs'] & _shifted(lay['stairs'], dy, dx))
    for area, pix in _components(edge):
        if area * lay['uu'] < LEDGE_LENGTH:
            edge.ravel()[pix] = False
    if edge.any():
        ledge = _grow(edge, max(0, w(LEDGE) // 2), True) & flo
        rgb[ledge] = WALL_RGB
    stairs = lay['stairs'] & flo
    if stairs.any() and hatch_lum is not None:
        # the source level's own spacing (Scenario_09's lines 1 px every 7 px)
        period = w(style.get('hatch_period', HATCH_PERIOD))
        width, inset = w(style.get('hatch_width', HATCH)), w(HATCH_INSET)
        # on whole output pixels: a 1 px line split over two by the box filter fell to
        # half its luminance (Scenario_Other_02's 139 grey lines read as 70)
        snap = SUPERSAMPLE if size % SUPERSAMPLE == 0 else 1
        period = max(snap, int(round(period / float(snap))) * snap)
        width = max(snap, int(round(width / float(snap))) * snap)
        line = np.zeros_like(stairs)
        across = lay['across']
        # each flight's lines along its climb; one way per flight (a component climbing
        # both ways is drawn as its two parts: Ice_03's side flights beside its main one)
        for part in ((stairs & across), (stairs & ~across)):
            for _area, pix in _components(part):
                pix = np.array(pix)
                ys, xs = pix // size, pix % size
                along = xs if across.ravel()[pix[0]] else ys
                c0, c1 = int(along.min()), int(along.max()) + 1
                if HATCH_ENDS:
                    # the first and last lines at the flight's ends, the period stretched
                    # to fit
                    span = c1 - c0 - width
                    n = int(round(span / float(period))) + 1
                    if n < 2 or span <= 0:
                        continue
                    marks = np.zeros(c1 - c0, bool)
                    for i in range(n):
                        q = c0 + int(round(i * span / float(n - 1)))
                        q -= q % snap
                        if q < c0:
                            q += snap
                        marks[q - c0:q - c0 + width] = True
                    line.ravel()[pix] = marks[along - c0]
                    continue
                n = (c1 - c0 - 2 * inset - width) // period + 1
                if n < 1:
                    continue
                start = c0 + (c1 - c0 - (n - 1) * period - width) // 2
                start -= start % snap
                off = along - start
                line.ravel()[pix] = (off >= 0) & (off < (n - 1) * period + width) & \
                    (off % period < width)
        rgb[stairs & line & (rgb == FLOOR_RGB)] = hatch_lum
    flyer = None
    if lay.get('flyer_band') is not None:
        # a flyer route over what is drawn empty: the grey band
        flyer = lay['flyer_band'] & ~plate
        rgb[flyer] = FLYER_GREY
        plate = plate | flyer
    out = np.zeros((size, size, 4), dtype=np.uint8)
    out[..., 0] = out[..., 1] = out[..., 2] = rgb
    out[..., :3][~plate] = WALL_RGB
    walls = plate & ~flo
    out[..., 3] = np.where(walls, wall_alpha, np.where(plate, alpha, 0))
    if flyer is not None:
        out[..., 3][flyer] = alpha
    return Image.fromarray(out, 'RGBA')


#: Massed styles (riftlord.minimapstyle) draw the solid standing above the floor this far
#: from it white, in uu (Sand_02's building masses reach 1,100 uu past its corridors).
MASS_REACH = 1200.0


def _mass(fl, volume, size, lay):
    """Bool [v][u]: off-floor pixels within MASS_REACH of the floor where the solid stands
    above the floor's main height."""
    import numpy as np
    from riftlord.build.minimap import mapdata
    project = mapdata.projector(volume)
    uu = lay['uu']
    grid = _solid_grid(fl, project, size, uu)
    flo = lay['floor']
    if grid is None or not flo.any():
        return np.zeros_like(flo)
    zmain = float(np.median(lay['z'][flo]))
    near = _grow(flo, int(round(MASS_REACH / uu)), True) & ~flo
    ys, xs = np.nonzero(near)
    hit = grid.between(_coarse(grid, size, ys, xs), np.full(len(ys), zmain + WALL_LOW),
                       np.full(len(ys), zmain + MASS_HIGH))
    out = np.zeros_like(flo)
    out[ys[hit], xs[hit]] = True
    # drawn as blocks: posts, rails and props opened away, gaps between pieces closed
    k = max(1, int(round(MASS_OPEN / uu)))
    out = _grow(_shrink(out, k, True), k, True)
    k = max(1, int(round(MASS_CLOSE / uu)))
    out = _shrink(_grow(out, k, True), k, True) & ~flo
    # only masses that touch the floor's walls
    return _reach(out | flo, flo) & out


#: ... standing at most this far above it, in uu (a mass, not a sky box or a far cliff).
MASS_HIGH = 3000.0
#: Walls and pillars the floor plan's cut passes through, joined to the playable floor
#: and within WALL_REACH of it, are drawn white past the band (Scenario_01's column
#: bases, Scenario_03's wall pillars), as blocks (MASS_OPEN, MASS_CLOSE).
WALLS_DRAWN = True
WALL_REACH = 300.0


#: Square styles (riftlord.minimapstyle) straighten the floor outline: bumps and notches
#: smaller than this along both axes go, in uu (the jaggies a walkable graph and props
#: leave along a kit wall).
SQUARE_LEN = 60.0


def _line_grow(mask, r, axis):
    import numpy as np
    out = mask.copy()
    for d in range(1, r + 1):
        a = np.roll(mask, d, axis)
        b = np.roll(mask, -d, axis)
        if axis == 0:
            a[:d, :] = False
            b[-d:, :] = False
        else:
            a[:, :d] = False
            b[:, -d:] = False
        out |= a | b
    return out


def _squared(lay):
    """`layers()`'s answer with the floor outline straightened along the axes."""
    import numpy as np
    flo = lay['floor']
    r = max(1, int(round(SQUARE_LEN / 2.0 / lay['uu'])))
    opened = np.zeros_like(flo)
    for axis in (0, 1):
        # opening along one axis: erode then dilate with a line
        er = ~_line_grow(~flo, r, axis)
        opened |= _line_grow(er, r, axis) & flo
    closed = np.ones_like(flo)
    for axis in (0, 1):
        di = _line_grow(opened, r, axis)
        closed &= ~_line_grow(~di, r, axis)
    new = closed & ~flo
    out = dict(lay)
    if new.any():
        z = _spread_height(np.where(flo, lay['z'], np.nan).astype(np.float32), new, 2 * r + 2)
        z = np.where(np.isnan(z), lay['z'], z)
        out['z'] = z
    out['floor'] = closed
    out['stairs'] = lay['stairs'] & closed
    out['holes'] = lay['holes'] & ~closed
    return out


# -- outline simplification (geometry only, per style) ---------------------------

def _contours(mask):
    """[[(x, y)] closed loops of pixel-corner points] round the True regions of `mask`, each
    with the region on its left (outer loops one way round, holes the other)."""
    import numpy as np
    h, w = mask.shape
    m = np.zeros((h + 2, w + 2), bool)
    m[1:-1, 1:-1] = mask
    nxt = {}
    # directed crack edges, region on the left (y down: x right, y down)
    ys, xs = np.nonzero(m[1:-1, 1:-1] & ~m[:-2, 1:-1])      # top edges: go left
    for y, x in zip(ys.tolist(), xs.tolist()):
        nxt.setdefault((x + 1, y), []).append((x, y))
    ys, xs = np.nonzero(m[1:-1, 1:-1] & ~m[2:, 1:-1])       # bottom edges: go right
    for y, x in zip(ys.tolist(), xs.tolist()):
        nxt.setdefault((x, y + 1), []).append((x + 1, y + 1))
    ys, xs = np.nonzero(m[1:-1, 1:-1] & ~m[1:-1, :-2])      # left edges: go down
    for y, x in zip(ys.tolist(), xs.tolist()):
        nxt.setdefault((x, y), []).append((x, y + 1))
    ys, xs = np.nonzero(m[1:-1, 1:-1] & ~m[1:-1, 2:])       # right edges: go up
    for y, x in zip(ys.tolist(), xs.tolist()):
        nxt.setdefault((x + 1, y + 1), []).append((x + 1, y))
    loops = []
    while nxt:
        start = next(iter(nxt))
        loop = [start]
        cur = start
        prev_dir = None
        while True:
            outs = nxt.get(cur)
            if not outs:
                break
            if len(outs) > 1 and prev_dir is not None:
                # a pinch point: turn left first (keeps diagonal touching regions apart)
                def turn(p):
                    d = (p[0] - cur[0], p[1] - cur[1])
                    return (prev_dir[0] * d[1] - prev_dir[1] * d[0])
                outs.sort(key=turn)
            p = outs.pop(0)
            if not outs:
                del nxt[cur]
            prev_dir = (p[0] - cur[0], p[1] - cur[1])
            cur = p
            if cur == start:
                break
            loop.append(cur)
        if len(loop) >= 4:
            loops.append(loop)
    return loops


def _corners(loop):
    """The loop's points where its direction turns."""
    out = []
    n = len(loop)
    for i in range(n):
        a, b, c = loop[i - 1], loop[i], loop[(i + 1) % n]
        if (b[0] - a[0], b[1] - a[1]) != (c[0] - b[0], c[1] - b[1]):
            out.append(b)
    return out or loop[:1]


def _dp(points, eps):
    """Douglas-Peucker of an open polyline."""
    import math
    import numpy as np
    if len(points) < 3:
        return list(points)
    pts = np.asarray(points, float)
    keep = np.zeros(len(pts), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = pts[i], pts[j]
        d = b - a
        L = math.hypot(*d)
        seg = pts[i + 1:j]
        if L == 0:
            dist = np.hypot(*(seg - a).T)
        else:
            dist = np.abs(d[0] * (seg[:, 1] - a[1]) - d[1] * (seg[:, 0] - a[0])) / L
        k = int(np.argmax(dist))
        if dist[k] > eps:
            m = i + 1 + k
            keep[m] = True
            stack.append((i, m))
            stack.append((m, j))
    return [tuple(p) for p in pts[keep]]


def _dp_loop(loop, eps):
    """Douglas-Peucker of a closed loop: split at its two farthest-apart corners."""
    import numpy as np
    pts = _corners(loop)
    if len(pts) < 4:
        return pts
    a = np.asarray(pts, float)
    i = 0
    j = int(np.argmax(np.hypot(*(a - a[0]).T)))
    first = _dp(pts[i:j + 1], eps)
    second = _dp(pts[j:] + pts[:1], eps)
    return first[:-1] + second[:-1]


def _orthogonal(poly, tol_deg=22.5):
    """Edges within tol_deg of an axis made axis-aligned (their ends share the mean x or
    y), 45 degree edges kept; repeated a few times so corners settle."""
    import math
    p = [list(v) for v in poly]
    n = len(p)
    if n < 3:
        return poly
    for _ in range(3):
        for i in range(n):
            a, b = p[i], p[(i + 1) % n]
            dx, dy = b[0] - a[0], b[1] - a[1]
            ang = math.degrees(math.atan2(abs(dy), abs(dx)))
            if ang <= tol_deg:
                y = (a[1] + b[1]) / 2.0
                a[1] = b[1] = y
            elif ang >= 90 - tol_deg:
                x = (a[0] + b[0]) / 2.0
                a[0] = b[0] = x
    return [tuple(v) for v in p]


def _refill(shape, polys):
    """Even-odd fill of the polygons into a bool raster of `shape`."""
    import numpy as np
    from PIL import Image, ImageDraw
    h, w = shape
    out = np.zeros(shape, bool)
    for q in polys:
        if len(q) < 3:
            continue
        img = Image.new('1', (w, h), 0)
        ImageDraw.Draw(img).polygon([(x, y) for x, y in q], fill=1)
        out ^= np.asarray(img, bool)
    return out


def _simplify(mask, eps, square):
    loops = _contours(mask)
    polys = []
    for loop in loops:
        q = _dp_loop(loop, eps)
        if square:
            q = _orthogonal(q)
        polys.append(q)
    return _refill(mask.shape, polys)


#: Outline simplification, in px at 1024: the floor outline's corners are thinned by
#: Douglas-Peucker to this tolerance and, on square styles, edges within 22.5 degrees of an
#: axis made axis-aligned (vanilla's floor outlines carry almost no detail under 5 px:
#: 0.0-0.3 px per 100 outline px on DLC_02, DLC_04, Scenario_04, Other_03). 0: off
#: (square styles then use _squared). Reached cut, 49 floors (round 25 sweep): 2 -> +0.0014,
#: no floor down more than 0.004 (Scenario_07 +0.016, Scenario_11 floor 1 +0.012); 4 ->
#: +0.0005, six floors down up to 0.011. Joint fit with PASSAGE (14 training levels): 1 ->
#: -0.0006, 3 -> +0.0005 over 2.
OUTLINE_EPS = 3.0
#: ... on free-form styles, Douglas-Peucker alone at this tolerance (0: off). Swept 2 and 4:
#: -0.007 and -0.010 on their 17 floors (DLC_03 floor 0 gained false hatching, -0.14).
FREE_EPS = 0.0


def _simplified(lay, eps, square):
    """`layers()`'s answer with the floor outline simplified (_simplify) at `eps` px at
    1024; stairs keep their pixels, pillars and openings stay."""
    import numpy as np
    flo = lay['floor']
    k = flo.shape[0] / 1024.0
    smooth = _simplify(flo, eps * k, square)
    smooth |= lay['stairs'] & flo
    smooth &= ~lay['holes'] & ~lay['voids']
    new = smooth & ~flo
    out = dict(lay)
    if new.any():
        r = int(round(eps * k)) + 2
        z = _spread_height(np.where(flo, lay['z'], np.nan).astype(np.float32), new, 2 * r)
        z = np.where(np.isnan(z), lay['z'], z)
        out['z'] = z
    out['floor'] = smooth
    out['stairs'] = lay['stairs'] & smooth
    out['holes'] = lay['holes'] & ~smooth
    return out


def _attached(fl, volume, size, lay):
    """Bool [v][u]: off-floor pixels within WALL_REACH of the playable floor where the
    floor plan's cut passes through standing geometry, joined to the floor, as blocks."""
    import numpy as np
    from riftlord.build.minimap import mapdata
    project = mapdata.projector(volume)
    uu = lay['uu']
    grid = _solid_grid(fl, project, size, uu)
    flo = lay['floor']
    if grid is None or not flo.any():
        return np.zeros_like(flo)
    # nearest floor height, spread on the solid's coarser grid
    f = max(1, size // grid.size)
    small = flo[::f, ::f]
    zs = np.where(small, lay['z'][::f, ::f], np.nan).astype(np.float32)
    zs = _spread_height(zs, np.ones(small.shape, bool), int(round(WALL_REACH / (uu * f))) + 1)
    near = _grow(small, int(round(WALL_REACH / (uu * f))), True) & ~small & ~np.isnan(zs)
    ys, xs = np.nonzero(near)
    hit = grid.between(_coarse(grid, small.shape[0], ys, xs), zs[ys, xs] + WALL_LOW,
                       zs[ys, xs] + WALL_CUT)
    out_s = np.zeros_like(small)
    out_s[ys[hit], xs[hit]] = True
    raw_hits = np.repeat(np.repeat(out_s, f, 0), f, 1)[:size, :size]
    k = max(1, int(round(MASS_OPEN / uu)))
    out = _grow(_shrink(raw_hits, k, True), k, True)
    # railings and thin walls the opening takes away are drawn as solid lines of
    # RAIL_WIDTH (Scenario_13's railing beside its stairs: about 5 px at 1024, where a
    # 1 px line read as no wall at all)
    thin = raw_hits & ~out if RAILS_DRAWN else np.zeros_like(out)
    rails = np.zeros_like(thin)
    min_len = int(round(RAIL_MIN_LENGTH / uu))
    for area, pix in _components(thin):
        pix = np.array(pix)
        ys_, xs_ = pix // size, pix % size
        if max(ys_.max() - ys_.min(), xs_.max() - xs_.min()) >= min_len:
            rails.ravel()[pix] = True
    if rails.any():
        rails = _grow(rails, max(1, int(round(RAIL_WIDTH * size / 2.0))), True)
    k = max(1, int(round(MASS_CLOSE / uu)))
    out = (_shrink(_grow(out, k, True), k, True) | rails) & ~flo
    region = (_art_region_cached(fl['level_name'], fl.get('art_floor') or 0)
              if fl.get('level_name') and fl['polys'] else None)
    if region is not None:
        # only inside the outline of the region the source art draws: wall ends and door
        # frames past it are not drawn (Scenario_14's stubs off its corridors' right
        # ends); holes it encloses count as inside (the pillars in the corners of Beset's
        # pits)
        out &= ~_outside(_art_mask(region, project, size))
        light = _art_light(fl.get('level_name'), fl.get('art_floor'))
        if light is not None and ART_MASSES:
            # white the source art draws inside its own outline beside the floor (Ice_02's
            # courtyard, drawn as one mass): drawn as a mass where the floor leaves it
            # ... but none within MASS_UNDRAWN of art floor this drawing leaves out (Beset's
            # side rooms past its doors: their walls came out as rings)
            add = _art_mask(light, project, size) & ~_outside(_art_mask(region, project, size))
            walls = (_art_walls_cached(fl['level_name'], fl.get('art_floor') or 0)
                     if ART_WALL_ALPHA else None)
            if walls is not None:
                # a style whose walls are fainter than its plate: its white at the wall
                # alpha counts (Sand_01's ground floor: the left wing drawn at 26)
                wl, wr, wp, wn = walls
                add |= (_art_mask((wl, wp, wn), project, size)
                        & ~_outside(_art_mask((wr, wp, wn), project, size)))
            art = _art_floor(fl.get('level_name'), fl.get('art_floor'))
            if art is not None:
                left = _art_mask(art, project, size) & ~flo
                # whole rooms, not the slivers along an outline a pixel or two off the art's
                k2 = max(1, int(round(BAND * size)))
                left = _grow(_shrink(left, k2, True), k2, True)
                add &= ~_grow(left, max(1, int(round(MASS_UNDRAWN * size))), True)
            # no mass a piece the plan removed or moved away overlaps, whether it held floor
            # or not: a mass knocked down does not come back from the art (what still
            # stands is drawn from the solid above); a mass is the art's white past a band
            # from the floor
            knock = _boxes_mask(fl.get('knocked'), project, size)
            if knock.any():
                far = add & ~_grow(flo, max(1, int(round(BAND * size))), True)
                for _area, pix in _components(far):
                    if knock.ravel()[pix].any():
                        add.ravel()[pix] = False
                add &= ~knock
            # as blocks: slivers that cut leaves are opened away
            k = max(1, int(round(MASS_OPEN / uu)))
            out |= _grow(_shrink(add, k, True), k, True) & ~flo
    band = _grow(flo, 2, True)
    return _reach(out | band, band) & out


def _boxes_mask(boxes, project, size):
    """Bool [v][u]: the footprints of world boxes [(lo, hi)] at `size` px."""
    import numpy as np
    from PIL import Image, ImageDraw
    img = Image.new('L', (size, size), 0)
    if boxes:
        pen = ImageDraw.Draw(img)
        for lo, hi in boxes:
            pen.polygon([tuple(c * size for c in project((x, y)))
                         for x, y in ((lo[0], lo[1]), (hi[0], lo[1]), (hi[0], hi[1]),
                                      (lo[0], hi[1]))], fill=255)
    return np.asarray(img) > 0


#: Draw the white masses the source art draws beside the floor (_attached), none within
#: MASS_UNDRAWN (a share of the drawing: twice the base game's band) of art floor the
#: drawing leaves out.
ART_MASSES = True
MASS_UNDRAWN = 24 / 1024.0
#: Art masses include the white a style draws at its own wall alpha when that is fainter
#: than its plate (_art_walls_cached).
ART_WALL_ALPHA = True
#: Massed styles (Sand_01, Sand_02) draw the art's masses and attached walls too.
MASSED_ART = True
#: Masses are opened by MASS_OPEN (posts and rails narrower than twice it go) and closed
#: by MASS_CLOSE (gaps between pieces narrower than twice it fill), in uu: Sand_02's masses
#: are blocks of whole kit cells (300 uu).
MASS_OPEN = 75.0
MASS_CLOSE = 150.0
#: Railings and thin walls at least RAIL_MIN_LENGTH long (uu) are drawn RAIL_WIDTH wide (a
#: share of the texture: 5 px at 1024, Scenario_13's railings and the 6 px railing walls
#: measured in the shipped art).
RAIL_MIN_LENGTH = 200.0
RAIL_WIDTH = 5 / 1024.0
#: Off: drawn on every railing it lowered the lines share (Scenario_02 0.66 -> 0.28,
#: Scenario_14's lower floor 1.0 -> 0.07): most railings are not in the shipped art.
RAILS_DRAWN = False


def _line_scale(fl, volume):
    """The source level's frame over `volume`'s (half-sizes): line widths read off the
    shipped texture are shares of the source frame, so a mission framed tighter (Beset's
    Cut frame) draws them as thick on the ground as the source does. 1 without a source."""
    vols, _why = sources(fl.get('level_name')) if fl.get('level_name') else (None, None)
    if not vols:
        return 1.0
    k = min(fl.get('art_floor') or 0, len(vols) - 1)
    src = max(abs(float(vols[k]['extent'][0])), abs(float(vols[k]['extent'][1])))
    own = max(abs(float(volume['extent'][0])), abs(float(volume['extent'][1])))
    return src / own if src > 0 and own > 0 else 1.0


def render(fl, volume, size, alpha, style=None, heights=None):
    """The minimap at `size` pixels: drawn at SUPERSAMPLE times and box-filtered. A
    `heights` dict receives 'z': the floor's height per output pixel (NaN off the floor)."""
    from PIL import Image
    import numpy as np
    lay = layers(fl, volume, size * SUPERSAMPLE)
    if heights is not None:
        zs = np.where(lay['floor'], lay['z'], np.nan).astype(np.float32)
        heights['z'] = zs[::SUPERSAMPLE, ::SUPERSAMPLE][:size, :size]
    style = dict(style or {}, scale=_line_scale(fl, volume))
    if style and style.get('square'):
        lay = _simplified(lay, OUTLINE_EPS, True) if OUTLINE_EPS > 0 else _squared(lay)
    elif FREE_EPS > 0:
        lay = _simplified(lay, FREE_EPS, False)
    if style and style.get('massed'):
        lay['mass'] = _mass(fl, volume, size * SUPERSAMPLE, lay)
        if MASSED_ART and WALLS_DRAWN:
            # ... and the art's own masses beside the floor (Sand_01's ground floor's left
            # wing, at its wall alpha)
            lay['mass'] |= _attached(fl, volume, size * SUPERSAMPLE, lay)
    elif WALLS_DRAWN:
        lay['mass'] = _attached(fl, volume, size * SUPERSAMPLE, lay)
    big = styled(lay, alpha, style)
    if SUPERSAMPLE == 1:
        return big
    out = np.array(big.resize((size, size), Image.BOX))
    # the filter premultiplies, which leaves clear pixels black; the shipped ones are white
    out[..., :3][out[..., 3] == 0] = WALL_RGB
    return Image.fromarray(out, 'RGBA')


def framed(fl, src, bounds=None):
    """The source volume moved and resized to the playable area: a square turned as the
    source, FRAME_MARGIN to spare on the long axis."""
    import numpy as np
    from riftlord.build.minimap import mapdata
    project = mapdata.projector(src)
    pts = [v for q in fl['polys'] for v in q] + [c for r in fl['rects'] for c in r]
    if not pts:
        raise SystemExit('the mission has no walkable floor to frame a minimap on')
    uv = np.array([project((p[0], p[1])) for p in pts])
    half0 = max(abs(src['extent'][0]), abs(src['extent'][1]))
    # a probe frame on the source's centre, big enough for all the floor
    grow = max(1.0, 2.0 * float(np.abs(uv - 0.5).max()) * 1.05)
    probe = dict(src, extent=(half0 * grow, half0 * grow, src['extent'][2]))
    bounds = bounds or BOUNDS_SIZE
    lay = layers(fl, probe, bounds)
    # the bare floor here; its wall band is a share of the final square
    rows, cols = np.nonzero(lay['floor'] | lay['holes'])
    n = float(bounds)
    cu, cv = (cols.min() + cols.max() + 1) / 2.0 / n, (rows.min() + rows.max() + 1) / 2.0 / n
    span = max(cols.max() + 1 - cols.min(), rows.max() + 1 - rows.min()) / n
    span /= (1.0 - 2.0 * FRAME_MARGIN - 2.0 * BAND)
    pp = mapdata.projector(probe)
    o = np.array(pp((0.0, 0.0)))
    m = np.array([np.array(pp((1.0, 0.0))) - o, np.array(pp((0.0, 1.0))) - o]).T
    cx, cy = np.linalg.solve(m, np.array([cu, cv]) - o)
    half = span * half0 * grow
    return {'translation': (float(cx), float(cy), float(src['translation'][2])),
            'rotation': tuple(src['rotation']),
            'extent': (float(half), float(half), abs(float(src['extent'][2])))}


def _clear(size):
    from PIL import Image
    return Image.new('RGBA', (size, size), (WALL_RGB, WALL_RGB, WALL_RGB, 0))


# -- build and preview --------------------------------------------------------------

def prepare(level_name, plan, level=None, bounds=None, solid_coarse=None):
    """{volume, reframe, painter, donor, floor, notes} for a build, or raises SystemExit.

    `painter(size)` draws the texture's top mip."""
    want = wanted(plan).get('mode')
    if want == 'cut':
        from riftlord.build.minimap import minimapcut
        src, why = source(level_name)
        if src is None:
            raise SystemExit('the minimap cannot be cut: %s' % why)
        got = minimapcut.prepare(level_name, plan, src, level=level)
        got['donor'] = donor(level_name)
        return got
    from riftlord.build.minimap import minimapstyle
    vols, why = sources(level_name)
    if vols is None:
        raise SystemExit('the minimap cannot be regenerated: %s' % why)
    fl = floor(level_name, plan, level=level)
    if not cut_at_doors(plan):
        # all reachable floor, approaches included (Cut at Doors off, the default)
        fl = floor_reached(level_name, fl)
    if solid_coarse:
        fl['solid_coarse'] = solid_coarse
    elif solid_coarse == 0:
        # a quick sketch: the walkable graph alone, no meshes read
        fl['solid'] = []
    if not fl['polys'] and not fl['rects']:
        raise SystemExit('the minimap cannot be regenerated: the mission has no walkable '
                         'floor')
    groups = floor_groups(vols)
    # a multi-floor level keeps its shipped frames: its floors share one, and its map
    # volume actors (which the HUD places dots by) are not all rewritable (Scenario_16's
    # brush carries no RelativeScale3D)
    reframe = len(groups) == 1
    try:
        frame = framed(fl, vols[0], bounds) if reframe else dict(vols[0])
    except SystemExit as exc:
        raise SystemExit('the minimap cannot be regenerated: %s' % exc)
    bands = floor_bands(vols, groups)
    vstyles = minimapstyle.level_style(vols)
    styles = [vstyles[ks[0]] for ks in groups]
    # every floor on the one square, each keeping its own height band
    volumes = ([dict(frame, translation=(frame['translation'][0], frame['translation'][1],
                                         float(v['translation'][2])),
                     extent=(frame['extent'][0], frame['extent'][1],
                             abs(float(v['extent'][2])))) for v in vols]
               if reframe else [{'translation': tuple(v['translation']),
                                 'rotation': tuple(v['rotation']),
                                 'extent': tuple(v['extent'])} for v in vols])
    element_group = [next(g for g, ks in enumerate(groups) if k in ks)
                     for k in range(len(vols))]
    caches = {}

    def painter_for(g):
        own = frame if reframe else vols[groups[g][0]]

        def paint(size):
            cache = caches.setdefault((size, id(own) if not reframe else 0), {})
            return render_floors(level_name, fl, bands, own, size, styles[g]['alpha'], g,
                                 cache, styles)
        return paint
    painters = [painter_for(g) for g in range(len(groups))]
    notes = fl['notes'] + (['cut at %d door(s) to the floor the rift reaches'
                            % len(fl['doors'])] if cut_at_doors(plan) else
                           ['all reachable floor, approaches included'])
    if len(groups) > 1:
        notes.append('%d floor drawings' % len(groups))
    return {'volume': volumes[0], 'volumes': volumes, 'element_group': element_group,
            'groups': groups, 'reframe': reframe, 'floor': fl, 'notes': notes,
            'donor': donor(level_name), 'alpha': styles[0]['alpha'], 'styles': styles,
            'bands': bands, 'painter': painters[0], 'painters': painters}


def _markers(level_name, plan, level):
    """(rifts, doors, route points) world positions after the plan."""
    from riftlord.build.minimap import mapdataemit
    from riftlord.mapeditor import planactors
    rifts, doors = mapdataemit.markers_for(level_name, plan, level=level)
    points = []
    try:
        nodes, _removed, _p = planactors.graph(copy.deepcopy(plan),
                                               planactors.level_actors(level_name))
        for node in nodes.values():
            at = node.get('at') if isinstance(node, dict) else None
            if at:
                points.append(tuple(at))
    except Exception:                                # noqa: BLE001
        points = [tuple(p['at']) for p in plan.get('points') or [] if p.get('at')]
    return rifts, doors, points


#: Plan keys a preview's drawing depends on: the geometry and what cuts it. Route
#: points, spawners, waves and the look are drawn as markers or not at all, so editing
#: them reuses the drawing (a build still draws from the whole plan); the flyer routes'
#: legs, which the band follows, are keyed on their own (preview_key).
PREVIEW_GEOMETRY = ('level', 'removed', 'edits', 'added', 'doors', 'wiring', 'minimap',
                    'region', 'scratch', 'rifts', 'playerStarts')
#: Drawings kept on disk (the oldest dropped past this many).
PREVIEW_KEEP = 200
#: One preview drawn at a time: a burst of edits must not start a drawing each.
_PREVIEW_LOCK = __import__('threading').Lock()


def _preview_dir():
    from riftlord.core import gamepath
    return os.path.join(gamepath.scratch('cache', gamepath.CODE_ROOT), 'minimap-preview')


def preview_key(level_name, plan, size):
    """Digest of what a preview drawing depends on: the level, the code that draws it,
    the drawing size, the plan's PREVIEW_GEOMETRY and its flyer routes' legs."""
    import hashlib
    import json
    from riftlord.build.minimap import minimapcut
    from riftlord.build.minimap import minimapsolid
    from riftlord.build.minimap import minimapstyle
    keep = {k: (plan or {}).get(k) for k in PREVIEW_GEOMETRY}
    if mode(plan) != 'cut':
        base = ((plan or {}).get('scratch') or {}).get('base') if _is_scratch(plan) else None
        keep['flyers'] = _flyer_routes(base or level_name, plan, from_spawner=True)
    h = hashlib.sha1()
    h.update(('%s %d' % (level_name, size)).encode())
    h.update(json.dumps(keep, sort_keys=True, default=str).encode())
    for mod in (sys.modules[__name__], minimapsolid, minimapstyle, minimapcut):
        try:
            h.update(str(os.path.getmtime(mod.__file__)).encode())
        except OSError:
            pass
    return h.hexdigest()[:20]


def preview(level_name, plan, reframe=True, quick=False):
    """{image (PNG data URL), images [{floor, image}], final, volume, notes,
    sourceVolume} of the plan's minimap for the panel, or {error}: Cut or Regenerate
    (Regenerate when it names neither; every floor of a multi-floor level), or a
    scratch level's own, markers drawn on. With `quick` a small first pass is drawn
    unless the full one is already kept (`final` says which). Drawings are kept on disk
    by preview_key, so an edit that leaves the geometry alone draws only the markers.
    `reframe` is accepted for older callers and ignored."""
    if mode(plan) != 'cut' and not _is_scratch(plan):
        # Regenerate, keeping the block's own settings (Cut at Doors, area)
        plan = dict(plan or {}, minimap=dict(clean((plan or {}).get('minimap')),
                                             mode='regenerate'))
    full = _cached_drawing(level_name, plan, PREVIEW_SIZE)
    if full is None and quick and mode(plan) != 'cut':
        got = _drawing(level_name, plan, PREVIEW_QUICK)
        final = False
    else:
        got = full if full is not None else _drawing(level_name, plan, PREVIEW_SIZE)
        final = True
    if 'error' in got:
        return got
    return dict(_with_markers(level_name, plan, got), final=final)


def _is_scratch(plan):
    from riftlord.mapeditor import scratch
    return scratch.is_scratch(plan)


def _drawing_path(level_name, plan, size):
    return os.path.join(_preview_dir(), '%s_%s.json'
                        % (level_name, preview_key(level_name, plan, size)))


def _cached_drawing(level_name, plan, size):
    import json
    try:
        with open(_drawing_path(level_name, plan, size)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


#: A preview is drawn at this much lower priority than the server (its own thread only),
#: so the editor stays responsive while it draws.
PREVIEW_NICE = 5


def _lower_priority():
    try:
        import threading
        tid = threading.get_native_id()
        now = os.getpriority(os.PRIO_PROCESS, tid)
        os.setpriority(os.PRIO_PROCESS, tid, min(19, now + PREVIEW_NICE))
    except (AttributeError, OSError):
        pass


def _drawing(level_name, plan, size):
    """{images [PNG data URL], volumes, notes, source} of the plan at `size`, kept."""
    import json
    _lower_priority()
    with _PREVIEW_LOCK:
        got = _cached_drawing(level_name, plan, size)
        if got is not None:
            return got
        got = _draw_preview(level_name, plan, size)
        if 'error' not in got:
            path = _drawing_path(level_name, plan, size)
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path + '.part', 'w') as fh:
                    json.dump(got, fh)
                os.replace(path + '.part', path)
                old = sorted((os.path.join(_preview_dir(), f) for f in os.listdir(_preview_dir())),
                             key=os.path.getmtime)
                for f in old[:-PREVIEW_KEEP]:
                    os.remove(f)
            except OSError:
                pass
        return got


def _data_url(img):
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


def _opaque(pic, size):
    """A drawn minimap at full opacity on clear, so it reads on the panel."""
    from PIL import Image
    alpha = pic.getchannel('A').point(lambda a: 255 if a else 0)
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    img.paste(pic.convert('RGB'), mask=alpha)
    return img


def scratch_minimap(level_name, plan, solid_coarse=None):
    """(volume, painter(size)) of a scratch level's minimap: drawn by the same renderer
    and rules as Regenerate, in the host level's style, on the frame the build gives the
    level's map volume (scratch.volume_for)."""
    import math
    from riftlord.build.minimap import minimapstyle
    from riftlord.mapeditor import scratch
    built = scratch.prepare(plan)
    frame = scratch.volume_for(built)
    fl = _scratch_floor(level_name, built)
    if solid_coarse:
        fl['solid_coarse'] = solid_coarse
    elif solid_coarse == 0:
        fl['solid'] = []
    base = fl['scratch_base']
    vols, _why = sources(base)
    style = minimapstyle.level_style(vols)[0] if vols else dict(minimapstyle.DEFAULT)
    bands = [(-math.inf, math.inf)]
    cache = {}

    def paint(size):
        if not fl['rects']:
            return _clear(size)
        return render_floors(base, fl, bands, frame, size, style['alpha'], 0,
                             cache.setdefault(size, {}), [style])
    return frame, paint, fl


def _shown(pic, size):
    """A Regenerate drawing for the panel: its own floor fully opaque, other floors'
    ghosts at the share of it they have in the texture."""
    import numpy as np
    from PIL import Image
    a = np.array(pic.convert('RGBA'))
    al = a[..., 3]
    drawn = al[al > 0]
    if drawn.size:
        vals, counts = np.unique(drawn, return_counts=True)
        big = vals[counts >= PLATE_SHARE * drawn.size]
        plate = float(big.max() if len(big) else vals[np.argmax(counts)])
        a[..., 3] = np.clip(al.astype(np.float32) * (255.0 / plate), 0, 255).astype(np.uint8)
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    img.alpha_composite(Image.fromarray(a, 'RGBA'))
    return img


def _draw_preview(level_name, plan, size):
    """{images [PNG data URL], volumes [{translation, rotation, extent}], notes, source}
    of the plan's minimap at `size`, without markers, or {error}."""
    from riftlord.mapeditor import scratch
    if scratch.is_scratch(plan):
        # a scratch level always draws its own (riftlord/scratch.py)
        vol, paint, sfl = scratch_minimap(level_name, plan,
                                          0 if size < PREVIEW_SIZE else PREVIEW_SOLID_COARSE)
        pics = [_shown(paint(size), size)]
        vols, notes, src = [vol], sfl['notes'], vol
    else:
        from riftlord.mapeditor import leveldata
        level = leveldata.level(level_name)
        try:
            quick = size < PREVIEW_SIZE
            got = prepare(level_name, plan, level=level,
                          bounds=size if quick else BOUNDS_SIZE,
                          solid_coarse=0 if quick else PREVIEW_SOLID_COARSE)
        except SystemExit as exc:
            return {'error': str(exc)}
        if got.get('cut'):
            pics = [_opaque(got['painter'](size), size)]
            vols = [got['volume']]
        else:
            pics = [_shown(p(size), size) for p in got['painters']]
            vols = [got['volumes'][ks[0]] for ks in got['groups']]
        notes = got['notes']
        src = (sources(level_name)[0] or [got['volume']])[0]

    def plain(v):
        return {'translation': [float(x) for x in v['translation']],
                'rotation': [float(x) for x in v.get('rotation') or (0, 0, 0, 1)],
                'extent': [float(x) for x in v['extent']]}
    return {'images': [_data_url(img) for img in pics], 'volumes': [plain(v) for v in vols],
            'notes': notes, 'source': plain(src), 'size': size}


def _with_markers(level_name, plan, got):
    """The panel's answer: `got`'s drawings with the rifts, doors and route points on."""
    from riftlord.build.minimap import mapdata
    from PIL import Image, ImageDraw
    from riftlord.mapeditor import leveldata
    from riftlord.mapeditor import scratch
    if scratch.is_scratch(plan):
        base = (plan.get('scratch') or {}).get('base') or level_name
        level, name = scratch.view(base), base
    else:
        level, name = leveldata.level(level_name), level_name
    rifts, doors, points = _markers(name, plan, level)
    vols = got['volumes']
    size = got['size']
    bands = (floor_bands(vols, [[j] for j in range(len(vols))]) if len(vols) > 1
             else [(-1e30, 1e30)])
    images = []
    for k, (url, vol) in enumerate(zip(got['images'], vols)):
        img = Image.open(io.BytesIO(base64.b64decode(url.split(',', 1)[1]))).convert('RGBA')
        pen = ImageDraw.Draw(img)
        project = mapdata.projector(vol)
        lo, hi = bands[k]
        dot = PREVIEW_DOT * size / float(PREVIEW_SIZE)
        for group, colour in ((points, PREVIEW_POINT), (doors, PREVIEW_DOOR),
                              (rifts, PREVIEW_RIFT)):
            for p in group:
                if len(p) > 2 and not lo <= p[2] < hi:
                    continue
                u, v = project(p)
                x, y = u * size, v * size
                pen.ellipse([x - dot, y - dot, x + dot, y + dot], fill=colour)
        images.append({'floor': k + 1, 'image': _data_url(img)})
    vol = vols[0]
    return {'image': images[0]['image'], 'images': images, 'reframe': True,
            'notes': got['notes'],
            'volume': {'translation': vol['translation'], 'extent': vol['extent']},
            'sourceVolume': {'translation': got['source']['translation'],
                             'extent': got['source']['extent']}}


# -- offline gates -------------------------------------------------------------------

def gates(files, plan, level_name):
    """[(name, passed, detail)] for a built pak's minimap: the MapData read back names,
    for every floor, a texture the pak carries that decodes and draws something, framed
    on its own source floor's height band. Empty when the plan keeps the source's."""
    import tempfile
    from riftlord.build.minimap import mapdata
    from riftlord.formats.ue import texture
    from riftlord.mapeditor import scratch
    if not (wanted(plan) or scratch.is_scratch(plan)):
        return []
    md = next((n for n in files if n.endswith('_MapData.uasset')), None)
    if md is None or md[:-7] + '.uexp' not in files:
        return [('minimap: the pak carries a MapData', False, 'none found')]
    got = mapdata.read_bytes(files[md], files[md[:-7] + '.uexp']) or {}
    vols = got.get('volumes') or []
    out = [('minimap: MapData read back (%d floor(s))' % len(vols), bool(vols), md)]
    src = (sources(level_name)[0] or []) if not scratch.is_scratch(plan) else []
    if src and wanted(plan).get('mode') == 'regenerate':
        out.append(('minimap: one MapData floor per source floor (%d)' % len(src),
                    len(vols) == len(src), '%d against %d' % (len(vols), len(src))))
        bad = ['floor %d z %.0f+-%.0f (source %.0f+-%.0f)' % (
            k, v['translation'][2], v['extent'][2], s['translation'][2], abs(s['extent'][2]))
            for k, (v, s) in enumerate(zip(vols, src))
            if abs(v['translation'][2] - s['translation'][2]) > 1
            or abs(abs(v['extent'][2]) - abs(s['extent'][2])) > 1]
        out.append(('minimap: each floor keeps its source height band', not bad,
                    '; '.join(bad[:3])))
    seen = {}
    for k, v in enumerate(vols):
        tex = (v.get('texture') or '').split('.')[0]
        entry = 'OMD/Content' + tex[len('/Game'):] if tex.startswith('/Game') else tex
        if entry + '.uasset' not in files:
            out.append(('minimap: floor %d texture in the pak' % k, False, tex))
            continue
        if tex in seen:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            pa, px_ = os.path.join(tmp, 't.uasset'), os.path.join(tmp, 't.uexp')
            with open(pa, 'wb') as fh:
                fh.write(files[entry + '.uasset'])
            with open(px_, 'wb') as fh:
                fh.write(files[entry + '.uexp'])
            try:
                img = texture.to_image(texture.parse(pa, px_))
                import numpy as np
                a = np.asarray(img.convert('RGBA'))[..., 3]
                drawn = float((a > 0).mean())
                seen[tex] = img
                out.append(('minimap: floor %d texture decodes %dx%d, %.0f%% drawn'
                            % (k, img.size[0], img.size[1], drawn * 100), drawn > 0, tex))
            except (Exception, SystemExit) as exc:          # noqa: BLE001
                out.append(('minimap: floor %d texture decodes' % k, False, str(exc)))
    if len(seen) > 1:
        import numpy as np
        pics = [np.asarray(i.convert('RGBA')) for i in seen.values()]
        same = all(np.array_equal(pics[0], p) for p in pics[1:])
        out.append(('minimap: the floors draw different pictures', not same, ''))
    return out
