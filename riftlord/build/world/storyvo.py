"""A level's story dialogue: the lines its script plays, and removing them at build.

Every story level's script plays its lines with `SpawnSoundAttached` on the `VO_Story`
ambient sound, chaining each line to the next through `OnAudioFinished`. The intro
chain runs only when `GetPlayScenarioDialogue()` (the player's "scenario dialogue"
setting) is true; reaction lines (a new minion, a boss) are not gated.

Removing the dialogue does two things to the persistent level's script, neither of
which moves a statement or a jump target:

* the gate's value becomes `False`, padded with `Nothing` to the same in-memory size,
  so the level runs as it does with the player's setting off;
* every other character line plays at volume 0, so chained lines still finish and
  fire whatever follows them.

Announcer lines (doors, waves, the rift) are kept. Plan key: `plan['world']
['storyDialog']` = 'remove'.
"""

import functools
import struct

#: The plan's world setting, and its values.
PLAN_KEY = 'storyDialog'
KEEP, REMOVE = 'keep', 'remove'

#: The user-setting getter the level script gates its intro dialogue on.
GATE_FUNCTION = 'GetPlayScenarioDialogue'
#: Calls that play a line, and the argument index of their volume multiplier.
SOUND_CALLS = {'SpawnSoundAttached': 7}
#: Package folder of every voice line.
DIALOGUE_ROOT = '/Game/Sounds/Dialogue/'
#: Voices kept when the dialogue is removed (gameplay call-outs).
KEPT_VOICES = ('/Announcer/',)
#: Volume a removed line plays at.
MUTED_VOLUME = 0.0

_FALSE, _NOTHING = 0x28, 0x0B


def _resolver(pkg, names):
    def resolve(i):
        if i < 0:
            return names[struct.unpack_from('<I', pkg.imports[-i - 1], 20)[0]]
        if i > 0:
            return names[struct.unpack_from('<I', pkg.exports[i - 1]['meta'], 16)[0]]
        return 'null'
    return resolve


def _import_package(pkg, names, index):
    """The package name of the import an object reference names, or ''."""
    if index >= 0:
        return ''
    outer = struct.unpack_from('<i', pkg.imports[-index - 1], 16)[0]
    if outer >= 0:
        return ''
    return names[struct.unpack_from('<I', pkg.imports[-outer - 1], 20)[0]]


def _calls(node, resolve, fn_name):
    return [x for x in node.walk()
            if x.name in ('FinalFunction', 'CallMath') and x.ops and x.ops[0][0] == 'i32'
            and resolve(x.ops[0][1]) == fn_name]


def _lines(pkg, names, resolve, nodes):
    """[(call node, cue package, voice kept)] for each voice line the script plays."""
    out = []
    for top in nodes:
        for fn, _vol in SOUND_CALLS.items():
            for call in _calls(top, resolve, fn):
                args = [v for k, v in call.ops if k == 'expr']
                if not args or args[0].name != 'ObjectConst':
                    continue
                cue = _import_package(pkg, names, args[0].ops[0][1])
                if cue.startswith(DIALOGUE_ROOT):
                    out.append((call, cue, any(k in cue for k in KEPT_VOICES)))
    return out


def _gates(nodes, resolve):
    """[(statement index, value expr)] for `LetBool(x, <GATE_FUNCTION call>)`."""
    out = []
    for i, top in enumerate(nodes):
        if top.name != 'LetBool':
            continue
        exprs = [v for k, v in top.ops if k == 'expr']
        if len(exprs) == 2 and _calls(exprs[1], resolve, GATE_FUNCTION):
            out.append((i, exprs[1]))
    return out


def _scripts(pkg):
    """(export, names, resolve, (offset, memory size, serialised size, walker)) per
    Function export whose script disassembles."""
    from riftlord.onslaught import bppatch
    from riftlord.formats.ue import kismet
    names = [n for n, _h in pkg.names]
    resolve = _resolver(pkg, names)
    for export, _name in bppatch.functions(pkg, names, resolve):
        found = kismet.find_bytecode(export['data'], names, resolve)
        if found:
            yield export, names, resolve, found


