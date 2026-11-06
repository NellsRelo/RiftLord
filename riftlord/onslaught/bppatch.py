"""Read and rewrite an onslaught Blueprint's waves, without the Unreal Editor.

A spawn call appears in the Ubergraph as

    FinalFunction OnslaughtSpawnEntities
      StructConst OMDSoftProtoPtr -> StructConst Guid -> 4 x IntConst   (the unit)
      IntConst / IntZero / IntOne                                       (count)
      True / False                                                      (shield)
      ...

so a unit is four integer constants and a count is one. This finds them, names the
units against AICharacterProtos, and can substitute either.

    python3 bppatch.py --bp BP_OMDOnslaughtCoordinator_Scenario01
    python3 bppatch.py --bp BP_... --swap OrcArcher=GnollAssassin --out patched/
"""

import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.formats.ue.uassetio import Package  # noqa: E402
from riftlord.formats.ue.kismet import Walker, find_bytecode, emit  # noqa: E402
from riftlord.build.navigation.navpatch import live_index, read_from_pak  # noqa: E402
from riftlord.onslaught import onslaught  # noqa: E402

SPAWN_FNS = ('OnslaughtSpawnEntities', 'OnslaughtSpawnEntitiesFormation')


def coordinators():
    """Every onslaught coordinator Blueprint, name -> package path.

    The base game keeps them in /Game/Data/Onslaughts; the DLC campaigns use a
    DLCOnslaughts subfolder, so the folder cannot be assumed. Installed mods keep theirs
    under `/Game/Mods/<mod>/` and name them `<mod>_Onslaught01` with no "Coordinator" in
    the name at all, so the test is on `Onslaught` and the index is the one the GAME
    sees -- otherwise the editor cannot open a mission these tools produced.
    """
    out = {}
    for key in live_index():
        if not key.endswith('.uasset'):
            continue
        leaf = key.rsplit('/', 1)[-1][:-len('.uasset')]
        if 'Onslaught' not in leaf or leaf == 'BP_OMDOnslaughtCoordinator':
            continue
        out[leaf] = key[:-len('.uasset')]
    return dict(sorted(out.items()))


def load(bp_name):
    idx = live_index()
    key = coordinators().get(bp_name)
    if key is None or key + '.uasset' not in idx:
        raise SystemExit('unknown coordinator %r' % bp_name)
    p1, n1 = idx[key + '.uasset']
    p2, n2 = idx[key + '.uexp']
    # Read through the helper: a mod's pak is v4 and the shipped reader cannot open it.
    pkg = Package(read_from_pak(p1, n1), read_from_pak(p2, n2))
    names = [n for n, _ in pkg.names]

    def resolve(i):
        if i < 0:
            return names[struct.unpack_from('<I', pkg.imports[-i - 1], 20)[0]]
        if i > 0:
            return names[struct.unpack_from('<I', pkg.exports[i - 1]['meta'], 16)[0]]
        return 'null'
    return pkg, names, resolve


def dispatch(mm, entry, bindings, names, resolve, limit=4000):
    """Which block an event call reaches, by simulating the dispatch.

    `bindings` maps a variable name fragment to an integer, e.g.
    {'Wave': 3, 'Spawner': 1}. The walk follows Jump, JumpIfNot,
    PushExecutionFlow and PopExecutionFlow, and evaluates the ladder's
    NotEqual_IntInt tests against the bindings, so it does not care whether a
    ladder is laid out flat, chained through pushed offsets, or nested.

    Returns (block_offset, trail, seen) where block_offset is where control
    settles -- the first statement that is not part of the dispatch -- or None
    if the walk falls out of the function. `seen` is every statement the walk
    stepped through, which is the dispatch machinery itself and so the right
    thing to bound a block against.
    """
    by_mem = dict((m, i) for i, (m, _s, _n) in enumerate(mm))
    stack, trail, temp, seen = [], [], {}, set()
    match = None                              # target of the last matched test
    pos = entry
    for _ in range(limit):
        i = by_mem.get(pos)
        if i is None:
            return None, trail, seen
        n = mm[i][2]
        seen.add(pos)
        if n.name == 'LetBool':
            call = next((x for x in _flat(n) if x.name == 'CallMath'), None)
            if call is not None and resolve(call.ops[0][1]) == 'NotEqual_IntInt':
                lit = next((x for x in _flat(call) if x.name == 'IntConst'), None)
                var = next((_varname(x, names) for x in _flat(call)
                            if x.name == 'LocalVariable'), '')
                value = next((v for k, v in bindings.items()
                              if k.lower() in var.lower()), None)
                if lit is not None and value is not None:
                    temp['t'] = (value != lit.ops[0][1])
                    trail.append((var, lit.ops[0][1], temp['t']))
                    pos = mm[i + 1][0] if i + 1 < len(mm) else None
                    if pos is None:
                        return None, trail, seen
                    continue
            return (match if match is not None else mm[i][0]), trail, seen
        if n.name == 'JumpIfNot':
            if temp.pop('t', None) is False:      # the test matched
                match = pos = n.ops[0][1]         # may be another ladder
                continue
            pos = mm[i + 1][0] if i + 1 < len(mm) else None
            if pos is None:
                return None, trail, seen
            continue
        if n.name == 'Jump':
            pos = n.ops[0][1]
            continue
        if n.name == 'PushExecutionFlow':
            stack.append(n.ops[0][1])
            pos = mm[i + 1][0] if i + 1 < len(mm) else None
            continue
        if n.name == 'PopExecutionFlow':
            if not stack:
                return None, trail, seen
            pos = stack.pop()
            continue
        # A block begins at the branch that selected it, not where the walk
        # settles: its own leading Jump or Push looks like dispatch from here.
        return (match if match is not None else mm[i][0]), trail, seen
    return None, trail, seen


#: Comparisons the declaration walk can evaluate against bound parameters.
COMPARISONS = ('NotEqual_IntInt', 'NotEqual_ByteByte', 'Greater_IntInt',
               'EqualEqual_IntInt', 'Less_IntInt')


