"""Read <Map>_MapData: rift and door locations, and the minimap's world placement.

UOMDMapData authors what a map picture needs. Each FOMDMapVolumeInfo gives a
Transform, an Extent (half-size, in the volume's local space) and the minimap
UTexture2D, which together define where in the world the texture lies -- so a
world position can be drawn on the real minimap rather than a schematic.
"""

import functools
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.build.navigation import navpatch  # noqa: E402
from riftlord.build.navigation.navpatch import _index, ue, Pak, maps, map_path  # noqa: E402,F401
from riftlord.formats.ue.uheader import parse as parse_header  # noqa: E402
from riftlord.formats.ue.parseuasset import R  # noqa: E402
from riftlord.formats.ue import texture  # noqa: E402

from riftlord.core import gamepath  # noqa: E402

TMP = gamepath.scratch('.mapdata_cache', gamepath.CODE_ROOT)

def _fetch(game_path, exts=('.uasset', '.uexp')):
    idx = _index()
    # An asset a mod brought in is not in the shipped paks, so fall back to the index the
    # GAME sees rather than reporting it absent.
    if game_path + '.uasset' not in idx and game_path + '.umap' not in idx:
        try:
            idx = navpatch.live_index()
        except Exception:                            # noqa: BLE001
            pass
    os.makedirs(TMP, exist_ok=True)
    base = os.path.join(TMP, game_path.strip('/').replace('/', '_'))
    got = {}
    for ext in exts:
        key = game_path + ext
        if key not in idx:
            continue
        p, n = idx[key]
        # Through navpatch, which handles a v4 pak as well as a shipped one: `ue.read`
        # wants `Pak.encoded` and a mod's pak has no encoded index, so a mission's own
        # MapData could not be read at all.
        data = navpatch.read_from_pak(p, n)
        navpatch.write_whole(base + ext, data)
        got[ext] = base + ext
    return got


def _tag(r, names):
    ni = r.i32(); r.i32()
    nm = names[ni] if 0 <= ni < len(names) else '?'
    if nm == 'None':
        return None, None, None
    ti = r.i32(); r.i32()
    tp = names[ti] if 0 <= ti < len(names) else '?'
    size = r.i32(); r.i32()
    sub = None
    if tp == 'BoolProperty':
        r.u8(); r.u8()
        return nm, tp, b''
    if tp == 'StructProperty':
        si = r.i32(); r.i32(); r.raw(17); sub = names[si]
    elif tp in ('ArrayProperty', 'ByteProperty', 'EnumProperty'):
        ii = r.i32(); r.i32(); r.u8(); sub = names[ii]
    else:
        r.u8()
    return nm, (tp, sub), r.raw(size)


def _vectors(payload):
    """A TArray<FVector> payload as a list of tuples."""
    r = R(payload, 0)
    n = r.i32()
    r.raw(8 + 8 + 4 + 4 + 8 + 16 + 1)          # inner element tag
    return [struct.unpack_from('<3f', payload, r.o + i * 12) for i in range(n)]


def _volumes(payload, names):
    """A TArray<FOMDMapVolumeInfo> payload as a list of dicts, one per element.

    Each element is its own tagged property list terminated by 'None', so a map
    with several floors yields several volumes -- reading only the first loses
    every point that belongs to another floor.
    """
    r = R(payload, 0)
    count = r.i32()
    r.raw(8 + 8 + 4 + 4 + 8 + 16 + 1)          # inner element tag
    out = []
    for _ in range(count):
        vol = {}
        while True:
            snm, _stp, sp = _tag(r, names)
            if snm is None:
                break
            if snm == 'Transform':
                t = R(sp, 0)
                while t.o < len(sp) - 4:
                    tn, _tt, tv = _tag(t, names)
                    if tn is None:
                        break
                    if tv and len(tv) == 12:
                        vol[tn.lower()] = struct.unpack('<3f', tv)
                    elif tv and len(tv) == 16:
                        vol[tn.lower()] = struct.unpack('<4f', tv)
            elif snm == 'Extent' and len(sp) == 12:
                vol['extent'] = struct.unpack('<3f', sp)
            elif snm == 'MapTexture':
                # A SoftObjectProperty keeps its asset path in the FName; the
                # trailing FString sub-path is empty, so reading it yields
                # nothing.
                idx = struct.unpack_from('<I', sp, 0)[0]
                vol['texture'] = names[idx] if idx < len(names) else ''
        if vol:
            out.append(vol)
    return out


