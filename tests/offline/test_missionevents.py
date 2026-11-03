"""Offline checks on the Mission Events piece's unstable rifts and scratch-level support.

    python3 test_missionevents.py
"""
import copy
import os
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from riftlord.missions import hostevents
from riftlord.missions import missionevents
from riftlord.missions import records  # noqa: E402
from riftlord.onslaught import wavespec  # noqa: E402

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


IDENT = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
PLAN = {'level': 'Scenario_02', 'version': 2, 'look': {}, 'world': {}, 'rifts': [],
        'doors': [], 'points': [], 'playerStarts': [], 'wiring': [], 'removed': [],
        'waves': [], 'edits': [],
        'spawners': [{'id': 1, 'at': [4954.0, 2552.0, 250.0], 'rotation': 0.0,
                      'kind': 'flyer', 'index': 3, 'maxSpawnPerRow': 0, 'door': 'Door01',
                      'start': 'OMDPathPoint13', 'flyerStart': None, 'unstable': True},
                     {'id': 2, 'at': [0.0, 0.0, 250.0], 'rotation': 0.0, 'kind': 'ground',
                      'index': 4, 'maxSpawnPerRow': 0, 'door': None,
                      'start': 'OMDPathPoint13', 'flyerStart': None}],
        'added': [{'name': 'Mission Events', 'kind': 'events', 'matrix': IDENT}],
        'events': [{'kind': 'rift', 'wave': 1, 'rift': 3, 'open': True},
                   {'kind': 'rift', 'wave': 2, 'rift': 3, 'open': False}]}

# 1. the save keeps an unstable rift as designer data, and only on the spawner that has it
_w, saved = records.validate_plan({'plan': copy.deepcopy(PLAN)})
sp = {s['id']: s for s in saved.get('spawners') or []}
check('save keeps unstable on the rift', sp.get(1, {}).get('unstable') is True, sp.get(1))
check('save makes an unstable rift a doorless ground spawner',
      sp.get(1, {}).get('kind') == 'ground' and sp.get(1, {}).get('door') is None, sp.get(1))
check('save adds no unstable to a plain spawner', 'unstable' not in sp.get(2, {}), sp.get(2))
check('save keeps the rift events', [e for e in saved.get('events') or []
                                     if e['kind'] == 'rift'] == PLAN['events'],
      saved.get('events'))
_w, again = records.validate_plan({'plan': copy.deepcopy(saved)})
check('a saved plan saves unchanged', again == saved)

# 2. the piece owns the rift's events; the host's caps gain the rift
work = missionevents.duties('Scenario_02', saved)
check('duties: open at 1, close at 2', work['rifts'] == [(1, 3, True), (2, 3, False)],
      work['rifts'])
caps = hostevents.with_piece(hostevents.capabilities('Scenario_02'),
                             missionevents.unstable_rifts(saved))
check('caps list the placed rift', caps['rift'].get('rifts') == [3]
      and caps['rift'].get('pieceRifts') == [3] and caps['rift'].get('available'),
      caps['rift'])
spec = {'waves': [{'spawns': {'1': [], '3': [{'unit': 'OrcLight', 'count': 2}]}},
                  {'spawns': {'1': []}}]}
_s, report = wavespec.apply_events(dict(spec, events=saved['events']), caps)
check('apply_events gives both rift events to the piece',
      all(r.get('piece') and r['status'] == wavespec.APPLIED for r in report), report)
kept = wavespec.without_actions(caps)['rift']
check('a coordinator without actions keeps the piece\'s rifts',
      kept.get('available') and kept.get('rifts') == [3], kept)
s18 = hostevents.with_piece(hostevents.capabilities('Scenario_18'), [15])
check('Scenario_18 keeps its own rifts beside a placed one',
      s18['rift']['rifts'] == list(range(1, 12)) + [15] and s18['rift']['pieceRifts'] == [15],
      s18['rift'])

# 3. the window check
check('no rift problems when spawns sit inside the window',
      not missionevents.rift_problems(saved, spec), missionevents.rift_problems(saved, spec))
late = {'waves': spec['waves'] + [{'spawns': {'3': [{'unit': 'OrcLight', 'count': 1}]}}]}
late['waves'][1] = {'spawns': {'3': [{'unit': 'OrcLight', 'count': 1}]}}
bad = missionevents.rift_problems(saved, late)
check('spawns through a closed rift are refused', len(bad) == 2, bad)
none = dict(saved, events=[])
check('a rift that never opens is refused',
      missionevents.rift_problems(none) == ['unstable rift 3 never opens'],
      missionevents.rift_problems(none))
same = dict(saved, events=[dict(e, wave=1) for e in saved['events']])
check('a close in the opening wave is refused', missionevents.rift_problems(same),
      missionevents.rift_problems(same))
