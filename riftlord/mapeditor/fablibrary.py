"""Read installed Fab art libraries (thirdparty/LIBRARY-FORMAT.md) for the Map Editor and the
emitter: the pack list, each pack's pieces, their meshes and textures for the 3D view, and the
package closure a mission pak copies.

A library is `<root>/<PackId>/` with `catalogue.json`, `<PackId>.pak`, `thumbs/` and
`report.json`, written by fabconvert.py. Libraries are only read here; a library that is
missing or being rebuilt is reported, never repaired.
"""

import functools
import hashlib
import io
import json
import os
import re
import struct
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

from riftlord.core import gamepath  # noqa: E402

#: catalogue.json `format` and the `format_version` this reader understands.
FORMAT = 'rift-lord-fab-library'
FORMAT_VERSION = 1
#: Every library package lives under this game path (pak entries `OMD/Content/Mods/Fab/`).
GAME_ROOT = '/Game/Mods/Fab/'
ENTRY_ROOT = 'OMD/Content/Mods/Fab/'
#: Package file extensions copied with a package.
EXTS = ('.uasset', '.uexp', '.ubulk')

#: Roles an author may rescale per placement, or None for every role. Was props only
#: (architecture kept its pack scale); the user asked to resize any piece (2026-10-19).
#: A rescaled piece still leaves out its suggested trap grids (fabemit).
SCALABLE_ROLES = None
#: Browser kinds -> catalogue roles. `opening` is any piece with a clear doorway.
KIND_ROLES = {
    'architecture': ('floor', 'wall', 'column', 'ceiling', 'arch', 'door', 'cliff'),
    'prop': ('prop', 'rock'),
    'stairs': ('stairs',),
    'roof': ('roof',),
    'foliage': ('foliage',),
}
KINDS = ('architecture', 'prop', 'opening', 'stairs', 'roof', 'foliage', 'terrain')
#: Catalogue pack.kind whose pieces are listed under the browser kind of the same name only.
OWN_KIND_PACKS = ('terrain',)
#: Catalogue grid kind -> the trap-grid layer's surface name.
SURFACE = {'floor': 'Floor', 'slope_floor': 'Floor', 'wall': 'Wall', 'slope_wall': 'Wall',
           'ceiling': 'Ceiling', 'slope_ceiling': 'Ceiling'}
#: Grid kinds a role never takes (user decisions; the catalogue already applies them).
REFUSED_GRID_KINDS = {'stairs': ('floor', 'slope_floor'), 'door': ('floor', 'slope_floor',
                      'wall', 'slope_wall', 'ceiling', 'slope_ceiling'), 'foliage': ('floor', 'slope_floor',
                      'wall', 'slope_wall', 'ceiling', 'slope_ceiling')}
#: A roof grid's cell counts must be whole within this.
WHOLE_CELL_TOLERANCE = 1e-3
#: Texture previews: the largest edge served, and the default.
TEXTURE_MAX = 2048
TEXTURE_DEFAULT = 512
#: Largest edge of a library's previews/<leaf>.png (LIBRARY-FORMAT.md); asked at this size or
#: smaller, a texture is served from its preview instead of being decoded.
PREVIEW_SIZE = 256

_LOCK = threading.Lock()
_CATALOGUES = {}
_PAKS = {}


def root():
    """The library folder (LIBRARY-FORMAT.md "Where it lives")."""
    return os.environ.get('RIFTLORD_FAB_LIBRARY') or gamepath.user_data('FabLibrary')


def terrain_root():
    """The terrain kits' folder: libraries Setup builds from the game's own ground
    (riftlord.terrainkit), kept apart from the Fab ones."""
    return os.environ.get('RIFTLORD_TERRAIN_LIBRARY') or gamepath.user_data('TerrainKits')


def roots():
    return [root(), terrain_root()]


def pack_dir(pack):
    """The folder of a library, in whichever root holds it (the Fab root when none does)."""
    pack = _safe_pack(pack)
    for base in roots():
        if os.path.isdir(os.path.join(base, pack)):
            return os.path.join(base, pack)
    return os.path.join(root(), pack)


def _safe_pack(pack):
    pack = str(pack or '')
    if not pack or not all(c.isalnum() or c == '_' for c in pack):
        raise SystemExit('no such Fab library: %r' % pack)
    return pack


