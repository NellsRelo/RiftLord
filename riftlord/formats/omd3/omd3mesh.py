"""Read an OMD3 render mesh out of a cooked `UStaticMesh`.

    python3 omd3mesh.py --mesh RailingShortC
    python3 omd3mesh.py --build          # writes omd3-models.json
    python3 omd3mesh.py --check

The assembly tool has to show a person what they are placing, and bounds and
collision hulls do not. This reads the geometry the game draws.

## Finding it

Cooked `FStaticMeshRenderData` follows the export's tagged properties. Rather than
walk a version-specific preamble, each piece is located by a property only correct
data satisfies:

* **The section table** sits 48 bytes into the cooked region and reads
  `MaterialIndex, FirstIndex, NumTriangles, MinVertexIndex, MaxVertexIndex`. For a
  single-section mesh the first, second and fourth are zero, which makes it
  unmistakable, and it yields the two counts everything else is checked against:
  `RailingShortC` is 3,007 triangles over 2,038 vertices.
* **Positions** are plain `float3`, found as the run of `NumVertices` triples whose
  bounding box matches the mesh's own `ExtendedBounds`.
* **The vertex buffer** announces itself as `NumTexCoords, NumVertices, 0, 0, 8,
  NumVertices`, followed by 8 bytes of packed tangents per vertex and then
  `4 * NumTexCoords` bytes of half-float UVs per vertex.
* **Indices** are `uint16`, `NumTriangles * 3` of them, and every one must be below
  `NumVertices`.

## The thing that hid it for so long

The position buffer starts at an offset that is **even but not a multiple of four**
-- 30 in `RailingShortC`. Every scan that stepped four bytes at a time walked past
it, which is why the data looked absent and quantised. Step one byte, or step two,
but do not assume alignment: UE pads nothing here.
"""

import argparse
import collections
import functools
import json
import math
import os
import struct
import sys
from riftlord.core import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.core import gamefiles  # noqa: E402
from riftlord.build import levelbuild  # noqa: E402
from riftlord.formats.omd3 import meshcat  # noqa: E402
from riftlord.formats.ue.uassetio import Package  # noqa: E402

MODELS = gamepath.data('omd3-models.json')

#: Where the section table sits, measured from the end of the tagged properties.
SECTION_AT = 48


def cooked_region(pkg, export):
    """The export's bytes and where its cooked data begins."""
    names = [n for n, _h in pkg.names]
    data = export['data']
    o = 0
    while o + 8 <= len(data):
        ni = struct.unpack_from('<i', data, o)[0]
        name = names[ni] if 0 <= ni < len(names) else '?'
        if name == 'None':
            return data, o + 8
        ti = struct.unpack_from('<i', data, o + 8)[0]
        kind = names[ti] if 0 <= ti < len(names) else ''
        size = struct.unpack_from('<i', data, o + 16)[0]
        head = 24 + 1
        if kind == 'BoolProperty':
            head, size = 26, 0
        elif kind == 'StructProperty':
            head = 24 + 8 + 17
        elif kind in ('ArrayProperty', 'ByteProperty', 'EnumProperty'):
            head = 24 + 8 + 1
        o += head + size
    return data, len(data)


#: `Sections` is a TArray, so its element count sits just before the first record.
SECTION_COUNT_AT = 44

#: A sanity bound on a section's material index. The real limit is the length of
#: `StaticMaterials`, which is not parsed here; nothing in the catalogue comes close.
MAX_MATERIALS = 64

#: One `FStaticMeshSection`: five uint32 then four bools serialised as int32.
#: Confirmed on `SM_TropicalTree_6`, whose second section begins 36 bytes after the
#: first with `MaterialIndex = 1` and `FirstIndex = 2520`, which is its first
#: section's 840 triangles times three.
SECTION_SIZE = 36


def sections(data, start, table=None):
    """Every LOD0 section as (material, first index, triangles, min, max), or None.

    `table` is the offset of a section count other than LOD0's, for an inline LOD
    found by `inline_lod`.

    Most meshes have one, but not all -- `WoodBridge2` has three and
    `SM_TropicalTree_6` two, about 3% of the catalogue. Reading only the first
    leaves those partly built, which looks like a mesh with pieces missing rather
    than like a bug.
    """
    at = start + SECTION_COUNT_AT if table is None else table
    if at + 4 > len(data):
        return None
    count = struct.unpack_from('<I', data, at)[0]
    if not 1 <= count <= 64:
        return None
    out = []
    for k in range(count):
        o = at + 4 + k * SECTION_SIZE
        if o + 20 > len(data):
            return None
        material, first, tris, lo, hi = struct.unpack_from('<5I', data, o)
        # A section's material index is an index into StaticMaterials, and it is NOT
        # the section's own number: `Jar` runs 0, 2, 1 and `PineTree_01` runs 1, 0.
        # Requiring material == k rejected every mesh whose materials are not in
        # ascending order -- 200 of 1,166, 124 of them castle kit, all of them
        # correctly laid out and none of them drawable.
        if material > MAX_MATERIALS or lo > hi or tris == 0 or hi > 5_000_000:
            return None
        # What does hold: section 0 starts at index 0, and the index runs forward.
        #
        # Their VERTEX ranges say nothing -- sections index into one shared buffer and
        # may overlap. `Mesh_Caltrop` runs 0..479 then 22..1256, and requiring
        # `lo == previous hi + 1` rejected it although its totals (1,636 triangles,
        # 1,257 vertices) were right. Fourth time a validation in this reader held only
        # on the meshes it was written against.
        if k == 0:
            if first != 0:
                return None
        elif first <= out[-1][1]:
            return None
        out.append((material, first, tris, lo, hi))
    return out


