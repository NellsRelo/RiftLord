// Offline check of kitplace.js snapGrid: a wall's actor origin lands on a lattice line across
// the wall and its grid's start on a line along it, at every quarter turn, as shipped levels
// place Wall_FB_MT_*_SAND (grid plane 47.5 uu in front of the origin); a column whose grid
// plane is its origin lands the same as before.
//   cd web/mapeditor && node test/kitplace_check.mjs
import * as THREE from 'three'
import { snapGrid } from '../src/kitplace.js'

let failed = 0
const onLine = (v, what) => {
  const off = Math.abs(v / 300 - Math.round(v / 300)) * 300
  if (off > 1e-3) { failed++; console.log('FAIL', what, v) } else console.log('PASS', what, v)
}
//: Wall_FB_MT_1x2_SAND_C's grid (leveldata.kit_parts)
const wall = [{ surface: 'Wall', min: [0, -57.5, 20], max: [600, -37.5, 470], normal: [0, 1, 0] }]
//: Column_Sec_1_1_FB_C's grid: its plane through the origin
const column = [{ surface: 'Wall', min: [-10, -300, 20], max: [10, 0, 470], normal: [1, 0, 0] }]
const lattice = { grid: 300, ground: 0 }
for (const [name, grids, along] of [['wall', wall, 0], ['column', column, 1]]) {
  for (let k = 0; k < 4; k++) {
    const m = new THREE.Matrix4().makeRotationZ(k * Math.PI / 2).setPosition(123.4, -77.7, 10)
    const d = snapGrid(grids, m, lattice)
    const moved = m.clone().premultiply(new THREE.Matrix4().makeTranslation(d[0], d[1], d[2]))
    const e = moved.elements
    const n = new THREE.Vector3(...grids[0].normal).transformDirection(moved)
    const across = Math.abs(n.x) >= Math.abs(n.y) ? 0 : 1
    onLine(e[12 + across], `${name} turned ${k * 90}: origin across the wall`)
    const box = new THREE.Box3(new THREE.Vector3(...grids[0].min), new THREE.Vector3(...grids[0].max))
      .applyMatrix4(moved)
    onLine(1 - across === 0 ? box.min.x : box.min.y, `${name} turned ${k * 90}: grid start along the wall`)
  }
}
console.log(failed ? `${failed} failed` : 'all passed')
process.exit(failed ? 1 : 0)
