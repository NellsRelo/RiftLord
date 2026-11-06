"""Compound mining: groups of placed pieces that recur at fixed relative transforms across the
shipped levels, curated into riftlord/data/compounds.json (read by compounds.py).

    python3 -m riftlord.mapeditor.compoundmine --write     # rebuild compounds.json
    python3 -m riftlord.mapeditor.compoundmine --check     # it matches the levels (gate)
    python3 -m riftlord.mapeditor.compoundmine --null      # real vs role-shuffled counts
    python3 -m riftlord.mapeditor.compoundmine --sheet DIR # contact sheets of the uncurated

Steps:
1. scan: every static mesh, Blueprint actor (by its root) and effect emitter of each shipped
   level and its sublevels; foliage, hidden parts, landscape and splines left out. Cached per
   level, keyed by the base paks' size and time and SCAN_VERSION.
2. contacts: pairs whose world boxes lie within GAP of each other, each as the other's
   transform in the first one's frame.
3. signatures: per anchor key and member key, those transforms clustered within POS_TOL /
   ROT_TOL; a signature on fewer than MIN_COPIES anchor placements is dropped.
4. candidates: closed sets of signatures over the anchor's placements (intersections of
   what each placement carries) with at least MIN_COPIES carriers.
5. suppression: the lattice rule (LATTICE_ROLES), then candidates in score order (copies x
   members) claim the units of their copies; a copy whose units are mostly claimed already
   (CLAIMED) does not count; a candidate needs NEED[pieces] copies left (else MIN_COPIES).
6. definitions: the frame is the anchor's origin turned by its yaw; the anchor's own
   rotation-and-scale in it is the commonest over its copies; the members are those of the
   copy nearest the others (with that anchor turn), as shipped. Filed by art set (ART_SETS)
   and matched to the curation (riftlord/data/compound-curation.json: keep with a label and
   a group, or a reason), which was decided by eye from contact sheets of each candidate
   beside the shipped spot it was cut from.
   The Blueprint beam walls (BEAM_WALL: Wall_RB_WD*, Wall_RB_WDMT*, Trim_Column_WDMT*) also
   give every candidate on MIN_COPIES of their placements, without the null-model counts.

7. dressed walls: wood (beams, planks, joints, brackets, scaffolding) sits against walls
   anywhere along the face, at a few depths and stretched to length, so few exact repeats
   exist. Every wood piece goes to the wall it touches nearest; a wall's dressing layout is,
   per piece, its key, its turn, its depth band (DRESS_DEPTH off the face) and its height
   band (thirds of the wall), ignoring where along the face it sits and its length. Walls of
   one mesh with one layout are copies; the compound is the copy nearest the others (its
   wood as shipped). Wood touching two or more walls is one unit with all of them (frames over
   a doorway, scaffolding across a corner): copies share the walls' keys and every piece's
   turn. Copies needed: DRESS_NEED.
8. runs (compoundfamilies.runs) and dressed walls (compoundwalls: walls with wood or iron
   dressing pooled by look, each one real wall as shipped, curated under family 'dressed
   wall') join the definitions.
9. provenance: each kept definition's default layout is looked for on every level
   (occurrences: each piece a unit of its name within POS_TOL and ROT_TOL of where the
   anchor puts it); `source` is the nearest occurrence (level, sublevel and actor name of
   each piece, the anchor's world transform), `copies` and `levels` count them all. One
   that occurs nowhere is dropped. The hand-made compounds get the same under `hand`, with
   `seen`: the stack mids and chain links found on the levels.

Thresholds (measured 2026-10-28 over the 33 shipped levels, 87.6k units):
- POS_TOL: nearest-copy distance of every contact relation: a mode under 0.5 uu, the
  density falling to its floor at 3-5 uu (3.5k per uu, the level of the background).
- ROT_TOL: 97.6% of nearest copies agree within 0.0005 per entry, flat beyond 0.01.
- NEED: keys shuffled among one level's units of the same kind and role (the null model:
  lattice and grid kept, which piece is where broken), three seeds. The fewest copies at
  which the null finds at most ~10% as many candidates as the levels: 2 pieces 12 (8%),
  3 pieces 5 (8%), 4-5 pieces 3 (7%), 6+ pieces 3 (1%). Overall 761 vs 68-72 (9%).
- Dressed walls: 1,247 walls carry wood in 1,107 distinct layouts; wood sits on a 25 uu
  grid along the face for only 22% of pieces (8% by chance), depth repeats within 3 uu for
  25 of 166 wall-and-wood pairs, and a beam's length scale varies. With wood keys shuffled
  among one level's wood (three seeds): layouts of 2+ pieces on 3+ walls 9 real vs 0, of
  one piece on 6+ walls 8 real vs 0-1.
"""

import argparse
import collections
import glob
import hashlib
import json
import math
import os
import re
import sys
import time

import numpy as np

from riftlord.core import gamepath

DATA = gamepath.data('compounds.json')
CURATION = gamepath.data('compound-curation.json')
SCAN_VERSION = 2
#: contact: world boxes within this of each other, uu
GAP = 20.0
POS_TOL = 3.0
ROT_TOL = 0.01
MIN_COPIES = 3
#: copies a candidate needs by its piece count (anchor included)
NEED = {2: 12, 3: 5}
#: a copy counts when fewer than this share of its units are claimed already
CLAIMED = 0.5
#: closed sets kept per anchor key before it is given up
CLOSED_CAP = 4000
#: an effect has no bounds: a cube this half-size
POINT_HALF = 25.0
#: a curated entry matches a candidate when every member is within this of its own, uu
MATCH_TOL = 10.0
#: shuffled keys for --null
NULL_SEEDS = (1, 2, 3)
#: candidates with a piece of one of these roles are left out
SKIP_ROLES = {'env', 'terrain', 'foliage'}
#: plan doors are gameplay objects (the spawn system): never part of a mined compound
DOOR = re.compile(r'^BP_Door_')

