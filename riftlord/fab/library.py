# Generated from OMD2-Converter/thirdparty/fabconvert/library.py by thirdparty/fabconvert/port_riftlord.py; edit the source.
"""Convert one Fab pack from the author's vault into a cooked OMD3 asset library.

    from riftlord.fab import library
    summary = library.convert('/home/me/Documents/EpicVault/dsa60895b3c928aV1', out_dir, jobs=12)

Output (thirdparty/LIBRARY-FORMAT.md is the contract):
    <out>/<PackId>/<PackId>.pak       every cooked package, mounted at /Game/Mods/Fab/<PackId>/
    <out>/<PackId>/catalogue.json     per mesh and material: what Rift Lord needs to place it
    <out>/<PackId>/thumbs/<mesh>.png  offline renders
    <out>/<PackId>/report.json        counts, sizes, every check and its result

Steps: list meshes -> analyse each slot material -> decide variants (vertex-colour constants,
mesh-paint blends) -> bake variants -> dedupe and encode textures -> write MICs -> write meshes
(geometry, collision, analysis, thumbnail) -> gates (pkgcheck, imports, strict readback, MIC
value readback, texture format) -> pak -> preflight against the proven paks -> catalogue.

Incremental: bakes, encoded textures and written meshes are cached under the cache dir keyed
by the converter version, the pack's file fingerprint and the job, and each mesh's grid search
by the geometry it reads and the grid-search code (cache/grid); output is deterministic
(sorted names, no clocks), so a rerun on the same input writes byte-identical files.
Vault data is untrusted: run under `python3 -I`; the vault is only read.
"""

import contextlib
import copy
import hashlib
import io
import json
import multiprocessing
import os
import pickle
import re
import shutil
import struct
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path[:0] = [HERE, REPO, os.path.join(REPO, 'UE Assets'), os.path.join(REPO, 'thirdparty', 'survey-tools'),
                os.path.join(REPO, 'thirdparty', 'rounds', 'mat2')]

from PIL import Image                                # noqa: E402

from riftlord.fab import analyse as AN                                 # noqa: E402
from riftlord.fab import permesh as PM                                 # noqa: E402
from riftlord.fab import matconvert as MC                              # noqa: E402
from riftlord.fab import meshconvert as MS                             # noqa: E402
from riftlord.fab import micwrite as MW                                # noqa: E402
from riftlord.fab import packs as PK                                   # noqa: E402
from riftlord.fab import thumbs as TH                                  # noqa: E402

#: Bump when any output byte may change for the same input (invalidates the caches).
VERSION = 'fabconvert-1'
#: Where every package goes: /Game/Mods/Fab/<PackId>/...
MOUNT_ROOT = '/Game/Mods/Fab'
#: Hex digits of the build folder under the pack root.
BUILD_ID_LEN = 8
#: Side of the per-texture preview PNGs in previews/ (Map Editor previews).
PREVIEW_PX = 256
#: Shipped donor MICs per route (rounds mat1 / mat2 proved each in game).
DONOR = {
    'prop': '/Game/Environments/CastleOrder/MetalWork/Mat/Metal_Set_Dark_INST',
    'prop_em': '/Game/Environments/LandScapes/Crystals/Mat/Crystal_1_INST',
    'prop2s': '/Game/Environments/Props/Lizardman_WatchTower/Mat/Lizardman_WatchTower_2sided_INST',
    'alphamask': '/Game/Traps/RollingBoulder_WM/Mat/RollingBoulderTrap_INST',
    'foliage': '/Game/Environments/LandScapes/Foliage/Fern/Mat/Fern_1_INST',
    'paint': '/Game/Environments/CastleOrder/Walls/StoneWalls/Mat/Old/StoneWalls_Pattern_01',
}
MASTER = {
    'prop': '/Game/MasterMaterials/Enviro/Prop/Master_Prop',
    'prop_em': '/Game/MasterMaterials/Enviro/Prop/Master_Prop',
    'prop2s': '/Game/MasterMaterials/Enviro/Prop/Master_Prop',
    'alphamask': '/Game/MasterMaterials/Traps/Master_Trap_AlphaMask',
    'foliage': '/Game/MasterMaterials/Enviro/Terrain/Foliage/Master_Foliage',
    'paint': '/Game/MasterMaterials/Enviro/Master_Enviro_MeshPaint_Shell',
}
#: Shipped textures used instead of writing a constant one.
SHIPPED_FLAT_NM = '/Game/MasterMaterials/Shared/Flat_NM'
SHIPPED_BLACK = '/Game/MasterMaterials/Shared/Black_BC'
#: Translucent water sections draw with this shipped water instance.
WATER_MIC = '/Game/MasterMaterials/Enviro/Water/Mat/Water_Calm_INST'
#: Weather a route receives (weather agent 2026-10-14, decoded from the shipped DXBC: Master_Prop
#: snow / sand from MPC_Enviro_Level x vertex colour R x up-facing normal (white = full), rain on
#: roughness; ice needs vertex colour G below 1, moss needs a baked lightmap; Master_Trap and
#: Master_Foliage rain only; Master_Enviro_MeshPaint_Shell reads no parameter collection).
WEATHER = {
    'prop': {'snow': True, 'sand': True, 'rain': True, 'ice': False, 'moss': False},
    'alphamask': {'snow': False, 'sand': False, 'rain': True, 'ice': False, 'moss': False},
    'foliage': {'snow': False, 'sand': False, 'rain': True, 'ice': False, 'moss': False},
    'paint': {'snow': False, 'sand': False, 'rain': False, 'ice': False, 'moss': False},
    'water': None,
}
WEATHER['prop_em'] = WEATHER['prop2s'] = WEATHER['fallback'] = WEATHER['prop']
#: A material on any mesh at least this big (scaled, uu) bakes at the large size.
LARGE_MESH_UU = 1200.0
#: Vertex colours (bytes) whose spread is at most this count as one constant.
VC_CONST_TOL = 2
#: Fallback material: Master_Prop, neutral grey, matte.
FALLBACK_BC = (128, 128, 128)
FALLBACK_MRA = (0, 204, 255)
#: Neutral values set on every clone beyond the master defaults (values the rounds approved).
#: SnowOverride 0.1: snow on stone, none on sand (user picked it from a 0.33/0.2/0.1/0 ladder on
#: Front Lawn, round fc3; the master default 1.0 buried pack floors).
#: SnowOverride 0.1: snow on stone, none on sand (user picked it from a 0.33/0.2/0.1/0 ladder on
#: Front Lawn, round fc3; the master default 1.0 buried pack floors).
SNOW_OVERRIDE = 0.1
PROP_VALUES = {'Metalness_Override': 1.0, 'Roughness_Override': 0.0, 'SnowOverride': SNOW_OVERRIDE}
TRAP_VALUES = {'EmissiveFresnelAmount': 0.0, 'BC_Brightness': 1.0, 'SelectGlow': 0.0, 'SpawnHotGlow': 0.0}
PAINT_VALUES = dict([('BC_Brightness', 1.0), ('BC_Desaturation', 0.0)] +
                    [('BumpOffset_%d' % n, 0.0) for n in (1, 2, 3, 4)] +
                    [('TileSize_%s_%d' % (a, n), 1.0) for n in (1, 2, 3, 4) for a in 'UV'] +
                    [('Offset_%s_%d' % (a, n), 0.0) for n in (1, 2, 3, 4) for a in 'UV'])
#: Texture kinds -> donor family and the parameter they bind per route.
TEX_PARAMS = {
    'prop': {'BC': 'BC', 'MRA': 'MRA', 'NM': 'Normal', 'MK2': 'MK2'},
    'prop_em': {'BC': 'BC', 'MRA': 'MRA', 'NM': 'Normal', 'EM': 'EM'},
    'prop2s': {'BC': 'BC', 'MRA': 'MRA', 'NM': 'Normal'},
    'alphamask': {'BC': 'BC', 'MRA': 'MRA', 'NM': 'Normal', 'EM': 'EM'},
    'foliage': {'BC': 'BC', 'NM': 'NM', 'SRK': 'SRK'},
}
#: Proven paks the written pak's structure is compared with (preflight.py REFERENCES + mat2).
PROVEN_PAKS = [os.path.join(REPO, 'thirdparty', 'rounds', 'mat1', '702-MatProbe_Swatches_P.pak'),
               os.path.join(REPO, 'thirdparty', 'rounds', 'sm1', '701-SMProbe_DocksMeshes_P.pak'),
               os.path.join(REPO, 'thirdparty', 'rounds', 'mat2', '704-MatProbe2_P.pak')]
#: Exit codes for the CLI.
EXIT_OK, EXIT_PROBLEMS, EXIT_USAGE, EXIT_CRASH = 0, 1, 2, 3


def code_hash(*mods):
    """Hash of converter source files, so a code change invalidates exactly the caches it feeds."""
    hh = hashlib.sha1()
    for m in mods:
        with open(os.path.join(HERE, m), 'rb') as fh:
            hh.update(fh.read())
    return hh.hexdigest()[:16]


#: Module-level names that only locate code (path set-up), left out of logic_hash.
PATH_NAMES = ('HERE', 'REPO', 'TP', 'SURVEY', 'ENGINE', 'PHYSX', 'PHYSX_LIBS', 'SOURCE', 'BINARY', 'MASTERS_JSON')
#: Functions that find, build or check tools and never shape output (omd3physx's tool set-up,
#: which Rift Lord makes portable), left out of logic_hash.
TOOLING_FUNCS = ('build_tool', '_run', 'tool_status', 'verify', 'shipped_inputs', 'all_static_meshes')


def _logic_only(tree):
    """The module without imports and path set-up, recursively (a copy in another package
    tree, e.g. RiftLordEditor/riftlord/fab, hashes the same when its logic is the same)."""
    import ast

    def keep(n):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            return False
        if isinstance(n, ast.FunctionDef) and n.name in TOOLING_FUNCS:
            return False
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
            return False                    # docstrings
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in PATH_NAMES for t in n.targets):
            return False
        src = ast.unparse(n) if isinstance(n, (ast.Assign, ast.Expr, ast.AugAssign)) else ''
        if 'sys.path' in src:
            return False
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in PATH_NAMES for t in n.targets):
            return False
        return True
    for node in ast.walk(tree):
        for field in ('body', 'orelse', 'finalbody'):
            seq = getattr(node, field, None)
            if isinstance(seq, list):
                kept = [n for n in seq if not isinstance(n, ast.stmt) or keep(n)]
                setattr(node, field, kept or [ast.Pass()])
    return tree


_LOGIC = {}


def logic_hash(*mods):
    """Hash of the converter's logic (code_hash less imports and path set-up): names the library
    build folder, so the same logic gives the same build wherever the code lives."""
    if mods in _LOGIC:
        return _LOGIC[mods]
    import ast
    hh = hashlib.sha1()
    for m in mods:
        with open(os.path.join(HERE, m), encoding='utf-8') as fh:
            hh.update(ast.unparse(_logic_only(ast.parse(fh.read()))).encode())
    _LOGIC[mods] = hh.hexdigest()[:16]
    return _LOGIC[mods]


#: Routes whose material draws both faces: their triangles collide from both sides
#: (bDoubleSidedGeometry) and take grids on both sides (user, Docks showcase 2026-10-15: the
#: single-sheet wall panels took wall traps on one side only).
TWO_SIDED_ROUTES = ('prop2s', 'alphamask')
#: Routes whose cross-frame materials are baked per mesh (permesh.py; user 2026-10-14).
PERMESH_ROUTES = ('prop', 'prop2s', 'prop_em', 'alphamask')
#: Each cache's key includes the source of the code that produces it, the shared repo modules
#: too: a fix in fabmesh.py (UE5 hull doubles) once left 103 cached failures in place.
BAKE_CODE = ('matgraph.py', 'matconvert.py', 'vaulttex.py', 'survey/meshgeo.py',
             'micwrite.py', 'fbxpack.py', 'packgraph.py', 'survey/pkgread.py')
MESH_CODE = ('meshconvert.py', 'analyse.py', 'thumbs.py', 'permesh.py', 'matgraph.py', 'matconvert.py', 'micwrite.py', 'fabmesh.py', '../formats/omd3/omd3smwrite.py',
             '../formats/omd3/omd3sm_format.py', '../formats/omd3/omd3physx.py', 'survey/meshgeo.py', 'fbxpack.py', 'fbxread.py',
             'packgraph.py', 'survey/pkgread.py')
