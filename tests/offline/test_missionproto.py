"""Exhaustive checks on the mission-proto writer."""
import sys, os, struct, subprocess, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from riftlord.build import missionproto
from riftlord.formats.ue import pakv4
from riftlord.formats.ue.uassetio import Package, fname_hashes

OUT = tempfile.mkdtemp()
checks = []
def check(name, ok, detail=''):
    checks.append((name, ok, detail))

# 1. both tables round-trip unchanged
for tbl, path in (('MissionProtos', missionproto.TABLE),
                  ('CampaignProtos', missionproto.CAMPAIGN_TABLE)):
    t = missionproto.read(path)
    check('%s round-trips byte-identical' % tbl,
          t.serialise() == t.export['data'], '%d rows' % len(t.rows))

# 2. a full registration
r = subprocess.run([sys.executable, '-m', 'riftlord.build.missionproto', '--clone', 'Scenario_01',
                    '--row', 'Scenario_TestA', '--name', 'Test A',
                    '--level', '/Game/Maps/Custom/TestA/TestA', '--par', '480',
                    '--campaign', 'OMD3_Main_Story', '--out', OUT],
                   capture_output=True, text=True)
check('clone command succeeds', r.returncode == 0, r.stderr.strip()[-120:])

def load(name):
    p = os.path.join(OUT, name)
    pkg = Package(open(p + '.uasset', 'rb').read(), open(p + '.uexp', 'rb').read())
    pkg.load_payloads()
    return missionproto.Table(pkg)

m, c = load('MissionProtos'), load('CampaignProtos')
orig_m, orig_c = missionproto.read(), missionproto.read(missionproto.CAMPAIGN_TABLE)

check('mission row added', len(m.rows) == len(orig_m.rows) + 1,
      '%d -> %d' % (len(orig_m.rows), len(m.rows)))
check('every original mission row untouched',
      all([f.bytes() for f in a['fields']] == [f.bytes() for f in b['fields']]
          and a['name'] == b['name'] for a, b in zip(orig_m.rows, m.rows)))
check('new row has all 25 fields', len(m.row('Scenario_TestA')['fields']) == 25)

new_guid = bytes(m.field(m.row('Scenario_TestA'), 'Guid').payload)
guids = [bytes(m.field(r, 'Guid').payload) for r in m.rows]
check('guid is unique in the table', len(set(guids)) == len(guids))
check('guid is not the template\'s',
      new_guid != bytes(m.field(m.row('Scenario_01'), 'Guid').payload))

crow = c.row('OMD3_Main_Story')
cnt, _tag, items = c.struct_array(c.field(crow, 'MissionList'))
ocnt, _t2, oitems = orig_c.struct_array(orig_c.field(orig_c.row('OMD3_Main_Story'), 'MissionList'))
check('campaign gained one mission', cnt == ocnt + 1, '%d -> %d' % (ocnt, cnt))
check('existing mission list entries untouched', items[:ocnt] == oitems)
check('the new entry carries the new guid', items[-1][-24:-8] == new_guid)
check('every other campaign row untouched',
      all([f.bytes() for f in a['fields']] == [f.bytes() for f in b['fields']]
          for a, b in zip(orig_c.rows, c.rows) if a['name'] != 'OMD3_Main_Story'))

# 3. name table integrity
bad = [s for s, h in m.pkg.names if fname_hashes(s) != h]
check('every name hash in the written table is correct', not bad, str(bad[:2]))
check('the new asset path is in the name table',
      '/Game/Maps/Custom/TestA/TestA.TestA' in [s for s, _h in m.pkg.names])

# 4. packaging round trip
pak = os.path.join(OUT, 'test.pak')
files = {}
for n in ('MissionProtos', 'CampaignProtos'):
    for ext in ('.uasset', '.uexp'):
        files['OMD/Content/Data/Campaign/%s%s' % (n, ext)] = \
            open(os.path.join(OUT, n + ext), 'rb').read()
pakv4.write(pak, files)
_mount, _ver, recs = pakv4.read(pak)
raw = open(pak, 'rb').read()
got = {n: raw[o + 53:o + 53 + sz] for n, o, sz in recs}
check('pak stores all four files', len(recs) == 4)
check('pak bytes match what was written',
      all(got[k] == v for k, v in files.items()))
mp = Package(got['OMD/Content/Data/Campaign/MissionProtos.uasset'],
             got['OMD/Content/Data/Campaign/MissionProtos.uexp'])
mp.load_payloads()
check('table re-reads straight out of the pak',
      missionproto.Table(mp).row('Scenario_TestA') is not None)

# 5. refuses a duplicate row
r2 = subprocess.run([sys.executable, '-m', 'riftlord.build.missionproto', '--clone', 'Scenario_01',
                     '--row', 'Scenario_01'], capture_output=True, text=True)
