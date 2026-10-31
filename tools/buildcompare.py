"""Build one Missions record from source in two trees and compare the paks byte for byte.

    python3 tools/buildcompare.py RECORD [--with ITEM] [--old snapshot|DIR] [--new DIR] [--out DIR]

`--old snapshot` (the default) is the old tree's code as last taken (.sync/base, each
file checked against .sync/manifest.json). It sits on scratch beside links to
Win64's game tooling (UHTHeaderDump, Mappings.usmap, the exe, Mods), so its relative
paths reach the same files and the old tree itself is only read.

Each build runs in scratch folders of its own (XDG data/cache/config, TMPDIR, Missions,
plans, source caches) with a copy of RECORD from the user's Missions, which is only read.
A side's build is cached under a digest of its code, record and bpkit (`inputs_digest`)
with the files it opened outside its own folders (game paks, kits, Fab libraries; recorded
by an audit hook) and reused while all of those are unchanged in size and time;
`--fresh` builds both.

    python3 tools/buildcompare.py RECORD --repin --expect GLOB[,GLOB] [--why TEXT] [--accept]

re-pins a pinned record (BASELINES) in one command: builds it with this tree, lists what
differs from its pin (each package's added and removed names and changed exports), and
pins the new build, rewriting its BASELINES entry in this file, only when every differing
entry matches an --expect glob (entries EXPECTED allows for the record count as matched),
unless --accept. A Missions item the gate compares the record with (gate.RECORDS) is copied
in as --with would. Writes report.json in the run folder; exit 0 when every pak entry is identical, except
a campaign table whose only difference is the Endless extension row's per-build Guid, and
the entries EXPECTED declares for the record (each with its reason and the step that
certifies it instead; `--kinds RECORD...` prints which apply).
"""

import argparse
import fnmatch
import hashlib
import json
import os
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
NEW = os.path.dirname(HERE)
sys.path.insert(0, NEW)
sys.path.insert(0, HERE)

import oldtree                                        # noqa: E402
import testservers                                    # noqa: E402

SCRATCH = os.path.join(testservers.SCRATCH, 'buildcompare')
#: The game's Win64 folder: RIFTLORD_WIN64 when the code runs from a copy (the gate's snapshot).
WIN64 = os.environ.get('RIFTLORD_WIN64') or os.path.dirname(NEW)
INSTALL = os.path.normpath(os.path.join(WIN64, '..', '..', '..'))
#: Win64 entries the code reaches by relative path (bpkit, trapmod, kitactors).
WIN64_LINKS = ['UHTHeaderDump', 'CXXHeaderDump', 'Mappings.usmap', 'OMD-Win64-Shipping.exe',
               'Mods']
#: The Missions folder records are copied from: the test missions (tests/fixtures/missions).
MISSIONS = testservers.FIXTURE_MISSIONS
#: Where the test missions' Fab and terrain libraries and mod paks are written
#: (tests/fixtures/fixturelibs.py), never the user's.
FIXTURE_LIBS = os.path.join(testservers.SCRATCH, 'fixture-libraries')
NICE = ['nice', '-n', '10', 'ionice', '-c', '3']
BUILD_TIMEOUT = 3 * 3600

SOURCE_BUILD = r'''
import json, os, sys
#: every file the build opens outside its own folders, for the cache's input check
_opened, _skip = set(), tuple(os.path.realpath(p) for p in sys.argv[4:])
def _audit(event, args):
    if event == 'open' and isinstance(args[0], str) and not args[0].startswith(_skip):
        _opened.add(args[0])
sys.addaudithook(_audit)
sys.path.insert(0, sys.argv[1])
try:
    from riftlord.missions import bucket          # this layout
except ImportError:
    import bucket                                 # the old tree's flat layout
bucket.activate()
got = bucket.build([sys.argv[2]])
print('BUILD', json.dumps(got), flush=True)
if got['built']:
    print('PACKAGE', json.dumps(bucket.package([sys.argv[2]], sys.argv[3])), flush=True)
with open(sys.argv[3] + '.inputs.json', 'w') as fh:
    json.dump(sorted(_opened), fh)
'''


def snapshot(dest):
    """The old tree's code at copy time, in dest/w64/OMD2-Converter. Returns its root."""
    man = oldtree.load()
    w64 = os.path.join(dest, 'w64')
    root = os.path.join(w64, 'OMD2-Converter')
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    for name in WIN64_LINKS:
        link = os.path.join(w64, name)
        if not os.path.lexists(link) and os.path.exists(os.path.join(WIN64, name)):
            os.symlink(os.path.join(WIN64, name), link)
    unknown = []
    for rel, f in list(man['files'].items()) + list(man.get('dropped', {}).items()):
        src = oldtree.base_path(rel)
        if not os.path.isfile(src) or oldtree.sha(src) != f['old']:
            unknown.append(rel)
            continue
        os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
        shutil.copy2(src, os.path.join(root, rel))
    if unknown:
        raise SystemExit('no base copy matching the manifest for: %s (tools/oldtree.py '
                         'init-base)' % ', '.join(unknown))
    return root


#: The reason a level differs only by its cleaned walkable graph (navfan_certify).
NAVFAN_REASON = 'walkable graph cleaned (navfan)'
#: The reason a level's added spawners differ only by facing their routes (spawnerfacing_apply).
SPAWNERFACING_REASON = 'added spawners face their routes (spawnerfacing)'
#: Degrees an added spawner's yaw may be off spawnerfacing's rule read back (its 0.1 rounding).
FACING_SLACK = 0.1
#: The reason a level differs where geomedits.park wrote it (content_certify).
PARKED_REASON = 'parked actors as geomedits.park writes them'
#: Entries allowed to differ from the old code's build: (kind, patterns, reason, certifier).
#: `kind` is what record_kinds() reports for a record; `certifier` is the step that checks
#: those entries instead (the gate runs it when a compared record has that kind).
EXPECTED = [('regenerate', ('*_Minimap.uexp', '*_Minimap.uasset', '*_Minimap_*.uexp',
                            '*_Minimap_*.uasset'),
             'Regenerate improved; certified by minimapscore --gate (all three cuts, as the '
             'baseline was chosen from)',
             ['-m', 'riftlord.build.minimap.minimapscore', '--gate', '--cut', 'three']),
            # by content, not name: an entry whose every differing byte is inside an ISPC
            # output the new build recorded (tools/bccertify.py), each graded against the old
            # build's Python-encoded bytes there; certified in the comparison itself
            ('ispc', (), 'ISPC encoder', None),
            # by content: a level whose only difference is its walkable graph, the new one
            # being the old one through navfan.clean with no fan defect; certified in the
            # comparison itself (navfan_certify)
            ('navfan', (), NAVFAN_REASON, None),
            # by content: a level whose added spawners' root rotation went from yaw 0 (or
            # none) to spawnerfacing's rule for each, read back from the new level, nothing
            # else in it differing; certified in the comparison itself (spawnerfacing_apply)
            ('spawnerfacing', (), SPAWNERFACING_REASON, None),
            # by content: a level (or sublevel) whose parked actors' sounds are silenced
            # (geomedits.silence) and whose own moon, under a sky from another home
            # (skyfog.own_extras), is parked as geomedits.park writes it, read with FNames
            # resolved, nothing else in it differing; the walkable graph and spawner facing
            # certified alongside; certified in the comparison itself (content_certify)
            ('parked', (), PARKED_REASON, None)]
