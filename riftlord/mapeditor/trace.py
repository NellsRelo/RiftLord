"""The Map Editor's Trace tool: a level from another Orcs Must Die game drawn as a ghost
in the 3D view, to rebuild it by hand with OMD3 pieces.

A development tool, not mission data: nothing in a plan or a build reads it. Its state
is kept per open level or mission in Rift Lord's data folder
(gamepath.user_data(STATE_FOLDER)/STATE_FILE), keyed by the page's key
(`mission:<id>` or `level:<name>`):

    {"source": {"game": "omd3" | "omd2" | "omd1" | "omdu" | "omdd", "name": "<level or scene>"},
     "at": [x, y, z], "rotation": <yaw degrees>, "scale": <multiplier>,
     "opacity": 0..1, "visible": true, "mode": "ghost" | "wire" | "textured",
     "grid": <draw the source's trap grids>}

The ghost's own frame: `anchor` (geometry meta) is put on `at`, turned `rotation` about
Z and scaled by `scale`; world = at + Rz(rotation) . scale . (p - anchor).

## Geometry

Every source is reduced to the same thing, in Unreal space (Z up, left-handed): unique
meshes (float3 positions, triangle indices) and instances (a 4x4 per placement). Meshes
are welded (positions only; the page shades each face from its own slope), then a
mesh placed once, or whose placements together stay under BAKE_TRIS triangles, is
baked into one merged part; the rest stay instanced. A level is then a handful of
dozens of draw calls whatever its size: Scenario_05 draws 8.8 million triangles (1,985
placements of 143 meshes) from 1.06 million sent in 69 parts, a 19 MB file.

Served as one binary (`geometry(game, name)`), cached in gamepath.scratch(CACHE_FOLDER) (RIFTLORD_TRACE_CACHE moves it):

    'RLTR', u32 FORMAT_VERSION, u32 json length, json (utf-8, space-padded to 4 bytes)
    then per entry of meta['parts']: float32 positions [verts * 3],
    uint32 indices [tris * 3], float32 column-major matrices [instances * 16]

`instances == 0` is a baked part, drawn as it is. The json meta also carries `anchor`
(the floor-centre of the source's play area: the middle of the placements' 2%..98%
band in X and Y and the low edge of that band in Z, the way omd3level frames a level's
`focus`), `bounds`, `triangles` (as drawn), `sent` (triangles in the file), `removed`
(coplanar copies left out of the baked part: drop_covered) and counts.

## Sources

- **omd3**: a shipped level (leveldata.levels), read the way the Map Editor draws it:
  leveldata.level's placements and omd3serve's meshes (omd3landscape for terrain).
  Hidden pieces, foliage (painted grass and plants: 11.4 of Scenario_09's 27.4 million
  triangles) and the skybox are left out. Already in Unreal space. A piece painted by
  its actor (`material`) wears that material's texture, as the Map Editor draws it.
- **omd2**: an OMD2 scene (`Orcs Must Die 2/data.zip`): the placements its shipping
  zone bake draws (riftlord/trace_vision.py), the art meshes and the exporter's merged
  `SM_*` meshes, each submesh with its own diffuse map. Vision is right-handed: every
  placement is mirrored through Y (transform.MIRROR_AXIS) as emitplan does, and
  nothing is re-centred, so a level converted by omd2record lines up by its
  `scratch.source.shift`. 1:1 in world units (transform.WORLD_SCALE). Scenes ending in
  `Perf` are left out.
- **omd1**: an OMD1 scene (riftlord/trace_omd1.py): the same Vision formats as OMD2,
  spread over seven zips.
- **omdu**: an Unchained map (riftlord/trace_omdu.py): UE3 packages, already in Unreal
  space.
- **omdd**: a Deathtrap map (riftlord/trace_omdd.py): UE 5.3 IoStore with unversioned
  properties, read through the `Mappings.usmap` dumped from the game; already in
  Unreal space. Greyed when that file is missing.
"""

import functools
import json
import math
import os
import re
import struct
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

from riftlord.core import gamepath  # noqa: E402

#: Bump when the geometry or the file layout changes, so stale cached files are not served.
FORMAT_VERSION = 4
#: Cache folder name (gamepath.scratch).
CACHE_FOLDER = 'trace'
#: Rift Lord's data folder for the tool's state (gamepath.user_data), and its file.
STATE_FOLDER = 'Trace'
STATE_FILE = 'trace.json'
#: Most levels the state file remembers; the least recently saved go first.
STATE_MAX = 500
#: Display modes: translucent faces, wireframe, faces with their base-colour textures.
MODES = ('ghost', 'wire', 'textured')
#: Bump when the textured file changes (textured()).
TEXTURED_VERSION = 3


def cache_root():
    """The trace cache folder: RIFTLORD_TRACE_CACHE (the gate keeps one per format and
    textured version), else gamepath.scratch(CACHE_FOLDER)."""
    path = os.environ.get('RIFTLORD_TRACE_CACHE')
    if not path:
        return gamepath.scratch(CACHE_FOLDER)
    os.makedirs(path, exist_ok=True)
    return path
#: Longest side of a texture in the textured file (px).
TEXTURE_SIZE = 256
#: Bump when the trap grid answer changes (grids()).
GRIDS_VERSION = 1
#: The trap grid faces sit this far in front of their surface, in uu (traceui.js draws
#: them over the ghost).
GRID_LIFT = 12.0
#: One OMD3 trap cell, in uu (trapgrids.js CELL).
OMD3_CELL = 300.0
#: Opacity a new trace starts at (traceui.js DEFAULT_OPACITY).
DEFAULT_OPACITY = 0.35
#: Positions closer than this (uu, in the world) are welded into one vertex.
WELD_UU = 1.0
#: A mesh whose placements together hold no more than this many triangles is baked
#: into the merged part; a bigger one stays instanced.
BAKE_TRIS = 20000
#: Share of placements dropped at each end when framing a source (omd3level.band).
BAND_DROP = 0.02
#: OMD2 scenes left out of the list: performance-test copies of real scenes.
OMD2_SKIP = re.compile(r'Perf$')

#: The games, in the order the picker lists them (release order): (id, label, Steam folder).
GAMES = (('omd1', 'Orcs Must Die', 'Orcs Must Die!'),
         ('omd2', 'Orcs Must Die 2', 'Orcs Must Die 2'),
         ('omdu', 'Orcs Must Die: Unchained', 'OrcsMustDieUnchained'),
         ('omd3', 'Orcs Must Die 3', 'Orcs Must Die 3'),
         ('omdd', 'Orcs Must Die: Deathtrap', 'Orcs Must Die Deathtrap'))
GAME_IDS = tuple(g[0] for g in GAMES)


# -- state --------------------------------------------------------------------------

def _num(value, fallback, lo=None, hi=None):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(out):
        return fallback
    if lo is not None:
        out = max(lo, out)
    if hi is not None:
        out = min(hi, out)
    return out


