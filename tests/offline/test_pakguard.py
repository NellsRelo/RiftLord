"""Offline checks on changing the game's paks while it runs (riftlord/core/pakguard.py),
in a fake Paks folder with a flag file standing for the running game: a new pak is added at
once and atomically; a replacement, removal or rename of an existing one waits until the
game closes and then completes by itself; a pak outside the game's folder is never held;
the game's process is told from a command line that merely names it; and the Missions
page's Install hands a held install to the background and shows it waiting.

    python3 test_pakguard.py
"""
import os
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from riftlord.core import gamepath  # noqa: E402
from riftlord.core import pakguard  # noqa: E402
from riftlord.core import paksource  # noqa: E402
from riftlord.formats.ue import pakv4  # noqa: E402

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


root = tempfile.mkdtemp(prefix='pakguard-test-')
paks = os.path.join(root, 'Paks')
mods = os.path.join(paks, '~mods')
logic = os.path.join(paks, 'LogicMods')
outside = os.path.join(root, 'Missions')
for d in (mods, logic, outside):
    os.makedirs(d)
flag = os.path.join(root, 'game-running')
os.environ[gamepath.GAME_FLAG_ENV] = flag
pakguard._game_paks = lambda: os.path.realpath(paks)
pakguard.POLL = 0.05
said = []


def files(text):
    return {'OMD/Content/Mods/Test/Note.txt': text.encode()}


def content(path):
    return paksource.read(path, 'OMD/Content/Mods/Test/Note.txt').decode()


def later(fn):
    """Run `fn` on a thread; (thread, {'done': bool, 'error': ...})."""
    state = {'done': False, 'error': None}

    def run():
        try:
            fn()
        except BaseException as exc:                 # noqa: BLE001
            state['error'] = exc
        state['done'] = True
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, state