#: How far, in uu, decoded positions may fall short of the catalogue bounds.
POSITION_FILL_SLACK = 1.5
#: The same for an inline lower LOD, as a fraction of the mesh's largest half-extent.
INLINE_LOD_FILL = 0.01

#: Bytes per vertex an inline LOD0 needs at least: a float3 position and 8 bytes of
#: packed tangents.
INLINE_LOD0_BYTES_PER_VERTEX = 20


def inline_lod(data, start, vertices):
    """Offset of the section count of the first LOD whose positions are inline, or None.

    A mesh whose LOD0 is streamed out (`S_Desert_Mountain_01`: LOD0 62,579 vertices,
    not in the export) keeps a smaller LOD inline: its position header
    (12, n, 12, n) is the only one present, and its own section table, ending in a
    record whose MaxVertexIndex is n - 1, sits before it.
    """
    if list(position_headers(data, start, vertices)):
        return None
    #: LOD0's positions and tangents alone would not fit in the export
    if len(data) - start >= vertices * INLINE_LOD0_BYTES_PER_VERTEX:
        return None
    k = start
    while True:
        k = data.find(struct.pack('<I', 12), k)
        if k < 0 or k + 16 > len(data):
            return None
        a, n, b, m = struct.unpack_from('<4I', data, k)
        if a == 12 and b == 12 and n == m and 0 < n < vertices:
            for table in range(start, k - 4):
                c = struct.unpack_from('<I', data, table)[0]
                if not 1 <= c <= 64 or table + 4 + c * SECTION_SIZE > k:
                    continue
                got = sections(data, start, table)
                if got and max(sec[4] for sec in got) + 1 == n:
                    return table
        k += 1


def section(data, start, table=None):
    """(total triangles, total vertices) across every section, or None."""
    got = sections(data, start, table)
    if not got:
        return None
    return sum(s[2] for s in got), max(s[4] for s in got) + 1


def vertex_buffer(data, start, vertices, unaligned=False):
    """Where `FStaticMeshVertexBuffer` begins, or None.

    It announces itself: `NumTexCoords, NumVertices, 0, 0, 8, NumVertices`, where
    the 8 is the size of a packed tangent pair. Finding it first is what makes
    reading a mesh quick -- the position buffer ends just before it, so the search
    for positions shrinks from the whole export to about a hundred bytes (116 in
    both meshes measured).
    """
    #: four-byte steps; `unaligned` adds a byte-by-byte pass, for an inline LOD whose
    #: buffer is not aligned to the region start
    for step in ((4, 1) if unaligned else (4,)):
        for o in range(start, len(data) - 24, step):
            a = struct.unpack_from('<6I', data, o)
            if (a[1] == vertices and a[5] == vertices and a[2] == 0 and a[3] == 0
                    and a[4] == 8 and 1 <= a[0] <= 8):
                return o
    return None


def position_candidates(data, start, count, lo, hi, slack=1.5, limit=None):
    """Every offset at which `count` float3 sit inside the mesh's own bounds.

    There is usually more than one, and **the first is often wrong**: zero is
    inside almost every mesh's bounding box, so a run that begins in padding reads
    as seven zeroes followed by the real vertices and still passes a range test.
    `Floor_4x4` decoded that way had 15 distinct positions where a box has 8, and
    the geometry built from it was shattered.

    So this returns them all and lets the caller decide, using the triangles.

    Stepped **one byte at a time**: the buffer is not four-byte aligned.
    """
    need = count * 12
    last = (limit - need) if limit else (len(data) - need)
    out = []
    for base in range(start, max(start, last) + 1):
        first = struct.unpack_from('<3f', data, base)
        if not all(lo[i] - slack <= first[i] <= hi[i] + slack for i in range(3)):
            continue
        pts = [struct.unpack_from('<3f', data, base + i * 12) for i in range(count)]
        ok = True
        mn = [1e30] * 3
        mx = [-1e30] * 3
        for p in pts:
            for i in range(3):
                if not (lo[i] - slack <= p[i] <= hi[i] + slack):
                    ok = False
                    break
                mn[i] = min(mn[i], p[i])
                mx[i] = max(mx[i], p[i])
            if not ok:
                break
        if not ok:
            continue
        if max(max(abs(mn[i] - lo[i]), abs(mx[i] - hi[i]))
               for i in range(3)) < slack:
            out.append((base, pts))
    return out


