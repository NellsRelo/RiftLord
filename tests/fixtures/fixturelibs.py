"""The piece libraries and mod paks the test missions (tests/fixtures/missions) use, written
into a folder of the caller's, never the user's Fab library, terrain kits or ~mods.

    python3 tests/fixtures/fixturelibs.py OUT
    python3 tests/fixtures/fixturelibs.py OUT --run-paks DEST   # a gate run's Paks folder
    python3 tests/fixtures/fixturelibs.py --pin NAME...   # pin real mod paks (PINNED; paths
                                                          # under Paks, bare names in ~mods)
    import fixturelibs; roots = fixturelibs.ensure(OUT)   # {'fab', 'terrain', 'paks'}
    fixturelibs.pinned_mod('Wraiths_P.pak')               # a pinned copy, or None

- Fab (OUT/fab): FAB_PACK, made-up pieces written by riftlord.fab.library.write_pieces:
  FAB_PIECES (a wall box with a suggested wall grid, a floor slab), drawn with a shipped
  material.
- Terrain (OUT/terrain): the terrain kit of TERRAIN_LEVEL (riftlord.terrainkit.build), cut
  from that map's own ground.
- Paks (OUT/paks, a Paks folder for RIFTLORD_SUPPORTED_PAKS): ~mods/MINION_MOD_P.pak, a
  minion mod with one row (MINION_ROW, the shipped MINION_DONOR row under a new name and
  Guid) and a Minions.yaml carrying one package (CARRIED, a shipped material instance under
  the mod's folder); and a stand-in of each supported mod (supportedmods.MODS), each class
  a package kept with the tests (KEPT) or a shipped Blueprint package renamed to it, with
  the other packages it must carry (KEPT_EXTRA).
A library is written again only when it is missing or its build is not the current one
(the writer's build id, from its code and the pieces); the paks when their bytes differ.
"""

import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

#: The made-up Fab library's pack id and title.
FAB_PACK = 'RiftLordFixtureKit'
FAB_TITLE = 'Rift Lord Fixture Kit'
#: The shipped material its pieces are drawn with.
FAB_MATERIAL = '/Game/Environments/CastleOrder/Archways/CastStone/Mat/Archway_INST'
#: name -> (role, size x, y, z in uu, mesh-local box from (-x/2, -y/2, 0)).
FAB_PIECES = {'FixtureWall': ('wall', 600.0, 40.0, 300.0),
              'FixtureSlab': ('floor', 600.0, 600.0, 40.0)}
#: The map whose terrain kit the test missions place a tile of.
TERRAIN_LEVEL = 'Scenario_09'
#: The made-up minion mod: its key, its one row, the shipped row it copies, the row's Guid
#: (as stored) and the package its manifest carries (with the shipped one it copies).
MINION_MOD = 'FixtureMinions'
MINION_ROW = 'FixtureMinions_Grunt'
MINION_DONOR = 'OrcLight'
MINION_GUID = '6669787475726547727568744d696e31'
MINION_TABLE = '/Game/Mods/%s/Tables/%s_AICharacterProtos' % (MINION_MOD, MINION_MOD)
CARRIED = '/Game/Mods/%s/Art/MI_FixtureGrunt' % MINION_MOD
CARRIED_DONOR = FAB_MATERIAL
#: The shipped minion table.
AI_TABLE = '/Game/Data/AICharacterProtos'
#: Real mod paks some tests read (a minion mod, built missions), pinned: copies taken once
#: from a ~mods by `fixturelibs.py --pin NAME...` (a person's step, never a test's), kept on
#: scratch in PINNED_DIR and checked by sha256, so no test reads the game's ~mods. A test whose
#: copy is missing skips those checks. {file name: sha256}
PINNED = {
    # pinned 2026-10-29 from the installed ~mods
    'Wraiths_P.pak': 'dc6d0d4ff1e1e4de81a9c58804ffa730dc99349c6b303819f396daeaff0ff488',
    '900_Cellar_Alpha_1_P.pak': 'c7c43a323a9f42c17688991ea1711b417ba5378b4a43e61b1ad9b613ab90ac61',
    '900_Beset_Alpha_3_P.pak': '3a292a92c77e75fc45f1231fa0c84cc254c2d928a7b5de2d36fa78e4649fb476',
    # the supported mods, for a gate run's Paks folder (run_paks)
    'OrcsMustUI.pak': '732ccef8914402c63dbf371f40bbc6e23ece09f5d5c7712acc5122f4a5262a3c',
    'TreasureChests.pak': '7cdc1d5e3391fbd2e777316d14d1f4a859f04ce7c8a3f182b9a4b92f5805c0ea',
    'CrystalDefense_P.pak': '47f55d267c4db7a4be2c50b90d7e9abc3fb9a10a09cbf2c51cc39175a17a50b1'}
