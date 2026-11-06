"""Geometry edits on a cooked level: moved and removed pieces, per foliage instance,
and the precomputed-visibility reset every moved or added primitive needs.

    apply_edits(builder, edits, report)     # one level's {'moved', 'parked', 'instances'}
    snap = snapshot(builder)  ...  forget_moved_visibility(builder, snap)
"""

import math
import struct

from riftlord.build import levelbuild

#: Far enough below any level that nothing reaches it, and finite (apply.PARKED).
PARKED = (0.0, 0.0, -50000.0)

#: Parking spot for an actor with a minimap marker: PARKED's height, x and y outside
#: every shipped minimap volume (the farthest edge is 21,200 uu from the origin).
PARKED_AWAY = (-400000.0, -400000.0, -50000.0)

#: `VisibilityId` for a primitive the level's precomputed visibility does not cover.
NO_VISIBILITY_ID = -1

#: Bytes per `FInstancedStaticMeshInstanceData`: one float FMatrix.
INSTANCE_STRIDE = 64

#: Bytes per render-side instance origin: float x, y, z and the per-instance random.
ORIGIN_STRIDE = 16

#: Transform properties whose change means a component moved.
TRANSFORM_FIELDS = ('RelativeLocation', 'RelativeRotation', 'RelativeScale3D')

#: Largest difference (uu, degrees or scale) treated as no change.
SAME = 1e-4

#: Largest location difference treated as no move, in uu (float32 at level scale).
SAME_PLACE = 0.01

#: Component class that draws an actor's minimap marker (AOMDDoor, spawners, traps,
#: teleporters, rifts).
MARKER_CLASS = 'OMDMapDisplayComponent'

#: ObjectProperty on BP_OMDTeleportRift_C naming the teleporter at the other end.
TELEPORT_LINK = 'LinkedTeleporter'


def _object_property(builder, index, name):
    """The package index an ObjectProperty holds, or None."""
    data = builder.pkg.exports[index - 1]['data']
    span = builder._tag_span(bytearray(data), name)
    if span is None:
        return None
    return struct.unpack_from('<i', data, span[2])[0]


def hide_markers(builder, actor):
    """Turn off the minimap marker of each map-display component `actor` owns.
    A parked actor's marker still draws at the parked x, y. Returns the count."""
    found = _marker_components(builder, actor)
    for index in found:
        builder.set_bool(index, 'bShowMarker', False)
    return len(found)


def teleport_partner(builder, actor):
    """The teleporter `actor` sends players to, or None for any other actor."""
    target = _object_property(builder, actor, TELEPORT_LINK)
    return target if target and 0 < target <= len(builder.pkg.exports) else None


def _marker_components(builder, actor):
    pkg = builder.pkg
    out = []
    for i, e in enumerate(pkg.exports):
        if struct.unpack_from('<i', e['meta'], levelbuild.OUTER_AT)[0] != actor:
            continue
        cls = struct.unpack_from('<i', e['meta'], levelbuild.CLASS_AT)[0]
        if levelbuild.resolve(pkg, cls)[1] == MARKER_CLASS:
            out.append(i + 1)
    return out


def park_location(builder, actor):
    """PARKED_AWAY for an actor with a minimap marker, else PARKED."""
    return PARKED_AWAY if _marker_components(builder, actor) else PARKED


#: Component class of an actor's sound (an AmbientSound's root, a torch's crackle).
AUDIO_CLASS = 'AudioComponent'


def silence(builder, actor):
    """Set `bAutoActivate` false and `Sound` to none on each AudioComponent `actor` owns,
    so a parked sound neither starts nor plays when a level script calls Play on it (a
    sound with no attenuation is heard wherever it stands). Returns the count."""
    pkg = builder.pkg
    found = []
    for i, e in enumerate(pkg.exports):
        if struct.unpack_from('<i', e['meta'], levelbuild.OUTER_AT)[0] != actor:
            continue
        cls = struct.unpack_from('<i', e['meta'], levelbuild.CLASS_AT)[0]
        if levelbuild.resolve(pkg, cls)[1] == AUDIO_CLASS:
            found.append(i + 1)
    for index in found:
        builder.set_bool(index, 'bAutoActivate', False)
        builder.set_object(index, 'Sound', 0)
    return len(found)


