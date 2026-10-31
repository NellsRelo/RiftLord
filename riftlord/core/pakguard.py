"""Changing the game's paks safely while it may be running.

A running Orcs Must Die 3 reopens each mounted pak BY NAME for every new reading thread
(UE 4.27 FPakFile::GetSharedReader, and the pak precacher) while keeping the index it read
at mount, so a pak replaced, removed or renamed under it is read at stale offsets and the
game crashes (2026-10-28: two paks swapped into a running game, a crash eight minutes
later reading 0xffffffffffffffff).

So, for a `.pak` inside the game's Paks folder (gamepath.paks(): ~mods, LogicMods and the
rest):
  - adding a NEW pak (nothing at that name yet) goes ahead at once;
  - replacing, removing or renaming an existing one while the game runs waits until the
    game has closed, then completes by itself.

Every pak is written beside its place as `<name>.pak.part` and renamed in (`write`,
`copy`), so nothing reading the folder -- the game, the Onslaught Editor's ~mods watcher,
another tool -- ever sees a half-written `.pak` (they list `*.pak`).

The wait is for automated processes only (user, 2026-10-29). Installs a person asks for in
the app never wait: the app sets USER_ENV, which its builds inherit, and nothing is held.

`pending()` names what is waiting now ("Waiting for the game to close").
The game is found by gamepath.game_running() (RIFTLORD_GAME_FLAG in tests).
"""

import os
import threading
import time

#: Seconds between looks at whether the game has closed.
POLL = 5.0
#: What the app shows for a change waiting on the game.
WAITING = 'Waiting for the game to close'
#: Set (by the app) when the changes are a person's own: nothing waits for the game.
USER_ENV = 'RIFTLORD_USER_INSTALLS'

_lock = threading.Lock()
#: {pak path: what waits on it ('replace', 'remove', 'rename')}
_pending = {}


def _game_paks():
    from riftlord.core import gamepath
    try:
        p = gamepath.paks()
    except Exception:                                # noqa: BLE001
        return None
    return os.path.realpath(p) if p else None


def in_game_paks(path):
    """Whether `path` is a `.pak` inside the game's Paks folder (any subfolder)."""
    if not str(path).lower().endswith('.pak'):
        return False
    root = _game_paks()
    if not root:
        return False
    here = os.path.realpath(path)
    return here == root or here.startswith(root + os.sep)


def game_running():
    from riftlord.core import gamepath
    return gamepath.game_running()


def held(path):
    """Whether changing the existing pak `path` must wait: not a person's own change
    (USER_ENV), it is in the game's Paks folder, it exists (the game may have it mounted)
    and the game is running."""
    if os.environ.get(USER_ENV):
        return False
    return in_game_paks(path) and os.path.exists(path) and game_running()


def pending():
    """{pak path: verb} of the changes waiting for the game to close now."""
    with _lock:
        return dict(_pending)


def settle(path, verb='replace', note=None):
    """Return once `path` may be changed: at once unless held(); else after the game has
    closed, saying once (through `note`, else print) what waits. True when it waited."""
    if not held(path):
        return False
    say = note or (lambda text: print(text, flush=True))
    say('%s to %s %s' % (WAITING, verb, os.path.basename(path)))
    with _lock:
        _pending[os.path.realpath(path)] = verb
    try:
        from riftlord.core import buildstage
        buildstage.stage(WAITING)
    except Exception:                                # noqa: BLE001
        pass
    try:
        while game_running():
            time.sleep(POLL)
    finally:
        with _lock:
            _pending.pop(os.path.realpath(path), None)
    return True


def replace(src, dest, note=None):
    """os.replace(src, dest), held until the game closes when dest is a pak it may have
    mounted."""
    settle(dest, 'replace', note)
    os.replace(src, dest)


def remove(path, note=None):
    """os.remove(path), held until the game closes when it is a pak it may have mounted."""
    settle(path, 'remove', note)
    os.remove(path)


def rename(src, dest, note=None):
    """os.replace(src, dest) for an existing pak moved or renamed: held until the game
    closes when either is a pak it may have mounted."""
    settle(src, 'rename', note)
    settle(dest, 'replace', note)
    os.replace(src, dest)


def write(dest, writer, note=None):
    """Write a pak to `dest` through `writer(path)`: written to `<dest>.part`, then put in
    place (replace()). Returns what `writer` returned."""
    part = dest + '.part'
    try:
        got = writer(part)
        replace(part, dest, note)
    except BaseException:
        if os.path.exists(part):
            os.remove(part)
        raise
    return got


def copy(src, dest, note=None):
    """Copy the pak `src` to `dest` the same way."""
    import shutil
    return write(dest, lambda part: shutil.copyfile(src, part), note)


def would_wait(paths):
    """The paths of `paths` whose change would wait now (held())."""
    return [p for p in paths if held(p)]