def _stat(path):
    st = os.stat(path)
    return (st.st_size, st.st_mtime_ns)


def catalogue(pack):
    """The pack's catalogue.json, re-read when the file changes. SystemExit when absent."""
    pack = _safe_pack(pack)
    path = os.path.join(pack_dir(pack), 'catalogue.json')
    try:
        stamp = _stat(path)
    except OSError:
        raise SystemExit('the Fab library %s is not installed (no %s)' % (pack, path))
    with _LOCK:
        hit = _CATALOGUES.get(pack)
        if hit and hit[0] == stamp:
            return hit[1]
    try:
        with open(path, 'rb') as fh:
            raw = fh.read()
        cat = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise SystemExit('the Fab library %s catalogue does not read (being rebuilt?): %s'
                         % (pack, exc))
    if cat.get('format') != FORMAT or cat.get('format_version') != FORMAT_VERSION:
        raise SystemExit('the Fab library %s is format %r v%r; Rift Lord reads %s v%d'
                         % (pack, cat.get('format'), cat.get('format_version'), FORMAT,
                            FORMAT_VERSION))
    cat['_by_object'] = {m['object_path']: m for m in cat.get('meshes') or []
                         if m.get('object_path')}
    cat['_by_package'] = {m['package']: m for m in cat.get('meshes') or [] if m.get('package')}
    cat['_by_source'] = {m['source']: m for m in cat.get('meshes') or [] if m.get('source')}
    cat['_by_rel'] = {build_free(m['object_path'], pack): m for m in cat.get('meshes') or []
                      if m.get('object_path')}
    cat['_sha1'] = hashlib.sha1(raw).hexdigest()
    with _LOCK:
        _CATALOGUES[pack] = (stamp, cat)
    return cat


def pak_path(pack):
    cat = catalogue(pack)
    return os.path.join(pack_dir(pack), cat.get('pak') or '%s.pak' % pack)


def stamp(pack):
    """(catalogue sha1, pak size, pak mtime): changes whenever the library is rebuilt."""
    cat = catalogue(pack)
    path = pak_path(pack)
    try:
        size, mtime = _stat(path)
    except OSError:
        raise SystemExit('the Fab library %s names %s, which is missing (being rebuilt?)'
                         % (pack, os.path.basename(path)))
    return (cat['_sha1'], size, mtime)


def open_pak(pack):
    """The library pak (pak.Pak), reopened when the file changes."""
    from riftlord.formats.ue import pak as _pak
    path = pak_path(pack)
    try:
        st = _stat(path)
    except OSError:
        raise SystemExit('the Fab library %s names %s, which is missing (being rebuilt?)'
                         % (pack, os.path.basename(path)))
    with _LOCK:
        hit = _PAKS.get(pack)
        if hit and hit[0] == st:
            return hit[1], hit[2]
    try:
        src = _pak.Pak(path)
    except Exception as exc:                         # noqa: BLE001
        raise SystemExit('the Fab library %s pak does not read (being rebuilt?): %s'
                         % (pack, exc))
    entries = {}
    for key in src.files:
        if ENTRY_ROOT in key:
            entries[key[key.index('OMD/'):]] = key
    with _LOCK:
        _PAKS[pack] = (st, src, entries)
    return src, entries


def forget(pack, cache=False):
    """Drop what this process holds of one library: its open pak, its catalogue and the
    meshes read from it; with `cache`, its preview cache folder too (the library is going)."""
    pack = _safe_pack(pack)
    with _LOCK:
        _CATALOGUES.pop(pack, None)
        held = _PAKS.pop(pack, None)
        if held is not None:
            try:
                held[1].f.close()
            except (AttributeError, OSError):
                pass
    _mesh_cached.cache_clear()
    if cache:
        import shutil
        shutil.rmtree(os.path.join(gamepath.scratch('fabview'), pack), ignore_errors=True)


def entry_stem(game_path):
    """`/Game/Mods/Fab/X/a/b` -> `OMD/Content/Mods/Fab/X/a/b`."""
    if not str(game_path).startswith(GAME_ROOT):
        raise SystemExit('%s is not a Fab library package' % game_path)
    return 'OMD/Content' + game_path[len('/Game'):]


def pack_of(game_path):
    """The PackId a `/Game/Mods/Fab/<PackId>/...` path belongs to."""
    rest = str(game_path)[len(GAME_ROOT):] if str(game_path).startswith(GAME_ROOT) else ''
    return rest.split('/', 1)[0] if '/' in rest else None


