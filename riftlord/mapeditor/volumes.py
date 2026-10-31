"""The level's collision and kill volumes: what they block, where they are, and edits.

`level_volumes(level)` reads the volumes of a level's persistent level with their
world boxes. `crossed(level, chains)` names the ones a ground route walks through.
`clean(raw)` validates a plan's `volumes` edits and `apply(builder, plan, level, report)`
writes them, and moves out of play any volume a ground route crosses that the plan
leaves alone.
"""

import functools
import math
import struct

#: class -> (label, what it stops). `None` for BlockingVolume: its profile decides.
CLASSES = {
    'BlockingVolume': ('Blocking volume', None),
    'OMDPlayerBlockingVolume': ('Player blocking volume', 'players'),
    'OMDCharacterKillVolume': ('Kill volume', 'kill'),
    'OMDCameraBlockingVolume': ('Camera blocking volume', 'camera'),
    'OMDTrapBlockingVolume': ('Trap blocking volume', 'traps'),
}

#: BlockingVolume collision profile -> what it stops (DefaultEngine.ini). A profile
#: not listed stops everything, as BlockAll and InvisibleWall do. Measured on the 165
#: shipped BlockingVolumes: OMDPlayerBlockingVolume (41) ignores every channel but the
#: player character's, Pawn (enemies) included; a Custom one (52) is decided by its own
#: responses (custom_stops).
PROFILE_STOPS = {'OMDTrap': 'traps', 'OMDBlockAllNoTrap': 'everyone',
                 'OMDPlayerBlockingVolume': 'players'}
#: Classes whose volumes the plan can make stop players only or everyone: the level volume
#: is parked and the donor of the chosen kind stands in its place (apply).
BLOCK_CLASSES = ('BlockingVolume', 'OMDPlayerBlockingVolume')
#: The channels enemies and players move on.
ENEMY_CHANNEL = 'Pawn'
PLAYER_CHANNEL = 'OMDPlayerCharacter'


def custom_stops(responses):
    """What a Custom-profile BlockingVolume stops, from its {channel: response}: players
    only when it ignores Pawn and not the player character (13 shipped), everyone when it
    blocks Pawn (34 shipped), else everyone as before (5 shipped ignore both)."""
    if responses.get(ENEMY_CHANNEL) == 'ECR_Ignore' and \
            responses.get(PLAYER_CHANNEL) != 'ECR_Ignore':
        return 'players'
    return 'everyone'


def _tags(data, names, start, end):
    """[(name, kind, payload offset, size)] of tagged properties from `start`, and the
    offset just past their None."""
    out, o = [], start
    while o + 8 <= end:
        ni = struct.unpack_from('<i', data, o)[0]
        if not 0 <= ni < len(names):
            break
        if names[ni] == 'None':
            return out, o + 8
        ti = struct.unpack_from('<i', data, o + 8)[0]
        kind = names[ti] if 0 <= ti < len(names) else ''
        size = struct.unpack_from('<i', data, o + 16)[0]
        head = 25
        if kind == 'BoolProperty':
            head, size = 26, 0
        elif kind == 'StructProperty':
            head = 49
        elif kind in ('ArrayProperty', 'ByteProperty', 'EnumProperty'):
            head = 33
        out.append((names[ni], kind, o + head, size))
        o += head + size
    return out, o


def responses_of(cdata, names, body):
    """{channel: response (ECR_...)} of a BodyInstance's CollisionResponses
    (`body` from meshcat._walk of the BodyInstance struct)."""
    from riftlord.formats.omd3 import meshcat
    if 'CollisionResponses' not in body:
        return {}
    _k, at, size = body['CollisionResponses']
    cr = meshcat._walk(cdata, names, at, at + size)
    if 'ResponseArray' not in cr:
        return {}
    _k, ro, rs = cr['ResponseArray']
    count = struct.unpack_from('<i', cdata, ro)[0]
    o, out = ro + 4 + 49, {}
    for _ in range(max(count, 0)):
        tags, o = _tags(cdata, names, o, ro + rs)
        got = {name: (kind, at2, s) for name, kind, at2, s in tags}

        def fname(key):
            if key not in got or got[key][2] != 8:
                return None
            n = struct.unpack_from('<i', cdata, got[key][1])[0]
            return names[n] if 0 <= n < len(names) else None
        if fname('Channel'):
            out[fname('Channel')] = fname('Response')
    return out

