# Generated from OMD2-Converter/thirdparty/material-tools/packgraph.py by thirdparty/fabconvert/port_riftlord.py; edit the source.
"""Decode the editor material graphs of one Fab pack (read-only).

python3 -I packgraph.py <pack_root_dir> <out.json>

<pack_root_dir> is a folder directly under a project's Content folder, e.g.
.../data/Content/Medieval_Dock. Writes, per Material and MaterialFunction in the pack: header,
parameters, one formula per output pin, measured facts (texture samples with channels and UVs,
vertex colour, world position, functions), derived feature tags; per MaterialInstanceConstant its
overrides; and a pack summary with channel-packing patterns and decoder coverage.
"""
import sys, os, glob, json, struct, collections, time

HERE = os.path.dirname(os.path.abspath(__file__))
SURVEY = os.path.join(os.path.dirname(HERE), 'survey-tools')
sys.path.insert(0, SURVEY)
from riftlord.fab.survey import pkgread                                  # noqa: E402
from riftlord.fab.survey.pkgread import find                        # noqa: E402
from riftlord.fab.survey.inventory import safe_props, child, main_export   # noqa: E402

GENERATED_BY = 'thirdparty/material-tools/packgraph.py'

# Recursion depth cap for one expression tree.
MAX_DEPTH = 48
# Node visits allowed per output pin before the walk stops expanding.
MAX_VISITS_PER_PIN = 6000
# Formula strings longer than this are cut and end with ELLIPSIS.
MAX_FORMULA_CHARS = 3000
ELLIPSIS = '...'
# Characters kept of a lerp operand when describing what vertex colour blends.
CONTEXT_CHARS = 160
# Number format for constants in formulas.
NUM_FMT = '%.4g'
# Two tiling factors closer than this count as the same UV scale.
UV_SCALE_EPS = 1e-4

# Byte size of a natively serialized FExpressionInput (Expression, OutputIndex, InputName FName, 5 masks).
EXPR_INPUT_SIZE = 36
# Material output pins in display order.
PINS = ['MaterialAttributes', 'BaseColor', 'Metallic', 'Specular', 'Roughness', 'Anisotropy', 'EmissiveColor',
        'Opacity', 'OpacityMask', 'Normal', 'Tangent', 'WorldPositionOffset', 'WorldDisplacement',
        'TessellationMultiplier', 'SubsurfaceColor', 'CustomData0', 'CustomData1', 'ClearCoat',
        'ClearCoatRoughness', 'AmbientOcclusion', 'Refraction', 'PixelDepthOffset',
        'ShadingModelFromMaterialExpression'] + ['CustomizedUVs%d' % i for i in range(8)]
# Summary pins shown in the one-line formula summary.
SUMMARY_PINS = ['BaseColor', 'Normal', 'Roughness', 'Metallic', 'Specular', 'AmbientOcclusion', 'EmissiveColor',
                'Opacity', 'OpacityMask', 'WorldPositionOffset']
# Material header properties and their engine defaults (a tagged property is absent when default).
HEADER_DEFAULTS = {'BlendMode': 'BLEND_Opaque', 'TwoSided': False, 'ShadingModel': 'MSM_DefaultLit',
                   'MaterialDomain': 'MD_Surface', 'OpacityMaskClipValue': 0.3333,
                   'bUseMaterialAttributes': False, 'DitheredLODTransition': False,
                   'TranslucencyLightingMode': 'TLM_VolumetricNonDirectional', 'bTangentSpaceNormal': True}
# FMaterialInstanceBasePropertyOverrides values and defaults; a value applies only when its bOverride_ flag is set.
OVERRIDE_DEFAULTS = {'OpacityMaskClipValue': 0.3333, 'BlendMode': 'BLEND_Opaque', 'ShadingModel': 'MSM_DefaultLit',
                     'TwoSided': False, 'DitheredLODTransition': False, 'bCastDynamicShadowAsMasked': False}
# Texture asset properties read for sRGB/compression, with engine defaults.
TEXTURE_DEFAULTS = {'SRGB': True, 'CompressionSettings': 'TC_Default', 'LODGroup': 'TEXTUREGROUP_World'}

# Per-class defaults for constant operands used when an input pin is unconnected.
CONST_DEFAULTS = {
    'Multiply': {'ConstA': 0.0, 'ConstB': 1.0}, 'Add': {'ConstA': 0.0, 'ConstB': 1.0},
    'Subtract': {'ConstA': 1.0, 'ConstB': 1.0}, 'Divide': {'ConstA': 1.0, 'ConstB': 2.0},
    'Max': {'ConstA': 0.0, 'ConstB': 1.0}, 'Min': {'ConstA': 0.0, 'ConstB': 1.0},
    'LinearInterpolate': {'ConstA': 0.0, 'ConstB': 1.0, 'ConstAlpha': 0.5},
    'Power': {'ConstExponent': 2.0}, 'Clamp': {'MinDefault': 0.0, 'MaxDefault': 1.0},
    'TextureCoordinate': {'UTiling': 1.0, 'VTiling': 1.0, 'CoordinateIndex': 0},
    'Panner': {'SpeedX': 0.0, 'SpeedY': 0.0, 'ConstCoordinate': 0},
    'Rotator': {'CenterX': 0.5, 'CenterY': 0.5, 'Speed': 0.25},
    'Fresnel': {'Exponent': 5.0, 'BaseReflectFraction': 0.04},
    'DepthFade': {'FadeDistanceDefault': 100.0, 'OpacityDefault': 1.0},
    'ConstantBiasScale': {'Bias': 1.0, 'Scale': 0.5}, 'Sine': {'Period': 1.0}, 'Cosine': {'Period': 1.0},
    'SphereMask': {'AttenuationRadius': 256.0, 'HardnessPercent': 100.0},
    'If': {'ConstB': 0.0, 'EqualsThreshold': 0.00001},
    'ScalarParameter': {'DefaultValue': 0.0}, 'Constant': {'R': 0.0},
    'StaticSwitchParameter': {'DefaultValue': False}, 'StaticBoolParameter': {'DefaultValue': False},
    'StaticSwitch': {'DefaultValue': False}, 'Desaturation': {},
    'FunctionOutput': {'OutputName': 'Result'}, 'FunctionInput': {'InputName': 'In'},
}
# Expression classes that carry no graph meaning.
SKIP_CLASSES = {'Comment'}
# Editor-only expression properties left out of generic formulas.
EDITOR_PROPS = {'MaterialExpressionEditorX', 'MaterialExpressionEditorY', 'MaterialExpressionGuid', 'Material',
                'Function', 'ExpressionGUID', 'Desc', 'bCollapsed', 'Outputs', 'bRealtimePreview',
                'bNeedToUpdatePreview', 'bShowOutputNameOnPin', 'bHidePreviewWindow', 'bShowMaskColorsOnPin',
                'bCommentBubbleVisible', 'SortPriority', 'Group', 'ParameterName', 'Description'}
# Texture expression classes.
TEXTURE_CLASSES = {'TextureSample', 'TextureSampleParameter2D', 'TextureObject', 'TextureObjectParameter',
                   'TextureSampleParameterCube', 'TextureSampleParameter2DArray', 'TextureSampleParameterVolume',
                   'TextureSampleParameterSubUV'}
# Parameter expression classes listed in the parameter table.
PARAM_CLASSES = {'ScalarParameter', 'VectorParameter', 'TextureSampleParameter2D', 'TextureObjectParameter',
                 'TextureSampleParameterCube', 'StaticSwitchParameter', 'StaticBoolParameter',
                 'StaticComponentMaskParameter', 'TextureSampleParameterSubUV', 'CollectionParameter',
                 'FontSampleParameter'}
# Classes with a dedicated formula handler (everything else falls back to the generic printer).
HANDLED = {'Multiply', 'Add', 'Subtract', 'Divide', 'Max', 'Min', 'LinearInterpolate', 'Power', 'Clamp',
           'OneMinus', 'Abs', 'Saturate', 'Sine', 'Cosine', 'Normalize', 'Floor', 'Ceil', 'Frac', 'SquareRoot',
           'Constant', 'Constant2Vector', 'Constant3Vector', 'Constant4Vector', 'ComponentMask',
           'AppendVector', 'TextureCoordinate', 'Panner', 'Rotator', 'WorldPosition', 'VertexColor', 'Fresnel',
           'StaticSwitchParameter', 'StaticSwitch', 'StaticBoolParameter', 'StaticBool',
           'StaticComponentMaskParameter', 'MaterialFunctionCall', 'FunctionInput', 'FunctionOutput', 'Reroute',
           'NamedRerouteUsage', 'NamedRerouteDeclaration', 'Time', 'Desaturation', 'ConstantBiasScale', 'If',
           'ScalarParameter', 'VectorParameter', 'MakeMaterialAttributes', 'BreakMaterialAttributes',
           'LandscapeLayerBlend', 'LandscapeLayerCoords', 'LandscapeLayerSample', 'LandscapeLayerWeight',
           'DotProduct', 'CrossProduct', 'Distance', 'DepthFade', 'SphereMask', 'Transform',
           'CameraPositionWS', 'ObjectPositionWS', 'VertexNormalWS', 'PixelNormalWS', 'ParticleColor',
           'DynamicParameter', 'TwoSidedSign', 'PixelDepth', 'SceneDepth', 'PerInstanceFadeAmount',
           'PrecomputedAOMask', 'FeatureLevelSwitch', 'QualitySwitch', 'SceneTexture', 'Noise',
           'DistanceToNearestSurface', 'DistanceFieldGradient', 'ObjectRadius', 'ObjectBounds',
           'CameraVectorWS', 'ReflectionVectorWS', 'ScreenPosition', 'PixelNormalWS', 'SpeedTree',
           'LandscapeGrassOutput'} | TEXTURE_CLASSES
