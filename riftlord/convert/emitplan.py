"""Convert one OMD2 level into a list of placed OMD3 actors.

    python3 emitplan.py --scene TheCrunch
    python3 emitplan.py --scene TheCrunch --json plan.json
    python3 emitplan.py --selftest

This joins the stages: `prefabmap` says which prefabs are assemblies, `prefab-map.json`
says what each assembly becomes, `artgroup` merges everything else into runs,
`mesh-map.json` says which OMD3 family or mesh a run's art becomes, `meshpick` fits the
family's members to the run, and `emitgeo` solves each piece's location, rotation and
scale. What comes out is a plan -- data, not a package. Writing it into a level is the
sublevel-authoring step and is deliberately not done here.

**Three routes, because OMD2 says three different kinds of thing.**

1. An **assembly prefab** is replaced whole. `2WideArch.prefab` is 20 copies of 22
   meshes in TheCrunch and 263 copies across 15 levels, and it becomes an OMD3 archway
   recipe: a short arch top on two column trims. One entry converts 6,100 instances.
2. A **run** of ordinary tiles is fitted per run, not per tile, so `Floor_Squarebrick_4x4`
   is placed once instead of nine 1.33-cell scraps.
3. A **special** emits no mesh at all: a rift and a kill volume are gameplay actors, a
   liquid is a flat plane, and some OMD2 pieces (a keystone, an arch top block) are
   simply dropped because the OMD3 mesh that replaces their assembly already includes
   them.

**The check is a box comparison, not a count.** `verify` measures every placement's
world box against the space it was asked to fill: an assembly's parts must stay inside
the copy's own bounding box, and a run's pieces must cover the run's box. A plan that
places the right number of pieces in the wrong places passes a count and fails this.
"""

import argparse
import collections
import json
import math
import os
import sys
from riftlord.core import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.convert import emitgeo
from riftlord.formats.omd3 import meshcat
from riftlord.convert import meshpick

#: How far outside the space it was given a placement may reach, in uu. An assembly is
#: allowed a little: OMD3's pieces are not cut to OMD2's dimensions and the reviewer
#: accepted overhang where the player cannot see it.
SLOP = 60.0

#: Roles that are ONE OBJECT PER INSTANCE and must never be merged into a run or
#: stretched to fill one. The reviewer put it plainly about WallCandle01 -- "this is a
#: decoration, not a wall itself" -- and a decoration does not tile: a torch, a banner
#: or a statue is placed where OMD2 placed it, at its own size. Merged as a surface
#: instead, a 3-tile row of hanging banners became one banner scaled 11x.
PROP_ROLES = frozenset(('light', 'banner', 'statue', 'rift', 'trap', 'spawn'))

#: Roles whose piece means something a bounding box does not capture -- which way it
#: goes. A wall is its plane and may be turned to fit; a staircase turned to fit climbs
#: in the wrong direction. A mapping entry may also set `"directional": true`, which is
#: how the stair side panels and rails opt in without changing their role.
DIRECTIONAL_ROLES = frozenset(('stair',))

#: How far a directional piece may be stretched to span its run before the fit is
#: refused and it falls back to tiling. A staircase scaled much outside this stops
#: reading as steps.
DIRECTIONAL_SCALE = (0.5, 2.5)

#: How many times its SOURCE MESH's volume a piece may occupy before it is refused.
#:
#: This is the structural rule the conversion was missing. The art mapping matched by
#: name and never checked size, so a 34x408x155 base strip (`WallBase1`) was emitted as
#: `Wall_Sec_2_4_V2_RB`, a 1200x101x600 full wall -- **34x the volume**, 117 times -- and
#: a 112x114x321 arch STONE became a 900x316x300 whole archway 348 times. In game that
#: reads as "walls just jutting out for no reason" and slabs hanging in open sky.
#:
#: MEASURED, not chosen. Across 7,236 placements the ratio runs median 0.55, p75 1.09,
#: p90 3.97 -- and then p95 jumps to 20.75. Four is where the distribution breaks, so
#: everything above it is a different population: a mapping that does not fit, not a
#: piece that happens to be large.
#:
#: **This REPORTS, it does not refuse.** Refusing was tried and measurably made the map
#: worse: dropping the oversized pieces removed the structure that held other pieces up,
#: and the count of geometry hanging in mid-air went from 797 to 857. An oversized piece
#: is a bad mapping; a missing one is a bad mapping AND a hole. So the rule names them
#: and `emitview.py` shows them, while the real repair is the thickness rule in
#: `meshpick.plan_run` (which can pick a better family member) or a mapping change.
VOLUME_LIMIT = 4.0

#: Roles that are a LINE, not a surface: tiled along their run's long axis only, keeping
#: their own cross-section. A wall trim is one decorative band along a wall top and a
#: railing is one rail along an edge; treating either as a surface stacks it to fill a
#: thickness it was never meant to fill. Measured: OMD2's `Thickness2` is 105uu tall and
#: `WallTrim_Top_1_8` is 30, so the surface path stacked four bands of thirteen pieces
#: for one trim and took the level from 7,730 placements to 29,525.
LINEAR_ROLES = frozenset(('trim', 'railing', 'column'))

#: Anchors a recipe part may ask for, inside the copy's own world box.
ANCHORS = ('centre', 'floor-centre', 'top-centre', 'side', 'side-outer')


def _maps():
    with open(gamepath.data('mesh-map.json')) as fh:
        mesh = json.load(fh)['map']
    with open(gamepath.data('prefab-map.json')) as fh:
        pre = json.load(fh)['map']
    return ({k.replace('\\', '/').lower(): v for k, v in mesh.items()},
            {k.replace('\\', '/').lower(): v for k, v in pre.items()})


