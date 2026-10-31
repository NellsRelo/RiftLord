// Settings of the shipped actors the Map Editor places or picks (riftlord/controllers.py,
// riftlord/envtraps.py, GET /api/controllers):
//
// * Wall Plate on a wall torch: the level's own (plan.wallPlates, '<sublevel>|<export>':
//   bool, only where it differs from the level) or an added one (entry.wallPlate).
// * Sound on an added AmbientSound (entry.cue), Retrigger Time and Glow Color on added
//   lightning (entry.retrigger [min, max], entry.glow [r, g, b, a]), Pour Length on an
//   added cauldron (entry.pour).
// * Group, Chance and rewards on an added treasure chest (entry.chest, riftlord/kitactors.py).
// * A falling boom barrel hangs from a chain by its actor origin (hangAnchor):
//   barrelUnder(point) is the entry that hangs one from a chain's lower end.

const esc = (text) => String(text ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/>/g, '&gt;').replace(/"/g, '&quot;')

let ctx = null
let choices = null
//: {level object, map: '<sublevel>|<export>' -> whether the level's torch has its plate}
let shippedPlates = { level: null, map: new Map() }

/** Hand the module the editor's state and helpers, and fetch what it offers. */
export function useControllers (context) {
  ctx = context
  ctx.json('api/controllers').then((got) => {
    choices = got
    ctx.reapply()
  }).catch(() => { choices = null })
}

const torchOf = (cls) => (choices && choices.torches && choices.torches[cls]) || null
const actorKey = (p) => `${p.sub || 'persistent'}|${p.export}`

/** '<sublevel>|<export>' -> whether each wall torch of the level ships with its plate:
 * its plate parts are drawn from the level's own components, not the class's. */
function levelPlates () {
  const level = ctx.level()
  if (shippedPlates.level === level) return shippedPlates.map
  const map = new Map()
  for (const p of (level && level.pieces) || []) {
    const t = p.source === 'blueprint' && torchOf(p.actor)
    if (!t || !t.plateMeshes.includes(p.mesh)) continue
    const key = actorKey(p)
    map.set(key, (map.get(key) || false) || (p.component !== undefined && p.component !== null))
  }
  shippedPlates = { level, map }
  return map
}

function plateOn (key) {
  const set = (ctx.plan().wallPlates || {})[key]
  return set === undefined ? !!levelPlates().get(key) : set
}

/** Whether one of the level's own pieces is a wall plate the plan or the level turns off. */
export function plateHidden (piece) {
  const t = piece && piece.source === 'blueprint' && torchOf(piece.actor)
  return !!t && t.plateMeshes.includes(piece.mesh) && !plateOn(actorKey(piece))
}

/** Whether a part of an added actor is drawn: no wall plate on a torch without one, no
 * lightning ambient sphere (a 10,000 uu shell). */
export function drawsPart (entry, part) {
  const t = torchOf(entry.name)
  if (t && t.plateComponents.includes(part.component)) {
    return entry.wallPlate === undefined ? t.default : entry.wallPlate
  }
  const bolt = choices && choices.lightning
  if (bolt && entry.name === bolt.cls) return !bolt.hiddenParts.includes(part.component)
  return true
}

/** The added kit entry or the level torch the panel is about, or null. */
function subject (picked) {
  if (!choices || !picked.length) return null
  const one = picked[0]
  if (picked.length === 1 && one.added && one.added.kind === 'kit') {
    return { entry: one.added, cls: one.added.name }
  }
  const keys = new Set(picked.filter(p => !p.added && !p.marker).map(actorKey))
  if (keys.size === 1 && picked.every(p => !p.added && p.source === 'blueprint') &&
      torchOf(one.actor)) {
    return { key: [...keys][0], cls: one.actor }
  }
  return null
}

const select = (id, options, now) => `<select id="${id}">` + options.map(([v, label]) =>
  `<option value="${esc(v)}"${String(v) === String(now) ? ' selected' : ''}>${esc(label)}` +
  '</option>').join('') + '</select>'

function toHex (rgb) {
  return '#' + rgb.slice(0, 3).map(v => Math.round(Math.max(0, Math.min(1, v)) * 255)
    .toString(16).padStart(2, '0')).join('')
}

/** The Glow Color as a colour and a brightness: the colour scaled so its brightest channel
 * is 1, and that channel's value. */
function glowParts (glow) {
  const top = Math.max(glow[0], glow[1], glow[2], 1e-6)
  return { hex: toHex(glow.map(v => v / top)), brightness: Math.round(top * 100) / 100 }
}

/** The panel sections for the picked actor's own settings, or ''. */
export function controllersHtml (picked) {
  const s = subject(picked)
  if (!s) return ''
  const out = []
  const t = torchOf(s.cls)
  if (t) {
    const on = s.entry ? (s.entry.wallPlate === undefined ? t.default : s.entry.wallPlate)
      : plateOn(s.key)
    out.push('<label class="pick"><span>Wall Plate</span>' +
      select('ctlplate', [['on', 'On'], ['off', 'Off']], on ? 'on' : 'off') + '</label>')
  }
  if (!s.entry) return out.join('')
  const e = s.entry
  if (e.name === choices.ambientClass && choices.ambience.length) {
    const now = e.cue || choices.defaultCue
    const group = (bed) => choices.ambience.filter(c => c.bed === bed)
      .map(c => `<option value="${esc(c.key)}"${c.key === now ? ' selected' : ''}>` +
        `${esc(c.label)}</option>`).join('')
    out.push('<label class="pick"><span>Sound</span><select id="ctlcue">' +
      `<optgroup label="Emitters">${group(false)}</optgroup>` +
      `<optgroup label="Beds">${group(true)}</optgroup></select></label>`)
  }
  const bolt = choices.lightning
  if (bolt && e.name === bolt.cls) {
    const r = e.retrigger || bolt.retrigger
    const g = glowParts(e.glow || bolt.glow)
    out.push('<div class="pair">' + [0, 1].map(i =>
      `<label class="pick"><span>${i ? 'Retrigger Max' : 'Retrigger Min'}</span>` +
      `<input type="number" data-ctlretrigger="${i}" min="0" max="${bolt.retriggerMax}" ` +
      `step="0.5" value="${r[i]}"></label>`).join('') + '</div>')
    out.push('<div class="pair"><label class="pick"><span>Glow Color</span>' +
      `<input type="color" id="ctlglow" value="${g.hex}"></label>` +
      '<label class="pick"><span>Glow Brightness</span><input type="number" ' +
      `id="ctlglowbright" min="0" max="${bolt.glowMax}" step="0.1" value="${g.brightness}">` +
      '</label></div>')
  }
  const traps = choices.envtraps
  if (traps && e.name === traps.cauldron && traps.pours.length) {
    out.push('<label class="pick"><span>Pour Length</span>' +
      select('ctlpour', traps.pours.map(p => [p.key, p.label]), e.pour || traps.defaultPour) +
      '</label>')
  }
  const kit = choices.kitactors
  if (kit && e.name === kit.chest) out.push(chestHtml(e, kit))
  return out.join('')
}

// -- treasure chests (riftlord/kitactors.py) ----------------------------------------------
//
// entry.chest = {group, chance, coin: [min, max], coinChance, skulls: [min, max],
// skullChance}. Spots sharing a group are one chest: every spot of a group holds the same
// settings, so an edit is written to all of them.

const isChest = (a, kit) => !!a && a.kind === 'kit' && a.name === kit.chest

/** The groups the plan's chest spots name, in the order first placed. */
function chestGroups (kit) {
  const out = []
  for (const a of ctx.plan().added || []) {
    if (isChest(a, kit) && a.chest && a.chest.group && !out.includes(a.chest.group)) {
      out.push(a.chest.group)
    }
  }
  return out
}

/** A group name no spot uses: "Chest N". */
function newGroupName (kit) {
  const used = new Set(chestGroups(kit))
  let n = used.size + 1
  while (used.has(`Chest ${n}`)) n++
  return `Chest ${n}`
}

/** The plan's chest spots in `group`. */
const chestSpots = (kit, group) => (ctx.plan().added || [])
  .filter(a => isChest(a, kit) && a.chest && a.chest.group === group)

/** A chest entry's settings; a new chest gets the defaults in a group of its own. */
function chestOf (e, kit) {
  if (!e.chest) e.chest = { ...structuredClone(kit.defaults), group: newGroupName(kit) }
  return e.chest
}

function chestHtml (e, kit) {
  const c = chestOf(e, kit)
  const num = (id, label, value, max) => `<label class="pick"><span>${label}</span>` +
    `<input type="number" id="${id}" min="0" max="${max}" step="1" value="${value}"></label>`
  const groups = chestGroups(kit).map(g => [g, g]).concat([['', 'New Group']])
  return '<label class="pick"><span>Group</span>' + select('ctlchestgroup', groups, c.group) +
    '</label>' + num('ctlchestchance', 'Chance %', c.chance, 100) +
    '<div class="pair">' + num('ctlcoinmin', 'Coin Min', c.coin[0], kit.intMax) +
    num('ctlcoinmax', 'Coin Max', c.coin[1], kit.intMax) + '</div>' +
    '<div class="pair">' + num('ctlskullmin', 'Skull Min', c.skulls[0], kit.intMax) +
    num('ctlskullmax', 'Skull Max', c.skulls[1], kit.intMax) + '</div>' +
    '<div class="pair">' + num('ctlcoinpct', 'Coin %', c.coinChance, 100) +
    num('ctlskullpct', 'Skull %', c.skullChance, 100) + '</div>' +
    '<label class="pick"><span>Coin Lean (0 = low, 1 = high)</span>' +
    `<input type="number" id="ctlcoinlean" min="0" max="1" step="0.05" ` +
    `value="${c.coinLean ?? COIN_LEAN_DEFAULT}"></label>` +
    '<button id="ctlchestspot">Add Spot</button>'
}

const clampNum = (v, hi) => Math.max(0, Math.min(hi, Math.round(+v || 0)))
//: Coin Lean when a chest sets none: an even spread over Coin Min..Max (kitactors.py)
const COIN_LEAN_DEFAULT = 0.5

/** Wire the chest panel: settings go to every spot of the group; Add Spot places a spot
 * of the same group beside this one. */
function wireChest (body, e, kit) {
  const field = (id) => body.querySelector('#' + id)
  const group = field('ctlchestgroup')
  if (!group) return
  group.addEventListener('change', () => {
    ctx.snapshot()
    if (group.value) {
      const other = chestSpots(kit, group.value).find(a => a !== e)
      e.chest = other ? structuredClone(other.chest) : { ...e.chest, group: group.value }
    } else {
      e.chest = { ...e.chest, group: newGroupName(kit) }
    }
    ctx.showProps()
  })
  const write = (change) => {
    ctx.snapshot()
    const spots = chestSpots(kit, e.chest.group)
    for (const a of spots.includes(e) ? spots : [e]) change(a.chest)
    ctx.showProps()
  }
  const pair = (lo, hi, key, max) => {
    for (const id of [lo, hi]) {
      const input = field(id)
      if (!input) continue
      input.addEventListener('change', () => {
        const a = clampNum(field(lo).value, max)
        const b = clampNum(field(hi).value, max)
        write(c => { c[key] = [Math.min(a, b), Math.max(a, b)] })
      })
    }
  }
  pair('ctlcoinmin', 'ctlcoinmax', 'coin', kit.intMax)
  pair('ctlskullmin', 'ctlskullmax', 'skulls', kit.intMax)
  const chance = field('ctlchestchance')
  chance.addEventListener('change', () => {
    const v = clampNum(chance.value, 100)
    write(c => { c.chance = v })
  })
  const lean = field('ctlcoinlean')
  lean.addEventListener('change', () => {
    const v = Math.max(0, Math.min(1, Math.round((+lean.value || 0) * 100) / 100))
    write(c => { c.coinLean = v })
  })
  //: Coin % and Skull % share the reward: one is 100 less the other
  for (const [id, key, other] of [['ctlcoinpct', 'coinChance', 'skullChance'],
    ['ctlskullpct', 'skullChance', 'coinChance']]) {
    const input = field(id)
    input.addEventListener('change', () => {
      const v = clampNum(input.value, 100)
      write(c => { c[key] = v; c[other] = 100 - v })
    })
  }
  field('ctlchestspot').addEventListener('click', () => {
    const m = e.matrix
    const len = Math.hypot(m[4], m[5], m[6]) || 1
    const step = kit.spotOffset / len
    const matrix = m.slice()
    matrix[12] += m[4] * step
    matrix[13] += m[5] * step
    matrix[14] += m[6] * step
    ctx.addEntry({ kind: 'kit', name: kit.chest, matrix, chest: structuredClone(e.chest) })
  })
}

/** Wire the sections controllersHtml drew. */
export function wireControllers (body, picked) {
  const s = subject(picked)
  if (!s) return
  const plate = body.querySelector('#ctlplate')
  if (plate) {
    plate.addEventListener('change', async () => {
      ctx.snapshot()
      const on = plate.value === 'on'
      if (s.entry) {
        const t = torchOf(s.cls)
        if (on === t.default) delete s.entry.wallPlate
        else s.entry.wallPlate = on
        await ctx.redrawAdded()
      } else {
        const plan = ctx.plan()
        plan.wallPlates = plan.wallPlates || {}
        if (on === !!levelPlates().get(s.key)) delete plan.wallPlates[s.key]
        else plan.wallPlates[s.key] = on
        if (!Object.keys(plan.wallPlates).length) delete plan.wallPlates
        ctx.reapply()
      }
      ctx.showProps()
    })
  }
  if (!s.entry) return
  const e = s.entry
  const cue = body.querySelector('#ctlcue')
  if (cue) cue.addEventListener('change', () => { ctx.snapshot(); e.cue = cue.value })
  const pour = body.querySelector('#ctlpour')
  if (pour) pour.addEventListener('change', () => { ctx.snapshot(); e.pour = pour.value })
  const bolt = choices.lightning
  for (const input of body.querySelectorAll('[data-ctlretrigger]')) {
    input.addEventListener('change', () => {
      ctx.snapshot()
      const r = (e.retrigger || bolt.retrigger).slice()
      r[+input.dataset.ctlretrigger] = Math.max(0, Math.min(bolt.retriggerMax, +input.value))
      e.retrigger = [Math.min(r[0], r[1]), Math.max(r[0], r[1])]
      ctx.showProps()
    })
  }
  const colour = body.querySelector('#ctlglow')
  const bright = body.querySelector('#ctlglowbright')
  const setGlow = () => {
    ctx.snapshot()
    const hex = colour.value
    const scale = Math.max(0, Math.min(bolt.glowMax, +bright.value))
    const rgb = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255 * scale)
    e.glow = rgb.map(v => Math.round(v * 10000) / 10000).concat([1])
  }
  if (colour) colour.addEventListener('change', setGlow)
  if (bright) bright.addEventListener('change', setGlow)
  const kit = choices.kitactors
  if (kit && e.name === kit.chest) wireChest(body, e, kit)
}

/** A falling boom barrel's added entry hanging from a chain whose lower end is `point`
 * ([x, y, z], world), turned `yaw` degrees. */
export function barrelUnder (point, yaw = 0) {
  const traps = choices && choices.envtraps
  if (!traps) return null
  const a = traps.hangAnchor
  const r = yaw * Math.PI / 180
  const c = Math.cos(r)
  const s = Math.sin(r)
  const x = point[0] - (c * a[0] - s * a[1])
  const y = point[1] - (s * a[0] + c * a[1])
  return { kind: 'kit', name: traps.barrel,
           matrix: [c, s, 0, 0, -s, c, 0, 0, 0, 0, 1, 0, x, y, point[2] - a[2], 1] }
}
