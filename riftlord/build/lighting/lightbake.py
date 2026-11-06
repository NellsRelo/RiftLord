"""Write a level's baked lighting ourselves: the Rift Lord build hook.

Each streamed sublevel names its `<sublevel>_BuiltData` package, which holds the
light maps of every primitive in it (lightmaps.py reads and writes it, lightcodec.py
encodes the texels). This module copies a sublevel's BuiltData into the mod beside
the sublevel copy, rewrites its light maps, and points the sublevel copy at it.

Step 2 of the baker (workshop/research/LIGHTMAP-BAKER.md) is a probe: with
`PATTERN` on, every light map in the PATTERN_SUBLEVELS shows a checker in its own
lightmap UV space, in a hue of its own, so the game proves it draws texels we
encoded (proven in game 2026-10-16).

Step 3: with `BAKE` on, the BAKE_SUBLEVELS' light maps are baked from the level's
Static lights and sky light (lightbaker.py, run in the render venv as a subprocess),
with the plan's removed, moved and added pieces (added meshes and kit pieces get light
maps of their own in the persistent level, and their Static lights light the bake:
lightalloc.py), its liquid lights recoloured
(riftlord.liquidlights), its own lights' values, moves and removals and its Sky Light
values (riftlord.build.world.lighting), and the chosen sky's texture. Bakes are cached by
their inputs.

Switches: `lightbake.PATTERN=true` (default off), `lightbake.BAKE=true|false|auto` (auto by
default: bakes when needs() finds the shipped light maps stale); `lightbake.QUALITY="preview"`
for a quick bake.
"""

import colorsys
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import threading

import numpy as np
from riftlord.core import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

#: Replace the copied sublevels' light maps with the probe pattern.
PATTERN = False
#: Bake the copied sublevels' light maps from the level's lights (step 3): True, False, or
#: 'auto' (the default) = when needs() finds the shipped light maps stale: a sky, liquid or
#: light colour of the plan's own (since Beset Bake C-F, 2026-10-16), or pieces removed,
#: moved or added (since 2026-11-04: the lighting is baked from the final geometry, user
#: 2026-10-15).
BAKE = 'auto'
#: The decision for the build under way (decide()).
_bake_now = False
#: Sublevels whose light maps BAKE writes, by their tag. The persistent level's few
#: meshes keep shipped light maps (its copy is written by makemod).
BAKE_SUBLEVELS = ('Castle', 'Environment', 'Lighting', 'Foliage')
#: Spacing and height over the lava of the lava lights a bake adds over open lava,
#: measured on North Wing (nearest-neighbour median 925 uu; median 34 uu over the lava).
LAVA_LIGHT_SPACING = 925.0
LAVA_LIGHT_RISE = 34.0
#: Also bake the persistent level's own few light-mapped meshes and ship its BuiltData
#: copy (persistent_files, called by riftlord.emit after makemod builds the level).
BAKE_PERSISTENT = True
#: Bake cache folder (env); default `bakes` beside LIGHTMAPS_CACHE.
BAKE_CACHE_ENV = 'LIGHTBAKE_CACHE'
#: UE's ExponentialHeightFog FogMaxOpacity default, when the home level states none.
DEFAULT_FOG_MAX_OPACITY = 1.0
#: Bake quality (lightbaker.QUALITIES): 'final' for releases, 'preview' for quick looks.
QUALITY = 'final'
#: Folder under the bake cache holding per-sublevel results.
SUBLEVEL_CACHE = 'sublevels'
#: Most seconds one bake may take.
BAKE_TIMEOUT = 4 * 3600
#: Sublevels whose baked lighting the pattern replaces, by their tag.
PATTERN_SUBLEVELS = ('Castle', 'Environment')
#: Checker squares across one mesh's light map, each way.
CHECKER_CELLS = 4
#: Light a white surface receives from a bright and a dark checker square.
BRIGHT = 1.6
DARK = 0.35
#: Saturation of the per-mesh hue (0 grey, 1 pure).
SATURATION = 0.85
#: Hue step between consecutive meshes (the golden ratio spreads them evenly).
HUE_STEP = 0.618033988749895
#: Suffix of a level package's built-data package.
SUFFIX = '_BuiltData'


#: {shipped BuiltData path: (uasset, uexp, ubulk)} baked by prepare() for this build
_baked = {}
#: Per thread: the kind of build under way (build_as) and the reasons the light maps were
#: stale that its decide() left unbaked (pending).
_BUILD = threading.local()
#: The console's name for a build no build_as block names (the command line, probes).
DEFAULT_KIND = 'build'