#: Per-record baselines other than a build of the old code (docs/PLAN.md "The gate"):
#: {record: {'mode': 'none', 'why': ...}} builds the new code only, with no byte
#: comparison; {'mode': 'pinned', 'digest': ..., 'what': ...} compares with a stored build
#: (PINNED/<record>/<digest>), the same EXPECTED and certify rules applying. Pin a build
#: with `--pin RECORD --from RUN_FOLDER --side old|new`; a pinned build that goes missing
#: fails the comparison saying so. The test missions (tests/fixtures/missions) are pinned:
#: the old code builds neither ("no saved record or waves to build from"), so each is
#: compared with the new code's build pinned when it was made.
#: Kept in PINS_FILE, a data file outside the gate's shared code, so a re-pin reruns only
#: the comparisons that read it.
PINS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'buildcompare-pins.json')


def load_pins(path=None):
    """BASELINES as PINS_FILE (or `path`) holds them."""
    with open(path or PINS_FILE) as fh:
        return json.load(fh)


BASELINES = load_pins()
#: Where pinned builds are kept.
PINNED = os.path.join(testservers.SCRATCH, 'buildcompare-pinned')
#: Where the new build records its ISPC encodes (ispctex.RECORD_ENV), in its work folder.
BC_RECORD = 'bc-record'


def record_kinds(ident):
    """The EXPECTED kinds that apply to a Missions record: 'regenerate' when its minimap is
    Regenerate's drawing (mode Regenerate, or a scratch level, which always draws its own)."""
    from riftlord.build.minimap import minimapredraw
    from riftlord.mapeditor import scratch
    path = os.path.join(MISSIONS, '.riftlord', 'records', ident + '.json')
    try:
        with open(path) as fh:
            plan = json.load(fh).get('plan') or {}
    except (OSError, ValueError):
        return []
    return ['regenerate'] if scratch.is_scratch(plan) or \
        minimapredraw.mode(plan) == 'regenerate' else []


def expected_reason(name, kinds):
    """The reason `name` may differ for a record of these kinds, or None."""
    for kind, patterns, reason, _cert in EXPECTED:
        if kind in kinds and any(fnmatch.fnmatchcase(name, p) for p in patterns):
            return reason
    return None


def copy_item(ident, missions):
    """Copy a Missions item that has no record (a custom campaign): its folder, pak and side
    files. MISSIONS is only read."""
    import glob
    src = MISSIONS
    folder = os.path.join(src, ident)
    if os.path.isdir(folder):
        shutil.copytree(folder, os.path.join(missions, ident), symlinks=False)
    for side in glob.glob(os.path.join(glob.escape(src), glob.escape(ident) + '.*')):
        if os.path.isfile(side):
            shutil.copy2(side, missions)


#: Built paks kept per (side, record, inputs digest): the old code is frozen, so its build
#: of the same inputs never changes; the new code's is reused while nothing it reads did.
CACHE = os.path.join(testservers.SCRATCH, 'buildcompare-cache')
#: Cached builds kept per side and record.
CACHE_KEEP = 2
#: The new code's light bakes (lightbake.LIGHTBAKE_CACHE), reused while their inputs are
#: unchanged, and how many are kept per level and per sublevel.
BAKES = os.path.join(testservers.SCRATCH, 'buildcompare-bakes')
BAKES_KEEP = 3


def _file_sha(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for block in iter(lambda: fh.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def _stamps(folder, suffixes):
    """[(relative path, size, mtime_ns)] of the files under `folder` with these suffixes."""
    out = []
    for base, dirs, files in os.walk(folder):
        dirs.sort()
        for f in sorted(files):
            if f.endswith(suffixes):
                st = os.stat(os.path.join(base, f))
                out.append((os.path.relpath(os.path.join(base, f), folder), st.st_size,
                            st.st_mtime_ns))
    return out


def code_digest(label, tree):
    """What the code side is: the base snapshot's hashes (old, frozen) or this package's
    files (new)."""
    import hashlib
    h = hashlib.sha256(SOURCE_BUILD.encode())
    if label == 'old':
        man = oldtree.load()
        for rel, f in sorted(list(man['files'].items()) + list(man.get('dropped', {}).items())):
            h.update(('%s %s\n' % (rel, f['old'])).encode())
    else:
        for rel, _size, _mt in _stamps(os.path.join(tree, 'riftlord'), ('.py', '.json')):
            h.update(('%s %s\n' % (rel, _file_sha(os.path.join(tree, 'riftlord', rel)))).encode())
    return h.hexdigest()


def inputs_digest(label, tree, ident, extra):
    """The digest of what a build of `ident` is made from: the code side, the record and
    the Missions items copied in, and the vendored bpkit. The game files it reads are checked
    separately, against the list the build recorded (cached)."""
    import glob
    import hashlib
    h = hashlib.sha256(('%s %s %s\n' % (label, code_digest(label, tree), sys.version)).encode())
    src = MISSIONS
    rec_dir = os.path.join(src, '.riftlord', 'records')
    for f in sorted(glob.glob(os.path.join(glob.escape(rec_dir), glob.escape(ident) + '.*'))):
        h.update(('%s %s\n' % (os.path.basename(f), _file_sha(f))).encode())
    for item in extra:
        h.update(repr(_stamps(os.path.join(src, item), ('',))).encode())
        for f in sorted(glob.glob(os.path.join(glob.escape(src), glob.escape(item) + '.*'))):
            if os.path.isfile(f):
                h.update(('%s %s\n' % (os.path.basename(f), _file_sha(f))).encode())
    from riftlord.core import bpkitlink
    for rel, _s, _m in _stamps(bpkitlink.VENDORED, ('.py',)):
        h.update(('bpkit %s %s\n' % (rel, _file_sha(os.path.join(bpkitlink.VENDORED, rel)))).encode())
    return h.hexdigest()[:24]


def _stamp(path):
    try:
        st = os.stat(path)
        return [st.st_size, st.st_mtime_ns]
    except OSError:
        return None


def cached(label, ident, key):
    """{pak name: path} of a cached build whose recorded inputs are all unchanged, or None."""
    folder = os.path.join(CACHE, '%s-%s-%s' % (label, ident, key))
    try:
        with open(os.path.join(folder, 'inputs.json')) as fh:
            inputs = json.load(fh)
    except (OSError, ValueError):
        return None
    if any(_stamp(path) != stamp for path, stamp in inputs.items()):
        return None
    return {f: os.path.join(folder, f) for f in sorted(os.listdir(folder)) if f.endswith('.pak')}


def store(label, ident, key, paks, inputs, bc_record=None):
    """Keep a build's paks, its ISPC record and its inputs' stamps under its key; drop all
    but the newest CACHE_KEEP of this side and record."""
    import glob
    folder = os.path.join(CACHE, '%s-%s-%s' % (label, ident, key))
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder)
    for name, path in paks.items():
        shutil.copy2(path, os.path.join(folder, name))
    if bc_record and os.path.isdir(bc_record):
        shutil.copytree(bc_record, os.path.join(folder, BC_RECORD))
    with open(os.path.join(folder, 'inputs.json'), 'w') as fh:
        json.dump({p: _stamp(p) for p in inputs}, fh, indent=0)
    others = sorted(glob.glob(os.path.join(glob.escape(CACHE), '%s-%s-*' % (label, ident))),
                    key=os.path.getmtime, reverse=True)
    for old in others[CACHE_KEEP:]:
        shutil.rmtree(old, ignore_errors=True)


def fixture_env():
    """The test missions' Fab and terrain libraries and Paks folder (fixturelibs.environment
    under FIXTURE_LIBS), written when missing or not current."""
    sys.path.insert(0, os.path.join(NEW, 'tests', 'fixtures'))
    import fixturelibs
    return fixturelibs.environment(FIXTURE_LIBS)


def build(tree, ident, work, label, extra=()):
    """Build `ident` with the code in `tree` and package it; returns ({pak name: path} or
    None, log path, seconds). A package holds the mission's pak and any campaign's."""
    dirs = {k: os.path.join(work, label, k) for k in
            ('data', 'cache', 'config', 'Missions', 'tmp', 'source-cache')}
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)
    testservers.copy_mission(ident, dirs['Missions'], MISSIONS, built=False)
    for item in extra:
        copy_item(item, dirs['Missions'])
    env = dict(os.environ, XDG_DATA_HOME=dirs['data'], XDG_CACHE_HOME=dirs['cache'],
               XDG_CONFIG_HOME=dirs['config'], OMD3_BUCKET=dirs['Missions'],
               TMPDIR=dirs['tmp'], OMD3_INSTALL=INSTALL,
               RIFTLORD_PLANS=os.path.join(dirs['data'], 'riftlord-plans'),
               RIFTLORD_SOURCE_CACHE=dirs['source-cache'], PYTHONDONTWRITEBYTECODE='1',
               **fixture_env())
    if label == 'new':
        env['RIFTLORD_BC_RECORD'] = os.path.join(work, label, BC_RECORD)   # ispctex.RECORD_ENV
        # light bakes reused across runs while their inputs are unchanged (lightbake.reusable)
        env['LIGHTBAKE_CACHE'] = BAKES
        env['LIGHTBAKE_INPUTS_SKIP'] = work
    zpath = os.path.join(work, '%s-%s.zip' % (label, ident))
    log = os.path.join(work, '%s-build.log' % label)
    t = time.time()
    with open(log, 'wb') as out:
        skip = [work, tree, sys.prefix, sys.base_prefix, '/usr', '/proc', '/dev', '/sys', '/etc',
                tempfile.gettempdir()]
        p = subprocess.Popen(NICE + [sys.executable, '-B', '-c', SOURCE_BUILD, tree, ident, zpath]
                             + skip,
                             cwd=tree, env=env, stdout=out, stderr=subprocess.STDOUT,
                             start_new_session=True)
        try:
            code = p.wait(BUILD_TIMEOUT)
        except subprocess.TimeoutExpired:
            code = None
        finally:
            try:
                os.killpg(p.pid, signal.SIGKILL)      # the build and anything it started
            except (ProcessLookupError, PermissionError):
                pass
    secs = time.time() - t
    if code != 0 or not os.path.exists(zpath):
        return None, log, secs, None
    out = os.path.join(work, '%s-pak' % label)
    with zipfile.ZipFile(zpath) as z:
        paks = [n for n in z.namelist() if n.endswith('.pak')]
        z.extractall(out)
    built = {os.path.basename(n): os.path.join(out, n) for n in paks}
    try:
        with open(zpath + '.inputs.json') as fh:
            inputs = [p for p in json.load(fh) if os.path.isfile(p)]
    except (OSError, ValueError):
        inputs = None
    return built, log, secs, inputs


