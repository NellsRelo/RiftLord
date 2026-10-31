"""Instanced foliage added by a plan: `added` items of kind 'foliage', one per instance.

    {'kind': 'foliage', 'name': <static mesh>, 'matrix': <column-major 4x4>, 'group': n}

The build lifts the shipped InstancedFoliageActor of `DONOR_LEVEL` (an actor with
`FoliageInstancedStaticMeshComponent`s and a root SceneComponent at the origin) once per
`len(components)` meshes, points each component at one mesh and writes that mesh's
instances into it. Each component's native tail is written whole:

    object-guid flag 0 | LODData count 0 | bCooked 1 | PerInstanceSMData (64, N, N FMatrix)
    | PerInstanceSMCustomData (4, 0) | render data size 0 (uint64) | cluster tree (64, 0)

Render data size 0 makes the engine build the instance buffer from PerInstanceSMData,
and NumBuiltInstances 0 with no InstanceReorderTable makes it build the cluster tree on
load (UE 4.27 InstancedStaticMesh.cpp SerializeRenderData / InitPerInstanceRenderData,
HierarchicalInstancedStaticMesh.cpp OnPostLoadPerInstanceData, BuildTreeIfOutdated).
Shipped persistent levels carry components in exactly that state (Scenario_01's
InstancedFoliageActor). The actor's own FoliageInfos map is written empty.

    python3 -m riftlord.foliageadd --survey      # rewrite foliage-cull.json
"""

import argparse
import copy
import json
import os
import struct
import sys
from riftlord.core import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

KIND = 'foliage'
#: The level whose persistent InstancedFoliageActor is lifted for added foliage.
DONOR_LEVEL = '/Game/Maps/Campaign/Scenario_01/Scenario_01'
ACTOR_CLASS = 'InstancedFoliageActor'
COMPONENT_CLASS = 'FoliageInstancedStaticMeshComponent'
#: Name of each lifted foliage actor (apply.free_name adds a letter when taken).
ACTOR_NAME = 'RiftLordFoliage'
#: Bytes per FInstancedStaticMeshInstanceData (one FMatrix) and per FClusterNode.
INSTANCE_STRIDE = 64
CLUSTER_STRIDE = 64
#: Bytes per custom-data float.
CUSTOM_STRIDE = 4
#: Tagged properties that describe the donor's mesh, instances or built tree.
CLEARED = ('OverrideMaterials', 'StreamingTextureData', 'CacheMeshExtendedBounds',
           'InstanceReorderTable', 'SortedInstances', 'NumBuiltInstances',
           'BuiltInstanceBounds', 'UnbuiltInstanceBoundsList', 'InstanceCountToRender',
           'OcclusionLayerNumNodes', 'RelativeLocation', 'RelativeRotation',
           'RelativeScale3D', 'VisibilityId')
#: Added foliage has no lightmap, so it is Movable like every added piece (fabemit).
MOBILITY = 'EComponentMobility::Movable'
#: Per-mesh cull distances measured on shipped foliage components (`--survey`).
CULL_PATH = gamepath.data('riftlord/foliage-cull.json')
#: Instances one component may hold (omd3level.MAX_INSTANCES reads no more).
MAX_INSTANCES = 200000


def items(plan):
    """[(index in `added`, item)] of the plan's foliage instances."""
    return [(n, a) for n, a in enumerate((plan or {}).get('added') or [])
            if isinstance(a, dict) and a.get('kind') == KIND]


def clean_piece(raw, matrix):
    """The saved form of one foliage instance (leveldata.save_plan)."""
    from riftlord.formats.omd3 import meshcat
    name = str(raw.get('name') or '')
    if name not in meshcat.load():
        raise ValueError('no such foliage mesh: %r' % name)
    return {'name': name, 'kind': KIND, 'matrix': matrix}


def cull_table():
    """{'meshes': {mesh: [start, end]}, 'default': [start, end]} (foliage-cull.json)."""
    try:
        with open(CULL_PATH) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {'meshes': {}, 'default': [0, 0]}


def cull_for(mesh, table=None):
    table = table or cull_table()
    return tuple((table.get('meshes') or {}).get(mesh) or table.get('default') or (0, 0))


# -- reading cooked exports ------------------------------------------------------

def _class(pkg, i):
    from riftlord.build import levelbuild
    return levelbuild.resolve(pkg, struct.unpack_from('<i', pkg.exports[i]['meta'], 0)[0])[1]