def read_package(pack, game_path):
    """{ext: bytes} of one library package. SystemExit when the pak lacks it."""
    src, entries = open_pak(pack)
    stem = entry_stem(game_path)
    if stem + '.uasset' not in entries:
        raise SystemExit('the Fab library %s has no %s (being rebuilt?)' % (pack, game_path))
    out = {}
    with _LOCK:
        for ext in EXTS:
            if stem + ext in entries:
                out[ext] = src.read_file(entries[stem + ext])
    return out


# -- what the browser shows --------------------------------------------------------------------

def libraries():
    """[{id, title, scale, meshes, grids, ogrePassable, status, detail}] for every folder in
    the Fab library root, then every finished terrain kit (one not built yet is not listed);
    status is `ready`, or why the library cannot be used now."""
    out = []
    folders = []
    for base in roots():
        terrain = base == terrain_root() and base != root()
        for pack in sorted(os.listdir(base)) if os.path.isdir(base) else []:
            if not os.path.isdir(os.path.join(base, pack)):
                continue
            if terrain and not os.path.isfile(os.path.join(base, pack, 'report.json')):
                continue
            folders.append(pack)
    for pack in folders:
        row = {'id': pack, 'title': pack, 'scale': None, 'meshes': 0, 'grids': 0,
               'ogrePassable': 0, 'status': 'ready', 'detail': ''}
        try:
            cat = catalogue(pack)
            info = cat.get('pack') or {}
            row.update(title=info.get('title') or pack, scale=info.get('scale'))
            conv = [m for m in cat.get('meshes') or [] if m.get('status') == 'converted']
            row['meshes'] = len(conv)
            row['grids'] = sum(len(m.get('grids') or []) for m in conv)
            row['ogrePassable'] = sum(1 for m in conv
                                      if (m.get('opening') or {}).get('ogre_passable'))
            stamp(pack)
        except SystemExit as exc:
            row['status'], row['detail'] = 'unavailable', str(exc)
        out.append(row)
    return out


def kinds_of(entry, pack_kind=None):
    """The browser kinds one mesh entry is listed under (a terrain kit's: terrain)."""
    if pack_kind in OWN_KIND_PACKS:
        return [pack_kind]
    role = entry.get('role')
    out = [k for k, roles in KIND_ROLES.items() if role in roles]
    if (entry.get('opening') or {}).get('has_opening'):
        out.append('opening')
    return out


def scalable(entry):
    return SCALABLE_ROLES is None or (entry or {}).get('role') in SCALABLE_ROLES


def collides(entry):
    """False for a piece its catalogue marks `collision.collides` false (a sky, clouds):
    placed with collision off, and out of the walkable graph and the collision check."""
    return ((entry or {}).get('collision') or {}).get('collides') is not False


def grid_allowed(entry, grid):
    """Whether a suggested grid may be emitted for this piece (role rules, LIBRARY-FORMAT)."""
    role = (entry or {}).get('role')
    if grid.get('kind') in REFUSED_GRID_KINDS.get(role, ()):
        return False
    if role == 'roof':
        cells = grid.get('cells') or [0, 0]
        return all(abs(c - round(c)) < WHOLE_CELL_TOLERANCE and round(c) >= 1 for c in cells)
    return True


def ui_grid(grid):
    """A catalogue grid in the trap-grid layer's shape (trapgrids.js), mesh-local."""
    ax = grid['axes']
    axes = [ax['x'], ax['y'], ax['z']]
    ext = grid['extent']
    c = grid['center']
    corners = []
    for sx in (-1, 1):
        for sy in (-1, 1):
            for sz in (-1, 1):
                corners.append([c[k] + sx * ext[0] * axes[0][k] + sy * ext[1] * axes[1][k]
                                + sz * ext[2] * axes[2][k] for k in range(3)])
    return {'id': grid['id'], 'kind': grid['kind'], 'surface': SURFACE.get(grid['kind'], 'Floor'),
            'origin': list(c), 'axes': axes, 'extent': list(ext), 'normal': list(axes[0]),
            'min': [min(p[k] for p in corners) for k in range(3)],
            'max': [max(p[k] for p in corners) for k in range(3)],
            'cells': grid.get('cells'), 'bits': grid.get('bits'),
            'warmachine': grid.get('warmachine'), 'tilt': grid.get('tilt_deg')}