def scan(pkg):
    """{'gates': n, 'lines': n, 'kept': n} for a level package."""
    out = {'gates': 0, 'lines': 0, 'kept': 0}
    for _e, names, resolve, found in _scripts(pkg):
        nodes = found[3].nodes
        out['gates'] += len(_gates(nodes, resolve))
        for _c, _cue, kept in _lines(pkg, names, resolve, nodes):
            out['kept' if kept else 'lines'] += 1
    return out


@functools.lru_cache(maxsize=64)
def survey(level):
    """scan() of the shipped persistent level."""
    from riftlord.core import gamefiles
    from riftlord.build.minimap import mapdata
    from riftlord.formats.ue.uassetio import Package
    pkg = Package(*gamefiles.package(mapdata.map_path(level)))
    pkg.load_payloads()
    return scan(pkg)


def has_dialogue(level):
    """True when the level's script plays story or character lines."""
    got = survey(level)
    return bool(got['gates'] or got['lines'])


def remove(pkg):
    """Remove the story dialogue from a level package in place. Returns
    {'gates': n, 'muted': n, 'skipped': [reason]}."""
    from riftlord.formats.ue import kismet
    done = {'gates': 0, 'muted': 0, 'skipped': []}
    for export, names, resolve, (off, mem, size, walker) in _scripts(pkg):
        nodes = list(walker.nodes)
        before = {id(n): m for m, _s, n in kismet.memory_map(nodes)}
        changed = False
        for i, value in reversed(_gates(nodes, resolve)):
            top = nodes[i]
            k = next(j for j, (kind, v) in enumerate(top.ops) if v is value)
            false = kismet.Node(_FALSE, 'False', 0)
            pad = kismet.node_sizes(value)[1] - kismet.node_sizes(false)[1]
            if pad < 0:
                done['skipped'].append('a gate is smaller than False')
                continue
            top.ops[k] = ('expr', false)
            nodes[i + 1:i + 1] = [kismet.Node(_NOTHING, 'Nothing', 0) for _ in range(pad)]
            done['gates'] += 1
            changed = True
        for call, cue, kept in _lines(pkg, names, resolve, nodes):
            if kept:
                continue
            fn = resolve(call.ops[0][1])
            args = [v for k, v in call.ops if k == 'expr']
            at = SOUND_CALLS[fn]
            if len(args) <= at or args[at].name != 'FloatConst':
                done['skipped'].append('%s: volume is not a constant'
                                       % cue.rsplit('/', 1)[-1])
                continue
            vol = args[at]
            vol.ops = [('f32', MUTED_VOLUME) if k == 'f32' else (k, v) for k, v in vol.ops]
            done['muted'] += 1
            changed = True
        if not changed:
            continue
        after = kismet.memory_map(nodes)
        moved = [n for m, _s, n in after if id(n) in before and before[id(n)] != m]
        total = sum(kismet.node_sizes(n)[1] for n in nodes)
        if moved or total != mem:
            raise SystemExit('story dialogue edit would move %d statement(s); not written'
                             % len(moved))
        script = kismet.emit(nodes)
        data = bytes(export['data'])
        export['data'] = (data[:off] + struct.pack('<ii', mem, len(script)) + script
                          + data[off + 8 + size:])
        export['size'] = len(export['data'])
    return done


def wanted_removed(plan):
    """True when the plan removes the level's story dialogue."""
    return ((plan or {}).get('world') or {}).get(PLAN_KEY) == REMOVE


def apply(builder, plan, report):
    """Remove the dialogue from the level being built when the plan asks."""
    if not wanted_removed(plan):
        return
    got = remove(builder.pkg)
    if got['gates'] or got['muted']:
        report['notes'].append('story dialogue removed (%d gate, %d line(s) silenced)'
                               % (got['gates'], got['muted']))
    else:
        report['notes'].append('this level has no story dialogue to remove')
    if got['skipped']:
        from riftlord.core import buildmessage
        report['skipped'].append(buildmessage.left_out(
            'all of the story dialogue', '; '.join(got['skipped']),
            effect='some story lines may still play', verb='silence'))
