"""Fab art in a mission: placed library pieces become StaticMeshActors in the level copy,
trap grids become BP_OMDTrapGrid_Blank_C actors at scale 1, and the mission pak carries the
placed meshes' package closure copied from each library pak, nothing else of the library.

Plan fields (riftlord/PLAN-ACTORS.md "Fab pieces"): `added` entries with `kind: 'fab'`, and
`fabGrids` (grids the author laid in world space). Nothing here runs for a plan without them.
"""

import math
import re
import struct

from riftlord.mapeditor import fablibrary

MESH_DONOR = 'StaticMeshActor'
GRID_DONOR = 'BP_OMDTrapGrid_Blank_C'
GRID_BOX = 'OMDTrapGridBox'
#: Export names for what this module adds (digits with no underscore: apply.fname_safe).
PIECE_NAME = 'RiftLordFab%d'
GRID_NAME = 'RiftLordFabGrid%d'
#: A lifted StaticMeshActor's texture streaming data describes the donor's mesh; removed so
#: the engine streams by the Fab mesh itself (as the runtime-spawned meshes did in game).
CLEARED_ON_PIECE = ('StreamingTextureData',)
#: A written mesh has no lightmap, so pieces are Movable: lit by the volumetric lightmap and
#: dynamic lights, as traps and heroes are.
PIECE_MOBILITY = 'EComponentMobility::Movable'
#: One cooked FStaticMeshComponentLODInfo: strip flags (2 bytes), MapBuildDataId (16).
LOD_INFO_SIZE = 18
#: A column length further than this from 1 counts as a rescale (uu/uu).
SCALE_TOLERANCE = 1e-3
#: A grid's half thickness (uu, LIBRARY-FORMAT "A grid": extent [10, a, b]).
GRID_HALF_THICKNESS = 10.0
#: Grid half-extents along the surface are whole multiples of this (uu, half a cell).
GRID_HALF_STEP = 75.0
#: `TrapPlacementTypes` bits a grid may carry (floor 1, wall 2, ceiling 4, war machines 8/16/32).
GRID_BITS_MASK = 0x3F


def pieces(plan):
    """[(index in `added`, entry)] of the plan's Fab pieces."""
    return [(n, e) for n, e in enumerate((plan or {}).get('added') or [])
            if isinstance(e, dict) and e.get('kind') == 'fab']


def uses_fab(plan):
    return bool(pieces(plan) or (plan or {}).get('fabGrids'))


def packs(plan):
    return sorted({e.get('pack') for _n, e in pieces(plan) if e.get('pack')})


# -- plan cleaning (leveldata.save_plan) ---------------------------------------------------------

def _vec3(v):
    return [round(float(x), 4) for x in list(v)[:3]]


def clean_piece(a, matrix):
    """A saved `added` entry of kind 'fab': {name, kind, pack, object, role, grids, matrix}."""
    return {'name': str(a.get('name') or ''), 'kind': 'fab',
            'pack': str(a.get('pack') or ''), 'object': str(a.get('object') or ''),
            'source': str(a.get('source') or ''),
            'role': str(a.get('role') or ''),
            'grids': [str(g) for g in (a.get('grids') or []) if isinstance(g, str)],
            'matrix': matrix}


def clean_grids(raw):
    """The plan's `fabGrids`, each {id, center, axes{x,y,z}, extent, bits, warmachine}."""
    out = []
    for g in raw or []:
        if not isinstance(g, dict):
            continue
        try:
            ax = g['axes']
            wm = g.get('warmachine')
            out.append({'id': int(g.get('id') or len(out) + 1), 'center': _vec3(g['center']),
                        'axes': {k: _vec3(ax[k]) for k in ('x', 'y', 'z')},
                        'extent': _vec3(g['extent']), 'bits': int(g['bits']),
                        'warmachine': None if wm is None else int(wm),
                        'pieces': [int(p) for p in g.get('pieces') or []
                                   if isinstance(p, (int, float))]})
        except (KeyError, TypeError, ValueError):
            continue
    return out


#: A Fab piece is under a laid grid when its drawing's centre lies inside the grid's
#: rectangle and within this of its plane (uu): a floor tile's centre sits half its
#: thickness below the grid laid on its top.
UNDER_PLANE = 150.0


