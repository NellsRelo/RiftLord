"""Read, check, back up and edit Missions records (<Missions>/.riftlord/records/<id>.json).

    python3 -m riftlord.records [--root MISSIONS] list
    python3 -m riftlord.records show ID [--path plan.look]
    python3 -m riftlord.records check ID
    python3 -m riftlord.records backup ID
    python3 -m riftlord.records backups [ID]
    python3 -m riftlord.records diff ID [BACKUP | --against FILE] [--full]
    python3 -m riftlord.records restore ID BACKUP
    python3 -m riftlord.records set ID dotted.path=JSON [more.path=JSON ...]

--root (or RIFTLORD_RECORDS_ROOT) names the Missions folder; by default it is the one
Rift Lord uses (OMD3_BUCKET, the folder chosen in the app, or RiftLord/Missions in the
user's data folder). Every write -- restore and set -- backs the record up first, with
its picture files, into ~/.cache/rift-lord/record-backups/<id>.<timestamp>.json
(--backups or RIFTLORD_RECORD_BACKUPS for another folder). BACKUP is a file path, a
backup's file name, its timestamp, or `latest`.

`set` checks a changed plan with the Map Editor's own save rules (leveldata.save_plan,
run against a scratch plans folder, so nothing else is written) and bumps `updated`.
Close the mission in the app before writing its record: the app saves over it otherwise.
"""

import argparse
import copy
import datetime
import glob
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

BACKUPS = os.environ.get('RIFTLORD_RECORD_BACKUPS') or os.path.join(
    os.path.expanduser('~'), '.cache', 'rift-lord', 'record-backups')
#: Record fields that name picture files kept beside the record.
PICTURE_SLOTS = ('picture', 'portalPicture')
#: How many changed paths `diff` prints before summarising (without --full).
DIFF_LINES = 60


# -- where things are -----------------------------------------------------------------

def missions_root(explicit=None):
    root = explicit or os.environ.get('RIFTLORD_RECORDS_ROOT')
    if root:
        return os.path.abspath(root)
    from riftlord.core import gamepath
    chosen = os.environ.get('OMD3_BUCKET') or gamepath.load_settings().get('bucket')
    if chosen:
        return chosen
    return os.path.join(os.environ.get('XDG_DATA_HOME') or
                        os.path.join(os.path.expanduser('~'), '.local', 'share'),
                        'RiftLord', 'Missions')


def records_dir(root):
    return os.path.join(root, '.riftlord', 'records')


def record_path(root, ident):
    if not ident or '/' in ident or '\\' in ident or ident.startswith('.'):
        raise SystemExit('not a mission id: %r' % ident)
    return os.path.join(records_dir(root), ident + '.json')


def load(root, ident):
    path = record_path(root, ident)
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise SystemExit('no record %s in %s' % (ident, records_dir(root)))


def write_record(root, ident, rec):
    """Write a record whole: beside it, then renamed over it."""
    path = record_path(root, ident)
    part = path + '.part'
    with open(part, 'w', encoding='utf-8') as fh:
        json.dump(rec, fh, indent=1)
    os.replace(part, path)


def picture_files(root, rec):
    out = []
    for slot in PICTURE_SLOTS:
        pic = rec.get(slot)
        if isinstance(pic, dict) and pic.get('file'):
            path = os.path.join(records_dir(root), pic['file'])
            if os.path.exists(path):
                out.append((slot, path))
    return out


# -- backups ----------------------------------------------------------------------------

