# Generated from OMD2-Converter/thirdparty/fabconvert/meshconvert.py by thirdparty/fabconvert/port_riftlord.py; edit the source.
"""Read a pack's EDITOR static mesh and turn it into an omd3smwrite description.

    src = read(path)                      # triangle corners, slots, collision, settings
    desc, slots, info = build(src, scale, slot_plans)

Differences from fabmesh.read (the reader rounds sm1 / mat2 used):
* corners stay unwelded until every per-slot change is made (UV frames, two-sided copies,
  vertex-colour rewrites), then weld on identical attributes;
* normals that the source asks to be recomputed (bRecomputeNormals, or no usable stored
  normals) are rebuilt with the source's HARD EDGES respected: corners of one vertex share a
  normal only across soft edges (UE's MeshDescription semantics), angle-weighted;
* vertex colours keep the linear floats so the value the material saw (FColor sRGB-encoded,
  read back raw) can be reproduced.
"""

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path[:0] = [REPO, os.path.join(REPO, 'UE Assets'), os.path.join(REPO, 'thirdparty', 'survey-tools')]

from riftlord.fab import fabmesh                                      # noqa: E402
from riftlord.fab.survey import meshgeo                                      # noqa: E402
from riftlord.fab.survey import pkgread                                      # noqa: E402
from riftlord.fab.survey.meshgeo import R                               # noqa: E402

from riftlord.fab import matgraph as mg                               # noqa: E402

#: FRawMesh carries this many UV channels (MAX_MESH_TEXTURE_COORDS).
RAWMESH_MAX_UV = 8
#: A stored normal shorter than this counts as missing.
NORMAL_MIN = 1e-6
#: Two corners belong to the same vertex fan when they share a vertex and a soft edge.
#: Smoothing without edge data falls back to welding by position (omd3smwrite.vertex_normals).
#: A face (above this many uu^2) whose UV0 area is at most COLLAPSED_UV_MAX of its world area
#: samples one line or point of its texture. Packs do this on purpose (City of Brass maps whole
#: wall faces onto one row of a trim sheet for a flat colour band), so it is reported, not warned.
COLLAPSED_UV_MIN_AREA = 1.0
COLLAPSED_UV_MAX = 1e-9
#: World-planar projection: a face projects on the plane its normal is most along.
PROJ_AXES = {0: (1, 2), 1: (0, 2), 2: (0, 1)}


def read(path):
    from riftlord.fab import fbxpack
    if fbxpack.is_mesh_path(path):
        return fbxpack.read(path)
    p = pkgread.Package(path)
    sm = [e for e in p.exports if p.class_of(e) == 'StaticMesh' and e['outer'] == 0][0]
    props, end = p.props(sm)
    edver = meshgeo.custom(p, meshgeo.ED_GUID) or 0
    if p.ue5:
        md = _md_ue5(p)
    else:
        r = R(p.d, end)
        r.i32(); r.raw(2); r.i32(); r.i32(); r.i32()
        r.fstr(); r.u32(); r.raw(16)
        r.raw(4 * r.i32())
        if edver < meshgeo.ED_RAWMESH_DEPRECATED:
            md = _md_rawmesh(meshgeo.bulk_bytes(p, meshgeo.bulk_header(r, p)))
        else:
            if not r.i32():
                raise ValueError('no mesh description')
            h = meshgeo.bulk_header(r, p)
            md = fabmesh.mesh_description(meshgeo.bulk_bytes(p, h), edver)
    remap = _section_material_map(p, props)
    if remap:
        g = md['tri_group']
        md['tri_group'] = np.array([remap.get(int(x), int(x)) for x in range(int(g.max()) + 1)], np.int64)[g] if len(g) else g
    settings = fabmesh.build_settings(p, props)
    sm_mats = pkgread.find(props, 'StaticMaterials')
    slots = []
    for it in (sm_mats or {}).get('items', []):
        g = {x['name']: x.get('value') for x in it}
        slots.append((g.get('MaterialSlotName'), p.obj_path(g.get('MaterialInterface') or 0)))
    if slots and not any(m for _s, m in slots):
        slots = _native_static_materials(p, sm, end, slots) or slots
    body = [e for e in p.exports if p.class_of(e) == 'BodySetup']
    coll = fabmesh.collision(p, body[0]) if body else {'boxes': [], 'convex': [], 'sphyls': 0, 'trace': 'default'}
    lmi = pkgread.find(props, 'LightMapCoordinateIndex')
    return {'md': md, 'slots': slots, 'collision': coll, 'settings': settings, 'path': path,
            'lightmap_index': lmi.get('value') if lmi else None}


#: Bytes of one natively serialised FStaticMaterial (UE 4.14-4.15): MaterialInterface (int32),
#: MaterialSlotName and ImportedMaterialSlotName (FName each), FMeshUVChannelInfo (bInitialized,
#: bOverrideDensities, 4 floats).
NATIVE_STATIC_MATERIAL = 44


