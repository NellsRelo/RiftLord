"""Write cooked OMD3 SkeletalMesh packages: new geometry bound to a shipped skeleton.

Clone-and-replace: a shipped `_SKM` that already references the target USkeleton is
the template. Its tagged properties, reference skeleton, physics asset and material
slots are kept; its render data is replaced by one LOD built from a plain mesh
description (positions, normals, UVs, influences by bone NAME, triangles per slot).
The codec is `omd3skm_format`; the format and the runtime rules are in the
omd3-skeletal-mesh-writing skill.

    python3 omd3skmwrite.py identity Orc_Light_SKM Max_SKM     # byte-identical round trips
    python3 omd3skmwrite.py rewrite Orc_Light_SKM --source Orc_Medium_SKM --out DIR
    python3 omd3skm_acceptance.py DIR                           # the offline acceptance run
"""

import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.core import gamefiles  # noqa: E402
from riftlord.formats.omd3 import omd3skm  # noqa: E402
from riftlord.formats.omd3 import omd3skm_format as fmt  # noqa: E402
from riftlord.formats.ue.uassetio import Package, fname_hashes  # noqa: E402

#: A section's bone map is indexed by an 8-bit bone index in the weight buffer.
MAX_SECTION_BONES = 256
#: Influence counts the shipped weight buffers use (4, or 8 with extra influences).
INFLUENCE_WIDTHS = (4, 8)
#: FSkeletalMeshLODRenderData picks 32-bit indices from this vertex count up.
INDEX32_FROM = 65535
#: Exporter-side equality for "same position" (UE's THRESH_POINTS_ARE_SAME).
POINTS_ARE_SAME = 0.00002
#: The cloth asset LOD a written (single-LOD) mesh's cloth sections bind to.
CLOTH_LOD = 0
#: FSkeletalMeshVertexClothBuffer's strip flags on every shipped cloth mesh.
CLOTH_BUFFER_STRIP = (1, 0)


# -- template ---------------------------------------------------------------------

class Template:
    """A shipped skeletal mesh package, parsed strictly."""

    def __init__(self, name_or_path, blobs=None):
        path = name_or_path if name_or_path.startswith('/') else omd3skm.asset_path(name_or_path)
        if not path:
            raise SystemExit('no skeletal mesh %r' % name_or_path)
        self.path = path
        self.object = path.rsplit('/', 1)[-1]
        if blobs is None:
            ua, ux = gamefiles.package(path)
            blobs = {'.uasset': ua, '.uexp': ux}
        self.blobs = blobs
        self.pkg = Package(blobs['.uasset'], blobs['.uexp'])
        self.names = [n for n, _h in self.pkg.names]
        self.index = None
        for i, e in enumerate(self.pkg.exports):
            if self._class(e) == 'SkeletalMesh':
                self.index = i
        if self.index is None:
            raise SystemExit('%s holds no SkeletalMesh export' % path)
        data = self.export['data']
        self.ctx = fmt.context(data, self.names)
        self.model = fmt.parse(data, self.ctx['end'], self.ctx)
        self.bone_names = [self.fname(b[0], b[1]) for b in self.model['bones']]
        self.parents = [b[2] for b in self.model['bones']]
        self.bone_index = {n: i for i, n in enumerate(self.bone_names)}

    @property
    def export(self):
        return self.pkg.exports[self.index]

    def fname(self, index, number):
        return self.names[index] if number == 0 else '%s_%d' % (self.names[index], number - 1)

    def _class(self, e):
        ci = struct.unpack_from('<i', e['meta'], 0)[0]
        if ci >= 0:
            return None
        return self.names[struct.unpack('<7i', self.pkg.imports[-ci - 1])[5]]

    def import_record(self, index):
        """(class name, outer index, object name) of import -N."""
        r = struct.unpack('<7i', self.pkg.imports[-index - 1])
        return self.names[r[2]], r[4], self.names[r[5]]

    def material_paths(self):
        """Each slot's material as (package path, object name, slot name)."""
        out = []
        for m in self.model['materials']:
            path = obj = None
            if m['object'] < 0:
                _cls, outer, obj = self.import_record(m['object'])
                if outer < 0:
                    path = self.import_record(outer)[2]
            out.append((path, obj, self.fname(*m['slot'])))
        return out

    def skeleton_path(self):
        """The /Game path of the USkeleton this mesh is bound to."""
        got = [g for g in self.ctx['tags'] if g[0] == 'Skeleton']
        index = struct.unpack_from('<i', self.export['data'], got[0][3])[0]
        _cls, outer, _obj = self.import_record(index)
        return self.import_record(outer)[2]

    def add_bones(self, wanted, source):
        """Append bones the reference skeleton lacks, with their missing ancestors.

        source: {'names': [...], 'parents': [parent name or None], 'pose': [10 floats]}
        from a mesh on the same USkeleton. Returns the names appended.
        """
        at = {n: i for i, n in enumerate(source['names'])}
        added = []

        def need(name):
            if name in self.bone_index:
                return
            if name not in at:
                raise SystemExit('bone %s is in neither %s nor the source skeleton'
                                 % (name, self.object))
            parent = source['parents'][at[name]]
            if parent is None:
                raise SystemExit('cannot add a second root bone %s' % name)
            need(parent)
            idx = len(self.bone_names)
            self.model['bones'].append((self.pkg.add_name(name), 0, self.bone_index[parent]))
            self.model['pose'].append(tuple(source['pose'][at[name]]))
            self.model['name_map'].append((self.pkg.add_name(name), 0, idx))
            self.bone_names.append(name)
            self.parents.append(self.bone_index[parent])
            self.bone_index[name] = idx
            added.append(name)

        for name in wanted:
            need(name)
        self.names = [n for n, _h in self.pkg.names]
        return added

    def refuse_unsupported(self):
        """Templates whose extra data is tied to the old vertices cannot be reused (cloth is
        written fresh by the `cloth` option of `write`, never inherited)."""
        tags = {t[0] for t in self.ctx['tags']}
        bad = tags & {'MeshClothingAssets', 'MorphTargets'}
        if bad:
            raise SystemExit('%s carries %s, which index the old vertices; pick a template '
                             'without them' % (self.object, ', '.join(sorted(bad))))


# -- decoding (shipped LOD -> plain description) ---------------------------------------

def _half(raw, n, sets):
    return np.frombuffer(raw, '<f2').astype(np.float32).reshape(n, sets, 2)