def park(builder, actor, report):
    """Park one actor with its minimap marker off and off every minimap, and its sounds
    off."""
    from riftlord.build import apply
    apply.place_whole(builder, actor, location=park_location(builder, actor))
    hide_markers(builder, actor)
    silence(builder, actor)
    report['parked'] += 1


# -- deletion ------------------------------------------------------------------

#: Removed actors leave their level's actor list (True) or are only parked (False).
DELETE = False

#: Class-name suffix of a component that draws or collides as level geometry.
MESH_COMPONENT_SUFFIX = 'MeshComponent'


def _children(builder):
    """{outer package index: [export package indices]} for the whole package."""
    out = {}
    for i in range(1, len(builder.pkg.exports) + 1):
        out.setdefault(_outer(builder, i), []).append(i)
    return out


def _class_name(builder, index):
    cls = struct.unpack_from('<i', builder.pkg.exports[index - 1]['meta'],
                             levelbuild.CLASS_AT)[0]
    return levelbuild.resolve(builder.pkg, cls)[1] or ''


def owning_actor(builder, export, listed):
    """The listed actor `export` is (or sits under), or None."""
    seen = set()
    i = export
    while i > 0 and i not in seen:
        if i in listed:
            return i
        seen.add(i)
        i = _outer(builder, i)
    return None


def _mesh_components(builder, actor, children):
    out, todo = [], list(children.get(actor, ()))
    while todo:
        i = todo.pop()
        if _class_name(builder, i).endswith(MESH_COMPONENT_SUFFIX):
            out.append(i)
        todo += children.get(i, ())
    return out


def is_gameplay(builder, actor):
    """True for an actor with a minimap marker or a teleporter link."""
    return bool(_marker_components(builder, actor) or teleport_partner(builder, actor))


def delete(builder, export, removed, report, ctx=None):
    """Remove the actor `export` belongs to from its level, or park `export`.

    The actor is deleted when `export` is the actor or every mesh component under it is
    in `removed`; otherwise only `export` is parked. A gameplay actor (minimap marker,
    teleporter) is also parked with its markers off, so an actor iterator that still
    finds the unlisted object finds it out of play. Returns 'deleted' or 'parked'.
    """
    if not DELETE:
        park(builder, export, report)
        return 'parked'
    if ctx is None:
        ctx = {}
    if 'listed' not in ctx:
        ctx['listed'] = set(builder.actor_list())
        ctx['children'] = _children(builder)
    actor = owning_actor(builder, export, ctx['listed'])
    if actor is None or actor == builder.actor_list()[0]:
        park(builder, export, report)
        return 'parked'
    if export != actor:
        meshes = _mesh_components(builder, actor, ctx['children'])
        if not set(meshes) <= set(removed):
            park(builder, export, report)
            return 'parked'
    if actor in ctx.setdefault('done', set()):
        return 'deleted'
    ctx['done'].add(actor)
    if is_gameplay(builder, actor):
        park(builder, actor, report)
        report['parked'] -= 1
    builder.unregister_actor(actor)
    ctx['listed'].discard(actor)
    report['deleted'] = report.get('deleted', 0) + 1
    return 'deleted'


def references_to(builder, targets):
    """{target: [export indices whose tagged properties hold it as an object]}."""
    from riftlord.build.navigation import navvolinject
    targets = set(targets)
    packed = [struct.pack('<i', t) for t in targets]
    out = {}
    for i, e in enumerate(builder.pkg.exports):
        data = e['data']
        if not any(p in data for p in packed):
            continue
        for _nm, kind, _start, end in navvolinject.walk_properties(data, builder.pkg.names):
            if kind != 'ObjectProperty':
                continue
            value = struct.unpack_from('<i', data, end - 4)[0]
            if value in targets and value != i + 1:
                out.setdefault(value, []).append(i + 1)
    return out


# -- 4x4 helpers (nested rows, column vectors; flat lists are column-major) ----

def _rows(flat):
    return [[flat[c * 4 + r] for c in range(4)] for r in range(4)]


def _flat(m):
    return [m[r][c] for c in range(4) for r in range(4)]


def _mul(a, b):
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)]
            for r in range(4)]


