// Offline check of kitplace.js latticeDelta and footprintDelta, at every quarter turn:
// a lone floor's grid start lands on the 150 uu lattice and its plane on a multiple of 50;
// a lone wall's foot (actor origin) and grid start on the lattice and its grid bottom 20
// over a multiple of 50; a wall dropped near a floor stands on that floor's edge, its
// grid 20 over the floor's plane; a floor beside an off-lattice floor joins it edge to
// edge; a level piece's own phase keeps it on its own lattice; a turned piece is left alone;
// floor grids chained edge to edge make a room whose phase is measured from its grids, and
// a piece dropped under or around the room lands on that phase and plane.
// The kit placement census's rules (docs/research/kit-placement-census.md), each from the
// class geometry /api/kit serves (kitfit.py `fit`): a ceiling's plane 20 over a step; a
// plain wall stands on a floor with its grid's bottom and its face on the floor's edge,
// and puts its face on the lattice; the floor in front of a wall whose skirting falls short
// of its origin stops at the reach rounded down to 2.5; an upper floor's edge on the origin
// line of the wall under it; a column at a wall's end edge to edge with it, on a skirted
// wall's origin line or 50 in front of a plain wall's face; a grid-only wall (origin behind
// its face) puts its face on the lattice and takes no floor; a grid without fit data keeps
// the rules from before.
//   cd web/mapeditor && node test/kitplace_check.mjs
import * as THREE from 'three'
import { latticeDelta, footprintDelta, gridPlace, piecePlaces, rooms, roomAt, standHeight, LATTICE,
  HEIGHT_STEP, WALL_ABOVE_FLOOR, CEILING_ABOVE_STEP, NEIGHBOUR_RANGE, ROOM_PLANE, STOP_STEP,
  COLUMN_PROUD_PLAIN } from '../src/kitplace.js'

let failed = 0
const near = (got, want, what) => {
  if (Math.abs(got - want) > 1e-3) { failed++; console.log('FAIL', what, got, '!=', want) } else console.log('PASS', what, +got.toFixed(3))
}
const onStep = (v, step, offset, what) => {
  const k = (v - offset) / step
  near((k - Math.round(k)) * step, 0, `${what} (${+v.toFixed(3)})`)
}
// Grids and fit as /api/kit serves them (leveldata.kit_grids, kitfit.fits), 2026-11-05.
//: Wall_RB_MT_v1_1x4_C: skirted, plane 47.5 uu behind the origin, skirting reaching it
const wall = [{ surface: 'Wall', min: [0, -57.5, 20], max: [1200, -37.5, 470], normal: [0, 1, 0],
  fit: { below: 35.08, reach: 48.09, top: 531.45 } }]
//: the same grid without fit data: the rules from before fit data
const wallOld = [{ surface: 'Wall', min: [0, -57.5, 20], max: [1200, -37.5, 470], normal: [0, 1, 0] }]
//: Wall_RB_2x2_C: plain (nothing below its grid), its origin on its face
const plain = [{ surface: 'Wall', min: [0, -10, 0], max: [600, 10, 600], normal: [0, 1, 0],
  fit: { below: 0.01, reach: -1.45, top: 600.04 } }]
//: Wall_RB_3x1_C: plain, its origin 47.5 in front of its face
const plainAhead = [{ surface: 'Wall', min: [0, -57.5, 0], max: [300, -37.5, 900], normal: [0, 1, 0],
  fit: { below: 0, reach: -3.95, top: 900.01 } }]
//: Wall_RB_WDMTL_v1_1x2_C: skirted, its skirting reaching 39.33 of the origin's 47.5
const shortSkirt = [{ surface: 'Wall', min: [0, -57.5, 20], max: [600, -37.5, 470], normal: [0, 1, 0],
  fit: { below: 38.01, reach: 39.33, top: 516.55 } }]
//: Wall_RB_WDMT_v1_2x3_C: skirted, its skirting reaching 41.46 of the origin's 50
const shortSkirt2 = [{ surface: 'Wall', min: [0, -60, 20], max: [900, -40, 620], normal: [0, 1, 0],
  fit: { below: 38.52, reach: 41.46, top: 669.9 } }]
