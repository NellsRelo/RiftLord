"""Decode the SURFACE half of a NavPower navgraph -- the walkable one.

    python3 navsurf.py --level Scenario_01
    python3 navsurf.py --survey
    python3 navsurf.py --selftest

**This buffer is now readable end to end**, verified on all 33 shipped maps. The
volumetric (flight) half was already decoded and `navvolgen.py` writes one from scratch;
this is the other buffer, the one ground movers use.

## A surface buffer holds TWO graphs, one per agent size

Found by a header identity rather than guessed: `word[G+80] == 340 + word[G+56] +
word[G+60]`, together with the constant 56 at `G+72`. It matches **66 times across the
33 maps -- exactly twice per map** -- and the second graph's agent parameters are
radius 80 and height 360 against the first's 42 and 192. The first graph is the ordinary
agent (`AgentRadius=42`, `AgentHeight=192` in OMD3's own `DefaultEngine.ini`); the
second is the large one, and it holds about half as many polygons because a fatter agent
gets a coarser decomposition.

**The next graph's base is `G + total`.** Sum the two totals and you are 48 bytes short
of the buffer, which is the two 24-byte containers.

## One graph, with every offset measured

    G+0     container, 24 bytes
    G+24    65536 -- 1.0 in 16.16 (the volumetric buffer has 131072 here)
    G+28    inner size
    G+56    section A size: the record stream
    G+60    section B size: the kd-tree
    G+72    56, constant
    G+80    340 + A + B, and the ORIGIN every internal offset is measured from
    G+88    build scale 200.0, then the agent: 13.86, 42, 48, 192 (or 26.4, 80, 48, 360)
    G+108   world bbox, min then max
    G+340   48 bytes of high-entropy data, then 32 zero bytes  -- UNIDENTIFIED
    G+420   the record stream, `A - 44` bytes of it
    G+420+A-44  the kd-tree

**`G+80` is the origin**, which is what makes the layout fall into place: the records
begin at `G+80+340`, every pointer inside the graph is `G+80+value`, and the tree's
leaves are record offsets in the same coordinate.

## The record stream

A record is `72 + 32n` bytes and the count is inside it:

    +0   centre      3f
    +12  radius      1f   -- the BOUNDING radius: max distance to its own vertices
    +16  0xFFFFFFFF
    +20  0
    +24  type        byte 0 = 0x80 | vertexCount, then FF FF 01
    +28  flags       00 00 11 0X, X in 2..7
    +32  four zero words
    +48  vertices    n x 32: position 3f, (0, -4) as i16, then two u64 fields
    +48+32n  24 zero bytes

`ESPSection_Trap` and `ESPSection_Wedge` are two subclasses of one base, which is why
no fixed stride was ever found and why the count has to be read per record. Across all
33 maps the counts are 3 (8,712 records) and 4 (11,814) dominant, out to 15.

**The last record is short by 44 bytes** -- it has neither its 24-byte tail nor the
20-byte tail of its final vertex, because the tree header follows immediately. That is
why the stream is `A - 44` and not `A`, and the tree header sits at `G+420+A-44` with
`(0xFFFC0000, 0, 0)` then the graph's own bbox: **66/66 graphs**.

## The kd-tree, and what its leaves mean

The header is 44 bytes and matches the volumetric tree's field placement -- bbox at +12,
size at +36, root link at +40 -- with `size == B - 28` on **66/66 graphs**. Node
encoding is the volumetric one: `clipA, clipB, child1, [child1's subtree inline],
child2, ...`, where a child is `0x8000000N` for a leaf or `(axis << 24) | offset` for a
link, and the offset is the distance from the pointed-at node's start to just past its
own second child word.

**A leaf is a record offset, not an index.** `G + 80 + leaf` lands on a record start for
**every leaf of all 66 graphs**, and the leaf count equals the record count on all 66 --
so the tree indexes every polygon exactly once.

## What is still unknown

* the second u64 field of a vertex resolves to a section record (89 of 90 on
  Scenario_01) but is too sparse to be ordinary adjacency -- see `adjacency`;
* the first u64 field is unidentified: values 33..5,917 with half of them odd, so not an
  offset to anything aligned;
* the 48 bytes at `G+340`.
"""
import argparse
import collections
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

#: Where the payload header ends and the first section begins, from
#: `word[0x50] - word[0x38] - word[0x3c] == 340` holding on all 33 shipped maps.
HEADER_END = 364

#: Offsets inside the buffer of the two section sizes and their total.
OFF_SECTION_A = 0x38
OFF_SECTION_B = 0x3c
OFF_SECTION_TOTAL = 0x50

#: Every internal offset in a graph is measured from here. Records begin at
#: ORIGIN + HEADER_END, pointers resolve as ORIGIN + value, and the tree's leaves are
#: record offsets in the same coordinate.
ORIGIN = 80

#: Where a graph's record stream begins, relative to its base: ORIGIN + 340.
RECORDS_BEGIN = 420

#: The last record is short by this much -- no 24-byte tail and no 20-byte tail on its
#: final vertex -- because the kd-tree header follows it immediately.
LAST_RECORD_SHORT = 44

