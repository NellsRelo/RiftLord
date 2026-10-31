"""The mission's own MapData: the asset that draws its minimap and its markers.

A level's minimap does not live in the level. It is a sibling package,
`<map>_MapData`, holding one `OMDMapData` export with `RiftLocations`,
`DoorLocations` and `MapVolumes` -- the last naming the minimap image and the
world-to-texture transform ([[omd3-minimap-and-mapdata]]).

A copy of a shipped level therefore has no MapData, and `makemod` pointed the
mission row at the SOURCE map's. That draws the right picture, because the
geometry is the source's, but the rift and door markers stay where the shipped
level had them -- so a plan that moves the rift drew it in the old place, and a
door the plan added drew nothing at all.

This emits a real copy: the source's volumes and minimap image kept, the marker
arrays rewritten from the plan.
"""

import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

from riftlord.build.minimap import mapdata  # noqa: E402
from riftlord.build import modtable  # noqa: E402
from riftlord.formats.ue.uassetio import Package  # noqa: E402

#: The two marker arrays, both `TArray<FVector>` of world positions. Measured against
#: Scenario_01: `RiftLocations[0]` is its rift actor's location to the float, and
#: `DoorLocations[0]` is `Door01`'s.
MARKERS = ('RiftLocations', 'DoorLocations')

#: A `TArray<FVector>` payload is the count, then the inner element's own property tag,
#: then the vectors. The tag is 49 bytes; everything in it is copied through except its
#: size field, which counts the elements' bytes.
INNER_TAG = 49

#: Where the size sits inside a property tag, inner ones included.
ELEMENT_SIZE_AT = 16

#: Bytes one `FVector` occupies.
VECTOR = 12


def _walk(names, data):
    """([(name, kind, header, payload)], tail) over one export's tagged properties.

    `tail` is whatever follows the terminating `None`, and it goes back untouched: an
    `OMDMapData` export carries four more bytes after its property list, and dropping
    them left every emitted MapData four bytes short. The package header is recomputed on
    write, so it stayed self-consistent and `pkgcheck` saw nothing -- the game answered
    "Corrupt data" when the hub loaded the mission.
    """
    out, o = [], 0
    while o + 8 <= len(data):
        ni = struct.unpack_from('<i', data, o)[0]
        nm = names[ni] if 0 <= ni < len(names) else '?'
        if nm == 'None':
            out.append((nm, None, data[o:o + 8], b''))
            o += 8
            return out, data[o:]          # nothing is tagged past the terminator
        if o + 24 > len(data):
            break
        ti = struct.unpack_from('<i', data, o + 8)[0]
        kind = names[ti] if 0 <= ti < len(names) else '?'
        size = struct.unpack_from('<i', data, o + 16)[0]
        if kind == 'BoolProperty':
            head, size = 26, 0
        elif kind == 'StructProperty':
            head = 24 + 8 + 17
        elif kind in ('ArrayProperty', 'ByteProperty', 'EnumProperty'):
            head = 24 + 8 + 1
        else:
            head = 24 + 1
        out.append((nm, kind, data[o:o + head], data[o + head:o + head + size]))
        o += head + size
    return out, data[o:]


def _reassemble(fields, tail=b''):
    """Tagged properties back into bytes, each header's size field made to fit."""
    out = bytearray()
    for _nm, kind, head, payload in fields:
        head = bytearray(head)
        if kind is not None and kind != 'BoolProperty':
            struct.pack_into('<i', head, 16, len(payload))
        out += head + payload
    return bytes(out) + tail


def _vectors_payload(template, points):
    """A marker array's payload holding `points`, keeping the inner element tag.

    The tag describes the element type, not the contents, so it is copied rather than
    written: getting those 49 bytes wrong is not something an offline check would catch.
    """
    if len(template) < 4 + INNER_TAG:
        raise SystemExit('a marker array is too short to carry its element tag')
    # The element tag holds the size of the elements TOGETHER, not of one, so an array
    # that grows without it being rewritten is malformed. `pkgcheck` catches it --
    # "2 element(s) of Vector occupy 24 bytes, the element tag says 12" -- and nothing
    # else would have: the package is otherwise well formed and reads back fine here.
    tag = bytearray(template[4:4 + INNER_TAG])
    struct.pack_into('<i', tag, ELEMENT_SIZE_AT, VECTOR * len(points))
    return (struct.pack('<i', len(points)) + bytes(tag)
            + b''.join(struct.pack('<3f', *(float(v) for v in p)) for p in points))


