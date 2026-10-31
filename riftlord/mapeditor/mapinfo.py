"""Map settings for the Map Editor's right sidebar: world settings and the mission's Guid.

`info(level, mission=None)` reads the level's `OMDWorldSettings` and, for a mission in
Missions, that mission's own rows and Guid. `_shipped_rows()` lists every shipped
mission row, for Guid clash checks (bucket.guid_clashes). `apply_world(builder, plan, report)` writes a plan's `world` settings into
a level being built.
"""

import functools
import struct

from riftlord.build.navigation import navpatch
from riftlord.onslaught import onslaught

#: The world settings a plan may change: plan key -> (property, kind).
WORLD_FIELDS = {'killZ': ('KillZ', 'FloatProperty'),
                'warScenario': ('bIsWarScenario', 'BoolProperty')}

#: Class defaults, for a property the cooked export omits.
WORLD_DEFAULTS = {'KillZ': -1048575.0, 'bIsWarScenario': False,
                  'bEnableNavigationSystem': True, 'bPrecomputeVisibility': False}

#: Shown read-only.
WORLD_READONLY = ('bEnableNavigationSystem', 'bPrecomputeVisibility')


def guid_text(raw):
    """A Guid as UE spells it (Conv_GuidToString): four uint32s, %08X each."""
    raw = bytes(raw)[:16]
    if len(raw) < 16:
        return ''
    return ''.join('%08X' % v for v in struct.unpack('<4I', raw))


@functools.lru_cache(maxsize=16)
def world_settings(level):
    """{property: value} of the level's OMDWorldSettings, defaults filled in."""
    info, uexp, hdr = onslaught.load_map(level)
    out = dict(WORLD_DEFAULTS)
    for ex in info['exports']:
        if str(ex.get('cls')) != 'OMDWorldSettings':
            continue
        for name, kind, raw in onslaught.read_props(info, uexp, hdr, ex):
            if kind == 'FloatProperty':
                out[name] = round(struct.unpack('<f', raw)[0], 2)
            elif kind == 'BoolProperty':
                out[name] = bool(raw)
            elif kind == 'IntProperty':
                out[name] = struct.unpack('<i', raw)[0]
        break
    return {k: out[k] for k in WORLD_DEFAULTS}


@functools.lru_cache(maxsize=1)
def _shipped_rows():
    """[(row, level, title, guid, war scenario)] of every shipped MissionProtos row."""
    from riftlord.build.minimap import mapdata
    from riftlord.build import missionproto
    table = missionproto.read()
    levels = mapdata.mission_levels()
    out = []
    for row in table.rows:
        fields = {getattr(f, 'name', None): f for f in row['fields']}
        title = ''
        if 'MissionName' in fields:
            title = mapdata._ftext_source(fields['MissionName'].payload) or ''
        guid = guid_text(fields['Guid'].payload) if 'Guid' in fields else ''
        ws = bool(getattr(fields.get('bIsWarScenario'), 'bool_value', False))
        out.append((str(row['name']), levels.get(row['name']), title, guid, ws))
    return out


def mission_rows(mission):
    """The rows of a mission in Missions: [{row, title, guid}]."""
    from riftlord.missions import bucket
    if not mission or not bucket.ACTIVE:
        return []
    path = bucket._source(mission)
    if path is None:
        return []
    return [{'row': r, 'title': t, 'guid': guid_text(bytes.fromhex(g)) if g else ''}
            for r, t, g in bucket._mission_rows(path)]


def info(level, mission=None):
    """Everything the map settings panel shows for one level."""
    out = {'level': level, 'world': {}, 'editable': list(WORLD_FIELDS),
           'readonly': list(WORLD_READONLY), 'mission': [], 'guid': ''}
    try:
        out['world'] = world_settings(level)
    except (Exception, SystemExit) as exc:           # noqa: BLE001
        out['worldError'] = '%s: %s' % (type(exc).__name__, exc)
    try:
        from riftlord.build.world import levelfx
        out['introFilter'] = sorted({m for ms in levelfx.filters(level).values() for m in ms})
        out['scene'] = levelfx.scene(level)
        out['sceneFields'] = levelfx.fields()
    except (Exception, SystemExit) as exc:           # noqa: BLE001
        out['introFilter'] = []
        out['sceneError'] = '%s: %s' % (type(exc).__name__, exc)
    try:
        from riftlord.build.world import storyvo
        out['storyDialog'] = storyvo.survey(level)
    except (Exception, SystemExit) as exc:           # noqa: BLE001
        out['storyDialog'] = None
        out['storyError'] = '%s: %s' % (type(exc).__name__, exc)
    try:
        from riftlord.build.world import levelintro
        out['intro'] = levelintro.survey(level)
    except (Exception, SystemExit) as exc:           # noqa: BLE001
        out['intro'] = None
        out['introError'] = '%s: %s' % (type(exc).__name__, exc)
    try:
        from riftlord.build.world import riftwarning
        out['riftWarning'] = riftwarning.shipped(level)
        out['riftWarningRange'] = list(riftwarning.SIZE_RANGE)
    except (Exception, SystemExit) as exc:           # noqa: BLE001
        out['riftWarning'] = None
        out['riftWarningError'] = '%s: %s' % (type(exc).__name__, exc)
    try:
        from riftlord.build.world import levelmusic
        out['music'] = levelmusic.shipped(level)
        out['musicChoices'] = levelmusic.choices() if out['music'] else []
    except (Exception, SystemExit) as exc:           # noqa: BLE001
        out['music'] = []
        out['musicError'] = '%s: %s' % (type(exc).__name__, exc)
    try:
        out['musicChange'] = music_change(level, mission)
    except (Exception, SystemExit) as exc:           # noqa: BLE001
        out['musicChange'] = None
        out['musicChangeError'] = '%s: %s' % (type(exc).__name__, exc)
    try:
        out['mission'] = mission_rows(mission)
        if mission:
            from riftlord.missions import bucket
            out['guid'] = bucket.guid_spelled(mission) if bucket.ACTIVE else ''
    except (Exception, SystemExit) as exc:           # noqa: BLE001
        out['missionError'] = '%s: %s' % (type(exc).__name__, exc)
    return out


