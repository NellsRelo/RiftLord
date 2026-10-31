"""Copy a shipped material instance under a new path with some parameter values replaced.

Forked from trapmod.clone_material / material_params and the helpers they use
(trapmod, skinmod) on 2026-10-22, so Rift Lord does not import the trap pipeline.
"""

import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.build.minimap import mapdata  # noqa: E402
from riftlord.build import missionproto  # noqa: E402
from riftlord.build import modtable  # noqa: E402
from riftlord.formats.ue.uassetio import Package  # noqa: E402


def leaf(path):
    """An asset path's last part, without any object suffix."""
    return path.rsplit('/', 1)[-1].split('.')[0]


def _path_leaf(path):
    """An asset path's last part, as written (skinmod.leaf)."""
    return path.rsplit('/', 1)[-1]


def shipped(path):
    """A shipped package, (uasset, uexp)."""
    files = mapdata._fetch(path)
    with open(files['.uasset'], 'rb') as fh:
        ua = fh.read()
    with open(files['.uexp'], 'rb') as fh:
        ux = fh.read()
    return ua, ux


def own_names(pkg, old_leaf):
    """Names of a package that carry its asset name."""
    return [s for s, _h in pkg.names if old_leaf in s]


def renamed_package(source, new_path, retarget=None, rename=None):
    """A shipped package (Package, payloads loaded) renamed to new_path, with `retarget`
    {old asset path: new asset path} applied to its imports. Refuses a name that carries
    the asset name and has no rename."""
    pkg = Package(*shipped(source))
    pkg.load_payloads()
    old, new = leaf(source), leaf(new_path)
    exact = {source: new_path, old: new, old + '_C': new + '_C',
             'Default__%s_C' % old: 'Default__%s_C' % new,
             'ExecuteUbergraph_' + old: 'ExecuteUbergraph_' + new}
    stray = [s for s in own_names(pkg, old) if s not in exact]
    if stray:
        raise SystemExit('%s: names carry the asset name but have no rename: %s' % (source, stray))
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
    for was, now in (rename or {}).items():
        if was not in names:
            raise SystemExit('%s has no name %s to rename' % (source, was))
        exact[was] = now
    modtable.retarget(pkg, exact=exact)
    return pkg


def export_by_name(pkg, name):
    """The one export of a package with this object name."""
    names = [s for s, _h in pkg.names]
    got = [e for e in pkg.exports if names[struct.unpack_from('<i', e['meta'], 16)[0]] == name]
    if len(got) != 1:
        raise SystemExit('%d exports are named %s' % (len(got), name))
    return got[0]


def properties(pkg, data):
    """An export's tagged properties: [(name, type, at, head, size)], where the value is
    data[at + head:at + head + size]; and where None ends them."""
    names = [st for st, _h in pkg.names]
    o = 0
    out = []
    while o + 8 <= len(data):
        name = names[struct.unpack_from('<i', data, o)[0]]
        if name == 'None':
            return out, o
        kind = names[struct.unpack_from('<i', data, o + 8)[0]]
        size = struct.unpack_from('<i', data, o + 16)[0]
        head = 24 + 1
        if kind == 'BoolProperty':
            head, size = 26, 0
        elif kind == 'StructProperty':
            head = 24 + 8 + 17
        elif kind in ('ArrayProperty', 'ByteProperty', 'EnumProperty'):
            head = 24 + 8 + 1
        out.append((name, kind, o, head, size))
        o += head + size
    raise SystemExit('properties run past the export')


def import_of(pkg, path):
    """The import reference of an asset by its package path: the import named as the
    asset, whose outer is the package import of that path."""
    names = [st for st, _h in pkg.names]

    def fields(i):
        return struct.unpack('<iiiiiii', pkg.imports[i])

    for i in range(len(pkg.imports)):
        _cp, _n1, _cn, _n2, outer, obj, _n3 = fields(i)
        if names[obj] != _path_leaf(path) or outer >= 0:
            continue
        if names[fields(-outer - 1)[5]] == path:
            return -(i + 1)
    raise SystemExit('no import of %s' % path)


def _import_ref(pkg, path):
    """The import reference of an object by its path (package.Object or package)."""
    package = path.split('.')[0]
    try:
        return import_of(pkg, package)
    except SystemExit:
        raise SystemExit('the material does not import %s; only imported textures can be used' % path)


def _param_name(holder, data, o):
    """The Name inside an FMaterialParameterInfo payload."""
    while True:
        sub, used = missionproto.Table._read_field(holder, data, o)
        if sub is None:
            raise SystemExit('a material parameter without a Name')
        if sub.name == 'Name':
            return holder.names[struct.unpack_from('<i', data, o + len(sub.header))[0]]
        o += used


def clone_material(spec_mat, new_path):
    """A shipped material instance copied to new_path with parameter values replaced:
    `textures` {parameter: texture path the instance already imports}, `vectors`
    {parameter: [r, g, b, a]}, `scalars` {parameter: x}. Only parameters the source
    overrides can be changed."""
    pkg = renamed_package(spec_mat['from'], new_path, spec_mat.get('retarget'))
    export = export_by_name(pkg, leaf(new_path))
    names = [s for s, _h in pkg.names]
    holder = type('Names', (), {'names': names})()
    data = bytearray(export['data'])
    found, _end = properties(pkg, data)
    want = {'TextureParameterValues': spec_mat.get('textures') or {},
            'VectorParameterValues': spec_mat.get('vectors') or {},
            'ScalarParameterValues': spec_mat.get('scalars') or {}}
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
                    label = _param_name(holder, data, body)
                elif sub.name == 'ParameterValue':
                    value_at = (body, sub)
                o += used
            if label in want[pname]:
                body, sub = value_at
                value = want[pname][label]
                if pname == 'ScalarParameterValues':
                    struct.pack_into('<f', data, body, float(value))
                elif pname == 'VectorParameterValues':
                    struct.pack_into('<4f', data, body, *value)
                else:
                    struct.pack_into('<i', data, body, _import_ref(pkg, value))
                done.add((pname, label))
    missing = [(k, p) for k, v in want.items() for p in v if (k, p) not in done]
    if missing:
        raise SystemExit('%s does not override %s' % (spec_mat['from'], missing))
    export['data'] = bytes(data)
    return pkg


def material_params(path):
    """{parameter: value} a shipped material instance overrides: floats for scalars,
    (r, g, b, a) for vectors, import references for textures."""
    pkg = renamed_package(path, path)
    export = export_by_name(pkg, leaf(path))
    names = [s for s, _h in pkg.names]
    holder = type('Names', (), {'names': names})()
    data = export['data']
    found, _end = properties(pkg, data)
    out = {}
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
                    label = _param_name(holder, data, body)
                elif sub.name == 'ParameterValue':
                    value = (struct.unpack_from('<f', data, body)[0] if pname[0] == 'S' else
                             struct.unpack_from('<4f', data, body) if pname[0] == 'V' else
                             struct.unpack_from('<i', data, body)[0])
                o += used
            out[label] = value
    return out
