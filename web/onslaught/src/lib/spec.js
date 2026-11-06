// The onslaught being authored, and the rules the editor applies for the author.
//
// Shape: waves in play order, each holding its spawns keyed by spawner index
// plus the wave-level settings the coordinator fires by name. This is what the
// build endpoint receives verbatim.

/** EOnslaughtDifficulty, from the game's own reflection data. */
export const DIFFICULTY = {
  5: 'Any',
  0: 'Apprentice',
  1: 'War Mage',
  2: 'Rift Lord (not in Endless)',
  3: 'Co-op',
  4: 'Endless, from loop 2',
}

/** Any: the value that applies whatever is being played. */
export const ALL_DIFFICULTIES = 5

/** EOnslaughtDifficulty.Endless: these spawns play in Endless mode from its second loop on. */
export const ENDLESS_DIFFICULTY = 4

/** The bytes that play on the first Endless loop of a solo game (wavespec.LOOP_ONE). */
export const LOOP_ONE = [ALL_DIFFICULTIES]

/** The campaign row Endless mode enumerates. */
export const ENDLESS_CAMPAIGN = 'OMD3_Endless'
//: The editor's name for "no campaign": a custom map (makemod.CUSTOM_MAPS).
export const CUSTOM_MAPS = 'CustomMaps'
//: The placement that creates a campaign (customcampaign.NEW).
export const NEW_CAMPAIGN = 'new'
//: An installed custom campaign's placement starts with this (customcampaign.CUSTOM).
export const CUSTOM_CAMPAIGN = 'custom:'

/** True when the placement is a shipped campaign, the only kind with an Endless twin. */
export function isShipped(placement) {
  return placement !== CUSTOM_MAPS && placement !== NEW_CAMPAIGN
    && !String(placement).startsWith(CUSTOM_CAMPAIGN)
}

//: What a new mission starts with: Rift Lord's Settings (setDefaults), else bucket.DEFAULTS.
export const DEFAULTS = { author: '', version: '', placement: CUSTOM_MAPS, endless: false,
                          hub: 'OldFriends' }

/** Take Rift Lord's mission defaults. `campaigns` are the placements this editor
 *  offers; a default campaign no longer among them falls back to Custom Maps. */
export function setDefaults(got, campaigns) {
  Object.assign(DEFAULTS, got ?? {})
  if (!campaigns.some((c) => c.name === DEFAULTS.placement)) DEFAULTS.placement = CUSTOM_MAPS
}

/** Author, version and description for OrcsMustUI, and a new campaign's fields. */
export function newDetails(keep = {}) {
  return {
    author: keep.author || DEFAULTS.author,
    version: keep.version || DEFAULTS.version,
    description: '',
    //: hub style (customcampaign.HUBS); '' is Automatic
    hub: DEFAULTS.hub ?? '',
    newCampaign: { name: '', description: '', banner: null, hub: '' },
  }
}

/** A break terminates a wave, so the kinds are the wave's own ending. */
export const BREAKS = [
  { kind: 'wave', label: 'Wave Break' },
  { kind: 'go', label: 'Go Break' },
]

export const DEFAULT_BREAK_SECONDS = 10

let nextId = 1

/** Carry on numbering past every wave and spawn id a saved spec already uses, so a wave
 *  or spawn added after reopening it never repeats one (a repeated key stops the page
 *  updating: "+ Wave" did nothing and the tabs stuck). A wave or spawn saved without an id
 *  (a spec a script wrote, as a probe's) gets a new one. Returns the spec. */
export function adopt(spec) {
  const rowsOf = (w) => Object.values(w.spawns ?? {}).flat()
  for (const w of spec?.waves ?? []) {
    if (Number.isFinite(w.id)) nextId = Math.max(nextId, w.id + 1)
    for (const r of rowsOf(w)) if (Number.isFinite(r.id)) nextId = Math.max(nextId, r.id + 1)
  }
  for (const w of spec?.waves ?? []) {
    if (!Number.isFinite(w.id)) w.id = nextId++
    for (const r of rowsOf(w)) if (!Number.isFinite(r.id)) r.id = nextId++
  }
  return spec
}

/** EndlessPromotePercent; 0.5 in the great majority of shipped calls. */
export const DEFAULT_PROMOTE = 0.5

export function newSpawn(unit = '') {
  return {
    id: nextId++,
    unit,
    count: 5,
    promote: DEFAULT_PROMOTE,
    // Pacing for this spawner only, as {kind: 'wait'|'gate', value}. `after`
    // follows this spawn; `before` runs ahead of it and is offered only on the
    // first row, where it opens the wave.
    before: null,
    after: null,
    difficulty: ALL_DIFFICULTIES,
    // EndlessAddCount, carried from the map's own onslaught; null keeps the call's own
    addCount: null,
  }
}

/** True for a row the game sends units for at some point: a count of at least one, or
 *  a count of zero that grows with each Endless loop. */
export function sends(row) {
  return (Number(row.count) || 0) >= 1 || (Number(row.addCount) || 0) >= 1
}