bare = dict(saved, added=[])
check('a rift without the piece is refused',
      any('Mission Events piece' in p for p in missionevents.rift_problems(bare)))

# 4. the piece compiles with rift duties (bpkit verify runs inside package())
try:
    got = missionevents.package('Scenario_02', 'MissionEventsTest', saved)
    (ua, _ux), = got.values()
    from riftlord.formats.ue.uassetio import Package
    names = {n for n, _h in Package(ua).names}
    check('piece compiles and names Open Rift and Close Rift',
          {'Open Rift', 'Close Rift', 'BP_OMDAISpawner_VorwickRift_C'} <= names)
except (Exception, SystemExit) as exc:                # noqa: BLE001
    check('piece compiles and names Open Rift and Close Rift', False, str(exc))

# 5. dialogue lines: saved, given to the piece, written as silent captioned waves
from riftlord.build.world import captions  # noqa: E402
LINES = [{'kind': 'dialogue', 'wave': 1, 'when': 'start', 'delay': 2, 'speaker': ' Egan ',
          'text': 'Here they come. Hold the line!', 'duration': None},
         {'kind': 'dialogue', 'wave': 1, 'when': 'after', 'delay': 0.5, 'speaker': 'Kelsey',
          'text': 'Traps first, heroics second.', 'duration': 3},
         {'kind': 'dialogue', 'wave': 1, 'when': 'break', 'delay': 0, 'speaker': '',
          'text': 'Ünïcode survives.', 'duration': 19.9}]
talk = dict(copy.deepcopy(PLAN), events=PLAN['events'] + LINES)
_w, saved_talk = records.validate_plan({'plan': copy.deepcopy(talk)})
said = [e for e in saved_talk.get('events') or [] if e['kind'] == 'dialogue']
check('save keeps the dialogue lines in order, trimmed',
      [(e['when'], e['speaker'], e['text'], e['duration']) for e in said] ==
      [('start', 'Egan', 'Here they come. Hold the line!', None),
       ('after', 'Kelsey', 'Traps first, heroics second.', 3.0),
       ('break', '', 'Ünïcode survives.', 19.9)], said)
_w, again_talk = records.validate_plan({'plan': copy.deepcopy(saved_talk)})
check('a saved plan with dialogue saves unchanged', again_talk == saved_talk)
for name, bad_line in (('no text', dict(LINES[0], text='  ')),
                       ('a colon in the speaker', dict(LINES[0], speaker='Egan: ')),
                       ('a line break', dict(LINES[0], text='one\ntwo')),
                       ('too long', dict(LINES[0], text='x' * (captions.TEXT_MAX + 1))),
                       ('a length out of range', dict(LINES[0], duration=captions.DURATION_MAX + 1)),
                       ('a delay out of range', dict(LINES[0], delay=-1)),
                       ('an unknown timing', dict(LINES[0], when='later')),
                       ('a first line after another', dict(LINES[1]))):
    try:
        hostevents.clean([bad_line])
        check('clean refuses %s' % name, False)
    except ValueError:
        check('clean refuses %s' % name, True)
check('a line\'s length from its text follows the shipped fit',
      captions.derived_duration('x' * 50) == 4.3 and captions.derived_duration('x') == 1.3
      and captions.derived_duration('x' * 1000) == captions.DURATION_MAX,
      (captions.derived_duration('x' * 50), captions.derived_duration('')))
work = missionevents.duties('Scenario_02', saved_talk)
check('duties: every line to the piece, lengths resolved',
      [(l['n'], l['when'], l['duration']) for l in work['dialogue']] ==
      [(1, 'start', captions.derived_duration(LINES[0]['text'])), (2, 'after', 3.0),
       (3, 'break', 19.9)], work['dialogue'])
check('no dialogue without the piece',
      missionevents.duties('Scenario_02', dict(saved_talk, added=[]))['dialogue'] == [])
host = hostevents.capabilities('Scenario_02')
check('a host alone plays no dialogue', not host['dialogue']['available']
      and host['dialogue']['why'] == hostevents.DIALOGUE_NEEDS, host['dialogue'])
caps = hostevents.with_piece(host, [])
_s, report = wavespec.apply_events({'waves': spec['waves'], 'events': LINES}, caps)
check('apply_events gives every line to the piece',
      all(r.get('piece') and r['status'] == wavespec.APPLIED for r in report), report)
_s, report = wavespec.apply_events({'waves': spec['waves'], 'events': LINES}, host)
check('without the piece a line is not possible',
      all(r['status'] == wavespec.NOT_POSSIBLE for r in report), report)
