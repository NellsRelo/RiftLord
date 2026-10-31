"""What a host level's scripted events support, read offline from its cooked assets.

A mission event is two halves (workshop/research/MISSION-SPECIAL-EVENTS.md):

- the TRIGGER: the onslaught coordinator calls `OnslaughtAction("<Name>")` in a wave;
  its `DoOnslaughtAction` switch compares the name and broadcasts a multicast delegate
  declared on the coordinator class (`SpawnGhorbash`, `BattleMusicChange`,
  `OpenRedRift(int)`);
- the EFFECT: whatever the persistent level's script bound to that delegate at BeginPlay
  (`AddMulticastDelegate` on its reference to the placed coordinator).

The music change also needs the `_Audio` sublevel: its `ChangeMusic` event crossfades from
the opening loop (spawned on `[MUSIC] Go Break Music Stop`) to the second loop.

`capabilities(map)` answers, per host: which boss reactions it has (and for which boss
unit), whether it can change music (and its two loops), and its unstable rifts.
`check_files(files, coord_path, wanted)` is the build gate: every action the built
coordinator fires has a handler and a level listener, and every wanted action is fired.
"""

import functools
import re
import struct

#: Boss reaction actions: action -> (boss name, the boss unit the host spawns with it).
#: Measured: each of the four boss maps spawns that unit in the action's wave.
BOSS_ACTIONS = {
    'SpawnGhorbash': ('Ghorbash', 'OgreBasher'),
    'SpawnCragg': ('Cragg', 'OrcBoss'),
    'SpawnZelzadore': ('Zelzadore', 'WraithBoss'),
    'SpawnMongrifel': ('Mongrifel', 'KoboldKing'),
}
#: The music change action.
MUSIC_ACTION = 'BattleMusicChange'
#: Unstable rift actions: OpenRedRift_NN / CloseRedRift_NN.
RIFT_ACTION = re.compile(r'^(?P<verb>Open|Close)RedRift_(?P<n>\d\d)$')
#: The `_Audio` event that switches to the second loop.
CHANGE_EVENT = 'ChangeMusic'
#: The `_Audio` event whose path spawns the opening loop.
OPENING_EVENT = '[MUSIC] Go Break Music Stop'
#: The coordinator function every wave event goes through.
ACTION_FN = 'OnslaughtAction'
#: String comparisons a coordinator's action switch uses.
SWITCH_COMPARES = ('NotEqual_StriStri', 'EqualEqual_StriStri')
#: Statements followed from a switch case to its delegate call.
CASE_LOOKAHEAD = 8


def action_name(kind, **kw):
    """The coordinator action a kind of event fires."""
    if kind == 'music':
        return MUSIC_ACTION
    if kind == 'rift':
        return '%sRedRift_%02d' % ('Open' if kw.get('open', True) else 'Close',
                                   int(kw['rift']))
    raise ValueError(kind)


def is_event_action(name):
    """True for an action an event owns (boss reaction, music change, unstable rift)."""
    return name in BOSS_ACTIONS or name == MUSIC_ACTION or bool(RIFT_ACTION.match(name))


#: Highest wave number an event may name.
MAX_WAVE = 99
#: Highest boss count an event may ask for.
MAX_BOSS_COUNT = 20


