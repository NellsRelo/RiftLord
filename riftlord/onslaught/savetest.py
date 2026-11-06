"""Onslaught save regression suite: author waves into scratch copies and read them back.

    python3 -m onslaughtgui.savetest            (or: python3 -m onslaughtgui.selftest --saves)
    python3 -m onslaughtgui.savetest --missions 900_Beset_Alpha_3_P --maps Scenario_09

Cases, on each Rift Lord mission and each shipped map:
  resave   the onslaught as it is
  add      one wave appended (a copy of the last)
  addmove  a wave's ground spawns moved onto ground spawners it never used (the last
           wave that has some), then a wave appended
  remove   the last wave dropped
  events   the map's mission events read out (wavespec.split_events) and each moved two
           waves earlier, a music event switched to another track; a map with none gets a
           boss on wave 1. The readback must match the events written into the waves
  par      the par time set to PAR_CASE seconds; every row of the built mission table
           must read it back

A mission is copied from the Missions folder (read only) into a scratch bucket and
bundled twice: as Rift Lord does (authored from its source level's coordinator) and as
the standalone editor does (authored over the mission's own built coordinator). A
shipped map is built as loose assets. Every case must pass check_built, terminate in
flowsim, read back as authored through read_onslaught, and hold exactly one break per
wave but the last and its coin calls on one spawner. The scratch folder is removed at
the end. Exit status 0 when every case passes or is skipped.
"""

import argparse
import contextlib
import copy
import json
import os
import shutil
import struct
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

#: Rift Lord missions tested by default, by record id.
MISSIONS = ('900_Beset_Alpha_3_P', '900_Cellar_Alpha_1_P')
#: Shipped maps tested by default.
#: Scenario_12 and Scenario_18 have waves that leave a ground spawner unused (addmove).
#: Scenario_08 fires a music change its level never hears, and replays door 3's spawns.
#: Scenario_Sand_01 and _03 wait 12 and 7 times for a pause set per difficulty
#: (wavespec.delay_sites); Sand_01's waves set it in Switch cases an appended copy must take.
MAPS = ('Scenario_02', 'Scenario_08', 'Scenario_09', 'Scenario_12', 'Scenario_18',
        'Scenario_Sand_01', 'Scenario_Sand_03')
CASES = ('resave', 'add', 'addmove', 'remove', 'events', 'par')
#: The par time the par case sets, in seconds (no shipped row uses it).
PAR_CASE = 345.0
#: Waves the events case moves each event earlier by.
EVENT_SHIFT = 2
#: The boss the events case adds on a map with no events.
EVENT_BOSS = 'OgreBasher'
#: The track the events case switches music to.
EVENT_TRACK = 'Battle_09'
#: Where the scratch folder goes: the 2 TB disk when present (/tmp is RAM).
SCRATCH_PARENT = '/mnt/2tb_pro_a/tmp' if os.path.isdir('/mnt/2tb_pro_a/tmp') else None
#: A spawner parked this far off the map is a source level's original, not a door.
PARKED_X = -100000.0


class Skip(Exception):
    pass


def _real_missions():
    from riftlord.core import gamepath
    return (os.environ.get('OMD3_BUCKET') or gamepath.load_settings().get('bucket')
            or gamepath.user_data('Missions'))


def _load_from(ua, ux):
    from riftlord.formats.ue.uassetio import Package
    pkg = Package(ua, ux)
    names = [n for n, _ in pkg.names]

    def resolve(i):
        if i < 0:
            return names[struct.unpack_from('<I', pkg.imports[-i - 1], 20)[0]]
        if i > 0:
            return names[struct.unpack_from('<I', pkg.exports[i - 1]['meta'], 16)[0]]
        return 'null'
    return pkg, names, resolve


@contextlib.contextmanager
def _coordinator_as(name, ua, ux):
    """bppatch.load answers `name` with these bytes."""
    from riftlord.onslaught import bppatch
    real = bppatch.load
    bppatch.load = lambda n: _load_from(ua, ux) if n == name else real(n)
    try:
        yield
    finally:
        bppatch.load = real


