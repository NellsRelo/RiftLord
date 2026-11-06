// The Crystal of Crystal Defense (riftlord/mapeditor/crystal.py): one per mission, placed
// over the rift from New Object's Gameplay cards, drawn as its own model (the pieces it
// brings and its floating crystal, /api/crystal `parts`; a stand-in until they load), with
// a panel editing its Rift Groups: entry.crystal.groups = [{name, rifts: [{spawner}], ...}]
// (riftlord/mapeditor/rifttear.py). `spawner` is the id of a plan spawner with `unstable`.
// Everything else on the entry (Map Difficulty and the rift point overrides, each group's
// triggers, rows and chances) is the Onslaught Editor's, kept here as it comes.
import * as THREE from 'three'

export const CRYSTAL_KIND = 'crystal'
export const CRYSTAL_CLASS = 'BP_Crystal_C'
export const CRYSTAL_LABEL = 'Crystal'
//: the Gameplay card's dot, and its preview's name (/api/thumb, crystal.thumbnail)
export const CRYSTAL_CARD_COLOUR = '#e0405a'
export const CRYSTAL_THUMB = 'crystal:'
const FOOT_COLOUR = 0x8a7a9e
const CRYSTAL_COLOUR = 0xe0405a
//: The stand-in's sizes before /api/crystal answers (crystal.py FOOT_SIZE, CRYSTAL_SIZE,
//: CRYSTAL_CENTRE), uu in actor space; the foot's base is at the actor's origin.
const STAND_IN = { foot: [972, 1098, 407.9],
  crystal: { size: [333.4, 334.1, 1223.3], centre: [51.2, 20.8, 835.2] } }
//: Groups a crystal takes, and rifts a group takes (rifttear.py GROUPS_MAX, RIFTS_MAX).
const GROUPS_MAX = 16
const GROUP_RIFTS_MAX = 32
//: EOnslaughtDifficulty Any, and players Any (rifttear.py ANY, PLAYERS)
const ANY = 5
const ANY_PLAYERS = 0
//: plan keys of the health models before rift point health (crystal.py OLD_HEALTH_KEYS)
const OLD_HEALTH_KEYS = ['difficultyHealth', 'healthPerRiftPoint', 'minHealth']

let cache = null

/** /api/crystal for a level and the rift where it stands now: {spot: {at, yaw, matrix},
 * available, pak, defaults, standIn}. Resolves to the cached answer. */
export function crystalInfo (json, levelName, rift) {
  const key = levelName + '|' + (rift || []).map(v => Math.round(v)).join(',')
  if (!cache || cache.key !== key) {
    const q = rift ? '?rift=' + rift.map(v => (+v).toFixed(2)).join(',') : ''
    cache = { key, got: null,
              wait: json('api/crystal/' + encodeURIComponent(levelName) + q) }
    cache.wait.then(got => { if (cache && cache.key === key) cache.got = got })
      .catch(() => { if (cache && cache.key === key) cache = null })
  }
  return cache.wait
}

//: /api/crystal/<level>/fit for the Crystal where it stands (riftlord/mapeditor/crystalfit.py):
//: the HitX / HitY the build would fit there, shown as the fields' placeholders
let fitCache = null

/** The fitted hitbox for a Crystal at `matrix`: resolves to {half, default, footArt, range,
 * short, tooBig, aboveFloor, floorZ}. */
export function crystalFit (json, levelName, matrix) {
  const key = levelName + '|' + matrix.map(v => (+v).toFixed(1)).join(',')
  if (!fitCache || fitCache.key !== key) {
    fitCache = { key, got: null,
                 wait: json('api/crystal/' + encodeURIComponent(levelName) + '/fit?matrix=' +
                            matrix.map(v => (+v).toFixed(4)).join(',')) }
    fitCache.wait.then(got => { if (fitCache && fitCache.key === key) fitCache.got = got })
      .catch(() => { if (fitCache && fitCache.key === key) fitCache = null })
  }
  return fitCache.wait
}

//: The hitbox fields (crystalfit.FIELDS, DROP_KEY): plan key, label, the fit's value for it.
//: HitDrop may be 0 (the user's own); HitX / HitY blank or 0 are fitted.
const HIT_FIELDS = [['hitX', 'HitX', f => f.half[0]], ['hitY', 'HitY', f => f.half[1]],
  ['hitDrop', 'HitDrop', f => f.drop]]
const DROP = 'hitDrop'

/** The last /api/crystal answer, or null. */
export const crystalKnown = () => (cache && cache.got) || null