def backup(root, ident, backups=BACKUPS, note=None):
    """Copy the record and its pictures into `backups`. Returns the backup's path."""
    rec = load(root, ident)
    os.makedirs(backups, exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')[:-3]
    dest = os.path.join(backups, '%s.%s.json' % (ident, stamp))
    shutil.copy2(record_path(root, ident), dest)
    for _slot, path in picture_files(root, rec):
        suffix = os.path.basename(path)[len(ident):]           # e.g. .picture.png
        shutil.copy2(path, os.path.join(backups, '%s.%s%s' % (ident, stamp, suffix)))
    if note:
        with open(dest[:-5] + '.note.txt', 'w') as fh:
            fh.write(note + '\n')
    return dest


def list_backups(ident=None, backups=BACKUPS):
    pattern = '%s.*.json' % (glob.escape(ident) if ident else '*')
    out = [p for p in glob.glob(os.path.join(backups, pattern))
           if not p.endswith('.note.txt')]
    return sorted(out, key=lambda p: os.path.basename(p).rsplit('.', 2)[-2])


def find_backup(ident, which, backups=BACKUPS):
    if which and os.path.isfile(which):
        return which
    hits = list_backups(ident, backups)
    if not hits:
        raise SystemExit('no backups of %s in %s' % (ident, backups))
    if which in (None, '', 'latest'):
        return hits[-1]
    for h in hits:
        name = os.path.basename(h)
        if name == which or name == which + '.json' or name.split('.')[-2] == which:
            return h
    raise SystemExit('no backup %r of %s; see `backups %s`' % (which, ident, ident))


# -- summaries ------------------------------------------------------------------------------

def when(t):
    return datetime.datetime.fromtimestamp(t).strftime('%Y-%m-%d %H:%M:%S') if t else '-'


def counts(rec):
    """The numbers `show` and `diff` print."""
    plan = rec.get('plan') or {}
    spec = rec.get('spec') or {}
    added = [a for a in plan.get('added') or [] if isinstance(a, dict)]
    by_kind, fx = {}, {}
    for a in added:
        by_kind[a.get('kind') or 'mesh'] = by_kind.get(a.get('kind') or 'mesh', 0) + 1
        if a.get('kind') == 'fx':
            fx[a.get('fx')] = fx.get(a.get('fx'), 0) + 1
    spawners = {}
    for s in plan.get('spawners') or []:
        spawners[s.get('kind') or '?'] = spawners.get(s.get('kind') or '?', 0) + 1
    look = plan.get('look') or {}
    used = sorted({int(k) for w in spec.get('waves') or [] for k in (w.get('spawns') or {})})
    return {'added': len(added), 'added_by_kind': by_kind, 'fx': dict(sorted(fx.items())),
            'doors': len(plan.get('doors') or []), 'spawners': spawners,
            'points': len(plan.get('points') or []), 'removed': len(plan.get('removed') or []),
            'edits': len(plan.get('edits') or []), 'wiring': len(plan.get('wiring') or []),
            'paints': len(look.get('paints') or {}), 'look': {k: v for k, v in look.items()
                                                            if isinstance(v, str)},
            'minimap': plan.get('minimap') or {}, 'world': plan.get('world') or {},
            'spec_waves': len(spec.get('waves') or []), 'spec_spawners': used,
            'spec_map': spec.get('map')}


def build_state(root, rec):
    """'built', 'stale', 'unbuilt' or 'failed', as the Missions page shows it."""
    os.environ['OMD3_BUCKET'] = root
    from riftlord.missions import bucket
    try:
        return bucket.build_state(rec, bucket._source(rec['id']))
    except Exception as exc:                          # noqa: BLE001
        return 'unknown (%s)' % exc


def get_path(obj, dotted):
    cur = obj
    for part in [p for p in dotted.split('.') if p]:
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            if part not in cur:
                raise SystemExit('no %r in %s' % (part, dotted))
            cur = cur[part]
        else:
            raise SystemExit('%s: %r is not an object or list' % (dotted, part))
    return cur


def set_path(obj, dotted, value):
    parts = [p for p in dotted.split('.') if p]
    if not parts:
        raise SystemExit('empty path')
    cur = obj
    for part in parts[:-1]:
        if isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur.setdefault(part, {})
    last = parts[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        cur[last] = value


def show(root, ident, path=None):
    rec = load(root, ident)
    if path:
        print(json.dumps(get_path(rec, path), indent=1))
        return
    c = counts(rec)
    print('%s  "%s"' % (ident, rec.get('title')))
    print('  level %s, map %s, mod %s, kind %s, tools %s' % (
        rec.get('level'), rec.get('map'), rec.get('mod'), rec.get('kind'),
        ', '.join(rec.get('tools') or [])))
    print('  created %s, updated %s, baked %s; build %s%s' % (
        when(rec.get('created')), when(rec.get('updated')), when(rec.get('bakedAt')),
        build_state(root, rec), ('; bake error: ' + rec['bakeError']) if rec.get('bakeError') else ''))
    print('  added %d: %s' % (c['added'], ', '.join('%s %d' % kv for kv in sorted(c['added_by_kind'].items()))))
    print('  fx by effect: %s' % (', '.join('%s %d' % kv for kv in c['fx'].items()) or 'none'))
    print('  doors %d; spawners %s; points %d; wiring %d; removed %d; edits %d' % (
        c['doors'], c['spawners'], c['points'], c['wiring'], c['removed'], c['edits']))
    print('  look %s; paints %d' % (c['look'], c['paints']))
    print('  minimap %s; world %s' % (c['minimap'] or 'shipped', c['world']))
    print('  spec: map %s, %d waves, spawners used %s' % (c['spec_map'], c['spec_waves'], c['spec_spawners']))
    pics = picture_files(root, rec)
    print('  pictures: %s' % (', '.join('%s %s' % (s, os.path.basename(p)) for s, p in pics) or 'none'))
    print('  file %s' % record_path(root, ident))


# -- check ----------------------------------------------------------------------------------

def check(root, ident):
    """[warning] for one record."""
    rec = load(root, ident)
    plan = rec.get('plan') or {}
    spec = rec.get('spec') or {}
    out = []
    from riftlord.build.world import lavafx
    effects = set(lavafx.DONORS)
    for i, a in enumerate(plan.get('added') or []):
        if not isinstance(a, dict):
            continue
        if a.get('kind') != 'fx' and (a.get('fx') or a.get('name') in effects):
            out.append('added[%d] %r looks like an effect but its kind is %r: the build reads '
                       'it as a mesh' % (i, a.get('fx') or a.get('name'), a.get('kind')))
        if a.get('kind') == 'fx' and a.get('fx') not in effects:
            out.append('added[%d]: no such effect %r' % (i, a.get('fx')))
    paints = (plan.get('look') or {}).get('paints') or {}
    if paints:
        from riftlord.mapeditor import leveldata
        try:
            pieces = leveldata.level(rec.get('level'))['pieces']
            removed = set(plan.get('removed') or [])
            for key in sorted(paints, key=lambda k: int(k)):
                n = int(key)
                if n >= len(pieces):
                    out.append('look.paints %s: no such piece in %s' % (key, rec.get('level')))
                elif n in removed:
                    out.append('look.paints %s: the piece is removed' % key)
        except Exception as exc:                      # noqa: BLE001
            out.append('look.paints: the level could not be read: %s' % exc)
        try:
            every = set(leveldata.materials())
            from riftlord.build.world import lavafx
            for key, value in sorted(paints.items()):
                for mat in lavafx.materials_in(value):
                    if mat not in every:
                        out.append('look.paints %s: no such material %r' % (key, mat))
        except Exception as exc:                      # noqa: BLE001
            out.append('look.paints: the materials could not be listed: %s' % exc)
    if spec.get('waves'):
        have = {int(s['index']) for s in plan.get('spawners') or [] if s.get('index') is not None}
        gone = {w.get('name') for w in plan.get('wiring') or [] if w.get('gone')}
        note = ''
        if rec.get('level'):
            try:
                from riftlord.onslaught import onslaught
                for sp in onslaught.map_report(rec['level'])['spawners']:
                    if sp['export'] not in gone:
                        have.add(int(sp['index']))
            except Exception as exc:                  # noqa: BLE001
                note = ' (the shipped spawners could not be read: %s)' % exc
        if not plan:
            have = None                               # an Onslaught Editor mission: its map has them
        for w in spec['waves']:
            for k in (w.get('spawns') or {}):
                if have is not None and int(k) not in have:
                    out.append('spec wave %s spawns at spawner %s, which the plan does not have%s'
                               % (w.get('id'), k, note))
    state = build_state(root, rec)
    if state != 'built':
        out.append('build is %s: build it again before installing' % state)
    return out


# -- diff -------------------------------------------------------------------------------------

def _keyed(items):
    """{id: item} when every item is an object with a unique id, else None."""
    if not items or not all(isinstance(x, dict) and 'id' in x for x in items):
        return None
    out = {x['id']: x for x in items}
    return out if len(out) == len(items) else None


def diff_values(a, b, path, out):
    """Readable differences from `a` (old) to `b` (new), appended to `out`."""
    if type(a) is not type(b) and not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        out.append('%s: %s -> %s' % (path or '.', _short(a), _short(b)))
        return
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b), key=str):
            p = '%s.%s' % (path, k) if path else str(k)
            if k not in a:
                out.append('%s: added %s' % (p, _short(b[k])))
            elif k not in b:
                out.append('%s: removed (was %s)' % (p, _short(a[k])))
            elif a[k] != b[k]:
                diff_values(a[k], b[k], p, out)
        return
    if isinstance(a, list):
        ka, kb = _keyed(a), _keyed(b)
        if ka is not None and kb is not None:
            for k in sorted(set(ka) | set(kb), key=str):
                p = '%s[id=%s]' % (path, k)
                if k not in ka:
                    out.append('%s: added %s' % (p, _short(kb[k])))
                elif k not in kb:
                    out.append('%s: removed' % p)
                elif ka[k] != kb[k]:
                    diff_values(ka[k], kb[k], p, out)
            return
        if all(not isinstance(x, (dict, list)) for x in a + b) and len(a) + len(b) > 16:
            sa, sb = set(map(json.dumps, a)), set(map(json.dumps, b))
            out.append('%s: %d -> %d items (+%d -%d)%s' % (
                path, len(a), len(b), len(sb - sa), len(sa - sb),
                (' added %s' % ', '.join(sorted(sb - sa)[:8])) if sb - sa else ''))
            return
        if all(isinstance(x, dict) for x in a + b) and (len(a) > 8 or len(b) > 8):
            def label(x):
                return '%s:%s' % (x.get('kind') or '', x.get('fx') or x.get('name') or x.get('unit') or '')
            ca, cb = {}, {}
            for x in a:
                ca[label(x)] = ca.get(label(x), 0) + 1
            for x in b:
                cb[label(x)] = cb.get(label(x), 0) + 1
            delta = {k: cb.get(k, 0) - ca.get(k, 0) for k in set(ca) | set(cb)
                     if cb.get(k, 0) != ca.get(k, 0)}
            moved = sum(1 for x, y in zip(a, b) if label(x) == label(y) and x != y)
            out.append('%s: %d -> %d items%s%s' % (
                path, len(a), len(b),
                (' (%s)' % ', '.join('%s %+d' % kv for kv in sorted(delta.items()))) if delta else '',
                ('; %d changed in place' % moved) if moved else ''))
            return
        if len(a) != len(b):
            out.append('%s: %s -> %s' % (path, _short(a), _short(b)))
            return
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                diff_values(x, y, '%s[%d]' % (path, i), out)
        return
    out.append('%s: %s -> %s' % (path or '.', _short(a), _short(b)))


