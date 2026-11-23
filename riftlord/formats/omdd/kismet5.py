"""Kismet (Blueprint) bytecode of Orcs Must Die! Deathtrap (UE 5.3): walked into nodes and
written back byte for byte.

A function's script is a run of expressions ending with EX_EndOfScript. Each expression is an
opcode byte followed by its operands in a fixed order (UE's ScriptSerialization.h); `OPERANDS`
lists that order per opcode. Operand kinds, with their serialized and in-memory sizes (the
UStruct stores both totals before the script):

    u8 u16 u32 i32 i64 u64 f32 f64   fixed numbers, the same size on disk and in memory
    obj    an object (function, class, struct, asset): FPackageIndex i32, 8 bytes in memory
    name   an FName: name map index u32 + number u32, 12 bytes in memory (FScriptName)
    field  a property (FField): FFieldPath, a count, that many FNames and the owning struct's
           FPackageIndex; 8 bytes in memory
    str    an ANSI string up to and including its zero byte
    ustr   a UTF-16 string up to and including its zero code unit
    expr   a nested expression

Measured on every shipped function (docs/research/DEATHTRAP-MAPS.md "P2 Blueprint writer"):
vectors, rotators and transforms are doubles (large world coordinates), EX_DoubleConst holds a
double, EX_Cast (UE4's PrimitiveCast) takes a conversion byte and one expression.

Jump and skip operands are offsets into the in-memory script, so a script written back with
the same nodes keeps them valid; code that builds new scripts computes them with `sizes`.
"""

import struct

#: EExprToken (UE 5.3 Script.h) -> name.
EX = {
    0x00: 'LocalVariable', 0x01: 'InstanceVariable', 0x02: 'DefaultVariable',
    0x04: 'Return', 0x06: 'Jump', 0x07: 'JumpIfNot', 0x09: 'Assert', 0x0B: 'Nothing',
    0x0C: 'NothingInt32', 0x0F: 'Let', 0x11: 'BitFieldConst', 0x12: 'ClassContext',
    0x13: 'MetaCast', 0x14: 'LetBool', 0x15: 'EndParmValue', 0x16: 'EndFunctionParms',
    0x17: 'Self', 0x18: 'Skip', 0x19: 'Context', 0x1A: 'Context_FailSilent',
    0x1B: 'VirtualFunction', 0x1C: 'FinalFunction', 0x1D: 'IntConst', 0x1E: 'FloatConst',
    0x1F: 'StringConst', 0x20: 'ObjectConst', 0x21: 'NameConst', 0x22: 'RotationConst',
    0x23: 'VectorConst', 0x24: 'ByteConst', 0x25: 'IntZero', 0x26: 'IntOne', 0x27: 'True',
    0x28: 'False', 0x29: 'TextConst', 0x2A: 'NoObject', 0x2B: 'TransformConst',
    0x2C: 'IntConstByte', 0x2D: 'NoInterface', 0x2E: 'DynamicCast', 0x2F: 'StructConst',
    0x30: 'EndStructConst', 0x31: 'SetArray', 0x32: 'EndArray', 0x33: 'PropertyConst',
    0x34: 'UnicodeStringConst', 0x35: 'Int64Const', 0x36: 'UInt64Const', 0x37: 'DoubleConst',
    0x38: 'Cast', 0x39: 'SetSet', 0x3A: 'EndSet', 0x3B: 'SetMap', 0x3C: 'EndMap',
    0x3D: 'SetConst', 0x3E: 'EndSetConst', 0x3F: 'MapConst', 0x40: 'EndMapConst',
    0x41: 'Vector3fConst', 0x42: 'StructMemberContext', 0x43: 'LetMulticastDelegate',
    0x44: 'LetDelegate', 0x45: 'LocalVirtualFunction', 0x46: 'LocalFinalFunction',
    0x48: 'LocalOutVariable', 0x4A: 'DeprecatedOp4A', 0x4B: 'InstanceDelegate',
    0x4C: 'PushExecutionFlow', 0x4D: 'PopExecutionFlow', 0x4E: 'ComputedJump',
    0x4F: 'PopExecutionFlowIfNot', 0x50: 'Breakpoint', 0x51: 'InterfaceContext',
    0x52: 'ObjToInterfaceCast', 0x53: 'EndOfScript', 0x54: 'CrossInterfaceCast',
    0x55: 'InterfaceToObjCast', 0x5A: 'WireTracepoint', 0x5B: 'SkipOffsetConst',
    0x5C: 'AddMulticastDelegate', 0x5D: 'ClearMulticastDelegate', 0x5E: 'Tracepoint',
    0x5F: 'LetObj', 0x60: 'LetWeakObjPtr', 0x61: 'BindDelegate',
    0x62: 'RemoveMulticastDelegate', 0x63: 'CallMulticastDelegate',
    0x64: 'LetValueOnPersistentFrame', 0x65: 'ArrayConst', 0x66: 'EndArrayConst',
    0x67: 'SoftObjectConst', 0x68: 'CallMath', 0x69: 'SwitchValue',
    0x6A: 'InstrumentationEvent', 0x6B: 'ArrayGetByRef', 0x6C: 'ClassSparseDataVariable',
    0x6D: 'FieldPathConst',
}
OP = {name: code for code, name in EX.items()}

