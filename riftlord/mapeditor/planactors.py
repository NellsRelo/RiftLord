"""Gameplay actors in a Rift Lord plan: the shared path graph, spawners, player starts
and edits to the level's own actors. The schema is riftlord/PLAN-ACTORS.md.

    migrate(plan)            version 1 -> version 2, in place
    clean(data)              the actor keys of a plan, coerced and checked
    check(plan, level_name)  (errors, warnings) for the path graph and the counts
    emit(builder, ...)       writes them into a level copy (called by apply.apply_plan)
"""

import math
import struct

from riftlord.build import levelbuild

#: The version this module writes.
VERSION = 2

#: Where a removed actor goes: far below any level, and finite.
PARKED = (0.0, 0.0, -50000.0)

#: More branches than this out of one point is accepted, with a warning. Shipped
#: levels have up to four (ScenarioDLC_03 OMDPathPoint0 and OMDPathPoint39).
MAX_SHIPPED_FANOUT = 4

#: The range shipped levels' branch weights add up to at one point (0.4 on
#: Scenario_06, 2.0 on ScenarioDLC_03, Scenario_08 and Scenario_Sand_03; 669 of the
#: 696 points with branches add up to exactly 1.0). A sum outside it is accepted, with a warning.
SHIPPED_WEIGHT_SUM = (0.4, 2.0)

#: Slack on the weight-sum range, for rounding.
WEIGHT_TOLERANCE = 0.01

#: An edited location within this many units of the shipped one is not a move.
SAME_PLACE = 1.0

#: When a removed point is spliced out, a branch weight below this is dropped.
MIN_WEIGHT = 1e-4

#: Player start phases, and the two flags each one writes (None = class default).
PHASES = {
    'breaks': {'bAllowDuringBreaks': None, 'bAllowDuringWaves': False},
    'waves': {'bAllowDuringBreaks': False, 'bAllowDuringWaves': None},
    'both': {'bAllowDuringBreaks': None, 'bAllowDuringWaves': None},
}

#: The phase of an added player start that names none.
DEFAULT_PHASE = 'breaks'

#: Path point fields: name -> (kind, default).
POINT_FIELDS = {
    'reachRadius': ('float', 300.0),
    'formationSize': ('int', 3),
    'formationSpacing': ('float', 0.0),
    'flightHeight': ('float', 0.0),
    'flyersOnly': ('bool', False),
    'groundOnly': ('bool', False),
}

KINDS = ('spawner', 'rift', 'door', 'point', 'playerStart')

#: How far a rift's exit point stands below the rift (uu): the median over the 33 shipped
#: levels, each with one rift and its exit point 160 to 364 below it and within 21 uu across.
RIFT_OVER_EXIT = 258.2

#: BP_OMDRift's RiftEnterArea, the capsule that takes an enemy into the rift (its class
#: default object): radius, half-height, and how far its centre stands below the rift (uu).
#: Every shipped exit point stands inside it.
RIFT_ENTER_RADIUS = 200.0
RIFT_ENTER_HALF = 400.0
RIFT_ENTER_DROP = 200.0

#: The spawner class of an unstable rift (Scenario_18's red rifts). Lifted from
#: Scenario_18 with `bIsActive` false; the Mission Events piece opens and closes it
#: (riftlord/missionevents.py).
UNSTABLE_CLASS = 'BP_OMDAISpawner_VorwickRift_C'


# -- cleaning ----------------------------------------------------------------

def _vec(value):
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    try:
        return [round(float(v), 2) for v in value[:3]]
    except (TypeError, ValueError):
        return None


def _num(value, default=0.0):
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return default


def ref(value):
    """A point or door reference: an int id, a level export name, or None."""
    if value in (None, ''):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value)
    if text.lstrip('-').isdigit():
        return int(text)
    return text


def branches(raw):
    """[{to, weight}] with `to` an id, a level point name, or 'rift'."""
    out = []
    for b in raw or []:
        if not isinstance(b, dict):
            continue
        to = 'rift' if str(b.get('to')) == 'rift' else ref(b.get('to'))
        if to is None:
            continue
        out.append({'to': to, 'weight': _num(b.get('weight', 1.0), 1.0)})
    return out


def clean_point(raw, next_id):
    out = {'id': int(raw.get('id') or next_id), 'at': _vec(raw.get('at')) or [0.0] * 3}
    for field, (kind, default) in POINT_FIELDS.items():
        value = raw.get(field, default)
        try:
            out[field] = (int(value) if kind == 'int' else
                          bool(value) if kind == 'bool' else round(float(value), 3))
        except (TypeError, ValueError):
            out[field] = default
    out['next'] = branches(raw.get('next'))
    return out


def migrate(plan):
    """Turn a version 1 plan (points nested under spawners) into version 2, in place."""
    if plan.get('version', 1) >= VERSION and 'points' in plan:
        plan.setdefault('playerStarts', [])
        return plan
    points = list(plan.get('points') or [])
    for sp in plan.get('spawners') or []:
        nested = sp.pop('points', None) or []
        nested = [{'at': p} if isinstance(p, (list, tuple)) else p for p in nested]
        nested = [p for p in nested if isinstance(p, dict)]
        if nested:
            first = next((p for p in nested if p.get('isStart')), nested[0])
            key = 'flyerStart' if (sp.get('kind') or 'flyer') == 'flyer' else 'start'
            if sp.get(key) in (None, '') and first.get('id') is not None:
                sp[key] = first.get('id')
        for p in nested:
            p.pop('isStart', None)
            p.pop('isExit', None)
            points.append(p)
    plan['points'] = points
    plan.setdefault('playerStarts', [])
    plan['version'] = VERSION
    return plan