def position_headers(data, start, count, limit=None):
    """Offsets where a run of `count` positions begins, found by its array header.

    UE writes `FPositionVertexBuffer` as its element stride and element count -- 12 and
    the vertex count -- immediately before the float3 run, so the buffer can be located
    exactly instead of searched for. Measured on `Floor_4x4`, `Jar` and `Stairs_3x_2_SB`:
    the eight bytes before the run the scoring picks are `pack('<2I', 12, count)` every
    time.

    Every occurrence is returned, not the first: the pattern is only eight bytes and the
    caller still scores each candidate against the triangles, so a coincidence costs a
    little work rather than a wrong reading.
    """
    want = struct.pack('<2I', 12, count)
    out, at = [], max(0, start - 8)
    while True:
        at = data.find(want, at, limit)
        if at < 0:
            return out
        out.append(at + 8)
        at += 1


def points_at(data, base, count, lo, hi, slack=1.5):
    """`count` float3 read at `base`, or None if they do not fill the mesh's bounds.

    The same test `position_candidates` applies at every offset, for a caller that
    already knows where to look.
    """
    if base < 0 or base + count * 12 > len(data):
        return None
    pts = [struct.unpack_from('<3f', data, base + i * 12) for i in range(count)]
    mn = [1e30] * 3
    mx = [-1e30] * 3
    for p in pts:
        for i in range(3):
            if not (lo[i] - slack <= p[i] <= hi[i] + slack):
                return None
            mn[i] = min(mn[i], p[i])
            mx[i] = max(mx[i], p[i])
    if max(max(abs(mn[i] - lo[i]), abs(mx[i] - hi[i]))
           for i in range(3)) >= slack:
        return None
    return pts


def positions(data, start, count, lo, hi, slack=1.5, limit=None):
    """The first offset whose float3 run matches the bounds. See the caveat above."""
    got = position_candidates(data, start, count, lo, hi, slack, limit)
    return got[0] if got else (None, None)


#: How far past the start of the cooked region the fallback scan may look for a position
#: buffer. Measured: where the buffer is found at all it begins 831-1,559 bytes in
#: (Floor_4x4 831, Jar 1,559, Stairs_3x_2_SB 831, SM_Ice_01 1,159, S_Desert_Mountain_01
#: 1,050, Portcullis_Wood_3W_Half 831), so 64 kB is forty times the largest seen. Beyond
#: it there is nothing to find and the scan only costs: unbounded, the four desert
#: mountains spent 117-143 seconds EACH before failing anyway, because the scan unpacks
#: every vertex at every offset whose first point lands inside the bounds.
FALLBACK_REACH = 1 << 16

#: How far before the expected start of the position buffer to begin looking. The
#: buffer ends where the vertex buffer begins, so the start is `vb - verts * 12` give or
#: take whatever padding sits between; 512 bytes is far more than any mesh measured
#: needs and still leaves the scan trivial.
POSITION_WINDOW = 512

#: How many of a candidate's triangles may be degenerate before it is accepted
#: outright. A real render index buffer has almost none; the other buffers UE keeps
#: per LOD (depth-only, reversed, adjacency) produce plenty when read as triangles.
DEGENERATE_LIMIT = 0.02


def _degenerate_share(verts, idx, sample=600):
    """The fraction of a candidate's triangles with no area.

    This is the test that separates a real reading from a plausible one. Matching
    on range alone accepts an index buffer that yields 9 degenerate triangles out
    of 12 on `Floor_4x4`, and a position run that begins in padding -- zero is
    inside almost every mesh's bounds, so seven leading zeroes pass unnoticed.
    Together they produce geometry that looks shattered.
    """
    count = min(len(idx) // 3, sample)
    if count == 0:
        return 1.0
    bad = 0
    for t in range(count):
        a, b, c = idx[t * 3], idx[t * 3 + 1], idx[t * 3 + 2]
        if a == b or b == c or a == c:
            bad += 1
            continue
        pa, pb, pc = verts[a], verts[b], verts[c]
        u = (pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2])
        w = (pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2])
        cr = (u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2],
              u[0] * w[1] - u[1] * w[0])
        if cr[0] * cr[0] + cr[1] * cr[1] + cr[2] * cr[2] < 1e-8:
            bad += 1
    return bad / count


def index_headers(data, triangles, start=0, limit=None):
    """(offset, element width) for every index buffer holding `triangles` triangles.

    UE serialises the raw index buffer as a **byte** array -- element size 1 and a count
    in BYTES -- so the header is `pack('<2I', 1, triangles * 3 * width)`, and the width
    that count implies is what says whether the indices are 16- or 32-bit. Measured on
    meshes that read correctly: `Floor_4x4` (1, 72), `Jar` (1, 18516),
    `Stairs_3x_2_SB` (1, 91512), each exactly two bytes per index and each sitting 22
    bytes past the end of the UV array.

    This is how a mesh with more than 65,536 vertices is read at all: its indices cannot
    fit in `uint16`, and the scan in `indices()` only ever tried that width, so the three
    portcullises (77k-173k vertices) could not be read and spent minutes failing --
    almost every 16-bit value is a valid index when there are that many vertices, so the
    scan's early-out never fired.
    """
    out = []
    need = triangles * 3
    if need == 0:
        return out
    for width in (2, 4):
        pattern = struct.pack('<2I', 1, need * width)
        at = start
        while True:
            at = data.find(pattern, at, limit)
            if at < 0:
                break
            out.append((at + 8, width))
            at += 1
    return out


