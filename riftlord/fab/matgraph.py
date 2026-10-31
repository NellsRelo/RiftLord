# Generated from OMD2-Converter/thirdparty/fabconvert/matgraph.py by thirdparty/fabconvert/port_riftlord.py; edit the source.
"""Evaluate a Fab pack's EDITOR material graph numerically, per texel.

The pack graph reader (thirdparty/material-tools/packgraph.py) turns a material into
formula strings; this module walks the same expression graph (its Pack / Graph / Node
classes) and computes values with numpy over a set of sample points (a "domain"):

    domain = {'uv': [(P,2) per UV channel], 'wuv': (P,2) world-planar coordinate in uu,
              'vc': (P,4) vertex colour 0..1, 'normal': (P,3)}

Every value carries the set of domain inputs it depends on ('uv0', 'wuv', 'vc', ...), so
one cheap "probe" run tells which texture sample reads which coordinate, and a "bake" run
over a texel grid produces the baked pins (BaseColor, Metallic, Roughness, AO, Normal,
Emissive, OpacityMask). Every place where the value can only be approximated (a view
dependent node, a motion term frozen at time 0, ...) is recorded in `notes`.

Engine material functions are implemented natively by name (ENGINE_FUNCTIONS); an
unknown node class or engine function raises Unsupported, which the caller turns into a
"fallback" grade. Vault data is untrusted: run under `python3 -I`.
"""

import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TP = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(TP, 'material-tools'), os.path.join(TP, 'survey-tools')]

from riftlord.fab import packgraph as pg                      # noqa: E402
from riftlord.fab.survey.pkgread import find                    # noqa: E402

#: Output pins a conversion reads (the rest - WPO, refraction, ... - are not baked).
PINS = ('BaseColor', 'Metallic', 'Specular', 'Roughness', 'AmbientOcclusion', 'Normal', 'EmissiveColor',
        'Opacity', 'OpacityMask', 'SubsurfaceColor')
#: Pin defaults when the pin is unconnected (UE material compiler defaults).
PIN_DEFAULTS = {'BaseColor': (0.0, 0.0, 0.0), 'Metallic': (0.0,), 'Specular': (0.5,), 'Roughness': (0.5,),
                'AmbientOcclusion': (1.0,), 'Normal': (0.0, 0.0, 1.0), 'EmissiveColor': (0.0, 0.0, 0.0),
                'Opacity': (1.0,), 'OpacityMask': (1.0,), 'SubsurfaceColor': (1.0, 1.0, 1.0)}
#: Material attribute ids (SetMaterialAttributes AttributeSetTypes) -> attribute name, from
#: UE 4.27 MaterialShared.cpp FMaterialAttributeDefinitionMap::InitializeAttributeMap.
ATTRIBUTE_GUIDS = {
    '69B8D336-16ED4D49-9AA49729-2F050F7A': 'BaseColor',
    '57C3A161-7F064296-B00B24A5-A496F34C': 'Metallic',
    '9FDAB399-25564CC9-8CD2D572-C12C8FED': 'Specular',
    'D1DD967C-4CAD47D3-9E6346FB-08ECF210': 'Roughness',
    '55E2B4FB-C1C54DB2-9F11875F-7231EB1E': 'Anisotropy',
    'B769B54D-D08D4440-ABC21BA6-CD27D0E2': 'EmissiveColor',
    'B8F50FBA-2A754EC1-9EF672CF-EB27BF51': 'Opacity',
    '679FFB17-2BB5422C-AD520483-166E0C75': 'OpacityMask',
    '0FA2821A-200F4A4A-B719B789-C1259C64': 'Normal',
    'D5F8E9CF-CDC3468D-B10E4465-596A7BBA': 'Tangent',
    'F905F895-D5814314-916D2434-8C40CE9E': 'WorldPositionOffset',
    '5B8FC679-51CE4082-9D777BEE-F4F72C44': 'SubsurfaceColor',
    '9E502E69-3C8F48FA-94645CFD-28E5428D': 'ClearCoat',
    'BE4F2FFD-12FC4296-B0124EEA-12C28D92': 'ClearCoatRoughness',
    'E8EBD0AD-B1654CBE-B079C3A8-B39B9F15': 'AmbientOcclusion',
    'D0B0FA03-14D74455-A851BAC5-81A0788B': 'Refraction',
    '0AC97EC3-E3D047BA-B610167D-C4D919FF': 'PixelDepthOffset',
}
#: QualitySwitch input used for the game's quality (OMD3 ships at High; Epic falls back the same).
QUALITY = 'High'
#: FeatureLevelSwitch input used (OMD3 ships PCD3D_SM5 only, MATERIALS.md section 2).
FEATURE_LEVEL = 'SM5'
#: Constant stand-ins for scene-dependent leaves (value, note).
LEAF_STANDINS = {
    'Time': (0.0, None),
    'TwoSidedSign': (1.0, None),
    'PerInstanceFadeAmount': (0.0, None),
    'PixelDepth': (1000.0, 'PixelDepth read as 1000 uu'),
    'SceneDepth': (100000.0, 'SceneDepth read as far'),
    # 0 = unoccluded: what the engine gives a surface with no baked AO, which a converted mesh
    # never has. Read as 1 it painted every Sky Dungeon surface its master's dark AO colour.
    'PrecomputedAOMask': (0.0, 'PrecomputedAOMask (lightmap AO) read as 0: no baked crevice darkening'),
    'ObjectRadius': (200.0, 'ObjectRadius read as 200 uu'),
    'DistanceToNearestSurface': (0.0, None),
    'ParticleColor': (1.0, 'ParticleColor read as white'),
    'DynamicParameter': (1.0, 'DynamicParameter read as 1'),
}
#: Sampler types that decode as tangent-space normals.
NORMAL_SAMPLERS = ('SAMPLERTYPE_Normal',)
#: Value used for a texture whose pixels are not in the pack (engine content).
MISSING_TEXTURE = 0.5
#: Channels of a TextureSample output by output index (UE4.27 order).
TEX_OUTPUTS = ('rgb', 'r', 'g', 'b', 'a', 'rgba')
CH = 'rgba'


class Unsupported(Exception):
    """The graph uses something this evaluator cannot compute."""


class NoLandscape(Unsupported):
    """A runtime virtual texture sample: the landscape a piece was blended into. Fab pieces stand
    on no UE landscape in OMD3, so no such texture is ever rendered under them."""


class Val:
    """(P, C) float32 values and the domain inputs they depend on."""
    __slots__ = ('a', 'deps')

    def __init__(self, a, deps=frozenset()):
        a = np.asarray(a, np.float32)
        if a.ndim == 1:
            a = a[:, None]
        self.a, self.deps = a, frozenset(deps)

    @property
    def c(self):
        return self.a.shape[1]


class TexRef:
    """A texture object flowing through the graph (TextureObject / TextureObjectParameter)."""
    __slots__ = ('path', 'sampler', 'param')

    def __init__(self, path, sampler, param=None):
        self.path, self.sampler, self.param = path, sampler, param


def _pair(a, b):
    """Broadcast two values the way the material compiler does: a scalar spreads, otherwise
    the narrower width wins (UE refuses mismatched widths; real graphs mask first)."""
    x, y = a.a, b.a
    if x.shape[1] == 1 and y.shape[1] > 1:
        x = np.repeat(x, y.shape[1], 1)
    elif y.shape[1] == 1 and x.shape[1] > 1:
        y = np.repeat(y, x.shape[1], 1)
    elif x.shape[1] != y.shape[1]:
        n = min(x.shape[1], y.shape[1])
        x, y = x[:, :n], y[:, :n]
    return x, y, a.deps | b.deps


def _const(v, P):
    v = np.atleast_1d(np.asarray(v, np.float32))
    return Val(np.broadcast_to(v, (P, len(v))).copy())