def pacing(rows):
    """A door's rows as they play: units and waits in order. A pause before a row and one
    after the row ahead of it are the same wait (and read back as the latter); waits in a
    row add up; nothing after the last unit counts. A per-difficulty wait is its values,
    slot by slot, and adds up with nothing."""
    out = []

    def pause(p):
        if not p or not p.get('value'):
            return
        if p.get('byDifficulty'):
            out.append(('wait', tuple(sorted((s, round(float(v or 0), 1))
                                             for s, v in p['byDifficulty'].items()))))
            return
        kind, value = p.get('kind') or 'wait', round(float(p['value']), 1)
        if kind == 'wait' and out and out[-1][0] == 'wait' and not isinstance(out[-1][1], tuple):
            out[-1] = ('wait', round(out[-1][1] + value, 1))
        else:
            out.append((kind, value))
    for r in rows:
        if not r.get('unit'):
            continue
        pause(r.get('before'))
        out.append(('unit', r['unit']))
        pause(r.get('after'))
    while out and out[-1][0] != 'unit':
        out.pop()
    # A row reads back one pause, the gate when there is one: a gate and a wait between
    # the same two units (a moved row's own wait beside the gate after the row ahead)
    # play as written but read back as the gate.
    return [p for k, p in enumerate(out)
            if not (p[0] == 'wait' and 'gate' in (out[k - 1][0] if k else '',
                                                  out[k + 1][0]))]


def _summary(spec):
    out = []
    for w in spec['waves']:
        spawns = {k: sorted((e['unit'], e.get('count')) for e in v if e.get('unit'))
                  for k, v in (w.get('spawns') or {}).items()
                  if any(e.get('unit') for e in v)}
        paced = {k: pacing(v) for k, v in (w.get('spawns') or {}).items()
                 if any(e.get('unit') for e in v)}
        out.append((spawns, w.get('breakKind') or 'wave',
                    float(w.get('breakSeconds') or 10), w.get('coin'),
                    sorted(w.get('actions') or []), paced))
    return out


def expected(spec, map_name, bundled=False):
    """The waves a spec authors: its events written into them, as the build does.

    A spec without `events` has them read out of its waves first (wavespec.with_events),
    so an action the host never hears is not authored: Scenario_08 fires
    BattleMusicChange in wave 5, but its level binds no handler to it.
    """
    from riftlord.onslaught import wavespec
    from riftlord.missions import hostevents
    caps = hostevents.capabilities(map_name)
    if bundled:
        caps = wavespec.bundle_caps(map_name, caps)
    if 'events' not in spec:
        spec = wavespec.split_events(spec, caps)[0]
    # a per-difficulty pause moved off its home is written as a flat wait
    return wavespec.apply_events(wavespec.settle_pauses(spec), caps)[0]


def compare(spec, got):
    """What read back differently from the spec, as sentences."""
    a, b = _summary(spec), _summary(got)
    issues = []
    if len(a) != len(b):
        issues.append('%d waves read back, %d authored' % (len(b), len(a)))
    for i, (x, y) in enumerate(zip(a, b)):
        last = i == len(a) - 1
        if x[0] != y[0]:
            issues.append('wave %d spawns differ' % (i + 1))
        if not last and x[1] != y[1]:
            issues.append('wave %d break %s, authored %s' % (i + 1, y[1], x[1]))
        if not last and x[1] == 'wave' and x[2] != y[2]:
            issues.append('wave %d break %ss, authored %ss' % (i + 1, y[2], x[2]))
        if not last and x[3] not in (None, '') and x[3] != y[3]:
            issues.append('wave %d coin %s, authored %s' % (i + 1, y[3], x[3]))
        if x[4] != y[4]:
            issues.append('wave %d events %s, authored %s' % (i + 1, y[4], x[4]))
        # order and waits, where the units themselves agree
        if x[0] == y[0]:
            for door in sorted(x[5]):
                want, have = x[5][door], y[5].get(door, [])
                if want != have:
                    at = next((k for k, (p, q) in enumerate(zip(want, have)) if p != q),
                              min(len(want), len(have)))
                    issues.append('wave %d door %s pacing differs at %d: %s, authored %s'
                                  % (i + 1, door, at, have[at:at + 2], want[at:at + 2]))
    return issues


