/** The level's blocking and kill volumes: a layer of translucent boxes that are picked,
 * moved, turned, resized and removed like the level's own actors.
 *
 * Plan key `volumes`: [{name, at: [x, y, z]} | {name, matrix} | {name, gone: true}]
 * (riftlord/mapeditor/volumes.py): `at` for a move alone, `matrix` (column-major 4x4,
 * Unreal space) once it is turned or resized.
 * A volume's marker carries userData {wiring: true, kind: 'volume', name, data}.
 */
import * as THREE from 'three'

//: Box colour per what a volume stops.
export const VOLUME_COLOUR = {
  everyone: 0xff5a4f, players: 0xffb347, kill: 0xd23cff, camera: 0x6aa8ff, traps: 0x9aa0aa,
}

//: Fill and outline opacity of a volume's box.
const FILL_OPACITY = 0.16
const EDGE_OPACITY = 0.75


let ctx = null

/** Hand the module the editor's state and helpers it edits through. */
export function useVolumes (context) {
  ctx = context
}

const escape = (s) => String(s).replace(/[&<>"]/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c])

/** The plan's edit for one volume, or undefined. */
function editOf (plan, name) {
  return (plan.volumes || []).find(v => v.name === name)
}

/** One volume's translucent box: its brush's local box under `v.matrix`. */
function volumeBox (v) {
  const size = [0, 1, 2].map(a => Math.max(v.max[a] - v.min[a], 1))
  const geo = new THREE.BoxGeometry(size[0], size[1], size[2])
  geo.translate(...[0, 1, 2].map(a => (v.max[a] + v.min[a]) / 2))
  const colour = VOLUME_COLOUR[v.stops] || VOLUME_COLOUR.traps
  const box = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({
    color: colour, transparent: true, opacity: FILL_OPACITY, depthWrite: false,
    side: THREE.DoubleSide, fog: false }))
  const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geo),
    new THREE.LineBasicMaterial({ color: colour, transparent: true, opacity: EDGE_OPACITY,
      fog: false }))
  edges.raycast = () => {}
  box.add(edges)
  new THREE.Matrix4().fromArray(v.matrix)
    .decompose(box.position, box.quaternion, box.scale)
  box.renderOrder = 3
  box.userData = { wiring: true, kind: 'volume', name: v.name, data: v }
  return box
}

/** Draw every volume of `data.volumes` into `group`, one box each. */
export function drawVolumes (group, data) {
  for (const v of data.volumes || []) group.add(volumeBox(v))
}

//: An added volume's brush (api/volumes/donor: {cls, label, stops, stopsText, min, max}),
//: once read; the pending read while it is asked for.
let donorBox = null
let donorAsk = null

/** The added volume's brush, read once. */
export function loadDonor () {
  donorAsk = donorAsk || fetch('api/volumes/donor').then(r => r.json()).then((got) => {
    donorBox = got && got.max ? got : null
    if (!donorBox) donorAsk = null
    return donorBox
  }).catch(() => { donorAsk = null; return null })
  return donorAsk
}

//: Plan names of added volumes (riftlord/mapeditor/volumes.py ADDED_PREFIX).
const ADDED_PREFIX = 'RiftLordVolume'

/** The data of an added volume's box: the brush, placed by the plan's matrix. */
function addedData (e) {
  return { ...donorBox, name: e.name, matrix: e.matrix, location: e.matrix.slice(12, 15),
           added: true, stops: ADDED_DEFAULT, blocks: ADDED_DEFAULT }
}

//: What an added volume whose plan entry does not say stops (volumes.py ADDED_DEFAULT).
const ADDED_DEFAULT = 'everyone'
//: The Blocks choices: [kind, label] (volumes.py BLOCKS).
const BLOCKS = [['players', 'Players'], ['everyone', 'Players and Enemies']]
//: What each kind stops, in words (volumes.py STOPS_TEXT).
const STOPS_TEXT = { players: 'stops players only', everyone: 'stops players and enemies' }

/** What volume box `m` stops now: the plan's Blocks, else as shipped (or as added). */
function stopsNow (m, plan) {
  const e = editOf(plan, m.userData.name)
  return (m.userData.data.blocks && e && e.blocks) || m.userData.data.stops
}

