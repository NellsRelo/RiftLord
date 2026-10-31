"""Vision Engine scenes (OMD1 and OMD2) for the Trace tool (riftlord/trace.py).

What a scene draws is read from its cooked zones, the files the game itself loads, not
from the editor's Layers. Measured 2026-10-23:

- A scene's shipping bake is `scenes/<scene>/<scene>.vscene_data/*.vzone`, beside the
  scene's own top-level `<scene>.vscene` (TheTunnels also carries a stale `Level2a`
  bake; `Master/` holds older ones). Every OMD2 and OMD1 scene has one.
- A `.vzone` is a VArchive object stream. A `VisStaticMeshInstance_cl` record is
  `01 00 00 00 00 02`, a u32 length and an ASCII `.vmesh` path, a u32 object id, a u32
  zone key, a row-major float 4x4 (the instance's world transform; its bottom row is
  exactly 0 0 0 1), then float4 bounds min, max and the zone origin. The other drawn
  classes are particle effects and lights; visibility zone proxies draw nothing.
- The zones hold the art meshes as placed AND `<scene>.vscene_data/SM_<zone>_<n>.vmesh`,
  meshes the exporter merged from small placements (floors, wall panels, designs): on
  TheCrunch 3,785 art placements of 73 meshes plus 162 merged meshes.
- The editor's Layers and prefabs disagree with the bake: TheCrunch's Layers and
  prefabs expand to 8,541 placements, of which 2,677 are drawn nowhere in the game
  (prefab contents such as CenterMainRoom's Ceiling4x4 x504, WallBase4_6 x228) and
  2,311 are inside the merged meshes, while 816 placements the game draws are
  missing from them (the arch pieces of CastleEntranceHall and Tower2x3).
- A `.vmesh`'s `SUBM` chunk gives each submesh's index range and material number,
  `MTRS` the materials in order (name, then texture paths: the diffuse map is the
  `_df`/`_da` one). A `<mesh>.vmesh_data/materials.xml` with `override="TRUE"`
  replaces a material's diffuse map by its name. Texture paths are from the data root
  (`\\art\\Act2\\floors\\MainFloor1_df.dds`) or from the mesh's folder
  (`\\Textures\\OpenArch1_da.dds`); a few name only a file found elsewhere.
- Vision is right-handed: the caller mirrors every placement through Y.
"""

import os
import posixpath
import re
import struct
import zipfile

#: A static mesh instance record's lead bytes (version, a zero u32, inline string).
MESH_SIG = b'\x01\x00\x00\x00\x00\x02'
#: The longest mesh path a record may carry.
MAX_PATH = 260
#: Diffuse texture suffixes (OMD2 uses both).
DIFFUSE = ('_df', '_da')
#: Texture suffixes that are never a base colour: specular, normal.
NOT_DIFFUSE = ('_sp', '_nm', '_n', '_s')


def key(name):
    return str(name).replace('\\', '/').lower()


class Files:
    """Every file in some zips, by lower-case forward-slash path; the first zip that
    holds a path wins."""

    def __init__(self, paths):
        self.zips = [zipfile.ZipFile(p) for p in paths]
        self.index = {}
        for i, zf in enumerate(self.zips):
            for n in zf.namelist():
                self.index.setdefault(key(n), (i, n))
        self._leaves = None

    def read(self, name):
        hit = self.index.get(key(name))
        if not hit:
            return None
        return self.zips[hit[0]].read(hit[1])

    def has(self, name):
        return key(name) in self.index

    def names(self):
        return [real for _i, real in self.index.values()]

    def by_leaf(self, leaf):
        """Every lower-case path whose file name is `leaf`."""
        if self._leaves is None:
            self._leaves = {}
            for k in self.index:
                self._leaves.setdefault(k.rsplit('/', 1)[-1], []).append(k)
        return self._leaves.get(leaf.lower(), [])


