"""Read OMD3's cooked SKELETAL meshes, so a skeletal actor can be drawn.

A trap like the grinder pit or the cauldron is a `SkeletalMesh`, and the static-mesh
reader (`omd3mesh`) cannot touch one: a `UStaticMesh` keeps its render data in
`FStaticMeshLODResources` and a `USkeletalMesh` in `FSkeletalMeshLODRenderData`, which
is a different layout with bone influences in the middle of it. Rift Lord drew nothing
at all where those traps stand.

**The reference pose is already in the buffer.** Skinning is applied at runtime from
the bind pose, so `FPositionVertexBuffer` holds positions we can draw directly -- no
skeleton, no bone weights, no animation. That is the whole reason this is a short
module rather than a second mesh pipeline: for an editor that needs to show where a
trap sits, the bind pose IS the answer.

Nothing here parses the header. `USkeletalMesh::Serialize` writes strip flags, bounds,
materials and the reference skeleton before the render data, and reading that chain
needs the exact engine version; two attempts at it produced bounds of
(399, 453, 369) with a sphere radius of 283, which cannot describe the same box. Every
buffer below is instead found by a signature it states about itself, which is the same
method that cracked the static meshes and is indifferent to the version.
"""

import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.core import gamefiles  # noqa: E402
from riftlord.build import levelbuild  # noqa: E402
from riftlord.formats.omd3 import omd3mesh  # noqa: E402
from riftlord.build.levelbuild import Package  # noqa: E402

#: `FPositionVertexBuffer` writes `Stride` (always 12) and `NumVertices`, then a TArray
#: that repeats both: element size 12, count the same. Four numbers, two of them equal
#: and two of them 12, is a signature nothing else in these files produced across 603
#: skeletal assets -- and it yields the vertex count for free.
POSITION_STRIDE = 12

#: A sane bound on the vertex count, so a pair of coincidental 12s cannot name a
#: buffer that runs off the end. The largest LOD0 measured is 37,771 (GrinderPit_4X).
MAX_VERTICES = 4_000_000

#: `FMultiSizeIndexContainer` writes the element width as a byte and then a TArray that
#: repeats it, so an index buffer is 2 or 4 bytes wide. Every mesh measured is 16-bit,
#: which matches the static meshes: 32-bit only appears past 65,536 vertices.
INDEX_WIDTHS = (2, 4)

#: How many triangles to sample when judging whether a candidate index buffer is the
#: real one. The decoys UE keeps per LOD -- depth-only, reversed, adjacency -- are full
#: of degenerate triangles when read as a triangle list; a render buffer has none.
DEGENERATE_SAMPLE = 600


def _f16(h):
    """One IEEE half. UVs are `FVector2DHalf` unless the mesh asked for full precision."""
    sign, exp, man = (h >> 15) & 1, (h >> 10) & 0x1F, h & 0x3FF
    if exp == 0:
        return ((-1) ** sign) * (man / 1024.0) * 2.0 ** -14
    if exp == 31:
        return 0.0
    return ((-1) ** sign) * (1.0 + man / 1024.0) * 2.0 ** (exp - 15)


def _vertex_buffer_agrees(data, pos_at, count):
    """Does a `FStaticMeshVertexBuffer` sit after this position buffer and agree?

    The gate that makes the signature trustworthy on a big mesh. `Cyclops_Medium_SKM`
    has 1,456 places where four little-endian words read as (12, n, 12, n) -- inside
    skin weights and morph targets, which are full of small numbers -- and calling
    those LODs was nonsense. A real position buffer is followed by the vertex buffer
    for the same LOD, which states `NumVertices` again and whose tangent array has one
    element per vertex; coincidences do not survive that.
    """
    at = pos_at + 16 + POSITION_STRIDE * count
    if at + 26 > len(data):
        return False
    ntex, nvert = struct.unpack_from('<2i', data, at + 2)
    if nvert != count or not 1 <= ntex <= 8:
        return False
    tan_size, tan_count = struct.unpack_from('<2I', data, at + 18)
    return tan_count == count and tan_size in (8, 16)


def position_buffers(data, start):
    """[(offset of the TArray header, vertex count)] per LOD, largest first."""
    out = []
    o, end = start, len(data) - 16
    while o < end:
        o = data.find(b'\x0c\x00\x00\x00', o, end)
        if o < 0:
            break
        count = struct.unpack_from('<I', data, o + 4)[0]
        if 8 <= count <= MAX_VERTICES:
            size2, count2 = struct.unpack_from('<2I', data, o + 8)
            if (size2 == POSITION_STRIDE and count2 == count
                    and o + 16 + POSITION_STRIDE * count <= len(data)
                    and _vertex_buffer_agrees(data, o, count)):
                out.append((o, count))
        o += 1
    return sorted(out, key=lambda r: -r[1])


