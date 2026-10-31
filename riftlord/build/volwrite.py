"""Can we WRITE a NavPower flight graph? Build the paks that settle it.

The checksum is cracked (``navvol.checksum``, verified on all 43 OMD3 buffers) and it
is the gate the game actually enforces. So the question left is whether a buffer we
edited and resealed is accepted in play.

Three paks, and the pair is the point:

    good    the world bbox expanded, checksum RESEALED      expect normal flight
    stale   the same edit, checksum left STALE              expect NO flight
    grow    every flight volume enlarged 200uu, resealed    expect normal flight

``stale`` is the control that makes ``good`` mean something. Without it, flight working
in ``good`` could just mean the edit was ignored; with it, ``good`` working and
``stale`` failing proves the edit reached the loader and the checksum carried it.

``grow`` is the real demonstration: enlarging a box means editing six plane equations
*and* the stored bounding radius that is redundant with them, so it only survives if
the record layout is understood rather than guessed.

    python3 volwrite.py --map Scenario_Ice_01 --build
    python3 volwrite.py --map Scenario_Ice_01 --install good

Install ONE at a time; --install removes the others. Judge by whether flyers travel
their chain, never by whether they attack something beside them.
"""

import argparse
import math
import os
import shutil
import struct
import sys
from riftlord.core import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.build.navigation import navpatch  # noqa: E402
from riftlord.build.navigation import navvol  # noqa: E402
from riftlord.formats.ue import pakv4  # noqa: E402

OUT = gamepath.beside_code(gamepath.CODE_ROOT, 'patched/volwrite')
GROW = 200.0
SHRINK = 300.0
DISPLACE = 20000.0

CASES = {
    'control': ('719-VolWriteControl_P.pak',
                'repacked unchanged -- proves the pipeline on THIS map'),
    'good': ('716-VolWriteGood_P.pak',
             'bbox expanded 100uu, checksum resealed -- expect NORMAL flight'),
    'stale': ('717-VolWriteStale_P.pak',
              'the same edit, checksum left stale -- expect NO flight'),
    'displace': ('721-VolWriteDisplace_P.pak',
                 'the WHOLE graph moved %guu in z, so it covers nothing near the '
                 'route -- does the graph need to contain the route at all?' % DISPLACE),
    'unportal': ('722-VolWriteUnportal_P.pak',
                 'ONE portal shifted off its junction, volumes untouched -- are the '
                 'portals the edges?'),
    'uncover': ('723-VolWriteUncover_P.pak',
                'a volume shrunk in z ONLY until a route point falls outside it, '
                'portals still touching -- does point coverage matter?'),
    'disconnect': ('720-VolWriteDisconnect_P.pak',
                   'the connector volume shrunk %guu so the volumes no longer touch, '
                   'while every path point stays covered -- expect flyers to STOP '
                   'partway' % SHRINK),
    'grow': ('718-VolWriteGrow_P.pak',
             'every volume enlarged %guu, resealed -- expect NORMAL flight' % GROW),
}


def expand_bbox(buf, by=100.0):
    """Push the graph's world bbox out. Behaviourally neutral: a looser bound."""
    out = bytearray(buf)
    base = navvol.CONTAINER + navvol.OFF_BBOX
    lo = list(struct.unpack_from('<3f', out, base))
    hi = list(struct.unpack_from('<3f', out, base + 12))
    struct.pack_into('<3f', out, base, *[v - by for v in lo])
    struct.pack_into('<3f', out, base + 12, *[v + by for v in hi])
    return bytes(out)


