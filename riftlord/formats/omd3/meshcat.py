"""Measure every OMD3 static mesh, so detail can be placed instead of approximated.

    python3 meshcat.py --build          # writes omd3-meshes.json
    python3 meshcat.py --mesh RailingShortC
    python3 meshcat.py --like 0.17 1.34 0.57     # find meshes this size, in cells

## Why

Kit pieces are locked to OMD3's 300uu grid, and every OMD2 module is a fraction of
a cell -- a wall panel is 0.33 x 1.33 x 2.00, a railing 0.17 x 1.34 x 0.57. Nothing
in the kit is that small, so detail converted as kit pieces comes out as the
nearest cell-sized block, which is why arches read as walls and railings vanish.

A `StaticMeshActor` has no such constraint. It goes exactly where OMD2 put it, at
OMD2's rotation and scale. To choose one, we need to know how big each OMD3 mesh
is, which is what this measures.

## Where the bounds live

Not in the render data, and not where the name suggests. `UStaticMesh` carries an
`ExtendedBounds` property that looks like an `FBoxSphereBounds` but is **159 bytes**
-- because it is serialised as nested tagged properties, not as a raw struct:

    Origin        StructProperty Vector   49 + 12
    BoxExtent     StructProperty Vector   49 + 12
    SphereRadius  FloatProperty           25 + 4
    None                                   8
                                         = 159

Reading seven floats at the tag head, which is what a raw struct would need, gives
zeros and looks like a mesh with no bounds. `BoxExtent` is a **half**-extent.

The radius is a cheap check on the parse: it should be close to the length of the
extent vector. It is normally a little smaller, because UE computes it from the
vertices rather than from the box corner.
"""

import argparse
import collections
import json
import math
import os
import struct
import sys
from riftlord.core import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.core import gamefiles  # noqa: E402
from riftlord.build import levelbuild  # noqa: E402
from riftlord.core import transform  # noqa: E402
from riftlord.formats.ue.uassetio import Package  # noqa: E402

CATALOGUE = gamepath.data('omd3-meshes.json')

#: Folders that cannot hold a mesh anyone would place in a level: localisation,
#: audio, the editor's own gizmo art, engine sample content, and the minions, whose
#: 5,126 assets are animated characters rather than scenery.
#:
#: This is a deny-list on purpose. It was an allow-list of `/Environments/` alone,
#: which silently left out all 1,714 assets under `/Traps/` and every prop that
#: lives somewhere else, and nothing said so -- a mesh that is not scanned looks
#: exactly like a mesh that does not exist.
SKIP_ROOTS = (
    '/L10N/', '/Sounds/', '/Characters/', '/UI/', '/Movies/', '/Localization/',
    '/EditorMeshes/', '/EditorMaterials/', '/EditorResources/', '/VREditor/',
    '/EditorBlueprintResources/', '/EditorLandscapeResources/',
    '/EngineMaterials/', '/EngineMeshes/', '/EngineResources/', '/EngineSounds/',
    '/EngineFonts/', '/EngineDebugMaterials/', '/EngineDamageTypes/',
    '/StarterContent/', '/Mannequin/', '/MobileResources/', '/ThirdPersonCPP/',
    '/MapTemplates/', '/Tutorial/', '/ArtTools/', '/Datasmith/',
    '/PlaceholderTextures/', '/Maps/Cinematic/', '/Maps/UI/',
)

#: Kept for callers that still pass roots explicitly.
ROOTS = ()


def placeable_asset(path):
    """Whether an asset is worth opening in the hunt for a static mesh."""
    return not any(skip in path for skip in SKIP_ROOTS)


def _walk(data, names, start, end):
    """Tagged properties between two offsets: {name: (kind, payload offset, size)}."""
    out = {}
    o = start
    while o + 8 <= end:
        ni = struct.unpack_from('<i', data, o)[0]
        if not 0 <= ni < len(names):
            break
        name = names[ni]
        if name == 'None':
            break
        ti = struct.unpack_from('<i', data, o + 8)[0]
        kind = names[ti] if 0 <= ti < len(names) else ''
        size = struct.unpack_from('<i', data, o + 16)[0]
        head = 24 + 1
        if kind == 'BoolProperty':
            head, size = 26, 0
        elif kind == 'StructProperty':
            head = 24 + 8 + 17
        elif kind in ('ArrayProperty', 'ByteProperty', 'EnumProperty'):
            head = 24 + 8 + 1
        out[name] = (kind, o + head, size)
        o += head + size
    return out


