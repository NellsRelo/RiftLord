"""Propose OMD3 meshes for each OMD2 mesh, for a HUMAN to judge by eye.

    python3 meshmatch.py --scene TheCrunch --out compare.html

The converter has always matched OMD2 art to OMD3 art by SHAPE, which cannot tell
stone from wood -- a tall thin boulder classifies as a wall and comes out as a wall
panel. `omd2vmesh.materials` now reads what an OMD2 mesh is actually made of (its
material name and diffuse texture), so a proposal can consider the material too.

**It is still only a proposal.** No measurement distinguishes "castle stone" from
"dungeon stone"; that is a judgement about how a level should look, and it belongs to
whoever is converting the level. So this renders the OMD2 mesh beside its candidates --
same projection, same shading, each with its own texture swatch -- and asks.
"""

import argparse
import base64
import collections
import io
import json
import os
import re
import sys
import zipfile
from riftlord.core import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.formats.omd3 import meshcat  # noqa: E402
from riftlord.mapeditor import omd3serve  # noqa: E402
from riftlord.mapeditor import omd3thumb  # noqa: E402
from riftlord.formats.omd2 import omd2vmesh  # noqa: E402

#: Words in a texture's name that say what a surface is made of. Crude on purpose: the
#: point is to offer the eye a RANGE of materials per slot, not to decide.
MATERIAL_WORDS = ('stone', 'brick', 'wood', 'plank', 'metal', 'iron', 'grate',
                  'tile', 'rock', 'dirt', 'sand', 'ice', 'snow', 'marble', 'cast',
                  'trim', 'rubble', 'cobble', 'thatch', 'crystal')


def words_of(text):
    low = (text or '').lower()
    return {w for w in MATERIAL_WORDS if w in low}


def omd2_inventory(scene, zip_path=None, limit=None):
    """[(material, diffuse, mesh path, instances)] for one OMD2 level, commonest first."""
    from riftlord.formats.omd2 import trapgrids
    from riftlord.formats.omd2 import omd2mesh
    zip_path = zip_path or trapgrids.DEFAULT_DATA_ZIP
    insts = omd2mesh.scene_meshes(scene, zip_path)
    by_mesh = collections.Counter(i.get('mesh') or '' for i in insts)
    rows = []
    with zipfile.ZipFile(zip_path) as zf:
        low = {n.lower().replace('\\', '/'): n for n in zf.namelist()}
        for path, n in by_mesh.most_common():
            key = path.lower().replace('\\', '/')
            hit = next((v for k, v in low.items() if k.endswith(key)), None)
            mats = omd2vmesh.materials(zf.read(hit)) if hit else []
            m = mats[0] if mats else {}
            rows.append({'mesh': path, 'instances': n, 'zip': hit,
                         'material': m.get('name'), 'diffuse': m.get('diffuse')})
    return rows[:limit] if limit else rows


#: What a mesh IS, by the words in its path or name. OMD2 states this plainly in its
#: folders -- `art/Act2/floors/`, `art/Act2/Wall/`, `art/Act2/Ceilings/` -- which is a
#: far better signal than a bounding box: a tall thin boulder has a wall's proportions
#: and is not a wall.
ROLE_WORDS = (('stair', ('stair', 'steps')),
              #: hazards first: OMD2 keeps them in folders like `PitAcid`, and left to
              #: the later words a pit of acid reads as a FLOOR and is offered roof
              #: tiles. In OMD3 these are gameplay actors anyway, so naming the role
              #: is what lets the pipeline route them away from a mesh swap.
              ('hazard', ('acid', 'lava', 'tar', 'pitfall', 'spike')),
              ('light', ('candle', 'torch', 'brazier', 'lantern', 'lighting',
                         'chandelier', 'sconce')),
              ('banner', ('banner', 'flag', 'tapestry')),
              ('window', ('window', 'stainedglass')),
              ('statue', ('statue', 'gargoyle')),
              ('door', ('door', 'portcullis', 'gate')),
              ('floor', ('floor', 'ground')),
              ('ceiling', ('ceiling', 'roof')),
              ('arch', ('arch', 'archway')),
              ('column', ('column', 'pillar', 'cornerstone', 'buttress')),
              ('railing', ('railing', 'rail', 'fence')),
              ('wall', ('wall', 'crenel')))


