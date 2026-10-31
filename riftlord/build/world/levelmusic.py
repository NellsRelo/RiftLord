"""A level's music, and choosing another shipped track at build.

Each level streams a `<map>_Audio` sublevel whose level script holds the music:
`BattleMusic` and `BattleMusicVariant` (two loops, one per play) on battle maps,
`BattleMusicIntro` and the loop on war scenarios, each a SoundCue the script plays
when the persistent level's script fires `RemoteEvent(MusicIntroStart)` and on its
break events. Scenario_18 also has a stinger (Vorwick's death).

Choosing a track copies the `_Audio` sublevel into the mod, as geometry edits copy
`_Castle`, and points every music cue it names at the chosen track: loops at its loop,
an intro at its intro (a track without one plays its loop there). Stingers are kept.
Plan key: `plan['world']['music']` = a TRACKS key. A `music` event in `plan['events']`
sets the second loop (the one the `ChangeMusic` event switches to) on its own, and the
opening loop then takes `world.music` (riftlord.hostevents.audio_facts tells them apart).

Preview: each loop cue mixes SoundWaves `<track>_{A,B,C}_{Full,Break}_LP`. The waves
are UE4 native audio (not Wwise), cooked as format OGG00000000: a plain Ogg Vorbis
stream inline in the wave's uexp. `preview(track)` returns the A Full loop's Ogg
bytes as they are, which a browser plays, cached in the Rift Lord cache.
"""

import functools
import os
import re
import struct
import threading

#: The plan's world setting.
PLAN_KEY = 'music'
#: Folder of the level music cues.
MUSIC_ROOT = '/Game/Sounds/Music/Cues/Final/'
#: A cue name: MX_OMD3_<track>_LP_SC or MX_OMD3_<track>_Intro_LP_SC.
CUE = re.compile(r'^MX_OMD3_(?P<track>(?:DLC_)?(?:Battle|War)_\d\d)_(?P<intro>Intro_)?LP_SC$')
#: The tracks offered: key -> (folder under MUSIC_ROOT, has an intro).
TRACKS = dict(
    [('Battle_%02d' % n, ('Battle', False)) for n in range(1, 10)]
    + [('DLC_Battle_%02d' % n, ('Battle', False)) for n in range(1, 4)]
    + [('War_%02d' % n, ('War', True)) for n in range(1, 6)]
    + [('DLC_War_01', ('War', True))])
#: The tag the copied sublevel is known by (riftlord/emit.py sublevel copies).
TAG = 'Audio'


def cue_path(track, intro=False):
    """Package path of a track's loop or intro cue."""
    folder, has_intro = TRACKS[track]
    leaf = 'MX_OMD3_%s_%sLP_SC' % (track, 'Intro_' if intro and has_intro else '')
    return MUSIC_ROOT + '%s/%s' % (folder, leaf)


#: The loop wave a preview plays, by leaf-name fragment, in order of preference.
PREVIEW_WAVES = ('_A_Full', '_Full')
#: The cache folder of extracted previews (gamepath.scratch).
PREVIEW_CACHE = 'music-previews'
#: Bytes of FByteBulkData header before the payload: flags, count, size, offset.
BULK_HEAD = struct.Struct('<IiiQ')


def preview_wave(track):
    """Package path of the SoundWave a track's preview plays."""
    from riftlord.core import gamefiles
    from riftlord.formats.ue.uassetio import Package
    loop = cue_path(track)
    ua, ux = gamefiles.package(loop)
    waves = [n for n in (s for s, _h in Package(ua, ux).names)
             if n.startswith(MUSIC_ROOT) and '.' not in n and n != loop]
    for frag in PREVIEW_WAVES:
        for wave in waves:
            if frag in wave.rsplit('/', 1)[-1]:
                return wave
    if not waves:
        raise ValueError('%s names no sound wave' % loop)
    return waves[0]


def _ogg(uexp):
    """The inline Ogg Vorbis payload of a cooked SoundWave's uexp."""
    at = uexp.find(b'OggS')
    if at < BULK_HEAD.size:
        raise ValueError('no inline Ogg stream in the sound wave')
    _flags, count, size, _off = BULK_HEAD.unpack_from(uexp, at - BULK_HEAD.size)
    if count != size or size <= 0 or at + size > len(uexp):
        raise ValueError('unexpected bulk data header before the Ogg stream')
    return uexp[at:at + size]


def preview(track):
    """Ogg Vorbis bytes of a track's preview (its A Full loop), cached."""
    if track not in TRACKS:
        raise KeyError(track)
    from riftlord.core import gamepath
    from riftlord.core import gamefiles
    cache = os.path.join(gamepath.scratch(PREVIEW_CACHE), track + '.ogg')
    if os.path.isfile(cache):
        with open(cache, 'rb') as fh:
            return fh.read()
    data = _ogg(gamefiles.package(preview_wave(track))[1])
    tmp = '%s.%d.tmp' % (cache, threading.get_ident())
    with open(tmp, 'wb') as fh:
        fh.write(data)
    os.replace(tmp, cache)
    return data


