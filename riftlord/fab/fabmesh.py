# Generated from OMD2-Converter/fabmesh.py by thirdparty/fabconvert/port_riftlord.py; edit the source.
"""Read a third-party (Fab / Marketplace) EDITOR StaticMesh into an omd3smwrite description.

Input side of the static-mesh pipeline. UE 4.22-4.27 editor packages keep LOD0 as an
FMeshDescription bulk (thirdparty/survey-tools/meshgeo.py finds it); this reads the
per-vertex-instance attributes too (normals, tangents, binormal signs, colours, every UV
channel), the polygon groups (material slots), the editor build settings and the authored
collision (BodySetup AggGeom: boxes, convex hulls, sphyls, trace flag).

    from riftlord.fab import fabmesh
    src = fabmesh.read('/home/.../Medieval_Dock/Mesh/House_Modules/SM_wall_module_01.uasset')
    src['desc']        -> omd3smwrite.write(...) description
    src['collision']   -> omd3smwrite collision spec
    src['slots']       -> [(slot name, source material path), ...]

Run on vault data with `python3 -I` (the vault is read-only and untrusted).
"""

import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'thirdparty', 'survey-tools'))

from riftlord.fab.survey import meshgeo                                     # noqa: E402
from riftlord.fab.survey import pkgread                                     # noqa: E402
from riftlord.fab.survey.meshgeo import R, attr_set, bitarray, tarr    # noqa: E402,F401

#: Instances are welded into one render vertex when every attribute is bit-identical
#: (UE's build welds on near-equality; exact equality is the safe subset).
WELD_EXACT = True


def _attr(b, sets, key, channel=0, dtype='<f4', width=1):
    got = sets.get(key)
    if got is None or channel >= len(got[1]):
        return None
    o, esz, cnt = got[1][channel]
    a = np.frombuffer(b, dtype, cnt * width, o)
    return a.reshape(cnt, width) if width > 1 else a


def mesh_description(b, edver):
    """meshgeo.mesh_description_ue4, keeping every per-instance attribute."""
    r = R(b)
    verts = bitarray(r)
    vi_ids = bitarray(r)
    vi_vert = np.frombuffer(b, '<i4', len(vi_ids), r.o).copy()
    r.o += 4 * len(vi_ids)
    ed = bitarray(r)
    edge_v = np.frombuffer(b, '<i4', 2 * len(ed), r.o).reshape(-1, 2).copy()
    r.o += 8 * len(ed)
    pids = bitarray(r)
    poly_group = np.empty(len(pids), np.int32)
    contours = []
    for k in range(len(pids)):
        contours.append(tarr(r, '<i4'))
        if edver < meshgeo.ED_REMOVED_HOLES:
            for _ in range(r.i32()):
                tarr(r, '<i4')
        poly_group[k] = r.i32()
    _pg = bitarray(r)
    sets = [attr_set(r) for _ in range(5)]
    if edver >= meshgeo.ED_TRIANGLES:
        tids = bitarray(r)
        t = np.frombuffer(b, '<i4', 4 * len(tids), r.o).reshape(-1, 4)
        r.o += 16 * len(tids)
        tri_inst, tri_poly = t[:, :3].copy(), t[:, 3].copy()
    else:
        tw, tp = [], []
        for k, c in enumerate(contours):
            for j in range(1, len(c) - 1):
                tw.append((c[0], c[j], c[j + 1]))
                tp.append(pids[k])
        tri_inst, tri_poly = np.array(tw, np.int64), np.array(tp, np.int64)
    va, via, ea, pga = sets[0][1], sets[1][1], sets[2][1], sets[4][1]
    pos = _attr(b, va, 'Position', width=3)
    inst_vertex = np.full(int(vi_ids.max()) + 1, -1, np.int64)
    inst_vertex[vi_ids] = vi_vert
    nuv = len(via['TextureCoordinate'][1]) if 'TextureCoordinate' in via else 0
    pmap = np.full(int(pids.max()) + 1, -1, np.int64)
    pmap[pids] = poly_group
    slots = pga.get('ImportedMaterialSlotName', (6, [[]]))[1][0]
    hard = _attr(b, ea, 'IsHard', dtype='<u4')
    return {
        'positions': np.asarray(pos, np.float32),
        'inst_vertex': inst_vertex,
        'tri_inst': tri_inst.astype(np.int64),
        'tri_group': pmap[tri_poly],
        'normal': _attr(b, via, 'Normal', width=3),
        'tangent': _attr(b, via, 'Tangent', width=3),
        'sign': _attr(b, via, 'BinormalSign'),
        'color': _attr(b, via, 'Color', width=4),
        'uvs': [_attr(b, via, 'TextureCoordinate', k, width=2) for k in range(nuv)],
        'edge_vertices': edge_v, 'edge_hard': hard[ed] if hard is not None and len(ed) else None,
        'slot_names': slots,
    }


