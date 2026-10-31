"""The Mission Events piece: mission events on a host whose level script never listens.

A host's events work only where its persistent level script binds the coordinator's
delegates (riftlord/hostevents.py); 19 of the 33 shipped maps bind none. The piece is a
placed actor of a Blueprint class built per mission with bpkit. It listens to the game
state itself, so neither the coordinator nor the level script changes:

- **music change**: on its wave (the game state's `CurrentWave` reached, no break
  active, enemies spawned) it finds the playing battle loop (an AudioComponent named
  `MX_OMD3_*_LP_SC`, not an intro, under an AmbientSound of the `_Audio` sublevel) and
  calls `SetSound` with the chosen track's loop cue. SetSound restarts the component
  with the new cue and keeps its instance parameters (the break/full crossfade).
- **boss reaction**: it binds the game state's `OnBossSpawned` (a NetMulticast, so it
  fires on every machine) and, once the event's wave is reached, plays the boss's
  shipped reaction cues in order (each started by the previous one's OnAudioFinished).
- **unstable rift**: a plan spawner with `unstable` is placed as Scenario_18's red rift
  spawner class (`planactors.UNSTABLE_CLASS`), closed (`bIsActive` false). On an open
  event's wave, once the break is over, the piece finds the rift by its index and calls
  its `Open Rift`; on a close event's wave it calls `Close Rift`. Both are the class's
  own functions (effects, sound, minimap ping, local camera shake, `bIsActive` on the
  server), as Scenario_18's level script calls them.
- **dialogue**: each line is a silent SoundWave of the mission's own carrying the line's
  subtitle cue (riftlord/build/world/captions.py), played with `SpawnSound2D` on every
  machine, so the game's closed captions draw it where and how they draw shipped lines,
  and only with the player's Closed Captioning on. A line plays as its wave starts (the
  game state's wave in play reaches it), in the break after its wave, or after the line
  before it ends, each after its own delay (a timer); none play with the player's
  Scenario Dialogue setting off, as the shipped story lines do not, nor in Endless or
  Scramble (the server decides from the game difficulty and the game instance and
  replicates it, so a piece with dialogue replicates).

Only the events the host cannot do itself go to the piece; the rest keep the host's
own wiring. Plan data: one `added` item `{kind: 'events', name, matrix}` per level.
"""

import os
import struct

#: The `added` kind of the piece.
KIND = 'events'
#: Its name in the Map Editor.
LABEL = 'Mission Events'
#: Donor of the placed actor: a plain Actor with one SceneComponent (as
#: omui-dev/build/menumap.py places OrcsMustUI's ModActor, proven in game).
DONOR_LEVEL = '/Game/Maps/UI/Levels/MainMenu_V2'
DONOR_NAME = 'EnemiesSpawnRoot'
#: The placed actor's export name.
PLACED_NAME = 'RiftLordMissionEvents'
#: Seconds between the piece's checks of the game state.
TICK_SECONDS = 0.5
#: The unstable rift's Blueprint class (planactors.UNSTABLE_CLASS).
RIFT_CLASS_PATH = '/Game/Game/BP_OMDAISpawner_VorwickRift.BP_OMDAISpawner_VorwickRift_C'
#: `Open Rift`'s Opening Delay: the class's own Kickoff Delay (its default object, 3.67 s),
#: so the opening waits for no voice line. Scenario_18 passes its Vorwick line's length.
RIFT_OPEN_DELAY = 3.67
#: Export meta offsets: class, template, first preload, the four preload counts.
CLASS_AT, TEMPLATE_AT = 0, 8
PRELOAD_FIRST_AT, PRELOAD_COUNTS_AT = 84, 88
SERIALIZATION_BEFORE_CREATE = 2

#: Reaction cues per boss unit, in play order. Measured from the four boss maps' level
#: scripts (`[VO] <Boss> Spawned` and its chained `Play <Boss> Line N`).
REACTION_LINES = {
    'OgreBasher': ('/Game/Sounds/Dialogue/Vorwick/Cue/DX_OMD3_Vorwick_Reaction_Ghorbash_SC',
                   '/Game/Sounds/Dialogue/Cygnus/Cue/DX_OMD3_Cygnus_Reaction_Ghorbash_SC'),
    'OrcBoss': ('/Game/Sounds/Dialogue/Cygnus/Cue/DX_OMD3_Cygnus_Reaction_Cragg_SC',
                '/Game/Sounds/Dialogue/Vorwick/Cue/DX_OMD3_Vorwick_Reaction_Cragg_SC'),
    'WraithBoss': ('/Game/Sounds/Dialogue/Kelsey/Cue/DX_OMD3_Kelsey_Mob_WraithBoss_SC',
                   '/Game/Sounds/Dialogue/Gabriella/Cue/DX_OMD3_Gabby_MobResponse_WraithBoss_SC',
                   '/Game/Sounds/Dialogue/Egan/Cue/DX_OMD3_Egan_MobResponse_WraithBoss_SC'),
    'KoboldKing': ('/Game/Sounds/Dialogue/Egan/Cue/DX_OMD3_Egan_Mob_KoboldKing_SC',
                   '/Game/Sounds/Dialogue/Kelsey/Cue/DX_OMD3_Kelsey_MobResponse_KoboldKing_SC',
                   '/Game/Sounds/Dialogue/Gabriella/Cue/DX_OMD3_Gabby_MobResponse_KoboldKing_SC'),
}