def declaration(mm, entry, bindings, names, resolve, limit=8000,
                branch_unknown=False):
    """Every statement an event call executes, in order, for these parameters.

    This is the whole declaration, not just "the block": a wave may be prefixed
    by an OnslaughtAction hook that pops back into the ladder afterwards, so
    stopping at the first call misses everything the wave actually spawns.

    Ladder tests are evaluated against `bindings` -- keys are matched as
    substrings of the variable name, so {'Wave': 3, 'Spawner': 1} answers the
    wave ladder and {'Endless': 0, 'Difficulty': 2} answers the others.

    A condition that cannot be evaluated (its value comes from a function call
    that is not bound) takes the fall-through. With `branch_unknown` it explores
    BOTH sides instead and returns the union, which is what shows every wave a
    coordinator can declare rather than one difficulty's worth.
    """
    by_mem = dict((m, i) for i, (m, _s, _n) in enumerate(mm))
    out, seen = [], set()
    work = [(entry, (), {})]
    steps = 0
    while work and steps < limit:
        pos, saved, vars0 = work.pop()
        stack, vars = list(saved), dict(vars0)
        while steps < limit:
            steps += 1
            i = by_mem.get(pos)
            if i is None or (pos, len(stack)) in seen:
                break
            seen.add((pos, len(stack)))
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
                                    if x.name in ('LocalVariable',
                                                  'InstanceVariable')), '')
                        got = vars.get(var)
                        if got is None:
                            got = next((v for k, v in bindings.items()
                                        if k.lower() in var.lower()), None)
                        if got is not None and lit is not None:
                            value = (got != lit) if fn.startswith('NotEqual') else \
                                    (got > lit) if fn.startswith('Greater') else \
                                    (got < lit) if fn.startswith('Less') else (got == lit)
                    else:
                        # a value the walk can only know if it is bound, e.g.
                        # GetEndlessIteration or GetGameDifficulty
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
                if call is not None and str(resolve(call.ops[0][1])).startswith('Onslaught'):
                    out.append(i)

            elif n.name == 'JumpIfNot':
                cond = next((_varname(x, names) for k, x in n.ops
                             if k == 'expr' and x.name in ('LocalVariable',
                                                           'InstanceVariable')), None)
                val = vars.get(cond)
                if val is False:
                    pos = n.ops[0][1]
                    continue
                if val is None and branch_unknown:
                    work.append((n.ops[0][1], tuple(stack), dict(vars)))

            elif n.name in ('Return', 'EndOfScript', 'ComputedJump'):
                break
            elif n.name == 'PushExecutionFlow':
                stack.append(n.ops[0][1])
            elif n.name == 'PopExecutionFlow':
                if not stack:
                    break
                pos = stack.pop()
                continue
            elif n.name == 'Jump':
                pos = n.ops[0][1]
                continue
            else:
                out.append(i)

            pos = mm[i + 1][0] if i + 1 < len(mm) else None
            if pos is None:
                break
    return out


def wave_map(mm, entry, names, resolve, waves=16, spawners=16):
    """{(wave, spawner): block offset} by asking the dispatch for each pair.

    A gap is meaningful, not an end: on a map with several spawners a wave
    exists only for the spawners that send something, so the scan covers the
    whole range rather than stopping at the first miss.
    """
    out, seen = {}, set()
    for wave in range(1, waves + 1):
        for spawner in range(1, spawners + 1):
            block, _trail, walked = dispatch(
                mm, entry, {'Wave': wave, 'Spawner': spawner}, names, resolve)
            seen |= walked
            if block is not None:
                out[(wave, spawner)] = block
    return out, seen


def find_test(mm, names, resolve, fragment):
    """A statement index whose LetBool tests `fragment` against a literal.

    Used as a donor to clone: cloning a shipped test copies a working
    comparison against the right event parameter without building one.
    """
    for i, (_m, _s, n) in enumerate(mm):
        if n.name != 'LetBool' or i + 1 >= len(mm) or mm[i + 1][2].name != 'JumpIfNot':
            continue
        call = next((x for x in _flat(n) if x.name == 'CallMath'), None)
        if call is None or resolve(call.ops[0][1]) != 'NotEqual_IntInt':
            continue
        var = next((_varname(x, names) for x in _flat(call)
                    if x.name == 'LocalVariable'), '')
        if fragment.lower() in var.lower():
            return i
    return None


def find_jump(mm):
    """A statement index holding a bare Jump, to clone as an unconditional one."""
    return next((i for i, (_m, _s, n) in enumerate(mm) if n.name == 'Jump'), None)


def clone_jump(mm, target):
    """A copy of some Jump statement, retargeted."""
    import copy
    i = find_jump(mm)
    if i is None:
        raise SystemExit('no Jump to clone in this function')
    j = copy.deepcopy(mm[i][2])
    j.ops[0] = (j.ops[0][0], target)
    return j


def append_wave(nodes, mm, names, resolve, entry, wave, spawner, block, close):
    """A new wave, added without touching the existing ladder.

    Builds a test chain out of cloned statements, appends it, and returns the
    node the event's entry must now point at:

        if SpawnerIndex != S  -> the original entry
        if WaveNumber == W    -> `block`
        if WaveNumber == W+1  -> `close`
        otherwise             -> the original entry

    Catching W before the original ladder sees it means the ladder needs no
    edit at all, so this works whatever shape it has. Catching W+1 keeps the
    onslaught's close reachable one number further on, which is what stops the
    wave enumeration running away.
    """
    wave_donor = find_test(mm, names, resolve, 'Wave')
    spawn_donor = find_test(mm, names, resolve, 'Spawner')
    if wave_donor is None:
        raise SystemExit('no WaveNumber test to clone in this function')

    chain = []
    if spawn_donor is not None and spawner is not None:
        sp = clone_rung(mm, {'index': spawn_donor, 'jump': spawn_donor + 1},
                        spawner, None)
        chain += sp + [clone_jump(mm, entry)]
    mine = clone_rung(mm, {'index': wave_donor, 'jump': wave_donor + 1}, wave, block)
    closer = clone_rung(mm, {'index': wave_donor, 'jump': wave_donor + 1},
                        wave + 1, close)
    chain += mine + closer + [clone_jump(mm, entry)]
    if spawn_donor is not None and spawner is not None:
        chain[1].ops[0] = (chain[1].ops[0][0], chain[3])   # spawner match -> wave test

    at = len(nodes) - 1                     # before EndOfScript
    for k, n in enumerate(chain):
        nodes.insert(at + k, n)
    return chain[0]