class build_as:
    """Name the builds this thread runs in the block (`kind`, said on the console) and
    whether they may bake. An editor's build (`bake=False`: opening a mission in the
    Onslaught Editor, saving waves into it) needs the level's actors and waves, not its
    lighting, so it keeps the shipped light maps and leaves pending() for the Missions
    page's Build, Install and Package to bake."""

    def __init__(self, kind, bake=True):
        self.kind, self.bake = kind, bake

    def __enter__(self):
        self.before = getattr(_BUILD, 'kind', None), getattr(_BUILD, 'bake', True)
        _BUILD.kind, _BUILD.bake = self.kind, self.bake
        return self

    def __exit__(self, *exc):
        _BUILD.kind, _BUILD.bake = self.before


def pending():
    """[reason] the last decide() of this thread found the light maps stale and left to a
    build that may bake (build_as); [] when it baked, nothing was stale, or this program
    carries no baker (baker_missing), which no later build of it would change."""
    return list(getattr(_BUILD, 'pending', None) or [])


def stale(plan):
    """[reason] the shipped light maps no longer match a plan's level, [] when they do:
    a sky, liquid or plane look, the Sky Light's values, the level's own Static lights
    changed, moved or removed (riftlord.build.world.lighting), and pieces removed, moved
    or added (the kinds a bake lays in: lightscene.OCCLUDING_KINDS and
    lightalloc.RECEIVE_KINDS). Suns, fog and post process are drawn every frame and
    never baked."""
    from riftlord.build.lighting import lightalloc
    from riftlord.build.lighting import lightscene
    from riftlord.build.world import lighting
    from riftlord.mapeditor import scratch
    plan = lighting.migrated(plan or {}) or {}
    look = plan.get('look') or {}
    #: a sky reaches the light maps through a Static sky light (scratch.streamed_scene)
    static_sky = scratch.streamed_scene(plan).get('static_sky', True)
    baked = ('sky', 'liquid', 'planes') if static_sky else ('liquid', 'planes')
    out = [what for what in baked if look.get(what)]
    if static_sky and (lighting.clean(plan.get(lighting.PLAN_KEY)).get('skyLight')):
        out.append('sky light')
    changed = baked_lights_changed(plan)
    if changed:
        out.append('%d changed light%s' % (changed, '' if changed == 1 else 's'))
    removed = len(plan.get('removed') or [])
    #: an edit that only switches collision is no move (piececollision)
    from riftlord.mapeditor import piececollision
    moved = len(piececollision.moving_edits(plan))
    kinds = set(lightscene.OCCLUDING_KINDS) | set(lightalloc.RECEIVE_KINDS)
    added = sum(1 for a in plan.get('added') or []
                if a.get('kind', 'mesh') in kinds and a.get('name'))
    for count, what in ((removed, 'removed'), (moved, 'moved'), (added, 'added')):
        if count:
            out.append('%d %s piece%s' % (count, what, '' if count == 1 else 's'))
    return out


def baked_lights_changed(plan):
    """How many of the level's own lights the plan changes (`lights`), moves
    (`actorMoves`) or removes (`removedFx`) that are, or become, Static. With no level
    named, every `lights` entry counts."""
    from riftlord.build.world import lighting
    plan = plan or {}
    edits = lighting.clean_lights(plan.get(lighting.LIGHTS_KEY))
    level = plan.get('level')
    if not level:
        return len(edits)
    from riftlord.mapeditor import scratch
    if scratch.is_scratch(plan):
        return 0
    from riftlord.mapeditor import leveldata
    try:
        rig = {l['key']: l for l in leveldata.light_rig(level) if l.get('key')}
    except Exception:                                # noqa: BLE001
        return len(edits)
    moved = set(leveldata.clean_actor_moves(plan.get('actorMoves')))
    gone = {str(k) for k in plan.get('removedFx') or []}
    count = 0
    for key, light in rig.items():
        static = str(light.get('mobility') or '').endswith('Static')
        made = (edits.get(key) or {}).get('Mobility')
        if (key in edits and (static or made == lighting.STATIC)) or \
                (static and (key in moved or key in gone)):
            count += 1
    return count


