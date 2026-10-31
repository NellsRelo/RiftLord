"""Turn an authored spec into a coordinator, and that into a mod pak.

The spec is what the editor posts: waves in play order, each holding its spawns
keyed by spawner index, how it ends, and the events it fires. This module maps
that onto the coordinator model -- a break terminates a wave, every call is
gated by a difficulty byte, and the highest rung closes the onslaught -- and
drives `bppatch` to write it.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

from riftlord.onslaught import bppatch  # noqa: E402
from riftlord.build import makelevel  # noqa: E402
from riftlord.build.minimap import mapdata  # noqa: E402
from riftlord.build.navigation import navpatch  # noqa: E402
from riftlord.onslaught import onslaught  # noqa: E402
from riftlord.formats.ue.kismet import find_bytecode, memory_map  # noqa: E402

#: EOnslaughtDifficulty, from the game's reflection data. 5 is Any, which is
#: why it is the value that applies whatever is being played.
DIFFICULTY = {0: 'Apprentice', 1: 'War Mage', 2: 'Rift Lord', 3: 'Co-op',
              4: 'Endless', 5: 'Any'}

ALL = bppatch.ALL_DIFFICULTIES

#: EOnslaughtDifficulty.Endless: skipped on the first Endless loop, played from the second.
ENDLESS = 4

#: The bytes that play on the first Endless loop of a solo game.
LOOP_ONE = (ALL,)

#: Opcodes an int argument of a spawn call is written with.
INT_OPS = ('IntConst', 'IntZero', 'IntOne')


def ubergraph(pkg, names, resolve):
    """(walker, memory map) for a coordinator's ubergraph."""
    export = next(x for x, n in bppatch.functions(pkg, names, resolve)
                  if n.startswith('ExecuteUbergraph'))
    _o, _m, _s, walker = find_bytecode(export['data'], names, resolve)
    return walker, memory_map(walker.nodes)


#: Calls whose presence identifies what a wave is.
BREAK_FNS = ('OnslaughtWaveBreak', 'OnslaughtGoBreak')

#: Calls that close a wave's declaration; a spawn added to the wave goes before them.
WAVE_ENDS = BREAK_FNS + ('OnslaughtGiveCoin',)
#: Calls dropped from a host wave past the last authored one, besides its spawns.
LATER_WAVE_CALLS = WAVE_ENDS + ('OnslaughtAction',)

#: No shipped coordinator declares anywhere near this many waves.
WAVE_LIMIT = 24

#: Spec key buildjob.save fills with the mission's pictures from its Missions record:
#: {'picture': {'path', 'crop'} or None, 'portalPicture': ...}. Never kept in a record.
PICTURES_KEY = '_pictures'

#: The spec's mission settings (`spec['settings']`): spec key -> (coordinator class
#: default property, kind, native default from AOMDOnslaughtCoordinator's constructor,
#: lowest, highest). Written into the mission coordinator's class default object; a
#: setting the spec leaves out keeps the source coordinator's own value.
SETTINGS = {
    'startingCoin': ('DefaultStartingCoin', 'IntProperty', 2000, 0, 999999),
    'riftPoints': ('RiftPoints', 'IntProperty', 20, 1, 999),
    'maxEnemies': ('maxAICharacters', 'IntProperty', 10000, 1, 10000),
    'spawnInterval': ('DefaultEntityTimer', 'FloatProperty', 1.0, 0.1, 60.0),
    'groupDelay': ('DefaultGroupTimer', 'FloatProperty', 6.0, 0.0, 600.0),
    'waveDelay': ('DefaultWaveTimer', 'FloatProperty', 10.0, 0.0, 600.0),
    'postBreakDelay': ('DefaultPostGoBreakTimer', 'FloatProperty', 3.0, 0.0, 600.0),
}


def clean_settings(raw):
    """The spec's mission settings, validated: {key: number}, unknown keys dropped."""
    out = {}
    for key, (_prop, kind, _default, lo, hi) in SETTINGS.items():
        value = (raw or {}).get(key)
        if value is None or value == '':
            continue
        try:
            value = int(value) if kind == 'IntProperty' else float(value)
        except (TypeError, ValueError):
            raise SystemExit('mission setting %s is not a number: %r' % (key, value))
        if not lo <= value <= hi:
            raise SystemExit('mission setting %s must be between %g and %g' % (key, lo, hi))
        out[key] = value
    return out


def coordinator_settings(pkg, names, cdo_index):
    """{spec key: value} the class default object sets, the native default for the rest."""
    import struct
    data = bytes(pkg.exports[cdo_index]['data'])
    have = {}
    for name, kind, at, _end in _cdo_tags(data, names):
        have[name] = struct.unpack_from('<i' if kind == 'IntProperty' else '<f', data, at + 25)[0]
    return {key: have.get(prop, default) for key, (prop, _k, default, _lo, _hi)
            in SETTINGS.items()}


def _cdo_tags(data, names):
    """[(name, kind, start, end)] of a class default object's top-level tags."""
    import struct
    out, o = [], 0
    while o + 24 <= len(data):
        ni = struct.unpack_from('<i', data, o)[0]
        if not 0 <= ni < len(names) or names[ni] == 'None':
            break
        kind = names[struct.unpack_from('<i', data, o + 8)[0]]
        size = struct.unpack_from('<i', data, o + 16)[0]
        head = {'BoolProperty': 26, 'StructProperty': 49, 'ArrayProperty': 33,
                'ByteProperty': 33, 'EnumProperty': 33}.get(kind, 25)
        if kind == 'BoolProperty':
            size = 0
        out.append((names[ni], kind, o, o + head + size))
        o += head + size
    return out


def write_settings(pkg, names, cdo_index, settings, note=lambda _s: None):
    """Write the spec's mission settings into the class default object: an existing tag
    in place, a missing one added before the None that ends the tags. `names` (the
    caller's list of the package's names) is kept in step when a name is added."""
    import struct
    if not settings:
        return

    def index_of(text):
        if text not in names:
            pkg.add_name(text)
            names[:] = [n for n, _h in pkg.names]
        return names.index(text)

    data = bytearray(pkg.exports[cdo_index]['data'])
    for key, value in settings.items():
        prop, kind, _default, _lo, _hi = SETTINGS[key]
        payload = struct.pack('<i' if kind == 'IntProperty' else '<f', value)
        tags = _cdo_tags(bytes(data), names)
        hit = next((t for t in tags if t[0] == prop), None)
        if hit:
            data[hit[2] + 25:hit[3]] = payload
        else:
            at = tags[-1][3] if tags else 0
            tag = (struct.pack('<ii', index_of(prop), 0) + struct.pack('<ii', index_of(kind), 0)
                   + struct.pack('<ii', len(payload), 0) + b'\0' + payload)
            data[at:at] = tag
        note('mission setting %s = %s' % (prop, value))
    pkg.exports[cdo_index]['data'] = bytes(data)
    pkg.exports[cdo_index]['size'] = len(data)


def wave_calls(mm, entry, names, resolve, wave, spawner):
    """{call name: count} for one wave through one spawner, in declared order.

    `declaration` walks the whole flow with the parameters bound, which is what
    catches a wave prefixed by an action hook that pops back into the ladder --
    Scenario_09's fifth wave spawns Ghorbash that way and would otherwise read
    as empty.
    """
    want = ('OnslaughtSpawnEntities', 'OnslaughtEnd', 'OnslaughtGiveCoin',
            'OnslaughtAction') + BREAK_FNS
    out = {}
    for i in bppatch.declaration(mm, entry, {'Wave': wave, 'Spawner': spawner},
                                 names, resolve, branch_unknown=True):
        for fn in want:
            n = len(bppatch.calls_named(mm[i][2], fn, resolve))
            if n:
                out[fn] = out.get(fn, 0) + n
    return out


def shape(bp_name):
    """What a coordinator offers: its live waves, spawners and close wave.

    The close is the first wave that calls `OnslaughtEnd`, read from the
    declaration rather than from the wave ladder's highest rung. Coordinators
    interleave their wave and spawner tests -- Scenario_09's ladder opens on
    `WaveNumber == 5`, which is its Ghorbash wave, not its terminator -- so the
    rung number is 5 where the onslaught actually closes at 6.
    """
    pkg, names, resolve = bppatch.load(bp_name)
    walker, mm = ubergraph(pkg, names, resolve)
    eps = bppatch.entry_points(pkg, names, resolve)
    entry = eps['Onslaught_Start']
    wmap, _seen = bppatch.wave_map(mm, entry, names, resolve)
    base = min(s for _w, s in wmap)

    close, breaks = None, {}
    for wave in range(1, WAVE_LIMIT + 1):
        calls = wave_calls(mm, entry, names, resolve, wave, base)
        if calls.get('OnslaughtEnd'):
            close = wave
            break
        kind = next((f for f in BREAK_FNS if calls.get(f)), None)
        breaks[wave] = kind
    if close is None:
        raise SystemExit('%s never closes: no OnslaughtEnd within %d waves'
                         % (bp_name, WAVE_LIMIT))

    # capacity from the declaration, not the ladder's block: an action-prefixed
    # wave declares its spawns outside the block entirely
    capacity = {}
    for wave in range(1, close):
        for spawner in sorted({s for _w, s in wmap}):
            n = len(spawn_statements(
                mm, declared(mm, entry, names, resolve, wave, spawner), resolve))
            if n:
                capacity.setdefault(wave, {})[spawner] = n
    # a coordinator that names its units through Blueprint variables can field
    # only as many distinct units as it declares variables
    vars_, cdo_index = proto_vars(pkg, names, resolve)
    return {'name': bp_name, 'close': close, 'base_spawner': base,
            'waves': list(range(1, close)), 'breaks': breaks,
            'spawners': sorted({s for v in capacity.values() for s in v}),
            'capacity': capacity,
            'unit_style': 'variable' if (vars_ and cdo_index is not None)
                          else 'literal',
            'max_units': len(vars_) if (vars_ and cdo_index is not None) else None}


def declared(mm, entry, names, resolve, wave, spawner):
    """Statement indices a wave declares through one spawner, in order."""
    return bppatch.declaration(mm, entry, {'Wave': wave, 'Spawner': spawner},
                               names, resolve, branch_unknown=True)


def spawn_calls(node, resolve):
    """Every spawn call in a statement, whichever way it names its unit.

    `bppatch.spawn_calls_in` requires an inline proto struct, so it finds none
    at all in a coordinator that uses variables.
    """
    return [{'call': c} for c in
            bppatch.calls_named(node, 'OnslaughtSpawnEntities', resolve)]


def spawn_statements(mm, indices, resolve=None):
    """[(statement index, call)] over a declaration, in declared order.

    Read from the DECLARATION, not from the wave ladder's block: a wave can be
    prefixed by an action hook that pops back into the ladder, and then its
    spawns are not in the block at all. Scenario_05's fifth wave and
    Scenario_09's both declare more than twenty spawns whose block reads as
    empty, and Scenario_Ice_01 reads as having no spawners whatsoever.

    Each spawn call is its own statement in every shipped coordinator, which is
    what makes one addable and removable at a time.
    """
    out = []
    for i in indices:
        got = (spawn_calls(mm[i][2], resolve) if resolve is not None
               else bppatch.spawn_calls_in(mm[i][2]))
        for call in got:
            out.append((i, call))
    return out


def drop(nodes, node):
    """Remove one statement by identity. list.remove would match by value."""
    for j, n in enumerate(nodes):
        if n is node:
            del nodes[j]
            return True
    return False


def insert_before(nodes, anchor, fresh):
    """Put `fresh` straight before `anchor`, by identity.

    A break goes before the statement its wave's declaration ends on, not
    after: appended past the end it lands outside the wave, which reads as an
    empty extra wave in game.
    """
    for j, n in enumerate(nodes):
        if n is anchor:
            nodes.insert(j, fresh)
            return True
    raise SystemExit('the statement to insert before is no longer in the '
                     'function; appending past EndOfScript would emit code '
                     'the game never reaches')


def insert_after(nodes, anchor, fresh):
    """Put `fresh` straight after `anchor`, by identity."""
    for j, n in enumerate(nodes):
        if n is anchor:
            nodes.insert(j + 1, fresh)
            return True
    raise SystemExit('the statement to insert after is no longer in the '
                     'function; appending past EndOfScript would emit code '
                     'the game never reaches')


def break_import(pkg, names, fn_name):
    """FPackageIndex for a break function, adding the import if it is missing.

    Half the coordinators use only one kind of break and import only that one,
    so a map like Scenario_09 or Scenario_18 has nothing to point a WaveBreak
    at. Every Onslaught call is imported the same way -- a Function under the
    OMDOnslaughtCoordinator class -- so a sibling's record gives the class and
    outer and only the name differs.
    """
    import struct
    for k, imp in enumerate(pkg.imports):
        if names[struct.unpack_from('<I', imp, 20)[0]] == fn_name:
            return -(k + 1)
    for imp in pkg.imports:
        nm = names[struct.unpack_from('<I', imp, 20)[0]]
        if nm.startswith('Onslaught') and nm != 'Onslaught_Start':
            cp = names[struct.unpack_from('<I', imp, 0)[0]]
            cn = names[struct.unpack_from('<I', imp, 8)[0]]
            outer = struct.unpack_from('<i', imp, 16)[0]
            idx = pkg.add_import(cp, cn, outer, fn_name)
            # resolve() closes over this list, so it must stay in step
            names[:] = [n for n, _h in pkg.names]
            return idx
    return None


def action_strings(node):
    """Every string literal in a statement, which is how an action is named."""
    out = []
    for n in bppatch._flat(node):
        if n.name in ('StringConst', 'UnicodeStringConst'):
            out += [v for _k, v in n.ops if isinstance(v, str)]
    return out


def action_statements(mm, indices, resolve):
    """[(statement index, [names])] for the OnslaughtAction calls a wave makes."""
    out = []
    for i in indices:
        if bppatch.calls_named(mm[i][2], 'OnslaughtAction', resolve):
            out.append((i, action_strings(mm[i][2])))
    return out


def clone_group(mm, resolve, percent):
    """A copy of an OnslaughtStartGroup, retimed.

    Its float is `waitUntilPreviousGroupDeadPercent`: the group waits until
    that share of the group before it is dead. That is how the shipped waves
    pace one spawner -- Scenario_05 opens groups on 50 and 20.
    """
    import copy
    i = bppatch.find_call_statement(mm, resolve, 'OnslaughtStartGroup')
    if i is None:
        return None
    node = copy.deepcopy(mm[i][2])
    for call in bppatch.calls_named(node, 'OnslaughtStartGroup', resolve):
        f = next((v for k, v in call.ops
                  if k == 'expr' and v.name == 'FloatConst'), None)
        if f is not None:
            f.ops[0] = (f.ops[0][0], float(percent))
    return node


def clone_delay(mm, resolve, seconds, difficulty=ALL):
    """A copy of an OnslaughtDelay statement, retimed.

    `OnslaughtDelay(Delay, Difficulty)` is the call that actually waits.
    """
    import copy
    i = bppatch.find_call_statement(mm, resolve, 'OnslaughtDelay')
    if i is None:
        return None
    donor_float = None
    for _m, _s, other in mm:
        donor_float = next((x for x in bppatch._flat(other)
                            if x.name == 'FloatConst'), None)
        if donor_float is not None:
            break
    node = copy.deepcopy(mm[i][2])
    for call in bppatch.calls_named(node, 'OnslaughtDelay', resolve):
        args = [k for k, (kind, v) in enumerate(call.ops) if kind == 'expr']
        f = next((v for k, v in call.ops
                  if k == 'expr' and v.name == 'FloatConst'), None)
        if f is not None:
            f.ops[0] = (f.ops[0][0], float(seconds))
        elif donor_float is not None and args:
            # this coordinator reads its delay from a Blueprint variable, so
            # the operand is replaced with a literal we can actually set
            fresh = copy.deepcopy(donor_float)
            fresh.ops[0] = (fresh.ops[0][0], float(seconds))
            call.ops[args[0]] = ('expr', fresh)
        b = next((v for k, v in call.ops
                  if k == 'expr' and v.name == 'ByteConst'), None)
        if b is not None:
            b.ops[0] = (b.ops[0][0], difficulty)
    return node