def structure(ua, ux, waves, spawners):
    """Exactly one break per wave but the last, none on the last, coin on one spawner."""
    from riftlord.onslaught import bppatch
    from riftlord.onslaught import wavespec
    pkg, names, resolve = _load_from(ua, ux)
    _w, mm = wavespec.ubergraph(pkg, names, resolve)
    entry = bppatch.entry_points(pkg, names, resolve)['Onslaught_Start']
    issues = []
    for w in range(1, waves + 1):
        breaks, coin_on = set(), set()
        for s in spawners:
            for j in wavespec.declared(mm, entry, names, resolve, w, s):
                node = mm[j][2]
                if any(bppatch.calls_named(node, f, resolve) for f in wavespec.BREAK_FNS):
                    breaks.add(j)
                if bppatch.calls_named(node, 'OnslaughtGiveCoin', resolve):
                    coin_on.add(s)
        want = 0 if w == waves else 1
        if len(breaks) != want:
            issues.append('wave %d has %d breaks, wants %d' % (w, len(breaks), want))
        if len(coin_on) > 1:
            issues.append('wave %d gives coin on spawners %s' % (w, sorted(coin_on)))
    return issues


def _renumbered(wave, bump=10000):
    wave = copy.deepcopy(wave)
    wave['id'] = (wave.get('id') or 0) + bump
    for rows in (wave.get('spawns') or {}).values():
        for e in rows:
            e['id'] = (e.get('id') or 0) + bump
    return wave


def make_case(case, base, map_name, source_shape):
    """The spec for one case, or Skip."""
    from riftlord.onslaught import onslaught
    from riftlord.onslaught import wavespec
    spec = copy.deepcopy(base)
    waves = spec['waves']
    if case == 'resave':
        return spec
    if case == 'par':
        spec['par'] = PAR_CASE
        return spec
    if case == 'remove':
        if len(waves) < 2:
            raise Skip('one wave only')
        spec['waves'] = waves[:-1]
        return spec
    if case == 'add':
        waves.append(_renumbered(waves[-1]))
        return spec
    if case == 'events':
        from riftlord.missions import hostevents
        spec, events = wavespec.split_events(spec, hostevents.capabilities(map_name))
        for ev in events:
            ev['wave'] = max(1, ev['wave'] - EVENT_SHIFT)
            if ev['kind'] == 'music':
                ev['track'] = EVENT_TRACK
        if not events:
            walk = {sp['index'] for sp in onslaught.map_report(map_name)['spawners']
                    if sp.get('start')}
            door = next((int(k) for k in sorted(waves[0].get('spawns') or {})
                         if int(k) in walk), None)
            if door is None:
                raise Skip('wave 1 sends nothing through a walking door')
            events.append({'kind': 'boss', 'wave': 1, 'unit': EVENT_BOSS, 'door': door,
                           'count': 1, 'reaction': True})
        return spec
    # addmove: a wave's ground rows onto ground spawners it never used, the last wave
    # that has such spawners
    flags = onslaught.proto_flags()
    protos = onslaught.protos()
    report = onslaught.map_report(map_name)['spawners']
    ground = [sp['index'] for sp in report
              if sp.get('start') and sp['location'][0] > PARKED_X]
    for number in range(len(waves), 0, -1):
        wave = waves[number - 1]
        used = (set((source_shape['capacity'].get(number) or {}).keys())
                | {int(k) for k, v in (wave.get('spawns') or {}).items()
                   if any(e.get('unit') for e in v)})
        free = sorted(s for s in ground if s not in used)
        moved = [e for v in (wave.get('spawns') or {}).values() for e in v
                 if e.get('unit') and not flags.get(e['unit'], {}).get('flyer')]
        if free and moved:
            break
    else:
        raise Skip('no wave has a ground spawner it never used (rebuild the mission '
                   'after adding doors)')
    kept = {}
    for k, rows in (wave.get('spawns') or {}).items():
        rest = [e for e in rows if not any(e is m for m in moved)]
        if rest:
            kept[k] = rest
    wave['spawns'] = kept
    for k, e in enumerate(moved):
        wave['spawns'].setdefault(str(free[k % len(free)]), []).append(e)
    spec['moved'] = {'wave': number, 'onto': free}
    waves.append(_renumbered(base['waves'][-1]))
    wavespec.validate(dict(spec, map=map_name), protos, flags)
    return spec


