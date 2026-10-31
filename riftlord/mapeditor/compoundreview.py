"""Review images of a compound: its textured default beside its textured shipped spot.

    python3 -m riftlord.mapeditor.compoundreview OUT_DIR [KEY ...] [--cell 250]

`pair(d, idx, cat)` draws a compound definition (compounds.COMPOUNDS[key], or any with
`source`) at its default params, and the level it was cut from around its `source` in the
same frame: its own pieces textured and shown through what hides them, the level's other
pieces textured and blended toward CONTEXT. Both from the front three-quarter view and from
behind (YAWS). `sheets` lays pairs out as contact sheets.
"""

import argparse
import functools
import json
import math
import os

import numpy as np

#: View yaws: the preview's three-quarter view and the one from behind (degrees).
YAWS = (35.0, 215.0)
#: The level's other pieces: blended this share toward this colour.
CONTEXT = (110, 128, 160)
CONTEXT_SHARE = 0.55
#: The region around a compound's box the shipped spot shows: this share of its size, at least
#: MARGIN_MIN uu.
MARGIN_SHARE = 0.35
MARGIN_MIN = 150.0
CELL = 250


@functools.lru_cache(maxsize=1024)
def geometry(name, kind):
    """A layout or scan piece as a preview model (omd3thumb): a kit, door or Blueprint by its
    parts (kitdonors.model), a mesh as served, anything else (an effect) as a small box."""
    from riftlord.mapeditor import compoundmine, kitdonors, omd3serve
    got = None
    if kind in ('kit', 'door', 'bp'):
        got = kitdonors.model(name)
    elif kind == 'mesh':
        got = omd3serve.mesh(name)
    if got and got.get('verts') and got.get('tris'):
        return got
    h = compoundmine.POINT_HALF
    verts = [[x, y, z] for x in (-h, h) for y in (-h, h) for z in (-h, h)]
    tris = [0, 1, 3, 0, 3, 2, 4, 6, 7, 4, 7, 5, 0, 4, 5, 0, 5, 1,
            2, 3, 7, 2, 7, 6, 0, 2, 6, 0, 6, 4, 1, 5, 7, 1, 7, 3]
    return {'verts': verts, 'tris': tris}


def _box(parts):
    lo = np.min([p['v'].min(axis=0) for p in parts], axis=0)
    hi = np.max([p['v'].max(axis=0) for p in parts], axis=0)
    return lo, hi


def default(d):
    """(model, (lo, hi)) of a compound definition at its default params, from the origin."""
    from riftlord.mapeditor import compoundmine, compounds, omd3thumb
    parts = [omd3thumb.place(geometry(n, k), np.asarray(m, dtype=np.float64))
             for n, k, m in compounds.layout_of(d) if n not in compoundmine.VOLUME_CLASSES]
    parts = [p for p in parts if p is not None]
    return omd3thumb.merge(parts), _box(parts)


def spot(d, idx, cat, box):
    """The model of `d`'s source level within `box` (lo, hi), in the compound's frame: its
    own pieces textured and ghosted, the level's other pieces (no environment or terrain,
    no other effects) blended toward CONTEXT. None when nothing is there."""
    from riftlord.mapeditor import compoundmine as cm
    from riftlord.mapeditor import compounds, omd3thumb
    lay = compounds.layout_of(d)
    src = d['source']
    units = idx.scanned[src['level']]
    mats = idx.mats[src['level']]
    names = {(u[2], u[4]): i for i, u in enumerate(units)}
    mine = {names[tuple(a)] for a in src['actors'] if a and tuple(a) in names}
    frame = np.linalg.inv(cm._m4(src['anchor']) @ np.linalg.inv(np.array(lay[0][2], float)))
    lo, hi = box
    parts = []
    for i, u in enumerate(units):
        kind = u[1]
        if kind == 'fx' and i not in mine:
            continue
        if i not in mine and cm.role(u[0], kind, cat.meshes) in ('env', 'terrain'):
            continue
        M = frame @ mats[i]
        b = cat.world_box(u[0], kind, M)
        if np.any(b[3:] < lo) or np.any(b[:3] > hi):
            continue
        key = u[0][3:] if kind == 'fx' else u[0]
        parts.append(omd3thumb.place(
            geometry(key, {'bp': 'kit', 'mesh': 'mesh', 'fx': 'fx'}[kind]), M,
            mix=None if i in mine else (CONTEXT, CONTEXT_SHARE), ghost=i in mine))
    return omd3thumb.merge(parts)