def role_of(text):
    """The role a path or name suggests, or None.

    Order matters and is load-bearing: `stair` before `floor` so `RailingStairsB` is a
    stair; the hazard, light and banner words before the structural ones so `PitAcid` is
    not a floor and `WallCandle01` is not a wall; `wall` last so `wallarch_blocks` reads
    as an arch. Measured on the second review pass -- without the hazard word, OMD2's
    acid pit was offered `Roof_Mediterranean_2Side_2x2_SM` as its best match.
    """
    low = (text or '').lower()
    for role, words in ROLE_WORDS:
        if any(w in low for w in words):
            return role
    return None


def proportions(extent):
    """A shape's two aspect ratios, largest axis first -- scale-free, so a 400uu
    OMD2 tile and a 300uu OMD3 one compare as the same shape."""
    e = sorted((abs(v) or 1e-6 for v in extent), reverse=True)
    return (e[1] / e[0], e[2] / e[0])


#: Which catalogue categories a castle level may draw from. `effect`, `foliage` and
#: `rift` are not building material, and leaving them in is how a flat OMD2 floor got
#: offered `Scen_15_WaterSurface_02` and `SM_Butterfly01` as its best matches -- both
#: flat, both nothing to do with a floor.
BUILD_CATEGORIES = ('castle', 'decoration', 'terrain', 'trap')

#: Roles whose THICKNESS is buried and must not be scored. A floor slab's depth is under
#: the floor and a ceiling's is above it; only the footprint shows. Scoring it anyway
#: ranked `Floor_1x45` (40uu thick, ratio 0.27) above `Floor_Squarebrick_2x2` (12uu,
#: ratio 0.04) for every one of OMD2's seven decorative floor tiles, because OMD2 cuts
#: its slabs 100uu thick -- a difference the player never sees.
FLAT_ROLES = frozenset(('floor', 'ceiling'))


def candidates(omd2_extent, diffuse, cat=None, want=8,
               categories=BUILD_CATEGORIES, role=None):
    """OMD3 meshes worth showing for one OMD2 mesh, nearest in SIZE and shape.

    **Absolute size matters, not just proportion.** OMD2's trap cell is 300 units and
    OMD3's is 300, so the two games' art is directly comparable and a 400uu floor tile
    should be offered 300-600uu pieces rather than a 50uu pebble of the same aspect
    ratio. Ranking on normalised proportions alone did exactly that.

    Material then SPREADS the offer: once a texture word matches, only two of that word
    are shown, so a slot does not fill with eight shades of the same stone and hide the
    wooden option entirely.
    """
    cat = cat or meshcat.load()
    want_words = words_of(diffuse)
    want_role = role or ''
    a1, a2 = proportions(omd2_extent)
    scored = []
    for name, info in cat.items():
        if categories and info.get('category') not in categories:
            continue
        e = info.get('extent') or [0, 0, 0]
        if not any(e):
            continue
        b1, b2 = proportions(e)
        shape = (abs(a1 - b1) if want_role in FLAT_ROLES
                 else abs(a1 - b1) + abs(a2 - b2))
        got = words_of(name) | words_of(info.get('path') or '')
        theirs = role_of(name) or role_of(info.get('path') or '')
        #: ROLE FIRST, and size NOT AT ALL.
        #:
        #: OMD2 tiles its art at 400uu while an OMD3 `Floor_4x4` is 1200 -- three OMD2
        #: tiles to one OMD3 piece -- so ranking by absolute size actively penalises
        #: the right answer, and that is how a floor came to be offered a railing.
        #: Scale is written per instance now, so size is free; what must match is what
        #: the thing IS. A floor mesh wants a floor, and OMD2 says which in its own
        #: folder name.
        role_err = 0.0 if (want_role and theirs == want_role) else (
            1.2 if want_role else 0.0)
        bonus = 0.0 if not want_words else (-0.6 if (got & want_words) else 0.0)
        scored.append((role_err + shape + bonus, name, got))
    scored.sort()
    out, seen = [], collections.Counter()
    for _s, name, got in scored:
        key = tuple(sorted(got)) or ('?',)
        if seen[key] >= 2:
            continue
        seen[key] += 1
        out.append(name)
        if len(out) >= want:
            break
    return out


def _png(img):
    if img is None:
        return None
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


def _swatch(raw, size=96):
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
        return im.convert('RGB').resize((size, size), Image.LANCZOS)
    except Exception:                                # noqa: BLE001
        return None