def clean(data):
    """{points, spawners, playerStarts, wiring} from a submitted plan, version 2.

    Raises ValueError on anything malformed enough that the emitter could not act on
    it. Version 1 input is migrated first.
    """
    data = migrate(dict(data, spawners=[dict(s) for s in data.get('spawners') or []
                                        if isinstance(s, dict)]))
    points = []
    for raw in data.get('points') or []:
        if isinstance(raw, dict):
            points.append(clean_point(raw, len(points) + 1))
    spawners = []
    for raw in data.get('spawners') or []:
        kind = raw.get('kind') if raw.get('kind') in ('flyer', 'ground') else 'flyer'
        try:
            index = int(raw.get('index') or 0)
            per_row = int(raw.get('maxSpawnPerRow') or 0)
        except (TypeError, ValueError):
            raise ValueError('spawner %s: index and maxSpawnPerRow must be whole numbers'
                             % raw.get('id'))
        spawners.append({
            'id': int(raw.get('id') or len(spawners) + 1),
            'at': _vec(raw.get('at')) or [0.0] * 3,
            'rotation': _num(raw.get('rotation')),
            'kind': kind, 'index': index, 'maxSpawnPerRow': per_row,
            'door': ref(raw.get('door')),
            'start': ref(raw.get('start')),
            'flyerStart': ref(raw.get('flyerStart')),
        })
        #: an unstable rift: a ground spawner of the rift class, no door
        if raw.get('unstable'):
            spawners[-1].update(unstable=True, kind='ground', door=None)
        #: turned by hand: its rotation is written as it is (riftlord/mapeditor/spawnerfacing.py)
        if raw.get('turned'):
            spawners[-1]['turned'] = True
    starts = []
    for raw in data.get('playerStarts') or []:
        if not isinstance(raw, dict):
            continue
        phase = raw.get('phase') if raw.get('phase') in PHASES else DEFAULT_PHASE
        starts.append({'id': int(raw.get('id') or len(starts) + 1),
                       'at': _vec(raw.get('at')) or [0.0] * 3,
                       'rotation': _num(raw.get('rotation')), 'phase': phase})
    wiring = []
    for raw in data.get('wiring') or []:
        if not isinstance(raw, dict) or not raw.get('name'):
            continue
        entry = {'name': str(raw['name']), 'kind': str(raw.get('kind') or ''),
                 'gone': bool(raw.get('gone'))}
        at = _vec(raw.get('at'))
        if at is not None and not (entry['gone'] and at == [0.0, 0.0, 0.0]):
            entry['at'] = at
        if raw.get('rotation') is not None:
            entry['rotation'] = _num(raw.get('rotation'))
        if 'next' in raw and raw['next'] is not None:
            entry['next'] = branches(raw['next'])
        for key in ('start', 'flyerStart', 'door'):
            if key in raw:
                entry[key] = ref(raw[key])
        if raw.get('phase') in PHASES:
            entry['phase'] = raw['phase']
        wiring.append(entry)
    return {'version': VERSION, 'points': points, 'spawners': spawners,
            'playerStarts': starts, 'wiring': wiring}


# -- the level as it ships ---------------------------------------------------

def level_actors(level_name):
    """What the level ships: {'points', 'spawners', 'doors', 'exit', 'starts'}.

    `points` maps export name -> {index, location, next (names), exit, start}. A point
    with no stored PointIndex has the class default, 0.
    """
    from riftlord.onslaught import onslaught
    rep = onslaught.map_report(level_name)
    raw = rep['points']
    index_of = {n: (r['index'] if isinstance(r.get('index'), int) else 0)
                for n, r in raw.items()}
    by_index = {}
    for n, i in index_of.items():
        by_index.setdefault(i, n)
    points = {}
    for n, r in raw.items():
        points[n] = {
            'index': index_of[n],
            'location': tuple(r.get('location') or (0.0, 0.0, 0.0)),
            'next': [{'to': by_index.get(b.get('PointIndex'), b.get('PointIndex')),
                      'weight': float(b.get('BranchWeight', 1.0))}
                     for b in (r.get('next') or [])],
            'exit': bool(r.get('exit')), 'start': bool(r.get('start'))}
        if r.get('reachRadius') is not None:
            points[n]['reachRadius'] = r['reachRadius']
    exit_name = next((n for n, p in points.items() if p['exit']), None)
    spawners = {}
    for sp in rep['spawners']:
        spawners[sp['export']] = {
            'index': sp.get('index'),
            'door': (sp.get('door') or '').split('.')[-1] or None,
            'start': (sp.get('start') or '').split('.')[-1] or None,
            'flyerStart': (sp.get('flyer') or '').split('.')[-1] or None,
            'location': sp.get('location')}
    return {'points': points, 'spawners': spawners,
            'doors': dict(rep.get('doors') or {}), 'exit': exit_name,
            'starts': level_player_starts(level_name)}


def level_player_starts(level_name):
    """[{export, location, rotation (yaw), phase}] for the level's own player starts."""
    from riftlord.onslaught import onslaught
    info, uexp, hdr = onslaught.load_map(level_name)
    out, cache = [], {}
    for e in info['exports']:
        if 'PlayerStart' not in str(e.get('class') or e.get('cls')):
            continue
        props = {p[0]: p for p in onslaught.read_props(info, uexp, hdr, e)}
        breaks = not ('bAllowDuringBreaks' in props and not props['bAllowDuringBreaks'][2])
        waves = not ('bAllowDuringWaves' in props and not props['bAllowDuringWaves'][2])
        phase = 'both' if breaks and waves else 'breaks' if breaks else 'waves'
        try:
            location = onslaught.actor_location(info, uexp, hdr, e, cache)
        except Exception:                            # noqa: BLE001
            location = None
        out.append({'export': str(e.get('name')), 'location': location,
                    'rotation': _root_rotation(onslaught, info, uexp, hdr, e),
                    'phase': phase})
    return out


