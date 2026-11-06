// The selected sun's direction handle: an arrow from a ball on a sphere around where the
// camera looks, pointing the way the sun shines. Dragging the ball over the sphere turns
// the sun; the drag ends in one change (`onTurn(rotator)`). A moon's handle is the same
// in its own colour, its arrow from the moon toward the level (moonsui.handleRotator).
//
// It lives in the level group (`world`, Unreal's space under three.js's), so its points
// are level coordinates.

import * as THREE from 'three'
import { forward, rotatorOf } from './lightingui.js'

//: The sphere the ball moves on, in uu around where the camera looks.
const RADIUS = 2500
//: The ball's radius, in uu.
const BALL = 140
const COLOUR = 0xffd24a
//: A moon's handle.
export const MOON_COLOUR = 0x9fc8ff

export class SunHandle {
  /** `world`: the level group; `camera`, `dom`: the view; `controls`: the orbit controls,
   * paused while the ball is dragged; `onTurn(rotator)`: the drag's end; `colour`: the
   * handle's. */
  constructor (world, camera, dom, controls, onTurn, colour = COLOUR) {
    this.world = world
    this.camera = camera
    this.dom = dom
    this.controls = controls
    this.onTurn = onTurn
    this.group = new THREE.Group()
    this.group.visible = false
    this.ball = new THREE.Mesh(new THREE.SphereGeometry(BALL, 16, 12),
      new THREE.MeshBasicMaterial({ color: colour, fog: false, depthTest: false }))
    this.ball.renderOrder = 999
    this.arrow = new THREE.ArrowHelper(new THREE.Vector3(1, 0, 0), new THREE.Vector3(), RADIUS,
      colour, RADIUS * 0.12, RADIUS * 0.06)
    for (const m of [this.arrow.line.material, this.arrow.cone.material]) {
      m.depthTest = false
      m.fog = false
    }
    this.arrow.renderOrder = 999
    this.group.add(this.ball, this.arrow)
    world.add(this.group)
    this.rot = [0, 0, 0]
    this.centre = new THREE.Vector3()
    this.dragging = false
    this.ray = new THREE.Raycaster()
    dom.addEventListener('pointerdown', (ev) => this.down(ev), true)
    //: the click that ends a drag of the ball picks nothing
    dom.addEventListener('click', (ev) => {
      if (!this.ate) return
      this.ate = false
      ev.stopImmediatePropagation()
    }, true)
    addEventListener('pointermove', (ev) => this.move(ev))
    addEventListener('pointerup', () => this.up())
  }

  /** Show the handle for a sun pointing along rotator `rot`, or hide it with null. */
  show (rot) {
    this.group.visible = !!rot
    if (rot && !this.dragging) this.rot = rot.slice()
    this.place()
  }

  /** Keep the handle around `centre` (level space). */
  follow (centre) {
    if (!this.group.visible) return
    this.centre.copy(centre)
    this.place()
  }

  place () {
    const d = forward(this.rot)
    const dir = new THREE.Vector3(d[0], d[1], d[2])
    const from = this.centre.clone().addScaledVector(dir, -RADIUS)
    this.ball.position.copy(from)
    this.arrow.position.copy(from)
    this.arrow.setDirection(dir)
  }

  pointer (ev) {
    const r = this.dom.getBoundingClientRect()
    return new THREE.Vector2(((ev.clientX - r.left) / r.width) * 2 - 1,
      -((ev.clientY - r.top) / r.height) * 2 + 1)
  }

  down (ev) {
    if (!this.group.visible || ev.button !== 0) return
    this.ray.setFromCamera(this.pointer(ev), this.camera)
    if (!this.ray.intersectObject(this.ball, false).length) return
    ev.stopImmediatePropagation()
    ev.preventDefault()
    this.ate = true
    this.dragging = true
    this.controls.enabled = false
  }

  move (ev) {
    if (!this.dragging) return
    this.ray.setFromCamera(this.pointer(ev), this.camera)
    //: the ray in level space, met with the sphere around the centre
    const inv = this.world.matrixWorld.clone().invert()
    const o = this.ray.ray.origin.clone().applyMatrix4(inv)
    const far = this.ray.ray.origin.clone().add(this.ray.ray.direction).applyMatrix4(inv)
    const dir = far.sub(o).normalize()
    const oc = o.clone().sub(this.centre)
    const b = oc.dot(dir)
    const c = oc.lengthSq() - RADIUS * RADIUS
    const h = b * b - c
    const t = h >= 0 ? -b - Math.sqrt(h) : -b
    const hit = o.addScaledVector(dir, t)
    const shine = this.centre.clone().sub(hit)
    this.rot = rotatorOf([shine.x, shine.y, shine.z], this.rot[2])
    this.place()
  }

  up () {
    if (!this.dragging) return
    this.dragging = false
    this.controls.enabled = true
    this.onTurn(this.rot.slice())
  }
}
