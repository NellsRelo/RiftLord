<script>
  // Minimap on the left, the wave form on the right. Tabs are doors; waves are
  // sections down the form, with the Timeline tab holding what belongs to the
  // wave rather than to a door.
  import { listMaps, listUnits, listCampaigns, missionDefaults, bucketItem, mapDetail, startBuild, buildStatus, installStatus, readOnslaught, shutdown, rescan, missionTitles, previewSpec } from './lib/api.js'
  import { adopt, newSpec, newWave, newDetails, fromMap, review, sanitize, setDefaults, copyDoor, copyDoorWave, clearDoorWave, endlessDoors, endlessSummary, waveList, openedByMode, doorTab, doorLabel, CRYSTAL_DEFENSE, injectTear, tearForSave } from './lib/spec.js'
  import { forgetKept, prefetchMaps } from './lib/api.js'
  import { untrack } from 'svelte'
  import DoorCopy from './lib/DoorCopy.svelte'
  import MapPanel from './lib/MapPanel.svelte'
  import WaveSection from './lib/WaveSection.svelte'
  import Timeline from './lib/Timeline.svelte'
  import FindGame from './lib/FindGame.svelte'
  import Issues from './lib/Issues.svelte'
  import Details from './lib/Details.svelte'
  import PakOpener from './lib/PakOpener.svelte'
  import RiftGroup from './lib/RiftGroup.svelte'
  import TabStrip from './lib/TabStrip.svelte'
  import EnemyPreview from '../../shared/EnemyPreview.svelte'
  import { sendable } from '../../shared/LoadPicture.svelte'

  const TIMELINE = 'timeline'
  //: the hub's enemy preview tab (makepreview)
  const PREVIEW = 'preview'
  //: a Rift Group's tab (lib/RiftGroup.svelte): GROUP_TAB + its index
  const GROUP_TAB = 'group-'
  //: tabs that are not doors
  const OWN_TABS = [TIMELINE, PREVIEW]

  let maps = $state([])
  let units = $state([])
  let campaigns = $state([])
  let install = $state(null)
  //: Set when this page was built AFTER the server started, which means the server is
  //: running older code than the page it just served -- it reads `dist` off disk on every
  //: request, so the two drift apart the moment the UI is rebuilt. Without this the only
  //: symptom is a request failing on a route that process does not have.
  let stale = $state(null)

  $effect(() => {
    fetch('api/version')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error('no /api/version'))))
      .then((got) => {
        if (typeof __BUILD_TIME__ === 'number' && __BUILD_TIME__ > got.started * 1000) {
          stale = `running ${got.version} from before this page was built`
        }
      })
      .catch(() => { stale = 'running a version from before this check existed' })
  })

  //: Set once the editor has been asked to stop, so the page says so instead of
  //: looking merely unresponsive.
  let stopped = $state(false)

  /** Stop the editor. Without this the only way to close it is a terminal or a
   *  process manager, which is how one ended up running for two days on stale code. */
  async function stopEditor() {
    if (!confirm('Shut the Onslaught Editor down?')) return
    try {
      await shutdown()
    } catch (err) {
      // The server may go down before the reply lands; that is still a success.
    }
    stopped = true
  }
  let showIssues = $state(false)
  let showDetails = $state(false)
  //: the mission's pictures as the Details dialog shows them, and the changes the next
  //: save sends ({slot: value}; a slot left out is unchanged)
  let pictures = $state({ picture: null, portalPicture: null })
  let pictureChanges = $state({})
  //: The pak opener, which owns the file input. A map only reachable from a pak on disk
  //: cannot be picked from the list, which is otherwise built from what is installed.
  let opener = $state(null)

  /** A pak was read: refresh the picker so its maps are listed, then open the map the
   *  author chose -- or the only one it holds. */
  async function tookPak(got, which = null) {
    maps = await listMaps()
    const name = which ?? got.maps?.[0]?.name
    if (name) choose(name)
  }
  let consoleOpen = $state(true)
  //: Inside Rift Lord, which has its own way to quit, its own Missions bucket and the
  //: one theme switch for the whole app.
  //: Framed by the shell counts too, so a reload that lost the query stays embedded.
  const embedded = new URLSearchParams(location.search).has('embedded') || window.self !== window.top
  let theme = $state(new URLSearchParams(location.search).get('theme')
                     || (embedded ? null : localStorage.getItem('theme')) || 'dark')

  $effect(() => {
    document.documentElement.dataset.theme = theme
    if (!embedded) localStorage.setItem('theme', theme)
  })

  // Rift Lord's sidebar switches the theme for every page it shows.
  $effect(() => {
    if (!embedded) return
    const take = (e) => {
      if (e.source === window.parent && e.data?.type === 'riftlord-theme') theme = e.data.theme
      // the shell's Missions tab asks whether a map is open or opening (web/shell openState)
      if (e.source === window.parent && e.data?.type === 'riftlord-open-state') {
        window.parent.postMessage({ type: 'riftlord-open-state', id: e.data.id,
                                    open: !!detail || loading || opening }, '*')
      }
    }
    window.addEventListener('message', take)
    return () => window.removeEventListener('message', take)
  })
  // Inside Rift Lord the webview's own context menu (Reload) and reload keys are
  // turned off; text fields keep their menu for copy and paste.
  $effect(() => {
    if (!embedded) return
    const menu = (e) => {
      if (!e.target.closest?.('input, textarea, select, [contenteditable="true"]')) e.preventDefault()
    }
    const keys = (e) => {
      if (e.key === 'F5' || ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'r')) e.preventDefault()
    }
    window.addEventListener('contextmenu', menu)
    window.addEventListener('keydown', keys)
    return () => {
      window.removeEventListener('contextmenu', menu)
      window.removeEventListener('keydown', keys)
    }
  })
  let detail = $state(null)
  let spec = $state(newSpec())
  //: The Missions item being edited (Rift Lord only): Save writes into it, "Save as
  //: copy" makes a new one. Null for a map not yet saved to Missions.
  let editing = $state(new URLSearchParams(location.search).get('bucket'))
  //: Bundling writes into the chosen map's OWN pak, so it is offered only for a
  //: map the server reports as bundleable -- a mod map, never a shipped one.
  const bundleable = $derived(
    !!maps.find((m) => m.name === spec.map && m.bundle))
  let tab = $state(TIMELINE)
  let loading = $state(false)
  //: true while a Missions item is fetched, which builds it first when it changed
  let opening = $state(false)
  let error = $state(null)
  //: errors and warnings from loading and saving, [{level, where, text}], shown in the
  //: errors and warnings modal beside the form's own
  let notices = $state([])

  /** Add a load or save problem to the modal, once per text. */
  function notice(level, where, text) {
    if (!text || notices.some((n) => n.text === text)) return
    notices = [...notices, { level, where, text }]
  }

  /** Show a failure in the header and in the modal. */
  function fail(where, e) {
    error = e?.message ?? String(e)
    notice('error', where, error)
  }
  //: what a load from another map had to throw away, so the author is told
  //: rather than left with rows that read blank
  let dropped = $state([])
  let droppedTotal = $state(0)
  $effect(() => {
    const now = dropped
    untrack(() => { for (const d of now) notice('warn', 'Load', `${d.count} left out: ${d.why}`) })
  })
  let job = $state(null)            // {state, log, result, error} while building
  //: the saved/failed notice was dismissed (inside Rift Lord, which shows no console here)
  let jobSeen = $state(false)

  // a successful save's notice goes by itself; a failure stays until dismissed
  $effect(() => {
    if (job?.state !== 'done') return
    const t = setTimeout(() => (jobSeen = true), 4000)
    return () => clearTimeout(t)
  })

  /** Rift Lord's console, which holds every build's log. */
  function openConsole() {
    window.parent?.postMessage({ type: 'riftlord-console' }, '*')
  }
  let form = $state(null)                    // the scrolling form, for wave jumps

  $effect(() => {
    installStatus().then((got) => {
      install = got
      if (!got.found) { booting = false; return }
      load()
    }).catch((e) => { fail('Load', e); booting = false })
  })

  //: True while the paks are first read, which the loading modal covers.
  let booting = $state(true)
  //: True while a Rescan runs.
  let rescanning = $state(false)

  /** Read the paks again, for a mod installed or a mission saved elsewhere. */
  async function doRescan() {
    rescanning = true
    error = null
    try {
      maps = await rescan()
      forgetKept()
    } catch (e) {
      fail('Rescan', e)
    } finally {
      rescanning = false
    }
  }

  function load() {
    booting = true
    Promise.all([listMaps(), listUnits(), listCampaigns(), missionDefaults().catch(() => ({}))])
      .then(([m, u, c, d]) => {
        maps = m
        units = u
        campaigns = c
        setDefaults(d, c)
        spec = { ...spec, ...(spec.map ? {} : newSpec()) }
        // ?map=Scenario_13&tab=door-2 reopens where the author left off;
        // ?bucket=<item> reopens something the Workshop's bucket holds
        const q = new URLSearchParams(location.search)
        if (q.get('bucket')) openBucketItem(q.get('bucket'))
        else if (q.get('map')) choose(q.get('map'), q.get('tab'))
      })
      .catch((e) => { fail('Load', e) })
      .finally(() => { booting = false })
  }

  let byCampaign = $derived.by(() => {
    const out = new Map()
    for (const m of maps) {
      if (!out.has(m.campaign)) out.set(m.campaign, [])
      out.get(m.campaign).push(m)
    }
    return [...out]
  })

  // Drastic Steps ships with the base game now, so flagging it tells nobody
  // anything; the two real purchases get a colour each.
  const BADGES = {
    DLC4: { label: 'Cold as Eyes', tone: 'ice' },
    DLC5: { label: 'Tipping the Scales', tone: 'sand' },
  }
  let badge = $derived(BADGES[detail?.can?.entitlement?.rows?.[0]] ?? null)

  let doorCount = $derived((detail?.doors ?? []).filter((d) => d.kind === 'door').length)
  let riftCount = $derived((detail?.doors ?? []).filter((d) => d.kind === 'rift').length)
  //: the form's own checks; an error here blocks saving
  let checks = $derived(review(spec, detail?.can, detail, units))
  let blocking = $derived(checks.filter((p) => p.level === 'error'))
  let problems = $derived([
    ...checks,
    ...(detail?.can?.authorable === false ? [{ level: 'error', where: 'Map', text: detail.can.reason }] : []),
    ...notices])
  let errors = $derived(problems.filter((p) => p.level === 'error'))
  let warnings = $derived(problems.filter((p) => p.level === 'warn'))

  // The door the map panel should show as active; the Timeline and preview tabs own none.
  let activeDoor = $derived(OWN_TABS.includes(tab) || tab.startsWith(GROUP_TAB) ? null : tab)
  let door = $derived(doorTabs.find((d) => d.key === tab) ?? null)
  //: the mission's game mode (riftlord/onslaught/gamemode.py): {gameType, label, hide, opens}
  let mode = $derived(detail?.mode ?? null)
  //: the open Rift Group tab's index, or null
  let groupAt = $derived(tab.startsWith(GROUP_TAB) ? Number(tab.slice(GROUP_TAB.length)) : null)
  let group = $derived(groupAt === null ? null : spec.riftTear?.groups?.[groupAt] ?? null)
  //: door tabs: every door entry but the Unstable Rifts the mode's piece opens, which
  //: belong to its Rift Groups' tabs
  let doorTabs = $derived((detail?.doors ?? []).filter((d) => !openedByMode(d, mode)))
  //: {spawner id in the view: indexInLevel} of the Crystal's Unstable Rifts
  let riftIndex = $derived(new Map((spec.riftTear?.rifts ?? []).map((r) => [r.id, r.index])))
  /** An Unstable Rift's name as the map shows it, from its id in the Crystal's view. */
  function riftName(id) {
    const index = riftIndex.get(id) ?? id
    const d = (detail?.doors ?? []).find((x) => x.spawners.includes(index))
    return d ? doorTab(d) : `Unstable Rift ${index}`
  }
  //: the indexInLevel of the open Rift Group's rifts, which the map singles out
  let groupRifts = $derived(group ? (group.rifts ?? []).map((r) => riftIndex.get(r.spawner) ?? r.spawner) : null)
  //: every tab: [{key, text, title, cls}]
  let tabs = $derived([
    { key: TIMELINE, text: 'Timeline' },
    { key: PREVIEW, text: 'Enemy Preview', title: 'The enemies the mission hub shows for this onslaught' },
    ...(spec.riftTear?.groups ?? []).map((g, gi) => ({ key: GROUP_TAB + gi, text: g.name, cls: 'rift' })),
    ...doorTabs.map((d) => ({ key: d.key, text: doorTab(d), door: d,
      cls: d.kind === 'rift' ? 'rift' : d.kind === 'spawner' ? 'spawner' : '',
      title: d.pathing === 'fly' ? `${doorLabel(d)} — flyer route, only Hellbats`
        : d.pathing === 'both' ? `${doorLabel(d)} — walking and flyer routes` : doorLabel(d) })),
  ])

  /** A map marker picked: an Unstable Rift a Rift Group holds opens the group's tab. */
  function pickOnMap(key) {
    const d = (detail?.doors ?? []).find((x) => x.key === key)
    if (d && openedByMode(d, mode)) {
      const gi = (spec.riftTear?.groups ?? []).findIndex((g) => (g.rifts ?? [])
        .some((r) => d.spawners.includes(riftIndex.get(r.spawner) ?? r.spawner)))
      if (gi >= 0) { tab = GROUP_TAB + gi; return }
    }
    tab = key
  }

  /** The Crystal's view onto the spec: the record's, else the map's own (a level opened
   *  without its record reads the same), its per-wave rows and chances on the waves. */
  function takeTear(view) {
    spec.riftTear = view ? structuredClone($state.snapshot(view)) : null
    injectTear(spec)
  }

  /** Open a bucket item: its map, and the onslaught it was last built with. A map
   *  Rift Lord made is edited by bundling into its own pak, so the item stays one pak. */
  async function openBucketItem(id) {
    try {
      opening = true
      const rec = await bucketItem(id).finally(() => { opening = false })
      if (!rec.map) throw new Error(`${rec.title || id} has no map to open`)
      await choose(rec.map, null, { own: false })
      if (!detail) return
      if (rec.eventCaps) detail = { ...detail, events: { ...(detail.events ?? {}), ...rec.eventCaps } }
      //: the game mode of the mission's plan (bucket.item)
      if (rec.mode) detail = { ...detail, mode: rec.mode }
      editing = id
      pictures = { picture: rec.pictureView ?? null, portalPicture: rec.portalPictureView ?? null }
      pictureChanges = {}
      const base = newSpec(rec.map)
      if (rec.spec) {
        adopt(rec.spec)
        spec = { ...base, ...rec.spec, map: rec.map,
                 details: { ...base.details, ...(rec.spec.details ?? {}),
                            newCampaign: { ...base.details.newCampaign,
                                           ...(rec.spec.details?.newCampaign ?? {}),
                                           banner: null } } }
      } else {
        // no waves saved yet: the record's details and campaign, and the map's onslaught
        spec = { ...base, title: rec.title || detail.title,
                 details: { ...base.details, ...(rec.details ?? {}),
                            newCampaign: { ...base.details.newCampaign,
                                           ...(rec.details?.newCampaign ?? {}),
                                           banner: null } },
                 ...(rec.placement ? { intoCampaign: rec.placement } : {}),
                 ...(rec.endless != null ? { endless: !!rec.endless } : {}),
                 ...(rec.endlessOnly ? { endlessOnly: true } : {}) }
        await loadFromMap(null, { auto: true })
        if (rec.events) spec.events = rec.events
      }
      if (rec.kind === 'map') spec.bundle = true
      spec.resetDoors = !!(rec.resetDoors ?? rec.spec?.resetDoors)
      //: the Scramble tiers it joins (bucket.item)
      spec.scramble = rec.scramble ?? null
      //: the Crystal's Rift Groups and health (bucket.item, riftlord/mapeditor/rifttear.py)
      takeTear(rec.riftTear ?? detail.riftTear)
    } catch (e) {
      fail('Open mission', e)
    }
  }

  /** Open a map. `own` (the default) fills the form with the map's onslaught. */
  async function choose(name, want = null, { own = true } = {}) {
    if (!name) return
    // Clear the old map first: reading one takes a moment, and leaving the
    // previous minimap up makes it look as though nothing happened.
    detail = null
    editing = null
    pictures = { picture: null, portalPicture: null }
    pictureChanges = {}
    copied = null
    loadedEarly = null
    loading = true
    error = null
    notices = []
    try {
      detail = await mapDetail(name)
      // the author and version carry over to the next map; the rest is per mission
      spec = { ...newSpec(name), details: newDetails(spec.details) }
      tab = detail.doors.some((d) => d.key === want) || want === PREVIEW
        || String(want ?? '').startsWith(GROUP_TAB) ? want : TIMELINE
    } catch (e) {
      fail('Load map', e)
      detail = null
    } finally {
      loading = false
    }
    if (detail && own) await loadFromMap(null, { auto: true })
  }

  function addWave() {
    spec.waves.push(newWave())
    // the author is adding it to work on it, so bring it into view
    requestAnimationFrame(() => jump(spec.waves.at(-1).id))
  }

  const removeWave = (id) => {
    if (spec.waves.length > 1) spec.waves = spec.waves.filter((w) => w.id !== id)
  }

  $effect(() => {
    if (!detail) return
    const q = new URLSearchParams({ map: detail.map })
    if (tab !== TIMELINE) q.set('tab', tab)
    // what the shell passed in survives a reload
    if (embedded) q.set('embedded', '1')
    const shellTheme = new URLSearchParams(location.search).get('theme')
    if (shellTheme) q.set('theme', theme)
    if (editing) q.set('bucket', editing)
    history.replaceState(null, '', '?' + q)
  })

  // a door tab opened: read every map ahead, for Copy Door Waves (api.prefetchMaps)
  $effect(() => {
    if (door && maps.length) untrack(() => prefetchMaps(maps.map((m) => m.name)))
  })

  //: open state of the Export button's menu
  let exportMenu = $state(false)

  let cannotBuild = $derived(
    !detail || blocking.length > 0 || job?.state === 'running'
    || detail?.can?.authorable === false)
  let cannotBuildWhy = $derived(
    detail?.can?.authorable === false ? detail.can.reason
    : blocking.length ? 'Fix the errors first'
    : job?.state === 'running' ? 'A build is already running' : '')

  /**
   * Build. `raw` writes the uasset/uexp tree instead of packing it: the same
   * build and the same checks, only the last step differs.
   */
  async function build(raw = false, copy = false, title = null) {
    exportMenu = false
    job = { state: 'running', log: [], result: null, error: null }
    jobSeen = false
    notices = notices.filter((n) => n.where !== 'Save')
    try {
      consoleOpen = true
      const sent = Object.fromEntries(Object.entries(pictureChanges)
        .map(([slot, p]) => [slot, sendable(p) ?? null]))
      // an installed mod map's onslaught is written into its own pak (wavespec.bundle);
      // inside Rift Lord the server decides from the mission being saved (buildjob.placed)
      const { id } = await startBuild({ ...tearForSave($state.snapshot(spec)), map: detail.map, raw,
                                        bundle: embedded ? !!spec.bundle : bundleable && !raw,
                                        title: title ?? spec.title,
                                        mission: embedded ? editing : null, copy,
                                        ...(embedded && Object.keys(sent).length
                                          ? { pictures: sent } : {}) })
      for (;;) {
        const got = await buildStatus(id)
        job = got
        if (got.state !== 'running') break
        await new Promise((r) => setTimeout(r, 600))
      }
      for (const w of job.warnings ?? []) notice(w.level, 'Save', w.text)
      if (job.state === 'failed') notice('error', 'Save', job.error || 'The save stopped without saying why.')
      if (embedded && job.state === 'done' && job.result?.mission) {
        editing = job.result.mission
        if (title != null) spec.title = title
        spec.bundle = job.result.kind === 'map'
        pictureChanges = {}
      }
      // the saved map's onslaught is read again when copied from
      if (job.state === 'done') forgetKept(detail.map)
      // a new campaign's banner goes with the save that chose it
      if (job.state === 'done' && spec.details?.newCampaign?.banner) {
        spec.details.newCampaign.banner = null
      }
      // a Campaign Index is applied by the save that sent it
      if (job.state === 'done' && spec.details?.campaignIndex != null) {
        spec.details.campaignIndex = null
      }
      // A build can create a campaign or change what one lists.
      if (job.state === 'done' || job.result) {
        campaigns = await listCampaigns()
        const made = campaigns.find((c) => c.kind === 'custom'
          && c.label === spec.details.newCampaign.name.trim())
        if (spec.intoCampaign === 'new' && made) spec.intoCampaign = made.name
      }
    } catch (e) {
      job = { state: 'failed', log: [], error: e.message }
      notice('error', 'Save', e.message)
    }
  }

  // The map's own onslaught, as a starting point to edit rather than a blank
  // form. Reading it takes as long as reading the map did.
  let pickSource = $state(false)

  /**
   * Fill the form from a map's own onslaught. Borrowing from another map keeps
   * only the spawners this map actually has -- a wave aimed at a door that is
   * not here would spawn nowhere.
   */
  async function loadFromMap(from = null, { auto = false } = {}) {
    if (!detail) return
    pickSource = false
    loading = true
    try {
      const got = await readOnslaught(from ?? detail.map)
      if (from) {
        const mine = new Set(detail.spawners.map((s) => String(s.index)))
        for (const w of got.waves) {
          w.spawns = Object.fromEntries(
            Object.entries(w.spawns).filter(([k]) => mine.has(k)))
        }
        const kept = got.waves.filter((w) => Object.keys(w.spawns).length)
        got.waves = kept.length ? kept : got.waves
        got.events = (got.events ?? []).filter((e) => e.kind !== 'boss' || mine.has(String(e.door)))
      }
      spec = fromMap(got, { ...spec, title: spec.title || detail.title })
      if (!auto) loadedEarly = { from: got.title, doors: endlessSummary(endlessDoors(spec, detail)) }
      // Whatever this map cannot actually run goes now, named, rather than
      // arriving as a row with nothing selectable in it.
      const clean = sanitize(spec.waves, detail, units)
      dropped = clean.dropped
      droppedTotal = clean.total
      spec.waves = clean.waves.length ? clean.waves : [newWave()]
      takeTear(spec.riftTear ?? detail.riftTear)
    } catch (e) {
      fail('Load onslaught', e)
    } finally {
      loading = false
    }
  }

  //: the doors an onslaught loaded from a map opens early in Endless, until dismissed
  let loadedEarly = $state(null)
  //: per door key, where the onslaught opens it early in Endless (endlessDoors)
  let early = $derived(endlessDoors(spec, detail))

  //: "Save as copy" naming dialog: {name, taken, error} while open
  let naming = $state(null)

  /** A free name for a copy: "<title> copy", numbered when that is taken. */
  function copyName(title, taken) {
    const base = `${(title || 'Mission').trim()} copy`
    let name = base
    for (let n = 2; taken.has(name.toLowerCase()); n++) name = `${base} ${n}`
    return name
  }

  async function askCopyName() {
    exportMenu = false
    let titles = []
    try { titles = await missionTitles() } catch (e) { fail('Save as copy', e) }
    const taken = new Set(titles.map((t) => t.trim().toLowerCase()))
    naming = { name: copyName(spec.title, taken), taken }
  }

  //: two missions may share a name: each has its own id, mod and Guids
  let namingProblem = $derived(!naming ? '' : !naming.name.trim() ? 'Enter a name.' : '')

  function saveCopy() {
    if (namingProblem) return
    const title = naming.name.trim()
    naming = null
    build(false, true, title)
  }

  //: open state of the door-copy menu
  let copyMenu = $state(false)
  //: what the last door copy brought in, shown until dismissed
  let copied = $state(null)

  /** Put another door's waves into the active door, dropping what it cannot run: every
   *  wave, or (`one`: {wave, into}) one source wave into one of this door's waves. */
  function takeDoor(rows, from, one = null) {
    copyMenu = false
    const rest = door.spawners.slice(1)
    if (one) {
      copyDoorWave(spec, door.spawners[0], rows, one.into)
      clearDoorWave(spec.waves[one.into], rest)
    } else {
      copyDoor(spec, door.spawners[0], rows)
      for (const w of spec.waves) clearDoorWave(w, rest)
    }
    const clean = sanitize(spec.waves, detail, units)
    const byId = new Map(clean.waves.map((w) => [w.id, w]))
    spec.waves = spec.waves.map((w) => byId.get(w.id) ?? { ...w, spawns: {} })
    dropped = clean.dropped
    droppedTotal = clean.total
    const here = endlessDoors(spec, detail)[door.key]
    copied = { from: one ? `wave ${one.wave + 1} of ${from}` : `the waves of ${from}`,
               into: one ? one.into + 1 : null,
               early: here ? here.early.size : 0, waves: here?.waves ?? [],
               label: door.label }
  }

  function saveSpec() {
    const blob = new Blob([JSON.stringify(spec, null, 2)],
                          { type: 'application/json' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = (spec.title || 'onslaught').replace(/[^\w.-]+/g, '_') + '.json'
    a.click()
    URL.revokeObjectURL(a.href)
  }

  function jump(id) {
    form?.querySelector(`#wave-${id}`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
</script>

{#if stopped}
  <div class="stopped">
    <h1>The editor has stopped</h1>
    <p>Its port is free. Close this tab; run it again when you need it.</p>
  </div>
{:else if install && !install.found}
  <FindGame status={install} onfound={(got) => { install = got; load() }} />
{:else}
<div class="app">
  {#if stale}
    <div class="stale" role="alert">
      <b>Restart the editor.</b> It is {stale}, so what you see here and what answers
      your clicks are different versions.
      <button class="ghost" onclick={stopEditor}>Shut down</button>
    </div>
  {/if}
  <header class="bar">
    <h1>Onslaught <span>Editor</span></h1>
    <select value={detail?.map ?? ''} onchange={(e) => choose(e.currentTarget.value)}
            aria-label="Map">
      <option value="">— choose a map —</option>
      {#each byCampaign as [campaign, entries]}
        <optgroup label={campaign}>
          {#each entries as m}
            <option value={m.name}>{m.title}</option>
          {/each}
        </optgroup>
      {/each}
    </select>
    <button class="pak" onclick={() => opener?.pick()}
            title="Choose a pak file and edit a map from it">Open Pak</button>
    <button class="pak" onclick={doRescan} disabled={rescanning || booting}
            title="Read the game's paks again, to pick up mods and missions added since">
      {rescanning ? 'Rescanning…' : 'Rescan'}</button>

    {#if badge}
      <span class="badge {badge.tone}"
            title="This map belongs to {badge.label}; the mission keeps that requirement.">
        {badge.label}
      </span>
    {/if}
    {#if mode}
      <span class="badge mode" class:crystal={mode.gameType === CRYSTAL_DEFENSE}
            data-mode={mode.gameType}>{mode.label}</span>
    {/if}
    {#if detail}
      <input class="title" bind:value={spec.title} placeholder="Name your onslaught"
             class:bad={!spec.title.trim()} aria-label="Onslaught name" />
      <button class="pak" onclick={() => (showDetails = true)}
              title="Author, version, description and campaign, as OrcsMustUI shows them">Details…</button>
    {/if}

    <span class="grow"></span>
    {#if error}<span class="err">{error}</span>{/if}
    {#if problems.length}
      <button class="issues" onclick={() => (showIssues = true)}
              title="Show errors and warnings">
        {#if errors.length}<span class="err">{errors.length} error{errors.length === 1 ? '' : 's'}</span>{/if}
        {#if errors.length && warnings.length}<span class="sep">·</span>{/if}
        {#if warnings.length}<span class="wrn">{warnings.length} warning{warnings.length === 1 ? '' : 's'}</span>{/if}
      </button>
    {/if}
    {#if !embedded}
    <button class="theme" onclick={() => (theme = theme === 'dark' ? 'light' : 'dark')}
            title="Switch between light and dark" aria-label="Switch theme">
      {#if theme === 'dark'}
        <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
          <path d="M21 13.2A9 9 0 1 1 10.8 3a7.2 7.2 0 0 0 10.2 10.2z" />
        </svg>
      {:else}
        <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
          <circle cx="12" cy="12" r="5" />
          <g stroke="currentColor" stroke-width="2.2" stroke-linecap="round">
            <path d="M12 1.5v3M12 19.5v3M1.5 12h3M19.5 12h3
                     M4.6 4.6l2.1 2.1M17.3 17.3l2.1 2.1
                     M19.4 4.6l-2.1 2.1M6.7 17.3l-2.1 2.1" />
          </g>
        </svg>
      {/if}
    </button>
    {/if}
    {#if embedded}
      <!-- Inside Rift Lord a mission is saved to its Missions bucket, unpacked; it is
           packed into a pak only when it is installed from there. -->
      <span class="split export">
        <button class="primary" onclick={() => build(false)} disabled={cannotBuild}
                title={cannotBuild ? cannotBuildWhy
                       : editing ? 'Save your changes to this mission'
                       : 'Save as a new mission in Missions'}>
          {#if job?.state === 'running'}<span class="btn-spin" aria-hidden="true"></span> Saving…{:else}Save Mission{/if}
        </button>
        {#if editing}
          <button class="primary caret" onclick={() => (exportMenu = !exportMenu)}
                  disabled={cannotBuild} aria-label="Other ways to save"
                  aria-expanded={exportMenu} title="Other ways to save">▾</button>
          {#if exportMenu}
            <div class="scrim" onclick={() => (exportMenu = false)}
                 role="presentation"></div>
            <div class="menu right">
              <button class="item" onclick={askCopyName}>Save as copy</button>
            </div>
          {/if}
        {/if}
      </span>
    {:else}
    <span class="split export">
      <button class="primary" onclick={() => build(false)} disabled={cannotBuild}
              title={cannotBuild ? cannotBuildWhy : 'Build and install the pak'}>
        {job?.state === 'running' ? 'Exporting…' : 'Export'}
      </button>
      <button class="primary caret" onclick={() => (exportMenu = !exportMenu)}
              disabled={cannotBuild} aria-label="Other export formats"
              aria-expanded={exportMenu} title="Other export formats">▾</button>
      {#if exportMenu}
        <!-- a click anywhere else closes the menu, so it cannot sit open over
             the form after the pointer has moved on -->
        <div class="scrim" onclick={() => (exportMenu = false)}
             role="presentation"></div>
        <div class="menu right">
          <button class="item" onclick={() => build(true)}>
            Export Raw Assets
            <em>the uasset/uexp files, loose, instead of a pak</em>
          </button>
        </div>
      {/if}
    </span>
    {/if}
  </header>

  {#if embedded && job && job.state !== 'running' && !jobSeen}
    <div class="outcome" class:bad={job.state === 'failed'} role={job.state === 'failed' ? 'alert' : 'status'}>
      {#if job.state === 'failed'}
        <strong>The mission was not saved.</strong>
        <span class="why">{job.error || 'The build stopped without saying why.'}</span>
        <button class="ghost" onclick={() => (showIssues = true)}>Errors and warnings</button>
        <button class="ghost" onclick={openConsole}>Open console</button>
      {:else}
        <strong>{job.result?.copied ? 'Saved as a new mission.' : 'Saved to Missions.'}</strong>
        {#each (job.result?.events ?? []).filter((e) => e.status !== 'applied') as e}
          <span class="why">Wave {e.wave} {e.kind}: {e.status}</span>
        {/each}
      {/if}
      <button class="ghost" onclick={() => (jobSeen = true)} aria-label="Dismiss">✕</button>
    </div>
  {/if}

  {#if copied}
    <div class="dropped ok" role="status">
      <strong>Copied {copied.from}{copied.into ? ` into wave ${copied.into}` : ''}.</strong>
      {#if copied.early}
        <p>They include {copied.early} Endless row{copied.early === 1 ? '' : 's'} that send
          units through {copied.label} from Endless loop 2 in
          wave{copied.waves.length === 1 ? '' : 's'} {waveList(copied.waves)}, where loop 1
          sends nothing through it. They are marked on the door's waves.</p>
      {/if}
      <button class="ghost" onclick={() => (copied = null)} aria-label="Dismiss">✕</button>
    </div>
  {/if}

  {#if loadedEarly?.doors}
    <div class="dropped" role="status">
      <strong>{loadedEarly.from}'s onslaught opens doors early in Endless.</strong>
      <p>From Endless loop 2 these doors also send units in waves where loop 1 leaves them
        closed: {loadedEarly.doors}. The rows that do it are marked on each door.</p>
      <button class="ghost" onclick={() => (loadedEarly = null)} aria-label="Dismiss">✕</button>
    </div>
  {/if}

  {#if dropped.length}
    <div class="dropped" role="status">
      <strong>{droppedTotal} {droppedTotal === 1 ? 'spawn or event' : 'spawns and events'} this map
        cannot run {droppedTotal === 1 ? 'was' : 'were'} left out</strong>
      <ul>
        {#each dropped.slice(0, 4) as d}
          <li><b>×{d.count}</b> {d.why}</li>
        {/each}
        {#if dropped.length > 4}
          <li>…and {dropped.length - 4} other reason{dropped.length === 5 ? '' : 's'}</li>
        {/if}
      </ul>
      <button class="ghost" onclick={() => (dropped = [])}
              aria-label="Dismiss">✕</button>
    </div>
  {/if}

  <main>
    <section class="left">
      <MapPanel {detail} {mode} active={activeDoor} {groupRifts} onpick={pickOnMap} />
      {#if job && consoleOpen && !embedded}
        <div class="console" class:bad={job.state === 'failed'} role="status">
          <header onclick={() => (consoleOpen = false)} role="button" tabindex="0"
                  onkeydown={(e) => e.key === 'Enter' && (consoleOpen = false)}>
            <h4>
              {embedded
                ? (job.state === 'running' ? 'Saving…'
                   : job.state === 'failed' ? 'Save failed' : 'Saved to Missions')
                : (job.state === 'running' ? 'Exporting…'
                   : job.state === 'failed' ? 'Export failed' : 'Exported')}
            </h4>
            <span class="grow"></span>
            <button class="ghost" onclick={(e) => { e.stopPropagation(); job = null }}
                    aria-label="Clear">✕</button>
          </header>
          <pre>{(job.log ?? []).join('\n')}</pre>
        </div>
      {/if}

      {#if detail}
        <div class="facts">
          <span><b>{detail.title}</b></span>
          <span>{doorCount} {doorCount === 1 ? 'door' : 'doors'}</span>
          {#if riftCount}<span>{riftCount} unstable {riftCount === 1 ? 'rift' : 'rifts'}</span>{/if}
          <span>{detail.spawners.length} spawners</span>
          {#if detail.volumes > 1}<span>{detail.volumes} floors</span>{/if}
          <span class="grow"></span>
          <!-- served by the API, so this never carries a second copy to drift -->
          {#if install?.version}<span class="ver">v{install.version}</span>{/if}
          {#if !embedded}
            <button class="quit" onclick={stopEditor}
                    title="Stop the editor and free its port">Shut down</button>
          {/if}
          {#if job && !embedded}
            <button class="console-tab" class:bad={job.state === 'failed'}
                    onclick={() => (consoleOpen = !consoleOpen)}>Console</button>
          {/if}
          {#if detail.can.maxUnits}
            <span title="This map names its enemies through Blueprint variables rather than in each spawn call, so it can field at most this many different units.">
              up to {detail.can.maxUnits} unit types
            </span>
          {/if}
        </div>
      {/if}
    </section>

    <section class="right">
      {#if !detail}
        <div class="blank"></div>
      {:else}
        <TabStrip {tabs} active={tab} {early} onselect={(key) => (tab = key)} />

        <div class="jump">
          <span class="lab">Jump to</span>
          {#each spec.waves as w, i (w.id)}
            <button class="ghost chip" onclick={() => jump(w.id)}>{i + 1}</button>
          {/each}
          <button class="go chip" onclick={addWave}>+ Wave</button>
          <span class="grow"></span>
          <!-- Only for a map that came from a mod pak. Writing the onslaught into
               a shipped map's pak would mean redistributing base-game assets, so
               the server refuses it and the option is not offered. -->
          {#if door}
            <span class="split copy">
              <button class="load chip" onclick={() => (copyMenu = !copyMenu)}
                      aria-expanded={copyMenu}
                      title="Copy what a door of the same type sends, from this or another map">
                Copy Door Waves ▾
              </button>
              {#if copyMenu}
                <DoorCopy groups={byCampaign} {detail} {door} waves={spec.waves.length}
                          onpick={takeDoor}
                          onclose={() => (copyMenu = false)} />
              {/if}
            </span>
          {/if}
          <span class="split">
            <button class="load chip" onclick={() => loadFromMap()}
                    title="Fill the form with this map's waves">
              Load onslaught from map
            </button>
            <button class="load chip caret" onclick={() => (pickSource = !pickSource)}
                    title="Load from another map" aria-label="Load from another map">▾</button>
            {#if pickSource}
              <div class="menu">
                <p>Other map</p>
                <!-- a real list, not a <select>: browsers ignore hover styling
                     on an <option>, and the group headings have to read as
                     headings rather than as choices -->
                <div class="maps" role="listbox" tabindex="-1">
                  {#each byCampaign as [campaign, entries]}
                    <div class="group">{campaign}</div>
                    {#each entries.filter((m) => m.name !== detail.map) as m}
                      <button class="pick" role="option" aria-selected="false"
                              onclick={() => loadFromMap(m.name)}>{m.title}</button>
                    {/each}
                  {/each}
                </div>
                <p class="note">
                  Spawns for doors not present in current map are discarded.
                </p>
              </div>
            {/if}
          </span>
        </div>

        <div class="form" bind:this={form}>
          {#if tab === TIMELINE}
            <Timeline {spec} {detail} {mode} {units} {riftName} onremovewave={removeWave} />
          {:else if tab === PREVIEW}
            <div class="preview-tab">
              <EnemyPreview options={spec.preview ?? {}}
                            load={(o) => previewSpec({ ...tearForSave($state.snapshot(spec)), map: detail.map }, o)}
                            onchange={(o) => (spec.preview = o)} />
            </div>
          {:else if group}
            <RiftGroup {group} gi={groupAt} {spec} {units} {riftName} onremovewave={removeWave} />
          {:else}
            {#if early[door?.key]}
              <p class="endless-door" role="note">
                In Endless, loop 1 sends nothing through {door.label} in
                wave{early[door.key].waves.length === 1 ? '' : 's'} {waveList(early[door.key].waves)},
                but from loop 2 on the marked Endless rows send units through it there.
                {#if spec.resetDoors}
                  Reset door after Onslaught Loop is on: when the mission is built, those rows
                  go through the doors loop 1 uses in the same wave (one row to each in turn),
                  so every loop repeats loop 1's doors. Units, counts and Endless growth are
                  kept; a door the players break stays broken.
                {/if}
              </p>
            {/if}
            {#each spec.waves as wave, i (wave.id)}
              <WaveSection {wave} index={i} {door} {units} {spec} early={early[door?.key] ?? null}
                           onremovewave={() => removeWave(wave.id)} />
            {/each}
          {/if}

          {#if detail?.can?.authorable === false}
            <div class="review">
              <h4>This map cannot be authored</h4>
              <p class="error">{detail.can.reason}</p>
            </div>
          {/if}
        </div>
      {/if}
    </section>
  </main>

  {#if loading || booting || rescanning || opening}
    <div class="loading" role="status" aria-live="polite">
      <div class="spinner" aria-hidden="true"></div>
      <p>{opening ? 'Opening the mission (building it first if it changed)…'
          : loading ? 'Reading the map…' : 'Loading the game\'s paks…'}</p>
    </div>
  {/if}

  <PakOpener bind:this={opener} onopen={tookPak} />
  {#if showDetails}
    <Details {spec} {campaigns} mission={embedded ? editing : null} map={detail?.map}
             {pictures} allowPicture={embedded}
             onpictures={(slot, p) => { pictures[slot] = p; pictureChanges[slot] = p }}
             onclose={() => { showDetails = false; listCampaigns().then((c) => { campaigns = c }).catch(() => {}) }} />
  {/if}
  {#if showIssues}
    <Issues {problems} onclose={() => (showIssues = false)} />
  {/if}
  {#if naming}
    <div class="name-scrim" onclick={() => (naming = null)} role="presentation"></div>
    <form class="name-modal" role="dialog" aria-label="Save as copy"
          onsubmit={(e) => { e.preventDefault(); saveCopy() }}
          onkeydown={(e) => { if (e.key === 'Escape') naming = null }}>
      <h3>Save as copy</h3>
      <label><span>Name</span>
        <!-- svelte-ignore a11y_autofocus -->
        <input bind:value={naming.name} class:bad={!!namingProblem} autofocus
               aria-label="Name of the new mission" /></label>
      {#if namingProblem}<p class="hint err">{namingProblem}</p>{/if}
      <footer>
        <button type="button" class="ghost" onclick={() => (naming = null)}>Cancel</button>
        <button type="submit" class="primary" disabled={!!namingProblem}>Save copy</button>
      </footer>
    </form>
  {/if}
</div>
{/if}

<style>
  .app { height: 100%; display: flex; flex-direction: column; }

  .bar {
    display: flex;
    align-items: center;
    gap: 11px;
    padding: 9px 13px;
    background: var(--panel-2);
    border-bottom: 1px solid var(--edge);
    flex: none;
  }
  .pak { font-size: 12px; white-space: nowrap; flex: none; }
  .stale {
    flex: none;
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 7px 13px;
    font-size: 12px;
    color: #14100a;
    background: var(--warn, #e0a33e);
  }
  .stale b { font-weight: 700; }
  .stale button { margin-left: auto; color: #14100a; border-color: #14100a55; }
  /* Rift Lord's section title: the name in the page's text colour, its last word in
     the shared title purple, as the Map Editor's "Map Editor" is */
  h1 span { color: var(--hot); }
  h1 {
    font-size: 15px;
    color: var(--text);
    font-weight: 700;
    letter-spacing: 0.02em;
    white-space: nowrap;
  }
  .title { width: 15rem; }
  .grow { flex: 1; }
  .dim { color: var(--text-dim); font-size: 12px; }
  .err { color: var(--bad); }
  .wrn { color: var(--warn); }
  .sep { color: var(--text-dim); margin: 0 4px; }
  .issues { font-size: 12px; padding: 4px 9px; }
  .badge {
    font-size: 11px;
    font-weight: 600;
    padding: 3px 9px;
    border-radius: 10px;
    border: 1px solid transparent;
    color: #fff;
    white-space: nowrap;
  }
  .badge.ice { background: #2f6fd0; }
  .badge.sand { background: #d1791f; }
  /* the game mode: the rift's blue, the Crystal's red */
  .badge.mode { background: var(--rift); }
  .badge.mode.crystal { background: var(--rift-red); }

  main {
    flex: 1;
    display: grid;
    grid-template-columns: minmax(320px, 44%) 1fr;
    min-height: 0;
  }

  .left {
    border-right: 1px solid var(--edge);
    min-width: 0;
    display: grid;
    grid-template-rows: 1fr auto auto;
    min-height: 0;
  }
  /* A console over the map pane: one fixed height, scrolling inside, folded
     away by its own header or reopened from the status bar. */
  .console {
    display: flex;
    flex-direction: column;
    height: 13rem;
    border-top: 2px solid var(--accent);
    background: var(--panel);
    min-height: 0;
  }
  .console.bad { border-top-color: var(--bad); }
  .console header {
    display: flex;
    align-items: center;
    padding: 6px 12px;
    border-bottom: 1px solid var(--edge);
    cursor: pointer;
    user-select: none;
    flex: none;
  }
  .console h4 { font-size: 12px; }
  .console .grow { flex: 1; }
  .console pre {
    margin: 0;
    padding: 9px 12px;
    overflow: auto;
    flex: 1;
    font: 11.5px/1.5 ui-monospace, Menlo, Consolas, monospace;
    color: var(--text-dim);
    white-space: pre-wrap;
  }
  .console-tab {
    font-size: 11px;
    padding: 1px 9px;
    border-radius: 4px;
    background: var(--raised);
    border: 1px solid var(--edge);
    color: var(--text-dim);
  }
  .console-tab.bad { color: var(--bad); border-color: var(--bad); }
  /* the outcome of a save inside Rift Lord, which keeps the log in its console */
  .outcome {
    display: flex; align-items: center; gap: 10px; padding: 7px 10px 7px 14px;
    border-bottom: 1px solid var(--edge); background: var(--panel); font-size: 13px;
  }
  .outcome strong { color: var(--ok); }
  .outcome.bad strong { color: var(--bad); }
  .outcome .why { flex: 1; color: var(--text); white-space: pre-wrap; }
  .outcome:not(.bad)::after { content: ''; flex: 1; }
  .btn-spin {
    display: inline-block; box-sizing: border-box; width: 0.85em; height: 0.85em;
    margin-right: 6px; vertical-align: -0.1em;
    border: 2px solid currentColor; border-right-color: transparent; border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .facts {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 14px;
    padding: 8px 13px;
    border-top: 1px solid var(--edge);
    background: var(--panel-2);
    color: var(--text-dim);
    font-size: 12px;
  }
  .facts b { color: var(--accent-ink); font-weight: 600; }
  .right {
    display: flex;
    flex-direction: column;
    min-width: 0;
    min-height: 0;
    position: relative;      /* the export sheet anchors here */
  }

  .blank { flex: 1; }

  .quit { background: none; border: 1px solid var(--line, #333); color: inherit;
          border-radius: 4px; padding: 1px 8px; font: inherit; cursor: pointer;
          opacity: 0.7; }
  .quit:hover { opacity: 1; border-color: var(--rift-red); }
  .stopped { display: grid; place-items: center; height: 100vh; text-align: center;
             gap: 4px; }
  .stopped h1 { font-size: 18px; margin: 0; }
  .stopped p { margin: 0; opacity: 0.7; }
  .theme {
    background: var(--raised);
    border-color: var(--edge);
    padding: 4px 8px;
    line-height: 0;
  }
  .theme svg { fill: currentColor; }

  .jump {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 7px 10px;
    flex: none;
    border-bottom: 1px solid var(--edge);
    background: var(--panel-2);
    position: relative;
    z-index: 5;            /* the load menu drops over the form below it */
  }
  .lab {
    color: var(--text-dim);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-right: 3px;
  }
  .chip { padding: 1px 9px; font-size: 12px; border: 1px solid var(--edge); }
  .chip.add { color: var(--accent-ink); border-color: var(--edge); }
  .jump .grow { flex: 1; }
  .campaign { font-size: 12px; padding: 2px 6px; }
  .ver { color: var(--text-dim); font-variant-numeric: tabular-nums; }

  /* the Export button and its menu, which needs to sit above the form below */
  .export { position: relative; z-index: 12; }
  /* the pair reads as one control: square the join, keep the outer corners */
  .export .primary:first-child { border-radius: 5px 0 0 5px; }
  /* alone (no caret beside it): round all four corners */
  .export .primary:only-child { border-radius: 5px; }
  .export .caret {
    padding: 4px 7px;
    font-size: 11px;
    border-radius: 0 5px 5px 0;
    border-left: 1px solid rgba(0, 0, 0, 0.25);
  }
  .export .menu { width: 17.5rem; padding: 5px; }
  .scrim { position: fixed; inset: 0; z-index: -1; }
  .export .item {
    display: block;
    width: 100%;
    padding: 7px 9px;
    text-align: left;
    background: none;
    border: 0;
    border-radius: 4px;
    color: var(--text);
    font-size: 13px;
  }
  .export .item:hover { background: var(--accent); color: var(--on-accent); }
  .export .item em {
    display: block;
    margin-top: 2px;
    font-style: normal;
    font-size: 11px;
    color: var(--text-dim);
  }
  .export .item:hover em { color: var(--on-accent); opacity: 0.85; }
  .dropped {
    position: relative;
    margin: 0 12px 8px;
    padding: 8px 34px 8px 12px;
    border: 1px solid var(--edge);
    border-left: 3px solid var(--warn);
    border-radius: 4px;
    background: var(--panel-2);
    font-size: 12px;
    color: var(--text-dim);
  }
  .dropped strong { color: var(--warn); font-weight: 700; }
  .dropped.ok { border-left-color: var(--ok); }
  .dropped.ok strong { color: var(--ok); }
  .copy { margin-right: 6px; }
  .dropped p { margin: 4px 0 0; }
  .endless-door {
    margin: 0 0 12px; padding: 7px 10px; font-size: 12px; color: var(--text);
    border: 1px solid var(--edge); border-left: 3px solid var(--warn);
    border-radius: 4px; background: var(--panel-2);
  }
  .name-scrim { position: fixed; inset: 0; z-index: 1000; background: rgba(0, 0, 0, 0.45); }
  .name-modal {
    position: fixed; z-index: 1001; top: 18%; left: 50%; transform: translateX(-50%);
    width: min(26rem, calc(100vw - 32px)); padding: 14px 16px 12px;
    background: var(--panel); color: var(--text); border: 1px solid var(--edge);
    border-radius: 8px; box-shadow: 0 12px 40px rgba(0, 0, 0, 0.45);
  }
  .name-modal h3 { margin: 0 0 10px; font-size: 14px; color: var(--hot); }
  .name-modal label { display: flex; align-items: center; gap: 8px; }
  .name-modal label span { color: var(--text-dim); font-size: 12px; }
  .name-modal input { flex: 1; min-width: 0; }
  .name-modal .hint { margin: 6px 0 0; font-size: 12px; color: var(--text-dim); }
  .name-modal .hint.err { color: var(--bad); }
  .name-modal footer { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
  .dropped ul { margin: 4px 0 0; padding-left: 18px; }
  .dropped li { margin: 1px 0; }
  .dropped li b { color: var(--text); font-variant-numeric: tabular-nums; }
  .dropped > button { position: absolute; top: 5px; right: 6px; }
  .split { position: relative; display: inline-flex; gap: 0; }
  .split .chip:first-child { border-radius: 4px 0 0 4px; }
  .caret { border-radius: 0 4px 4px 0; border-left: 0; padding: 1px 7px; }
  .menu {
    position: absolute;
    top: calc(100% + 5px);
    right: 0;
    z-index: 9;
    width: 17rem;
    padding: 9px;
    background: var(--panel);
    border: 1px solid var(--edge);
    border-radius: 6px;
    box-shadow: 0 10px 28px rgba(0, 0, 0, 0.35);
  }
  .menu p {
    margin: 0 0 6px;
    font-size: 11px;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  .maps {
    max-height: 17rem;
    overflow-y: auto;
    border: 1px solid var(--edge);
    border-radius: 4px;
    background: var(--bg);
  }
  .group {
    padding: 4px 9px;
    background: var(--raised);
    color: var(--text-dim);
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    position: sticky;
    top: 0;
  }
  .pick {
    display: block;
    width: 100%;
    text-align: left;
    padding: 4px 9px 4px 16px;
    font-size: 12.5px;
    background: none;
    border: 0;
    border-radius: 0;
    color: var(--text);
  }
  .pick:hover { background: #2f6fd0; color: #fff; }
  .menu .note { margin: 7px 0 0; text-transform: none; letter-spacing: 0; }

  .form { flex: 1; overflow-y: auto; padding: 12px; min-height: 0; }
  .preview-tab { border: 1px solid var(--edge); border-radius: 6px; background: var(--panel);
                 padding: 10px 12px; }

  .review {
    border: 1px solid var(--edge);
    border-radius: 6px;
    padding: 9px 11px;
    background: var(--panel);
  }
  .review h4 {
    margin: 0 0 5px;
    font-size: 12px;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  .review p { margin: 3px 0; font-size: 12.5px; }
  .review b { color: var(--text); font-weight: 600; }
  .review .error { color: var(--bad); }
  .review .warn { color: var(--warn); }

  .loading {
    position: fixed;
    inset: 0;
    display: grid;
    place-content: center;
    justify-items: center;
    gap: 14px;
    background: rgba(0, 0, 0, 0.55);
    z-index: 10;
  }
  .loading p { margin: 0; color: #e6e8ea; font-size: 13px; }
  .spinner {
    width: 34px;
    height: 34px;
    border: 3px solid rgba(255, 255, 255, 0.22);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 700ms linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* The export log rises over the form from the bottom of the right pane,
     rather than pushing the waves about. */
  .sheet {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    max-height: 55%;
    display: flex;
    flex-direction: column;
    background: var(--panel);
    border-top: 2px solid var(--accent);
    box-shadow: 0 -8px 26px rgba(0, 0, 0, 0.35);
    animation: rise 160ms ease-out;
  }
  .sheet.bad { border-top-color: var(--bad); }
  @keyframes rise { from { transform: translateY(100%); } }
  .sheet header {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 12px;
    border-bottom: 1px solid var(--edge);
  }
  .sheet h4 { font-size: 13px; }
  .sheet .path { font-size: 11.5px; color: var(--text-dim); word-break: break-all; }
  .sheet .grow { flex: 1; }
  .sheet pre {
    margin: 0;
    padding: 10px 12px;
    overflow: auto;
    font: 11.5px/1.5 ui-monospace, Menlo, Consolas, monospace;
    color: var(--text-dim);
    white-space: pre-wrap;
  }
</style>