def editor_choices(level, rifts=''):
    """What the Map Editor's Mission Events list offers on `level`: {'host': the host's
    own capabilities (hostevents.capabilities, raw facts left out), 'piece': with the
    piece placed and the unstable rifts `rifts` ('3,5': spawner indices) (with_piece),
    'tracks': [{key, label}], 'bosses': [{name, label}]}. Until Setup has built bpkit's type
    data (riftlord.types.setup) the piece offers nothing beyond the host."""
    from riftlord.missions import hostevents
    from riftlord.build.world import levelmusic
    from riftlord.types import setup
    keep = ('reactions', 'music', 'rift')
    host = hostevents.capabilities(level)
    wanted = [int(r) for r in str(rifts or '').split(',') if r.strip().isdigit()]
    piece_caps = host if setup.unavailable() else hostevents.with_piece(host, wanted)
    try:
        from riftlord.onslaught import gamedata
        bosses = [{'name': u['name'], 'label': u['label']} for u in gamedata.units()
                  if u['boss'] and not u['scramble']]
    except Exception:                                # noqa: BLE001
        bosses = []
    return {'host': {k: host[k] for k in keep}, 'piece': {k: piece_caps[k] for k in keep},
            'tracks': [{'key': t['key'], 'label': t['label']} for t in levelmusic.choices()],
            'bosses': bosses}


def piece(plan):
    """The plan's Mission Events item, or None."""
    return next((a for a in (plan or {}).get('added') or []
                 if isinstance(a, dict) and a.get('kind') == KIND), None)


def clean_piece(item, matrix):
    """The piece as a plan keeps it."""
    return {'name': LABEL, 'kind': KIND, 'matrix': matrix}


def class_name(mod):
    return '%s_MissionEvents' % mod


def package_path(mod):
    """Package path of the piece's Blueprint in a mission."""
    return '/Game/Mods/%s/%s' % (mod, class_name(mod))


def unstable_rifts(plan):
    """Spawner indices of the plan's unstable rifts, sorted."""
    return sorted(int(sp.get('index') or 0) for sp in (plan or {}).get('spawners') or []
                  if isinstance(sp, dict) and sp.get('unstable') and int(sp.get('index') or 0) > 0)


def dialogue_path(mod, n):
    """Package path of a mission's dialogue line `n` (1-based, in plan order)."""
    return '/Game/Mods/%s/Dialogue/DX_%s_Line%d' % (mod, mod, n)


def dialogue_lines(plan):
    """[{n, wave, when, delay, speaker, text, duration}] of the plan's dialogue events in
    order, `duration` resolved (captions.derived_duration when unset)."""
    from riftlord.build.world import captions
    out = []
    for ev in (plan or {}).get('events') or []:
        if not isinstance(ev, dict) or ev.get('kind') != 'dialogue':
            continue
        text = str(ev.get('text') or '').strip()
        duration = ev.get('duration')
        out.append({'n': len(out) + 1, 'wave': int(ev.get('wave') or 1),
                    'when': ev.get('when') or 'start', 'delay': float(ev.get('delay') or 0),
                    'speaker': str(ev.get('speaker') or '').strip(), 'text': text,
                    'duration': captions.derived_duration(text) if duration in (None, '')
                    else round(float(duration), 1)})
    return out