def omd2_render(row, zip_path, models, size=110):
    """(shape thumbnail, texture swatch) for one OMD2 mesh, as data URIs."""
    key = (row['mesh'] or '').lower().replace('\\', '/')
    model = None
    for k, v in models.items():
        if k.lower().replace('\\', '/').endswith(key):
            model = v
            break
    shape = _png(omd3thumb.render(model, size=size)) if model else None
    tex = None
    if row.get('diffuse'):
        with zipfile.ZipFile(zip_path) as zf:
            want = row['diffuse'].lower()
            hit = next((n for n in zf.namelist()
                        if n.lower().endswith(want + '.dds')), None)
            if hit:
                tex = _png(_swatch(zf.read(hit), size))
    return shape, tex, (model or {}).get('verts')


def omd3_render(name, size=110):
    """(shape thumbnail, texture swatch, mesh record) for one OMD3 mesh."""
    try:
        m = omd3serve.mesh(name)
    except Exception:                                # noqa: BLE001
        m = None
    if not m:
        return None, None, None
    shape = _png(omd3thumb.render(m, size=size))
    tex = None
    if m.get('texture'):
        try:
            tex = _png(_swatch(omd3serve.texture_png(m['texture'], size=size), size))
        except Exception:                            # noqa: BLE001
            tex = None
    return shape, tex, m


def decided(path=None):
    """{normalised omd2 mesh path: its map entry} for every mesh already reviewed."""
    path = path or gamepath.data('mesh-map.json')
    try:
        with open(path) as fh:
            raw = json.load(fh)['map']
    except (OSError, KeyError, ValueError):
        return {}
    return {k.replace('\\', '/').lower(): v for k, v in raw.items()}


#: A word that turns one OMD2 mesh into a different KIND of thing, so a sibling's
#: family must not be inherited across it. Measured from the reviewer's own two
#: corrections: `Wall1_2Angled` inherited `Wall_FB` from `Wall1_2` where the right
#: answer is `WallAngled_1_2` exactly, and `WallCandle01` inherited a wall family while
#: being "a decoration, not a wall itself". Each word also names an OMD3 family, which
#: is what makes the better answer findable rather than merely blocking the worse one.
MODIFIER_WORDS = ('angled', 'candle', 'corner', 'broken', 'half', 'design',
                  'inner', 'outer', 'top', 'base', 'keystone', 'endcap', 'cap',
                  'flat', 'thickness', 'trim', 'window')


def proposal(mesh, done, cat=None):
    """A family to suggest for `mesh`, taken from an already-reviewed SIBLING.

    Two thirds of what is left after a first review pass are siblings of meshes the
    reviewer has already ruled on -- `Thickness1` and `Thickness3` beside `Thickness2`,
    `CornerStoneIn` beside `CornerStoneOut`, `FloorMainDesign04` beside `FloorMain1`.
    Proposing the sibling's family turns the second pass into confirmation.

    **A stem match is not enough when the names differ by a MODIFIER.** The reviewer
    rejected exactly two proposals and both failed this way, so a word in the new name
    that the sibling lacks either blocks the proposal or, better, names the family to
    propose instead: `Angled` finds `WallAngled`, `Candle` finds the lights. A differing
    ROLE blocks it too, which is what catches `WallCandle01` reading as a light.
    """
    leaf = mesh.replace('\\', '/').rsplit('/', 1)[-1].lower()
    bare = os.path.splitext(leaf)[0]
    stem = re.sub(r'[\d_]+$', '', bare)
    mine = role_of(mesh)
    best = None
    for key, entry in done.items():
        other = os.path.splitext(key.rsplit('/', 1)[-1])[0]
        core = re.sub(r'[\d_]+$', '', other)
        if not core or not stem:
            continue
        n = 0
        while n < min(len(core), len(stem)) and core[n] == stem[n]:
            n += 1
        if n < 4:
            continue
        #: a modifier the sibling does not carry makes them different things
        extra = [w for w in MODIFIER_WORDS if w in bare and w not in other]
        if extra:
            continue
        #: and so does a different role, however well the stems agree
        if mine and role_of(key) and role_of(key) != mine:
            continue
        if best is None or n > best[0]:
            best = (n, entry, other)
    if best:
        return {'family': best[1].get('family'), 'role': best[1].get('role'),
                'from': best[2], 'action': best[1].get('action'),
                'via': 'sibling'}
    #: no usable sibling: if a modifier names an OMD3 family, say so instead
    fam = family_for_modifier(bare, cat)
    if fam:
        return {'family': fam, 'role': mine, 'from': 'the "%s" in its name' % fam,
                'via': 'modifier'}
    return None