def receives(plan):
    """Whether a plan's level has anything to bake into: the host's own light-mapped
    pieces (scratch.streamed_scene keeps them), or added pieces (lightalloc.receivers,
    with BAKE_PERSISTENT)."""
    from riftlord.build.lighting import lightalloc
    from riftlord.mapeditor import scratch
    if scratch.streamed_scene(plan).get('host', True):
        return True
    return bool(BAKE_PERSISTENT and lightalloc.receivers(plan))


def needs(plan):
    """Whether BAKE 'auto' bakes a plan's build: stale() finds its light maps out of date
    and it has something to bake into (receives)."""
    return bool(stale(plan)) and receives(plan)


def decide(plan):
    """Settle BAKE for one build and say on the console which kind of build it is
    (build_as) and why it bakes or not; returns whether it bakes."""
    global _bake_now
    kind = getattr(_BUILD, 'kind', None) or DEFAULT_KIND
    _bake_now, _BUILD.pending = False, []
    if not receives(plan):
        print('lightbake: %s: not baking: no piece of the level takes a light map'
              % kind)
        return _bake_now
    reasons = stale(plan) or (['lightbake.BAKE is on'] if BAKE is True else [])
    if BAKE is False or not reasons:
        print('lightbake: %s: not baking: %s' % (kind,
              'switched off (lightbake.BAKE)' if BAKE is False else
              'the plan changes nothing the shipped light maps hold'))
        return _bake_now
    if not getattr(_BUILD, 'bake', True):
        _BUILD.pending = reasons
        print('lightbake: %s: not baking; Build, Install or Package on the Missions page '
              'bakes the light maps, which the plan changes: %s' % (kind, ', '.join(reasons)))
        return _bake_now
    why = baker_missing()
    if why:                          # no later build of this program could bake it either
        print('lightbake: %s: NOT baking (%s); the build keeps the shipped light maps, '
              'though the plan changes them: %s' % (kind, why, ', '.join(reasons)))
        return _bake_now
    print('lightbake: %s: bakes its light maps: %s' % (kind, ', '.join(reasons)))
    _bake_now = True
    return _bake_now


#: The switch a frozen Rift Lord server answers by running lightbaker (run_workshop.py).
FROZEN_SWITCH = '--lightbake'


#: The baker's native packages (Mitsuba, Dr.Jit, moderngl), zipped into a frozen Rift
#: Lord beside its modules by tools/build.py.
FROZEN_PACKAGES = 'baker-packages.zip'
#: The baker's module, which a frozen Rift Lord must carry.
BAKER_MODULE = 'riftlord.build.lighting.lightbaker'


def baker_missing():
    """Why this program cannot bake, or None. From source the render venv runs the
    baker; a frozen Rift Lord carries it when its build could add FROZEN_PACKAGES."""
    import importlib.util
    from riftlord.core import gamepath
    if not gamepath.frozen():
        return None
    if importlib.util.find_spec(BAKER_MODULE) is None:
        return 'this Rift Lord build carries no light baker'
    if not os.path.exists(os.path.join(getattr(sys, '_MEIPASS', ROOT), FROZEN_PACKAGES)):
        return 'this Rift Lord build carries neither Mitsuba nor moderngl'
    return None


def frozen_packages():
    """Put a frozen Rift Lord's FROZEN_PACKAGES on sys.path, unpacked once per build
    into the user's cache (`baker/<digest>`). Returns the folder, or None without them."""
    import zipfile
    from riftlord.core import gamepath
    archive = os.path.join(getattr(sys, '_MEIPASS', ROOT), FROZEN_PACKAGES)
    if not os.path.exists(archive):
        return None
    h = hashlib.sha1()
    with open(archive, 'rb') as fh:
        for block in iter(lambda: fh.read(1 << 20), b''):
            h.update(block)
    folder = os.path.join(gamepath.scratch('baker'), h.hexdigest()[:16])
    done = os.path.join(folder, '.unpacked')
    if not os.path.exists(done):
        part = folder + '.part%d' % os.getpid()
        shutil.rmtree(part, ignore_errors=True)
        with zipfile.ZipFile(archive) as z:
            z.extractall(part)
        open(os.path.join(part, '.unpacked'), 'w').close()
        try:
            os.replace(part, folder)
        except OSError:                              # another bake unpacked it first
            shutil.rmtree(part, ignore_errors=True)
    sys.path.insert(0, folder)
    return folder