def _native_static_materials(p, sm, tags_end, slots):
    """[(slot name, material path)] from the StaticMaterials array a UE 4.15 StaticMesh writes
    after its tagged properties (the tagged copy has no MaterialInterface; Modular Medieval Sewer
    Dungeon), found as a count equal to the slot count followed by records whose slot names
    match; None when not found."""
    import struct
    d, end = p.d, sm['offset'] + sm['size']
    n = len(slots)
    for o in range(tags_end, end - 4 - n * NATIVE_STATIC_MATERIAL + 1):
        if struct.unpack_from('<i', d, o)[0] != n:
            continue
        recs = []
        for k in range(n):
            r = o + 4 + k * NATIVE_STATIC_MATERIAL
            mat, name = struct.unpack_from('<iq', d, r)
            if p.fname(name) != slots[k][0]:
                break
            recs.append((slots[k][0], p.obj_path(mat) if mat else None))
        if len(recs) == n:
            return recs
    return None


def _section_material_map(p, props):
    """{section: material slot} of LOD 0 from the StaticMesh's SectionInfoMap. A section (a
    face material index / polygon group) is not its slot number: Eastern Village
    SM_roof_canopy_06 maps section 1 to slot 3 and 3 to 1, which drew its roof in concrete."""
    sim = pkgread.find(props, 'SectionInfoMap')
    m = next((c for c in (sim or {}).get('children', []) if c['name'] == 'Map'), None)
    if m is None:
        return {}
    r = pkgread.R(p.d, m['at'])
    stop = m['at'] + m['size']
    out = {}
    try:
        r.i32()                                          # keys to remove
        for _ in range(r.i32()):
            key = r.u32()
            tags = p._tags(r, stop)
            idx = next((t.get('value') for t in tags if t['name'] == 'MaterialIndex'), None)
            if key >> 16 == 0 and idx is not None:
                out[key & 0xFFFF] = int(idx)
    except Exception:                                    # noqa: BLE001 - keep the identity
        return {}
    return out


def _fcolor_linear(cols):
    """FColor (BGRA bytes in a little-endian uint32, sRGB) -> linear float RGBA, as the
    MeshDescription stores vertex colour."""
    c = np.asarray(cols, np.uint32)
    b, g, r, a = (c & 255), (c >> 8) & 255, (c >> 16) & 255, (c >> 24) & 255
    rgb = mg.srgb_to_linear(np.stack([r, g, b], -1).astype(np.float64) / 255.0)
    return np.concatenate([rgb, (a.astype(np.float64) / 255.0)[:, None]], -1).astype(np.float32)


def _md_rawmesh(b):
    """UE 4.20/4.21 FRawMesh (StaticMesh source data before MeshDescription) in the same shape
    fabmesh.mesh_description returns: every wedge is a vertex instance."""
    r = meshgeo.R(b)
    r.i32(); r.i32()                                     # version, licensee version
    fmi = meshgeo.tarr(r, '<i4')                         # FaceMaterialIndices
    meshgeo.tarr(r, '<u4')                               # FaceSmoothingMasks (normals are stored)
    pos = meshgeo.tarr(r, '<f4', 3)
    wedge = meshgeo.tarr(r, '<u4')
    tx = meshgeo.tarr(r, '<f4', 3)
    ty = meshgeo.tarr(r, '<f4', 3)
    tz = meshgeo.tarr(r, '<f4', 3)
    uvs = [meshgeo.tarr(r, '<f4', 2) for _ in range(RAWMESH_MAX_UV)]
    cols = meshgeo.tarr(r, '<u4')
    n = len(wedge)
    nt = n // 3
    sign = None
    if len(tx) == n and len(ty) == n and len(tz) == n:
        sign = np.where((np.cross(tz, tx) * ty).sum(1) < 0, -1.0, 1.0).astype(np.float32)
    return {
        'positions': np.asarray(pos, np.float32),
        'inst_vertex': np.asarray(wedge, np.int64),
        'tri_inst': np.arange(nt * 3, dtype=np.int64).reshape(nt, 3),
        'tri_group': np.asarray(fmi[:nt], np.int64),
        'normal': np.asarray(tz, np.float32) if len(tz) == n else None,
        'tangent': np.asarray(tx, np.float32) if len(tx) == n else None,
        'sign': sign,
        'color': _fcolor_linear(cols) if len(cols) == n else None,
        'uvs': [np.asarray(u, np.float32) for u in uvs if len(u) == n],
        'edge_vertices': None, 'edge_hard': None,
        'slot_names': [],
    }


