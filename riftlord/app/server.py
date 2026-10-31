"""Rift Lord's own server: the shell page, and the bucket's API.

One listener serves all three: the Map Editor under /mapeditor/ and the Onslaught Editor
under /onslaught/ (PREFIXES; their own handlers, with the prefix taken off), the shell
the rest. The shell shows the editors' pages in frames, now on its own origin; their
pages use relative URLs. The bucket is shared through the `bucket` module.
"""

import json
import mimetypes
import os
import posixpath
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.missions import bucket  # noqa: E402
from riftlord.core import gamepath  # noqa: E402

#: Sent by the shell page on every write; another site cannot set it without a
#: CORS preflight, which this server never grants.
GUARD_HEADER = 'X-Workshop'

#: The one listener's port (or the next three, or any free one).
DEFAULT_PORT = 8870

#: Set by Quit in the page, so the launcher's wait ends.
STOPPING = threading.Event()
STARTED = time.time()
#: {tool: url}, filled in by the launcher once the tools listen.
TOOLS = {}
#: native_status(), worked out once.
_NATIVE = {}


def native_status():
    """Each native tool a build may need: None when it can run here, else the message a
    build that needs it refuses with (pxcook: collision cooking; recastgen: walkable-graph
    regeneration; fab: the Fab converter, fabkits.unavailable), and `tools`: pxcook's
    omd3physx.tool_status() (ready, buildable or missing). Asked afresh each time: pxcook
    goes from buildable to ready on first use."""
    from riftlord.formats.omd3 import omd3physx
    from riftlord.app import fabkits
    if 'recastgen' not in _NATIVE:
        from riftlord.build.navigation import recastgen
        _NATIVE['recastgen'] = recastgen.available()
    status = omd3physx.tool_status()
    return dict(_NATIVE, pxcook=omd3physx.unavailable_message(status), fab=fabkits.unavailable(),
                tools={'pxcook': status})


def web_root():
    """The built shell page, or None."""
    d = gamepath.web_dist('shell')
    return d if os.path.isfile(os.path.join(d, 'index.html')) else None


def reveal(path):
    """Show a folder in the system's file manager."""
    if os.name == 'nt':
        os.startfile(path)                           # noqa: S606
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', path])
    else:
        subprocess.Popen(['xdg-open', path])


