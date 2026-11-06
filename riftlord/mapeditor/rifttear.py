"""Rift Groups: the Crystal's groups of Unstable Rifts, the enemies they send and when they
open (Crystal Defense contract with win64-64, 2026-10-24; launch timing 2026-10-25; per-wave
rows and chances 2026-10-28).

Plan data, on the plan's Crystal item (`crystal.groups`; `crystal.riftGap` and
`crystal.waveCount` beside it):

    {name, rifts: [{spawner, difficulty, players,          # the Map Editor: name and rifts
                    chances: [% per wave]}],               # the Onslaught Editor from here
     difficulty, players,
     waves: [[{unit, count, addCount, promote, difficulty, before, after}] per wave],
     minions: [{unit, count, addCount}],                    # older: the rows of every launch
     triggers: [{at, wave, alive, both, open, launchAfter, launchAlive, repeat}]}

`spawner` is the id of a plan spawner with `unstable`, or -indexInLevel for a rift of the
level's own (an adopted Crystal, crystal.adopt). `difficulty` is an EOnslaughtDifficulty (0
Apprentice, 1 War Mage, 2 Rift Lord, 3 Co-op, 4 Endless from loop 2, 5 Any), as wave rows use
it; `players` 0 any, 1 solo, 2 co-op. A trigger MARKS when the Crystal's health falls to `at`
% of its total, the wave reaches `wave` and/or the current wave's enemies left fall to `alive`
% of its most (`both`: all set conditions, else any); blank fields are none. A marked group
LAUNCHES in a wave: a wave or enemies-left trigger's in the wave it is met in while that wave
is fought, else the next; a health trigger's in the next wave to begin; at the first of
`launchAfter` seconds into the wave (already passed: at once), its enemies left falling to `launchAlive` %,
and its last enemy (both 0: 50 %). The first row comes out 2 s after the rift appears (win64-64,
2026-10-28). A trigger marks once; with `repeat` N (Repeat Every (Waves), 0 = once) it marks
again from N waves after it last marked whenever its conditions still hold, counting across
Endless loops (win64-76, 2026-10-29). At launch it picks
`open` of its members whose locks pass, weighted by their chance in that wave (0: not this
wave); a wave whose rows are empty, or with no member to pick, is one it does not open in.
Each opened rift sends the wave's rows: a row's pause (`before` on the first, else the row
above's `after`: wait seconds, or gate % of the earlier rows dead) or RiftGap. A `close` (Stay
Open Until Wave) in an older plan is dropped.

Written to the placed Crystal (names are the contract's):
    GroupStart, GroupSize -> GroupMembers (indexInLevel), MemberDifficulty, MemberPlayers,
    GroupDifficulty, GroupPlayers, GroupName; GroupUnitStart, GroupUnitSize -> UnitProto
    (FOMDSoftProtoPtr), UnitCount, UnitEndlessAdd (older rows); triggers RiftGroup, RiftAt
    (100 = none), RiftWave (0 = none), RiftAlive (0 = none), RiftBoth, RiftPick,
    RiftLaunchAfter, RiftLaunchAlive (0 = none), RiftRepeat (0 = once), all at full length;
    with a wave count W:
    WaveCount, GroupWaveStart/GroupWaveSize [g*W + w] -> RowProto, RowCount, RowEndlessAdd,
    RowPromote, RowDifficulty, RowPauseKind (0 RiftGap, 1 wait, 2 gate), RowPauseValue; and
    MemberChance [m*W + w]. RiftClose and RiftDifficulty are never written.

Plans made before groups carry `crystal.rifts` rows ({at, spawner, unit, count}); each
reads as a group of one rift with its unit and one health trigger.
"""

import struct

#: EOnslaughtDifficulty (wave rows' values; Onslaught Editor spec.js DIFFICULTY).
DIFFICULTIES = {5: 'Any', 0: 'Apprentice', 1: 'War Mage', 2: 'Rift Lord (not in Endless)',
                3: 'Co-op', 4: 'Endless, from loop 2'}
ANY = 5
#: Players: any, solo, co-op.
PLAYERS = {0: 'Any', 1: 'Solo', 2: 'Co-op'}
#: RiftAt and RiftWave values that mean "no condition".
NO_HEALTH, NO_WAVE = 100.0, 0
#: RiftLaunchAfter and RiftLaunchAlive values that mean "no condition".
NO_LAUNCH = 0.0
#: The RiftRepeat value of a trigger that marks once.
NO_REPEAT = 0
#: Limits.
GROUPS_MAX, RIFTS_MAX, UNITS_MAX, TRIGGERS_MAX = 16, 32, 24, 32
NAME_MAX = 40
#: Most waves a group's rows and a rift's chances cover (wave fields' range).
WAVES_MAX = 99
#: A rift's chance in a wave with none given (MemberChance: 100 = always eligible).
CHANCE_DEFAULT = 100.0
RANGES = {'at': (0.1, 99.9), 'wave': (1, 99), 'open': (1, 32), 'alive': (0.0, 99.9),
          'launchAfter': (0.0, 600.0), 'launchAlive': (0.0, 99.9), 'repeat': (0, 99),
          'count': (1, 200), 'rowCount': (0, 200), 'addCount': (0, 200),
          'chance': (0.0, 100.0),
          'promote': (0.0, 1.0), 'wait': (0.0, 600.0), 'gate': (0.0, 100.0)}