# -- roles (by name, then by the mesh catalogue's category) -----------------------------
ROLE_RULES = [
    ('env', r'Skybox|SkySphere|^Plane$|^Cube1?$|^Cylinder$|^Sphere$|^Cone$|Surface|Aurora|'
            r'Planet|WaterPlane|WaterFall|Water_|LavaFall|AcidFall|Lightbeam|Shape_'),
    ('envtrap', r'^BP_FallingBoomBarrel|^BP_Cauldron|^BP_GrinderPit|^BP_Grinders|^BP_LavaBurst'),
    ('door', r'^BP_Door_|^ArchwayGate|Portcullis|^BP_Exterior_Portcullis|Drawbridge'),
    ('lightprop', r'Torch|Brazier|Chandelier|Lantern|Candle|^Lighting_|WallCrystal|Sconce'),
    ('banner', r'Banner|Pennant|^Flag|FlagPole|Crest_|Tapestry'),
    ('railing', r'Railing|Banister|Balustr'),
    ('window', r'Window|Shutter'),
    ('stairs', r'Stair|Steps|Ramp'),
    ('arch', r'Archway|^Arch_|ArchPillar'),
    ('column', r'Column|Pillar|Post(?!Base)'),
    ('cren', r'Cren|Merlon|TowerTop|Battlement'),
    ('roof', r'Roof|Awning|Turret'),
    ('ceiling', r'Ceiling'),
    ('floor', r'Floor|Platform|Landing|Tile_|Ground|Bridge'),
    ('wall', r'Wall|Castle_|Tower'),
    ('trim', r'Trim|Corbel|Molding|Moulding|Bracket|Band|Plate|Rivet|Join'),
    ('wood', r'Wood|Beam|Plank|Joint'),
    ('metal', r'Metal|Chain|Pipe|Grate|Bars'),
    ('statue', r'Statue|Monolith|Throne|Glyph'),
    ('prop', r'Barrel|Crate|Sack|Box|Cart|Table|Chair|Bench|Shelf|Book|Bottle|Pot|Jar|'
             r'Basket|Chest|Bucket|Anvil|Weapon|Rack|Shield|Spear|Sword|Tent|Boat|Anchor|'
             r'Bone|Skull|Paint|Decor|Cauldron|Hay|Rope|Wheel|Bed|Rug|Carpet|Scroll|'
             r'Candelabra|Food|Keg|Pile|Debris|Rubble|Coin|Gold'),
    ('rock', r'Rock|Cliff|Boulder|Stone(?!.*Wall)|Stalag|Crystal|Ice|Mountain|Acid|Lava'),
    ('foliage', r'Tree|Bush|Grass|Fern|Ivy|Vine|Plant|Leaf|Leaves|Branch|Flower|Moss|'
                r'Shrub|Mushroom|Weed|Palm|Cactus|Root|Hedge|Foliage|Reed|Log|Stump'),
]
ROLE_RX = [(r, re.compile(p, re.I)) for r, p in ROLE_RULES]
CATEGORY_ROLE = {'foliage': 'foliage', 'terrain': 'rock', 'decoration': 'prop',
                 'effect': 'fx', 'trap': 'trap', 'rift': 'rift'}
#: a rock mesh longer than this on its longest side is terrain, not scatter, uu
TERRAIN_SIZE = 1500.0
#: the lattice rule: every piece of one of these roles, each turned by quarter turns
LATTICE_ROLES = {'wall', 'floor', 'ceiling', 'column', 'cren', 'stairs', 'arch', 'roof',
                 'door', 'window', 'railing', 'kit', 'castle'}
#: art set by piece name, then by the levels every copy is on; else ART_DEFAULT
ART_NAMES = (('Sand', re.compile(r'_sand', re.I)), ('Ice', re.compile(r'_ice|ice_|snow', re.I)))
ART_LEVELS = (('Sand', 'Scenario_Sand'), ('Ice', 'Scenario_Ice'))
ART_DEFAULT = 'Castle'


def _meshes():
    with open(gamepath.data('omd3-meshes.json')) as fh:
        return json.load(fh)['meshes']


_ROLE = {}


def role(key, kind, meshes):
    k = (key, kind)
    if k not in _ROLE:
        _ROLE[k] = _role(key, kind, meshes)
    return _ROLE[k]


def _role(key, kind, meshes):
    if kind == 'fx':
        return 'fx'
    r = None
    for name, rx in ROLE_RX:
        if rx.search(key):
            r = name
            break
    if r is None:
        cat = (meshes.get(key) or {}).get('category')
        r = CATEGORY_ROLE.get(cat) or ('kit' if kind == 'bp' else
                                       'castle' if cat == 'castle' else 'other')
    if r == 'rock' and max((meshes.get(key) or {}).get('size') or [0]) > TERRAIN_SIZE:
        return 'terrain'
    return r


# -- 1. scan ---------------------------------------------------------------------------

def shipped_levels():
    from riftlord.build.navigation import navpatch
    return sorted(k for k in navpatch.maps(include_mods=False) if k.startswith('Scenario'))


def _pak_key():
    from riftlord.build.navigation import navpatch
    rows = []
    for p in sorted(glob.glob(os.path.join(navpatch.PAK_DIR or '', '*.pak'))):
        st = os.stat(p)
        rows.append([os.path.basename(p), st.st_size, int(st.st_mtime)])
    return hashlib.sha1(json.dumps([SCAN_VERSION, rows]).encode()).hexdigest()[:16]


def cache_dir():
    return gamepath.scratch('.compoundmine', gamepath.CODE_ROOT)


def _m12(m):
    return [round(m[r][c], 3) for r in range(3) for c in range(4)]


def scan_level(level):
    """[[key, kind ('mesh'|'bp'|'fx'), sublevel, m12, actor]] of one shipped level (m12: the
    row-major 3x4 world matrix; a Blueprint by its actor root; actor: the placing actor's
    name, `<name>#<n>` for the n-th instance of an instanced component)."""
    from riftlord.formats.omd3 import omd3actors, omd3level

    def rows(flat):
        return [[flat[c * 4 + r] for c in range(4)] for r in range(4)]
    parts, meshes = omd3actors.part_index(), omd3actors.mesh_index()
    units = []
    for pkg in omd3level.sublevels(level) + omd3level.extra_sublevels(level):
        sub = pkg.rsplit('/', 1)[-1]
        try:
            pieces, _lights, vfx, _ = omd3level.read_package(pkg, parts, meshes, keep_local=True)
            exports = omd3level._load(pkg)[0]['exports']
        except (Exception, SystemExit):              # noqa: BLE001
            continue

        def name_of(i):
            return str(exports[i - 1]['name']) if 0 < i <= len(exports) else '?'
        seen = set()
        instance = collections.Counter()
        for p in pieces:
            if p.get('hidden') or p.get('foliage') or p.get('source') in ('landscape', 'spline'):
                continue
            if p.get('source') == 'blueprint':
                if p['export'] in seen:
                    continue
                seen.add(p['export'])
                try:
                    root = omd3level._matmul(rows(p['matrix']),
                                             omd3level._inverse(rows(p['local'])))
                except (ValueError, KeyError):
                    continue
                units.append([p['actor'], 'bp', sub, _m12(root), name_of(p['export'])])
            else:
                e = p['export']
                actor = name_of(exports[e - 1].get('outer') or 0 if 0 < e <= len(exports) else 0)
                n = instance[e]
                instance[e] += 1
                if n or p.get('instanced'):
                    actor = '%s#%d' % (actor, n)
                units.append([p['mesh'], 'mesh', sub, _m12(rows(p['matrix'])), actor])
        for f in vfx:
            if f.get('actorClass') in ('Emitter', 'NiagaraActor'):
                x, y, z = f['loc']
                units.append(['FX:' + str(f['template']), 'fx', sub,
                              [1, 0, 0, x, 0, 1, 0, y, 0, 0, 1, z], f.get('actorName') or '?'])
    return units


