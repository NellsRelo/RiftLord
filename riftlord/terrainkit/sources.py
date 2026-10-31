"""The ground of the shipped maps, two kinds of source:

* `mesh`: placed static meshes drawn with a mesh-paint terrain material (paint.MASTER) and
  painted per placement (OverrideVertexColors, one colour per mesh vertex), wide enough for a
  tile;
* `landscape`: Landscape actors whose material's master is in paint.LANDSCAPE_LOOKS or
  mics.CARRIED (the Sand and Ice landscapes), read around the map's routes (landscape.route_box).

Survey 2026-10-28 (/mnt/2tb_pro_a/tmp/claude-scratch/terrain-kit/survey.md): 13 such placements
on six maps, every one painted with exactly the mesh's vertex count. Found here by those rules,
never by name, and cached per state of the game's paks.
"""

import hashlib
import json
import os
import struct

import numpy as np

#: A ground mesh is at least this wide on both horizontal axes (uu): one tile.
MIN_EXTENT = 1200.0
#: Where the per-install cache lives (under the user's cache folder).
CACHE_NAME = 'sources.json'


def cache_dir():
    base = os.environ.get('LOCALAPPDATA') if os.name == 'nt' else (
        os.environ.get('XDG_CACHE_HOME') or os.path.join(os.path.expanduser('~'), '.cache'))
    return os.path.join(base or os.path.expanduser('~'), 'rift-lord', 'terrainkit')


def paks_stamp():
    """A hash of the game's pak files (names, sizes, times) and of the code that picks sources."""
    from riftlord.core import gamepath
    root = gamepath.paks()
    root = root if isinstance(root, str) else root[0]
    h = hashlib.sha1()
    for name in sorted(os.listdir(root)):
        if name.endswith('.pak'):
            st = os.stat(os.path.join(root, name))
            h.update(('%s|%d|%d\n' % (name, st.st_size, int(st.st_mtime))).encode())
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ('sources.py', 'paint.py', 'landscape.py', 'mics.py'):
        with open(os.path.join(here, name), 'rb') as fh:
            h.update(fh.read())
    return h.hexdigest()


def maps():
    """{map: package path} of the shipped playable maps (no mods)."""
    from riftlord.build.navigation import navpatch
    return dict(navpatch.maps(include_mods=False))


def titles():
    """{map: mission title}."""
    try:
        from riftlord.build.minimap import mapdata
        return dict(mapdata.mission_names())
    except Exception:                                # noqa: BLE001
        return {}


def _tail(info, uexp, hdr, i):
    """(start, end) of export i's bytes after its tagged properties."""
    from riftlord.formats.ue.parseuasset import R
    e = info['exports'][i]
    start = e['off'] - hdr
    end = start + e['size']
    names = [n[0] if isinstance(n, (list, tuple)) else n for n in info['names']]
    r = R(uexp, start)
    while r.o < end:
        ni = r.i32(); r.i32()
        if names[ni] == 'None':
            break
        ti = r.i32(); r.i32()
        tp = names[ti]
        size = r.i32(); r.i32()
        if tp == 'BoolProperty':
            r.u8(); r.u8()
            continue
        if tp == 'StructProperty':
            r.i32(); r.i32(); r.raw(17)
        elif tp in ('ArrayProperty', 'ByteProperty', 'EnumProperty', 'SetProperty'):
            r.i32(); r.i32(); r.u8()
        elif tp == 'MapProperty':
            r.raw(17)
        else:
            r.u8()
        r.raw(size)
    return r.o, end