def clean(raw):
    """The tool's state as kept, or None when it names no usable source."""
    if not isinstance(raw, dict):
        return None
    src = raw.get('source')
    if not isinstance(src, dict) or src.get('game') not in GAME_IDS or not src.get('name'):
        return None
    source = {'game': src['game'], 'name': str(src['name'])[:200]}
    at = raw.get('at') if isinstance(raw.get('at'), (list, tuple)) else []
    at = [round(_num(v, 0.0, -1e7, 1e7), 2) for v in (list(at) + [0, 0, 0])[:3]]
    mode = raw.get('mode') if raw.get('mode') in MODES else MODES[0]
    return {'source': source, 'at': at,
            'rotation': round(_num(raw.get('rotation'), 0.0, -3600, 3600), 3),
            'scale': round(_num(raw.get('scale'), 1.0, 0.001, 1000), 5),
            'opacity': round(_num(raw.get('opacity'), DEFAULT_OPACITY, 0.0, 1.0), 3),
            'visible': bool(raw.get('visible', True)), 'mode': mode,
            'grid': bool(raw.get('grid', False))}


_STATE_LOCK = threading.Lock()


def _state_path():
    return os.path.join(gamepath.user_data(STATE_FOLDER), STATE_FILE)


def _safe_key(key):
    key = str(key or '')[:300]
    if not re.fullmatch(r'[A-Za-z0-9_.:\- ]+', key):
        raise SystemExit('no such trace key: %r' % (key,))
    return key


def _read_states():
    try:
        with open(_state_path(), encoding='utf-8') as fh:
            got = json.load(fh)
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def load_state(key):
    """{state} for one level or mission: the kept state, or None."""
    key = _safe_key(key)
    with _STATE_LOCK:
        return {'key': key, 'state': clean(_read_states().get(key))}


def save_state(key, raw):
    """Keep (or, for an empty state, forget) one level's state; returns it cleaned."""
    key = _safe_key(key)
    state = clean(raw)
    with _STATE_LOCK:
        states = _read_states()
        states.pop(key, None)
        if state:
            states[key] = state
        while len(states) > STATE_MAX:
            states.pop(next(iter(states)))
        _keep(_state_path(), json.dumps(states, indent=1).encode('utf-8'))
    return {'key': key, 'state': state}


# -- sources ------------------------------------------------------------------------

def game_folder(folder):
    """A game's install folder through the Steam libraries, or None."""
    return gamepath.other_game(folder)


def omd2_zip():
    """OMD2's data.zip, or None when OMD2 is not installed."""
    return gamepath.other_game('Orcs Must Die 2', 'data.zip')


def omd2_scenes():
    """OMD2 scene names, or [] when OMD2 is not installed."""
    path = omd2_zip()
    if not path:
        return []
    import zipfile
    from riftlord.formats.omd2 import trapgrids
    with zipfile.ZipFile(path) as zf:
        return [s for s in trapgrids.list_scenes(zf) if not OMD2_SKIP.search(s)]


def _levels(game):
    """[{name, label}] for one installed game."""
    if game == 'omd3':
        from riftlord.mapeditor import leveldata
        return [{'name': lv['name'],
                 'label': '%s (%s)' % (lv['title'], lv['name']) if lv.get('title')
                 else lv['name']} for lv in leveldata.levels()]
    if game == 'omd2':
        return [{'name': s, 'label': s} for s in omd2_scenes()]
    if game == 'omd1':
        from riftlord.formats.omd1 import trace_omd1
        return [{'name': s, 'label': s} for s in trace_omd1.scenes()]
    if game == 'omdu':
        from riftlord.formats.omdu import trace_omdu
        return [{'name': s, 'label': s} for s in trace_omdu.levels()]
    if game == 'omdd':
        from riftlord.formats.omdd import trace_omdd
        return [{'name': s, 'label': s[len('Level_'):].replace('_', ' ')
                 if s.startswith('Level_') else s} for s in trace_omdd.levels()]
    return []


def _not_read(game):
    """Why an installed game has no levels to offer, or '' when it can be read."""
    if game == 'omdd':
        from riftlord.formats.omdd import trace_omdd
        return trace_omdd.missing()
    return ''


def sources():
    """{games: [{id, label, installed, levels, reason}]}: the installed games and what
    of each can be traced. `reason` says why an installed game offers nothing."""
    out = []
    for gid, label, folder in GAMES:
        installed = True if gid == 'omd3' else bool(game_folder(folder))
        entry = {'id': gid, 'label': label, 'installed': installed, 'levels': [],
                 'reason': ''}
        if not installed:
            continue
        why = _not_read(gid)
        if why:
            entry['reason'] = why
        else:
            try:
                entry['levels'] = _levels(gid)
                if not entry['levels']:
                    entry['reason'] = 'no levels found'
            except (Exception, SystemExit) as exc:   # noqa: BLE001
                entry['reason'] = str(exc) or type(exc).__name__
        out.append(entry)
    return {'games': out}


# -- readers: unique meshes and placements ------------------------------------------
#
# Every reader returns (models, insts, focus, texture):
#
#   models   {key: {verts (N,3), uvs (N,2) or None, tris (M,3),
#                   sections [{first, tris, texture, uvScale?}]}}, `first` and `tris` in
#            triangles, `texture` a key for `texture` or None, and `uvScale` (optional)
#            a multiplier of the section's UVs
#   insts    [(key, 4x4 numpy, column vectors)], Unreal space
#   focus    the play area band ((x0, x1), (y0, y1), (z0, z1)) or None
#   texture  texture(key) -> PNG bytes or None
#
# A placement whose key has no model is a mesh that could not be read (`missing`).

def _model(verts, tris, uvs=None, sections=None):
    """A model from raw arrays; `sections` defaults to one untextured section."""
    import numpy as np
    verts = np.asarray(verts, np.float64).reshape(-1, 3)
    tris = np.asarray(tris, np.int64).reshape(-1)
    tris = tris[:len(tris) - len(tris) % 3].reshape(-1, 3)
    if uvs is not None:
        uvs = np.asarray(uvs, np.float64).reshape(-1, 2)
        if len(uvs) != len(verts):
            uvs = None
    if not sections:
        sections = [{'first': 0, 'tris': len(tris), 'texture': None}]
    return {'verts': verts, 'uvs': uvs, 'tris': tris, 'sections': sections}


def _no_texture(_key):
    return None