def plan_line(members, size_u, size_v, cat, long_axis=None, normal=None):
    """Lay a family along ONE axis: [(name, offset, length, yaw)].

    Returns placements along whichever in-plane axis is longer, with the piece keeping
    its own depth and height. This is how a trim or a rail actually goes on: end to end
    in a single row.

    **The yaw comes from the RUN's direction, not the piece's own proportions.** This
    used to offer `(0.0, full[0])` and `(90.0, full[1])` and keep whichever was longer,
    which reads a yaw off the piece alone -- while the caller marched the row along the
    run's `long_axis` regardless. When those disagreed the whole row pointed across
    itself: 34 copies of `RailingStairsA_Rails` stood broadside on a y-facing run, 9
    more on an x one, which is the quarter-turned railing the author kept reporting.

    So each of the piece's two horizontals is still a candidate length, but each carries
    the one yaw that actually brings it onto `long_axis` -- the same "there is exactly
    ONE right orientation" rule `meshpick.footprints` applies to a wall. A vertical
    `long_axis` is left alone, since no yaw can lay a piece along z.
    """
    def turns(full):
        """[(length along the run, yaw)] for a piece, given where the run goes."""
        thin_h = 0 if full[0] <= full[1] else 1
        if long_axis in (0, 1):
            #: ONE candidate, not two. Offering both orientations let the row's
            #: remainder be plugged with the piece turned BROADSIDE -- 63 copies of
            #: `RailingStairsA_Rails` contributed their 78uu depth as a length, which is
            #: a rail laid across its own row. A piece has one right orientation here:
            #: its long horizontal along the run, its thin one facing the normal.
            long_h = 1 - thin_h
            return [(full[long_h], 0.0 if long_h == long_axis else 90.0)]
        if normal in (0, 1):
            #: A CLIMBING RUN. 347 of TheCrunch's 789 linear runs span further in z
            #: than horizontally, because a staircase's rail climbs with the steps. No
            #: yaw lays a piece along z, so the yaw comes from the other half of the
            #: same rule instead: the piece's thin horizontal faces the run's own
            #: normal, which is what makes a rail sit flush against the surface it
            #: belongs to rather than broadside across it. Pitching a piece to follow
            #: the climb needs more than a yaw and is not attempted.
            return [(full[1 - thin_h], 0.0 if thin_h == normal else 90.0)]
        return [(full[0], 0.0), (full[1], 90.0)]

    best = None
    for name, _cells, info in members:
        full = [2.0 * abs(info['extent'][a]) for a in (0, 1, 2)]
        for along, yaw in turns(full):
            if along <= 1.0:
                continue
            if best is None or along > best[1]:
                best = (name, along, yaw, info)
    if best is None:
        return []
    #: longest member first, then fill the remainder with the longest that still fits
    lengths = []
    for name, _cells, info in members:
        full = [2.0 * abs(info['extent'][a]) for a in (0, 1, 2)]
        for along, yaw in turns(full):
            if along > 1.0:
                lengths.append((along, name, yaw))
    lengths.sort(reverse=True)
    out, at = [], 0.0
    target = max(size_u, size_v)
    guard = 0
    while at < target - 1.0 and guard < 400:
        guard += 1
        pick = next(((l, n, y) for l, n, y in lengths if l <= target - at + 1.0), None)
        if pick is None:
            pick = lengths[-1]
        length, name, yaw = pick
        out.append((name, at, length, yaw))
        at += length
    return out


#: Which way an OMD3 kit wall's finished face points in its OWN local frame. A kit wall
#: is authored long on local x and thin on local y, so its faces point along +-y, and +y
#: is the assumption here.
#:
#: **If every converted wall comes out facing the wrong way, this is the one constant to
#: turn.** The facing of the pieces RELATIVE to one another is right either way, because
#: it is read from the source; this constant only fixes which absolute sense is "out".
KIT_FRONT = 90.0

#: How much of a mesh's vertical face area must point one way before it has a front at
#: all. `Wall1_2` is 62%, `WallBase4_6` 100%; a symmetric piece has no front and must not
#: be turned on the strength of a coin toss.
FRONT_SHARE = 0.45


def _front_yaw(yaw, world_front, kit_front=KIT_FRONT):
    """`yaw` or `yaw`+180 -- whichever points the piece's finished face like the source.

    `meshpick.footprints` only ever returns 0 or 90 because it answers "which axis does
    this run face", and an axis has no sign. Measured on TheCrunch: every directional
    wall mesh appears at both a yaw and its opposite, and 2,925 emitted pieces come from
    such a source, so about half had their finished face pointing into the rock. The box
    is identical either way round, which is why `misfacing` cannot see it and why this
    has survived every offline check so far.
    """
    best, best_d = yaw, None
    for cand in (yaw % 360.0, (yaw + 180.0) % 360.0):
        d = abs(((kit_front + cand) - world_front + 180.0) % 360.0 - 180.0)
        if best_d is None or d < best_d:
            best, best_d = cand, d
    return best


#: Which way each kit staircase climbs, measured by `stairfit` and cached. Every
#: `Stairs_*_SB` climbs toward 270 deg (local -y); OMD2's climb toward 0 deg (local +x).
STAIRS = None


def _stairs():
    global STAIRS
    if STAIRS is None:
        from riftlord.convert import stairfit
        STAIRS = stairfit.load().get('ascent') or {}
    return STAIRS


def stair_yaw(run, piece, models=None):
    """The yaw that makes `piece` climb the way its source climbs, or None.

    **A staircase's direction is baked into the MESH, not into the instance's rotation.**
    Copying the source's yaw across therefore lands the climb a quarter turn off, because
    the two kits disagree about which local axis is "up the stairs": the author saw a wide
    staircase joining the wrong pair of sides, at 90 degrees to where the stair belonged.

    A flat source is a single riser rather than a flight, so there is no slope to read. Its
    direction comes from the face of the riser instead -- you climb INTO it -- which is the
    same measurement `front_of` already makes for a wall.
    """
    from riftlord.convert import artgroup
    kit = _stairs().get(piece)
    if not kit:
        return None
    got = artgroup.ascent_of(run['mesh'], models)
    if got:
        local = got[0]
    else:
        front = artgroup.front_of(run['mesh'], models)
        if not front or front[1] < FRONT_SHARE:
            return None
        local = (front[0] + 180.0) % 360.0
    world = (local + float((run.get('orientation') or [0.0])[0])) % 360.0
    return (world - kit['degrees']) % 360.0


def _uniform(piece, want, yaw, cat):
    """ONE scale factor for a piece, from the axis it is most constrained on.

    A per-axis scale shears the art, which on a brick floor or a stone wall is the most
    visible kind of wrong. The factor is taken from the largest requested axis, because
    that is the one the run's own extent fixed; the others follow and any difference
    becomes overhang, which the surplus policy already bounds.
    """
    info = cat.get(piece)
    if not info:
        return (1.0, 1.0, 1.0)
    rot = emitgeo.rot_z(yaw)
    size = [2.0 * sum(abs(rot[a][b]) * abs(info['extent'][b]) for b in (0, 1, 2))
            for a in (0, 1, 2)]
    pick = max((0, 1, 2), key=lambda a: want[a])
    k = (want[pick] / size[pick]) if size[pick] > 1.0 else 1.0
    return (k, k, k)


def entry_for(mesh, mesh_map):
    low = str(mesh).replace('\\', '/').lower()
    for key, entry in mesh_map.items():
        if low.endswith(key):
            return entry
    return None