def grow_volumes(buf, by=GROW, only=None, axes=(0, 1, 2)):
    """Resize decoded volumes by `by` on all sides, radius kept consistent.

    Negative `by` shrinks. `only` limits it to one volume index.


    For a plane ``n . p == d`` with a unit axis normal, moving that face outward by t
    is ``d += t`` whichever way the normal points: a +x face has ``x == d`` and a -x
    face has ``x == -d``, so both grow as d grows. The stored bounding radius is
    redundant with the planes, so it has to be recomputed or the record contradicts
    itself -- and that redundancy is exactly what validated the decode.
    """
    out = bytearray(buf)
    body_at = navvol.CONTAINER
    parsed = navvol.parse(bytes(buf))
    touched = 0
    for i, v in enumerate(parsed['volumes']):
        if not v['box'] or (only is not None and i != only):
            continue
        planes_at = body_at + v['at'] + 8
        for j, pl in enumerate(v['planes']):
            ax = max(range(3), key=lambda k: abs(pl[k]))
            if ax not in axes:
                continue
            d_at = planes_at + j * 16 + 12
            d = struct.unpack_from('<f', out, d_at)[0]
            struct.pack_into('<f', out, d_at, d + by)
        half = [(v['box'][1][k] - v['box'][0][k]) / 2.0 + (by if k in axes else 0.0)
                for k in range(3)]
        rad_at = body_at + v['at'] - 4
        struct.pack_into('<f', out, rad_at,
                         math.sqrt(sum(h * h for h in half)))
        touched += 1
    if not touched:
        raise SystemExit('no volumes decoded, so there is nothing to grow')
    return bytes(out), touched


def displace(buf, delta):
    """Translate the whole graph: bbox, every volume and every portal.

    For a plane ``n . p == d``, translating the volume by delta gives
    ``n . (p + delta) == d + n . delta``, so the offset added to d is the DOT
    PRODUCT, not the raw delta. Getting that wrong shears the box instead of moving
    it, and the stored centre and radius would then disagree with the planes -- which
    is what the readback checks catch.
    """
    out = bytearray(buf)
    base = navvol.CONTAINER + navvol.OFF_BBOX
    for off in (base, base + 12):
        v = list(struct.unpack_from('<3f', out, off))
        struct.pack_into('<3f', out, off, *[v[k] + delta[k] for k in range(3)])
    parsed = navvol.parse(bytes(buf))
    for v in parsed['volumes']:
        at = navvol.CONTAINER + v['at']
        c = list(struct.unpack_from('<3f', out, at - 16))
        struct.pack_into('<3f', out, at - 16, *[c[k] + delta[k] for k in range(3)])
        for j, pl in enumerate(v['planes']):
            d_at = at + 8 + j * 16 + 12
            dot = sum(pl[k] * delta[k] for k in range(3))
            struct.pack_into('<f', out, d_at,
                             struct.unpack_from('<f', out, d_at)[0] + dot)
    for po in parsed['portals']:
        at = navvol.CONTAINER + po['at'] + 4
        for j in range(len(po['verts'])):
            v = list(struct.unpack_from('<3f', out, at + j * 12))
            struct.pack_into('<3f', out, at + j * 12,
                             *[v[k] + delta[k] for k in range(3)])
    return bytes(out)


def move_portal(buf, index, delta):
    """Shift ONE portal off its junction, leaving the volumes alone.

    In a portal graph the portals are the edges, so this is the test the shrink
    test should have been: it moves the link rather than the region.
    """
    out = bytearray(buf)
    parsed = navvol.parse(bytes(buf))
    if index >= len(parsed['portals']):
        raise SystemExit('no portal %d' % index)
    po = parsed['portals'][index]
    at = navvol.CONTAINER + po['at'] + 4
    for j in range(len(po['verts'])):
        v = list(struct.unpack_from('<3f', out, at + j * 12))
        struct.pack_into('<3f', out, at + j * 12,
                         *[v[k] + delta[k] for k in range(3)])
    return bytes(out), po