def _outer(pkg, i):
    from riftlord.build import levelbuild
    return struct.unpack_from('<i', pkg.exports[i]['meta'], levelbuild.OUTER_AT)[0]


def _props(pkg, i):
    """{name: (kind, start, end)} of one export's tagged properties."""
    from riftlord.build.navigation import navvolinject
    return {n: (k, s, e) for n, k, s, e in
            navvolinject.walk_properties(pkg.exports[i]['data'] or b'', pkg.names)}


def _int_prop(pkg, i, name):
    got = _props(pkg, i).get(name)
    if not got:
        return None
    return struct.unpack_from('<i', pkg.exports[i]['data'], got[2] - 4)[0]


def import_label(pkg, ref):
    """The name an import shows, with its FName number (`Grass_Clumped` 2 is
    `Grass_Clumped_1`), or None."""
    if not ref or ref >= 0 or -ref > len(pkg.imports):
        return None
    rec = pkg.imports[-ref - 1]
    name = pkg.names[struct.unpack_from('<i', rec, 20)[0]][0]
    number = struct.unpack_from('<i', rec, 24)[0]
    return '%s_%d' % (name, number - 1) if number else name


def _terminated(data, names):
    """Offset just past the None that ends `data`'s tagged properties."""
    from riftlord.build import levelbuild
    o = 0
    while o + 8 <= len(data):
        ni = struct.unpack_from('<i', data, o)[0]
        if not 0 <= ni < len(names):
            break
        if names[ni][0] == 'None':
            return o + 8
        ti = struct.unpack_from('<i', data, o + 8)[0]
        kind = names[ti][0] if 0 <= ti < len(names) else ''
        size = 0 if kind == 'BoolProperty' else struct.unpack_from('<i', data, o + 16)[0]
        o += levelbuild.Builder.HEAD.get(kind, levelbuild.Builder.HEAD_DEFAULT) + size
    raise ValueError('no property terminator')


def component_tail(matrices):
    """The native tail of a foliage component holding `matrices` (column-major 4x4s)."""
    out = bytearray(struct.pack('<iii', 0, 0, 1))
    out += struct.pack('<ii', INSTANCE_STRIDE, len(matrices))
    for m in matrices:
        out += struct.pack('<16f', *[float(v) for v in m])
    out += struct.pack('<ii', CUSTOM_STRIDE, 0)
    out += struct.pack('<Q', 0)
    out += struct.pack('<ii', CLUSTER_STRIDE, 0)
    return bytes(out)


def read_tail(data, names):
    """[column-major 4x4] a component tail written by `component_tail` holds, or None
    for any other layout."""
    at = _terminated(data, names)
    guid, lods, cooked = struct.unpack_from('<iii', data, at)
    if guid or lods or cooked != 1:
        return None
    stride, n = struct.unpack_from('<ii', data, at + 12)
    if stride != INSTANCE_STRIDE:
        return None
    first = at + 20
    out = [list(struct.unpack_from('<16f', data, first + k * INSTANCE_STRIDE))
           for k in range(n)]
    rest = data[first + n * INSTANCE_STRIDE:]
    if rest != struct.pack('<iiQii', CUSTOM_STRIDE, 0, 0, CLUSTER_STRIDE, 0):
        return None
    return out


# -- the donor -------------------------------------------------------------------

def donor(cache={}):
    """(package with the foliage actor's native tails cut to the bare object-guid flag,
    actor export (zero-based), [component exports]) -- what `Builder.lift_actor` can
    lift without a handler for the two native payloads, which `place` then rewrites."""
    if 'got' in cache:
        return cache['got']
    from riftlord.core import gamefiles
    from riftlord.formats.ue.uassetio import Package
    ua, ux = gamefiles.package(DONOR_LEVEL)
    src = Package(ua, ux)
    src.load_payloads()
    actor = next((i for i in range(len(src.exports)) if _class(src, i) == ACTOR_CLASS), None)
    if actor is None:
        raise SystemExit('%s has no %s to lift for added foliage' % (DONOR_LEVEL, ACTOR_CLASS))
    comps = [i for i in range(len(src.exports))
             if _outer(src, i) == actor + 1 and _class(src, i) == COMPONENT_CLASS]
    if not comps:
        raise SystemExit('the %s of %s has no foliage components' % (ACTOR_CLASS, DONOR_LEVEL))
    cut = copy.copy(src)
    cut.exports = [dict(e) for e in src.exports]
    for i in [actor] + comps:
        data = cut.exports[i]['data']
        end = _terminated(data, src.names)
        cut.exports[i]['data'] = bytes(data[:end]) + b'\0\0\0\0'
    cache['got'] = (cut, actor, comps)
    return cache['got']