def piece_view(pack, entry, pack_kind=None):
    """One mesh entry as the browser and the 3D view use it."""
    grids = [ui_grid(g) for g in entry.get('grids') or [] if grid_allowed(entry, g)]
    return {'pack': pack, 'name': entry['name'], 'display': entry.get('display_name') or entry['name'],
            'role': entry.get('role'), 'kinds': kinds_of(entry, pack_kind), 'object': entry['object_path'],
            'source': entry.get('source'),
            'package': entry['package'], 'mesh': mesh_key(entry['package']),
            'bounds': entry.get('bounds'), 'pivot': entry.get('pivot'),
            'thumb': entry.get('thumbnail'), 'scalable': scalable(entry), 'grids': grids,
            'opening': entry.get('opening'), 'stairs': entry.get('stairs'),
            'warnings': entry.get('warnings') or [], 'grade': entry.get('grade'),
            'triangles': entry.get('triangles')}


def pieces(pack):
    """{pack, title, scale, pieces: [...]} for the browser; converted meshes only."""
    cat = catalogue(pack)
    info = cat.get('pack') or {}
    return {'pack': pack, 'title': info.get('title') or pack, 'scale': info.get('scale'),
            'kinds': list(KINDS),
            'pieces': [piece_view(pack, m, info.get('kind')) for m in cat.get('meshes') or []
                       if m.get('status') == 'converted' and m.get('object_path')]}


def mesh_key(package):
    """The `/api/mesh/` name of a library mesh: `fab/<PackId>/<path under the pack>`."""
    return 'fab/' + package[len(GAME_ROOT):]


def build_free(path, pack):
    """A library object or package path without its build folder: `/Game/Mods/Fab/<pack>/
    B<id>/meshes/x.x` -> `meshes/x.x` (a library rebuilt under a new build id keeps it)."""
    head = GAME_ROOT + pack + '/'
    rest = str(path or '')[len(head):] if str(path or '').startswith(head) else ''
    first, sep, tail = rest.partition('/')
    if sep and re.fullmatch(r'B[0-9a-fA-F]+', first):
        return tail
    return rest


def entry_for(pack, object_path, source=None):
    """The catalogue entry for a placed piece: by its pack `source` path, else by
    `object_path` in the current build, else by that path without its build folder (a
    piece saved against an earlier build of the library); None if the current build has
    none of them."""
    cat = catalogue(pack)
    return ((source and cat['_by_source'].get(source)) or cat['_by_object'].get(object_path)
            or cat['_by_rel'].get(build_free(object_path, pack)))


def current_piece(entry):
    """A plan's Fab piece moved to its library's current build: `object`, `name` (the mesh
    key) and `source` as the catalogue has them now, and `mesh` (its build-free path).
    When the current build no longer has it, `missing` names why; the piece is kept."""
    out = dict(entry)
    pack, obj = out.get('pack'), out.get('object')
    out.pop('missing', None)
    try:
        found = entry_for(pack, obj, out.get('source'))
    except SystemExit as exc:
        out['missing'] = str(exc)
        return out
    if not found or found.get('status') != 'converted':
        out['missing'] = 'the Fab library %s no longer has %s' % (pack, build_free(obj, pack) or obj)
        return out
    out.update(object=found['object_path'], name=mesh_key(found['package']),
               source=found.get('source') or out.get('source') or '',
               mesh=build_free(found['object_path'], pack))
    return out


def current_plan(plan):
    """A copy of `plan` with every Fab piece moved to its library's current build
    (current_piece); other pieces as they are. Records keep reading after a rebuild."""
    if not isinstance(plan, dict) or not any(isinstance(e, dict) and e.get('kind') == 'fab'
                                             for e in plan.get('added') or []):
        return plan
    out = dict(plan)
    out['added'] = [current_piece(e) if isinstance(e, dict) and e.get('kind') == 'fab' else e
                    for e in plan['added']]
    return out


def thumbnail(pack, rel):
    """Bytes of `thumbs/<name>.png`, or None."""
    pack = _safe_pack(pack)
    rel = str(rel).replace('\\', '/')
    if '..' in rel.split('/') or not rel.startswith('thumbs/') or not rel.endswith('.png'):
        return None
    path = os.path.join(pack_dir(pack), *rel.split('/'))
    try:
        with open(path, 'rb') as fh:
            return fh.read()
    except OSError:
        return None


