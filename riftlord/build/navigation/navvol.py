"""Read the volumetric (flight) NavPower navgraph.

Flyers need this buffer and nothing else will do: surface coverage does not make
them move, and adding ANavPowerFlightVolume actors to a map that ships no
volumetric buffer does nothing, because the actors describe airspace for a graph
the level does not contain. Ten shipped levels have one, 724 to 3,968 bytes.

Layout, measured on all ten:

    24-byte container   0, version 2, payloadSize, hash, 0, flags 0x01000001
    +0x14               world bbox, six floats, min then max
    +0x2c               volume count -- equals the level's NavPowerFlightVolume
                        actor count on all ten maps, so it is ground truth for
                        whether ``scan`` found everything
    +0x30               edge count -- equals sum(degree)/2 on every map that
                        decodes completely, and equals the number of portals

A volume's ``degree`` is simply HOW MANY PORTALS REFERENCE IT, so the adjacency lives
entirely in the portal records and the degree word is a cached count. Scenario_Ice_01's
portals join (0,2), (1,2), (1,3) and (3,4), giving degrees 1,2,2,2,1 -- a graph, not a
chain.
    volume records      centre 3f, radius 1f, numPlanes u32, degree u32,
                        then numPlanes * (nx, ny, nz, d)  with  n . p == d
    portal records      vertex count u32, then count * (x, y, z)

``degree`` is the volume's neighbour count, so the graph is an ordinary adjacency
structure: sum(degree) / 2 == the count at +0x30 == the number of portals, which
holds on every map that decodes completely. On Scenario_Sand_05 the three volumes
tile contiguously in y (-9350 to -7350 to -6050 to 450) with degrees 1, 2, 1 and a
portal at each junction, which is the whole flight route for that level.

The plane convention is ``n . p == d``. Reading d as a signed extent instead gives
boxes mirrored in y and z that look almost right and sit on no face of anything.

Every record carries a centre and a bounding radius that are redundant with its
own planes, so ``parse`` recomputes both and reports whether they agree. That is
the check that makes the decode verifiable rather than merely plausible: it passed
on every record of all ten maps. ``scan`` is conservative -- it finds a subset of
records on five maps and has never over-counted -- so trust what it returns and
not the absence of what it does not.

    python3 navvol.py --list                # every level with a flight graph
    python3 navvol.py --show Scenario_Ice_01
"""

import argparse
import math
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.build.navigation import navpatch  # noqa: E402

CONTAINER = 24
OFF_BBOX = 0x14
OFF_NVOLS = 0x2c
OFF_COUNT = 0x30
FLAGS_EXPECTED = 0x01000001


def checksum(payload):
    """The container's +12 word: the sum of the payload's uint32 words, mod 2^32.

    Cracked offline by sweeping 181 functions over 7 candidate byte ranges and keeping
    only variants that matched every buffer. **Verified on all 43 OMD3 buffers** - 33
    surface and 10 volumetric - with no exceptions.

    It is validated: flipping this word in game strands every agent that uses that
    buffer, and the two buffers are checked independently. So any written graph must
    carry a correct one, and now can.

    OMD2's .path graphs do NOT satisfy this, under any of four candidate ranges, so
    the checksum is OMD3's and is one more real difference between the two games.
    """
    n = len(payload) // 4
    return sum(struct.unpack_from('<%dI' % n, payload, 0)) & 0xFFFFFFFF


def reseal(buf):
    """A copy of the buffer with its +12 checksum recomputed from its payload."""
    out = bytearray(buf)
    struct.pack_into('<I', out, 12, checksum(bytes(out[CONTAINER:])))
    return bytes(out)


def verify(buf):
    """Does this buffer's stored checksum match its payload?"""
    return struct.unpack_from('<I', buf, 12)[0] == checksum(buf[CONTAINER:])


def aabb(planes):
    """(min, max) from axis-aligned planes by solving n . p == d, else None."""
    lo = [None] * 3
    hi = [None] * 3
    for nx, ny, nz, d in planes:
        nrm = (nx, ny, nz)
        ax = max(range(3), key=lambda i: abs(nrm[i]))
        if abs(abs(nrm[ax]) - 1.0) > 1e-3:
            return None
        v = d / nrm[ax]
        if nrm[ax] > 0:
            hi[ax] = v if hi[ax] is None else max(hi[ax], v)
        else:
            lo[ax] = v if lo[ax] is None else min(lo[ax], v)
    if any(x is None for x in lo + hi):
        return None
    return lo, hi