def clone_action(mm, resolve, name, difficulty=ALL):
    """A copy of an OnslaughtAction statement, renamed to fire `name`.

    The handler is a Blueprint event, so only a name the coordinator already
    fires can be used -- which is why the editor offers a map only its own.
    """
    import copy
    i = bppatch.find_call_statement(mm, resolve, 'OnslaughtAction')
    if i is None:
        return None
    node = copy.deepcopy(mm[i][2])
    for n in bppatch._flat(node):
        if n.name in ('StringConst', 'UnicodeStringConst'):
            for k, (kind, v) in enumerate(n.ops):
                if isinstance(v, str):
                    n.ops[k] = (kind, name)
    for call in bppatch.calls_named(node, 'OnslaughtAction', resolve):
        b = next((v for k, v in call.ops
                  if k == 'expr' and v.name == 'ByteConst'), None)
        if b is not None:
            b.ops[0] = (b.ops[0][0], difficulty)
    return node


def row_count(row):
    """The count a row writes. Zero only with an EndlessAddCount, which makes it a row
    that joins as Endless escalates; any other count below one writes one."""
    n = int(row.get('count') or 0)
    if n < 1 and not int(row.get('addCount') or 0):
        return 1
    return max(0, n)


def add_count_node(call):
    """A spawn call's EndlessAddCount argument: the int after SpawnWithShield."""
    _p, count, _d, _f = bppatch.spawn_parts(call)
    kids = [v for k, v in call.ops if k == 'expr']
    at = next((i for i, v in enumerate(kids) if v is count), None)
    if at is None:
        return None
    return next((v for v in kids[at + 1:] if v.name in INT_OPS), None)


def read_add_count(call):
    """A spawn call's EndlessAddCount, or None when it has none to read."""
    node = add_count_node(call)
    if node is None:
        return None
    return node.ops[0][1] if node.name == 'IntConst' else int(node.name == 'IntOne')


def set_add_count(call, row, int_donor=None):
    """Write a row's `addCount` into a spawn call. A row without one leaves the call's
    own value. IntZero and IntOne hold no other value, so `int_donor` (an IntConst to
    copy) takes their place, as `bppatch.set_spawn` does for the count."""
    import copy
    want = row.get('addCount')
    node = add_count_node(call)
    if want is None or node is None:
        return False
    want = int(want)
    if node.name == 'IntConst':
        node.ops[0] = (node.ops[0][0], want)
        return True
    if (node.name == 'IntZero' and want == 0) or (node.name == 'IntOne' and want == 1):
        return False
    if int_donor is None:
        return False
    fresh = copy.deepcopy(int_donor)
    fresh.ops[0] = (fresh.ops[0][0], want)
    for i, (k, v) in enumerate(call.ops):
        if k == 'expr' and v is node:
            call.ops[i] = ('expr', fresh)
            return True
    return False


def copy_spawn(mm, node):
    """A standalone copy of a spawn statement, for a door a wave never used."""
    import copy
    if node is None:
        raise SystemExit('this coordinator has no spawn statement to copy')
    return copy.deepcopy(node)


def write_spawns(nodes, mm, indices, entries, protos, donor=None,
                 resolve=None, retarget_var=None):
    """Make a block spawn exactly `entries`, adding or dropping calls to fit.

    Every shipped coordinator gives each spawn call a statement of its own, so
    a surplus call is dropped outright rather than hidden behind a difficulty
    that does not apply -- hiding it would bring it back on that difficulty.

    `donor` is a spawn statement already inserted into `nodes` for a block that
    declares none; it becomes the first slot.
    """
    slots = [(mm[i][2], c['call']) for i, c in spawn_statements(mm, indices, resolve)]
    if not slots and donor is not None:
        calls = (spawn_calls(donor, resolve) if resolve is not None
                 else bppatch.spawn_calls_in(donor))
        if calls:
            slots = [(donor, calls[0]['call'])]
    int_call = next((call for _n, call in slots
                     if bppatch.spawn_parts(call)[1] is not None
                     and bppatch.spawn_parts(call)[1].name == 'IntConst'), None)
    int_donor = bppatch.spawn_parts(int_call)[1] if int_call is not None else None

    at_node = {}
    used = 0
    for k, want in enumerate(entries):
        args = dict(guid=None if retarget_var else protos[want['unit']],
                    count=row_count(want),
                    difficulty=int(want.get('difficulty', ALL)),
                    promote=float(want.get('promote') or 0.0),
                    int_donor=int_donor)
        if k < len(slots):
            bppatch.set_spawn(slots[k][1], **args)
            set_add_count(slots[k][1], want, int_donor)
            at_node[k] = slots[k][0]
            if retarget_var:
                retarget_var(slots[k][1], want['unit'])
        else:
            import copy
            if not slots:
                raise SystemExit(
                    'This door has no spawn to copy in this wave. Load the map\'s '
                    'original onslaught, or add the units to a door that already sends '
                    'enemies in this wave, then save again.')
            anchor = slots[-1][0]
            fresh = copy.deepcopy(anchor)
            insert_after(nodes, anchor, fresh)
            calls = (spawn_calls(fresh, resolve) if resolve is not None
                     else bppatch.spawn_calls_in(fresh))
            bppatch.set_spawn(calls[0]['call'], **args)
            set_add_count(calls[0]['call'], want, int_donor)
            at_node[k] = fresh
            if retarget_var:
                retarget_var(calls[0]['call'], want['unit'])
        used = k + 1

    dropped = 0
    for node, _c in slots[used:]:
        if drop(nodes, node):
            dropped += 1
    return {'set': min(used, len(slots)), 'added': max(0, used - len(slots)),
            'dropped': dropped, 'nodes': at_node}


def write_pause(nodes, mm, resolve, anchor, pause, ahead):
    """Put a pause beside a spawn statement.

    `pause` is {'kind': 'wait'|'gate', 'value': n}: a wait is a flat
    `OnslaughtDelay`, a gate an `OnslaughtStartGroup` that holds until that
    share of the group before it has died.
    """
    if not pause or not pause.get('value'):
        return False
    if pause.get('kind') == 'gate':
        node = clone_group(mm, resolve, float(pause['value']))
    else:
        node = clone_delay(mm, resolve, float(pause['value']))
    if node is None:
        return False
    if ahead:
        insert_before(nodes, anchor, node)
    else:
        insert_after(nodes, anchor, node)
    return True


def find_coin(mm, plan, wave, spawners, resolve):
    """(statement index, call) of a wave's coin reward, or None.

    One per wave, in whichever spawner's declaration carries the break -- the
    same place and the same once-per-wave shape, measured across the shipped
    coordinators.
    """
    for spawner in spawners:
        for i in plan.get((wave, spawner)) or []:
            hits = bppatch.calls_named(mm[i][2], 'OnslaughtGiveCoin', resolve)
            if hits:
                return i, hits[0]
    return None


def clone_coin(mm, resolve, amount, difficulty=ALL):
    """A copy of an OnslaughtGiveCoin statement, for a wave that has none."""
    import copy
    i = bppatch.find_call_statement(mm, resolve, 'OnslaughtGiveCoin')
    if i is None:
        return None
    node = copy.deepcopy(mm[i][2])
    for call in bppatch.calls_named(node, 'OnslaughtGiveCoin', resolve):
        n = next((v for k, v in call.ops
                  if k == 'expr' and v.name == 'IntConst'), None)
        if n is not None:
            n.ops[0] = (n.ops[0][0], int(amount))
        b = next((v for k, v in call.ops
                  if k == 'expr' and v.name == 'ByteConst'), None)
        if b is not None:
            b.ops[0] = (b.ops[0][0], difficulty)
    return node


def find_break(mm, plan, wave, spawners, resolve):
    """(spawner, statement index, call, function) of a wave's break, or None.

    The break can sit under any spawner -- Scenario_13 keeps waves 2 and 3 on
    spawner 2 while the rest are on spawner 1 -- so every one is searched, in
    declared order so the break found is the one that ends the wave.
    """
    for spawner in spawners:
        for i in reversed(plan.get((wave, spawner)) or []):
            for fn in BREAK_FNS:
                hits = bppatch.calls_named(mm[i][2], fn, resolve)
                if hits:
                    return spawner, i, hits[0], fn
    return None


#: What a spawner added to a level sends in each wave before anyone has authored an
#: onslaught for it. Modest on purpose: enough that the mission is playable as built,
#: while the Onslaught Editor is where the wave is actually designed.
ARM_UNIT = {'flyer': 'HellbatFire', 'ground': 'OrcLight'}

#: Spawns per wave in a generated arm.
ARM_PER_WAVE = 2


#: Statements that hand control somewhere else, so a cloned block already ends properly.
ENDS_FLOW = ('Jump', 'JumpIfNot', 'Return', 'PopExecutionFlow', 'EndOfScript',
             'ComputedJump')


def continuation_of(mm, indices):
    """The offset a donor block's flow continued at, or None.

    `clone_block` copies statements and rewires the jumps BETWEEN them, but a block that
    ended by **falling through** to the statement after it -- `PopExecutionFlow` then
    `Return`, which is how every shipped coordinator ends a wave's block -- has no jump to
    rewire. Relocated to the end of the function, the copy then falls into whatever happens
    to follow it there: the dispatch chain that sent the wave to it. That is an infinite
    loop, and the execution-flow stack grows with every pass.

    Measured: the game hangs on the loading screen with the CPU pinned and memory
    climbing, and `scratchpad/flowtrace.py` reproduces it as
    `wave 1 spawner 2 -> cycle`.
    """
    # The statement AFTER the block is not good enough. `declared()` follows one run of
    # the flow, so its last index can sit mid-block -- for Scenario_01's wave 2 the next
    # statement was still eight of that wave's own spawn calls, and the copy ran them: the
    # new spawner sent eight ground units down a flyers-only route. Scan forward to where
    # the block genuinely ENDS instead.
    last = max(indices)
    for j in range(last + 1, len(mm)):
        if mm[j][2].name in ('PopExecutionFlow', 'Return'):
            return mm[j][0]
    return None


def close_clone(nodes, mm, clones, at):
    """Give a cloned block the continuation its donor fell through to.

    Appended only when the copy does not already end by transferring control, so a block
    that ended in a jump is left alone.
    """
    if clones and clones[-1].name in ENDS_FLOW:
        return 0
    cont = continuation_of(mm, at)
    if cont is None:
        return 0
    nodes.insert(nodes.index(clones[-1]) + 1, bppatch.clone_jump(mm, cont))
    return 1


def add_spawner_arms(bp_name, wanted, protos, note=lambda _s: None, only_waves=None):
    """A mutate giving a coordinator an arm for each spawner in `wanted`.

    `only_waves` limits which waves the new spawner joins -- all the live ones by default.

    `wanted` is {spawner index: 'flyer' or 'ground'}. A coordinator branches on a spawner
    index, so a spawner the LEVEL gained has no code path in it at all -- its spawns are
    not dropped, they were never reachable. Each wave gets a clone of an existing
    spawner's block, pointed at the new index and given a starting roster, which is the
    same move `author` makes to open a door on a wave that never used one.
    """
    sh = shape(bp_name)

    def mutate(pkg, names, resolve):
        eps = bppatch.entry_points(pkg, names, resolve)
        entry = eps['Onslaught_Start']

        def edit(nodes, mm):
            head = None
            plan = {}
            for number in sh['waves'] + [sh['close']]:
                for spawner in sorted(set(sh['spawners']) | set(wanted)
                                      | {sh['base_spawner']}):
                    plan[(number, spawner)] = declared(
                        mm, entry, names, resolve, number, spawner)
            added = 0
            live = [w for w in sh['waves']
                    if only_waves is None or w in set(only_waves)]
            for number in live:
                for spawner, kind in sorted(wanted.items()):
                    if spawn_statements(mm, plan[(number, spawner)], resolve):
                        continue                    # it already has an arm here
                    src = next((plan[(number, other)] for other in sh['spawners']
                                if spawn_statements(
                                    mm, plan.get((number, other)) or [], resolve)),
                               None)
                    if src is None:
                        continue                    # nothing in this wave to copy
                    clones, block_entry = bppatch.clone_block(mm, src, mm[src[0]][0])
                    at = len(nodes) - 1
                    for k, c in enumerate(clones):
                        nodes.insert(at + k, c)
                    # ...and the terminator the donor fell through to, or the copy runs
                    # straight on into the chain that dispatched to it.
                    close_clone(nodes, mm, clones, src)
                    calls = [c for node in clones
                             for c in bppatch.spawn_calls_in(node)]
                    unit = ARM_UNIT.get(kind) or ARM_UNIT['ground']
                    entries = [{'unit': unit, 'count': 1, 'difficulty': ALL}
                               for _ in range(ARM_PER_WAVE)]
                    block_entry = _write_clone_spawns(
                        nodes, clones, calls, entries, protos, entry=block_entry)
                    clone_break_in(nodes, mm, clones, resolve,
                                   'OnslaughtWaveBreak', 10.0, keep=False)
                    if block_entry is None:
                        raise SystemExit(
                            'wave %d spawner %d: the copied block lost its entry '
                            'statement' % (number, spawner))
                    head = bppatch.append_wave(nodes, mm, names, resolve,
                                               head or entry, number, spawner,
                                               block_entry, entry)
                    added += 1
                    note('  wave %d spawner %d: arm added (%d %s)'
                         % (number, spawner, ARM_PER_WAVE, unit))
            # NO close chain is appended, and that is deliberate.
            #
            # `shape()` reads the SOURCE coordinator, which closes at 5; a renamed copy of
            # the very same asset reads as closing at **6** with five live waves. So a
            # close chain built from the source's number pointed wave 5 at the copy's
            # wave-5 SPAWN block and, worse, redirected wave 6 -- the copy's real
            # terminator -- at it too. An unreachable terminator is precisely what
            # `bppatch.terminator_rung` warns hangs the level on load, and it did:
            # one arm plus this chain hung the game while the same arms without it did
            # not.
            #
            # Nor is it needed. A spawner the coordinator never dispatches to does not
            # stall the enumeration -- measured in game: a level carrying a second
            # spawner with no arm at all loads and runs.
            note('  %d spawner arm(s) added' % added)
            return head

        bppatch.edit_ubergraph(pkg, names, resolve, edit)

    return mutate


