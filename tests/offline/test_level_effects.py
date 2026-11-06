"""Offline checks on the level's own effects and sounds, on The Lava Pits (Scenario_06).

    python3 test_level_effects.py [--keep]

- An effect Blueprint's particle system comes from its class when the placement names
  none: the 13 BP_LavaBurst placements list P_LavaBurst_1/_2, not 'particles'.
- The level's ambient sounds are listed (`sounds`), from the `_Audio` sublevel too, one per
  AmbientSound that names a Sound; LevelMusic and the MIN_GRP_* sounds (no Sound of their
  own, played by the level script) are not.
- `removedFx` keys of sounds ('Audio|<actor>') and of effect Blueprints park them
  (apply.sublevel_edits).
- A parked sound is silenced: written into the music copy of `_Audio` (as a build writes
  it when the plan also chooses a track), its AudioComponent has bAutoActivate false and no
  Sound, stands at the park, and the music cues still name the chosen track; a sound
  left alone keeps its Sound.

The level's moon: tests/offline/test_moons.py.

Reads the game's packages only. Exit 0 when every check passes.
"""

import argparse
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from riftlord.build import apply  # noqa: E402
from riftlord.build.world import levelmusic  # noqa: E402
from riftlord.core import gamefiles  # noqa: E402
from riftlord.formats.ue.uheader import parse as parse_header  # noqa: E402
from riftlord.mapeditor import geomedits  # noqa: E402
from riftlord.mapeditor import leveldata  # noqa: E402
from riftlord.onslaught import onslaught  # noqa: E402

LEVEL = 'Scenario_06'
#: The AmbientSounds of Scenario_06_Audio this test removes: the lava bed and one bubbles.
BED, BUBBLES, WIND = 13, 24, 14
TRACK = 'Battle_03'
SCRATCH_PARENT = '/mnt/2tb_pro_a/tmp' if os.path.isdir('/mnt/2tb_pro_a/tmp') else None

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))
    print('  %s  %s%s' % ('PASS' if ok else 'FAIL', name, (': ' + str(detail)) if detail else ''))


def read_back(ua, ux, scratch):
    """(header info, uexp bytes, header size, props(i)) of a written package."""
    path = os.path.join(scratch, 'level.umap')
    with open(path, 'wb') as fh:
        fh.write(ua)
    info = parse_header(path)

    def props(i):
        return dict((q[0], q) for q in onslaught.read_props(info, ux, len(ua),
                                                            info['exports'][i - 1]))
    return info, props


def run(scratch):
    lv = leveldata.level(LEVEL)
    bursts = [f for f in lv['vfx'] if str(f.get('actorClass') or '').startswith('BP_LavaBurst')]
    check('13 lava bursts listed', len(bursts) == 13, len(bursts))
    check('each lava burst names its class\'s particle system',
          bursts and all(f['template'] in ('P_LavaBurst_1', 'P_LavaBurst_2') for f in bursts),
          sorted({f['template'] for f in bursts}))
    torches = [f for f in lv['vfx'] if 'Torch' in str(f.get('actorClass') or '')]
    check('torch fire names its particle system',
          torches and all(f['template'] == 'P_Torch_Fire' for f in torches),
          sorted({f['template'] for f in torches}))

    sounds = lv.get('sounds') or []
    audio = [s for s in sounds if s['sub'] == 'Audio']
    check('65 ambient sounds listed from _Audio', len(audio) == 65, len(audio))
    check('sounds the level script plays are not listed',
          not any(s['actorName'] == 'LevelMusic' or s['actorName'].startswith('MIN_GRP')
                  for s in sounds))
    lava = [s for s in audio if 'Lava' in s['cue']]
    check('55 lava sounds', len(lava) == 55, len(lava))
    bed = next((s for s in audio if s['actor'] == BED), None)
    check('the lava bed is AmbBed_LavaLandscape_LP_SC',
          bed and bed['cue'] == 'AmbBed_LavaLandscape_LP_SC', bed)
    keys = ['%s|%d' % (s['sub'], s['actor']) for s in sounds]
    check('sound keys are unique', len(keys) == len(set(keys)))

    burst = bursts[0]
    plan = {'removedFx': ['Audio|%d' % BED, 'Audio|%d' % BUBBLES,
                          '%s|%d' % (burst['sub'], burst['actor'])]}
    edits = apply.sublevel_edits(LEVEL, plan)
    check('removed sounds park in Audio',
          sorted((edits.get('Audio') or {}).get('parked') or []) == [BED, BUBBLES],
          (edits.get('Audio') or {}).get('parked'))
    check('a removed lava burst parks', burst['actor'] in (
        (edits.get(burst['sub']) or {}).get('parked') or []))

    path = levelmusic.audio_path(LEVEL)
    ua, ux = gamefiles.package(path)
    ua, ux, music = levelmusic.retarget(ua, ux, TRACK)
    ua, ux, got = apply.apply_to_sublevel(ua, ux, 'RiftLordTest_Map01_Audio',
                                          edits['Audio'])
    check('two sounds parked', got['parked'] == 2, got['parked'])
    info, props = read_back(ua, ux, scratch)
    names = [n for n in info['names'] if isinstance(n, str)]
    check('the music copy plays the chosen track',
          any(TRACK in n for n in names if n.startswith(levelmusic.MUSIC_ROOT)),
          music.get('replaced'))
    for actor in (BED, BUBBLES):
        root = onslaught.as_int(props(actor)['RootComponent'][2])
        cp = props(root)
        auto = cp.get('bAutoActivate')
        sound = onslaught.as_int(cp['Sound'][2]) if 'Sound' in cp else None
        at = onslaught.as_vec(cp['RelativeLocation'][2]) if 'RelativeLocation' in cp else None
        check('sound %d does not auto-activate' % actor, auto is not None and not auto[2],
              auto)
        check('sound %d names no Sound' % actor, sound == 0, sound)
        check('sound %d is parked' % actor, at and tuple(round(v, 1) for v in at) in (
            geomedits.PARKED, geomedits.PARKED_AWAY), at)
    wind = props(onslaught.as_int(props(WIND)['RootComponent'][2]))
    check('a sound left alone keeps its Sound',
          'Sound' in wind and onslaught.as_int(wind['Sound'][2]) < 0)

    bad = [c for c in checks if not c[1]]
    print('%d of %d checks pass' % (len(checks) - len(bad), len(checks)))
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--keep', action='store_true', help='keep the scratch folder')
    args = ap.parse_args(argv)
    scratch = tempfile.mkdtemp(prefix='level-effects-test-', dir=SCRATCH_PARENT)
    try:
        return run(scratch)
    finally:
        if args.keep:
            print('kept %s' % scratch)
        else:
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
