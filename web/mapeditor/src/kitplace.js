// Kit pieces and their trap grids: where a dropped piece goes and which way it faces,
// snapping a piece's trap grid onto the shipped lattice and against its neighbours, and
// the marker that shows which side of a piece takes traps.

import * as THREE from 'three'

//: Trap-side marker colour and opacity.
const FACE_COLOUR = 0x3ddc84
const FACE_OPACITY = 0.28
//: The marker sits this far in front of the grid's face, in uu.
const FACE_LIFT = 12
//: Length of the arrow out of the trap side, in uu.
const ARROW_LENGTH = 300
//: The world lattice trap grids snap to, in uu: half a trap cell
//: (docs/research/kit-seams-and-trap-grids.md).
export const LATTICE = 150
//: Floor heights snap to multiples of this, in uu.
export const HEIGHT_STEP = 50
//: A skirted wall's trap grid starts this far above the plane of the floor grid it stands
//: on, in uu, and a wall's grid bottom with no floor under it this far over a HEIGHT_STEP
//: (docs/research/kit-placement-census.md section 6: 3,274 of 6,434 wall grids, 1,044
//: with the bottom itself on a step).
export const WALL_ABOVE_FLOOR = 20
//: A wall is skirted, and stands WALL_ABOVE_FLOOR over a floor, when its meshes reach this
//: far or more below its grid's bottom (kitfit.py `below`; census section 2): they reach
//: the floor. Others are plain and stand on the floor with their grid's bottom.
export const SKIRT_BELOW = WALL_ABOVE_FLOOR
//: Ceiling planes sit this far over a HEIGHT_STEP: 50 over a wall grid's top, which is at
//: the walls' WALL_ABOVE_FLOOR (census section 2: 921 of 1,236 ceiling grids at 20 mod 50,
//: 84 on 50).
export const CEILING_ABOVE_STEP = WALL_ABOVE_FLOOR
//: The floor in front of a skirted wall stops at its actor origin when the skirting
//: reaches within FACE_TOLERANCE of it, else at the skirting's reach rounded down to a
//: STOP_STEP, the reach taken to the nearest REACH_STEP first (census section 9, its
//: fallback.py: 37.5 for Wall_RB_WDMTL_v1_1x2, 40 for Wall_RB_WDMT_v1_2x3), in uu.
export const FACE_TOLERANCE = 0.5
export const REACH_STEP = 0.5
export const STOP_STEP = 2.5
//: A floor counts as an upper floor over a wall's top when its plane lies this far over
//: the wall's highest mesh vertex, in uu (census section 4, upper floors proper).
export const UPPER_FLOOR_RISE = [-10, 300]
//: At a plain wall's end a column's grid facing the same way stands this far in front of
//: the wall's face; at a skirted wall's end, on its origin line (census section 5.4:
//: Wall_RB with Column_Sec_RB 50 x146, Wall_RB_Dry with ColumnBlock_RB 50 x50), in uu.
export const COLUMN_PROUD_PLAIN = 50
//: A piece is a column when its wall grids face this many level directions or more; a
//: wall's face one way or two opposite ways.
export const COLUMN_SIDES = 3
//: A neighbour's grid edge, plane or floor catches a snap within this many uu.
export const NEIGHBOUR_RANGE = 37.5
//: A room is floor grids chained edge to edge (docs/research/kit-seams-and-trap-grids.md
//: section 4, /mnt/2tb_pro_a/tmp/kitseams/analyse.py rooms): two floor grids chain when
//: their planes lie within ROOM_PLANE uu, their facing edges within ROOM_SEAM uu and
//: they share ROOM_RUN uu of that edge or more; a room has ROOM_GRIDS grids or more.
export const ROOM_PLANE = 25
export const ROOM_SEAM = 1
export const ROOM_RUN = LATTICE
export const ROOM_GRIDS = 3
//: A grid edge counts toward a room's phase within this many uu of it (analyse.py rooms).
const PHASE_TOLERANCE = 1
//: A grid whose world normal's z is above this faces up (Floor) or down (Ceiling).
const FACE_VERTICAL = 0.7
//: An actor axis counts as lying along a level axis within this share of its length.
const AXIS_TOLERANCE = 1e-4

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
export const mainGrid = (grids) => (grids || []).find(g => g.surface === 'Wall') || (grids || [])[0]

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

/** True when `matrix` (a Matrix4) only turns by quarter turns about the level's axes, so
 * its grids' world boxes are the grids themselves. */
