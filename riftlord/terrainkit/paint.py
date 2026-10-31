"""The layers of a mesh-paint terrain material and the per-vertex layer weights its vertex colours
give.

Master_Enviro_MeshPaint_Terrain's base pass (its cooked FNoLightMapPolicy pixel shader, read with
OMD2-Converter/thirdparty/material-tools/dxbc, 2026-10-28):

    c = BC_1
    c = lerp(c, BC_2 * BC_2_Color, R)
    c = lerp(c, BC_3 * BC_3_Color, G)
    c = lerp(c, BC_4 * BC_4_Color, B)
    c = lerp(c, BC_1 * BC_1_Color, A)

(the vertex shader fetches the colour buffer with UE's bgra swizzle, so R G B A are the vertex
colour's own channels). Every BC parameter of the master is named "BC": a slot is found through
the texture the master uses for it by default (the shader's TextureIndex into the master's
ReferencedTextures) and that parameter's ExpressionGUID, which the instance repeats on its value.
"""

import functools
import os
import re
import struct
import tempfile

import numpy as np

#: The terrain master this module reads.
MASTER = '/Game/MasterMaterials/Enviro/Terrain/Master_Enviro_MeshPaint_Terrain'
#: Per master: the successive layers as (BC slot, vertex-colour channel or None for the base,
#: tint vector or None).
BLENDS = {MASTER: (('BC_1', None, None), ('BC_2', 0, 'BC_2_Color'), ('BC_3', 1, 'BC_3_Color'),
                   ('BC_4', 2, 'BC_4_Color'), ('BC_1', 3, 'BC_1_Color'))}
#: Per master: each BC slot's default texture in the master (shader TextureIndex 1, 0, 3, 2 into
#: ReferencedTextures).
SLOT_DEFAULTS = {MASTER: {'BC_1': 'Test1_BC', 'BC_2': 'Test2_BC', 'BC_3': 'Test3_BC',
                          'BC_4': 'Test4_BC'}}
#: Scalar parameters scaling UV0 per layer.
TILE_SIZE_PARAMS = ('TileSize_U', 'TileSize_V')
#: Parent links followed before giving up.
MAX_PARENTS = 8


def _imp_name(pkg, names, index):
    r = struct.unpack('<7i', pkg.imports[-index - 1])
    return names[r[5]]


@functools.lru_cache(maxsize=256)
def chain(path):
    """[(path, names, pkg, props)] from a material instance up to its master."""
    from riftlord.formats.omd3 import omd3matlook as ML
    out, seen = [], set()
    while path and path not in seen and len(out) < MAX_PARENTS:
        seen.add(path)
        try:
            names, pkg, props = ML._export_props(path)
        except Exception:                            # noqa: BLE001
            break
        out.append((path, names, pkg, props))
        parent = props.get('Parent')
        path = ML._parent_path(pkg, names, parent) if parent is not None else None
    return tuple(out)


def master_of(material):
    """The master material's path at the end of `material`'s parent chain, or None."""
    got = chain(material)
    return got[-1][0] if got else None


def _parameter(material, kind, name=None, guid=None):
    """The first value of a parameter along the chain (instance first): kind 'Texture' gives a
    texture path, 'Vector' an [r, g, b, a], 'Scalar' a float."""
    for path, names, pkg, props in chain(material):
        for p in props.get(kind + 'ParameterValues') or []:
            info = p.get('ParameterInfo') or {}
            if name is not None and info.get('Name') != name:
                continue
            if guid is not None and p.get('ExpressionGUID') != guid:
                continue
            value = p.get('ParameterValue')
            if kind == 'Texture':
                return _import_path(pkg, names, value)
            return value
    return None


def _import_path(pkg, names, index):
    """`/Game/...` package path of an import, or None."""
    if not isinstance(index, int) or index >= 0:
        return None
    r = struct.unpack('<7i', pkg.imports[-index - 1])
    outer = r[4]
    if outer < 0:
        return _imp_name(pkg, names, outer)
    return names[r[5]]


def _slot_guids(master):
    """{BC slot: ExpressionGuid} from the master's cached expression data."""
    got = chain(master)
    _path, names, pkg, props = got[-1]
    ced = props.get('CachedExpressionData') or {}
    entries = (ced.get('Parameters') or {}).get('RuntimeEntries') or {}
    tex = entries.get(2) or entries.get('2') or {}
    defaults = (ced.get('Parameters') or {}).get('TextureValues') or []
    by_default = {}
    for info, g, d in zip(tex.get('ParameterInfos') or [], tex.get('ExpressionGuids') or [], defaults):
        if info.get('Name') == 'BC':
            by_default[_imp_name(pkg, names, d)] = g
    return {slot: by_default.get(tex_name) for slot, tex_name in SLOT_DEFAULTS[master].items()}


