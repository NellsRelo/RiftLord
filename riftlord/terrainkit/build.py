"""One map's terrain library: its ground sources grouped into art sets (one per material), each
cut into flat and ramp tiles (tiles.py) and, for mesh-paint ground, one tile per layer painted
with that layer alone; snow and sand landscapes also get paintable tiles on a carried
mesh-paint instance (mics.py); written through riftlord.fab.library.write_pieces
as pack `Terrain_<map>`, titled "Terrain: <mission>".
"""

import hashlib
import json
import os

import numpy as np

from riftlord.terrainkit import mics
from riftlord.terrainkit import paint
from riftlord.terrainkit import sources
from riftlord.terrainkit import tiles

#: Catalogue pack.kind of a terrain library.
KIND = 'terrain'
#: Library folder (pack id) prefix.
PACK_PREFIX = 'Terrain_'
#: Catalogue role and name word per tile kind.
ROLES = {'flat': 'floor', 'ramp': 'floor'}
WORDS = {'flat': 'Ground', 'ramp': 'Ramp'}
#: Flat tiles per landscape art set (no paint to vary: relief only).
LANDSCAPE_FLAT_TILES = 2
#: Thumbnail texture size (px).
THUMB_TEXTURE = 256


def pack_id(level):
    return PACK_PREFIX + level


def title(level, names=None):
    names = sources.titles() if names is None else names
    return 'Terrain: %s' % names.get(level, level)


def code_hash():
    """The tile rules' code: a change makes every library stale."""
    h = hashlib.sha256()
    for mod in (paint, tiles, sources, mics):
        with open(mod.__file__, 'rb') as fh:
            h.update(fh.read())
    with open(__file__, 'rb') as fh:
        h.update(fh.read())
    return h.hexdigest()


def fingerprint(level_sources):
    """What shapes a map's library: each source's bytes, paint and placement, and the code."""
    h = hashlib.sha256(code_hash().encode())
    for s in sorted(level_sources, key=lambda s: (s['package'], s['component'])):
        h.update(sources.fingerprint_parts(s).encode())
    return h.hexdigest()


def build_id(level_sources):
    """The build id write_pieces gives a map's library now (its writer code and fingerprint)."""
    from riftlord.fab import library
    parts = [library.VERSION, library.logic_hash(*library.PIECE_CODE), fingerprint(level_sources), KIND]
    carried = carried_specs(level_sources)
    if carried:
        parts += [library.logic_hash(*library.MIC_CODE), carried]
    return library.h(parts)[:library.BUILD_ID_LEN]


def carried_specs(level_sources):
    """write_pieces mics for a map: the carried instances its landscapes need."""
    return [mics.spec(n) for n in mics.used(level_sources)]


def art_sets(level_sources):
    """{material: [source]}."""
    out = {}
    for s in level_sources:
        out.setdefault(s['material'], []).append(s)
    return out


def _letters(k):
    s = ''
    k += 1
    while k:
        k, r = divmod(k - 1, 26)
        s = chr(65 + r) + s
    return s


def full_fills(material_layers):
    """[(layer name, RGBA)] of vertex colours painting one layer alone, one per distinct layer
    name (the topmost layer of that name), in blend order."""
    fills = {}
    for k, layer in enumerate(material_layers):
        rgba = [0, 0, 0, 0]
        if layer['channel'] is not None:
            rgba[layer['channel']] = 255
        fills[layer['name']] = (k, rgba)
    return [(name, rgba) for name, (_k, rgba) in sorted(fills.items(), key=lambda kv: kv[1][0])]


def _mesh_set(material, group):
    """Plan items of one mesh-paint art set: FLAT_TILES flats, RAMP_TILES ramps and a full tile
    per layer (paint.layers)."""
    layers = paint.layers(material)
    spans, rasters = [], []
    for s in group:
        P, T, C, UV = sources.geometry(s)
        r = tiles.raster(P, T, C)
        r['source'] = s
        rasters.append(r)
        A = np.c_[P[:, 0], P[:, 1], np.ones(len(P))]
        cu, *_ = np.linalg.lstsq(A, UV[:, 0], rcond=None)
        cv, *_ = np.linalg.lstsq(A, UV[:, 1], rcond=None)
        spans.append(tiles.TILE * (abs(cu[0]) + abs(cv[1])) / 2)
    span, seam = tiles.uv_span(float(np.median(spans)), paint.tile_sizes(material))
    covered = np.concatenate([r['rgba'][np.isfinite(r['z'])] for r in rasters])
    border = np.median(covered, axis=0)
    cands = [dict(w, raster=r) for r in rasters for w in tiles.windows(r)]
    if not cands:
        return []
    feature = lambda w: paint.weights(tiles.window_paint(w['raster'], w), layers).mean(0)  # noqa: E731
    flats = [w for w in cands if w['slope'] <= tiles.FLAT_SOURCE_SLOPE_DEG] or cands
    Ff = np.array([feature(w) for w in flats])
    base = int(np.argmin(np.linalg.norm(Ff - np.median(Ff, axis=0), axis=1)))
    steep = int(np.argmax([w['slope'] for w in cands]))
    common = {'material': material, 'layers': layers, 'uv_span': span, 'uv_seam': seam, 'border': border}
    out = [dict(common, kind='flat', window=w) for w in tiles.choose(flats, tiles.FLAT_TILES, feature, base)]
    out += [dict(common, kind='ramp', window=w) for w in tiles.choose(cands, tiles.RAMP_TILES, feature, steep)]
    quiet = min(flats, key=lambda w: w['rms'])
    for name, rgba in full_fills(layers):
        out.append(dict(common, kind='full', window=quiet, fill=np.array(rgba, np.float64), name=name))
    return out