#: Operands per opcode, in serialized order. ('until', 'EndX') reads expressions up to and
#: including the terminating EndX expression; 'switch' and 'text' are read by their own rules.
OPERANDS = {
    'LocalVariable': ['field'], 'InstanceVariable': ['field'], 'DefaultVariable': ['field'],
    'LocalOutVariable': ['field'], 'ClassSparseDataVariable': ['field'],
    'PropertyConst': ['field'],
    'Return': ['expr'], 'Jump': ['u32'], 'JumpIfNot': ['u32', 'expr'],
    'Assert': ['u16', 'u8', 'expr'], 'NothingInt32': ['i32'],
    'Let': ['field', 'expr', 'expr'], 'LetObj': ['expr', 'expr'],
    'LetWeakObjPtr': ['expr', 'expr'], 'LetBool': ['expr', 'expr'],
    'LetDelegate': ['expr', 'expr'], 'LetMulticastDelegate': ['expr', 'expr'],
    'LetValueOnPersistentFrame': ['field', 'expr'],
    'BitFieldConst': ['field', 'u8'],
    'ClassContext': ['expr', 'u32', 'field', 'expr'],
    'Context': ['expr', 'u32', 'field', 'expr'],
    'Context_FailSilent': ['expr', 'u32', 'field', 'expr'],
    'InterfaceContext': ['expr'], 'StructMemberContext': ['field', 'expr'],
    'MetaCast': ['obj', 'expr'], 'DynamicCast': ['obj', 'expr'],
    'ObjToInterfaceCast': ['obj', 'expr'], 'CrossInterfaceCast': ['obj', 'expr'],
    'InterfaceToObjCast': ['obj', 'expr'], 'Cast': ['u8', 'expr'],
    'Skip': ['u32', 'expr'],
    'VirtualFunction': ['name', ('until', 'EndFunctionParms')],
    'LocalVirtualFunction': ['name', ('until', 'EndFunctionParms')],
    'FinalFunction': ['obj', ('until', 'EndFunctionParms')],
    'LocalFinalFunction': ['obj', ('until', 'EndFunctionParms')],
    'CallMath': ['obj', ('until', 'EndFunctionParms')],
    'CallMulticastDelegate': ['obj', ('until', 'EndFunctionParms')],
    'IntConst': ['i32'], 'FloatConst': ['f32'], 'DoubleConst': ['f64'],
    'StringConst': ['str'], 'UnicodeStringConst': ['ustr'], 'ObjectConst': ['obj'],
    'NameConst': ['name'], 'RotationConst': ['f64'] * 3, 'VectorConst': ['f64'] * 3,
    'Vector3fConst': ['f32'] * 3, 'TransformConst': ['f64'] * 10,
    'ByteConst': ['u8'], 'IntConstByte': ['u8'], 'Int64Const': ['i64'], 'UInt64Const': ['u64'],
    'SkipOffsetConst': ['u32'], 'TextConst': ['text'],
    'SoftObjectConst': ['expr'], 'FieldPathConst': ['expr'],
    'StructConst': ['obj', 'i32', ('until', 'EndStructConst')],
    'SetArray': ['expr', ('until', 'EndArray')],
    'SetSet': ['expr', 'i32', ('until', 'EndSet')],
    'SetMap': ['expr', 'i32', ('until', 'EndMap')],
    'ArrayConst': ['field', 'i32', ('until', 'EndArrayConst')],
    'SetConst': ['field', 'i32', ('until', 'EndSetConst')],
    'MapConst': ['field', 'field', 'i32', ('until', 'EndMapConst')],
    'PushExecutionFlow': ['u32'], 'ComputedJump': ['expr'], 'PopExecutionFlowIfNot': ['expr'],
    'InstanceDelegate': ['name'], 'BindDelegate': ['name', 'expr', 'expr'],
    'AddMulticastDelegate': ['expr', 'expr'], 'RemoveMulticastDelegate': ['expr', 'expr'],
    'ClearMulticastDelegate': ['expr'], 'SwitchValue': ['switch'],
    'ArrayGetByRef': ['expr', 'expr'],
}
for _name in ('Nothing', 'EndParmValue', 'EndFunctionParms', 'Self', 'IntZero', 'IntOne',
              'True', 'False', 'NoObject', 'NoInterface', 'EndStructConst', 'EndArray',
              'EndArrayConst', 'EndSet', 'EndMap', 'EndSetConst', 'EndMapConst',
              'PopExecutionFlow', 'EndOfScript', 'Breakpoint', 'WireTracepoint', 'Tracepoint',
              'DeprecatedOp4A'):
    OPERANDS[_name] = []