def bake_lines(log):
    """The light bake's hit or miss lines in a build log ('lightbake: reused ...',
    'lightbake: baking ...', 'lightbake: not baking ...'); [] for a build that bakes
    nothing."""
    try:
        with open(log, encoding='utf-8', errors='replace') as fh:
            return [ln.strip() for ln in fh if ln.startswith(('lightbake: reused',
                                                              'lightbake: baking',
                                                              'lightbake: not baking'))]
    except OSError:
        return []


def entries(pak):
    from riftlord.core import paksource
    return paksource.entries(pak)


def fresh_guid_only(name, x, y):
    """True when a mission or campaign table differs only inside one 16-byte span: a Guid
    makemod draws with uuid4 on every build (the OMD3_Endless extension row; an Endless
    twin whose record keeps no Guid), so any two builds of the same code differ there."""
    if not name.endswith(('_CampaignProtos.uexp', '_MissionProtos.uexp')) or len(x) != len(y):
        return False
    at = [i for i in range(len(x)) if x[i] != y[i]]
    return bool(at) and at[-1] - at[0] < 16


def _ispc(x, y, recs, outs, name, expected, differ):
    """True when the entry's differences are all the ISPC encoder's (bccertify.explain);
    it then goes to `expected` when every graded texture passes, else to `differ`."""
    import bccertify
    ok, grades = bccertify.explain(x, y, recs, outs)
    if not ok:
        return False
    bad = [g for g in grades if not g['ok']]
    row = {'entry': name, 'reason': 'ISPC encoder: %d texture(s) graded, %d over the Python '
           'encoder\'s RMSE + %.2f' % (len(grades), len(bad), bccertify.SLACK),
           'sizes': [len(x), len(y)], 'grades': grades}
    if bad:
        row.update(first_diff=0, worst=bad[:3])
        differ.append(row)
    else:
        expected.append(row)
    return True


def _polygon_set(buf):
    """[Counter of polygons (vertex tuples rounded to 0.01 uu)] per agent graph, and the
    polygons as lists."""
    import collections
    from riftlord.build.navigation import navsurf
    out = []
    for head in navsurf.graphs(buf):
        polys = [[tuple(v) for v in s['verts']] for s in navsurf.sections(buf, head)]
        out.append(polys)
    return [collections.Counter(_polygon_key(p) for p in polys) for polys in out], out


def _polygon_key(poly):
    """A polygon's vertices rounded to 0.01 uu, as a hashable key."""
    return tuple(tuple(round(c, 2) for c in v) for v in poly)


def navfan_certify(a_ua, a_ux, b_ua, b_ux):
    """(ok, detail): the new level is the old one with only its walkable graph replaced
    (the old level re-injected with the new buffer comes out byte for byte the new level),
    the new graph has no fan defect (navfan.problems), and its polygons are the old graph's
    through navfan.clean (ids may renumber)."""
    from riftlord.build.navigation import navsurfextend
    from riftlord.build.navigation import navsurfinject
    try:
        old_buf = navsurfextend._surface_of(a_ua, a_ux)
        new_buf = navsurfextend._surface_of(b_ua, b_ux)
        ua, ux, rows = navsurfinject.set_surface_bytes(a_ua, a_ux, new_buf)
    except Exception as exc:                          # noqa: BLE001
        return False, 'no walkable graph to compare: %s' % exc
    if not all(ok for _n, ok in rows) or ua != b_ua or ux != b_ux:
        return False, 'differs outside the walkable-graph buffer'
    return navfan_graphs(old_buf, new_buf)