class Handler(BaseHTTPRequestHandler):
    server_version = 'RiftLord'

    def log_message(self, *_args):
        pass

    def _local_origin(self):
        """False when a request was made on another site's behalf."""
        origin = self.headers.get('Origin')
        if origin and origin.split('//', 1)[-1] not in (
                '127.0.0.1:%d' % self.server.port, 'localhost:%d' % self.server.port):
            return False
        host = (self.headers.get('Host') or '').split(':')[0]
        return not host or host in ('127.0.0.1', 'localhost')

    def _send(self, code, body, ctype='application/json', extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode('utf-8')
        elif isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _fail(self, code, message):
        if code != 404:
            from riftlord.core import console
            console.error(message, console.MISSIONS)
        self._send(code, {'error': message})

    @staticmethod
    def _blame(exc):
        # the converter refuses by `raise SystemExit('...')`, already worded for a person
        if isinstance(exc, SystemExit):
            return str(exc) or 'refused'
        return '%s: %s' % (type(exc).__name__, exc)

    def do_HEAD(self):                               # noqa: N802
        self.do_GET()

    def do_GET(self):                                # noqa: N802
        path, _, query = self.path.partition('?')
        args = dict(urllib.parse.parse_qsl(query))
        try:
            if not path.startswith('/api/'):
                return self._static(path)
            if not self._local_origin():
                return self._fail(403, 'cross-origin requests are refused')
            parts = [p for p in path[5:].split('/') if p]
            if parts == ['tools']:
                from . import __version__
                return self._send(200, {'tools': TOOLS, 'version': __version__,
                                        'started': STARTED,
                                        'game': gamepath.find() is not None,
                                        'native': native_status()})
            if parts == ['bucket']:
                bucket.refresh()
                return self._send(200, bucket.items())
            if parts == ['settings']:
                from riftlord.missions import customcampaign
                return self._send(200, {'defaults': bucket.defaults(), 'root': bucket.root(),
                                        'game': gamepath.find(),
                                        'choices': customcampaign.choices(mods=bucket.root())})
            #: Settings > Supported Mods: detected afresh on every ask (riftlord/core/supportedmods.py)
            if parts == ['supportedmods']:
                from riftlord.core import supportedmods
                return self._send(200, supportedmods.state())
            if parts == ['console']:
                from riftlord.core import console
                return self._send(200, console.since(int(args.get('since') or 0)))
            #: the Setup pane (riftlord/types/setup.py): started here when an input changed
            if parts == ['setup']:
                from riftlord.types import setup
                got = setup.status()
                return self._send(200, setup.start() if got['state'] == 'needed' else got)
            if parts == ['bucket', 'settings']:
                return self._send(200, bucket.settings(args.get('id') or ''))
            if parts == ['bucket', 'campaign']:
                return self._send(200, bucket.campaign_settings(args.get('key') or ''))
            #: the campaign button preview's frame (customcampaign.card_preview)
            if parts == ['campaign', 'frame']:
                from riftlord.missions import customcampaign
                return self._send(200, customcampaign.card_preview() or {})
            #: the Details dialog's Guid field (webshared/MissionDetails.svelte guidTools)
            if parts == ['guid', 'new']:
                return self._send(200, {'guid': bucket.new_guid()})
            if parts == ['guid', 'check']:
                return self._send(200, bucket.guid_check(args.get('guid') or '',
                                                         args.get('mission')))
            if parts == ['bucket', 'build']:
                return self._send(200, bucket.build_status())
            #: the Fab Kits tab (fabkits.py): its jobs, and the vault's packs (or the
            #: running scan of the vault, `scanning`)
            if parts == ['fab', 'state']:
                from riftlord.app import fabkits
                return self._send(200, fabkits.state())
            if parts == ['fab', 'vault']:
                from riftlord.app import fabkits
                return self._send(200, fabkits.vault_status(max_age=0 if args.get('fresh')
                                                            else fabkits.VAULT_FRESH))
            #: Settings > Fab Kits: the vault folder and the convert limits
            if parts == ['fab', 'settings']:
                from riftlord.app import fabkits
                return self._send(200, fabkits.settings_view(start=True))
            if parts == ['bucket', 'package.zip']:
                # a plain browser's Package; the app window saves through /package
                ids = [i for i in (args.get('ids') or '').split(',') if i]
                name, blob = bucket.package(ids)
                return self._send(200, blob, 'application/zip', {
                    'Content-Disposition': 'attachment; filename="%s"' % name})
            return self._fail(404, 'no such endpoint: %s' % path)
        except BrokenPipeError:
            return None
        except (Exception, SystemExit) as exc:       # noqa: BLE001
            return self._fail(400, self._blame(exc))

    def do_POST(self):                               # noqa: N802
        if not self._local_origin() or not self.headers.get(GUARD_HEADER):
            return self._fail(403, 'this endpoint only answers the Rift Lord app')
        length = int(self.headers.get('Content-Length') or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b'{}') if length else {}
        except ValueError:
            return self._fail(400, 'body is not JSON')
        path = self.path.partition('?')[0]
        try:
            ids = [str(i) for i in payload.get('ids') or []]
            if path == '/api/bucket/remove':
                return self._send(200, bucket.remove(ids))
            if path == '/api/bucket/rename':
                return self._send(200, bucket.rename(str(payload.get('id') or ''),
                                                     payload.get('title')))
            if path == '/api/bucket/install':
                return self._send(200, bucket.install(ids))
            if path == '/api/bucket/build':
                return self._send(200, bucket.start_build(ids))
            if path == '/api/bucket/package':
                target = str(payload.get('path') or '')
                if not target:
                    return self._fail(400, 'no file to package into')
                if not target.lower().endswith('.zip'):
                    target += '.zip'
                return self._send(200, bucket.package(ids, target))
            if path == '/api/bucket/root':
                folder = str(payload.get('path') or '')
                if not folder:
                    return self._fail(400, 'no folder given')
                return self._send(200, bucket.set_root(folder))
            if path == '/api/settings':
                return self._send(200, {'defaults': bucket.set_defaults(payload.get('defaults'))})
            if path == '/api/bucket/settings':
                return self._send(200, bucket.update_settings(
                    str(payload.get('id') or ''), details=payload.get('details'),
                    placement=payload.get('placement'),
                    new_campaign=payload.get('newCampaign'), endless=payload.get('endless'),
                    reset_doors=payload.get('resetDoors'), preview=payload.get('preview'),
                    guid=payload.get('guid'), scramble=payload.get('scramble'),
                    campaign_index=payload.get('campaignIndex'),
                    working_title=payload.get('workingTitle'),
                    endless_only=payload.get('endlessOnly'),
                    unlocks=payload.get('unlocks')))
            #: a mission's place in an installed custom campaign (bucket.set_campaign_index)
            if path == '/api/bucket/campaignindex':
                return self._send(200, bucket.set_campaign_index(
                    str(payload.get('key') or ''), str(payload.get('id') or ''),
                    payload.get('index')))
            #: a custom campaign's name, description, hub style and banner
            if path == '/api/bucket/campaign':
                return self._send(200, bucket.update_campaign(str(payload.get('key') or ''),
                                                              payload.get('values')))
            #: the enemy preview with unsaved options, for the Settings panel (makepreview)
            if path == '/api/bucket/preview':
                return self._send(200, bucket.preview(str(payload.get('id') or ''),
                                                      payload.get('options')) or {})
            if path == '/api/fab/settings':
                from riftlord.app import fabkits
                return self._send(200, fabkits.set_settings(payload.get('values')))
            if path in ('/api/fab/convert', '/api/fab/measure', '/api/fab/cancel',
                        '/api/fab/delete', '/api/fab/move'):
                from riftlord.app import fabkits
                item = str(payload.get('item') or '')
                if path == '/api/fab/convert':
                    return self._send(200, fabkits.convert(item, payload.get('scale')))
                if path == '/api/fab/move':
                    return self._send(200, fabkits.move(item, payload.get('index') or 0))
                if path == '/api/fab/measure':
                    return self._send(200, fabkits.measure(item))
                if path == '/api/fab/cancel':
                    return self._send(200, fabkits.cancel(item))
                return self._send(200, fabkits.delete(item))
            #: a supported mod's page, opened in the system browser (its registry link only)
            if path == '/api/supportedmods/open':
                from riftlord.core import supportedmods
                return self._send(200, supportedmods.open_link(str(payload.get('id') or '')))
            if path == '/api/setup/start':
                from riftlord.types import setup
                return self._send(200, setup.start(force=bool(payload.get('force'))))
            if path == '/api/console/clear':
                from riftlord.core import console
                console.clear()
                return self._send(200, {'ok': True})
            if path == '/api/bucket/reveal':
                reveal(bucket.root())
                return self._send(200, {'ok': True})
            if path == '/api/quit':
                self._send(200, {'stopping': True})
                threading.Thread(target=self._stop, daemon=True).start()
                return None
            return self._fail(404, 'no such endpoint: %s' % path)
        except BrokenPipeError:
            return None
        except (Exception, SystemExit) as exc:       # noqa: BLE001
            return self._fail(400, self._blame(exc))

    def _stop(self):
        from riftlord.app import fabkits
        from riftlord.types import setup
        fabkits.stop_all()
        setup.stop()
        STOPPING.set()
        self.server.shutdown()

    def _static(self, path):
        root = web_root()
        if root is None:
            return self._send(200, UNBUILT, 'text/html; charset=utf-8')
        rel = posixpath.normpath(urllib.parse.unquote(path)).lstrip('/')
        target = os.path.join(root, rel) if rel and rel != '.' else \
            os.path.join(root, 'index.html')
        if not os.path.abspath(target).startswith(os.path.abspath(root)):
            return self._fail(403, 'outside the web root')
        if not os.path.isfile(target):
            target = os.path.join(root, 'index.html')
        ctype = mimetypes.guess_type(target)[0] or 'application/octet-stream'
        with open(target, 'rb') as fh:
            return self._send(200, fh.read(), ctype)


UNBUILT = """<!doctype html><meta charset=utf-8><title>Rift Lord</title>
<body style="font:15px/1.6 system-ui;margin:3rem auto;max-width:34rem">
<h1>The UI is not built yet</h1>
<p>From <code>workshop/web</code> run <code>npm install</code> then
<code>npm run build</code>.</p>"""


#: Each editor's path prefix on the one listener, by tool name (TOOLS' keys).
PREFIXES = {'riftlord': '/mapeditor', 'onslaught': '/onslaught'}


class _Route:
    """Picks the handler class for each request on the one listener, by path prefix:
    `/mapeditor/...` and `/onslaught/...` go to those editors' handlers with the prefix
    taken off, the bare prefix is redirected to its folder, the rest is the shell's. The
    class is chosen before the request line is parsed (it decides the protocol
    version), and again for every request on a kept-alive connection."""

    def parse_request(self):
        try:
            target = self.raw_requestline.split()[1].decode('latin-1')
        except (IndexError, UnicodeDecodeError):
            target = '/'
        path = target.partition('?')[0]
        self.__class__ = ROUTED['shell']
        for tool, prefix in PREFIXES.items():
            if path == prefix:
                self.__class__ = ROUTED['redirect']
            elif path.startswith(prefix + '/'):
                self.__class__ = ROUTED[tool]
        if not BaseHTTPRequestHandler.parse_request(self):
            return False
        for tool, prefix in PREFIXES.items():
            if self.__class__ is ROUTED[tool]:
                self.path = self.path[len(prefix):]
        return True


class _Redirect(BaseHTTPRequestHandler):
    """A bare editor prefix: on to its folder, so the page's relative URLs resolve."""

    def log_message(self, *_args):
        pass

    def do_GET(self):                                # noqa: N802
        path, _, query = self.path.partition('?')
        self.send_response(301)
        self.send_header('Location', path + '/' + ('?' + query if query else ''))
        self.send_header('Content-Length', '0')
        self.end_headers()

    do_HEAD = do_GET


#: {route: handler class with _Route first}, filled by routed().
ROUTED = {}


def routed(tools):
    """The one listener's handler class: `tools` is {tool name: its Handler class}."""
    for name, cls in [('shell', Handler), ('redirect', _Redirect)] + sorted(tools.items()):
        ROUTED[name] = type('Routed' + cls.__name__ + name.title(), (_Route, cls), {})
    return ROUTED['shell']


def listen(handler, preferred):
    """A loopback server on `preferred`, or the next three, or any free port."""
    last = None
    for port in [preferred + n for n in range(4)] + [0]:
        try:
            httpd = ThreadingHTTPServer(('127.0.0.1', port), handler)
        except OSError as exc:
            last = exc
            continue
        httpd.port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd
    raise SystemExit('cannot listen on 127.0.0.1: %s' % last)