def srgb_to_linear(c):
    c = np.asarray(c, np.float32)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4).astype(np.float32)


def linear_to_srgb(x):
    x = np.clip(np.asarray(x, np.float32), 0, 1)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055).astype(np.float32)


class Params:
    """Parameter values of one material instance chain: MIC overrides over master defaults."""

    def __init__(self, scalars=None, vectors=None, textures=None, switches=None, masks=None):
        self.scalars = dict(scalars or {})
        self.vectors = dict(vectors or {})
        self.textures = dict(textures or {})
        self.switches = dict(switches or {})
        self.masks = dict(masks or {})

    def key(self):
        return (tuple(sorted(self.scalars.items())), tuple(sorted((k, tuple(v)) for k, v in self.vectors.items())),
                tuple(sorted(self.textures.items())), tuple(sorted(self.switches.items())),
                tuple(sorted(self.masks.items())))


class Env:
    def __init__(self, g, bindings=None, parent=None, key=()):
        self.g, self.bindings, self.parent, self.key = g, bindings or {}, parent, key


class Evaluator:
    """One evaluation of one material over one domain.

    textures: object with .sample(path, uv (P,2), srgb, sampler) -> (P,4) float32 and
              .info(path) -> {'SRGB', 'CompressionSettings'}.
    sample_override: {sample key: (P? or 4,) constant} - substitute a sample by a constant
              (its texture mean) when it cannot be baked in the chosen frame.
    """

    def __init__(self, pack, material_graph, params, domain, textures, sample_override=None, record=False):
        self.pack, self.g0, self.params, self.dom, self.tex = pack, material_graph, params, domain, textures
        self.P = len(domain['uv'][0]) if domain.get('uv') else len(domain['wuv'])
        self.memo = {}
        self.notes = set()
        self.samples = [] if record else None
        self.override = sample_override or {}
        self.stack = set()

    # -- entry points ---------------------------------------------------------------------------

    def pin_refs(self):
        g = self.g0
        refs = pg.pin_refs(g)
        use_ma = bool((find(g.mprops, 'bUseMaterialAttributes') or {}).get('value'))
        return refs, use_ma

    def pins(self, names=PINS):
        refs, use_ma = self.pin_refs()
        env = Env(self.g0)
        out = {}
        if use_ma:
            ma = refs.get('MaterialAttributes')
            attrs = self.eval_ref(env, ma) if ma and ma['expr'] else {}
            if not isinstance(attrs, dict):
                raise Unsupported('MaterialAttributes pin is not attributes')
            for k in names:
                if k in attrs and attrs[k] is not None:
                    out[k] = attrs[k]
        else:
            for k in names:
                r = refs.get(k)
                if r is None:
                    continue
                if r['expr']:
                    v = self.eval_ref(env, r)
                    if v is None and env.g.node(r['expr']) is not None and env.g.node(r['expr']).cls == 'Reroute':
                        continue        # UE traces a dangling reroute to no input: the pin default
                    if isinstance(v, dict) or isinstance(v, TexRef) or v is None:
                        raise Unsupported('pin %s does not evaluate to a value' % k)
                    out[k] = v
                elif r.get('use_constant') and r.get('constant') is not None:
                    c = r['constant']
                    if isinstance(c, tuple) and len(c) == 4 and k == 'BaseColor':
                        c = tuple(x / 255.0 for x in c[:3])
                    out[k] = _const(c, self.P)
        for k in list(out):
            want = len(PIN_DEFAULTS.get(k, (0,)))
            v = out[k]
            if v.c > want:
                out[k] = Val(v.a[:, :want], v.deps)
            elif v.c < want:
                out[k] = Val(np.repeat(v.a[:, :1], want, 1) if v.c == 1 else
                             np.concatenate([v.a, np.zeros((self.P, want - v.c), np.float32)], 1), v.deps)
        return out

    # -- references -----------------------------------------------------------------------------

    def eval_ref(self, env, ref):
        if not ref or not ref.get('expr'):
            return None
        n = env.g.node(ref['expr'])
        if n is None:
            raise Unsupported('dangling expression reference %s' % ref['expr'])
        key = (env.key, n.idx, ref.get('out', 0))
        if key in self.stack:
            raise Unsupported('cycle at %s' % n.name)
        if key not in self.memo:
            self.stack.add(key)
            try:
                self.memo[key] = self.node(env, n, ref)
            finally:
                self.stack.discard(key)
        v = self.memo[key]
        return self.masked(v, ref, n)

    def masked(self, v, ref, n):
        if not isinstance(v, Val) or not ref.get('mask'):
            return v
        pick = [i for i, k in enumerate(('mr', 'mg', 'mb', 'ma')) if ref.get(k)]
        if not pick:
            return v
        if max(pick) >= v.c:
            if v.c == 1:
                return v
            pick = [i for i in pick if i < v.c] or [0]
        return Val(v.a[:, pick], v.deps)

    def inp(self, env, n, k, const=None, default=None):
        r = n.inp(k)
        if r:
            v = self.eval_ref(env, r)
            if v is not None:
                return v
        if const is not None:
            c = n.get(const)
            if c is not None:
                return _const(c, self.P)
        if default is not None:
            return _const(default, self.P)
        return None

    # -- nodes ----------------------------------------------------------------------------------

    def node(self, env, n, ref):
        c = n.cls
        P = self.P
        I = lambda k, const=None, default=None: self.inp(env, n, k, const, default)  # noqa: E731
        if c in ('Reroute', 'NamedRerouteDeclaration'):
            return self.eval_ref(env, n.inp('Input'))
        if c == 'NamedRerouteUsage':
            d = env.g.node(n.get('Declaration')) if isinstance(n.get('Declaration'), int) else None
            if d is None:
                raise Unsupported('named reroute without declaration')
            return self.eval_ref(env, d.inp('Input'))
        if c == 'ComponentMask':
            v = I('Input')
            pick = [i for i, k in enumerate('RGBA') if n.get(k)]
            return self._pick(v, pick)
        if c in ('Multiply', 'Add', 'Subtract', 'Divide', 'Max', 'Min'):
            a, b = I('A', 'ConstA'), I('B', 'ConstB')
            x, y, d = _pair(a, b)
            with np.errstate(divide='ignore', invalid='ignore'):
                r = {'Multiply': lambda: x * y, 'Add': lambda: x + y, 'Subtract': lambda: x - y,
                     'Divide': lambda: np.where(np.abs(y) < 1e-12, 0.0, x / np.where(y == 0, 1, y)),
                     'Max': lambda: np.maximum(x, y), 'Min': lambda: np.minimum(x, y)}[c]()
            return Val(r, d)
        if c == 'LinearInterpolate':
            a, b, t = I('A', 'ConstA'), I('B', 'ConstB'), I('Alpha', 'ConstAlpha')
            x, y, d = _pair(a, b)
            ta = t.a if t.c == 1 or t.c == x.shape[1] else t.a[:, :1]
            return Val(x + (y - x) * ta, d | t.deps)
        if c == 'Power':
            a, e = I('Base'), I('Exponent', 'ConstExponent')
            x, y, d = _pair(a, e)
            return Val(np.power(np.maximum(x, 0), y), d)
        if c == 'Clamp':
            v, lo, hi = I('Input'), I('Min', 'MinDefault'), I('Max', 'MaxDefault')
            x, l, dl = _pair(v, lo)
            x2, h, dh = _pair(Val(x, dl), hi)
            return Val(np.minimum(np.maximum(x2, l[:, :x2.shape[1]] if l.shape[1] >= x2.shape[1] else l), h), dl | dh)
        if c in ('OneMinus', 'Abs', 'Saturate', 'Floor', 'Ceil', 'Frac', 'SquareRoot'):
            v = I('Input')
            f = {'OneMinus': lambda x: 1 - x, 'Abs': np.abs, 'Saturate': lambda x: np.clip(x, 0, 1),
                 'Floor': np.floor, 'Ceil': np.ceil, 'Frac': lambda x: x - np.floor(x),
                 'SquareRoot': lambda x: np.sqrt(np.maximum(x, 0))}[c]
            return Val(f(v.a), v.deps)
        if c == 'BlackBody':
            # MaterialTemplate.ush MaterialExpressionBlackBody: the colour of a black body at
            # Temp kelvin, scaled by (0.0004 Temp)^4 (Sewer Dungeon brazier embers)
            v = I('Temp')
            t = v.a[:, :1].astype(np.float64)
            u = (0.860117757 + 1.54118254e-4 * t + 1.28641212e-7 * t * t) / (1.0 + 8.42420235e-4 * t + 7.08145163e-7 * t * t)
            w = (0.317398726 + 4.22806245e-5 * t + 4.20481691e-8 * t * t) / (1.0 - 2.89741816e-5 * t + 1.61456053e-7 * t * t)
            den = 2 * u - 8 * w + 4
            x, y = 3 * u / den, 2 * w / den
            xyz = np.concatenate([x / y, np.ones_like(y), (1 - x - y) / y], 1)
            m = np.array([[3.2404542, -1.5371385, -0.4985314], [-0.9692660, 1.8760108, 0.0415560],
                          [0.0556434, -0.2040259, 1.0572252]])
            return Val((xyz @ m.T * (0.0004 * t) ** 4).astype(np.float32), v.deps)
        if c == 'Normalize':
            v = I('VectorInput')
            ln = np.linalg.norm(v.a, axis=1, keepdims=True)
            return Val(v.a / np.maximum(ln, 1e-12), v.deps)
        if c in ('Sine', 'Cosine'):
            v = I('Input')
            per = n.get('Period') or 1.0
            f = np.sin if c == 'Sine' else np.cos
            return Val(f(v.a * (2 * math.pi / per)) if per > 0 else f(v.a), v.deps)
        if c == 'DotProduct':
            a, b = I('A'), I('B')
            x, y, d = _pair(a, b)
            return Val((x * y).sum(1, keepdims=True), d)
        if c == 'CrossProduct':
            # cross(ddx(WorldPosition), ddy(WorldPosition)): the face normal, toward the camera
            # (screen x right, y down)
            ra, rb = n.inp('A'), n.inp('B')
            na = env.g.node(ra['expr']) if ra else None
            nb = env.g.node(rb['expr']) if rb else None
            if na is not None and nb is not None and na.cls == 'DDX' and nb.cls == 'DDY':
                va, vb = na.inp('Value'), nb.inp('Value')
                nv = env.g.node(va['expr']) if va else None
                if nv is not None and nv.cls == 'WorldPosition' and vb and vb['expr'] == va['expr']:
                    self.notes.add('normal from screen derivatives of the world position read as the face normal')
                    return Val(self.dom['normal'], {'normal'})
            a, b = I('A'), I('B')
            return Val(np.cross(a.a[:, :3], b.a[:, :3]), a.deps | b.deps)
        if c == 'Distance':
            a, b = I('A'), I('B')
            x, y, d = _pair(a, b)
            return Val(np.linalg.norm(x - y, axis=1, keepdims=True), d)
        if c == 'Constant':
            return _const(n.get('R', 0.0), P)
        if c == 'Constant2Vector':
            return _const((n.get('R', 0.0), n.get('G', 0.0)), P)
        if c in ('Constant3Vector', 'Constant4Vector'):
            v = tuple(n.get('Constant') or (0.0, 0.0, 0.0, 0.0))
            return _const(v[:3] if c == 'Constant3Vector' else v[:4], P)
        if c == 'AppendVector':
            a, b = I('A'), I('B')
            return Val(np.concatenate([a.a, b.a], 1), a.deps | b.deps)
        if c == 'TextureCoordinate':
            ci = int(n.get('CoordinateIndex', 0) or 0)
            uvs = self.dom.get('uv') or []
            if ci >= len(uvs):
                raise Unsupported('UV channel %d not in the domain' % ci)
            uv = uvs[ci] * np.array([n.get('UTiling', 1.0), n.get('VTiling', 1.0)], np.float32)
            return Val(uv, {'uv%d' % ci})
        if c == 'Panner':
            coord = I('Coordinate') or self._uv(int(n.get('ConstCoordinate') or 0))
            t = I('Time')
            sp = I('Speed')
            tval = t.a[:, :1] if t is not None else 0.0
            if t is None:
                self.notes.add('panner motion frozen at time 0')
                return coord
            speed = sp.a[:, :2] if sp is not None else np.array([n.get('SpeedX', 0.0), n.get('SpeedY', 0.0)], np.float32)
            return Val(coord.a[:, :2] + speed * tval, coord.deps | t.deps | (sp.deps if sp is not None else frozenset()))
        if c == 'Rotator':
            coord = I('Coordinate') or self._uv(int(n.get('ConstCoordinate') or 0))
            t = I('Time')
            if t is None:
                self.notes.add('rotator motion frozen at time 0')
                return coord
            ang = t.a[:, :1] * float(n.get('Speed', 0.25))
            cx, cy = float(n.get('CenterX', 0.5)), float(n.get('CenterY', 0.5))
            u, v = coord.a[:, 0:1] - cx, coord.a[:, 1:2] - cy
            ca, sa = np.cos(ang), np.sin(ang)
            return Val(np.concatenate([u * ca - v * sa + cx, u * sa + v * ca + cy], 1), coord.deps | t.deps)
        if c == 'WorldPosition':
            w = self.dom.get('wuv')
            if w is None:
                raise Unsupported('world position not in the domain')
            self.notes.add('world position read directly: baked as a top-down (Z) projection')
            return Val(np.concatenate([-w, np.zeros((P, 1), np.float32)], 1), {'wuv'})
        if c == 'VertexColor':
            return Val(self.dom['vc'], {'vc'})
        if c in ('VertexNormalWS', 'PixelNormalWS', 'CameraVectorWS', 'ReflectionVectorWS'):
            if c in ('CameraVectorWS', 'ReflectionVectorWS'):
                self.notes.add('%s read as the surface normal (view-dependent)' % c)
            return Val(self.dom['normal'], {'normal'})
        if c == 'RuntimeVirtualTextureSample':
            raise NoLandscape('runtime virtual texture (landscape) sample')
        if c == 'PerInstanceRandom':
            # UE returns 0 off instanced meshes; Rift Lord places pieces as StaticMeshActors
            return _const((0.0,), P)
        if c == 'Fresnel':
            self.notes.add('Fresnel evaluated head-on (view-dependent)')
            base = I('BaseReflectFractionIn', 'BaseReflectFraction')
            return base
        if c in ('StaticSwitchParameter', 'StaticSwitch'):
            if c == 'StaticSwitchParameter':
                nm = n.get('ParameterName')
                on = self.params.switches.get(nm, bool(n.get('DefaultValue')))
            else:
                vv = n.inp('Value')
                on = bool(n.get('DefaultValue'))
                if vv:
                    x = self.eval_ref(env, vv)
                    on = bool(x) if isinstance(x, bool) else on
            return self.eval_ref(env, n.inp('A' if on else 'B'))
        if c in ('StaticBoolParameter', 'StaticBool'):
            if c == 'StaticBool':
                return bool(n.get('Value'))
            return self.params.switches.get(n.get('ParameterName'), bool(n.get('DefaultValue')))
        if c == 'StaticComponentMaskParameter':
            v = I('Input')
            m = self.params.masks.get(n.get('ParameterName'))
            pick = [i for i, k in enumerate('RGBA') if (m[i] if m else n.get('Default' + k))]
            return self._pick(v, pick)
        if c == 'ScalarParameter':
            nm = n.get('ParameterName')
            return _const(self.params.scalars.get(nm, n.get('DefaultValue', 0.0)), P)
        if c == 'VectorParameter':
            nm = n.get('ParameterName')
            v = self.params.vectors.get(nm) or n.get('DefaultValue') or (0.0, 0.0, 0.0, 0.0)
            return _const(tuple(v)[:4], P)
        if c in LEAF_STANDINS:
            v, note = LEAF_STANDINS[c]
            if note:
                self.notes.add(note)
            return _const(v, P)
        if c in pg.TEXTURE_CLASSES:
            return self.texture(env, n, ref)
        if c == 'Desaturation':
            v = I('Input')
            fr = I('Fraction', default=1.0)
            lum = np.asarray(n.get('LuminanceFactors') or (0.3, 0.59, 0.11, 0.0), np.float32)[:3]
            rgb = v.a[:, :3]
            g = (rgb * lum).sum(1, keepdims=True)
            return Val(rgb + (g - rgb) * fr.a[:, :1], v.deps | fr.deps)
        if c == 'ConstantBiasScale':
            v = I('Input')
            return Val((v.a + float(n.get('Bias'))) * float(n.get('Scale')), v.deps)
        if c == 'If':
            a, b = I('A'), I('B', 'ConstB')
            gt, eq, lt = I('AGreaterThanB'), I('AEqualsB'), I('ALessThanB')
            eq = eq if eq is not None else gt
            thr = float(n.get('EqualsThreshold', 1e-5))
            x, y, d = _pair(a, b)
            x, y = x[:, :1], y[:, :1]
            outs = [v for v in (gt, eq, lt) if v is not None]
            w = max(v.c for v in outs)
            sel = lambda v: np.repeat(v.a, w, 1) if v.c == 1 else v.a[:, :w]  # noqa: E731
            r = np.where(np.abs(x - y) <= thr, sel(eq), np.where(x > y, sel(gt), sel(lt)))
            return Val(r, d | gt.deps | eq.deps | lt.deps)
        if c == 'DepthFade':
            self.notes.add('DepthFade read as its input opacity')
            return I('InOpacity', 'OpacityDefault')
        if c == 'SphereMask':
            a, b = I('A'), I('B')
            rad = I('Radius', 'AttenuationRadius')
            hard = I('Hardness', 'HardnessPercent')
            x, y, d = _pair(a, b)
            dist = np.linalg.norm(x - y, axis=1, keepdims=True)
            h = np.clip(hard.a[:, :1] / 100.0, 0, 0.9999)
            r = np.clip((1 - dist / np.maximum(rad.a[:, :1], 1e-6)) / (1 - h), 0, 1)
            return Val(r, d | rad.deps | hard.deps)
        if c == 'Transform':
            self.notes.add('vector Transform read as identity')
            return I('Input')
        if c == 'Custom':
            # only HLSL that hands back one of its inputs: a bare return, or the Lumen card switch
            # (OMD3 renders no Lumen cards, so its non-Lumen branch)
            import re
            code = ' '.join(re.sub(r'//[^\n]*', '', str(n.get('Code') or '')).split())
            m = (re.fullmatch(r'#if LUMEN_CARD_CAPTURE return \w+ ?; #else return (\w+) ?; #endif', code)
                 or re.fullmatch(r'return (\w+) ?;', code))
            if not m:
                raise Unsupported('expression Custom')
            for item in (n.byname.get('Inputs') or {}).get('items') or []:
                nm = find(item, 'InputName')
                if nm and nm.get('value') == m.group(1):
                    r = pg.read_input(env.g.p, find(item, 'Input'))
                    if r and r['expr']:
                        return self.eval_ref(env, r)
            raise Unsupported('Custom input %s not wired' % m.group(1))
        if c == 'ShadowReplace':
            return I('Default')                 # the bake is the main pass, not the shadow pass
        if c == 'TransformPosition':
            # the piece sits at the origin at scale 1 and the camera at the origin: local, world
            # and translated world are one space
            spaces = ('TRANSFORMPOSSOURCE_Local', 'TRANSFORMPOSSOURCE_World', 'TRANSFORMPOSSOURCE_TranslatedWorld')
            src = str(n.get('TransformSourceType') or 'TRANSFORMPOSSOURCE_Local')
            dst = str(n.get('TransformType') or 'TRANSFORMPOSSOURCE_Local')
            if src not in spaces or dst not in spaces:
                raise Unsupported('TransformPosition %s -> %s' % (src, dst))
            return I('Input')
        if c in ('SkyAtmosphereLightDirection', 'AtmosphericLightVector'):
            # UE's DefaultSunDirection when no light drives the atmosphere (SceneRendering.cpp)
            self.notes.add('%s read as overhead (0, 0, 1): per level in game' % c)
            return _const((0.0, 0.0, 1.0), P)
        if c == 'FeatureLevelSwitch':
            return self._switch(env, n, pg.FEATURE_LEVELS, FEATURE_LEVEL)
        if c == 'QualitySwitch':
            return self._switch(env, n, pg.QUALITY_LEVELS, QUALITY)
        if c == 'Noise':
            self.notes.add('procedural Noise read as 0.5')
            return _const(0.5, P)
        if c == 'DistanceFieldGradient':
            return _const((0.0, 0.0, 1.0), P)
        if c == 'ObjectPositionWS' or c == 'CameraPositionWS':
            return _const((0.0, 0.0, 0.0), P)
        if c == 'MakeMaterialAttributes':
            out = {}
            for k, r in n.inputs():
                v = self.eval_ref(env, r)
                if isinstance(v, Val):
                    out[k] = v
            return out
        if c == 'SetMaterialAttributes':
            refs = pg.read_input_array(env.g.p, n.byname.get('Inputs'))
            kinds = pg.read_guid_array(env.g.p, n.byname.get('AttributeSetTypes'))
            base = self.eval_ref(env, refs[0]) if refs and refs[0]['expr'] else {}
            if not isinstance(base, dict):
                raise Unsupported('SetMaterialAttributes of a non-attribute value')
            out = dict(base)
            for r, gid in zip(refs[1:], kinds):
                name = ATTRIBUTE_GUIDS.get(gid)
                if name and r['expr']:
                    v = self.eval_ref(env, r)
                    if isinstance(v, Val):
                        out[name] = v
            return out
        if c == 'BlendMaterialAttributes':
            # a side that needs the landscape (runtime virtual texture) gives way to the other
            ra, rb = n.inp('A'), n.inp('B')
            try:
                a = self.eval_ref(env, ra) if ra else {}
            except NoLandscape:
                self.notes.add('landscape blend (runtime virtual texture) dropped: no landscape under the piece')
                return self.eval_ref(env, rb) if rb else {}
            try:
                b = self.eval_ref(env, rb) if rb else {}
            except NoLandscape:
                self.notes.add('landscape blend (runtime virtual texture) dropped: no landscape under the piece')
                return a
            if not isinstance(a, dict) or not isinstance(b, dict):
                raise Unsupported('BlendMaterialAttributes of a non-attribute value')
            mode = str(n.get('PixelAttributeBlendType') or 'Blend')
            if mode.endswith('UseA'):
                return a
            if mode.endswith('UseB'):
                return b
            try:
                al = I('Alpha')
            except NoLandscape:
                self.notes.add('landscape blend (runtime virtual texture) dropped: no landscape under the piece')
                return a
            if al is None:
                raise Unsupported('BlendMaterialAttributes without Alpha')
            out = {}
            for k in set(a) | set(b):
                x = a.get(k) or _const(PIN_DEFAULTS.get(k, (0.0,)), P)
                y = b.get(k) or _const(PIN_DEFAULTS.get(k, (0.0,)), P)
                xa, yb, d = _pair(x, y)
                out[k] = Val(xa + (yb - xa) * al.a[:, :1], d | al.deps)
            return out
        if c == 'GetMaterialAttributes':
            # output 0 passes the attributes on; output k is the attribute AttributeGetTypes[k - 1] names
            r = n.inp('MaterialAttributes')
            attrs = self.eval_ref(env, r) if r else {}
            if not isinstance(attrs, dict):
                raise Unsupported('GetMaterialAttributes of a non-attribute value')
            o = ref.get('out', 0)
            if o == 0:
                return attrs
            kinds = pg.read_guid_array(env.g.p, n.byname.get('AttributeGetTypes'))
            name = ATTRIBUTE_GUIDS.get(kinds[o - 1]) if o - 1 < len(kinds) else None
            v = attrs.get(name)
            if v is None and name in PIN_DEFAULTS:
                v = _const(PIN_DEFAULTS[name], P)
            return v
        if c == 'BreakMaterialAttributes':
            names = n.output_names() or (pg.BREAK_MA_OUTPUTS if env.g.engine >= pg.BREAK_MA_NEW_ENGINE
                                         else pg.BREAK_MA_OUTPUTS_OLD)
            o = ref.get('out', 0)
            attrs = self.eval_ref(env, n.inp('MaterialAttributes'))
            on = names[o] if o < len(names) else None
            if not isinstance(attrs, dict):
                raise Unsupported('BreakMaterialAttributes of a non-attribute value')
            v = attrs.get(on)
            if v is None and on in PIN_DEFAULTS:
                v = _const(PIN_DEFAULTS[on], P)
            return v
        if c == 'MaterialFunctionCall':
            return self.call(env, n, ref)
        if c == 'FunctionInput':
            nm = n.get('InputName')
            if nm in env.bindings:
                cenv, cref = env.bindings[nm]
                if cref:
                    return self.eval_ref(cenv, cref)
            pv = n.inp('Preview')
            if pv and (n.get('bUsePreviewValueAsDefault') or env.parent is None):
                return self.eval_ref(env, pv)
            it = str(n.get('InputType') or 'FunctionInput_Vector3')
            if 'StaticBool' in it:
                return bool((n.get('PreviewValue') or (0,))[0])
            width = {'Scalar': 1, 'Vector2': 2, 'Vector3': 3, 'Vector4': 4}.get(it.split('_')[-1], 3)
            return _const(tuple(n.get('PreviewValue') or (0.0, 0.0, 0.0, 0.0))[:width], P)
        if c == 'FunctionOutput':
            return self.eval_ref(env, n.inp('A'))
        raise Unsupported('expression %s' % c)

    def _uv(self, k):
        uvs = self.dom.get('uv') or []
        if k >= len(uvs):
            raise Unsupported('UV channel %d not in the domain' % k)
        return Val(uvs[k], {'uv%d' % k})

    @staticmethod
    def _pick(v, pick):
        if v is None:
            return None
        pick = [i for i in pick if i < v.c] or [0]
        return Val(v.a[:, pick], v.deps)

    def _switch(self, env, n, labels, want):
        r = None
        for k, ref in n.inputs():
            if k == 'Default':
                continue
            i = int(k[k.index('[') + 1:-1]) if '[' in k else 0
            if i < len(labels) and labels[i] == want:
                r = ref
        return self.eval_ref(env, r or n.inp('Default'))

    # -- textures -------------------------------------------------------------------------------

    def texture(self, env, n, ref):
        c = n.cls
        tex = pg.full_ref(env.g.p, n.get('Texture'))
        pname = n.get('ParameterName')
        sampler = n.get('SamplerType') or 'SAMPLERTYPE_Color'
        if pname and pname in self.params.textures:
            tex = self.params.textures[pname]
        to = n.inp('TextureObject')
        if to:
            obj = self.eval_ref(env, to)
            if isinstance(obj, TexRef):
                tex, pname = obj.path, obj.param
        if c in ('TextureObject', 'TextureObjectParameter'):
            return TexRef(tex, sampler, pname)
        if c not in ('TextureSample', 'TextureSampleParameter2D'):
            raise Unsupported('texture expression %s' % c)
        uvr = n.inp('Coordinates')
        coord = self.eval_ref(env, uvr) if uvr else self._uv(int(n.get('ConstCoordinate') or 0))
        val = self.sample(env.key + (n.idx,), tex, coord, sampler)
        o = ref.get('out', 0)
        if not ref.get('mask'):
            chans = TEX_OUTPUTS[o] if o < len(TEX_OUTPUTS) else 'rgba'
            return Val(val.a[:, [CH.index(x) for x in chans]], val.deps)
        return val

    def sample(self, key, tex, coord, sampler):
        """(P,4): the texture at coord. Colour samplers return linear values (sRGB decoded when the
        texture is sRGB); normal samplers return the unpacked tangent-space vector in rgb."""
        if self.samples is not None:
            self.samples.append({'key': key, 'texture': tex, 'coord': coord.a[:, :2].copy(), 'deps': coord.deps,
                                 'sampler': sampler})
        if key in self.override:
            return Val(np.broadcast_to(np.asarray(self.override[key], np.float32), (self.P, 4)).copy(), set())
        if not tex:
            raise Unsupported('texture sample with no texture')
        if coord.c < 2:
            raise Unsupported('texture coordinate narrower than 2')
        px = self.tex.sample(tex, coord.a[:, :2], sampler)
        if px is None:
            self.notes.add('texture %s is not in the pack: read as %.1f' % (tex.rsplit('/', 1)[-1], MISSING_TEXTURE))
            return Val(np.full((self.P, 4), MISSING_TEXTURE, np.float32), coord.deps)
        return Val(px, coord.deps)

    # -- functions ------------------------------------------------------------------------------

    def call(self, env, n, ref):
        mf = n.get('MaterialFunction')
        fpath = pg.full_ref(env.g.p, mf) if isinstance(mf, int) else None
        fname = pg.leafname(fpath) or '?'
        outs = (n.byname.get('FunctionOutputs') or {}).get('items', []) or []
        oi = ref.get('out', 0)
        oname = None
        if oi < len(outs):
            o = find(outs[oi], 'Output')
            on = pg.child(o, 'OutputName')
            oname = on.get('value') if on else None
        bindings = {}
        for it in (n.byname.get('FunctionInputs') or {}).get('items', []) or []:
            r = pg.read_input(env.g.p, find(it, 'Input'))
            if r:
                bindings[r['name']] = (env, r if r['expr'] else None)
        if fpath and fpath.startswith('/Game/'):
            g = self.pack.graph(fpath)
            if g is None:
                raise Unsupported('material function %s does not read' % fname)
            fenv = Env(g, bindings, env, env.key + ((fpath, n.idx),))
            target = None
            fos = g.function_outputs()
            for fo in fos:
                if fo.get('OutputName') == oname:
                    target = fo
            if target is None and oi < len(fos):
                target = fos[oi]
            if target is None:
                raise Unsupported('function %s has no output %s' % (fname, oname))
            return self.eval_ref(fenv, dict(expr=target.idx, out=0, mask=0, mr=0, mg=0, mb=0, ma=0))
        impl = ENGINE_FUNCTIONS.get(fname)
        if impl is None:
            raise Unsupported('engine function %s' % fname)

        def arg(name, default=None):
            b = bindings.get(name)
            if b and b[1]:
                return self.eval_ref(b[0], b[1])
            if default is None:
                return None
            if isinstance(default, bool):
                return default
            return _const(default, self.P)
        self.call_inputs = [k for k, b in bindings.items() if b and b[1]]   # what the caller wired in
        return impl(self, arg, oname, env.key + (('fn', n.idx),))