def _md_ue5(p):
    """UE 5.0-5.2 MeshDescription (FCompressedBuffer payload) in the same shape. Each attribute
    is a labelled block: 7 uint32 (type, extent, count, channels, ...) then the first channel;
    a further channel is (1, element size, count) then its data (measured, Winter City)."""
    import struct
    payloads = meshgeo.ue5_payloads(p.d)
    if not payloads:
        raise ValueError('no compressed payload')
    b = sorted(payloads, key=lambda x: -len(x[1]))[0][1]
    labs = meshgeo._labels(b)

    def block(name, k=0):
        if name not in labs or len(labs[name]) <= k:
            return None, 0, []
        o = labs[name][k]
        t, ext, n, nch, _e2, esz, cnt = struct.unpack_from('<7I', b, o)
        dt, w = meshgeo.DT5[t]
        width = esz // np.dtype(dt).itemsize
        o += 28
        chans = []
        for c in range(max(nch, 1)):
            if c:
                _one, esz, cnt = struct.unpack_from('<3I', b, o)
                o += 12
            a = np.frombuffer(b, dt, cnt * esz // np.dtype(dt).itemsize, o)
            o += cnt * esz
            chans.append(a.reshape(cnt, -1) if width > 1 else a)
        return chans[0], nch, chans
    pos, _n, _c = block('Position')
    inst_vertex, _n, _c = block('VertexIndex', 0)
    tri_inst, _n, _c = block('VertexInstanceIndex')
    group, _n, _c = block('PolygonGroupIndex')
    normal, _n, _c = block('Normal')
    tangent, _n, _c = block('Tangent')
    sign, _n, _c = block('BinormalSign')
    color, _n, _c = block('Color')
    _uv0, _n, uv_chans = block('TextureCoordinate')
    return {
        'positions': np.asarray(pos, np.float32),
        'inst_vertex': np.asarray(inst_vertex, np.int64),
        'tri_inst': np.asarray(tri_inst, np.int64).reshape(-1, 3),
        'tri_group': np.asarray(group, np.int64),
        'normal': None if normal is None else np.asarray(normal, np.float32),
        'tangent': None if tangent is None else np.asarray(tangent, np.float32),
        'sign': None if sign is None else np.asarray(sign, np.float32),
        'color': None if color is None else np.asarray(color, np.float32),
        'uvs': [np.asarray(u, np.float32) for u in uv_chans],
        'edge_vertices': None, 'edge_hard': None,
        'slot_names': [],
    }


def _unit(v):
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(n, 1e-20)


def corner_normals(P, tri_vertex, edge_vertices, edge_hard):
    """Per corner (T,3,3) smoothing-group normals: for each vertex, the corners around it are
    split into fans connected through soft edges; each fan gets the angle-weighted sum of its
    faces' normals (UE winding: front = (p2-p0) x (p1-p0))."""
    T = len(tri_vertex)
    p0, p1, p2 = P[tri_vertex[:, 0]], P[tri_vertex[:, 1]], P[tri_vertex[:, 2]]
    fn = _unit(np.cross(p2 - p0, p1 - p0))
    ang = np.zeros((T, 3))
    for c in range(3):
        a = P[tri_vertex[:, c]]
        b = P[tri_vertex[:, (c + 1) % 3]]
        d = P[tri_vertex[:, (c + 2) % 3]]
        u, w = _unit(b - a), _unit(d - a)
        ang[:, c] = np.arccos(np.clip((u * w).sum(1), -1, 1))
    hard = set()
    if edge_vertices is not None and edge_hard is not None:
        for (a, b), hd in zip(edge_vertices.tolist(), edge_hard.tolist()):
            if hd:
                hard.add((min(a, b), max(a, b)))
    # union-find over corners: corners (t, c) and (u, d) of the same vertex join when the
    # two triangles share a soft edge through that vertex
    parent = np.arange(T * 3)

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    edge_tris = {}
    for t in range(T):
        tv = tri_vertex[t]
        for c in range(3):
            a, b = int(tv[c]), int(tv[(c + 1) % 3])
            key = (min(a, b), max(a, b))
            edge_tris.setdefault(key, []).append((t, c))
    for key, users in edge_tris.items():
        if key in hard or len(users) < 2:
            continue
        for i in range(len(users)):
            for j in range(i + 1, len(users)):
                (t, c), (u, d) = users[i], users[j]
                # vertex key[0] and key[1] are each a corner in both triangles
                for v in key:
                    ct = [k for k in range(3) if tri_vertex[t][k] == v][0]
                    cu = [k for k in range(3) if tri_vertex[u][k] == v][0]
                    ra, rb = find(t * 3 + ct), find(u * 3 + cu)
                    if ra != rb:
                        parent[ra] = rb
    roots = np.array([find(i) for i in range(T * 3)])
    acc = np.zeros((T * 3, 3))
    np.add.at(acc, roots, (fn[:, None, :] * ang[:, :, None]).reshape(-1, 3))
    out = _unit(acc[roots])
    bad = np.linalg.norm(out, axis=1) < 0.5
    out[bad] = np.repeat(fn, 3, 0)[bad]
    return out.reshape(T, 3, 3)


def corners(src):
    """Triangle-corner arrays (T,3,...) of the source LOD0."""
    md = src['md']
    scale = np.array(src['settings'].get('BuildScale3D') or (1, 1, 1), np.float64)
    ti = md['tri_inst']
    tv = md['inst_vertex'][ti]
    P = md['positions'].astype(np.float64) * scale
    out = {'P': P[tv], 'tri_vertex': tv, 'slot': md['tri_group'].astype(np.int64)}
    N = md['normal'][ti].astype(np.float64) if md['normal'] is not None else None
    recompute = bool(src['settings'].get('bRecomputeNormals'))
    if N is None or recompute or (np.linalg.norm(N, axis=-1) < NORMAL_MIN).mean() > 0.5:
        out['N'] = corner_normals(P, tv, md.get('edge_vertices'), md.get('edge_hard'))
        out['normals_rebuilt'] = True
    else:
        out['N'] = _unit(N)
        out['normals_rebuilt'] = False
    keep_t = (md['tangent'] is not None and not src['settings'].get('bRecomputeTangents', True)
              and not out['normals_rebuilt'])
    out['TX'] = md['tangent'][ti].astype(np.float64) if keep_t else None
    out['S'] = (np.where(md['sign'][ti] < 0, -1.0, 1.0) if (keep_t and md['sign'] is not None) else None)
    out['UV'] = [u[ti].astype(np.float64) for u in md['uvs']]
    out['C'] = md['color'][ti].astype(np.float64) if md['color'] is not None else None
    return out


def seen_colours(C):
    """Vertex colour as a material reads it: linear float -> FColor (sRGB-encoded) -> /255."""
    rgb = mg.linear_to_srgb(np.clip(C[..., :3], 0, 1))
    a = np.clip(C[..., 3:4], 0, 1)
    q = np.rint(np.concatenate([rgb, a], -1) * 255.0) / 255.0
    return q


def world_planar(P, fn):
    """Per corner the world-planar coordinate WorldAlignedTexture reads (sign included):
    -(the two world axes of the plane the face looks along)."""
    ax = np.argmax(np.abs(fn), 1)
    out = np.zeros(P.shape[:2] + (2,))
    for a, (i, j) in PROJ_AXES.items():
        sel = ax == a
        out[sel, :, 0] = -P[sel, :, i]
        out[sel, :, 1] = -P[sel, :, j]
    return out


def build(src, scale, slot_jobs):
    """slot_jobs: per source slot {'drop': bool, 'frame': plan frame | None, 'two_sided': bool,
    'paint': None | {'channel': i, 'lo': x, 'hi': y}, 'out_slot': int} ->
    (omd3smwrite description, stats)."""
    c = corners(src)
    T = len(c['slot'])
    P0 = c['P']                                      # unscaled (texture projection space)
    fnorm = _unit(np.cross(P0[:, 2] - P0[:, 0], P0[:, 1] - P0[:, 0]))
    keep = np.ones(T, bool)
    uv = [u.copy() for u in c['UV']] or [np.zeros((T, 3, 2))]
    uv_rewritten = False
    colours = None
    paint_any = any(j.get('paint') for j in slot_jobs)
    if paint_any:
        colours = np.ones((T, 3, 4))
    for s, job in enumerate(slot_jobs):
        sel = c['slot'] == s
        if not sel.any():
            continue
        if job.get('drop'):
            keep[sel] = False
            continue
        fr = job.get('frame')
        if job.get('bake_uv_data') is not None:
            # a per-mesh bake (permesh.py) is laid out in this layout: it becomes the slot's UV0
            uv[0][sel] = np.asarray(job['bake_uv_data'], np.float64)
            uv_rewritten = True
        elif fr is not None:
            G, g = np.asarray(fr['G']), np.asarray(fr['g'])
            if fr['base'] == 'wuv':
                base = world_planar(P0[sel], fnorm[sel])
            else:
                k = int(fr['base'][2:])
                base = c['UV'][k][sel] if k < len(c['UV']) else uv[0][sel]
            uv[0][sel] = base @ G.T + g
            uv_rewritten = True
        if job.get('paint') and c['C'] is not None:
            pj = job['paint']
            seen = seen_colours(c['C'][sel])[..., pj['channel']]
            w = np.clip((seen - pj['lo']) / max(pj['hi'] - pj['lo'], 1e-6), 0, 1)
            col = np.zeros(seen.shape + (4,))
            col[..., 0] = w
            colours[sel] = col                                  # R selects layer 2, A = 0
    tri_slot = np.array([slot_jobs[s]['out_slot'] if s < len(slot_jobs) else 0 for s in c['slot']])
    sel = keep
    P = P0[sel] * scale
    N = c['N'][sel]
    UVs = [u[sel] for u in uv]
    TX = None if (c['TX'] is None or uv_rewritten) else c['TX'][sel]
    S = None if TX is None else c['S'][sel]
    C = None if colours is None else colours[sel]
    slots = tri_slot[sel]
    two = np.array([slot_jobs[s].get('two_sided', False) for s in c['slot'][sel]], bool) if len(slots) else np.zeros(0, bool)
    if two.any():
        P = np.concatenate([P, P[two][:, ::-1]])
        N = np.concatenate([N, -N[two][:, ::-1]])
        UVs = [np.concatenate([u, u[two][:, ::-1]]) for u in UVs]
        if TX is not None:
            TX = np.concatenate([TX, TX[two][:, ::-1]])
            S = np.concatenate([S, -S[two][:, ::-1]])
        if C is not None:
            C = np.concatenate([C, C[two][:, ::-1]])
        slots = np.concatenate([slots, slots[two]])
    Tn = len(P)
    if Tn == 0:
        return None, {'triangles': 0}
    # weld identical corners
    cols = [P.reshape(-1, 3), N.reshape(-1, 3)] + [u.reshape(-1, 2) for u in UVs]
    if TX is not None:
        cols += [TX.reshape(-1, 3), S.reshape(-1, 1)]
    if C is not None:
        cols.append(C.reshape(-1, 4))
    allc = np.ascontiguousarray(np.concatenate(cols, 1).astype(np.float32))
    _u, first, inv = np.unique(allc.view('V%d' % (allc.shape[1] * 4)).ravel(), return_index=True, return_inverse=True)
    tris = inv.reshape(-1, 3)
    desc = {
        'positions': P.reshape(-1, 3)[first].astype(np.float32),
        'normals': N.reshape(-1, 3)[first].astype(np.float32),
        'tangents': None if TX is None else TX.reshape(-1, 3)[first].astype(np.float32),
        'signs': None if TX is None else S.reshape(-1)[first].astype(np.float32),
        'uvs': [u.reshape(-1, 2)[first].astype(np.float32) for u in UVs],
        'colors': None if C is None else np.clip(np.rint(C.reshape(-1, 4)[first] * 255), 0, 255).astype(np.uint8),
        'triangles': tris.astype(np.int64),
        'materials': slots.astype(np.int64),
    }
    a3 = np.linalg.norm(np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]), axis=1) / 2
    u0 = UVs[0] if UVs else np.zeros((Tn, 3, 2))
    a2 = np.abs((u0[:, 1, 0] - u0[:, 0, 0]) * (u0[:, 2, 1] - u0[:, 0, 1])
                - (u0[:, 1, 1] - u0[:, 0, 1]) * (u0[:, 2, 0] - u0[:, 0, 0])) / 2
    big = a3 > COLLAPSED_UV_MIN_AREA
    collapsed = big & (a2 <= COLLAPSED_UV_MAX * a3)
    stats = {'triangles': int(len(tris)), 'vertices': int(len(first)), 'source_triangles': T,
             'collapsed_uv_area': round(float(a3[collapsed].sum() / max(a3[big].sum(), 1e-9)), 3),
             'dropped_triangles': int((~keep).sum()), 'two_sided_copies': int(two.sum()),
             'normals_rebuilt': bool(c['normals_rebuilt']), 'uv_rewritten': uv_rewritten,
             'uv_channels': len(c['UV'])}
    return desc, stats


