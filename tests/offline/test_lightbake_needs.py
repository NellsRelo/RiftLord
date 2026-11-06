"""Offline checks on when a build bakes its light maps (riftlord/build/lighting/lightbake.py:
stale, needs, decide, baker_missing).

    python3 test_lightbake_needs.py

- A plan bakes when the shipped light maps are stale: a sky, liquid or plane look, its own
  light colours, pieces removed or moved, or meshes and kit pieces added
  (lightscene.OCCLUDING_KINDS, lightalloc.RECEIVE_KINDS). Added Fab, effect and Crystal
  pieces alone do not bake: the baker does not read them.
- A level from scratch bakes like any other when it adds a mesh or kit piece (its light
  map receivers); with only Fab pieces it has nothing to bake into, and decide() says so.
- decide() says on the console why a build bakes or why not.
- A frozen Rift Lord finds its baker module and its packages (baker_missing is None), and
  says so when the packages are missing; decide() then keeps the shipped light maps.
- The gate's fixture records: 900_Fixture_Edit_P and 900_Fixture_Scratch_P both bake.

Reads no game files. Exit 0 when every check passes.
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from riftlord.build.lighting import lightbake  # noqa: E402

RECORDS = os.path.join(ROOT, 'tests', 'fixtures', 'missions', '.riftlord', 'records')
#: A placement, as plans store them (column-major 4x4).
MATRIX = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 100.0, 200.0, 0.0, 1.0]

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))
    print('  %s  %s%s' % ('PASS' if ok else 'FAIL', name, (': ' + str(detail)) if detail else ''))


def added(kind):
    return {'name': 'Floor_4x4', 'kind': kind, 'matrix': MATRIX}


def said(plan):
    """(decide(plan), what it printed)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        got = lightbake.decide(plan)
    return got, out.getvalue().strip()


@contextlib.contextmanager
def frozen(with_packages):
    """The process looks like a PyInstaller build whose bundle folder holds the baker's
    packages zip or not."""
    folder = tempfile.mkdtemp(prefix='bakeneeds-')
    if with_packages:
        zipfile.ZipFile(os.path.join(folder, lightbake.FROZEN_PACKAGES), 'w').close()
    sys.frozen, sys._MEIPASS = True, folder
    try:
        yield
    finally:
        del sys.frozen, sys._MEIPASS
        shutil.rmtree(folder, ignore_errors=True)


def record_plan(name):
    with open(os.path.join(RECORDS, name + '.json')) as fh:
        return json.load(fh).get('plan')