def clean(events):
    """A plan's `events` validated: a list of {kind, wave, ...} with only the fields
    each kind uses, or None when the plan has none. Raises ValueError on a bad one."""
    if events is None:
        return None
    if not isinstance(events, list):
        raise ValueError('events must be a list')
    out = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        kind = ev.get('kind')
        wave = int(ev.get('wave') or 0)
        if not 1 <= wave <= MAX_WAVE:
            raise ValueError('event wave out of range: %r' % ev.get('wave'))
        if kind == 'boss':
            got = {'kind': kind, 'wave': wave, 'unit': str(ev.get('unit') or ''),
                   'door': int(ev.get('door') or 1),
                   'count': max(1, min(MAX_BOSS_COUNT, int(ev.get('count') or 1))),
                   'reaction': bool(ev.get('reaction'))}
            if isinstance(ev.get('row'), dict):
                got['row'] = {k: ev['row'].get(k) for k in
                              ('promote', 'before', 'after', 'difficulty', 'addCount')}
            if ev.get('guid'):
                got['guid'] = str(ev['guid'])
        elif kind == 'music':
            from riftlord.build.world import levelmusic
            track = ev.get('track')
            if track not in levelmusic.TRACKS:
                raise ValueError('unknown music track %r' % (track,))
            got = {'kind': kind, 'wave': wave, 'track': track}
        elif kind == 'rift':
            got = {'kind': kind, 'wave': wave, 'rift': int(ev.get('rift') or 1),
                   'open': bool(ev.get('open', True))}
        elif kind == 'dialogue':
            got = clean_line(ev, wave)
        else:
            raise ValueError('unknown event kind %r' % (kind,))
        out.append(got)
    lines = [e for e in out if e['kind'] == 'dialogue']
    if lines and lines[0]['when'] == DIALOGUE_AFTER:
        raise ValueError('the first dialogue line cannot follow another')
    return out


#: When a dialogue line plays: as its wave starts, in the break after its wave, or after
#: the dialogue line before it ends (the Mission Events piece, riftlord/missionevents.py).
DIALOGUE_START, DIALOGUE_BREAK, DIALOGUE_AFTER = 'start', 'break', 'after'
DIALOGUE_WHEN = (DIALOGUE_START, DIALOGUE_BREAK, DIALOGUE_AFTER)
#: Longest delay a line may wait, in seconds: the longest break the Timeline sets.
DIALOGUE_DELAY_MAX = 300
#: Why a host without the piece plays no dialogue.
DIALOGUE_NEEDS = 'Needs the Mission Events piece (Map Editor, New Object)'


def clean_line(ev, wave):
    """A dialogue event as a plan keeps it: {kind, wave, when, delay, speaker, text,
    duration} (duration None = from the text, captions.derived_duration). Raises
    ValueError on a line that cannot be written."""
    from riftlord.build.world import captions
    when = ev.get('when') or DIALOGUE_START
    if when not in DIALOGUE_WHEN:
        raise ValueError('unknown dialogue timing %r' % (when,))
    try:
        delay = float(ev.get('delay') or 0)
    except (TypeError, ValueError):
        raise ValueError('dialogue delay is not a number: %r' % (ev.get('delay'),))
    if not 0 <= delay <= DIALOGUE_DELAY_MAX:
        raise ValueError('dialogue delay outside 0-%d s: %r' % (DIALOGUE_DELAY_MAX, delay))
    duration = ev.get('duration')
    duration = None if duration in (None, '') else duration
    speaker, text = ev.get('speaker'), ev.get('text')
    if not isinstance(speaker, (str, type(None))) or not isinstance(text, (str, type(None))):
        raise ValueError('dialogue speaker and text must be text')
    bad = captions.problems(speaker or '', text or '', duration)
    if bad:
        raise ValueError('dialogue line: %s' % '; '.join(bad))
    return {'kind': 'dialogue', 'wave': wave, 'when': when, 'delay': round(delay, 1),
            'speaker': (speaker or '').strip(), 'text': (text or '').strip(),
            'duration': None if duration is None else round(float(duration), 1)}


# -- reading cooked bytes -------------------------------------------------------------

def _load(ua, ux):
    from riftlord.formats.ue.uassetio import Package
    pkg = Package(ua, ux)
    pkg.load_payloads()
    names = [n for n, _h in pkg.names]

    def resolve(i):
        if i < 0:
            return names[struct.unpack_from('<I', pkg.imports[-i - 1], 20)[0]]
        if i > 0:
            return names[struct.unpack_from('<I', pkg.exports[i - 1]['meta'], 16)[0]]
        return 'null'
    return pkg, names, resolve