def read(map_name, data_path=None):
    """{'rifts': [...], 'doors': [...], 'volumes': [{transform, extent, texture}]}

    `data_path` names the MapData package outright, for a map that does not have one of
    its own: a copied level keeps the source map's MapData, because that asset draws the
    minimap and a copy has no minimap image under its own name. The mission row says
    which one, so it is followed rather than guessed from the map's name. Without it,
    the MapData the map's own level imports is read (map_data_path).
    """
    path = data_path or map_data_path(map_name)
    if not path:
        return None
    files = _fetch(path)
    if '.uasset' not in files:
        return None
    return _read_files(files)


def map_data_path(map_name):
    """The MapData package that draws a map's minimap, or None: `<map>_MapData` beside the
    map (every shipped map), else the one the map's level imports (a Rift Lord mission's
    level copy imports `<source>_MapData` from its own folder; another mod's level names
    its own)."""
    try:
        base = map_path(map_name)
    except (KeyError, Exception, SystemExit):           # noqa: BLE001
        return None
    if '.uasset' in _fetch(base + '_MapData', exts=('.uasset',)):
        return base + '_MapData'
    files = _fetch(base, exts=('.umap',))
    if '.umap' not in files:
        return None
    try:
        imports = parse_header(files['.umap'])['imports']
    except Exception:                                    # noqa: BLE001
        return None
    for imp in imports:
        name = imp.get('name') or ''
        if imp.get('cls') == 'Package' and name.endswith('_MapData'):
            return name
    return None


def host_of(map_name):
    """The shipped map a map was made from: itself when shipped, else the map its MapData
    is named after (a level copy's `<source>_MapData`), else None."""
    try:
        if map_name in maps(include_mods=False):
            return map_name
    except (Exception, SystemExit):                      # noqa: BLE001
        return None
    path = map_data_path(map_name)
    leaf = (path or '').rsplit('/', 1)[-1]
    if leaf.endswith('_MapData') and leaf[:-len('_MapData')] in maps(include_mods=False):
        return leaf[:-len('_MapData')]
    return None


def read_bytes(uasset, uexp):
    """`read` for a MapData package in hand (a built pak's), as bytes."""
    import tempfile
    with tempfile.TemporaryDirectory(dir=TMP if os.path.isdir(TMP) else None) as tmp:
        files = {'.uasset': os.path.join(tmp, 'md.uasset'), '.uexp': os.path.join(tmp, 'md.uexp')}
        for ext, blob in (('.uasset', uasset), ('.uexp', uexp)):
            with open(files[ext], 'wb') as fh:
                fh.write(blob)
        return _read_files(files)


def _read_files(files):
    info = parse_header(files['.uasset'])
    names = info['names']
    hdr = os.path.getsize(files['.uasset'])
    with open(files['.uexp'], 'rb') as fh:
        uexp = fh.read()
    e = info['exports'][0]
    r = R(uexp, e['off'] - hdr)

    out = {'rifts': [], 'doors': [], 'volumes': []}
    while True:
        nm, tp, pay = _tag(r, names)
        if nm is None:
            break
        if nm == 'RiftLocations':
            out['rifts'] = _vectors(pay)
        elif nm == 'DoorLocations':
            out['doors'] = _vectors(pay)
        elif nm == 'MapVolumes':
            out['volumes'] = _volumes(pay, names)
    return out


def _strings(data):
    """Every null-terminated FString in a uexp, as (offset, text)."""
    out, o = [], 0
    while o < len(data) - 4:
        n = struct.unpack_from('<i', data, o)[0]
        if 2 <= n <= 240 and o + 4 + n <= len(data):
            raw = data[o + 4:o + 4 + n]
            if raw.endswith(b'\0') and all(32 <= c < 127 for c in raw[:-1]):
                out.append((o, raw[:-1].decode('ascii')))
                o += 4 + n
                continue
        o += 1
    return out


