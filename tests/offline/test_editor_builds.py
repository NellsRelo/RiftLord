"""Offline checks on which builds bake a mission's light maps (riftlord/missions/bucket.py,
riftlord/build/lighting/lightbake.py build_as): an editor's build never does, the Missions
page's does.

    python3 test_editor_builds.py

- lightbake.build_as: inside an editor's block (bake=False) decide() keeps the shipped light
  maps, says the build's kind and leaves the stale reasons in pending(); a Missions build
  bakes them and leaves nothing pending; the kind is the thread's own and ends with the block.
- bucket.bake runs an editor's build (editor=...) without the light bake and any other as
  the Missions build; opening a stale mission in an editor (item(built=True)) and saving
  into one (target) are editor builds.
- A current build whose light bake is pending is 'unlit': listed with its reasons, not
  current for Install and Package (needs_build, refuse_unbuilt), current for the editors
  (is_stale), and 'built' again once a build leaves nothing pending.

Reads no game files; a scratch Missions folder. Exit 0 when every check passes.
"""

import contextlib
import io
import os
import shutil
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from riftlord.build.lighting import lightbake  # noqa: E402

#: A plan whose light maps are stale (a removed piece) and the reasons stale() gives.
PLAN = {'level': 'Scenario_16', 'removed': [6, 7]}
REASONS = ['2 removed pieces']
IDENT = '900_Editor_Builds_P'

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))
    print('  %s  %s%s' % ('PASS' if ok else 'FAIL', name, (': ' + str(detail)) if detail else ''))


def said(plan):
    """(decide(plan), what it printed)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        got = lightbake.decide(plan)
    return got, out.getvalue().strip()


def kinds():
    # -- lightbake.build_as ------------------------------------------------------------
    with lightbake.build_as('editor build (opening it in an editor)', bake=False):
        got, text = said(PLAN)
        check('an editor\'s build does not bake', not got, text)
        check('and says its kind and who bakes instead',
              text.startswith('lightbake: editor build (opening it in an editor): not baking')
              and 'Build, Install or Package' in text, text)
        check('its stale reasons are pending', lightbake.pending() == REASONS,
              lightbake.pending())
        got, text = said({'level': 'Scenario_16'})
        check('nothing stale, nothing pending', not got and lightbake.pending() == [], text)
        seen = {}
        th = threading.Thread(target=lambda: seen.update(zip(('got', 'text'), said(PLAN))))
        th.start()
        th.join()
        check('another thread\'s build is not an editor\'s', seen['got'], seen['text'])
    with lightbake.build_as('Missions build'):
        got, text = said(PLAN)
        check('a Missions build bakes, saying so',
              got and text == 'lightbake: Missions build: bakes its light maps: 2 removed pieces',
              text)
        check('and leaves nothing pending', lightbake.pending() == [])
    got, text = said(PLAN)
    check('after the block, the default kind', got and text.startswith('lightbake: build:'), text)


def bucket_rules(tmp):
    os.environ['OMD3_BUCKET'] = tmp
    from riftlord.missions import bucket
    bucket.activate()
    os.makedirs(os.path.join(tmp, '.riftlord', 'records'), exist_ok=True)
    os.makedirs(os.path.join(tmp, IDENT, 'OMD', 'Content'))          # a loose build
    rec = {'id': IDENT, 'kind': 'map', 'level': 'Scenario_16', 'plan': dict(PLAN),
           'title': 'Editor Builds'}
    rec['baked'] = bucket.bake_inputs(rec)
    bucket._write_json(bucket._record_path(IDENT), rec)
    have_build = bucket._source(IDENT) is not None
    check('the scratch mission has a build', have_build)

    # -- which builds bake ---------------------------------------------------------------
    calls = []
    real_bake_inner, real_bake = bucket._bake, bucket.bake

    def fake_inner(ident, note):
        calls.append(said(bucket.load(ident)['plan']))
        return os.path.join(tmp, ident)
    bucket._bake = fake_inner
    try:
        bucket.bake(IDENT, editor='opening it in an editor')
        bucket.bake(IDENT)
    finally:
        bucket._bake = real_bake_inner
    check('bucket.bake with editor= skips the light bake',
          calls and not calls[0][0] and 'editor build (opening it in an editor)' in calls[0][1],
          calls[:1])
    check('bucket.bake without it is the Missions build, which bakes',
          len(calls) == 2 and calls[1][0] and 'Missions build' in calls[1][1], calls[1:])

    asked = []
    bucket.bake = lambda ident, note=None, editor=None: asked.append(editor)
    try:
        stale = dict(bucket.load(IDENT), baked='old')
        bucket._write_json(bucket._record_path(IDENT), stale)
        bucket.item(IDENT, built=True)
        try:
            bucket.target(IDENT)
        except SystemExit:
            pass                                     # the scratch build has no mission row
    finally:
        bucket.bake = real_bake
    check('opening and saving into a stale mission are editor builds',
          asked == ['opening it in an editor', 'saving into it from an editor'], asked)

    # -- the record of a build that left its light bake ------------------------------------
    rec = dict(bucket.load(IDENT), lightPending=REASONS)
    rec['baked'] = bucket.bake_inputs(rec)
    bucket._write_json(bucket._record_path(IDENT), rec)
    rec = bucket.load(IDENT)
    check('a build whose light bake is pending is unlit',
          bucket.build_state(rec, bucket._source(IDENT)) == 'unlit')
    check('editors take it as current', not bucket.is_stale(rec))
    check('Install and Package do not', bucket.needs_build(rec))
    try:
        bucket.refuse_unbuilt([IDENT], 'installed')
        why = None
    except SystemExit as exc:
        why = str(exc)
    check('Install refuses it, naming the lighting', why and 'lighting is still to bake' in why,
          why)
    real_rows = bucket._mission_rows
    #: the scratch build's mission row, as a build's MissionProtos lists it
    bucket._mission_rows = lambda path: [('EditorBuilds_Mission01', 'Editor Builds', '0' * 32)]
    try:
        row = next((i for i in bucket.items()['items'] if i['id'] == IDENT), {})
    finally:
        bucket._mission_rows = real_rows
    check('the Missions page lists it unlit, with why',
          row.get('state') == 'unlit' and row.get('lighting') == REASONS,
          {k: row.get(k) for k in ('state', 'lighting')})
    rec.pop('lightPending')
    bucket._write_json(bucket._record_path(IDENT), rec)
    check('built again once nothing is pending',
          bucket.build_state(bucket.load(IDENT), bucket._source(IDENT)) == 'built'
          and not bucket.needs_build(bucket.load(IDENT)))


def run():
    kinds()
    tmp = tempfile.mkdtemp(prefix='editor-builds-')
    try:
        bucket_rules(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    bad = [c for c in checks if not c[1]]
    print('%d of %d checks pass' % (len(checks) - len(bad), len(checks)))
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(run())
