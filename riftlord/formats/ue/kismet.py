"""Disassembler for UE4 Kismet (Blueprint) bytecode in cooked packages.

Written before any emitter, because the only way to trust an opcode table is to
walk a real stream with it: a correct walk consumes every byte and lands exactly
on EX_EndOfScript. Anything else means an operand size is wrong, and a generator
built on a wrong table produces Blueprints that load and then misbehave.

Two things about cooked bytecode:
  * object and property pointers serialise as FPackageIndex (4 bytes) although
    they occupy 8 in memory -- which is why a function stores both a memory size
    and a serialised size.
  * the stream is not aligned; operands follow their opcode immediately.
"""

import struct

# Opcode table. Values are from Script.h; the walk validates them.
EX = {
    0x00: 'LocalVariable', 0x01: 'InstanceVariable', 0x02: 'DefaultVariable',
    0x04: 'Return', 0x06: 'Jump', 0x07: 'JumpIfNot', 0x09: 'Assert',
    0x0B: 'Nothing', 0x0F: 'Let', 0x12: 'ClassContext', 0x13: 'MetaCast',
    0x14: 'LetBool', 0x15: 'EndParmValue', 0x16: 'EndFunctionParms',
    0x17: 'Self', 0x18: 'Skip', 0x19: 'Context', 0x1A: 'Context_FailSilent',
    0x1B: 'VirtualFunction', 0x1C: 'FinalFunction', 0x1D: 'IntConst',
    0x1E: 'FloatConst', 0x1F: 'StringConst', 0x20: 'ObjectConst',
    0x21: 'NameConst', 0x22: 'RotationConst', 0x23: 'VectorConst',
    0x24: 'ByteConst', 0x25: 'IntZero', 0x26: 'IntOne', 0x27: 'True',
    0x28: 'False', 0x29: 'TextConst', 0x2A: 'NoObject', 0x2B: 'TransformConst',
    0x2C: 'IntConstByte', 0x2D: 'NoInterface', 0x2E: 'DynamicCast',
    0x2F: 'StructConst', 0x30: 'EndStructConst', 0x31: 'SetArray',
    0x32: 'EndArray', 0x33: 'PropertyConst', 0x34: 'UnicodeStringConst',
    0x35: 'Int64Const', 0x36: 'UInt64Const', 0x38: 'PrimitiveCast',
    0x39: 'SetSet', 0x3A: 'EndSet', 0x3B: 'SetMap', 0x3C: 'EndMap',
    0x3D: 'SetConst', 0x3E: 'EndSetConst', 0x3F: 'MapConst', 0x40: 'EndMapConst',
    0x42: 'StructMemberContext', 0x43: 'LetMulticastDelegate',
    0x44: 'LetDelegate', 0x45: 'LocalVirtualFunction',
    0x46: 'LocalFinalFunction', 0x48: 'LocalOutVariable',
    0x4A: 'DeprecatedOp4A', 0x4B: 'InstanceDelegate',
    0x4C: 'PushExecutionFlow', 0x4D: 'PopExecutionFlow', 0x4E: 'ComputedJump',
    0x4F: 'PopExecutionFlowIfNot', 0x50: 'Breakpoint', 0x51: 'InterfaceContext',
    0x52: 'ObjToInterfaceCast', 0x53: 'EndOfScript',
    0x54: 'CrossInterfaceCast', 0x55: 'InterfaceToObjCast',
    0x5A: 'WireTracepoint', 0x5B: 'SkipOffsetConst', 0x5C: 'AddMulticastDelegate',
    0x5D: 'ClearMulticastDelegate', 0x5E: 'Tracepoint', 0x5F: 'LetObj',
    0x60: 'LetWeakObjPtr', 0x61: 'BindDelegate', 0x62: 'RemoveMulticastDelegate',
    0x63: 'CallMulticastDelegate', 0x64: 'LetValueOnPersistentFrame',
    0x65: 'ArrayConst', 0x66: 'EndArrayConst', 0x67: 'SoftObjectConst',
    0x68: 'CallMath', 0x69: 'SwitchValue', 0x6A: 'InstrumentationEvent',
    0x6B: 'ArrayGetByRef', 0x6C: 'ClassSparseDataVariable',
    0x6D: 'FieldPathConst',
}


