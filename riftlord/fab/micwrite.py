# Generated from OMD2-Converter/thirdparty/fabconvert/micwrite.py by thirdparty/fabconvert/port_riftlord.py; edit the source.
"""Cooked Texture2D and data-only MaterialInstanceConstant packages, cloned from shipped donors.

The writers proven in game by rounds mat1 / mat2 (thirdparty/rounds/mat1/build.py
`write_texture`, `_renamed_package`, `clone_material`, `param_name`; mat2 `add_overrides`,
`write_mic`, `mic_values`), moved here as a library: a written texture is a renamed copy of a
shipped texture of the same format and size with every mip re-encoded (texpatch); a MIC is a
renamed copy of a shipped MIC with its texture imports retargeted, its scalar / vector
overrides set, overrides the donor lacks appended, and (optionally) its Parent import
retargeted onto another master with the same parameter list (mat2 O2: Master_Trap ->
Master_Trap_AlphaMask, proven).

Donors are read only from the SHIPPED paks (gamefiles), never from the live mod index, so the
writer cannot be fed its own output.
"""

import copy
import hashlib
import json
import os
import pickle
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path[:0] = [REPO, os.path.join(REPO, 'UE Assets')]

from riftlord.core import gamefiles                                     # noqa: E402
from riftlord.build import missionproto                                  # noqa: E402
from riftlord.build import modtable                                      # noqa: E402
from riftlord.build import materialclone as skinmod                                       # noqa: E402
from riftlord.build import texpatch                                      # noqa: E402
from riftlord.build import materialclone as trapmod                                       # noqa: E402
from riftlord.core import uetags                                        # noqa: E402
from riftlord.formats.ue.uassetio import Package                         # noqa: E402

#: Texture donors: (kind, size) -> shipped texture. Measured with omd3textures.texture_info
#: (format, size, sRGB, compression, full mip chain); the 1024 BC / MRA / NM, 512 NM / BCA and
#: 256 BC / MRA donors are the ones proven in game by mat1 / mat2.
TEX_DONORS = {
    ('bc', 256): '/Game/MasterMaterials/Shared/Black_BC',                                   # DXT1 sRGB, 9 mips
    ('bc', 512): '/Game/Environments/LandScapes/Foliage/Grass/Mat/Grass_1_BC',              # DXT1 sRGB, 10
    ('bc', 1024): '/Game/Environments/CastleOrder/Archways/CastStone/Mat/Archway_BC',       # DXT1 sRGB, 11
    ('bc', 2048): '/Game/Environments/CastleOrder/Archways/Archway_Pointed/Mat/Archway_Pointed_BC',  # 12
    ('bca', 512): '/Game/Environments/Props/Lighting_Brazier_Large/Mat/Brazier_Logs_EM',    # DXT5 sRGB, 10
    ('bca', 1024): '/Game/Environments/LandScapes/Foliage/Jungle/Mat/T_TropicalLeaves_2_BC',  # DXT5 sRGB, 11
    ('mra', 256): '/Game/MasterMaterials/Shared/Dull_MRA',                                  # DXT1 linear, 9
    ('mra', 1024): '/Game/Environments/CastleOrder/Archways/CastStone/Mat/Archway_MRA',     # DXT1 linear, 11
    ('mra', 2048): '/Game/Environments/CastleOrder/Archways/Archway_Pointed/Mat/Archway_Pointed_MRA',  # 12
    ('nm', 256): '/Game/MasterMaterials/Shared/Flat_NM',                                    # BC5, 9
    ('nm', 512): '/Game/Environments/LandScapes/Foliage/Grass/Mat/Grass_1_NM',              # BC5, 10
    ('nm', 1024): '/Game/MasterMaterials/Enviro/Structure/Textures/RoughStoneGrit_NM',      # BC5, 11
    ('nm', 2048): '/Game/Environments/CastleOrder/Archways/Archway_Pointed/Mat/Archway_Pointed_NM',  # 12
}
#: What each donor must be (checked on every written copy).
TEX_FORMAT = {'bc': 'PF_DXT1', 'bca': 'PF_DXT5', 'mra': 'PF_DXT1', 'nm': 'PF_BC5'}
#: Texture parameters a clone may leave on the donor's texture (shipped shared defaults).
SHARED_OK = ('/Game/MasterMaterials/Shared/', '/Game/Proto/MasterMaterials/Shared/')
MASTERS_JSON = os.path.join(HERE, 'data', 'omd3-masters.json')
#: Masters missing from omd3-masters.json -> one with the same parameter list (catalogue scan).
PARAM_ALIAS = {'/Game/MasterMaterials/Traps/Master_Trap_AlphaMask': '/Game/MasterMaterials/Traps/Master_Trap'}