# -- engine functions, natively ---------------------------------------------------------------------
# Each is UE 4.27's function graph written out (Engine/Content/Functions/...). They take
# (evaluator, arg(name, default), output name, call key) and return a Val (or attributes dict).

def _cheap_contrast(x, c):
    return np.clip(-c + x * (1 + 2 * c), 0, 1)


def fn_cheap_contrast(ev, arg, out, key):
    v, c = arg('In', 0.0), arg('Contrast', 0.0)
    return Val(_cheap_contrast(v.a[:, :1], c.a[:, :1]), v.deps | c.deps)


def fn_cheap_contrast_rgb(ev, arg, out, key):
    v, c = arg('In', 0.0), arg('Contrast', 0.0)
    return Val(_cheap_contrast(v.a[:, :3], c.a[:, :1]), v.deps | c.deps)


def fn_hue_shift(ev, arg, out, key):
    """HueShift: the colour rotated about the grey axis by percent x 360 degrees (Rodrigues);
    the rule approved in round mat2 (sample X2)."""
    p, t = arg('Hue Shift Percentage', 0.0), arg('Texture', (0.0, 0.0, 0.0))
    k = np.ones(3, np.float32) / math.sqrt(3)
    a = (2 * math.pi * p.a[:, :1]).astype(np.float32)
    c = t.a[:, :3]
    r = c * np.cos(a) + np.cross(k, c) * np.sin(a) + (c @ k)[:, None] * k[None, :] * (1 - np.cos(a))
    return Val(r, p.deps | t.deps)