def _root_rotation(onslaught, info, uexp, hdr, export):
    """An actor's yaw in degrees, from its root component (0 when it has none)."""
    props = {p[0]: p for p in onslaught.read_props(info, uexp, hdr, export)}
    ref = props.get('RootComponent')
    idx = onslaught.as_int(ref[2]) if ref else None
    if not idx or idx <= 0 or idx > len(info['exports']):
        return 0.0
    comp = {p[0]: p for p in onslaught.read_props(info, uexp, hdr,
                                                   info['exports'][idx - 1])}
    rot = onslaught.as_vec(comp['RelativeRotation'][2]) \
        if 'RelativeRotation' in comp else None
    return round(float(rot[1]), 3) if rot else 0.0


def actor_yaws(level_name):
    """{export name: yaw} for the level's spawners, rifts, doors and player starts."""
    from riftlord.onslaught import onslaught
    info, uexp, hdr = onslaught.load_map(level_name)
    out = {}
    for e in info['exports']:
        cls = str(e.get('class') or e.get('cls'))
        if any(k in cls for k in ('AISpawner', 'Rift', 'PlayerStart', 'Door')):
            out[str(e.get('name'))] = _root_rotation(onslaught, info, uexp, hdr, e)
    return out


# -- the graph ---------------------------------------------------------------

def _distance(a, b):
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def moved(at, shipped):
    """True when an edit's location `at` differs from the shipped one."""
    if at is None:
        return False
    if shipped is None:
        return True
    return _distance(at, shipped) > SAME_PLACE


def same_branches(edited, shipped):
    """True when an edit's branch list says what the level ships."""
    def norm(lst):
        return sorted((str(b['to']), round(float(b['weight']), 3)) for b in lst or [])
    return norm(edited) == norm(shipped)


def in_rift(at, rift_at):
    """Whether `at` stands inside the RiftEnterArea capsule of a rift at `rift_at`."""
    dx, dy = at[0] - rift_at[0], at[1] - rift_at[1]
    dz = at[2] - (rift_at[2] - RIFT_ENTER_DROP)
    past = max(0.0, abs(dz) - (RIFT_ENTER_HALF - RIFT_ENTER_RADIUS))
    return dx * dx + dy * dy + past * past <= RIFT_ENTER_RADIUS * RIFT_ENTER_RADIUS


def moves_exit(plan, level):
    """True when the plan removes the level's exit point and its first rift, naming no exit
    point of its own, takes that exit instead (it is moved to the rift)."""
    exit_name = level.get('exit')
    rifts = plan.get('rifts') or []
    if not exit_name or not rifts or rifts[0].get('exit') is not None:
        return False
    return any(w.get('name') == exit_name and w.get('gone')
               and w.get('kind') in ('', 'point') for w in plan.get('wiring') or [])


def rift_exits(plan, level):
    """{added point id: rift id}: the exit point of each of the plan's rifts.

    A rift names its exit point (`exit`). One that names none takes the nearest added point
    with no branches inside its RiftEnterArea, except the first rift when it takes the
    level's moved exit (moves_exit)."""
    points = {p['id']: p for p in plan.get('points') or [] if isinstance(p.get('id'), int)}
    rifts = plan.get('rifts') or []
    out = {}
    for r in rifts:
        e = r.get('exit')
        if isinstance(e, int) and e in points and e not in out:
            out[e] = r['id']
    moved_first = moves_exit(plan, level)
    for n, r in enumerate(rifts):
        if r.get('exit') is not None or (n == 0 and moved_first) or not r.get('at'):
            continue
        free = [p for pid, p in points.items() if pid not in out and not p.get('next')
                and in_rift(p['at'], r['at'])]
        if free:
            best = min(free, key=lambda p: _distance(p['at'], r['at']))
            out[best['id']] = r['id']
    return out


def rift_label(plan, rift_id):
    """'Rift N' for one of the plan's rifts, by its id as the Map Editor names it."""
    return 'Rift %s' % rift_id