def ladder_at(mm, start, names, resolve):
    """The dispatch ladder reachable from a memory offset, as statement indices.

    Each rung is {'index', 'jump', 'const', 'var', 'target'}: the LetBool's
    statement index, its JumpIfNot's index, the number tested, the variable
    tested, and the memory offset control reaches on a match.

    Two shapes occur. A flat ladder falls through from one rung to the next, so
    the following test sits two statements on. A continuation ladder pushes the
    next test before each rung and pops to it when the test fails, so the
    following test is the pushed offset. Both are walked here, as is a preamble
    of Jump, PushExecutionFlow and PopExecutionFlow before the first rung.
    """
    by_mem = dict((m, i) for i, (m, _s, _n) in enumerate(mm))

    def rung_at(pos):
        i = by_mem.get(pos)
        if i is None or i + 1 >= len(mm) or mm[i][2].name != 'LetBool':
            return None
        calls = [x for x in _flat(mm[i][2]) if x.name == 'CallMath']
        if not calls or resolve(calls[0].ops[0][1]) != 'NotEqual_IntInt':
            return None
        if mm[i + 1][2].name != 'JumpIfNot':
            return None
        return i

    out, seen, pending = [], set(), None
    pos = start
    for _ in range(len(mm)):                 # preamble: reach the first rung
        if rung_at(pos) is not None or pos not in by_mem:
            break
        n = mm[by_mem[pos]][2]
        if n.name == 'Jump':
            pos = n.ops[0][1]
        elif n.name == 'PushExecutionFlow':
            pending = n.ops[0][1]
            pos = mm[by_mem[pos] + 1][0] if by_mem[pos] + 1 < len(mm) else None
        elif n.name == 'PopExecutionFlow':
            pos = pending
        else:
            break
        if pos is None:
            break

    while pos in by_mem and pos not in seen:
        seen.add(pos)
        i = rung_at(pos)
        if i is None:
            break
        call = next(x for x in _flat(mm[i][2]) if x.name == 'CallMath')
        consts = [x for x in _flat(call) if x.name == 'IntConst']
        var = next((_varname(x, names) for x in _flat(call)
                    if x.name == 'LocalVariable'), '?')
        jump = mm[i + 1][2]
        out.append({'index': i, 'jump': i + 1,
                    'const': consts[0].ops[0][1] if consts else None,
                    'var': var, 'target': jump.ops[0][1], 'push': pending})
        nxt = mm[i + 2][0] if i + 2 < len(mm) else None
        if nxt is not None and rung_at(nxt) is None:
            nxt = pending                    # a failed test pops to the next one
            if nxt in by_mem and mm[by_mem[nxt]][2].name == 'PushExecutionFlow':
                pending = mm[by_mem[nxt]][2].ops[0][1]
                nxt = mm[by_mem[nxt] + 1][0] if by_mem[nxt] + 1 < len(mm) else None
        pos = nxt
    return out


def find_ladder(mm, entry, names, resolve, want='Wave', depth=0):
    """The ladder that sorts on a variable whose name contains `want`.

    Ladders nest, and which of wave and spawner comes first is not fixed, so
    this descends from the event's entry until it finds the right one.
    """
    rungs = ladder_at(mm, entry, names, resolve)
    if not rungs or depth > 4:
        return []
    if want.lower() in rungs[0]['var'].lower():
        return rungs
    for r in rungs:
        found = find_ladder(mm, r['target'], names, resolve, want, depth + 1)
        if found:
            return found
    return []


def clone_rung(mm, rung, const, target):
    """A copy of a rung testing `const` and branching to `target`.

    `target` may be a memory offset in the pre-edit layout or a Node already in
    the statement list; either resolves during relocation. Returns the two new
    statements, to be inserted together.
    """
    import copy
    letbool = copy.deepcopy(mm[rung['index']][2])
    jump = copy.deepcopy(mm[rung['jump']][2])
    call = next(x for x in _flat(letbool) if x.name == 'CallMath')
    lit = next(x for x in _flat(call) if x.name == 'IntConst')
    lit.ops[0] = (lit.ops[0][0], const)
    jump.ops[0] = (jump.ops[0][0], target)
    return [letbool, jump]


def terminator_rung(mm, rungs):
    """The rung that closes the onslaught: the highest-numbered one.

    Measured across all 48 coordinators with a wave ladder: the highest rung's
    block is always two statements with no spawn calls, closing the onslaught
    with OnslaughtEnd (33) or OnslaughtAction (15). The game enumerates wave
    numbers until that block runs, so a new wave goes BEFORE it and the
    terminator is renumbered to stay highest. Leave it unreachable and the
    enumeration never ends -- the level hangs while loading.
    """
    return max(rungs, key=lambda r: r['const']) if rungs else None


def is_flat_ladder(rungs):
    """True when each rung falls through to the next two statements on.

    A continuation ladder reaches its next test through a pushed offset, so a
    rung inserted between two of its statements is simply jumped over.
    """
    return all(rungs[i + 1]['index'] == rungs[i]['index'] + 2
               for i in range(len(rungs) - 1))


