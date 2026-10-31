"""Compound kits (riftlord/mapeditor/compounds.py): every compound's pieces are New Object
pieces of the kind it names, its New Object card previews the whole assembly, the Hanging
Chain preview hangs its chain and chandelier; every compound, hand-made or mined, has verified
provenance: its `source` names a real shipped occurrence (level, sublevel and actor of each
piece, the anchor's world transform) and its default layout equals that occurrence, piece for
piece, within the miner's tolerances (compoundmine.within) on the level scan (the
compoundmine scan, cached); a parametric compound's ranges are those its params take on the
shipped levels (`seen`); dressed walls (compoundwalls) keep every piece touching the wall or
its dressing, and every param value keeps the dressing on the wall face; every mined compound is filed, draws and renders its preview; every
run builds and renders at its default and both range ends and keeps its params in range; the
Blueprint beam walls carry plain New Object names.

    python3 tests/offline/test_compounds.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np  # noqa: E402

from riftlord.mapeditor import compounds, leveldata  # noqa: E402

#: The fewest mined compounds, and the fewest runs (compoundfamilies.runs).
MINED_MIN = 200
RUNS_MIN = 10
#: The size run range ends are drawn at, px.
RANGE_PREVIEW = 48
#: How far a source's anchor may sit from its recorded world transform (rounding), uu.
ANCHOR_TOLERANCE = 0.01
#: The role groups and art sets New Object files compounds under.
GROUPS = {'Arches and Gates', 'Beams and Scaffolding', 'Columns and Pillars', 'Crenellations',
          'Environmental Traps', 'Lights and Braziers', 'Metalwork', 'Props', 'Roofs',
          'Rocks and Crystals', 'Stairs and Railings', 'Towers and Turrets', 'Walls and Platforms',
          'Windows'}
SETS = {'Castle', 'Sand', 'Ice'}
#: The dressed-wall family (compoundwalls) and the fewest dressed walls.
DRESSED = 'dressed wall'
DRESSED_MIN = 25
checks = []


def check(name, ok, detail=''):
    checks.append(ok)
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, (': %s' % (detail,)) if detail else ''))


def catalogue_checks():
    kinds = {r['name']: r['kind'] for r in leveldata.catalogue()}
    bad = []
    for key in compounds.COMPOUNDS:
        for name, kind, _m in compounds.layout(key):
            want = 'kit' if kind == 'door' else kind
            if kinds.get(name) != want and not (kind == 'kit' and name == compounds.KILL_VOLUME_CLASS):
                bad.append('%s: %s is %r, not %r' % (key, name, kinds.get(name), want))
    check('every compound piece is a New Object piece of its kind (%d compounds)'
          % len(compounds.COMPOUNDS), not bad, bad[:4])
    rows = compounds.catalogue_rows([])
    check('every compound card previews its own assembly',
          all(r['thumb'] == compounds.THUMB_PREFIX + r['name'] for r in rows))


def preview_checks():
    chain = [n for n, _k, _m in compounds.layout('chain')]
    check('the Hanging Chain preview has its post, ring, chain and chandelier',
          chain[0] == 'Metal_PostBase' and 'Chain_Ring_End' in chain
          and any(n.startswith('Chain_Short') for n in chain)
          and compounds.HUNG[compounds.CHAIN_ITEM]['name'] in chain, chain)
    empty = [k for k in compounds.COMPOUNDS if compounds.model(k) is None]
    check('every compound preview draws', not empty, empty)
    png = compounds.thumbnail('chain')
    check('the Hanging Chain preview renders as a PNG', bool(png) and png[:4] == b'\x89PNG')


def _m4(mm):
    M = np.eye(4)
    M[:3, :] = np.array(mm, float).reshape(3, 4)
    return M


def provenance_checks():
    """The gate: every compound's default equals its recorded shipped source."""
    from riftlord.mapeditor import compoundmine as cm
    from riftlord.mapeditor import kitactors
    scanned = cm.scan()
    by = {lv: {(u[2], u[4]): u for u in units} for lv, units in scanned.items()}
    missing, bad, worst = [], [], [0.0, 0.0]
    for key, c in compounds.COMPOUNDS.items():
        src = c.get('source')
        lay = compounds.layout(key)
        if not src or src.get('level') not in by or len(src.get('actors') or []) != len(lay):
            missing.append(key)
            continue
        units = by[src['level']]
        A = _m4(src['anchor'])
        head = units.get(tuple(src['actors'][0] or ()))
        if head is None or head[0] != cm.scan_key(lay[0][0], lay[0][1])[0] or \
                np.abs(_m4(head[3]) - A).max() > ANCHOR_TOLERANCE:
            bad.append('%s: anchor %s' % (key, src['actors'][0]))
            continue
        F = A @ np.linalg.inv(np.array(lay[0][2], float))
        scale = cm.anchor_scale(A)
        used = set()
        for (name, kind, m), ref in zip(lay[1:], src['actors'][1:]):
            W = F @ np.array(m, float)
            if ref is None:
                if not kitactors.CLASSES.get(name, {}).get('carried'):
                    bad.append('%s: %s has no source and is no carried kit' % (key, name))
                continue
            ref = tuple(ref)
            if ref in used:
                bad.append('%s: %s named twice' % (key, ref))
                continue
            used.add(ref)
            if name in cm.VOLUME_CLASSES:
                if cm.volume_at(src['level'], name, W) != ref[1]:
                    bad.append('%s: %s is not where %s puts it' % (key, ref[1], name))
                continue
            u = units.get(ref)
            if u is None or u[0] != cm.scan_key(name, kind)[0]:
                bad.append('%s: %s is not a %s' % (key, ref, name))
                continue
            M = _m4(u[3])
            if kind == 'fx':
                M[:3, :3] = W[:3, :3]
            pe, re, ok = cm.within(W, M, scale)
            worst = [max(worst[0], pe / scale), max(worst[1], re)]
            if not ok:
                bad.append('%s: %s off by %.2f uu / %.4f' % (key, name, pe, re))
    check('every compound records a shipped source (%d compounds)' % len(compounds.COMPOUNDS),
          not missing, missing[:8])
    check('every compound\'s default equals its source piece for piece within %.0f uu and %.2f '
          '(worst %.2f uu, %.4f)' % (cm.POS_TOL, cm.ROT_TOL, worst[0], worst[1]), not bad, bad[:8])
    levels = {c['source']['level'] for c in compounds.COMPOUNDS.values() if c.get('source')}
    check('the sources span the shipped levels (%d levels)' % len(levels), len(levels) >= 20)


