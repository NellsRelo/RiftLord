"""Landscape terrain in a cooked OMD3 level, as one drawable mesh per Landscape actor.

    python3 omd3landscape.py /Game/Maps/Campaign/Scenario_08/Scenario_08_Environment

A `Landscape` actor owns `LandscapeComponent`s. Each component is a square of
`ComponentSizeQuads` quads placed at its `RelativeLocation` (in quads) under the
landscape's root, whose scale gives the quad size. Heights come from the component's
`HeightmapTexture`, a Texture2D export in the same package: B8G8R8A8 with the 16-bit
height in R (high byte) and G (low byte), sampled at texel
`vertex + HeightmapScaleBias.zw * texture size`.

The mesh is served under a synthetic name, `MESH_PREFIX + package + '|' + export`.
"""

import functools
import json
import struct
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))

#: Synthetic mesh names start with this.
MESH_PREFIX = '@landscape:'
#: Unreal's landscape height scale: local z = (height - LANDSCAPE_MID) * LANDSCAPE_ZSCALE.
LANDSCAPE_ZSCALE = 1.0 / 128.0
LANDSCAPE_MID = 32768
#: Texture repeats per quad on the drawn terrain.
LANDSCAPE_UV_PER_QUAD = 0.125
#: Inline bulk-data flag of a texture mip.
BULKDATA_IN_SEPARATE_FILE = 0x0100
#: Most vertices one landscape is drawn with: past it, every n-th height is taken
#: (a DLC Sand landscape is 1,024 components of 126 quads, 16.5 million heights).
LANDSCAPE_MAX_VERTS = 600000


def is_landscape_mesh(name):
    return str(name).startswith(MESH_PREFIX)


def _heightmap(info, uexp, hdr, export_index, ubulk=b''):
    """(width, height, bytes BGRA) of the largest mip of a Texture2D export: inline, or
    in the package's `.ubulk` (`ubulk`, its offset absolute in that file) when streamed.
    The DLC Sand levels stream every 128 x 128 heightmap and keep only 64 x 64 inline,
    too small for their 126-quad components."""
    e = info['exports'][export_index]
    start = e['off'] - hdr
    d = uexp[start:start + e['size']]
    at = d.find(b'PF_B8G8R8A8')
    if at < 0:
        return None
    length = struct.unpack_from('<i', d, at - 4)[0]
    o = at + length + 4
    num_mips = struct.unpack_from('<i', d, o)[0]
    o += 4
    best = None
    for _ in range(max(0, min(num_mips, 16))):
        o += 4
        flags, count, on_disk, offset = struct.unpack_from('<iiiq', d, o)
        o += 12 + 8
        if flags & BULKDATA_IN_SEPARATE_FILE:
            data = ubulk[offset:offset + on_disk] if ubulk and offset >= 0 else None
        else:
            data = d[o:o + on_disk]
            o += on_disk
        sx, sy, _sz = struct.unpack_from('<3i', d, o)
        o += 12
        if data is not None and len(data) == sx * sy * 4 == on_disk:
            if best is None or sx * sy > best[0] * best[1]:
                best = (sx, sy, data)
    return best


def landscapes(info, uexp, hdr, props, world):
    """[{'export', 'root', 'material', 'components': [...]}] for every Landscape actor.

    `props(i)` and `world(i)` are omd3level.read_package's readers for export `i`.
    """
    from riftlord.onslaught import onslaught
    out = []
    exports = info['exports']

    def cls(i):
        return str(exports[i].get('class') or exports[i].get('cls') or '')

    for i in range(len(exports)):
        if cls(i) not in ('Landscape', 'LandscapeProxy', 'LandscapeStreamingProxy'):
            continue
        p = props(i)
        root = onslaught.as_int(p['RootComponent'][2]) if 'RootComponent' in p else None
        if not root or not 0 < root <= len(exports):
            continue
        material = None
        if 'LandscapeMaterial' in p:
            idx = onslaught.as_int(p['LandscapeMaterial'][2])
            imports = info.get('imports') or []
            if idx is not None and idx < 0 and -idx - 1 < len(imports):
                material = imports[-idx - 1].get('name')
        out.append({'export': i + 1, 'root': root - 1, 'material': material,
                    'matrix': world(root - 1)})
    return out