def author(spec, sh, protos, note=lambda _s: None):
    """A mutate for `build_coordinator` that writes the spec into a coordinator.

    `sh` is the source coordinator's `shape`, read before the edit because the
    wave and close numbers are what the edit is expressed in.
    """
    waves = spec['waves']
    want = len(waves)
    live = sh['waves']
    # Every spawner the onslaught uses, not only those the coordinator already
    # declares. A spawner added to the level -- Rift Lord's flyer spawner is index 2 --
    # has no arm in a coordinator copied from a one-spawner map, and iterating only the
    # declared ones meant every spawn assigned to it was skipped in silence: the log
    # said "0 dropped" while verification found four spawns missing. The clone path
    # below writes the arm, the same way it opens a door on a wave that never used it.
    asked = {int(k) for wave in waves for k in (wave.get('spawns') or {})}
    spawners = sorted(set(sh['spawners']) | asked)
    settings = clean_settings(spec.get('settings'))

    def mutate(pkg, names, resolve):
        #: first, so the offsets read from the class default object below are current
        if settings:
            cdo_at, _cdo = class_default(pkg, names, resolve)
            if cdo_at is None:
                raise SystemExit('the coordinator has no class default object to take '
                                 'the mission settings')
            write_settings(pkg, names, cdo_at, settings, note)
        eps = bppatch.entry_points(pkg, names, resolve)
        entry = eps['Onslaught_Start']

        # A coordinator that names its units through Blueprint variables cannot
        # take a proto guid in the call. Each authored unit is given a variable
        # instead: one that already holds it where possible, otherwise a spare
        # whose default is rewritten in the class default object.
        vars_, cdo_index = proto_vars(pkg, names, resolve)
        retarget_var = None
        if vars_ and cdo_index is not None:
            vars_ = name_protos(vars_, pkg, cdo_index, protos)
            wanted = []
            for wave in waves:
                for _sp, rows in sorted((wave.get('spawns') or {}).items()):
                    for row in rows:
                        if row.get('unit') and row['unit'] not in wanted:
                            wanted.append(row['unit'])
            assign, taken = {}, set()
            for unit in wanted:
                hit = next((nm for nm, r in vars_.items()
                            if r['proto'] == unit and nm not in taken), None)
                if hit:
                    assign[unit] = hit
                    taken.add(hit)
            for unit in wanted:
                if unit in assign:
                    continue
                spare = next((nm for nm in vars_
                              if nm not in taken
                              and vars_[nm]['proto'] not in wanted), None)
                if spare is None:
                    raise SystemExit(
                        'this map names its units through %d Blueprint '
                        'variables, so it can field at most that many distinct '
                        'units; the onslaught asks for %d'
                        % (len(vars_), len(wanted)))
                set_var_proto(pkg, cdo_index, vars_[spare], protos[unit])
                note('  variable %s now holds %s' % (spare, unit))
                assign[unit] = spare
                taken.add(spare)

            def retarget_var(call, unit, _a=assign, _v=vars_):
                point_call_at(call, _v[_a[unit]]['name_index'])
            note('  %d units mapped onto Blueprint variables' % len(assign))
        # imports must exist before any statement can point at them
        want_kinds = {('OnslaughtGoBreak' if w.get('breakKind') == 'go'
                       else 'OnslaughtWaveBreak') for w in waves[:-1]}
        fn_index = {k: break_import(pkg, names, k) for k in want_kinds}

        def edit(nodes, mm):
            wmap, _seen = bppatch.wave_map(mm, entry, names, resolve)
            blocks = set(wmap.values())
            head = None

            # What each wave declares through each spawner, read once. This is
            # the truth about a wave; the ladder's block is not.
            # every spawner the ladder dispatches, including ones that spawn nothing
            # but hold a wave's break, coin or the close
            dispatched = sorted(set(spawners) | {sh['base_spawner']}
                                | {s for _w, s in wmap})
            plan = {}
            for number in live + [sh['close']]:
                for spawner in dispatched:
                    if (number, spawner) in plan:
                        continue
                    plan[(number, spawner)] = declared(mm, entry, names,
                                                       resolve, number, spawner)

            # A statement claimed by two waves cannot be edited for one of them
            # without changing the other, so the edit stops rather than
            # silently corrupting a wave the author did not touch.
            owner = {}
            for (number, spawner), idx in plan.items():
                if number >= sh['close']:
                    continue
                for i, _c in spawn_statements(mm, idx, resolve):
                    if i in owner and owner[i] != (number, spawner):
                        raise SystemExit(
                            'this coordinator shares spawn statement %d between '
                            'wave %d spawner %d and wave %d spawner %d, so its '
                            'waves cannot be authored independently'
                            % (i, owner[i][0], owner[i][1], number, spawner))
                    owner[i] = (number, spawner)

            # a spawn statement to copy when a wave sends nothing through a
            # door the author wants to use
            any_spawn = next((mm[i][2] for (w, sp), ix in sorted(plan.items())
                              if w < sh['close']
                              for i, _c in spawn_statements(mm, ix, resolve)), None)

            # door blocks copied in step 1, {wave number: [clones]}, not yet given a break
            opened = {}
            # {wave number: (break statement, clones)} for a break step 3 put in a copy
            break_home = {}
            # every door block copied in step 1, {wave number: [clones]}
            doors = {}

            def last_of(i):
                return i == want - 1

            # 1. spawns, into the waves the map already has
            for i, wave in enumerate(waves[:len(live)]):
                number = live[i]
                for spawner in spawners:
                    idx = plan.get((number, spawner)) or []
                    entries = [e for e in (wave.get('spawns') or {})
                               .get(str(spawner), []) if e.get('unit')]
                    if not idx and not entries:
                        continue
                    donor = None
                    if not spawn_statements(mm, idx, resolve) and entries:
                        # a spawn hangs off the wave's last statement here that is
                        # neither its break nor its coin, which end the wave
                        live_nodes = {id(n) for n in nodes}
                        # a statement other spawners also run (an action prefix) would
                        # send the spawn through every spawner
                        shared = {j for other in spawners if other != spawner
                                  for j in plan.get((number, other)) or []}
                        donor = next((mm[j][2] for j in reversed(idx)
                                      if id(mm[j][2]) in live_nodes and j not in shared
                                      and not any(bppatch.calls_named(mm[j][2], fn, resolve)
                                                  for fn in WAVE_ENDS)), None)
                        if donor is None:
                            # The map has no code path for this wave through
                            # this door at all -- a war scenario opens each
                            # door only on the waves that use it. One is added
                            # the same way an extra wave is, by a chain that
                            # pre-empts the ladder. Its closer rung is pointed
                            # back at the ladder, so it decides nothing.
                            src = next((plan[(number, other)]
                                        for other in spawners
                                        if spawn_statements(
                                            mm, plan.get((number, other)) or [],
                                            resolve)),
                                       None)
                            if src is None:
                                raise SystemExit(
                                    'wave %d sends nothing through any door in '
                                    'this map, so there is no block to copy'
                                    % (i + 1))
                            # clone_block keys its entry by memory offset, not
                            # by statement index
                            clones, block_entry = bppatch.clone_block(
                                mm, src, mm[src[0]][0])
                            at = len(nodes) - 1
                            for k, c in enumerate(clones):
                                nodes.insert(at + k, c)
                            # The donor block ended by falling through to its
                            # `PopExecutionFlow`; the copy must be given that or it runs
                            # on into the chain that dispatched to it and loops forever.
                            close_clone(nodes, mm, clones, src)
                            calls = [c for node in clones
                                     for c in bppatch.spawn_calls_in(node)]
                            block_entry = _write_clone_spawns(
                                nodes, clones, calls, entries, protos,
                                entry=block_entry)
                            # the copy's coin goes (step 3a places one per wave), as do
                            # events the author did not ask for
                            block_entry, _n = strip_clone_calls(
                                nodes, clones, resolve, 'OnslaughtGiveCoin',
                                block_entry)
                            want_actions = set(wave.get('actions') or [])
                            block_entry, _n = strip_clone_calls(
                                nodes, clones, resolve, 'OnslaughtAction', block_entry,
                                keep=lambda n, w=want_actions: any(
                                    a in w for a in action_strings(n)))
                            opened.setdefault(number, []).append(clones)
                            doors.setdefault(number, []).append(clones)
                            if block_entry is None:
                                raise SystemExit(
                                    'wave %d door %d: the copied block lost its '
                                    'entry statement' % (i + 1, spawner))
                            head = bppatch.append_wave(
                                nodes, mm, names, resolve, head or entry,
                                number, spawner, block_entry, entry)
                            note('  wave %d spawner %d: door opened (%d spawns)'
                                 % (i + 1, spawner, len(entries)))
                            continue
                        fresh = copy_spawn(mm, any_spawn)
                        insert_after(nodes, donor, fresh)
                        donor = fresh
                    got = write_spawns(nodes, mm, idx, entries, protos,
                                       donor=donor, resolve=resolve,
                                       retarget_var=retarget_var)
                    # pacing sits in front of the spawn it delays, so one
                    # spawner can be padded without touching the others
                    for k, paced in enumerate(entries):
                        anchor = got['nodes'].get(k)
                        if anchor is None:
                            continue
                        write_pause(nodes, mm, resolve, anchor,
                                    paced.get('before'), ahead=True)
                        write_pause(nodes, mm, resolve, anchor,
                                    paced.get('after'), ahead=False)
                    note('  wave %d spawner %d: %d set, %d added, %d dropped'
                         % (i + 1, spawner, got['set'], got['added'],
                            got['dropped']))

            # 1b. Opening delay. OnslaughtDelay(Delay, Difficulty) is the
            #     call that waits; OnslaughtStartGroup's float is
            #     `waitUntilPreviousGroupDeadPercent`, which gates on the
            #     previous group dying rather than on the clock. A wave whose
            #     previous group was emptied by authoring can sit on such a
            #     gate, so they are cleared and the author's delay is a real
            #     OnslaughtDelay in front of the wave's first spawn.
            for i, wave in enumerate(waves[:len(live)]):
                number = live[i]
                for spawner in spawners:
                    for j in plan.get((number, spawner)) or []:
                        for call in bppatch.calls_named(mm[j][2],
                                                        'OnslaughtStartGroup',
                                                        resolve):
                            f = next((v for k, v in call.ops
                                      if k == 'expr' and v.name == 'FloatConst'),
                                     None)
                            if f is not None:
                                f.ops[0] = (f.ops[0][0], 0.0)


            # 2. waves beyond what the map ships, cloned and dispatched by
            #    an appended chain, which needs no ladder edit. The donor is a
            #    wave that carries a break, so each copy has one to retune --
            #    a block's control flow is Node references once cloned, which
            #    is no place to insert a statement.
            if want > len(live):
                # spawners that reach OnslaughtEnd at the old close number: each gets
                # an arm in every appended wave, empty if it sends nothing
                closers = {s for s in dispatched
                           if any(bppatch.calls_named(mm[j][2], 'OnslaughtEnd', resolve)
                                  for j in plan.get((sh['close'], s)) or [])}
                donor_wave = next(
                    (w for w in live
                     if find_break(mm, plan, w, dispatched, resolve)), live[-1])
                for extra in range(len(live), want):
                    number = extra + 1
                    wave = waves[extra]
                    last = (extra == want - 1)
                    kind = ('OnslaughtGoBreak' if wave.get('breakKind') == 'go'
                            else 'OnslaughtWaveBreak')
                    seconds = float(wave.get('breakSeconds') or 10)
                    want_actions = list(wave.get('actions') or [])
                    # the block copied for a spawner the donor wave has none for
                    fallback = wmap.get((donor_wave, sh['base_spawner'])) or next(
                        (wmap[(donor_wave, s)] for s in sorted(spawners)
                         if (donor_wave, s) in wmap), None)
                    has_rows = {s for s in spawners
                                if any(e.get('unit') for e in
                                       (wave.get('spawns') or {}).get(str(s), []))}
                    # the one block per wave that keeps the break, coin and events:
                    # the first spawner that sends something
                    arms = [s for s in sorted(set(spawners) | closers)
                            if (donor_wave, s) in wmap or (
                                fallback is not None
                                and (s in has_rows or s in closers))]
                    home = next((s for s in arms if s in has_rows),
                                arms[0] if arms else None)
                    for spawner in arms:
                        src = wmap.get((donor_wave, spawner)) or fallback
                        idx = bppatch.block_sequence(mm, src, blocks - {src})
                        clones, block_entry = bppatch.clone_block(mm, idx, src)
                        at = len(nodes) - 1
                        for k, c in enumerate(clones):
                            nodes.insert(at + k, c)
                        close_clone(nodes, mm, clones, idx)
                        entries = [e for e in (wave.get('spawns') or {})
                                   .get(str(spawner), []) if e.get('unit')]
                        calls = [c for node in clones
                                 for c in bppatch.spawn_calls_in(node)]
                        block_entry = _write_clone_spawns(
                            nodes, clones, calls, entries, protos,
                            entry=block_entry)
                        for node in clones:
                            for call in bppatch.calls_named(
                                    node, 'OnslaughtStartGroup', resolve):
                                f = next((v for k, v in call.ops
                                          if k == 'expr'
                                          and v.name == 'FloatConst'), None)
                                if f is not None:
                                    f.ops[0] = (f.ops[0][0], 0.0)
                        amount = wave.get('coin')
                        if spawner == home and not last:
                            got = clone_break_home(nodes, mm, clones, resolve, kind,
                                                   seconds, index=fn_index.get(kind))
                            if got is None:
                                raise SystemExit('wave %d: its block has no break and '
                                                 'no spawn to hang one on' % number)
                            # one coin, in the block that holds the break
                            coin_at = next((n for n in clones if bppatch.calls_named(
                                n, 'OnslaughtGiveCoin', resolve)), None)
                            if amount not in (None, ''):
                                if coin_at is None:
                                    coin_at = clone_coin(mm, resolve, int(amount))
                                    if coin_at is not None:
                                        insert_after(nodes, got, coin_at)
                                        clones.insert(clones.index(got) + 1, coin_at)
                                else:
                                    call = bppatch.calls_named(
                                        coin_at, 'OnslaughtGiveCoin', resolve)[0]
                                    n_ = next((v for k, v in call.ops if k == 'expr'
                                               and v.name == 'IntConst'), None)
                                    if n_ is not None:
                                        n_.ops[0] = (n_.ops[0][0], int(amount))
                            block_entry, _n = strip_clone_calls(
                                nodes, clones, resolve, 'OnslaughtGiveCoin', block_entry,
                                keep=lambda n, c=coin_at: n is c)
                        else:
                            clone_break_in(nodes, mm, clones, resolve, kind,
                                           seconds, keep=False,
                                           index=fn_index.get(kind))
                            block_entry, _n = strip_clone_calls(
                                nodes, clones, resolve, 'OnslaughtGiveCoin', block_entry)
                        # events: only those the author asked for, fired once
                        block_entry, _n = strip_clone_calls(
                            nodes, clones, resolve, 'OnslaughtAction', block_entry,
                            keep=lambda n, w=want_actions: spawner == home and any(
                                a in w for a in action_strings(n)))
                        if spawner == home:
                            have = {a for n in clones
                                    if bppatch.calls_named(n, 'OnslaughtAction', resolve)
                                    for a in action_strings(n)}
                            for name in want_actions:
                                if name in have:
                                    continue
                                fresh = clone_action(mm, resolve, name)
                                anchor = last_clone_spawn(nodes, clones, resolve)
                                if fresh is None or anchor is None:
                                    raise SystemExit('wave %d: the event %r has nothing '
                                                     'to copy or hang off'
                                                     % (number, name))
                                insert_after(nodes, anchor, fresh)
                                clones.insert(clones.index(anchor) + 1, fresh)
                        if block_entry is None:
                            raise SystemExit(
                                'wave %d spawner %d: the cloned block lost its '
                                'entry statement' % (number, spawner))
                        close_block = wmap.get((sh['close'], spawner),
                                               wmap[(sh['close'],
                                                     sh['base_spawner'])])
                        head = bppatch.append_wave(
                            nodes, mm, names, resolve, head or entry, number,
                            spawner, block_entry, close_block)
                    note('  wave %d: appended (%s)'
                         % (number, 'ends the onslaught' if last
                            else kind.replace('Onslaught', '')))

            # 3. breaks on the waves the map already had. A break terminates a
            #    wave, so every wave but the last authored one needs exactly
            #    one and the last needs none.
            for i, wave in enumerate(waves[:len(live)]):
                number = live[i]
                last = (i == want - 1)
                found = find_break(mm, plan, number, dispatched, resolve)
                if last:
                    if found:
                        drop(nodes, mm[found[1]][2])
                        note('  wave %d: trailing break removed (it ends the '
                             'onslaught)' % (i + 1))
                    continue
                kind = ('OnslaughtGoBreak' if wave.get('breakKind') == 'go'
                        else 'OnslaughtWaveBreak')
                seconds = float(wave.get('breakSeconds') or 10)
                if found:
                    _b, _i, call, have = found
                    if have != kind:
                        bppatch.set_break_kind(mm, call, kind, resolve, seconds,
                                               index=fn_index.get(kind))
                    elif kind == 'OnslaughtWaveBreak':
                        f = next((v for k, v in call.ops
                                  if k == 'expr' and v.name == 'FloatConst'),
                                 None)
                        if f is not None:
                            f.ops[0] = (f.ops[0][0], seconds)
                    note('  wave %d: %s' % (i + 1, kind.replace('Onslaught', '')))
                else:
                    fresh = fresh_break(mm, resolve, kind, seconds,
                                        fn_index.get(kind))
                    # Hang the break off the wave's last surviving spawn, so
                    # it is unambiguously inside the wave. The statement the
                    # declaration used to end on may have been dropped with the
                    # spawns, and what follows it can belong to the close.
                    live_nodes = {id(n) for n in nodes}
                    anchor = None
                    for spawner in [sh['base_spawner']] + spawners:
                        for j, _c in reversed(spawn_statements(
                                mm, plan.get((number, spawner)) or [], resolve)):
                            if id(mm[j][2]) in live_nodes:
                                anchor = mm[j][2]
                                break
                        if anchor is not None:
                            break
                    if anchor is None and opened.get(number):
                        # no original spawn survives: the first copied door block
                        # that spawns takes the break, its own retuned or a new one
                        k = next((k for k, c in enumerate(opened[number])
                                  if last_clone_spawn(nodes, c, resolve)), 0)
                        clones = opened[number].pop(k)
                        got = clone_break_home(nodes, mm, clones, resolve, kind,
                                               seconds, index=fn_index.get(kind))
                        if got is None:
                            raise SystemExit('wave %d: its copied door block spawns '
                                             'nothing to hang a break on' % (i + 1))
                        break_home[number] = (got, clones)
                        note('  wave %d: %s (in a copied door block)'
                             % (i + 1, kind.replace('Onslaught', '')))
                        continue
                    if anchor is None:
                        raise SystemExit('wave %d has no surviving spawn to '
                                         'hang a break on' % (i + 1))
                    insert_after(nodes, anchor, fresh)
                    note('  wave %d: %s added' % (i + 1,
                                                  kind.replace('Onslaught', '')))

            # the copied door blocks no wave needed a break from lose theirs
            for clone_sets in opened.values():
                for clones in clone_sets:
                    clone_break_in(nodes, mm, clones, resolve, 'OnslaughtWaveBreak',
                                   10.0, keep=False)

            # 3a. coin. One per wave, wherever the break lives.
            for i, wave in enumerate(waves[:len(live)]):
                number = live[i]
                # coin calls on any spawner but the first that has one (the break's
                # spawner first) are removed
                live_nodes = {id(x) for x in nodes}
                found = find_break(mm, plan, number, dispatched, resolve)
                order = ([found[0]] if found else []) + dispatched
                coins, kept_on = [], None
                for spawner in order:
                    here = [j for j in plan.get((number, spawner)) or []
                            if id(mm[j][2]) in live_nodes
                            and bppatch.calls_named(mm[j][2], 'OnslaughtGiveCoin',
                                                    resolve)]
                    if here and kept_on is None:
                        kept_on = set(here)
                    coins += [j for j in here if j not in coins]
                extra = [j for j in coins if j not in (kept_on or ())]
                for j in extra:
                    drop(nodes, mm[j][2])
                if extra:
                    note('  wave %d: %d extra coin call(s) removed' % (i + 1, len(extra)))
                amount = wave.get('coin')
                if amount in (None, ''):
                    continue
                got = ((coins[0], bppatch.calls_named(mm[coins[0]][2],
                                                      'OnslaughtGiveCoin', resolve)[0])
                       if coins else None)
                if got:
                    n = next((v for k, v in got[1].ops
                              if k == 'expr' and v.name == 'IntConst'), None)
                    if n is not None:
                        n.ops[0] = (n.ops[0][0], int(amount))
                        note('  wave %d: %d coin' % (i + 1, int(amount)))
                else:
                    fresh = clone_coin(mm, resolve, int(amount))
                    live_nodes = {id(x) for x in nodes}
                    anchor = None
                    for spawner in [sh['base_spawner']] + spawners:
                        for j, _c in reversed(spawn_statements(
                                mm, plan.get((number, spawner)) or [], resolve)):
                            if id(mm[j][2]) in live_nodes:
                                anchor = mm[j][2]
                                break
                        if anchor is not None:
                            break
                    if anchor is None and number in break_home:
                        # after the break step 3 put in a copied door block
                        anchor = break_home[number][0]
                    if anchor is None:
                        found = find_break(mm, plan, number, dispatched, resolve)
                        if found and id(mm[found[1]][2]) in live_nodes:
                            anchor = mm[found[1]][2]
                    if fresh is not None and anchor is not None:
                        insert_after(nodes, anchor, fresh)
                        note('  wave %d: %d coin added' % (i + 1, int(amount)))
                    elif not last_of(i):
                        raise SystemExit('wave %d has nowhere to put its %d coin'
                                         % (i + 1, int(amount)))

            # 3b. wave events. An action the author did not ask for is
            #     dropped, so a map's own boss does not turn up uninvited.
            for i, wave in enumerate(waves[:len(live)]):
                number = live[i]
                want_actions = list(wave.get('actions') or [])
                live_nodes = {id(n) for n in nodes}
                have = []
                for spawner in dispatched:
                    for j, names_in in action_statements(
                            mm, plan.get((number, spawner)) or [], resolve):
                        if id(mm[j][2]) in live_nodes:
                            have.append((j, names_in))
                for j, names_in in have:
                    if not any(n in want_actions for n in names_in):
                        drop(nodes, mm[j][2])
                        note('  wave %d: dropped event %s'
                             % (i + 1, ', '.join(names_in) or '?'))
                already = {n for _j, ns in have for n in ns}
                for name in want_actions:
                    if name in already:
                        continue
                    fresh = clone_action(mm, resolve, name)
                    if fresh is None:
                        raise SystemExit('this coordinator fires no named event, '
                                         'so %r cannot be added' % name)
                    # right after the wave's first spawn, so the event fires as the
                    # wave starts (after the last spawn it came late in the wave, in
                    # game 2026-10-17; before the first, the read-back finds it in no
                    # wave)
                    anchor = None
                    for spawner in [sh['base_spawner']] + spawners:
                        for j, _c in spawn_statements(
                                mm, plan.get((number, spawner)) or [], resolve):
                            if id(mm[j][2]) in {id(n) for n in nodes}:
                                anchor = mm[j][2]
                                break
                        if anchor is not None:
                            break
                    if anchor is None:
                        # a wave sent only through copied door blocks
                        anchor = next((a for c in doors.get(number, [])
                                       for a in [first_clone_spawn(nodes, c, resolve)]
                                       if a is not None), None)
                    if anchor is None:
                        raise SystemExit('wave %d has no spawn to hang the event '
                                         '%r off' % (i + 1, name))
                    insert_after(nodes, anchor, fresh)
                    note('  wave %d: event %s' % (i + 1, name))

            # 3c. host waves past the last authored one: their spawns, breaks, coin
            #     and events are dropped, so the coordinator holds the authored
            #     waves only. Statements an authored wave or the close also
            #     declares are kept.
            later = live[want:]
            kept = {j for (number, _s), idx in plan.items() if number not in later
                    for j in idx}
            for number in later:
                live_nodes = {id(n) for n in nodes}
                doomed = {}
                for spawner in dispatched:
                    for j in plan.get((number, spawner)) or []:
                        node = mm[j][2]
                        if j in kept or id(node) not in live_nodes:
                            continue
                        if spawn_calls(node, resolve) or any(
                                bppatch.calls_named(node, fn, resolve)
                                for fn in LATER_WAVE_CALLS):
                            doomed[id(node)] = node
                gone = sum(1 for node in doomed.values() if drop(nodes, node))
                note('  host wave %d: %d statement(s) dropped (past the last authored '
                     'wave)' % (number, gone))

            # 4. the close must be reachable at exactly one past the last wave,
            #    for every spawner, or the enumeration never stops and the map
            #    hangs while loading. Every host wave number from there up to the
            #    host's own close dispatches the close too.
            closing = want + 1
            if closing != sh['close']:
                close_block = wmap[(sh['close'], sh['base_spawner'])]
                for number in range(closing, max(closing + 1, sh['close']), 2):
                    head = bppatch.append_wave(nodes, mm, names, resolve,
                                               head or entry, number, None,
                                               close_block, close_block)
                note('  close moved to wave %d' % closing)
            return head

        bppatch.edit_ubergraph(pkg, names, resolve, edit)
    return mutate