# Nodes whose output is one float (a component selection does not apply to them).
SCALAR_LEAVES = {'Constant', 'ScalarParameter', 'Time', 'PixelDepth', 'PerInstanceFadeAmount', 'TwoSidedSign',
                 'PrecomputedAOMask', 'ObjectRadius', 'SceneDepth', 'DepthFade', 'Fresnel', 'StaticBoolParameter',
                 'StaticBool', 'DistanceToNearestSurface', 'Distance', 'DotProduct', 'SphereMask'}
# Output pins that take one float: a wider value is truncated to its first component by the material compiler.
SCALAR_PINS = {'Metallic', 'Specular', 'Roughness', 'Anisotropy', 'Opacity', 'OpacityMask', 'AmbientOcclusion',
               'PixelDepthOffset', 'TessellationMultiplier', 'CustomData0', 'CustomData1', 'ClearCoat',
               'ClearCoatRoughness'}
# Index names of FeatureLevelSwitch.Inputs (ERHIFeatureLevel) and QualitySwitch.Inputs (EMaterialQualityLevel).
FEATURE_LEVELS = ['ES2', 'ES3_1', 'SM4', 'SM5']
QUALITY_LEVELS = ['Low', 'High', 'Medium', 'Epic']
# Engine functions that only split a vector: output name -> channel it selects from the single input.
FN_SPLIT = {'SplitComponents': {'R': 'r', 'G': 'g', 'B': 'b', 'A': 'a'},
            'BreakOutFloat2Components': {'R': 'r', 'G': 'g'},
            'BreakOutFloat3Components': {'R': 'r', 'G': 'g', 'B': 'b'},
            'BreakOutFloat4Components': {'R': 'r', 'G': 'g', 'B': 'b', 'A': 'a'}}
# Channels a texture object reaches through a sampling engine function output, by output name.
FN_OUTPUT_CHANNELS = {'XYZ Texture': 'rgba', 'XY Texture': 'rgba', 'Z Texture': 'rgba'}
# Function input names (lower-case fragments) whose value acts as a blend mask, not as surface data.
FN_MASK_INPUT_WORDS = ('alpha', 'height', 'transition', 'mask', 'weight', 'vertex color', 'sharpness')
# Formula text for an unconnected function input or material attribute.
UNSET = 'unset'
# BreakMaterialAttributes output order when the package does not store output names (UE 4.27 order).
BREAK_MA_OUTPUTS = ['BaseColor', 'Metallic', 'Specular', 'Roughness', 'Anisotropy', 'EmissiveColor', 'Opacity',
                    'OpacityMask', 'Normal', 'Tangent', 'WorldPositionOffset', 'WorldDisplacement',
                    'TessellationMultiplier', 'SubsurfaceColor', 'ClearCoat', 'ClearCoatRoughness',
                    'AmbientOcclusion', 'Refraction'] + ['CustomizedUVs%d' % i for i in range(8)] + \
                   ['PixelDepthOffset', 'ShadingModel']
# The same list before Anisotropy and Tangent were inserted (packages saved by an engine older than BREAK_MA_NEW_ENGINE).
BREAK_MA_OUTPUTS_OLD = [k for k in BREAK_MA_OUTPUTS if k not in ('Anisotropy', 'Tangent', 'ShadingModel')]
BREAK_MA_NEW_ENGINE = (4, 25)
# Short pin names used in slot labels.
PIN_SHORT = {'BaseColor': 'BC', 'Normal': 'N', 'Roughness': 'Rough', 'Metallic': 'Metal', 'AmbientOcclusion': 'AO',
             'EmissiveColor': 'Emis', 'Opacity': 'Op', 'OpacityMask': 'OpMask', 'Specular': 'Spec',
             'WorldPositionOffset': 'WPO', 'SubsurfaceColor': 'SSS', 'MaterialAttributes': 'MA',
             'Refraction': 'Refr', 'PixelDepthOffset': 'PDO'}
# Simple binary operators and their infix symbol.
BINOPS = {'Multiply': '*', 'Add': '+', 'Subtract': '-', 'Divide': '/'}
# Single-input math functions (input property 'Input').
UNARY = {'OneMinus': '1-', 'Abs': 'abs', 'Saturate': 'saturate', 'Normalize': 'normalize', 'Floor': 'floor',
         'Ceil': 'ceil', 'Frac': 'frac', 'SquareRoot': 'sqrt'}
# Leaf nodes printed by a fixed name.
LEAVES = {'Time': 'Time', 'CameraPositionWS': 'CameraPos', 'ObjectPositionWS': 'ObjectPos',
          'VertexNormalWS': 'VertexNormalWS', 'PixelNormalWS': 'PixelNormalWS', 'ParticleColor': 'ParticleColor',
          'DynamicParameter': 'DynamicParam', 'TwoSidedSign': 'TwoSidedSign', 'PixelDepth': 'PixelDepth',
          'PerInstanceFadeAmount': 'PerInstanceFade', 'PrecomputedAOMask': 'PrecomputedAOMask',
          'ObjectRadius': 'ObjectRadius', 'ObjectBounds': 'ObjectBounds', 'CameraVectorWS': 'CameraVector',
          'ReflectionVectorWS': 'ReflectionVector', 'ScreenPosition': 'ScreenPos',
          'DistanceFieldGradient': 'DFGradient', 'SpeedTree': 'SpeedTree'}

# Engine function name fragments that mark a feature (lower-case match on the function name).
FN_WORLD_ALIGNED = ('worldalignedtexture', 'worldalignednormal', 'worldalignedblend', 'triplanar',
                    'worldpositionwithscale')
FN_WIND = ('simplegrasswind', 'pivotpainter', 'wind', 'rotateaboutaxis', 'simplegrass')
FN_HUE = ('hueshift',)
FN_COLOUR_ADJUST = ('cheapcontrast', 'luminosity_and_color', 'desaturation', 'colorcorrection')
# Engine colour-blend family (function name starts with this, lower-case).
FN_COLOUR_BLEND_PREFIX = 'blend_'
# Material-layer blend functions (lower-case fragment).
FN_LAYER_BLEND = ('matlayerblend',)
FN_FLATTEN = ('flattennormal',)
FN_NORMAL_BLEND = ('blendanglecorrectednormals',)
FN_HEIGHT_BLEND = ('heightlerp',)
# Output pins whose tree drives the vertex-shader position.
WPO_PINS = ('WorldPositionOffset', 'WorldDisplacement')
# Channel letters by component index.
CH = 'rgba'


def fmt(v):
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, (int, float)):
        return NUM_FMT % v
    if isinstance(v, (list, tuple)):
        return '(' + ','.join(fmt(x) for x in v) + ')'
    return str(v)


def short(cls):
    return cls[len('MaterialExpression'):] if cls.startswith('MaterialExpression') else cls


def leafname(path):
    if not path:
        return None
    s = path.rsplit('/', 1)[-1]
    return s.split('.', 1)[0]


def cut(s, n):
    return s if len(s) <= n else s[:n] + ELLIPSIS


class Pack:
    """Locates and caches packages of one pack by /Game path."""

    def __init__(self, root_dir):
        self.root_dir = os.path.abspath(root_dir)
        self.content = os.path.dirname(self.root_dir)
        self.root = os.path.basename(self.root_dir)
        self.cache = {}
        self.tex_cache = {}

    def file_of(self, gpath):
        if not gpath or not gpath.startswith('/Game/'):
            return None
        rel = gpath[len('/Game/'):].split('.', 1)[0]
        f = os.path.join(self.content, rel + '.uasset')
        return f if os.path.isfile(f) else None

    def gpath_of(self, f):
        rel = os.path.relpath(f, self.content)
        return '/Game/' + os.path.splitext(rel)[0].replace(os.sep, '/')

    def package(self, f):
        if f not in self.cache:
            try:
                self.cache[f] = pkgread.Package(f)
            except Exception:
                self.cache[f] = None
        return self.cache[f]

    def graph(self, gpath):
        f = self.file_of(gpath)
        if not f:
            return None
        key = ('graph', f)
        if key not in self.cache:
            p = self.package(f)
            self.cache[key] = Graph(self, p, f) if p else None
        return self.cache[key]

    def texture(self, gpath):
        if gpath in self.tex_cache:
            return self.tex_cache[gpath]
        info = dict(path=gpath, in_pack=bool(self.file_of(gpath)))
        f = self.file_of(gpath)
        if f:
            p = self.package(f)
            e = main_export(p, f) if p else None
            if e:
                info['class'] = p.class_of(e)
                pr, _ = safe_props(p, e)
                for k, dv in TEXTURE_DEFAULTS.items():
                    t = find(pr, k)
                    info[k] = t.get('value') if t else dv
                    info[k + '_from_default'] = not t
                src = find(pr, 'Source')
                for k in ('SizeX', 'SizeY'):
                    c = child(src, k)
                    if c:
                        info[k] = c.get('value')
        self.tex_cache[gpath] = info
        return info


def full_ref(p, idx):
    """Object path of a reference: /Package/Path.Object for imports, self:Name for exports."""
    if not isinstance(idx, int) or idx == 0:
        return None
    try:
        if idx < 0:
            pk = p.import_package(idx)
            return '%s.%s' % (pk, p.obj_name(idx)) if pk else p.obj_name(idx)
        return 'self:' + p.obj_name(idx)
    except Exception:
        return None


#: FFrameworkObjectVersion: from PinsStoreFName (31) an FExpressionInput's InputName is an FName;
#: before it (UE 4.15 packs, which carry no Framework version: 0) an FString, so the masks after
#: it move (MaterialShared.cpp SerializeExpressionInput).
#: pkgread keys custom versions by the GUID's bytes as stored (four little-endian uint32s), not
#: by its printed form CFFC743F-43B04480-939114DF-171D2073.
FRAMEWORK_GUID = struct.pack('<4I', 0xCFFC743F, 0x43B04480, 0x939114DF, 0x171D2073).hex()
PINS_STORE_FNAME = 31