def index_run(data, at, triangles, vertices, width):
    """`triangles * 3` indices read at `at`, or None if any is out of range."""
    need = triangles * 3
    if at < 0 or at + need * width > len(data):
        return None
    idx = struct.unpack_from('<%d%s' % (need, 'H' if width == 2 else 'I'), data, at)
    if max(idx) != vertices - 1:
        return None
    return list(idx)


def indices(data, start, triangles, vertices, verts=None, window=1 << 22):
    """The render index buffer: `uint16`, and its triangles must be real.

    Searched forward from the end of the UV array rather than computed, because
    what sits between them varies -- `MineTracks_LargeA` has a colour buffer and
    another array there.

    The scan is cheap because a failure says where to resume: if the k-th value
    from a candidate is out of range then no start before it can work either, so
    the search jumps past that value rather than advancing two bytes.

    `verts` enables the degeneracy test, which is what actually identifies the
    right buffer. Without it this returns the first range-valid run, and UE keeps
    several index buffers per LOD that pass that test and are not the geometry.
    """
    need = triangles * 3
    if need == 0:
        return None, None
    probe = min(need, 16)
    base = start
    last = min(len(data) - need * 2, start + window)
    best = None
    while base <= last:
        head = struct.unpack_from('<%dH' % probe, data, base)
        bad = -1
        for k, v in enumerate(head):
            if v >= vertices:
                bad = k
                break
        if bad >= 0:
            base += (bad + 1) * 2
            continue
        idx = struct.unpack_from('<%dH' % need, data, base)
        if max(idx) == vertices - 1:
            if verts is None:
                return base, list(idx)
            share = _degenerate_share(verts, idx)
            if share <= DEGENERATE_LIMIT:
                return base, list(idx)
            if best is None or share < best[0]:
                best = (share, base, idx)
        base += 2
    if best is not None:
        return best[1], list(best[2])
    return None, None


def uv_end(data, vb, vertices, sets):
    """Where the vertex buffer's UV array finishes, which is where indices begin.

    Read from the array's own header rather than assumed. Computing it as
    `4 * NumTexCoords * NumVertices` is right for most meshes and wrong for enough
    of them to matter -- the header says `(element size, count)` and that is what
    the data actually occupies.
    """
    tangents = vb + 24 + vertices * 8
    if tangents + 8 > len(data):
        return vb + 24 + vertices * 8 + 8 + vertices * 4 * sets
    elem, count = struct.unpack_from('<2I', data, tangents)
    if 1 <= elem <= 16 and 0 < count <= 8 * vertices + 16:
        return tangents + 8 + elem * count
    return tangents + 8 + vertices * 4 * sets


def _imports(pkg):
    """[(class, outer index, name)] for a package's imports."""
    names = [n for n, _h in pkg.names]

    def nm(i):
        return names[i] if 0 <= i < len(names) else '?'
    out = []
    for imp in pkg.imports:
        out.append((nm(struct.unpack_from('<I', imp, 8)[0]),
                    struct.unpack_from('<i', imp, 16)[0],
                    nm(struct.unpack_from('<I', imp, 20)[0])))
    return out


def material_slots(info, uexp, hdr):
    """Every material slot a StaticMesh declares, as `/Game/...` paths, in order.

    A section's material index points into THIS list. 117 meshes in the catalogue
    declare more than one slot -- `Orc_Decor3` has three -- and taking the first
    `MaterialInstanceConstant` import for the whole mesh paints every section with one
    of them, which is why a mesh could come out partly right and partly wrong.
    """
    from riftlord.onslaught import onslaught
    names = info.get('names') or []
    imports = info.get('imports') or []

    def nm(i):
        v = names[i] if 0 <= i < len(names) else None
        return (v[0] if isinstance(v, (tuple, list)) else v)

    def path_of(idx):
        k = -idx - 1
        if not (0 <= k < len(imports)):
            return None
        entry = imports[k]
        outer = entry.get('outer')
        if outer is not None and outer < 0:
            ok = -outer - 1
            if (0 <= ok < len(imports)
                    and str(imports[ok].get('cls') or '') == 'Package'):
                return imports[ok].get('name') or imports[ok].get('object_name')
        return entry.get('name') or entry.get('object_name')

    for e in info['exports']:
        for pr in onslaught.read_props(info, uexp, hdr, e):
            if pr[0] != 'StaticMaterials':
                continue
            raw = pr[2]
            try:
                count = struct.unpack_from('<i', raw, 0)[0]
            except Exception:                        # noqa: BLE001
                return []
            o = 4 + 16 + 8 + 8 + 16 + 1
            out = []
            for _k in range(min(count, 32)):
                fields, o = _tagged(raw, o, nm)
                if not fields:
                    break
                ref = fields.get('MaterialInterface')
                got = None
                if ref and len(ref[1]) >= 4:
                    idx = struct.unpack_from('<i', ref[1], 0)[0]
                    if idx < 0:
                        got = path_of(idx)
                out.append(got)
            return out
    return []


