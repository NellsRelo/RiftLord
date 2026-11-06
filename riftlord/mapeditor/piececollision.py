"""The Collision switch on an added kit or mesh piece (marker: trapfix).

A plan `added` item of kind 'kit' or 'mesh' with `collision: false` is placed with
collision off: nothing collides with it, so it no longer blocks trap placement, players,
projectiles or the camera, and the walkable-graph rebuild leaves it out (as it leaves out
the level's own NoCollision pieces and a Fab piece its library marks as not colliding).
An item without the key collides, as before.

Written the way the shipped levels switch decor off, measured on Scenario_01/02's
StaticMeshActors (51 of 80 Column_Sec_1_4_RB, 8 of 10 Metal_Base_Plate, 6 of 7
Bracket2_spike): on each static mesh component, `bUseDefaultCollision` false and a
`BodyInstance` whose `CollisionEnabled` and `CollisionProfileName` are NoCollision.
`bUseDefaultCollision` matters: a StaticMeshActor's component defaults it to true, and the
engine then replaces the component's BodyInstance with the mesh's own profile when it
registers (UStaticMeshComponent::OnRegister -> UpdateCollisionFromStaticMesh).

The level's own pieces have the same switch (`edits` entry key `collision`, stored only
when it differs from the shipped state; an edit may carry it with the shipped matrix).
`shipped(level)` reads each placed static mesh component's state as the engine resolves
it: with `bUseDefaultCollision` (stored, else true on a StaticMeshActor's component and
false on any other) the mesh's own profile (its BodySetup `DefaultInstance`), else the
component's `BodyInstance`. `annotate` puts it on the pieces as `collides`; a piece
without it has no switch (Blueprint parts, foliage and instanced pieces, meshes with no
collision shape). `switch_component` writes it: off as above; on, a StaticMeshActor's
component whose mesh collides gets both properties removed (the form 21,939 shipped
StaticMeshActors have), any other gets `bUseDefaultCollision` false and a BodyInstance
naming the mesh's profile, or BlockAll when the mesh's own is NoCollision.

    python3 -m riftlord.mapeditor.piececollision --meshes   # rebuild MESH_TABLE
"""

import functools
import json
import struct

from riftlord.build import levelbuild

#: The plan key on an added item; False switches collision off.
KEY = 'collision'
#: The added kinds the switch applies to (Fab pieces follow their library entry).
KINDS = ('kit', 'mesh')


def is_off(item):
    """True for an added kit or mesh piece placed with collision off."""
    return isinstance(item, dict) and item.get('kind') in KINDS and item.get(KEY) is False


def body_off(builder):
    """A BodyInstance payload with collision off (the NoCollision profile)."""
    def fname(text):
        return struct.pack('<ii', builder.pkg.add_name(text), 0)
    out = bytearray()
    out += fname('CollisionEnabled') + fname('ByteProperty') + struct.pack('<ii', 8, 0)
    out += fname('ECollisionEnabled') + b'\0' + fname('ECollisionEnabled::NoCollision')
    out += fname('CollisionProfileName') + fname('NameProperty') + struct.pack('<ii', 8, 0)
    out += b'\0' + fname('NoCollision')
    out += fname('None')
    return bytes(out)


def static_mesh_components(builder, actor):
    """Package indices of the static mesh components the placed actor owns."""
    pkg = builder.pkg
    out = []
    for i, e in enumerate(pkg.exports):
        if struct.unpack_from('<i', e['meta'], levelbuild.OUTER_AT)[0] != actor:
            continue
        cls = levelbuild.resolve(pkg, struct.unpack_from('<i', e['meta'], 0)[0])[1] or ''
        if cls.endswith('StaticMeshComponent'):
            out.append(i + 1)
    return out


def switch_off(builder, actor):
    """Collision off on every static mesh component of a placed actor. Returns how many."""
    comps = static_mesh_components(builder, actor)
    for c in comps:
        builder.set_bool(c, 'bUseDefaultCollision', False)
        builder.set_property(c, 'BodyInstance', 'StructProperty', body_off(builder),
                             'BodyInstance')
    return len(comps)


# -- the level's own pieces ---------------------------------------------------------------