#: The code whose logic names a library build: MESH_CODE less omd3physx, whose tool set-up and
#: pxcook source change per platform (Rift Lord's Windows build) without changing the cooked
#: bytes. Cooking itself is checked against shipped bytes (omd3physx verify), not by the build id.
BUILD_CODE = tuple(m for m in MESH_CODE if not m.endswith('omd3physx.py'))
#: The grid search (analyse.py) is cached on its own, keyed by the geometry it reads: a grid-rule
#: change re-runs only the grid search, not the per-mesh bakes and mesh writes.
ANALYSE_CODE = ('analyse.py', '../formats/omd3/omd3smwrite.py')
MESH_WORK_CODE = tuple(m for m in MESH_CODE if m != 'analyse.py')


def cache_root():
    if os.name == 'nt':
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
        return os.path.join(base, 'rift-lord', 'fabconvert')
    base = os.environ.get('XDG_CACHE_HOME') or os.path.join(os.path.expanduser('~'), '.cache')
    return os.path.join(base, 'rift-lord', 'fabconvert')


def default_library():
    from riftlord.core import gamepath
    return gamepath.user_data('FabLibrary')


def sanitize(s):
    return re.sub(r'[^A-Za-z0-9_]', '_', s)


def fname_safe(leaf):
    """A package / object leaf that no FName parser splits: UE reads a trailing `_<digits>`
    without a leading zero as the FName number, so a lookup by path text (`SM_wall_10`) misses
    the literally stored name. A leading zero keeps it literal: SM_wall_10 -> SM_wall_010,
    _0 -> _00 (Rift Lord request 2026-10-14; bitten three times)."""
    m = re.search(r'_(\d+)$', leaf)
    if m and not (len(m.group(1)) > 1 and m.group(1)[0] == '0'):
        return leaf[:m.start(1)] + '0' + m.group(1)
    return leaf