def navfan_graphs(old_buf, new_buf):
    """(ok, detail): the new walkable graph has no fan defect (navfan.problems) and its
    polygons are the old graph's through navfan.clean (ids may renumber)."""
    import collections
    from riftlord.build.navigation import navfan
    from riftlord.build.navigation import navsurf
    bad = navfan.problems(new_buf)
    if bad:
        return False, 'the new walkable graph has fan defects: %s' % '; '.join(bad[:3])
    want_sets, old_polys = _polygon_set(old_buf)
    got_sets, _new = _polygon_set(new_buf)
    if len(want_sets) != len(got_sets):
        return False, 'the walkable graphs differ in agent count'
    notes = []
    for g, (polys, got) in enumerate(zip(old_polys, got_sets)):
        cleaned, _n = navfan.clean(polys)
        want = collections.Counter(_polygon_key(p) for p in cleaned)
        if want != got:
            return False, 'graph %d: the new polygons are not the old ones cleaned (%d old, %d ' \
                          'new, %d differing)' % (g, len(polys), sum(got.values()),
                                                  sum(((want - got) + (got - want)).values()))
        area = lambda c: sum(abs(navsurf.polygon_area(list(p))) * n for p, n in c.items())
        notes.append('graph %d: %d -> %d polygons, %d changed, area %.0f -> %.0f uu2' % (
            g, len(polys), sum(got.values()),
            sum((want_sets[g] - got).values()), area(want_sets[g]), area(got)))
    return True, '; '.join(notes)


def spawnerfacing_apply(a_ua, a_ux, b_ua, b_ux):
    """(notes, ua, ux, refused): the old level with each added spawner whose root rotation
    differs given the new level's, when the old one is yaw 0 and the new one is yaw only and
    within FACING_SLACK of spawnerfacing's rule for it (spawnerfacing.read_level `want`).
    `notes` names each spawner turned; `refused` says why one may not be, else None."""
    from riftlord.build import apply
    from riftlord.build import levelbuild
    from riftlord.mapeditor import spawnerfacing
    try:
        old = {r['name']: r for r in spawnerfacing.read_level(a_ua, a_ux)}
        new = {r['name']: r for r in spawnerfacing.read_level(b_ua, b_ux)}
    except Exception as exc:                          # noqa: BLE001
        return [], a_ua, a_ux, 'the added spawners could not be read back: %s' % exc
    builder, notes = None, []
    for name in sorted(set(old) & set(new)):
        o, n = old[name], new[name]
        if [o[k] for k in ('pitch', 'yaw', 'roll')] == [n[k] for k in ('pitch', 'yaw', 'roll')]:
            continue
        if o['pitch'] or o['yaw'] or o['roll']:
            return notes, a_ua, a_ux, '%s is turned in the old level (pitch %s, yaw %s, roll %s)' \
                % (name, o['pitch'], o['yaw'], o['roll'])
        if n['pitch'] or n['roll'] or n['off'] > FACING_SLACK:
            return notes, a_ua, a_ux, '%s is not spawnerfacing\'s rule in the new level (pitch ' \
                '%s, yaw %s, roll %s; the rule %s)' % (name, n['pitch'], n['yaw'], n['roll'],
                                                       n['want'])
        builder = builder or levelbuild.Builder.around(a_ua, a_ux)
        apply.place_whole(builder, builder.index_of(name), rotation=(0.0, n['yaw'], 0.0))
        notes.append('%s yaw 0 -> %s (rule %s)' % (name, n['yaw'], n['want']))
    if builder is None:
        return notes, a_ua, a_ux, None
    ua, ux = builder.pkg.write()
    return notes, ua, ux, None


# -- a level read with FNames resolved -----------------------------------------------------

#: Native structs written as raw values, by struct name: their struct format (None: bytes).
RAW_STRUCTS = {'Vector': '<3f', 'Rotator': '<3f', 'Vector2D': '<2f', 'LinearColor': '<4f',
               'Color': '<4B', 'Quat': '<4f', 'IntPoint': '<2i', 'Vector4': '<4f',
               'IntVector': '<3i', 'Guid': None, 'Box': None}
#: Names geomedits.park and the spawner facing may add to a level's name table.
PARKED_NAMES = {'bAutoActivate', 'Sound', 'bShowMarker', 'RelativeLocation', 'RelativeRotation',
                'Rotator', 'Vector', 'BoolProperty', 'ObjectProperty', 'StructProperty'}
#: An export's meta bytes left out of the comparison: its name (FName) and serial size and
#: offset.
META_SKIP = ((16, 24), (28, 44))


class _Tags:
    """One export's tagged properties, FNames and object references resolved."""

    def __init__(self, pkg, paths, data):
        self.pkg, self.paths, self.d, self.o = pkg, paths, data, 0
        self.names = [n for n, _h in pkg.names]

    def i32(self):
        v = struct.unpack_from('<i', self.d, self.o)[0]
        self.o += 4
        return v

    def fname(self):
        i, n = self.i32(), self.i32()
        s = self.names[i] if 0 <= i < len(self.names) else '?%d' % i
        return s if not n else '%s_%d' % (s, n - 1)

    def obj(self, idx):
        if idx > 0:
            return ('export', self.paths[idx - 1] if idx <= len(self.paths) else idx)
        if idx < 0:
            return ('import', levelbuild_resolve(self.pkg, idx)[1])
        return None

    def read(self):
        """(props, the bytes after them), or (None, the export's bytes) when it cannot be read."""
        try:
            props = self.props(len(self.d))
            return props, bytes(self.d[self.o:])
        except Exception:                            # noqa: BLE001
            return None, bytes(self.d)

    def props(self, end):
        out = []
        while self.o < end:
            name = self.fname()
            if name == 'None':
                return out
            tp = self.fname()
            size, idx = self.i32(), self.i32()
            extra = None
            if tp == 'StructProperty':
                extra = self.fname()
                self.o += 16
            elif tp in ('ByteProperty', 'EnumProperty', 'ArrayProperty', 'SetProperty'):
                extra = self.fname()
            elif tp == 'MapProperty':
                extra = (self.fname(), self.fname())
            key = '%s[%d]' % (name, idx)
            if tp == 'BoolProperty':
                val = bool(self.d[self.o])
                self.o += 2
                out.append((key, tp, val))
                continue
            self.o += 1
            start = self.o
            val = self.value(tp, extra, size)
            self.o = start + size
            out.append((key, '%s:%s' % (tp, extra) if extra else tp, val))
        raise ValueError('no None terminator')

    def value(self, tp, extra, size):
        d, o = self.d, self.o
        if tp == 'NameProperty':
            return self.fname()
        if tp in ('ObjectProperty', 'ClassProperty', 'WeakObjectProperty'):
            return self.obj(self.i32())
        if tp in ('ByteProperty', 'EnumProperty') and size == 8:
            return self.fname()
        if tp in ('SoftObjectProperty', 'SoftClassProperty') or (
                tp == 'StructProperty' and extra in ('SoftObjectPath', 'SoftClassPath')):
            return (self.fname(), bytes(d[self.o:o + size]))
        if tp == 'DelegateProperty':
            return (self.obj(self.i32()), self.fname())
        if tp == 'StructProperty':
            if extra in RAW_STRUCTS:
                fmt = RAW_STRUCTS[extra]
                return struct.unpack_from(fmt, d, o) if fmt else bytes(d[o:o + size])
            return self.props(o + size)
        if tp == 'ArrayProperty':
            return self.array(extra, o + size)
        if tp in ('MapProperty', 'SetProperty', 'TextProperty', 'MulticastDelegateProperty',
                  'InterfaceProperty', 'LazyObjectProperty'):
            raise ValueError('%s holds names this reader does not resolve' % tp)
        return bytes(d[o:o + size])

    def array(self, inner, end):
        count = self.i32()
        if inner == 'StructProperty':
            self.fname(), self.fname(), self.i32(), self.i32()
            sname = self.fname()
            self.o += 17
            fmt = RAW_STRUCTS.get(sname, 'tagged')
            if fmt is None:
                return bytes(self.d[self.o:end])
            if fmt != 'tagged':
                step = struct.calcsize(fmt)
                out = [struct.unpack_from(fmt, self.d, self.o + k * step) for k in range(count)]
                self.o += step * count
                return out
            return [self.props(end) for _ in range(count)]
        if inner == 'NameProperty':
            return [self.fname() for _ in range(count)]
        if inner in ('ObjectProperty', 'ClassProperty'):
            return [self.obj(self.i32()) for _ in range(count)]
        if inner in ('ByteProperty', 'EnumProperty') and end - self.o == 8 * count:
            return [self.fname() for _ in range(count)]
        if inner in ('ByteProperty', 'IntProperty', 'FloatProperty', 'BoolProperty',
                     'UInt32Property', 'Int64Property', 'UInt64Property'):
            return bytes(self.d[self.o:end])
        raise ValueError('an array of %s' % inner)