def baker_command(args):
    """(argv, subprocess keywords) running lightbaker with `args` at low priority: the
    render venv from source, the frozen server itself (FROZEN_SWITCH) when frozen."""
    from riftlord.core import gamepath
    from riftlord.build.lighting import lightbaker
    if gamepath.frozen():
        cmd = [sys.executable, FROZEN_SWITCH] + list(args)
    else:
        cmd = [lightbaker.VENV_PYTHON, '-m', lightbaker.__name__] + list(args)
    if os.name == 'nt':
        return cmd, {'creationflags': subprocess.BELOW_NORMAL_PRIORITY_CLASS}
    prefix = ['nice', '-n', '10'] + (['ionice', '-c', '3'] if shutil.which('ionice') else [])
    return prefix + cmd, {}


def active():
    return bool(PATTERN or _bake_now)


def give_up():
    """Stop this build's bake (its bake failed): it keeps the level's shipped light maps."""
    global _bake_now
    _bake_now = False
    _baked.clear()


def _tags():
    return BAKE_SUBLEVELS if _bake_now else PATTERN_SUBLEVELS


def built_data_path(level_path):
    return level_path + SUFFIX


def sublevel_tags(level_name, plan=None):
    """The sublevels the active mode rewrites that this level has and the plan's level
    streams (scratch.streamed_scene), each with a BuiltData package."""
    from riftlord.core import gamefiles
    from riftlord.build.navigation import navpatch
    from riftlord.mapeditor import scratch
    streamed = scratch.streamed_scene(plan).get('streamed')
    base = navpatch.map_path(level_name)
    out = []
    for tag in _tags():
        if streamed is not None and tag not in streamed:
            continue
        stem = '../../../OMD/Content' + built_data_path('%s_%s' % (base, tag))[len('/Game'):]
        if gamefiles.stored(stem + '.uasset'):
            out.append(tag)
    return out


def _leaf(path):
    return path.rsplit('/', 1)[-1]


def retarget_level(uasset, uexp, old_level_path, new_level_path):
    """(uasset, uexp, changed): a level copy pointed at its own BuiltData package.

    The level imports its registry as package `<level>_BuiltData` and object of the
    same leaf; both names move to the copy's."""
    from riftlord.build import modtable
    from riftlord.formats.ue.uassetio import Package
    pkg = Package(uasset, uexp)
    pkg.load_payloads()
    old, new = built_data_path(old_level_path), built_data_path(new_level_path)
    changed = modtable.retarget(pkg, exact={old: new, _leaf(old): _leaf(new)})
    if changed != 2:
        raise ValueError('%s names its built data %d times, expected the path and the '
                         'object once each' % (_leaf(old_level_path), changed))
    ua, ux = pkg.write()
    return ua, ux, changed


def _hue(index):
    return colorsys.hsv_to_rgb((index * HUE_STEP) % 1.0, SATURATION, 1.0)


def mesh_light(index, w, h):
    """Float light (h, w, 3) for one mesh's allocation: a checker of its hue and grey."""
    ys = (np.arange(h) + 0.5) / h
    xs = (np.arange(w) + 0.5) / w
    cell = ((xs[None, :] * CHECKER_CELLS).astype(int)
            + (ys[:, None] * CHECKER_CELLS).astype(int)) % 2
    bright = BRIGHT * np.asarray(_hue(index))
    dark = np.full(3, DARK)
    return np.where(cell[..., None] == 0, bright, dark)