def _omd3(name):
    """A shipped OMD3 level (the reader protocol above). A piece its actor paints
    (`material`) is keyed `<mesh>\\x01<material>` and wears that material's texture."""
    import numpy as np
    from riftlord.formats.omd3 import omd3landscape
    from riftlord.mapeditor import omd3serve
    from riftlord.mapeditor import leveldata
    if name not in {lv['name'] for lv in leveldata.levels()}:
        raise SystemExit('no such OMD3 level: %r' % (name,))
    lvl = leveldata.level(name)
    sky = (lvl.get('sky') or {}).get('mesh')
    painted = lvl.get('materials') or {}
    insts = []
    for p in lvl.get('pieces') or []:
        if p.get('hidden') or p.get('foliage') or p['mesh'] == sky or not p.get('matrix'):
            continue
        key = p['mesh']
        if p.get('material') and painted.get(p['material']):
            key = '%s\x01%s' % (p['mesh'], p['material'])
        insts.append((key, np.asarray(p['matrix'], np.float64).reshape(4, 4).T))
    names = sorted({k.split('\x01')[0] for k, _ in insts})
    terrain = [n for n in names if omd3landscape.is_landscape_mesh(n)]
    got = omd3serve.meshes([n for n in names if n not in terrain])
    for n in terrain:
        try:
            m = omd3landscape.mesh(n)
        except Exception:                            # noqa: BLE001
            m = None
        if m:
            got[n] = m
    models = {}
    for key in sorted({k for k, _ in insts}):
        mesh, _, material = key.partition('\x01')
        m = got.get(mesh)
        if not m or not m.get('verts') or not m.get('tris'):
            continue
        ntri = len(m['tris']) // 3
        if material:
            sections = [{'first': 0, 'tris': ntri, 'texture': painted[material]}]
        else:
            slots = m.get('slotTextures') or []
            sections = []
            # a section's `first` is an INDEX (omd3mesh.sections), three per triangle
            for sec in m.get('sections') or []:
                first, count = int(sec.get('first') or 0), int(sec.get('tris') or 0)
                if count <= 0 or first % 3 or first // 3 + count > ntri:
                    continue
                slot = int(sec.get('slot') or 0)
                tex = (slots[slot] if slot < len(slots) else None) or m.get('texture')
                sections.append({'first': first // 3, 'tris': count, 'texture': tex})
            if not sections:
                sections = [{'first': 0, 'tris': ntri, 'texture': m.get('texture')}]
        models[key] = _model(m['verts'], m['tris'], m.get('uvs'), sections)

    def texture(key):
        return omd3serve.texture_png(key, TEXTURE_SIZE)
    return models, insts, lvl.get('focus'), texture


@functools.lru_cache(maxsize=1)
def _omd2_files():
    from riftlord.formats.vision import trace_vision
    return trace_vision.Files([omd2_zip()])


def _vision_texture(files):
    from riftlord.formats.vision import trace_vision

    def texture(key):
        return trace_vision.texture_png(files, key, _png)

    def reason(key):
        """Why texture(key) gives nothing (check)."""
        if not files.has(key):
            return 'file not in the game\'s zips'
        try:
            texture(key)
        except Exception as exc:                     # noqa: BLE001
            return 'decode failed (%s: %s)' % (type(exc).__name__, exc)
        return 'decoded to nothing'
    texture.reason = reason
    return texture


def _omd2(name):
    """An OMD2 scene (the reader protocol above), from its shipping zones
    (riftlord/trace_vision.py), mirrored into Unreal handedness (transform.mirror_matrix's
    S . M, applied to the placement; the mesh keeps its own vertices, so S . M is the
    whole transform)."""
    from riftlord.formats.vision import trace_vision
    if not omd2_zip():
        raise SystemExit('Orcs Must Die 2 is not installed')
    if name not in omd2_scenes():
        raise SystemExit('no such OMD2 scene: %r' % (name,))
    files = _omd2_files()
    models, insts = trace_vision.read(files, name, mirror_y)
    return models, insts, None, _vision_texture(files)


def mirror_y(m):
    """S . M with S the Y mirror (transform.MIRROR_AXIS): Vision's right-handed world in
    Unreal's left-handed one."""
    import numpy as np
    from riftlord.core import transform
    s = np.ones(4)
    s[transform.MIRROR_AXIS] = -1.0
    return s[:, None] * np.asarray(m, np.float64)


def _read(game, name):
    """(models, insts, focus, texture) of one source (the reader protocol above)."""
    if game == 'omd3':
        return _omd3(name)
    if game == 'omd2':
        return _omd2(name)
    if game == 'omd1':
        from riftlord.formats.omd1 import trace_omd1
        models, insts = trace_omd1.models(name)
        return models, insts, None, _vision_texture(trace_omd1.files())
    if game == 'omdu':
        from riftlord.formats.omdu import trace_omdu
        return trace_omdu.read(name)
    if game == 'omdd':
        from riftlord.formats.omdd import trace_omdd
        return trace_omdd.read(name)
    raise SystemExit('no such game: %r' % (game,))


# -- building -----------------------------------------------------------------------

def weld(verts, tris, cell):
    """(verts, tris) with positions closer than `cell` merged and the triangles that
    collapse dropped."""
    import numpy as np
    keys = np.floor(verts / max(cell, 1e-6)).astype(np.int64)
    _, first, inv = np.unique(keys, axis=0, return_index=True, return_inverse=True)
    inv = inv.reshape(-1)
    out = inv[tris]
    keep = (out[:, 0] != out[:, 1]) & (out[:, 1] != out[:, 2]) & (out[:, 0] != out[:, 2])
    return verts[first], out[keep]


def _band(values):
    from riftlord.formats.omd3 import omd3level
    return omd3level.band(list(values), BAND_DROP)


def _frame(at, focus):
    """(anchor, bounds) of a source from its placements' positions (module docstring)."""
    if focus:
        bx, by, bz = focus
    else:
        bx, by, bz = _band(at[:, 0]), _band(at[:, 1]), _band(at[:, 2])
    anchor = [round((bx[0] + bx[1]) / 2.0, 2), round((by[0] + by[1]) / 2.0, 2),
              round(float(bz[0]), 2)]
    bounds = [[round(float(bx[0]), 2), round(float(by[0]), 2), round(float(bz[0]), 2)],
              [round(float(bx[1]), 2), round(float(by[1]), 2), round(float(bz[1]), 2)]]
    return anchor, bounds


def unique(insts):
    """`insts` without exact repeats: one mesh twice at one transform (within
    DUPLICATE_UU) is drawn once, as the two would draw the same surface."""
    import numpy as np
    seen, out = set(), []
    for k, m in insts:
        sig = (k,) + tuple(np.round(np.asarray(m)[:3, :].ravel() / DUPLICATE_UU).astype(np.int64))
        if sig not in seen:
            seen.add(sig)
            out.append((k, m))
    return out


def _usable(model):
    """True when a model's triangles index its vertices."""
    t = model['tris']
    return len(model['verts']) > 0 and len(t) > 0 and int(t.max()) < len(model['verts']) \
        and int(t.min()) >= 0


#: Coplanar copies (drop_covered). Two triangles share a plane when each component of their
#: unit normals rounds to the same 1/NORMAL_STEPS and their distances from the origin to
#: the same COPLANAR_UU; a point lies on a triangle within COVER_EDGE_UU of its edges.
COPLANAR_UU = 0.5
NORMAL_STEPS = 100
COVER_EDGE_UU = 0.01
#: The points tested on a triangle: its centre, and COVER_INSET of the way from it to
#: each corner.
COVER_INSET = 0.8
#: Grid cells of one plane's lookup: the smallest cell side (uu), and the most cells.
COVER_CELL = 16.0
COVER_CELLS_MAX = 2000000
#: Most (point, triangle) tests for one plane and one point set (a plane with more is
#: left as it is), and how many are made at once.
COVER_PAIRS_MAX = 50000000
COVER_CHUNK = 1000000


def drop_covered(tris, owner):
    """Mask (N,) of the world triangles `tris` (N, 3, 3) to keep. A triangle whose centre
    and three inner points (COVER_INSET) each lie on a triangle of another placement (an
    other `owner`) in the same plane that outranks it -- larger, or as large and of an
    earlier placement -- is a copy: the game draws both and they z-fight (OMD2 zones
    place art pieces on the exporter's merged floors); dropped. The ranking is strict, so
    of overlapping copies the highest-ranked always stays."""
    import numpy as np
    keep = np.ones(len(tris), bool)
    if len(tris) < 2:
        return keep
    cr = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    size = np.linalg.norm(cr, axis=1)
    ok = size > 1e-6
    nrm = np.zeros_like(cr)
    nrm[ok] = cr[ok] / size[ok, None]
    big = np.argmax(np.abs(nrm), axis=1)
    sign = np.sign(nrm[np.arange(len(nrm)), big])
    sign[sign == 0] = 1.0
    nrm *= sign[:, None]
    dist = (nrm * tris.mean(axis=1)).sum(axis=1)
    key = np.concatenate([np.round(nrm * NORMAL_STEPS).astype(np.int64),
                          np.floor(dist / COPLANAR_UU).astype(np.int64)[:, None]], axis=1)
    idx = np.nonzero(ok)[0]
    if not len(idx):
        return keep
    _, grp = np.unique(key[idx], axis=0, return_inverse=True)
    grp = grp.reshape(-1)
    order = np.argsort(grp, kind='stable')
    for members in np.split(idx[order], np.flatnonzero(np.diff(grp[order])) + 1):
        own = owner[members]
        area = size[members]
        if len(members) < 2 or own.min() == own.max():
            continue
        flat = [k for k in range(3) if k != big[members[0]]]
        p = tris[members][:, :, flat]                # (m, 3, 2) in the plane
        lo, hi = p.min(axis=1), p.max(axis=1)
        cell = max(COVER_CELL, float(np.sqrt(np.prod(hi - lo + COVER_CELL, axis=1).sum()
                                             / COVER_CELLS_MAX)))
        c0 = np.floor(lo / cell).astype(np.int64)
        span = np.floor(hi / cell).astype(np.int64) - c0 + 1
        count = span[:, 0] * span[:, 1]
        # every cell each triangle's box touches, by a single int64 key
        tri = np.repeat(np.arange(len(members)), count)
        off = np.arange(int(count.sum())) - np.repeat(np.cumsum(count) - count, count)
        cells = ((c0[tri, 0] + off % span[tri, 0]) << 32) + (c0[tri, 1] + off // span[tri, 0])
        by = np.argsort(cells, kind='stable')
        cells, tri = cells[by], tri[by]
        centre = p.mean(axis=1)
        covered = np.ones(len(members), bool)
        for point in [centre] + [centre + COVER_INSET * (p[:, k] - centre) for k in range(3)]:
            pc = np.floor(point / cell).astype(np.int64)
            pk = (pc[:, 0] << 32) + pc[:, 1]
            start = np.searchsorted(cells, pk, 'left')
            n = np.searchsorted(cells, pk, 'right') - start
            if int(n.sum()) > COVER_PAIRS_MAX:
                covered[:] = False
                break
            hit = np.zeros(len(members), bool)
            ends = np.cumsum(n)
            first = 0
            while first < len(members):
                # members first..last, at most COVER_CHUNK tests at a time
                base = ends[first - 1] if first else 0
                last = max(first + 1, int(np.searchsorted(ends, base + COVER_CHUNK, 'right')))
                k = n[first:last]
                who = np.repeat(np.arange(first, last), k)
                at = np.repeat(start[first:last], k) + np.arange(int(k.sum())) - np.repeat(np.cumsum(k) - k, k)
                cand = tri[at]
                outranks = (own[cand] != own[who]) & (
                    (area[cand] > area[who]) | ((area[cand] == area[who]) & (own[cand] < own[who])))
                who, cand = who[outranks], cand[outranks]
                q = point[who]
                a, b, c = p[cand, 0], p[cand, 1], p[cand, 2]
                turn = np.sign((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1])
                               - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]))
                inside = np.ones(len(who), bool)
                for e0, e1 in ((a, b), (b, c), (c, a)):
                    edge = e1 - e0
                    side = (edge[:, 0] * (q[:, 1] - e0[:, 1]) - edge[:, 1] * (q[:, 0] - e0[:, 0])) * turn
                    inside &= side >= -COVER_EDGE_UU * np.hypot(edge[:, 0], edge[:, 1])
                hit[who[inside]] = True
                first = last
            covered &= hit
        keep[members[covered]] = False
    return keep