def scan(levels=None, log=lambda _s: None):
    """{level: units} of the shipped levels, from the cache when the paks are unchanged."""
    key = _pak_key()
    folder = cache_dir()
    out = {}
    for lv in levels or shipped_levels():
        path = os.path.join(folder, '%s.%s.json' % (lv, key))
        if os.path.exists(path):
            with open(path) as fh:
                out[lv] = json.load(fh)
            continue
        t = time.time()
        out[lv] = scan_level(lv)
        for old in glob.glob(os.path.join(folder, '%s.*.json' % lv)):
            os.remove(old)
        with open(path + '.tmp', 'w') as fh:
            json.dump(out[lv], fh)
        os.replace(path + '.tmp', path)
        log('scanned %s: %d units, %.1fs' % (lv, len(out[lv]), time.time() - t))
    return out


# -- 2-4. mining -----------------------------------------------------------------------

class Catalogue:
    """What New Object can place, and each piece's local bounds."""

    def __init__(self):
        from riftlord.mapeditor import leveldata
        rows = leveldata.catalogue()
        self.kinds = {}
        for r in rows:
            self.kinds.setdefault(r['name'], r['kind'])
        self.meshes = _meshes()
        with open(gamepath.data('kit-donor-parts.json')) as fh:
            self.parts = json.load(fh)['parts']
        self._box = {}

    def placeable(self, key, kind):
        if kind == 'bp':
            return self.kinds.get(key) == 'kit'
        if kind == 'fx':
            return self.kinds.get(key[3:]) == 'fx'
        return self.kinds.get(key) == 'mesh'

    def box(self, key, kind):
        k = (key, kind)
        if k in self._box:
            return self._box[k]
        from riftlord.formats.omd3 import omd3level
        box = None
        if kind == 'mesh' and key in self.meshes:
            b = self.meshes[key]
            o, e = np.array(b['origin'], float), np.array(b['extent'], float)
            box = (o - e, o + e)
        elif kind == 'bp':
            lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
            for p in self.parts.get(key) or []:
                b = self.meshes.get(p.get('mesh'))
                if not b:
                    continue
                M = np.array(omd3level._matrix(p.get('loc') or (0, 0, 0),
                                               p.get('rot') or (0, 0, 0),
                                               p.get('scale') or (1, 1, 1)))
                o, e = np.array(b['origin'], float), np.array(b['extent'], float)
                c = M[:3, :3] @ o + M[:3, 3]
                w = np.abs(M[:3, :3]) @ e
                lo, hi = np.minimum(lo, c - w), np.maximum(hi, c + w)
            if np.isfinite(lo).all():
                box = (lo, hi)
        if box is None:
            box = (np.full(3, -POINT_HALF), np.full(3, POINT_HALF))
        self._box[k] = box
        return box

    def world_box(self, key, kind, M):
        lo, hi = self.box(key, kind)
        c, e = (lo + hi) / 2, (hi - lo) / 2
        wc = M[:3, :3] @ c + M[:3, 3]
        we = np.abs(M[:3, :3]) @ e
        return np.concatenate([wc - we, wc + we])


def _m4(mm):
    M = np.eye(4)
    M[:3, :] = np.array(mm, float).reshape(3, 4)
    return M


def placed_units(scanned, cat, seed=None):
    """{level: [(key, kind, 4x4, sublevel, actor)]} of the units New Object can place; with
    `seed`, keys shuffled among one level's units of the same kind and role (the null model)."""
    out = {}
    for lv in sorted(scanned):
        us = [(k, kd, _m4(mm), sub, actor) for k, kd, sub, mm, actor in scanned[lv]
              if cat.placeable(k, kd)]
        if seed is not None:
            rng = np.random.default_rng(seed)
            groups = collections.defaultdict(list)
            for i, u in enumerate(us):
                groups[(u[1], role(u[0], u[1], cat.meshes))].append(i)
            for idx in groups.values():
                keys = [us[i][0] for i in idx]
                rng.shuffle(keys)
                for i, k in zip(idx, keys):
                    us[i] = (k,) + us[i][1:]
        out[lv] = us
    return out


def contacts(units, cat):
    """{level: (I, J, V)}: unit J's transform in unit I's frame as V (12: translation, then
    the rotation-and-scale rows; an effect's rotation zeroed), for boxes within GAP."""
    out = {}
    for lv, us in units.items():
        if not us:
            out[lv] = (np.zeros(0, int), np.zeros(0, int), np.zeros((0, 12)))
            continue
        boxes = np.array([cat.world_box(u[0], u[1], u[2]) for u in us])
        lo, hi = boxes[:, :3], boxes[:, 3:]
        mats = np.array([u[2] for u in us])
        fx = np.array([u[1] == 'fx' for u in us])
        I, J, V = [], [], []
        for i in range(len(us)):
            near = np.all((lo <= hi[i] + GAP) & (hi >= lo[i] - GAP), axis=1)
            near[i] = False
            js = np.nonzero(near)[0]
            if not len(js):
                continue
            rel = np.linalg.inv(mats[i]) @ mats[js]
            v = np.concatenate([rel[:, :3, 3], rel[:, :3, :3].reshape(-1, 9)], axis=1)
            v[fx[js], 3:] = 0.0
            I.append(np.full(len(js), i))
            J.append(js)
            V.append(v)
        out[lv] = (np.concatenate(I), np.concatenate(J), np.concatenate(V)) if I else \
            (np.zeros(0, int), np.zeros(0, int), np.zeros((0, 12)))
    return out


def _cluster(vecs):
    """Leader clustering within POS_TOL / ROT_TOL: (cluster index per row, centres)."""
    buckets = collections.defaultdict(list)
    centres = []
    out = np.empty(len(vecs), int)
    cells = np.floor(vecs[:, :3] / POS_TOL).astype(int)
    for n, v in enumerate(vecs):
        b = cells[n]
        found = -1
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for c in buckets.get((b[0] + dx, b[1] + dy, b[2] + dz), ()):
                        cv = centres[c]
                        if np.abs(cv[:3] - v[:3]).max() <= POS_TOL and \
                                np.abs(cv[3:] - v[3:]).max() <= ROT_TOL:
                            found = c
                            break
                    if found >= 0:
                        break
                if found >= 0:
                    break
            if found >= 0:
                break
        if found < 0:
            found = len(centres)
            centres.append(v)
            buckets[tuple(b)].append(found)
        out[n] = found
    return out, centres


