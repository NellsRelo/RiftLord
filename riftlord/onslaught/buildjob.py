"""Background pak builds, so a long build does not block the UI.

A build runs in a worker thread and the UI polls `/api/build/<id>` for its log
and outcome. The spec the UI posts is the declarative wave plan; turning that
into a coordinator is `wavespec`'s job.
"""

import threading
import traceback
import uuid

_JOBS = {}
_LOCK = threading.Lock()
#: Console levels a save hands to the editor's errors and warnings modal.
WARN_LEVELS = ('warn', 'error')
#: Console sources a save writes through: the editor itself, and a bake it starts.
WARN_SOURCES = ('Onslaught Editor', 'Map Cooker')


def status(job_id):
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def _run(job_id, spec):
    from riftlord.core import console
    console.thread_source(console.ONSLAUGHT_EDITOR)
    first = console.since(1 << 62)['next']
    console.info('saving %s' % (spec.get('title') or 'the onslaught'), console.ONSLAUGHT_EDITOR)

    def warnings():
        """Warnings and errors the save wrote to the console, for the editor's modal."""
        lines = console.since(first)['lines']
        got = [{'level': e['level'], 'text': e['text']} for e in lines
               if e['level'] in WARN_LEVELS and e['source'] in WARN_SOURCES]
        with _LOCK:
            _JOBS[job_id]['warnings'] = got

    def note(line):
        with _LOCK:
            _JOBS[job_id]['log'].append(str(line))
        console.debug(line, console.ONSLAUGHT_EDITOR)
    try:
        out = save(spec, note)
        console.info('saved %s' % (spec.get('title') or 'the onslaught'),
                     console.ONSLAUGHT_EDITOR)
        warnings()
        with _LOCK:
            _JOBS[job_id].update(state='done', result=out)
    except (Exception, SystemExit) as exc:                 # noqa: BLE001
        # SystemExit too: the converter refuses by `raise SystemExit`, which is a
        # BaseException, so an `except Exception` worker died without ever leaving
        # 'running' -- the page then polled a job that would never finish, which is
        # what a build that refused to write looked like from the outside.
        from riftlord.core import console
        warnings()
        console.error('not saved: %s' % (str(exc) or 'refused'), console.ONSLAUGHT_EDITOR)
        if isinstance(exc, SystemExit):
            note(str(exc) or 'refused')
        else:
            note('%s: %s' % (type(exc).__name__, exc))
            note(traceback.format_exc().rstrip())
        with _LOCK:
            _JOBS[job_id].update(state='failed', error=str(exc) or 'refused')


def save(spec, note=lambda _s: None):
    """Build `spec` where `placed` decides and record it in Missions. Returns the
    build's result, with `mission`, `kind` and `copied` when Missions holds it."""
    from riftlord.onslaught import wavespec
    from riftlord.missions import bucket
    #: the Details dialog's picture changes ({slot: value}), kept in the mission's record
    pictures = spec.get('pictures')
    spec = {k: v for k, v in spec.items() if k != 'pictures'}
    if spec.get('copy'):
        title_free(spec.get('title'))
    spec = placed(spec)
    #: each unit row saved with its current row name and its Guid (wavespec.resolve_units)
    spec = wavespec.resolve_units(spec, note=note, stamp=True)
    #: no wave sends units through a rift the Crystal opens (riftlord/mapeditor/rifttear.py)
    from riftlord.mapeditor import rifttear
    stuck = rifttear.spec_problems(spec)
    #: a map opened without its record: the rifts its built level's Crystal opens
    if not stuck and spec.get('map') and not isinstance(spec.get('riftTear'), dict):
        from riftlord.onslaught import gamemode, onslaught
        try:
            mode = gamemode.of_map(spec['map'], onslaught.map_report(spec['map'])['pieces'])
        except (Exception, SystemExit):              # noqa: BLE001
            mode = None
        stuck = gamemode.spec_problems(spec, mode)
    if stuck:
        raise SystemExit('; '.join(stuck))
    if bucket.ACTIVE and not spec.get('raw') and spec.get('out'):
        from riftlord.core import paksource
        got = bucket.take_pictures(paksource.stem(spec['out']), pictures)
        spec = dict(spec, **{wavespec.PICTURES_KEY: got})
        #: what beating it grants is set on the Missions page (missionunlocks); a spec that
        #: does not carry it keeps the mission's
        if 'unlocks' not in spec:
            from riftlord.missions import missionunlocks
            spec = dict(spec, unlocks=missionunlocks.of_record(
                bucket.load(paksource.stem(spec['out']))))
    #: `bundle` writes the onslaught INTO an installed map's own pak, so the
    #: author ships one file instead of a map pak plus an onslaught pak. It is
    #: refused for a shipped map, where it would mean redistributing base-game
    #: level assets.
    out = (wavespec.bundle(spec, note) if spec.get('bundle')
           else wavespec.build(spec, note))
    # Inside Rift Lord (the app) the pak went to its bucket: keep the spec beside it, so
    # the onslaught can be opened again for editing.
    kept = {k: v for k, v in spec.items()
            if k not in ('out', 'raw', 'mission', 'copy', 'modName', wavespec.PICTURES_KEY)}
    kept['details'] = bucket.without_files(spec.get('details'))
    if out.get('placement'):
        kept['intoCampaign'] = out['placement']
    #: a shipped campaign's Campaign Index goes into the build, so the spec keeps it
    from riftlord.missions import customcampaign
    index = (spec.get('details') or {}).get('campaignIndex') or spec.get('campaignIndex')
    if kept.get('intoCampaign') in customcampaign.SHIPPED and index not in (None, ''):
        kept['campaignIndex'] = bucket.shipped_index(kept['intoCampaign'], index)
    else:
        kept.pop('campaignIndex', None)
    rec = bucket.record(out.get('pak'), 'onslaught', map=spec.get('map'), spec=kept,
                        title=None if spec.get('bundle') else spec.get('title'),
                        details=None if spec.get('bundle') else kept['details'],
                        placement=None if spec.get('bundle') else kept.get('intoCampaign'),
                        resetDoors=bool(spec.get('resetDoors')))
    #: a Map Editor mission's details live in its record, as the Missions page sets them;
    #: a spec without details or a campaign leaves the record's as they are
    if rec and bucket._editable(rec):
        placement = spec.get('intoCampaign') or None
        chosen = (spec.get('details') or {}).get('newCampaign') or {}
        # as the Missions page sends them (workshop MissionSettings.svelte): a new
        # campaign's fields; an installed one is changed in its own editor
        new_campaign = chosen if placement == 'new' else None
        bucket.update_settings(rec['id'], details=kept['details'] or None,
                               placement=placement, new_campaign=new_campaign,
                               endless=spec.get('endless') if 'endless' in spec else None,
                               endless_only=bool(spec.get('endlessOnly'))
                               if 'endlessOnly' in spec else None,
                               scramble=(spec.get('scramble') or {'on': False})
                               if 'scramble' in spec else None,
                               campaign_index=kept.get('campaignIndex'))
    #: the Crystal's settings are written into its level, which only a Map Editor mission builds
    if isinstance(spec.get('riftTear'), dict) and not (rec and bucket._editable(rec)):
        from riftlord.core import console
        console.warn('Crystal Health and Rift Groups not saved: %s has no Map Editor mission '
                     'in Missions' % (spec.get('map') or 'this map'), console.ONSLAUGHT_EDITOR)
    if rec:
        out['mission'] = rec['id']
        out['kind'] = rec.get('kind')
        out['copied'] = bool(spec.get('copy'))
    return out


