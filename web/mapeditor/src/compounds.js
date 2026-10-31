// Compound kits: one New Object card places several ordinary plan items -- kit pieces,
// meshes and, for an archway gate, a door -- sharing a `group` id, so they are picked,
// moved, turned and deleted together, and Ungroup separates them. The anchor member
// carries `compound` and `params` so a stack height or chain length can be changed.
// Offsets come from /api/decor (riftlord/compounds.py, measured on the shipped levels).
//
// The door of a gate stays a plan `doors` entry (it is part of the spawn system): it
// follows its group's moves and turns, can be selected and moved on its own, and is
// removed with the group when every member of the group is deleted.

import * as THREE from 'three'
import { kitGrids, placeMatrix } from './kitplace.js'
import { barrelUnder } from './controllers.js'

let api = null
let defs = null

/** Hand the module main.js's state and helpers, and fetch the compound definitions. */
export function install (hooks) {
  api = hooks
  api.json('api/decor').then(got => { defs = got }, () => { defs = null })
}

/** /api/decor, or null until it has arrived. */
export const definitions = () => defs

const relMatrix = (m) => new THREE.Matrix4().set(
  m.r[0][0], m.r[0][1], m.r[0][2], m.t[0],
  m.r[1][0], m.r[1][1], m.r[1][2], m.t[1],
  m.r[2][0], m.r[2][1], m.r[2][2], m.t[2],
  0, 0, 0, 1)
const turnMatrix = (r) => relMatrix({ r, t: [0, 0, 0] })
const lift = (z) => new THREE.Matrix4().makeTranslation(0, 0, z)

/** The anchor's own rotation-and-scale in the compound's frame (compounds.py anchor `r`):
 * members are placed in that frame. */
const anchorTurn = (c) => c.anchor.r ? turnMatrix(c.anchor.r) : new THREE.Matrix4()

/** The next free group id across the plan's added items and doors. */
export function nextGroup () {
  const plan = api.plan()
  let top = 0
  for (const a of plan.added || []) top = Math.max(top, a.group || 0)
  for (const d of plan.doors || []) top = Math.max(top, d.group || 0)
  return top + 1
}

/** A door's matrix in Unreal space from its `at` and yaw. */
export function doorMatrix (door) {
  return new THREE.Matrix4().makeRotationZ(THREE.MathUtils.degToRad(door.rotation || 0))
    .setPosition(door.at[0], door.at[1], door.at[2])
}

/** Write a matrix into a door as `at` and yaw (shipped doors are pure yaw). */
function setDoor (door, m) {
  const e = m.elements
  door.at = [e[12], e[13], e[14]].map(v => Math.round(v * 100) / 100)
  door.rotation = Math.round(THREE.MathUtils.radToDeg(Math.atan2(e[1], e[0])) * 1000) / 1000
}

/** The chain's segments for `links` links, longest first (compounds.SEGMENTS). */
function segmentsFor (links) {
  const out = []
  let left = Math.max(1, links)
  for (const [n, mesh, drop] of defs.chain.segments) {
    while (left >= n) { out.push({ n, mesh, drop }); left -= n }
  }
  return out
}

/** Members of a hanging chain under a post at `post` (the post itself excluded), and the
 * hook point of its last segment in Unreal space. */
function chainMembers (post, params) {
  const c = defs.chain
  const out = [{ name: c.ring.name, kind: 'mesh', matrix: post.clone().multiply(relMatrix(c.ring)) }]
  let pivot = post.clone().multiply(relMatrix({ t: c.chainInPost[0], r: c.chainInPost[1] }))
  const segs = segmentsFor(params.links || 1)
  segs.forEach((s, i) => {
    if (i > 0) {
      const prev = segs[i - 1]
      pivot = pivot.clone().multiply(lift(-(prev.n === 6 ? c.stackBelowX6 : prev.drop)))
    }
    out.push({ name: s.mesh, kind: 'mesh', matrix: pivot.clone() })
  })
  const last = segs[segs.length - 1]
  const hook = pivot.clone().multiply(lift(-last.drop))
  const hung = c.hung[params.item]
  const e = post.elements
  const barrel = hung && hung.via === 'barrelUnder' &&
    barrelUnder([hook.elements[12], hook.elements[13], hook.elements[14]],
      THREE.MathUtils.radToDeg(Math.atan2(e[1], e[0])))
  if (barrel) {
    out.push({ name: barrel.name, kind: barrel.kind, matrix: new THREE.Matrix4().fromArray(barrel.matrix) })
  } else if (hung) {
    const at = hook.clone().multiply(new THREE.Matrix4().makeTranslation(hung.xy[0], hung.xy[1], 0))
      .multiply(turnMatrix(hung.r))
    out.push({ name: hung.name, kind: hung.kind, matrix: at })
  }
  return { members: out, hook: new THREE.Vector3().setFromMatrixPosition(hook) }
}

