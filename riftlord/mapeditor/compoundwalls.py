"""Dressed walls: shipped walls carrying wood or iron (beams, posts, braces, beam ends, planks,
scaffolding, brackets, bands, plates, trim), pooled by look; each look's compound is one real
wall with its dressing as shipped. compoundmine.build adds these definitions (family
'dressed wall') before the curation is matched.

Steps:
1. hosting: each dressing piece (is_dress) touching a wall, tower or column (boxes within HOST)
   goes to the nearest one; a dressing piece touching hosted dressing (LINK) of one host only
   joins it (HOPS rounds). Hosts kept: upright (UPRIGHT) walls named by WALL_NAME.
2. roles, in the wall's frame (its thinner horizontal axis the face normal): by name
   (ROLE_BY_NAME), else by the long axis: post (vertical), top beam (along the face, centre above
   TOP_SHARE of the height), mid beam, beam end (out of the face), brace (diagonal in the face),
   other. Face: top (on the wall's top), end (past its ends), else F or B by side; the wall is
   turned so F carries the most pieces. Plates not flat on the face (ledges) and wood lying at
   the foot (below FLOOR over it) are left out.
3. look: the wall's class (WALL_CLASS) and the set of MAIN roles on F and top. The walls of one
   look are where it is shown (`shown`); a look needs NEED walls.
4. default: of the look's walls, those whose pieces stay within the wall (ALONG past its ends,
   BELOW its foot, ABOVE its top, OFF_FACE off the face except beam ends and scaffolding) with
   the commonest counts; else the one passing least. Where a main piece passes the wall by
   more than TIP (it runs onto the next structure), the default is the whole real assembly:
   supports (dressing touching it, lower, standing more than lying) followed down, and every
   wall, tower or column the dressing runs onto past SLACK; a tip or a fitting past the wall
   stays as shipped on the one wall.
5. frame: the wall's origin, its dressed face looking at -y (the preview camera).
6. beam length: a look whose dressing is one top or mid beam gets `length`, the beam stretched
   about its centre; the range is the lengths that beam role has on the look's clean walls,
   kept to those that fit the wall face (ALONG); `seen` counts them.

Thresholds (measured 2026-10-29 over the 33 shipped levels): 1,424 walls carry dressing on 32
levels; pooled by look, 77 looks are on 2+ walls (NEED). 45 were drawn beside their shipped
spots and eye-checked (riftlord/data/compound-curation.json, family 'dressed wall').
"""
import collections
import re

import numpy as np

FAMILY = 'dressed wall'
HOST = 20.0
LINK = 5.0
HOPS = 2
#: walls that host dressing (names), and the least share of up their z axis keeps
WALL_NAME = re.compile(r'^Wall(?!_Beams|Crystal|_Torch)')
UPRIGHT = 0.98
METAL = re.compile(r'^Metal_(Trim|Base_Plate|ThinRow_Plate|Thin_|1x1_Plate|4WayJoint|3WayJoint)|'
                   r'^SM_MERGED_Metal_Trim|MetalBand')
NOT_DRESS = re.compile(r'Lightbeam|Drawbridge|Archway|Wall_Beams|TowerTop|Floor|Ceiling|Bridge|'
                       r'Stairs|Barrier', re.I)
ROLE_BY_NAME = [(r, re.compile(p)) for r, p in (
    ('band', r'MetalBand'), ('plate', r'Plate'), ('bracket', r'Bracket|WoodJoint|Beam_Support'),
    ('strap', r'^Metal_|^SM_MERGED_Metal'), ('scaffold', r'Scaffold|WoodPlanks_v|Ladder'),
    ('plank', r'WoodPlank'))]
#: a long axis within this (cosine) of an axis lies along it; under BRACE off the face, a brace
AXIS = 0.85
BRACE = 0.3
TOP_SHARE = 0.8
#: a plate is flat on the face when its depth is at most this share of its other sides
FLAT = 0.35
FLOOR = 60.0
MAIN = ('post', 'top beam', 'mid beam', 'brace', 'beam end', 'scaffold', 'plank', 'band', 'plate')
WALL_CLASS = (('Beam wall', re.compile(r'^Wall_RB_WD')), ('Ruin', re.compile(r'Broken')),
              ('Block', re.compile(r'FB|SAND')), ('Stone', re.compile(r'.')))
