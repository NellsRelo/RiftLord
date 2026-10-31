"""A Fab library rebuilt under a new build id: a record saved against the old build still
reads and builds (riftlord.mapeditor.fablibrary.current_plan, fabemit.resolve), and a mesh
the new build no longer has is named, not a crash. Uses a fake library in a scratch folder
(RIFTLORD_FAB_LIBRARY); no user record is read or written.

    python3 tests/offline/test_fabrebuild.py
"""
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

TMP = tempfile.mkdtemp(prefix='fabrebuild-test-')
os.environ['RIFTLORD_FAB_LIBRARY'] = TMP

from riftlord.mapeditor import fabemit, fablibrary  # noqa: E402

PACK = 'FakeVillage'
checks = []


def check(name, ok, detail=''):
    checks.append(ok)
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, (': %s' % (detail,)) if detail else ''))


def write_library(build, meshes):
    """The fake library's catalogue for one build: meshes [(rel path, source)]."""
    folder = os.path.join(TMP, PACK)
    os.makedirs(folder, exist_ok=True)
    rows = []
    for rel, source in meshes:
        package = '%s%s/%s/%s' % (fablibrary.GAME_ROOT, PACK, build, rel)
        rows.append({'name': rel.rsplit('/', 1)[-1], 'status': 'converted', 'source': source,
                     'package': package, 'object_path': package + '.' + rel.rsplit('/', 1)[-1]})
    cat = {'format': fablibrary.FORMAT, 'format_version': fablibrary.FORMAT_VERSION,
           'build': build, 'pack': {'title': 'Fake Village'}, 'meshes': rows}
    with open(os.path.join(folder, 'catalogue.json'), 'w') as fh:
        json.dump(cat, fh)
    time.sleep(0.01)                       # a new mtime: the catalogue cache re-reads it


def piece(build, rel, source=''):
    package = '%s%s/%s/%s' % (fablibrary.GAME_ROOT, PACK, build, rel)
    leaf = rel.rsplit('/', 1)[-1]
    return {'kind': 'fab', 'pack': PACK, 'name': fablibrary.mesh_key(package),
            'object': package + '.' + leaf, 'source': source,
            'matrix': [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]}


def main():
    write_library('Baa91576b', [('meshes/floor/SM_square', 'Village/meshes/floor/SM_square'),
                                ('meshes/wall/SM_wall', 'Village/meshes/wall/SM_wall'),
                                ('meshes/wall/SM_gone', 'Village/meshes/wall/SM_gone')])
    plan = {'added': [piece('Baa91576b', 'meshes/floor/SM_square', 'Village/meshes/floor/SM_square'),
                      piece('Baa91576b', 'meshes/wall/SM_wall'),          # saved without a source
                      piece('Baa91576b', 'meshes/wall/SM_gone')]}
    placed, problems = fabemit.resolve(plan)
    check('the old build: every piece resolves', len(placed) == 3 and not problems, problems)
    # the library is rebuilt under a new build id, and one mesh is gone from it
    write_library('B28837125', [('meshes/floor/SM_square', 'Village/meshes/floor/SM_square'),
                                ('meshes/wall/SM_wall', 'Village/meshes/wall/SM_wall')])
    now = fablibrary.current_plan(plan)
    moved = now['added']
    check('pieces move to the current build (with and without a source)',
          all('/B28837125/' in e['object'] and '/B28837125/' in e['name'] for e in moved[:2]),
          [e['object'] for e in moved[:2]])
    check('the build-free mesh path is kept', moved[1].get('mesh') == 'meshes/wall/SM_wall.SM_wall',
          moved[1].get('mesh'))
    check('a mesh the new build lacks is marked missing, and kept',
          len(moved) == 3 and 'no longer has meshes/wall/SM_gone' in (moved[2].get('missing') or ''),
          moved[2].get('missing'))
    check('the record itself is not changed', '/Baa91576b/' in plan['added'][0]['object'])
    placed, problems = fabemit.resolve(plan)              # the old record, as saved
    check('the old record builds against the new build',
          [p[2]['object_path'].split('/')[5] for p in placed] == ['B28837125', 'B28837125'],
          [p[2]['object_path'] for p in placed])
    check('the vanished mesh is refused by name',
          problems == ['the Fab library FakeVillage no longer has the mesh meshes/wall/SM_gone.SM_gone '
                       '(in its current build)'], problems)
    print('%d checks, %d failed' % (len(checks), checks.count(False)))
    return 0 if all(checks) else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