def decode(t, lod=0):
    """A LOD of a template as the plain description `build_lod` accepts."""
    L = t.model['lods'][lod]
    b = L['buffers']
    n = b['pos_meta'][1]
    ntex, _nv, full_uv, hi_tan = b['vb_meta']
    pos = np.frombuffer(b['positions'][2], '<f4').reshape(n, 3).copy()
    if hi_tan:
        tan = np.frombuffer(b['tangents'][2], '<i2').reshape(n, 2, 4).astype(np.float32) / 32767.0
    else:
        tan = np.frombuffer(b['tangents'][2], 'i1').reshape(n, 2, 4).astype(np.float32) / 127.0
    if full_uv:
        uv = np.frombuffer(b['texcoords'][2], '<f4').reshape(n, ntex, 2).copy()
    else:
        uv = _half(b['texcoords'][2], n, ntex)
    width = b['index_width']
    idx = np.frombuffer(b['indices'][2], '<u2' if width == 2 else '<u4').astype(np.int64)
    _var, inf, _nb, _n, b16 = b['sw_meta']
    bw = 2 if b16 else 1
    raw = np.frombuffer(b['weights'][2], 'u1').reshape(n, inf * (bw + 1))
    local = raw[:, :inf * bw].view('<u2' if b16 else 'u1').astype(np.int64)
    weights = raw[:, inf * bw:].astype(np.float32) / 255.0
    bones = np.zeros_like(local)
    tri_mat = np.zeros(len(idx) // 3, np.int64)
    for s in L['sections']:
        bm = np.array(s['bone_map'], np.int64)
        lo, hi = s['base_vertex'], s['base_vertex'] + s['vertices']
        bones[lo:hi] = bm[np.minimum(local[lo:hi], len(bm) - 1)]
        t0 = s['base_index'] // 3
        tri_mat[t0:t0 + s['triangles']] = s['material']
    colors = None
    if 'colors' in b and b.get('colors'):
        colors = np.frombuffer(b['colors'][2], 'u1').reshape(n, 4).copy()
    return {'positions': pos, 'normals': tan[:, 1, :3].copy(), 'tangents': tan[:, 0, :3].copy(),
            'signs': np.where(tan[:, 1, 3] < 0, -1.0, 1.0).astype(np.float32),
            'uvs': [uv[:, k].copy() for k in range(ntex)], 'colors': colors,
            'bones': list(t.bone_names), 'influences': bones, 'weights': weights,
            'triangles': idx.reshape(-1, 3), 'materials': tri_mat,
            'skeleton': {'names': list(t.bone_names),
                         'parents': [t.bone_names[p] if p >= 0 else None for p in t.parents],
                         'pose': [list(p) for p in t.model['pose']]}}


def influences_from_pairs(pairs):
    """Per-vertex [(bone name, weight), ...] -> (bone names, influence array, weights)."""
    names, at = [], {}
    k = max(1, max(len(p) for p in pairs))
    inf = np.zeros((len(pairs), k), np.int64)
    wt = np.zeros((len(pairs), k), np.float32)
    for v, p in enumerate(pairs):
        for j, (name, w) in enumerate(p):
            if name not in at:
                at[name] = len(names)
                names.append(name)
            inf[v, j], wt[v, j] = at[name], w
    return names, inf, wt


# -- geometry helpers -----------------------------------------------------------------

def face_normals(pos, tris):
    """Area-weighted face normals in UE's winding (front face is (p2-p0) x (p1-p0))."""
    p0, p1, p2 = pos[tris[:, 0]], pos[tris[:, 1]], pos[tris[:, 2]]
    return np.cross(p2 - p0, p1 - p0)


def vertex_normals(pos, tris):
    """Unit vertex normals, area-weighted, welded across split vertices."""
    fn = face_normals(pos, tris)
    key = _weld_keys(pos)
    acc = np.zeros((key.max() + 1, 3))
    for c in range(3):
        np.add.at(acc, key[tris[:, c]], fn)
    n = acc[key]
    return _unit(n, np.array([0.0, 0.0, 1.0]))


def tangent_frame(pos, normals, uv, tris):
    """(tangent X, sign) per vertex from UV0, orthogonal to the normal."""
    p0, p1, p2 = pos[tris[:, 0]], pos[tris[:, 1]], pos[tris[:, 2]]
    t0, t1, t2 = uv[tris[:, 0]], uv[tris[:, 1]], uv[tris[:, 2]]
    e1, e2 = p1 - p0, p2 - p0
    d1, d2 = t1 - t0, t2 - t0
    det = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    r = np.where(np.abs(det) > 1e-12, 1.0 / np.where(det == 0, 1, det), 0.0)[:, None]
    tu = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) * r
    tv = (e2 * d1[:, 0:1] - e1 * d2[:, 0:1]) * r
    acc_u = np.zeros_like(pos, dtype=np.float64)
    acc_v = np.zeros_like(pos, dtype=np.float64)
    for c in range(3):
        np.add.at(acc_u, tris[:, c], tu)
        np.add.at(acc_v, tris[:, c], tv)
    n = normals.astype(np.float64)
    tx = acc_u - n * (acc_u * n).sum(1, keepdims=True)
    fallback = np.cross(n, np.where(np.abs(n[:, 2:3]) < 0.9, [[0, 0, 1.0]], [[1.0, 0, 0]]))
    tx = _unit(tx, None, fallback)
    sign = np.where((np.cross(n, tx) * acc_v).sum(1) < 0, -1.0, 1.0)
    return tx.astype(np.float32), sign.astype(np.float32)


def _unit(v, default=None, fallback=None):
    v = np.asarray(v, np.float64)
    ln = np.linalg.norm(v, axis=1, keepdims=True)
    bad = (ln[:, 0] < 1e-12)
    out = v / np.where(ln < 1e-12, 1, ln)
    if bad.any():
        out[bad] = fallback[bad] / np.linalg.norm(fallback[bad], axis=1, keepdims=True) \
            if fallback is not None else default
    return out


def _weld_keys(pos):
    """An id per distinct position (exact float equality)."""
    _u, inv = np.unique(np.ascontiguousarray(pos, np.float32).view('V12').ravel(),
                        return_inverse=True)
    return inv.ravel()


def pack_normal(v, w=None):
    """FPackedNormal: int8 components, round(x * 127) clamped; W = 127 or the sign."""
    out = np.empty((len(v), 4), np.int8)
    out[:, :3] = np.clip(np.rint(np.asarray(v, np.float64) * 127.0), -127, 127)
    out[:, 3] = 127 if w is None else np.where(w < 0, -127, 127)
    return out


def quantise_weights(w):
    """UE's import rule: truncate each weight to /255, add the remainder to the first."""
    q = np.floor(np.asarray(w, np.float64) * 255.0 + 1e-6).clip(0, 255).astype(np.int64)
    q[:, 0] += 255 - q.sum(1)
    return q


def adjacency(idx, pos, uv0):
    """PN-AEN dominant-corner index buffer (12 per triangle), as nvtess builds it."""
    tris = idx.reshape(-1, 3)
    key = _weld_keys(pos)
    # reversed edges by position pair; the last triangle to claim one wins
    edges = {}
    for a, b, c in tris:
        for x, y in ((a, b), (b, c), (c, a)):
            edges[(key[y], key[x])] = (y, x)
    # dominant corner: per position, the vertex with the smallest UV0
    corner = {}
    for a, b, c in tris:
        for v in (a, b, c):
            k = key[v]
            got = corner.get(k)
            if got is None or tuple(uv0[v]) < tuple(uv0[got]):
                corner[k] = v
    out = np.empty((len(tris), 12), np.int64)
    for t, (a, b, c) in enumerate(tris):
        # corners, three edge pairs (own edge when unshared), three dominant corners
        row = [a, b, c, a, b, b, c, c, a, a, b, c]
        for u, (x, y) in enumerate(((a, b), (b, c), (c, a))):
            hit = edges.get((key[x], key[y]))
            if hit is not None:
                row[3 + 2 * u], row[4 + 2 * u] = hit
        for u, v in enumerate((a, b, c)):
            row[9 + u] = corner[key[v]]
        out[t] = row
    return out.ravel()