export function newWave() {
  return {
    id: nextId++,
    breakKind: 'wave',
    breakSeconds: DEFAULT_BREAK_SECONDS,
    coin: null,
    actions: [],
    spawns: {},
  }
}

/** Turn a map's own onslaught, as the server read it, into an editable spec. */
export function fromMap(read, base) {
  const waves = read.waves.map((w) => ({
    ...newWave(),
    breakKind: w.breakKind,
    breakSeconds: w.breakSeconds,
    coin: w.coin ?? null,
    actions: [...(w.actions ?? [])],
    spawns: Object.fromEntries(
      Object.entries(w.spawns ?? {}).map(([spawner, rows]) => [
        spawner,
        rows.map((r) => ({ ...newSpawn(r.unit), ...r, id: nextId++ })),
      ])),
  }))
  return { ...base, waves: waves.length ? waves : [newWave()],
           events: (read.events ?? []).map((e) => ({ ...e })) }
}

/** The values a per-difficulty pause holds (wavespec.PAUSE_SLOTS), in order. `all` is every
 *  difficulty and the Endless loops before `endlessLaterFrom`. */
export const PAUSE_SLOTS = ['all', 'apprentice', 'warMage', 'riftLord', 'endless', 'endlessLater']
/** Longest pause, in seconds (Pause.svelte's flat wait). */
export const PAUSE_MAX = 300

/** A per-difficulty pause slot's label. */
export function slotLabel(slot, from = 2) {
  return { all: 'All', apprentice: 'Apprentice', warMage: 'War Mage', riftLord: 'Rift Lord',
           endless: from > 2 ? `Endless, loops 1-${from - 1}` : 'Endless, loop 1',
           endlessLater: `Endless, from loop ${from}` }[slot]
}

/** True when a pause value is a number from 0 to PAUSE_MAX. */
export function pauseOk(v) {
  return v !== '' && v !== null && Number.isFinite(Number(v)) && Number(v) >= 0 && Number(v) <= PAUSE_MAX
}

/** A per-difficulty pause in one line: "Wait 32 / 30 / 28 s (Endless 16 → 0)". */
export function pauseSummary(p) {
  const v = p.byDifficulty, n = (x) => `${Number(x)}`
  const own = ['all', 'apprentice', 'warMage', 'riftLord'].filter((s) => s in v).map((s) => n(v[s]))
  const loop = 'endless' in v ? v.endless : v.all
  const from = (p.endlessLaterFrom ?? 2) > 2 ? ` from loop ${p.endlessLaterFrom}` : ''
  const later = 'endlessLater' in v ? ` (Endless ${n(loop)} → ${n(v.endlessLater)}${from})` : ''
  return `Wait ${own.join(' / ')} s${later}`
}

/** Mission event kinds (wavespec.apply_events): boss, music change, unstable rift. */
export const EVENT_KINDS = ['boss', 'music', 'rift']

/** "Battle_06" -> "Battle 6" (levelmusic.choices). */
export function trackLabel(key, detail) {
  return detail?.events?.tracks?.find((t) => t.key === key)?.label
    ?? String(key ?? '').replace(/_/g, ' ').replace(/ 0(\d)$/, ' $1')
}

/** Why the host cannot do a kind of event, or '' when it can. */
export function eventBlocked(kind, detail) {
  const caps = detail?.events
  if (!caps) return 'This map\'s events could not be read'
  if (kind === 'music') return caps.music?.available ? '' : (caps.music?.why || 'This map has no music change')
  if (kind === 'rift') return caps.rift?.available ? '' : (caps.rift?.why || 'This map has no unstable rifts')
  if (kind === 'dialogue') return caps.dialogue?.available ? '' : (caps.dialogue?.why || DIALOGUE_NEEDS)
  return ''
}

// -- dialogue lines (hostevents.clean_line, riftlord/build/world/captions.py) ------------

/** Why a map plays no dialogue without the Mission Events piece (hostevents.DIALOGUE_NEEDS). */
export const DIALOGUE_NEEDS = 'Needs the Mission Events piece (Map Editor, New Object)'
/** When a line plays (hostevents.DIALOGUE_WHEN), and its label. */
export const DIALOGUE_WHEN = [['start', 'Wave Start'], ['break', 'Break After Wave'],
                              ['after', 'After Previous Line']]
/** Text and speaker lengths, line length and delay ranges (captions.TEXT_MAX, SPEAKER_MAX,
 *  DURATION_MIN, DURATION_MAX; hostevents.DIALOGUE_DELAY_MAX). */
export const LINE_TEXT_MAX = 160
export const LINE_SPEAKER_MAX = 24
export const LINE_MIN = 0.7
export const LINE_MAX = 20
export const LINE_DELAY_MAX = 300
/** The speakers shipped captions name (the game's Subtitles string table). */
export const SHIPPED_SPEAKERS = ['Cygnus', 'Egan', 'Kelsey', 'Max', 'Sorceress', 'Vorwick']
/** A line's length from its text (captions.derived_duration: the shipped lines' fit). */
const LINE_BASE = 1.274
const LINE_PER_CHAR = 0.0596