#: Cached. Reading the titles means parsing MissionProtos twice -- once for the row
#: names and levels, once for the FText -- at about 0.3s a call, and the level list,
#: the map picker and every build ask for it.
@functools.lru_cache(maxsize=1)
def mission_names():
    """Map name -> the level's display name, from the MissionProtos DataTable.

    Each row's `MissionName` is an FText written as three FStrings: the
    namespace `MissionProtos`, a key `<map>_Name`, and the source text. The DLC
    campaign keys its rows `DLCScenario_NN` while its folders are named
    `ScenarioDLC_NN`.
    """
    got = mission_names_from_rows()
    if got:
        return got
    files = _fetch('/Game/Data/Campaign/MissionProtos')
    if '.uexp' not in files:
        return {}
    with open(files['.uexp'], 'rb') as fh:
        return mission_names_in(fh.read(), mission_levels())


def _ftext_source(payload):
    """The source string of an FText property payload, or None.

    Flags, a one-byte history type, then three FStrings: namespace, key, source.
    """
    def fstring(buf, o):
        n = struct.unpack_from('<i', buf, o)[0]
        o += 4
        if n >= 0:
            return buf[o:o + max(0, n - 1)].decode('utf-8', 'replace'), o + n
        n = -n
        return (buf[o:o + max(0, (n - 1) * 2)].decode('utf-16-le', 'replace'),
                o + n * 2)
    try:
        buf = bytes(payload)
        o = 5
        _ns, o = fstring(buf, o)
        _key, o = fstring(buf, o)
        src, _o = fstring(buf, o)
        return src or None
    except Exception:                                # noqa: BLE001
        return None


@functools.lru_cache(maxsize=1)
def mission_names_from_rows():
    """{level name: display name}, read from each row's OWN name and level.

    The string scan this replaces keyed titles by the FText **localisation key**,
    which is not the row and not the level: row `Scenario_12_WS` carries the key
    `Scenario_12_Name` and the text "Mage Tower", and plays on **Scenario_15**. Its
    opposite number does the reverse, so Mage Tower and Dragon Boneyard came out
    swapped -- the only two campaign levels with no plain row of their own.

    Precedence matters as much as the mapping. A level can be named by several rows:
    the campaign mission, its `_End` twin, a `_WS` war scenario and any number of
    `WC_NN` weekly challenges -- and `WC_13` plays on Scenario_15, so letting it win
    would title Mage Tower after a weekly challenge. A row whose own name IS the
    level is taken first, then a war scenario, and nothing else is consulted.
    """
    try:
        from riftlord.build import missionproto
        table = missionproto.read()
    except Exception:                                # noqa: BLE001
        return {}
    levels = mission_levels()
    best = {}
    for row in table.rows:
        name = str(row['name'])
        if not (name.startswith('Scenario') or name.startswith('DLCScenario')):
            continue                                 # WC_*, Developer, Random
        level = levels.get(name) or _level_key(name)
        field = next((f for f in row['fields']
                      if getattr(f, 'name', None) == 'MissionName'), None)
        if field is None:
            continue
        title = _ftext_source(field.payload)
        if not title:
            continue
        rank = 0 if _level_key(name) == level else 1
        if level not in best or rank < best[level][0]:
            best[level] = (rank, title)
    return {level: title for level, (_r, title) in best.items()}


@functools.lru_cache(maxsize=1)
def mission_levels():
    """{row name: level name} from each MissionProtos row's own `MissionLevel`.

    THE AUTHORITATIVE MAPPING, and it does not always agree with the row name.
    `Scenario_12_WS` plays on **Scenario_15** and `Scenario_15_WS` plays on
    **Scenario_12** -- the two are genuinely crossed in the shipped table -- so
    deriving the level by stripping `_WS` swapped Mage Tower with Dragon Boneyard.
    Every `WC_NN` weekly-challenge row names a level unrelated to its own name too.

    `MissionLevel` is a `SoftObjectProperty` whose payload is an FName index then a
    number, so it needs the table's name list rather than a string scan.
    """
    try:
        from riftlord.build import missionproto
        table = missionproto.read()
    except Exception:                                # noqa: BLE001
        return {}
    out = {}
    for row in table.rows:
        field = next((f for f in row['fields']
                      if getattr(f, 'name', None) == 'MissionLevel'), None)
        if field is None:
            continue
        try:
            idx, _num = struct.unpack_from('<ii', bytes(field.payload), 0)
        except Exception:                            # noqa: BLE001
            continue
        if not 0 <= idx < len(table.names):
            continue
        got = table.names[idx]
        got = got[0] if isinstance(got, (tuple, list)) else got
        leaf = str(got).rsplit('/', 1)[-1].split('.')[0]
        if leaf:
            out[row['name']] = leaf
    return out