/** Colour box `m` (and its edges) by what it stops now. */
function paintBox (m, plan) {
  const colour = VOLUME_COLOUR[stopsNow(m, plan)] || VOLUME_COLOUR.traps
  m.material.color.setHex(colour)
  for (const c of m.children) if (c.material) c.material.color.setHex(colour)
}

/** Draw and drop the boxes of added volumes so they match the plan's. */
function syncAdded (group, plan) {
  const want = (plan.volumes || []).filter(e => e.added && e.matrix)
  for (const m of [...group.children]) {
    if (m.userData.data.added && !want.some(e => e.name === m.userData.name)) {
      group.remove(m)
      m.geometry.dispose()
    }
  }
  if (!want.length) return
  if (!donorBox) {
    loadDonor().then((got) => { if (got) applyVolumes(group, ctx.plan()) })
    return
  }
  for (const e of want) {
    if (!group.children.some(m => m.userData.name === e.name)) group.add(volumeBox(addedData(e)))
  }
}

/** Add a blocking volume (the donor's brush, `size` uu each way) standing on `at`, and
 * return its box, or null when the brush cannot be read. */
export async function addVolume (group, at, size = 300) {
  const got = await loadDonor()
  if (!got) return null
  const plan = ctx.plan()
  plan.volumes = plan.volumes || []
  let n = 1
  while (plan.volumes.some(e => e.name === ADDED_PREFIX + n)) n += 1
  const scale = [0, 1, 2].map(a => size / Math.max(got.max[a] - got.min[a], 1))
  const lift = -got.min[2] * scale[2]
  const matrix = new THREE.Matrix4().makeScale(...scale)
    .setPosition(at[0], at[1], at[2] + lift).toArray()
  ctx.snapshot()
  plan.volumes.push({ name: ADDED_PREFIX + n, added: true, matrix, blocks: got.new })
  applyVolumes(group, plan)
  return group.children.find(m => m.userData.name === ADDED_PREFIX + n) || null
}

/** Put each volume where the plan says: moved, turned and resized, or hidden when removed. */
export function applyVolumes (group, plan) {
  syncAdded(group, plan)
  for (const m of group.children) {
    const d = m.userData
    const e = editOf(plan, d.name)
    m.visible = !(e && e.gone)
    new THREE.Matrix4().fromArray((e && e.matrix) || d.data.matrix)
      .decompose(m.position, m.quaternion, m.scale)
    if (e && e.at && !e.matrix) m.position.set(e.at[0], e.at[1], e.at[2])
    paintBox(m, plan)
  }
}

const DEG = 180 / Math.PI
//: The smallest a volume's scale may be on any axis (riftlord/mapeditor/volumes.py
//: MIN_SCALE).
const MIN_SCALE = 0.02

/** An Unreal rotator [pitch, yaw, roll] in degrees from a column-major matrix in Unreal
 * space (riftlord/build/apply.py transform_of). */
export function rotatorOf (matrix) {
  const e = matrix.elements
  const rows = [0, 1, 2].map((c) => {
    const v = [e[c * 4], e[c * 4 + 1], e[c * 4 + 2]]
    const n = Math.hypot(...v) || 1
    return v.map(x => x / n)
  })
  const sp = Math.max(-1, Math.min(1, rows[0][2]))
  const cp = Math.sqrt(Math.max(0, 1 - sp * sp))
  const pitch = Math.asin(sp) * DEG
  if (cp < 1e-3) return [pitch, Math.atan2(-rows[1][0], rows[1][1]) * DEG, 0]
  return [pitch, Math.atan2(rows[0][1], rows[0][0]) * DEG,
          Math.atan2(-rows[1][2], rows[2][2]) * DEG]
}

/** The column-major matrix of an Unreal transform: location, rotator (pitch, yaw, roll in
 * degrees) and scale (riftlord/formats/omd3/omd3level.py _matrix). */
export function unrealMatrix (loc, rot, scale) {
  const [p, y, r] = rot.map(v => v / DEG)
  const sp = Math.sin(p)
  const cp = Math.cos(p)
  const sy = Math.sin(y)
  const cy = Math.cos(y)
  const sr = Math.sin(r)
  const cr = Math.cos(r)
  const rows = [[cp * cy, cp * sy, sp],
    [sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp],
    [-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp]]
  const m = new THREE.Matrix4()
  m.set(rows[0][0] * scale[0], rows[1][0] * scale[1], rows[2][0] * scale[2], loc[0],
        rows[0][1] * scale[0], rows[1][1] * scale[1], rows[2][1] * scale[2], loc[1],
        rows[0][2] * scale[0], rows[1][2] * scale[1], rows[2][2] * scale[2], loc[2],
        0, 0, 0, 1)
  return m
}

