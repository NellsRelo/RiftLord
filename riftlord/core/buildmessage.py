"""Build failures, fixes and warnings, sorted three ways.

A. Fixed by the build: one line saying what it did ("Moved Path Point 4 by 120 units onto
   the floor at (x, y, z)."), in the build's `fixed` list and the console.
B. A design decision: a Problem or Refusal naming the piece, route point or spawner as the
   Map Editor shows it, with its position and what to change. It carries `checks`
   ([{text, target, at}]) that the Map Editor's Checks list shows; clicking one selects
   and frames it. The build's message points there.
C. Rift Lord's own fault: never the designer's task. With a safe fallback the build takes
   it and says what it did (a warning); without one it raises a Fault: "Rift Lord could
   not <part>.", the full diagnostic in the console and a saved report file.

    raise buildmessage.Problem('Path Point 3 at (10, 20, 30) is ...; move it ...',
                               detail='graph 0 ...', target={'kind': 'point', 'id': 3},
                               at=(10, 20, 30))
    raise buildmessage.fault('write the floor enemies walk on', detail)
    shown, detail, checks = buildmessage.explain(exc, stage='Navmesh', reports=folder)
"""

import os
import re
import time

#: Where the Map Editor lists what a build needs changed, as a B message names it.
CHECKS = 'It is listed under Checks in the Map Editor.'

#: What each build stage (buildstage.stage) is doing, as a C message names it.
STEPS = {
    'Level': 'write the level',
    'Navmesh': 'build the floor enemies walk on',
    'Lightmap': 'bake the light maps',
    'Minimap': 'draw the minimap',
    'Waves': 'write the waves',
}
#: The step named when no stage was announced.
DEFAULT_STEP = 'build this mission'

#: Folders whose own SystemExit texts are technical (relative to the riftlord package).
INTERNAL_DIRS = ('build', 'formats', 'core')
#: Modules elsewhere whose SystemExit texts are technical.
INTERNAL_MODULES = ('mapeditor/kitactors.py', 'mapeditor/fabemit.py',
                    'mapeditor/foliageadd.py', 'mapeditor/controllers.py',
                    'mapeditor/scratch.py', 'mapeditor/geomedits.py',
                    'mapeditor/kitdonors.py', 'mapeditor/kitgeom.py', 'mapeditor/kitfit.py',
                    'mapeditor/leveldata.py', 'mapeditor/compoundmine.py',
                    'mapeditor/chandelier.py', 'onslaught/bppatch.py',
                    'onslaught/onslaught.py', 'onslaught/flowsim.py',
                    'onslaught/gamedata.py')
#: Modules under INTERNAL_DIRS whose SystemExit texts are written for a designer.
DESIGNER_MODULES = ('core/supportedmods.py', 'core/pakspelling.py', 'core/pakguard.py')

#: Words a designer-facing message never holds alone.
JARGON = ('export', 'checksum', 'edge id', 'contour', 'preload', 'corridor', 'navgraph',
          'graph', 'polygon', 'indexInLevel', 'buffer', 'uasset', 'uexp', 'FName')
#: What no message says: a designer is never sent to someone else.
SENDS = re.compile(r'send (the|this|your) map|to the developer', re.IGNORECASE)

#: Report files kept per folder (save_report), newest first.
REPORTS_KEPT = 30


def check(text, target=None, at=None):
    """One Checks row: {text, target, at}. `target` is {'kind', 'id'} for something the
    plan placed, {'name'} for one of the level's own actors, {'added': n} for an added
    piece; `at` its position."""
    out = {'text': text}
    if target:
        out['target'] = dict(target)
    if at is not None:
        out['at'] = [round(float(v), 1) for v in at[:3]]
    return out


class Problem(ValueError):
    """B: a step's failure that needs a design decision, in a designer's words (`plain`),
    with its technical `detail` and the Checks rows (`checks`) that point at the cause."""

    def __init__(self, plain, detail='', target=None, at=None, checks=None):
        ValueError.__init__(self, plain)
        self.plain = plain
        self.detail = detail
        self.target = target
        self.at = at
        self.checks = list(checks) if checks is not None else (
            [check(plain, target, at)] if (target or at is not None) else [])


class Refusal(SystemExit):
    """B: a build refused for a design decision, in a designer's words (`plain`), with its
    technical `detail` and its Checks rows."""

    def __init__(self, plain, detail='', checks=()):
        SystemExit.__init__(self, plain)
        self.plain = plain
        self.detail = detail
        self.checks = list(checks)


class Fault(SystemExit):
    """C: a part of the build Rift Lord could not make, with no fallback. `plain` is
    "Rift Lord could not <part>."; `detail` the diagnostic for the console and report."""

    def __init__(self, plain, detail=''):
        SystemExit.__init__(self, plain)
        self.plain = plain
        self.detail = detail
        self.checks = []