def build_mesh(info, uexp, hdr, props, actor_export, ubulk=b''):
    """{'verts', 'tris', 'uvs'} in the landscape root's space, or None. `ubulk` is the
    package's `.ubulk`, for streamed heightmaps."""
    from riftlord.onslaught import onslaught
    exports = info['exports']
    verts, tris, uvs = [], [], []
    comps = []
    for j, e in enumerate(exports):
        if str(e.get('class') or e.get('cls') or '') != 'LandscapeComponent':
            continue
        if e.get('outer') != actor_export:
            continue
        p = props(j)
        size = onslaught.as_int(p['ComponentSizeQuads'][2]) \
            if 'ComponentSizeQuads' in p else None
        tex = onslaught.as_int(p['HeightmapTexture'][2]) \
            if 'HeightmapTexture' in p else None
        if not size or not tex or not 0 < tex <= len(exports):
            continue
        hm = _heightmap(info, uexp, hdr, tex - 1, ubulk)
        if not hm:
            continue
        tw, th, data = hm
        bias = (0.0, 0.0, 0.0, 0.0)
        if 'HeightmapScaleBias' in p and len(p['HeightmapScaleBias'][2]) == 16:
            bias = struct.unpack('<4f', p['HeightmapScaleBias'][2])
        ox = int(round(bias[2] * tw))
        oy = int(round(bias[3] * th))
        loc = onslaught.as_vec(p['RelativeLocation'][2]) \
            if 'RelativeLocation' in p else (0.0, 0.0, 0.0)
        if ox + size + 1 > tw or oy + size + 1 > th:
            continue
        comps.append((size, tw, data, ox, oy, loc))
    # every `step`-th height, so the whole landscape stays within LANDSCAPE_MAX_VERTS
    step = 1
    while sum((-(-size // step) + 1) ** 2 for size, *_ in comps) > LANDSCAPE_MAX_VERTS:
        step += 1
    for size, tw, data, ox, oy, loc in comps:
        at = list(range(0, size, step)) + [size]
        n = len(at)
        base = len(verts)
        for y in at:
            for x in at:
                k = ((oy + y) * tw + (ox + x)) * 4
                h = (data[k + 2] << 8) | data[k + 1]
                vx, vy = loc[0] + x, loc[1] + y
                verts.append([round(vx, 3), round(vy, 3),
                              round(loc[2] + (h - LANDSCAPE_MID) * LANDSCAPE_ZSCALE, 4)])
                uvs.append([round(vx * LANDSCAPE_UV_PER_QUAD, 4),
                            round(vy * LANDSCAPE_UV_PER_QUAD, 4)])
        for y in range(n - 1):
            for x in range(n - 1):
                a = base + y * n + x
                b, c, d = a + 1, a + n, a + n + 1
                tris += [a, c, b, b, c, d]
    if not tris:
        return None
    return {'verts': verts, 'tris': tris, 'uvs': uvs}


def _ubulk(package_path):
    """A map package's `.ubulk` bytes, or b''."""
    try:
        from riftlord.core import gamefiles
        return gamefiles.read(str(package_path).replace('/Game/', '../../../OMD/Content/') +
                              '.ubulk')
    except Exception:                                # noqa: BLE001
        return b''


def mesh_name(package_path, actor_export):
    return '%s%s|%d' % (MESH_PREFIX, package_path, actor_export)


@functools.lru_cache(maxsize=32)
def mesh(name):
    """The served payload for a synthetic landscape mesh name, or None."""
    if not is_landscape_mesh(name):
        return None
    package_path, _, export = name[len(MESH_PREFIX):].rpartition('|')
    from riftlord.formats.omd3 import omd3level
    from riftlord.onslaught import onslaught
    info, uexp, hdr = omd3level._load(package_path)
    cache = {}

    def props(i):
        if i not in cache:
            cache[i] = dict((p[0], p) for p in onslaught.read_props(
                info, uexp, hdr, info['exports'][i]))
        return cache[i]

    got = build_mesh(info, uexp, hdr, props, int(export))
    if not got:
        got = build_mesh(info, uexp, hdr, props, int(export), _ubulk(package_path))
    if not got:
        return None
    material = None
    p = props(int(export) - 1)
    if 'LandscapeMaterial' in p:
        idx = onslaught.as_int(p['LandscapeMaterial'][2])
        imports = info.get('imports') or []
        if idx is not None and idx < 0 and -idx - 1 < len(imports):
            material = imports[-idx - 1].get('name')
    texture = None
    if material:
        try:
            from riftlord.mapeditor import omd3serve
            texture = omd3serve.material_texture(material)
        except Exception:                            # noqa: BLE001
            texture = None
    ntri = len(got['tris']) // 3
    got.update({'material': material, 'texture': texture,
                'slots': [material], 'slotTextures': [texture],
                'sections': [{'slot': 0, 'first': 0, 'tris': ntri}],
                'triangles': ntri, 'vertices': len(got['verts']),
                'landscape': True})
    return got


def main(argv=None):
    from riftlord.formats.omd3 import omd3level
    from riftlord.onslaught import onslaught
    argv = sys.argv[1:] if argv is None else argv
    for pkg in argv:
        info, uexp, hdr = omd3level._load(pkg)
        for i, e in enumerate(info['exports']):
            if str(e.get('class') or e.get('cls') or '') in ('Landscape', 'LandscapeProxy',
                                                            'LandscapeStreamingProxy'):
                m = mesh(mesh_name(pkg, i + 1))
                if not m:
                    print(pkg, i + 1, 'no mesh')
                    continue
                zs = [v[2] for v in m['verts']]
                print(json.dumps({'package': pkg, 'export': i + 1,
                                  'verts': m['vertices'], 'tris': m['triangles'],
                                  'z': [min(zs), max(zs)], 'material': m['material'],
                                  'texture': m['texture']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
