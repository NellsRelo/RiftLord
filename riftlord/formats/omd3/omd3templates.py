"""Component templates of a Blueprint class, every scene component, merged over ancestry.

    python3 omd3templates.py Trapezoid_Landing_C

A placed Blueprint's components appear in the level as exports of their own, outered
to the actor, and each states only what differs from its template: usually just
`AttachParent`. The rest -- mesh, material and relative transform -- comes from the
class's component template of the same name. Scene components with no mesh are kept,
because a mesh may be attached to one (`Ceiling_RB_MT_v1_4x4_C` hangs its trim off
`Metal` and `CastTrim`; `Trapezoid_Landing_C` hangs its floor off `Floor_Sec_BAse`).

Result per class: {component name: {field: value}}, fields only where some class in
the chain states them. Fields: loc, rot, scale, mesh, skeletal, material,
child_class, visible, hidden, particle (a particle component's system).
"""

import functools
import json
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.core import gamepath  # noqa: E402

#: The on-disk cache, one JSON file for every class read so far.
CACHE = os.path.join(gamepath.scratch('cache', gamepath.CODE_ROOT), 'omd3-component-templates.json')
#: Bumped when the record shape changes, so an old cache is discarded.
CACHE_VERSION = 4

#: Component suffix the cooker adds to a component promoted to a variable.
GEN_SUFFIX = '_GEN_VARIABLE'

_lock = threading.Lock()
_own = None
_dirty = False


def _load_cache():
    global _own
    if _own is not None:
        return _own
    _own = {}
    try:
        with open(CACHE) as fh:
            got = json.load(fh)
        if got.get('version') == CACHE_VERSION:
            _own = got.get('classes') or {}
    except Exception:                                # noqa: BLE001
        pass
    return _own


def save():
    """Write the cache if anything was added since the last write."""
    global _dirty
    with _lock:
        if not _dirty:
            return
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        tmp = '%s.%d.tmp' % (CACHE, os.getpid())
        with open(tmp, 'w') as fh:
            json.dump({'version': CACHE_VERSION, 'classes': _own}, fh)
        os.replace(tmp, CACHE)
        _dirty = False


def body_fields(raw, names):
    """{CollisionEnabled, CollisionProfileName: FName} stated by a BodyInstance struct's
    tagged properties `raw`; `names` the package's name table."""
    import struct
    from riftlord.formats.omd3 import meshcat
    names = [n[0] if isinstance(n, (list, tuple)) else n for n in names]
    body = meshcat._walk(raw, names, 0, len(raw))
    out = {}
    for field in ('CollisionEnabled', 'CollisionProfileName'):
        if field in body and body[field][2] >= 8:
            _kind, at, size = body[field]
            k = struct.unpack_from('<i', raw, at + size - 8)[0]
            if 0 <= k < len(names):
                out[field] = str(names[k])
    return out


def _read_own(class_name):
    """{component: fields} stated by this class alone."""
    from riftlord.formats.omd3 import omd3actors
    from riftlord.onslaught import onslaught
    got = omd3actors._header_of(class_name)
    if not got:
        return {}
    info, ux, hdr = got
    imports = info.get('imports') or []

    def import_name(index):
        i = -index - 1
        if 0 <= i < len(imports) and isinstance(imports[i], dict):
            return imports[i].get('name') or imports[i].get('object_name')
        return None

    out = {}
    for e in info['exports']:
        kind = str(e.get('class') or e.get('cls') or '')
        if not kind.endswith('Component'):
            continue
        name = str(e.get('name') or '')
        if not name:
            continue
        props = dict((p[0], p) for p in onslaught.read_props(info, ux, hdr, e))
        rec = {}
        for field, key in (('RelativeLocation', 'loc'), ('RelativeRotation', 'rot'),
                           ('RelativeScale3D', 'scale')):
            if field in props:
                v = onslaught.as_vec(props[field][2])
                if v:
                    rec[key] = list(v)
        skeletal = 'SkeletalMeshComponent' in kind
        ref = props.get('SkeletalMesh' if skeletal else 'StaticMesh')
        if ref:
            idx = onslaught.as_int(ref[2])
            if idx is not None and idx < 0:
                mesh = import_name(idx)
                if mesh:
                    rec['mesh'] = mesh
                    if skeletal:
                        rec['skeletal'] = True
        mat = omd3actors._override_material(props, import_name)
        if mat:
            rec['material'] = mat
        #: a box's half size, and the collision its BodyInstance states
        if 'BoxExtent' in props:
            v = onslaught.as_vec(props['BoxExtent'][2])
            if v:
                rec['extent'] = list(v)
        if isinstance((props.get('BodyInstance') or (0, 0, None))[2], (bytes, bytearray)):
            body = body_fields(bytes(props['BodyInstance'][2]), info.get('names') or [])
            if body:
                rec['body'] = body
        for field, key in (('bVisible', 'visible'), ('bHiddenInGame', 'hidden')):
            if field in props and isinstance(props[field][2], int):
                rec[key] = bool(props[field][2])
        #: a particle component's system: Template (Cascade) or Asset (Niagara)
        for field in ('Template', 'Asset'):
            if field in props and ('ParticleSystemComponent' in kind
                                   or 'NiagaraComponent' in kind):
                idx = onslaught.as_int(props[field][2])
                if idx is not None and idx < 0:
                    system = import_name(idx)
                    if system:
                        rec['particle'] = system
        if 'ChildActorClass' in props:
            idx = onslaught.as_int(props['ChildActorClass'][2])
            if idx is not None and idx < 0:
                child = import_name(idx)
                if child:
                    rec['child_class'] = child
        key = name[:-len(GEN_SUFFIX)] if name.endswith(GEN_SUFFIX) else name
        out.setdefault(key, {}).update(rec)
    return out


def own(class_name):
    """{component: fields} stated by this class alone, cached."""
    global _dirty
    cache = _load_cache()
    with _lock:
        if class_name in cache:
            return cache[class_name]
    got = _read_own(class_name)
    with _lock:
        cache[class_name] = got
        _dirty = True
    return got


def drawn(instance_props, template):
    """False when a component is hidden in game by its instance or its template."""
    for field, key, off in (('bVisible', 'visible', 0), ('bHiddenInGame', 'hidden', 1)):
        if field in instance_props and isinstance(instance_props[field][2], int):
            if bool(instance_props[field][2]) == bool(off):
                return False
        elif key in template and template[key] == bool(off):
            return False
    return True


@functools.lru_cache(maxsize=4096)
def templates(class_name):
    """{component: fields} for a class, ancestor first and the leaf winning per field."""
    from riftlord.formats.omd3 import omd3actors
    merged = {}
    for cls in reversed(omd3actors.ancestry(class_name)):
        for name, rec in own(cls).items():
            merged.setdefault(name, {}).update(rec)
    return merged


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    for cls in argv:
        print(json.dumps(templates(cls), indent=1))
    save()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