def successor(nodes, node):
    """The statement after `node`, by identity, or None."""
    for j, n in enumerate(nodes):
        if n is node:
            return nodes[j + 1] if j + 1 < len(nodes) else None
    return None


def retarget(clones, victim, to):
    """Point any jump among `clones` that references `victim` at `to` instead.

    `clone_block` rewires a copy's internal jumps to the Node objects of its
    other copies rather than to offsets, and the relocation pass only fixes
    offsets. So a cloned statement cannot simply be dropped -- the reference
    would dangle, which fails the edit outright. This does for a Node what
    `shift_table(on_removed='next')` does for an offset.
    """
    from riftlord.formats.ue.kismet import code_offsets
    for c in clones:
        for node, j in code_offsets([c]):
            kind, val = node.ops[j]
            if val is victim:
                node.ops[j] = (kind, to)


def clone_break_in(nodes, mm, clones, resolve, kind, seconds, keep,
                   index=None):
    """Retune or remove the break a cloned block inherited from its donor.

    An appended wave is cloned from a donor that carries a break, so the copy
    has one to work with rather than needing one inserted into a block whose
    control flow is all Node references.
    """
    for node in list(clones):
        for fn in BREAK_FNS:
            hits = bppatch.calls_named(node, fn, resolve)
            if not hits:
                continue
            if not keep:
                retarget(clones, node, successor(nodes, node))
                drop(nodes, node)
                return 'removed'
            call = hits[0]
            if fn != kind:
                bppatch.set_break_kind(mm, call, kind, resolve, seconds,
                                       index=index)
            elif kind == 'OnslaughtWaveBreak':
                f = next((v for k, v in call.ops
                          if k == 'expr' and v.name == 'FloatConst'), None)
                if f is not None:
                    f.ops[0] = (f.ops[0][0], float(seconds))
            return kind
    return None


def drop_clone_statement(nodes, clones, victim, entry=None):
    """Remove one statement of a cloned block. Returns the block's entry, moved on to
    the next statement when the entry itself was removed."""
    after = successor(nodes, victim)
    retarget(clones, victim, after)
    if victim is entry:
        entry = after
    drop(nodes, victim)
    if victim in clones:
        clones.remove(victim)
    return entry


def strip_clone_calls(nodes, clones, resolve, fn_name, entry=None, keep=lambda _n: False):
    """Remove a cloned block's statements that call `fn_name`, except those `keep`
    accepts. Returns (entry, removed count)."""
    removed = 0
    for node in list(clones):
        if bppatch.calls_named(node, fn_name, resolve) and not keep(node):
            entry = drop_clone_statement(nodes, clones, node, entry)
            removed += 1
    return entry, removed


def first_clone_spawn(nodes, clones, resolve):
    """The first statement of a cloned block that spawns and is still in `nodes`."""
    live = {id(n) for n in nodes}
    for node in clones:
        if id(node) in live and spawn_calls(node, resolve):
            return node
    return None


def last_clone_spawn(nodes, clones, resolve):
    """The last statement of a cloned block that spawns and is still in `nodes`."""
    live = {id(n) for n in nodes}
    for node in reversed(clones):
        if id(node) in live and spawn_calls(node, resolve):
            return node
    return None


def fresh_break(mm, resolve, kind, seconds, index=None):
    """A new break statement of `kind`, copied from any break the coordinator calls."""
    fresh = bppatch.clone_break(mm, resolve, kind, seconds=seconds)
    if fresh is not None:
        return fresh
    # no statement of this kind to copy, so copy the other kind and convert the copy
    other = next(f for f in BREAK_FNS if f != kind)
    fresh = bppatch.clone_break(mm, resolve, other, seconds=seconds)
    if fresh is None:
        raise SystemExit('this coordinator calls no break at all, so there is none '
                         'to copy')
    call = bppatch.calls_named(fresh, other, resolve)[0]
    bppatch.set_break_kind(mm, call, kind, resolve, seconds, index=index)
    return fresh


def clone_break_home(nodes, mm, clones, resolve, kind, seconds, index=None):
    """Give a cloned block the break that ends its wave: the inherited one retuned, or
    a new one after the block's last spawn. Returns the break statement, or None when
    the block spawns nothing to hang one on."""
    for node in clones:
        if any(bppatch.calls_named(node, fn, resolve) for fn in BREAK_FNS):
            clone_break_in(nodes, mm, clones, resolve, kind, seconds, keep=True,
                           index=index)
            return node
    anchor = last_clone_spawn(nodes, clones, resolve)
    if anchor is None:
        return None
    fresh = fresh_break(mm, resolve, kind, seconds, index)
    insert_after(nodes, anchor, fresh)
    clones.insert(clones.index(anchor) + 1, fresh)
    return fresh


def _write_clone_spawns(nodes, clones, calls, entries, protos, entry=None):
    """Fit a cloned block's spawn calls to `entries`, adding or dropping.

    Returns the block's entry statement, which moves if the original was one of
    the ones dropped -- the dispatch chain branches to it, and a chain pointing
    at a statement no longer in the list fails the whole edit.
    """
    import copy
    int_donor = None
    for c in calls:
        node = bppatch.spawn_parts(c['call'])[1]
        if node is not None and node.name == 'IntConst':
            int_donor = node
            break

    owner = {}
    for node in clones:
        for got in bppatch.spawn_calls_in(node):
            owner[id(got['call'])] = node

    for k, want in enumerate(entries):
        args = dict(guid=protos[want['unit']],
                    count=row_count(want),
                    difficulty=int(want.get('difficulty', ALL)),
                    promote=float(want.get('promote') or 0.0),
                    int_donor=int_donor)
        if k < len(calls):
            bppatch.set_spawn(calls[k]['call'], **args)
            set_add_count(calls[k]['call'], want, int_donor)
        else:
            anchor = owner.get(id(calls[-1]['call'])) if calls else clones[-1]
            fresh = copy.deepcopy(anchor)
            insert_after(nodes, anchor, fresh)
            clones.append(fresh)
            got = bppatch.spawn_calls_in(fresh)
            if got:
                bppatch.set_spawn(got[0]['call'], **args)
                set_add_count(got[0]['call'], want, int_donor)

    for c in calls[len(entries):]:
        victim = owner.get(id(c['call']))
        if victim is None:
            continue
        after = successor(nodes, victim)
        retarget(clones, victim, after)
        if victim is entry:
            entry = after
        drop(nodes, victim)
        if victim in clones:
            clones.remove(victim)
    return entry


def units_declared(mm, entry, names, resolve, wave, spawner, lookup):
    """The proto each of a wave's spawn calls will send, in declared order."""
    out = []
    for _i, call in spawn_statements(
            mm, declared(mm, entry, names, resolve, wave, spawner), resolve):
        kids = [v for k, v in call['call'].ops if k == 'expr']
        first = kids[0] if kids else None
        if first is None:
            out.append(None)
        elif first.name == 'StructConst':
            g = bppatch.guid_of(first)
            out.append(lookup.get(tuple(g)) if g else None)
        else:
            out.append(lookup.get(first.ops[1][1]))
    return out


def read_back(ua, ux):
    """(shape-like reading, per-wave calls) for a coordinator held in memory."""
    import struct
    from riftlord.formats.ue.uassetio import Package
    pkg = Package(ua, ux)
    names = [n for n, _ in pkg.names]

    def resolve(i):
        if i < 0:
            return names[struct.unpack_from('<I', pkg.imports[-i - 1], 20)[0]]
        if i > 0:
            return names[struct.unpack_from('<I', pkg.exports[i - 1]['meta'],
                                            16)[0]]
        return 'null'
    _walker, mm = ubergraph(pkg, names, resolve)
    eps = bppatch.entry_points(pkg, names, resolve)
    entry = eps['Onslaught_Start']
    wmap, _seen = bppatch.wave_map(mm, entry, names, resolve)
    spawners = sorted({s for _w, s in wmap})

    # a unit is named either by an inline guid or by a Blueprint variable, so
    # one lookup is keyed by guid and the other by the variable's name index
    lookup = {}
    protos = onslaught.protos()
    for nm, g in protos.items():
        lookup[tuple(g)] = nm
    vars_, cdo_index = proto_vars(pkg, names, resolve)
    if vars_ and cdo_index is not None:
        for _nm, rec in name_protos(vars_, pkg, cdo_index, protos).items():
            lookup[rec['name_index']] = rec['proto']

    out, close, units = {}, None, {}
    for wave in range(1, WAVE_LIMIT + 1):
        per = {}
        for spawner in spawners:
            per[spawner] = wave_calls(mm, entry, names, resolve, wave, spawner)
            units[(wave, spawner)] = units_declared(
                mm, entry, names, resolve, wave, spawner, lookup)
        out[wave] = per
        if any(c.get('OnslaughtEnd') for c in per.values()):
            close = wave
            break
    return close, out, units


