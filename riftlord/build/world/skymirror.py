"""A sky's home lighting, copied onto the level: suns, sky light, atmosphere, height fog
and the unbound post-process settings, property by property.

Painting colours alone left Beset under a night sky looking like day (Bake B,
2026-10-16): Front Lawn's fill suns stayed at -10 deg pitch, its sky light lit from a
fixed daytime cubemap (SourceType SLS_SpecifiedCubemap) where Dragon Boneyard's
captures the night sky, and Dragon Boneyard's blue colour grade (ColorGamma) and
atmosphere sun never came across. Each home component's properties replace the
level's, so a property the home leaves at its default is removed here too.

Only plain values are copied: numbers, bools, names and structs built from them.
Object references (cubemaps, blendable materials, attachments) stay the level's own.

    mirror(builder, home, report)   # count of components rewritten
"""

import functools
import struct

#: Component classes copied from the home level.
CLASSES = ('DirectionalLightComponent', 'SkyLightComponent', 'AtmosphericFogComponent',
           'ExponentialHeightFogComponent')
#: Properties that stay the level's own: where a component sits (the fog's height is
#: the level's floor), how it is lit and built, and its identity.
KEEP = ('RelativeLocation', 'RelativeScale3D', 'Mobility', 'LightGuid', 'AttachParent',
        'WeightedBlendables')
#: Structs stored as raw values, with no names inside.
RAW_STRUCTS = ('LinearColor', 'Color', 'Vector', 'Vector4', 'Vector2D', 'Rotator', 'Quat',
               'IntPoint', 'Guid', 'Box')
#: Property kinds whose payload holds no names or references.
RAW_KINDS = ('FloatProperty', 'DoubleProperty', 'IntProperty', 'UInt32Property',
             'Int8Property', 'Int16Property', 'Int64Property', 'UInt16Property',
             'UInt64Property')


def _tags(data, names, start=0, end=None):
    """[(name, kind, start, end)] of a property stream, stopping at None."""
    end = len(data) if end is None else end
    out, o = [], start
    while o + 8 <= end:
        ni = struct.unpack_from('<i', data, o)[0]
        if not 0 <= ni < len(names) or names[ni] == 'None':
            break
        kind = names[struct.unpack_from('<i', data, o + 8)[0]]
        size = struct.unpack_from('<i', data, o + 16)[0]
        head = {'BoolProperty': 26, 'StructProperty': 49, 'ArrayProperty': 33,
                'ByteProperty': 33, 'EnumProperty': 33}.get(kind, 25)
        if kind == 'BoolProperty':
            size = 0
        out.append((names[ni], kind, o, o + head + size))
        o += head + size
    return out


def _terminator(data, names, start=0, end=None):
    tags = _tags(data, names, start, end)
    return tags[-1][3] if tags else start


def _retag(data, names, start, end, add_name):
    """One tag rewritten into the target's names, or None when it holds a reference."""
    kind = names[struct.unpack_from('<i', data, start + 8)[0]]
    name = names[struct.unpack_from('<i', data, start)[0]]
    index = struct.unpack_from('<i', data, start + 20)[0]

    def head(size, extra=b''):
        return (struct.pack('<ii', add_name(name), 0) + struct.pack('<ii', add_name(kind), 0)
                + struct.pack('<ii', size, index) + extra)
    if kind == 'BoolProperty':
        if data[start + 25]:
            return None
        return head(0) + bytes((data[start + 24], 0))
    if kind in RAW_KINDS:
        if data[start + 24]:
            return None
        return head(end - start - 25, b'\0') + data[start + 25:end]
    if kind == 'NameProperty':
        ref = struct.unpack_from('<ii', data, start + 25)
        return head(8, b'\0') + struct.pack('<ii', add_name(names[ref[0]]), ref[1])
    if kind in ('ByteProperty', 'EnumProperty'):
        enum = names[struct.unpack_from('<i', data, start + 24)[0]]
        if data[start + 32]:
            return None
        payload = data[start + 33:end]
        if len(payload) == 8:
            ref = struct.unpack_from('<ii', payload, 0)
            payload = struct.pack('<ii', add_name(names[ref[0]]), ref[1])
        return head(len(payload), struct.pack('<ii', add_name(enum), 0) + b'\0') + payload
    if kind == 'StructProperty':
        sname = names[struct.unpack_from('<i', data, start + 24)[0]]
        if data[start + 48]:
            return None
        if sname in RAW_STRUCTS:
            payload = data[start + 49:end]
        else:
            payload = _restream(data, names, start + 49, end, add_name)
            if payload is None:
                return None
        return head(len(payload), struct.pack('<ii', add_name(sname), 0) + b'\0' * 17) + payload
    return None


def _restream(data, names, start, end, add_name):
    """A nested property stream rewritten into the target's names, or None."""
    out = bytearray()
    for _n, _k, a, b in _tags(data, names, start, end):
        got = _retag(data, names, a, b, add_name)
        if got is None:
            return None
        out += got
    return bytes(out) + struct.pack('<ii', add_name('None'), 0)