def _functions(pkg, names, resolve):
    """[(function name, top-level statements)] of every function with bytecode."""
    from riftlord.onslaught import bppatch
    from riftlord.formats.ue import kismet
    out = []
    for export, fname in bppatch.functions(pkg, names, resolve):
        found = kismet.find_bytecode(export['data'], names, resolve)
        if found:
            out.append((fname, found[3].nodes))
    return out


def _field(node, names):
    """(property name, owner index) of an InstanceVariable's FFieldPath, or (None, 0)."""
    if node is None or node.name != 'InstanceVariable' or not node.ops:
        return None, 0
    name = next((names[v] for k, v in node.ops if k == 'u32' and v < len(names)), None)
    owner = next((v for k, v in reversed(node.ops) if k == 'i32'), 0)
    return name, owner


def _strings(node):
    return [v for n in node.walk() if n.name in ('StringConst', 'UnicodeStringConst')
            for _k, v in n.ops if isinstance(v, str)]


def coordinator_facts(ua, ux):
    """{'object', 'delegates', 'handled': {action: [delegate, arg]}, 'fired': [action]}.

    `handled` comes from the DoOnslaughtAction switch: each string compare's case body
    calls one multicast delegate (with an int argument for the red rifts).
    """
    from riftlord.formats.ue import kismet
    pkg, names, resolve = _load(ua, ux)
    delegates = sorted(f[:-len('__DelegateSignature')]
                       for f, _n in _functions(pkg, names, resolve)
                       if f.endswith('__DelegateSignature'))
    fired, handled = [], {}
    for fname, nodes in _functions(pkg, names, resolve):
        mm = kismet.memory_map(nodes)
        by_mem = {m: i for i, (m, _s, _n) in enumerate(mm)}
        for i, top in enumerate(nodes):
            for n in top.walk():
                if n.name in ('FinalFunction', 'LocalFinalFunction') and \
                        str(resolve(n.ops[0][1])) == ACTION_FN:
                    fired += [s for s in _strings(n) if s not in fired]
            if top.name != 'LetBool':
                continue
            cmp_ = next((n for n in top.walk() if n.name == 'CallMath'
                         and str(resolve(n.ops[0][1])) in SWITCH_COMPARES), None)
            if cmp_ is None or i + 1 >= len(nodes) or nodes[i + 1].name != 'JumpIfNot':
                continue
            text = _strings(cmp_)
            if not text:
                continue
            target = by_mem.get(nodes[i + 1].ops[0][1])
            got = _case_delegate(mm, by_mem, target, names)
            if got:
                handled[text[0]] = got
    obj = next((names[struct.unpack_from('<I', e['meta'], 16)[0]] for e in pkg.exports
                if names[struct.unpack_from('<I', e['meta'], 16)[0]].endswith('_C')
                and not names[struct.unpack_from('<I', e['meta'], 16)[0]].startswith(
                    'Default__')), None)
    return {'object': obj[:-2] if obj else None, 'delegates': delegates,
            'handled': handled, 'fired': fired}


def _case_delegate(mm, by_mem, at, names):
    """[delegate, int argument or None] the switch case starting at statement `at` calls."""
    i = at
    for _ in range(CASE_LOOKAHEAD):
        if i is None or i >= len(mm):
            return None
        top = mm[i][2]
        call = next((n for n in top.walk() if n.name == 'CallMulticastDelegate'), None)
        if call is not None:
            var = next((v for k, v in call.ops if k == 'expr'
                        and v.name == 'InstanceVariable'), None)
            name, _owner = _field(var, names)
            arg = next((n.ops[0][1] for n in call.walk()
                        if n.name in ('IntConst', 'ByteConst') and n.ops), None)
            return [name, arg] if name else None
        if top.name == 'Jump':
            i = by_mem.get(top.ops[0][1])
            continue
        if top.name in ('Return', 'EndOfScript', 'PopExecutionFlow'):
            return None
        i += 1
    return None