def closes_for_every_spawner(files, coord_path, spawners):
    """[{name, ok, detail}] -- can every spawner reach `OnslaughtEnd`?

    The coordinator enumerates waves per spawner, and a spawner that never meets the
    close enumerates forever: the game **hangs on the loading screen**, with the mission
    otherwise perfect and every other check passing. It is the one failure that costs a
    game restart to discover, so it is asserted rather than remembered.

    `spawners` is every spawner the LEVEL holds. One that sends nothing is still
    enumerated, so an idle spawner -- a supported design -- needs the close as much as a
    busy one.
    """
    base = 'OMD/Content' + coord_path[len('/Game'):]
    ua, ux = files.get(base + '.uasset'), files.get(base + '.uexp')
    if not ua or not ux:
        return []
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

    _walker, mm = ubergraph(pkg, names, resolve)
    entry = bppatch.entry_points(pkg, names, resolve)['Onslaught_Start']
    out = []
    for spawner in sorted(spawners):
        ends = [w for w in range(1, WAVE_LIMIT + 1)
                if wave_calls(mm, entry, names, resolve, w, spawner).get('OnslaughtEnd')]
        out.append({'name': 'spawner %d reaches the close' % spawner,
                    'ok': bool(ends),
                    'detail': ('OnslaughtEnd at wave %s' % ends[0]) if ends
                              else 'NEVER -- the map would hang on load'})
    return out


def _flow_rows(files, coord_path):
    """The dispatch simulated: does it terminate for every (wave, spawner)?"""
    import tempfile

    from riftlord.onslaught import flowsim
    from riftlord.build.navigation import navpatch
    from riftlord.formats.ue import pakv4
    # the check pak is removed after the survey
    import shutil
    folder = tempfile.mkdtemp(prefix='flowcheck-')
    try:
        tmp = os.path.join(folder, 'check.pak')
        pakv4.write(tmp, files)
        navpatch.open_pak(tmp)
        try:
            bad = flowsim.survey(coord_path.rsplit('/', 1)[-1])
        finally:
            navpatch.close_pak(tmp)
    except Exception as exc:                             # noqa: BLE001
        return [{'name': 'the dispatch terminates', 'ok': False,
                 'detail': 'could not be simulated: %s: %s' % (type(exc).__name__, exc)}]
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return [{'name': 'the dispatch terminates', 'ok': not bad,
             'detail': ('loops at ' + ', '.join('wave %d spawner %d' % (w, s)
                                                for w, s, _h, _n, _t in bad[:4]))
                       if bad else 'every wave and spawner ends'}]


def check_built(files, coord_path, spec, note=lambda _s: None):
    """Confirm the coordinator that was built declares the authored onslaught.

    The table checks say nothing about bytecode, and a structurally perfect
    edit can still be semantically wrong -- the build that hung the game on
    load passed every other check. This reads the finished package back and
    compares it against the spec.
    """
    base = 'OMD/Content' + coord_path[len('/Game'):]
    ua, ux = files.get(base + '.uasset'), files.get(base + '.uexp')
    if not ua or not ux:
        return [{'name': 'coordinator present', 'ok': False,
                 'detail': 'not in the pak at %s' % base}]
    close, per_wave, per_units = read_back(ua, ux)
    waves = spec['waves']
    want_close = len(waves) + 1
    out = [{'name': 'coordinator re-parses', 'ok': True,
            'detail': '%d waves readable' % (len(per_wave))},
           {'name': 'closes one past the last wave',
            'ok': close == want_close,
            'detail': 'OnslaughtEnd at wave %s, wanted %d' % (close, want_close)}]

    # Every spawner the MAP has, not those the onslaught sends units through. A spawner
    # is allowed to sit idle -- that is a real design, not an oversight -- and the game
    # still enumerates it, so it still has to reach the close or the map hangs. Keying
    # this off the spec would have left exactly the idle ones unchecked.
    # Reporting only, never a failure: "every spawner reaches the close" turned out to be
    # false -- a spawner with no arm at all is fine, measured in game. The real question is
    # whether the dispatch terminates, which `flowsim` answers by simulating it.
    try:
        every = {sp['index'] for sp in onslaught.map_report(spec['map'])['spawners']}
    except (Exception, SystemExit):                                    # noqa: BLE001
        every = {int(k) for w in waves for k in (w.get('spawns') or {})}
    for row in closes_for_every_spawner(files, coord_path, every):
        out.append(dict(row, ok=True,
                        detail=row['detail'] + ('' if row['ok'] else ' (idle spawner)')))
    out += _flow_rows(files, coord_path)

    for i, wave in enumerate(waves):
        number = i + 1
        got = per_wave.get(number) or {}
        spawns = sum(c.get('OnslaughtSpawnEntities', 0) for c in got.values())
        want = sum(1 for e in
                   [x for v in (wave.get('spawns') or {}).values() for x in v]
                   if e.get('unit'))
        out.append({'name': 'wave %d spawn calls' % number,
                    'ok': spawns == want,
                    'detail': '%d in the Blueprint, %d authored'
                              % (spawns, want)})
        # what the built Blueprint will actually send, against what was asked
        sent, asked = [], []
        for spawner, rows in sorted((wave.get('spawns') or {}).items()):
            asked += [r['unit'] for r in rows if r.get('unit')]
            sent += [u for u in per_units.get((number, int(spawner)), []) if u]
        out.append({'name': 'wave %d units' % number,
                    'ok': sorted(sent) == sorted(asked),
                    'detail': ('%s' % ', '.join(sorted(set(sent))) or 'none')
                              + (' — asked for %s' % ', '.join(sorted(set(asked)))
                                 if sorted(sent) != sorted(asked) else '')})

        want_actions = set(wave.get('actions') or [])
        if want_actions:
            fired = sum(c.get('OnslaughtAction', 0) for c in got.values())
            out.append({'name': 'wave %d events' % number,
                        'ok': fired >= len(want_actions),
                        'detail': '%d action calls, %d asked for'
                                  % (fired, len(want_actions))})
        kinds = {f for c in got.values() for f in BREAK_FNS if c.get(f)}
        last = number == len(waves)
        want_kind = set() if last else {
            'OnslaughtGoBreak' if wave.get('breakKind') == 'go'
            else 'OnslaughtWaveBreak'}
        out.append({'name': 'wave %d break' % number, 'ok': kinds == want_kind,
                    'detail': '%s, wanted %s'
                              % (sorted(k.replace('Onslaught', '')
                                        for k in kinds) or 'none',
                                 sorted(k.replace('Onslaught', '')
                                        for k in want_kind) or 'none')})
    for row in out:
        note('  %-4s %-40s %s' % ('OK' if row['ok'] else 'FAIL',
                                  row['name'], row['detail']))
    return out


def roster_of(spec):
    """Units in order of first appearance, for the hub's enemy preview.

    The final wave leads, because its first unit is the one the diorama makes
    the headline; everything else follows in the order the onslaught shows it.
    """
    order = []
    waves = spec['waves']
    for wave in ([waves[-1]] + waves[:-1]) if waves else []:
        for _spawner, entries in sorted((wave.get('spawns') or {}).items()):
            for e in entries:
                if e.get('unit') and e['unit'] not in order:
                    order.append(e['unit'])
    #: boss events not yet written into the waves (apply_events)
    for ev in spec.get('events') or []:
        if ev.get('kind') == 'boss' and ev.get('unit') and ev['unit'] not in order:
            order.append(ev['unit'])
    return order


def unit_counts(spec):
    """{unit: spawns over every wave}, for the preview's choice of what to show."""
    out = {}
    for wave in spec.get('waves') or []:
        for _spawner, entries in (wave.get('spawns') or {}).items():
            for e in entries:
                if e.get('unit'):
                    out[e['unit']] = out.get(e['unit'], 0) + int(e.get('count') or 0)
    for ev in spec.get('events') or []:
        if ev.get('kind') == 'boss' and ev.get('unit'):
            out[ev['unit']] = out.get(ev['unit'], 0) + int(ev.get('count') or 1)
    return out


def safe_name(spec):
    """The mission's title reduced to something a filename can hold."""
    name = (spec.get('title') or 'onslaught')
    return ''.join(c if c.isalnum() else '_' for c in name).strip('_') \
        or 'Onslaught'


def default_out(spec):
    """Where a built pak lands: the install's ~mods, wherever that is."""
    from riftlord.core import gamepath
    mods = gamepath.target_dir()
    if not mods:
        raise SystemExit('Orcs Must Die 3 was not found, so there is nowhere '
                         'to install the pak')
    return gamepath.path_in(mods, '901-%s_P' % safe_name(spec))


def mod_of(spec):
    """The mod folder a build names: `modName`, else the title, letters and digits only."""
    return ''.join(c for c in (spec.get('modName') or spec.get('title') or '')
                   if c.isalnum()) or 'Onslaught'


def _taken_names(folder):
    """(build stems, mod folder names) already used by the builds in `folder`."""
    from riftlord.core import paksource
    stems, mods = set(), set()
    for path in paksource.sources(folder):
        stems.add(paksource.stem(path).lower())
        try:
            inner = paksource.names(path)
        except (Exception, SystemExit):                            # noqa: BLE001
            continue
        for name in inner:
            parts = name.split('/')
            if len(parts) > 3 and parts[2] == 'Mods':
                mods.add(parts[3].lower())
    return stems, mods


def fresh_names(spec):
    """A copy of `spec` with a `modName` and `out` no build in the target folder uses,
    so it saves as a new mission: the title first, then the title with a letter suffix
    (leveltitle.letter: B, C, ...; never `_<digits>`, an FName number)."""
    from riftlord.core import gamepath
    from riftlord.build import leveltitle
    folder = gamepath.target_dir()
    if not folder:
        raise SystemExit('Orcs Must Die 3 was not found, so there is nowhere to save')
    stems, mods = _taken_names(folder)
    base_mod, base_file = mod_of(dict(spec, modName=None)), safe_name(spec)
    n = 1
    while True:
        tail = leveltitle.letter(n)
        mod = base_mod + tail
        stem = '901-%s_P' % (base_file + ('_' + tail if tail else ''))
        if mod.lower() not in mods and stem.lower() not in stems:
            return dict(spec, modName=mod, out=gamepath.path_in(folder, stem))
        n += 1


def default_raw_out(spec):
    """Where loose assets land: a folder beside where the pak would go.

    Raw assets are for inspecting the build or folding it into a larger mod --
    the game loads paks, not loose files -- so they go somewhere the author
    already looks rather than into the game's own content tree.
    """
    from riftlord.core import gamepath
    mods = gamepath.target_dir()
    if not mods:
        raise SystemExit('Orcs Must Die 3 was not found, so there is nowhere '
                         'to put the assets')
    return os.path.join(mods, '%s_Assets' % safe_name(spec))


def write_raw(root, files, note=lambda _s: None):
    """Write the build's files as a loose tree. Returns (root, count, bytes).

    The keys are the paths the pak would use, so the tree that lands on disk is
    exactly the one the pak would have carried.
    """
    root = os.path.abspath(root)
    written = 0
    for inside in sorted(files):
        target = os.path.join(root, *inside.split('/'))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'wb') as fh:
            fh.write(files[inside])
        written += len(files[inside])
        note('  %s' % inside)
    return root, len(files), written


def validate(spec, protos=None, flags=None, note=lambda _s: None):
    """Reject a spec the game could not play, naming what is wrong.

    Shared by `build` and `bundle`. It used to live inside `build`, so bundling
    skipped it and an unknown unit surfaced as a bare `KeyError` from deep inside the
    bytecode writer -- a traceback where the author needed one sentence.
    """
    protos = onslaught.protos() if protos is None else protos
    flags = onslaught.proto_flags() if flags is None else flags
    map_name = spec.get('map')
    waves = spec.get('waves') or []

    pathing = {}
    for sp in onslaught.map_report(map_name)['spawners']:
        walk, fly = bool(sp.get('start')), bool(sp.get('flyer'))
        pathing[sp['index']] = ('both' if (walk and fly) else
                                'walk' if walk else 'fly' if fly else 'none')

    for i, wave in enumerate(waves):
        for spawner, entries in (wave.get('spawns') or {}).items():
            if int(spawner) not in pathing and any(e.get('unit') for e in entries):
                raise SystemExit(
                    'wave %d sends units through spawner %s, which this map does '
                    'not have (it has %s)'
                    % (i + 1, spawner,
                       ', '.join(str(x) for x in sorted(pathing)) or 'none'))
            path = pathing.get(int(spawner), 'none')
            for e in entries:
                unit = e.get('unit')
                if not unit:
                    continue
                if unit not in protos:
                    raise SystemExit('wave %d, spawner %s: unknown unit %r'
                                     % (i + 1, spawner, unit))
                # a spawner offers a walking start point, a flyer one or both,
                # and a unit sent down a path it cannot use has nowhere to go
                flies = flags.get(unit, {}).get('flyer', False)
                if flies and path == 'walk':
                    raise SystemExit(
                        'wave %d: %s flies, but spawner %s has only a walking '
                        'path' % (i + 1, unit, spawner))
                if not flies and path == 'fly':
                    raise SystemExit(
                        'wave %d: %s walks, but spawner %s has only a flyer '
                        'path' % (i + 1, unit, spawner))

    # Two protos have no diorama mesh anywhere in the shipped previews, so a
    # roster containing them silently loses one. Say so rather than let the
    # author wonder where their firelings went.
    from riftlord.onslaught import makepreview
    for u in roster_of(spec):
        if not makepreview.creature_for(u) \
                or not makepreview.assets_for(makepreview.creature_for(u)):
            note('note: %s has no preview art in any shipped diorama, so it will '
                 'not appear in the mission preview' % u)


def normalise(spec):
    """A copy of `spec` whose `spawns` keys are strings.

    `author` reads them as `spawns[str(index)]` while the validation above reads them
    as `int(index)`, so a spec built in Python with integer keys passes every check
    and then authors NOTHING -- 22 calls dropped, 0 written, a silently empty
    onslaught. The browser only ever sends JSON, where object keys are strings, which
    is why this never showed up there.
    """
    out = dict(spec)
    out['waves'] = []
    for wave in spec.get('waves') or []:
        wave = dict(wave)
        if wave.get('spawns'):
            wave['spawns'] = {str(k): v for k, v in wave['spawns'].items()}
        out['waves'].append(wave)
    return out


# -- Unit names and Guids ------------------------------------------------------------
#
# A spawn row or boss event names its unit by proto row name (`unit`) and, when saved
# by the Onslaught Editor, by that row's Guid too (`guid`, 32 hex digits as stored).
# A build resolves the name first, then the Guid, so a mod that renames a row keeps
# the specs that spawn it working. A spec without Guids resolves by name alone.

def guid_hex(guid):
    """A protos() Guid (four int32) as its stored bytes in hex."""
    import struct
    return struct.pack('<4i', *guid).hex()


def _unit_rows(spec):
    """Every dict in `spec` that names a unit: wave spawn rows and boss events."""
    for wave in spec.get('waves') or []:
        for entries in (wave.get('spawns') or {}).values():
            for e in entries or []:
                if isinstance(e, dict) and e.get('unit'):
                    yield e
    for ev in spec.get('events') or []:
        if isinstance(ev, dict) and ev.get('unit'):
            yield ev