def graph(plan, level):
    """The final path graph: {node: {'at', 'next', 'kind', 'changed'}}.

    Nodes are plan point ids (int) and level point names (str). 'rift' and an empty
    branch list on a plan point both resolve to the level's exit (or, with the level's exit
    removed, the first rift's own exit point). An exit point (the level's, and each of the
    plan's rifts', rift_exits) has `exit` set and no branches. Removed level points are
    spliced out: a branch into one is replaced by that point's own branches, with the
    weights multiplied. Returns (nodes, removed, problems).
    """
    exit_name = level['exit']
    edits = {w['name']: w for w in plan.get('wiring') or []
             if w.get('kind') in ('', 'point') and w['name'] in level['points']}
    removed = {n for n, w in edits.items() if w.get('gone')}
    problems = []
    own = rift_exits(plan, level)
    # Removing the exit while adding a rift moves the exit to that rift instead.
    if exit_name in removed and moves_exit(plan, level):
        removed.discard(exit_name)
        edits[exit_name] = dict(edits[exit_name], gone=False,
                                at=list(plan['rifts'][0]['at']), exit_moved=True)
    #: where 'rift' leads: the level's exit, or with it removed the first rift's own exit
    rift_to = exit_name
    if exit_name in removed and own:
        order = [r.get('id') for r in plan.get('rifts') or []]
        rift_to = min(own, key=lambda pid: order.index(own[pid]))
    nodes = {}
    for name, p in level['points'].items():
        if name in removed:
            continue
        w = edits.get(name) or {}
        nxt = w['next'] if 'next' in w else p['next']
        nodes[name] = {'at': tuple(w.get('at') or p['location']), 'kind': 'level',
                       'next': [dict(b) for b in nxt],
                       'changed': 'next' in w and not same_branches(w['next'], p['next']),
                       'moved': moved(w.get('at'), p['location']), 'index': p['index']}
    if exit_name in nodes:
        nodes[exit_name]['exit'] = True
    for p in plan.get('points') or []:
        if p['id'] in own:
            nodes[p['id']] = {'at': tuple(p['at']), 'kind': 'plan', 'next': [],
                              'changed': True, 'point': p, 'exit': True,
                              'rift': own[p['id']]}
            continue
        nxt = p.get('next') or [{'to': 'rift', 'weight': 1.0}]
        nodes[p['id']] = {'at': tuple(p['at']), 'kind': 'plan', 'next':
                          [dict(b) for b in nxt], 'changed': True, 'point': p}
    if exit_name in removed and rift_to == exit_name:
        problems.append('%s is the level\'s exit point. Removing it needs a rift '
                        'added in its place, and this plan adds none' % exit_name)
    for n in nodes.values():
        for b in n['next']:
            if b['to'] == 'rift':
                b['to'] = rift_to
    # splice out removed points, repeatedly, so chains of removed points resolve
    removed_next = {name: [dict(b) for b in
                           ((edits[name].get('next')) or level['points'][name]['next'])]
                    for name in removed}
    #: a branch into the removed level exit goes on to where 'rift' now leads
    if exit_name in removed and rift_to != exit_name:
        removed_next[exit_name] = [{'to': rift_to, 'weight': 1.0}]
    for _round in range(len(removed) + 1):
        spliced = False
        for key, n in nodes.items():
            out = []
            for b in n['next']:
                if b['to'] in removed:
                    spliced = True
                    n['changed'] = True
                    for b2 in removed_next[b['to']]:
                        to = rift_to if b2['to'] == 'rift' else b2['to']
                        out.append({'to': to, 'weight': b['weight'] * b2['weight']})
                else:
                    out.append(b)
            merged = {}
            for b in out:
                if b['weight'] >= MIN_WEIGHT and b['to'] != key:
                    merged[b['to']] = merged.get(b['to'], 0.0) + b['weight']
            n['next'] = [{'to': t, 'weight': round(w, 4)} for t, w in merged.items()]
        if not spliced:
            break
    for key, n in nodes.items():
        if n['kind'] != 'plan' and not n['changed']:
            continue
        for b in n['next']:
            if b['to'] not in nodes:
                problems.append('point %s leads to %s, which is neither an added point '
                                'nor one of the level\'s' % (key, b['to']))
    return nodes, removed, problems


def exits(nodes):
    """The exit nodes of a graph() (the level's exit point and each rift's own)."""
    return [k for k, n in nodes.items() if n.get('exit')]


def reaches_exit(nodes, exit_name):
    """The set of nodes from which an exit can be reached. `exit_name` is one exit node or
    a list of them."""
    back = {}
    for key, n in nodes.items():
        for b in n['next']:
            back.setdefault(b['to'], set()).add(key)
    good = set()
    todo = list(exit_name) if isinstance(exit_name, (list, tuple, set)) else [exit_name]
    while todo:
        cur = todo.pop()
        if cur in good:
            continue
        good.add(cur)
        todo.extend(back.get(cur, ()))
    return good