class Node:
    """One opcode and its operands, in the exact order they appear.

    ``ops`` holds ('u8', v) / ('i32', v) / ('f32', v) / ('cstr', s) primitives and
    ('expr', Node) children, interleaved as read. Keeping the order verbatim is
    what lets the emitter rebuild the stream byte for byte -- and what lets a
    caller change one constant without understanding the rest.
    """

    __slots__ = ('op', 'name', 'ops', 'offset', 'fieldpaths', 'objptrs')

    def __init__(self, op, name, offset):
        self.op = op
        self.name = name
        self.offset = offset
        self.fieldpaths = []     # element count of each FFieldPath read here
        self.objptrs = 0         # FPackageIndex object references read here
        self.ops = []

    def __repr__(self):
        return '<%s %d ops>' % (self.name, len(self.ops))

    def walk(self):
        """This node and every descendant, depth first."""
        yield self
        for kind, val in self.ops:
            if kind == 'expr':
                for n in val.walk():
                    yield n


class Walker:
    def __init__(self, data, names=None, resolve=None):
        self.d = data
        self.o = 0
        self.names = names or []
        self.resolve = resolve or (lambda i: str(i))
        self.out = []
        self.unknown = []
        self.nodes = []          # top-level statements
        self._stack = []         # node being filled

    def _rec(self, kind, value):
        if self._stack:
            self._stack[-1].ops.append((kind, value))
        return value

    # -- primitive reads -------------------------------------------------
    def u8(self):
        v = self.d[self.o]; self.o += 1; return self._rec('u8', v)

    def i32(self):
        v = struct.unpack_from('<i', self.d, self.o)[0]; self.o += 4
        return self._rec('i32', v)

    def u32(self):
        v = struct.unpack_from('<I', self.d, self.o)[0]; self.o += 4
        return self._rec('u32', v)

    def i64(self):
        v = struct.unpack_from('<q', self.d, self.o)[0]; self.o += 8
        return self._rec('i64', v)

    def f32(self):
        v = struct.unpack_from('<f', self.d, self.o)[0]; self.o += 4
        return self._rec('f32', v)

    def ptr(self):
        """An OBJECT reference (function, class, struct): FPackageIndex."""
        if self._stack:
            self._stack[-1].objptrs += 1
        return self.resolve(self.i32())

    def field(self):
        """A PROPERTY reference.

        Since 4.25 an FProperty is not a UObject, so it cannot be an
        FPackageIndex. It serialises as an FFieldPath: int32 count, that many
        FNames naming the path, then an FPackageIndex for the owning struct.
        """
        n = self.i32()
        if self._stack:
            self._stack[-1].fieldpaths.append(max(n, 0))
        parts = [self.fname() for _ in range(max(n, 0))]
        owner = self.i32()
        return '.'.join(parts) if parts else '(none)' 

    def fname(self):
        idx = self.u32(); self.u32()
        return self.names[idx] if idx < len(self.names) else '?name%d' % idx

    def cstr(self):
        start = self.o
        while self.d[self.o] != 0:
            self.o += 1
        text = self.d[start:self.o].decode('utf-8', 'replace')
        self.o += 1
        return self._rec('cstr', text)

    # -- expression walk -------------------------------------------------
    def expr(self, depth=0):
        start = self.o
        op = self.d[self.o]; self.o += 1        # opcode itself is not an operand
        node = Node(op, EX.get(op, 'Unknown_%02X' % op), start)
        if self._stack:
            self._stack[-1].ops.append(('expr', node))
        else:
            self.nodes.append(node)
        self._stack.append(node)
        try:
            return self._expr_body(op, start, depth, node)
        finally:
            self._stack.pop()

    def _expr_body(self, op, start, depth, node):
        name = EX.get(op)
        if name is None:
            self.unknown.append((start, op))
            raise ValueError('unknown opcode 0x%02X at %d' % (op, start))
        emit = lambda text: self.out.append((start, depth, text))

        if name in ('LocalVariable', 'InstanceVariable', 'DefaultVariable',
                    'LocalOutVariable', 'ClassSparseDataVariable', 'PropertyConst'):
            emit('%s %s' % (name, self.field()))
        elif name in ('Nothing', 'EndFunctionParms', 'EndParmValue', 'Self',
                      'IntZero', 'IntOne', 'True', 'False', 'NoObject',
                      'NoInterface', 'EndStructConst', 'EndArray', 'EndArrayConst',
                      'EndSet', 'EndMap', 'EndSetConst', 'EndMapConst',
                      'PopExecutionFlow', 'EndOfScript', 'Breakpoint',
                      'WireTracepoint', 'Tracepoint', 'DeprecatedOp4A'):
            emit(name)
        elif name == 'Return':
            emit(name); self.expr(depth + 1)
        elif name in ('Jump', 'PushExecutionFlow', 'SkipOffsetConst'):
            emit('%s -> %d' % (name, self.u32()))
        elif name == 'JumpIfNot':
            emit('%s -> %d' % (name, self.u32())); self.expr(depth + 1)
        elif name == 'PopExecutionFlowIfNot':
            # Pops the flow stack when the condition is false. Unlike JumpIfNot it
            # carries NO offset -- just the condition expression.
            emit(name); self.expr(depth + 1)
        elif name == 'ComputedJump':
            emit(name); self.expr(depth + 1)
        elif name == 'LetValueOnPersistentFrame':
            # destination property, then ONE value expression -- not two.
            emit('%s %s' % (name, self.field())); self.expr(depth + 1)
        elif name in ('Let', 'LetObj', 'LetWeakObjPtr', 'LetBool', 'LetDelegate',
                      'LetMulticastDelegate'):
            if name == 'Let':
                emit('%s %s' % (name, self.field()))
            else:
                emit(name)
            self.expr(depth + 1); self.expr(depth + 1)
        elif name in ('IntConst',):
            emit('%s %d' % (name, self.i32()))
        elif name == 'FloatConst':
            emit('%s %g' % (name, self.f32()))
        elif name == 'ByteConst' or name == 'IntConstByte':
            emit('%s %d' % (name, self.u8()))
        elif name == 'Int64Const':
            emit('%s %d' % (name, self.i64()))
        elif name == 'UInt64Const':
            emit('%s %d' % (name, self.i64()))
        elif name == 'ObjectConst':
            emit('%s %s' % (name, self.ptr()))
        elif name == 'SoftObjectConst':
            emit(name); self.expr(depth + 1)
        elif name == 'NameConst':
            emit('%s %s' % (name, self.fname()))
        elif name == 'StringConst':
            emit('%s "%s"' % (name, self.cstr()))
        elif name == 'VectorConst':
            emit('%s (%g,%g,%g)' % (name, self.f32(), self.f32(), self.f32()))
        elif name == 'RotationConst':
            emit('%s (%g,%g,%g)' % (name, self.f32(), self.f32(), self.f32()))
        elif name == 'TransformConst':
            vals = [self.f32() for _ in range(10)]
            emit('%s %s' % (name, vals))
        elif name == 'StructConst':
            p = self.ptr(); size = self.i32()
            emit('%s %s size=%d' % (name, p, size))
            while self.peek() != 0x30:
                self.expr(depth + 1)
            self.expr(depth + 1)
        elif name in ('FinalFunction', 'LocalFinalFunction', 'CallMath'):
            emit('%s %s' % (name, self.ptr()))
            self.parms(depth + 1)
        elif name in ('VirtualFunction', 'LocalVirtualFunction'):
            emit('%s %s' % (name, self.fname()))
            self.parms(depth + 1)
        elif name in ('Context', 'Context_FailSilent', 'ClassContext'):
            emit(name)
            self.expr(depth + 1)              # object
            self.u32()                        # skip-over size
            self.field()                      # r-value property
            self.expr(depth + 1)              # member expression
        elif name == 'InterfaceContext':
            # Unlike Context: no skip size, no property, just the interface value.
            emit(name); self.expr(depth + 1)
        elif name == 'StructMemberContext':
            emit('%s %s' % (name, self.field())); self.expr(depth + 1)
        elif name in ('DynamicCast', 'MetaCast', 'ObjToInterfaceCast',
                      'CrossInterfaceCast', 'InterfaceToObjCast'):
            emit('%s %s' % (name, self.ptr())); self.expr(depth + 1)
        elif name == 'PrimitiveCast':
            emit('%s kind=%d' % (name, self.u8())); self.expr(depth + 1)
        elif name in ('SetArray', 'SetSet'):
            emit(name); self.expr(depth + 1)
            while self.peek() not in (0x32, 0x3A):
                self.expr(depth + 1)
            self.expr(depth + 1)
        elif name == 'SetMap':
            emit(name); self.expr(depth + 1)
            while self.peek() != 0x3C:
                self.expr(depth + 1)
            self.expr(depth + 1)
        elif name in ('ArrayConst', 'SetConst'):
            p = self.field(); n = self.i32()
            emit('%s %s count=%d' % (name, p, n))
            for _ in range(n):
                self.expr(depth + 1)
            self.expr(depth + 1)
        elif name == 'MapConst':
            kp = self.field(); vp = self.field(); n = self.i32()
            emit('%s %s/%s count=%d' % (name, kp, vp, n))
            for _ in range(n * 2):
                self.expr(depth + 1)
            self.expr(depth + 1)
        elif name == 'SwitchValue':
            n = self.u16(); end = self.u32()
            emit('%s cases=%d end=%d' % (name, n, end))
            self.expr(depth + 1)                    # index expression
            for _ in range(n):
                self.expr(depth + 1)                # case value
                self.u32()                          # offset to next case
                self.expr(depth + 1)                # case result
            self.expr(depth + 1)                    # default
        elif name == 'ArrayGetByRef':
            emit(name); self.expr(depth + 1); self.expr(depth + 1)
        elif name in ('AddMulticastDelegate', 'RemoveMulticastDelegate'):
            emit(name); self.expr(depth + 1); self.expr(depth + 1)
        elif name == 'ClearMulticastDelegate':
            emit(name); self.expr(depth + 1)
        elif name == 'BindDelegate':
            # The bound function's name, then the delegate and the object.
            emit('%s %s' % (name, self.fname()))
            self.expr(depth + 1); self.expr(depth + 1)
        elif name == 'CallMulticastDelegate':
            emit('%s %s' % (name, self.ptr())); self.expr(depth + 1)
            self.parms(depth + 1)
        elif name == 'InstanceDelegate':
            emit('%s %s' % (name, self.fname()))
        elif name == 'Assert':
            emit('%s line=%d' % (name, self.u16())); self.u8(); self.expr(depth + 1)
        elif name == 'Skip':
            emit('%s -> %d' % (name, self.u32())); self.expr(depth + 1)
        elif name == 'TextConst':
            # Operands follow EBlueprintTextLiteralType: 0 Empty, 1 Localized
            # (source, key, namespace), 2 Invariant and 3 LiteralString (one
            # string), 4 StringTableEntry (table object, then table id and key).
            kind = self.u8()
            emit('%s kind=%d' % (name, kind))
            if kind == 1:
                for _ in range(3):
                    self.expr(depth + 1)
            elif kind in (2, 3):
                self.expr(depth + 1)
            elif kind == 4:
                self.ptr(); self.expr(depth + 1); self.expr(depth + 1)
            elif kind != 0:
                raise ValueError('TextConst kind %d at %d' % (kind, start))
        elif name == 'FieldPathConst':
            emit(name); self.expr(depth + 1)
        else:
            self.unknown.append((start, op))
            raise ValueError('opcode %s (0x%02X) at %d has no rule' % (name, op, start))
        return name

    def u16(self):
        v = struct.unpack_from('<H', self.d, self.o)[0]; self.o += 2
        return self._rec('u16', v)

    def peek(self):
        return self.d[self.o] if self.o < len(self.d) else -1

    def parms(self, depth):
        while self.peek() != 0x16:
            self.expr(depth)
        self.expr(depth)

    def run(self):
        while self.o < len(self.d):
            if self.expr(0) == 'EndOfScript':
                break
        return self


