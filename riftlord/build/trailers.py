"""What a component export holds after its property stream's None: its class's own
serialization. UObject writes an int32 (no object GUID); UStaticMeshComponent adds its
LODData count (an int32 0 when it has none), so a StaticMeshComponent written with the
UObject trailer alone crashed the game at load ("Serial size mismatch: Got 340, Expected
41", Crystal Probe A, 2026-10-24). Other classes may add their own data.

A component Rift Lord writes takes the trailer shipped exports of its class carry: the
placed ones in the host level (the level copy itself, its own exports), else its own
template (a Blueprint's component template, serialized by the same class), else the
placed ones in the first shipped map that places the class. Among several, the most common
trailer of exports with the same name, else of the class.

The gate holds a lifted actor's components to the exact donor components they were lifted
from (a kit piece's StaticMeshComponents carry their own lightmap and vertex-colour LODData;
a light-map receiver's names its own MapBuildDataId instead, lightalloc),
and a written actor's components to that reference or, where the class's serialization is
known (StaticMeshComponent), to its structure.

    python3 -m riftlord.build.trailers PAK [--plan PLAN_OR_RECORD.json]
"""

import argparse
import collections
import re
import struct

#: The UObject trailer: no object GUID.
UOBJECT = b'\0' * 4
#: Placed actors whose components Rift Lord writes or lifts, by export name (apply.free_name
#: may add a letter).
OURS = re.compile(r'^(RiftLordCrystal|RiftLordKit_\d+)[A-Z]?$')

_MAPS = {}


def _class_name(pkg, i):
    from riftlord.mapeditor import kitactors as K
    c = K._meta(pkg, i, 0)
    return K._import_rec(pkg, c)[3] if c < 0 else None


def trailer_of(pkg, i):
    """The bytes after export i's (zero-based) property-stream None, or None when the
    stream does not walk."""
    from riftlord.mapeditor import kitactors as K
    data = pkg.exports[i]['data'] or b''
    try:
        props = K._walk(data, K._names(pkg))
    except (Exception, SystemExit):                  # noqa: BLE001
        return None
    at = props[-1][6] if props else 0
    names = K._names(pkg)
    if at + 8 > len(data) or names[struct.unpack_from('<i', data, at)[0]] != 'None':
        return None
    return bytes(data[at + 8:])


def placed_trailers(pkg, cls, skip=()):
    """[(export name, trailer)] of the exports of class `cls` in `pkg` whose outer is an
    export (a placed component), `skip` (zero-based indices) left out."""
    from riftlord.mapeditor import kitactors as K
    out = []
    for i in range(len(pkg.exports)):
        if i in skip or K._meta(pkg, i, 12) <= 0 or _class_name(pkg, i) != cls:
            continue
        t = trailer_of(pkg, i)
        if t is not None:
            out.append((K._export_name(pkg, i)[0], t))
    return out


def _shipped_maps():
    from riftlord.core import gamefiles
    return [p for p in gamefiles.find('/Maps/', ext='.umap')
            if not any(w in p for w in ('_Audio', '_Lighting', '_BuiltData', '/UI/'))]


def map_trailers(cls):
    """(map, [(export name, trailer)]) from the first shipped map that places class
    `cls`, or (None, [])."""
    if cls in _MAPS:
        return _MAPS[cls]
    from riftlord.core import gamefiles
    from riftlord.formats.ue.uassetio import Package
    got = (None, [])
    for path in _shipped_maps():
        try:
            pkg = Package(*gamefiles.package(path[:-len('.umap')]))
            pkg.load_payloads()
        except Exception:                            # noqa: BLE001
            continue
        found = placed_trailers(pkg, cls)
        if found:
            got = (path.rsplit('/', 1)[-1], found)
            break
    _MAPS[cls] = got
    return got