#: Fixed operand kinds -> struct format.
PACK = {'u8': '<B', 'u16': '<H', 'u32': '<I', 'i32': '<i', 'i64': '<q', 'u64': '<Q',
        'f32': '<f', 'f64': '<d'}
#: In-memory sizes of the operands that differ from their serialized size.
MEMORY = {'obj': 8, 'name': 12, 'field': 8}
#: EBlueprintTextLiteralType.
TEXT_EMPTY, TEXT_LOCALIZED, TEXT_INVARIANT, TEXT_LITERAL, TEXT_TABLE = range(5)


class Node:
    """One expression: its opcode and its operands as (kind, value), in serialized order.
    A 'field' value is ([(name index, number)], owner FPackageIndex); a 'name' value is
    (name index, number); a 'str' or 'ustr' value is its text."""
    __slots__ = ('op', 'ops', 'offset')

    def __init__(self, op, ops=None, offset=None):
        self.op = OP[op] if isinstance(op, str) else op
        self.ops = list(ops or [])
        self.offset = offset

    @property
    def name(self):
        return EX.get(self.op, 'Unknown_%02X' % self.op)

    def __repr__(self):
        return '<%s %d ops>' % (self.name, len(self.ops))

    def walk(self):
        """This node and every node inside it, depth first."""
        yield self
        for kind, v in self.ops:
            if kind == 'expr':
                yield from v.walk()

    def children(self):
        return [v for kind, v in self.ops if kind == 'expr']


class BytecodeError(ValueError):
    pass


class Reader:
    """Walks a serialized script into top-level nodes."""

    def __init__(self, data):
        self.d, self.o = bytes(data), 0

    def take(self, fmt):
        try:
            v, = struct.unpack_from(fmt, self.d, self.o)
        except struct.error:
            raise BytecodeError('ran past the script at %d' % self.o) from None
        self.o += struct.calcsize(fmt)
        return v

    def name(self):
        return struct.unpack_from('<II', self.d, self._advance(8))

    def _advance(self, n):
        at = self.o
        self.o += n
        if self.o > len(self.d):
            raise BytecodeError('ran past the script at %d' % at)
        return at

    def field(self):
        n = self.take('<i')
        if not 0 <= n <= 64:
            raise BytecodeError('bad field path length %d at %d' % (n, self.o - 4))
        path = [self.name() for _ in range(n)]
        return path, self.take('<i')

    def cstr(self, width):
        start, zero = self.o, b'\0' * width
        while self.d[self.o:self.o + width] != zero:
            self._advance(width)
        self._advance(width)
        raw = self.d[start:self.o - width]
        return raw.decode('latin-1') if width == 1 else raw.decode('utf-16-le', 'surrogatepass')

    def expr(self):
        start = self.o
        op = self.d[self._advance(1)]
        name = EX.get(op)
        if name is None:
            raise BytecodeError('unknown opcode 0x%02X at %d' % (op, start))
        node = Node(op, offset=start)
        for kind in OPERANDS[name]:
            self.operand(node, kind)
        return node

    def operand(self, node, kind):
        ops = node.ops
        if isinstance(kind, tuple):
            end = OP[kind[1]]
            while True:
                child = self.expr()
                ops.append(('expr', child))
                if child.op == end:
                    return
        if kind == 'expr':
            ops.append(('expr', self.expr()))
        elif kind in PACK:
            ops.append((kind, self.take(PACK[kind])))
        elif kind == 'obj':
            ops.append(('obj', self.take('<i')))
        elif kind == 'name':
            ops.append(('name', self.name()))
        elif kind == 'field':
            ops.append(('field', self.field()))
        elif kind == 'str':
            ops.append(('str', self.cstr(1)))
        elif kind == 'ustr':
            ops.append(('ustr', self.cstr(2)))
        elif kind == 'text':
            literal = self.take('<B')
            ops.append(('u8', literal))
            if literal == TEXT_LOCALIZED:
                ops += [('expr', self.expr()) for _ in range(3)]
            elif literal in (TEXT_INVARIANT, TEXT_LITERAL):
                ops.append(('expr', self.expr()))
            elif literal == TEXT_TABLE:
                ops.append(('obj', self.take('<i')))
                ops += [('expr', self.expr()) for _ in range(2)]
            elif literal != TEXT_EMPTY:
                raise BytecodeError('text literal type %d' % literal)
        elif kind == 'switch':
            cases = self.take('<H')
            ops += [('u16', cases), ('u32', self.take('<I')), ('expr', self.expr())]
            for _ in range(cases):
                ops += [('expr', self.expr()), ('u32', self.take('<I')), ('expr', self.expr())]
            ops.append(('expr', self.expr()))
        else:
            raise BytecodeError('operand kind %r' % (kind,))

    def run(self):
        """Top-level nodes up to and including EX_EndOfScript."""
        nodes = []
        while self.o < len(self.d):
            node = self.expr()
            nodes.append(node)
            if node.op == OP['EndOfScript']:
                break
        return nodes