def levelbuild_resolve(pkg, idx):
    from riftlord.build import levelbuild
    return levelbuild.resolve(pkg, idx)


def level_content(ua, ux):
    """A level read with FNames resolved: {'pkg', 'paths' [export path per index], 'exports'
    {path: (index, class, meta, props, tail)}, 'imports', 'names', 'preload'}."""
    from riftlord.build import levelbuild
    from riftlord.formats.ue.uassetio import Package
    pkg = Package(ua, ux)
    pkg.load_payloads()
    names = [n for n, _h in pkg.names]
    raw = []
    for i, e in enumerate(pkg.exports):
        parts, j = [], i
        while j >= 0 and len(parts) < 32:
            parts.append(levelbuild._name_of(pkg, j))
            outer = struct.unpack_from('<i', pkg.exports[j]['meta'], levelbuild.OUTER_AT)[0]
            j = outer - 1 if outer > 0 else -1
        raw.append('/'.join(reversed(parts)))
    paths, seen = [], set()
    for p in raw:
        while p in seen:
            p += '+'
        seen.add(p)
        paths.append(p)
    exports = {}
    for i, e in enumerate(pkg.exports):
        meta = bytes(e['meta'])
        kept = meta[:META_SKIP[0][0]] + meta[META_SKIP[0][1]:META_SKIP[1][0]] + \
            meta[META_SKIP[1][1]:]
        cls = levelbuild.resolve(pkg, struct.unpack_from('<i', meta, levelbuild.CLASS_AT)[0])[1]
        props, tail = _Tags(pkg, paths, e['data']).read()
        exports[paths[i]] = (i + 1, cls, kept, props, tail)
    imports = []
    for rec in pkg.imports:
        cp, cn, outer, on = (struct.unpack_from('<I', rec, 0)[0],
                             struct.unpack_from('<I', rec, 8)[0],
                             struct.unpack_from('<i', rec, 16)[0],
                             struct.unpack_from('<I', rec, 20)[0])
        imports.append((names[cp], names[cn], outer, names[on], bytes(rec[4:8]),
                        bytes(rec[12:16]), bytes(rec[24:])))
    return {'pkg': pkg, 'paths': paths, 'exports': exports, 'imports': imports,
            'names': set(names), 'preload': list(pkg.preload)}


def _close(v, w, slack=1e-3):
    return v is not None and w is not None and len(v) == len(w) and \
        all(abs(float(x) - float(y)) <= slack for x, y in zip(v, w))


def _parked_at(loc):
    from riftlord.mapeditor import geomedits
    return any(_close(loc, spot, 0.5) for spot in (geomedits.PARKED, geomedits.PARKED_AWAY))


def _mentions(value, words):
    """True when a resolved value (nested lists and tuples) holds a string with one of `words`."""
    if isinstance(value, str):
        return any(w in value for w in words)
    if isinstance(value, (list, tuple)):
        return any(_mentions(v, words) for v in value)
    return False


