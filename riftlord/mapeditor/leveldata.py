"""What Rift Lord reads and writes: a shipped level, and the plan laid over it.

The tool never edits a level in place. It reads one, holds a **plan** -- the
spawners, doors and path points to add, and the waves to give them -- and an
emitter applies that plan to a copy. Keeping the plan separate is what makes the
work reviewable: a file that can be read, diffed and kept, rather than a change
buried in a 3,884-export package.
"""

import functools
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

from riftlord.core import gamepath  # noqa: E402
from riftlord.build.navigation import navinfo  # noqa: E402
from riftlord.build.navigation import navpatch  # noqa: E402
from riftlord.build.navigation import navvol  # noqa: E402
from riftlord.formats.omd3 import omd3actors  # noqa: E402
from riftlord.formats.omd3 import omd3level  # noqa: E402
from riftlord.mapeditor import omd3serve  # noqa: E402
from riftlord.formats.omd3 import omd3sky  # noqa: E402
from riftlord.onslaught import onslaught  # noqa: E402

#: Saved plans: beside the code, or in the user's data folder when frozen, where the
#: code is unpacked into a temporary folder that is thrown away on exit.
#: `RIFTLORD_PLANS` moves them (testservers.py points it into its scratch folder).
PLANS = os.environ.get('RIFTLORD_PLANS') or (
    gamepath.user_data('riftlord-plans') if gamepath.frozen()
    else gamepath.beside_code(gamepath.CODE_ROOT, 'riftlord-plans'))

#: What a spawner can be. A flyer is the one kind that needs no navigation work at
#: all -- it ignores the navmesh -- so it is the default and the honest starting
#: point; a ground spawner's route has to lie on floor the level's own navgraph
#: already covers.
KINDS = ('flyer', 'ground')

#: Names of the effect meshes that are light rather than solid: beams, rays, glow cones,
#: spheres and planes, lightning, portal smoke, swirls.
GLOW_MESH = re.compile(r'(beam|rays|_fx_|lightning|portalsmoke|recall_cylinder|billboardplane'
                       r'|swirl|windbelt|darkness)', re.I)


@functools.lru_cache(maxsize=1)
def levels():
    """The levels that can be opened, with their mission names and the campaign each is
    filed under (riftlord.mapeditor.levelgroups: `campaign`, `order`, `index`), in the
    picker's order."""
    try:
        names = sorted(navpatch.maps())
    except Exception:                                # noqa: BLE001
        return []
    titles = omd3sky.mission_names()
    try:
        from riftlord.build.minimap import mapdata
        by_level = mapdata.mission_names()
    except Exception:                                # noqa: BLE001
        by_level = {}
    from riftlord.mapeditor import levelgroups
    # Titles keyed by each row's MissionLevel, as the Onslaught Editor reads them.
    return levelgroups.file_levels(
        [{'name': n, 'title': by_level.get(n) or omd3sky.title_for(n, titles)}
         for n in names])


@functools.lru_cache(maxsize=8)
def level(name):
    """One shipped level: drawable pieces, spawners, doors, path points.

    The pieces are one entry per placement carrying only a mesh name, so the front
    end fetches a level's distinct meshes -- 106 on Split Stairs, over 1,256
    placements -- and draws the rest as instances.

    Also `player_starts` [{export, location, rotation, phase}] and a `rotation` (yaw,
    degrees) on every spawner and rift (riftlord/PLAN-ACTORS.md).
    """
    got = omd3level.read(name)
    #: glow meshes (light beams, rays, glow cones: meshcat category 'effect' with a
    #: GLOW_MESH name) are drawn as the light they are, not as solid geometry
    try:
        from riftlord.formats.omd3 import meshcat
        cat = meshcat.load()
        for piece in got.get('pieces') or []:
            mesh = piece.get('mesh') or ''
            if (cat.get(mesh) or {}).get('category') == 'effect' and GLOW_MESH.search(mesh):
                piece['glow'] = True
    except Exception:                                # noqa: BLE001
        pass
    #: {material: [r, g, b]} for the materials pieces carry, beside `materials`
    tints = {}
    for mat in got.get('materials') or {}:
        try:
            tints[mat] = material_tint(mat)
        except Exception:                            # noqa: BLE001
            tints[mat] = None
    got['materialTints'] = tints
    try:
        from riftlord.mapeditor import planactors
        got['player_starts'] = planactors.level_player_starts(name)
        yaws = planactors.actor_yaws(name)
        for sp in got.get('spawners') or []:
            sp['rotation'] = yaws.get(sp.get('export'), 0.0)
        for r in got.get('rifts') or []:
            r['rotation'] = yaws.get(r.get('name'), 0.0)
    except Exception:                                # noqa: BLE001
        got.setdefault('player_starts', [])
    # each door's export number: its drawn parts are the persistent pieces with that `export`
    try:
        exports = onslaught.load_map(name)[0]['exports']
        number = {}
        for i, ex in enumerate(exports):
            number.setdefault(str(ex.get('name')), i + 1)
        for door in (got.get('doors') or {}).values():
            door['index'] = number.get(door.get('export'))
    except (Exception, SystemExit):                  # noqa: BLE001
        pass
    #: blocking and kill volumes (riftlord.volumes.level_volumes)
    try:
        from riftlord.mapeditor import volumes
        got['volumes'] = volumes.level_volumes(name)
    except (Exception, SystemExit):                  # noqa: BLE001
        got['volumes'] = []
    #: {'<sub>|<export>': '<sub>|<export>'} teleporter ends and the end each links to
    try:
        got['teleports'] = teleport_pairs(name, got)
    except (Exception, SystemExit):                  # noqa: BLE001
        got['teleports'] = {}
    return got


#: Class of a teleporter end, and the property naming its other end (riftlord.geomedits).
TELEPORT_CLASS = 'BP_OMDTeleportRift_C'


def teleport_pairs(name, got):
    """Each teleporter end's linked end, keyed '<sub>|<export>', within its own package."""
    from riftlord.build import levelbuild
    from riftlord.build.navigation import navpatch
    from riftlord.core import gamepath
    from riftlord.mapeditor import geomedits
    subs = sorted({p.get('sub') or 'persistent' for p in got.get('pieces') or []
                   if p.get('actor') == TELEPORT_CLASS})
    out = {}
    for sub in subs:
        path = (got.get('subPackages') or {}).get(sub)
        if not path:
            continue
        files = navpatch.fetch_map(path, gamepath.scratch('.subcopy', gamepath.CODE_ROOT))
        with open(files['.umap'], 'rb') as fh:
            ua = fh.read()
        with open(files['.uexp'], 'rb') as fh:
            ux = fh.read()
        builder = levelbuild.Builder.around(ua, ux)
        for p in got.get('pieces') or []:
            if p.get('actor') != TELEPORT_CLASS or (p.get('sub') or 'persistent') != sub:
                continue
            partner = geomedits.teleport_partner(builder, p['export'])
            if partner:
                out['%s|%d' % (sub, p['export'])] = '%s|%d' % (sub, partner)
    return out


@functools.lru_cache(maxsize=8)
def onslaughts(name):
    """The level's existing waves, so new ones can be written beside them."""
    try:
        return onslaught.map_report(name)
    except Exception:                                # noqa: BLE001
        return {'map': name, 'spawners': [], 'points': {}, 'doors': {}}


def units():
    """Every AI character proto, with the flags that decide where it can spawn: the
    shipped rows and every installed mod's minion rows (modminions), read again when a
    mod pak changes.

    `bIsFlyer` is what makes a unit usable on a spawner's flyer path. Guessing from
    the name would work today only because every flyer happens to be a Hellbat.
    """
    try:
        from riftlord.missions import modminions
        stamp = tuple(modminions._paks())
    except (Exception, SystemExit):                  # noqa: BLE001
        stamp = ()
    return _units(stamp)


