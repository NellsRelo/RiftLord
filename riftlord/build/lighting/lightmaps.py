"""Read and write a cooked level's baked lighting: its `<Level>_BuiltData` package.

Every OMD3 level and sublevel that has static lighting ships a separate
`<Level>_BuiltData` package (UE 4.27 `MapBuildDataSeparatePackage`). It holds:

* one `MapBuildDataRegistry` export -- per-primitive light and shadow maps keyed
  by the component's `MapBuildDataId`, the precomputed light volume, the
  volumetric lightmap, per-light shadow depth maps and reflection captures;
* the `LightMapTexture2D` atlases those light maps point at (HQ: PF_DXT5,
  LQ: PF_DXT1), with their streaming mips in the `.ubulk` beside it;
* sometimes `ShadowMapTexture2D` atlases (stationary lights only).

Formats follow the engine source (MapBuildData.cpp, LightMap.cpp,
ShadowMap.cpp, PrecomputedLightVolume.cpp, PrecomputedVolumetricLightmap.cpp,
TextureDerivedData.cpp, BulkData.cpp). The proof is a byte-identical round
trip of uasset, uexp and ubulk:

    python3 lightmaps.py --roundtrip Scenario_09 Scenario_01
    python3 lightmaps.py --dump /Game/Maps/Campaign/Scenario_09/Scenario_09_Castle_BuiltData
"""

import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.formats.ue.uassetio import Package  # noqa: E402

#: FLightMap / FShadowMap type tags (LightMap.h, ShadowMap.h).
LMT_NONE, LMT_1D, LMT_2D = 0, 1, 2
SMT_NONE, SMT_2D = 0, 2
#: NUM_STORED_LIGHTMAP_COEF: HQ colour, HQ directionality, LQ colour, LQ directionality.
NUM_COEF = 4
#: TVolumeLightingSample<3>: position, radius, 3 x 9 SH floats, FColor, uint8.
VOLUME_SAMPLE_BYTES = 12 + 4 + 3 * 9 * 4 + 4 + 1
#: FByteBulkData flags used here (BulkDataCommon.h).
BULK_AT_END = 0x1
BULK_SEPARATE_FILE = 0x100
BULK_OPTIONAL = 0x800
BULK_SIZE64 = 0x2000
BULK_BAD_VERSION = 0x8000
#: Bytes per 4x4 block for the block formats lightmaps use.
BLOCK_BYTES = {'PF_DXT1': 8, 'PF_DXT5': 16, 'PF_BC4': 8, 'PF_BC5': 16}
#: Raw formats, bytes per texel.
RAW_BYTES = {'PF_B8G8R8A8': 4, 'PF_R8G8B8A8': 4, 'PF_G8': 1, 'PF_A8': 1}
#: Classes whose exports are textures with cooked platform data.
#: Environment variable naming a cache folder for extracted packages (a level's
#: `.ubulk` files run to 100+ MB, so keep them off a RAM-backed /tmp).
CACHE_ENV = 'LIGHTMAPS_CACHE'
TEXTURE_CLASSES = ('LightMapTexture2D', 'ShadowMapTexture2D', 'Texture2D',
                   'TextureCube', 'LightMapVirtualTexture2D')


# --------------------------------------------------------------------------- io

class Reader:
    def __init__(self, data, o=0):
        self.b = data
        self.o = o

    def take(self, fmt):
        v = struct.unpack_from(fmt, self.b, self.o)
        self.o += struct.calcsize(fmt)
        return v

    def i32(self):
        return self.take('<i')[0]

    def u32(self):
        return self.take('<I')[0]

    def i64(self):
        return self.take('<q')[0]

    def raw(self, n):
        v = bytes(self.b[self.o:self.o + n])
        if len(v) != n:
            raise ValueError('read past end at %d (+%d)' % (self.o, n))
        self.o += n
        return v

    def guid(self):
        return self.raw(16)

    def fstring_raw(self):
        """An FString kept as its exact bytes (length prefix included)."""
        start = self.o
        n = self.i32()
        self.o += n if n >= 0 else -2 * n
        return bytes(self.b[start:self.o])


def fstring_text(raw):
    n = struct.unpack_from('<i', raw, 0)[0]
    if n >= 0:
        return raw[4:4 + n].split(b'\0')[0].decode('utf-8', 'replace')
    return raw[4:].decode('utf-16-le').rstrip('\0')