/** The seconds a line of `text` lasts when no length is set. */
export function lineLength(text) {
  const raw = LINE_BASE + LINE_PER_CHAR * String(text ?? '').trim().length
  return Math.round(Math.max(LINE_MIN, Math.min(LINE_MAX, Math.round(raw * 10) / 10)) * 10) / 10
}

/** A new dialogue line after `last` (or at wave 1's start). */
export function newLine(last) {
  return last
    ? { kind: 'dialogue', wave: last.wave, when: 'after', delay: 0, speaker: last.speaker ?? '',
        text: '', duration: null }
    : { kind: 'dialogue', wave: 1, when: 'start', delay: 0, speaker: '', text: '', duration: null }
}

/** The dialogue lines among `events`, in order. */
export function dialogueLines(events) {
  return (events ?? []).filter((e) => e.kind === 'dialogue')
}

/** Each 'after' line takes the wave of the line before it. */
export function syncLineWaves(events) {
  let wave = 1
  for (const line of dialogueLines(events)) {
    if (line.when === 'after') line.wave = wave
    wave = line.wave
  }
}

/** {field: why} for what keeps line `i` of `lines` from being written. */
export function lineProblems(line, i, waves) {
  const out = {}
  const text = String(line.text ?? '')
  const speaker = String(line.speaker ?? '')
  if (!text.trim()) out.text = 'has no text'
  else if (text.trim().length > LINE_TEXT_MAX) out.text = `is longer than ${LINE_TEXT_MAX} characters`
  // eslint-disable-next-line no-control-regex
  else if (/[\u0000-\u001f]/.test(text)) out.text = 'holds a line break'
  if (speaker.trim().length > LINE_SPEAKER_MAX) out.speaker = `is longer than ${LINE_SPEAKER_MAX} characters`
  else if (speaker.includes(':')) out.speaker = 'holds a colon'
  const delay = Number(line.delay ?? 0)
  if (!(delay >= 0 && delay <= LINE_DELAY_MAX)) out.delay = `must be 0 to ${LINE_DELAY_MAX} seconds`
  if (line.duration != null && line.duration !== '') {
    const d = Number(line.duration)
    if (!(d >= LINE_MIN && d <= LINE_MAX)) out.duration = `must be ${LINE_MIN} to ${LINE_MAX} seconds`
  }
  if (line.when === 'after' && i === 0) out.when = 'has no line before it'
  else if (line.when !== 'after') {
    const n = Number(line.wave)
    if (line.wave === '' || line.wave == null || !Number.isInteger(n) || n < 1) out.wave = 'At least 1'
    else if (n > waves) out.wave = waves === 1 ? 'The onslaught has 1 wave' : `The onslaught has ${waves} waves`
    else if (line.when === 'break' && n === waves) out.wave = 'The last wave has no break'
  }
  return out
}

/** The reaction the host has for a boss unit, or null. */
export function reactionFor(unit, detail) {
  return detail?.events?.reactions?.find((r) => r.unit === unit) ?? null
}

/** A new event of `kind` for this map, on the last wave. */
export function newEvent(kind, spec, detail, units) {
  const wave = spec.waves.length
  if (kind === 'music') {
    return { kind, wave, track: detail?.events?.music?.change ?? 'Battle_01' }
  }
  if (kind === 'rift') {
    return { kind, wave, rift: detail?.events?.rift?.rifts?.[0] ?? 1, open: true }
  }
  const boss = detail?.events?.reactions?.[0]?.unit
    ?? (units ?? []).find((u) => u.boss)?.name ?? ''
  const door = (detail?.doors ?? []).find((d) => d.kind === 'door') ?? detail?.doors?.[0]
  return { kind: 'boss', wave, unit: boss, door: door?.spawners?.[0] ?? 1, count: 1,
           reaction: !!reactionFor(boss, detail) }
}

/** Spawner index -> the routes its door offers: 'walk', 'fly' or 'both'. */
function pathingBySpawner(detail) {
  const out = {}
  for (const door of detail?.doors ?? []) {
    for (const i of door.spawners ?? []) out[String(i)] = door.pathing
  }
  return out
}

/**
 * Drop the spawns a map cannot actually run, and say what went.
 *
 * An onslaught loaded from another map was written for that map's doors. Move
 * it and some of its spawns become impossible: a Hellbat cannot walk, so it
 * cannot come through a walking door, and the form has no option to offer for
 * it -- which is how a row ends up looking blank. Rather than leave the author
 * a row they cannot fix, the row goes and is named.
 *
 * A count of zero is the other case. It is kept with an EndlessAddCount, where it
 * means "none at first, more each Endless loop"; without one it sends nobody.
 */