def template_trailer(pkg, i, files=None):
    """(where, [(name, trailer)]) of export i's template, when it is an export of a cooked
    package (a Blueprint's component template, serialized by the same class): read from
    `files` (the mission pak), a detected supported mod, or the game. (None, []) else."""
    from riftlord.core import gamefiles
    from riftlord.core import paksource
    from riftlord.core import supportedmods
    from riftlord.formats.ue.uassetio import Package
    from riftlord.mapeditor import kitactors as K
    t = K._meta(pkg, i, 8)
    if t >= 0:
        return None, []
    chain = []
    while t < 0:
        rec = K._import_rec(pkg, t)
        chain.append(rec[3])
        t = rec[2]
    path = chain[-1]
    if not path.startswith('/Game/'):
        return None, []
    entry = 'OMD/Content/' + path[len('/Game/'):]
    try:
        if files and entry + '.uasset' in files:
            blobs = (files[entry + '.uasset'], files.get(entry + '.uexp', b''))
        elif supportedmods.holder(entry + '.uasset'):
            held = supportedmods.holder(entry + '.uasset')
            blobs = (paksource.read(held, entry + '.uasset'), paksource.read(held, entry + '.uexp'))
        else:
            blobs = gamefiles.package(path)
        tp = Package(*blobs)
        tp.load_payloads()
    except Exception:                                # noqa: BLE001
        return None, []
    want = chain[0]
    parent = chain[1] if len(chain) > 2 else None
    for j in range(len(tp.exports)):
        if K._export_name(tp, j)[0] != want:
            continue
        outer = K._meta(tp, j, 12)
        if parent and (outer <= 0 or K._export_name(tp, outer - 1)[0] != parent):
            continue
        got = trailer_of(tp, j)
        if got is not None:
            return 'template %s.%s' % (path.rsplit('/', 1)[-1], want), [(want, got)]
    return None, []


def reference(pkg, i, cls, skip=(), files=None):
    """(where, [(name, trailer)]) export i (zero-based, class `cls`) is held to: the
    class's placed exports in the host level copy `pkg` (without `skip`), else its own
    template's export, else the first shipped map placing the class."""
    found = placed_trailers(pkg, cls, skip)
    if found:
        return 'the host level', found
    where, found = template_trailer(pkg, i, files)
    if found:
        return where, found
    return map_trailers(cls)


def choose(found, name):
    """The most common trailer among `found` exports named `name`, else among all."""
    same = [t for n, t in found if n == name]
    pool = same or [t for _n, t in found]
    return collections.Counter(pool).most_common(1)[0][0] if pool else None


def set_trailer(builder, index, cls, name, skip=()):
    """Give written export `index` (package index) the trailer `reference` finds for its
    class. Returns the trailer; SystemExit when there is nothing to take it from."""
    _where, found = reference(builder.pkg, index - 1, cls, skip)
    tail = choose(found, name)
    if tail is None:
        raise SystemExit('no shipped placed %s to take its serialization from' % cls)
    data = bytearray(builder.pkg.exports[index - 1]['data'])
    at = builder._terminator(data)
    if at is None:
        raise SystemExit('export %d has no None terminator' % index)
    data = bytes(data[:at + 8]) + tail
    builder.pkg.exports[index - 1]['data'] = data
    builder.pkg.exports[index - 1]['size'] = len(data)
    return tail


#: A cooked FStaticMeshComponentLODInfo with no vertex colours: strip flags (2) + the
#: map build data Guid (16) (shipped Scenario_02 DoorMesh / kit pieces, 2026-10-24).
SMC_LOD_MIN = 18


def structure_ok(cls, tail):
    """Whether `tail` is a well-formed trailer of class `cls` where its serialization is
    known: StaticMeshComponent = the UObject int32 (0), its LODData count, and at least
    SMC_LOD_MIN bytes per entry (exactly 8 bytes with none). None where not known."""
    if cls != 'StaticMeshComponent':
        return None
    if tail is None or len(tail) < 8 or tail[:4] != UOBJECT:
        return False
    count = struct.unpack_from('<i', tail, 4)[0]
    if count < 0:
        return False
    return len(tail) == 8 if count == 0 else len(tail) >= 8 + SMC_LOD_MIN * count