def duties(level, plan):
    """{'music': [(wave, track)], 'reactions': [(wave, unit)], 'rifts': [(wave, index,
    open)], 'dialogue': [line]}: the plan's events the host cannot do itself, which the
    piece performs (every dialogue line: no host plays one). Empty without the piece."""
    out = {'music': [], 'reactions': [], 'rifts': [], 'dialogue': []}
    if piece(plan) is None:
        return out
    out['dialogue'] = dialogue_lines(plan)
    rifts = set(unstable_rifts(plan))
    from riftlord.missions import hostevents
    from riftlord.build.world import levelmusic
    caps = hostevents.capabilities(level)
    native = {r['unit'] for r in caps['reactions']}
    for ev in (plan or {}).get('events') or []:
        if not isinstance(ev, dict):
            continue
        wave = int(ev.get('wave') or 0)
        if ev.get('kind') == 'music' and ev.get('track') in levelmusic.TRACKS \
                and not caps['music']['available']:
            out['music'].append((wave, ev['track']))
        elif ev.get('kind') == 'boss' and ev.get('reaction') \
                and ev.get('unit') in REACTION_LINES and ev['unit'] not in native:
            out['reactions'].append((wave, ev['unit']))
        elif ev.get('kind') == 'rift' and int(ev.get('rift') or 0) in rifts:
            out['rifts'].append((wave, int(ev['rift']), bool(ev.get('open', True))))
    out['music'].sort()
    out['reactions'].sort()
    out['rifts'].sort(key=lambda r: (r[0], not r[2], r[1]))
    return out


def _asset(path, kind='SoundCue'):
    leaf = path.rsplit('/', 1)[-1]
    return 'Asset("%s\'%s.%s\'")' % (kind, path, leaf)


SCRIPT_HEAD = '''{rift_alias}Bound: bool = False
SawBreak: bool = False
Lines: list[SoundBase]
Line: int = 0
Voice: AudioComponent
{flags}

@override
def ReceiveTick(DeltaSeconds: float):
    Game = cast(OMDGameState, GetGameState(self))
    if not IsValid(Game):
        return
    if not self.Bound:
        self.Bound = True
        Game.OnBossSpawned.add(self, "BossSpawned")
{decide}{closes}    if Game.bIsGoBreakActive or Game.bIsTimedBreakActive:
        self.SawBreak = True
{breaks}        return
{seen}{opens}    if Game.EnemiesSpawned <= 0:
        return
{music}
{rift_fn}{say_fn}
def SwapMusic(Track: SoundBase) -> bool:
    Swapped = False
    GetAllActorsOfClass(self, AmbientSound, Found)
    for Each in Found:
        Parts = Each.K2_GetComponentsByClass(AudioComponent)
        for Part in Parts:
            Music = cast(AudioComponent, Part)
            if not IsValid(Music):
                continue
            if not IsValid(Music.Sound):
                continue
            if not Music.IsPlaying():
                continue
            Name = GetObjectName(Music.Sound)
            if not StartsWith(Name, "MX_OMD3_", ESearchCase.IgnoreCase):
                continue
            if not EndsWith(Name, "_LP_SC", ESearchCase.IgnoreCase):
                continue
            if Contains(Name, "_Intro_", False, False):
                continue
            Music.SetSound(Track)
            Swapped = True
    return Swapped


def BossSpawned(GameState: OMDGameState, bossProto: OMDSoftProtoPtr):
{reactions}    return


def NextLine():
    if self.Line >= len(self.Lines):
        return
    Cue = self.Lines[self.Line]
    self.Line = self.Line + 1
    self.Voice = SpawnSound2D(self, Cue, 1.0, 1.0, 0.0, None, False, True)
    if IsValid(self.Voice):
        self.Voice.OnAudioFinished.add(self, "NextLine")
'''

MUSIC_CASE = '''    if not self.Music{n} and Game.CurrentWave >= {wave}:
        if self.SwapMusic({cue}):
            self.Music{n} = True
'''

RIFT_ALIAS = '''RiftSpawner = Blueprint("{rift_class}")

'''

RIFT_FN = '''
def SetRift(Index: int, Open: bool) -> bool:
    Done = False
    GetAllActorsOfClass(self, RiftSpawner, Rifts)
    for Each in Rifts:
        Rift = cast(RiftSpawner, Each)
        if not IsValid(Rift):
            continue
        if Rift.indexInLevel != Index:
            continue
        if Open:
            Rift.Open_Rift({delay})
        else:
            Rift.Close_Rift()
        Done = True
    return Done

'''

RIFT_OPEN_CASE = '''    if not self.Rift{n} and Game.CurrentWave >= {wave}:
        if self.SawBreak or Game.EnemiesSpawned > 0:
            if self.SetRift({index}, True):
                self.Rift{n} = True
'''

RIFT_CLOSE_CASE = '''    if not self.Rift{n} and Game.CurrentWave >= {wave}:
        if self.SetRift({index}, False):
            self.Rift{n} = True
'''

#: The wave in play: the game state's wave once a break has been seen or enemies have
#: come (so the build phase before wave 1 is not wave 1), kept through each break.
SEEN = '''    if self.SawBreak or Game.EnemiesSpawned > 0:
        self.Seen = Game.CurrentWave
'''

