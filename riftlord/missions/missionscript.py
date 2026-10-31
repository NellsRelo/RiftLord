"""Mission scripts: a mission's own bpkit Blueprint, carried in its record and placed at build.

    python3 -m riftlord.missions.missionscript put RECORD SCRIPT.py [--name NAME]
        [--set VAR=JSON ...] [--replicates] [--always-relevant] [--tick SECONDS] [--root MISSIONS]
    python3 -m riftlord.missions.missionscript remove RECORD NAME [--root MISSIONS]
    python3 -m riftlord.missions.missionscript show RECORD [--root MISSIONS]
    python3 -m riftlord.missions.missionscript check SCRIPT.py [--name NAME] [--set VAR=JSON ...]

A one-off event of one mission (North Wings' opening, 2026-10-30) is a script of its own rather
than a Rift Lord feature. The plan holds it as an `added` item:

    {kind: 'script', name: 'NorthWingsIntro', matrix, source: '<bpkit script>',
     settings: {FlickerSeconds: 3.0, ...}, actor: {replicates, always_relevant, tick}}

The build compiles each one into `/Game/Mods/<mod>/<mod>_<name>` (a child of Actor) and places
it in the level copy, as the Mission Events piece is placed (missionevents.place_actor). Its
`settings` replace the script's own top-level defaults (`Name: type = value` lines) by name, so a
timing is tuned in the record without touching the script; a setting the script does not declare
is refused. `actor` takes bpkit's actor keys (bpkit-replication) and the tick interval.

`put` stores a script file in a record (backed up first, records.backup; checked with the Map
Editor's save rules), replacing a script of the same name and keeping its place and settings
unless given; it stands at the mission's Crystal, else at its first rift, else at the origin.
`check` compiles a script file alone. Close the mission in the app before `put` or `remove`:
the app saves over the record otherwise.
"""

import argparse
import copy
import json
import os
import re
import sys
import time

#: The `added` kind of a mission script.
KIND = 'script'
#: A script's name: the class is `<mod>_<name>`, so it starts with a letter and has no
#: underscore (a trailing `_<digits>` would split into an FName number).
NAME_PATTERN = re.compile(r'^[A-Za-z][A-Za-z0-9]{0,47}$')
#: Its placed actor's export name, before the script's name.
PLACED_PREFIX = 'RiftLordScript'
#: A top-level declaration with a default: `Name: type = value`.
DECLARATION = re.compile(r'^(?P<name>[A-Za-z][A-Za-z0-9]*)(?P<type>\s*:\s*[^=\n]+?)\s*=\s*(?P<value>.+?)\s*$',
                         re.M)
#: The actor keys a script item may set (bpkit.replication.ACTOR_KEYS, and `tick`).
ACTOR_KEYS = ('replicates', 'always_relevant', 'only_relevant_to_owner', 'net_update_frequency',
              'min_net_update_frequency', 'net_priority', 'net_load_on_client', 'tick')
#: The tick interval when the item gives none (seconds; a script may change its own).
DEFAULT_TICK = 0.25
#: A setting value: JSON numbers, booleans and strings.
SETTING_TYPES = (bool, int, float, str)
IDENTITY = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]


def pieces(plan):
    """The plan's mission scripts, in plan order."""
    return [a for a in (plan or {}).get('added') or []
            if isinstance(a, dict) and a.get('kind') == KIND]


def name_problem(name):
    """Why `name` cannot name a script, or None."""
    if not isinstance(name, str) or not NAME_PATTERN.match(name):
        return ('a script name is a letter then letters and digits (up to 48), no underscore: '
                '%r' % (name,))
    return None


def clean_piece(item, matrix):
    """The script as a plan keeps it (leveldata.save_plan). Raises ValueError on what the build
    could not use."""
    name = item.get('name')
    why = name_problem(name)
    if why:
        raise ValueError(why)
    source = item.get('source')
    if not isinstance(source, str) or not source.strip():
        raise ValueError('script %s has no source' % name)
    settings = item.get('settings') or {}
    if not isinstance(settings, dict):
        raise ValueError('script %s: settings must be an object' % name)
    for key, value in settings.items():
        if not NAME_PATTERN.match(str(key)) or not _setting_ok(value):
            raise ValueError('script %s: setting %r=%r is not a name and a number, true/false, '
                             'text or a list of numbers' % (name, key, value))
    actor = item.get('actor') or {}
    if not isinstance(actor, dict) or set(actor) - set(ACTOR_KEYS):
        raise ValueError('script %s: actor keys are %s' % (name, ', '.join(ACTOR_KEYS)))
    out = {'name': name, 'kind': KIND, 'matrix': matrix, 'source': source}
    if settings:
        out['settings'] = dict(settings)
    if actor:
        out['actor'] = dict(actor)
    return out