def level_listeners(ua, ux, coordinator_object):
    """[{'delegate', 'handler', 'bound'}] for each AddMulticastDelegate in the level's
    script on a coordinator delegate. `bound` is True when the delegate's owner is the
    given coordinator's class (the binding resolves on that coordinator)."""
    pkg, names, resolve = _load(ua, ux)
    want = (coordinator_object or '') + '_C'
    out = []
    for _fname, nodes in _functions(pkg, names, resolve):
        handler = None
        for top in nodes:
            if top.name == 'BindDelegate':
                handler = names[top.ops[0][1]]
                continue
            if top.name != 'AddMulticastDelegate':
                continue
            ctx = next((v for k, v in top.ops if k == 'expr'), None)
            var = None
            if ctx is not None:
                var = next((v for k, v in reversed(ctx.ops) if k == 'expr'
                            and v.name == 'InstanceVariable'), None)
            name, owner = _field(var, names)
            owner_name = str(resolve(owner)) if owner else ''
            if 'Onslaught' in owner_name:
                out.append({'delegate': name, 'handler': handler,
                            'owner': owner_name, 'bound': owner_name == want})
            handler = None
    return out


def _reach(mm, entry, names, resolve):
    """Statement indices an event reaches, following latent resumes (Delay)."""
    from riftlord.onslaught import bppatch
    todo, done, out = [entry], set(), []
    while todo:
        at = todo.pop()
        if at in done:
            continue
        done.add(at)
        for i in bppatch.declaration(mm, at, {}, names, resolve, branch_unknown=True):
            out.append(i)
            for n in mm[i][2].walk():
                if n.name == 'SkipOffsetConst':
                    todo.append(n.ops[0][1])
    return out


def audio_facts(ua, ux):
    """{'opening': track or None, 'change': track or None, 'changeEvent': bool} of an
    `_Audio` sublevel: the loop spawned when the first break ends, and the loop its
    ChangeMusic event switches to."""
    from riftlord.onslaught import bppatch
    from riftlord.formats.ue import kismet
    from riftlord.build.world import levelmusic
    pkg, names, resolve = _load(ua, ux)
    eps = bppatch.entry_points(pkg, names, resolve)
    ub = next((nodes for f, nodes in _functions(pkg, names, resolve)
               if f.startswith('ExecuteUbergraph')), None)
    out = {'opening': None, 'change': None, 'changeEvent': CHANGE_EVENT in eps}
    if ub is None:
        return out
    mm = kismet.memory_map(ub)
    for key, event in (('opening', OPENING_EVENT), ('change', CHANGE_EVENT)):
        if event not in eps:
            continue
        for i in _reach(mm, eps[event], names, resolve):
            for n in mm[i][2].walk():
                if n.name != 'ObjectConst':
                    continue
                m = levelmusic.CUE.match(str(resolve(n.ops[0][1])))
                if m and not m.group('intro') and out[key] is None:
                    out[key] = m.group('track')
    return out


# -- per host ------------------------------------------------------------------------

def _package(path):
    """(uasset-or-umap, uexp) bytes of a package, shipped or from the live index."""
    from riftlord.core import gamefiles
    from riftlord.build.navigation import navpatch
    try:
        return gamefiles.package(path)
    except Exception:                                # noqa: BLE001
        pass
    idx = navpatch.live_index()
    for head in ('.umap', '.uasset'):
        if path + head in idx and path + '.uexp' in idx:
            return (navpatch.read_from_pak(*idx[path + head]),
                    navpatch.read_from_pak(*idx[path + '.uexp']))
    raise SystemExit('not found in any pak: %s' % path)


def _audio_of(level_path, level_names):
    """Package path of the level's `_Audio` sublevel, or None."""
    for n in level_names:
        if n.startswith('/Game/') and n.endswith('_Audio') and '.' not in n:
            return n
    return level_path + '_Audio'