export function sanitize(waves, detail, units) {
  const pathing = pathingBySpawner(detail)
  const byName = Object.fromEntries((units ?? []).map((u) => [u.name, u]))
  const label = (i) =>
    detail?.doors?.find((d) => (d.spawners ?? []).includes(Number(i)))?.label
    ?? `spawner ${i}`
  // Grouped by reason: a wave that loses 46 orcs to the same flyer-only door
  // is one thing to tell the author, not 46.
  const counts = new Map()
  const dropped = { push: (why) => counts.set(why, (counts.get(why) ?? 0) + 1) }
  const out = waves.map((wave) => {
    const spawns = {}
    for (const [spawner, rows] of Object.entries(wave.spawns ?? {})) {
      const keep = []
      for (const row of rows) {
        const unit = byName[row.unit]
        const route = pathing[spawner]
        let why = null
        if (!row.unit) why = 'the source names a minion this game build has no proto for'
        else if (!unit) why = `${row.unit} is not a minion this map can spawn`
        else if (route === 'fly' && !unit.flyer)
          why = `${unit.label} cannot use ${label(spawner)}, a flyer-only route`
        else if (route === 'walk' && unit.flyer)
          why = `${unit.label} flies, and ${label(spawner)} is a walking route`
        else if (!sends(row))
          why = `${unit.label} is asked for zero with no Endless growth`
        if (why) dropped.push(why)
        else keep.push(row)
      }
      if (keep.length) {
        // A pause read off a dropped row would now pace nothing, and only the
        // first row may carry an opening one. Untouched spawners keep theirs.
        if (keep.length !== rows.length) {
          keep[keep.length - 1].after = null
          keep.forEach((r, i) => { if (i) r.before = null })
        }
        spawns[spawner] = keep
      }
    }
    // A named event is a Blueprint event the coordinator handles, so only the ones
    // this map's coordinator already fires can be kept: another map's music change or
    // boss would be a call to nothing, and the build refuses it.
    const fires = new Set((detail?.actions ?? []).map((a) => a.name))
    const actions = (wave.actions ?? []).filter((name) => {
      if (fires.has(name)) return true
      dropped.push(`this map fires no ${name} event`)
      return false
    })
    return { ...wave, spawns, actions }
  })
  const kept = out.filter((w) => Object.keys(w.spawns).length)
  return {
    waves: kept,
    total: [...counts.values()].reduce((a, b) => a + b, 0),
    dropped: [...counts].map(([why, count]) => ({ why, count }))
      .sort((a, b) => b.count - a.count),
  }
}

export function newSpec(map = null) {
  return {
    map,
    title: '',
    //: the mission's par time in seconds (MissionProtos ParTimeSeconds); null keeps the
    //: map's own (detail.par)
    par: null,
    war: false,
    // A custom map by default: in no campaign, reached from OrcsMustUI's Custom
    // Maps tab, its mission table in its own mod folder (registered from the pak), so the pak
    // patches no file another mod also ships.
    intoCampaign: DEFAULTS.placement,
    //: What OrcsMustUI shows for the mission (Details.svelte).
    details: newDetails(),
    // Reset door after Onslaught Loop: in Endless, Endless rows that would open a door
    // before its loop-1 wave move into that wave when the mission builds (wavespec).
    resetDoors: false,
    // Also give the mission its Endless-mode twin. Endless reads its own campaign row
    // and lists a separate mission, which is why every shipped map has a _End row;
    // without one the mission exists only where it is listed. Opt-in (Details).
    endless: DEFAULTS.endless,
    //: Write the onslaught into the chosen map's own pak instead of a mission of
    //: its own. Only meaningful for an installed mod map; the server refuses it
    //: for a shipped one, so the option is hidden there rather than failing late.
    bundle: false,
    waves: [newWave()],
    //: mission events (EVENT_KINDS), shown on the Timeline tab
    events: [],
  }
}

/** Shortest and longest par time, in seconds (wavespec.PAR_MIN, PAR_MAX). */
export const PAR_MIN = 30
export const PAR_MAX = 7200

/** Insert a new spawn directly after row `k` of one spawner's list in one wave. */
export function insertSpawn(wave, spawner, k) {
  spawnsFor(wave, spawner).splice(k + 1, 0, newSpawn())
}

/** Take every row out of one wave for a door's spawners. */
export function clearDoorWave(wave, spawners) {
  for (const s of spawners) delete wave.spawns[String(s)]
}

/** Spawn entries for one spawner in one wave, creating the list on demand. */
export function spawnsFor(wave, spawner) {
  if (!wave.spawns[spawner]) wave.spawns[spawner] = []
  return wave.spawns[spawner]
}

/** Total enemies a wave sends, across every door. */
export function waveTotal(wave) {
  return Object.entries(wave.spawns ?? {}).filter(([k]) => !isGroupKey(k)).flatMap(([, r]) => r)
    .reduce((sum, s) => sum + (s.unit ? Number(s.count) || 0 : 0), 0)
}