def family_for_modifier(bare, cat=None):
    """An OMD3 family whose name carries the same modifier word, or None.

    `Wall1_2Angled` -> `WallAngled`, which is the reviewer's exact answer and was
    sitting in the catalogue the whole time.
    """
    cat = cat or meshcat.load()
    words = [w for w in MODIFIER_WORDS if w in bare]
    if not words:
        return None
    base = re.match(r'[a-z]+', bare)
    base = base.group(0) if base else ''
    best = None
    for name in cat:
        low = name.lower()
        for w in words:
            if w in low and base and low.startswith(base[:4]):
                fam = re.sub(r'[_\d]+$', '', name.split('_1')[0]) if '_1' in name else name
                if best is None or len(fam) < len(best):
                    best = fam
    return best


def build(scene, out, top=12, want=8, size=110, zip_path=None,
          skip_decided=False, only=(), offer=()):
    """Write an HTML sheet comparing each OMD2 mesh with its OMD3 candidates."""
    from riftlord.formats.omd2 import trapgrids
    zip_path = zip_path or trapgrids.DEFAULT_DATA_ZIP
    models = json.load(open(gamepath.data('omd2-models.json')))['models']
    cat = meshcat.load()
    done = decided() if skip_decided else {}
    rows = omd2_inventory(scene, zip_path, limit=None)
    if done:
        #: a mesh the reviewer has already ruled on is not asked about again
        rows = [r for r in rows
                if not any((r['mesh'] or '').replace('\\', '/').lower().endswith(k)
                           for k in done)]
    if only:
        #: a focused pass: one group of meshes, so a reviewer is not handed 34 cards to
        #: answer a question about seven
        want_words = tuple(w.lower() for w in only)
        rows = [r for r in rows
                if any(w in (r['mesh'] or '').lower() for w in want_words)]
    rows = rows[:top]
    total = sum(r['instances'] for r in rows)
    cards = []
    for r in rows:
        shape, tex, verts = omd2_render(r, zip_path, models, size)
        extent = [1, 1, 1]
        if verts:
            lo = [min(v[i] for v in verts) for i in range(3)]
            hi = [max(v[i] for v in verts) for i in range(3)]
            extent = [(hi[i] - lo[i]) / 2.0 or 1e-6 for i in range(3)]
        opts = []
        #: An explicit offer beats the ranker. The ranker scores SHAPE, and every square
        #: floor tile has the same shape, so it cannot tell `Floor_Herringbone_2x2_SM`
        #: from `FloorTemplate_8x8` -- which is useless for a question that is purely
        #: about pattern. Where the answer turns on appearance alone, name the candidates.
        chosen = list(offer) if offer else candidates(
            extent, r.get('diffuse'), cat, want, role=role_of(r['mesh']))
        for name in chosen:
            s3, t3, m3 = omd3_render(name, size)
            if s3:
                opts.append({'name': name, 'shape': s3, 'tex': t3,
                             'texture': (m3 or {}).get('texture') or ''})
        cards.append({'row': r, 'shape': shape, 'tex': tex, 'options': opts,
                      'extent': [round(v) for v in extent],
                      'proposal': proposal(r['mesh'] or '', done, cat) if done else None})
    html = _html(scene, cards, total, size)
    with open(out, 'w', encoding='utf-8') as fh:
        fh.write(html)
    return out, cards


