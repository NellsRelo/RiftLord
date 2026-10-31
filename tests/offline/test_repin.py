"""buildcompare's one-command re-pin (`buildcompare.py RECORD --repin --expect GLOB`): a build
that differs from its pin only in expected entries is pinned and its BASELINES entry
rewritten; one that differs elsewhere is refused unless --accept; one that matches its pin
changes nothing; a failed build is refused.

    python3 tests/offline/test_repin.py

The comparison and the pin are stand-ins (two small paks, a fake pin); the BASELINES rewrite
goes to a copy of buildcompare-pins.json.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))

import buildcompare  # noqa: E402
from riftlord.core import paksource  # noqa: E402

RECORD = '900_Fixture_Edit_P'
TMP = tempfile.mkdtemp(prefix='repin-test-')
checks = []


def check(name, ok, detail=''):
    checks.append(ok)
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, (': %s' % (detail,)) if detail else ''))


def pak(name, files):
    path = os.path.join(TMP, name)
    paksource.write_pak(path, files)
    return path


OLD = pak('old.pak', {'OMD/Content/Mods/X/Data.bin': b'one', 'OMD/Content/Mods/X/Same.bin': b's'})
NEW = pak('new.pak', {'OMD/Content/Mods/X/Data.bin': b'two', 'OMD/Content/Mods/X/Same.bin': b's'})
pins = []


def fake_main(new_pak, built=True):
    """buildcompare.main as a comparison of OLD (the pin) with `new_pak` would end."""
    def main(argv):
        work = argv[argv.index('--out') + 1]
        os.makedirs(work, exist_ok=True)
        name = RECORD + '.pak'
        c = buildcompare.compare(OLD, new_pak)
        report = {'old': {'pak': {name: OLD}}, 'new': {'pak': {name: new_pak} if built else None},
                  'compare': {name: c}}
        with open(os.path.join(work, 'report.json'), 'w') as fh:
            json.dump(report, fh)
        same = not (c['different'] or c['only_old'] or c['only_new'])
        return 0 if same and built else 1
    return main


def run(argv, new_pak=NEW, built=True):
    buildcompare.main = fake_main(new_pak, built)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = buildcompare.repin([RECORD, '--repin', '--out', os.path.join(TMP, 'work'),
                                   '--with', 'none'] + argv)
    return code, buf.getvalue()


def main():
    copy = os.path.join(TMP, 'buildcompare-pins.json')
    shutil.copyfile(buildcompare.PINS_FILE, copy)
    real_file, real_main, real_pin = buildcompare.PINS_FILE, buildcompare.main, buildcompare.pin
    real_base = dict(buildcompare.BASELINES)
    buildcompare.PINS_FILE = copy
    buildcompare.pin = lambda record, work, side: pins.append((record, side)) or \
        'feedbeefcafe%04d' % len(pins)
    old_digest = buildcompare.BASELINES[RECORD]['digest']
    try:
        code, text = run(['--expect', '*/Other.bin'])
        check('a difference outside --expect is refused, naming the entry', code == 1 and
              'REFUSED' in text and 'Data.bin' in text and not pins, text.strip()[-200:])
        with open(copy) as fh:
            check('a refused re-pin leaves BASELINES as it was', old_digest in fh.read())
        code, text = run(['--expect', '*/Data.bin', '--why', 'test change'])
        with open(copy) as fh:
            rewritten = fh.read()
        check('a difference inside --expect is pinned', code == 0 and pins == [(RECORD, 'new')] and
              'PINNED' in text, text.strip()[-200:])
        entry = json.loads(rewritten)[RECORD]
        check('its BASELINES entry gets the new digest and says why',
              entry['digest'] == 'feedbeefcafe0001' and 'test change' in entry['what'])
        code, text = run(['--expect', '*/Other.bin', '--accept'])
        check('--accept pins a difference outside --expect', code == 0 and len(pins) == 2, text[-200:])
        code, text = run(['--expect', '*/Data.bin'], new_pak=OLD)
        check('a build matching its pin changes nothing', code == 0 and len(pins) == 2 and
              'already matches' in text, text.strip()[-200:])
        code, text = run(['--expect', '*'], built=False)
        check('a failed build is refused', code == 1 and 'REFUSED' in text and len(pins) == 2,
              text.strip()[-200:])
    finally:
        buildcompare.PINS_FILE, buildcompare.main, buildcompare.pin = real_file, real_main, real_pin
        buildcompare.BASELINES.clear()
        buildcompare.BASELINES.update(real_base)
        shutil.rmtree(TMP, ignore_errors=True)
    print('%d of %d passed' % (sum(checks), len(checks)))
    return 0 if all(checks) else 1


if __name__ == '__main__':
    raise SystemExit(main())