def check(plan, level_name, level=None):
    """(errors, warnings) for the plan's actors against the level they go into."""
    level = level or level_actors(level_name)
    errors, warnings = [], []
    nodes, removed, problems = graph(plan, level)
    errors += problems
    # Loops are allowed; every point must still have some way on to a rift.
    good = reaches_exit(nodes, exits(nodes) or [level['exit']])
    # each of the plan's rifts has its own exit point, and no two share one
    own = rift_exits(plan, level)
    points = {p['id']: p for p in plan.get('points') or []}
    named = {}
    for n, r in enumerate(plan.get('rifts') or []):
        label = rift_label(plan, r.get('id'))
        e = r.get('exit')
        if e is not None and e not in points:
            errors.append('%s: its exit point %s is not a path point you placed' % (label, e))
        elif e is not None and e in named:
            errors.append('%s and %s share exit point %s'
                          % (rift_label(plan, named[e]), label, e))
        elif e is None and r.get('id') not in own.values() and not (
                n == 0 and moves_exit(plan, level)):
            errors.append('%s has no exit point, so no route can end at it. Place a path '
                          'point inside it' % label)
        if e is not None:
            named[e] = r.get('id')
    for pid, rid in own.items():
        rift = next((r for r in plan.get('rifts') or [] if r.get('id') == rid), None)
        if rift and rift.get('at') and not in_rift(points[pid]['at'], rift['at']):
            warnings.append('point %s, the exit of %s, stands outside the rift\'s enter area '
                            '(%g uu across it, %g below to %g above it)'
                            % (pid, rift_label(plan, rid), RIFT_ENTER_RADIUS,
                               RIFT_ENTER_DROP + RIFT_ENTER_HALF,
                               RIFT_ENTER_HALF - RIFT_ENTER_DROP))
        if points[pid].get('next'):
            warnings.append('point %s is the exit of %s, so its branches are not used'
                            % (pid, rift_label(plan, rid)))
    for key, n in nodes.items():
        if key not in good:
            errors.append('enemies at point %s can never reach the rift: none of its '
                          'branches leads there. Give it a branch toward the rift'
                          % key)
        total = sum(b['weight'] for b in n['next'])
        low, high = SHIPPED_WEIGHT_SUM
        if (n['kind'] == 'plan' or n['changed']) and n['next'] and not (
                low - WEIGHT_TOLERANCE <= total <= high + WEIGHT_TOLERANCE):
            warnings.append('point %s: branch weights add up to %.2f; shipped levels '
                            'use %.1f to %.1f' % (key, total, low, high))
        if len(n['next']) > MAX_SHIPPED_FANOUT and (n['kind'] == 'plan' or n['changed']):
            warnings.append('point %s has %d branches; no shipped level has more than '
                            '%d' % (key, len(n['next']), MAX_SHIPPED_FANOUT))
    # spawner starts
    level_edits = {w['name']: w for w in plan.get('wiring') or []}
    for sp in plan.get('spawners') or []:
        for key in ('start', 'flyerStart'):
            r = sp.get(key)
            if r is not None and r not in nodes:
                errors.append('spawner %s: its %s %s is not a point in the level'
                              % (sp['id'], 'flyer start' if key == 'flyerStart'
                                 else 'start', r))
    for name, sp in level['spawners'].items():
        w = level_edits.get(name) or {}
        if w.get('gone'):
            continue
        for key in ('start', 'flyerStart'):
            r = w[key] if key in w else sp.get(key)
            if r is not None and r not in nodes:
                errors.append('spawner %s starts at %s, which this plan removes or does '
                              'not have; give it another start point' % (name, r))
    reached = set()
    for sp in list(plan.get('spawners') or []) + list(level['spawners'].values()):
        for key in ('start', 'flyerStart'):
            if sp.get(key) in nodes:
                reached.add(sp[key])
    for w in plan.get('wiring') or []:
        reached.update(w.get(k) for k in ('start', 'flyerStart') if w.get(k) in nodes)
    for n in nodes.values():
        reached.update(b['to'] for b in n['next'])
    for p in plan.get('points') or []:
        if p['id'] not in reached:
            warnings.append('point %s is not reached from any spawner or point'
                            % p['id'])
    # indices
    seen = {}
    for name, sp in level['spawners'].items():
        if not (level_edits.get(name) or {}).get('gone') and sp.get('index'):
            seen.setdefault(sp['index'], []).append(name)
    for sp in plan.get('spawners') or []:
        if sp.get('index'):
            seen.setdefault(sp['index'], []).append('spawner %s' % sp['id'])
    for i, who in seen.items():
        if len(who) > 1:
            errors.append('spawner index %d is used by %s' % (i, ' and '.join(who)))
    # player starts
    phases = [(s['phase'], level_edits.get(s['export']) or {}) for s in level['starts']]
    live = [w.get('phase', ph) for ph, w in phases if not w.get('gone')]
    live += [s['phase'] for s in plan.get('playerStarts') or []]
    if level['starts'] or plan.get('playerStarts'):
        if not any(ph in ('breaks', 'both') for ph in live):
            errors.append('no player start is left for between waves')
        if not any(ph in ('waves', 'both') for ph in live):
            errors.append('no player start is left for during waves')
    # doors
    gone_doors = {w['name'] for w in plan.get('wiring') or [] if w.get('gone')
                  and w['name'] in level['doors']}
    for sp in plan.get('spawners') or []:
        if isinstance(sp.get('door'), str) and sp['door'] in gone_doors:
            errors.append('spawner %s uses %s, which this plan removes'
                          % (sp['id'], sp['door']))
    return errors, warnings


# -- a removed spawner's waves -------------------------------------------------

def removed_spawners(plan, level):
    """{indexInLevel: export name} for the level's own spawners the plan removes.

    An index one of the plan's own spawners takes over (a level from scratch frees the
    host's, riftlord/scratch.py) keeps its waves for that spawner."""
    gone = {w['name'] for w in plan.get('wiring') or [] if w.get('gone')}
    reused = ({sp.get('index') for sp in plan.get('spawners') or [] if sp.get('index')}
              if plan.get('scratch') else set())
    return {sp['index']: name for name, sp in level['spawners'].items()
            if name in gone and sp.get('index') and sp['index'] not in reused}


def level_spec(level_name):
    """The level's own onslaught as a wave spec (wavespec.read_onslaught), cached."""
    if level_name not in _LEVEL_SPECS:
        from riftlord.onslaught import wavespec
        _LEVEL_SPECS[level_name] = wavespec.read_onslaught(level_name)
    return _LEVEL_SPECS[level_name]


_LEVEL_SPECS = {}


def mission_spec(level_name, mission=None):
    """The waves a mission sends: its own wave spec when it has one, else the level's."""
    if mission:
        from riftlord.missions import bucket
        rec = bucket.load(mission) if bucket.ACTIVE else None
        if rec and isinstance(rec.get('spec'), dict):
            return rec['spec']
    return level_spec(level_name)


def spawner_waves(spec):
    """{spawner index: {'waves': waves with spawns there, 'spawns': spawn rows}}."""
    out = {}
    for wave in (spec or {}).get('waves') or []:
        for key, rows in (wave.get('spawns') or {}).items():
            rows = [r for r in rows or [] if r.get('unit')]
            if not rows:
                continue
            got = out.setdefault(int(key), {'waves': 0, 'spawns': 0})
            got['waves'] += 1
            got['spawns'] += len(rows)
    return out


def without_spawners(spec, indices):
    """A copy of `spec` with no spawn rows for the spawner indices given."""
    drop = {str(i) for i in indices}
    out = dict(spec)
    out['waves'] = []
    for wave in spec.get('waves') or []:
        wave = dict(wave)
        wave['spawns'] = {k: v for k, v in (wave.get('spawns') or {}).items()
                          if str(k) not in drop}
        out['waves'].append(wave)
    return out