NEED = 2
ALONG = 30.0
BELOW = 30.0
ABOVE = 250.0
OFF_FACE = 120.0
SLACK = 30.0
#: a main piece passing its wall by more than this runs onto the next structure; at most this
#: is a tip (beam end, brace tip). Measured: the most any piece passes its wall, over the 499
#: dressed walls with a piece past theirs, is densest at 10-50 uu (2-3 walls per uu) and
#: halves past 50 (1.2 per uu at 50-75, under 1 beyond 75).
TIP = 50.0
TIMBER = ('post', 'top beam', 'mid beam', 'brace', 'beam end', 'scaffold', 'plank')
#: looks kept whole by eye though their beams pass by a tip only: the Ice timber frame, its
#: arched beams set into the flanking columns
WHOLE = {('Stone', ('beam end', 'mid beam', 'post', 'top beam'))}
#: roles a length param is given for
LENGTH_ROLES = ('top beam', 'mid beam')


def is_dress(key, kind):
    from riftlord.mapeditor import compoundmine as cm
    if kind == 'fx' or NOT_DRESS.search(key):
        return False
    return cm.is_wood(key, kind) or bool(METAL.search(key))


def _unit(W):
    U = W.copy()
    U[:3, :3] = W[:3, :3] / np.linalg.norm(W[:3, :3], axis=0)
    return U


def _touch(B, b, gap):
    return np.nonzero(np.all((B[:, :3] <= b[3:] + gap) & (B[:, 3:] >= b[:3] - gap), axis=1))[0]


def _piece(cat, key, kind, Q, lo, hi, n, a):
    """{role, face, box, dir, len} of a piece at Q in a wall's unit frame."""
    b = cat.world_box(key, kind, Q)
    c = (b[:3] + b[3:]) / 2
    plo, phi = cat.box(key, kind)
    vecs = Q[:3, :3] * ((phi - plo) / 2)[None, :]
    k = int(np.argmax(np.linalg.norm(vecs, axis=0)))
    d = vecs[:, k] / max(np.linalg.norm(vecs[:, k]), 1e-9)
    role = next((r for r, rx in ROLE_BY_NAME if rx.search(key)), None)
    if role is None:
        if abs(d[2]) > AXIS:
            role = 'post'
        elif abs(d[a]) > AXIS:
            role = 'top beam' if c[2] >= lo[2] + TOP_SHARE * (hi[2] - lo[2]) else 'mid beam'
        elif abs(d[n]) > AXIS:
            role = 'beam end'
        elif abs(d[n]) < BRACE:
            role = 'brace'
        else:
            role = 'other'
    if b[2] >= hi[2] - HOST and c[2] > hi[2]:
        face = 'top'
    elif c[a] < lo[a] - 10 or c[a] > hi[a] + 10:
        face = 'end'
    else:
        face = 'F' if c[n] >= (lo[n] + hi[n]) / 2 else 'B'
    return {'role': role, 'face': face, 'box': b, 'dir': d, 'len': float(np.linalg.norm(vecs[:, k]) * 2)}