class LeaveOut(Fault):
    """C with a fallback: added pieces (`items`, indices into plan['added']) Rift Lord could
    not put in the level; the build is made again without them."""

    def __init__(self, items, names, detail=''):
        Fault.__init__(self, 'Rift Lord could not link %s into the level.'
                       % ', '.join(names), detail)
        self.items = sorted(set(items))
        self.names = list(names)


def fault(part, detail=''):
    """Fault: 'Rift Lord could not <part>.'"""
    return Fault('Rift Lord could not %s.' % part, detail)


def internal(step, detail=''):
    """Fault for a step Rift Lord could not finish ('write the waves')."""
    return fault(step, detail)


def at(p):
    """'(x, y, z)' rounded to whole units."""
    return '(%.0f, %.0f, %.0f)' % tuple(float(v) for v in p[:3])


def where(label, p):
    """'<label> at (x, y, z)', or the position alone with no label."""
    return '%s at %s' % (label, at(p)) if label else at(p)


def technical(exc):
    """An exception as the console shows it: '<Type>: <text>', a SystemExit's text alone."""
    if isinstance(exc, (Problem, Refusal, Fault)):
        return exc.detail or exc.plain
    if isinstance(exc, SystemExit):
        return str(exc.code if exc.code is not None else '') or 'SystemExit'
    return '%s: %s' % (type(exc).__name__, exc)


def refusal(exc, step, plain=()):
    """What a build raises for `exc` caught during `step`: a Problem or Refusal as a
    Refusal in its own words (B), a Fault as it is; one of the `plain` exception classes
    (written for a designer) as a Refusal; anything else as a Fault for `step` (C)."""
    if isinstance(exc, Fault):
        return exc
    if isinstance(exc, (Problem, Refusal)):
        return Refusal(exc.plain, exc.detail, exc.checks)
    if plain and isinstance(exc, plain):
        return Refusal(str(exc), getattr(exc, 'detail', '') or '')
    return fault(step, technical(exc))


def _origin(exc):
    """The riftlord-relative path of the file `exc` was raised in, or ''."""
    tb = exc.__traceback__
    path = ''
    while tb is not None:
        path = tb.tb_frame.f_code.co_filename
        tb = tb.tb_next
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.abspath(path) if path else ''
    if not path.startswith(here + os.sep):
        return ''
    return os.path.relpath(path, here).replace(os.sep, '/')


def designer_written(exc):
    """True when `exc` already speaks to a designer: a Problem or Refusal, or a SystemExit
    raised outside the build's internals (Missions, records, the editors' own checks)."""
    if isinstance(exc, Fault):
        return False
    if isinstance(exc, (Problem, Refusal)):
        return True
    if not isinstance(exc, SystemExit):
        return False
    origin = _origin(exc)
    if not origin or origin in DESIGNER_MODULES:
        return True
    return (origin.split('/', 1)[0] not in INTERNAL_DIRS
            and origin not in INTERNAL_MODULES)


def is_fault(exc):
    """True when `exc` is Rift Lord's own (C): a Fault, any error that is not a SystemExit,
    or a SystemExit from the build's internals."""
    return not designer_written(exc)


def explain(exc, stage=None, reports=None, title=''):
    """(shown, detail, checks) for an exception that stopped a build: what the designer
    sees, what the console keeps at debug level, and the Checks rows it points at. A fault
    (C) is "Rift Lord could not <part>."; with `reports` (a folder) its diagnostic is saved
    there and the message names the file."""
    if isinstance(exc, (Problem, Refusal)) and not isinstance(exc, Fault):
        return exc.plain, exc.detail, list(exc.checks)
    if designer_written(exc):
        return technical(exc), '', []
    plain = exc.plain if isinstance(exc, Fault) else \
        'Rift Lord could not %s.' % STEPS.get(stage, DEFAULT_STEP)
    detail = technical(exc)
    if reports:
        try:
            path = save_report(reports, title, plain, detail, exc)
            plain = '%s The full report is saved as %s.' % (plain, path)
        except OSError:
            pass
    return plain, detail, []


def save_report(folder, title, plain, detail, exc=None):
    """Write a fault's report (what was shown, the detail, the traceback) into `folder`,
    keeping the newest REPORTS_KEPT; returns its path."""
    import traceback
    os.makedirs(folder, exist_ok=True)
    stem = re.sub(r'[^A-Za-z0-9]+', '_', title or 'build').strip('_') or 'build'
    path = os.path.join(folder, '%s-%s.txt' % (stem, time.strftime('%Y%m%d-%H%M%S')))
    with open(path, 'w') as fh:
        fh.write('%s\n%s\n\n%s\n\n%s\n' % (
            title or 'build', time.strftime('%Y-%m-%d %H:%M:%S'), plain, detail))
        if exc is not None and exc.__traceback__ is not None:
            fh.write('\n' + ''.join(traceback.format_exception(type(exc), exc,
                                                               exc.__traceback__)))
    kept = sorted((os.path.join(folder, f) for f in os.listdir(folder)
                   if f.endswith('.txt')), key=os.path.getmtime, reverse=True)
    for old in kept[REPORTS_KEPT:]:
        try:
            os.remove(old)
        except OSError:
            pass
    return path