# -- meshes and textures for the 3D view -------------------------------------------------------

def _cache_dir(pack):
    return os.path.join(gamepath.scratch('fabview'), _safe_pack(pack))


def _bc_texture(pack, cat, material):
    """The base-colour texture package of one library material, or None."""
    for m in (cat.get('materials') or {}).values():
        if m.get('package') == material:
            got = (m.get('textures') or {}).get('BC')
            if got:
                return got
    try:
        from riftlord.formats.ue.uassetio import Package
        blobs = read_package(pack, material)
        pkg = Package(blobs['.uasset'])
        names = [n for n, _h in pkg.names]
        for rec in pkg.imports:
            _cp, _n1, cls, _n2, outer, obj, _n3 = struct.unpack('<7i', rec)
            if outer == 0 and names[cls] == 'Package' and names[obj].endswith('_BC'):
                return names[obj]
    except (Exception, SystemExit):                  # noqa: BLE001
        return None
    return None


def _texture_url(path):
    """A texture path as `/api/texture` takes it: Fab textures under `/fab/<PackId>/...`."""
    if not path:
        return None
    if path.startswith(GAME_ROOT):
        return '/fab/' + path[len(GAME_ROOT):]
    return path


@functools.lru_cache(maxsize=256)
def _mesh_cached(pack, rel, key):
    import numpy as np
    from riftlord.formats.omd3 import omd3smwrite
    package = GAME_ROOT + pack + '/' + rel
    blobs = read_package(pack, package)
    t = omd3smwrite.Template(path=package, blobs={'.uasset': blobs['.uasset'],
                                                  '.uexp': blobs['.uexp']})
    d = omd3smwrite.decode(t)
    lod = t.lod0()
    cat = catalogue(pack)
    slots = [p for p, _o, _s, _c in t.material_paths()]
    tex = [_texture_url(_bc_texture(pack, cat, p)) if p else None for p in slots]
    secs = [{'slot': int(s[0]), 'first': int(s[1]), 'tris': int(s[2])} for s in lod['sections']]
    uv = d['uvs'][0] if d['uvs'] else None
    return {'fab': True, 'pack': pack, 'package': package,
            'verts': np.round(d['positions'].astype(np.float64), 3).tolist(),
            'tris': d['triangles'].reshape(-1).astype(int).tolist(),
            'uvs': np.round(uv.astype(np.float64), 5).tolist() if uv is not None else None,
            'sections': secs, 'slots': slots, 'slotTextures': tex,
            'texture': next((x for x in tex if x), None),
            'triangles': int(len(d['triangles'])), 'vertices': int(len(d['positions']))}


def mesh(key):
    """`/api/mesh/fab/<PackId>/<path>`: geometry, UV0, sections and per-slot base colours."""
    rest = str(key)[len('fab/'):] if str(key).startswith('fab/') else ''
    pack, _sep, rel = rest.partition('/')
    if not rel:
        return None
    try:
        return _mesh_cached(_safe_pack(pack), rel, stamp(pack))
    except SystemExit:
        pass
    try:                                     # a key from an earlier build of the library
        found = catalogue(pack)['_by_rel'].get(build_free(GAME_ROOT + rest, pack) + '.' +
                                               rel.rsplit('/', 1)[-1])
        if found and found.get('package'):
            return _mesh_cached(_safe_pack(pack), found['package'][len(GAME_ROOT) + len(pack) + 1:],
                                stamp(pack))
    except SystemExit:
        pass
    return None


def _preview_png(pack, rel, size):
    """The library's own preview of a texture as PNG bytes, shrunk to `size`, or None."""
    try:
        cat = catalogue(pack)
    except SystemExit:
        return None
    leaf = (cat.get('texture_previews') or {}).get(GAME_ROOT + pack + '/' + rel)
    if not leaf:
        return None
    base = pack_dir(pack)
    path = os.path.normpath(os.path.join(base, leaf))
    if not path.startswith(base + os.sep):
        return None
    try:
        with open(path, 'rb') as fh:
            png = fh.read()
    except OSError:
        return None
    if size >= PREVIEW_SIZE:
        return png
    from PIL import Image
    image = Image.open(io.BytesIO(png)).convert('RGB')
    if max(image.size) > size:
        k = size / max(image.size)
        image = image.resize((max(1, int(image.width * k)), max(1, int(image.height * k))))
    buf = io.BytesIO()
    image.save(buf, format='PNG')
    return buf.getvalue()