def bounds_of(package_path):
    """(origin, half-extent, radius) for one static mesh asset, or None.

    Returns None both for an asset that is not a static mesh at all -- a material,
    a texture, a Blueprint -- and for one that is but could not be measured, and the
    caller has to tell those apart: a single counter covering both read as "6,956
    meshes we cannot measure" when almost all of them were never meshes.
    """
    ua, ux = gamefiles.package(package_path)
    pkg = Package(ua, ux)
    pkg.load_payloads()
    names = [n for n, _h in pkg.names]
    for e in pkg.exports:
        cls = levelbuild.resolve(
            pkg, struct.unpack_from('<i', e['meta'], 0)[0])[1] or ''
        if cls != 'StaticMesh':
            continue
        data = e['data']
        top = _walk(data, names, 0, len(data))
        got = top.get('ExtendedBounds')
        if not got:
            continue
        _kind, at, size = got
        inner = _walk(data, names, at, at + size)
        # A tagged struct leaves out a field that still holds its default, so a mesh
        # centred on its own origin has no `Origin` at all: the whole property comes
        # to 98 bytes instead of 159, exactly the 61 an omitted vector tag takes.
        # Requiring it threw out every well-centred mesh in the game.
        origin = (0.0, 0.0, 0.0)
        extent = radius = None
        for field, (_k, ia, isz) in inner.items():
            if field == 'Origin' and isz == 12:
                origin = struct.unpack_from('<3f', data, ia)
            elif field == 'BoxExtent' and isz == 12:
                extent = struct.unpack_from('<3f', data, ia)
            elif field == 'SphereRadius' and isz == 4:
                radius = struct.unpack_from('<f', data, ia)[0]
        if extent is not None:
            return origin, extent, radius
    return None


def sane(extent, radius, tolerance=0.25):
    """Whether the radius agrees with the extent, as a check on the parse.

    The radius has to lie between the longest half-extent and the distance to the
    box corner. Both ends matter and neither is the middle: comparing the radius
    against the corner distance alone rejects every flat mesh, because a disc
    772 x 769 x 87 has a corner 546 away and a bounding sphere of only 392. That
    threw out the rift base, both rift discs and 48 other pieces -- all of them
    correctly measured, all of them the flat ones a floor is made of.
    """
    if not radius or radius <= 0:
        return False
    longest = max(extent)
    corner = math.sqrt(sum(v * v for v in extent))
    if corner <= 0:
        return False
    return longest * (1 - tolerance) <= radius <= corner * (1 + tolerance)


def build(out_path=CATALOGUE, roots=None, limit=None):
    """Measure every static mesh the game ships and write the catalogue.

    `roots` narrows the scan to paths containing one of those strings, for a quick
    pass over one folder; left out, everything but `SKIP_ROOTS` is scanned.
    """
    assets = [a for a in sorted(gamefiles.find(ext='.uasset'))
              if (any(r in a for r in roots) if roots else placeable_asset(a))]
    if limit:
        assets = assets[:limit]
    out, bad = {}, collections.Counter()
    for a in assets:
        name = a.rsplit('/', 1)[-1][:-len('.uasset')]
        try:
            got = bounds_of(a[:-len('.uasset')])
        except Exception as exc:                     # noqa: BLE001
            bad[type(exc).__name__] += 1
            continue
        if not got:
            # Almost all of these are not meshes: the folders hold materials,
            # textures and Blueprints too. Saying so keeps the number from reading
            # as a pile of meshes we failed on, which is how a real failure stayed
            # hidden inside it.
            bad['not a static mesh, or unmeasurable'] += 1
            continue
        origin, extent, radius = got
        if not sane(extent, radius):
            bad['radius disagrees with extent'] += 1
            continue
        size = [2.0 * v for v in extent]
        out[name] = {
            'path': a[:-len('.uasset')],
            'category': category_of(a, name),
            'origin': [round(v, 2) for v in origin],
            'extent': [round(v, 2) for v in extent],
            'size': [round(v, 2) for v in size],
            'cells': [round(v / transform.OMD3_CELL, 3) for v in size],
        }
    with open(out_path, 'w') as fh:
        json.dump({'meshes': out, 'skipped': dict(bad)}, fh, indent=1,
                  sort_keys=True)
    return out, bad


def load(path=CATALOGUE):
    with open(path) as fh:
        return json.load(fh)['meshes']


def like(cells, catalogue=None, limit=10, tolerance=0.5):
    """Meshes whose footprint is closest to `cells`, best first.

    Compared on the sorted dimensions, so a mesh laid out along a different axis
    still matches -- an actor's rotation is ours to choose.
    """
    catalogue = catalogue or load()
    want = sorted(cells)
    scored = []
    for name, info in catalogue.items():
        got = sorted(info['cells'])
        err = sum(abs(want[i] - got[i]) for i in range(3))
        scored.append((err, name, info))
    scored.sort(key=lambda r: (round(r[0], 4), r[1]))
    return [(round(e, 3), n, i) for e, n, i in scored[:limit]
            if e <= tolerance * 3 + 1e-9]



