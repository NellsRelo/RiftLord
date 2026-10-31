"""The colour a material instance multiplies its base-colour texture by.

    python3 omd3matlook.py /Game/Environments/Props/Mat/Barrel_INST

Master materials multiply the base colour by vector and scalar parameters
(`BC_ColorTint`, `BC_FinalBrightness`, `BC_Brightness`). An instance states only the
parameters it overrides; the rest come from its parent chain and, at the end, from
the master's `CachedExpressionData` defaults. Result: [r, g, b] linear, or None
when every factor is 1.
"""

import functools
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

#: Vector parameters that multiply the base colour.
TINT_VECTORS = ('BC_ColorTint', 'BC_Color', 'ColorTint', 'BC_FinalBrightness')
#: Scalar parameters that multiply the base colour.
TINT_SCALARS = ('BC_Brightness',)
#: Parent links followed before giving up.
MAX_PARENTS = 6
#: Each channel of the result is clamped to this.
TINT_MAX = 4.0


class _Reader:
    def __init__(self, data, names):
        self.d = data
        self.o = 0
        self.names = names

    def i32(self):
        v = struct.unpack_from('<i', self.d, self.o)[0]
        self.o += 4
        return v

    def name(self):
        i = self.i32()
        self.i32()
        return self.names[i] if 0 <= i < len(self.names) else '?'


class _Elements(dict):
    """{array index: value} for a C-array property."""


def _props(r, end, depth=0):
    """{name: value} of a tagged property stream; nested structs and arrays read."""
    out = {}
    while r.o < end:
        name = r.name()
        if name == 'None':
            break
        kind = r.name()
        size = r.i32()
        index = r.i32()
        struct_name = inner = None
        if kind == 'StructProperty':
            struct_name = r.name()
            r.o += 17
        elif kind == 'BoolProperty':
            out[name] = bool(r.d[r.o])
            r.o += 2
            continue
        elif kind in ('ByteProperty', 'EnumProperty'):
            r.name()
            r.o += 1
        elif kind in ('ArrayProperty', 'SetProperty'):
            inner = r.name()
            r.o += 1
        elif kind == 'MapProperty':
            r.name()
            r.name()
            r.o += 1
        else:
            r.o += 1
        start = r.o
        try:
            value = _value(r, kind, struct_name, inner, size, depth)
        except Exception:                            # noqa: BLE001
            value = None
        r.o = start + size
        #: a C-array property is one tag per element, told apart by its index
        if index or name in out and isinstance(out[name], _Elements):
            if not isinstance(out.get(name), _Elements):
                first = out.get(name)
                out[name] = _Elements()
                if first is not None:
                    out[name][0] = first
            out[name][index] = value
        else:
            out[name] = value
    return out


def _struct_value(r, struct_name, end, depth):
    if struct_name == 'LinearColor':
        return list(struct.unpack_from('<4f', r.d, r.o))
    if struct_name in ('Vector', 'Rotator'):
        return list(struct.unpack_from('<3f', r.d, r.o))
    if struct_name == 'Guid':
        return r.d[r.o:r.o + 16].hex()
    return _props(r, end, depth + 1)


def _value(r, kind, struct_name, inner, size, depth):
    end = r.o + size
    if kind == 'FloatProperty':
        return struct.unpack_from('<f', r.d, r.o)[0]
    if kind == 'IntProperty':
        return struct.unpack_from('<i', r.d, r.o)[0]
    if kind == 'NameProperty':
        return r.name()
    if kind == 'ObjectProperty':
        return struct.unpack_from('<i', r.d, r.o)[0]
    if kind == 'StructProperty':
        return _struct_value(r, struct_name, end, depth)
    if kind == 'ArrayProperty':
        n = r.i32()
        if inner == 'StructProperty':
            r.name()                                 # array name
            r.name()                                 # StructProperty
            r.i32()
            r.i32()
            sname = r.name()
            r.o += 17
            items = []
            for _ in range(n):
                at = r.o
                if sname == 'LinearColor':
                    items.append(list(struct.unpack_from('<4f', r.d, r.o)))
                    r.o = at + 16
                elif sname == 'Guid':
                    items.append(r.d[r.o:r.o + 16].hex())
                    r.o = at + 16
                else:
                    items.append(_props(r, end, depth + 1))
            return items
        if inner == 'FloatProperty':
            return list(struct.unpack_from('<%df' % n, r.d, r.o))
        if inner == 'NameProperty':
            return [r.name() for _ in range(n)]
        if inner in ('ObjectProperty', 'IntProperty'):
            return list(struct.unpack_from('<%di' % n, r.d, r.o))
        return None
    return None


def _export_props(path):
    """(names, imports, {property: value}) of a material package's main export."""
    from riftlord.core import gamefiles
    from riftlord.formats.ue.uassetio import Package
    ua, ux = gamefiles.package(path)
    pkg = Package(ua, ux)
    pkg.load_payloads()
    names = [str(n[0]) if isinstance(n, (list, tuple)) else str(n) for n in pkg.names]
    leaf = path.rsplit('/', 1)[-1]
    for e in pkg.exports:
        data = bytes(e.get('data') or b'')
        if not data:
            continue
        ni = struct.unpack_from('<i', bytes(e['meta']), 16)[0]
        if not 0 <= ni < len(names) or names[ni] != leaf:
            continue
        r = _Reader(data, names)
        return names, pkg, _props(r, len(data))
    return names, pkg, {}


