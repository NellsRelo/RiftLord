"""Launch the Onslaught Editor.

Starts the local API, then shows the UI in a desktop window. The window is the
system webview where one is available -- WebView2 on Windows, WebKitGTK on Linux
-- and otherwise the author's own browser, which cannot fail to be there.

    python3 -m onslaughtgui [--browser] [--port N] [--no-window]
"""

import argparse
import sys

from . import __version__
from riftlord.onslaught import server
from riftlord.onslaught.server import serve, web_root


def window(url, title='Onslaught Editor'):
    """Show `url` in a native window. True if one opened."""
    try:
        import webview                                    # pywebview
    except ImportError:
        return False
    try:
        webview.create_window(title, url, width=1380, height=880,
                              min_size=(980, 620))
        webview.start()
        return True
    except Exception as exc:                               # noqa: BLE001
        print('no system webview (%s); falling back to the browser' % exc,
              file=sys.stderr)
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(prog='onslaughtgui',
                                 description=__doc__.split('\n')[0])
    ap.add_argument('--version', action='version',
                    version='Onslaught Editor %s' % __version__)
    ap.add_argument('--port', type=int, default=None,
                    help='listen here instead of %d; 0 asks the OS for any free port'
                         % server.DEFAULT_PORT)
    ap.add_argument('--browser', action='store_true',
                    help='use the default browser even if a webview exists')
    ap.add_argument('--no-window', action='store_true',
                    help='serve only, and print the address')
    args = ap.parse_args(argv)

    httpd = serve(args.port)
    url = 'http://127.0.0.1:%d/' % httpd.port
    if web_root() is None:
        print('warning: the UI is not built -- run npm install && npm run build '
              'in onslaughtgui/web', file=sys.stderr)
    print('Onslaught Editor %s on %s' % (__version__, url))
    if httpd.port != httpd.wanted_port:
        print('warning: port %d was busy -- another copy may still be running'
              % httpd.wanted_port, file=sys.stderr)

    if args.no_window:
        try:
            # Waits on the server's own flag, so "Shut down" from the page ends the
            # process rather than only closing the socket.
            server.STOPPING.wait()
        except KeyboardInterrupt:
            pass
        print('stopped')
        return 0

    if not args.browser and window(url):
        return 0

    import webbrowser
    webbrowser.open(url)
    print('Close this window, or use Shut down in the editor, to stop it.')
    try:
        server.STOPPING.wait()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