def mend_grids(grids, added, saved_at=None, notes=None):
    """Point each laid grid's `pieces` at Fab pieces of the saved `added` (marker: fabgrids).

    `grids` are clean_grids output, their indices into the raw list the editor sent;
    `saved_at[i]` is where raw item i landed in `added` (None when the save dropped it).
    An index past the end or at a piece that is not a Fab piece is dropped. A grid left with
    none is pointed at the Fab pieces under it, and dropped when there are none: it floats
    where deleted pieces used to be. Returns the grids kept."""
    notes = notes if notes is not None else []
    out, under_cache = [], {}
    for g in grids:
        got = []
        for i in g.get('pieces') or []:
            j = i if saved_at is None else (saved_at[i] if 0 <= i < len(saved_at) else None)
            if j is not None and 0 <= j < len(added) and \
                    (added[j] or {}).get('kind') == 'fab' and j not in got:
                got.append(j)
        if not got:
            got = _pieces_under(g, added, under_cache)
            if not got:
                notes.append('Trap grid %s has no Fab piece left under it, so it was left '
                             'out; place it over a Fab piece, or remove it' % g.get('id'))
                continue
        out.append(dict(g, pieces=got))
    return out


def _pieces_under(grid, added, cache):
    """Indices of the Fab pieces whose drawing's centre lies under `grid`."""
    if 'placed' not in cache:
        cache['placed'] = resolve({'added': added})[0]
    ax = grid['axes']
    n, u, v = ax['x'], ax['y'], ax['z']
    c = grid['center']
    out = []
    for i, _e, entry, matrix in cache['placed']:
        b = entry.get('bounds') or {}
        lo, hi = b.get('min'), b.get('max')
        mid = [(lo[k] + hi[k]) / 2 for k in range(3)] if lo and hi else [0.0, 0.0, 0.0]
        d = [p - q for p, q in zip(_apply(matrix, mid), c)]
        dot = lambda a: sum(x * y for x, y in zip(a, d))  # noqa: E731
        if abs(dot(n)) <= UNDER_PLANE and abs(dot(u)) <= grid['extent'][1] + 1 and \
                abs(dot(v)) <= grid['extent'][2] + 1:
            out.append(i)
    return out


# -- transforms --------------------------------------------------------------------------------

def _columns(matrix):
    return [[float(matrix[c * 4 + r]) for r in range(3)] for c in range(3)]


def _det(cols):
    a, b, c = cols
    return (a[0] * (b[1] * c[2] - b[2] * c[1]) - b[0] * (a[1] * c[2] - a[2] * c[1])
            + c[0] * (a[1] * b[2] - a[2] * b[1]))


def scale_of(matrix):
    return [math.sqrt(sum(v * v for v in col)) for col in _columns(matrix)]


def unscaled(matrix):
    """`matrix` with each axis column normalised (scale 1, same turn and place)."""
    out = [float(v) for v in matrix]
    for c, length in enumerate(scale_of(matrix)):
        if length > 1e-9:
            for r in range(3):
                out[c * 4 + r] /= length
    return out


def is_unscaled(matrix):
    return all(abs(s - 1.0) <= SCALE_TOLERANCE for s in scale_of(matrix))


def _apply(matrix, p):
    return [sum(float(matrix[c * 4 + r]) * p[c] for c in range(3)) + float(matrix[12 + r])
            for r in range(3)]


def _turn(matrix, v):
    return [sum(float(matrix[c * 4 + r]) * v[c] for c in range(3)) for r in range(3)]


def grid_matrix(axes, center):
    """Column-major matrix of a grid box: columns are its x (normal), y, z axes."""
    x, y, z = axes
    return list(x) + [0.0] + list(y) + [0.0] + list(z) + [0.0] + list(center) + [1.0]


# -- what a plan places --------------------------------------------------------------------------