def _short(v, n=90):
    s = json.dumps(v, sort_keys=True)
    return s if len(s) <= n else s[:n - 3] + '...'


def diff(old, new, full=False):
    """Lines describing what changed from record `old` to record `new`."""
    lines = []
    for section in ('plan', 'spec'):
        sub = []
        diff_values(old.get(section) or {}, new.get(section) or {}, section, sub)
        lines += sub
    rest_a = {k: v for k, v in old.items() if k not in ('plan', 'spec')}
    rest_b = {k: v for k, v in new.items() if k not in ('plan', 'spec')}
    diff_values(rest_a, rest_b, '', lines)
    if not full and len(lines) > DIFF_LINES:
        lines = lines[:DIFF_LINES] + ['... %d more (--full shows them)' % (len(lines) - DIFF_LINES)]
    return lines


# -- set ----------------------------------------------------------------------------------------

def validate_plan(rec):
    """leveldata.save_plan's checks on the record's plan, with its plans folder pointed at
    a scratch folder. Returns (warnings, the cleaned plan). Raises on what it refuses."""
    from riftlord.mapeditor import leveldata
    plan = rec.get('plan')
    if not isinstance(plan, dict):
        return [], None
    scratch = tempfile.mkdtemp(prefix='records-validate-')
    saved = leveldata.PLANS
    try:
        leveldata.PLANS = scratch
        got = leveldata.save_plan(plan.get('level') or rec.get('level'), copy.deepcopy(plan))
        with open(leveldata.plan_path(plan.get('level') or rec.get('level'))) as fh:
            cleaned = json.load(fh)
    finally:
        leveldata.PLANS = saved
        shutil.rmtree(scratch, ignore_errors=True)
    return got.get('warnings') or [], cleaned