#: What an OMD2 mesh's own path says it is, and the words OMD3 names such a thing.
#:
#: Size alone is not a match. A railing 0.17 x 1.34 x 0.57 cells is closest in size
#: to `Window_Pointed_2x`, and wall trim to `Door_Catacomb` -- both plausible boxes
#: and both wrong. What makes the choice is the kind, which OMD2's own folders
#: record: `art/Act2/Railing/RailingA2.vmesh` is a railing and OMD3 has several.
KIND_WORDS = (
    (('railing',), ('railing',)),
    (('arch',), ('arch', 'archway')),
    (('column', 'pillar'), ('column', 'pillar')),
    (('buttress',), ('buttress', 'column')),
    (('stair', 'step'), ('stair', 'step', 'ramp')),
    (('window',), ('window')),
    (('door', 'gate'), ('door', 'gate', 'portcullis')),
    (('banner', 'flag', 'pennant'), ('banner', 'pennant', 'flag')),
    (('torch', 'brazier', 'lamp', 'lantern'), ('torch', 'brazier', 'lamp')),
    (('barrel', 'crate', 'box'), ('barrel', 'crate', 'box')),
    (('rock', 'boulder', 'stone', 'cliff'), ('rock', 'stone', 'boulder', 'cliff')),
    (('beam', 'wood', 'plank'), ('beam', 'wood', 'plank')),
    (('roof', 'tile'), ('roof',)),
    (('crenel', 'cren', 'battlement'), ('cren', 'battlement')),
    (('floor',), ('floor',)),
    (('ceiling',), ('ceiling',)),
    (('wall',), ('wall',)),
)


def kind_of(mesh_path):
    """The OMD3 name words that suit this OMD2 mesh, or () if nothing fits."""
    low = str(mesh_path).replace('\\', '/').lower()
    for omd2_words, omd3_words in KIND_WORDS:
        if any(w in low for w in omd2_words):
            return omd3_words
    return ()


def match(mesh_path, cells, catalogue=None, limit=8, tolerance=1.5):
    """OMD3 meshes that suit an OMD2 mesh: right kind first, then closest size.

    Falls back to size alone when the path says nothing recognisable, and says
    which it did, because a size-only match is a guess and should be reviewed.
    """
    catalogue = catalogue or load()
    words = kind_of(mesh_path)
    pool = catalogue
    how = 'size only'
    if words:
        narrowed = {n: i for n, i in catalogue.items()
                    if any(w in n.lower() for w in words)}
        if narrowed:
            pool, how = narrowed, 'kind + size'
    want = sorted(cells)
    scored = []
    for name, info in pool.items():
        got = sorted(info['cells'])
        err = sum(abs(want[i] - got[i]) for i in range(3))
        scored.append((round(err, 4), name, info))
    scored.sort(key=lambda r: (r[0], r[1]))
    return how, [(e, n, i) for e, n, i in scored[:limit] if e <= tolerance]


#: What each mesh is for, which is how OMD3 splits a level into sublevels: the
#: castle geometry, the foliage and the decoration each stream separately
#: ([[omd3-custom-map-packaging]] -- "Castle typically contains the main level
#: geometry, foliage contains foliage, vfx contains emitters").
#:
#: Decided from the asset path first, because that is what the artists organised
#: by, and from the name only as a fallback. Order matters: a tree in
#: `LandScapes/Foliage` is foliage, not landscape.
CATEGORY_RULES = (
    ('foliage', ('/foliage/', '/grass/', '/trees/', '/plants/', '/ivy/')),
    ('foliage', ('grass', 'ivy', 'tree', 'branch', 'leaf', 'leaves', 'bush',
                 'fern', 'clover', 'moss', 'vine', 'flower', 'root')),
    ('rift', ('/riftbase/', '/shell/bp/',)),
    ('rift', ('rift', 'dais')),
    ('trap', ('/traps/', '/devices/')),
    ('trap', ('trap', 'barricade', 'ballista', 'brimstone', 'tarpit')),
    ('castle', ('/castleorder/', '/mastermaterials/enviro/structure/')),
    ('castle', ('wall', 'floor', 'ceiling', 'stair', 'column', 'archway',
                'arch', 'pillar', 'buttress', 'railing', 'balcony', 'tower',
                'portcullis', 'battlement', 'cren', 'trim', 'roof', 'door',
                'gate', 'window', 'bridge')),
    ('terrain', ('/landscapes/', '/terrain/', '/sand/', '/snowice/')),
    ('terrain', ('rock', 'cliff', 'stone_', 'ground', 'terrain', 'sand',
                 'snow', 'ice', 'cave', 'lava', 'acid', 'water')),
    ('effect', ('/emitters/', '/rain_assets/', '/vfx/', '/fx/', '/vectorfields/')),
    ('effect', ('emitter', 'particle', 'fx_', 'splash', 'smoke', 'fire')),
    ('decoration', ('/props/', '/furniture/', '/medieval_furniture', '/decor',
                    '/pickups/', '/weapons/', '/trinkets/')),
)