def removed_spawner_notes(plan, level, spec):
    """One line per removed spawner that had waves, saying what goes with it."""
    have = spawner_waves(spec)
    lines = []
    for index, name in sorted(removed_spawners(plan, level).items()):
        got = have.get(index)
        if got:
            lines.append('%s (spawner %d) is removed, and its %d spawn(s) over %d '
                         'wave(s) go with it' % (name, index, got['spawns'],
                                                 got['waves']))
    return lines


#: Most routes one flyer start may contribute to the flight graph, counting each
#: distinct path through its forks.
MAX_FLYER_PATHS = 32


def flyer_routes(plan, level):
    """[(route points, anchors)] for every flyer start, for the flight graph.

    One route per path through the start's forks, so every branch gets flight volumes.
    A level point ends a path and is included, which is how a route hands off to the
    exit; a branch back to a point already on the path ends it there.
    """
    nodes, _removed, _p = graph(plan, level)
    doors = {d['id']: d for d in plan.get('doors') or []}
    out = []
    for sp in plan.get('spawners') or []:
        start = sp.get('flyerStart')
        if start not in nodes:
            continue
        anchors = [tuple(float(x) for x in sp['at'])]
        if sp.get('door') in doors:
            anchors.append(tuple(float(x) for x in doors[sp['door']]['at']))
        paths, stack = [], [[start]]
        while stack and len(paths) < MAX_FLYER_PATHS:
            path = stack.pop()
            cur = path[-1]
            nxt = [b['to'] for b in nodes[cur]['next'] if b['to'] in nodes] \
                if nodes[cur]['kind'] != 'level' else []
            ahead = [n for n in nxt if n not in path]
            if not ahead:
                paths.append(path)
                continue
            for n in reversed(ahead):
                stack.append(path + [n])
        for path in paths:
            route = [tuple(float(x) for x in nodes[n]['at']) for n in path]
            if len(route) >= 2:
                out.append((route, anchors))
    return out


# -- emission ----------------------------------------------------------------

