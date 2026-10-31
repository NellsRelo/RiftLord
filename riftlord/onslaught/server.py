"""The editor's local HTTP server: a JSON API over the converter, plus the UI.

Standard library only, so the frozen build carries no web framework. Binds to
loopback on an ephemeral port; the caller reads `port` to know where to point
the window.
"""

import json
import mimetypes
import os
import posixpath
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from riftlord.core import gamepath
from riftlord.onslaught import gamedata

HERE = os.path.dirname(os.path.abspath(__file__))

def web_dirs():
    """Where the built page is."""
    return [gamepath.web_dist('onslaught')]


def web_root():
    for d in web_dirs():
        if os.path.isdir(d):
            return d
    return None


#: Sent by the editor's own page on every write. A page on another site cannot
#: set a custom header without a CORS preflight, which this server never grants,
#: so requiring it stops a request the browser made on someone else's behalf.
GUARD_HEADER = 'X-Onslaught-Editor'


#: Set when the page asks the editor to stop, so the launcher's wait can end. The
#: alternative is leaving a server running that the user has no way to close without a
#: terminal or a process manager -- which is how one ended up serving day-old code.
STOPPING = threading.Event()


def _shutdown(httpd):
    """Stop serving, a moment after the reply has gone out."""
    STOPPING.set()
    httpd.shutdown()