def scan(body):
    """Volume records found in the payload body (after the 24-byte container).

    Conservative: a record is only accepted when its planes are unit normals, so
    variants this has not seen are skipped rather than guessed at.
    """
    out = []
    n = len(body)
    o = 0
    while o + 8 <= n:
        cnt, flags = struct.unpack_from('<2I', body, o)
        # `flags` is the volume's DEGREE -- how many portals name it. Every shipped
        # graph happens to use only 1 and 2, but nothing says a volume cannot be a
        # hub, and accepting only 1 or 2 silently dropped a volume whose degree an
        # emitter had raised. The unit-normal, centre and radius checks below are
        # what actually identify a volume record.
        if 1 <= flags <= 16 and 4 <= cnt <= 16 and o >= 16 and o + 8 + cnt * 16 <= n:
            fl = struct.unpack_from('<%df' % (cnt * 4), body, o + 8)
            planes = [fl[i * 4:i * 4 + 4] for i in range(cnt)]
            if all(abs(math.sqrt(p[0] ** 2 + p[1] ** 2 + p[2] ** 2) - 1.0) < 1e-3
                   for p in planes):
                cx, cy, cz, rad = struct.unpack_from('<4f', body, o - 16)
                box = aabb(planes)
                centre_ok = radius_ok = False
                if box:
                    mid = [(box[0][i] + box[1][i]) / 2.0 for i in range(3)]
                    half = [(box[1][i] - box[0][i]) / 2.0 for i in range(3)]
                    centre_ok = all(abs(mid[i] - (cx, cy, cz)[i]) < 1.0
                                    for i in range(3))
                    radius_ok = abs(math.sqrt(sum(h * h for h in half)) - rad) < 1.0
                out.append({'at': o, 'planes': planes, 'box': box,
                            'centre': (cx, cy, cz), 'radius': rad,
                            'flags': flags,
                            'centre_ok': centre_ok, 'radius_ok': radius_ok})
                o += 8 + cnt * 16
                continue
        o += 4
    return out


def portals(body, bbox, vols):
    """Portal polygons: a vertex count then that many (x, y, z).

    Accepted only when every vertex lies inside the graph bbox and the polygon is
    flat in at least one axis, which is what a face joining two volumes gives. Byte
    ranges already claimed by a volume record are skipped, so a plane's floats
    cannot be re-read as a vertex list.
    """
    out = []
    n = len(body)
    mn, mx = bbox[:3], bbox[3:]
    claimed = [(v['at'] - 16, v['at'] + 8 + len(v['planes']) * 16) for v in vols]
    o = OFF_COUNT + 4
    while o + 4 <= n:
        if any(a <= o < b for a, b in claimed):
            o += 4
            continue
        cnt = struct.unpack_from('<I', body, o)[0]
        if 3 <= cnt <= 12 and o + 4 + cnt * 12 <= n:
            vs = [struct.unpack_from('<3f', body, o + 4 + i * 12)
                  for i in range(cnt)]
            if all(mn[a] - 1 <= v[a] <= mx[a] + 1 for v in vs for a in range(3)):
                flat = [a for a in range(3)
                        if max(v[a] for v in vs) - min(v[a] for v in vs) < 0.01]
                if flat:
                    # The record starts 20 bytes before the vertex count:
                    #   volA i32, volB i32, centre.x/y/z floats, count i32
                    # Decoded by reading the bytes before each portal on maps whose
                    # adjacency is known from the volume geometry; the centre matches
                    # the quad's own centre exactly on every shipped map, and the two
                    # indices name volumes that both contain the quad.
                    rec = {'at': o, 'verts': vs, 'flat': flat}
                    if o >= 20:
                        a, b = struct.unpack_from('<2i', body, o - 20)
                        rec['volumes'] = (a, b)
                        rec['centre'] = struct.unpack_from('<3f', body, o - 12)
                        rec['head'] = o - 20
                    out.append(rec)
                    o += 4 + cnt * 12
                    continue
        o += 4
    return out