def emit(builder, plan, level_name, level_path, ctx):
    """Write the plan's actors into the level copy wrapped by `builder`.

    `ctx` carries what apply_plan owns: by_name, door_index, free_name, free_index,
    taken_indices, first_point, report, and the helpers add/soft_ref/add_preload/
    place_whole/donor_for/branch_body.
    """
    report = ctx['report']
    by_name = ctx['by_name']
    level = level_actors(level_name)
    errors, warnings = check(plan, level_name, level)
    if errors:
        raise SystemExit('; '.join(errors))
    report.setdefault('warnings', []).extend(warnings)
    nodes, removed, _problems = graph(plan, level)
    edits = {w['name']: w for w in plan.get('wiring') or []}

    # the level's own actors: moved, turned, parked
    for w in plan.get('wiring') or []:
        index = by_name.get(w['name'])
        if not index:
            report['skipped'].append('%s is not in the level' % w['name'])
            continue
        if w.get('gone') and w['name'] == level['exit'] and moves_exit(plan, level):
            builder.place(index, location=tuple(plan['rifts'][0]['at']))
            report['moved'] += 1
            report['notes'].append('%s, the level\'s exit, moved to the new rift'
                                   % w['name'])
            continue
        if w.get('gone'):
            from riftlord.mapeditor import geomedits
            builder.place(index, location=geomedits.park_location(builder, index))
            geomedits.hide_markers(builder, index)
            report['parked'] += 1
            #: parked AND out of the actor list: an actor iterator still finds the
            #: object, so it keeps the parked location
            if geomedits.DELETE and builder.unregister_actor(index):
                report['deleted'] = report.get('deleted', 0) + 1
            if w['name'] in level['spawners']:
                index = level['spawners'][w['name']].get('index')
                if index in removed_spawners(plan, level):
                    report['notes'].append('%s removed, with its waves (spawner %s)'
                                           % (w['name'], index))
                else:
                    report['notes'].append('%s removed; its waves go to the new '
                                           'spawner %s' % (w['name'], index))
            continue
        rotation = ((0.0, float(w['rotation']), 0.0)
                    if w.get('rotation') is not None and w.get('kind') != 'point'
                    else None)
        if w.get('at') is not None or rotation is not None:
            ctx['place_whole'](builder, index,
                               location=tuple(w['at']) if w.get('at') else None,
                               rotation=rotation)
            report['moved'] += 1
        if 'phase' in w and any(s['export'] == w['name'] for s in level['starts']):
            _set_phase(builder, index, w['phase'])

    # doors this plan removes: the spawners that used them go doorless
    gone_doors = {w['name'] for w in plan.get('wiring') or []
                  if w.get('gone') and w['name'] in level['doors']}
    for name, sp in level['spawners'].items():
        w = edits.get(name) or {}
        if sp.get('door') in gone_doors and 'door' not in w and not w.get('gone'):
            index = by_name.get(name)
            if index:
                builder.clear_property(index, 'StartingDoor')
                builder.set_bool(index, 'bInvulnerableOnSpawn', False)
                report['notes'].append('%s lost its door, so its units are not '
                                       'invulnerable when they spawn' % name)

    # added points: one export each, on the level's own path
    path_index = ctx['level_path_index'](builder)
    point_donor = ctx['donor_for']('OMDPathPoint')
    made = {}
    point_at = ctx['first_point']
    if plan.get('points') and not (path_index and point_donor):
        raise SystemExit('this level has no path to add points to')
    for p in plan.get('points') or []:
        pname = ctx['free_name']('RiftLordPoint%d' % p['id'])
        idx = builder.lift_actor(point_donor[0], point_donor[1], rename=pname)
        builder.place(idx, location=tuple(p['at']))
        builder.set_object(idx, 'ParentPath', path_index)
        ctx['add_preload'](builder, idx, ctx['CREATE_BEFORE_SERIALIZE'], path_index)
        for stale in ('NextPoints', 'bIsExitPoint', 'bIsStartPoint',
                      'bGroundOnly', 'bFlyersOnly'):
            builder.clear_property(idx, stale)
        builder.set_property(idx, 'PointIndex', 'IntProperty',
                             struct.pack('<i', point_at))
        for field, prop in (('reachRadius', 'ReachRadius'),
                            ('formationSpacing', 'FormationSpacing'),
                            ('flightHeight', 'FlightHeight')):
            value = p.get(field)
            if value is None:
                value = POINT_FIELDS[field][1]
            builder.set_property(idx, prop, 'FloatProperty',
                                 struct.pack('<f', float(value)))
        if p.get('formationSize'):
            builder.set_property(idx, 'FormationSize', 'IntProperty',
                                 struct.pack('<i', int(p['formationSize'])))
        for field, prop in (('groundOnly', 'bGroundOnly'),
                            ('flyersOnly', 'bFlyersOnly')):
            if p.get(field):
                builder.set_bool(idx, prop, True)
        #: a rift's own exit point (rift_exits)
        if nodes[p['id']].get('exit'):
            builder.set_bool(idx, 'bIsExitPoint', True)
            report['exits'] = report.get('exits', 0) + 1
        made[p['id']] = (idx, point_at, pname)
        nodes[p['id']]['index'] = point_at
        point_at += 1
        report['points'] += 1
    if made:
        builder.set_object_array(path_index, 'PathPoints',
                                 builder.object_array(path_index, 'PathPoints')
                                 + [m[0] for m in made.values()])

    def export_of(node):
        return made[node][2] if node in made else node

    # the level's own spawners, re-routed or given another door
    ctx['made_name'] = export_of
    for name, w in edits.items():
        if name in level['spawners'] and not w.get('gone') and by_name.get(name):
            _retarget_spawner(builder, by_name[name], w, level, ctx, level_path)

    # start flags on every point a spawner starts at
    starts = set()
    for sp in plan.get('spawners') or []:
        starts.update(sp.get(k) for k in ('start', 'flyerStart') if sp.get(k) in nodes)
    for name, w in edits.items():
        if name in level['spawners'] and not w.get('gone'):
            starts.update(w.get(k) for k in ('start', 'flyerStart')
                          if w.get(k) in nodes)
    for node in starts:
        if node in made:
            builder.set_bool(made[node][0], 'bIsStartPoint', True)
        elif not level['points'][node]['start'] and by_name.get(node):
            builder.set_bool(by_name[node], 'bIsStartPoint', True)

    # added spawners, facing their route's first point unless turned by hand
    from riftlord.mapeditor import spawnerfacing
    for n, sp in enumerate(plan.get('spawners') or []):
        name = ctx['free_name']('RiftLordSpawner%d' % n)
        yaw = spawnerfacing.facing(sp, nodes, level)
        index = ctx['add'](builder, UNSTABLE_CLASS if sp.get('unstable') else 'BP_OMDAISpawner_C',
                           sp['at'], name, yaw, report)
        if not index:
            continue
        report['spawners'] += 1
        if not sp.get('turned') and yaw is not None and yaw != sp.get('rotation'):
            report['spawners_faced'] = report.get('spawners_faced', 0) + 1
        if sp.get('unstable'):
            #: closed until the Mission Events piece opens it
            builder.set_bool(index, 'bIsActive', False)
        wanted = int(sp.get('index') or 0)
        if wanted <= 0 or wanted in ctx['taken_indices']:
            was, wanted = wanted, ctx['free_index']()
            if was > 0:
                report['notes'].append('spawner index %d is taken; used %d instead'
                                       % (was, wanted))
        else:
            ctx['taken_indices'].add(wanted)
        sp['index'] = wanted
        builder.set_property(index, 'indexInLevel', 'IntProperty',
                             struct.pack('<i', wanted))
        if sp.get('maxSpawnPerRow'):
            builder.set_property(index, 'maxSpawnPerRow', 'IntProperty',
                                 struct.pack('<i', int(sp['maxSpawnPerRow'])))
        for stale in ('StartingDoor', 'StartingPathPoint', 'StartingFlyerPathPoint'):
            builder.clear_property(index, stale)
        door = _door_name(sp.get('door'), ctx['door_index'], level)
        if door:
            ctx['soft_ref'](builder, index, 'StartingDoor', level_path, door)
        else:
            builder.set_bool(index, 'bInvulnerableOnSpawn', False)
            report['notes'].append('%s has no door, so its units are not invulnerable '
                                   'when they spawn' % name)
        for key, prop in (('start', 'StartingPathPoint'),
                          ('flyerStart', 'StartingFlyerPathPoint')):
            if sp.get(key) in nodes:
                ctx['soft_ref'](builder, index, prop, level_path, export_of(sp[key]))

    # flight volume actors along each flyer route's added points
    on_flyer_route = set()
    for sp in plan.get('spawners') or []:
        cur, seen = sp.get('flyerStart'), set()
        while cur in made and cur not in seen:
            seen.add(cur)
            on_flyer_route.add(cur)
            nxt = nodes[cur]['next']
            cur = nxt[0]['to'] if nxt else None
    for k, pid in enumerate(sorted(on_flyer_route, key=lambda i: made[i][1])):
        got = ctx['add'](builder, ctx['FLIGHT_VOLUME'], nodes[pid]['at'],
                         ctx['free_name']('RiftLordFlightV%d' % k), None, report)
        if got:
            report['flight_volumes'] = report.get('flight_volumes', 0) + 1

    # branches: every added point, and every level point whose branches changed
    for key, node in nodes.items():
        if not node['changed'] and not _targets_moved(node, nodes):
            continue
        target = made[key][0] if key in made else by_name.get(key)
        if not target:
            continue
        bodies = [ctx['branch_body'](builder, {
            'PointIndex': nodes[b['to']]['index'],
            'BranchWeight': float(b['weight']),
            'BranchLength': _distance(node['at'], nodes[b['to']]['at'])})
            for b in node['next'] if b['to'] in nodes]
        if bodies:
            builder.struct_array(target, 'NextPoints', 'OMDPathBranch', bodies)
        elif key not in made:
            builder.clear_property(target, 'NextPoints')
        report['branches'] += len(bodies)
    report['spliced'] = sorted(removed)
    return made