def material_of(pkg):
    """The package path of the mesh's material, or None.

    `StaticMaterials` holds a `MaterialInterface` pointing at an imported
    `MaterialInstanceConstant`, whose outer is the `Package` import that names the
    path.
    """
    imports = _imports(pkg)
    for cls, outer, name in imports:
        if cls != 'MaterialInstanceConstant':
            continue
        if -outer - 1 < len(imports):
            ocls, _oo, opath = imports[-outer - 1]
            if ocls == 'Package':
                return opath
    return None


#: OMD3 names its textures by role: base colour, then metallic/roughness/AO and
#: the normal map. Only the base colour is wanted.
#:
#: `_BC` is the house style, but older art uses `_df`/`_diff` -- 18 of the 316
#: materials name their base colour that way, and taking `_BC` alone left those
#: meshes untextured. Order matters: the first suffix with a match wins, and the
#: normal (`_nm`, `_N`, `_normal`), emissive (`_EM`) and roughness (`_MRA`, `_MRH`)
#: maps that sit beside them must never be picked.
BASE_COLOUR = '_BC'
BASE_COLOUR_SUFFIXES = ('_BC', '_df', '_diff')


def _tagged(raw, o, nm, limit=64):
    """A tagged property list -> ({name: (type, body)}, offset past the terminator).

    Enough of UE4's property serialisation to read material parameters: tag name and
    type as FNames, size, array index, then per-type extras, a has-guid byte, and the
    body. Struct and array properties carry an extra FName (and a guid for structs),
    and a bool keeps its value in the tag with a zero-length body -- miss any of those
    and every property after it is read at the wrong offset.
    """
    out = {}
    for _ in range(limit):
        if o + 8 > len(raw):
            break
        name = nm(struct.unpack_from('<i', raw, o)[0])
        o += 8
        if not name or name == 'None':
            break
        typ = nm(struct.unpack_from('<i', raw, o)[0])
        o += 8
        size = struct.unpack_from('<i', raw, o)[0]
        o += 8                                       # size + array index
        if typ == 'StructProperty':
            o += 8 + 16
        elif typ in ('ArrayProperty', 'SetProperty', 'ByteProperty', 'EnumProperty'):
            o += 8
        elif typ == 'MapProperty':
            o += 16
        elif typ == 'BoolProperty':
            o += 1
        o += 1                                       # has-guid
        out[name] = (typ, raw[o:o + size])
        o += size
    return out, o


@functools.lru_cache(maxsize=2048)
def texture_parameters(material_path):
    """[(parameter name, texture name)] a material instance actually assigns.

    THE AUTHORITATIVE SOURCE, and it is ordered. A layered terrain material imports
    four ground textures and assigns them as four `BC` parameters in layer order:
    `Ground_Dirt_Rock_Mud_MP` assigns grass, overgrown, dirt-rock, dirt. Choosing by
    import order instead picked the third and drew Front Lawn's grass brown.

    The earlier note that these arrays are empty was wrong -- it held for the few
    instances that only override colours, not for layered or master-derived ones.
    """
    try:
        ua, ux = gamefiles.package(material_path)
    except Exception:                                # noqa: BLE001
        return ()
    import tempfile
    from riftlord.onslaught import onslaught
    from riftlord.formats.ue.uheader import parse as parse_header
    try:
        root = tempfile.mkdtemp(prefix='omd3mat-')
        staged = os.path.join(root, 'a.uasset')
        with open(staged, 'wb') as fh:
            fh.write(ua)
        info = parse_header(staged)
    except Exception:                                # noqa: BLE001
        return ()
    names = info.get('names') or []
    imports = info.get('imports') or []

    def nm(i):
        v = names[i] if 0 <= i < len(names) else None
        return (v[0] if isinstance(v, (tuple, list)) else v)

    def imp(i):
        """The FULL `/Game/...` path of an imported texture, not its leaf name.

        A Texture2D import carries only its object name; the path lives on its OUTER,
        which is the Package import -- exactly as `material_of` reads a material's.
        Returning the leaf gave the UI `Set1_Grass_Wet_01_BC`, which it pasted onto
        `/api/texture` to make a URL that could never resolve: every texture 404'd and
        the whole level drew black.
        """
        k = -i - 1
        if not (0 <= k < len(imports)):
            return None
        entry = imports[k]
        outer = entry.get('outer')
        if outer is not None and outer < 0:
            ok = -outer - 1
            if 0 <= ok < len(imports):
                owner = imports[ok]
                if str(owner.get('cls') or '') == 'Package':
                    got = owner.get('name') or owner.get('object_name')
                    if got:
                        return got
        return entry.get('name') or entry.get('object_name')

    out = []
    for e in info['exports']:
        for pr in onslaught.read_props(info, ux, len(ua), e):
            if pr[0] != 'TextureParameterValues':
                continue
            raw = pr[2]
            try:
                count = struct.unpack_from('<i', raw, 0)[0]
            except Exception:                        # noqa: BLE001
                continue
            #: count, then the array's own element tag (16 for the two FNames, 8 for
            #: size and index, 8 for the struct name, 16 for its guid) and a has-guid
            #: byte, and only then the elements.
            o = 4 + 16 + 8 + 8 + 16 + 1
            for _k in range(min(count, 64)):
                fields, o = _tagged(raw, o, nm)
                if not fields:
                    break
                param = None
                pi = fields.get('ParameterInfo')
                if pi:
                    inner, _end = _tagged(pi[1], 0, nm)
                    got = inner.get('Name')
                    if got and len(got[1]) >= 4:
                        param = nm(struct.unpack_from('<i', got[1], 0)[0])
                val = fields.get('ParameterValue')
                tex = None
                if val and len(val[1]) >= 4:
                    tex = imp(struct.unpack_from('<i', val[1], 0)[0])
                if param or tex:
                    out.append((param, tex))
    return tuple(out)


