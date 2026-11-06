"""Build a probe mission from a Missions record (or a plan file) in one command.

    python3 -m riftlord.probe 900_Beset_Alpha_3_P "Beset Probe X" \\
        --set plan.world.riftWarning=[1600,1600,600] --switch navsurfadd.ENABLED=true --install

    python3 -m riftlord.mapeditor.probe --from-record 900_Pier_Pressure_P \\
        --waves SPEC.json --title "Wraiths Probe (Pier Pressure)" --key WraithsPier \\
        --out DIR [--no-install] [--json]

The record is copied out of the real Missions folder into a scratch one, so the user's
missions are only ever read. The probe gets its own title, mod, table folder and Guids, is built
through the normal bake path, packed, checked, and with --install copied into ~mods
(riftlord.core.pakguard: written as .part and renamed in; a pak the running game has mounted
is replaced once the game closes, the command waiting for it). An installed probe prints its
checklist and writes it beside the pak (CHECKLIST-<pak>.md): the pak and when it went in,
whether the game must be restarted, where to find the mission, what the build changes
(--set, --switch, --waves), what to look at (--check) and what each outcome means
(--outcome), and the other Rift Lord missions installed. With --from-record, `--key` names the mod (pak 900_<KEY>_P.pak),
`--waves` replaces the onslaught, `--out` takes the pak, `--mods-dir` reads staged mod paks
first (mod minions from there only). The whole run holds a light slot
(riftlord.core.heavyslot; a light bake inside takes a heavy slot for itself). A round of one
--key from one record keeps its mission Guids and reuses the cached level build when its
inputs are unchanged (riftlord.mapeditor.probecache; --no-cache). The last line is `PASS|FAIL pak PATH sha256 HEX`, or with --json
one JSON object. docs/MAP-EDITOR.md, "Probe builds from a record".
"""

import argparse
import collections
import contextlib
import hashlib
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

from riftlord.core import gamepath  # noqa: E402

#: When this module finished importing (the start-up fallback off Linux).
_IMPORTED = time.time()


def _process_started():
    """Wall-clock time this process started (from /proc), else when this module loaded."""
    try:
        with open('/proc/self/stat') as fh:
            ticks = int(fh.read().rsplit(')', 1)[1].split()[19])
        with open('/proc/uptime') as fh:
            up = float(fh.read().split()[0])
        return time.time() - up + ticks / os.sysconf('SC_CLK_TCK')
    except (OSError, ValueError, IndexError, AttributeError):
        return _IMPORTED


class Clock:
    """A probe run's phases in order ([(name, seconds)]), and the time spent inside the
    functions `wrap` times ({label: [calls, seconds]}, nested in the phases)."""

    def __init__(self):
        self.phases = []
        self.within = collections.OrderedDict()
        #: [{lane, job, heldBy, seconds}]: each wait for a slot, from heavyslot.WAIT_LISTENERS
        self.waits = []
        self._restore = []

    def add(self, name, seconds):
        self.phases.append((name, seconds))

    def wrap(self, module, attr, label):
        """Time every call of module.attr under `label` until restore()."""
        import functools
        real = getattr(module, attr)

        @functools.wraps(real)
        def timed(*a, **kw):
            t = time.time()
            try:
                return real(*a, **kw)
            finally:
                got = self.within.setdefault(label, [0, 0.0])
                got[0] += 1
                got[1] += time.time() - t
        setattr(module, attr, timed)
        self._restore.append((module, attr, real))

    def restore(self):
        for module, attr, real in reversed(self._restore):
            setattr(module, attr, real)
        self._restore = []

    def report(self, total):
        """{'phases', 'other', 'total', 'within', 'waits'} in seconds, rounded."""
        spent = sum(s for _n, s in self.phases)
        return {'phases': [[n, round(s, 2)] for n, s in self.phases],
                'other': round(max(0.0, total - spent), 2), 'total': round(total, 2),
                'within': {k: {'calls': c, 'seconds': round(s, 2)}
                           for k, (c, s) in self.within.items()},
                'waits': [dict(w) for w in self.waits]}


def _timed_functions(clock):
    """Wrap the functions a probe run spends its time in (see Clock.within)."""
    from riftlord.build import emit
    from riftlord.build import makemod
    from riftlord.missions import modminions
    from riftlord.onslaught import wavespec
    clock.wrap(modminions, 'scan', 'mod discovery (modminions.scan)')
    clock.wrap(modminions, 'carry', 'minion carry (modminions.carry)')
    clock.wrap(modminions, 'gates', 'minion gates (modminions.gates)')
    clock.wrap(makemod, 'installed_tables', 'registry scan of installed paks')
    clock.wrap(emit, 'write', 'level build (emit.write)')
    clock.wrap(wavespec, 'bundle', 'waves (wavespec.bundle)')
    #: this module as imported by name (modminions calls it so), and as running when it
    #: is __main__
    import importlib
    named = importlib.import_module('riftlord.mapeditor.probe')
    clock.wrap(named, 'import_problems', 'import resolution')
    if named.import_problems is not globals().get('import_problems'):
        clock.wrap(_Globals(globals()), 'import_problems', 'import resolution')


