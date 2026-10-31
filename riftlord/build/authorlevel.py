"""Author a converted level's own sublevels, so it borrows nothing it can write itself.

    python3 authorlevel.py --mod TheCrunch --out .

`makelevel.py` copies a shipped persistent level and re-points its sublevels;
`levelbuild.py` builds the geometry sublevel. This is the rest of step 3: the
sublevels a level needs besides its geometry, written as our own packages.

**`_Lighting` is the one that cannot be skipped.** Scenario_01's persistent level holds
exactly one point light; the sun, the sky light, the fog, the post-process volume and
the skybox mesh are all in `Scenario_01_Lighting`. So dropping the host's sublevels
leaves a black map unless we bring a light rig of our own.

**Every light here has to be Movable.** A Static or Stationary light contributes through
baked lightmaps, and nothing we author has any -- the geometry is emitted into a cooked
package and no lighting build ever runs over it. Measured on the donor: all four
`DirectionalLight`s already ship Movable, and the `SkyLight` ships **Static**, so it is
the one that would silently have gone dark. `movable()` rewrites them all rather than
trusting the donor, because the cost of being wrong is a map nobody can see.

**The rig is lifted, not invented.** `AtmosphericFogComponent` carries 594 KB of
precomputed atmosphere tables and the post-process volume carries a brush and a model;
a hand-written substitute for either is a good way to produce something that parses
offline and crashes in game.
"""

import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.core import gamefiles  # noqa: E402
from riftlord.build import levelbuild  # noqa: E402
from riftlord.build.navigation import navvolinject  # noqa: E402
from riftlord.formats.ue.uassetio import Package  # noqa: E402

#: The level the light rig is lifted from. Scenario_01's, because a converted map is
#: anchored onto Scenario_01's own player start, so the donor's world-space light and
#: skybox positions already line up with where the geometry lands.
LIGHT_SOURCE = '/Game/Maps/Campaign/Scenario_01/Scenario_01_Lighting'

#: In lift order. **This exact set was approved in game on TheCrunch** -- "skybox seems
#: fine, light levels are good, I don't notice much fog and normally there wouldn't be
#: much fog in Crunch".
#:
#: `AtmosphericFog` is deliberately NOT here. It loads (its 594 KB of precomputed tables
#: are inline and travel intact) but it renders atmospheric scattering over the sky, and
#: the skybox mesh plus these lights already produce the look the author signed off. The
#: rule is not to change an approved appearance for the sake of completeness; pass it in
#: `classes` if a level ever wants it.
LIGHT_CLASSES = ('DirectionalLight', 'SkyLight', 'ExponentialHeightFog',
                 'PostProcessVolume')

#: Lifted by mesh rather than by class: it is an ordinary StaticMeshActor.
SKYBOX_MESH = 'SM_Skybox_Mesh'

MOVABLE = 'EComponentMobility::Movable'


def _package(path):
    ua, ux = gamefiles.package(path)
    pkg = Package(ua, ux)
    pkg.load_payloads()
    return pkg


def _import_name(pkg, package_index):
    """The object name an import package index names, or None for anything else."""
    if package_index >= 0:
        return None
    raw = pkg.imports[-package_index - 1]
    ni = struct.unpack_from('<i', raw, 20)[0]
    return pkg.names[ni][0] if 0 <= ni < len(pkg.names) else None


def classes_of(pkg, want):
    """Every export index of class `want`, in export order."""
    from riftlord.build import apply as _apply
    return [i for i in range(len(pkg.exports))
            if _apply._class_at(pkg, i) == want]


def actors_using_mesh(pkg, mesh_name):
    """Actor export indices whose component's `StaticMesh` names `mesh_name`."""
    from riftlord.build import apply as _apply
    out = []
    for i in range(len(pkg.exports)):
        if not (_apply._class_at(pkg, i) or '').endswith('StaticMeshComponent'):
            continue
        data = bytes(pkg.exports[i]['data'] or b'')
        for name, _kind, _start, end in navvolinject.walk_properties(data, pkg.names):
            if name != 'StaticMesh':
                continue
            oi = struct.unpack_from('<i', data, end - 4)[0]
            if _import_name(pkg, oi) == mesh_name:
                outer = struct.unpack_from('<i', pkg.exports[i]['meta'],
                                           levelbuild.OUTER_AT)[0]
                if outer > 0:
                    out.append(outer - 1)
    return sorted(set(out))


def movable(builder):
    """Make every light component in the package Movable. Returns what changed.

    `Mobility` is a ByteProperty whose payload is an FName -- the enum entry's own name,
    not an ordinal -- so the enum name has to travel in the tag as well. Both come out
    of the donor so neither is guessed.
    """
    from riftlord.build import apply as _apply
    pkg = builder.pkg
    changed = []
    for i in range(len(pkg.exports)):
        cls = _apply._class_at(pkg, i) or ''
        if 'LightComponent' not in cls:
            continue
        data = bytes(pkg.exports[i]['data'] or b'')
        span = builder._tag_span(bytearray(data), 'Mobility')
        enum = 'EComponentMobility'
        was = None
        if span is not None:
            _start, _end, at = span
            was = pkg.names[struct.unpack_from('<i', data, at)[0]][0]
            enum = pkg.names[struct.unpack_from('<i', data, _start + 24)[0]][0]
            if was == MOVABLE:
                continue
        builder.set_property(i + 1, 'Mobility', 'ByteProperty',
                             struct.pack('<ii', pkg.add_name(MOVABLE), 0),
                             struct_name=enum)
        changed.append((levelbuild._name_of(pkg, i), was or 'absent'))
    return changed


