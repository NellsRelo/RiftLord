"""scratch.frame_volume_actors on a level whose map volume brush stores no RelativeScale3D
(Scenario_04: the cooker leaves a property at its default out): the brush is framed with
the default filled in, its transform reads back as written, and framing the result again
reads the stored scale.

    python3 tests/offline/test_mapvolume_defaults.py
"""
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from riftlord.build.minimap import mapdata  # noqa: E402
from riftlord.build.navigation import navpatch  # noqa: E402
from riftlord.formats.ue.uassetio import Package  # noqa: E402
from riftlord.mapeditor import scratch  # noqa: E402

LEVEL = 'Scenario_04'
checks = []


def check(name, ok, detail=''):
    checks.append(ok)
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, '' if ok else ': %s' % (detail,)))


def brushes(ua, ux):
    """[{field: (x, y, z)}] of each OMDMapVolume's brush transform as stored."""
    pkg = Package(ua, ux)
    pkg.load_payloads()
    names = [n for n, _h in pkg.names]
    out = []
    for e in pkg.exports:
        cls = struct.unpack_from('<i', e['meta'], 0)[0]
        if cls >= 0 or names[struct.unpack_from('<i', pkg.imports[-cls - 1], 20)[0]] != \
                scratch.MAP_VOLUME_CLASS:
            continue
        root = [struct.unpack_from('<i', e['data'], at)[0]
                for nm, _k, _s, at, _z in scratch._tags(e['data'], names, 0, len(e['data']))
                if nm == 'RootComponent'][0]
        c = pkg.exports[root - 1]['data']
        f = {nm: at for nm, _k, _s, at, _z in scratch._tags(c, names, 0, len(c))}
        out.append({k: struct.unpack_from('<3f', c, f[k]) if k in f else None
                    for k in scratch.BRUSH_DEFAULTS})
    return out


got = navpatch.fetch_map(navpatch.maps()[LEVEL], tempfile.mkdtemp(prefix='mapvolume-'))
ua, ux = (open(got[ext], 'rb').read() for ext in ('.umap', '.uexp'))
before = brushes(ua, ux)
check('the shipped brush stores no RelativeScale3D', before and before[0]['RelativeScale3D'] is None,
      before)

host = mapdata.read(LEVEL)['volumes'][0]
want = dict(host, extent=tuple(e * 1.1 for e in host['extent']))
a, x, n = scratch.frame_volume_actors(ua, ux, LEVEL, want)
after = brushes(a, x)
check('framed with the default filled in', n == 1 and after[0]['RelativeScale3D'] is not None, after)
check('the scale written is the frame over the brush (1.1)',
      all(abs(s - 1.1) < 1e-4 for s in after[0]['RelativeScale3D']), after)
check('the location written is the frame\'s',
      all(abs(p - q) < 1e-3 for p, q in zip(after[0]['RelativeLocation'], want['translation'])), after)
a2, x2, n2 = scratch.frame_volume_actors(a, x, LEVEL, want)
check('framing the framed level again finds its stored scale', n2 == 1, n2)

print('%d of %d passed' % (sum(checks), len(checks)))
sys.exit(0 if all(checks) else 1)