//: The level piece ids the placed Crystal parks (riftlord/mapeditor/crystalpark.py), hidden
//: in the viewport as the build takes them out of play.
export const crystalHidden = new Set()
let hiddenKey = ''
let hiddenTimer = null
//: A moved Crystal asks again once it has rested this long, in ms.
const HIDDEN_SETTLE = 250

/** Ask which level pieces a Crystal at `matrix` (none: no Crystal) parks; `apply` redraws
 * when they change. */
export function refreshCrystalHidden (json, levelName, matrix, apply) {
  const key = levelName + '|' + (matrix ? matrix.map(v => (+v).toFixed(1)).join(',') : '')
  if (key === hiddenKey) return
  hiddenKey = key
  clearTimeout(hiddenTimer)
  if (!matrix) {
    if (crystalHidden.size) {
      crystalHidden.clear()
      apply()
    }
    return
  }
  hiddenTimer = setTimeout(() => {
    json('api/crystal/' + encodeURIComponent(levelName) + '/parked?matrix=' +
         matrix.map(v => (+v).toFixed(4)).join(','))
      .then(got => {
        if (hiddenKey !== key) return
        const ids = (got.parked || []).map(r => r.id)
        if (ids.length === crystalHidden.size && ids.every(i => crystalHidden.has(i))) return
        crystalHidden.clear()
        for (const i of ids) crystalHidden.add(i)
        apply()
      })
      .catch(() => {})
  }, HIDDEN_SETTLE)
}

/** The Crystal's model from /api/crystal's `parts` ([{mesh, loc, rot, scale, fit, texture}]),
 * drawn with the Map Editor's `partMesh` and `partLocal`, round a faint foot box that picks
 * it; a part naming a `texture` takes it through `retexture(mesh, path)`; the stand-in when
 * no part draws. */
export async function crystalModel (got, partMesh, partLocal, retexture = null) {
  const parts = (got && got.parts) || []
  if (!parts.length) return crystalStandIn(got)
  const s = (got && got.standIn) || STAND_IN
  const [fx, fy, fz] = s.foot
  const footGeo = new THREE.BoxGeometry(fx, fy, fz)
  footGeo.translate(0, 0, fz / 2)
  const root = new THREE.Mesh(footGeo, new THREE.MeshBasicMaterial({
    color: FOOT_COLOUR, transparent: true, opacity: 0.08, depthWrite: false }))
  let drew = 0
  for (const part of parts) {
    const mesh = await partMesh(part, true)
    if (!mesh) continue
    if (part.texture && retexture) retexture(mesh, part.texture)
    if (part.fit) {
      //: scaled and moved into the box the floating crystal fills in game
      mesh.geometry.computeBoundingBox()
      const box = mesh.geometry.boundingBox
      const size = box.getSize(new THREE.Vector3())
      const centre = box.getCenter(new THREE.Vector3())
      const k = size.z > 0 ? part.fit.size[2] / size.z : 1
      mesh.scale.setScalar(k)
      mesh.position.set(part.fit.centre[0] - centre.x * k, part.fit.centre[1] - centre.y * k,
                        part.fit.centre[2] - centre.z * k)
    } else {
      partLocal(part).decompose(mesh.position, mesh.quaternion, mesh.scale)
    }
    root.add(mesh)
    drew++
  }
  return drew ? root : crystalStandIn(got)
}

/** Whether a plan `added` entry is the crystal. */
export const isCrystal = (a) => !!a && a.kind === CRYSTAL_KIND

/** The plan's crystal entry, or undefined. */
export const planCrystal = (plan) => (plan.added || []).find(isCrystal)

/** The crystal's stand-in: a translucent box the foot's size (pickable) holding a long
 * diamond where the floating crystal hangs. */
export function crystalStandIn (got) {
  const s = (got && got.standIn) || STAND_IN
  const [fx, fy, fz] = s.foot
  const footGeo = new THREE.BoxGeometry(fx, fy, fz)
  footGeo.translate(0, 0, fz / 2)
  const foot = new THREE.Mesh(footGeo, new THREE.MeshStandardMaterial({
    color: FOOT_COLOUR, transparent: true, opacity: 0.55, roughness: 0.8 }))
  const [cx, cy, cz] = s.crystal.size
  const gem = new THREE.Mesh(new THREE.OctahedronGeometry(0.5),
    new THREE.MeshStandardMaterial({ color: CRYSTAL_COLOUR, emissive: CRYSTAL_COLOUR,
      emissiveIntensity: 0.35, roughness: 0.3, flatShading: true }))
  gem.scale.set(cx, cy, cz)
  gem.position.set(...s.crystal.centre)
  foot.add(gem)
  return foot
}