/** A pattern value: a number, or [param, factor, offset] (compounds.py value). */
const patternValue = (e, params) => typeof e === 'number' ? e
  : (Number(params[e[0]]) || 0) * e[1] + e[2]

/** [{name, kind, matrix (compound frame)}] of a pattern compound's parts at `params`: parts
 * stretched along an axis, repeated across a span or at a step, or left out (compounds.py
 * pattern_parts). */
export function patternParts (c, params) {
  params = Object.assign({}, c.params || {}, params || {})
  const out = []
  for (const part of c.parts) {
    if (part.when !== undefined && patternValue(part.when, params) <= 0) continue
    const r = part.r.map(row => row.slice())
    if (part.stretch) {
      const k = part.stretch.axis
      const s = patternValue(part.stretch.by, params)
      for (let i = 0; i < 3; i++) r[i][k] *= s
    }
    const t = part.t.map(e => patternValue(e, params))
    let spots = [t]
    if (part.repeat) {
      const rep = part.repeat
      const n = Math.max(0, Math.round(patternValue(rep.count, params)))
      spots = []
      for (let i = 0; i < n; i++) {
        const at = t.slice()
        if (rep.across !== undefined) {
          const step = n > 1 ? patternValue(rep.across, params) / (n - 1) : 0
          at[rep.axis] = patternValue(rep.at, params) + (i - (n - 1) / 2) * step
        } else if (rep.step !== undefined) {
          at[rep.axis] = patternValue(rep.at, params) + (i - (n - 1) / 2) * patternValue(rep.step, params)
        } else {
          const [a, b] = rep.span.map(e => patternValue(e, params))
          at[rep.axis] = n === 1 ? (a + b) / 2 : a + i * (b - a) / (n - 1)
        }
        spots.push(at)
      }
    }
    for (const at of spots) {
      out.push({ name: part.name, kind: part.kind, matrix: relMatrix({ r, t: at }) })
    }
  }
  return out
}

/** {members, doors} a compound places beside its anchor at `anchor`. */
export function build (key, anchor, params) {
  const c = defs.compounds[key]
  const members = []
  const doors = []
  if (c.type === 'fixed') {
    for (const m of c.members) {
      const matrix = anchor.clone().multiply(relMatrix(m))
      if (m.kind === 'door') doors.push({ cls: m.name, matrix })
      //: an effect is drawn by its donor mesh and placed by its library name
      else if (m.kind === 'fx') members.push({ name: m.mesh || m.name, kind: 'fx', fx: m.name, matrix })
      else members.push({ name: m.name, kind: m.kind, matrix })
    }
  } else if (c.type === 'stack') {
    const [first, step, top] = c.pitch
    let at = anchor.clone()
    const mids = Math.max(0, Math.min(c.midsMax, params.mids ?? 0))
    for (let k = 0; k < mids; k++) {
      at = at.clone().multiply(lift(k === 0 ? first : step))
      members.push({ name: c.mid, kind: 'mesh', matrix: at })
    }
    at = at.clone().multiply(lift(mids ? top : first))
    members.push({ name: c.top, kind: 'mesh', matrix: at })
  } else if (c.type === 'chain') {
    members.push(...chainMembers(anchor, params).members)
  } else if (c.type === 'pattern') {
    for (const m of patternParts(c, params)) {
      members.push({ name: m.name, kind: m.kind, matrix: anchor.clone().multiply(m.matrix) })
    }
  }
  return { members, doors }
}