def h(obj):
    return hashlib.sha1(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def fingerprint(roots):
    """Hash of every file of the pack (relative path, size, mtime)."""
    items = []
    for root in roots:
        for dp, _dn, fns in os.walk(root):
            for f in fns:
                p = os.path.join(dp, f)
                st = os.stat(p)
                items.append((os.path.relpath(p, os.path.dirname(root)), st.st_size, st.st_mtime_ns))
    return h(sorted(items))


def log(msg):
    sys.stderr.write('[fabconvert] %s\n' % msg)
    sys.stderr.flush()


#: Conversion stages in order, as progress events name them (a GUI's progress bar).
STAGES = ('scan', 'materials', 'bake', 'textures', 'meshes', 'pack', 'checks')


def json_events(stage, done, total, msg='', fraction=None, eta_s=None):
    """One progress event as a JSON line on stderr: {stage, index, stages, done, total, msg,
    fraction (0-1 of the whole convert), eta_s (seconds left)}."""
    sys.stderr.write(json.dumps({'stage': stage, 'index': STAGES.index(stage), 'stages': len(STAGES),
                                 'done': done, 'total': total, 'msg': msg,
                                 'fraction': fraction, 'eta_s': eta_s}) + '\n')
    sys.stderr.flush()


#: Seconds each stage takes per static mesh on a pack's first convert (cold caches), the ETA's
#: expectation until the pack has a convert on record (STAGE_RECORD). The median of three cold
#: converts, 2 workers, ISPC encoder (2026-10-28): Stylized Desert (46 meshes, 153 s), Sewer
#: Dungeon (94, 292 s), Stylized Castle (129, 311 s).
STAGE_SECONDS_PER_MESH = {'scan': 0.001, 'materials': 0.431, 'bake': 1.741, 'textures': 0.25, 'meshes': 0.282,
                          'pack': 0.037, 'checks': 0.006}
#: What each stage of a pack's last convert took (seconds), in its cache folder.
STAGE_RECORD = 'stage_seconds.json'


class _Progress:
    """An events callable that adds `fraction` and `eta_s` to each event: each stage is expected
    to take `expected` seconds, scaled by the pace of the convert so far; a stage with finished
    items expects the rest of its items at their own pace. Records what each stage took."""

    def __init__(self, events, expected):
        self.events, self.expected = events, expected
        self.t0 = time.time()
        self.stage, self.began, self.took = None, None, {}

    def __call__(self, stage, done, total, msg=''):
        now = time.time()
        if stage != self.stage:
            if self.stage is not None:
                self.took[self.stage] = round(now - self.began, 2)
            self.stage, self.began = stage, now
        k = STAGES.index(stage)
        exp = self.expected
        part = done / total if total else 0.0
        so_far = sum(exp[s] for s in STAGES[:k]) + exp[stage] * part
        whole = sum(exp.values()) or 1.0
        pace = (now - self.t0) / so_far if so_far > 0 else 1.0
        here = (now - self.began) / done * (total - done) if done else exp[stage] * (1.0 - part) * pace
        eta = here + sum(exp[s] for s in STAGES[k + 1:]) * pace
        if self.events:
            self.events(stage, done, total, msg, fraction=round(min(so_far / whole, 1.0), 4), eta_s=int(round(eta)))

    def finish(self, cache):
        if self.stage is not None:
            self.took[self.stage] = round(time.time() - self.began, 2)
        try:
            with open(os.path.join(cache, STAGE_RECORD), 'w') as fh:
                json.dump({s: self.took.get(s, 0.0) for s in STAGES}, fh, sort_keys=True)
        except OSError:
            pass


def _expected_seconds(cache, meshes, stages=STAGES):
    """{stage: seconds} the next convert of a pack should take: its last convert's, else
    STAGE_SECONDS_PER_MESH for `meshes` static meshes; 0 for stages not in `stages`."""
    try:
        with open(os.path.join(cache, STAGE_RECORD)) as fh:
            got = json.load(fh)
        if set(got) == set(STAGES):
            return {s: max(float(got[s]), 0.01) if s in stages else 0.0 for s in STAGES}
    except (OSError, ValueError, TypeError):
        pass
    return {s: STAGE_SECONDS_PER_MESH[s] * max(meshes, 1) if s in stages else 0.0 for s in STAGES}


def _pmap(pool, fn, items, stage=None, events=None, chunksize=1):
    """pool.map in order, reporting each finished item to `events` when given."""
    items = list(items)
    if events is None:
        return pool.map(fn, items, chunksize=chunksize)
    out = []
    events(stage, 0, len(items))
    for k, r in enumerate(pool.imap(fn, items, chunksize=chunksize), 1):
        out.append(r)
        events(stage, k, len(items))
    return out


def encoder_id():
    """The BC encoder texpatch writes Fab textures with, as texpatch.encoder_id names it;
    'python' when it names none."""
    from riftlord.build import texpatch
    name = getattr(texpatch, 'encoder_id', None)
    return name('texture') if name else 'python'


def _with_encoder(parts):
    """Key parts plus the encoder when it is not the Python one (Python-encoded builds and
    caches keep the keys they had before encoders could differ)."""
    enc = encoder_id()
    return parts if enc == 'python' else parts + [enc]


def tex_cache_dir(cache):
    """Encoded textures, one folder per encoder."""
    enc = encoder_id()
    return os.path.join(cache, 'tex' if enc == 'python' else 'tex-' + sanitize(enc))


def build_id(fp, scale, foliage_master='alphamask'):
    """The library build folder id: changes when the converter code, the pack files, the scale
    or the texture encoder change (catalogue `build`)."""
    return h(_with_encoder([VERSION, logic_hash(*BUILD_CODE), logic_hash(*BAKE_CODE), fp, scale,
                            foliage_master]))[:BUILD_ID_LEN]


def _pload(path):
    try:
        with open(path, 'rb') as fh:
            return pickle.load(fh)
    except (OSError, EOFError, pickle.UnpicklingError):
        return None


def _psave(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = '%s.%d.tmp' % (path, os.getpid())
    with open(tmp, 'wb') as fh:
        pickle.dump(obj, fh, protocol=4)
    os.replace(tmp, path)


# -- worker state ---------------------------------------------------------------------------------------

_W = {}

#: Decoded source textures one worker keeps (MB): 8-bit sources (16 MB at 2048 px) and their
#: float32 copies (64 MB). Unbounded, every worker grew to ~2 GB and swapped the machine (City of
#: Brass, 2026-10-15); one 384 MB float cache re-decoded Winter City's many-texture materials
#: for every slot (four hours). Sources are also kept decoded on disk (cache_root/<pack>/src).
TEX_CACHE_MB = 768
TEX_FLOAT_CACHE_MB = 512
#: Memory one worker is allowed for when sizing the pool, and the least each worker's guard allows
#: (MB): the largest resident size measured, rounded up to 256 MB (Orasot, Desert Castle and City of
#: Brass with cold caches, 2026-10-25: 4836-5587 MB; 2048 stopped all three).
WORKER_MB = 5632
#: Memory left to the rest of the machine when sizing the pool (MB).
RESERVE_MB = 4096


class _LruBytes(dict):
    """A dict of numpy arrays (or None) that drops the least recently used entries past a byte budget."""

    def __init__(self, budget):
        super().__init__()
        self.budget = budget
        self.used = 0

    def __getitem__(self, k):
        v = super().pop(k)
        super().__setitem__(k, v)                       # most recent last
        return v

    def __setitem__(self, k, v):
        if k in self:
            self.used -= _nbytes(super().pop(k))
        super().__setitem__(k, v)
        self.used += _nbytes(v)
        while self.used > self.budget and len(self) > 1:
            old = next(iter(self))
            self.used -= _nbytes(super().pop(old))


def _nbytes(v):
    return 0 if v is None else int(getattr(v, 'nbytes', 0))


#: Outputs are written beside the library under this suffix and swapped in at the end.
NEW_SUFFIX = '.new'
#: Share of the CPUs a build takes by default: the rest stay free for the desktop, the game and
#: other sessions' builds (user via win64-f2 2026-10-15: 8 workers loaded the PC; cap 3-4).
CPU_SHARE = 0.25


#: Before each bake or mesh task a worker waits while free memory (MemAvailable) is under
#: RESERVE_MB, checking every MEM_WAIT_STEP seconds for at most MEM_WAIT_MAX, so the pool backs
#: off as other jobs grow instead of pushing the machine into swap (Rift Lord, 2026-10-24).
MEM_WAIT_STEP = 3.0
MEM_WAIT_MAX = 900.0


def _available_mb():
    try:
        with open('/proc/meminfo') as fh:
            return next(int(l.split()[1]) // 1024 for l in fh if l.startswith('MemAvailable:'))
    except (OSError, StopIteration, ValueError):
        return None


def _wait_for_memory():
    waited = 0.0
    while waited < MEM_WAIT_MAX:
        avail = _available_mb()
        if avail is None or avail >= RESERVE_MB:
            return
        time.sleep(MEM_WAIT_STEP)
        waited += MEM_WAIT_STEP


def memory_budget_mb(memory_mb=None):
    """Memory a convert may use (MB): the free memory less RESERVE_MB, and no more than
    `memory_mb` (the user's setting) when given; None when neither is known."""
    avail = _available_mb()
    budget = None if avail is None else avail - RESERVE_MB
    if memory_mb:
        budget = memory_mb if budget is None else min(budget, memory_mb)
    return budget


def default_jobs(memory_mb=None):
    """Workers: CPU_SHARE of the CPUs, and no more than the memory budget holds at WORKER_MB each."""
    cpus = max(1, int((os.cpu_count() or 2) * CPU_SHARE))
    budget = memory_budget_mb(memory_mb)
    if budget is None:
        return cpus
    return max(1, min(cpus, budget // WORKER_MB))


def worker_limit_mb(jobs, memory_mb=None):
    """Each worker's share of the memory budget, at least WORKER_MB (the measured need of the
    heaviest job); None when the budget is unknown."""
    budget = memory_budget_mb(memory_mb)
    if budget is None:
        return None
    return max(WORKER_MB, budget // max(1, jobs))


#: How often the parent checks its workers (s).
GUARD_STEP = 1.0


def _rss_mb(pid):
    try:
        with open('/proc/%d/status' % pid) as fh:
            return next(int(l.split()[1]) // 1024 for l in fh if l.startswith('VmRSS:'))
    except (OSError, StopIteration, ValueError):
        return 0


class _Guard:
    """Watches a pool's workers from the parent (Rift Lord 2026-10-24: one City of Brass worker
    reached 19.5 GB; an address-space limit then starved thread creation, and the lost task
    left the pool waiting for ever). A worker resident over `limit_mb`, or one that vanished,
    raises MemoryError / RuntimeError in the main thread, which ends the convert with an error.
    POSIX only (SIGUSR1; resident size from /proc)."""

    def __init__(self, pool, limit_mb):
        import signal
        import threading
        self.pool, self.limit_mb, self.why = pool, limit_mb, None
        self.pids = {p.pid for p in pool._pool}
        self.stop = threading.Event()
        self.old = signal.signal(signal.SIGUSR1, self._raise)
        threading.Thread(target=self._watch, daemon=True).start()

    def _raise(self, _sig, _frame):
        raise (MemoryError if 'over' in self.why else RuntimeError)(self.why)

    def _watch(self):
        import signal
        while not self.stop.wait(GUARD_STEP):
            now = {p.pid for p in self.pool._pool}
            if self.stop.is_set():
                return
            if self.pids - now:
                self.why = 'a converter worker died: its task is lost'
            elif self.limit_mb:
                big = max((_rss_mb(pid) for pid in now), default=0)
                if big > self.limit_mb:
                    self.why = 'a converter worker went over %d MB (%d MB resident)' % (self.limit_mb, big)
            if self.why:
                os.kill(os.getpid(), signal.SIGUSR1)
                return

    def close(self):
        import signal
        self.stop.set()
        signal.signal(signal.SIGUSR1, self.old)


def _init(root, cache):
    # a forked worker must not share the parent's open pak readers (one file offset, many
    # readers: corrupt reads, seen as LZ4 errors when a second convert ran in one process)
    from riftlord.core import gamefiles
    gamefiles._open.clear()
    from riftlord.fab import fbxpack
    fbxpack.configure([root])
    _W['ctx'] = MC.Context(root)
    _W['ctx'].tex.cache = _LruBytes(TEX_CACHE_MB << 20)
    _W['ctx'].tex.fcache = _LruBytes(TEX_FLOAT_CACHE_MB << 20)
    _W['ctx'].tex.disk = os.path.join(cache, 'src')
    _W['cache'] = cache


def _ctx():
    return _W['ctx']


# -- step 1: meshes -------------------------------------------------------------------------------------

def list_meshes(roots):
    from riftlord.fab.survey import pkgread
    out = []
    for root in roots:
        for dp, _dn, fns in os.walk(root):
            for f in sorted(fns):
                if not f.endswith('.uasset'):
                    continue
                p = os.path.join(dp, f)
                try:
                    pk = pkgread.Package(p)
                except Exception:                        # noqa: BLE001 - not a package we read
                    continue
                if any(pk.class_of(e) == 'StaticMesh' and e['outer'] == 0 for e in pk.exports):
                    out.append(p)
    from riftlord.fab import fbxpack
    return sorted(out) + fbxpack.list_meshes(roots)


def source_rel(m, content):
    """A mesh's catalogue `source`: its package path under Content without .uasset, or for an
    FBX object '<fbx path without .fbx>/<object>'."""
    from riftlord.fab import fbxpack
    if fbxpack.is_mesh_path(m):
        return fbxpack.source_rel(m, content)
    return os.path.relpath(m, content)[:-len('.uasset')]


def job_mesh_info(args):
    path, scale = args
    try:
        src = MS.read(path)
    except Exception as e:                               # noqa: BLE001
        return path, {'error': '%s: %s' % (type(e).__name__, e)}
    md = src['md']
    ti = md['tri_inst']
    P = md['positions'][md['inst_vertex'][ti]].reshape(-1, 3) * np.array(src['settings'].get('BuildScale3D') or (1, 1, 1))
    ext = (P.max(0) - P.min(0)) * scale
    slots = []
    for k, (slot, mat) in enumerate(src['slots']):
        sel = md['tri_group'] == k
        info = {'slot': slot, 'material': (mat or '').split('.')[0] or None, 'triangles': int(sel.sum())}
        if md['color'] is not None and sel.any():
            seen = np.rint(MS.seen_colours(md['color'][ti[sel]].reshape(-1, 4)) * 255).astype(int)
            info['vc_min'] = seen.min(0).tolist()
            info['vc_max'] = seen.max(0).tolist()
            info['vc_median'] = np.median(seen, 0).round().astype(int).tolist()
        else:
            info['vc_min'] = info['vc_max'] = info['vc_median'] = [255, 255, 255, 255]
        slots.append(info)
    return path, {'slots': slots, 'extent': ext.tolist(), 'triangles': int(len(ti)),
                  'uv_channels': len(md['uvs'])}


# -- step 2: material analysis ------------------------------------------------------------------------------

def _public(plan):
    return {k: v for k, v in plan.items() if not k.startswith('_')}


def job_analyse(gpath):
    p = MC.analyse(_ctx(), gpath)
    return gpath, _public(p)


# -- step 3: bake a variant -------------------------------------------------------------------------------

def job_bake(var):
    """var: {'name', 'material', 'vc', 'large', 'paint', 'key'} -> plan + image hashes."""
    _wait_for_memory()
    cache = _W['cache']
    cpath = os.path.join(cache, 'bake', var['key'][:2], var['key'] + '.pkl')
    got = _pload(cpath)
    if got is not None:
        return var['name'], got
    ctx = _ctx()
    t0 = time.time()
    out = {'name': var['name']}
    if var.get('paint'):
        pj = var['paint']
        plans, imgs = [], []
        for val in (pj['lo'], pj['hi']):
            vc = np.asarray(var['vc'], np.float32).copy()
            vc[pj['channel']] = val / 255.0
            p = MC.analyse(ctx, var['material'], vc=vc, large=var['large'], foliage_master=var.get('foliage_master', 'alphamask'))
            if p['route'] in ('fallback', 'drop', 'water'):
                break
            plans.append(p)
            imgs.append(MC.images(p, MC.bake(ctx, p, vc=vc)))
        if len(plans) == 2:
            plan = plans[0]
            plan['route'] = 'paint'
            plan['reasons'] = sorted(set(plans[0]['reasons']) | set(plans[1]['reasons']) |
                                     {'mesh paint blend: linear in vertex colour %s between %d and %d (the pack '
                                      'blends with its curve)' % ('RGBA'[pj['channel']], pj['lo'], pj['hi']),
                                      'Master_Enviro_MeshPaint_Shell reads no weather (no snow, sand, rain)'})
            if plans[0].get('emissive_scale') or plans[1].get('emissive_scale'):
                plan['reasons'].append('emissive dropped on the mesh-paint master')
            plan['grade'] = MC.grade_of(plan['reasons'])
            layers = [{'BC': i['BC'], 'MRH': _mrh(i['MRA']), 'NM': i['NM']} for i in imgs]
            out['plan'] = _public(plan)
            out['images'] = _store_images(cache, {'%s%d' % (k, n + 1): v for n, L in enumerate(layers)
                                                  for k, v in L.items()})
            out['preview'] = _preview(imgs[1]['BC'])
            out['seconds'] = round(time.time() - t0, 2)
            _psave(cpath, out)
            return var['name'], out
        var = dict(var, paint=None)
        out['paint_failed'] = True
    vc = None if var['vc'] is None else np.asarray(var['vc'], np.float32)
    plan = MC.analyse(ctx, var['material'], vc=vc, large=var['large'], foliage_master=var.get('foliage_master', 'alphamask'))
    if var.get('vc_note'):
        plan['reasons'] = sorted(set(plan['reasons']) | {var['vc_note']})
        plan['grade'] = MC.grade_of(plan['reasons'], plan['grade'] if plan['grade'] in MC.GRADES else 'exact')
    if plan['route'] in ('fallback', 'drop', 'water'):
        out['plan'] = _public(plan)
        out['images'] = {}
        _psave(cpath, out)
        return var['name'], out
    try:
        im = MC.images(plan, MC.bake(ctx, plan, vc=vc))
    except (MC.Unsupported, ValueError, IndexError, KeyError, FloatingPointError) as e:
        plan.update(route='fallback', grade='fallback', reasons=plan['reasons'] + ['bake failed: %s' % e])
        out['plan'] = _public(plan)
        out['images'] = {}
        _psave(cpath, out)
        return var['name'], out
    if plan['route'] == 'prop2s' and plan.get('emissive_scale'):
        plan['route'] = 'prop_em'
        plan['two_sided_mesh'] = True
        plan['reasons'] = sorted(set(plan['reasons']) | {'two-sided by duplicated faces (emissive needs the EM donor)'})
        plan['grade'] = MC.grade_of(plan['reasons'])
    elif plan['route'] == 'prop' and plan.get('emissive_scale'):
        plan['route'] = 'prop_em'
    keep = {'BC': im['BC'], 'MRA': im['MRA'], 'NM': im['NM']}
    if 'EM' in im:
        keep['EM'] = im['EM']
    if 'SRK' in im:
        keep['SRK'] = im['SRK']
    out['plan'] = _public(plan)
    out['images'] = _store_images(cache, keep)
    out['preview'] = _preview(im['BC'])
    out['seconds'] = round(time.time() - t0, 2)
    _psave(cpath, out)
    return var['name'], out


def _mrh(mra):
    out = mra.copy()
    out[..., 2] = 0
    return out


def _preview(bc):
    im = Image.fromarray(bc, 'RGBA')
    if im.width > 256:
        im = im.resize((256, 256), Image.BOX)
    return np.asarray(im)


def _store_images(cache, imgs):
    """Save each image by content hash; return {kind: {'hash', 'size', 'constant'}}."""
    out = {}
    for k, a in sorted(imgs.items()):
        a = np.ascontiguousarray(a)
        hh = hashlib.sha1(a.tobytes() + repr(a.shape).encode()).hexdigest()
        p = os.path.join(cache, 'img', hh[:2], hh + '.npy')
        if not os.path.exists(p):
            os.makedirs(os.path.dirname(p), exist_ok=True)
            tmp = '%s.%d.tmp.npy' % (p[:-4], os.getpid())
            np.save(tmp, a)
            os.replace(tmp, p)
        out[k] = {'hash': hh, 'size': int(a.shape[0]), 'constant': MC.is_constant(a)}
    return out


def _write_preview(previews_dir, tex_path, img):
    """previews/<texture leaf>.png at PREVIEW_PX (Map Editor previews; Rift Lord request)."""
    im = Image.fromarray(np.ascontiguousarray(img), 'RGBA')
    if im.width > PREVIEW_PX:
        im = im.resize((PREVIEW_PX, PREVIEW_PX), Image.BOX)
    name = tex_path.rsplit('/', 1)[-1] + '.png'
    im.save(os.path.join(previews_dir, name), optimize=False)
    return 'previews/' + name


def _load_image(cache, hh):
    return np.load(os.path.join(cache, 'img', hh[:2], hh + '.npy'))


# -- step 4: textures ------------------------------------------------------------------------------------

def tex_kind(param_kind, route, size, constant):
    """(donor family, donor size) for a texture."""
    if param_kind in ('NM',):
        fam = 'nm'
    elif param_kind in ('MRA', 'MRH', 'SRK'):
        fam = 'mra'
    elif param_kind == 'BC' and route == 'alphamask':
        fam = 'bca'
    else:
        fam = 'bc'
    if constant:
        size = min(MW.sizes(fam))
    avail = MW.sizes(fam)
    below = [s for s in avail if s <= size]
    return fam, (max(below) if below else min(avail))


def job_texture(args):
    path, fam, size, hh, stage = args
    cache = _W['cache']
    img = Image.fromarray(_load_image(cache, hh), 'RGBA')
    blobs = MW.write_texture(fam, size, img, path, tex_cache_dir(cache))
    probs = MW.texture_check(blobs, fam, size)
    files = _stage_package(stage, path, blobs)
    return path, {'files': files, 'problems': probs, 'bytes': sum(len(b) for b in blobs.values())}


def _stage_package(stage, path, blobs):
    out = {}
    for ext, data in blobs.items():
        entry = 'OMD/Content' + path[len('/Game'):] + ext
        f = os.path.join(stage, entry)
        os.makedirs(os.path.dirname(f), exist_ok=True)
        with open(f, 'wb') as fh:
            fh.write(data)
        out[entry] = f
    return out


# -- step 6: meshes --------------------------------------------------------------------------------------

def job_mesh(args):
    job = args
    _wait_for_memory()
    cache = _W['cache']
    cpath = os.path.join(cache, 'mesh', job['key'][:2], job['key'] + '.pkl')
    got = _pload(cpath)
    if got is None or (got.get('status') == 'converted' and 'analysis_in' not in got):
        got = _mesh_work(job)
        _psave(cpath, got)
    if got.get('status') == 'converted':
        got = dict(got)
        try:
            got['analysis'] = _analysis_cached(cache, got['analysis_in'], job['role'], job['scale'])
        except Exception as e:                           # noqa: BLE001 - reported per mesh, never fatal
            import traceback
            got = {'status': 'failed', 'error': '%s: %s' % (type(e).__name__, e), 'trace': traceback.format_exc(limit=6)}
    files = {}
    if got.get('blobs'):
        files = _stage_package(job['stage'], job['package'], got['blobs'])
    nbytes = sum(len(b) for b in (got.get('blobs') or {}).values())
    for path, blobs in (got.get('extra') or {}).items():
        files.update(_stage_package(job['stage'], path, blobs))
        nbytes += sum(len(b) for b in blobs.values())
    res = {k: v for k, v in got.items() if k not in ('blobs', 'extra', 'analysis_in')}
    res['files'] = files
    res['bytes'] = nbytes
    return job['rel'], res


def _analysis_cached(cache, ain, role, scale):
    """AN.analyse of a written mesh's geometry (positions, triangles, collision, two-sided flags),
    cached by that content and the grid-search code."""
    key = h([VERSION, code_hash(*ANALYSE_CODE), hashlib.sha1(pickle.dumps(ain, protocol=4)).hexdigest(), role, scale])
    cpath = os.path.join(cache, 'grid', key[:2], key + '.pkl')
    got = _pload(cpath)
    if got is None:
        got = AN.analyse({'positions': ain['positions'], 'triangles': ain['triangles']}, role, ain['coll'], scale,
                         two_sided=ain['two'])
        _psave(cpath, got)
    return got


#: Per worker: material analyses for per-mesh bakes, by (material, vertex colour, large, foliage
#: master). One Winter City material took 10-15 s and was analysed again for every mesh using it.
_ANALYSES = {}


def _analyse_cached(ctx, material, vc, large, foliage_master):
    """MC.analyse, memoised; returns a shallow copy (callers set top-level keys)."""
    key = (material, None if vc is None else np.asarray(vc, np.float32).tobytes(), bool(large), foliage_master)
    if key not in _ANALYSES:
        _ANALYSES[key] = MC.analyse(ctx, material, vc=vc, large=large, foliage_master=foliage_master)
    return dict(_ANALYSES[key])


def _permesh(job, src):
    """Bake this mesh's flagged slots (permesh.py) into one shared texture set, laid out in a
    clean UV channel or a generated layout over all of them, and write each slot's MIC on it.
    Rewrites the slot jobs in place (bake_uv_data, or the variant frame for a slot that failed).
    Returns ({package path: blobs}, {slot: info}, problems, {out slot: BC preview})."""
    extra, info, problems, previews = {}, {}, [], {}
    tex_prev = {}
    info['_tex_previews'] = tex_prev
    flagged = [(k, sj) for k, sj in enumerate(job['slot_jobs']) if sj.get('permesh')]
    if not flagged:
        return extra, info, problems, previews
    from riftlord.core import pkgcheck
    ctx = _ctx()
    c = MS.corners(src)
    lmi = src.get('lightmap_index')
    texmaps, plans, failed = {}, {}, {}
    for k, sj in flagged:
        pm = sj['permesh']
        if not (c['slot'] == k).any():
            failed[k] = 'empty slot'
            continue
        try:
            vc = None if pm['vc'] is None else np.asarray(pm['vc'], np.float32)
            plans[k] = _analyse_cached(ctx, pm['material'], vc, pm['large'], pm['foliage_master'])
        except Exception as e:                           # noqa: BLE001 - fall back to the shared bake
            failed[k] = 'per-mesh bake failed (%s: %s): kept the shared material bake' % (type(e).__name__, e)
    if plans:
        try:
            union = np.isin(c['slot'], list(plans))
            uv_all, ch, size = PM.layout(c, union, lmi, job['scale'])
            # only slots whose bake looks different from the shared bake (user 2026-10-15)
            scores = {}
            for k in list(plans):
                scores[k], _n = PM.difference(ctx, plans[k], src, k, uv_all[c['slot'][union] == k])
                if not PM.visible(scores[k]):
                    failed[k] = ('no visible difference from the shared bake (broad %.1f, fine %.1f): shared bake kept'
                                 % (scores[k]['broad'], scores[k]['fine']))
                    del plans[k]
            if plans and len(plans) < len(scores):
                union = np.isin(c['slot'], list(plans))
                uv_all, ch, size = PM.layout(c, union, lmi, job['scale'])
        except Exception as e:                           # noqa: BLE001 - fall back to the shared bake
            for k in plans:
                failed[k] = 'per-mesh bake failed (%s: %s): kept the shared material bake' % (type(e).__name__, e)
            plans = {}
    if plans:
        try:
            in_union = c['slot'][union]
            comp, cover = {}, np.zeros((size, size), bool)
            alpha = any(job['slot_jobs'][k]['permesh']['route'] == 'alphamask' for k in plans)
            kinds = {}
            for k, plan in plans.items():
                uvb = uv_all[in_union == k]
                grids, filled = PM.bake(ctx, plan, src, k, uvb, size)
                plan['size'] = size
                plan['frame'] = None
                plan['route'] = job['slot_jobs'][k]['permesh']['route']
                with np.errstate(all='ignore'):
                    im = MC.images(plan, grids)
                kinds[k] = [kind for kind in ('BC', 'MRA', 'NM', 'EM') if kind in im]
                for kind in kinds[k]:
                    dst = comp.setdefault(kind, np.zeros((size, size, 4), np.uint8))
                    dst[filled] = im[kind][filled]
                cover |= filled
                info[k] = {'layout': 'uv%d' % ch if ch is not None else 'generated', 'size': size}
            base = job['slot_jobs'][flagged[0][0]]['permesh']['tex']
            path0 = '%s/Textures/T_%s__Baked' % (base.split('/Textures/')[0], fname_safe(job['name']))
            paths = {}
            for kind, img in comp.items():
                img = PM.pad_image(img, cover)
                fam, tsize = tex_kind(kind, 'alphamask' if alpha else 'prop', size, MC.is_constant(img))
                path = '%s_%s' % (path0, kind)
                blobs = MW.write_texture(fam, tsize, Image.fromarray(img, 'RGBA'), path,
                                         tex_cache_dir(_W['cache']))
                problems += ['%s: %s' % (path, x) for x in MW.texture_check(blobs, fam, tsize)]
                extra[path] = blobs
                paths[kind] = path
                tex_prev[path] = _preview(img)
                if kind == 'BC':
                    bc_prev = _preview(img)
            for k in plans:
                sj = job['slot_jobs'][k]
                texmaps[k] = {kind: paths[kind] for kind in kinds[k]}
                previews[sj['out_slot']] = bc_prev
                sj['bake_uv_data'] = uv_all[in_union == k]
                sj['frame'] = None
                info[k]['cover'] = round(float(cover.mean()), 3)
        except Exception as e:                           # noqa: BLE001 - fall back to the shared bake
            for k in plans:
                failed[k] = 'per-mesh bake failed (%s: %s): kept the shared material bake' % (type(e).__name__, e)
                info.pop(k, None)
            texmaps = {}
    for k, sj in flagged:
        pm = sj.pop('permesh')
        texmap = texmaps.get(k)
        if texmap is None:
            texmap = pm['fallback_tex']
            sj['frame'] = pm['frame']
            sj.pop('bake_uv_data', None)
            info[k] = {'skipped': failed.get(k, 'not baked')}
        elif pm['route'] == 'prop_em' and 'EM' not in texmap:
            texmap['EM'] = SHIPPED_BLACK
        plan_s = {'emissive_scale': 1.0 if 'EM' in texmap and texmap['EM'] != SHIPPED_BLACK else 0.0}
        spec = mic_spec(pm['route'], plan_s, texmap)
        try:
            blobs, expect = MW.write_mic(spec, pm['mic'])
        except SystemExit as e:
            problems.append('%s: MIC not written: %s' % (pm['mic'], e))
            continue
        own = pm['mic'].split('/Materials/')[0]
        allowed = tuple(v for v in texmap.values() if not v.startswith(own))
        problems += MW.readback_problems(blobs, pm['mic'], expect, own, allowed)
        problems += ['%s: %s' % (pm['mic'], x) for x in pkgcheck.problems(blobs['.uasset'], blobs['.uexp'], pm['mic'])]
        extra[pm['mic']] = blobs
    return extra, info, problems, previews


def _mesh_work(job):
    from riftlord.formats.omd3 import omd3smwrite as W
    t0 = time.time()
    try:
        src = MS.read(job['source'])
        extra, pm_info, pm_problems, pm_previews = _permesh(job, src)
        desc, stats = MS.build(src, job['scale'], job['slot_jobs'])
        if desc is None:
            return {'status': 'dropped', 'stats': stats, 'reason': 'every section is a dropped effect or decal'}
        two_out = sorted({j['out_slot'] for j in job['slot_jobs'] if not j.get('drop') and j.get('two_sided_material')})
        two_tris = np.isin(desc['materials'], two_out)
        coll, csum = MS.collision(src, job['scale'], desc, job['role'], double_sided=bool(two_tris.any()), name=job['name'])
        nslots = max(j['out_slot'] for j in job['slot_jobs'] if not j.get('drop')) + 1
        mats = []
        for k in range(nslots):
            m = job['out_materials'][k]
            mats.append({'path': m['path'], 'object': m['path'].rsplit('/', 1)[-1], 'slot': sanitize(m['slot'] or 'Slot%d' % k),
                         'class': 'MaterialInstanceConstant'})
        lmi = src.get('lightmap_index')
        nuv = len(desc['uvs'])
        lmi = lmi if (isinstance(lmi, int) and 0 < lmi < nuv) else 1
        blobs, rep = W.write(W.Template(), desc, job['package'], materials=mats, collision=coll, lightmap_index=lmi)
        problems = [p for p in W.strict_check(blobs, job['name']) + W.pkgcheck_problems(blobs, job['name'])]
        ana_in = {'positions': desc['positions'], 'triangles': desc['triangles'], 'coll': coll, 'two': two_tris}
        slot_bc = {}
        for k in range(nslots):
            hh = job['out_materials'][k].get('preview')
            slot_bc[k] = pm_previews[k] if k in pm_previews else (_load_image(job['cache'], hh) if hh else None)
        thumb = TH.render(desc, slot_bc)
        buf = io.BytesIO()
        thumb.save(buf, 'PNG', optimize=False)
        problems += pm_problems
        return {'status': 'converted', 'blobs': blobs, 'extra': extra, 'permesh': pm_info,
                'stats': stats, 'collision': csum, 'analysis_in': ana_in,
                'write': {'vertices': rep['vertices'], 'triangles': rep['triangles'], 'sections': rep['sections'],
                          'bounds': [round(float(x), 3) for x in rep['bounds']], 'index32': rep['index32'],
                          'uv_channels': rep['uv_channels']},
                'problems': problems, 'thumb': buf.getvalue(), 'seconds': round(time.time() - t0, 2)}
    except Exception as e:                               # noqa: BLE001 - reported per mesh, never fatal
        import traceback
        return {'status': 'failed', 'error': '%s: %s' % (type(e).__name__, e),
                'trace': traceback.format_exc(limit=6)}
    except SystemExit as e:                              # the writer refuses by SystemExit
        return {'status': 'failed', 'error': 'refused: %s' % e}


# -- verification -----------------------------------------------------------------------------------------

def imports_problems(stage_files):
    """Every import of our packages resolves: to an export of that name and class in one of
    our packages, or to a package the game ships (gamefiles index)."""
    from riftlord.core import gamefiles
    from riftlord.formats.ue.uassetio import Package
    idx = gamefiles.index()
    ours = {}
    for entry, f in stage_files.items():
        if entry.endswith('.uasset'):
            stem = entry[:-len('.uasset')]
            gp = '/Game' + stem[len('OMD/Content'):]
            ux = stage_files.get(stem + '.uexp')
            ours[gp] = (f, ux)
    exports_of = {}

    def exports(gp):
        if gp not in exports_of:
            ua = open(ours[gp][0], 'rb').read()
            pkg = Package(ua)
            nm = [n for n, _h in pkg.names]
            ex = {}
            for e in pkg.exports:
                ci = struct.unpack_from('<i', e['meta'], 0)[0]
                cname = nm[struct.unpack('<7i', pkg.imports[-ci - 1])[5]] if ci < 0 else None
                ex[nm[struct.unpack_from('<i', e['meta'], 16)[0]]] = cname
            exports_of[gp] = ex
        return exports_of[gp]
    probs = []
    for gp, (ua_f, _ux) in sorted(ours.items()):
        pkg = Package(open(ua_f, 'rb').read())
        nm = [n for n, _h in pkg.names]
        for r in pkg.imports:
            _cp, _n1, cls, _n2, outer, obj, _n3 = struct.unpack('<7i', r)
            if outer >= 0:
                if nm[cls] == 'Package' and nm[obj].startswith('/Game/') and nm[obj] not in ours:
                    rel = '../../../OMD/Content' + nm[obj][len('/Game'):]
                    if rel + '.uasset' not in idx:
                        probs.append('%s imports %s, which neither the game nor the library has' % (gp, nm[obj]))
                continue
            o = struct.unpack('<7i', pkg.imports[-outer - 1])
            target = nm[o[5]]
            if nm[o[2]] != 'Package' or target not in ours:
                continue
            ex = exports(target)
            if nm[obj] not in ex:
                probs.append('%s imports %s.%s: no such export' % (gp, target, nm[obj]))
            elif ex[nm[obj]] != nm[cls]:
                probs.append('%s imports %s.%s as %s; it is a %s' % (gp, target, nm[obj], nm[cls], ex[nm[obj]]))
    return probs


def preflight(pak):
    from riftlord.fab import preflight as PF
    saved = list(PF.REFERENCES)
    PF.REFERENCES[:] = [p for p in PROVEN_PAKS if os.path.exists(p)]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = PF.main([pak])
    finally:
        PF.REFERENCES[:] = saved
    return rc, buf.getvalue().splitlines()


# -- the conversion -------------------------------------------------------------------------------------

def humanize(name):
    n = re.sub(r'^(SM|S|M|MI)_', '', name)
    n = re.sub(r'[_\-]+', ' ', n)
    n = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', n)
    return ' '.join(w if w.isupper() else w.capitalize() for w in n.split())


def convert(pack_dir, out_dir=None, jobs=None, scale=None, foliage_master='alphamask', only=None, progress=log,
            pack_id=None, materials=None, events=None, memory_mb=None):
    """Convert a pack; returns the summary dict (also written to report.json).

    materials: {source material path: shipped OMD3 material path}; slots using a listed source
    material keep their triangles and draw with the shipped material (e.g. translucent glass).
    events: callable(stage, done, total, msg='', fraction=, eta_s=) for a progress bar
    (json_events), or None."""
    materials = materials or {}
    t_start = time.time()
    item, content, roots = PK.locate(pack_dir)
    ident = PK.identity(item, roots)
    if scale is not None:
        ident['scale'] = float(scale)
    if pack_id:
        ident['id'] = sanitize(pack_id)
    if ident['scale'] is None:
        raise ValueError('%s is deferred (no scale decided); pass --scale to convert it anyway' % ident['title'])
    pid = ident['id']
    out_dir = out_dir or default_library()
    lib = os.path.join(out_dir, pid)
    cache = os.path.join(cache_root(), pid)
    stage = os.path.join(cache, 'stage')
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage, exist_ok=True)
    os.makedirs(lib, exist_ok=True)
    fp = fingerprint(roots)
    jobs = jobs or default_jobs(memory_mb)
    # one folder per library build: missions built from different builds never ship the same
    # path with different bytes (Rift Lord request); output is deterministic, so the id moves
    # only when the bytes can
    build = build_id(fp, ident['scale'], foliage_master)
    own = '%s/%s/B%s' % (MOUNT_ROOT, pid, build)
    report = {'pack': ident, 'converter': VERSION, 'fingerprint': fp, 'problems': [], 'warnings': []}
    if not ident['known']:
        report['warnings'].append('pack not in the registry: scale %.2f assumed' % ident['scale'])
    progress('%s (%s): scale %.2f, %d root(s), library %s' % (ident['title'], pid, ident['scale'], len(roots), lib))

    # the collision cooker, compiled (or found) once here and not by the workers at once: two
    # workers compiling into one path ran into "Text file busy" (2026-10-23)
    from riftlord.formats.omd3 import omd3physx
    omd3physx.build_tool()
    from riftlord.fab import fbxpack
    fbxpack.configure(roots)
    meshes = list_meshes(roots)
    if only:
        meshes = [m for m in meshes if any(o in m for o in only)]
    progress('%d static meshes' % len(meshes))
    events = _Progress(events, _expected_seconds(cache, len(meshes)))
    ctx_root = roots[0]
    limit_mb = worker_limit_mb(jobs, memory_mb)
    report['settings'] = {'workers': jobs, 'memory_mb': memory_mb, 'worker_limit_mb': limit_mb}
    if limit_mb:
        progress('each worker capped at %d MB resident' % limit_mb)
    guard = None
    with multiprocessing.get_context('spawn' if os.name == 'nt' else 'fork').Pool(
            jobs, initializer=_init, initargs=(ctx_root, cache)) as pool:
        if os.name != 'nt':
            guard = _Guard(pool, limit_mb)
        info = dict(_pmap(pool, job_mesh_info, [(m, ident['scale']) for m in meshes], 'scan', events, 4))
        mats = sorted({s['material'] for v in info.values() if 'slots' in v for s in v['slots'] if s['material']})
        progress('%d materials referenced; analysing' % len(mats))
        plan0 = dict(_pmap(pool, job_analyse, mats, 'materials', events, 4))
        # -- variants
        large = {}
        for m, v in info.items():
            if 'slots' not in v:
                continue
            big = max(v['extent']) >= LARGE_MESH_UU
            for s in v['slots']:
                if s['material']:
                    large[s['material']] = large.get(s['material'], False) or big
        variants = {}
        slot_variant = {}
        for m, v in sorted(info.items()):
            for k, s in enumerate(v.get('slots', [])):
                mat = s['material']
                if mat in materials:
                    slot_variant[(m, k)] = ('static', 'shipped', mat)
                    continue
                p0 = plan0.get(mat) if mat else None
                if p0 is None or p0['route'] in ('fallback', 'drop', 'water'):
                    slot_variant[(m, k)] = ('static', p0['route'] if p0 else 'fallback', mat)
                    continue
                read = [('rgba'.index(c)) for c in p0.get('vc_read') or []]
                leaf = sanitize(mat.rsplit('/', 1)[-1])
                var = {'material': mat, 'vc': None, 'large': large.get(mat, False), 'paint': None,
                       'foliage_master': foliage_master}
                name = leaf if leaf.startswith('MI_') else 'MI_%s' % leaf
                if read:
                    lo = [s['vc_min'][c] for c in read]
                    hi = [s['vc_max'][c] for c in read]
                    med = [s['vc_median'][c] for c in read]
                    vc = [x / 255.0 for x in s['vc_median']]
                    spread = max(b - a for a, b in zip(lo, hi))
                    if spread <= VC_CONST_TOL:
                        var['vc'] = vc
                        if any(x != 255 for x in med):
                            name += '_vc' + ''.join('%02x' % x for x in med)
                    elif len(read) == 1 and p0['route'] == 'prop':
                        var['vc'] = vc
                        var['paint'] = {'channel': read[0], 'lo': lo[0], 'hi': hi[0]}
                        name += '_paint%s%02x%02x' % ('RGBA'[read[0]], lo[0], hi[0])
                    else:
                        var['vc'] = vc
                        var['vc_note'] = ('vertex colour %s varies on this mesh (%s..%s): evaluated at the mean'
                                          % (''.join('RGBA'[c] for c in read), lo, hi))
                        name += '_vcmean' + ''.join('%02x' % x for x in med)
                var['name'] = name
                if name in variants and variants[name]['material'] != mat:
                    name = name + '_' + h(mat)[:6]
                    var['name'] = name
                variants.setdefault(name, var)
                slot_variant[(m, k)] = ('variant', name, mat)
        for name, var in variants.items():
            var['key'] = h([VERSION, code_hash(*BAKE_CODE), fp, var['material'], var['vc'], var['large'], var['paint'], var.get('vc_note'),
                            foliage_master])
        progress('%d material variants; baking' % len(variants))
        baked = dict(_pmap(pool, job_bake, [variants[n] for n in sorted(variants)], 'bake', events))
        # -- textures: dedupe by content, name by the first variant that made each
        tex_jobs, tex_path, mic_specs, mat_records = [], {}, {}, {}
        for name in sorted(baked):
            b = baked[name]
            plan = b['plan']
            route = plan['route']
            if route in ('fallback', 'drop', 'water'):
                mat_records[name] = {'route': route, 'plan': plan}
                continue
            path = '%s/Materials/%s' % (own, fname_safe(name))
            texmap = {}
            for kind, rec in sorted(b['images'].items()):
                base_kind = re.sub(r'\d$', '', kind)
                fam, size = tex_kind(base_kind, route, rec['size'], rec['constant'])
                if base_kind == 'NM' and rec['constant']:
                    a = _load_image(cache, rec['hash'])
                    if abs(int(a[0, 0, 0]) - 128) <= 1 and abs(int(a[0, 0, 1]) - 128) <= 1:
                        texmap[kind] = SHIPPED_FLAT_NM
                        continue
                key = (rec['hash'], fam, size)
                if key in tex_path and tex_path[key] in texmap.values():
                    # the same pixels twice in one MIC (an emissive equal to its base colour, two
                    # equal mesh-paint layers): a separate copy, since a MIC cannot retarget two
                    # imports onto one texture
                    key = key + (name, kind)
                if key not in tex_path:
                    tp = '%s/Textures/T_%s_%s' % (own, name[3:] if name.startswith('MI_') else name, kind)
                    tex_path[key] = tp
                    tex_jobs.append((tp, fam, size, rec['hash'], stage))
                texmap[kind] = tex_path[key]
            mic_specs[name] = (path, route, plan, texmap)
            mat_records[name] = {'route': route, 'plan': plan, 'package': path, 'textures': texmap}
        progress('%d textures to encode (%d variants)' % (len(tex_jobs), len(mic_specs)))
        tex_res = dict(_pmap(pool, job_texture, sorted(tex_jobs), 'textures', events))
        previews_dir = os.path.join(lib, 'previews' + NEW_SUFFIX)
        shutil.rmtree(previews_dir, ignore_errors=True)
        os.makedirs(previews_dir, exist_ok=True)
        tex_previews = {}
        for tp, _fam, _size, hh, _stage in tex_jobs:
            tex_previews[tp] = _write_preview(previews_dir, tp, _load_image(cache, hh))
        stage_files = {}
        tex_bytes = 0
        for p, r in sorted(tex_res.items()):
            stage_files.update(r['files'])
            tex_bytes += r['bytes']
            report['problems'] += ['%s: %s' % (p, x) for x in r['problems']]
        # -- MICs
        mic_bytes = 0
        fallback_path = '%s/Materials/MI_Fallback_Grey' % own
        need_fallback = any(v[0] == 'static' and v[1] == 'fallback' for v in slot_variant.values()) or \
            any(r['route'] == 'fallback' for r in mat_records.values())
        if need_fallback:
            fb = {}
            for kind, rgb in (('BC', FALLBACK_BC), ('MRA', FALLBACK_MRA)):
                a = np.zeros((256, 256, 4), np.uint8)
                a[..., :3] = rgb
                a[..., 3] = 255
                rec = _store_images(cache, {kind: a})[kind]
                fam = 'bc' if kind == 'BC' else 'mra'
                tp = '%s/Textures/T_Fallback_Grey_%s' % (own, kind)
                r = job_texture_local(cache, (tp, fam, 256, rec['hash'], stage))
                stage_files.update(r['files'])
                tex_bytes += r['bytes']
                fb[kind] = tp
            fb['NM'] = SHIPPED_FLAT_NM
            mic_specs['MI_Fallback_Grey'] = (fallback_path, 'prop', {'emissive_scale': 0.0}, fb)
        mic_expect = {}
        for name, (path, route, plan, texmap) in sorted(mic_specs.items()):
            spec = mic_spec(route, plan, texmap)
            try:
                blobs, expect = MW.write_mic(spec, path)
            except SystemExit as e:
                report['problems'].append('%s: MIC not written: %s' % (path, e))
                continue
            stage_files.update(_stage_package(stage, path, blobs))
            mic_bytes += sum(len(x) for x in blobs.values())
            allowed = tuple(v for v in texmap.values() if not v.startswith(own))
            report['problems'] += MW.readback_problems(blobs, path, expect, own, allowed)
            from riftlord.core import pkgcheck
            report['problems'] += ['%s: %s' % (path, x) for x in pkgcheck.problems(blobs['.uasset'], blobs['.uexp'], path)]
            mic_expect[name] = spec
        # -- meshes
        mesh_jobs = []
        for m, v in sorted(info.items()):
            if 'slots' not in v:
                continue
            rel = source_rel(m, content)
            mname = sanitize(os.path.basename(rel))
            parts = [sanitize(x) for x in re.split(r'[\\/]', rel)[1:]]
            parts[-1] = fname_safe(parts[-1])
            pkg_path = '%s/%s' % (own, '/'.join(parts))
            slot_jobs, out_mats = [], []
            out_index = {}
            for k, s in enumerate(v['slots']):
                kind, name, mat = slot_variant[(m, k)]
                permesh = None
                if kind == 'static':
                    if name == 'drop':
                        slot_jobs.append({'drop': True, 'out_slot': 0})
                        continue
                    target = {'water': WATER_MIC, 'shipped': materials.get(mat)}.get(name) or fallback_path
                    plan = None
                else:
                    rec = mat_records[name]
                    plan = rec['plan']
                    if rec['route'] == 'drop':
                        slot_jobs.append({'drop': True, 'out_slot': 0})
                        continue
                    target = WATER_MIC if rec['route'] == 'water' else rec.get('package', fallback_path)
                    if rec['route'] == 'fallback':
                        target = fallback_path
                    permesh = None
                    if rec['route'] in PERMESH_ROUTES and PM.needs_bake(plan.get('reasons')) and rec.get('package'):
                        base = rec['package'].rsplit('/', 1)[-1]
                        permesh = {'material': variants[name]['material'], 'vc': variants[name]['vc'],
                                   'large': variants[name]['large'],
                                   'foliage_master': variants[name].get('foliage_master', 'alphamask'),
                                   'route': rec['route'], 'variant': name,
                                   'mic': '%s/Materials/%s' % (own, fname_safe('%s__%s' % (base, mname))),
                                   'tex': '%s/Textures/T_%s__%s' % (own, base[3:] if base.startswith('MI_') else base, mname),
                                   'fallback_tex': rec.get('textures') or {}, 'frame': plan.get('frame')}
                        target = permesh['mic']
                okey = (target, s['slot'])
                if okey not in out_index:
                    out_index[okey] = len(out_mats)
                    prev = baked.get(name, {}).get('preview') if kind == 'variant' else None
                    ph = None
                    if prev is not None:
                        ph = _store_images(cache, {'P': prev})['P']['hash']
                    out_mats.append({'path': target, 'slot': s['slot'], 'preview': ph})
                fr = plan.get('frame') if plan and plan['route'] not in ('fallback', 'water') else None
                paint = variants[name]['paint'] if kind == 'variant' and plan and plan['route'] == 'paint' else None
                job = {'drop': False, 'frame': fr, 'two_sided': bool(plan and plan.get('two_sided_mesh')),
                       'two_sided_material': bool(kind != 'static' and rec['route'] in TWO_SIDED_ROUTES),
                       'paint': paint, 'out_slot': out_index[okey]}
                if kind != 'static' and permesh:
                    job['permesh'] = permesh
                slot_jobs.append(job)
            role_survey = _survey_role(m)
            ext = v['extent']
            role = AN.role_of(mname, m, role_survey, ext)
            key = h(_with_encoder([VERSION, code_hash(*MESH_WORK_CODE), fp, rel, ident['scale'], slot_jobs, out_mats, role]))
            mesh_jobs.append({'name': mname, 'rel': rel, 'source': m, 'package': pkg_path, 'scale': ident['scale'],
                              'slot_jobs': slot_jobs, 'out_materials': out_mats, 'role': role, 'key': key,
                              'stage': stage, 'cache': cache, 'slots_info': v['slots']})
        names = [j['name'] for j in mesh_jobs]
        dup = {n for n in names if names.count(n) > 1}
        for j in mesh_jobs:
            if j['name'] in dup:
                j['thumb_name'] = sanitize(j['rel'].replace(os.sep, '_'))
        progress('writing %d meshes' % len(mesh_jobs))
        res = dict(_pmap(pool, job_mesh, sorted(mesh_jobs, key=lambda j: j['rel']), 'meshes', events))
        if guard is not None:
            guard.close()
    # -- results per mesh
    thumbs_dir = os.path.join(lib, 'thumbs' + NEW_SUFFIX)
    shutil.rmtree(thumbs_dir, ignore_errors=True)
    os.makedirs(thumbs_dir, exist_ok=True)
    mesh_bytes = 0
    catalogue_meshes = []
    for j in sorted(mesh_jobs, key=lambda j: j['rel']):
        r = res[j['rel']]
        entry = _catalogue_mesh(j, r, mat_records, plan0, slot_variant, own, ident)
        if r.get('thumb'):
            tn = j.get('thumb_name', j['name']) + '.png'
            with open(os.path.join(thumbs_dir, tn), 'wb') as fh:
                fh.write(r['thumb'])
            entry['thumbnail'] = 'thumbs/' + tn
        for tp, img in ((r.get('permesh') or {}).pop('_tex_previews', None) or {}).items():
            tex_previews[tp] = _write_preview(previews_dir, tp, img)
        stage_files.update(r.get('files', {}))
        mesh_bytes += r.get('bytes', 0)
        report['problems'] += ['%s: %s' % (j['name'], x) for x in r.get('problems', [])]
        catalogue_meshes.append(entry)
    pak, raw, entries = _pack_and_check(lib, pid, stage_files, report, events, progress)
    # -- catalogue
    materials = {}
    for name, rec in sorted(mat_records.items()):
        plan = rec['plan']
        materials[name] = {'package': rec.get('package'), 'source': plan.get('source'), 'route': rec['route'],
                           'stable_id': '%s:%s' % (pid, name),
                           'master': MASTER.get(rec['route']), 'grade': plan.get('grade'),
                           'reasons': plan.get('reasons', []), 'weather': WEATHER.get(rec['route']),
                           'textures': rec.get('textures', {}), 'bake_size': plan.get('size'),
                           'frame': plan.get('frame'), 'vertex_colour': plan.get('vc'),
                           'emissive_scale': plan.get('emissive_scale')}
    counts = _counts(catalogue_meshes, materials)
    cat = {'format': 'rift-lord-fab-library', 'format_version': 1, 'converter': VERSION, 'build': build,
           'root': own, 'texture_previews': {k: tex_previews[k] for k in sorted(tex_previews)}, 'pack': ident,
           'mount': own, 'pak': os.path.basename(pak), 'fingerprint': fp, 'conventions': _conventions(),
           'counts': counts, 'meshes': catalogue_meshes, 'materials': materials}
    _swap_in(lib, pak, cat, ('previews', 'thumbs'))
    report['counts'] = counts
    report['sizes'] = {'pak_bytes': len(raw), 'textures_bytes': tex_bytes, 'materials_bytes': mic_bytes,
                       'meshes_bytes': mesh_bytes, 'packages': sum(1 for e in stage_files if e.endswith('.uasset')),
                       'pak_entries': len(entries)}
    report['library'] = lib
    report['pak'] = pak
    report['seconds'] = round(time.time() - t_start, 1)
    events.finish(cache)
    report['ok'] = not report['problems']
    with open(os.path.join(lib, 'report.json'), 'w') as fh:
        json.dump({k: v for k, v in report.items() if k != 'seconds'}, fh, indent=1, sort_keys=True, default=str)
    return report


def _conventions():
    return {'units': 'uu (cm)', 'axes': 'UE left-handed: x forward, y right, z up',
            'mesh_space': 'mesh-local, scale baked in; place actors at scale 1.0',
            'grid_cell': AN.CELL, 'grid_half_thickness': AN.THIN,
            'grid_bits': {'floor': 1, 'wall': 2, 'ceiling': 4, 'war_machine_floor': 8,
                          'war_machine_wall': 16, 'war_machine_ceiling': 32},
            'ogre_capsule': {'height': AN.OGRE_HEIGHT, 'width': AN.OGRE_WIDTH},
            'hero': {'height': AN.HERO_HEIGHT, 'width': AN.HERO_WIDTH}}


def _pack_and_check(lib, pid, stage_files, report, events, progress):
    """The import gate over every staged package, then <pid>.pak.new written, read back and
    preflighted. Adds to report['problems'] and report['preflight']; returns (pak, raw, entries)."""
    progress('checking imports of %d packages' % sum(1 for e in stage_files if e.endswith('.uasset')))
    if events:
        events('pack', 0, 2, 'checking imports')
    report['problems'] += imports_problems(stage_files)
    if events:
        events('pack', 1, 2, 'writing the pak')
    pak = os.path.join(lib, '%s.pak' % pid)
    from riftlord.formats.ue import pakv4
    pakv4.write(pak + NEW_SUFFIX, {k: stage_files[k] for k in sorted(stage_files)})
    _m, _v, entries = pakv4.read(pak + NEW_SUFFIX)
    raw = open(pak + NEW_SUFFIX, 'rb').read()
    same = all(raw[o + pakv4.ENTRY_SIZE:o + pakv4.ENTRY_SIZE + n] == open(stage_files[name], 'rb').read()
               for name, o, n in entries)
    if not same or len(entries) != len(stage_files):
        report['problems'].append('pak does not read back')
    if events:
        events('pack', 2, 2)
        events('checks', 0, 1, 'preflight')
    rc, pf = preflight(pak + NEW_SUFFIX)
    if events:
        events('checks', 1, 1)
    if rc:
        report['problems'].append('preflight against the proven paks FAILED: %s' % [x for x in pf if x.startswith('FAIL')][:10])
    report['preflight'] = pf[-3:]
    return pak, raw, entries


def _swap_in(lib, pak, cat, dirs):
    """Swaps the new pak, folders (<dir>.new) and catalogue in together, so an interrupted run
    leaves the previous library whole (a stopped Sky Dungeon run had emptied previews/, 2026-10-15)."""
    os.replace(pak + NEW_SUFFIX, pak)
    for d in dirs:
        shutil.rmtree(os.path.join(lib, d), ignore_errors=True)
        os.replace(os.path.join(lib, d + NEW_SUFFIX), os.path.join(lib, d))
    with open(os.path.join(lib, 'catalogue.json' + NEW_SUFFIX), 'w') as fh:
        json.dump(cat, fh, indent=1, sort_keys=True)
    os.replace(os.path.join(lib, 'catalogue.json' + NEW_SUFFIX), os.path.join(lib, 'catalogue.json'))


# -- libraries of ready-made pieces ---------------------------------------------------------------------

#: The code whose logic names a piece library's build (write_pieces), with the caller's fingerprint.
PIECE_CODE = ('analyse.py', 'thumbs.py', '../formats/omd3/omd3smwrite.py', '../formats/omd3/omd3sm_format.py')
#: Stages a piece library reports (no materials, bakes or textures).
PIECE_STAGES = ('meshes', 'pack', 'checks')
#: Collision of a piece that names none: the drawn triangles, for simple queries too.
DRAWN = {'boxes': [], 'convex': [], 'trace': 'complex_as_simple', 'double_sided': False}
#: The pieces of the write_pieces run in progress; forked workers read them by index.
_PIECES = []
#: The code a carried material instance adds to a piece library's build.
MIC_CODE = ('micwrite.py',)
#: A carried instance's folder under the build root.
MIC_FOLDER = 'Materials'
#: MIC parameter arrays by write_carried_mic's spec key.
MIC_ARRAYS = {'scalars': 'ScalarParameterValues', 'vectors': 'VectorParameterValues',
              'textures': 'TextureParameterValues'}
_NO_GUID = '0' * 32


def _mic_rows(pkg, path):
    """[(spec key, label (with FName number), ExpressionGUID hex, ParameterValue offset)] of a MIC
    export's parameter values, in order."""
    export = MW.trapmod.export_by_name(pkg, MW.leaf(path))
    names = [s for s, _h in pkg.names]
    holder = type('Names', (), {'names': names})()
    data = export['data']
    found, _end = MW.skinmod.properties(pkg, data)
    arrays = {v: k for k, v in MIC_ARRAYS.items()}
    rows = []
    for pname, kind, at, head, _size in found:
        if kind != 'ArrayProperty' or pname not in arrays:
            continue
        count = struct.unpack_from('<i', data, at + head)[0]
        o = at + head + 4 + MW.missionproto.Table.INNER_TAG
        for _ in range(count):
            label = guid = value_at = None
            while True:
                sub, used = MW.missionproto.Table._read_field(holder, data, o)
                if sub is None:
                    o += used
                    break
                body = o + len(sub.header)
                if sub.name == 'ParameterInfo':
                    label = MW.param_name(holder, data, body)
                elif sub.name == 'ParameterValue':
                    value_at = body
                elif sub.name == 'ExpressionGUID':
                    guid = data[body:body + 16].hex()
                o += used
            rows.append((arrays[pname], label, guid or _NO_GUID, value_at))
    return rows


def _label_base(label):
    """A parameter label less its FName number ('BC_2' -> 'BC')."""
    head, _s, tail = label.rpartition('_')
    return head if head and tail.isdigit() else label


def _texture_import(pkg, path):
    """The import index of the texture object in package `path`."""
    names = [s for s, _h in pkg.names]
    for k, raw in enumerate(pkg.imports):
        imp = struct.unpack('<7i', raw)
        if names[imp[5]] == MW.leaf(path) and imp[4] < 0:
            outer = struct.unpack('<7i', pkg.imports[-imp[4] - 1])
            if names[outer[5]] == path:
                return -k - 1
    raise SystemExit('no import of %s' % path)


def write_carried_mic(spec, path):
    """A data-only MIC cloned from a shipped donor (Parent kept), its values set by ExpressionGUID.

    spec: {'donor': shipped MIC, 'textures': [(name, ExpressionGUID hex, texture path)],
           'scalars': [(name, guid, float)], 'vectors': [(name, guid, (r, g, b, a))]}.
    name is the parameter's name with or without its FName number; it must agree with the GUID.
    A scalar or vector with guid None is set by name, appended when the donor lacks it. Every donor
    texture outside SHARED_OK is set. Slots naming one texture share one import; a donor import
    no slot keeps stays, unused. Returns ({ext: bytes}, expected values with 'guids')."""
    donor_path = spec['donor']
    b = MW.shipped(donor_path, ('.uasset', '.uexp'))
    rows = _mic_rows(MW.Package(b['.uasset'], b['.uexp']), donor_path)
    keys = [(r[0], r[1]) for r in rows]
    if len(set(keys)) != len(keys):
        raise SystemExit('%s: donor %s repeats a parameter label' % (path, donor_path))
    by_guid = {(r[0], r[2]): r[1] for r in rows if r[2] != _NO_GUID}
    donor = MW.donor_values(donor_path)
    want = {k: {} for k in MIC_ARRAYS}
    for arr in MIC_ARRAYS:
        for name, guid, value in spec.get(arr) or ():
            if guid is None and arr != 'textures':
                label = name
            else:
                label = by_guid.get((arr, str(guid).lower()))
                if label is None:
                    raise SystemExit('%s: donor %s has no %s parameter with ExpressionGUID %s'
                                     % (path, donor_path, arr[:-1], guid))
                if name not in (label, _label_base(label)):
                    raise SystemExit('%s: ExpressionGUID %s is %s, not %s' % (path, guid, label, name))
            if label in want[arr]:
                raise SystemExit('%s: %s %s is set twice' % (path, arr[:-1], label))
            want[arr][label] = value
    tex = want['textures']
    unset = sorted(k for k, was in donor['textures'].items() if k not in tex and not was.startswith(MW.SHARED_OK))
    if unset:
        raise SystemExit('%s: donor textures not set: %s' % (path, unset))
    # one import per texture: a texture the donor imports keeps its import; any other takes the
    # import of a slot whose donor texture nothing keeps
    kept = {now for now in tex.values() if now in donor['textures'].values()}
    retarget = {}
    for now in sorted(set(tex.values()) - kept):
        free = [donor['textures'][k] for k in sorted(tex) if tex[k] == now
                and donor['textures'][k] not in kept and donor['textures'][k] not in retarget]
        if not free:
            free = [was for k, was in sorted(donor['textures'].items())
                    if was not in kept and was not in retarget and not was.startswith(MW.SHARED_OK)]
        if not free:
            raise SystemExit('%s: no donor import is free for %s' % (path, now))
        retarget[free[0]] = now
    pkg = MW._renamed_package(donor_path, path, retarget)
    export = MW.trapmod.export_by_name(pkg, MW.leaf(path))
    data = bytearray(export['data'])
    for arr, label, _g, value_at in _mic_rows(pkg, path):
        if arr == 'textures' and label in tex:
            struct.pack_into('<i', data, value_at, _texture_import(pkg, tex[label]))
    export['data'] = bytes(data)
    scal = {k: float(v) for k, v in want['scalars'].items()}
    vec = {k: tuple(float(x) for x in v) for k, v in want['vectors'].items()}
    MW._set_values(pkg, path, {k: v for k, v in scal.items() if k in donor['scalars']},
                   {k: v for k, v in vec.items() if k in donor['vectors']})
    add_s = {k: v for k, v in scal.items() if k not in donor['scalars']}
    add_v = {k: v for k, v in vec.items() if k not in donor['vectors']}
    if add_s or add_v:
        MW._add_overrides(pkg, path, add_s, add_v, donor['parent'].split(' (')[0])
    ua, ux = pkg.write()
    expect = copy.deepcopy(donor)
    expect['scalars'].update(scal)
    expect['vectors'].update(vec)
    expect['textures'].update(tex)
    expect['guids'] = {(r[0], r[1]): r[2] for r in rows}
    return {'.uasset': ua, '.uexp': ux}, expect


def carried_mic_problems(blobs, path, expect, own_root):
    """MW.readback_problems, and every donor parameter still carries its ExpressionGUID."""
    probs = MW.readback_problems(blobs, path, {k: v for k, v in expect.items() if k != 'guids'}, own_root,
                                 tuple(expect['textures'].values()))
    got = {(r[0], r[1]): r[2] for r in _mic_rows(MW.Package(blobs['.uasset'], blobs['.uexp']), path)}
    for key, guid in sorted(expect['guids'].items()):
        if got.get(key) != guid:
            probs.append('%s: %s %s reads back ExpressionGUID %s, wanted %s' % (path, key[0][:-1], key[1],
                                                                                 got.get(key), guid))
    return probs


def _init_pieces(cache):
    _W['cache'] = cache


def job_piece(arg):
    """Writes one piece of write_pieces: its package, the package gates, grids and thumbnail.
    arg: an index into _PIECES (forked workers) or (index, piece). Returns (index, result)."""
    from riftlord.formats.omd3 import omd3smwrite as W
    k, p = (arg, _PIECES[arg]) if isinstance(arg, int) else arg
    try:
        desc = dict(p['desc'])
        desc.setdefault('materials', np.zeros(len(desc['triangles']), np.int64))
        coll = dict(p.get('collision') or DRAWN)
        source = coll.pop('source', None) or ('drawn' if not coll.get('boxes') and not coll.get('convex') else 'authored')
        coll.setdefault('collides', True)
        mats = [{'path': m['path'], 'object': m.get('object') or m['path'].rsplit('/', 1)[-1],
                 'slot': sanitize(m.get('slot') or 'Slot%d' % n), 'class': m.get('class', 'MaterialInstanceConstant')}
                for n, m in enumerate(p['materials'])]
        blobs, rep = W.write(W.Template(), desc, p['package'], materials=mats, collision=dict(coll),
                             lightmap_index=p.get('lightmap_index', 1))
        problems = W.strict_check(blobs, p['name']) + W.pkgcheck_problems(blobs, p['name'])
        ana = _analysis_cached(_W['cache'], {'positions': desc['positions'], 'triangles': desc['triangles'],
                                             'coll': coll, 'two': None}, p['role'], 1.0)
        buf = io.BytesIO()
        TH.render(desc, p.get('thumb') or {}).save(buf, 'PNG', optimize=False)
        tris = np.bincount(np.asarray(desc['materials'], np.int64), minlength=len(mats))
        return k, {'status': 'converted', 'blobs': blobs, 'analysis': ana, 'problems': problems,
                   'thumb': buf.getvalue(), 'slot_triangles': [int(x) for x in tris],
                   'write': {'vertices': rep['vertices'], 'triangles': rep['triangles']},
                   'collision': {'source': source, 'boxes': len(coll.get('boxes') or []),
                                 'convex': len(coll.get('convex') or []), 'trace': coll.get('trace'),
                                 'double_sided': bool(coll.get('double_sided')), 'collides': bool(coll['collides']),
                                 'sphyls_ignored': 0}}
    except Exception as e:                               # noqa: BLE001 - reported per piece, never fatal
        import traceback
        return k, {'status': 'failed', 'error': '%s: %s' % (type(e).__name__, e), 'trace': traceback.format_exc(limit=6)}
    except SystemExit as e:                              # the writer refuses by SystemExit
        return k, {'status': 'failed', 'error': 'refused: %s' % e}


def write_pieces(pack_id, title, pieces, out_dir=None, *, kind, fingerprint, mics=None, jobs=None, events=None,
                 progress=log):
    """Writes ready-made mesh pieces (not read from a vault) as a library in the convert format;
    returns the summary dict (also written to report.json).

    fingerprint: a hash of everything that shapes the pieces (source data, the caller's cutting
    rules); with the writer code it names the build folder. kind: catalogue pack.kind.
    Each piece: {name, source (unique; the stable id), role, desc (writer mesh description,
    mesh-local; 'materials' per triangle defaults to slot 0), materials [{path or mic, slot, object?,
    class? (MaterialInstanceConstant), grade? (exact), reasons?}], collision (writer dict, plus
    optional 'source' and 'collides'; None = DRAWN), thumb ({slot: thumbs.render base colour}),
    geometry (extra catalogue geometry), lightmap_index? (1), display_name?}.
    mics: carried material instances, each a write_carried_mic spec plus 'name', written to
    <build root>/Materials/<name>; a piece material {'mic': name} names one. They join the build id."""
    t_start = time.time()
    pid = sanitize(pack_id)
    names = [fname_safe(sanitize(p['name'])) for p in pieces]
    mics = [dict(m, name=fname_safe(sanitize(m['name']))) for m in (mics or [])]
    mic_names = [m['name'] for m in mics]
    for what, seq in (('name', names), ('source', [p['source'] for p in pieces]), ('material instance', mic_names)):
        dup = sorted({x for x in seq if seq.count(x) > 1})
        if dup:
            raise ValueError('pieces share a %s: %s' % (what, ', '.join(dup[:5])))
    unknown = sorted({m['mic'] for p in pieces for m in p['materials'] if m.get('mic') and m['mic'] not in mic_names})
    if unknown:
        raise ValueError('pieces name material instances not given: %s' % ', '.join(unknown[:5]))
    out_dir = out_dir or default_library()
    lib = os.path.join(out_dir, pid)
    cache = os.path.join(cache_root(), pid)
    stage = os.path.join(cache, 'stage')
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage, exist_ok=True)
    os.makedirs(lib, exist_ok=True)
    parts = [VERSION, logic_hash(*PIECE_CODE), fingerprint, kind]
    if mics:
        parts += [logic_hash(*MIC_CODE), mics]
    build = h(parts)[:BUILD_ID_LEN]
    own = '%s/%s/B%s' % (MOUNT_ROOT, pid, build)
    ident = {'id': pid, 'title': title, 'scale': 1.0, 'known': True, 'vault_item': None, 'kind': kind}
    jobs = jobs or default_jobs()
    report = {'pack': ident, 'converter': VERSION, 'fingerprint': fingerprint, 'problems': [], 'warnings': [],
              'settings': {'workers': jobs}}
    progress('%s (%s): %d pieces, library %s' % (title, pid, len(pieces), lib))
    events = _Progress(events, _expected_seconds(cache, len(pieces), PIECE_STAGES))
    mic_path = {n: '%s/%s/%s' % (own, MIC_FOLDER, n) for n in mic_names}
    _PIECES[:] = [dict(p, name=n, package='%s/Meshes/%s' % (own, n),
                       materials=[dict(m, path=mic_path[m['mic']]) if m.get('mic') else m for m in p['materials']])
                  for p, n in zip(pieces, names)]
    from riftlord.formats.omd3 import omd3physx
    omd3physx.build_tool()
    fork = os.name != 'nt'
    try:
        with multiprocessing.get_context('fork' if fork else 'spawn').Pool(
                jobs, initializer=_init_pieces, initargs=(cache,)) as pool:
            items = range(len(_PIECES)) if fork else list(enumerate(_PIECES))
            res = dict(_pmap(pool, job_piece, items, 'meshes', events))
        pieces = list(_PIECES)
    finally:
        _PIECES[:] = []
    thumbs_dir = os.path.join(lib, 'thumbs' + NEW_SUFFIX)
    shutil.rmtree(thumbs_dir, ignore_errors=True)
    os.makedirs(thumbs_dir, exist_ok=True)
    stage_files, meshes, mesh_bytes = {}, [], 0
    for k, p in enumerate(pieces):
        r = res[k]
        entry = {'name': p['name'], 'display_name': p.get('display_name') or humanize(p['name']),
                 'source': p['source'], 'stable_id': '%s:%s' % (pid, p['source']), 'package': p['package'],
                 'object_path': '%s.%s' % (p['package'], p['name']), 'role': p['role'], 'scale': 1.0,
                 'status': r['status']}
        meshes.append(entry)
        if r['status'] != 'converted':
            entry.update({'error': r['error'], 'grade': 'failed'})
            report['problems'].append('%s: %s' % (p['name'], r['error']))
            continue
        stage_files.update(_stage_package(stage, p['package'], r['blobs']))
        mesh_bytes += sum(len(b) for b in r['blobs'].values())
        report['problems'] += ['%s: %s' % (p['name'], x) for x in r['problems']]
        slots = [{'slot': m.get('slot'), 'source': m['path'], 'material': m['path'], 'variant': m.get('mic'),
                  'route': 'carried' if m.get('mic') else 'shipped', 'grade': m.get('grade', 'exact'),
                  'reasons': list(m.get('reasons') or ['%s material %s' % ('carried' if m.get('mic') else 'shipped',
                                                                            m['path'])]), 'dropped': False,
                  'triangles': r['slot_triangles'][n], 'weather': None}
                 for n, m in enumerate(p['materials'])]
        order = ('exact', 'close', 'approximate', 'fallback')
        ana = r['analysis']
        entry.update({'grade': max((s['grade'] for s in slots), key=order.index), 'materials': slots,
                      'bounds': ana['bounds'], 'pivot': ana['pivot'], 'triangles': r['write']['triangles'],
                      'vertices': r['write']['vertices'], 'collision': r['collision'], 'grids': ana['grids'],
                      'warnings': list(ana['warnings']),
                      'geometry': dict(p.get('geometry') or {}, **r['write'])})
        for key in ('opening', 'stairs'):
            if key in ana:
                entry[key] = ana[key]
        tn = p['name'] + '.png'
        with open(os.path.join(thumbs_dir, tn), 'wb') as fh:
            fh.write(r['thumb'])
        entry['thumbnail'] = 'thumbs/' + tn
    meshes.sort(key=lambda m: m['source'])
    materials, mic_bytes = {}, 0
    for m in mics:
        path = mic_path[m['name']]
        try:
            blobs, expect = write_carried_mic(m, path)
        except SystemExit as e:
            report['problems'].append('%s: MIC not written: %s' % (path, e))
            continue
        stage_files.update(_stage_package(stage, path, blobs))
        mic_bytes += sum(len(x) for x in blobs.values())
        report['problems'] += carried_mic_problems(blobs, path, expect, own)
        from riftlord.core import pkgcheck
        report['problems'] += ['%s: %s' % (path, x) for x in pkgcheck.problems(blobs['.uasset'], blobs['.uexp'], path)]
        materials[m['name']] = {'package': path, 'source': m['donor'], 'route': 'carried',
                                'stable_id': '%s:%s' % (pid, m['name']), 'master': expect['parent'],
                                'grade': 'exact', 'reasons': ['carried instance of %s' % m['donor']],
                                'weather': None, 'textures': dict(sorted(expect['textures'].items())),
                                'bake_size': None, 'frame': None, 'vertex_colour': None, 'emissive_scale': None}
    pak, raw, entries = _pack_and_check(lib, pid, stage_files, report, events, progress)
    counts = _counts(meshes, materials)
    cat = {'format': 'rift-lord-fab-library', 'format_version': 1, 'converter': VERSION, 'build': build,
           'root': own, 'texture_previews': {}, 'pack': ident, 'mount': own, 'pak': os.path.basename(pak),
           'fingerprint': fingerprint, 'conventions': _conventions(), 'counts': counts, 'meshes': meshes,
           'materials': materials}
    shutil.rmtree(os.path.join(lib, 'previews'), ignore_errors=True)
    _swap_in(lib, pak, cat, ('thumbs',))
    report.update({'counts': counts, 'library': lib, 'pak': pak, 'seconds': round(time.time() - t_start, 1),
                   'sizes': {'pak_bytes': len(raw), 'textures_bytes': 0, 'materials_bytes': mic_bytes,
                             'meshes_bytes': mesh_bytes, 'packages': sum(1 for e in stage_files if e.endswith('.uasset')),
                             'pak_entries': len(entries)}})
    events.finish(cache)
    report['ok'] = not report['problems']
    with open(os.path.join(lib, 'report.json'), 'w') as fh:
        json.dump({k: v for k, v in report.items() if k != 'seconds'}, fh, indent=1, sort_keys=True, default=str)
    return report


def job_texture_local(cache, args):
    _W['cache'] = cache
    return job_texture(args)[1]


def mic_spec(route, plan, tex):
    """The write_mic spec for a route."""
    em = float(plan.get('emissive_scale') or 0.0)
    if route == 'paint':
        t = {}
        for n in (1, 2):
            t['BC_%d' % n] = tex['BC%d' % n]
            t['MRH_%d' % n] = tex['MRH%d' % n]
            t['NM_%d' % n] = tex['NM%d' % n]
        return {'donor': DONOR['paint'], 'master': MASTER['paint'], 'textures': t, 'scalars': dict(PAINT_VALUES)}
    if route == 'alphamask':
        t = {'BC': tex['BC'], 'MRA': tex['MRA'], 'Normal': tex['NM'], 'EM': tex.get('EM', SHIPPED_BLACK)}
        v = {'EmissiveColor': (1.0, 1.0, 1.0, 1.0) if em else (0.0, 0.0, 0.0, 1.0), 'BC_ColorTint': (1.0, 1.0, 1.0, 1.0),
             'EmissiveFresnelColor': (0.0, 0.0, 0.0, 1.0)}
        s = dict(TRAP_VALUES, EmissiveBrightness=em or 1.0)
        return {'donor': DONOR['alphamask'], 'master': MASTER['alphamask'], 'parent': MASTER['alphamask'],
                'textures': t, 'scalars': s, 'vectors': v}
    if route == 'foliage':
        return {'donor': DONOR['foliage'], 'master': MASTER['foliage'],
                'textures': {'BC': tex['BC'], 'NM': tex['NM'], 'SRK': tex['SRK']}}
    if route == 'prop_em':
        return {'donor': DONOR['prop_em'], 'master': MASTER['prop'],
                'textures': {'BC': tex['BC'], 'MRA': tex['MRA'], 'Normal': tex['NM'], 'EM': tex['EM']},
                'scalars': dict(PROP_VALUES, EmissiveBrightness=em),
                'vectors': {'EmissiveColor': (1.0, 1.0, 1.0, 1.0), 'BC_ColorTint': (1.0, 1.0, 1.0, 1.0)}}
    if route == 'prop2s':
        return {'donor': DONOR['prop2s'], 'master': MASTER['prop'],
                'textures': {'BC': tex['BC'], 'MRA': tex['MRA'], 'Normal': tex['NM']},
                'scalars': dict(PROP_VALUES), 'vectors': {'BC_ColorTint': (1.0, 1.0, 1.0, 1.0)}}
    return {'donor': DONOR['prop'], 'master': MASTER['prop'],
            'textures': {'BC': tex['BC'], 'MRA': tex['MRA'], 'Normal': tex['NM'], 'MK2': SHIPPED_BLACK},
            'scalars': dict(PROP_VALUES, EmissiveBrightness=1.0),
            'vectors': {'EmissiveColor': (0.0, 0.0, 0.0, 1.0), 'BC_ColorTint': (1.0, 1.0, 1.0, 1.0)}}


_ROLE_RULES = None


def _survey_role(path):
    global _ROLE_RULES
    if _ROLE_RULES is None:
        from riftlord.fab.survey import report as R
        _ROLE_RULES = R.role
    return _ROLE_RULES(path)


def _catalogue_mesh(j, r, mat_records, plan0, slot_variant, own, ident):
    # stable_id: the same for this piece in every build of the pack (package paths carry the build)
    entry = {'name': j['name'], 'display_name': humanize(j['name']), 'source': j['rel'].replace(os.sep, '/'),
             'stable_id': '%s:%s' % (ident['id'], j['rel'].replace(os.sep, '/')),
             'package': j['package'], 'object_path': '%s.%s' % (j['package'], j['package'].rsplit('/', 1)[-1]),
             'role': j['role'], 'scale': ident['scale'], 'status': r.get('status')}
    if r.get('status') == 'failed':
        entry['error'] = r.get('error')
        entry['grade'] = 'failed'
        return entry
    slots = []
    grades = []
    for k, s in enumerate(j['slots_info']):
        kind, name, mat = slot_variant[(j['source'], k)]
        sj = j['slot_jobs'][k]
        if kind == 'static' and name == 'shipped':
            shipped = j['out_materials'][sj['out_slot']]['path']
            rec = {'route': name, 'plan': {'grade': 'exact', 'reasons': ['shipped material %s (by request)' % shipped]}}
        elif kind == 'static':
            rec = {'route': name, 'plan': plan0.get(mat) or {'grade': 'fallback', 'reasons': ['no material']}}
        else:
            rec = mat_records[name]
        plan = rec['plan']
        g = plan.get('grade') or 'fallback'
        d = {'slot': s['slot'], 'source': mat, 'material': None if sj.get('drop') else j['out_materials'][sj['out_slot']]['path'],
             'variant': name if kind == 'variant' else None, 'route': rec['route'], 'grade': g,
             'reasons': plan.get('reasons', []), 'dropped': bool(sj.get('drop')), 'triangles': s['triangles'],
             'weather': WEATHER.get(rec['route'])}
        slots.append(d)
        if not sj.get('drop'):
            grades.append(g)
    entry['materials'] = slots
    order = ('exact', 'close', 'approximate', 'fallback')
    entry['grade'] = max(grades, key=order.index) if grades else 'fallback'
    if r.get('status') == 'dropped':
        entry['reason'] = r.get('reason')
        return entry
    ana = r['analysis']
    entry.update({'bounds': ana['bounds'], 'pivot': ana['pivot'], 'triangles': r['write']['triangles'],
                  'vertices': r['write']['vertices'], 'collision': r['collision'], 'grids': ana['grids'],
                  'warnings': list(ana['warnings'])})
    if 'opening' in ana:
        entry['opening'] = ana['opening']
    if 'stairs' in ana:
        entry['stairs'] = ana['stairs']
    st = r['stats']
    if st.get('dropped_triangles'):
        entry['warnings'].append('%d triangles of effect / decal sections dropped' % st['dropped_triangles'])
    if r['collision']['source'] == 'box_fallback':
        entry['warnings'].append('no authored simple collision: one box around the mesh (+ render-mesh complex collision)')
    elif r['collision']['source'] == 'drawn':
        entry['warnings'].append('collides as drawn: a converter-made simple shape would stand proud of the drawing')
    elif r['collision']['source'] == 'none':
        entry['warnings'].append('sky: no collision')
    if r['collision'].get('sphyls_ignored'):
        entry['warnings'].append('%d capsule collision shapes not converted' % r['collision']['sphyls_ignored'])
    if st.get('uv_channels', 1) == 0:
        entry['warnings'].append('mesh has no UVs')
    if st.get('normals_rebuilt'):
        entry['normals'] = 'rebuilt (hard edges kept)'
    if any(d['grade'] == 'fallback' and not d['dropped'] for d in slots):
        entry['warnings'].append('a section draws with the neutral fallback material')
    entry['geometry'] = st
    return entry


def _counts(meshes, materials):
    by_status, by_role, by_grade = {}, {}, {}
    for m in meshes:
        by_status[m['status']] = by_status.get(m['status'], 0) + 1
        rr = by_role.setdefault(m['role'], {'converted': 0, 'fallback': 0, 'failed': 0, 'dropped': 0})
        if m['status'] == 'converted':
            rr['fallback' if m['grade'] == 'fallback' else 'converted'] += 1
        else:
            rr[m['status']] = rr.get(m['status'], 0) + 1
        by_grade[m['grade']] = by_grade.get(m['grade'], 0) + 1
    mg = {}
    for v in materials.values():
        mg[v['grade']] = mg.get(v['grade'], 0) + 1
    return {'meshes': len(meshes), 'by_status': by_status, 'by_role': by_role, 'mesh_grades': by_grade,
            'material_variants': len(materials), 'material_grades': mg,
            'grids': sum(len(m.get('grids', [])) for m in meshes),
            'meshes_with_grids': sum(1 for m in meshes if m.get('grids'))}