#: Roles whose pieces collide as drawn (CTF_UseComplexAsSimple: the render mesh answers simple
#: queries too). The game tests a trap against collision, and packs' simple shapes stand off the
#: drawn surfaces (City of Brass Coirridor_A_WallShort_400B's box is 5-9 uu proud of its faces;
#: Docks SM_wall_module_06's lintel box covers part of the lintel): with the drawn mesh as the
#: collision a grid on a drawn surface is always backed (round fc2).
COMPLEX_AS_SIMPLE_ROLES = ('floor', 'wall', 'ceiling', 'roof', 'stairs', 'arch', 'column', 'door')     # stairs: see stair_ramps


#: Stairs collide as smooth ramps through their tread tops (convex hulls of the up-facing faces
#: and their footprint on the ground), so a walker never catches on a tread nose or open riser
#: (user, Docks showcase 2026-10-15: stuck at the top of SM_wood_stairs_01). Up-facing: a face
#: normal's z at least this.
STAIR_TREAD_NZ = 0.7
#: One hull over every tread filled the open space of curved, split and landing stairs (Rift
#: Lord 2026-10-28: MedievalVillage SM_central_stairs stood a block up to 774 uu proud over the
#: void between its flights): the treads are split in two (2-means on their centres) until each
#: group's hull, with the hulls already kept, stands no more proud of the drawing than a step
#: (proud_area). A tread is a run of up-facing faces
#: sharing vertices; packs model each tread as its own block, so runs are not flights.
#: Most hulls a stairs piece takes before it collides as drawn instead (MedievalVillage
#: SM_central_stairs, two curved flights and a straight one, takes 106).
RAMP_MAX_HULLS = 128
#: Lloyd iterations of a 2-means split.
RAMP_SPLIT_ITERATIONS = 8
#: A ramp hull's foot lies at least this far below its lowest tread (uu), so a flight on the
#: piece's lowest level still has volume.
RAMP_FOOT = 10.0
#: Proud: simple collision over PROUD_RISE above the drawn top (the mesh's lowest z where nothing
#: is drawn) across PROUD_AREA, rasterised top-down on PROUD_CELL cells (Rift Lord's
#: fabcollision measure, 2026-10-28: ramps over straight treads stand at most 103 uu over them;
#: ramps hulled across open space 135-1114 uu; the area is a hero's footprint, 84 uu wide).
PROUD_CELL = 25.0
PROUD_RISE = 120.0
PROUD_AREA = 84.0 * 84.0
#: Most cells per axis; larger meshes use larger cells.
PROUD_MAX_CELLS = 1024
#: Most sample points per triangle edge.
PROUD_MAX_STEPS = 600
#: A box's 12 triangles over its corners (index = 4x + 2y + z, each 0 or 1).
BOX_TRIS = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
                     [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])