def override_colours(uexp, start, end):
    """RGBA uint8 (n, 4) of LOD 0's OverrideVertexColors in a StaticMeshComponent's tail
    (UObject int32, LODData count, FStaticMeshComponentLODInfo), or None."""
    d = uexp[start:end]
    if len(d) < 10:
        return None
    n = struct.unpack_from('<i', d, 4)[0]
    if n < 1:
        return None
    o = 8
    gflags, cflags = d[o], d[o + 1]
    o += 2
    if not gflags & 2:
        o += 16
    if cflags & 1 or o >= len(d) or not d[o]:
        return None
    o += 1 + 2
    _stride, nv = struct.unpack_from('<ii', d, o)
    o += 8
    if nv <= 0:
        return None
    esize, count = struct.unpack_from('<ii', d, o)
    o += 8
    if esize != 4 or count != nv or o + 4 * count > len(d):
        return None
    bgra = np.frombuffer(d[o:o + 4 * count], np.uint8).reshape(-1, 4)
    return bgra[:, [2, 1, 0, 3]].copy()


_MESH = {}


def mesh_info(name):
    """{path, vertices, material, size} of a static mesh by object name, or None."""
    if name in _MESH:
        return _MESH[name]
    from riftlord.core import gamefiles
    from riftlord.formats.omd3 import meshcat
    from riftlord.formats.omd3 import omd3smwrite as W
    got = None
    entry = meshcat.load().get(name)
    path = entry['path'].replace('../../../OMD/Content', '/Game') if entry else None
    if path is None:
        hits = [n for n in gamefiles.index() if n.endswith('/' + name + '.uasset')
                and n.startswith('../../../OMD/Content/')]
        path = hits[0].replace('../../../OMD/Content', '/Game')[:-len('.uasset')] if hits else None
    if path:
        try:
            t = W.Template(path)
            lod = t.lod0()
            mats = t.material_paths()
            got = {'path': path, 'vertices': int(lod['buffers']['pos_meta'][1]),
                   'material': mats[0][0] if mats else None}
        except (Exception, SystemExit):              # noqa: BLE001
            got = None
    _MESH[name] = got
    return got


def _material_path(info, name):
    """Package path of a material by object name: the level's import of it, else the one
    shipped package of that name (a Blueprint's own override is not imported by the level)."""
    for imp in info.get('imports') or []:
        if imp.get('name') == name and (imp.get('outer') or 0) < 0:
            return info['imports'][-imp['outer'] - 1].get('name')
    from riftlord.core import gamefiles
    hits = [n for n in gamefiles.index() if n.endswith('/' + name + '.uasset')
            and n.startswith('../../../OMD/Content/')]
    if len(hits) == 1:
        return hits[0].replace('../../../OMD/Content', '/Game')[:-len('.uasset')]
    return None


def discover_map(level):
    """[source] of one map: {map, package, component, mesh, meshPath, material, matrix (16,
    column-major), vertices}."""
    from riftlord.formats.omd3 import omd3level
    from riftlord.terrainkit import mics, paint
    out, seen = [], set()
    for pkg in omd3level.sublevels(level) + omd3level.extra_sublevels(level):
        try:
            pieces, _l, _v, _a = omd3level.read_package(pkg)
            info, uexp, hdr = omd3level._load(pkg)
        except (Exception, SystemExit):              # noqa: BLE001
            continue
        for p in pieces:
            if p.get('source') == 'landscape':
                material = _material_path(info, p['material']) if p.get('material') else None
                if material and (paint.master_of(material) in paint.LANDSCAPE_LOOKS
                                 or paint.master_of(material) in mics.CARRIED):
                    from riftlord.terrainkit import landscape
                    out.append({'kind': 'landscape', 'map': level, 'package': pkg,
                                'export': int(p['export']), 'mesh': 'Landscape',
                                'component': int(p['export']), 'material': material,
                                'matrix': [float(x) for x in p['matrix']],
                                'box': landscape.route_box(level)})
                continue
            if p.get('hidden') or p.get('source') not in ('component', 'blueprint'):
                continue
            comp = p.get('component') or (p['export'] if p.get('source') == 'component' else None)
            if not comp or not 0 < comp <= len(info['exports']):
                continue
            e = info['exports'][comp - 1]
            cls = str(e.get('class') or e.get('cls') or '')
            if cls != 'StaticMeshComponent':
                continue
            m = mesh_info(p['mesh'])
            if not m:
                continue
            material = _material_path(info, p['material']) if p.get('material') else m['material']
            if not material or paint.master_of(material) not in paint.BLENDS:
                continue
            start, end = _tail(info, uexp, hdr, comp - 1)
            cols = override_colours(uexp, start, end)
            if cols is None or len(cols) != m['vertices']:
                continue
            key = (p['mesh'], material, hashlib.sha1(cols.tobytes()).hexdigest())
            if key in seen:
                continue
            seen.add(key)
            mt = np.array(p['matrix'], np.float64).reshape(4, 4).T
            lo, hi = mesh_bounds(m['path'])
            corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1])
                                for z in (lo[2], hi[2])]) @ mt[:3, :3].T
            span = corners.max(0) - corners.min(0)
            if span[0] < MIN_EXTENT or span[1] < MIN_EXTENT:
                continue
            out.append({'kind': 'mesh', 'map': level, 'package': pkg, 'component': int(comp),
                        'mesh': p['mesh'],
                        'meshPath': m['path'], 'material': material,
                        'matrix': [float(x) for x in p['matrix']], 'vertices': m['vertices']})
    return out