def _merge(target, tnames, t_lo, t_hi, home, hnames, h_lo, h_hi, add_name):
    """Target stream [t_lo, t_hi) with home's plain properties in place of its own.
    Returns the new stream bytes (properties only, without the None)."""
    have = {n: (a, b) for n, _k, a, b in _tags(target, tnames, t_lo, t_hi)}
    out = bytearray()
    for n, (a, b) in have.items():
        #: a reference, or a kept property, stays; a plain value the home has is replaced,
        #: and one the home leaves at its default goes
        if n in KEEP or _retag(target, tnames, a, b, lambda s: 0) is None:
            out += target[a:b]
    for n, _k, a, b in _tags(home, hnames, h_lo, h_hi):
        if n in KEEP:
            continue
        got = _retag(home, hnames, a, b, add_name)
        if got is not None:
            out += got
    return bytes(out)


def _class_of(pkg, ex):
    from riftlord.build import levelbuild
    return levelbuild.resolve(pkg, struct.unpack_from('<i', ex['meta'], 0)[0])[1]


def _unbound_settings(pkg, names, ex):
    """(start, end) of an unbound PostProcessVolume's Settings tag, or None."""
    tags = {n: (a, b) for n, _k, a, b in _tags(ex['data'], names)}
    if 'bUnbound' not in tags or 'Settings' not in tags:
        return None
    return tags['Settings']


@functools.lru_cache(maxsize=4)
def _home(home):
    """{class: [(names, data)]} of the home level's components, plus 'Settings':
    (names, data, start, end) of its unbound post-process settings."""
    from riftlord.core import gamefiles
    from riftlord.formats.omd3 import omd3level
    from riftlord.formats.ue.uassetio import Package
    out = {}
    for path in omd3level.sublevels(home):
        try:
            pkg = Package(*gamefiles.package(path))
        except Exception:                            # noqa: BLE001
            continue
        pkg.load_payloads()
        names = [n for n, _h in pkg.names]
        for ex in pkg.exports:
            cls = _class_of(pkg, ex)
            if cls in CLASSES:
                out.setdefault(cls, []).append((names, ex['data']))
            elif cls == 'PostProcessVolume' and 'Settings' not in out:
                span = _unbound_settings(pkg, names, ex)
                if span:
                    out['Settings'] = (names, ex['data']) + span
    return out


def _intensity(data, names):
    for n, _k, a, _b in _tags(data, names):
        if n == 'Intensity':
            return struct.unpack_from('<f', data, a + 25)[0]
    return 1.0                                       # the component's default


def holds(pkg):
    """Whether a package has anything mirror() would rewrite."""
    return any(_class_of(pkg, ex) in CLASSES + ('PostProcessVolume',) for ex in pkg.exports)


def mirror(builder, home, report):
    """Copy `home`'s lighting components onto the matching ones in builder.pkg."""
    got = _home(home)
    if not got:
        report['skipped'].append('%s\'s lighting could not be read' % home)
        return 0
    pkg = builder.pkg
    add = pkg.add_name
    done = 0
    targets = {}
    for i, ex in enumerate(pkg.exports):
        cls = _class_of(pkg, ex)
        if cls in CLASSES or cls == 'PostProcessVolume':
            targets.setdefault(cls, []).append(i)
    for cls in CLASSES:
        homes, mine = got.get(cls) or [], targets.get(cls) or []
        if not homes or not mine:
            continue
        names = [n for n, _h in pkg.names]
        #: suns pair by brightness, the main sun with the main sun; a level with more
        #: lights than the home gives the extras the home's dimmest
        homes = sorted(homes, key=lambda h: -_intensity(h[1], h[0]))
        mine = sorted(mine, key=lambda i: -_intensity(pkg.exports[i]['data'], names))
        for rank, i in enumerate(mine):
            hn, hd = homes[min(rank, len(homes) - 1)]
            names = [n for n, _h in pkg.names]
            data = pkg.exports[i]['data']
            end = _terminator(data, names)
            body = _merge(data, names, 0, end, hd, hn, 0, _terminator(hd, hn), add)
            new = body + data[end:]
            pkg.exports[i]['data'] = new
            pkg.exports[i]['size'] = len(new)
            done += 1
    if 'Settings' in got:
        hn, hd, h_lo, h_hi = got['Settings']
        for i in targets.get('PostProcessVolume') or []:
            names = [n for n, _h in pkg.names]
            data = pkg.exports[i]['data']
            span = _unbound_settings(pkg, names, pkg.exports[i])
            if not span:
                continue
            a, b = span
            inner = _merge(data, names, a + 49, b, hd, hn, h_lo + 49, h_hi, add)
            inner += struct.pack('<ii', add('None'), 0)
            tag = bytearray(data[a:a + 49])
            struct.pack_into('<i', tag, 16, len(inner))
            new = data[:a] + bytes(tag) + inner + data[b:]
            pkg.exports[i]['data'] = new
            pkg.exports[i]['size'] = len(new)
            done += 1
    if done:
        report.setdefault('notes', []).append('%d lighting component(s) set as %s\'s'
                                              % (done, home))
    return done
