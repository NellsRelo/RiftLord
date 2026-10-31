"""Read an OMD3 map's spawners and routes, and an onslaught Blueprint's waves.

A wave is declared as `Onslaught_Start(WaveNumber, SpawnerIndex)`, so editing an
onslaught is only meaningful alongside the map: SpawnerIndex is an AI spawner's
`indexInLevel`, and that spawner's `StartingDoor` and `StartingPathPoint` decide
where its enemies enter and which way they travel. This reports both sides.

    python3 onslaught.py --map Scenario_12
    python3 onslaught.py --bp BP_OMDOnslaughtCoordinator_Scenario01
    python3 onslaught.py --protos          # every AI character proto guid
"""

import argparse
import os
import struct
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.build.navigation import navpatch  # noqa: E402
from riftlord.formats.ue.uheader import parse as parse_header  # noqa: E402
from riftlord.formats.ue.parseuasset import R  # noqa: E402

from riftlord.core import gamepath  # noqa: E402

TMP = gamepath.scratch('.onslaught', gamepath.CODE_ROOT)

#: AOMDAISpawner::indexInLevel when the cooked asset omits it. See map_report().
DEFAULT_SPAWNER_INDEX = 1


def read_props(info, uexp, header_size, export):
    """Tagged properties of one export: [(name, type, raw)]."""
    out = []
    start = export['off'] - header_size
    r = R(uexp, start)
    names = info['names']
    try:
        while r.o < start + export['size']:
            ni = r.i32(); r.i32()
            nm = names[ni] if 0 <= ni < len(names) else '?'
            if nm == 'None':
                break
            ti = r.i32(); r.i32()
            tp = names[ti] if 0 <= ti < len(names) else '?'
            size = r.i32(); r.i32()
            if tp == 'BoolProperty':
                v = r.u8(); r.u8()
                out.append((nm, tp, v))
                continue
            if tp == 'StructProperty':
                r.i32(); r.i32(); r.raw(17)
            elif tp in ('ArrayProperty', 'ByteProperty', 'EnumProperty'):
                r.i32(); r.i32(); r.u8()
            else:
                r.u8()
            out.append((nm, tp, r.raw(size)))
    except Exception:
        pass
    return out


def soft_path(info, raw):
    """A SoftObjectProperty is an FName asset path then an FString sub-path."""
    try:
        idx = struct.unpack_from('<I', raw, 0)[0]
        asset = info['names'][idx] if idx < len(info['names']) else '?'
        n = struct.unpack_from('<i', raw, 8)[0]
        sub = raw[12:12 + max(n - 1, 0)].decode('ascii', 'replace')
        return asset, sub
    except Exception:
        return None, None


def as_int(raw):
    return struct.unpack('<i', raw)[0] if raw and len(raw) == 4 else None


def as_vec(raw):
    return struct.unpack('<3f', raw) if raw and len(raw) == 12 else None


def actor_location(info, uexp, hdr, export, cache):
    """An actor's world position.

    Actors do not carry RelativeLocation themselves; it lives on the component
    named by their RootComponent reference, which is another export in the same
    package.
    """
    props = dict((p[0], p) for p in read_props(info, uexp, hdr, export))
    if 'RelativeLocation' in props:
        return as_vec(props['RelativeLocation'][2])
    ref = props.get('RootComponent')
    if not ref:
        return None
    idx = as_int(ref[2])
    if not idx or idx <= 0 or idx > len(info['exports']):
        return None
    # Key the cache by export INDEX, never by name: component exports routinely
    # share a name (DefaultSceneRoot, SceneComponent), so a name-keyed cache hands
    # every actor the first one's position.
    if idx in cache:
        return cache[idx]
    comp = info['exports'][idx - 1]
    cprops = dict((p[0], p) for p in read_props(info, uexp, hdr, comp))
    loc = as_vec(cprops['RelativeLocation'][2]) if 'RelativeLocation' in cprops else None
    cache[idx] = loc
    return loc


