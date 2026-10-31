"""The game's native reflection, read from its executable.

UnrealHeaderTool compiles every reflected class, struct, enum, function and property into
constant tables (UE4CodeGen_Private, UE 4.27, shipping: no metadata fields). They sit in
the exe's data, joined by pointers the relocation table lists:

    FPackageParams    Name, Singletons, NumSingletons, PackageFlags, two CRCs
    FClassParams      NoRegister, ConfigName, CppClassInfo, Dependencies, FunctionLinks,
                      Properties, Interfaces, four counts, ClassFlags
    FStructParams     Outer, Super, StructOps, Name, SizeOf, AlignOf, Properties, count,
                      ObjectFlags, StructFlags
    FEnumParams       Outer, DisplayName, Name, CppType, Enumerators, count, ObjectFlags,
                      EnumFlags, DynamicType, CppForm
    FFunctionParams   Outer, Super, Name, OwningClass, DelegateName, StructureSize,
                      Properties, count, ObjectFlags, FunctionFlags, RPCId, RPCResponseId
    F*PropertyParams  Name, RepNotify, PropertyFlags, GenFlags, ObjectFlags, ArrayDim,
                      Offset (bool: ElementSize, SizeOfOuter), then the type's references

A reference to another type is a pointer to its Z_Construct function, which loads its
table's address with a RIP-relative LEA; a class reference is its StaticClass, whose
GetPrivateStaticClass passes the class's wide-string name in rdx and its package in rcx.

    python3 -m riftlord.types.exe [--out game.json] [--exe PATH]
"""

import bisect
import json
import os
import re
import struct
import sys
import time

#: EPropertyGenFlags type numbers (the low five bits).
GEN = ['Byte', 'Int8', 'Int16', 'Int', 'Int64', 'UInt16', 'UInt32', 'UInt64', 'UnsizedInt',
       'UnsizedUInt', 'Float', 'Double', 'Bool', 'SoftClass', 'WeakObject', 'LazyObject',
       'SoftObject', 'Class', 'Object', 'Interface', 'Name', 'Str', 'Array', 'Map', 'Set',
       'Struct', 'Delegate', 'InlineMulticastDelegate', 'SparseMulticastDelegate', 'Text',
       'Enum', 'FieldPath']
NATIVE_BOOL = 0x20
#: Canonical text of each type with no reference (bpkit.typedb's spelling).
SIMPLE = {'Int8': 'int8', 'Int16': 'int16', 'Int': 'int', 'Int64': 'int64', 'UInt16': 'uint16',
          'UInt32': 'uint32', 'UInt64': 'uint64', 'UnsizedInt': 'int', 'UnsizedUInt': 'uint32',
          'Float': 'float', 'Double': 'double', 'Bool': 'bool', 'Name': 'name', 'Str': 'str',
          'Text': 'text', 'FieldPath': 'fieldpath'}
#: Byte offset of the first type-specific field of each property table.
EXTRA_AT = 40
#: Type-specific fields: (field name, what it points at).
EXTRA = {'Byte': [('enum', 'enum')], 'Enum': [('enum', 'enum')],
         'Object': [('class', 'class')], 'WeakObject': [('class', 'class')],
         'LazyObject': [('class', 'class')], 'SoftObject': [('class', 'class')],
         'Class': [('meta_class', 'class'), ('class', 'class')],
         'SoftClass': [('meta_class', 'class')], 'Interface': [('class', 'class')],
         'Struct': [('struct', 'struct')], 'Delegate': [('signature', 'function')],
         'InlineMulticastDelegate': [('signature', 'function')],
         'SparseMulticastDelegate': [('signature', 'function')]}
#: Children each container reads after itself (ConstructFProperty's ReadMore).
CHILDREN = {'Array': ('inner',), 'Set': ('element',), 'Enum': ('underlying',),
            'Map': ('key', 'value')}

IDENT = re.compile(rb'[A-Za-z_][A-Za-z0-9_]*\Z')
PRINTABLE = re.compile(rb'[\x20-\x7e]*\Z')
#: What a package name starts with.
SCRIPT = '/Script/'
#: RF_Public | RF_Transient | RF_MarkAsNative: every function, struct and enum table's
#: ObjectFlags.
NATIVE_OBJECT_FLAGS = 0x45