def candidates(units, near):
    """Raw candidates: {'anchor': (key, kind), 'members': [(key, kind, vec)], 'emb':
    [(level, [anchor unit, member units...])]} for every closed signature set on at least
    MIN_COPIES of its anchor's placements."""
    by_anchor = collections.defaultdict(list)
    for lv in sorted(units):
        for i, u in enumerate(units[lv]):
            if u[1] != 'fx':
                by_anchor[(u[0], u[1])].append((lv, i))
    #: contact rows by (level, anchor unit)
    rows_of = {}
    for lv, (I, J, V) in near.items():
        if not len(I):
            continue
        order = np.argsort(I, kind='stable')
        I, J, V = I[order], J[order], V[order]
        starts = np.searchsorted(I, np.arange(len(units[lv]) + 1))
        rows_of[lv] = (J, V, starts)
    out = []
    for (ka, kda) in sorted(by_anchor):
        places = by_anchor[(ka, kda)]
        if len(places) < MIN_COPIES:
            continue
        per_member = collections.defaultdict(list)
        for lv, i in places:
            if lv not in rows_of:
                continue
            J, V, starts = rows_of[lv]
            for r in range(starts[i], starts[i + 1]):
                u = units[lv][J[r]]
                if (u[0], u[1]) != (ka, kda):
                    per_member[(u[0], u[1])].append((lv, i, int(J[r]), V[r]))
        tx = collections.defaultdict(dict)
        centre = {}
        for mk in sorted(per_member):
            items = per_member[mk]
            if len(items) < MIN_COPIES:
                continue
            idx, centres = _cluster(np.array([v for *_t, v in items]))
            cnt = collections.Counter(idx.tolist())
            for (lv, i, j, _v), c in zip(items, idx.tolist()):
                if cnt[c] >= MIN_COPIES:
                    s = (mk[0], mk[1], c)
                    tx[(lv, i)].setdefault(s, j)
                    centre[s] = centres[c]
        if not tx:
            continue
        sup = collections.Counter(s for t in tx.values() for s in t)
        keep = {s for s, n in sup.items() if n >= MIN_COPIES}
        trans = [(p, frozenset(s for s in t if s in keep)) for p, t in sorted(tx.items())]
        trans = [(p, t) for p, t in trans if t]
        closed = set()
        for _p, t in trans:
            closed |= {t} | {c & t for c in closed if c & t}
            if len(closed) > CLOSED_CAP:
                break
        if len(closed) > CLOSED_CAP:
            continue
        for c in sorted(closed, key=lambda s: sorted(s)):
            carriers = [p for p, t in trans if c <= t]
            if len(carriers) < MIN_COPIES:
                continue
            sigs = sorted(c)
            out.append({'anchor': (ka, kda),
                        'members': [(s[0], s[1], centre[s]) for s in sigs],
                        'emb': [(lv, [i] + [tx[(lv, i)][s] for s in sigs])
                                for lv, i in carriers]})
    return out


# -- 5. suppression --------------------------------------------------------------------

def lattice(c, meshes):
    roles = {role(*c['anchor'], meshes)} | {role(k, kd, meshes) for k, kd, _v in c['members']}
    if len(roles) != 1 or not roles <= LATTICE_ROLES:
        return False
    for _k, _kd, v in c['members']:
        R = np.array(v[3:], float).reshape(3, 3)
        s = np.linalg.norm(R, axis=0)
        U = R / np.where(s > 0, s, 1)
        if np.abs(np.abs(U).max(0) - 1).max() > 0.02:
            return False
    return True


def _sig(c):
    return (c['anchor'], tuple((k, kd, tuple(np.round(v, 2).tolist())) for k, kd, v in c['members']))


def suppress(cands, cat, strict=True):
    """The candidates left after the role filter, the lattice rule (strict) and the claim of
    units in score order, each with its free copies. Of candidates covering the same units
    from different anchors, the one anchored on the biggest piece is kept."""
    meshes = cat.meshes

    def volume(key):
        lo, hi = cat.box(*key)
        return float(np.prod(np.maximum(hi - lo, 1.0)))

    def rank(c):
        return (len(c['members']), round(volume(c['anchor']), 1), _sig(c))
    same = {}
    for c in cands:
        k = frozenset((lv, tuple(ids)) for lv, ids in c['emb'])
        if k not in same or rank(c) > rank(same[k]):
            same[k] = c
    pool = []
    for c in same.values():
        roles = [role(*c['anchor'], meshes)] + [role(k, kd, meshes) for k, kd, _v in c['members']]
        if SKIP_ROLES & set(roles):
            continue
        if strict and lattice(c, meshes):
            continue
        pool.append(c)
    pool.sort(key=lambda c: (-len(c['emb']) * len(c['members']), _sig(c)))
    claimed = set()
    kept = []
    for c in pool:
        size = 1 + len(c['members'])
        need = NEED.get(size, MIN_COPIES) if strict else MIN_COPIES
        free = [(lv, ids) for lv, ids in c['emb']
                if sum((lv, i) in claimed for i in ids) < CLAIMED * len(ids)]
        if len(free) < need:
            continue
        for lv, ids in free:
            claimed.update((lv, i) for i in ids)
        kept.append(dict(c, emb=free))
    return kept


# -- 6. definitions --------------------------------------------------------------------

def _frame(A):
    yaw = math.atan2(A[1, 0], A[0, 0])
    F = np.eye(4)
    F[:2, :2] = [[math.cos(yaw), -math.sin(yaw)], [math.sin(yaw), math.cos(yaw)]]
    F[:3, 3] = A[:3, 3]
    return F


KIND = {'bp': 'kit', 'mesh': 'mesh', 'fx': 'fx'}


def art_set(names, levels):
    """The art set of pieces `names` copied on `levels`: by a piece's name, then by the levels
    every copy is on, else ART_DEFAULT."""
    return next((s for s, rx in ART_NAMES if any(rx.search(n) for n in names)), None) or \
        next((s for s, prefix in ART_LEVELS if all(lv.startswith(prefix) for lv in levels)),
             ART_DEFAULT)


def define(c, units):
    """A candidate as a compound definition (no label yet)."""
    ka, kda = c['anchor']
    turns = []
    for lv, ids in c['emb']:
        A = units[lv][ids[0]][2]
        turns.append(np.round(np.linalg.inv(_frame(A)) @ A, 3)[:3, :3] + 0.0)
    modal = collections.Counter(t.tobytes() for t in turns).most_common(1)[0][0]
    use = [e for e, t in zip(c['emb'], turns) if t.tobytes() == modal]
    rturn = next(t for t in turns if t.tobytes() == modal)
    rels = [[] for _m in c['members']]
    for lv, ids in use:
        Finv = np.linalg.inv(_frame(units[lv][ids[0]][2]))
        for k, i in enumerate(ids[1:]):
            rels[k].append(Finv @ units[lv][i][2])
    #: the copy nearest the others, as shipped
    pos = np.array([[r[:3, 3] for r in got] for got in rels])           # member, copy, xyz
    far = np.abs(pos - pos.mean(1, keepdims=True)).max(2).sum(0) if len(pos) else np.zeros(len(use))
    pick = int(np.argmin(far))
    members, spread = [], 0.0
    for (name, kind, _v), got in zip(c['members'], rels):
        a = np.array(got)
        m = a[pick].copy()
        spread = max(spread, float(a[:, :3, 3].std(0).max()))
        if kind == 'fx':
            m[:3, :3] = np.eye(3)
        members.append({'name': name[3:] if kind == 'fx' else name, 'kind': KIND[kind],
                        't': [round(float(x), 2) + 0.0 for x in m[:3, 3]],
                        'r': [[round(float(x), 4) + 0.0 for x in row] for row in m[:3, :3]]})
    per_level = collections.Counter(lv for lv, _ids in c['emb'])
    art = art_set([ka] + [m['name'] for m in members], per_level)
    return {'type': 'fixed', 'set': art,
            'anchor': {'name': ka, 'kind': KIND[kda],
                       'r': [[round(float(x), 4) + 0.0 for x in row] for row in rturn]},
            'members': members, 'copies': len(c['emb']),
            'levels': dict(sorted(per_level.items())), 'spread': round(spread, 2),
            'door': bool(DOOR.search(ka) or any(DOOR.search(m['name']) for m in members))}