def _bake(chunks):
    """(verts, tris, removed) of the baked part from [(world verts, tris, owner)]: the
    copies drop_covered finds left out, then welded."""
    import numpy as np
    verts, tris, owners, base = [], [], [], 0
    for v, t, o in chunks:
        verts.append(v)
        tris.append(t + base)
        owners.append(np.full(len(t), o, np.int64))
        base += len(v)
    v, t = np.concatenate(verts), np.concatenate(tris)
    keep = drop_covered(v.astype(np.float32)[t], np.concatenate(owners))
    v, t = weld(v, t[keep], WELD_UU)
    return v, t, int((~keep).sum())


def build(models, insts, focus=None):
    """(meta, [arrays]) for one source: see the module docstring for the parts. The baked
    part leaves out coplanar copies (drop_covered; meta `removed` counts them)."""
    import numpy as np
    insts = unique(insts)
    by_mesh, order = {}, {}
    for i, (key, m) in enumerate(insts):
        if key in models and _usable(models[key]):
            by_mesh.setdefault(key, []).append(m)
            order.setdefault(key, []).append(i)
    missing = sorted({k for k, _ in insts if k not in by_mesh})
    baked = []
    parts, arrays = [], []
    drawn = 0
    for key in sorted(by_mesh):
        mats = by_mesh[key]
        verts, tris = models[key]['verts'], models[key]['tris']
        stack = np.stack(mats)
        # the largest axis scale any placement gives it: the local weld cell that keeps
        # every placement within WELD_UU in the world
        grow = float(np.abs(stack[:, :3, :3]).sum(axis=1).max()) or 1.0
        verts, tris = weld(verts, tris, WELD_UU / grow)
        if not len(tris):
            continue
        drawn += len(tris) * len(mats)
        if len(mats) == 1 or len(tris) * len(mats) <= BAKE_TRIS:
            for m, i in zip(mats, order[key]):
                baked.append((verts @ m[:3, :3].T + m[:3, 3], tris, i))
            continue
        parts.append({'mesh': key, 'verts': len(verts), 'tris': len(tris),
                      'instances': len(mats)})
        arrays.append((verts.astype(np.float32), tris.astype(np.uint32),
                       np.ascontiguousarray(stack.transpose(0, 2, 1)).astype(np.float32)))
    removed = 0
    if baked:
        v, t, removed = _bake(baked)
        parts.insert(0, {'mesh': '', 'verts': len(v), 'tris': len(t), 'instances': 0})
        arrays.insert(0, (v.astype(np.float32), t.astype(np.uint32), None))
    if not parts:
        raise SystemExit('nothing in it could be drawn')
    at = np.array([m[:3, 3] for ms in by_mesh.values() for m in ms])
    anchor, bounds = _frame(at, focus)
    meta = {'anchor': anchor, 'bounds': bounds, 'parts': parts, 'triangles': int(drawn),
            'removed': removed,
            'sent': int(sum(p['tris'] for p in parts)),
            'meshes': len(by_mesh), 'instances': int(sum(len(v) for v in by_mesh.values())),
            'missing': len(missing),
            'dropped': int(sum(1 for k, _ in insts if k not in by_mesh))}
    return meta, arrays