//: Wall_RB_MT_v2_2x3_C: skirted, mesh top 601.88 over its grid's bottom
const lowerWall = [{ surface: 'Wall', min: [0, -57.5, 20], max: [900, -37.5, 620], normal: [0, 1, 0],
  fit: { below: 35.08, reach: 48.15, top: 601.88 } }]
//: ArchwayGate_1x1x3_RB_C's first grid: its origin 5 behind its face
const archway = [{ surface: 'Wall', min: [-15, -350, 20], max: [5, -50, 470], normal: [-1, 0, 0],
  fit: { below: 70.99, reach: 0.27, top: 560.07 } }]
//: Column_Sec_1_4_RB_C: wall grids on four sides
const columnFit = { below: 0.94, reach: 5.04, top: 1200.03 }
const column4 = [
  { surface: 'Wall', min: [-10, -300, 0], max: [10, 0, 1200], normal: [-1, 0, 0], fit: columnFit },
  { surface: 'Wall', min: [300, -300, 0], max: [320, 0, 1200], normal: [1, 0, 0], fit: columnFit },
  { surface: 'Wall', min: [0, -310, 0], max: [300, -290, 1200], normal: [0, -1, 0], fit: columnFit },
  { surface: 'Wall', min: [0, -10, 0], max: [300, 10, 1200], normal: [0, 1, 0], fit: columnFit }]
//: Column_Sec_1_1_FB_C's grid: its plane through the origin
const column = [{ surface: 'Wall', min: [-10, -300, 20], max: [10, 0, 470], normal: [1, 0, 0] }]
//: Floor_RB_3x3_C's grid
const floor = [{ surface: 'Floor', min: [0, 0, -10], max: [900, 900, 10], normal: [0, 0, 1] }]
//: Ceiling_SB_2x1_C's grid
const ceiling = [{ surface: 'Ceiling', min: [0, 0, -90], max: [300, 600, -70], normal: [0, 0, -1] }]

const turn = (k) => new THREE.Matrix4().makeRotationZ(k * Math.PI / 2)
const at = (k, x, y, z) => turn(k).multiply(new THREE.Matrix4().makeTranslation(x, y, z))
const moved = (m, d) => m.clone().premultiply(new THREE.Matrix4().makeTranslation(...d))
const placeOf = (grids, m) => gridPlace(grids[0], m)

for (let k = 0; k < 4; k++) {
  // lone pieces onto the lattice
  for (const [name, grids] of [['wall', wall], ['wall without fit data', wallOld], ['column', column]]) {
    const m = at(k, 123.4, -77.7, 10)
    const p = placeOf(grids, moved(m, latticeDelta(grids, m).delta))
    const along = 1 - p.across
    onStep(p.foot, LATTICE, 0, `${name} turned ${k * 90}: foot on the lattice`)
    onStep(p.box.min.getComponent(along), LATTICE, 0, `${name} turned ${k * 90}: grid start on the lattice`)
    onStep(p.box.min.z, HEIGHT_STEP, WALL_ABOVE_FLOOR, `${name} turned ${k * 90}: grid bottom 20 over a height step`)
  }
  {
    const m = at(k, 123.4, -77.7, 1017)
    const p = placeOf(floor, moved(m, latticeDelta(floor, m).delta))
    onStep(p.box.min.x, LATTICE, 0, `floor turned ${k * 90}: x start on the lattice`)
    onStep(p.box.min.y, LATTICE, 0, `floor turned ${k * 90}: y start on the lattice`)
    onStep(p.foot, HEIGHT_STEP, 0, `floor turned ${k * 90}: plane on a height step`)
  }
  // a wall dropped near the floor in front of it: foot 20 off the floor's edge, bottom 7 over it
  {
    const f = gridPlace(floor[0], at(k, 0, 0, 1000))
    const m = at(k, 33, -20, 987)
    const got = latticeDelta(wall, m, { near: [f] })
    const p = placeOf(wall, moved(m, got.delta))
    const edge = p.sign > 0 ? f.box.min.getComponent(p.across) : f.box.max.getComponent(p.across)
    near(p.foot, edge, `wall turned ${k * 90}: foot on the floor's edge`)
    near(p.box.min.z, 1000 + WALL_ABOVE_FLOOR, `wall turned ${k * 90}: grid bottom 20 over the floor`)
    near(got.hits.length, 3, `wall turned ${k * 90}: joined the floor on three axes`)
  }
  // a floor dropped beside a wall: its facing edge onto the wall's foot
  {
    const w = gridPlace(wall[0], at(k, 0, -20, 1020))
    const m = at(k, 0, 15, 1003)
    const p = placeOf(floor, moved(m, latticeDelta(floor, m, { near: [w] }).delta))
    const edge = w.sign > 0 ? p.box.min.getComponent(w.across) : p.box.max.getComponent(w.across)
    near(edge, w.foot, `floor turned ${k * 90}: edge on the wall's foot`)
  }
}