def overlapping(pos):
    """FDuplicatedVerticesBuffer arrays for one section: (DupVertData, DupVertIndexData)."""
    key = _weld_keys(pos)
    order = np.argsort(key, kind='stable')
    groups = {}
    for v in order:
        groups.setdefault(key[v], []).append(int(v))
    data, pairs = [], []
    for v in range(len(pos)):
        others = [o for o in groups[key[v]] if o != v]
        pairs.append((len(others), len(data)))
        data.extend(others)
    if not data:
        data = [0]
        pairs = [(0, 0)] * len(pos)
    vd = np.array(data, '<u4').tobytes()
    pd = np.array(pairs, '<u4').tobytes()
    return (len(data), vd), (len(pairs), pd)


# -- encoding (plain description -> LOD) ---------------------------------------------------

def _resolve_bones(t, desc, rename):
    """Template bone index per description bone, by NAME (unweighted bones map to 0)."""
    used = set(np.unique(np.asarray(desc['influences'])[np.asarray(desc['weights']) > 0]).tolist())
    names = [rename.get(n, n) if rename else n for n in desc['bones']]
    if desc.get('skeleton'):
        # weighted bones the template lacks, then any unweighted ones the caller asks for
        # (desc['extra_bones']: e.g. a bone a Blueprint names, which must exist)
        wanted = [names[i] for i in sorted(used) if names[i] not in t.bone_index]
        wanted += [b for b in desc.get('extra_bones', ()) if b not in t.bone_index and b not in wanted]
        desc['added_bones'] = t.add_bones(wanted, desc['skeleton'])
    out, missing = [], []
    for i, name in enumerate(names):
        got = t.bone_index.get(name)
        if got is None and i in used:
            missing.append(name)
        out.append(0 if got is None else got)
    if missing:
        raise SystemExit('bones not in %s\'s reference skeleton: %s'
                         % (t.object, ', '.join(sorted(set(missing))[:20])))
    return np.array(out, np.int64)


def _influences(t, desc, rename, width):
    """(ref bone index, quantised weight) arrays, sorted by weight, `width` wide."""
    remap = _resolve_bones(t, desc, rename)
    inf = remap[np.asarray(desc['influences'], np.int64)]
    w = np.asarray(desc['weights'], np.float64).copy()
    n, k = inf.shape
    # merge repeats of one bone within a vertex
    for a in range(k):
        for b in range(a + 1, k):
            same = (inf[:, a] == inf[:, b]) & (w[:, b] > 0)
            w[same, a] += w[same, b]
            w[same, b] = 0
    order = np.argsort(-w, axis=1, kind='stable')
    inf = np.take_along_axis(inf, order, 1)
    w = np.take_along_axis(w, order, 1)
    used = int((w > 0).sum(1).max()) if n else 1
    if width is None:
        width = 4 if used <= 4 else 8
    if k < width:
        inf = np.pad(inf, ((0, 0), (0, width - k)))
        w = np.pad(w, ((0, 0), (0, width - k)))
    inf, w = inf[:, :width], w[:, :width]
    total = w.sum(1, keepdims=True)
    if (total <= 0).any():
        raise SystemExit('%d vertices have no bone weight' % int((total <= 0).sum()))
    w = w / total
    inf = np.where(w > 0, inf, inf[:, :1])
    return inf, quantise_weights(w), width


def _chunks(tri_bones, limit):
    """Split a slot's triangles so no section needs more than `limit` bones."""
    out, cur, bones = [], [], set()
    for t, tb in enumerate(tri_bones):
        grown = bones | tb
        if len(grown) > limit and cur:
            out.append((cur, bones))
            cur, grown = [], set(tb)
        cur.append(t)
        bones = grown
    if cur:
        out.append((cur, bones))
    return out


def _bind_cloth(t, sec, spec, asset_index, P, N, TX):
    """Bind a written section to a cloth spec (UClothingAssetCommon::BindToSkeletalMesh):
    its mapping to the sim mesh, the asset index and Guid, and the cloth's bones added to
    the section's bone map (after its own, so the weight buffer's indices still hold)."""
    from riftlord.formats.omd3 import omd3cloth
    missing = [b for b in spec['bones'] if b not in t.bone_index]
    if missing:
        raise SystemExit('cloth %s uses bones the mesh lacks: %s' % (spec['name'], missing))
    spec['bone_indices'] = [t.bone_index[b] for b in spec['bones']]
    # 'skinned_only_flags' False: no 0xFFFF "skinned only" marks, so every render vertex follows
    # the sim mesh (as on Gabrielle's flaps and loincloth); default True (BindToSkeletalMesh)
    data = omd3cloth.mesh_to_mesh(P, N, TX, spec['vertices'], spec['indices'],
                                  spec['max_distance'] if spec.get('skinned_only_flags', True) else None)
    sec['cloth_mapping'] = (len(data), data.tobytes())
    sec['cloth_asset'] = asset_index
    sec['clothing_data'] = bytes(spec['guid']) + struct.pack('<i', CLOTH_LOD)
    bone_map = list(sec['bone_map'])
    for b in spec['bone_indices']:
        if b not in bone_map:
            bone_map.append(b)
    if len(bone_map) > MAX_SECTION_BONES:
        raise SystemExit('cloth %s needs %d bones in one section' % (spec['name'], len(bone_map)))
    sec['bone_map'] = bone_map