def _degenerate(idx, limit=DEGENERATE_SAMPLE):
    bad = 0
    for i in range(0, min(len(idx), limit * 3), 3):
        a, b, c = idx[i], idx[i + 1], idx[i + 2]
        if a == b or b == c or a == c:
            bad += 1
    return bad


def index_buffer(data, start, pos_at, vertices):
    """(indices, width) for LOD0, or None.

    `FSkeletalMeshLODRenderData` writes the indices BEFORE the vertex buffers, so the
    search runs from the start of the cooked region up to the position buffer.

    Two conditions pick it out, and both are needed: the highest index must be exactly
    `vertices - 1`, because a render buffer references every vertex it has, and no
    sampled triangle may be degenerate. On GrinderPit_4X the second-largest candidate
    is also degenerate-free -- 68,166 indices -- and is rejected only by its highest
    index being 15,274 rather than 37,770.
    """
    best = None
    o = start
    while o < pos_at - 8:
        width, count = struct.unpack_from('<2I', data, o)
        if (width in INDEX_WIDTHS and count >= 3 and count % 3 == 0
                and o + 8 + width * count <= pos_at + 64
                and (best is None or count > len(best[0]))):
            try:
                idx = struct.unpack_from(
                    '<%d%s' % (count, 'H' if width == 2 else 'I'), data, o + 8)
            except struct.error:
                o += 1
                continue
            if max(idx) == vertices - 1 and not _degenerate(idx):
                best = (idx, width, o)
        o += 1
    return best


def uvs_at(data, pos_at, vertices):
    """[(u, v)] per vertex from the `FStaticMeshVertexBuffer` after the positions.

    Its header is a two-byte prefix, then `NumTexCoords`, `NumVertices`, and flags for
    full-precision UVs and high-precision tangents, then the tangent TArray (8 bytes a
    vertex) and the texcoord TArray.

    The texcoord array is element 4 with count `vertices * NumTexCoords`, NOT element
    `4 * NumTexCoords` with count `vertices`: FallingBoomBarrel has two UV sets and
    47,404 elements for 23,702 vertices, which is what settled it. Only set 0 is read,
    because set 1 is the lightmap.
    """
    at = pos_at + 16 + POSITION_STRIDE * vertices
    if at + 26 > len(data):
        return None
    ntex, nvert, full_uv, _hi = struct.unpack_from('<4i', data, at + 2)
    if nvert != vertices or not 1 <= ntex <= 8:
        return None
    tan_size, tan_count = struct.unpack_from('<2I', data, at + 18)
    if tan_count != vertices:
        return None
    head = at + 26 + tan_size * tan_count
    if head + 8 > len(data):
        return None
    size, count = struct.unpack_from('<2I', data, head)
    wide = bool(full_uv)
    if size != (8 if wide else 4) or count != vertices * ntex:
        return None
    body = head + 8
    out = []
    for i in range(vertices):
        o = body + (i * ntex) * size
        if wide:
            u, v = struct.unpack_from('<2f', data, o)
        else:
            hu, hv = struct.unpack_from('<2H', data, o)
            u, v = _f16(hu), _f16(hv)
        out.append([round(u, 5), round(v, 5)])
    return out


def _skeletal_export(pkg):
    for e in pkg.exports:
        cls = levelbuild.resolve(
            pkg, struct.unpack_from('<i', e['meta'], 0)[0])[1] or ''
        if cls == 'SkeletalMesh':
            return e
    return None


def asset_path(name, cache={}):
    """The `/Game/...` path of a skeletal mesh by asset name, or None."""
    if not cache:
        for path in gamefiles.index():
            if path.endswith('.uasset'):
                cache.setdefault(path.rsplit('/', 1)[-1][:-len('.uasset')], path)
    got = cache.get(name)
    return got.replace('../../../OMD/Content', '/Game')[:-len('.uasset')] if got else None


#: Where `USkeletalMesh::Serialize` puts its `Materials` array, measured.
#:
#: The source says StripFlags then `ImportedBounds` (an FBoxSphereBounds, 28 bytes)
#: then `Ar << GetMaterials()`, which would put the count at +30. It is at +34, and the
#: four bytes between are unexplained -- so these are the MEASURED offsets, confirmed
#: on six meshes whose count then matches the number of material slots the sections
#: use. Reading the nominal layout gave a "count" of 1,143,098,959, which is a float
#: bit pattern.
MATERIALS_COUNT_AT = 34
MATERIALS_FIRST_AT = 38