#: Whether this mission's dialogue plays, decided once on the server and replicated (Talk:
#: 0 not yet, 1 it plays, 2 not): never in Endless, which plays no campaign dialogue or
#: intro, nor in Scramble (user 2026-10-30; the checks the North Wings intro's Mode makes).
DECIDE = '''    if self.Talk == 0:
        if IsServer(self):
            self.Decide()
'''

DECIDE_FN = '''
def Decide():
    Instance = cast(OMDGameInstance, GetGameInstance(self))
    if not IsValid(Instance):
        return
    OMDDifficultyBlueprintLibrary.GetGameDifficulty(Current)
    if Current == EGameDifficulty.Endless:
        self.Talk = 2
        return
    if Instance.IsInRandomModeCampaign():
        self.Talk = 2
        return
    self.Talk = 1

'''

DIALOGUE_BREAK_CASE = '''        if not self.Heard{n} and self.Seen == {wave} and self.Talk == 1:
            self.Heard{n} = True
            {call}
'''

DIALOGUE_START_CASE = '''    if not self.Heard{n} and self.Seen == {wave} and self.Talk == 1:
        self.Heard{n} = True
        {call}
'''

SPEAK_FN = '''
def Speak(Line: SoundBase):
    Settings = cast(OMDGameUserSettings, GameUserSettings.GetGameUserSettings())
    if IsValid(Settings):
        if not Settings.GetPlayScenarioDialogue():
            return
    self.Spoken = SpawnSound2D(self, Line, 1.0, 1.0, 0.0, None, False, True)

'''

SAY_FN = '''
def Say{n}():
    if self.Said{n} or self.Talk != 1:
        return
    self.Said{n} = True
    self.Speak({line})
{follow}'''

SAY_FOLLOW = '''    K2_SetTimer(self, "Say{m}", {after}, False)
'''


def _say_call(n, delay):
    """Line `n` played now, or after `delay` seconds (a timer; a timer of 0 s never runs)."""
    if delay > 0:
        return 'K2_SetTimer(self, "Say%d", %r, False)' % (n, float(delay))
    return 'self.Say%d()' % n


REACTION_CASE = '''    if not self.Reacted{n} and GameState.CurrentWave >= {wave}:
        self.Reacted{n} = True
        self.Lines = [{cues}]
        self.Line = 0
        self.NextLine()
        return
'''


def script(work, mod=None):
    """The piece's bpkit script for `duties`; `mod` names its dialogue lines' packages."""
    from riftlord.build.world import levelmusic
    flags, music, reactions = [], '', ''
    lines = work.get('dialogue') or []
    breaks = starts = say_fn = ''
    if lines:
        flags.append('Seen: int = 0')
        flags.append('Talk: Replicated[int]')
        flags.append('Spoken: AudioComponent')
        say_fn = SPEAK_FN + DECIDE_FN
    for i, line in enumerate(lines):
        n = line['n']
        flags.append('Said%d: bool = False' % n)
        if line['when'] != 'after':
            flags.append('Heard%d: bool = False' % n)
            case = DIALOGUE_BREAK_CASE if line['when'] == 'break' else DIALOGUE_START_CASE
            text = case.format(n=n, wave=int(line['wave']), call=_say_call(n, line['delay']))
            if line['when'] == 'break':
                breaks += text
            else:
                starts += text
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        follow = SAY_FOLLOW.format(m=nxt['n'], after=float(round(
            line['duration'] + nxt['delay'], 2))) if nxt and nxt['when'] == 'after' else ''
        say_fn += SAY_FN.format(n=n, follow=follow,
                                line=_asset(dialogue_path(mod or 'Mod', n), 'SoundWave'))
    for n, (wave, track) in enumerate(work['music']):
        flags.append('Music%d: bool = False' % n)
        music += MUSIC_CASE.format(n=n, wave=int(wave), cue=_asset(levelmusic.cue_path(track)))
    for n, (wave, unit) in enumerate(work['reactions']):
        flags.append('Reacted%d: bool = False' % n)
        reactions += REACTION_CASE.format(n=n, wave=int(wave), cues=', '.join(
            _asset(p) for p in REACTION_LINES[unit]))
    opens, closes = '', ''
    for n, (wave, index, is_open) in enumerate(work.get('rifts') or []):
        flags.append('Rift%d: bool = False' % n)
        case = RIFT_OPEN_CASE if is_open else RIFT_CLOSE_CASE
        text = case.format(n=n, wave=int(wave), index=int(index))
        if is_open:
            opens += text
        else:
            closes += text
    return SCRIPT_HEAD.format(flags='\n'.join(flags) + '\n', music=music or '    return\n',
                              reactions=reactions, opens=opens, closes=closes,
                              breaks=breaks, seen=(SEEN + starts) if lines else '',
                              decide=DECIDE if lines else '',
                              say_fn=say_fn,
                              rift_alias=RIFT_ALIAS.format(rift_class=RIFT_CLASS_PATH)
                              if work.get('rifts') else '',
                              rift_fn=RIFT_FN.format(delay=repr(float(RIFT_OPEN_DELAY)))
                              if work.get('rifts') else '')


