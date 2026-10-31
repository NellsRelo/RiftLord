"""Serve OMD3's own art to a browser: mesh geometry, textures and previews.

Shared by the tools rather than written twice. The assembly tool needs it to show
the kit beside a converted OMD2 level; Rift Lord needs it to draw a shipped OMD3
level in detail. One implementation means one set of caches on disk, so a mesh read
for one tool is already read for the other.

    python3 omd3serve.py --mesh Floor_4x4
    python3 omd3serve.py --warm            # read every catalogued mesh once

## Why the caches matter

Reading one mesh means pulling its package out of a pak, walking the cooked
StaticMesh, and finding the vertex and index buffers at unaligned offsets. That is
tenths of a second each and a level is a hundred or more of them, so the result is
written to `cache/omd3-mesh` and a preview to `previews/omd3`. Held in memory alone
-- which is how this started -- every restart pays the whole cost again, and the
tool feels slow forever rather than once.
"""

import argparse
import functools
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.core import gamefiles  # noqa: E402
from riftlord.core import gamepath  # noqa: E402
from riftlord.formats.omd3 import meshcat  # noqa: E402
from riftlord.formats.omd3 import omd3mesh  # noqa: E402

MESH_CACHE = os.path.join(gamepath.scratch('cache', gamepath.CODE_ROOT), 'omd3-mesh')
#: Rendered previews: beside the code in a checkout; frozen, in the user's cache, where
#: they survive the bundle being unpacked fresh each launch.
PREVIEWS = os.path.join(gamepath.scratch('previews', gamepath.CODE_ROOT), 'omd3')

_THUMBS = {}
_PNG = {}


def safe_name(name):
    """A mesh or texture name as a file name. Asset names are tame; this is the belt."""
    return ''.join(c if (c.isalnum() or c in '._-') else '_' for c in str(name))


def keep(path, blob):
    """Write a cache file through a temporary name.

    A request interrupted part way through a direct write leaves a truncated file,
    and a truncated cache entry is then served in place of the real thing forever.
    """
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.part'
        mode = 'wb' if isinstance(blob, (bytes, bytearray)) else 'w'
        with open(tmp, mode) as fh:
            fh.write(blob)
        os.replace(tmp, path)
    except Exception:                                # noqa: BLE001
        pass


@functools.lru_cache(maxsize=1)
def models():
    """The monolithic geometry catalogue, if one has been built."""
    try:
        return omd3mesh.load()
    except Exception:                                # noqa: BLE001
        return {}


@functools.lru_cache(maxsize=1024)
def mesh(name, fresh=False):
    """One OMD3 mesh: triangles, UVs and its base-colour texture path.

    Three sources in order of cost: the monolithic catalogue if it exists, then a
    file per mesh written the first time that mesh was wanted, then the package
    itself. `fresh` skips both and re-reads: without it `--all` read nothing, because
    this returned the cached copy before the reader was ever called, so a reader fix
    could never reach a mesh that was already cached. The per-mesh files are what make a level usable without waiting on a
    full build, so the catalogue stays an optimisation rather than a requirement.
    """
    cached = os.path.join(MESH_CACHE, '%s.json' % safe_name(name))
    if not fresh:
        got = models().get(name)
        if got is not None:
            return got
        if os.path.exists(cached):
            try:
                with open(cached) as fh:
                    return json.load(fh)
            except Exception:                        # noqa: BLE001
                pass
    try:
        got = omd3mesh.read(name)
    except Exception:                                # noqa: BLE001
        got = None
    #: SKELETAL MESHES, after the static reader has had its go.
    #:
    #: A grinder pit, a cauldron trap and a falling boom barrel are `SkeletalMesh`
    #: assets, and the static reader cannot read one -- so the editor drew nothing
    #: where those traps stand. `omd3skm` reads the bind pose, which is what the
    #: position buffer already holds, and returns the same payload shape. Second,
    #: not first, because the static path is the common case and the catalogue
    #: lookup in front of it is cheaper.
    if got is None:
        try:
            from riftlord.formats.omd3 import omd3skm
            got = omd3skm.read(name)
        except Exception:                            # noqa: BLE001
            got = None
    if got is not None:
        keep(cached, json.dumps(got))
    return got


