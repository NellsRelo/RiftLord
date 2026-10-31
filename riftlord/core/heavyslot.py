"""A machine-wide limit on Rift Lord's own heavy jobs: SLOTS at once, the rest wait; and a
separate light lane for short single-core jobs: LIGHT_SLOTS at once.

    from riftlord.core import heavyslot
    with heavyslot.slot('gate'):
        ...                                   # at most SLOTS such blocks run on the machine
    with heavyslot.slot('probe X', light=True):
        ...                                   # a light slot (FOLDER/light-<n>.lock)

Each slot is a lock file (FOLDER/slot-<n>.lock) held with flock while the block runs, so a
job that dies frees its slot at once. Slot 0 is reserved for install gates (`gate.py
--install`, `release_check.py --install`): they never queue behind a partial run. The
others wait in arrival order: each writes a ticket (QUEUE/<time>-<pid>-<thread>) and may take
the shared slot only while its ticket is the oldest of a live process, so a job that frees
the slot and asks again at once goes to the back. Taken by tools/gate.py, tools/release_check.py, the
light bakes (riftlord.build.lighting.lightbake, besides its GPU lock) and minimapscore
rounds. A job holding a slot sets ENV for its children, which then run inside it instead
of waiting for a second slot (a gate's release check, comparisons and bakes). Off Linux and
macOS (no flock) it does nothing. docs/HEAVY-JOBS.md.
"""

import contextlib
import os
import sys
import threading
import time

#: Slots on the machine, and where their lock files are.
SLOTS = int(os.environ.get('RIFTLORD_HEAVY_SLOTS') or 3)
FOLDER = os.path.join(os.path.expanduser('~'), '.cache', 'rift-lord', 'heavy')
#: Where waiters' tickets are (under FOLDER).
QUEUE_NAME = 'queue'
#: Slots below this are only for install gates (slot(..., reserved=True)).
RESERVED = 1
#: Set while a slot is held: children of the holder run inside it.
ENV = 'RIFTLORD_HEAVY_HELD'
#: Light slots (slot(..., light=True)): a separate pool for short single-core jobs.
LIGHT_SLOTS = int(os.environ.get('RIFTLORD_LIGHT_SLOTS') or 2)
#: Where light waiters' tickets are (under FOLDER).
LIGHT_QUEUE_NAME = 'light-queue'
#: Set while a light slot is held: children of the holder run inside it.
LIGHT_ENV = 'RIFTLORD_LIGHT_HELD'
#: Seconds between tries while every slot is taken.
POLL = 5


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _first(queue, ticket):
    """True when `ticket` is the oldest ticket of a live process; dead ones are dropped."""
    for name in sorted(os.listdir(queue)):
        if name == ticket:
            return True
        try:
            pid = int(name.split('-')[1])
        except (IndexError, ValueError):
            pid = 0
        if pid and _alive(pid):
            return False
        try:
            os.remove(os.path.join(queue, name))      # its process is gone
        except OSError:
            pass
    return False


def _take(fcntl, slots, name, prefix='slot'):
    """The open, locked lock file of the first free slot in `slots`, or None."""
    for n in slots:
        fh = open(os.path.join(FOLDER, '%s-%d.lock' % (prefix, n)), 'a+')
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            continue
        fh.seek(0)
        fh.truncate()
        fh.write('%s pid %d since %s\n' % (name, os.getpid(), time.strftime('%H:%M:%S')))
        fh.flush()
        return fh
    return None


def shared_holders():
    """What holds the slots a non-reserved job may take, as their lock files say."""
    return '; '.join(holders().split('; ')[RESERVED:])


#: Called on every first wait and every take after one, in any lane:
#: listener(event, lane, name, held, seconds) with event 'wait' (seconds 0) or 'got'.
WAIT_LISTENERS = []


def _tell(event, lane, name, held, seconds=0.0):
    for listener in list(WAIT_LISTENERS):
        try:
            listener(event, lane, name, held, seconds)
        except Exception:                            # noqa: BLE001
            pass


@contextlib.contextmanager
def slot(name, reserved=False, on_wait=None, light=False):
    """Hold a slot for the block, waiting while they are taken; without flock, just run
    the block.

    Heavy (the default): one of SLOTS. Slot 0 is RESERVED for install gates
    (`reserved=True`), which may take any slot at once; every other job takes only the
    rest, in arrival order (its ticket in QUEUE must be the oldest live one). Inside a heavy
    holder's process tree the block runs in that slot; inside a light holder it still waits
    for a heavy slot.

    Light (`light=True`), for short single-core jobs: one of LIGHT_SLOTS, a separate pool
    with its own queue (LIGHT_QUEUE_NAME), so it never waits behind heavy jobs. Inside a
    light or a heavy holder's process tree the block runs in that slot. A light job's step
    that is multi-core or uses the GPU takes a heavy slot for itself.

    `on_wait(holders of the slots it may take)` is called once, when the job first has to
    wait, in place of the line printed to stderr; WAIT_LISTENERS hear every lane."""
    try:
        import fcntl
    except ImportError:
        fcntl = None
    if light:
        held_env, queue_name, prefix = LIGHT_ENV, LIGHT_QUEUE_NAME, 'light'
        slots, lane = range(LIGHT_SLOTS), 'light'
        inside = os.environ.get(LIGHT_ENV) or os.environ.get(ENV)
        who = light_holders
    else:
        held_env, queue_name, prefix = ENV, QUEUE_NAME, 'slot'
        slots, lane = range(0 if reserved else RESERVED, SLOTS), 'heavy'
        inside = os.environ.get(ENV)
        who = holders if reserved else shared_holders
    if fcntl is None or inside:
        yield
        return
    queue = os.path.join(FOLDER, queue_name)
    os.makedirs(queue, exist_ok=True)
    ticket = None
    if not reserved:
        ticket = '%020d-%d-%d' % (time.time_ns(), os.getpid(), threading.get_ident())
        with open(os.path.join(queue, ticket), 'w') as fh:
            fh.write(name + '\n')
    said, started, held = False, time.time(), ''
    try:
        while True:
            fh = None
            if ticket is None or _first(queue, ticket):
                fh = _take(fcntl, slots, name, prefix)
            if fh is not None:
                if ticket is not None:
                    os.remove(os.path.join(queue, ticket))
                    ticket = None
                if said:
                    _tell('got', lane, name, held, time.time() - started)
                os.environ[held_env] = name
                try:
                    yield
                finally:
                    os.environ.pop(held_env, None)
                    fh.seek(0)
                    fh.truncate()
                    fh.close()                # closing drops the lock
                return
            if not said:
                held = who()
                if on_wait is not None:
                    on_wait(held)
                else:
                    print('%s: waiting for a %s slot (held by %s)' % (name, lane, held),
                          file=sys.stderr, flush=True)
                _tell('wait', lane, name, held)
                said = True
            time.sleep(POLL)
    finally:
        if ticket is not None:
            try:
                os.remove(os.path.join(queue, ticket))
            except OSError:
                pass


def _lock_texts(prefix, count):
    out = []
    for n in range(count):
        try:
            with open(os.path.join(FOLDER, '%s-%d.lock' % (prefix, n))) as fh:
                out.append(fh.read().strip() or 'free')
        except OSError:
            out.append('free')
    return out


def holders():
    """What holds each heavy slot now, as its lock files say."""
    return '; '.join(_lock_texts('slot', SLOTS))


def light_holders():
    """What holds each light slot now, as its lock files say."""
    return '; '.join(_lock_texts('light', LIGHT_SLOTS))