#: Each catalogued mesh's collision (riftlord/data): {mesh: {shape, profile}}.
MESH_TABLE = 'omd3-mesh-collision.json'
#: The owner class whose component defaults `bUseDefaultCollision` to true.
SMA = 'StaticMeshActor'
#: Values of CollisionProfileName / CollisionEnabled that collide with nothing.
OFF_VALUES = ('NoCollision', 'ECollisionEnabled::NoCollision')
#: The profile an explicit switch-on names when the mesh's own collides with nothing.
ON_PROFILE = 'BlockAll'


def _body(data, names, at, size):
    """{field: FName} of a BodyInstance's CollisionEnabled and CollisionProfileName."""
    from riftlord.formats.omd3 import meshcat
    body = meshcat._walk(data, names, at, at + size)
    out = {}
    for field in ('CollisionEnabled', 'CollisionProfileName'):
        if field in body and body[field][2] >= 8:
            _k, fat, fsize = body[field]
            k = struct.unpack_from('<i', data, fat + fsize - 8)[0]
            out[field] = names[k] if 0 <= k < len(names) else None
    return out


def _off(body):
    return any(v in OFF_VALUES for v in (body or {}).values())


def read_mesh(package_path):
    """{shape, profile} of one static mesh package: `shape` true when its BodySetup has a
    simple collision element or uses its triangles as simple collision, `profile` its
    DefaultInstance's CollisionProfileName ('NoCollision' also when CollisionEnabled is)."""
    from riftlord.core import gamefiles
    from riftlord.formats.omd3 import meshcat
    from riftlord.formats.ue.uassetio import Package
    ua, ux = gamefiles.package(package_path.replace('../../../OMD/Content', '/Game'))
    pkg = Package(ua, ux)
    pkg.load_payloads()
    names = [n for n, _h in pkg.names]
    for e in pkg.exports:
        if levelbuild.resolve(pkg, struct.unpack_from('<i', e['meta'], 0)[0])[1] != 'BodySetup':
            continue
        data = e['data']
        top = meshcat._walk(data, names, 0, len(data))
        shape = False
        if 'AggGeom' in top:
            _k, at, size = top['AggGeom']
            for _n, (_t, eat, _s) in meshcat._walk(data, names, at, at + size).items():
                shape = shape or struct.unpack_from('<i', data, eat)[0] > 0
        if 'CollisionTraceFlag' in top:
            _k, at, size = top['CollisionTraceFlag']
            k = struct.unpack_from('<i', data, at + size - 8)[0]
            shape = shape or (0 <= k < len(names) and names[k] == 'CTF_UseComplexAsSimple')
        body = _body(data, names, *top['DefaultInstance'][1:]) \
            if 'DefaultInstance' in top else {}
        profile = 'NoCollision' if _off(body) else body.get('CollisionProfileName', ON_PROFILE)
        return {'shape': shape, 'profile': profile}
    return {'shape': False, 'profile': 'NoCollision'}


@functools.lru_cache(maxsize=1)
def _table():
    from riftlord.core import gamepath
    try:
        with open(gamepath.data(MESH_TABLE)) as fh:
            return json.load(fh)['meshes']
    except (OSError, ValueError, KeyError):
        return {}


@functools.lru_cache(maxsize=4096)
def mesh_collision(mesh):
    """{shape, profile} of a catalogued mesh (MESH_TABLE, else read), or None."""
    got = _table().get(mesh)
    if got:
        return got
    from riftlord.formats.omd3 import meshcat
    entry = meshcat.load().get(mesh)
    if not entry:
        return None
    try:
        return read_mesh(entry['path'])
    except Exception:                                # noqa: BLE001
        return None


def build_mesh_table():
    """MESH_TABLE for every mesh of the static mesh catalogue. Returns how many."""
    from riftlord.core import gamepath
    from riftlord.formats.omd3 import meshcat
    out = {}
    for name, entry in sorted(meshcat.load().items()):
        try:
            out[name] = read_mesh(entry['path'])
        except Exception:                            # noqa: BLE001
            continue
    with open(gamepath.data(MESH_TABLE), 'w') as fh:
        json.dump({'meshes': out}, fh, sort_keys=True, separators=(',', ':'))
    _table.cache_clear()
    mesh_collision.cache_clear()
    return len(out)


