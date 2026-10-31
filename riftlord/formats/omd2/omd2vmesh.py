"""Read an OMD2 render mesh: real triangles, normals and UVs.

    python3 omd2vmesh.py --mesh art/Act2/floors/FloorMain1.vmesh
    python3 omd2vmesh.py --build          # writes omd2-models.json
    python3 omd2vmesh.py --check

The assembly tool drew collision hulls because they were the easy thing to read.
This reads what the game actually draws, which matters once OMD2 geometry becomes a
guide for hand-built work: a hull tells you where a wall is, the mesh tells you
what it looks like.

## The format

Same `VBIN` container as `.vcolmesh` and `.model`, and the same `VMSH` chunk as a
`.model`, so `omd2model.read_vmsh` reads it: a vertex descriptor (stride and a
format word per slot), a count record, the vertices, then the indices (u16 or
u32, as the count record says) and a bounding box. Every `.vmesh` in OMD1 and
OMD2 ends exactly 28 bytes after its indices.

Three vertex layouts are used, in both games:

    stride 56   pos, packed RGBA colour, normal, UV, lightmap UV, tangent
                (scene-baked meshes, `*.vscene_data/SM_*`)
    stride 52   pos, normal, UV, lightmap UV, tangent (art meshes)
    stride 44   as 52 without the lightmap UV (3 meshes)

Normals in about a quarter of the stride-56 meshes are not unit length (often
two-thirds), so they are normalised here.

`SMSH` is a welded copy of the triangles plus an edge list, not the render
indices.
"""

import argparse
import collections
import json
import math
import os
import struct
import sys
import zipfile
from riftlord.core import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))

MODELS = gamepath.data('omd2-models.json')


def chunks(buf):
    """Every VBIN chunk found by scanning, as (tag, body offset, size).

    Scanned rather than walked. These containers pad between chunks and nest them
    -- `MTRL` sits inside `MTRS` -- so following sizes from the start stops at the
    first gap and finds nothing, which is exactly what it did.
    """
    out = []
    for o in range(0, len(buf) - 8, 2):
        tag = buf[o:o + 4]
        if not tag.isalpha():
            continue
        size, = struct.unpack_from('<I', buf, o + 4)
        if size == 0 or size > len(buf) - o - 8:
            continue
        out.append((tag[::-1].decode('latin1', 'replace'), o + 8, size))
    return out


def read(buf):
    """{'verts', 'normals', 'uvs', 'tris'} for one `.vmesh`, or None."""
    from riftlord.formats.omd2 import omd2model
    if buf[:4] != b'VBIN':
        return None
    try:
        top = omd2model.chunk_map(buf)
        vm = omd2model.read_vmsh(buf, top['VMSH'])
    except (KeyError, ValueError, struct.error):
        return None
    if vm['nverts'] < 3:
        return None
    s = vm['streams']
    verts = [[round(float(c), 3) for c in v] for v in s['pos']]
    normals = []
    for n in s.get('normal', ()):
        length = math.sqrt(sum(float(c) * c for c in n)) or 1.0
        normals.append([round(float(c) / length, 4) for c in n])
    uvs = [[round(float(c), 5) for c in uv] for uv in s.get('tex0', ())]
    tris = [int(i) for i in vm['indices']]
    tris = tris[:len(tris) - len(tris) % 3]
    return {'verts': verts, 'normals': normals, 'uvs': uvs, 'tris': tris}


#: The suffixes OMD2 puts on a diffuse (base colour) texture. Both appear, roughly
#: evenly: `MainFloor1_df.dds` beside `Wall1_da.dds`. `_sp` is specular and `_nm` is
#: the normal map, and neither says anything about what a surface looks like.
OMD2_DIFFUSE = ('_df', '_da')


def materials(buf):
    """[{'name', 'diffuse', 'textures'}] for one `.vmesh`, in MTRL order.

    **This is the key for matching OMD2 art to OMD3 art.** Shape alone cannot tell
    stone from wood, and the converter has always matched on shape -- which is how a
    boulder becomes a wall panel. The material name and the diffuse texture say what
    the thing IS: `Floor1` on `MainFloor1_df.dds`, `Walls` on `Wall1_da.dds`,
    `blocks` on `WallArch_blocks_da.dds`.

    Read by scanning for printable runs rather than by field offsets. An `MTRL` body
    is a short name followed by texture paths with trailing quotes and padding spaces
    (`\\art\\Act2\\floors\\MainFloor1_df.dds"`), and the layout around them is not
    pinned down; the strings are unambiguous and the offsets are not.
    """
    import re as _re
    out = []
    for tag, off, size in chunks(buf):
        if tag != 'MTRL':
            continue
        body = buf[off:off + size]
        runs = [r.decode('latin1') for r in _re.findall(rb'[ -~]{3,}', body)]
        #: CUT AT `.dds`, because the byte AFTER it varies.
        #:
        #: A texture path ends with a terminator that is sometimes a quote, sometimes
        #: `#`, sometimes `*`, sometimes a space -- `wallthickness_da.dds#` beside
        #: `MainFloor1_df.dds"` and `cornerstones_df.dds*`. Stripping quotes alone left
        #: the others ending in junk, `endswith('.dds')` then rejected them, and the
        #: material fell back to its NORMAL MAP: three of TheCrunch's eighteen
        #: commonest materials were identified as `*_nm` and would have been matched to
        #: OMD3 art by the wrong texture entirely.
        texts, names = [], []
        for r in runs:
            m = _re.search(r'(?i)^(.*?\.dds)', r.strip())
            (texts if m else names).append(m.group(1) if m else r.strip())
        if not texts and not names:
            continue
        diffuse = None
        for t in texts:
            stem = t.rsplit('\\', 1)[-1].rsplit('/', 1)[-1]
            base = stem[:-4] if stem.lower().endswith('.dds') else stem
            if base.lower().endswith(OMD2_DIFFUSE):
                diffuse = base
                break
        if diffuse is None and texts:
            stem = texts[0].rsplit('\\', 1)[-1].rsplit('/', 1)[-1]
            diffuse = stem[:-4] if stem.lower().endswith('.dds') else stem
        out.append({'name': names[0] if names else None,
                    'diffuse': diffuse,
                    'textures': [t.rsplit('\\', 1)[-1].rsplit('/', 1)[-1] for t in texts]})
    return out


