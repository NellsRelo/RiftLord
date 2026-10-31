"""Offline checks on Preview effects (makepreview.add_effect): a mod minion's Preview
Effect becomes a live particle system on its creature in the hub diorama.

    python3 test_previewfx.py [--keep]

Needs the pinned copy of Wraiths_P.pak (tests/fixtures/fixturelibs.PINNED). It is copied into a scratch mods folder (modminions.ROOTS)
with its Minions.yaml rewritten so that four rows carry stand-in effect keys:

- Wraiths_ZombieOrcMedium: the shipped status fire P_Fire on bone `head`, with an offset,
  a rotation and a scale;
- Wraiths_SkeletonOrc: the mod's own P_SkeletonRiftGlow (in the row's Packages), at the
  mesh root;
- Wraiths_ZombieOrcLight: an Effect path nothing ships, a bad offset, an unknown bone;
- Wraiths_Ghoul: an Effect that is a skeletal mesh, not a ParticleSystem;
- Wraiths_Gremlin: the mod's glow on a bone its mesh does not have (shown at the root).

A preview is built for the four, read back, and gated as a mission build gates it. The
game's ~mods is never read. Exit 0 when every check passes.
"""

import argparse
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'fixtures'))

from riftlord.missions import modminions  # noqa: E402

MOD_PAK = 'Wraiths_P.pak'
KEY = 'Wraiths'
FIRE, GLOW, LIGHT, GHOUL = ('Wraiths_ZombieOrcMedium', 'Wraiths_SkeletonOrc',
                            'Wraiths_ZombieOrcLight', 'Wraiths_Ghoul')
SHIPPED_FX = '/Game/Proto/FX/Particles/StatusFX/P_Fire.P_Fire'
MOD_FX = '/Game/Mods/Wraiths/Skeletons/FX/P_SkeletonRiftGlow.P_SkeletonRiftGlow'
KEYS = {
    FIRE: {'Effect': SHIPPED_FX, 'EffectSocket': 'head', 'EffectOffset': '0 4.5 12',
           'EffectRotation': '-90 -28.47 -0.208', 'EffectScale': 0.8},
    GLOW: {'Effect': MOD_FX},
    LIGHT: {'Effect': '/Game/Nowhere/P_Missing.P_Missing', 'EffectOffset': 'banana',
            'EffectSocket': 'no_such_bone'},
    GHOUL: {'Effect': '/Game/Mods/Wraiths/Ghoul/Ghoul_SKM.Ghoul_SKM'},
    'Wraiths_Gremlin': {'Effect': MOD_FX, 'EffectSocket': 'no_such_bone'},
}
GREMLIN = 'Wraiths_Gremlin'
#: The Burning Skull with the keys Wraiths gives it (win64-47, 2026-10-24), on the shipped
#: fire until the mod's own P_SkullFire is in the pak; a pak whose row has an Effect keeps it.
SKULL = 'Wraiths_SkullFlyer'
SKULL_KEYS = {'Effect': SHIPPED_FX, 'EffectSocket': 'head', 'EffectOffset': '-32.927 -17.347 0',
              'EffectRotation': '-90 -28.47 -0.208', 'EffectScale': 1.0}
PREVIEW = '/Game/Mods/PreviewFxTest/PreviewFxTest_Enemies01'
SCRATCH_PARENT = '/mnt/2tb_pro_a/tmp' if os.path.isdir('/mnt/2tb_pro_a/tmp') else None

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))
    print('  %s  %s%s' % ('PASS' if ok else 'FAIL', name, (': ' + str(detail)) if detail else ''))


def stage(installed, scratch):
    """The mod pak copied to a scratch mods folder with KEYS in its rows' Preview blocks."""
    import yaml
    from riftlord.core import paksource
    files = paksource.entries(installed)
    inner = 'OMD/Content/Mods/%s/OrcsMustUI/%s' % (KEY, modminions.MANIFEST)
    man = yaml.safe_load(files[inner].decode('utf-8'))
    for entry in man['Minions']:
        entry.setdefault('Preview', {}).update(KEYS.get(entry['Row'], {}))
        if entry['Row'] == SKULL and not entry['Preview'].get('Effect'):
            entry['Preview'].update(SKULL_KEYS)
    files[inner] = yaml.safe_dump(man, sort_keys=False).encode('utf-8')
    mods = os.path.join(scratch, 'mods')
    os.makedirs(mods)
    paksource.write(os.path.join(mods, MOD_PAK), files, keep_omui=False)
    modminions.ROOTS = [mods]
    return os.path.join(mods, MOD_PAK)


def mission_files(pfiles, rows_used, pak):
    """The preview plus the packages a mission spawning `rows_used` carries."""
    src = modminions.Source(pak)
    files = {'OMD/Content' + PREVIEW[len('/Game'):] + '.uasset': pfiles[0],
             'OMD/Content' + PREVIEW[len('/Game'):] + '.uexp': pfiles[1]}
    for row in rows_used:
        for gp in modminions.carried_packages(row) or []:
            stem = 'OMD/Content' + gp[len('/Game'):]
            for ext in modminions.EXTS:
                if stem + ext in src:
                    files[stem + ext] = src.read(stem + ext)
    return files


