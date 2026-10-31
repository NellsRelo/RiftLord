"""Generate the mission-hub enemy preview from a wave roster.

The preview is a Blueprint of posed skeletal mesh components. Each slot sets two
object references that follow a convention -- `<Creature>_SKM` for the mesh and
an idle animation for the pose -- while its transform is the diorama's layout
rather than anything creature-specific. So a preview for any roster is made by
cloning a diorama and repointing those two references per slot.

    python3 makepreview.py --units Kobold LizardmanRanger --path /Game/Mods/M/M_Enemies
"""

import argparse
import math
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.onslaught import featured  # noqa: E402
from riftlord.build.minimap import mapdata  # noqa: E402
from riftlord.build import modtable  # noqa: E402
from riftlord.build.navigation import navpatch  # noqa: E402
from riftlord.formats.ue.uassetio import Package  # noqa: E402


def walk(data, names, limit=4000):
    """Tagged properties as (name, kind, sub, value offset, size)."""
    out, o = [], 0
    for _ in range(limit):
        if o > len(data) - 8:
            break
        ni = struct.unpack_from('<i', data, o)[0]
        nm = names[ni] if 0 <= ni < len(names) else '?'
        if nm == 'None':
            break
        ti = struct.unpack_from('<i', data, o + 8)[0]
        kind = names[ti] if 0 <= ti < len(names) else '?'
        size = struct.unpack_from('<i', data, o + 16)[0]
        sub, head = None, 24 + 1
        if kind == 'StructProperty':
            si = struct.unpack_from('<i', data, o + 24)[0]
            sub = names[si] if 0 <= si < len(names) else '?'
            head = 24 + 8 + 17
        elif kind in ('ArrayProperty', 'ByteProperty', 'EnumProperty'):
            ii = struct.unpack_from('<i', data, o + 24)[0]
            sub = names[ii] if 0 <= ii < len(names) else '?'
            head = 24 + 8 + 1
        elif kind == 'BoolProperty':
            head, size = 26, 0
        step = head + size
        if step <= 0:                       # never loop on a malformed tag
            break
        out.append((nm, kind, sub, o + head, size))
        o += step
    return out


from riftlord.core import gamepath  # noqa: E402

CACHE = os.path.join(gamepath.scratch('previews', gamepath.CODE_ROOT), 'preview_assets.json')

#: Bumped whenever what is harvested changes, so a cache written by an older
#: build is rebuilt rather than trusted. A frozen editor keeps its cache in the
#: user's cache directory, where it outlives the build that wrote it.
CACHE_VERSION = 3


def harvest(force=False):
    """{creature: (mesh package, mesh, anim package, anim)} and diorama layouts.

    Read from the shipped dioramas rather than guessed from a naming
    convention, because the shipped names are irregular: `Wraith_Boss_SKm` has a
    lowercase m, `HellBat_Fire_Idle` capitalises differently from its component,
    and Orc_Medium plays a bespoke `HUB_01_Orc_Medium_02` rather than any idle.
    Cached, since it costs a pass over every preview package.
    """
    import json
    # read once per process: size_of and scale_of call this for every placement tried
    if not force and globals().get('_HARVESTED'):
        return globals()['_HARVESTED']
    if not force and os.path.exists(CACHE):
        with open(CACHE) as fh:
            data = json.load(fh)
        if data.get('version') == CACHE_VERSION:
            globals()['_COUNTS'] = data.get('counts', {})
            globals()['_SIZES'] = data.get('sizes', {})
            globals()['_SCALES'] = data.get('scales', {})
            globals()['_HARVESTED'] = (data['creatures'], data['layouts'])
            return data['creatures'], data['layouts']

    import collections
    creatures = collections.defaultdict(collections.Counter)
    scales = collections.defaultdict(list)
    layouts = {}
    for key in sorted(navpatch._index()):
        if 'BP_UI_Enemies' not in key or not key.endswith('.uasset'):
            continue
        path = key[:-7]
        try:
            f = mapdata._fetch(path)
            pkg = Package(open(f['.uasset'], 'rb').read(),
                          open(f['.uexp'], 'rb').read())
            pkg.load_payloads()
        except Exception:
            continue
        names = [s for s, _h in pkg.names]

        def obj(i):
            if i < 0:
                return names[struct.unpack_from('<I', pkg.imports[-i - 1], 20)[0]]
            return None

        def outer_pkg(i):
            if i >= 0:
                return None
            outer = struct.unpack('<iiiiiii', pkg.imports[-i - 1])[4]
            return (names[struct.unpack_from('<I', pkg.imports[-outer - 1], 20)[0]]
                    if outer < 0 else None)
        places = []
        for e, creature, mesh_at, anim_at in slot_exports(pkg):
            for pn, _k, _s, at, size in walk(e['data'], names):
                if pn == 'RelativeScale3D' and size == 12:
                    scales[creature].append(abs(struct.unpack_from('<3f', e['data'], at)[2]))
            mi = struct.unpack_from('<i', e['data'], mesh_at)[0]
            ai = struct.unpack_from('<i', e['data'], anim_at)[0]
            loc = None
            for pn, _k, _s, at, size in walk(e['data'], names):
                if pn == 'RelativeLocation' and size == 12:
                    loc = [round(x, 1) for x in
                           struct.unpack_from('<3f', e['data'], at)]
            if obj(mi) and outer_pkg(mi) and obj(ai) and outer_pkg(ai):
                creatures[creature][(outer_pkg(mi), obj(mi),
                                     outer_pkg(ai), obj(ai))] += 1
            if loc:
                places.append(loc)
        if places:
            layouts[path] = places
    best = {c: list(v.most_common(1)[0][0]) for c, v in creatures.items()}
    counts = {c: sum(v.values()) for c, v in creatures.items()}
    for creature, parts in from_characters(best).items():
        best[creature] = parts
        counts[creature] = 0        # no diorama uses it, so it never wins a tie
    sizes = {c: creature_size(parts[1]) for c, parts in best.items()}
    import statistics
    med = {c: round(statistics.median(v), 2) for c, v in scales.items() if v}
    globals().update(_COUNTS=counts, _SIZES=sizes, _SCALES=med, _HARVESTED=(best, layouts))
    with open(CACHE, 'w') as fh:
        json.dump({'version': CACHE_VERSION, 'creatures': best, 'layouts': layouts,
                   'counts': counts, 'sizes': sizes, 'scales': med}, fh, indent=1)
    return best, layouts


#: A creature's footprint radius is this percentile of its bind-pose vertices' distance
#: from the vertical axis (outstretched bind-pose arms inflate the maximum).
FOOTPRINT_PERCENTILE = 80
#: Height and footprint radius (uu) for a creature whose mesh cannot be read.
DEFAULT_HEIGHT, DEFAULT_RADIUS = 200.0, 50.0


def creature_size(mesh):
    """{height, radius} of a skeletal mesh's bind pose, or None."""
    import numpy as np
    from riftlord.formats.omd3 import omd3skm
    try:
        got = omd3skm.read(mesh)
    except Exception:                                # noqa: BLE001
        return None
    if not got or not got['verts']:
        return None
    v = np.array(got['verts'])
    r = np.hypot(v[:, 0], v[:, 1])
    return {'height': round(float(v[:, 2].max() - v[:, 2].min()), 1),
            'radius': round(float(np.percentile(r, FOOTPRINT_PERCENTILE)), 1)}


def size_of(creature):
    """(height, radius) in uu at the creature's shipped scale."""
    if creature in _MOD:
        return _MOD[creature]['height'], _MOD[creature]['radius']
    harvest()
    got = globals().get('_SIZES', {}).get(creature) or {}
    k = scale_of(creature)
    return (got.get('height') or DEFAULT_HEIGHT) * k, (got.get('radius') or DEFAULT_RADIUS) * k


def scale_of(creature):
    """The creature's median scale over the shipped dioramas (1 when unmeasured)."""
    if creature in _MOD:
        return _MOD[creature]['scale']
    harvest()
    return float(globals().get('_SCALES', {}).get(creature) or 1.0)


def object_name(path):
    """The name of a package's first export, which case may differ from the file.

    `Elemental_EarthLing_SKM` holds an object called `Elemental_Earthling_SKM`,
    and a preview slot references the object, not the file.
    """
    f = mapdata._fetch(path)
    pkg = Package(open(f['.uasset'], 'rb').read(), open(f['.uexp'], 'rb').read())
    names = [s for s, _h in pkg.names]
    # FObjectExport: class, super, template, outer, then the object's FName
    i = struct.unpack_from('<i', bytes(pkg.exports[0]['meta']), 16)[0]
    return names[i] if 0 <= i < len(names) else None