def run():
    # -- which sublevels a bake rewrites, levels stored with a doubled slash included --
    was = lightbake._bake_now
    lightbake._bake_now = True
    try:
        for level in ('Scenario_01', 'Scenario_Ice_02'):
            got = lightbake.sublevel_tags(level)
            check('%s: every sublevel with light maps is baked' % level,
                  got == list(lightbake.BAKE_SUBLEVELS), got)
    finally:
        lightbake._bake_now = was
    # -- the bake cache key: a piece without a light map (type none) ------------------
    import numpy as np
    from riftlord.build.lighting import lightbaker

    class Baker:
        lights, light_array = [], np.zeros((0, 1, 4))
        comps = {'P': [
            {'export': 3, 'mesh': 'Floor_4x4', 'matrices': [np.eye(4)], 'guid': b'\1' * 16,
             'occluder': True, 'lightmap': {'type': 2, 'textures': [1, 2],
                                            'coord_bias': (0.0, 0.0)}},
            {'export': 4, 'mesh': 'Rock_01', 'matrices': [np.eye(4)], 'guid': b'\2' * 16,
             'occluder': True, 'lightmap': {'type': 0}}]}
    try:
        key = lightbaker.sublevel_key(Baker, 'P', None, {})
        check('the bake cache key takes a piece without a light map', len(key) == 20, key)
    except Exception as exc:                         # noqa: BLE001
        check('the bake cache key takes a piece without a light map', False, repr(exc))

    # -- what makes the shipped light maps stale -------------------------------------
    check('an empty plan does not bake', not lightbake.needs({}))
    check('no plan does not bake', not lightbake.needs(None))
    for look in ({'sky': 'Dawn_Sky_INST'}, {'liquid': 'Acid_Surface_Inst'},
                 {'planes': {'1': 'Lava_01_Inst'}}):
        check('a %s bakes' % next(iter(look)), lightbake.needs({'look': look}))
    check('the plan\'s own light colours bake',
          lightbake.needs({'lightColours': {'Lighting|12': '#00890c'}}))
    check('light colours that are not colours do not',
          not lightbake.needs({'lightColours': {'Lighting|12': 'green'}}))
    check('a removed piece bakes', lightbake.needs({'removed': [6]}))
    check('a moved piece bakes',
          lightbake.needs({'edits': [{'id': 7, 'mesh': 'Floor_4x4', 'matrix': MATRIX}]}))
    check('an added mesh bakes', lightbake.needs({'added': [added('mesh')]}))
    check('an added piece without a kind is a mesh and bakes',
          lightbake.needs({'added': [{'name': 'Floor_4x4', 'matrix': MATRIX}]}))
    check('an added kit piece bakes', lightbake.needs({'added': [added('kit')]}))
    for kind in ('fab', 'fx', 'crystal'):
        check('an added %s alone does not bake' % kind,
              not lightbake.needs({'added': [added(kind)]}))
    check('reasons are counted',
          lightbake.stale({'look': {'sky': 'Dawn_Sky_INST'}, 'removed': [1, 2],
                           'edits': [{'id': 3, 'matrix': MATRIX}],
                           'added': [added('mesh'), added('kit'), added('fab')]})
          == ['sky', '2 removed pieces', '1 moved piece', '2 added pieces'])

    # -- a level from scratch ---------------------------------------------------------
    scratch = {'scratch': {'version': 1, 'base': 'Scenario_01', 'name': 'Test'},
               'look': {'sky': 'Dawn_Sky_INST'}, 'added': [added('mesh')]}
    check('a level from scratch adding a mesh bakes', lightbake.needs(scratch))
    got, text = said(scratch)
    check('decide() bakes a scratch level and says why',
          got and text == 'lightbake: build: bakes its light maps: 1 added piece', text)
    check('a scratch level\'s sky alone does not bake (its sky light is Movable)',
          not lightbake.needs(dict(scratch, added=[])))
    fab_only = dict(scratch, added=[added('fab')])
    check('a level from scratch with only Fab pieces does not bake',
          not lightbake.needs(fab_only))
    got, text = said(fab_only)
    check('decide() says a scratch level has nothing to bake into',
          not got and 'no piece of the level takes a light map' in text, text)

    # -- what the console says ------------------------------------------------------
    got, text = said({'removed': [6], 'added': [added('mesh')]})
    check('decide() says a build bakes and why',
          got and text == 'lightbake: build: bakes its light maps: 1 removed piece, '
                          '1 added piece', text)
    got, text = said({'added': [added('fab')]})
    check('decide() says a build does not bake and why',
          not got and 'changes nothing the shipped light maps hold' in text, text)
    was = lightbake.BAKE
    try:
        lightbake.BAKE = False
        got, text = said({'removed': [6]})
        check('switched off, decide() says so', not got and 'switched off' in text, text)
        lightbake.BAKE = True
        got, text = said({})
        check('switched on, an unchanged plan bakes', got and 'BAKE is on' in text, text)
    finally:
        lightbake.BAKE = was

    # -- a frozen Rift Lord ---------------------------------------------------------
    with frozen(with_packages=True):
        why = lightbake.baker_missing()
        check('a frozen build finds its baker module and packages', why is None, why)
        got, text = said({'removed': [6]})
        check('a frozen build bakes', got, text)
    with frozen(with_packages=False):
        why = lightbake.baker_missing()
        check('a frozen build without the packages says so',
              why and 'neither Mitsuba nor moderngl' in why, why)
        got, text = said({'removed': [6]})
        check('and keeps the shipped light maps, saying why',
              not got and 'NOT baking' in text and '1 removed piece' in text, text)
    check('from source the baker is never missing', lightbake.baker_missing() is None)

    # -- the gate's fixture records ---------------------------------------------------
    check('900_Fixture_Edit_P bakes', lightbake.needs(record_plan('900_Fixture_Edit_P')))
    check('900_Fixture_Scratch_P bakes',
          lightbake.needs(record_plan('900_Fixture_Scratch_P')))

    bad = [c for c in checks if not c[1]]
    print('%d of %d checks pass' % (len(checks) - len(bad), len(checks)))
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(run())