#: Plain wording per kind, for the editor.
STOPS_TEXT = {
    'everyone': 'stops players and enemies',
    'players': 'stops players only',
    'kill': 'kills characters that enter it',
    'camera': 'stops the camera',
    'traps': 'stops trap placement',
}

#: Kinds that stand in a walking character's or its camera's way, so a ground route
#: must not cross them.
IN_THE_WAY = ('everyone', 'players', 'kill', 'camera')

#: Where a volume moved out of play goes (the same place removed actors are parked).
PARKED = (0.0, 0.0, -50000.0)

#: Heights above a route point tested for a crossing: a character's feet to head (uu).
BODY_HEIGHTS = (40.0, 120.0, 190.0)

#: A walking character's radius (DefaultEngine.ini AgentRadius), added around a volume.
BODY_RADIUS = 42.0

#: Spacing of the samples taken along a route leg (uu).
SAMPLE_STEP = 25.0

#: A kill volume's two switches: plan key -> AOMDCharacterKillVolume property. Both are
#: true unless stored (88 shipped kill volumes; `bKillMinions` false on 10 in DLC_02,
#: Scenario_11 and 18, `bKillPlayers` false on 2 in Sand_02: q1_report.txt).
KILL_FLAGS = {'killMinions': 'bKillMinions', 'killPlayers': 'bKillPlayers'}
KILL_CLASS = 'OMDCharacterKillVolume'


def _package(level):
    from riftlord.core import gamefiles
    from riftlord.build.navigation import navpatch
    from riftlord.formats.ue.uassetio import Package
    ua, ux = gamefiles.package(navpatch.map_path(level))
    pkg = Package(ua, ux)
    pkg.load_payloads()
    return pkg


def _convex_points(pkg, names, index):
    """The convex collision vertices of one BodySetup export, in its local space."""
    from riftlord.formats.omd3 import meshcat
    from riftlord.formats.omd3 import meshhull
    data = pkg.exports[index - 1]['data']
    top = meshcat._walk(data, names, 0, len(data))
    if 'AggGeom' not in top:
        return []
    _k, at, size = top['AggGeom']
    agg = meshcat._walk(data, names, at, at + size)
    if 'ConvexElems' not in agg:
        return []
    _ak, ia, asize = agg['ConvexElems']
    count = struct.unpack_from('<i', data, ia)[0]
    o, out = ia + 4 + meshhull.INNER_TAG, []
    for _ in range(max(count, 0)):
        el, o = meshhull._walk_to_end(data, names, o, ia + asize)
        if 'VertexData' not in el:
            continue
        _vk, va, _vs = el['VertexData']
        n = struct.unpack_from('<i', data, va)[0]
        out += [struct.unpack_from('<3f', data, va + 4 + meshhull.INNER_TAG + i * 12)
                for i in range(n)]
    return out