def zones(files, scene):
    """The scene's shipping `.vzone` paths (module docstring)."""
    prefix = 'scenes/%s/' % scene.lower()
    names = [k for k in files.index if k.startswith(prefix)]
    found = [k for k in names if k.endswith('.vzone')]
    tops = [k for k in names if k.endswith('.vscene') and k[len(prefix):].count('/') == 0]
    tops.sort(key=lambda k: (posixpath.basename(k)[:-len('.vscene')] != scene.lower(), k))
    for top in tops:
        want = top[:-len('.vscene')] + '.vscene_data/'
        got = sorted(k for k in found if k.startswith(want))
        if got:
            return got
    return sorted(k for k in found if '/master/' not in k)


def zone_instances(buf):
    """[(mesh path, 4x4 row-major numpy)] of one `.vzone`'s static mesh instances."""
    import numpy as np
    out = []
    for m in re.finditer(re.escape(MESH_SIG), buf):
        o = m.end()
        if o + 4 > len(buf):
            continue
        n, = struct.unpack_from('<I', buf, o)
        if not 0 < n < MAX_PATH or o + 4 + n + 8 + 64 > len(buf):
            continue
        raw = buf[o + 4:o + 4 + n]
        if not all(32 <= c < 127 for c in raw) or not raw.lower().endswith(b'.vmesh'):
            continue
        mat = np.frombuffer(buf, '<f4', 16, o + 4 + n + 8).astype(np.float64).reshape(4, 4)
        if not np.allclose(mat[3], (0, 0, 0, 1), atol=1e-5) or not np.isfinite(mat).all():
            continue
        out.append((key(raw.decode('ascii')), mat))
    return out


def instances(files, scene):
    """[(mesh key, 4x4)] the scene's shipping zones draw, in Vision's own world."""
    out = []
    for z in zones(files, scene):
        out.extend(zone_instances(files.read(z)))
    return out


def _diffuse_of(textures):
    dds = [t for t in textures if t.lower().endswith('.dds')]
    for t in dds:
        if posixpath.splitext(key(t).rsplit('/', 1)[-1])[0].endswith(DIFFUSE):
            return t
    for t in dds:
        if not posixpath.splitext(key(t).rsplit('/', 1)[-1])[0].endswith(NOT_DIFFUSE):
            return t
    return None


def texture_path(files, mesh, texture):
    """The lower-case path of the texture file `texture` (as a material names it) for
    the mesh at `mesh`, or None."""
    t = key(texture).lstrip('/')
    folder = key(mesh).rsplit('/', 1)[0]
    for cand in (t, posixpath.normpath(posixpath.join(folder, t))):
        if files.has(cand):
            return cand
    leaf = t.rsplit('/', 1)[-1]
    hits = files.by_leaf(leaf)
    if not hits:
        return None
    # the copy nearest the mesh: the longest shared folder prefix
    return max(hits, key=lambda h: (len(posixpath.commonprefix([h, folder])), h))


def _xml_diffuse(files, mesh):
    """{material name (lower case): diffuse file} from an overriding materials.xml."""
    base = key(mesh)
    if base.endswith('.vmesh'):
        base = base[:-len('.vmesh')]
    data = files.read(base + '.vmesh_data/materials.xml')
    if not data or not re.search(rb'override\s*=\s*"true"', data, re.I):
        return {}
    out = {}
    for m in re.finditer(rb'<Material\b[^>]*>', data):
        tag = m.group(0)
        name = re.search(rb'\bname="([^"]*)"', tag)
        diff = re.search(rb'\bdiffuse="([^"]*)"', tag)
        if name and diff and diff.group(1).strip():
            out[name.group(1).decode('latin1').lower()] = diff.group(1).decode('latin1')
    return out