def resolve_units(spec, protos=None, note=lambda _s: None, stamp=False):
    """A copy of `spec` whose unit rows name current protos: a row whose `unit` is no
    proto but whose `guid` is one takes that proto's name, each such rename noted once.
    With `stamp`, every row naming a proto also gets that proto's `guid`."""
    import copy
    protos = onslaught.protos() if protos is None else protos
    by_guid = {guid_hex(g): n for n, g in protos.items()}
    out = copy.deepcopy(spec)
    told = set()
    for row in _unit_rows(out):
        unit = row['unit']
        if unit not in protos:
            now = by_guid.get(str(row.get('guid') or '').lower().replace('-', ''))
            if now is None:
                continue
            if (unit, now) not in told:
                told.add((unit, now))
                note('%s is now %s' % (unit, now))
            row['unit'] = unit = now
        if stamp:
            row['guid'] = guid_hex(protos[unit])
    return out


# -- Mission events ----------------------------------------------------------------
#
# `spec['events']` is designer data (workshop/research/MISSION-SPECIAL-EVENTS.md):
#
#   {kind: 'boss',  wave, unit, door, count, reaction}   door = the spawner index
#   {kind: 'music', wave, track}                          the switch to the second loop
#   {kind: 'rift',  wave, rift, open}                     Scenario_18's rifts, or a
#                                                          placed unstable rift (spawner index)
#   {kind: 'dialogue', wave, when, delay, speaker, text, duration}
#                                                          a captioned line the Mission
#                                                          Events piece plays (hostevents.clean_line)
#
# A build turns them into what `author` already writes: a boss is a spawn row on its
# door, and a reaction, music change or rift is that wave's OnslaughtAction; a dialogue
# line needs no action (riftlord/missions/missionevents.py plays it). With
# `events` present it owns every event action (riftlord.hostevents.is_event_action);
# a spec without the key has its events read out of its waves first (`split_events`).

#: Event kinds a spec holds.
EVENT_KINDS = ('boss', 'music', 'rift', 'dialogue')
#: Report statuses, per event.
APPLIED = 'applied'
APPLIED_NO_REACTION = 'applied without reaction'
NOT_POSSIBLE = 'not possible on this host'
#: Report detail of an event the Mission Events piece performs (riftlord/missionevents.py).
PIECE = 'by Mission Events'
#: Row fields a boss event keeps from the spawn row it was read from.
BOSS_ROW_FIELDS = ('promote', 'before', 'after', 'difficulty', 'addCount')
#: A boss row's defaults (spec.js newSpawn).
BOSS_ROW_DEFAULTS = {'promote': 0.5, 'before': None, 'after': None,
                     'difficulty': ALL, 'addCount': None}


def split_events(spec, caps):
    """(spec, events) read out of a spec's waves: each event action becomes an event and
    a boss reaction takes its boss's spawn row in the same wave with it."""
    import copy
    from riftlord.missions import hostevents
    spec = copy.deepcopy(spec)
    events = []
    for i, wave in enumerate(spec.get('waves') or []):
        number = i + 1
        for name in list(wave.get('actions') or []):
            if name in hostevents.BOSS_ACTIONS:
                unit = hostevents.BOSS_ACTIONS[name][1]
                ev = {'kind': 'boss', 'wave': number, 'unit': unit, 'door': None,
                      'count': 1, 'reaction': True}
                for spawner, rows in sorted((wave.get('spawns') or {}).items()):
                    row = next((r for r in rows if r.get('unit') == unit), None)
                    if row is not None:
                        rows.remove(row)
                        ev.update(door=int(spawner), count=int(row.get('count') or 1),
                                  row={k: row.get(k) for k in BOSS_ROW_FIELDS})
                        break
                if ev['door'] is None:
                    keys = sorted(int(k) for k in (wave.get('spawns') or {}))
                    ev['door'] = keys[0] if keys else 1
                events.append(ev)
            elif name == hostevents.MUSIC_ACTION:
                events.append({'kind': 'music', 'wave': number,
                               'track': (caps.get('music') or {}).get('change')})
            else:
                m = hostevents.RIFT_ACTION.match(name)
                if m:
                    events.append({'kind': 'rift', 'wave': number, 'rift': int(m.group('n')),
                                   'open': m.group('verb') == 'Open'})
        wave['actions'] = [a for a in (wave.get('actions') or [])
                           if not hostevents.is_event_action(a)]
        wave['spawns'] = {k: v for k, v in (wave.get('spawns') or {}).items() if v}
    spec['events'] = events
    return spec, events


def _reaction_for(caps, unit):
    return next((r for r in caps.get('reactions') or [] if r['unit'] == unit), None)


def apply_events(spec, caps):
    """(spec with the events written into its waves, report). The returned spec has no
    `events` key, so it is not translated twice; report rows are
    {event, status, detail, action}."""
    import copy
    from riftlord.missions import hostevents
    spec = copy.deepcopy(spec)
    events = spec.pop('events', None) or []
    waves = spec.get('waves') or []
    for wave in waves:
        wave['actions'] = [a for a in (wave.get('actions') or [])
                           if not hostevents.is_event_action(a)]
    report = []

    def fire(number, action):
        acts = waves[number - 1].setdefault('actions', [])
        if action not in acts:
            acts.append(action)

    for ev in events:
        kind, number = ev.get('kind'), int(ev.get('wave') or 0)
        row = {'event': ev, 'status': APPLIED, 'detail': '', 'action': None}
        report.append(row)
        if kind not in EVENT_KINDS:
            row.update(status=NOT_POSSIBLE, detail='unknown event kind %r' % kind)
            continue
        if not 1 <= number <= len(waves):
            row.update(status=NOT_POSSIBLE, detail='the onslaught has no wave %d' % number)
            continue
        if kind == 'boss':
            unit = ev.get('unit')
            if not unit:
                row.update(status=NOT_POSSIBLE, detail='no boss chosen')
                continue
            entry = dict(BOSS_ROW_DEFAULTS, **(ev.get('row') or {}))
            entry.update(unit=unit, count=max(1, int(ev.get('count') or 1)), boss=True)
            spawns = waves[number - 1].setdefault('spawns', {})
            spawns.setdefault(str(int(ev.get('door') or 1)), []).append(entry)
            if ev.get('reaction'):
                got = _reaction_for(caps, unit)
                if got and got.get('piece'):
                    row.update(piece=True, detail=PIECE)
                elif got:
                    fire(number, got['action'])
                    row['action'] = got['action']
                else:
                    row.update(status=APPLIED_NO_REACTION,
                               detail=NO_ACTION_TO_COPY if caps.get('noActions')
                               else 'this map has no reaction lines for %s' % unit)
        elif kind == 'music':
            music = caps.get('music') or {}
            if not music.get('available'):
                row.update(status=NOT_POSSIBLE, detail=music.get('why') or '')
                continue
            if music.get('piece'):
                row.update(piece=True, detail=PIECE)
                continue
            fire(number, hostevents.MUSIC_ACTION)
            row['action'] = hostevents.MUSIC_ACTION
        elif kind == 'rift':
            rift = caps.get('rift') or {}
            n = int(ev.get('rift') or 0)
            if n in (rift.get('pieceRifts') or []):
                row.update(piece=True, detail=PIECE)
                continue
            if n not in (rift.get('rifts') or []):
                row.update(status=NOT_POSSIBLE,
                           detail=rift.get('why') or 'this map has no unstable rift %d' % n)
                continue
            action = hostevents.action_name('rift', rift=n, open=bool(ev.get('open', True)))
            fire(number, action)
            row['action'] = action
        elif kind == 'dialogue':
            dialogue = caps.get('dialogue') or {}
            if dialogue.get('piece'):
                row.update(piece=True, detail=PIECE)
            else:
                row.update(status=NOT_POSSIBLE,
                           detail=dialogue.get('why') or hostevents.DIALOGUE_NEEDS)
    return spec, report


#: Why an event needing an action cannot be written when the coordinator has none.
NO_ACTION_TO_COPY = 'its coordinator has no event to copy'


def can_add_actions(bp_name):
    """True when the coordinator `author` writes from has an OnslaughtAction statement
    for `clone_action` to copy."""
    pkg, names, resolve = bppatch.load(bp_name)
    _w, mm = ubergraph(pkg, names, resolve)
    return bppatch.find_call_statement(mm, resolve, 'OnslaughtAction') is not None


def without_actions(caps):
    """Capabilities with every action-driven kind switched off (NO_ACTION_TO_COPY). What
    the Mission Events piece does (`piece: True`) needs no action and stays."""
    music = caps.get('music') or {}
    return dict(caps, reactions=[r for r in caps.get('reactions') or [] if r.get('piece')],
                noActions=True,
                music=music if music.get('piece')
                else dict(music, available=False, why=NO_ACTION_TO_COPY),
                rift=_piece_rifts_only(caps.get('rift') or {}))


def _piece_rifts_only(rift):
    """`caps['rift']` keeping only the Mission Events piece's rifts."""
    own = list(rift.get('pieceRifts') or [])
    if own:
        return dict(rift, available=True, why='', rifts=own)
    return dict(rift, available=False, rifts=[], why=NO_ACTION_TO_COPY)


def bundle_caps(map_name, caps):
    """`caps` as `bundle` can use them: without action-driven kinds when the coordinator
    it authors from has no action to copy."""
    _p, authored_over = makelevel.coordinator_of(map_name)
    if can_add_actions(source_coordinator(map_name) or authored_over):
        return caps
    return without_actions(caps)


def events_view(report):
    """The report as the editor shows it: [{kind, wave, status, detail}]."""
    return [{'kind': r['event'].get('kind'), 'wave': r['event'].get('wave'),
             'status': r['status'], 'detail': r['detail']} for r in report]


def events_wanted(report):
    """The actions applied events fire."""
    return {r['action'] for r in report if r.get('action')}


def music_track(spec):
    """The track the spec's first music event switches to, or None."""
    return next((e.get('track') for e in spec.get('events') or []
                 if e.get('kind') == 'music' and e.get('track')), None)


def with_events(spec, caps, note=lambda _s: None):
    """(spec ready to author, report): events read out of the waves when the spec has
    none, then written back in."""
    if 'events' not in spec:
        spec, _e = split_events(spec, caps)
    spec, report = apply_events(spec, caps)
    for r in report:
        ev = r['event']
        note('  event wave %s %s: %s%s' % (ev.get('wave'), ev.get('kind'), r['status'],
                                            (' (%s)' % r['detail']) if r['detail'] else ''))
    return spec, report


def event_checks(files, coord_path, report):
    """The build gate: [{name, ok, detail}] from riftlord.hostevents.check_files."""
    from riftlord.missions import hostevents
    return hostevents.check_files(files, coord_path, events_wanted(report))


def retune_audio(files, level_path, caps, track):
    """Point the second music loop of an `_Audio` copy in `files` at `track`. Returns
    True when it was changed or already played it."""
    from riftlord.build.world import levelmusic
    music = caps.get('music') or {}
    if not track or track == music.get('change'):
        return True
    base = 'OMD/Content' + level_path[len('/Game'):] + '_' + levelmusic.TAG
    if base + '.umap' not in files:
        return False
    ua, ux, _r = levelmusic.retarget(files[base + '.umap'], files[base + '.uexp'],
                                     None, change=track)
    files[base + '.umap'], files[base + '.uexp'] = ua, ux
    return True


# -- Endless door order ------------------------------------------------------------
#
# The game skips Endless (byte 4) spawns on the first Endless loop and plays them from
# the second. A byte-4 row on a door that sends nothing in that wave on loop 1 puts the
# door to work in that wave on every later loop. `endless_doors` finds those rows and
# `reset_doors` ("Reset door after Onslaught Loop") moves each to a door that IS active
# in the same wave on loop 1, spread by RESET_SPREAD, keeping its unit, count,
# EndlessAddCount, promotion and byte. Every later loop then uses loop 1's doors per
# wave. Door state (open, broken) is never touched.

#: How moved rows are spread over a wave's loop-1 doors.
#: 'rows': each moved row goes to the active door that has taken the fewest moved rows
#: in that wave so far (ties: the door listed first). 'share': to the active door with
#: the fewest moved units per loop-1 unit it sends in that wave.
RESET_SPREAD = 'rows'

def door_groups(map_name):
    """Spawner indices grouped by the door they come through, as the editor tabs them;
    a spawner without a door is a group of its own."""
    report = onslaught.map_report(map_name)
    by = {}
    for sp in report['spawners']:
        door = (sp.get('door') or '').split('.')[-1] or None
        key = door if door in report['doors'] else ('loose', sp['index'])
        by.setdefault(key, []).append(sp['index'])
    return sorted(sorted(v) for v in by.values())


def _sends(row):
    return bool(row.get('unit'))


def _byte(row):
    return int(row.get('difficulty', ALL))


def _loop_one_units(row):
    """Units the row sends on the first Endless loop."""
    return row_count(row) if _sends(row) and _byte(row) in LOOP_ONE else 0


def _sends_later(row):
    """True when the row sends units on some Endless loop after the first."""
    return (_sends(row) and _byte(row) in LOOP_ONE + (ENDLESS,)
            and (row_count(row) > 0 or int(row.get('addCount') or 0) > 0))


def _groups(spec, groups):
    if groups is None:
        keys = sorted({int(k) for w in spec.get('waves') or []
                       for k in (w.get('spawns') or {})})
        groups = [[g] for g in keys]
    return [list(g) for g in groups]


def loop_one_doors(spec, groups=None):
    """Per wave: the door groups (indices into `groups`) that send units on loop 1,
    with the spawner key each one sends from first and its loop-1 units."""
    groups = _groups(spec, groups)
    out = []
    for wave in spec.get('waves') or []:
        spawns = wave.get('spawns') or {}
        active = []
        for g, spawners in enumerate(groups):
            sent = {str(s): sum(_loop_one_units(r) for r in spawns.get(str(s), []))
                    for s in spawners}
            first = next((k for k in (str(s) for s in spawners) if sent[k]), None)
            if first is not None:
                active.append({'group': g, 'key': first, 'units': sum(sent.values())})
        out.append(active)
    return out


def endless_doors(spec, groups=None):
    """Per door: [{'spawners', 'loop1', 'later', 'early', 'waves'}].

    `loop1` is the wave (1-based) the door first sends units in on the first Endless
    loop, `later` the wave it first does from the second loop on, each None when it
    never does. `early` lists the rows that send units through the door, from loop 2
    on, in a wave where it sends nothing on loop 1, as (wave index, spawner key, row
    index); `waves` the 1-based waves they are in. A wave in which no door sends on
    loop 1 has no pattern to keep, so its rows are never early."""
    waves = spec.get('waves') or []
    groups = _groups(spec, groups)
    active = loop_one_doors(spec, groups)
    out = []
    for g, spawners in enumerate(groups):
        keys = [str(s) for s in spawners]
        loop1 = later = None
        early = []
        for i, wave in enumerate(waves):
            spawns = wave.get('spawns') or {}
            rows = [r for k in keys for r in spawns.get(k, [])]
            if loop1 is None and any(_loop_one_units(r) for r in rows):
                loop1 = i + 1
            if later is None and any(_sends_later(r) for r in rows):
                later = i + 1
            if not active[i] or any(a['group'] == g for a in active[i]):
                continue
            for k in keys:
                for j, r in enumerate(spawns.get(k, [])):
                    if _sends_later(r):
                        early.append((i, k, j))
        out.append({'spawners': list(spawners), 'loop1': loop1, 'later': later,
                    'early': early, 'waves': sorted({i + 1 for i, _k, _j in early})})
    return out