@functools.lru_cache(maxsize=8)
def level_volumes(level):
    """[{index, name, cls, label, stops, stopsText, profile, location, rotation, scale,
    min, max (local box), matrix (column-major 4x4), worldMin, worldMax}] for the
    volumes of CLASSES in the level's persistent level."""
    from riftlord.build import levelbuild
    from riftlord.formats.omd3 import meshcat
    from riftlord.formats.omd3 import omd3level
    from riftlord.onslaught import onslaught
    pkg = _package(level)
    names = [n for n, _h in pkg.names]
    #: export names as the game spells them (with the FName number)
    shown = [str(e.get('name')) for e in onslaught.load_map(level)[0]['exports']]
    out = []
    for i, ex in enumerate(pkg.exports):
        cls = levelbuild.resolve(pkg, struct.unpack_from('<i', ex['meta'], 0)[0])[1]
        if cls not in CLASSES:
            continue
        data = ex['data']
        props = meshcat._walk(data, names, 0, len(data))
        if 'RootComponent' not in props:
            continue
        root = struct.unpack_from('<i', data, props['RootComponent'][1])[0]
        if not 0 < root <= len(pkg.exports):
            continue
        cdata = pkg.exports[root - 1]['data']
        cprops = meshcat._walk(cdata, names, 0, len(cdata))

        def vec(field, default):
            return (struct.unpack_from('<3f', cdata, cprops[field][1])
                    if field in cprops else default)
        loc = vec('RelativeLocation', (0.0, 0.0, 0.0))
        rot = vec('RelativeRotation', (0.0, 0.0, 0.0))
        scale = vec('RelativeScale3D', (1.0, 1.0, 1.0))
        profile = None
        responses = {}
        if 'BodyInstance' in cprops:
            _k, at, size = cprops['BodyInstance']
            body = meshcat._walk(cdata, names, at, at + size)
            if 'CollisionProfileName' in body:
                n = struct.unpack_from('<i', cdata, body['CollisionProfileName'][1])[0]
                profile = names[n] if 0 <= n < len(names) else None
            responses = responses_of(cdata, names, body)
        points = []
        if 'BrushBodySetup' in cprops:
            setup = struct.unpack_from('<i', cdata, cprops['BrushBodySetup'][1])[0]
            if 0 < setup <= len(pkg.exports):
                points = _convex_points(pkg, names, setup)
        if not points:
            continue
        label, stops = CLASSES[cls]
        if stops is None:
            stops = (custom_stops(responses) if profile == 'Custom'
                     else PROFILE_STOPS.get(profile, 'everyone'))
        #: a volume of BLOCK_CLASSES with a box brush centred on its actor (161 of 165
        #: shipped BlockingVolumes) can be made to stop players only or everyone (BLOCKS)
        box = len({tuple(round(c, 2) for c in p) for p in points}) == 8 and all(
            abs(max(p[a] for p in points) + min(p[a] for p in points)) < 1 for a in range(3))
        flags = {}
        if cls == KILL_CLASS:
            for key, prop in KILL_FLAGS.items():
                flags[key] = bool(data[props[prop][1] - 2]) if prop in props else True
        lo = [min(p[a] for p in points) for a in range(3)]
        hi = [max(p[a] for p in points) for a in range(3)]
        m = omd3level._matrix(loc, rot, scale)
        corners = [_apply(m, (x, y, z)) for x in (lo[0], hi[0]) for y in (lo[1], hi[1])
                   for z in (lo[2], hi[2])]
        out.append({
            'index': i + 1, 'name': shown[i], 'cls': cls,
            'label': label, 'stops': stops, 'stopsText': STOPS_TEXT[stops],
            'profile': profile, 'location': [round(v, 3) for v in loc],
            'rotation': [round(v, 3) for v in rot], 'scale': [round(v, 4) for v in scale],
            'min': [round(v, 2) for v in lo], 'max': [round(v, 2) for v in hi],
            'matrix': omd3level._flat(m),
            'worldMin': [round(min(c[a] for c in corners), 1) for a in range(3)],
            'worldMax': [round(max(c[a] for c in corners), 1) for a in range(3)],
            'blocks': stops if cls in BLOCK_CLASSES and box and stops in BLOCKS else None,
            **flags})
    return out


def _apply(m, p):
    return [sum(m[r][k] * p[k] for k in range(3)) + m[r][3] for r in range(3)]


def _local(vol, p, location=None):
    """`p` in the volume's own space, with the volume at `location` (default: its own)."""
    from riftlord.formats.omd3 import omd3level
    m = omd3level._matrix(location or vol['location'], vol['rotation'], vol['scale'])
    d = [p[r] - m[r][3] for r in range(3)]
    out = []
    for c in range(3):
        axis = [m[r][c] for r in range(3)]
        length2 = sum(a * a for a in axis) or 1.0
        out.append(sum(d[r] * axis[r] for r in range(3)) / length2)
    return out, m


def _inside(vol, p, location=None):
    q, m = _local(vol, p, location)
    for c in range(3):
        stretch = math.sqrt(sum(m[r][c] ** 2 for r in range(3))) or 1.0
        pad = BODY_RADIUS / stretch
        if not vol['min'][c] - pad <= q[c] <= vol['max'][c] + pad:
            return False
    return True


def leg_crosses(vol, a, b, location=None):
    """True when a character walking from `a` to `b` passes through `vol`."""
    steps = max(1, int(math.dist(a, b) / SAMPLE_STEP))
    for s in range(steps + 1):
        t = s / steps
        foot = [a[k] + (b[k] - a[k]) * t for k in range(3)]
        for h in BODY_HEIGHTS:
            if _inside(vol, (foot[0], foot[1], foot[2] + h), location):
                return True
    return False


def crossed(level, chains, skip=()):
    """[(volume, a, b)] for each in-the-way volume a chain leg crosses, first leg only."""
    out = []
    for vol in level_volumes(level):
        if vol['stops'] not in IN_THE_WAY or vol['name'] in skip:
            continue
        #: a kill volume that spares enemies is not in an enemy route's way
        if vol.get('killMinions') is False:
            continue
        for chain in chains:
            hit = next(((a, b) for a, b in zip(chain, chain[1:])
                        if leg_crosses(vol, a, b)), None)
            if hit:
                out.append((vol, hit[0], hit[1]))
                break
    return out


