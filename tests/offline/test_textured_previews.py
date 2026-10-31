"""Textured New Object previews (riftlord/mapeditor/omd3thumb.py): a stone wall and a beam
wall render with their textures' variance, not a flat tint; each section takes the material
the Map Editor viewport gives it; texture.parse picks the small mip asked for; a compound
draws every member with its own materials; the review pair renders; untextured models keep
the old flat tint; the preview cache names and the Map Editor's preview URLs carry the
drawing's version.

    python3 tests/offline/test_textured_previews.py
"""
import os
import re
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import numpy as np                                        # noqa: E402

from riftlord.mapeditor import omd3serve, omd3thumb       # noqa: E402

#: A stone wall mesh (rustic brick) and its texture; a kit wall with wooden beams.
STONE = 'Wall_Sec_2_2_V2_RB'
STONE_TEXTURE = '/Game/Environments/CastleOrder/Walls/RusticBrick/Mat/Walls_Base_RusticBrick_V3_BC'
BEAM_WALL = 'Wall_RB_WD_v1_1x2_C'
WOOD_TEXTURE = '/Game/Environments/CastleOrder/Woodbeams/Mat/WoodBeam_BC'
#: A compound of a beam wall and stone pillars.
COMPOUND = 'stone_wall_with_top_beam_between_pillars'
#: A mesh of three sections in slot order 0, 2, 1, one of them textured.
MULTI = 'Jar'
#: A drawn face's spread of luminance must reach this share of what its texture alone gives
#: at the mip drawn, times the face's light (the 3x downsample averages some of it away).
VARIANCE_SHARE = 0.5
SIZE = 96
checks = []


def check(name, ok, detail=''):
    checks.append(ok)
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, (': %s' % (detail,)) if detail else ''))


def luminance(rgb):
    rgb = np.asarray(rgb, dtype=np.float64)
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


def biggest_face(flat):
    """Mask of the most common colour of a flat render that is not the background."""
    px = flat.reshape(-1, 3)
    drawn = (px != np.asarray(omd3thumb.BACKGROUND, dtype=np.uint8)).any(axis=1)
    colours, counts = np.unique(px[drawn], axis=0, return_counts=True)
    top = colours[counts.argmax()]
    return (flat == top).all(axis=2)


def stone_wall():
    mesh = omd3serve.mesh(STONE)
    check('%s reads with UV0 for every vertex' % STONE,
          bool(mesh) and len(mesh.get('uvs') or []) == len(mesh['verts']))
    faces, surfs = omd3thumb.surfaces(mesh)
    check('%s: one surface, its rustic brick texture' % STONE,
          [s['texture'] for s in surfs] == [STONE_TEXTURE], surfs)
    t = time.time()
    textured = np.asarray(omd3thumb.render(mesh, SIZE))
    took = time.time() - t
    flat = np.asarray(omd3thumb.render({'verts': mesh['verts'], 'tris': mesh['tris']}, SIZE))
    face = biggest_face(flat)
    # without supersampling the flat face is one colour; its interior is where both compare
    inner = face & np.roll(face, 1, 0) & np.roll(face, -1, 0) & np.roll(face, 1, 1) & \
        np.roll(face, -1, 1)
    flat_spread = luminance(flat[inner]).std()
    got = luminance(textured[inner]).std()
    # what the texture alone gives at the mip that face is drawn at, times its light
    shade = luminance(flat[inner]).mean() / luminance(omd3thumb.TINT)
    tint = np.asarray(surfs[0]['tint'] or (1, 1, 1)) ** (1 / omd3thumb.GAMMA)
    side = omd3thumb._side(SIZE * omd3thumb.SUPERSAMPLE)
    tex = omd3thumb.texture_rgb(STONE_TEXTURE, side)
    expect = luminance(tex * tint).std() * shade
    check('%s: its biggest face (%d px) varies like its texture, flat %.1f, textured %.1f, '
          'texture x light %.1f (rendered in %.2f s)' % (STONE, inner.sum(), flat_spread, got,
                                                          expect, took),
          inner.sum() > 200 and flat_spread < 1.0 and got >= VARIANCE_SHARE * expect)
    hue = textured[inner].mean(axis=0)
    check('%s: drawn in the texture\'s colour, not the flat tint (%s vs tint %s)'
          % (STONE, np.round(hue / hue.sum(), 3), np.round(np.asarray(omd3thumb.TINT) /
                                                              sum(omd3thumb.TINT), 3)),
          np.abs(hue / hue.sum() - np.asarray(omd3thumb.TINT) / sum(omd3thumb.TINT)).max()
          > 0.01)


def beam_wall():
    from riftlord.mapeditor import kitdonors
    model = kitdonors.model(BEAM_WALL)
    textures = {s['texture'] for s in model['surfaces']}
    check('%s: its parts keep their own materials (wood and stone)' % BEAM_WALL,
          WOOD_TEXTURE in textures and len(textures) >= 2, sorted(map(str, textures)))
    wood = [k for k, s in enumerate(model['surfaces']) if s['texture'] == WOOD_TEXTURE]
    image = np.asarray(omd3thumb.render(model, SIZE))
    only = dict(model, faces=[0 if f in wood else 1 for f in model['faces']],
                surfaces=[{'texture': WOOD_TEXTURE, 'tint': None},
                          {'texture': None, 'tint': None}])
    mask = np.asarray(omd3thumb.render(only, SIZE, tint=(0, 0, 0)))
    beam = luminance(mask) > 0
    spread = luminance(image[beam]).std() if beam.any() else 0.0
    check('%s: the beams draw in wood (%d px, spread %.1f)' % (BEAM_WALL, beam.sum(), spread),
          beam.sum() > 30 and spread > 1.0)


