"""Run compounds (compounds.py 'pattern' type): a piece the shipped levels repeat at one step
in its own frame, with the pieces each repeat carries (the cell), as a compound whose
`count` param is the number of sections. Written into compounds.json by compoundmine (`runs`).

The default is the median chain's length and the Sections range runs from the shortest to the
longest shipped chain (`seen`: chains by length); compoundmine keeps a run only where its
default occurs whole on a shipped level.
"""
import collections
import math

import numpy as np


def _rows(M):
    return [[round(float(x), 4) + 0.0 for x in row] for row in M]


def _num(x):
    return round(float(x), 2) + 0.0


def spec(label, lo, hi, step):
    return {'label': label, 'min': lo, 'max': hi, 'step': step}


# -- runs ----------------------------------------------------------------------------------

#: the longest step between two pieces of a run, uu
RUN_STEP_MAX = 2500.0
#: chains of this many and runs of this many chains
RUN_MIN_CHAIN = 3
RUN_MIN_CHAINS = 4
#: a cell piece is on this share of a run's elements
RUN_CELL_SHARE = 0.7
#: a run's piece matches the exemplar chain's within this, uu
RUN_MATCH = 10.0
#: one structural piece repeated alone is a lattice
RUN_LATTICE = {'wall', 'floor', 'ceiling', 'stairs', 'castle', 'kit', 'column', 'window', 'arch',
               'door', 'railing'}


def _yaw_frame(A):
    yaw = math.atan2(A[1, 0], A[0, 0])
    F = np.eye(4)
    F[:2, :2] = [[math.cos(yaw), -math.sin(yaw)], [math.sin(yaw), math.cos(yaw)]]
    F[:3, 3] = A[:3, 3]
    return F