def pack(meta, arrays):
    """The binary the page reads (module docstring)."""
    head = json.dumps(meta, separators=(',', ':')).encode('utf-8')
    head += b' ' * (-len(head) % 4)
    out = [b'RLTR', struct.pack('<II', FORMAT_VERSION, len(head)), head]
    for verts, tris, mats in arrays:
        out.append(verts.tobytes())
        out.append(tris.tobytes())
        if mats is not None:
            out.append(mats.tobytes())
    return b''.join(out)


def unpack_meta(blob):
    """The json meta of a packed file."""
    if blob[:4] != b'RLTR':
        raise ValueError('not a trace file')
    _ver, n = struct.unpack_from('<II', blob, 4)
    return json.loads(blob[12:12 + n].decode('utf-8'))


# -- the cache ----------------------------------------------------------------------

def _safe(name):
    return ''.join(c if (c.isalnum() or c in '._-') else '_' for c in str(name))


def _keep(path, blob):
    tmp = path + '.part'
    with open(tmp, 'wb') as fh:
        fh.write(blob)
    os.replace(tmp, path)


_BUILD_LOCKS = {}
_BUILD_LOCKS_LOCK = threading.Lock()


def geometry(game, name):
    """The packed ghost of one source, built once and cached."""
    if game not in GAME_IDS:
        raise SystemExit('no such game: %r' % (game,))
    path = os.path.join(cache_root(),
                        'v%d_%s_%s.bin' % (FORMAT_VERSION, game, _safe(name)))
    with _BUILD_LOCKS_LOCK:
        lock = _BUILD_LOCKS.setdefault(path, threading.Lock())
    with lock:
        if os.path.isfile(path):
            with open(path, 'rb') as fh:
                return fh.read()
        models, insts, focus, _texture = _read(game, name)
        meta, arrays = build(models, insts, focus)
        meta.update({'game': game, 'name': name})
        blob = pack(meta, arrays)
        _keep(path, blob)
        _drop_stale()
        return blob


def _drop_stale():
    """Delete cached files (`v`: geometry, `tx`: textured, `grids`) of other versions."""
    folder = cache_root()
    now = {'v': FORMAT_VERSION, 'tx': TEXTURED_VERSION, 'grids': GRIDS_VERSION}
    for f in os.listdir(folder):
        m = re.match(r'(v|tx|grids)(\d+)_', f)
        if m and int(m.group(2)) != now[m.group(1)]:
            try:
                os.remove(os.path.join(folder, f))
            except OSError:
                pass


# -- textured -----------------------------------------------------------------------
#
# The Textured display: the same placements, each mesh with its UVs and base-colour
# texture. Served as its own file (`textured(game, name)`), built only when asked:
#
#     'RLTX', u32 TEXTURED_VERSION, u32 json length, json (space-padded to 4 bytes)
#     then per entry of meta['parts']: float32 positions [verts * 3], float32 uvs
#     [verts * 2], uint32 indices [tris * 3], float32 matrices [instances * 16]
#     then per entry of meta['textures']: its PNG bytes, padded to 4 bytes
#
# Parts as in `build`, one per material section of a mesh: small ones baked together,
# one baked part per texture, so a level is one draw call per texture plus its instanced
# meshes. Textures are downscaled to TEXTURE_SIZE. A part's `texture` is an index into
# meta['textures'], or -1 for a section whose texture is not known or not read (drawn as
# the ghost); meta['unread'] names the textures that could not be read. Every source
# gives each section its own base colour map.

def _png(image):
    import io
    image = image.convert('RGB')
    if max(image.size) > TEXTURE_SIZE:
        k = TEXTURE_SIZE / float(max(image.size))
        image = image.resize((max(1, int(image.width * k)), max(1, int(image.height * k))))
    buf = io.BytesIO()
    image.save(buf, format='PNG', optimize=True)
    return buf.getvalue()


def sections_of(models, insts):
    """({section key: {verts, uvs, tris, texture}}, insts per section): every model's
    sections as meshes of their own, holding only the vertices their triangles use."""
    import numpy as np
    out, keys = {}, {}
    for key in sorted({k for k, _ in insts}):
        m = models.get(key)
        if not m or not _usable(m):
            continue
        keys[key] = []
        for i, sec in enumerate(m['sections']):
            tris = m['tris'][sec['first']:sec['first'] + sec['tris']]
            if not len(tris):
                continue
            used, inv = np.unique(tris, return_inverse=True)
            uvs = m['uvs'][used] if m['uvs'] is not None else None
            if uvs is not None and sec.get('uvScale'):
                uvs = uvs * float(sec['uvScale'])
            k = '%s#%d' % (key, i)
            out[k] = {'verts': m['verts'][used], 'uvs': uvs, 'tris': inv.reshape(-1, 3),
                      'texture': sec.get('texture') if uvs is not None else None}
            keys[key].append(k)
    return out, [(k, m) for key, m in insts for k in keys.get(key, ())]


def build_textured(models, insts, focus, texture):
    """(meta, [arrays], [png]) for one source: see the section comment."""
    import numpy as np
    # each placement carries its index along, for drop_covered's order
    meshes, insts = sections_of(models, [(k, (m, i)) for i, (k, m) in enumerate(unique(insts))])
    by_mesh, order = {}, {}
    for key, (m, i) in insts:
        if key in meshes:
            by_mesh.setdefault(key, []).append(m)
            order.setdefault(key, []).append(i)
    tex_index, pngs = {}, []

    def tex_of(key):
        if not key:
            return -1
        if key not in tex_index:
            try:
                png = texture(key)
            except Exception:                        # noqa: BLE001
                png = None
            tex_index[key] = len(pngs) if png else -1
            if png:
                pngs.append((key, png))
        return tex_index[key]

    placed = []
    parts, arrays = [], []
    drawn = 0
    for key in sorted(by_mesh):
        mats = by_mesh[key]
        m = meshes[key]
        verts, tris = m['verts'], m['tris']
        uvs = m['uvs'] if m['uvs'] is not None else np.zeros((len(verts), 2))
        tex = tex_of(m['texture'])
        drawn += len(tris) * len(mats)
        if len(mats) == 1 or len(tris) * len(mats) <= BAKE_TRIS:
            for mm, i in zip(mats, order[key]):
                placed.append((tex, verts @ mm[:3, :3].T + mm[:3, 3], uvs, tris, i))
            continue
        stack = np.stack(mats)
        parts.append({'mesh': key, 'verts': len(verts), 'tris': len(tris),
                      'instances': len(mats), 'texture': tex})
        arrays.append((verts.astype(np.float32), uvs.astype(np.float32),
                       tris.astype(np.uint32),
                       np.ascontiguousarray(stack.transpose(0, 2, 1)).astype(np.float32)))
    # the baked placements, coplanar copies left out (drop_covered), one part per texture
    removed = 0
    baked = {}
    if placed:
        keep = drop_covered(np.concatenate([w.astype(np.float32)[t] for _x, w, _u, t, _i in placed]),
                            np.concatenate([np.full(len(t), i, np.int64)
                                            for _x, _w, _u, t, i in placed]))
        removed = int((~keep).sum())
        at = 0
        for tex, w, uvs, tris, _i in placed:
            mine = keep[at:at + len(tris)]
            at += len(tris)
            bucket = baked.setdefault(tex, ([], [], [], [0]))
            bucket[0].append(w)
            bucket[1].append(uvs)
            bucket[2].append(tris[mine] + bucket[3][0])
            bucket[3][0] += len(w)
    for tex in sorted(baked, reverse=True):
        v, u, t, _n = baked[tex]
        v, u, t = np.concatenate(v), np.concatenate(u), np.concatenate(t)
        parts.insert(0, {'mesh': '', 'verts': len(v), 'tris': len(t), 'instances': 0,
                         'texture': tex})
        arrays.insert(0, (v.astype(np.float32), u.astype(np.float32), t.astype(np.uint32),
                          None))
    if not parts:
        raise SystemExit('nothing in it could be drawn')
    at = np.array([mm[:3, 3] for ms in by_mesh.values() for mm in ms])
    anchor, _bounds = _frame(at, focus)
    wanted = {m['texture'] for m in meshes.values() if m['texture']}
    meta = {'anchor': anchor, 'parts': parts, 'triangles': int(drawn), 'removed': removed,
            'sent': int(sum(p['tris'] for p in parts)),
            'textured': int(sum(p['tris'] * max(1, p['instances']) for p in parts
                                if p['texture'] >= 0)),
            'unread': sorted(k for k in wanted if tex_index.get(k, -1) < 0),
            'textures': [{'key': k, 'bytes': len(png)} for k, png in pngs]}
    return meta, arrays, [png for _k, png in pngs]