#: The colour grade a lifted `PostProcessVolume` carries is the DONOR MAP's, and
#: Scenario_01's is a cool night grade: `ColorGamma` pulls red and green to 0.82 while
#: leaving blue at 1.0, `ColorOffsetShadows` lifts blue in the shadows, and saturation
#: goes up 10%. Applied to another map it reads as a cyan cast -- measured against OMD2
#: screenshots, our frames had red crushed to 26 against blue 125 and a mean saturation
#: of 0.80 where OMD2's is 0.54.
#:
#: Neutralised rather than dropped, so the volume still contributes its ambient
#: occlusion, bloom and exposure, which are not map-specific.
NEUTRAL_GRADE = {
    'ColorSaturation': (1.0, 1.0, 1.0, 1.0),
    'ColorGamma': (1.0, 1.0, 1.0, 1.0),
    'ColorOffsetShadows': (0.0, 0.0, 0.0, 0.0),
}


def neutralise_grade(builder, grade=None):
    """Reset the donor map's colour grading inside `FPostProcessSettings`.

    An in-place write: each value is an `FVector4`, so nothing changes size and no
    export grows. The fields live INSIDE the `Settings` struct, which is a nested
    property stream, so they are found by walking that body rather than the export's
    top level.
    """
    import struct as _struct
    from riftlord.build import apply as _apply
    pkg = builder.pkg
    names = [n for n, _h in pkg.names]
    want = dict(grade or NEUTRAL_GRADE)
    changed = []
    for i in range(len(pkg.exports)):
        if _apply._class_at(pkg, i) != 'PostProcessVolume':
            continue
        data = bytearray(pkg.exports[i]['data'] or b'')
        span = builder._tag_span(data, 'Settings')
        if span is None:
            continue
        _start, end, at = span
        o = at
        while o + 8 < end:
            ni = _struct.unpack_from('<i', data, o)[0]
            if not 0 <= ni < len(names):
                break
            name = names[ni]
            if name == 'None':
                break
            ti = _struct.unpack_from('<i', data, o + 8)[0]
            kind = names[ti] if 0 <= ti < len(names) else ''
            size = _struct.unpack_from('<i', data, o + 16)[0]
            head = 25
            if kind == 'BoolProperty':
                head, size = 26, 0
            elif kind == 'StructProperty':
                head = 49
            elif kind in ('ArrayProperty', 'ByteProperty', 'EnumProperty'):
                head = 33
            if name in want and kind == 'StructProperty' and size == 16:
                was = _struct.unpack_from('<4f', data, o + head)
                _struct.pack_into('<4f', data, o + head, *want[name])
                changed.append((name, [round(v, 3) for v in was], list(want[name])))
            o += head + size
        pkg.exports[i]['data'] = bytes(data)
    return changed


def lighting(mod, source=LIGHT_SOURCE, skybox=True, classes=None, neutral_grade=True):
    """((uasset, uexp), report) for `<mod>_Map01_Lighting`.

    `classes` narrows the rig to a subset, which is how a control ladder isolates one
    actor kind: the full rig crashed at the loading screen on its first test, and every
    pak in that round has to differ from the known-good one by exactly one thing.
    """
    src = _package(source)
    b = levelbuild.Builder('%s_Map01_Lighting' % mod)
    report = {'source': source, 'lifted': [], 'skybox': 0,
              'classes': tuple(classes or LIGHT_CLASSES)}
    for cls in (classes or LIGHT_CLASSES):
        for n, i in enumerate(classes_of(src, cls)):
            b.lift_actor(src, i, rename='%s_%d' % (cls, n))
            report['lifted'].append('%s_%d' % (cls, n))
    if skybox:
        for n, i in enumerate(actors_using_mesh(src, SKYBOX_MESH)):
            b.lift_actor(src, i, rename='Skybox_%d' % n)
            report['skybox'] += 1
    report['mobility'] = movable(b)
    report['grade'] = neutralise_grade(b) if neutral_grade else []
    return b.write(), report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--mod', default='TheCrunch')
    ap.add_argument('--out')
    args = ap.parse_args(argv)
    (ua, ux), rep = lighting(args.mod)
    print('%s_Map01_Lighting: %d actor(s) from %s'
          % (args.mod, len(rep['lifted']), rep['source'].rsplit('/', 1)[-1]))
    print('   %s' % ', '.join(rep['lifted']))
    print('   skybox actor(s): %d' % rep['skybox'])
    print('   made movable: %s' % (rep['mobility'] or 'nothing (all were already)'))
    for name, was, now in rep.get('grade') or []:
        print('   grade %-20s %s -> %s' % (name, was, now))
    print('   %d + %d bytes' % (len(ua), len(ux)))
    if args.out:
        stem = os.path.join(args.out, '%s_Map01_Lighting' % args.mod)
        open(stem + '.umap', 'wb').write(ua)
        open(stem + '.uexp', 'wb').write(ux)
        print('   -> %s.umap' % stem)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