def set_rung_const(mm, rung, const):
    """Renumber the wave a rung tests for."""
    call = next(x for x in _flat(mm[rung['index']][2]) if x.name == 'CallMath')
    lit = next(x for x in _flat(call) if x.name == 'IntConst')
    lit.ops[0] = (lit.ops[0][0], const)


def block_bounds(mm, rungs, keep):
    """Memory offsets a block must not run into, for `block_statements`.

    `keep` is the entry offset of the block being collected, which is excluded
    so a block does not bound itself.

    Other waves' blocks alone are not enough: a continuation ladder reaches the
    next test through a pushed offset, so without the ladder's own statements
    and its pushed continuations in the bound, one wave's block collects the
    rest of the ladder and every wave after it.
    """
    out = set(r['target'] for r in rungs)
    out |= set(mm[r['index']][0] for r in rungs)
    out |= set(r['push'] for r in rungs if r.get('push') is not None)
    return out - {keep}


def block_statements(mm, start, others):
    """Statement indices reachable from a block entry, in layout order.

    The same flow simulation as wave_table's collector: follow Jump, both sides
    of JumpIfNot and the pushed continuation, and refuse to enter another
    block's entry so one wave does not absorb the next one's statements.
    """
    by_mem = dict((m, i) for i, (m, _s, _n) in enumerate(mm))
    found, visited = set(), set()
    work = [(start, tuple())]
    while work:
        pos, saved = work.pop()
        stack = list(saved)
        while pos in by_mem and pos not in visited:
            visited.add(pos)
            i = by_mem[pos]
            n = mm[i][2]
            found.add(i)
            if n.name in ('Return', 'EndOfScript', 'ComputedJump'):
                break
            if n.name == 'PushExecutionFlow':
                stack.append(n.ops[0][1])
            elif n.name == 'PopExecutionFlow':
                if not stack:
                    break
                pos = stack.pop()
                if pos in others:
                    break
                continue
            elif n.name == 'Jump':
                pos = n.ops[0][1]
                if pos in others:
                    break
                continue
            elif n.name == 'JumpIfNot':
                tgt = n.ops[0][1]
                if tgt not in others:
                    work.append((tgt, tuple(stack)))
            nxt = mm[i + 1][0] if i + 1 < len(mm) else None
            if nxt is None or nxt in others:
                break
            pos = nxt
    return sorted(found)


def block_sequence(mm, start, others, limit=4000):
    """Statement indices of a block in EXECUTION order.

    Blocks are threaded through pushed continuations, so their layout order is
    not the order the onslaught is declared in. This walks the flow the way the
    game does, which is what says where a wave's calls really fall -- in
    particular that a break is the LAST call of the wave it ends.
    """
    by_mem = dict((m, i) for i, (m, _s, _n) in enumerate(mm))
    order, seen, stack = [], set(), []
    pos = start
    for _ in range(limit):
        i = by_mem.get(pos)
        if i is None or pos in seen or pos in others:
            break
        seen.add(pos)
        n = mm[i][2]
        order.append(i)
        if n.name in ('Return', 'EndOfScript', 'ComputedJump'):
            break
        if n.name == 'PushExecutionFlow':
            stack.append(n.ops[0][1])
        elif n.name == 'PopExecutionFlow':
            if not stack:
                break
            pos = stack.pop()
            continue
        elif n.name == 'Jump':
            pos = n.ops[0][1]
            continue
        pos = mm[i + 1][0] if i + 1 < len(mm) else None
        if pos is None:
            break
    return order


def clone_block(mm, indices, entry=None):
    """Copies of those statements, with jumps between them rewired to the copies.

    A jump leaving the block keeps its old offset and relocates normally; a jump
    within it becomes a reference to the corresponding copy, which resolves once
    the edited statement list has positions.

    Returns (clones, entry_clone). `entry` is the block's entry memory offset;
    statements come back in layout order, and the entry is rarely the first of
    them, so a rung must branch to `entry_clone`, not to `clones[0]`.

    A SwitchValue's case chain points inside its own statement; in a copy it becomes
    (copy, offset into it), or the copy's cases would run the original's and carry on
    after the original statement.
    """
    import copy
    from riftlord.formats.ue.kismet import code_offsets, node_sizes
    clones = [copy.deepcopy(mm[i][2]) for i in indices]
    inside = dict((mm[i][0], clones[k]) for k, i in enumerate(indices))
    spans = [(mm[i][0], mm[i][0] + node_sizes(mm[i][2])[1], clones[k])
             for k, i in enumerate(indices)]
    for c in clones:
        for node, j in code_offsets([c]):
            kind, old = node.ops[j]
            # a case chain ends at the end of its statement at most, which is also
            # where the next statement starts
            home = next((s for s in spans if s[2] is c and s[0] < old <= s[1]), None) \
                if node.name == 'SwitchValue' and isinstance(old, int) else None
            if home is not None:
                node.ops[j] = (kind, (c, old - home[0]))
            elif isinstance(old, int) and old in inside:
                node.ops[j] = (kind, inside[old])
    return clones, (inside.get(entry) if entry is not None else clones[0])


def remove_rung(nodes, mm, rung):
    """Drop a rung from a ladder, making its wave unreachable.

    Only the two dispatch statements go; the block they guarded stays as dead
    code, which keeps every other block's statements untouched. Control falls
    through the gap to the next rung, so anything that pointed at the removed
    pair relocates to the next surviving statement.
    """
    letbool, jump = mm[rung['index']][2], mm[rung['jump']][2]
    nodes.remove(letbool)
    nodes.remove(jump)
    return [letbool, jump]


def functions(pkg, names, resolve):
    """(export, name) for every Function export in the package."""
    for e in pkg.exports:
        if resolve(struct.unpack_from('<i', e['meta'], 0)[0]) == 'Function':
            yield e, names[struct.unpack_from('<I', e['meta'], 16)[0]]