def _input_body(p, o):
    """(expr, output, input name, offset after the name) of an FExpressionInput at o."""
    expr, outi = struct.unpack_from('<ii', p.d, o)
    if p.custom.get(FRAMEWORK_GUID, 0) >= PINS_STORE_FNAME:
        return expr, outi, p.fname(struct.unpack_from('<q', p.d, o + 8)[0]), o + 16
    n = struct.unpack_from('<i', p.d, o + 8)[0]
    if n < 0:
        raw, end = p.d[o + 12:o + 12 - 2 * n], o + 12 - 2 * n
        name = raw.decode('utf-16-le', 'replace').rstrip('\0')
    else:
        raw, end = p.d[o + 12:o + 12 + n], o + 12 + n
        name = raw.decode('latin1').rstrip('\0')
    return expr, outi, name, end


def input_size(p, t):
    """Bytes of the FExpressionInput part of a struct tag (36 with an FName; 32 + the name with
    an FString)."""
    _e, _o, _n, after = _input_body(p, t['at'])
    return after + 20 - t['at']


def read_input(p, t):
    """Decode a natively serialized FExpressionInput struct tag."""
    if not t or t.get('type') != 'StructProperty' or t['size'] < 32:
        return None
    o = t['at']
    expr, outi, name, after = _input_body(p, o)
    size = after + 20 - o
    if size > t['size']:
        return None
    mask = struct.unpack_from('<5i', p.d, after)
    r = dict(expr=expr, out=outi, name=name, mask=mask[0], mr=mask[1], mg=mask[2], mb=mask[3], ma=mask[4])
    extra = t['size'] - size
    if extra >= 8:
        use_const = struct.unpack_from('<i', p.d, o + size)[0]
        r['use_constant'] = bool(use_const)
        body = p.d[o + size + 4:o + t['size']]
        st = t.get('struct')
        if st == 'ColorMaterialInput' and len(body) >= 4:
            b, g, rr, a = body[:4]
            r['constant'] = (rr, g, b, a)
        elif st == 'VectorMaterialInput' and len(body) >= 12:
            r['constant'] = struct.unpack_from('<3f', body)
        elif st == 'Vector2MaterialInput' and len(body) >= 8:
            r['constant'] = struct.unpack_from('<2f', body)
        elif st == 'ScalarMaterialInput' and len(body) >= 4:
            r['constant'] = struct.unpack_from('<f', body)[0]
        elif st == 'ShadingModelMaterialInput' and len(body) >= 4:
            r['constant'] = struct.unpack_from('<I', body)[0]
    return r


#: An array of structs: the count, one inner property tag (name, type, size, index, struct
#: name, struct guid, has-guid byte), then the items.
ARRAY_STRUCT_TAG = 49


def read_input_array(p, t):
    """[ref] of a TArray<FExpressionInput> tag (SetMaterialAttributes Inputs); expr 0 where
    nothing is wired."""
    if not t or t.get('type') != 'ArrayProperty' or not t.get('count'):
        return []
    o = t['at'] + 4 + ARRAY_STRUCT_TAG
    out = []
    for _ in range(t['count']):
        expr, outi, name, after = _input_body(p, o)
        mask = struct.unpack_from('<5i', p.d, after)
        out.append(dict(expr=expr, out=outi, name=name, mask=mask[0], mr=mask[1], mg=mask[2], mb=mask[3], ma=mask[4]))
        o = after + 20
    return out


def read_guid_array(p, t):
    """['XXXXXXXX-XXXXXXXX-XXXXXXXX-XXXXXXXX'] of a TArray<FGuid> tag (SetMaterialAttributes
    AttributeSetTypes), printed as UE's FGuid(A, B, C, D)."""
    if not t or t.get('type') != 'ArrayProperty' or not t.get('count'):
        return []
    o = t['at'] + 4 + ARRAY_STRUCT_TAG
    return ['%08X-%08X-%08X-%08X' % struct.unpack_from('<4I', p.d, o + 16 * k) for k in range(t['count'])]


def mask_channels(ref):
    if ref and ref.get('mask'):
        return ''.join(c for c, m in zip(CH, (ref['mr'], ref['mg'], ref['mb'], ref['ma'])) if m)
    return None


class Node:
    def __init__(self, g, e):
        self.g, self.e = g, e
        self.cls = short(g.p.class_of(e))
        self.name = e['name']
        self.idx = e['index']
        self.props, _ = safe_props(g.p, e)
        self.byname = {}
        for t in self.props:
            self.byname[t['name'] if not t.get('index') else '%s[%d]' % (t['name'], t['index'])] = t
        self.used = set()

    def get(self, k, default=None):
        t = self.byname.get(k)
        if t is None:
            d = CONST_DEFAULTS.get(self.cls, {})
            return d.get(k, default)
        return t.get('value', default)

    def inp(self, k):
        self.used.add(k)
        t = self.byname.get(k)
        r = read_input(self.g.p, t) if t else None
        return r if r and r['expr'] else None

    def inputs(self):
        """Every connected FExpressionInput struct property, in property order."""
        out = []
        for k, t in self.byname.items():
            if t['type'] == 'StructProperty' and t.get('struct') in ('ExpressionInput', 'MaterialAttributesInput') \
                    and t['size'] == input_size(self.g.p, t):
                r = read_input(self.g.p, t)
                if r and r['expr']:
                    out.append((k, r))
        return out

    def output_names(self):
        t = self.byname.get('Outputs')
        out = []
        for it in (t or {}).get('items', []) or []:
            n = find(it, 'OutputName')
            out.append(n.get('value') if n else '')
        return out


class Graph:
    """One Material or MaterialFunction package."""

    def __init__(self, pack, p, f):
        self.pack, self.p, self.f = pack, p, f
        self.gpath = pack.gpath_of(f)
        try:
            self.engine = tuple(int(x) for x in p.saved_by.split('-')[0].split('.')[:2])
        except ValueError:
            self.engine = BREAK_MA_NEW_ENGINE
        self.main = main_export(p, f)
        self.kind = p.class_of(self.main) if self.main else None
        self.nodes = {}
        for e in p.exports:
            c = p.class_of(e)
            if c.startswith('MaterialExpression'):
                n = Node(self, e)
                if n.cls not in SKIP_CLASSES:
                    self.nodes[e['index']] = n
        self.mprops = []
        for e in p.exports:
            if p.class_of(e) in ('Material', 'MaterialEditorOnlyData', 'MaterialFunction',
                                 'MaterialFunctionMaterialLayer', 'MaterialFunctionMaterialLayerBlend'):
                pr, _ = safe_props(p, e)
                self.mprops += pr

    def node(self, idx):
        return self.nodes.get(idx)

    def function_inputs(self):
        return {n.get('InputName'): n for n in self.nodes.values() if n.cls == 'FunctionInput'}

    def function_outputs(self):
        outs = [n for n in self.nodes.values() if n.cls == 'FunctionOutput']
        outs.sort(key=lambda n: (n.get('SortPriority', 0) or 0, n.idx))
        return outs


class Env:
    """A graph plus bindings of its FunctionInput names to (caller env, input ref)."""

    def __init__(self, g, bindings=None, parent=None, call=None):
        self.g, self.bindings, self.parent, self.call = g, bindings or {}, parent, call

    def depth(self):
        return 0 if not self.parent else 1 + self.parent.depth()


class Ctx:
    """Collects facts while one output pin is walked."""

    def __init__(self, pin):
        self.pin = pin
        self.facts = []
        self.visits = 0
        self.capped = False
        self.classes = collections.Counter()
        self.generic = collections.Counter()
        self.attr = None
        self.attr_hit = False
        self.fn_chan = None
        self.reached = set()

    def add(self, kind, roles, **kw):
        kw.update(kind=kind, pin=self.pin)
        if roles:
            kw['roles'] = sorted(roles)
        self.facts.append(kw)