def tags_end(data, names, o=0):
    """Offset just past a tagged-property stream's terminating `None`.

    Header sizes follow FPropertyTag: 24 bytes of name/type/size/index, then the
    type's extra fields, then the HasPropertyGuid byte (and a guid if set).
    """
    while True:
        ni = struct.unpack_from('<i', data, o)[0]
        if not 0 <= ni < len(names):
            raise ValueError('bad property name index %d at %d' % (ni, o))
        if names[ni] == 'None':
            return o + 8
        kind = names[struct.unpack_from('<i', data, o + 8)[0]]
        size = struct.unpack_from('<i', data, o + 16)[0]
        p = o + 24
        if kind == 'StructProperty':
            p += 8 + 16
        elif kind == 'BoolProperty':
            p += 1
            size = 0
        elif kind in ('ByteProperty', 'EnumProperty', 'ArrayProperty', 'SetProperty'):
            p += 8
        elif kind == 'MapProperty':
            p += 16
        has_guid = data[p]
        p += 1 + (16 if has_guid else 0)
        o = p + size


# ------------------------------------------------------------- light/shadow maps

def read_lightmap(r):
    """FLightMap* (LightMap.cpp operator<<), cooked for desktop, VT off."""
    kind = r.u32()
    if kind == LMT_NONE:
        return {'type': kind}
    if kind != LMT_2D:
        raise ValueError('unsupported light map type %d' % kind)
    lm = {'type': kind}
    lm['light_guids'] = [r.guid() for _ in range(r.i32())]
    lm['textures'] = list(r.take('<2i'))
    lm['sky_occlusion'], lm['ao_mask'] = r.take('<2i')
    lm['scale'], lm['add'] = [], []
    for _ in range(NUM_COEF):
        lm['scale'].append(r.take('<4f'))
        lm['add'].append(r.take('<4f'))
    lm['coord_scale'] = r.take('<2f')
    lm['coord_bias'] = r.take('<2f')
    lm['shadow_valid'] = r.take('<4I')
    lm['inv_penumbra'] = r.take('<4f')
    lm['virtual_textures'] = list(r.take('<2i'))
    return lm


def write_lightmap(w, lm):
    w += struct.pack('<I', lm['type'])
    if lm['type'] == LMT_NONE:
        return
    w += struct.pack('<i', len(lm['light_guids']))
    w += b''.join(lm['light_guids'])
    w += struct.pack('<2i', *lm['textures'])
    w += struct.pack('<2i', lm['sky_occlusion'], lm['ao_mask'])
    for k in range(NUM_COEF):
        w += struct.pack('<4f', *lm['scale'][k])
        w += struct.pack('<4f', *lm['add'][k])
    w += struct.pack('<2f', *lm['coord_scale'])
    w += struct.pack('<2f', *lm['coord_bias'])
    w += struct.pack('<4I', *lm['shadow_valid'])
    w += struct.pack('<4f', *lm['inv_penumbra'])
    w += struct.pack('<2i', *lm['virtual_textures'])


def read_shadowmap(r):
    """FShadowMap* (ShadowMap.cpp operator<<)."""
    kind = r.u32()
    if kind == SMT_NONE:
        return {'type': kind}
    if kind != SMT_2D:
        raise ValueError('unsupported shadow map type %d' % kind)
    sm = {'type': kind}
    sm['light_guids'] = [r.guid() for _ in range(r.i32())]
    sm['texture'] = r.i32()
    sm['coord_scale'] = r.take('<2f')
    sm['coord_bias'] = r.take('<2f')
    sm['channel_valid'] = r.take('<4I')
    sm['inv_penumbra'] = r.take('<4f')
    return sm


def write_shadowmap(w, sm):
    w += struct.pack('<I', sm['type'])
    if sm['type'] == SMT_NONE:
        return
    w += struct.pack('<i', len(sm['light_guids']))
    w += b''.join(sm['light_guids'])
    w += struct.pack('<i', sm['texture'])
    w += struct.pack('<2f', *sm['coord_scale'])
    w += struct.pack('<2f', *sm['coord_bias'])
    w += struct.pack('<4I', *sm['channel_valid'])
    w += struct.pack('<4f', *sm['inv_penumbra'])