def build_lod(t, desc, rename=None, max_bones=MAX_SECTION_BONES, influences=None,
              uv_sets=None, cloth=None):
    """One FSkeletalMeshLODRenderData dict for the codec, plus a report.

    cloth: None, or a list of cloth specs (omd3cloth.asset_props describes the sim fields);
    each spec's 'triangles' (indices into desc['triangles']) become their own section(s),
    bound to that spec's sim mesh."""
    pos = np.asarray(desc['positions'], np.float32)
    tris = np.asarray(desc['triangles'], np.int64).reshape(-1, 3)
    n = len(pos)
    if not len(tris) or tris.min() < 0 or tris.max() >= n:
        raise SystemExit('triangles index outside the %d vertices' % n)
    mats = np.asarray(desc.get('materials', np.zeros(len(tris))), np.int64)
    uvs = [np.asarray(u, np.float32) for u in desc['uvs']]
    tmpl = t.model['lods'][0]
    ntex = uv_sets or tmpl['buffers']['vb_meta'][0]
    while len(uvs) < ntex:
        uvs.append(uvs[0])
    uvs = uvs[:ntex]
    normals = desc.get('normals')
    normals = vertex_normals(pos, tris) if normals is None else _unit(normals, [0, 0, 1.0])
    if desc.get('tangents') is None:
        tx, sign = tangent_frame(pos, normals, uvs[0], tris)
    else:
        tx = _unit(desc['tangents'], [1.0, 0, 0])
        sign = np.asarray(desc.get('signs', np.ones(n)), np.float32)
    bones, qw, width = _influences(t, desc, rename, influences)

    # sections: slot order, then bone-limit chunks; vertices duplicated per section.
    # With `cloth`, each cloth group's triangles form their own section(s) after the slot's
    # plain triangles (group -1); without it every triangle is group -1, as before.
    group = np.full(len(tris), -1, np.int64)
    for k, spec in enumerate(cloth or ()):
        group[np.asarray(spec['triangles'], np.int64)] = k
    sections = []
    base_vertex = 0
    for slot in sorted(set(mats.tolist())):
        for g in sorted(set(group[mats == slot].tolist())):
            sel = np.nonzero((mats == slot) & (group == g))[0]
            tri_bones = [set(np.unique(bones[tris[s]][qw[tris[s]] > 0]).tolist()) for s in sel]
            for part, bset in _chunks(tri_bones, max_bones):
                ts = tris[sel[part]]
                # vertices in first-use order, so the stream follows the triangles
                verts, at = np.unique(ts.ravel(), return_index=True)
                verts = verts[np.argsort(at)]
                first = np.full(n, -1)
                first[verts] = np.arange(len(verts))
                bone_map = sorted(bset)
                sections.append({'slot': int(slot), 'verts': verts, 'bone_map': bone_map,
                                 'tris': first[ts] + base_vertex, 'base_vertex': base_vertex,
                                 'cloth': g})
                base_vertex += len(verts)
    total = base_vertex
    if sum(len(s['tris']) for s in sections) != len(tris):
        raise AssertionError('triangles lost while sectioning')

    # the vertex stream in section order; `order` maps output -> input vertex
    order = np.concatenate([s['verts'] for s in sections])
    P = pos[order]
    N = normals[order]
    TX = tx[order]
    SG = sign[order]
    UV = np.stack([u[order] for u in uvs], 1)
    B = bones[order]
    Q = qw[order]
    # bone indices become positions in each section's bone map
    local = np.zeros_like(B)
    for s in sections:
        lo, hi = s['base_vertex'], s['base_vertex'] + len(s['verts'])
        lut = np.zeros(len(t.bone_names), np.int64)
        lut[s['bone_map']] = np.arange(len(s['bone_map']))
        local[lo:hi] = np.where(Q[lo:hi] > 0, lut[B[lo:hi]], 0)
    if local.max(initial=0) > 255:
        raise SystemExit('a section bone index exceeds 8 bits')
    idx = np.concatenate([s['tris'].ravel() for s in sections])
    width_i = 4 if total >= INDEX32_FROM else 2
    itype = '<u2' if width_i == 2 else '<u4'

    tb = tmpl['buffers']
    sec0 = tmpl['sections'][0]
    out_secs = []
    base_index = 0
    for s in sections:
        lo, hi = s['base_vertex'], s['base_vertex'] + len(s['verts'])
        dup_v, dup_i = overlapping(P[lo:hi])
        out_secs.append({
            'strip': sec0['strip'], 'material': s['slot'], 'base_index': base_index,
            'triangles': len(s['tris']), 'recompute_tangent': 0,
            'recompute_mask': sec0['recompute_mask'], 'cast_shadow': 1,
            'base_vertex': lo, 'cloth_mapping': (0, b''), 'bone_map': s['bone_map'],
            'vertices': hi - lo, 'max_influences': int((Q[lo:hi] > 0).sum(1).max()),
            'cloth_asset': -1, 'clothing_data': b'\0' * 16 + struct.pack('<i', -1),
            'dup_verts': dup_v, 'dup_index': dup_i, 'disabled': 0})
        base_index += 3 * len(s['tris'])
        if s['cloth'] >= 0:
            _bind_cloth(t, out_secs[-1], cloth[s['cloth']], s['cloth'], P[lo:hi], N[lo:hi], TX[lo:hi])

    # buffers, in the layout omd3skm_format writes
    tangents = np.stack([pack_normal(TX), pack_normal(N, SG)], 1)
    weights = np.concatenate([local.astype('u1'), Q.astype('u1')], 1)
    uvh = UV.astype('<f2')
    buf = {
        'strip': tb['strip'], 'index_width': width_i,
        'indices': (width_i, len(idx), idx.astype(itype).tobytes()),
        'pos_meta': (12, total), 'positions': (12, total, P.astype('<f4').tobytes()),
        'vb_strip': tb['vb_strip'], 'vb_meta': (ntex, total, 0, 0),
        'tangents': (8, total, tangents.tobytes()),
        'texcoords': (4, total * ntex, uvh.tobytes()),
        'sw_strip': tb['sw_strip'], 'sw_meta': (0, width, width * total, total, 0),
        'weights': (1, total * width * 2, weights.tobytes()),
        'lk_strip': tb['lk_strip'], 'lk_vertices': 0, 'lookup': (4, 0, b''),
        'weight_profiles': 0, 'ray_tracing': (0, b''),
    }
    if t.ctx['vertex_colors']:
        col = desc.get('colors')
        if col is None:
            col = np.tile(_common_color(tb), (n, 1))
        col = np.asarray(col, np.uint8)[order]
        buf['col_strip'] = tb['col_strip']
        buf['col_meta'] = (4, total)
        buf['colors'] = (4, total, col.tobytes())
    if 'adjacency' in tb:
        adj = adjacency(idx, P, UV[:, 0].astype(np.float32))
        buf['adj_width'] = width_i
        buf['adjacency'] = (width_i, len(adj), adj.astype(itype).tobytes())
    else:
        buf['strip'] = (tb['strip'][0], tb['strip'][1] | fmt.CDSF_ADJACENCY)
    if cloth:
        # FSkeletalMeshVertexClothBuffer: every cloth section's mapping in section order, and
        # per section (BaseVertexIndex << 32 | first mapping) or 0 (GetClothMappingData)
        mapping, keys = [], []
        for s in out_secs:
            if s['cloth_mapping'][0]:
                keys.append((s['base_vertex'] << 32) | sum(m[0] for m in mapping))
                mapping.append(s['cloth_mapping'])
            else:
                keys.append(0)
        buf['cloth_strip'] = CLOTH_BUFFER_STRIP
        buf['cloth'] = (fmt.CLOTH_VERT, sum(m[0] for m in mapping), b''.join(m[1] for m in mapping))
        buf['cloth_index_mapping'] = (len(keys), np.array(keys, '<u8').tobytes())

    # every bone a section uses, plus its ancestors
    active = set()
    for s in out_secs:
        for bone in s['bone_map']:
            while bone >= 0 and bone not in active:
                active.add(bone)
                bone = t.parents[bone]
    lod = {'strip': tmpl['strip'], 'cooked_out': 0, 'inlined': 1,
           'required_bones': list(range(len(t.bone_names))),
           'sections': out_secs, 'active_bones': sorted(active), 'buffers': buf}
    lod['buffers_size'] = fmt.streamed_size(lod, t.ctx)
    report = {'vertices': total, 'input_vertices': n, 'triangles': len(tris),
              'sections': [(s['material'], s['triangles'], s['vertices'], len(s['bone_map']))
                           for s in out_secs],
              'influences': width, 'index_width': width_i, 'order': order}
    return lod, report