class Handler(BaseHTTPRequestHandler):
    server_version = 'OnslaughtEditor'

    def _local_origin(self):
        """False when the request was made on another site's behalf.

        The server listens on loopback, but loopback is reachable from any page
        the author happens to have open: a build takes the path to write as
        input, so an unguarded POST is a file write anybody's web page can ask
        for. Browsers always set `Origin` on a cross-origin request, so a
        mismatch is refused; a missing one is allowed, which keeps curl and the
        packaging check working.
        """
        origin = self.headers.get('Origin')
        if origin:
            host = origin.split('//', 1)[-1]
            if host not in ('127.0.0.1:%d' % self.server.port,
                            'localhost:%d' % self.server.port):
                return False
        # A DNS name that resolves to loopback would carry a matching Origin of
        # its own, so the Host is checked too.
        host = (self.headers.get('Host') or '').strip()
        if host and host.split(':')[0] not in ('127.0.0.1', 'localhost'):
            return False
        return True

    def log_message(self, fmt, *args):
        pass                                  # the console is not a request log

    # -- plumbing ---------------------------------------------------------
    def _send(self, code, body, ctype, extra=None):
        if isinstance(body, str):
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

    def _json(self, payload, code=200):
        self._send(code, json.dumps(payload), 'application/json')

    def _fail(self, code, message):
        if code != 404:
            from riftlord.core import console
            console.debug('%s failed' % self.path.partition('?')[0], console.ONSLAUGHT_EDITOR)
            console.error(message, console.ONSLAUGHT_EDITOR)
        self._json({'error': message}, code)

    def _read_exactly(self, length):
        """The whole body. A pak is tens of megabytes, and one `read` returns a
        socket's worth rather than all of it, which truncated the file silently."""
        out = bytearray()
        while len(out) < length:
            chunk = self.rfile.read(min(1 << 20, length - len(out)))
            if not chunk:
                break
            out += chunk
        return bytes(out)

    def _blame(self, exc):
        """An error the page can show.

        The converter modules refuse by `raise SystemExit('...')`, which is a
        BaseException: an `except Exception` handler misses it and the handler thread
        dies, so the page sees the socket close rather than a reason. Their message is
        already written for a person, so it is passed through unadorned.
        """
        if isinstance(exc, SystemExit):
            return str(exc) or 'refused'
        return '%s: %s' % (type(exc).__name__, exc)

    # -- routing ----------------------------------------------------------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path, _, query = self.path.partition('?')
        args = {}
        for pair in query.split('&'):
            if '=' in pair:
                k, v = pair.split('=', 1)
                args[k] = urllib.parse.unquote_plus(v)
        try:
            if path.startswith('/api/'):
                if not self._local_origin():
                    return self._fail(403, 'cross-origin requests are refused')
                return self._api(path[5:].strip('/').split('/'), args)
            return self._static(path)
        except BrokenPipeError:
            pass
        except (Exception, SystemExit) as exc:            # noqa: BLE001
            if not isinstance(exc, SystemExit):
                import traceback
                traceback.print_exc()
            self._fail(500, self._blame(exc))

    def do_POST(self):
        if not self._local_origin() or not self.headers.get(GUARD_HEADER):
            return self._fail(403, 'this endpoint only answers the editor\'s page')
        length = int(self.headers.get('Content-Length') or 0)
        path = self.path.partition('?')[0]
        # The picked pak arrives as raw bytes with its name in a header: a file input
        # gives the page the contents and a bare name, never a path, and multipart would
        # only wrap the same two things in a parser this has no need of.
        if path == '/api/openpak/file':
            raw = self._read_exactly(length)
            name = urllib.parse.unquote(self.headers.get('X-Pak-Name') or 'chosen.pak')
            try:
                return self._json(gamedata.open_uploaded(name, raw))
            except BrokenPipeError:
                return None
            except (Exception, SystemExit) as exc:         # noqa: BLE001
                return self._fail(400, self._blame(exc))
        raw = self.rfile.read(length) if length else b'{}'
        try:
            payload = json.loads(raw or b'{}')
        except ValueError:
            return self._fail(400, 'body is not JSON')
        try:
            if path == '/api/install':
                return self._json(gamedata.set_install(payload.get('path')))
            #: a custom campaign's name, description, hub style and banner (bucket.update_campaign)
            if path == '/api/campaign':
                from riftlord.missions import bucket
                return self._json(bucket.update_campaign(str(payload.get('key') or ''),
                                                         payload.get('values')))
            if path == '/api/openpak':
                return self._json(gamedata.open_pak(payload.get('path')))
            if path == '/api/closepak':
                return self._json(gamedata.close_pak(payload.get('path')))
            if path == '/api/rescan':
                return self._json(gamedata.rescan())
            if path == '/api/build':
                from riftlord.onslaught import buildjob
                return self._json(buildjob.start(payload))
            #: Give a mission in Missions its own Guid (bucket.set_guid).
            if path == '/api/bucket/guid':
                from riftlord.missions import bucket
                return self._json(bucket.set_guid(payload.get('id'), payload.get('guid')))
            #: the hub's enemy preview for the spec being edited (makepreview)
            if path == '/api/preview':
                from riftlord.missions import bucket
                spec = payload.get('spec') if isinstance(payload.get('spec'), dict) else {}
                return self._json(bucket.preview_view({'spec': spec},
                                                      payload.get('options')) or {})
            if path == '/api/quit':
                # Answered before the server goes down, or the page sees the socket
                # close rather than a reply and reports a failure for a success.
                self._json({'stopping': True})
                threading.Thread(target=_shutdown, args=(self.server,),
                                 daemon=True).start()
                return None
            return self._fail(404, 'no such endpoint: %s' % path)
        except BrokenPipeError:
            pass
        except (Exception, SystemExit) as exc:            # noqa: BLE001
            self._fail(400, self._blame(exc))

    def _api(self, parts, args):
        if parts == ['maps']:
            return self._json(gamedata.maps())
        if parts == ['units']:
            return self._json(gamedata.units())
        if parts == ['actions']:
            return self._json(gamedata.ACTIONS)
        if parts == ['campaigns']:
            return self._json(gamedata.campaigns(args.get('mission')))
        #: the live Scramble tiers a mission can join (missionscramble.view)
        if parts == ['scramble']:
            from riftlord.missions import missionscramble
            return self._json(missionscramble.view())
        #: the campaign button preview's frame (customcampaign.card_preview)
        if parts == ['campaign', 'frame']:
            from riftlord.missions import customcampaign
            return self._json(customcampaign.card_preview() or {})
        if parts == ['install']:
            return self._json(gamedata.install_status())
        #: Crystal Defense's minimap icon (riftlord.mapeditor.crystal.map_icon_png)
        if parts == ['crystal', 'mapicon.png']:
            from riftlord.mapeditor import crystal
            png = crystal.map_icon_png()
            return self._send(200, png, 'image/png') if png else self._fail(404, 'no icon')
        #: a music track's preview: Ogg Vorbis (riftlord.build.world.levelmusic.preview)
        if len(parts) == 2 and parts[0] == 'music':
            from riftlord.build.world import levelmusic
            if parts[1] not in levelmusic.TRACKS:
                return self._fail(404, 'no such track')
            return self._send(200, levelmusic.preview(parts[1]), 'audio/ogg')
        if parts == ['version']:
            from . import __version__
            return self._json({'version': __version__, 'started': STARTED})
        if parts == ['browse']:
            return self._json(gamedata.browse(args.get('dir')))
        if len(parts) >= 2 and parts[0] == 'map':
            name = parts[1]
            if name not in {m['name'] for m in gamedata.maps()}:
                return self._fail(404, 'unknown map: %s' % name)
            if len(parts) == 2:
                return self._json(gamedata.map_detail(name))
            if parts[2] == 'onslaught':
                from riftlord.onslaught import wavespec
                read = wavespec.read_onslaught(name)
                #: its events read out of the waves (wavespec.split_events)
                caps = gamedata.map_detail(name).get('events')
                if caps is not None:
                    read, _events = wavespec.split_events(read, caps)
                return self._json(read)
            if parts[2] == 'minimap.png':
                volume = args.get('volume')
                png = gamedata.minimap_png(
                    name, int(volume) if volume is not None else None)
                if png is None:
                    return self._fail(404, '%s has no minimap' % name)
                return self._send(200, png, 'image/png')
        #: A bucket item's record, so the page can reopen what it was built from.
        #: Rift Lord's mission defaults, for a new mission's fields (bucket.DEFAULTS
        #: standalone).
        if parts == ['defaults']:
            from riftlord.missions import bucket
            return self._json(bucket.defaults() if bucket.ACTIVE else dict(bucket.DEFAULTS))
        #: Titles already in Missions, which a copy's suggested name steps past.
        if parts == ['bucket', 'titles']:
            from riftlord.onslaught import buildjob
            return self._json(buildjob.titles())
        #: A mission's Guid as the game spells it, and a fresh one for Generate.
        if len(parts) == 3 and parts[:2] == ['bucket', 'guid']:
            from riftlord.missions import bucket
            return self._json({'guid': bucket.guid_spelled(parts[2])})
        if parts == ['guid', 'new']:
            from riftlord.missions import bucket
            return self._json({'guid': bucket.new_guid()})
        #: whether a Guid is spelled right and who else uses it (the Details dialog)
        if parts == ['guid', 'check']:
            from riftlord.missions import bucket
            return self._json(bucket.guid_check(args.get('guid') or '', args.get('mission')))
        #: the source level's own pictures, for a mission without its own (sourcepictures)
        if parts == ['sourcepictures']:
            from riftlord.missions import bucket
            rec = bucket.load(args['mission']) if args.get('mission') and bucket.ACTIVE else None
            if rec:
                return self._json(bucket.source_pictures(rec) or {})
            return self._json(bucket.source_pictures({'level': args.get('map')}) or {})
        if len(parts) == 3 and parts[:2] == ['bucket', 'item']:
            from riftlord.missions import bucket
            got = bucket.item(parts[2], built=True)
            return self._json(got) if got else self._fail(404, 'no such item')
        if parts and parts[0] == 'build' and len(parts) == 2:
            from riftlord.onslaught import buildjob
            got = buildjob.status(parts[1])
            return self._json(got) if got else self._fail(404, 'no such job')
        return self._fail(404, 'no such endpoint: /api/%s' % '/'.join(parts))

    def _static(self, path):
        root = web_root()
        if root is None:
            return self._send(200, UNBUILT, 'text/html; charset=utf-8')
        rel = posixpath.normpath(path).lstrip('/')
        target = os.path.join(root, rel)
        if os.path.isdir(target) or not rel:
            target = os.path.join(root, rel, 'index.html')
        if not os.path.abspath(target).startswith(os.path.abspath(root)):
            return self._fail(403, 'outside the web root')
        if not os.path.isfile(target):
            # a single-page app serves its shell for any unknown route
            target = os.path.join(root, 'index.html')
            if not os.path.isfile(target):
                return self._fail(404, 'not found: %s' % path)
        ctype = mimetypes.guess_type(target)[0] or 'application/octet-stream'
        with open(target, 'rb') as fh:
            return self._send(200, fh.read(), ctype)


