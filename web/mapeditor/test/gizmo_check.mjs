// Offline check of gizmo.js: the proxy's axes lie along the target's own axes under
// the Map Editor's mirrored `world` group, a move along the proxy's x moves the target
// along its own x, and pull/push round-trips.
//   cd riftlord/web && node test/gizmo_check.mjs
import * as THREE from 'three'
import { Gizmo } from '../src/gizmo.js'

const dom = { addEventListener () {}, removeEventListener () {}, style: {},
              ownerDocument: { addEventListener () {}, removeEventListener () {} },
              setPointerCapture () {}, releasePointerCapture () {} }
const scene = new THREE.Scene()
const world = new THREE.Group()
world.rotation.x = -Math.PI / 2
world.scale.set(1, -1, 1)
scene.add(world)
const camera = new THREE.PerspectiveCamera()
const gizmo = new Gizmo(camera, dom, scene, () => 0)

let failed = 0
const near = (a, b, what) => {
  const ok = Math.abs(a - b) < 1e-4
  if (!ok) { failed++; console.log('FAIL', what, a, b) }
}

for (const yawDeg of [0, 30, 90, 137, 180, -60]) {
  const marker = new THREE.Object3D()
  marker.position.set(1000, -2000, 300)
  marker.rotation.z = THREE.MathUtils.degToRad(yawDeg)
  marker.scale.set(1.5, 2, 1)
  world.add(marker)
  gizmo.attach(marker)
  // the proxy's x axis, in scene space, is the target's x axis reversed; y and z match
  const pm = gizmo.proxy.matrix
  marker.updateWorldMatrix(true, false)
  const tm = marker.matrixWorld
  for (let c = 0; c < 3; c++) {
    const sign = c === 0 ? -1 : 1
    for (let r = 0; r < 3; r++) near(pm.elements[c * 4 + r], sign * tm.elements[c * 4 + r], `yaw ${yawDeg} axis ${c}`)
  }
  // a move of 100 along the proxy's local -x (the arrow drawn as the target's +x)
  const before = marker.position.clone()
  const along = new THREE.Vector3(-100, 0, 0).applyQuaternion(gizmo.proxy.quaternion)
  gizmo.proxy.position.add(along)
  gizmo.push()
  const moved = marker.position.clone().sub(before)
  const want = new THREE.Vector3(1, 0, 0).applyQuaternion(marker.quaternion).multiplyScalar(100)
  near(moved.x, want.x, `yaw ${yawDeg} move x`)
  near(moved.y, want.y, `yaw ${yawDeg} move y`)
  near(moved.z, want.z, `yaw ${yawDeg} move z`)
  near(THREE.MathUtils.radToDeg(marker.rotation.z), yawDeg, `yaw ${yawDeg} kept`)
  near(marker.scale.x, 1.5, `yaw ${yawDeg} scale x kept`)
  // round trip
  const was = marker.matrix.clone()
  gizmo.pull()
  gizmo.push()
  for (let i = 0; i < 16; i++) near(marker.matrix.elements[i], was.elements[i], `yaw ${yawDeg} round trip ${i}`)
  world.remove(marker)
}
console.log(failed ? `${failed} check(s) failed` : 'gizmo: all checks passed')
process.exit(failed ? 1 : 0)