def dressed(units, cat):
    """[{level, wall (unit index), n (face normal axis), lo, hi (wall box in its unit frame),
    flip, pieces: [{u, role, face, box, dir, len}]}] of the upright walls carrying dressing."""
    from riftlord.mapeditor import compoundmine as cm
    is_host = cm.wall_test(cat)
    out = []
    for lv in sorted(units):
        us = units[lv]
        dress = [i for i, u in enumerate(us) if is_dress(u[0], u[1])]
        hosts = [i for i, u in enumerate(us) if is_host(u[0], u[1]) and not is_dress(u[0], u[1])]
        if not dress or not hosts:
            continue
        box = {i: cat.world_box(us[i][0], us[i][1], us[i][2]) for i in dress + hosts}
        HL = np.array([box[i] for i in hosts])
        host_of = {}
        for j in dress:
            cj = (box[j][:3] + box[j][3:]) / 2
            best = None
            for m in _touch(HL, box[j], HOST):
                i = hosts[m]
                p = (np.linalg.inv(us[i][2]) @ np.append(cj, 1.0))[:3]
                lo, hi = cat.box(us[i][0], us[i][1])
                dist = float(np.maximum(np.maximum(lo - p, p - hi), 0).max())
                if best is None or (dist, i) < best:
                    best = (dist, i)
            if best is not None:
                host_of[j] = best[1]
        DL = np.array([box[j] for j in dress])
        for _hop in range(HOPS):
            add = {}
            for j in dress:
                if j in host_of:
                    continue
                hs = {host_of[dress[m]] for m in _touch(DL, box[j], LINK) if dress[m] in host_of}
                if len(hs) == 1:
                    add[j] = hs.pop()
            host_of.update(add)
        by_host = collections.defaultdict(list)
        for j, i in host_of.items():
            by_host[i].append(j)
        for i, js in sorted(by_host.items()):
            key, kd = us[i][0], us[i][1]
            W = us[i][2]
            if not WALL_NAME.search(key) or cm.role(key, kd, cat.meshes) != 'wall' or \
                    W[2, 2] / np.linalg.norm(W[:3, 2]) < UPRIGHT:
                continue
            U = _unit(W)
            Uinv = np.linalg.inv(U)
            wb = cat.world_box(key, kd, Uinv @ W)
            lo, hi = wb[:3], wb[3:]
            ext = hi - lo
            n = 0 if ext[0] < ext[1] else 1
            pieces = []
            for j in sorted(js):
                p = _piece(cat, us[j][0], us[j][1], Uinv @ us[j][2], lo, hi, n, 1 - n)
                p['u'] = j
                pieces.append(p)
            flip = sum(p['face'] == 'B' for p in pieces) > sum(p['face'] == 'F' for p in pieces)
            if flip:
                for p in pieces:
                    p['face'] = {'F': 'B', 'B': 'F'}.get(p['face'], p['face'])
            out.append({'level': lv, 'wall': i, 'n': n, 'lo': lo, 'hi': hi, 'flip': flip,
                        'pieces': pieces})
    return out


def kept(w):
    """The pieces of a wall's dressing that count: ledge plates and wood lying at the foot
    left out."""
    n, a = w['n'], 1 - w['n']
    out = []
    for p in w['pieces']:
        e = p['box'][3:] - p['box'][:3]
        if p['role'] == 'plate' and e[n] > FLAT * min(e[a], e[2]):
            continue
        if abs(p['dir'][2]) < 0.5 and p['box'][5] < w['lo'][2] + FLOOR and \
                p['role'] in ('beam end', 'plank', 'mid beam', 'other', 'scaffold'):
            continue
        out.append(p)
    return out


def _past(w, p):
    """[uu] a piece passes its wall by on each side: ends, foot, top (and the face, but for
    beam ends, scaffolding and pieces on top)."""
    lo, hi, n, a = w['lo'], w['hi'], w['n'], 1 - w['n']
    b = p['box']
    out = [lo[a] - ALONG - b[a], b[3 + a] - hi[a] - ALONG, lo[2] - BELOW - b[2],
           b[5] - hi[2] - ABOVE]
    if p['role'] not in ('beam end', 'scaffold') and p['face'] != 'top':
        out += [lo[n] - OFF_FACE - b[n], b[3 + n] - hi[n] - OFF_FACE]
    return [max(0.0, x) for x in out]


def overhang(w, pieces):
    """How far (summed, uu) pieces pass the wall: its ends, foot, top or face."""
    return sum(sum(_past(w, p)) for p in pieces)


def runs_onto(w, pieces):
    """Whether the dressing runs past its wall onto more: a main piece (TIMBER) passing it by
    more than TIP; fittings and tips stay on the one wall."""
    return any(max(_past(w, p)) > TIP for p in pieces if p['role'] in TIMBER)


def wall_class(key):
    return next(c for c, rx in WALL_CLASS if rx.search(key))


def look(key, pieces):
    return (wall_class(key), tuple(sorted({p['role'] for p in pieces
                                           if p['role'] in MAIN and p['face'] in ('F', 'top')})))


def counts(pieces):
    return tuple(sorted(collections.Counter(
        p['role'] for p in pieces if p['role'] in MAIN and p['face'] in ('F', 'top')).items()))