def _setting_ok(value):
    if isinstance(value, list):
        return 0 < len(value) <= 4 and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                           for v in value)
    return isinstance(value, SETTING_TYPES)


def class_name(mod, name):
    return '%s_%s' % (mod, name)


def package_path(mod, name):
    """Package path of a script's Blueprint in a mission."""
    return '/Game/Mods/%s/%s' % (mod, class_name(mod, name))


def placed_name(name):
    return PLACED_PREFIX + name


def declared(source):
    """{name: (type text, default text)} of the script's top-level declarations with a default."""
    return {m.group('name'): (m.group('type').split(':', 1)[1].strip(), m.group('value'))
            for m in DECLARATION.finditer(source)}


def _literal(kind, value):
    """`value` as script text for a declaration of type `kind`."""
    if isinstance(value, list):
        nums = ', '.join(repr(float(v)) for v in value)
        return '%s(%s)' % (kind, nums)
    if isinstance(value, bool):
        return 'True' if value else 'False'
    if isinstance(value, (int, float)):
        return repr(float(value)) if kind == 'float' else repr(value)
    return json.dumps(value)


def source_with(item):
    """The item's script with its settings put in place of the defaults they name."""
    source = item['source']
    have = declared(source)
    settings = item.get('settings') or {}
    unknown = sorted(k for k in settings if k not in have)
    if unknown:
        raise SystemExit('script %s: no top-level default to set for %s'
                         % (item.get('name'), ', '.join(unknown)))

    def one(m):
        key = m.group('name')
        if key not in settings:
            return m.group(0)
        kind = m.group('type').split(':', 1)[1].strip()
        return '%s%s = %s' % (key, m.group('type'), _literal(kind, settings[key]))
    return DECLARATION.sub(one, source)


def _desc(mod, item, folder):
    actor = dict(item.get('actor') or {})
    tick = float(actor.pop('tick', DEFAULT_TICK))
    desc = {'name': class_name(mod, item['name']), 'path': package_path(mod, item['name']),
            'script': item['name'] + '.py',
            'defaults': {'PrimaryActorTick': {'bCanEverTick': True, 'TickInterval': tick}},
            '_dir': folder}
    desc.update(actor)
    return desc


def compile_item(mod, item, folder):
    """(package path, compiled bpkit package) of one script, its source written into `folder`."""
    from riftlord.types import setup
    if setup.unavailable():
        raise SystemExit(setup.unavailable())
    from bpkit import actor, widget
    from bpkit.typedb import add_package
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, item['name'] + '.py'), 'w', encoding='utf-8') as fh:
        fh.write(source_with(item))
    desc = _desc(mod, item, folder)
    widget.declare([desc])
    pkg = actor.compile(desc)
    problems = widget.verify(pkg, desc['name'], desc['path'])
    if problems:
        raise SystemExit('mission script %s not built: %s' % (item['name'], '; '.join(problems)))
    add_package(widget.db(), pkg, desc['path'])
    return desc['path'], pkg


def package(level, mod, plan):
    """{package path: (uasset, uexp)} of the plan's scripts' Blueprints ({} without any)."""
    items = pieces(plan)
    if not items:
        return {}
    names = [i.get('name') for i in items]
    if len(set(names)) != len(names):
        raise SystemExit('two mission scripts share a name: %s' % ', '.join(names))
    from riftlord.core import gamepath
    out = {}
    for item in items:
        why = name_problem(item.get('name'))
        if why:
            raise SystemExit(why)
        folder = os.path.join(gamepath.scratch('mission-scripts'), mod, item['name'])
        path, pkg = compile_item(mod, item, folder)
        out[path] = pkg.write()
    return out


def apply(builder, level, plan, report):
    """Place the plan's scripts in the level copy. Returns how many were placed."""
    items = pieces(plan)
    if not items:
        return 0
    mod = (plan or {}).get('_modKey')
    if not mod:
        report['skipped'].append('mission scripts: no mod name to build them under')
        return 0
    from riftlord.missions import missionevents
    for item in items:
        missionevents.place_actor(builder, item['matrix'], package_path(mod, item['name']),
                                  class_name(mod, item['name']), placed_name(item['name']))
    report.setdefault('notes', []).append('mission scripts placed: %s'
                                          % ', '.join(i['name'] for i in items))
    return len(items)