def paint(bd, report):
    """Rewrite every 2D light map in a BuiltData with the pattern, re-encoding its atlases.

    Each mesh gets its own scale/add vectors (Lightmass quantisation of its own texels),
    so meshes sharing an atlas stay independent, as the editor packs them."""
    from riftlord.build.lighting import lightcodec
    from riftlord.build.lighting import lightmaps
    atlases = {}                     # texture export index -> {coef: float image, ...}
    for n, (_guid, mesh) in enumerate(bd.registry['meshes']):
        lm = mesh['lightmap']
        if lm['type'] != lightmaps.LMT_2D:
            continue
        for slot, coef in ((0, 0), (1, 2)):           # HQ texture: coef 0/1; LQ: 2/3
            ti = bd.texture_ref(lm['textures'][slot])
            if ti is None or ti not in bd.textures:
                continue
            top = bd.textures[ti]['formats'][0]['data']['mips'][0]['dims']
            size_x, half_y = top[0], top[1] // 2
            x0, y0, x1, y1 = lightcodec.allocation_rect(
                lm['coord_scale'], lm['coord_bias'], size_x, half_y)
            if x1 <= x0 or y1 <= y0:
                continue
            entry = atlases.setdefault(ti, {
                'img': np.zeros((half_y * 2, size_x, 4), np.uint8),
                'mask': np.zeros((half_y * 2, size_x), bool), 'coef': coef})
            light = mesh_light(n, x1 - x0, y1 - y0)
            c0, c1 = lightcodec.flat_light(light)
            texels, scale, add = lightcodec.quantize(c0, c1)
            entry['img'][y0:y1, x0:x1] = texels[..., coef, :]
            entry['img'][half_y + y0:half_y + y1, x0:x1] = texels[..., coef + 1, :]
            entry['mask'][y0:y1, x0:x1] = True
            entry['mask'][half_y + y0:half_y + y1, x0:x1] = True
            for k in (coef, coef + 1):
                lm['scale'][k] = tuple(float(v) for v in scale[k])
                lm['add'][k] = tuple(float(v) for v in add[k])
        report['meshes'] = report.get('meshes', 0) + 1
    for ti, entry in atlases.items():
        fmt_entry = bd.textures[ti]['formats'][0]
        fmt = fmt_entry['pixel_format']
        chain = fmt_entry['data']['mips']
        images = lightcodec.mips(entry['img'], len(chain), entry['mask'])
        top_w, top_h = chain[0]['dims'][:2]
        for k, (mip, img) in enumerate(zip(chain, images)):
            # the cooked chain records mips below one block as 4x4 (BC block size)
            w, h = max(top_w >> k, 1), max(top_h >> k, 1)
            if img.shape[:2] != (h, w):
                raise ValueError('%s mip %dx%d, built %dx%d'
                                 % (bd.export_name(ti), w, h, img.shape[1], img.shape[0]))
            payload = lightcodec.encode(fmt, img)
            if len(payload) != len(mip['payload']):
                raise ValueError('%s mip %dx%d encodes to %d bytes, shipped %d'
                                 % (bd.export_name(ti), w, h, len(payload),
                                    len(mip['payload'])))
            mip['payload'] = payload
        report.setdefault('atlases', []).append('%s %s %dx%d' % (
            bd.export_name(ti), fmt, chain[0]['dims'][0], chain[0]['dims'][1]))
    return report


def copy_built_data(old_level_path, new_level_path):
    """(new BuiltData path, (uasset, uexp, ubulk), report) for a sublevel copy:
    the shipped BuiltData renamed to the copy's and, with PATTERN, repainted."""
    from riftlord.build.lighting import lightmaps
    from riftlord.build import modtable
    old, new = built_data_path(old_level_path), built_data_path(new_level_path)
    if _bake_now and old in _baked:
        ua, ux, ub = _baked[old]
    else:
        ua, ux, ub = lightmaps.fetch(old)
    bd = lightmaps.BuiltData(ua, ux, ub)
    report = {'source': old, 'baked': bool(_bake_now and old in _baked)}
    if PATTERN and not _bake_now:
        paint(bd, report)
    modtable.rename_package(bd.pkg, old, _leaf(old), new, _leaf(new))
    blobs = bd.write()
    if bd.uptnl:
        raise ValueError('%s keeps optional mips in a .uptnl, which builds do not ship yet'
                         % _leaf(old))
    check = lightmaps.BuiltData(*blobs)               # it must read back
    report['readback_meshes'] = len(check.registry['meshes'])
    report['registry_object'] = check.export_name(check.registry_index)
    if report['registry_object'] != _leaf(new):
        raise ValueError('copied registry is named %s, expected %s'
                         % (report['registry_object'], _leaf(new)))
    return new, blobs, report


def imports_resolve(level_files, built_files, new_level_path):
    """True when the level copy imports exactly the registry the BuiltData copy exports."""
    from riftlord.formats.ue.uassetio import Package
    level = Package(level_files[0], level_files[1])
    names = [s for s, _h in level.names]
    want = built_data_path(new_level_path)
    package_index = None
    for i, rec in enumerate(level.imports):
        if names[struct.unpack_from('<i', rec, 20)[0]] == want:
            package_index = -(i + 1)
    if package_index is None:
        return False
    objects = [names[struct.unpack_from('<i', rec, 20)[0]] for rec in level.imports
               if struct.unpack_from('<i', rec, 16)[0] == package_index]
    built = Package(built_files[0], built_files[1])
    bnames = [s for s, _h in built.names]
    exported = {bnames[struct.unpack_from('<i', e['meta'], 16)[0]] for e in built.exports}
    return bool(objects) and all(o in exported for o in objects)