def _level_key(key):
    """The level a mission row's key refers to, by name alone."""
    if key.endswith('_End'):              # the post-mission row repeats the name
        key = key[:-4]
    if key.endswith('_WS'):               # a war scenario's row
        key = key[:-3]
    if key.startswith('DLCScenario_'):
        key = 'ScenarioDLC_' + key[len('DLCScenario_'):]
    return key


def mission_names_in(uexp, levels=None):
    """The same reading, for a MissionProtos that came from somewhere else.

    A mod that adds missions to the shared table rather than to a private one -- Endless
    War adds thirty -- is only visible in the copy the game actually loads, so a caller
    with those bytes in hand needs the row scan without the fetch.

    `levels` is {row name: level} from `mission_levels()`. Given it, a title is filed
    under the level the row actually plays on; without it the row name is all there is
    and the `_WS` rows land on the wrong level.
    """
    runs = _strings(uexp)
    levels = levels or {}
    out = {}
    for i, (_o, text) in enumerate(runs):
        if text != 'MissionProtos' or i + 2 >= len(runs):
            continue
        key, name = runs[i + 1][1], runs[i + 2][1]
        if not key.endswith('_Name'):
            continue
        row = key[:-5]
        out.setdefault(levels.get(row) or _level_key(row), name)
    return out


def _yaw_of(quat):
    """Yaw in radians from a quaternion, which is all a top-down view needs."""
    import math
    if not quat:
        return 0.0
    x, y, z, w = quat
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def projector(volume):
    """World (x, y) -> texture (u, v) in 0..1, or None if the volume is unusable.

    The inverse of the volume's placement: shift by the translation, rotate by
    -yaw, normalise against the extent. The square texture covers a square of
    half-size max(Extent.X, Extent.Y) about the translation on both axes, so a
    non-square volume is not stretched to fill it. Scale3D does not enter; the
    texture's V axis runs with the volume's local +Y, so neither axis is flipped.
    """
    import math
    extent = volume.get('extent')
    if not extent:
        return None
    tx, ty = (volume.get('translation') or (0, 0, 0))[:2]
    yaw = _yaw_of(volume.get('rotation'))
    cos, sin = math.cos(-yaw), math.sin(-yaw)
    ex = ey = max(abs(extent[0]), abs(extent[1]))

    def project(p):
        dx, dy = p[0] - tx, p[1] - ty
        lx = dx * cos - dy * sin
        ly = dx * sin + dy * cos
        return (0.5 + lx / (2.0 * ex), 0.5 + ly / (2.0 * ey))
    return project


def pick_volume(volumes, point):
    """Index of the volume a world point belongs to.

    Prefers a volume that contains the point outright; otherwise the one whose
    floor is nearest in Z.
    """
    best = None
    for i, vol in enumerate(volumes):
        pr = projector(vol)
        if pr is None:
            continue
        u, v = pr(point)
        inxy = -0.05 <= u <= 1.05 and -0.05 <= v <= 1.05
        dz = abs(point[2] - (vol.get('translation') or (0, 0, 0))[2])
        inz = dz <= (vol.get('extent') or (0, 0, 0))[2]
        rank = (0 if (inxy and inz) else 1 if inxy else 2, dz)
        if best is None or rank < best[0]:
            best = (rank, i)
    return best[1] if best else 0


def minimap_image(volume):
    """The volume's minimap as an RGBA image, or None."""
    path = volume.get('texture')
    if not path:
        return None
    game = path.split('.')[0]
    files = _fetch(game, exts=('.uasset', '.uexp', '.ubulk'))
    if '.uasset' not in files or '.uexp' not in files:
        return None
    info = texture.parse(files['.uasset'], files['.uexp'], files.get('.ubulk'))
    return texture.to_image(info)