def package(level, mod, plan):
    """{package path: (uasset, uexp)} of the piece's Blueprint, or {} without the piece."""
    if piece(plan) is None:
        return {}
    from riftlord.core import gamepath
    from riftlord.types import setup
    if setup.unavailable():
        raise SystemExit(setup.unavailable())
    from bpkit import actor, widget
    from bpkit.typedb import add_package
    work = duties(level, plan)
    folder = os.path.join(gamepath.scratch('mission-events'), mod)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, 'MissionEvents.py'), 'w') as fh:
        fh.write(script(work, mod))
    path = package_path(mod)
    desc = {'name': class_name(mod), 'path': path, 'script': 'MissionEvents.py',
            'defaults': {'PrimaryActorTick': {'bCanEverTick': True,
                                              'TickInterval': TICK_SECONDS}},
            '_dir': folder}
    if work['dialogue']:
        #: the server's decision whether dialogue plays reaches every machine (DECIDE)
        desc.update(replicates=True, always_relevant=True)
    widget.declare([desc])
    pkg = actor.compile(desc)
    problems = widget.verify(pkg, desc['name'], path)
    if problems:
        raise SystemExit('Mission Events piece not built: %s' % '; '.join(problems))
    add_package(widget.db(), pkg, path)
    out = {path: pkg.write()}
    out.update(dialogue_packages(mod, work['dialogue']))
    return out


def dialogue_packages(mod, lines):
    """{package path: (uasset, uexp)} of the dialogue lines' silent captioned waves."""
    from riftlord.build.world import captions
    return {dialogue_path(mod, line['n']): captions.wave(
        dialogue_path(mod, line['n']), line['speaker'], line['text'], line['duration'],
        'RiftLord.%s' % mod, 'Line%d' % line['n']) for line in lines}


def _export_name(pkg, i):
    return pkg.names[struct.unpack_from('<I', pkg.exports[i]['meta'], 16)[0]][0]


def apply(builder, level, plan, report):
    """Place the piece in the level copy. Returns 1 when placed, else 0."""
    item = piece(plan)
    if item is None:
        return 0
    mod = (plan or {}).get('_modKey')
    if not mod:
        report['skipped'].append('Mission Events: no mod name for the piece')
        return 0
    place_actor(builder, item['matrix'], package_path(mod), class_name(mod), PLACED_NAME)
    work = duties(level, plan)
    report.setdefault('notes', []).append(
        'Mission Events placed: %d music change(s), %d boss reaction(s), %d rift event(s), '
        '%d dialogue line(s)' % (len(work['music']), len(work['reactions']),
                                 len(work['rifts']), len(work['dialogue'])))
    return 1


def place_actor(builder, matrix, path, leaf, placed):
    """Place an actor of the Blueprint class `leaf` (package `path`) in the level copy as
    `placed`, at `matrix`: the donor's plain Actor lifted, its class and template swapped for
    the Blueprint's, both serialized before it is created. Returns the actor's export."""
    from riftlord.core import gamefiles
    from riftlord.build import apply as applier
    from riftlord.formats.ue.uassetio import Package
    src = Package(*gamefiles.package(DONOR_LEVEL))
    src.load_payloads()
    donor = next(i for i in range(len(src.exports)) if _export_name(src, i) == DONOR_NAME)
    actor = builder.lift_actor(src, donor, rename=placed)
    name = leaf + '_C'
    pkg_index = builder._import('/Script/CoreUObject', 'Package', 0, path)
    cls = builder._import('/Script/Engine', 'BlueprintGeneratedClass', pkg_index, name)
    cdo = builder._import(path, name, pkg_index, 'Default__' + name)
    meta = bytearray(builder.pkg.exports[actor - 1]['meta'])
    old_class = struct.unpack_from('<i', meta, CLASS_AT)[0]
    old_template = struct.unpack_from('<i', meta, TEMPLATE_AT)[0]
    struct.pack_into('<i', meta, CLASS_AT, cls)
    struct.pack_into('<i', meta, TEMPLATE_AT, cdo)
    builder.pkg.exports[actor - 1]['meta'] = bytes(meta)
    #: the class and its default object serialized before the actor is created
    #: (memory: placed-bp-actor-preloads)
    first = struct.unpack_from('<i', meta, PRELOAD_FIRST_AT)[0]
    counts = struct.unpack_from('<4i', meta, PRELOAD_COUNTS_AT)
    start = first + counts[0] + counts[1]
    swapped = 0
    for at in range(start, start + counts[SERIALIZATION_BEFORE_CREATE]):
        if builder.pkg.preload[at] == old_class:
            builder.pkg.preload[at] = cls
            swapped += 1
        elif builder.pkg.preload[at] == old_template:
            builder.pkg.preload[at] = cdo
            swapped += 1
    if swapped != 2:
        raise SystemExit('%s: the donor\'s serialize-before-create group did not hold its '
                         'class and template (%d swapped)' % (placed, swapped))
    loc, rot, scale = applier.transform_of(matrix)
    applier.place_whole(builder, actor, location=tuple(loc), rotation=tuple(rot))
    return actor