def _targets_moved(node, nodes):
    return any(nodes.get(b['to'], {}).get('moved') for b in node['next'])


def _door_name(value, door_index, level):
    if isinstance(value, int):
        return door_index.get(value)
    if isinstance(value, str) and value in level['doors']:
        return value
    return None


def _retarget_spawner(builder, index, w, level, ctx, level_path):
    for key, prop in (('start', 'StartingPathPoint'),
                      ('flyerStart', 'StartingFlyerPathPoint')):
        if key not in w:
            continue
        builder.clear_property(index, prop)
        if w[key] is not None:
            target = ctx['made_name'](w[key]) if isinstance(w[key], int) else w[key]
            ctx['soft_ref'](builder, index, prop, level_path, target)
    if 'door' in w:
        builder.clear_property(index, 'StartingDoor')
        door = _door_name(w['door'], ctx['door_index'], level)
        if door:
            ctx['soft_ref'](builder, index, 'StartingDoor', level_path, door)
            builder.clear_property(index, 'bInvulnerableOnSpawn')
        else:
            builder.set_bool(index, 'bInvulnerableOnSpawn', False)


def _set_phase(builder, index, phase):
    for prop, value in PHASES[phase].items():
        builder.clear_property(index, prop)
        if value is not None:
            builder.set_bool(index, prop, value)


def emit_player_starts(builder, plan, ctx):
    """Add the plan's player starts. Returns how many were added."""
    report = ctx['report']
    count = 0
    for n, s in enumerate(plan.get('playerStarts') or []):
        index = ctx['add'](builder, 'OMDPlayerStart', s['at'],
                           ctx['free_name']('RiftLordPlayerStart%d' % n),
                           s.get('rotation'), report)
        if not index:
            continue
        _set_phase(builder, index, s['phase'])
        count += 1
    report['player_starts'] = count
    return count


def ground_chains(plan, level):
    """[[x, y, z], ...] chains for the walkable-floor extension, one per ground route.

    Each chain is a spawner (lowered to its floor) followed by the points its ground
    route walks, ending at the first level point it reaches. A second branch at a
    fork becomes its own chain, starting at the fork.
    """
    from riftlord.build.navigation import navsurfextend
    migrate(plan)
    nodes, removed, _p = graph(plan, level)
    starts = []
    for sp in plan.get('spawners') or []:
        if sp.get('start') in nodes:
            starts.append((tuple(sp['at']), sp['start']))
    for name, w in ((w['name'], w) for w in plan.get('wiring') or []):
        sp = level['spawners'].get(name)
        if not sp or w.get('gone'):
            continue
        start = w['start'] if 'start' in w else sp.get('start')
        if start in nodes and (moved(w.get('at'), sp['location'])
                               or start != sp.get('start')):
            starts.append((tuple(w.get('at') or sp['location']), start))
    chains, todo, walked = [], [(None, at, first) for at, first in starts], set()
    while todo:
        fork, at, first = todo.pop(0)
        if fork is None:
            chain = [(at[0], at[1], at[2] - navsurfextend.SPAWNER_ABOVE_FLOOR)]
        else:
            chain = [nodes[fork]['at']]
        cur, joined = first, None
        while cur in nodes:
            if cur in walked:
                joined = cur
                break
            walked.add(cur)
            node = nodes[cur]
            chain.append(node['at'])
            if node['kind'] == 'level' and not node.get('moved'):
                break
            if node['kind'] == 'plan' and node['point'].get('flyersOnly'):
                break
            nxt = node['next']
            for b in nxt[1:]:
                if b['to'] not in walked:
                    todo.append((cur, None, b['to']))
            cur = nxt[0]['to'] if nxt else None
        if joined is not None:
            chain.append(nodes[joined]['at'])
        if len(chain) >= 2:
            chains.append(chain)
    return chains


def ground_names(plan, level):
    """{(x, y, z): label} of the points ground_chains walks, labelled as the Map Editor
    labels them: "Spawner N" (an added spawner, at its floor), "Point N" (an added path
    point) and the level's own point names."""
    from riftlord.build.navigation import navsurfextend
    nodes, _removed, _p = graph(plan, level)
    out = {}
    for sp in plan.get('spawners') or []:
        if sp.get('at') is not None:
            at = sp['at']
            out[(at[0], at[1], at[2] - navsurfextend.SPAWNER_ABOVE_FLOOR)] = \
                'Spawner %s' % sp.get('id')
    for name, w in ((w['name'], w) for w in plan.get('wiring') or []):
        sp = level['spawners'].get(name)
        if sp and not w.get('gone'):
            at = w.get('at') or sp['location']
            out[(at[0], at[1], at[2] - navsurfextend.SPAWNER_ABOVE_FLOOR)] = name
    for key, n in nodes.items():
        out[tuple(n['at'])] = ('Point %s' % key) if n['kind'] == 'plan' else str(key)
    return out