try:
    # the game's process
    check('a Proton command line running the exe is the game',
          gamepath._is_game_args(['Z:\\games\\OMD\\Binaries\\Win64\\OMD-Win64-Shipping.exe', '-x'])
          and gamepath._is_game_args(['/usr/bin/wine64-preloader', '/usr/bin/wine64',
                                      'C:\\OMD\\Binaries\\Win64\\OMD-Win64-Shipping.exe']), '')
    check('a command merely naming it is not',
          not gamepath._is_game_args(['grep', '-i', 'OMD-Win64-Shipping.exe'])
          and not gamepath._is_game_args(['python3', '-c', 'print("OMD-Win64-Shipping.exe")']),
          '')
    check('the flag file stands for the game', not gamepath.game_running(), '')

    # the game closed: everything at once
    old = os.path.join(mods, '900_Old_P.pak')
    pakv4.write(old, files('one'))
    paksource.write(old, files('two'), keep_omui=False)
    check('with the game closed a pak is replaced at once', content(old) == 'two', '')

    open(flag, 'w').close()
    check('with the flag the game runs', gamepath.game_running(), '')

    # a new pak: at once, atomically
    new = os.path.join(logic, 'New.pak')
    pakv4.write(new, files('new'))
    check('a new pak is added at once while the game runs',
          os.path.exists(new) and not os.path.exists(new + '.part') and content(new) == 'new'
          and not pakguard.pending(), os.listdir(logic))

    # a replacement: held, then completes by itself
    t, st = later(lambda: paksource.write(old, files('three'), keep_omui=False))
    time.sleep(0.5)
    waiting = pakguard.pending()
    check('a replacement waits while the game runs, the old pak untouched',
          not st['done'] and content(old) == 'two' and os.path.realpath(old) in waiting
          and waiting[os.path.realpath(old)] == 'replace', (st, waiting))
    check('the new build waits beside it as .pak.part, which nothing lists as a pak',
          os.path.exists(old + '.part') and old + '.part' not in paksource.sources(mods),
          os.listdir(mods))
    os.remove(flag)
    t.join(5)
    check('once the game closes the replacement completes by itself',
          st['done'] and not st['error'] and content(old) == 'three' and
          not os.path.exists(old + '.part') and not pakguard.pending(), st)

    # a removal and a rename: held the same way
    open(flag, 'w').close()
    t, st = later(lambda: paksource.remove(new))
    t2, st2 = later(lambda: pakguard.rename(old, old + '.disabled'))
    time.sleep(0.5)
    check('a removal and a rename wait while the game runs',
          not st['done'] and not st2['done'] and os.path.exists(new) and os.path.exists(old)
          and set(pakguard.pending().values()) == {'remove', 'rename'}, pakguard.pending())
    os.remove(flag)
    t.join(5)
    t2.join(5)
    check('both complete once the game closes',
          st['done'] and st2['done'] and not os.path.exists(new) and not os.path.exists(old)
          and os.path.exists(old + '.disabled'), (st, st2))

    # outside the game's Paks folder: never held
    open(flag, 'w').close()
    mine = os.path.join(outside, 'Build_P.pak')
    pakv4.write(mine, files('a'))
    t, st = later(lambda: paksource.write(mine, files('b'), keep_omui=False))
    t.join(5)
    check('a pak outside the game\'s folder is replaced at once while it runs',
          st['done'] and content(mine) == 'b', st)

    # a CLI waits in its own process and says so once
    cli = os.path.join(mods, '900_Cli_P.pak')
    pakv4.write(cli, files('cli1'))
    t, st = later(lambda: pakguard.copy(mine, cli, note=said.append))
    time.sleep(0.4)
    os.remove(flag)
    t.join(5)
    check('a CLI install waits, says so once, and completes',
          st['done'] and content(cli) == 'b' and len(said) == 1 and
          said[0].startswith(pakguard.WAITING), said)

    # the Missions page's Install: handed to the background while held
    from riftlord.missions import bucket
    open(flag, 'w').close()
    ran = []
    real = (bucket.export, bucket.refuse_unbuilt, bucket._install_targets, bucket.refresh)
    bucket.refuse_unbuilt = lambda ids, verb: None
    bucket._install_targets = lambda ids: [cli]
    bucket.refresh = lambda: None

    def export(ids, dest=None, note=None):
        pakguard.settle(cli, 'replace', note=lambda _t: None)
        ran.append(list(ids))
        return {'dest': dest, 'paks': [i + '.pak' for i in ids], 'campaigns': []}
    bucket.export = export
    try:
        got = bucket.install(['900_Cli_P'])
        check('Install returns at once, waiting, while the game runs',
              got.get('waiting') and got.get('message') == pakguard.WAITING and not ran and
              '900_Cli_P' in bucket._WAITING_INSTALL, got)
        os.remove(flag)
        for _ in range(100):
            if ran and not bucket._WAITING_INSTALL:
                break
            time.sleep(0.05)
        check('and completes by itself once the game closes',
              ran == [['900_Cli_P']] and not bucket._WAITING_INSTALL, ran)
        got = bucket.install(['900_Cli_P'])
        check('with the game closed Install runs at once', not got.get('waiting') and
              len(ran) == 2, got)
        # the app's own installs (USER_ENV): never held, game running or not
        open(flag, 'w').close()
        os.environ[pakguard.USER_ENV] = '1'
        try:
            got = bucket.install(['900_Cli_P'])
            check('in the app Install runs at once while the game runs',
                  not got.get('waiting') and len(ran) == 3 and not pakguard.held(cli), got)
        finally:
            del os.environ[pakguard.USER_ENV]
            os.remove(flag)
    finally:
        bucket.export, bucket.refuse_unbuilt, bucket._install_targets, bucket.refresh = real
finally:
    if os.path.exists(flag):
        os.remove(flag)
    shutil.rmtree(root, ignore_errors=True)

bad = [c for c in checks if not c[1]]
for name, ok, detail in checks:
    print('%s %s%s' % ('ok  ' if ok else 'FAIL', name, '' if ok else ': %s' % (detail,)))
print('%d of %d checks passed' % (len(checks) - len(bad), len(checks)))
sys.exit(1 if bad else 0)