#: The kd-tree's header is 44 bytes and its declared size is the section size minus
#: this. Both hold on 66/66 graphs.
TREE_HEADER = 44
TREE_SIZE_SLACK = 28

#: A tree child word: a leaf names a record by offset, a link names a node.
LEAF_TAG = 0x80000000

#: A section record's fixed part: centre 3f, radius 1f, 0xFFFFFFFF, 0, type, flags, and
#: four zero words. Vertices follow, 32 bytes each, then a 24-byte tail.
SECTION_FIXED = 48
SECTION_TAIL = 24
VERTEX_STRIDE = 32

#: `0x80 | vertexCount` in byte 0 of the type word, with `FF FF 01` behind it on every
#: record of every shipped map. Reading that word as a decimal integer is what hid it:
#: 33554307 looks like 0x02000103 if you assume the shape, and is really 0x01FFFF83.
COUNT_FLAG = 0x80

#: A neighbour pointer in a vertex record is an offset from HERE, not from the buffer
#: start or the section start. Measured: `80 + value` lands on a section record start
#: for 89 of 90 non-zero values on Scenario_01, and 420 - 80 = 340 is exactly the
#: payload header size, so the first record's own pointer value is 340.
LINK_BASE = 80

#: A portal's vertex count must be at least a triangle and no more than this. The
#: volumetric graph only ever uses quads; a surface portal between two trapezoids
#: should be an edge, so 2 is allowed here and is the interesting case.
MIN_PORTAL_VERTS = 2
MAX_PORTAL_VERTS = 8

#: How far a portal's stored centre may sit from the mean of its own vertices, in uu.
#: The volumetric decode found this exact on every shipped map, so a tight bound is the
#: whole point: it is what makes a hit self-validating rather than a coincidence.
CENTRE_TOLERANCE = 1.0


def buffer(map_name, tmp=None):
    """The surface buffer of one shipped level, as bytes."""
    from riftlord.core import gamepath
    from riftlord.build.navigation import navpatch
    tmp = tmp or gamepath.beside_code(gamepath.CODE_ROOT, '.navtmp')
    os.makedirs(tmp, exist_ok=True)
    files = navpatch.fetch_map(navpatch.map_path(map_name), tmp)
    found = navpatch.nav_buffers(files['.umap'], files['.uexp'])
    if not found or not found[2]:
        return None
    with open(files['.uexp'], 'rb') as fh:
        data = fh.read()
    return data[found[1]:found[1] + found[2]]


def graphs(buf):
    """Every graph embedded in a surface buffer, found by the header identity.

    `word[G+80] == 340 + word[G+56] + word[G+60]` with 56 at `G+72` matches exactly
    twice per map on all 33 shipped maps -- the ordinary agent then the large one -- and
    the second base is always the first's `G + total`, so the graphs are contiguous.
    """
    out = []
    for g in range(0, max(len(buf) - 140, 0), 4):
        a, b = struct.unpack_from('<I', buf, g + OFF_SECTION_A), None
        a = struct.unpack_from('<I', buf, g + OFF_SECTION_A)[0]
        b = struct.unpack_from('<I', buf, g + OFF_SECTION_B)[0]
        total = struct.unpack_from('<I', buf, g + OFF_SECTION_TOTAL)[0]
        if struct.unpack_from('<I', buf, g + 72)[0] != 56:
            continue
        if total and a and total == 340 + a + b:
            out.append(header(buf, g))
    return out


def header(buf, base=0):
    """The known fields of one graph's header, with the identity checked."""
    if not buf or len(buf) < base + HEADER_END:
        return None
    w = lambda o: struct.unpack_from('<I', buf, base + o)[0]
    f = lambda o: struct.unpack_from('<f', buf, base + o)[0]
    a, b, total = w(OFF_SECTION_A), w(OFF_SECTION_B), w(OFF_SECTION_TOTAL)
    return {
        'base': base,
        'origin': base + ORIGIN,
        'records_at': base + RECORDS_BEGIN,
        'records_bytes': max(a - LAST_RECORD_SHORT, 0),
        'tree_at': base + RECORDS_BEGIN + a - LAST_RECORD_SHORT,
        #: the tree's data ends here, which is also where the NEXT graph's header
        #: fields begin: `T + 40 + size` works out to `base + 48 + total`, measured
        #: exactly on every graph of every map
        'ends': base + 48 + total,
        'next': base + total,
        'size': len(buf),
        'version': w(4),
        'payload': w(8),
        'hash': w(12),
        'flags': w(20),
        'denominator': w(24),
        'inner': w(28),
        'section_a': a,
        'section_b': b,
        'section_total': total,
        'identity': total - a - b,          #: must be 340
        'build_scale': f(88),
        'agent': [f(92), f(96), f(100), f(104)],
        'bbox': [f(108 + 4 * i) for i in range(6)],
    }


def _in_bbox(p, bbox, pad=200.0):
    return all(bbox[i] - pad <= p[i] <= bbox[3 + i] + pad for i in (0, 1, 2))