#: The trigger fields' labels, as the Onslaught Editor's Rift Group tabs word them (When,
#: Opens At, Open, Repeat). `alive` is an older plan's Enemies Left mark, no longer authored.
LABELS = {'at': 'Crystal Health (%)', 'wave': 'Wave', 'open': 'Open (Rifts)',
          'launchAfter': 'Opens At Seconds Into the Wave',
          'launchAlive': 'Opens At Enemies Left (%)',
          'alive': 'Enemies Left (%)', 'repeat': 'Repeat Every (Waves)'}
#: A row pause's kind as RowPauseKind writes it: none (RiftGap), wait seconds, gate %.
PAUSE_KINDS = {None: 0, 'wait': 1, 'gate': 2}
#: The contract's per-instance array names and kinds.
ARRAYS = (('GroupStart', 'int'), ('GroupSize', 'int'), ('GroupMembers', 'int'),
          ('MemberDifficulty', 'int'), ('MemberPlayers', 'int'),
          ('GroupDifficulty', 'int'), ('GroupPlayers', 'int'),
          ('GroupUnitStart', 'int'), ('GroupUnitSize', 'int'), ('UnitProto', 'proto'),
          ('UnitCount', 'int'), ('UnitEndlessAdd', 'int'),
          ('RiftGroup', 'int'), ('RiftAt', 'float'), ('RiftWave', 'int'), ('RiftBoth', 'bool'),
          ('RiftPick', 'int'), ('RiftLaunchAfter', 'float'), ('RiftLaunchAlive', 'float'),
          ('RiftAlive', 'float'), ('RiftRepeat', 'int'),
          #: per wave of a loop (WaveCount): each group's rows and each member's chance
          ('GroupWaveStart', 'int'), ('GroupWaveSize', 'int'), ('RowProto', 'proto'),
          ('RowCount', 'int'), ('RowEndlessAdd', 'int'), ('RowPromote', 'float'),
          ('RowDifficulty', 'int'), ('RowPauseKind', 'int'), ('RowPauseValue', 'float'),
          ('MemberChance', 'float'),
          #: read back only by Rift Lord
          ('GroupName', 'str'),
          #: one rift per entry, read when GroupStart is empty (older rows, crystal.rows_*)
          ('RiftIndex', 'int'))
#: The arrays written only with a wave count.
PER_WAVE = ('GroupWaveStart', 'GroupWaveSize', 'RowProto', 'RowCount', 'RowEndlessAdd',
            'RowPromote', 'RowDifficulty', 'RowPauseKind', 'RowPauseValue', 'MemberChance')
#: The int the waves of one loop are written in.
WAVE_COUNT = 'WaveCount'
PROTO_STRUCT = 'OMDSoftProtoPtr'


def _num(raw, key, label, whole=True, blank=False):
    """A validated number, or None for a blank when `blank`."""
    if blank and (raw is None or raw == ''):
        return None
    lo, hi = RANGES[key]
    try:
        v = float(raw)
    except (TypeError, ValueError):
        raise ValueError('%s must be a number' % label)
    if v != v or not lo <= v <= hi:
        raise ValueError('%s must be between %g and %g' % (label, lo, hi))
    return int(round(v)) if whole else round(v, 4)


def _choice(raw, allowed, default):
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return default
    return v if v in allowed else default


def _pause(raw):
    """A row pause ({kind: 'wait'|'gate', value}) as a plan keeps it, or None."""
    if not isinstance(raw, dict) or raw.get('kind') not in ('wait', 'gate'):
        return None
    return {'kind': raw['kind'], 'value': _num(raw.get('value') or 0, raw['kind'],
                                               'Pause', False)}


def clean_row(raw):
    """A group's enemy row (the Onslaught Editor's spawn row) as a plan keeps it, or None."""
    if not isinstance(raw, dict):
        return None
    return {'unit': str(raw.get('unit') or '').strip() or None,
            'count': _num(raw.get('count', 1) or 0, 'rowCount', 'Count'),
            'addCount': _num(raw.get('addCount') or 0, 'addCount', 'Endless Add'),
            'promote': _num(0.5 if raw.get('promote') is None else raw['promote'], 'promote',
                            'Promote', False),
            'difficulty': _choice(raw.get('difficulty'), DIFFICULTIES, ANY),
            'before': _pause(raw.get('before')), 'after': _pause(raw.get('after'))}


