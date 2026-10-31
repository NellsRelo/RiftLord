"""The rift's "enemies near the rift" warning box, and resizing it at build.

`BP_OMDRift` carries a BoxComponent `AnnounceWarning`; an enemy entering it plays the
announcer's rift warning (`DX_OMD3_Announcer_Rift_Near_SC`). The class gives it a
half-size of 1700 x 1700 x 500 about the rift; some levels override `BoxExtent`,
`RelativeLocation` or `RelativeScale3D` on their rift's copy.

Plan key: `plan['world']['riftWarning']` = [x, y, z], the box's full size in world
units along the rift's own axes. A build writes `BoxExtent` on every rift's
AnnounceWarning in the persistent level, divided by that component's scale, and scales
its offset from the rift by the same factor, so the box shrinks toward the rift.
"""

import functools
import struct

#: The plan's world setting.
PLAN_KEY = 'riftWarning'
#: The warning box component on BP_OMDRift.
COMPONENT = 'AnnounceWarning'
#: Actor class names that carry it.
RIFT_CLASSES = ('BP_OMDRift_C',)
#: The rift Blueprint whose component template holds the default box.
RIFT_BLUEPRINT = '/Game/Environments/Shared/BP_OMDRift'
#: Full size the editor offers per axis, in uu: (min, max, step).
SIZE_RANGE = (100.0, 20000.0, 50.0)


def _props(pkg, index):
    from riftlord.core import uetags
    names = [n for n, _h in pkg.names]
    props, _end = uetags.read(pkg.exports[index - 1]['data'], names, 0)
    return props


def _vector(props, name, default):
    for p in props:
        if p.name == name and isinstance(p.value, (bytes, bytearray)) and len(p.value) == 12:
            return list(struct.unpack('<3f', p.value))
    return list(default)


@functools.lru_cache(maxsize=1)
def class_extent():
    """The class template's half-size, [x, y, z]."""
    from riftlord.core import gamefiles
    from riftlord.build import levelbuild
    from riftlord.formats.ue.uassetio import Package
    pkg = Package(*gamefiles.package(RIFT_BLUEPRINT))
    pkg.load_payloads()
    for i in range(len(pkg.exports)):
        if levelbuild._name_of(pkg, i) == COMPONENT + '_GEN_VARIABLE':
            return _vector(_props(pkg, i + 1), 'BoxExtent', (0.0, 0.0, 0.0))
    raise SystemExit('%s has no %s template' % (RIFT_BLUEPRINT, COMPONENT))


def boxes(pkg):
    """Package indices of every rift's AnnounceWarning component in `pkg`."""
    from riftlord.build import levelbuild
    out = []
    for i, e in enumerate(pkg.exports):
        if levelbuild._name_of(pkg, i) != COMPONENT:
            continue
        outer = struct.unpack_from('<i', e['meta'], levelbuild.OUTER_AT)[0]
        if outer <= 0:
            continue
        cls = levelbuild.resolve(pkg, struct.unpack_from('<i', pkg.exports[outer - 1]['meta'],
                                                         0)[0])[1]
        if cls in RIFT_CLASSES:
            out.append(i + 1)
    return out


def read(pkg, index):
    """{'size': full world size [x, y, z], 'offset': [x, y, z], 'scale': [x, y, z]}."""
    props = _props(pkg, index)
    half = _vector(props, 'BoxExtent', class_extent())
    scale = _vector(props, 'RelativeScale3D', (1.0, 1.0, 1.0))
    return {'size': [round(2 * h * s, 3) for h, s in zip(half, scale)],
            'offset': _vector(props, 'RelativeLocation', (0.0, 0.0, 0.0)),
            'scale': scale}


@functools.lru_cache(maxsize=64)
def _shipped(level):
    from riftlord.core import gamefiles
    from riftlord.build.minimap import mapdata
    from riftlord.formats.ue.uassetio import Package
    pkg = Package(*gamefiles.package(mapdata.map_path(level)))
    pkg.load_payloads()
    found = boxes(pkg)
    return read(pkg, found[0]) if found else None


def shipped(level):
    """read() of the level's own rift warning box, or None."""
    got = _shipped(level)
    return dict(got) if got else None


def clean(value):
    """A plan's riftWarning value validated to three sizes in SIZE_RANGE, or None."""
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError('rift warning size needs three numbers')
    lo, hi, _step = SIZE_RANGE
    out = [float(v) for v in value]
    if any(not lo <= v <= hi for v in out):
        raise ValueError('rift warning size must be between %g and %g' % (lo, hi))
    return out


def apply(builder, plan, report):
    """Write the plan's warning size into every rift of the level being built."""
    want = clean(((plan or {}).get('world') or {}).get(PLAN_KEY))
    if want is None:
        return
    found = boxes(builder.pkg)
    for index in found:
        now = read(builder.pkg, index)
        half = [w / 2.0 / (s or 1.0) for w, s in zip(want, now['scale'])]
        builder.set_vector(index, 'BoxExtent', half)
        if any(now['offset']):
            builder.set_vector(index, 'RelativeLocation',
                               [o * w / (n or w) for o, w, n
                                in zip(now['offset'], want, now['size'])])
    if found:
        report['notes'].append('rift warning box set to %s uu on %d rift(s)'
                               % (' x '.join('%g' % v for v in want), len(found)))
    else:
        report['skipped'].append('rift warning: this level has no rift to resize')