def find_portals(buf, head, lo=HEADER_END, hi=None, max_index=None):
    """Records matching the volumetric graph's `Portal` layout, found by its identity.

    The layout, from the other buffer:

        indexA i32, indexB i32, centre.x/y/z 3f, vertexCount i32, vertices 3f each

    and the identity that makes a hit trustworthy: **the stored centre equals the mean
    of the record's own vertices.** That is an arithmetic relation between fields of one
    record, so it cannot be satisfied by noise at any rate worth worrying about -- which
    is exactly the kind of check that cracked the header.
    """
    hi = hi if hi is not None else len(buf)
    out = []
    at = lo
    end = hi - 32
    while at < end:
        a, b = struct.unpack_from('<ii', buf, at)
        if a < 0 or b < 0 or a == b or (max_index is not None
                                        and (a > max_index or b > max_index)):
            at += 4
            continue
        cx, cy, cz, n = struct.unpack_from('<fffi', buf, at + 8)
        if not (MIN_PORTAL_VERTS <= n <= MAX_PORTAL_VERTS):
            at += 4
            continue
        need = at + 24 + 12 * n
        if need > hi:
            at += 4
            continue
        if not _in_bbox((cx, cy, cz), head['bbox']):
            at += 4
            continue
        verts = [struct.unpack_from('<fff', buf, at + 24 + 12 * i) for i in range(n)]
        if not all(_in_bbox(v, head['bbox']) for v in verts):
            at += 4
            continue
        mean = [sum(v[i] for v in verts) / float(n) for i in (0, 1, 2)]
        if max(abs(mean[i] - (cx, cy, cz)[i]) for i in (0, 1, 2)) > CENTRE_TOLERANCE:
            at += 4
            continue
        out.append({'at': at, 'a': a, 'b': b, 'centre': (cx, cy, cz),
                    'verts': verts, 'size': 24 + 12 * n})
        at = need
    return out


def sections(buf, head=None):
    """Walk section A's record stream: the walkable polygons. [] if it does not parse.

    **This is the decode, and a strict sequential walk is its proof.** Every earlier
    attempt looked for a fixed stride and failed, because `ESPSection_Trap` and
    `ESPSection_Wedge` are two subclasses of one base and a record's length depends on
    its vertex count. The count is in the record itself:

        +0   centre        3f
        +12  radius        1f   -- the BOUNDING radius: max distance to its own vertices
        +16  0xFFFFFFFF
        +20  0
        +24  type          byte 0 = 0x80 | vertexCount, then FF FF 01
        +28  flags         00 00 11 0X, where X is 2, 3 or 4
        +32  four zero words
        +48  vertices      n records of 32 bytes: position 3f, 0xFFFC0000, then 4 words
        +48+32n  24-byte tail

    so a record is `72 + 32n` bytes. Walking from `RECORDS_BEGIN` with the count read
    per record consumes **exactly `section_a` bytes** -- measured on all 33 shipped
    maps, from 117 records to 755 -- and the radius identity holds on **every record**,
    which is what makes the layout trustworthy rather than merely plausible.

    The centre is NOT always the mean of the vertices (it is on about half of them), so
    it is a bounding-sphere centre or an area-weighted centroid rather than a plain
    average. The radius is the identity to check.
    """
    head = head or header(buf)
    if not head:
        return []
    end = head['tree_at']
    at = head['records_at']
    out = []
    while at + SECTION_FIXED <= end:
        if at + SECTION_FIXED > len(buf):
            break
        marker, zero = struct.unpack_from('<2I', buf, at + 16)
        tb = buf[at + 24:at + 28]
        if marker != 0xFFFFFFFF or not tb or not (tb[0] & COUNT_FLAG):
            break
        n = tb[0] & 0x7F
        if not 1 <= n <= 32 or at + SECTION_FIXED + VERTEX_STRIDE * n > len(buf):
            break
        centre = struct.unpack_from('<fff', buf, at)
        radius = struct.unpack_from('<f', buf, at + 12)[0]
        #: The final vertex of the LAST record is a bare position -- 12 bytes, no tag
        #: and no 64-bit fields -- because the tree header follows it. Reading fields
        #: for it reports whatever the tree header happens to hold: that is where the
        #: "1 of 90 pointers does not resolve" came from, and it would make a generated
        #: graph that writes nothing there look as though it wrote one pointer.
        last_record = at + SECTION_FIXED + VERTEX_STRIDE * n + SECTION_TAIL > end
        verts, links = [], []
        for i in range(n):
            vat = at + SECTION_FIXED + VERTEX_STRIDE * i
            verts.append(struct.unpack_from('<fff', buf, vat))
            if last_record and i == n - 1:
                links.append((0, 0, 0, 0))
                continue
            links.append(struct.unpack_from('<4I', buf, vat + 16))
        out.append({'at': at, 'centre': centre, 'radius': radius, 'verts': verts,
                    'links': links, 'kind': tb[1:].hex(),
                    'neighbours': [0 if (last_record and i == n - 1) else
                                   struct.unpack_from(
                                       '<Q', buf,
                                       at + SECTION_FIXED + VERTEX_STRIDE * i + 24)[0]
                                   for i in range(n)],
                    'edge_ids': [0 if (last_record and i == n - 1) else
                                 struct.unpack_from(
                                     '<Q', buf,
                                     at + SECTION_FIXED + VERTEX_STRIDE * i + 16)[0]
                                 for i in range(n)],
                    'truncated': last_record,
                    'flags': buf[at + 28:at + 32].hex(),
                    'size': SECTION_FIXED + VERTEX_STRIDE * n + SECTION_TAIL})
        at += SECTION_FIXED + VERTEX_STRIDE * n + SECTION_TAIL
    return out