def _chances(raw):
    """A rift's chance per wave ([%], blank = CHANCE_DEFAULT), or None for none given."""
    if not isinstance(raw, list):
        return None
    return [CHANCE_DEFAULT if v is None or v == '' else _num(v, 'chance', 'Chance', False)
            for v in raw[:WAVES_MAX]]


def clean_groups(raw):
    """The groups as a plan keeps them, validated; ValueError on a bad field."""
    out = []
    for g in (raw or [])[:GROUPS_MAX]:
        if not isinstance(g, dict):
            continue
        name = str(g.get('name') or '').strip()[:NAME_MAX] or 'Group %d' % (len(out) + 1)
        rifts, seen = [], set()
        for r in (g.get('rifts') or [])[:RIFTS_MAX]:
            sp = r.get('spawner') if isinstance(r, dict) else r
            if not isinstance(sp, (int, float)) or int(sp) in seen:
                continue
            seen.add(int(sp))
            r = r if isinstance(r, dict) else {}
            rifts.append({'spawner': int(sp),
                          'difficulty': _choice(r.get('difficulty'), DIFFICULTIES, ANY),
                          'players': _choice(r.get('players'), PLAYERS, 0)})
            chances = _chances(r.get('chances'))
            if chances is not None:
                rifts[-1]['chances'] = chances
        minions = []
        for m in (g.get('minions') or [])[:UNITS_MAX]:
            if not isinstance(m, dict):
                continue
            minions.append({'unit': str(m.get('unit') or '').strip() or None,
                            'count': _num(m.get('count', 1), 'count', 'Count'),
                            'addCount': _num(m.get('addCount', 0) or 0, 'addCount',
                                             'Endless Add')})
        triggers = []
        for t in (g.get('triggers') or [])[:TRIGGERS_MAX]:
            if not isinstance(t, dict):
                continue
            triggers.append({'at': _num(t.get('at'), 'at', LABELS['at'], False, True),
                             'wave': _num(t.get('wave'), 'wave', LABELS['wave'], True, True),
                             'alive': _num(t.get('alive') or 0, 'alive', LABELS['alive'],
                                           False),
                             'both': bool(t.get('both')),
                             'open': _num(t.get('open', 1) or 1, 'open', LABELS['open']),
                             'launchAfter': _num(t.get('launchAfter') or 0, 'launchAfter',
                                                 LABELS['launchAfter'], False),
                             'launchAlive': _num(t.get('launchAlive') or 0, 'launchAlive',
                                                 LABELS['launchAlive'], False),
                             'repeat': _num(t.get('repeat') or 0, 'repeat',
                                            LABELS['repeat'])})
        out.append({'name': name, 'rifts': rifts,
                    'difficulty': _choice(g.get('difficulty'), DIFFICULTIES, ANY),
                    'players': _choice(g.get('players'), PLAYERS, 0),
                    'minions': minions, 'triggers': triggers})
        #: the enemy rows of each wave of a loop, as the Onslaught Editor's Rift Group tab has them
        if isinstance(g.get('waves'), list):
            out[-1]['waves'] = [[r for r in (clean_row(x) for x in (rows or [])[:UNITS_MAX]) if r]
                                if isinstance(rows, list) else []
                                for rows in g['waves'][:WAVES_MAX]]
    return out


def sends_minions(group):
    """Whether a group sends anything: older rows, or a row in any wave."""
    return bool(group.get('minions')) or any(group.get('waves') or [])


def wave_count(settings):
    """Waves of one onslaught loop the Crystal's per-wave arrays cover (0: none, the older
    per-group rows apply on every launch)."""
    try:
        n = int(settings.get('waveCount') or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, min(WAVES_MAX, n))


def from_rows(rows):
    """Groups of one rift each from a plan's older `rifts` rows ({at, spawner, unit, count})."""
    out = []
    for i, r in enumerate(rows or []):
        if not isinstance(r, dict) or r.get('spawner') is None:
            continue
        out.append({'name': 'Rift Group %d' % (i + 1),
                    'rifts': [{'spawner': r['spawner'], 'difficulty': ANY, 'players': 0}],
                    'difficulty': ANY, 'players': 0,
                    'minions': [{'unit': r.get('unit'), 'count': int(r.get('count') or 1),
                                 'addCount': 0}] if r.get('unit') else [],
                    'triggers': [{'at': r.get('at'), 'wave': None, 'both': False, 'open': 1,
                                  'alive': NO_LAUNCH, 'launchAfter': NO_LAUNCH,
                                  'launchAlive': NO_LAUNCH, 'repeat': NO_REPEAT}]})
    return out