def facts(level, coordinator, audio):
    """Capabilities from cooked bytes: `level`, `coordinator`, `audio` are (ua, ux) pairs
    (`audio` may be None). See `capabilities`."""
    coord = coordinator_facts(*coordinator)
    listeners = level_listeners(level[0], level[1], coord['object'])
    heard = {l['delegate']: l['handler'] for l in listeners if l['bound']}
    sound = audio_facts(*audio) if audio else {'opening': None, 'change': None,
                                               'changeEvent': False}

    def live(action):
        got = coord['handled'].get(action)
        return bool(got and got[0] in heard)

    reactions = [{'action': a, 'name': n, 'unit': u} for a, (n, u) in BOSS_ACTIONS.items()
                 if live(a)]
    music_ok = live(MUSIC_ACTION) and sound['changeEvent'] and bool(sound['change'])
    if music_ok:
        music_why = ''
    elif MUSIC_ACTION in coord['handled'] and not live(MUSIC_ACTION):
        music_why = 'This map never hears its music change'
    else:
        music_why = 'This map has no music change'
    rifts = sorted({int(m.group('n')) for a in coord['handled']
                    for m in [RIFT_ACTION.match(a)] if m and live(a)})
    fired = coord['fired']
    return {
        'coordinator': coord['object'],
        'reactions': reactions,
        'music': {'available': music_ok, 'why': music_why,
                  'opening': sound['opening'], 'change': sound['change']},
        'rift': {'available': bool(rifts), 'rifts': rifts,
                 'why': '' if rifts else 'This map has no unstable rifts'},
        'dialogue': {'available': False, 'why': DIALOGUE_NEEDS},
        'listeners': [dict(l) for l in listeners],
        'fired': fired,
        'unheard': [a for a in fired if not live(a)],
    }


@functools.lru_cache(maxsize=64)
def _shipped(level_name):
    return _capabilities(level_name)


def _capabilities(map_name):
    from riftlord.build import makelevel
    from riftlord.build.minimap import mapdata
    from riftlord.formats.ue.uassetio import Package
    level_path = mapdata.map_path(map_name)
    level = _package(level_path)
    coord_path, _obj = makelevel.coordinator_of(map_name)
    coordinator = _package(coord_path)
    names = [n for n, _h in Package(level[0]).names]
    try:
        audio = _package(_audio_of(level_path, names))
    except (Exception, SystemExit):                  # noqa: BLE001
        audio = None
    return facts(level, coordinator, audio)


def capabilities(map_name):
    """What the host can do for each event kind:

    {'reactions': [{'action', 'name', 'unit'}],     boss reaction lines the level hears
     'music': {'available', 'why', 'opening', 'change'},
     'rift': {'available', 'why', 'rifts'},
     'dialogue': {'available', 'why'},              lines only the Mission Events piece plays
     'listeners', 'fired', 'unheard'}               the raw binding facts

    Shipped maps are cached; an installed or built mod map is read each time.
    """
    from riftlord.build.navigation import navpatch
    if map_name in navpatch.maps(include_mods=False):
        return _shipped(map_name)
    return _capabilities(map_name)


# -- the build gate ------------------------------------------------------------------

def _from_files(files, path):
    base = 'OMD/Content' + path[len('/Game'):]
    for head in ('.umap', '.uasset'):
        if base + head in files and base + '.uexp' in files:
            return files[base + head], files[base + '.uexp']
    return None


def _level_in(files, coord_path):
    """(umap, uexp, package path) of the level in `files` that names the coordinator."""
    needle = coord_path.encode()
    for key, data in files.items():
        if key.endswith('.umap') and needle in data and key[:-5] + '.uexp' in files:
            return data, files[key[:-5] + '.uexp'], '/Game' + key[len('OMD/Content'):-5]
    return None