def resolved(udc, body, sma, mesh_info):
    """True when a placed static mesh component collides, as the engine resolves it.
    `udc`: its stored bUseDefaultCollision or None; `body`: its BodyInstance fields
    (_body) or None; `sma`: owned by a StaticMeshActor; `mesh_info`: mesh_collision."""
    if udc is None:
        udc = sma
    if udc and mesh_info:
        return mesh_info['profile'] != 'NoCollision'
    return not _off(body)


#: Mesh component classes that are UStaticMeshComponents (the bUseDefaultCollision
#: rule applies) without 'StaticMeshComponent' in their name.
STATIC_MESH_CLASSES = ('SplineMeshComponent',)


def _component_rows(pkg):
    """{component export: (udc or None, body fields or None, owner is SMA, mesh or None)}
    for every mesh component; one that is not a UStaticMeshComponent gets (None, body,
    False, None), so resolved() takes its BodyInstance."""
    from riftlord.formats.omd3 import meshcat
    names = [n for n, _h in pkg.names]
    out = {}
    for i, e in enumerate(pkg.exports):
        cls = levelbuild.resolve(pkg, struct.unpack_from('<i', e['meta'], 0)[0])[1] or ''
        if 'MeshComponent' not in cls:
            continue
        data = e['data']
        props = meshcat._walk(data, names, 0, len(data))
        body = _body(data, names, *props['BodyInstance'][1:]) \
            if 'BodyInstance' in props else None
        if 'StaticMeshComponent' not in cls and cls not in STATIC_MESH_CLASSES:
            out[i + 1] = (None, body, False, None)
            continue
        outer = struct.unpack_from('<i', e['meta'], levelbuild.OUTER_AT)[0]
        owner = levelbuild.resolve(
            pkg, struct.unpack_from('<i', pkg.exports[outer - 1]['meta'], 0)[0])[1] \
            if 0 < outer <= len(pkg.exports) else None
        udc = bool(data[props['bUseDefaultCollision'][1] - 2]) \
            if 'bUseDefaultCollision' in props else None
        mesh = None
        if 'StaticMesh' in props and props['StaticMesh'][2] == 4:
            ref = struct.unpack_from('<i', data, props['StaticMesh'][1])[0]
            mesh = levelbuild.resolve(pkg, ref)[1] if ref < 0 else None
        out[i + 1] = (udc, body, owner == SMA, mesh)
    return out


@functools.lru_cache(maxsize=8)
def _rows_for(packages):
    """{(sublevel tag, component export): _component_rows row} over `packages`, a tuple
    of (sublevel tag, package path)."""
    from riftlord.core import gamefiles
    from riftlord.formats.ue.uassetio import Package
    out = {}
    for tag, path in packages:
        try:
            ua, ux = gamefiles.package(path)
            pkg = Package(ua, ux)
            pkg.load_payloads()
        except Exception:                            # noqa: BLE001
            continue
        for export, row in _component_rows(pkg).items():
            out[(tag, export)] = row
    return out


def _level_rows(level_name):
    from riftlord.mapeditor import leveldata
    return _rows_for(tuple(sorted((leveldata.level(level_name).get('subPackages')
                                   or {}).items())))


def level_collides(level_name):
    """{(sublevel tag, mesh component export): True/False}: whether each mesh component
    of the level collides, as the engine resolves it (resolved)."""
    return {key: resolved(row[0], row[1], row[2],
                          mesh_collision(row[3]) if row[3] else None)
            for key, row in _level_rows(level_name).items()}


def switchable(piece):
    """True for a level piece placed as a static mesh component of its own (not a
    Blueprint part, foliage or an instance) whose mesh has a collision shape."""
    if piece.get('source') != 'component' or piece.get('foliage') or piece.get('instanced'):
        return False
    info = mesh_collision(piece.get('mesh'))
    return bool(info and info['shape'])


