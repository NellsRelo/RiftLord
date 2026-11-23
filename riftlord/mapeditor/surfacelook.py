"""The surface weathering as the viewport draws it: what each master material does with
MPC_Enviro_Level's values (riftlord/build/world/surfaces.py), each material's own numbers
for it, and the vertex paint of each placement that carries some.

Read from the masters' base-pass pixel shaders (Master_Structure sh061, Master_Prop sh086,
Master_Terrain_Rock sh003, Master_SnowTrees sh002, Master_Snow_Mountains sh000,
M_Ice_Formations sh005; FLocalVF, the same blend in every vertex factory). N is the world
normal, n the geometric normal's z, (R, G, B, A) the vertex colour (white when a mesh has
none and the placement paints none), g the material's MRA green:

* up, kind 'steep': |N.z| * min(3 * max(n, 0), 1);
  kind 'blend': |N.z| * saturate(n ^ Blend * Contrast), 0 when n <= 0.
* snow mask = Snow * SnowOverride * (R * up + min((g * c) ^ SnowVertexMaskTex_Power, 1)),
  c = 1 - B; colour lerps toward Snow_BC * ColorSnow (rock, props, walls) or
  Snow_BC * SnowTint (snow trees, mountains, ice formations).
* sand mask = Sand * SandOverride * saturate(R * up + min((g * c) ^ SandVertexMaskTex_Power, 1)),
  c = 1 - B on walls and rock, c = B on props; colour toward Sand_BC * ColorSand (walls,
  props) or Sand_BC * SandTint (rock).
* ice: colour toward IceTint by Ice * (1 - G), and glow (emissive) toward
  lerp(EM_OuterColor, luminance(BC_Ice, refracted) * EM_InnerColor * Emissive Intensity Ice,
  r.z ^ IceFresnel) by the same mask, less the snow and sand masks over it (r the view
  reflected about the ice normal, tangent space; the viewport takes N . view).
* moss on rock: saturate((N.z + 1) / 2 * (MossWorldBlendMax - MossBlendFalloff)
  + MossBlendFalloff) * A * MossLayerOpacity * MossRock; on walls and props: the baked
  ambient occlusion squared * PrecomputedAOMask * MossCastle * (1 - Moss_WorldOverlay_MK).
  Colour Moss_BC * Moss_Color * (1 + (1 - v) ^ MossFuzzyPower * MossFuzzyBright
  - v * MossFuzzyDark), v = saturate(N . view).
* walls only, before BC_FinalBrightness: lerp toward luminance (0.3, 0.59, 0.11) by
  DesaturateCastleStone, then an overlay blend with ColorStoneCastle.

Rain changes roughness and drips only. The viewport draws a texture with flat lighting, so
it uses the geometric normal for N, the texture means below for textures sampled in world
space, the mean of g ^ power for the all-over term (exact for a placement painted one
colour), the mean of each placement's paint, and no baked occlusion (1).
"""

import functools