// -- Rift Groups (riftlord/mapeditor/rifttear.py) ------------------------------------------
//
// spec.riftTear is the Crystal's view from the server: {groups: [{name, rifts: [{spawner,
// difficulty, players, chances}], difficulty, players, triggers, waves, minions}], rifts:
// [{id, index}], health, riftGap, waveCount}. While editing, a group's enemy rows of a wave
// live in that wave's spawns under groupKey(gi), so they move with the wave and take the
// spawn row's controls; a member's chance in a wave is wave.riftChance[memberKey(gi, id)]
// (missing: RIFT_CHANCE). tearForSave folds both back into the view.

//: A member's chance in a wave none was given for (rifttear.CHANCE_DEFAULT).
export const RIFT_CHANCE = 100
//: The label and tooltip of a member's per-wave chance (rifttear MemberChance), wherever shown.
export const PICK_LABEL = 'Pick Chance (%)'
export const PICK_TIP = 'Chance this rift is picked when the group opens in this wave'
export const groupKey = (gi) => `g${gi}`
export const isGroupKey = (k) => /^g\d+$/.test(String(k))
export const memberKey = (gi, spawner) => `${gi}:${spawner}`

//: The most Enemies Left % a trigger opens at: a higher one can end the wave as the units come out.
export const TRIGGER_ALIVE_MAX = 75

//: The Crystal's health fields (rifttear._health, crystal.py HEALTH_KEYS): [key, label, step].
export const HEALTH_FIELDS = [['mapDifficulty', 'Map Difficulty', 0.1],
  ['pointHealth', 'Rift Point Health', 1], ['pointDifficultyShare', 'Difficulty Share', 0.05]]

/** Why a Crystal health field's value is refused for its [lo, hi] `range`, or ''; blank is
 *  none (Map Difficulty 0, an override the mode's). */
export function healthProblem(v, range) {
  if (v === null || v === undefined || v === '') return ''
  const n = Number(v)
  if (!Number.isFinite(n)) return 'A number'
  if (range && (n < range[0] || n > range[1])) return `From ${range[0]} to ${range[1]}`
  return ''
}

/** Why a trigger's Open (rifts opened, RiftPick) is out of range for a group of `rifts`
 *  Unstable Rifts, or ''. */
export function triggerOpenProblem(t, rifts) {
  const n = Number(t.open)
  if (t.open === '' || t.open == null || !Number.isInteger(n) || n < 1) return 'At least 1'
  if (n > rifts) return rifts === 1 ? 'This group has 1 Unstable Rift' : `This group has ${rifts} Unstable Rifts`
  return ''
}

/** Why a trigger's Wave is out of range for an onslaught of `waves` waves, or ''; '' with no
 *  Wave. */
export function triggerWaveProblem(t, waves) {
  if (t.wave === null || t.wave === undefined) return ''
  const n = Number(t.wave)
  if (t.wave === '' || !Number.isInteger(n) || n < 1) return 'At least 1'
  if (n > waves) return waves === 1 ? 'The onslaught has 1 wave' : `The onslaught has ${waves} waves`
  return ''
}

/** An older trigger as the Rift Group tab edits it (Trigger.svelte): an Enemies Left mark
 *  (`alive`) becomes the opening (`launchAlive`, at most TRIGGER_ALIVE_MAX) from Wave 1 when
 *  it had no Wave or Health; an opening at both Seconds and Enemies Left keeps the Seconds;
 *  Health or Wave (`both` false) becomes two triggers. Returns the triggers. */
export function convertTrigger(t) {
  const out = { ...t }
  const hasAt = out.at != null && out.at !== '' && Number(out.at) < 100
  const hasWave = out.wave != null && out.wave !== '' && Number(out.wave) >= 1
  if (Number(out.alive) > 0) {
    if (!(Number(out.launchAlive) > 0) && !(Number(out.launchAfter) > 0)) {
      out.launchAlive = Math.min(TRIGGER_ALIVE_MAX, Number(out.alive))
    }
    if (!hasAt && !hasWave) out.wave = 1
  }
  out.alive = 0
  if (Number(out.launchAfter) > 0) out.launchAlive = 0
  else if (Number(out.launchAlive) > TRIGGER_ALIVE_MAX) out.launchAlive = TRIGGER_ALIVE_MAX
  if (hasAt && hasWave && !out.both) {
    return [{ ...out, wave: null }, { ...out, at: null }]
  }
  return [out]
}

/** Give rows read from the server the ids the form keys them by. */
function withIds(rows) {
  return (rows ?? []).map((r) => ({ ...newSpawn(r.unit ?? ''), ...r, id: nextId++ }))
}

/** Put the view's per-wave rows and chances onto spec.waves. A view without per-wave rows
 *  (waveCount 0) sends its older rows in every wave. */