# -- 6b. the Blueprint beam walls ------------------------------------------------------

#: the shipped Blueprint stone walls with wood built in (leveldata.kit_label)
BEAM_WALL = re.compile(r'^(Wall_RB_WD|Trim_Column_WDMT)')


def beam_wall_candidates(raw, kept, cat):
    """Candidates anchored on a Blueprint beam wall on MIN_COPIES+ of its placements, without
    the null-model counts and the claim (each is curated by eye): per anchor and set of
    carriers the one with the most pieces, per anchor and piece names the one with the most
    carriers, none already in `kept`."""
    taken = {_sig(c) for c in kept}
    best = {}
    for c in raw:
        if not BEAM_WALL.match(c['anchor'][0]) or _sig(c) in taken:
            continue
        if any(DOOR.search(k) for k, _kd, _v in c['members']) or \
                SKIP_ROLES & {role(k, kd, cat.meshes) for k, kd, _v in c['members']}:
            continue
        k = (c['anchor'], frozenset((lv, ids[0]) for lv, ids in c['emb']))
        if k not in best or (len(c['members']), _sig(c)) > (len(best[k]['members']), _sig(best[k])):
            best[k] = c
    out = {}
    for c in sorted(best.values(), key=lambda c: (-len(c['emb']), _sig(c))):
        k = (c['anchor'], tuple(sorted(m[0] for m in c['members'])))
        out.setdefault(k, c)
    return list(out.values())


# -- 7. dressed walls -----------------------------------------------------------------

#: wood dressing, by name (windows, doors and banners are not dressing)
WOOD = re.compile(r'wood|beam|plank|joint|bracket|support|scaffold|^post|_post|metalband', re.I)
NOT_WOOD = re.compile(r'Metal_PostBase|SupportBlock|Floor|Ceiling|Platform|Window|Shutter|'
                      r'Portcullis|Door|Banner', re.I)
#: wood touching a wall (boxes within), and wood touching wood, uu
DRESS_HOST = 20.0
DRESS_LINK = 5.0
#: depth off the face per band, uu; height bands per wall
DRESS_DEPTH = 10.0
DRESS_BANDS = 3
#: walls carrying one layout needed, by wood pieces (1, else 2+)
DRESS_NEED = {1: 6}
DRESS_MIN = 3
#: the most walls and wood pieces one multi-wall unit may have
DRESS_WALLS_MAX = 4
DRESS_WOOD_MAX = 12


def _turn_of(M):
    R = M[:3, :3] / np.linalg.norm(M[:3, :3], axis=0)
    return tuple(int(v) for v in np.round(R[:, 0])) + tuple(int(v) for v in np.round(R[:, 2]))


def _medoid(vecs):
    a = np.array(vecs)
    return int(np.argmin(np.abs(a[:, None, :] - a[None, :, :]).sum((1, 2))))


def wall_test(cat):
    """Whether a unit (key, kind) is a wall that wood dresses."""
    return lambda k, kd: role(k, kd, cat.meshes) in ('wall', 'castle') and not WOOD.search(k)


def is_wood(k, kd):
    return kd != 'fx' and bool(WOOD.search(k)) and not NOT_WOOD.search(k)


def dressed(units, cat):
    """Pseudo-candidates of walls with their wood dressing: {'anchor', 'members', 'emb': the
    chosen copy, 'copies', 'levels'}."""
    is_wall = wall_test(cat)

    def vol(k, kd):
        lo, hi = cat.box(k, kd)
        return float(np.prod(np.maximum(hi - lo, 1.0)))
    one = collections.defaultdict(list)
    multi = collections.defaultdict(list)
    for lv in sorted(units):
        us = units[lv]
        woods = [i for i, u in enumerate(us) if is_wood(u[0], u[1])]
        walls = [i for i, u in enumerate(us) if is_wall(u[0], u[1])]
        if not woods or not walls:
            continue
        box = {i: cat.world_box(us[i][0], us[i][1], us[i][2]) for i in woods + walls}
        WL = np.array([box[i] for i in walls])
        touching = {}
        hosted = collections.defaultdict(list)
        for j in woods:
            b = box[j]
            t = np.nonzero(np.all((WL[:, :3] <= b[3:] + DRESS_HOST) &
                                  (WL[:, 3:] >= b[:3] - DRESS_HOST), axis=1))[0]
            touching[j] = [walls[n] for n in t]
            best = None
            for i in touching[j]:
                p = (np.linalg.inv(us[i][2]) @ us[j][2])[:3, 3]
                lo, hi = cat.box(us[i][0], us[i][1])
                dist = float(np.maximum(np.maximum(lo - p, p - hi), 0).max())
                if best is None or (dist, i) < best:
                    best = (dist, i)
            if best is not None:
                hosted[best[1]].append(j)
        for i, js in sorted(hosted.items()):
            W = us[i][2]
            lo, hi = cat.box(us[i][0], us[i][1])
            lay, pos = [], []
            for j in js:
                r = np.linalg.inv(W) @ us[j][2]
                band = int(min(DRESS_BANDS - 1, max(0, (r[2, 3] - lo[2]) /
                                                    max(hi[2] - lo[2], 1.0) * DRESS_BANDS)))
                lay.append((us[j][0], _turn_of(r), int(round(r[1, 3] / DRESS_DEPTH)), band, j,
                            r[:3, 3].tolist()))
            lay.sort(key=lambda x: x[:4] + (x[5][0],))
            key = (us[i][0], tuple(x[:4] for x in lay))
            one[key].append((lv, [i] + [x[4] for x in lay], [v for x in lay for v in x[5]]))
        # wood touching two or more walls: one unit with all of them
        W2 = np.array([box[j] for j in woods])
        parent = list(range(len(woods)))

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a
        for a in range(len(woods)):
            t = np.all((W2[:, :3] <= W2[a, 3:] + DRESS_LINK) & (W2[:, 3:] >= W2[a, :3] - DRESS_LINK), axis=1)
            for b in np.nonzero(t)[0]:
                ra, rb = find(a), find(int(b))
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
        comps = collections.defaultdict(list)
        for a in range(len(woods)):
            comps[find(a)].append(woods[a])
        for ids in comps.values():
            host = sorted({w for j in ids for w in touching[j]})
            if not 2 <= len(host) <= DRESS_WALLS_MAX or len(ids) > DRESS_WOOD_MAX:
                continue
            anchor = max(ids, key=lambda j: (vol(us[j][0], us[j][1]), us[j][0], -j))
            inv = np.linalg.inv(us[anchor][2])
            parts = sorted(((us[w][0], _turn_of(inv @ us[w][2]), w) for w in host)) + \
                sorted(((us[j][0], _turn_of(inv @ us[j][2]), j) for j in ids if j != anchor))
            key = (us[anchor][0], tuple(p[:2] for p in parts))
            multi[key].append((lv, [anchor] + [p[2] for p in parts],
                               [v for p in parts for v in (inv @ us[p[2]][2])[:3, 3].tolist()]))
    out = []
    for groups, is_multi in ((one, False), (multi, True)):
        for key in sorted(groups, key=str):
            copies = groups[key]
            wood = len(key[1]) - (sum(1 for k in key[1] if not WOOD.search(k[0])) if is_multi else 0)
            if len(copies) < DRESS_NEED.get(wood, DRESS_MIN):
                continue
            lv, ids, _v = copies[_medoid([c[2] for c in copies])]
            us = units[lv]
            if is_multi:
                #: the biggest wall anchors the unit
                anchor = max((i for i in ids if not WOOD.search(us[i][0])),
                             key=lambda i: (vol(us[i][0], us[i][1]), us[i][0]))
                ids = [anchor] + [i for i in ids if i != anchor]
            out.append({'anchor': (us[ids[0]][0], us[ids[0]][1]),
                        'members': [(us[i][0], us[i][1], None) for i in ids[1:]],
                        'emb': [(lv, ids)], 'copies': len(copies),
                        'levels': dict(sorted(collections.Counter(c[0] for c in copies).items()))})
    return out