def _master_texture(master, leaf):
    """Package path of the master's import named `leaf`."""
    _path, names, pkg, _props = chain(master)[-1]
    for k in range(len(pkg.imports)):
        if _imp_name(pkg, names, -k - 1) == leaf:
            return _import_path(pkg, names, -k - 1)
    return None


def layers(material):
    """[{slot, channel, texture (path), tint [r, g, b], name}] of a supported material's blend,
    base first; SystemExit when its master is not supported."""
    master = master_of(material)
    if master not in BLENDS:
        raise SystemExit('%s: not a mesh-paint terrain material (master %s)' % (material, master))
    guids = _slot_guids(master)
    out = []
    for slot, channel, tint in BLENDS[master]:
        tex = _parameter(material, 'Texture', name='BC', guid=guids[slot])
        if tex is None:
            tex = _master_texture(master, SLOT_DEFAULTS[master][slot])
        colour = _parameter(material, 'Vector', name=tint) if tint else None
        out.append({'slot': slot, 'channel': channel, 'texture': tex,
                    'tint': [float(c) for c in (colour or (1.0, 1.0, 1.0, 1.0))[:3]],
                    'name': layer_name(tex)})
    return out


#: Texture-name words that say nothing about the ground.
NAME_NOISE = ('TEX', 'T', 'LSC', 'col', 'BC', 'D', 'Ground')


def layer_name(texture):
    """A plain name from a layer's texture: Set1_Overgrown_Wet_01_BC -> Overgrown Wet,
    TEX_LSC_Ground_SandA_col -> Sand, Dirt_RedRock_01_BC -> Dirt Red Rock."""
    leaf = str(texture or '').rsplit('/', 1)[-1]
    leaf = re.sub(r'^Set\d+_', '', leaf)
    leaf = re.sub(r'(_\d+)?_BC$', '', leaf)
    words = []
    for w in leaf.split('_'):
        if not w or w in NAME_NOISE or w.isdigit():
            continue
        w = re.sub(r'(?<=[a-z])[A-Z]$', '', w)
        words += re.findall(r'[A-Z][a-z]+|[a-z]+|[A-Z]+(?![a-z])', w) or [w]
    return ' '.join(words) or 'Ground'


def tile_sizes(material):
    """Every TileSize_U / TileSize_V value the material's instances set (1.0 when none)."""
    vals = []
    for _path, _names, _pkg, props in chain(material):
        got = [float(p['ParameterValue']) for p in props.get('ScalarParameterValues') or []
               if (p.get('ParameterInfo') or {}).get('Name') in TILE_SIZE_PARAMS]
        if got:
            vals = got
            break
    return vals or [1.0]


def channels(material_layers):
    """The vertex-colour channels of the layers after the base, in blend order."""
    return tuple(l['channel'] for l in material_layers[1:])


def weights(colors, material_layers):
    """Per-vertex layer weights (V, layers) of the successive lerp."""
    c = np.asarray(colors, np.float64) / 255.0
    w = np.zeros((len(c), len(material_layers)))
    w[:, 0] = 1.0
    for k, ch in enumerate(channels(material_layers), 1):
        w *= (1.0 - c[:, ch:ch + 1])
        w[:, k] = c[:, ch]
    return w


def dominant(colors, material_layers):
    """The name of the layer covering the most of the given vertices (layers sharing a texture
    counted together)."""
    w = weights(colors, material_layers).mean(0)
    by = {}
    for l, v in zip(material_layers, w):
        by[l['name']] = by.get(l['name'], 0.0) + v
    return max(by, key=by.get)


@functools.lru_cache(maxsize=64)
def texture_image(path, size=256):
    """(H, W, 4) uint8 sRGB top mip of a shipped texture, scaled to `size` on its long side."""
    from PIL import Image
    from riftlord.core import gamefiles
    from riftlord.formats.ue import texture
    stem = str(path).replace('/Game/', '../../../OMD/Content/')
    with tempfile.TemporaryDirectory(prefix='terrainkit-tex-') as root:
        staged = {}
        for ext in ('.uasset', '.uexp', '.ubulk'):
            try:
                data = gamefiles.read(stem + ext)
            except KeyError:
                continue
            out = os.path.join(root, 'tex' + ext)
            with open(out, 'wb') as fh:
                fh.write(data)
            staged[ext] = out
        info = texture.parse(staged['.uasset'], staged['.uexp'], staged.get('.ubulk'))
        image = texture.to_image(info).convert('RGBA')
    if max(image.size) > size:
        k = size / max(image.size)
        image = image.resize((max(1, int(image.width * k)), max(1, int(image.height * k))),
                             Image.BILINEAR)
    return np.asarray(image, np.uint8)