#: The smallest a volume's scale may be on any axis.
MIN_SCALE = 0.01


def _matrix_ok(m):
    """Whether `m` is a column-major 4x4 of finite numbers whose axes are all longer than
    MIN_SCALE and not mirrored."""
    if not isinstance(m, (list, tuple)) or len(m) != 16:
        return False
    try:
        vals = [float(v) for v in m]
    except (TypeError, ValueError):
        return False
    if not all(math.isfinite(v) for v in vals):
        return False
    cols = [vals[c * 4:c * 4 + 3] for c in range(3)]
    if any(math.sqrt(sum(v * v for v in col)) < MIN_SCALE for col in cols):
        return False
    a, b, c = cols
    det = (a[0] * (b[1] * c[2] - b[2] * c[1]) - b[0] * (a[1] * c[2] - a[2] * c[1])
           + c[0] * (a[1] * b[2] - a[2] * b[1]))
    return det > 0


def edited_transform(vol, edit):
    """(location, rotation, scale) of `vol` (level_volumes) after plan edit `edit`: its
    `matrix` (location, turn and size), else `at` (location only), else as shipped."""
    from riftlord.build import apply as _apply
    edit = edit or {}
    if edit.get('matrix'):
        loc, rot, scale = _apply.transform_of(edit['matrix'])
        return list(loc), list(rot), list(scale)
    return (list(edit.get('at') or vol['location']), list(vol['rotation']),
            list(vol['scale']))


def _edited(vol, edit):
    """`vol` with its transform after `edit`."""
    loc, rot, scale = edited_transform(vol, edit)
    return dict(vol, location=loc, rotation=rot, scale=scale)


# -- volumes the plan adds --------------------------------------------------------------

#: What a blocking volume can be made to stop (the editor's Blocks) -> the shipped
#: BlockingVolume that kind is lifted from (level, export name): a box brush centred on its
#: actor, unturned, carrying the collision exactly as the shipped ones of that kind do.
#: Lifted whole: the actor, its BrushComponent, the brush Model and the BodySetup.
#:   players:  BodyInstance with profile OMDPlayerBlockingVolume and its responses, as the
#:             41 shipped ones set it (every channel ignored but the player character's)
#:   everyone: no BodyInstance, so BlockingVolume's BlockAll, as 58 shipped ones
BLOCKS = {'players': ('Scenario_04', 'BlockingVolume12'),
          'everyone': ('Scenario_02', 'BlockingVolume7_3')}
#: What a new volume stops.
NEW_BLOCKS = 'players'
#: What an added volume whose plan entry does not say stops (entries made before Blocks).
ADDED_DEFAULT = 'everyone'
#: The box an added volume's matrix places and scales: 200 uu, centred on the actor.
BOX = ([-100.0, -100.0, -100.0], [100.0, 100.0, 100.0])
#: The BlockAll donor (the walkable-graph rebuild's blocking brush).
DONOR_LEVEL, DONOR_NAME = BLOCKS['everyone']
#: Plan names of added volumes: this prefix and a number.
ADDED_PREFIX = 'RiftLordVolume'
#: Export name of the volume that stands in for a level volume whose Blocks the plan
#: changes: this prefix and the level volume's export index.
SWAPPED_PREFIX = 'RiftLordVolumeR'


@functools.lru_cache(maxsize=4)
def donor(blocks='everyone'):
    """The level_volumes entry of the donor of kind `blocks` (BLOCKS), or None."""
    level, name = BLOCKS[blocks]
    return next((v for v in level_volumes(level) if v['name'] == name
                 and v['cls'] == 'BlockingVolume' and v['blocks'] == blocks), None)


def donor_view():
    """The editor's added volume: {cls, label, min, max (BOX), blocks: {kind: text}, new}."""
    if not all(donor(k) for k in BLOCKS):
        return None
    return {'cls': 'BlockingVolume', 'label': CLASSES['BlockingVolume'][0],
            'min': BOX[0], 'max': BOX[1], 'new': NEW_BLOCKS,
            'blocks': {k: STOPS_TEXT[k] for k in BLOCKS}}


def _donor_scale(scale, lo, hi, d):
    """The scale that makes donor `d`'s brush the box lo..hi under `scale`."""
    return [scale[a] * (hi[a] - lo[a]) / max(d['max'][a] - d['min'][a], 1e-6)
            for a in range(3)]