# -- the build -------------------------------------------------------------------

def _set_tail(builder, index, tail):
    pkg = builder.pkg
    data = pkg.exports[index - 1]['data']
    end = _terminated(data, pkg.names)
    pkg.exports[index - 1]['data'] = bytes(data[:end]) + tail
    pkg.exports[index - 1]['size'] = len(pkg.exports[index - 1]['data'])


def _int_payload(value):
    return struct.pack('<i', int(value))


def _fill(builder, comp, mesh_index, matrices, cull):
    """Point one lifted component at a mesh and write its instances."""
    for field in CLEARED:
        builder.clear_property(comp, field)
    if mesh_index:
        builder.set_object(comp, 'StaticMesh', mesh_index)
    builder.set_property(comp, 'Mobility', 'ByteProperty',
                         struct.pack('<ii', builder.pkg.add_name(MOBILITY), 0),
                         struct_name='EComponentMobility')
    start, end = cull
    for field, value in (('InstanceStartCullDistance', start),
                         ('InstanceEndCullDistance', end)):
        if value:
            builder.set_property(comp, field, 'IntProperty', _int_payload(value))
        else:
            builder.clear_property(comp, field)
    _set_tail(builder, comp, component_tail(matrices))


def by_mesh(plan):
    """{mesh: [column-major 4x4]} of the plan's foliage, meshes in first-placed order."""
    out = {}
    for _n, a in items(plan):
        m = a.get('matrix')
        if isinstance(m, list) and len(m) == 16 and a.get('name'):
            out.setdefault(a['name'], []).append([float(v) for v in m])
    return out