def copy_box(group, models):
    """(lo, hi) world box of one prefab copy, from its instances' own sizes."""
    lo = [float('inf')] * 3
    hi = [float('-inf')] * 3
    for inst in group:
        model = models.get(str(inst['mesh']).replace('\\', '/').lower())
        if not model or not model.get('verts'):
            continue
        vs = model['verts']
        half = [(max(v[a] for v in vs) - min(v[a] for v in vs)) / 2.0
                for a in (0, 1, 2)]
        pos = inst['position']
        for a in (0, 1, 2):
            lo[a] = min(lo[a], pos[a] - half[a])
            hi[a] = max(hi[a], pos[a] + half[a])
    if lo[0] == float('inf'):
        return None
    return lo, hi


def part_axes(info):
    """A kit piece's dimensions as (long horizontal, short horizontal, height).

    **z is height, always.** A kit piece is authored upright, so its vertical extent is
    never the direction it faces, and the only question is which way it is turned.
    Deriving the facing from the thinnest axis instead is what laid a 900uu arch across
    a 507uu-deep opening: `Archway_1x1x3_Short_RB` is 900 x 316 x 300 and its thinnest
    axis is its HEIGHT.
    """
    extent = info['extent']
    full = [2.0 * abs(extent[a]) for a in (0, 1, 2)]
    long_h, short_h = max(full[0], full[1]), min(full[0], full[1])
    #: which of the piece's own axes carries its long side, so the yaw can be worked out
    long_is_x = full[0] >= full[1]
    return {'long': long_h, 'short': short_h, 'up': full[2], 'long_is_x': long_is_x}


def place_recipe(recipe, box, cat, slop=SLOP):
    """Placements for one assembly copy, from its recipe and its world box."""
    lo, hi = box
    size = [hi[a] - lo[a] for a in (0, 1, 2)]
    #: the long horizontal axis is the one an opening spans, so that is where a `side`
    #: part goes -- an archway's columns stand at the two ends of its opening
    long_axis = 0 if size[0] >= size[1] else 1
    short_axis = 1 - long_axis
    mid = [(lo[a] + hi[a]) / 2.0 for a in (0, 1, 2)]
    out = []
    for part in recipe:
        info = cat.get(part['mesh'])
        if not info:
            continue
        ax = part_axes(info)
        #: turn the piece so its long side lies along the opening
        yaw = 0.0 if (ax['long_is_x'] == (long_axis == 0)) else 90.0
        k = 1.0
        if part.get('fit') == 'scale' and ax['long'] > 1.0:
            #: one factor on every axis: scaling a single axis shears the art
            k = size[long_axis] / ax['long']
        world = [0.0, 0.0, 0.0]
        world[long_axis] = ax['long'] * k
        world[short_axis] = ax['short'] * k
        world[2] = ax['up'] * k
        for key in ('offset',):
            pass

        spots = []
        at = part.get('at', 'centre')
        if at == 'centre':
            spots = [list(mid)]
        elif at == 'floor-centre':
            spots = [[mid[0], mid[1], lo[2] + world[2] / 2.0]]
        elif at == 'top-centre':
            spots = [[mid[0], mid[1], hi[2] - world[2] / 2.0]]
        elif at in ('side', 'side-outer'):
            ends = ((lo[long_axis], 1), (hi[long_axis], -1))
            if at == 'side-outer':
                ends = ((hi[long_axis], -1),)
            for end, sign in ends:
                spot = list(mid)
                spot[long_axis] = end + sign * world[long_axis] / 2.0
                spot[2] = lo[2] + world[2] / 2.0
                spots.append(spot)
        shift = part.get('offset') or (0.0, 0.0, 0.0)
        for spot in spots:
            spot = [spot[a] + shift[a] for a in (0, 1, 2)]
            target_min = [spot[a] - world[a] / 2.0 for a in (0, 1, 2)]
            p = emitgeo.place_at(part['mesh'], target_min, world, yaw, (k, k, k), cat)
            if p:
                p['why'] = 'assembly'
                p['anchor'] = at
                out.append(p)
    return out


def _source_size(key, cache={}):
    """The OMD2 mesh's own local size, for comparing against what we emit for it."""
    if key not in cache:
        from riftlord.convert import artgroup as _artgroup
        try:
            box = _artgroup.local_bounds(key)
            cache[key] = [box[1][k] - box[0][k] for k in (0, 1, 2)] if box else None
        except Exception:                                # noqa: BLE001
            cache[key] = None
    return cache[key]