@functools.lru_cache(maxsize=1)
def _units(_stamp):
    try:
        flags = onslaught.proto_flags()
    except Exception:                                # noqa: BLE001
        flags = {}
    try:
        guids = onslaught.protos()
    except Exception:                                # noqa: BLE001
        guids = {}
    try:
        from riftlord.onslaught import makepreview
    except Exception:                                # noqa: BLE001
        makepreview = None
    out = []
    for name in sorted(set(flags) | set(guids)):
        f = flags.get(name) or {}
        # The creature art travels with the unit, so the front end can show what the
        # mission's enemy preview becomes without asking again per edit.
        creature = None
        if makepreview is not None:
            try:
                creature = makepreview.creature_for(name)
            except Exception:                        # noqa: BLE001
                creature = None
        out.append({'name': name,
                    'label': f.get('name') or name,
                    'mod': f.get('mod'),
                    'flyer': bool(f.get('flyer')),
                    'boss': bool(f.get('boss')),
                    'runner': bool(f.get('runner')),
                    'creature': creature,
                    'guid': list(guids.get(name) or [])})
    return out


#: Onslaughts a converted or edited mission must never be written into. Weekly
#: Challenges and the developer coordinator are not ours to touch.
FORBIDDEN_ONSLAUGHTS = ('_WC_', 'WeeklyChallenge', 'Developer')


@functools.lru_cache(maxsize=1)
def _onslaught_assets():
    from riftlord.core import gamefiles
    return [a for a in sorted(gamefiles.find(ext='.uasset'))
            if 'Onslaught' in a.rsplit('/', 1)[-1]
            and not any(bad in a for bad in FORBIDDEN_ONSLAUGHTS)]


@functools.lru_cache(maxsize=16)
def coordinator(name):
    """The onslaught Blueprint that drives one level, or None.

    A level's coordinator is named after it with the underscore dropped --
    `Scenario_05` is driven by `BP_OMDOnslaughtCoordinator_Scenario05` -- and the
    DLC campaigns keep their own with `DLC` in the class name. Matching on the bare
    number would pick up the Sand campaign's fifth mission as well, so the whole
    squashed name has to agree.
    """
    want = name.replace('_', '')
    for a in _onslaught_assets():
        leaf = a.rsplit('/', 1)[-1][:-len('.uasset')]
        if leaf.replace('_', '').endswith(want):
            return a[:-len('.uasset')]
    return None


@functools.lru_cache(maxsize=16)
def existing_units(name):
    """Which units a level's onslaught already sends.

    The proto guids sit in the Blueprint's uexp as raw literals, unaligned, so this
    scans for the bytes rather than parsing the bytecode -- the same route the
    onslaught editor takes.
    """
    import struct
    from riftlord.core import gamefiles
    path = coordinator(name)
    if not path:
        return []
    try:
        _ua, uexp = gamefiles.package(path)
    except Exception:                                # noqa: BLE001
        return []
    blob = uexp or b''
    found = []
    for unit, guid in sorted(onslaught.protos().items()):
        try:
            needle = struct.pack('<4i', *guid)
        except Exception:                            # noqa: BLE001
            continue
        if needle in blob:
            found.append(unit)
    return found


def roster(name):
    """The enemies this mission will advertise once the plan is applied.

    The mission hub shows a diorama of the enemies a level sends, and adding a
    spawner changes that set -- a level with no flyers in it becomes a level with
    flyers, and the preview has to say so or the mission lies about itself.
    `makepreview` builds the diorama from a roster; this works out what the roster
    becomes.
    """
    from riftlord.onslaught import makepreview
    have = existing_units(name)
    plan = load_plan(name)
    added = []
    for w in plan.get('waves') or []:
        unit = w.get('unit')
        if unit and unit not in have and unit not in added:
            added.append(unit)
    out = {'level': name, 'coordinator': coordinator(name),
           'existing': have, 'added': added, 'creatures': [], 'no_art': []}
    for unit in have + added:
        try:
            creature = makepreview.creature_for(unit)
        except Exception:                            # noqa: BLE001
            creature = None
        if creature:
            out['creatures'].append({'unit': unit, 'creature': creature,
                                     'new': unit in added})
        else:
            out['no_art'].append(unit)
    return out


@functools.lru_cache(maxsize=8)
def mesh_names(name):
    """The distinct meshes one level draws, commonest first."""
    import collections
    counts = collections.Counter(p['mesh'] for p in level(name)['pieces'])
    # Commonest first, so the earliest chunk draws the most of the level. On Split
    # Stairs the first dozen meshes account for a third of its 1,256 placements.
    return [n for n, _ in counts.most_common()]


def material_tint(material):
    """[r, g, b] base-colour multiplier of a material named by path or bare name."""
    if not material:
        return None
    path = material
    if not str(material).startswith('/Game/'):
        found = omd3actors._asset_for(material)
        if not found:
            return None
        path = '/Game/' + found.split('OMD/Content/')[-1]
    from riftlord.formats.omd3 import omd3matlook
    return omd3matlook.tint(path)


def one_sided(mesh):
    """True when every material slot of a served mesh draws only its front faces."""
    from riftlord.formats.omd3 import omd3matlook
    slots = [s for s in (mesh.get('slots') or [mesh.get('material')]) if s]
    if not slots:
        return False
    for s in slots:
        path = s
        if not str(s).startswith('/Game/'):
            found = omd3actors._asset_for(s)
            if not found:
                return False
            path = '/Game/' + found.split('OMD/Content/')[-1]
        if omd3matlook.two_sided(path) is not False:
            return False
    return True


def geometry(name, start=0, count=12, names=None):
    """One slice of a level's geometry, read in parallel.

    Served in slices rather than all at once so the level draws as it arrives. The
    front end used to ask for one mesh at a time and wait for each, which made a
    level take as long as the sum of its parts; a slice overlaps its reads and still
    gives the browser something to draw every second or two.

    `names` limits it to those meshes (a level from scratch draws only its sky).
    """
    from riftlord.formats.omd3 import omd3landscape
    names = mesh_names(name) if names is None else names
    chunk = names[start:start + max(1, count)]
    terrain = [n for n in chunk if omd3landscape.is_landscape_mesh(n)]
    got = omd3serve.meshes([n for n in chunk if n not in terrain])
    for n in terrain:
        try:
            m = omd3landscape.mesh(n)
        except Exception:                            # noqa: BLE001
            m = None
        if m:
            got[n] = m
    #: base-colour multipliers of each mesh's own materials
    for m in got.values():
        try:
            m['tint'] = material_tint(m.get('material'))
            m['slotTints'] = [material_tint(s) for s in (m.get('slots') or [])]
            m['oneSided'] = one_sided(m)
        except Exception:                            # noqa: BLE001
            pass
    return {'level': name, 'start': start, 'total': len(names),
            'meshes': got, 'missing': sorted(set(chunk) - set(got))}