#: Anything none of the rules claim.
DEFAULT_CATEGORY = 'decoration'

#: Folders of the player's arsenal (weapons and trinkets): their meshes are 'trap', their
#: effects (under a '/fx/' folder) 'effect', whatever the asset is named.
ARSENAL_ROOTS = ('/weapons/', '/trinkets/')

#: The meshes whose footprint goes on the trap-grid lattice when placed in the Map Editor:
#: floor tiles and stairs by their folder, the crenellation shelf by its name. Kit
#: placement census section 6 (docs/research/kit-placement-census.md), footprint on the
#: 150 uu lattice: 1,063 of 1,824 shipped placements under Floors, 201 of 489 under
#: Stairs, 302 of 671 of Cren_Shelf_1x1; every other castle folder 26% or less.
FOOTPRINT_LATTICE = ('/castleorder/floors/', '/castleorder/stairs/', 'cren_shelf_1x1')


def footprint_on_lattice(path, name=''):
    """True when a mesh's footprint snaps to the lattice (FOOTPRINT_LATTICE): a folder
    needle (leading '/') matched in its path, else a needle in its name."""
    low = str(path).lower()
    leaf = str(name or path.rsplit('/', 1)[-1]).lower()
    return any(n in low if n.startswith('/') else n in leaf for n in FOOTPRINT_LATTICE)


def category_of(path, name=''):
    """Which shelf of the kit one asset belongs on.

    'rift' and 'trap' come before the shape words on purpose: a rift platform is a
    floor and a barricade is a wall, and filed by shape they disappear into the
    thousand other floors and walls. The arsenal's folders come before every rule: a
    weapon or trinket is filed with the traps, never by a word in its name
    (Amulet_Ice is not terrain, Trinket_EmptyRift is not a rift).
    """
    low = str(path).lower()
    leaf = str(name or path.rsplit('/', 1)[-1]).lower()
    if any(root in low for root in ARSENAL_ROOTS):
        return 'effect' if '/fx/' in low else 'trap'
    for kind, needles in CATEGORY_RULES:
        for needle in needles:
            if needle.startswith('/'):
                if needle in low:
                    return kind
            elif needle in leaf:
                return kind
    return DEFAULT_CATEGORY


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--build', action='store_true')
    ap.add_argument('--mesh')
    ap.add_argument('--like', nargs=3, type=float, metavar=('W', 'D', 'H'))
    ap.add_argument('--for', dest='for_mesh',
                    help='an OMD2 mesh path, to match by kind as well as size')
    ap.add_argument('--limit', type=int, default=12)
    args = ap.parse_args(argv)

    if args.build:
        out, bad = build()
        print('measured %d mesh(es)' % len(out))
        for reason, n in bad.most_common(6):
            print('   %5d skipped: %s' % (n, reason))
        sizes = collections.Counter()
        for info in out.values():
            sizes[tuple(round(c) for c in sorted(info['cells']))] += 1
        print('   commonest footprints (cells): %s'
              % dict(sizes.most_common(6)))
        return 0

    if args.mesh:
        cat = load()
        info = cat.get(args.mesh)
        if not info:
            hits = [n for n in cat if args.mesh.lower() in n.lower()]
            print('no exact match; did you mean: %s' % hits[:8])
            return 1
        print('%s\n   %s\n   size  %s uu\n   cells %s'
              % (args.mesh, info['path'], info['size'], info['cells']))
        return 0

    if args.like:
        if args.for_mesh:
            how, hits = match(args.for_mesh, args.like, limit=args.limit)
            print('matched by %s' % how)
            for err, name, info in hits:
                print('  %-42s err %-6.3f cells %s' % (name, err, info['cells']))
            return 0
        for err, name, info in like(args.like, limit=args.limit):
            print('  %-42s err %-6.3f cells %s' % (name, err, info['cells']))
        return 0

    ap.error('need --build, --mesh or --like')


if __name__ == '__main__':
    raise SystemExit(main())