def ours(level):
    """{zero-based export index: (actor index, actor name, (export name, number), class)}
    of the components of Rift Lord's written and lifted actors (OURS) in a built level
    (levelbuild.Builder)."""
    from riftlord.build import levelbuild
    from riftlord.mapeditor import kitactors as K
    pkg = level.pkg
    actors = {a for a in level.actor_list() if a > 0
              and OURS.match(levelbuild._name_of(pkg, a - 1))}
    out = {}
    for i in range(len(pkg.exports)):
        outer = K._meta(pkg, i, 12)
        if outer in actors:
            out[i] = (outer, levelbuild._name_of(pkg, outer - 1), K._export_name(pkg, i),
                      _class_name(pkg, i))
    return out


def written_class(cls):
    """Whether an actor of class `cls` is written from its class (the pickups) rather than
    lifted from a donor placement (kit pieces, chests, the Crystal)."""
    from riftlord.mapeditor import chandelier
    from riftlord.mapeditor import kitactors
    return kitactors.handles(cls) and cls not in (kitactors.CHEST, chandelier.CLASS)


def donor_of(actor_name, actor_cls, plan, cache):
    """(package, zero-based actor export) a lifted actor came from, or None: its plan item's
    own donor (controllers, compounds), else its class's catalogue donor; a chest's is the
    cauldron and the Crystal's the trap-base child they are retargeted from."""
    from riftlord.build import apply as applier
    from riftlord.mapeditor import crystal
    from riftlord.mapeditor import kitactors
    key = (actor_name, actor_cls)
    if key in cache:
        return cache[key]
    got = None
    try:
        from riftlord.mapeditor import chandelier
        if actor_cls == kitactors.CHEST:
            got = applier.donor_for(kitactors.CHEST_DONOR)
        elif actor_cls == chandelier.CLASS:
            got = applier.donor_for(chandelier.DONOR)
        elif actor_cls == crystal.CLASS:
            got = crystal.donor()[1]
        else:
            m = re.match(r'^RiftLordKit_(\d+)', actor_name)
            added = (plan or {}).get('added') or []
            item = added[int(m.group(1))] if m and int(m.group(1)) < len(added) else None
            if item:
                from riftlord.mapeditor import compounds
                from riftlord.mapeditor import controllers
                got = controllers.donor(item) or compounds.donor(item)
            got = got or applier.donor_for(actor_cls)
    except (Exception, SystemExit):                  # noqa: BLE001
        got = None
    cache[key] = got
    return got


def donor_trailer(donor, name):
    """The trailer of the donor actor's component (name, number), or None."""
    from riftlord.mapeditor import kitactors as K
    pkg, j = donor
    for k in range(len(pkg.exports)):
        if K._meta(pkg, k, 12) == j + 1 and K._export_name(pkg, k) == tuple(name):
            return trailer_of(pkg, k)
    return None


def _lod_ids(trailer):
    """(the trailer with every LODData MapBuildDataId zeroed and an empty LODData given
    one zeroed entry, [ids]) of a StaticMeshComponent trailer of ids alone, or None."""
    from riftlord.build.lighting import lightalloc
    if trailer is None or len(trailer) < 8:
        return None
    at = 4 + (16 if struct.unpack_from('<i', trailer, 0)[0] else 0)
    if at + 4 > len(trailer):
        return None
    count = struct.unpack_from('<i', trailer, at)[0]
    size = lightalloc.LOD_INFO_SIZE
    if count < 0 or at + 4 + count * size != len(trailer):
        return None
    out = bytearray(trailer)
    ids = []
    for k in range(count):
        o = at + 4 + k * size + 2
        ids.append(bytes(trailer[o:o + 16]))
        out[o:o + 16] = bytes(16)
    if count == 0:
        out[at:at + 4] = struct.pack('<i', 1)
        out += lightalloc.LOD_STRIP + bytes(16)
    return bytes(out), ids


def received(got, want, ids):
    """True when trailer `got` is the donor's `want` with its LODData naming receiver ids
    (`ids`, lightalloc.receivers) in place of the donor's."""
    a, b = _lod_ids(got), _lod_ids(want)
    return bool(a and b and ids and a[0] == b[0] and a[1] and all(i in ids for i in a[1]))