def resolve(plan):
    """([(index, entry, catalogue entry, matrix)], [problems]) for the plan's Fab pieces.

    The matrix written is the plan's, with any rescale of a non-prop removed: pack scales are
    baked in and architecture is never rescaled (user decision). A mirrored matrix is written
    as it is (transform_of puts the mirror on X): the engine reverses culling for a negative
    determinant, a convex element uses its cooked mirrored copy (every library BodySetup cooks
    one) and a cooked trimesh takes the negative scale itself (PhysX 3.4 PxMeshScale)."""
    out, problems = [], []
    for n, e in pieces(plan):
        pack, obj = e.get('pack'), e.get('object')
        try:
            entry = fablibrary.entry_for(pack, obj, e.get('source'))
        except SystemExit as exc:
            problems.append(str(exc))
            continue
        if not entry or entry.get('status') != 'converted':
            problems.append('the Fab library %s no longer has the mesh %s (in its current '
                            'build)' % (pack, fablibrary.build_free(obj, pack) or obj))
            continue
        matrix = e.get('matrix')
        if not isinstance(matrix, list) or len(matrix) != 16:
            problems.append('Fab piece %s has no placement' % obj)
            continue
        if not fablibrary.scalable(entry) and not is_unscaled(matrix):
            matrix = unscaled(matrix)
        out.append((n, e, entry, matrix))
    return out, problems


def world_grids(plan, placed=None, notes=None):
    """Every grid the plan emits, in world space: [{center, axes, extent, bits, warmachine,
    source}]. A piece's suggested grids follow it; `fabGrids` are written as given."""
    placed = placed if placed is not None else resolve(plan)[0]
    notes = notes if notes is not None else []
    out = []
    for n, e, entry, matrix in placed:
        wanted = [str(g) for g in (e.get('grids') or [])]
        if not wanted:
            continue
        if not is_unscaled(matrix):
            notes.append('%s is rescaled, so its suggested trap grids are left out; set its '
                         'scale back to 1 to keep them' % entry['name'])
            continue
        by_id = {g['id']: g for g in entry.get('grids') or []}
        for gid in wanted:
            g = by_id.get(gid)
            if g is None:
                notes.append('%s no longer has suggested trap grid %s (its Fab library was '
                             'rebuilt), so it was left out; pick its trap grids again'
                             % (entry['name'], gid))
                continue
            if not fablibrary.grid_allowed(entry, g):
                notes.append('%s cannot take trap grid %s as a %s, so it was left out; pick '
                             'another trap grid for it' % (entry['name'], gid,
                                                           entry.get('role')))
                continue
            ax = g['axes']
            axes = [_turn(matrix, ax['x']), _turn(matrix, ax['y']), _turn(matrix, ax['z'])]
            if _det(axes) < 0:
                #: a mirrored piece: the normal follows the mirror, y turns back so the
                #: grid box keeps a right-handed frame (its extents are symmetric)
                axes[1] = [-v for v in axes[1]]
            out.append({'center': _apply(matrix, g['center']),
                        'axes': axes,
                        'extent': [float(v) for v in g['extent']], 'bits': int(g['bits']),
                        'warmachine': g.get('warmachine'),
                        'source': '%s %s' % (entry['name'], gid)})
    for g in (plan or {}).get('fabGrids') or []:
        ax = g.get('axes') or {}
        out.append({'center': [float(v) for v in g['center']],
                    'axes': [list(map(float, ax['x'])), list(map(float, ax['y'])),
                             list(map(float, ax['z']))],
                    'extent': [float(v) for v in g['extent']], 'bits': int(g['bits']),
                    'warmachine': g.get('warmachine'), 'source': 'grid %s' % g.get('id')})
    return out


#: Grids merged into one run must lie on one plane within this (uu) and face the same way
#: within RUN_NORMAL_COS.
RUN_PLANE_TOLERANCE = 15.0
RUN_NORMAL_COS = 0.999
#: A run's rectangles must cover its bounding rectangle to within this share (no gaps).
RUN_COVER_TOLERANCE = 0.01