class _Drawn:
    """The drawn top of a mesh on a top-down raster, for proud_area."""

    def __init__(self, P, T):
        self.lo, hi = P.min(0), P.max(0)
        self.cell = max(PROUD_CELL, float((hi - self.lo)[:2].max()) / PROUD_MAX_CELLS)
        self.shape = (int((hi[0] - self.lo[0]) / self.cell) + 1, int((hi[1] - self.lo[1]) / self.cell) + 1)
        top = self.top(P[T])
        self.floor = np.where(np.isfinite(top), top, self.lo[2])

    def top(self, tris):
        """Highest z over each cell of the triangles (n, 3, 3); -inf where none."""
        top = np.full(self.shape, -np.inf)
        if not len(tris):
            return top
        a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
        edge = np.maximum(np.maximum(np.linalg.norm((b - a)[:, :2], axis=1), np.linalg.norm((c - b)[:, :2], axis=1)),
                          np.linalg.norm((a - c)[:, :2], axis=1))
        steps = np.clip(np.ceil(edge / (self.cell / 2.0)), 1, PROUD_MAX_STEPS).astype(int)
        for n in np.unique(steps):
            pick = steps == n
            i, j = np.meshgrid(np.arange(n + 1), np.arange(n + 1), indexing='ij')
            keep = i + j <= n
            u, v = i[keep] / n, j[keep] / n
            aa, bb, cc = a[pick], b[pick], c[pick]
            per = max(1, 2000000 // len(u))
            for k in range(0, len(aa), per):
                sl = slice(k, k + per)
                p = (aa[sl, None, :] + (bb[sl, None, :] - aa[sl, None, :]) * u[None, :, None]
                     + (cc[sl, None, :] - aa[sl, None, :]) * v[None, :, None]).reshape(-1, 3)
                ix = np.clip(((p[:, 0] - self.lo[0]) / self.cell).astype(int), 0, self.shape[0] - 1)
                iy = np.clip(((p[:, 1] - self.lo[1]) / self.cell).astype(int), 0, self.shape[1] - 1)
                np.maximum.at(top, (ix, iy), p[:, 2])
        return top

    def proud_area(self, tris, under=None):
        """Area (uu^2) where the triangles' top (or `under`, a top already rasterised, where
        higher) stands over PROUD_RISE above the drawn top; and that top."""
        top = self.top(tris)
        if under is not None:
            top = np.maximum(top, under)
        rise = np.where(np.isfinite(top), top - self.floor, 0.0)
        return float((rise > PROUD_RISE).sum()) * self.cell * self.cell, top


def _hull_tris(points):
    from riftlord.formats.omd3 import omd3smwrite as W
    v, t, _polys = W.hull_triangles(np.asarray(points, np.float32))
    return np.asarray(v, np.float64)[t]


def _box_tris(b):
    from riftlord.formats.omd3 import omd3smwrite as W
    c = np.asarray(b['center'], np.float64)
    size = np.asarray(b['size'], np.float64)
    R = W.rotator_matrix(b.get('rotation', (0, 0, 0)))
    corners = np.array([c + R @ ((np.array([x, y, z]) - 0.5) * size) for x in (0, 1) for y in (0, 1) for z in (0, 1)])
    return corners[BOX_TRIS]


def _face_runs(T, faces, key):
    """`faces` (indices into T) split into runs that share a vertex (`key`: welded vertex ids)."""
    if not len(faces):
        return []
    V = key[T[faces]]
    lab = np.arange(len(faces))
    while True:
        vl = np.full(key.max() + 1, len(faces))
        np.minimum.at(vl, V.ravel(), np.repeat(lab, 3))
        new = vl[V].min(1)
        if np.array_equal(new, lab):
            break
        lab = new
    return [faces[lab == k] for k in np.unique(lab)]


def _two_means(X):
    """Two groups of the points X (n, 3): split at the median across their main axis, then
    Lloyd iterations. None when they do not part."""
    X = X - X.mean(0)
    d = X @ np.linalg.eigh(X.T @ X)[1][:, -1]
    lab = d > np.median(d)
    for _ in range(RAMP_SPLIT_ITERATIONS):
        if lab.all() or not lab.any():
            return None
        m0, m1 = X[~lab].mean(0), X[lab].mean(0)
        new = np.linalg.norm(X - m1, axis=1) < np.linalg.norm(X - m0, axis=1)
        if np.array_equal(new, lab):
            break
        lab = new
    return None if lab.all() or not lab.any() else lab


def stair_ramps(desc):
    """[hull points] of the ramps over a stairs piece's treads; [] when it has no treads to
    ramp; None when they cannot be hulled without standing proud of the drawing."""
    P = np.asarray(desc['positions'], np.float64)
    T = np.asarray(desc['triangles'], np.int64)
    a, b, c = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
    n = np.cross(c - a, b - a)                         # UE front face
    nz = n[:, 2] / np.maximum(np.linalg.norm(n, axis=1), 1e-12)
    treads = np.flatnonzero(nz >= STAIR_TREAD_NZ)
    if not len(treads):
        return []
    _u, key = np.unique(np.round(P, 1), axis=0, return_inverse=True)
    drawn = _Drawn(P, T)
    centre = (a + b + c) / 3.0
    todo, out, kept = [_face_runs(T, treads, key.ravel())], [], None
    while todo:
        if len(out) + len(todo) > RAMP_MAX_HULLS:
            return None
        group = todo.pop()
        top = P[np.unique(T[np.concatenate(group)])]
        foot = top.copy()
        foot[:, 2] = min(drawn.lo[2], top[:, 2].min() - RAMP_FOOT)
        pts = np.concatenate([top, foot])
        if np.linalg.matrix_rank(pts - pts.mean(0)) < 3:
            continue                                   # a sliver: nothing to stand on
        area, top = drawn.proud_area(_hull_tris(pts), kept)
        if area < PROUD_AREA:
            out.append(pts)
            kept = top
            continue
        if len(group) == 1:                            # one tread proud of itself: split its faces
            group = [np.array([f]) for f in group[0]]
        lab = _two_means(np.array([centre[g].mean(0) for g in group]))
        if lab is None:
            return None
        todo += [[g for g, k in zip(group, lab) if k], [g for g, k in zip(group, lab) if not k]]
    return out


#: Words of a mesh name (survey-tools report.name_words) that make it sky (is_sky): no collision
#: at all, no trap grids. Shipped SM_SkySphere / SM_Skybox_Mesh carry no simple shapes; Fab skies stood
#: as 2 km shells (Orasot SM_SkyBoxTransperent) and 3.5 km cubes (Sky Dungeon SM_sky).
SKY_WORDS = ('sky', 'cloud', 'clouds')
#: Foliage without authored shapes collides as the shipped foliage does (measured on OMD3's 81
#: foliage meshes): trees by their trunk alone (PineTree_01's hull 173 uu wide on a 1029 uu
#: tree, SM_BF_BroadleafA's 94 on 1684), grass, ferns, ivy and branches with no simple shapes.
#: A trunk: the piece is at least a hero tall and its cross-section TRUNK_PROBE above its base
#: spans at most TRUNK_SHARE of its plan width (Fab trees 0.03-0.18, vines, ferns and bushes
#: 0.34 and up, 2026-10-28). The trunk hull is that cross-section from the base to the tallest
#: walker's height (an ogre).
TRUNK_PROBE = 97.5
TRUNK_SHARE = 0.26
HERO_HEIGHT = 195.0
OGRE_HEIGHT = 360.0


def is_sky(name):
    """A sky word in the name and no building-part word (report.ARCH: SM_SkyBridge_Floor is a floor)."""
    import re
    from riftlord.fab.survey import report
    words = report.name_words(name)
    return (any(w in SKY_WORDS for w in words.split('_'))
            and not any(re.search(report.WORD_START + rx, words) for r, rx in report.ROLE_RULES if r in report.ARCH))


def trunk(desc):
    """Hull points of a tree's trunk (see TRUNK_SHARE), or None when the piece has none."""
    P = np.asarray(desc['positions'], np.float64)
    T = np.asarray(desc['triangles'], np.int64)
    z0, z1 = P[:, 2].min(), P[:, 2].max()
    width = float(np.ptp(P[:, :2], 0).max())
    if z1 - z0 < HERO_HEIGHT or width <= 0:
        return None
    z = z0 + TRUNK_PROBE
    a, b, c = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
    cut = []
    for u, v in ((a, b), (b, c), (c, a)):
        du, dv = u[:, 2] - z, v[:, 2] - z
        hit = du * dv < 0
        t = du[hit] / (du[hit] - dv[hit])
        cut.append(u[hit, :2] + (v[hit, :2] - u[hit, :2]) * t[:, None])
    cut = np.concatenate(cut)
    if len(cut) < 3 or float(np.ptp(cut, 0).max()) > TRUNK_SHARE * width:
        return None
    top = min(z1, z0 + OGRE_HEIGHT)
    pts = np.concatenate([np.c_[cut, np.full(len(cut), z0)], np.c_[cut, np.full(len(cut), top)]])
    return pts if np.linalg.matrix_rank(pts - pts.mean(0)) == 3 else None


#: An authored capsule (KSphylElem) is written as a convex hull through points on it: each
#: hemisphere on rings at these elevations (degrees) plus its pole, CAPSULE_SIDES around. The
#: hull lies inside the capsule, at most r(1 - cos 15 deg) = 3.4% of its radius off.
CAPSULE_RINGS = (0.0, 30.0, 60.0)
CAPSULE_SIDES = 12


def capsule_points(cap, scale):
    """Hull points of an authored capsule {center, rotation, radius, length} (along its local z),
    scaled."""
    from riftlord.formats.omd3 import omd3smwrite as W
    r, half = cap['radius'], cap['length'] / 2.0
    az = np.radians(np.arange(CAPSULE_SIDES) * 360.0 / CAPSULE_SIDES)
    pts = []
    for sign in (1.0, -1.0):
        for el in np.radians(CAPSULE_RINGS):
            pts += [(r * np.cos(el) * np.cos(a), r * np.cos(el) * np.sin(a), sign * (half + r * np.sin(el))) for a in az]
        pts.append((0.0, 0.0, sign * (half + r)))
    R = W.rotator_matrix(cap.get('rotation') or (0, 0, 0))
    return (np.asarray(cap['center'], np.float64) + np.asarray(pts) @ R.T) * scale


def collision(src, scale, desc, role=None, double_sided=False, name=''):
    """The pack's authored simple shapes scaled (capsules as hulls, capsule_points); none ->
    foliage as the shipped foliage (a trunk hull or no simple shapes), else one box around the
    written vertices, or the drawn mesh when that box would stand proud of it (hanging and
    draped pieces). The source trace flag is kept
    (complex queries use the written render mesh), except that architecture collides as drawn
    (COMPLEX_AS_SIMPLE_ROLES), stairs as ramps (stair_ramps) and sky not at all (SKY_WORDS:
    no shapes, simple-as-complex, `collides` false)."""
    if is_sky(name):
        return {'boxes': [], 'convex': [], 'trace': 'simple_as_complex', 'double_sided': False, 'collides': False}, {
            'source': 'none', 'boxes': 0, 'convex': 0, 'sphyls_ignored': src['collision'].get('sphyls', 0),
            'trace': 'simple_as_complex', 'double_sided': False, 'collides': False}
    coll = src['collision']
    boxes = []
    for b in coll.get('boxes', []):
        boxes.append({'center': [x * scale for x in b['center']], 'rotation': list(b.get('rotation') or (0, 0, 0)),
                      'size': [x * scale for x in b['size']]})
    convex = [np.asarray(h, np.float64) * scale for h in coll.get('convex', [])]
    convex += [capsule_points(c, scale) for c in coll.get('capsules', []) if c['radius'] > 0]
    convex = [h for h in convex if len(h) >= 4 and np.linalg.matrix_rank(h - h.mean(0)) == 3]
    source = 'authored'
    trace = 'complex_as_simple' if role in COMPLEX_AS_SIMPLE_ROLES else coll.get('trace', 'default')
    if not boxes and not convex and role == 'foliage':
        stem = trunk(desc)
        convex, source = ([stem], 'trunk') if stem is not None else ([], 'no_simple')
        trace = 'default'
    elif not boxes and not convex:
        from riftlord.formats.omd3 import omd3smwrite as W
        boxes = W.boxes_from_mesh(desc['positions'])
        source = 'box_fallback'
        if trace != 'complex_as_simple' and role != 'stairs':
            P = np.asarray(desc['positions'], np.float64)
            if _Drawn(P, np.asarray(desc['triangles'], np.int64)).proud_area(_box_tris(boxes[0]))[0] >= PROUD_AREA:
                boxes, source, trace = [], 'drawn', 'complex_as_simple'
    if role == 'stairs':
        ramps = stair_ramps(desc)
        if ramps:
            boxes, convex, source, trace = [], ramps, 'stair_ramp', 'simple_as_complex'
        elif ramps is None:
            boxes, convex, source, trace = [], [], 'drawn', 'complex_as_simple'
    return {'boxes': boxes, 'convex': convex, 'trace': trace, 'double_sided': bool(double_sided)}, {
        'source': source, 'boxes': len(boxes), 'convex': len(convex),
        'sphyls_ignored': coll.get('sphyls', 0) - sum(1 for c in coll.get('capsules', []) if c['radius'] > 0),
        'trace': trace, 'double_sided': bool(double_sided), 'collides': True}