def in_files(files):
    """Package path of a Mission Events Blueprint in a set of built files, or None."""
    for key in files:
        if key.endswith('_MissionEvents.uasset') and key.startswith('OMD/Content/Mods/'):
            return '/Game' + key[len('OMD/Content'):-len('.uasset')]
    return None


#: Export names of the spawners Rift Lord adds (planactors.emit).
ADDED_SPAWNER_PREFIX = 'RiftLordSpawner'


def rifts_in_level(uasset, uexp):
    """{spawner index: bIsActive} of the unstable rifts Rift Lord placed in a built level."""
    from riftlord.formats.ue.uassetio import Package
    from riftlord.build import apply as applier
    from riftlord.mapeditor import planactors
    pkg = Package(uasset, uexp)
    pkg.load_payloads()
    names = [n for n, _h in pkg.names]
    rift_class = planactors.UNSTABLE_CLASS
    out = {}
    for i, e in enumerate(pkg.exports):
        if not _export_name(pkg, i).startswith(ADDED_SPAWNER_PREFIX):
            continue
        cls = struct.unpack_from('<i', e['meta'], CLASS_AT)[0]
        if cls >= 0 or names[struct.unpack_from('<I', pkg.imports[-cls - 1], 20)[0]] != rift_class:
            continue
        index, active = 1, True
        for name, kind, at, size in applier._properties(e['data'], names):
            if name == 'indexInLevel' and kind == 'IntProperty':
                index = struct.unpack_from('<i', e['data'], at)[0]
            elif name == 'bIsActive' and kind == 'BoolProperty':
                active = bool(e['data'][at - 2])
        out[index] = active
    return out


def rift_decode_problems(uasset, uexp):
    """[text]: exports of the added spawners (and their components) whose properties do
    not read back, as a name left pointing into the donor's table would."""
    from bpkit import props as P
    from riftlord.formats.ue.uassetio import Package
    pkg = Package(uasset, uexp)
    pkg.load_payloads()
    out = []
    for i, e in enumerate(pkg.exports):
        outer = struct.unpack_from('<i', e['meta'], 12)[0]
        owner = _export_name(pkg, outer - 1) if outer > 0 else ''
        name = _export_name(pkg, i)
        if not (name.startswith(ADDED_SPAWNER_PREFIX) or owner.startswith(ADDED_SPAWNER_PREFIX)):
            continue
        try:
            P.decode_object(e['data'], [s for s, _h in pkg.names], lambda i: i)
        except Exception as exc:                     # noqa: BLE001
            out.append('%s: %s' % (name, exc))
    return out