def surface_area(model):
    total = 0.0
    v, t = model['verts'], model['tris']
    for i in range(0, len(t), 3):
        p, q, r = v[t[i]], v[t[i + 1]], v[t[i + 2]]
        a = [q[k] - p[k] for k in range(3)]
        b = [r[k] - p[k] for k in range(3)]
        cr = (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
              a[0] * b[1] - a[1] * b[0])
        total += math.sqrt(sum(c * c for c in cr)) / 2.0
    return total


class Library:
    """Render meshes, read out of data.zip on demand."""

    def __init__(self, zip_path=None):
        from riftlord.formats.omd2 import trapgrids as T
        self.zf = zipfile.ZipFile(zip_path or T.DEFAULT_DATA_ZIP)
        self.names = {n.replace('\\', '/').lower(): n for n in self.zf.namelist()}

    def read(self, mesh):
        real = self.names.get(str(mesh).replace('\\', '/').lower())
        if not real:
            return None
        try:
            return read(self.zf.read(real))
        except Exception:                            # noqa: BLE001
            return None


def build(out_path=MODELS, meshes=None):
    from riftlord.formats.omd2 import omd2mesh
    from riftlord.formats.omd2 import trapgrids as T
    lib = Library()
    if meshes is None:
        with zipfile.ZipFile(T.DEFAULT_DATA_ZIP) as zf:
            scenes = [s for s in T.list_scenes(zf)
                      if not s.endswith('Perf') and s != 'TheCrossingBlue']
        meshes = set()
        for scene in scenes:
            try:
                for i in omd2mesh.scene_meshes(scene):
                    meshes.add(i['mesh'])
            except Exception:                        # noqa: BLE001
                continue
    out, why = {}, collections.Counter()
    for mesh in sorted(meshes):
        model = lib.read(mesh)
        if not model or not model['tris']:
            why['no readable geometry'] += 1
            continue
        out[str(mesh).replace('\\', '/').lower()] = model
    with open(out_path, 'w') as fh:
        json.dump({'models': out, 'notes': dict(why)}, fh)
    return out, why


def load(path=MODELS):
    if not os.path.exists(path):
        return {}
    with open(path) as fh:
        return json.load(fh)['models']


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--mesh')
    ap.add_argument('--build', action='store_true')
    ap.add_argument('--check', action='store_true')
    args = ap.parse_args(argv)
    lib = Library()

    if args.mesh:
        m = lib.read(args.mesh)
        if not m:
            print('%s: could not read' % args.mesh)
            return 1
        lo = [min(v[i] for v in m['verts']) for i in range(3)]
        hi = [max(v[i] for v in m['verts']) for i in range(3)]
        print('%s\n   %d vertices, %d triangles\n   bbox %s .. %s\n   area %.0f'
              % (args.mesh, len(m['verts']), len(m['tris']) // 3,
                 [round(v) for v in lo], [round(v) for v in hi],
                 surface_area(m)))
        return 0

    if args.check:
        # Compared against the collision hull, which is an independent read of a
        # different chunk. Containment is NOT the invariant: OMD2's collision is
        # often simpler than its art -- a railing collides as bare posts -- so the
        # render mesh legitimately sticks out. What should hold is that the two
        # describe the same object, so the check is on agreement of the boxes.
        from riftlord.formats.omd2 import omd2mesh
        hulls = omd2mesh.Library()
        close = loose = miss = 0
        worst = []
        for mesh in sorted(load()):
            m = lib.read(mesh)
            got = hulls.bounds(mesh)
            if not m or not got:
                miss += 1
                continue
            lo = [min(v[i] for v in m['verts']) for i in range(3)]
            hi = [max(v[i] for v in m['verts']) for i in range(3)]
            span = max(max(hi[i] - lo[i] for i in range(3)), 1.0)
            off = max(max(abs(lo[i] - got[0][i]), abs(hi[i] - got[1][i]))
                      for i in range(3))
            if off <= 0.05 * span:
                close += 1
            elif off <= 0.5 * span:
                loose += 1
            else:
                worst.append((off / span, mesh))
        worst.sort(reverse=True)
        print('render mesh vs collision hull, %d mesh(es):' % (close + loose + len(worst)))
        print('   %4d agree within 5%% of the object size' % close)
        print('   %4d within 50%% (collision is a simplification)' % loose)
        print('   %4d differ by more than that' % len(worst))
        for ratio, mesh in worst[:5]:
            print('        %.1fx  %s' % (ratio, mesh))
        return 0

    if args.build:
        out, why = build()
        print('read %d model(s)' % len(out))
        for reason, n in why.most_common(5):
            print('   %5d  %s' % (n, reason))
        tris = sum(len(m['tris']) // 3 for m in out.values())
        print('   %d triangles in total' % tris)
        return 0

    ap.error('need --mesh, --build or --check')


if __name__ == '__main__':
    raise SystemExit(main())