UNBUILT = """<!doctype html><meta charset=utf-8>
<title>Onslaught Editor</title>
<style>body{font:15px/1.6 system-ui;margin:3rem auto;max-width:34rem;color:#ddd;
background:#15120e}code{background:#2a241c;padding:.15rem .4rem;border-radius:3px}</style>
<h1>The UI is not built yet</h1>
<p>The API is running, but no built frontend was found. From
<code>onslaughtgui/web</code> run <code>npm install</code> then
<code>npm run build</code>.</p>
<p>The API itself answers on <code>/api/maps</code>,
<code>/api/units</code> and <code>/api/map/&lt;name&gt;</code>.</p>
"""


#: The editor's own port. Fixed so the address is the same every launch: a bookmark
#: keeps working, and `?map=...` links stay valid between sessions. 8779 sits clear of
#: the assembly tool (8777) and Rift Lord (8850).
DEFAULT_PORT = 8779


#: When this process started, so a page can tell whether it was built after the server
#: was. The static files come off disk on every request, which is how a page that POSTs
#: to a route this process does not have got "body is not JSON" back instead of a clue.
STARTED = time.time()


def serve(port=None):
    """Start the server on loopback in a daemon thread; returns it.

    `DEFAULT_PORT` unless asked otherwise. If it is taken -- a second copy, or one that
    has not finished shutting down -- the next few are tried before giving up and letting
    the OS choose, so a stale process cannot stop the editor from opening at all. Pass 0
    to ask for an ephemeral port outright.
    """
    wanted = DEFAULT_PORT if port is None else port
    tries = [wanted] if wanted == 0 else [wanted + n for n in range(4)] + [0]
    last = None
    for candidate in tries:
        try:
            httpd = ThreadingHTTPServer(('127.0.0.1', candidate), Handler)
        except OSError as exc:
            last = exc
            continue
        httpd.port = httpd.server_address[1]
        httpd.wanted_port = wanted
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd
    raise SystemExit('cannot listen on 127.0.0.1: %s' % last)