@functools.lru_cache(maxsize=512)
def material_texture(material_name):
    """The `/Game/...` path of the texture a material instance uses, or None.

Resolution goes through `omd3mesh.texture_of`, which reads
    `TextureParameterValues` first -- what the instance ASSIGNS, in layer order -- and
    only then scans imports. The earlier note here, that the parameter array is always
    empty, was wrong: it holds for instances that merely override colours, and not for
    layered or master-derived ones, where the import scan picks the wrong layer.

    The fallback below names textures as **imports**, with `cls == 'Texture2D'` and an
    `outer` pointing at the package they live in -- so `Arcane_Sky_INST1` gives
    `/Game/Environments/Sky/Mat/ArcaneSky2`.

    `DefaultTexture` is skipped; every material imports it.
    """
    import os as _os
    import tempfile
    from riftlord.formats.omd3 import omd3actors
    from riftlord.formats.ue.uheader import parse as parse_header
    path = omd3actors._asset_for(material_name)
    if not path:
        return None
    #: ONE rule for resolving a material's texture, in `omd3mesh.texture_of`: it reads
    #: `TextureParameterValues` -- what the instance actually assigns, in layer order
    #: -- before falling back to the import scan below. Without this a mesh painted by
    #: its actor got a different (and for layered terrain, a wrong) answer from the
    #: same material the mesh path resolves correctly.
    try:
        from riftlord.formats.omd3 import omd3mesh
        game = path.replace('../../../OMD/Content/', '/Game/')
        if not game.startswith('/Game/'):
            game = '/Game/' + game.split('OMD/Content/')[-1]
        got = omd3mesh.texture_of(game)
        if got:
            return got
    except Exception:                                # noqa: BLE001
        pass
    try:
        ua = gamefiles.read(path + '.uasset')
    except Exception:                                # noqa: BLE001
        return None
    try:
        root = tempfile.mkdtemp(prefix='omd3mat-')
        staged = _os.path.join(root, 'a.uasset')
        with open(staged, 'wb') as fh:
            fh.write(ua)
        info = parse_header(staged)
    except Exception:                                # noqa: BLE001
        return None
    imports = info.get('imports') or []
    for imp in imports:
        if not isinstance(imp, dict) or imp.get('cls') != 'Texture2D':
            continue
        name = imp.get('name')
        if not name or name == 'DefaultTexture':
            continue
        outer = imp.get('outer')
        if isinstance(outer, int) and outer < 0:
            i = -outer - 1
            if 0 <= i < len(imports) and isinstance(imports[i], dict):
                package = imports[i].get('name') or ''
                if package.startswith('/Game/'):
                    return package
        return name
    return None


def texture_png(game_path, size=256):
    """One OMD3 texture as PNG, downscaled to `size` on its longest side.

    256 is right for a preview on a wall and wrong for the sky: `ArcaneSky2` is
    natively **4096 x 4096**, and a 256-pixel copy stretched over a whole skybox is
    indistinguishable from a flat colour. The caller asks for what it needs and each
    size is cached separately.

    OMD3 ships DXT in a `.ubulk` sidecar with a mip chain; `texture.parse` walks it and
    `to_image` returns the top mip.
    """
    key = '%s@%d' % (game_path, size)
    if key in _PNG:
        return _PNG[key]
    cached = os.path.join(MESH_CACHE, 'tex', '%s.png' % safe_name(key))
    if os.path.exists(cached):
        try:
            with open(cached, 'rb') as fh:
                _PNG[key] = fh.read()
                return _PNG[key]
        except Exception:                            # noqa: BLE001
            pass
    png = None
    try:
        import io
        import tempfile
        from riftlord.formats.ue import texture
        # From the asset path, not the cache key: the key carries the size on the end.
        stem = str(game_path).replace('/Game/', '../../../OMD/Content/')
        staged = {}
        root = tempfile.mkdtemp(prefix='omd3tex-')
        for ext in ('.uasset', '.uexp', '.ubulk'):
            try:
                data = gamefiles.read(stem + ext)
            except Exception:                        # noqa: BLE001
                continue
            out = os.path.join(root, 'tex' + ext)
            with open(out, 'wb') as fh:
                fh.write(data)
            staged[ext] = out
        if '.uasset' in staged and '.uexp' in staged:
            info = texture.parse(staged['.uasset'], staged['.uexp'],
                                 staged.get('.ubulk'))
            image = texture.to_image(info)
            if image is not None:
                image = image.convert('RGB')
                if max(image.size) > size:
                    scale = size / max(image.size)
                    image = image.resize((max(1, int(image.width * scale)),
                                          max(1, int(image.height * scale))))
                buf = io.BytesIO()
                image.save(buf, format='PNG', optimize=True)
                png = buf.getvalue()
                keep(cached, png)
    except Exception:                                # noqa: BLE001
        png = None
    _PNG[key] = png
    return png