def markers_for(level_name, plan, level=None):
    """(rifts, doors) world positions the minimap should mark, after the plan.

    The level's own actors, then the plan applied: `wiring` moves one or parks it
    (`gone`), and whatever the plan adds is appended. A parked actor is dropped rather
    than drawn at its parking spot far below the map.
    """
    from riftlord.mapeditor import leveldata
    level = level if level is not None else leveldata.level(level_name)
    rifts = {r['name']: tuple(r['loc']) for r in (level.get('rifts') or [])}
    doors = {d['export']: tuple(d['location'])
             for d in (level.get('doors') or {}).values()}
    for edit in plan.get('wiring') or []:
        name = edit.get('name')
        for group in (rifts, doors):
            if name in group:
                if edit.get('gone'):
                    del group[name]
                elif edit.get('at'):
                    group[name] = tuple(edit['at'])
    out_rifts = list(rifts.values()) + [tuple(r['at']) for r in (plan.get('rifts') or [])
                                        if r.get('at')]
    out_doors = list(doors.values()) + [tuple(d['at']) for d in (plan.get('doors') or [])
                                        if d.get('at')]
    return out_rifts, out_doors


def build(level_name, new_path, plan, level=None):
    """((uasset, uexp), report) for the mission's MapData, or (None, report).

    `new_path` is the package path it will be written at; the mission row has to name
    the same one.
    """
    source = '%s_MapData' % mapdata.map_path(level_name)
    files = mapdata._fetch(source)
    report = {'from': source, 'to': new_path}
    if '.uasset' not in files or '.uexp' not in files:
        report['skipped'] = '%s has no MapData to copy' % level_name
        return None, report
    with open(files['.uasset'], 'rb') as fh:
        uasset = fh.read()
    with open(files['.uexp'], 'rb') as fh:
        uexp = fh.read()
    pkg = Package(uasset, uexp)
    pkg.load_payloads()
    names = [n for n, _h in pkg.names]

    rifts, doors = markers_for(level_name, plan, level=level)
    wanted = {'RiftLocations': rifts, 'DoorLocations': doors}

    original = pkg.exports[0]['data']
    fields, tail = _walk(names, original)
    # The rewrite must be lossless before anything is changed. Nothing downstream can
    # catch a loss: the header is recomputed on write, so a short export stays
    # self-consistent and only the game complains, with "Corrupt data".
    if _reassemble(fields, tail) != original:
        raise SystemExit('%s does not round-trip; refusing to rewrite it' % source)
    fields, tail = _walk(names, original)
    have = {nm for nm, _k, _h, _p in fields}
    template = next((p for nm, _k, _h, p in fields if nm in MARKERS), None)
    out, changed = [], {}
    for nm, kind, head, payload in fields:
        if nm in wanted:
            out.append((nm, kind, head, _vectors_payload(payload, wanted[nm])))
            changed[nm] = len(wanted[nm])
            continue
        out.append((nm, kind, head, payload))

    # A map with no doors ships no DoorLocations at all, so a plan that adds one needs
    # the array created. Its header is the other marker array's with the name swapped --
    # same type, same element tag -- which is the only way to be sure those bytes are
    # right.
    for nm in MARKERS:
        if nm in have or not wanted.get(nm):
            continue
        if template is None:
            report.setdefault('warnings', []).append(
                'no marker array to copy, so %s could not be added' % nm)
            continue
        donor = next(f for f in fields if f[0] in MARKERS)
        head = bytearray(donor[2])
        struct.pack_into('<i', head, 0, pkg.add_name(nm))
        struct.pack_into('<i', head, 4, 0)
        out.insert(len(out) - 1 if out and out[-1][0] == 'None' else len(out),
                   (nm, donor[1], bytes(head),
                    _vectors_payload(template, wanted[nm])))
        changed[nm] = len(wanted[nm])
        report.setdefault('added_arrays', []).append(nm)

    pkg.exports[0]['data'] = _reassemble(out, tail)
    leaf_old, leaf_new = source.rsplit('/', 1)[-1], new_path.rsplit('/', 1)[-1]
    report['renamed'] = modtable.retarget(pkg, exact={
        source: new_path,
        '%s.%s' % (source, leaf_old): '%s.%s' % (new_path, leaf_new),
        leaf_old: leaf_new,
    })
    report['markers'] = changed
    return pkg.write(), report