def fn_flatten_normal(ev, arg, out, key):
    nrm, f = arg('Normal', (0.0, 0.0, 1.0)), arg('Flatness', 0.0)
    a = nrm.a[:, :3]
    return Val(a + (np.array([0, 0, 1], np.float32) - a) * f.a[:, :1], nrm.deps | f.deps)


def fn_height_lerp(ev, arg, out, key):
    a, b = arg('A', 0.0), arg('B', 1.0)
    tp, h, c = arg('Transition Phase', 0.5), arg('Height Texture', 0.5), arg('Contrast', 0.0)
    raw = np.clip((h.a[:, :1] - 1) + tp.a[:, :1] * 2, 0, 1)
    d = tp.deps | h.deps | c.deps
    if out == 'Lerp Alpha No Contrast':
        return Val(raw, d)
    al = _cheap_contrast(raw, c.a[:, :1])
    if out == 'Alpha':
        return Val(al, d)
    x, y, dd = _pair(a, b)
    return Val(x + (y - x) * al, d | dd)


def fn_world_aligned_texture(ev, arg, out, key):
    """WorldAlignedTexture: the texture projected on the world plane the face looks along,
    at TextureSize uu per repeat. The bake domain carries that planar coordinate per face
    ('wuv', sign included), so the three projections become one sample of wuv / size."""
    t = arg('TextureObject')
    if not isinstance(t, TexRef):
        raise Unsupported('WorldAlignedTexture without a texture object')
    size = arg('TextureSize', (1.0, 1.0, 1.0))
    w = ev.dom.get('wuv')
    if w is None:
        raise Unsupported('world-aligned texture outside a world-planar domain')
    s = np.abs(size.a[:, :2])
    if np.ptp(size.a[:, :3]) > 1e-3 * max(1.0, float(np.abs(size.a).max())):
        ev.notes.add('WorldAlignedTexture size not uniform: per-axis sizes collapsed onto one plane')
    coord = Val(w / np.maximum(s, 1e-6), {'wuv'} | size.deps)
    ev.notes.add('world-aligned (triplanar) texture rewritten as planar UVs per face')
    v = ev.sample(key, t.path, coord, t.sampler)
    if out == 'Z Texture':
        ev.notes.add('WorldAlignedTexture top-only output applied to every face')
    exp4 = arg('Export Float 4', False)
    return Val(v.a if exp4 is True else v.a[:, :3], v.deps)