def tree(buf, head):
    """The kd-tree over one graph's records: {at, bbox, size, root, leaves}.

    A leaf is a **record offset** in the graph's own coordinate, so a record is found at
    `origin + leaf`. Verified on all 66 graphs: every leaf lands on a record start and
    the leaf count equals the record count, so the tree indexes each polygon once.
    """
    t = head['tree_at']
    if t + TREE_HEADER > len(buf):
        return None
    pre = struct.unpack_from('<3I', buf, t)
    bbox = [struct.unpack_from('<f', buf, t + 12 + 4 * i)[0] for i in range(6)]
    size = struct.unpack_from('<I', buf, t + 36)[0]
    root = struct.unpack_from('<I', buf, t + 40)[0]
    limit = min(head['ends'], len(buf))
    leaves = []
    for off in range(t + TREE_HEADER, limit - 3, 4):
        w = struct.unpack_from('<I', buf, off)[0]
        if w & 0xF0000000 == LEAF_TAG:
            leaves.append(w & 0x0FFFFFFF)
    return {'at': t, 'pre': pre, 'bbox': bbox, 'size': size, 'root': root,
            'leaves': leaves,
            'size_ok': size == head['section_b'] - TREE_SIZE_SLACK,
            'pre_ok': pre == (0xFFFC0000, 0, 0)}


def walk_tree(buf, head, t=None):
    """Descend the kd-tree the way the loader must: {leaves, bytes, problems}.

    **Scanning for leaf words is not a check.** `tree` finds leaves by looking for the
    0x8 tag at every 4-byte offset, which a structurally broken tree passes trivially --
    and a generated graph whose tree does not descend correctly loads and strands every
    mover. This walks it: a node is `clipA, clipB, child1, [child1's subtree inline],
    child2, [child2's subtree inline]`, and a link child's offset must equal
    `16 + len(that child's own first subtree)`, which is a checkable invariant rather
    than a convention to be trusted.

    Verified against shipped data first: on Scenario_01, Other_03 and Scenario_16 the
    walk finds exactly the records the scan does, with zero link errors, and its byte
    count equals `section B - 32` -- which is also the relation an emitter must satisfy.
    """
    t = t if t is not None else tree(buf, head)
    leaves, problems = [], []
    if not t:
        return {'leaves': [], 'bytes': 0, 'problems': ['no tree header']}
    if t['root'] & LEAF_TAG:
        return {'leaves': [t['root'] & 0x0FFFFFFF], 'bytes': 0, 'problems': []}

    def length(at, depth=0):
        if depth > 400 or at + 16 > len(buf):
            return 0
        w1 = struct.unpack_from('<I', buf, at + 8)[0]
        n1 = 0 if (w1 & LEAF_TAG) else length(at + 12, depth + 1)
        q = at + 12 + n1
        if q + 4 > len(buf):
            return 0
        w2 = struct.unpack_from('<I', buf, q)[0]
        n2 = 0 if (w2 & LEAF_TAG) else length(q + 4, depth + 1)
        return 12 + n1 + 4 + n2

    def descend(at, depth=0):
        if depth > 400:
            problems.append('tree deeper than 400 at +%d' % at)
            return 0
        if at + 16 > len(buf):
            problems.append('node at +%d runs past the buffer' % at)
            return 0
        w1 = struct.unpack_from('<I', buf, at + 8)[0]
        if w1 & LEAF_TAG:
            leaves.append(w1 & 0x0FFFFFFF)
            n1 = 0
        else:
            inner = length(at + 12 + 12, depth + 1) if not (
                struct.unpack_from('<I', buf, at + 12 + 8)[0] & LEAF_TAG) else 0
            want = 16 + inner
            if (w1 & 0x00FFFFFF) != want:
                problems.append('link at +%d carries off=%d, its target needs %d'
                                % (at + 8, w1 & 0x00FFFFFF, want))
            n1 = descend(at + 12, depth + 1)
        q = at + 12 + n1
        w2 = struct.unpack_from('<I', buf, q)[0]
        if w2 & LEAF_TAG:
            leaves.append(w2 & 0x0FFFFFFF)
            n2 = 0
        else:
            n2 = descend(q + 4, depth + 1)
        return 12 + n1 + 4 + n2

    total = descend(t['at'] + TREE_HEADER)
    want = head['section_b'] - 32
    if total != want:
        problems.append('the walk covered %d tree bytes, section B implies %d'
                        % (total, want))
    return {'leaves': leaves, 'bytes': total, 'problems': problems}