def _common_color(tb):
    """The template's most frequent vertex colour (the material may read it)."""
    if not tb.get('colors'):
        return np.array([255, 255, 255, 255], np.uint8)
    col = np.frombuffer(tb['colors'][2], 'u1').reshape(-1, 4)
    vals, counts = np.unique(col, axis=0, return_counts=True)
    return vals[counts.argmax()]


# -- tagged-property surgery ----------------------------------------------------------

def _array_keep_first(data, names, tag, keep):
    """Shrink an ArrayProperty of structs to its first `keep` elements, sizes fixed up."""
    name, kind, at, value_at, size, _flag = tag
    count = struct.unpack_from('<i', data, value_at)[0]
    if count <= keep:
        return data, 0
    inner = value_at + 4
    elem_size_at = inner + 16
    sname = fmt._fname(data, inner + 24, names)
    body = inner + 24 + 24 + 1
    o = body
    ends = []
    for _k in range(count):
        o = _struct_end(data, names, sname, o)
        ends.append(o)
    if o != value_at + size:
        raise AssertionError('%s did not parse to its tag size' % name)
    new_end = ends[keep - 1]
    removed = (value_at + size) - new_end
    out = bytearray(data[:new_end] + data[value_at + size:])
    struct.pack_into('<i', out, value_at, keep)
    struct.pack_into('<i', out, elem_size_at,
                     struct.unpack_from('<i', data, elem_size_at)[0] - removed)
    struct.pack_into('<i', out, at + 16, size - removed)
    return bytes(out), removed


def _struct_end(data, names, sname, o):
    if sname == 'SkeletalMeshSamplingLODBuiltData':
        n1 = struct.unpack_from('<i', data, o)[0]
        o += 4 + 4 * n1
        n2 = struct.unpack_from('<i', data, o)[0]
        return o + 4 + 4 * n2 + 4
    _got, end = fmt.tags(data, names, o)
    return end


def single_lod_tags(t):
    """The tagged properties with LODInfo (and per-LOD sampling data) cut to LOD0."""
    data = t.export['data'][:t.ctx['end']]
    lods = len(t.model['lods'])
    got, _end = fmt.tags(data, t.names)
    by = {g[0]: g for g in got}
    data2, _removed = _array_keep_first(data, t.names, by['LODInfo'], 1)
    got, _end = fmt.tags(data2, t.names)
    by = {g[0]: g for g in got}
    if 'SamplingInfo' in by:
        data2 = _trim_sampling(data2, t.names, by['SamplingInfo'], lods)
    return data2


def _trim_sampling(data, names, tag, lods):
    """WholeMeshBuiltData holds one sampler per LOD; keep LOD0's, emptied."""
    _n, _k, at, value_at, size, _f = tag
    inner, end = fmt.tags(data, names, value_at)
    built = [g for g in inner if g[0] == 'BuiltData']
    if not built:
        return data
    sub, _e = fmt.tags(data, names, built[0][3])
    whole = [g for g in sub if g[0] == 'WholeMeshBuiltData']
    if not whole:
        return data
    w = whole[0]
    count = struct.unpack_from('<i', data, w[3])[0]
    first = w[3] + 4 + 24 + 24 + 1
    if count and struct.unpack_from('<i', data, first)[0]:
        raise SystemExit('the template samples its triangles uniformly; rebuilding the '
                         'area sampler is not implemented')
    if count != lods or count <= 1:
        return data
    new, removed = _array_keep_first(data, names, w, 1)
    out = bytearray(new)
    for g in (built[0], tag):
        struct.pack_into('<i', out, g[2] + 16, struct.unpack_from('<i', data, g[2] + 16)[0] - removed)
    return bytes(out)


# -- materials and names -----------------------------------------------------------------

def _post_process_tag(pkg, t, tagged, path, obj):
    """`tagged` with PostProcessAnimBlueprint = path.obj (an AnimBlueprintGeneratedClass import,
    added with its Package and a create-before-serialization preload dependency)."""
    names = [n for n, _h in pkg.names]
    if 'PostProcessAnimBlueprint' in [g[0] for g in fmt.tags(tagged, names)[0]]:
        raise SystemExit('the template already sets PostProcessAnimBlueprint')
    outer = pkg.add_import('/Script/CoreUObject', 'Package', 0, path)
    index = pkg.add_import('/Script/Engine', 'AnimBlueprintGeneratedClass', outer, obj)
    pkg.add_preload_dependency(t.index, index, 'create_before_serialization')
    tag = struct.pack('<iiiiii', pkg.add_name('PostProcessAnimBlueprint'), 0, pkg.add_name('ObjectProperty'), 0, 4, 0) \
        + b'\0' + struct.pack('<i', index)
    if tagged[-8:] != struct.pack('<ii', pkg.add_name('None'), 0):
        raise SystemExit('the tagged properties do not end with None')
    return tagged[:-8] + tag + tagged[-8:]


def _material_import(pkg, path, obj, cls='MaterialInstanceConstant'):
    """Package index of a material import, added (with its Package outer) when new."""
    names = [n for n, _h in pkg.names]
    for i, rec in enumerate(pkg.imports):
        r = struct.unpack('<7i', rec)
        if names[r[5]] == obj and names[r[2]] in ('MaterialInstanceConstant', 'Material') \
                and r[4] < 0 and names[struct.unpack('<7i', pkg.imports[-r[4] - 1])[5]] == path:
            return -(i + 1), False
    outer = None
    for i, rec in enumerate(pkg.imports):
        r = struct.unpack('<7i', rec)
        if names[r[2]] == 'Package' and names[r[5]] == path:
            outer = -(i + 1)
    if outer is None:
        outer = pkg.add_import('/Script/CoreUObject', 'Package', 0, path)
    return pkg.add_import('/Script/Engine', cls, outer, obj), True


def rename(pkg, old_path, new_path):
    """Point a cloned package at a new path; the asset takes the path's leaf name."""
    old_obj, new_obj = old_path.rsplit('/', 1)[-1], new_path.rsplit('/', 1)[-1]
    hits = 0
    for i, (s, _h) in enumerate(pkg.names):
        if s == old_path:
            pkg.names[i] = (new_path, fname_hashes(new_path))
            hits += 1
        elif s == old_obj:
            pkg.names[i] = (new_obj, fname_hashes(new_obj))
            hits += 1
    if hits != 2:
        raise SystemExit('%s does not name itself as expected (%d hits)' % (old_path, hits))


# -- the writer ---------------------------------------------------------------------