def music_change(level, mission=None):
    """The host's music change for the Music section (riftlord.hostevents):
    {available, opening, change, wave, events}. `events` are the mission's events as its
    waves hold them (wavespec.split_events), for a plan that has no `events` of its own
    yet; `wave` is the wave its music change fires in, or None."""
    from riftlord.onslaught import wavespec
    from riftlord.missions import hostevents
    from riftlord.mapeditor import planactors
    caps = hostevents.capabilities(level)
    music = caps.get('music') or {}
    spec = planactors.mission_spec(level, mission) or {}
    if isinstance(spec.get('events'), list):
        events = spec['events']
    else:
        _spec, events = wavespec.split_events(spec, caps)
    events = [e for e in events if not (e.get('kind') == 'music' and not e.get('track'))]
    events = hostevents.clean(events) or []
    wave = next((e['wave'] for e in events if e['kind'] == 'music'), None)
    return {'available': bool(music.get('available')), 'opening': music.get('opening'),
            'change': music.get('change'), 'wave': wave, 'events': events}


def clean_world(raw):
    """A plan's `world` block, validated: {killZ: float, warScenario: bool}, unset keys dropped."""
    out = {}
    if not isinstance(raw, dict):
        return out
    if raw.get('killZ') not in (None, ''):
        value = float(raw['killZ'])
        if value != value or abs(value) > 1e7:
            raise ValueError('kill height out of range: %r' % raw['killZ'])
        out['killZ'] = value
    if raw.get('warScenario') is not None:
        out['warScenario'] = bool(raw['warScenario'])
    from riftlord.build.world import levelfx
    if raw.get(levelfx.PLAN_KEY) == levelfx.REMOVE:
        out[levelfx.PLAN_KEY] = levelfx.REMOVE
    out.update(levelfx.clean(raw))
    from riftlord.build.world import storyvo
    from riftlord.build.world import riftwarning
    if raw.get(storyvo.PLAN_KEY) == storyvo.REMOVE:
        out[storyvo.PLAN_KEY] = storyvo.REMOVE
    from riftlord.build.world import levelintro
    if raw.get(levelintro.PLAN_KEY) == levelintro.REMOVE:
        out[levelintro.PLAN_KEY] = levelintro.REMOVE
    warning = riftwarning.clean(raw.get(riftwarning.PLAN_KEY))
    if warning is not None:
        out[riftwarning.PLAN_KEY] = warning
    from riftlord.build.world import levelmusic
    music = levelmusic.clean(raw.get(levelmusic.PLAN_KEY))
    if music is not None:
        out[levelmusic.PLAN_KEY] = music
    return out


def apply_world(builder, plan, report):
    """Write the plan's world settings into every WorldSettings export of the level."""
    from riftlord.build import levelbuild
    world = (plan or {}).get('world') or {}
    if not world:
        return
    pkg = builder.pkg
    for i, ex in enumerate(pkg.exports):
        cls = levelbuild.resolve(pkg, struct.unpack_from('<i', ex['meta'], 0)[0])[1]
        if not cls or 'WorldSettings' not in cls:
            continue
        if 'killZ' in world:
            builder.set_property(i + 1, 'KillZ', 'FloatProperty',
                                 struct.pack('<f', world['killZ']))
        if 'warScenario' in world and cls == 'OMDWorldSettings':
            builder.set_bool(i + 1, 'bIsWarScenario', world['warScenario'])
    if 'killZ' in world:
        report['notes'].append('kill height set to %g' % world['killZ'])
    if 'warScenario' in world:
        report['notes'].append('War Scenario flag %s'
                               % ('set' if world['warScenario'] else 'cleared'))
