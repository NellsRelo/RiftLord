"""Vertex paint: one colour over every vertex of a placed static mesh, written as its
component's OverrideVertexColors (what the editor's Mesh Paint stores per placement).

The weathering masters read it (riftlord/mapeditor/surfacelook.py): R lets snow and sand
lie on upward faces, 1 - B lets them cover the whole rough surface, 1 - G lets ice form,
A carries moss on rock. A mesh with no paint reads white. Icebound Mine paints 3,981
placements; most levels none.

Plan data: `look.vertexPaint` {level piece id: [r, g, b, a]} and an added mesh's
`vertexPaint` [r, g, b, a], each 0..1. A level piece the plan names gets the colour in
place of whatever it shipped with; one it does not name keeps its own.

Cooked layout (UE 4.27 StaticMeshComponent.cpp operator<< FStaticMeshComponentLODInfo,
ColorVertexBuffer.cpp Serialize), after the component's tagged properties: int32, int32
LOD count, then per LOD: FStripDataFlags (global, class; class bit 0 = no paint), the
lightmap build Guid unless global bit 1, and unless class bit 0: uint8 load, and when 1
the colour buffer: FStripDataFlags, int32 stride 4, int32 vertices, int32 element size 4,
int32 count, BGRA per vertex. Icebound Mine's painted rocks carry an entry per mesh LOD,
LOD 1 on with a zero Guid; an unpainted placement carries LOD 0 only. Every mesh LOD is
painted here, so the far LODs do not draw white. The vertex count must be the mesh LOD's.
"""

import struct

PLAN_KEY = 'vertexPaint'
#: Strip flags the shipped colour buffers carry (editor data stripped).
BUFFER_FLAGS = b'\x01\x00'
#: Strip flags of a LOD entry a placement did not have (as Icebound Mine's LOD 1 on).
NEW_ENTRY_FLAGS = 1
WHITE = [1.0, 1.0, 1.0, 1.0]


def clean_colour(v):
    """[r, g, b, a] clamped to 0..1, or None."""
    if not isinstance(v, (list, tuple)) or len(v) != 4:
        return None
    try:
        return [round(min(1.0, max(0.0, float(x))), 4) for x in v]
    except (TypeError, ValueError):
        return None


def clean(raw):
    """`look.vertexPaint` validated: {piece id: [r, g, b, a]}, or None."""
    if not isinstance(raw, dict):
        return None
    out = {}
    for k, v in raw.items():
        c = clean_colour(v)
        if c is not None:
            out[str(k)] = c
    return out or None


def lod_vertices(mesh):
    """[vertex count] of each LOD of static mesh `mesh` (object name), or None."""
    from riftlord.formats.omd3 import omd3smwrite
    from riftlord.terrainkit import sources
    info = sources.mesh_info(mesh)
    if not info:
        return None
    try:
        t = omd3smwrite.Template(info['path'])
        return [int(lod['buffers']['pos_meta'][1]) for lod in t.model['lods'] if 'buffers' in lod]
    except (Exception, SystemExit):                  # noqa: BLE001
        return None


def entries(tail):
    """([(global, class, guid bytes or None)], end offset) of a component tail's LOD
    entries; raises ValueError when the bytes are not that layout."""
    if len(tail) < 8:
        raise ValueError('no LOD list')
    n = struct.unpack_from('<i', tail, 4)[0]
    if not 0 <= n <= 16:
        raise ValueError('LOD count %d' % n)
    o, out = 8, []
    for _ in range(n):
        g, c = tail[o], tail[o + 1]
        o += 2
        guid = None
        if not g & 2:
            guid = bytes(tail[o:o + 16])
            o += 16
        if not c & 1:
            load = tail[o]
            o += 1
            if load:
                o += 2
                _stride, nv = struct.unpack_from('<ii', tail, o)
                o += 8
                es, count = struct.unpack_from('<ii', tail, o)
                if es != 4 or count != nv:
                    raise ValueError('colour buffer %d x %d' % (es, count))
                o += 8 + 4 * count
        out.append((g, c, guid))
        if o > len(tail):
            raise ValueError('past the end')
    return out, o