def left_out(what, detail='', fix='', effect='', verb='write'):
    """A warning for a part a build went on without. With `fix` (B) it says what to change:
    'The build left out <what>: <fix>.' Without (C) it is Rift Lord's: 'Rift Lord could not
    <verb> <what>, so the mission is built without it.', or '...: <effect>.' when `effect`
    says what the mission does instead. The detail is printed, so it reaches the console at
    debug level."""
    if detail:
        print('left out: %s: %s' % (what, detail))
    if fix:
        fix = fix.rstrip('.')
        if fix[:1].isupper() and fix[1:2].islower():
            fix = fix[:1].lower() + fix[1:]
        return 'The build left out %s: %s.' % (what, fix)
    if effect:
        return 'Rift Lord could not %s %s; %s.' % (verb, what, effect.rstrip('.'))
    return 'Rift Lord could not %s %s, so the mission is built without it.' % (verb, what)


def warn(text):
    """Write a warning to the console under the source the current thread works as."""
    from riftlord.core import console
    console.write(text, getattr(console._LOCAL, 'source', None) or console.SERVER, 'warn')


def jargon(text):
    """The JARGON words in `text`, for the tests."""
    low = str(text).lower()
    return [w for w in JARGON if re.search(r'\b%s\b' % re.escape(w.lower()), low)]


# -- names as the Map Editor shows them --------------------------------------------------

#: Export names the build gives the plan's actors (planactors.emit, apply.apply_plan);
#: free_name may add a capital letter.
_ADDED = re.compile(r'^RiftLord(Spawner|Point|Rift|Door|Kit_|Mesh_)(\d+)[B-Z]?$')


def piece_label(item):
    """An added piece's name as the Map Editor's New Object card names it."""
    name = str((item or {}).get('name') or (item or {}).get('cls') or 'piece')
    try:
        from riftlord.mapeditor import leveldata
        got = leveldata.kit_label(name)
    except Exception:                                # noqa: BLE001
        got = None
    if got:
        return got
    return name[:-2] if name.endswith('_C') else name


def editor_target(export, plan=None):
    """The Checks target of an actor of a built level: {'kind', 'id'} for the plan's
    spawners, points, rifts and doors, {'added': n} for an added piece, {'name'} for the
    level's own."""
    name = str(export or '')
    m = _ADDED.match(name)
    if not m:
        return {'name': name} if name else None
    kind, n = m.group(1), int(m.group(2))
    plan = plan or {}
    key = {'Spawner': 'spawners', 'Rift': 'rifts', 'Door': 'doors'}.get(kind)
    if kind == 'Point':
        return {'kind': 'point', 'id': n}
    if key:
        items = plan.get(key) or []
        item = items[n] if 0 <= n < len(items) and isinstance(items[n], dict) else {}
        return {'kind': key[:-1], 'id': item.get('id', n)}
    return {'added': n}


def editor_name(export, plan=None):
    """The Map Editor's name for an actor of a built level: 'Spawner <id>', 'Unstable Rift
    <index>', 'Path Point <id>', 'Rift <id>', 'Door <id>', the added piece's name; the
    level's own actors keep their own names."""
    name = str(export or '')
    m = _ADDED.match(name)
    if not m:
        return name or 'an actor'
    kind, n = m.group(1), int(m.group(2))
    plan = plan or {}

    def nth(key):
        items = plan.get(key) or []
        return items[n] if 0 <= n < len(items) and isinstance(items[n], dict) else None
    if kind == 'Spawner':
        sp = nth('spawners')
        if sp is None:
            return 'an added spawner'
        if sp.get('unstable') and sp.get('index'):
            return 'Unstable Rift %s' % sp['index']
        return 'Spawner %s' % sp.get('id', n)
    if kind == 'Point':
        return 'Path Point %d' % n
    if kind == 'Rift':
        rift = nth('rifts')
        return 'Rift %s' % (rift.get('id', n) if rift else n)
    if kind == 'Door':
        door = nth('doors')
        return 'Door %s' % (door.get('id', n) if door else n)
    item = nth('added')
    return 'the added piece %s' % piece_label(item) if item else 'an added piece'


class plain_errors:
    """A block whose failures leave it as Refusals or Faults in a designer's words
    (explain), the technical detail written to the console at debug level under `source`."""

    def __init__(self, source='', reports=None):
        self.source = source
        self.reports = reports

    def __enter__(self):
        return self

    def __exit__(self, kind, exc, tb):
        if exc is None or isinstance(exc, (KeyboardInterrupt, Refusal, Fault)):
            return False
        if not isinstance(exc, (Exception, SystemExit)):
            return False
        shown, detail, checks = explain(exc, reports=self.reports)
        if detail:
            from riftlord.core import console
            console.debug(detail, self.source)
        if is_fault(exc):
            raise Fault(shown, detail) from exc
        raise Refusal(shown, detail, checks) from exc