def content_certify(a_ua, a_ux, b_ua, b_ux):
    """(ok, detail): the new level, read with FNames resolved (level_content), is the old one
    but for exports these account for, each by its own rule:
      - an AudioComponent of an actor parked in the new level: `bAutoActivate` false and
        `Sound` none (geomedits.silence);
      - an OMDMapDisplayComponent of a parked actor: `bShowMarker` false (hide_markers);
      - the root component of an actor with a moon (skyfog.SKY_EXTRA_WORDS) on it: only its
        RelativeLocation, moved to a parking spot (geomedits.park);
      - the root component of an added spawner: only its RelativeRotation, from none or yaw 0
        to spawnerfacing's rule (as spawnerfacing_apply);
      - the walkable-graph export: its graph cleaned (navfan_graphs), the rest of the export
        as the old level given the new graph.
    The imports, the export table (less names and serial offsets) and the preloads must be
    the same, and the only names gained PARKED_NAMES."""
    from riftlord.build import levelbuild
    from riftlord.build.world import skyfog
    from riftlord.mapeditor import geomedits
    from riftlord.mapeditor import spawnerfacing
    try:
        a, b = level_content(a_ua, a_ux), level_content(b_ua, b_ux)
    except Exception as exc:                          # noqa: BLE001
        return False, 'the levels could not be read: %s' % exc
    if a['paths'] != b['paths']:
        gone = sorted(set(a['paths']) - set(b['paths']))[:3]
        new = sorted(set(b['paths']) - set(a['paths']))[:3]
        return False, 'the exports differ (only old %s, only new %s)' % (gone, new)
    if a['imports'] != b['imports']:
        return False, 'the imports differ'
    if a['preload'] != b['preload']:
        return False, 'the preloads differ'
    if a['names'] - b['names'] or not (b['names'] - a['names']) <= PARKED_NAMES:
        return False, 'the name tables differ: gained %s, lost %s' % (
            sorted(b['names'] - a['names'])[:6], sorted(a['names'] - b['names'])[:6])
    builder = levelbuild.Builder.around(b_ua, b_ux)
    paths, ex_a, ex_b = b['paths'], a['exports'], b['exports']

    def owner(index):
        return struct.unpack_from('<i', b['pkg'].exports[index - 1]['meta'],
                                  levelbuild.OUTER_AT)[0]

    def location(side, actor):
        root = builder._root_component(actor)
        if not root:
            return None
        got = dict((k, v) for k, _t, v in (side[paths[root - 1]][3] or []))
        return got.get('RelativeLocation[0]')

    def parked(actor):
        return actor > 0 and _parked_at(location(ex_b, actor))

    def has_moon(actor):
        return any(_mentions(ex_b[p][3], skyfog.SKY_EXTRA_WORDS) for i, p in enumerate(paths)
                   if p == paths[actor - 1] or owner(i + 1) == actor)

    facing = None
    counts = {'silenced': 0, 'markers': 0}
    moons, turned, graph = [], [], None
    for path in paths:
        index, cls, meta, props, tail = ex_b[path]
        _i, _c, meta_a, props_a, tail_a = ex_a[path]
        if meta != meta_a:
            return False, '%s: its export entry differs' % path
        if props == props_a and tail == tail_a:
            continue
        if props is None or props_a is None:
            return False, '%s (%s) differs and cannot be read' % (path, cls)
        da = dict((k, (t, v)) for k, t, v in props_a)
        db = dict((k, (t, v)) for k, t, v in props)
        keys = sorted(k for k in set(da) | set(db) if da.get(k) != db.get(k))
        actor = owner(index)
        if cls and cls.endswith('NavigationData') and not keys:
            from riftlord.build.navigation import navsurfextend
            from riftlord.build.navigation import navsurfinject
            try:
                new_buf = navsurfextend._surface_of(b_ua, b_ux)
                ok, detail = navfan_graphs(navsurfextend._surface_of(a_ua, a_ux), new_buf)
                ua, ux, _rows = navsurfinject.set_surface_bytes(a_ua, a_ux, new_buf)
                given = level_content(ua, ux)['exports'][path]
            except Exception as exc:                  # noqa: BLE001
                return False, '%s: no walkable graph to compare: %s' % (path, exc)
            if not ok:
                return False, detail
            if given[3] != props or given[4] != tail:
                return False, '%s differs outside the walkable-graph buffer' % path
            graph = detail
            continue
        if tail != tail_a:
            return False, '%s (%s) differs after its properties' % (path, cls)
        if cls == geomedits.AUDIO_CLASS and parked(actor) and \
                set(keys) <= {'bAutoActivate[0]', 'Sound[0]'} and \
                db.get('bAutoActivate[0]', (None, None))[1] is False and \
                ('Sound[0]' not in keys or db['Sound[0]'][1] is None):
            counts['silenced'] += 1
            continue
        if cls == geomedits.MARKER_CLASS and parked(actor) and keys == ['bShowMarker[0]'] and \
                db['bShowMarker[0]'][1] is False:
            counts['markers'] += 1
            continue
        if actor > 0 and builder._root_component(actor) == index:
            name = levelbuild._name_of(b['pkg'], actor - 1)
            if keys == ['RelativeLocation[0]'] and _parked_at(db[keys[0]][1]) and \
                    not _parked_at((da.get(keys[0]) or (None, None))[1]) and has_moon(actor):
                moons.append(name)
                continue
            if keys == ['RelativeRotation[0]'] and name.startswith(spawnerfacing.ADDED_PREFIX):
                was = (da.get(keys[0]) or (None, (0.0, 0.0, 0.0)))[1]
                now = db[keys[0]][1]
                if facing is None:
                    facing = {r['name']: r for r in spawnerfacing.read_level(b_ua, b_ux)}
                rule = facing.get(name)
                if _close(was, (0.0, 0.0, 0.0), 1e-6) and rule and \
                        _close((now[0], now[2]), (0.0, 0.0), 1e-6) and \
                        spawnerfacing.off(now[1], rule['want']) <= FACING_SLACK:
                    turned.append('%s yaw 0 -> %s (rule %s)' % (name, round(now[1], 2),
                                                               rule['want']))
                    continue
        return False, '%s (%s) differs in %s' % (path, cls, ', '.join(keys) or 'its values')
    notes = []
    if counts['silenced'] or counts['markers'] or moons:
        notes.append('%s: %d sound(s) of parked actors silenced%s%s' % (
            PARKED_REASON, counts['silenced'],
            ', %d marker(s) off' % counts['markers'] if counts['markers'] else '',
            ', moon parked: %s' % ', '.join(moons) if moons else ''))
    if turned:
        notes.append('%s: %s' % (SPAWNERFACING_REASON, ', '.join(turned)))
    if graph:
        notes.append('%s: %s' % (NAVFAN_REASON, graph))
    return True, '; '.join(notes) or 'the same content'


def certify_level(a_ua, a_ux, b_ua, b_ux):
    """(ok, detail): byte_certify, else content_certify (the level read with FNames
    resolved)."""
    ok, detail = byte_certify(a_ua, a_ux, b_ua, b_ux)
    if ok:
        return ok, detail
    got, why = content_certify(a_ua, a_ux, b_ua, b_ux)
    if got:
        return True, why
    return False, '%s; by content: %s' % (detail, why)


def byte_certify(a_ua, a_ux, b_ua, b_ux):
    """(ok, detail): the new level is the old one with its added spawners facing their routes
    (spawnerfacing_apply), its walkable graph cleaned (navfan_certify), or both, and nothing
    else differing."""
    notes, ua, ux, refused = spawnerfacing_apply(a_ua, a_ux, b_ua, b_ux)
    if refused:
        return False, refused
    done = ['%s: %s' % (SPAWNERFACING_REASON, ', '.join(notes))] if notes else []
    if ua == b_ua and ux == b_ux:
        return True, '; '.join(done)
    ok, detail = navfan_certify(ua, ux, b_ua, b_ux)
    if not ok:
        return False, '; '.join(done + [detail])
    return True, '; '.join(done + ['%s: %s' % (NAVFAN_REASON, detail)])


def _level_pairs(a, b, done, expected, differ):
    """Certify each level and sublevel (.umap + .uexp) whose bytes differ, by certify_level:
    both entries go to `expected` when it holds, to `differ` with its reason when it does not.
    Names handled are added to `done`."""
    for umap in sorted(n for n in set(a) & set(b) if n.endswith('.umap')):
        uexp = umap[:-5] + '.uexp'
        if uexp not in a or uexp not in b or (a[umap] == b[umap] and a[uexp] == b[uexp]):
            continue
        ok, detail = certify_level(a[umap], a[uexp], b[umap], b[uexp])
        for name in (umap, uexp):
            if a[name] == b[name]:
                continue
            done.add(name)
            row = {'entry': name, 'sizes': [len(a[name]), len(b[name])]}
            if ok:
                expected.append(dict(row, reason=detail))
            else:
                x, y = a[name], b[name]
                at = next((i for i in range(min(len(x), len(y))) if x[i] != y[i]),
                          min(len(x), len(y)))
                differ.append(dict(row, first_diff=at, reason='not certified as a level: %s'
                                                              % detail))