def seen_checks():
    """Params vary only as the shipped levels vary them."""
    bad = []
    for key, c in compounds.COMPOUNDS.items():
        seen = c.get('seen') or {}
        if c['type'] == 'stack':
            mids = sorted(int(v) for v in seen.get('mids', {}))
            if not mids or c['midsMax'] != mids[-1] or c['params']['mids'] not in mids:
                bad.append('%s: mids max %s, seen %s' % (key, c.get('midsMax'), mids))
        elif c['type'] == 'chain':
            links = sorted(int(v) for v in seen.get('links', {}))
            if not links or compounds.CHAIN_LINKS_MAX != links[-1] or c['params']['links'] not in links:
                bad.append('%s: links max %d, seen %s' % (key, compounds.CHAIN_LINKS_MAX, links))
        elif c['type'] == 'pattern':
            for name, spec in c['paramSpec'].items():
                got = sorted(int(v) for v in seen.get(name, {}))
                if not got or (spec['min'], spec['max']) != (got[0], got[-1]) or \
                        c['params'][name] not in got:
                    bad.append('%s: %s %s..%s, seen %s' % (key, name, spec['min'], spec['max'], got))
    check('every parametric compound\'s range is what its params take on the shipped levels',
          not bad, bad[:6])
    families = [k for k, c in compounds.COMPOUNDS.items()
                if c['type'] == 'pattern' and (c.get('family') not in ('run', DRESSED) or c.get('walls'))]
    check('no synthesised pattern compounds (wall families, wall choice)', not families, families[:6])


def mined_checks():
    import time
    mined = compounds.MINED
    check('mined compounds load (%d) beside the %d hand-made ones' % (
        len(mined), len(compounds.HAND)), len(mined) >= MINED_MIN)
    rows = {r['name']: r for r in compounds.catalogue_rows([])}
    bad = [k for k in compounds.COMPOUNDS if rows[k]['group'] not in GROUPS or
           not rows[k]['set'] or any(v not in SETS for v in np.atleast_1d(rows[k]['set']))]
    check('every compound is filed under a role group and an art set', not bad, bad[:6])
    labels = [c['label'] for c in compounds.COMPOUNDS.values()]
    check('compound labels are unique', len(labels) == len(set(labels)))
    empty = [k for k in mined if compounds.model(k) is None]
    check('every mined compound draws', not empty, empty[:6])
    t = time.time()
    unrendered = [k for k in mined if (compounds.thumbnail(k) or b'')[:4] != b'\x89PNG']
    check('every mined compound preview renders (%.1fs)' % (time.time() - t), not unrendered,
          unrendered[:6])