def edit_ubergraph(pkg, names, resolve, mutate):
    """Restructure the ubergraph: `mutate(nodes, memory_map)` edits it in place.

    Adding or removing a statement invalidates every jump in the function, so
    this relocates all of them, rewrites both size fields, and moves each event's
    entry literal in its own stub. Statements are matched by identity, so mutate
    must insert new Node objects rather than reuse existing ones.

    If `mutate` returns a Node, that statement becomes the new entry point for
    every event that shared the old one -- which is how a test chain appended to
    the end gets control before the original ladder.

    Returns {'relocated', 'entries', 'mem', 'ser', 'statements'}.
    """
    from riftlord.formats.ue.kismet import find_bytecode, memory_map, node_sizes, emit, shift_table, relocate, unrelocatable

    uber = next(e for e, n in functions(pkg, names, resolve)
                if n.startswith('ExecuteUbergraph'))
    off, mem, size, w = find_bytecode(uber['data'], names, resolve)
    blocked = unrelocatable(w.nodes)
    if blocked:
        raise SystemExit('cannot relocate a function using %s' % ', '.join(sorted(blocked)))

    before = memory_map(w.nodes)
    new_head = mutate(w.nodes, before)
    after = memory_map(w.nodes)
    translate = shift_table(before, after)
    moved = relocate(w.nodes, translate,
                     {id(n): m for m, _s, n in after})

    code = emit(w.nodes)
    new_mem = after[-1][0] + node_sizes(after[-1][2])[1] if after else 0
    data = bytearray(uber['data'])
    data[off + 8:off + 8 + size] = code
    struct.pack_into('<ii', data, off, new_mem, len(code))
    uber['data'] = bytes(data)

    positions = {id(n): m for m, _s, n in after}
    old_entries = entry_points(pkg, names, resolve)
    wanted = dict((v, translate(v)) for v in set(old_entries.values()))
    if new_head is not None:
        head = positions[id(new_head)]
        for name, off in old_entries.items():
            if name == 'Onslaught_Start':
                wanted[off] = head
    moved_entries = 0
    for e, name in functions(pkg, names, resolve):
        if name not in old_entries:
            continue
        soff, smem, ssize, sw = find_bytecode(e['data'], names, resolve)
        hits = []

        def scan(n):
            for k, v in n.ops:
                if k != 'expr':
                    continue
                if v.name == 'IntConst' and v.ops and v.ops[0][1] in wanted:
                    hits.append(v)
                scan(v)
        for n in sw.nodes:
            scan(n)
        if not hits:
            continue
        for v in hits:
            v.ops[0] = (v.ops[0][0], wanted[v.ops[0][1]])
            moved_entries += 1
        scode = emit(sw.nodes)
        sdata = bytearray(e['data'])
        sdata[soff + 8:soff + 8 + ssize] = scode
        struct.pack_into('<ii', sdata, soff, smem, len(scode))
        e['data'] = bytes(sdata)

    return {'relocated': moved, 'entries': moved_entries, 'mem': new_mem,
            'ser': len(code), 'statements': len(after)}


def guid_of(node):
    """The four IntConsts under a StructConst Guid, as a tuple, or None."""
    ints = []
    for kind, val in node.ops:
        if kind == 'expr':
            if val.name == 'StructConst':
                inner = guid_of(val)
                if inner:
                    return inner
            elif val.name == 'IntConst':
                ints.append(val.ops[0][1])
    return tuple(ints) if len(ints) == 4 else None


def set_guid(node, guid):
    """Overwrite those four IntConsts in place."""
    ints = []

    def collect(n):
        for kind, val in n.ops:
            if kind == 'expr':
                if val.name == 'StructConst':
                    collect(val)
                elif val.name == 'IntConst':
                    ints.append(val)
    collect(node)
    if len(ints) != 4:
        return False
    for n, v in zip(ints, guid):
        n.ops[0] = ('i32', v)
    return True


def spawn_calls(walker):
    """Every spawn call, with its proto node and its literal arguments."""
    found = []
    for node in [n for top in walker.nodes for n in top.walk()]:
        if node.name not in ('FinalFunction', 'LocalFinalFunction'):
            continue
        children = [v for k, v in node.ops if k == 'expr']
        if not children:
            continue
        proto = next((c for c in children if c.name == 'StructConst'), None)
        if proto is None:
            continue
        g = guid_of(proto)
        if g is None:
            continue
        literals = [c for c in children
                    if c.name in ('IntConst', 'IntZero', 'IntOne', 'True',
                                  'False', 'FloatConst', 'ByteConst')]
        found.append({'call': node, 'proto': proto, 'guid': g, 'args': literals})
    return found