def fn_world_aligned_normal(ev, arg, out, key):
    return fn_world_aligned_texture(ev, arg, 'XYZ Texture', key)


def fn_mat_layer_blend_standard(ev, arg, out, key):
    base, top, al = arg('Base Material'), arg('Top Material'), arg('Alpha', 0.5)
    if not isinstance(base, dict):
        base = {}
    if not isinstance(top, dict):
        top = {}
    outd = {}
    for k in set(base) | set(top):
        x = base.get(k) or _const(PIN_DEFAULTS.get(k, (0.0,)), ev.P)
        y = top.get(k) or _const(PIN_DEFAULTS.get(k, (0.0,)), ev.P)
        a, b, d = _pair(x, y)
        outd[k] = Val(a + (b - a) * al.a[:, :1], d | al.deps)
    return outd


def fn_blend_angle_corrected_normals(ev, arg, out, key):
    b, a = arg('BaseNormal', (0.0, 0.0, 1.0)), arg('AdditionalNormal', (0.0, 0.0, 1.0))
    bn = b.a[:, :3] + np.array([0, 0, 1], np.float32)
    an = a.a[:, :3] * np.array([-1, -1, 1], np.float32)
    r = bn * (bn * an).sum(1, keepdims=True) / np.maximum(bn[:, 2:3], 1e-6) - an
    return Val(r, a.deps | b.deps)