#: What each master's blend takes: per layer, its up kind and parameters, its all-over
#: channel ('1-b', 'b' or None) and exponent parameter, its texture parameter and its
#: tint (an MPC value by name, or ('param', name) for the material's own).
FAMILIES = {
    'Master_Structure': {
        'snow': {'up': ('steep',), 'all': ('1-b', 'SnowVertexMaskTex_Power'),
                 'tex': 'Snow_BC', 'tint': 'ColorSnow', 'clamp': False},
        'sand': {'up': ('blend', 'SandBlend', 'SandContrast'),
                 'all': ('1-b', 'SandVertexMaskTex_Power'),
                 'tex': 'Sand_BC', 'tint': 'ColorSand', 'mpc': 'UseSandCastle'},
        'ice': True, 'moss': 'castle', 'stone': True},
    'Master_Prop': {
        'snow': {'up': ('steep',), 'all': ('1-b', 'SnowVertexMaskTex_Power'),
                 'tex': 'Snow_BC', 'tint': 'ColorSnow', 'clamp': False},
        'sand': {'up': ('steep',), 'all': ('b', 'SandVertexMaskTex_Power'),
                 'tex': 'Sand_BC', 'tint': 'ColorSand', 'mpc': 'UseSandCastle'},
        'ice': True, 'moss': 'castle'},
    'Master_Terrain_Rock': {
        'snow': {'up': ('steep',), 'all': ('1-b', 'SnowVertexMaskTex_Power'),
                 'tex': 'Snow_BC', 'tint': 'ColorSnow', 'clamp': False},
        'sand': {'up': ('steep',), 'all': ('1-b', 'SandVertexMaskTex_Power'),
                 'tex': 'Sand_BC', 'tint': ('param', 'SandTint'), 'mpc': 'UseSandRock'},
        'ice': True, 'moss': 'rock'},
    'Master_SnowTrees': {
        'snow': {'up': ('blend', 'SnowBlend', 'SnowContrast'), 'all': None,
                 'tex': 'Snow_BC', 'tint': ('param', 'SnowTint'), 'clamp': True}},
    'Master_Snow_Mountains': {
        'snow': {'up': ('blend', 'SnowBlend', 'SnowContrast'), 'all': None,
                 'tex': 'Snow_BC', 'tint': ('param', 'SnowTint'), 'clamp': True}},
    'M_Ice_Formations': {
        'snow': {'up': ('steep',), 'all': ('1-b', 'SnowVertexMaskTex_Power'),
                 'tex': 'Snow_BC', 'tint': ('param', 'SnowTint'), 'clamp': True}},
}
#: Size, in pixels, the textures are measured at.
MEASURE_SIZE = 256


def _chain(path):
    """(family name, {scalar}, {vector}, {texture: path}) along a material's parents, the
    nearest value winning, or None when the chain ends at no master in FAMILIES."""
    from riftlord.formats.omd3 import omd3matlook
    scalars, vectors, textures = {}, {}, {}
    cur = path
    for _ in range(omd3matlook.MAX_PARENTS):
        if not cur:
            return None
        try:
            names, pkg, props = omd3matlook._export_props(cur)
        except Exception:                            # noqa: BLE001
            return None
        for v in props.get('ScalarParameterValues') or []:
            n = (v.get('ParameterInfo') or {}).get('Name') if isinstance(v, dict) else None
            if n:
                scalars.setdefault(n, v.get('ParameterValue'))
        for v in props.get('VectorParameterValues') or []:
            n = (v.get('ParameterInfo') or {}).get('Name') if isinstance(v, dict) else None
            if n:
                vectors.setdefault(n, v.get('ParameterValue'))
        for v in props.get('TextureParameterValues') or []:
            n = (v.get('ParameterInfo') or {}).get('Name') if isinstance(v, dict) else None
            if n and n not in textures:
                textures[n] = omd3matlook._parent_path(pkg, names, v.get('ParameterValue'))
        if 'CachedExpressionData' in props:
            leaf = cur.rsplit('/', 1)[-1]
            if leaf not in FAMILIES:
                return None
            for n, v in omd3matlook._defaults(props).items():
                (vectors if isinstance(v, list) else scalars).setdefault(n, v)
            params = (props['CachedExpressionData'].get('Parameters') or {})
            entries = params.get('RuntimeEntries') or {}
            infos = (entries[2].get('ParameterInfos') or []) if len(entries) > 2 else []
            for info, ref in zip(infos, params.get('TextureValues') or []):
                n = info.get('Name') if isinstance(info, dict) else None
                if n and n not in textures:
                    textures[n] = omd3matlook._parent_path(pkg, names, ref)
            return leaf, scalars, vectors, textures
        cur = omd3matlook._parent_path(pkg, names, props.get('Parent'))
    return None