def pair(d, idx, cat, size=CELL, yaws=YAWS):
    """[default, shipped spot] images (PIL, `size` square) for each view in `yaws` (degrees),
    in that order: the compound textured at its default params beside its `source` cut from
    the level with every piece around it, in one frame. idx: compoundmine.Index(scan()),
    cat: compoundmine.Catalogue()."""
    from riftlord.mapeditor import omd3thumb
    left, (lo, hi) = default(d)
    margin = np.maximum(MARGIN_MIN, MARGIN_SHARE * (hi - lo).max())
    box = (lo - margin, hi + margin)
    right = spot(d, idx, cat, box)
    out = []
    for yaw in yaws:
        for model in (left, right):
            image = omd3thumb.render(model, size, yaw=math.radians(yaw), box=box) \
                if model else None
            if image is None:
                from PIL import Image
                image = Image.new('RGB', (size, size), omd3thumb.BACKGROUND)
            out.append(image)
    return out


def sheets(defs, out_dir, cell=CELL, per_row=2, rows=5, log=print):
    """Contact sheets of compound definitions (each with 'key' and `source`): per compound
    its pair() images in a row, labelled. Returns the sheet paths and writes index.json."""
    from PIL import Image, ImageDraw
    from riftlord.mapeditor import compoundmine as cm
    from riftlord.mapeditor import compounds
    os.makedirs(out_dir, exist_ok=True)
    idx = cm.Index(cm.scan())
    cat = cm.Catalogue()
    width = per_row * (2 * len(YAWS) * cell + 10)
    height = rows * (cell + 32)
    paths, index = [], []
    for s in range(0, len(defs), per_row * rows):
        sheet = Image.new('RGB', (width, height), (12, 13, 16))
        draw = ImageDraw.Draw(sheet)
        for n, d in enumerate(defs[s:s + per_row * rows]):
            x = (n % per_row) * (2 * len(YAWS) * cell + 10)
            y = (n // per_row) * (cell + 32)
            for k, image in enumerate(pair(d, idx, cat, cell)):
                sheet.paste(image, (x + k * cell, y))
            draw.text((x + 3, y + 2), '#%d %s' % (s + n, d['key']), fill=(255, 230, 120))
            draw.text((x + 3, y + cell + 2), ('%s   n%d L%d  %s' % (
                d.get('label', ''), d.get('copies', 0), len(d.get('levels') or {}),
                d['source']['level']))[:120], fill=(235, 235, 235))
            draw.text((x + 3, y + cell + 16), ('%s +%d   left: default, right: shipped' % (
                d['anchor']['name'], len(compounds.layout_of(d)) - 1))[:120],
                fill=(150, 160, 180))
            index.append({'n': s + n, 'key': d['key'], 'sheet': s // (per_row * rows)})
        path = os.path.join(out_dir, 'sheet_%02d.png' % (s // (per_row * rows)))
        sheet.save(path)
        paths.append(path)
        log(path)
    with open(os.path.join(out_dir, 'index.json'), 'w') as fh:
        json.dump(index, fh, indent=1)
    return paths


def main(argv=None):
    from riftlord.mapeditor import compounds
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('out')
    ap.add_argument('keys', nargs='*', help='compound keys (default every compound)')
    ap.add_argument('--cell', type=int, default=CELL)
    args = ap.parse_args(argv)
    keys = args.keys or list(compounds.COMPOUNDS)
    unknown = [k for k in keys if k not in compounds.COMPOUNDS]
    if unknown:
        ap.error('unknown compound(s): %s' % ', '.join(unknown))
    defs = [dict(compounds.COMPOUNDS[k], key=k) for k in keys
            if compounds.COMPOUNDS[k].get('source')]
    from riftlord.core import heavyslot
    with heavyslot.slot('compound review sheets'):
        sheets(defs, args.out, args.cell)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