def tags(pkg, i):
    from riftlord.onslaught import makepreview
    names = [s for s, _h in pkg.names]
    data = pkg.exports[i]['data']
    out = {}
    for pn, kind, _s, at, size in makepreview.walk(data, names):
        if kind == 'NameProperty':
            out[pn] = (names[struct.unpack_from('<i', data, at)[0]],
                       struct.unpack_from('<i', data, at + 4)[0])
        elif kind == 'StructProperty' and size == 12:
            out[pn] = tuple(round(v, 3) for v in struct.unpack_from('<3f', data, at))
        elif kind == 'ObjectProperty':
            out[pn] = struct.unpack_from('<i', data, at)[0]
        else:
            out[pn] = kind
    return out


def run(installed, scratch):
    from riftlord.build import trailers
    from riftlord.core import pakspelling
    from riftlord.core import pkgcheck
    from riftlord.formats.ue.uassetio import Package
    from riftlord.mapeditor import probe
    from riftlord.onslaught import makepreview
    pak = stage(installed, scratch)

    print('keys')
    eff, warns = modminions.preview_effect(FIRE)
    check('a full row reads: path, socket, offset, rotation, scale',
          eff == {'path': SHIPPED_FX, 'socket': 'head', 'offset': (0.0, 4.5, 12.0),
                  'rotation': (-90.0, -28.47, -0.208), 'scale': 0.8} and not warns, eff)
    eff, warns = modminions.preview_effect(GLOW)
    check('defaults: mesh root, no offset, no rotation, scale 1',
          eff and eff['socket'] is None and eff['offset'] == (0, 0, 0)
          and eff['rotation'] == (0, 0, 0) and eff['scale'] == 1.0 and not warns, eff)
    eff, warns = modminions.preview_effect(LIGHT)
    check('a bad EffectOffset is a warning, not an error', eff and len(warns) == 1, warns)
    eff, warns = modminions.preview_effect('Wraiths_Lich')
    check('a row without Effect has no effect and no warning', eff is None and not warns)

    print('build')
    units = [FIRE, GLOW, LIGHT, GHOUL, GREMLIN]
    pfiles, rep = makepreview.build(units, PREVIEW)
    check('the preview builds and shows all five', pfiles and len(rep['showing']) == 5,
          rep.get('showing'))
    fx = {c: (p, s) for c, p, s in rep['effects']}
    check('three effects added: shipped fire on head, mod glow at the root twice',
          fx == {FIRE: (SHIPPED_FX, 'head'), GLOW: (MOD_FX, None), GREMLIN: (MOD_FX, None)},
          rep['effects'])
    w = rep['warnings']
    check('warnings name the missing Effect, the bad offset, the non-particle Effect and '
          'the unknown bone',
          any(LIGHT in x and 'neither shipped' in x for x in w)
          and any(LIGHT in x and 'EffectOffset' in x for x in w)
          and any(GHOUL in x and 'not a ParticleSystem' in x for x in w)
          and any(GREMLIN in x and 'no_such_bone' in x for x in w), w)
    check('pkgcheck reads the preview back cleanly',
          not pkgcheck.problems(pfiles[0], pfiles[1], PREVIEW.rsplit('/', 1)[-1], 'object'),
          pkgcheck.problems(pfiles[0], pfiles[1], PREVIEW.rsplit('/', 1)[-1], 'object'))

    print('readback')
    pkg = Package(*pfiles)
    got = makepreview.effect_exports(pkg)
    check('three templates, each with a node and a creature slot as parent',
          len(got) == 3 and all(n is not None and p is not None for _t, n, p in got), got)
    by_mesh = {}
    for t, n, p in got:
        mesh = tags(pkg, tags(pkg, p)['ComponentTemplate'] - 1).get('SkeletalMesh')
        by_mesh[makepreview._import_target(pkg, mesh)[2][0]] = (t, n, p)
    t, n, p = by_mesh.get('ZombieOrc_Medium_SKM', (None,) * 3)
    check('A: the fire is on the Zombie Orc Medium slot', t is not None, sorted(by_mesh))
    if t is not None:
        tt, nt = tags(pkg, t), tags(pkg, n)
        cls, path, form = makepreview._import_target(pkg, tt['Template'])
        check('A: Template imports P_Fire (ParticleSystem, one spelling)',
              (cls, path, form) == ('ParticleSystem', SHIPPED_FX.split('.')[0], ('P_Fire', 0)),
              (cls, path, form))
        check('A: offset, rotation and scale written, no bAutoActivate tag',
              tt.get('RelativeLocation') == (0.0, 4.5, 12.0)
              and tt.get('RelativeRotation') == (-90.0, -28.47, -0.208)
              and tt.get('RelativeScale3D') == (0.8, 0.8, 0.8) and 'bAutoActivate' not in tt,
              tt)
        var = makepreview._export_form(pkg, t)[0][:-len('_GEN_VARIABLE')]
        check('A: node AttachToName head, InternalVariableName its template\'s variable',
              nt.get('AttachToName') == ('head', 0) and var.startswith(makepreview.EFFECT_VAR)
              and nt.get('InternalVariableName') == (var, 0)
              and nt.get('ComponentTemplate') == t + 1, nt)
    for mesh in ('SkeletonOrc_SKM', 'Gremlin_SKM'):
        t, n, p = by_mesh.get(mesh, (None,) * 3)
        tt, nt = (tags(pkg, t), tags(pkg, n)) if t is not None else ({}, {})
        check('%s: no AttachToName, no transform tags (the shipped root-attached shape)' % mesh,
              t is not None and 'AttachToName' not in nt and not {
                  'RelativeLocation', 'RelativeRotation', 'RelativeScale3D'} & set(tt), (tt, nt))

    print('gates')
    files = mission_files(pfiles, units, pak)
    rows_g = makepreview.effect_gates(files)
    check('effect gates pass (%d)' % len(rows_g),
          len(rows_g) == 3 and all(ok for _n, ok, _d in rows_g),
          '; '.join('%s %s %s' % (n, ok, d) for n, ok, d in rows_g))
    rows_t = trailers.gates(files)
    check('trailer gate passes (%d)' % len(rows_t),
          rows_t and all(ok for _n, ok, _d in rows_t),
          '; '.join('%s %s %s' % (n, ok, d) for n, ok, d in rows_t))
    inner = 'OMD/Content' + PREVIEW[len('/Game'):] + '.uasset'
    count, bad = probe.import_problems(files, inner, cache={})
    check('every preview import resolves, by string and number (%d)' % count, not bad, bad[:3])
    twice = [k for k, v in pakspelling.spellings(files).items() if len(v) > 1]
    check('one spelling per package path over the preview and carried packages', not twice,
          twice[:3])
    drop = {k: v for k, v in files.items() if 'P_SkeletonRiftGlow' not in k}
    rows_g = makepreview.effect_gates(drop)
    check('the mod particle not carried: the Template gate fails',
          rows_g and not rows_g[0][1] and 'P_SkeletonRiftGlow' in rows_g[0][2], rows_g[:1])

    print('the Pier probe roster (every Wraiths row)')
    pier = sorted(r for r in modminions.rows() if r.startswith(KEY + '_'))
    want, _w = modminions.preview_effect(SKULL)
    pfiles3, rep3 = makepreview.build(pier, PREVIEW)
    fx = {c: (p, s) for c, p, s in rep3['effects']}
    check('the Burning Skull carries its Effect on head (%s)' % want['path'].rsplit('.', 1)[-1],
          fx.get(SKULL) == (want['path'], 'head'), rep3['effects'])
    pkg3 = Package(*pfiles3)
    skull = None
    for t, n, p in makepreview.effect_exports(pkg3):
        slot = tags(pkg3, p).get('ComponentTemplate')
        mesh = tags(pkg3, slot - 1).get('SkeletalMesh')
        if makepreview._import_target(pkg3, mesh)[2][0] == 'SkullFlyer_SKM':
            skull = (tags(pkg3, t), tags(pkg3, slot - 1))
    check('its effect: offset and rotation as given, on the skull slot, which flies',
          skull and skull[0].get('RelativeLocation') == want['offset']
          and skull[0].get('RelativeRotation') == tuple(round(v, 3) for v in want['rotation'])
          and skull[1].get('RelativeLocation', (0, 0, 0))[2] >= makepreview.FLYER_Z_MIN, skull)
    files3 = mission_files(pfiles3, pier, pak)
    rows3 = makepreview.effect_gates(files3) + trailers.gates(files3)
    count, bad = probe.import_problems(files3, inner, cache={})
    check('its gates pass (%d), and every import resolves (%d)' % (len(rows3), count),
          all(ok for _n, ok, _d in rows3) and not bad,
          '; '.join('%s %s' % (n, d) for n, ok, d in rows3 if not ok) + '; '.join(bad[:3]))

    print('no effects')
    pfiles2, rep2 = makepreview.build(['Wraiths_Lich', 'Wraiths_SkeletonGnoll'], PREVIEW)
    check('a roster without Effect keys adds nothing',
          pfiles2 and not rep2['effects'] and not rep2['warnings']
          and not makepreview.effect_exports(Package(*pfiles2)), rep2['effects'])
    bad = [c for c in checks if not c[1]]
    print('%d of %d checks pass' % (len(checks) - len(bad), len(checks)))
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--keep', action='store_true', help='keep the scratch folder')
    ap.add_argument('--mod-pak', help='a staged Wraiths_P.pak to read instead of the pinned one')
    args = ap.parse_args(argv)
    import fixturelibs
    installed = args.mod_pak or fixturelibs.pinned_mod(MOD_PAK)
    if not installed or not os.path.exists(installed):
        print('skipped: no pinned %s (tests/fixtures/fixturelibs.py --pin)' % MOD_PAK)
        return 0
    scratch = tempfile.mkdtemp(prefix='previewfx-test-', dir=SCRATCH_PARENT)
    try:
        return run(installed, scratch)
    finally:
        if args.keep:
            print('kept %s' % scratch)
        else:
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