export function injectTear(spec) {
  const tear = spec.riftTear
  if (!tear) return spec
  for (const g of tear.groups ?? []) g.triggers = (g.triggers ?? []).flatMap(convertTrigger)
  const perWave = (tear.waveCount ?? 0) > 0
  spec.waves.forEach((wave, w) => {
    wave.spawns = Object.fromEntries(Object.entries(wave.spawns ?? {}).filter(([k]) => !isGroupKey(k)))
    wave.riftChance = {}
    tear.groups.forEach((g, gi) => {
      const rows = perWave ? (g.waves ?? [])[w] ?? [] : (g.minions ?? []).map((m) => ({
        unit: m.unit, count: m.count, addCount: m.addCount ?? 0 }))
      if (rows.length) wave.spawns[groupKey(gi)] = withIds(rows)
      for (const r of g.rifts ?? []) {
        const c = r.chances?.[w]
        if (c !== undefined && c !== null && Number(c) !== RIFT_CHANCE) wave.riftChance[memberKey(gi, r.spawner)] = Number(c)
      }
    })
  })
  return spec
}

/** `spec` as the server takes it: group rows and chances out of the waves and into
 *  riftTear (groups' waves and rifts' chances, waveCount), the older rows dropped. */
export function tearForSave(spec) {
  const plain = (r) => ({ unit: r.unit, count: r.count, addCount: r.addCount ?? 0,
                          promote: r.promote, difficulty: r.difficulty,
                          before: r.before ?? null, after: r.after ?? null })
  const waves = spec.waves.map((w) => {
    const { riftChance: _c, ...rest } = w
    return { ...rest, spawns: Object.fromEntries(Object.entries(w.spawns ?? {}).filter(([k]) => !isGroupKey(k))) }
  })
  if (!spec.riftTear) return { ...spec, waves }
  const tear = spec.riftTear
  const riftTear = { ...tear, waveCount: spec.waves.length,
    groups: tear.groups.map((g, gi) => ({ ...g, minions: [],
      waves: spec.waves.map((w) => (w.spawns?.[groupKey(gi)] ?? []).map(plain)),
      rifts: (g.rifts ?? []).map((r) => ({ ...r,
        chances: spec.waves.map((w) => w.riftChance?.[memberKey(gi, r.spawner)] ?? RIFT_CHANCE) })) })) }
  return { ...spec, waves, riftTear }
}

/** Enemies one spawner sends in one wave. */
export function doorTotal(wave, spawners) {
  return spawners.flatMap((i) => wave.spawns?.[i] ?? [])
    .reduce((sum, s) => sum + (s.unit ? Number(s.count) || 0 : 0), 0)
}

/**
 * Problems worth showing the author, as {level, where, text}.
 *
 * These are the rules the tooling has actually established, not style advice:
 * the final wave owns no break, a wave with nothing in it never ends, and a
 * spawn call gated to one difficulty is invisible on the others.
 */