def _par_in(folder):
    """The par times (sorted, distinct) of the mission table in a loose build folder."""
    from riftlord.build import makemod
    from riftlord.onslaught import wavespec
    files = {}
    for d, _s, fs in os.walk(folder):
        for f in fs:
            rel = os.path.relpath(os.path.join(d, f), folder).replace(os.sep, '/')
            at = rel.find('OMD/Content/')
            if at >= 0 and rel.endswith(('.uasset', '.uexp')) and 'Tables' in rel:
                with open(os.path.join(d, f), 'rb') as fh:
                    files[rel[at:]] = fh.read()
    if makemod.mission_table_key(files)[0] is None:
        raise SystemExit('no mission table in the build')
    _k, t = wavespec._build_table(files)
    return sorted({wavespec._row_par(t, r) for r in t.rows})


def _par_issues(spec, folder):
    if spec.get('par') in (None, ''):
        return []
    got = _par_in(folder)
    return [] if got == [spec['par']] else ['par time reads %s, not %g' % (got, spec['par'])]


def _done(spec, res, moved):
    return '%d waves, %d checks%s' % (
        len(spec['waves']), len(res['checks']),
        ', wave %d moved onto %s' % (moved['wave'], moved['onto']) if moved else '')


def _record(results, label, fn):
    t = time.time()
    try:
        detail = fn() or ''
        results.append(('PASS', label, detail))
    except Skip as why:
        results.append(('SKIP', label, str(why)))
    except (Exception, SystemExit) as exc:                  # noqa: BLE001
        results.append(('FAIL', label, '%s: %s' % (type(exc).__name__, exc)))
    status, _l, detail = results[-1]
    print('%-4s %-46s %5.1fs  %s' % (status, label, time.time() - t, detail), flush=True)


def run_mission(ident, scratch, cases, results):
    from riftlord.missions import bucket
    from riftlord.build import makelevel
    from riftlord.build.navigation import navpatch
    from riftlord.onslaught import onslaught
    from riftlord.onslaught import wavespec
    real = _real_missions()
    rec_path = os.path.join(real, '.riftlord', 'records', ident + '.json')
    if not os.path.exists(rec_path) or not os.path.isdir(os.path.join(real, ident)):
        results.append(('SKIP', ident, 'no record or build in %s' % real))
        print('SKIP %s: no record or build in %s' % (ident, real))
        return
    with open(rec_path, encoding='utf-8') as fh:
        rec = json.load(fh)
    map_name = rec['map']
    root = os.path.join(scratch, 'bucket_' + ident)
    os.makedirs(os.path.join(root, '.riftlord', 'records'))
    shutil.copyfile(rec_path, os.path.join(root, '.riftlord', 'records', ident + '.json'))
    src = os.path.join(root, ident)
    shutil.copytree(os.path.join(real, ident), src)
    os.environ['OMD3_BUCKET'] = root
    bucket.activate()
    try:
        coord_path, coord_name = makelevel.coordinator_of(map_name)
        for mode in ('riftlord', 'standalone'):
            # standalone: no Rift Lord source, so the built coordinator is authored over
            bucket.ACTIVE = mode == 'riftlord'
            source = wavespec.source_coordinator(map_name) or coord_name
            sh = wavespec.shape(source)
            spawners = sorted({sp['index'] for sp in onslaught.map_report(map_name)['spawners']})
            for case in cases:
                def one(case=case, sh=sh):
                    spec = make_case(case, rec['spec'], map_name, sh)
                    moved = spec.pop('moved', None)
                    out = os.path.join(scratch, 'out')
                    shutil.rmtree(out, ignore_errors=True)
                    shutil.copytree(src, out)
                    try:
                        res = wavespec.bundle(dict(spec, map=map_name, bundle=True, out=out))
                        bad = [c for c in res['checks'] if not c['ok']]
                        if bad:
                            raise SystemExit('; '.join('%s: %s' % (c['name'], c['detail'])
                                                       for c in bad))
                        base_ = out + '/OMD/Content' + coord_path[len('/Game'):]
                        with open(base_ + '.uasset', 'rb') as a, open(base_ + '.uexp', 'rb') as b:
                            ua, ux = a.read(), b.read()
                        par = _par_issues(spec, out)
                    finally:
                        shutil.rmtree(out, ignore_errors=True)
                    with _coordinator_as(coord_name, ua, ux):
                        got = wavespec.read_onslaught(map_name)
                    issues = compare(expected(spec, map_name, True), got) + structure(
                        ua, ux, len(spec['waves']), spawners) + par
                    if issues:
                        raise SystemExit('; '.join(issues))
                    return _done(spec, res, moved)
                _record(results, '%s %s %s' % (ident, mode, case), one)
    finally:
        bucket.ACTIVE = True
        for path in navpatch.opened_paks():
            if path.startswith(scratch):
                navpatch.close_pak(path, every=True)
        bucket.ACTIVE = False
        os.environ.pop('OMD3_BUCKET', None)