def set_values(root, ident, assignments, backups=BACKUPS, dry_run=False):
    rec = load(root, ident)
    new = copy.deepcopy(rec)
    touched = []
    for a in assignments:
        if '=' not in a:
            raise SystemExit('expected dotted.path=JSON, got %r' % a)
        path, raw = a.split('=', 1)
        try:
            value = json.loads(raw)
        except ValueError as exc:
            raise SystemExit('%s: not JSON (%s); quote strings, e.g. %s=\'"text"\'' % (path, exc, path))
        if path.split('.')[0] in ('id',):
            raise SystemExit('the record id cannot be set')
        set_path(new, path, value)
        touched.append(path)
    warnings = []
    if any(p.split('.')[0] == 'plan' for p in touched):
        try:
            warnings, cleaned = validate_plan(new)
        except (ValueError, SystemExit) as exc:
            raise SystemExit('refused by the Map Editor\'s save rules: %s' % exc)
        for p in touched:
            if p.startswith('plan.') and cleaned is not None:
                try:
                    get_path({'plan': cleaned}, p)
                except (SystemExit, KeyError, IndexError, ValueError):
                    warnings.append('%s is not kept by the Map Editor\'s save: the build ignores it' % p)
    new['updated'] = time.time()
    if dry_run:
        return None, diff(rec, new), warnings
    kept = backup(root, ident, backups, note='before set %s' % ' '.join(touched))
    write_record(root, ident, new)
    return kept, diff(rec, new), warnings