text = missionevents.script(work, 'MissionEventsTest')
check('script: wave-start line on a 2 s timer, break line at once, the after line chained '
      'after line 1\'s length and its delay',
      'K2_SetTimer(self, "Say1", 2.0, False)' in text and 'self.Say3()' in text
      and 'K2_SetTimer(self, "Say2", %r, False)' % (work['dialogue'][0]['duration'] + 0.5)
      in text and 'GetPlayScenarioDialogue' in text, text)
check('script: the server decides once from the difficulty and Scramble, every line waits '
      'for it, none in Endless or Scramble',
      'OMDDifficultyBlueprintLibrary.GetGameDifficulty(Current)' in text
      and 'Current == EGameDifficulty.Endless' in text and 'Instance.IsInRandomModeCampaign()' in text
      and text.count('self.Talk = 2') == 2 and 'Talk: Replicated[int]' in text
      and text.count('and self.Talk == 1:') == 2 and text.count('or self.Talk != 1:') == 3, text)
plain = missionevents.script(missionevents.duties('Scenario_02', saved), 'MissionEventsTest')
check('a piece without dialogue is scripted as before',
      'Seen' not in plain and 'Speak' not in plain and 'Say1' not in plain
      and 'Talk' not in plain and 'GetGameDifficulty' not in plain)
for line in work['dialogue']:
    path = missionevents.dialogue_path('MissionEventsTest', line['n'])
    try:
        ua, ux = captions.wave(path, line['speaker'], line['text'], line['duration'],
                               'RiftLord.MissionEventsTest', 'Line%d' % line['n'])
        rows = captions.checks(ua, ux, line['speaker'], line['text'], line['duration'])
        check('line %d wave: %s' % (line['n'], ', '.join(r['name'] for r in rows)),
              all(r['ok'] for r in rows), [r for r in rows if not r['ok']])
    except (Exception, SystemExit) as exc:            # noqa: BLE001
        check('line %d wave written' % line['n'], False, str(exc))
try:
    got = missionevents.package('Scenario_02', 'MissionEventsTest', saved_talk)
    files = {}
    for path, (ua, ux) in got.items():
        files['OMD/Content' + path[len('/Game'):] + '.uasset'] = ua
        files['OMD/Content' + path[len('/Game'):] + '.uexp'] = ux
    rows = {r['name']: r for r in missionevents.check_files(files, 'Scenario_02', saved_talk)}
    check('piece with dialogue compiles and names every line',
          rows['Mission Events plays every event it owns']['ok'],
          rows['Mission Events plays every event it owns'])
    check('the build gate reads every line back', rows['dialogue lines carry their captions']['ok'],
          rows['dialogue lines carry their captions'])
    from riftlord.formats.ue.uassetio import Package
    piece_ua = got[missionevents.package_path('MissionEventsTest')][0]
    pnames = {n for n, _h in Package(piece_ua).names}
    check('the piece with dialogue replicates its decision (Talk, the difficulty and Scramble)',
          {'Talk', 'GetGameDifficulty', 'IsInRandomModeCampaign', 'bReplicates',
           'bAlwaysRelevant'} <= pnames,
          sorted(n for n in pnames if 'Rep' in n or 'Relevant' in n or n == 'Talk'))
except (Exception, SystemExit) as exc:                # noqa: BLE001
    check('piece with dialogue compiles and names every line', False, str(exc))

# Scenario_08 fires BattleMusicChange in wave 5, but its level binds no handler to it: the
# build drops it as not possible on this host, and savetest must expect that.
try:
    from riftlord.onslaught import savetest
    caps = hostevents.capabilities('Scenario_08')
    check('Scenario_08 never hears its music change',
          'BattleMusicChange' in caps['unheard'] and not caps['music']['available'],
          caps['music'])
    spec = wavespec.read_onslaught('Scenario_08')
    check('Scenario_08 wave 5 reads its music change',
          'BattleMusicChange' in spec['waves'][4]['actions'], spec['waves'][4]['actions'])
    built, report = wavespec.with_events(spec, caps)
    want = savetest.expected(spec, 'Scenario_08')
    check('an unheard music change is not authored, and savetest expects that',
          not built['waves'][4]['actions'] and not want['waves'][4]['actions']
          and [r['status'] for r in report] == [wavespec.NOT_POSSIBLE],
          (built['waves'][4]['actions'], want['waves'][4]['actions'], report))
except (Exception, SystemExit) as exc:                 # noqa: BLE001
    check('an unheard music change is not authored, and savetest expects that', False,
          str(exc))

for name, ok, detail in checks:
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, '' if ok else ': %s' % (detail,)))
failed = [c for c in checks if not c[1]]
print('%d checks, %d failed' % (len(checks), len(failed)))
sys.exit(1 if failed else 0)