#: a half turn about z, as rotation-and-scale rows
HALF_TURN = np.diag([-1.0, -1.0, 1.0])


def face_camera(d, cat):
    """A dressed wall turned so its wood faces the preview camera (omd3thumb looks from the
    frame's -y side): a half turn when the wood's centre is on the wall's +y side."""
    a = d['anchor']
    lo, hi = cat.box(a['name'], 'bp' if a['kind'] == 'kit' else a['kind'])
    centre = np.array(a['r']) @ ((lo + hi) / 2)
    wood = [m['t'] for m in d['members'] if WOOD.search(m['name'])]
    if not wood or np.mean([t[1] for t in wood]) <= centre[1]:
        return d

    def rows(r):
        return [[round(float(x), 4) + 0.0 for x in row] for row in r]
    a = dict(a, r=rows(HALF_TURN @ np.array(a['r'])))
    members = [dict(m, t=[round(float(x), 2) + 0.0 for x in HALF_TURN @ np.array(m['t'])],
                    r=rows(HALF_TURN @ np.array(m['r']))) for m in d['members']]
    return dict(d, anchor=a, members=members)


# -- 8. provenance --------------------------------------------------------------------

#: classes no scan unit stands for: found among the level's volumes (volumes.level_volumes)
VOLUME_CLASSES = {'OMDCharacterKillVolume'}
SCAN_KIND = {'kit': 'bp', 'door': 'bp', 'mesh': 'mesh', 'fx': 'fx'}
#: the most mid tiles and chain links looked for on the shipped levels (seen evidence)
MIDS_SEARCH = 12
LINKS_SEARCH = 30


def scan_key(name, kind):
    """The (key, kind) a scan unit of a layout piece (`kind` kit, door, mesh or fx) has."""
    sk = SCAN_KIND.get(kind)
    return ('FX:' + name, 'fx') if sk == 'fx' else (name, sk)


class Index:
    """Every scanned unit of the shipped levels (scan()) by level and (key, kind)."""

    def __init__(self, scanned):
        self.scanned = scanned
        self.mats, self.by = {}, {}
        for lv, us in scanned.items():
            self.mats[lv] = np.array([_m4(u[3]) for u in us]) if us else np.zeros((0, 4, 4))
            by = collections.defaultdict(list)
            for i, u in enumerate(us):
                by[(u[0], u[1])].append(i)
            self.by[lv] = {k: np.array(v) for k, v in by.items()}
        self.keys = {k for by in self.by.values() for k in by}

    def key(self, name, kind):
        return scan_key(name, kind)

    def ids(self, lv, name, kind):
        return self.by[lv].get(self.key(name, kind), np.zeros(0, int))


def within(W, M, scale):
    """(position error, rotation error, ok) of matrix M against where W puts a piece; the
    position tolerance grows with the anchor's `scale`, the rotation one with the piece's."""
    pe = float(np.abs(M[:3, 3] - W[:3, 3]).max())
    re = float(np.abs(M[:3, :3] - W[:3, :3]).max())
    return pe, re, pe <= POS_TOL * scale and re <= ROT_TOL * max(1.0, float(np.abs(W[:3, :3]).max()))


def anchor_scale(A):
    return max(1.0, float(abs(np.linalg.det(A[:3, :3]))) ** (1 / 3))


def occurrences(lay, idx, skip=()):
    """[{'level', 'units': [unit index, or None for a piece of a class in `skip`], 'err',
    'rerr'}] of the places a layout ([(name, kind, 4x4)], anchor first, as compounds.layout)
    occurs whole: every piece a unit of its name within() where the anchor's unit puts it,
    one unit each."""
    aname, akind, alocal = lay[0]
    Ainv = np.linalg.inv(np.array(alocal, float))
    out = []
    for lv in sorted(idx.scanned):
        mats = idx.mats[lv]
        for a in idx.ids(lv, aname, akind):
            F = mats[a] @ Ainv
            scale = anchor_scale(mats[a])
            used, got, err, rerr = {int(a)}, [int(a)], 0.0, 0.0
            for name, kind, m in lay[1:]:
                if name in skip:
                    got.append(None)
                    continue
                W = F @ np.array(m, float)
                best = None
                for j in idx.ids(lv, name, kind):
                    if int(j) in used:
                        continue
                    M = mats[j].copy()
                    if kind == 'fx':
                        M[:3, :3] = W[:3, :3]
                    pe, re, ok = within(W, M, scale)
                    if ok and (best is None or (pe, re) < best[:2]):
                        best = (pe, re, int(j))
                if best is None:
                    break
                used.add(best[2])
                got.append(best[2])
                err, rerr = max(err, best[0]), max(rerr, best[1])
            else:
                out.append({'level': lv, 'units': got, 'err': err, 'rerr': rerr})
    return out


_VOLUMES = {}


def volume_at(level, cls, W):
    """The name of the persistent level's volume of class `cls` within() where W puts it,
    or None."""
    if level not in _VOLUMES:
        from riftlord.mapeditor import volumes
        _VOLUMES[level] = volumes.package_volumes(level)
    for v in _VOLUMES[level]:
        M = np.array(v['matrix'], float).reshape(4, 4).T
        if v['cls'] == cls and within(W, M, 1.0)[2]:
            return v['name']
    return None