def _inverse(m):
    """General 4x4 inverse by Gauss-Jordan."""
    a = [list(row) + [1.0 if r == c else 0.0 for c in range(4)]
         for r, row in enumerate(m)]
    for col in range(4):
        pivot = max(range(col, 4), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise ValueError('singular transform')
        a[col], a[pivot] = a[pivot], a[col]
        p = a[col][col]
        a[col] = [v / p for v in a[col]]
        for r in range(4):
            if r != col and a[r][col]:
                f = a[r][col]
                a[r] = [v - f * w for v, w in zip(a[r], a[col])]
    return [row[4:] for row in a]


def _vector(builder, index, field, default):
    data = bytearray(builder.pkg.exports[index - 1]['data'])
    at = builder._find_vector(data, field)
    return struct.unpack_from('<3f', data, at) if at is not None else default


def relative(builder, index):
    """(location, rotation, scale) a component stores, defaults filled in."""
    return (_vector(builder, index, 'RelativeLocation', (0.0, 0.0, 0.0)),
            _vector(builder, index, 'RelativeRotation', (0.0, 0.0, 0.0)),
            _vector(builder, index, 'RelativeScale3D', (1.0, 1.0, 1.0)))


def _same(a, b):
    return all(abs(float(x) - float(y)) < SAME for x, y in zip(a, b))


def moved_relative(builder, export, matrix, original):
    """(target export, location, rotation or None, scale or None) for a moved piece.

    `matrix` and `original` are the piece's new and shipped WORLD matrices. A
    Blueprint piece is one component of an actor, so the actor's root takes the
    same world-space change: root' = new . original^-1 . root. A component piece
    is its own target: rel' = rel . original^-1 . new. Rotation and scale come
    back None when they did not change.
    """
    from riftlord.build import apply
    from riftlord.formats.omd3 import omd3level
    root = builder._root_component(export)
    target = root or export
    loc, rot, scale = relative(builder, target)
    if not original or len(original) != 16:
        where, r, s = apply.transform_of(matrix)
        return target, where, r, s
    rel = omd3level._matrix(loc, rot, scale)
    new, old = _rows(matrix), _rows(original)
    if root:
        out = _mul(_mul(new, _inverse(old)), rel)
    else:
        out = _mul(_mul(rel, _inverse(old)), new)
    where, r, s = apply.transform_of(_flat(out))
    if all(abs(out[i][j] - rel[i][j]) < SAME for i in range(3) for j in range(3)):
        return target, where, None, None
    turn = lambda angles: omd3level._matrix((0, 0, 0), angles, (1, 1, 1))
    same_turn = all(abs(a - b) < SAME for ra, rb in zip(turn(r), turn(rot))
                    for a, b in zip(ra, rb))
    return (target, where,
            None if same_turn else r,
            None if _same(s, scale) else s)


# -- foliage and other instanced components -----------------------------------

def _tail_start(builder, index):
    """Offset just past an export's tagged-property terminator."""
    from riftlord.build.navigation import navvolinject
    data = builder.pkg.exports[index - 1]['data']
    for name, _kind, _start, end in navvolinject.walk_properties(data, builder.pkg.names):
        if name == 'None':
            return end
    raise ValueError('no property terminator')


def _reorder(builder, index, count):
    """`InstanceReorderTable` (instance -> render slot), or identity."""
    data = bytearray(builder.pkg.exports[index - 1]['data'])
    span = builder._tag_span(data, 'InstanceReorderTable')
    if not span:
        return list(range(count))
    at = span[2]
    n = struct.unpack_from('<i', data, at)[0]
    if n != count:
        raise ValueError('reorder table holds %d entries for %d instances' % (n, count))
    return list(struct.unpack_from('<%di' % n, data, at + 4))


def instance_layout(builder, index):
    """(per-instance offset, count, render origin offset, reorder) for one export.

    `PerInstanceSMData` is (element size 64, count, count FMatrix) after the tagged
    properties; then `PerInstanceSMCustomData` (element size, count, floats); then the
    cooked render data, whose origins are `count` float4 starting 28 bytes in. Accepted
    only when every instance's translation equals its render origin through the reorder
    table, so a misread layout is refused rather than written.
    """
    data = builder.pkg.exports[index - 1]['data']
    tail = _tail_start(builder, index)
    at = data.find(struct.pack('<i', INSTANCE_STRIDE), tail)
    while at >= 0:
        n = struct.unpack_from('<i', data, at + 4)[0]
        if 0 < n and at + 8 + n * INSTANCE_STRIDE <= len(data):
            break
        at = data.find(struct.pack('<i', INSTANCE_STRIDE), at + 1)
    if at < 0:
        raise ValueError('no per-instance data')
    first = at + 8
    after = first + n * INSTANCE_STRIDE
    elem, custom = struct.unpack_from('<ii', data, after)
    render = after + 8 + elem * custom
    head = struct.unpack_from('<qiiiii', data, render)
    if head[2] != n or head[4] != ORIGIN_STRIDE or head[5] != n:
        raise ValueError('unexpected render data header %r' % (head,))
    origins = render + 28
    reorder = _reorder(builder, index, n)
    for k in range(n):
        mine = struct.unpack_from('<3f', data, first + k * INSTANCE_STRIDE + 48)
        drawn = struct.unpack_from('<3f', data, origins + reorder[k] * ORIGIN_STRIDE)
        if mine != drawn:
            raise ValueError('instance %d does not match its render origin' % k)
    return first, n, origins, reorder


def park_instances(builder, index, instances, component_world):
    """Move the listed instances of one instanced component to PARKED.

    Both copies of each instance move: `PerInstanceSMData` (collision, navigation)
    and the cooked render origin. `component_world` is the component's world matrix
    (rows), so the instance lands at PARKED in world space.
    """
    first, n, origins, reorder = instance_layout(builder, index)
    local = _mul(_inverse(component_world), [[1, 0, 0, PARKED[0]], [0, 1, 0, PARKED[1]],
                                             [0, 0, 1, PARKED[2]], [0, 0, 0, 1]])
    where = (local[0][3], local[1][3], local[2][3])
    data = bytearray(builder.pkg.exports[index - 1]['data'])
    done = 0
    for k in sorted(set(instances)):
        if not 0 <= k < n:
            continue
        struct.pack_into('<3f', data, first + k * INSTANCE_STRIDE + 48, *where)
        struct.pack_into('<3f', data, origins + reorder[k] * ORIGIN_STRIDE, *where)
        done += 1
    builder.pkg.exports[index - 1]['data'] = bytes(data)
    return done


def component_world(piece_matrix, builder, index, instance):
    """The component's world matrix (rows), from one instance's world matrix."""
    first, _n, _o, _r = instance_layout(builder, index)
    data = builder.pkg.exports[index - 1]['data']
    f = struct.unpack_from('<16f', data, first + instance * INSTANCE_STRIDE)
    local = [[f[c * 4 + r] for c in range(4)] for r in range(4)]
    return _mul(_rows(piece_matrix), _inverse(local))


# -- precomputed visibility ---------------------------------------------------

def _set_visibility_id(builder, index):
    data = bytearray(builder.pkg.exports[index - 1]['data'])
    span = builder._tag_span(data, 'VisibilityId')
    if not span:
        return False
    at = span[2]
    if struct.unpack_from('<i', data, at)[0] == NO_VISIBILITY_ID:
        return False
    struct.pack_into('<i', data, at, NO_VISIBILITY_ID)
    builder.pkg.exports[index - 1]['data'] = bytes(data)
    return True


def _outer(builder, index):
    return struct.unpack_from('<i', builder.pkg.exports[index - 1]['meta'],
                              levelbuild.OUTER_AT)[0]


def forget_visibility(builder, index):
    """Give an actor's (or one component's) primitives `VisibilityId` -1.

    A level built with precomputed visibility culls each primitive by the cells its
    `VisibilityId` was visible from, so a moved or lifted primitive keeps the old
    position's (or another level's) answer. -1 leaves it to runtime occlusion only.
    """
    owner = index
    if not builder._root_component(index):
        parent = _outer(builder, index)
        if parent > 0 and builder._root_component(parent):
            owner = parent
    count = int(_set_visibility_id(builder, owner))
    for i in range(1, len(builder.pkg.exports) + 1):
        if i != owner and _outer(builder, i) == owner:
            count += int(_set_visibility_id(builder, i))
    return count


def snapshot(builder):
    """What `forget_moved_visibility` compares against: export count and transforms."""
    pkg = builder.pkg
    return len(pkg.exports), [tuple(_vector(builder, i, f, None) for f in TRANSFORM_FIELDS)
                              for i in range(1, len(pkg.exports) + 1)]


def forget_moved_visibility(builder, snap):
    """`forget_visibility` on every actor added or moved since `snap`. Returns a count."""
    count, before = snap
    touched = set()
    for i in range(1, len(builder.pkg.exports) + 1):
        if i > count:
            touched.add(i)
            continue
        now = tuple(_vector(builder, i, f, None) for f in TRANSFORM_FIELDS)
        if now != before[i - 1]:
            touched.add(i)
    done = 0
    for i in sorted(touched):
        done += forget_visibility(builder, i)
    return done


#: `FPrecomputedVisibilityHandler` with no cells (`Invalidate`'s state): bucket origin
#: (2 floats), cell sizes XY and Z (floats), bucket size, bucket count, empty bucket array.
EMPTY_VISIBILITY = struct.pack('<2f2f2ii', 0.0, 0.0, 0.0, 0.0, 0, 0, 0)

#: Bytes per `FPrecomputedVisibilityCell`: FVector Min, uint16 ChunkIndex, uint16 DataOffset.
VISIBILITY_CELL = 16


def visibility_is_stale(plan):
    """True when the plan takes away or moves shipped geometry: the persistent level's
    precomputed visibility then still hides pieces behind walls that are gone, from
    every cell that saw them only through those walls."""
    from riftlord.mapeditor import scratch
    from riftlord.mapeditor import piececollision
    plan = plan or {}
    return bool(plan.get('removed') or piececollision.moving_edits(plan)
                or scratch.is_scratch(plan))


def _fstring_end(data, at):
    n = struct.unpack_from('<i', data, at)[0]
    return at + 4 + (n if n >= 0 else -2 * n)


def visibility_span(builder):
    """(Level export index, start, end) of the persistent Level's
    `FPrecomputedVisibilityHandler` (`ULevel::Serialize`: after the tagged properties,
    Actors, URL, Model, ModelComponents, LevelScriptActor, NavListStart, NavListEnd).
    Raises ValueError when the bytes there do not read as one."""
    from riftlord.core import pkgcheck
    li, e = builder._level_export()
    data = bytes(e['data'])
    w = pkgcheck.Walk(data, [n for n, _h in builder.pkg.names])
    pkgcheck.properties(w, 'Level')
    at = w.o + 4
    at += 4 + 4 * struct.unpack_from('<i', data, at)[0]          # Actors
    for _field in range(4):                                     # URL Protocol Host Map Portal
        at = _fstring_end(data, at)
    ops = struct.unpack_from('<i', data, at)[0]                 # URL Op
    at += 4
    for _op in range(ops):
        at = _fstring_end(data, at)
    at += 8                                                     # URL Port, Valid
    at += 4                                                     # Model
    at += 4 + 4 * struct.unpack_from('<i', data, at)[0]          # ModelComponents
    at += 12                                                    # LevelScriptActor, NavList
    start = at
    try:
        _x, _y, cell_xy, cell_z, _size, _count, buckets = struct.unpack_from(
            '<2f2f2ii', data, at)
        at += len(EMPTY_VISIBILITY)
        if buckets and not (cell_xy > 0 and cell_z > 0):
            raise ValueError('cell size %r x %r' % (cell_xy, cell_z))
        for _b in range(buckets):
            _cell_bytes, cells = struct.unpack_from('<ii', data, at)
            at += 8 + VISIBILITY_CELL * cells
            chunks = struct.unpack_from('<i', data, at)[0]
            at += 4
            for _c in range(chunks):
                compressed, _full, size = struct.unpack_from('<3i', data, at)
                if compressed not in (0, 1) or size < 0:
                    raise ValueError('chunk at %d does not read' % at)
                at += 12 + size
    except struct.error as exc:
        raise ValueError('runs past the export: %s' % exc)
    if at > len(data):
        raise ValueError('runs past the export')
    return li, start, at


def forget_precomputed_visibility(builder):
    """Empty the persistent Level's precomputed visibility, so the game culls by its
    runtime occlusion tests only. Returns the bytes taken out (0 when it had none)."""
    li, start, end = visibility_span(builder)
    data = bytearray(builder.pkg.exports[li]['data'])
    if bytes(data[start:end]) == EMPTY_VISIBILITY:
        return 0
    data[start:end] = EMPTY_VISIBILITY
    builder.pkg.exports[li]['data'] = bytes(data)
    builder.pkg.exports[li]['size'] = len(data)
    return end - start - len(EMPTY_VISIBILITY)


# -- one level's edits ---------------------------------------------------------

def apply_edits(builder, edits, report):
    """Write one level's moved, parked and per-instance edits.

    `edits` is one entry of `apply.sublevel_edits`: 'parked' exports, 'moved'
    (export, matrix, shipped matrix) rows, 'collision' {export: (on, mesh)} and
    'instances' {export: {'parked': [(instance, piece matrix)], 'moved': [...]}}.
    """
    from riftlord.build import apply
    parked = list(edits.get('parked') or [])
    # a teleporter pair works only whole: a kept end would send players to PARKED
    for export in list(parked):
        partner = teleport_partner(builder, export)
        if partner and partner not in parked:
            parked.append(partner)
            report.setdefault('notes', []).append(
                'teleporter %s removed with its other end, %s'
                % (levelbuild._name_of(builder.pkg, export - 1),
                   levelbuild._name_of(builder.pkg, partner - 1)))
    ctx = {}
    for export in parked:
        delete(builder, export, parked, report, ctx)
    gone = ctx.get('done') or set()
    held = {t: [r for r in refs if r not in gone and owning_actor(
                builder, r, gone) is None]
            for t, refs in references_to(builder, gone).items()}
    for target, refs in sorted(held.items()):
        if refs:
            report.setdefault('notes', []).append(
                'deleted %s is still named by %s' % (
                    levelbuild._name_of(builder.pkg, target - 1),
                    ', '.join(levelbuild._name_of(builder.pkg, r - 1) for r in refs[:4])))
    for row in edits.get('moved') or []:
        export, matrix = row[0], row[1]
        if export in parked:
            continue
        original = row[2] if len(row) > 2 else None
        try:
            target, where, rot, scale = moved_relative(builder, export, matrix, original)
        except ValueError as exc:
            from riftlord.core import buildmessage
            from riftlord.build import levelbuild
            report['skipped'].append(buildmessage.left_out(
                'the level piece %s' % levelbuild._name_of(builder.pkg, export - 1),
                'export %d: %s' % (export, exc), effect='it stays where the level has it',
                verb='move'))
            continue
        if rot is None and scale is None and all(
                abs(p - q) < SAME_PLACE for p, q in zip(where, relative(builder, target)[0])):
            continue
        apply.place_whole(builder, target, location=where, rotation=rot, scale=scale)
        report['moved'] += 1
    #: the level's own pieces' Collides switch (riftlord/mapeditor/piececollision.py)
    from riftlord.mapeditor import piececollision
    for export, (on, mesh) in sorted((edits.get('collision') or {}).items()):
        if export in parked:
            continue
        piececollision.switch_component(builder, export, on, mesh)
        report['collision_switched'] = report.get('collision_switched', 0) + 1
    for export, rows in (edits.get('instances') or {}).items():
        parked = rows.get('parked') or []
        if rows.get('moved'):
            from riftlord.core import buildmessage
            report['skipped'].append(buildmessage.left_out(
                'the move of %d foliage instance%s' % (len(rows['moved']),
                                                       '' if len(rows['moved']) == 1 else 's'),
                'export %d' % export, 'Foliage the level ships with can be removed but not moved; '
                'move it back, or remove it and add new foliage.'))
        if not parked:
            continue
        try:
            world = component_world(parked[0][1], builder, export, parked[0][0])
            got = park_instances(builder, export, [k for k, _m in parked], world)
        except ValueError as exc:
            from riftlord.core import buildmessage
            report['skipped'].append(buildmessage.left_out(
                'removing %d foliage instance%s' % (len(parked), '' if len(parked) == 1 else 's'),
                'export %d: %s' % (export, exc)))
            continue
        report['parked'] += got
        report.setdefault('instances', 0)
        report['instances'] += got