def thumb_layers(material_layers, size=256):
    """The layers' base colours for thumbs.render, each multiplied by its tint (sRGB)."""
    out = []
    for l in material_layers:
        img = texture_image(l['texture'], size).astype(np.float64)
        lin = np.where(img[..., :3] <= 10.31, img[..., :3] / 255.0 / 12.92,
                       ((img[..., :3] / 255.0 + 0.055) / 1.055) ** 2.4) * np.array(l['tint'])
        srgb = np.where(lin <= 0.0031308, lin * 12.92, 1.055 * np.clip(lin, 0, None) ** (1 / 2.4) - 0.055)
        rgba = np.concatenate([np.clip(srgb * 255.0, 0, 255), img[..., 3:4]], axis=2)
        out.append(rgba.astype(np.uint8))
    return out


# -- landscape materials on a tile ------------------------------------------------------------

#: Landscape masters a tile may draw with as they are: off a landscape their layer weights
#: compile to each layer's PreviewWeight (UE 4.27 FHLSLMaterialTranslator::StaticTerrainLayerWeight),
#: so the tile shows one fixed blend and its vertex colours are not read. Per master: the
#: ground's name, the scalars scaling UV0 with the factor the shader adds (Master_Landscape_Sand's
#: FLocalVertexFactory base pass, read 2026-10-28: base at UV x Sand_Base_Tiling, detail and
#: small detail at UV x 0.5 x their tiling, BC = 0.5 x each), a rotation parameter, and the
#: thumbnail's texture (the master's import starting `thumb` and ending _BC, or the instance's BC
#: whose path holds `name`) and tint.
#: Not the ice master (Master_Enviro_Landscape_Ice_MP): on a static tile it draws black and
#: strongly reflective (user, Terrain Kit D, 2026-10-28); snow waits for a carried mesh-paint
#: instance.
LANDSCAPE_LOOKS = {
    '/Game/MasterMaterials/Enviro/Sand/Master_Landscape_Sand': {
        'name': 'Sand', 'uv': (('Sand_Base_Tiling', 1.0), ('Sand_Detail_Tiling', 0.5),
                               ('Sand_Small_Detail_Tiling', 0.5)),
        'rotate': 'TextureRotate', 'thumb': 'Sand_Base', 'tint': 'Sand_Base_Color',
        'decoded': True},
}


def landscape_look(material):
    """{name, tile_sizes, rotated, texture, tint, decoded} of a landscape material, or None."""
    master = master_of(material)
    look = LANDSCAPE_LOOKS.get(master)
    if look is None:
        return None
    sizes = []
    for param, factor in look['uv']:
        vals = []
        for _path, _names, _pkg, props in chain(material):
            vals = [float(p['ParameterValue']) for p in props.get('ScalarParameterValues') or []
                    if (p.get('ParameterInfo') or {}).get('Name') == param]
            if vals:
                break
        sizes += [v * factor for v in (vals or [1.0])]
    rotate = _parameter(material, 'Scalar', name=look['rotate']) if look['rotate'] else None
    if look['thumb']:
        _mp, mnames, mpkg, _mprops = chain(master)[-1]
        texture = next((_import_path(mpkg, mnames, -k - 1) for k in range(len(mpkg.imports))
                        if _imp_name(mpkg, mnames, -k - 1).startswith(look['thumb'])
                        and _imp_name(mpkg, mnames, -k - 1).endswith('_BC')), None)
    else:
        texture = None
        for path, names, pkg, props in chain(material):
            for p in props.get('TextureParameterValues') or []:
                t = _import_path(pkg, names, p.get('ParameterValue'))
                if (p.get('ParameterInfo') or {}).get('Name') == 'BC' and t and look['name'].lower() in t.lower():
                    texture = texture or t
    tint = _parameter(material, 'Vector', name=look['tint']) if look['tint'] else None
    return {'name': look['name'], 'tile_sizes': sizes, 'rotated': bool(rotate),
            'texture': texture, 'tint': [float(c) for c in (tint or (1.0, 1.0, 1.0))[:3]],
            'decoded': look['decoded']}