@functools.lru_cache(maxsize=4096)
def texture_of(material_path):
    """The base-colour texture for a material, or None.

    A material instance imports every texture its master might use -- the railing
    material cites grass, sand, snow and rain -- so the one that belongs to it is
    the `_BC` in the material's **own folder**.

    Cached: a kit shares far fewer materials than it has meshes, and opening the
    material package for every mesh doubled the time to build the catalogue.
    """
    #: WHAT THE INSTANCE ASSIGNS, before any guessing. The parameter named `BC` is
    #: the base colour, and the FIRST one is the first layer -- which is the whole
    #: answer for a layered terrain material, where import order picks the wrong
    #: layer and paints grass brown.
    for param, tex in texture_parameters(material_path):
        #: A PATH or nothing. A leaf name looks plausible all the way to the browser
        #: and then 404s, so it is rejected here rather than three layers later.
        if (tex and str(tex).startswith('/')
                and str(param or '').upper() in ('BC', 'BASECOLOR', 'BASE_COLOR',
                                                 'DIFFUSE', 'ALBEDO')):
            return tex
    try:
        ua, ux = gamefiles.package(material_path)
    except Exception:                                # noqa: BLE001
        return None
    pkg = Package(ua, ux)
    pkg.load_payloads()
    folder = material_path.rsplit('/', 1)[0].lower()
    stem = material_path.rsplit('/', 1)[-1]
    packages = [name for cls, _o, name in _imports(pkg) if cls == 'Package']
    for suffix in BASE_COLOUR_SUFFIXES:
        got = _pick_texture(packages, folder, stem, suffix)
        if got:
            return got

    #: A LAYERED material has no texture of its own folder, and its layers are the
    #: real thing. `Ground_Dirt_Rock_Mud_MP` and `LavaDesertTest1_MAT` -- the terrain
    #: of Front Lawn and The Lava Pits -- keep their ground textures in the shared
    #: `GroundTextures` tree, so demanding a same-folder match left every terrain mesh
    #: untextured. Falling back is right for those; it was only ever wrong when a
    #: same-folder texture existed and got passed over, which is now handled above.
    #:
    #: Placeholders are skipped: `/MasterMaterials/Shared/` holds Black, White and
    #: Test1-4, which every master material imports and none of them means.
    for suffix in BASE_COLOUR_SUFFIXES:
        for name in packages:
            if not name.endswith(suffix):
                continue
            if '/MasterMaterials/Shared/' in name:
                continue
            return name
    #: Nothing rather than a placeholder: a mesh with no texture draws in a flat
    #: colour, which reads as "untextured" and is honest.
    return None


def _pick_texture(packages, folder, stem, suffix):
    """The base-colour texture for one suffix, or None."""
    candidates = [name for name in packages if name.endswith(suffix)]

    #: Match the FULL folder, not its last segment.
    #:
    #: The old test was `candidate_folder.endswith(material_folder.split('/')[-1])`,
    #: and that last segment is `Mat` for essentially every material in the game --
    #: so every candidate matched and the FIRST one won. A material instance imports
    #: every texture its master might use (grass, sand, snow, black, white), grass
    #: sorts first, and the result was grass-textured metal plates and palisade
    #: walls. Eight meshes were reported wrong; 150 were.
    same = [n for n in candidates if n.rsplit('/', 1)[0].lower() == folder]

    #: A folder holds several materials, so the folder alone is not enough:
    #: Orc_Walls/Mat has both Orc_PylonWall_BC and Orc_Ceiling_Wood_BC. The
    #: instance's own name says which -- `Orc_PylonWall_INST` -> `Orc_PylonWall_BC`.
    want = stem[:-len('_INST')] if stem.endswith('_INST') else stem
    for name in same:
        leaf = name.rsplit('/', 1)[-1]
        if leaf[:-len(suffix)].lower() == want.lower():
            return name
    #: Then a looser read of the same name, for instances with a suffix of their own
    #: (`DartSpitter_Static_INST` beside `DartSpitter_BC`).
    for name in same:
        leaf = name.rsplit('/', 1)[-1][:-len(suffix)].lower()
        if want.lower().startswith(leaf) or leaf.startswith(want.lower()):
            return name
    if same:
        return same[0]
    return None