// The census's rules, at every quarter turn.
//: `place`'s across coordinate of a floor's edge facing it
const facing = (w, f) => w.sign > 0 ? f.box.min.getComponent(w.across) : f.box.max.getComponent(w.across)
//: a piece of `grids` dropped at [x, y, z] turned k quarter turns, snapped against `near`
const drop = (grids, k, x, y, z, near = []) => {
  const m = at(k, x, y, z)
  return moved(m, latticeDelta(grids, m, { near }).delta)
}
for (let k = 0; k < 4; k++) {
  const turned = `turned ${k * 90}`
  // a ceiling's plane 20 over a height step
  {
    const p = placeOf(ceiling, drop(ceiling, k, 123.4, -77.7, 1017))
    onStep(p.foot, HEIGHT_STEP, CEILING_ABOVE_STEP, `ceiling ${turned}: plane 20 over a height step`)
  }
  // lone walls: a plain wall's face on the lattice, a skirted wall's origin
  {
    const p = placeOf(plainAhead, drop(plainAhead, k, 123.4, -77.7, 10))
    onStep(p.plane, LATTICE, 0, `plain wall with its origin ahead ${turned}: face on the lattice`)
    const off = ((p.foot % LATTICE) + LATTICE) % LATTICE
    near(Math.min(off, LATTICE - off), 47.5,
      `plain wall with its origin ahead ${turned}: origin 47.5 off the lattice`)
    const s = placeOf(wall, drop(wall, k, 123.4, -77.7, 10))
    onStep(s.foot, LATTICE, 0, `skirted wall ${turned}: origin on the lattice`)
  }
  // a floor under foot: a plain wall stands on it with its grid's bottom, its face on the
  // edge; a skirted wall 20 over it, its origin on the edge
  const f = gridPlace(floor[0], at(k, 0, 0, 1000))
  for (const [name, grids, lift, line] of [['plain wall', plain, 0, 'plane'], ['skirted wall', wall, WALL_ABOVE_FLOOR, 'foot']]) {
    const p = placeOf(grids, drop(grids, k, 33, -20, 1000 + lift + 9, [f]))
    near(p.box.min.z, 1000 + lift, `${name} ${turned}: grid bottom ${lift} over the floor`)
    near(p[line], facing(p, f), `${name} ${turned}: ${line === 'plane' ? 'face' : 'origin'} on the floor's edge`)
  }
  // a plain wall over a floor running under it stands on it with its grid's bottom
  {
    const p = placeOf(plain, drop(plain, k, 213.3, 450, 1000 + 11, [f]))
    near(p.box.min.z, 1000, `plain wall ${turned} on a floor running under it: grid bottom on the floor`)
  }
  // the floor in front of a wall stops at the wall's stop
  //: a wall grid's plane in its actor's space
  const w0 = (grids) => (grids[0].min[1] + grids[0].max[1]) / 2
  for (const [name, grids, from] of [['Wall_RB_WDMTL_v1_1x2 (reach 39.33)', shortSkirt, 37.5],
    ['Wall_RB_WDMT_v1_2x3 (reach 41.46)', shortSkirt2, 40], ['Wall_RB_MT_v1_1x4 (reach 48.09)', wall, 47.5],
    ['Wall_RB_2x2 (plain)', plain, 0]]) {
    const w = placeOf(grids, at(k, 0, 0, 1000))
    const p = placeOf(floor, drop(floor, k, 0, w0(grids) + from - 17, 1000 - w.lift, [w]))
    near((facing(w, p) - w.plane) * w.sign, from, `floor in front of ${name} ${turned}: stops ${from} from its face`)
  }
  near((STOP_STEP * Math.floor(39.5 / STOP_STEP)), 37.5, `stop step ${STOP_STEP}: 39.5 rounds down to 37.5`)
  // an upper floor behind a wall, over its top: its edge on the wall's origin line
  {
    const w = placeOf(lowerWall, at(k, 0, 0, 1000))
    const top = 1000 + 20 + 601.88
    const m = at(k, 300, -900 - 15, Math.ceil(top / HEIGHT_STEP) * HEIGHT_STEP)
    const p = placeOf(floor, moved(m, latticeDelta(floor, m, { near: [w] }).delta))
    const edge = w.sign > 0 ? p.box.max.getComponent(w.across) : p.box.min.getComponent(w.across)
    near(edge, w.foot, `upper floor over Wall_RB_MT_v2_2x3 ${turned}: edge on its origin line`)
    // and a wall moved under the floor puts its origin line on the floor's edge
    const wm = at(k, 0, 12, 1000)
    const q = placeOf(lowerWall, moved(wm, latticeDelta(lowerWall, wm, { near: [placeOf(floor, at(k, 300, -900, Math.ceil(top / HEIGHT_STEP) * HEIGHT_STEP))] }).delta))
    near(q.foot, 0, `Wall_RB_MT_v2_2x3 ${turned} under an upper floor: origin line on its edge`)
  }
  // a column at a wall's end: edge to edge with the wall's grid, on a skirted wall's
  // origin line or COLUMN_PROUD_PLAIN in front of a plain wall's face
  for (const [name, grids, ahead, width] of [['skirted wall', wall, 0, 1200], ['plain wall', plain, COLUMN_PROUD_PLAIN, 600]]) {
    const w = placeOf(grids, at(k, 0, 0, 1000))
    const ns = piecePlaces(grids, at(k, 0, 0, 1000))
    const m = at(k, width + 17, ahead - 12, 1000)
    const got = latticeDelta(column4, m, { near: ns })
    const front = piecePlaces(column4, moved(m, got.delta)).find(p => p.across === w.across && p.sign === w.sign)
    near(front.column ? 1 : 0, 1, `${name} ${turned}: the column piece is a column`)
    near((front.plane - w.foot) * w.sign, ahead, `column at a ${name}'s end ${turned}: grid ${ahead} in front of its origin line`)
    const along = 1 - w.across
    const end = Math.abs(front.box.min.getComponent(along) - w.box.max.getComponent(along)) < 1e-3 ||
      Math.abs(front.box.max.getComponent(along) - w.box.min.getComponent(along)) < 1e-3
    near(end ? 1 : 0, 1, `column at a ${name}'s end ${turned}: grid edge to edge with the wall's`)
  }
  // a grid-only wall: its face on the lattice; a floor in front does not stop at it
  {
    const p = placeOf(archway, drop(archway, k, 123.4, -77.7, 10))
    near(p.gridOnly ? 1 : 0, 1, `archway ${turned}: grid only (origin behind its face)`)
    onStep(p.plane, LATTICE, 0, `archway ${turned}: face on the lattice`)
    const a = placeOf(archway, at(k, 0, 0, 1000))
    const m = at(k, -905 + 17, -200, 1000)
    const with_ = latticeDelta(floor, m, { near: [a] }).delta
    const alone = latticeDelta(floor, m).delta
    near(Math.max(...with_.map((d, i) => Math.abs(d - alone[i]))), 0, `floor in front of an archway ${turned}: no stop`)
  }
  // a wall without fit data keeps the rules from before: 20 over the floor, origin on its edge
  {
    const p = placeOf(wallOld, drop(wallOld, k, 33, -20, 1009, [f]))
    near(p.box.min.z, 1000 + WALL_ABOVE_FLOOR, `wall without fit data ${turned}: grid bottom 20 over the floor`)
    near(p.foot, facing(p, f), `wall without fit data ${turned}: origin on the floor's edge`)
  }
}

