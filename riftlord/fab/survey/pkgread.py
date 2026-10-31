# Generated from OMD2-Converter/thirdparty/survey-tools/pkgread.py by thirdparty/fabconvert/port_riftlord.py; edit the source.
"""Read-only reader for UE4 (4.21-4.27) and UE5 (5.2-5.5) EDITOR packages: summary, names,
imports, exports, tagged properties (both the classic and the 5.4+ complete-type-name tags)."""
import struct

MAGIC = 0x9E2A83C1


class R:
    def __init__(self, b, o=0):
        self.b, self.o = b, o

    def i32(self):
        v = struct.unpack_from('<i', self.b, self.o)[0]; self.o += 4; return v

    def u32(self):
        v = struct.unpack_from('<I', self.b, self.o)[0]; self.o += 4; return v

    def i64(self):
        v = struct.unpack_from('<q', self.b, self.o)[0]; self.o += 8; return v

    def u8(self):
        v = self.b[self.o]; self.o += 1; return v

    def f32(self):
        v = struct.unpack_from('<f', self.b, self.o)[0]; self.o += 4; return v

    def f64(self):
        v = struct.unpack_from('<d', self.b, self.o)[0]; self.o += 8; return v

    def raw(self, n):
        v = self.b[self.o:self.o + n]; self.o += n; return v

    def fstr(self):
        n = self.i32()
        if n == 0:
            return ''
        if n < 0:
            if -n > 1 << 20:
                raise ValueError('bad fstring')
            return self.raw(-2 * n).decode('utf-16-le', 'replace').rstrip('\0')
        if n > 1 << 20:
            raise ValueError('bad fstring')
        return self.raw(n).decode('latin1').rstrip('\0')