def actor_components(info, uexp, hdr, actor_index, imports_cache={}):
    """{component name: {mesh?, loc?, rot?, scale?}} a PLACED actor overrides.

    A Blueprint's component defaults live in the class, but the level may override any
    of them per placement, and for a force-field door that is the whole point: the
    class ships a 100uu plane at scale 1, and every placed one states its own
    `RelativeScale3D` -- (10, 14, 1) on Scenario_Ice_01, so 1000x1400uu. Drawing the
    class default put a 100uu plane in a 1400uu doorway, which reads as nothing being
    drawn at all.

    Component exports are outered to their actor, so they are found by outer index --
    not by name, because `DoorMesh`, `Root` and `Collision` repeat across every door
    in the level.

    Only fields the placement actually states are returned, so anything it leaves out
    still falls through to the class.
    """
    out = {}
    key = id(info)
    imps = imports_cache.get(key)
    if imps is None:
        imps = info.get('imports') or []
        imports_cache[key] = imps

    def import_name(index):
        i = -index - 1
        if 0 <= i < len(imps):
            imp = imps[i]
            if isinstance(imp, dict):
                return imp.get('name') or imp.get('object_name')
        return None

    for e in info['exports']:
        if e.get('outer') != actor_index:
            continue
        if 'StaticMeshComponent' not in str(e.get('class') or e.get('cls') or ''):
            continue
        name = str(e.get('name') or '').replace('_GEN_VARIABLE', '')
        if not name:
            continue
        props = dict((pr[0], pr) for pr in read_props(info, uexp, hdr, e))
        rec = {}
        ref = props.get('StaticMesh')
        if ref:
            idx = as_int(ref[2])
            if idx is not None and idx < 0:
                mesh = import_name(idx)
                if mesh:
                    rec['mesh'] = mesh
        for field, k in (('RelativeLocation', 'loc'), ('RelativeRotation', 'rot'),
                         ('RelativeScale3D', 'scale')):
            if field in props:
                v = as_vec(props[field][2])
                if v:
                    rec[k] = list(v)
        if rec:
            out[name] = rec
    return out


def actor_transform(info, uexp, hdr, export, cache):
    """An actor's world location, rotation and scale.

    Same indirection as `actor_location` -- it all lives on the component named by
    `RootComponent` -- but a door needs more than a position to be drawn as the mesh
    it actually is: an unrotated portcullis sits across the archway instead of in it.

    Absent fields are returned as identity rather than None, because a component only
    stores what differs from the default and every shipped door leaves at least one
    of the three out.
    """
    props = dict((p[0], p) for p in read_props(info, uexp, hdr, export))
    here = props
    ref = props.get('RootComponent')
    if 'RelativeLocation' not in props and ref:
        idx = as_int(ref[2])
        if idx and 0 < idx <= len(info['exports']):
            key = ('xform', idx)
            if key in cache:
                return cache[key]
            comp = info['exports'][idx - 1]
            here = dict((p[0], p) for p in read_props(info, uexp, hdr, comp))
            got = _xform_of(here)
            cache[key] = got
            return got
    return _xform_of(here)


def _xform_of(props):
    loc = as_vec(props['RelativeLocation'][2]) \
        if 'RelativeLocation' in props else (0.0, 0.0, 0.0)
    rot = as_vec(props['RelativeRotation'][2]) \
        if 'RelativeRotation' in props else (0.0, 0.0, 0.0)
    scale = as_vec(props['RelativeScale3D'][2]) \
        if 'RelativeScale3D' in props else (1.0, 1.0, 1.0)
    return {'location': list(loc) if loc else [0.0, 0.0, 0.0],
            'rotation': list(rot) if rot else [0.0, 0.0, 0.0],
            'scale': list(scale) if scale else [1.0, 1.0, 1.0]}