def reset_doors(spec, groups=None, spread=None):
    """(spec, moves): `spec` with every early row moved, within its wave, to a door
    that sends on loop 1 in that wave (spread by RESET_SPREAD), appended after that
    door's rows on the spawner it sends from first. A moved row keeps its unit, count,
    EndlessAddCount, promotion and byte, and loses its pauses; a pause that opened its
    old spawner's wave passes to the row now first there. `moves` is
    [{'spawners', 'unit', 'count', 'wave', 'to'}]: source door spawners, 1-based wave,
    target spawner index."""
    import copy
    spread = spread or RESET_SPREAD
    if spread not in ('rows', 'share'):
        raise ValueError('RESET_SPREAD is rows or share, not %r' % spread)
    spec = copy.deepcopy(spec)
    groups = _groups(spec, groups)
    waves = spec.get('waves') or []
    active = loop_one_doors(spec, groups)
    doors = endless_doors(spec, groups)
    moves = []
    for i, wave in enumerate(waves):
        picked = {}
        for door in doors:
            for wi, k, j in door['early']:
                if wi == i:
                    picked.setdefault(k, []).append((j, door['spawners']))
        if not picked:
            continue
        spawns = wave['spawns']
        moving = []
        for k in [str(s) for g in groups for s in g if str(s) in picked]:
            rows = spawns[k]
            gone = {j for j, _s in picked[k]}
            source = dict(picked[k])
            opening = rows[0].get('before') if rows else None
            keep = [r for j, r in enumerate(rows) if j not in gone]
            if keep and 0 in gone and opening and not keep[0].get('before'):
                keep[0]['before'] = opening
            if keep:
                spawns[k] = keep
            else:
                del spawns[k]
            moving += [(source[j], r) for j, r in enumerate(rows) if j in gone]
        taken = {a['group']: 0 for a in active[i]}
        for source, r in moving:
            if spread == 'rows':
                to = min(active[i], key=lambda a: taken[a['group']])
                taken[to['group']] += 1
            else:
                to = min(active[i], key=lambda a: taken[a['group']] / a['units'])
                taken[to['group']] += row_count(r)
            r['before'] = r['after'] = None
            spawns.setdefault(to['key'], []).append(r)
            moves.append({'spawners': source, 'unit': r.get('unit'),
                          'count': row_count(r), 'wave': i + 1, 'to': int(to['key'])})
    return spec, moves


def apply_reset_doors(spec, note=lambda _s: None):
    """`spec` as it builds: with its early Endless rows moved when `resetDoors` is set."""
    if not spec.get('resetDoors'):
        return spec
    out, moves = reset_doors(spec, door_groups(spec['map']))
    note('reset doors after each Endless loop: %d row(s) moved to loop-1 doors (%s)'
         % (len(moves), RESET_SPREAD))
    for m in moves:
        note('  wave %d: %s x%d from spawner %s to spawner %d'
             % (m['wave'], m['unit'], m['count'],
                '/'.join(str(s) for s in m['spawners']), m['to']))
    return out


def reset_doors_bundle(map_name, out, note=lambda _s: None):
    """Rewrite a built mission's coordinator with its own waves and Reset door after
    Onslaught Loop applied, for a mission that has no wave spec. Nothing is written when
    every later loop already keeps loop 1's doors. Returns the `bundle` result, or None."""
    spec = read_onslaught(map_name)
    spec = dict(spec, map=map_name, bundle=True, out=out, resetDoors=True)
    _s, moves = reset_doors(normalise(spec), door_groups(map_name))
    if not moves:
        note('reset doors after each Endless loop: every loop already uses loop 1\'s doors')
        return None
    return bundle(spec, note)


def bundleable_maps():
    """Names of maps an onslaught can be bundled INTO, computed once.

    The map picker needs this per row, and `bundle_target` opens the live pak index
    each call, which is far too slow to repeat for sixty-odd maps.
    """
    return {name for name, path in _mod_map_paths().items()
            if path.startswith('/Game/Mods/')}


def _mod_map_paths():
    """{map name: package path} for maps from ~mods and from paks opened by name
    (Rift Lord opens its bucket's), the opened ones winning."""
    out = dict(navpatch.mod_maps())
    for _name, pak in navpatch.opened_maps().items():
        try:
            out.update(navpatch.maps_in_pak(pak))
        except (Exception, SystemExit):                            # noqa: BLE001
            continue
    return out


def mission_guids(spec, out):
    """{row: Guid bytes} a rebuild writes: the previous build's, with the Missions
    record's over them, so a Guid set since the last build is kept."""
    from riftlord.missions import bucket
    from riftlord.build import makemod
    guids = makemod.previous_guids(out)
    ident = spec.get('mission')
    rec = bucket.load(ident) if (ident and bucket.ACTIVE and not spec.get('copy')) else None
    for row, g in ((rec or {}).get('guids') or {}).items():
        guids[row] = bytes.fromhex(g)[:16]
    return guids


def source_coordinator(map_name):
    """The coordinator object a Map Editor mission's waves are authored from: its
    source level's, whose spawn calls are intact. None for any other map."""
    from riftlord.missions import bucket
    if not bucket.ACTIVE:
        return None
    for name in bucket._record_files():
        if not name.endswith('.json'):
            continue
        rec = bucket.load(name[:-5]) or {}
        if rec.get('map') == map_name and bucket._editable(rec):
            return makelevel.coordinator_of(rec['level'])[1]
    return None


def bundle_target(map_name):
    """The mod and pak behind an INSTALLED map, or None if it is not one.

    Bundling is offered **only** for a map that came from a mod pak. On a shipped map
    it would mean copying base-game level assets into a redistributable pak, which is
    not ours to hand out; the ordinary build already handles those by copying the
    level into a mission of its own.

    `/Game/Mods/` is the test, not `navpatch.mod_maps()` alone -- that also reports
    `Dungeons`, which lives under `/Game/Maps/CustomMaps/` and is base-game content.
    """
    path = _mod_map_paths().get(map_name)
    if not path or not path.startswith('/Game/Mods/'):
        return None
    mod = path[len('/Game/Mods/'):].split('/')[0]
    # an opened pak is the one being worked on, even when a copy is also installed
    opened = navpatch.opened_maps().get(map_name)
    try:
        got = (opened, None) if opened else navpatch.live_index().get(path + '.umap')
    except (Exception, SystemExit):                                # noqa: BLE001
        got = None
    return {'mod': mod, 'level': path, 'pak': got[0] if got else None,
            'coordinator': '/Game/Mods/%s/%s_Onslaught01' % (mod, mod),
            'preview': '/Game/Mods/%s/%s_Enemies01' % (mod, mod)}


def _inner(game_path, ext):
    return game_path.replace('/Game/', 'OMD/Content/') + ext


def _pak_entries(pak_path):
    """{inner path: bytes} for every file in a pak.

    `pak.Pak` keys carry the mount prefix (`../../../OMD/Content/...`), so they are
    cut back to the inner path the writer expects.
    """
    from riftlord.core import paksource
    return paksource.entries(pak_path)


# -- Par time ----------------------------------------------------------------------
#
# A mission's par time is its MissionProtos row's ParTimeSeconds (shipped rows run from
# 180 to 2100). `spec['par']` sets it; without one a build keeps the map's own.

#: Shortest and longest par time a spec may set, in seconds.
PAR_MIN, PAR_MAX = 30, 7200


def _row_par(table, row):
    import struct
    field = table.field(row, 'ParTimeSeconds')
    return round(struct.unpack('<f', bytes(field.payload)[:4])[0], 3) if field else None


def _build_table(files):
    """(inner path, missionproto.Table) of a build's own mission table, or (None, None)."""
    from riftlord.build import makemod
    from riftlord.build import missionproto
    from riftlord.formats.ue.uassetio import Package
    key, _slot = makemod.mission_table_key(files)
    if key is None or key[:-7] + '.uexp' not in files:
        return None, None
    pkg = Package(files[key], files[key[:-7] + '.uexp'])
    pkg.load_payloads()
    return key, missionproto.Table(pkg)


def map_par(map_name):
    """The par time in seconds a map's mission has now: a shipped map's own row, or the
    mission row in an installed mod map's pak. None when there is none to read."""
    from riftlord.build import makemod
    try:
        target = bundle_target(map_name)
        if target is None:
            from riftlord.build import missionproto
            row = makemod.mission_row_for(map_name)
            if row is None:
                return None
            t = missionproto.read()
            return _row_par(t, t.row(row))
        if not target['pak'] or not os.path.exists(target['pak']):
            return None
        _key, t = _build_table(_pak_entries(target['pak']))
        if t is None:
            return None
        main = [r for r in t.rows if not r['name'].endswith(makemod.ENDLESS_TWIN)] or t.rows
        return _row_par(t, main[0]) if main else None
    except (Exception, SystemExit):                  # noqa: BLE001
        return None


def spec_par(spec):
    """The par time a spec sets, in seconds, or None for the map's own. Refuses one
    outside PAR_MIN..PAR_MAX."""
    raw = spec.get('par')
    if raw in (None, ''):
        return None
    try:
        par = float(raw)
    except (TypeError, ValueError):
        raise SystemExit('the par time %r is not a number of seconds' % (raw,))
    if not PAR_MIN <= par <= PAR_MAX:
        raise SystemExit('a par time must be %d to %d seconds, not %g' % (PAR_MIN, PAR_MAX, par))
    return par


def set_par(files, par, note=lambda _s: None):
    """Write `par` (seconds) into every mission row of a build's own mission table, the
    Endless twin's included, in `files` ({inner path: bytes}). Returns True when the
    table changed; False when the build has no table of its own or already has it."""
    key, t = _build_table(files)
    if t is None:
        note('par time not set: the build has no mission table')
        return False
    changed = False
    for row in t.rows:
        field = t.field(row, 'ParTimeSeconds')
        if field is not None and _row_par(t, row) != round(float(par), 3):
            t.set_float(field, par)
            changed = True
    if changed:
        t.export['data'] = t.serialise()
        files[key], files[key[:-7] + '.uexp'] = t.pkg.write()
        note('par time %g seconds' % par)
    return changed


def par_checks(files, par):
    """Readback of the par time in a build's own mission table."""
    _key, t = _build_table(files)
    if t is None:
        return [{'name': 'par time', 'ok': False, 'detail': 'no mission table to read back'}]
    got = sorted({_row_par(t, r) for r in t.rows})
    return [{'name': 'par time %g seconds' % par, 'ok': got == [round(float(par), 3)],
             'detail': 'rows read %s' % ', '.join('%g' % g for g in got)}]


def minion_checks(files, own, note=lambda _s: None):
    """modminions.gates as check rows: the mod minions a build spawns are carried, byte
    for byte the installed mod's, and every spawn names a shipped or carried row."""
    from riftlord.missions import modminions
    out = []
    for name, ok, detail in modminions.gates(files, own):
        out.append({'name': name, 'ok': bool(ok), 'detail': detail})
        note('  %-4s %-46s %s' % ('OK' if ok else 'FAIL', name, detail))
    return out


def bundle(spec, note=lambda _s: None):
    """Write the authored onslaught INTO the installed map's own pak. One file.

    The ordinary build copies the chosen level into a mission of its own, which for an
    installed map means a second copy of that map and a second mission -- so the
    author ends up shipping two paks and players see the map twice. This instead
    rewrites the map's own mod: its coordinator is replaced with the authored one and
    its hub preview regenerated, and everything else in the pak is carried across
    untouched, including the level and its flight navgraph.

    Nothing new is registered: the map's mod already has its mission and campaign
    rows, so its title, campaign and unlock chain are left exactly as they are.
    """
    from riftlord.formats.ue import pakv4

    spec = resolve_units(normalise(spec), note=note)
    map_name = spec.get('map')
    if not map_name:
        raise SystemExit('no map chosen')
    spec = apply_reset_doors(spec, note)
    target = bundle_target(map_name)
    if not target:
        raise SystemExit('%s is not an installed map, so there is no pak to bundle '
                         'into -- build it as a separate mission instead' % map_name)
    if not target['pak'] or not os.path.exists(target['pak']):
        raise SystemExit('cannot find the pak that provides %s' % map_name)
    waves = spec.get('waves') or []
    if not waves:
        raise SystemExit('an onslaught needs at least one wave')
    files = _pak_entries(target['pak'])
    #: mission events, against what the mission's own built level can do
    from riftlord.missions import hostevents
    caps = (hostevents.facts_from_files(files, target['coordinator'])
            or hostevents.capabilities(map_name))
    caps = bundle_caps(map_name, caps)
    track = music_track(spec)
    spec, events = with_events(spec, caps, note)
    validate(spec, note=note)

    note('bundling into %s' % os.path.basename(target['pak']))
    coord_asset = _inner(target['coordinator'], '.uasset')
    if coord_asset not in files:
        raise SystemExit('%s has no coordinator at %s, so it was not built by these '
                         'tools' % (os.path.basename(target['pak']),
                                    target['coordinator']))

    protos = onslaught.protos()
    _path, coord_name = makelevel.coordinator_of(map_name)
    # A Map Editor mission is authored from its source level's coordinator, as its bake
    # is; otherwise source and destination are the same package.
    source = source_coordinator(map_name) or coord_name
    note('authoring %s from %s' % (coord_name, source))
    sh = shape(source)
    note('source has %d waves over %d spawners, closing at %d'
         % (len(sh['waves']), len(sh['spawners']), sh['close']))
    cfiles = makelevel.build_coordinator(
        source, target['coordinator'], author(spec, sh, protos, note))
    files[coord_asset] = cfiles[0]
    files[_inner(target['coordinator'], '.uexp')] = cfiles[1]

    from riftlord.onslaught import makepreview
    pfiles, prep = makepreview.build(roster_of(spec), target['preview'],
                                     counts=unit_counts(spec), options=spec.get('preview'))
    if pfiles:
        files[_inner(target['preview'], '.uasset')] = pfiles[0]
        files[_inner(target['preview'], '.uexp')] = pfiles[1]
        note('preview %s' % prep.get('units', prep))
        from riftlord.core import console
        for line in prep.get('warnings') or []:
            note('warning: preview: %s' % line)
            console.warn('preview: %s' % line, console.ONSLAUGHT_EDITOR)

    par = spec_par(spec)
    if par is not None:
        set_par(files, par, note)

    #: mod minions the waves spawn, re-copied from the installed mod (modminions)
    from riftlord.missions import modminions
    files, _carried = modminions.carry(files, target['mod'], note)

    #: the PACKAGE PATH, not the object name: check_built slices '/Game' off the
    #: front, so an object name silently becomes a mangled path that is never found.
    checks = list(check_built(files, target['coordinator'], spec, note))
    checks += minion_checks(files, target['mod'], note)
    if par is not None and _build_table(files)[1] is not None:
        checks += par_checks(files, par)
    level = hostevents._level_in(files, target['coordinator'])
    if level and any(r['action'] == hostevents.MUSIC_ACTION for r in events) \
            and not retune_audio(files, level[2], caps, track):
        for r in events:
            if r['action'] == hostevents.MUSIC_ACTION:
                r['detail'] = 'the second loop stays %s until the mission is built' \
                              % (caps.get('music') or {}).get('change')
    checks += event_checks(files, target['coordinator'], events)
    bad = [r for r in checks if not r['ok']]
    if bad:
        #: Name them. "1 check(s) failed" sends the author looking through a log for
        #: something the message already knows.
        raise SystemExit('nothing written -- %s'
                         % '; '.join('%s: %s' % (r['name'], r['detail'])
                                     for r in bad))

    out = os.path.abspath(spec.get('out') or target['pak'])
    os.makedirs(os.path.dirname(out), exist_ok=True)
    #: Back the pak up before overwriting it IN PLACE, which is the default here.
    #: An earlier rebuild of a map pak destroyed an authored onslaught that had no
    #: copy anywhere, and recovering it depended on an unrelated backup happening to
    #: exist. One kept copy costs a few hundred KB.
    from riftlord.core import paksource
    if os.path.exists(out) and os.path.abspath(out) == os.path.abspath(target['pak']):
        keep = paksource.backup(out, '.before-bundle')
        note('kept the previous build as %s' % os.path.basename(keep))
    paksource.write(out, files)
    note('wrote %s (%d bytes, %d files)'
         % (out, paksource.size(out), len(files)))

    #: The dispatch is simulated on the FINISHED pak, because a coordinator that
    #: cannot terminate for one spawner hangs the game on the loading screen with
    #: every structural check passing.
    from riftlord.onslaught import flowsim
    navpatch.open_pak(out)
    try:
        loops = flowsim.survey(coord_name)
    finally:
        navpatch.close_pak(out)
    checks.append({'name': 'coordinator terminates for every spawner',
                   'ok': not loops,
                   'detail': ('loops at ' + ', '.join('wave %d spawner %d'
                                                      % (w, sp)
                                                      for w, sp, _h, _n, _t
                                                      in loops[:4]))
                             if loops else 'every wave and spawner ends'})
    if loops:
        raise SystemExit('the bundled coordinator does not terminate: %s'
                         % loops[:4])
    note('done')
    return {'pak': out, 'checks': checks, 'bundled_into': target['mod'],
            'events': events_view(events),
            'report': {'mod': target['mod'], 'level': target['level'],
                       'coordinator': coord_name,
                       'source_pak': target['pak']},
            'units': roster_of(spec), 'waves': len(waves)}