def assembly(w, pieces, units, cat):
    """(walls, dressing) unit indices of the whole real assembly around a wall's dressing."""
    us = units[w['level']]
    boxes = np.array([cat.world_box(u[0], u[1], u[2]) for u in us])
    from riftlord.mapeditor import compoundmine as cm
    isd = np.array([is_dress(u[0], u[1]) for u in us])
    iss = np.array([cm.role(u[0], u[1], cat.meshes) in ('wall', 'castle', 'column') and not isd[k]
                    for k, u in enumerate(us)])
    wb = boxes[w['wall']]
    dress = [p['u'] for p in pieces]
    seen, front = set(dress), list(dress)
    while front:
        j = front.pop()
        cj = (boxes[j][:3] + boxes[j][3:]) / 2
        for k in _touch(boxes, boxes[j], LINK):
            k = int(k)
            if not isd[k] or k in seen:
                continue
            e = boxes[k][3:] - boxes[k][:3]
            if (boxes[k][:3] + boxes[k][3:])[2] / 2 < cj[2] and e[2] > max(e[0], e[1]):
                seen.add(k)
                dress.append(k)
                front.append(k)
    walls = set()
    for j in dress:
        b = boxes[j]
        if np.all(b[:3] >= wb[:3] - SLACK) and np.all(b[3:] <= wb[3:] + SLACK):
            continue
        for k in _touch(boxes, b, HOST):
            k = int(k)
            if not iss[k] or k == w['wall']:
                continue
            lo = np.maximum(boxes[k][:3] - HOST, b[:3])
            hi = np.minimum(boxes[k][3:] + HOST, b[3:])
            if np.any(lo < wb[:3] - SLACK) or np.any(hi > wb[3:] + SLACK):
                walls.add(k)
    return sorted(walls), sorted(dress)


def frame(w, A):
    """The compound frame of a wall placed at A: its origin, the dressed face's normal -y."""
    U = A[:3, :3] / np.linalg.norm(A[:3, :3], axis=0)
    nw = U[:, w['n']] * (-1.0 if w['flip'] else 1.0)
    nw = np.array([nw[0], nw[1], 0.0]) / np.hypot(nw[0], nw[1])
    ey = -nw
    F = np.eye(4)
    F[:3, 0], F[:3, 1], F[:3, 2] = (ey[1], -ey[0], 0.0), ey, (0.0, 0.0, 1.0)
    F[:3, 3] = A[:3, 3]
    return F


def _rows(M):
    return [[round(float(x), 4) + 0.0 for x in r] for r in M]


def _num(x):
    return round(float(x), 2) + 0.0


def _length_param(d, beam, w, lengths):
    """`d` as a pattern compound whose one beam stretches about its centre (`length`), or `d`
    when no shown length but its own fits the wall."""
    plo, phi = beam['box_local']
    k = beam['axis']
    R0, t0 = np.array(beam['r']), np.array(beam['t'])
    L0 = int(round(beam['len']))
    mk = float((plo[k] + phi[k]) / 2)
    centre_a = beam['centre_a']
    room = min(centre_a - (w['lo'][1 - w['n']] - ALONG), (w['hi'][1 - w['n']] + ALONG) - centre_a)
    seen = collections.Counter(int(round(x)) for x in lengths)
    seen = {v: c for v, c in seen.items() if v * abs(beam['dir_a']) / 2 <= room}
    if L0 not in seen or len(seen) < 2:
        return d
    lo_v, hi_v = min(seen), max(seen)
    part = {'name': beam['name'], 'kind': beam['kind'], 'r': _rows(R0),
            't': [['length', _num(-R0[i, k] * mk / L0), _num(t0[i] + R0[i, k] * mk)] for i in range(3)],
            'stretch': {'axis': k, 'by': ['length', 1.0 / L0, 0.0]}}
    parts = [{'name': m['name'], 'kind': m['kind'], 'r': m['r'], 't': m['t']}
             for m in d['members'] if m is not beam['member']] + [part]
    out = {x: v for x, v in d.items() if x != 'members'}
    out.update(type='pattern', parts=parts, params={'length': L0},
               paramSpec={'length': {'label': 'Beam Length', 'min': lo_v, 'max': hi_v, 'step': 1}},
               seen={'length': {str(v): seen[v] for v in sorted(seen)}})
    return out