@functools.lru_cache(maxsize=256)
def _pixels(texture):
    """float (h, w, 3) of a texture's top mip at MEASURE_SIZE, values 0..1, or None."""
    if not texture:
        return None
    try:
        import io
        import numpy as np
        from PIL import Image
        from riftlord.mapeditor import omd3serve
        png = omd3serve.texture_png(texture, size=MEASURE_SIZE)
        if not png:
            return None
        return np.asarray(Image.open(io.BytesIO(png)).convert('RGB'), np.float64) / 255.0
    except Exception:                                # noqa: BLE001
        return None


def _mean_linear(texture):
    """[r, g, b] mean of a base-colour (sRGB) texture in linear light, or None."""
    px = _pixels(texture)
    if px is None:
        return None
    import numpy as np
    lin = np.where(px <= 0.04045, px / 12.92, ((px + 0.055) / 1.055) ** 2.4)
    return [round(float(c), 4) for c in lin.reshape(-1, 3).mean(axis=0)]


def _mean_green_pow(texture, power):
    """Mean of (MRA green) ^ power, or None."""
    px = _pixels(texture)
    if px is None or power is None:
        return None
    return round(float((px[..., 1] ** float(power)).mean()), 4)


def _rgb(v, default=(1.0, 1.0, 1.0)):
    return [float(x) for x in v[:3]] if isinstance(v, list) and len(v) >= 3 else list(default)


def _mul(a, b):
    return [round(x * y, 4) for x, y in zip(a, b)]


@functools.lru_cache(maxsize=2048)
def describe(material):
    """What the viewport needs to draw one material's weathering (module docstring), or
    None when its master reads none of MPC_Enviro_Level's values."""
    path = _path(material)
    got = _chain(path) if path else None
    if not got:
        return None
    family, sc, vec, tex = got
    spec = FAMILIES[family]
    mra = tex.get('MRA')
    out = {'family': family}
    for layer in ('snow', 'sand'):
        s = spec.get(layer)
        if not s:
            continue
        over = 'SnowOverride' if layer == 'snow' else 'SandOverride'
        up = s['up']
        d = {'over': float(sc.get(over, 1.0)), 'up': up[0],
             'blend': float(sc.get(up[1], 1.0)) if up[0] == 'blend' else 0.0,
             'contrast': float(sc.get(up[2], 1.0)) if up[0] == 'blend' else 0.0,
             'clamp': bool(s.get('clamp', True))}
        if s['all']:
            power = sc.get(s['all'][1])
            d['all'] = s['all'][0]
            d['power'] = float(power) if power is not None else 1.0
            d['allMean'] = _mean_green_pow(mra, d['power']) or 0.0
        colour = _mean_linear(tex.get(s['tex'])) or [1.0, 1.0, 1.0]
        tint = s['tint']
        if isinstance(tint, tuple):
            d['colour'] = _mul(colour, _rgb(vec.get(tint[1])))
            d['mpcColour'] = None
        else:
            d['colour'] = colour
            d['mpcColour'] = tint
        d['mpc'] = s.get('mpc', 'UseSnowCastle' if layer == 'snow' else None)
        out[layer] = d
    if spec.get('ice'):
        ice = _mean_linear(tex.get('BC_Ice'))
        out['ice'] = {'tint': _rgb(vec.get('IceTint'), (0.0, 0.0, 0.0)),
                      'inner': _mul(_rgb(vec.get('EM_InnerColor'), (0.0, 0.0, 0.0)),
                                    [float(sc.get('Emissive Intensity Ice', 0.0))] * 3),
                      'outer': _rgb(vec.get('EM_OuterColor'), (0.0, 0.0, 0.0)),
                      'fresnel': float(sc.get('IceFresnel', 1.0)),
                      'luminance': round(0.3 * ice[0] + 0.59 * ice[1] + 0.11 * ice[2], 4)
                      if ice else 0.0}
    moss = spec.get('moss')
    if moss:
        m = {'kind': moss,
             'colour': _mul(_mean_linear(tex.get('Moss_BC')) or [1.0, 1.0, 1.0],
                            _rgb(vec.get('Moss_Color'))),
             'fuzzPower': float(sc.get('MossFuzzyPower', 1.0)),
             'fuzzBright': float(sc.get('MossFuzzyBright', 0.0)),
             'fuzzDark': float(sc.get('MossFuzzyDark', 0.0))}
        if moss == 'rock':
            m['falloff'] = float(sc.get('MossBlendFalloff', 0.0))
            m['max'] = float(sc.get('MossWorldBlendMax', 1.0))
            m['opacity'] = float(sc.get('MossLayerOpacity', 1.0))
        else:
            overlay = _pixels(tex.get('Moss_WorldOverlay_MK'))
            # the shader reads the mask's green (swizzle .yzwx into w)
            mean = float(overlay[..., 1].mean()) if overlay is not None else 0.0
            m['scale'] = round(float(sc.get('PrecomputedAOMask', 1.0)) * (1.0 - mean), 4)
        out['moss'] = m
    if spec.get('stone'):
        out['stone'] = {'finalBrightness': _rgb(vec.get('BC_FinalBrightness'))}
    return out