def plan(scene, zip_path=None, cat=None, mirror=True, no_ceilings=False,
         facing=True, recess=True):
    """{placements, actors, dropped, skipped, counts} for one OMD2 level.

    `mirror` turns OMD2's right-handed world into Unreal's left-handed one. It is on by
    default: without it the converted map comes out as a mirror image of the original.
    """
    from riftlord.formats.omd2 import trapgrids
    from riftlord.formats.omd2 import omd2mesh
    from riftlord.convert import prefabmap
    from riftlord.convert import artgroup
    cat = cat or meshcat.load()
    mesh_map, pre_map = _maps()
    with open(gamepath.data('omd2-models.json')) as fh:
        models = json.load(fh)['models']
    zip_path = zip_path or trapgrids.DEFAULT_DATA_ZIP
    insts = omd2mesh.scene_meshes(scene, zip_path)
    #: HANDEDNESS, applied before anything is planned. See `transform.MIRROR_AXIS`.
    if mirror:
        from riftlord.core import transform as _transform
        for inst in insts:
            _transform.mirror_instance(inst)
    rows = {r['prefab']: r for r in prefabmap.survey(scene, zip_path, models)}
    assemblies = {k for k, r in rows.items() if r['kind'] == 'assembly'}

    placements, actors = [], []
    counts = collections.Counter()
    skipped = collections.Counter()
    by_copy = collections.defaultdict(list)
    rest = []
    for inst in insts:
        copy = inst.get('copy')
        if copy and copy.split('#')[0] in assemblies:
            by_copy[copy].append(inst)
        else:
            rest.append(inst)

    #: route 1 -- assemblies, one recipe per copy
    for copy, group in by_copy.items():
        recipe = pre_map.get(copy.split('#')[0])
        if not recipe:
            skipped['assembly with no recipe: %s' % copy.split('#')[0].rsplit('/', 1)[-1]] += 1
            counts['assembly instances left unconverted'] += len(group)
            continue
        box = copy_box(group, models)
        if not box:
            skipped['assembly with no measurable box'] += 1
            continue
        if recipe['kind'] in ('actor', 'liquid') and not recipe['recipe']:
            actors.append({'kind': recipe['kind'], 'role': recipe.get('role'),
                           'at': [(box[0][a] + box[1][a]) / 2.0 for a in (0, 1, 2)],
                           'from': copy})
            counts['gameplay actors'] += 1
            counts['instances replaced by an actor'] += len(group)
            continue
        got = place_recipe(recipe['recipe'], box, cat)
        for p in got:
            p['from'] = copy
        placements.extend(got)
        counts['assembly copies converted'] += 1
        counts['instances replaced by a recipe'] += len(group)
        counts['pieces from assemblies'] += len(got)

    #: route 2 -- props, one placement per instance at its own spot
    surfaces = []
    for inst in rest:
        entry = entry_for(inst['mesh'], mesh_map)
        if entry is None or (entry.get('role') not in PROP_ROLES):
            surfaces.append(inst)
            continue
        action = entry.get('action')
        if action in ('drop', 'drop-if-paired'):
            counts['dropped on purpose'] += 1
            continue
        if action in ('kill-volume', 'gameplay-actor'):
            actors.append({'kind': action, 'role': entry.get('role'),
                           'at': list(inst['position']), 'from': inst['mesh']})
            counts['gameplay actors'] += 1
            continue
        names = entry.get('members') or ([entry['member']] if entry.get('member') else [])
        if not names:
            skipped['prop with no mesh: %s'
                    % str(inst['mesh']).replace('\\', '/').rsplit('/', 1)[-1]] += 1
            continue
        for name in names:
            info = cat.get(name)
            if not info:
                skipped['prop mesh absent: %s' % name] += 1
                continue
            #: A PROP KEEPS THE FACING ITS SOURCE HAD.
            #:
            #: This used to pass yaw 0 for every prop, so 779 pieces stood on an axis
            #: their source mesh never uses -- measured per source mesh against OMD2's
            #: own normals. `WarMageStatue` faces X in OMD2 and every copy of ours faced
            #: Y, which is why the author reported both statues looking the same way
            #: instead of at each other; the same line turned every banner, brazier and
            #: railing cap. A prop has a front, and a bounding box cannot carry it --
            #: the same fault as the stairs, in a different route.
            yaw = float((inst.get('orientation') or [0.0, 0.0, 0.0])[0]) % 360.0
            rot = emitgeo.rot_z(yaw)
            own = [2.0 * sum(abs(rot[a][b]) * abs(info['extent'][b])
                             for b in (0, 1, 2)) for a in (0, 1, 2)]
            #: a prop sits where OMD2's own box sits around the instance position, not
            #: centred on it -- see the run path above
            rel = artgroup.world_bounds(inst['mesh'], inst.get('orientation') or
                                        [0.0, 0.0, 0.0], models)
            if rel:
                mid = [(rel[0][a] + rel[1][a]) / 2.0 for a in (0, 1, 2)]
                at = [inst['position'][a] + mid[a] - own[a] / 2.0 for a in (0, 1, 2)]
            else:
                at = [inst['position'][a] - own[a] / 2.0 for a in (0, 1, 2)]
            #: props keep their own size: scaling a torch to an OMD2 candle's box
            #: distorts a thing the player looks straight at
            p = emitgeo.place_at(name, at, own, yaw, (1.0, 1.0, 1.0), cat)
            if p:
                p['why'] = 'prop'
                p['from'] = inst['mesh']
                placements.append(p)
                counts['props placed at their authored size'] += 1

    #: route 3 -- surfaces, grouped into runs first
    for run in artgroup.runs(surfaces):
        entry = entry_for(run['mesh'], mesh_map)
        if entry is None:
            skipped['no map entry: %s' % str(run['mesh']).replace('\\', '/').rsplit('/', 1)[-1]] += 1
            counts['run instances with no mapping'] += run['count']
            continue
        action = entry.get('action')
        if action in ('drop', 'drop-if-paired'):
            counts['dropped on purpose'] += run['count']
            continue
        #: Ceilings can be left out wholesale. OMD2's data says TheCrunch's middle IS
        #: roofed -- the run over it is 23x34 cells, 100% filled by 782 source instances
        #: -- but the author reports an open courtyard in game and, more immediately, a
        #: ceiling hides the floor levels underneath it, which is what they need to see
        #: to judge the stairs. So this is a switch rather than a decision.
        if no_ceilings and entry.get('role') == 'ceiling':
            counts['ceilings left out'] += run['count']
            continue
        if action in ('kill-volume', 'gameplay-actor'):
            actors.append({'kind': action, 'role': entry.get('role'),
                           'at': list(run['origin']), 'from': run['mesh']})
            counts['gameplay actors'] += 1
            continue
        axis = run['axis']
        inplane = [a for a in (0, 1, 2) if a != axis]
        linear = entry.get('role') in LINEAR_ROLES
        #: how tall the OMD2 mesh is, so a family cannot offer a lying piece for an
        #: upright one -- see `meshpick.HEIGHT_FLOOR`
        _src = _source_size(str(run['mesh']).replace('\\', '/'))
        source_size = _src
        #: The run's world box, placed by the SOURCE MESH'S OWN OFFSET rather than
        #: centred on the instance position. OMD2 does not centre its meshes: a floor's
        #: origin is its walking SURFACE (local z -100..0) and a staircase's is its
        #: BOTTOM (0..600). Centring put every staircase 300uu too low, with its top
        #: landing on a floor and its bottom hanging in the void, and every floor 50uu
        #: too high -- which is what left a converted level's storeys unconnected.
        rel_lo = run.get('offset_lo')
        rel_hi = run.get('offset_hi')
        if rel_lo is None or rel_hi is None:
            ext = artgroup.world_extent(run['mesh'], run['orientation'], models)
            thick = ext[axis] if ext else 100.0
            rel_lo = [-thick / 2.0 if a == axis else 0.0 for a in (0, 1, 2)]
            rel_hi = [thick / 2.0 if a == axis else 0.0 for a in (0, 1, 2)]
        target_min = [0.0, 0.0, 0.0]
        target_size = [0.0, 0.0, 0.0]
        target_min[axis] = run['origin'][axis] + rel_lo[axis]
        target_size[axis] = rel_hi[axis] - rel_lo[axis]
        for k, a in enumerate(inplane):
            target_min[a] = run['origin'][a] + rel_lo[a]
            target_size[a] = run['span'][k]

        names = entry.get('members') or ([entry['member']] if entry.get('member') else [])
        hides = entry.get('role') in meshpick.HIDES_OVERHANG
        #: which way the SOURCE's finished face points in world, so the emitted piece can
        #: be turned to match -- see `_front_yaw`. Only a wall has a front; a floor's
        #: facing is its normal and carries no sign worth keeping.
        #: a directional role keeps its direction on EVERY route, not just the
        #: directional one -- see `stair_yaw`
        directional = entry.get('role') in DIRECTIONAL_ROLES
        world_front = None
        if facing and axis in (0, 1):
            from riftlord.convert import artgroup
            _f = artgroup.front_of(run['mesh'], models)
            if _f and _f[1] >= FRONT_SHARE:
                world_front = (_f[0] + float((run.get('orientation')
                                              or [0.0])[0])) % 360.0

        if linear:
            family = entry.get('family')
            members = ([(n, tuple(cat[n]['cells']), cat[n]) for n in names if n in cat]
                       or (meshpick.inventory(family, cat) if family else []))
            if not members:
                skipped['linear role resolves to nothing: %s'
                        % str(run['mesh']).replace('\\', '/').rsplit('/', 1)[-1]] += 1
                counts['run instances unresolved'] += run['count']
                continue
            long_axis = inplane[0] if target_size[inplane[0]] >= target_size[inplane[1]] \
                else inplane[1]
            if source_size:
                kept = [m for m in members
                        if meshpick.height_ok(m[2], source_size[2])
                        and meshpick.upright_matches(m[2], source_size)]
                members = kept or members
            for name, at_along, length, yaw in plan_line(
                    members, target_size[inplane[0]], target_size[inplane[1]], cat,
                    long_axis=long_axis, normal=axis):
                info = cat[name]
                rot = emitgeo.rot_z(yaw)
                own = [2.0 * sum(abs(rot[a][b]) * abs(info['extent'][b])
                                 for b in (0, 1, 2)) for a in (0, 1, 2)]
                at = [run['origin'][a] - own[a] / 2.0 for a in (0, 1, 2)]
                at[long_axis] = target_min[long_axis] + at_along
                want = list(own)
                p = emitgeo.place_at(name, at, want, yaw, (1.0, 1.0, 1.0), cat)
                if p:
                    p['why'] = 'line'
                    p['from'] = run['mesh']
                    p['run'] = run['cells']
                    p['axis'] = axis
                    placements.append(p)
                    counts['pieces laid as a line'] += 1
            counts['runs laid as a line'] += 1
            continue

        #: A LIQUID SURFACE IS STRETCHED, NOT TILED.
        #:
        #: Everything else is tiled because scaling one piece over a whole run put a
        #: 150uu column trim across two tiles at 23.6x. A liquid plane is the opposite
        #: case: it is a featureless sheet, so stretching it is invisible, and tiling it
        #: is actively wrong -- `plan_run` covered a run of pits with ONE plane at its
        #: native 1890x1985 against an 800x800 source pit, 50 times over, and the acid
        #: flooded the arena floor so no trap could be placed on it (seen in game).
        if entry.get('action') == 'liquid-plane' and names and names[0] in cat:
            name = names[0]
            own = [2.0 * abs(v) for v in cat[name]['extent']]
            want = list(target_size)
            scale = [1.0, 1.0, 1.0]
            for a_ in inplane:
                if own[a_] > 1e-6 and want[a_] > 1e-6:
                    scale[a_] = want[a_] / own[a_]
            p = emitgeo.place_at(name, list(target_min), want, 0.0, tuple(scale), cat)
            if p:
                p['why'] = 'liquid plane'
                p['from'] = run['mesh']
                p['run'] = run['cells']
                p['axis'] = axis
                placements.append(p)
                counts['liquid planes'] += 1
            counts['runs by liquid plane'] += 1
            continue

        #: A STAIRCASE HAS A DIRECTION OF ASCENT, WHICH A BOUNDING BOX CANNOT EXPRESS.
        #:
        #: Every other piece is oriented by fitting its box, which is correct for a wall
        #: because a wall IS its plane. A staircase turned 90 degrees fits the same box
        #: and ascends sideways: OMD2's `2widestairs` is 1202 wide x 803 deep while
        #: `Stairs_3x_4_SB` is 900 x 1211, so the fitter turned it and the level change
        #: was never built -- the middle of TheCrunch stayed level with the ledges around
        #: it, and 560 stair SIDE panels followed the turn as walls at yaw 90 (seen in
        #: game).
        #:
        #: So a directional role takes its yaw from the SOURCE instance and is scaled to
        #: fit, never turned to fit.
        directional = (entry.get('directional')
                       or entry.get('role') in DIRECTIONAL_ROLES)
        if directional and names and names[0] in cat:
            name = names[0]
            yaw = round((run.get('orientation') or [0.0])[0] / 90.0) * 90.0 % 360.0
            rot = emitgeo.rot_z(yaw)
            own = [2.0 * sum(abs(rot[a_][b_]) * abs(cat[name]['extent'][b_])
                             for b_ in (0, 1, 2)) for a_ in (0, 1, 2)]
            #: UNIFORM, and never along the piece's own thin axis. Fitting each axis
            #: separately asked a 12uu-thick stair side panel to become 1200 thick --
            #: a scale of 101 -- and refused every one of them. A directional piece
            #: also has to keep its proportions: a staircase stretched on one axis
            #: stops reading as steps.
            big = max(own)
            room = max(target_size[a_] for a_ in inplane)
            factor = (room / big) if big > 1e-6 and room > 1e-6 else 1.0
            scale = [factor, factor, factor]
            want = [own[a_] * factor for a_ in (0, 1, 2)]
            if DIRECTIONAL_SCALE[0] <= factor <= DIRECTIONAL_SCALE[1]:
                p = emitgeo.place_at(name, list(target_min), want, yaw,
                                     tuple(scale), cat)
                if p:
                    p['why'] = 'directional'
                    p['from'] = run['mesh']
                    p['run'] = run['cells']
                    p['axis'] = axis
                    placements.append(p)
                    counts['directional pieces'] += 1
                    counts['runs by direction'] += 1
                    continue
            else:
                skipped['%s would need a uniform scale of %.2f, outside %s'
                        % (name, factor, list(DIRECTIONAL_SCALE))] += 1

        if names:
            #: a named member is still TILED across the run, never stretched over it.
            #: Scaling one piece to the whole run put a single 150uu column trim across
            #: a 2-tile span at 23.6x and an 11-tile angled wall on one piece at 14.7x.
            for name in names:
                members = [(name, tuple(cat[name]['cells']), cat[name])] \
                    if name in cat else []
                if not members:
                    skipped['named mesh absent: %s' % name] += 1
                    continue
                parts, _sc = meshpick.plan_run(
                    members, target_size[inplane[0]], target_size[inplane[1]],
                    axis, may_overhang=hides, thickness=target_size[axis],
                    source_size=source_size)
                for pname, off_u, off_v, size_u, size_v, yaw in parts:
                    if directional:
                        _sy = stair_yaw(run, pname, models)
                        if _sy is not None:
                            yaw = _sy
                            counts['stairs turned to their source climb'] += 1
                    elif world_front is not None:
                        yaw = _front_yaw(yaw, world_front)
                    at = list(target_min)
                    at[inplane[0]] += off_u
                    at[inplane[1]] += off_v
                    want = list(target_size)
                    want[inplane[0]] = size_u
                    want[inplane[1]] = size_v
                    scale = _uniform(pname, want, yaw, cat)
                    p = emitgeo.place_at(pname, at, want, yaw, scale, cat)
                    if p:
                        p['why'] = 'named mesh'
                        p['from'] = run['mesh']
                        p['run'] = run['cells']
                        p['axis'] = axis
                        placements.append(p)
                        counts['pieces from named meshes'] += 1
            counts['runs by named mesh'] += 1
            continue

        family = entry.get('family')
        if not family:
            skipped['entry with no family and no member: %s'
                    % str(run['mesh']).rsplit('\\', 1)[-1]] += 1
            counts['run instances unresolved'] += run['count']
            continue
        members = meshpick.inventory(family, cat)
        if not members:
            skipped['family resolves to nothing: %s' % family] += 1
            counts['run instances unresolved'] += run['count']
            continue
        #: A horizontal run served by a family with no flat member is a MAPPING fault,
        #: not a fitting one: `plan_run`'s never-empty guard will stand a wall panel on
        #: the floor rather than build nothing. TheCrunch has two -- four pitched
        #: `Wall1_1` slabs and two `WallRamp` ramps, both mapped role=wall -- and both
        #: axis readings are right (the slabs carry pitch 90; the ramp is thinnest on z),
        #: so the entry is what needs a human, and naming it here is how it gets one.
        if axis == 2 and not any(meshpick.lies_flat(m[2]) for m in members):
            skipped['MAPPING: %s is role=%s on a horizontal run, and family %s has no '
                    'flat member' % (str(run['mesh']).replace('\\', '/').rsplit('/', 1)[-1],
                                     entry.get('role'), family)] += run['count']
        parts, _scale = meshpick.plan_run(members, target_size[inplane[0]],
                                          target_size[inplane[1]], axis,
                                          may_overhang=hides,
                                          thickness=target_size[axis],
                                          source_size=source_size)
        if not parts:
            skipped['family fitted nothing: %s' % family] += 1
            counts['run instances unresolved'] += run['count']
            continue
        for name, off_u, off_v, size_u, size_v, yaw in parts:
            if directional:
                _sy = stair_yaw(run, name, models)
                if _sy is not None:
                    yaw = _sy
                    counts['stairs turned to their source climb'] += 1
            elif world_front is not None:
                yaw = _front_yaw(yaw, world_front)
            at = list(target_min)
            at[inplane[0]] += off_u
            at[inplane[1]] += off_v
            want = list(target_size)
            want[inplane[0]] = size_u
            want[inplane[1]] = size_v
            scale = _uniform(name, want, yaw, cat)
            p = emitgeo.place_at(name, at, want, yaw, scale, cat)
            if p:
                p['why'] = 'family'
                p['from'] = run['mesh']
                p['run'] = run['cells']
                p['axis'] = axis
                placements.append(p)
                counts['pieces from families'] += 1
        counts['runs by family'] += 1

    #: THE SIZE RULE, applied to every route at once rather than at each append site.
    kept, refused = [], collections.Counter()
    for p in placements:
        key = str(p.get('from') or '').replace('\\', '/')
        s = _source_size(key)
        if s:
            lo, hi = p['box']
            sv = max(1.0, s[0] * s[1] * s[2])
            ev = (hi[0] - lo[0]) * (hi[1] - lo[1]) * (hi[2] - lo[2])
            if ev / sv > VOLUME_LIMIT:
                refused['OVERSIZED: %s -> %s is %.0fx its source volume'
                        % (key.rsplit('/', 1)[-1], p['mesh'], ev / sv)] += 1
        kept.append(p)
    counts['oversized (reported, still placed)'] = sum(refused.values())
    skipped.update(refused)
    placements = kept

    #: A BURIED FLOOR IS THE ONE THE GAME WALKS ON -- see `RECESS_DROP`.
    #:
    #: Measured on TheCrunch: the recess floor was ALREADY emitted over 453 of the
    #: arena's 462 cells (98%) -- it was only ever buried. With this on, the blanket over
    #: it falls from 462 cells to 47, and those 47 are rim bleed (railing end caps and
    #: boundary tiles where the surrounding floor genuinely exists), not a blanket.
    placements, buried = drop_buried_floors(placements) if recess else (placements, [])
    if buried:
        counts['floor pieces burying a lower floor, removed'] = len(buried)
        _b = collections.Counter(p['mesh'] for p in buried)
        for _m, _n in _b.most_common(6):
            skipped['RECESS: %s laid over a floor below it' % _m] += _n

    #: NOTHING BURIED INSIDE ANOTHER PIECE -- see `drop_buried_pieces`.
    placements, hidden = drop_buried_pieces(placements)
    if hidden:
        counts['pieces buried inside another, removed'] = len(hidden)

    counts['placements'] = len(placements)
    counts['actors'] = len(actors)
    return {'scene': scene, 'placements': placements, 'actors': actors,
            'counts': counts, 'skipped': skipped}