def runs(units, cat, near, role, pos_tol, rot_tol, art_set, log=lambda _s: None):
    """[definition] of the run compounds: a piece repeated at one step in its own frame (the
    same turn) in RUN_MIN_CHAINS+ chains of RUN_MIN_CHAIN+, with the pieces on RUN_CELL_SHARE
    of its elements at one place (the cell); `count` cells, default the median chain."""
    steps = collections.defaultdict(collections.Counter)
    links = collections.defaultdict(list)
    for lv in sorted(units):
        us = units[lv]
        by = collections.defaultdict(list)
        for i, u in enumerate(us):
            if u[1] != 'fx':
                by[(u[0], u[1])].append(i)
        for key in sorted(by):
            ids = by[key]
            if len(ids) < RUN_MIN_CHAIN:
                continue
            P = np.array([us[i][2][:3, 3] for i in ids])
            for a, i in enumerate(ids):
                inv = np.linalg.inv(us[i][2])
                d = np.linalg.norm(P - P[a], axis=1)
                for b in np.nonzero((d > 1) & (d < RUN_STEP_MAX))[0]:
                    rel = inv @ us[ids[b]][2]
                    if np.abs(rel[:3, :3] - np.eye(3)).max() > rot_tol:
                        continue
                    t = tuple(int(round(v)) for v in rel[:3, 3])
                    if next((v for v in t if v), 0) <= 0:
                        continue
                    steps[key][t] += 1
                    links[(key, t)].append((lv, i, ids[b]))
    rows_of = {}
    for lv, (I, J, V) in near.items():
        o = np.argsort(I, kind='stable')
        rows_of[lv] = (J[o], V[o], np.searchsorted(I[o], np.arange(len(units[lv]) + 1)))
    found = []
    for key in sorted(steps):
        for t, n in sorted(steps[key].items(), key=lambda kv: (-kv[1], kv[0]))[:3]:
            if n < RUN_MIN_CHAIN:
                continue
            nxt = {(lv, i): (lv, j) for lv, i, j in links[(key, t)]}
            prv = set(nxt.values())
            chains = []
            for s in sorted(nxt):
                if s in prv:
                    continue
                c = [s]
                while c[-1] in nxt and len(c) < 200:
                    c.append(nxt[c[-1]])
                if len(c) >= RUN_MIN_CHAIN:
                    chains.append(c)
            if len(chains) >= RUN_MIN_CHAINS:
                found.append((key, t, chains))
    # the smallest step per key and direction
    best = {}
    for key, t, chains in found:
        s = np.array(t, float)
        k = (key, tuple(np.round(s / np.linalg.norm(s), 2)))
        if k not in best or np.linalg.norm(s) < np.linalg.norm(best[k][1]):
            best[k] = (key, t, chains)
    cand = []
    for key, t, chains in best.values():
        elems = [e for c in chains for e in c]
        sig = collections.defaultdict(list)
        for lv, i in elems:
            J, V, st = rows_of[lv]
            for k in range(st[i], st[i + 1]):
                u = units[lv][J[k]]
                if (u[0], u[1]) == key and np.abs(V[k][3:] - np.eye(3).ravel()).max() <= rot_tol:
                    continue
                q = (u[0], u[1]) + tuple(np.round(V[k][:3] / pos_tol).astype(int)) + \
                    tuple(np.round(V[k][3:] / 0.05).astype(int))
                sig[q].append(((lv, i), V[k], (lv, int(J[k]))))
        cell = []
        cell_units = set()
        for q in sorted(sig):
            items = sig[q]
            if len({e for e, _v, _u in items}) >= RUN_CELL_SHARE * len(elems):
                cell.append((q[0], q[1], np.mean([v for _e, v, _u in items], axis=0)))
                cell_units |= {u for _e, _v, u in items}
        cand.append((key, t, chains, cell, set(elems) | cell_units))
    cand.sort(key=lambda c: (-len(c[2]) * (1 + len(c[3])), c[0], c[1]))
    claimed = set()
    out = []
    for key, t, chains, cell_raw, mine in cand:
        if not cell_raw and role(key[0], key[1]) in RUN_LATTICE:
            continue
        if len(mine & claimed) > 0.5 * len(mine):
            continue
        claimed |= mine
        lens = [len(c) for c in chains]
        #: the median chain (the lower one of an even count), so the default is a shipped length
        count = sorted(lens)[(len(lens) - 1) // 2]
        chain = next((c for c in chains if len(c) == count), chains[0])
        lv, i0 = chain[0]
        A = units[lv][i0][2]
        F = _yaw_frame(A)
        R = (np.linalg.inv(F) @ A)[:3, :3]
        sf = R @ np.array(t, float)
        axis = int(np.argmax(np.abs(sf)))
        if np.abs(np.delete(sf, axis)).max() > 1.0:
            continue
        sv = float(sf[axis])
        period = abs(sv)
        #: where the exemplar chain's pieces are, by name
        placed_at = collections.defaultdict(list)
        for u in units[lv]:
            placed_at[u[0]].append(u[2][:3, 3])

        def on_chain(name, tt):
            """How many of the `count` copies of a piece at `tt` (frame) the exemplar has."""
            got = 0
            for n in range(count):
                at = tt.copy()
                at[axis] += n * sv
                w = F[:3, :3] @ at + F[:3, 3]
                got += any(np.abs(q - w).max() <= RUN_MATCH for q in placed_at[name])
            return got
        cell, seen = [], set()
        for k, kd, v in cell_raw:
            M = np.eye(4)
            M[:3, :3] = R @ np.array(v[3:]).reshape(3, 3)
            tt = R @ np.array(v[:3])
            fold = tt[axis] % period
            if fold > period / 2:
                fold -= period
            #: the period the exemplar chain carries the piece in (the chain's ends differ)
            best_at = None
            for shift in (0.0, period, -period):
                cand_t = tt.copy()
                cand_t[axis] = fold + math.copysign(shift, sv)
                score = on_chain(k, cand_t)
                if best_at is None or score > best_at[0]:
                    best_at = (score, cand_t[axis])
            tt[axis] = best_at[1]
            q = (k, kd) + tuple(np.round(tt / 5).astype(int)) + \
                tuple(np.round(M[:3, :3].ravel() / 0.05).astype(int))
            if q not in seen:
                seen.add(q)
                cell.append((k, kd, tt, M[:3, :3]))
        kind = 'kit' if key[1] == 'bp' else key[1]
        parts = [{'name': key[0], 'kind': kind, 'r': _rows(R), 't': [0.0, 0.0, 0.0],
                  'repeat': {'count': ['count', 1, -1], 'axis': axis, 'step': _num(sv),
                             'at': ['count', _num(sv / 2), 0.0]}}]
        for k, kd, tt, Rm in cell:
            parts.append({'name': k, 'kind': 'kit' if kd == 'bp' else kd, 'r': _rows(Rm),
                          't': [_num(x) for x in tt],
                          'repeat': {'count': ['count', 1, 0], 'axis': axis, 'step': _num(sv),
                                     'at': ['count', _num(sv / 2), _num(tt[axis] - sv / 2)]}})
        lvs = collections.Counter(c[0][0] for c in chains)
        names = [key[0]] + [c[0] for c in cell]
        d = {'type': 'pattern', 'family': 'run', 'set': art_set(names, list(lvs)),
             'anchor': {'name': key[0], 'kind': kind, 'r': _rows(R)}, 'parts': parts,
             'params': {'count': count},
             'paramSpec': {'count': spec('Sections', min(lens), max(lens), 1)},
             'seen': {'count': {str(n): k for n, k in sorted(collections.Counter(lens).items())}},
             'copies': len(chains), 'levels': dict(sorted(lvs.items()))}
        out.append(d)
    log('%d run compounds' % len(out))
    return out