def sizes(kind):
    return sorted(s for k, s in TEX_DONORS if k == kind)


def leaf(p):
    return p.rsplit('/', 1)[-1].split('.')[0]


def shipped(path, exts=('.uasset', '.uexp', '.ubulk')):
    """A shipped package's files {ext: bytes}, from the shipped paks only."""
    stem = '../../../OMD/Content' + path[len('/Game'):]
    idx = gamefiles.index()
    out = {}
    for e in exts:
        if stem + e in idx:
            out[e] = gamefiles.read(stem + e)
    if '.uasset' not in out:
        raise SystemExit('%s is not in the shipped paks' % path)
    return out


# -- textures ----------------------------------------------------------------------------------------

def write_texture(kind, size, image, new_path, cache_dir):
    """A donor texture renamed to new_path (package AND object: an import by the old object
    name draws black, Techne P4 run 1) with every mip re-encoded from `image` (RGBA PIL).
    Encoded donors are cached by donor + pixels (encoding is the slow part)."""
    from PIL import Image
    container = TEX_DONORS[(kind, size)]
    key = hashlib.sha1(b'v1' + container.encode() + image.tobytes() + repr(image.size).encode()).hexdigest()
    cached = os.path.join(cache_dir, key[:2], key + '.pkl')
    if os.path.exists(cached):
        with open(cached, 'rb') as fh:
            blobs = pickle.load(fh)
    else:
        blobs = shipped(container)
        texpatch.write(blobs, lambda w, h: image if (w, h) == image.size else image.resize((w, h), Image.LANCZOS))
        os.makedirs(os.path.dirname(cached), exist_ok=True)
        tmp = cached + '.%d.tmp' % os.getpid()
        with open(tmp, 'wb') as fh:
            pickle.dump(blobs, fh)
        os.replace(tmp, cached)
    pkg = Package(blobs['.uasset'], blobs['.uexp'])
    modtable.rename_package(pkg, container, leaf(container), new_path, leaf(new_path))
    ua, ux = pkg.write()
    out = {'.uasset': ua, '.uexp': ux}
    if '.ubulk' in blobs:
        out['.ubulk'] = blobs['.ubulk']
    return out


def texture_check(blobs, kind, size):
    fmt, mips = texpatch.mips(blobs)
    probs = []
    if fmt != TEX_FORMAT[kind] or mips[0]['width'] != size or mips[0]['height'] != size:
        probs.append('texture is %s %dx%d, expected %s %d' % (fmt, mips[0]['width'], mips[0]['height'],
                                                              TEX_FORMAT[kind], size))
    return probs


# -- MICs --------------------------------------------------------------------------------------------

def param_name(holder, data, o):
    """The Name inside an FMaterialParameterInfo payload WITH its FName number."""
    while True:
        sub, used = missionproto.Table._read_field(holder, data, o)
        if sub is None:
            raise SystemExit('a material parameter without a Name')
        if sub.name == 'Name':
            idx, num = struct.unpack_from('<ii', data, o + len(sub.header))
            base = holder.names[idx]
            return base if num == 0 else '%s_%d' % (base, num - 1)
        o += used