def added_volume(edit):
    """A level_volumes-shaped entry for an added volume (`edit` from clean): BOX under its
    matrix, stopping its `blocks`; None without a donor."""
    blocks = edit.get('blocks') or ADDED_DEFAULT
    d = donor(blocks)
    if not d or not edit.get('matrix'):
        return None
    loc, rot, scale = edited_transform(d, edit)
    return dict(d, name=edit['name'], index=None, location=loc, rotation=rot, scale=scale,
                min=list(BOX[0]), max=list(BOX[1]), stops=blocks,
                stopsText=STOPS_TEXT[blocks], blocks=blocks, added=True)


def _lift_donor(builder, name, blocks, loc, rot, scale, lo, hi, report):
    """Lift the donor of kind `blocks` as `name`, its brush made the box lo..hi under
    `scale`, at `loc` turned `rot`. Its package index, or None."""
    from riftlord.build import apply as _apply
    d = donor(blocks)
    if not d:
        report['skipped'].append('%s: no %s donor volume %s in %s'
                                 % ((name, blocks) + BLOCKS[blocks][::-1]))
        return None
    return _apply._add(builder, d['cls'], loc, name, rot, report,
                       scale=_donor_scale(scale, lo, hi, d),
                       donor=(_package(BLOCKS[blocks][0]), d['index'] - 1))


def clean(raw):
    """A plan's `volumes` list, validated: [{name, at? | matrix?, gone?, blocks?,
    killMinions?, killPlayers?}] (`matrix`: column-major 4x4 world transform, from a turn
    or resize; `blocks`: a BLOCKS kind) for the level's own, and [{name: ADDED_PREFIX<n>,
    added: true, matrix, blocks?}] for added ones; other entries dropped."""
    out = []
    names = set()
    for e in raw if isinstance(raw, list) else []:
        if not isinstance(e, dict) or not isinstance(e.get('name'), str):
            continue
        if e.get('added'):
            if (e['name'].startswith(ADDED_PREFIX) and e['name'][len(ADDED_PREFIX):].isdigit()
                    and e['name'] not in names and _matrix_ok(e.get('matrix'))):
                names.add(e['name'])
                out.append({'name': e['name'], 'added': True,
                            'matrix': [float(v) for v in e['matrix']]})
                if e.get('blocks') in BLOCKS:
                    out[-1]['blocks'] = e['blocks']
            continue
        entry = {'name': e['name']}
        if e.get('gone'):
            entry['gone'] = True
        elif _matrix_ok(e.get('matrix')):
            entry['matrix'] = [float(v) for v in e['matrix']]
        elif isinstance(e.get('at'), (list, tuple)) and len(e['at']) == 3:
            entry['at'] = [float(v) for v in e['at']]
        if e.get('blocks') in BLOCKS and not entry.get('gone'):
            entry['blocks'] = e['blocks']
        for key in KILL_FLAGS:
            if isinstance(e.get(key), bool) and not entry.get('gone'):
                entry[key] = e[key]
        if len(entry) == 1:
            continue
        out.append(entry)
    return out


def _write_kill_flags(builder, index, vol, edit, report):
    """A kill volume's switches: a false one stored, a true one removed (the class
    default), as the shipped levels store them."""
    for key, prop in KILL_FLAGS.items():
        if key not in edit:
            continue
        if vol['cls'] != KILL_CLASS:
            report['skipped'].append('%s is not a kill volume' % vol['name'])
            return
        if edit[key]:
            builder.clear_property(index, prop)
        else:
            builder.set_bool(index, prop, False)
        report['notes'].append('%s %s %s' % (
            vol['name'], {'killMinions': 'kills enemies:',
                          'killPlayers': 'kills heroes:'}[key], 'yes' if edit[key] else 'no'))


def _fmt(p):
    return '(%.0f, %.0f, %.0f)' % tuple(p[:3])


