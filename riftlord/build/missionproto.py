"""Read and rewrite the MissionProtos DataTable, so a mod can add a mission.

A row is 25 fields and every shipped row sets all of them, so a new mission is
made by cloning an existing row and overriding the handful that identify it --
name, level, map data, art, par time and a fresh Guid. Everything else is
inherited from the template, which is what keeps the manual entry small.

    python3 missionproto.py --list
    python3 missionproto.py --show Scenario_01
    python3 missionproto.py --check
    python3 missionproto.py --clone Scenario_01 --row Scenario_Custom01 \\
        --name "My Custom Map" --level /Game/Maps/Custom/MyMap/MyMap --out .
"""

import argparse
import os
import struct
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.build.minimap import mapdata  # noqa: E402
from riftlord.formats.ue.uassetio import Package  # noqa: E402

TABLE = '/Game/Data/Campaign/MissionProtos'
CAMPAIGN_TABLE = '/Game/Data/Campaign/CampaignProtos'

#: Fields a new mission must supply; everything else is inherited.
REQUIRED = ('MissionName', 'MissionLevel', 'MapData', 'Guid')
#: Fields a user will usually want to set as well.
OPTIONAL = ('ParTimeSeconds', 'EndlessWavesPerSkull', 'ThumbnailTexture',
            'MissionLoadScreen', 'RandomModeMissionSelectionTexture',
            'RandomModeLoadingScreenTexture', 'ChallengeImage',
            'bIsWarScenario', 'bRequiresEntitlement')


def _fname(names, index, number):
    """An FName as it reads: a number of N renders the name with suffix N-1."""
    base = names[index] if 0 <= index < len(names) else '?'
    return base if number == 0 else '%s_%d' % (base, number - 1)


def _fstring(text):
    if not text:
        return struct.pack('<i', 0)
    raw = text.encode('utf-8') + b'\0'
    return struct.pack('<i', len(raw)) + raw


class Field:
    """One tagged property: its header, its payload, and how to rewrite it."""

    def __init__(self, name, kind, sub, header, payload, bool_value=None):
        self.name, self.kind, self.sub = name, kind, sub
        self.header, self.payload = bytearray(header), bytearray(payload)
        self.bool_value = bool_value

    def bytes(self):
        out = bytearray(self.header)
        struct.pack_into('<i', out, 16, len(self.payload))   # size field
        return bytes(out) + bytes(self.payload)