@functools.lru_cache(maxsize=1)
def catalogue():
    """Everything that can be added to a level: static meshes and kit pieces.

    Two kinds, and the difference matters. A **mesh** is geometry and nothing else. A
    **kit** piece is a Blueprint: several meshes at fixed offsets, and often an
    `OMDTrapGridBoxComponent`, which is what makes a wall or floor something the
    player can build on. Adding a doorway as loose meshes gives a level a doorway
    nobody can put a trap beside.
    """
    import json as _json
    from riftlord.formats.omd3 import meshcat
    rows = []
    cat = meshcat.load()
    for name, info in sorted(cat.items()):
        #: the shelf by the current rules (meshcat.category_of), not as the catalogue
        #: was last built
        shelf = (meshcat.category_of(info['path'], name) if info.get('path')
                 else info.get('category', 'decoration'))
        rows.append({'name': name, 'kind': 'mesh', 'thumb': name,
                     'category': shelf,
                     'cells': info.get('cells', [0, 0, 0]),
                     'trapgrid': False})
    kit_path = gamepath.data('kit-catalogue.json')
    parts = omd3actors.part_index()
    try:
        with open(kit_path) as fh:
            kit = _json.load(fh)
    except Exception:                                # noqa: BLE001
        kit = {}
    for name, info in sorted(kit.items()):
        cls = name if name.endswith('_C') else name + '_C'
        grids = info.get('trap_grids') or []
        mine = parts.get(cls) or []
        # A kit piece's preview is drawn from all of its parts as its donor placement
        # builds them (riftlord/kitdonors.py, served by /api/thumb/kit:<class>); the
        # widest part's own preview is the fallback without donor parts.
        thumb = ''
        from riftlord.mapeditor import kitdonors
        if kitdonors.parts(cls):
            thumb = kitdonors.THUMB_NAME + cls
        elif mine:
            widest = max(mine, key=lambda pt: _footprint_of(cat, pt['mesh']))
            thumb = widest['mesh']
        rows.append({'name': cls, 'kind': 'kit', **({'label': kit_label(cls)} if kit_label(cls) else {}),
                     'category': 'trapgrid' if grids else 'kit',
                     'cells': (grids[0].get('cells') + [0])[:3] if grids else [0, 0, 0],
                     'trapgrid': bool(grids),
                     'parts': len(mine),
                     'thumb': thumb,
                     'surfaces': sorted({g.get('surface') for g in grids
                                         if g.get('surface')})})
    #: placeable effects (riftlord.lavafx): emitters and effect meshes lifted from the
    #: shipped lava levels
    try:
        from riftlord.build.world import lavafx
        rows += lavafx.catalogue_rows()
    except Exception:                                # noqa: BLE001
        pass
    #: shipped actors beyond the kit: lights, torches, banners, sound, interactables
    try:
        taken = {r['name'] for r in rows}
        rows += [r for r in placeable_actors() if r['name'] not in taken]
    except Exception:                                # noqa: BLE001
        pass
    #: treasure chests and pickups (riftlord/kitactors.py)
    from riftlord.mapeditor import kitactors
    rows += kitactors.catalogue_rows()
    #: compound kits (riftlord/compounds.py)
    from riftlord.mapeditor import compounds
    rows += compounds.catalogue_rows(rows)
    return rows


#: The Blueprint stone walls with wood built in, by class name: Wall_RB_WDMT* carry a banded
#: top beam, Wall_RB_WD_v1 posts and beams (v2 with spiked brackets), v3 and v4 braced timber
#: frames; `Dry` is the desert art, `_v2` the second beam layout, Trim_Column_WDMT the beam
#: collar that rings a column top.
BEAM_WALL = re.compile(r'^Wall_RB_WD(MT)?L?_v(\d)(_Dry)?_(\d+x\d+)(_v2)?_C$')
BEAM_WALL_KIND = {('MT', '1'): 'Stone Wall with Top Beam', ('', '1'): 'Stone Wall with Beams',
                  ('', '2'): 'Stone Wall with Spiked Beams', ('', '3'): 'Braced Timber Wall A',
                  ('', '4'): 'Braced Timber Wall B'}
BEAM_COLLAR = re.compile(r'^Trim_Column_WDMT(_Dry)?_v1_C$')


def kit_label(cls):
    """A plain New Object name for a kit class whose class name says little, or None."""
    m = BEAM_WALL.match(cls)
    if m and (m.group(1) or '', m.group(2)) in BEAM_WALL_KIND:
        extra = ', Dry' if m.group(3) else ''
        return '%s%s (%s%s)' % (BEAM_WALL_KIND[(m.group(1) or '', m.group(2))], extra,
                                m.group(4), ', B' if m.group(5) else '')
    m = BEAM_COLLAR.match(cls)
    if m:
        return 'Column Beam Collar' + (', Dry' if m.group(1) else '')
    return None


#: Shipped actor classes New Object offers beside the kit, as (group, class pattern): each
#: is lifted whole from a level that places it, as a kit piece is (riftlord/apply.py _add).
#: A level's own lighting rig, the sun, coordinators, volumes and characters are left out.
ACTOR_GROUPS = (
    ('Lights', r'^(PointLight|SpotLight|RectLight)$'),
    ('Torches and Braziers',
     r'^(BP_(WallTorch|OrcTorch|Brazier|Chandelier|Lighting_WallCrystal)\w*|Lizard_Brazier)_C$'),
    ('Banners', r'^Banner\w*_BP(_Wind)?_C$'),
    ('Sound', r'^AmbientSound$'),
    #: riftlord/envtraps.py
    ('Environmental Traps', r'^BP_(FallingBoomBarrel|Cauldron|GrinderPit_[34]X|Grinders_3X)_C$'),
    #: rain-level sky lightning (riftlord/controllers.py)
    ('Weather', r'^BP_LightningBolt_RainLevels_C$'),
)
#: The catalogue category of the placeable actors.
ACTOR_CATEGORY = 'actors'
#: A Blueprint component is taken for a light when its name says so.
LIGHT_COMPONENT = re.compile(r'(Light|Point|Spot|Rect)', re.I)


def _actor_lights(cls, info):
    """[{kind, colour, brightness, radius, inverseSquared}] of the lights one placeable
    actor brings: a native light's from its donor placement, a Blueprint's from its class
    defaults (lightscene)."""
    from riftlord.build.lighting import lightscene
    out = []

    def row(kind, p):
        p = dict(lightscene.DEFAULTS, **p)
        return {'kind': kind, 'colour': [round(float(v), 4)
                                         for v in lightscene.linear_colour(p['LightColor'])],
                'brightness': round(float(lightscene._brightness(kind, p)), 3),
                'radius': float(p['AttenuationRadius']),
                'inverseSquared': bool(p['bUseInverseSquaredFalloff']) or kind == 'rect'}
    try:
        if cls.endswith('_C'):
            for name, props in lightscene._class_templates(cls).items():
                if LIGHT_COMPONENT.search(name) and ('Intensity' in props or
                                                     'AttenuationRadius' in props):
                    kind = 'spot' if 'spot' in name.lower() else 'point'
                    out.append(row(kind, props))
        else:
            kind = {'PointLight': 'point', 'SpotLight': 'spot', 'RectLight': 'rect'}.get(cls)
            if not kind:
                return out
            pkg = lightscene.Package(info['level'].replace('../../../OMD/Content', '/Game'))
            for i, e in enumerate(pkg.exports):
                if (e.get('outer') or 0) == info['export'] + 1 and \
                        lightscene.LIGHT_KINDS.get(str(e.get('cls') or '')) == kind:
                    out.append(row(kind, pkg.props(i)))
                    break
    except Exception:                                # noqa: BLE001
        pass
    return out


@functools.lru_cache(maxsize=1)
def placeable_actors():
    """New Object rows for the shipped actors in ACTOR_GROUPS: kind 'kit' (placed as a
    lifted Blueprint or native actor), category ACTOR_CATEGORY, with `group`, `label`,
    `thumb` and the `lights` they bring for the viewport."""
    from riftlord.mapeditor import kitdonors
    known = omd3actors.load()
    rows = []
    for group, pattern in ACTOR_GROUPS:
        rx = re.compile(pattern)
        for cls in sorted(known):
            if not rx.match(cls):
                continue
            parts = omd3actors.part_index().get(cls) or []
            thumb = ''
            if kitdonors.parts(cls):
                thumb = kitdonors.THUMB_NAME + cls
            elif parts:
                thumb = parts[0]['mesh']
            label = re.sub(r'_C$', '', cls)
            label = re.sub(r'^BP_', '', label).replace('_', ' ')
            #: names and previews of the controller actors (riftlord/controllers.py)
            from riftlord.mapeditor import controllers
            label, thumb = controllers.ROW_LABELS.get(cls, label), \
                controllers.ROW_THUMBS.get(cls, thumb)
            rows.append({'name': cls, 'kind': 'kit', 'category': ACTOR_CATEGORY,
                         'group': group, 'label': label, 'thumb': thumb,
                         'cells': [0, 0, 0], 'trapgrid': False, 'parts': len(parts),
                         'surfaces': [], 'lights': _actor_lights(cls, known[cls])})
    return rows


def _footprint_of(cat, mesh_name):
    info = cat.get(mesh_name)
    if not info:
        return 0.0
    size = info.get('size') or [0, 0, 0]
    return float(size[0]) * float(size[1])


