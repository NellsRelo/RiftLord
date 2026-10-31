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
"""

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
