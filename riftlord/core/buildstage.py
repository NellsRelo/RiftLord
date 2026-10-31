"""The stage a mission build is in, for the Missions page's progress (Building... Navmesh).

A build calls `stage('Navmesh')` as each step starts; whoever is showing progress listens:

    with buildstage.listen(lambda name: progress[ident] = name):
        bucket build ...
"""

import contextlib

_listeners = []


def stage(name):
    """Announce that the build under way has started `name`."""
    for fn in list(_listeners):
        try:
            fn(name)
        except Exception:                            # noqa: BLE001
            pass


@contextlib.contextmanager
def listen(fn):
    """Call `fn(name)` for every stage announced inside the block."""
    _listeners.append(fn)
    try:
        yield
    finally:
        _listeners.remove(fn)