export function review(spec, can = null, detail = null, units = []) {
  const out = []
  const add = (level, where, text) => out.push({ level, where, text })

  if (!spec.title?.trim()) add('error', 'Name', 'the onslaught has no name.')
  if (spec.par != null && spec.par !== ''
      && !(Number(spec.par) >= PAR_MIN && Number(spec.par) <= PAR_MAX)) {
    const clock = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
    add('error', 'Par time', `must be ${clock(PAR_MIN)} to ${clock(PAR_MAX)}.`)
  }

  if (can?.maxUnits) {
    const distinct = new Set(
      spec.waves.flatMap((w) => Object.values(w.spawns ?? {}).flat())
        .map((s) => s.unit).filter(Boolean))
    if (distinct.size > can.maxUnits) {
      add('error', 'Roster',
        `uses ${distinct.size} different units, but this map names its enemies through ${can.maxUnits} Blueprint variables and can field only that many.`)
    }
  }

  const flies = new Set(units.filter((u) => u.flyer).map((u) => u.name))
  const pathOf = {}
  for (const s of detail?.spawners ?? []) pathOf[s.index] = s.pathing
  const doorOf = {}
  for (const d of detail?.doors ?? []) for (const i of d.spawners) doorOf[i] = doorLabel(d, detail?.mode)
  //: a Rift Group's rows come out of Unstable Rifts, which walk
  ;(spec.riftTear?.groups ?? []).forEach((g, gi) => {
    doorOf[groupKey(gi)] = g.name
    pathOf[groupKey(gi)] = 'walk'
  })
  //: spawners the mission's game-mode piece opens (the Crystal's rifts)
  const opened = new Set(detail?.mode?.opens ?? [])

  spec.waves.forEach((wave, i) => {
    const where = `Wave ${i + 1}`
    Object.entries(wave.spawns ?? {}).forEach(([spawner, list]) => {
      const path = pathOf[spawner]
      const door = doorOf[spawner] ?? `spawner ${spawner}`
      if (opened.has(Number(spawner)) && list.some(sends)) {
        add('error', where, `${door} is opened by the Crystal, so no wave sends units through it.`)
      }
      list.forEach((s) => {
        if (!s.unit) { add('error', where, `${door} has a row with no unit chosen.`); return }
        if (!sends(s)) {
          add('error', where, `${s.unit} on ${door} has a count below 1.`)
        }
        if (flies.has(s.unit) && path === 'walk') {
          add('error', where, `${s.unit} flies, but ${door} has only a walking path.`)
        } else if (!flies.has(s.unit) && path === 'fly') {
          add('error', where, `${s.unit} walks, but ${door} is a flyer route.`)
        }
        for (const p of [s.before, s.after]) {
          const bad = Object.entries(p?.byDifficulty ?? {}).filter(([, v]) => !pauseOk(v))
          if (bad.length) {
            add('error', where, `a pause on ${door} needs ${bad.map(([k]) => slotLabel(k, p.endlessLaterFrom)).join(', ')} from 0 to ${PAUSE_MAX} seconds.`)
          }
        }

      })
    })
  })

  const health = spec.riftTear?.health
  for (const [key, label] of health ? HEALTH_FIELDS : []) {
    const why = healthProblem(health.values?.[key], health.ranges?.[key])
    if (why) add('error', 'Crystal Health', `${label} ${health.values[key]}: ${why}.`)
  }

  for (const g of spec.riftTear?.groups ?? []) {
    for (const t of g.triggers ?? []) {
      const why = triggerOpenProblem(t, (g.rifts ?? []).length)
      if (why) add('error', g.name, `Open ${t.open === '' ? '(blank)' : t.open} Rifts: ${why}.`)
      const wave = triggerWaveProblem(t, spec.waves.length)
      if (wave) add('error', g.name, `When Wave ${t.wave === '' ? '(blank)' : t.wave}: ${wave}.`)
    }
  }

  const lines = dialogueLines(spec.events)
  const dialogueWhy = lines.length ? eventBlocked('dialogue', detail) : ''
  if (dialogueWhy && detail?.events) add('error', 'Dialogue', `${dialogueWhy}.`)
  lines.forEach((line, i) => {
    for (const [field, why] of Object.entries(lineProblems(line, i, spec.waves.length))) {
      const label = { text: 'Line', speaker: 'Speaker', delay: 'Delay', duration: 'Length',
                      when: 'When', wave: 'Wave' }[field]
      add('error', `Dialogue line ${i + 1}`, `${label}: ${why}.`)
    }
  })

  for (const ev of spec.events ?? []) {
    if (ev.kind === 'dialogue') continue
    const where = `Wave ${ev.wave}`
    if (ev.wave > spec.waves.length) add('error', where, `the onslaught has no wave ${ev.wave} for its ${ev.kind} event.`)
    if (ev.kind === 'boss' && !ev.unit) add('error', where, 'a boss event has no boss chosen.')
    if (ev.kind === 'boss' && ev.unit) {
      const path = pathOf[ev.door]
      if (flies.has(ev.unit) && path === 'walk') add('error', where, `${ev.unit} flies, but ${doorOf[ev.door] ?? `spawner ${ev.door}`} has only a walking path.`)
      else if (!flies.has(ev.unit) && path === 'fly') add('error', where, `${ev.unit} walks, but ${doorOf[ev.door] ?? `spawner ${ev.door}`} is a flyer route.`)
    }
  }

  // Difficulty 4 is Endless: those spawns play only from Endless's second loop, so
  // authoring them and then not shipping an Endless entry throws them away.
  const endlessSpawns = spec.waves
    .flatMap((w) => Object.values(w.spawns ?? {}).flat())
    .filter((s) => Number(s.difficulty) === ENDLESS_DIFFICULTY).length
  const inEndless = spec.endless === true || spec.endlessOnly === true
    || spec.intoCampaign === ENDLESS_CAMPAIGN
  if (endlessSpawns && !inEndless) {
    add('warn', 'Endless',
      `Endless Spawn Data detected, but endless is disabled — ${endlessSpawns} `
      + `spawn${endlessSpawns === 1 ? '' : 's'} set to Endless difficulty will `
      + 'never play. Tick "Add to Endless" under Details.')
  }
  // What OrcsMustUI shows on the mission's description page.
  const d = spec.details ?? {}
  if (!['author', 'version', 'description'].every((k) => String(d[k] ?? '').trim())) {
    add('warn', 'Details', 'No mission details set. The Mission description panel will be empty!')
  }
  if (spec.intoCampaign === NEW_CAMPAIGN && !spec.details?.newCampaign?.name?.trim()) {
    add('error', 'Campaign', 'The new campaign needs a name. Set it under Details.')
  }

  return out
}

/** The GameType of a mission with the Crystal (riftlord/onslaught/gamemode.py). */
export const CRYSTAL_DEFENSE = 'CrystalDefense'

/** True for a door entry whose every spawner the mission's game-mode piece opens itself
 *  (detail.mode.opens: the Crystal's rifts); no wave sends units through it. */
export function openedByMode(door, mode) {
  const opens = mode?.opens ?? []
  return !!door && door.spawners.length > 0 && door.spawners.every((s) => opens.includes(s))
}