def run_map(map_name, scratch, cases, results):
    from riftlord.build import makelevel
    from riftlord.onslaught import onslaught
    from riftlord.onslaught import wavespec
    _path, coord_name = makelevel.coordinator_of(map_name)
    base = wavespec.read_onslaught(map_name)
    sh = wavespec.shape(coord_name)
    spawners = sorted({sp['index'] for sp in onslaught.map_report(map_name)['spawners']})
    for case in cases:
        def one(case=case):
            spec = make_case(case, base, map_name, sh)
            moved = spec.pop('moved', None)
            out = os.path.join(scratch, 'out_' + map_name)
            shutil.rmtree(out, ignore_errors=True)
            try:
                res = wavespec.build(dict(spec, map=map_name, raw=True, out=out,
                                          title='SaveTest %s %s' % (map_name, case)))
                coord = res['report']['coordinator']
                base_ = out + '/OMD/Content' + coord[len('/Game'):]
                if not os.path.exists(base_ + '.uasset'):
                    # write_raw may nest the content under a folder of its own
                    hits = [os.path.join(d, f) for d, _s, fs in os.walk(out) for f in fs
                            if f == os.path.basename(coord) + '.uasset']
                    base_ = hits[0][:-len('.uasset')]
                with open(base_ + '.uasset', 'rb') as a, open(base_ + '.uexp', 'rb') as b:
                    ua, ux = a.read(), b.read()
                par = _par_issues(spec, out)
            finally:
                shutil.rmtree(out, ignore_errors=True)
            with _coordinator_as(coord_name, ua, ux):
                got = wavespec.read_onslaught(map_name)
            issues = compare(expected(spec, map_name), got) + structure(
                ua, ux, len(spec['waves']), spawners) + par
            if issues:
                raise SystemExit('; '.join(issues))
            return _done(spec, res, moved)
        _record(results, '%s %s' % (map_name, case), one)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--missions', nargs='*', default=list(MISSIONS))
    ap.add_argument('--maps', nargs='*', default=list(MAPS))
    ap.add_argument('--cases', nargs='*', default=list(CASES), choices=CASES)
    args = ap.parse_args(argv)
    scratch = tempfile.mkdtemp(prefix='onslaught-savetest-', dir=SCRATCH_PARENT)
    # flowsim's check paks follow TMPDIR; keep them off the RAM disk too
    old_tmp = tempfile.tempdir
    tempfile.tempdir = scratch
    results = []
    try:
        for ident in args.missions:
            run_mission(ident, scratch, args.cases, results)
        for map_name in args.maps:
            run_map(map_name, scratch, args.cases, results)
    finally:
        tempfile.tempdir = old_tmp
        shutil.rmtree(scratch, ignore_errors=True)
    counts = {s: sum(1 for r in results if r[0] == s) for s in ('PASS', 'FAIL', 'SKIP')}
    print('%(PASS)d passed, %(FAIL)d failed, %(SKIP)d skipped' % counts)
    return 1 if counts['FAIL'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