def merge_run(grids):
    """One grid laid across `grids` (world space, as world_grids gives), or (None, why).

    The grids must face the same way on one plane, carry the same placement bits, and
    together tile a rectangle; the merged box sits on the frontmost of their planes."""
    if len(grids) < 2:
        return None, 'pick two or more grids on one surface'
    first = grids[0]
    n, u, v = first['axes']
    for g in grids[1:]:
        if sum(a * b for a, b in zip(g['axes'][0], n)) < RUN_NORMAL_COS:
            return None, 'the grids face different ways'
        if g['bits'] != first['bits'] or g.get('warmachine') != first.get('warmachine'):
            return None, 'the grids take different traps'
    depth = [sum(n[k] * g['center'][k] for k in range(3)) for g in grids]
    if max(depth) - min(depth) > RUN_PLANE_TOLERANCE:
        return None, 'the grids are not on one plane (%.0f uu apart)' % (max(depth) - min(depth))
    rects, area = [], 0.0
    for g in grids:
        cu = sum(u[k] * g['center'][k] for k in range(3))
        cv = sum(v[k] * g['center'][k] for k in range(3))
        hu = sum(abs(sum(u[k] * g['axes'][i][k] for k in range(3))) * g['extent'][i]
                 for i in (1, 2))
        hv = sum(abs(sum(v[k] * g['axes'][i][k] for k in range(3))) * g['extent'][i]
                 for i in (1, 2))
        rects.append((cu - hu, cu + hu, cv - hv, cv + hv))
        area += 4 * hu * hv
    u0, u1 = min(r[0] for r in rects), max(r[1] for r in rects)
    v0, v1 = min(r[2] for r in rects), max(r[3] for r in rects)
    box = (u1 - u0) * (v1 - v0)
    if abs(box - area) > RUN_COVER_TOLERANCE * box:
        return None, 'the grids do not tile one rectangle (gaps or overlaps)'
    eu, ev = (u1 - u0) / 2, (v1 - v0) / 2
    for e in (eu, ev):
        if abs(e / GRID_HALF_STEP - round(e / GRID_HALF_STEP)) > 1e-2:
            return None, 'the run is not a whole number of half cells'
    eu, ev = round(eu / GRID_HALF_STEP) * GRID_HALF_STEP, round(ev / GRID_HALF_STEP) * GRID_HALF_STEP
    d, mu, mv = max(depth), (u0 + u1) / 2, (v0 + v1) / 2
    center = [n[k] * d + u[k] * mu + v[k] * mv for k in range(3)]
    return {'center': center, 'axes': [list(n), list(u), list(v)],
            'extent': [GRID_HALF_THICKNESS, eu, ev], 'bits': first['bits'],
            'warmachine': first.get('warmachine'), 'source': 'run'}, None


def grid_problems(grid):
    """Why a grid would be refused: thickness, half-cell sizes, bits, an orthonormal frame."""
    bad = []
    ext = grid['extent']
    if abs(ext[0] - GRID_HALF_THICKNESS) > 1e-3:
        bad.append('thickness %g, not %g' % (2 * ext[0], 2 * GRID_HALF_THICKNESS))
    for v in ext[1:]:
        if v < GRID_HALF_STEP - 1e-3 or abs(v / GRID_HALF_STEP - round(v / GRID_HALF_STEP)) > 1e-3:
            bad.append('half-extent %g is not a whole number of half cells' % v)
    if not 0 < grid['bits'] <= GRID_BITS_MASK:
        bad.append('placement bits %r' % grid['bits'])
    a = grid['axes']
    for i in range(3):
        if abs(math.sqrt(sum(v * v for v in a[i])) - 1.0) > 1e-3:
            bad.append('axis %d is not unit length' % i)
        for j in range(i + 1, 3):
            if abs(sum(a[i][k] * a[j][k] for k in range(3))) > 1e-3:
                bad.append('axes %d and %d are not square' % (i, j))
    if _det(a) < 0:
        bad.append('axes are left-handed')
    return bad


# -- writing the level ----------------------------------------------------------------------------

def _free_name(builder, base, taken):
    from riftlord.build import levelbuild
    if not taken:
        for i in range(len(builder.pkg.exports)):
            got = levelbuild._name_of(builder.pkg, i)
            if got:
                taken.add(got)
    n, name = 0, base
    while name in taken:
        n += 1
        name = '%s%s' % (base, chr(ord('A') + n - 1))
    taken.add(name)
    return name


def lod_data(data, tail):
    """(offset of the LODData count, count) in a StaticMeshComponent's native tail, which
    starts at `tail`: the object-guid flag (and guid), then the LODData array."""
    at = tail + 4 + (16 if struct.unpack_from('<i', data, tail)[0] else 0)
    count = struct.unpack_from('<i', data, at)[0]
    if count < 0 or at + 4 + count * LOD_INFO_SIZE != len(data):
        raise SystemExit('a Fab piece component has a native tail this build does not know '
                         '(%d LODData entries, %d bytes)' % (count, len(data) - tail))
    return at, count