class Image:
    """A PE32+ image: sections, relocations, function bounds, typed reads by address."""

    def __init__(self, path):
        with open(path, 'rb') as fh:
            self.data = fh.read()
        d = self.data
        pe = struct.unpack_from('<I', d, 0x3C)[0]
        count = struct.unpack_from('<H', d, pe + 6)[0]
        opt = struct.unpack_from('<H', d, pe + 20)[0]
        self.base = struct.unpack_from('<Q', d, pe + 24 + 24)[0]
        self.size = struct.unpack_from('<I', d, pe + 24 + 56)[0]
        dirs = pe + 24 + 112
        self.sections = []
        at = pe + 24 + opt
        for i in range(count):
            name, vsize, va, rsize, raw = struct.unpack_from('<8sIIII', d, at + i * 40)
            chars = struct.unpack_from('<I', d, at + i * 40 + 36)[0]
            self.sections.append((name.rstrip(b'\0').decode('ascii', 'replace'), va,
                                  max(vsize, rsize), raw, rsize, chars))
        self.sections.sort(key=lambda s: s[1])
        self._starts = [s[1] for s in self.sections]
        self.code = [(s[1], s[1] + s[2]) for s in self.sections if s[5] & 0x20000000]
        self.reloc = bytearray(self.size // 8 + 1)
        self.unaligned = 0
        rva, size = struct.unpack_from('<II', d, dirs + 5 * 8)
        at, end = self.offset(rva), self.offset(rva) + size
        while at < end:
            page, block = struct.unpack_from('<II', d, at)
            if block < 8:
                break
            for k in range(8, block, 2):
                e = struct.unpack_from('<H', d, at + k)[0]
                if e >> 12 == 10:
                    slot = page + (e & 0xFFF)
                    if slot % 8:
                        self.unaligned += 1
                    else:
                        self.reloc[slot // 8] = 1
            at += block
        rva, size = struct.unpack_from('<II', d, dirs + 3 * 8)
        at = self.offset(rva)
        funcs = sorted(struct.unpack_from('<II', d, at + k * 12) for k in range(size // 12))
        merged = []
        for begin, end in funcs:
            if merged and begin <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([begin, end])
        self.fstart = [f[0] for f in merged]
        self.fend = [f[1] for f in merged]

    def offset(self, rva):
        """File offset of an RVA, or None outside the file's bytes."""
        i = bisect.bisect_right(self._starts, rva) - 1
        if i < 0:
            return None
        _n, va, vsize, raw, rsize, _c = self.sections[i]
        if va <= rva < va + rsize:
            return raw + rva - va
        return None

    def rva(self, address):
        return address - self.base if address else None

    def is_code(self, address):
        r = address - self.base
        return any(a <= r < b for a, b in self.code)

    def readable(self, address):
        r = address - self.base
        return 0 <= r < self.size and self.offset(r) is not None

    def slot(self, address):
        """True when the relocation table lists a pointer at this address."""
        r = address - self.base
        return 0 <= r < self.size and not r % 8 and self.reloc[r // 8] == 1

    def u64(self, address):
        o = self.offset(address - self.base)
        return struct.unpack_from('<Q', self.data, o)[0] if o is not None else None

    def i32(self, address):
        o = self.offset(address - self.base)
        return struct.unpack_from('<i', self.data, o)[0] if o is not None else None

    def u32(self, address):
        o = self.offset(address - self.base)
        return struct.unpack_from('<I', self.data, o)[0] if o is not None else None

    def u16(self, address):
        o = self.offset(address - self.base)
        return struct.unpack_from('<H', self.data, o)[0] if o is not None else None

    def u8(self, address):
        o = self.offset(address - self.base)
        return self.data[o] if o is not None else None

    def ptr(self, address):
        """The pointer stored at `address` when one is listed there, 0 for a null, None
        when the field holds something else."""
        if self.slot(address):
            return self.u64(address)
        v = self.u64(address)
        return 0 if v == 0 else None

    def cstr(self, address, limit=512):
        """The NUL-terminated printable ASCII text at `address`, or None."""
        if not address or not self.readable(address):
            return None
        o = self.offset(address - self.base)
        end = self.data.find(b'\0', o, o + limit)
        if end < 0:
            return None
        raw = self.data[o:end]
        return raw.decode('ascii') if raw and PRINTABLE.match(raw) else None

    def wstr(self, address, limit=256):
        """The NUL-terminated UTF-16 ASCII text at `address`, or None."""
        if not address or not self.readable(address):
            return None
        o = self.offset(address - self.base)
        out = []
        for k in range(limit):
            c = struct.unpack_from('<H', self.data, o + 2 * k)[0]
            if c == 0:
                return ''.join(out) if out else None
            if not 0x20 <= c < 0x7F:
                return None
            out.append(chr(c))
        return None

    def function_end(self, address):
        """End address of the function holding `address` (.pdata), or address + 256."""
        r = address - self.base
        i = bisect.bisect_right(self.fstart, r) - 1
        if i >= 0 and self.fstart[i] <= r < self.fend[i]:
            return self.base + self.fend[i]
        return address + 256

    def follow(self, address, depth=4):
        """`address` past any leading jmp thunks."""
        for _ in range(depth):
            o = self.offset(address - self.base)
            if o is None:
                return address
            op = self.data[o]
            if op == 0xE9:
                address = address + 5 + struct.unpack_from('<i', self.data, o + 1)[0]
            elif op == 0xEB:
                address = address + 2 + struct.unpack_from('<b', self.data, o + 1)[0]
            elif op == 0x48 and self.data[o + 1] == 0xFF and self.data[o + 2] == 0x25:
                address = self.u64(address + 7 + struct.unpack_from('<i', self.data, o + 3)[0])
                if not address:
                    return None
            else:
                return address
        return address

    def leas(self, address, limit=640):
        """[(register, target)] of every RIP-relative 64-bit LEA in the function at
        `address`, in order, within `limit` bytes."""
        o = self.offset(address - self.base)
        if o is None:
            return []
        end = min(self.function_end(address), address + limit)
        d = self.data
        out = []
        n = end - address
        for k in range(max(0, n - 6)):
            b0 = d[o + k]
            if b0 in (0x48, 0x4C) and d[o + k + 1] == 0x8D and d[o + k + 2] & 0xC7 == 0x05:
                disp = struct.unpack_from('<i', d, o + k + 3)[0]
                reg = (d[o + k + 2] >> 3 & 7) + (8 if b0 == 0x4C else 0)
                out.append((reg, address + k + 7 + disp))
        return out

    def calls(self, address, limit=640):
        """Targets of every direct call (E8 rel32) in the function at `address`."""
        o = self.offset(address - self.base)
        if o is None:
            return []
        end = min(self.function_end(address), address + limit)
        out = []
        for k in range(max(0, end - address - 4)):
            if self.data[o + k] == 0xE8:
                out.append(address + k + 5 + struct.unpack_from('<i', self.data, o + k + 1)[0])
        return out


class Reader:
    """Every reflection table in one image, and the names that join them."""

    def __init__(self, image, log=None):
        self.im = image
        self.log = log or (lambda _s: None)
        self.packages = {}            # FPackageParams address -> name
        self.structs = {}             # FStructParams address -> raw table
        self.enums = {}               # FEnumParams address -> raw table
        self.functions = {}           # FFunctionParams address -> raw table
        self.classes = {}             # FClassParams address -> raw table
        self._table_of = {}           # code address -> table address
        self._class_name = {}         # code address -> (name, package)
        self.problems = []

    # -- finding tables -----------------------------------------------------------------

    def _code(self, address):
        return address is not None and address != 0 and self.im.is_code(address)

    def _code_or_null(self, value):
        return value == 0 or self._code(value)

    def scan(self):
        """Find every table in the image's data sections."""
        im = self.im
        names = {}
        for _n, va, vsize, _raw, _rsize, chars in im.sections:
            if chars & 0x20000000:
                continue
            first, last = va // 8, (va + vsize) // 8
            for k in range(first, min(last, len(im.reloc))):
                if not im.reloc[k]:
                    continue
                at = im.base + k * 8
                target = im.u64(at)
                if target is None:
                    continue
                if self._code(target):
                    self._try_class(at)
                    continue
                text = names.get(target)
                if text is None:
                    text = im.cstr(target) or ''
                    names[target] = text
                if not text:
                    continue
                if text.startswith(SCRIPT):
                    self._try_package(at, text)
                elif IDENT.match(text.encode()):
                    self._try_struct(at - 24, text) or self._try_enum(at - 16, text) or \
                        self._try_function(at - 16, text)
        self.log('tables: %d packages, %d classes, %d structs, %d enums, %d functions' % (
            len(self.packages), len(self.classes), len(self.structs), len(self.enums),
            len(self.functions)))

    def _props_ok(self, array, count):
        im = self.im
        if count < 0 or count > 20000:
            return False
        if count == 0:
            return array == 0 or array is not None
        if not array or not im.slot(array):
            return False
        first = im.u64(array)
        return bool(first) and im.slot(first) and im.cstr(im.u64(first)) is not None

    def _try_package(self, at, text):
        im = self.im
        singletons, count = im.ptr(at + 8), im.i32(at + 16)
        if singletons is None or count is None or count < 0 or (count and not singletons):
            return False
        self.packages[at] = text
        return True

    def _try_struct(self, at, name):
        im = self.im
        outer, sup, ops = im.ptr(at), im.ptr(at + 8), im.ptr(at + 16)
        if not self._code(outer) or not self._code_or_null(sup) or not self._code_or_null(ops):
            return False
        size, align = im.u64(at + 32), im.u64(at + 40)
        if not size or size >= 1 << 20 or align not in (1, 2, 4, 8, 16):
            return False
        props, count = im.ptr(at + 48), im.i32(at + 56)
        if props is None or not self._props_ok(props, count):
            return False
        if im.u32(at + 60) & NATIVE_OBJECT_FLAGS != NATIVE_OBJECT_FLAGS:
            return False
        self.structs[at] = {'name': name, 'outer': outer, 'super': sup, 'size': size,
                            'align': align, 'props': props, 'count': count,
                            'object_flags': im.u32(at + 60), 'struct_flags': im.u32(at + 64)}
        return True

    def _try_enum(self, at, name):
        im = self.im
        outer, display = im.ptr(at), im.ptr(at + 8)
        if not self._code(outer) or not self._code_or_null(display):
            return False
        cpp = im.cstr(im.ptr(at + 24) or 0)
        items, count = im.ptr(at + 32), im.i32(at + 40)
        if not cpp or not (cpp == name or cpp.startswith(name + '::')):
            return False
        if items is None or count is None or count < 0 or count > 4096:
            return False
        if count and (not items or im.cstr(im.ptr(items) or 0) is None):
            return False
        form = im.u8(at + 56)
        if form not in (0, 1, 2) or \
                im.u32(at + 44) & NATIVE_OBJECT_FLAGS != NATIVE_OBJECT_FLAGS:
            return False
        values = []
        for k in range(count):
            values.append((im.cstr(im.u64(items + 16 * k)), struct.unpack(
                '<q', struct.pack('<Q', im.u64(items + 16 * k + 8)))[0]))
        self.enums[at] = {'name': name, 'outer': outer, 'cpp': cpp, 'values': values,
                          'object_flags': im.u32(at + 44), 'enum_flags': im.u8(at + 48),
                          'form': form}
        return True

    def _try_function(self, at, name):
        im = self.im
        outer, sup = im.ptr(at), im.ptr(at + 8)
        if not self._code(outer) or not self._code_or_null(sup):
            return False
        owning, delegate = im.ptr(at + 24), im.ptr(at + 32)
        if owning is None or delegate is None:
            return False
        size = im.u64(at + 40)
        if size is None or size >= 1 << 16:
            return False
        props, count = im.ptr(at + 48), im.i32(at + 56)
        if props is None or not self._props_ok(props, count):
            return False
        if im.u32(at + 60) & NATIVE_OBJECT_FLAGS != NATIVE_OBJECT_FLAGS:
            return False
        self.functions[at] = {'name': name, 'outer': outer, 'super': sup,
                              'owning_class': im.cstr(owning) if owning else None,
                              'delegate_name': im.cstr(delegate) if delegate else None,
                              'size': size, 'props': props, 'count': count,
                              'object_flags': im.u32(at + 60), 'flags': im.u32(at + 64),
                              'rpc_id': im.u16(at + 68), 'rpc_response_id': im.u16(at + 70)}
        return True

    def _try_class(self, at):
        im = self.im
        config, info, deps = im.ptr(at + 8), im.ptr(at + 16), im.ptr(at + 24)
        if config is None or not info or not deps or self._code(info) or self._code(deps):
            return False
        if config and im.cstr(config) is None:
            return False
        links, props, faces = im.ptr(at + 32), im.ptr(at + 40), im.ptr(at + 48)
        if links is None or props is None or faces is None:
            return False
        ndeps, nfuncs, nprops, nfaces = (im.i32(at + 56 + 4 * k) for k in range(4))
        if not 1 <= ndeps <= 64 or not 0 <= nfuncs <= 5000 or not 0 <= nfaces <= 64:
            return False
        if bool(nfuncs) != bool(links) or bool(nfaces) != bool(faces):
            return False
        if not self._props_ok(props, nprops):
            return False
        dep_list = [im.ptr(deps + 8 * k) for k in range(ndeps)]
        if not all(self._code(d) for d in dep_list):
            return False
        link_list = []
        for k in range(nfuncs):
            create, fname = im.ptr(links + 16 * k), im.ptr(links + 16 * k + 8)
            if not self._code(create) or not fname or im.cstr(fname) is None:
                return False
            link_list.append((create, im.cstr(fname)))
        face_list = []
        for k in range(nfaces):
            fn = im.ptr(faces + 16 * k)
            if not self._code(fn):
                return False
            face_list.append((fn, im.i32(faces + 16 * k + 8), bool(im.u8(faces + 16 * k + 12))))
        self.classes[at] = {'no_register': im.ptr(at), 'config': im.cstr(config) if config
                            else None, 'deps': dep_list, 'links': link_list,
                            'props': props, 'count': nprops, 'interfaces': face_list,
                            'flags': im.u32(at + 72)}
        return True

    # -- joining tables -----------------------------------------------------------------

    def table_of(self, code, tables):
        """The table in `tables` the Z_Construct function at `code` loads, or None."""
        if not code:
            return None
        key = (code, id(tables))
        if key not in self._table_of:
            found = None
            start = self.im.follow(code)
            for _reg, target in self.im.leas(start) if start else ():
                if target in tables:
                    found = target
                    break
            self._table_of[key] = found
        return self._table_of[key]

    def class_name(self, code):
        """(name, package) of the class whose StaticClass is at `code`, or (None, None)."""
        if not code:
            return None, None
        if code not in self._class_name:
            self._class_name[code] = self._name_in(code, 2)
        return self._class_name[code]

    def _name_in(self, code, depth):
        im = self.im
        start = im.follow(code)
        if not start:
            return None, None
        name = package = None
        found = im.leas(start)
        for reg, target in found + [(2, t) for r, t in found if r != 2]:
            if reg == 2 and name is None:
                text = im.wstr(target)
                prefix = im.u16(target - 2)
                if text and IDENT.match(text.encode()) and prefix is not None and \
                        chr(prefix) in 'UAI_':
                    name = text
                    if prefix == ord('_') and im.wstr(target - 24) == 'UDEPRECATED_' + text:
                        name = 'DEPRECATED_' + text
            elif reg == 1 and package is None:
                text = im.wstr(target)
                if text and text.startswith(SCRIPT):
                    package = text
        if name is None and package is not None:
            # unoptimised code loads the whole TEXT("UName") and adds the prefix after
            for _reg, target in found:
                text = im.wstr(target)
                if text and re.match(r'[UA][A-Z]\w*\Z', text):
                    return text[1:], package
        if name is None and depth:
            last = (None, None)
            for target in im.calls(start, 64):
                if im.is_code(target):
                    got = self._name_in(target, depth - 1)
                    if got[0]:
                        last = got
            return last
        return name, package

    def package_of(self, code):
        """The '/Script/X' package a Z_Construct_UPackage function builds, or None."""
        at = self.table_of(code, self.packages)
        return self.packages.get(at) if at else None

    # -- properties --------------------------------------------------------------------

    def properties(self, array, count):
        """The property tree of a table's property array, in reflection order."""
        im = self.im
        entries = [im.u64(array + 8 * k) for k in range(count)] if count else []
        out = []
        pos = [len(entries)]

        def construct():
            pos[0] -= 1
            at = entries[pos[0]]
            p = self._property(at)
            for child in CHILDREN.get(p['kind'], ()):
                if pos[0] <= 0:
                    self.problems.append('%s: container without its %s' % (p['name'], child))
                    break
                p[child] = construct()
            return p

        while pos[0] > 0:
            out.append(construct())
        out.reverse()
        return out

    def _property(self, at):
        im = self.im
        gen = im.u32(at + 24)
        kind = GEN[gen & 0x1F] if gen is not None and gen & 0x1F < len(GEN) else 'Unknown'
        p = {'name': im.cstr(im.u64(at)), 'kind': kind,
             'rep_notify': im.cstr(im.ptr(at + 8) or 0),
             'flags': im.u64(at + 16), 'object_flags': im.u32(at + 28),
             'array_dim': im.i32(at + 32)}
        if kind == 'Bool':
            p['element_size'] = im.u32(at + 36)
            p['outer_size'] = im.u64(at + 40)
            p['native_bool'] = bool(gen & NATIVE_BOOL)
        else:
            p['offset'] = im.i32(at + 32 + 4)
        if kind in ('Array', 'Map'):
            p['container_flags'] = im.u8(at + 36 + 4)
        for k, (field, what) in enumerate(EXTRA.get(kind, ())):
            p[field] = (what, im.ptr(at + EXTRA_AT + 8 * k))
        return p

    # -- the result ---------------------------------------------------------------------

    def result(self):
        """Every table, named and joined, as plain data:

            {'classes': {name: {...}}, 'structs': {...}, 'enums': {...},
             'functions': {'Owner:Name': {...}}, 'delegates': {'Name': {...}}}
        """
        im = self.im
        t0 = time.time()
        struct_name = {a: s['name'] for a, s in self.structs.items()}
        enum_name = {a: e['name'] for a, e in self.enums.items()}
        class_of_params = {}
        for a, c in self.classes.items():
            name, package = self.class_name(c['no_register'])
            class_of_params[a] = name
            c['name'], c['package'] = name, package
        out = {'classes': {}, 'structs': {}, 'enums': {}, 'functions': {}, 'delegates': {},
               'duplicates': {'classes': [], 'structs': [], 'enums': []}}

        def ref(what, code):
            if not code:
                return None
            if what == 'class':
                return self.class_name(code)[0]
            if what == 'struct':
                return struct_name.get(self.table_of(code, self.structs))
            if what == 'enum':
                return enum_name.get(self.table_of(code, self.enums))
            at = self.table_of(code, self.functions)
            return self.functions[at]['name'] if at else None

        def plain(p):
            q = {k: v for k, v in p.items() if k not in CHILDREN.get(p['kind'], ())
                 and not isinstance(v, tuple)}
            for k, v in p.items():
                if isinstance(v, tuple):
                    q[k] = ref(*v)
                    if v[1] and q[k] is None:
                        self.problems.append('%s: %s not resolved' % (p['name'], k))
            for child in CHILDREN.get(p['kind'], ()):
                if child in p:
                    q[child] = plain(p[child])
            q['type'] = canonical(q)
            return q

        def module_of(code):
            pkg = self.package_of(code)
            return pkg[len(SCRIPT):] if pkg else None

        for a, s in self.structs.items():
            entry = {'super': ref('struct', s['super']), 'module': module_of(s['outer']),
                     'size': s['size'], 'align': s['align'], 'struct_flags': s['struct_flags'],
                     'properties': [plain(p) for p in self.properties(s['props'], s['count'])]}
            if s['name'] in out['structs']:
                out['duplicates']['structs'].append(s['name'])
            out['structs'][s['name']] = entry
        for a, e in self.enums.items():
            if e['name'] in out['enums']:
                out['duplicates']['enums'].append(e['name'])
            out['enums'][e['name']] = {'cpp': e['cpp'], 'form': e['form'],
                                       'module': module_of(e['outer']),
                                       'enum_flags': e['enum_flags'],
                                       'values': [v[0] for v in e['values']],
                                       'numbers': [v[1] for v in e['values']]}
        for a, c in self.classes.items():
            name = c['name']
            if not name:
                self.problems.append('class table at %#x has no name' % a)
                continue
            sup = None
            if len(c['deps']) > 1:
                sup_at = self.table_of(c['deps'][0], self.classes)
                sup = class_of_params.get(sup_at) if sup_at else \
                    self.class_name(c['deps'][0])[0]
                if sup is None:
                    self.problems.append('%s: super not resolved' % name)
            module = module_of(c['deps'][-1]) or (c['package'] or '')[len(SCRIPT):] or None
            entry = {'super': sup, 'module': module, 'flags': c['flags'],
                     'config': c['config'],
                     'interfaces': [{'class': self.class_name(f)[0], 'offset': off,
                                     'k2': k2} for f, off, k2 in c['interfaces']],
                     'functions': [n for _c, n in c['links']],
                     'properties': [plain(p) for p in self.properties(c['props'], c['count'])]}
            if name in out['classes']:
                out['duplicates']['classes'].append(name)
            out['classes'][name] = entry
            for create, fname in c['links']:
                fat = self.table_of(create, self.functions)
                if fat is None:
                    self.problems.append('%s:%s: function table not found' % (name, fname))
                    continue
                self.functions[fat]['owner'] = name
        for a, f in self.functions.items():
            owner = f.get('owner')
            if owner is None:
                cat = self.table_of(f['outer'], self.classes)
                owner = class_of_params.get(cat) if cat else None
            entry = {'flags': f['flags'], 'size': f['size'], 'owner': owner,
                     'module': None if owner else module_of(f['outer']),
                     'super': ref('function', f['super']), 'rpc_id': f['rpc_id'],
                     'rpc_response_id': f['rpc_response_id'],
                     'owning_class': f['owning_class'], 'delegate_name': f['delegate_name'],
                     'params': [plain(p) for p in self.properties(f['props'], f['count'])]}
            if f['name'].endswith('__DelegateSignature'):
                out['delegates']['%s:%s' % (owner or entry['module'], f['name'])] = entry
            elif owner:
                out['functions']['%s:%s' % (owner, f['name'])] = entry
            else:
                self.problems.append('function %s has no owner' % f['name'])
        for name, c in out['classes'].items():
            if c['module'] is None:
                self.problems.append('%s: module not resolved' % name)
        self.log('joined in %.1f s' % (time.time() - t0))
        out['problems'] = self.problems
        return out


def canonical(p):
    """bpkit.typedb's canonical type text of a plain property."""
    k = p['kind']
    if k in SIMPLE:
        return SIMPLE[k]
    if k == 'Byte':
        return 'enum:%s' % p['enum'] if p.get('enum') else 'byte'
    if k == 'Enum':
        return 'enum:%s' % p.get('enum')
    if k in ('Object', 'WeakObject', 'LazyObject', 'SoftObject'):
        return '%s:%s' % ({'Object': 'object', 'WeakObject': 'weakobject',
                           'LazyObject': 'lazyobject', 'SoftObject': 'softobject'}[k],
                          p.get('class'))
    if k == 'Class':
        return 'class:%s' % p.get('meta_class')
    if k == 'SoftClass':
        return 'softclass:%s' % p.get('meta_class')
    if k == 'Interface':
        return 'interface:%s' % p.get('class')
    if k == 'Struct':
        return 'struct:%s' % p.get('struct')
    if k == 'Delegate':
        return 'delegate:%s' % (p.get('signature') or '').replace('__DelegateSignature', '')
    if k in ('InlineMulticastDelegate', 'SparseMulticastDelegate'):
        return 'mcdelegate:%s' % (p.get('signature') or '').replace('__DelegateSignature', '')
    if k == 'Array':
        return 'array<%s>' % p['inner']['type']
    if k == 'Set':
        return 'set<%s>' % p['element']['type']
    if k == 'Map':
        return 'map<%s,%s>' % (p['key']['type'], p['value']['type'])
    return 'unknown:%s' % k


def read(path, log=None):
    """The reflection of the exe at `path` (Reader.result)."""
    log = log or (lambda _s: None)
    t0 = time.time()
    im = Image(path)
    log('image read in %.1f s (%d unaligned relocations)' % (time.time() - t0, im.unaligned))
    r = Reader(im, log)
    t1 = time.time()
    r.scan()
    log('scanned in %.1f s' % (time.time() - t1))
    return r.result()


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--exe', help='the game exe (default: the found install\'s)')
    ap.add_argument('--out', help='write the result as JSON here')
    args = ap.parse_args(argv)
    path = args.exe
    if not path:
        from riftlord.core import gamepath
        install = gamepath.find()
        if not install:
            raise SystemExit('no game install found')
        path = os.path.join(install, 'OMD', 'Binaries', 'Win64', 'OMD-Win64-Shipping.exe')
    got = read(path, log=lambda s: print(s, file=sys.stderr))
    for k in ('classes', 'structs', 'enums', 'functions', 'delegates'):
        print('%-10s %d' % (k, len(got[k])))
    print('problems  %d' % len(got['problems']))
    for p in got['problems'][:20]:
        print('  ', p)
    if args.out:
        with open(args.out, 'w') as fh:
            json.dump(got, fh)


if __name__ == '__main__':
    main()