def check_files(files, plan):
    """[{name, ok, detail}]: each script's Blueprint is in the pak with its class and default
    object, names every asset its source names, and the level places it with them serialized
    before creation."""
    from riftlord.formats.ue.uassetio import Package
    from riftlord.missions import missionevents
    out = []
    mod = (plan or {}).get('_modKey') or _mod_in(files)
    for item in pieces(plan):
        name = item['name']
        base = 'OMD/Content' + package_path(mod, name)[len('/Game'):] if mod else None
        leaf = class_name(mod, name) if mod else name
        ok = bool(base and base + '.uasset' in files and base + '.uexp' in files)
        names = []
        if ok:
            names = [n for n, _h in Package(files[base + '.uasset']).names]
            ok = leaf + '_C' in names and 'Default__' + leaf + '_C' in names
        out.append({'name': 'mission script %s Blueprint in the pak' % name, 'ok': ok,
                    'detail': base or 'no mod folder'})
        missing = [w for w in named_assets(item['source']) if w not in names]
        out.append({'name': 'mission script %s imports every asset it names' % name,
                    'ok': ok and not missing, 'detail': ', '.join(missing) or
                    '%d assets' % len(named_assets(item['source']))})
        placed, detail = False, 'the level does not place it'
        for key, data in files.items():
            if not key.endswith('.umap') or key[:-5] + '.uexp' not in files:
                continue
            pkg = Package(data, files[key[:-5] + '.uexp'])
            lnames = [n for n, _h in pkg.names]
            if placed_name(name) not in lnames:
                continue
            placed, detail = missionevents.placed_ok(pkg, lnames, leaf, placed_name(name))
            break
        out.append({'name': 'mission script %s placed with its class preloaded' % name,
                    'ok': placed, 'detail': detail})
    return out


#: An asset the script names inline, `Asset("Kind'/Game/Path.Leaf'")`.
ASSET = re.compile(r"Asset\(\"[A-Za-z]+'[^\"'.]+\.([^\"']+)'\"\)")
#: A class alias, `Alias = Blueprint("/Game/Path.Leaf_C")`.
ALIAS = re.compile(r"^(?P<alias>[A-Za-z][A-Za-z0-9]*)\s*=\s*Blueprint\(\"[^\".]+\.(?P<leaf>[^\"]+)\"\)",
                   re.M)


def named_assets(source):
    """Object names the compiled script must import: each inline asset, and each class alias
    the script uses (an alias it never uses imports nothing)."""
    out = set(ASSET.findall(source))
    for m in ALIAS.finditer(source):
        if len(re.findall(r'\b%s\b' % m.group('alias'), source)) > 1:
            out.add(m.group('leaf'))
    return sorted(out)


def _mod_in(files):
    for key in files:
        if key.startswith('OMD/Content/Mods/') and key.endswith('_MissionProtos.uasset'):
            return key.split('/')[3]
    return None


# -- the record ------------------------------------------------------------------------------

def _standing(plan):
    """Where a new script stands: the mission's Crystal, else its first rift, else the origin."""
    for a in (plan or {}).get('added') or []:
        if isinstance(a, dict) and a.get('kind') == 'crystal' and len(a.get('matrix') or []) == 16:
            return list(a['matrix'])
    for r in (plan or {}).get('rifts') or []:
        at = r.get('at') if isinstance(r, dict) else None
        if isinstance(at, list) and len(at) >= 3:
            return IDENTITY[:12] + [float(at[0]), float(at[1]), float(at[2]), 1.0]
    return list(IDENTITY)


def put(rec, name, source, settings=None, actor=None):
    """The record with the script `name` put in its plan (replaced in place when there): its
    matrix kept or chosen (_standing), `settings` and `actor` merged over the old item's."""
    new = copy.deepcopy(rec)
    plan = new.setdefault('plan', {})
    added = plan.setdefault('added', [])
    old = next((a for a in pieces(plan) if a.get('name') == name), None)
    item = {'name': name, 'kind': KIND,
            'matrix': list(old['matrix']) if old else _standing(plan), 'source': source}
    merged = dict((old or {}).get('settings') or {}, **(settings or {}))
    if merged:
        item['settings'] = merged
    acts = dict((old or {}).get('actor') or {}, **(actor or {}))
    if acts:
        item['actor'] = acts
    item = clean_piece(item, item['matrix'])
    source_with(item)
    if old is not None:
        added[added.index(old)] = item
    else:
        added.append(item)
    return new