def load_map(name):
    files = navpatch.fetch_map(navpatch.map_path(name), TMP)
    info = parse_header(files['.umap'])
    with open(files['.uexp'], 'rb') as fh:
        uexp = fh.read()
    return info, uexp, os.path.getsize(files['.umap'])


def map_report(name, loaded=None):
    """The level's spawners, points, doors and game type pieces. `loaded` is an already
    read (info, uexp, header size) to report on instead of the map `name`."""
    info, uexp, hdr = loaded or load_map(name)

    from riftlord.onslaught import gamemode
    spawners, points, doors, pieces = [], {}, {}, []
    loc_cache = {}
    #: The export INDEX is needed as well as the record: a component is found by its
    #: outer index, and names repeat across every door in a level.
    for export_index, e in enumerate(info['exports'], start=1):
        cls = str(e.get('class') or e.get('cls'))
        props = dict((p[0], p) for p in read_props(info, uexp, hdr, e))

        if 'AISpawner' in cls:
            # A cooked asset omits any property equal to its class default, so an
            # absent indexInLevel means "the default", not zero. Across all 18
            # campaign maps exactly one spawner omits it and no map ever carries an
            # explicit 1, so the default is 1 and each map's spawners are a
            # contiguous 1..N. The Blueprint dispatch ladders agree, testing 1..14
            # on Scenario_18's fourteen spawners.
            rec = {'export': e['name'], 'index': DEFAULT_SPAWNER_INDEX,
                   'cls': cls,
                   'door': None, 'start': None, 'flyer': None, 'maxRow': None,
                   'index_explicit': False}
            if 'indexInLevel' in props:
                rec['index'] = as_int(props['indexInLevel'][2])
                rec['index_explicit'] = True
            if 'maxSpawnPerRow' in props:
                rec['maxRow'] = as_int(props['maxSpawnPerRow'][2])
            for key, field in (('StartingDoor', 'door'),
                               ('StartingPathPoint', 'start'),
                               ('StartingFlyerPathPoint', 'flyer')):
                if key in props:
                    _a, sub = soft_path(info, props[key][2])
                    rec[field] = sub
            #: the units the spawner sends itself (SpawnProtodata, Min/MaxSpawnCount), as
            #: an older Crystal's rift rows wrote them: (Guid, count)
            raw = props.get('SpawnProtodata', (0, None, b''))[2]
            if isinstance(raw, (bytes, bytearray)) and len(raw) >= 49 + 16:
                count = max(as_int(props[k][2]) for k in ('MinSpawnCount', 'MaxSpawnCount')
                            if k in props) if ('MinSpawnCount' in props
                                               or 'MaxSpawnCount' in props) else 1
                rec['units'] = (struct.unpack_from('<4i', raw, 49), count)
            rec['location'] = actor_location(info, uexp, hdr, e, loc_cache)
            spawners.append(rec)

        elif cls == 'OMDPathPoint':
            rec = {'export': e['name'], 'index': None, 'start': False,
                   'exit': False, 'next': []}
            if 'PointIndex' in props:
                rec['index'] = as_int(props['PointIndex'][2])
            # The scalars an editor needs to show for a point it did not place. A
            # cooked asset omits anything equal to its class default, so absence here
            # means the default rather than zero.
            for key, field in (('ReachRadius', 'reachRadius'),
                               ('FormationSpacing', 'formationSpacing'),
                               ('FlightHeight', 'flightHeight')):
                if key in props and props[key][2] and len(props[key][2]) == 4:
                    rec[field] = round(struct.unpack('<f', props[key][2])[0], 2)
            if 'FormationSize' in props:
                rec['formationSize'] = as_int(props['FormationSize'][2])
            for key, field in (('bGroundOnly', 'groundOnly'),
                               ('bFlyersOnly', 'flyersOnly')):
                if key in props:
                    rec[field] = bool(props[key][2])
            rec['location'] = actor_location(info, uexp, hdr, e, loc_cache)
            rec['start'] = bool(props.get('bIsStartPoint', (0, 0, 0))[2])
            rec['exit'] = bool(props.get('bIsExitPoint', (0, 0, 0))[2])
            if 'NextPoints' in props:
                rec['next'] = branches(info, props['NextPoints'][2])
            points[e['name']] = rec

        elif cls in gamemode.PIECES:
            #: a game type's piece (gamemode.PIECES): its arrays and scalars as written
            arrays, scalars = {}, {}
            for key, kind in gamemode.piece_arrays(cls).items():
                _nm, tag, raw = props.get(key, (key, None, b''))
                if tag == 'ArrayProperty' and len(raw) >= 4:
                    try:
                        arrays[key] = gamemode.decode(kind, raw)
                    except (struct.error, UnicodeDecodeError, ValueError):
                        pass
            for key, (kind, fmt) in gamemode.piece_scalars(cls).items():
                _nm, tag, raw = props.get(key, (key, None, b''))
                if tag == kind and len(raw) == struct.calcsize(fmt):
                    scalars[key] = round(struct.unpack(fmt, raw)[0], 4)
            xf = actor_transform(info, uexp, hdr, e, loc_cache)
            pieces.append({'export': e['name'], 'cls': cls, 'arrays': arrays,
                           'scalars': scalars,
                           'location': actor_location(info, uexp, hdr, e, loc_cache),
                           'rotation': xf['rotation']})

        elif 'Door' in cls:
            #: The CLASS and the ROTATION are what let a door be drawn as its own
            #: mesh rather than a box: the class says which portcullis, and without
            #: the rotation it lies across the archway instead of filling it.
            xf = actor_transform(info, uexp, hdr, e, loc_cache)
            doors[e['name']] = {'export': e['name'],
                                'cls': cls,
                                'location': actor_location(info, uexp, hdr, e,
                                                           loc_cache),
                                'rotation': xf['rotation'],
                                'scale': xf['scale'],
                                #: What THIS placement overrides on the class's
                                #: components -- the force-field plane's size lives
                                #: here and nowhere else.
                                'components': actor_components(info, uexp, hdr,
                                                               export_index)}

    #: a piece's rifts' own units (an older Crystal's rows on its spawners)
    for p in pieces:
        p['spawnerUnits'] = {sp['index']: sp['units'] for sp in spawners if sp.get('units')}
    return {'map': name, 'spawners': spawners, 'points': points, 'doors': doors,
            'pieces': pieces}