export function quarterTurned (matrix) {
  const e = matrix.elements
  for (let c = 0; c < 3; c++) {
    const v = [e[c * 4], e[c * 4 + 1], e[c * 4 + 2]]
    const len = Math.hypot(...v)
    if (!len || Math.max(...v.map(Math.abs)) / len < 1 - AXIS_TOLERANCE) return false
  }
  return true
}

/** One grid in world space under `matrix`: {box, surface, across, sign, foot}, and for a
 * wall {plane, line, stop, lift, top, gridOnly, column}.
 *
 * `surface` is Floor, Ceiling or Wall by the world normal. `across` is the axis the
 * normal lies along (2 for a floor or ceiling) and `sign` its direction on it. A wall's
 * `foot` is the actor origin's coordinate on `across`; a floor's or ceiling's is its
 * plane's height.
 *
 * A wall's rules come from its class's meshes against the grid (`grid.fit`, kitfit.py):
 * - `plane`: the grid's plane (its face) on `across`.
 * - `lift`: how far its grid's bottom stands over the floor under it: WALL_ABOVE_FLOOR when
 *   its meshes reach SKIRT_BELOW below the bottom (skirted), else 0 (plain).
 * - `gridOnly`: its actor origin lies behind its face (towers, archways): no foot.
 * - `line`: what goes on the lattice across it and on a column's grid: the origin of a
 *   skirted wall, the face of a plain or grid-only one.
 * - `stop`: where the floor in front of it stops: the origin of a skirted wall whose
 *   skirting reaches it, else the skirting's reach rounded down to a STOP_STEP; the face
 *   of a plain wall; null for a grid-only one.
 * - `top`: the height of its highest mesh vertex (its grid's top without fit data).
 * - `column`: `piece.column` (piecePlaces).
 * A grid without fit data keeps the rules from before fit data: lift WALL_ABOVE_FLOOR,
 * line and stop on the origin. */
export function gridPlace (grid, matrix, piece = {}) {
  const box = gridBox(grid, matrix)
  const n = gridNormal(grid, matrix)
  const surface = n.z > FACE_VERTICAL ? 'Floor' : n.z < -FACE_VERTICAL ? 'Ceiling' : 'Wall'
  const across = surface !== 'Wall' ? 2 : Math.abs(n.x) >= Math.abs(n.y) ? 0 : 1
  const sign = Math.sign(n.getComponent(across)) || 1
  const foot = surface === 'Wall' ? matrix.elements[12 + across] : box.getCenter(new THREE.Vector3()).z
  const place = { box, surface, across, sign, foot }
  if (surface !== 'Wall') return place
  const plane = (box.min.getComponent(across) + box.max.getComponent(across)) / 2
  const fit = grid.fit
  const height = matrix.elements[10]
  place.plane = plane
  place.column = !!piece.column
  if (!fit) {
    return Object.assign(place, { lift: WALL_ABOVE_FLOOR, gridOnly: false, line: foot, stop: foot, top: box.max.z })
  }
  const origin = (foot - plane) * sign
  const lift = fit.below >= SKIRT_BELOW ? WALL_ABOVE_FLOOR : 0
  const gridOnly = origin < -FACE_TOLERANCE
  let stop = plane
  if (gridOnly) stop = null
  else if (lift && fit.reach !== null && fit.reach !== undefined) {
    const reach = Math.round(fit.reach / REACH_STEP) * REACH_STEP
    stop = reach >= origin - FACE_TOLERANCE ? foot : plane + sign * Math.floor(reach / STOP_STEP) * STOP_STEP
  } else if (lift) stop = foot
  return Object.assign(place, { lift, gridOnly, stop, line: lift && !gridOnly ? foot : plane,
    top: box.min.z + fit.top * height })
}

/** Each grid of a piece with trap grids `grids` under `matrix` as a gridPlace, with
 * `column` set on its walls when the piece is a column: its wall grids face COLUMN_SIDES
 * level directions or more and its main grid has a foot. */
export function piecePlaces (grids, matrix) {
  const places = (grids || []).filter(g => g.min && g.max && g.normal).map(g => gridPlace(g, matrix))
  const sides = new Set(places.filter(p => p.surface === 'Wall').map(p => p.across * 2 + (p.sign > 0 ? 1 : 0)))
  const main = mainGrid(grids)
  const column = sides.size >= COLUMN_SIDES && !!main && !gridPlace(main, matrix).gridOnly
  if (column) for (const p of places) if (p.surface === 'Wall') p.column = true
  return places
}

