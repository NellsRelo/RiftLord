"""Rift Lord's Missions bucket: where the Map Editor and the Onslaught Editor put what
they build, unpacked, until it is installed.

While Rift Lord runs, `gamepath.TARGET` points at the bucket folder, so both editors
write their builds, notes and campaigns there instead of into `~mods` -- and write them
LOOSE: each mission is a folder of cooked files (`<stem>/OMD/Content/...`), never a
pak (paksource). Installing is what packs one into `~mods/<stem>.pak`; packaging packs
them into a zip to share. The folder is otherwise laid out like `~mods` (notes and tab
images beside each build), so customcampaign, navpatch and makemod.previous_guids read
it unchanged.

Rift Lord's own bookkeeping lives in `<bucket>/.riftlord/`: a record per mission
(`records/<stem>.json`: the Map Editor's plan, the onslaught spec, the details) so it
can be reopened for editing, and `installs.json`, what was last installed from where.

A Map Editor mission's record is the mission: saving writes only the record, and its
build is baked from it by Build (`build`), or when the Onslaught Editor opens it.
Install and Package refuse a mission whose build is missing or out of date
(workshop/SAVE-MODEL.md).

    python3 bucket.py --list
"""

import argparse
import io
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.core import buildmessage  # noqa: E402
from riftlord.core import gamepath  # noqa: E402
from riftlord.core import paksource  # noqa: E402

#: True while Rift Lord runs; the editors record their builds only then.
ACTIVE = False
#: Files that sit beside a loose build in the bucket and belong to it: OrcsMustUI's notes
#: and a campaign's banner. Packing puts them inside the pak (makemod.OMUI_DIR).
SIDECARS = ('.CustomMaps.yaml', '.Tab.png', '.Tab.jpg', '.Tab.jpeg', '.Card.png',
            '.CardHovered.png')
#: Rift Lord's bookkeeping inside the bucket. Hidden, so it is not taken for a build.
META = '.riftlord'


def default_root():
    return gamepath.user_data('Missions')


def root():
    """The bucket folder: `OMD3_BUCKET` (for testing), the folder the author chose, or
    `Missions` in Rift Lord's data folder."""
    chosen = os.environ.get('OMD3_BUCKET') or gamepath.load_settings().get('bucket')
    path = chosen or default_root()
    os.makedirs(path, exist_ok=True)
    return path