def find_bytecode(data, names, resolve):
    """Locate a UFunction's script within its export payload.

    A function stores (BytecodeSizeInMemory, SerializedScriptSize) just before the
    script, but scanning for that pair alone produces false matches -- a small
    function like Onslaught_Start has three plausible pairs, and the first is
    wrong. So every candidate is tried and only one that disassembles cleanly to
    EndOfScript, consuming exactly its stated length, is accepted.

    Returns (offset, memory_size, serialized_size, Walker) or None.
    """
    best = None
    for off in range(0, max(len(data) - 8, 0)):
        mem, size = struct.unpack_from('<ii', data, off)
        end = off + 8 + size
        # Keep the pre-filter loose: a tiny stub's script can be a handful of
        # bytes, and an earlier floor of 16 discarded 22 real functions. The walk
        # below is the real filter -- it must consume the stated length exactly
        # and finish on EndOfScript, which a false match essentially never does.
        if not (2 <= size and end <= len(data) and 0 <= len(data) - end <= 64):
            continue
        if not (0 < mem <= size * 8):
            continue
        try:
            w = Walker(data[off + 8:end], names, resolve).run()
        except Exception:
            continue
        if w.o == size and w.out and w.out[-1][2] == 'EndOfScript':
            # and the nodes' in-memory size is the one stated: HubBar's Settle
            # had a false pair at 92 that walked cleanly to its stated length
            try:
                if sum(node_sizes(n)[1] for n in w.nodes) != mem:
                    continue
            except Exception:
                continue
            best = (off, mem, size, w)
            break
    return best