/** The value of `place` that axis `axis` snaps, and that value's lattice: [value, step,
 * offset]. A floor or ceiling: its grid's start on x and y, its plane on z (a ceiling's
 * CEILING_ABOVE_STEP over a step). A wall: its line across it, its grid's start along it,
 * its grid's bottom on z (WALL_ABOVE_FLOOR over a step). */
function snapped (place, axis) {
  const { box, surface, across } = place
  if (axis !== 2) return [axis === across ? place.line : box.min.getComponent(axis), LATTICE, 0]
  if (surface === 'Wall') return [box.min.z, HEIGHT_STEP, WALL_ABOVE_FLOOR]
  return [place.foot, HEIGHT_STEP, surface === 'Ceiling' ? CEILING_ABOVE_STEP : 0]
}

/** True when boxes `a` and `b` overlap or come within `range` of each other on `axis`. */
const beside = (a, b, axis, range) =>
  b.max.getComponent(axis) >= a.min.getComponent(axis) - range &&
  b.min.getComponent(axis) <= a.max.getComponent(axis) + range

/** Where a column's grid facing the same way as wall `w` (a gridPlace) stands at its end,
 * on `across`: on a skirted wall's origin line, COLUMN_PROUD_PLAIN in front of a plain
 * wall's face. */
const columnLine = (w) => w.lift && w.line === w.foot ? w.foot : w.plane + w.sign * COLUMN_PROUD_PLAIN

/** The moves along `axis` that would put `me` (a gridPlace) against neighbour `n` (a
 * gridPlace) the way shipped levels join them, as [move]: grids edge to edge or lined up;
 * coplanar floors and walls; a wall's stop on the edge of the floor in front of it and its
 * grid its lift over that floor (or over a floor running under it); an upper floor's edge
 * on the origin line of the wall under its edge; a column's grid at a wall's end, edge to
 * edge with it on the wall's columnLine. A grid-only wall joins walls alone. */
function joins (me, n, axis, range) {
  const a = me.box
  const b = n.box
  const at = (v, k) => v.getComponent(k)
  const out = []
  const touching = () => out.push(at(b.max, axis) - at(a.min, axis), at(b.min, axis) - at(a.max, axis))
  const edges = () => {
    touching()
    out.push(at(b.min, axis) - at(a.min, axis), at(b.max, axis) - at(a.max, axis))
  }
  const flat = (p) => p.surface !== 'Wall'
  const middle = (box, k) => (at(box.min, k) + at(box.max, k)) / 2
  //: floor `f` at wall `w`'s foot height, null for a grid-only wall
  const atFoot = (w, f) => w.stop !== null && Math.abs(w.box.min.z - w.lift - f.foot) <= range
  //: the floor `f` in front of wall `w`: the edge facing it, or null when `f` is not
  //: at the wall's foot height or lies behind it
  const frontEdge = (w, f) => {
    if (!atFoot(w, f)) return null
    const c = w.across
    if ((middle(f.box, c) - w.plane) * w.sign <= 0) return null
    return w.sign > 0 ? at(f.box.min, c) : at(f.box.max, c)
  }
  //: true when floor `f` at wall `w`'s foot height runs under its face
  const under = (w, f) => atFoot(w, f) && at(f.box.min, w.across) <= w.plane && w.plane <= at(f.box.max, w.across)
  //: the floor `f` over wall `w`'s top, behind it: its edge toward the wall's trap side, or
  //: null
  const upperEdge = (w, f) => {
    if (w.gridOnly || f.surface !== 'Floor') return null
    const rise = f.foot - w.top
    if (rise < UPPER_FLOOR_RISE[0] || rise > UPPER_FLOOR_RISE[1]) return null
    const c = w.across
    if ((middle(f.box, c) - w.plane) * w.sign >= 0) return null
    return w.sign > 0 ? at(f.box.max, c) : at(f.box.min, c)
  }
  if (flat(me)) {
    if (flat(n) && n.surface === me.surface) {
      const level = Math.abs(n.foot - me.foot) <= range
      const others = [0, 1, 2].filter(k => k !== axis && k !== 2)
      if (axis === 2 && beside(a, b, 0, range) && beside(a, b, 1, range)) out.push(n.foot - me.foot)
      else if (axis !== 2 && level && others.every(k => beside(a, b, k, range))) edges()
    } else if (n.surface === 'Wall' && me.surface === 'Floor' && axis === n.across &&
               beside(a, b, 1 - axis, range)) {
      const edge = frontEdge(n, me)
      if (edge !== null) out.push(n.stop - edge)
      const upper = upperEdge(n, me)
      if (upper !== null) out.push(n.foot - upper)
    }
    return out
  }
  const c = me.across
  const u = 1 - c
  if (n.surface === 'Floor') {
    if (!beside(a, b, u, range)) return out
    const edge = frontEdge(me, n)
    const reach = edge !== null && Math.abs(edge - me.stop) <= range
    if (axis === c && edge !== null) out.push(edge - me.stop)
    else if (axis === 2 && (reach || under(me, n))) out.push(n.foot + me.lift - a.min.z)
    else if (axis === u && reach) out.push(at(b.min, u) - at(a.min, u), at(b.max, u) - at(a.max, u))
    const upper = upperEdge(me, n)
    if (axis === c && upper !== null) out.push(upper - me.foot)
    return out
  }
  if (n.surface !== 'Wall' || n.across !== c || n.sign !== me.sign) return out
  if (me.column !== n.column) {
    const wall = me.column ? n : me
    if (wall.gridOnly) return out
    const col = me.column ? me : n
    const gap = (columnLine(wall) - col.plane) * (me.column ? 1 : -1)
    if (axis === c && beside(a, b, u, range) && beside(a, b, 2, range)) out.push(gap)
    else if (axis === u && Math.abs(gap) <= range && beside(a, b, 2, range)) touching()
    return out
  }
  const coplanar = Math.abs(n.plane - me.plane) <= range
  if (axis === c && beside(a, b, u, range) && beside(a, b, 2, range)) out.push(n.plane - me.plane)
  else if (axis === u && coplanar && beside(a, b, 2, range)) edges()
  else if (axis === 2 && coplanar && beside(a, b, u, range)) out.push(b.min.z - a.min.z)
  return out
}