def definitions(units, cat, art_set, log=lambda _s: None):
    """The dressed-wall definitions (no label yet), one per look on NEED+ walls."""
    from riftlord.mapeditor import compoundmine as cm
    groups = collections.defaultdict(list)
    for w in dressed(units, cat):
        ps = kept(w)
        k = look(units[w['level']][w['wall']][0], ps)
        if k[1]:
            groups[k].append((w, ps))
    out = []
    for k, ws in sorted(groups.items(), key=lambda kv: (-len(kv[1]), str(kv[0]))):
        if len(ws) < NEED:
            continue
        modal = collections.Counter(counts(ps) for _w, ps in ws).most_common(1)[0][0]
        clean = [x for x in ws if overhang(*x) == 0]
        w, ps = min(clean or ws, key=lambda x: (counts(x[1]) != modal, overhang(*x),
                                                 x[0]['level'], x[0]['wall']))
        us = units[w['level']]
        if runs_onto(w, ps) or (k in WHOLE and overhang(w, ps)):
            extra, dress = assembly(w, ps, units, cat)
        else:
            extra, dress = [], [p['u'] for p in ps]
        A = us[w['wall']][2]
        Finv = np.linalg.inv(frame(w, A))
        members = []
        for j in extra + dress:
            M = Finv @ us[j][2]
            members.append({'name': us[j][0], 'kind': cm.KIND[us[j][1]],
                            't': [_num(x) for x in M[:3, 3]], 'r': _rows(M[:3, :3])})
        names = [us[w['wall']][0]] + [m['name'] for m in members]
        shown = collections.Counter(x[0]['level'] for x in ws)
        d = {'type': 'fixed', 'family': FAMILY, 'set': art_set(names, shown),
             'anchor': {'name': us[w['wall']][0], 'kind': cm.KIND[us[w['wall']][1]],
                        'r': _rows((Finv @ A)[:3, :3])},
             'members': members, 'look': [k[0], list(k[1])],
             'shown': {'walls': len(ws), 'levels': dict(sorted(shown.items()))}}
        beams = [p for p in ps if p['role'] in LENGTH_ROLES]
        if not extra and len(ps) == 1 and len(beams) == 1:
            p = beams[0]
            j = p['u']
            M = Finv @ us[j][2]
            plo, phi = cat.box(us[j][0], us[j][1])
            axis = int(np.argmax(np.linalg.norm(M[:3, :3] * ((phi - plo) / 2)[None, :], axis=0)))
            lengths = [q['len'] for x in clean for q in x[1] if q['role'] == p['role']]
            d = _length_param(d, {'name': us[j][0], 'kind': cm.KIND[us[j][1]], 'r': M[:3, :3],
                                  't': M[:3, 3], 'len': p['len'], 'axis': axis,
                                  'box_local': (plo, phi), 'member': members[0],
                                  'centre_a': float((p['box'][1 - w['n']] + p['box'][4 - w['n']]) / 2),
                                  'dir_a': float(p['dir'][1 - w['n']])}, w, lengths)
        out.append(d)
    log('%d dressed-wall looks on %d+ walls' % (len(out), NEED))
    return out


def on_face(layout, cat, within=False):
    """Problems of a dressed wall's layout ([(name, kind, 4x4)], wall first): a piece floating
    clear of the wall and every other piece (boxes apart by more than HOST); with `within`
    (placed by a param), also one buried in the wall (box inside its box) or passing the
    wall's ends by more than ALONG, its foot by BELOW or its top by ABOVE."""
    from riftlord.mapeditor import compoundmine as cm
    boxes = []
    for name, kind, m in layout:
        sk = cm.scan_key(name, kind)
        boxes.append(cat.world_box(sk[0], sk[1], np.array(m, float)))
    boxes = np.array(boxes)
    bad = []
    for i in range(1, len(boxes)):
        near = _touch(np.delete(boxes, i, axis=0), boxes[i], HOST)
        if not len(near):
            bad.append('%s floats' % layout[i][0])
        elif within and np.all(boxes[i][:3] >= boxes[0][:3]) and np.all(boxes[i][3:] <= boxes[0][3:]):
            bad.append('%s is buried in the wall' % layout[i][0])
        elif within and (boxes[i][0] < boxes[0][0] - ALONG or boxes[i][3] > boxes[0][3] + ALONG or
                         boxes[i][2] < boxes[0][2] - BELOW or boxes[i][5] > boxes[0][5] + ABOVE):
            bad.append('%s passes the wall' % layout[i][0])
    return bad