def _landscape_set(material, group):
    """Plan items of one landscape art set, spread over the route area (no paint to choose by):
    with a decoded look, LANDSCAPE_FLAT_TILES flats and RAMP_TILES ramps in that look; with a
    carried instance (mics.CARRIED), as many again drawn with it, unpainted (its base layer), and
    a full tile per layer."""
    from riftlord.terrainkit import landscape
    look = paint.landscape_look(material)
    mic = mics.carried(material)
    if look is None and mic is None:
        return []
    rasters = []
    for s in group:
        r = landscape.raster(s, tiles.step())
        r['source'] = s
        rasters.append(r)
    cands = [dict(w, raster=r) for r in rasters for w in tiles.windows(r)]
    if not cands:
        return []
    feature = lambda w: np.array([w['i'], w['j']], np.float64)  # noqa: E731
    flats = [w for w in cands if w['slope'] <= tiles.FLAT_SOURCE_SLOPE_DEG] or cands
    quiet = int(np.argmin([w['rms'] for w in flats]))
    steep = int(np.argmax([w['slope'] for w in cands]))
    picked = [('flat', w) for w in tiles.choose(flats, LANDSCAPE_FLAT_TILES, feature, quiet)]
    picked += [('ramp', w) for w in tiles.choose(cands, tiles.RAMP_TILES, feature, steep)]
    out = []
    if look is not None:
        quad = float(np.median([r['quad'] for r in rasters]))
        span, seam = tiles.uv_span(tiles.TILE / quad, look['tile_sizes'])
        common = {'material': material, 'look': look, 'uv_span': span,
                  'uv_seam': 1.0 if look['rotated'] else seam, 'border': None}
        out += [dict(common, kind=kind, window=w) for kind, w in picked]
    if mic is not None:
        layers = mics.layers(mic)
        span, seam = tiles.uv_span(tiles.TILE / tiles.CELL, mics.tile_sizes(mic))
        bare = np.zeros(4, np.float64)
        common = {'material': material, 'mic': mic, 'layers': layers, 'uv_span': span, 'uv_seam': seam,
                  'border': bare}
        out += [dict(common, kind=kind, window=w, fill=bare) for kind, w in picked]
        out += [dict(common, kind='full', window=flats[quiet], fill=np.array(rgba, np.float64), name=name)
                for name, rgba in full_fills(layers)]
    return out


def plan(level_sources):
    """[{kind, material, window, ...}] for a map: mesh-paint sets first, then landscapes."""
    out, fulls = [], set()
    groups = {}
    for s in level_sources:
        groups.setdefault((s.get('kind', 'mesh'), s['material']), []).append(s)
    for (kind, material), group in sorted(groups.items()):
        got = _mesh_set(material, group) if kind == 'mesh' else _landscape_set(material, group)
        for it in got:
            if it['kind'] == 'full':
                layer = next(l for l in reversed(it['layers']) if l['name'] == it['name'])
                look = (layer['texture'], tuple(round(c, 3) for c in layer['tint']))
                if look in fulls:
                    continue
                fulls.add(look)
            out.append(it)
    return out


