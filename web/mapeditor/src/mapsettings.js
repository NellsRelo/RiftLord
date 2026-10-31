// The right sidebar while nothing is selected: the open level's map settings.
//
// Reads /api/mapinfo (riftlord/mapinfo.py). World settings the plan changes are kept
// in `plan.world` ({killZ, warScenario, intro, introFilter, storyDialog, riftWarning, music}
// and the fog and post-process keys of riftlord/levelfx.py); the emitter writes them into
// the level. The music change is the `music` event of `plan.events` (riftlord/hostevents.py).
// Mission details (Hub, Guid) are in the Details dialog, not here.

//: Whether the War Scenario switch may be changed. The emitter writes it to the
//: level's WorldSettings and to the mission row.
export const WAR_SCENARIO_EDITABLE = true

import { music, bindMusicButton } from '../../shared/musicpreview.js'

const esc = (text) => String(text ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/>/g, '&gt;').replace(/"/g, '&quot;')

const row = (label, value) =>
  `<div class="row"><span>${esc(label)}</span><span>${esc(value)}</span></div>`

//: Linear colour channel to sRGB and back (the fog colour is stored linear).
const toSrgb = (c) => (c <= 0.0031308 ? 12.92 * c : 1.055 * Math.pow(c, 1 / 2.4) - 0.055)
const toLinear = (c) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4))

/** '#rrggbb' for a linear [r, g, b, a]. */
function toHex (rgba) {
  return '#' + (rgba || [0, 0, 0]).slice(0, 3).map(c =>
    Math.round(Math.min(1, Math.max(0, toSrgb(c))) * 255).toString(16).padStart(2, '0'))
    .join('')
}