@functools.lru_cache(maxsize=256)
def kit_parts(class_name):
    """The meshes and offsets of one kit class, for drawing a piece about to be added:
    as its donor placement builds it (riftlord/kitdonors.py), which is what an added
    piece is lifted from, else the class's component templates. A door is drawn from
    its class templates: the build resets a door's child transforms to the class's
    (apply.reset_component_transforms)."""
    from riftlord.mapeditor import kitdonors
    from riftlord.mapeditor import kitactors
    if kitactors.handles(class_name):
        return {'name': class_name, 'parts': kitactors.parts(class_name), 'grids': []}
    parts = None if class_name in DOOR_CLASSES else kitdonors.parts(class_name)
    if parts is None:
        parts = omd3actors.part_index().get(class_name)
    if parts is None:
        parts = omd3actors.parts_of(class_name)
    return {'name': class_name, 'parts': [p for p in parts or [] if not p.get('hidden')],
            'grids': kit_grids(class_name)}


@functools.lru_cache(maxsize=None)
def _kit_geometry():
    import json as _json
    try:
        with open(gamepath.data('kit-geometry.json')) as fh:
            return _json.load(fh).get('pieces') or {}
    except (OSError, ValueError):
        return {}


def kit_grids(class_name):
    """A kit class's trap grids in actor space: [{min, max, normal, surface}].

    `min`/`max` bound the grid box; `normal` is the box's local +X turned into actor
    space, the side traps go on; `surface` is Floor, Ceiling or Wall by that normal.
    """
    name = class_name[:-2] if class_name.endswith('_C') else class_name
    out = []
    for g in (_kit_geometry().get(name) or {}).get('grids') or []:
        m = omd3level._matrix((0.0, 0.0, 0.0), g.get('rotation') or (0, 0, 0), (1, 1, 1))
        normal = [round(m[r][0], 6) for r in range(3)]
        surface = ('Floor' if normal[2] > KIT_FACE_VERTICAL else
                   'Ceiling' if normal[2] < -KIT_FACE_VERTICAL else 'Wall')
        #: the grid box in actor space: its centre, local axes (X is the normal, Y and
        #: Z run along the cells) and half-extent
        axes = [[round(m[r][c], 6) for r in range(3)] for c in range(3)]
        out.append({'min': g['min'], 'max': g['max'], 'normal': normal,
                    'surface': surface, 'origin': g.get('offset'), 'axes': axes,
                    'extent': g.get('extent')})
    return out


#: A grid whose normal's z is above this faces up (Floor) or down (Ceiling).
KIT_FACE_VERTICAL = 0.7


def region_report(name, box):
    """What falls inside a region of a level, and what a mission there still needs.

    A War Scenario cannot be cut along its routes. All seven have exactly **one**
    exit point and **one** connected route graph -- every spawner in Scenario_18's
    fourteen feeds the same rift -- so a sub-area is not a unit the level already
    has. It has to be made into one.

    What makes a region a mission: a rift of its own, a player start, at least one
    spawner, and routes that end at that rift rather than leading out of the region.
    Everything outside is parked below the map rather than deleted, because removing
    an export renumbers every reference to it.
    """
    lo, hi = box[0], box[1]

    def inside(at):
        return at and all(lo[i] <= at[i] <= hi[i] for i in range(3))

    lv = level(name)
    wiring = onslaughts(name)
    points = wiring['points']
    byidx = {}
    for p in points.values():
        if p['index'] is not None:
            byidx.setdefault(p['index'], p)

    kept_points, outside_points = [], []
    for p in points.values():
        (kept_points if inside(p.get('location')) else outside_points).append(p)
    kept_names = {p['export'] for p in kept_points}

    # A route that leaves the region has to be re-pointed at the new rift, or the
    # wave walks out of the level.
    leaving = []
    for p in kept_points:
        for b in p.get('next') or []:
            to = byidx.get(b['PointIndex'])
            if to and to['export'] not in kept_names:
                leaving.append({'from': p['export'], 'to': to['export']})

    kept_spawners = [sp for sp in wiring['spawners'] if inside(sp.get('location'))]
    kept_doors = [d['export'] for d in wiring['doors'].values()
                  if inside(d.get('location'))]
    exits_inside = [p['export'] for p in kept_points if p['exit']]
    pieces_inside = sum(1 for piece in lv['pieces']
                        if inside([piece['matrix'][12], piece['matrix'][13],
                                   piece['matrix'][14]]))

    plan = load_plan(name)
    planned_rifts = [r for r in plan.get('rifts') or [] if inside(r.get('at'))]
    needs = []
    if not exits_inside and not planned_rifts:
        needs.append('no rift inside: place one, or move the original rift in, '
                     'or the wave has nowhere to walk to')
    if not kept_spawners:
        needs.append('no spawner inside: keep one or add one')
    if leaving:
        needs.append('%d route branch(es) lead out of the region and must be '
                     're-pointed at its rift' % len(leaving))
    if pieces_inside < 50:
        needs.append('only %d piece(s) of geometry inside: the region may be too '
                     'small or in the wrong place' % pieces_inside)
    return {
        'level': name,
        'box': [list(lo), list(hi)],
        'pieces_inside': pieces_inside,
        'pieces_total': len(lv['pieces']),
        'spawners_inside': [sp['index'] for sp in kept_spawners],
        'spawners_outside': [sp['index'] for sp in wiring['spawners']
                             if sp not in kept_spawners],
        'doors_inside': kept_doors,
        'points_inside': sorted(p['export'] for p in kept_points),
        'points_outside': len(outside_points),
        'exits_inside': exits_inside,
        'rifts_planned': [r['id'] for r in planned_rifts],
        'leaving': leaving,
        'needs': needs,
    }


