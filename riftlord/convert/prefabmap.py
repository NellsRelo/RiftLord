"""Classify an OMD2 level's prefabs, because a prefab is the unit worth converting.

    python3 prefabmap.py --scene TheCrunch
    python3 prefabmap.py --selftest

**60% of TheCrunch's 8,541 mesh instances come out of 17 prefabs.** A mesh instance
carries the prefab that placed it (`omd2prefab.instances_for` now also records WHICH
placement, so the parts of one archway are separable from the next), and that changes
what a converter should decide. Matching `wallarch_blocks` to an OMD3 mesh 626 times
answers the wrong question; the reviewer said an OMD3 archway is one or two meshes
rather than an assembly of blocks, and `2WideArch.prefab` -- 20 copies of 22 meshes --
is that assembly, named by its author.

Two kinds of prefab come out of the measurement, and they want opposite treatment:

* an **assembly** is placed many times and holds few meshes: 2WideArch 20x22,
  WallSupport 32x5, WallCandle1 57x1, StairsRailingleft 6x7, 4WideArch 2x34. One OMD3
  mesh (or a small group) replaces the whole copy, so the level needs one art decision
  per prefab instead of thousands per mesh.
* a **room** is placed once or twice and holds hundreds: CenterMainRoom 1x2082,
  CastleEntranceHall 1x640, Tower2x3 6x98. There is no OMD3 mesh for a room. Its
  contents go through the per-mesh run pipeline (`artgroup` then `meshpick`), which is
  where floors and walls tile and merge well.

`ASSEMBLY_MAX` separates them on meshes per copy. The gap in the data is wide -- 34 for
the biggest assembly against 98 for the smallest room -- so the threshold is not
delicate.

OMD2 ships assemblies in families of its own (`2widearch`, `2widearch2deep`,
`3widearch`, `4widearch`, `2widearchwindow`), which is the same shape of problem as an
OMD3 family: the family is the appearance and the number is the size.
"""

import argparse
import collections
import json
import os
import re
import sys
from riftlord.core import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))

#: Meshes per copy above which a prefab is a room rather than an assembly. Measured on
#: TheCrunch: the largest assembly holds 34 and the smallest room 98.
ASSEMBLY_MAX = 50

#: Copies below which a prefab is treated as bespoke even when it is small -- a one-off
#: is not a kit piece, and swapping it for a stock OMD3 mesh loses what made it one-off.
ASSEMBLY_MIN_COPIES = 2

#: What a prefab's own name says it is. OMD2's authors named these well, and the name is
#: better evidence than the mesh list: `StatueBaseTrappable` announces a trap surface
#: and `PortalFrame` announces the rift.
ROLE_BY_NAME = (
    ('arch', ('arch',)),
    ('railing', ('railing',)),
    ('stairs', ('stair',)),
    ('column', ('column', 'pillar', 'support')),
    ('light', ('candle', 'torch', 'lantern', 'brazier', 'light')),
    ('window', ('window',)),
    ('hazard', ('lava', 'acid', 'tar', 'spike')),
    ('rift', ('portal',)),
    ('spawn', ('spawn',)),
    ('statue', ('statue',)),
    ('door', ('door', 'gate', 'portcullis')),
)

#: OMD3 catalogue prefixes to offer for each role. A list, because the reviewer's answer
#: for archways was "entirely contextual -- any of these could work", so the picker is
#: given the whole family and matches on shape and size.
OMD3_FOR_ROLE = {
    'arch': ('Archway', 'Arch_'),
    'railing': ('Railing',),
    'stairs': ('RailingStairs', 'Stairs', 'Steps'),
    'column': ('Column', 'ColumnTrim', 'Pillar'),
    'light': ('Candle', 'Torch', 'Brazier', 'Lantern'),
    'window': ('Window',),
    'statue': ('Statue',),
    'door': ('Portcullis', 'Door', 'Gate'),
}

#: Roles that are gameplay actors in OMD3 rather than art, so a mesh swap is the wrong
#: answer: the game supplies its own rift, spawner and hazard volumes.
ACTOR_ROLES = frozenset(('rift', 'spawn', 'hazard'))

_NUM = re.compile(r'(\d+)\s*wide', re.I)


def role_of(prefab):
    """The role a prefab's own file name declares, or None."""
    low = os.path.basename(str(prefab)).replace('\\', '/').lower()
    for role, words in ROLE_BY_NAME:
        if any(w in low for w in words):
            return role
    return None


def declared_width(prefab):
    """The width in OMD2 tiles a name like `4WideArch` states, or None."""
    m = _NUM.search(os.path.basename(str(prefab)))
    return int(m.group(1)) if m else None


def survey(scene, zip_path=None, models=None):
    """[{prefab, copies, meshes, per_copy, kind, role, width, footprint, parts}]."""
    from riftlord.formats.omd2 import trapgrids
    from riftlord.formats.omd2 import omd2mesh
    insts = omd2mesh.scene_meshes(scene, zip_path or trapgrids.DEFAULT_DATA_ZIP)
    groups = collections.defaultdict(list)
    for inst in insts:
        if inst.get('copy'):
            groups[inst['copy'].split('#')[0]].append(inst)
    out = []
    for name, group in groups.items():
        copies = len({i['copy'] for i in group})
        per = len(group) / float(copies)
        kind = ('assembly' if per <= ASSEMBLY_MAX and copies >= ASSEMBLY_MIN_COPIES
                else 'room')
        out.append({
            'prefab': name,
            'copies': copies,
            'meshes': len(group),
            'per_copy': round(per, 1),
            'kind': kind,
            'role': role_of(name),
            'width': declared_width(name),
            'footprint': copy_footprint(group, models),
            'parts': collections.Counter(
                i['mesh'].replace('\\', '/').rsplit('/', 1)[-1] for i in group),
        })
    out.sort(key=lambda r: (-r['meshes'],))
    return out