def _half(h):
    """IEEE 754 binary16 -> float. UVs are stored this way."""
    sign = (h >> 15) & 1
    exponent = (h >> 10) & 0x1f
    mantissa = h & 0x3ff
    if exponent == 0:
        value = (mantissa / 1024.0) * (2.0 ** -14)
    elif exponent == 31:
        value = float('inf') if mantissa == 0 else float('nan')
    else:
        value = (1.0 + mantissa / 1024.0) * (2.0 ** (exponent - 15))
    return -value if sign else value


def texcoords(data, vb, vertices, sets):
    """The first UV set, as [(u, v)], or None.

    The vertex buffer holds tangents first -- 8 bytes a vertex -- then the UVs as
    `4 * NumTexCoords` bytes a vertex: two half floats per set. Only the first set
    is taken; the rest are lightmap coordinates.

    The tangent bytes are deliberately skipped rather than decoded. Reading them as
    a pair of `FPackedNormal` gives vectors 1.07 to 1.15 long instead of 1.0, so the
    layout is not what it appears, and nothing here needs them: three.js computes
    vertex normals from the triangles, and tangents matter only for normal mapping.
    """
    tan = vb + 24
    uv = tan + vertices * 8
    if uv + 8 > len(data):
        return None
    elem, count = struct.unpack_from('<2I', data, uv)
    if elem != 4 or count != vertices * sets:
        return None
    base = uv + 8
    stride = 4 * sets
    if base + stride * vertices > len(data):
        return None
    out = []
    for i in range(vertices):
        a, b = struct.unpack_from('<2H', data, base + i * stride)
        out.append([round(_half(a), 5), round(_half(b), 5)])
    return out