@functools.lru_cache(maxsize=8)
def coverage(name):
    """Where the level's navigation data actually reaches.

    Two very different kinds of answer, and the UI must not blur them:

    * **Flight** is exact. The volumetric graph is decoded, so each convex volume's
      box and each portal joining two of them is a real boundary -- draw it as
      geometry.
    * **Surface** is a bounding box and nothing more. The walkable graph's record
      layout is not cracked, so all that can be stated honestly is the world box the
      graph covers. Draw it as a bound, never as a floor, or it will imply precision
      we do not have.

    The flyer-point check is the part that earns its keep while authoring: a flyer
    path point outside every flight volume is a route into airspace the graph does
    not cover, which is exactly the failure that cost this project weeks.
    """
    out = {'map': name, 'surface': None, 'flight': None,
           'flyerPoints': [], 'groundPoints': [], 'warnings': []}
    tmp = os.path.join(PLANS, '.coverage')
    os.makedirs(tmp, exist_ok=True)

    try:
        d = navinfo.omd3_map(name, tmp)
    except (Exception, SystemExit) as exc:           # noqa: BLE001
        d = None
        out['warnings'].append('could not read the surface graph: %s' % exc)
    if d:
        out['surface'] = {'bytes': d['bytes'],
                          'min': list(d['min']), 'max': list(d['max']),
                          'exact': False, 'hashOk': None}
        # The checksum is the one thing the game actually enforces: a buffer whose
        # +12 word does not match its payload is refused, and the symptom in play is
        # that every agent using it simply never moves. Worth stating here, because
        # nothing else about an emitted graph will tell you.
        try:
            files = navpatch.fetch_map(navpatch.map_path(name), tmp)
            b = navpatch.nav_buffers(files['.umap'], files['.uexp'])
            if b:
                raw = open(files['.uexp'], 'rb').read()
                out['surface']['hashOk'] = navvol.verify(raw[b[1]:b[1] + b[2]])
        except Exception as exc:                     # noqa: BLE001
            out['warnings'].append('could not check the surface checksum: %s' % exc)
        if out['surface']['hashOk'] is False:
            out['warnings'].append(
                'the surface graph checksum does NOT match its payload -- the game '
                'will refuse this buffer and ground units will not move')

    try:
        v = navvol.for_map(name, tmp)
    except (Exception, SystemExit) as exc:           # noqa: BLE001
        v = None
        out['warnings'].append('could not read the flight graph: %s' % exc)

    boxes = []
    if v:
        for vol in v['volumes']:
            if not vol['box']:
                continue
            boxes.append(vol['box'])
            
        out['flight'] = {
            'bytes': v['bytes'], 'hash': v['hash'], 'hashOk': v['hash_ok'],
            'min': list(v['bbox'][:3]), 'max': list(v['bbox'][3:]),
            'declared': v['nvolumes'], 'decoded': len(v['volumes']),
            'complete': v['complete'], 'edges': v['count'],
            'volumes': [{'min': list(vol['box'][0]), 'max': list(vol['box'][1]),
                         'degree': vol['flags']}
                        for vol in v['volumes'] if vol['box']],
            'portals': [{'verts': [list(p) for p in po['verts']]}
                        for po in v['portals']],
        }
        if not v['hash_ok']:
            out['warnings'].append(
                'the flight graph checksum does NOT match its payload -- the game '
                'will refuse this buffer and flyers will not move')
        if not v['complete']:
            out['warnings'].append(
                'the flight graph declares %d volumes and only %d decode, so the '
                'drawing is incomplete -- absence of a volume here is not evidence'
                % (v['nvolumes'], len(v['volumes'])))
        if len(v['portals']) != v['count']:
            out['warnings'].append(
                'read %d portals but the graph declares %d edges, so some portals '
                'shown may be misread floats' % (len(v['portals']), v['count']))

    def inside_any(at):
        for lo, hi in boxes:
            if all(lo[i] - 1 <= at[i] <= hi[i] + 1 for i in range(3)):
                return True
        return False

    wiring = onslaughts(name)
    for p in (wiring.get('points') or {}).values():
        if not p.get('flyersOnly') or not p.get('location'):
            continue
        at = list(p['location'])
        ok = inside_any(at) if boxes else False
        out['flyerPoints'].append(
            {'export': p['export'], 'location': at, 'inside': bool(ok)})

    if out['flyerPoints'] and not v:
        out['warnings'].append(
            'this level has flyer path points and NO volumetric graph, so flyers '
            'cannot travel here -- adding NavPowerFlightVolume actors alone does '
            'not create one')
    stray = [p['export'] for p in out['flyerPoints'] if not p['inside']]
    if stray and boxes:
        out['warnings'].append(
            '%d flyer point(s) lie outside every decoded flight volume: %s'
            % (len(stray), ', '.join(sorted(stray)[:6])))

    # GROUND coverage, which is the other half of the question.
    #
    # The walkable graph's record layout is not cracked, so there is no floor to draw
    # and no finer answer than its world box -- the section table gives two offsets
    # and a size, not geometry. But the box still answers the one thing that decides
    # whether a land unit moves at all, and the overlay was silent about it: it
    # reported only flyers, so a ground point authored outside the graph showed
    # nothing until the build refused it.
    sbox = None
    if out['surface']:
        sbox = (out['surface']['min'], out['surface']['max'])

    def in_surface(at):
        if not sbox:
            return False
        return all(sbox[0][i] - 1 <= at[i] <= sbox[1][i] + 1 for i in range(3))

    for p in (wiring.get('points') or {}).values():
        if p.get('flyersOnly') or not p.get('location'):
            continue
        at = list(p['location'])
        out['groundPoints'].append(
            {'export': p['export'], 'location': at, 'inside': in_surface(at)})
    for sp in (wiring.get('spawners') or []):
        if not sp.get('location') or sp.get('flyer'):
            continue
        out['groundPoints'].append(
            {'export': sp.get('export'), 'location': list(sp['location']),
             'inside': in_surface(list(sp['location'])), 'spawner': True})

    #: Only actors Rift Lord added: shipped levels place some of their own just outside
    #: the box (Scenario_07 OMDPathPoint8, Scenario_13 MobSpawnerDoor1) and play.
    off = [p['export'] for p in out['groundPoints']
           if not p['inside'] and str(p['export']).startswith('RiftLord')]
    if off:
        out['warnings'].append(
            '%d added ground actor(s) lie outside the walkable graph\'s bounds: %s. '
            'Saving extends the floor to them where it can'
            % (len(off), ', '.join(sorted(str(x) for x in off)[:6])))
    return out


#: Words that name a liquid material, for the override picker.
LIQUID_MATERIAL_WORDS = ('lava', 'water', 'arcane', 'acid', 'slime', 'ooze',
                         'goop', 'magma')

#: Texture roles, which are not materials and must not be offered as ones.
_TEXTURE_SUFFIXES = ('_BC', '_EM', '_NM', '_MRA', '_MRH', '_MK', '_df', '_diff',
                     '_N', '_SPEC', '_RGB', '_MASTER', '_master')


@functools.lru_cache(maxsize=1)
def materials():
    """Every material instance in the game, by asset name: what a piece can be painted with
    (`look.paints`). Textures and master materials are left out."""
    from riftlord.core import gamefiles
    out = set()
    for key in gamefiles.index():
        if not key.endswith('.uasset') or '/Mat' not in key:
            continue
        leaf = key.rsplit('/', 1)[-1][:-len('.uasset')]
        if leaf.endswith(_TEXTURE_SUFFIXES) or 'inst' not in leaf.lower():
            continue
        out.add(leaf)
    return sorted(out)


@functools.lru_cache(maxsize=1)
def looks():
    """{sky: [...], liquid: [...]} -- the materials a plan may override with.

    Only material INSTANCES, never textures: a material folder holds `Lava_01_INST`
    beside `Lava_01_EM` and `Lava_01_NM`, and offering those would let an author pick
    a normal map as their water.
    """
    from riftlord.core import gamefiles
    sky, liquid = set(), set()
    for key in gamefiles.index():
        if not key.endswith('.uasset') or '/Mat/' not in key:
            continue
        leaf = key.rsplit('/', 1)[-1][:-len('.uasset')]
        if leaf.endswith(_TEXTURE_SUFFIXES):
            continue
        if 'inst' not in leaf.lower():
            continue
        low = leaf.lower()
        if 'sky' in low:
            sky.add(leaf)
        if any(w in low for w in LIQUID_MATERIAL_WORDS):
            liquid.add(leaf)
    return {'sky': sorted(sky), 'liquid': sorted(liquid)}


@functools.lru_cache(maxsize=256)
def look_texture(material):
    """The base-colour texture of one override material, for the viewport."""
    try:
        from riftlord.mapeditor import omd3serve
        return omd3serve.material_texture(material)
    except Exception:                                # noqa: BLE001
        return None


def plan_path(name):
    return os.path.join(PLANS, '%s.json' % omd3serve.safe_name(name))


def empty_plan(name):
    return {'level': name, 'region': None, 'look': {}, 'rifts': [], 'doors': [],
            'spawners': [], 'wiring': [], 'removed': [], 'waves': [], 'edits': [],
            'added': []}


def load_plan(name):
    path = plan_path(name)
    if not os.path.exists(path):
        return empty_plan(name)
    try:
        with open(path) as fh:
            got = json.load(fh)
    except Exception:                                # noqa: BLE001
        return empty_plan(name)
    for key in ('rifts', 'doors', 'spawners', 'wiring', 'removed', 'waves',
                'edits', 'added'):
        got.setdefault(key, [])
    got.setdefault('region', None)
    #: {sky, liquid} -- material instances this plan paints over the level's own.
    got.setdefault('look', {})
    got.setdefault('mission', {})
    #: added pieces drawn as the game builds them (apply.unsheared)
    from riftlord.build import apply as _apply
    for item in got['added']:
        if isinstance(item, dict) and item.get('kind') in ('kit', 'mesh') and \
                isinstance(item.get('matrix'), list) and len(item['matrix']) == 16:
            item['matrix'] = _apply.unsheared(item['matrix'])
    from riftlord.mapeditor import planactors
    return planactors.migrate(got)


def _vec(value, fallback=(0.0, 0.0, 0.0)):
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return list(fallback)
    return [round(float(v), 2) for v in value[:3]]