def pieces(level_sources):
    """write_pieces pieces for a map, named plainly ("Grass Ground 4x4", "Full Dirt Ground 4x4",
    "Sand Ramp 4x4")."""
    items = plan(level_sources)
    thumbs = {}
    out, seen = [], {}
    n = tiles.SAMPLES + 1
    for it in items:
        w, r = it['window'], it['window']['raster']
        z = r['z'][w['j']:w['j'] + n, w['i']:w['i'] + n]
        uvk = it['uv_span'] / tiles.TILE
        shape = 'ramp' if it['kind'] == 'ramp' else 'flat'
        if 'look' in it:
            rgba, border, layer = None, None, it['look']['name']
        elif it['kind'] == 'full':
            rgba = np.tile(it['fill'], (n * n, 1))
            border, layer = it['fill'], 'Full %s' % it['name']
        elif 'mic' in it:
            rgba = np.tile(it['fill'], (n * n, 1))
            border, layer = it['fill'], it['layers'][0]['name']
        else:
            rgba, border = tiles.window_paint(r, w), it['border']
            layer = paint.dominant(rgba, it['layers'])
        desc, kept = tiles.fitted_tile(shape, z, rgba, w['plane'], border, uvk)
        base = '%s %s 4x4' % (layer, WORDS[shape])
        seen[base] = seen.get(base, -1) + 1
        display = base if not seen[base] else '%s %s' % (base, _letters(seen[base]))
        src = r['source']
        wx = float(r['origin'][0] + w['i'] * tiles.step())
        wy = float(r['origin'][1] + w['j'] * tiles.step())
        key = '%s/%s/%d/%s/%d_%d' % (src['map'], src['mesh'], src['component'], it['kind'],
                                      int(round(wx)), int(round(wy)))
        if 'mic' in it:
            key += '/' + it['mic']
        if it['kind'] == 'full':
            key += '/' + it['name'].replace(' ', '_')
        if 'look' in it:
            look = it['look']
            if it['material'] not in thumbs:
                img = paint.texture_image(look['texture'], THUMB_TEXTURE) if look['texture'] else None
                thumbs[it['material']] = None if img is None else paint.thumb_layers(
                    [{'texture': look['texture'], 'tint': look['tint']}], THUMB_TEXTURE)[0]
            thumb = {0: thumbs[it['material']]}
            reason = 'shipped landscape material, its fixed blend off a landscape'
        else:
            tkey = it.get('mic') or it['material']
            if tkey not in thumbs:
                thumbs[tkey] = paint.thumb_layers(it['layers'], THUMB_TEXTURE)
            thumb = {0: {'layers': thumbs[tkey], 'weights': paint.weights(desc['colors'], it['layers'])}}
            reason = ('carried terrain instance %s; ground paint in vertex colours' % it['mic'] if 'mic' in it
                      else 'shipped terrain material; ground paint in vertex colours')
        material = ({'mic': it['mic'], 'slot': 'Ground', 'reasons': [reason]} if 'mic' in it
                    else {'path': it['material'], 'slot': 'Ground', 'reasons': [reason]})
        out.append({
            'name': display.replace(' ', '_'), 'display_name': display, 'source': key,
            'role': ROLES[shape], 'desc': desc,
            'materials': [material],
            'collision': None, 'thumb': thumb,
            'geometry': {'terrain': {'kind': it['kind'], 'map': src['map'], 'mesh': src['mesh'],
                                     'source': src.get('kind', 'mesh'), 'carried': it.get('mic'),
                                     'window_world': [round(wx, 1), round(wy, 1)],
                                     'source_slope_deg': round(w['slope'], 2),
                                     'uv_per_tile': round(it['uv_span'], 4),
                                     'uv_seam': round(it['uv_seam'], 4),
                                     'relief_removed': kept <= 0, 'relief_depth': kept}},
        })
    return out


def close_paks():
    """Close the game paks this process holds open, so forked writer workers open their own
    (a shared file offset across processes reads the wrong bytes)."""
    from riftlord.core import gamefiles
    with gamefiles._OPENING:
        for pak in list(gamefiles._open.values()):
            try:
                pak.f.close()
            except Exception:                        # noqa: BLE001
                pass
        gamefiles._open.clear()


def library_root():
    """Where terrain kits go: the terrain kits' folder, never the Fab library."""
    from riftlord.mapeditor import fablibrary
    return fablibrary.terrain_root()


def build(level, out_dir=None, jobs=None, events=None, progress=None, found=None):
    """Write a map's terrain library; returns write_pieces' report (with `pack_id`), or a report
    with `error` when the map has no ground."""
    from riftlord.fab import library
    found = sources.discover() if found is None else found
    level_sources = found.get(level) or []
    if not level_sources:
        return {'ok': False, 'error': '%s has no painted ground meshes' % level, 'pack_id': pack_id(level)}
    if events:
        events('scan', 0, 2, 'cutting tiles')
    made = pieces(level_sources)
    fp = fingerprint(level_sources)
    if events:
        events('scan', 2, 2, '%d tiles' % len(made))
    close_paks()
    kw = {'kind': KIND, 'fingerprint': fp, 'jobs': jobs, 'events': events, 'mics': carried_specs(level_sources)}
    if progress is not None:
        kw['progress'] = progress
    report = library.write_pieces(pack_id(level), title(level), made, out_dir or library_root(), **kw)
    report['pack_id'] = pack_id(level)
    return report


def status(level, level_sources, out_dir=None):
    """{status none|current|stale|failed, build, fingerprint} of a map's library."""
    from riftlord.fab import library
    lib = os.path.join(out_dir or library_root(), pack_id(level))
    cat_path = os.path.join(lib, 'catalogue.json')
    if not os.path.exists(cat_path):
        return {'status': 'none'}
    try:
        with open(cat_path) as fh:
            cat = json.load(fh)
        with open(os.path.join(lib, 'report.json')) as fh:
            rep = json.load(fh)
    except (OSError, ValueError):
        return {'status': 'failed'}
    state = 'current' if level_sources and cat.get('build') == build_id(level_sources) else 'stale'
    if rep.get('problems'):
        state = 'failed' if state == 'current' else state
    return {'status': state, 'build': cat.get('build'), 'pieces': len(cat.get('meshes') or []),
            'problems': len(rep.get('problems') or [])}
