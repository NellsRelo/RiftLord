// Decoration tools: Wall Run, Hang, Rock Scatter, Foliage, Beam and Railing Run. The
// designer picks a tool in the Decorate panel, edits its measured defaults, presses
// Place and clicks in the view; every piece a tool places is an ordinary plan item, one
// group per placement (compounds.js), so it can be moved, edited, ungrouped or deleted.
// Defaults come from /api/decor (riftlord/compounds.py, research part 3).
//
// Added foliage instances (kind 'foliage') draw with the other added pieces and show or
// hide with the Foliage layer.

import * as THREE from 'three'
import * as compounds from './compounds.js'

let api = null
let open = false
let armed = false
let tool = 'wallrun'
//: the first click of a two-click tool (Unreal point), or null
let first = null
//: field values per tool, filled from the defaults on first use
const values = {}

//: Tools in the Tool dropdown.
const TOOLS = [['wallrun', 'Wall Run'], ['hang', 'Hang'], ['rocks', 'Rock Scatter'],
  ['foliage', 'Foliage'], ['beam', 'Beam'], ['railing', 'Railing Run']]
//: Tools placed by two clicks.
const TWO_CLICKS = new Set(['wallrun', 'beam', 'railing'])
//: How far into the room the floor under a wall is looked for, in uu.
const FLOOR_PROBE_OUT = 50
//: Layer that neither the camera nor the picking ray sees.
const HIDDEN_LAYER = 31