/** A post on the ceiling at `at` (Unreal), turned `yaw` radians: the chain anchor. */
export function postAt (at, yaw = 0) {
  return new THREE.Matrix4().makeRotationZ(yaw).setPosition(at[0], at[1], at[2])
    .multiply(turnMatrix(defs.chain.post))
}

/** Where a compound dropped at `at` (a floor point, Unreal) puts its anchor. */
async function anchorFor (key, at, params) {
  const c = defs.compounds[key]
  if (c.type === 'chain') {
    //: raised so what hangs is at its measured height over the floor
    const probe = chainMembers(postAt([0, 0, 0]), params)
    const hung = defs.chain.hung[params.item]
    const above = (hung && hung.above) || defs.chain.aboveFloor
    return postAt([at[0], at[1], at[2] + above - probe.hook.z])
  }
  if (c.defended) return facingViewer(c, at)
  if (c.below !== undefined) return new THREE.Matrix4().makeTranslation(at[0], at[1], at[2] - c.below)
  if (c.anchor.kind === 'kit') {
    const grids = await kitGrids(c.anchor.name, api.json)
    const m = placeMatrix(grids, at, api.eye())
    if (m) return m
  }
  return new THREE.Matrix4().makeTranslation(at[0], at[1], at[2])
}

/** A gate dropped at `at`: turned by quarter turns so its defended side (local
 * `c.defended`) faces the viewer, its door (`c.centre`) on the click. */
function facingViewer (c, at) {
  const eye = api.eye()
  const want = new THREE.Vector2(eye.x - at[0], eye.y - at[1])
  let yaw = 0
  if (want.lengthSq() > 0) {
    want.normalize()
    let best = -Infinity
    for (let k = 0; k < 4; k++) {
      const a = k * Math.PI / 2
      const dx = Math.cos(a) * c.defended[0] - Math.sin(a) * c.defended[1]
      const dy = Math.sin(a) * c.defended[0] + Math.cos(a) * c.defended[1]
      const score = dx * want.x + dy * want.y
      if (score > best + 1e-6) { best = score; yaw = a }
    }
  }
  const m = new THREE.Matrix4().makeRotationZ(yaw)
  const centre = new THREE.Vector3(c.centre[0], c.centre[1], 0).applyMatrix4(m)
  return m.setPosition(at[0] - centre.x, at[1] - centre.y, at[2])
}

/** Add one compound's items to the plan, draw them, and pick the group. `anchor` is an
 * Unreal matrix to place it by (else it is dropped at `at`). */
export async function place (key, at, anchor = null, params = null) {
  if (!defs || !defs.compounds[key]) { api.flash('Compound kits are still loading.'); return null }
  const c = defs.compounds[key]
  params = Object.assign({}, c.params || {}, params || {})
  api.snapshot()
  const matrix = anchor || await anchorFor(key, at, params)
  const group = nextGroup()
  const head = { name: c.anchor.name, kind: c.anchor.kind,
                 matrix: matrix.clone().multiply(anchorTurn(c)).toArray(), group,
                 compound: key, anchor: true }
  if (c.params) head.params = params
  const made = [head]
  const got = build(key, matrix, params)
  for (const m of got.members) {
    made.push({ name: m.name, kind: m.kind, ...(m.fx ? { fx: m.fx } : {}), matrix: m.matrix.toArray(), group })
  }
  //: a compound with an effect in it turns the Effects layer on
  if (made.some(e => e.kind === 'fx')) api.showEffects()
  await addEntries(made)
  for (const d of got.doors) addDoor(d.cls, d.matrix, group)
  if (got.doors.length) api.drawPlan()
  pickGroup(group)
  return group
}

/** Push and draw added entries, all at once (one undo step is the caller's). */
export async function addEntries (made) {
  const plan = api.plan()
  for (const e of made) plan.added.push(e)
  await Promise.all(made.map(e => api.drawAdded(e)))
}