class Table:
    """The MissionProtos DataTable: a preamble, then rows of tagged properties."""

    def __init__(self, pkg):
        self.pkg = pkg
        self.names = [s for s, _h in pkg.names]
        self.export = pkg.exports[0]
        data = self.export['data']
        o = 0
        while True:
            f, ln = self._read_field(data, o)
            o += ln
            if f is None:
                break
        self.preamble = bytes(data[:o + 4])
        o += 4
        count = struct.unpack_from('<i', data, o)[0]
        o += 4
        self.rows = []
        for _ in range(count):
            ni, num = struct.unpack_from('<ii', data, o)
            o += 8
            fields = []
            while True:
                f, ln = self._read_field(data, o)
                o += ln
                if f is None:
                    break
                fields.append(f)
            self.rows.append({'name': _fname(self.names, ni, num),
                              'name_index': ni, 'name_number': num,
                              'fields': fields})
        self.tail = bytes(data[o:])

    def _read_field(self, data, o):
        ni, _n = struct.unpack_from('<ii', data, o)
        nm = self.names[ni] if 0 <= ni < len(self.names) else '?'
        if nm == 'None':
            return None, 8
        ti, _n2 = struct.unpack_from('<ii', data, o + 8)
        kind = self.names[ti] if 0 <= ti < len(self.names) else '?'
        size = struct.unpack_from('<i', data, o + 16)[0]
        head = 24
        sub = None
        bool_value = None
        if kind == 'BoolProperty':
            bool_value = data[o + 24]
            head = 26
            size = 0
        elif kind == 'StructProperty':
            si = struct.unpack_from('<i', data, o + 24)[0]
            sub = self.names[si] if 0 <= si < len(self.names) else '?'
            head = 24 + 8 + 17
        elif kind in ('ArrayProperty', 'ByteProperty', 'EnumProperty'):
            ii = struct.unpack_from('<i', data, o + 24)[0]
            sub = self.names[ii] if 0 <= ii < len(self.names) else '?'
            head = 24 + 8 + 1
        else:
            head = 24 + 1
        field = Field(nm, kind, sub, data[o:o + head], data[o + head:o + head + size],
                      bool_value)
        return field, head + size

    # -- lookup ----------------------------------------------------------
    def row(self, name):
        return next((r for r in self.rows if r['name'] == name), None)

    def field(self, row, name):
        return next((f for f in row['fields'] if f.name == name), None)

    # -- edits -----------------------------------------------------------
    def set_text(self, field, namespace, key, source):
        """An FText: flags, history type, then namespace/key/source strings."""
        field.payload = bytearray(struct.pack('<ib', 0, 0) + _fstring(namespace)
                                  + _fstring(key) + _fstring(source))

    def set_soft_path(self, field, path):
        """A SoftObjectProperty keeps its asset path in the FName."""
        idx = self.pkg.add_name(path)
        self.names = [s for s, _h in self.pkg.names]
        field.payload = bytearray(struct.pack('<ii', idx, 0) + struct.pack('<i', 0))

    def struct_sub_fields(self, field):
        """[(sub-field, payload offset)] inside a StructProperty payload."""
        data = bytes(field.payload)
        out, o = [], 0
        while o < len(data) - 8:
            sub, used = self._read_field(data, o)
            if sub is None:
                break
            out.append((sub, o + len(sub.header)))
            o += used
        return out

    def set_struct_soft_path(self, field, sub_name, path=None):
        """Point a soft path INSIDE a struct at `path`, or at nothing.

        A SoftObjectProperty is twelve bytes whatever it names, so the sub-field
        is patched where it lies and no size in any header changes.
        """
        idx = self.pkg.add_name(path if path is not None else 'None')
        self.names = [s for s, _h in self.pkg.names]
        data = bytearray(field.payload)
        for sub, at in self.struct_sub_fields(field):
            if sub.name == sub_name and sub.kind == 'SoftObjectProperty':
                struct.pack_into('<iii', data, at, idx, 0, 0)
                field.payload = data
                return True
        return False

    def set_float(self, field, value):
        field.payload = bytearray(struct.pack('<f', float(value)))

    def set_int(self, field, value):
        field.payload = bytearray(struct.pack('<i', int(value)))

    def set_bool(self, field, value):
        field.bool_value = 1 if value else 0
        field.header[24] = field.bool_value

    def set_guid(self, field, guid=None):
        """A Guid struct is four int32 in UE's A-B-C-D order."""
        g = uuid.uuid4() if guid is None else uuid.UUID(str(guid))
        b = g.bytes
        field.payload = bytearray(struct.pack('<4I', *struct.unpack('>4I', b)))
        return g

    #: A TArray<FStruct> payload opens with the count then one inner element
    #: tag: name, type, size, array index, struct name, guid, has-guid flag.
    INNER_TAG = 8 + 8 + 4 + 4 + 8 + 16 + 1

    def struct_array(self, field):
        """(count, inner tag bytes, [element bytes]) of a TArray<FStruct>."""
        p = bytes(field.payload)
        count = struct.unpack_from('<i', p, 0)[0]
        body = p[4 + self.INNER_TAG:]
        if count and len(body) % count:
            raise SystemExit('%s: %d bytes does not divide into %d elements'
                             % (field.name, len(body), count))
        size = len(body) // count if count else 0
        return count, p[4:4 + self.INNER_TAG], [body[i * size:(i + 1) * size]
                                                for i in range(count)]

    def _pack_array(self, tag, items):
        """Rebuild a TArray<FStruct> payload: count, inner tag, elements.

        The inner tag carries its own size field, and it counts the element
        bytes only -- not the count, not itself. The engine writes it by
        patching the value back once the elements are down, so a file where it
        disagrees with what follows is one the engine could not have written,
        and it is read back as corrupt. Growing an array therefore means
        rewriting two sizes: this one, and the outer tag's (see Field.bytes).
        """
        body = b''.join(items)
        tag = bytearray(tag)
        struct.pack_into('<i', tag, 16, len(body))
        return bytearray(struct.pack('<i', len(items)) + bytes(tag) + body)

    def append_struct(self, field, element):
        """Append one element to a TArray<FStruct> and bump its count."""
        count, tag, items = self.struct_array(field)
        items.append(element)
        field.payload = self._pack_array(tag, items)
        return len(items)

    def clear_array(self, row, field_name):
        """Empty a TArray<FStruct> on a row, keeping its inner tag.

        The element bytes go, the count drops to zero, and the tag that names
        the element type stays -- so the array still reads back as the same
        array, just with nothing in it.
        """
        field = self.field(row, field_name)
        if field is None:
            return None
        count, tag, _items = self.struct_array(field)
        field.payload = self._pack_array(tag, [])
        return count

    def set_missions(self, campaign, guid_payloads):
        """Replace a campaign's MissionList with exactly these Guids."""
        field = self.field(campaign, 'MissionList')
        _count, tag, items = self.struct_array(field)
        if not items:
            raise SystemExit('nothing to copy the element shape from')
        out = []
        for g in guid_payloads:
            element = bytearray(items[0])
            element[-24:-8] = g
            out.append(bytes(element))
        field.payload = self._pack_array(tag, out)
        return len(out)

    def guid_offset(self, field, known):
        """Where a Guid sits inside this array's elements.

        Not every array of proto pointers has the same element. A
        `MissionList` entry is a 73-byte FOMDSoftProtoPtr with its Guid near the
        end, while a `MissionUnlocks` entry is a 156-byte
        FOMDMissionProgressionElement -- `bool bIsPremission` plus the pointer --
        whose Guid is at 124. So the offset is located from an element that is
        already there rather than assumed.
        """
        _count, _tag, items = self.struct_array(field)
        for element in items:
            for g in known:
                at = element.find(g)
                if at >= 0:
                    return at
        return None

    def element_shape(self, field_name):
        """An element from any row that has one, to copy a new entry from.

        An array that is empty on the row being written still has to produce a
        correctly shaped element -- every Endless mission ships with an empty
        MissionUnlocks, so the first mission of that campaign has nothing of its
        own to copy.
        """
        for r in self.rows:
            f = self.field(r, field_name)
            if f is None:
                continue
            _count, _tag, items = self.struct_array(f)
            if items:
                return items[0], f
        return None, None

    def array_guid_offset(self, field_name, known):
        """Where a Guid sits in this array's elements, asked table-wide.

        A row whose own array is empty cannot answer, so the question goes to
        whichever row does have entries.
        """
        _shape, field = self.element_shape(field_name)
        return self.guid_offset(field, known) if field is not None else None

    def add_guid(self, row, field_name, guid_payload, known=None):
        """Add a Guid to an array of proto pointers on a row.

        A new entry is a copy of an existing one with its sixteen Guid bytes
        replaced -- at the offset the existing entries use, which differs by
        array. When this row's array is empty, the shape comes from a row whose
        is not.
        """
        field = self.field(row, field_name)
        if field is None:
            raise SystemExit('%s has no %s' % (row['name'], field_name))
        _count, _tag, items = self.struct_array(field)
        shape, from_field = (items[0], field) if items else \
            self.element_shape(field_name)
        if shape is None:
            raise SystemExit('no row has a %s element to copy' % field_name)
        at = self.guid_offset(from_field, known) if known else None
        if at is None:
            at = len(shape) - 24               # the FOMDSoftProtoPtr layout
        element = bytearray(shape)
        element[at:at + 16] = guid_payload
        return self.append_struct(field, bytes(element))

    def add_mission(self, campaign, guid_payload, at=None):
        """Add a mission Guid to a campaign's MissionList: last, or at place `at`
        (1-based; the elements from there on move along by one, byte for byte).

        Every element is an FOMDSoftProtoPtr -- a tagged Guid struct plus its
        None terminator -- so a new one is an existing element with its 16 Guid
        bytes replaced.
        """
        field = self.field(campaign, 'MissionList')
        if field is None:
            raise SystemExit('%s has no MissionList' % campaign['name'])
        _count, _tag, items = self.struct_array(field)
        if not items:
            raise SystemExit('%s has an empty MissionList to copy from'
                             % campaign['name'])
        element = bytearray(items[0])
        element[-24:-8] = guid_payload          # the Guid sits before the None tag
        if at is None:
            return self.append_struct(field, bytes(element))
        if not 1 <= at <= len(items) + 1:
            raise SystemExit('%s: place %d is not 1 to %d'
                             % (campaign['name'], at, len(items) + 1))
        items.insert(at - 1, bytes(element))
        field.payload = self._pack_array(_tag, items)
        return len(items)

    # -- lifting a row out of another package ----------------------------
    #
    # An FName is an index into its own package's name table, and two copies of
    # the same table do not agree on those indices -- EndlessWar's MissionProtos
    # diverges from stock at 459. So a row cannot be copied byte for byte; every
    # index in it has to be resolved through the source's names and re-added to
    # this one. Rewriting an index never changes its width, so the whole job is
    # an in-place patch and no size field moves.

    def _looks_tagged(self, data, names):
        """True when a payload is a tagged property stream rather than raw bytes.

        A `Guid` struct is sixteen opaque bytes; an `OMDSoftProtoPtr` is a
        little property stream. They are told apart by whether the first eight
        bytes read as a name followed by something ending in `Property`.
        """
        if len(data) < 8:
            return False
        ni = struct.unpack_from('<i', data, 0)[0]
        if not 0 <= ni < len(names):
            return False
        if names[ni] == 'None':
            return True
        if len(data) < 24:
            return False
        ti = struct.unpack_from('<i', data, 8)[0]
        return 0 <= ti < len(names) and names[ti].endswith('Property')

    def _remap_at(self, buf, at, names):
        """Patch the FName index at `at` from `names` into this table's."""
        i = struct.unpack_from('<i', buf, at)[0]
        if 0 <= i < len(names):
            struct.pack_into('<i', buf, at, self.pkg.add_name(names[i]))

    def remap_stream(self, buf, names, base=0, limit=None):
        """Patch every FName in a tagged property stream, in place.

        A property tag is 24 bytes and then a HasPropertyGuid byte, so the
        smallest header is 25 -- the same arithmetic `_read_field` does. Taking
        it as 24 lands one byte short of the next tag, which reads as a name
        index out of range, and the walk then gives up quietly with the rest of
        the stream still pointing at the other package's names.

        So a stream that cannot be followed to its None is an error, not a
        place to stop: the alternative is a row that looks fine and names the
        wrong assets.
        """
        o = base
        end = len(buf) if limit is None else limit
        while o + 8 <= end:
            ni = struct.unpack_from('<i', buf, o)[0]
            nm = names[ni] if 0 <= ni < len(names) else None
            if nm is None:
                raise ValueError('name index %d at %d is not in the source '
                                 'table (%d names)'
                                 % (struct.unpack_from('<i', buf, o)[0], o,
                                    len(names)))
            if nm == 'None':
                self._remap_at(buf, o, names)
                return
            ti = struct.unpack_from('<i', buf, o + 8)[0]
            kind = names[ti] if 0 <= ti < len(names) else ''
            size = struct.unpack_from('<i', buf, o + 16)[0]
            head, sub = 24 + 1, None
            if kind == 'BoolProperty':
                head, size = 26, 0
            elif kind == 'StructProperty':
                sub, head = o + 24, 24 + 8 + 17
            elif kind in ('ArrayProperty', 'ByteProperty', 'EnumProperty'):
                sub, head = o + 24, 24 + 8 + 1
            self._remap_at(buf, o, names)                  # property name
            self._remap_at(buf, o + 8, names)              # property type
            if sub is not None:
                self._remap_at(buf, sub, names)            # struct or inner type
            self._remap_payload(buf, o + head, size, kind, names)
            o += head + size
        raise ValueError('the stream at %d..%d ends without a None terminator'
                         % (base, end))

    def _remap_payload(self, buf, at, size, kind, names):
        """Patch whatever FNames a property's own payload carries."""
        if size <= 0:
            return
        # A SoftObjectProperty keeps its whole asset path in an FName, so an
        # unremapped one points at whatever sits at that index in the other
        # package -- or past the end of it.
        if kind in ('NameProperty', 'SoftObjectProperty', 'SoftClassProperty') \
                or (kind in ('ByteProperty', 'EnumProperty') and size == 8):
            self._remap_at(buf, at, names)
            return
        if kind == 'StructProperty':
            if self._looks_tagged(bytes(buf[at:at + size]), names):
                self.remap_stream(buf, names, at, at + size)
            return
        if kind != 'ArrayProperty':
            return
        count = struct.unpack_from('<i', buf, at)[0]
        if size < 4 + self.INNER_TAG:
            return
        # An empty array still carries the tag that names its element type, so
        # the tag is remapped whatever the count; only the elements are skipped.
        tag = at + 4
        self._remap_at(buf, tag, names)                    # inner property name
        self._remap_at(buf, tag + 8, names)                # inner property type
        self._remap_at(buf, tag + 24, names)               # its struct type
        if count <= 0:
            return
        body = at + 4 + self.INNER_TAG
        each = (size - 4 - self.INNER_TAG) // count
        for k in range(count):
            start = body + each * k
            if self._looks_tagged(bytes(buf[start:start + each]), names):
                self.remap_stream(buf, names, start, start + each)

    def adopt(self, other, row, row_name=None):
        """Append a row taken from another table, with its names remapped.

        The row keeps every value it had -- this is a move between packages, not
        a rebuild from a template.
        """
        name = row_name or row['name']
        if self.row(name):
            raise SystemExit('row %r already exists' % name)
        fields = []
        for f in row['fields']:
            header = bytearray(f.header)
            payload = bytearray(f.payload)
            self._remap_at(header, 0, other.names)
            self._remap_at(header, 8, other.names)
            if f.kind == 'StructProperty' or f.kind in ('ArrayProperty',
                                                        'ByteProperty',
                                                        'EnumProperty'):
                self._remap_at(header, 24, other.names)
            self._remap_payload(payload, 0, len(payload), f.kind, other.names)
            fields.append(Field(f.name, f.kind, f.sub, bytes(header),
                                bytes(payload), f.bool_value))
        idx = self.pkg.add_name(name)
        self.names = [s for s, _h in self.pkg.names]
        got = {'name': name, 'name_index': idx, 'name_number': 0,
               'fields': fields}
        self.rows.append(got)
        return got

    def clone(self, template, row_name):
        """Append a copy of a row under a new name. Returns the new row."""
        src = self.row(template)
        if src is None:
            raise SystemExit('no template row %r' % template)
        if self.row(row_name):
            raise SystemExit('row %r already exists' % row_name)
        fields = [Field(f.name, f.kind, f.sub, bytes(f.header), bytes(f.payload),
                        f.bool_value) for f in src['fields']]
        idx = self.pkg.add_name(row_name)
        self.names = [s for s, _h in self.pkg.names]
        row = {'name': row_name, 'name_index': idx, 'name_number': 0,
               'fields': fields}
        self.rows.append(row)
        return row

    # -- output ----------------------------------------------------------
    def serialise(self):
        none_i = self.names.index('None')
        out = bytearray(self.preamble)
        out += struct.pack('<i', len(self.rows))
        for row in self.rows:
            out += struct.pack('<ii', row['name_index'], row['name_number'])
            for f in row['fields']:
                out += f.bytes()
            out += struct.pack('<ii', none_i, 0)
        out += self.tail
        return bytes(out)

    def write(self):
        self.export['data'] = self.serialise()
        return self.pkg.write()