# ------------------------------------------------------------------ step 3: bake

def bake_spec(level_name, plan):
    """What lightbaker.bake needs from a plan: its piece edits, the liquid lights' new
    colours ({'<tag>|<export>': rgb}), the plan's own lights' values, moves and removals
    and its Sky Light values (riftlord.build.world.lighting.bake_inputs), and its sky."""
    from riftlord.build.world import lighting
    from riftlord.build.world import liquidlights
    plan = lighting.migrated(plan or {}, level_name) or {}
    colours = {}
    for tag, lights in (liquidlights.edits(level_name, plan) or {}).items():
        for export, rgb in lights.items():
            colours['%s|%d' % (tag, int(export))] = [float(v) for v in rgb]
    sky = ((plan.get('look') or {}).get('sky')) or None
    inputs = lighting.bake_inputs(level_name, plan)
    sub = {k: plan.get(k) or [] for k in ('removed', 'edits', 'added')}
    sub['look'] = plan.get('look') or {}
    from riftlord.build.lighting import lightalloc
    from riftlord.mapeditor import scratch
    scene = scratch.streamed_scene(plan)
    out = {'plan': sub, 'light_colours': colours, 'sky_level': None, 'sky': sky,
           'light_edits': {k: v for k, v in inputs['lights'].items()},
           'light_moves': inputs['moves'], 'light_removed': inputs['removed'],
           'sky_light_edit': inputs['sky_light'],
           'sky_fog': sky_fog(level_name, plan) if sky else None,
           'lava_lights': lava_light_points(level_name, plan) if scene.get('host', True)
           else []}
    out.update(scene)
    if BAKE_PERSISTENT:
        out['receivers'] = lightalloc.receivers(plan)
        out['added_lights'] = lightalloc.added_lights(plan)
    return out


def sky_fog(level_name, plan):
    """{'colour': linear rgb, 'opacity'} of the height fog the sky is seen through: the
    level's fog with the plan's Height Fog values, or None when the level has no fog."""
    from riftlord.build.world import lighting
    from riftlord.mapeditor import scratch
    plan = lighting.migrated(plan or {}, level_name) or {}
    try:
        state = lighting.shown(level_name, scratch.is_scratch(plan))
    except Exception:                                # noqa: BLE001
        return None
    fog = (state.get('stored') or {}).get('heightFog')
    if fog is None:
        return None
    fog = dict(fog)
    fog.update(lighting.clean(plan.get(lighting.PLAN_KEY)).get('heightFog') or {})
    colour = fog.get('FogInscatteringColor') or \
        lighting.DEFAULTS['ExponentialHeightFogComponent']['FogInscatteringColor']
    opacity = fog.get('FogMaxOpacity')
    return {'colour': [float(v) for v in colour[:3]],
            'opacity': float(DEFAULT_FOG_MAX_OPACITY if opacity is None else opacity)}


def lava_light_points(level_name, plan):
    """[(x, y, z)] where a bake puts North Wing-style lava lights (lightbaker.LAVA_LIGHT):
    the open lava riftlord.lavafx finds on the planes the plan turns into lava, thinned to
    LAVA_LIGHT_SPACING (greedy, in grid order), LAVA_LIGHT_RISE over the surface."""
    from riftlord.build.world import lavafx
    planes = lavafx._lava_planes(level_name, plan)
    if not planes:
        return []
    out = []
    for x, y, z in lavafx._open_lava(level_name, plan, planes):
        if all((x - a) ** 2 + (y - b) ** 2 >= LAVA_LIGHT_SPACING ** 2 for a, b, _c in out):
            out.append((float(x), float(y), float(z) + LAVA_LIGHT_RISE))
    return out


def _bake_cache():
    from riftlord.core import gamepath
    root = os.environ.get(BAKE_CACHE_ENV)
    if not root:
        base = os.environ.get('LIGHTMAPS_CACHE') or gamepath.scratch('lightmaps', beside=gamepath.CODE_ROOT)
        root = os.path.join(os.path.dirname(base.rstrip('/')), 'bakes')
    os.makedirs(root, exist_ok=True)
    return root