class Walker:
    def __init__(self, pack):
        self.pack = pack

    # ---- entry
    def walk(self, env, ref, ctx, sel=None, roles=frozenset(), depth=0, stack=()):
        if not ref or not ref.get('expr'):
            return None
        if depth > MAX_DEPTH:
            ctx.capped = True
            return ELLIPSIS
        ctx.visits += 1
        if ctx.visits > MAX_VISITS_PER_PIN:
            ctx.capped = True
            return ELLIPSIS
        idx = ref['expr']
        n = env.g.node(idx)
        if n is None:
            return '?ref%d' % idx
        key = (id(env), idx)
        if key in stack:
            return '<cycle:%s>' % n.name
        stack = stack + (key,)
        ctx.classes[n.cls] += 1
        ctx.reached.add((env.g.gpath, idx))
        if n.cls not in HANDLED:
            ctx.generic[n.cls] += 1
        return self.node(env, n, ref, ctx, sel, roles, depth + 1, stack)

    def sub(self, env, n, k, ctx, roles, depth, stack, const=None, sel=None):
        r = n.inp(k)
        if r:
            return self.walk(env, r, ctx, sel, roles, depth, stack)
        if const is not None:
            return fmt(n.get(const))
        return None

    @staticmethod
    def csel(ref, sel):
        """Selection over a node's full output after the reference mask and an outer selection."""
        avail = mask_channels(ref)
        if not avail:
            return sel
        chosen = avail if sel is None else ''.join(avail[i] for i in sel if i < len(avail))
        return tuple(CH.index(c) for c in chosen)

    @staticmethod
    def suffix(ref, sel, comps=None):
        """Channel suffix for a reference after an optional component selection."""
        avail = mask_channels(ref) or comps
        if sel is None:
            return ('.' + avail) if mask_channels(ref) else '', avail
        base = avail or CH
        chosen = ''.join(base[i] for i in sel if i < len(base))
        return '.' + chosen, chosen

    # ---- node formatting
    def node(self, env, n, ref, ctx, sel, roles, depth, stack):
        c = n.cls
        W = lambda k, const=None, r=roles, s=None: self.sub(env, n, k, ctx, r, depth, stack, const, s)  # noqa
        sfx, chosen = self.suffix(ref, sel)
        cs = self.csel(ref, sel)
        if c in SCALAR_LEAVES:
            sfx, chosen = '', None

        if c in ('Reroute', 'NamedRerouteDeclaration'):
            r = n.inp('Input')
            return self.walk(env, self.compose(r, ref), ctx, sel, roles, depth, stack) if r else '?'
        if c == 'NamedRerouteUsage':
            decl = n.get('Declaration')
            dn = env.g.node(decl) if isinstance(decl, int) else None
            r = dn.inp('Input') if dn else None
            return self.walk(env, self.compose(r, ref), ctx, sel, roles, depth, stack) if r else '?'
        if c == 'ComponentMask':
            pick = [i for i, k in enumerate('RGBA') if n.get(k)]
            if cs is not None:
                pick = [pick[i] for i in cs if i < len(pick)]
            r = n.inp('Input')
            return self.walk(env, r, ctx, tuple(pick), roles, depth, stack) if r else '?'
        if c in BINOPS:
            a = W('A', 'ConstA', s=cs); b = W('B', 'ConstB', s=cs)
            return '(%s %s %s)' % (a, BINOPS[c], b)
        if c in ('Max', 'Min'):
            return '%s(%s, %s)' % (c.lower(), W('A', 'ConstA', s=cs), W('B', 'ConstB', s=cs))
        if c == 'LinearInterpolate':
            nf = len(ctx.facts)
            al = W('Alpha', 'ConstAlpha', roles | {'alpha'}, cs)
            a = W('A', 'ConstA', s=cs); b = W('B', 'ConstB', s=cs)
            vcs = [f for f in ctx.facts[nf:] if f['kind'] == 'vertex_colour' and 'alpha' in f.get('roles', [])]
            if vcs:
                ctx.add('vertex_colour_lerp', roles, channels=''.join(sorted(set(''.join(f['channels'] or '' for f in vcs)))),
                        a=cut(a or '', CONTEXT_CHARS), b=cut(b or '', CONTEXT_CHARS), alpha=cut(al or '', CONTEXT_CHARS))
            return 'lerp(%s, %s, %s)' % (a, b, al)
        if c == 'Power':
            return 'pow(%s, %s)' % (W('Base', s=cs), W('Exponent', 'ConstExponent', s=cs))
        if c == 'Clamp':
            return 'clamp(%s, %s, %s)' % (W('Input', s=cs), W('Min', 'MinDefault', s=cs), W('Max', 'MaxDefault', s=cs))
        if c in UNARY:
            if c == 'Normalize':
                return '%s(%s)%s' % (UNARY[c], W('VectorInput'), sfx)
            s = W('Input', s=cs)
            if c == 'OneMinus':
                ctx.add('one_minus', roles, operand=cut(s or '', CONTEXT_CHARS))
                return '(1 - %s)' % s
            return '%s(%s)' % (UNARY[c], s)
        if c in ('Sine', 'Cosine'):
            per = n.get('Period')
            return '%s(%s%s)' % (c[:3].lower(), W('Input', s=cs), '' if per == 1.0 else ', period=' + fmt(per))
        if c in ('DotProduct', 'CrossProduct', 'Distance'):
            return '%s(%s, %s)%s' % (c[:-7].lower() if c != 'Distance' else 'distance', W('A'), W('B'), sfx)
        if c == 'Constant':
            v = n.get('R', 0.0)
            ctx.add('constant', roles, value=v)
            return fmt(v)
        if c in ('Constant2Vector', 'Constant3Vector', 'Constant4Vector'):
            if c == 'Constant2Vector':
                v = (n.get('R', 0.0), n.get('G', 0.0))
            else:
                v = tuple(n.get('Constant') or (0.0, 0.0, 0.0, 0.0))
                v = v[:3] if c == 'Constant3Vector' else v
            if cs is not None:
                v = tuple(v[i] for i in cs if i < len(v))
            ctx.add('constant', roles, value=list(v), negative=any(x < 0 for x in v))
            return fmt(v)
        if c == 'AppendVector':
            return 'append(%s, %s)%s' % (W('A'), W('B'), sfx)
        if c == 'TextureCoordinate':
            ci = n.get('CoordinateIndex', 0); u = n.get('UTiling', 1.0); v = n.get('VTiling', 1.0)
            ctx.add('texcoord', roles, index=ci, tiling=[u, v])
            s = 'UV%d' % ci
            if u != 1.0 or v != 1.0:
                s += '*' + fmt((u, v))
            return s + sfx
        if c == 'Panner':
            coord = W('Coordinate') or 'UV%d' % (n.get('ConstCoordinate') or 0)
            sp = W('Speed') or fmt((n.get('SpeedX', 0.0), n.get('SpeedY', 0.0)))
            tm = W('Time') or 'Time'
            ctx.add('panner', roles, speed=sp, time=tm)
            return 'panner(%s, speed=%s, time=%s)%s' % (coord, sp, tm, sfx)
        if c == 'Rotator':
            coord = W('Coordinate') or 'UV%d' % (n.get('ConstCoordinate') or 0)
            tm = W('Time') or 'Time'
            ctx.add('rotator', roles, speed=n.get('Speed'), time=tm)
            return 'rotator(%s, centre=%s, speed=%s, time=%s)%s' % (
                coord, fmt((n.get('CenterX'), n.get('CenterY'))), fmt(n.get('Speed')), tm, sfx)
        if c == 'WorldPosition':
            ctx.add('world_position', roles, option=n.get('WorldPositionShaderOffset'))
            return 'WorldPos' + sfx
        if c == 'VertexColor':
            ch = chosen if chosen else (mask_channels(ref) or 'rgba')
            ctx.add('vertex_colour', roles, channels=ch)
            return 'VertexColor' + ('.' + ch if ch else '')
        if c == 'Fresnel':
            ctx.add('fresnel', roles)
            args = ['exp=' + (W('ExponentIn') or fmt(n.get('Exponent'))),
                    'base=' + (W('BaseReflectFractionIn') or fmt(n.get('BaseReflectFraction')))]
            nrm = W('Normal')
            if nrm:
                args.append('normal=' + nrm)
            return 'fresnel(%s)%s' % (', '.join(args), sfx)
        if c in ('StaticSwitchParameter', 'StaticSwitch'):
            nm = n.get('ParameterName') if c == 'StaticSwitchParameter' else None
            dv = bool(n.get('DefaultValue'))
            label = nm or 'StaticSwitch'
            a = W('A', s=cs, r=roles | {'switch:%s=true' % label})
            b = W('B', s=cs, r=roles | {'switch:%s=false' % label})
            head = 'Switch[%s=%s]' % (nm, fmt(dv)) if nm else 'switch(%s; default=%s)' % (W('Value') or '', fmt(dv))
            return '%s(T: %s | F: %s)' % (head, a, b)
        if c in ('StaticBoolParameter', 'StaticBool'):
            nm = n.get('ParameterName')
            return 'Bool[%s=%s]' % (nm, fmt(bool(n.get('Value' if c == 'StaticBool' else 'DefaultValue'))))
        if c == 'StaticComponentMaskParameter':
            nm = n.get('ParameterName')
            return 'CMaskParam[%s=%s](%s)' % (nm, ''.join(k for k in 'RGBA' if n.get('Default' + k)), W('Input'))
        if c == 'ScalarParameter':
            ctx.add('scalar_param', roles, name=n.get('ParameterName'), default=n.get('DefaultValue'))
            return 'Scalar[%s]' % n.get('ParameterName')
        if c == 'VectorParameter':
            ctx.add('vector_param', roles, name=n.get('ParameterName'), default=n.get('DefaultValue'))
            return 'Vec[%s]%s' % (n.get('ParameterName'), sfx)
        if c == 'Time':
            ctx.add('time', roles)
            return 'Time'
        if c in LEAVES:
            ctx.add('leaf', roles, name=LEAVES[c])
            return LEAVES[c] + sfx
        if c in TEXTURE_CLASSES:
            return self.texture(env, n, ref, ctx, sel, roles, depth, stack)
        if c == 'Desaturation':
            ctx.add('colour_adjust', roles, function='Desaturation')
            return 'desaturate(%s, %s)%s' % (W('Input'), W('Fraction') or '1', sfx)
        if c == 'ConstantBiasScale':
            return '((%s + %s) * %s)' % (W('Input', s=cs), fmt(n.get('Bias')), fmt(n.get('Scale')))
        if c == 'If':
            return 'if(%s ? %s; >:%s =:%s <:%s)%s' % (W('A'), W('B', 'ConstB'), W('AGreaterThanB'),
                                                       W('AEqualsB'), W('ALessThanB'), sfx)
        if c == 'DepthFade':
            ctx.add('leaf', roles, name='DepthFade')
            return 'depthfade(%s, dist=%s)%s' % (W('InOpacity', 'OpacityDefault'),
                                                  W('FadeDistance', 'FadeDistanceDefault'), sfx)
        if c == 'SphereMask':
            return 'spheremask(%s, %s, r=%s, hard=%s)%s' % (W('A'), W('B'), W('Radius', 'AttenuationRadius'),
                                                           W('Hardness', 'HardnessPercent'), sfx)
        if c == 'Transform':
            return 'transform[%s->%s](%s)%s' % (n.get('TransformSourceType'), n.get('TransformType'),
                                                W('Input'), sfx)
        if c == 'SceneTexture':
            ctx.add('leaf', roles, name='SceneTexture')
            return 'SceneTexture[%s](%s)%s' % (n.get('SceneTextureId'), W('Coordinates') or '', sfx)
        if c == 'SceneDepth':
            ctx.add('leaf', roles, name='SceneDepth')
            return 'SceneDepth(%s)%s' % (W('Input') or '', sfx)
        if c in ('FeatureLevelSwitch', 'QualitySwitch'):
            labels = FEATURE_LEVELS if c == 'FeatureLevelSwitch' else QUALITY_LEVELS
            parts = ['default: ' + (W('Default', s=cs) or '')]
            for k, _ in n.inputs():
                if k == 'Default':
                    continue
                i = int(k[k.index('[') + 1:-1]) if '[' in k else 0
                parts.append('%s: %s' % (labels[i] if i < len(labels) else k, W(k, s=cs)))
            return '%s(%s)' % (c, ' | '.join(parts))
        if c == 'Noise':
            ctx.add('leaf', roles, name='Noise')
            return 'noise(%s, scale=%s)%s' % (W('Position') or 'WorldPos', fmt(n.get('Scale', 1.0)), sfx)
        if c == 'DistanceToNearestSurface':
            return 'DistToSurface(%s)%s' % (W('Position') or 'WorldPos', sfx)
        if c == 'LandscapeLayerCoords':
            ms = n.get('MappingScale', 0.0)
            ctx.add('landscape_coords', roles, scale=ms)
            return 'LandscapeUV(scale=%s)%s' % (fmt(ms), sfx)
        if c in ('LandscapeLayerSample', 'LandscapeLayerWeight'):
            nm = n.get('ParameterName')
            ctx.add('landscape_layer', roles, name=nm)
            if c == 'LandscapeLayerSample':
                return 'LayerSample[%s]%s' % (nm, sfx)
            return 'LayerWeight[%s](base=%s, layer=%s)%s' % (nm, W('Base'), W('Layer'), sfx)
        if c == 'LandscapeLayerBlend':
            parts = []
            t = n.byname.get('Layers')
            for it in (t or {}).get('items', []) or []:
                ln = find(it, 'LayerName'); bt = find(it, 'BlendType')
                li = read_input(env.g.p, find(it, 'LayerInput'))
                hi = read_input(env.g.p, find(it, 'HeightInput'))
                nm = ln.get('value') if ln else '?'
                ctx.add('landscape_layer', roles, name=nm)
                s = self.walk(env, li, ctx, sel, roles | {'layer:%s' % nm}, depth, stack) if li and li['expr'] else '0'
                if hi and hi['expr']:
                    s += ' ^h ' + self.walk(env, hi, ctx, None, roles | {'height'}, depth, stack)
                parts.append('%s[%s]: %s' % (nm, (bt.get('value') if bt else 'LB_WeightBlend').replace('LB_', ''), s))
            return 'LandscapeBlend(%s)%s' % ('; '.join(parts), sfx)
        if c == 'MakeMaterialAttributes' and ctx.attr:
            r = n.inp(ctx.attr)
            if not r:
                return None
            ctx.attr_hit = True
            sub_sel = (0,) if ctx.attr in SCALAR_PINS else None
            return self.walk(env, r, ctx, sub_sel, roles, depth, stack)
        if c == 'MakeMaterialAttributes':
            parts = ['%s=%s' % (k, self.walk(env, r, ctx, None, roles | {'attr:' + k}, depth, stack))
                     for k, r in n.inputs()]
            return 'MakeMA(%s)' % ', '.join(parts)
        if c == 'BreakMaterialAttributes':
            names = n.output_names() or (BREAK_MA_OUTPUTS if env.g.engine >= BREAK_MA_NEW_ENGINE
                                         else BREAK_MA_OUTPUTS_OLD)
            o = ref.get('out', 0)
            on = names[o] if o < len(names) else 'out%d' % o
            saved = ctx.attr
            ctx.attr = on
            hit = ctx.attr_hit
            inner = W('MaterialAttributes')
            ctx.attr, ctx.attr_hit = saved, hit
            return 'Break(%s).%s' % (inner, on)
        if c == 'MaterialFunctionCall':
            return self.call(env, n, ref, ctx, sel, roles, depth, stack)
        if c == 'FunctionInput':
            nm = n.get('InputName')
            if nm in env.bindings:
                cenv, cref = env.bindings[nm]
                if cref:
                    return self.walk(cenv, self.compose(cref, ref), ctx, sel, roles, depth, stack)
            pv = n.inp('Preview')
            if env.parent is not None and pv and n.get('bUsePreviewValueAsDefault'):
                return self.walk(env, pv, ctx, sel, roles, depth, stack)
            if env.parent is not None and n.get('bUsePreviewValueAsDefault'):
                return fmt(n.get('PreviewValue'))
            return 'In[%s]%s' % (nm, sfx)
        if c == 'FunctionOutput':
            return W('A', s=cs) or '?'
        return self.generic(env, n, ref, ctx, sel, roles, depth, stack, sfx)

    @staticmethod
    def compose(inner, outer):
        """Carry an outer reference's channel mask onto an inner pass-through reference."""
        if not inner:
            return inner
        if outer and outer.get('mask') and not inner.get('mask'):
            r = dict(inner)
            for k in ('mask', 'mr', 'mg', 'mb', 'ma'):
                r[k] = outer[k]
            return r
        return inner

    def generic(self, env, n, ref, ctx, sel, roles, depth, stack, sfx):
        args = ['%s=%s' % (k, self.walk(env, r, ctx, None, roles, depth, stack)) for k, r in n.inputs()]
        n.used.update(k for k, _ in n.inputs())
        for t in n.props:
            if t['name'] in EDITOR_PROPS or t['type'] in ('StructProperty', 'ArrayProperty'):
                continue
            v = t.get('value')
            if t['type'] == 'ObjectProperty':
                v = leafname(full_ref(env.g.p, v))
            if v is not None:
                args.append('%s=%s' % (t['name'], fmt(v)))
        names = n.output_names()
        o = ref.get('out', 0)
        on = ('.' + names[o]) if names and o < len(names) and names[o] and len(names) > 1 else ''
        ctx.add('generic', roles, cls=n.cls)
        return '%s(%s)%s%s' % (n.cls, ', '.join(args), on, sfx)

    def texture(self, env, n, ref, ctx, sel, roles, depth, stack):
        c = n.cls
        tex = full_ref(env.g.p, n.get('Texture'))
        to = n.inp('TextureObject')
        tobj_s = None
        pname = n.get('ParameterName')
        if to:
            saved, ctx.fn_chan = ctx.fn_chan, None
            nf = len(ctx.facts)
            tobj_s = self.walk(env, to, ctx, None, roles | {'texobj_into_sample'}, depth, stack)
            ctx.fn_chan = saved
            objs = [f for f in ctx.facts[nf:] if f['kind'] == 'texture_object']
            if objs:
                tex, pname = objs[-1]['texture'], objs[-1]['param']
        nm = leafname(tex)
        label = ('TexP[%s:%s]' % (pname, nm)) if pname else ('Tex[%s]' % nm)
        if tobj_s:
            label = 'Tex<%s>' % tobj_s
        uvr = n.inp('Coordinates')
        uv = self.walk(env, uvr, ctx, None, roles | {'uv'}, depth, stack) if uvr else \
            'UV%d' % (n.get('ConstCoordinate') or 0)
        st = n.get('SamplerType')
        sfx, chosen = self.suffix(ref, sel)
        is_object = c in ('TextureObject', 'TextureObjectParameter')
        if not is_object:
            if chosen is None:
                o = ref.get('out', 0)
                chosen = ['rgb', 'r', 'g', 'b', 'a', 'rgba'][o] if o < 6 else 'rgba'
            ctx.add('texture', roles, texture=tex, param=pname, channels=chosen, uv=uv, sampler=st,
                    node=n.name, via=None, texobj=tobj_s)
        else:
            ctx.add('texture_object', roles, texture=tex, param=pname, node=n.name)
            if ctx.fn_chan:
                fname, oname, outch = ctx.fn_chan
                ctx.add('texture', roles, texture=tex, param=pname, channels=outch, uv='fn:' + fname,
                        sampler=st, node=n.name, via='%s.%s' % (fname, oname), texobj=None)
        s = label
        if st and st not in ('SAMPLERTYPE_Color',):
            s += ':' + st.replace('SAMPLERTYPE_', '').lower()
        if not is_object and uv != 'UV0':
            s += '@' + uv
        return s + sfx

    def call(self, env, n, ref, ctx, sel, roles, depth, stack):
        mf = n.get('MaterialFunction')
        fpath = full_ref(env.g.p, mf) if isinstance(mf, int) else None
        fname = leafname(fpath) or '?'
        outs = n.byname.get('FunctionOutputs')
        oi = ref.get('out', 0)
        oname = None
        items = (outs or {}).get('items', []) or []
        if oi < len(items):
            o = find(items[oi], 'Output')
            on = child(o, 'OutputName')
            oname = on.get('value') if on else None
        bindings = {}
        argstr = []
        fin = n.byname.get('FunctionInputs')
        fl = fname.lower()
        sub_roles = roles | {'fn:' + fname}
        in_pack = bool(fpath and fpath.startswith('/Game/'))
        cs = self.csel(ref, sel)
        items_in = []
        for it in (fin or {}).get('items', []) or []:
            r = read_input(env.g.p, find(it, 'Input'))
            if r:
                bindings[r['name']] = (env, r if r['expr'] else None)
                if r['expr']:
                    items_in.append(r)
        split = FN_SPLIT.get(fname, {}).get(oname)
        if split is not None and len(items_in) == 1:
            pick = (CH.index(split),)
            if cs is not None:
                pick = tuple(pick[i] for i in cs if i < len(pick))
            return self.walk(env, items_in[0], ctx, pick, roles, depth, stack)
        outch = ''.join(CH[i] for i in cs) if cs else FN_OUTPUT_CHANNELS.get(oname, 'rgba')
        # inputs of in-pack functions are walked again when the body is inlined; walk them here for the
        # formula only
        actx = ctx
        if in_pack:
            actx = Ctx(ctx.pin)
            actx.attr = ctx.attr
        for r in items_in:
            nm = r['name']
            saved, actx.fn_chan = actx.fn_chan, (None if in_pack else (fname, oname, outch))
            mroles = {'alpha'} if any(w in nm.lower() for w in FN_MASK_INPUT_WORDS) else set()
            s = self.walk(env, r, actx, None, sub_roles | mroles | {'fnin:%s.%s' % (fname, nm)}, depth, stack)
            actx.fn_chan = saved
            argstr.append('%s=%s' % (nm, UNSET if s is None else s))
        if actx is not ctx:
            ctx.reached |= actx.reached
            ctx.classes.update(actx.classes)
            ctx.generic.update(actx.generic)
            ctx.visits += actx.visits
            ctx.capped = ctx.capped or actx.capped
        ctx.add('function', roles, function=fpath, output=oname, in_pack=in_pack)
        if any(k in fl for k in FN_FLATTEN):
            ctx.add('flatten_normal', roles, function=fname)
        g = self.pack.graph(fpath) if in_pack else None
        if g is not None and env.depth() < MAX_DEPTH:
            fenv = Env(g, bindings, env, fname)
            target = None
            for fo in g.function_outputs():
                if fo.get('OutputName') == oname:
                    target = fo
            if target is None and g.function_outputs() and oi < len(g.function_outputs()):
                target = g.function_outputs()[oi]
            if target is not None:
                fref = dict(expr=target.idx, out=0, mask=0, mr=0, mg=0, mb=0, ma=0)
                self.walk(fenv, fref, ctx, sel, roles | {'in:' + fname}, depth, stack)
        sfx, _ = self.suffix(ref, sel)
        return '%s%s(%s)%s' % (fname, ('.' + oname) if oname and len(items) > 1 else '', ', '.join(argstr), sfx)