def mesh_bounds(path):
    from riftlord.formats.omd3 import omd3smwrite as W
    P = W.decode(W.Template(path))['positions']
    return P.min(0), P.max(0)


def discover(events=None, refresh=False):
    """{map: [source]} for every shipped map, cached per paks_stamp()."""
    os.makedirs(cache_dir(), exist_ok=True)
    path = os.path.join(cache_dir(), CACHE_NAME)
    stamp = paks_stamp()
    if not refresh and os.path.exists(path):
        try:
            with open(path) as fh:
                got = json.load(fh)
            if got.get('stamp') == stamp:
                return got['maps']
        except (OSError, ValueError):
            pass
    found = {}
    names = sorted(maps())
    for k, level in enumerate(names):
        if events:
            events(k, len(names), level)
        found[level] = discover_map(level)
    if events:
        events(len(names), len(names), '')
    tmp = path + '.part'
    with open(tmp, 'w') as fh:
        json.dump({'stamp': stamp, 'maps': found}, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)
    return found


def geometry(source):
    """(positions (V, 3) world, triangles (T, 3), colours (V, 4) RGBA, uv0 (V, 2)) of a source."""
    from riftlord.formats.omd3 import omd3level
    from riftlord.formats.omd3 import omd3smwrite as W
    d = W.decode(W.Template(source['meshPath']))
    info, uexp, hdr = omd3level._load(source['package'])
    start, end = _tail(info, uexp, hdr, source['component'] - 1)
    cols = override_colours(uexp, start, end)
    mt = np.array(source['matrix'], np.float64).reshape(4, 4).T
    P = d['positions'].astype(np.float64) @ mt[:3, :3].T + mt[:3, 3]
    return P, np.asarray(d['triangles'], np.int64), cols, np.asarray(d['uvs'][0], np.float64)


def fingerprint_parts(source):
    """What shapes a source's tiles: its mesh package bytes, its paint, placement and material;
    a landscape's: its level package's bytes, placement, material and route box."""
    from riftlord.core import gamefiles
    h = hashlib.sha256()
    if source.get('kind') == 'landscape':
        stem = source['package'].replace('/Game/', '../../../OMD/Content/')
        for ext in ('.umap', '.uexp', '.ubulk'):
            try:
                h.update(gamefiles.read(stem + ext))
            except KeyError:
                pass
        h.update(json.dumps([source['matrix'], source['material'], source.get('box')]).encode())
        return h.hexdigest()
    stem = source['meshPath'].replace('/Game/', '../../../OMD/Content/')
    for ext in ('.uasset', '.uexp'):
        h.update(gamefiles.read(stem + ext))
    _P, _T, cols, _uv = geometry(source)
    h.update(cols.tobytes())
    h.update(json.dumps([source['matrix'], source['material']]).encode())
    return h.hexdigest()