def from_characters(known):
    """{creature: [mesh pkg, mesh, anim pkg, anim]} for creatures no diorama shows.

    Not every minion appears in a shipped preview: there is no slot anywhere for
    the plain Fire Elemental or for a Fireling, so matching on the dioramas alone
    finds nothing for a Fireling and hands the Fire Elemental its FireLord --
    the upgraded form -- because that is the nearest name that does have a slot.

    The character assets themselves are laid out predictably: a family directory
    with Mesh/<name>_SKM beside Anim/<name>_Idle. That pair is all a preview slot
    needs, so any family the dioramas miss is filled straight from there.

    That layout also holds for gib pieces, dropped weapons, and the Optimized,
    Cine and Atlas variants of creatures already covered, so a family only counts
    when a spawn proto is spelled exactly like it and no diorama creature already
    is -- which is what separates the Fireling, that nothing shows, from
    `Orc_Light_Atlas`, that nothing spawns.
    """
    import collections
    from riftlord.onslaught import onslaught
    spawnable = {frozenset(tokens(u)) for u in onslaught.protos()}
    covered = {frozenset(tokens(c)) for c in known}
    families = collections.defaultdict(dict)
    for key in navpatch._index():
        if not key.endswith('.uasset') or '/Characters/Minions/' not in key:
            continue
        path = key[:-7]
        stem = path.rsplit('/', 1)[-1]
        folder, _, leaf = path.rpartition('/Mesh/')
        if leaf and stem.endswith('_SKM'):
            families[folder]['mesh'] = path
            continue
        folder, _, leaf = path.rpartition('/Anim/')
        if leaf and stem.endswith('_Idle'):
            families[folder].setdefault('anim', []).append(path)

    out = {}
    for folder, got in sorted(families.items()):
        if 'mesh' not in got or 'anim' not in got:
            continue
        creature = got['mesh'].rsplit('/', 1)[-1][:-len('_SKM')]
        want = frozenset(tokens(creature))
        if creature in out or want in covered or want not in spawnable:
            continue
        covered.add(want)
        # Every family carries several idles -- distracted, feared, tar-slowed.
        # The plain one is the creature's own name, and the shortest name is the
        # nearest thing to it when that is missing.
        plain = '%s_Idle' % creature
        anim_path = next((a for a in got['anim']
                          if a.rsplit('/', 1)[-1] == plain),
                         min(got['anim'], key=lambda a: len(a)))
        try:
            mesh, anim = object_name(got['mesh']), object_name(anim_path)
        except Exception:                                 # noqa: BLE001
            continue
        if mesh and anim:
            out[creature] = [got['mesh'], mesh, anim_path, anim]
    return out


def tokens(text):
    """A name split into words, across camelCase and underscores."""
    spaced = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', text).replace('_', ' ')
    return set(w.lower() for w in spaced.split() if w)


#: Spawn protos whose creature is spelled differently in the dioramas. Derived
#: by elimination -- each of these was the only proto with no creature and the
#: only creature no proto claimed -- and confirmed against the missions that
#: spawn them: Armored Ogres on Scenario_17, Mountain Trolls on Scenario_18,
#: Mongrifel on Scenario_12. `HellbatLigtning` is misspelled in the proto.
ALIASES = {
    'CyclopsMage': 'Cyclops_Magic_User',
    'OgreArmored': 'Ogre_Heavy',
    'TrollMountain': 'Troll_Boss',
    'KoboldKing': 'Kobold_Boss',
    'KoboldKing_Scramble': 'Kobold_Boss',
    'HellbatBile': 'Hellbat_Acid',
    'HellbatLigtning': 'Hellbat_Lightning',
    'OgreBasher_Scramble': 'Ogre_Basher',
    'OrcBoss_Scramble': 'Orc_Boss',
    'WraithBoss_Scramble': 'Wraith_Boss',
}

#: Where a creature belongs, by how it reads on screen.
#: Bosses take the front centre; tall creatures fill the back so they do not
#: hide anything; heavy-but-not-tall sit in the middle; everything small is at
#: the front. A Gnoll Hunter takes the very middle when no boss claims it.
BOSSES = ('Kobold_Boss', 'Orc_Boss', 'Troll_Boss', 'Wraith_Boss',
          'Hellbat_Boss', 'Ogre_Basher', 'FireFiend_Overlord',
          'Elemental_EarthLord', 'Elemental_FireLord')
TALL = ('Elemental', 'Ogre', 'Troll', 'Cyclops')
#: Bosses big enough to belong at the back despite being bosses. Measured from
#: the shipped dioramas: Ogre_Basher sits at (0, -91) and FireFiend_Overlord at
#: (164, -242), while Orc_Boss, Kobold_Boss and Wraith_Boss are all front and
#: centre around y=220. Size wins over boss-ness.
LARGE = ('FireFiend_Overlord',)
CENTREPIECE = 'Gnoll_Assassin'

BACK, MIDDLE, FRONT = 0, 1, 2


def tier(creature):
    """Which row a creature belongs in: BACK, MIDDLE or FRONT.

    Size is judged before boss status, which is what puts Ghorbash at the back
    with the other ogres rather than front and centre. A mod minion takes its stand-in's
    row, else MIDDLE when its WeightClass is Heavy, else FRONT.
    """
    if creature in _MOD:
        mod = _MOD[creature]
        if mod['standin']:
            return tier(mod['standin'])
        return MIDDLE if mod['weight'] == 'Heavy' else FRONT
    if any(creature.startswith(t) for t in TALL) or creature in LARGE:
        return BACK
    if creature in BOSSES:
        return FRONT
    if 'Heavy' in creature:
        return MIDDLE
    return FRONT


def creature_for(unit):
    """The diorama creature a spawn proto shows as, or None.

    Matched on word sets rather than whole names: a proto is often a prefix of
    the creature (`Troll` against `Troll_Light`) or reorders its words
    (`KoboldTNT` against `Kobold_Runner_TNT`). Where several creatures contain
    the proto's words -- `Ogre` fits Light, Heavy and Basher -- the one the
    dioramas use most often wins, which is the plain version of the unit.

    An installed mod's minion row (modminions) shows as itself when its manifest's
    Preview block gives art it can use (mod_creature), else as its stand-in's creature.
    """
    rec = _mod_row(unit)
    if rec is not None:
        return mod_creature(unit, rec)
    known = harvest()[0]
    if unit in ALIASES:
        return ALIASES[unit] if ALIASES[unit] in known else None
    want = tokens(unit)
    exact = [c for c in known if featured._norm(c) == featured._norm(unit)]
    if exact:
        return exact[0]
    same = [c for c in known if tokens(c) == want]
    if same:
        return same[0]
    subset = [c for c in known if want and want <= tokens(c)]
    if not subset:
        return None
    counts = globals().get('_COUNTS', {})
    return sorted(subset, key=lambda c: (len(tokens(c)), -counts.get(c, 0), c))[0]


#: Mod minions shown by their own art, by creature (= row name): {mesh, anim (package,
#: object), scale, height, radius (uu, at scale), standin (shipped creature or None),
#: weight, label}. Filled by mod_creature.
_MOD = {}


def _mod_row(unit):
    """The installed mod minion row `unit` (modminions), or None for any other name."""
    try:
        from riftlord.missions import modminions
        return modminions.rows().get(unit)
    except (Exception, SystemExit):                  # noqa: BLE001
        return None


def _split(path):
    """'/Game/A/B.C' -> ('/Game/A/B', 'C'); without an object, the leaf names both."""
    head, _, leaf = str(path).rpartition('/')
    pkg, _, obj = leaf.partition('.')
    return head + '/' + pkg, obj or pkg


def _art_ships(unit, path):
    """Whether a mission spawning mod row `unit` can load package `path`: the game ships
    it, or the row's manifest carries it."""
    from riftlord.core import gamefiles
    from riftlord.missions import modminions
    pkg, _obj = _split(path)
    inner = '../../../OMD/Content' + pkg[len('/Game'):]
    if not pkg.startswith('/Game/Mods/'):
        return inner + '.uasset' in gamefiles.index()
    return pkg in (modminions.carried_packages(unit) or [])


def mod_creature(unit, rec):
    """The creature mod row `unit` shows as: the row itself when its manifest's Preview
    has a Mesh and Anim the game ships or the mission carries (sized by its Scale,
    Radius and Height), else its stand-in's creature (modminions.stand_in: the Preview
    StandIn, or the shipped row with the same UIIcon); None when it has neither."""
    from riftlord.missions import modminions
    hint = modminions.preview_hint(unit) or {}
    sub = modminions.stand_in(unit)
    standin = creature_for(sub) if sub else None
    mesh, anim = hint.get('Mesh'), hint.get('Anim')
    if mesh and anim and _art_ships(unit, mesh) and _art_ships(unit, anim):
        def number(key, default):
            try:
                got = float(hint.get(key))
                return got if got > 0 else default
            except (TypeError, ValueError):
                return default
        scale = number('Scale', 1.0)
        base_h, base_r = size_of(standin) if standin else (DEFAULT_HEIGHT * scale,
                                                           DEFAULT_RADIUS * scale)
        _MOD[unit] = {'mesh': _split(mesh), 'anim': _split(anim), 'scale': scale,
                      'height': number('Height', base_h), 'radius': number('Radius', base_r),
                      'standin': standin, 'weight': rec.get('weight'),
                      'label': rec.get('name') or unit}
        return unit
    return standin


def label_of(creature):
    """What the preview picture calls a creature."""
    if creature in _MOD:
        return _MOD[creature]['label']
    return creature.replace('_', ' ')


def blocks_centre(x, y):
    """True where a creature would stand in the scene's deep middle.

    The hub's backdrop carries a gate there. No shipped diorama puts a creature
    in it, and a generated one clipped through.
    """
    return y < DEEP_Y and abs(x) < CENTRE_CLEAR