def forget_lighting(builder, component):
    """The piece is Movable and has no LODData. The donor's LODData names the donor's
    MapBuildDataId, which drew the donor's baked lightmap through the Fab mesh's lightmap
    UVs: blocky and dark in game (Fab Probe Alpha)."""
    pkg = builder.pkg
    builder.set_property(component, 'Mobility', 'ByteProperty',
                         struct.pack('<ii', pkg.add_name(PIECE_MOBILITY), 0),
                         struct_name='EComponentMobility')
    data = bytearray(pkg.exports[component - 1]['data'])
    at, count = lod_data(data, builder._terminator(data) + 8)
    del data[at + 4:]
    struct.pack_into('<i', data, at, 0)
    pkg.exports[component - 1]['data'] = bytes(data)
    pkg.exports[component - 1]['size'] = len(data)
    return count


def mesh_import(builder, pack, package):
    """The level's import of one library mesh, named exactly as the library's export
    (string and FName number). Cached per package path."""
    cache = builder.__dict__.setdefault('_rl_fab_imports', {})
    if package in cache:
        return cache[package]
    name, number = fablibrary.export_name(pack, package)
    pkg = builder.pkg
    outer = pkg.add_import('/Script/CoreUObject', 'Package', 0, package)
    rec = struct.pack('<iiiiiii', pkg.add_name('/Script/Engine'), 0,
                      pkg.add_name('StaticMesh'), 0, outer, pkg.add_name(name), number)
    pkg.imports.append(rec)
    cache[package] = -len(pkg.imports)
    return cache[package]


def apply(builder, plan, report):
    """Write the plan's Fab pieces and grids into the level. Adds report['fab']."""
    from riftlord.build import apply as applier
    from riftlord.mapeditor import geomedits
    placed, problems = resolve(plan)
    notes = []
    grids = world_grids(plan, placed, notes)
    got = {'pieces': 0, 'grids': 0, 'packages': [], 'notes': notes, 'cleared': 0,
           'visibility_reset': 0}
    report['fab'] = got
    if problems:
        raise SystemExit('Fab pieces: ' + '; '.join(problems[:5]))
    for g in grids:
        bad = grid_problems(g)
        if bad:
            raise SystemExit('trap grid (%s): %s' % (g['source'], '; '.join(bad)))
    if not placed and not grids:
        return got
    from riftlord.mapeditor import fabcollision
    notes.extend('collision: ' + line for line in fabcollision.problems(plan))
    taken = set()
    if placed:
        donor = applier.donor_for(MESH_DONOR)
        if not donor:
            raise SystemExit('no %s to lift for Fab pieces' % MESH_DONOR)
    for n, e, entry, matrix in placed:
        actor = builder.lift_actor(donor[0], donor[1],
                                   rename=_free_name(builder, PIECE_NAME % n, taken))
        root = builder._root_component(actor)
        if not root:
            raise SystemExit('the lifted %s has no RootComponent' % MESH_DONOR)
        builder.set_object(root, 'StaticMesh', mesh_import(builder, e['pack'], entry['package']))
        for field in CLEARED_ON_PIECE:
            got['cleared'] += int(builder.clear_property(root, field))
        forget_lighting(builder, root)
        #: a piece that does not collide (a sky): collision off on its component, as the
        #: shipped levels switch decor off (piececollision: with bUseDefaultCollision false,
        #: or the engine puts the mesh's own profile back when the component registers)
        if not fablibrary.collides(entry):
            from riftlord.mapeditor import piececollision
            piececollision.switch_off(builder, actor)
            got['noCollision'] = got.get('noCollision', 0) + 1
        where, rot, scale = applier.transform_of(matrix)
        applier.place_whole(builder, actor, location=where, rotation=rot, scale=scale)
        got['visibility_reset'] += geomedits.forget_visibility(builder, actor)
        got['pieces'] += 1
        got['packages'].append(entry['package'])
    if grids:
        gdonor = applier.donor_for(GRID_DONOR)
        if not gdonor:
            raise SystemExit('no %s to lift for trap grids' % GRID_DONOR)
    for k, g in enumerate(grids):
        where, rot, _scale = applier.transform_of(grid_matrix(g['axes'], g['center']))
        actor = builder.lift_actor(gdonor[0], gdonor[1],
                                   rename=_free_name(builder, GRID_NAME % k, taken))
        applier.place_whole(builder, actor, location=where, rotation=rot)
        box = applier._child_named(builder, actor, GRID_BOX)
        if not box:
            raise SystemExit('the grid donor has no %s component' % GRID_BOX)
        builder.set_vector(box, 'BoxExtent', g['extent'])
        builder.set_property(box, 'TrapPlacementTypes', 'ByteProperty',
                             bytes([g['bits'] & 0xFF]), struct_name='None')
        if g.get('warmachine') is not None:
            builder.set_property(box, 'TrapWarMachineTypes', 'ByteProperty',
                                 bytes([int(g['warmachine']) & 0xFF]), struct_name='None')
        got['visibility_reset'] += geomedits.forget_visibility(builder, actor)
        got['grids'] += 1
    got['packages'] = sorted(set(got['packages']))
    return got