def sections():
    mesh = omd3serve.mesh(MULTI)
    faces, surfs = omd3thumb.surfaces(mesh)
    want = [None if not t else t for t in mesh['slotTextures']]
    check('%s: a surface per slot, as the viewport gives them' % MULTI,
          [s['texture'] for s in surfs] == want, surfs)
    first = {}
    for sec in mesh['sections']:
        first[sec['slot']] = faces[sec['first'] // 3]
    check('%s: each section\'s triangles take their own slot' % MULTI,
          all(first[k] == k for k in first), first)
    over = omd3thumb.surfaces(mesh, material=mesh['slots'][0])[1]
    check('%s: an override material paints the whole mesh' % MULTI, len(over) == 1, over)


def mips():
    small = omd3thumb.texture_rgb(STONE_TEXTURE, omd3thumb.TEXTURE_SIDE_MIN)
    big = omd3thumb.texture_rgb(STONE_TEXTURE, 256)
    check('the smallest mip at least as big as asked: %s for %d, %s for 256' % (
        small.shape[:2], omd3thumb.TEXTURE_SIDE_MIN, big.shape[:2]),
        max(small.shape[:2]) == omd3thumb.TEXTURE_SIDE_MIN and max(big.shape[:2]) == 256)
    check('mip edge for 100 texels across: %d' % omd3thumb._side(100),
          omd3thumb._side(100) == 128 and omd3thumb._side(10 ** 6) == omd3thumb.TEXTURE_SIDE_MAX)


def compound():
    from riftlord.mapeditor import compounds
    model = compounds.model(COMPOUND)
    textures = {s['texture'] for s in model['surfaces'] if s['texture']}
    check('%s: every member keeps its own materials (%d textures)' % (COMPOUND, len(textures)),
          WOOD_TEXTURE in textures and len(textures) >= 3, sorted(textures))
    image = omd3thumb.render(model, SIZE)
    check('%s renders' % COMPOUND, image is not None and image.size == (SIZE, SIZE))


def review():
    from riftlord.mapeditor import compoundmine, compoundreview, compounds
    d = compounds.COMPOUNDS[COMPOUND]
    idx = compoundmine.Index(compoundmine.scan([d['source']['level']]))
    images = compoundreview.pair(d, idx, compoundmine.Catalogue(), 64)
    drawn = [int((np.asarray(i) != np.asarray(omd3thumb.BACKGROUND, np.uint8)).any(axis=2)
                 .sum()) for i in images]
    check('review pair of %s: default and shipped spot, front and behind (%s px drawn)'
          % (COMPOUND, drawn), len(images) == 4 and min(drawn) > 100)


def untextured():
    quad = {'verts': [[0, 0, 0], [100, 0, 0], [100, 0, 100], [0, 0, 100]],
            'tris': [0, 1, 2, 0, 2, 3]}
    image = np.asarray(omd3thumb.render(quad, SIZE))
    px = image.reshape(-1, 3)
    px = px[(px != np.asarray(omd3thumb.BACKGROUND, dtype=np.uint8)).any(axis=1)]
    colours, counts = np.unique(px, axis=0, return_counts=True)
    face = colours[counts.argmax()].astype(float)
    tint = np.asarray(omd3thumb.TINT, dtype=float)
    check('a model with no materials keeps the flat tint (%s)' % face.astype(int),
          np.abs(face / face.sum() - tint / tint.sum()).max() < 0.01)


def cache_keys():
    name = omd3serve.preview_name(STONE)
    check('mesh preview cache names carry the drawing version (%s)' % name,
          omd3thumb.STYLE in name)
    with open(os.path.join(ROOT, 'web', 'mapeditor', 'src', 'main.js')) as fh:
        got = re.search(r"const THUMB_STYLE = '([^']*)'", fh.read())
    check('main.js THUMB_STYLE equals omd3thumb.STYLE (%s)' % (got and got.group(1)),
          bool(got) and got.group(1) == omd3thumb.STYLE)
    from riftlord.mapeditor import kitdonors
    keep = omd3serve.PREVIEWS
    with tempfile.TemporaryDirectory(prefix='thumbtest-') as tmp:
        omd3serve.PREVIEWS = tmp
        try:
            png = kitdonors.thumbnail(BEAM_WALL)
            files = os.listdir(tmp)
        finally:
            omd3serve.PREVIEWS = keep
    check('a kit preview is kept under a name with the drawing version (%s)' % files,
          bool(png) and len(files) == 1 and omd3thumb.STYLE in files[0])


def main():
    for part in (stone_wall, beam_wall, sections, mips, compound, review, untextured,
                 cache_keys):
        try:
            part()
        except Exception as exc:                     # noqa: BLE001
            check('%s raised' % part.__name__, False, '%s: %s' % (type(exc).__name__, exc))
    print('%d checks, %d failed' % (len(checks), checks.count(False)))
    return 1 if False in checks else 0


if __name__ == '__main__':
    raise SystemExit(main())