def _wedge(count):
    """Ground positions for up to WEDGE_MAX units: rank 0 front and centre, each next
    rank a step back on alternating sides, stepped further back when too close."""
    out = [(0.0, FRONT_Y)]
    for i in range(1, count):
        side = 1.0 if i % 2 else -1.0
        jitter = WEDGE_JITTER_X if ((i - 1) // 2) % 2 else -WEDGE_JITTER_X
        x, y = side * (WEDGE_SIDE_X + jitter), FRONT_Y - i * WEDGE_STEP_Y
        while any(math.dist((x, y), q) < GROUND_MIN_SEP for q in out):
            y -= WEDGE_STEP_Y
        out.append((x, y))
    return out


def _field(count, squeeze=1.0):
    """Ground positions for a large roster: staggered rows at GROUND_PITCH inside a box
    SPAN_X (times `squeeze`) by SPAN_Y that grows with the count, front edge at FRONT_Y."""
    span_x = (SPAN_X_BASE + SPAN_X_PER_UNIT * count) * squeeze
    span_y = SPAN_Y_BASE + SPAN_Y_PER_UNIT * count
    per_row = int(span_x // GROUND_PITCH) + 1
    rows = -(-count // per_row)
    step_y = span_y / (rows - 1) if rows > 1 else 0.0
    out = []
    for r in range(rows):
        n = min(per_row, count - r * per_row)
        y = FRONT_Y - r * step_y
        shift = GROUND_PITCH / 4 if r % 2 else -GROUND_PITCH / 4
        width = (n - 1) * GROUND_PITCH
        xs = [-width / 2 + k * GROUND_PITCH + shift for k in range(n)]
        # past DEEP_Y the row parts around the centre, keeping its spacing
        gap = CENTRE_CLEAR - min(abs(x) for x in xs)
        if y < DEEP_Y and gap > 0:
            xs = [x + gap if x >= 0 else x - gap for x in xs]
        out += [(x, y) for x in xs]
    return out


def ground_order(creatures):
    """Ground creatures front to back: a boss (or a Gnoll Hunter) first, then the
    front tier, the middle tier, and tall creatures last."""
    rank = {FRONT: 0, MIDDLE: 1, BACK: 2}
    lead = (next((c for c in creatures if c in BOSSES and tier(c) == FRONT), None)
            or (CENTREPIECE if CENTREPIECE in creatures else None))
    rest = sorted((c for c in creatures if c != lead), key=lambda c: rank[tier(c)])
    return ([lead] if lead else []) + rest


def _flyer_row(flyers, ground):
    """[(creature, (x, y, z))] for the flyers, a layer of their own over the ground
    block: one row (more when they do not fit) across the block's width plus the
    overhang, heights alternating about FLYER_Z, a boss low near the centre line."""
    if ground:
        xs = [x for _c, (x, _y, _z) in ground]
        ys = sorted(y for _c, (_x, y, _z) in ground)
        left, right = min(xs), max(xs)
        base_y = ys[len(ys) // 2] + FLYER_Y_OFFSET
    else:
        left, right, base_y = -WEDGE_SIDE_X, WEDGE_SIDE_X, FRONT_Y
    over = FLYER_SIDE_OVERHANG * (right - left)
    left, right = min(left - over, -WEDGE_SIDE_X), max(right + over, WEDGE_SIDE_X)
    out = []
    boss = next((c for c in flyers if c in BOSSES), None)
    if boss:
        out.append((boss, (0.0, base_y, FLYER_BOSS_Z)))
    rest = [c for c in flyers if c != boss]
    per_row = max(1, int((right - left) // FLYER_MIN_SEP) + 1)
    for r in range(-(-len(rest) // per_row) if rest else 0):
        row = rest[r * per_row:(r + 1) * per_row]
        n = len(row)
        pitch = min(FLYER_PITCH, (right - left) / (n - 1)) if n > 1 else 0.0
        width = pitch * (n - 1)
        for k, c in enumerate(row):
            z = FLYER_Z + (-FLYER_Z_STAGGER if k % 2 == 0 else FLYER_Z_STAGGER)
            out.append((c, (-width / 2 + k * pitch, base_y - r * FLYER_PITCH,
                            max(FLYER_Z_MIN, z))))
    return out


def arrange(creatures, flyers=()):
    """[(creature, (x, y, z))] for a roster.

    Ground creatures are laid out alone -- a wedge for up to WEDGE_MAX, a staggered
    field beyond -- and `flyers` (creatures whose proto flies) then take a layer of
    their own above them, never a ground slot.
    """
    flying = [c for c in creatures if c in set(flyers)]
    ground = ground_order([c for c in creatures if c not in set(flyers)])
    spots = _wedge(len(ground)) if len(ground) <= WEDGE_MAX else _field(len(ground))
    placed = [(c, (x, y, GROUND_Z)) for c, (x, y) in zip(ground, spots)]
    return placed + _flyer_row(flying, placed)


# -- F4: choose what to show, size-aware layout, fitted to the hub camera ---------------
# Measured in workshop/research/F4-preview-rules.md.

#: Ground creatures shown at most. The shipped non-DLC median is 6 slots (5 types) and
#: the DLC dioramas show 15-22; 6 read well on Beset but "could fit more" (user,
#: 2026-10-16), and 10 + 3 fit the frame on every shipped roster tried.
SLOT_BUDGET = 10
#: Flyers shown at most, over the ground block.
FLYER_BUDGET = 3
#: Two creatures stand at least this share of their summed footprint radii apart: the
#: shipped 25th percentile of nearest neighbours (median 1.04; bind-pose radii overstate
#: how much floor a creature covers, so designers let them overlap).
SPACING_FACTOR = 0.9
#: Hub camera (MainMenu_V2 Camera_MainMenu): location, horizontal FOV, screen aspect.
CAMERA_AT = (4670.2, 0.0, 140.9)
CAMERA_FOV = 65.24
SCREEN_ASPECT = 16.0 / 9.0
#: Where the mission-select diorama stands (EnemiesSpawnRoot) and its yaw.
PREVIEW_ROOT = (5513.0, 125.9, -15.0)
PREVIEW_ROOT_YAW = 100.0
#: The screen window every shipped non-DLC diorama fills (u, v in -1..1, u right, v up).
FRAME_U = (-0.2, 0.78)
#: The DLC dioramas (15-22 slots) reach further right; a layout past WEDGE_MAX may too.
FRAME_U_WIDE = (-0.2, 0.91)
#: A field too wide for the frame narrows by these shares of its width (more rows)
#: before any creature is dropped.
FIELD_SQUEEZE = (1.0, 0.85, 0.7, 0.55)
FRAME_V = (-0.85, 0.85)
#: A flyer's head may rise to here: shipped hellbats' bind-pose heads reach v 1.14 (their
#: bind pose stands taller than they fly), ground heads 0.79 (95th percentile).
FLYER_FRAME_TOP = 1.15
#: A layout too big for the frame first steps back from the camera by FIT_STEP_BACK a pass,
#: its front creature no deeper than FIT_FRONT_MIN (the shallowest shipped front edge, 109;
#: the median is 224), keeping every spacing; only then does it shrink.
FIT_STEP_BACK = 20.0
FIT_FRONT_MIN = 109.0
#: Each fitting pass shrinks the layout's spread by this factor.
FIT_SHRINK = 0.95


def family(creature):
    return creature.split('_')[0]


def is_big(creature):
    if creature in _MOD:
        return bool(_MOD[creature]['standin']) and is_big(_MOD[creature]['standin'])
    return any(creature.startswith(t) for t in TALL) or creature in LARGE


#: Most creatures a mission's preview options may ask for (the donor dioramas hold 22).
OPTION_MAX_GROUND, OPTION_MAX_FLYERS = 16, 6


def clean_options(raw):
    """A mission's preview options as saved: {ground, flyers, include, exclude}, with
    only the keys it sets. `include` units are always shown (budget allowing), `exclude`
    never."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for key, top in (('ground', OPTION_MAX_GROUND), ('flyers', OPTION_MAX_FLYERS)):
        if raw.get(key) not in (None, ''):
            out[key] = max(0, min(top, int(raw[key])))
    for key in ('include', 'exclude'):
        got = [str(u) for u in raw.get(key) or [] if str(u).strip()]
        if got:
            out[key] = sorted(set(got))
    return out


def choose(units, counts=None, flyers=(), options=None):
    """(ground creatures, flyer creatures) to show for a roster: at most SLOT_BUDGET on
    the ground, one per family first (most-spawned family first, its most-spawned
    member), a big creature and a boss when the roster has them, then second members,
    then a second copy of the most numerous; at most FLYER_BUDGET flyers. `options`
    (clean_options) can change both budgets, force units in and keep units out."""
    opts = clean_options(options)
    budget = opts.get('ground', SLOT_BUDGET)
    fly_budget = opts.get('flyers', FLYER_BUDGET)
    keep_out = set(opts.get('exclude') or ())
    forced = [creature_for(u) for u in opts.get('include') or () if u not in keep_out]
    weight = {}
    for n, unit in enumerate(units):
        c = creature_for(unit)
        if c is None or unit in keep_out:
            continue
        w = float((counts or {}).get(unit, 0)) if counts else float(len(units) - n)
        weight[c] = weight.get(c, 0.0) + w
    fly = sorted((c for c in weight if c in set(flyers)), key=lambda c: -weight[c])
    ground = sorted((c for c in weight if c not in set(flyers)), key=lambda c: -weight[c])
    fly = [c for c in forced if c in fly] + [c for c in fly if c not in forced]
    must = [c for c in forced if c in ground][:budget]
    fams = {}
    for c in ground:
        fams.setdefault(family(c), []).append(c)
    order = sorted(fams, key=lambda f: -sum(weight[c] for c in fams[f]))
    picked = list(must)
    for f in order:
        if len(picked) >= budget:
            break
        if not any(family(c) == f for c in picked):
            picked.append(fams[f][0])
    big = [c for c in ground if is_big(c)]
    if budget and big and not any(is_big(c) for c in picked):
        if len(picked) >= budget and len(picked) > len(must):
            picked.pop()
        if len(picked) < budget:
            picked.append(big[0])
    boss = next((c for c in ground if c in BOSSES), None)
    if boss and boss not in picked and len(picked) < budget:
        picked.append(boss)
    for c in ground:
        if len(picked) >= budget:
            break
        if c not in picked:
            picked.append(c)
    if ground and len(picked) < budget and len(picked) < 3:
        picked.append(ground[0])
    return picked, fly[:fly_budget]


def plan(units, counts=None, options=None):
    """[(creature, (x, y, z))]: what a mission's preview shows and where."""
    from riftlord.onslaught import onslaught
    flags = onslaught.proto_flags()
    flyers = {creature_for(u) for u in units if flags.get(u, {}).get('flyer')}
    ground, flying = choose(units, counts, flyers, options)
    return layout(ground, flying)


#: The preview picture's size (pixels), and how much of a creature's bind-pose footprint
#: radius its circle shows (bind poses stand wider than the creature does).
PICTURE_SIZE = (560, 460)
PICTURE_RADIUS_SHARE = 0.6
#: Margin round the formation in the picture (pixels).
PICTURE_MARGIN = 34


def picture(placed):
    """PNG bytes: the formation seen from above with the hub camera at the bottom --
    each ground creature a labelled circle at its footprint, flyers dashed, front row
    nearest the bottom edge."""
    import io
    from PIL import Image, ImageDraw
    w, h = PICTURE_SIZE
    img = Image.new('RGB', (w, h), (30, 26, 38))
    pen = ImageDraw.Draw(img)
    if not placed:
        buf = io.BytesIO()
        img.save(buf, 'PNG')
        return buf.getvalue()
    xs = [p[0] for _c, p in placed]
    ys = [p[1] for _c, p in placed]
    rad = max(size_of(c)[1] for c, _p in placed) * PICTURE_RADIUS_SHARE
    span = max(max(xs) - min(xs) + 2 * rad, (max(ys) - min(ys) + 2 * rad) * w / h, 1.0)
    k = (w - 2 * PICTURE_MARGIN) / span
    cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0

    def px(x, y):
        return (w / 2 + (x - cx) * k, h / 2 + (y - cy) * k)
    pen.text((8, h - 16), 'camera', fill=(120, 110, 140))
    pen.line([(0, h - 4), (w, h - 4)], fill=(70, 62, 86), width=2)
    order = sorted(placed, key=lambda t: (t[1][2] >= FLYER_Z_MIN, -t[1][1]))
    taken = []
    for c, (x, y, z) in order:
        r = size_of(c)[1] * PICTURE_RADIUS_SHARE * k
        u, v = px(x, y)
        tone = sum(map(ord, c)) * 2654435761 % 0xFFFFFF
        col = (110 + (tone & 0x5F), 90 + ((tone >> 8) & 0x5F), 120 + ((tone >> 16) & 0x5F))
        if z >= FLYER_Z_MIN:
            for a0 in range(0, 360, 30):
                pen.arc([u - r, v - r, u + r, v + r], a0, a0 + 15, fill=col, width=2)
        else:
            pen.ellipse([u - r, v - r, u + r, v + r], fill=tuple(int(t * 0.45) for t in col),
                        outline=col, width=2)
        label = label_of(c)
        tw = pen.textlength(label)
        # below the circle, or above it when that would cover a label already drawn
        for top in (v + r + 1, v - r - 14, v + r + 14, v - r - 27):
            box = (u - tw / 2 - 2, top, u + tw / 2 + 2, top + 12)
            if not any(box[0] < b[2] and b[0] < box[2] and box[1] < b[3] and b[1] < box[3]
                       for b in taken):
                break
        taken.append(box)
        pen.rectangle(box, fill=(20, 17, 26))
        pen.text((box[0] + 2, box[1]), label, fill=(240, 234, 248))
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return buf.getvalue()


def screen_of(x, y, z):
    """(u, v) of a diorama-local point through the hub camera, u right and v up, -1..1."""
    r = math.radians(PREVIEW_ROOT_YAW)
    wx = PREVIEW_ROOT[0] + math.cos(r) * x - math.sin(r) * y - CAMERA_AT[0]
    wy = PREVIEW_ROOT[1] + math.sin(r) * x + math.cos(r) * y - CAMERA_AT[1]
    wz = PREVIEW_ROOT[2] + z - CAMERA_AT[2]
    th = math.tan(math.radians(CAMERA_FOV / 2.0))
    return wy / wx / th, wz / wx / (th / SCREEN_ASPECT)


def frame_box(placed):
    """(u0, u1, v0, v1) of every creature's feet and head on screen; a flyer's head is
    scaled so FLYER_FRAME_TOP lands on FRAME_V's top."""
    us, vs = [], []
    for c, (x, y, z) in placed:
        h, r = size_of(c)
        for dx in (-r, r):
            for dz in (0.0, h):
                u, v = screen_of(x + dx, y, z + dz)
                if dz and z >= FLYER_Z_MIN:
                    v *= FRAME_V[1] / FLYER_FRAME_TOP
                us.append(u)
                vs.append(v)
    return min(us), max(us), min(vs), max(vs)


def _clear(a, pa, b, pb):
    return math.dist(pa[:2], pb[:2]) >= SPACING_FACTOR * (size_of(a)[1] + size_of(b)[1])


def _ground_ranks(ground):
    """Lead (a boss, else the first chosen that is not big: a big one up front fills the
    frame) front and centre, then shortest to tallest."""
    lead = next((c for c in ground if c in BOSSES and not is_big(c)),
                next((c for c in ground if not is_big(c)), ground[0]))
    rest = list(ground)
    rest.remove(lead)
    return [lead] + sorted(rest, key=lambda c: size_of(c)[0])


def place_ground(ground, squeeze=1.0):
    """[(creature, (x, y, 0))]: the F3 wedge (a staggered field past WEDGE_MAX), each rank
    stepped back until it clears every footprint already placed."""
    out = []
    ranks = _ground_ranks(ground)
    #: past WEDGE_MAX the shipped (DLC) previews are a wide staggered field, not a wedge
    spots = _field(len(ranks), squeeze) if len(ranks) > WEDGE_MAX else None
    if spots:
        spots.sort(key=lambda p: (-p[1], abs(p[0])))
    for i, c in enumerate(ranks):
        if spots:
            x, y = spots[i]
        elif i == 0:
            x, y = 0.0, FRONT_Y
        else:
            side = 1.0 if i % 2 else -1.0
            jitter = WEDGE_JITTER_X if ((i - 1) // 2) % 2 else -WEDGE_JITTER_X
            x = side * (WEDGE_SIDE_X + jitter)
            y = FRONT_Y - i * WEDGE_STEP_Y
        while any(not _clear(c, (x, y), q, qp) for q, qp in out) or blocks_centre(x, y):
            if blocks_centre(x, y):
                # stepping back never leaves the centre strip, so step aside of it
                x = CENTRE_CLEAR if x >= 0 else -CENTRE_CLEAR
                continue
            y -= WEDGE_STEP_Y
        out.append((c, (x, y, GROUND_Z)))
    return out


def fit(placed, frame_u=FRAME_U):
    """`placed` slid sideways, stepped back, then shrunk toward its centre until it fills no
    more than the hub's frame; None when it cannot fit without two footprints touching."""
    cur = list(placed)
    for _ in range(120):
        u0, u1, v0, v1 = frame_box(cur)
        mid = (frame_u[0] + frame_u[1]) / 2.0
        cy = sum(p[1] for _c, p in cur) / len(cur)
        per_uu = screen_of(1.0, cy, 0.0)[0] - screen_of(0.0, cy, 0.0)[0]
        shift = (mid - (u0 + u1) / 2.0) / per_uu
        cur = [(c, (x + shift, y, z)) for c, (x, y, z) in cur]
        u0, u1, v0, v1 = frame_box(cur)
        if (u0 >= frame_u[0] and u1 <= frame_u[1] and v0 >= FRAME_V[0]
                and v1 <= FRAME_V[1]):
            return cur
        front = max(p[1] for c, p in cur if p[2] < FLYER_Z_MIN) if any(
            p[2] < FLYER_Z_MIN for _c, p in cur) else FIT_FRONT_MIN
        if front - FIT_STEP_BACK >= FIT_FRONT_MIN:
            cur = [(c, (x, y - FIT_STEP_BACK, z)) for c, (x, y, z) in cur]
            continue
        cx = sum(p[0] for _c, p in cur) / len(cur)
        cy = sum(p[1] for _c, p in cur) / len(cur)
        nxt = [(c, (cx + (x - cx) * FIT_SHRINK, cy + (y - cy) * FIT_SHRINK, z))
               for c, (x, y, z) in cur]
        ground = [(c, p) for c, p in nxt if p[2] < FLYER_Z_MIN]
        if any(not _clear(a, pa, b, pb) for i, (a, pa) in enumerate(ground)
               for b, pb in ground[i + 1:]):
            return None
        cur = nxt
    return None


def layout(ground, flyers=()):
    """[(creature, (x, y, z))] for the chosen creatures, fitted to the hub's frame; the
    last-chosen creature is dropped until it fits."""
    ground, flyers = list(ground), list(flyers)
    while ground or flyers:
        wide = len(ground) > WEDGE_MAX
        for squeeze in (FIELD_SQUEEZE if wide else (1.0,)):
            placed = place_ground(ground, squeeze) if ground else []
            row = _flyer_row(flyers, placed)
            back = min((p[1] for _c, p in placed), default=FRONT_Y)
            # flyers move back over the block (further from the camera, lower on
            # screen) before anything is dropped
            for step in range(0, 20):
                moved = [(c, (x, y - step * WEDGE_STEP_Y, z)) for c, (x, y, z) in row]
                if moved and min(p[1] for _c, p in moved) < back - WEDGE_STEP_Y:
                    break
                got = fit(placed + moved, FRAME_U_WIDE if wide else FRAME_U)
                if got is not None:
                    return got
                if not row:
                    break
        # then flyers go before any ground creature
        if flyers:
            flyers.pop()
        else:
            ground.pop()
    return []


def yaw_for(x):
    """A slot's yaw in degrees: turned with its side of the frame."""
    return max(-YAW_MAX, min(YAW_MAX, YAW_PER_UNIT_X * x))


def assets_for(creature):
    """(mesh package, mesh object, anim package, anim object) or None."""
    if creature in _MOD:
        return _MOD[creature]['mesh'] + _MOD[creature]['anim']
    got = harvest()[0].get(creature)
    return tuple(got) if got else None


def spread(points):
    """The smallest gap between any two slots -- how crowded a diorama is."""
    import math
    if len(points) < 2:
        return 0.0
    return min(math.dist(a[:2], b[:2])
               for i, a in enumerate(points) for b in points[i + 1:])


#: Front of the diorama is +Y, X is lateral, Z is up. Measured over the 33 shipped
#: BP_UI_Enemies dioramas (workshop/research/F3-preview-spacing.md).
#: Ground units stand at this height.
GROUND_Z = 0.0
#: Depth of the front-most ground unit.
FRONT_Y = 230.0
#: Ground rosters up to this size form a wedge; larger ones a staggered field.
WEDGE_MAX = 8
#: Depth step between wedge ranks.
WEDGE_STEP_Y = 60.0
#: Side offset of every wedge rank after the first.
WEDGE_SIDE_X = 150.0
#: In and out jitter on that offset, so wedge columns do not line up.
WEDGE_JITTER_X = 30.0
#: No two ground units closer than this in xy.
GROUND_MIN_SEP = 120.0
#: Spacing in a staggered ground field.
GROUND_PITCH = 150.0
#: Field width and depth: base plus this much per ground unit.
SPAN_X_BASE, SPAN_X_PER_UNIT = 150.0, 40.0
SPAN_Y_BASE, SPAN_Y_PER_UNIT = 90.0, 55.0
#: Degrees of yaw per unit of x, and its limit.
YAW_PER_UNIT_X = 0.05
YAW_MAX = 40.0

#: Flyer height, alternated by the stagger, never below the minimum.
FLYER_Z = 240.0
FLYER_Z_STAGGER = 40.0
FLYER_Z_MIN = 180.0
#: Flyer spacing in a row, and the closest two may be.
FLYER_PITCH = 220.0
FLYER_MIN_SEP = 115.0
#: Flyers reach this share of the ground block's width past each side.
FLYER_SIDE_OVERHANG = 0.15
#: Flyer row depth relative to the ground block's median depth.
FLYER_Y_OFFSET = 90.0
#: A flying boss's height.
FLYER_BOSS_Z = 95.0


#: Depth past which the middle of the scene is kept clear, in scene units.
#: Measured over the 69 back-tier slots in the shipped dioramas: only 11 sit
#: within 70 of the centre, and every one of those nearer than 40 is shallower
#: than y = -188. Deeper than this a generated creature clipped the gate in the
#: mission hub.
DEEP_Y = -200.0

#: Half-width of the corridor kept clear past DEEP_Y. The closest shipped
#: creature at that depth is Elemental_Earth at x = -61.
CENTRE_CLEAR = 60.0


def clean_donors():
    """Dioramas with no particle, light or material-override baggage.

    A repointed slot inherits whatever else its donor slot carried: putting a
    kobold in Scenario_15's Wraith_Boss slot gave it the wraith's necrotic
    OverrideMaterials, its three particle systems and its point light.
    """
    out = {}
    for path in harvest()[1]:
        try:
            f = mapdata._fetch(path)
            pkg = Package(open(f['.uasset'], 'rb').read(),
                          open(f['.uexp'], 'rb').read())
            pkg.load_payloads()
        except Exception:
            continue
        names = [s for s, _h in pkg.names]

        def cls(e):
            i = struct.unpack_from('<i', e['meta'], 0)[0]
            return (names[struct.unpack_from('<I', pkg.imports[-i - 1], 20)[0]]
                    if i < 0 else '')
        if any('Particle' in cls(e) or 'Light' in cls(e) for e in pkg.exports):
            continue
        slots = slot_exports(pkg)
        placed = all(any(pn == 'RelativeLocation' and size == 12
                         for pn, _k, _s, _at, size in walk(e['data'], names))
                     for e, _c, _m, _a in slots)
        if slots and placed:
            out[path] = len(slots)
    return out


def clear_array(data, names, prop):
    """Empty an array property in an export payload, keeping it well formed."""
    out = bytearray(data)
    for pn, kind, _sub, at, size in walk(bytes(out), names):
        if pn != prop or kind != 'ArrayProperty':
            continue
        out[at:at + size] = struct.pack('<i', 0)
        struct.pack_into('<i', out, at - (24 + 8 + 1) + 16, 4)
        return bytes(out)
    return bytes(out)


def set_location(data, names, xyz):
    """Write a slot's RelativeLocation, if it has one."""
    out = bytearray(data)
    for pn, _kind, _sub, at, size in walk(bytes(out), names):
        if pn == 'RelativeLocation' and size == 12:
            struct.pack_into('<3f', out, at, *xyz)
            return bytes(out), True
    return bytes(out), False


def set_scale(data, names, k):
    """Write a slot's RelativeScale3D as a uniform, unmirrored `k`, if it has one."""
    out = bytearray(data)
    for pn, _kind, _sub, at, size in walk(bytes(out), names):
        if pn == 'RelativeScale3D' and size == 12:
            struct.pack_into('<3f', out, at, k, k, k)
            return bytes(out), True
    return bytes(out), False


def set_yaw(data, names, yaw):
    """Write the yaw of a slot's RelativeRotation (pitch, yaw, roll), if it has one."""
    out = bytearray(data)
    for pn, _kind, _sub, at, size in walk(bytes(out), names):
        if pn == 'RelativeRotation' and size == 12:
            struct.pack_into('<f', out, at + 4, yaw)
            return bytes(out), True
    return bytes(out), False


def pick_donor(needed):
    """The best-spread diorama that can hold `needed` creatures.

    Not the smallest that fits: Scenario_01's four slots are the most crowded
    of all 33 (a 52-unit minimum gap against 214 for Scenario_06), so choosing
    by slot count alone stacks the roster on top of itself.
    """
    _c, layouts = harvest()
    sized = [(p, len(v), spread(v)) for p, v in layouts.items() if len(v) >= needed]
    if not sized:
        return max(layouts, key=lambda p: len(layouts[p]))
    close = [t for t in sized if t[1] <= needed + 4] or sized
    return max(close, key=lambda t: t[2])[0]


def slot_exports(pkg):
    """[(export, creature, mesh value offset, anim value offset)] per slot."""
    names = [s for s, _h in pkg.names]
    out = []
    for e in pkg.exports:
        nm = names[struct.unpack_from('<I', e['meta'], 16)[0]]
        if not (nm.endswith('_GEN_VARIABLE') and '_SKM' in nm):
            continue
        data = e['data']
        mesh_at = anim_at = None
        for pn, kind, _sub, at, size in walk(data, names):
            if pn == 'SkeletalMesh' and kind == 'ObjectProperty':
                mesh_at = at
            elif pn == 'AnimationData' and kind == 'StructProperty':
                inner = data[at:at + size]
                for qn, qkind, _s2, qat, _sz in walk(inner, names):
                    if qn == 'AnimToPlay' and qkind == 'ObjectProperty':
                        anim_at = at + qat
        if mesh_at is not None and anim_at is not None:
            out.append((e, re.sub(r'_SKM\d*_GEN_VARIABLE$', '', nm), mesh_at, anim_at))
    return out


def reference(pkg, package_path, object_name, class_name):
    """An import for an asset, reusing one if the package already has it."""
    names = [s for s, _h in pkg.names]
    for k, imp in enumerate(pkg.imports):
        cp, _a, cn, _b, _outer, on, _c = struct.unpack('<iiiiiii', imp)
        if names[on] == object_name and names[cn] == class_name:
            return -(k + 1)
    pkg_ref = pkg.add_import('/Script/CoreUObject', 'Package', 0, package_path)
    return pkg.add_import('/Script/Engine', class_name, pkg_ref, object_name)


# -- a live particle effect on a mod minion ---------------------------------------------
# Measured over the 46 ParticleSystemComponents of the 12 shipped dioramas that place one
# (Scenario_07, 10, 11, 13, 15, 18, Ice_01-03, Sand_02, Sand_05, Other_03), 2026-10-24:
# - template `<Var>_GEN_VARIABLE`: class ParticleSystemComponent, template its CDO, outer
#   the Blueprint class, flags 0x29; tags Template, transform, PrimaryComponentTick, never
#   bAutoActivate (class default: on); preloads SbS [class] CbS [particle system]
#   SbC [component class, its CDO] CbC [class]; trailer the UObject int32.
# - SCS_Node (outer the SCS): ComponentClass, ComponentTemplate, AttachToName (the bone; 4
#   of 46 have none), VariableGuid, InternalVariableName; preloads CbS [template, component
#   class] SbC [SCS_Node class, its CDO] CbC [SCS]; trailer the UObject int32.
# - its creature slot's SCS_Node lists it in ChildNodes and CbS; the SCS lists it in
#   AllNodes and CbS; the class CDO's SbS has [node, template]; the class's CbS has the
#   component class. No member property on the class (ExecuteNodeOnActor only logs).

#: The shipped diorama effect added effects are copied from (package, variable name).
EFFECT_DONOR = ('/Game/Maps/Campaign/Scenario_10/BP/BP_UI_Enemies_Scenario_10',
                'P_TrapCatapultWMTrail')
#: Added effect components are named this plus a capital letter (no `_<digits>`).
EFFECT_VAR = 'RiftLordEffect'
EFFECT_LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
#: Class of an effect's object.
EFFECT_CLASS = 'ParticleSystem'
EFFECT_COMPONENT = 'ParticleSystemComponent'


def _package_blobs(unit, path):
    """(uasset, uexp) of /Game package `path`: the mod row `unit`'s installed pak for a
    /Game/Mods/ path, else the game."""
    from riftlord.core import gamefiles
    from riftlord.missions import modminions
    if path.startswith('/Game/Mods/'):
        src = modminions.Source(modminions.rows()[unit]['pak'])
        inner = 'OMD/Content' + path[len('/Game'):]
        return src.read(inner + '.uasset'), src.read(inner + '.uexp')
    return gamefiles.package(path)


def _export_class(pkg, i):
    c = struct.unpack_from('<i', pkg.exports[i]['meta'], 0)[0]
    return pkg.names[struct.unpack_from('<i', pkg.imports[-c - 1], 20)[0]][0] if c < 0 else None


def _export_form(pkg, i):
    """(name, number) an export stores for its object name."""
    return (pkg.names[struct.unpack_from('<i', pkg.exports[i]['meta'], 16)[0]][0],
            struct.unpack_from('<i', pkg.exports[i]['meta'], 20)[0])


_EFFECTS = {}


def effect_for(creature):
    """(effect, warnings) for a mod creature's Preview Effect: effect {package, object
    (name, number) as the package stores it, socket (name, number) or None, offset, scale,
    path}, or None when there is none or it cannot be shown (a warning says why)."""
    from riftlord.core import pakspelling
    from riftlord.missions import modminions
    if creature not in _MOD:
        return None, []
    eff, warns = modminions.preview_effect(creature)
    if eff is None:
        return None, warns
    key = (creature, eff['path'], eff['socket'], eff['offset'], eff['rotation'],
           eff['scale'], _MOD[creature]['mesh'])
    if key in _EFFECTS:
        return _EFFECTS[key][0], warns + _EFFECTS[key][1]
    more = []
    pkg_path, obj = _split(eff['path'])
    got = None
    if not _art_ships(creature, eff['path']):
        more.append('%s: Preview Effect %s is neither shipped nor in the row\'s Packages; '
                    'the preview shows it without an effect' % (creature, pkg_path))
    else:
        try:
            pkg = Package(*_package_blobs(creature, pkg_path))
            tops = [i for i in range(len(pkg.exports))
                    if struct.unpack_from('<i', pkg.exports[i]['meta'], 12)[0] == 0
                    and pakspelling.spelled(*_export_form(pkg, i)).lower() == obj.lower()]
            if not tops:
                more.append('%s: Preview Effect %s holds no object %s; no effect'
                            % (creature, pkg_path, obj))
            elif _export_class(pkg, tops[0]) != EFFECT_CLASS:
                more.append('%s: Preview Effect %s is a %s, not a %s (Cascade); no effect'
                            % (creature, eff['path'], _export_class(pkg, tops[0]),
                               EFFECT_CLASS))
            else:
                got = {'package': pkg_path, 'object': _export_form(pkg, tops[0]),
                       'socket': None, 'offset': eff['offset'],
                       'rotation': eff['rotation'], 'scale': eff['scale'],
                       'path': eff['path']}
        except (Exception, SystemExit) as exc:      # noqa: BLE001
            more.append('%s: Preview Effect %s does not read (%s); no effect'
                        % (creature, pkg_path, exc))
    if got and eff['socket']:
        form = pakspelling.fname_split(eff['socket'])
        if _has_name(creature, _MOD[creature]['mesh'][0], form[0]):
            got['socket'] = form
        else:
            more.append('%s: Preview EffectSocket %s is not a bone or socket of %s; the '
                        'effect sits at the mesh root'
                        % (creature, eff['socket'], _MOD[creature]['mesh'][1]))
    _EFFECTS[key] = (got, more)
    return got, warns + more


def _has_name(unit, mesh_path, name):
    """Whether the mesh package, or the skeleton package it imports, holds FName `name`
    (bone and socket names live in one or the other)."""
    from riftlord.core import pakspelling
    try:
        pkg = Package(_package_blobs(unit, mesh_path)[0])
    except (Exception, SystemExit):                  # noqa: BLE001
        return False
    names = [n.lower() for n, _h in pkg.names]
    if name.lower() in names:
        return True
    for rec in pkg.imports:
        cls, outer = pkg.names[struct.unpack_from('<i', rec, 8)[0]][0], \
            struct.unpack_from('<i', rec, 16)[0]
        if cls != 'Skeleton' or outer >= 0:
            continue
        orec = pkg.imports[-outer - 1]
        skel = pakspelling.spelled(pkg.names[struct.unpack_from('<i', orec, 20)[0]][0],
                                   struct.unpack_from('<i', orec, 24)[0])
        try:
            sk = Package(_package_blobs(unit, skel)[0])
        except (Exception, SystemExit):              # noqa: BLE001
            continue
        if name.lower() in [n.lower() for n, _h in sk.names]:
            return True
    return False


_EFFECT_REF = {}


def effect_reference():
    """(package, template export, node export) of EFFECT_DONOR (zero-based indices)."""
    if not _EFFECT_REF:
        f = mapdata._fetch(EFFECT_DONOR[0])
        src = Package(open(f['.uasset'], 'rb').read(), open(f['.uexp'], 'rb').read())
        src.load_payloads()
        want = EFFECT_DONOR[1] + '_GEN_VARIABLE'
        psc = next(i for i in range(len(src.exports)) if _export_form(src, i) == (want, 0))
        node = next(i for i in range(len(src.exports))
                    if _export_class(src, i) == 'SCS_Node'
                    and _object_tag(src, i, 'ComponentTemplate') == psc + 1)
        _EFFECT_REF.update(pkg=src, psc=psc, node=node)
    return _EFFECT_REF['pkg'], _EFFECT_REF['psc'], _EFFECT_REF['node']


def _object_tag(pkg, i, prop):
    """The package index an export's ObjectProperty `prop` holds, or None."""
    names = [s for s, _h in pkg.names]
    for pn, kind, _s, at, size in walk(pkg.exports[i]['data'] or b'', names):
        if pn == prop and kind == 'ObjectProperty' and size == 4:
            return struct.unpack_from('<i', pkg.exports[i]['data'], at)[0]
    return None


def _object_array(pkg, i, prop):
    names = [s for s, _h in pkg.names]
    data = pkg.exports[i]['data'] or b''
    for pn, kind, _s, at, size in walk(data, names):
        if pn == prop and kind == 'ArrayProperty':
            n = struct.unpack_from('<i', data, at)[0]
            return list(struct.unpack_from('<%di' % n, data, at + 4))
    return []


def groups_of(pkg, i):
    """Export i's four preload groups (SbS, CbS, SbC, CbC) as package indices."""
    meta = pkg.exports[i]['meta']
    first = struct.unpack_from('<i', meta, 84)[0]
    counts = struct.unpack_from('<4i', meta, 88)
    out, at = [], first
    for n in counts:
        out.append(list(pkg.preload[at:at + n]) if first >= 0 else [])
        at += n
    return out


def _set_groups(pkg, i, groups):
    """Give appended export i its preload groups, at the end of the table."""
    meta = bytearray(pkg.exports[i]['meta'])
    struct.pack_into('<i', meta, 84, len(pkg.preload) if any(groups) else -1)
    struct.pack_into('<4i', meta, 88, *[len(g) for g in groups])
    for g in groups:
        pkg.preload.extend(g)
    pkg.preload_count = len(pkg.preload)
    pkg.exports[i]['meta'] = bytes(meta)


def _zero_payloads(data, names, props):
    """`data` with the payloads of tags `props` zeroed (copied names and references a lift
    would otherwise carry over)."""
    out = bytearray(data)
    for pn, _k, _s, at, size in walk(bytes(out), names):
        if pn in props:
            out[at:at + size] = b'\0' * size
    return out


def _tag_bytes(b, name, kind, payload, struct_name=None):
    """A property tag as levelbuild.Builder.add_property writes it."""
    tag = bytearray()
    tag += struct.pack('<ii', b.pkg.add_name(name), 0)
    tag += struct.pack('<ii', b.pkg.add_name(kind), 0)
    tag += struct.pack('<ii', len(payload), 0)
    if kind == 'StructProperty':
        tag += struct.pack('<ii', b.pkg.add_name(struct_name), 0) + b'\0' * 16
    elif kind in ('ArrayProperty', 'ByteProperty', 'EnumProperty'):
        tag += struct.pack('<ii', b.pkg.add_name(struct_name or 'None'), 0)
    return bytes(tag) + b'\0' + payload


def _put_tag(b, index, name, kind, payload, struct_name=None, before=None):
    """Set tag `name` on export `index` (package index) in place, else insert it before tag
    `before` (when present), else before the None."""
    data = bytearray(b.pkg.exports[index - 1]['data'])
    tag = _tag_bytes(b, name, kind, payload, struct_name)
    span = b._tag_span(bytes(data), name)
    if span:
        data[span[0]:span[1]] = tag
    else:
        at = (b._tag_span(bytes(data), before) or (None,))[0] if before else None
        if at is None:
            at = b._terminator(bytes(data))
        data[at:at] = tag
    b.pkg.exports[index - 1]['data'] = bytes(data)
    b.pkg.exports[index - 1]['size'] = len(data)


def _preload_before(pkg, i, target, group, last):
    """Add `target` to export i's preload `group` (0..3), ahead of entry `last` when the
    group ends with it (a slot node lists its children before its component class)."""
    pkg.add_preload_dependency(i, target, ('serialization_before_serialization',
                                           'create_before_serialization',
                                           'serialization_before_create',
                                           'create_before_create')[group])
    first = struct.unpack_from('<i', pkg.exports[i]['meta'], 84)[0]
    counts = struct.unpack_from('<4i', pkg.exports[i]['meta'], 88)
    end = first + sum(counts[:group + 1])
    if counts[group] >= 2 and pkg.preload[end - 2] == last:
        pkg.preload[end - 2], pkg.preload[end - 1] = target, last


def _export_of_class(pkg, cls):
    return next(i for i in range(len(pkg.exports)) if _export_class(pkg, i) == cls)


def add_effect(pkg, slot, effect, letter):
    """Attach `effect` (effect_for) to slot export `slot` (zero-based) of preview `pkg`:
    a ParticleSystemComponent template and its SCS_Node, copied from EFFECT_DONOR and
    wired as the shipped dioramas wire theirs. Returns (template, node) package indices."""
    from riftlord.build import levelbuild
    from riftlord.core import pakspelling
    src, s_psc, s_node = effect_reference()
    b = levelbuild.Builder.__new__(levelbuild.Builder)
    b.pkg, b._missed, b.preload_dropped, b.name = pkg, [], [], 'preview'
    bpgc = _export_of_class(pkg, 'BlueprintGeneratedClass')
    scs = _export_of_class(pkg, 'SimpleConstructionScript')
    cdo = next(i for i in range(len(pkg.exports))
               if _export_form(pkg, i)[0].startswith('Default__'))
    parent = next(i for i in range(len(pkg.exports)) if _export_class(pkg, i) == 'SCS_Node'
                  and _object_tag(pkg, i, 'ComponentTemplate') == slot + 1)
    mapping = {_export_of_class(src, 'BlueprintGeneratedClass'): bpgc + 1,
               _export_of_class(src, 'SimpleConstructionScript'): scs + 1}
    src_names = [s for s, _h in src.names]
    var = EFFECT_VAR + letter
    made = []
    for j, zero in ((s_psc, ('Template', 'RelativeLocation', 'RelativeRotation')),
                    (s_node, ('AttachToName', 'VariableGuid', 'InternalVariableName'))):
        pkg.exports.append({'meta': bytes(src.exports[j]['meta']), 'size': 0, 'offset': 0,
                            'data': bytes(_zero_payloads(src.exports[j]['data'], src_names,
                                                         zero))})
        mapping[j] = len(pkg.exports)
        made.append(len(pkg.exports))
    psc, node = made
    for j, ours in ((s_psc, psc), (s_node, node)):
        meta = bytearray(src.exports[j]['meta'])
        for at in (0, 4, 8, 12):
            struct.pack_into('<i', meta, at,
                             b._reindex(src, struct.unpack_from('<i', meta, at)[0], mapping))
        data = bytearray(pkg.exports[ours - 1]['data'])
        b._rewrite(src, data, mapping=mapping)
        pkg.exports[ours - 1]['meta'] = bytes(meta)
        pkg.exports[ours - 1]['data'] = bytes(data)
    # the template: its object name, particle system and transform
    meta = bytearray(pkg.exports[psc - 1]['meta'])
    struct.pack_into('<ii', meta, 16, pkg.add_name(var + '_GEN_VARIABLE'), 0)
    pkg.exports[psc - 1]['meta'] = bytes(meta)
    base, number = pakspelling.fname_split(effect['package'])
    pref = b._import('/Script/CoreUObject', 'Package', 0, base, number)
    template = b._import('/Script/Engine', EFFECT_CLASS, pref, *effect['object'])
    _put_tag(b, psc, 'Template', 'ObjectProperty', struct.pack('<i', template))
    if any(effect['offset']):
        b.set_vector(psc, 'RelativeLocation', effect['offset'])
    else:
        b.clear_property(psc, 'RelativeLocation')
    if any(effect['rotation']):
        _put_tag(b, psc, 'RelativeRotation', 'StructProperty',
                 struct.pack('<3f', *effect['rotation']), 'Rotator',
                 before='PrimaryComponentTick')
    else:
        b.clear_property(psc, 'RelativeRotation')
    if effect['scale'] != 1.0:
        _put_tag(b, psc, 'RelativeScale3D', 'StructProperty',
                 struct.pack('<3f', *(effect['scale'],) * 3), 'Vector',
                 before='PrimaryComponentTick')
    # the node: its object name, bone, Guid and variable
    taken = [_export_form(pkg, i)[1] for i in range(len(pkg.exports))
             if _export_class(pkg, i) == 'SCS_Node' and i != node - 1
             and _export_form(pkg, i)[0] == 'SCS_Node']
    meta = bytearray(pkg.exports[node - 1]['meta'])
    struct.pack_into('<ii', meta, 16, pkg.add_name('SCS_Node'), max(taken + [0]) + 1)
    pkg.exports[node - 1]['meta'] = bytes(meta)
    if effect['socket']:
        _put_tag(b, node, 'AttachToName', 'NameProperty',
                 struct.pack('<ii', pkg.add_name(effect['socket'][0]), effect['socket'][1]))
    else:
        b.clear_property(node, 'AttachToName')
    import hashlib
    guid = hashlib.md5(('%s/%s' % (effect['path'], var)).encode()).digest()
    _put_tag(b, node, 'VariableGuid', 'StructProperty', guid, 'Guid')
    _put_tag(b, node, 'InternalVariableName', 'NameProperty',
             struct.pack('<ii', pkg.add_name(var), 0))
    # preloads, as the shipped dioramas lay them out
    cls = b._reindex(src, struct.unpack_from('<i', src.exports[s_psc]['meta'], 0)[0], mapping)
    cls_cdo = b._reindex(src, struct.unpack_from('<i', src.exports[s_psc]['meta'], 8)[0],
                         mapping)
    node_cls = b._reindex(src, struct.unpack_from('<i', src.exports[s_node]['meta'], 0)[0],
                          mapping)
    node_cdo = b._reindex(src, struct.unpack_from('<i', src.exports[s_node]['meta'], 8)[0],
                          mapping)
    _set_groups(pkg, psc - 1, [[bpgc + 1], [template], [cls, cls_cdo], [bpgc + 1]])
    _set_groups(pkg, node - 1, [[], [psc, cls], [node_cls, node_cdo], [scs + 1]])
    # the wiring: parent's children, the SCS's nodes, the CDO and the class
    kids = _object_array(pkg, parent, 'ChildNodes') + [node]
    _put_tag(b, parent + 1, 'ChildNodes', 'ArrayProperty',
             struct.pack('<%di' % (len(kids) + 1), len(kids), *kids), 'ObjectProperty',
             before='VariableGuid')
    _preload_before(pkg, parent, node, 1, last=_object_tag(pkg, parent, 'ComponentClass'))
    every = _object_array(pkg, scs, 'AllNodes') + [node]
    _put_tag(b, scs + 1, 'AllNodes', 'ArrayProperty',
             struct.pack('<%di' % (len(every) + 1), len(every), *every), 'ObjectProperty')
    pkg.add_preload_dependency(scs, node, 'create_before_serialization')
    pkg.add_preload_dependency(cdo, node, 'serialization_before_serialization')
    pkg.add_preload_dependency(cdo, psc, 'serialization_before_serialization')
    if cls not in groups_of(pkg, bpgc)[1]:
        pkg.add_preload_dependency(bpgc, cls, 'create_before_serialization')
    return psc, node


def effect_exports(pkg):
    """[(template, node, parent node)] zero-based, of the effects add_effect wrote."""
    out = []
    for i in range(len(pkg.exports)):
        name = _export_form(pkg, i)[0]
        if not (name.startswith(EFFECT_VAR) and name.endswith('_GEN_VARIABLE')):
            continue
        node = next((k for k in range(len(pkg.exports)) if _export_class(pkg, k) == 'SCS_Node'
                     and _object_tag(pkg, k, 'ComponentTemplate') == i + 1), None)
        parent = next((k for k in range(len(pkg.exports))
                       if node is not None and _export_class(pkg, k) == 'SCS_Node'
                       and node + 1 in _object_array(pkg, k, 'ChildNodes')), None)
        out.append((i, node, parent))
    return out


def _import_target(pkg, index):
    """(class, package path, (name, number)) of an object import."""
    from riftlord.core import pakspelling
    rec = pkg.imports[-index - 1]
    outer = struct.unpack_from('<i', rec, 16)[0]
    orec = pkg.imports[-outer - 1] if outer < 0 else None
    path = pakspelling.spelled(pkg.names[struct.unpack_from('<i', orec, 20)[0]][0],
                               struct.unpack_from('<i', orec, 24)[0]) if orec else None
    return (pkg.names[struct.unpack_from('<i', rec, 8)[0]][0], path,
            (pkg.names[struct.unpack_from('<i', rec, 20)[0]][0],
             struct.unpack_from('<i', rec, 24)[0]))


def effect_gates(files):
    """[(name, ok, detail)] for the effects of every generated preview in `files` ({inner
    path: bytes}); [] when none carries one."""
    from riftlord.core import gamefiles
    from riftlord.core import pakspelling
    from riftlord.missions import modminions
    previews = []
    for k in sorted(files):
        if not k.endswith('.uasset') or k[:-7] + '.uexp' not in files:
            continue
        head = modminions._bytes(files[k])
        if EFFECT_VAR.encode() not in head:
            continue
        pkg = Package(head, modminions._bytes(files[k[:-7] + '.uexp']))
        if effect_exports(pkg):
            previews.append((k, pkg))
    if not previews:
        return []
    src, s_psc, s_node = effect_reference()
    resolved, wired, bad_r, bad_w, count = 0, 0, [], [], 0
    for k, pkg in previews:
        label = k.rsplit('/', 1)[-1][:-7]
        bpgc = _export_of_class(pkg, 'BlueprintGeneratedClass')
        scs = _export_of_class(pkg, 'SimpleConstructionScript')
        cdo = next(i for i in range(len(pkg.exports))
                   if _export_form(pkg, i)[0].startswith('Default__'))
        for psc, node, parent in effect_exports(pkg):
            count += 1
            name = '%s.%s' % (label, _export_form(pkg, psc)[0])
            t = _object_tag(pkg, psc, 'Template') or 0
            cls, path, form = _import_target(pkg, t) if t < 0 else (None, None, None)
            ok = False
            if cls == EFFECT_CLASS and path:
                inner = 'OMD/Content' + path[len('/Game'):] + '.uasset'
                try:
                    blob = (modminions._bytes(files[inner]) if inner in files
                            else gamefiles.package(path)[0])
                    tp = Package(blob)
                    ok = any(_export_form(tp, i) == form and _export_class(tp, i) == EFFECT_CLASS
                             for i in range(len(tp.exports)))
                except (Exception, SystemExit):      # noqa: BLE001
                    ok = False
            if ok:
                resolved += 1
            else:
                bad_r.append('%s: Template %s.%s' % (name, path, form and pakspelling.spelled(*form)))
            problems = []
            if node is None or parent is None:
                problems.append('no SCS_Node, or no slot node lists it')
            else:
                pcls = struct.unpack_from('<i', pkg.exports[psc]['meta'], 0)[0]
                pcdo = struct.unpack_from('<i', pkg.exports[psc]['meta'], 8)[0]
                ncls = struct.unpack_from('<i', pkg.exports[node]['meta'], 0)[0]
                ncdo = struct.unpack_from('<i', pkg.exports[node]['meta'], 8)[0]
                want = {psc: [[bpgc + 1], [t], [pcls, pcdo], [bpgc + 1]],
                        node: [[], [psc + 1, pcls], [ncls, ncdo], [scs + 1]]}
                for i, groups in want.items():
                    if groups_of(pkg, i) != groups:
                        problems.append('%s preloads %s, shipped layout %s'
                                        % (_export_form(pkg, i)[0], groups_of(pkg, i), groups))
                if struct.unpack_from('<i', pkg.exports[psc]['meta'], 12)[0] != bpgc + 1:
                    problems.append('template outer is not the class')
                if _object_tag(pkg, node, 'ComponentClass') != pcls:
                    problems.append('node ComponentClass is not the component class')
                if node + 1 not in groups_of(pkg, parent)[1]:
                    problems.append('slot node does not preload it')
                if node + 1 not in _object_array(pkg, scs, 'AllNodes') \
                        or node + 1 not in groups_of(pkg, scs)[1]:
                    problems.append('the SCS does not list or preload it')
                if node + 1 not in groups_of(pkg, cdo)[0] or psc + 1 not in groups_of(pkg, cdo)[0]:
                    problems.append('the class default does not preload it')
                if pcls not in groups_of(pkg, bpgc)[1]:
                    problems.append('the class does not preload the component class')
                slot = _object_tag(pkg, parent, 'ComponentTemplate') or 0
                slots = {id(e) for e, _c, _m, _a in slot_exports(pkg)}
                if slot <= 0 or id(pkg.exports[slot - 1]) not in slots:
                    problems.append('its parent is not a creature slot')
            if problems:
                bad_w.append('%s: %s' % (name, '; '.join(problems)))
            else:
                wired += 1
    out = [('preview effects: every Template is a %s the game ships or the mission carries '
            '(%d of %d)' % (EFFECT_CLASS, resolved, count), not bad_r, '; '.join(bad_r[:3])),
           ('preview effects: wired as the shipped dioramas wire theirs (%d of %d)'
            % (wired, count), not bad_w, '; '.join(bad_w[:3]))]
    #: the previews and the mod folders they draw on, each path under one FName spelling
    folders = set()
    for _k, pkg in previews:
        for psc, _n, _p in effect_exports(pkg):
            t = _object_tag(pkg, psc, 'Template') or 0
            path = _import_target(pkg, t)[1] if t < 0 else None
            if path and path.startswith('/Game/Mods/'):
                folders.add(path.split('/')[3])
    pool = {k: modminions._bytes(v) for k, v in files.items()
            if k.endswith(('.uasset', '.umap')) and (
                any(k == p for p, _g in previews) or modminions.folder(k) in folders)}
    twice = sorted(p for p, forms in pakspelling.spellings(pool).items() if len(forms) > 1)
    out.append(('preview effects: the previews and the mod packages they name spell each '
                'path one way (%d packages)' % len(pool), not twice, ', '.join(twice[:3])))
    return out


def build(units, new_path, donor=None, counts=None, options=None):
    """A preview Blueprint showing a selection of `units` (F4 rules: `counts` {unit:
    spawns} ranks them; without it, roster order does). Returns (files, report); the
    report lists `effects` added and `warnings` (a mod minion's Preview effect keys that
    could not be used)."""
    unresolved = [u for u in units if creature_for(u) is None]
    if len(unresolved) == len(units):
        return None, {'unresolved': unresolved, 'reason': 'no unit resolved'}
    from riftlord.onslaught import onslaught
    flags = onslaught.proto_flags()
    flyers = {creature_for(u) for u in units if flags.get(u, {}).get('flyer')}
    ground, flying = choose(units, counts, flyers, options)
    placements = layout(ground, flying)
    if not placements:
        return None, {'unresolved': unresolved, 'reason': 'nothing fits the hub frame'}
    wanted = len(placements)
    clean = clean_donors()
    fits = sorted((n, p) for p, n in clean.items() if n >= wanted)
    donor_path = donor or (fits[0][1] if fits else pick_donor(wanted))

    f = mapdata._fetch(donor_path)
    pkg = Package(open(f['.uasset'], 'rb').read(), open(f['.uexp'], 'rb').read())
    pkg.load_payloads()
    slots = slot_exports(pkg)
    placements = placements[:len(slots)]
    names = [s for s, _h in pkg.names]
    filled = []
    for i, (export, _was, mesh_at, anim_at) in enumerate(slots):
        if i >= len(placements):
            # a surplus slot is pushed far below the camera rather than left
            # showing the donor's creature
            export['data'] = set_location(export['data'], names,
                                          (0.0, 0.0, -5000.0))[0]
            continue
        creature, where = placements[i]
        mesh_pkg, mesh_obj, anim_pkg, anim_obj = assets_for(creature)
        mesh_ref = reference(pkg, mesh_pkg, mesh_obj, 'SkeletalMesh')
        anim_ref = reference(pkg, anim_pkg, anim_obj, 'AnimSequence')
        data = bytearray(export['data'])
        struct.pack_into('<i', data, mesh_at, mesh_ref)
        struct.pack_into('<i', data, anim_at, anim_ref)
        data = bytearray(clear_array(bytes(data), names, 'OverrideMaterials'))
        data = bytearray(set_location(bytes(data), names, where)[0])
        data = bytearray(set_yaw(bytes(data), names, yaw_for(where[0]))[0])
        #: the creature's own scale, unmirrored: a donor slot's (a 0.9 kobold, a mirror)
        #: must not carry over to whatever stands there now
        data = bytearray(set_scale(bytes(data), names, scale_of(creature))[0])
        export['data'] = bytes(data)
        filled.append((creature, tuple(round(v) for v in where)))

    #: mod minions' Preview effects, one live particle system per shown creature
    from riftlord.core import pakspelling
    effects, warnings = [], []
    slot_index = {id(e): i for i, e in enumerate(pkg.exports)}
    for i, (export, _was, _m, _a) in enumerate(slots[:len(placements)]):
        creature = placements[i][0]
        effect, warns = effect_for(creature)
        warnings += [w for w in warns if w not in warnings]
        if effect is None or len(effects) >= len(EFFECT_LETTERS):
            continue
        add_effect(pkg, slot_index[id(export)], effect, EFFECT_LETTERS[len(effects)])
        effects.append((creature, effect['path'],
                        pakspelling.spelled(*effect['socket']) if effect['socket'] else None))

    old_object = donor_path.rsplit('/', 1)[-1]
    new_object = new_path.rsplit('/', 1)[-1]
    modtable.retarget(pkg, exact={donor_path: new_path},
                      substring=[(old_object, new_object)])
    return pkg.write(), {'donor': old_object, 'slots': len(slots),
                         'showing': filled, 'unresolved': unresolved,
                         'used': len(placements), 'effects': effects,
                         'warnings': warnings}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--units', nargs='+', required=True)
    ap.add_argument('--path', default='/Game/Mods/MyMod/MyMod_Enemies01')
    ap.add_argument('--out')
    args = ap.parse_args()

    files, report = build(args.units, args.path)
    if files is None:
        raise SystemExit('could not build a preview: %s' % report)
    print('donor       %s (%d slots, %d used)'
          % (report['donor'], report['slots'], report['used']))
    for creature, where in report['showing']:
        print('   %-22s at %s' % (creature, where))
    if report['unresolved']:
        print('unresolved  %s' % ', '.join(report['unresolved']))
    back = Package(*files)
    back.load_payloads()
    names = [s for s, _h in back.names]
    got = sorted(set(c for _e, c, _m, _a in slot_exports(back)))
    print('re-read     export names %s' % got)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        base = os.path.join(args.out, args.path.rsplit('/', 1)[-1])
        open(base + '.uasset', 'wb').write(files[0])
        open(base + '.uexp', 'wb').write(files[1])
        print('wrote %s.uasset (%d) and .uexp (%d)'
              % (base, len(files[0]), len(files[1])))


if __name__ == '__main__':
    main()
