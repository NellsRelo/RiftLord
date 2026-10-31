"""Launch Rift Lord: the Map Editor, the Onslaught Editor and the bucket.

    python3 -m workshop [--browser] [--no-window]

Everything either tool builds while Rift Lord runs lands in the bucket rather
than in the game's ~mods; the bucket's page installs it from there.
"""

import argparse
import os
import sys

from . import __version__
from riftlord.app import server

# Rift Lord keeps its settings and caches under its own name, not the Onslaught
# Editor's. Set before either editor is imported: several of their modules work out
# their cache folders as they load.
server.gamepath.APP_NAME = 'RiftLord'


def window(url, title='Rift Lord'):
    """Show `url` in a native window. True if one opened."""
    try:
        import webview                                    # pywebview
    except ImportError:
        return False
    try:
        webview.create_window(title, url, width=1500, height=920, min_size=(1100, 680))
        webview.start()
        return True
    except Exception as exc:                               # noqa: BLE001
        print('no system webview (%s); falling back to the browser' % exc,
              file=sys.stderr)
        return False


#: Seconds between looks at the window's pipe on Windows (_peek_until_closed).
PIPE_POLL = 0.5
#: Windows' ERROR_BROKEN_PIPE: the other end of a pipe was closed.
ERROR_BROKEN_PIPE = 109


def _peek_until_closed():
    """Wait until the window closes our stdin pipe, by polling it with PeekNamedPipe.
    False at once when stdin is not a pipe that can be peeked (the caller then reads it).

    On Windows a thread blocked in a read of the stdin pipe stops other threads from
    loading DLLs: the first numpy import in a request handler hung for good (measured
    under Wine, 2026-10-16, with the pipe the window gives us). Peeking never blocks."""
    import ctypes
    import msvcrt
    import time
    from ctypes import wintypes
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.PeekNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                  ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
                                  ctypes.c_void_p]
    fd = sys.stdin.fileno()
    handle = msvcrt.get_osfhandle(fd)
    avail = wintypes.DWORD()
    first = True
    while k32.PeekNamedPipe(handle, None, 0, None, ctypes.byref(avail), None):
        first = False
        if avail.value and not os.read(fd, avail.value):
            return True
        time.sleep(PIPE_POLL)
    return not first or ctypes.get_last_error() == ERROR_BROKEN_PIPE


def main(argv=None):
    ap = argparse.ArgumentParser(prog='workshop', description=__doc__.split('\n')[0])
    ap.add_argument('--version', action='version',
                    version='Rift Lord %s' % __version__)
    ap.add_argument('--browser', action='store_true',
                    help='use the default browser even if a webview exists')
    ap.add_argument('--no-window', action='store_true',
                    help='serve only, and print the address')
    ap.add_argument('--sidecar', action='store_true',
                    help="run under Rift Lord's application window: print the address "
                         'once ready, and stop when the window closes our stdin')
    args = ap.parse_args(argv)

    from riftlord.core import console
    console.capture()
    # installs asked for here are the user's own: they never wait for the game to close
    from riftlord.core import pakguard
    os.environ[pakguard.USER_ENV] = '1'
    from riftlord.missions import bucket
    bucket.activate()
    from riftlord.app import fabkits
    from riftlord.mapeditor import server as riftlord_server
    from riftlord.onslaught import server as onslaught_server
    # one listener: the editors under their prefixes (server.PREFIXES), the shell the rest
    shell = server.listen(server.routed({'riftlord': riftlord_server.Handler,
                                         'onslaught': onslaught_server.Handler}),
                          server.DEFAULT_PORT)
    url = 'http://127.0.0.1:%d/' % shell.port
    for name, prefix in server.PREFIXES.items():
        server.TOOLS[name] = url + prefix.lstrip('/') + '/'
    if server.web_root() is None:
        print('warning: the UI is not built -- run npm install && npm run build '
              'in workshop/web', file=sys.stderr)
    print('Rift Lord %s on %s' % (__version__, url))
    print('missions: %s' % bucket.root())
    # the supported mods, detected once at start (riftlord/core/supportedmods.py)
    from riftlord.core import supportedmods
    for row in supportedmods.detect():
        console.info('%s: %s%s' % (row['name'], row['label'],
                                   ' (%s)' % row['files'][0]['path'] if row['files'] else ''),
                     console.SERVER)
    # the Map Editor's New Object previews, drawn into the cache while the app opens
    riftlord_server.warm_thumbnails()
    # Setup: bpkit's type data, built when it is missing or an input changed
    from riftlord.types import setup
    setup.start()
    # the Fab converter's working folders of packs not converted for a day
    fabkits.trim_cache()

    if args.sidecar:
        # The window reads this line to know where to point itself, and holds our stdin
        # open for as long as it lives: when it closes -- however it closes, a crash
        # included -- stdin reaches end of file and the servers go down with it.
        print('RIFTLORD_READY %s' % url, flush=True)
        import threading

        def watch():
            try:
                if not (os.name == 'nt' and _peek_until_closed()):
                    while sys.stdin.read(4096):
                        pass
            except (OSError, ValueError):
                pass
            server.STOPPING.set()
        threading.Thread(target=watch, daemon=True).start()
        server.STOPPING.wait()
        fabkits.stop_all()
        print('stopped', flush=True)
        sys.stderr.flush()
        # no interpreter teardown: freeing a large, partly swapped-out heap outlasted the
        # window's stop wait; the servers are down and nothing registers atexit work
        os._exit(0)

    if not args.no_window and not args.browser and window(url):
        fabkits.stop_all()
        return 0
    if not args.no_window:
        import webbrowser
        webbrowser.open(url)
        print('Use Quit in Rift Lord, or close this window, to stop it.')
    try:
        server.STOPPING.wait()
    except KeyboardInterrupt:
        pass
    fabkits.stop_all()
    print('stopped')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