function addDoor (cls, matrix, group) {
  const plan = api.plan()
  const door = api.blank('door', [0, 0, 0])
  door.cls = cls
  door.group = group
  setDoor(door, matrix)
  plan.doors.push(door)
  return door
}

/** Pick every added member of `group`. */
export function pickGroup (group) {
  const recs = (api.plan().added || []).filter(a => a.group === group).map(api.addedRecord)
  api.setPicked(recs)
}

/** What a click on one added entry picks: its whole group, or just it. */
export function kin (rec) {
  const g = rec && rec.added && rec.added.group
  if (!g) return [rec]
  return (api.plan().added || []).filter(a => a.group === g).map(a =>
    a === rec.added ? rec : api.addedRecord(a))
}

/** Whether an added entry can be box-selected now: added foliage only while the Foliage
 * layer shows. */
export const shown = (entry) => entry.kind !== 'foliage' || api.groups().foliage.visible

/** The group fields a copy of `entry` keeps: all of them when the whole group is among
 * the copied `picked`, else none. */
export function copyFields (entry, picked) {
  if (!entry.group || !wholeGroups(picked.filter(p => p.added).map(p => p.added)).has(entry.group)) return {}
  const out = { group: entry.group }
  if (entry.compound) out.compound = entry.compound
  if (entry.anchor) out.anchor = true
  if (entry.params) out.params = JSON.parse(JSON.stringify(entry.params))
  return out
}

/** Clipboard items for the doors of the whole groups among `picked`. */
export function copiedDoors (picked) {
  const whole = wholeGroups(picked.filter(p => p.added).map(p => p.added))
  return (api.plan().doors || []).filter(d => whole.has(d.group)).map(d => ({
    name: d.cls, kind: 'door', matrix: doorMatrix(d).toArray(),
    door: Object.fromEntries(Object.entries(d).filter(([k]) => !['id', 'at', 'rotation'].includes(k))),
  }))
}

/** A paste's items: fresh group ids for each copied group, and its door items taken out
 * of `made` into the plan's doors. Call inside the paste's undo step. */
export function pasted (made) {
  const fresh = new Map()
  let next = nextGroup()
  for (const e of made) {
    if (e.kind === 'door' && e.door && e.door.group) e.group = e.door.group
    if (!e.group) continue
    if (!fresh.has(e.group)) fresh.set(e.group, next++)
    e.group = fresh.get(e.group)
  }
  let doors = 0
  for (let i = made.length - 1; i >= 0; i--) {
    const e = made[i]
    if (e.kind !== 'door') continue
    made.splice(i, 1)
    const d = addDoor(e.door.cls, new THREE.Matrix4().fromArray(e.matrix), e.group)
    Object.assign(d, e.door, { group: e.group })
    doors += 1
  }
  if (doors) api.drawPlan()
  return doors
}

/** The groups every member of which is among `entries`. */
function wholeGroups (entries) {
  const all = new Map()
  for (const a of api.plan().added || []) {
    if (a.group) all.set(a.group, (all.get(a.group) || 0) + 1)
  }
  const mine = new Map()
  for (const a of entries) if (a.group) mine.set(a.group, (mine.get(a.group) || 0) + 1)
  return new Set([...mine].filter(([g, n]) => n === all.get(g)).map(([g]) => g))
}

/** After the picked pieces moved by `delta` (an Unreal matrix): their groups' doors follow. */
export function follow (delta, picked) {
  const groups = new Set(picked.filter(p => p.added && p.added.group).map(p => p.added.group))
  if (!groups.size) return
  const moved = picked.filter(p => p.marker && p.kind === 'door').map(p => p.planId)
  let any = false
  for (const door of api.plan().doors || []) {
    if (!groups.has(door.group) || moved.includes(door.id)) continue
    setDoor(door, delta.clone().multiply(doorMatrix(door)))
    any = true
  }
  if (any) api.drawPlan()
}

/** Before `entries` are deleted: the doors of the groups they make up go with them.
 * Returns how many doors went. */
