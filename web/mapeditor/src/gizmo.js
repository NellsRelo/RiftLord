// The move/turn/size gizmo. TransformControls cannot drive an object under a MIRRORED
// parent: `world` turns Unreal's left-handed z-up space into three.js's (rotation x
// -90 degrees, scale y -1), and decomposing a mirrored world matrix puts the mirror on
// x, so the handles were drawn along axes the object does not have. Dragged along an
// arrow, a turned piece went the other way at 90 degrees and off at any other angle.
//
// So the controls drive a PROXY in plain scene space instead, holding the target's
// world transform with one local axis flipped (right-handed again), and every change
// is written back to the target through its parent. The arrows lie exactly along the
// target's own axes (x drawn reversed) and a drag follows the arrow.
//
// Translation snaps here, in steps of `snapStep()` uu (0 for none) counted from where
// the drag began, so a piece already on the grid stays on it. A turn snaps to
// `rotateStep()` radians (0 for none), counted from where the drag began. A resize hands
// the proxy's scale to `scaleStep(scale, startScale)`, which may change it in place.

import * as THREE from 'three'
import { TransformControls } from 'three/addons/controls/TransformControls.js'

//: Flips one local axis: a mirrored frame times this is a proper rotation and scale.
const FLIP = new THREE.Matrix4().makeScale(-1, 1, 1)

class Proxy extends THREE.Object3D {
  constructor (owner) {
    super()
    this.owner = owner
  }

  //: TransformControls reads the object here on every frame and when a drag begins, so
  //: the proxy picks up any change made to the target elsewhere (a typed position, undo).
  updateMatrixWorld (force) {
    if (!this.owner.controls.dragging) this.owner.pull()
    super.updateMatrixWorld(force)
  }
}

export class Gizmo {
  /** `snapStep()` is the translation step in uu, or 0 for none. `options.rotateStep()` is
   * the turn step in radians, or 0 for none; `options.scaleStep(scale, startScale)` snaps
   * a resize (Vector3s, changed in place), or is absent for none. */
  constructor (camera, dom, scene, snapStep = () => 0, options = {}) {
    this.controls = new TransformControls(camera, dom)
    this.proxy = new Proxy(this)
    this.proxy.name = 'gizmo-proxy'
    scene.add(this.proxy)
    this.target = null
    this.snapStep = snapStep
    this.rotateStep = options.rotateStep || (() => 0)
    this.scaleStep = options.scaleStep || null
    this.start = null
    //: TransformControls reads `rotationSnap` on every pointer move, so it is set from
    //: `rotateStep()` just before the controls hear the event (capture on the window)
    const setTurnSnap = () => { this.controls.rotationSnap = this.rotateStep() || null }
    if (typeof window !== 'undefined') {
      for (const type of ['pointerdown', 'pointermove']) window.addEventListener(type, setTurnSnap, true)
    }
    this.controls.addEventListener('mouseDown', () => {
      this.start = { pos: this.proxy.position.clone(), quat: this.proxy.quaternion.clone(),
                     scale: this.proxy.scale.clone() }
    })
    this.controls.addEventListener('mouseUp', () => { this.start = null })
    //: registered first, so the target has moved before any page listener hears of it
    this.controls.addEventListener('objectChange', () => {
      this.snap()
      this.push()
    })
  }

  get object () { return this.target || undefined }
  get mode () { return this.controls.mode }
  get axis () { return this.controls.axis }
  get dragging () { return this.controls.dragging }
  get space () { return this.controls.space }
  setMode (mode) { this.controls.setMode(mode) }
  setSpace (space) { this.controls.setSpace(space) }
  getHelper () { return this.controls.getHelper() }
  addEventListener (type, fn) { this.controls.addEventListener(type, fn) }
  removeEventListener (type, fn) { this.controls.removeEventListener(type, fn) }

  attach (object) {
    this.target = object
    this.pull()
    this.controls.attach(this.proxy)
  }

  detach () {
    this.target = null
    this.controls.detach()
  }

  /** The proxy takes the target's world transform, one axis flipped. */
  pull () {
    const t = this.target
    if (!t) return
    t.updateWorldMatrix(true, false)
    const m = t.matrixWorld.clone().multiply(FLIP)
    m.decompose(this.proxy.position, this.proxy.quaternion, this.proxy.scale)
    this.proxy.updateMatrix()
  }

  /** The target takes the proxy's transform, through its own parent. */
  push () {
    const t = this.target
    if (!t) return
    this.proxy.updateMatrix()
    const local = this.proxy.matrix.clone().multiply(FLIP)
    if (t.parent) {
      t.parent.updateWorldMatrix(true, false)
      local.premultiply(t.parent.matrixWorld.clone().invert())
    }
    local.decompose(t.position, t.quaternion, t.scale)
    t.updateMatrix()
  }

  /** Snap a move to whole steps from where the drag began, along the gizmo's axes, and
   * hand a resize to `scaleStep`. */
  snap () {
    if (!this.start) return
    if (this.controls.mode === 'scale') {
      if (this.scaleStep) this.scaleStep(this.proxy.scale, this.start.scale)
      return
    }
    const step = this.controls.mode === 'translate' ? this.snapStep() : 0
    if (!step) return
    const local = this.controls.space === 'local'
    const d = this.proxy.position.clone().sub(this.start.pos)
    if (local) d.applyQuaternion(this.start.quat.clone().invert())
    d.set(Math.round(d.x / step) * step, Math.round(d.y / step) * step,
          Math.round(d.z / step) * step)
    if (local) d.applyQuaternion(this.start.quat)
    this.proxy.position.copy(this.start.pos).add(d)
  }
}