def locate(buf, head, point, t=None):
    """Which records a point could lie in, by descending the tree as the loader must.

    The descent rule, validated on shipped data before being trusted: **`v <= clipA`
    goes to the first child and `v >= clipB` to the second**, where `v` is the point's
    coordinate on the node's split axis and that axis comes from the LINK that pointed
    at the node. On Scenario_01 and Scenario_16 this finds every polygon's own centre
    -- 117/117 and 755/755 -- visiting about 3.4 leaves per query. Two other readings
    manage 34% and 12%, and "descend both children always" finds everything by visiting
    every leaf, which is brute force rather than the tree working.

    Note the clips OVERLAP where children do, so both branches get searched there. That
    is not a quirk: it is what makes the tree correct for polygons, which have extent.
    """
    t = t if t is not None else tree(buf, head)
    if not t:
        return []
    out = []
    #: subtree sizes by node offset, kept on the tree so repeated queries reuse them
    spans = t.setdefault('_spans', {})

    def span(at, depth=0):
        if at in spans:
            return spans[at]
        if depth > 400 or at + 16 > len(buf):
            return 0
        w1 = struct.unpack_from('<I', buf, at + 8)[0]
        n1 = 0 if (w1 & LEAF_TAG) else span(at + 12, depth + 1)
        q = at + 12 + n1
        w2 = struct.unpack_from('<I', buf, q)[0]
        n2 = 0 if (w2 & LEAF_TAG) else span(q + 4, depth + 1)
        spans[at] = 12 + n1 + 4 + n2
        return spans[at]

    def go(at, axis, depth=0):
        if depth > 400 or at + 16 > len(buf):
            return
        clip_a, clip_b = struct.unpack_from('<ff', buf, at)
        w1 = struct.unpack_from('<I', buf, at + 8)[0]
        n1 = 0 if (w1 & LEAF_TAG) else span(at + 12)
        q = at + 12 + n1
        w2 = struct.unpack_from('<I', buf, q)[0]
        v = point[axis]
        if v <= clip_a:
            if w1 & LEAF_TAG:
                out.append(w1 & 0x0FFFFFFF)
            else:
                go(at + 12, (w1 >> 28) & 0x7, depth + 1)
        if v >= clip_b:
            if w2 & LEAF_TAG:
                out.append(w2 & 0x0FFFFFFF)
            else:
                go(q + 4, (w2 >> 28) & 0x7, depth + 1)

    if t['root'] & LEAF_TAG:
        return [t['root'] & 0x0FFFFFFF]
    go(t['at'] + TREE_HEADER, (t['root'] >> 28) & 0x7)
    return out


def check_locate(buf, head, got=None, t=None, pull=0.8):
    """Can a point INSIDE each polygon find that polygon? The check that matters.

    **Querying centres is not enough and was actively misleading.** A centre lies on the
    correct side of every split by construction, so a tree whose clips bound centres
    instead of extents scored 117/117 on centres and **58/503** on interior points -- and
    in game that tree let an orc walk a few steps, lose the surface under it, turn and
    attack the air. Shipped data scores 503/503 here.
    """
    head = head or header(buf)
    got = got if got is not None else sections(buf, head)
    t = t if t is not None else tree(buf, head)
    where = {s['at']: i for i, s in enumerate(got)}
    missed = 0
    total = 0
    for i, s in enumerate(got):
        c = s['centre']
        for v in s['verts']:
            p = tuple(c[k] + (v[k] - c[k]) * pull for k in (0, 1, 2))
            if not _inside_xy(s['verts'], p[0], p[1]):
                continue
            total += 1
            hits = {where.get(head['origin'] + g) for g in locate(buf, head, p, t)}
            if i not in hits:
                missed += 1
    if missed:
        return ['%d of %d interior points cannot find the polygon they belong to'
                % (missed, total)]
    return []


def _inside_xy(poly, x, y):
    """Point in polygon on the xy projection."""
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i][0], poly[i][1]
        x2, y2 = poly[(i + 1) % n][0], poly[(i + 1) % n][1]
        if (y1 > y) != (y2 > y):
            t = (y - y1) / (y2 - y1) if y2 != y1 else 0.0
            if x < x1 + t * (x2 - x1):
                inside = not inside
    return inside


def check_tree(buf, head, got=None, t=None):
    """Problems with a tree: its header fields, and whether its leaves name records."""
    got = got if got is not None else sections(buf, head)
    t = t if t is not None else tree(buf, head)
    bad = []
    if not t:
        return ['the tree header is past the end of the buffer']
    if not t['pre_ok']:
        bad.append('tree header does not open with 0xFFFC0000, 0, 0: %s' % (t['pre'],))
    if not t['size_ok']:
        bad.append('tree size %d, expected section B - %d = %d'
                   % (t['size'], TREE_SIZE_SLACK,
                      head['section_b'] - TREE_SIZE_SLACK))
    starts = {s['at'] for s in got}
    missed = [v for v in t['leaves'] if head['origin'] + v not in starts]
    if missed:
        bad.append('%d of %d leaves do not name a record'
                   % (len(missed), len(t['leaves'])))
    if len(t['leaves']) != len(got):
        bad.append('%d leaves for %d records' % (len(t['leaves']), len(got)))
    #: and the tree must DESCEND, not merely contain leaf-shaped words
    walked = walk_tree(buf, head, t)
    bad.extend(walked['problems'])
    if sorted(walked['leaves']) != sorted(t['leaves']):
        bad.append('the walk reaches %d leaves, the scan sees %d'
                   % (len(walked['leaves']), len(t['leaves'])))
    #: and the tree must ANSWER QUERIES, not merely descend
    bad.extend(check_locate(buf, head, got, t))
    return bad