export function removeWith (entries) {
  const gone = wholeGroups(entries)
  if (!gone.size) return 0
  const plan = api.plan()
  const ids = plan.doors.filter(d => gone.has(d.group)).map(d => d.id)
  if (!ids.length) return 0
  plan.doors = plan.doors.filter(d => !ids.includes(d.id))
  for (const sp of plan.spawners || []) if (ids.includes(sp.door)) sp.door = null
  for (const w of plan.wiring || []) if (ids.includes(w.door)) w.door = null
  api.drawPlan()
  return ids.length
}

/** Take `group` apart: its members and its door become ordinary items. */
export function ungroup (group) {
  api.snapshot()
  const plan = api.plan()
  for (const a of plan.added || []) {
    if (a.group !== group) continue
    delete a.group
    delete a.compound
    delete a.anchor
    delete a.params
  }
  for (const d of plan.doors || []) if (d.group === group) delete d.group
}

/** Rebuild `group` from its anchor with new `params`. */
export async function rebuild (group, params) {
  const plan = api.plan()
  const head = (plan.added || []).find(a => a.group === group && a.anchor)
  if (!head || !defs.compounds[head.compound]) return
  api.snapshot()
  const c = defs.compounds[head.compound]
  const frame = new THREE.Matrix4().fromArray(head.matrix)
    .multiply(anchorTurn(c).invert())
  head.params = Object.assign({}, head.params || {}, params)
  plan.added = plan.added.filter(a => a.group !== group || a === head)
  const got = build(head.compound, frame, head.params)
  for (const m of got.members) {
    plan.added.push({ name: m.name, kind: m.kind, ...(m.fx ? { fx: m.fx } : {}), matrix: m.matrix.toArray(), group })
  }
  await api.redrawAdded()
  pickGroup(group)
}

/** The compound key a group was placed from, or null. */
function groupCompound (group) {
  const head = (api.plan().added || []).find(a => a.group === group && a.anchor)
  return head ? head.compound : null
}

/** The group the whole selection is, or null. */
function pickedGroup (picked) {
  if (!picked.length || !picked.every(p => p.added && p.added.group)) return null
  const g = picked[0].added.group
  if (!picked.every(p => p.added.group === g)) return null
  const all = (api.plan().added || []).filter(a => a.group === g).length
  return all === picked.length ? g : null
}

//: Labels of the tool groups decortools.js makes.
const TOOL_LABELS = { wallrun: 'Wall Run', rocks: 'Rock Scatter', foliage: 'Foliage',
  beam: 'Beam', railing: 'Railing Run' }