def copy_footprint(group, models=None):
    """One copy's size in uu, from the instances of whichever copy is most complete."""
    if models is None:
        with open(gamepath.data('omd2-models.json')) as fh:
            models = json.load(fh)['models']
    best = collections.Counter(i['copy'] for i in group).most_common(1)
    if not best:
        return None
    pick = [i for i in group if i['copy'] == best[0][0]]
    lo = [float('inf')] * 3
    hi = [float('-inf')] * 3
    for inst in pick:
        model = models.get(inst['mesh'].replace('\\', '/').lower())
        if not model or not model.get('verts'):
            continue
        vs = model['verts']
        half = [(max(v[a] for v in vs) - min(v[a] for v in vs)) / 2.0 for a in (0, 1, 2)]
        pos = inst['position']
        #: an axis-aligned reach is enough for a footprint: the copies are compared for
        #: size against OMD3 members, not rasterised
        for a in (0, 1, 2):
            lo[a] = min(lo[a], pos[a] - half[a])
            hi[a] = max(hi[a], pos[a] + half[a])
    if lo[0] == float('inf'):
        return None
    return [round(hi[a] - lo[a], 1) for a in (0, 1, 2)]


def candidates(role, cat=None, limit=12):
    """OMD3 catalogue entries worth offering for a role, as (name, cells)."""
    from riftlord.formats.omd3 import meshcat
    cat = cat or meshcat.load()
    out = []
    for prefix in OMD3_FOR_ROLE.get(role) or ():
        for name, info in cat.items():
            if name.lower().startswith(prefix.lower()) and info.get('cells'):
                out.append((name, tuple(info['cells'])))
    out.sort(key=lambda t: t[0])
    return out[:limit]


def selftest():
    bad = []
    if role_of('prefabs/act2/2WideArch.prefab') != 'arch':
        bad.append('role_of missed an arch')
    if role_of('prefabs/act2/StairsRailingleft.prefab') != 'railing':
        bad.append('a stairs railing should read as a railing before stairs')
    if role_of('prefabs/act2/Lights/WallCandle1.prefab') != 'light':
        bad.append('role_of missed a candle in a subfolder')
    if role_of('prefabs/act2/CenterMainRoom.prefab') is not None:
        bad.append('a room should declare no role')
    if declared_width('4WideArch.prefab') != 4:
        bad.append('declared_width missed 4WideArch')
    if declared_width('2widearch2deep.prefab') != 2:
        bad.append('declared_width missed a lowercase name')
    if declared_width('CenterMainRoom.prefab') is not None:
        bad.append('declared_width invented a width')
    #: the threshold must put TheCrunch's measured extremes on the right sides
    if not (34 <= ASSEMBLY_MAX < 98):
        bad.append('ASSEMBLY_MAX no longer separates 4WideArch (34) from Tower2x3 (98)')
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--scene', default='TheCrunch')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--candidates', action='store_true')
    args = ap.parse_args(argv)
    if args.selftest:
        bad = selftest()
        for b in bad:
            print('FAILED  %s' % b)
        print('prefabmap selftest: %s'
              % ('passed' if not bad else '%d problem(s)' % len(bad)))
        return 1 if bad else 0
    rows = survey(args.scene)
    tot = sum(r['meshes'] for r in rows)
    asm = [r for r in rows if r['kind'] == 'assembly']
    print('%s: %d prefab(s) placing %d mesh instance(s)' % (args.scene, len(rows), tot))
    print('  %d assembly prefab(s) cover %d instance(s) -- %d art decision(s)'
          % (len(asm), sum(r['meshes'] for r in asm), len(asm)))
    print()
    print('%-30s %-9s %6s %6s %8s %-9s %s'
          % ('prefab', 'kind', 'copies', 'parts', 'per copy', 'role', 'footprint (uu)'))
    for r in rows:
        print('%-30s %-9s %6d %6d %8.1f %-9s %s'
              % (os.path.basename(r['prefab']), r['kind'], r['copies'], r['meshes'],
                 r['per_copy'], r['role'] or '-', r['footprint']))
    if args.candidates:
        from riftlord.formats.omd3 import meshcat
        cat = meshcat.load()
        print()
        for r in asm:
            if not r['role'] or r['role'] in ACTOR_ROLES:
                note = 'OMD3 gameplay actor, not art' if r['role'] else 'role unknown'
                print('%-30s %s' % (os.path.basename(r['prefab']), note))
                continue
            cands = candidates(r['role'], cat)
            print('%-30s %s -> %d candidate(s): %s'
                  % (os.path.basename(r['prefab']), r['role'], len(cands),
                     ', '.join(n for n, _ in cands[:6])))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