def _ends(c):
    """Params at the default, every range's low end and every range's high end."""
    spec = c.get('paramSpec') or {}
    return [('default', dict(c['params'])),
            ('low', {k: v['min'] for k, v in spec.items()}),
            ('high', {k: v['max'] for k, v in spec.items()})]


def run_checks():
    from riftlord.mapeditor import omd3thumb
    keys = [k for k, c in compounds.COMPOUNDS.items() if c['type'] == 'pattern']
    check('runs load (%d)' % len(keys), len(keys) >= RUNS_MIN)
    bad = []
    for key in keys:
        c = compounds.COMPOUNDS[key]
        for tag, params in _ends(c):
            lay = compounds.layout(key, params)
            got = compounds.model(key, params)
            img = omd3thumb.render(got, RANGE_PREVIEW) if got else None
            if not lay or img is None:
                bad.append('%s %s' % (key, tag))
    check('every run builds and renders at its default and both range ends (%d)' % len(keys),
          not bad, bad[:6])
    key = keys[0]
    spec = compounds.COMPOUNDS[key]['paramSpec']['count']
    kept = compounds.clean_member({'group': 1, 'compound': key, 'params': {'count': 999, 'x': 1}})
    check('a run keeps its params within their ranges',
          kept.get('params') == {'count': spec['max']}, kept)
    kept = compounds.clean_member({'group': 1, 'compound': 'column_a', 'params': {'mids': 99}})
    check('a column stack keeps its mids within its shipped range',
          kept.get('params') == {'mids': compounds.COMPOUNDS['column_a']['midsMax']}, kept)


def beam_wall_checks():
    rows = [r for r in leveldata.catalogue() if r['kind'] == 'kit' and
            (leveldata.BEAM_WALL.match(r['name']) or leveldata.BEAM_COLLAR.match(r['name']))]
    unnamed = [r['name'] for r in rows if not r.get('label')]
    labels = [r['label'] for r in rows if r.get('label')]
    check('every Blueprint beam wall has a plain New Object name (%d)' % len(rows),
          len(rows) >= 20 and not unnamed and len(labels) == len(set(labels)), unnamed[:6])


def dressed_wall_checks():
    """Dressed walls (compoundwalls): each look is shown on NEED+ shipped walls; no piece floats
    at the default (the shipped pieces as placed); a beam length keeps the beam on the wall face,
    neither floating nor buried, at every value its range takes (each value seen and both ends)."""
    from riftlord.mapeditor import compoundmine as cm
    from riftlord.mapeditor import compoundwalls as cw
    cat = cm.Catalogue()
    keys = [k for k, c in compounds.COMPOUNDS.items() if c.get('family') == DRESSED]
    check('dressed walls load (%d)' % len(keys), len(keys) >= DRESSED_MIN)
    few = [k for k in keys if compounds.COMPOUNDS[k]['shown']['walls'] < cw.NEED]
    check('every dressed wall\'s look is shown on %d+ shipped walls' % cw.NEED, not few, few[:6])
    bad = []
    for key in keys:
        c = compounds.COMPOUNDS[key]
        bad += ['%s default: %s' % (key, b) for b in cw.on_face(compounds.layout(key), cat)]
        spec = c.get('paramSpec') or {}
        for name, s in spec.items():
            for v in sorted({s['min'], s['max']} | {int(x) for x in c['seen'][name]}):
                bad += ['%s %s=%d: %s' % (key, name, v, b)
                        for b in cw.on_face(compounds.layout(key, {name: v}), cat, within=True)]
    check('dressed walls: no piece floats; every param value keeps the dressing on the wall face',
          not bad, bad[:6])


def main():
    catalogue_checks()
    preview_checks()
    provenance_checks()
    seen_checks()
    mined_checks()
    run_checks()
    beam_wall_checks()
    dressed_wall_checks()
    print('%d checks, %d failed' % (len(checks), checks.count(False)))
    return 0 if all(checks) else 1


if __name__ == '__main__':
    raise SystemExit(main())