def annotate(pieces, packages):
    """Put `collides` (shipped state) on every switchable piece of a level whose packages
    are `packages` ({sublevel tag: package path}). Returns how many."""
    rows = _rows_for(tuple(sorted((packages or {}).items())))
    n = 0
    for p in pieces:
        if not switchable(p):
            continue
        row = rows.get((p.get('sub') or 'persistent', p.get('export')))
        if row is None:
            continue
        p['collides'] = resolved(row[0], row[1], row[2], mesh_collision(p['mesh']))
        n += 1
    return n


def edited(plan):
    """{piece id: True/False} the plan's `edits` switch collision to."""
    return {e['id']: e[KEY] for e in (plan or {}).get('edits') or []
            if isinstance(e, dict) and 'id' in e and isinstance(e.get(KEY), bool)}


def switched(level_name, plan):
    """{(sublevel tag, component export): (on, mesh)} for the level pieces whose plan
    switch differs from the shipped state."""
    want = edited(plan)
    if not want:
        return {}
    from riftlord.mapeditor import leveldata
    rows = _level_rows(level_name)
    out = {}
    for p in leveldata.level(level_name)['pieces']:
        if p['id'] not in want or not switchable(p):
            continue
        key = (p.get('sub') or 'persistent', p.get('export'))
        row = rows.get(key)
        if row is None:
            continue
        if resolved(row[0], row[1], row[2], mesh_collision(p['mesh'])) != want[p['id']]:
            out[key] = (want[p['id']], p['mesh'])
    return out


def moving_edits(plan, level_name=None):
    """The plan's `edits` that move a piece: all but those carrying only a Collides switch
    (KEY, with the piece's shipped matrix, every entry within geomedits.SAME_PLACE)."""
    from riftlord.mapeditor import geomedits
    from riftlord.mapeditor import leveldata
    edits = [e for e in (plan or {}).get('edits') or [] if isinstance(e, dict) and 'id' in e]
    if not any(KEY in e for e in edits):
        return edits
    try:
        shipped = {p['id']: p['matrix'] for p in
                   leveldata.level(level_name or plan.get('level'))['pieces']}
    except Exception:                                # noqa: BLE001
        return edits

    def still(e):
        m = shipped.get(e['id'])
        return (KEY in e and m is not None and isinstance(e.get('matrix'), list)
                and len(e['matrix']) == len(m)
                and all(abs(a - b) < geomedits.SAME_PLACE for a, b in zip(e['matrix'], m)))
    return [e for e in edits if not still(e)]


def body_profile(builder, profile):
    """A BodyInstance payload naming one collision profile."""
    def fname(text):
        return struct.pack('<ii', builder.pkg.add_name(text), 0)
    return (fname('CollisionProfileName') + fname('NameProperty') + struct.pack('<ii', 8, 0)
            + b'\0' + fname(profile) + fname('None'))


def switch_component(builder, component, on, mesh):
    """Write a placed static mesh component's collision on or off (module notes)."""
    if not on:
        builder.set_bool(component, 'bUseDefaultCollision', False)
        builder.set_property(component, 'BodyInstance', 'StructProperty', body_off(builder),
                             'BodyInstance')
        return
    pkg = builder.pkg
    outer = struct.unpack_from('<i', pkg.exports[component - 1]['meta'],
                               levelbuild.OUTER_AT)[0]
    owner = levelbuild.resolve(
        pkg, struct.unpack_from('<i', pkg.exports[outer - 1]['meta'], 0)[0])[1] \
        if 0 < outer <= len(pkg.exports) else None
    info = mesh_collision(mesh) or {}
    profile = info.get('profile') or ON_PROFILE
    if owner == SMA and profile != 'NoCollision':
        builder.clear_property(component, 'bUseDefaultCollision')
        builder.clear_property(component, 'BodyInstance')
        return
    builder.set_bool(component, 'bUseDefaultCollision', False)
    builder.set_property(component, 'BodyInstance', 'StructProperty',
                         body_profile(builder, profile if profile != 'NoCollision'
                                      else ON_PROFILE), 'BodyInstance')


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--meshes', action='store_true', help='rebuild ' + MESH_TABLE)
    args = ap.parse_args(argv)
    if args.meshes:
        print('%d meshes' % build_mesh_table())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