const esc = (text) => String(text ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/"/g, '&quot;')

export function install (hooks) {
  api = hooks
  addEventListener('keydown', (ev) => {
    if (armed && ev.key === 'Escape') {
      disarm()
      ev.preventDefault()
      ev.stopImmediatePropagation()
    }
  }, true)
  const btn = document.getElementById('decorate')
  if (btn) btn.addEventListener('click', openPanel)
  requestAnimationFrame(syncLoop)
}

/** Keep the Decorate button's state and added foliage's visibility in step. */
let lastFoliage = null
let lastCount = -1
function syncLoop () {
  requestAnimationFrame(syncLoop)
  const btn = document.getElementById('decorate')
  if (btn) btn.disabled = !api.level()
  const shown = api.groups().foliage.visible
  const kids = api.groups().newGeo.children
  if (shown === lastFoliage && kids.length === lastCount) return
  lastFoliage = shown
  lastCount = kids.length
  for (const m of kids) {
    const e = m.userData.addedEntry
    if (!e || e.kind !== 'foliage') continue
    m.visible = shown
    if (shown) m.layers.set(0)
    else m.layers.set(HIDDEN_LAYER)
  }
}

function defaultsFor (name) {
  const d = compounds.definitions()
  if (!d) return {}
  if (name === 'wallrun') {
    const key = d.wallRun.default
    return { item: key, ...pick3(d.wallRun.items[key]) }
  }
  if (name === 'hang') return { links: d.compounds.chain.params.links, item: d.compounds.chain.params.item }
  if (name === 'rocks') {
    return { small: false, count: d.rocks.count, radius: d.rocks.radius,
             min: d.rocks.scale[0], max: d.rocks.scale[1] }
  }
  if (name === 'foliage') return { mesh: d.foliage.default, ...foliageDefaults(d.foliage.default) }
  if (name === 'beam') return { mesh: d.beam.mesh }
  if (name === 'railing') return { mesh: d.railing.meshes[0], pitch: d.railing.pitch }
  return {}
}

const pick3 = (row) => ({ spacing: row.spacing, height: row.height, offset: row.offset })

function foliageDefaults (mesh) {
  const d = compounds.definitions()
  const row = d.foliage.meshes[mesh]
  if (!row) return { spacing: 100, minSpacing: 50, count: 5, min: 1, max: 1, slope: false }
  return { spacing: row.spacing[1], minSpacing: row.spacing[0], count: Math.max(1, Math.round(row.clump[1])),
           min: row.scale[0], max: row.scale[1], slope: row.freeTilt > d.foliage.alignShare }
}

function fields () {
  if (!values[tool]) values[tool] = defaultsFor(tool)
  return values[tool]
}

/** The Decorate button: the tool panel in the right sidebar. */
export function openPanel () {
  if (!api.level()) return
  api.clearSelection()
  open = true
  api.showProps()
}

/** showProps' hook: false when the panel is not shown; a selection closes it. */
export function panel (selectionTaken) {
  if (selectionTaken) { open = false; if (armed) disarm() }
  if (!open) return false
  render()
  return true
}

const num = (id, label, value, step = 'any', min = '') =>
  `<label class="pick"><span>${label}</span><input id="${id}" type="number" step="${step}"` +
  `${min !== '' ? ` min="${min}"` : ''} value="${value}"></label>`

function render () {
  const d = compounds.definitions()
  const body = document.getElementById('propsbody')
  document.getElementById('app').classList.add('props')
  document.getElementById('propshead').textContent = 'Decorate'
  if (!d) { body.innerHTML = ''; return }
  const f = fields()
  const rows = ['<label class="pick"><span>Tool</span><select id="dttool">' +
    TOOLS.map(([k, l]) => `<option value="${k}"${k === tool ? ' selected' : ''}>${l}</option>`).join('') +
    '</select></label>']
  if (tool === 'wallrun') {
    rows.push('<label class="pick"><span>Dressing</span><select id="dtitem">' +
      Object.entries(d.wallRun.items).map(([k, r]) =>
        `<option value="${esc(k)}"${k === f.item ? ' selected' : ''}>${esc(r.label)}</option>`).join('') +
      '</select></label>')
    rows.push('<div class="pair">' + num('dtspacing', 'Spacing', f.spacing, 'any', 1) +
      num('dtheight', 'Height', f.height) + '</div>' + '<div class="pair">' +
      num('dtoffset', 'Offset', f.offset) + '</div>')
  } else if (tool === 'hang') {
    rows.push('<div class="pair">' + num('dtlinks', 'Links', f.links, 1, 1) +
      '<label class="pick"><span>Hung Item</span><select id="dthung">' +
      Object.keys(d.chain.hung).map(k => `<option value="${k}"${k === f.item ? ' selected' : ''}>` +
        `${{ none: 'None', chandelier: 'Chandelier', chandelier_small: 'Small Chandelier', boom_barrel: 'Boom Barrel', chandelier_trap: 'Chandelier Trap' }[k] || k}</option>`).join('') +
      '</select></label></div>')
  } else if (tool === 'rocks') {
    rows.push(`<label class="row"><span>Small Stones</span><input id="dtsmall" type="checkbox"${f.small ? ' checked' : ''}></label>`)
    rows.push('<div class="pair">' + num('dtcount', 'Count', f.count, 1, 1) + num('dtradius', 'Radius', f.radius, 'any', 0) + '</div>')
    rows.push('<div class="pair">' + num('dtmin', 'Scale Min', f.min, 'any', 0.01) + num('dtmax', 'Scale Max', f.max, 'any', 0.01) + '</div>')
  } else if (tool === 'foliage') {
    rows.push('<label class="pick"><span>Mesh</span><select id="dtmesh">' +
      Object.keys(d.foliage.meshes).map(k => `<option value="${esc(k)}"${k === f.mesh ? ' selected' : ''}>${esc(k)}</option>`).join('') +
      '</select></label>')
    rows.push('<div class="pair">' + num('dtspacing', 'Spacing', f.spacing, 'any', 1) + num('dtminspacing', 'Min Spacing', f.minSpacing, 'any', 0) + '</div>')
    rows.push('<div class="pair">' + num('dtcount', 'Patch', f.count, 1, 1) + '</div>')
    rows.push('<div class="pair">' + num('dtmin', 'Scale Min', f.min, 'any', 0.01) + num('dtmax', 'Scale Max', f.max, 'any', 0.01) + '</div>')
    rows.push(`<label class="row"><span>Follow Slope</span><input id="dtslope" type="checkbox"${f.slope ? ' checked' : ''}></label>`)
  } else if (tool === 'beam') {
    rows.push(`<div class="row"><span>Mesh</span><span>${esc(f.mesh)}</span></div>`)
  } else if (tool === 'railing') {
    rows.push('<label class="pick"><span>Mesh</span><select id="dtmesh">' +
      d.railing.meshes.map(k => `<option value="${k}"${k === f.mesh ? ' selected' : ''}>${k}</option>`).join('') +
      '</select></label>' + '<div class="pair">' + num('dtpitch', 'Pitch', f.pitch, 'any', 1) + '</div>')
  }
  rows.push(`<button id="dtplace"${armed ? ' class="on"' : ''}>${armed ? 'Stop Placing' : 'Place'}</button>`)
  rows.push('<button id="dtreset">Reset</button>')
  body.innerHTML = rows.join('')
  wire(body)
}

function wire (body) {
  const f = fields()
  const on = (id, fn) => { const n = body.querySelector('#' + id); if (n) n.addEventListener('change', () => fn(n)) }
  on('dttool', (n) => { tool = n.value; first = null; render() })
  on('dtitem', (n) => { Object.assign(f, { item: n.value }, pick3(compounds.definitions().wallRun.items[n.value])); render() })
  on('dtmesh', (n) => {
    f.mesh = n.value
    if (tool === 'foliage') Object.assign(f, foliageDefaults(n.value))
    render()
  })
  for (const [id, key] of [['dtspacing', 'spacing'], ['dtheight', 'height'], ['dtoffset', 'offset'],
    ['dtlinks', 'links'], ['dtcount', 'count'], ['dtradius', 'radius'], ['dtmin', 'min'],
    ['dtmax', 'max'], ['dtminspacing', 'minSpacing'], ['dtpitch', 'pitch']]) {
    on(id, (n) => { const v = parseFloat(n.value); if (Number.isFinite(v)) f[key] = v })
  }
  on('dthung', (n) => { f.item = n.value })
  on('dtsmall', (n) => {
    const d = compounds.definitions().rocks
    f.small = n.checked
    Object.assign(f, f.small ? { min: d.smallScale[0], max: d.smallScale[1] } : { min: d.scale[0], max: d.scale[1] })
    render()
  })
  on('dtslope', (n) => { f.slope = n.checked })
  body.querySelector('#dtplace').addEventListener('click', () => { armed ? disarm() : arm() })
  body.querySelector('#dtreset').addEventListener('click', () => { values[tool] = defaultsFor(tool); render() })
}

function arm () { armed = true; first = null; hud(); render() }
function disarm () { armed = false; first = null; hud(); if (open) render() }

function hud () {
  const r = document.getElementById('readout')
  if (!r) return
  const label = (TOOLS.find(([k]) => k === tool) || [])[1]
  r.innerHTML = armed
    ? `<b>${label}</b> &middot; ${TWO_CLICKS.has(tool) ? (first ? 'click the end' : 'click the start') : 'click to place'} &middot; Esc stops`
    : ''
}

export const isArmed = () => armed

// -- geometry ---------------------------------------------------------------------------

const ray = new THREE.Raycaster()

/** The nearest level or added surface under the pointer: {at (Unreal Vector3), hit}. */
function pointerHit (ev) {
  const hits = api.pick(ev, api.targets())
  if (!hits.length) return null
  return { at: api.world().worldToLocal(hits[0].point.clone()), hit: hits[0] }
}

/** {at, normal} of the surface straight below Unreal point `from`, or null. */
function surfaceBelow (from) {
  const world = api.world()
  world.updateMatrixWorld(true)
  const start = world.localToWorld(new THREE.Vector3(from.x, from.y, from.z))
  const down = new THREE.Vector3(0, 0, -1).transformDirection(world.matrixWorld)
  ray.set(start, down)
  ray.far = Infinity
  const hit = ray.intersectObjects(api.targets().filter(m =>
    !(m.userData.addedEntry && m.userData.addedEntry.kind === 'foliage')), false)[0]
  if (!hit) return null
  return { at: world.worldToLocal(hit.point.clone()), normal: hitNormal(hit) }
}

/** A hit's surface normal in Unreal space. */
function hitNormal (hit) {
  if (!hit.face) return new THREE.Vector3(0, 0, 1)
  const m = hit.object.matrixWorld.clone()
  if (hit.instanceId !== undefined && hit.object.isInstancedMesh) {
    const inst = new THREE.Matrix4()
    hit.object.getMatrixAt(hit.instanceId, inst)
    m.multiply(inst)
  }
  const n = hit.face.normal.clone().applyMatrix3(new THREE.Matrix3().getNormalMatrix(m)).normalize()
  const back = new THREE.Matrix3().getNormalMatrix(api.world().matrixWorld.clone().invert())
  return n.applyMatrix3(back).normalize()
}

/** A yaw (radians) whose local +y points along horizontal `n`. */
const yawFacing = (n) => Math.atan2(-n.x, n.y)

const yawMatrix = (yaw, at, scale = 1) => new THREE.Matrix4().makeRotationZ(yaw)
  .scale(new THREE.Vector3(scale, scale, scale)).setPosition(at.x, at.y, at.z)

const rand = (lo, hi) => lo + Math.random() * (hi - lo)

/** Pick from {name: weight}. */
function weighted (mix) {
  const total = Object.values(mix).reduce((a, b) => a + b, 0)
  let r = Math.random() * total
  for (const [k, w] of Object.entries(mix)) { r -= w; if (r <= 0) return k }
  return Object.keys(mix)[0]
}

/** A matrix standing a piece at `at` with its z along `up`, turned `yaw` about it. */
function upright (at, up, yaw, scale) {
  const z = up.clone().normalize()
  const ref = Math.abs(z.z) < 0.99 ? new THREE.Vector3(0, 0, 1) : new THREE.Vector3(1, 0, 0)
  const x0 = new THREE.Vector3().crossVectors(ref, z).normalize()
  const y0 = new THREE.Vector3().crossVectors(z, x0)
  const x = x0.clone().multiplyScalar(Math.cos(yaw)).addScaledVector(y0, Math.sin(yaw))
  const y = new THREE.Vector3().crossVectors(z, x)
  return new THREE.Matrix4().makeBasis(x, y, z).scale(new THREE.Vector3(scale, scale, scale))
    .setPosition(at.x, at.y, at.z)
}

// -- placing ----------------------------------------------------------------------------

/** main.js's click hook: true when the click went to an armed tool. */
export function click (ev) {
  if (!armed) return false
  const got = pointerHit(ev)
  if (!got) return true
  run(got.at, got.hit).catch(err => api.report('decorate', err))
  return true
}

async function run (at) {
  const f = fields()
  if (TWO_CLICKS.has(tool)) {
    if (!first) { first = at; hud(); return }
    const a = first
    first = null
    const made = tool === 'wallrun' ? wallRun(a, at, f)
      : tool === 'beam' ? beam(a, at, f) : railing(a, at, f)
    await commit(made, tool, true)
    disarm()
    return
  }
  if (tool === 'hang') {
    await compounds.place('chain', null, compounds.postAt([at.x, at.y, at.z]),
      { links: Math.max(1, Math.round(f.links)), item: f.item })
    disarm()
    return
  }
  const made = tool === 'rocks' ? rocks(at, f) : foliage(at, f)
  await commit(made, tool, false)
}

/** Add `made` ({name, kind, matrix}) as one group labelled `key`. */
async function commit (made, key, pickIt) {
  if (!made.length) { api.flash('Nothing to place there.'); return }
  api.snapshot()
  const group = compounds.nextGroup()
  made.forEach((m, i) => {
    m.group = group
    if (i === 0) m.compound = key
  })
  await compounds.addEntries(made)
  if (pickIt) compounds.pickGroup(group)
  else hud()
}

/** Points along a run from `a` to `b` every `spacing`, centred on the run. */
function along (a, b, spacing) {
  const d = new THREE.Vector3(b.x - a.x, b.y - a.y, 0)
  const len = d.length()
  if (len < 1e-3 || !(spacing > 0)) return { d, points: [] }
  d.normalize()
  const n = Math.floor(len / spacing) + 1
  const start = (len - (n - 1) * spacing) / 2
  const points = []
  for (let k = 0; k < n; k++) points.push(new THREE.Vector3(a.x, a.y, a.z).addScaledVector(d, start + k * spacing))
  return { d, points }
}

function wallRun (a, b, f) {
  const d = compounds.definitions().wallRun.items[f.item]
  const { d: dir, points } = along(a, b, f.spacing)
  if (!points.length) return []
  //: out of the wall: the horizontal side of the run the camera is on
  const eye = api.eye()
  let out = new THREE.Vector3(-dir.y, dir.x, 0)
  if (out.dot(new THREE.Vector3(eye.x - a.x, eye.y - a.y, 0)) < 0) out.negate()
  const yaw = yawFacing(out)
  return points.map(p => {
    const floor = surfaceBelow(p.clone().addScaledVector(out, FLOOR_PROBE_OUT))
    const z = (floor ? floor.at.z : p.z) + f.height
    const at = new THREE.Vector3(p.x, p.y, z).addScaledVector(out, f.offset)
    return { name: f.item, kind: d.kind, matrix: yawMatrix(yaw, at).toArray() }
  })
}

function beam (a, b, f) {
  const d = compounds.definitions().beam
  const span = new THREE.Vector3().subVectors(b, a)
  const len = span.length()
  if (len < 1e-3) return []
  const x = span.clone().normalize()
  const ref = Math.abs(x.z) < 0.99 ? new THREE.Vector3(0, 0, 1) : new THREE.Vector3(1, 0, 0)
  const y = new THREE.Vector3().crossVectors(ref, x).normalize()
  const z = new THREE.Vector3().crossVectors(x, y)
  const m = new THREE.Matrix4().makeBasis(x.multiplyScalar(len / d.length), y, z).setPosition(a.x, a.y, a.z)
  return [{ name: f.mesh, kind: 'mesh', matrix: m.toArray() }]
}

function railing (a, b, f) {
  const d = new THREE.Vector3(b.x - a.x, b.y - a.y, 0)
  const len = d.length()
  if (len < f.pitch) return []
  d.normalize()
  const yaw = Math.atan2(d.y, d.x)
  const out = []
  for (let k = 0; (k + 1) * f.pitch <= len + 1e-3; k++) {
    const at = new THREE.Vector3(a.x, a.y, a.z).addScaledVector(d, k * f.pitch)
    out.push({ name: f.mesh, kind: 'mesh', matrix: yawMatrix(yaw, at).toArray() })
  }
  return out
}

/** Points scattered in a disc of `radius` around `c`, at least `gap` apart. */
function scatter (c, count, radius, gap) {
  const out = []
  for (let tries = 0; out.length < count && tries < count * 30; tries++) {
    const r = radius * Math.sqrt(Math.random())
    const t = Math.random() * Math.PI * 2
    const p = new THREE.Vector3(c.x + r * Math.cos(t), c.y + r * Math.sin(t), c.z)
    if (out.every(q => Math.hypot(q.x - p.x, q.y - p.y) >= gap)) out.push(p)
  }
  return out
}

/** Find the surface at each scattered point, probing down from above the click. */
function onSurface (p, reach) {
  return surfaceBelow(new THREE.Vector3(p.x, p.y, p.z + reach))
}

function rocks (at, f) {
  const d = compounds.definitions().rocks
  const mix = f.small ? d.small : d.mix
  const out = []
  for (const p of scatter(at, Math.max(1, Math.round(f.count)), f.radius, 0)) {
    const s = onSurface(p, f.radius)
    if (!s) continue
    out.push({ name: weighted(mix), kind: 'mesh',
               matrix: yawMatrix(rand(0, Math.PI * 2), s.at.clone(), rand(f.min, f.max)).toArray() })
  }
  return out
}

function foliage (at, f) {
  const count = Math.max(1, Math.round(f.count))
  //: the disc that holds `count` instances `spacing` apart on average
  const radius = f.spacing * Math.sqrt(count / Math.PI)
  const out = []
  for (const p of scatter(at, count, radius, f.minSpacing)) {
    const s = onSurface(p, radius)
    if (!s) continue
    const up = f.slope ? s.normal : new THREE.Vector3(0, 0, 1)
    out.push({ name: f.mesh, kind: 'foliage',
               matrix: upright(s.at, up, rand(0, Math.PI * 2), rand(f.min, f.max)).toArray() })
  }
  return out
}

// -- probes for the UI smoke suite ------------------------------------------------------

//: Run a tool at Unreal points without the mouse: ('wallrun', [a, b]), ('foliage', [c]).
window.__rl_decorRun = async (name, points, overrides) => {
  tool = name
  values[name] = Object.assign(defaultsFor(name), overrides || {})
  armed = true
  for (const p of points) await run(new THREE.Vector3(p[0], p[1], p[2]))
  armed = false
  first = null
  const plan = api.plan()
  const top = compounds.nextGroup() - 1
  return JSON.parse(JSON.stringify((plan.added || []).filter(a => a.group === top)
    .map(a => ({ name: a.name, kind: a.kind, matrix: a.matrix }))))
}
window.__rl_decorOpen = () => { openPanel(); return document.getElementById('propshead').textContent }
window.__rl_addedFoliageShown = () => api.groups().newGeo.children
  .filter(m => m.userData.addedEntry && m.userData.addedEntry.kind === 'foliage')
  .map(m => m.visible)