#: How much thinner than its next-thinnest axis a placed box must be before "which way
#: does this face" has an answer at all. A column (`ColumnTrim_CastStone_900W` is
#: 104x103x900) is thin on BOTH horizontals, so calling either one its facing is a coin
#: toss -- counting those polluted three separate orientation metrics. 0.6 is the same
#: threshold the family picker uses for the same reason.
UNAMBIGUOUS = 0.6


def misfacing(result, limit=UNAMBIGUOUS):
    """Placements turned a quarter off: [(placement, thin axis, wanted axis)].

    **This is the direct test, not a metric.** `emitgeo.place_at` already returns the box
    a placement really produces, and a run already knows which axis its surface faces. A
    piece standing for that surface has to be thinnest on that same axis -- a wall on a
    y-facing run whose box is thinnest on x IS the quarter-turned wall, with no inference
    in between. The facing ratios I had been tracking (instance normals 46/54, run axes
    42/58, emitted 37/63) could only say the population had drifted; they could not name
    one wrong piece, which is why six passes of tuning them changed nothing on screen.

    Only unambiguously flat pieces are judged -- see `UNAMBIGUOUS`.
    """
    out = []
    for p in result['placements']:
        axis = p.get('axis')
        if axis is None:
            continue
        lo, hi = p['box']
        size = [hi[a] - lo[a] for a in (0, 1, 2)]
        order = sorted((0, 1, 2), key=lambda a: size[a])
        thin, next_thin = order[0], order[1]
        if size[next_thin] <= 0.0 or size[thin] / size[next_thin] > limit:
            continue
        if thin != axis:
            out.append((p, thin, axis))
    return out