def parse(buf):
    """Decode one volumetric buffer, container included."""
    w = struct.unpack_from('<6I', buf, 0)
    body = buf[CONTAINER:]
    vols = scan(buf[CONTAINER:])
    degree_sum = sum(v['flags'] for v in vols)
    return {
        'degree_sum': degree_sum,
        'bytes': len(buf),
        'version': w[1],
        'payload': w[2],
        'hash': w[3],
        'flags': w[5],
        'payload_ok': w[2] + CONTAINER == len(buf),
        'hash_ok': w[3] == checksum(body),
        'flags_ok': w[5] == FLAGS_EXPECTED,
        'bbox': struct.unpack_from('<6f', body, OFF_BBOX),
        'count': struct.unpack_from('<I', body, OFF_COUNT)[0],
        'nvolumes': struct.unpack_from('<I', body, OFF_NVOLS)[0],
        'complete': struct.unpack_from('<I', body, OFF_NVOLS)[0] == len(vols),
        'volumes': vols,
        'portals': portals(body, struct.unpack_from('<6f', body, OFF_BBOX), vols),
        'edges_ok': degree_sum == 2 * struct.unpack_from('<I', body, OFF_COUNT)[0],
    }


def for_map(map_name, tmp):
    """The volumetric graph of one level, or None if it ships none."""
    files = navpatch.fetch_map(navpatch.map_path(map_name), tmp)
    found = navpatch.nav_buffers(files['.umap'], files['.uexp'])
    if not found or not found[4]:
        return None
    with open(files['.uexp'], 'rb') as fh:
        data = fh.read()
    return parse(data[found[3]:found[3] + found[4]])


def flight_actors(map_name, tmp):
    """ANavPowerFlightVolume actors in the level."""
    files = navpatch.fetch_map(navpatch.map_path(map_name), tmp)
    info = navpatch.parse_header(files['.umap'])
    return sum(1 for e in info['exports']
               if str(e.get('class') or e.get('cls')) == 'NavPowerFlightVolume')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--show', metavar='MAP')
    a = ap.parse_args()
    tmp = tempfile.mkdtemp(prefix='navvol')

    if a.show:
        d = for_map(a.show, tmp)
        if not d:
            raise SystemExit('%s ships no volumetric navgraph' % a.show)
        print('%s  %d bytes  payload %d (%s)  flags %#010x (%s)  hash %#010x'
              % (a.show, d['bytes'], d['payload'], 'ok' if d['payload_ok'] else 'BAD',
                 d['flags'], 'ok' if d['flags_ok'] else 'BAD', d['hash']))
        b = d['bbox']
        print('  world bbox  min %9.0f %9.0f %9.0f   max %9.0f %9.0f %9.0f'
              % (b[0], b[1], b[2], b[3], b[4], b[5]))
        print('  volumes %d (+0x2c), edges %d (+0x30), actors %d, decoded %d  %s'
              % (d['nvolumes'], d['count'], flight_actors(a.show, tmp),
                 len(d['volumes']),
                 'COMPLETE' if d['complete'] else 'SCANNER INCOMPLETE'))
        for i, v in enumerate(d['volumes']):
            bx = v['box']
            print('  volume %-2d at +%-5d %2d planes flags %d  radius %8.1f %s %s'
                  % (i, v['at'], len(v['planes']), v['flags'], v['radius'],
                     'centre ok' if v['centre_ok'] else 'CENTRE MISMATCH',
                     'radius ok' if v['radius_ok'] else 'RADIUS MISMATCH'))
            if bx:
                print('              box min %8.0f %8.0f %8.0f  max %8.0f %8.0f %8.0f'
                      % (bx[0][0], bx[0][1], bx[0][2], bx[1][0], bx[1][1], bx[1][2]))
        return

    print('%-20s %7s %7s %7s %7s %7s %8s %9s'
          % ('level', 'bytes', 'volumes', 'edges', 'actors', 'decoded',
             'checks', 'deg sum/2'))
    for name in sorted(navpatch.maps(include_mods=False)):
        d = for_map(name, tmp)
        if not d:
            continue
        v = d['volumes']
        ok = sum(1 for x in v if x['centre_ok'] and x['radius_ok'])
        print('%-20s %7d %7d %7d %7d %7s %8s %9s'
              % (name, d['bytes'], d['nvolumes'], d['count'],
                 flight_actors(name, tmp),
                 '%d%s' % (len(v), '' if d['complete'] else '*'),
                 '%d/%d' % (ok, len(v)),
                 '%g %s' % (d['degree_sum'] / 2.0,
                            'ok' if d['edges_ok'] else 'NO')))


if __name__ == '__main__':
    main()