def apply(builder, plan, report, free_name=None):
    """Write the plan's foliage into the level. Returns the number of instances."""
    meshes = by_mesh(plan)
    if not meshes:
        return 0
    from riftlord.formats.omd3 import meshcat
    from riftlord.build import apply as applier
    from riftlord.mapeditor import geomedits
    cat = meshcat.load()
    src, actor, comps = donor()
    table = cull_table()
    names = list(meshes)
    per = len(comps)
    placed = 0
    got = report.setdefault('foliage', {'actors': 0, 'components': 0, 'instances': 0,
                                        'meshes': []})
    for k in range(0, len(names), per):
        chunk = names[k:k + per]
        rename = free_name('%s%d' % (ACTOR_NAME, k // per)) if free_name else \
            '%s%d' % (ACTOR_NAME, k // per)
        ours = builder.lift_actor(src, actor, rename=rename)
        _set_tail(builder, ours, struct.pack('<ii', 0, 0))
        lifted = [i + 1 for i in range(len(builder.pkg.exports))
                  if _outer(builder.pkg, i) == ours and
                  _class(builder.pkg, i) == COMPONENT_CLASS]
        if len(lifted) != per:
            raise SystemExit('the lifted %s has %d foliage components, the donor %d'
                             % (ACTOR_CLASS, len(lifted), per))
        for j, comp in enumerate(lifted):
            if j < len(chunk):
                mesh = chunk[j]
                info = cat.get(mesh)
                if not info:
                    report['skipped'].append('no foliage mesh %s' % mesh)
                    _fill(builder, comp, None, [], (0, 0))
                    continue
                mats = meshes[mesh][:MAX_INSTANCES]
                path = info['path'].replace('../../../OMD/Content', '/Game')
                _fill(builder, comp, applier.mesh_import(builder, path, mesh), mats,
                      cull_for(mesh, table))
                placed += len(mats)
                got['instances'] += len(mats)
                got['meshes'].append(mesh)
            else:
                _fill(builder, comp, None, [], (0, 0))
            got['components'] += 1
            geomedits.forget_visibility(builder, comp)
        got['actors'] += 1
    check = readback(builder.pkg, plan)
    if check:
        raise SystemExit('added foliage does not read back: %s' % '; '.join(check[:4]))
    return placed


def readback(pkg, plan):
    """[problem] comparing the foliage components in `pkg` with the plan's instances."""
    want = by_mesh(plan)
    have = {}
    from riftlord.build import levelbuild
    for i in range(len(pkg.exports)):
        if _class(pkg, i) != COMPONENT_CLASS:
            continue
        outer = _outer(pkg, i)
        if outer <= 0 or not levelbuild._name_of(pkg, outer - 1).startswith(ACTOR_NAME):
            continue
        mats = read_tail(pkg.exports[i]['data'], pkg.names)
        if mats is None:
            return ['component %d has an unexpected native tail' % (i + 1)]
        if not mats:
            continue
        mesh_ref = _int_prop(pkg, i, 'StaticMesh')
        have.setdefault(import_label(pkg, mesh_ref), []).extend(mats)
    problems = []
    for mesh, mats in want.items():
        got = have.get(mesh) or []
        if len(got) != len(mats[:MAX_INSTANCES]):
            problems.append('%s: %d instances written, %d planned' % (mesh, len(got), len(mats)))
            continue
        for a, b in zip(got, mats):
            if any(abs(x - y) > 1e-2 for x, y in zip(a, b)):
                problems.append('%s: an instance moved on the way in' % mesh)
                break
    for mesh in have:
        if mesh not in want:
            problems.append('%s written but not planned' % mesh)
    return problems


def gates(files, plan):
    """[(name, passed, detail)] for a built pak's files ({inner path: bytes}): the level
    copy's foliage components hold exactly the plan's instances, and none keeps a
    precomputed-visibility id."""
    from riftlord.build import levelbuild
    from riftlord.formats.ue.uassetio import Package
    out = []
    from riftlord.build import leveltitle
    for name, data in files.items():
        #: the mission's own level, whatever its Working Title names it
        if not leveltitle.is_level(name):
            continue
        pkg = Package(data, files[name[:-5] + '.uexp'])
        pkg.load_payloads()
        bad = readback(pkg, plan)
        want = sum(len(v) for v in by_mesh(plan).values())
        out.append(('added foliage reads back (%d instances of %d meshes)'
                    % (want, len(by_mesh(plan))), not bad, '; '.join(bad[:3])))
        ids = []
        for i in range(len(pkg.exports)):
            if _class(pkg, i) != COMPONENT_CLASS:
                continue
            outer = _outer(pkg, i)
            if outer > 0 and levelbuild._name_of(pkg, outer - 1).startswith(ACTOR_NAME):
                v = _int_prop(pkg, i, 'VisibilityId')
                if v is not None and v != -1:
                    ids.append(v)
        out.append(('added foliage has no precomputed-visibility id', not ids,
                    ', '.join(map(str, ids[:4]))))
    return out


# -- the cull distance survey ----------------------------------------------------

def survey():
    """Cull distances of every shipped foliage component that holds instances, as
    {'meshes': {mesh: [start, end]}, 'default': [start, end]} (most common per mesh,
    and over all components)."""
    import collections
    from riftlord.core import gamefiles
    from riftlord.build.navigation import navpatch
    from riftlord.formats.omd3 import omd3level
    from riftlord.formats.ue.uassetio import Package
    per = collections.defaultdict(collections.Counter)
    every = collections.Counter()
    levels = sorted(k for k in navpatch.maps(include_mods=False) if k.startswith('Scenario'))
    for lv in levels:
        for path in omd3level.sublevels(lv) + omd3level.extra_sublevels(lv):
            try:
                ua, ux = gamefiles.package(path)
                pkg = Package(ua, ux)
                pkg.load_payloads()
            except (Exception, SystemExit):            # noqa: BLE001
                continue
            for i in range(len(pkg.exports)):
                if _class(pkg, i) != COMPONENT_CLASS:
                    continue
                if not (_int_prop(pkg, i, 'NumBuiltInstances') or 0):
                    continue
                mesh = import_label(pkg, _int_prop(pkg, i, 'StaticMesh'))
                if not mesh:
                    continue
                pair = (_int_prop(pkg, i, 'InstanceStartCullDistance') or 0,
                        _int_prop(pkg, i, 'InstanceEndCullDistance') or 0)
                per[mesh][pair] += 1
                every[pair] += 1
    out = {'source': 'riftlord/foliageadd.py --survey: shipped FoliageInstancedStaticMesh'
                     'Components with instances, most common (start, end) per mesh',
           'meshes': {m: list(c.most_common(1)[0][0]) for m, c in sorted(per.items())},
           'default': list(every.most_common(1)[0][0]) if every else [0, 0]}
    with open(CULL_PATH, 'w') as fh:
        json.dump(out, fh, indent=1)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--survey', action='store_true',
                    help='measure cull distances on the shipped levels')
    args = ap.parse_args(argv)
    if args.survey:
        got = survey()
        print('%d meshes, default %s -> %s' % (len(got['meshes']), got['default'], CULL_PATH))


if __name__ == '__main__':
    main()