def apply(builder, plan, level_name, report):
    """Write the plan's volume edits, then move out of play every in-the-way volume a
    ground route crosses that the plan does not edit."""
    from riftlord.build import levelbuild
    edits = {e['name']: e for e in clean((plan or {}).get('volumes'))}
    vols = {v['name']: v for v in level_volumes(level_name)}
    pkg = builder.pkg

    def export_of(vol):
        i = vol['index']
        cls = levelbuild.resolve(
            pkg, struct.unpack_from('<i', pkg.exports[i - 1]['meta'], 0)[0])[1] \
            if i <= len(pkg.exports) else None
        if cls == vol['cls']:
            return i
        return None

    added = {name: e for name, e in edits.items() if e.get('added')}
    edits = {name: e for name, e in edits.items() if not e.get('added')}
    for name, e in sorted(added.items()):
        vol = added_volume(e)
        if not vol:
            report['skipped'].append('%s: no %s donor volume' % (name, e.get('blocks')))
            continue
        got = _lift_donor(builder, name, vol['blocks'], vol['location'], vol['rotation'],
                          vol['scale'], vol['min'], vol['max'], report)
        if got:
            report['notes'].append('%s added at %s, turned (%.1f, %.1f, %.1f), %s; it %s'
                                   % ((name, _fmt(vol['location'])) + tuple(vol['rotation'])
                                      + (' x '.join('%.0f' % ((vol['max'][a] - vol['min'][a])
                                                              * vol['scale'][a])
                                                    for a in range(3)), vol['stopsText'])))
    for name, e in edits.items():
        vol = vols.get(name)
        index = export_of(vol) if vol else None
        if index is None:
            report['skipped'].append('volume %s is not in this level' % name)
            continue
        if e.get('gone'):
            builder.place(index, location=PARKED)
            report['notes'].append('%s removed (%s)' % (name, vol['stopsText']))
            continue
        #: made to stop the other kind: parked, and that kind's donor stands in its place,
        #: its brush the same box
        if e.get('blocks') and vol.get('blocks') and e['blocks'] != vol['blocks']:
            loc, rot, scale = edited_transform(vol, e)
            builder.place(index, location=PARKED)
            got = _lift_donor(builder, SWAPPED_PREFIX + str(vol['index']), e['blocks'], loc,
                              rot, scale, vol['min'], vol['max'], report)
            if got:
                report['notes'].append('%s now %s (replaced by %s%d)' % (
                    name, STOPS_TEXT[e['blocks']], SWAPPED_PREFIX, vol['index']))
            continue
        if 'matrix' in e:
            from riftlord.build import apply as _apply
            loc, rot, scale = edited_transform(vol, e)
            _apply.place_whole(builder, index, location=tuple(loc), rotation=tuple(rot),
                               scale=tuple(scale))
            report['notes'].append('%s placed at %s, turned (%.1f, %.1f, %.1f), scale '
                                   '(%.3f, %.3f, %.3f)' % ((name, _fmt(loc)) + tuple(rot)
                                                           + tuple(scale)))
        elif 'at' in e:
            builder.place(index, location=tuple(e['at']))
            report['notes'].append('%s moved to %s' % (name, _fmt(e['at'])))
        _write_kill_flags(builder, index, vol, e, report)
    try:
        from riftlord.mapeditor import planactors
        chains = planactors.ground_chains(plan, planactors.level_actors(level_name))
    except Exception as exc:                         # noqa: BLE001
        report['skipped'].append('could not check volumes against the routes: %s: %s'
                                 % (type(exc).__name__, exc))
        return
    for vol, a, b in crossed(level_name, chains, skip=set(edits)):
        index = export_of(vol)
        if index is None:
            continue
        builder.place(index, location=PARKED)
        report['notes'].append('%s moved out of play: it %s and the ground route from %s '
                               'to %s crosses it' % (vol['name'], vol['stopsText'],
                                                     _fmt(a), _fmt(b)))
    for name, e in edits.items():
        vol = vols.get(name)
        #: one the plan sets Blocks on is in a route's way only when it stops enemies
        in_way = (e['blocks'] == 'everyone' if e.get('blocks') and vol and vol.get('blocks')
                  else vol and vol['stops'] in IN_THE_WAY)
        if vol and not e.get('gone') and in_way and \
                e.get('killMinions', vol.get('killMinions', True)) and any(
                leg_crosses(_edited(vol, e), a, b)
                for chain in chains for a, b in zip(chain, chain[1:])):
            report['notes'].append('%s, where the plan puts it, still stands across a '
                                   'ground route: it %s' % (name, STOPS_TEXT[
                                       e.get('blocks') or vol['stops']]))
    for name, e in sorted(added.items()):
        vol = added_volume(e)
        if vol and vol['blocks'] == 'everyone' and any(
                leg_crosses(vol, a, b) for chain in chains for a, b in zip(chain, chain[1:])):
            report['notes'].append('%s stands across a ground route: it %s'
                                   % (name, vol['stopsText']))
