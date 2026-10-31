"""A mission whose build carries a mod's minions goes out of date when that mod's installed
pak changes (riftlord.missions.bucket.carried_changed / is_stale), and a mod that is not
installed changes nothing, and a build from before the `carried` stamp that carries a mod
counts it as changed. Likewise for the kits Rift Lord builds on this machine (the
Chandelier's): the `kits` stamp, a kit built differently since, an unstamped build, and
--rebuild --install refusing a build whose kit differs.

    python3 tests/offline/test_carried_stale.py

Stand-in paks are written to a temporary folder; nothing of the game install is written.
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from riftlord.missions import bucket, modminions  # noqa: E402

checks = []


def check(name, ok, detail=''):
    checks.append(bool(ok))
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, (': %s' % (detail,)) if detail else ''))


def main():
    tmp = tempfile.mkdtemp(prefix='carried-stale-')
    try:
        pak = os.path.join(tmp, 'Wraiths_P.pak')
        with open(pak, 'wb') as fh:
            fh.write(b'version one')
        installed = {'Wraiths': {'pak': pak, 'own': True},
                     'Copied': {'pak': os.path.join(tmp, 'gone.pak'), 'own': False}}
        modminions.mods = lambda: installed
        rec = {'carried': {'Wraiths': bucket._pak_digest(pak)}}
        check('a just-built mission is current', bucket.carried_changed(rec) == [],
              bucket.carried_changed(rec))
        with open(pak, 'wb') as fh:
            fh.write(b'version two!')
        check('updating the carried mod makes it out of date',
              bucket.carried_changed(rec) == ['Wraiths'], bucket.carried_changed(rec))
        rec['carried']['Copied'] = 'abc'
        check('a mod that is not installed changes nothing',
              bucket.carried_changed(rec) == ['Wraiths'], bucket.carried_changed(rec))
        check('a record with no build changes nothing', bucket.carried_changed({}) == [])
        bucket._source = lambda ident: tmp
        bucket.carried_mods = lambda path, own=None: {'Wraiths': 'old'}
        check('a build from before the stamp that carries a mod counts it as changed',
              bucket.carried_changed({'id': 'Old', 'bakedAt': 1}) == ['Wraiths'])
        rec = {'carried': {'Wraiths': bucket._pak_digest(pak)}}
        os.utime(pak, ns=(1, 1))
        check('the same bytes with a new mtime are still current',
              bucket.carried_changed(rec) == [], bucket.carried_changed(rec))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    kits()
    print('%d checks, %d failed' % (len(checks), checks.count(False)))
    return 0 if all(checks) else 1


class FakeKit:
    """A machine-built kit as bucket._kit_modules lists one, built from `files`."""
    KIT_ROOT = '/Game/Mods/FakeKit'

    def __init__(self, files):
        self.files = files

    def kit(self):
        return dict(self.files)

    def digest(self, files):
        from riftlord.mapeditor import chandelier
        keep = chandelier.KIT_ROOT
        chandelier.KIT_ROOT = self.KIT_ROOT
        try:
            return chandelier.digest(files)
        finally:
            chandelier.KIT_ROOT = keep


def kits():
    """Machine-built kits (the Chandelier's): a build stamps the kits it carries; a kit built
    differently since makes it out of date; an unstamped build carrying a kit is out of date;
    --rebuild --install refuses a build whose kit differs."""
    from riftlord.core import paksource
    from riftlord.mapeditor import chandelier
    entry = 'OMD/Content/Mods/FakeKit/BP_Fake.uasset'
    tmp = tempfile.mkdtemp(prefix='carried-kits-')
    keep = (bucket._kit_modules, bucket._source, dict(bucket._KIT_NOW))
    try:
        fake = FakeKit({entry: b'kit one'})
        bucket._kit_modules = lambda: {'FakeKit': fake}
        pak = os.path.join(tmp, 'Mission_P.pak')
        paksource.write(pak, {entry: b'kit one', 'OMD/Content/Mods/Mission/Other.uasset': b'x'})
        bucket._source = lambda ident: pak
        stamp = bucket.carried_kits(pak)
        check('a build stamps the kit it carries', list(stamp) == ['FakeKit'] and stamp['FakeKit']
              == fake.digest({entry: b'kit one'}), stamp)
        bucket._KIT_NOW.clear()
        rec = {'id': 'Mission', 'kits': stamp}
        check('a mission carrying the kit built now is current', bucket.kits_changed(rec) == [])
        check('--install takes a build carrying the kit built now',
              bucket.kit_problems('Mission') == [], bucket.kit_problems('Mission'))
        fake.files = {entry: b'kit two'}
        bucket._KIT_NOW.clear()
        check('a kit built differently since makes it out of date',
              bucket.kits_changed(rec) == ['FakeKit'], bucket.kits_changed(rec))
        check('--rebuild --install refuses a build whose kit differs',
              len(bucket.kit_problems('Mission')) == 1, bucket.kit_problems('Mission'))
        check('a build from before the kits stamp that carries a kit is out of date',
              bucket.kits_changed({'id': 'Mission', 'bakedAt': 2}) == ['FakeKit'])
        plain = os.path.join(tmp, 'Plain_P.pak')
        paksource.write(plain, {'OMD/Content/Mods/Plain/Other.uasset': b'x'})
        bucket._source = lambda ident: plain
        check('a build carrying no kit stamps none and is current',
              bucket.carried_kits(plain) == {}
              and bucket.kits_changed({'id': 'Plain', 'bakedAt': 3}) == [])
        check('the Chandelier is a listed kit', chandelier.MOD_KEY in keep[0]())
        a = chandelier.digest({chandelier.inner(chandelier.PACKAGE) + '.uasset': b'a'})
        b = chandelier.digest({chandelier.inner(chandelier.PACKAGE) + '.uasset': b'b'})
        check('the Chandelier kit digest follows its bytes and ignores other files',
              a != b and a == chandelier.digest({chandelier.inner(chandelier.PACKAGE) + '.uasset': b'a',
                                                 'OMD/Content/Mods/Other/X.uasset': b'z'})
              and chandelier.digest({'OMD/Content/Mods/Other/X.uasset': b'z'}) is None)
    finally:
        bucket._kit_modules, bucket._source = keep[0], keep[1]
        bucket._KIT_NOW.clear()
        bucket._KIT_NOW.update(keep[2])
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