def branches(info, raw):
    """TArray<FOMDPathBranch>: [(PointIndex, BranchWeight, BranchLength)]."""
    names = info['names']
    r = R(raw, 0)
    try:
        count = r.i32()
        r.raw(8 + 8 + 4 + 4 + 8 + 16 + 1)      # inner element tag
        out = []
        for _ in range(count):
            rec = {}
            while True:
                ni = r.i32(); r.i32()
                nm = names[ni] if 0 <= ni < len(names) else '?'
                if nm == 'None':
                    break
                ti = r.i32(); r.i32()
                tp = names[ti] if 0 <= ti < len(names) else '?'
                size = r.i32(); r.i32(); r.u8()
                v = r.raw(size)
                if tp == 'IntProperty':
                    rec[nm] = struct.unpack('<i', v)[0]
                elif tp == 'FloatProperty':
                    rec[nm] = round(struct.unpack('<f', v)[0], 3)
            out.append(rec)
        return out
    except Exception:
        return []


def trace(points, start_sub, limit=40):
    """Follow a route from a spawner's start point to an exit."""
    by_index = {}
    for nm, p in points.items():
        if p['index'] is not None:
            by_index[p['index']] = p
    # start_sub looks like "PersistentLevel.OMDPathPoint3"
    key = start_sub.split('.')[-1] if start_sub else None
    cur = points.get(key)
    if cur is None:
        return None, 'start point %r not found' % key
    route, seen = [], set()
    while cur and len(route) < limit:
        tag = cur['export']
        if tag in seen:
            route.append('(loop)')
            break
        seen.add(tag)
        label = '%s[%s]' % (tag, cur['index'])
        if cur['exit']:
            route.append(label + '=EXIT')
            break
        route.append(label + ('*' if len(cur['next']) > 1 else ''))
        if not cur['next']:
            break
        nxt = max(cur['next'], key=lambda b: b.get('BranchWeight', 0))
        cur = by_index.get(nxt.get('PointIndex'))
    return route, None


