"""Run Rift Lord's Map Editor on its own: start the server and open a browser at it."""

import argparse
import webbrowser

from riftlord.mapeditor.server import serve

#: Its own port, so it can run alongside the assembly tool on 8777 and the
#: Onslaught Editor on 8900.
DEFAULT_PORT = 8850


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--port', type=int, default=DEFAULT_PORT)
    ap.add_argument('--no-browser', action='store_true')
    args = ap.parse_args(argv)
    try:
        httpd, port = serve(args.port)
    except OSError as exc:
        print('could not bind port %d (%s).' % (args.port, exc))
        print('another copy may already be running; try --port 0 for any free port')
        return 1
    url = 'http://127.0.0.1:%d/' % port
    print('Map Editor on %s  (ctrl-c to stop)' % url)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        while True:
            httpd._BaseServer__is_shut_down.wait(1.0)
    except KeyboardInterrupt:
        print('\nstopping')
        httpd.shutdown()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