def locate(lay, idx):
    """(source, occurrences) of a layout. Source: the occurrence nearest the layout as
    {'level', 'actors': [[sublevel, actor name] per piece, anchor first; None for a piece no
    shipped level places (a carried kit's own class)], 'anchor': the anchor's world m12};
    None when the layout occurs nowhere."""
    absent = {n for n, k, _m in lay if n not in VOLUME_CLASSES and idx.key(n, k) not in idx.keys}
    occ = occurrences(lay, idx, VOLUME_CLASSES | absent)
    occ.sort(key=lambda o: (o['err'], o['rerr'], o['level'], o['units'][0]))
    Ainv = np.linalg.inv(np.array(lay[0][2], float))
    for o in occ:
        lv, mats = o['level'], idx.mats[o['level']]
        F = mats[o['units'][0]] @ Ainv
        actors = []
        for (name, _kind, m), u in zip(lay, o['units']):
            if u is not None:
                s = idx.scanned[lv][u]
                actors.append([s[2], s[4]])
            elif name in VOLUME_CLASSES:
                v = volume_at(lv, name, F @ np.array(m, float))
                actors.append([lv, v] if v else False)
            else:
                actors.append(None)
        if False in actors:
            continue
        return {'level': lv, 'actors': actors, 'anchor': _m12(mats[o['units'][0]])}, occ
    return None, occ


def provenance(d, layout, idx):
    """`d` with `source`, and `copies` and `levels` counting where its default occurs whole;
    None when it occurs nowhere."""
    src, occ = locate(layout, idx)
    if src is None:
        return None
    return dict(d, source=src, copies=len(occ),
                levels=dict(sorted(collections.Counter(o['level'] for o in occ).items())))


def hand_made(idx, log=lambda _s: None):
    """{key: {source, copies, levels[, seen]}} of the hand-made compounds (compounds.HAND);
    `seen`: per param, {value: shipped occurrences} (stack mids, chain links)."""
    from riftlord.mapeditor import compounds
    out = {}
    for key, c in compounds.HAND.items():
        got = provenance({}, compounds.layout_of(c), idx)
        if got is None:
            log('hand-made %s: its default occurs nowhere' % key)
            continue
        if c['type'] == 'stack':
            got['seen'] = {'mids': _seen(dict(c, midsMax=MIDS_SEARCH), 'mids',
                                         range(MIDS_SEARCH + 1), idx)}
        elif c['type'] == 'chain':
            got['seen'] = {'links': _seen(c, 'links', range(1, LINKS_SEARCH + 1), idx,
                                          {'item': 'none'})}
        out[key] = got
    return out


def _seen(c, param, values, idx, extra=None):
    from riftlord.mapeditor import compounds
    out = {}
    for v in values:
        n = len(occurrences(compounds.layout_of(c, dict(extra or {}, **{param: v})), idx))
        if n:
            out[str(v)] = n
    return out


# -- curation --------------------------------------------------------------------------

def placed(d):
    """[[name, t]] of a definition's members (a pattern's at its default params)."""
    if d.get('type') == 'pattern':
        from riftlord.mapeditor import compounds
        return [[n, [round(m[0][3], 2), round(m[1][3], 2), round(m[2][3], 2)]]
                for n, _k, m in compounds.pattern_parts(d, d['params'])]
    return [[m['name'], m['t']] for m in d['members']]


def signature(d):
    """What a curated entry is matched by: the anchor and each member's name and place."""
    return {'anchor': d['anchor']['name'], 'members': sorted(placed(d))}


def matches(sig, d):
    left = placed(d)
    if sig['anchor'] != d['anchor']['name'] or len(sig['members']) != len(left):
        return False
    for name, t in sig['members']:
        hit = next((k for k, (n, u) in enumerate(left) if n == name and
                    max(abs(a - b) for a, b in zip(t, u)) <= MATCH_TOL), None)
        if hit is None:
            return False
        left.pop(hit)
    return True


def load_curation(path=CURATION):
    try:
        with open(path) as fh:
            return json.load(fh)['decisions']
    except (OSError, ValueError, KeyError):
        return []


def slug(label):
    return re.sub(r'[^a-z0-9]+', '_', label.lower()).strip('_')


def build(log=lambda _s: None, scanned=None):
    """(compounds {key: definition}, report) from the shipped levels and the curation."""
    t = time.time()
    cat = Catalogue()
    scanned = scanned or scan(log=log)
    units = placed_units(scanned, cat)
    near = contacts(units, cat)
    log('contacts %.1fs' % (time.time() - t))
    raw = candidates(units, near)
    kept = suppress(raw, cat)
    log('%d raw candidates, %d after suppression, %.1fs' % (len(raw), len(kept), time.time() - t))
    defs = [define(c, units) for c in kept]
    beams = beam_wall_candidates(raw, kept, cat)
    defs += [define(c, units) for c in beams]
    log('%d beam-wall candidates' % len(beams))
    walls = dressed(units, cat)
    for c in walls:
        defs.append(face_camera(dict(define(c, units), copies=c['copies'], levels=c['levels'],
                                     dressed=True),
                                cat))
    log('%d dressed walls, %.1fs' % (len(walls), time.time() - t))
    from riftlord.mapeditor import compoundfamilies, compounds
    defs += compoundfamilies.runs(units, cat, near, lambda k, kd: role(k, kd, cat.meshes),
                                  POS_TOL, ROT_TOL, art_set, log)
    from riftlord.mapeditor import compoundwalls
    defs += compoundwalls.definitions(units, cat, art_set, log)
    log('%.1fs' % (time.time() - t))
    decisions = load_curation()
    used = [False] * len(decisions)
    out, report = {}, {'candidates': len(defs), 'kept': 0, 'rejected': collections.Counter(),
                       'uncurated': [], 'missing': []}
    for d in defs:
        k = next((n for n, dec in enumerate(decisions) if not used[n] and
                  (dec.get('family') == compoundwalls.FAMILY) == (d.get('family') == compoundwalls.FAMILY)
                  and matches(dec['sig'], d)), None)
        if d.get('door'):
            report['rejected']['door'] += 1
            if k is not None:
                used[k] = True
            continue
        if k is None:
            report['uncurated'].append(d)
            continue
        used[k] = True
        dec = decisions[k]
        if not dec.get('keep'):
            report['rejected'][dec.get('reason', 'curated')] += 1
            continue
        key = slug(dec['label'])
        d = {'label': dec['label'], 'group': dec['group'], **{x: d[x] for x in d if x != 'door'}}
        out[key] = d
    report['missing'] = [dec for dec, u in zip(decisions, used) if not u and dec.get('keep')]
    for k in out.values():
        for m in k.get('members') or []:
            if m['kind'] == 'fx':
                from riftlord.build.world import lavafx
                m['mesh'] = lavafx.donor_mesh(m['name']) or ''
    idx = Index(scanned)
    report['nowhere'] = []
    for key in list(out):
        got = provenance(out[key], compounds.layout_of(out[key]), idx)
        if got is None:
            report['nowhere'].append(key)
            del out[key]
        else:
            out[key] = got
    report['kept'] = len(out)
    report['hand'] = hand_made(idx, log)
    log('provenance %.1fs' % (time.time() - t))
    out = dict(sorted(out.items(), key=lambda kv: (kv[1]['group'], str(kv[1]['set']), kv[1]['label'])))
    return out, report