def _blend(fn):
    def f(ev, arg, out, key):
        b, l = arg('Base', 0.0), arg('Blend', 0.0)
        x, y, d = _pair(b, l)
        return Val(fn(x, y), d)
    return f


def _overlay(base, blend):
    return np.where(base > 0.5, 1 - 2 * (1 - base) * (1 - blend), 2 * base * blend)


def _split(ch):
    def f(ev, arg, out, key):
        v = None
        for name in ('Color', 'Float4', 'Float3', 'Float2', 'Input', 'In', 'Vector'):
            v = arg(name)
            if v is not None:
                break
        if not isinstance(v, Val):
            raise Unsupported('split without input')
        i = {'R': 0, 'G': 1, 'B': 2, 'A': 3}.get(out, 0)
        i = min(i, v.c - 1)
        return Val(v.a[:, i:i + 1], v.deps)
    return f


def fn_make_float3(ev, arg, out, key):
    x, y, z = arg('X', 0.0), arg('Y', 0.0), arg('Z', 0.0)
    return Val(np.concatenate([x.a[:, :1], y.a[:, :1], z.a[:, :1]], 1), x.deps | y.deps | z.deps)


def fn_make_float2(ev, arg, out, key):
    x, y = arg('X', 0.0), arg('Y', 0.0)
    return Val(np.concatenate([x.a[:, :1], y.a[:, :1]], 1), x.deps | y.deps)


def fn_world_aligned_blend(ev, arg, out, key):
    """WorldAlignedBlend: a top-down coverage mask from the world normal,
    saturate(dot(N, up) x sharpness + bias) shape. Baked per texel against the face normal."""
    ev.notes.add('WorldAlignedBlend coverage baked from the face normal (approximate shape)')
    sharp, bias = arg('Blend Sharpness', 0.0), arg('Blend Bias', 0.0)
    nz = ev.dom['normal'][:, 2:3]
    r = np.clip((nz + bias.a[:, :1] / 1.0) * (1 + sharp.a[:, :1]), 0, 1)
    return Val(r, {'normal'} | sharp.deps | bias.deps)


def fn_luminosity_and_color(ev, arg, out, key):
    v = arg('Color', (0.0, 0.0, 0.0)) or arg('In', (0.0, 0.0, 0.0))
    lum = (v.a[:, :3] * np.array([0.3, 0.59, 0.11], np.float32)).sum(1, keepdims=True)
    return Val(lum if out and 'umin' in out else v.a[:, :3], v.deps)