def _renamed_package(source, new_path, retarget):
    pkg = Package(*[shipped(source)[e] for e in ('.uasset', '.uexp')])
    pkg.load_payloads()
    old, new = leaf(source), leaf(new_path)
    exact = {source: new_path, old: new}
    names = [s for s, _h in pkg.names]
    for was, now in (retarget or {}).items():
        if was not in names or leaf(was) not in names:
            raise SystemExit('%s does not import %s' % (source, was))
        if any(names[struct.unpack_from('<i', e['meta'], 16)[0]] == leaf(was) for e in pkg.exports):
            raise SystemExit('%s: an export shares the name %s' % (source, leaf(was)))
        exact[was], exact[leaf(was)] = now, leaf(now)
        soft_was = '%s.%s' % (was, leaf(was))
        if soft_was in names:
            exact[soft_was] = '%s.%s' % (now, leaf(now))
    stray = [s for s in trapmod.own_names(pkg, old) if s not in exact]
    if stray:
        raise SystemExit('%s: names carry the asset name but have no rename: %s' % (source, stray))
    modtable.retarget(pkg, exact=exact)
    return pkg


def mic_values_pkg(pkg, path):
    """{'scalars', 'vectors', 'textures' (param -> package path), 'parent'} of a MIC export."""
    export = trapmod.export_by_name(pkg, leaf(path))
    names = [s for s, _h in pkg.names]
    holder = type('Names', (), {'names': names})()
    data = export['data']
    found, _end = skinmod.properties(pkg, data)
    out = {'scalars': {}, 'vectors': {}, 'textures': {}, 'parent': None}
    props, _e = uetags.read(data, names, 0)
    par = uetags.get(props, 'Parent')
    if par is not None and par.value < 0:
        imp = struct.unpack('<7i', pkg.imports[-par.value - 1])
        outer = struct.unpack('<7i', pkg.imports[-imp[4] - 1])
        out['parent'] = names[outer[5]]
        if names[imp[5]] != leaf(out['parent']):
            out['parent'] += ' (object %s)' % names[imp[5]]
    for pname, kind, at, head, size in found:
        if kind != 'ArrayProperty' or pname not in ('ScalarParameterValues', 'VectorParameterValues',
                                                    'TextureParameterValues'):
            continue
        count = struct.unpack_from('<i', data, at + head)[0]
        o = at + head + 4 + missionproto.Table.INNER_TAG
        for _ in range(count):
            label, value = None, None
            while True:
                sub, used = missionproto.Table._read_field(holder, data, o)
                if sub is None:
                    o += used
                    break
                body = o + len(sub.header)
                if sub.name == 'ParameterInfo':
                    label = param_name(holder, data, body)
                elif sub.name == 'ParameterValue':
                    if pname[0] == 'S':
                        value = struct.unpack_from('<f', data, body)[0]
                    elif pname[0] == 'V':
                        value = struct.unpack_from('<4f', data, body)
                    else:
                        ref = struct.unpack_from('<i', data, body)[0]
                        imp = struct.unpack('<7i', pkg.imports[-ref - 1])
                        outer = struct.unpack('<7i', pkg.imports[-imp[4] - 1])
                        value = names[outer[5]]
                o += used
            out[{'S': 'scalars', 'V': 'vectors', 'T': 'textures'}[pname[0]]][label] = value
    return out


def mic_values(blobs, path):
    return mic_values_pkg(Package(blobs['.uasset'], blobs['.uexp']), path)


_DONOR_VALUES = {}


def donor_values(path):
    if path not in _DONOR_VALUES:
        b = shipped(path, ('.uasset', '.uexp'))
        _DONOR_VALUES[path] = mic_values(b, path)
    return copy.deepcopy(_DONOR_VALUES[path])


_MASTERS = None


def master_params(master):
    """{'scalar': {name: default}, 'vector': {...}, 'texture': {...}} of a shipped master."""
    global _MASTERS
    if _MASTERS is None:
        with open(MASTERS_JSON) as fh:
            _MASTERS = json.load(fh)['masters']
    p = _MASTERS[PARAM_ALIAS.get(master, master)]['parameters']
    return {k: {x[0]: x[1] for x in (p.get(k) or [])} for k in ('scalar', 'vector', 'texture')}