def build(spec, note=lambda _s: None):
    """Write the spec as a mod pak. Returns {'pak', 'checks', 'report'}."""
    from riftlord.build import makemod
    from riftlord.formats.ue import pakv4

    spec = resolve_units(normalise(spec), note=note)
    map_name = spec.get('map')
    if not map_name:
        raise SystemExit('no map chosen')
    waves = spec.get('waves') or []
    if not waves:
        raise SystemExit('an onslaught needs at least one wave')
    spec = apply_reset_doors(spec, note)
    #: mission events, against what the host level can do
    from riftlord.missions import hostevents
    caps = hostevents.capabilities(map_name)
    track = music_track(spec)
    spec, events = with_events(spec, caps, note)

    protos = onslaught.protos()
    flags = onslaught.proto_flags()
    validate(spec, protos, flags, note)

    _path, coord_name = makelevel.coordinator_of(map_name)
    note('reading %s' % coord_name)
    sh = shape(coord_name)
    note('source has %d waves over %d spawners, closing at %d'
         % (len(sh['waves']), len(sh['spawners']), sh['close']))
    note('authoring %d waves' % len(waves))

    # The mod name is the folder every asset this build makes lives under, so
    # two missions sharing one would overwrite each other's tables, levels and
    # coordinators. It follows the title unless the author names it outright.
    # Underscores separate the parts of a row name (`<mod>_Mission01`), so the
    # mod name has to be letters and digits only, not `safe_name`'s filename
    # form.
    mod = mod_of(spec)
    out = spec.get('out') or (default_raw_out(spec) if spec.get('raw')
                              else default_out(spec))
    # Every mission table goes in the mod's own table folder, which OrcsMustUI's
    # registry finds in the pak, so no build ships BP_OMDProtoManager. With no campaign it is a custom map; a
    # custom campaign lists it from a pak of its own (customcampaign), and a
    # shipped one means patching the shipped CampaignProtos.
    from riftlord.missions import customcampaign
    placement = spec.get('intoCampaign') or makemod.CUSTOM_MAPS
    into, choice = customcampaign.resolve(placement, spec.get('details'))
    if isinstance(choice, dict) and not (choice.get('name') or '').strip():
        raise SystemExit('name the new campaign')
    custom = into is None
    #: a music event's track as the level's second loop (riftlord.levelmusic)
    sublevels, extra = None, None
    if track and any(r['action'] == hostevents.MUSIC_ACTION for r in events) \
            and track != (caps.get('music') or {}).get('change'):
        from riftlord.build.world import levelmusic
        got = levelmusic.copy(map_name, mod or 'MyMod',
                              {'events': [{'kind': 'music', 'track': track}]})
        if got is not None:
            old, new, blobs, _r = got
            sublevels, extra = {old: new}, {new: (blobs, '.umap')}
    par_used = spec_par(spec) or map_par(map_name) or 600.0
    files, report = makemod.build(
        mod=mod or 'MyMod',
        title=spec.get('title') or 'My Onslaught',
        level='unused',
        par=par_used,
        war=bool(spec.get('war')),
        campaign_title=None,
        into=into,
        slot=makemod.slot_for(mod or 'MyMod'),
        # A rebuild keeps the mission's Guids, which is what campaigns list it by.
        guids=({} if spec.get('raw') else mission_guids(spec, out)),
        # The Endless twin, when asked for: listed in the shipped Endless row
        # alongside a shipped campaign (which is patched anyway), otherwise through
        # OrcsMustUI's Endless extension in the mission's own table folder (makemod).
        endless=spec.get('endless') is True,
        with_level=map_name,
        mutate=author(spec, sh, protos, note),
        roster=roster_of(spec),
        cinematic=bool(spec.get('cinematic')),
        sublevels=sublevels, extra_files=extra,
        #: the mission's own pictures from its Missions record (buildjob.save)
        load_picture=(spec.get(PICTURES_KEY) or {}).get('picture'),
        portal_picture=(spec.get(PICTURES_KEY) or {}).get('portalPicture'),
        #: the Scramble tiers it joins (missionscramble.clean)
        scramble=spec.get('scramble'),
        #: the heroes and items beating it grants (missionunlocks.clean)
        unlocks=spec.get('unlocks'),
        #: its place in a shipped campaign (the Details dialog's Campaign Index)
        into_index=(spec.get('campaignIndex')
                    or (spec.get('details') or {}).get('campaignIndex') or None)
        if into else None)
    from riftlord.core import console
    from riftlord.missions import missionscramble, missionunlocks
    for warn, line in missionscramble.describe(report) + missionunlocks.describe(report):
        note(line)
        if warn:
            console.warn(line, console.ONSLAUGHT_EDITOR)

    for key in ('mission_row', 'slot', 'mission_path', 'campaign_row', 'row_from', 'listed_in',
                'endless_row', 'endless_listed_in', 'unlocked', 'rewards',
                'entitlement', 'cinematic', 'level_from', 'coordinator',
                'featured'):
        if report.get(key):
            note('%-14s %s' % (key, report[key]))
    for line in report.get('preview_warnings') or []:
        note('warning: preview: %s' % line)
        console.warn('preview: %s' % line, console.ONSLAUGHT_EDITOR)

    #: mod minions the waves spawn, copied from the installed mod (modminions)
    from riftlord.missions import modminions
    files, _carried = modminions.carry(files, makemod.slot_for(mod or 'MyMod'), note)

    # the same checks makemod runs before it will write a pak, then a readback
    # of the coordinator itself, which none of those touch
    checks, failures = [], 0
    for row in (check_built(files, report['coordinator'], spec, note)
                + event_checks(files, report['coordinator'], events)
                + par_checks(files, par_used)
                + minion_checks(files, makemod.slot_for(mod or 'MyMod'), note)):
        checks.append(row)
        failures += 0 if row['ok'] else 1
    for name, ok, detail in makemod.verify(files, report):
        checks.append({'name': name, 'ok': bool(ok), 'detail': str(detail)})
        note('  %-4s %-46s %s' % ('OK' if ok else 'FAIL', name, detail))
        failures += 0 if ok else 1
    if failures:
        raise SystemExit('%d check(s) failed; nothing written' % failures)

    common = {'checks': checks, 'events': events_view(events),
              'report': {k: str(v) for k, v in report.items()},
              'units': roster_of(spec), 'waves': len(waves)}

    # Raw assets go through exactly the same build and the same checks; only
    # the last step differs, so what lands loose is what the pak would hold.
    if spec.get('raw'):
        root, count, size = write_raw(out, files, note)
        note('wrote %d files (%d bytes) to %s' % (count, size, root))
        note('these are loose assets: the game loads paks, so use them to '
             'inspect the build or fold it into a larger mod')
        note('done')
        return dict(common, raw=root, files=count, bytes=size)

    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    from riftlord.core import paksource
    paksource.write(out, files)
    note('wrote %s (%d bytes)' % (out, paksource.size(out)))
    placed = customcampaign.finish(out, report, placement, spec.get('details'), note)
    note('done')
    return dict(common, pak=out, campaign=placed.get('campaign'),
                notes=placed.get('notes'), placement=placed.get('placement'))


# ---------------------------------------------------------------------------
# Coordinators that name their units through Blueprint variables
#
# Scenario_Ice_01 is the only one of the 48, and it is a deliberate authoring
# style rather than an oddity: the designer declared one variable per enemy and
# referenced it from all 188 spawn calls instead of pasting the proto struct
# into each. Its defaults live in the class default object as `OMDSoftProtoPtr`
# properties, which despite the name hold a plain FGuid and no path.
# ---------------------------------------------------------------------------

#: The struct a proto variable is declared as.
PROTO_STRUCT = 'OMDSoftProtoPtr'


def class_default(pkg, names, resolve):
    """(export index, export) of the class default object, or (None, None)."""
    import struct
    for i, e in enumerate(pkg.exports):
        nm = names[struct.unpack_from('<I', e['meta'], 16)[0]]
        if nm.startswith('Default__'):
            return i, e
    return None, None


def proto_vars(pkg, names, resolve):
    """{variable name: {'guid_at', 'name_index', 'proto'}} from the CDO.

    Each entry is a tagged `OMDSoftProtoPtr` whose payload is itself a tagged
    `Guid` struct, so the sixteen bytes sit 49 into the payload: 8 name, 8 type,
    8 size and index, 8 struct name, 16 struct guid, 1 has-guid.
    """
    import struct
    idx, cdo = class_default(pkg, names, resolve)
    if cdo is None:
        return {}, None
    data = bytes(cdo['data'])
    out, o = {}, 0
    while o < len(data) - 8:
        ni = struct.unpack_from('<I', data, o)[0]
        o += 8
        nm = names[ni] if ni < len(names) else '?'
        if nm == 'None':
            break
        ti = struct.unpack_from('<I', data, o)[0]
        o += 8
        tp = names[ti] if ti < len(names) else '?'
        size = struct.unpack_from('<i', data, o)[0]
        o += 8
        sub = None
        if tp == 'StructProperty':
            si = struct.unpack_from('<I', data, o)[0]
            o += 8
            sub = names[si] if si < len(names) else '?'
            o += 17
        elif tp in ('ArrayProperty', 'ByteProperty', 'EnumProperty'):
            o += 9
        elif tp == 'BoolProperty':
            o += 2
            continue
        else:
            o += 1
        if sub == PROTO_STRUCT and size >= 65:
            out[nm] = {'guid_at': o + 49, 'name_index': ni, 'proto': None}
        o += size
    return out, idx


def name_protos(vars_, pkg, cdo_index, protos):
    """Fill in which proto each variable currently holds."""
    import struct
    data = bytes(pkg.exports[cdo_index]['data'])
    byguid = {tuple(g): n for n, g in protos.items()}
    for nm, rec in vars_.items():
        g = struct.unpack_from('<4i', data, rec['guid_at'])
        rec['proto'] = byguid.get(tuple(g))
    return vars_


def set_var_proto(pkg, cdo_index, rec, guid):
    """Point a proto variable at another unit, in the class default object."""
    import struct
    data = bytearray(pkg.exports[cdo_index]['data'])
    struct.pack_into('<4i', data, rec['guid_at'], *guid)
    pkg.exports[cdo_index]['data'] = bytes(data)


def var_of_call(call, names):
    """The variable a spawn call names, or None when it uses a literal."""
    kids = [v for k, v in call.ops if k == 'expr']
    if not kids or kids[0].name not in ('InstanceVariable', 'LocalVariable'):
        return None
    return bppatch._varname(kids[0], names)


def point_call_at(call, name_index):
    """Retarget a spawn call's variable reference by FName index.

    The operand is an FFieldPath -- int32 count, count x FName, int32 owner --
    so only the name index moves.
    """
    node = [v for k, v in call.ops if k == 'expr'][0]
    for k, (kind, v) in enumerate(node.ops):
        if kind == 'u32' and k == 1:
            node.ops[k] = (kind, name_index)
            return True
    return False


def read_onslaught(map_name):
    """The map's own onslaught, as a spec the editor can open and edit.

    Read through `declaration`, so an action-prefixed wave is included the same
    way the builder sees it. Group gates and delays are attached to the spawn
    they precede, which is how the form expresses pacing.
    """
    _path, coord = makelevel.coordinator_of(map_name)
    pkg, names, resolve = bppatch.load(coord)
    walker, mm = ubergraph(pkg, names, resolve)
    eps = bppatch.entry_points(pkg, names, resolve)
    entry = eps['Onslaught_Start']
    sh = shape(coord)

    protos = onslaught.protos()
    lookup = {tuple(g): n for n, g in protos.items()}
    vars_, cdo_index = proto_vars(pkg, names, resolve)
    if vars_ and cdo_index is not None:
        for _n, rec in name_protos(vars_, pkg, cdo_index, protos).items():
            lookup[rec['name_index']] = rec['proto']

    def literal(call, kind, skip=0):
        got = [v for k, v in call.ops
               if k == 'expr' and v.name == kind]
        return got[skip].ops[0][1] if len(got) > skip else None

    # every spawner the coordinator dispatches, including ones holding only a break or coin
    wmap, _seen = bppatch.wave_map(mm, entry, names, resolve)
    readable = sorted(set(sh['spawners']) | {s for _w, s in wmap})
    waves = []
    for number in sh['waves']:
        wave = {'breakKind': 'wave', 'breakSeconds': 10,
                'coin': None, 'actions': [], 'spawns': {}}
        for spawner in readable:
            rows, gate, wait = [], None, None
            for i in declared(mm, entry, names, resolve, number, spawner):
                node = mm[i][2]
                for call in bppatch.calls_named(node, 'OnslaughtStartGroup',
                                                resolve):
                    gate = literal(call, 'FloatConst')
                for call in bppatch.calls_named(node, 'OnslaughtDelay', resolve):
                    wait = literal(call, 'FloatConst')
                for call in bppatch.calls_named(node, 'OnslaughtAction',
                                                resolve):
                    for x in bppatch._flat(call):
                        if x.name in ('StringConst', 'UnicodeStringConst'):
                            for _k, v in x.ops:
                                if isinstance(v, str) and v not in wave['actions']:
                                    wave['actions'].append(v)
                for got in spawn_calls(node, resolve):
                    call = got['call']
                    kids = [v for k, v in call.ops if k == 'expr']
                    first = kids[0] if kids else None
                    unit = None
                    if first is not None and first.name == 'StructConst':
                        g = bppatch.guid_of(first)
                        unit = lookup.get(tuple(g)) if g else None
                    elif first is not None:
                        unit = lookup.get(first.ops[1][1])
                    _p, cnt, diff, promote = bppatch.spawn_parts(call)
                    pause = None
                    if gate:
                        pause = {'kind': 'gate', 'value': round(gate, 1)}
                    elif wait:
                        pause = {'kind': 'wait', 'value': round(wait, 1)}
                    rows.append({
                        'unit': unit,
                        'count': (cnt.ops[0][1] if cnt is not None
                                  and cnt.name == 'IntConst'
                                  else 0 if cnt is not None
                                  and cnt.name == 'IntZero' else 1),
                        'promote': (round(promote.ops[0][1], 3)
                                    if promote is not None else 0.5),
                        'difficulty': (diff.ops[0][1] if diff is not None
                                       else ALL),
                        'addCount': read_add_count(call),
                        'before': pause if not rows else None,
                        'after': None,
                        '_lead': pause if rows else None,
                    })
                    # a pause standing in front of a spawn is, to an author,
                    # the pause that follows the spawn before it
                    if len(rows) > 1 and rows[-1]['_lead']:
                        rows[-2]['after'] = rows[-1]['_lead']
                    gate, wait = None, None
                for call in bppatch.calls_named(node, 'OnslaughtGiveCoin',
                                                resolve):
                    n = next((v for k, v in call.ops
                              if k == 'expr' and v.name == 'IntConst'), None)
                    if n is not None:
                        wave['coin'] = n.ops[0][1]
                for fn in BREAK_FNS:
                    if bppatch.calls_named(node, fn, resolve):
                        wave['breakKind'] = ('go' if fn == 'OnslaughtGoBreak'
                                             else 'wave')
                        if fn == 'OnslaughtWaveBreak':
                            got = bppatch.calls_named(node, fn, resolve)[0]
                            v = literal(got, 'FloatConst')
                            if v:
                                wave['breakSeconds'] = round(v, 1)
            rows = [r for r in rows if r['unit']]
            for r in rows:
                r.pop('_lead', None)
            if rows:
                wave['spawns'][str(spawner)] = rows
        waves.append(wave)

    return {'map': map_name, 'waves': waves,
            'title': mapdata.mission_names().get(map_name) or map_name}