def protos():
    """Every AI character proto: name -> guid tuple. The shipped table's rows, then every
    installed mod's minion rows (modminions; their names are `<ModKey>_` prefixed)."""
    out = dict(shipped_protos())
    try:
        from riftlord.missions import modminions
        for name, guid in modminions.guids().items():
            out.setdefault(name, guid)
    except (Exception, SystemExit):                  # noqa: BLE001
        pass
    return out


def mod_flags():
    """proto_flags() entries of the installed mods' minion rows (modminions), each with
    its 'mod' key; {} when none can be read."""
    try:
        from riftlord.missions import modminions
        return modminions.flags()
    except (Exception, SystemExit):                  # noqa: BLE001
        return {}


def shipped_protos():
    """The shipped AICharacterProtos rows: name -> guid tuple."""
    from riftlord.build.navigation.navpatch import _index, ue, Pak
    idx = _index()
    out = {}
    for ext in ('.uasset', '.uexp'):
        key = '/Game/Data/AICharacterProtos' + ext
        p, nm = idx[key]
        data = ue.read(Pak(p), nm)
        os.makedirs(TMP, exist_ok=True)
        dest = os.path.join(TMP, 'AICharacterProtos' + ext)
        part = '%s.%d-%d.part' % (dest, os.getpid(), threading.get_ident())
        with open(part, 'wb') as fh:
            fh.write(data)
        os.replace(part, dest)                        # other processes may read it meanwhile
    ua = os.path.join(TMP, 'AICharacterProtos.uasset')
    info = parse_header(ua)
    hdr = os.path.getsize(ua)
    with open(os.path.join(TMP, 'AICharacterProtos.uexp'), 'rb') as fh:
        uexp = fh.read()
    names = info['names']
    ex = [x for x in info['exports']
          if 'DataTable' in str(x.get('class') or x.get('cls'))][0]
    r = R(uexp, ex['off'] - hdr)

    def skip():
        while True:
            ni = r.i32(); r.i32()
            nm = names[ni] if 0 <= ni < len(names) else '?'
            if nm == 'None':
                return
            ti = r.i32(); r.i32()
            tp = names[ti] if 0 <= ti < len(names) else '?'
            size = r.i32(); r.i32()
            if tp == 'BoolProperty':
                r.u8(); r.u8(); continue
            if tp == 'StructProperty':
                r.i32(); r.i32(); r.raw(17)
            elif tp in ('ArrayProperty', 'ByteProperty', 'EnumProperty'):
                r.i32(); r.i32(); r.u8()
            else:
                r.u8()
            r.raw(size)

    skip(); r.raw(4)
    n = r.i32()
    for _ in range(n):
        ri = r.i32(); r.i32()
        row = names[ri] if 0 <= ri < len(names) else '?'
        guid = None
        while True:
            ni = r.i32(); r.i32()
            nm = names[ni] if 0 <= ni < len(names) else '?'
            if nm == 'None':
                break
            ti = r.i32(); r.i32()
            tp = names[ti] if 0 <= ti < len(names) else '?'
            size = r.i32(); r.i32()
            if tp == 'BoolProperty':
                r.u8(); r.u8(); continue
            if tp == 'StructProperty':
                r.i32(); r.i32(); r.raw(17)
            elif tp in ('ArrayProperty', 'ByteProperty', 'EnumProperty'):
                r.i32(); r.i32(); r.u8()
            else:
                r.u8()
            pay = r.raw(size)
            if nm == 'Guid' and size == 16:
                guid = struct.unpack('<4i', pay)
        if guid:
            out[row] = guid
    return out