def _meta(*parts):
    path = os.path.join(root(), META, *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def activate():
    """Send every build to the bucket, and make its maps openable in both editors."""
    global ACTIVE
    ACTIVE = True
    gamepath.TARGET = root()
    refresh()


def refresh():
    """Open every build in the bucket that holds a map, and forget any that has gone."""
    from riftlord.build.navigation import navpatch
    here = os.path.abspath(root())
    for path in navpatch.opened_paks():
        if os.path.dirname(path.rstrip('/\\')) == here and not os.path.exists(path):
            navpatch.close_pak(path, every=True)
    for path in paksource.sources(here):
        if navpatch.is_opened(path):
            continue
        try:
            navpatch.open_pak(path)
        except (Exception, SystemExit):              # noqa: BLE001
            pass                                     # a campaign holds no map
    # the Onslaught Editor caches what it read of each map, and a rebuilt mission
    # would otherwise open as it was before
    editor = sys.modules.get('riftlord.onslaught.gamedata')
    if editor is not None:
        editor._CACHE.clear()


def set_root(folder, move=True):
    """Keep the bucket in `folder` from now on. With `move`, and when `folder` holds no
    missions of its own, the current bucket's contents move there; a folder that
    already holds missions is simply switched to. Returns what was done."""
    from riftlord.build.navigation import navpatch
    folder = os.path.abspath(os.path.expanduser(folder))
    old = os.path.abspath(root())
    if folder == old:
        return {'root': folder, 'moved': 0}
    os.makedirs(folder, exist_ok=True)
    moved = 0
    if move and not paksource.sources(folder):
        for path in navpatch.opened_paks():
            if os.path.dirname(path.rstrip('/\\')) == old:
                navpatch.close_pak(path, every=True)
        for name in os.listdir(old):
            if os.path.exists(os.path.join(folder, name)):
                continue
            shutil.move(os.path.join(old, name), os.path.join(folder, name))
            moved += 1
    gamepath.save_setting('bucket', folder)
    if ACTIVE:
        gamepath.TARGET = folder
        refresh()
    return {'root': folder, 'moved': moved}


#: What a new mission starts with in both editors, until the author changes it there.
#: `hub` is a hub style (customcampaign.HUBS), '' for Automatic (the level's own).
DEFAULTS = {'author': '', 'version': '', 'placement': 'CustomMaps', 'endless': False,
            'hub': 'OldFriends'}


def defaults():
    """The mission defaults the author set in Rift Lord's Settings."""
    saved = gamepath.load_settings().get('defaults') or {}
    out = dict(DEFAULTS)
    out.update({k: saved[k] for k in DEFAULTS if k in saved})
    return out


def set_defaults(values):
    """Keep new mission defaults. Unknown keys are ignored; text is trimmed."""
    out = defaults()
    for key in DEFAULTS:
        if key not in (values or {}):
            continue
        v = values[key]
        out[key] = bool(v) if key == 'endless' else str(v or '').strip()
    out['placement'] = out['placement'] or 'CustomMaps'
    from riftlord.missions import customcampaign
    out['hub'] = customcampaign.hub_name(out['hub'])
    gamepath.save_setting('defaults', out)
    return out


def item_id(path):
    return paksource.stem(path)


def _record_path(ident):
    if not ident or '/' in ident or '\\' in ident or ident.startswith('.'):
        raise SystemExit('no such mission: %r' % ident)
    return _meta('records', ident + '.json')


def _record_files():
    """File names in the records folder."""
    folder = os.path.dirname(_meta('records', 'x'))
    os.makedirs(folder, exist_ok=True)
    return os.listdir(folder)


def load(ident):
    """One mission's record, or None."""
    try:
        with open(_record_path(ident), encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _source(ident):
    """The build a mission id names, or None."""
    for path in paksource.sources(root()):
        if paksource.stem(path) == ident:
            return path
    return None


def item(ident, built=False):
    """A record as an editor reopens it, with the map its build carries filled in and
    the mission's details in its wave spec. With `built`, a Map Editor mission whose
    build is missing or out of date is baked first, so its map can be opened: an editor's
    build, which leaves its light bake to the Missions page (_bake_if_stale)."""
    rec = load(ident)
    if rec is None:
        return None
    if built and is_stale(rec):
        _bake_if_stale(ident, 'opening it in an editor')
        rec = load(ident)
    path = _source(ident)
    if path is None and not _editable(rec):
        return None
    rec['map'] = rec.get('map') or (_map_in(path) if path else None)
    #: a plan saved before the Lighting section, as the Map Editor edits it
    #: (riftlord.build.world.lightmigrate); the record itself changes on the next save
    if _editable(rec) and isinstance(rec.get('plan'), dict):
        from riftlord.build.world import lighting
        rec['plan'] = lighting.migrated(dict(rec['plan'], level=rec['plan'].get('level')
                                             or rec.get('level')))
    #: the editors' Campaign picker shows a mission placed in Endless as Endless
    if _editable(rec):
        rec['placement'] = _placement_view(rec)
    if isinstance(rec.get('spec'), dict) and rec.get('details'):
        rec['spec']['details'] = _merge_details(rec['spec'].get('details'), rec['details'])
    #: a Map Editor mission's listing is its record's, whichever page last changed it
    if isinstance(rec.get('spec'), dict) and _editable(rec):
        rec['spec']['intoCampaign'] = _placement_view(rec)
        rec['spec']['endless'] = bool(rec.get('endless'))
        if rec.get('endlessOnly') or 'endlessOnly' in rec['spec']:
            rec['spec']['endlessOnly'] = bool(rec.get('endlessOnly'))
    # the waves of a level spawner the plan removes are not the mission's any more
    if isinstance(rec.get('spec'), dict) and _editable(rec):
        from riftlord.mapeditor import planactors
        gone = planactors.removed_spawners(rec['plan'],
                                           planactors.level_actors(rec['level']))
        if gone:
            rec['spec'] = planactors.without_spawners(rec['spec'], gone)
    #: the plan's events are the mission's (wavespec.apply_events)
    if _editable(rec) and isinstance(rec['plan'].get('events'), list):
        rec['events'] = rec['plan']['events']
        if isinstance(rec.get('spec'), dict):
            rec['spec']['events'] = rec['plan']['events']
    #: a unit row whose row a mod renamed, under its current name (wavespec.resolve_units)
    if isinstance(rec.get('spec'), dict):
        from riftlord.core import console
        from riftlord.onslaught import wavespec
        rec['spec'] = wavespec.resolve_units(
            rec['spec'], note=lambda line: console.info(line, console.ONSLAUGHT_EDITOR))
        if isinstance(rec.get('events'), list) and isinstance(rec['spec'].get('events'), list):
            rec['events'] = rec['spec']['events']
    #: with the Mission Events piece placed, the events it adds (riftlord/missionevents.py)
    if _editable(rec):
        from riftlord.missions import missionevents
        if missionevents.piece(rec['plan']):
            try:
                from riftlord.missions import hostevents
                got = hostevents.with_piece(hostevents.capabilities(rec['level']),
                                            missionevents.unstable_rifts(rec['plan']))
                rec['eventCaps'] = {k: got[k] for k in ('reactions', 'music', 'rift',
                                                        'dialogue')}
            except (Exception, SystemExit):          # noqa: BLE001
                pass
    #: the Crystal's Rift Tear groups, as the Onslaught Editor's Rift Tear tab edits them
    #: (riftlord/mapeditor/rifttear.py)
    if _editable(rec):
        from riftlord.mapeditor import rifttear
        view = rifttear.editor_view(rec['plan'])
        if view:
            rec['riftTear'] = view
            if isinstance(rec.get('spec'), dict):
                rec['spec']['riftTear'] = view
        #: the mission's game mode, from its plan (riftlord/onslaught/gamemode.py)
        from riftlord.onslaught import gamemode
        rec['mode'] = gamemode.of_plan(rec['plan'])
    #: a wave spec saved before events: its events read out of its waves
    if isinstance(rec.get('spec'), dict) and 'events' not in rec['spec']:
        try:
            from riftlord.onslaught import wavespec
            from riftlord.missions import hostevents
            host = rec.get('level') or rec['spec'].get('map') or rec.get('map')
            rec['spec'], _e = wavespec.split_events(rec['spec'],
                                                    hostevents.capabilities(host))
        except (Exception, SystemExit):              # noqa: BLE001
            pass
    #: the mission's pictures as the Details dialog shows them
    for slot in PICTURES:
        rec[slot + 'View'] = picture_view(rec, slot)
    #: the Scramble tiers it joins, whichever editor keeps them
    from riftlord.missions import missionscramble
    rec['scramble'] = missionscramble.of_record(rec)
    return rec


def target(ident):
    """(build path, mod name) of a mission, so an editor can save into it again --
    the same folder, mod and Guids -- whichever editor made it. A Map Editor mission
    whose build is missing or out of date is baked first, as an editor's build
    (_bake_if_stale). The mod is read off its mission row (`<mod>_Mission01`), which both
    editors name the same way."""
    rec = load(ident)
    if rec is not None and is_stale(rec):
        _bake_if_stale(ident, 'saving into it from an editor')
    path = _source(ident)
    if path is None:
        raise SystemExit('%s is not in Missions' % ident)
    rows = _main_rows(_mission_rows(path))
    if not rows or '_' not in rows[0][0]:
        raise SystemExit('%s has no mission row to save into' % ident)
    return path, rows[0][0].rsplit('_', 1)[0]


# -- editable missions ---------------------------------------------------------------

#: Record fields a bake reads; a change to any of them makes the build out of date.
BAKE_INPUTS = ('level', 'plan', 'spec', 'title', 'mod', 'details', 'placement', 'endless',
               'resetDoors', 'picture', 'portalPicture', 'scramble', 'campaignIndex',
               'endlessOnly', 'unlocks')

#: Image types a mission's pictures may be.
PICTURE_EXTS = ('.png', '.jpg', '.jpeg')
#: Widest preview of a picture sent to the Details dialog, in pixels.
PICTURE_PREVIEW_WIDTH = 1600
#: A mission's pictures: record key -> (width:height it is cropped to, what it is).
PICTURES = {'picture': ((16, 9), 'loading-screen picture'),
            'portalPicture': ((1, 1), 'hub portal picture')}


def _picture_file(rec, slot='picture'):
    """Path of one of the mission's pictures beside its record, or None."""
    pic = rec.get(slot) if isinstance(rec, dict) else None
    if not isinstance(pic, dict) or not pic.get('file'):
        return None
    path = _meta('records', pic['file'])
    return path if os.path.exists(path) else None


def take_picture(rec, value, slot='picture'):
    """Apply one of the Details dialog's pictures (PICTURES) to a record: None removes
    it; {name, data (base64), crop} stores a new image beside the record; {crop}
    re-frames the one it has. Returns True when the record changed."""
    import base64
    import hashlib
    from riftlord.build.world import loadscreen
    from PIL import Image
    aspect, what = PICTURES[slot]
    old = rec.get(slot)
    if value is None:
        if old and _picture_file(rec, slot):
            os.remove(_picture_file(rec, slot))
        rec.pop(slot, None)
        return old is not None
    if not isinstance(value, dict):
        raise SystemExit('the %s is not readable' % what)
    pic = dict(old or {})
    if value.get('data'):
        ext = os.path.splitext(value.get('name') or '')[1].lower()
        if ext not in PICTURE_EXTS:
            raise SystemExit('a %s must be PNG or JPG' % what)
        blob = base64.b64decode(value['data'])
        try:
            with Image.open(io.BytesIO(blob)) as im:
                im.verify()
            with Image.open(io.BytesIO(blob)) as im:
                size = list(im.size)
        except Exception:                            # noqa: BLE001
            raise SystemExit('%s is not a PNG or JPG image' % (value.get('name') or 'the picture'))
        if old and _picture_file(rec, slot):
            os.remove(_picture_file(rec, slot))
        pic = {'file': '%s.%s%s' % (rec['id'], slot, ext), 'name': value.get('name'),
               'sha1': hashlib.sha1(blob).hexdigest(), 'size': size}
        with open(_meta('records', pic['file']), 'wb') as fh:
            fh.write(blob)
    elif not pic:
        raise SystemExit('choose a picture first')
    crop = value.get('crop') or pic.get('crop') or [0, 0, 1, 1]
    box = loadscreen.crop_box(pic['size'], crop, aspect)
    w, h = pic['size']
    pic['crop'] = [round(box[0] / w, 6), round(box[1] / h, 6),
                   round((box[2] - box[0]) / w, 6), round((box[3] - box[1]) / h, 6)]
    rec[slot] = pic
    return pic != old


def picture_view(rec, slot='picture'):
    """One picture as the Details dialog shows it: {name, crop, size, preview (a JPEG
    data URL)}, or None."""
    import base64
    from PIL import Image
    path = _picture_file(rec, slot)
    if path is None:
        return None
    pic = rec[slot]
    with Image.open(path) as im:
        im = im.convert('RGB')
        if im.width > PICTURE_PREVIEW_WIDTH:
            im = im.resize((PICTURE_PREVIEW_WIDTH,
                            round(im.height * PICTURE_PREVIEW_WIDTH / im.width)))
        buf = io.BytesIO()
        im.save(buf, format='JPEG', quality=85)
    return {'name': pic.get('name'), 'crop': pic.get('crop'), 'size': pic.get('size'),
            'preview': 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()}


def load_picture(rec, slot='picture'):
    """{'path', 'crop'} of one picture for a build, or None."""
    path = _picture_file(rec, slot)
    return {'path': path, 'crop': rec[slot]['crop']} if path else None


def take_pictures(ident, pictures):
    """Apply the Details dialog's pictures ({slot: value}, PICTURES; a slot left out is
    unchanged) to a mission's record, making one when it has none yet. Returns
    {slot: {'path', 'crop'} or None} for a build."""
    rec = load(ident) or {'id': ident, 'created': time.time(), 'kind': 'mission',
                          'tools': []}
    changed = False
    for slot in PICTURES:
        if isinstance(pictures, dict) and slot in pictures:
            changed = take_picture(rec, pictures[slot], slot) or changed
    if changed:
        rec['updated'] = time.time()
        _write_json(_record_path(ident), rec)
    return {slot: load_picture(rec, slot) for slot in PICTURES}


#: Detail fields OrcsMustUI shows for a mission; `hub` is its hub style
#: (customcampaign.HUBS), '' for the level's own.
DETAIL_FIELDS = ('author', 'version', 'description', 'hub')


def _editable(rec):
    """True for a record that holds a Map Editor mission's source: a level and a plan."""
    return bool(rec and rec.get('level') and isinstance(rec.get('plan'), dict))


def bake_inputs(rec):
    """A digest of what a bake of `rec` reads."""
    import hashlib
    want = {k: rec.get(k) for k in BAKE_INPUTS}
    #: a mission in no Scramble tier digests as before the field existed
    if want['scramble'] is None:
        del want['scramble']
    #: one listed last in its shipped campaign digests as before the field existed
    if want['campaignIndex'] is None:
        del want['campaignIndex']
    #: one not placed in Endless digests as before the field existed
    if not want['endlessOnly']:
        del want['endlessOnly']
    #: one that grants nothing digests as before the field existed
    if not want['unlocks']:
        del want['unlocks']
    if isinstance(want['spec'], dict):
        want['spec'] = {k: v for k, v in want['spec'].items() if k not in ('details', 'out')}
    if isinstance(want['details'], dict):
        want['details'] = {k: want['details'].get(k) for k in DETAIL_FIELDS}
    #: the old Reference overlay block, which plans saved before 2026-10-17 may hold; a
    #: build never read it (the Trace tool keeps its state outside the plan, riftlord/trace.py)
    if isinstance(want['plan'], dict) and 'reference' in want['plan']:
        want['plan'] = {k: v for k, v in want['plan'].items() if k != 'reference'}
    return hashlib.sha1(json.dumps(want, sort_keys=True, default=str).encode()).hexdigest()


_PAK_DIGESTS = {}


def _pak_digest(path):
    """sha1 of a file, cached by path, size and mtime; None when it cannot be read."""
    import hashlib
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (path, st.st_size, st.st_mtime_ns)
    if key not in _PAK_DIGESTS:
        h = hashlib.sha1()
        with open(path, 'rb') as fh:
            for block in iter(lambda: fh.read(1 << 20), b''):
                h.update(block)
        _PAK_DIGESTS[key] = h.hexdigest()
    return _PAK_DIGESTS[key]


def carried_mods(path, own=None):
    """{mod key: sha1 of its installed pak} for the mods whose minions the build at
    `path` carries."""
    from riftlord.core import paksource
    from riftlord.missions import modminions
    try:
        tables = [n for n in paksource.names(path) if modminions.TABLE_RE.match(n)]
        files = {n: paksource.read(path, n) for t in tables
                 for n in (t, t[:-len('.uasset')] + '.uexp')}
        keys = modminions.carried_folders(files, own)
    except (Exception, SystemExit):                  # noqa: BLE001
        return {}
    installed = modminions.mods()
    return {k: _pak_digest(installed[k]['pak']) for k in sorted(keys)
            if k in installed and installed[k]['own']}


_UNSTAMPED = {}


def carried_changed(rec):
    """Mod keys whose installed pak differs from the one the record's build carried. A
    build from before the `carried` stamp counts every installed mod it carries as
    changed (read once per build)."""
    from riftlord.missions import modminions
    rec = rec or {}
    installed = modminions.mods()
    if 'carried' not in rec:
        path = _source(rec['id']) if rec.get('id') else None
        if path is None:
            return []
        key = (path, rec.get('bakedAt'))
        if key not in _UNSTAMPED:
            _UNSTAMPED[key] = sorted(carried_mods(path, rec.get('mod')))
        return list(_UNSTAMPED[key])
    return sorted(k for k, sha in (rec['carried'] or {}).items()
                  if k in installed and installed[k]['own']
                  and _pak_digest(installed[k]['pak']) != sha)


def _kit_modules():
    """{kit key: module} of the kits Rift Lord builds on this machine and carries into a
    mission: each module has KIT_ROOT, kit() and digest(files)."""
    from riftlord.mapeditor import chandelier
    return {chandelier.MOD_KEY: chandelier}


def carried_kits(path):
    """{kit key: digest of its entries} for the machine-built kits the build at `path`
    carries."""
    from riftlord.core import paksource
    out = {}
    try:
        names = paksource.names(path)
        for key, mod in _kit_modules().items():
            folder = 'OMD/Content' + mod.KIT_ROOT[len('/Game'):] + '/'
            mine = [n for n in names if n.startswith(folder)]
            if mine:
                out[key] = mod.digest({n: paksource.read(path, n) for n in mine})
    except (Exception, SystemExit):                  # noqa: BLE001
        return {}
    return out


#: {kit key: digest of the kit as this machine builds it now}, once per process
_KIT_NOW = {}


def current_kit(key):
    """The digest of kit `key` as built now, or None when it cannot be built (no Setup)."""
    if key not in _KIT_NOW:
        mod = _kit_modules()[key]
        try:
            _KIT_NOW[key] = mod.digest(mod.kit())
        except (Exception, SystemExit):              # noqa: BLE001
            return None
    return _KIT_NOW[key]


_UNSTAMPED_KITS = {}


def kits_changed(rec):
    """Kit keys whose carried copy in the record's build differs from the kit built now. A
    build from before the `kits` stamp that carries a kit counts it as changed (read once
    per build); a kit that cannot be built now changes nothing."""
    rec = rec or {}
    if 'kits' not in rec:
        path = _source(rec['id']) if rec.get('id') else None
        if path is None:
            return []
        key = (path, rec.get('bakedAt'))
        if key not in _UNSTAMPED_KITS:
            _UNSTAMPED_KITS[key] = sorted(carried_kits(path))
        return list(_UNSTAMPED_KITS[key])
    out = []
    for k, sha in sorted((rec['kits'] or {}).items()):
        if k in _kit_modules():
            now = current_kit(k)
            if now is not None and now != sha:
                out.append(k)
    return out


def kit_problems(ident):
    """What differs between the kits a mission's build carries and the kits built now; []
    when they match."""
    path = _source(ident)
    if path is None:
        return ['%s has no build' % ident]
    out = []
    for k, sha in sorted(carried_kits(path).items()):
        now = current_kit(k)
        if now is None:
            out.append('%s cannot be built here to compare (Setup)' % k)
        elif now != sha:
            out.append('%s carried is not the kit built now: rebuild the mission' % k)
    return out


def carried_problems(ident):
    """What differs between a mission's build and the installed mods whose minions it
    carries (modminions.gates); [] when they match."""
    from riftlord.core import paksource
    from riftlord.missions import modminions
    path = _source(ident)
    if path is None:
        return ['%s has no build' % ident]
    return ['%s: %s' % (name, detail) for name, ok, detail
            in modminions.gates(paksource.entries(path)) if not ok]


def is_stale(rec):
    """True when a Map Editor mission's build is missing or older than its record, or a
    mod whose minions it carries has been updated since, or a kit it carries (the
    Chandelier's) is no longer the kit built now. A record from before baking
    existed (no stamp) counts as current while it has a build."""
    if not _editable(rec):
        return False
    if _source(rec['id']) is None:
        return True
    return 'baked' in rec and (rec['baked'] != bake_inputs(rec) or bool(carried_changed(rec))
                               or bool(kits_changed(rec)))


def light_pending(rec):
    """[reason] the light maps of a Map Editor mission's current build are still to bake:
    an editor built it (lightbake.build_as) after a change to its lighting; [] when they
    are baked or nothing needs them."""
    return list(rec.get('lightPending') or []) if _editable(rec) else []


def needs_build(rec):
    """True for a Map Editor mission that Install and Package must not take: never
    built, edited since its last build, its last build failed, or its light maps are
    still to bake (light_pending)."""
    return _editable(rec) and (is_stale(rec) or bool(rec.get('bakeError'))
                               or bool(light_pending(rec)))


def build_state(rec, path):
    """'built', 'unlit' (built, its light maps still to bake: light_pending), 'stale',
    'unbuilt' or 'failed' for a mission's record and build path. A mission without a Map
    Editor record is built: its build is all there is."""
    if not _editable(rec):
        return 'built'
    if rec.get('bakeError'):
        return 'failed'
    if path is None:
        return 'unbuilt'
    if is_stale(rec):
        return 'stale'
    return 'unlit' if light_pending(rec) else 'built'


#: Detail fields a dialog's choice always sets, even to '' (Automatic).
CHOSEN_DETAILS = ('hub',)


def _merge_details(old, new):
    """`old` details with the non-empty fields of `new` over them; a CHOSEN_DETAILS
    field `new` carries applies even when empty."""
    out = dict(old or {})
    for k, v in (new or {}).items():
        if v not in (None, '') or k not in out or (k in CHOSEN_DETAILS and v is not None):
            out[k] = v
    return out


def _mods_in_use():
    """Mod names that records or builds in Missions already use."""
    out = set()
    for name in _record_files():
        if name.endswith('.json'):
            rec = load(name[:-5]) or {}
            if rec.get('mod'):
                out.add(rec['mod'])
    for path in paksource.sources(root()):
        try:
            for inner in paksource.names(path):
                parts = inner.split('/')
                if len(parts) > 3 and parts[2] == 'Mods':
                    out.add(parts[3])
        except (Exception, SystemExit):              # noqa: BLE001
            continue
    return out


def _mission_maps():
    """The maps that Missions' records name as their own built level."""
    out = set()
    for name in _record_files():
        if name.endswith('.json'):
            rec = load(name[:-5]) or {}
            if rec.get('mod') and _editable(rec):
                out.add('%s_Map01' % rec['mod'])
    return out


def _fresh_identity(level, title):
    """(id, mod) for a new mission: named after its title, with a letter suffix
    (leveltitle.letter: `900_North_Wings_B_P`, `RiftLordNorthWingsB`) past any id or mod
    taken in Missions, whatever the case. Two missions may share a title; never an id or
    a mod."""
    from riftlord.build import emit, leveltitle
    stem = emit.pak_stem(title or '%s (Map Editor)' % level)
    base_mod = emit.mod_name(level, title)
    taken_ids = {paksource.stem(p).lower() for p in paksource.sources(root())}
    taken_ids |= {n[:-5].lower() for n in _record_files() if n.endswith('.json')}
    mods = {m.lower() for m in _mods_in_use()}
    n = 1
    while True:
        tail = leveltitle.letter(n)
        ident = '900_%s%s_P' % (stem, '_' + tail if tail else '')
        mod = base_mod + tail
        if ident.lower() not in taken_ids and mod.lower() not in mods:
            return ident, mod
        n += 1


def _shipped_index_of(rec):
    """The Campaign Index a mission keeps for its shipped campaign, or None."""
    spec = rec.get('spec') if isinstance(rec.get('spec'), dict) else {}
    return rec.get('campaignIndex') or spec.get('campaignIndex') or None


def level_name(rec, path=None):
    """The name a mission's own level goes by in the game: its Working Title, else
    `<mod>_Map01` (leveltitle; a mission made before Working Titles keeps its level's
    name); None for a mission that ships no level of its own."""
    from riftlord.build import leveltitle
    rec = rec or {}
    if not rec.get('mod'):
        return None
    own = leveltitle.default(rec['mod'])
    path = path or _source(rec.get('id') or '')
    if not _editable(rec) and (path is None or _map_in(path) != own):
        return None
    return rec.get('workingTitle') or own


def level_names(skip=None):
    """{lower-case level name: mission title} of every mission in Missions but `skip`."""
    out = {}
    for name in _record_files():
        if not name.endswith('.json') or name[:-5] == skip:
            continue
        rec = load(name[:-5]) or {}
        for level in {rec.get('workingTitle'), '%s_Map01' % rec['mod'] if rec.get('mod')
                      else None} - {None, ''}:
            out.setdefault(level.lower(), rec.get('title') or name[:-5])
    return out


def fresh_working_title(title, skip=None):
    """A new Working Title for a mission titled `title`: `RL_Scen_<ScenarioName>`, past
    the levels of every mission but `skip` and the shipped names (leveltitle.fresh)."""
    from riftlord.build import leveltitle
    return leveltitle.fresh(title, taken=level_names(skip=skip))


def working_title_view(rec, path=None):
    """The Settings dialog's Working Title field: {value (the level's name now), default
    (what an empty field saves: fresh_working_title), max, taken (lower-case level names of
    the other missions), shipped (lower-case shipped level and mission names)}, or None for
    a mission with no level of its own."""
    from riftlord.build import leveltitle
    name = level_name(rec, path)
    if name is None:
        return None
    return {'value': name, 'default': fresh_working_title(rec.get('title'), rec.get('id')),
            'max': leveltitle.MAX_LENGTH, 'taken': level_names(skip=rec.get('id')),
            'shipped': leveltitle.shipped_names()}


def _working_title(rec, text):
    """Give a record the Working Title `text` (leveltitle.problems); '' gives it a new one
    (fresh_working_title). The name its level has now changes nothing; `<mod>_Map01` is
    kept as no Working Title, which is what a mission made before them has. Its next
    Install or Package names the level by it. Returns the level's name."""
    from riftlord.build import leveltitle
    text = str(text or '').strip()
    now = level_name(rec)
    if now is None:
        if text:
            raise SystemExit('%s has no level to give a Working Title'
                             % (rec.get('title') or rec.get('id')))
        return ''
    if text == now:
        return now
    if not text:
        text = fresh_working_title(rec.get('title'), rec.get('id'))
    if text == leveltitle.default(rec['mod']):
        rec.pop('workingTitle', None)
        return text
    bad = leveltitle.problems(text, taken=level_names(skip=rec.get('id')))
    if bad:
        raise SystemExit(bad[0])
    rec['workingTitle'] = text
    return text


def _endless_variant(rec, on):
    """Place a record in Endless (`on`) or not: listed in Endless alone, its own row
    playing there, with no twin and in no campaign (makemod.build `endless_only`). Only a
    Map Editor mission's build reads it. True when it changed."""
    was = bool(rec.get('endlessOnly'))
    if on and not _editable(rec):
        raise SystemExit('Only a mission made in the Map Editor can be placed in Endless alone.')
    if on:
        rec['endlessOnly'] = True
    else:
        rec.pop('endlessOnly', None)
    return bool(on) != was


def _new_guids(mod):
    """{row: Guid hex} for a new mission's row and its Endless twin."""
    from riftlord.build import makemod
    return {'%s_Mission01' % mod: os.urandom(16).hex(),
            '%s%s' % (mod, makemod.ENDLESS_TWIN): os.urandom(16).hex()}


def _main_guid(rec):
    """The mission row's 16-byte Guid from a record, or None."""
    for row, g in (rec.get('guids') or {}).items():
        if not row.endswith(_twin()):
            return bytes.fromhex(g)[:16]
    return None


def spell_guid(raw):
    """16 file-order Guid bytes as UE spells them (Conv_GuidToString): four %08X uint32s."""
    import struct
    return ''.join('%08X' % v for v in struct.unpack('<4I', bytes(raw)[:16]))


def parse_guid(text):
    """16 file-order bytes from a Guid in UE's spelling; dashes, braces and spaces are
    ignored. Raises SystemExit for anything else."""
    import struct
    clean = ''.join(c for c in str(text or '') if c not in '-{}() \t').upper()
    if len(clean) != 32 or any(c not in '0123456789ABCDEF' for c in clean):
        raise SystemExit('a Guid is 32 hex digits, as the game writes it '
                         '(for example 7B0D5FA546BD7612...)')
    if not int(clean, 16):
        raise SystemExit('a Guid cannot be all zeros')
    return struct.pack('<4I', *(int(clean[i:i + 8], 16) for i in range(0, 32, 8)))


def new_guid():
    """A fresh random Guid in UE's spelling."""
    return spell_guid(os.urandom(16))


def guid_spelled(ident):
    """A mission's own Guid in UE's spelling, or ''. The record wins over its build."""
    rec = load(ident) or {}
    guid = _main_guid(rec)
    if guid is None:
        path = _source(ident)
        rows = _main_rows(_mission_rows(path)) if path else []
        guid = bytes.fromhex(rows[0][2]) if rows and rows[0][2] else None
    return spell_guid(guid) if guid else ''


def guid_clashes(text, ident=None):
    """Who else uses a Guid: {'shipped': [titles], 'missions': [titles]}. `ident` is the
    mission asking, left out of the answer."""
    guid = parse_guid(text)
    out = {'shipped': [], 'missions': []}
    try:
        from riftlord.mapeditor import mapinfo
        spelled = spell_guid(guid)
        out['shipped'] = [t or r for r, _lv, t, g, _ws in mapinfo._shipped_rows()
                          if g == spelled]
    except (Exception, SystemExit):                  # noqa: BLE001
        pass
    want = guid.hex()
    built = {paksource.stem(p): p for p in paksource.sources(root())}
    names = set(built) | {n[:-5] for n in _record_files() if n.endswith('.json')}
    for other in sorted(names - {ident}):
        rec = load(other) or {}
        mine = {g[:32] for g in (rec.get('guids') or {}).values()}
        if not mine and other in built:
            mine = {g for _r, _t, g in _mission_rows(built[other])}
        if want in mine:
            out['missions'].append(rec.get('title') or other)
    return out


def guid_check(text, ident=None):
    """{ok, error, guid, shipped, missions}: whether a Guid is spelled right and who else
    uses it (the Details dialog's Guid field)."""
    try:
        got = guid_clashes(text, ident or None) if ACTIVE else {'shipped': [], 'missions': []}
        got['guid'] = spell_guid(parse_guid(text))
        return dict(got, ok=not got['shipped'], error='')
    except SystemExit as exc:
        return {'ok': False, 'error': str(exc), 'shipped': [], 'missions': []}


def set_guid(ident, text):
    """Give a mission's own row a Guid (UE spelling); the record keeps it, so every
    rebuild writes it. Refuses a shipped mission's Guid. A custom campaign listing the
    old Guid lists the new one instead, and the mission is out of date until built.
    Returns {'guid', 'missions': [other missions using it], 'changed'}."""
    from riftlord.missions import customcampaign
    guid = parse_guid(text)
    clash = guid_clashes(text, ident)
    if clash['shipped']:
        raise SystemExit('that Guid is the shipped mission %s' % ', '.join(clash['shipped']))
    path = _source(ident)
    rec = _adopt(ident) if (load(ident) or path) else None
    if rec is None:
        raise SystemExit('%s is not in Missions' % ident)
    if not rec.get('guids'):
        if not rec.get('mod'):
            raise SystemExit('%s has no mission row to give a Guid' % ident)
        rec['guids'] = _new_guids(rec['mod'])
    old = _main_guid(rec)
    row = next((r for r in rec['guids'] if not r.endswith(_twin())), None)
    out = {'guid': spell_guid(guid), 'missions': clash['missions'], 'changed': old != guid}
    if not out['changed']:
        return out
    rec['guids'][row] = guid.hex()
    placement = rec.get('placement') or ''
    if old is not None and placement.startswith(customcampaign.CUSTOM):
        _place_now(rec)                              # new Guid first: the pak never empties
        customcampaign.place(old, None, mods=root())
    rec['baked'] = ''
    rec['updated'] = time.time()
    _write_json(_record_path(rec['id']), rec)
    refresh()
    return out


def shipped_index(placement, index):
    """A Campaign Index for shipped campaign `placement` checked
    (customcampaign.index_problem: SHIPPED_FIRST to its list's length + 1), or None for
    none (listed last)."""
    from riftlord.missions import customcampaign
    from riftlord.build import makemod
    if index in (None, ''):
        return None
    size = len(makemod.campaign_order(placement)) + 1
    return customcampaign.campaign_index(index, size, customcampaign.SHIPPED_FIRST)


def _keep_shipped_index(rec, index):
    """Keep a Campaign Index in a record whose placement is a shipped campaign (checked;
    None lists it last); any other placement keeps none. True when it changed."""
    from riftlord.missions import customcampaign
    was = rec.get('campaignIndex')
    if (rec.get('placement') or '') in customcampaign.SHIPPED and index not in (None, ''):
        rec['campaignIndex'] = shipped_index(rec['placement'], index)
    else:
        rec.pop('campaignIndex', None)
    return rec.get('campaignIndex') != was


def _place_now(rec, details=None):
    """List a record's mission in its custom campaign (or in none) right away, since a
    campaign names its missions by Guid; details' `campaignIndex` is its place there
    (customcampaign.place `index`). A shipped campaign is part of the build, so it
    waits for the bake, and its Campaign Index is kept in the record. Returns the
    campaign's title, or None."""
    from riftlord.missions import customcampaign
    from riftlord.build import makemod
    if rec.get('endlessOnly'):
        return _place_variant(rec)
    placement = rec.get('placement') or makemod.CUSTOM_MAPS
    index = (details or {}).get('campaignIndex')
    if placement in customcampaign.SHIPPED:
        if index not in (None, ''):
            _keep_shipped_index(rec, index)
        return None
    rec.pop('campaignIndex', None)
    guid = _main_guid(rec)
    if guid is None:
        return None
    _into, choice = customcampaign.resolve(placement, details)
    if isinstance(choice, dict) and not (choice.get('name') or '').strip():
        raise SystemExit('name the new campaign')
    placed = customcampaign.place(guid, choice, mods=root(), index=index)
    if placed.get('campaign_pak'):
        rec['placement'] = customcampaign.CUSTOM + '%s:%s' % (
            os.path.basename(placed['campaign_pak']), placed['campaign_row'])
    elif choice is None:
        rec['placement'] = makemod.CUSTOM_MAPS
    #: a campaign's own fields live with the campaign once it exists
    if rec['placement'].startswith(customcampaign.CUSTOM) and isinstance(rec.get('details'), dict):
        rec['details'].pop('newCampaign', None)
    return placed.get('campaign')


def _place_variant(rec):
    """A mission placed in Endless is listed there alone: its Guid is taken out of every
    campaign's list and it ships as a pak of its own (Custom Maps placement in the record,
    `endlessOnly` set). Returns None, the campaign it is in."""
    rec.pop('campaignIndex', None)
    guid = _main_guid(rec)
    if guid is not None:
        from riftlord.missions import customcampaign
        customcampaign.place(guid, None, mods=root())
    rec['placement'] = makemod_custom()
    return None


def _placement_view(rec):
    """A record's placement as the pickers show it: customcampaign.ENDLESS for a mission
    placed in Endless, else the record's."""
    from riftlord.missions import customcampaign
    return customcampaign.ENDLESS if (rec or {}).get('endlessOnly') else \
        ((rec or {}).get('placement') or makemod_custom())


def _adopt(ident):
    """A record for a mission whose build exists without one, or whose record predates
    editable missions: with its mod and Guids read from the build."""
    from riftlord.build import makemod
    rec = load(ident) or {'id': ident, 'created': time.time(), 'kind': 'mission', 'tools': []}
    path = _source(ident)
    if path is not None:
        if not rec.get('mod'):
            rows = _main_rows(_mission_rows(path))
            rec['mod'] = rows[0][0].rsplit('_', 1)[0] if rows and '_' in rows[0][0] else None
        if not rec.get('guids'):
            rec['guids'] = {r: g[:16].hex() for r, g in makemod.previous_guids(path).items()}
        if _editable(rec) and 'baked' not in rec:
            rec['baked'] = bake_inputs(rec)
    return rec


def save_map(level, plan, title=None, details=None, placement=None, mission=None):
    """Keep a Map Editor mission as its record, the editable form: the level it is built
    on, the plan, its details and placement. Nothing is cooked; Build on the Missions page
    and the Onslaught Editor bake it. `mission` is the id of the mission being
    edited, or None for a new one. Returns {mission, title, map, placement, campaign,
    stale}."""
    from riftlord.build import makemod
    if not ACTIVE:
        raise SystemExit('Missions are only kept while Rift Lord runs')
    if mission:
        if load(mission) is None and _source(mission) is None:
            raise SystemExit('%s is not in Missions' % mission)
        rec = _adopt(mission)
        if not rec.get('mod'):
            raise SystemExit('%s has no mission row to save into' % mission)
    else:
        ident, mod = _fresh_identity(level, title)
        rec = {'id': ident, 'created': time.time(), 'tools': [], 'mod': mod,
               'guids': _new_guids(mod)}
    if level == '%s_Map01' % rec['mod'] or level in _mission_maps():
        raise SystemExit('%s is a level Rift Lord built for a mission. Open that mission '
                         'from Custom Missions to change it.' % level)
    details = details or {}
    rec['level'] = level
    rec['plan'] = plan
    rec['kind'] = 'map'
    rec['tools'] = sorted(set(rec.get('tools', [])) | {'riftlord'})
    rec['map'] = '%s_Map01' % rec['mod']
    rec['title'] = title or rec.get('title') or '%s (Map Editor)' % level
    rec['details'] = _merge_details(rec.get('details'), without_files(
        {k: v for k, v in details.items() if k != 'scramble'}))
    for slot in PICTURES:
        if slot in details:
            take_picture(rec, details[slot], slot)
    rec['endless'] = bool(details.get('endless'))
    #: Campaign Endless lists the mission in the Endless hub alone, in no campaign
    #: (_endless_variant, _place_variant)
    from riftlord.missions import customcampaign
    if placement == customcampaign.ENDLESS or 'endlessOnly' in details:
        _endless_variant(rec, placement == customcampaign.ENDLESS
                         or bool(details.get('endlessOnly')))
    if rec.get('endlessOnly'):
        placement = makemod.CUSTOM_MAPS
    rec['resetDoors'] = bool(details.get('resetDoors'))
    #: the Scramble tiers it joins (missionscramble.clean)
    if 'scramble' in details:
        from riftlord.missions import missionscramble
        rec['scramble'] = missionscramble.clean(details.get('scramble'))
    #: a Campaign Index belongs to the campaign it was set for
    if (placement or makemod.CUSTOM_MAPS) != rec.get('placement'):
        rec.pop('campaignIndex', None)
    rec['placement'] = placement or makemod.CUSTOM_MAPS
    if not rec.get('guids'):
        rec['guids'] = _new_guids(rec['mod'])
    #: a new mission's level gets a name of its own (fresh_working_title)
    if not mission:
        rec['workingTitle'] = fresh_working_title(rec['title'], rec['id'])
    # a build made before this record held a plan has none of it
    rec.setdefault('baked', '')
    campaign = _place_now(rec, details)
    rec['updated'] = time.time()
    _write_json(_record_path(rec['id']), rec)
    return {'mission': rec['id'], 'title': rec['title'], 'map': rec['map'],
            'placement': rec['placement'], 'campaign': campaign, 'stale': is_stale(rec)}


#: One bake at a time, whichever page asked for it.
_BAKE_LOCK = threading.RLock()
#: {id: 'queued' | 'building'} for missions a build is working through.
_PROGRESS = {}
#: {id: the stage a building mission is in} ('Lightmap', 'Level', 'Navmesh', 'Waves'...),
#: announced through buildstage
_STAGE = {}
#: The Build job the Missions page started: {'running', 'ids', 'built', 'failed',
#: 'skipped', 'started', 'finished'}, or None before the first.
_JOB = None
_JOB_LOCK = threading.Lock()


#: The console's name for a build of the Missions page (Build, Install, Package) and the
#: command line: one that bakes the light maps (lightbake.build_as).
MISSIONS_BUILD = 'Missions build'


def bake(ident, note=None, editor=None):
    """Build a Map Editor mission's cooked files from its record, into its folder in
    Missions: the level from the plan, then the record's waves onto that level. The
    record is left as it is, apart from its bake stamp. `editor` names an editor's build
    ('opening it in an editor'), which skips the light bake and notes it as pending
    (light_pending) for a later build to do. Returns the build path."""
    from riftlord.core import console
    from riftlord.build.lighting import lightbake
    kind = 'editor build (%s)' % editor if editor else MISSIONS_BUILD
    with _BAKE_LOCK, console.working_as(console.MAP_COOKER), \
            lightbake.build_as(kind, bake=not editor):
        _PROGRESS[ident] = 'building'
        from riftlord.core import buildstage
        try:
            with buildstage.listen(lambda name: _STAGE.__setitem__(ident, name)):
                return _bake(ident, note or console.notes(console.MAP_COOKER))
        except (Exception, SystemExit) as exc:       # noqa: BLE001
            shown, rows = _say_failure(exc, ident, (load(ident) or {}).get('title') or ident)
            _note_failure(ident, shown, rows)
            raise _failed(exc, shown, rows) from exc
        finally:
            _PROGRESS.pop(ident, None)
            _STAGE.pop(ident, None)


def _failed(exc, shown, rows):
    """What a failed build raises on: a Fault for Rift Lord's own (C), else a Refusal with
    its Checks rows (B)."""
    if buildmessage.is_fault(exc):
        return buildmessage.Fault(shown, buildmessage.technical(exc))
    return buildmessage.Refusal(shown, buildmessage.technical(exc), rows)


def reports_dir():
    """Where a failed build's report file goes (buildmessage.save_report)."""
    return os.path.join(root(), '.riftlord', 'reports')


def _bake_if_stale(ident, editor):
    """Bake a mission unless it is current by the time no other bake is running: an
    editor's build (`editor` says what for), without its light bake."""
    with _BAKE_LOCK:
        if is_stale(load(ident)):
            bake(ident, editor=editor)


def build_skip(rec):
    """Why a mission cannot be built from what Missions keeps of it, or None. A Map
    Editor mission builds from its record, an Onslaught Editor mission from its saved
    wave spec."""
    if _editable(rec):
        return None
    spec = (rec or {}).get('spec')
    if not isinstance(spec, dict) or not spec.get('waves'):
        return 'no saved record or waves to build from; open it in an editor and save it'
    if spec.get('bundle'):
        return 'its waves were saved into another mission\'s build; rebuild that one'
    if not (spec.get('map') or rec.get('map')):
        return 'its saved waves name no map'
    return None


def rebuild(ident, note=None):
    """Build a mission again from what Missions keeps of it: a Map Editor mission's
    record (`bake`), or an Onslaught Editor mission's wave spec, into the same folder,
    mod and Guids. Returns the build path."""
    rec = load(ident)
    if _editable(rec):
        return bake(ident, note)
    why = build_skip(rec)
    if why:
        raise buildmessage.Refusal('%s was not built: %s.' % (rec.get('title') or ident, why)
                                   if rec else '%s is not in Missions.' % ident)
    from riftlord.core import console
    from riftlord.onslaught import buildjob
    with _BAKE_LOCK, console.working_as(console.MAP_COOKER):
        _PROGRESS[ident] = 'building'
        from riftlord.core import buildstage
        try:
            with buildstage.listen(lambda name: _STAGE.__setitem__(ident, name)):
                got = buildjob.resave(ident, note or console.notes(console.MAP_COOKER))
        except (Exception, SystemExit) as exc:       # noqa: BLE001
            shown, rows = _say_failure(exc, ident, rec.get('title') or ident)
            raise _failed(exc, shown, rows) from exc
        finally:
            _PROGRESS.pop(ident, None)
            _STAGE.pop(ident, None)
    return got.get('pak')


def build(ids):
    """Build or rebuild each listed mission from its saved record or wave spec, one
    after another (`rebuild`); one with neither is skipped with its reason. Returns
    {'built', 'failed': [{id, title, error}], 'skipped': [{id, title, reason}]}."""
    from riftlord.core import console
    out = {'built': [], 'failed': [], 'skipped': []}
    todo = []
    for ident in ids:
        rec = load(ident)
        why = build_skip(rec)
        if why:
            path = _source(ident)
            rows = _main_rows(_mission_rows(path)) if path else []
            title = (rec or {}).get('title') or (rows[0][1] if rows else ident)
            out['skipped'].append({'id': ident, 'reason': why, 'title': title})
        else:
            todo.append(ident)
    for ident in todo:
        _PROGRESS.setdefault(ident, 'queued')
    try:
        for ident in todo:
            title = (load(ident) or {}).get('title') or ident
            try:
                rebuild(ident)
                out['built'].append(ident)
            except (Exception, SystemExit) as exc:   # noqa: BLE001
                out['failed'].append({'id': ident, 'title': title,
                                      'error': buildmessage.explain(exc)[0]})
    finally:
        for ident in todo:
            _PROGRESS.pop(ident, None)
    if todo:
        console.info('build finished: %d built, %d failed'
                     % (len(out['built']), len(out['failed'])), console.MAP_COOKER)
    return out


def start_build(ids):
    """Run `build(ids)` in a worker thread. Refused while one is running. Returns the
    job as `build_status` reports it."""
    global _JOB
    with _JOB_LOCK:
        if _JOB and _JOB['running']:
            raise SystemExit('A build is already running; wait for it to finish.')
        _JOB = {'running': True, 'ids': list(ids), 'built': [], 'failed': [],
                'skipped': [], 'started': time.time(), 'finished': None}
        for ident in ids:
            if not build_skip(load(ident)):
                _PROGRESS.setdefault(ident, 'queued')
    threading.Thread(target=_run_build, args=(list(ids),), daemon=True).start()
    return build_status()


def _run_build(ids):
    from riftlord.core import console
    console.thread_source(console.MAP_COOKER)
    try:
        got = build(ids)
    except (Exception, SystemExit) as exc:           # noqa: BLE001
        shown, detail = buildmessage.explain(exc)
        got = {'built': [], 'skipped': [], 'failed': [
            {'id': '', 'title': 'Build', 'error': shown}]}
        if detail:
            console.debug('build stopped: %s' % detail, console.MAP_COOKER)
        console.error('The build stopped: %s' % shown, console.MAP_COOKER)
    with _JOB_LOCK:
        _JOB.update(got, running=False, finished=time.time())


def build_status():
    """{'job': the last Build job or None, 'progress': {id: 'queued' | 'building'},
    'stages': {id: stage}}. Progress covers every bake, including those the Onslaught
    Editor asks for."""
    with _JOB_LOCK:
        job = dict(_JOB) if _JOB else None
    return {'job': job, 'progress': dict(_PROGRESS), 'stages': dict(_STAGE)}


def _bake(ident, note):
    from riftlord.build import emit
    from riftlord.core import console
    from riftlord.build import makemod
    rec = load(ident)
    if not _editable(rec):
        path = _source(ident)
        if path is None:
            raise SystemExit('%s is not in Missions' % ident)
        return path
    title = rec.get('title') or ident
    out = os.path.join(root(), ident)
    console.info('building %s' % title, console.MAP_COOKER)
    guids = {r: bytes.fromhex(g) for r, g in (rec.get('guids') or {}).items()} \
        or makemod.previous_guids(out)
    spec = rec.get('spec') if isinstance(rec.get('spec'), dict) else None
    # a level spawner the plan removes takes its waves with it (riftlord.planactors)
    from riftlord.mapeditor import planactors
    gone = planactors.removed_spawners(rec['plan'], planactors.level_actors(rec['level']))
    if gone:
        spec = planactors.without_spawners(spec or planactors.level_spec(rec['level']),
                                           gone)
    #: the mission's events (wavespec.apply_events) are the plan's when it holds them
    if isinstance(rec['plan'].get('events'), list):
        spec = dict(spec or planactors.level_spec(rec['level']),
                    events=rec['plan']['events'])
    #: an unstable rift spawned through while closed stalls its wave (riftlord.missionevents)
    from riftlord.missions import missionevents
    stuck = (missionevents.rift_problems(rec['plan'],
                                         spec or planactors.level_spec(rec['level']))
             if missionevents.unstable_rifts(rec['plan']) else [])
    #: the Crystal: one, its rifts in the level with their units, no wave through them
    #: (riftlord.mapeditor.crystal)
    from riftlord.mapeditor import crystal
    stuck += crystal.problems(rec['plan'], spec if crystal.piece(rec['plan']) is None
                              else spec or planactors.level_spec(rec['level']))
    #: a chest, potion or Crystal whose supported mod is not detected (riftlord.core.supportedmods)
    from riftlord.core import supportedmods
    stuck += supportedmods.build_problems(rec['plan'])
    #: a mod pak the mission depends on that spells one package two ways (riftlord.core.pakspelling)
    from riftlord.core import pakspelling
    stuck += pakspelling.plan_problems(rec['plan'])
    if stuck:
        from riftlord.mapeditor import checks
        level = planactors.level_actors(rec['level'])
        raise buildmessage.Refusal(
            '%s could not be built: %s. %s' % (title, '; '.join(stuck), buildmessage.CHECKS),
            checks=[checks.row('error', text, rec['plan'], level) for text in stuck])
    details = dict(rec.get('details') or {}, endless=bool(rec.get('endless')),
                   endlessOnly=bool(rec.get('endlessOnly')))
    plan, fallbacks = rec['plan'], []
    report = _write_level(rec, out, title, details, guids, plan)
    while isinstance(report, buildmessage.LeaveOut):
        #: an added piece Rift Lord could not link: built again without it (C)
        left = report
        console.debug('%s: %s' % (title, left.detail), console.MAP_COOKER)
        plan = dict(plan, added=[a for n, a in enumerate(plan.get('added') or [])
                                 if n not in left.items])
        fallbacks.append('Rift Lord could not link %s into the level, so the mission is '
                         'built without %s.' % (', '.join(left.names),
                                                'it' if len(left.names) == 1 else 'them'))
        report = _write_level(rec, out, title, details, guids, plan)
    _refuse_unwritten(report, title, plan)
    #: the light maps this build left to a Missions build (lightbake.build_as)
    from riftlord.build.lighting import lightbake
    unlit = lightbake.pending()
    #: NavPower's vertex-fan walk closes everywhere on the written level's walkable graph;
    #: a fan that never closes freezes the game (riftlord.build.navigation.navfan). A
    #: rebuilt floor that fails it is built again on the level's shipped floor (C).
    from riftlord.build.navigation import navfan
    from riftlord.build.navigation import navregen
    bad = navfan.build_problems(out)
    if bad and not plan.get(emit.SHIPPED_FLOOR) and navregen.wanted(rec['level'], plan):
        console.debug('%s: navfan: %s' % (title, '; '.join(bad[:8])), console.MAP_COOKER)
        plan = dict(plan, **{emit.SHIPPED_FLOOR: True})
        report = _write_level(rec, out, title, details, guids, plan)
        _refuse_unwritten(report, title, plan)
        bad = navfan.build_problems(out)
    if bad:
        raise buildmessage.fault('build a floor enemies can walk without freezing the game',
                                 '; '.join(bad[:8]))
    report['warnings'] = fallbacks + [w for w in report.get('warnings') or []
                                      if w not in fallbacks]
    #: each unstable rift stands over walkable floor of the written level, nothing solid
    #: between; one higher over its floor than any shipped rift is a warning
    #: (riftlord.build.navigation.riftfloor)
    if missionevents.unstable_rifts(rec['plan']):
        from riftlord.build.navigation import riftfloor
        bad, high = riftfloor.build_report(out, rec['plan'], rec['level'])
        if bad:
            from riftlord.mapeditor import checks
            level = planactors.level_actors(rec['level'])
            raise buildmessage.Refusal(
                '%s could not be built: %s %s' % (title, ' '.join(bad), buildmessage.CHECKS),
                checks=[checks.row('error', text, rec['plan'], level) for text in bad])
        for line in high:
            console.warn('%s: %s' % (title, line), console.MAP_COOKER)
    #: a ground spawner standing higher over its floor than any shipped one: a warning
    #: (riftlord.build.navigation.riftfloor.spawner_warnings)
    try:
        from riftlord.build.navigation import riftfloor
        for line in riftfloor.spawner_warnings(out, rec['plan']):
            console.warn('%s: %s' % (title, line), console.MAP_COOKER)
    except Exception as exc:                         # noqa: BLE001
        console.debug('%s: ground spawner heights not checked: %s' % (title, exc),
                      console.MAP_COOKER)
    #: an added spawner facing away from its route's first point: a warning
    #: (riftlord.mapeditor.spawnerfacing.warnings)
    try:
        from riftlord.mapeditor import spawnerfacing
        for line in spawnerfacing.warnings(out, rec['plan']):
            console.warn('%s: %s' % (title, line), console.MAP_COOKER)
    except Exception as exc:                         # noqa: BLE001
        console.debug('%s: spawner facings not checked: %s' % (title, exc), console.MAP_COOKER)
    for line in report.get('not_applied') or []:
        console.warn('%s: %s' % (title, line), console.MAP_COOKER)
    for line in (report.get('fab') or {}).get('notes') or []:
        console.warn('%s: %s' % (title, line), console.MAP_COOKER)
    for line in report.get('warnings') or []:
        console.warn('%s: %s' % (title, line), console.MAP_COOKER)
    for line in report.get('fixed') or []:
        console.info('%s: %s' % (title, line), console.MAP_COOKER)
    from riftlord.missions import missionscramble, missionunlocks
    for warn, line in missionscramble.describe(report) + missionunlocks.describe(report):
        (console.warn if warn else console.info)('%s: %s' % (title, line), console.MAP_COOKER)
    applied = report.get('applied') or {}
    dropped = applied.get('preload_dropped') or []
    console.debug('%s: %d placed actor(s) preloaded by the level; %d donor preload '
                  'dependencies dropped%s' % (
                     title, applied.get('level_preloads_added') or 0, len(dropped),
                     (' (%s)' % ', '.join(sorted({str(n) for _i, n in dropped})))
                     if dropped else ''), console.MAP_COOKER)
    refresh()
    reset = bool(rec.get('resetDoors'))
    if spec and spec.get('waves'):
        from riftlord.onslaught import wavespec
        from riftlord.core import buildstage
        buildstage.stage('Waves')
        try:
            wavespec.bundle(dict(spec, map='%s_Map01' % report['mod'], bundle=True,
                                 out=out, resetDoors=reset), note=note)
        except (Exception, SystemExit) as exc:       # noqa: BLE001
            if buildmessage.is_fault(exc):
                raise (exc if isinstance(exc, buildmessage.Fault) else
                       buildmessage.internal('write the waves',
                                             buildmessage.technical(exc))) from exc
            raise buildmessage.Refusal(
                '%s could not be built: its waves do not fit the level: %s. Open it in the '
                'Onslaught Editor to change them.' % (title, buildmessage.explain(exc)[0])) \
                from exc
    elif reset:
        from riftlord.onslaught import wavespec
        try:
            wavespec.reset_doors_bundle('%s_Map01' % report['mod'], out, note)
        except (Exception, SystemExit) as exc:       # noqa: BLE001
            #: the doors behave as the level ships them (C)
            console.debug('%s: reset doors: %s' % (title, buildmessage.technical(exc)),
                          console.MAP_COOKER)
            console.warn('%s: Rift Lord could not apply Reset door after Onslaught Loop to '
                         'this level, so its doors behave as the level ships them.' % title,
                         console.MAP_COOKER)
    rec = load(ident)
    #: what the build fixed that the designer may change, kept in the plan (buildmessage A)
    take_plan_fixes(rec, report.get('planFixes') or [])
    rec.pop('buildChecks', None)
    rec['guids'] = {r: g[:16].hex() for r, g in makemod.previous_guids(out).items()}
    rec['placement'] = report.get('placement') or rec.get('placement')
    rec['map'] = '%s_Map01' % report['mod']
    rec.pop('bakeError', None)
    if unlit:
        rec['lightPending'] = unlit
        console.info('%s: built without its light bake (%s); Build, Install or Package '
                     'bakes it' % (title, ', '.join(unlit)), console.MAP_COOKER)
    else:
        rec.pop('lightPending', None)
    rec['baked'] = bake_inputs(rec)
    rec['bakedAt'] = time.time()
    rec['carried'] = carried_mods(out, report['mod'])
    rec['kits'] = carried_kits(out)
    _write_json(_record_path(ident), rec)
    refresh()
    console.info('built %s' % title, console.MAP_COOKER)
    return out


def _write_level(rec, out, title, details, guids, plan):
    """emit.write for a record with a copy of `plan` (a build writes fixes into the plan it
    is given); a LeaveOut it raises is returned instead."""
    import copy
    from riftlord.build import emit
    from riftlord.missions import missionunlocks
    try:
        return emit.write(rec['level'], out=out, mod=rec.get('mod'), title=title,
                          placement=rec.get('placement') or None, details=details,
                          plan=copy.deepcopy(plan), guids=guids, fresh_waves=True,
                          load_picture=load_picture(rec),
                          portal_picture=load_picture(rec, 'portalPicture'),
                          scramble=rec.get('scramble'),
                          #: the heroes and items beating it grants (missionunlocks)
                          unlocks=missionunlocks.of_record(rec),
                          #: its place in a shipped campaign (_keep_shipped_index)
                          into_index=rec.get('campaignIndex'))
    except buildmessage.LeaveOut as exc:
        if not exc.items:
            raise
        return exc


def _refuse_unwritten(report, title, plan):
    """Raise for a report emit.write did not write: a Fault for Rift Lord's own (C), else a
    Refusal naming what to change, with its Checks rows (B)."""
    from riftlord.core import console
    if report.get('written'):
        return
    for line in report.get('checks_detail') or []:
        console.debug('%s: failed check: %s' % (title, line), console.MAP_COOKER)
    detail = '; '.join(report.get('checks_detail') or [])
    if report.get('faults') or not report.get('checks_failed'):
        raise buildmessage.Fault(' '.join(report.get('faults') or [])
                                 or 'Rift Lord could not build this mission.', detail)
    raise buildmessage.Refusal(
        '%s could not be built: %s %s' % (title, ' '.join(report['checks_failed']),
                                          buildmessage.CHECKS),
        detail, report.get('checks') or [])


def take_plan_fixes(rec, fixes):
    """Store what a build fixed into the record's plan, where the designer can change it:
    [{'kind': 'point' | 'spawner', 'id', field: value}] (emit.snap_point, planactors.emit)."""
    plan = rec.get('plan') or {}
    for fix in fixes:
        key = {'point': 'points', 'spawner': 'spawners'}.get(fix.get('kind'))
        for item in plan.get(key) or []:
            if isinstance(item, dict) and item.get('id') == fix.get('id'):
                item.update({k: v for k, v in fix.items() if k not in ('kind', 'id')})


def _say_failure(exc, ident, title):
    """(shown, Checks rows) for a failed build (buildmessage.explain), written to the Map
    Cooker console as '<title> was not built: ...' with the technical detail and traceback
    at debug level; a fault's report is saved in reports_dir()."""
    import traceback
    from riftlord.core import console
    shown, detail, rows = buildmessage.explain(exc, _STAGE.get(ident), reports_dir(), title)
    if detail:
        console.debug('%s: %s' % (title, detail), console.MAP_COOKER)
    if buildmessage.is_fault(exc):
        console.debug(''.join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                      console.MAP_COOKER)
    console.error(shown if title in shown else '%s was not built: %s' % (title, shown),
                  console.MAP_COOKER)
    return shown, rows


def _note_failure(ident, why, rows=()):
    rec = load(ident)
    if rec is not None:
        rec['bakeError'] = why
        if rows:
            rec['buildChecks'] = list(rows)
        else:
            rec.pop('buildChecks', None)
        _write_json(_record_path(ident), rec)


#: A mod key given to copy(): letters and digits, starting with a letter.
KEY_RE = re.compile(r'^[A-Za-z][A-Za-z0-9]{0,63}$')


def copy(ident, title, key=None):
    """A new editable mission made from a Map Editor mission: the same level, plan and
    waves under a new title, mod, folder and Guids, not yet built. Returns its id.
    `key` names the mod (and the id `900_<key>_P`); SystemExit when it is not letters
    and digits or is already used in Missions."""
    import copy as _copy
    src = load(ident)
    if not _editable(src):
        raise SystemExit('%s was not made in the Map Editor, so it cannot be copied this way'
                         % ident)
    title = (title or '').strip() or '%s copy' % (src.get('title') or ident)
    if key is None:
        new_id, mod = _fresh_identity(src['level'], title)
    else:
        if not KEY_RE.match(str(key)):
            raise SystemExit('mod key %r: use letters and digits only, starting with a letter'
                             % (key,))
        new_id, mod = '900_%s_P' % key, str(key)
        taken = {paksource.stem(p) for p in paksource.sources(root())}
        taken |= {n[:-5] for n in _record_files() if n.endswith('.json')}
        if new_id in taken or mod in _mods_in_use():
            raise SystemExit('mod key %s is already used in %s' % (key, root()))
    rec = _copy.deepcopy(src)
    for key in ('baked', 'bakedAt', 'bakeError', 'source'):
        rec.pop(key, None)
    rec.update(id=new_id, title=title, mod=mod, map='%s_Map01' % mod, guids=_new_guids(mod),
               created=time.time(), updated=time.time())
    #: a Working Title names one mission's level only (level_names)
    rec['workingTitle'] = fresh_working_title(title, new_id)
    if isinstance(rec.get('spec'), dict):
        rec['spec']['title'] = title
        rec['spec']['map'] = rec['map']
    # the copy gets its own picture files
    for slot in PICTURES:
        if _picture_file(src, slot):
            ext = os.path.splitext(src[slot]['file'])[1]
            rec[slot] = dict(src[slot], file='%s.%s%s' % (new_id, slot, ext))
            shutil.copyfile(_picture_file(src, slot), _meta('records', rec[slot]['file']))
        else:
            rec.pop(slot, None)
    _place_now(rec, rec.get('details'))
    _write_json(_record_path(new_id), rec)
    return new_id


#: Longest mission title a rename takes.
TITLE_MAX = 120


def rename(ident, title):
    """Give a mission a new title: what the game, the loading screen and Missions call it.
    Its id, folder, mod and Guids stay. A Map Editor mission is out of date until built
    again; an Onslaught Editor mission is saved again in place. Returns {id, title, stale}."""
    title = ' '.join(str(title or '').split())[:TITLE_MAX]
    if not title:
        raise SystemExit('Enter a name.')
    rec = load(ident)
    if rec is None:
        raise SystemExit('%s has no saved record to rename; open it in its editor and save it'
                         % ident)
    editable = _editable(rec)
    spec = rec.get('spec') if isinstance(rec.get('spec'), dict) else None
    resave = not editable and _source(ident) is not None
    if resave and (not spec or not spec.get('waves') or spec.get('bundle')):
        raise SystemExit('%s was not saved from waves Missions can rebuild; open it in its '
                         'editor to rename it' % (rec.get('title') or ident))
    if title == rec.get('title'):
        return {'id': ident, 'title': title, 'stale': is_stale(rec)}
    rec['title'] = title
    if spec is not None:
        spec['title'] = title
    if isinstance(rec.get('plan'), dict):
        rec['plan']['mission'] = dict(rec['plan'].get('mission') or {}, title=title)
    rec['updated'] = time.time()
    _write_json(_record_path(ident), rec)
    if resave:
        from riftlord.core import console
        from riftlord.onslaught import buildjob
        with console.working_as(console.MAP_COOKER):
            buildjob.resave(ident, note=console.notes(console.MAP_COOKER))
    refresh()
    return {'id': ident, 'title': title, 'stale': is_stale(load(ident))}


def _write_json(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, path)


def record(path, tool, **source):
    """Note a build that landed in the bucket. No-op unless Rift Lord runs, or when the
    build went somewhere else.

    `tool` is 'riftlord' or 'onslaught'. `source` holds what reopening needs: `level`
    and `plan` from the Map Editor, `map` and `spec` from the Onslaught Editor, and
    `title`, `details`, `placement` from either. A second build of the same mission
    updates the record and keeps what this build did not say."""
    if not ACTIVE or not path:
        return None
    if os.path.dirname(os.path.abspath(path).rstrip('/\\')) != os.path.abspath(root()):
        return None
    ident = item_id(path)
    rec = load(ident) or {'id': ident, 'created': time.time()}
    # a build that was current stays current: this build applied the change itself
    fresh = _editable(rec) and not is_stale(rec)
    rec['source'] = os.path.basename(path.rstrip('/\\'))
    rec['updated'] = time.time()
    rec['tools'] = sorted(set(rec.get('tools', [])) | {tool})
    if 'kind' not in rec:
        rec['kind'] = 'map' if tool == 'riftlord' else 'mission'
    # a Map Editor mission's title, details and placement are the Map Editor's
    owned = ('title', 'details', 'placement') if tool == 'onslaught' and _editable(rec) else ()
    for key, value in source.items():
        if value is None or key in owned:
            continue
        rec[key] = _merge_details(rec.get(key), value) if key == 'details' else value
    #: the Onslaught Editor's events become the plan's; a changed music event needs a bake
    #: (the `_Audio` copy is made there), and with the Mission Events piece placed so does any
    #: changed event (the piece and its duties are made there), so the build is no longer current
    spec = source.get('spec')
    if tool == 'onslaught' and _editable(rec) and isinstance(spec, dict) \
            and isinstance(spec.get('events'), list):
        from riftlord.missions import missionevents
        with_piece = bool(missionevents.piece(rec['plan']))

        def baked(evs):
            return [e for e in evs or [] if isinstance(e, dict)
                    and (with_piece or e.get('kind') in ('music', 'dialogue'))]
        from riftlord.missions import hostevents
        events = hostevents.clean(spec['events'])
        if baked(rec['plan'].get('events')) != baked(events):
            fresh = False
        rec['plan']['events'] = events
    #: the Rift Tear tab's half of the Crystal's groups becomes the plan's
    #: (riftlord/mapeditor/rifttear.py); the level changes, so the build is no longer current
    if tool == 'onslaught' and _editable(rec) and isinstance(spec, dict) \
            and isinstance(spec.get('riftTear'), dict):
        from riftlord.mapeditor import rifttear
        if rifttear.merge_editor(rec['plan'], spec['riftTear']):
            fresh = False
        if isinstance(rec.get('spec'), dict):
            rec['spec'] = {k: v for k, v in rec['spec'].items() if k != 'riftTear'}
    if fresh:
        rec['baked'] = bake_inputs(rec)
    _write_json(_record_path(ident), rec)
    refresh()
    return rec


def without_files(details):
    """Details as a record keeps them: a new campaign's banner is written beside its
    build, and is not carried in the record as base64."""
    if not isinstance(details, dict):
        return details
    out = dict(details)
    if isinstance(out.get('newCampaign'), dict):
        out['newCampaign'] = dict(out['newCampaign'], banner=None)
    for slot in PICTURES:                    # kept as rec[slot] (take_picture)
        out.pop(slot, None)
    #: a custom campaign's order is its table's; a shipped one's is rec['campaignIndex']
    out.pop('campaignIndex', None)
    return out


def _mission_rows(path):
    """[(row, title, guid hex)] of the mission rows a build defines."""
    from riftlord.build.navigation import navpatch
    from riftlord.missions import customcampaign
    out = []
    try:
        names = navpatch.pak_names(path)
        for key in names:
            if not key.endswith('_MissionProtos.uasset') or key[:-7] + '.uexp' not in names:
                continue
            t = customcampaign._table(navpatch.read_from_pak(path, names[key][1]),
                                      navpatch.read_from_pak(path, names[key[:-7] + '.uexp'][1]))
            for r in t.rows:
                g = t.field(r, 'Guid')
                out.append((r['name'], customcampaign._text(t, r, 'MissionName'),
                            bytes(g.payload)[:16].hex() if g is not None else ''))
    except (Exception, SystemExit):                  # noqa: BLE001
        pass
    return out


def _map_in(path):
    """The map a build carries, or None for a mission on a shipped map."""
    from riftlord.build.navigation import navpatch
    try:
        found = navpatch.maps_in_pak(path)
    except (Exception, SystemExit):                  # noqa: BLE001
        return None
    return sorted(found)[0] if found else None


_SHIPPED = {}


def _shipped_label(placement):
    """The game's name for a shipped campaign a mission was listed in, else None."""
    from riftlord.missions import customcampaign
    if placement not in customcampaign.SHIPPED:
        return None
    if not _SHIPPED:
        _SHIPPED.update(customcampaign.shipped_names())
    return _SHIPPED.get(placement, placement)


# -- installing ---------------------------------------------------------------------

def _installs():
    try:
        with open(_meta('installs.json'), encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _installed_pak(ident):
    """The game's copy of a mission's pak, or None."""
    mods = gamepath.mods_dir()
    there = os.path.join(mods, ident + '.pak') if mods else None
    return there if there and os.path.exists(there) else None


def _install_state(path, rec=None):
    """'installed' (the game has exactly this build), 'changed' (it has an older one,
    the build changed since, or the mission was edited since it was built) or 'no'."""
    ident = paksource.stem(path)
    there = _installed_pak(ident)
    if not there:
        return 'no'
    last = _installs().get(ident)
    if not last or last.get('pak') != paksource.digest(there):
        return 'changed'
    if rec is not None and is_stale(rec):
        return 'changed'
    #: the installed pak names its level by the Working Title it was packed with ('' for
    #: `<mod>_Map01`, as installs before 2026-10-30 note it)
    if rec is not None and rec.get('mod'):
        from riftlord.build import leveltitle
        own = leveltitle.default(rec['mod'])
        if (last.get('level') or own) != (rec.get('workingTitle') or own):
            return 'changed'
    return 'installed' if last.get('source') == paksource.digest(path) else 'changed'


def items():
    """{'items': [...], 'campaigns': [...], 'root': ...} for the Missions page.

    Every mission build is an item, with or without a record: one copied into the
    folder by hand is listed too, just without a source to edit from. Campaigns are
    not items; they come back as campaigns, each naming its missions."""
    from riftlord.missions import customcampaign
    here = root()
    migrate_campaign_fields()
    campaigns = customcampaign.installed(here)
    out = []
    built = {paksource.stem(p): p for p in paksource.sources(here)}
    unbuilt = [n[:-5] for n in sorted(_record_files())
               if n.endswith('.json') and n[:-5] not in built]
    for ident in list(built) + unbuilt:
        path = built.get(ident)
        rows = _mission_rows(path) if path else []
        rec = load(ident) or {'id': ident, 'kind': 'mission', 'tools': []}
        if not rows and not (path is None and _editable(rec)):
            continue
        guids = ([g for _r, _t, g in rows] if rows
                 else [g[:32] for g in (rec.get('guids') or {}).values()])
        member = [c for c in campaigns if set(c['missions']) & set(guids)]
        out.append({
            'id': ident, 'source': os.path.basename(path) if path else None,
            'loose': paksource.is_loose(path) if path else True,
            'kind': rec.get('kind', 'mission'),
            'title': rec.get('title') or (_main_rows(rows)[0][1] if rows else ident),
            'tools': rec.get('tools', []),
            'level': rec.get('level'),
            #: the name its level goes by in the game (level_name), or None
            'workingTitle': level_name(rec, path),
            #: listed in Endless alone (_endless_variant)
            'endlessOnly': bool(rec.get('endlessOnly')),
            'map': rec.get('map') or (_map_in(path) if path else None),
            # The Map Editor opens a mission on the level it was built on: its own record
            # of it, or the map an onslaught was authored on before any geometry was.
            'editable': {'riftlord': bool(rec.get('level') or (rec.get('spec') or {}).get('map')),
                         'onslaught': bool(_editable(rec) or (path and _map_in(path))
                                           or rec.get('map'))},
            # 'built', 'unlit', 'stale', 'unbuilt', 'failed' (build_state), or 'queued'
            # and 'building' while a build works through it
            'state': _PROGRESS.get(ident) or build_state(rec, path),
            #: why its light maps are still to bake (light_pending)
            'lighting': light_pending(rec),
            'error': rec.get('bakeError'),
            'guids': guids, 'rows': [r for r, _t, _g in rows],
            'campaign': member[0]['key'] if member else None,
            'campaignTitle': member[0]['title'] if member else None,
            #: its place in that campaign's MissionList, 1-based
            'campaignIndex': next((member[0]['missions'].index(g) + 1 for g in guids
                                   if g in member[0]['missions']), None) if member else None,
            'updated': rec.get('updated') or (os.path.getmtime(path) if path else 0),
            'bytes': paksource.size(path) if path else 0,
            #: 'installed', 'changed', 'no', or 'waiting' while an install waits for the
            #: game to close (install)
            'installed': 'waiting' if ident in _WAITING_INSTALL else
            _install_state(path, rec) if path else
            ('changed' if _installed_pak(ident) else 'no'),
            'author': (rec.get('details') or {}).get('author', ''),
            'placement': rec.get('placement'),
            'placementLabel': _shipped_label(rec.get('placement')),
        })
    camp = []
    for c in campaigns:
        entry = {'key': c['key'], 'title': c['title'], 'source': os.path.basename(c['pak']),
                 'description': c['description'], 'missions': c['missions'],
                 'own': bool(c.get('own'))}
        if c.get('own'):
            #: one pak with its missions (_pack_campaign); while it is not installed, each
            #: mission's own pak, if it has one, is what the game has (_units)
            unit = campaign_members(c, out)
            entry['members'] = unit
            entry['installed'] = 'waiting' if set(unit) & _WAITING_INSTALL else \
                _campaign_install_state(c, unit)
            for item in out:
                if item['id'] in unit and entry['installed'] != 'no':
                    item['installed'] = entry['installed']
        else:
            entry['installed'] = _install_state(c['pak'])
        camp.append(entry)
    return {'items': out, 'campaigns': camp, 'root': here}


def _campaign_install_state(c, ids):
    """'installed' (the game has this campaign's pak, packed from these builds of `ids`,
    each current), 'changed' (it has another) or 'no' (_install_state for one pak)."""
    stem = paksource.stem(c['pak'])
    mods = gamepath.mods_dir()
    there = os.path.join(mods, stem + '.pak') if mods else None
    if not there or not os.path.exists(there):
        return 'no'
    last = _installs().get(stem) or {}
    if last.get('pak') != paksource.digest(there) \
            or last.get('source') != paksource.digest(c['pak']) \
            or last.get('members') != _unit_sources(c, ids) \
            or any(is_stale(load(i) or {}) for i in ids):
        return 'changed'
    return 'installed'


#: Missions folders whose records migrate_campaign_fields has been through
_MIGRATED = set()


def migrate_campaign_fields():
    """Move the campaign fields older records kept per mission (details.newCampaign:
    description and hub) to the custom campaign they are listed in, where it has none of
    its own, and drop them from the records. Once per Missions folder per run."""
    from riftlord.missions import customcampaign
    here = os.path.abspath(root())
    if here in _MIGRATED:
        return
    _MIGRATED.add(here)
    try:
        campaigns = {customcampaign.CUSTOM + c['key']: c for c in customcampaign.installed(here)}
    except (Exception, SystemExit):                  # noqa: BLE001
        return
    for name in sorted(_record_files()):
        if not name.endswith('.json'):
            continue
        rec = load(name[:-5])
        if not isinstance(rec, dict):
            continue
        placement = rec.get('placement') or ''
        holders = [d for d in (rec.get('details'), (rec.get('spec') or {}).get('details')
                               if isinstance(rec.get('spec'), dict) else None)
                   if isinstance(d, dict) and 'newCampaign' in d]
        if not holders or not placement.startswith(customcampaign.CUSTOM):
            continue
        fresh = holders[0].get('newCampaign') or {}
        c = campaigns.get(placement)
        if c is not None and c.get('own'):
            want = {}
            if str(fresh.get('description') or '').strip() and not c.get('description'):
                want['description'] = fresh['description']
            if str(fresh.get('hub') or '').strip() and not c.get('hub'):
                want['hub'] = fresh['hub']
            if want:
                try:
                    customcampaign.edit(placement[len(customcampaign.CUSTOM):], mods=here, **want)
                    c.update(want)
                except (Exception, SystemExit):      # noqa: BLE001
                    pass
        for d in holders:
            d.pop('newCampaign', None)
        _write_json(_record_path(rec['id']), rec)


def campaign_settings(key):
    """A custom campaign in Missions as its editor shows it (customcampaign.choices)."""
    from riftlord.missions import customcampaign
    got = next((c for c in customcampaign.choices(shipped=False, mods=root())
                if c['name'] == customcampaign.CUSTOM + key), None)
    if got is None:
        raise SystemExit('campaign %s is not in Missions' % key)
    return got


def update_campaign(key, values):
    """Change a custom campaign -- in Missions while Rift Lord runs, else in the game's
    mods folder: `values` {name, description, hub, banner} (banner as
    customcampaign._save_banner takes it, or {crop, cardCrop} to crop its kept picture
    again, or null to remove it); a field left out is kept. Returns the campaign as
    customcampaign.choices lists it."""
    from riftlord.missions import customcampaign
    values = values or {}
    where = root() if ACTIVE else None
    banner = None
    if 'banner' in values:
        sent = values['banner']
        if sent is None:
            banner = False
        elif isinstance(sent, dict) and sent.get('data'):
            banner = sent
        elif isinstance(sent, dict) and (sent.get('crop') or sent.get('cardCrop')):
            # the same picture with other crops
            banner = _recrop(key, sent.get('crop'), where, sent.get('cardCrop'))
    got = customcampaign.edit(key, mods=where, name=values.get('name'),
                              description=values.get('description'),
                              hub=values.get('hub'), banner=banner)
    return dict(got)


def _recrop(key, crop, where=None, card_crop=None):
    """The campaign's kept banner source with a new tab crop and card crop (either None
    keeps the one it has), as _save_banner takes it, or None when it has no source to
    crop again or neither crop changed."""
    import base64
    from riftlord.missions import customcampaign
    c = next((c for c in customcampaign.installed(where)
              if customcampaign.same_key(c['key'], key)), None)
    if c is None or not paksource.is_loose(c['pak']):
        return None
    for ext in customcampaign.BANNER_TYPES:
        path = paksource.sidecar(c['pak'], customcampaign.BANNER_SOURCE + ext)
        if os.path.isfile(path):
            meta = paksource.sidecar(c['pak'], customcampaign.BANNER_SOURCE + '.json')
            try:
                with open(meta, encoding='utf-8') as fh:
                    old = json.load(fh)
            except (OSError, ValueError):
                old = {}
            crop = crop or old.get('crop')
            card_crop = card_crop or old.get('cardCrop')
            if not crop or (old.get('crop') == crop and old.get('cardCrop') == card_crop):
                return None
            with open(path, 'rb') as fh:
                data = base64.b64encode(fh.read()).decode()
            name = old.get('name') or ''
            if os.path.splitext(name)[1].lower() != ext:
                name = 'banner' + ext
            return {'name': name, 'data': data, 'crop': crop, 'cardCrop': card_crop}
    return None


def _sidecars(path):
    """The sidecar files beside a build."""
    return [p for p in (paksource.sidecar(path, s) for s in SIDECARS) if os.path.exists(p)]


def remove(ids):
    """Take missions out of the bucket: their builds, notes and records, and their
    Guids out of any campaign in the bucket. Nothing installed in the game is touched."""
    from riftlord.missions import customcampaign
    from riftlord.build.navigation import navpatch
    here = root()
    done = []
    for ident in ids:
        path = _source(ident)
        rec = load(ident)
        if path is None and rec is None:
            raise SystemExit('%s is not in Missions' % ident)
        guids = {g for _row, _title, g in _mission_rows(path)} if path else set()
        guids |= {g[:32] for g in ((rec or {}).get('guids') or {}).values()}
        for guid in sorted(g for g in guids if g):
            customcampaign.place(bytes.fromhex(guid), None, mods=here)
        if path is not None:
            navpatch.close_pak(path, every=True)
            for p in _sidecars(path):
                os.remove(p)
            paksource.remove(path)
        for slot in PICTURES:
            if rec is not None and _picture_file(rec, slot):
                os.remove(_picture_file(rec, slot))
        for b in ('.before-bundle', '.before-campaign'):
            keep = os.path.join(here, '.backups', ident + b)
            if os.path.isdir(keep):
                shutil.rmtree(keep)
        rp = _record_path(ident)
        if os.path.exists(rp):
            os.remove(rp)
        done.append(ident)
    refresh()
    return {'removed': done}


def settings(ident, views=True):
    """What the Missions page's Settings show for one mission: its details, where it
    is listed, and whether that can change here. `views` False leaves out the pictures
    (for a caller that only needs the facts)."""
    from riftlord.missions import customcampaign
    path = _source(ident)
    rec = load(ident) or {}
    if path is None and not _editable(rec):
        raise SystemExit('%s is not in Missions' % ident)
    rows = _mission_rows(path) if path else []
    if path is not None:
        notes = customcampaign.build_notes(path)
        mine = {e.get('Mission'): e for e in notes.get('Maps', [])}
        entry = next((mine[r] for r, _t, _g in rows if r in mine), {})
        details = {'author': entry.get('Author', ''), 'version': entry.get('Version', ''),
                   'description': entry.get('Description', ''), 'hub': entry.get('Hub', '')}
        details = _merge_details(details, {k: (rec.get('details') or {}).get(k)
                                           for k in DETAIL_FIELDS})
        endless = any(r.endswith(_twin()) for r, _t, _g in rows)
        guids = [g for _r, _t, g in rows]
    else:
        details = {k: (rec.get('details') or {}).get(k, '') for k in DETAIL_FIELDS}
        endless = bool(rec.get('endless'))
        guids = [g[:32] for g in (rec.get('guids') or {}).values()]
    placement = _placement_view(rec)
    member = [c for c in customcampaign.installed(root()) if set(c['missions']) & set(guids)]
    if member:
        placement = customcampaign.CUSTOM + member[0]['key']
    shipped = placement in customcampaign.SHIPPED
    pictured = _can_picture(rec)
    from riftlord.missions import missionscramble, missionunlocks
    return {'id': ident, 'title': rec.get('title') or (rows[0][1] if rows else ident),
            'details': details, 'placement': placement, 'endless': endless,
            #: listed in Endless alone (_endless_variant); offered to Map Editor missions
            'endlessOnly': bool(rec.get('endlessOnly')),
            'canEndlessOnly': _editable(rec),
            'resetDoors': bool(rec.get('resetDoors')),
            #: the Scramble tiers it joins, and the live tiers to choose from
            'scramble': missionscramble.value(missionscramble.of_record(rec)),
            'scrambleTiers': missionscramble.view() if views else None,
            #: what beating it grants, and the heroes and items installed paks offer
            #: (missionunlocks); a mission placed in Endless grants nothing
            'unlocks': missionunlocks.value(missionunlocks.of_record(rec)) if views else None,
            'unlockChoices': missionunlocks.view() if views else None,
            # Map Editor missions, and missions with Onslaught Editor waves to rebuild
            'canResetDoors': _editable(rec) or bool(isinstance(rec.get('spec'), dict)
                                                    and rec['spec'].get('waves')),
            'shipped': shipped and not _editable(rec),
            'editable': _editable(rec),
            # the mission's pictures: a build from its record or saved waves applies them
            'canPicture': pictured,
            'picture': picture_view(rec) if pictured and views else None,
            'portalPicture': picture_view(rec, 'portalPicture') if pictured and views else None,
            # what it shows without pictures of its own (sourcepictures)
            'sourcePictures': source_pictures(rec) if pictured and views else None,
            'guid': guid_spelled(ident) if (rec.get('guids') or rows) else '',
            #: the level's name in the game (working_title_view), or None
            'workingTitle': working_title_view(dict(rec, id=ident), path),
            #: each campaign's Campaign Index range for this mission (customcampaign.choices)
            'choices': customcampaign.choices(mods=root(), guid=_own_guid(rec, rows),
                                              shipped_index={placement: _shipped_index_of(rec)}
                                              if shipped else None)}


def _own_guid(rec, rows):
    """A mission's own 16-byte Guid: its build's main row's, else its record's, or None."""
    main = _main_rows(rows) if rows else []
    if main and main[0][2]:
        return bytes.fromhex(main[0][2])[:16]
    return _main_guid(rec)


def choices_for(ident=None):
    """customcampaign.choices for the editors' campaign pickers, with each campaign's
    Campaign Index range for mission `ident` (or for a new mission)."""
    from riftlord.missions import customcampaign
    rec = (load(ident) or {}) if ident and ACTIVE else {}
    path = _source(ident) if ident and ACTIVE else None
    rows = _mission_rows(path) if path else []
    placement = rec.get('placement') or ''
    return customcampaign.choices(
        mods=root() if ACTIVE else None, guid=_own_guid(rec, rows),
        shipped_index={placement: _shipped_index_of(rec)}
        if placement in customcampaign.SHIPPED else None)


def set_campaign_index(key, ident, index):
    """Give mission `ident` place `index` in installed custom campaign `key` (its
    customcampaign key); the missions between move along. Returns customcampaign.move's
    answer."""
    from riftlord.missions import customcampaign
    rec = load(ident) or {}
    path = _source(ident)
    guid = _own_guid(rec, _mission_rows(path) if path else [])
    if guid is None:
        raise SystemExit('%s has no Guid for a campaign to list' % ident)
    got = customcampaign.move(key, guid, index, mods=root())
    refresh()
    return got


def _can_picture(rec):
    """True for a mission whose next build applies its pictures: a Map Editor mission,
    or one rebuilt from its saved Onslaught Editor waves."""
    spec = (rec or {}).get('spec')
    return _editable(rec) or bool(isinstance(spec, dict) and spec.get('waves')
                                  and not spec.get('bundle'))


def source_level(rec):
    """The shipped level a mission was made from, or None."""
    spec = (rec or {}).get('spec') if isinstance((rec or {}).get('spec'), dict) else {}
    return (rec or {}).get('level') or spec.get('map') or (rec or {}).get('map')


def source_pictures(rec):
    """The source level's own loading screen and hub portal pictures (sourcepictures.view),
    or None."""
    level = source_level(rec)
    if not level:
        return None
    try:
        from riftlord.missions import sourcepictures
        got = sourcepictures.view(level)
    except (Exception, SystemExit):                  # noqa: BLE001
        return None
    try:
        from riftlord.build.minimap import mapdata
        got['title'] = mapdata.mission_names().get(level) or level
    except (Exception, SystemExit):                  # noqa: BLE001
        got['title'] = level
    return got


def preview_view(rec, options=None):
    """The mission's enemy preview as the Settings show it: {options, units [{unit,
    creature, count, flyer, art}], showing [creature], image (PNG data URL)}, or None for
    a mission without waves. `options` previews unsaved ones."""
    import base64
    spec = rec.get('spec') if isinstance(rec, dict) else None
    if not isinstance(spec, dict) or not spec.get('waves'):
        return None
    from riftlord.onslaught import makepreview
    from riftlord.onslaught import onslaught
    from riftlord.onslaught import wavespec
    opts = makepreview.clean_options(spec.get('preview') if options is None else options)
    spec = wavespec.resolve_units(spec)
    units = wavespec.roster_of(spec)
    counts = wavespec.unit_counts(spec)
    flags = onslaught.proto_flags()
    placed = makepreview.plan(units, counts, opts)
    rows = []
    for u in sorted(units, key=lambda u: -counts.get(u, 0)):
        c = makepreview.creature_for(u)
        rows.append({'unit': u, 'label': flags.get(u, {}).get('name') or u,
                     'creature': c, 'count': counts.get(u, 0),
                     'flyer': bool(flags.get(u, {}).get('flyer')),
                     'art': bool(c and makepreview.assets_for(c))})
    return {'options': opts,
            'defaults': {'ground': makepreview.SLOT_BUDGET, 'flyers': makepreview.FLYER_BUDGET,
                         'maxGround': makepreview.OPTION_MAX_GROUND,
                         'maxFlyers': makepreview.OPTION_MAX_FLYERS},
            'units': rows, 'showing': [c for c, _p in placed],
            'image': 'data:image/png;base64,' + base64.b64encode(
                makepreview.picture(placed)).decode() if placed else None}


def preview(ident, options=None):
    """preview_view for a mission in Missions, with unsaved `options`."""
    return preview_view(load(ident) or {}, options)


def _twin():
    from riftlord.build import makemod
    return makemod.ENDLESS_TWIN


def _main_rows(rows):
    """The mission's own rows, without its Endless twin."""
    return [r for r in rows if not r[0].endswith(_twin())] or rows


def makemod_custom():
    from riftlord.build import makemod
    return makemod.CUSTOM_MAPS


def update_settings(ident, details=None, placement=None, new_campaign=None, endless=None,
                    reset_doors=None, preview=None, guid=None, scramble=None,
                    campaign_index=None, working_title=None, endless_only=None,
                    unlocks=None):
    """Change a mission's details, where it is listed, and Reset door after Onslaught
    Loop. A Map Editor mission given a new reset setting is out of date until built; an
    Onslaught Editor mission is rebuilt in place. `scramble` ({on, tier, rows},
    missionscramble.clean) is written into a current build in place, and so is `unlocks`
    ({heroes, items} of Guids, missionunlocks.clean). `endless_only` makes
    a Map Editor mission one placed in Endless (_endless_variant; placement
    customcampaign.ENDLESS does the same): listed in Endless alone,
    out of any campaign at once, and out of date until built.

    `campaign_index` is its place in its campaign (1-based): a custom campaign's table is
    rewritten at once (customcampaign.place, move); a shipped one's is kept in the record
    for the next build. `working_title` is its level's name in the game (set_working_title;
    '' for the default).

    A Map Editor mission keeps them in its record, and a current build gets them in
    place too; any campaign, shipped ones included, can be chosen, since its next build
    applies it. Any other mission's details are its notes file beside the build, and
    Custom Maps and the custom campaigns are lists of Guids, so moving between them
    rewrites only campaign tables; a shipped campaign is part of its build, so changing
    that means saving it again in its editor."""
    from riftlord.missions import customcampaign
    path = _source(ident)
    rec = load(ident) or {'id': ident, 'created': time.time(), 'kind': 'mission', 'tools': []}
    editable = _editable(rec)
    if path is None and not editable:
        raise SystemExit('%s is not in Missions' % ident)
    rows = _mission_rows(path) if path else []
    if path is not None and not rows:
        raise SystemExit('%s defines no mission' % ident)
    fresh = editable and path is not None and not is_stale(rec)
    current = settings(ident, views=False)
    done = {'id': ident}
    resave = False
    if details is not None:
        clean = {k: str(details.get(k) or '').strip() for k in DETAIL_FIELDS}
        clean['hub'] = customcampaign.hub_name(clean['hub'])
        if path is not None:
            customcampaign.write_notes(path, maps=[{
                'Mission': _main_rows(rows)[0][0], 'Author': clean['author'],
                'Version': clean['version'], 'Description': clean['description'],
                'Hub': clean['hub']}])
        rec['details'] = dict(rec.get('details') or {}, **clean)
        if isinstance(rec.get('spec'), dict):
            rec['spec']['details'] = dict(rec['spec'].get('details') or {}, **clean)
        if isinstance(rec.get('plan'), dict):
            rec['plan']['mission'] = dict(rec['plan'].get('mission') or {}, **clean)
        done['details'] = clean
        for slot, (_aspect, what) in PICTURES.items():
            if slot not in details:
                continue
            if not _can_picture(rec):
                raise SystemExit('Only a mission made in the Map Editor, or saved with '
                                 'Onslaught Editor waves, can have a custom %s.' % what)
            if take_picture(rec, details[slot], slot):
                fresh = False
                resave = resave or (not editable and path is not None)
                done[slot] = True
    campaign_index = None if campaign_index in (None, '') else campaign_index
    #: Campaign Endless lists it in the Endless hub alone; any other campaign takes it out
    if placement == customcampaign.ENDLESS:
        endless_only, placement = True, None
    elif placement and placement != current['placement'] and endless_only is None:
        endless_only = False
    turned = endless_only is not None and _endless_variant(rec, endless_only)
    if turned:
        fresh = False
        if isinstance(rec.get('spec'), dict):
            rec['spec']['endlessOnly'] = bool(endless_only)
        if isinstance(rec.get('plan'), dict):
            rec['plan']['mission'] = dict(rec['plan'].get('mission') or {},
                                          endlessOnly='true' if endless_only else '')
        done['endlessOnly'] = bool(endless_only)
    if rec.get('endlessOnly'):
        done['campaign'] = _place_variant(rec)
        done['placement'] = customcampaign.ENDLESS
        placement, campaign_index = None, None
    elif turned:
        #: out of Endless: in Custom Maps until the campaign asked for lists it (below)
        current['placement'] = rec['placement'] = makemod_custom()
        done['placement'] = rec['placement']
    if placement and placement != current['placement']:
        # the loading screen names the campaign, so a built mission is out of date
        fresh = False
        to_shipped = placement in customcampaign.SHIPPED
        from_shipped = (current['placement'] in customcampaign.SHIPPED)
        if not editable and (from_shipped or to_shipped):
            raise SystemExit('Listing a mission in a shipped campaign, or taking it out of '
                             'one, changes the mission itself: open it in its editor, '
                             'choose the campaign there and save it again.')
        if to_shipped or from_shipped:
            fresh = False                    # the shipped table is part of the build
        if to_shipped:
            rec['placement'] = placement
            _keep_shipped_index(rec, campaign_index)
            if rows or rec.get('guids'):
                listed = _main_guid(rec) or bytes.fromhex(_main_rows(rows)[0][2])
                customcampaign.place(listed, None, mods=root())
            done['campaign'] = _shipped_label(placement)
        else:
            choice_details = {'newCampaign': new_campaign or {}}
            _into, choice = customcampaign.resolve(placement, choice_details)
            if isinstance(choice, dict) and not (choice.get('name') or '').strip():
                raise SystemExit('name the new campaign')
            listed = _main_guid(rec) if path is None else \
                bytes.fromhex(_main_rows(rows)[0][2])
            placed = customcampaign.place(listed, choice, mods=root(), index=campaign_index)
            if choice is None:
                rec['placement'] = makemod_custom()
            else:
                rec['placement'] = customcampaign.CUSTOM + '%s:%s' % (
                    os.path.basename(placed['campaign_pak']), placed['campaign_row'])
                done['campaignIndex'] = placed.get('campaign_index')
            rec.pop('campaignIndex', None)
            done['campaign'] = placed.get('campaign')
        if isinstance(rec.get('spec'), dict):
            rec['spec']['intoCampaign'] = rec['placement']
        if isinstance(rec.get('plan'), dict):
            rec['plan']['mission'] = dict(rec['plan'].get('mission') or {},
                                          placement=rec['placement'])
        done['placement'] = rec['placement']
    elif campaign_index is not None and current['placement'] in customcampaign.SHIPPED:
        if not editable and _shipped_index_of(rec) != customcampaign._whole(campaign_index):
            raise SystemExit('Its place in a shipped campaign is part of the mission itself: '
                             'open it in its editor, set the Campaign Index there and save '
                             'it again.')
        rec['placement'] = current['placement']
        if _keep_shipped_index(rec, campaign_index):
            fresh = False
            done['campaignIndex'] = rec.get('campaignIndex')
    elif campaign_index is not None and current['placement'].startswith(customcampaign.CUSTOM):
        listed = _main_guid(rec) if path is None else bytes.fromhex(_main_rows(rows)[0][2])
        moved = customcampaign.move(current['placement'][len(customcampaign.CUSTOM):], listed,
                                    campaign_index, mods=root())
        done['campaignIndex'] = moved['index']
    if working_title is not None:
        done['workingTitle'] = _working_title(rec, working_title)
    if endless is not None and bool(endless) != current['endless']:
        if current['shipped']:
            raise SystemExit('This mission is in a shipped campaign, so its Endless twin is '
                             'part of that campaign\'s table: change it in its editor.')
        #: a mission placed in Endless has no twin to add or take out
        if path is not None and (fresh or not editable) and not rec.get('endlessOnly'):
            from riftlord.build import makemod
            paksource.write(path, makemod.set_endless(paksource.entries(path), bool(endless)))
        rec['endless'] = bool(endless)
        if isinstance(rec.get('spec'), dict):
            rec['spec']['endless'] = bool(endless)
        if isinstance(rec.get('plan'), dict):
            rec['plan']['mission'] = dict(rec['plan'].get('mission') or {},
                                          endless='true' if endless else '')
        done['endless'] = bool(endless)
    if scramble is not None:
        from riftlord.missions import missionscramble
        wanted = missionscramble.clean(scramble)
        if wanted != missionscramble.of_record(rec):
            if path is not None and (fresh or not editable):
                done['scrambleReport'] = _rescramble(path, rows, wanted)
            rec['scramble'] = wanted
            if isinstance(rec.get('spec'), dict):
                rec['spec']['scramble'] = wanted
            if isinstance(rec.get('plan'), dict):
                kept = {k: v for k, v in (rec['plan'].get('mission') or {}).items()
                        if k != 'scramble'}
                rec['plan']['mission'] = dict(kept, **({'scramble': wanted} if wanted else {}))
            done['scramble'] = missionscramble.value(wanted)
    if unlocks is not None:
        from riftlord.missions import missionunlocks
        was = missionunlocks.of_record(rec)
        wanted = missionunlocks.clean(unlocks, previous=was)
        if wanted != was:
            if path is not None and (fresh or not editable):
                done['unlocksReport'] = _reunlock(path, wanted, bool(rec.get('endlessOnly')))
            rec['unlocks'] = wanted
            if isinstance(rec.get('spec'), dict):
                rec['spec']['unlocks'] = wanted
            done['unlocks'] = missionunlocks.value(wanted)
    if preview is not None and isinstance(rec.get('spec'), dict) and rec['spec'].get('waves'):
        from riftlord.onslaught import makepreview
        opts = makepreview.clean_options(preview)
        if opts != makepreview.clean_options(rec['spec'].get('preview')):
            if opts:
                rec['spec']['preview'] = opts
            else:
                rec['spec'].pop('preview', None)
            fresh = False
            resave = resave or (not editable and path is not None)
            done['preview'] = opts
    if reset_doors is not None and bool(reset_doors) != bool(rec.get('resetDoors')):
        if not editable and not (isinstance(rec.get('spec'), dict)
                                 and rec['spec'].get('waves')):
            raise SystemExit('%s has no waves saved by the Onslaught Editor, so Reset door '
                             'after Onslaught Loop cannot be set here.'
                             % (rec.get('title') or ident))
        rec['resetDoors'] = bool(reset_doors)
        if isinstance(rec.get('spec'), dict):
            rec['spec']['resetDoors'] = bool(reset_doors)
        if isinstance(rec.get('plan'), dict):
            rec['plan']['mission'] = dict(rec['plan'].get('mission') or {},
                                          resetDoors='true' if reset_doors else '')
        fresh = False
        resave = resave or (not editable and path is not None)
        done['resetDoors'] = bool(reset_doors)
    if fresh:
        rec['baked'] = bake_inputs(rec)
    elif editable:
        rec.setdefault('baked', '')                  # an unstamped record goes out of date
    rec['updated'] = time.time()
    _write_json(_record_path(ident), rec)
    #: set_guid rewrites the record and the campaign listing, and leaves it to be built
    if guid and str(guid).strip() and str(guid).strip().upper() != current.get('guid'):
        got = set_guid(ident, guid)
        done['guid'] = got['guid']
        if got['missions']:
            done['guidShared'] = got['missions']
        resave = resave or (not editable and path is not None)
    if resave:
        from riftlord.core import console
        from riftlord.onslaught import buildjob
        with console.working_as(console.MAP_COOKER):
            buildjob.resave(ident, note=console.notes(console.MAP_COOKER),
                            resetDoors=bool(rec.get('resetDoors')))
    refresh()
    return done


def _rescramble(path, rows, scramble):
    """Write a build's Scramble extension for `scramble` in place
    (missionscramble.apply), checked first. Returns its console lines."""
    from riftlord.missions import missionscramble
    row, _title, guid = _main_rows(rows)[0]
    mod, guid = row.rsplit('_', 1)[0], bytes.fromhex(guid)[:16]
    files, report = missionscramble.apply(paksource.entries(path), mod, guid, scramble)
    bad = missionscramble.problems(files, mod, guid, [tuple(e) for e in report['scramble']],
                                   report['scramble_tiers'])
    if bad:
        raise SystemExit('Scramble was not written: %s' % '; '.join(bad[:3]))
    paksource.write(path, files)
    return [line for _warn, line in missionscramble.describe(report)]


def _reunlock(path, unlocks, endless_only):
    """Write a build's mission row granting `unlocks` in place (missionunlocks.apply),
    checked first. Returns its console lines."""
    from riftlord.missions import missionunlocks
    files, report = missionunlocks.apply(paksource.entries(path), unlocks,
                                         endless_only=endless_only)
    bad = [name for name, ok, _d in missionunlocks.checks(files, report) if not ok]
    if bad:
        raise SystemExit('Unlocks were not written: %s' % '; '.join(bad[:3]))
    paksource.write(path, files)
    return [line for _warn, line in missionunlocks.describe(report)]


def _guids_in(folder, skip=()):
    """Every mission Guid (hex) defined by a build in `folder`."""
    from riftlord.build import makemod
    out = set()
    for path in paksource.sources(folder):
        if os.path.basename(path) not in skip:
            out |= {g[:16].hex() for g in makemod.previous_guids(path).values()}
    return out


def _pack(path, dest_dir, files=None, rec=None):
    """Write a build into `dest_dir` as `<stem>.pak`, with OrcsMustUI's files for it --
    the notes and banner beside it in the bucket -- inside the pak, in its mod folder
    (makemod.OMUI_DIR). Old sidecars beside the installed pak are removed. A record `rec`
    with a Working Title names the pak's level by it (leveltitle.retitle)."""
    from riftlord.build import makemod
    out = os.path.join(dest_dir, paksource.stem(path) + '.pak')
    files = _packed_files(path, files, rec)
    paksource.write(out, files, keep_omui=False)
    makemod.remove_loose_sidecars(out)
    return out


def _packed_files(path, files=None, rec=None):
    """{inner path: bytes} of build `path` as Install and Package pack it: its level named
    by the record's Working Title (leveltitle.retitle), and its notes and banner beside it
    in the bucket put inside, in its mod folder (makemod.OMUI_DIR)."""
    from riftlord.build import makemod
    files = dict(files if files is not None else paksource.entries(path))
    if rec and rec.get('workingTitle') and rec.get('mod'):
        from riftlord.build import leveltitle
        files, _r = leveltitle.retitle(files, rec['mod'], rec['workingTitle'])
    key = makemod.pak_mod_key(files)
    if key:
        makemod.embed_loose_sidecars(path, files, key)
    elif _sidecars(path):
        raise SystemExit('%s has notes but no mod folder (/Game/Mods/<mod>/Tables) to hold '
                         'them in the pak' % paksource.stem(path))
    return files


# -- a custom campaign is one pak --------------------------------------------------------

def campaign_members(c, listing=None):
    """Ids of the missions custom campaign `c` (customcampaign.installed) packs: those it
    lists, in its order. `listing` is items()['items'] (read when None)."""
    listing = items()['items'] if listing is None else listing
    order = {g: i for i, g in enumerate(c['missions'])}
    listed = [i for i in listing if set(i['guids']) & set(order)]
    listed.sort(key=lambda i: min(order[g] for g in i['guids'] if g in order))
    return [i['id'] for i in listed]


def _units(ids, listing=None, dest=None):
    """What Install and Package write for missions `ids`: [(campaign, [ids])]. A mission
    in a custom campaign Rift Lord made goes into that campaign's ONE pak with every other
    mission it packs (campaign_members) when `ids` hold all of them (Install Campaign), or
    when that pak is already in `dest` (installing: it is packed again, so the mission is
    never both alone and in it); else it is a pak of its own, as is any other mission (campaign
    None). A mission in a shipped campaign patches the game's campaign table, so it stays a
    pak of its own."""
    from riftlord.missions import customcampaign
    listing = items()['items'] if listing is None else listing
    by_id = {i['id']: i for i in listing}
    camps = {c['key']: c for c in customcampaign.installed(root()) if c.get('own')}
    out, seen = [], set()
    for ident in ids:
        c = camps.get((by_id.get(ident) or {}).get('campaign'))
        whole = c is not None and (
            set(campaign_members(c, listing)) <= set(ids) or
            bool(dest) and os.path.exists(os.path.join(dest, paksource.stem(c['pak']) + '.pak')))
        if not whole:
            out.append((None, [ident]))
        elif c['key'] not in seen:
            seen.add(c['key'])
            out.append((c, campaign_members(c, listing)))
    return out


def _pack_campaign(c, ids, dest_dir):
    """Write custom campaign `c` with its missions `ids` into `dest_dir` as ONE pak,
    `<campaign stem>.pak`: the campaign's table, listing those of its missions that are
    there; each mission's files as it is packed alone (_packed_files: its own mod folder,
    its level by its Working Title); and one CustomMaps.yaml in the campaign's folder with
    every mission's notes and the campaign's, beside its banner and cards. ProtoRegistry
    registers the tables of every mod folder in a pak, but copies OrcsMustUI's files by pak
    and file name (ModData/<pak>.<file>), so a pak carries each file once. Returns its
    path."""
    from riftlord.build import makemod
    from riftlord.missions import customcampaign
    files = dict(paksource.entries(c['pak']))
    key = makemod.pak_mod_key(files)
    makemod.embed_loose_sidecars(c['pak'], files, key)
    omui = {name: files.pop(inner) for name, inner in _omui_data(files).items()}
    notes = customcampaign.parse_notes(
        omui.pop(customcampaign.NOTES_NAME, b'').decode('utf-8', 'replace'))
    present = set()
    for ident in ids:
        src = _source(ident)
        if src is None:
            raise SystemExit('%s of %s is not built' % (ident, c['title']))
        mfiles = _packed_files(src, rec=load(ident) or {})
        present |= {g[:16].hex() for g in makemod.previous_guids(src).values()}
        for name, inner in _omui_data(mfiles).items():
            data = mfiles.pop(inner)
            if name == customcampaign.NOTES_NAME:
                got = customcampaign.parse_notes(data.decode('utf-8', 'replace'))
                for section in ('Maps', 'Campaigns'):
                    notes[section] = notes.get(section, []) + got.get(section, [])
            elif omui.get(name, data) != data:
                raise SystemExit('%s: two of its missions carry a different OrcsMustUI %s'
                                 % (c['title'], name))
            else:
                omui[name] = data
        for path, data in mfiles.items():
            if files.get(path, data) != data:
                raise SystemExit('%s: two of its missions carry a different %s; build them '
                                 'again' % (c['title'], path))
            files[path] = data
    #: a campaign listing a mission it does not pack shows it greyed out
    keep = [g for g in c['missions'] if g in present]
    if keep != c['missions']:
        table = 'OMD/Content' + makemod.campaign_slot_path(c['slot'])[len('/Game'):] + '.uasset'
        files[table], files[table[:-7] + '.uexp'] = customcampaign._with_list(
            files[table], files[table[:-7] + '.uexp'], c['row'], [bytes.fromhex(g) for g in keep])
    text = customcampaign.notes_text(notes)
    if text:
        omui[customcampaign.NOTES_NAME] = text.encode('utf-8')
    for name, data in sorted(omui.items()):
        makemod.add_omui_file(files, key, name, data)
    bad = makemod.omui_problems(files)
    if bad:
        raise SystemExit('%s not packed: %s' % (c['title'], bad[0]))
    out = os.path.join(dest_dir, paksource.stem(c['pak']) + '.pak')
    paksource.write(out, files, keep_omui=False)
    makemod.remove_loose_sidecars(out)
    return out


def _mod_of(ident):
    """A mission's mod folder: its record's, else its build's mission row's prefix."""
    rec = load(ident) or {}
    if rec.get('mod'):
        return rec['mod']
    path = _source(ident)
    rows = _main_rows(_mission_rows(path)) if path else []
    return rows[0][0].rsplit('_', 1)[0] if rows and '_' in rows[0][0] else None


def _moved_from(dest, units, listing):
    """([(campaign, ids)], [paths]): the campaign paks in `dest` that hold a mission
    `units` now packs somewhere else (it left the campaign, or moved to another), to be
    packed again with the missions they pack now; and those whose campaign is gone from
    Missions (left with no missions, customcampaign.place), to be removed."""
    from riftlord.missions import customcampaign
    here = root()
    camps = {paksource.stem(c['pak']): c for c in customcampaign.installed(here)
             if c.get('own')}
    packing = {paksource.stem(c['pak']) for c, _u in units if c is not None}
    mods = {_mod_of(i) for c, unit in units for i in unit} - {None}
    more, gone = [], []
    for name in sorted(os.listdir(dest)):
        stem = name[:-len('.pak')]
        if not name.endswith('_Campaign_P.pak') or stem in packing:
            continue
        try:
            inside = {n.split('/')[3] for n in paksource.names(os.path.join(dest, name))
                      if n.startswith('OMD/Content/Mods/') and n.count('/') > 3}
        except (Exception, SystemExit):              # noqa: BLE001
            continue
        if not inside & mods:
            continue
        if stem in camps:
            more.append((camps[stem], campaign_members(camps[stem], listing)))
        elif any(os.path.exists(os.path.join(here, stem + tail + '.disabled'))
                 for tail in ('', '.pak')):
            gone.append(os.path.join(dest, name))
    return more, gone


def _omui_data(files):
    """{file name: inner path} of the OrcsMustUI data files among `files` (ProtoRegistry
    copies them by pak and file name); not the Blueprint packages OrcsMustUI loads by path."""
    from riftlord.build import makemod
    return {name: inner for name, inner in makemod.omui_files(files).items()
            if not name.endswith(('.uasset', '.uexp', '.ubulk'))}


def _unit_sources(c, ids):
    """{id: (build digest, level name)} of the missions a campaign pak was packed from,
    which its install state is checked against."""
    out = {}
    for ident in ids:
        src = _source(ident)
        out[ident] = [paksource.digest(src) if src else '', level_name(load(ident) or {}, src)
                      or '']
    return out


def refuse_unbuilt(ids, verb):
    """Raise SystemExit naming every listed mission that is not built, out of date,
    failed its last build or is being built. `verb` is 'installed' or 'packaged'."""
    from riftlord.core import console
    unbuilt = []
    for ident in ids:
        rec = load(ident)
        if ident in _PROGRESS or needs_build(rec):
            unbuilt.append((rec or {}).get('title') or ident)
    if not unbuilt:
        return
    console.error('nothing %s: %d mission(s) not built' % (verb, len(unbuilt)),
                  console.PACKAGING)
    if len(unbuilt) == 1:
        raise SystemExit('Build %s first: it is not built, is out of date or its lighting is '
                         'still to bake. Nothing was %s.' % (unbuilt[0], verb))
    raise SystemExit('Build these missions first; they are not built, are out of date or '
                     'their lighting is still to bake: %s. Nothing was %s.'
                     % (', '.join(unbuilt), verb))


def export(ids, dest=None, note=None):
    """Pack missions into `dest` as paks -- the game's ~mods by default, which is what
    installing is. A mission in a custom campaign Rift Lord made goes into that campaign's
    ONE pak with every mission it packs (_units,
    _pack_campaign); any other mission is a pak of its own. Installing, a campaign pak
    takes over: the paks its missions had of their own in ~mods are removed, and a campaign
    pak there still holding a mission packed elsewhere now is packed again without it
    (_moved_from), or removed with its campaign. A custom campaign Rift Lord did not make
    is packed as its table alone, listing only the missions that are actually at `dest`
    afterwards: a campaign naming a mission the player does not have shows it with Play
    greyed out."""
    from riftlord.core import console
    from riftlord.missions import customcampaign
    note = note or console.notes(console.PACKAGING, 'info')
    here = root()
    installing = dest is None
    dest = dest or gamepath.mods_dir()
    if not dest:
        raise SystemExit('Orcs Must Die 3 was not found, so there is nowhere to install')
    os.makedirs(dest, exist_ok=True)
    listing = items()['items']
    every = {i['id']: i for i in listing}
    picked = [every[i] for i in ids if i in every]
    if not picked:
        raise SystemExit('no missions chosen')
    units = _units([i['id'] for i in picked], listing, dest if installing else None)
    gone = []
    if installing:
        more, gone = _moved_from(dest, units, listing)
        units += more
    #: the missions asked for are built from their records; a campaign pak packed again for
    #: one of them takes its other missions' builds as they are
    asked = {i['id'] for i in picked}
    refuse_unbuilt([i for _c, unit in units for i in unit if i in asked],
                   'installed' if installing else 'packaged')
    missing = [(load(i) or {}).get('title') or i for _c, unit in units for i in unit
               if i not in asked and _source(i) is None]
    if missing:
        raise SystemExit('Build %s first: the campaign pak holds %s too. Nothing was %s.'
                         % (', '.join(missing), 'it' if len(missing) == 1 else 'them',
                            'installed' if installing else 'packaged'))
    installs = _installs()
    written = []
    camps = []
    for c, unit in units:
        if c is None:
            src = _source(unit[0])
            rec = load(unit[0]) or {}
            out = _pack(src, dest, rec=rec)
            if installing:
                installs[unit[0]] = {'pak': paksource.digest(out),
                                     'source': paksource.digest(src), 'at': time.time(),
                                     'level': level_name(rec, src) or ''}
        else:
            out = _pack_campaign(c, unit, dest)
            camps.append({'campaign': c['title'], 'pak': os.path.basename(out),
                          'missions': len(unit), 'left_out': 0})
            if installing:
                installs[paksource.stem(c['pak'])] = {
                    'pak': paksource.digest(out), 'source': paksource.digest(c['pak']),
                    'members': _unit_sources(c, unit), 'at': time.time()}
                #: the campaign pak takes over from its missions' own paks
                for ident in unit:
                    installs.pop(ident, None)
                    alone = os.path.join(dest, ident + '.pak')
                    if os.path.exists(alone):
                        paksource.remove(alone)
                        note('removed %s: %s packs it' % (os.path.basename(alone),
                                                          os.path.basename(out)))
        written.append(os.path.basename(out))
        note('%s %s' % ('installed' if installing else 'packed', os.path.basename(out)))
    for path in gone:
        paksource.remove(path)
        installs.pop(paksource.stem(path), None)
        note('removed %s: its campaign has no missions left' % os.path.basename(path))
    want = {g for item in picked for g in item['guids']}
    packed = {c['key'] for c, _u in units if c is not None}
    for c in customcampaign.installed(here):
        if not set(c['missions']) & want or c['key'] in packed or c.get('own'):
            continue
        name = paksource.stem(c['pak']) + '.pak'
        present = _guids_in(dest, skip=(name,))
        keep = [g for g in c['missions'] if g in present]
        files = paksource.entries(c['pak'])
        if keep != c['missions']:
            from riftlord.build import makemod
            key = 'OMD/Content' + makemod.campaign_slot_path(c['slot'])[len('/Game'):] \
                + '.uasset'
            files[key], files[key[:-7] + '.uexp'] = customcampaign._with_list(
                files[key], files[key[:-7] + '.uexp'], c['row'],
                [bytes.fromhex(g) for g in keep])
        out = _pack(c['pak'], dest, files)
        if installing and keep == c['missions']:
            installs[paksource.stem(c['pak'])] = {'pak': paksource.digest(out),
                                                  'source': paksource.digest(c['pak']),
                                                  'at': time.time()}
        camps.append({'campaign': c['title'], 'pak': name, 'missions': len(keep),
                      'left_out': len(c['missions']) - len(keep)})
        note('campaign %s: %d of %d missions' % (c['title'], len(keep), len(c['missions'])))
    if installing:
        _write_json(_meta('installs.json'), installs)
        # missions declare entitlements in their slot; the merged EntitlementsProtos
        # stays only while an installed pak still ships the table (riftlord/entitlements)
        from riftlord.build.world import entitlements
        merged = entitlements.merge(dest)
        if merged['from']:
            note('%s kept: %s still ship%s EntitlementsProtos; rebuild to drop it'
                 % (entitlements.MERGED_PAK, ', '.join(merged['from']),
                    's' if len(merged['from']) == 1 else ''))
        elif merged['removed']:
            note('removed %s: no installed pak ships EntitlementsProtos'
                 % entitlements.MERGED_PAK)
        elif merged['pak']:
            note('%s kept: could not read %s' % (entitlements.MERGED_PAK,
                                                 ', '.join(merged['unreadable'])))
    return {'dest': dest, 'paks': written, 'campaigns': camps}


#: ids of missions whose install waits for the game to close (install)
_WAITING_INSTALL = set()


def _install_targets(ids):
    """The paks in ~mods an install of `ids` may replace or remove: the missions' own and
    those of every mission their campaign packs (a campaign pak takes over from them), every
    campaign pak in ~mods (an install packs a campaign again, or removes it) and the merged
    entitlements pak."""
    from riftlord.build.world import entitlements
    mods = gamepath.mods_dir()
    if not mods:
        return []
    try:
        ids = [i for _c, unit in _units(ids, dest=mods) for i in unit]
    except Exception:                                # noqa: BLE001
        pass
    out = [os.path.join(mods, ident + '.pak') for ident in ids]
    out.append(os.path.join(mods, entitlements.MERGED_PAK))
    out += [os.path.join(mods, name) for name in sorted(os.listdir(mods))
            if name.endswith('_Campaign_P.pak')]
    return out


def install(ids, note=None):
    """Pack missions into the game's ~mods. While the game runs, an install that would
    replace or remove a pak it may have mounted carries on by itself in the background and
    completes once the game has closed (riftlord.core.pakguard): it returns at once with
    `waiting` set, and items() shows each such mission's install as 'waiting'."""
    from riftlord.core import console
    from riftlord.core import pakguard
    refuse_unbuilt(ids, 'installed')
    if not pakguard.would_wait(_install_targets(ids)):
        return export(ids, None, note)
    ids = list(ids)
    _WAITING_INSTALL.update(ids)
    console.info('%s: %s' % (pakguard.WAITING, ', '.join(ids)), console.PACKAGING)

    def run():
        try:
            export(ids, None, note)
        except BaseException as exc:                 # noqa: BLE001
            console.error('install of %s failed: %s' % (', '.join(ids), exc),
                          console.PACKAGING)
        finally:
            _WAITING_INSTALL.difference_update(ids)
            refresh()
    threading.Thread(target=run, name='install after the game closes', daemon=True).start()
    return {'waiting': True, 'message': pakguard.WAITING, 'paks': [i + '.pak' for i in ids],
            'campaigns': []}


def package(ids, zip_path=None):
    """Pack missions into a zip laid out to unzip into ~mods -- for sharing. Written
    to `zip_path` when given; otherwise (file name, bytes)."""
    from riftlord.core import console
    with tempfile.TemporaryDirectory(prefix='riftlord') as tmp:
        report = export(ids, dest=tmp, note=console.notes(console.PACKAGING))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            for name in sorted(os.listdir(tmp)):
                z.write(os.path.join(tmp, name), name)
        name = (report['paks'][0][:-4] if len(report['paks']) == 1
                else 'RiftLord_Missions_%d' % len(report['paks'])) + '.zip'
    console.info('packaged %d mission(s) into %s' % (len(report['paks']),
                                                     zip_path or name), console.PACKAGING)
    if zip_path:
        with open(zip_path, 'wb') as fh:
            fh.write(buf.getvalue())
        return {'path': zip_path, 'paks': report['paks'], 'campaigns': report['campaigns']}
    return name, buf.getvalue()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--rebuild', metavar='ID', action='append', default=[],
                    help='build a mission again from its record (as Build does)')
    ap.add_argument('--stale', action='store_true',
                    help='with --rebuild: only when out of date (record, carried mods or kits)')
    ap.add_argument('--install', action='store_true', help='with --rebuild: install it')
    args = ap.parse_args()
    if args.rebuild:
        from riftlord.core import console
        bad = 0
        for ident in args.rebuild:
            rec = load(ident)
            if rec is None:
                print('%s: no such mission' % ident)
                bad += 1
                continue
            changed = carried_changed(rec) + kits_changed(rec)
            if args.stale and not is_stale(rec):
                print('%s: up to date' % ident)
            else:
                if changed:
                    print('%s: %s updated since its last build' % (ident, ', '.join(changed)))
                try:
                    print('%s: built %s' % (ident, rebuild(ident, console.notes(console.MAP_COOKER))))
                except (Exception, SystemExit) as exc:   # noqa: BLE001
                    print('%s: not built: %s' % (ident, str(exc) or type(exc).__name__))
                    bad += 1
                    continue
            if args.install:
                wrong = carried_problems(ident) + kit_problems(ident)
                if wrong:
                    print('%s: not installed: %s' % (ident, '; '.join(wrong[:3])))
                    bad += 1
                    continue
                #: in this process: a pak the running game has mounted is replaced once
                #: it closes, waiting here (riftlord.core.pakguard prints the wait)
                got = export([ident])
                print('%s: installed %s' % (ident, ', '.join(got.get('paks') or []) or 'nothing'))
        raise SystemExit(1 if bad else 0)
    if args.list:
        got = items()
        print('missions: %s' % got['root'])
        for i in got['items']:
            print('  %-40s %-8s %-10s %s' % (i['id'], i['kind'], i['installed'],
                                            i['campaignTitle'] or ''))


if __name__ == '__main__':
    main()