def gates(files, plan=None):
    """[(name, ok, detail)]: every component of a lifted actor ends as the donor component
    it was lifted from; every component of a written actor ends as `reference` finds for
    its class, or is well-formed where its class's serialization is known. A lifted
    component whose donor cannot be found is held to the written rule."""
    from riftlord.mapeditor import controllers
    from riftlord.mapeditor import kitactors as K
    effects = preview_gates(files)
    _name, level = K._level_of(files)
    if level is None:
        return effects
    mine = ours(level)
    if not mine:
        return effects
    bad, lifted, written, cache = [], 0, 0, {}
    from riftlord.build.lighting import lightalloc
    ids = {bytes.fromhex(r['guid']) for r in lightalloc.receivers(plan)}
    for i, (actor, aname, name, cls) in sorted(mine.items()):
        if not cls:
            continue
        t = trailer_of(level.pkg, i)
        label = '%s.%s (%s)' % (aname, name[0] if not name[1] else '%s_%d' % (name[0], name[1] - 1),
                                cls)
        acls = controllers._class_name(level.pkg, actor) or ''
        if not written_class(acls):
            donor = donor_of(aname, acls, plan, cache)
            want = donor_trailer(donor, name) if donor else None
            if want is not None:
                lifted += 1
                if t != want and not received(t, want, ids):
                    bad.append('%s: %d trailer bytes %s, its donor %d bytes %s'
                               % (label, len(t or b''), (t or b'').hex() or '-', len(want),
                                  want.hex()))
                continue
        written += 1
        where, found = reference(level.pkg, i, cls, set(mine), files)
        if t in {f for _n, f in found} or structure_ok(cls, t):
            continue
        if not found:
            bad.append('%s: no shipped placed %s to compare' % (label, cls))
        else:
            want = choose(found, name[0])
            bad.append('%s: %d trailer bytes %s, shipped (%s) %d bytes %s'
                       % (label, len(t or b''), (t or b'').hex() or '-', where, len(want),
                          want.hex()))
    return [('components end as their class serializes (%d lifted = donor, %d written)'
             % (lifted, written), not bad, '; '.join(bad[:3]))] + effects


def preview_gates(files):
    """[(name, ok, detail)]: every effect component template and SCS_Node a hub preview in
    `files` carries (makepreview.add_effect) ends as the shipped diorama effect it was
    copied from (makepreview.EFFECT_DONOR); [] when no preview carries one."""
    from riftlord.formats.ue.uassetio import Package
    from riftlord.missions import modminions
    from riftlord.onslaught import makepreview
    src, s_psc, s_node = makepreview.effect_reference()
    want = {'template': trailer_of(src, s_psc), 'node': trailer_of(src, s_node)}
    bad, count = [], 0
    for k in sorted(files):
        if not k.endswith('.uasset') or k[:-7] + '.uexp' not in files:
            continue
        head = modminions._bytes(files[k])
        if makepreview.EFFECT_VAR.encode() not in head:
            continue
        pkg = Package(head, modminions._bytes(files[k[:-7] + '.uexp']))
        for psc, node, _parent in makepreview.effect_exports(pkg):
            for what, i in (('template', psc), ('node', node)):
                count += 1
                got = trailer_of(pkg, i) if i is not None else None
                if got != want[what]:
                    bad.append('%s %s %s: trailer %s, shipped %s'
                               % (k.rsplit('/', 1)[-1][:-7], what,
                                  makepreview._export_form(pkg, i)[0] if i is not None else '-',
                                  (got or b'').hex() or '-', want[what].hex()))
    if not count:
        return []
    return [('preview effect components end as the shipped diorama effect (%d)' % count,
             not bad, '; '.join(bad[:3]))]


def main(argv=None):
    import json
    from riftlord.core import paksource
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('pak')
    ap.add_argument('--plan', help='the mission plan, or a Missions record holding it (.json)')
    args = ap.parse_args(argv)
    plan = None
    if args.plan:
        with open(args.plan, encoding='utf-8') as fh:
            got = json.load(fh)
        plan = got.get('plan') if isinstance(got.get('plan'), dict) else got
    rows = gates(paksource.entries(args.pak), plan)
    for name, ok, detail in rows:
        print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, (': ' + detail) if detail else ''))
    return 0 if all(r[1] for r in rows) else 1


if __name__ == '__main__':
    raise SystemExit(main())