const esc = (text) => String(text ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/"/g, '&quot;')

/** The Group section of the Selected panel, or '' when the selection is not one group. */
export function section (picked) {
  const g = pickedGroup(picked)
  if (g === null || !defs) return ''
  const plan = api.plan()
  const head = plan.added.find(a => a.group === g && a.anchor) ||
    plan.added.find(a => a.group === g && a.compound)
  const key = head && head.compound
  const c = key && defs.compounds[key]
  const label = c ? c.label : TOOL_LABELS[key] || 'Group'
  const doors = (plan.doors || []).filter(d => d.group === g).length
  const rows = [`<h2>Group</h2><div class="row"><span>${esc(label)}</span>` +
    `<span>${picked.length + doors}</span></div>`]
  if (c && c.type === 'stack') {
    rows.push(`<label class="pick"><span>Mid Tiles</span><input id="cpmids" type="number" min="0" ` +
      `max="${c.midsMax}" step="1" value="${(head.params || {}).mids ?? 0}"></label>`)
  }
  if (c && c.type === 'chain') {
    const p = head.params || {}
    const items = Object.keys(defs.chain.hung)
    rows.push('<div class="pair">' +
      `<label class="pick"><span>Links</span><input id="cplinks" type="number" min="1" ` +
      `max="${defs.chain.linksMax}" step="1" value="${p.links || 1}"></label>` +
      '<label class="pick"><span>Hung Item</span><select id="cpitem">' +
      items.map(k => `<option value="${k}"${k === p.item ? ' selected' : ''}>${HUNG_LABELS[k] || k}</option>`).join('') +
      '</select></label></div>')
  }
  if (c && c.type === 'pattern') {
    const p = Object.assign({}, c.params || {}, head.params || {})
    const fields = Object.entries(c.paramSpec || {}).map(([name, spec]) =>
      `<label class="pick"><span>${esc(spec.label)}</span><input type="number" ` +
          `data-cparam="${esc(name)}" min="${spec.min}" max="${spec.max}" step="${spec.step}" ` +
          `value="${p[name] ?? spec.min}"></label>`)
    for (let i = 0; i < fields.length; i += 2) rows.push('<div class="pair">' + fields.slice(i, i + 2).join('') + '</div>')
  }
  rows.push('<button id="cpungroup">Ungroup</button>')
  return rows.join('')
}

//: Hung item labels.
const HUNG_LABELS = { none: 'None', chandelier: 'Chandelier', chandelier_small: 'Small Chandelier',
  boom_barrel: 'Boom Barrel', chandelier_trap: 'Chandelier Trap' }

/** Wire the Group section's fields. */
export function wire (body, picked) {
  const g = pickedGroup(picked)
  if (g === null) return
  const ungroupBtn = body.querySelector('#cpungroup')
  if (ungroupBtn) {
    ungroupBtn.addEventListener('click', () => {
      ungroup(g)
      api.refreshPicked()
      api.showProps()
    })
  }
  const mids = body.querySelector('#cpmids')
  if (mids) mids.addEventListener('change', () => rebuild(g, { mids: Math.max(0, +mids.value || 0) }))
  const links = body.querySelector('#cplinks')
  if (links) links.addEventListener('change', () => rebuild(g, { links: Math.max(1, +links.value || 1) }))
  for (const input of body.querySelectorAll('[data-cparam]')) {
    input.addEventListener('change', () => {
      const spec = defs.compounds[groupCompound(g)].paramSpec[input.dataset.cparam]
      const v = Number(input.value)
      if (!Number.isFinite(v)) return
      rebuild(g, { [input.dataset.cparam]: Math.max(spec.min, Math.min(spec.max, v)) })
    })
  }
  const item = body.querySelector('#cpitem')
  if (item) item.addEventListener('change', () => rebuild(g, { item: item.value }))
}

// -- probes for the UI smoke suite ------------------------------------------------------

window.__rl_compoundPlace = async (key, at) => place(key, at)
window.__rl_pickGroup = (group) => { pickGroup(group); return api.picked().length }
window.__rl_lastGroup = () => nextGroup() - 1
window.__rl_compoundParams = (group, params) => rebuild(group, params)
window.__rl_groupIndex = (group) => (api.plan().added || []).findIndex(a => a.group === group &&
  (a.anchor || a.compound))
//: The cosine between a gate's defended side (+x) and the way to the camera.
window.__rl_gateFacing = (group) => {
  const head = (api.plan().added || []).find(a => a.group === group && a.anchor)
  if (!head) return null
  const m = head.matrix
  const eye = api.eye()
  const to = new THREE.Vector2(eye.x - m[12], eye.y - m[13]).normalize()
  const x = new THREE.Vector2(m[0], m[1]).normalize()
  return x.dot(to)
}
window.__rl_addedMatrix = (i) => ((api.plan().added || [])[i] || {}).matrix || null
window.__rl_groupState = (group) => {
  const plan = api.plan()
  return JSON.parse(JSON.stringify({
    added: (plan.added || []).filter(a => a.group === group)
      .map(a => ({ name: a.name, kind: a.kind, matrix: a.matrix, anchor: !!a.anchor, compound: a.compound || null })),
    doors: (plan.doors || []).filter(d => d.group === group)
      .map(d => ({ id: d.id, cls: d.cls, at: d.at, rotation: d.rotation })),
    picked: api.picked().filter(p => p.added && p.added.group === group).length,
  }))
}
window.__rl_doorInGroup = (group) => {
  const door = (api.plan().doors || []).find(d => d.group === group)
  const head = (api.plan().added || []).find(a => a.group === group && a.anchor)
  if (!door || !head) return null
  const rel = new THREE.Matrix4().fromArray(head.matrix).invert().multiply(doorMatrix(door))
  const e = rel.elements
  return { t: [e[12], e[13], e[14]], x: [e[0], e[1], e[2]] }
}