def polygon_area(verts):
    """A planar polygon's area, by the shoelace formula on its own plane."""
    n = len(verts)
    if n < 3:
        return 0.0
    nx = ny = nz = 0.0
    for i in range(n):
        a, b = verts[i], verts[(i + 1) % n]
        nx += (a[1] - b[1]) * (a[2] + b[2])
        ny += (a[2] - b[2]) * (a[0] + b[0])
        nz += (a[0] - b[0]) * (a[1] + b[1])
    return 0.5 * math.sqrt(nx * nx + ny * ny + nz * nz)


def adjacency(buf, head=None, got=None):
    """{record index: {neighbour indices}} -- the walkable graph, from shared EDGE IDS.

    **The first u64 field of a vertex is an edge id, and that is where the connectivity
    lives.** Two polygons that share an edge quote the same id, so pairing records by id
    gives the graph. The evidence is the multiplicity: across every map the id counts are
    **even, overwhelmingly exactly two** -- 59 ids twice, 2 four times and 1 six times on
    Scenario_01; 292, 69, 4, 2, 3, 1, 1, 1, 1 on Scenario_16 -- which is what a shared
    edge does and what nothing else would. A count above two is a long edge several
    neighbours abut.

    Measured: this puts **80-94% of a level's walkable AREA in one component**, against
    20-52% for the pointer field alone.

    The second u64 field is a section pointer (`origin + value` lands on a record start),
    and it is **redundant**: on every map tested its links are a strict subset of these,
    so adding them changes neither the component count nor the area. It is best read as a
    cached primary neighbour rather than as the adjacency.
    """
    head = head or header(buf)
    got = got if got is not None else sections(buf, head)
    by_edge = collections.defaultdict(set)
    for i, s in enumerate(got):
        for e in s['edge_ids']:
            if e:
                by_edge[e].add(i)
    out = collections.defaultdict(set)
    for members in by_edge.values():
        m = sorted(members)
        for i in range(len(m)):
            for j in range(i + 1, len(m)):
                out[m[i]].add(m[j])
                out[m[j]].add(m[i])
    return {i: out.get(i, set()) for i in range(len(got))}


def pointer_links(buf, head=None, got=None):
    """{record index: {neighbour indices}} from the vertex's SECOND u64 field.

    Kept separate from `adjacency` because it is a different field with a different
    meaning: sparse, redundant with the edge ids, and resolving through `origin + value`
    to a record start (89 of 90 non-zero values on Scenario_01).
    """
    head = head or header(buf)
    got = got if got is not None else sections(buf, head)
    starts = {s['at']: i for i, s in enumerate(got)}
    out = collections.defaultdict(set)
    for i, s in enumerate(got):
        for v in s['neighbours']:
            if not v:
                continue
            j = starts.get(head['origin'] + v)
            if j is not None and j != i:
                out[i].add(j)
                out[j].add(i)
    return {i: out.get(i, set()) for i in range(len(got))}