/** Whether matrices `a` and `b` turn and scale alike (their 3 x 3 parts agree). */
function sameBasis (a, b) {
  const x = a.elements
  const y = b.elements
  for (const i of [0, 1, 2, 4, 5, 6, 8, 9, 10]) {
    if (Math.abs(x[i] - y[i]) > 1e-4 * Math.max(1, Math.abs(y[i]))) return false
  }
  return true
}

/** Store marker `m`'s transform in its plan edit: `at` while it is only moved, `matrix`
 * once it is turned or resized; the edit goes when nothing is left in it. */
function storeTransform (m) {
  m.scale.set(...[m.scale.x, m.scale.y, m.scale.z].map(s => Math.max(Math.abs(s), MIN_SCALE)))
  m.updateMatrix()
  const d = m.userData
  const e = entry(d.name)
  const round = (v, i) => i >= 12 && i < 15 ? Math.round(v * 100) / 100 : Math.round(v * 1e6) / 1e6
  //: an added volume is its matrix
  if (d.data.added) {
    e.matrix = m.matrix.toArray().map(round)
    d.data.matrix = e.matrix
    return
  }
  const shipped = new THREE.Matrix4().fromArray(d.data.matrix)
  delete e.at
  delete e.matrix
  if (!sameBasis(m.matrix, shipped)) {
    e.matrix = m.matrix.toArray().map((v, i) => i >= 12 && i < 15
      ? Math.round(v * 100) / 100 : Math.round(v * 1e6) / 1e6)
  } else {
    const p = m.position
    const at = [p.x, p.y, p.z].map(v => Math.round(v * 100) / 100)
    if (at.some((v, i) => Math.abs(v - d.data.location[i]) > 0.005)) e.at = at
  }
  const plan = ctx.plan()
  if (Object.keys(e).length === 1) plan.volumes = (plan.volumes || []).filter(o => o !== e)
}

function entry (name) {
  const plan = ctx.plan()
  plan.volumes = plan.volumes || []
  let e = editOf(plan, name)
  if (!e) {
    e = { name }
    plan.volumes.push(e)
  }
  return e
}

/** Copy the picked volume's dragged place, turn and size into the plan. */
export function recordVolumeMove (m) {
  storeTransform(m)
}

/** Remove the picked volume from the level. */
export function removeVolume (m) {
  ctx.snapshot()
  const plan = ctx.plan()
  if (m.userData.data.added) {
    plan.volumes = (plan.volumes || []).filter(o => o.name !== m.userData.name)
    applyVolumes(m.parent, plan)
    ctx.unpick()
    return
  }
  const e = entry(m.userData.name)
  delete e.at
  delete e.matrix
  e.gone = true
  m.visible = false
  ctx.unpick()
}

//: A kill volume's switches (riftlord/volumes.py KILL_FLAGS) and their labels.
const KILL_FLAGS = [['killMinions', 'Kills enemies'], ['killPlayers', 'Kills heroes']]

/** The Kill section of a kill volume: its switches as the plan sets them, else shipped. */
function killFlagsHtml (v, e) {
  if (!KILL_FLAGS.some(([key]) => key in v)) return ''
  return '<h2>Kill</h2>' + KILL_FLAGS.map(([key, label]) => {
    const on = e && key in e ? e[key] : v[key]
    return `<label class="row"><span>${label}</span>` +
      `<input data-killflag="${key}" type="checkbox"${on ? ' checked' : ''}></label>`
  }).join('')
}