PACK = {'u8': '<B', 'u16': '<H', 'i32': '<i', 'u32': '<I',
        'i64': '<q', 'f32': '<f'}


def emit(nodes):
    """Rebuild a bytecode stream from a node tree.

    Operands were recorded in read order, so writing them back in that order
    reproduces the original exactly -- which is the test: parse a real script,
    emit it, and require the bytes to match. Nothing here needs to understand
    what an opcode means, so the emitter cannot drift from the disassembler.
    """
    out = bytearray()

    def write(node):
        out.append(node.op)
        for kind, val in node.ops:
            if kind == 'expr':
                write(val)
            elif kind == 'cstr':
                out.extend(val.encode('utf-8'))
                out.append(0)
            else:
                out.extend(struct.pack(PACK[kind], val))

    for n in nodes:
        write(n)
    return bytes(out)


def roundtrip(data, names, resolve):
    """Parse then re-emit; returns (ok, walker, rebuilt)."""
    w = Walker(data, names, resolve).run()
    rebuilt = emit(w.nodes)
    return rebuilt == data[:w.o], w, rebuilt


# Jump targets, ComputedJump entry points and PushExecutionFlow offsets are all
# expressed in the script's IN-MEMORY layout, not its serialised one. The two
# differ per reference, and each node records what it read:
#
#   object pointer   FPackageIndex, 4 bytes on disk, an 8-byte pointer in memory
#   property path    FFieldPath, int32 count + count x FName + int32 owner, so
#                    8 + 8*count on disk against one 8-byte pointer in memory
#
# Counting paths rather than assuming one name each is what makes this exact:
# a zero-name path costs nothing, and assuming -8 for it put the tail of
# BP_OMDOnslaughtCoordinator's ubergraph 40 bytes adrift.