def _parent_path(pkg, names, index):
    """`/Game/...` path of a parent material import, or None."""
    if not isinstance(index, int) or index >= 0:
        return None
    try:
        ent = pkg.imports[-index - 1]
        outer = struct.unpack_from('<i', ent, 16)[0]
        if outer < 0:
            pent = pkg.imports[-outer - 1]
            return names[struct.unpack_from('<i', pent, 20)[0]]
    except Exception:                                # noqa: BLE001
        return None
    return None


def _defaults(props):
    """{parameter: value} from a master's CachedExpressionData."""
    ced = props.get('CachedExpressionData') or {}
    params = ced.get('Parameters') or {}
    entries = params.get('RuntimeEntries') or {}
    if not isinstance(entries, dict):
        entries = {0: entries}
    out = {}
    for kind, values in ((0, params.get('ScalarValues') or []),
                         (1, params.get('VectorValues') or [])):
        if not isinstance(entries.get(kind), dict):
            continue
        infos = entries[kind].get('ParameterInfos') or []
        for info, value in zip(infos, values):
            if isinstance(info, dict) and info.get('Name'):
                out.setdefault(info['Name'], value)
    return out


@functools.lru_cache(maxsize=1024)
def tint(path):
    """[r, g, b] base-colour multiplier for a material path, or None when it is 1."""
    found = {}
    cur = path
    for _ in range(MAX_PARENTS):
        if not cur:
            break
        try:
            names, pkg, props = _export_props(cur)
        except Exception:                            # noqa: BLE001
            break
        for v in props.get('VectorParameterValues') or []:
            n = ((v.get('ParameterInfo') or {}).get('Name')) if isinstance(v, dict) else None
            if n in TINT_VECTORS and n not in found:
                found[n] = v.get('ParameterValue')
        for v in props.get('ScalarParameterValues') or []:
            n = ((v.get('ParameterInfo') or {}).get('Name')) if isinstance(v, dict) else None
            if n in TINT_SCALARS and n not in found:
                found[n] = v.get('ParameterValue')
        if 'CachedExpressionData' in props:
            for n, v in _defaults(props).items():
                if (n in TINT_VECTORS or n in TINT_SCALARS) and n not in found:
                    found[n] = v
            break
        cur = _parent_path(pkg, names, props.get('Parent'))
    rgb = [1.0, 1.0, 1.0]
    for n in TINT_VECTORS:
        v = found.get(n)
        if isinstance(v, list) and len(v) >= 3:
            rgb = [rgb[k] * v[k] for k in range(3)]
    for n in TINT_SCALARS:
        v = found.get(n)
        if isinstance(v, (int, float)):
            rgb = [c * v for c in rgb]
    rgb = [round(max(0.0, min(TINT_MAX, c)), 4) for c in rgb]
    if all(abs(c - 1.0) < 1e-3 for c in rgb):
        return None
    return rgb


@functools.lru_cache(maxsize=1024)
def two_sided(path):
    """True when a material draws both faces: the first `BasePropertyOverrides` along the
    parent chain with `bOverride_TwoSided` decides, else the master's `TwoSided`.
    None when the chain cannot be read."""
    cur = path
    for _ in range(MAX_PARENTS):
        if not cur:
            return None
        try:
            names, pkg, props = _export_props(cur)
        except Exception:                            # noqa: BLE001
            return None
        if not props:
            return None
        over = props.get('BasePropertyOverrides')
        if isinstance(over, dict) and over.get('bOverride_TwoSided'):
            return bool(over.get('TwoSided'))
        if 'CachedExpressionData' in props:
            return bool(props.get('TwoSided'))
        cur = _parent_path(pkg, names, props.get('Parent'))
    return None


def explain(path):
    """What `tint` found along the chain, for checking by hand."""
    out = []
    cur = path
    for _ in range(MAX_PARENTS):
        if not cur:
            break
        names, pkg, props = _export_props(cur)
        rec = {'path': cur,
               'vectors': {(v.get('ParameterInfo') or {}).get('Name'): v.get('ParameterValue')
                           for v in props.get('VectorParameterValues') or []
                           if isinstance(v, dict)},
               'scalars': {(v.get('ParameterInfo') or {}).get('Name'): v.get('ParameterValue')
                           for v in props.get('ScalarParameterValues') or []
                           if isinstance(v, dict)}}
        if 'CachedExpressionData' in props:
            d = _defaults(props)
            rec['defaults'] = {k: v for k, v in d.items()
                               if k in TINT_VECTORS or k in TINT_SCALARS}
            rec['default_count'] = len(d)
            out.append(rec)
            break
        out.append(rec)
        cur = _parent_path(pkg, names, props.get('Parent'))
    return out


def main(argv=None):
    import json
    for path in (sys.argv[1:] if argv is None else argv):
        print(json.dumps(explain(path), indent=1, default=str))
        print('tint', tint(path))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