def rift_problems(plan, spec=None):
    """[text]: unstable rifts the plan cannot run. Each rift needs the piece, an open
    event, a close after its open, and every wave that spawns through it open then. A rift
    the Crystal opens (riftlord/mapeditor/crystal.py) opens as its health falls, so only
    its events, when it has any, need the piece."""
    rifts = unstable_rifts(plan)
    if not rifts:
        return []
    from riftlord.mapeditor import crystal
    by_crystal = set(crystal.rift_indices(plan))
    out = []
    events = [ev for ev in (plan or {}).get('events') or []
              if isinstance(ev, dict) and ev.get('kind') == 'rift']
    eventful = {int(ev.get('rift') or 0) for ev in events}
    need = [n for n in rifts if n not in by_crystal or n in eventful]
    if need and piece(plan) is None:
        out.append('unstable rift(s) %s need the Mission Events piece'
                   % ', '.join(map(str, need)))
    for n in rifts:
        if n in by_crystal:
            continue
        opens = sorted(int(ev.get('wave') or 0) for ev in events
                       if int(ev.get('rift') or 0) == n and ev.get('open', True))
        closes = sorted(int(ev.get('wave') or 0) for ev in events
                        if int(ev.get('rift') or 0) == n and not ev.get('open', True))
        if not opens:
            out.append('unstable rift %d never opens' % n)
            continue
        if closes and closes[0] <= opens[0]:
            out.append('unstable rift %d closes at wave %d, not after it opens (wave %d)'
                       % (n, closes[0], opens[0]))
        last = closes[0] - 1 if closes else None
        for i, wave in enumerate((spec or {}).get('waves') or []):
            number = i + 1
            if not (wave.get('spawns') or {}).get(str(n)):
                continue
            if number < opens[0] or (last is not None and number > last):
                out.append('wave %d spawns through unstable rift %d while it is closed'
                           % (number, n))
    return out


def rift_flow_checks(pak_path, coord_object, plan, spec):
    """[{name, ok, detail}]: along the coordinator's real flow (flowsim), each unstable
    rift is sent every wave the spec spawns through it."""
    rifts = unstable_rifts(plan)
    if not rifts or not spec:
        return []
    from riftlord.build.navigation import navpatch
    from riftlord.onslaught import flowsim
    wanted = {(i + 1, n) for i, w in enumerate(spec.get('waves') or [])
              for n in rifts if (w.get('spawns') or {}).get(str(n))}
    waves = len(spec.get('waves') or [])
    navpatch.open_pak(pak_path)
    try:
        ran = flowsim.calls_run(coord_object, waves=max(1, waves), spawners=max(rifts))
    except Exception as exc:                         # noqa: BLE001
        return [{'name': 'unstable rifts reached by the onslaught', 'ok': False,
                 'detail': 'could not be simulated: %s: %s' % (type(exc).__name__, exc)}]
    finally:
        navpatch.close_pak(pak_path)
    missing = sorted('wave %d rift %d' % k for k in wanted
                     if not (ran.get(k) or {}).get('OnslaughtSpawnEntities'))
    stray = sorted('wave %d rift %d' % k for k, got in ran.items()
                   if k[1] in rifts and got.get('OnslaughtSpawnEntities') and k not in wanted)
    return [{'name': 'unstable rifts reached by the onslaught', 'ok': not missing and not stray,
             'detail': '; '.join(['not sent: ' + ', '.join(missing)] * bool(missing)
                                 + ['sent unasked: ' + ', '.join(stray)] * bool(stray))
             or ', '.join('wave %d rift %d' % k for k in sorted(wanted)) or 'none sent'}]


