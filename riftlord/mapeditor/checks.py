"""The Map Editor's Checks list (POST /api/checks/<level>): what the open plan needs changed
before it builds, what a build will fix by itself, and what the last build of its mission
found. Each row names the piece, route point or spawner as the editor shows it and carries
its target and position, so a click selects and frames it (web/mapeditor/src/checks.js).

Rows: {'level': 'error' | 'warning' | 'fixed' | 'build', 'text', 'target', 'at'};
target as buildmessage.check gives it.
"""

import re

from riftlord.core import buildmessage

#: Labels in a message and the plan list they name ('Unstable Rift' by index in level).
LABELS = (('Unstable Rift', 'spawners', 'index'), ('Path Point', 'points', 'id'),
          ('Spawner', 'spawners', 'id'), ('Rift', 'rifts', 'id'), ('Door', 'doors', 'id'))
LABEL_RE = re.compile(r'\b(Unstable Rift|Path Point|Spawner|Rift|Door) (\d+)\b')
#: Row levels, in the order the list shows them.
LEVELS = ('error', 'build', 'warning', 'fixed')


def locate(text, plan, level):
    """(target, at) of the first thing `text` names: a plan actor by its label, else one of
    the level's own by its name; (None, None) when it names none."""
    for m in LABEL_RE.finditer(str(text)):
        label, number = m.group(1), int(m.group(2))
        if label == 'Rift' and str(text)[max(0, m.start() - 9):m.start()] == 'Unstable ':
            continue
        _l, key, field = next(row for row in LABELS if row[0] == label)
        item = next((o for o in plan.get(key) or [] if isinstance(o, dict)
                     and o.get(field) == number), None)
        if item is not None:
            return {'kind': key[:-1], 'id': item.get('id')}, item.get('at')
    for key in ('points', 'spawners', 'doors'):
        for name, rec in (level.get(key) or {}).items():
            if re.search(r'\b%s\b' % re.escape(str(name)), str(text)):
                wired = next((w for w in plan.get('wiring') or [] if w.get('name') == name),
                             {})
                return {'name': name}, wired.get('at') or rec.get('location')
    return None, None


def row(level_word, text, plan, level, target=None, at=None):
    """One Checks row, its target found from the text when not given."""
    if target is None:
        target, at = locate(text, plan, level)
    got = buildmessage.check(text[:1].upper() + text[1:], target, at)
    got['level'] = level_word
    return got


def index_fixes(level_name, plan):
    """[(spawner, was, now)]: the added spawners whose index in level is taken, and the
    index the build gives each (planactors.emit's free_index, in plan order)."""
    from riftlord.build import apply as applier
    from riftlord.mapeditor import scratch
    taken = (set(scratch.taken_indices(level_name, plan)) if scratch.is_scratch(plan)
             else set(applier.used_spawner_indices(level_name)))
    out = []
    for sp in plan.get('spawners') or []:
        wanted = int(sp.get('index') or 0)
        if wanted <= 0 or wanted in taken:
            now = 1
            while now in taken:
                now += 1
            taken.add(now)
            if wanted > 0 and not sp.get('unstable'):
                out.append((sp, wanted, now))
        else:
            taken.add(wanted)
    return out


def for_plan(level_name, plan, mission=None):
    """{'checks': [row]} for the open plan of `level_name`, and the last build of
    `mission` (a Missions record id) when given."""
    from riftlord.mapeditor import planactors
    plan = plan or {}
    level = planactors.level_actors(level_name)
    out = []

    def add(level_word, texts):
        for text in texts:
            out.append(row(level_word, text, plan, level))
    try:
        errors, warnings = planactors.check(plan, level_name, level)
    except (Exception, SystemExit) as exc:           # noqa: BLE001
        print('checks: planactors.check: %s' % buildmessage.technical(exc))
        errors, warnings = [], []
    add('error', errors)
    add('warning', warnings)
    for sp, was, now in index_fixes(level_name, plan):
        out.append(row('fixed', 'Spawner %s: index in level %d is taken, so the build gives '
                       'it %d.' % (sp.get('id'), was, now), plan, level,
                       {'kind': 'spawner', 'id': sp.get('id')}, sp.get('at')))
    spec = None
    try:
        spec = planactors.mission_spec(level_name, mission)
    except (Exception, SystemExit) as exc:           # noqa: BLE001
        print('checks: mission spec: %s' % buildmessage.technical(exc))
    for label, fn in (('crystal', _crystal), ('rift events', _rift_events),
                      ('supported mods', _mods), ('spawner facing', _facing)):
        try:
            for level_word, text, target, at in fn(level_name, plan, level, spec):
                out.append(row(level_word, text, plan, level, target, at))
        except (Exception, SystemExit) as exc:       # noqa: BLE001
            print('checks: %s: %s' % (label, buildmessage.technical(exc)))
    out += last_build(mission)
    out.sort(key=lambda r: LEVELS.index(r['level']))
    return {'checks': out}


def _crystal(level_name, plan, level, spec):
    from riftlord.mapeditor import crystal
    item = crystal.piece(plan)
    if item is None:
        return []
    added = plan.get('added') or []
    target = {'added': added.index(item)} if item in added else None
    at = (item.get('matrix') or [0] * 16)[12:15] if item.get('matrix') else None
    return [('error', text, None, None) if re.search(LABEL_RE, text)
            else ('error', text, target, at) for text in crystal.problems(plan, spec)]


def _rift_events(level_name, plan, level, spec):
    from riftlord.missions import missionevents
    if not missionevents.unstable_rifts(plan):
        return []
    return [('error', text, None, None) for text in missionevents.rift_problems(plan, spec)]


def _mods(level_name, plan, level, spec):
    from riftlord.core import supportedmods
    return [('error', text, None, None) for text in supportedmods.build_problems(plan)]


def _facing(level_name, plan, level, spec):
    """A spawner turned by hand more than spawnerfacing.WARN_OFF degrees away from its
    route's first point."""
    from riftlord.mapeditor import planactors
    from riftlord.mapeditor import spawnerfacing
    nodes, _removed, _p = planactors.graph(plan, level)
    out = []
    for sp in plan.get('spawners') or []:
        if not sp.get('turned') or sp.get('rotation') is None:
            continue
        want = spawnerfacing.facing(dict(sp, turned=False), nodes, level)
        if want is None or want == sp.get('rotation'):
            continue
        off = spawnerfacing.off(float(sp['rotation']), want)
        if off > spawnerfacing.WARN_OFF:
            out.append(('warning', 'Spawner %s faces %.0f degrees away from its route\'s first '
                        'point, so the enemies it sends walk backwards. Turn it toward that '
                        'point.' % (sp.get('id'), off),
                        {'kind': 'spawner', 'id': sp.get('id')}, sp.get('at')))
    return out


def last_build(mission):
    """Rows for what the last build of `mission` stopped on: its Checks rows, else its
    message."""
    if not mission:
        return []
    from riftlord.missions import bucket
    rec = bucket.load(str(mission)) or {}
    rows = []
    for got in rec.get('buildChecks') or []:
        rows.append(dict(got, level='build'))
    if not rows and rec.get('bakeError'):
        rows.append({'level': 'build', 'text': rec['bakeError']})
    return rows