def write(template, desc, new_path=None, materials=None, rename_bones=None,
          max_bones=MAX_SECTION_BONES, influences=None, out_dir=None, pose='source',
          post_process=None, cloth=None):
    """Write `desc` as a single-LOD SkeletalMesh cloned from `template`.

    materials: None keeps the template's slots; otherwise a list, one per slot used by
    `desc['materials']`, each an int (a template slot to reuse) or a dict
    {'path': '/Game/.../X_INST', 'object': 'X_INST', 'slot': 'SlotName',
    'class': 'MaterialInstanceConstant'}. pose: 'source' takes the reference pose of
    every bone `desc['skeleton']` names, 'template' keeps the template's.
    post_process: (package path, class object name) of an AnimBlueprintGeneratedClass to set as
    the mesh's PostProcessAnimBlueprint (a tagged ObjectProperty; run after the main AnimBP on
    every component wearing the mesh).
    cloth: None (default: no cloth, output unchanged), or a list of cloth specs: each spec's
    triangles become a cloth section bound to a new UClothingAssetCommon (+ UClothConfigNv)
    export in the same package (omd3cloth.attach; fields in omd3cloth.asset_props).
    Returns {ext: bytes} and the report.
    """
    # a fresh parse every time: writing edits the template's tables in place
    t = Template(template.path, template.blobs) if isinstance(template, Template) \
        else Template(template)
    t.refuse_unsupported()
    lod, report = build_lod(t, desc, rename_bones, max_bones, influences, cloth=cloth)
    if desc.get('skeleton') and pose == 'source':
        # the vertices were bound against the source's reference pose
        src = {n: p for n, p in zip(desc['skeleton']['names'], desc['skeleton']['pose'])}
        t.model['pose'] = [tuple(src.get(n, p)) for n, p in zip(t.bone_names, t.model['pose'])]
        report['pose_from_source'] = sum(n in src for n in t.bone_names)
    m = dict(t.model)
    m['lods'] = [lod]
    m['inlined_lods'] = m['non_optional_lods'] = 1
    P = np.frombuffer(lod['buffers']['positions'][2], '<f4').reshape(-1, 3)
    lo, hi = P.min(0).astype(np.float64), P.max(0).astype(np.float64)
    ext = (hi - lo) / 2
    m['bounds'] = tuple(((lo + hi) / 2).tolist() + ext.tolist() + [float(np.linalg.norm(ext))])
    pkg = t.pkg
    if materials is not None:
        mats = []
        for k, spec in enumerate(materials):
            if isinstance(spec, int):
                mats.append(dict(t.model['materials'][spec]))
                continue
            index, new = _material_import(pkg, spec['path'], spec['object'],
                                          spec.get('class', 'MaterialInstanceConstant'))
            if new:
                pkg.add_preload_dependency(t.index, index, 'create_before_serialization')
            base = dict(t.model['materials'][min(k, len(t.model['materials']) - 1)])
            base['object'] = index
            base['slot'] = (pkg.add_name(spec.get('slot', spec['object'])), 0)
            mats.append(base)
        m['materials'] = mats
    used = max(s['material'] for s in lod['sections'])
    if used >= len(m['materials']):
        raise SystemExit('a triangle names slot %d; the mesh has %d' % (used, len(m['materials'])))
    tagged = single_lod_tags(t)
    if post_process:
        tagged = _post_process_tag(pkg, t, tagged, *post_process)
    if cloth:
        from riftlord.formats.omd3 import omd3cloth
        tagged, report['cloth_exports'] = omd3cloth.attach(pkg, t.index, tagged, cloth)
    ctx = dict(t.ctx)
    native = fmt.build(m, ctx)
    pkg.exports[t.index]['data'] = tagged + native
    if new_path and new_path != t.path:
        rename(pkg, t.path, new_path)
    ua, ux = pkg.write()
    blobs = {'.uasset': ua, '.uexp': ux}
    report['bounds'] = m['bounds']
    report['export_size'] = len(tagged) + len(native)
    if out_dir:
        leaf = (new_path or t.path).rsplit('/', 1)[-1]
        os.makedirs(out_dir, exist_ok=True)
        for e, b in blobs.items():
            with open(os.path.join(out_dir, leaf + e), 'wb') as fh:
                fh.write(b)
    return blobs, report