#: Opcodes carrying a standalone FName. An FName serialises into bytecode as two
#: uint32 -- 8 bytes -- but occupies 12 in memory in this build, so each of these
#: costs 4 more than the serialised form. Measured, not assumed: the gap between
#: every function's DECLARED memory size (the two int32 the engine writes ahead of
#: the script) and the computed one was always 4 x the count of these opcodes,
#: across 217 functions of the behaviour-tree and coordinator assets.
#:
#: An FFieldPath's FNames are NOT included: the whole path collapses to a single
#: FProperty pointer in memory, which `fieldpaths` already accounts for.
FNAME_OPS = {'NameConst', 'VirtualFunction', 'LocalVirtualFunction',
             'InstanceDelegate', 'BindDelegate'}


def node_sizes(node):
    """(serialised, in-memory) byte size of a node and everything under it."""
    ser = 1                      # the opcode
    for kind, val in node.ops:
        if kind == 'expr':
            s, _ = node_sizes(val)
            ser += s
        elif kind == 'cstr':
            ser += len(val.encode('utf-8')) + 1
        else:
            ser += struct.calcsize(PACK[kind])
    mem = ser
    for n in node.walk():
        mem += 4 * n.objptrs
        mem -= 8 * sum(n.fieldpaths)
        if n.name in FNAME_OPS:
            mem += 4
    return ser, mem


