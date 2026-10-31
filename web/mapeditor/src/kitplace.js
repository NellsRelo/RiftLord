// Kit pieces and their trap grids: where a dropped piece goes and which way it faces,
// snapping a piece's trap grid onto a scratch level's lattice, and the marker that
// shows which side of a piece takes traps.

import * as THREE from 'three'

//: Trap-side marker colour and opacity.
const FACE_COLOUR = 0x3ddc84
const FACE_OPACITY = 0.28
//: The marker sits this far in front of the grid's face, in uu.
const FACE_LIFT = 12
//: Length of the arrow out of the trap side, in uu.
const ARROW_LENGTH = 300
//: A drop counts as on a lattice level when within this many uu under the next one up.
const Z_TOLERANCE = 30

//: class -> its grids, or the pending request for them
const gridCache = new Map()

/** A kit class's trap grids in actor space ([] when none), cached. `fetchJson` gets a URL. */
export function kitGrids (cls, fetchJson) {
  if (!cls) return Promise.resolve([])
  if (!gridCache.has(cls)) {
    const pending = fetchJson('api/kit/' + encodeURIComponent(cls))
      .then(r => (r && r.grids) || [], () => [])
      .then(got => { gridCache.set(cls, got); return got })
    gridCache.set(cls, pending)
  }
  const hit = gridCache.get(cls)
  return Array.isArray(hit) ? Promise.resolve(hit) : hit
}

/** A kit class's grids if they have arrived, else null. */
export const cachedGrids = (cls) => {
  const hit = gridCache.get(cls)
  return Array.isArray(hit) ? hit : null
}

/** The grid a piece is placed by: its wall grid if it has one, else its first. */
const mainGrid = (grids) => (grids || []).find(g => g.surface === 'Wall') || (grids || [])[0]

/** World box of one grid under `matrix`. */
function gridBox (grid, matrix) {
  return new THREE.Box3(new THREE.Vector3(...grid.min), new THREE.Vector3(...grid.max))
    .applyMatrix4(matrix)
}

/** World direction of a grid's trap side under `matrix`. */
function gridNormal (grid, matrix) {
  return new THREE.Vector3(...grid.normal).transformDirection(matrix)
}

/** Matrix for a kit piece dropped at `at` and seen from `eye`.
 *
 * A wall is turned by quarter turns so its trap side faces the viewer, then placed
 * with its grid's base on the clicked surface and centred on the click. A floor is
 * centred on the click with its grid at the clicked height. Null when it has no grid. */
export function placeMatrix (grids, at, eye) {
  const grid = mainGrid(grids)
  if (!grid) return null
  let yaw = 0
  if (grid.surface === 'Wall') {
    const want = new THREE.Vector2(eye.x - at[0], eye.y - at[1])
    if (want.lengthSq() > 0) {
      want.normalize()
      let best = -Infinity
      for (let k = 0; k < 4; k++) {
        const turn = new THREE.Matrix4().makeRotationZ(k * Math.PI / 2)
        const n = gridNormal(grid, turn)
        const score = n.x * want.x + n.y * want.y
        if (score > best + 1e-6) { best = score; yaw = k * Math.PI / 2 }
      }
    }
  }
  const matrix = new THREE.Matrix4().makeRotationZ(yaw)
  const box = gridBox(grid, matrix)
  const centre = box.getCenter(new THREE.Vector3())
  const base = grid.surface === 'Wall' ? box.min.z : centre.z
  matrix.setPosition(at[0] - centre.x, at[1] - centre.y, at[2] - base)
  return matrix
}

/** [dx, dy, dz] that puts a kit piece's trap grid on a scratch lattice, or null.
 *
 * `lattice` is {grid: cell, ground: z}. A wall's actor origin goes onto the nearest cell
 * line across the wall (shipped levels put the origin there, not the grid plane: a
 * Wall_FB_MT_*_SAND grid plane is 47.5 uu in front of it) and its grid's length onto
 * whole cells; a floor's footprint onto whole cells. The grid's base (a wall) or plane
 * (a floor) goes onto a whole number of cells above the ground, at or below where it was
 * dropped. */
export function snapGrid (grids, matrix, lattice) {
  const grid = mainGrid(grids)
  if (!grid || !lattice) return null
  const cell = lattice.grid
  const box = gridBox(grid, matrix)
  const centre = box.getCenter(new THREE.Vector3())
  const toLine = (v) => Math.round(v / cell) * cell - v
  const delta = [toLine(box.min.x), toLine(box.min.y), 0]
  let height
  if (grid.surface === 'Wall') {
    const n = gridNormal(grid, matrix)
    const across = Math.abs(n.x) >= Math.abs(n.y) ? 0 : 1
    delta[across] = toLine(matrix.elements[12 + across])
    height = box.min.z
  } else {
    height = centre.z
  }
  const z = height - lattice.ground
  delta[2] = Math.floor((z + Z_TOLERANCE) / cell) * cell - z
  return delta
}

/** The markers showing each kit piece's trap side, kept in step with the plan. */
export class TrapFaces {
  constructor (parent) {
    this.group = new THREE.Group()
    this.group.name = 'trap-faces'
    parent.add(this.group)
    this.shown = new Map()
    this.material = new THREE.MeshBasicMaterial({
      color: FACE_COLOUR, transparent: true, opacity: FACE_OPACITY,
      side: THREE.DoubleSide, depthWrite: false })
  }

  /** One marker per grid of each `{cls, matrix}` item (a flat column-major matrix). */
  sync (items) {
    const keep = new Set()
    for (const item of items) {
      const grids = cachedGrids(item.cls)
      if (!grids || !grids.length) continue
      const key = item.cls + '|' + item.matrix.join(',')
      keep.add(key)
      if (this.shown.has(key)) continue
      const matrix = new THREE.Matrix4().fromArray(item.matrix)
      const marks = new THREE.Group()
      for (const grid of grids) this._mark(grid, matrix, marks)
      this.group.add(marks)
      this.shown.set(key, marks)
    }
    for (const [key, marks] of this.shown) {
      if (keep.has(key)) continue
      this.group.remove(marks)
      for (const m of marks.children) if (m.geometry) m.geometry.dispose()
      this.shown.delete(key)
    }
  }

  _mark (grid, matrix, into) {
    const box = gridBox(grid, matrix)
    const size = box.getSize(new THREE.Vector3())
    const centre = box.getCenter(new THREE.Vector3())
    const n = gridNormal(grid, matrix)
    const thin = [Math.abs(n.x), Math.abs(n.y), Math.abs(n.z)]
    const axis = thin.indexOf(Math.max(...thin))
    const dims = [size.x, size.y, size.z]
    const face = new THREE.BoxGeometry(axis === 0 ? 1 : dims[0], axis === 1 ? 1 : dims[1],
      axis === 2 ? 1 : dims[2])
    const panel = new THREE.Mesh(face, this.material)
    panel.position.copy(centre).addScaledVector(n, dims[axis] / 2 + FACE_LIFT)
    panel.raycast = () => {}
    into.add(panel)
    const arrow = new THREE.ArrowHelper(n, panel.position, ARROW_LENGTH, FACE_COLOUR)
    arrow.traverse(o => { o.raycast = () => {} })
    into.add(arrow)
  }
}