def read_mesh(r):
    """FMeshMapBuildData: light map, shadow map, irrelevant lights, per-instance UV biases."""
    m = {'lightmap': read_lightmap(r), 'shadowmap': read_shadowmap(r)}
    m['irrelevant'] = [r.guid() for _ in range(r.i32())]
    # TArray::BulkSerialize: element size, count, raw elements. FPerInstanceLightmapData
    # is two FVector2D: lightmap UV bias, shadow map UV bias.
    m['instance_elem'] = r.i32()
    n = r.i32()
    m['instances'] = [r.take('<4f') for _ in range(n)]
    if n and m['instance_elem'] != 16:
        raise ValueError('per-instance element size %d' % m['instance_elem'])
    return m


def write_mesh(w, m):
    write_lightmap(w, m['lightmap'])
    write_shadowmap(w, m['shadowmap'])
    w += struct.pack('<i', len(m['irrelevant']))
    w += b''.join(m['irrelevant'])
    w += struct.pack('<ii', m['instance_elem'], len(m['instances']))
    for v in m['instances']:
        w += struct.pack('<4f', *v)


# --------------------------------------------------------------- light volumes

def read_light_volume(r):
    """FPrecomputedLightVolumeData* (the pre-volumetric indirect lighting cache)."""
    start = r.o
    valid = r.u32()
    v = {'valid': valid}
    if valid:
        v['initialized'] = r.u32()
        if v['initialized']:
            v['bounds'] = r.raw(25)
            v['spacing'] = r.raw(4)
            v['num_sh'] = r.i32()
            if v['num_sh'] != 9:
                raise ValueError('light volume with %d SH samples' % v['num_sh'])
            v['hq'] = r.raw(VOLUME_SAMPLE_BYTES * r.i32())
            v['lq'] = r.raw(VOLUME_SAMPLE_BYTES * r.i32())
    v['_span'] = (start, r.o)
    return v