def _props_value(p, t):
    return t.get('value') if t else None


def build_settings(p, props):
    sm = pkgread.find(props, 'SourceModels')
    out = {}
    if sm and sm.get('items'):
        bs = pkgread.find(sm['items'][0], 'BuildSettings')
        for c in (bs or {}).get('children', []):
            out[c['name']] = c.get('value')
    return out


def collision(p, body_export):
    """{'boxes', 'convex', 'sphyls', 'trace'} from the editor BodySetup."""
    props, _end = p.props(body_export)
    out = {'boxes': [], 'convex': [], 'sphyls': 0, 'trace': 'default'}
    for t in props:
        if t['name'] == 'CollisionTraceFlag':
            out['trace'] = {'CTF_UseSimpleAsComplex': 'simple_as_complex',
                            'CTF_UseComplexAsSimple': 'complex_as_simple'}.get(t.get('value'), 'default')
    agg = pkgread.find(props, 'AggGeom')
    for c in (agg or {}).get('children', []):
        if c['name'] == 'BoxElems':
            for it in c.get('items', []):
                g = {x['name']: x.get('value') for x in it}
                out['boxes'].append({'center': list(g.get('Center') or (0, 0, 0)),
                                     'rotation': list(g.get('Rotation') or (0, 0, 0)),
                                     'size': [g.get('X', 0.0), g.get('Y', 0.0), g.get('Z', 0.0)]})
        elif c['name'] == 'ConvexElems':
            for it in c.get('items', []):
                vd = next(x for x in it if x['name'] == 'VertexData')
                n = struct.unpack_from('<i', p.d, vd['at'])[0]
                # inner tag: name 8, type 8, size 4, index 4, struct name 8, guid 16, flag 1;
                # UE 5.4+ (file version 1012, complete type names) writes none
                at = vd['at'] + 4 + (0 if p.ue5 >= 1012 else 49)
                # UE5 (large world coordinates) saves FVector as three doubles, UE4 as floats
                pts = np.frombuffer(p.d, '<f8' if p.ue5 else '<f4', 3 * n, at).reshape(-1, 3).astype(np.float64)
                tr = next((x for x in it if x['name'] == 'Transform'), None)
                if tr is not None:
                    q, tl, sc = (0, 0, 0, 1), np.zeros(3), np.ones(3)
                    for f in tr.get('children', []):
                        if f['name'] == 'Rotation':
                            q = f['value']
                        elif f['name'] == 'Translation':
                            tl = np.array(f['value'])
                        elif f['name'] == 'Scale3D':
                            sc = np.array(f['value'])
                    from riftlord.formats.omd3 import omd3physx
                    pts = (omd3physx._quat_matrix(q) @ (pts * sc).T).T + tl
                out['convex'].append(pts.astype(np.float32))
        elif c['name'] == 'SphylElems':
            out['sphyls'] = c.get('count', 0)
            for it in c.get('items', []):
                g = {x['name']: x.get('value') for x in it}
                out['capsules'] = out.get('capsules', []) + [{
                    'center': list(g.get('Center') or (0, 0, 0)), 'rotation': list(g.get('Rotation') or (0, 0, 0)),
                    'radius': float(g.get('Radius') or 0.0), 'length': float(g.get('Length') or 0.0)}]
    return out