/** A linear [r, g, b, a] for '#rrggbb', keeping alpha `a`. */
function fromHex (hex, a) {
  const n = parseInt(hex.slice(1), 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
    .map(v => Math.round(toLinear(v / 255) * 10000) / 10000).concat([a ?? 1])
}

//: The Music preview (web/shared/musicpreview.js, shared with the Onslaught Editor). It
//: plays while the map settings panel is on screen (its `data-mapsettings` card), through
//: re-renders and while the map info reloads; a re-render naming another track switches to
//: it; it stops when another panel replaces it, the level closes, the page goes away, or the
//: author changes the selection.
const MUSIC_PLACE = 'mapsettings'
const panelGone = () => !document.querySelector('[data-mapsettings]')

/** The panel's HTML.
 *
 * ctx: {level, plan, info, levelTitle, levelName?, source?} -- `info` is /api/mapinfo's
 * answer or null while it loads; `levelName` replaces the level's name under the title
 * (null: none); `source` ({title, name}) is the shipped level a mission is built on. */
export function mapSettingsHtml (ctx) {
  const { level, plan, info } = ctx
  const world = (info && info.world) || {}
  const set = (plan && plan.world) || {}
  const ws = 'warScenario' in set ? set.warScenario : !!world.bIsWarScenario
  const title = (ctx.levelTitle || level.level)
  const src = ctx.source
  const out = [`<div class="card" data-mapsettings><b>${esc(title)}</b><div class="meta">` +
    ['levelName' in ctx ? ctx.levelName : level.level, ws ? 'War Scenario' : null]
      .filter(Boolean).map(esc).join(' &middot; ') + '</div>' +
    (src ? '<div class="meta" data-source>Source Level: ' +
      esc(src.title && src.title !== src.name ? `${src.title} (${src.name})` : src.name) + '</div>' : '') +
    '</div>']

  // world settings
  out.push('<h2>World Settings</h2>')
  if (info && info.worldError) {
    out.push(`<div class="hint bad">Could not read them: ${esc(info.worldError)}</div>`)
  } else if (!info) {
    out.push('<div class="hint"><span class="spinner"></span>Reading&hellip;</div>')
  } else {
    const kz = 'killZ' in set ? set.killZ : world.KillZ
    out.push('<label class="pick"><span>Kill Z</span>' +
      `<input id="worldkillz" type="number" step="100" value="${esc(kz)}"></label>`)
    out.push(`<label class="row"><span>War Scenario</span><input id="worldws" type="checkbox"` +
      `${ws ? ' checked' : ''}${WAR_SCENARIO_EDITABLE ? '' : ' disabled'}></label>`)
    if ('killZ' in set || 'warScenario' in set) {
      out.push('<button id="worldreset">Reset</button>')
    }
  }

  // the intro card (riftlord/levelintro.py), the intro's colour filter (riftlord/levelfx.py)
  // and the story dialogue (riftlord/storyvo.py)
  const intro = info && info.intro
  const tint = info && (info.introFilter || []).length
  const story = info && info.storyDialog
  const hasStory = !!(story && (story.gates || story.lines))
  if ((intro && intro.removable) || tint || hasStory) out.push('<h2>Intro and Dialogue</h2>')
  if (intro && intro.removable) out.push(onOffPick('Intro', 'introcard', set.intro !== 'remove'))
  if (tint) {
    const removed = set.introFilter === 'remove'
    const noStory = set.storyDialog === 'remove'
    out.push('<label class="pick"><span>Intro Tint</span>' +
      `<select id="introfilter"${noStory ? ' disabled' : ''}>` +
      `<option value="keep"${removed ? '' : ' selected'}>Sepia</option>` +
      `<option value="remove"${removed ? ' selected' : ''}>None</option></select></label>`)
  }
  if (hasStory) {
    out.push(onOffPick('Story Dialogue', 'storydialog', set.storyDialog !== 'remove'))
  }

  // the level's music (riftlord/levelmusic.py) and its music change (riftlord/hostevents.py)
  if (info && (info.music || []).length && (info.musicChoices || []).length) {
    const change = canChangeMusic(ctx)
    const options = (now) => info.musicChoices.map(c =>
      `<option value="${esc(c.key)}"${now === c.key ? ' selected' : ''}>` +
      `${esc(c.label)}${c.levels.length ? ' — ' + esc(c.levels.slice(0, 3).join(', ')) +
        (c.levels.length > 3 ? '…' : '') : ''}</option>`).join('')
    const label = (key) => {
      const c = info.musicChoices.find(x => x.key === key)
      return c ? c.label : key
    }
    //: the level's own track is selected when the plan sets none; a level that plays
    //: several and cannot change between them gets one entry naming them all
    const ownKey = change ? openingMusic(info) : ownMusic(info)
    const now = set.music || ownKey
    const own = !change && info.music.length > 1
      ? `<option value=""${set.music ? '' : ' selected'}>` +
        `${esc(info.music.map(label).join(' / '))}</option>`
      : (info.musicChoices.some(c => c.key === ownKey) ? ''
          : `<option value="${esc(ownKey)}"${set.music ? '' : ' selected'}>${esc(ownKey)}</option>`)
    out.push('<h2>Music</h2>')
    out.push(`<label class="pick"><span>${change ? 'Opening' : 'Music'}</span><div class="musicrow">` +
      '<select id="levelmusic">' + own + options(now) +
      `</select><button id="musicplay" class="iconbtn" data-own="${esc(ownKey)}" ` +
      'title="Hear this track"></button></div></label>')
    if (change) {
      const ev = musicEvent(ctx)
      const track = ev ? ev.track : ''
      out.push(`<label class="pick"><span>Change${ev ? ' at Wave ' + esc(ev.wave) : ''}</span>` +
        '<select id="musicchange">' +
        `<option value=""${track ? '' : ' selected'}>None</option>` + options(track) +
        '</select></label>')
    }
  }

  // the rift's "enemies near the rift" warning box (riftlord/riftwarning.py)
  const warn = info && info.riftWarning
  if (warn) {
    const [lo, hi, step] = info.riftWarningRange || [100, 20000, 50]
    const now = set.riftWarning || warn.size
    out.push('<h2>Rift Warning</h2><div class="pair">')
    ;['Length', 'Width', 'Height'].forEach((label, i) => {
      out.push(`<label class="pick"><span>${label} (uu)</span>` +
        `<input type="number" data-riftwarn="${i}" min="${lo}" max="${hi}" step="${step}" ` +
        `value="${esc(Math.round(now[i]))}"></label>`)
    })
    out.push('</div>')
    if (set.riftWarning) out.push('<button id="riftwarnreset">Reset</button>')
  }

  // fog and post process (riftlord/levelfx.py SCENE_FIELDS); only what the level sets:
  // numbers two to a row, the colours on one row
  if (info && info.sceneFields) {
    const shipped = info.scene || {}
    const shown = info.sceneFields.filter(f => f.key in shipped)
    const groups = [...new Set(shown.map(f => f.group))]
    for (const group of groups) {
      out.push(`<h2>${esc(group)}</h2>`)
      const fields = shown.filter(f => f.group === group)
      const field = (f) => {
        const now = f.key in set ? set[f.key] : shipped[f.key]
        const input = f.kind === 'colour'
          ? `<input type="color" data-scene="${f.key}" value="${toHex(now)}">`
          : `<input type="number" data-scene="${f.key}" min="${f.min}" max="${f.max}" ` +
            `step="${f.step}" value="${esc(now)}">`
        return `<label class="pick"><span>${esc(f.label)}</span>${input}</label>`
      }
      const numbers = fields.filter(f => f.kind !== 'colour')
      const colours = fields.filter(f => f.kind === 'colour')
      if (numbers.length) out.push('<div class="pair">' + numbers.map(field).join('') + '</div>')
      if (colours.length) {
        out.push(`<div class="pair" style="grid-template-columns: repeat(${colours.length}, 1fr)">` +
          colours.map(field).join('') + '</div>')
      }
    }
    if (shown.some(f => f.key in set)) out.push('<button id="scenereset">Reset</button>')
  }

  // what the level holds
  out.push('<h2>Map</h2>')
  out.push(row('Sublevels', (level.sublevels || []).length))
  out.push(row('Pieces', (level.pieces || []).length))
  out.push(row('Spawners', (level.spawners || []).length))
  out.push(row('Doors', Object.keys(level.doors || {}).length))
  out.push(row('Path Points', Object.keys(level.points || {}).length))
  const rift = (level.rifts || [])[0]
  out.push(row('Rift', rift ? rift.loc.map(v => Math.round(v)).join(', ') : 'none'))
  return out.join('')
}

/** A new mission's `plan.world` defaults, given the level's map info: the intro card
 * and the story dialogue Off where the level has them. Returns whether it changed. */
export function newMissionWorld (info, world) {
  let changed = false
  if (info && info.intro && info.intro.removable && !('intro' in world)) {
    world.intro = 'remove'
    changed = true
  }
  const story = info && info.storyDialog
  if (story && (story.gates || story.lines) && !('storyDialog' in world)) {
    world.storyDialog = 'remove'
    changed = true
  }
  return changed
}

/** An On/Off picker. */
function onOffPick (label, id, on) {
  return `<label class="row toggle"><span>${esc(label)}</span>` +
    `<input id="${id}" type="checkbox" class="switch" role="switch"${on ? ' checked' : ''}></label>`
}

//: The wave a new music change fires in; the Onslaught Editor's Timeline moves it.
const FIRST_WAVE = 1

/** Whether the mission can change music: the host can, or the Mission Events piece is
 * placed (riftlord/missionevents.py). */
function canChangeMusic (ctx) {
  const mc = ctx.info && ctx.info.musicChange
  if (!mc) return false
  return !!mc.available || ((ctx.plan && ctx.plan.added) || []).some(a => a.kind === 'events')
}

/** The mission's events: the plan's, or as its waves hold them when the plan has none. */
function missionEvents (ctx) {
  const own = ctx.plan && ctx.plan.events
  const got = Array.isArray(own) ? own : (((ctx.info && ctx.info.musicChange) || {}).events || [])
  return JSON.parse(JSON.stringify(got))
}

/** The mission's music change event, or undefined. */
function musicEvent (ctx) {
  return missionEvents(ctx).find(e => e.kind === 'music')
}

/** The track the level opens with: the host's opening loop, else its first. */
function openingMusic (info) {
  return ((info && info.musicChange) || {}).opening || ownMusic(info)
}

/** The level's own music track key (the first when it plays several). */
function ownMusic (info) {
  return ((info && info.music) || [])[0] || ''
}

/** Wire the panel's controls. `onWorld(world, events)` gets the new `plan.world`, and
 * the new `plan.events` when they change (undefined otherwise). */
export function wireMapSettings (body, ctx, { onWorld }) {
  const world = Object.assign({}, (ctx.plan && ctx.plan.world) || {})
  const shipped = (ctx.info && ctx.info.world) || {}
  const kz = body.querySelector('#worldkillz')
  if (kz) {
    kz.addEventListener('change', () => {
      const v = kz.value.trim()
      if (v === '' || +v === shipped.KillZ) delete world.killZ
      else if (Number.isFinite(+v)) world.killZ = +v
      onWorld(world)
    })
  }
  const ws = body.querySelector('#worldws')
  if (ws && WAR_SCENARIO_EDITABLE) {
    ws.addEventListener('change', () => {
      if (ws.checked === !!shipped.bIsWarScenario) delete world.warScenario
      else world.warScenario = ws.checked
      onWorld(world)
    })
  }
  const scene = (ctx.info && ctx.info.scene) || {}
  for (const input of body.querySelectorAll('[data-scene]')) {
    input.addEventListener('change', () => {
      const key = input.dataset.scene
      const field = ((ctx.info && ctx.info.sceneFields) || []).find(f => f.key === key)
      if (!field) return
      if (field.kind === 'colour') {
        const v = fromHex(input.value, (scene[key] || [])[3])
        if (toHex(v) === toHex(scene[key])) delete world[key]
        else world[key] = v
      } else {
        const v = +input.value
        if (input.value.trim() === '' || v === scene[key]) delete world[key]
        else if (Number.isFinite(v)) world[key] = Math.min(field.max, Math.max(field.min, v))
      }
      onWorld(world)
    })
  }
  const sceneReset = body.querySelector('#scenereset')
  if (sceneReset) {
    sceneReset.addEventListener('click', () => {
      for (const f of (ctx.info && ctx.info.sceneFields) || []) delete world[f.key]
      onWorld(world)
    })
  }
  const intro = body.querySelector('#introfilter')
  if (intro) {
    intro.addEventListener('change', () => {
      if (intro.value === 'remove') world.introFilter = 'remove'
      else delete world.introFilter
      onWorld(world)
    })
  }
  const musicSel = body.querySelector('#levelmusic')
  const musicBtn = body.querySelector('#musicplay')
  if (musicBtn) {
    const track = () => (musicSel && musicSel.value) || musicBtn.dataset.own
    // a re-render that names another track (the mission's plan arriving after the
    // level's own settings) plays that one; the author's own change stops it (below)
    bindMusicButton(musicBtn, { place: MUSIC_PLACE, track, gone: panelGone })
  } else if (music.playing() && ctx.info) {
    music.stop()                    // the map info has loaded and has no music section
  }
  const change = canChangeMusic(ctx)
  if (musicSel) {
    musicSel.addEventListener('change', () => {
      music.stop()
      const own = change ? openingMusic(ctx.info) : ownMusic(ctx.info)
      const single = change || (ctx.info.music || []).length === 1
      if (!musicSel.value || (single && musicSel.value === own)) delete world.music
      else world.music = musicSel.value
      // a track for the opening alone needs the change event in the plan's events
      const mc = ctx.info.musicChange || {}
      const seed = change && mc.available && !Array.isArray(ctx.plan && ctx.plan.events)
      onWorld(world, seed ? missionEvents(ctx) : undefined)
    })
  }
  const changeSel = body.querySelector('#musicchange')
  if (changeSel) {
    changeSel.addEventListener('change', () => {
      music.stop()
      const events = missionEvents(ctx)
      const i = events.findIndex(e => e.kind === 'music')
      if (!changeSel.value) {
        if (i >= 0) events.splice(i, 1)
      } else if (i >= 0) {
        events[i].track = changeSel.value
      } else {
        events.push({ kind: 'music', wave: (ctx.info.musicChange || {}).wave || FIRST_WAVE,
                      track: changeSel.value })
      }
      onWorld(world, events)
    })
  }
  const storySel = body.querySelector('#storydialog')
  if (storySel) {
    storySel.addEventListener('change', () => {
      if (!storySel.checked) world.storyDialog = 'remove'
      else delete world.storyDialog
      onWorld(world)
    })
  }
  const introSel = body.querySelector('#introcard')
  if (introSel) {
    introSel.addEventListener('change', () => {
      if (!introSel.checked) world.intro = 'remove'
      else delete world.intro
      onWorld(world)
    })
  }
  const warn = ctx.info && ctx.info.riftWarning
  for (const input of body.querySelectorAll('[data-riftwarn]')) {
    input.addEventListener('change', () => {
      const [lo, hi] = (ctx.info && ctx.info.riftWarningRange) || [100, 20000]
      const size = (world.riftWarning || warn.size).slice()
      const v = +input.value
      if (input.value.trim() === '' || !Number.isFinite(v)) return
      size[+input.dataset.riftwarn] = Math.min(hi, Math.max(lo, v))
      if (size.every((s, i) => Math.abs(s - warn.size[i]) < 0.5)) delete world.riftWarning
      else world.riftWarning = size
      onWorld(world)
    })
  }
  const warnReset = body.querySelector('#riftwarnreset')
  if (warnReset) {
    warnReset.addEventListener('click', () => {
      delete world.riftWarning
      onWorld(world)
    })
  }
  const reset = body.querySelector('#worldreset')
  if (reset) {
    reset.addEventListener('click', () => {
      delete world.killZ
      delete world.warScenario
      onWorld(world)
    })
  }
}