#: How deep a recess may be before the floor above it is a separate STOREY rather than
#: fill over the same room, in uu.
#:
#: OMD2 stacks floors: 681 of TheCrunch's 1,857 floor cells carry more than one level. The
#: centre arena is `FloorMain1` at top -980 with a `Ground4x4` blanket at -750 laid over
#: **100% of it** (110 tiles, unit scale, 100 thick, one prefab, every one flagged visible
#: and colliding) -- so nothing in the data marks the blanket as fill, and emitting it
#: buried the recess and left the centre level with the main path.
#:
#: The drop tells them apart. Measured over the stacked cells: 230uu over 82 cells
#: (`Ground4x4` over `FloorMain1` -- the recess, and `MiniStep2a`/`MiniStep1A` top out at
#: -1050 and -800, which is the thin stairway between the two), then 600uu over 42 cells,
#: which is the level ladder itself (the two big plateaus are -750 and -150). 400 sits in
#: the gap, so a shallower stack is one room and a deeper one is two storeys that must
#: both be built.
RECESS_DROP = 400.0

#: How far apart two floors must be before the upper one is fill rather than DETAIL laid
#: on top. `FloorMainDesign04` and its base sit within a few uu of each other and both
#: belong; only a real step counts.
RECESS_FLOOR = 100.0