const esc = (s) => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))

/** The Unstable Rifts a group may hold: [{id, label}], the plan's, then the level's own
 * (/api/crystal `levelRifts`, id -indexInLevel). */
const unstableRifts = (plan) => (plan.spawners || []).filter(sp => sp.unstable)
  .map(sp => ({ id: sp.id, label: `Unstable Rift ${sp.index || sp.id}` }))
  .concat(((crystalKnown() || {}).levelRifts) || [])

/** A crystal entry's settings, the defaults filled in. */
function settings (entry, got) {
  const base = (got && got.defaults) || { mapDifficulty: 0, bobHeight: 25, bobSeconds: 4 }
  entry.crystal = Object.assign({ bobHeight: base.bobHeight, bobSeconds: base.bobSeconds,
    mapDifficulty: base.mapDifficulty, groups: [] }, entry.crystal || {})
  const c = entry.crystal
  for (const key of OLD_HEALTH_KEYS) delete c[key]
  //: rows of plans made before groups: each a group of one rift (rifttear.from_rows)
  if ((!c.groups || !c.groups.length) && (c.rifts || []).length) {
    c.groups = c.rifts.filter(r => r.spawner !== null && r.spawner !== undefined).map((r, i) => ({
      name: `Rift Group ${i + 1}`, rifts: [{ spawner: r.spawner, difficulty: ANY, players: ANY_PLAYERS }],
      difficulty: ANY, players: ANY_PLAYERS,
      minions: r.unit ? [{ unit: r.unit, count: r.count || 1, addCount: 0 }] : [],
      triggers: [{ at: r.at, wave: null, both: false, open: 1, launchAfter: 0, launchAlive: 0 }] }))
  }
  delete c.rifts
  c.groups = c.groups || []
  return c
}

/** A new group's name: "Rift Group N", N the lowest no group uses. */
function newGroupName (c) {
  const used = new Set(c.groups.map(g => g.name))
  let n = 1
  while (used.has(`Rift Group ${n}`)) n++
  return `Rift Group ${n}`
}

//: The group whose next viewport click on an unstable rift adds it (null: none).
let pickInto = null

/** Whether the next click on an unstable rift goes to a Crystal group. */
export const crystalPicking = () => pickInto !== null

/** A clicked plan object while a group waits for one: an unstable rift joins the group.
 * Returns whether the click was taken. */
export function takeCrystalPick (found, plan, snapshot) {
  if (pickInto === null) return false
  const entry = planCrystal(plan)
  const g = entry && entry.crystal && (entry.crystal.groups || [])[pickInto]
  pickInto = null
  if (!g || !found || found.kind !== 'spawner' || !found.obj.unstable) return false
  if (!g.rifts.some(r => r.spawner === found.obj.id)) {
    snapshot()
    g.rifts.push({ spawner: found.obj.id, difficulty: ANY, players: ANY_PLAYERS })
  }
  return true
}

/** The Crystal section for one picked piece, or '': its Rift Groups, each a name and its
 * Unstable Rifts. */
export function crystalHtml (piece, plan) {
  if (!piece || !isCrystal(piece.added)) return ''
  const c = settings(piece.added, crystalKnown())
  const rifts = unstableRifts(plan)
  const label = (id) => (rifts.find(o => o.id === id) || { label: `Unstable Rift ${id}` }).label
  const groups = c.groups.map((g, gi) => {
    const free = rifts.filter(o => !g.rifts.some(r => r.spawner === o.id))
    return `<div class="route"><div class="routehead">` +
      `<input type="text" id="crystalgname${gi}" maxlength="40" value="${esc(g.name)}" aria-label="Rift Group name">` +
      `<button data-crystalrmgroup="${gi}">Delete</button></div>` +
      g.rifts.map((r, ri) => '<div class="routehead">' +
        `<span>${esc(label(r.spawner))}</span>` +
        `<button data-crystalrmrift="${gi}:${ri}">Remove</button></div>`).join('') +
      (g.rifts.length < GROUP_RIFTS_MAX && free.length
        ? '<div class="grid2">' +
          `<select id="crystaladdrift${gi}"><option value="" selected>Add Unstable Rift</option>` +
          free.map(o => `<option value="${o.id}">${esc(o.label)}</option>`).join('') +
          '</select>' +
          `<button data-crystalpick="${gi}"${pickInto === gi ? ' class="on"' : ''}>` +
          'Pick in Viewport</button></div>'
        : '') + '</div>'
  }).join('')
  const fitted = fitCache && fitCache.got
  const set = (key) => key === DROP ? c[key] !== undefined && c[key] !== null : !!c[key]
  const hit = '<h2>Hitbox</h2><div class="grid2">' + HIT_FIELDS.map(([key, label, auto]) =>
    `<label for="crystal${key}">${label}</label>` +
    `<input type="number" id="crystal${key}" step="1"` +
    ` min="${key === DROP ? 0 : fitted ? fitted.range[0] : 386}"` +
    ` value="${set(key) ? esc(c[key]) : ''}"` +
    ` placeholder="${fitted ? esc(auto(fitted)) : ''}" aria-label="${label}">`).join('') +
    '</div>'
  return '<div id="crystalpanel">' + hit + '<h2>Rift Groups</h2>' + groups +
    (c.groups.length < GROUPS_MAX ? '<button id="crystaladdgroup">Add Rift Group</button>' : '') +
    '</div>'
}