def describe_args(args):
    out = []
    for a in args:
        if a.name == 'IntConst':
            out.append(str(a.ops[0][1]))
        elif a.name == 'FloatConst':
            out.append('%g' % a.ops[0][1])
        elif a.name == 'ByteConst':
            out.append('b%d' % a.ops[0][1])
        elif a.name == 'IntZero':
            out.append('0')
        elif a.name == 'IntOne':
            out.append('1')
        else:
            out.append(a.name.lower())
    return ' '.join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--bp', required=True)
    ap.add_argument('--swap', action='append', default=[],
                    metavar='FROM=TO', help='replace one unit with another')
    ap.add_argument('--out', help='write the patched .uasset/.uexp here')
    args = ap.parse_args()

    protos = onslaught.protos()
    byguid = dict((v, k) for k, v in protos.items())

    pkg, names, resolve = load(args.bp)
    swaps = {}
    for s in args.swap:
        a, _, b = s.partition('=')
        if a not in protos or b not in protos:
            raise SystemExit('unknown unit in --swap %s (see onslaught.py --protos)' % s)
        swaps[protos[a]] = (b, protos[b])

    total, changed = 0, 0
    for e in pkg.exports:
        if resolve(struct.unpack_from('<i', e['meta'], 0)[0]) != 'Function':
            continue
        found = find_bytecode(e['data'], names, resolve)
        if not found:
            continue
        off, mem, size, _ = found
        code = e['data'][off + 8:off + 8 + size]
        w = Walker(code, names, resolve).run()
        calls = spawn_calls(w)
        if not calls:
            continue
        fname = names[struct.unpack_from('<I', e['meta'], 16)[0]]
        print('\n%s  (%d spawn call%s)' % (fname, len(calls), '' if len(calls) == 1 else 's'))
        for c in calls:
            total += 1
            unit = byguid.get(c['guid'], 'UNKNOWN %s' % (c['guid'],))
            note = ''
            if c['guid'] in swaps:
                newname, newguid = swaps[c['guid']]
                if set_guid(c['proto'], newguid):
                    changed += 1
                    note = '   -> %s' % newname
            print('   %-22s args: %s%s' % (unit, describe_args(c['args']), note))

        if changed and args.out:
            rebuilt = emit(w.nodes)
            delta = len(rebuilt) - size
            data = bytearray(e['data'])
            data[off + 8:off + 8 + size] = rebuilt
            # Only constants changed, so the pointer count is unchanged and the
            # in-memory size moves by the same delta as the serialised size.
            struct.pack_into('<ii', data, off, mem + delta, size + delta)
            e['data'] = bytes(data)

    print('\n%d spawn call(s); %d unit(s) swapped' % (total, changed))

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        ua, ux = pkg.write()
        base = os.path.join(args.out, args.bp)
        with open(base + '.uasset', 'wb') as fh:
            fh.write(ua)
        with open(base + '.uexp', 'wb') as fh:
            fh.write(ux)
        print('wrote %s.uasset (%d) and .uexp (%d)' % (base, len(ua), len(ux)))
        # Re-read what was written: a patch that cannot be parsed back is a bug.
        check = Package(ua, ux)
        print('re-parsed: %d exports, %d names' % (len(check.exports), len(check.names)))


if __name__ == '__main__':
    main()


def _varname(node, names):
    """The property name behind a LocalVariable node."""
    # ops are (i32 count, u32 nameIdx, u32 number, ..., i32 owner)
    for i, (kind, val) in enumerate(node.ops):
        if kind == 'u32':
            return names[val] if val < len(names) else '?'
    return '?'


def _flat(node, out=None):
    out = [] if out is None else out
    out.append(node)
    for kind, val in node.ops:
        if kind == 'expr':
            _flat(val, out)
    return out


def entry_points(pkg, names, resolve):
    """Ubergraph entry offsets, read from each event stub.

    An event like Onslaught_Start compiles to a tiny function that writes its
    arguments into the persistent frame and then calls ExecuteUbergraph with a
    literal offset. That offset -- not the first LetBool in the file -- is where
    the dispatch ladder for that event begins.
    """
    from riftlord.formats.ue.kismet import find_bytecode, Walker
    out = {}
    for e in pkg.exports:
        if resolve(struct.unpack_from('<i', e['meta'], 0)[0]) != 'Function':
            continue
        name = names[struct.unpack_from('<I', e['meta'], 16)[0]]
        if name.startswith('ExecuteUbergraph'):
            continue
        found = find_bytecode(e['data'], names, resolve)
        if not found:
            continue
        _off, _mem, _size, w = found
        for top in w.nodes:
            for n in _flat(top):
                if n.name not in ('LocalFinalFunction', 'FinalFunction'):
                    continue
                if not str(resolve(n.ops[0][1])).startswith('ExecuteUbergraph'):
                    continue
                consts = [x.ops[0][1] for x in _flat(n) if x.name == 'IntConst']
                if consts:
                    out[name] = consts[0]
    return out


def wave_table(walker, names, resolve, entry=None):
    """Reconstruct (wave, spawner) -> spawn calls from the dispatch ladders.

    The Ubergraph opens with ComputedJump EntryPoint; each event enters at its own
    offset, where ladders of

        LetBool <t> = NotEqual_IntInt(var, N)
        JumpIfNot -> target

    sort on WaveNumber and SpawnerIndex. JumpIfNot branches when its condition is
    FALSE and the condition is a NOT-equal, so the jump target is the MATCH and
    the fall-through is the next test.

    Ladders nest in either order, and how deeply is not fixed, so this walks them
    recursively and reads which variable each one tests rather than assuming.
    """
    from riftlord.formats.ue.kismet import memory_map
    mm = memory_map(walker.nodes)
    by_mem = dict((m, (i, n)) for i, (m, s, n) in enumerate(mm))

    def ladder(start):
        """[(constant, match_target, variable)] for the chain beginning here."""
        out = []
        pos = start
        seen = set()
        while pos in by_mem and pos not in seen:
            seen.add(pos)
            i, n = by_mem[pos]
            if n.name != 'LetBool':
                break
            calls = [x for x in _flat(n) if x.name == 'CallMath']
            if not calls or resolve(calls[0].ops[0][1]) != 'NotEqual_IntInt':
                break
            consts = [x.ops[0][1] for x in _flat(calls[0]) if x.name == 'IntConst']
            var = next((_varname(x, names) for x in _flat(calls[0])
                        if x.name == 'LocalVariable'), '?')
            nxt = mm[i + 1][2] if i + 1 < len(mm) else None
            if nxt is None or nxt.name != 'JumpIfNot':
                break
            out.append((consts[0] if consts else None, nxt.ops[0][1], var))
            pos = mm[i + 2][0] if i + 2 < len(mm) else None
        return out

    def block_calls(start, other_starts):
        """Every spawn call reachable from a block.

        PopExecutionFlow is a RETURN, not an end: a block typically pushes a
        continuation, runs some calls, then pops back to it. Stopping at the first
        pop loses most of the wave -- Scenario_01 reported 27 of its 121 calls
        that way. So this follows the flow instead: Jump, both sides of JumpIfNot,
        and the pushed continuation, refusing to enter another block's entry so
        one wave cannot absorb the next one's calls.
        """
        found, visited = [], set()
        stack = []
        work = [(start, tuple())]
        while work:
            pos, saved = work.pop()
            stack = list(saved)
            while pos in by_mem and pos not in visited:
                visited.add(pos)
                i, n = by_mem[pos]
                if n.name in ('Return', 'EndOfScript', 'ComputedJump'):
                    break
                if n.name == 'PushExecutionFlow':
                    stack.append(n.ops[0][1])
                elif n.name == 'PopExecutionFlow':
                    if not stack:
                        break
                    pos = stack.pop()
                    if pos in other_starts:
                        break
                    continue
                elif n.name == 'Jump':
                    pos = n.ops[0][1]
                    if pos in other_starts:
                        break
                    continue
                elif n.name == 'JumpIfNot':
                    tgt = n.ops[0][1]
                    if tgt not in other_starts:
                        work.append((tgt, tuple(stack)))
                else:
                    found.extend(spawn_calls_in(n))
                nxt = mm[i + 1][0] if i + 1 < len(mm) else None
                if nxt is None or nxt in other_starts:
                    break
                pos = nxt
        return found

    # Two passes: find every block start first, so call collection can refuse to
    # cross from one wave's block into another's.
    table = {}
    leaves = []

    def descend(start, bound, depth=0):
        rungs = ladder(start)
        if not rungs or depth > 4:
            key = (bound.get('wave'), bound.get('spawner'))
            if key != (None, None):
                leaves.append((key, start))
            return
        for const, target, var in rungs:
            nb = dict(bound)
            nb['wave' if 'Wave' in var else 'spawner'] = const
            descend(target, nb, depth + 1)

    starts = [entry] if entry is not None else \
        [m for m, s, n in mm if n.name == 'LetBool']
    for st in starts:
        descend(st, {})

    # Collect only once every block start is known, so a block can refuse to run
    # on into the next wave's entry.
    other = set(b for _k, b in leaves)
    for key, block in leaves:
        if key in table:
            continue
        table[key] = {'block_mem': block,
                      'calls': block_calls(block, other - {block})}
    return table