def with_piece(caps, rifts=()):
    """Capabilities with the Mission Events piece placed (riftlord/missionevents.py): a
    music change and a reaction for every boss it has lines for, where the host has none,
    and the unstable rifts `rifts` (spawner indices) the plan places. The piece's own
    entries carry `piece: True` and no action; its rifts are listed in `pieceRifts`."""
    from riftlord.missions import missionevents
    caps = dict(caps)
    if rifts:
        rift = dict(caps.get('rift') or {})
        own = sorted(set(int(r) for r in rifts) - set(rift.get('rifts') or []))
        rift.update(available=True, why='', pieceRifts=own,
                    rifts=sorted(set(rift.get('rifts') or []) | set(own)))
        caps['rift'] = rift
    have = {r['unit'] for r in caps.get('reactions') or []}
    names = {u: n for n, u in BOSS_ACTIONS.values()}
    caps['reactions'] = list(caps.get('reactions') or []) + [
        {'action': None, 'name': names.get(u, u), 'unit': u, 'piece': True}
        for u in missionevents.REACTION_LINES if u not in have]
    music = dict(caps.get('music') or {})
    if not music.get('available'):
        music.update(available=True, why='', piece=True)
    caps['music'] = music
    caps['dialogue'] = {'available': True, 'why': '', 'piece': True}
    caps['piece'] = True
    return caps


def facts_from_files(files, coord_path):
    """`capabilities` of the mission a set of built files holds, or None when they hold
    no level naming the coordinator. The `_Audio` sublevel is read from the files when
    copied, else from the game. With the Mission Events piece's Blueprint in the files,
    the piece's capabilities are added (`with_piece`)."""
    from riftlord.formats.ue.uassetio import Package
    coord = _from_files(files, coord_path)
    level = _level_in(files, coord_path)
    if coord is None or level is None:
        return None
    names = [n for n, _h in Package(level[0]).names]
    path = _audio_of(level[2], names)
    audio = _from_files(files, path)
    if audio is None:
        try:
            audio = _package(path)
        except (Exception, SystemExit):              # noqa: BLE001
            audio = None
    got = facts(level[:2], coord, audio)
    from riftlord.missions import missionevents
    return (with_piece(got, missionevents.rifts_in_level(*level[:2]))
            if missionevents.in_files(files) else got)


def fire_counts(ua, ux, spawners, waves=None):
    """{(wave, action): number of spawners whose declaration of that wave fires it}."""
    from riftlord.onslaught import bppatch
    from riftlord.onslaught import wavespec
    pkg, names, resolve = _load(ua, ux)
    _w, mm = wavespec.ubergraph(pkg, names, resolve)
    entry = bppatch.entry_points(pkg, names, resolve)['Onslaught_Start']
    out = {}
    for wave in waves or range(1, WAVES_SCANNED + 1):
        for s in spawners:
            for j in wavespec.declared(mm, entry, names, resolve, wave, s):
                for call in bppatch.calls_named(mm[j][2], ACTION_FN, resolve):
                    for action in _strings(call):
                        out[(wave, action)] = out.get((wave, action), 0) + 1
    return out


#: Waves `fire_counts` scans by default.
WAVES_SCANNED = 12


def check_files(files, coord_path, wanted=None):
    """[{name, ok, detail}]: the built coordinator's actions against the built level's
    listeners. `files` maps 'OMD/Content/...' to bytes; `wanted` is the set of actions
    the mission's events ask for (None skips that half)."""
    coord = _from_files(files, coord_path)
    if coord is None:
        return []
    level = _level_in(files, coord_path)
    if level is None:
        return []
    c = coordinator_facts(*coord)
    listeners = level_listeners(level[0], level[1], c['object'])
    heard = {l['delegate'] for l in listeners if l['bound']}
    stray = [l['delegate'] for l in listeners if not l['bound']]
    deaf = []
    for action in c['fired']:
        got = c['handled'].get(action)
        if not got:
            deaf.append('%s (no handler)' % action)
        elif got[0] not in heard:
            deaf.append('%s (nothing listens)' % action)
    out = [{'name': 'every event the coordinator fires has a listener',
            'ok': not deaf and not stray,
            'detail': ('; '.join(deaf + ['%s bound to another coordinator' % s
                                         for s in stray]))
                      or '%d fired, all heard' % len(c['fired'])}]
    if wanted is not None:
        missing = sorted(a for a in wanted if a not in c['fired'])
        out.append({'name': 'every event asked for is fired',
                    'ok': not missing,
                    'detail': ', '.join(missing) or '%d asked for' % len(wanted)})
    return out