def _path(material):
    """`/Game/...` of a material named by path or bare name, or None."""
    if not material:
        return None
    if str(material).startswith('/Game/'):
        return str(material)
    from riftlord.formats.omd3 import omd3actors
    found = omd3actors._asset_for(material)
    return '/Game/' + found.split('OMD/Content/')[-1] if found else None


def materials(names):
    """{material: description} of every one of `names` that weathers."""
    out = {}
    for n in names or []:
        try:
            d = describe(str(n))
        except Exception:                            # noqa: BLE001
            d = None
        if d:
            out[str(n)] = d
    return out


@functools.lru_cache(maxsize=8)
def paint(level_name):
    """{piece id: [r, g, b, a] 0..1}: the mean vertex paint of each level placement that
    paints its mesh (StaticMeshComponent OverrideVertexColors, LOD 0)."""
    from riftlord.formats.omd3 import omd3level
    from riftlord.mapeditor import leveldata
    from riftlord.terrainkit import sources
    level = leveldata.level(level_name)
    if level.get('scratch'):
        return {}
    pkgs = omd3level.sublevels(level_name) + omd3level.extra_sublevels(level_name)
    by_sub = {}
    for i, pkg in enumerate(pkgs):
        leaf = pkg.rsplit('/', 1)[-1]
        by_sub['persistent' if i == 0 else leaf[len(level_name) + 1:] if
               leaf.startswith(level_name + '_') else leaf] = pkg
    loaded = {}
    out = {}
    for p in level['pieces']:
        if p.get('source') not in ('component', 'blueprint'):
            continue
        comp = p.get('component') or (p.get('export') if p.get('source') == 'component' else None)
        pkg = by_sub.get(p.get('sub'))
        if not comp or not pkg:
            continue
        if pkg not in loaded:
            try:
                loaded[pkg] = omd3level._load(pkg)
            except (Exception, SystemExit):          # noqa: BLE001
                loaded[pkg] = None
        got = loaded[pkg]
        if not got:
            continue
        info, uexp, hdr = got
        if not 0 < comp <= len(info['exports']):
            continue
        cls = str(info['exports'][comp - 1].get('class') or info['exports'][comp - 1].get('cls') or '')
        if 'StaticMeshComponent' not in cls:
            continue
        try:
            a, b = sources._tail(info, uexp, hdr, comp - 1)
            col = sources.override_colours(uexp, a, b)
        except Exception:                            # noqa: BLE001
            col = None
        if col is not None and len(col):
            out[str(p['id'])] = [round(float(c) / 255.0, 4) for c in col.mean(axis=0)]
    return out