# -- the pak ------------------------------------------------------------------------------------

def closure_files(plan):
    """({pak entry: bytes}, {pack: [game paths]}) every placed piece needs from its library."""
    placed, problems = resolve(plan)
    if problems:
        raise SystemExit('Fab pieces: ' + '; '.join(problems[:5]))
    by_pack = {}
    for _n, e, entry, _m in placed:
        by_pack.setdefault(e['pack'], set()).add(entry['package'])
    files, listed = {}, {}
    for pack, wanted in sorted(by_pack.items()):
        before = fablibrary.stamp(pack)
        got, paths = fablibrary.closure(pack, sorted(wanted))
        if fablibrary.stamp(pack) != before:
            raise SystemExit('the Fab library %s changed while it was read (being rebuilt?); '
                             'build again when it is finished' % pack)
        files.update(got)
        listed[pack] = paths
    return files, listed


def add_closure(files, plan, report):
    """`files` with the placed closure added; refuses a clash with a mission file."""
    lib, listed = closure_files(plan)
    clash = [k for k in lib if k in files and files[k] != lib[k]]
    if clash:
        raise SystemExit('Fab library files clash with mission files: %s' % ', '.join(clash[:3]))
    out = dict(files)
    out.update(lib)
    report.setdefault('fab', {})['closure'] = {p: len(v) for p, v in listed.items()}
    report['fab']['closure_bytes'] = sum(len(v) for v in lib.values())
    return out


# -- gates on a built pak (riftlord/probe.py) -------------------------------------------------------

def _fname(names, index, number):
    return names[index] if number == 0 else '%s_%d' % (names[index], number - 1)


def _class_name(pkg, names, ci):
    if ci < 0:
        return names[struct.unpack('<7i', pkg.imports[-ci - 1])[5]]
    if ci > 0:
        return names[struct.unpack_from('<i', pkg.exports[ci - 1]['meta'], 16)[0]]
    return None


