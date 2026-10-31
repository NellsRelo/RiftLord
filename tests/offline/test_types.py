"""Offline checks on Setup's type data (riftlord/types): the rules on fixtures, then, where the
installs are on this machine (built into RIFTLORD_TYPES, else a folder of the tests' own, never
the user's cache), the exe's reflection, the engine headers, the merged database against
bpkit's old typedb.json and the CXXHeaderDump, and a Mission Events piece built with
both databases, byte for byte.

    python3 tests/offline/test_types.py [--quick]

--quick runs the fixture checks only. A part whose install is missing says SKIP and passes.
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from riftlord.core import gamepath  # noqa: E402
from riftlord.types import agree, merge, setup  # noqa: E402

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


def skip(name):
    checks.append(('SKIP ' + name, True, ''))


# -- 1. rules, on fixtures -----------------------------------------------------------------

def dump(name, values, form):
    e = {'values': values, 'numbers': list(range(len(values))), 'form': form}
    return merge._dump_values(name, e)[0]


check('enum: a user _Max last is dropped (ECollisionTypeEnum)',
      dump('ECollisionTypeEnum', ['ECollisionTypeEnum::Chaos_Volumetric',
                                  'ECollisionTypeEnum::Chaos_Max'], 2) == ['Chaos_Volumetric'])
check('enum: a _Max not of the prefix stays (EFieldCullingOperationType)',
      dump('EFieldCullingOperationType', ['Field_Culling_Inside', 'Field_Culling_Outside',
                                          'Field_Culling_Operation_Max'], 0) ==
      ['Field_Culling_Inside', 'Field_Culling_Outside', 'Field_Culling_Operation_Max'])
check('enum: the prefix is case-sensitive (EFieldFalloffType)',
      dump('EFieldFalloffType', ['Field_FallOff_None', 'Field_Falloff_Linear',
                                 'Field_Falloff_Max'], 0)[-1] == 'Field_Falloff_Max')
check('enum: a namespaced MAX stays (EAILockSource)',
      dump('EAILockSource', ['EAILockSource::Logic', 'EAILockSource::MAX'], 1) == ['Logic', 'MAX'])
check('enum: run time adds <Name>_MAX',
      merge.runtime_values('ENavLinkDirection', ['ENavLinkDirection::BothWays'], 1) ==
      ['BothWays', 'ENavLinkDirection_MAX'])
got = merge.game_param({'name': 'Goal', 'type': 'struct:Vector', 'flags': 0x8000182})
check('param: const ref is not out', not got['out'] and not got['ref'] and got['const'], got)
got = merge.game_param({'name': 'Out', 'type': 'int', 'flags': 0x180})
check('param: a reference out', got['out'] and not got['ref'] and not got['const'], got)
got = merge.game_param({'name': 'ReturnValue', 'type': 'bool', 'flags': 0x580})
check('param: the return value', got['return'] and not got['out'], got)
got = merge.game_param({'name': 'Text', 'type': 'str', 'flags': 0x80})
check('param: a string by value is const, as the dump writes it', got['const'], got)
check('specifiers from flags', merge.specifiers(0x84220cc0) ==
      ['BlueprintCallable', 'Reliable', 'Server', 'WithValidation'], merge.specifiers(0x84220cc0))
check('meta from parameter names',
      merge.meta([{'name': 'worldContextObject'}, {'name': 'LatentInfo'}]) ==
      {'WorldContext': 'worldContextObject', 'Latent': '', 'LatentInfo': 'LatentInfo'})
game = {'classes': {'OMDTrap': {'super': 'Actor'}, 'Actor': {'super': 'Object'}},
        'enums': {'EBTParallelMode': {'cpp': 'EBTParallelMode::Type'}},
        'delegates': {'Widget:GetText__DelegateSignature': {'owner': 'Widget'}}}
raw = {'classes': {}}
cases = [({'kind': 'Object', 'class': 'OMDTrap'}, 'AOMDTrap*'),
         ({'kind': 'Class', 'meta_class': 'Object'}, 'UClass*'),
         ({'kind': 'Byte', 'enum': 'EBTParallelMode'}, 'TEnumAsByte<EBTParallelMode::Type>'),
         ({'kind': 'Delegate', 'signature': 'GetText__DelegateSignature'}, 'UWidget::FGetText'),
         ({'kind': 'Array', 'inner': {'kind': 'Struct', 'struct': 'Vector'}}, 'TArray<FVector>')]
for p, want in cases:
    got = merge.cpp_text(p, game, raw, 'EditableText')
    check('C++ text %s' % want, got == want, got)

with tempfile.TemporaryDirectory(prefix='types-test-') as tmp:
    exe = os.path.join(tmp, 'OMD-Win64-Shipping.exe')
    with open(exe, 'wb') as fh:
        fh.write(b'MZ')
    real_exe, real_ue, real_folder = setup.exe_path, setup.ue_root, os.environ.get(setup.ENV)
    os.environ[setup.ENV] = os.path.join(tmp, 'cache')
    try:
        setup.exe_path = lambda: exe
        k1 = setup.key('game')
        os.utime(exe, (1, 1))
        check('a changed input changes its key', setup.key('game') != k1)
        setup.ue_root = lambda: None
        st = setup.status()
        check('no UE 4.27: unavailable, with the one line', st['state'] == 'unavailable' and
              st['message'] == setup.NEEDS_UE, st)
        check('no UE 4.27: Mission Events unavailable',
              setup.unavailable() == setup.NEEDS_UE)
        check('the three steps and the terrain kits, labels only', [s['label'] for s in st['steps']] ==
              ['Engine types', 'Game types', 'Blueprints', 'Terrain'])
        setup.ue_root = lambda: tmp
        check('UE without its headers: unavailable', setup.missing_message() == setup.NEEDS_UE)
    finally:
        setup.exe_path, setup.ue_root = real_exe, real_ue
        if real_folder is None:
            os.environ.pop(setup.ENV, None)
        else:
            os.environ[setup.ENV] = real_folder
        setup._NEEDED['at'] = 0.0

# -- 2. the real installs --------------------------------------------------------------------

QUICK = '--quick' in sys.argv
#: The type data these checks build and read: RIFTLORD_TYPES (the gate's prepared folder),
#: else a folder of the tests' own on scratch; never the user's cache.
if not os.environ.get(setup.ENV):
    sys.path.insert(0, os.path.join(ROOT, 'tools'))
    import testservers  # noqa: E402
    os.environ[setup.ENV] = os.path.join(testservers.SCRATCH, 'types-test')
    setup._NEEDED['at'] = 0.0
#: The converter's own bpkit caches and the CXX header dump, compared against when present.
OLD = os.path.join(gamepath.WIN64, 'OMD2-Converter', 'cache')
OLD_DB = os.path.join(OLD, 'typedb.json')
CXX = os.path.join(gamepath.WIN64, 'CXXHeaderDump')
#: A Mission Events piece with every duty: music, a boss line, a rift opened and closed.
PLAN = {'level': 'Scenario_02', 'version': 2,
        'spawners': [{'id': 1, 'at': [4954.0, 2552.0, 250.0], 'rotation': 0.0, 'kind': 'ground',
                      'index': 3, 'maxSpawnPerRow': 0, 'door': None, 'start': 'OMDPathPoint13',
                      'flyerStart': None, 'unstable': True}],
        'added': [{'name': 'Mission Events', 'kind': 'events',
                   'matrix': [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]}],
        'events': [{'kind': 'music', 'wave': 1, 'track': 'War_01'},
                   {'kind': 'boss', 'wave': 1, 'unit': 'OgreBasher', 'door': 1, 'count': 1,
                    'reaction': True},
                   {'kind': 'rift', 'wave': 1, 'rift': 3, 'open': True},
                   {'kind': 'rift', 'wave': 2, 'rift': 3, 'open': False}]}
BUILD = '''
import hashlib, json, os, sys
sys.path.insert(0, %r)
import riftlord
from riftlord.types import setup
setup.ready = lambda: True
from bpkit import typedb, replication
typedb.CACHE = os.path.join(%r, 'typedb.json')
typedb.NATIVE_SIZES = os.path.join(%r, 'native_struct_sizes.json')
replication.CACHE = os.path.join(%r, 'replicated.json')
from riftlord.missions import missionevents
got = missionevents.package('Scenario_02', 'RiftLordTypesTest', json.load(open(%r)))
print(json.dumps({p: [hashlib.sha256(a).hexdigest(), hashlib.sha256(b).hexdigest()]
                  for p, (a, b) in got.items()}))
'''


def need(name, ok):
    if not ok:
        skip(name)
    return ok


def build_both():
    """{'old'|'new': {package: [uasset sha256, uexp sha256]} or the error}."""
    builds = {}
    with tempfile.TemporaryDirectory(prefix='types-test-') as tmp:
        plan = os.path.join(tmp, 'plan.json')
        with open(plan, 'w') as fh:
            json.dump(PLAN, fh)
        for which in ('old', 'new'):
            caches = os.path.join(tmp, which)
            os.makedirs(caches)
            for name in setup.MERGED:
                src = os.path.join(OLD if which == 'old' else setup.folder(), name)
                with open(src, 'rb') as a, open(os.path.join(caches, name), 'wb') as b:
                    b.write(a.read())
            run = subprocess.run([sys.executable, '-c', BUILD % (ROOT, caches, caches, caches,
                                                                 plan)],
                                 capture_output=True, text=True, cwd=tmp)
            lines = [ln for ln in run.stdout.splitlines() if ln.startswith('{')]
            builds[which] = json.loads(lines[-1]) if lines else run.stderr[-400:]
    return builds


if not QUICK and need('setup run (no game or no UE 4.27 here)', setup.missing_message() is None):
    if setup.needed(fresh=True):
        from riftlord.core import heavyslot
        with heavyslot.slot('test_types'):
            setup.run()
    check('setup: ready, nothing needed', setup.ready() and not setup.needed(fresh=True),
          setup.needed(fresh=True))
    st = setup.state()
    check('setup: each step timed', all(k in st['seconds'] for k in (
        'engine', 'game', 'blueprints', 'merge')), st.get('seconds'))
    game = json.load(open(os.path.join(setup.folder(), 'game.json')))
    check('exe: every table named and joined', game['problems'] == [], game['problems'][:3])
    counts = {k: len(game[k]) for k in ('classes', 'structs', 'enums', 'functions', 'delegates')}
    check('exe: tables read', counts['classes'] >= 2700 and counts['structs'] >= 1850 and
          counts['enums'] >= 1050 and counts['functions'] >= 8500 and counts['delegates'] >= 360,
          counts)
    engine = json.load(open(os.path.join(setup.folder(), 'engine.json')))
    check('engine headers read', len(engine['raw']['classes']) >= 1000 and
          len(engine['raw']['structs']) >= 700, engine['report'])
    new = json.load(open(os.path.join(setup.folder(), 'typedb.json')))

    if need('CXXHeaderDump agreement (no %s)' % CXX, os.path.isdir(CXX)):
        rows = agree.compare_cxx(game, agree.read_cxx(CXX)).data()
        for field in ('classes in the CXX dump', 'structs in the CXX dump', 'classes super',
                      'structs super', 'classes property names', 'structs property names',
                      'classes property offsets', 'structs property offsets', 'struct sizes'):
            row = rows.get(field, {})
            check('CXX dump: %s (%d same, %d case)' % (field, row.get('same', 0),
                                                      row.get('case', 0)),
                  row.get('differ', 1) == 0, row.get('examples'))

    if need('old typedb agreement (no %s)' % OLD_DB, os.path.isfile(OLD_DB)):
        old = json.load(open(OLD_DB))
        rows = agree.compare_db(old, new).data()
        #: fields that agree in full (spelling case aside)
        exact = [r for r in rows if (r.startswith('engine ') or r.startswith('blueprint ')) and
                 'usmap' not in r] + [
            'game classes present', 'game classes super', 'game classes module',
            'game classes kind', 'game classes interfaces', 'game classes properties names',
            'game structs present', 'game structs super', 'game structs module',
            'game structs properties names', 'game enums kind', 'game enums module',
            'game delegates present', 'game delegates owner', 'game delegates module',
            'game delegates multicast', 'game delegates param type', 'game functions owner',
            'game functions static', 'game functions param count', 'game functions param out',
            'game functions param return']
        for field in exact:
            row = rows.get(field)
            if row is None:
                continue
            check('old typedb: %s (%d same, %d case)' % (field, row['same'], row['case']),
                  row['differ'] == 0, row['examples'][:3])
        #: fields where the exe holds the truth the old database lacked, each difference known
        known = {'game classes properties values': 16, 'game structs properties values': 12,
                 'game functions param type': 1, 'game functions present': 2,
                 'game enums numbers': 2, 'game enums present': 1,
                 'game functions param name': 13, 'game functions call opcode': 321}
        for field, most in known.items():
            row = rows.get(field, {'differ': 0})
            check('old typedb: %s differs in at most %d (%d)' % (field, most, row['differ']),
                  row['differ'] <= most, row.get('examples', [])[:3])

    if need('Mission Events built with both databases (no %s)' % OLD_DB, os.path.isfile(OLD_DB)):
        builds = build_both()
        check('Mission Events: built with the old database', isinstance(builds['old'], dict),
              builds['old'])
        check('Mission Events (music, boss line, rift open and close): byte-identical with the '
              'new database', builds['old'] == builds['new'], builds)

for name, ok, detail in checks:
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, '' if ok else ': %s' % (detail,)))
failed = [c for c in checks if not c[1]]
print('%d checks, %d failed' % (len(checks), len(failed)))
sys.exit(1 if failed else 0)