def pack_textured(meta, arrays, pngs):
    head = json.dumps(meta, separators=(',', ':')).encode('utf-8')
    head += b' ' * (-len(head) % 4)
    out = [b'RLTX', struct.pack('<II', TEXTURED_VERSION, len(head)), head]
    for verts, uvs, tris, mats in arrays:
        out += [verts.tobytes(), uvs.tobytes(), tris.tobytes()]
        if mats is not None:
            out.append(mats.tobytes())
    for png in pngs:
        out.append(png + b'\0' * (-len(png) % 4))
    return b''.join(out)


def _cached(name, make):
    path = os.path.join(cache_root(), name)
    with _BUILD_LOCKS_LOCK:
        lock = _BUILD_LOCKS.setdefault(path, threading.Lock())
    with lock:
        if os.path.isfile(path):
            with open(path, 'rb') as fh:
                return fh.read()
        blob = make()
        _keep(path, blob)
        _drop_stale()
        return blob


def textured(game, name):
    """The packed Textured display of one source, built once and cached."""
    if game not in GAME_IDS:
        raise SystemExit('no such game: %r' % (game,))

    def make():
        models, insts, focus, texture = _read(game, name)
        meta, arrays, pngs = build_textured(models, insts, focus, texture)
        meta.update({'game': game, 'name': name})
        return pack_textured(meta, arrays, pngs)
    return _cached('tx%d_%s_%s.bin' % (TEXTURED_VERSION, game, _safe(name)), make)


# -- trap grids ---------------------------------------------------------------------
#
# The source level's trap grids, as faces in the ghost's own frame (before `anchor`):
# [{surface: Floor | Wall | Ceiling, origin, along, up: [x, y, z], n, m}], a corner, the
# face's two edges and the cell count along each. OMD3: the kit Blueprints' grids
# (leveldata.kit_grids) on each placed actor; OMD2 (and OMD1 where its layers hold
# any): the scene's trap placement volumes (trapgrid.boxes), mirrored as the geometry
# is. Unchained and Deathtrap: none read yet.

def _omd3_grids(name):
    import numpy as np
    from riftlord.formats.omd3 import omd3level
    from riftlord.mapeditor import leveldata
    lvl = leveldata.level(name)
    actors = {}
    for p in lvl.get('pieces') or []:
        if p.get('source') != 'blueprint' or p.get('export') is None or not p.get('actor'):
            continue
        actors.setdefault((p.get('sub') or 'persistent', p['export']), []).append(p)
    out = []
    for kin in actors.values():
        cls = kin[0]['actor']
        grids = leveldata.kit_grids(cls)
        if not grids:
            continue
        parts = leveldata.kit_parts(cls)['parts']
        once = [q for q in kin if sum(1 for pt in parts if pt['mesh'] == q['mesh']) == 1]
        piece = (once or kin)[0]
        part = next((pt for pt in parts if pt['mesh'] == piece['mesh']), None)
        if part is None:
            continue
        local = np.asarray(omd3level._matrix(part['loc'], part['rot'], part['scale']),
                           np.float64)
        actor = np.asarray(piece['matrix'], np.float64).reshape(4, 4).T @ np.linalg.inv(local)
        for g in grids:
            if not g.get('axes') or not g.get('extent') or not g.get('origin'):
                continue
            ex, ey, ez = g['extent']
            ax, ay, az = (np.asarray(a, np.float64) for a in g['axes'])
            centre = np.asarray(g['origin'], np.float64) + ax * (ex + GRID_LIFT)
            corner = centre - ay * ey - az * ez
            pts = [corner, corner + ay * 2 * ey, corner + az * 2 * ez]
            w = [actor[:3, :3] @ q + actor[:3, 3] for q in pts]
            out.append({'surface': g['surface'], 'origin': w[0].tolist(),
                        'along': (w[1] - w[0]).tolist(), 'up': (w[2] - w[0]).tolist(),
                        'n': 2 * ey / OMD3_CELL, 'm': 2 * ez / OMD3_CELL})
    return out


def _vision_grids(volumes):
    from riftlord.build import trapgrid
    from riftlord.core import transform
    out = []
    for v in volumes:
        v['position'] = list(transform.mirror_point(v['position']))
        v['orientation'] = list(transform.mirror_orientation(v['orientation'] or [0.0, 0.0, 0.0]))
        for b in trapgrid.boxes(v):
            n, u, w = b['basis']
            _thin, hu, hw = b['half']
            c = [b['centre'][k] + n[k] * GRID_LIFT for k in range(3)]
            origin = [c[k] - u[k] * hu - w[k] * hw for k in range(3)]
            out.append({'surface': b['surface'] or 'Floor', 'origin': origin,
                        'along': [u[k] * 2 * hu for k in range(3)],
                        'up': [w[k] * 2 * hw for k in range(3)],
                        'n': b['cells'][0], 'm': b['cells'][1]})
    return out


def _omd1_volumes(name):
    from riftlord.formats.omd2 import trapgrids
    from riftlord.formats.omd2.nrbf import parse
    from riftlord.formats.omd1 import trace_omd1
    fs = trace_omd1.files()
    out = []
    for layer in trace_omd1._layers(name):
        try:
            graph = parse(fs.read(layer))
        except Exception:                            # noqa: BLE001
            continue
        out.extend(trapgrids.volumes_in(graph, os.path.basename(layer)))
    return out