def add_cloth(template, cloth, reskin=None):
    """Bind cloth to an already written single-LOD mesh, in place of re-encoding it.

    template: a Template of the written package; cloth: specs as for `write`, whose
    'triangles' index the written LOD's index buffer. Each section holding cloth triangles is
    split: its other triangles keep the section (vertices in their old order), the cloth ones
    follow as a new section with the same material and bone map (plus the cloth's bones), so
    every untouched buffer row is copied byte for byte -- only rows are permuted, never
    re-quantised. The cloth vertices must not be shared with other triangles.
    reskin: {cloth index: bone name} -- that cloth's render vertices weighted 100% to the bone
    (their skinning then matches a sim mesh skinned the same way).
    Returns ({ext: bytes}, report)."""
    from riftlord.formats.omd3 import omd3cloth
    t = Template(template.path, template.blobs) if isinstance(template, Template) else Template(template)
    if len(t.model['lods']) != 1:
        raise SystemExit('add_cloth binds single-LOD meshes (this has %d LODs)' % len(t.model['lods']))
    if 'MeshClothingAssets' in [g[0] for g in t.ctx['tags']]:
        raise SystemExit('%s already carries cloth' % t.object)
    L = t.model['lods'][0]
    b = L['buffers']
    n = b['pos_meta'][1]
    width = b['index_width']
    itype = '<u2' if width == 2 else '<u4'
    tris = np.frombuffer(b['indices'][2], itype).astype(np.int64).reshape(-1, 3)
    group = np.full(len(tris), -1, np.int64)
    for k, spec in enumerate(cloth):
        tsel = np.asarray(spec['triangles'], np.int64)
        if (group[tsel] >= 0).any():
            raise SystemExit('a triangle is claimed by two cloth specs')
        group[tsel] = k
    P = np.frombuffer(b['positions'][2], '<f4').reshape(n, 3)
    ntex, _nv, full_uv, hi_tan = b['vb_meta']
    tan = (np.frombuffer(b['tangents'][2], '<i2').reshape(n, 2, 4) / 32767.0 if hi_tan
           else np.frombuffer(b['tangents'][2], 'i1').reshape(n, 2, 4) / 127.0)
    _var, inf, _nb, _n, b16 = b['sw_meta']
    wrow = inf * (3 if b16 else 2)
    weights = np.frombuffer(b['weights'][2], 'u1').reshape(n, wrow).copy()
    perm = np.arange(n)                  # new vertex position -> old vertex
    secs, tri_order = [], []
    for s in L['sections']:
        t0 = s['base_index'] // 3
        span = np.arange(t0, t0 + s['triangles'])
        g = group[span]
        if (g < 0).all():
            secs.append((dict(s), span, None))
            continue
        lo, hi = s['base_vertex'], s['base_vertex'] + s['vertices']
        owner = np.full(hi - lo, -1, np.int64)
        for gg in sorted(set(g.tolist()) - {-1}):
            vs = np.unique(tris[span[g == gg]])
            plain = np.unique(tris[span[g != gg]])
            if np.intersect1d(vs, plain).size:
                raise SystemExit('cloth %d shares vertices with other triangles of section %d'
                                 % (gg, L['sections'].index(s)))
            owner[vs - lo] = gg
        at = lo
        for gg in sorted(set(owner.tolist()) | set(g.tolist())):
            old = lo + np.nonzero(owner == gg)[0]
            perm[at:at + len(old)] = old
            part = dict(s)
            part['base_vertex'], part['vertices'] = at, len(old)
            part['cloth_group'] = gg
            secs.append((part, span[g == gg], (at, old)))
            at += len(old)
    inv = np.empty(n, np.int64)
    inv[perm] = np.arange(n)
    # rows permuted, never re-encoded
    def rows(raw, stride):
        return np.frombuffer(raw, 'u1').reshape(n, stride)[perm].tobytes()
    nb = dict(b)
    nb['positions'] = (12, n, rows(b['positions'][2], 12))
    nb['tangents'] = (b['tangents'][0], n, rows(b['tangents'][2], b['tangents'][0]))
    nb['texcoords'] = (b['texcoords'][0], b['texcoords'][1], rows(b['texcoords'][2], b['texcoords'][0] * ntex))
    W8 = weights[perm]
    if b.get('colors'):
        nb['colors'] = (4, n, rows(b['colors'][2], 4))
    new_tris, new_adj, out_secs = [], [], []
    adj = (np.frombuffer(b['adjacency'][2], itype).astype(np.int64).reshape(-1, 12)
           if 'adjacency' in b else None)
    base_index = 0
    report = {'cloth_sections': [], 'reskinned': 0}
    for part, span, moved in secs:
        sec = dict(part)
        gg = sec.pop('cloth_group', -1)
        sec['base_index'] = base_index
        sec['triangles'] = len(span)
        new_tris.append(inv[tris[span]])
        if adj is not None:
            new_adj.append(inv[adj[span]])
        base_index += 3 * len(span)
        if moved is not None:
            lo, hi = sec['base_vertex'], sec['base_vertex'] + sec['vertices']
            if gg >= 0 and reskin and gg in reskin:
                if reskin[gg] not in t.bone_index:
                    raise SystemExit('cannot reskin cloth %d to %s: the mesh has no such bone' % (gg, reskin[gg]))
                bone = t.bone_index[reskin[gg]]
                bm = list(sec['bone_map'])
                if bone not in bm:
                    bm.append(bone)
                sec['bone_map'] = bm
                W8[lo:hi, :] = 0
                W8[lo:hi, 0] = bm.index(bone)
                W8[lo:hi, inf] = 255
                report['reskinned'] += hi - lo
            sec['dup_verts'], sec['dup_index'] = overlapping(np.frombuffer(nb['positions'][2], '<f4').reshape(n, 3)[lo:hi])
            sec['max_influences'] = int((W8[lo:hi, inf:inf * 2] > 0).sum(1).max()) if hi > lo else 1
            if gg >= 0:
                Pn = P[perm[lo:hi]]
                _bind_cloth(t, sec, cloth[gg], gg, Pn, tan[perm[lo:hi], 1, :3], tan[perm[lo:hi], 0, :3])
                report['cloth_sections'].append(len(out_secs))
        out_secs.append(sec)
    nb['weights'] = (1, n * wrow, W8.tobytes())
    idx = np.concatenate(new_tris).ravel()
    nb['indices'] = (width, len(idx), idx.astype(itype).tobytes())
    if adj is not None:
        a = np.concatenate(new_adj).ravel()
        nb['adjacency'] = (width, len(a), a.astype(itype).tobytes())
    mapping, keys = [], []
    for s in out_secs:
        if s['cloth_mapping'][0]:
            keys.append((s['base_vertex'] << 32) | sum(m[0] for m in mapping))
            mapping.append(s['cloth_mapping'])
        else:
            keys.append(0)
    nb['cloth_strip'] = CLOTH_BUFFER_STRIP
    nb['cloth'] = (fmt.CLOTH_VERT, sum(m[0] for m in mapping), b''.join(m[1] for m in mapping))
    nb['cloth_index_mapping'] = (len(keys), np.array(keys, '<u8').tobytes())
    active = set(L['active_bones'])
    for s in out_secs:
        for bone in s['bone_map']:
            while bone >= 0 and bone not in active:
                active.add(bone)
                bone = t.parents[bone]
    lod = dict(L, sections=out_secs, buffers=nb, active_bones=sorted(active))
    lod['buffers_size'] = fmt.streamed_size(lod, t.ctx)
    m = dict(t.model)
    m['lods'] = [lod]
    tagged = t.export['data'][:t.ctx['end']]
    tagged, report['cloth_exports'] = omd3cloth.attach(t.pkg, t.index, tagged, cloth)
    t.pkg.exports[t.index]['data'] = tagged + fmt.build(m, t.ctx)
    ua, ux = t.pkg.write()
    report['order'] = perm
    return {'.uasset': ua, '.uexp': ux}, report


# -- checks -------------------------------------------------------------------------