class _Globals:
    """A module's globals dict seen through getattr/setattr (Clock.wrap)."""

    def __init__(self, space):
        object.__setattr__(self, '_space', space)

    def __getattr__(self, name):
        return self._space[name]

    def __setattr__(self, name, value):
        self._space[name] = value

#: Where probe builds and their scratch Missions folders live ($RIFTLORD_PROBE_HOME moves it).
PROBE_HOME = os.environ.get('RIFTLORD_PROBE_HOME') or os.path.expanduser(
    '~/.cache/rift-lord/probes')
#: Shared tables a mission pak must not ship (a last-pak-wins copy locks other missions).
SHARED_TABLES = ('Data/EntitlementsProtos', 'Data/MissionProtos', 'Data/CampaignProtos')


def _real_root():
    """The user's Missions folder, read before the scratch one is switched in."""
    return os.environ.get('OMD3_BUCKET') or gamepath.load_settings().get('bucket') \
        or gamepath.user_data('Missions')


def _set(rec, dotted, value):
    """Set a field by dotted path; a numeric part indexes a list."""
    keys = [int(k) if k.isdigit() else k for k in dotted.split('.')]
    node = rec
    for key in keys[:-1]:
        node = node[key] if isinstance(node, list) else node.setdefault(key, {})
    node[keys[-1]] = value


def _parse_value(text):
    try:
        return json.loads(text)
    except ValueError:
        return text


def _module(name):
    """A riftlord module by its dotted path, or by its bare name (`navsurfadd`)."""
    import importlib
    import pkgutil
    import riftlord
    if '.' in name:
        return importlib.import_module(name)
    for info in pkgutil.walk_packages(riftlord.__path__, 'riftlord.'):
        if info.name.rsplit('.', 1)[-1] == name:
            return importlib.import_module(info.name)
    raise SystemExit('no riftlord module named %s' % name)


def _switch(spec):
    """`module.NAME=value`: set a module-level switch before the build."""
    target, value = spec.split('=', 1)
    module, name = target.rsplit('.', 1)
    setattr(_module(module), name, _parse_value(value))


def import_problems(files, level_entry, cache=None):
    """Imports of a built level (or any `.uasset` entry) that name no export, by exact
    FName (string and number), in their package: the package from the pak's own `files`
    (entry name -> bytes; a mod minion's carried packages among them) or the game's.
    `/Script/` packages are native and skipped. `cache` ({}) shares loaded packages
    between calls. Returns (checked, [problem])."""
    import struct
    from riftlord.core import gamefiles
    from riftlord.formats.ue.uassetio import Package
    stem = level_entry.rsplit('.', 1)[0]
    pkg = Package(files[level_entry], files.get(stem + '.uexp', b''))
    names = [n for n, _h in pkg.names]

    def fname(rec):
        return names[struct.unpack_from('<i', rec, 20)[0]], struct.unpack_from('<i', rec, 24)[0]

    def spelled(name, number):
        return name if not number else '%s_%d' % (name, number - 1)
    loaded = {} if cache is None else cache
    #: a class the player has installed, not carried: the detected supported mods' paks
    from riftlord.core import paksource
    from riftlord.core import supportedmods

    def package_of(path):
        if path not in loaded:
            entry = 'OMD/Content/' + path[len('/Game/'):]
            try:
                carried = entry + '.uasset' in files or entry + '.umap' in files
                held = None if carried else supportedmods.holder(entry + '.uasset')
                if carried:
                    head = files.get(entry + '.uasset') or files[entry + '.umap']
                    blobs = (head, files.get(entry + '.uexp', b''))
                elif held:
                    blobs = (paksource.read(held, entry + '.uasset'),
                             paksource.read(held, entry + '.uexp'))
                else:
                    blobs = gamefiles.package(path)
                p = Package(*blobs)
                loaded[path] = p
            except Exception:                        # noqa: BLE001
                loaded[path] = None
        return loaded[path]

    def export_index(p, outer, name, number):
        for i, e in enumerate(p.exports):
            if (struct.unpack_from('<i', e['meta'], 12)[0] == outer
                    and p.names[struct.unpack_from('<i', e['meta'], 16)[0]][0] == name
                    and struct.unpack_from('<i', e['meta'], 20)[0] == number):
                return i + 1
        return None
    checked, bad = 0, []
    for rec in pkg.imports:
        chain = [fname(rec)]
        outer = struct.unpack_from('<i', rec, 16)[0]
        while outer < 0:
            up = pkg.imports[-outer - 1]
            chain.append(fname(up))
            outer = struct.unpack_from('<i', up, 16)[0]
        if len(chain) < 2:
            continue                                 # a package itself
        path = spelled(*chain[-1])
        if path.startswith('/Script/') or not path.startswith('/Game/'):
            continue
        p = package_of(path)
        checked += 1
        if p is None:
            bad.append('%s: package not found' % path)
            continue
        at = 0
        for name, number in reversed(chain[:-1]):
            at = export_index(p, at, name, number)
            if at is None:
                bad.append('%s.%s: no such export' % (path, spelled(*chain[0])))
                break
    return checked, bad


