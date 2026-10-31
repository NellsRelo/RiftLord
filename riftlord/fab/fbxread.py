# Generated from OMD2-Converter/thirdparty/fabconvert/fbxread.py by thirdparty/fabconvert/port_riftlord.py; edit the source.
"""Read binary FBX files (Kaydara FBX Binary, versions 7.0-7.7): the node tree, with property
values decoded (arrays inflated). The file is only read; it is untrusted (callers run under -I).

    node = read(path)            # the root: node.children is the top-level node list
    node.find('Objects'), node.findall('Geometry'), node.props, node.children
"""

import struct
import zlib

import numpy as np

MAGIC = b'Kaydara FBX Binary  \x00'
#: From this version, node record offsets and counts are 64-bit.
WIDE_VERSION = 7500
ARRAY_TYPES = {'f': '<f4', 'd': '<f8', 'l': '<i8', 'i': '<i4', 'b': '<u1'}


class Node:
    __slots__ = ('name', 'props', 'children')

    def __init__(self, name, props, children):
        self.name, self.props, self.children = name, props, children

    def find(self, name):
        return next((c for c in self.children if c.name == name), None)

    def findall(self, name):
        return [c for c in self.children if c.name == name]

    def __repr__(self):
        return 'Node(%s, %d props, %d children)' % (self.name, len(self.props), len(self.children))


def _prop(d, o):
    t = chr(d[o])
    o += 1
    if t == 'Y':
        return struct.unpack_from('<h', d, o)[0], o + 2
    if t == 'C':
        return bool(d[o]), o + 1
    if t == 'I':
        return struct.unpack_from('<i', d, o)[0], o + 4
    if t == 'F':
        return struct.unpack_from('<f', d, o)[0], o + 4
    if t == 'D':
        return struct.unpack_from('<d', d, o)[0], o + 8
    if t == 'L':
        return struct.unpack_from('<q', d, o)[0], o + 8
    if t in ('S', 'R'):
        n = struct.unpack_from('<I', d, o)[0]
        raw = d[o + 4:o + 4 + n]
        return (raw.decode('utf-8', 'replace') if t == 'S' else bytes(raw)), o + 4 + n
    if t in ARRAY_TYPES:
        count, enc, clen = struct.unpack_from('<III', d, o)
        o += 12
        raw = d[o:o + clen]
        if enc == 1:
            raw = zlib.decompress(raw)
        elif enc != 0:
            raise ValueError('FBX array encoding %d' % enc)
        arr = np.frombuffer(raw, ARRAY_TYPES[t], count)
        return arr, o + clen
    raise ValueError('FBX property type %r at %d' % (t, o - 1))


def _node(d, o, wide):
    if wide:
        end, nprops, plen = struct.unpack_from('<QQQ', d, o)
        o += 24
    else:
        end, nprops, plen = struct.unpack_from('<III', d, o)
        o += 12
    nlen = d[o]
    name = d[o + 1:o + 1 + nlen].decode('ascii', 'replace')
    o += 1 + nlen
    if end == 0:                                         # the null record that ends a list
        return None, o
    props = []
    p = o
    for _ in range(nprops):
        v, p = _prop(d, p)
        props.append(v)
    o += plen
    children = []
    null = 25 if wide else 13
    while o < end - null + 1 and o < end:
        child, o = _node(d, o, wide)
        if child is None:
            break
        children.append(child)
    return Node(name, props, children), end


def read(path):
    """The root Node of a binary FBX file; root.props = [version]."""
    with open(path, 'rb') as fh:
        d = fh.read()
    if not d.startswith(MAGIC):
        raise ValueError('%s is not a binary FBX file (ASCII FBX is not read)' % path)
    version = struct.unpack_from('<I', d, 23)[0]
    wide = version >= WIDE_VERSION
    o = 27
    top = []
    null = 25 if wide else 13
    while o < len(d) - null:
        n, o = _node(d, o, wide)
        if n is None:
            break
        top.append(n)
    return Node('', [version], top)


def properties70(node):
    """{name: values} of a node's Properties70 block (P records: name, type, label, flags, values...)."""
    out = {}
    block = node.find('Properties70') if node is not None else None
    for p in (block.children if block is not None else []):
        if p.name == 'P' and p.props:
            out[p.props[0]] = p.props[4:]
    return out