// a floor beside an off-lattice floor (phase 227.9, height 2018.2) joins it edge to edge
{
  const n = gridPlace(floor[0], new THREE.Matrix4().makeTranslation(227.9, 216, 2018.2))
  const m = new THREE.Matrix4().makeTranslation(227.9 + 900 + 20, 216 + 12, 2030)
  const p = placeOf(floor, moved(m, latticeDelta(floor, m, { near: [n] }).delta))
  near(p.box.min.x, n.box.max.x, 'beside an off-lattice floor: edge to edge')
  near(p.box.min.y, n.box.min.y, 'beside an off-lattice floor: lined up')
  near(p.foot, n.foot, 'beside an off-lattice floor: coplanar')
}
// a level piece's own phase keeps it on its own lattice
{
  const m = new THREE.Matrix4().makeTranslation(227.9, 216, 2018.2)
  const phase = latticeDelta(floor, m).delta.map(d => -d)
  const dragged = moved(m, [160, 0, 0])
  const p = placeOf(floor, moved(dragged, latticeDelta(floor, dragged, { axes: [0], phase }).delta))
  near(p.box.min.x, 227.9 + LATTICE, 'own phase: one lattice step on')
  near(p.box.min.y, 216, 'own phase: other axes untouched')
}
// a piece turned off the level's axes does not snap
{
  const m = new THREE.Matrix4().makeRotationZ(Math.PI / 4).setPosition(123.4, -77.7, 10)
  near(Math.max(...latticeDelta(floor, m).delta.map(Math.abs)), 0, 'turned 45 degrees: left alone')
}
// a piece with no grid: its footprint's whole lattice steps on lattice lines, the overhang shared
{
  const box = new THREE.Box3(new THREE.Vector3(37, -260, 5), new THREE.Vector3(327, 50, 90))
  const d = footprintDelta(box)
  near(box.min.x + d[0], 5, 'footprint: 290 uu centred on two lattice steps')
  near(box.min.y + d[1], -305, 'footprint: 310 uu centred on two lattice steps')
  near(d[2], 0, 'footprint: height untouched')
}
// rooms: three floors chained edge to edge off the world lattice
const ROOM_AT = [227.9, 216.4, 2018.2]
//: Around a piece means within this many uu (main.js LATTICE_REACH, four trap cells).
const REACH = 1200
const wrap = (v, step) => ((v % step) + step) % step
const roomFloors = [0, 1, 2].map(i => gridPlace(floor[0],
  new THREE.Matrix4().makeTranslation(ROOM_AT[0] + 900 * i, ROOM_AT[1], ROOM_AT[2])))
{
  const found = rooms(roomFloors)
  near(found.length, 1, 'room: three chained floors make one room')
  const r = found[0]
  near(r.phase[0], wrap(ROOM_AT[0], LATTICE), 'room: x phase from its grids')
  near(r.phase[1], wrap(ROOM_AT[1], LATTICE), 'room: y phase from its grids')
  near(r.phase[2], wrap(ROOM_AT[2], HEIGHT_STEP), 'room: z phase from its plane')
  near(rooms(roomFloors.slice(0, 2)).length, 0, 'room: two chained floors are not a room')
  const gapped = roomFloors.map((f, i) => gridPlace(floor[0],
    new THREE.Matrix4().makeTranslation(ROOM_AT[0] + (900 + 2) * i, ROOM_AT[1], ROOM_AT[2])))
  near(rooms(gapped).length, 0, 'room: floors 2 uu apart do not chain')
  const stepped = roomFloors.map((f, i) => gridPlace(floor[0],
    new THREE.Matrix4().makeTranslation(ROOM_AT[0] + 900 * i, ROOM_AT[1], ROOM_AT[2] + (ROOM_PLANE + 1) * i)))
  near(rooms(stepped).length, 0, `room: floors ${ROOM_PLANE + 1} uu apart in height do not chain`)
  // a floor dropped beside the room, clear of its edges: the room's phase and plane
  const m = new THREE.Matrix4().makeTranslation(ROOM_AT[0] + 2700 + 313.3, ROOM_AT[1] + 451.7, ROOM_AT[2] + 9)
  const place = gridPlace(floor[0], m)
  const room = roomAt(found, place.box, standHeight(place), REACH)
  near(room === r ? 1 : 0, 1, 'room: found around a floor dropped 313 uu off its edge')
  const p = placeOf(floor, moved(m, latticeDelta(floor, m, { near: roomFloors, phase: r.phase }).delta))
  onStep(p.box.min.x, LATTICE, r.phase[0], 'room: dropped floor x start on the room lattice')
  onStep(p.box.min.y, LATTICE, r.phase[1], 'room: dropped floor y start on the room lattice')
  near(p.foot, ROOM_AT[2], 'room: dropped floor on the room\'s plane')
  near(Math.abs(p.box.min.x - (ROOM_AT[0] + 2700)) > NEIGHBOUR_RANGE ? 1 : 0, 1,
    'room: dropped floor clear of the room\'s edge (no neighbour join)')
  // a wall dropped inside the room: foot on the room lattice, grid 20 over its plane
  const wm = new THREE.Matrix4().makeTranslation(ROOM_AT[0] + 413.3, ROOM_AT[1] + 371.1, ROOM_AT[2] + 5)
  const wp = gridPlace(wall[0], wm)
  near(roomAt(found, wp.box, standHeight(wp), REACH) === r ? 1 : 0, 1, 'room: found under a wall dropped in it')
  const w = placeOf(wall, moved(wm, latticeDelta(wall, wm, { phase: r.phase }).delta))
  onStep(w.foot, LATTICE, r.phase[1], 'room: wall foot on the room lattice')
  onStep(w.box.min.x, LATTICE, r.phase[0], 'room: wall grid start on the room lattice')
  near(w.box.min.z, ROOM_AT[2] + WALL_ABOVE_FLOOR, 'room: wall grid bottom 20 over the room\'s plane')
  // nothing within reach: no room
  const far = gridPlace(floor[0], new THREE.Matrix4().makeTranslation(ROOM_AT[0] + 2700 + REACH + 1, ROOM_AT[1], ROOM_AT[2]))
  near(roomAt(found, far.box, far.foot, REACH) === null ? 1 : 0, 1, 'room: none beyond reach')
  // two rooms stacked: the one nearest in height
  const upper = rooms(roomFloors.map(f => gridPlace(floor[0], new THREE.Matrix4().makeTranslation(
    f.box.min.x + 40, f.box.min.y + 40, f.foot + 600))))
  const both = found.concat(upper)
  near(roomAt(both, place.box, ROOM_AT[2] + 590, REACH) === upper[0] ? 1 : 0, 1, 'room: the stacked room nearest in height')
  // a footprint on the room's phase
  const box = new THREE.Box3(new THREE.Vector3(ROOM_AT[0] + 37, ROOM_AT[1] - 260, 5), new THREE.Vector3(ROOM_AT[0] + 327, ROOM_AT[1] + 50, 90))
  const d = footprintDelta(box, r.phase)
  onStep(box.min.x + d[0] + (290 - 2 * LATTICE) / 2, LATTICE, r.phase[0], 'room: footprint x on the room lattice')
  onStep(box.min.y + d[1] + (310 - 2 * LATTICE) / 2, LATTICE, r.phase[1], 'room: footprint y on the room lattice')
}
console.log(failed ? `${failed} failed` : 'all passed')
process.exit(failed ? 1 : 0)