#: A difficulty the test is not being played on, used to silence a spawn call
#: without removing it. Byte 2 and 4 calls were repeatedly absent on War Mage.
#: This is a difficulty selector, not a disable flag -- the call still fires on
#: whichever difficulty the value names.
OTHER_DIFFICULTY = 4

#: Difficulty selector on an Onslaught call. 5 is the value every
#: OnslaughtWaveBreak, OnslaughtGoBreak and nearly every OnslaughtGiveCoin
#: carries, so it is the one that applies on every difficulty; a spawn left on
#: another value is invisible unless that difficulty is being played.
ALL_DIFFICULTIES = 5


def calls_named(node, fn_name, resolve):
    """Calls to a named function inside one statement."""
    out = []
    for n in node.walk():
        if n.name in ('FinalFunction', 'LocalFinalFunction'):
            if str(resolve(n.ops[0][1])) == fn_name:
                out.append(n)
    return out


def drop_statements(nodes, statements, resolve, fn_name):
    """Remove statements whose only Onslaught call is `fn_name`.

    A statement that something jumps to is left alone -- removing it would send
    that jump to the next survivor, which is a silent change of meaning. Both
    kinds of jump target count: a memory offset, and a direct Node reference
    left by clone_block for a statement that has no offset yet. Returns the
    statements actually removed.
    """
    from riftlord.formats.ue.kismet import code_offsets, Node
    targets, referenced = set(), set()
    for node, i in code_offsets(nodes):
        v = node.ops[i][1]
        if isinstance(v, Node):
            referenced.add(id(v))
        elif isinstance(v, int):
            targets.add(v)
    keep, gone = [], []
    for mem, node in statements:
        onsl = [n for n in node.walk()
                if n.name in ('FinalFunction', 'LocalFinalFunction')
                and str(resolve(n.ops[0][1])).startswith('Onslaught')]
        if len(onsl) == 1 and str(resolve(onsl[0].ops[0][1])) == fn_name \
                and mem not in targets and id(node) not in referenced:
            gone.append(node)
        else:
            keep.append(node)
    for node in gone:
        nodes.remove(node)
    return gone


def find_call_statement(mm, resolve, fn_name):
    """A statement index whose only Onslaught call is `fn_name`, to clone."""
    for i, (_m, _s, node) in enumerate(mm):
        onsl = [n for n in node.walk()
                if n.name in ('FinalFunction', 'LocalFinalFunction')
                and str(resolve(n.ops[0][1])).startswith('Onslaught')]
        if len(onsl) == 1 and str(resolve(onsl[0].ops[0][1])) == fn_name:
            return i
    return None


def clone_break(mm, resolve, fn_name, seconds=None,
                difficulty=ALL_DIFFICULTIES):
    """A copy of a break statement, retimed.

    A wave gains a break by running one before it spawns: the shipped waves
    carry theirs trailing, so appending a wave leaves the previously final wave
    with nothing after it and the new wave with no countdown to open on.
    """
    import copy
    i = find_call_statement(mm, resolve, fn_name)
    if i is None:
        return None
    node = copy.deepcopy(mm[i][2])
    for call in calls_named(node, fn_name, resolve):
        if seconds is not None:
            f = next((v for k, v in call.ops
                      if k == 'expr' and v.name == 'FloatConst'), None)
            if f is not None:
                f.ops[0] = (f.ops[0][0], float(seconds))
        b = next((v for k, v in call.ops
                  if k == 'expr' and v.name == 'ByteConst'), None)
        if b is not None:
            b.ops[0] = (b.ops[0][0], difficulty)
    return node


BREAK_FNS = ('OnslaughtWaveBreak', 'OnslaughtGoBreak')


def function_index(mm, resolve, fn_name):
    """The FPackageIndex a call to `fn_name` uses, or None if never called."""
    for _m, _s, node in mm:
        for call in calls_named(node, fn_name, resolve):
            return call.ops[0][1]
    return None