def titles():
    """Titles of the missions in Missions, which a copy's suggested name steps past. Empty
    outside Rift Lord."""
    from riftlord.missions import bucket
    if not bucket.ACTIVE:
        return []
    return sorted({i['title'] for i in bucket.items()['items'] if i.get('title')})


def title_free(title):
    """Refuse a copy's name that is empty. Two missions may share a title: each has an id,
    mod, Guids and level of its own (bucket._fresh_identity)."""
    name = (title or '').strip()
    if not name:
        raise SystemExit('Name the copy.')


def resave(ident, note=lambda _s: None, **changes):
    """Rebuild an Onslaught Editor mission from its stored wave spec with `changes`
    applied (for example resetDoors=True), into the same folder, mod and Guids."""
    from riftlord.missions import bucket
    rec = bucket.load(ident) or {}
    spec = rec.get('spec')
    if not isinstance(spec, dict) or not spec.get('waves'):
        raise SystemExit('%s has no waves saved by the Onslaught Editor, so it cannot be '
                         'rebuilt here' % (rec.get('title') or ident))
    spec = dict(spec, map=spec.get('map') or rec.get('map'), mission=ident, copy=False,
                **changes)
    return save(spec, note)


def placed(spec):
    """The spec with where it saves decided, inside Rift Lord.

    `mission` (a Missions id) saves into that mission: a Map Editor mission by bundling
    into it, any other by rebuilding it under its own folder and mod, which keeps its
    Guids. `copy`, or no `mission`, saves a new mission under names no build in Missions
    uses; a copy of a Map Editor mission is a new Missions record (`bucket.copy`) that is
    then saved into. Outside Rift Lord the spec is returned unchanged."""
    from riftlord.missions import bucket
    from riftlord.onslaught import wavespec
    if not bucket.ACTIVE or spec.get('raw'):
        return spec
    mission = spec.get('mission')
    is_map = mission and (bucket.load(mission) or {}).get('kind') == 'map'
    if is_map and spec.get('copy'):
        mission = bucket.copy(mission, spec.get('title'))
        bucket.target(mission)
        spec = dict(spec, mission=mission, map=(bucket.item(mission) or {}).get('map')
                    or spec.get('map'))
    if mission and (is_map or not spec.get('copy')):
        path, mod = bucket.target(mission)
        if is_map:
            return dict(spec, bundle=True, out=path)
        return dict(spec, bundle=False, out=path, modName=mod)
    return wavespec.fresh_names(dict(spec, bundle=False, modName=None))


def start(spec):
    job_id = uuid.uuid4().hex[:12]
    with _LOCK:
        _JOBS[job_id] = {'id': job_id, 'state': 'running', 'log': [],
                         'result': None, 'error': None}
    threading.Thread(target=_run, args=(job_id, spec), daemon=True).start()
    return {'id': job_id}