def _sha1(path):
    with open(path, 'rb') as fh:
        return hashlib.sha1(fh.read()).hexdigest()


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for block in iter(lambda: fh.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def main(argv=None):
    started, entered = _process_started(), time.time()
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('source', nargs='?', help='a Missions record id, or a plan .json with --level')
    ap.add_argument('title', nargs='?', help="the probe's mission title")
    ap.add_argument('--from-record', dest='record', metavar='ID',
                    help='the Missions record to copy (read only), in place of SOURCE')
    ap.add_argument('--title', dest='title_opt', metavar='TITLE', help='in place of TITLE')
    ap.add_argument('--key', help="the probe's mod key: mod folder Mods/<KEY>, pak 900_<KEY>_P.pak")
    ap.add_argument('--out', metavar='DIR', help='write the pak into DIR (working files in '
                    'DIR/.probe-<key>) instead of the probe cache')
    ap.add_argument('--level', help='source level when SOURCE is a plan file')
    ap.add_argument('--set', action='append', default=[], metavar='PATH=JSON',
                    help='override a record field, e.g. plan.world.riftWarning=[800,800,600]')
    ap.add_argument('--switch', action='append', default=[], metavar='MODULE.NAME=JSON',
                    help='set a module switch for this build only')
    ap.add_argument('--spec', '--waves', dest='spec', metavar='SPEC.json',
                    help="a wave spec .json (the Onslaught Editor's format) to store as the "
                         "record's onslaught")
    ap.add_argument('--install', action='store_true', help='copy the probe into ~mods')
    ap.add_argument('--check', action='append', default=[], metavar='TEXT',
                    help='what the tester looks at (the checklist; repeatable)')
    ap.add_argument('--outcome', action='append', default=[], metavar='SEEN => MEANS',
                    help='an outcome and what it means (the checklist; repeatable)')
    ap.add_argument('--no-install', action='store_true',
                    help='never install (the default; refuses --install)')
    ap.add_argument('--json', action='store_true',
                    help='print the result as one JSON line, last')
    ap.add_argument('--no-cache', action='store_true',
                    help='build the level afresh (riftlord.mapeditor.probecache)')
    ap.add_argument('--mods-dir', metavar='DIR',
                    help='staged mod paks: mod minions are found, carried and checked from '
                         'DIR only; imports and Supported Mods read DIR first, then the '
                         'installed folders')
    args = ap.parse_args(argv)
    args.source = args.record or args.source
    args.title = args.title_opt or args.title
    if not args.source or not args.title:
        ap.error('name a source (SOURCE or --from-record) and a title (TITLE or --title)')
    if args.record and args.source != args.record:
        ap.error('give SOURCE or --from-record, not both')
    if args.install and args.no_install:
        ap.error('--install and --no-install: give one')
    if args.key and args.source.endswith('.json'):
        ap.error('--key needs a record source (--from-record)')
    if args.mods_dir and not os.path.isdir(args.mods_dir):
        ap.error('--mods-dir %s is not a folder' % args.mods_dir)
    from riftlord.core import heavyslot
    from riftlord.core import supportedmods
    from riftlord.missions import modminions
    saved = (modminions.ROOTS, supportedmods.STAGED_DIR)
    if args.mods_dir:
        args.mods_dir = os.path.abspath(args.mods_dir)
        modminions.ROOTS = [args.mods_dir]
        supportedmods.STAGED_DIR = args.mods_dir
    modminions._SCAN.clear()
    clock = Clock()
    clock.add('start-up and imports', entered - started)
    clock.add('arguments and set-up', time.time() - entered)

    def listener(event, lane, name, held, seconds):
        """Each wait in any lane, this run's own or a step's: printed once, recorded."""
        if event == 'wait':
            print('waiting for a %s slot%s (held by %s)'
                  % (lane, '' if name == own else ' for %s' % name, held), flush=True)
            clock.waits.append({'lane': lane, 'job': name, 'heldBy': held, 'seconds': None})
        else:
            for w in reversed(clock.waits):
                if w['lane'] == lane and w['job'] == name and w['seconds'] is None:
                    w['seconds'] = round(seconds, 2)
                    break
    own = 'probe %s' % args.title
    heavyslot.WAIT_LISTENERS.append(listener)
    try:
        _timed_functions(clock)
        t = time.time()
        #: the probe build is short and single-core: a light slot; a step that bakes light
        #: (multi-core, GPU) takes a heavy slot and the GPU lock for itself (lightbake)
        with heavyslot.slot(own, light=True, on_wait=lambda _held: None):
            clock.add('waiting for a light slot', time.time() - t)
            return _probe(args, clock, started)
    finally:
        heavyslot.WAIT_LISTENERS.remove(listener)
        clock.restore()
        modminions.ROOTS, supportedmods.STAGED_DIR = saved
        modminions._SCAN.clear()


def mod_sources(staged=None):
    """[{kind, name, pak, where}]: the pak each mod is read from for this run, minion mods
    (modminions) and Supported Mods; `where` is 'staged' for a pak under `staged`."""
    from riftlord.core import supportedmods
    from riftlord.missions import modminions

    def where(path):
        inside = staged and os.path.abspath(path).startswith(os.path.join(staged, ''))
        return 'staged' if inside else 'installed'
    out = [{'kind': 'minions', 'name': key, 'pak': m['pak'], 'where': where(m['pak'])}
           for key, m in sorted(modminions.mods().items())]
    for row in supportedmods.detect():
        for f in row['files']:
            if f['present']:
                out.append({'kind': 'supported', 'name': row['id'], 'pak': f['full'],
                            'where': where(f['full'])})
    return out


def registry_others(staged):
    """makemod.installed_tables() as the game would see it with the paks in `staged`
    installed: installed paks of the same file names left out, the staged ones in."""
    from riftlord.build import makemod
    names = [os.path.basename(p) for base, _d, ns in os.walk(staged)
             for p in ns if p.endswith('.pak')]
    out = makemod.installed_tables(exclude=names)
    out.update(makemod.installed_tables((), staged))
    return out


def _probe(args, clock, process_started):
    """main's build, packing, gates and report, inside a light slot."""
    results = []
    t = time.time()
    sources = mod_sources(args.mods_dir)
    print('mods read from%s:' % (' (--mods-dir %s first)' % args.mods_dir
                                 if args.mods_dir else ''))
    for s in sources:
        print('  %-9s %-16s %-9s %s' % (s['kind'], s['name'], s['where'], s['pak']))
    clock.add('mod discovery', time.time() - t)
    mark = [time.time()]

    def step(name, started):
        clock.add(name, time.time() - started)

    def gate(name, ok, detail=''):
        now = time.time()
        results.append((name, ok, detail, now - mark[0]))
        mark[0] = now

    real = _real_root()
    safe = ''.join(c if c.isalnum() else '_' for c in args.title)
    if args.out:
        out_dir = os.path.abspath(args.out)
        scratch = os.path.join(out_dir, '.probe-%s' % (args.key or safe))
    else:
        scratch = os.path.join(PROBE_HOME, safe)
        out_dir = os.path.join(scratch, '_out')
    if os.path.abspath(scratch) == os.path.abspath(real):
        raise SystemExit('the probe folder may not be the Missions folder %s' % real)
    t = time.time()
    #: a keyed probe's earlier round from the same record: its mission Guids are kept
    earlier = {}
    if args.key:
        try:
            with open(os.path.join(scratch, '.riftlord', 'records', '900_%s_P.json' % args.key),
                      encoding='utf-8') as fh:
                earlier = json.load(fh)
        except (OSError, ValueError):
            earlier = {}
    shutil.rmtree(scratch, ignore_errors=True)
    os.makedirs(os.path.join(scratch, '.riftlord', 'records'), exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)
    step('clear the probe folder', t)
    os.environ['OMD3_BUCKET'] = scratch

    t = time.time()
    from riftlord.missions import bucket
    bucket.activate()
    if args.source.endswith('.json'):
        if not args.level:
            raise SystemExit('a plan file needs --level')
        with open(args.source, encoding='utf-8') as fh:
            plan = json.load(fh)
        plan.setdefault('level', args.level)
        ident = bucket.save_map(args.level, plan, title=args.title)
        ident = ident['mission'] if isinstance(ident, dict) else ident
    else:
        src_dir = os.path.join(real, '.riftlord', 'records')
        if not os.path.exists(os.path.join(src_dir, args.source + '.json')):
            raise SystemExit('no record %s in %s' % (args.source, src_dir))
        for name in os.listdir(src_dir):
            if name.startswith(args.source + '.'):
                shutil.copyfile(os.path.join(src_dir, name),
                                os.path.join(scratch, '.riftlord', 'records', name))
        if args.record:
            #: a --from-record probe is listed under Custom Maps, in no campaign
            from riftlord.build import makemod
            src = bucket.load(args.source)
            src['placement'] = makemod.CUSTOM_MAPS
            if isinstance(src.get('spec'), dict):
                src['spec']['intoCampaign'] = makemod.CUSTOM_MAPS
            bucket._write_json(bucket._record_path(args.source), src)
        ident = bucket.copy(args.source, args.title, key=args.key)
    rec = bucket.load(ident)
    rec['probeSource'] = args.source
    #: the Guids the probe started with (a bake rewrites `guids` with the rows it wrote)
    kept = earlier.get('probeGuids') or {}
    if (earlier.get('probeSource') == args.source and earlier.get('mod') == rec.get('mod')
            and kept and set(kept) == set(rec.get('guids') or {})):
        rec['guids'] = dict(kept)
        print('mission Guids kept from the previous round of %s' % args.key)
    rec['probeGuids'] = dict(rec.get('guids') or {})
    if args.spec:
        with open(args.spec, encoding='utf-8') as fh:
            spec = json.load(fh)
        if not isinstance(spec, dict) or not isinstance(spec.get('waves'), list):
            raise SystemExit('%s is not a wave spec: it needs a "waves" list' % args.spec)
        #: unit rows by row name, else by Guid, saved with both (wavespec.resolve_units)
        from riftlord.onslaught import wavespec
        spec = wavespec.resolve_units(spec, note=print, stamp=True)
        #: the source's onslaught settings (par, details, ...) with the given waves and events
        base = {k: v for k, v in (rec.get('spec') or {}).items() if k not in ('waves', 'events')}
        spec = dict(base, **spec)
        spec.setdefault('events', [])
        spec.update(title=args.title, map=rec.get('map') or spec.get('map'))
        rec['spec'] = spec
        if isinstance((rec.get('plan') or {}).get('events'), list):
            rec['plan']['events'] = spec['events']
    for item in args.set:
        path, value = item.split('=', 1)
        _set(rec, path, _parse_value(value))
    bucket._write_json(bucket._record_path(ident), rec)
    for spec in args.switch:
        _switch(spec)
    step('record', t)

    t = mark[0] = time.time()
    from riftlord.mapeditor import probecache
    cache = probecache.LevelCache(os.path.join(os.path.dirname(scratch),
                                               '.probe-cache-%s' % (args.key or safe)))
    stats = {'hit': None, 'key': None, 'seconds': 0.0, 'saved': False, 'changed': None}
    with contextlib.ExitStack() as stack:
        if not args.no_cache:
            stats = stack.enter_context(cache.wrapping(
                os.path.join(bucket.root(), ident), staged=args.mods_dir,
                switches=args.switch, note=print))
        try:
            bucket.bake(ident)
            gate('bake', True)
        except (Exception, SystemExit) as exc:  # noqa: BLE001
            detail = getattr(exc, 'detail', '')
            gate('bake', False, '%s%s' % (exc, ' [%s]' % detail if detail else ''))
    step('bake', t)
    level_cache = {'used': not args.no_cache, 'hit': stats['hit'], 'saved': stats['saved'],
                   'key': stats['key'], 'keying seconds': round(stats['seconds'], 2),
                   'changed': stats['changed']}
    print('level cache: %s' % ('off (--no-cache)' if args.no_cache else
                               'hit' if stats['hit'] else
                               'miss, %s' % ('saved' if stats['saved'] else 'not saved')
                               if stats['hit'] is False else 'not used for this plan'))

    pak = None
    if results[-1][1]:
        t = time.time()
        got = bucket.export([ident], dest=out_dir)
        pak = os.path.join(out_dir, got['paks'][0])
        step('pack', t)

        t = mark[0] = time.time()
        from riftlord.formats.ue import pakv4
        from riftlord.core import paksource
        names = paksource.names(pak)
        mount, _version, raw = pakv4.read(pak)
        bad = [n for n, _o, _s in raw if not n.startswith('OMD/')]
        gate('mount ../../../', mount == '../../../', mount)
        gate('entry names start OMD/', not bad, ', '.join(bad[:3]))
        shipped = [n for n in names for s in SHARED_TABLES if n.endswith(s + '.uasset')]
        gate('no shared tables', not shipped, ', '.join(shipped))
        from riftlord.core import pkgcheck
        files = paksource.entries(pak)
        #: the registry's rules against ~mods: own mod folder, prefixed rows, own Guids
        from riftlord.build import makemod
        carriers = []
        bad = makemod.registry_problems(
            files, os.path.basename(pak),
            others=registry_others(args.mods_dir) if args.mods_dir else None, notes=carriers)
        gate('registry: separate mod folder, prefixed rows, unique Guids', not bad,
             '; '.join(bad[:3]))
        for line in carriers:
            print('  note: %s' % line)
        #: tables are registered from their folder and OrcsMustUI's files ride inside
        #: the pak (makemod.OMUI_DIR): nothing may sit beside it
        beside = sorted(n for n in os.listdir(out_dir)
                        if n.startswith(paksource.stem(pak) + '.') and not n.endswith('.pak'))
        gate('nothing beside the pak', not beside, ', '.join(beside))
        gate('tables in its mod folder', bool(makemod.registry_tables(files)),
             ', '.join(makemod.registry_tables(files)))
        omui = sorted(makemod.omui_files(files))
        gate('OrcsMustUI files inside (%s)' % (', '.join(omui) or 'none'),
             not makemod.omui_problems(files), '; '.join(makemod.omui_problems(files)[:3]))
        found = []
        for name, data in files.items():
            stem, ext = os.path.splitext(name)
            if ext not in ('.uasset', '.umap') or stem + '.uexp' not in files:
                continue
            try:
                found += pkgcheck.problems(data, files[stem + '.uexp'], os.path.basename(stem))
            except (Exception, SystemExit) as exc:  # noqa: BLE001
                found.append('%s: %s' % (os.path.basename(stem), exc))
        gate('pkgcheck', not found, '; '.join(map(str, found[:3])))
        from riftlord.build import makelevel
        from riftlord.formats.ue.uassetio import Package
        stale = []
        #: the mission's own level: `<mod>_Map01`, or its Working Title (leveltitle)
        from riftlord.build import leveltitle
        own_level = leveltitle.level_entry(files)
        for name, data in files.items():
            if name != own_level:
                continue
            pkg = Package(data, files[name[:-5] + '.uexp'])
            pkg.load_payloads()
            own = '/Game/' + name[len('OMD/Content/'):-len('.umap')]
            stale.append(makelevel.self_ref_problems(pkg, own))
        gate('level self-references name its World', not any(stale),
             '%d stale' % sum(stale))
        from riftlord.build import levelbuild
        for name, data in files.items():
            if name == own_level:
                lb = levelbuild.Builder.around(data, files[name[:-5] + '.uexp'])
                gaps = lb.level_preload_gaps()
                gate('the Level preloads every listed actor (%d)'
                     % len([a for a in lb.actor_list() if a > 0]), not gaps,
                     '%d not preloaded' % len(gaps))
                checked, missing = import_problems(files, name)
                gate('every import names an export, string and number (%d)' % checked,
                     not missing, '; '.join(missing[:3]))
        from riftlord.build.navigation import navobstruct
        for name, data in files.items():
            if name == own_level:
                count, bad = navobstruct.check_level(data, files[name[:-5] + '.uexp'])
                gate('obstructions read back (%d)' % count, not bad, '; '.join(bad[:3]))
                from riftlord.build.navigation import navsurf
                from riftlord.build.navigation import navsurfcarve
                from riftlord.build.navigation import navsurfextend
                buf = navsurfextend._surface_of(data, files[name[:-5] + '.uexp'])
                counts = '/'.join(str(len(navsurf.sections(buf, h)))
                                  for h in navsurf.graphs(buf))
                bad = navsurfcarve.check(buf)
                gate('walkable graph checks (%s records)' % counts, not bad,
                     '; '.join(bad[:3]))
                #: NavPower's vertex-fan walk closes everywhere, one edge per neighbour pair
                #: (riftlord.build.navigation.navfan)
                from riftlord.build.navigation import navfan
                bad = navfan.problems(buf)
                gate('walkable graph: every vertex fan closes, one edge per neighbour pair',
                     not bad, '; '.join(bad[:4]))
                from riftlord.build.navigation import navregen
                if navregen.ENABLED:
                    plan = rec.get('plan') or {}
                    level = rec.get('level') or plan.get('level')
                    bad = navregen.check(buf)
                    gate('regenerated graph parses, pointer slots and edge ids', not bad,
                         '; '.join(bad[:3]))
                    from riftlord.build.navigation import navstrict
                    #: a defect the shipped graph has too is not ours (Front Lawn ships
                    #: three T-junctions), so only an excess over shipped fails
                    worst = collections.Counter()
                    for h in navsurf.graphs(buf):
                        found, _tags = navstrict.check_graph(navstrict.records(buf, h))
                        for key in navstrict.FATAL:
                            worst[key] += len(found.get(key, ()))
                    ship = navsurf.buffer(level)
                    for h in navsurf.graphs(ship):
                        found, _tags = navstrict.check_graph(navstrict.records(ship, h))
                        for key in navstrict.FATAL:
                            worst[key] -= len(found.get(key, ()))
                    gate('regenerated graph has no fatal polygon defects beyond shipped',
                         not +worst, ', '.join('%s %d' % kv for kv in (+worst).items()))
                    #: a level spawner or door the plan leaves where it is and the shipped
                    #: graph has no floor under either is not ours (Scenario_Ice_02's
                    #: MobSpawnerDoor3 and BP_WSForceFieldDoor_2)
                    moved = {w['name'] for w in plan.get('wiring') or [] if w.get('at')}
                    ship_bare = set(navregen.unsupported(ship, level, {}))
                    bare = [b for b in navregen.unsupported(buf, level, plan)
                            if b not in ship_bare or b.split(' ', 1)[1] in moved]
                    gate('regenerated graph has floor under every spawner and door the '
                         'shipped graph has floor under', not bare, ', '.join(bare[:6]))
                    cut = navregen.disconnected(buf, level, plan)
                    edited = bool(plan.get('removed') or plan.get('edits')
                                  or plan.get('added'))
                    if edited and cut:
                        gate('regenerated graph: legs the plan\'s geometry cuts', True,
                             ', '.join(cut[:8]))
                    else:
                        gate('regenerated graph joins every route leg', not cut,
                             ', '.join(cut[:6]))
        #: mission events: each one's outcome, and every fired event heard (riftlord.hostevents)
        from riftlord.missions import hostevents
        coord = next((n for n in files if n.endswith('_Onslaught01.uasset')), None)
        if coord:
            cp = '/Game' + coord[len('OMD/Content'):-len('.uasset')]
            wanted = None
            plan_events = (rec.get('plan') or {}).get('events')
            if isinstance(plan_events, list):
                from riftlord.onslaught import wavespec
                from riftlord.mapeditor import planactors
                level = rec.get('level') or (rec.get('plan') or {}).get('level')
                spec = rec.get('spec') if isinstance(rec.get('spec'), dict) \
                    else planactors.level_spec(level)
                _s, report = wavespec.apply_events(dict(spec, events=plan_events),
                                                   hostevents.facts_from_files(files, cp))
                wanted = wavespec.events_wanted(report)
                for r in report:
                    ev = r['event']
                    gate('event wave %s %s: %s' % (ev.get('wave'), ev.get('kind'), r['status']),
                         True, r['detail'])
            for row in hostevents.check_files(files, cp, wanted):
                gate(row['name'], row['ok'], row['detail'])
            music = (hostevents.facts_from_files(files, cp) or {}).get('music') or {}
            if music.get('available') and not music.get('piece'):
                gate('music loops: opens %s, changes to %s' % (music['opening'], music['change']),
                     True)
        #: the Mission Events piece: built, placed, and owning its events (riftlord.missionevents)
        from riftlord.missions import missionevents
        if missionevents.piece(rec.get('plan')):
            level = rec.get('level') or (rec.get('plan') or {}).get('level')
            for row in missionevents.check_files(files, level, rec.get('plan')):
                gate(row['name'], row['ok'], row['detail'])
        #: the mission's own scripts: built, importing what they name, placed (missionscript)
        from riftlord.missions import missionscript
        if missionscript.pieces(rec.get('plan')):
            for row in missionscript.check_files(files, dict(rec.get('plan') or {},
                                                             _modKey=rec.get('mod'))):
                gate(row['name'], row['ok'], row['detail'])
        #: unstable rifts: open while the waves send through them, and sent along the real flow
        if missionevents.unstable_rifts(rec.get('plan')) and coord:
            from riftlord.mapeditor import planactors
            level = rec.get('level') or (rec.get('plan') or {}).get('level')
            spec = rec.get('spec') if isinstance(rec.get('spec'), dict) \
                else planactors.level_spec(level)
            bad = missionevents.rift_problems(rec.get('plan'), spec)
            gate('unstable rifts open on every wave that spawns through them', not bad,
                 '; '.join(bad))
            for row in missionevents.rift_flow_checks(pak, cp.rsplit('/', 1)[-1],
                                                      rec.get('plan'), spec):
                gate(row['name'], row['ok'], row['detail'])
        #: unstable rifts stand over walkable floor, nothing solid between
        #: (riftlord.build.navigation.riftfloor)
        if missionevents.unstable_rifts(rec.get('plan')):
            from riftlord.build.navigation import riftfloor
            level = rec.get('level') or (rec.get('plan') or {}).get('level')
            for name, ok, detail in riftfloor.gates(files, rec.get('plan'), level):
                gate(name, ok, detail)
        #: intro card removed and its handler run in place (riftlord.levelintro)
        from riftlord.build.world import levelintro
        if levelintro.wanted_removed(rec.get('plan')):
            level = rec.get('level') or (rec.get('plan') or {}).get('level')
            for row in levelintro.check_files(files, level):
                gate(row['name'], row['ok'], row['detail'])
        #: Fab library pieces: the pak carries exactly the placed closure (riftlord.fabemit)
        from riftlord.mapeditor import fabemit
        fab_plan = rec.get('plan') or {}
        if fabemit.uses_fab(fab_plan) or any(n.startswith('OMD/Content/Mods/Fab/') for n in files):
            for name, passed, detail in fabemit.gates(files, fab_plan):
                gate(name, passed, detail)
        #: the minimap the build drew: every floor's texture, read back (riftlord.minimapredraw)
        from riftlord.build.minimap import minimapredraw
        level = rec.get('level') or (rec.get('plan') or {}).get('level')
        for name, passed, detail in minimapredraw.gates(files, rec.get('plan') or {}, level):
            gate(name, passed, detail)
        #: wind, kill volume switches, wall plates and placed actors (riftlord.controllers)
        from riftlord.mapeditor import controllers
        for name, passed, detail in controllers.gates(files, rec.get('plan') or {}):
            gate(name, passed, detail)
        #: treasure chests and pickups, and the kits they ship (riftlord.kitactors)
        from riftlord.mapeditor import kitactors
        for name, passed, detail in kitactors.gates(files, rec.get('plan') or {}):
            gate(name, passed, detail)
        #: the Chandelier's kit and chains (riftlord.mapeditor.chandelier)
        from riftlord.mapeditor import chandelier
        for name, passed, detail in chandelier.gates(files, rec.get('plan') or {}):
            gate(name, passed, detail)
        #: the Crystal: placed, its properties and rifts, the game type (riftlord.mapeditor.crystal)
        from riftlord.mapeditor import crystal
        for name, passed, detail in crystal.gates(files, rec.get('plan') or {}):
            gate(name, passed, detail)
        #: the level pieces the Crystal parks, listed (riftlord.mapeditor.crystalpark)
        from riftlord.mapeditor import crystalpark
        for name, passed, detail in crystalpark.gates(
                rec.get('level') or (rec.get('plan') or {}).get('level'), rec.get('plan') or {}):
            gate(name, passed, detail)
        #: each mod pak the mission depends on spells every package one way (riftlord.core.pakspelling)
        from riftlord.core import pakspelling
        for name, passed, detail in pakspelling.gates(rec.get('plan') or {}):
            gate(name, passed, detail)
        #: written and lifted components end as their class serializes (riftlord.build.trailers)
        from riftlord.build import trailers
        for name, passed, detail in trailers.gates(files, rec.get('plan') or {}):
            gate(name, passed, detail)
        #: mod minions the waves spawn, carried byte for byte from the installed mod (modminions)
        from riftlord.missions import modminions
        for name, passed, detail in modminions.gates(files):
            gate(name, passed, detail)
        #: the built pak, and each mod pak it carries minions from, under one spelling each
        for path in [pak] + sorted({m['pak'] for k, m in modminions.mods().items()
                                    if k in modminions.carried_folders(files) and m['own']}):
            bad = pakspelling.problems(path)
            gate('%s: every package path under one FName spelling' % os.path.basename(path),
                 not bad, '; '.join(bad[:3]))
        #: every texture mip stored at the size its format needs (riftlord.build.texsizes)
        from riftlord.build import texsizes
        count, bad = texsizes.problems(files)
        gate('texture mip sizes (%d textures)' % count, not bad, '; '.join(bad[:3]))
        #: added instanced foliage, instance for instance (riftlord.foliageadd)
        from riftlord.mapeditor import foliageadd
        if foliageadd.items(rec.get('plan')):
            for name, passed, detail in foliageadd.gates(files, rec.get('plan')):
                gate(name, passed, detail)
        step('checks', t)

    ok = all(r[1] for r in results)
    checklist = None
    if ok and args.install:
        t = time.time()
        mods = gamepath.mods_dir()
        installed = os.path.join(mods, os.path.basename(pak))
        #: atomic, and a pak the running game has mounted is replaced once it closes
        from riftlord.core import pakguard
        waited = pakguard.held(installed)
        pakguard.copy(pak, installed)
        from riftlord.build import makemod
        for gone in makemod.remove_loose_sidecars(installed):
            print('  removed old sidecar %s' % gone)
        gate('installed copy matches', _sha1(installed) == _sha1(pak))
        step('install', t)
        checklist = write_checklist(args, pak, installed, waited)

    ok = all(r[1] for r in results)
    t = time.time()
    sha = _sha256(pak) if pak and os.path.exists(pak) else None
    step('sha256', t)
    timing = clock.report(time.time() - process_started)
    print('\nprobe %r from %s' % (args.title, args.source))
    for name, passed, detail, seconds in results:
        print('  %s  %s%s  [%.1fs]' % ('PASS' if passed else 'FAIL', name,
                                       (': ' + detail) if detail else '', seconds))
    print('  timing (%.1fs in all): %s, other %.1fs' % (
        timing['total'], ', '.join('%s %.1fs' % (n, s) for n, s in timing['phases']),
        timing['other']))
    print('  within those: ' + (', '.join('%s %.1fs (%d call%s)' % (
        k, v['seconds'], v['calls'], '' if v['calls'] == 1 else 's')
        for k, v in timing['within'].items() if v['calls']) or 'none'))
    for w in timing['waits']:
        print('  waited %s for a %s slot%s, held by %s' % (
            'unfinished' if w['seconds'] is None else '%.1fs' % w['seconds'], w['lane'],
            '' if w['job'] == 'probe %s' % args.title else ' (%s)' % w['job'], w['heldBy']))
    if args.install and ok:
        print('  installed into %s' % gamepath.mods_dir())
    if checklist:
        print('\n' + '\n'.join(checklist['lines']))
    if args.json:
        timings = collections.OrderedDict((n, s) for n, s in timing['phases'])
        timings.update(other=timing['other'], total=timing['total'], waits=timing['waits'])
        print(json.dumps({'ok': ok, 'pak': pak, 'sha256': sha, 'mission': ident,
                          'mod': rec.get('mod'), 'guids': rec.get('guids'),
                          'modSources': sources,
                          'gates': [{'name': n, 'ok': p, 'detail': d, 'seconds': round(s, 2)}
                                    for n, p, d, s in results],
                          'timings': timings,
                          'within': {k: v for k, v in timing['within'].items() if v['calls']},
                          'levelCache': level_cache, 'checklist': checklist}))
    else:
        print('%s pak %s sha256 %s' % ('PASS' if ok else 'FAIL', pak, sha))
    return 0 if ok else 1


def write_checklist(args, pak, installed, waited):
    """The installed probe's checklist: {path, lines}, written beside the built pak."""
    running = gamepath.game_running()
    changes = ['--set %s' % x for x in args.set] + ['--switch %s' % x for x in args.switch] + \
        (['--waves %s' % args.spec] if args.spec else [])
    mine = os.path.basename(installed)
    others = sorted(n for n in os.listdir(os.path.dirname(installed))
                    if n.startswith('900_') and n.endswith('_P.pak') and n != mine)
    lines = ['CHECKLIST  %s' % args.title,
             '  pak       %s (%s)' % (installed, 'put in after the game closed' if waited else
                                     'installed now'),
             '  game      %s' % ('running: RESTART it before testing' if running else
                                 'not running: start it'),
             '  find it   Custom Maps > %s' % args.title,
             '  changes   %s' % ('; '.join(changes) or 'none: the record as it is, built from '
                                 '%s' % args.source)]
    lines += ['  check     %s' % c for c in args.check] or ['  check     (none given: --check)']
    lines += ['  outcome   %s' % o for o in args.outcome] or \
        ['  outcome   (none given: --outcome "SEEN => MEANS")']
    lines.append('  also in ~mods (Rift Lord missions): %s' % (', '.join(others) or 'none'))
    path = os.path.join(os.path.dirname(pak), 'CHECKLIST-%s.md' % os.path.splitext(mine)[0])
    with open(path + '.part', 'w') as fh:
        fh.write('\n'.join(lines) + '\n')
    os.replace(path + '.part', path)
    return {'path': path, 'lines': lines}


if __name__ == '__main__':
    raise SystemExit(main())