/** A door entry's tab text and its full label. */
export function doorTab(door) {
  return door.kind === 'rift' ? `Unstable Rift ${door.short}`
    : door.kind === 'spawner' ? `Spawner ${door.short}` : door.label
}
export function doorLabel(door) {
  return door.label
}

/** 'fly' for a flyer-only door, 'ground' for a walking one, 'both' when it has both. */
export function doorType(door) {
  return door?.pathing === 'fly' ? 'fly' : door?.pathing === 'both' ? 'both' : 'ground'
}

/** True when units sent through `from` can come through `to`: same type, or either
 *  door takes both. */
export function doorsMatch(from, to) {
  const a = doorType(from), b = doorType(to)
  return a === b || a === 'both' || b === 'both'
}

/**
 * Replace one door's spawns in every wave with another onslaught's door.
 *
 * `rows[i]` is what the source door sends in wave i. The target door gets exactly
 * those rows; waves past the source's last send nothing through it, and waves the
 * spec lacks are added.
 */
export function copyDoor(spec, spawner, rows) {
  const key = String(spawner)
  while (spec.waves.length < rows.length) spec.waves.push(newWave())
  spec.waves.forEach((wave, i) => {
    const got = (rows[i] ?? []).map((r) => ({ ...newSpawn(r.unit), ...r, id: nextId++ }))
    if (got.length) wave.spawns[key] = got
    else delete wave.spawns[key]
  })
}

/**
 * Replace what one door sends in one wave (`into`, 0-based) with `rows`. Waves the spec
 * lacks up to `into` are added; every other wave is left as it is.
 */
export function copyDoorWave(spec, spawner, rows, into) {
  const key = String(spawner)
  while (spec.waves.length <= into) spec.waves.push(newWave())
  const got = (rows ?? []).map((r) => ({ ...newSpawn(r.unit), ...r, id: nextId++ }))
  if (got.length) spec.waves[into].spawns[key] = got
  else delete spec.waves[into].spawns[key]
}

/** The count a row writes (wavespec.row_count). */
function rowCount(r) {
  const n = Number(r.count) || 0
  return n < 1 && !(Number(r.addCount) || 0) ? 1 : Math.max(0, n)
}
const rowByte = (r) => Number(r.difficulty ?? ALL_DIFFICULTIES)
/** True when the row sends units on the first Endless loop. */
const sendsLoopOne = (r) => !!r.unit && LOOP_ONE.includes(rowByte(r)) && rowCount(r) > 0
/** True when the row sends units on some Endless loop after the first. */
const sendsLater = (r) => !!r.unit
  && (LOOP_ONE.includes(rowByte(r)) || rowByte(r) === ENDLESS_DIFFICULTY)
  && (rowCount(r) > 0 || (Number(r.addCount) || 0) > 0)

/**
 * Where later Endless loops leave loop 1's door pattern (wavespec.endless_doors).
 *
 * The game skips Endless rows on the first Endless loop and plays them from the
 * second, so an Endless row on a door that sends nothing in that wave on loop 1 puts
 * the door to work there on every later loop. Returns door key -> {label, loop1,
 * later, early, waves, into}: `loop1`/`later` the first wave the door sends in on loop
 * 1 / from loop 2 (1-based, null when never), `early` the ids of those rows, `waves`
 * the waves they are in, `into` wave -> labels of the doors that send in it on loop 1
 * (where Reset door after Onslaught Loop moves them). Only doors with early rows.
 */
export function endlessDoors(spec, detail) {
  const doors = (detail?.doors ?? []).map((d) => ({ ...d, keys: (d.spawners ?? []).map(String) }))
  const rowsOf = (wave, door) => door.keys.flatMap((k) => wave.spawns?.[k] ?? [])
  const active = spec.waves.map((wave) => doors.filter((d) => rowsOf(wave, d).some(sendsLoopOne)))
  const out = {}
  for (const door of doors) {
    let loop1 = null, later = null
    const early = new Set(), waves = [], into = {}
    spec.waves.forEach((wave, i) => {
      const rows = rowsOf(wave, door)
      if (loop1 == null && rows.some(sendsLoopOne)) loop1 = i + 1
      if (later == null && rows.some(sendsLater)) later = i + 1
      if (!active[i].length || active[i].includes(door)) return
      const here = rows.filter(sendsLater)
      if (!here.length) return
      here.forEach((r) => early.add(r.id))
      waves.push(i + 1)
      into[i + 1] = active[i].map((d) => d.label)
    })
    if (early.size) out[door.key] = { label: door.label, loop1, later, early, waves, into }
  }
  return out
}

/** "1, 2 and 4" */
export function waveList(waves) {
  return waves.length < 2 ? waves.join('') : `${waves.slice(0, -1).join(', ')} and ${waves.at(-1)}`
}

/** One line naming the doors later Endless loops add to waves, or ''. */
export function endlessSummary(doors) {
  const list = Object.values(doors)
  if (!list.length) return ''
  return list.map((d) => `${d.label} in wave${d.waves.length === 1 ? '' : 's'} ${waveList(d.waves)}`).join('; ')
}