def _set_values(pkg, path, scalars, vectors):
    """Set overrides the clone already carries (mat1 clone_material's walk)."""
    export = trapmod.export_by_name(pkg, leaf(path))
    names = [s for s, _h in pkg.names]
    holder = type('Names', (), {'names': names})()
    data = bytearray(export['data'])
    found, _end = skinmod.properties(pkg, data)
    want = {'VectorParameterValues': vectors or {}, 'ScalarParameterValues': scalars or {}}
    done = set()
    for pname, kind, at, head, size in found:
        if pname not in want or kind != 'ArrayProperty':
            continue
        count = struct.unpack_from('<i', data, at + head)[0]
        o = at + head + 4 + missionproto.Table.INNER_TAG
        for _ in range(count):
            label, value_at = None, None
            while True:
                sub, used = missionproto.Table._read_field(holder, data, o)
                if sub is None:
                    o += used
                    break
                body = o + len(sub.header)
                if sub.name == 'ParameterInfo':
                    label = param_name(holder, data, body)
                elif sub.name == 'ParameterValue':
                    value_at = body
                o += used
            if label in want[pname]:
                v = want[pname][label]
                if pname == 'ScalarParameterValues':
                    struct.pack_into('<f', data, value_at, float(v))
                else:
                    struct.pack_into('<4f', data, value_at, *[float(x) for x in v])
                done.add((pname, label))
    missing = [(k, p) for k, v in want.items() for p in v if (k, p) not in done]
    if missing:
        raise SystemExit('%s: clone does not carry %s' % (path, missing))
    export['data'] = bytes(data)


def _add_overrides(pkg, path, scalars, vectors, master):
    """Append overrides the donor does not carry (mat2 add_overrides, proven in game O4/O5)."""
    params = master_params(master)
    export = trapmod.export_by_name(pkg, leaf(path))
    names = [n for n, _h in pkg.names]
    props, end = uetags.read(export['data'], names, 0)
    tail = export['data'][end:]
    rebuilt = uetags.write(props, pkg)
    if rebuilt != export['data'][:end]:
        # a retargeted texture whose path the donor already names leaves that name in the table
        # twice; the rebuild then points at the first copy (Docks MI_Styl_Wood_Planks and 13 more,
        # all retargeted to Flat_NM). Same text, other slot: accept only when it decodes equal.
        names = [n for n, _h in pkg.names]
        again, _e = uetags.read(rebuilt + export['data'][end:], names, 0)
        if repr(again) != repr(props):
            raise SystemExit('%s: uetags does not rebuild this MIC (it decodes differently)' % path)
    for arr, adds, kind in (('ScalarParameterValues', scalars, 'scalar'), ('VectorParameterValues', vectors, 'vector')):
        if not adds:
            continue
        p = uetags.get(props, arr)
        if p is None:
            raise SystemExit('%s has no %s to extend' % (path, arr))
        tag, sname, elems, inner = p.value
        for name, value in sorted(adds.items()):
            if name not in params[kind]:
                raise SystemExit('%s: %s is not a %s parameter of %s' % (path, name, kind, master))
            e = copy.deepcopy(elems[0])
            for q in e:
                if q.name == 'ParameterInfo':
                    for r in q.value:
                        if r.name == 'Name':
                            r.value = name
                elif q.name == 'ParameterValue':
                    q.value = float(value) if kind == 'scalar' else struct.pack('<4f', *[float(x) for x in value])
                elif q.name == 'ExpressionGUID':
                    q.value = b'\0' * 16
            elems.append(e)
        p.value = (tag, sname, elems, inner)
    export['data'] = uetags.write(props, pkg) + tail


