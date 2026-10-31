// A level from scratch (riftlord/scratch.py): the New Level dialog, the ground grid a
// new level is built on, and the snapping that keeps placed pieces on the trap grid.

import * as THREE from 'three'

//: Grid lines every this many cells are drawn brighter.
const MAJOR_EVERY = 4
const MINOR_COLOUR = 0x2f3644
const MAJOR_COLOUR = 0x56607a
//: The grid sits this far under the ground height, so a floor laid on it is not
//: z-fighting with its lines.
const GRID_SINK = 1
//: A piece lands on the grid level at or below where it was dropped, unless it was
//: dropped within this many uu under the next one up.
const Z_TOLERANCE = 30

const ground = new THREE.Group()
ground.name = 'scratch-ground'
let info = null

const esc = (text) => String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/>/g, '&gt;').replace(/"/g, '&quot;')

function build (grid) {
  for (const c of [...ground.children]) {
    ground.remove(c)
    if (c.geometry) c.geometry.dispose()
    if (c.material) c.material.dispose()
  }
  const { grid: cell, half, ground: z } = grid
  const minor = []
  const major = []
  const steps = Math.round(half / cell)
  for (let k = -steps; k <= steps; k++) {
    const v = k * cell
    const into = (k % MAJOR_EVERY === 0) ? major : minor
    into.push(-half, v, z - GRID_SINK, half, v, z - GRID_SINK)
    into.push(v, -half, z - GRID_SINK, v, half, z - GRID_SINK)
  }
  for (const [points, colour] of [[minor, MINOR_COLOUR], [major, MAJOR_COLOUR]]) {
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.Float32BufferAttribute(points, 3))
    ground.add(new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: colour })))
  }
  //: what a click lands on where there is no geometry yet
  const plane = new THREE.Mesh(new THREE.PlaneGeometry(half * 2, half * 2),
    new THREE.MeshBasicMaterial({ visible: false, side: THREE.DoubleSide }))
  plane.position.z = z
  plane.userData.scratchGround = true
  ground.add(plane)
}

/** Show the ground grid for an open scratch level (`level.scratch`), under `parent`.
 * The gizmo's snap is the Map Editor's own (gizmo.js, Snap to Grid). */
export function show (grid, parent) {
  info = grid || null
  if (!info) { hide(); return }
  build(info)
  if (ground.parent !== parent) parent.add(ground)
  ground.visible = true
}

export function hide () {
  info = null
  ground.visible = false
  if (ground.parent) ground.parent.remove(ground)
}

export const active = () => !!info

/** {grid: cell, ground: z} of the open scratch level's lattice, or null. */
export const lattice = () => info ? { grid: info.grid, ground: info.ground } : null

/** What a click may land on: the ground plane while a scratch level is open. */
export function pickables () {
  return info ? ground.children.filter(c => c.userData.scratchGround) : []
}

/** [dx, dy, dz] that puts a placed piece on the grid, or null: its footprint's cells on
 * the trap grid (a mesh overhangs its cells by a few uu, shared out on both sides) and
 * its origin on the whole number of cells above the ground at or below it. `originZ` is the piece's. */
export function snapDelta (meshes, originZ) {
  if (!info || !meshes.length) return null
  const box = new THREE.Box3()
  for (const m of meshes) {
    m.updateMatrix()
    if (!m.geometry.boundingBox) m.geometry.computeBoundingBox()
    box.union(m.geometry.boundingBox.clone().applyMatrix4(m.matrix))
  }
  if (box.isEmpty()) return null
  const cell = info.grid
  const axis = (lo, hi) => {
    const size = hi - lo
    const over = (size - Math.max(1, Math.round(size / cell)) * cell) / 2
    const from = lo + over
    return Math.round(from / cell) * cell - from
  }
  const z = originZ - info.ground
  return [axis(box.min.x, box.max.x), axis(box.min.y, box.max.y),
          Math.floor((z + Z_TOLERANCE) / cell) * cell - z]
}

//: what the last plan save said a scratch level still needs
let needed = []

/** Lines saying what a scratch level still needs, for the notice under Save; `given`
 * is a plan save's `needed`, kept for the notice after Save Mission. */
export function neededHtml (given) {
  if (given !== undefined) needed = given || []
  if (!info || !needed.length) return ''
  return '<br><b>Still needed before it plays:</b>' +
    needed.map(n => `<br>&bull; ${esc(n)}`).join('')
}

/** The New Level dialog. `skies` is /api/scratch/skies ([{material, title, default}]),
 * `modes` /api/scratch/modes ([{gameType, label, default}]; the Game Mode field shows only
 * with more than one). Resolves {name, base, sky, gameType}, or null when cancelled. */
export function ask (bases, skies = [], modes = []) {
  return new Promise((resolve) => {
    const shade = document.createElement('div')
    shade.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:50;' +
      'display:flex;align-items:center;justify-content:center'
    const one = bases.length === 1
    shade.innerHTML = `
      <div style="background:var(--panel);border:1px solid var(--line);border-radius:6px;
                  padding:16px;width:min(380px,92vw)">
        <h2 style="margin-top:0">New Level</h2>
        <label>Name<br><input id="scratchname" type="text" style="width:100%"
          placeholder="My Level"></label>
        <div style="margin-top:8px"${one ? ' hidden' : ''}>Light rig<br>
          <select id="scratchbase" style="width:100%">${bases.map(b =>
            `<option value="${esc(b.name)}"${b.default ? ' selected' : ''}>${esc(b.title)}</option>`).join('')}
          </select></div>
        <div style="margin-top:8px"${skies.length ? '' : ' hidden'}>Sky<br>
          <select id="scratchsky" style="width:100%">${skies.map(s =>
            `<option value="${esc(s.material)}"${s.default ? ' selected' : ''} ` +
            `title="${esc(s.material)}">${esc(s.title)}</option>`).join('')}
          </select></div>
        <div style="margin-top:8px"${modes.length > 1 ? '' : ' hidden'}>Game Mode<br>
          <select id="scratchmode" style="width:100%">${modes.map(m =>
            `<option value="${esc(m.gameType)}"${m.default ? ' selected' : ''}>` +
            `${esc(m.label)}</option>`).join('')}
          </select></div>
        <div class="grid2" style="margin-top:12px">
          <button id="scratchmake">Create</button>
          <button id="scratchcancel">Cancel</button>
        </div>
      </div>`
    document.body.appendChild(shade)
    const name = shade.querySelector('#scratchname')
    name.focus()
    const done = (value) => { shade.remove(); resolve(value) }
    const make = () => {
      const text = name.value.trim()
      if (!text) { name.focus(); return }
      done({ name: text, base: shade.querySelector('#scratchbase').value,
             sky: shade.querySelector('#scratchsky').value || null,
             gameType: modes.length > 1 ? shade.querySelector('#scratchmode').value : '' })
    }
    shade.querySelector('#scratchmake').addEventListener('click', make)
    shade.querySelector('#scratchcancel').addEventListener('click', () => done(null))
    name.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') make()
      if (ev.key === 'Escape') done(null)
    })
  })
}