def painted_tail(tail, counts, colour):
    """`tail` with every LOD in `counts` painted `colour` ([r, g, b, a] 0..1)."""
    have, end = entries(tail)
    bgra = bytes(int(round(colour[k] * 255)) for k in (2, 1, 0, 3))
    out = bytearray(tail[:4]) + struct.pack('<i', len(counts))
    for i, nv in enumerate(counts):
        g, _c, guid = have[i] if i < len(have) else (NEW_ENTRY_FLAGS, 0, None)
        out += bytes([g, 0])
        if not g & 2:
            out += guid if guid is not None else b'\0' * 16
        out += b'\x01' + BUFFER_FLAGS + struct.pack('<iiii', 4, nv, 4, nv) + bgra * nv
    out += tail[end:]
    return bytes(out)


def paint(builder, export, mesh, colour):
    """Paint component `export` (1-based) of mesh `mesh`; None, or why it was not."""
    counts = lod_vertices(mesh)
    if not counts:
        return 'no LOD vertex counts for %s' % mesh
    data = bytes(builder.pkg.exports[export - 1]['data'])
    at = builder._terminator(data)
    if at is None:
        return 'no end to its properties'
    head, tail = data[:at + 8], data[at + 8:]
    try:
        new = painted_tail(tail, counts, colour)
    except ValueError as exc:
        return 'its LOD data does not read (%s)' % exc
    builder.pkg.exports[export - 1]['data'] = head + new
    builder.pkg.exports[export - 1]['size'] = len(head) + len(new)
    return None


def level_edits(level_name, plan):
    """{sublevel tag: {component export: (colour, mesh)}} for the level pieces the plan
    paints (paint_target: a component piece, or a Blueprint part's component)."""
    want = clean(((plan or {}).get('look') or {}).get(PLAN_KEY)) or {}
    if not want:
        return {}
    from riftlord.build.world import lavafx
    from riftlord.mapeditor import leveldata
    removed = {r['id'] if isinstance(r, dict) else r for r in (plan or {}).get('removed') or []}
    out = {}
    for p in leveldata.level(level_name)['pieces']:
        colour = want.get(str(p.get('id')))
        target = lavafx.paint_target(p)
        if colour is None or not target or p.get('foliage') or p.get('id') in removed:
            continue
        out.setdefault(p.get('sub') or 'persistent', {})[target] = (colour, p['mesh'])
    return out


def write_all(builder, rows, report):
    """Paint `rows` ({export: (colour, mesh)}) in one package; notes and left_out rows."""
    from riftlord.core import buildmessage
    done = 0
    for export, (colour, mesh) in sorted((rows or {}).items()):
        why = paint(builder, export, mesh, colour)
        if why:
            report['skipped'].append(buildmessage.left_out(
                'the vertex paint on a %s' % mesh, why, 'Reset its Vertex Paint.'))
        else:
            done += 1
    if done:
        report['notes'].append('%d placement(s) vertex painted' % done)
    return done


def paint_added(builder, added, placed, report):
    """Paint the added meshes that carry `vertexPaint` (their root component)."""
    rows = {}
    for n, actor in sorted(placed.items()):
        item = added[n] or {}
        colour = clean_colour(item.get(PLAN_KEY))
        if colour is None or item.get('kind') != 'mesh':
            continue
        comp = builder._root_component(actor)
        if comp:
            rows[comp] = (colour, item.get('name'))
    return write_all(builder, rows, report)


def colours_of(builder, export):
    """[[r, g, b, a] per vertex] of LOD 0 of a component's paint, or None (for tests)."""
    data = bytes(builder.pkg.exports[export - 1]['data'])
    at = builder._terminator(data)
    from riftlord.terrainkit import sources
    tail = data[at + 8:]
    got = sources.override_colours(tail, 0, len(tail))
    return None if got is None else (got / 255.0).tolist()
