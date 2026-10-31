// Marquee selection in the 3D view: a left-button drag draws a rectangle and selects
// what is inside it. The left button has no camera binding (orbit is the right button,
// dolly the middle), so any left drag that does not start on a gizmo handle is a
// marquee. Shift adds to the selection; Ctrl or Alt removes from it.

import * as THREE from 'three'

//: How far the pointer must travel, in screen pixels, before a press becomes a drag.
//: The click handler uses the same distance to tell a click from a drag.
export const DRAG_MIN = 4

/** {replace|add|remove} from a pointer event's modifier keys. */
export const marqueeMode = (ev) =>
  ev.shiftKey ? 'add' : (ev.ctrlKey || ev.altKey || ev.metaKey) ? 'remove' : 'replace'

/** Draw marquees on `dom`. `enabled()` gates a press; `gizmo` is the TransformControls
 * whose handles keep their drags; `onSelect(rect, mode)` gets the client-space rect. */
export function useMarquee ({ dom, gizmo, enabled, onSelect }) {
  const style = document.createElement('style')
  style.textContent =
    '.marquee{position:fixed;pointer-events:none;z-index:50;' +
    'border:1px dashed #53e0a0;background:rgba(83,224,160,.10)}' +
    '.marquee[data-mode=remove]{border-color:#e06a53;background:rgba(224,106,83,.10)}'
  document.head.appendChild(style)
  const box = document.createElement('div')
  box.className = 'marquee'
  box.hidden = true
  document.body.appendChild(box)
  let from = null
  let live = false
  const rectOf = (ev) => ({
    left: Math.min(from[0], ev.clientX), right: Math.max(from[0], ev.clientX),
    top: Math.min(from[1], ev.clientY), bottom: Math.max(from[1], ev.clientY),
  })
  dom.addEventListener('pointerdown', (ev) => {
    if (ev.button !== 0 || !enabled() || gizmo.axis || gizmo.dragging) return
    from = [ev.clientX, ev.clientY]
    live = false
  })
  dom.addEventListener('pointermove', (ev) => {
    if (!from) return
    if (!(ev.buttons & 1) || gizmo.dragging) { from = null; box.hidden = true; return }
    if (!live && Math.hypot(ev.clientX - from[0], ev.clientY - from[1]) <= DRAG_MIN) return
    if (!live) {
      live = true
      try { dom.setPointerCapture(ev.pointerId) } catch (_e) { /* not capturable */ }
    }
    const r = rectOf(ev)
    Object.assign(box.style, { left: r.left + 'px', top: r.top + 'px',
                               width: (r.right - r.left) + 'px',
                               height: (r.bottom - r.top) + 'px' })
    box.dataset.mode = marqueeMode(ev)
    box.hidden = false
  })
  const finish = (ev) => {
    if (!from) return
    const was = live
    const r = was ? rectOf(ev) : null
    from = null
    live = false
    box.hidden = true
    if (was) onSelect(r, marqueeMode(ev))
  }
  dom.addEventListener('pointerup', finish)
  dom.addEventListener('pointercancel', () => { from = null; live = false; box.hidden = true })
}

/** Whether Unreal-space point `at`, under `world`, projects inside client rect `r`. */
export function inRect (at, world, camera, canvasRect, r) {
  const v = world.localToWorld(new THREE.Vector3(at[0], at[1], at[2]))
  const view = v.clone().applyMatrix4(camera.matrixWorldInverse)
  if (view.z >= 0) return false
  v.project(camera)
  const x = canvasRect.left + (v.x + 1) / 2 * canvasRect.width
  const y = canvasRect.top + (1 - v.y) / 2 * canvasRect.height
  return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom
}