def gates(files, plan):
    """[(name, ok, detail)] for a built pak's entries {name: bytes} against its plan."""
    from riftlord.core import gamefiles
    from riftlord.formats.ue.uassetio import Package
    out = []
    fab = {k for k in files if k.startswith(fablibrary.ENTRY_ROOT)}
    try:
        want, listed = closure_files(plan)
    except SystemExit as exc:
        return [('Fab closure computable from the libraries', False, str(exc))]
    missing = sorted(set(want) - fab)
    extra = sorted(fab - set(want))
    differ = sorted(k for k in set(want) & fab if files[k] != want[k])
    out.append(('Fab: every placed package\'s closure is in the pak, byte-identical',
                not missing and not differ,
                '%d files from %s; missing %d, differing %d%s'
                % (len(fab), ', '.join(sorted(listed)) or 'no library', len(missing),
                   len(differ), (': ' + ', '.join((missing + differ)[:3])) if missing or differ else '')))
    out.append(('Fab: nothing from a library that is not placed', not extra,
                '%d extra%s' % (len(extra), (': ' + ', '.join(extra[:3])) if extra else '')))
    ours = {}
    for key in files:
        if key.endswith('.uasset') or key.endswith('.umap'):
            ours['/Game' + key[len('OMD/Content'):].rsplit('.', 1)[0]] = key
    shipped = {k.replace('//', '/') for k in gamefiles.index()}
    exported = {}

    def exports_of(gp):
        if gp not in exported:
            pkg = Package(files[ours[gp]])
            nm = [n for n, _h in pkg.names]
            exported[gp] = {(nm[struct.unpack_from('<i', e['meta'], 16)[0]],
                             struct.unpack_from('<i', e['meta'], 20)[0]):
                            _class_name(pkg, nm, struct.unpack_from('<i', e['meta'], 0)[0])
                            for e in pkg.exports}
        return exported[gp]
    probs, count, level_refs = [], 0, set()
    for gp, key in sorted(ours.items()):
        is_fab = gp.startswith(fablibrary.GAME_ROOT)
        pkg = Package(files[key])
        nm = [n for n, _h in pkg.names]
        for rec in pkg.imports:
            _cp, _n1, cls, _n2, outer, obj, objnum = struct.unpack('<7i', rec)
            if outer == 0:
                path = _fname(nm, obj, objnum)
                if not is_fab and not path.startswith(fablibrary.GAME_ROOT):
                    continue
                count += 1
                if path.startswith(fablibrary.GAME_ROOT):
                    if not is_fab:
                        level_refs.add(path)
                    if path not in ours:
                        probs.append('%s imports %s: not in the pak' % (gp, path))
                    continue
                if nm[cls] != 'Package' or path.startswith('/Script/'):
                    continue
                rel = ('../../../OMD/Content' + path[len('/Game'):] if path.startswith('/Game/')
                       else '../../../Engine/Content' + path[len('/Engine'):])
                if rel + '.uasset' not in shipped and rel + '.umap' not in shipped:
                    probs.append('%s imports %s: not in the pak and not shipped' % (gp, path))
                continue
            if outer > 0:
                continue
            o = struct.unpack('<7i', pkg.imports[-outer - 1])
            target = _fname(nm, o[5], o[6])
            if not target.startswith(fablibrary.GAME_ROOT) or target not in ours:
                continue
            count += 1
            ex = exports_of(target)
            key2 = (nm[obj], objnum)
            if key2 not in ex:
                probs.append('%s imports %s.%s (FName number %d): no such export'
                             % (gp, target, nm[obj], objnum))
            elif ex[key2] != nm[cls]:
                probs.append('%s imports %s.%s as %s; it is a %s'
                             % (gp, target, nm[obj], nm[cls], ex[key2]))
    out.append(('Fab: every import of and into library packages resolves', not probs,
                '%d imports%s' % (count, ('; ' + '; '.join(probs[:3])) if probs else '')))
    placed, _problems = resolve(plan)
    want_refs = {entry['package'] for _n, _e, entry, _m in placed}
    out.append(('Fab: the level imports exactly the placed meshes', level_refs == want_refs,
                '%d placed, %d imported' % (len(want_refs), len(level_refs))))
    from riftlord.build import leveltitle
    #: the mission's own level, whatever its Working Title names it
    for key in sorted(files):
        if leveltitle.is_level(key):
            out.append(gate_level(files[key], files[key[:-5] + '.uexp'], plan))
    if placed:
        from riftlord.mapeditor import fabcollision
        out.append(fabcollision.gate(plan))
    return out


#: Positions and extents read back from the level must match the plan within this (uu).
PLACE_TOLERANCE = 0.05
#: Export names this module writes (PIECE_NAME, GRID_NAME, with _free_name's letter).
OURS = re.compile(r'^RiftLordFab(Grid)?\d+[A-Z]*$')


