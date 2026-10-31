"""Step a coordinator's ubergraph the way the game does, and report a cycle.

THE check for a coordinator edit. `bppatch.declaration` answers "what could this wave
declare" by exploring both sides of every test; this answers "what does the game actually
run", which is a different question and the one that matters. A cloned block with no
terminator passed every structural check and looped forever in game -- CPU pinned, memory
climbing, stuck on the loading screen -- and this reproduces it offline in a second.

`bppatch.declaration` dedupes on `(pos, len(stack))`, so a flow that loops forever simply
stops early and reads as a clean walk -- which is why an appended arm passed every offline
check and hung the game. This keeps no dedupe: it records each state and says when one
repeats.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.onslaught import bppatch  # noqa: E402
from riftlord.onslaught.bppatch import _flat, _varname  # noqa: E402

COMPARISONS = bppatch.COMPARISONS


def step(mm, entry, bindings, names, resolve, limit=20000):
    """(outcome, path) for one (Wave, Spawner). Outcome is 'end', 'cycle' or 'limit'."""
    by_mem = {m: i for i, (m, _s, _n) in enumerate(mm)}
    pos, stack, vars = entry, [], {}
    path, states = [], set()
    for _ in range(limit):
        i = by_mem.get(pos)
        if i is None:
            return 'end', path
        state = (pos, tuple(stack))
        if state in states:
            return 'cycle', path + [i]
        states.add(state)
        path.append(i)
        n = mm[i][2]

        if n.name in ('Let', 'LetBool'):
            dest = next((_varname(x, names) for x in _flat(n)
                         if x.name in ('LocalVariable', 'InstanceVariable')), None)
            call = next((x for x in _flat(n)
                         if x.name in ('CallMath', 'FinalFunction',
                                       'LocalFinalFunction')), None)
            value = None
            if call is not None:
                fn = str(resolve(call.ops[0][1]))
                if fn in COMPARISONS:
                    lit = next((x.ops[0][1] for x in _flat(call)
                                if x.name in ('IntConst', 'ByteConst')), None)
                    var = next((_varname(x, names) for x in _flat(call)
                                if x.name in ('LocalVariable', 'InstanceVariable')), '')
                    got = vars.get(var)
                    if got is None:
                        got = next((v for k, v in bindings.items()
                                    if k.lower() in var.lower()), None)
                    if got is not None and lit is not None:
                        value = (got != lit) if fn.startswith('NotEqual') else \
                                (got > lit) if fn.startswith('Greater') else \
                                (got < lit) if fn.startswith('Less') else (got == lit)
                else:
                    value = next((v for k, v in bindings.items()
                                  if k.lower() in fn.lower()), None)
            elif n.name == 'LetBool':
                src = [_varname(x, names) for x in _flat(n)
                       if x.name in ('LocalVariable', 'InstanceVariable')]
                if len(src) > 1 and src[1] in vars:
                    value = vars[src[1]]
            if dest is not None:
                if value is None:
                    vars.pop(dest, None)
                else:
                    vars[dest] = value

        elif n.name == 'JumpIfNot':
            cond = next((_varname(x, names) for k, x in n.ops
                         if k == 'expr' and x.name in ('LocalVariable',
                                                       'InstanceVariable')), None)
            if vars.get(cond) is False:
                pos = n.ops[0][1]
                continue
            # An unknown condition takes the fall-through, as the game would with
            # whatever the real value is; this is one concrete run, not a union.

        elif n.name in ('Return', 'EndOfScript', 'ComputedJump'):
            return 'end', path
        elif n.name == 'PushExecutionFlow':
            stack.append(n.ops[0][1])
        elif n.name == 'PopExecutionFlow':
            if not stack:
                return 'end', path
            pos = stack.pop()
            continue
        elif n.name == 'Jump':
            pos = n.ops[0][1]
            continue

        pos = mm[i + 1][0] if i + 1 < len(mm) else None
        if pos is None:
            return 'end', path
    return 'limit', path


def survey(bp_name, waves=8, spawners=3):
    """Every (wave, spawner) the game would enumerate, and how each one ends."""
    from riftlord.build.navigation import navpatch  # noqa: F401
    from riftlord.onslaught import wavespec
    pkg, names, resolve = bppatch.load(bp_name)
    _w, mm = wavespec.ubergraph(pkg, names, resolve)
    entry = bppatch.entry_points(pkg, names, resolve)['Onslaught_Start']
    bad = []
    for wave in range(1, waves + 1):
        for spawner in range(1, spawners + 1):
            how, path = step(mm, entry, {'Wave': wave, 'Spawner': spawner},
                             names, resolve)
            if how != 'end':
                bad.append((wave, spawner, how, len(path), path[-6:]))
    return bad


def _from_blobs(ua, ux):
    """(pkg, names, resolve) of a coordinator held in memory, as bppatch.load gives."""
    import struct
    from riftlord.formats.ue.uassetio import Package
    pkg = Package(ua, ux)
    names = [n for n, _ in pkg.names]

    def resolve(i):
        if i < 0:
            return names[struct.unpack_from('<I', pkg.imports[-i - 1], 20)[0]]
        if i > 0:
            return names[struct.unpack_from('<I', pkg.exports[i - 1]['meta'], 16)[0]]
        return 'null'
    return pkg, names, resolve


#: Waves and spawners units_run steps through.
UNIT_WAVES, UNIT_SPAWNERS = 24, 16


def units_run(bp_name=None, blobs=None, waves=UNIT_WAVES, spawners=UNIT_SPAWNERS, named=True):
    """{(wave, spawner): [unit]} each spawn call along the REAL flow sends, for a
    coordinator by name or as (uasset, uexp) `blobs`. A unit is its row name from
    onslaught.protos() (shipped rows and installed mods' minion rows), else its Guid as
    32 hex digits (stored bytes); with `named` False, always the hex."""
    import struct
    from riftlord.onslaught import wavespec
    pkg, names, resolve = _from_blobs(*blobs) if blobs else bppatch.load(bp_name)
    _w, mm = wavespec.ubergraph(pkg, names, resolve)
    entry = bppatch.entry_points(pkg, names, resolve)['Onslaught_Start']
    by_guid = {}
    if named:
        from riftlord.onslaught import onslaught
        by_guid = {struct.pack('<4i', *g).hex(): n for n, g in onslaught.protos().items()}
    out = {}
    for wave in range(1, waves + 1):
        for spawner in range(1, spawners + 1):
            _how, path = step(mm, entry, {'Wave': wave, 'Spawner': spawner}, names, resolve)
            got = []
            for i in path:
                for call in bppatch.spawn_calls_in(mm[i][2]):
                    g = struct.pack('<4i', *call['guid']).hex()
                    got.append(by_guid.get(g, g))
            if got:
                out[(wave, spawner)] = got
    return out


def calls_run(bp_name, waves=8, spawners=3):
    """{(wave, spawner): {call name: count}} along the REAL flow, not a union."""
    from riftlord.onslaught import wavespec
    pkg, names, resolve = bppatch.load(bp_name)
    _w, mm = wavespec.ubergraph(pkg, names, resolve)
    entry = bppatch.entry_points(pkg, names, resolve)['Onslaught_Start']
    out = {}
    for wave in range(1, waves + 1):
        for spawner in range(1, spawners + 1):
            how, path = step(mm, entry, {'Wave': wave, 'Spawner': spawner},
                             names, resolve)
            got = {}
            for i in path:
                node = mm[i][2]
                if node.name in ('FinalFunction', 'LocalFinalFunction'):
                    fn = str(resolve(node.ops[0][1]))
                    if fn.startswith('Onslaught'):
                        got[fn] = got.get(fn, 0) + 1
            if got or how != 'end':
                out[(wave, spawner)] = dict(got, _outcome=how)
    return out