def read(data):
    """(top-level nodes, bytes consumed) of a serialized script."""
    r = Reader(data)
    nodes = r.run()
    return nodes, r.o


# -- writing and sizes -----------------------------------------------------------------------

def _operand_bytes(kind, v):
    if kind in PACK:
        return struct.pack(PACK[kind], v)
    if kind == 'obj':
        return struct.pack('<i', v)
    if kind == 'name':
        return struct.pack('<II', *v)
    if kind == 'field':
        path, owner = v
        return (struct.pack('<i', len(path)) + b''.join(struct.pack('<II', *n) for n in path)
                + struct.pack('<i', owner))
    if kind == 'str':
        return v.encode('latin-1') + b'\0'
    if kind == 'ustr':
        return v.encode('utf-16-le', 'surrogatepass') + b'\0\0'
    raise BytecodeError('operand kind %r' % (kind,))


def node_bytes(node):
    out = [bytes((node.op,))]
    for kind, v in node.ops:
        out.append(node_bytes(v) if kind == 'expr' else _operand_bytes(kind, v))
    return b''.join(out)


def write(nodes):
    """The serialized script of top-level nodes."""
    return b''.join(node_bytes(n) for n in nodes)


def memory_size(node):
    """In-memory size of an expression and everything inside it."""
    total = 1
    for kind, v in node.ops:
        if kind == 'expr':
            total += memory_size(v)
        elif kind in MEMORY:
            total += MEMORY[kind]
        else:
            total += len(_operand_bytes(kind, v))
    return total


def sizes(nodes):
    """(serialized size, in-memory size) of top-level nodes."""
    return len(write(nodes)), sum(memory_size(n) for n in nodes)


# -- references and text ---------------------------------------------------------------------

def references(nodes):
    """Every object, name and property reference in the nodes: (kind, value) for kinds
    'obj', 'name' and 'field'."""
    for top in nodes:
        for node in top.walk():
            for kind, v in node.ops:
                if kind in ('obj', 'name', 'field'):
                    yield kind, v


def census(nodes, counter):
    """Count every opcode of the nodes into `counter` (a collections.Counter)."""
    for top in nodes:
        for node in top.walk():
            counter[node.name] += 1


def disassemble(nodes, name=None, obj=None):
    """Readable lines of the nodes: `name((index, number))` and `obj(index)` give the text
    shown for names and object references."""
    name = name or (lambda v: 'name%d_%d' % v)
    obj = obj or (lambda v: 'obj%d' % v)
    lines, at = [], [0]

    def show(kind, v):
        if kind == 'obj':
            return obj(v)
        if kind == 'name':
            return name(v)
        if kind == 'field':
            return '.'.join(name(n) for n in v[0]) + '@' + obj(v[1])
        if kind in ('str', 'ustr'):
            return repr(v)
        return str(v)

    def emit(node, depth):
        plain = ' '.join(show(k, v) for k, v in node.ops if k != 'expr')
        lines.append('%s%s%s' % ('  ' * depth, node.name, ' ' + plain if plain else ''))
        for child in node.children():
            emit(child, depth + 1)

    for top in nodes:
        lines.append('%5d:' % at[0])
        emit(top, 1)
        at[0] += memory_size(top)
    return lines
