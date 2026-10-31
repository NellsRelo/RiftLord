"""The heavy-job slots' queue (riftlord.core.heavyslot): waiters are served in arrival order,
a job that frees the shared slot and asks again at once goes to the back, a dead waiter's
ticket is dropped, and an install gate takes the reserved slot past the queue. The light
lane: a light job runs while both shared heavy slots are held, its waiters are served in
arrival order, a nested light request runs in its holder's slot, and a heavy step inside a
light job waits for a heavy slot (and the wait is heard, with its holder).

    python3 tests/offline/test_heavyslot.py
"""
import multiprocessing
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from riftlord.core import heavyslot  # noqa: E402

TMP = tempfile.mkdtemp(prefix='heavyslot-test-')
LOG = os.path.join(TMP, 'order.log')
#: Seconds each job holds the slot.
HOLD = 0.6
checks = []


def check(name, ok, detail=''):
    checks.append(ok)
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, (': %s' % (detail,)) if detail else ''))


def note(text):
    with open(LOG, 'a') as fh:
        fh.write(text + '\n')


def setup(slots=2, light_slots=1):
    """In a child process: the test's lock folder, a fast poll, and the slot counts (by
    default one shared heavy slot beside the reserved one and one light slot, so queue
    order is observable)."""
    heavyslot.FOLDER = os.path.join(TMP, 'heavy')
    heavyslot.POLL = 0.05
    heavyslot.SLOTS = slots
    heavyslot.LIGHT_SLOTS = light_slots
    os.environ.pop(heavyslot.ENV, None)
    os.environ.pop(heavyslot.LIGHT_ENV, None)


def job(name, hold=HOLD, reserved=False, light=False, slots=2):
    """In a child process: take a slot, note it, hold it."""
    setup(slots)
    with heavyslot.slot(name, reserved=reserved, light=light):
        note(name)
        time.sleep(hold)


def light_job_with_heavy_step(name):
    """In a child process: a light job whose nested light request runs at once and whose
    heavy step waits for a heavy slot; the waits it hears are noted."""
    setup()
    heard = []
    heavyslot.WAIT_LISTENERS.append(lambda e, lane, n, held, s: heard.append((e, lane, held)))
    with heavyslot.slot(name, light=True):
        note('%s light' % name)
        with heavyslot.slot(name + ' nested', light=True):
            note('%s nested light' % name)
        with heavyslot.slot(name + ' step'):
            note('%s heavy step' % name)
    note('%s heard %s' % (name, '|'.join('%s %s %s' % h for h in heard)))


def retaker(rounds, light=False):
    """A job that asks again as soon as it is done, each time as a new process."""
    for i in range(rounds):
        p = multiprocessing.Process(target=job, args=('R%d' % i,),
                                    kwargs={'light': light})
        p.start()
        p.join()


def order():
    with open(LOG) as fh:
        return [line.strip() for line in fh if line.strip()]


def main():
    ctx = multiprocessing.get_context('fork')
    multiprocessing.Process = ctx.Process
    # 1. arrival order: R holds the shared slot and asks again at once; A and B arrived
    #    meanwhile and are served before R's second turn
    r = ctx.Process(target=retaker, args=(2,))
    r.start()
    time.sleep(0.2)
    a = ctx.Process(target=job, args=('A',))
    a.start()
    time.sleep(0.1)
    b = ctx.Process(target=job, args=('B',))
    b.start()
    for p in (r, a, b):
        p.join(30)
    got = order()
    check('waiters served in arrival order, the re-taker after them',
          got == ['R0', 'A', 'B', 'R1'], got)
    # 2. a dead waiter's ticket does not block the queue
    queue = os.path.join(TMP, 'heavy', heavyslot.QUEUE_NAME)
    os.makedirs(queue, exist_ok=True)
    dead = ctx.Process(target=lambda: None)
    dead.start()
    dead.join()
    with open(os.path.join(queue, '%020d-%d-1' % (1, dead.pid)), 'w') as fh:
        fh.write('gone\n')
    os.remove(LOG)
    c = ctx.Process(target=job, args=('C', 0.1))
    c.start()
    c.join(10)
    check('a dead waiter\'s ticket is dropped', order() == ['C'] and not os.listdir(queue),
          (order(), os.listdir(queue)))
    # 3. an install gate takes the reserved slot while the shared one is held and others wait
    os.remove(LOG)
    h = ctx.Process(target=job, args=('H', 1.5))
    h.start()
    time.sleep(0.2)
    w = ctx.Process(target=job, args=('W', 0.1))
    w.start()
    time.sleep(0.1)
    g = ctx.Process(target=job, args=('G', 0.1, True))
    g.start()
    for p in (h, w, g):
        p.join(30)
    got = order()
    check('the install gate runs at once, past the queue', got[:2] == ['H', 'G'] and got[2] == 'W',
          got)
    # 4. a light job runs while both shared heavy slots are held (and a heavy job waits)
    os.remove(LOG)
    held = [ctx.Process(target=job, args=('H%d' % i, 1.5), kwargs={'slots': 3})
            for i in range(2)]
    for p in held:
        p.start()
    time.sleep(0.3)
    hw = ctx.Process(target=job, args=('HW', 0.1), kwargs={'slots': 3})
    hw.start()
    time.sleep(0.1)
    lt = ctx.Process(target=job, args=('L', 0.1), kwargs={'light': True, 'slots': 3})
    lt.start()
    for p in held + [hw, lt]:
        p.join(30)
    got = order()
    check('a light job runs at once while both shared heavy slots are held',
          sorted(got[:2]) == ['H0', 'H1'] and got[2] == 'L' and got[3] == 'HW', got)
    # 5. the light lane serves its waiters in arrival order, the re-taker after them
    os.remove(LOG)
    r = ctx.Process(target=retaker, args=(2, True))
    r.start()
    time.sleep(0.2)
    a = ctx.Process(target=job, args=('A',), kwargs={'light': True})
    a.start()
    time.sleep(0.1)
    b = ctx.Process(target=job, args=('B',), kwargs={'light': True})
    b.start()
    for p in (r, a, b):
        p.join(30)
    got = order()
    check('light waiters served in arrival order, the re-taker after them',
          got == ['R0', 'A', 'B', 'R1'], got)
    # 6. inside a light job a nested light request runs at once, and a heavy step waits
    #    for a heavy slot, hearing who holds it
    os.remove(LOG)
    h = ctx.Process(target=job, args=('H', 1.0))
    h.start()
    time.sleep(0.2)
    lj = ctx.Process(target=light_job_with_heavy_step, args=('J',))
    lj.start()
    h.join(30)
    note('H done')
    lj.join(30)
    got = order()
    heard = next((g for g in got if g.startswith('J heard ')), '')
    check('a light job\'s nested light request runs inside its slot',
          got[:3] == ['H', 'J light', 'J nested light'], got)
    check('a heavy step inside a light job waits for the heavy slot',
          got.index('J heavy step') > got.index('H done'), got)
    check('the wait is heard with its lane and holder',
          'wait heavy H pid' in heard and 'got heavy H pid' in heard, heard)
    print('%d checks, %d failed' % (len(checks), checks.count(False)))
    return 0 if all(checks) else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