def _html(scene, cards, total, size):
    """The comparison sheet. Content only -- the publish skeleton supplies the wrapper."""
    import html as _h
    css = """
<title>%s Art Match</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&family=JetBrains+Mono:wght@400;500&display=swap">
<style>
:root{
  --bg:#f2f1ee; --card:#ffffff; --line:#d8d5cd; --ink:#1c1b19; --dim:#6a665e;
  --accent:#8c5a2b; --rule:#e6e3db; --chip:#efece5;
  --ok:#3f6b34;
  --sans:'Inter',ui-sans-serif,system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
  --mono:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    color-scheme:dark;
    --bg:#16161a; --card:#1f1f25; --line:#32323b; --ink:#eceae5; --dim:#9b968c;
    --accent:#e0a35c; --rule:#2a2a31; --chip:#26262d;
  }
}
:root[data-theme="dark"]{
  color-scheme:dark;
  --bg:#16161a; --card:#1f1f25; --line:#32323b; --ink:#eceae5; --dim:#9b968c;
  --accent:#e0a35c; --rule:#2a2a31; --chip:#26262d;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);
     font-size:14px;line-height:1.55;padding-block:28px 56px;padding-left:16px;
     padding-right:16px}
.wrap{max-width:1180px;margin:0 auto}
h1{font-size:clamp(20px,3vw,27px);margin:0 0 6px;letter-spacing:-.01em;
   text-wrap:balance;font-weight:600}
.lede{color:var(--dim);margin:0 0 4px;max-width:65ch}
.count{font-family:var(--mono);font-variant-numeric:tabular-nums;color:var(--accent)}
.rule{height:1px;background:var(--rule);margin:22px 0 4px}
.prop{margin:0 0 8px;font:500 12px/1.5 var(--mono);color:var(--ok);
  background:var(--chip);border:1px solid var(--line);border-radius:6px;padding:6px 9px}
.prop b{font-weight:600}
.prop span{display:block;color:var(--dim);font-weight:400}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;
      margin:14px 0;padding:14px;display:flex;gap:18px;flex-wrap:wrap}
.src{flex:0 0 auto;width:%dpx}
.pair{display:flex;gap:5px}
.pair img{width:%dpx;height:%dpx;border-radius:5px;border:1px solid var(--line);
          display:block;background:var(--chip)}
.blank{width:%dpx;height:%dpx;border-radius:5px;border:1px dashed var(--line)}
.src h2{font-family:var(--mono);font-size:12.5px;font-weight:500;margin:9px 0 3px;
        color:var(--accent);word-break:break-all;line-height:1.35}
.meta{color:var(--dim);font-size:11.5px;word-break:break-all;margin:0;
      font-variant-numeric:tabular-nums}
.opts{flex:1 1 320px;display:flex;gap:11px;flex-wrap:wrap;align-content:flex-start}
.opt{width:%dpx}
.opt .name{font-family:var(--mono);font-size:10.5px;margin-top:5px;
           word-break:break-all;line-height:1.35}
.opt .tx{font-family:var(--mono);font-size:9.5px;color:var(--dim);
         word-break:break-all;line-height:1.3}
@media (max-width:520px){.src{width:100%%}.opts{flex-basis:100%%}}
</style>""" % (_h.escape(scene), size * 2 + 12, size, size, size, size, size)
    parts = [css, '<div class="wrap">',
             '<h1>%s &mdash; OMD2 art, OMD3 candidates</h1>' % _h.escape(scene),
             '<p class="lede">Left of each row: the OMD2 mesh, flat-shaded, with its '
             'diffuse texture beside it. Right: OMD3 meshes ranked by role, then shape, '
             'spread across materials so each row offers a choice rather than eight '
             'shades of one stone. Both sides use the same renderer and the same light, '
             'so only the art differs.</p>',
             '<p class="lede">Scale is written per instance, so size is not a '
             'constraint &mdash; an OMD2 tile is 400 units and an OMD3 floor piece is '
             '1200. Pick on material and character. '
             '<span class="count">%d</span> instances covered.</p>' % total,
             '<div class="rule"></div>']
    for c in cards:
        r = c['row']
        parts.append('<div class="card"><div class="src"><div class="pair">')
        for img in (c['shape'], c['tex']):
            parts.append('<img src="%s" alt="">' % img if img
                         else '<div class="blank"></div>')
        parts.append('</div><h2>%s</h2><p class="meta">%s &middot; %s<br>'
                     '%d instances &middot; half-extent %s</p></div>'
                     % (_h.escape((r['mesh'] or '').rsplit('\\', 1)[-1].rsplit('/', 1)[-1]),
                        _h.escape(str(r.get('material'))),
                        _h.escape(str(r.get('diffuse'))), r['instances'], c['extent']))
        prop = c.get('proposal')
        if prop:
            parts.append('<p class="prop">proposed: <b>%s</b>%s<span>from %s</span></p>'
                         % (_h.escape(str(prop.get('family') or prop.get('action')
                                          or 'unresolved')),
                            (' &middot; %s' % _h.escape(prop['role'])) if prop.get('role') else '',
                            _h.escape(prop['from'])))
        parts.append('<div class="opts">')
        for o in c['options']:
            parts.append('<div class="opt"><div class="pair">')
            parts.append('<img src="%s" alt="">' % o['shape'])
            parts.append('<img src="%s" alt="">' % o['tex'] if o['tex']
                         else '<div class="blank"></div>')
            parts.append('</div><div class="name">%s</div><div class="tx">%s</div></div>'
                         % (_h.escape(o['name']),
                            _h.escape((o['texture'] or '').rsplit('/', 1)[-1])))
        parts.append('</div></div>')
    parts.append('</div>')
    return ''.join(parts)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--scene', default='TheCrunch')
    ap.add_argument('--out', default='compare.html')
    ap.add_argument('--top', type=int, default=12)
    ap.add_argument('--only', help='comma-separated mesh-name substrings')
    ap.add_argument('--offer', help='comma-separated OMD3 mesh names to show instead '
                                    'of the ranker\'s picks')
    ap.add_argument('--new', action='store_true',
                    help='only meshes not already in mesh-map.json')
    ap.add_argument('--want', type=int, default=8)
    args = ap.parse_args(argv)
    out, cards = build(args.scene, args.out, args.top, args.want,
                       skip_decided=args.new,
                       only=tuple(x for x in (args.only or '').split(',') if x),
                       offer=tuple(x for x in (args.offer or '').split(',') if x))
    print('%s -> %s' % (args.scene, out))
    for c in cards:
        print('  %-34s %5d inst  %-22s -> %s'
              % ((c['row']['mesh'] or '').rsplit('/', 1)[-1], c['row']['instances'],
                 str(c['row'].get('diffuse')),
                 ', '.join(o['name'] for o in c['options'][:4])))
    print('  %d bytes' % os.path.getsize(out))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())