def build_one(map_name, case, out_dir):
    work = os.path.join(out_dir, map_name, case)
    if os.path.isdir(work):
        shutil.rmtree(work)
    os.makedirs(work)
    game_path = navpatch.map_path(map_name)
    files = navpatch.fetch_map(game_path, work)
    found = navpatch.nav_buffers(files['.umap'], files['.uexp'])
    if not found or not found[4]:
        raise SystemExit('%s ships no volumetric buffer' % map_name)
    _e, _so, _ss, vol_off, vol_size = found

    with open(files['.uexp'], 'rb') as fh:
        uexp = bytearray(fh.read())
    original = bytes(uexp[vol_off:vol_off + vol_size])
    assert navvol.verify(original), 'the shipped buffer fails its checksum'

    note = ''
    if case == 'control':
        # Nothing is edited. Without this, a failure on a map we have not exercised
        # cannot be told apart from the pak pipeline failing for that map.
        checks = [
            ('buffer untouched', True),
            ('checksum valid', navvol.verify(original)),
        ]
        return {'files': files, 'game_path': game_path, 'checks': checks,
                'note': 'nothing edited',
                'hash_before': struct.unpack_from('<I', original, 12)[0],
                'hash_after': struct.unpack_from('<I', original, 12)[0]}
    if case == 'displace':
        edited = navvol.reseal(displace(original, (0.0, 0.0, DISPLACE)))
        note = 'whole graph translated %guu in z' % DISPLACE
    elif case == 'unportal':
        #: Slide the portal along the chain into a volume's interior, NOT out of the
        #: world. Pushing it 20000uu away would put it outside the graph bbox, so a
        #: failure could be out-of-bounds rejection rather than a broken edge -- and
        #: the point here is to break the edge and nothing else.
        p0 = navvol.parse(original)
        if not p0['portals']:
            raise SystemExit('no portals to move')
        po = p0['portals'][0]
        y = po['verts'][0][1]
        inner = None
        for v in p0['volumes']:
            if v['box'] and v['box'][0][1] < y <= v['box'][1][1]:
                inner = v['box']
                break
        if inner is None:
            raise SystemExit('could not find the volume behind portal 0')
        # A quarter of the way back into that volume: clear of the junction, well
        # inside the bbox.
        shift = -max(400.0, (inner[1][1] - inner[0][1]) * 0.25)
        edited, _po = move_portal(original, 0, (0.0, shift, 0.0))
        edited = navvol.reseal(edited)
        note = ('portal 0 slid %+guu in y, from the junction at y=%g to y=%g '
                '(inside a volume, volumes untouched)' % (shift, y, y + shift))
    elif case == 'uncover':
        #: Shrink in z only, by just enough to put a route point outside. All axes at
        #: once would invert the narrow x extent, and shrinking in y would move the
        #: faces the portals sit on -- which is the confound that spoiled 'disconnect'.
        p0 = navvol.parse(original)
        target = None
        for i, v in enumerate(p0['volumes']):
            if v['box'] and v['flags'] == 2:
                target = (i, v)
                break
        if not target:
            raise SystemExit('no interior volume to uncover')
        idx, v = target
        zlo, zhi = v['box'][0][2], v['box'][1][2]
        drop = (zhi - zlo) * 0.45
        edited, _n = grow_volumes(original, -drop, only=idx, axes=(2,))
        edited = navvol.reseal(edited)
        note = ('volume %d z %g..%g -> %g..%g (portals untouched)'
                % (idx, zlo, zhi, zlo + drop, zhi - drop))
    elif case == 'disconnect':
        #: Shrink the narrowest volume that has two neighbours. That breaks the chain
        #: geometrically while leaving every path point inside a volume AND leaving the
        #: stored degree words still claiming two neighbours -- so the result separates
        #: "the runtime derives connectivity from the planes" from "it trusts the
        #: stored topology or the trailing index".
        p0 = navvol.parse(original)
        cand = [(i, v) for i, v in enumerate(p0['volumes'])
                if v['box'] and v['flags'] == 2]
        if not cand:
            raise SystemExit('no volume with two neighbours to disconnect')
        cand.sort(key=lambda t: min(t[1]['box'][1][k] - t[1]['box'][0][k]
                                    for k in range(3)))
        idx = cand[0][0]
        box = cand[0][1]['box']
        if min(box[1][k] - box[0][k] for k in range(3)) <= 2 * SHRINK:
            raise SystemExit('volume %d is too small to shrink %guu safely'
                             % (idx, SHRINK))
        edited, n = grow_volumes(original, -SHRINK, only=idx)
        edited = navvol.reseal(edited)
        note = ('volume %d shrunk %guu: y %g..%g -> %g..%g'
                % (idx, SHRINK, box[0][1], box[1][1],
                   box[0][1] + SHRINK, box[1][1] - SHRINK))
    elif case == 'grow':
        edited, n = grow_volumes(original)
        edited = navvol.reseal(edited)
        note = '%d volume(s) enlarged by %guu' % (n, GROW)
    else:
        edited = expand_bbox(original)
        if case == 'good':
            edited = navvol.reseal(edited)
        note = 'bbox expanded 100uu'

    assert len(edited) == len(original), 'buffer length must not change'
    uexp[vol_off:vol_off + vol_size] = edited
    with open(files['.uexp'], 'wb') as fh:
        fh.write(uexp)

    #: Readback, because the writer's own reader is blind to its own damage.
    with open(files['.uexp'], 'rb') as fh:
        back = fh.read()
    got = back[vol_off:vol_off + vol_size]
    checks = [
        ('package length unchanged', len(back) == len(uexp)),
        ('buffer read back as written', got == edited),
        ('payload still describes itself',
         struct.unpack_from('<I', got, 8)[0] + 24 == vol_size),
        ('flags still 0x01000001',
         struct.unpack_from('<I', got, 20)[0] == 0x01000001),
        ('the payload really changed', got[24:] != original[24:]),
        ('checksum %s' % ('valid' if case != 'stale' else 'STALE as intended'),
         navvol.verify(got) == (case != 'stale')),
    ]
    if case in ('grow', 'disconnect', 'displace', 'uncover', 'unportal'):
        p = navvol.parse(got)
        checks.append(('every grown volume still self-consistent',
                       bool(p['volumes']) and all(v['centre_ok'] and v['radius_ok']
                                                  for v in p['volumes'])))
        checks.append(('volume count unchanged', p['complete']))
    return {'files': files, 'game_path': game_path, 'checks': checks, 'note': note,
            'hash_before': struct.unpack_from('<I', original, 12)[0],
            'hash_after': struct.unpack_from('<I', got, 12)[0]}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--map', default='Scenario_Ice_01')
    ap.add_argument('--build', action='store_true')
    ap.add_argument('--install', choices=sorted(CASES))
    a = ap.parse_args()
    if not a.build and not a.install:
        ap.error('need --build or --install')

    mods = os.path.join(navpatch.PAK_DIR, '~mods')
    for case in (sorted(CASES) if a.build else [a.install]):
        name, why = CASES[case]
        rep = build_one(a.map, case, OUT)
        inner = rep['game_path'].replace('/Game/', 'OMD/Content/')
        dest_dir = OUT if a.build else mods
        os.makedirs(dest_dir, exist_ok=True)
        info = pakv4.write(os.path.join(dest_dir, name), {
            inner + '.umap': rep['files']['.umap'],
            inner + '.uexp': rep['files']['.uexp']})
        pakv4.read(info['pak'])
        bad = [lbl for lbl, ok in rep['checks'] if not ok]
        print('%-6s %s' % (case, why))
        print('       %s; checksum %#010x -> %#010x'
              % (rep['note'], rep['hash_before'], rep['hash_after']))
        print('       readback %d checks, %s'
              % (len(rep['checks']), 'all pass' if not bad else 'FAILED: %s' % bad))
        if bad:
            raise SystemExit('refusing to ship a pak that fails readback')
        print('       %s' % info['pak'])

    if a.install:
        # Everything that overrides this same map has to go, including hashtest's
        # paks: two paks on one level is a race, not a test.
        from riftlord.build.navigation import hashtest
        others = [n for c, (n, _w) in CASES.items() if c != a.install]
        others += [n for n, _w, _why in hashtest.CASES.values()]
        from riftlord.core import pakguard
        for name in others:
            stale = os.path.join(mods, name)
            if os.path.exists(stale):
                pakguard.remove(stale)
                print('removed  %s' % stale)
        print()
        print('Installed %r for %s.' % (a.install, a.map))
    else:
        print()
        print('Built in %s' % OUT)


if __name__ == '__main__':
    main()