def grids(game, name):
    """{faces, reason} of one source's trap grids (section comment), cached."""
    if game not in GAME_IDS:
        raise SystemExit('no such game: %r' % (game,))

    def make():
        if game == 'omd3':
            faces = _omd3_grids(name)
        elif game == 'omd2':
            from riftlord.formats.omd2 import trapgrids
            if name not in omd2_scenes():
                raise SystemExit('no such OMD2 scene: %r' % (name,))
            faces = _vision_grids(trapgrids.extract(omd2_zip(), name)['volumes'])
        elif game == 'omd1':
            #: OMD1's scene layers hold no trap placement volumes
            volumes = _omd1_volumes(name)
            faces = _vision_grids(volumes) if volumes else None
        else:
            faces = None
        return json.dumps({'faces': faces or [],
                           'reason': '' if faces is not None else 'not read for this game'
                           }).encode('utf-8')
    return json.loads(_cached('grids%d_%s_%s.json' % (GRIDS_VERSION, game, _safe(name)), make))


# -- the checker ----------------------------------------------------------------------
#
# `python3 -m riftlord.trace check GAME [NAME ...]` reads sources the way the page gets
# them (no cache) and counts what each holds and what it loses: placements, meshes,
# triangles, material sections and textures, plus a comparison with an independent
# reading of the same level where there is one (OMD1/OMD2: the editor's Layers and
# prefabs against the shipping zones; OMD3: the Map Editor's own pieces). `flags` names
# every gap. Textures: why each unread one failed (format, mip table, .tfc, LZO, short
# data, decode), and the small, flat and flat mid-grey ones read (a mid grey is flagged:
# an AO map or mask taken for the base colour). Coplanar copies (drop_covered): removed
# from the baked part, left in it (flagged), and among every placement.
# `check --textures` with no game is the gate's step (TEXTURE_CHECKS).

#: Share of drawn triangles that must carry a read texture before the checker flags it.
TEXTURED_SHARE = 0.9
#: Placements of one mesh closer than this (uu, translation and matrix) are duplicates.
DUPLICATE_UU = 1.0
#: Position match tolerance (uu) between two readings of one level.
MATCH_UU = 5.0
#: Texture report: a texture read this small (longest side, px) is counted; one whose
#: channels vary less than FLAT_SD is flat; a flat one within GREY_SPREAD of neutral and
#: GREY_RANGE of GREY_MID is a mid grey -- the Unchained castle kits' AO maps taken for
#: base colours measured 128..146 grey at std dev 0.4..3.9.
SMALL_TEXTURE = 64
FLAT_SD = 4.0
GREY_SPREAD = 6.0
GREY_MID = 128.0
GREY_RANGE = 32.0
#: Most triangles _copies tests across every placement.
COPIES_CHECK_MAX = 4000000
#: What `check --textures` reads with no game named: a level of each game whose texture
#: and copy rules a regression would show in, skipped when the game is not installed.
TEXTURE_CHECKS = (('omdu', 'PvE_TheWall'), ('omd2', 'TheCrunch'))


def _match(a, b):
    """(matched, only in a, only in b) of two [(key, 4x4)] by key and position."""
    import numpy as np
    pa, pb = {}, {}
    for k, m in a:
        pa.setdefault(k, []).append(np.asarray(m)[:3, 3])
    for k, m in b:
        pb.setdefault(k, []).append(np.asarray(m)[:3, 3])
    matched = 0
    for k in set(pa) & set(pb):
        x, y = np.asarray(pa[k]), np.asarray(pb[k])
        used = np.zeros(len(y), bool)
        for p in x:
            d = np.abs(y - p).max(axis=1)
            d[used] = np.inf
            i = int(d.argmin())
            if d[i] < MATCH_UU:
                used[i] = True
                matched += 1
    return matched, len(a) - matched, len(b) - matched


def _compare(game, name, insts):
    """{label: value} of an independent reading of the same level, or {}."""
    import numpy as np
    if game in ('omd1', 'omd2'):
        from riftlord.formats.vision import trace_vision
        if game == 'omd2':
            from riftlord.formats.omd2 import omd2prefab
            files = _omd2_files()
            editor = trace_vision.editor_instances(
                files, name, trace_vision.layers(files, name), omd2prefab.Library(omd2_zip()))
        else:
            from riftlord.formats.omd1 import trace_omd1
            editor = trace_omd1.instances(name)
        editor = [(k, mirror_y(np.asarray(m, np.float64))) for k, m in editor]
        both, zones_only, editor_only = _match(insts, editor)
        return {'editor layers+prefabs': len(editor), 'matched': both,
                'drawn by the game, not in the editor files': zones_only,
                'in the editor files, not drawn by the game': editor_only}
    if game == 'omd3':
        from riftlord.mapeditor import leveldata
        lvl = leveldata.level(name)
        sky = (lvl.get('sky') or {}).get('mesh')
        drawn = [p for p in lvl.get('pieces') or []
                 if not p.get('hidden') and p.get('matrix') and p['mesh'] != sky]
        return {'editor pieces drawn': len(drawn),
                'of them foliage (left out)': sum(1 for p in drawn if p.get('foliage'))}
    return {}


def check(game, name, textures=True):
    """{counts, flags} for one source (section comment)."""
    import numpy as np
    models, insts, focus, texture = _read(game, name)
    out = {'game': game, 'name': name, 'placements': len(insts)}
    flags = []
    bad = [k for k, m in insts if not np.isfinite(m).all() or abs(np.linalg.det(m[:3, :3])) < 1e-9]
    out['degenerate placements'] = len(bad)
    if bad:
        flags.append('%d placements have a degenerate or non-finite transform' % len(bad))
    usable = {k for k, m in models.items() if _usable(m)}
    per = {}
    for k, _m in insts:
        per[k] = per.get(k, 0) + 1
    lost = {k: n for k, n in per.items() if k not in usable}
    if game in ('omd1', 'omd2'):
        # a mesh the zones place but no game file holds: the game cannot draw it either
        from riftlord.formats.omd1 import trace_omd1
        files = _omd2_files() if game == 'omd2' else trace_omd1.files()
        absent = {k for k in lost if not files.has(k)}
        out['meshes absent from the game files'] = len(absent)
        lost = {k: n for k, n in lost.items() if k not in absent}
    out['meshes'] = len(per)
    out['meshes unread'] = len(lost)
    out['placements unread'] = int(sum(lost.values()))
    if lost:
        top = sorted(lost.items(), key=lambda kv: -kv[1])[:8]
        flags.append('%d meshes not read (%d placements): %s' % (
            len(lost), sum(lost.values()), ', '.join('%s x%d' % kv for kv in top)))
    tris = sum(len(models[k]['tris']) * n for k, n in per.items() if k in usable)
    out['triangles'] = int(tris)
    out['repeated placements (drawn once)'] = len(insts) - len(unique(insts))
    secs = [(k, s) for k in usable for s in models[k]['sections']]
    out['sections'] = len(secs)
    keys = sorted({s['texture'] for _k, s in secs if s.get('texture')})
    out['textures named'] = len(keys)
    readable = set()
    if textures:
        weight = {}
        for k, s in secs:
            if s.get('texture'):
                weight[s['texture']] = weight.get(s['texture'], 0) + s['tris'] * per[k]
        why, small, flat, grey = {}, [], [], []
        for t in keys:
            try:
                png = texture(t)
            except Exception as exc:                 # noqa: BLE001
                png, why[t] = None, 'decode failed (%s: %s)' % (type(exc).__name__, exc)
            if not png:
                if t not in why:
                    explain = getattr(texture, 'reason', None)
                    why[t] = explain(t) if explain else 'reader gave nothing'
                continue
            readable.add(t)
            size, sd, mean = _texture_stats(png)
            if max(size) < SMALL_TEXTURE:
                small.append(t)
            if sd < FLAT_SD:
                flat.append(t)
                if max(mean) - min(mean) < GREY_SPREAD and abs(sum(mean) / 3.0 - GREY_MID) <= GREY_RANGE:
                    grey.append(t)
        out['textures read'] = len(readable)
        out['textures smaller than %dpx' % SMALL_TEXTURE] = len(small)
        out['textures flat (std dev < %g)' % FLAT_SD] = len(flat)
        out['flat mid-grey textures'] = len(grey)
        if why:
            kinds = {}
            for t, w in why.items():
                kinds.setdefault(w.split(' (')[0].split(': ', 1)[-1], []).append(t)
            out['textures not read, by reason'] = {k: len(v) for k, v in sorted(kinds.items())}
            flags.append('%d of %d textures not read: %s' % (
                len(why), len(keys), '; '.join('%s (%s)' % (t, why[t]) for t in sorted(why)[:6])))
        if grey:
            top = sorted(grey, key=lambda t: -weight.get(t, 0))[:6]
            flags.append('%d textures are a flat mid grey, an AO map or mask taken for the base '
                         'colour: %s' % (len(grey), ', '.join('%s (%d tris)' % (t, weight.get(t, 0))
                                                              for t in top)))
    else:
        readable = set(keys)
    textured = sum(s['tris'] * per[k] for k, s in secs
                   if s.get('texture') in readable and models[k]['uvs'] is not None)
    out['textured share'] = round(textured / float(tris or 1), 4)
    if out['textured share'] < TEXTURED_SHARE:
        bare = {}
        for k, s in secs:
            if not (s.get('texture') in readable and models[k]['uvs'] is not None):
                bare[k] = bare.get(k, 0) + s['tris'] * per[k]
        top = sorted(bare.items(), key=lambda kv: -kv[1])[:5]
        flags.append('only %.0f%% of triangles textured; most untextured: %s' % (
            100 * out['textured share'], ', '.join('%s (%d)' % kv for kv in top)))
    out.update(_copies(models, insts, flags))
    out.update(_compare(game, name, insts))
    out['flags'] = flags
    return out