PINNED_DIR = os.path.join(os.environ.get('RIFTLORD_TEST_SCRATCH') or (
    '/mnt/2tb_pro_a/tmp/rift-lord-tests' if os.path.isdir('/mnt/2tb_pro_a/tmp')
    else os.path.join(__import__('tempfile').gettempdir(), 'rift-lord-tests')),
    'fixture-libraries', 'pinned-mods')
_PINNED_OK = {}


def _box(sx, sy, sz):
    """The writer's mesh description of a box (x and y centred, z from 0): one quad per
    face wound to face out, UV0 in uu / 100, UV1 a 3 x 2 lightmap layout with gaps."""
    lo = np.array([-sx / 2, -sy / 2, 0.0])
    hi = np.array([sx / 2, sy / 2, sz])
    faces = [((0, 1), 2, 1.0), ((0, 1), 2, -1.0), ((0, 2), 1, 1.0), ((0, 2), 1, -1.0),
             ((1, 2), 0, 1.0), ((1, 2), 0, -1.0)]
    P, N, UV, UV1, T = [], [], [], [], []
    for k, ((a, b), axis, sign) in enumerate(faces):
        n = np.zeros(3)
        n[axis] = sign
        base = len(P)
        cell = np.array([k % 3, k // 3], np.float64)
        for u, v in ((0, 0), (1, 0), (1, 1), (0, 1)):
            p = np.where(n > 0, hi, lo).astype(np.float64)
            p[a] = (lo[a], hi[a])[u]
            p[b] = (lo[b], hi[b])[v]
            P.append(p)
            N.append(n)
            UV.append((p[a] / 100.0, p[b] / 100.0))
            UV1.append(((cell[0] + 0.05 + 0.9 * u) / 3.0, (cell[1] + 0.05 + 0.9 * v) / 2.0))
        for tri in ((0, 1, 2), (0, 2, 3)):
            i0, i1, i2 = (base + t for t in tri)
            p0, p1, p2 = P[i0], P[i1], P[i2]
            if np.dot(np.cross(p2 - p0, p1 - p0), n) < 0:
                i1, i2 = i2, i1
            T.append((i0, i1, i2))
    return {'positions': np.array(P, np.float32), 'normals': np.array(N, np.float32),
            'tangents': None, 'signs': None,
            'uvs': [np.array(UV, np.float32), np.array(UV1, np.float32)], 'colors': None,
            'triangles': np.array(T, np.int64), 'materials': np.zeros(len(T), np.int64)}


def fab_pieces():
    """write_pieces pieces of FAB_PIECES."""
    out = []
    for name, (role, sx, sy, sz) in sorted(FAB_PIECES.items()):
        out.append({'name': name, 'display_name': name, 'source': '%s/%s' % (FAB_PACK, name),
                    'role': role, 'desc': _box(sx, sy, sz),
                    'materials': [{'path': FAB_MATERIAL, 'slot': 'Stone',
                                   'reasons': ['shipped material']}],
                    'collision': None, 'thumb': {}})
    return out


#: Version of the made-up pieces' shapes (_box); a change gives the library a new build.
FAB_SHAPES = 1


def fab_fingerprint():
    """What shapes the made-up pieces: their table, material and FAB_SHAPES."""
    return hashlib.sha256(json.dumps([FAB_PACK, FAB_MATERIAL, FAB_PIECES, FAB_SHAPES],
                                     sort_keys=True).encode()).hexdigest()


def _current(folder):
    """The library's catalogue `build`, or None when it has none or no report."""
    try:
        with open(os.path.join(folder, 'catalogue.json')) as fh:
            cat = json.load(fh)
        if not os.path.isfile(os.path.join(folder, 'report.json')):
            return None
        return cat.get('build')
    except (OSError, ValueError):
        return None


def fab_build_id():
    """The build id write_pieces gives the made-up library now."""
    from riftlord.fab import library
    return library.h([library.VERSION, library.logic_hash(*library.PIECE_CODE),
                      fab_fingerprint(), 'building'])[:library.BUILD_ID_LEN]


def ensure_fab(out):
    """OUT/<FAB_PACK>, written unless its current build is there. Returns the folder."""
    from riftlord.fab import library
    folder = os.path.join(out, FAB_PACK)
    if _current(folder) == fab_build_id():
        return folder
    library.write_pieces(FAB_PACK, FAB_TITLE, fab_pieces(), out, kind='building',
                         fingerprint=fab_fingerprint(), jobs=1, progress=lambda *_a: None)
    return folder


def ensure_terrain(out):
    """OUT/Terrain_<TERRAIN_LEVEL>, built unless current. Returns the folder."""
    from riftlord.terrainkit import build, sources
    found = sources.discover()
    folder = os.path.join(out, build.pack_id(TERRAIN_LEVEL))
    if build.status(TERRAIN_LEVEL, found.get(TERRAIN_LEVEL) or [], out)['status'] != 'current':
        got = build.build(TERRAIN_LEVEL, out, jobs=1, found=found, progress=lambda *_a: None)
        if got.get('error'):
            raise SystemExit('terrain kit %s: %s' % (TERRAIN_LEVEL, got['error']))
    return folder


def _stem(game_path):
    return 'OMD/Content' + game_path[len('/Game'):]


def _renamed(game_path, new_path):
    """(uasset, uexp) of a shipped package under a new path (package and object names)."""
    from riftlord.build import modtable
    from riftlord.core import gamefiles
    from riftlord.formats.ue.uassetio import Package
    pkg = Package(*gamefiles.package(game_path))
    pkg.load_payloads()
    modtable.rename_package(pkg, game_path, game_path.rsplit('/', 1)[-1], new_path,
                            new_path.rsplit('/', 1)[-1])
    return pkg.write()


def minion_files():
    """{pak entry: bytes} of the made-up minion mod."""
    from riftlord.build import missionproto
    from riftlord.build import modtable
    from riftlord.core import gamefiles
    from riftlord.formats.ue.uassetio import Package
    pkg = Package(*gamefiles.package(AI_TABLE))
    pkg.load_payloads()
    t = missionproto.Table(pkg)
    src = next(r for r in t.rows if r['name'] == MINION_DONOR)
    index = t.pkg.add_name(MINION_ROW)
    t.names = [s for s, _h in t.pkg.names]
    row = {'name': MINION_ROW, 'name_index': index, 'name_number': 0,
           'fields': [missionproto.Field(f.name, f.kind, f.sub, bytes(f.header), bytes(f.payload),
                                         f.bool_value) for f in src['fields']]}
    t.field(row, 'Guid').payload = bytearray(bytes.fromhex(MINION_GUID))
    t.rows = [row]
    modtable.rename_package(t.pkg, AI_TABLE, AI_TABLE.rsplit('/', 1)[-1], MINION_TABLE,
                            MINION_TABLE.rsplit('/', 1)[-1])
    ua, ux = t.write()
    files = {_stem(MINION_TABLE) + '.uasset': ua, _stem(MINION_TABLE) + '.uexp': ux}
    ca, cx = _renamed(CARRIED_DONOR, CARRIED)
    files[_stem(CARRIED) + '.uasset'], files[_stem(CARRIED) + '.uexp'] = ca, cx
    manifest = ('Minions:\n  - Row: %s\n    Guid: "%s"\n    Name: "Fixture Grunt"\n'
                '    Family: Orc\n    Flyer: false\n    Packages:\n      - %s\n'
                % (MINION_ROW, MINION_GUID, CARRIED))
    files['OMD/Content/Mods/%s/OrcsMustUI/Minions.yaml' % MINION_MOD] = manifest.encode()
    return files


#: Class packages kept with the tests (tests/fixtures/mods/<mod id>/<leaf>.uasset/.uexp),
#: used instead of a renamed shipped Blueprint: BP_Crystal (Crystal Defense's build of
#: 2026-10-28), which a build reads (its parent, its float defaults).
KEPT = os.path.join(HERE, 'mods')
#: Other packages a stand-in carries, kept with the tests too: {mod id: [package path]}.
#: OrcsMustUI's MinionInfo: a mod minion's packages import it (riftlord.mapeditor.probe
#: import_problems resolves it in the detected OrcsMustUI pak).
KEPT_EXTRA = {'OrcsMustUI': ['/Game/Mods/OrcsMustUI/MinionInfo']}


def stand_in_files(mod):
    """{pak entry: bytes} of a supported mod's stand-in: each feature class from KEPT, else a
    shipped Blueprint package renamed to it (tests/ui_smoke/fakemods.renamed_package); and
    its KEPT_EXTRA packages."""
    sys.path.insert(0, os.path.join(ROOT, 'tests', 'ui_smoke'))
    import fakemods
    from riftlord.core import supportedmods
    files = {fakemods.MARKER % mod.id: b'fake'}
    for feat in mod.features:
        for cls in feat.classes:
            stem = supportedmods.package_entry(cls)
            kept = os.path.join(KEPT, mod.id, cls.split('.', 1)[0].rsplit('/', 1)[-1])
            if os.path.isfile(kept + '.uasset'):
                for ext in ('.uasset', '.uexp'):
                    with open(kept + ext, 'rb') as fh:
                        files[stem + ext] = fh.read()
                continue
            files[stem + '.uasset'], files[stem + '.uexp'], _src = fakemods.renamed_package(cls)
    for path in KEPT_EXTRA.get(mod.id, []):
        kept = os.path.join(KEPT, mod.id, path.rsplit('/', 1)[-1])
        for ext in ('.uasset', '.uexp'):
            with open(kept + ext, 'rb') as fh:
                files['OMD/Content/' + path[len('/Game/'):] + ext] = fh.read()
    return files


def _write_pak(path, files):
    """Write `files` as a pak at `path` unless one with the same entries is there."""
    from riftlord.core import paksource
    if os.path.isfile(path):
        try:
            if paksource.entries(path) == files:
                return path
        except Exception:                            # noqa: BLE001
            pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    paksource.write_pak(path + '.part', files)
    os.replace(path + '.part', path)
    return path


def ensure_paks(out):
    """OUT as a Paks folder: the minion mod and every supported mod's stand-in."""
    from riftlord.core import supportedmods
    _write_pak(os.path.join(out, '~mods', '%s_P.pak' % MINION_MOD), minion_files())
    for mod in supportedmods.MODS:
        _write_pak(os.path.join(out, *mod.files[0].split('/')), stand_in_files(mod))
    return out


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for block in iter(lambda: fh.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def pinned_mod(name):
    """The pinned copy of real mod pak `name` (PINNED), or None when it is missing or its
    bytes are not the pinned ones."""
    path = os.path.join(PINNED_DIR, name)
    try:
        st = os.stat(path)
    except OSError:
        return None
    stamp = (st.st_size, st.st_mtime_ns)
    if _PINNED_OK.get(path, (None,))[0] != stamp:
        _PINNED_OK[path] = (stamp, PINNED.get(name) == _sha256(path))
    return path if _PINNED_OK[path][1] else None


def pin(names, paks):
    """Copy each named pak (a path under the Paks folder `paks`; a bare name is in ~mods)
    into PINNED_DIR by its file name; {file name: sha256} to put in PINNED."""
    import shutil
    os.makedirs(PINNED_DIR, exist_ok=True)
    out = {}
    for name in names:
        rel = name if '/' in name else '~mods/' + name
        dest = os.path.join(PINNED_DIR, os.path.basename(rel))
        shutil.copyfile(os.path.join(paks, *rel.split('/')), dest + '.part')
        os.replace(dest + '.part', dest)
        out[os.path.basename(rel)] = _sha256(dest)
    return out


def run_paks(out, dest):
    """A Paks folder for one gate run at `dest`: ensure_paks(out)'s, with each supported mod's
    stand-in replaced by its pinned real copy where there is one (the UI cases draw the
    chests and the Crystal from them). Returns dest."""
    import shutil
    from riftlord.core import supportedmods
    shutil.copytree(ensure_paks(out), dest, ignore=shutil.ignore_patterns('*.part'))
    for mod in supportedmods.MODS:
        for rel in mod.files:
            pinned = pinned_mod(os.path.basename(rel))
            if pinned:
                shutil.copyfile(pinned, os.path.join(dest, *rel.split('/')))
    return dest


def ensure(out):
    """Everything under OUT: {'fab': OUT/fab, 'terrain': OUT/terrain, 'paks': OUT/paks} (the
    folders RIFTLORD_FAB_LIBRARY, RIFTLORD_TERRAIN_LIBRARY and RIFTLORD_SUPPORTED_PAKS
    name)."""
    roots = {k: os.path.join(out, k) for k in ('fab', 'terrain', 'paks')}
    for r in roots.values():
        os.makedirs(r, exist_ok=True)
    ensure_fab(roots['fab'])
    ensure_terrain(roots['terrain'])
    ensure_paks(roots['paks'])
    return roots


def environment(out):
    """The environment variables that point a build or server at ensure(out)'s folders."""
    roots = ensure(out)
    return {'RIFTLORD_FAB_LIBRARY': roots['fab'], 'RIFTLORD_TERRAIN_LIBRARY': roots['terrain'],
            'RIFTLORD_SUPPORTED_PAKS': roots['paks']}


if __name__ == '__main__':
    if sys.argv[1:2] == ['--pin'] and len(sys.argv) > 2:
        from riftlord.core import gamepath
        print(json.dumps(pin(sys.argv[2:], gamepath.paks()), indent=1))
        raise SystemExit(0)
    if len(sys.argv) == 4 and sys.argv[2] == '--run-paks':
        got = environment(os.path.abspath(sys.argv[1]))
        got['RIFTLORD_SUPPORTED_PAKS'] = run_paks(os.path.join(os.path.abspath(sys.argv[1]),
                                                              'paks'), sys.argv[3])
        print(json.dumps(got))
        raise SystemExit(0)
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    print(json.dumps(environment(os.path.abspath(sys.argv[1]))))