def remove(rec, name):
    new = copy.deepcopy(rec)
    added = (new.get('plan') or {}).get('added') or []
    keep = [a for a in added if not (isinstance(a, dict) and a.get('kind') == KIND
                                     and a.get('name') == name)]
    if len(keep) == len(added):
        raise SystemExit('no mission script %s in %s' % (name, rec.get('id')))
    new['plan']['added'] = keep
    return new


def _write(root, ident, old, new, what):
    from riftlord.missions import records
    try:
        warnings, _cleaned = records.validate_plan(new)
    except (ValueError, SystemExit) as exc:
        raise SystemExit('refused by the Map Editor\'s save rules: %s' % exc)
    new['updated'] = time.time()
    kept = records.backup(root, ident, note='before missionscript %s' % what)
    records.write_record(root, ident, new)
    print('backed up to %s' % kept)
    for line in records.diff(old, new):
        print('  ' + line)
    for w in warnings:
        print('warning: %s' % w)
    print('rebuild the mission for the change to reach the game')


def _settings(pairs):
    out = {}
    for pair in pairs or []:
        if '=' not in pair:
            raise SystemExit('expected VAR=JSON, got %r' % pair)
        key, raw = pair.split('=', 1)
        try:
            out[key] = json.loads(raw)
        except ValueError:
            out[key] = raw
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('put', help='store a script file in a record')
    p.add_argument('record')
    p.add_argument('file')
    p.add_argument('--name', help='the script name (default: the file name)')
    p.add_argument('--set', action='append', default=[], metavar='VAR=JSON')
    p.add_argument('--replicates', action='store_true')
    p.add_argument('--always-relevant', action='store_true')
    p.add_argument('--tick', type=float)
    p.add_argument('--root')
    p = sub.add_parser('remove', help='take a script out of a record')
    p.add_argument('record')
    p.add_argument('name')
    p.add_argument('--root')
    p = sub.add_parser('show', help="list a record's scripts and their settings")
    p.add_argument('record')
    p.add_argument('--root')
    p = sub.add_parser('check', help='compile a script file alone')
    p.add_argument('file')
    p.add_argument('--name')
    p.add_argument('--set', action='append', default=[], metavar='VAR=JSON')
    p.add_argument('--replicates', action='store_true')
    p.add_argument('--always-relevant', action='store_true')
    args = ap.parse_args(argv)
    from riftlord.missions import records
    if args.cmd == 'check':
        name = args.name or os.path.splitext(os.path.basename(args.file))[0]
        with open(args.file, encoding='utf-8') as fh:
            item = {'name': name, 'kind': KIND, 'source': fh.read(),
                    'settings': _settings(args.set), 'actor': _actor(args)}
        item = clean_piece(item, list(IDENTITY))
        from riftlord.core import gamepath
        path, pkg = compile_item('RiftLordScriptCheck', item,
                                 os.path.join(gamepath.scratch('mission-scripts'), 'check', name))
        print('compiled %s: %d exports' % (path, len(pkg.exports)))
        return 0
    root = records.missions_root(args.root)
    rec = records.load(root, args.record)
    if args.cmd == 'show':
        for item in pieces(rec.get('plan')):
            print('%s at (%s): %d lines; settings %s; actor %s' % (
                item['name'], ', '.join('%.0f' % v for v in item['matrix'][12:15]),
                item['source'].count('\n') + 1, json.dumps(item.get('settings') or {}),
                json.dumps(item.get('actor') or {})))
        return 0
    if args.cmd == 'remove':
        _write(root, args.record, rec, remove(rec, args.name), 'remove %s' % args.name)
        return 0
    name = args.name or os.path.splitext(os.path.basename(args.file))[0]
    with open(args.file, encoding='utf-8') as fh:
        source = fh.read()
    new = put(rec, name, source, _settings(args.set), _actor(args))
    _write(root, args.record, rec, new, 'put %s' % name)
    return 0


def _actor(args):
    out = {}
    if getattr(args, 'replicates', False):
        out['replicates'] = True
    if getattr(args, 'always_relevant', False):
        out['always_relevant'] = True
    if getattr(args, 'tick', None) is not None:
        out['tick'] = args.tick
    return out


if __name__ == '__main__':
    sys.exit(main())
