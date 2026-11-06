"""Offline checks on the Collision switch of added kit and mesh pieces (marker: trapfix):
an added mesh and a kit piece written into a copy of Scenario_01 with `collision: false`
come back with collision off on every static mesh component (bUseDefaultCollision false,
BodyInstance CollisionEnabled and profile NoCollision, as the shipped levels switch decor
off), a piece without the key is left as it was, the walkable-graph rebuild leaves the
switched-off pieces out, and the Map Editor offers the switch.

    python3 test_trapfix.py
"""
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from riftlord.core import gamefiles  # noqa: E402
from riftlord.build import apply  # noqa: E402
from riftlord.build import levelbuild  # noqa: E402
from riftlord.build.navigation import navpatch  # noqa: E402
from riftlord.build.navigation import navregen  # noqa: E402
from riftlord.formats.omd3 import meshcat  # noqa: E402
from riftlord.formats.ue.uassetio import Package  # noqa: E402
from riftlord.mapeditor import piececollision  # noqa: E402
from riftlord.mapeditor import volumes  # noqa: E402

LEVEL = 'Scenario_01'
MESH = 'Column_Sec_1_4_RB'
KIT = 'Column_Sec_1_4_RB_C'

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


def flat(x):
    return [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, x, 0.0, 0.0, 1.0]


# 1. the plan key
check('collision false on a kit or mesh piece reads as off',
      piececollision.is_off({'kind': 'kit', 'collision': False}) and
      piececollision.is_off({'kind': 'mesh', 'collision': False}), '')
check('no key, true, or a Fab piece reads as on',
      not piececollision.is_off({'kind': 'mesh'}) and
      not piececollision.is_off({'kind': 'mesh', 'collision': True}) and
      not piececollision.is_off({'kind': 'fab', 'collision': False}), '')

# 2. written into a copy of the level and read back
ua, ux = gamefiles.package(navpatch.map_path(LEVEL))
builder = levelbuild.Builder.around(ua, ux, LEVEL)
report = {'notes': [], 'skipped': [], 'added': 0}
path = meshcat.load()[MESH]['path'].replace('../../../OMD/Content', '/Game')
items = [{'kind': 'mesh', 'name': MESH, 'matrix': flat(0.0), 'collision': False},
         {'kind': 'mesh', 'name': MESH, 'matrix': flat(500.0)},
         {'kind': 'kit', 'name': KIT, 'matrix': flat(1000.0), 'collision': False},
         {'kind': 'kit', 'name': KIT, 'matrix': flat(1500.0)}]
names = {}
for n, item in enumerate(items):
    if item['kind'] == 'mesh':
        actor, why = apply.add_mesh(builder, apply.donor_for('StaticMeshActor'), path, MESH,
                                    item['matrix'], 'TrapfixMesh_%d' % n)
    else:
        where, rot, scale = apply.transform_of(item['matrix'])
        actor = apply._add(builder, KIT, where, 'TrapfixKit_%d' % n, rot, report, scale=scale)
    check('piece %d (%s) placed' % (n, item['kind']), actor, report['skipped'])
    if actor:
        apply._collision_off(builder, actor, item, report)
        names[n] = levelbuild._name_of(builder.pkg, actor - 1)
check('two pieces switched off', report.get('collision_off') == 2, report)
ua2, ux2 = builder.write()
pkg = Package(ua2, ux2)
pkg.load_payloads()
names_ = [n for n, _h in pkg.names]


def components(actor_name):
    """[(bUseDefaultCollision value or None, {BodyInstance field: name})] per mesh component."""
    actor = next(i + 1 for i in range(len(pkg.exports))
                 if levelbuild._name_of(pkg, i) == actor_name)
    out = []
    for i, e in enumerate(pkg.exports):
        if struct.unpack_from('<i', e['meta'], levelbuild.OUTER_AT)[0] != actor:
            continue
        cls = levelbuild.resolve(pkg, struct.unpack_from('<i', e['meta'], 0)[0])[1] or ''
        if not cls.endswith('StaticMeshComponent'):
            continue
        data = e['data']
        props = meshcat._walk(data, names_, 0, len(data))
        udc = None
        if 'bUseDefaultCollision' in props:
            udc = data[props['bUseDefaultCollision'][1] - 2]
        fields = {}
        if 'BodyInstance' in props:
            _k, at, size = props['BodyInstance']
            for name, _kind, off, sz in volumes._tags(data, names_, at, at + size)[0]:
                if sz == 8:
                    k = struct.unpack_from('<i', data, off)[0]
                    fields[name] = names_[k] if 0 <= k < len(names_) else k
        out.append((udc, fields))
    return out


def is_off(comp):
    udc, f = comp
    return (udc == 0 and f.get('CollisionEnabled') == 'ECollisionEnabled::NoCollision'
            and f.get('CollisionProfileName') == 'NoCollision')


for n, item in enumerate(items):
    if n not in names:
        continue
    comps = components(names[n])
    if item.get('collision') is False:
        check('%s %d: every mesh component has collision off' % (item['kind'], n),
              comps and all(is_off(c) for c in comps), comps)
    else:
        check('%s %d: collision left as lifted' % (item['kind'], n),
              comps and not any(is_off(c) for c in comps), comps)

# 3. the walkable-graph rebuild leaves switched-off pieces out
got = [bool(navregen.added_meshes(item)) for item in items]
check('the walkable-graph rebuild takes only the pieces that collide',
      got == [False, True, False, True], got)

# 4. the Map Editor's switch
src = open(os.path.join(ROOT, 'web', 'mapeditor', 'src', 'main.js')).read()
check('the piece panel has the Collision switch',
      'collisionSection()' in src and 'id="piececollision"' in src
      and 'a.collision = false' in src and 'delete a.collision' in src, '')

# 5. a Map Editor save keeps the switch (leveldata.save_plan), into a scratch plans folder
import json  # noqa: E402
import tempfile  # noqa: E402
from riftlord.mapeditor import leveldata  # noqa: E402
leveldata.PLANS = tempfile.mkdtemp(prefix='trapfix-plans-')
ident = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]
leveldata.save_plan('Scenario_01', {'added': [
    {'kind': 'mesh', 'name': '1M_Cube', 'matrix': ident, 'collision': False},
    {'kind': 'mesh', 'name': '1M_Cube', 'matrix': ident}]})
with open(os.path.join(leveldata.PLANS, os.listdir(leveldata.PLANS)[0])) as fh:
    saved = json.load(fh).get('added') or []
check('a save keeps collision off, and adds nothing to pieces that collide',
      [a.get('collision') for a in saved] == [False, None], saved)

bad = [c for c in checks if not c[1]]
for name, ok, detail in checks:
    print('%s %s%s' % ('ok  ' if ok else 'FAIL', name, '' if ok else ': %s' % (detail,)))
print('%d of %d checks passed' % (len(checks) - len(bad), len(checks)))
sys.exit(1 if bad else 0)