# ---------------------------------------------------------------- material level
def header(g, mic_count):
    h = dict(path=g.gpath, kind=g.kind)
    pr = g.mprops
    for k, dv in HEADER_DEFAULTS.items():
        t = find(pr, k)
        h[k] = t.get('value') if t else dv
    t = find(pr, 'ShadingModels')
    if t and t.get('children'):
        c = child(t, 'ShadingModelField')
        h['ShadingModels'] = c.get('value') if c else None
    h['usage'] = sorted(t['name'] for t in pr if t['name'].startswith('bUsedWith') and t.get('value'))
    h['mic_count'] = mic_count
    if g.kind and g.kind.startswith('MaterialFunction'):
        for k in ('Description', 'bExposeToLibrary'):
            t = find(pr, k)
            if t:
                h[k] = t.get('value')
    return h


def parameters(g, pack):
    out = []
    for n in sorted(g.nodes.values(), key=lambda n: n.idx):
        if n.cls not in PARAM_CLASSES:
            continue
        d = dict(cls=n.cls, name=n.get('ParameterName'), group=n.get('Group') or 'None', node=n.name)
        if n.cls == 'ScalarParameter':
            d['default'] = n.get('DefaultValue', 0.0)
            for k in ('SliderMin', 'SliderMax'):
                if n.get(k) is not None:
                    d[k] = n.get(k)
        elif n.cls == 'VectorParameter':
            d['default'] = list(n.get('DefaultValue') or (0.0, 0.0, 0.0, 0.0))
        elif n.cls in TEXTURE_CLASSES:
            tp = full_ref(g.p, n.get('Texture'))
            d['default'] = tp
            d['sampler'] = n.get('SamplerType') or 'SAMPLERTYPE_Color'
            if tp:
                ti = pack.texture(tp)
                d['srgb'] = ti.get('SRGB')
                d['compression'] = ti.get('CompressionSettings')
        elif n.cls in ('StaticSwitchParameter', 'StaticBoolParameter'):
            d['default'] = bool(n.get('DefaultValue'))
        elif n.cls == 'StaticComponentMaskParameter':
            d['default'] = ''.join(k for k in 'RGBA' if n.get('Default' + k))
        out.append(d)
    return out