def model(files, mesh):
    """{verts (N,3), uvs (N,2) or None, tris (M,3), sections [{first, tris, texture}]}
    for one `.vmesh`, or None. `first` and `tris` count triangles; `texture` is the
    lower-case path of the section's diffuse map, or None."""
    import numpy as np
    from riftlord.formats.omd2 import omd2model
    buf = files.read(mesh)
    if not buf or buf[:4] != b'VBIN':
        return None
    try:
        top = omd2model.chunk_map(buf)
        vm = omd2model.read_vmsh(buf, top['VMSH'])
    except (KeyError, ValueError, struct.error):
        return None
    s = vm['streams']
    verts = np.asarray(s['pos'], np.float64).reshape(-1, 3)
    idx = np.asarray(vm['indices'], np.int64)
    idx = idx[:len(idx) - len(idx) % 3]
    if len(verts) < 3 or not len(idx) or int(idx.max()) >= len(verts):
        return None
    tris = idx.reshape(-1, 3)
    uvs = s.get('tex0')
    uvs = np.asarray(uvs, np.float64).reshape(-1, 2) if uvs is not None and len(uvs) == len(verts) \
        else None
    try:
        subs = omd2model.read_submeshes(buf, top['SUBM']) if 'SUBM' in top else []
    except (ValueError, struct.error):
        subs = []
    try:
        mats = omd2model.read_materials(buf, top['MTRS']) if 'MTRS' in top else []
    except (ValueError, struct.error):
        mats = []
    override = _xml_diffuse(files, mesh)
    textures = []
    for m in mats:
        name = (m.get('name') or '').lower()
        want = override.get(name) or _diffuse_of(m.get('textures') or [])
        textures.append(texture_path(files, mesh, want) if want else None)
    sections = []
    for sub in subs:
        first, count = int(sub['first_index']), int(sub['index_count'])
        if count < 3 or first % 3 or first + count > len(idx):
            continue
        mi = int(sub['material'])
        sections.append({'first': first // 3, 'tris': count // 3,
                         'texture': textures[mi] if 0 <= mi < len(textures) else None})
    if not sections:
        sections = [{'first': 0, 'tris': len(tris), 'texture': textures[0] if textures else None}]
    return {'verts': verts, 'uvs': uvs, 'tris': tris, 'sections': sections}


def texture_png(files, path, png):
    """The texture file at `path` through `png(PIL image) -> bytes`, or None."""
    import io
    from PIL import Image
    data = files.read(path)
    if not data:
        return None
    with Image.open(io.BytesIO(data)) as im:
        return png(im)


def read(files, scene, mirror):
    """(models {key: model}, [(key, 4x4)]) of one scene; `mirror(m)` maps a Vision
    placement into Unreal handedness."""
    insts = [(k, mirror(m)) for k, m in instances(files, scene)]
    models = {}
    for k in sorted({k for k, _ in insts}):
        got = model(files, k)
        if got is not None:
            models[k] = got
    return models, insts


def editor_instances(files, scene, layers, prefab_library):
    """[(mesh key, 4x4)] the editor's Layers and prefabs place (for the checker)."""
    from riftlord.formats.omd2 import clr
    from riftlord.formats.omd2 import omd2prefab
    from riftlord.formats.omd2.nrbf import parse
    out = []
    for name in layers:
        try:
            graph = parse(files.read(name))
        except Exception:                            # noqa: BLE001
            continue
        for sh in graph.by_class('VisionEditorPlugin.Shapes.StaticMeshShape'):
            if sh.get('_bVisible') is False or not sh.get('_meshFile'):
                continue
            m = omd2prefab.compose(clr.vec3(sh.get('_position')) or (0.0, 0.0, 0.0),
                                   clr.vec3(sh.get('_orientation')) or (0.0, 0.0, 0.0),
                                   clr.vec3(sh.get('_scale')) or (1.0, 1.0, 1.0))
            out.append((key(sh.get('_meshFile')), m))
        for sh in graph.by_class('VisionEditorPlugin.Shapes.PrefabShape'):
            if sh.get('_bVisible') is False or not sh.get('_fileName'):
                continue
            at = omd2prefab.compose(clr.vec3(sh.get('_position')) or (0.0, 0.0, 0.0),
                                    clr.vec3(sh.get('_orientation')) or (0.0, 0.0, 0.0),
                                    clr.vec3(sh.get('_scale')) or (1.0, 1.0, 1.0))
            for part in prefab_library.contents(sh.get('_fileName')):
                out.append((key(part['mesh']), omd2prefab.matmul(at, part['matrix'])))
    return out


def layers(files, scene):
    """The scene's editor Layer files (real names)."""
    prefix = 'scenes/%s/' % scene.lower()
    return sorted(real for k, (_i, real) in files.index.items()
                  if k.startswith(prefix) and k.endswith('.layer'))