def write_light_volume(w, v):
    w += struct.pack('<I', v['valid'])
    if not v['valid']:
        return
    w += struct.pack('<I', v['initialized'])
    if not v['initialized']:
        return
    w += v['bounds'] + v['spacing'] + struct.pack('<i', v['num_sh'])
    for key in ('hq', 'lq'):
        w += struct.pack('<i', len(v[key]) // VOLUME_SAMPLE_BYTES) + v[key]


def read_layer(r):
    """FVolumetricLightmapDataLayer: TArray<uint8> then the pixel format name."""
    n = r.i32()
    return {'data': r.raw(n), 'format': r.fstring_raw()}


def write_layer(w, layer):
    w += struct.pack('<i', len(layer['data'])) + layer['data'] + layer['format']


#: Brick layers in serialisation order (PrecomputedVolumetricLightmap.cpp).
BRICK_LAYERS = (['ambient'] + ['sh%d' % i for i in range(6)]
                + ['sky_bent_normal', 'directional_shadowing', 'lq_color', 'lq_direction'])


def read_volumetric(r):
    """FPrecomputedVolumetricLightmapData*: indirection texture plus brick layers."""
    valid = r.u32()
    v = {'valid': valid}
    if not valid:
        return v
    v['bounds'] = r.raw(25)
    v['indirection_dims'] = r.take('<3i')
    v['indirection'] = read_layer(r)
    v['brick_size'] = r.i32()
    v['brick_dims'] = r.take('<3i')
    v['layers'] = {}
    for name in BRICK_LAYERS:
        v['layers'][name] = read_layer(r)
    v['sublevel_brick_positions'] = r.raw(12 * r.i32())
    v['indirection_original'] = r.raw(4 * r.i32())
    return v


def write_volumetric(w, v):
    w += struct.pack('<I', v['valid'])
    if not v['valid']:
        return
    w += v['bounds'] + struct.pack('<3i', *v['indirection_dims'])
    write_layer(w, v['indirection'])
    w += struct.pack('<i', v['brick_size']) + struct.pack('<3i', *v['brick_dims'])
    for name in BRICK_LAYERS:
        write_layer(w, v['layers'][name])
    w += struct.pack('<i', len(v['sublevel_brick_positions']) // 12)
    w += v['sublevel_brick_positions']
    w += struct.pack('<i', len(v['indirection_original']) // 4)
    w += v['indirection_original']


def read_light_build(r):
    """FLightComponentMapBuildData: shadow channel, FStaticShadowDepthMapData."""
    d = {'channel': r.i32()}
    d['world_to_light'] = r.raw(64)
    d['size'] = r.take('<2i')
    d['depth'] = r.raw(2 * r.i32())                 # TArray<FFloat16>
    return d


def write_light_build(w, d):
    w += struct.pack('<i', d['channel']) + d['world_to_light']
    w += struct.pack('<2i', *d['size'])
    w += struct.pack('<i', len(d['depth']) // 2) + d['depth']


def read_capture(r):
    """FReflectionCaptureMapBuildData: size, brightness, FullHDR cube data, encoded texture."""
    d = {'size': r.i32()}
    d['average_brightness'], d['brightness'] = r.take('<2f')
    d['full_hdr'] = r.raw(r.i32())
    d['encoded'] = r.i32()                          # UTextureCube* package index
    return d


def write_capture(w, d):
    w += struct.pack('<i2f', d['size'], d['average_brightness'], d['brightness'])
    w += struct.pack('<i', len(d['full_hdr'])) + d['full_hdr']
    w += struct.pack('<i', d['encoded'])


def _read_map(r, value):
    return [(r.guid(), value(r)) for _ in range(r.i32())]


def _write_map(w, items, value):
    w += struct.pack('<i', len(items))
    for key, v in items:
        w += key
        value(w, v)


def read_registry(data, names):
    """UMapBuildDataRegistry::Serialize, after its tagged properties."""
    end = tags_end(data, names)
    r = Reader(data, end)
    reg = {}
    reg['has_guid'] = r.u32()                     # UObject: PossiblySerializeObjectGuid
    if reg['has_guid']:
        reg['object_guid'] = r.guid()
    reg['properties'] = bytes(data[:end])
    reg['strip'] = r.raw(2)                       # FStripDataFlags
    reg['meshes'] = _read_map(r, read_mesh)
    reg['light_volumes'] = _read_map(r, read_light_volume)
    reg['volumetric'] = _read_map(r, read_volumetric)
    reg['lights'] = _read_map(r, read_light_build)
    reg['captures'] = _read_map(r, read_capture)
    reg['sky_atmosphere'] = _read_map(r, lambda _r: None)
    reg['trailer'] = bytes(data[r.o:])
    return reg


def write_registry(reg):
    w = bytearray(reg['properties'])
    w += struct.pack('<I', reg['has_guid'])
    if reg['has_guid']:
        w += reg['object_guid']
    w += reg['strip']
    _write_map(w, reg['meshes'], write_mesh)
    _write_map(w, reg['light_volumes'], write_light_volume)
    _write_map(w, reg['volumetric'], write_volumetric)
    _write_map(w, reg['lights'], write_light_build)
    _write_map(w, reg['captures'], write_capture)
    _write_map(w, reg['sky_atmosphere'], lambda _w, _v: None)
    w += reg['trailer']
    return bytes(w)


# --------------------------------------------------------------------- textures

def read_texture(data, names, base, ubulk, is_lightmap, optional=b''):
    """A cooked UTexture2D export, platform data decoded and mips resolved.

    `base` is the export's absolute offset (header size + its place in the
    .uexp): SkipOffset and inline bulk offsets are absolute positions.
    """
    end = tags_end(data, names)
    r = Reader(data, end)
    t = {'properties': bytes(data[:end])}
    t['has_guid'] = r.u32()
    if t['has_guid']:
        t['object_guid'] = r.guid()
    t['strip'] = r.raw(4)                 # UTexture and UTexture2D FStripDataFlags
    t['cooked'] = r.u32()
    t['formats'] = []
    while True:
        fname = r.take('<2i')
        if names[fname[0]] == 'None':
            t['end_name'] = fname
            break
        skip = r.i64()
        pd = read_platform_data(r, base, ubulk, optional)
        if base + r.o != skip:
            raise ValueError('SkipOffset %d but data ends at %d' % (skip, base + r.o))
        t['formats'].append({'name': fname, 'pixel_format': names[fname[0]], 'data': pd})
    if is_lightmap:
        t['lightmap_flags'] = r.u32()
    t['trailer'] = bytes(data[r.o:])
    return t


def read_platform_data(r, base, ubulk, optional=b''):
    pd = {}
    pd['size'] = r.take('<2i')
    pd['packed'] = r.u32()
    pd['format'] = r.fstring_raw()
    if pd['packed'] & 0x80000000:                # HasOptData
        pd['opt'] = r.raw(8)
    pd['first_mip'] = r.i32()
    pd['mips'] = []
    for _ in range(r.i32()):
        mip = {'cooked': r.u32(), 'flags': r.u32()}
        if mip['flags'] & BULK_SIZE64:
            mip['count'], mip['disk'] = r.take('<2q')
        else:
            mip['count'], mip['disk'] = r.take('<2i')
        mip['offset'] = r.i64()
        if mip['flags'] & BULK_BAD_VERSION:
            raise ValueError('bulk data with BadDataVersion')
        if mip['flags'] & BULK_OPTIONAL:
            mip['payload'] = bytes(optional[mip['offset']:mip['offset'] + mip['disk']])
            mip['where'] = 'uptnl'
        elif mip['flags'] & BULK_SEPARATE_FILE:
            mip['payload'] = bytes(ubulk[mip['offset']:mip['offset'] + mip['disk']])
            mip['where'] = 'ubulk'
        elif mip['flags'] & BULK_AT_END:
            raise ValueError('payload at end of file is not handled')
        else:
            if mip['offset'] != base + r.o:
                raise ValueError('inline bulk offset %d, payload at %d'
                                 % (mip['offset'], base + r.o))
            mip['payload'] = r.raw(mip['disk'])
            mip['where'] = 'inline'
        if len(mip['payload']) != mip['disk']:
            raise ValueError('mip payload short: %d of %d' % (len(mip['payload']), mip['disk']))
        mip['dims'] = r.take('<3i')
        pd['mips'].append(mip)
    pd['virtual'] = r.u32()
    if pd['virtual']:
        raise ValueError('virtual textures are not handled')
    return pd


def write_texture(t, base, ubulk_out, optional_out):
    """Bytes of a texture export placed at absolute offset `base`.

    Separate-file mips are appended to `ubulk_out`, optional ones to `optional_out`
    (bytearrays), in mip order.
    """
    w = bytearray(t['properties'])
    w += struct.pack('<I', t['has_guid'])
    if t['has_guid']:
        w += t['object_guid']
    w += t['strip'] + struct.pack('<I', t['cooked'])
    for f in t['formats']:
        w += struct.pack('<2i', *f['name'])
        skip_at = len(w)
        w += struct.pack('<q', 0)
        pd = f['data']
        w += struct.pack('<2iI', pd['size'][0], pd['size'][1], pd['packed'])
        w += pd['format']
        if 'opt' in pd:
            w += pd['opt']
        w += struct.pack('<ii', pd['first_mip'], len(pd['mips']))
        for mip in pd['mips']:
            w += struct.pack('<II', mip['cooked'], mip['flags'])
            size_fmt = '<2q' if mip['flags'] & BULK_SIZE64 else '<2i'
            payload = mip['payload']
            w += struct.pack(size_fmt, len(payload), len(payload))
            if mip['where'] in ('ubulk', 'uptnl'):
                out = ubulk_out if mip['where'] == 'ubulk' else optional_out
                w += struct.pack('<q', len(out))
                out += payload
            else:
                w += struct.pack('<q', base + len(w) + 8)
                w += payload
            w += struct.pack('<3i', *mip['dims'])
        w += struct.pack('<I', pd['virtual'])
        struct.pack_into('<q', w, skip_at, base + len(w))
    w += struct.pack('<2i', *t['end_name'])
    if 'lightmap_flags' in t:
        w += struct.pack('<I', t['lightmap_flags'])
    w += t['trailer']
    return bytes(w)


# ------------------------------------------------------------------ the package

class BuiltData:
    """One `_BuiltData` package: registry and textures decoded, writable."""

    def __init__(self, uasset, uexp, ubulk=b'', uptnl=b''):
        self.pkg = Package(uasset, uexp)
        self.names = [s for s, _h in self.pkg.names]
        self.ubulk_in = ubulk
        #: the `.uptnl` written by the last write(): optional (top) mips, if any
        self.uptnl = b''
        self.header_size = len(uasset)
        self.kinds = []
        self.registry = None
        self.registry_index = None
        self.textures = {}
        for i, e in enumerate(self.pkg.exports):
            cls = self.class_name(struct.unpack_from('<i', e['meta'], 0)[0])
            self.kinds.append(cls)
            base = e['offset']
            if cls == 'MapBuildDataRegistry':
                self.registry = read_registry(e['data'], self.names)
                self.registry_index = i
            elif cls in ('LightMapTexture2D', 'ShadowMapTexture2D'):
                self.textures[i] = read_texture(e['data'], self.names, base, ubulk,
                                                cls == 'LightMapTexture2D', uptnl)

    def class_name(self, index):
        if index < 0:
            rec = self.pkg.imports[-index - 1]
            return self.names[struct.unpack_from('<i', rec, 20)[0]]
        if index > 0:
            return 'export'
        return 'Class'

    def export_name(self, i):
        name, number = struct.unpack_from('<ii', self.pkg.exports[i]['meta'], 16)
        text = self.names[name]
        return '%s_%d' % (text, number - 1) if number else text

    def texture_ref(self, package_index):
        """The export index a light map's texture reference names, or None."""
        return package_index - 1 if package_index > 0 else None

    def write(self):
        """(uasset, uexp, ubulk) with every decoded export re-serialised; optional
        mips land in `self.uptnl`."""
        # Export sizes do not depend on where they sit, so lay out once with any base,
        # then again at the real offsets.
        ubulk, uptnl = bytearray(), bytearray()
        payloads = self._payloads([0] * len(self.pkg.exports), bytearray(), bytearray())
        for e, data in zip(self.pkg.exports, payloads):
            e['data'] = data
        head, _body = self.pkg.write()
        bases, at = [], len(head)
        for data in payloads:
            bases.append(at)
            at += len(data)
        payloads = self._payloads(bases, ubulk, uptnl)
        for e, data in zip(self.pkg.exports, payloads):
            e['data'] = data
        head, body = self.pkg.write()
        self.uptnl = bytes(uptnl)
        return head, body, bytes(ubulk)

    def _payloads(self, bases, ubulk, uptnl):
        out = []
        for i, e in enumerate(self.pkg.exports):
            if i == self.registry_index:
                out.append(write_registry(self.registry))
            elif i in self.textures:
                out.append(write_texture(self.textures[i], bases[i], ubulk, uptnl))
            else:
                out.append(e['data'])
        return out


def _pak_stem(game_path):
    for mount, root in (('/Game/', '../../../OMD/Content/'), ('/Engine/', '../../../Engine/Content/')):
        if game_path.startswith(mount):
            return root + game_path[len(mount):]
    raise ValueError('not a /Game or /Engine path: %s' % game_path)


def _pak_name(game_path, ext):
    """The pak index name of one file of a package, or None. Optional-payload files
    are indexed with a doubled slash before the file name."""
    from riftlord.core import gamefiles
    stem = _pak_stem(game_path)
    for name in (stem + ext, '%s//%s%s' % (stem.rsplit('/', 1)[0], stem.rsplit('/', 1)[1], ext)):
        if name in gamefiles.index():
            return name
    return None


def fetch(game_path, optional=False):
    """(uasset, uexp, ubulk[, uptnl]) bytes of a shipped package, cached on the 2 TB scratch."""
    from riftlord.core import gamefiles
    from riftlord.core import gamepath
    cache = os.environ.get(CACHE_ENV) or gamepath.scratch('lightmaps', beside=gamepath.CODE_ROOT)
    os.makedirs(cache, exist_ok=True)
    stem = game_path.rsplit('/', 1)[-1]
    out = []
    for ext in ('.uasset', '.uexp', '.ubulk') + (('.uptnl',) if optional else ()):
        local = os.path.join(cache, stem + ext)
        if not os.path.exists(local):
            pak_path = _pak_name(game_path, ext)
            if pak_path is None:
                out.append(b'')
                continue
            data = gamefiles.read(pak_path)
            with open(local + '.part', 'wb') as fh:
                fh.write(data)
            os.replace(local + '.part', local)
        with open(local, 'rb') as fh:
            out.append(fh.read())
    return tuple(out)


def built_data_packages(level):
    """Every `_BuiltData` package of a level (persistent and sublevels), as /Game paths."""
    from riftlord.core import gamefiles
    level = level.rsplit('/', 1)[-1]
    out = []
    for name in gamefiles.index():
        base = name.rsplit('/', 1)[-1]
        if base.startswith(level) and base.endswith('_BuiltData.uasset'):
            rest = base[len(level):-len('_BuiltData.uasset')]
            if not level or rest == '' or rest.startswith('_'):
                for root, mount in (('../../../OMD/Content/', '/Game/'),
                                    ('../../../Engine/Content/', '/Engine/')):
                    if name.startswith(root):
                        out.append(mount + name[len(root):-len('.uasset')])
    return sorted(out)


# ------------------------------------------------------------------- reporting

def summary(bd):
    reg = bd.registry
    lines = []
    lm = [m for _g, m in reg['meshes'] if m['lightmap']['type'] == LMT_2D]
    sm = [m for _g, m in reg['meshes'] if m['shadowmap']['type'] == SMT_2D]
    inst = sum(len(m['instances']) for _g, m in reg['meshes'])
    lines.append('meshes %d (light maps %d, shadow maps %d, instanced entries %d)'
                 % (len(reg['meshes']), len(lm), len(sm), inst))
    for key in ('light_volumes', 'volumetric', 'lights', 'captures', 'sky_atmosphere'):
        lines.append('%s %d' % (key, len(reg[key])))
    for _g, v in reg['volumetric']:
        if v['valid']:
            lines.append('  volumetric: indirection %s brick %d bricks %s; layers %s'
                         % (v['indirection_dims'], v['brick_size'], v['brick_dims'],
                            ', '.join('%s %s %d' % (k, fstring_text(l['format']), len(l['data']))
                                      for k, l in v['layers'].items())))
    for _g, v in reg['light_volumes']:
        if v.get('initialized'):
            lines.append('  light volume: %d HQ, %d LQ samples'
                         % (len(v['hq']) // VOLUME_SAMPLE_BYTES,
                            len(v['lq']) // VOLUME_SAMPLE_BYTES))
    for _g, c in reg['captures']:
        lines.append('  capture: size %d brightness %.3f full HDR %d bytes encoded %d'
                     % (c['size'], c['brightness'], len(c['full_hdr']), c['encoded']))
    for i, t in sorted(bd.textures.items()):
        for f in t['formats']:
            pd = f['data']
            mips = pd['mips']
            lines.append('  %-22s %s %s mips %d (%d in ubulk) top %s'
                         % (bd.export_name(i), bd.kinds[i], f['pixel_format'], len(mips),
                            sum(m['where'] == 'ubulk' for m in mips), mips[0]['dims'][:2]))
    return lines


def roundtrip(game_path):
    """True when the package re-serialises byte-identical (uasset, uexp, ubulk)."""
    ua, ux, ub, uo = fetch(game_path, optional=True)
    bd = BuiltData(ua, ux, ub, uo)
    a, x, b = bd.write()
    same = (a == ua, x == ux, b == ub, bd.uptnl == uo)
    return bd, same


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--roundtrip', nargs='+', metavar='LEVEL')
    ap.add_argument('--all', action='store_true', help='round-trip every _BuiltData package')
    ap.add_argument('--dump', metavar='GAME_PATH')
    args = ap.parse_args(argv)
    if args.all:
        args.roundtrip = ['']
    bad = 0
    if args.dump:
        ua, ux, ub = fetch(args.dump)
        for line in summary(BuiltData(ua, ux, ub)):
            print(line)
    for level in args.roundtrip or ():
        for path in built_data_packages(level):
            try:
                bd, same = roundtrip(path)
            except Exception as exc:                 # noqa: BLE001
                print('FAIL %s: %s: %s' % (path, type(exc).__name__, exc))
                bad += 1
                continue
            leftover = [bd.export_name(i) for i, t in bd.textures.items() if t['trailer']]
            if bd.registry['trailer']:
                leftover.append('registry (%d bytes)' % len(bd.registry['trailer']))
            ok = all(same) and not leftover
            if leftover:
                print('     undecoded bytes after: %s' % ', '.join(leftover))
            bad += not ok
            print('%s %s  uasset %s uexp %s ubulk %s uptnl %s' % (
                'OK  ' if ok else 'DIFF', path, *('same' if s else 'differs' for s in same)))
            for line in summary(bd):
                print('     ' + line)
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