#: What each authored object actually carries, measured from the shipped levels rather
#: than guessed: field -> (kind, default). These are the properties the game reads, so
#: the editor offers exactly these and the emitter writes exactly these.
#:
#: Values seen in the shipped data: `ReachRadius` 150-600 (300 and 500 commonest),
#: `FormationSpacing` 0 or 3.5-7, `FormationSize` almost always 3, `FlightHeight` 0 or
#: 1500, `maxSpawnPerRow` 4-7.
SCHEMA = {
    'rift': {
        'rotation': ('float', 0.0),
    },
    'spawner': {
        'rotation': ('float', 0.0),
        'kind': ('choice', 'flyer'),
        'index': ('int', 0),
        'maxSpawnPerRow': ('int', 0),
        #: an unstable rift (planactors.UNSTABLE_CLASS), opened by Mission Events
        'unstable': ('bool', False),
    },
    'door': {
        'rotation': ('float', 0.0),
        'cls': ('text', 'BP_Door_Interior_3W_C'),
        'health': ('float', 0.0),
        'slotSpacing': ('float', 0.0),
        #: A force field door is sized by its DoorMesh component's RelativeScale3D,
        #: measured across every shipped level: (8, 13, 1) and (10, 26, 1) in
        #: ScenarioDLC_01. The ACTOR carries no size property at all, which is why
        #: no field on the door itself could ever have worked. Three independent
        #: numbers, because the two axes differ per door and z is authorable too.
        #: 1 for a door in general. A force field gets FORCEFIELD_SCALE applied when
        #: it is placed instead: its mesh is a 100uu plane, so a scale of 1 is a
        #: 100x100uu speck, but a portcullis is already the right size and scaling it
        #: to a force field's proportions stretches it grotesquely. Defaulting this
        #: for every door did exactly that.
        'scaleX': ('float', 1.0),
        'scaleY': ('float', 1.0),
        'scaleZ': ('float', 1.0),
    },
    'point': {
        'reachRadius': ('float', 300.0),
        'formationSize': ('int', 3),
        'formationSpacing': ('float', 0.0),
        'flightHeight': ('float', 0.0),
        'flyersOnly': ('bool', False),
        'groundOnly': ('bool', False),
        'isStart': ('bool', False),
        'isExit': ('bool', False),
    },
}

#: The door classes a level can be given. Interior doors carry `Health` and
#: `SlotSpacing`; a force field door carries neither, so those fields are hidden for it.
DOOR_CLASSES = ('BP_Door_Interior_3W_C', 'BP_Door_Exterior_2W_C',
                'BP_Door_Exterior_3W_C', 'BP_Door_Forcefield_C')

#: Fields that only apply to some classes, so the panel can leave them out rather than
#: What a newly placed FORCE FIELD is sized to: the median of the 25 shipped ones
#: (x 5..25, y 8..75, z always 1). Only the force field, because only its mesh is a
#: bare plane that means nothing at scale 1.
FORCEFIELD_SCALE = (10.0, 25.0, 1.0)

#: offer a setting the game will ignore.
#: A SIZE IS OFFERED FOR EVERY DOOR CLASS.
#:
#: It used to be the force field's alone, because the force field is the only one whose
#: mesh means nothing at scale 1 and the only one with a sensible default other than 1.
#: Being resizable is a different question from needing a default: a portcullis in an
#: archway it does not quite fill is the ordinary reason to want this. The default stays
#: 1 for everything but the force field, which is what stopped a placed portcullis
#: being stretched to a force field's proportions.
ONLY_FOR = {
    'health': ('BP_Door_Interior_3W_C',),
    'slotSpacing': ('BP_Door_Interior_3W_C',),
}

#: A path point's outgoing links. `FOMDPathBranch` is (PointIndex, BranchWeight,
#: BranchLength): the weight is a **probability** and the weights of one point's
#: branches sum to 1.0 at every fork in every shipped level, while the length is simply
#: the distance to the next point -- 1,064 to 7,688 across the campaign -- so it is
#: computed rather than typed.
BRANCH_FIELDS = ('to', 'weight')


def _typed(kind, value, default):
    """One property coerced to what the game expects, or the default."""
    try:
        if kind == 'int':
            return int(value)
        if kind == 'float':
            return round(float(value), 3)
        if kind == 'bool':
            return bool(value)
        if kind == 'choice':
            return str(value) if str(value) in KINDS else default
        return str(value)
    except Exception:                                # noqa: BLE001
        return default


def _object(raw, shape, next_id):
    """One authored object: its place, and the properties its class actually has."""
    out = {'id': int(raw.get('id') or next_id), 'at': _vec(raw.get('at'))}
    for field, (kind, default) in SCHEMA[shape].items():
        out[field] = _typed(kind, raw.get(field, default), default)
    if shape == 'point':
        out['next'] = _branches(raw.get('next'))
    #: a rift's own exit point, an added point's id (riftlord/PLAN-ACTORS.md)
    if shape == 'rift' and raw.get('exit') not in (None, '') and \
            str(raw['exit']).lstrip('-').isdigit():
        out['exit'] = int(raw['exit'])
    return out


def _branches(raw):
    """A point's outgoing branches, cleaned.

    `to` is one of three things: another PLANNED point's id (an int), the export name of a
    point the LEVEL already has (a string, e.g. `OMDPathPoint3`), or the string 'rift'.
    Naming an existing point is what lets an added route join the level's own -- a level
    has a single `OMDAIPath` and one exit point, so a new route almost always has to hand
    off to something already there.

    The weights are left as given rather than normalised, because a set that does not sum
    to 1.0 is worth showing to the author rather than silently changing under them;
    `branch_warnings` reports it.
    """
    out = []
    for b in raw or []:
        if not isinstance(b, dict) or b.get('to') in (None, ''):
            continue
        to = b['to']
        if str(to) == 'rift':
            to = 'rift'
        elif isinstance(to, str) and not to.lstrip('-').isdigit():
            to = str(to)
        else:
            to = int(to)
        out.append({'to': to, 'weight': round(float(b.get('weight', 1.0)), 3)})
    return out


def branch_warnings(plan, level_points=None):
    """Where a route's branch weights do not add up, or lead nowhere.

    `level_points` is the export names the level already has, so a branch onto one of them
    reads as valid rather than as a dangling reference.
    """
    ids = {pt['id'] for sp in plan.get('spawners', []) for pt in sp['points']}
    if level_points is None:
        try:
            level_points = set(level(plan.get('level') or '')['points'])
        except Exception:                            # noqa: BLE001
            level_points = set()
    out = []
    for sp in plan.get('spawners', []):
        for pt in sp['points']:
            branches = pt.get('next') or []
            if not branches:
                continue
            total = sum(b['weight'] for b in branches)
            if abs(total - 1.0) > 0.01:
                out.append('point %d: branch weights add up to %.2f, not 1.00'
                           % (pt['id'], total))
            for b in branches:
                to = b['to']
                if to == 'rift' or to in ids or to in level_points:
                    continue
                out.append('point %d: branch leads to %s, which is neither in the plan '
                           'nor in the level' % (pt['id'], to))
    return out


#: An `actorMoves` or `removedFx` key: '<sublevel tag>|<actor export, 1-based>'.
ACTOR_KEY = re.compile(r'^[^|]+\|[1-9][0-9]*$')
#: Farthest a level light or effect may be moved, in uu along any axis.
ACTOR_MOVE_MAX = 1.0e6


def clean_actor_moves(raw):
    """The plan's `actorMoves`, {'<tag>|<actor export>': [dx, dy, dz]}: the level's own
    lights and effects moved by the Map Editor, by a world-space offset. {} for none."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for key, delta in raw.items():
        if not ACTOR_KEY.match(str(key)) or not isinstance(delta, (list, tuple)) \
                or len(delta) != 3:
            continue
        try:
            got = [round(float(v), 3) for v in delta]
        except (TypeError, ValueError):
            continue
        if all(abs(v) <= ACTOR_MOVE_MAX for v in got) and any(got):
            out[str(key)] = got
    return out


def actor_moves(plan):
    """{sublevel tag: [(actor export, matrix, shipped matrix)]} for `actorMoves`, in the
    rows `riftlord.geomedits.apply_edits` moves: the actor's root takes the offset."""
    out = {}
    for key, (dx, dy, dz) in clean_actor_moves((plan or {}).get('actorMoves')).items():
        tag, _bar, export = key.rpartition('|')
        shift = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, dx, dy, dz, 1.0]
        still = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
        out.setdefault(tag, []).append((int(export), shift, still))
    return out