#: Version of the bake key; a change misses every cached bake.
KEY_VERSION = 2
#: Inputs recorded by the baker in each cached bake (bakeinputs).
INPUTS = 'inputs.json'


def _source_hash():
    """sha1 of every file of riftlord/build/lighting, by name and content."""
    h = hashlib.sha1()
    for name in sorted(n for n in os.listdir(HERE) if n.endswith('.py')):
        with open(os.path.join(HERE, name), 'rb') as fh:
            h.update(name.encode() + b'\0' + fh.read())
    return h.hexdigest()[:16]


def source_digest(level_name, tags):
    """sha256 of the shipped bytes a bake starts from: each baked package's level
    (uasset/umap, uexp) and BuiltData (uasset, uexp, ubulk)."""
    from riftlord.build.lighting import lightmaps
    from riftlord.build.navigation import navpatch
    from riftlord.core import gamefiles
    base = navpatch.map_path(level_name)
    h = hashlib.sha256()
    for tag in tags:
        level_path = base if tag == 'persistent' else '%s_%s' % (base, tag)
        for blob in tuple(gamefiles.package(level_path)) + tuple(
                lightmaps.fetch(built_data_path(level_path))):
            h.update(hashlib.sha256(blob).digest())
    return h.hexdigest()[:16]


def bake_key(level_name, tags, spec):
    """The cached bake's key: the level, its baked packages, the spec (the plan's pieces,
    lights, sky and quality), the shipped bytes it starts from and the lighting code."""
    return hashlib.sha1(json.dumps([KEY_VERSION, level_name, tags, spec, _source_hash(),
                                    source_digest(level_name, tags)],
                                   sort_keys=True).encode()).hexdigest()[:16]


def reusable(out):
    """(True, '') when `out` holds a finished bake whose recorded inputs (bakeinputs) are
    unchanged, else (False, why not)."""
    from riftlord.build.lighting import bakeinputs
    if not os.path.exists(os.path.join(out, 'report.json')):
        return False, 'no earlier bake of these inputs'
    return bakeinputs.unchanged(bakeinputs.load(os.path.join(out, INPUTS)))


def prune(root, keep):
    """Delete all but the newest `keep` cached bakes of each level, and of each sublevel
    in SUBLEVEL_CACHE, under `root`. Returns the names deleted."""
    import re
    gone = []
    groups = {}
    for n in os.listdir(root) if os.path.isdir(root) else ():
        m = re.match(r'^(.+)-[0-9a-f]{16}$', n)
        if m and os.path.isdir(os.path.join(root, n)):
            groups.setdefault(m.group(1), []).append(os.path.join(root, n))
    subs = os.path.join(root, SUBLEVEL_CACHE)
    for n in os.listdir(subs) if os.path.isdir(subs) else ():
        m = re.match(r'^(.+_BuiltData)-[0-9a-f]{20}\.inputs\.json$', n)
        if m:
            groups.setdefault('sub:' + m.group(1), []).append(
                os.path.join(subs, n[:-len('.inputs.json')]))
    for paths in groups.values():
        paths.sort(key=lambda p: os.path.getmtime(p if os.path.isdir(p) else p + '.inputs.json'),
                   reverse=True)
        for p in paths[keep:]:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                for ext in ('.uasset', '.uexp', '.ubulk', '.inputs.json'):
                    try:
                        os.remove(p + ext)
                    except OSError:
                        pass
            gone.append(os.path.basename(p))
    return gone