def bp_report(bp_name, known):
    """Proto guids present in an onslaught Blueprint, with their offsets."""
    from riftlord.build.navigation.navpatch import _index, ue, Pak
    idx = _index()
    key = '/Game/Data/Onslaughts/%s.uexp' % bp_name
    if key not in idx:
        raise SystemExit('not found: %s' % key)
    p, nm = idx[key]
    data = ue.read(Pak(p), nm)
    byguid = dict((v, k) for k, v in known.items())
    hits = {}
    # Step one byte, not four: these guids are NOT 4-byte aligned in the asset.
    # A step-4 scan finds 5 of OrcLight's 11 occurrences and misses OrcMedium's
    # 8 entirely, which would hide half the units a wave uses.
    for off in range(0, len(data) - 16):
        g = struct.unpack_from('<4i', data, off)
        if g in byguid:
            hits.setdefault(byguid[g], []).append(off)
    return len(data), hits


def waves_from_log(log_path, map_name=None):
    """Recover the wave -> spawner -> spawn-call table from a NavProbe capture.

    The coordinator asks the Blueprint about EVERY (wave, spawner) combination at
    BeginPlay -- 98 events for Scenario_18's 7 waves x 14 spawners -- and the switch
    answers only for the pairs it defines. So the declaration log is the authored
    wave table, and this reads it back out.
    """
    import re
    import collections
    with open(log_path, encoding='utf-8', errors='replace') as fh:
        text = fh.read()
    i = text.rfind("watching the coordinator's own calls")
    tail = text[i:] if i >= 0 else text
    events = re.findall(r'== Onslaught_Start wave=(\S+) spawner=(\S+) ==', tail)
    tagged = re.findall(r'\]\s+\[\d+\] w(\S+)/s(\S+)\s+(\w+)', tail)
    per = collections.Counter()
    for w, sp, fn in tagged:
        if fn == 'OnslaughtSpawnEntities' and w != 'nil':
            per[(w, sp)] += 1
    waves = sorted({w for w, _ in events if w != 'nil'}, key=lambda x: int(x))
    spawners = sorted({s for _, s in events if s != 'nil'}, key=lambda x: int(x))
    return {'events': len(events), 'waves': waves, 'spawners': spawners, 'per': per}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--map')
    ap.add_argument('--bp')
    ap.add_argument('--protos', action='store_true')
    ap.add_argument('--waves', metavar='UE4SS.log',
                    help='recover the wave/spawner table from a NavProbe capture')
    args = ap.parse_args()

    if args.waves:
        w = waves_from_log(args.waves)
        print('%d Onslaught_Start events = %d waves x %d spawners\n'
              % (w['events'], len(w['waves']), len(w['spawners'])))
        header = 'wave  ' + ' '.join('%4s' % s for s in w['spawners'])
        print(header)
        print('-' * len(header))
        for wv in w['waves']:
            row = ['%4d' % w['per'][(wv, s)] if w['per'].get((wv, s)) else '   .'
                   for s in w['spawners']]
            print('%-5s ' % wv + ' '.join(row))
        print('\n(cell = spawn calls declared for that wave and spawner; . = none)')
        return

    if args.protos:
        for k, v in sorted(protos().items()):
            print('%-28s %s' % (k, ','.join(str(x) for x in v)))
        return

    if args.map:
        rep = map_report(args.map)
        print('%s: %d spawner(s), %d path point(s)\n'
              % (rep['map'], len(rep['spawners']), len(rep['points'])))
        for sp in sorted(rep['spawners'], key=lambda s: (s['index'] is None, s['index'])):
            print('  SpawnerIndex %-4s%s (%s)' % (
                sp['index'], '' if sp.get('index_explicit') else '*', sp['export']))
            print('    door          %s' % (sp['door'] or '-'))
            print('    start point   %s' % (sp['start'] or '-'))
            if sp['flyer']:
                print('    flyer start   %s' % sp['flyer'])
            print('    maxSpawnPerRow %s' % sp['maxRow'])
            route, err = trace(rep['points'], sp['start'])
            if err:
                print('    route         %s' % err)
            else:
                print('    route         %s' % ' -> '.join(route))
            print()
        print('  (* on a SpawnerIndex means the asset omitted it, so it is the')
        print('   default, 1; * in a route marks a branch, heaviest followed)')

    if args.bp:
        known = protos()
        size, hits = bp_report(args.bp, known)
        print('\n%s.uexp: %d bytes' % (args.bp, size))
        if not hits:
            print('  no known proto guids found')
        total = sum(len(v) for v in hits.values())
        print('  %d guid occurrence(s) across %d unit(s)' % (total, len(hits)))
        for unit, offs in sorted(hits.items(), key=lambda kv: -len(kv[1])):
            print('  %-24s %2d at %s%s' % (unit, len(offs),
                  ', '.join(str(o) for o in offs[:6]),
                  ' ...' if len(offs) > 6 else ''))
        print('\n  patch a unit by overwriting its 16 bytes at these offsets;')
        print('  guid values come from --protos')