/** Wire the Crystal section. ctx: {snapshot, showProps, plan, load}: `load` asks
 * /api/crystal, and the panel is drawn again with its answer. */
export function wireCrystal (body, piece, ctx) {
  if (!piece || !isCrystal(piece.added) || !body.querySelector('#crystalpanel')) return
  if (!crystalKnown() && ctx.load) ctx.load().then(() => ctx.showProps()).catch(() => {})
  const c = settings(piece.added, crystalKnown())
  const field = (id) => body.querySelector('#' + id)
  //: the placeholders: the fit where the Crystal stands now, drawn again once known
  if (ctx.fit && piece.added.matrix && !(fitCache && fitCache.got &&
      fitCache.key.endsWith(piece.added.matrix.map(v => (+v).toFixed(1)).join(',')))) {
    ctx.fit(piece.added.matrix).then(() => ctx.showProps()).catch(() => {})
  }
  for (const [key] of HIT_FIELDS) {
    const input = field(`crystal${key}`)
    if (!input) continue
    input.addEventListener('change', (ev) => {
      const raw = ev.target.value.trim()
      const v = Number(raw)
      const got = fitCache && fitCache.got
      const range = key === DROP ? (got ? got.dropRange : [0, 5000]) : (got ? got.range : [386, 5000])
      ctx.snapshot()
      //: blank: fitted at build; else kept within crystalfit.RANGE / DROP_RANGE
      if (raw === '' || !Number.isFinite(v) || (key !== DROP && v <= 0)) delete c[key]
      else c[key] = Math.round(Math.max(range[0], Math.min(range[1], v)) * 10) / 10
      ctx.showProps()
    })
  }
  c.groups.forEach((g, gi) => {
    field(`crystalgname${gi}`).addEventListener('change', (ev) => {
      ctx.snapshot()
      g.name = ev.target.value.trim().slice(0, 40) || g.name
      ctx.showProps()
    })
    const add = field(`crystaladdrift${gi}`)
    if (add) {
      add.addEventListener('change', (ev) => {
        if (ev.target.value === '') return
        ctx.snapshot()
        g.rifts.push({ spawner: +ev.target.value, difficulty: ANY, players: ANY_PLAYERS })
        ctx.showProps()
      })
    }
  })
  const on = (sel, fn) => {
    for (const b of body.querySelectorAll(sel)) b.addEventListener('click', () => fn(b.dataset))
  }
  on('[data-crystalrmrift]', (d) => {
    const [gi, ri] = d.crystalrmrift.split(':').map(Number)
    ctx.snapshot()
    c.groups[gi].rifts.splice(ri, 1)
    ctx.showProps()
  })
  on('[data-crystalrmgroup]', (d) => {
    ctx.snapshot()
    c.groups.splice(+d.crystalrmgroup, 1)
    pickInto = null
    ctx.showProps()
  })
  on('[data-crystalpick]', (d) => {
    pickInto = pickInto === +d.crystalpick ? null : +d.crystalpick
    ctx.showProps()
  })
  const addGroup = field('crystaladdgroup')
  if (addGroup) {
    addGroup.addEventListener('click', () => {
      ctx.snapshot()
      c.groups.push({ name: newGroupName(c), rifts: [], difficulty: ANY, players: ANY_PLAYERS,
                      minions: [], triggers: [] })
      ctx.showProps()
    })
  }
}