/** The sidebar for the picked volume. */
export function showVolumeProps (m) {
  const body = ctx.el('propsbody')
  ctx.el('app').classList.add('props')
  const d = m.userData
  const v = d.data
  const plan = ctx.plan()
  const e = editOf(plan, d.name)
  m.updateMatrix()
  const at = [m.position.x, m.position.y, m.position.z]
  const rot = rotatorOf(m.matrix)
  const scale = [0, 1, 2].map(c => new THREE.Vector3().setFromMatrixColumn(m.matrix, c).length())
  const extent = [0, 1, 2].map(a => Math.max(v.max[a] - v.min[a], 1))
  const size = [0, 1, 2].map(a => extent[a] * scale[a])
  const round = (x, places) => Math.round(x * 10 ** places) / 10 ** places
  const triple = (key, labels, values, places, step) => '<div class="triple">' +
    labels.map((label, i) => `<label><span>${label}</span><input data-${key}="${i}" ` +
      `type="number" step="${step}" value="${round(values[i], places)}"></label>`).join('') +
    '</div>'
  const mode = ctx.gizmo.mode
  const blocks = stopsNow(m, plan)
  const rows = [`<div class="card sel"><b>${escape(v.title || d.name)}</b>` +
    `<div class="meta">${escape(v.label)}: ${escape(STOPS_TEXT[blocks] || v.stopsText)}` +
    '</div></div>',
  v.blocks
    ? '<label class="pick"><span>Blocks</span><select id="volblocks">' + BLOCKS.map(([k, label]) =>
      `<option value="${k}"${k === blocks ? ' selected' : ''}>${label}</option>`).join('') +
      '</select></label>'
    : '',
  '<h2>Handles</h2><div id="gizmomode">' + [['translate', 'move'], ['rotate', 'turn'],
    ['scale', 'size']].map(([k, label]) =>
    `<button data-gmode="${k}"${mode === k ? ' class="on"' : ''}>${label}</button>`).join('') +
    '</div>',
  '<h2>Position</h2>' + triple('vat', ['X', 'Y', 'Z'], at, 0, 1),
  '<h2>Rotation</h2>' + triple('vrot', ['Pitch', 'Yaw', 'Roll'], rot, 1, 1),
  '<h2>Size</h2>' + triple('vsize', ['X', 'Y', 'Z'], size, 0, 1),
  killFlagsHtml(v, e)]
  if (e && !v.added) rows.push('<button id="restorevolume">Reset</button>')
  rows.push('<button id="removevolume">Delete</button>')
  body.innerHTML = rows.join('')
  ctx.el('gizmomode').addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-gmode]')
    if (!b) return
    ctx.gizmo.setMode(b.dataset.gmode)
    showVolumeProps(m)
  })
  //: Blocks: an added volume stores its choice; a level volume stores only a change
  const choose = ctx.el('volblocks')
  if (choose) {
    choose.addEventListener('change', () => {
      ctx.snapshot()
      const edit = entry(d.name)
      if (!v.added && choose.value === v.blocks) delete edit.blocks
      else edit.blocks = choose.value
      if (Object.keys(edit).length === 1) {
        plan.volumes = (plan.volumes || []).filter(o => o !== edit)
      }
      ctx.refresh()
    })
  }
  //: a typed position, rotation or size: that one value changed, the rest as they are
  const now = { vat: at, vrot: rot, vsize: size }
  for (const key of Object.keys(now)) {
    for (const input of body.querySelectorAll(`[data-${key}]`)) {
      input.addEventListener('change', () => {
        if (input.value === '' || !Number.isFinite(+input.value)) return
        ctx.snapshot()
        now[key] = now[key].slice()
        now[key][+input.dataset[key]] = +input.value
        const sized = now.vsize.map((s, a) => Math.max(s, 1) / extent[a])
        unrealMatrix(now.vat, now.vrot, sized).decompose(m.position, m.quaternion, m.scale)
        storeTransform(m)
        ctx.refresh()
      })
    }
  }
  const restore = ctx.el('restorevolume')
  if (restore) {
    restore.addEventListener('click', () => {
      ctx.snapshot()
      plan.volumes = (plan.volumes || []).filter(o => o.name !== d.name)
      new THREE.Matrix4().fromArray(v.matrix).decompose(m.position, m.quaternion, m.scale)
      ctx.refresh()
    })
  }
  ctx.el('removevolume').addEventListener('click', () => removeVolume(m))
  for (const box of body.querySelectorAll('[data-killflag]')) {
    box.addEventListener('change', () => {
      ctx.snapshot()
      const key = box.dataset.killflag
      const edit = entry(d.name)
      if (box.checked === v[key]) delete edit[key]
      else edit[key] = box.checked
      if (Object.keys(edit).length === 1) {
        plan.volumes = (plan.volumes || []).filter(o => o.name !== d.name)
      }
      ctx.refresh()
    })
  }
  ctx.gizmo.attach(m)
}