def gate_level(uasset, uexp, plan):
    """The level holds each Fab piece (its mesh, at its place, at its scale with any mirror,
    listed, VisibilityId -1, no donor streaming data) and each grid (place, extent, bits)."""
    from riftlord.build import levelbuild
    from riftlord.build import apply as applier
    from riftlord.formats.ue.uassetio import Package
    pkg = Package(uasset, uexp)
    pkg.load_payloads()
    nm = [n for n, _h in pkg.names]
    b = levelbuild.Builder.around(uasset, uexp)
    listed = set(b.actor_list())

    def props(i):
        d = pkg.exports[i - 1]['data']
        return d, {n: (k, at, sz) for n, k, at, sz in applier._properties(d, nm)}

    def vec(i, field):
        d, p = props(i)
        return struct.unpack_from('<3f', d, p[field][1]) if field in p else None

    def obj(i, field):
        d, p = props(i)
        return struct.unpack_from('<i', d, p[field][1])[0] if field in p else 0

    def import_path(index):
        o = struct.unpack('<7i', pkg.imports[-index - 1])
        outer = struct.unpack('<7i', pkg.imports[-o[4] - 1])
        return _fname(nm, outer[5], outer[6])
    got_pieces, got_grids, probs = [], [], []
    for i, e in enumerate(pkg.exports, 1):
        name = nm[struct.unpack_from('<i', e['meta'], 16)[0]]
        if not OURS.match(name) or not b._root_component(i):
            continue
        root = obj(i, 'RootComponent')
        if i not in listed:
            probs.append('%s is not in the actor list' % name)
        if name.startswith('RiftLordFabGrid'):
            box = applier._child_named(b, i, GRID_BOX)
            d, p = props(box) if box else (b'', {})
            bits = d[p['TrapPlacementTypes'][1]] if 'TrapPlacementTypes' in p else None
            got_grids.append((vec(root, 'RelativeLocation'), vec(box, 'BoxExtent') if box else None,
                              bits))
            continue
        mesh = obj(root, 'StaticMesh')
        d, p = props(root)
        if any(f in p for f in CLEARED_ON_PIECE):
            probs.append('%s keeps donor streaming data' % name)
        if 'VisibilityId' in p and struct.unpack_from('<i', d, p['VisibilityId'][1])[0] != -1:
            probs.append('%s VisibilityId is not -1' % name)
        if 'Mobility' not in p or nm[struct.unpack_from('<i', d, p['Mobility'][1])[0]] != PIECE_MOBILITY:
            probs.append('%s is not %s' % (name, PIECE_MOBILITY))
        if lod_data(d, b._terminator(d) + 8)[1]:
            probs.append('%s keeps donor LODData (the donor\'s lightmap)' % name)
        got_pieces.append((import_path(mesh) if mesh < 0 else None, vec(root, 'RelativeLocation'),
                           vec(root, 'RelativeScale3D') or (1.0, 1.0, 1.0)))

    def near(a, bb):
        return a is not None and bb is not None and max(abs(a[k] - bb[k]) for k in range(3)) <= PLACE_TOLERANCE
    want_pieces = [(entry['package'],) + tuple(applier.transform_of(m)[0::2])
                   for _n, _e, entry, m in placed_of(plan)]
    for pkg_path, at, scale in want_pieces:
        hit = next((g for g in got_pieces if g[0] == pkg_path and near(g[1], at)), None)
        if hit is None:
            probs.append('no %s at %s' % (pkg_path.rsplit('/', 1)[-1], [round(v) for v in at]))
            continue
        got_pieces.remove(hit)
        if max(abs(hit[2][k] - scale[k]) for k in range(3)) > SCALE_TOLERANCE:
            probs.append('%s at %s has scale %s, not %s' % (
                pkg_path.rsplit('/', 1)[-1], [round(v) for v in at],
                [round(v, 3) for v in hit[2]], [round(v, 3) for v in scale]))
    for g in world_grids(plan):
        at = applier.transform_of(grid_matrix(g['axes'], g['center']))[0]
        hit = next((x for x in got_grids if near(x[0], at) and near(x[1], g['extent'])
                    and x[2] == g['bits']), None)
        if hit is None:
            probs.append('no grid %s at %s' % (g['source'], [round(v) for v in at]))
        else:
            got_grids.remove(hit)
    probs += ['unplanned %s' % (p[0],) for p in got_pieces]
    mirrored = sum(1 for _p, _a, s in want_pieces if s[0] * s[1] * s[2] < 0)
    probs += ['unplanned grid at %s' % (g[0],) for g in got_grids]
    return ('Fab: the level holds every piece and grid where the plan put them', not probs,
            '%d pieces (%d mirrored), %d grids%s' % (len(want_pieces), mirrored,
                                                     len(world_grids(plan)),
                                                     ('; ' + '; '.join(probs[:4])) if probs else ''))


def placed_of(plan):
    return resolve(plan)[0]