def restore(root, ident, which, backups=BACKUPS):
    src = find_backup(ident, which, backups)
    with open(src, encoding='utf-8') as fh:
        rec = json.load(fh)
    if rec.get('id') not in (None, ident):
        raise SystemExit('%s is a backup of %s, not %s' % (src, rec.get('id'), ident))
    kept = backup(root, ident, backups, note='before restoring %s' % os.path.basename(src))
    old = load(root, ident)
    stem = os.path.basename(src)[:-len('.json')]
    for slot in PICTURE_SLOTS:
        pic = rec.get(slot)
        if isinstance(pic, dict) and pic.get('file'):
            got = os.path.join(os.path.dirname(src), stem + pic['file'][len(ident):])
            if os.path.exists(got):
                shutil.copy2(got, os.path.join(records_dir(root), pic['file']))
    write_record(root, ident, rec)
    return src, kept, diff(old, rec)


# -- command line ----------------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(prog='python3 -m riftlord.records',
                                 description=__doc__.split('\n')[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog='\n'.join(__doc__.split('\n')[2:]))
    ap.add_argument('--root', help='the Missions folder (default: the one Rift Lord uses)')
    ap.add_argument('--backups', default=BACKUPS, help='backup folder (default %(default)s)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('list', help='every record: id, title, level, updated, build state')
    p = sub.add_parser('show', help='a summary, or one value with --path')
    p.add_argument('id')
    p.add_argument('--path', help='print the JSON at this dotted path (e.g. plan.look)')
    p = sub.add_parser('check', help='warnings: fx kinds, paints, spawners, a stale build')
    p.add_argument('id')
    p = sub.add_parser('backup', help='copy the record and its pictures to the backups')
    p.add_argument('id')
    p = sub.add_parser('backups', help='list backups')
    p.add_argument('id', nargs='?')
    p = sub.add_parser('diff', help='plan/spec-level differences from a backup or file')
    p.add_argument('id')
    p.add_argument('backup', nargs='?', help='a backup (default: the latest)')
    p.add_argument('--against', metavar='FILE', help='another record file')
    p.add_argument('--full', action='store_true')
    p = sub.add_parser('restore', help='put a backup back (backing up the current one first)')
    p.add_argument('id')
    p.add_argument('backup')
    p = sub.add_parser('set', help='change values: dotted.path=JSON (backs up first)')
    p.add_argument('id')
    p.add_argument('assign', nargs='+')
    p.add_argument('--dry-run', action='store_true', help='show the change, write nothing')
    args = ap.parse_args(argv)
    root = missions_root(args.root)

    if args.cmd == 'list':
        folder = records_dir(root)
        files = sorted(f for f in os.listdir(folder) if f.endswith('.json')) if os.path.isdir(folder) else []
        print('%d records in %s' % (len(files), folder))
        for f in files:
            ident = f[:-5]
            rec = load(root, ident)
            print('  %-28s %-26s %-14s updated %s  build %s' % (
                ident, '"%s"' % (rec.get('title') or ''), rec.get('level') or rec.get('map') or '-',
                when(rec.get('updated')), build_state(root, rec)))
        return 0
    if args.cmd == 'show':
        show(root, args.id, args.path)
        return 0
    if args.cmd == 'check':
        warnings = check(root, args.id)
        print('%s: %s' % (args.id, 'no warnings' if not warnings else '%d warning(s)' % len(warnings)))
        for w in warnings:
            print('  - ' + w)
        return 1 if warnings else 0
    if args.cmd == 'backup':
        print(backup(root, args.id, args.backups))
        return 0
    if args.cmd == 'backups':
        for h in list_backups(args.id, args.backups):
            note = h[:-5] + '.note.txt'
            print('%s%s' % (os.path.basename(h), ('  (%s)' % open(note).read().strip()) if os.path.exists(note) else ''))
        return 0
    if args.cmd == 'diff':
        new = load(root, args.id)
        if args.against:
            with open(args.against, encoding='utf-8') as fh:
                old = json.load(fh)
            label = args.against
        else:
            label = find_backup(args.id, args.backup, args.backups)
            with open(label, encoding='utf-8') as fh:
                old = json.load(fh)
        lines = diff(old, new, args.full)
        print('%s -> current %s:' % (os.path.basename(label), args.id))
        for line in lines or ['no differences']:
            print('  ' + line)
        return 0
    if args.cmd == 'restore':
        src, kept, lines = restore(root, args.id, args.backup, args.backups)
        print('restored %s (the record it replaced is kept as %s)' % (os.path.basename(src), kept))
        for line in lines:
            print('  ' + line)
        return 0
    if args.cmd == 'set':
        kept, lines, warnings = set_values(root, args.id, args.assign, args.backups, args.dry_run)
        print('dry run, nothing written:' if kept is None else 'written; the old record is kept as %s' % kept)
        for line in lines:
            print('  ' + line)
        for w in warnings:
            print('  warning: ' + w)
        return 0
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