def fn_wpo_only(ev, arg, out, key):
    """Vertex-animation functions (wind, sway): no pixel contribution; motion is dropped."""
    ev.notes.add('vertex animation (wind / sway) dropped: static')
    return _const((0.0, 0.0, 0.0), ev.P)


def fn_screen_aligned_uvs(ev, arg, out, key):
    """Screen-space UVs: no screen offline; read as the screen centre."""
    ev.notes.add('screen-space UVs (ScreenAlignedUVs) read as the screen centre: view-dependent')
    return _const((0.5, 0.5), ev.P)


def fn_dither(ev, arg, out, key):
    ev.notes.add('DitherTemporalAA read as its alpha threshold input')
    return arg('Alpha Threshold', 0.5)


#: Input names WorldPositionWithScale's scale may carry (the engine asset's header layout is not
#: one pkgread reads; its graph is WorldPosition / FunctionInput -> ComponentMask -> outputs).
WPS_SCALE_INPUTS = ('Scale', 'World Scale', 'WorldScale', 'Size', 'Texture Size', 'UV Scale')


def fn_world_position_with_scale(ev, arg, out, key):
    """Engine WorldPositionWithScale: the world position divided by a scale, masked to the
    components its output names (XY, XZ, YZ, or all three). World position is the per-face
    world-planar coordinate this domain carries, as for the WorldPosition node."""
    w = ev.dom.get('wuv')
    if w is None:
        raise Unsupported('world position not in the domain')
    ev.notes.add('world position read directly: baked as a top-down (Z) projection')
    pos = np.concatenate([-w, np.zeros((ev.P, 1), np.float32)], 1)
    scale = None
    wired = list(getattr(ev, 'call_inputs', []))
    for name in list(WPS_SCALE_INPUTS) + wired:      # its only input, whatever it is called
        scale = arg(name)
        if scale is not None:
            break
    deps = {'wuv'}
    if scale is not None:
        sc = scale.a[:, :3] if scale.c >= 3 else np.repeat(scale.a[:, :1], 3, 1)
        pos = pos / np.where(np.abs(sc) < 1e-6, 1.0, sc)
        deps |= scale.deps
    else:
        ev.notes.add('WorldPositionWithScale: no scale input bound, scale 1')
    letters = ''.join(ch for ch in (out or '').upper() if ch in 'XYZ')
    if len(letters) in (2, 3):
        idx = ['XYZ'.index(ch) for ch in letters]
        return Val(pos[:, idx], deps)
    return Val(pos, deps)


def fn_custom_rotator(ev, arg, out, key):
    """Engine CustomRotator: UVs rotated about Rotation Center by Rotation Angle (0-1) turns
    (Engine_MaterialFunctions02/Texturing/CustomRotator)."""
    uv = arg('UVs')
    if uv is None:
        raise Unsupported('CustomRotator without UVs')
    c = arg('Rotation Center', (0.5, 0.5))
    a = arg('Rotation Angle (0-1)', 0.0)
    ang = 2 * math.pi * a.a[:, :1]
    u, v = uv.a[:, 0:1] - c.a[:, 0:1], uv.a[:, 1:2] - c.a[:, 1:2]
    ca, sa = np.cos(ang), np.sin(ang)
    rot = np.concatenate([u * ca - v * sa + c.a[:, 0:1], u * sa + v * ca + c.a[:, 1:2]], 1)
    return Val(rot.astype(np.float32), uv.deps | c.deps | a.deps)


def fn_detail_texturing(ev, arg, out, key):
    """Engine DetailTexturing (Engine_MaterialFunctions01/Texturing): DetailDiffuse and
    DetailNormal sampled at UV0 x Scale; Diffuse = clamp(Diffuse x lerp(1, 0.5 + detail,
    DiffuseIntensity)), Normal = detail normal x (NormalIntensity, NormalIntensity, 0) + Normal."""
    scale = arg('Scale', 2.0)
    uvs = ev.dom.get('uv') or []
    if not uvs:
        raise Unsupported('UV channel 0 not in the domain')
    coord = Val(uvs[0] * scale.a[:, :1], {'uv0'} | scale.deps)
    if out == 'Normal':
        t = arg('DetailNormal')
        if not isinstance(t, TexRef):
            raise Unsupported('DetailTexturing without a detail normal texture')
        dn = ev.sample(key + ('DetailNormal',), t.path, coord, 'SAMPLERTYPE_Normal')
        k = arg('NormalIntensity', 1.0)
        base = arg('Normal', (0.0, 0.0, 1.0))
        w = np.concatenate([k.a[:, :1], k.a[:, :1], np.zeros((ev.P, 1), np.float32)], 1)
        return Val(dn.a[:, :3] * w + base.a[:, :3], dn.deps | k.deps | base.deps)
    t = arg('DetailDiffuse')
    if not isinstance(t, TexRef):
        raise Unsupported('DetailTexturing without a detail diffuse texture')
    dd = ev.sample(key + ('DetailDiffuse',), t.path, coord, 'SAMPLERTYPE_Color')
    diffuse = arg('Diffuse', (0.5, 0.5, 0.5))
    k = arg('DiffuseIntensity', 1.0)
    detail = 1.0 + (0.5 + dd.a[:, :3] - 1.0) * k.a[:, :1]
    return Val(np.clip(diffuse.a[:, :3] * detail, 0, 1), dd.deps | diffuse.deps | k.deps)


def fn_fuzzy_shading(ev, arg, out, key):
    """Engine FuzzyShading (Engine_MaterialFunctions01/Shading): BaseColor x (1 - t x CoreDarkness
    + (1 - t)^Power x EdgeBrightness), t = clamp(camera . normal). Viewed head-on (the camera
    vector is the surface normal), t is the tangent-space normal's z."""
    ev.notes.add('FuzzyShading evaluated head-on (view-dependent)')
    n = arg('Normal', (0.0, 0.0, 1.0))
    t = np.clip(n.a[:, 2:3] if n.c >= 3 else np.ones((ev.P, 1), np.float32), 0, 1)
    core, edge, power = arg('CoreDarkness', 0.8), arg('EdgeBrightness', 0.8), arg('Power', 6.0)
    base = arg('BaseColor', (0.5, 0.5, 0.5))
    shade = 1 - t * core.a[:, :1] + np.power(1 - t, power.a[:, :1]) * edge.a[:, :1]
    return Val(base.a[:, :3] * shade, n.deps | core.deps | edge.deps | power.deps | base.deps)


def fn_object_scale(ev, arg, out, key):
    """Engine ObjectScale (Engine_MaterialFunctions02/WorldPositionOffset): the length of each
    local axis in world space; 1 for a piece placed at actor scale 1 (pack scale is in the mesh)."""
    return _const((1.0, 1.0, 1.0) if out == 'Scale XYZ' or not out else (1.0,), ev.P)


def fn_linear_gradient(ev, arg, out, key):
    """Engine LinearGradient (Engine_MaterialFunctions01/Gradient): UGradient / VGradient are the
    U and V of its UV Channel input, UV0 when unwired."""
    uv = arg('UV Channel')
    if uv is None:
        uvs = ev.dom.get('uv') or []
        if not uvs:
            raise Unsupported('UV channel 0 not in the domain')
        uv = Val(uvs[0], {'uv0'})
    i = 1 if out == 'VGradient' else 0
    return Val(uv.a[:, i:i + 1], uv.deps)


def fn_multiply_add(ev, arg, out, key):
    """Engine MultiplyAdd (Engine_MaterialFunctions02/Math), as its graph is wired:
    Add x Base + Base."""
    base, add = arg('Base', 0.0), arg('Add', 0.0)
    a, b, d = _pair(add, base)
    return Val(a * b + b, d)