check('refuses to clone onto an existing row name', r2.returncode != 0)
r3 = subprocess.run([sys.executable, '-m', 'riftlord.build.missionproto', '--clone', 'NoSuchRow',
                     '--row', 'X'], capture_output=True, text=True)
check('refuses an unknown template', r3.returncode != 0)

# 6. the read-back checker: the shipped files must pass it, and the three
#    mistakes that once shipped must fail it. A checker that only ever says
#    "clean" is worth nothing, so each one is broken on purpose here.
from riftlord.core import pkgcheck
from riftlord.build.minimap import mapdata

for path, kind in ((missionproto.CAMPAIGN_TABLE, 'table'),
                   (missionproto.TABLE, 'table'),
                   ('/Game/Data/EntitlementsProtos', 'table'),
                   ('/Game/Game/BP_OMDProtoManager', 'object')):
    f = mapdata._fetch(path)
    ua = open(f['.uasset'], 'rb').read()
    ux = open(f['.uexp'], 'rb').read()
    bad = pkgcheck.problems(ua, ux, path, kind)
    check('shipped %s reads back clean' % path.rsplit('/', 1)[-1],
          not bad, bad[0] if bad else '')

f = mapdata._fetch(missionproto.CAMPAIGN_TABLE)
VA = open(f['.uasset'], 'rb').read()
VX = open(f['.uexp'], 'rb').read()

def broken(mutate_uasset=None, mutate_uexp=None):
    a, b = bytearray(VA), bytearray(VX)
    if mutate_uasset:
        mutate_uasset(a)
    if mutate_uexp:
        mutate_uexp(b)
    return pkgcheck.problems(bytes(a), bytes(b), 'test', 'table')

def stale_element_tag(b):
    """What a grown array looked like before _pack_array maintained it."""
    at = bytes(b).find(struct.pack('<i', 1314))
    struct.pack_into('<i', b, at, 1314 - 73)

check('catches an element tag left stale', bool(broken(mutate_uexp=stale_element_tag)))
check('catches an FName from another package',
      bool(broken(mutate_uexp=lambda b: struct.pack_into('<i', b, 53, 9999))))
check('catches a header that disagrees with the file',
      bool(broken(mutate_uasset=lambda a: struct.pack_into('<i', a, 24, len(VA) + 1))))
check('catches a truncated .uexp', bool(broken(mutate_uexp=lambda b: b.pop())))

# 7. a row lifted out of another package keeps every nested name it had
from riftlord.build import reconcile
rep = reconcile.scan()
lifts = [m for m in rep['mods'] if m['defines_shipped']]
if lifts:
    src = reconcile.Source(os.path.join(rep['mods_dir'], lifts[0]['pak']))
    other = src.table(missionproto.TABLE)
    mine = missionproto.read(missionproto.TABLE)
    have = {r['name'] for r in mine.rows}
    fresh = next((r for r in other.rows if r['name'] not in have), None)
    if fresh is not None:
        got = mine.adopt(other, fresh)
        before = reconcile.fingerprint(other, fresh)
        after = reconcile.fingerprint(mine, got)
        differ = [k for k in before if before[k] != after.get(k)]
        check('a lifted row keeps every nested value',
              not differ, '%s: %s' % (fresh['name'], ', '.join(differ[:3])))
        ua, ux = mine.write()
        bad = pkgcheck.problems(ua, ux, 'lifted', 'table')
        check('the table with a lifted row reads back clean',
              not bad, bad[0] if bad else '')

# 8. merging twice must work: Weaver's own output is not a mod
check("the scan ignores Weaver's own pak",
      reconcile.DEFAULT_PAK not in [m['pak'] for m in rep['mods']],
      ', '.join(m['pak'] for m in rep['mods']))
import shutil
probe = os.path.join(OUT, 'mods')
os.makedirs(probe, exist_ok=True)
real = os.path.join(rep['mods_dir'], reconcile.DEFAULT_PAK)
if not os.path.exists(real):
    merged = reconcile.merge(rep)
    reconcile.install(merged, out=os.path.join(probe, reconcile.DEFAULT_PAK))
else:
    shutil.copy(real, os.path.join(probe, reconcile.DEFAULT_PAK))
for m in rep['mods']:
    shutil.copy(os.path.join(rep['mods_dir'], m['pak']),
                os.path.join(probe, m['pak']))
again = reconcile.scan(mods_dir=probe)
second = reconcile.merge(again)
check('merging a folder that already holds a merge is clean',
      not second['conflicts'],
      '%d mod(s), %d conflict(s)' % (len(again['mods']), len(second['conflicts'])))

print()
for name, ok, detail in checks:
    print('  %-3s %-52s %s' % ('OK' if ok else 'FAIL', name, detail))
print()
print('%d of %d checks passed' % (sum(1 for _n, o, _d in checks if o), len(checks)))