def prepare(level_name, plan, log=print):
    """Bake the level's BAKE_SUBLEVELS for this plan (cached by its inputs) and keep the
    results for copy_built_data. Runs lightbaker in the render venv. Says whether it
    reused a bake (`lightbake: reused ...`) or why it bakes (`lightbake: baking ...`)."""
    _baked.clear()
    if not _bake_now:
        return None
    from riftlord.build.navigation import navpatch
    spec = bake_spec(level_name, plan)
    spec['quality'] = QUALITY
    tags = list(sublevel_tags(level_name, plan))
    if BAKE_PERSISTENT and _has_built_data(navpatch.map_path(level_name)):
        tags.append('persistent')
    if not tags:
        raise SystemExit('lightbake: %s has no lighting data in the game files to bake into'
                         % level_name)
    from riftlord.build.lighting import lightalloc
    if 'persistent' not in tags or not lightalloc.active(level_name, plan):
        spec.pop('receivers', None)
        spec.pop('added_lights', None)
    key = bake_key(level_name, tags, spec)
    out = os.path.join(_bake_cache(), '%s-%s' % (level_name, key))
    report_path = os.path.join(out, 'report.json')
    hit, why = reusable(out)
    if hit:
        log('lightbake: reused the bake of %s (%s), key %s' % (level_name, ', '.join(tags), key))
    else:
        part = '%s.part%d' % (out, os.getpid())
        shutil.rmtree(part, ignore_errors=True)
        os.makedirs(part)
        spec_path = os.path.join(part, 'spec.json')
        # per-sublevel results are reused across plans that leave a sublevel's inputs alone
        spec['sublevel_cache'] = os.path.join(_bake_cache(), SUBLEVEL_CACHE)
        with open(spec_path, 'w') as fh:
            json.dump(spec, fh)
        args = ['bake', level_name, '--out', part, '--spec', spec_path]
        for tag in tags:
            args += ['--tag', tag]
        cmd, kw = baker_command(args)
        log('lightbake: baking %s (%s), key %s (%s) into %s' % (
            level_name, ', '.join(tags), key, why, out))
        env = dict(os.environ)
        from riftlord.core import heavyslot
        # a heavy-job slot (machine-wide, docs/HEAVY-JOBS.md), then the GPU
        from riftlord.core import gpulock
        with heavyslot.slot('light bake %s' % level_name), \
                gpulock.turn('light bake %s' % level_name, log):
            got = subprocess.run(cmd, cwd=gamepath.REPO_ROOT, env=env, timeout=BAKE_TIMEOUT,
                                 stderr=subprocess.PIPE, text=True, errors='replace', **kw)
        if got.stderr:
            print(got.stderr.rstrip())
        if got.returncode != 0 or not os.path.exists(os.path.join(part, 'report.json')):
            shutil.rmtree(part, ignore_errors=True)
            #: the baker's last error line, for the console
            last = [ln for ln in (got.stderr or '').splitlines() if ln.strip()][-1:]
            from riftlord.core import buildmessage
            raise buildmessage.fault(
                'bake the light maps',
                'lightbake: the bake of %s failed (exit %d)%s'
                % (level_name, got.returncode, ': ' + last[0] if last else ''))
        shutil.rmtree(out, ignore_errors=True)
        os.replace(part, out)
    base = navpatch.map_path(level_name)
    for tag in tags:
        level_path = base if tag == 'persistent' else '%s_%s' % (base, tag)
        leaf = level_path.rsplit('/', 1)[-1] + SUFFIX
        blobs = []
        for ext in ('.uasset', '.uexp', '.ubulk'):
            with open(os.path.join(out, leaf + ext), 'rb') as fh:
                blobs.append(fh.read())
        _baked[built_data_path(level_path)] = tuple(blobs)
    with open(report_path) as fh:
        return json.load(fh)


def _has_built_data(level_path):
    from riftlord.core import gamefiles
    stem = '../../../OMD/Content' + built_data_path(level_path)[len('/Game'):]
    return bool(gamefiles.stored(stem + '.uasset'))


def persistent_files(files, level_name, mod, report=None):
    """Point the mission's persistent level copy at a baked copy of the persistent
    level's BuiltData (BAKE_PERSISTENT), adding that copy to `files` (pak entry names).
    Returns the number of packages added (0 when nothing was baked for it)."""
    from riftlord.build.navigation import navpatch
    base = navpatch.map_path(level_name)
    if not _bake_now or built_data_path(base) not in _baked:
        return 0
    key = next((k for k in files if k.endswith('/%s_Map01.umap' % mod)), None)
    if key is None:
        raise SystemExit('lightbake: no persistent level copy for %s in the build' % mod)
    stem = key[:-len('.umap')]
    new_path = '/Game' + stem[len('OMD/Content'):]
    ua, ux, _n = retarget_level(files[key], files[stem + '.uexp'], base, new_path)
    bd_path, blobs, got = copy_built_data(base, new_path)
    if not imports_resolve((ua, ux), blobs, new_path):
        raise SystemExit('%s does not import the built data copied for it' % stem)
    files[key], files[stem + '.uexp'] = ua, ux
    out = 'OMD/Content' + bd_path[len('/Game'):]
    files[out + '.uasset'], files[out + '.uexp'] = blobs[0], blobs[1]
    if blobs[2]:
        files[out + '.ubulk'] = blobs[2]
    if report is not None:
        report['persistent'] = got
    return 1