def wave_break(mm, block, others, resolve):
    """(statement index, call node, function name) of a wave's trailing break.

    A break is the last call of the wave it ends, so this reads the declared
    order rather than the layout. Returns None for the final wave, which has no
    break.
    """
    for i in reversed(block_sequence(mm, block, others)):
        for fn in BREAK_FNS:
            hits = calls_named(mm[i][2], fn, resolve)
            if hits:
                return i, hits[0], fn
    return None


def set_break_kind(mm, call, to_fn, resolve, seconds=10.0, index=None):
    """Turn a break call into the other kind, in place.

    The statement is usually a continuation target, so it is edited rather than
    replaced: swapping the node would send those jumps past it. GoBreak takes
    only a difficulty; WaveBreak takes a duration as well, so the difference is
    one argument.
    """
    idx = index if index is not None else function_index(mm, resolve, to_fn)
    if idx is None:
        raise SystemExit('%s is never called in this Blueprint, so there is no '
                         'import to point at' % to_fn)
    floats = [k for k, (kind, v) in enumerate(call.ops)
              if kind == 'expr' and v.name == 'FloatConst']
    call.ops[0] = (call.ops[0][0], idx)
    if to_fn == 'OnslaughtGoBreak':
        for k in reversed(floats):
            del call.ops[k]
    elif not floats:
        donor = None
        for _m, _s, node in mm:
            for other in calls_named(node, 'OnslaughtWaveBreak', resolve):
                donor = next((v for kind, v in other.ops
                              if kind == 'expr' and v.name == 'FloatConst'), None)
                if donor is not None:
                    break
            if donor is not None:
                break
        if donor is None:
            # a coordinator that never calls WaveBreak has no duration to copy
            # from one, but StartGroup, Delay and every spawn carry a float
            for _m, _s, node in mm:
                donor = next((x for x in _flat(node) if x.name == 'FloatConst'),
                             None)
                if donor is not None:
                    break
        if donor is None:
            raise SystemExit('no FloatConst to copy for a WaveBreak duration')
        import copy
        fresh = copy.deepcopy(donor)
        fresh.ops[0] = (fresh.ops[0][0], float(seconds))
        call.ops.insert(1, ('expr', fresh))
    return to_fn


def set_break(nodes, resolve, fn_name, seconds):
    """Set the duration on every call to a break function. Returns the count.

    OnslaughtWaveBreak(seconds, difficulty) is the pause between waves and
    OnslaughtGoBreak(difficulty) the build phase. A wave cloned from an early
    donor inherits its break, which would run a countdown after the last wave
    and before the onslaught closes.
    """
    n = 0
    for node in nodes:
        for call in calls_named(node, fn_name, resolve):
            f = next((v for k, v in call.ops
                      if k == 'expr' and v.name == 'FloatConst'), None)
            if f is not None:
                f.ops[0] = (f.ops[0][0], float(seconds))
                n += 1
    return n


def spawn_parts(call):
    """(proto, count_node, difficulty_node, promote_node) for a spawn call.

    The real signature, from the game's own reflection data, is
    OnslaughtSpawnEntities(OMDAICharacterProto, count, bool SpawnWithShield,
    int32 EndlessAddCount, float EndlessPromotePercent, EOnslaughtDifficulty).
    The float is the endless promotion chance, NOT a delay or a spawn rate --
    which is why a count of one still carries 0.5 in shipped waves.
    """
    kids = [v for k, v in call.ops if k == 'expr']
    proto = next((c for c in kids if c.name == 'StructConst'), None)
    # Scenario_Ice_01 names its units through Blueprint variables, so the first
    # argument is an InstanceVariable and there is no struct literal; the rest
    # of the signature is unchanged, so the remaining arguments still follow it
    after = kids[(kids.index(proto) if proto is not None else 0) + 1:]
    count = next((c for c in after
                  if c.name in ('IntConst', 'IntZero', 'IntOne')), None)
    diff = next((c for c in reversed(kids) if c.name == 'ByteConst'), None)
    delay = next((c for c in after if c.name == 'FloatConst'), None)
    return proto, count, diff, delay


def set_spawn(call, guid=None, count=None, difficulty=None, int_donor=None,
              promote=None):
    """Retarget one spawn call. Returns what was actually changed.

    A count stored as the compact IntZero/IntOne opcode cannot hold another
    value, so `int_donor` -- any IntConst node to copy -- is used in its place.
    """
    import copy
    proto, count_node, diff_node, promote_node = spawn_parts(call)
    done = []
    if guid is not None and proto is not None and set_guid(proto, guid):
        done.append('unit')
    if count is not None and count_node is not None:
        if count_node.name == 'IntConst':
            count_node.ops[0] = (count_node.ops[0][0], count)
            done.append('count')
        elif int_donor is not None:
            fresh = copy.deepcopy(int_donor)
            fresh.ops[0] = (fresh.ops[0][0], count)
            kids = [i for i, (k, v) in enumerate(call.ops)
                    if k == 'expr' and v is count_node]
            if kids:
                call.ops[kids[0]] = ('expr', fresh)
                done.append('count')
    if difficulty is not None and diff_node is not None:
        diff_node.ops[0] = (diff_node.ops[0][0], difficulty)
        done.append('difficulty')
    if promote is not None and promote_node is not None:
        promote_node.ops[0] = (promote_node.ops[0][0], float(promote))
        done.append('promote')
    return done


def spawn_calls_in(node):
    """Spawn calls contained in one statement."""
    out = []
    for n in _flat(node):
        if n.name not in ('FinalFunction', 'LocalFinalFunction'):
            continue
        kids = [v for k, v in n.ops if k == 'expr']
        proto = next((c for c in kids if c.name == 'StructConst'), None)
        if proto is None:
            continue
        g = guid_of(proto)
        if g is None:
            continue
        lits = [c for c in kids if c.name in ('IntConst', 'IntZero', 'IntOne',
                                              'True', 'False', 'FloatConst',
                                              'ByteConst')]
        out.append({'call': n, 'proto': proto, 'guid': g, 'args': lits})
    return out