def memory_map(nodes):
    """Annotate top-level statements with their in-memory offsets.

    Returns [(memory_offset, serialised_offset, node)], which is what turns a
    jump target into a statement you can actually point at.
    """
    out = []
    mem = 0
    for n in nodes:
        out.append((mem, n.offset, n))
        _, m = node_sizes(n)
        mem += m
    return out


#: Opcodes whose direct u32 operands are absolute in-memory code offsets.
#: Context's u32 is a relative skip size, not a target, so it is not here.
ABS_OFFSET_OPS = {'Jump', 'JumpIfNot', 'PushExecutionFlow', 'SkipOffsetConst',
                  'SwitchValue'}
#: Opcodes carrying an offset this module cannot recompute. A function using one
#: is refused rather than silently mis-linked.
UNRELOCATABLE_OPS = {'Skip', 'Context', 'Context_FailSilent', 'ClassContext'}


def code_offsets(nodes):
    """[(node, op_index)] for every operand that is an absolute code offset."""
    out = []

    def visit(n):
        if n.name in ABS_OFFSET_OPS:
            out.extend((n, i) for i, (k, _v) in enumerate(n.ops) if k == 'u32')
        for k, v in n.ops:
            if k == 'expr':
                visit(v)
    for n in nodes:
        visit(n)
    return out


def unrelocatable(nodes):
    """Names of opcodes present that block relocation, if any."""
    found = set()
    for top in nodes:
        for n in top.walk():
            if n.name in UNRELOCATABLE_OPS:
                found.add(n.name)
    return found


def shift_table(before, after, on_removed='next'):
    """translate(old_offset) -> new_offset, across an edit to the statement list.

    `before` and `after` are memory_map results; a statement is matched by
    identity, so an inserted statement needs no entry. Offsets inside a statement
    move with it, which is what keeps a SwitchValue's internal case chain correct.

    An offset landing in a *removed* statement resolves by `on_removed`:
    'next' sends it to the first surviving statement after it -- where control
    would have fallen through, which is what removing a ladder rung wants --
    while 'raise' refuses.
    """
    new_of = {id(n): m for m, _s, n in after}
    spans = []
    for i, (m, _s, n) in enumerate(before):
        end = before[i + 1][0] if i + 1 < len(before) else None
        spans.append((m, end, new_of.get(id(n))))
    end_before = (before[-1][0] + node_sizes(before[-1][2])[1]) if before else 0
    end_after = (after[-1][0] + node_sizes(after[-1][2])[1]) if after else 0

    def survivor_after(i):
        for start, _end, new in spans[i + 1:]:
            if new is not None:
                return new
        return end_after

    def translate(off):
        if off == end_before:
            return end_after
        for i, (start, end, new) in enumerate(spans):
            if off < start or (end is not None and off >= end):
                continue
            if new is not None:
                return off - start + new
            if on_removed == 'next':
                return survivor_after(i)
            raise KeyError('offset %d is inside a removed statement' % off)
        raise KeyError('offset %d is outside the function' % off)
    return translate


def relocate(nodes, translate, positions=None):
    """Rewrite every absolute code offset. Returns the count.

    An operand that is a Node rather than an int is a jump to a statement with no
    old offset -- a newly built one -- and resolves through `positions`, a
    {id(node): memory offset} map of the edited statement list. Everything else
    goes through `translate`.
    """
    n = 0
    for node, i in code_offsets(nodes):
        kind, old = node.ops[i]
        if isinstance(old, Node):
            if not positions or id(old) not in positions:
                raise KeyError('jump target node is not a statement in this function')
            new = positions[id(old)]
        else:
            new = translate(old)
        node.ops[i] = (kind, new)
        n += 1
    return n


def statement_at(nodes, memory_offset):
    """The statement a jump to this memory offset lands on, or None."""
    for mem, ser, n in memory_map(nodes):
        if mem == memory_offset:
            return mem, ser, n
    return None