def read(name, catalogue=None):
    """{'verts', 'tris', 'triangles', 'vertices'} for one OMD3 mesh, or None."""
    catalogue = catalogue or meshcat.load()
    info = catalogue.get(name)
    if not info:
        return None
    lo = [info['origin'][i] - info['extent'][i] for i in range(3)]
    hi = [info['origin'][i] + info['extent'][i] for i in range(3)]
    ua, ux = gamefiles.package(info['path'])
    pkg = Package(ua, ux)
    pkg.load_payloads()
    for e in pkg.exports:
        cls = levelbuild.resolve(
            pkg, struct.unpack_from('<i', e['meta'], 0)[0])[1] or ''
        if cls != 'StaticMesh':
            continue
        data, start = cooked_region(pkg, e)
        got = section(data, start)
        if not got:
            return None
        tris, verts = got
        #: LOD0 streamed out: read the first LOD that is inline instead
        table = inline_lod(data, start, verts)
        fill_slack = POSITION_FILL_SLACK
        if table is not None:
            tris, verts = section(data, start, table)
            #: a lower LOD need not reach LOD0's bounds exactly
            fill_slack = max(fill_slack, INLINE_LOD_FILL * max(info['extent']))
        vb = vertex_buffer(data, start, verts, unaligned=table is not None)
        sets = struct.unpack_from('<I', data, vb)[0] if vb else 1
        after = uv_end(data, vb, verts, sets) if vb else None

        # Positions and indices are chosen **together**. Either alone has several
        # readings that pass their own test, and only the pair says which is real:
        # the right positions with the right index buffer give triangles with area,
        # and every other combination gives a mesh full of degenerate ones.
        # EVERY candidate is scored and the best kept -- no early exit.
        #
        # The candidates sit twelve bytes apart, which is one whole vertex, so a
        # near-miss is the right buffer read one vertex late: positions pair with
        # the wrong indices and the mesh comes out subtly deformed everywhere
        # while still scoring well. `Stairs_3x_2_SB` offers seven candidates whose
        # scores fall 0.3, 0.3, 0.2, 0.2, 0.2, 0.0, 0.0 -- stopping at the first
        # one under a threshold takes a wrong reading that looks almost right.
        #
        # Ties go to the later offset, because the real buffer ends where the
        # vertex buffer begins and a spurious match can only start earlier.
        #
        # The positions are searched BACK from the vertex buffer rather than forward
        # from the start of the export: the position buffer ends exactly where the
        # vertex buffer begins, so the answer is within a few bytes of
        # `vb - verts * 12`. Scanning from `start` walked the whole export a byte at a
        # time, unpacking every vertex at every offset that happened to begin with a
        # plausible point -- `PineTree_01` is 10,330 vertices in a 1.1 MB export, so
        # that was on the order of a billion unpacks and took minutes.
        # The buffer's own array header says where it is, so the scan below is a
        # fallback rather than the route. Without this a mesh whose vertex buffer was
        # not located had no anchor at all and the search walked the whole export
        # unpacking every vertex at every offset: `SM_Ice_01` and the desert mountains
        # never finished, which is why 11 of 1,166 meshes stayed uncached.
        found = [(at, pts) for at, pts
                 in ((a, points_at(data, a, verts, lo, hi, slack=fill_slack))
                     for a in position_headers(data, start, verts, limit=vb))
                 if pts]
        window = POSITION_WINDOW if vb else None
        first = start
        reach_to = vb
        if vb:
            first = max(start, vb - verts * 12 - window)
        else:
            reach_to = min(len(data), start + FALLBACK_REACH)
        # How far past the UV array the index buffer can start. What sits between is a
        # colour buffer at four bytes a vertex and a couple of array headers, so a few
        # times that is generous; the default 4 MB meant every rejected candidate cost
        # a full-export scan, and `Jar` pays for fourteen of them.
        reach = verts * 8 + 8192
        # The index buffer's own header says where it is and how wide its elements are,
        # so it is read rather than scanned for. The scan stays as the fallback.
        #
        # Sorted, and the EARLIEST acceptable buffer wins -- which is what the scan did.
        # UE keeps several buffers per LOD with the same triangle count (depth-only, and
        # the reversed-winding one), so they all match the same header and all score the
        # same zero degeneracy. Taking the last instead of the first changed the geometry
        # of 20 of 140 meshes while leaving their vertex and triangle counts identical.
        # From the end of the UV array where that is known, which is where the scan
        # began: searching the whole region instead reaches buffers belonging to earlier
        # sections, and `WallSec_3_4_V2_RB` then read a different one.
        spots = sorted(index_headers(
            data, tris, start=after if after is not None else start))
        best = None
        for at, pts in (found or position_candidates(
                data, first, verts, lo, hi, limit=reach_to)):
            runs = [(i, run) for i, run
                    in ((i, index_run(data, i, tris, verts, w)) for i, w in spots)
                    if run]
            if not runs:
                base = after if after is not None else at + verts * 12
                iat, idx = indices(data, base, tris, verts, verts=pts, window=reach)
                if idx is None and vb:
                    iat, idx = indices(data, vb, tris, verts, verts=pts, window=reach)
                runs = [(iat, idx)] if idx else []
            pick = None
            for _iat, idx in runs:
                share = _degenerate_share(pts, idx)
                if share <= DEGENERATE_LIMIT:
                    pick = (share, idx)
                    break
                if pick is None or share < pick[0]:
                    pick = (share, idx)
            if pick is None:
                continue
            # Ties across POSITION candidates still go to the later offset: the real
            # buffer ends where the vertex buffer begins, so a spurious match can only
            # start earlier.
            if best is None or pick[0] <= best[0]:
                best = (pick[0], at, pts, pick[1])
        if best is None:
            return None
        _share, at, pts, idx = best

        uvs = texcoords(data, vb, verts, sets) if vb else None
        mat = material_of(pkg)

        #: PER-SECTION materials. A section names a slot in `StaticMaterials`, and
        #: 117 meshes declare more than one -- `Orc_Decor3` has three, and its slots
        #: are ordered B, plain, A, so neither the first import nor the section number
        #: gives the right one. Without this a multi-material mesh wears one of its
        #: materials everywhere.
        slots, secs = [], []
        try:
            import tempfile as _tf
            from riftlord.formats.ue.uheader import parse as _ph
            _root = _tf.mkdtemp(prefix='omd3slots-')
            _staged = os.path.join(_root, 'a.uasset')
            with open(_staged, 'wb') as _fh:
                _fh.write(ua)
            _hdr = _ph(_staged)
            slots = material_slots(_hdr, ux, len(ua))
            for _m, _first, _tris, _lo, _hi in (sections(data, start, table) or []):
                secs.append({'slot': _m, 'first': _first, 'tris': _tris})
        except Exception:                            # noqa: BLE001
            slots, secs = [], []
        slot_tex = [texture_of(m) if m else None for m in slots]

        return {'material': mat, 'texture': texture_of(mat) if mat else None,
                'slots': slots, 'slotTextures': slot_tex, 'sections': secs,
                'verts': [[round(v, 3) for v in p] for p in pts],
                'tris': idx, 'uvs': uvs, 'triangles': tris, 'vertices': verts,
                'path': info['path']}
    return None


def surface_area(model):
    total = 0.0
    v, t = model['verts'], model['tris']
    for i in range(0, len(t) - 2, 3):
        p, q, r = v[t[i]], v[t[i + 1]], v[t[i + 2]]
        a = [q[k] - p[k] for k in range(3)]
        b = [r[k] - p[k] for k in range(3)]
        cr = (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
              a[0] * b[1] - a[1] * b[0])
        total += math.sqrt(sum(c * c for c in cr)) / 2.0
    return total


def build(out_path=MODELS, limit=None):
    cat = meshcat.load()
    names = sorted(cat)
    if limit:
        names = names[:limit]
    out, why = {}, collections.Counter()
    for name in names:
        try:
            model = read(name, cat)
        except Exception as exc:                     # noqa: BLE001
            why[type(exc).__name__] += 1
            continue
        if not model:
            why['could not locate the buffers'] += 1
            continue
        out[name] = model
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
    ap.add_argument('--limit', type=int)
    args = ap.parse_args(argv)

    if args.mesh:
        m = read(args.mesh)
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

    if args.build:
        out, why = build(limit=args.limit)
        print('read %d OMD3 model(s)' % len(out))
        for reason, n in why.most_common(5):
            print('   %5d  %s' % (n, reason))
        print('   %d triangles in total'
              % sum(len(m['tris']) // 3 for m in out.values()))
        return 0

    ap.error('need --mesh or --build')


if __name__ == '__main__':
    raise SystemExit(main())