def pin_refs(g):
    refs = {}
    for t in g.mprops:
        if t['name'] in PINS and t['type'] == 'StructProperty':
            r = read_input(g.p, t)
            if r and (r['expr'] or r.get('use_constant')):
                refs[t['name']] = r
    return refs


def walk_pins(pack, g):
    """Formula and facts per output pin; functions report each FunctionOutput as a pin.

    A material with bUseMaterialAttributes reports its MaterialAttributes tree once per attribute,
    projected through every MakeMaterialAttributes it reaches; other materials ignore that pin."""
    w = Walker(pack)
    env = Env(g)
    pins = collections.OrderedDict()
    reached = set()
    jobs = []
    if g.kind == 'Material':
        refs = pin_refs(g)
        use_ma = bool((find(g.mprops, 'bUseMaterialAttributes') or {}).get('value'))
        if use_ma:
            ma = refs.get('MaterialAttributes')
            if ma and ma['expr']:
                for k in PINS:
                    if k != 'MaterialAttributes':
                        jobs.append((k, ma, k))
        else:
            jobs = [(k, refs[k], None) for k in PINS if k in refs and k != 'MaterialAttributes']
        for n in g.nodes.values():
            if n.cls == 'LandscapeGrassOutput':
                for it in (n.byname.get('GrassTypes') or {}).get('items', []) or []:
                    nm = find(it, 'Name'); r = read_input(g.p, find(it, 'Input'))
                    if r and r['expr']:
                        jobs.append(('LandscapeGrass:%s' % (nm.get('value') if nm else '?'), r, None))
    else:
        for fo in g.function_outputs():
            jobs.append(('out:' + str(fo.get('OutputName')),
                         dict(expr=fo.idx, out=0, mask=0, mr=0, mg=0, mb=0, ma=0), None))
    for k, r, attr in jobs:
        ctx = Ctx(k)
        ctx.attr = attr
        sel = (0,) if k in SCALAR_PINS else None
        if r['expr']:
            s = w.walk(env, r, ctx, sel)
        else:
            s = 'const' + fmt(r.get('constant'))
        if attr and not ctx.attr_hit:
            continue
        reached |= ctx.reached
        pins[k] = dict(formula=cut(s or '', MAX_FORMULA_CHARS), facts=ctx.facts, capped=ctx.capped,
                       visits=ctx.visits, classes=dict(ctx.classes), generic=dict(ctx.generic))
        if attr:
            pins[k]['via_material_attributes'] = True
        if r.get('use_constant'):
            pins[k]['use_constant'] = True
            pins[k]['constant'] = r.get('constant')
    return pins, reached


# ---------------------------------------------------------------- feature tags
def uv_scale(uv):
    """Canonical UV string with parameter defaults kept symbolic; used to compare sample scales."""
    return uv or 'UV0'