def _loaded(ua, ux):
    pkg = Package(ua, ux)
    pkg.load_payloads()
    return pkg


def read(table=TABLE):
    files = mapdata._fetch(table)
    with open(files['.uasset'], 'rb') as fh:
        ua = fh.read()
    with open(files['.uexp'], 'rb') as fh:
        ux = fh.read()
    pkg = Package(ua, ux)
    pkg.load_payloads()
    return Table(pkg)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--show')
    ap.add_argument('--check', action='store_true',
                    help='re-serialise unchanged and compare byte for byte')
    ap.add_argument('--clone', metavar='TEMPLATE_ROW')
    ap.add_argument('--row', help='name for the new row')
    ap.add_argument('--name', help='display name')
    ap.add_argument('--level', help='/Game path of the map package')
    ap.add_argument('--map-data', help='/Game path of the MapData asset')
    ap.add_argument('--par', type=float)
    ap.add_argument('--war', action='store_true', help='mark as a war scenario')
    ap.add_argument('--campaign', help='also list the mission in this campaign row')
    ap.add_argument('--out', help='directory to write the patched table into')
    args = ap.parse_args()

    t = read()
    if args.check:
        print('rows %d | round-trip byte-identical: %s'
              % (len(t.rows), t.serialise() == t.export['data']))
        return
    if args.list:
        for r in t.rows:
            print('  %s' % r['name'])
        print('%d rows' % len(t.rows))
        return
    if args.show:
        row = t.row(args.show)
        if row is None:
            raise SystemExit('no row %r' % args.show)
        for f in row['fields']:
            mark = '*' if f.name in REQUIRED else ' '
            print(' %s %-34s %-18s %s %d bytes'
                  % (mark, f.name, f.kind, f.sub or '', len(f.payload)))
        return
    if args.clone:
        if not args.row:
            ap.error('--clone needs --row')
        row = t.clone(args.clone, args.row)
        done = []
        if args.name:
            t.set_text(t.field(row, 'MissionName'), 'MissionProtos',
                       '%s_Name' % args.row, args.name)
            done.append('MissionName=%r' % args.name)
        if args.level:
            t.set_soft_path(t.field(row, 'MissionLevel'),
                            '%s.%s' % (args.level, args.level.rsplit('/', 1)[-1]))
            done.append('MissionLevel')
        md = args.map_data or (args.level + '_MapData' if args.level else None)
        if md:
            t.set_soft_path(t.field(row, 'MapData'),
                            '%s.%s' % (md, md.rsplit('/', 1)[-1]))
            done.append('MapData')
        if args.par:
            t.set_float(t.field(row, 'ParTimeSeconds'), args.par)
            done.append('ParTimeSeconds=%g' % args.par)
        if args.war:
            t.set_bool(t.field(row, 'bIsWarScenario'), True)
            done.append('bIsWarScenario')
        g = t.set_guid(t.field(row, 'Guid'))
        done.append('Guid=%s' % g)
        print('cloned %s -> %s' % (args.clone, args.row))
        for d in done:
            print('   set %s' % d)
        written = {}
        ua, ux = t.write()
        written['MissionProtos'] = (ua, ux)
        if args.campaign:
            c = read(CAMPAIGN_TABLE)
            crow = c.row(args.campaign)
            if crow is None:
                raise SystemExit('no campaign row %r' % args.campaign)
            n = c.add_mission(crow, bytes(t.field(row, 'Guid').payload))
            print('   listed in campaign %s (now %d missions)' % (args.campaign, n))
            written['CampaignProtos'] = c.write()
        if args.out:
            os.makedirs(args.out, exist_ok=True)
            for name, (a, b) in written.items():
                base = os.path.join(args.out, name)
                with open(base + '.uasset', 'wb') as fh:
                    fh.write(a)
                with open(base + '.uexp', 'wb') as fh:
                    fh.write(b)
                print('wrote %s.uasset (%d) and .uexp (%d)' % (base, len(a), len(b)))
        back = Table(_loaded(*written['MissionProtos']))
        print('re-read: %d rows, new row present: %s'
              % (len(back.rows), back.row(args.row) is not None))
        return
    ap.error('need --list, --show, --check or --clone')


if __name__ == '__main__':
    main()