#: One `FSkeletalMaterial`: the material reference, its slot FName, a bool and
#: `FMeshUVChannelInfo`. Measured as a flat 40 from the spacing of six meshes'
#: material references.
MATERIAL_STRIDE = 40


def material_slots(pkg, data, start):
    """The material path of each slot, IN SLOT ORDER, which is what a section names.

    Not the import order. `Archer_Bank_Base_SKM` imports Base then Trim and its slots
    are Trim then Base, so taking imports in order paints the two sections the wrong
    way round -- the same trap the static meshes have with `Orc_Decor3`.

    `Ballista_SKM` has FOUR slots and only three distinct materials, because two slots
    share one. That is also why its four sections looked like a contradiction against a
    count of three materials.
    """
    imports = omd3mesh._imports(pkg)

    def path_of(index):
        """An import's outer package path -- what `texture_of` needs."""
        if index >= 0 or -index - 1 >= len(imports):
            return None
        cls, outer, _name = imports[-index - 1]
        if cls not in ('MaterialInstanceConstant', 'Material'):
            return None
        if outer < 0 and -outer - 1 < len(imports):
            ocls, _oo, oname = imports[-outer - 1]
            if ocls == 'Package' and str(oname).startswith('/'):
                return str(oname)
        return None

    at = start + MATERIALS_COUNT_AT
    if at + 4 > len(data):
        return []
    count = struct.unpack_from('<i', data, at)[0]
    if not 1 <= count <= 64:
        return []
    out = []
    for k in range(count):
        o = start + MATERIALS_FIRST_AT + MATERIAL_STRIDE * k
        if o + 4 > len(data):
            return []
        out.append(path_of(struct.unpack_from('<i', data, o)[0]))
    return out


def _walk_section(data, o):
    """(material slot, first index, triangles, end offset) for one render section.

    Field for field from `operator<<(FArchive&, FSkelMeshRenderSection&)` in 4.27.2.
    Two things make it readable at all, and neither is guessable:

    * it opens with `FStripDataFlags`, TWO BYTES before `MaterialIndex` -- every scan
      that looked for the material/base/triangle triple at the record start failed on
      exactly that;
    * a `bool` serialises as **32 bits** ("as if it were UBOOL (legacy, 32 bit int)"),
      so the three flags cost 4 bytes each, not 1.
    """
    p = o + 2                                        # FStripDataFlags
    mat = struct.unpack_from('<H', data, p)[0]
    p += 2
    base, tris = struct.unpack_from('<2I', data, p)
    p += 8
    p += 4 + 1 + 4 + 4   # bRecomputeTangent, mask channel, bCastShadow, BaseVertexIndex
    n = struct.unpack_from('<I', data, p)[0]
    p += 4 + 64 * n                                  # ClothMappingData
    n = struct.unpack_from('<I', data, p)[0]
    p += 4 + 2 * n                                   # BoneMap
    p += 4 + 4 + 2 + 20   # NumVertices, MaxBoneInfluences, CorrespondCloth, ClothingData
    n = struct.unpack_from('<I', data, p)[0]
    p += 4 + 4 * n                                   # DuplicatedVerticesBuffer.DupVertData
    n = struct.unpack_from('<I', data, p)[0]
    p += 4 + 8 * n                                   # .DupVertIndexData
    p += 4                                           # bDisabled
    return mat, base, tris, p


def sections(data, start, idx_header, triangles):
    """[(slot, first index, triangles)] for LOD0, or [].

    Anchored BACKWARD from the index buffer, which is the only way this is findable.
    Per LOD, 4.27.2 writes StripFlags, `bIsLODCookedOut`, `bInlined`, `RequiredBones`,
    **`RenderSections`**, `ActiveBoneIndices`, `BuffersSize`, then the streamed block --
    and the streamed block opens with StripFlags (2) and the index container's
    `DataTypeSize` (1) before the index array's own header. So `ActiveBoneIndices` ends
    exactly 7 bytes before that header, and the sections must end where it begins.

    `SerializeStreamedData` has `//Ar << RenderSections;` COMMENTED OUT with a TODO,
    which is why hunting for the table between the cooked region and the index buffer
    with no anchor kept finding coincidences instead.
    """
    abi_end = idx_header - 3 - 4
    for c in range(start, max(start, abi_end - 8)):
        count = struct.unpack_from('<i', data, c)[0]
        if not 1 <= count <= 32:
            continue
        p, recs = c + 4, []
        try:
            for _k in range(count):
                mat, base, tris, p = _walk_section(data, p)
                recs.append((mat, base, tris))
        except Exception:                            # noqa: BLE001
            continue
        #: `ActiveBoneIndices` must then run from here to exactly where the streamed
        #: block begins -- one equation, so its length is solved rather than searched.
        room = abi_end - p - 4
        if room < 0 or room % 2:
            continue
        if struct.unpack_from('<I', data, p)[0] != room // 2:
            continue
        if recs[0][1] != 0 or sum(r[2] for r in recs) != triangles:
            continue
        return [(m, b, t) for m, b, t in recs]
    return []