def groups(settings):
    """The groups of a Crystal's settings (crystal.values): its own, else from its rows."""
    return settings.get('groups') or from_rows(settings.get('rifts'))


def member_ids(settings):
    """Plan spawner ids of every rift in a group."""
    return sorted({r['spawner'] for g in groups(settings) for r in g.get('rifts') or []})


def _index_of(spawners):
    """{plan spawner id: indexInLevel} of the plan's unstable rifts (numbered by
    planactors.emit at build)."""
    out = {int(sp['id']): int(sp.get('index') or 0) for sp in spawners or []
           if isinstance(sp, dict) and sp.get('unstable') and sp.get('id') is not None}
    #: the level's own Unstable Rifts (an adopted Crystal's, crystal.adopt): -indexInLevel
    return _with_level(out)


def _pause_of(rows, i):
    """(RowPauseKind, RowPauseValue) before row i: the first row's own `before`, else the
    row above's `after`."""
    p = rows[i].get('before') if i == 0 else rows[i - 1].get('after')
    if not isinstance(p, dict) or p.get('kind') not in PAUSE_KINDS:
        return 0, 0.0
    return PAUSE_KINDS[p['kind']], round(float(p.get('value') or 0), 4)


class _with_level(dict):
    """{plan spawner id: indexInLevel}, where a negative id is the level's own rift -id."""

    def get(self, key, default=None):
        if isinstance(key, int) and key < 0 and not dict.__contains__(self, key):
            return -key
        return dict.get(self, key, default)

    def __getitem__(self, key):
        if isinstance(key, int) and key < 0 and not dict.__contains__(self, key):
            return -key
        return dict.__getitem__(self, key)

    def __contains__(self, key):
        return (isinstance(key, int) and key < 0) or dict.__contains__(self, key)


def arrays(settings, plan, guids):
    """{array name: [values]} the Crystal is written with; {} with no group. Members whose
    spawner is not an unstable rift, and unit rows naming no known minion, are left out.
    With a wave count (wave_count), each group's rows and each member's chance per wave of a
    loop too; MemberChance is CHANCE_DEFAULT past the end of a rift's chances."""
    gs = groups(settings)
    if not gs:
        return {}
    index = _index_of((plan or {}).get('spawners'))
    waves = wave_count(settings)
    out = {name: [] for name, _k in ARRAYS if name != 'RiftIndex'}
    for gi, g in enumerate(gs):
        out['GroupName'].append(str(g.get('name') or 'Group %d' % (gi + 1)))
        members = [r for r in g['rifts'] if index.get(r['spawner'])]
        out['GroupStart'].append(len(out['GroupMembers']))
        out['GroupSize'].append(len(members))
        for r in members:
            out['GroupMembers'].append(index[r['spawner']])
            out['MemberDifficulty'].append(int(r.get('difficulty', ANY)))
            out['MemberPlayers'].append(int(r.get('players', 0)))
        out['GroupDifficulty'].append(int(g.get('difficulty', ANY)))
        out['GroupPlayers'].append(int(g.get('players', 0)))
        units = [m for m in g.get('minions') or [] if m.get('unit') in guids]
        out['GroupUnitStart'].append(len(out['UnitProto']))
        out['GroupUnitSize'].append(len(units))
        for m in units:
            out['UnitProto'].append(tuple(guids[m['unit']]))
            out['UnitCount'].append(int(m['count']))
            out['UnitEndlessAdd'].append(int(m.get('addCount') or 0))
        for t in g.get('triggers') or []:
            out['RiftGroup'].append(gi)
            out['RiftAt'].append(NO_HEALTH if t.get('at') is None else round(float(t['at']), 4))
            out['RiftWave'].append(NO_WAVE if t.get('wave') is None else int(t['wave']))
            out['RiftAlive'].append(round(float(t.get('alive') or NO_LAUNCH), 4))
            out['RiftBoth'].append(bool(t.get('both')))
            out['RiftPick'].append(int(t.get('open') or 1))
            out['RiftLaunchAfter'].append(round(float(t.get('launchAfter') or NO_LAUNCH), 4))
            out['RiftLaunchAlive'].append(round(float(t.get('launchAlive') or NO_LAUNCH), 4))
            out['RiftRepeat'].append(int(t.get('repeat') or NO_REPEAT))
    if waves:
        for g in gs:
            per_wave = g.get('waves') or []
            for w in range(waves):
                rows = [r for r in (per_wave[w] if w < len(per_wave) else []) or []
                        if r.get('unit') in guids]
                out['GroupWaveStart'].append(len(out['RowProto']))
                out['GroupWaveSize'].append(len(rows))
                for i, r in enumerate(rows):
                    kind, value = _pause_of(rows, i)
                    out['RowProto'].append(tuple(guids[r['unit']]))
                    out['RowCount'].append(int(r.get('count') or 0))
                    out['RowEndlessAdd'].append(int(r.get('addCount') or 0))
                    out['RowPromote'].append(round(float(0.5 if r.get('promote') is None
                                                         else r['promote']), 4))
                    out['RowDifficulty'].append(int(r.get('difficulty', ANY)))
                    out['RowPauseKind'].append(kind)
                    out['RowPauseValue'].append(value)
            for r in g['rifts']:
                if not index.get(r['spawner']):
                    continue
                chances = r.get('chances') or []
                out['MemberChance'].extend(
                    round(float(chances[w]), 4) if w < len(chances) else CHANCE_DEFAULT
                    for w in range(waves))
        out[WAVE_COUNT] = waves
    else:
        for name in PER_WAVE:
            del out[name]
    return out