if __name__ == '__main__':
    main()


def proto_flags():
    """name -> {'flyer', 'boss', 'runner', 'barricades'} from AICharacterProtos.

    A cooked row omits any property equal to its class default, so an absent
    flag means false rather than missing. `bIsFlyer` is what decides whether a
    unit can use a spawner's flyer path -- guessing from the name would work
    today only because every flyer happens to be a Hellbat.
    """
    shipped_protos()                          # fills the cache files
    ua = os.path.join(TMP, 'AICharacterProtos.uasset')
    info = parse_header(ua)
    names = info['names']
    hdr = os.path.getsize(ua)
    with open(os.path.join(TMP, 'AICharacterProtos.uexp'), 'rb') as fh:
        uexp = fh.read()
    ex = [x for x in info['exports']
          if 'DataTable' in str(x.get('class') or x.get('cls'))][0]
    r = R(uexp, ex['off'] - hdr)

    def walk(collect):
        while True:
            ni = r.i32(); r.i32()
            nm = names[ni] if 0 <= ni < len(names) else '?'
            if nm == 'None':
                return
            ti = r.i32(); r.i32()
            tp = names[ti] if 0 <= ti < len(names) else '?'
            size = r.i32(); r.i32()
            if tp == 'BoolProperty':
                v = r.u8(); r.u8()
                if collect is not None:
                    collect[nm] = bool(v)
                continue
            if tp == 'StructProperty':
                r.i32(); r.i32(); r.raw(17)
            elif tp in ('ArrayProperty', 'ByteProperty', 'EnumProperty'):
                r.i32(); r.i32(); r.u8()
            else:
                r.u8()
            pay = r.raw(size)
            # an FText is written as three FStrings -- namespace, key, then the
            # source text, which is the name a player sees
            if collect is not None and tp == 'TextProperty':
                import re as _re
                runs = [b.decode('ascii', 'ignore').rstrip('\x00')
                        for b in _re.findall(rb'[ -~]{2,}', bytes(pay))]
                if runs:
                    collect[nm] = runs[-1]

    walk(None)
    r.raw(4)
    out = {}
    for _ in range(r.i32()):
        ri = r.i32(); r.i32()
        row = names[ri] if 0 <= ri < len(names) else '?'
        flags = {}
        walk(flags)
        out[row] = {'flyer': flags.get('bIsFlyer', False),
                    'boss': flags.get('bIsBoss', False),
                    'runner': flags.get('bHasRunnerMovement', False),
                    'barricades': flags.get('bIsBarricadeDestroyer', False),
                    'name': flags.get('DisplayName') or None}
    for row, rec in mod_flags().items():
        out.setdefault(row, rec)
    return out