def components(got, adj):
    """[(polygon count, area)] per connected component, largest area first."""
    parent = list(range(len(got)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, nbrs in adj.items():
        for b in nbrs:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
    count = collections.Counter()
    area = collections.defaultdict(float)
    for i, s in enumerate(got):
        r = find(i)
        count[r] += 1
        area[r] += polygon_area(s['verts'])
    out = [(count[r], area[r]) for r in count]
    out.sort(key=lambda t: -t[1])
    return out


def check_sections(buf, head=None, got=None):
    """Problems with a section walk: the two identities, and exact coverage."""
    head = head or header(buf)
    got = got if got is not None else sections(buf, head)
    bad = []
    if not got:
        return ['section A did not parse at all']
    consumed = sum(s['size'] for s in got)
    #: the last record is short by LAST_RECORD_SHORT, so the stream is A - 44
    want = head['records_bytes']
    if consumed - LAST_RECORD_SHORT != want:
        bad.append('the walk consumed %d bytes of records, expected %d (off by %d)'
                   % (consumed - LAST_RECORD_SHORT, want,
                      consumed - LAST_RECORD_SHORT - want))
    for s in got:
        reach = max(math.dist(v, s['centre']) for v in s['verts'])
        if abs(reach - s['radius']) > 1.0:
            bad.append('+%d: radius %.2f but its farthest vertex is %.2f'
                       % (s['at'], s['radius'], reach))
            break
    return bad


def emit_record(centre, radius, verts, flags=0x02110000, last=False):
    """One section record. `last` drops the 44 bytes the final record does not carry."""
    n = len(verts)
    out = struct.pack('<fff', *centre) + struct.pack('<f', radius)
    out += struct.pack('<2I', 0xFFFFFFFF, 0)
    out += bytes([0x80 | n]) + bytes.fromhex('ffff01')
    out += struct.pack('<I', flags) + b'\x00' * 16
    for i, v in enumerate(verts):
        body = struct.pack('<fff', *v) + struct.pack('<2h', 0, -4)
        if last and i == n - 1:
            out += struct.pack('<fff', *v)     #: the final vertex keeps only its position
            break
        out += body + struct.pack('<4I', 0, 0, 0, 0)
    else:
        if not last:
            out += b'\x00' * SECTION_TAIL
    return out


def emit_graph(polys, bbox, agent=(13.86, 42.0, 48.0, 192.0), build_scale=200.0):
    """A whole surface graph: header, record stream and a one-level tree.

    Written to prove the layout is understood well enough to PRODUCE it, not to ship a
    navgraph -- the tree it emits is a flat chain rather than a balanced kd-tree, and
    nothing here computes the vertex link fields, which are still unidentified. What it
    does is close the loop: `sections` and `tree` read back what this writes, which is
    the discipline that caught every bug in the volumetric emitter.
    """
    recs, offsets = b'', []
    for i, (centre, radius, verts) in enumerate(polys):
        offsets.append(RECORDS_BEGIN + len(recs))
        recs += emit_record(centre, radius, verts, last=(i == len(polys) - 1))
    #: a chain of nodes, each splitting off one leaf, so every record is named once
    nodes = b''
    for i in range(len(offsets) - 1, -1, -1):
        leaf = LEAF_TAG | (offsets[i] - ORIGIN)
        if not nodes:
            nodes = struct.pack('<I', leaf)
        else:
            body = struct.pack('<ff', bbox[0], bbox[0]) + struct.pack('<I', leaf)
            nodes = body + struct.pack('<I', 0x10000000 | (len(body) + 4)) + nodes
    a = len(recs) + LAST_RECORD_SHORT
    b = len(nodes) + 40 - 40 + TREE_SIZE_SLACK + 4
    tree_hdr = struct.pack('<3I', 0xFFFC0000, 0, 0) + struct.pack('<6f', *bbox)
    tree_hdr += struct.pack('<2I', b - TREE_SIZE_SLACK, 0)
    body = bytearray(b'\x00' * RECORDS_BEGIN)
    struct.pack_into('<I', body, 24, 65536)
    struct.pack_into('<I', body, OFF_SECTION_A, a)
    struct.pack_into('<I', body, OFF_SECTION_B, b)
    struct.pack_into('<I', body, 72, 56)
    struct.pack_into('<I', body, OFF_SECTION_TOTAL, 340 + a + b)
    struct.pack_into('<f', body, 88, build_scale)
    for i, v in enumerate(agent):
        struct.pack_into('<f', body, 92 + 4 * i, v)
    for i, v in enumerate(bbox):
        struct.pack_into('<f', body, 108 + 4 * i, v)
    return bytes(body) + recs + tree_hdr[:TREE_HEADER] + nodes


def selftest():
    """Check the header identity and that a synthesised portal is found."""
    bad = []
    #: a portal built to the known layout must be recognised by its own identity
    verts = [(100.0, 200.0, 0.0), (400.0, 200.0, 0.0), (400.0, 500.0, 0.0),
             (100.0, 500.0, 0.0)]
    centre = [sum(v[i] for v in verts) / 4.0 for i in (0, 1, 2)]
    blob = struct.pack('<ii', 3, 7) + struct.pack('<fff', *centre) \
        + struct.pack('<i', 4) + b''.join(struct.pack('<fff', *v) for v in verts)
    buf = b'\x00' * HEADER_END + blob + b'\x00' * 64
    head = {'bbox': [0.0, 0.0, -100.0, 1000.0, 1000.0, 100.0]}
    got = find_portals(buf, head)
    if len(got) != 1:
        bad.append('a synthetic portal was not found exactly once: %d hits' % len(got))
    elif got[0]['a'] != 3 or got[0]['b'] != 7 or len(got[0]['verts']) != 4:
        bad.append('the synthetic portal decoded wrong: %s' % got[0])
    #: the constants must stay consistent with each other
    if RECORDS_BEGIN != ORIGIN + 340:
        bad.append('RECORDS_BEGIN should be ORIGIN + 340')
    #: a graph this module writes must read back: records, counts and the tree's leaves
    polys = []
    for k in range(4):
        vs = [(300.0 * k, 0.0, 0.0), (300.0 * k + 300.0, 0.0, 0.0),
              (300.0 * k + 300.0, 300.0, 0.0), (300.0 * k, 300.0, 0.0)]
        c = tuple(sum(v[i] for v in vs) / 4.0 for i in (0, 1, 2))
        polys.append((c, max(math.dist(v, c) for v in vs), vs))
    box = [-100.0, -100.0, -50.0, 1600.0, 500.0, 50.0]
    blob = emit_graph(polys, box)
    h2 = header(blob)
    back = sections(blob, h2)
    if len(back) != len(polys):
        bad.append('round trip: wrote %d records, read %d' % (len(polys), len(back)))
    else:
        for i, s2 in enumerate(back):
            if len(s2['verts']) != 4:
                bad.append('round trip: record %d came back with %d vertices'
                           % (i, len(s2['verts'])))
                break
        probs = check_sections(blob, h2, back)
        if probs:
            bad.extend('round trip: ' + p for p in probs)
        t2 = tree(blob, h2)
        if t2 and len(t2['leaves']) != len(polys):
            bad.append('round trip: %d leaves for %d records'
                       % (len(t2['leaves']), len(polys)))
    #: a synthesised section record must walk and pass both identities
    verts = [(0.0, 0.0, 0.0), (300.0, 0.0, 0.0), (300.0, 300.0, 0.0),
             (0.0, 300.0, 0.0)]
    centre = tuple(sum(v[i] for v in verts) / 4.0 for i in (0, 1, 2))
    rec = struct.pack('<fff', *centre)
    rec += struct.pack('<f', max(math.dist(v, centre) for v in verts))
    rec += struct.pack('<2I', 0xFFFFFFFF, 0)
    rec += bytes([0x80 | 4]) + bytes.fromhex('ffff01')
    rec += bytes.fromhex('00001102') + b'\x00' * 16
    for v in verts:
        #: 32 bytes: position, the 0xFFFC0000 tag, then FOUR link words. Folding the tag
        #: in as the first of the four makes the vertex 28 bytes and the whole walk
        #: drift -- which is how this test caught itself.
        rec += (struct.pack('<fff', *v) + struct.pack('<I', 0xFFFC0000)
                + struct.pack('<4I', 0, 0, 0, 0))
    rec += b'\x00' * SECTION_TAIL
    fake = bytearray(b'\x00' * (RECORDS_BEGIN + len(rec)))
    struct.pack_into('<I', fake, OFF_SECTION_A, len(rec))
    struct.pack_into('<I', fake, OFF_SECTION_B, 0)
    struct.pack_into('<I', fake, OFF_SECTION_TOTAL, 340 + len(rec))
    fake[RECORDS_BEGIN:] = rec
    head2 = header(bytes(fake))
    got = sections(bytes(fake), head2)
    if len(got) != 1 or len(got[0]['verts']) != 4:
        bad.append('a synthetic section record did not walk: %d record(s)' % len(got))
    else:
        problems = check_sections(bytes(fake), head2, got)
        if problems:
            bad.extend('synthetic record: ' + p for p in problems)
    #: and a record whose centre does NOT match its vertices must be refused, or the
    #: search is just matching plausible floats
    bad_centre = struct.pack('<ii', 3, 7) + struct.pack('<fff', 1.0, 2.0, 3.0) \
        + struct.pack('<i', 4) + b''.join(struct.pack('<fff', *v) for v in verts)
    if find_portals(b'\x00' * HEADER_END + bad_centre + b'\x00' * 64, head):
        bad.append('a record with a wrong centre was accepted')
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--level', default='Scenario_01')
    ap.add_argument('--survey', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args(argv)
    if args.selftest:
        bad = selftest()
        for b in bad:
            print('FAILED  %s' % b)
        print('navsurf selftest: %s'
              % ('passed' if not bad else '%d problem(s)' % len(bad)))
        return 1 if bad else 0

    levels = ([args.level] if not args.survey else
              ['Scenario_01', 'Scenario_Other_03', 'Scenario_07', 'Scenario_12',
               'Scenario_16', 'Scenario_14', 'Scenario_18', 'Scenario_Ice_01'])
    print('%-20s %-7s %8s %7s %7s %6s %6s %-5s %s'
          % ('level', 'graph', 'sectionA', 'records', 'leaves', 'links',
             'area', 'agent', 'ok'))
    for name in levels:
        buf = buffer(name)
        if not buf:
            print('%-20s no surface buffer' % name)
            continue
        for head in graphs(buf):
            got = sections(buf, head)
            t = tree(buf, head)
            adj = adjacency(buf, head, got)
            comps = components(got, adj)
            total = sum(a for _n, a in comps) or 1.0
            probs = check_sections(buf, head, got) + check_tree(buf, head, got, t)
            print('%-20s +%-6d %8d %7d %7d %6d %5.0f%% %-5.0f %s'
                  % (name, head['base'], head['section_a'], len(got),
                     len(t['leaves']) if t else 0,
                     sum(len(v) for v in adj.values()) // 2,
                     100.0 * comps[0][1] / total if comps else 0.0,
                     head['agent'][1],
                     'yes' if not probs else probs[0][:34]))
            if not args.survey:
                counts = collections.Counter(len(s2['verts']) for s2 in got)
                print('      vertex counts %s' % sorted(counts.items()))
                print('      bbox %s' % [round(v) for v in head['bbox']])
                ptr = pointer_links(buf, head, got)
                pl = sum(len(v) for v in ptr.values()) // 2
                extra = sum(1 for i, nb in ptr.items() for j in nb
                            if j not in adj.get(i, ()))
                print('      pointer links %d, of which %d are NOT in the edge-id graph'
                      % (pl, extra // 2))
                print('      components: %s'
                      % ['%d polys %.0f%%' % (n, 100.0*a/total) for n, a in comps[:4]])
                print('      records [+%d, +%d)  tree +%d  ends +%d  next graph +%d'
                      % (head['records_at'],
                         head['records_at'] + head['records_bytes'],
                         head['tree_at'], head['ends'], head['next']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