def read(path):
    p = pkgread.Package(path)
    sm = [e for e in p.exports if p.class_of(e) == 'StaticMesh' and e['outer'] == 0][0]
    props, end = p.props(sm)
    edver = meshgeo.custom(p, meshgeo.ED_GUID) or 0
    if p.ue5 or edver < meshgeo.ED_RAWMESH_DEPRECATED:
        raise SystemExit('%s: only UE 4.22-4.27 MeshDescription packages are read here' % path)
    r = R(p.d, end)
    r.i32(); r.raw(2); r.i32(); r.i32(); r.i32()   # guid flag, strip, bCooked, BodySetup, NavCollision
    r.fstr(); r.u32(); r.raw(16)                     # HighResSourceMeshName, CRC, lighting guid
    r.raw(4 * r.i32())                               # sockets
    if not r.i32():
        raise SystemExit('%s: no mesh description' % path)
    h = meshgeo.bulk_header(r, p)
    md = mesh_description(meshgeo.bulk_bytes(p, h), edver)
    settings = build_settings(p, props)
    scale = np.array(settings.get('BuildScale3D') or (1, 1, 1), np.float32)

    # render vertices = vertex instances used by triangles, welded when identical
    inst = np.unique(md['tri_inst'].ravel())
    P = md['positions'][md['inst_vertex'][inst]] * scale
    cols = [P]
    N = md['normal'][inst] if md['normal'] is not None else None
    T = md['tangent'][inst] if md['tangent'] is not None else None
    S = md['sign'][inst] if md['sign'] is not None else None
    C = md['color'][inst] if md['color'] is not None else None
    UV = [u[inst] for u in md['uvs']]
    for a in (N, T, S, C, *UV):
        if a is not None:
            cols.append(np.asarray(a, np.float32).reshape(len(inst), -1))
    allattr = np.ascontiguousarray(np.concatenate(cols, 1))
    if WELD_EXACT:
        _u, first, weld = np.unique(allattr.view('V%d' % (allattr.shape[1] * 4)).ravel(),
                                    return_index=True, return_inverse=True)
    else:
        first, weld = np.arange(len(inst)), np.arange(len(inst))
    lookup = np.full(int(inst.max()) + 1, -1, np.int64)
    lookup[inst] = weld.ravel()
    tris = lookup[md['tri_inst']]
    pick = first
    recompute_n = bool(settings.get('bRecomputeNormals'))
    recompute_t = bool(settings.get('bRecomputeTangents', True))
    normals = None if (N is None or recompute_n) else N[pick]
    if normals is not None and (np.linalg.norm(normals, axis=1) < 1e-6).mean() > 0.5:
        normals = None
    desc = {
        'positions': P[pick],
        'normals': normals,
        'tangents': None if (T is None or recompute_t or normals is None) else T[pick],
        'signs': None if S is None else np.where(S[pick] < 0, -1.0, 1.0),
        'uvs': [u[pick] for u in UV],
        'colors': None if C is None else np.clip(np.rint(C[pick] * 255), 0, 255).astype(np.uint8),
        'triangles': tris,
        'materials': md['tri_group'].astype(np.int64),
    }
    # polygon groups are slots in group order; their imported names label them
    sm_mats = pkgread.find(props, 'StaticMaterials')
    slots = []
    for k, it in enumerate((sm_mats or {}).get('items', [])):
        g = {x['name']: x.get('value') for x in it}
        slots.append((g.get('MaterialSlotName'), p.obj_path(g.get('MaterialInterface') or 0)))
    body = [e for e in p.exports if p.class_of(e) == 'BodySetup']
    coll = collision(p, body[0]) if body else {'boxes': [], 'convex': [], 'trace': 'default'}
    return {'desc': desc, 'slots': slots, 'collision': coll, 'settings': settings,
            'group_names': md['slot_names'], 'source': path,
            'stats': {'instances': int(len(inst)), 'vertices': int(len(pick)),
                      'triangles': int(len(tris)), 'uv_channels': len(UV),
                      'stored_normals': normals is not None,
                      'recompute_normals': recompute_n, 'recompute_tangents': recompute_t}}


def winding_check(desc):
    """Median dot of UE's front-face normal ((p2-p0) x (p1-p0)) with the stored normals:
    +1 means the description already uses the cooked winding."""
    if desc['normals'] is None:
        return None
    P, T = desc['positions'], desc['triangles']
    fn = np.cross(P[T[:, 2]] - P[T[:, 0]], P[T[:, 1]] - P[T[:, 0]])
    fn /= np.maximum(np.linalg.norm(fn, axis=1), 1e-12)[:, None]
    n = desc['normals'][T].mean(1)
    n /= np.maximum(np.linalg.norm(n, axis=1), 1e-12)[:, None]
    return float(np.median((fn * n).sum(1)))


if __name__ == '__main__':
    for f in sys.argv[1:]:
        s = read(f)
        print(os.path.basename(f), s['stats'], 'slots', s['slots'], 'winding', winding_check(s['desc']),
              'collision', {k: (len(v) if isinstance(v, list) else v) for k, v in s['collision'].items()})