def fn_parallax_occlusion_mapping(ev, arg, out, key):
    """Engine ParallaxOcclusionMapping (Engine_MaterialFunctions01/Texturing) viewed head-on: the
    tangent-space view ray has no sideways part, so the parallax offset is 0 (Parallax UVs = UVs)
    and the surface is unshadowed."""
    ev.notes.add('ParallaxOcclusionMapping evaluated head-on (view-dependent): no offset')
    if out == 'Offset Only':
        return _const((0.0, 0.0), ev.P)
    if out in ('Shadow',):
        return _const((1.0,), ev.P)
    if out == 'Pixel Depth Offset':
        return _const((0.0,), ev.P)
    if out != 'Parallax UVs':
        raise Unsupported('ParallaxOcclusionMapping output %s' % out)
    uv = arg('UVs')
    if uv is None:
        uvs = ev.dom.get('uv') or []
        if not uvs:
            raise Unsupported('UV channel 0 not in the domain')
        uv = Val(uvs[0], {'uv0'})
    return Val(uv.a[:, :2], uv.deps)


def fn_normal_from_heightmap(ev, arg, out, key):
    """Engine NormalFromHeightmap (Engine_MaterialFunctions01/Texturing): heights h0, hU, hV
    (channel-selected samples at the UV and one UV offset along U and V); dU = (hU - h0) x I,
    dV = (hV - h0) x I; Normal = cross((1, 0, dU), (0, 1, dV)) = (-dU, -dV, 1)."""
    t = arg('Height Map')
    if not isinstance(t, TexRef):
        raise Unsupported('NormalFromHeightmap without a height map texture')
    uv = arg('Coordinates')
    if uv is None:
        uvs = ev.dom.get('uv') or []
        if not uvs:
            raise Unsupported('UV channel 0 not in the domain')
        uv = Val(uvs[0], {'uv0'})
    sel = arg('Height Map Channel Selector', (1.0, 0.0, 0.0, 0.0))
    off = arg('Height Map UV Offset', 0.005)
    k = arg('Normal Map Intensity', 8.0)
    ch = np.zeros((ev.P, 4), np.float32)
    ch[:, :sel.c] = sel.a[:, :4]
    o = off.a[:, :1]
    zero = np.zeros_like(o)
    heights = []
    for name, d in (('h0', np.concatenate([zero, zero], 1)), ('hU', np.concatenate([o, zero], 1)),
                    ('hV', np.concatenate([zero, o], 1))):
        s = ev.sample(key + (name,), t.path, Val(uv.a[:, :2] + d, uv.deps | off.deps), 'SAMPLERTYPE_Color')
        heights.append((s.a[:, :4] * ch).sum(1, keepdims=True))
    du = (heights[1] - heights[0]) * k.a[:, :1]
    dv = (heights[2] - heights[0]) * k.a[:, :1]
    deps = uv.deps | off.deps | sel.deps | k.deps
    return Val(np.concatenate([-du, -dv, np.ones_like(du)], 1).astype(np.float32), deps)


def fn_local_position(ev, arg, out, key):
    """Engine LocalPosition (Engine_MaterialFunctions02/WorldPositionOffset): the world position
    transformed to local space; one space for a piece at the origin at scale 1, so it reads as
    the WorldPosition node does."""
    w = ev.dom.get('wuv')
    if w is None:
        raise Unsupported('world position not in the domain')
    ev.notes.add('world position read directly: baked as a top-down (Z) projection')
    return Val(np.concatenate([-w, np.zeros((ev.P, 1), np.float32)], 1), {'wuv'})


def fn_bump_offset_advanced(ev, arg, out, key):
    """Engine BumpOffset_Advanced (Engine_MaterialFunctions01/Texturing) viewed head-on: the
    camera vector has no part along the surface, so the distortion is 0."""
    ev.notes.add('BumpOffset_Advanced evaluated head-on (view-dependent): no offset')
    if out == 'Distortion':
        return _const((0.0, 0.0), ev.P)
    uv = arg('TextureCoordinates')
    if uv is None:
        uvs = ev.dom.get('uv') or []
        if not uvs:
            raise Unsupported('UV channel 0 not in the domain')
        uv = Val(uvs[0], {'uv0'})
    return Val(uv.a[:, :2], uv.deps)


def fn_camera_direction_vector(ev, arg, out, key):
    """Engine CameraDirectionVector (Engine_MaterialFunctions02/Utility): the camera's forward
    axis in world space. Viewed head-on the camera looks against the face normal."""
    ev.notes.add('CameraDirectionVector evaluated head-on (view-dependent)')
    return Val(-ev.dom['normal'], {'normal'})


def fn_add_components(ev, arg, out, key):
    """Engine AddComponents (Engine_MaterialFunctions02/Math): f2 = r + g, f3 = r + g + b,
    f4 = r + g + b + a of the input of the same name."""
    name = out if out in ('f2', 'f3', 'f4') else 'f3'
    v = arg(name)
    if v is None:
        raise Unsupported('AddComponents %s not wired' % name)
    n = min(int(name[1]), v.c)
    return Val(v.a[:, :n].sum(1, keepdims=True), v.deps)


ENGINE_FUNCTIONS = {
    'CameraDirectionVector': fn_camera_direction_vector,
    'AddComponents': fn_add_components,
    'BumpOffset_Advanced': fn_bump_offset_advanced,
    'LocalPosition': fn_local_position,
    'NormalFromHeightmap': fn_normal_from_heightmap,
    'MultiplyAdd': fn_multiply_add,
    'ParallaxOcclusionMapping': fn_parallax_occlusion_mapping,
    'LinearGradient': fn_linear_gradient,
    'DetailTexturing': fn_detail_texturing,
    'FuzzyShading': fn_fuzzy_shading,
    'ObjectScale': fn_object_scale,
    'CustomRotator': fn_custom_rotator,
    'WorldPositionWithScale': fn_world_position_with_scale,
    'CheapContrast': fn_cheap_contrast,
    'CheapContrast_RGB': fn_cheap_contrast_rgb,
    'HueShift': fn_hue_shift,
    'FlattenNormal': fn_flatten_normal,
    'HeightLerp': fn_height_lerp,
    'WorldAlignedTexture': fn_world_aligned_texture,
    'WorldAlignedNormal': fn_world_aligned_normal,
    'MatLayerBlend_Standard': fn_mat_layer_blend_standard,
    'BlendAngleCorrectedNormals': fn_blend_angle_corrected_normals,
    'Blend_Overlay': _blend(_overlay),
    'Blend_Screen': _blend(lambda a, b: 1 - (1 - a) * (1 - b)),
    'Blend_Multiply': _blend(lambda a, b: a * b),
    'Blend_Darken': _blend(np.minimum),
    'Blend_Lighten': _blend(np.maximum),
    'Blend_Difference': _blend(lambda a, b: np.abs(a - b)),
    'Blend_LinearDodge': _blend(lambda a, b: np.clip(a + b, 0, 1)),
    'Blend_LinearBurn': _blend(lambda a, b: np.clip(a + b - 1, 0, 1)),
    'Blend_SoftLight': _blend(lambda a, b: np.where(b > 0.5, 1 - (1 - a) * (1 - (b - 0.5)),
                                                    a * (b + 0.5))),
    'SplitComponents': _split(None),
    'BreakOutFloat2Components': _split(None),
    'BreakOutFloat3Components': _split(None),
    'BreakOutFloat4Components': _split(None),
    'MakeFloat3': fn_make_float3,
    'MakeFloat2': fn_make_float2,
    'WorldAlignedBlend': fn_world_aligned_blend,
    'Luminosity_And_Color': fn_luminosity_and_color,
    'SimpleGrassWind': fn_wpo_only,
    'PivotPainter2FoliageShader': fn_wpo_only,
    'DitherTemporalAA': fn_dither,
    'ScreenAlignedUVs': fn_screen_aligned_uvs,
}