/** Where a piece with trap grids `grids` under `matrix` (a Matrix4) snaps to:
 * {delta: [dx, dy, dz], hits: [Box3 of each neighbour joined]}.
 *
 * Each axis in `options.axes` (default all three) snaps the piece's main grid (any of a
 * column's wall grids, for a join): to the nearest join with a neighbour in
 * `options.near` ([gridPlace]) within `options.range` uu, else onto the lattice: a
 * floor's grid start on the LATTICE and its plane on a HEIGHT_STEP; a ceiling's likewise,
 * its plane CEILING_ABOVE_STEP over a step; a wall's line on the LATTICE, its grid start
 * along it on the LATTICE and its grid bottom WALL_ABOVE_FLOOR over a HEIGHT_STEP.
 * `options.phase` ([x, y, z], default 0) shifts the lattice per axis. A piece with no
 * grid, or turned off the level's axes, does not snap (delta 0). */
export function latticeDelta (grids, matrix, options = {}) {
  const delta = [0, 0, 0]
  const hits = []
  const grid = mainGrid(grids)
  if (!grid || !quarterTurned(matrix)) return { delta, hits }
  const places = piecePlaces(grids, matrix)
  const me = gridPlace(grid, matrix, { column: places.some(p => p.column) })
  const joining = me.column ? places.filter(p => p.surface === 'Wall') : [me]
  const range = options.range ?? NEIGHBOUR_RANGE
  const phase = options.phase || [0, 0, 0]
  for (const axis of options.axes || [0, 1, 2]) {
    let best = null
    for (const n of options.near || []) {
      for (const mine of joining) {
        for (const move of joins(mine, n, axis, range)) {
          if (Math.abs(move) <= range && (!best || Math.abs(move) < Math.abs(best.move))) best = { move, box: n.box }
        }
      }
    }
    if (best) {
      delta[axis] = best.move
      hits.push(best.box)
      continue
    }
    const [value, step, offset] = snapped(me, axis)
    const base = offset + phase[axis]
    delta[axis] = Math.round((value - base) / step) * step + base - value
  }
  return { delta, hits }
}

/** [dx, dy, 0] that puts a piece with no trap grid on the lattice by the box around its
 * drawn parts (a Box3): its footprint's whole LATTICE steps on lattice lines, the few uu
 * a mesh overhangs them shared out on both sides. `phase` ([x, y, z], default 0) shifts
 * the lattice per axis. */
