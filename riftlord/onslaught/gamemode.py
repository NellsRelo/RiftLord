"""A mission's game mode, as the Onslaught Editor shows it.

The mode is keyed by the GameType string a build writes to the mission's CustomMaps.yaml
entry (customcampaign.finish, from the plan's pieces): '' is Rift Defense, the game's own;
'CrystalDefense' is a mission with the Crystal (riftlord.mapeditor.crystal) placed.

Where the mode comes from, for every mission alike:
  - a mission with a plan (a Missions record, a probe's record, a plan of any age): the
    plan's pieces (of_plan);
  - a build opened without its record (an installed or opened pak): the same pieces as
    the built level places them (of_level, from onslaught.map_report's `pieces`).

view() is what the editor receives:
    {gameType, label, hide: [mission setting keys it has no use for],
     opens: [spawner indexInLevel the mode's piece opens itself; no wave goes through them]}

A new game type adds a MODES row and its piece to PIECES.
"""

from riftlord.mapeditor import crystal

#: The game's own mode: no GameType.
RIFT_DEFENSE = ''
#: GameType -> the editor's label and the mission settings (wavespec.SETTINGS keys) it hides.
MODES = {RIFT_DEFENSE: {'label': 'Rift Defense', 'hide': ()},
         crystal.GAME_TYPE: {'label': 'Crystal Defense', 'hide': ('riftPoints',)}}
#: Placed class of each game type's piece -> (GameType, the int array properties holding
#: the spawner indices it opens: Rift Tear GroupMembers, and RiftIndex of older rows).
PIECES = {crystal.CLASS: (crystal.GAME_TYPE, ('GroupMembers', 'RiftIndex'))}


def piece_arrays(cls):
    """{array property: ARRAYS kind} a built piece of class `cls` is read with."""
    if cls == crystal.CLASS:
        from riftlord.mapeditor import rifttear
        return dict(rifttear.ARRAYS, **{crystal.DIFFICULTY_HEALTH: 'float'})
    return {name: 'int' for name in PIECES.get(cls, (None, ()))[1]}


def piece_scalars(cls):
    """{scalar property: (tag type, struct format)} a built piece of class `cls` is read with."""
    if cls == crystal.CLASS:
        from riftlord.mapeditor import rifttear
        return {rifttear.WAVE_COUNT: ('IntProperty', '<i'), 'RiftGap': ('FloatProperty', '<f')}
    return {}


def decode(kind, raw):
    """An array property's values from its raw payload (rifttear.decode)."""
    from riftlord.mapeditor import rifttear
    return rifttear.decode(kind, raw)


def view(game_type, opens=()):
    """The editor's mode record for `game_type`; an unknown one reads as its own name."""
    mode = MODES.get(game_type) or {'label': game_type, 'hide': ()}
    return {'gameType': game_type, 'label': mode['label'], 'hide': list(mode['hide']),
            'opens': sorted({int(i) for i in opens if int(i) > 0})}


def crystal_opens(plan):
    """indexInLevel of every unstable rift the plan's Crystal opens (its Rift Tear groups,
    or the rows of a plan made before groups)."""
    from riftlord.mapeditor import rifttear
    item = crystal.piece(plan)
    if not item:
        return []
    index = rifttear._index_of((plan or {}).get('spawners'))
    return sorted({index[i] for i in rifttear.member_ids(crystal.values(item)) if index.get(i)})


def of_plan(plan):
    """The mode of a mission with this plan."""
    game_type = crystal.game_type(plan)
    return view(game_type, crystal_opens(plan) if game_type == crystal.GAME_TYPE else ())


def of_level(pieces):
    """The mode of a built level, from map_report's `pieces`: [{cls, arrays: {name: [int]}}]."""
    for p in pieces or []:
        got = PIECES.get(p.get('cls'))
        if got:
            game_type, names = got
            return view(game_type, [i for n in names for i in (p.get('arrays') or {}).get(n, [])])
    return view(RIFT_DEFENSE)


def record_of(map_name):
    """The Missions record whose build holds `map_name`, when it has a plan, else None."""
    from riftlord.build.navigation import navpatch
    from riftlord.core import paksource
    from riftlord.missions import bucket
    if not bucket.ACTIVE:
        return None
    pak = navpatch.opened_maps().get(map_name)
    if not pak:
        return None
    from riftlord.onslaught import gamedata
    if not gamedata._in_missions(pak):
        return None
    rec = bucket.load(paksource.stem(pak))
    return rec if bucket._editable(rec) else None


def of_map(map_name, pieces=None):
    """The mode of a map opened in the editor: its Missions record's plan, else the pieces
    its built level places."""
    rec = record_of(map_name)
    return of_plan(rec['plan']) if rec else of_level(pieces)


def tear_of_map(map_name, pieces=None):
    """What the Onslaught Editor edits of a map's Crystal (rifttear.editor_view's shape): from
    its Missions record's plan, else read back from the Crystal its built level places; None
    without one."""
    from riftlord.mapeditor import rifttear
    rec = record_of(map_name)
    if rec:
        return rifttear.editor_view(rec['plan'])
    for p in pieces or []:
        if p.get('cls') == crystal.CLASS:
            return rifttear.level_view(p.get('arrays') or {}, p.get('scalars') or {},
                                       p.get('spawnerUnits'))
    return None


def spec_problems(spec, mode):
    """[text]: waves of `spec` that send units through a rift the mode's piece opens."""
    out = []
    for n in (mode or {}).get('opens') or []:
        for i, wave in enumerate((spec or {}).get('waves') or []):
            if (wave.get('spawns') or {}).get(str(n)):
                out.append('wave %d sends units through unstable rift %d, which the Crystal '
                           'opens' % (i + 1, n))
    return out