def payload(compounds, hand):
    return {'version': 2,
            'params': {'GAP': GAP, 'POS_TOL': POS_TOL, 'ROT_TOL': ROT_TOL, 'NEED': NEED,
                       'MIN_COPIES': MIN_COPIES, 'CLAIMED': CLAIMED},
            'compounds': compounds, 'hand': hand}


def load(path=DATA):
    """{key: definition} of the mined compounds, or {}."""
    try:
        with open(path) as fh:
            return json.load(fh).get('compounds') or {}
    except (OSError, ValueError):
        return {}


# -- the null model --------------------------------------------------------------------

def null_table(log=print):
    """Real vs role-shuffled candidate counts by piece count and copies (no lattice rule,
    MIN_COPIES for all), and the strict totals."""
    cat = Catalogue()
    scanned = scan(log=log)
    runs = []
    for seed in (None,) + NULL_SEEDS:
        units = placed_units(scanned, cat, seed)
        raw = candidates(units, contacts(units, cat))
        loose = [c for c in suppress(raw, cat, strict=False) if not lattice(c, cat.meshes)]
        strict = suppress(raw, cat)
        runs.append((loose, strict))
        log('seed %s: %d loose, %d strict' % (seed, len(loose), len(strict)))
    real, nulls = runs[0], runs[1:]
    for lo, hi in ((2, 2), (3, 3), (4, 5), (6, 999)):
        row = []
        for k in (3, 4, 5, 6, 8, 10, 12):
            r = sum(1 for c in real[0] if lo <= 1 + len(c['members']) <= hi and len(c['emb']) >= k)
            n = np.mean([sum(1 for c in nl[0] if lo <= 1 + len(c['members']) <= hi
                             and len(c['emb']) >= k) for nl in nulls])
            row.append('k%d %d/%.1f/%.2f' % (k, r, n, n / max(r, 1)))
        log('pieces %d-%d: ' % (lo, min(hi, 99)) + '  '.join(row))
    log('strict: real %d, null %s' % (len(real[1]), [len(nl[1]) for nl in nulls]))


# -- contact sheets --------------------------------------------------------------------

def sheets(defs, folder, per=30, cell=220):
    """PNG contact sheets of definitions (whole assemblies, as New Object previews draw)."""
    from PIL import Image, ImageDraw
    from riftlord.mapeditor import kitdonors, omd3serve, omd3thumb
    os.makedirs(folder, exist_ok=True)
    cache = {}

    def piece(name, kind):
        if (name, kind) not in cache:
            got = kitdonors.model(name) if kind == 'kit' else (
                omd3serve.mesh(name) if kind == 'mesh' else None)
            cache[(name, kind)] = got if got and got.get('verts') and got.get('tris') else None
        return cache[(name, kind)]
    paths = []
    for s in range(0, len(defs), per):
        sheet = Image.new('RGB', (6 * cell, -(-per // 6) * (cell + 34)), (16, 18, 22))
        draw = ImageDraw.Draw(sheet)
        for n, d in enumerate(defs[s:s + per]):
            head = d['anchor']
            if d.get('type') == 'pattern':
                from riftlord.mapeditor import compounds
                head = compounds.pattern_anchor(d, d['params'])
            lay = [(head['name'], head['kind'], np.array(head['r']), np.zeros(3))]
            if d.get('type') == 'pattern':
                lay += [(n, k, np.array(m)[:3, :3], np.array(m)[:3, 3])
                        for n, k, m in compounds.pattern_parts(d, d['params'])]
            else:
                lay += [(m['name'], m['kind'], np.array(m['r']), np.array(m['t'])) for m in d['members']]
            placed = []
            for name, kind, R, t in lay:
                got = piece(name, kind)
                if got is None:
                    continue
                placed.append(omd3thumb.place(got, np.hstack([R, np.reshape(t, (3, 1))])))
            got = omd3thumb.merge(placed)
            x, y = (n % 6) * cell, (n // 6) * (cell + 34)
            if got:
                img = omd3thumb.render(got, cell)
                if img is not None:
                    sheet.paste(img, (x, y))
            draw.text((x + 3, y + cell + 1), '%s n%d L%d %s' % (
                d.get('label') or '#%d' % (s + n), d['copies'], len(d['levels']), d['set']),
                fill=(230, 230, 230))
            draw.text((x + 3, y + cell + 16), ('%s +%d' % (d['anchor']['name'],
                                                           len(lay) - 1))[:36],
                      fill=(160, 170, 190))
        path = os.path.join(folder, 'sheet_%03d.png' % (s // per))
        sheet.save(path)
        paths.append(path)
    return paths


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--write', action='store_true', help='rebuild riftlord/data/compounds.json')
    ap.add_argument('--check', action='store_true', help='compounds.json matches the levels')
    ap.add_argument('--null', action='store_true', help='real vs role-shuffled counts')
    ap.add_argument('--sheet', metavar='DIR', help='contact sheets of the uncurated candidates')
    args = ap.parse_args(argv)
    log = lambda s: print(s, flush=True)              # noqa: E731
    if args.null:
        null_table(log)
        return 0
    got, report = build(log)
    log('%d candidates: %d kept, rejected %s, %d uncurated, %d curated not found, %d occur nowhere' % (
        report['candidates'], report['kept'], dict(report['rejected']),
        len(report['uncurated']), len(report['missing']), len(report['nowhere'])))
    for dec in report['missing']:
        log('  not found: %s (%s)' % (dec['label'], dec['sig']['anchor']))
    for key in report['nowhere']:
        log('  occurs nowhere: %s' % key)
    if args.sheet:
        for p in sheets(report['uncurated'], args.sheet):
            log(p)
    if args.write:
        with open(DATA + '.tmp', 'w') as fh:
            json.dump(payload(got, report['hand']), fh, indent=1)
            fh.write('\n')
        os.replace(DATA + '.tmp', DATA)
        log('wrote %s (%d compounds, %d hand-made sources)' % (DATA, len(got), len(report['hand'])))
    if args.check:
        try:
            with open(DATA) as fh:
                have = json.load(fh)
        except (OSError, ValueError):
            have = {}
        want = json.loads(json.dumps(payload(got, report['hand'])))
        same = all(want[k] == have.get(k) for k in ('compounds', 'hand'))
        log('compounds.json %s the levels and the curation (%d)' % (
            'matches' if same else 'DOES NOT match', len(have.get('compounds') or {})))
        if not same:
            for k in sorted(set(have.get('compounds') or {}) ^ set(got)):
                log('  only in %s: %s' % ('the file' if k in (have.get('compounds') or {}) else 'the levels', k))
            return 1
        if report['missing']:
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
