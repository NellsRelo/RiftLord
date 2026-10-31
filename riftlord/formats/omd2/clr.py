"""Helpers for the .NET container types that vForge's Layer files serialise.

``nrbf.py`` reproduces the stream faithfully, which means an ``ArrayList`` arrives
as an object with ``_items``/``_size`` rather than as a list. These functions turn
the common shapes into ordinary Python values.
"""

from riftlord.formats.omd2.nrbf import Instance, Array


def items(obj):
    """Elements of a System.Collections.ArrayList (or anything list-like)."""
    if obj is None:
        return []
    if isinstance(obj, Array):
        return list(obj.values)
    if isinstance(obj, Instance):
        arr = obj.get('_items')
        n = obj.get('_size')
        vals = list(arr.values) if isinstance(arr, Array) else []
        return vals[:n] if isinstance(n, int) else vals
    return []


def vec3(obj):
    """CSharpFramework.Math.Vector3F -> (x, y, z), or None."""
    if not isinstance(obj, Instance):
        return None
    return (obj.get('X', 0.0), obj.get('Y', 0.0), obj.get('Z', 0.0))


def dynamic_properties(shape):
    """A shape's entity properties as a plain {name: value} dict.

    Values stay as parsed: numbers and strings come through directly, a Vector3F
    becomes a tuple, and anything else is left as its Instance.
    """
    out = {}
    coll = shape.get('_entityProperties') if isinstance(shape, Instance) else None
    if not isinstance(coll, Instance):
        return out
    for prop in items(coll.get('_properties')):
        if not isinstance(prop, Instance):
            continue
        ptype = prop.get('_propertyType')
        name = ptype.get('_name') if isinstance(ptype, Instance) else None
        if name is None:
            continue
        value = prop.get('_value')
        if isinstance(value, Instance) and value.cls.endswith('Vector3F'):
            value = vec3(value)
        out[name] = value
    return out


def parent_chain(shape):
    """A shape's ancestors, nearest first, following _parentShape."""
    chain = []
    seen = set()
    node = shape.get('_parentShape') if isinstance(shape, Instance) else None
    while isinstance(node, Instance) and id(node) not in seen:
        seen.add(id(node))
        chain.append(node)
        node = node.get('_parentShape')
    return chain


def position(shape):
    """A shape's world position.

    vForge stores ``_position`` as an **absolute world position**, so ancestor
    positions must not be accumulated. A ``Group3DShape``'s own position is the
    group's pivot handle, not a transform applied to its children.

    Verified on TheEdge: "Wall Trap Placement50" sits at x=13351.8 while its
    parent group "Trap Grid" sits at x=14600. The scene's "minimap box" volume
    spans x 9325..13275, so the raw value is in frame and the sum (27951) is
    nowhere near the level.
    """
    return vec3(shape.get('_position')) or (0.0, 0.0, 0.0)


def scale_warnings(shape):
    """Flag a shape whose own scale is not unit, since that rescales its cells."""
    s = vec3(shape.get('_scale')) or (1.0, 1.0, 1.0)
    if any(abs(v - 1.0) > 1e-4 for v in s):
        return ['shape scale is %s, not unit' % (tuple(round(v, 4) for v in s),)]
    return []