class Package:
    def __init__(self, path, data=None):
        self.path = path
        self.d = d = data if data is not None else open(path, 'rb').read()
        r = R(d)
        if r.u32() != MAGIC:
            raise ValueError('not a package')
        self.legacy = r.i32()
        if self.legacy != -4:
            r.i32()
        self.ue4 = r.i32()
        self.ue5 = r.i32() if self.legacy <= -8 else 0
        self.licensee = r.i32()
        if self.ue5 >= 1016:
            r.raw(20); r.i32()        # saved hash, total header size
        ncv = r.i32()
        self.custom = {}
        for _ in range(ncv):
            g = r.raw(16); v = r.i32()
            self.custom[g.hex()] = v
        if self.ue5 < 1016:
            self.total_header = r.i32()
        self.folder = r.fstr()
        self.flags = r.u32()
        self.editor_only_filtered = bool(self.flags & 0x80000000)
        self.name_count, self.name_off = r.i32(), r.i32()
        if self.ue5 >= 1008:
            r.i32(); r.i32()
        if not self.editor_only_filtered and self.ue4 >= 516:
            self.loc_id = r.fstr()
        if self.ue4 >= 459:
            r.i32(); r.i32()
        self.export_count, self.export_off = r.i32(), r.i32()
        self.import_count, self.import_off = r.i32(), r.i32()
        if self.ue5 >= 1015:
            r.i32(); r.i32(); r.i32(); r.i32()
        if self.ue5 >= 1014:
            r.i64()
        self.depends_off = r.i32()
        # rest of summary: soft package refs (384), searchable names (510), thumbnails, guid, ...
        if self.ue4 >= 384:
            r.i32(); r.i32()
        if self.ue4 >= 510:
            r.i32()
        r.i32()
        r.raw(16)
        if not self.editor_only_filtered and self.ue4 >= 518:
            r.raw(16)
            if self.ue4 < 520:
                r.raw(16)
        gens = r.i32(); r.raw(gens * 8)
        def ever():
            ma = struct.unpack_from('<HHHI', r.b, r.o); r.o += 10
            br = r.fstr()
            return '%d.%d.%d-%d+%s' % (ma[0], ma[1], ma[2], ma[3], br)
        self.saved_by = ever()
        self.compatible = ever()
        r.u32(); nch = r.i32(); r.raw(nch * 16)
        r.u32(); nadd = r.i32()
        for _ in range(nadd):
            r.fstr()
        self.asset_registry_off = r.i32()
        self.bulk_start = r.i64()
        # names
        self.names = []
        p = R(d, self.name_off)
        for _ in range(self.name_count):
            self.names.append(p.fstr())
            if self.ue4 >= 504:
                p.raw(4)
        # imports
        isz = 28
        if not self.editor_only_filtered and self.ue4 >= 520:
            isz += 8
        if self.ue5 >= 1003:
            isz += 4
        if self.import_count:
            span = self.export_off - self.import_off
            if span % self.import_count == 0 and 28 <= span // self.import_count <= 48:
                isz = span // self.import_count
        self.import_size = isz
        self.imports = []
        for k in range(self.import_count):
            o = self.import_off + k * isz
            cp, cn, outer, on = struct.unpack_from('<qqiq', d, o)
            self.imports.append(dict(class_package=self.fname(cp), class_name=self.fname(cn),
                                     outer=outer, name=self.fname(on)))
        # exports
        esz = None
        if self.export_count:
            span = self.depends_off - self.export_off
            # 68 bytes before bIsAsset and the preload dependencies (UE 4.13 engine content: 72)
            if span % self.export_count == 0 and 68 <= span // self.export_count <= 140:
                esz = span // self.export_count
        if esz is None:
            esz = 104 if self.ue5 == 0 else 112
        self.export_size = esz
        self.exports = []
        # TemplateIndex from VER_UE4_TemplateIndex_IN_COOKED_EXPORTS (508); 64-bit serial size and
        # offset from VER_UE4_64BIT_EXPORTMAP_SERIALSIZES (511) (UE 4.15 packs: 510)
        has_tmpl = self.ue4 >= 508 or self.ue5
        for k in range(self.export_count):
            o = self.export_off + k * esz
            if has_tmpl:
                cls, sup, tmpl, outer = struct.unpack_from('<4i', d, o)
                o2 = o + 16
            else:
                cls, sup, outer = struct.unpack_from('<3i', d, o)
                o2 = o + 12
            nm = struct.unpack_from('<q', d, o2)[0]
            if self.ue4 >= 511 or self.ue5:
                oflags, ssize, soff = struct.unpack_from('<Iqq', d, o2 + 8)
            else:
                oflags, ssize, soff = struct.unpack_from('<Iii', d, o2 + 8)
            self.exports.append(dict(index=k + 1, cls=cls, sup=sup, outer=outer, name=self.fname(nm),
                                     flags=oflags, size=ssize, offset=soff))

    # ---- names / references
    def fname(self, packed):
        i, num = packed & 0xffffffff, packed >> 32
        s = self.names[i] if 0 <= i < len(self.names) else '?%d' % i
        return s if num == 0 else '%s_%d' % (s, num - 1)

    def class_of(self, e):
        c = e['cls']
        if c < 0:
            return self.imports[-c - 1]['name']
        if c > 0:
            return self.exports[c - 1]['name']
        return 'Class'

    def obj_name(self, idx):
        if idx < 0:
            return self.imports[-idx - 1]['name']
        if idx > 0:
            return self.exports[idx - 1]['name']
        return None

    def obj_path(self, idx):
        """Full path of an object reference: package path for imports."""
        if idx == 0:
            return None
        parts = []
        while idx != 0:
            if idx < 0:
                im = self.imports[-idx - 1]; parts.append(im['name']); idx = im['outer']
            else:
                ex = self.exports[idx - 1]; parts.append(ex['name']); idx = ex['outer']
                if idx == 0:
                    parts.append('<self>')
        parts.reverse()
        return parts[0] + ('.' + ':'.join(parts[1:]) if len(parts) > 1 else '')

    def import_package(self, idx):
        """Outermost package name of an import index."""
        while idx < 0:
            im = self.imports[-idx - 1]
            if im['outer'] == 0:
                return im['name']
            idx = im['outer']
        return None

    # ---- tagged properties
    def props(self, e, start_skip=None):
        """Parse an export's tagged properties. Returns (list, end_offset)."""
        base = e['offset']
        tries = [start_skip] if start_skip is not None else ([0, 1] if self.ue5 >= 1011 else [0])
        last = None
        for s in tries:
            try:
                r = R(self.d, base + s)
                out = self._tags(r, base + e['size'])
                return out, r.o
            except Exception as ex:  # noqa
                last = ex
        raise last

    def _typename(self, r):
        n = self.fname(r.i64()); c = r.i32()
        inner = [self._typename(r) for _ in range(c)]
        return (n, inner)

    def _tags(self, r, end):
        out = []
        while True:
            if r.o + 8 > end:
                raise ValueError('ran off export')
            name = self.fname(r.i64())
            if name == 'None':
                return out
            t = dict(name=name)
            if self.ue5 >= 1012:
                tn = self._typename(r)
                t['type'] = tn[0]
                size = r.i32(); fl = r.u8()
                t['index'] = r.i32() if fl & 1 else 0
                if fl & 2:
                    r.raw(16)
                if fl & 4:
                    ext = r.u8()
                    if ext & 2:
                        r.u8(); r.u8()
                t['bool'] = bool(fl & 0x10)
                if tn[0] == 'StructProperty' and tn[1]:
                    t['struct'] = tn[1][0][0]
                elif tn[0] in ('ArrayProperty', 'SetProperty') and tn[1]:
                    t['inner'] = tn[1][0][0]
                    if tn[1][0][0] == 'StructProperty' and tn[1][0][1]:
                        t['inner_struct'] = tn[1][0][1][0][0]
                elif tn[0] in ('ByteProperty', 'EnumProperty') and tn[1]:
                    t['enum'] = tn[1][0][0]
            else:
                t['type'] = self.fname(r.i64())
                size = r.i32(); t['index'] = r.i32()
                ty = t['type']
                if ty == 'StructProperty':
                    t['struct'] = self.fname(r.i64())
                    if self.ue4 >= 441:
                        r.raw(16)
                elif ty == 'BoolProperty':
                    t['bool'] = bool(r.u8())
                elif ty in ('ByteProperty', 'EnumProperty'):
                    t['enum'] = self.fname(r.i64())
                elif ty in ('ArrayProperty', 'SetProperty'):
                    t['inner'] = self.fname(r.i64())
                elif ty == 'MapProperty':
                    r.i64(); r.i64()
                if self.ue4 >= 503:
                    if r.u8():
                        r.raw(16)
                if self.ue5 >= 1011:
                    ext = r.u8()
                    if ext & 2:
                        r.u8(); r.u8()
            if size < 0 or r.o + size > end:
                raise ValueError('bad size %s %d' % (name, size))
            t['size'] = size
            t['at'] = r.o
            self._value(t, r)
            r.o = t['at'] + size
            out.append(t)

    def _value(self, t, r):
        ty = t['type']; o = t['at']; d = self.d
        try:
            if ty == 'ObjectProperty' or ty == 'ClassProperty':
                t['value'] = struct.unpack_from('<i', d, o)[0]
            elif ty == 'FloatProperty':
                t['value'] = struct.unpack_from('<f', d, o)[0]
            elif ty == 'DoubleProperty':
                t['value'] = struct.unpack_from('<d', d, o)[0]
            elif ty in ('IntProperty',):
                t['value'] = struct.unpack_from('<i', d, o)[0]
            elif ty in ('UInt32Property',):
                t['value'] = struct.unpack_from('<I', d, o)[0]
            elif ty == 'NameProperty':
                t['value'] = self.fname(struct.unpack_from('<q', d, o)[0])
            elif ty in ('ByteProperty', 'EnumProperty'):
                if t['size'] == 8:
                    t['value'] = self.fname(struct.unpack_from('<q', d, o)[0])
                elif t['size'] == 1:
                    t['value'] = d[o]
            elif ty == 'StrProperty':
                t['value'] = R(d, o).fstr()
            elif ty == 'BoolProperty':
                t['value'] = t.get('bool')
            elif ty == 'SoftObjectProperty':
                rr = R(d, o)
                if self.ue5 >= 1007:
                    a = self.fname(rr.i64()); b = self.fname(rr.i64())
                    t['value'] = a + '.' + b
                else:
                    t['value'] = self.fname(rr.i64())
            elif ty == 'StructProperty':
                st = t.get('struct')
                if st in ('Vector', 'Rotator'):
                    if t['size'] == 24:
                        t['value'] = struct.unpack_from('<3d', d, o)
                    else:
                        t['value'] = struct.unpack_from('<3f', d, o)
                elif st == 'Vector2D' or st == 'IntPoint':
                    t['value'] = struct.unpack_from('<2d' if t['size'] == 16 and st == 'Vector2D' else ('<2f' if st == 'Vector2D' else '<2i'), d, o)
                elif st == 'LinearColor':
                    t['value'] = struct.unpack_from('<4f', d, o)
                elif st == 'Quat':
                    t['value'] = struct.unpack_from('<4d' if t['size'] == 32 else '<4f', d, o)
                elif st == 'Box':
                    if t['size'] >= 49:
                        t['value'] = struct.unpack_from('<6d', d, o)
                    else:
                        t['value'] = struct.unpack_from('<6f', d, o)
                elif st not in ('Guid', 'Color'):
                    rr = R(d, o)
                    try:
                        t['children'] = self._tags(rr, o + t['size'])
                    except Exception:
                        pass
            elif ty == 'ArrayProperty':
                rr = R(d, o)
                n = rr.i32(); t['count'] = n
                inner = t.get('inner')
                if inner == 'ObjectProperty' and 0 <= n < 100000:
                    t['value'] = list(struct.unpack_from('<%di' % n, d, rr.o))
                elif inner == 'StructProperty' and 0 <= n < 100000:
                    if self.ue5 < 1012:
                        # inner tag
                        rr.i64(); rr.i64(); rr.i32(); rr.i32()
                        t['inner_struct'] = self.fname(rr.i64())
                        rr.raw(16)
                        if self.ue4 >= 503 and rr.u8():
                            rr.raw(16)
                        if self.ue5 >= 1011:
                            ext = rr.u8()
                            if ext & 2:
                                rr.u8(); rr.u8()
                    items = []
                    st = t.get('inner_struct')
                    if st not in ('Vector', 'Guid', 'Color', 'LinearColor', 'Vector2D', 'IntPoint', 'Quat', 'Rotator', 'Box'):
                        for _ in range(n):
                            items.append(self._tags(rr, o + t['size']))
                        t['items'] = items
                elif inner == 'NameProperty' and 0 <= n < 100000:
                    t['value'] = [self.fname(struct.unpack_from('<q', d, rr.o + 8 * k)[0]) for k in range(n)]
        except Exception:
            pass


def find(props, name):
    for t in props:
        if t['name'] == name:
            return t
    return None


def walk(props):
    for t in props:
        yield t
        for c in t.get('children', []) or []:
            yield from walk([c])
        for it in t.get('items', []) or []:
            yield from walk(it)
