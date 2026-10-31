"""The Map Editor's local server (it was once called Rift Lord, the name the whole app
now has): a JSON API over one OMD3 level, plus the UI.

Standard library only, the same shape as the assembly tool's and the Onslaught
Editor's: loopback, a fixed port, a guard header on every write, and nothing
cached. The serving arrangement is deliberately identical to theirs because that is
the part already through packaging; what differs is everything above it.
"""

import json
import mimetypes
import os
import posixpath
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from riftlord.core import gamepath
from riftlord.mapeditor import leveldata

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.mapeditor import omd3serve  # noqa: E402

#: A preview picture is drawn once from shipped art and kept on disk (omd3serve.PREVIEWS),
#: so the browser may keep it too: for a day, then ask again.
THUMB_CACHE = 'public, max-age=86400'
#: How many previews the warm-up draws at once, in the background.
THUMB_WARM_WORKERS = 2

#: Sent by the tool's own page on every write. Another site cannot set a custom
#: header without a CORS preflight, which this server never grants, so requiring it
#: stops a request a browser made on someone else's behalf.
GUARD_HEADER = 'X-Rift-Lord'


def web_dirs():
    """Where the built page is."""
    return [gamepath.web_dist('mapeditor')]


def web_root():
    for d in web_dirs():
        if os.path.isdir(d) and os.path.exists(os.path.join(d, 'index.html')):
            return d
    return None


_THUMBS = {}
_THUMBS_LOCK = threading.Lock()
#: Whether warm_thumbnails has started.
_WARMING = False


#: {name: Event} for previews being drawn, so a card and the warm-up never draw one twice
_DRAWING = {}


def thumbnail(name):
    """A New Object card's preview PNG ('kit:<class>', 'compound:<key>' or a mesh name), kept
    in memory beside the disk cache (kitdonors / compounds / omd3serve), or None."""
    with _THUMBS_LOCK:
        if name in _THUMBS:
            return _THUMBS[name]
        wait = _DRAWING.get(name)
        if wait is None:
            _DRAWING[name] = threading.Event()
    if wait is not None:
        wait.wait()
        with _THUMBS_LOCK:
            return _THUMBS.get(name)
    png = None
    try:
        if name.startswith('kit:'):
            from riftlord.mapeditor import kitactors
            from riftlord.mapeditor import kitdonors
            cls = name[len('kit:'):]
            png = kitactors.thumbnail(cls) if kitactors.handles(cls) else kitdonors.thumbnail(cls)
        elif name.startswith('compound:'):
            from riftlord.mapeditor import compounds
            png = compounds.thumbnail(name[len('compound:'):])
        elif name == 'crystal:':
            from riftlord.mapeditor import crystal
            png = crystal.thumbnail()
        else:
            png = omd3serve.thumbnail(name)
    finally:
        with _THUMBS_LOCK:
            _THUMBS[name] = png
            _DRAWING.pop(name).set()
    return png