@functools.lru_cache(maxsize=4)
def light_rig(name):
    """The level's point, spot and rect lights as the Map Editor previews them, read with
    their Blueprint class defaults (lightscene): [{key ('<tag>|<actor export>'), kind,
    position, direction, colour (linear rgb), brightness (UE's units: candelas x 10^4 for
    an inverse-squared light), radius, inverseSquared, falloff, outer, inner, mobility}]."""
    from riftlord.build.lighting import lightscene
    out = []
    for path in lightscene.packages(name):
        try:
            pkg = lightscene.Package(path)
        except Exception:                            # noqa: BLE001
            continue
        tag = lightscene.tag_of(name, path)
        for i, e in enumerate(pkg.exports):
            kind = lightscene.LIGHT_KINDS.get(str(e.get('cls') or ''))
            if not kind:
                continue
            try:
                p = dict(lightscene.DEFAULTS)
                p.update(pkg.props(i))
                m = pkg.world(i)
                outer = int(e.get('outer') or 0)
                out.append({
                    'key': '%s|%d' % (tag, outer) if outer else None, 'kind': kind,
                    'position': [round(float(v), 2) for v in m[:3, 3]],
                    'direction': [round(float(v), 4) for v in lightscene._axis(m, 0)],
                    'colour': [round(float(v), 4) for v in lightscene.linear_colour(p['LightColor'])],
                    'brightness': round(float(lightscene._brightness(kind, p)), 3),
                    'radius': float(p['AttenuationRadius']),
                    'inverseSquared': bool(p['bUseInverseSquaredFalloff']) or kind == 'rect',
                    'falloff': float(p['LightFalloffExponent']),
                    'outer': float(p['OuterConeAngle']), 'inner': float(p['InnerConeAngle']),
                    'mobility': str(p.get('Mobility') or '')})
            except Exception:                        # noqa: BLE001
                continue
    return out


def _slot_paint(value, every):
    """A painted material, or {slot: material}, checked against the known materials;
    None when it names none."""
    if isinstance(value, dict):
        kept = {}
        for slot, want in value.items():
            if want in (None, ''):
                continue
            if not str(slot).isdigit():
                raise ValueError('a material slot must be a number: %r' % (slot,))
            if str(want) not in every:
                raise ValueError('no such material: %r' % (want,))
            kept[str(int(slot))] = str(want)
        return kept or None
    if value in (None, ''):
        return None
    if str(value) not in every:
        raise ValueError('no such material: %r' % (value,))
    return str(value)


def clean_added_paints(raw, every):
    """An added item's `paints`, {part: material or {slot: material}} (README "Painted
    materials"); None for none."""
    if not isinstance(raw, dict):
        return None
    out = {}
    for part, value in raw.items():
        got = _slot_paint(value, every)
        if got is not None:
            out[str(part)] = got
    return out or None


#: What a plan keeps about the mission it builds (save_plan).
MISSION_FIELDS = ('title', 'author', 'version', 'description', 'placement',
                  'campaignName', 'campaignDescription', 'endless', 'resetDoors', 'hub',
                  'campaignHub', 'endlessOnly')


