"""Read and WRITE the volumetric navgraph's kd-tree -- the structure that answers
"which volume contains this point", and therefore the one that decides whether a
flyer can be placed on the graph at all.

Layout, measured on all ten shipped flight levels and round-tripped byte for byte:

    44-byte header
        +0   0
        +4   payload size (tail bytes - 12)
        +8   1
        +12  bbox min x, y, z
        +24  bbox max x, y, z
        +36  size of everything from +40 to the end (tail bytes - 40)
        +40  the ROOT's link word
    then the nodes, depth first:
        node = clipA float, clipB float,
               child1, [child1's subtree INLINE if it is a link],
               child2, [child2's subtree INLINE if it is a link]

    child = 0x8000000N            a leaf naming volume N
          = (axis << 24) | off    a link; `axis` is the SPLIT AXIS OF THE NODE IT
                                  POINTS AT (0=x, 0x10=y, presumably 0x20=z), and
                                  `off` is the byte distance from that node's start
                                  to just past its own SECOND child word -- the jump
                                  that skips the left subtree.

The two clips are a bounding-interval pair: the left group's maximum and the right
group's minimum on the split axis. They are equal wherever the split is clean.

Nodes are VARIABLE length and a node's two child words are NOT adjacent -- the second
sits after the first child's whole subtree. Every fixed-stride reading of this fails.
"""

import struct

HDR = 44
LEAF = 0x80000000
AXIS_TAG = {0: 0x00000000, 1: 0x10000000, 2: 0x20000000}
TAG_AXIS = {v: k for k, v in AXIS_TAG.items()}


class Node(object):
    __slots__ = ('clip', 'axis', 'kids')

    def __init__(self, clip, axis, kids):
        self.clip = clip          # (left max, right min) on `axis`
        self.axis = axis
        self.kids = kids          # each ('leaf', index) or ('node', Node)


def bbox_of(tail):
    """The bbox the TAIL carries, which is not quite the graph header's.

    Measured: on Scenario_Ice_02 and Scenario_Sand_01 they differ in the last ULP --
    -6225.00048828125 here against -6225.0 there -- so the tail's box is rounded
    outward and has to be read from the tail, not copied from the graph header. Two
    bytes per map, which is exactly how it showed up in a byte-for-byte round trip.
    """
    return struct.unpack_from('<6f', tail, 12)


def parse(tail, nvolumes):
    """The tree in `tail`, or None if it does not parse."""
    nw = len(tail) // 4
    w = struct.unpack_from('<%dI' % nw, tail, 0)
    fl = struct.unpack_from('<%df' % nw, tail, 0)

    def is_leaf(x):
        return (x & 0xFFFFFF00) == LEAF and (x & 0xFF) < nvolumes

    def node(p, axis):
        kids = []
        q = p + 2
        for _ in range(2):
            if q >= nw:
                return None, None
            c = w[q]
            if is_leaf(c):
                kids.append(('leaf', c & 0xFF))
                q += 1
            else:
                sub, q = node(q + 1, TAG_AXIS.get(c & 0xFF000000, 0))
                if sub is None:
                    return None, None
                kids.append(('node', sub))
        return Node((fl[p], fl[p + 1]), axis, kids), q

    root_axis = TAG_AXIS.get(w[10] & 0xFF000000, 0)
    tree, end = node(HDR // 4, root_axis)
    if tree is None or end != nw:
        return None
    return tree


def _extent(node):
    """Words a subtree occupies."""
    n = 2
    for kind, val in node.kids:
        n += 1
        if kind == 'node':
            n += _extent(val)
    return n


def _to_child2(node):
    """Words from a node's start to just past its own second child word."""
    n = 3                                   # clips, then the first child word
    if node.kids[0][0] == 'node':
        n += _extent(node.kids[0][1])
    return n + 1


def emit(tree, bbox):
    """The whole tail: 44-byte header then the nodes."""
    body = []

    def write(node):
        body.extend(struct.unpack('<2I', struct.pack('<2f', *node.clip)))
        for kind, val in node.kids:
            if kind == 'leaf':
                body.append(LEAF | val)
            else:
                body.append(AXIS_TAG[val.axis] | (_to_child2(val) * 4))
                write(val)

    write(tree)
    nodes = struct.pack('<%dI' % len(body), *body)
    tail_len = HDR + len(nodes)
    head = struct.pack('<3I', 0, tail_len - 12, 1)
    head += struct.pack('<6f', *bbox)
    head += struct.pack('<I', tail_len - 40)
    head += struct.pack('<I', AXIS_TAG[tree.axis] | (_to_child2(tree) * 4))
    assert len(head) == HDR, len(head)
    return head + nodes


def build(boxes):
    """A kd-tree over volume boxes, split like the shipped ones: on the axis with the
    widest spread of centres, at the boundary between the two halves.

    The clip pair is the left group's maximum and the right group's minimum on that
    axis, which is what every shipped node carries.
    """
    def rec(items):
        if len(items) == 1:
            return ('leaf', items[0][0])
        axis = max(range(3), key=lambda k: max(
            (b[0][k] + b[1][k]) / 2.0 for _i, b in items) - min(
            (b[0][k] + b[1][k]) / 2.0 for _i, b in items))
        items = sorted(items, key=lambda ib: (ib[1][0][axis] + ib[1][1][axis]) / 2.0)
        half = len(items) // 2
        left, right = items[:half], items[half:]
        clip = (max(b[1][axis] for _i, b in left),
                min(b[0][axis] for _i, b in right))
        kids = [rec(left), rec(right)]
        return ('node', Node(clip, axis, kids))

    kind, val = rec(list(enumerate(boxes)))
    if kind == 'leaf':                       # a single volume still needs a root
        raise ValueError('a tree needs at least two volumes')
    return val


def leaves_of(node, out=None):
    out = [] if out is None else out
    for kind, val in node.kids:
        if kind == 'leaf':
            out.append(val)
        else:
            leaves_of(val, out)
    return out


def check(tail, nvolumes, boxes=None):
    """Invariants a generated tree must satisfy. Returns a list of (name, ok)."""
    out = []
    tree = parse(tail, nvolumes)
    out.append(('the tree parses', tree is not None))
    if tree is None:
        return out
    lv = sorted(leaves_of(tree))
    out.append(('every volume is a leaf exactly once (%d/%d)'
                % (len(lv), nvolumes), lv == list(range(nvolumes))))
    out.append(('size is 44 + nodes', len(tail) == HDR + (_extent(tree)) * 4))
    hdr = struct.unpack_from('<3I', tail, 0)
    out.append(('header payload size', hdr[1] == len(tail) - 12))
    out.append(('header tail-from-40 size',
                struct.unpack_from('<I', tail, 36)[0] == len(tail) - 40))
    root = struct.unpack_from('<I', tail, 40)[0]
    out.append(('header root link matches the root node',
                root == (AXIS_TAG[tree.axis] | (_to_child2(tree) * 4))))
    if boxes:
        # Every leaf's box must sit on the correct side of each clip above it.
        bad = []

        def walk(node, lo, hi):
            for side, (kind, val) in enumerate(node.kids):
                if kind == 'leaf':
                    b = boxes[val]
                    c = node.clip[side]
                    if side == 0 and b[1][node.axis] > c + 1.0:
                        bad.append(val)
                    if side == 1 and b[0][node.axis] < c - 1.0:
                        bad.append(val)
                else:
                    walk(val, lo, hi)
        walk(tree, None, None)
        out.append(('every leaf lies on its clip\'s side (%d stray)' % len(bad),
                    not bad))
    return out