def derive(pins, hdr, pack, params):
    tags = collections.OrderedDict()
    tex_facts = [f for p in pins.values() for f in p['facts'] if f['kind'] == 'texture']
    all_facts = [f for p in pins.values() for f in p['facts']]

    # UV usage
    uvs = sorted({f['uv'] for f in tex_facts})
    wp_in_uv = [f for f in all_facts if f['kind'] == 'world_position' and 'uv' in f.get('roles', [])]
    fn_world = sorted({(f['pin'], leafname(f['function'])) for f in all_facts if f['kind'] == 'function'
                       and any(k in (leafname(f['function']) or '').lower() for k in FN_WORLD_ALIGNED)})
    land_uv = [f for f in all_facts if f['kind'] == 'landscape_coords']
    uv0_only = bool(tex_facts) and all(u.startswith('UV0') and 'WorldPos' not in u and 'fn:' not in u
                                       for u in uvs) and not wp_in_uv
    tags['uv0_only'] = uv0_only
    tags['uv_sets'] = sorted({'UV%d' % f['index'] for f in all_facts if f['kind'] == 'texcoord'} |
                             ({'UV0'} if any(u == 'UV0' for u in uvs) else set()))
    tags['uv_tiling'] = sorted({fmt(tuple(f['tiling'])) for f in all_facts if f['kind'] == 'texcoord'
                                and tuple(f['tiling']) != (1.0, 1.0)})
    tags['uv_formulas'] = uvs
    tags['world_aligned'] = sorted({p for p, _ in fn_world} | {f['pin'] for f in wp_in_uv})
    tags['world_aligned_functions'] = sorted({fn for _, fn in fn_world})
    tags['landscape_uv_scale'] = sorted({f['scale'] for f in land_uv})
    # vertex colour
    vc = [f for f in all_facts if f['kind'] == 'vertex_colour']
    tags['vertex_colour'] = {}
    for f in vc:
        tags['vertex_colour'].setdefault(f['pin'], set()).update(f['channels'] or '')
    tags['vertex_colour'] = {k: ''.join(sorted(v, key=CH.index)) for k, v in tags['vertex_colour'].items()}
    tags['vertex_colour_blends'] = [dict(pin=f['pin'], channels=f['channels'], a=f['a'], b=f['b'])
                                    for f in all_facts if f['kind'] == 'vertex_colour_lerp']
    # detail texture: one pin samples textures at two or more UV scales
    detail = {}
    for pin, p in pins.items():
        by_uv = collections.defaultdict(set)
        for f in p['facts']:
            if f['kind'] == 'texture' and not f['uv'].startswith('fn:'):
                by_uv[uv_scale(f['uv'])].add(leafname(f['texture']) or f['param'])
        if len(by_uv) >= 2:
            detail[pin] = {u: sorted(x for x in v if x) for u, v in by_uv.items()}
    tags['multi_scale_samples'] = detail
    # WPO / animation
    wpo = {}
    for pin in WPO_PINS:
        if pin in pins:
            fns = sorted({leafname(f['function']) for f in pins[pin]['facts'] if f['kind'] == 'function'})
            wpo[pin] = dict(functions=fns, time=any(f['kind'] == 'time' for f in pins[pin]['facts']),
                            vertex_colour=''.join(sorted({c for f in pins[pin]['facts']
                                                          if f['kind'] == 'vertex_colour'
                                                          for c in (f['channels'] or '')})))
    tags['wpo'] = wpo
    tags['wind'] = sorted({leafname(f['function']) for f in all_facts if f['kind'] == 'function'
                           and any(k in (leafname(f['function']) or '').lower() for k in FN_WIND)})
    tags['panner'] = sorted({f['pin'] for f in all_facts if f['kind'] == 'panner'})
    tags['uv_rotator'] = sorted({'%s(%s)' % (f['pin'], 'animated' if 'Time' in f['time'] else 'static time=' + f['time'])
                                 for f in all_facts if f['kind'] == 'rotator'})
    tags['time'] = sorted({f['pin'] for f in all_facts if f['kind'] == 'time'})
    # emissive
    em = pins.get('EmissiveColor')
    if em:
        etex = [leafname(f['texture']) or f['param'] for f in em['facts'] if f['kind'] == 'texture'
                and role_of(f) is None]
        emask = [leafname(f['texture']) or f['param'] for f in em['facts'] if f['kind'] == 'texture'
                 and role_of(f) is not None]
        tags['emissive'] = dict(textures=sorted(set(x for x in etex if x)),
                                params=sorted({f['name'] for f in em['facts'] if f['kind'] in
                                               ('scalar_param', 'vector_param')}),
                                mask_textures=sorted(set(x for x in emask if x)),
                                kind='texture' if etex else ('masked-constant' if emask else 'constant'))
    else:
        tags['emissive'] = None
    # colour adjust / hue / fresnel / flatten
    fnames = [(f['pin'], leafname(f['function']) or '') for f in all_facts if f['kind'] == 'function']
    tags['hue_shift'] = sorted({p for p, n in fnames if any(k in n.lower() for k in FN_HUE)})
    tags['layer_blend'] = sorted({'%s:%s' % (p, n) for p, n in fnames if any(k in n.lower() for k in FN_LAYER_BLEND)})
    tags['colour_adjust'] = sorted({'%s:%s' % (p, n) for p, n in fnames if any(k in n.lower() for k in FN_COLOUR_ADJUST)
                                    or n.lower().startswith(FN_COLOUR_BLEND_PREFIX)}
                                   | {'%s:Desaturation' % f['pin'] for f in all_facts if f['kind'] == 'colour_adjust'})
    tags['fresnel'] = sorted({f['pin'] for f in all_facts if f['kind'] == 'fresnel'})
    tags['normal_flatten'] = sorted({'%s:%s' % (f['pin'], f['function']) for f in all_facts
                                     if f['kind'] == 'flatten_normal'})
    tags['normal_blend'] = sorted({'%s:%s' % (p, n) for p, n in fnames if any(k in n.lower() for k in FN_NORMAL_BLEND)})
    tags['height_blend'] = sorted({'%s:%s' % (p, n) for p, n in fnames if any(k in n.lower() for k in FN_HEIGHT_BLEND)})
    # normal sign tricks: negative constants or one-minus over a texture channel inside the Normal pin
    trick = []
    nm = pins.get('Normal')
    if nm:
        for f in nm['facts']:
            if set(f.get('roles', [])) & {'uv', 'alpha'}:
                continue
            if f['kind'] == 'constant' and f.get('negative') and len(f['value']) >= 2:
                trick.append('negative constant %s' % fmt(tuple(f['value'])))
            if f['kind'] == 'one_minus' and 'Tex' in f['operand']:
                trick.append('one-minus %s' % f['operand'])
    tags['normal_sign_tricks'] = trick
    # blend
    bm = hdr.get('BlendMode')
    op = {}
    for pin in ('Opacity', 'OpacityMask'):
        if pin in pins:
            op[pin] = sorted({'%s.%s' % (leafname(f['texture']) or f['param'], f['channels'])
                              for f in pins[pin]['facts'] if f['kind'] == 'texture'})
            op[pin + '_params'] = sorted({f['name'] for f in pins[pin]['facts'] if f['kind'] in ('scalar_param',)})
    tags['blend'] = dict(mode=bm, sources=op)
    tags['static_switches'] = sorted({p['name'] for p in params if p['cls'] in
                                      ('StaticSwitchParameter', 'StaticBoolParameter')})
    tags['landscape_layers'] = sorted({f['name'] for f in all_facts if f['kind'] == 'landscape_layer'})
    tags['scene_or_depth'] = sorted({f['name'] for f in all_facts if f['kind'] == 'leaf' and f['name'] in
                                     ('SceneTexture', 'SceneDepth', 'DepthFade', 'PixelDepth')})
    tags['generic_nodes'] = sorted({f['cls'] for f in all_facts if f['kind'] == 'generic'})
    tags['in_pack_functions'] = sorted({f['function'] for f in all_facts if f['kind'] == 'function' and f['in_pack']})
    tags['engine_functions'] = sorted({f['function'] for f in all_facts if f['kind'] == 'function'
                                       and not f['in_pack']})
    tags['packing'], tags['packing_pattern'] = packing(pins, pack)
    tags['packing_labels'] = ' + '.join(sorted(x['label'] for x in tags['packing'])) or '(no textures)'
    return tags


def role_of(f):
    rs = f.get('roles', [])
    if 'uv' in rs:
        return 'uv'
    if 'alpha' in rs or 'height' in rs:
        return 'mask'
    return None


def packing(pins, pack):
    """Per texture slot: channel -> pins it reaches (with (mask) when it only drives a lerp alpha)."""
    slots = collections.OrderedDict()
    for pin, p in pins.items():
        for f in p['facts']:
            if f['kind'] != 'texture':
                continue
            key = f['param'] or leafname(f['texture']) or '?'
            s = slots.setdefault(key, dict(texture=f['texture'], param=f['param'], sampler=set(), channels={}))
            if f['sampler']:
                s['sampler'].add(f['sampler'].replace('SAMPLERTYPE_', ''))
            ch = f['channels'] or 'rgba'
            ch = ch if ch in ('rgb', 'rgba') else ch
            rl = role_of(f)
            target = pin + ('(%s)' % rl if rl else '')
            if f['via']:
                target += '[%s]' % f['via'].split('.')[0]
            s['channels'].setdefault(ch, set()).add(target)
    out = []
    pattern = []
    for key, s in slots.items():
        ti = pack.texture(s['texture']) if s['texture'] else {}
        chmap = {k: sorted(v) for k, v in sorted(s['channels'].items(), key=lambda kv: CH.index(kv[0][0]))}
        out.append(dict(slot=key, texture=s['texture'], sampler=sorted(s['sampler']), srgb=ti.get('SRGB'),
                        compression=ti.get('CompressionSettings'), srgb_from_default=ti.get('SRGB_from_default'),
                        channels=chmap))
        pattern.append('{' + ','.join('%s:%s' % (k, '+'.join(v)) for k, v in chmap.items()) + '}')
        out[-1]['label'] = slot_label(chmap)
    return out, ' + '.join(sorted(pattern))


def short_target(t):
    pin = t.split('(')[0].split('[')[0]
    nm = PIN_SHORT.get(pin, pin)
    if '(mask)' in t:
        return 'mask'
    if '(uv)' in t:
        return 'uv'
    return nm


def slot_label(chmap):
    """Short role label of one texture slot, e.g. BC, N, BC+A(OpMask), TEX(R=AO,G=Rough,B=Metal)."""
    chans = {}
    for ch, targets in chmap.items():
        names = sorted({short_target(t) for t in targets}, key=lambda x: (x in ('mask', 'uv'), x))
        chans[ch] = names
    keys = list(chans)
    if len(keys) == 1 and len(keys[0]) >= 3:
        return '+'.join(chans[keys[0]])
    wide = [k for k in keys if len(k) >= 3]
    if len(wide) == 1 and all(len(k) == 1 for k in keys if k != wide[0]):
        head = '+'.join(chans[wide[0]])
        rest = ','.join('%s=%s' % (k.upper(), '+'.join(chans[k])) for k in keys if k != wide[0])
        return head + ('(%s)' % rest if rest else '')
    return 'TEX(%s)' % ','.join('%s=%s' % (k.upper(), '+'.join(chans[k])) for k in keys)


# ---------------------------------------------------------------- material instances
def param_name(it):
    pi = find(it, 'ParameterInfo')
    nm = child(pi, 'Name') if pi else find(it, 'ParameterName')
    return nm.get('value') if nm else None


def mic_record(pack, p, e, gpath):
    pr, _ = safe_props(p, e)
    par = find(pr, 'Parent')
    rec = dict(path=gpath, parent=full_ref(p, par.get('value')) if par else None)
    sc = find(pr, 'ScalarParameterValues')
    rec['scalars'] = {param_name(it): (find(it, 'ParameterValue') or {}).get('value')
                      for it in (sc or {}).get('items', []) or []}
    vc = find(pr, 'VectorParameterValues')
    rec['vectors'] = {}
    for it in (vc or {}).get('items', []) or []:
        v = find(it, 'ParameterValue')
        rec['vectors'][param_name(it)] = list(v['value']) if v and v.get('value') else None
    tc = find(pr, 'TextureParameterValues')
    rec['textures'] = {}
    for it in (tc or {}).get('items', []) or []:
        v = find(it, 'ParameterValue')
        tp = full_ref(p, v.get('value')) if v else None
        ti = pack.texture(tp) if tp else {}
        rec['textures'][param_name(it)] = dict(path=tp, srgb=ti.get('SRGB'), compression=ti.get('CompressionSettings'))
    sp = find(pr, 'StaticParameters')
    rec['static_switches'] = []
    rec['static_masks'] = []
    for c in (sp or {}).get('children', []) or []:
        for it in c.get('items', []) or []:
            d = dict(name=param_name(it))
            for k in ('Value', 'bOverride', 'R', 'G', 'B', 'A'):
                t = find(it, k)
                if t is not None:
                    d[k] = t.get('value')
            if c['name'] == 'StaticSwitchParameters':
                rec['static_switches'].append(d)
            elif c['name'] == 'StaticComponentMaskParameters':
                rec['static_masks'].append(d)
            else:
                d['array'] = c['name']
                rec['static_masks'].append(d)
    bp = find(pr, 'BasePropertyOverrides')
    raw = {c['name']: c.get('value') for c in (bp or {}).get('children', []) or []}
    rec['base_overrides_raw'] = raw
    rec['base_overrides'] = {k: raw.get(k, OVERRIDE_DEFAULTS.get(k)) for k in OVERRIDE_DEFAULTS
                             if raw.get('bOverride_' + k)}
    return rec