def _array_payload(builder, kind, values):
    if kind == 'int':
        return struct.pack('<i', len(values)) + b''.join(struct.pack('<i', v) for v in values), \
            'IntProperty'
    if kind == 'float':
        return struct.pack('<i', len(values)) + b''.join(struct.pack('<f', v) for v in values), \
            'FloatProperty'
    if kind == 'bool':
        return struct.pack('<i', len(values)) + bytes(1 if v else 0 for v in values), \
            'BoolProperty'
    if kind == 'str':
        return struct.pack('<i', len(values)) + b''.join(_fstring(v) for v in values), \
            'StrProperty'
    raise ValueError(kind)


def _fstring(text):
    """An FString: ASCII as length + bytes + NUL, anything else as UTF-16 (negative length)."""
    text = str(text)
    try:
        raw = text.encode('ascii') + b'\0'
        return struct.pack('<i', len(raw)) + raw
    except UnicodeEncodeError:
        raw = text.encode('utf-16-le') + b'\0\0'
        return struct.pack('<i', -(len(raw) // 2)) + raw


def _read_fstring(data, at):
    """(text, next offset) of an FString at `at`."""
    n = struct.unpack_from('<i', data, at)[0]
    if n >= 0:
        return data[at + 4:at + 4 + n].rstrip(b'\0').decode('latin-1'), at + 4 + n
    raw = data[at + 4:at + 4 - 2 * n]
    return raw.decode('utf-16-le').rstrip('\0'), at + 4 - 2 * n


def write(builder, actor, got):
    """Write `arrays()` output on the placed Crystal; an empty array is left out (reads as
    none)."""
    from riftlord.mapeditor import crystal
    kinds = dict(ARRAYS)
    for name, values in got.items():
        if name == WAVE_COUNT:
            builder.set_property(actor, name, 'IntProperty', struct.pack('<i', int(values)))
            continue
        if not values:
            continue
        if kinds[name] == 'proto':
            builder.struct_array(actor, name, PROTO_STRUCT,
                                 [crystal.proto_ptr(builder, g)[0:] for g in values])
            continue
        payload, inner = _array_payload(builder, kinds[name], values)
        builder.set_property(actor, name, 'ArrayProperty', payload, inner)


def read(pkg, actor):
    """{array name: [values]} of the contract's arrays on a placed Crystal, read back."""
    from riftlord.mapeditor import kitactors as K
    names = K._names(pkg)
    data = pkg.exports[actor - 1]['data']
    kinds = dict(ARRAYS)
    out = {}
    for name, _num, kind, start, at, size, _e in K._walk(data, names):
        if name == WAVE_COUNT and kind == 'IntProperty':
            out[name] = struct.unpack_from('<i', data, at)[0]
            continue
        if name not in kinds or kind != 'ArrayProperty':
            continue
        out[name] = decode(kinds[name], data, at)
    return out


def decode(k, data, at=0):
    """An array property's values from its payload at `at` (the element count first), as
    ARRAYS kind `k` reads them."""
    n = struct.unpack_from('<i', data, at)[0]
    if k == 'str':
        vals, body = [], at + 4
        for _i in range(n):
            text, body = _read_fstring(data, body)
            vals.append(text)
        return vals
    if k == 'int':
        return list(struct.unpack_from('<%di' % n, data, at + 4))
    if k == 'float':
        return [round(v, 4) for v in struct.unpack_from('<%df' % n, data, at + 4)]
    if k == 'bool':
        return [bool(b) for b in data[at + 4:at + 4 + n]]
    #: count, the 49-byte inner tag, then each element: Guid tag (49) + 16 + None (8)
    body, vals = at + 4 + 49, []
    for _i in range(n):
        vals.append(struct.unpack_from('<4i', data, body + 49))
        body += 49 + 16 + 8
    return vals


def problems(settings, plan, spec, guids):
    """[text]: groups the build cannot write as asked."""
    out = []
    index = _index_of((plan or {}).get('spawners'))
    members = set()
    for g in groups(settings):
        if not g['rifts']:
            out.append('Rift Tear group %s has no rifts' % g['name'])
        for r in g['rifts']:
            if r['spawner'] not in index:
                out.append('Rift Tear group %s names a rift that is not an unstable rift in '
                           'the level' % g['name'])
            elif index[r['spawner']]:
                members.add(index[r['spawner']])
        for m in (g.get('minions') or []) + [r for rows in g.get('waves') or [] for r in rows]:
            if m.get('unit') and m['unit'] not in guids:
                out.append('Rift Tear group %s sends %s, which is not a minion'
                           % (g['name'], m['unit']))
    for n in sorted(members):
        for i, wave in enumerate((spec or {}).get('waves') or []):
            if (wave.get('spawns') or {}).get(str(n)):
                out.append('wave %d sends units through unstable rift %d, which the Crystal '
                           'opens' % (i + 1, n))
    return out


def warnings(settings):
    """[text]: what builds but may not do what the designer means, including groups whose
    onslaught side (minions, triggers) is not set yet: such a group never opens."""
    out = []
    for g in groups(settings):
        if not sends_minions(g):
            out.append('Rift Group %s sends no minions yet' % g['name'])
        if any(not m.get('unit') for m in (g.get('minions') or [])
               + [r for rows in g.get('waves') or [] for r in rows]):
            out.append('Rift Group %s has a minion row with no unit' % g['name'])
        if not g.get('triggers'):
            out.append('Rift Group %s has no trigger yet, so it never opens' % g['name'])
        for t in g.get('triggers') or []:
            if t.get('at') is None and t.get('wave') is None and not t.get('alive'):
                out.append('Rift Group %s has a trigger with no When (Wave or Crystal Health)'
                           % g['name'])
        out += _open_problems(g)
    return out


def _open_problems(group):
    """[text]: the group's triggers whose Open is more Unstable Rifts than it has."""
    n = len(group.get('rifts') or [])
    return ['Rift Group %s: Open %d Rifts, but it has %d Unstable Rift%s'
            % (group.get('name'), int(t.get('open') or 1), n, '' if n == 1 else 's')
            for t in group.get('triggers') or [] if int(t.get('open') or 1) > n]


def spec_problems(spec):
    """[text]: waves of an Onslaught Editor spec that send units through a rift its Rift
    Tear groups (spec['riftTear'], editor_view's shape) open."""
    tear = (spec or {}).get('riftTear')
    if not isinstance(tear, dict):
        return []
    index = {r.get('id'): r.get('index') for r in tear.get('rifts') or [] if isinstance(r, dict)}
    members = {index.get(r.get('spawner')) for g in tear.get('groups') or []
               for r in g.get('rifts') or [] if isinstance(r, dict)} - {None, 0}
    out = []
    for g in tear.get('groups') or []:
        if isinstance(g, dict):
            try:
                out += _open_problems(g)
            except (TypeError, ValueError):
                out.append('Rift Group %s: Open must be a whole number' % g.get('name'))
            waves = len(spec.get('waves') or [])
            for t in g.get('triggers') or []:
                w = t.get('wave') if isinstance(t, dict) else None
                if w not in (None, '') and not (isinstance(w, (int, float)) and 1 <= w <= waves):
                    out.append('Rift Group %s: When Wave %s, but the onslaught has %d wave%s'
                               % (g.get('name'), w, waves, '' if waves == 1 else 's'))
    for n in sorted(members):
        for i, wave in enumerate(spec.get('waves') or []):
            if (wave.get('spawns') or {}).get(str(n)):
                out.append('wave %d sends units through unstable rift %d, which the Crystal '
                           'opens' % (i + 1, n))
    return out


# -- the Onslaught Editor's half --------------------------------------------------------------

#: The fields of a group, a rift and the Crystal the Onslaught Editor owns.
ONSLAUGHT_GROUP = ('difficulty', 'players', 'minions', 'triggers', 'waves')
ONSLAUGHT_RIFT = ('difficulty', 'players', 'chances')


def _health(values):
    """The Onslaught Editor's health fields from the Crystal's `values` (crystal.HEALTH_KEYS:
    an unset override None), with each field's range and the class's defaults."""
    from riftlord.mapeditor import crystal
    base = crystal.class_defaults()
    return {'values': {k: values.get(k) for k in crystal.HEALTH_KEYS},
            'defaults': {k: base[prop] for k, prop in crystal.HEALTH_KEYS.items()},
            'ranges': {k: list(crystal.RANGES[k]) for k in crystal.HEALTH_KEYS}}


def editor_view(plan):
    """What the Onslaught Editor edits of the plan's Crystal, or None without one:
    {groups: [...], riftGap, waveCount, rifts: [{id, index, label}],
     health: {values: {mapDifficulty, pointHealth, pointDifficultyShare}, defaults: {..},
              ranges: {key: [lo, hi]}}}.
    A group's `waves` are its enemy rows per wave of a loop; a rift's `chances` its % per
    wave."""
    from riftlord.mapeditor import crystal
    item = crystal.piece(plan)
    if not item:
        return None
    import copy
    v = crystal.values(item)
    index = dict(_index_of((plan or {}).get('spawners')))
    for sid in member_ids(v):
        if sid < 0:
            index[sid] = -sid
    return {'groups': copy.deepcopy(groups(v)), 'riftGap': v.get('riftGap'),
            'waveCount': wave_count(v),
            'rifts': [{'id': i, 'index': n, 'label': 'Unstable Rift %d' % (n or i)}
                      for i, n in sorted(index.items())],
            'health': _health(v)}


def _row_view(rows, i, values):
    """A spawn row of the Onslaught Editor from read-back row `i` of a group's wave, with the
    pause before it on itself (first row) or on the row above."""
    kinds = {v: k for k, v in PAUSE_KINDS.items()}
    kind = kinds.get(values['RowPauseKind'][i]) if i < len(values['RowPauseKind']) else None
    pause = {'kind': kind, 'value': values['RowPauseValue'][i]} if kind else None
    row = {'unit': values['unit'], 'count': values['RowCount'][i],
           'addCount': values['RowEndlessAdd'][i], 'promote': values['RowPromote'][i],
           'difficulty': values['RowDifficulty'][i], 'before': None, 'after': None}
    if rows:
        rows[-1]['after'] = pause
    else:
        row['before'] = pause
    return row


def level_view(arrays, scalars=None, spawner_units=None):
    """editor_view's shape from a built Crystal's arrays (read back, map_report's `pieces`),
    for a mission opened without its record: rifts are named by their indexInLevel. An older
    Crystal's rift (RiftIndex) sends its spawner's own units (`spawner_units`: {indexInLevel:
    (Guid, count)}), as the Crystal falls back to them."""
    from riftlord.mapeditor import crystal
    a = {name: list(arrays.get(name) or []) for name, _k in ARRAYS}
    scalars = scalars or {}
    names = {tuple(g): u for u, g in crystal.unit_guids().items()}
    pad = lambda key, n, fill: (a[key] + [fill] * n)[:n]   # noqa: E731
    waves = int(scalars.get(WAVE_COUNT) or 0)
    out = []
    if not a['GroupStart'] and a['RiftIndex']:
        n = len(a['RiftIndex'])
        for i, idx in enumerate(a['RiftIndex']):
            own = (spawner_units or {}).get(idx)
            out.append({'name': 'Rift Group %d' % (i + 1),
                        'rifts': [{'spawner': idx, 'difficulty': ANY, 'players': 0}],
                        'difficulty': ANY, 'players': 0,
                        'minions': [{'unit': names.get(tuple(own[0])), 'count': int(own[1]),
                                     'addCount': 0}] if own and names.get(tuple(own[0]))
                        else [],
                        'triggers': [{'at': None if pad('RiftAt', n, NO_HEALTH)[i] >= NO_HEALTH
                                      else pad('RiftAt', n, NO_HEALTH)[i],
                                      'wave': pad('RiftWave', n, 0)[i] or None, 'alive': 0.0,
                                      'both': False, 'open': 1, 'launchAfter': 0.0,
                                      'launchAlive': 0.0, 'repeat': NO_REPEAT}]})
    member = 0
    for gi, start in enumerate(a['GroupStart']):
        size = pad('GroupSize', len(a['GroupStart']), 0)[gi]
        rifts = []
        for m in range(start, start + size):
            r = {'spawner': a['GroupMembers'][m] if m < len(a['GroupMembers']) else 0,
                 'difficulty': pad('MemberDifficulty', m + 1, ANY)[m],
                 'players': pad('MemberPlayers', m + 1, 0)[m]}
            if waves and a['MemberChance']:
                r['chances'] = pad('MemberChance', (member + 1) * waves,
                                   CHANCE_DEFAULT)[member * waves:(member + 1) * waves]
            rifts.append(r)
            member += 1
        g = {'name': (a['GroupName'][gi] if gi < len(a['GroupName']) else '')
                 or 'Rift Group %d' % (gi + 1),
             'rifts': rifts,
             'difficulty': pad('GroupDifficulty', gi + 1, ANY)[gi],
             'players': pad('GroupPlayers', gi + 1, 0)[gi], 'minions': [], 'triggers': []}
        us = pad('GroupUnitStart', gi + 1, 0)[gi]
        for u in range(us, us + pad('GroupUnitSize', gi + 1, 0)[gi]):
            if u < len(a['UnitProto']):
                g['minions'].append({'unit': names.get(tuple(a['UnitProto'][u])),
                                     'count': pad('UnitCount', u + 1, 1)[u],
                                     'addCount': pad('UnitEndlessAdd', u + 1, 0)[u]})
        if waves and a['GroupWaveStart']:
            g['waves'] = []
            for w in range(waves):
                slot = gi * waves + w
                start_row = pad('GroupWaveStart', slot + 1, 0)[slot]
                rows = []
                for i in range(start_row, start_row + pad('GroupWaveSize', slot + 1, 0)[slot]):
                    if i >= len(a['RowProto']):
                        break
                    values = {k: pad(k, i + 1, d) for k, d in (
                        ('RowCount', 0), ('RowEndlessAdd', 0), ('RowPromote', 0.5),
                        ('RowDifficulty', ANY), ('RowPauseKind', 0), ('RowPauseValue', 0.0))}
                    values['unit'] = names.get(tuple(a['RowProto'][i]))
                    rows.append(_row_view(rows, i, values))
                g['waves'].append(rows)
        out.append(g)
    n = len(a['RiftGroup'])
    for e, gi in enumerate(a['RiftGroup']):
        if gi >= len(out) or not a['GroupStart']:
            continue
        at = pad('RiftAt', n, NO_HEALTH)[e]
        out[gi]['triggers'].append({
            'at': None if at >= NO_HEALTH else at, 'wave': pad('RiftWave', n, 0)[e] or None,
            'alive': pad('RiftAlive', n, 0.0)[e], 'both': pad('RiftBoth', n, False)[e],
            'open': pad('RiftPick', n, 1)[e] or 1,
            'launchAfter': pad('RiftLaunchAfter', n, 0.0)[e],
            'launchAlive': pad('RiftLaunchAlive', n, 0.0)[e],
            'repeat': pad('RiftRepeat', n, NO_REPEAT)[e]})
    indices = sorted({r['spawner'] for g in out for r in g['rifts'] if r['spawner']})
    #: the health variables as written; MapDifficulty not written is the class default
    health = {k: scalars.get(prop) for k, prop in crystal.HEALTH_KEYS.items()}
    if health[crystal.MAP_DIFFICULTY] is None:
        health[crystal.MAP_DIFFICULTY] = crystal.class_defaults()[
            crystal.HEALTH_KEYS[crystal.MAP_DIFFICULTY]]
    return {'groups': out, 'riftGap': scalars.get('RiftGap', crystal.CLASS_FLOATS['RiftGap']),
            'waveCount': waves,
            'rifts': [{'id': i, 'index': i, 'label': 'Unstable Rift %d' % i} for i in indices],
            'health': _health(health)}


def merge_editor(plan, view):
    """`plan` with the Onslaught Editor's fields (`view`, editor_view's shape) taken onto its
    Crystal: a group by position, a rift by spawner; the Map Editor's names and rift lists
    are kept. Returns whether anything changed."""
    from riftlord.mapeditor import crystal
    item = crystal.piece(plan)
    if not item or not isinstance(view, dict):
        return False
    settings = item.setdefault(crystal.KEY, {})

    def state():
        return repr((settings.get('groups'), settings.get('riftGap'), settings.get('rifts'),
                     settings.get('waveCount'),
                     [crystal.values(item)[k] for k in crystal.HEALTH_KEYS]))
    before = state()
    mine = groups(crystal.values(item))
    theirs = clean_groups(view.get('groups'))
    for g, t in zip(mine, theirs):
        for k in ONSLAUGHT_GROUP:
            if k in t:
                g[k] = t[k]
        by_sp = {r['spawner']: r for r in t['rifts']}
        for r in g['rifts']:
            for k in ONSLAUGHT_RIFT:
                if k in by_sp.get(r['spawner'], {}):
                    r[k] = by_sp[r['spawner']][k]
    settings['groups'] = mine
    settings.pop('rifts', None)
    if view.get('riftGap') is not None:
        settings['riftGap'] = crystal._number(view['riftGap'], 'riftGap',
                                              crystal.LABELS['riftGap'])
    if view.get('waveCount') is not None and (wave_count(view) or 'waveCount' in settings):
        settings['waveCount'] = wave_count(view)
    health = view.get('health') if isinstance(view.get('health'), dict) else {}
    if isinstance(health.get('values'), dict):
        for key in crystal.HEALTH_KEYS:
            settings.pop(key, None)
        settings.update(crystal.clean_health(health['values']))
    for old in crystal.OLD_HEALTH_KEYS:
        settings.pop(old, None)
    return before != state()