export function footprintDelta (box, phase = [0, 0, 0]) {
  const axis = (lo, hi, p) => {
    const size = hi - lo
    const over = (size - Math.max(1, Math.round(size / LATTICE)) * LATTICE) / 2
    const from = lo + over
    return Math.round((from - p) / LATTICE) * LATTICE + p - from
  }
  return [axis(box.min.x, box.max.x, phase[0]), axis(box.min.y, box.max.y, phase[1]), 0]
}

/** `v` modulo `step`, in [0, step). */
const wrap = (v, step) => ((v % step) + step) % step

/** The height a piece with grid place `place` (a gridPlace) stands at: a floor's or
 * ceiling's plane, a wall's grid bottom less its lift. */
export const standHeight = (place) =>
  place.surface === 'Wall' ? place.box.min.z - place.lift : place.foot

/** True when floor grids `a` and `b` (gridPlaces) chain into one room. */
function chained (a, b) {
  if (Math.abs(a.foot - b.foot) > ROOM_PLANE) return false
  const sep = [0, 1].map(k => Math.max(b.box.min.getComponent(k) - a.box.max.getComponent(k),
    a.box.min.getComponent(k) - b.box.max.getComponent(k)))
  const t = sep[0] >= sep[1] ? 0 : 1
  return Math.abs(sep[t]) <= ROOM_SEAM && -sep[1 - t] >= ROOM_RUN
}

/** A room's lattice phase [x, y, z] from its own grids (gridPlaces): on x and y the
 * value mod LATTICE that most of its grid edges lie within PHASE_TOLERANCE of; on z its
 * middle grid's plane mod HEIGHT_STEP. */
function roomPhase (grids) {
  const phase = [0, 1].map(axis => {
    const edges = grids.flatMap(g => [g.box.min.getComponent(axis), g.box.max.getComponent(axis)])
      .map(v => wrap(v, LATTICE))
    let best = 0
    let most = -1
    for (const c of edges) {
      const n = edges.filter(v => Math.abs(wrap(v - c + LATTICE / 2, LATTICE) - LATTICE / 2) <= PHASE_TOLERANCE).length
      if (n > most) { best = c; most = n }
    }
    return best
  })
  const planes = grids.map(g => g.foot).sort((a, b) => a - b)
  phase.push(wrap(planes[Math.floor(planes.length / 2)], HEIGHT_STEP))
  return phase
}

/** The rooms among grid places `places` (gridPlaces; only floors count): [{grids, phase}],
 * `phase` the room's lattice phase [x, y, z] for latticeDelta and footprintDelta. */
export function rooms (places) {
  const floors = places.filter(p => p.surface === 'Floor')
  const parent = floors.map((_, i) => i)
  const find = (i) => {
    while (parent[i] !== i) { parent[i] = parent[parent[i]]; i = parent[i] }
    return i
  }
  for (let i = 0; i < floors.length; i++) {
    for (let j = i + 1; j < floors.length; j++) {
      if (chained(floors[i], floors[j])) parent[find(i)] = find(j)
    }
  }
  const groups = new Map()
  floors.forEach((f, i) => {
    const r = find(i)
    if (!groups.has(r)) groups.set(r, [])
    groups.get(r).push(f)
  })
  return [...groups.values()].filter(g => g.length >= ROOM_GRIDS)
    .map(grids => ({ grids, phase: roomPhase(grids) }))
}

/** The room of `rooms` under or around a piece whose box (a Box3) is `box` and which
 * stands at `height`: of the rooms with a grid within `reach` uu of the box across the
 * level's plan and in height, the one nearest across the plan, then the one whose plane
 * is nearest `height`; null when none. */
export function roomAt (rooms, box, height, reach) {
  const gap = (b, k) => Math.max(0, b.min.getComponent(k) - box.max.getComponent(k),
    box.min.getComponent(k) - b.max.getComponent(k))
  let best = null
  for (const room of rooms) {
    for (const g of room.grids) {
      if (gap(g.box, 2) > reach) continue
      const plan = Math.hypot(gap(g.box, 0), gap(g.box, 1))
      if (plan > reach) continue
      const rise = Math.abs(g.foot - height)
      if (!best || plan < best.plan || (plan === best.plan && rise < best.rise)) best = { room, plan, rise }
    }
  }
  return best && best.room
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