def texture_png(path, size=TEXTURE_DEFAULT):
    """`/api/texture/fab/<PackId>/<path>` as PNG (largest edge `size`), cached per library
    build. None when the texture is not in the library."""
    rest = str(path).lstrip('/')
    if not rest.startswith('fab/'):
        return None
    pack, _sep, rel = rest[len('fab/'):].partition('/')
    size = max(16, min(TEXTURE_MAX, int(size)))
    try:
        st = stamp(pack)
    except SystemExit:
        return None
    if size <= PREVIEW_SIZE:
        got = _preview_png(pack, rel, size)
        if got:
            return got
    tag = hashlib.sha1(repr((rel, size, st)).encode()).hexdigest()[:20]
    cached = os.path.join(_cache_dir(pack), 'tex', tag + '.png')
    try:
        with open(cached, 'rb') as fh:
            return fh.read()
    except OSError:
        pass
    try:
        from riftlord.formats.ue import texture
        blobs = read_package(pack, GAME_ROOT + pack + '/' + rel)
        with tempfile.TemporaryDirectory(prefix='fabtex-') as tmp:
            staged = {}
            for ext, data in blobs.items():
                staged[ext] = os.path.join(tmp, 't' + ext)
                with open(staged[ext], 'wb') as fh:
                    fh.write(data)
            image = texture.to_image(texture.parse(staged['.uasset'], staged['.uexp'],
                                                   staged.get('.ubulk')))
        if image is None:
            return None
        image = image.convert('RGB')
        if max(image.size) > size:
            k = size / max(image.size)
            image = image.resize((max(1, int(image.width * k)), max(1, int(image.height * k))))
        buf = io.BytesIO()
        image.save(buf, format='PNG')
        png = buf.getvalue()
    except (Exception, SystemExit):                  # noqa: BLE001
        return None
    try:
        os.makedirs(os.path.dirname(cached), exist_ok=True)
        with open(cached + '.part', 'wb') as fh:
            fh.write(png)
        os.replace(cached + '.part', cached)
    except OSError:
        pass
    return png


# -- the closure a mission pak copies ---------------------------------------------------------

def package_imports(uasset):
    """`/Game/...` packages one package imports."""
    from riftlord.formats.ue.uassetio import Package
    pkg = Package(uasset)
    names = [n for n, _h in pkg.names]
    out = []
    for rec in pkg.imports:
        _cp, _n1, cls, _n2, outer, obj, num = struct.unpack('<7i', rec)
        if outer == 0 and names[cls] == 'Package':
            out.append(names[obj] if num == 0 else '%s_%d' % (names[obj], num - 1))
    return out


def closure(pack, packages):
    """({pak entry name: bytes}, [game paths]) of every library package `packages` need,
    following imports of `/Game/Mods/Fab/<pack>/` packages transitively. A package
    importing another Fab pack is refused (libraries are self-contained)."""
    want, seen, files = list(packages), set(), {}
    while want:
        gp = want.pop()
        if gp in seen:
            continue
        seen.add(gp)
        for ext, data in read_package(pack, gp).items():
            files[entry_stem(gp) + ext] = data
        for dep in package_imports(files[entry_stem(gp) + '.uasset']):
            if dep.startswith(GAME_ROOT):
                if pack_of(dep) != pack:
                    raise SystemExit('%s imports %s from another Fab library' % (gp, dep))
                want.append(dep)
    return files, sorted(seen)


def export_name(pack, package):
    """(string, number) FName of the StaticMesh export of one library mesh package."""
    from riftlord.formats.ue.uassetio import Package
    pkg = Package(read_package(pack, package)['.uasset'])
    names = [n for n, _h in pkg.names]
    for e in pkg.exports:
        ci = struct.unpack_from('<i', e['meta'], 0)[0]
        if ci < 0 and names[struct.unpack('<7i', pkg.imports[-ci - 1])[5]] == 'StaticMesh':
            return (names[struct.unpack_from('<i', e['meta'], 16)[0]],
                    struct.unpack_from('<i', e['meta'], 20)[0])
    raise SystemExit('%s holds no StaticMesh export' % package)