def audio_path(level):
    """The level's `_Audio` sublevel package, or None."""
    from riftlord.build.minimap import mapdata
    from riftlord.core import gamefiles
    path = mapdata.map_path(level) + '_' + TAG
    try:
        gamefiles.package(path)
    except Exception:                                # noqa: BLE001
        return None
    return path


def _cues(names):
    """[(name-table index, package path, track, intro)] of the music cues in a
    package's name table."""
    out = []
    for i, n in enumerate(names):
        if not n.startswith(MUSIC_ROOT):
            continue
        m = CUE.match(n.rsplit('/', 1)[-1])
        if m:
            out.append((i, n, m.group('track'), bool(m.group('intro'))))
    return out


@functools.lru_cache(maxsize=64)
def shipped(level):
    """[track key] the level's music plays, loops first; [] when it has none."""
    from riftlord.core import gamefiles
    from riftlord.formats.ue.uassetio import Package
    path = audio_path(level)
    if path is None:
        return []
    pkg = Package(gamefiles.package(path)[0])
    got = []
    for _i, _n, track, intro in sorted(_cues([s for s, _h in pkg.names]),
                                       key=lambda c: c[3]):
        if track not in got:
            got.append(track)
    return got


@functools.lru_cache(maxsize=1)
def choices():
    """[{key, label, levels}] for the Music picker: every track, with the shipped
    levels whose music it is."""
    from riftlord.build.minimap import mapdata
    from riftlord.build.navigation import navpatch
    names = mapdata.mission_names()
    used = {}
    for level in sorted(navpatch.maps(include_mods=False)):
        for track in shipped(level):
            used.setdefault(track, []).append(names.get(level, level))
    out = []
    for key in TRACKS:
        kind = key.replace('_', ' ')
        label = re.sub(r' 0(\d)$', r' \1', kind)
        out.append({'key': key, 'label': label, 'intro': TRACKS[key][1],
                    'levels': used.get(key, [])})
    return out


def wanted(plan):
    """The plan's track key, or None for the level's own music."""
    key = ((plan or {}).get('world') or {}).get(PLAN_KEY)
    return key if key in TRACKS else None


def clean(value):
    """A plan's music value validated, or None."""
    if value in (None, ''):
        return None
    if value not in TRACKS:
        raise ValueError('unknown music track %r' % value)
    return value


def retarget(uasset, uexp, track, change=None):
    """(uasset, uexp, report): the `_Audio` sublevel with its music cues pointed at
    `track`. Only name-table entries change.

    With `change`, each loop is set on its own (riftlord.hostevents.audio_facts): the
    opening loop (and its intro) to `track`, or kept when `track` is None, and the loop
    the ChangeMusic event switches to, to `change`."""
    from riftlord.build import modtable
    from riftlord.formats.ue.uassetio import Package
    loops = {}
    if change is not None:
        from riftlord.missions import hostevents
        got = hostevents.audio_facts(uasset, uexp)
        if not got['change']:
            raise SystemExit('the level\'s audio sublevel has no second music loop')
        loops = {got['change']: change}
        if track is not None and got['opening']:
            loops[got['opening']] = track
    pkg = Package(uasset, uexp)
    pkg.load_payloads()
    names = [s for s, _h in pkg.names]
    exact = {}
    for _i, path, old, intro in _cues(names):
        to = loops.get(old) if change is not None else track
        if to is None or to == old:
            continue
        new = cue_path(to, intro)
        exact[path] = new
        exact[path.rsplit('/', 1)[-1]] = new.rsplit('/', 1)[-1]
    if not exact and change is None:
        raise SystemExit('the level\'s audio sublevel names no music to replace')
    if exact:
        modtable.retarget(pkg, exact=exact)
    ua, ux = pkg.write()
    return ua, ux, {'music': track, 'change': change, 'replaced': sorted(
        p.rsplit('/', 1)[-1] for p in exact if p.startswith('/'))}


def copy(level, mod, plan):
    """(old package path, new package path, (uasset, uexp), report) for the level's
    `_Audio` sublevel playing the plan's track, or None when the plan keeps the
    level's music."""
    track = wanted(plan)
    change = change_wanted(level, plan)
    if track is None and change is None:
        return None
    from riftlord.core import gamefiles
    old = audio_path(level)
    if old is None:
        raise SystemExit('%s has no audio sublevel, so its music cannot change' % level)
    ua, ux = gamefiles.package(old)
    new = '/Game/Mods/%s/Maps/%s_Map01/%s_Map01_%s' % (mod, mod, mod, TAG)
    ua, ux, report = retarget(ua, ux, track, change=change)
    return old, new, (ua, ux), report


def change_wanted(level, plan):
    """The track the plan's music event switches to, when the level can change music
    and that track is not already its second loop; else None."""
    track = next((e.get('track') for e in (plan or {}).get('events') or []
                  if isinstance(e, dict) and e.get('kind') == 'music'
                  and e.get('track') in TRACKS), None)
    if track is None:
        return None
    from riftlord.missions import hostevents
    music = hostevents.capabilities(level)['music']
    if not music['available']:
        return None
    if track == music['change'] and wanted(plan) is None:
        return None
    return track