def compare(a_pak, b_pak, kinds=(), bc_record=None):
    a, b = entries(a_pak), entries(b_pak)
    same, differ, fresh, expected = [], [], [], []
    recs = outs = None
    if bc_record and os.path.isdir(bc_record):
        import bccertify
        recs = bccertify.records(bc_record)
        outs = bccertify.outputs(recs) if recs else None
    levels = set()
    _level_pairs(a, b, levels, expected, differ)
    for name in sorted(set(a) & set(b)):
        if name in levels:
            continue
        if a[name] == b[name]:
            same.append(name)
        elif fresh_guid_only(name, a[name], b[name]):
            fresh.append(name)
        elif expected_reason(name, kinds):
            expected.append({'entry': name, 'reason': expected_reason(name, kinds),
                             'sizes': [len(a[name]), len(b[name])]})
        elif outs and _ispc(a[name], b[name], recs, outs, name, expected, differ):
            pass
        else:
            x, y = a[name], b[name]
            at = next((i for i in range(min(len(x), len(y))) if x[i] != y[i]), min(len(x), len(y)))
            differ.append({'entry': name, 'sizes': [len(x), len(y)], 'first_diff': at})
    return {'identical': len(same), 'different': differ, 'fresh_guid_only': fresh,
            'expected': expected,
            'only_old': sorted(set(a) - set(b)), 'only_new': sorted(set(b) - set(a)),
            'pak_sha256': [hashlib.sha256(open(p, 'rb').read()).hexdigest() for p in (a_pak, b_pak)]}


def pak_digest(paks):
    """16 hex digits over a build's paks ({name: path}), names and bytes."""
    h = hashlib.sha256()
    for name in sorted(paks):
        h.update(name.encode() + b'\0')
        with open(paks[name], 'rb') as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b''):
                h.update(chunk)
    return h.hexdigest()[:16]


def pin(record, run_folder, side):
    """Keep `side`'s paks of a comparison run folder as a pinned baseline; returns its
    digest (BASELINES takes it)."""
    with open(os.path.join(run_folder, 'report.json')) as fh:
        paks = json.load(fh)[side]['pak']
    digest = pak_digest(paks)
    folder = os.path.join(PINNED, record, digest)
    os.makedirs(folder, exist_ok=True)
    for name, path in paks.items():
        shutil.copy2(path, os.path.join(folder, name))
    with open(os.path.join(folder, 'pinned.json'), 'w') as fh:
        json.dump({'from': run_folder, 'side': side, 'at': time.strftime('%Y-%m-%d %H:%M')}, fh)
    return digest


def baseline_pak(record):
    """The mission pak of a record's pinned baseline (BASELINES), or None when it has none."""
    base = BASELINES.get(record) or {}
    if base.get('mode') != 'pinned':
        return None
    path = os.path.join(PINNED, record, base['digest'], record + '.pak')
    return path if os.path.isfile(path) else None


def pinned(record, digest):
    """{name: path} of a pinned baseline, checked against its digest; SystemExit when gone."""
    folder = os.path.join(PINNED, record, digest or 'none')
    paks = {f: os.path.join(folder, f) for f in sorted(os.listdir(folder))
            if f.endswith('.pak')} if os.path.isdir(folder) else {}
    if not paks or pak_digest(paks) != digest:
        raise SystemExit('the pinned baseline %s of %s is missing or changed (%s): pin it again '
                         'with --pin, or change BASELINES' % (digest, record, folder))
    return paks


def package_change(a_ua, a_ux, b_ua, b_ux):
    """Lines saying how a package changed: names added and removed, exports added, removed
    or resized (name, class, sizes)."""
    from riftlord.formats.ue.uheader import parse
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        heads = []
        for side, (ua, ux) in (('a', (a_ua, a_ux)), ('b', (b_ua, b_ux))):
            for ext, data in (('.uasset', ua), ('.uexp', ux)):
                with open(os.path.join(tmp, side + ext), 'wb') as fh:
                    fh.write(data or b'')
            heads.append(parse(os.path.join(tmp, side + '.uasset')))
    a, b = heads
    added = [n for n in b['names'] if n not in set(a['names'])]
    gone = [n for n in a['names'] if n not in set(b['names'])]
    if added or gone:
        out.append('names: added %s; removed %s' % (added[:8] or 'none', gone[:8] or 'none'))

    def key(e):
        return (e.get('name'), e.get('cls') or e.get('class'), e.get('outername'))
    old = {key(e): e for e in a['exports']}
    new = {key(e): e for e in b['exports']}
    for k in sorted(set(old) | set(new), key=str):
        if k not in new:
            out.append('export removed: %s (%s)' % (k[0], k[1]))
        elif k not in old:
            out.append('export added: %s (%s), %d bytes' % (k[0], k[1], new[k]['size']))
        elif old[k]['size'] != new[k]['size']:
            out.append('export %s (%s): %d -> %d bytes' % (k[0], k[1], old[k]['size'],
                                                          new[k]['size']))
    return out or ['same names and export sizes: bytes inside an export differ']