def _material_path(pkg):
    """The `/Game/...` path of the mesh's material, or None.

    The material comes from the IMPORTS, the same place a static mesh's does:
    `CauldronTrap_SKM` imports `Cauldron_Trap_INST`, and the components that place
    these traps carry no override, so the asset's own material is the only one there
    is.

    The PATH, not the import's object name. `texture_of` takes a path and opens it;
    handed a bare `Cauldron_Trap_INST` it returns None and the trap draws grey -- the
    same mistake that once turned every mesh in the editor black. An import's path is
    its outer `Package`, and a negative package index of -N is import N-1.
    """
    imports = omd3mesh._imports(pkg)
    for cls, outer, _name in imports:
        if cls not in ('MaterialInstanceConstant', 'Material'):
            continue
        if outer < 0 and -outer - 1 < len(imports):
            cls2, _o2, name2 = imports[-outer - 1]
            if cls2 == 'Package' and str(name2).startswith('/'):
                return str(name2)
    return None


def read(name):
    """The same payload shape `omd3mesh.read` returns, for a skeletal mesh, or None."""
    path = asset_path(name)
    if not path:
        return None
    ua, ux = gamefiles.package(path)
    pkg = Package(ua, ux)
    pkg.load_payloads()
    export = _skeletal_export(pkg)
    if not export:
        return None
    data, start = omd3mesh.cooked_region(pkg, export)
    lods = position_buffers(data, start)
    if not lods:
        return None
    pos_at, vertices = lods[0]
    got = index_buffer(data, start, pos_at, vertices)
    if not got:
        return None
    idx, _width, idx_header = got
    pts = [list(struct.unpack_from('<3f', data, pos_at + 16 + POSITION_STRIDE * i))
           for i in range(vertices)]
    #: PER-SECTION materials. A section names a SLOT, and a skeletal mesh has as many
    #: slots as sections -- `Ballista_SKM` has four of each. Without this a
    #: multi-material mesh wears one of its materials everywhere, which is 35% of them.
    slots = material_slots(pkg, data, start)
    secs = [{'slot': m, 'first': b, 'tris': t}
            for m, b, t in sections(data, start, idx_header, len(idx) // 3)]
    mat = (slots[0] if slots else None) or _material_path(pkg)
    return {'material': mat, 'texture': omd3mesh.texture_of(mat) if mat else None,
            'slots': slots, 'sections': secs,
            'slotTextures': [omd3mesh.texture_of(m) if m else None for m in slots],
            'verts': [[round(v, 3) for v in p] for p in pts],
            'tris': list(idx), 'uvs': uvs_at(data, pos_at, vertices) or [],
            'triangles': len(idx) // 3, 'vertices': vertices,
            'skeletal': True, 'lods': len(lods), 'indexWidth': _width,
            'path': path}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('names', nargs='*', default=None)
    ap.add_argument('--sweep', type=int, metavar='N',
                    help='read N skeletal assets and report how many decode')
    args = ap.parse_args(argv)
    if args.sweep:
        names = sorted({p.rsplit('/', 1)[-1][:-len('.uasset')]
                        for p in gamefiles.index()
                        if p.endswith('_SKM.uasset')})[:args.sweep]
    else:
        names = args.names or ['CauldronTrap_SKM']
    ok = 0
    for n in names:
        try:
            got = read(n)
        except BaseException as exc:                 # noqa: BLE001
            print('%-34s raised %s: %s' % (n, type(exc).__name__, str(exc)[:60]))
            continue
        if not got:
            print('%-34s no' % n)
            continue
        ok += 1
        lo = [min(v[i] for v in got['verts']) for i in range(3)]
        hi = [max(v[i] for v in got['verts']) for i in range(3)]
        print('%-34s %6d verts %6d tris %d LOD  idx%d  uv %-5s  %-22s  %s..%s'
              % (n, got['vertices'], got['triangles'], got['lods'],
                 got['indexWidth'], bool(got['uvs']), got['material'],
                 [round(v) for v in lo], [round(v) for v in hi]))
    print('%d of %d decoded' % (ok, len(names)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