#: A trailing NxM (or N_M) in an OMD3 mesh name is its size in GRID CELLS, confirmed
#: against the catalogue: `Floor_Squarebrick_4x4` measures 4.03 x 4.02 cells and
#: `WallTrim_Bottom_1_8` is 8.0 cells long. So a family is the decision and the member
#: follows from the cell size actually needed -- which is how the art was reviewed.
SIZE_SUFFIX = __import__('re').compile(
    r'^(?P<fam>.+?)[_-](?P<a>\d+)[x_](?P<b>\d+)(?P<tail>.*)$', __import__('re').I)


def families(cat=None, keys=(), categories=BUILD_CATEGORIES):
    """{family: [(mesh, (cells a, b))]} for meshes whose name matches any key."""
    cat = cat or meshcat.load()
    out = collections.defaultdict(list)
    for name, info in cat.items():
        if categories and info.get('category') not in categories:
            continue
        low = name.lower()
        if keys and not any(k in low for k in keys):
            continue
        m = SIZE_SUFFIX.match(name)
        fam = (m.group('fam') + m.group('tail')).strip('_') if m else name
        cells = tuple(round(v / 150.0, 1) for v in (info.get('extent') or [0, 0, 0]))
        out[fam].append((name, cells))
    return out


def family_sheet(groups, out, size=104):
    """An HTML sheet of whole families, for choosing at family level."""
    import html as _h
    css = _html('', [], 0, size).split('<div class="wrap">')[0]
    css = css.replace('<title></title>', '<title>OMD3 Mesh Families</title>')
    parts = [css, '<div class="wrap">',
             '<h1>OMD3 mesh families</h1>',
             '<p class="lede">For the rows where no candidate fitted. The numbers in an '
             'OMD3 mesh name are <strong>grid cells</strong>, so a family is the choice '
             'and the member follows from the size needed. Each tile shows the mesh and '
             'its texture.</p><div class="rule"></div>']
    for title, note, fams in groups:
        parts.append('<h1 style="font-size:17px;margin:22px 0 2px">%s</h1>'
                     '<p class="lede">%s</p>' % (_h.escape(title), _h.escape(note)))
        for fam, members in sorted(fams.items(), key=lambda kv: -len(kv[1])):
            parts.append('<div class="card"><div class="src"><h2>%s</h2>'
                         '<p class="meta">%d member(s)</p></div><div class="opts">'
                         % (_h.escape(fam), len(members)))
            for name, cells in sorted(members)[:10]:
                shape, tex, _m = omd3_render(name, size)
                if not shape:
                    continue
                parts.append('<div class="opt"><div class="pair">')
                parts.append('<img src="%s" alt="">' % shape)
                parts.append('<img src="%s" alt="">' % tex if tex
                             else '<div class="blank"></div>')
                parts.append('</div><div class="name">%s</div>'
                             '<div class="tx">%g x %g cells</div></div>'
                             % (_h.escape(name), cells[0], cells[1]))
            parts.append('</div></div>')
    parts.append('</div>')
    with open(out, 'w', encoding='utf-8') as fh:
        fh.write(''.join(parts))
    return out