def check_files(files, level, plan):
    """[{name, ok, detail}]: the piece's Blueprint is in the pak with its class and default
    object, the level copy places it with them serialized before creation, and the
    Blueprint names every cue its duties play."""
    from riftlord.formats.ue.uassetio import Package
    from riftlord.missions import hostevents
    from riftlord.build.world import levelmusic
    if piece(plan) is None:
        return []
    out = []
    path = in_files(files)
    base = 'OMD/Content' + path[len('/Game'):] if path else None
    ok = bool(base and base + '.uasset' in files and base + '.uexp' in files)
    leaf = path.rsplit('/', 1)[-1] if path else ''
    names = []
    if ok:
        names = [n for n, _h in Package(files[base + '.uasset']).names]
        ok = leaf + '_C' in names and 'Default__' + leaf + '_C' in names
    out.append({'name': 'Mission Events Blueprint in the pak', 'ok': ok,
                'detail': path or 'missing'})
    work = duties(level, plan)
    want = [levelmusic.cue_path(t) for _w, t in work['music']] + \
        [p for _w, u in work['reactions'] for p in REACTION_LINES[u]]
    if work['rifts']:
        want += ['Open Rift', 'Close Rift', RIFT_CLASS_PATH.rsplit('.', 1)[-1]]
    mod = path.split('/')[3] if path else ''
    want += [dialogue_path(mod, line['n']) for line in work['dialogue']]
    missing = [p.rsplit('/', 1)[-1] for p in want if p not in names]
    out.append({'name': 'Mission Events plays every event it owns', 'ok': ok and not missing,
                'detail': ', '.join(missing) or '%d music, %d reaction, %d rift, %d dialogue' % (
                    len(work['music']), len(work['reactions']), len(work['rifts']),
                    len(work['dialogue']))})
    if work['dialogue']:
        out += dialogue_checks(files, mod, work['dialogue'])
    placed, detail = False, 'the level does not place it'
    for key, data in files.items():
        if not key.endswith('.umap') or key[:-5] + '.uexp' not in files:
            continue
        pkg = Package(data, files[key[:-5] + '.uexp'])
        lnames = [n for n, _h in pkg.names]
        if PLACED_NAME not in lnames:
            continue
        placed, detail = placed_ok(pkg, lnames, leaf)
        break
    out.append({'name': 'Mission Events placed with its class preloaded', 'ok': placed,
                'detail': detail})
    if unstable_rifts(plan):
        from riftlord.build import leveltitle
        found, unread = {}, []
        for key, data in files.items():
            if leveltitle.is_level(key) and key[:-5] + '.uexp' in files:
                found = rifts_in_level(data, files[key[:-5] + '.uexp'])
                unread = rift_decode_problems(data, files[key[:-5] + '.uexp'])
        out.append({'name': 'added spawners and their components read back', 'ok': not unread,
                    'detail': '; '.join(unread[:3]) or 'all read'})
        want = unstable_rifts(plan)
        bad = ['%d %s' % (n, 'missing' if n not in found else 'starts open')
               for n in want if found.get(n, True)]
        out.append({'name': 'unstable rifts placed closed, at their indices', 'ok': not bad,
                    'detail': ', '.join(bad) or ', '.join(map(str, want))})
        problems = rift_problems(plan, None)
        out.append({'name': 'every unstable rift opens, then closes after', 'ok': not problems,
                    'detail': '; '.join(problems) or 'ok'})
    unheard = []
    coord = next((n for n in files if n.endswith('_Onslaught01.uasset')), None)
    if coord:
        cp = '/Game' + coord[len('OMD/Content'):-len('.uasset')]
        caps = hostevents.facts_from_files(files, cp) or {}
        unheard = caps.get('unheard') or []
    out.append({'name': 'every event fired is heard by the level or the piece',
                'ok': not unheard, 'detail': ', '.join(unheard) or 'all heard'})
    return out


def dialogue_checks(files, mod, lines):
    """[{name, ok, detail}]: every dialogue line's wave is in the pak, reads back clean
    (pkgcheck) and holds its caption, length and silent audio (captions.checks)."""
    from riftlord.build.world import captions
    from riftlord.core import pkgcheck
    bad, shown = [], []
    for line in lines:
        base = 'OMD/Content' + dialogue_path(mod, line['n'])[len('/Game'):]
        ua, ux = files.get(base + '.uasset'), files.get(base + '.uexp')
        if ua is None or ux is None:
            bad.append('line %d missing' % line['n'])
            continue
        rows = captions.checks(ua, ux, line['speaker'], line['text'], line['duration'])
        wrong = [r['name'] for r in rows if not r['ok']] + \
            list(pkgcheck.problems(ua, ux, 'line %d' % line['n']))
        if wrong:
            bad.append('line %d: %s' % (line['n'], '; '.join(map(str, wrong))))
        else:
            shown.append('%d "%s" %.1f s' % (line['n'], captions.caption(
                line['speaker'], line['text'])[:40], line['duration']))
    return [{'name': 'dialogue lines carry their captions', 'ok': not bad,
             'detail': '; '.join(bad) or ', '.join(shown)}]


def placed_ok(pkg, names, leaf, placed=PLACED_NAME):
    """(ok, detail): the level places `placed` with class `leaf`_C and its default object as
    template, both serialized before it is created."""
    def res(i):
        if i < 0:
            return names[struct.unpack_from('<I', pkg.imports[-i - 1], 20)[0]]
        if i > 0:
            return names[struct.unpack_from('<I', pkg.exports[i - 1]['meta'], 16)[0]]
        return 'null'
    for e in pkg.exports:
        meta = e['meta']
        if names[struct.unpack_from('<I', meta, 16)[0]] != placed:
            continue
        cls = struct.unpack_from('<i', meta, CLASS_AT)[0]
        tpl = struct.unpack_from('<i', meta, TEMPLATE_AT)[0]
        if res(cls) != leaf + '_C' or res(tpl) != 'Default__' + leaf + '_C':
            return False, 'class %s, template %s' % (res(cls), res(tpl))
        first = struct.unpack_from('<i', meta, PRELOAD_FIRST_AT)[0]
        counts = struct.unpack_from('<4i', meta, PRELOAD_COUNTS_AT)
        start = first + counts[0] + counts[1]
        group = set(pkg.preload[start:start + counts[SERIALIZATION_BEFORE_CREATE]])
        if cls not in group or tpl not in group:
            return False, 'class or default object not serialized before create'
        return True, placed
    return False, 'no %s export' % placed