def repin(argv):
    """`--repin`: compare RECORD with its pin, list what differs, pin the new build when every
    differing entry is expected (or --accept). Returns the exit code."""
    ap = argparse.ArgumentParser(prog='buildcompare.py RECORD --repin')
    ap.add_argument('record')
    ap.add_argument('--repin', action='store_true')
    ap.add_argument('--expect', default='', help='comma list of entry globs that may differ')
    ap.add_argument('--why', default='', help='what changed, for the BASELINES entry')
    ap.add_argument('--accept', action='store_true',
                    help='pin even when entries outside --expect differ')
    ap.add_argument('--with', dest='extra', action='append', default=None)
    ap.add_argument('--out')
    args = ap.parse_args(argv)
    base = BASELINES.get(args.record)
    if not base or base['mode'] != 'pinned':
        raise SystemExit('%s has no pinned baseline in BASELINES' % args.record)
    if args.extra is None:
        import gate
        args.extra = list(gate.RECORDS.get(args.record, []))
    work = args.out or os.path.join(SCRATCH, 'repin-%s-%s' % (args.record,
                                                             time.strftime('%Y-%m-%d-%H%M%S')))
    code = main([args.record, '--out', work] + sum((['--with', w] for w in args.extra), []))
    with open(os.path.join(work, 'report.json')) as fh:
        report = json.load(fh)
    if not (report.get('new') or {}).get('pak'):
        print('REFUSED: the new build failed (%s)' % (report.get('new') or {}).get('log'))
        return 1
    if code == 0:
        print('%s already matches its pin %s: nothing to do' % (args.record, base['digest']))
        return 0
    globs = [g.strip() for g in args.expect.split(',') if g.strip()]
    old_paks, new_paks = report['old']['pak'], report['new']['pak']
    outside = []
    for name in sorted(set(old_paks) | set(new_paks)):
        if name not in old_paks or name not in new_paks:
            print('%s: only in the %s build' % (name, 'new' if name in new_paks else 'pinned'))
            outside.append(name)
            continue
        c = report['compare'][name]
        a, b = entries(old_paks[name]), entries(new_paks[name])
        rows = [(d['entry'], 'differs') for d in c['different']] + \
            [(e, 'only in the pin') for e in c['only_old']] + \
            [(e, 'only in the new build') for e in c['only_new']] + \
            [(d['entry'], 'differs (%s)' % d['reason']) for d in c['expected']]
        for entry, how in rows:
            allowed = any(fnmatch.fnmatchcase(entry, g) for g in globs) or \
                how.startswith('differs (')
            print('%s %s: %s' % ('  ' if allowed else '!!', entry, how))
            stem, ext = os.path.splitext(entry)
            if ext in ('.uasset', '.umap') and entry in a and entry in b:
                for line in package_change(a[entry], a.get(stem + '.uexp'), b[entry],
                                           b.get(stem + '.uexp')):
                    print('      ' + line)
            if not allowed:
                outside.append(entry)
    if outside and not args.accept:
        print('REFUSED: %d entr%s outside --expect %s differ%s; check them, then widen --expect '
              'or pass --accept' % (len(outside), 'y' if len(outside) == 1 else 'ies',
                                    globs or '(none)', 's' if len(outside) == 1 else ''))
        return 1
    digest = pin(args.record, work, 'new')
    what = "the new code's build of %s%s" % (time.strftime('%Y-%m-%d %H:%M'),
                                            ' (%s)' % args.why if args.why else '')
    path = PINS_FILE
    pins = load_pins(path)
    if (pins.get(args.record) or {}).get('mode') != 'pinned':
        print('pinned %s as %s, but its BASELINES entry was not found to rewrite: set '
              "'digest': '%s' in %s by hand" % (args.record, digest, digest, path))
        return 1
    pins[args.record] = dict(pins[args.record], digest=digest, what=what)
    with open(path + '.part', 'w') as fh:
        json.dump(pins, fh, indent=2)
        fh.write('\n')
    os.replace(path + '.part', path)
    BASELINES[args.record] = pins[args.record]
    print('PINNED %s: %s (was %s); BASELINES updated in %s' % (args.record, digest,
                                                               base['digest'], path))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('record')
    ap.add_argument('--old', default='snapshot')
    ap.add_argument('--new', default=NEW)
    ap.add_argument('--out')
    ap.add_argument('--with', dest='extra', action='append', default=[],
                    help='also copy this Missions item (a custom campaign) in')
    ap.add_argument('--fresh', action='store_true', help='build both sides, ignoring the cache')
    ap.add_argument('--kinds', action='store_true',
                    help='print RECORD (comma list) -> its EXPECTED kinds as JSON, and stop')
    ap.add_argument('--pin', action='store_true',
                    help="keep RECORD's build from --from (a run folder) as a pinned baseline")
    ap.add_argument('--from', dest='from_run', help='--pin: the comparison run folder')
    ap.add_argument('--side', choices=('old', 'new'), default='old', help='--pin: which build')
    args = ap.parse_args(argv)
    if args.pin:
        print(pin(args.record, args.from_run, args.side))
        return 0
    if args.kinds:
        print(json.dumps({r: record_kinds(r) for r in args.record.split(',')}))
        return 0
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    work = args.out or os.path.join(SCRATCH, time.strftime('%Y-%m-%d-%H%M%S'))
    os.makedirs(work, exist_ok=True)
    old = snapshot(work) if args.old == 'snapshot' else os.path.abspath(args.old)
    print('run folder %s\nold code %s\nnew code %s' % (work, old, args.new), flush=True)
    kinds = record_kinds(args.record)
    report = {'record': args.record, 'old': old, 'new': args.new, 'kinds': kinds}
    paks, records = {}, {}
    base = BASELINES.get(args.record) if args.old == 'snapshot' and not args.fresh else None
    report['baseline'] = base
    if base and base['mode'] == 'pinned':
        paks['old'] = pinned(args.record, base['digest'])
        report['old'] = {'pak': paks['old'], 'pinned': base['digest']}
        print('old build: %s (pinned %s: %s)' % (', '.join(sorted(paks['old'])), base['digest'],
                                                base['what']), flush=True)
    sides = (('new', args.new),) if base else (('old', old), ('new', args.new))
    for label, tree in sides:
        cacheable = not args.fresh and (args.old == 'snapshot' if label == 'old' else
                                        os.path.abspath(args.new) == NEW)
        key = inputs_digest(label, tree, args.record, args.extra) if cacheable else None
        pak = cached(label, args.record, key) if key else None
        record = None
        if pak:
            record = os.path.join(os.path.dirname(next(iter(pak.values()))), BC_RECORD)
            report[label] = {'pak': pak, 'cached': key}
            print('%s build: %s (cached %s)' % (label, ', '.join(sorted(pak)), key), flush=True)
        else:
            if label == 'old':
                # the old code bakes without a GPU lock of its own: the whole build holds it
                from riftlord.core import gpulock
                with gpulock.turn('old-code build %s' % args.record):
                    pak, log, secs, inputs = build(tree, args.record, work, label, args.extra)
            else:
                pak, log, secs, inputs = build(tree, args.record, work, label, args.extra)
            record = os.path.join(work, label, BC_RECORD)
            report[label] = {'pak': pak, 'log': log, 'seconds': round(secs, 1)}
            print('%s build: %s in %.0f s' % (label, ', '.join(sorted(pak)) if pak else
                                              'FAILED, see ' + log, secs), flush=True)
            if label == 'new':
                bakes = bake_lines(log)
                report[label]['bake'] = bakes
                for line in bakes:
                    print('  %s' % line, flush=True)
                from riftlord.build.lighting import lightbake
                lightbake.prune(BAKES, BAKES_KEEP)
            if pak and key and inputs is not None:
                store(label, args.record, key, pak, inputs, record)
                report[label]['inputs'] = len(inputs)
        paks[label] = pak
        records[label] = record
    ok = False
    if base and base['mode'] == 'none':
        ok = bool(paks.get('new'))
        print('no byte comparison for %s: %s; the new build %s' % (
            args.record, base['why'], 'built' if ok else 'FAILED'), flush=True)
    elif paks['old'] and paks['new']:
        ok = sorted(paks['old']) == sorted(paks['new'])
        report['compare'] = {}
        for name in sorted(set(paks['old']) & set(paks['new'])):
            c = compare(paks['old'][name], paks['new'][name], kinds, records.get('new'))
            report['compare'][name] = c
            ok = ok and not (c['different'] or c['only_old'] or c['only_new'])
            print('%s: entries identical %d, only a per-build Guid %d, expected to differ %d, '
                  'different %d, only old %d, only new %d; pak sha256 %s'
                  % (name, c['identical'], len(c['fresh_guid_only']), len(c['expected']),
                     len(c['different']), len(c['only_old']), len(c['only_new']),
                     'same' if c['pak_sha256'][0] == c['pak_sha256'][1] else 'different'))
            for d in c['expected']:
                print('  expected to differ: %s (%s)' % (d['entry'], d['reason']))
            for d in c['different'][:20]:
                print('  differs: %s sizes %s first byte %d' % (d['entry'], d['sizes'],
                                                                d['first_diff']))
    report['ok'] = ok
    with open(os.path.join(work, 'report.json'), 'w') as fh:
        json.dump(report, fh, indent=1)
    return 0 if ok else 1


def limited_main(argv=None):
    """main() in a heavy-job slot when run on its own (a gate's comparisons run in the
    gate's); its old-code builds take the GPU lock, the new code's bakes take their own."""
    argv = sys.argv[1:] if argv is None else argv
    if '--pin' in argv or '--kinds' in argv:          # no build: no slot
        return main(argv)
    if '--repin' in argv:
        sys.path.insert(0, NEW)
        from riftlord.core import heavyslot
        with heavyslot.slot('buildcompare repin'):
            return repin(argv)
    sys.path.insert(0, NEW)
    from riftlord.core import heavyslot
    with heavyslot.slot('buildcompare'):
        return main(argv)


if __name__ == '__main__':
    raise SystemExit(limited_main())