def _texture_stats(png):
    """((width, height), mean channel std dev, [mean R, G, B]) of a PNG."""
    import io
    import numpy as np
    from PIL import Image
    with Image.open(io.BytesIO(png)) as im:
        a = np.asarray(im.convert('RGB'), np.float64).reshape(-1, 3)
        return im.size, float(a.std(axis=0).mean()), a.mean(axis=0).tolist()


def _copies(models, insts, flags):
    """{label: count} of coplanar copies (drop_covered): left out of the baked part, any
    left in it after that (flagged), and those among every placement (the instanced
    parts keep theirs; the page's stencil draws each pixel once)."""
    import numpy as np
    placed = [(i, k, m) for i, (k, m) in enumerate(unique(insts))
              if k in models and _usable(models[k])]
    count = {}
    for _i, k, _m in placed:
        count[k] = count.get(k, 0) + 1
    tris, owners, baked = [], [], []
    for i, k, m in placed:
        t = models[k]['tris']
        w = (models[k]['verts'] @ m[:3, :3].T + m[:3, 3]).astype(np.float32)[t]
        tris.append(w)
        owners.append(np.full(len(t), i, np.int64))
        baked.append(np.full(len(t), count[k] == 1 or len(t) * count[k] <= BAKE_TRIS))
    if not tris:
        return {}
    tris, owners, baked = np.concatenate(tris), np.concatenate(owners), np.concatenate(baked)
    out = {}
    keep = drop_covered(tris[baked], owners[baked])
    out['coplanar copies removed from the baked part'] = int((~keep).sum())
    left = drop_covered(tris[baked][keep], owners[baked][keep])
    out['coplanar copies left in the baked part'] = int((~left).sum())
    if (~left).any():
        flags.append('%d coplanar copies left in the baked part' % int((~left).sum()))
    if len(owners) <= COPIES_CHECK_MAX:
        every = drop_covered(tris, owners)
        out['coplanar copies among every placement'] = int((~every).sum())
    else:
        out['coplanar copies among every placement'] = 'not counted (%d triangles)' % len(owners)
    return out


def _check_main(argv):
    """python3 -m riftlord.mapeditor.trace check [GAME [NAME ...] | --game G --level L ...]
    [--all] [--textures | --no-textures] [--json OUT]

    `--textures` with no game reads TEXTURE_CHECKS (a game not installed is skipped)."""
    import argparse
    ap = argparse.ArgumentParser(prog='riftlord.mapeditor.trace check')
    ap.add_argument('game', nargs='?', choices=GAME_IDS)
    ap.add_argument('names', nargs='*')
    ap.add_argument('--game', dest='game_opt', choices=GAME_IDS)
    ap.add_argument('--level', action='append', default=[], help='a level of --game (repeatable)')
    ap.add_argument('--all', action='store_true', help="every level the game offers")
    ap.add_argument('--textures', action='store_true',
                    help='the texture report; with no game, the levels in TEXTURE_CHECKS')
    ap.add_argument('--no-textures', action='store_true', help='skip decoding textures')
    ap.add_argument('--json')
    a = ap.parse_args(argv)
    game = a.game or a.game_opt
    if game:
        names = [lv['name'] for lv in _levels(game)] if a.all else a.names + a.level
        if not names:
            ap.error('give level names or --all')
        todo = [(game, n) for n in names]
    elif a.textures:
        listed = {g['id']: {lv['name'] for lv in g['levels']} for g in sources()['games']}
        todo = []
        for g, n in TEXTURE_CHECKS:
            if n in listed.get(g, ()):
                todo.append((g, n))
            else:
                print('%s %s: not installed, skipped' % (g, n))
    else:
        ap.error('give a game, or --textures')
    results = []
    for g, n in todo:
        try:
            r = check(g, n, textures=not a.no_textures)
        except (Exception, SystemExit) as exc:       # noqa: BLE001
            r = {'game': g, 'name': n, 'flags': ['could not be read: %s' % exc]}
        results.append(r)
        print('%s %s' % (g, n), flush=True)
        for k, v in r.items():
            if k not in ('game', 'name', 'flags'):
                print('   %-46s %s' % (k, v))
        for f in r['flags']:
            print('   FLAG %s' % f)
    if a.json:
        with open(a.json, 'w') as fh:
            json.dump(results, fh, indent=1)
    return 1 if any(r['flags'] for r in results) else 0


def main(argv=None):
    """python3 -m riftlord.trace GAME NAME [OUT.bin] -- build one source, print its meta.
    python3 -m riftlord.trace check GAME [NAME ...] [--all] -- the checker."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ['check']:
        return _check_main(argv[1:])
    if len(argv) not in (2, 3):
        print(main.__doc__)
        return 2
    blob = geometry(argv[0], argv[1])
    if len(argv) == 3:
        with open(argv[2], 'wb') as fh:
            fh.write(blob)
    meta = unpack_meta(blob)
    meta['parts'] = len(meta['parts'])
    meta['bytes'] = len(blob)
    print(json.dumps(meta, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