#: How thick a placement may be and still be judged a floor. A wall standing in a cell is
#: not a floor above it.
FLOOR_THICK = 150.0


#: How much of a lower floor an upper one must cover before it counts as a BLANKET laid
#: over it rather than a balcony above it.
#:
#: Derived rather than named: a blanket covers essentially all of what it hides.
#: `Ground4x4` covers 100% of the centre recess (`FloorMain1`, 135 cells, 110 tiles at
#: unit scale), while a real upper storey covers its own footprint and stops.
BLANKET_SHARE = 0.9


def _floor_layers(placements, cell, thick):
    """{(i, j): [(top z, index)]} for floor-like placements, lowest first.

    Liquid is excluded. An acid plane is a surface to look at, not one to stand on, and
    counting it as a floor is what broke the first version of this pass: the recess floor
    sat 385uu above the acid, inside the recess band, so the recess floor was removed
    along with the blanket and the arena came out with no floor at all.
    """
    out = collections.defaultdict(list)
    for n, p in enumerate(placements):
        if p.get('why') == 'liquid plane':
            continue
        lo, hi = p['box']
        if hi[2] - lo[2] > thick or min(hi[0] - lo[0], hi[1] - lo[1]) < cell:
            continue
        for i in range(int(lo[0] // cell), int(hi[0] // cell) + 1):
            for j in range(int(lo[1] // cell), int(hi[1] // cell) + 1):
                out[(i, j)].append((hi[2], n))
    for v in out.values():
        v.sort()
    return out


def drop_buried_floors(placements, cell=150.0, low=RECESS_FLOOR, high=RECESS_DROP,
                       thick=FLOOR_THICK, share=BLANKET_SHARE):
    """Remove a blanket floor laid over a floor between `low` and `high` below it.

    Returns (kept, removed). The lower surface wins because that is the one the game walks
    on -- see `RECESS_DROP` for the measurement that fixes the band.

    **Which mesh is a blanket is measured, not named.** For every (upper source, lower
    source) pair seen stacked within the band, the upper is a blanket over the lower only
    when it covers at least `share` of the cells that lower floor occupies. That keeps a
    balcony, which covers part of the floor below it and stops, and removes a ground tile
    laid across the whole of a recess.

    It cannot cascade: the decision is taken per (upper, lower) source pair against the
    ORIGINAL layering, so removing a blanket never promotes anything into being removed.
    """
    layers = _floor_layers(placements, cell, thick)

    def src(n):
        return str(placements[n].get('from') or '?').replace('\\', '/')

    #: Cells each floor occupies AT ONE LEVEL, and the cells where one sits above
    #: another. The level has to be part of the key: `FloorMain1` is used at every
    #: storey of TheCrunch (tops -1065, -825, -225, 75, 375, ...), so measuring a
    #: blanket against all ~1,000 cells that mesh occupies anywhere scored the recess
    #: at 13% and found nothing. Against the 135 cells of the recess ITSELF it is 100%.
    def level(z):
        return round(z / 100.0) * 100.0

    own = collections.defaultdict(set)
    over = collections.defaultdict(set)
    for key, stack in layers.items():
        for top, n in stack:
            own[(src(n), level(top))].add(key)
        for a in range(len(stack)):
            for b in range(a + 1, len(stack)):
                gap = stack[b][0] - stack[a][0]
                if low <= gap <= high:
                    over[(src(stack[b][1]), src(stack[a][1]),
                          level(stack[a][0]))].add(key)

    blankets = set()
    for (upper, lower, at), cells in over.items():
        if upper == lower:
            continue
        base = own[(lower, at)]
        if base and len(cells) / float(len(base)) >= share:
            blankets.add((upper, lower, at))
    if not blankets:
        return list(placements), []

    doomed = set()
    for key, stack in layers.items():
        for a in range(len(stack)):
            for b in range(a + 1, len(stack)):
                gap = stack[b][0] - stack[a][0]
                if not low <= gap <= high:
                    continue
                if (src(stack[b][1]), src(stack[a][1]),
                        level(stack[a][0])) in blankets:
                    doomed.add(stack[b][1])
    kept = [p for n, p in enumerate(placements) if n not in doomed]
    return kept, [p for n, p in enumerate(placements) if n in doomed]


#: How far a piece's box may stick out of another's and still count as buried, in uu.
#: The catalogue carries two decimals, so a unit is noise; 2 absorbs the rounding that
#: `place_at` leaves when it solves a location from a pivot offset.
BURIED_SLOP = 2.0

#: How closely a buried piece must match its container's extent on an axis to count as
#: spanning it, in uu. A stacked trim shares its container's cross-section exactly; a
#: railing standing inside a staircase's empty box shares nothing.
FLUSH = 10.0


def drop_buried_pieces(placements, slop=BURIED_SLOP):
    """Remove pieces wholly inside another piece's box. Returns (kept, removed).

    Measured on TheCrunch: 213 pieces sit at the same spot as another, and the largest
    group is 131 copies of `ColumnTrim_CastStone_300W` buried inside a
    `ColumnTrim_CastStone_900W` -- the line route's remainder fill overshooting and laying
    a short trim inside a tall one. None of it can be seen; all of it is drawn.

    Only a piece that is strictly SMALLER is dropped, so two pieces that coincide exactly
    lose one copy rather than both.

    **A box is not the solid it stands for**, so containment alone is not enough: a
    staircase's box is mostly the empty air above its steps, and a railing standing beside
    it sits inside that box while being perfectly visible. So the buried piece must also
    span its container's FULL cross-section on at least two axes -- which is what a short
    trim stacked inside a tall one does, and what a railing beside a staircase does not.
    """
    boxes = []
    for n, p in enumerate(placements):
        lo, hi = p['box']
        boxes.append((n, lo, hi, (hi[0] - lo[0]) * (hi[1] - lo[1]) * (hi[2] - lo[2])))
    #: bucket by a coarse cell so this stays linear rather than quadratic over 8,000 boxes
    grid = collections.defaultdict(list)
    for item in boxes:
        _n, lo, hi, _v = item
        for i in range(int(lo[0] // 600), int(hi[0] // 600) + 1):
            for j in range(int(lo[1] // 600), int(hi[1] // 600) + 1):
                grid[(i, j)].append(item)
    doomed = set()
    for item in boxes:
        n, lo, hi, vol = item
        if n in doomed:
            continue
        seen = set()
        for i in range(int(lo[0] // 600), int(hi[0] // 600) + 1):
            for j in range(int(lo[1] // 600), int(hi[1] // 600) + 1):
                for other in grid.get((i, j), ()):
                    m, olo, ohi, ovol = other
                    if m == n or m in seen or m in doomed:
                        continue
                    seen.add(m)
                    if ovol < vol or (ovol == vol and m < n):
                        continue
                    if not all(olo[k] - slop <= lo[k] and hi[k] <= ohi[k] + slop
                               for k in (0, 1, 2)):
                        continue
                    flush = sum(1 for k in (0, 1, 2)
                                if abs(olo[k] - lo[k]) <= FLUSH
                                and abs(ohi[k] - hi[k]) <= FLUSH)
                    if flush >= 2:
                        doomed.add(n)
                        break
                if n in doomed:
                    break
            if n in doomed:
                break
    kept = [p for n, p in enumerate(placements) if n not in doomed]
    return kept, [p for n, p in enumerate(placements) if n in doomed]


def verify(result, slop=SLOP):
    """Problems with a plan: non-finite numbers, and boxes nowhere near their target."""
    bad = []
    for p in result['placements']:
        for v in list(p['location']) + list(p['scale']):
            if not math.isfinite(v):
                bad.append('%s has a non-finite transform' % p['mesh'])
                break
        else:
            lo, hi = p['box']
            thin = sum(1 for a in (0, 1, 2) if hi[a] - lo[a] <= 0.0)
            #: a liquid surface is a PLANE -- every one OMD3 ships has a z size of
            #: exactly 0 -- so one flat axis is correct and only two is degenerate
            if thin > 1 or (thin == 1 and hi[2] - lo[2] > 0.0):
                bad.append('%s has an empty box' % p['mesh'])
            if any(abs(v) > 1e7 for v in lo + hi):
                bad.append('%s is placed outside any plausible level' % p['mesh'])
            if any(s <= 0.0 for s in p['scale']):
                bad.append('%s has a non-positive scale' % p['mesh'])
    return bad


def selftest():
    """Place one synthetic assembly and check its parts stay inside the copy's box."""
    bad = []
    cat = meshcat.load()
    #: a 2WideArch-shaped box: 507 deep, 940 wide, 1586 tall
    box = ([0.0, 0.0, 0.0], [507.0, 939.8, 1586.3])
    recipe = [{'mesh': 'Archway_1x1x3_Short_RB', 'at': 'top-centre', 'fit': 'scale'},
              {'mesh': 'ColumnTrim_CastStone_1200W', 'at': 'side', 'fit': 'keep'}]
    got = place_recipe(recipe, box, cat)
    if len(got) != 3:
        bad.append('a side part should emit at BOTH ends: got %d placements' % len(got))
    for p in got:
        lo, hi = p['box']
        for a in (0, 1, 2):
            if lo[a] < box[0][a] - SLOP or hi[a] > box[1][a] + SLOP:
                bad.append('%s (%s) reaches %.0f..%.0f outside the copy box on axis %d'
                           % (p['mesh'], p['anchor'], lo[a], hi[a], a))
                break
    #: the arch top must sit at the TOP, not the middle
    top = [p for p in got if p['anchor'] == 'top-centre']
    if top:
        lo, hi = top[0]['box']
        if hi[2] < box[1][2] - SLOP:
            bad.append('the arch top was placed %.0fuu below the opening top'
                       % (box[1][2] - hi[2]))
    #: a scaled part must stay square -- scaling one axis alone shears the art
    for p in got:
        s = p['scale']
        if max(s) - min(s) > 1e-6:
            bad.append('%s was scaled unevenly: %s' % (p['mesh'], s))
    if verify({'placements': got}):
        bad.extend(verify({'placements': got}))
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--scene', default='TheCrunch')
    ap.add_argument('--json')
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args(argv)
    if args.selftest:
        bad = selftest()
        for b in bad:
            print('FAILED  %s' % b)
        print('emitplan selftest: %s'
              % ('passed' if not bad else '%d problem(s)' % len(bad)))
        return 1 if bad else 0
    got = plan(args.scene)
    print('%s:' % args.scene)
    for k, n in sorted(got['counts'].items(), key=lambda t: -t[1]):
        print('   %-40s %6d' % (k, n))
    problems = verify(got)
    print('   %-40s %6d' % ('placements that fail verification', len(problems)))
    turned = misfacing(got)
    print('   %-40s %6d' % ('pieces turned a quarter off', len(turned)))
    if turned:
        by_mesh = collections.Counter(
            '%s on a %s run, thin on %s'
            % (t[0]['mesh'], 'xyz'[t[2]], 'xyz'[t[1]]) for t in turned)
        for k, n in by_mesh.most_common(10):
            print('      %-56s x%d' % (k, n))
    for p in problems[:6]:
        print('      %s' % p)
    if got['skipped']:
        print('\n   not converted:')
        for k, n in got['skipped'].most_common(12):
            print('      %-46s x%d' % (k, n))
    if args.json:
        with open(args.json, 'w') as fh:
            json.dump({'scene': got['scene'], 'placements': got['placements'],
                       'actors': got['actors'],
                       'counts': dict(got['counts'])}, fh)
        print('\n   wrote %s (%.1f MB)'
              % (args.json, os.path.getsize(args.json) / 1e6))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