def preview_name(name):
    """The file name of one mesh's preview in PREVIEWS, carrying the drawing's version."""
    from riftlord.mapeditor import omd3thumb
    return '%s__%s.png' % (safe_name(name), omd3thumb.STYLE)


def thumbnail(name, size=96):
    """A small preview of one mesh as PNG, rendered on demand and kept on disk."""
    if name in _THUMBS:
        return _THUMBS[name]
    png = None
    try:
        import io
        from riftlord.mapeditor import omd3thumb
        cached = os.path.join(PREVIEWS, preview_name(name))
        if os.path.exists(cached):
            with open(cached, 'rb') as fh:
                png = fh.read()
        else:
            model = mesh(name)
            if model:
                image = omd3thumb.render(model, size)
                if image is not None:
                    buf = io.BytesIO()
                    image.save(buf, format='PNG')
                    png = buf.getvalue()
                    keep(cached, png)
    except Exception:                                # noqa: BLE001
        png = None
    _THUMBS[name] = png
    return png


def meshes(names, workers=8, fresh=False):
    """Read many meshes at once. {name: model}, missing ones left out.

    Reading one mesh means pulling its package out of a pak and walking the cooked
    StaticMesh, which is IO-bound and independent per mesh, so they are read in
    parallel. Serially, and one HTTP round trip at a time, a level of a hundred
    meshes took minutes; this is the same work without the waiting.
    """
    import threading
    names = list(dict.fromkeys(names))
    out = {}
    if not names:
        return out
    # daemon workers, not a ThreadPoolExecutor: the interpreter joins executor threads
    # at exit, so a server stopped mid-read would wait for every queued mesh
    queue, got, errors = iter(names), {}, []
    lock = threading.Lock()

    def work():
        while True:
            with lock:
                name = next(queue, None)
            if name is None or errors:
                return
            try:
                got[name] = mesh(name, fresh=fresh)
            except BaseException as exc:                 # noqa: BLE001 -- raised below
                errors.append(exc)
    threads = [threading.Thread(target=work, name='mesh-read-%d' % k, daemon=True)
               for k in range(max(1, min(workers, len(names))))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        raise errors[0]
    for name in names:
        if got.get(name) is not None:
            out[name] = got[name]
    return out


def warm(limit=None, workers=8, only_missing=True):
    """Read every catalogued mesh once, so the caches are there before they matter.

    Parallel and skipping what is already on disk. One at a time it managed 702 of
    1,166 in three hours and was cut off; the reads are independent and IO-bound, so
    there was never a reason to do them in series.
    """
    names = sorted(meshcat.load())
    if only_missing:
        names = [n for n in names
                 if not os.path.exists(
                     os.path.join(MESH_CACHE, '%s.json' % safe_name(n)))]
    if limit:
        names = names[:limit]
    got = meshes(names, workers=workers, fresh=not only_missing)
    return len(got), len(names) - len(got)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--mesh')
    ap.add_argument('--warm', action='store_true')
    ap.add_argument('--limit', type=int)
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--all', action='store_true',
                    help='re-read meshes already cached')
    args = ap.parse_args(argv)
    if args.warm:
        ok, bad = warm(args.limit, workers=args.workers, only_missing=not args.all)
        print('%d mesh(es) cached, %d could not be read' % (ok, bad))
        return 0
    if args.mesh:
        m = mesh(args.mesh)
        if not m:
            print('%s: could not read' % args.mesh)
            return 1
        print('%s: %d verts, %d tris, texture %s'
              % (args.mesh, len(m['verts']), len(m['tris']) // 3,
                 m.get('texture') or 'none'))
        return 0
    ap.error('need --mesh NAME or --warm')


if __name__ == '__main__':
    raise SystemExit(main())