# ---------------------------------------------------------------- pack
def summary_line(pins):
    out = {}
    for k in SUMMARY_PINS:
        if k in pins:
            out[k] = cut(pins[k]['formula'], CONTEXT_CHARS)
    return out


def run(root_dir, out_path):
    t0 = time.time()
    pack = Pack(root_dir)
    files = sorted(glob.glob(os.path.join(pack.root_dir, '**', '*.uasset'), recursive=True))
    mats, funcs, mics = [], [], []
    for f in files:
        p = pack.package(f)
        if not p:
            continue
        e = main_export(p, f)
        c = p.class_of(e) if e else None
        if c == 'Material':
            mats.append(f)
        elif c and c.startswith('MaterialFunction'):
            funcs.append(f)
        elif c in ('MaterialInstanceConstant',):
            mics.append((f, p, e))
    # instances and their masters
    mic_recs = [mic_record(pack, p, e, pack.gpath_of(f)) for f, p, e in mics]
    by_path = {r['path']: r for r in mic_recs}
    for r in mic_recs:
        seen = set(); cur = r['parent']; chain = []
        while cur:
            pk = cur.split('.', 1)[0]
            if pk in seen:
                break
            seen.add(pk); chain.append(pk)
            nxt = by_path.get(pk)
            if not nxt:
                break
            cur = nxt['parent']
        r['parent_chain'] = chain
        r['master'] = chain[-1] if chain else None
    mic_count = collections.Counter(r['master'] for r in mic_recs)
    direct_count = collections.Counter((r['parent'] or '').split('.', 1)[0] for r in mic_recs)
    decoded = collections.Counter(); generic = collections.Counter(); capped = []
    all_classes = collections.Counter()
    mat_nodes = collections.Counter(); mat_reached = collections.Counter()
    reached_nodes = {}
    out_mats = []
    for kind, lst in (('Material', mats), ('MaterialFunction', funcs)):
        for f in lst:
            g = pack.graph(pack.gpath_of(f))
            if g is None:
                continue
            for n in g.nodes.values():
                all_classes[n.cls] += 1
                (decoded if n.cls in HANDLED else generic)[n.cls] += 1
            hdr = header(g, mic_count.get(g.gpath, 0))
            hdr['direct_mic_count'] = direct_count.get(g.gpath, 0)
            params = parameters(g, pack)
            pins, reached = walk_pins(pack, g)
            for gp, i in reached:
                rg = pack.graph(gp)
                rn = rg.node(i) if rg else None
                if rn is not None:
                    reached_nodes[(gp, i)] = rn
            if kind == 'Material':
                for n in g.nodes.values():
                    mat_nodes[n.cls] += 1
                    if (g.gpath, n.idx) in reached:
                        mat_reached[n.cls] += 1
            for k, pdata in pins.items():
                if pdata.get('capped'):
                    capped.append('%s:%s' % (g.gpath, k))
            tags = derive(pins, hdr, pack, params)
            out_mats.append(dict(header=hdr, parameters=params, pins=pins, tags=tags,
                                 summary=summary_line(pins), n_expressions=len(g.nodes)))
    masters = [m for m in out_mats if m['header']['kind'] == 'Material']
    unfollowed = collections.Counter()
    for rn in reached_nodes.values():
        if rn.cls in ('MaterialFunctionCall', 'LandscapeLayerBlend', 'MakeMaterialAttributes', 'Comment'):
            continue
        for k, _ in rn.inputs():
            if k not in rn.used:
                unfollowed['%s.%s' % (rn.cls, k)] += 1
    patterns = collections.Counter(); pattern_mics = collections.Counter()
    labels = collections.Counter(); label_mics = collections.Counter(); label_masters = collections.defaultdict(list)
    for m in masters:
        pat = m['tags']['packing_pattern'] or '(no textures)'
        patterns[pat] += 1
        pattern_mics[pat] += m['header']['mic_count']
        lb = m['tags']['packing_labels']
        labels[lb] += 1
        label_mics[lb] += m['header']['mic_count']
        label_masters[lb].append(m['header']['path'].rsplit('/', 1)[-1])
    srgb = collections.Counter()
    for m in masters:
        for sl in m['tags']['packing']:
            kind = 'packed' if sl['label'].startswith('TEX(') else sl['label']
            srgb['%s srgb=%s sampler=%s' % (kind, sl['srgb'], '/'.join(sl['sampler']) or 'Color')] += 1
    called = set()
    for m in masters:
        called.update(m['tags']['in_pack_functions'])
    summ = dict(
        masters=[dict(path=m['header']['path'], mic_count=m['header']['mic_count'],
                      blend=m['header']['BlendMode'], two_sided=m['header']['TwoSided'],
                      shading=m['header']['ShadingModel'], domain=m['header']['MaterialDomain'],
                      packing=m['tags']['packing_labels'], tags=compact_tags(m['tags']), formulas=m['summary'])
                 for m in masters],
        packing_labels=[dict(pattern=k, masters=v, mics=label_mics[k], names=label_masters[k])
                        for k, v in labels.most_common()],
        packing_patterns=[dict(pattern=k, masters=v, mics=pattern_mics[k]) for k, v in patterns.most_common()],
        functions_called_by_masters=sorted(called),
        slot_srgb_sampler=dict(srgb.most_common()),
        coverage=dict(expression_nodes=sum(all_classes.values()), handled_nodes=sum(decoded.values()),
                      generic_nodes=sum(generic.values()), generic_classes=dict(generic.most_common()),
                      handled_classes=dict(decoded.most_common()), capped_pins=capped,
                      connected_inputs_not_followed=dict(unfollowed.most_common()),
                      master_nodes=sum(mat_nodes.values()), master_nodes_reached_from_pins=sum(mat_reached.values()),
                      master_unreached_by_class={k: v - mat_reached[k] for k, v in mat_nodes.most_common()
                                                 if v - mat_reached[k]}),
        counts=dict(materials=len(mats), functions=len(funcs), mics=len(mic_recs)))
    json.dump(dict(generated_by=GENERATED_BY, pack_root=pack.root, pack_dir=pack.root_dir,
                   constants=dict(MAX_DEPTH=MAX_DEPTH, MAX_VISITS_PER_PIN=MAX_VISITS_PER_PIN,
                                  MAX_FORMULA_CHARS=MAX_FORMULA_CHARS),
                   seconds=round(time.time() - t0, 1), summary=summ, materials=out_mats, mics=mic_recs,
                   textures=pack.tex_cache), open(out_path, 'w'), indent=1, default=jsonable)


def compact_tags(t):
    out = []
    if t['uv0_only']:
        out.append('uv0-only')
    if t['uv_tiling']:
        out.append('uv-tiling ' + '/'.join(t['uv_tiling']))
    if t['world_aligned']:
        out.append('world-aligned[%s]' % ','.join(t['world_aligned']))
    if t['landscape_layers']:
        out.append('landscape-layers ' + ','.join(t['landscape_layers']))
    for pin, ch in t['vertex_colour'].items():
        out.append('vc.%s->%s' % (ch, pin))
    if t['multi_scale_samples']:
        out.append('multi-scale[%s]' % ','.join(t['multi_scale_samples']))
    for pin, w in t['wpo'].items():
        out.append('%s(%s%s)' % (pin, ','.join(x for x in w['functions'] if x) or 'graph',
                                 ',time' if w['time'] else ''))
    if t['panner']:
        out.append('panner[%s]' % ','.join(t['panner']))
    if t['uv_rotator']:
        rot = sorted({x.split('(', 1)[1][:-1] for x in t['uv_rotator']})
        out.append('uv-rotator[%s]' % ';'.join(rot))
    if t['emissive']:
        out.append('emissive-' + t['emissive']['kind'])
    if t['hue_shift']:
        out.append('hue-shift[%s]' % ','.join(t['hue_shift']))
    if t['colour_adjust']:
        out.append('colour-adjust[%s]' % ','.join(t['colour_adjust']))
    if t['fresnel']:
        out.append('fresnel[%s]' % ','.join(t['fresnel']))
    if t['normal_flatten']:
        out.append('flatten-normal')
    if t['layer_blend']:
        out.append('layer-blend[%s]' % ','.join(sorted({x.split(':')[1] for x in t['layer_blend']})))
    if t['normal_blend']:
        out.append('normal-blend')
    if t['height_blend']:
        out.append('height-blend')
    if t['normal_sign_tricks']:
        out.append('normal-sign-trick')
    src = t['blend']['sources']
    if src.get('Opacity') or src.get('OpacityMask'):
        out.append('alpha-from ' + ','.join(src.get('OpacityMask', []) + src.get('Opacity', [])))
    if t['static_switches']:
        out.append('switches ' + ','.join(t['static_switches']))
    if t['scene_or_depth']:
        out.append('scene/depth ' + ','.join(t['scene_or_depth']))
    return out


def jsonable(o):
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    if isinstance(o, tuple):
        return list(o)
    return str(o)


if __name__ == '__main__':
    run(sys.argv[1], sys.argv[2])