def save_plan(name, data):
    """Store the plan for one level. Written whole, and checked on the way in.

    Checked here rather than trusted: the next thing to read this file is the emitter
    that writes into a level package, and a plan naming a unit or a door class the game
    does not have is a level that crashes rather than one that is merely wrong.
    """
    if not isinstance(data, dict):
        raise ValueError('expected an object')
    known_units = {u['name'] for u in units()}

    rifts = []
    for r in data.get('rifts') or []:
        if isinstance(r, dict):
            rifts.append(_object(r, 'rift', len(rifts) + 1))

    doors = []
    for d in data.get('doors') or []:
        if not isinstance(d, dict):
            continue
        got = _object(d, 'door', len(doors) + 1)
        if got['cls'] not in DOOR_CLASSES:
            raise ValueError('no such door class: %r' % got['cls'])
        #: a door placed with its archway keeps the group (riftlord/compounds.py)
        from riftlord.mapeditor import compounds as _compounds
        doors.append(_compounds.keep_door_group(d, got))

    #: points, spawners, player starts and level edits: riftlord/PLAN-ACTORS.md
    from riftlord.mapeditor import planactors
    actors = planactors.clean(data)
    spawners = actors['spawners']

    waves = []
    for w in data.get('waves') or []:
        if not isinstance(w, dict):
            continue
        unit = str(w.get('unit') or '')
        if unit and unit not in known_units:
            raise ValueError('no such unit: %r' % unit)
        waves.append({'spawner': int(w.get('spawner') or 0),
                      'wave': int(w.get('wave') or 1), 'unit': unit,
                      'count': int(w.get('count') or 1),
                      'delay': round(float(w.get('delay') or 0.0), 2)})

    edits = []
    for e in data.get('edits') or []:
        if not isinstance(e, dict) or 'id' not in e:
            continue
        matrix = e.get('matrix')
        if not isinstance(matrix, list) or len(matrix) != 16:
            continue
        edits.append({'id': int(e['id']), 'mesh': str(e.get('mesh') or ''),
                      'matrix': [round(float(v), 4) for v in matrix]})

    added = []
    #: (items cleaned so far, raw item): group fields copied after (compounds.keep_groups)
    group_marks = []
    for a in data.get('added') or []:
        if not isinstance(a, dict):
            continue
        matrix = a.get('matrix')
        if not isinstance(matrix, list) or len(matrix) != 16:
            continue
        group_marks.append((len(added), a))
        if a.get('kind') == 'foliage':
            #: one added foliage instance (riftlord/foliageadd.py)
            from riftlord.mapeditor import foliageadd
            added.append(foliageadd.clean_piece(a, [round(float(v), 4) for v in matrix]))
            continue
        if a.get('kind') == 'fab':
            #: a Fab library piece (riftlord/fabemit.py)
            from riftlord.mapeditor import fabemit
            added.append(fabemit.clean_piece(a, [round(float(v), 4) for v in matrix]))
            continue
        if a.get('kind') == 'fx':
            #: a placed effect (riftlord/lavafx.py): its kind and effect must survive a
            #: save, or the build reads it as a mesh named after the effect
            from riftlord.build.world import lavafx
            if a.get('fx') not in lavafx.DONORS:
                raise ValueError('no such effect: %r' % (a.get('fx'),))
            added.append({'name': str(a.get('name') or a['fx']), 'kind': 'fx',
                          'fx': str(a['fx']),
                          'matrix': [round(float(v), 6) for v in matrix]})
            continue
        if a.get('kind') == 'events':
            #: the Mission Events piece (riftlord/missionevents.py), one per level
            from riftlord.missions import missionevents
            if not any(x.get('kind') == 'events' for x in added):
                added.append(missionevents.clean_piece(a, [round(float(v), 4)
                                                           for v in matrix]))
            continue
        if a.get('kind') == 'script':
            #: a mission's own script (riftlord/missions/missionscript.py): its source and
            #: settings must survive a save, or the build reads it as a mesh
            from riftlord.missions import missionscript
            added.append(missionscript.clean_piece(a, [round(float(v), 4) for v in matrix]))
            continue
        if a.get('kind') == 'crystal':
            #: the Crystal Defense crystal (riftlord/mapeditor/crystal.py), one per mission
            from riftlord.mapeditor import crystal
            if not any(x.get('kind') == 'crystal' for x in added):
                added.append(crystal.clean_piece(a, [round(float(v), 4) for v in matrix]))
            continue
        #: stored as the game builds it: a sheared matrix is squared up (apply.unsheared)
        from riftlord.build import apply as _apply
        item = {'name': str(a.get('name') or ''),
                'kind': 'kit' if a.get('kind') == 'kit' else 'mesh',
                'matrix': [round(float(v), 4)
                           for v in _apply.unsheared([float(v) for v in matrix])]}
        #: a teleporter: its id and the id of the teleporter it sends players to
        #: (apply.link_teleporters)
        if item['kind'] == 'kit' and item['name'] == TELEPORT_CLASS and \
                isinstance(a.get('tid'), int):
            item['tid'] = int(a['tid'])
            item['link'] = int(a['link']) if isinstance(a.get('link'), int) else None
        #: painted materials per part and slot (README "Painted materials")
        paints = clean_added_paints(a.get('paints'), set(materials())) \
            if a.get('paints') else None
        if paints:
            item['paints'] = paints
        #: an actor's own settings: cue, wall plate, lightning, pour (riftlord/controllers.py)
        if item['kind'] == 'kit':
            from riftlord.mapeditor import controllers
            controllers.clean_item(item, a)
        added.append(item)
    #: compound and tool groups (riftlord/compounds.py)
    from riftlord.mapeditor import compounds as _compounds
    _compounds.keep_groups(added, group_marks, len(added))

    region = None
    raw_region = data.get('region')
    if isinstance(raw_region, (list, tuple)) and len(raw_region) == 2:
        try:
            a, b = _vec(raw_region[0]), _vec(raw_region[1])
            region = [[min(a[i], b[i]) for i in range(3)],
                      [max(a[i], b[i]) for i in range(3)]]
        except Exception:                            # noqa: BLE001
            region = None

    # Pieces of the level to take out. Carving a smaller mission out of a War
    # Scenario means the unused parts are gone, not merely fenced off, so removal is
    # a first-class edit rather than something a region implies.
    removed = sorted({int(v) for v in (data.get('removed') or [])
                      if isinstance(v, (int, float))})
    #: the level's own effects to take out, '<sublevel>|<actor export>' (apply.removed_fx)
    removed_fx = sorted({str(v) for v in (data.get('removedFx') or [])
                         if isinstance(v, str) and '|' in v
                         and v.rpartition('|')[2].isdigit()})
    # Moves and removals applied to the level's OWN gameplay actors, named by their
    # export. Carving a smaller mission out of a War Scenario means re-using what is
    # already wired up, not only adding beside it.
    wiring = actors['wiring']
    #: THE LOOK OVERRIDE, WHICH THIS DROPPED.
    #:
    #: `out` never carried `look`, so the chosen sky and pool materials lived only as
    #: long as the page stayed open. That is worse than it sounds: the GUI saves the
    #: plan and then builds from the FILE, so a repaint chosen in the pickers never
    #: reached a packaged pak at all -- and nothing said so, because `load_plan`
    #: defaults the key to {} and a plan with no look is a legitimate plan.
    #:
    #: Checked against the real material lists, like the door class above: a plan
    #: naming a material the game does not have is an import pointing at nothing.
    look = {}
    if isinstance(data.get('look'), dict):
        known = looks()
        for key in ('sky', 'liquid'):
            want = data['look'].get(key)
            if want in (None, ''):
                continue
            if str(want) not in (known.get(key) or []):
                raise ValueError('no such %s material: %r' % (key, want))
            look[key] = str(want)
        #: {liquid piece id: material} -- one pool plane painted on its own
        planes = data['look'].get('planes')
        if isinstance(planes, dict) and planes:
            pieces = level(name)['pieces']
            kept = {}
            for key, want in planes.items():
                if want in (None, ''):
                    continue
                if str(want) not in (known.get('liquid') or []):
                    raise ValueError('no such liquid material: %r' % (want,))
                try:
                    piece = pieces[int(key)]
                except (ValueError, IndexError):
                    raise ValueError('no liquid plane %r in %s' % (key, name))
                if not piece.get('liquid'):
                    raise ValueError('piece %r is not a liquid plane' % (key,))
                kept[str(int(key))] = str(want)
            if kept:
                look['planes'] = kept
        #: {piece id: material} -- a level piece painted by the designer (riftlord/lavafx.py)
        paints = data['look'].get('paints')
        if isinstance(paints, dict) and paints:
            every = set(materials())
            kept = {}
            for key, want in paints.items():
                #: a material for the whole piece, or {slot: material}
                got = _slot_paint(want, every)
                if got is not None:
                    kept[str(int(key))] = got
            if kept:
                look['paints'] = kept
        #: older plans' lava falls, which the build still reads
        if isinstance(data['look'].get('lavaFalls'), list) and data['look']['lavaFalls']:
            look['lavaFalls'] = data['look']['lavaFalls']

    #: The mission's name, its OrcsMustUI notes and its campaign: text only. A new
    #: campaign's tab image is not kept here; it is written when the pak is built.
    mission = {}
    if isinstance(data.get('mission'), dict):
        for key in MISSION_FIELDS:
            value = data['mission'].get(key)
            if isinstance(value, (str, int, float)) and str(value).strip():
                mission[key] = str(value)[:4000]
        #: the Scramble tiers it joins (missionscramble.clean)
        if isinstance(data['mission'].get('scramble'), dict):
            from riftlord.missions import missionscramble
            got = missionscramble.clean(data['mission']['scramble'])
            if got:
                mission['scramble'] = got
    #: world settings (riftlord.mapinfo.clean_world)
    from riftlord.mapeditor import mapinfo
    world = mapinfo.clean_world(data.get('world'))
    out = {'level': str(data.get('level') or name), 'region': region, 'look': look,
           'mission': mission, 'world': world,
           'rifts': rifts, 'doors': doors, 'spawners': spawners,
           'wiring': wiring, 'removed': removed, 'waves': waves,
           'edits': edits, 'added': added,
           'version': actors['version'], 'points': actors['points'],
           'playerStarts': actors['playerStarts']}
    #: a level from scratch (riftlord/scratch.py)
    from riftlord.mapeditor import scratch
    if removed_fx:
        out['removedFx'] = removed_fx
    #: the level's own lights and effects moved (clean_actor_moves)
    if clean_actor_moves(data.get('actorMoves')):
        out['actorMoves'] = clean_actor_moves(data.get('actorMoves'))
    #: the level's own lights recoloured and effects swapped (riftlord.build.world.levelretint)
    from riftlord.build.world import levelretint
    if levelretint.clean_colours(data.get('lightColours')):
        out['lightColours'] = levelretint.clean_colours(data.get('lightColours'))
    if levelretint.clean_swaps(data.get('fxSwaps')):
        out['fxSwaps'] = levelretint.clean_swaps(data.get('fxSwaps'))
    #: the level's own torches' wall plates (riftlord/controllers.py)
    from riftlord.mapeditor import controllers
    if controllers.clean_plates(data.get(controllers.PLATES_KEY)):
        out[controllers.PLATES_KEY] = controllers.clean_plates(data.get(controllers.PLATES_KEY))
    if scratch.clean(data.get('scratch')):
        out['scratch'] = scratch.clean(data.get('scratch'))
    #: edits to the level's blocking and kill volumes (riftlord.volumes)
    from riftlord.mapeditor import volumes
    if volumes.clean(data.get('volumes')):
        out['volumes'] = volumes.clean(data.get('volumes'))
    #: a minimap redrawn from the walkable floor (riftlord.minimapredraw)
    from riftlord.build.minimap import minimapredraw
    if minimapredraw.clean(data.get('minimap')):
        out['minimap'] = minimapredraw.clean(data.get('minimap'))
    #: mission events: boss, music change, unstable rift (riftlord/hostevents.py)
    from riftlord.missions import hostevents
    events = hostevents.clean(data.get('events'))
    if events is not None:
        out['events'] = events
    #: trap grids the author laid over Fab pieces (riftlord/fabemit.py)
    if data.get('fabGrids'):
        from riftlord.mapeditor import fabemit
        if fabemit.clean_grids(data.get('fabGrids')):
            out['fabGrids'] = fabemit.clean_grids(data.get('fabGrids'))
    try:
        errors, warnings = planactors.check(out, out['level'])
        warnings = errors + warnings
    except Exception as exc:                         # noqa: BLE001
        warnings = ['the routes could not be checked: %s' % exc]
    os.makedirs(PLANS, exist_ok=True)
    with open(plan_path(name), 'w') as fh:
        json.dump(out, fh, indent=1)
    return {'rifts': len(rifts), 'doors': len(doors), 'spawners': len(spawners),
            'removed': len(removed), 'wiring': len(wiring),
            'points': len(out['points']),
            'player_starts': len(out['playerStarts']),
            'waves': len(waves), 'edits': len(edits), 'added': len(added),
            'look': sorted(v for v in look.values() if isinstance(v, str)),
            'warnings': warnings,
            'needed': scratch.still_needed(out) if out.get('scratch') else [],
            'path': os.path.relpath(plan_path(name), os.path.dirname(PLANS))}