def warm_thumbnails():
    """Draw every New Object preview not yet on disk, in the background, in the order
    the cards are listed, so they show at once when the panel opens. Once per server."""
    global _WARMING
    with _THUMBS_LOCK:
        if _WARMING:
            return
        _WARMING = True

    def run():
        #: daemon workers, not a ThreadPoolExecutor: the interpreter joins executor
        #: threads at exit, so quitting waited for the whole warm-up
        import queue
        try:
            names = list(dict.fromkeys(r['thumb'] for r in leveldata.catalogue()
                                       if r.get('thumb')))
        except Exception:                            # noqa: BLE001
            return
        todo = queue.Queue()
        for name in names:
            todo.put(name)

        def work():
            while True:
                try:
                    name = todo.get_nowait()
                except queue.Empty:
                    return
                try:
                    thumbnail(name)
                except Exception:                    # noqa: BLE001
                    pass
        for k in range(THUMB_WARM_WORKERS):
            threading.Thread(target=work, name='thumb-warm-%d' % k, daemon=True).start()
    threading.Thread(target=run, name='thumb-warm', daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    server_version = 'RiftLord/0.1'

    def log_message(self, *_args):
        pass                                          # the console is the user's

    # -- plumbing ---------------------------------------------------------
    def _send(self, code, body, kind='application/json', cache=None):
        """Send `body`. `cache` is a Cache-Control value for what may be kept (a
        thumbnail); everything else is never cached."""
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode('utf-8')
        elif isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', cache or 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _fail(self, code, message):
        if code != 404:
            from riftlord.core import console
            console.debug('%s failed' % self.path.partition('?')[0], console.MAP_EDITOR)
            console.error(message, console.MAP_EDITOR)
        self._send(code, {'error': message})

    def _body(self):
        length = int(self.headers.get('Content-Length') or 0)
        if not length:
            return None
        return json.loads(self.rfile.read(length).decode('utf-8'))

    # -- the API ----------------------------------------------------------
    def do_GET(self):                                # noqa: N802
        path = posixpath.normpath(urllib.parse.unquote(
            self.path.split('?', 1)[0]))
        if not path.startswith('/api/'):
            return self._static(path)
        parts = [p for p in path[len('/api/'):].split('/') if p]
        try:
            if parts == ['levels']:
                return self._send(200, leveldata.levels())
            #: a level from scratch (riftlord/scratch.py): its hosts, and the view of one
            if parts == ['scratch', 'bases']:
                from riftlord.mapeditor import scratch
                return self._send(200, scratch.bases())
            if parts == ['scratch', 'skies']:
                from riftlord.mapeditor import scratch
                return self._send(200, scratch.skies())
            #: the box an added blocking volume is (riftlord/mapeditor/volumes.py donor_view)
            if parts == ['volumes', 'donor']:
                from riftlord.mapeditor import volumes
                return self._send(200, volumes.donor_view() or {})
            #: the game modes a new level can be made for (scratch.game_modes)
            if parts == ['scratch', 'modes']:
                from riftlord.mapeditor import scratch
                return self._send(200, scratch.game_modes())
            #: the weather presets Map Settings offers (riftlord/weather.py)
            if parts == ['weather']:
                from riftlord.build.world import weather
                return self._send(200, weather.choices())
            #: wall plates, ambience cues, lightning, environmental traps (controllers.py)
            if parts == ['controllers']:
                from riftlord.mapeditor import controllers
                return self._send(200, controllers.choices())
            if len(parts) == 3 and parts[:2] == ['scratch', 'level']:
                from riftlord.mapeditor import scratch
                return self._send(200, scratch.view(parts[2]))
            #: Where a mission can be listed: no campaign, a new or installed custom
            #: campaign, or a shipped one (customcampaign.choices), each with its Campaign
            #: Index range for `?mission=` (bucket.choices_for).
            if parts == ['campaigns']:
                from riftlord.missions import bucket
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                return self._send(200, bucket.choices_for((query.get('mission') or [''])[0]))
            #: the live Scramble tiers a mission can join (missionscramble.view)
            if parts == ['scramble']:
                from riftlord.missions import missionscramble
                return self._send(200, missionscramble.view())
            #: the campaign button preview's frame (customcampaign.card_preview)
            if parts == ['campaign', 'frame']:
                from riftlord.missions import customcampaign
                return self._send(200, customcampaign.card_preview() or {})
            #: A bucket item's record, so the page can reopen what it was built from.
            #: Rift Lord's mission defaults, for a new mission's fields (bucket.DEFAULTS
            #: standalone).
            #: Missions for the level picker: what reopens with its plan, and the
            #: mission's own copy of its level, which the picker leaves out.
            if parts == ['missions']:
                from riftlord.missions import bucket
                if not bucket.ACTIVE:
                    return self._send(200, [])
                return self._send(200, [{'id': i['id'], 'title': i['title'], 'map': i['map'],
                                         'editable': i['editable']['riftlord']}
                                        for i in bucket.items()['items']])
            if parts == ['defaults']:
                from riftlord.missions import bucket
                return self._send(200, bucket.defaults() if bucket.ACTIVE
                                  else dict(bucket.DEFAULTS))
            if len(parts) == 3 and parts[:2] == ['bucket', 'item']:
                from riftlord.missions import bucket
                from riftlord.mapeditor import fablibrary
                got = bucket.item(parts[2])
                if got and isinstance(got.get('plan'), dict):
                    # Fab pieces moved to their libraries' current builds; the record on disk
                    # changes only when the user saves it
                    got = dict(got, plan=fablibrary.current_plan(got['plan']))
                return self._send(200, got) if got else self._fail(404, 'no such item')
            #: a music track's preview: Ogg Vorbis (riftlord/levelmusic.py)
            if len(parts) == 2 and parts[0] == 'music':
                from riftlord.build.world import levelmusic
                if parts[1] not in levelmusic.TRACKS:
                    return self._fail(404, 'no such track')
                return self._send(200, levelmusic.preview(parts[1]), 'audio/ogg')
            #: the Trace tool (riftlord/trace.py): the games and levels it can draw, one
            #: level's packed ghost, and the tool's kept state for a level or mission
            if parts == ['trace', 'sources']:
                from riftlord.mapeditor import trace
                return self._send(200, trace.sources())
            if len(parts) == 4 and parts[:2] == ['trace', 'geometry']:
                from riftlord.mapeditor import trace
                return self._send(200, trace.geometry(parts[2], parts[3]),
                                  'application/octet-stream')
            #: its Textured display and its trap grids
            if len(parts) == 4 and parts[:2] == ['trace', 'textured']:
                from riftlord.mapeditor import trace
                return self._send(200, trace.textured(parts[2], parts[3]),
                                  'application/octet-stream')
            if len(parts) == 4 and parts[:2] == ['trace', 'grids']:
                from riftlord.mapeditor import trace
                return self._send(200, trace.grids(parts[2], parts[3]))
            if len(parts) == 3 and parts[:2] == ['trace', 'state']:
                from riftlord.mapeditor import trace
                return self._send(200, trace.load_state(parts[2]))
            if parts == ['units']:
                return self._send(200, leveldata.units())
            #: compound kits and decoration tool defaults (riftlord/compounds.py)
            if parts == ['decor']:
                from riftlord.mapeditor import compounds
                return self._send(200, compounds.decor())
            if len(parts) == 2 and parts[0] == 'level':
                #: with the Crystal a built level places, as a plan item (crystal.adopt)
                from riftlord.mapeditor import crystal
                return self._send(200, dict(leveldata.level(parts[1]),
                                            crystal=crystal.adopt(parts[1])))
            #: Reading a saved plan back is EXPLICIT, and that is the whole point.
            #:
            #: `openLevel` deliberately does not load it: a level that reopened wearing
            #: yesterday's edits read as though the shipped level had been changed. So
            #: resuming is a button the user presses, and this is what it fetches.
            if len(parts) == 2 and parts[0] == 'plan':
                from riftlord.mapeditor import fablibrary      # Fab pieces: current builds
                return self._send(200, fablibrary.current_plan(leveldata.load_plan(parts[1])))
            #: Map settings for the right sidebar; `?mission=<id>` adds that mission's rows.
            if len(parts) == 2 and parts[0] == 'mapinfo':
                from riftlord.mapeditor import mapinfo
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                return self._send(200, mapinfo.info(
                    parts[1], (query.get('mission') or [None])[0]))
            #: A fresh mission Guid in UE's spelling (bucket.new_guid).
            if parts == ['guid', 'new']:
                from riftlord.missions import bucket
                return self._send(200, {'guid': bucket.new_guid()})
            #: `?guid=<text>&mission=<id>`: {ok, error, shipped, missions} -- whether the
            #: Guid is spelled right and who else uses it.
            if parts == ['guid', 'check']:
                from riftlord.missions import bucket
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                text = (query.get('guid') or [''])[0]
                try:
                    got = bucket.guid_clashes(text, (query.get('mission') or [None])[0]) \
                        if bucket.ACTIVE else {'shipped': [], 'missions': []}
                    got['guid'] = bucket.spell_guid(bucket.parse_guid(text))
                    return self._send(200, dict(got, ok=not got['shipped'], error=''))
                except SystemExit as exc:
                    return self._send(200, {'ok': False, 'error': str(exc),
                                            'shipped': [], 'missions': []})
            #: {spawner index: {waves, spawns}}, what goes with a removed spawner;
            #: `?mission=<id>` reads that mission's waves.
            if len(parts) == 2 and parts[0] == 'spawnerwaves':
                from riftlord.mapeditor import planactors
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                return self._send(200, planactors.spawner_waves(planactors.mission_spec(
                    parts[1], (query.get('mission') or [None])[0])))
            #: the level's lights with their brightness, for the editor's lighting preview
            if len(parts) == 2 and parts[0] == 'lights':
                return self._send(200, leveldata.light_rig(parts[1]))
            #: the Mission Events list: what the host and the piece can do, the tracks,
            #: bosses and spawners (riftlord/missionevents.py editor_choices)
            if len(parts) == 2 and parts[0] == 'events':
                from riftlord.missions import missionevents
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                return self._send(200, missionevents.editor_choices(
                    parts[1], (query.get('rifts') or [''])[0]))
            #: the Crystal's hitbox fitted at `?matrix=` on the level's own walkable floor,
            #: for the panel's HitX / HitY placeholders (riftlord/mapeditor/crystalfit.py)
            if len(parts) == 3 and parts[0] == 'crystal' and parts[2] == 'fit':
                from riftlord.mapeditor import crystalfit
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                m = [float(v) for v in (query.get('matrix') or [''])[0].split(',') if v.strip()]
                if len(m) != 16:
                    return self._send(400, {'error': 'matrix needs 16 numbers'})
                return self._send(200, crystalfit.editor_fit(parts[1], m))
            #: the Crystal card and panel: where it goes over `?rift=x,y,z`, whether its
            #: mod is installed, its defaults and stand-in (riftlord/mapeditor/crystal.py)
            if len(parts) == 2 and parts[0] == 'crystal':
                from riftlord.mapeditor import crystal
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                rift = [float(v) for v in (query.get('rift') or [''])[0].split(',')
                        if v.strip()]
                return self._send(200, crystal.editor_info(
                    parts[1], rift if len(rift) == 3 else None))
            #: the height an unstable rift clicked at `?at=x,y,z` goes to over the walkable
            #: floor under it (riftlord/build/navigation/riftfloor.py)
            if len(parts) == 2 and parts[0] == 'riftfloor':
                from riftlord.build.navigation import riftfloor
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                at = [float(v) for v in (query.get('at') or [''])[0].split(',') if v.strip()]
                if len(at) != 3:
                    return self._send(400, {'error': 'at needs x,y,z'})
                #: `?for=spawner`: a ground spawner's height over its floor instead;
                #: `?for=point`: a ground path point's
                return self._send(200, riftfloor.editor_snap(
                    parts[1], at, (query.get('for') or [''])[0]))
            #: the level pieces a Crystal at `?matrix=` (16 numbers) parks, by its mod's
            #: manifest (riftlord/mapeditor/crystalpark.py)
            if len(parts) == 3 and parts[0] == 'crystal' and parts[2] == 'parked':
                from riftlord.mapeditor import crystalpark
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                m = [float(v) for v in (query.get('matrix') or [''])[0].split(',') if v.strip()]
                return self._send(200, {'parked': crystalpark.parked(parts[1], m)
                                        if len(m) == 16 else []})
            #: the supported mods detected now and the features they turn on, for New
            #: Object's cards (riftlord/core/supportedmods.py)
            if parts == ['supportedmods']:
                from riftlord.core import supportedmods
                return self._send(200, supportedmods.state())
            if len(parts) == 2 and parts[0] == 'coverage':
                return self._send(200, leveldata.coverage(parts[1]))
            if len(parts) == 2 and parts[0] == 'onslaught':
                return self._send(200, leveldata.onslaughts(parts[1]))
            if len(parts) == 2 and parts[0] == 'region':
                q = urllib.parse.parse_qs(
                    urllib.parse.urlsplit(self.path).query)
                nums = [float(v) for v in (q.get('box') or [''])[0].split(',')
                        if v.strip()]
                if len(nums) != 6:
                    return self._fail(400, 'box needs six numbers')
                box = [nums[:3], nums[3:]]
                box = [[min(box[0][i], box[1][i]) for i in range(3)],
                       [max(box[0][i], box[1][i]) for i in range(3)]]
                return self._send(200, leveldata.region_report(parts[1], box))
            if parts == ['schema']:
                return self._send(200, {
                    'schema': {k: {f: {'kind': v[0], 'default': v[1]}
                                   for f, v in fields.items()}
                               for k, fields in leveldata.SCHEMA.items()},
                    'doors': list(leveldata.DOOR_CLASSES),
                    'onlyFor': {k: list(v) for k, v in leveldata.ONLY_FOR.items()},
                    'kinds': list(leveldata.KINDS)})
            if parts == ['looks']:
                return self._send(200, leveldata.looks())
            #: a level's own loading screen and hub portal pictures (sourcepictures.py)
            if len(parts) == 2 and parts[0] == 'sourcepictures':
                from riftlord.missions import bucket
                return self._send(200, bucket.source_pictures({'level': parts[1]}) or {})
            #: every material instance a piece can be painted with (look.paints)
            if parts == ['materials']:
                return self._send(200, leveldata.materials())
            if len(parts) == 2 and parts[0] == 'look':
                from riftlord.build.world import skyfog
                home, fog = skyfog.fog_for(parts[1])
                return self._send(200, {'material': parts[1],
                                        'texture': leveldata.look_texture(parts[1]),
                                        'fogHome': home, 'fog': fog})
            if parts == ['catalogue']:
                return self._send(200, leveldata.catalogue())
            #: start drawing the previews the browser has not got yet
            if parts == ['thumbs', 'warm']:
                warm_thumbnails()
                return self._send(200, {'ok': True})
            if len(parts) == 2 and parts[0] == 'kit':
                from riftlord.mapeditor import kitactors
                got = leveldata.kit_parts(parts[1])
                #: a pickup draws as its particles, which nothing but the view uses
                if kitactors.handles(parts[1]):
                    got = dict(got, parts=kitactors.view_parts(parts[1]))
                #: a part's override material as the level's pieces resolve theirs: its
                #: base-colour texture and tint (main.js partMesh)
                got = dict(got, parts=[dict(p, materialTexture=leveldata.look_texture(
                    p['material']), materialTint=leveldata.material_tint(p['material']))
                    if p.get('material') else p for p in got.get('parts') or []])
                return self._send(200, got)
            if len(parts) == 2 and parts[0] == 'geometry':
                q = urllib.parse.parse_qs(
                    urllib.parse.urlsplit(self.path).query)
                start = int((q.get('from') or ['0'])[0])
                count = int((q.get('count') or ['12'])[0])
                only = None
                if q.get('scratch'):
                    from riftlord.mapeditor import scratch
                    only = scratch.mesh_names(parts[1])
                return self._send(
                    200, leveldata.geometry(parts[1], start, count, names=only))
            if len(parts) == 2 and parts[0] == 'roster':
                return self._send(200, leveldata.roster(parts[1]))
            if len(parts) == 2 and parts[0] == 'plan':
                from riftlord.mapeditor import fablibrary      # Fab pieces: current builds
                return self._send(200, fablibrary.current_plan(leveldata.load_plan(parts[1])))
            #: Fab art libraries (riftlord/fablibrary.py): the packs, one pack's pieces,
            #: a piece's thumbnail; meshes and textures go through /api/mesh and /api/texture
            if parts == ['fab', 'libraries']:
                from riftlord.mapeditor import fablibrary
                return self._send(200, fablibrary.libraries())
            if len(parts) == 3 and parts[:2] == ['fab', 'pieces']:
                from riftlord.mapeditor import fablibrary
                return self._send(200, fablibrary.pieces(parts[2]))
            if len(parts) >= 4 and parts[:2] == ['fab', 'thumb']:
                from riftlord.mapeditor import fablibrary
                png = fablibrary.thumbnail(parts[2], '/'.join(parts[3:]))
                return self._send(200, png, 'image/png', cache=THUMB_CACHE) if png \
                    else self._fail(404, 'no preview')
            if len(parts) >= 3 and parts[:2] == ['mesh', 'fab']:
                from riftlord.mapeditor import fablibrary
                got = fablibrary.mesh('/'.join(parts[1:]))
                return self._send(200, got) if got else self._fail(404, 'no such mesh')
            #: meshes and textures of the kits placed chests and potions come from
            #: (riftlord/kitactors.py)
            if len(parts) >= 3 and parts[:2] == ['mesh', 'kit']:
                from riftlord.mapeditor import kitactors
                got = kitactors.mesh('/'.join(parts[1:]))
                return self._send(200, got) if got else self._fail(404, 'no such mesh')
            if parts == ['pickup', 'sprite']:
                from riftlord.mapeditor import kitactors
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                png = kitactors.sprite_png((q.get('path') or [''])[0])
                return self._send(200, png, 'image/png') if png else self._fail(404, 'no such sprite')
            if len(parts) >= 3 and parts[:2] == ['texture', 'kit']:
                from riftlord.mapeditor import kitactors
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                png = kitactors.texture_png('/'.join(parts[1:]),
                                            int((q.get('size') or ['512'])[0]))
                return self._send(200, png, 'image/png') if png else self._fail(404, 'no such texture')
            if len(parts) >= 3 and parts[:2] == ['texture', 'fab']:
                from riftlord.mapeditor import fablibrary
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                png = fablibrary.texture_png('/'.join(parts[1:]), int(
                    (q.get('size') or [str(fablibrary.TEXTURE_DEFAULT)])[0]))
                return self._send(200, png, 'image/png') if png else self._fail(404, 'no such texture')
            if len(parts) >= 2 and parts[0] == 'mesh':
                got = omd3serve.mesh('/'.join(parts[1:]))
                if got is None:
                    return self._fail(404, 'no such mesh')
                try:
                    #: as a level slice serves it (leveldata.geometry)
                    got = dict(got, oneSided=leveldata.one_sided(got),
                               tint=leveldata.material_tint(got.get('material')),
                               slotTints=[leveldata.material_tint(s)
                                          for s in (got.get('slots') or [])])
                except Exception:                    # noqa: BLE001
                    pass
                return self._send(200, got)
            if len(parts) >= 2 and parts[0] == 'texture':
                q = urllib.parse.parse_qs(
                    urllib.parse.urlsplit(self.path).query)
                size = max(64, min(4096, int((q.get('size') or ['256'])[0])))
                png = omd3serve.texture_png('/' + '/'.join(parts[1:]), size)
                if not png:
                    return self._fail(404, 'no such texture')
                return self._send(200, png, 'image/png')
            if len(parts) == 2 and parts[0] == 'thumb':
                png = thumbnail(parts[1])
                if not png:
                    return self._fail(404, 'no preview')
                return self._send(200, png, 'image/png', cache=THUMB_CACHE)
        #: `BaseException`, because the converter REFUSES BY `raise SystemExit`.
        #:
        #: `SystemExit` is not an `Exception`, so it escaped this handler, killed the
        #: worker thread and left the browser holding a closed socket -- no status, no
        #: message, nothing to show the user. `Dungeons` is in the level list and
        #: refuses with "names no onslaught coordinator", which is a perfectly good
        #: answer that the user could never see. KeyboardInterrupt is left alone so
        #: Ctrl-C still stops the server.
        except KeyboardInterrupt:
            raise
        except BaseException as exc:                 # noqa: BLE001
            return self._fail(400 if isinstance(exc, SystemExit) else 500,
                              (str(exc) or 'refused') if isinstance(exc, SystemExit)
                              else '%s: %s' % (type(exc).__name__, exc))
        return self._fail(404, 'no such endpoint')

    def do_POST(self):                               # noqa: N802
        if self.headers.get(GUARD_HEADER) is None:
            return self._fail(403, 'missing %s' % GUARD_HEADER)
        path = posixpath.normpath(urllib.parse.unquote(
            self.path.split('?', 1)[0]))
        try:
            body = self._body()
            #: a new level from scratch: its starting plan, not saved
            if path == '/api/scratch/new':
                from riftlord.mapeditor import scratch
                opts = body or {}
                return self._send(200, scratch.new_plan(
                    str(opts.get('name') or ''), str(opts.get('base') or scratch.DEFAULT_BASE),
                    sky=str(opts['sky']) if opts.get('sky') else None))
            #: weather effects over the level's play area, as fx items to add
            if path.startswith('/api/weather/suggest/'):
                from riftlord.build.world import weather
                return self._send(200, weather.suggest(path.rsplit('/', 1)[-1],
                                                       str((body or {}).get('kind') or ''),
                                                       (body or {}).get('plan')))
            #: the Trace tool's state for a level or mission (riftlord/trace.py)
            if path.startswith('/api/trace/state/'):
                from riftlord.mapeditor import trace
                return self._send(200, trace.save_state(path.rsplit('/', 1)[-1], body or {}))
            if path.startswith('/api/plan/'):
                name = path.rsplit('/', 1)[-1]
                return self._send(200, leveldata.save_plan(name, body or {}))
            #: {at, for, plan}: the height api/riftfloor gives, over the floor the open plan
            #: leaves (riftlord/build/navigation/riftfloor.py plan_floor)
            if path.startswith('/api/riftfloor/'):
                from riftlord.build.navigation import riftfloor
                opts = body or {}
                at = [float(v) for v in opts.get('at') or []]
                if len(at) != 3:
                    return self._send(400, {'error': 'at needs x,y,z'})
                return self._send(200, riftfloor.editor_snap(
                    path.rsplit('/', 1)[-1], at, str(opts.get('for') or ''),
                    opts.get('plan') or None))
            #: a custom campaign's name, description, hub style and banner (bucket.update_campaign)
            if path == '/api/campaign':
                from riftlord.missions import bucket
                opts = body or {}
                return self._send(200, bucket.update_campaign(str(opts.get('key') or ''),
                                                              opts.get('values')))
            #: the minimap a build would redraw for this plan (riftlord/minimapredraw.py)
            if path.startswith('/api/minimap/'):
                from riftlord.build.minimap import minimapredraw
                opts = body or {}
                return self._send(200, minimapredraw.preview(
                    path.rsplit('/', 1)[-1], opts.get('plan') or {},
                    reframe=bool(opts.get('reframe', True)), quick=bool(opts.get('quick'))))
            #: Save As: the open mission kept as a NEW Missions record (its own title, mod
            #: and Guids), its waves copied from the mission it was opened from
            #: (bucket.copy), then this plan saved into it (bucket.save_map)
            if path.startswith('/api/saveas/'):
                from riftlord.missions import bucket
                if not bucket.ACTIVE:
                    raise SystemExit('Missions are only kept while Rift Lord runs')
                name = path.rsplit('/', 1)[-1]
                opts = body or {}
                title = ' '.join(str(opts.get('title') or '').split())[:bucket.TITLE_MAX]
                if not title:
                    raise SystemExit('Enter a name.')
                source = str(opts['mission']) if opts.get('mission') else None
                rec = bucket.load(source) if source else None
                into = bucket.copy(source, title) if rec and bucket._editable(rec) else None
                got = bucket.save_map(
                    name, leveldata.load_plan(name), title=title,
                    details=opts.get('details') or None,
                    placement=opts.get('placement') or None, mission=into)
                from riftlord.core import console
                console.info('saved %s as %s' % (source or name, got['title']),
                             console.MAP_EDITOR)
                return self._send(200, dict(got, copiedFrom=source if into else None))
            if path.startswith('/api/build/'):
                from riftlord.build import emit
                name = path.rsplit('/', 1)[-1]
                opts = body or {}
                from riftlord.core import console
                from riftlord.missions import bucket
                # Inside Rift Lord (the app) a save keeps the editable mission in
                # Missions; Install and Package build it (workshop/SAVE-MODEL.md).
                if bucket.ACTIVE:
                    guid = str(opts.get('guid') or '').strip()
                    if guid:
                        clash = bucket.guid_clashes(guid, opts.get('mission') or None)
                        if clash['shipped']:
                            raise SystemExit('that Guid is the shipped mission %s'
                                             % ', '.join(clash['shipped']))
                    got = bucket.save_map(
                        name, leveldata.load_plan(name), title=opts.get('title') or None,
                        details=opts.get('details') or None,
                        placement=opts.get('placement') or None,
                        mission=str(opts['mission']) if opts.get('mission') else None)
                    if guid:
                        given = bucket.set_guid(got['mission'], guid)
                        got['guid'] = given['guid']
                        if given['missions']:
                            got['guidShared'] = given['missions']
                    console.info('saved %s%s' % (
                        got['title'], ' (in %s)' % got['campaign'] if got.get('campaign')
                        else ''), console.MAP_EDITOR)
                    #: what goes with each level spawner the plan removes
                    from riftlord.mapeditor import planactors
                    try:
                        plan = leveldata.load_plan(name)
                        removed = planactors.removed_spawner_notes(
                            plan, planactors.level_actors(name),
                            planactors.mission_spec(name, got.get('mission')))
                    except Exception as exc:         # noqa: BLE001
                        removed = ['could not count the removed spawners\' waves: %s'
                                   % exc]
                    return self._send(200, dict(got, checks_failed=[], not_applied=[],
                                                removed_waves=removed))
                #: a chest, potion or Crystal whose supported mod is not detected
                from riftlord.core import supportedmods
                missing = supportedmods.build_problems(leveldata.load_plan(name))
                if missing:
                    raise SystemExit('%s could not be built: %s.'
                                     % (opts.get('title') or name, '; '.join(missing)))
                console.info('building %s' % (opts.get('title') or name), console.MAP_COOKER)
                into = {}
                if opts.get('mission') and bucket.ACTIVE:
                    out, mod = bucket.target(str(opts['mission']))
                    into = {'out': out, 'mod': mod}
                from riftlord.missions import missionunlocks
                report = emit.write(
                    name, install=bool(opts.get('install')),
                    title=opts.get('title') or None,
                    placement=opts.get('placement') or None,
                    details=opts.get('details') or None,
                    scramble=(opts.get('details') or {}).get('scramble'),
                    #: its place in a shipped campaign (emit.build `into_index`)
                    into_index=(opts.get('details') or {}).get('campaignIndex') or None,
                    #: what beating it grants, as the Missions page set it (missionunlocks)
                    unlocks=missionunlocks.of_record(bucket.load(str(opts['mission'])))
                    if into else None,
                    **into)
                # Inside Rift Lord (the app) the build went to Missions: note what it was
                # built from, so it can be opened again for editing. Its level is now the
                # mission's own copy, which the Onslaught Editor edits in place (kind map).
                bucket.record(report.get('written'), 'riftlord', level=name,
                              plan=leveldata.load_plan(name),
                              title=report.get('title'),
                              details=bucket.without_files(opts.get('details')),
                              placement=report.get('placement'), kind='map',
                              map='%s_Map01' % report['mod'] if report.get('written') else None)
                if report.get('written'):
                    console.info('wrote %s' % os.path.basename(report['written']),
                                 console.MAP_COOKER)
                for failed in report.get('checks_failed') or []:
                    console.error('not built: %s' % failed, console.MAP_COOKER)
                for skipped in report.get('not_applied') or []:
                    console.warn('not applied: %s' % skipped, console.MAP_COOKER)
                return self._send(200, report)
        #: `BaseException`, because the converter REFUSES BY `raise SystemExit`.
        #:
        #: `SystemExit` is not an `Exception`, so it escaped this handler, killed the
        #: worker thread and left the browser holding a closed socket -- no status, no
        #: message, nothing to show the user. `Dungeons` is in the level list and
        #: refuses with "names no onslaught coordinator", which is a perfectly good
        #: answer that the user could never see. KeyboardInterrupt is left alone so
        #: Ctrl-C still stops the server.
        except KeyboardInterrupt:
            raise
        except BaseException as exc:                 # noqa: BLE001
            return self._fail(400 if isinstance(exc, SystemExit) else 500,
                              (str(exc) or 'refused') if isinstance(exc, SystemExit)
                              else '%s: %s' % (type(exc).__name__, exc))
        return self._fail(404, 'no such endpoint')

    # -- the UI -----------------------------------------------------------
    def _static(self, path):
        root = web_root()
        if not root:
            return self._send(200, _NO_UI, 'text/html; charset=utf-8')
        if path in ('/', ''):
            path = '/index.html'
        target = os.path.normpath(os.path.join(root, path.lstrip('/')))
        if not target.startswith(root) or not os.path.isfile(target):
            target = os.path.join(root, 'index.html')
            if not os.path.isfile(target):
                return self._fail(404, 'not found')
        kind, _enc = mimetypes.guess_type(target)
        with open(target, 'rb') as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header('Content-Type', kind or 'application/octet-stream')
        self.send_header('Content-Length', str(len(data)))
        # Never cached: the files change under the browser every time the UI is
        # rebuilt, and a stale index.html points at a bundle that is gone.
        self.send_header('Cache-Control', 'no-store, must-revalidate')
        self.end_headers()
        self.wfile.write(data)


_NO_UI = """<!doctype html><meta charset="utf-8">
<title>Map Editor</title>
<body style="font:14px system-ui;padding:2rem;max-width:40rem">
<h1>The UI has not been built yet</h1>
<p>The API is running. Build the front end with:</p>
<pre>cd riftlord/web &amp;&amp; npm install &amp;&amp; npm run build</pre>
<p>Or try the API directly: <a href="api/levels">api/levels</a></p>
"""


def serve(port=0, host='127.0.0.1'):
    """Start the server. Returns (httpd, port); the caller opens a browser."""
    httpd = ThreadingHTTPServer((host, port), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, httpd.server_address[1]