def write_mic(spec, path):
    """spec: {'donor': shipped MIC, 'master': master the values are checked against,
              'parent': optional new parent master path,
              'textures': {param: package path}, 'scalars': {...}, 'vectors': {...},
              'neutral': True -> every donor scalar / vector the spec does not set goes back to
                         the master default (the clone then looks as its textures say, not as
                         the donor's tuning)}
    Returns ({ext: bytes}, expected values)."""
    donor = donor_values(spec['donor'])
    master = spec['master']
    defaults = master_params(master)
    scal = dict(spec.get('scalars') or {})
    vec = {k: tuple(v) for k, v in (spec.get('vectors') or {}).items()}
    if spec.get('neutral', True):
        for k in donor['scalars']:
            if k not in scal and k in defaults['scalar']:
                scal[k] = defaults['scalar'][k]
        for k in donor['vectors']:
            if k not in vec and k in defaults['vector']:
                vec[k] = tuple(defaults['vector'][k])
    retarget = {}
    for param, now in (spec.get('textures') or {}).items():
        if param not in donor['textures']:
            raise SystemExit('%s: donor %s has no texture parameter %s' % (path, spec['donor'], param))
        retarget[donor['textures'][param]] = now
    for param, was in donor['textures'].items():
        if param not in (spec.get('textures') or {}) and not was.startswith(SHARED_OK):
            raise SystemExit('%s: donor texture %s (%s) is not retargeted' % (path, param, was))
    if len(set(retarget.values())) != len(retarget):
        raise SystemExit('%s: two imports retargeted to one texture' % path)
    if spec.get('parent'):
        retarget[donor['parent']] = spec['parent']
    pkg = _renamed_package(spec['donor'], path, retarget)
    have_s = {k: v for k, v in scal.items() if k in donor['scalars']}
    have_v = {k: v for k, v in vec.items() if k in donor['vectors']}
    _set_values(pkg, path, have_s, have_v)
    # an override equal to the master default changes nothing: it is not added (and the
    # expected values leave it out, as the package will)
    add_s = {k: v for k, v in scal.items() if k not in donor['scalars']
             and not (k in defaults['scalar'] and abs(float(v) - float(defaults['scalar'][k])) < 1e-7)}
    add_v = {k: v for k, v in vec.items() if k not in donor['vectors']
             and not (k in defaults['vector'] and max(abs(float(x) - float(y)) for x, y in
                                                       zip(v, defaults['vector'][k])) < 1e-7)}
    for k in [k for k in scal if k not in donor['scalars'] and k not in add_s]:
        del scal[k]
    for k in [k for k in vec if k not in donor['vectors'] and k not in add_v]:
        del vec[k]
    if add_s or add_v:
        _add_overrides(pkg, path, add_s, add_v, master)
    ua, ux = pkg.write()
    expect = copy.deepcopy(donor)
    expect['scalars'].update(scal)
    expect['vectors'].update(vec)
    for k, v in (spec.get('textures') or {}).items():
        expect['textures'][k] = v
    if spec.get('parent'):
        expect['parent'] = spec['parent']
    return {'.uasset': ua, '.uexp': ux}, expect


def readback_problems(blobs, path, expect, own_root, allowed=()):
    """The written MIC's overrides read from its bytes equal `expect` exactly (none missing,
    none extra), and no texture parameter still names a donor texture."""
    got = mic_values(blobs, path)
    probs = []
    for kind in ('scalars', 'vectors', 'textures'):
        g, w = got[kind], expect[kind]
        if set(g) != set(w):
            probs.append('%s: %s names differ: extra %s missing %s' % (path, kind, sorted(set(g) - set(w)),
                                                                       sorted(set(w) - set(g))))
        for k in set(g) & set(w):
            if kind == 'textures':
                ok = g[k] == w[k]
            elif kind == 'scalars':
                ok = abs(g[k] - w[k]) < 1e-6
            else:
                ok = max(abs(a - b) for a, b in zip(g[k], w[k])) < 1e-6
            if not ok:
                probs.append('%s: %s %s reads back %r, wanted %r' % (path, kind, k, g[k], w[k]))
    if got['parent'] != expect['parent']:
        probs.append('%s: parent reads back %r, wanted %r' % (path, got['parent'], expect['parent']))
    for k, t in got['textures'].items():
        if not t.startswith(own_root) and not t.startswith(SHARED_OK) and t not in allowed:
            probs.append('%s: texture %s points at %s' % (path, k, t))
    return probs