def strict_check(blobs):
    """Re-parse a written package with the strict codec; [problems]."""
    bad = []
    pkg = Package(blobs['.uasset'], blobs['.uexp'])
    names = [n for n, _h in pkg.names]
    sk = None
    for e in pkg.exports:
        ci = struct.unpack_from('<i', e['meta'], 0)[0]
        if ci < 0 and names[struct.unpack('<7i', pkg.imports[-ci - 1])[5]] == 'SkeletalMesh':
            sk = e
    data = sk['data']
    ctx = fmt.context(data, names)
    m = fmt.parse(data, ctx['end'], ctx)
    if m['rest']:
        bad.append('%d unparsed bytes after the native block' % len(m['rest']))
    if fmt.build(m, ctx) != data[ctx['end']:]:
        bad.append('native block does not rebuild byte-identical')
    nl = len(m['lods'])
    lodinfo = [g for g in ctx['tags'] if g[0] == 'LODInfo']
    if lodinfo and struct.unpack_from('<i', data, lodinfo[0][3])[0] != nl:
        bad.append('LODInfo has a different count from the render data')
    if (m['inlined_lods'], m['non_optional_lods']) != (nl, nl):
        bad.append('inlined/non-optional LOD counts disagree with %d LODs' % nl)
    for li, L in enumerate(m['lods']):
        b = L['buffers']
        n = b['pos_meta'][1]
        w = b['index_width']
        idx = np.frombuffer(b['indices'][2], '<u2' if w == 2 else '<u4')
        if L['buffers_size'] != fmt.streamed_size(L, ctx):
            bad.append('LOD%d BuffersSize %d is not the streamed size' % (li, L['buffers_size']))
        if (w == 4) != (n >= INDEX32_FROM):
            bad.append('LOD%d index width %d for %d vertices' % (li, w, n))
        if b['vb_meta'][1] != n or b['sw_meta'][3] != n:
            bad.append('LOD%d vertex counts disagree' % li)
        if b['sw_meta'][2] != n * b['sw_meta'][1]:
            bad.append('LOD%d NumBones is not vertices x influences' % li)
        if 'adjacency' in b and b['adjacency'][1] != 4 * len(idx):
            bad.append('LOD%d adjacency is not 12 per triangle' % li)
        inf = b['sw_meta'][1]
        wt = np.frombuffer(b['weights'][2], 'u1').reshape(n, 2 * inf)
        if (wt[:, inf:].sum(1) != 255).any():
            bad.append('LOD%d weights do not sum to 255' % li)
        tri_total = 0
        vend = 0
        for si, s in enumerate(L['sections']):
            if s['base_index'] != 3 * tri_total or s['base_vertex'] != vend:
                bad.append('LOD%d section %d is not contiguous' % (li, si))
            seg = idx[s['base_index']:s['base_index'] + 3 * s['triangles']]
            if len(seg) and (seg.min() < s['base_vertex']
                             or seg.max() >= s['base_vertex'] + s['vertices']):
                bad.append('LOD%d section %d indexes outside its vertices' % (li, si))
            loc = wt[s['base_vertex']:s['base_vertex'] + s['vertices'], :inf]
            if loc.size and loc.max() >= len(s['bone_map']):
                bad.append('LOD%d section %d bone index past its bone map' % (li, si))
            if any(bm >= len(m['bones']) for bm in s['bone_map']):
                bad.append('LOD%d section %d bone map past the skeleton' % (li, si))
            if not set(s['bone_map']) <= set(L['active_bones']):
                bad.append('LOD%d section %d bones missing from ActiveBoneIndices' % (li, si))
            if len(s['dup_index'][1]) != 8 * s['vertices'] or s['dup_verts'][0] < 1:
                bad.append('LOD%d section %d duplicated-vertex buffer malformed' % (li, si))
            if s['material'] >= len(m['materials']):
                bad.append('LOD%d section %d names a missing slot' % (li, si))
            tri_total += s['triangles']
            vend += s['vertices']
        if tri_total * 3 != len(idx) or vend != n:
            bad.append('LOD%d sections cover %d tris/%d verts of %d/%d'
                       % (li, tri_total, vend, len(idx) // 3, n))
    P = np.frombuffer(m['lods'][0]['buffers']['positions'][2], '<f4').reshape(-1, 3)
    lo, hi = P.min(0), P.max(0)
    if not np.allclose(m['bounds'][:3], (lo + hi) / 2, atol=1e-3) \
            or not np.allclose(m['bounds'][3:6], (hi - lo) / 2, atol=1e-3):
        bad.append('ImportedBounds do not match LOD0 positions')
    return bad, m


def skeleton_bones(path):
    """(names, parent indices) of a shipped USkeleton's reference skeleton."""
    ua, ux = gamefiles.package(path)
    pkg = Package(ua, ux)
    names = [n for n, _h in pkg.names]
    for e in pkg.exports:
        ci = struct.unpack_from('<i', e['meta'], 0)[0]
        if ci < 0 and names[struct.unpack('<7i', pkg.imports[-ci - 1])[5]] == 'Skeleton':
            _tags, end = fmt.tags(e['data'], names)
            bones, _pose, _map = fmt.parse_skeleton(e['data'], end)
            fn = [names[i] if n == 0 else '%s_%d' % (names[i], n - 1) for i, n, _p in bones]
            return fn, [p for _i, _n, p in bones]
    raise SystemExit('%s holds no Skeleton' % path)


def compatible(mesh_names, mesh_parents, skel_names, skel_parents):
    """USkeleton::IsCompatibleMesh for a mesh's reference skeleton: [problems]."""
    at = {n: i for i, n in enumerate(skel_names)}
    bad = []

    def chain_matches(si, mi):
        # DoesParentChainMatch: parent names agree all the way up
        if si == 0:
            return skel_names[0] == mesh_names[0]
        while True:
            sp, mp = skel_parents[si], mesh_parents[mi]
            if sp < 0 or mp < 0:
                return sp < 0 and mp < 0
            if skel_names[sp] != mesh_names[mp]:
                return False
            si, mi = sp, mp

    for mi, name in enumerate(mesh_names):
        si = at.get(name)
        if si is None:
            bad.append('mesh bone %s is not in the skeleton' % name)
        elif not chain_matches(si, mi):
            bad.append('mesh bone %s has a different parent chain' % name)
    return bad


def independent_check(blobs):
    """omd3skm's signature reader on the written bytes: (verts, tris, lods, sections)."""
    from riftlord.build.levelbuild import Package as LBPackage
    from riftlord.formats.omd3 import omd3mesh
    pkg = LBPackage(blobs['.uasset'], blobs['.uexp'])
    pkg.load_payloads()
    e = omd3skm._skeletal_export(pkg)
    data, start = omd3mesh.cooked_region(pkg, e)
    lods = omd3skm.position_buffers(data, start)
    pos_at, verts = lods[0]
    idx, _w, hdr = omd3skm.index_buffer(data, start, pos_at, verts)
    secs = omd3skm.sections(data, start, hdr, len(idx) // 3)
    return {'vertices': verts, 'triangles': len(idx) // 3, 'lods': len(lods),
            'sections': secs, 'uvs': bool(omd3skm.uvs_at(data, pos_at, verts)),
            'slots': omd3skm.material_slots(pkg, data, start)}


def pkgcheck_problems(blobs, label='mesh'):
    """pkgcheck's structural readback."""
    from riftlord.core import pkgcheck
    return pkgcheck.problems(blobs['.uasset'], blobs['.uexp'], label)


# -- command line ---------------------------------------------------------------------

def identity(names):
    """Strict parse + rebuild of shipped meshes; True per name when byte-identical."""
    out = {}
    for n in names:
        t = Template(n)
        native = fmt.build(t.model, t.ctx)
        t.export['data'] = t.export['data'][:t.ctx['end']] + native
        ua, ux = t.pkg.write()
        out[n] = (ua == t.blobs['.uasset'] and ux == t.blobs['.uexp'], len(ux))
    return out


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    a = sub.add_parser('identity', help='round-trip shipped meshes byte-identical')
    a.add_argument('names', nargs='+')
    a = sub.add_parser('rewrite', help='decode a mesh and write it back through the encoder')
    a.add_argument('template')
    a.add_argument('--source', help='take geometry from this mesh (bones remapped by name)')
    a.add_argument('--path', help='new /Game/... package path')
    a.add_argument('--out', required=True)
    args = ap.parse_args(argv)
    if args.cmd == 'identity':
        for n, (ok, size) in identity(args.names).items():
            print('%-34s %s  (%d byte .uexp)' % (n, 'byte-identical' if ok else 'DIFFERS', size))
        return 0
    t = Template(args.template)
    desc = decode(Template(args.source) if args.source else t)
    blobs, report = write(t, desc, args.path, out_dir=args.out)
    bad, _m = strict_check(blobs)
    print({k: v for k, v in report.items() if k != 'order'}, 'added bones:', desc.get('added_bones'))
    print('strict check:', bad or 'clean')
    print('skeleton compatible:', compatible(t.bone_names, t.parents,
                                             *skeleton_bones(t.skeleton_path())) or 'yes')
    print('pkgcheck:', pkgcheck_problems(blobs) or 'clean')
    print('omd3skm reads:', {k: v for k, v in independent_check(blobs).items() if k != 'sections'})
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
