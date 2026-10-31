// Rift Lord: add spawners, doors and AI routes to a level OMD3 already ships.
//
// The level is drawn from its own packages -- Split Stairs is 1,256 placements of
// 106 distinct meshes -- with its existing spawners, doors and routes over the top,
// so new content can be put somewhere real instead of at a guessed coordinate.
//
// Nothing here edits a level. The tool holds a PLAN, saved as its own file, and an
// emitter applies that plan to a copy. A change buried in a 3,884-export package
// cannot be reviewed; a plan can be read, diffed and kept.
//
// Unreal is Z-up and three.js is Y-up, so the scene is rotated once at the root and
// nothing below swizzles. An Unreal rotator is not a three.js Euler either: Unreal
// rolls about X, pitches about Y, yaws about Z, and applies them in that order.

import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { Gizmo } from './gizmo.js'
import { mount, unmount } from 'svelte'
// the mission Details dialog Rift Lord's pages share
import MissionDetails from '../../shared/MissionDetails.svelte'
import { sendable } from '../../shared/LoadPicture.svelte'
import { mapSettingsHtml, wireMapSettings, newMissionWorld } from './mapsettings.js'
import { levelFxHtml, wireLevelFx, removable, isWeatherItem } from './levelfxui.js'
import * as scratchUi from './scratch.js'
import { kitGrids, placeMatrix, snapGrid, cachedGrids } from './kitplace.js'
import { TrapGridLayer, trapGridHtml, SYNC_INTERVAL as TRAP_GRID_SYNC } from './trapgrids.js'
import * as fab from './fabui.js'
import { minimapOffered, minimapHtml, wireMinimap, previewCache, minimapPreviewed } from './minimapui.js'
import { createTrace } from './traceui.js'
import { levelPicker } from './levelpicker.js'
import { proposal as reconnectProposal, applyProposal, reconnectHtml,
         wireReconnect, routes as levelRoutes } from './reconnect.js'
import { useMarquee, inRect } from './marquee.js'
import { useVolumes, drawVolumes, applyVolumes, recordVolumeMove, removeVolume,
         showVolumeProps, addVolume } from './volumes.js'
import { installHeightFog, setHeightFog, updateHeightFog } from './heightfog.js'
import { eventsHtml, wireEvents, needsPiece } from './eventsui.js'
import { CRYSTAL_KIND, CRYSTAL_CLASS, CRYSTAL_LABEL, CRYSTAL_CARD_COLOUR, CRYSTAL_THUMB, crystalInfo,
  crystalKnown, isCrystal, planCrystal, crystalModel, crystalHtml, wireCrystal, crystalFit,
  crystalHidden, refreshCrystalHidden, crystalPicking, takeCrystalPick } from './crystalui.js'
import * as compounds from './compounds.js'
import * as decor from './decortools.js'
import { useControllers, controllersHtml, wireControllers, plateHidden,
         drawsPart } from './controllers.js'

installHeightFog()
import { PHASES, migratePlan, asRef, exitName, shippedNext, wiringOf, nextOf, isGone,
         graphReport, removePlanPoint, startsCover, SHIPPED_WEIGHT_SUM,
         spawnerFacing, riftExits, riftTarget, riftsReached, movesExit, pathGraph,
         RIFT_OVER_EXIT } from './actors.js'

const el = (id) => document.getElementById(id)

//: Rift Lord's sidebar holds the one theme switch for the whole app: the page opens in
//: the theme it is given and follows the switch from then on.
document.documentElement.dataset.theme =
  new URLSearchParams(location.search).get('theme') || 'dark'
window.addEventListener('message', (e) => {
  if (e.source === window.parent && e.data && e.data.type === 'riftlord-theme') {
    document.documentElement.dataset.theme = e.data.theme
  }
  // the shell's Fab Kits tab converted or deleted a library
  if (e.source === window.parent && e.data && e.data.type === 'riftlord-fab-changed') {
    fab.forget(e.data.pack)
    if (el('fabpanel') && !el('fabpanel').hidden) listFab().catch(err => report('Fab libraries', err))
  }
})

// A thrown error used to leave the UI looking merely unresponsive -- a panel that
// never appeared rather than a failure -- so anything uncaught is shown.
function report (what, err) {
  const line = `${what}: ${err && err.message ? err.message : err}`
  const box = el('saved')
  if (box) box.textContent = line
  console.error(what, err)
}
addEventListener('error', (ev) => report('error', ev.error || ev.message))
addEventListener('unhandledrejection', (ev) => report('error', ev.reason))
//: The viewport's busy text; shown there only while the loading modal is down.
let busyText = ''
const busy = (text) => {
  const b = el('busy')
  busyText = text || ''
  b.style.display = text ? 'grid' : 'none'
  if (text) b.textContent = loadingDepth ? '' : text
  if (text && loadingDepth) el('loadtext').textContent = text
}

//: How many loads the loading modal is covering.
let loadingDepth = 0

/** Run `work` with the loading modal up, saying `text`; nested loads share one modal. */
async function whileLoading (text, work) {
  loadingDepth += 1
  el('loadtext').textContent = text
  el('loadmodal').hidden = false
  try {
    return await work()
  } finally {
    loadingDepth -= 1
    if (!loadingDepth) {
      el('loadmodal').hidden = true
      if (busyText) el('busy').textContent = busyText
    }
  }
}

async function json (url, options) {
  const r = await fetch(url, options)
  if (!r.ok) {
    const text = await r.text()
    let message = text.slice(0, 400)
    try { message = JSON.parse(text).error || message } catch { /* not JSON */ }
    throw new Error(message)
  }
  return r.json()
}

// -- the scene ---------------------------------------------------------------

//: A stencil buffer for the Trace ghost's one-colour-per-pixel pass (traceui.js ONCE).
const renderer = new THREE.WebGLRenderer({ antialias: true, stencil: true })
renderer.setPixelRatio(Math.min(devicePixelRatio, 2))
//: Per-material clipping, for the cutaway.
renderer.localClippingEnabled = true
//: Unreal UVs put v = 0 at the TOP row of a texture, as glTF does; three.js flips
//: images by default, which samples an atlas from the wrong region.
const UE_TEXTURE_FLIP_Y = false
el('view').appendChild(renderer.domElement)

const scene = new THREE.Scene()
scene.background = new THREE.Color(0x0d1016)

const camera = new THREE.PerspectiveCamera(55, 1, 10, 500000)
const controls = new OrbitControls(camera, renderer.domElement)
controls.enableDamping = false
controls.zoomToCursor = true
controls.screenSpacePanning = true
controls.minDistance = 50
controls.maxDistance = 200000
// Rotate on the RIGHT button. The left one is for selecting, and having it orbit as
// well meant every attempt to pick something also swung the camera. WASD covers what
// panning was for.
controls.mouseButtons = { LEFT: null, MIDDLE: THREE.MOUSE.DOLLY,
                          RIGHT: THREE.MOUSE.ROTATE }
controls.rotateSpeed = 0.4

//: Orbiting a level 50,000 units across is not how anyone wants to get around it, so
//: the camera also flies: WASD along the view, QE up and down, shift to hurry. The
//: speed follows how far out the camera is, because a step that feels right across a
//: courtyard is imperceptible across the whole map.
const held = new Set()
//: Keyboard shortcuts act only while the 3D view holds focus: never in a dialog, a
//: text field, a select or any sidebar control.
renderer.domElement.tabIndex = 0
renderer.domElement.addEventListener('pointerdown',
  () => renderer.domElement.focus({ preventScroll: true }))
renderer.domElement.addEventListener('blur', () => held.clear())
const viewFocused = () =>
  document.activeElement === renderer.domElement && !detailsDialog
addEventListener('keydown', (ev) => {
  if (!viewFocused()) return
  held.add(ev.key.toLowerCase())
})
addEventListener('keyup', (ev) => held.delete(ev.key.toLowerCase()))
addEventListener('blur', () => held.clear())

//: The trap grid's cell, in uu (riftlord/scratch.py GRID_CELL).
const TRAP_CELL = 300
//: A gizmo move snaps to this share of a trap cell.
const SNAP_SHARE = 0.25
//: Where Snap to Grid is remembered between sessions.
const SNAP_KEY = 'riftlord.snapToGrid'
let snapToGrid = localStorage.getItem(SNAP_KEY) !== 'off'
//: Shift, read off every pointer and key event before the gizmo hears it: holding it
//: moves freely.
let shiftDown = false
//: Alt, read the same way: holding it while moving turns edge snapping off.
let altDown = false
for (const type of ['pointerdown', 'pointermove', 'keydown', 'keyup']) {
  addEventListener(type, (ev) => { shiftDown = !!ev.shiftKey; altDown = !!ev.altKey }, true)
}

/** The gizmo's move step in uu, or 0 while snapping is off or shift is held. */
function snapStep () {
  if (!snapToGrid || shiftDown) return 0
  return ((level && level.scratch && level.scratch.grid) || TRAP_CELL) * SNAP_SHARE
}

//: A turn with the gizmo snaps to this many degrees unless shift is held.
const TURN_SNAP_DEGREES = 15

/** The gizmo's turn step in radians, or 0 while shift is held. */
function rotateStep () {
  return shiftDown ? 0 : THREE.MathUtils.degToRad(TURN_SNAP_DEGREES)
}
//: No browser context menu (and so no Reload) outside text fields. The 3D view's
//: right-drag turns the camera through OrbitControls, which this does not affect.
addEventListener('contextmenu', (ev) => {
  const t = ev.target
  const text = t && (t.tagName === 'TEXTAREA' || t.isContentEditable ||
    (t.tagName === 'INPUT' && !['checkbox', 'radio', 'range', 'button', 'submit', 'file']
      .includes(t.type)))
  if (!text) ev.preventDefault()
})

const FLY = { w: [0, 0, -1], s: [0, 0, 1], a: [-1, 0, 0], d: [1, 0, 0],
              e: [0, 1, 0], q: [0, -1, 0] }

//: How fast WASD moves, as a fraction of how far out the camera is. Low, because the
//: hard part is working inside a room rather than crossing the map, and shift is
//: there for when it is the other way round.
let flySpeed = 0.25

//: Cutaway: level geometry above a chosen Unreal height is not drawn.
//: Slider positions from the bottom of the play area (0) to off (CUTAWAY_STEPS).
const CUTAWAY_STEPS = 1000
//: How the slider eases towards the top: the cut falls as (1 - t) ^ CUT_EASE of the
//: height range, so the last stretch before off moves the cut in small steps.
const CUT_EASE = 1.6
//: Uu below the slider's top (CUT_TOP_SHARE) at which its first step cuts.
const CUT_TOP_GAP = 10
//: Pieces whose centre lies outside the play area are left out of the height range: the
//: play area is the CUT_AREA_SHARE..(1 - CUT_AREA_SHARE) band of piece centres along each
//: horizontal axis, widened by CUT_AREA_PAD of its width on each side.
const CUT_AREA_SHARE = 0.05
const CUT_AREA_PAD = 0.25
//: The share of piece bottoms the slider's bottom sits above, and of piece tops its top
//: sits above (the tallest few -- a spire, a cliff -- are cut from the first step).
const CUT_LOW_SHARE = 0.02
const CUT_TOP_SHARE = 0.995
//: Three.js clips where the signed distance is negative: -y + constant < 0 above it.
const cutPlane = new THREE.Plane(new THREE.Vector3(0, -1, 0), Infinity)
const CUT_PLANES = [cutPlane]
//: [bottom, top] Unreal z the slider spans (cutBand)
let cutRange = [0, 1]

/** The cut height for one slider position, or Infinity at the top (off). */
function cutHeight (step) {
  if (step >= CUTAWAY_STEPS) return Infinity
  const [lo, hi] = cutRange
  return hi - (hi - lo) * Math.pow(1 - step / CUTAWAY_STEPS, CUT_EASE)
}

/** The slider position for cut height `z` (cutHeight's inverse), below off. */
function cutStep (z) {
  const [lo, hi] = cutRange
  const share = Math.min(1, Math.max(0, (hi - z) / Math.max(1, hi - lo)))
  return Math.max(0, Math.min(CUTAWAY_STEPS - 1,
    Math.round((1 - Math.pow(share, 1 / CUT_EASE)) * CUTAWAY_STEPS)))
}

function setCutaway (step) {
  cutPlane.constant = cutHeight(step)
  const label = el('cutlabel')
  if (label) {
    label.textContent = Number.isFinite(cutPlane.constant)
      ? String(Math.round(cutPlane.constant)) : 'off'
  }
}

/** Value at share `q` of sorted numbers `xs`. */
const quantile = (xs, q) => xs[Math.min(xs.length - 1, Math.max(0, Math.floor(q * (xs.length - 1))))]

/** [bottom, top] Unreal z of the drawn level and added geometry inside the play area,
 * widened to the Trace ghost's heights while one is shown (its CUT_LOW_SHARE and
 * CUT_TOP_SHARE vertex heights), or null with nothing drawn. */
function cutBand () {
  const inside = playBoxes()
  const ghost = trace.heights().sort((a, b) => a - b)
  if (!inside.length && !ghost.length) return null
  const lows = [], tops = []
  if (inside.length) {
    tops.push(quantile(inside.map(b => b.max.y).sort((a, b) => a - b), CUT_TOP_SHARE))
    lows.push(quantile(inside.map(b => b.min.y).sort((a, b) => a - b), CUT_LOW_SHARE))
  }
  if (ghost.length) {
    tops.push(quantile(ghost, CUT_TOP_SHARE))
    lows.push(quantile(ghost, CUT_LOW_SHARE))
  }
  const bottom = Math.min(...lows)
  return [bottom, Math.max(bottom + 1, Math.max(...tops) - CUT_TOP_GAP)]
}

/** World boxes of every drawn level and added piece whose centre is in the play area. */
function playBoxes () {
  world.updateMatrixWorld(true)
  const boxes = []
  const m = new THREE.Matrix4()
  const take = (geo, matrix) => {
    if (!geo.boundingBox) geo.computeBoundingBox()
    const b = geo.boundingBox.clone().applyMatrix4(matrix)
    const s = b.getSize(new THREE.Vector3())
    if (s.x + s.y + s.z > 1e-3) boxes.push(b)
  }
  for (const g of [groups.level, groups.newGeo]) {
    g.traverse((o) => {
      if (!o.geometry || !o.visible) return
      if (o.isInstancedMesh) {
        for (let i = 0; i < o.count; i++) {
          o.getMatrixAt(i, m)
          take(o.geometry, m.premultiply(o.matrixWorld))
        }
      } else {
        take(o.geometry, o.matrixWorld)
      }
    })
  }
  if (!boxes.length) return []
  //: three.js x and z are Unreal x and y; three.js y is Unreal z
  const area = (axis) => {
    const c = boxes.map(b => (b.min[axis] + b.max[axis]) / 2).sort((a, b) => a - b)
    const lo = quantile(c, CUT_AREA_SHARE)
    const hi = quantile(c, 1 - CUT_AREA_SHARE)
    const pad = Math.max(hi - lo, 1) * CUT_AREA_PAD
    return [lo - pad, hi + pad]
  }
  const [x0, x1] = area('x')
  const [z0, z1] = area('z')
  return boxes.filter((b) => {
    const cx = (b.min.x + b.max.x) / 2
    const cz = (b.min.z + b.max.z) / 2
    return cx >= x0 && cx <= x1 && cz >= z0 && cz <= z1
  })
}

/** Fit the slider to what is drawn now, keeping the cut where it is. */
function refreshCutRange () {
  const band = cutBand()
  if (!band) return
  cutRange = band
  const input = el('cutaway')
  if (input && Number.isFinite(cutPlane.constant)) input.value = String(cutStep(cutPlane.constant))
}

/** A level opened: the cut starts off. */
function resetCutaway () {
  const input = el('cutaway')
  if (input) {
    input.max = String(CUTAWAY_STEPS)
    input.value = String(CUTAWAY_STEPS)
  }
  setCutaway(CUTAWAY_STEPS)
}

/** Whether a ray hit landed on geometry the cutaway hides. */
function cutHides (hit) {
  if (!Number.isFinite(cutPlane.constant) || hit.point.y <= cutPlane.constant) return false
  const mats = Array.isArray(hit.object.material) ? hit.object.material : [hit.object.material]
  return mats.some(m => m && m.clippingPlanes === CUT_PLANES)
}

//: How often a live cut makes sure everything drawn since is cut too, in ms.
const CUT_COVER_MS = 500
let cutCoverAt = 0

//: groups a cut never hides: the sky, the selection's outlines and the move cue
const UNCUT = new Set()

/** Make everything in the view take the cut: added pieces, foliage, markers, lights,
 * effects, trap grids, routes. Run while a cut is live, for what is drawn after it. */
function coverCut () {
  if (!Number.isFinite(cutPlane.constant)) return
  const now = performance.now()
  if (now - cutCoverAt < CUT_COVER_MS) return
  cutCoverAt = now
  for (const g of world.children) {
    if (UNCUT.has(g)) continue
    g.traverse((o) => {
      const mats = Array.isArray(o.material) ? o.material : o.material ? [o.material] : []
      for (const m of mats) {
        if (m.clippingPlanes === CUT_PLANES) continue
        m.clippingPlanes = CUT_PLANES
        m.needsUpdate = true
      }
    })
  }
}

function fly (dt) {
  let moved = false
  const step = Math.max(120, controls.target.distanceTo(camera.position) * flySpeed)
    * dt * (held.has('shift') ? 4 : 1)
  const move = new THREE.Vector3()
  for (const [key, dir] of Object.entries(FLY)) {
    if (!held.has(key)) continue
    moved = true
    const v = new THREE.Vector3(dir[0], dir[1], dir[2])
    // Along the view for WASD, along world up for QE, so up is always up.
    if (dir[1] === 0) v.applyQuaternion(camera.quaternion)
    move.add(v)
  }
  if (!moved) return
  move.normalize().multiplyScalar(step)
  camera.position.add(move)
  controls.target.add(move)
}

const fill = new THREE.HemisphereLight(0xbcd2ff, 0x202430, 2.2)
const sun = new THREE.DirectionalLight(0xffffff, 1.6)
sun.position.set(8000, 14000, 10000)
scene.add(fill, sun, sun.target)
//: The sun's shadows, while the level's lighting shows (the lights layer): drawn over a
//: square this many uu across around where the camera looks, at SHADOW_MAP texels.
const SHADOW_SPAN = 12000
const SHADOW_MAP = 4096
//: How far back along its light the sun sits from where the camera looks, in uu.
const SUN_DISTANCE = 20000
//: Shadow offsets against acne on double-sided level materials (depth, and uu along
//: the surface normal).
const SHADOW_BIAS = -0.0005
const SHADOW_NORMAL_BIAS = 20
renderer.shadowMap.enabled = true
renderer.shadowMap.type = THREE.PCFSoftShadowMap
sun.shadow.mapSize.set(SHADOW_MAP, SHADOW_MAP)
Object.assign(sun.shadow.camera, { left: -SHADOW_SPAN / 2, right: SHADOW_SPAN / 2,
  top: SHADOW_SPAN / 2, bottom: -SHADOW_SPAN / 2, near: 1, far: 2 * SUN_DISTANCE })
sun.shadow.camera.updateProjectionMatrix()
sun.shadow.bias = SHADOW_BIAS
sun.shadow.normalBias = SHADOW_NORMAL_BIAS
sun.castShadow = false
//: Where the sun shines from (scene space, unit length), and the level's own sun and
//: sky light strengths (applySky).
const sunFrom = new THREE.Vector3(8000, 14000, 10000).normalize()
const levelRig = { sun: 1.6, fill: 2.2 }

/** An OMD3 FLinearColor as a three.js colour. */
function linear (rgba, fallback) {
  if (!rgba || rgba.length < 3) return new THREE.Color(fallback)
  return new THREE.Color(rgba[0], rgba[1], rgba[2])
}

/** Light the view the way the level is lit, and put its sky behind it.
 *
 * Only two of OMD3's forty skies have a sphere to draw; the rest are atmospheric and
 * height fog, so the horizon colour IS the sky. Split Stairs' is a deep blue. Using
 * the level's own numbers matters for placement, not decoration -- a route judged
 * under a neutral grey rig sits differently under the level's actual light. */
function applySky (sky) {
  const look = (sky && sky.look) || {}
  const dir = look.DirectionalLightComponent || {}
  const amb = look.SkyLightComponent || {}
  const fog = look.ExponentialHeightFogComponent || {}
  const horizon = linear(fog.FogInscatteringColor, 0x0d1016)
  scene.background = horizon
  // Far enough out not to grey the level, and the sky itself opts out of fog, so the
  // horizon colour tints the distance without swallowing the skybox.
  scene.fog = new THREE.Fog(horizon, 30000, 220000)
  sun.color.copy(linear(dir.LightColor, 0xffffff))
  sun.intensity = Math.max(0.3, (dir.Intensity ?? 1.25) * 1.3)
  // An Unreal rotator points the light along its own +X; three.js wants the place it
  // shines from, and this group's up is +Z, so the swizzle is (x, z, -y).
  const [pitch, yaw] = dir.RelativeRotation || [-45, 45]
  const p = pitch * Math.PI / 180, y = yaw * Math.PI / 180
  const d = [Math.cos(p) * Math.cos(y), Math.cos(p) * Math.sin(y), Math.sin(p)]
  sun.position.set(-d[0] * 20000, -d[2] * 20000, d[1] * 20000)
  sunFrom.copy(sun.position).normalize()
  fill.color.copy(linear(amb.LightColor, 0xbcd2ff))
  fill.groundColor.copy(linear(amb.LowerHemisphereColor, 0x202430))
  fill.intensity = Math.max(0.4, (amb.Intensity ?? 0.65) * 2.4)
  levelRig.sun = sun.intensity
  levelRig.fill = fill.intensity
}

const world = new THREE.Group()
//: UNREAL IS LEFT-HANDED AND THREE.JS IS NOT, so the view was mirrored.
//:
//: Rotating -90 degrees about X puts Unreal's Z up, which is what this line did, and
//: a rotation PRESERVES handedness -- so UE (x, y, z) became (x, z, -y). Looking
//: along +X with up = UE +Z, three.js then puts +Z (which is UE -Y) on the right,
//: where Unreal puts +Y. Every level read left-for-right against the game.
//:
//: Negating Y before the rotation gives (x, z, y) -- the real conversion, with the
//: negative determinant that a handedness change requires. The underlying data stays
//: in Unreal coordinates, so plans, picking and the emitter are untouched; only the
//: view is corrected. The flipped winding decides which side a one-sided material
//: shows (meshSide).
world.rotation.x = -Math.PI / 2
world.scale.set(1, -1, 1)
scene.add(world)

//: Which faces a served mesh draws. Unreal's triangles wind the other way to three.js's
//: under the mirrored `world` (Floor_4x4's top face: cross product points down), so a
//: one-sided material (`oneSided`, leveldata.one_sided) shows its BACK side here.
const meshSide = (m) => (m && m.oneSided ? THREE.BackSide : THREE.DoubleSide)

//: The untransformed bounding box of each mesh, so a picked piece can be outlined
//: where it actually sits.
const meshBounds = new Map()

//: The geometry and material the LEVEL is drawing for each mesh name.
//:
//: Copying one of the level's own pieces used to re-ask the server for a mesh that was
//: already on screen: 14 seconds between Ctrl+V and the copy appearing, for a wall the
//: renderer was holding all along. Rebuilt per level and never put in `geoCache`,
//: because `drawLevel` DISPOSES these -- a cross-level cache would hand out dead
//: buffers, which is the failure that has already cost two rounds here.
const levelGeo = new Map()

const groups = {
  sky: new THREE.Group(),
  level: new THREE.Group(),
  foliage: new THREE.Group(),
  lights: new THREE.Group(),
  vfx: new THREE.Group(),
  outline: new THREE.Group(),
  routes: new THREE.Group(),
  wiring: new THREE.Group(),
  newGeo: new THREE.Group(),
  added: new THREE.Group(),
  coverage: new THREE.Group(),
  doorMesh: new THREE.Group(),
  forcefield: new THREE.Group(),
  volumes: new THREE.Group(),
  //: empty: its visibility is the fog switch (heightfog.js)
  fog: new THREE.Group(),
  //: each kit piece's trap surfaces as cells (trapgrids.js)
  trapGrids: new THREE.Group(),
}
for (const g of Object.values(groups)) world.add(g)

//: the Trace tool, another game's level as a ghost (traceui.js); never picked or built
const trace = createTrace({
  parent: world,
  box: el('tracebox'),
  clip: CUT_PLANES,
  renderer,
  getKey: () => level ? (editing ? 'mission:' + editing : 'level:' + plan.level) : null,
  ownLevel: () => level && !level.scratch ? plan.level : null,
  conversion: () => (plan.scratch && plan.scratch.source) || null,
  focus: () => {
    if (!level) return null
    if (level.focus) {
      const f = level.focus
      return { lo: [f[0][0], f[1][0], f[2][0]], hi: [f[0][1], f[1][1], f[2][1]] }
    }
    return level.bounds ? { lo: level.bounds[0], hi: level.bounds[1] } : null
  },
})

//: Existing content is cool and new content is warm, so the two never blur
//: together when a new route runs beside one the level already had.
const COLOUR = {
  spawner: 0x4f7fb5, door: 0x5f8f6b, point: 0x5d7c9a, exit: 0xdc50c8, exitPoint: 0x9a7bff,
  newSpawner: 0xff7a45, newDoor: 0xffc247, newPoint: 0x9be86a, newRoute: 0xffa060,
  newRift: 0xff5ad0, playerStart: 0x4fb0a6, newPlayerStart: 0xff8f6a,
  //: Navigation coverage is a boundary, not content, so it reads as neither the cool
  //: existing wiring nor the warm plan. Flight is exact and drawn solid; the surface
  //: bound is a guess at nothing more than extent and is drawn faint.
  flightVolume: 0x3fd8c8, flightPortal: 0xf7f36a, surfaceBound: 0x6a6f7a,
  flyerStray: 0xff3b30,
}

//: Everything that can be hidden. A level is 1,983 pieces, 289 lights and 115 effects
//: across five streaming sublevels, and working inside a room means being able to take
//: the ceiling, the lights or a whole sublevel away.
const LAYERS = [
  ['sky', 'the sky'], ['level', 'the level'], ['foliage', 'foliage'],
  ['lights', 'lights'], ['vfx', 'effects'],
  ['wiring', 'spawners and points'], ['routes', 'routes'],
  ['coverage', 'navigation coverage'], ['volumes', 'blocking volumes'], ['fog', 'fog'],
  ['trapGrids', 'trap grids'],
]
//: The layers that start hidden when a level opens: lights, effects, navigation
//: coverage, blocking volumes and trap grids.
const HIDDEN_ON_OPEN = ['lights', 'vfx', 'coverage', 'volumes', 'trapGrids']

//: Which streaming sublevels are shown. Each of a level's own gets a box of its own.
const shownSubs = new Set()

//: Comfortably inside the camera's far plane (500,000) and larger than any level's
//: extent (about 120,000 on Dragon Boneyard), so the dome always encloses the view.
const SKY_RADIUS = 300000
let skyMesh = null

const gizmo = new Gizmo(camera, renderer.domElement, scene, snapStep,
  { rotateStep, scaleStep: (scale, start) => snapPickedScale(scale, start) })
gizmo.setSpace('local')
scene.add(gizmo.getHelper())
//: the handle's matrix when the current drag began
let outlineFrom = new THREE.Matrix4()
gizmo.addEventListener('dragging-changed', (ev) => {
  controls.enabled = !ev.value
  if (gizmo.object === handle) {
    //: the outlines ride with the handle during a drag and are rebuilt when it ends
    outlineFrom = handle.matrix.clone()
    if (ev.value && gizmo.mode === 'translate') edgeSnapBegin()
    if (ev.value && gizmo.mode === 'scale') scaleSnapBegin()
    if (!ev.value) {
      edgeSnapEnd()
      groups.outline.matrix.identity()
      groups.outline.matrixAutoUpdate = true
      drawPickOutlines()
    }
  }
  // One undo step per drag, taken as it begins.
  if (ev.value) {
    snapshot()
    const found = findObject(selected)
    scaleBase = found
      ? [found.obj.scaleX || 1, found.obj.scaleY || 1, found.obj.scaleZ || 1]
      : [1, 1, 1]
  } else if (gizmo.mode === 'scale') {
    //: redraw so the pane takes its new size and the marker returns to scale 1
    drawPlan()
    showProps()
  } else if (wiringPick && gizmo.object === wiringPick) {
    drawPlan()
    showProps()
  } else if (gizmo.object === handle && picked.some(p => p.marker)) {
    //: routes and door lines follow the markers the selection carried
    drawPlan()
  }
  //: a moved ground spawner or ground path point stands on the floor under it again
  //: (shift: where it is let go)
  if (!ev.value && gizmo.mode === 'translate' && !shiftDown) {
    const found = findObject(selected)
    if (found && floorSnapOf(found.obj)) snapRiftHeight(found.obj, floorSnapOf(found.obj))
  }
})
gizmo.addEventListener('objectChange', () => {
  if (gizmo.object === handle) {
    if (gizmo.mode === 'translate') edgeSnapMove()
    movePicked()
    //: the trap grids layer follows the drag
    trapGridAt = 0
    groups.outline.matrixAutoUpdate = false
    groups.outline.matrix.copy(handle.matrix).multiply(outlineFrom.clone().invert())
    return
  }
  if (wiringPick && gizmo.object === wiringPick) {
    recordWiringMove()
    drawPickOutlines()
    return
  }
  writeBackSelected()
  drawPlan()
  showProps()
})

// -- state -------------------------------------------------------------------

let level = null
let units = []
let plan = { level: null, version: 2, rifts: [], doors: [], spawners: [], points: [],
             playerStarts: [], wiring: [], removed: [], waves: [], edits: [], added: [] }

//: What each kind of object carries, fetched from the server so the panel and the
//: emitter cannot disagree about a property the game reads.
let schema = { schema: {}, doors: [], kinds: [] }
let mode = 'none'
let selected = null            // { kind: 'spawner'|'door'|'point', id, index }
let nextId = 1

//: The level's own placements, by mesh, so an edit can find the instance to move and
//: the id to record it against.
const piecesByMesh = new Map()
const instByMesh = new Map()

//: One of the level's own gameplay actors, when that is what is selected. Kept apart
//: from `picked` (geometry) and `selected` (the plan) because all three have different
//: properties and different edits.
let wiringPick = null
//: One of the level's own effects (a marker of the effects layer), when that is what
//: is selected.
let fxPick = null

//: Which of the level's own pieces are picked. More than one at a time because a
//: door rarely fits without shifting the wall either side of it, and moving them one
//: at a time loses the relationship between them.
let picked = []

//: the blocking-volumes layer edits the plan through these (volumes.js)
useVolumes({
  plan: () => plan,
  el,
  gizmo,
  snapshot: () => snapshot(),
  flash: (text) => flash(text),
  refresh: () => { drawPlan(); refreshPicked(); showProps() },
  unpick: () => {
    wiringPick = null
    gizmo.detach()
    drawPlan()
    refreshPicked()
    showProps()
  },
})

//: wall plates, ambience cues, lightning, cauldron pours (controllers.js)
useControllers({
  plan: () => plan,
  level: () => level,
  json,
  snapshot: () => snapshot(),
  showProps: () => showProps(),
  redrawAdded: () => redrawAdded(),
  reapply: () => { if (level) applyRemoved() },
  addEntry: (entry) => addPickedEntry(entry),
})

//: The invisible object the gizmo actually drags when level geometry is selected.
//: Its movement is applied to everything picked, so a multiple selection keeps its
//: shape instead of collapsing onto one point.
const handle = new THREE.Object3D()
handle.visible = false
world.add(handle)
let handleFrom = new THREE.Matrix4()

let catalogue = []
let kitFilter = 'all'
//: the compound filter's role group and art set ('all' or a value rows carry)
let compoundGroup = 'all'
let compoundSet = 'all'
//: the rows New Object's filter and search show, and how many of them are drawn
let kitShown = []
let kitDrawn = 0



// -- drawing the level -------------------------------------------------------

function clear (group) {
  for (const child of [...group.children]) {
    group.remove(child)
    child.geometry?.dispose()
    if (child.material) {
      const mats = Array.isArray(child.material) ? child.material : [child.material]
      for (const m of mats) m.dispose()
    }
  }
}

//: Remove without disposing, for a group whose meshes SHARE cached geometry.
//: `clear` disposes, which is right when each mesh owns its buffers and fatal when
//: they do not: the next redraw hands out a disposed geometry and draws nothing.
function detach (group) {
  for (const child of [...group.children]) group.remove(child)
}

//: A piece arrives with its world transform already composed, column-major, because
//: two things have to be walked to get it: a component's parent chain, and a
//: Blueprint kit piece's own component offsets. Composing it here from angles was
//: wrong twice over -- an Unreal rotator is not a three.js Euler, and a kit piece is
//: not one mesh at the actor's origin.
function pieceMatrix (piece) {
  return new THREE.Matrix4().fromArray(piece.matrix)
}

//: Object matrix of an InstancedMesh holding mirrored placements; its own inverse.
//: three.js picks the front face from the OBJECT's determinant, never an instance's,
//: so mirrored instances are stored as MIRROR x matrix under a mirrored object.
const MIRROR = new THREE.Matrix4().makeScale(-1, 1, 1)
//: Suffix on the group key of the mirrored InstancedMesh of a mesh.
const MIRRORED_KEY = '\u0001mirrored'
//: Appended to the group key of a mesh's foliage placements (the foliage layer).
const FOLIAGE_KEY = '\u0001foliage'

/** The level geometry a click may hit: the level layer, and foliage while it shows. */
function levelPickables () {
  return groups.level.children.concat(groups.foliage.visible ? groups.foliage.children : [],
                                      scratchUi.pickables(),
                                      flipGroup.children.filter(m => m.visible))
}

/** A linear [r, g, b] multiplier as a THREE.Color, or null. */
function tintColour (rgb) {
  if (!Array.isArray(rgb) || rgb.length < 3) return null
  return new THREE.Color().setRGB(rgb[0], rgb[1], rgb[2], THREE.LinearSRGBColorSpace)
}

//: How much of a light beam's or glow mesh's own colour adds over what is behind it.
const GLOW_OPACITY = 0.35

/** A level material made a soft additive glow: its texture and tint, unlit, no depth
 * writes, both sides (a light beam, a glow cone). Slot materials each become one. */
function glowMaterial (material) {
  const one = (m) => {
    const g = new THREE.MeshBasicMaterial({
      map: m.map || null, color: m.color ? m.color.clone() : 0xffffff, transparent: true,
      opacity: GLOW_OPACITY, blending: THREE.AdditiveBlending, depthWrite: false,
      side: THREE.DoubleSide, fog: false, clippingPlanes: CUT_PLANES })
    m.dispose()
    return g
  }
  return Array.isArray(material) ? material.map(one) : one(material)
}

function isMirrored (flat) {
  return new THREE.Matrix4().fromArray(flat).determinant() < 0
}

/** setMatrixAt with a placement's own matrix, whichever InstancedMesh holds it. */
function setPieceMatrix (inst, i, m) {
  inst.setMatrixAt(i, inst.userData && inst.userData.mirrored
    ? m.clone().premultiply(MIRROR) : m)
}

/** Draw the level, one InstancedMesh per distinct mesh, as the geometry arrives.
 *
 * Geometry comes in slices, commonest mesh first, and each slice is drawn as soon as
 * it lands. An earlier version asked for one mesh at a time and awaited each, so a
 * level took as long as the sum of its parts -- minutes of a blank screen. The
 * server reads a slice's meshes in parallel, so this is the same work with the
 * waiting overlapped and something on screen within a second or two.
 */
async function drawLevel (data) {
  clear(groups.level)
  clear(groups.foliage)
  //: their geometry and materials were the level's, disposed with it
  flipGroup.clear()
  flipMeshes.clear()
  levelGeo.clear()
  clear(groups.sky)
  skyMesh = null
  piecesByMesh.clear()
  instByMesh.clear()
  const byMesh = new Map()
  //: Force-field panes are pulled out of the opaque pass.
  //:
  //: The level is drawn one InstancedMesh per MESH NAME, with the mesh's own
  //: texture -- so all three `Plane` placements in a level share one material even
  //: though they carry three different ones, and a force field came out as an
  //: untextured quad, which looks exactly like nothing being drawn. They are
  //: translucent anyway, so they belong in a pass of their own.
  const fields = []
  for (const piece of data.pieces) {
    //: hidden in game (bVisible off or bHiddenInGame on): not drawn
    if (piece.hidden) continue
    if (/forcefield/i.test(piece.material || '')) {
      fields.push(piece)
      continue
    }
    if (!byMesh.has(piece.mesh)) byMesh.set(piece.mesh, [])
    byMesh.get(piece.mesh).push(piece)
  }
  drawForcefields(fields, data)
  const loader = new THREE.TextureLoader()
  const CHUNK = 12
  let start = 0
  let total = byMesh.size
  let drawn = 0
  const gen = levelGen
  while (start < total) {
    if (gen !== levelGen) return
    busy(`drawing ${data.level}\n${drawn} of ${total} meshes`)
    let slice
    try {
      slice = await json(`api/geometry/${encodeURIComponent(data.level)}` +
        `?from=${start}&count=${CHUNK}` + (data.scratch ? '&scratch=1' : ''))
    } catch (err) {
      busy('could not read the level: ' + err.message)
      return
    }
    if (gen !== levelGen) return
    total = slice.total || total
    for (const [name, m] of Object.entries(slice.meshes)) {
      drawn += 1
      const pieces = byMesh.get(name)
      if (!pieces || !m.verts || !m.tris) continue
      const geo = new THREE.BufferGeometry()
      geo.setAttribute('position',
        new THREE.Float32BufferAttribute(m.verts.flat(), 3))
      if (m.uvs && m.uvs.length === m.verts.length) {
        geo.setAttribute('uv', new THREE.Float32BufferAttribute(m.uvs.flat(), 2))
      }
      geo.setIndex(m.tris)
      geo.computeVertexNormals()
      geo.computeBoundingBox()
      meshBounds.set(name, geo.boundingBox)
      noteSlots(name, m)
      const isSky = level && level.sky && name === level.sky.mesh
      const opts = { side: meshSide(m), clippingPlanes: CUT_PLANES }
      if (isSky) {
        // The sky is not lit and is not fogged, and it is drawn from the inside, so it
        // needs its own material rather than the one the rest of the level uses. Its
        // texture comes from the material the level paints it with -- `Lava_Sky_INST`
        // on North Wing, `Arcane_Sky_INST1` on Split Stairs -- not from the mesh.
        const map = level.sky.texture
          ? new THREE.TextureLoader().load(
            'api/texture' + level.sky.texture + '?size=2048')
          : null
        if (map) {
          map.colorSpace = THREE.SRGBColorSpace
          map.wrapS = map.wrapT = THREE.RepeatWrapping
          map.flipY = UE_TEXTURE_FLIP_Y
        }
        //: The sky RIDES THE CAMERA at a size that fits the view frustum.
        //:
        //: As the level places it, the skybox is a 4096uu mesh at scale 5000 -- a
        //: radius of 20,480,000uu, against a camera far plane of 500,000. It was
        //: forty times outside the frustum and clipped away entirely, every level,
        //: which is why the viewport showed nothing but the clear colour however
        //: well the texture resolved.
        //:
        //: Keeping its ROTATION matters: the level orients the sky texture, and that
        //: orientation is the difference between a horizon and a seam overhead. Only
        //: the position and the scale are replaced.
        const spin = new THREE.Quaternion()
        const junk = new THREE.Vector3()
        pieceMatrix(pieces[0]).decompose(junk, spin, new THREE.Vector3())
        const sky = new THREE.Mesh(
          geo, new THREE.MeshBasicMaterial({
            map: map || undefined,
            color: map ? 0xffffff : 0x223044,
            side: THREE.BackSide, fog: false, depthWrite: false }))
        sky.quaternion.copy(spin)
        sky.scale.setScalar(SKY_RADIUS / 4096)
        pieces.forEach((piece, i) => { piece.instance = i })
        skyMesh = sky
        // Drawn first and never occluding anything.
        sky.renderOrder = -1
        sky.userData = { mesh: name, sky: true }
        groups.sky.add(sky)
        continue
      }
      let liquid = null
      if (piecesAreLiquid(pieces)) {
        // Lava and arcane pools ship translucent, and drawn opaque an arcane pool
        // reads as a slab of floor. Lit from within as well, because they are the
        // brightest thing in a room and nothing else in the view emits.
        opts.transparent = true
        opts.opacity = 0.72
        opts.depthWrite = false
        // TINTED BY WHAT IT IS.
        //
        // Several liquid surfaces name no material at all -- Mage Tower's three lava
        // planes carry neither a slot nor an override -- so they drew as untextured
        // grey at 70% opacity, which is indistinguishable from water whatever the
        // pool actually is. The tint comes from the mesh's own name, and a real
        // texture still wins over it.
        liquid = pieces[0].liquidKind || null
      }
      // ONE INSTANCED MESH PER (mesh, material).
      //
      // A mesh is often painted by the ACTOR that places it rather than carrying a
      // material itself -- `Scenario_13_Ground` has none of its own and is painted
      // `Ground_Dirt_Rock_Mud_MP` by its Blueprint -- so drawing by mesh name alone
      // and using only the mesh's own texture left those untextured, and gave every
      // placement of a shared mesh the same material even when they differ.
      const byMat = new Map()
      for (const piece of pieces) {
        const key = piece.material || ''
        if (!byMat.has(key)) byMat.set(key, [])
        byMat.get(key).push(piece)
      }
      const texOf = (path) => {
        const map = loader.load('api/texture' + path)
        map.flipY = UE_TEXTURE_FLIP_Y
        map.wrapS = map.wrapT = THREE.RepeatWrapping
        map.colorSpace = THREE.SRGBColorSpace
        return map
      }
      for (const [matName, group] of byMat) {
        const texture = (matName && data.materials && data.materials[matName]) ||
          m.texture
        // PER-SECTION MATERIALS, when the actor does not override the whole mesh.
        //
        // A section names a slot in the mesh's own material list and 117 meshes
        // declare more than one; `Jar`'s sections use slots 0, 2, 1, so neither the
        // first material nor the section number is the right one. Each section
        // becomes a geometry group with its own material.
        const multi = !matName && (m.sections || []).length > 1 &&
          (m.slotTextures || []).some(t => t)
        let material
        if (multi) {
          geo.clearGroups()
          for (const sec of m.sections) {
            geo.addGroup(sec.first, sec.tris * 3, sec.slot)
          }
          material = (m.slotTextures || []).map((t, slot) => {
            const o = Object.assign({}, opts)
            if (t) {
              o.map = texOf(t)
              const tint = tintColour((m.slotTints || [])[slot])
              if (tint) o.color = tint
            } else o.color = 0x8a8f99
            return new THREE.MeshLambertMaterial(o)
          })
        } else {
          const o = Object.assign({}, opts)
          if (texture) {
            o.map = texOf(texture)
            //: the material's base-colour multiplier (BC_ColorTint, BC_FinalBrightness)
            const tint = tintColour(matName
              ? (data.materialTints || {})[matName] : m.tint)
            if (tint) o.color = tint
            //: an `_EM` map is EMISSIVE, not a base colour. Lava's only texture is
            //: one, and drawn flat it is a dark red-brown that reads as murky water.
            if (/_EM$/.test(texture)) {
              o.emissiveMap = o.map
              o.emissive = new THREE.Color(0xffffff)
            }
          } else if (liquid) {
            o.color = new THREE.Color(LIQUID_TINT[liquid] || LIQUID_TINT.water)
            if (liquid === 'lava' || liquid === 'magma' || liquid === 'arcane') {
              o.emissive = new THREE.Color(LIQUID_TINT[liquid])
              o.emissiveIntensity = 0.6
            }
          } else {
            o.color = 0x8a8f99
          }
          material = new THREE.MeshLambertMaterial(o)
        }
        //: a light beam or glow cone is light, not a solid: drawn soft and additive
        if (group.every(p => p.glow)) material = glowMaterial(material)
        const baseKey = byMat.size > 1 ? name + '\u0000' + matName : name
        for (const piece of group) {
          // Kept so undo can put a moved piece back where the level had it.
          piece.original = piece.original || piece.matrix
          const edited = plan.edits.find(e => e.id === piece.id)
          if (edited) piece.matrix = edited.matrix
        }
        //: Mirrored placements (negative determinant) get an InstancedMesh of their own,
        //: and so do foliage placements, which draw into the foliage layer.
        const split = []
        for (const foliage of [false, true]) {
          const own = group.filter(p => !!p.foliage === foliage)
          split.push({ foliage, mirrored: false, part: own.filter(p => !isMirrored(p.matrix)) })
          split.push({ foliage, mirrored: true, part: own.filter(p => isMirrored(p.matrix)) })
        }
        split.forEach(({ foliage, mirrored, part }) => {
          if (!part.length) return
          const key = (mirrored ? baseKey + MIRRORED_KEY : baseKey) +
            (foliage ? FOLIAGE_KEY : '')
          const inst = new THREE.InstancedMesh(geo, material, part.length)
          inst.userData = { mesh: name, group: key, mirrored: !!mirrored, liquid: !!liquid }
          if (mirrored) {
            inst.matrixAutoUpdate = false
            inst.matrix.copy(MIRROR)
          }
          part.forEach((piece, i) => {
            // Remember where each placement sits in the instanced mesh, so a click can
            // be turned back into the piece it hit and a move written against its id.
            piece.instance = i
            piece.group = key
            setPieceMatrix(inst, i, pieceMatrix(piece))
          })
          piecesByMesh.set(key, part)
          instByMesh.set(key, inst)
          inst.instanceMatrix.needsUpdate = true
          ;(foliage ? groups.foliage : groups.level).add(inst)
        })
        //: first material wins for a mesh drawn with several, which is the one the
        //: commonest placement uses
        if (!levelGeo.has(name)) levelGeo.set(name, { geo, mat: material })
      }
    }
    drawn += (slice.missing || []).length
    start += CHUNK
  }
  syncFlipped()
  busy('')
}

/** Whether these placements are a liquid or energy surface. */
//: What each kind of pool reads as when it names no material of its own.
const LIQUID_TINT = {
  lava: 0xff5a1e, magma: 0xff5a1e, arcane: 0x9a5cff, acid: 0x86e02a,
  slime: 0x6fbf3a, goop: 0x7a8c2a, ooze: 0x5d7a2a, tar: 0x23232a,
  water: 0x3f86b5,
}

function piecesAreLiquid (pieces) {
  return pieces.length > 0 && pieces.every(p => p.liquid)
}

function marker (at, colour, radius, shape, onTop) {
  const geo = shape === 'box'
    ? new THREE.BoxGeometry(radius * 1.6, radius * 1.6, radius * 3)
    : shape === 'octa'
      ? new THREE.OctahedronGeometry(radius)
      : new THREE.SphereGeometry(radius, 16, 12)
  const m = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({
    color: colour, emissive: colour, emissiveIntensity: 0.25, roughness: 0.5,
    // A gameplay marker sits inside the geometry it belongs to -- a rift stands on a
    // floor, a door fills an archway -- so drawing it behind that geometry made it
    // impossible to see or click.
    depthTest: !onTop, fog: false }))
  //: a door's box lets the door mesh show through it
  if (shape === 'box') {
    m.material.transparent = true
    m.material.opacity = DOOR_BOX_OPACITY
    m.material.depthWrite = false
  }
  if (onTop) m.renderOrder = 2
  m.position.set(at[0], at[1], at[2])
  return m
}

//: Opacity of a door's box marker.
const DOOR_BOX_OPACITY = 0.35

//: A spawner's facing cone, in multiples of its marker's radius: length, base radius, and
//: how far out along its facing it stands.
const SPAWNER_NOSE = { length: 0.9, radius: 0.35, out: 1.3 }
//: One cone geometry of unit length and base radius along +x, shared by every nose.
const SPAWNER_NOSE_GEO = new THREE.ConeGeometry(1, 1, 8).rotateZ(-Math.PI / 2)

/** A spawner's marker: an octahedron with a cone along its facing (local +x). The cone
 * shares the marker's material. */
function spawnerMarker (at, colour, radius, onTop) {
  const m = marker(at, colour, radius, 'octa', onTop)
  const nose = new THREE.Mesh(SPAWNER_NOSE_GEO, m.material)
  nose.scale.set(radius * SPAWNER_NOSE.length, radius * SPAWNER_NOSE.radius,
                 radius * SPAWNER_NOSE.radius)
  nose.position.set(radius * SPAWNER_NOSE.out, 0, 0)
  nose.renderOrder = m.renderOrder
  m.add(nose)
  return m
}

//: A route drawn as a TUBE, not a line.
//:
//: WebGL ignores `linewidth`, so every `THREE.Line` is one pixel wide whatever is
//: asked for -- and a one-pixel line between two path points, over a lit level, is
//: invisible at any sensible camera distance. The routes layer looked like a toggle
//: that did nothing; it had been drawing all along. A cylinder costs nothing at these
//: counts (6 branches on North Wing, about 80 on Canyon Keep).
//: Its own geometry per tube, NOT a shared one. `groups.routes` and `groups.added`
//: are emptied with `clear`, which disposes geometry -- a shared cylinder would be
//: disposed on the first redraw and every route after that would draw nothing. A
//: six-sided cylinder is a few dozen bytes and there are at most a hundred.
function tube (from, to, colour, radius) {
  const a = new THREE.Vector3(from[0], from[1], from[2])
  const b = new THREE.Vector3(to[0], to[1], to[2])
  const span = new THREE.Vector3().subVectors(b, a)
  const len = span.length()
  if (!len) return null
  const mesh = new THREE.Mesh(
    new THREE.CylinderGeometry(1, 1, 1, 6, 1, true),
    new THREE.MeshBasicMaterial(
      { color: colour, transparent: true, opacity: 0.85, fog: false }))
  mesh.scale.set(radius || 14, len, radius || 14)
  mesh.position.copy(a).addScaledVector(span, 0.5)
  //: the cylinder runs along its own +Y, so it is turned to face the segment
  mesh.quaternion.setFromUnitVectors(
    new THREE.Vector3(0, 1, 0), span.clone().normalize())
  return mesh
}

function line (from, to, colour, width) {
  const geo = new THREE.BufferGeometry().setFromPoints([
    new THREE.Vector3(from[0], from[1], from[2]),
    new THREE.Vector3(to[0], to[1], to[2])])
  return new THREE.Line(geo, new THREE.LineBasicMaterial(
    { color: colour, linewidth: width || 1 }))
}

//: At most this many of a level's lights are real lights at once: the ones nearest the
//: view, re-chosen every LAMP_SYNC_MS. Split Stairs ships 289 and every real one costs
//: shader work; a fixed pool keeps the shaders from recompiling as the view moves.
const MAX_REAL_LIGHTS = 40
//: How often the real lights are moved to the lights nearest the view, in ms.
const LAMP_SYNC_MS = 500
//: Scene units per Unreal brightness unit: the same gain the sun's lux gets (applySky),
//: so a point light and the sun keep the ratio the level has.
const LIGHT_GAIN = 1.3
//: A light's marker: a bulb this many uu across, and a spot light's cone this long.
const LIGHT_BULB_RADIUS = 45
const SPOT_CONE_LENGTH = 400
//: Widest spot cone drawn, in degrees from its axis.
const SPOT_CONE_MAX = 80
//: The pool of real lights, kept under `groups.lights` so the layer switch covers them.
const lamps = []
//: /api/lights for the open level (leveldata.light_rig), or null until it arrives.
let lightRig = null
let lampsAt = 0

/** The level's own lights as markers (a bulb, and a cone for a spot light), pickable
 * like any piece while the lights layer shows; the nearest are real lights (syncLamps). */
function drawLights (data) {
  clear(groups.lights)
  lamps.length = 0
  for (const l of data.lights || []) {
    const shown = lightColour(actorKey(l), l.colour)
    const colour = new THREE.Color(shown[0], shown[1], shown[2])
    const bulb = new THREE.Mesh(new THREE.SphereGeometry(LIGHT_BULB_RADIUS, 10, 8),
      new THREE.MeshBasicMaterial({ color: colour, fog: false }))
    if (l.kind === 'SpotLight' && Array.isArray(l.dir)) {
      const half = THREE.MathUtils.degToRad(Math.min(l.cone || 44, SPOT_CONE_MAX))
      const geo = new THREE.ConeGeometry(Math.tan(half) * SPOT_CONE_LENGTH, SPOT_CONE_LENGTH,
                                         16, 1, true)
      //: the cone's tip at the bulb, opening along +Y, then turned onto the light's axis
      geo.translate(0, -SPOT_CONE_LENGTH / 2, 0)
      geo.rotateX(Math.PI)
      const cone = new THREE.LineSegments(new THREE.EdgesGeometry(geo, 1),
        new THREE.LineBasicMaterial({ color: colour, transparent: true, opacity: 0.7 }))
      geo.dispose()
      cone.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0),
        new THREE.Vector3(l.dir[0], l.dir[1], l.dir[2]).normalize())
      cone.raycast = () => {}
      bulb.add(cone)
    }
    bulb.position.set(l.loc[0], l.loc[1], l.loc[2])
    bulb.userData = { levelActor: actorKey(l), kind: 'light', entry: l, base: l.loc.slice() }
    groups.lights.add(bulb)
  }
  for (let i = 0; i < MAX_REAL_LIGHTS; i += 1) {
    const lamp = new THREE.PointLight(0xffffff, 0, 1000, 2)
    lamp.userData = { lamp: true }
    lamps.push(lamp)
    groups.lights.add(lamp)
  }
  lampsAt = 0
  applyActorEdits()
}

/** Put the real lights on the level lights nearest the view, at their level brightness
 * (leveldata.light_rig; the marker list until it arrives), moved and removed as the plan
 * says. */
function syncLamps () {
  if (!groups.lights.visible || !level || !lamps.length) return
  const now = performance.now()
  if (now - lampsAt < LAMP_SYNC_MS) return
  lampsAt = now
  world.updateMatrixWorld(true)
  const eye = world.worldToLocal(controls.target.clone())
  const rows = []
  for (const l of lightRig || []) {
    const at = actorPlace(l.key, l.position)
    const hex = (plan.lightColours || {})[l.key]
    const lit = hex ? { ...l, colour: hexRgb(hex).map(srgbToLinear) } : l
    if (at) rows.push({ l: lit, at, d: at.distanceToSquared(eye) })
  }
  //: lights the plan places (torches, braziers, lights from New Object), at the actor
  for (const e of plan.added || []) {
    if (e.kind !== 'kit') continue
    const row = catalogue.find(r => r.name === e.name)
    for (const l of (row && row.lights) || []) {
      const at = new THREE.Vector3(e.matrix[12], e.matrix[13], e.matrix[14])
      rows.push({ l, at, d: at.distanceToSquared(eye) })
    }
  }
  if (!lightRig) {
    for (const m of groups.lights.children) {
      if (!m.userData.entry || !m.visible) continue
      const e = m.userData.entry
      rows.push({ l: { colour: e.colour, brightness: e.intensity || 5000, radius: e.radius,
                       inverseSquared: false }, at: m.position.clone(),
                  d: m.position.distanceToSquared(eye) })
    }
  }
  rows.sort((a, b) => a.d - b.d)
  lamps.forEach((lamp, i) => {
    const r = rows[i]
    if (!r) { lamp.intensity = 0; return }
    lamp.position.copy(r.at)
    lamp.color.setRGB(r.l.colour[0], r.l.colour[1], r.l.colour[2], THREE.LinearSRGBColorSpace)
    lamp.distance = r.l.radius || 1000
    //: an inverse-squared light falls off as 1/d^2, Unreal's own; an exponent falloff
    //: light is drawn without one, inside its radius
    lamp.decay = r.l.inverseSquared ? 2 : 0
    lamp.intensity = (r.l.brightness || 0) * LIGHT_GAIN
  })
}

/** The plan key ('<sublevel>|<actor export>') of a level light's or effect's actor, or
 * null when it has none. */
const actorKey = (f) => f && f.actor ? `${f.sub || 'persistent'}|${f.actor}` : null

/** '#rrggbb' -> [r, g, b] (0-1, sRGB). */
const hexRgb = (hex) => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255)
/** [r, g, b] (0-1, sRGB) -> '#rrggbb'. */
const rgbHex = (rgb) => '#' + rgb.slice(0, 3).map(v =>
  Math.max(0, Math.min(255, Math.round(v * 255))).toString(16).padStart(2, '0')).join('')
/** One sRGB channel (0-1) as linear light. */
const srgbToLinear = (c) => c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)

/** A level light's colour (0-1, sRGB): the plan's (`lightColours`, riftlord/build/world/
 * levelretint.py) or its own `colour`. */
function lightColour (key, colour) {
  const hex = key && (plan.lightColours || {})[key]
  return hex ? hexRgb(hex) : colour
}

/** The particle system a level effect plays: the plan's (`fxSwaps`) or its own. */
const fxTemplate = (key, template) => (key && (plan.fxSwaps || {})[key]) || template

/** The level pieces of the Blueprint actor `key` names, or []. */
function actorPieces (key) {
  if (!key || !level) return []
  const [sub, export_] = [key.slice(0, key.lastIndexOf('|')), +key.slice(key.lastIndexOf('|') + 1)]
  return (level.pieces || []).filter(p => p.source === 'blueprint' && p.export === export_ &&
    (p.sub || 'persistent') === sub)
}

/** Where a level light or effect of actor `key`, shipped at `loc`, is now: moved with
 * the plan's `actorMoves` or with its Blueprint's drawn parts; null once removed. */
function actorPlace (key, loc) {
  const at = new THREE.Vector3(loc[0], loc[1], loc[2])
  if (!key) return at
  if ((plan.removedFx || []).includes(key)) return null
  const delta = (plan.actorMoves || {})[key]
  if (delta) return at.add(new THREE.Vector3(delta[0], delta[1], delta[2]))
  const parts = actorPieces(key)
  if (!parts.length) return at
  const gone = new Set(plan.removed || [])
  if (parts.some(p => gone.has(p.id))) return null
  const lead = parts.find(p => p.original && p.original !== p.matrix)
  if (!lead) return at
  const move = new THREE.Matrix4().fromArray(lead.matrix)
    .multiply(new THREE.Matrix4().fromArray(lead.original).invert())
  return at.applyMatrix4(move)
}

/** Put the level's lights and effects where the plan has them, and hide removed ones. */
function applyActorEdits () {
  for (const g of [groups.lights, groups.vfx]) {
    for (const m of g.children) {
      const d = m.userData
      if (!d || !d.base) continue
      const at = actorPlace(d.levelActor, d.base)
      m.visible = !!at
      if (at) m.position.copy(at)
      if (d.kind === 'light') {
        const c = lightColour(d.levelActor, d.entry.colour)
        for (const part of [m, ...m.children]) part.material.color.setRGB(c[0], c[1], c[2])
      } else if (d.kind === 'fx' && !d.sound) {
        const template = fxTemplate(d.levelActor, d.entry.template)
        if (template !== d.vfx) restyleEffect(m, template)
      }
    }
  }
  lampsAt = 0
}

/** Draw a level effect's cloud as particle system `template`'s group. */
function restyleEffect (m, template) {
  const row = catalogue.find(r => r.kind === 'fx' && r.template === template)
  const group = row ? row.group : guessFxGroup(template)
  const shape = EFFECT_SHAPE_OF[group] || 'drift'
  const cloud = m.children[0]
  cloud.geometry = effectGeometry(shape)
  cloud.material = effectMaterial(FX_GROUP_COLOUR[group] || FX_DEFAULT_COLOUR, shape)
  m.userData.vfx = template
  m.userData.group = group
}

//: An effect is drawn as a drifting cloud of glowing points in its group's colour and
//: shape (a rising cone for fire, a falling column for weather), this many points.
const EFFECT_POINTS = 48
//: Size of one point, in uu.
const EFFECT_POINT_SIZE = 60
//: Radius of an effect's pick target, in uu.
const EFFECT_PICK_RADIUS = 150
//: {shape: [radius, height, rise per second (uu, negative falls)]} of the clouds.
const EFFECT_SHAPES = {
  rise: [180, 520, 160], fall: [420, 900, -420], drift: [260, 260, 40], burst: [220, 300, 260],
}
//: Which shape each surveyed effect group (riftlord/fxsurvey.py) is drawn with.
const EFFECT_SHAPE_OF = {
  'Fire and smoke': 'rise', 'Lava and ash': 'burst', Weather: 'fall', Water: 'fall',
  'Fog and dust': 'drift', Magic: 'drift', Nature: 'drift', Acid: 'rise',
}
//: The clouds' shared clock (seconds), advanced in tick().
const effectClock = { value: 0 }
//: shape -> its geometry, shared by every cloud of that shape
const effectGeo = new Map()
//: colour|shape -> its material
const effectMat = new Map()
let pointSprite = null

/** A soft round dot, the points' texture. */
function effectSprite () {
  if (pointSprite) return pointSprite
  const c = document.createElement('canvas')
  c.width = c.height = 64
  const g = c.getContext('2d')
  const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32)
  grad.addColorStop(0, 'rgba(255,255,255,1)')
  grad.addColorStop(0.4, 'rgba(255,255,255,0.55)')
  grad.addColorStop(1, 'rgba(255,255,255,0)')
  g.fillStyle = grad
  g.fillRect(0, 0, 64, 64)
  pointSprite = new THREE.CanvasTexture(c)
  return pointSprite
}

/** The points of one cloud shape: x, y in a disc (a cone narrowing upwards for `rise`),
 * z along its height, each with a seed for its drift. */
function effectGeometry (shape) {
  if (effectGeo.has(shape)) return effectGeo.get(shape)
  const [radius, height] = EFFECT_SHAPES[shape]
  const pos = []
  const seed = []
  for (let i = 0; i < EFFECT_POINTS; i += 1) {
    //: evenly spread by the golden angle, not at random, so every cloud looks the same
    const t = (i + 0.5) / EFFECT_POINTS
    const a = i * 2.399963
    const r = radius * Math.sqrt(t)
    pos.push(Math.cos(a) * r, Math.sin(a) * r, 0)
    seed.push(t * height)
  }
  const geo = new THREE.BufferGeometry()
  geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3))
  geo.setAttribute('seed', new THREE.Float32BufferAttribute(seed, 1))
  geo.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, 0, height / 2), radius + height)
  effectGeo.set(shape, geo)
  return geo
}

/** The drifting-points material of one colour and shape. */
function effectMaterial (colour, shape) {
  const key = colour + '|' + shape
  if (effectMat.has(key)) return effectMat.get(key)
  const [, height, rise] = EFFECT_SHAPES[shape]
  const mat = new THREE.PointsMaterial({
    color: colour, size: EFFECT_POINT_SIZE, map: effectSprite(), transparent: true,
    depthWrite: false, blending: THREE.AdditiveBlending, fog: false,
    clippingPlanes: CUT_PLANES })
  mat.onBeforeCompile = (shader) => {
    shader.uniforms.uTime = effectClock
    shader.vertexShader = 'attribute float seed;\nuniform float uTime;\n' +
      shader.vertexShader.replace('#include <begin_vertex>',
        '#include <begin_vertex>\n' +
        `float h = mod(seed + uTime * ${rise.toFixed(1)}, ${height.toFixed(1)});\n` +
        `transformed.z += h;\n` +
        (shape === 'rise' ? `transformed.xy *= 1.0 - 0.8 * h / ${height.toFixed(1)};\n` : '') +
        (shape === 'burst' ? `transformed.xy *= 0.3 + h / ${height.toFixed(1)};\n` : ''))
  }
  effectMat.set(key, mat)
  return mat
}

//: The most an effect's pick target spreads and rises, in uu: a rain column is picked
//: where it starts, not across the room it falls over.
const EFFECT_PICK_SPREAD = 240
const EFFECT_PICK_RISE = 600
/** The pick target of a cloud of `shape`: a cylinder from its base up over the points.
 * Its own geometry each (`clear` disposes a target's). */
function effectPickGeometry (shape) {
  const [radius, height] = EFFECT_SHAPES[shape]
  const r = Math.max(EFFECT_PICK_RADIUS, Math.min(radius, EFFECT_PICK_SPREAD))
  const h = Math.max(2 * EFFECT_PICK_RADIUS, Math.min(height, EFFECT_PICK_RISE))
  const geo = new THREE.CylinderGeometry(r, r, h, 12)
  geo.rotateX(Math.PI / 2)
  geo.translate(0, 0, h / 2 - EFFECT_PICK_RADIUS / 2)
  return geo
}

/** An effect as what it is: a pick target (invisible) holding a drifting cloud of its
 * group's colour and shape. */
function effectStandIn (group, colour) {
  const shape = EFFECT_SHAPE_OF[group] || 'drift'
  const target = new THREE.Mesh(effectPickGeometry(shape),
    new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false }))
  const cloud = new THREE.Points(effectGeometry(shape), effectMaterial(colour, shape))
  cloud.raycast = () => {}
  target.add(cloud)
  return target
}

/** The level's own particle effects, drawn as effect clouds and pickable like any piece
 * while the effects layer shows. */
function drawVfx (data) {
  clear(groups.vfx)
  for (const f of data.vfx || []) {
    const template = fxTemplate(vfxKey(f), f.template)
    const row = catalogue.find(r => r.kind === 'fx' && r.template === template)
    const group = row ? row.group : guessFxGroup(template)
    const m = effectStandIn(group, FX_GROUP_COLOUR[group] || FX_DEFAULT_COLOUR)
    m.position.set(f.loc[0], f.loc[1], f.loc[2])
    m.userData = { vfx: template, vfxKey: vfxKey(f), levelActor: vfxKey(f), kind: 'fx',
                   entry: f, base: f.loc.slice(), group }
    groups.vfx.add(m)
  }
  //: the level's ambient sounds (any sublevel, `_Audio` included), in the effects layer
  for (const s of data.sounds || []) {
    const entry = { ...s, template: s.cue }
    const m = soundStandIn()
    m.position.set(s.loc[0], s.loc[1], s.loc[2])
    m.userData = { vfx: s.cue, vfxKey: vfxKey(entry), levelActor: vfxKey(entry), kind: 'fx',
                   sound: true, entry, base: s.loc.slice(), group: guessFxGroup(s.cue) }
    groups.vfx.add(m)
  }
  applyRemovedFx()
}

/** One of the level's ambient sounds: a pick target (invisible) holding the diamond an
 * added AmbientSound draws as. */
function soundStandIn () {
  const target = new THREE.Mesh(new THREE.SphereGeometry(EFFECT_PICK_RADIUS, 12, 8),
    new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false }))
  const mark = new THREE.Mesh(soundMarkGeometry(), soundMarkMaterial())
  mark.raycast = () => {}
  target.add(mark)
  return target
}

let soundGeo = null
let soundMat = null
/** The shared diamond of the level's sounds. */
function soundMarkGeometry () {
  soundGeo = soundGeo || new THREE.OctahedronGeometry(ACTOR_MARK_RADIUS)
  return soundGeo
}
/** The shared material of the level's sounds. */
function soundMarkMaterial () {
  soundMat = soundMat || new THREE.MeshBasicMaterial({
    color: ACTOR_MARK_COLOUR.Sound, fog: false, clippingPlanes: CUT_PLANES })
  return soundMat
}

/** 'AmbEmitter_LavaBubbles_LP_SC' -> 'Lava Bubbles'. */
function soundLabel (cue) {
  const name = String(cue || 'Sound').replace(/^Amb(Emitter|Emit|Bed)?_/, '')
    .replace(/(_LP)?_SC$/, '')
  return name.replace(/_/g, ' ').replace(/(?<=[a-z])(?=[A-Z])/g, ' ').trim() || 'Sound'
}

/** A level effect's group from its particle system's name, before the catalogue is read. */
function guessFxGroup (template) {
  const t = String(template || '')
  if (/rain|snow|splash/i.test(t)) return 'Weather'
  if (/lava|ash|ember/i.test(t)) return 'Lava and ash'
  if (/fire|smoke|torch|flame|brazier/i.test(t)) return 'Fire and smoke'
  if (/water|drip|fall/i.test(t)) return 'Water'
  if (/acid|goo|slime/i.test(t)) return 'Acid'
  if (/arcane|magic|rune|mote|spark/i.test(t)) return 'Magic'
  if (/leaf|leaves|pollen|firefl/i.test(t)) return 'Nature'
  return 'Fog and dust'
}

/** The plan's key for one of the level's own effects ('<sublevel>|<actor export>'), or
 * null for one with no actor of its own (riftlord/apply.py removed_fx). */
const vfxKey = (f) => removable(f) ? `${f.sub || 'persistent'}|${f.actor}` : null

/** Hide the level's effects and lights the plan takes out, and place the moved ones. */
function applyRemovedFx () {
  applyActorEdits()
}

/** Take the level's own effects with these keys out (or `back`, put them back). */
function setRemovedFx (keys, back) {
  snapshot()
  const gone = new Set(plan.removedFx || [])
  for (const k of keys) if (k) back ? gone.delete(k) : gone.add(k)
  plan.removedFx = [...gone].sort()
  applyRemovedFx()
}

/** The level's own spawners, doors and routes, drawn cool. */
function drawWiring (data) {
  clear(groups.wiring)
  clear(groups.routes)
  for (const p of Object.values(data.points)) {
    if (!p.location) continue
    // An exit point is not the rift, so it does not wear the rift's colour.
    const m = marker(p.location, p.exit ? COLOUR.exitPoint : COLOUR.point,
                     p.exit ? 170 : 110, p.exit ? 'octa' : 'ball', true)
    // The level's own gameplay is selectable too: carving a mission out of a War
    // Scenario means moving and removing what is already wired up, not only adding.
    m.userData = { wiring: true, kind: 'point', name: p.export, data: p }
    groups.wiring.add(m)
  }
  //: an actor's shipped yaw in degrees: a rotator's [1] or a plain number; undefined
  //: when the level data does not carry it (Turn is then not offered)
  const yawOf = (rot) => {
    const y = Array.isArray(rot) ? rot[1] : rot
    return typeof y === 'number' ? ((y % 360) + 360) % 360 : undefined
  }
  const facing = (m) => {
    if (typeof m.userData.yaw === 'number') {
      m.rotation.z = THREE.MathUtils.degToRad(m.userData.yaw)
    }
    groups.wiring.add(m)
  }
  for (const s of data.spawners) {
    if (!s.location) continue
    const m = spawnerMarker(s.location, COLOUR.spawner, 240, true)
    m.userData = { wiring: true, kind: 'spawner', name: s.export, data: s,
                   yaw: yawOf(s.rotation) }
    facing(m)
  }
  for (const d of Object.values(data.doors)) {
    if (!d.location) continue
    const m = marker(d.location, COLOUR.door, 170, 'box', true)
    m.userData = { wiring: true, kind: 'door', name: d.export, data: d,
                   yaw: yawOf(d.rotation) }
    facing(m)
  }
  for (const r of data.rifts || []) {
    const m = marker(r.loc, COLOUR.exit, 340, 'octa', true)
    m.userData = { wiring: true, kind: 'rift', name: r.name, data: r,
                   yaw: yawOf(r.rotation) }
    facing(m)
  }
  for (const s of data.player_starts || []) {
    if (!s.location) continue
    const yaw = yawOf(s.rotation)
    const m = startMarker(s.location, COLOUR.playerStart, yaw)
    m.userData = { wiring: true, kind: 'playerStart', name: s.export, data: s, yaw }
    groups.wiring.add(m)
  }
}

//: A player start's marker: a capsule a player tall, with a cone showing its facing.
const START_RADIUS = 60
const START_HEIGHT = 192

function startMarker (at, colour, yaw) {
  const mat = new THREE.MeshStandardMaterial({
    color: colour, emissive: colour, emissiveIntensity: 0.25, roughness: 0.5,
    depthTest: false, fog: false })
  const geo = new THREE.CapsuleGeometry(START_RADIUS, START_HEIGHT - 2 * START_RADIUS, 4, 10)
  geo.rotateX(Math.PI / 2)
  const m = new THREE.Mesh(geo, mat)
  const nose = new THREE.Mesh(new THREE.ConeGeometry(START_RADIUS * 0.6, START_RADIUS * 1.6, 8),
    mat.clone())
  nose.geometry.rotateZ(-Math.PI / 2)
  nose.position.set(START_RADIUS * 1.6, 0, START_HEIGHT * 0.25)
  nose.renderOrder = 2
  m.add(nose)
  m.renderOrder = 2
  m.position.set(at[0], at[1], at[2])
  m.rotation.z = THREE.MathUtils.degToRad(yaw || 0)
  return m
}

/** Where a Ref is drawn: an added point's place, or a level point's, after moves. */
function refLocation (ref) {
  if (ref === 'rift') ref = riftTarget(plan, level)
  if (typeof ref === 'number') {
    const p = plan.points.find(o => o.id === ref)
    return p ? p.at : null
  }
  const w = wiringOf(plan, ref)
  if (w && w.at) return w.at
  const own = ((level && level.points) || {})[ref]
  return (own && own.location) || null
}

/** The level's own routes, after the plan's edits: moved and removed points, and
 * replaced branches. */
function drawLevelRoutes () {
  clear(groups.routes)
  if (!level) return
  for (const name of Object.keys(level.points || {})) {
    if (isGone(plan, name)) continue
    const from = refLocation(name)
    const edited = Array.isArray((wiringOf(plan, name) || {}).next)
    for (const b of nextOf(plan, level, name)) {
      const to = refLocation(b.to)
      if (!from || !to) continue
      const t = tube(from, to, edited ? COLOUR.newRoute : COLOUR.point, 12)
      if (t) groups.routes.add(t)
    }
  }
  //: the level's own spawners to their first point, when the plan re-routes them
  for (const s of level.spawners || []) {
    const w = wiringOf(plan, s.export)
    if (!w || w.gone) continue
    const at = w.at || s.location
    for (const k of ['start', 'flyerStart']) {
      if (!(k in w) || w[k] === null) continue
      const to = refLocation(w[k])
      if (at && to) groups.routes.add(line(at, to, COLOUR.newRoute))
    }
  }
}

/** Force-field panes, drawn as what they are: translucent, coloured, two-sided.
 *
 * Their coverage is the thing a designer has to judge -- how much of an opening the
 * field actually fills -- so they are drawn so the geometry behind stays visible
 * rather than being hidden by an opaque slab. No toggle: a door is not an overlay.
 */
async function drawForcefields (fields, data) {
  detach(groups.forcefield)
  if (!fields.length) return
  const geo = await fieldGeometry()
  if (!geo) return
  for (const piece of fields) {
    const mesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({
      color: 0xa060ff, transparent: true, opacity: 0.38,
      side: THREE.DoubleSide, depthWrite: false }))
    pieceMatrix(piece).decompose(mesh.position, mesh.quaternion, mesh.scale)
    mesh.userData = { forcefield: true, mesh: piece.mesh, id: piece.id, piece }
    groups.forcefield.add(mesh)
  }
}

/** One translucent force-field pane, sharing the cached plane geometry. */
async function fieldMesh () {
  const geo = await fieldGeometry()
  if (!geo) return null
  return new THREE.Mesh(geo, new THREE.MeshBasicMaterial({
    color: 0xa060ff, transparent: true, opacity: 0.38,
    side: THREE.DoubleSide, depthWrite: false }))
}

let fieldGeo = null
async function fieldGeometry () {
  if (fieldGeo !== null) return fieldGeo
  try {
    const m = await json('api/mesh/Plane')
    const g = new THREE.BufferGeometry()
    g.setAttribute('position',
      new THREE.Float32BufferAttribute(m.verts.flat(), 3))
    g.setIndex(m.tris)
    g.computeVertexNormals()
    fieldGeo = g
  } catch (err) {
    fieldGeo = false
  }
  return fieldGeo
}

/** Where the level's navigation data reaches.
 *
 * Two kinds of answer that must not be blurred together. **Flight is exact**: the
 * volumetric graph is decoded, so each convex volume and each portal joining two of
 * them is a real boundary and is drawn as geometry. **Surface is a bounding box and
 * nothing more** -- the walkable graph's records are not cracked -- so it is drawn
 * faint and labelled as an extent, never as a floor.
 *
 * The flyer-point check is what this is for while authoring: a point outside every
 * volume is a route into airspace the graph does not cover, which is the failure
 * that cost this project weeks.
 */
function drawCoverage (cov) {
  clear(groups.coverage)
  if (!cov) return
  const box3 = (lo, hi) => new THREE.Box3(
    new THREE.Vector3(lo[0], lo[1], lo[2]), new THREE.Vector3(hi[0], hi[1], hi[2]))

  if (cov.surface) {
    const h = new THREE.Box3Helper(
      box3(cov.surface.min, cov.surface.max), COLOUR.surfaceBound)
    h.material.transparent = true
    h.material.opacity = 0.35
    groups.coverage.add(h)
  }

  const f = cov.flight
  if (f) {
    for (const v of f.volumes) {
      const h = new THREE.Box3Helper(box3(v.min, v.max), COLOUR.flightVolume)
      h.material.transparent = true
      h.material.opacity = 0.85
      groups.coverage.add(h)
    }
    for (const po of f.portals) {
      const vs = po.verts
      if (vs.length < 3) continue
      const pts = []
      // A fan, so a portal with more than four corners still closes.
      for (let i = 1; i + 1 < vs.length; i += 1) {
        pts.push(vs[0][0], vs[0][1], vs[0][2],
                 vs[i][0], vs[i][1], vs[i][2],
                 vs[i + 1][0], vs[i + 1][1], vs[i + 1][2])
      }
      const geo = new THREE.BufferGeometry()
      geo.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3))
      geo.computeVertexNormals()
      groups.coverage.add(new THREE.Mesh(geo, new THREE.MeshBasicMaterial({
        color: COLOUR.flightPortal, transparent: true, opacity: 0.4,
        side: THREE.DoubleSide, depthWrite: false })))
    }
  }

  for (const p of cov.flyerPoints || []) {
    if (p.inside) continue
    // Only the strays are marked. A point that is covered is already drawn as wiring,
    // and ringing every one of them would bury the problem in decoration.
    const m = marker(p.location, COLOUR.flyerStray, 200, 'octa', true)
    m.userData = { coverage: true, name: p.export }
    groups.coverage.add(m)
  }
  for (const p of cov.groundPoints || []) {
    //: Red when it is outside the walkable bounds, which is the only case worth
    //: looking at; the rest are drawn small so the outliers stand out.
    const m = marker(p.location, p.inside ? 0x5f7a52 : 0xff5a5a,
                     p.inside ? 70 : 150, 'ball', true)
    m.userData = { coverage: true, name: p.export, ground: true }
    groups.coverage.add(m)
  }
}

/** The plan, drawn warm, with each marker carrying what it stands for. */
function drawPlan () {
  aimSpawners()
  clear(groups.added)
  applyRemovedFx()
  drawTeleportLinks()
  const rift = planRift()
  const own = riftExits(plan, level)
  for (const r of plan.rifts) {
    add(marker(r.at, COLOUR.newRift, 320, 'octa'), 'rift', r.id, r.rotation)
  }
  for (const d of plan.doors) {
    //: onTop for the same reason the level's door markers use it: the mesh now
    //: drawn around a door would otherwise bury its handle.
    add(marker(d.at, COLOUR.newDoor, 200, 'box', true), 'door', d.id, d.rotation)
  }
  for (const sp of plan.spawners) {
    add(spawnerMarker(sp.at, COLOUR.newSpawner, 280), 'spawner', sp.id, sp.rotation)
    const door = typeof sp.door === 'number'
      ? (plan.doors.find(d => d.id === sp.door) || {}).at
      : sp.door ? doorLocation(sp.door) : null
    if (door) groups.added.add(line(sp.at, door, COLOUR.newDoor))
    for (const k of ['start', 'flyerStart']) {
      const to = sp[k] !== null && sp[k] !== undefined ? refLocation(sp[k]) : null
      if (to) groups.added.add(line(sp.at, to, k === 'flyerStart' ? 0x7ad4ff : COLOUR.newRoute))
    }
  }
  for (const pt of plan.points) {
    //: a rift's own exit point wears the level exit's colour and shape, and leads nowhere
    if (own.has(pt.id)) {
      add(marker(pt.at, COLOUR.exitPoint, 170, 'octa'), 'point', pt.id, 0)
      continue
    }
    // Flyer points read cool, ground points warm, so a mixed route is obvious.
    add(marker(pt.at, pt.flyersOnly ? 0x7ad4ff : COLOUR.newPoint, 140), 'point', pt.id, 0)
    // The route is drawn from each point's own branches. A weighted fork is drawn
    // thinner on the less likely side.
    const branches = pt.next && pt.next.length ? pt.next : [{ to: 'rift', weight: 1 }]
    for (const b of branches) {
      const to = b.to === 'rift' ? rift : refLocation(b.to)
      if (!to) continue
      const minor = branches.length > 1 && b.weight < 0.5
      const t = tube(pt.at, to, minor ? 0xa8703c : COLOUR.newRoute, minor ? 9 : 14)
      if (t) groups.added.add(t)
    }
  }
  for (const s of plan.playerStarts || []) {
    const m = startMarker(s.at, COLOUR.newPlayerStart, s.rotation)
    m.userData = { plan: true, kind: 'playerStart', id: s.id }
    groups.added.add(m)
  }
  drawLevelRoutes()
  applyWiring()
  trace.sync(plan)
  // The markers are rebuilt, so the gizmo is holding an object that no longer
  // exists; re-attaching it here is what keeps a selected thing draggable. A level
  // actor's or level piece's handle is left where it is.
  if (selected || (!wiringPick && !picked.length)) attachTo(selected)
  //: Door meshes cover the level's doors AND the plan's, and a plan edit can move,
  //: re-class or resize a door, so they are redrawn with the plan. Parts and
  //: geometry are cached, so this is cheap after the first pass.
  drawDoorMeshes()
}

/** Where a branch points: the rift, any point the PLAN placed, or one the level has.
 *
 * This looked only at the same spawner's own points, so a branch to a point already in
 * the level drew no line at all -- and that is the commonest branch there is, because
 * an added route hands off to the level's exit. A join between two of the plan's own
 * routes was invisible for the same reason. The emitter resolves all three, so the
 * viewport has to as well, or it draws a route the saved map does not have.
 */
function branchTarget (to) {
  return refLocation(to)
}

/** Where one of the level's own doors stands, after the plan's moves. */
function doorLocation (name) {
  const w = wiringOf(plan, name)
  if (w && w.at) return w.at
  const d = Object.values((level && level.doors) || {}).find(o => o.export === name)
  return d ? d.location : null
}

function add (mesh, kind, id, rotation, owner) {
  mesh.rotation.z = THREE.MathUtils.degToRad(rotation || 0)
  mesh.userData = { plan: true, kind, id, owner }
  groups.added.add(mesh)
  return mesh
}

// -- the plan ----------------------------------------------------------------

/** Where 'rift' and a point with no branches lead: the placed rift taking the level's moved
 * exit, else the level's exit point, else (it removed) the first rift's own exit point. */
function planRift () {
  if (movesExit(plan, level)) return plan.rifts[0].at
  const to = riftTarget(plan, level)
  if (typeof to === 'number') return refLocation(to)
  const own = ((level && level.rifts) || []).find(r => !isGone(plan, r.name))
  if (own) return (wiringOf(plan, own.name) || {}).at || own.loc
  if (plan.rifts.length) return plan.rifts[0].at
  return riftPoint()
}

/** The level's own rift: the rift ACTOR, not the exit path point.
 *
 * They are different things sitting close together -- North Wing's rift is
 * `BP_OMDRift_2` at z=350 and its exit point is `OMDPathPoint6` at z=70 -- and treating
 * the point as the rift made the tool report a path point as one. */
function riftPoint () {
  if (!level) return null
  const actor = (level.rifts || [])[0]
  if (actor) return actor.loc
  for (const p of Object.values(level.points)) {
    if (p.exit && p.location) return p.location
  }
  return null
}

/** Everything in the plan, flattened, so one lookup serves selection and lists. */
function planObjects () {
  const out = []
  for (const r of plan.rifts) out.push({ kind: 'rift', obj: r })
  for (const d of plan.doors) out.push({ kind: 'door', obj: d })
  for (const sp of plan.spawners) out.push({ kind: 'spawner', obj: sp })
  for (const pt of plan.points || []) out.push({ kind: 'point', obj: pt })
  for (const s of plan.playerStarts || []) out.push({ kind: 'playerStart', obj: s })
  return out
}

function findObject (sel) {
  if (!sel) return null
  return planObjects().find(o => o.kind === sel.kind && o.obj.id === sel.id) || null
}

/** The plan spawner a new door is given to: the selected one, else the newest. */
function currentSpawner () {
  const last = plan.spawners[plan.spawners.length - 1] || null
  const found = findObject(selected)
  return (found && found.kind === 'spawner' ? found.obj : null) || last
}

//: A new player start is usable in this phase until the user picks another.
const DEFAULT_PHASE = 'breaks'

/** A new object of any kind, with the defaults its class actually ships with. */
function blank (kind, at) {
  const fields = schema.schema[kind] || {}
  const obj = { id: nextId++, at }
  for (const [field, spec] of Object.entries(fields)) obj[field] = spec.default
  if (kind === 'spawner') {
    obj.door = null
    obj.start = null
    obj.flyerStart = null
    // The index is what an onslaught names to spawn here, and a level's own run 1..N
    // contiguously, so a new spawner takes the next free number rather than 0.
    obj.index = nextSpawnerIndex()
  }
  if (kind === 'point') {
    obj.next = []
    delete obj.isStart
    delete obj.isExit
  }
  if (kind === 'playerStart') {
    obj.rotation = 0
    obj.phase = DEFAULT_PHASE
  }
  return obj
}

/** The Ref of the point currently selected: an added point's id, a level point's name. */
function selectedPointRef () {
  if (selected && selected.kind === 'point') return selected.id
  if (wiringPick && wiringPick.userData.kind === 'point') return wiringPick.userData.name
  return null
}

/** Add a branch from a point (added or the level's own) to `to`, evening the weights. */
function linkFrom (ref, to) {
  if (typeof ref === 'number') {
    const p = plan.points.find(o => o.id === ref)
    if (!p) return
    p.next = (p.next || []).concat([{ to, weight: 1.0 }])
    evenBranches(p)
    return
  }
  const w = wiringEntry(ref, 'point')
  w.next = (Array.isArray(w.next) ? w.next : shippedNext(level, ref))
    .concat([{ to, weight: 1.0 }])
  evenBranches(w)
}

/** Rifts in play: the plan's, and the level's own not removed. */
function riftsInPlay () {
  return plan.rifts.length +
    ((level && level.rifts) || []).filter(r => !isGone(plan, r.name)).length
}

/** The selected added spawner, when its route of its own kind has no start yet. */
function startingSpawner () {
  const found = findObject(selected)
  if (!found || found.kind !== 'spawner') return null
  const sp = found.obj
  const key = sp.kind === 'ground' ? 'start' : 'flyerStart'
  return sp[key] === null || sp[key] === undefined
    ? { plan: true, obj: sp, kind: sp.kind } : null
}

/** The plan's edit entry for one of the level's own actors, made if missing. */
function wiringEntry (name, kind) {
  plan.wiring = plan.wiring || []
  let w = plan.wiring.find(o => o.name === name)
  if (!w) {
    w = { name, kind }
    plan.wiring.push(w)
  }
  return w
}

/** One past the highest spawner index in the level and in the plan. */
function nextSpawnerIndex () {
  let highest = 0
  for (const sp of (level && level.spawners) || []) {
    highest = Math.max(highest, sp.index || 0)
  }
  for (const sp of plan.spawners) highest = Math.max(highest, sp.index || 0)
  return highest + 1
}

function place (requested, at) {
  snapshot()
  // A force field door IS a door. It gets its own button because it is placed for a
  // different job -- a flyer opening, usually -- and because it is the only door with
  // a size. But it stays kind 'door' throughout, so the schema, the selection, the
  // plan file and the emitter all keep treating it as one.
  const forcefield = requested === 'forcefield'
  //: an unstable rift is a ground spawner with `unstable` set (riftlord/planactors.py)
  const unstable = requested === UNSTABLE_KIND
  const kind = forcefield ? 'door' : unstable ? 'spawner' : requested
  const obj = blank(kind, at)
  if (unstable) {
    obj.unstable = true
    obj.kind = 'ground'
  }
  if (forcefield) {
    obj.cls = 'BP_Door_Forcefield_C'
    //: Sized like the shipped ones. Its mesh is a 100uu plane, so scale 1 is a
    //: 100x100uu speck; a portcullis needs no such thing and must keep scale 1.
    obj.scaleX = 10
    obj.scaleY = 25
    obj.scaleZ = 1
  }
  let exitPoint = null
  if (kind === 'rift') {
    plan.rifts.push(obj)
    exitPoint = giveExit(obj)
  } else if (kind === 'door') {
    plan.doors.push(obj)
    const sp = currentSpawner()
    if (sp && !sp.door) sp.door = obj.id
  } else if (kind === 'spawner') plan.spawners.push(obj)
  else if (kind === 'playerStart') plan.playerStarts.push(obj)
  else {
    // A point joins whatever was selected when it was placed: a point (added or the
    // level's own) branches to it, a spawner with no route of that kind starts at it.
    // Anything else leaves it unlinked until a branch is added.
    const from = selectedPointRef()
    plan.points.push(obj)
    if (from !== null) {
      linkFrom(from, obj.id)
    } else {
      const sp = startingSpawner()
      if (sp) {
        const key = sp.kind === 'ground' ? 'start' : 'flyerStart'
        if (sp.plan) sp.obj[key] = obj.id
        else wiringEntry(sp.name, 'spawner')[key] = obj.id
      }
    }
  }
  selected = { kind, id: obj.id }
  // Everything else that holds a selection has to let go, or the panel can fall back
  // to a stale one and the outline keeps ringing the old object. showProps() only
  // consults wiringPick and picked when it cannot find `selected`, so leaving them
  // set made a near-miss look like the wrong object being selected.
  wiringPick = null
  picked = []
  // Back to selecting once something is placed. Staying in placement mode meant the
  // next click made a second object instead of picking the one just made, so its
  // properties were never reachable by clicking it.
  setMode('none')
  drawPlan()
  refreshPicked()
  // The gizmo has to move to what was just placed. Without this it stayed on the
  // previously selected object, so the handles and the panel disagreed.
  attachTo(selected)
  showProps()
  if (unstable) snapRiftHeight(obj)
  else if (floorSnapOf(obj)) snapRiftHeight(obj, floorSnapOf(obj))
  if (exitPoint) snapRiftHeight(exitPoint, 'point')
}

/** A rift's own exit point (riftExits): the added point with no branches inside it, else
 * one made RIFT_OVER_EXIT below it; null for the first rift taking the level's moved exit.
 * Written into the rift's `exit`. */
function giveExit (rift) {
  if (plan.rifts[0] === rift && movesExit(plan, level)) return null
  for (const [pid, rid] of riftExits(plan, level)) {
    if (rid === rift.id) {
      rift.exit = pid
      return null
    }
  }
  const pt = blank('point', [rift.at[0], rift.at[1],
    Math.round((rift.at[2] - RIFT_OVER_EXIT) * 100) / 100])
  plan.points.push(pt)
  rift.exit = pt.id
  return pt
}

/** Every placed rift with an exit point of its own, named in its `exit` (an older plan's
 * rift takes the added point inside it, else a new one). */
function giveRiftsExits () {
  for (const r of plan.rifts || []) {
    if (r.exit === undefined || r.exit === null) giveExit(r)
  }
}

/** Move a placed rift's exit point with it: by the rift's move from `before`. */
function followRift (rift, before) {
  if (!rift || !before || rift.exit === undefined || rift.exit === null) return
  const pt = (plan.points || []).find(p => p.id === rift.exit)
  if (!pt) return
  pt.at = pt.at.map((v, k) => Math.round((v + rift.at[k] - before[k]) * 100) / 100)
}

/** An added point out of the plan (removePlanPoint); a rift it was the exit point of names
 * none. */
function dropPoint (id) {
  removePlanPoint(plan, id)
  for (const r of plan.rifts || []) if (r.exit === id) r.exit = null
}

/** A placed rift and its exit point out of the plan; branches into the exit point go on
 * to where 'rift' leads. */
function dropRift (id) {
  const rift = plan.rifts.find(r => r.id === id)
  plan.rifts = plan.rifts.filter(r => r.id !== id)
  if (rift && typeof rift.exit === 'number') removePlanPoint(plan, rift.exit)
}

/** Face every plan spawner not turned by hand toward its route's first point (yaw only;
 * actors.js spawnerFacing, as the build writes it). One with no route keeps its yaw. */
function aimSpawners () {
  for (const sp of plan.spawners || []) {
    if (sp.turned) continue
    const yaw = spawnerFacing(plan, level, sp, refLocation)
    if (yaw !== null) sp.rotation = yaw
  }
}

/** Whether plan object `obj` is a ground spawner that is not an unstable rift. */
const isGroundSpawner = (obj) => !!obj && plan.spawners.includes(obj) &&
  obj.kind === 'ground' && !obj.unstable

/** Whether plan object `obj` is a path point ground units walk to: not flyers only, no
 * spawner's flyer start, and no flyers-only point branches to it. */
const isGroundPoint = (obj) => !!obj && (plan.points || []).includes(obj) &&
  !obj.flyersOnly && !plan.spawners.some(sp => sp.flyerStart === obj.id) &&
  !plan.points.some(p => p.flyersOnly && (p.next || []).some(b => b.to === obj.id))

/** The floor snap a plan object takes when placed, moved or given a position: 'spawner'
 * for a ground spawner, 'point' for a ground path point, else null. */
const floorSnapOf = (obj) => isGroundSpawner(obj) ? 'spawner'
  : isGroundPoint(obj) ? 'point' : null

/** The height api/riftfloor gives an object at `at` (`mode` 'spawner', 'point', or '' for
 * an unstable rift) over the floor the plan leaves: {at, floor, polygon, why}. */
const askFloor = (at, mode) => json('api/riftfloor/' + encodeURIComponent(level.level), {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', 'X-Rift-Lord': '1' },
  body: JSON.stringify({
    at,
    for: mode,
    plan: { scratch: plan.scratch, added: plan.added, removed: plan.removed, edits: plan.edits },
  }),
})

/** Set an unstable rift's height to the shipped offset over the walkable floor under it,
 * a ground spawner's (`mode` 'spawner') to navsurfextend.SPAWNER_ABOVE_FLOOR over it, or a
 * ground path point's ('point') to navsurfextend.POINT_ABOVE_FLOOR over the floor a build
 * puts it on (api/riftfloor, riftlord/build/navigation/riftfloor.py), on the floor the plan
 * leaves: a scratch level's own floor pieces, a shipped level's graph with the plan's floor
 * added and removed; left as placed where there is no floor there. */
async function snapRiftHeight (obj, mode = 'rift') {
  if (!level) return
  let got
  try {
    got = await askFloor(obj.at, mode === 'rift' ? '' : mode)
  } catch (err) {
    return
  }
  const found = planObjects().find(o => o.obj === obj)
  if (!got || !got.at || !found) return
  obj.at = [obj.at[0], obj.at[1], got.at[2]]
  drawPlan()
  if (selected && selected.kind === found.kind && selected.id === obj.id) {
    attachTo(selected)
    showProps()
  }
}

function removeSelected () {
  const found = findObject(selected)
  if (!found) return
  snapshot()
  if (found.kind === 'rift') {
    if (plan.rifts[0] === found.obj && movesExit(plan, level)) {
      history.pop()
      flash('This rift is the level’s exit now. Bring the exit point back before ' +
            'removing it.')
      return
    }
    if (riftsInPlay() <= 1) {
      history.pop()
      flash('A level needs one rift in play. Place another, or bring back the ' +
            'original rift, before removing this one.')
      return
    }
    dropRift(found.obj.id)
  } else if (found.kind === 'door') {
    plan.doors = plan.doors.filter(d => d.id !== found.obj.id)
    for (const sp of plan.spawners) if (sp.door === found.obj.id) sp.door = null
    for (const w of plan.wiring || []) if (w.door === found.obj.id) w.door = null
  } else if (found.kind === 'spawner') {
    plan.spawners = plan.spawners.filter(sp => sp.id !== found.obj.id)
  } else if (found.kind === 'playerStart') {
    const keep = plan.playerStarts.filter(s => s.id !== found.obj.id)
    const cover = startsCover({ ...plan, playerStarts: keep }, level)
    if (!cover.breaks || !cover.waves) {
      history.pop()
      flash('Keep at least one player start for between waves and one for during waves.')
      return
    }
    plan.playerStarts = keep
  } else {
    dropPoint(found.obj.id)
  }
  selected = null
  gizmo.detach()
  drawPlan()
  showProps()
}


/** Copy a dragged marker back into the object it stands for. */
function writeBackSelected () {
  if (!gizmo.object || !gizmo.object.userData.plan) return
  const found = findObject(gizmo.object.userData)
  if (!found) return
  const p = gizmo.object.position
  const before = found.obj.at.slice()
  found.obj.at = [p.x, p.y, p.z].map(v => Math.round(v * 100) / 100)
  if (found.kind === 'rift') followRift(found.obj, before)
  if ('rotation' in found.obj) {
    found.obj.rotation =
      Math.round(THREE.MathUtils.radToDeg(gizmo.object.rotation.z))
  }
  //: a spawner turned by hand keeps its yaw (aimSpawners)
  if (gizmo.mode === 'rotate' && found.kind === 'spawner') found.obj.turned = true
  //: A DRAGGED SIZE, mapped through THAT CLASS's own mesh rotation.
  //
  // A door's mesh is held at an angle inside its Blueprint, so the marker's axes are
  // not the mesh's and writing the marker's scale straight into scaleX/Y/Z resizes the
  // wrong edge. This was worked out by hand for the force field, whose pane sits at
  // (-90, 0, 90) and whose x/y/z therefore run along the marker's z/x/y. Derived per
  // class instead: the same rule gives [2, 0, 1] for the force field -- exactly the
  // hand-computed mapping -- and the identity for the portcullis classes, which hold
  // their mesh square.
  if (gizmo.mode === 'scale' && found.kind === 'door') {
    const sc = [gizmo.object.scale.x, gizmo.object.scale.y, gizmo.object.scale.z]
    const map = doorAxes.get(found.obj.cls) || [0, 1, 2]
    const round = (v) => Math.max(0.1, Math.round(v * 100) / 100)
    found.obj.scaleX = round(scaleBase[0] * sc[map[0]])
    found.obj.scaleY = round(scaleBase[1] * sc[map[1]])
    found.obj.scaleZ = round(scaleBase[2] * sc[map[2]])
  }
}

//: Which marker axis stretches which of a door mesh's own axes, per door class.
const doorAxes = new Map()

//: What each kind is placed as, for naming it. A door and a spawner carry their own
//: class in the plan; a rift and a path point have exactly one each.
const CLASS_OF = { rift: 'BP_OMDRift_C', point: 'OMDPathPoint' }

/** The mesh a class draws as, from the parts cache. Null until that is filled.
 *
 * Read rather than fetched, because the readout and the panel are drawn
 * synchronously. `boot` primes the cache for every class the plan can place.
 */
function classMesh (cls) {
  const parts = partsCache.get(cls)
  if (!parts || !parts.length) return null
  const main = parts.find(p => p.component === 'DoorMesh') || parts[0]
  return (main && main.mesh) || null
}

/** Work that mapping out from the class's `DoorMesh` component. */
function doorAxisMap (parts) {
  //: The `DoorMesh` component by name, which is the one the emitter writes the scale
  //: to. BP_Door_Exterior_3W_C has two components held at different angles, so taking
  //: the first would have mapped the axes of its force-field pane onto its portcullis.
  const part = (parts || []).find(p => p.component === 'DoorMesh') || (parts || [])[0]
  const rot = (part && part.rot) || [0, 0, 0]
  //: through `partLocal`, so this cannot disagree with how the mesh is drawn
  const e = partLocal({ loc: [0, 0, 0], rot: rot, scale: [1, 1, 1] }).elements
  const out = []
  for (let i = 0; i < 3; i += 1) {
    const col = [Math.abs(e[i * 4]), Math.abs(e[i * 4 + 1]), Math.abs(e[i * 4 + 2])]
    out.push(col.indexOf(Math.max(...col)))
  }
  return out
}

//: The size a force field had when a scale drag began. The marker is rebuilt at
//: scale 1 on every redraw, so the gizmo's scale is a FACTOR for this drag, not an
//: absolute -- without a baseline each frame would multiply the last one again.
let scaleBase = [1, 1, 1]

function raiseSelected (by) {
  const found = findObject(selected)
  if (!found) return false
  snapshot()
  found.obj.at[2] = Math.round(found.obj.at[2] + by)
  drawPlan()
  showProps()
  return true
}

// -- undo --------------------------------------------------------------------

//: Snapshots of the plan, taken before each change. Shallow copies would share the
//: objects being edited, so each one is a full clone.
const history = []
const HISTORY_MAX = 60

function snapshot () {
  changes += 1
  history.push(JSON.stringify(plan))
  if (history.length > HISTORY_MAX) history.shift()
}

function undo () {
  if (!history.length) {
    flash('Nothing to undo.')
    return
  }
  plan = JSON.parse(history.pop())
  fxPick = null
  selected = null
  picked = []
  gizmo.detach()
  drawPlan()
  showProps()
  // The level's own geometry has to be put back too: moving and removing map assets
  // are undoable edits like any other, and restoring only the plan left the level
  // looking however the undone edit had left it.
  applyEdits()
  refreshPicked()
  redrawAdded()
  previewPlanes().catch(err => report('liquid', err))
  flash('Undone.')
}

async function redrawAdded () {
  //: `detach`, because an added mesh's geometry is shared out of `geoCache`
  detach(groups.newGeo)
  for (const entry of plan.added || []) await drawAdded(entry)
}

function flash (text) {
  el('saved').textContent = text
}

// -- the properties panel ----------------------------------------------------

//: How a field is labelled and stepped. The names come from the game's own
//: properties, which are not written for a sidebar.
const FIELD_LABEL = {
  rotation: ['yaw', 15], index: ['index in level', 1],
  maxSpawnPerRow: ['max per row', 1], cls: ['class', 0],
  health: ['health', 100], slotSpacing: ['slot spacing', 10],
  reachRadius: ['reach radius', 50], formationSize: ['formation size', 1],
  formationSpacing: ['formation spacing', 0.5],
  flightHeight: ['flight height', 100], flyersOnly: ['flyers only', 0],
  groundOnly: ['ground only', 0], isStart: ['start point', 0],
  isExit: ['exit point', 0], kind: ['kind', 0],
}

//: How each real property is labelled and stepped, and a line saying what it does.
//: The names are the game's own, which were not written for a sidebar.
const FIELD = {
  rotation: ['yaw', 15, 'degrees about the world up'],
  kind: ['route', 0, 'a flyer ignores the navmesh; a ground unit needs covered floor'],
  index: ['index in level', 1, 'what an onslaught wave names to spawn here'],
  maxSpawnPerRow: ['max per row', 1, 'how many abreast come through (4-7 shipped)'],
  cls: ['door class', 0, 'interior doors have health; a force field does not'],
  health: ['health', 100, 'damage the door takes before it breaks'],
  slotSpacing: ['slot spacing', 10, 'how far apart minions queue at it'],
  reachRadius: ['reach radius', 50, 'how close counts as arrived (150-600 shipped)'],
  formationSize: ['formation size', 1, 'how wide the group walks (3 almost always)'],
  formationSpacing: ['formation spacing', 0.5, 'gap between them (0, or 3.5-7)'],
  flightHeight: ['flight height', 100, 'how high flyers cross this point'],
  flyersOnly: ['flyers only', 0, 'ground units ignore this point'],
  groundOnly: ['ground only', 0, 'flyers ignore this point'],
  isStart: ['start point', 0, 'a spawner can begin its route here'],
  isExit: ['exit point', 0, 'reaching this is reaching the rift'],
  unstable: ['unstable rift', 0, ''],
}

//: What each kind of object is, so the panel says what is being edited.
const ABOUT = {
  rift: 'Where this area\u2019s waves end. Routes end at its exit point.',
  spawner: 'Where a wave enters. Its index is what an onslaught names; its routes ' +
           'start at the points chosen below.',
  door: 'What the horde comes through. Interior doors are destructible.',
  point: 'One step of a route. Its branches decide where the wave goes next. Any ' +
         'spawner may start here and any point may lead here.',
  playerStart: 'Where a player appears. The capsule’s nose shows which way they face.',
}

//: What each kind is called in the panel.
const KIND_LABEL = { rift: 'Rift', spawner: 'Spawner', door: 'Door', point: 'Path Point',
                     playerStart: 'Player Start' }
//: The New Object card of an unstable rift, and its panel name.
const UNSTABLE_KIND = 'unstableRift'
//: The New Object card that places a teleporter, and the class it places
//: (riftlord/leveldata.py TELEPORT_CLASS).
const TELEPORTER_KIND = 'teleporter'
//: the New Object card that adds a blocking volume (volumes.js addVolume)
const VOLUME_KIND = 'blockingVolume'
const TELEPORT_CLASS = 'BP_OMDTeleportRift_C'
const UNSTABLE_LABEL = 'Unstable Rift'

function field (obj, name, spec, fields) {
  const [label, step, help] = FIELD[name] || [name, 1, '']
  const hint = help ? `<div class="hint">${help}</div>` : ''
  if (spec.kind === 'bool') {
    return `<label class="row"><span>${label}</span>` +
      `<input data-field="${name}" type="checkbox"${obj[name] ? ' checked' : ''}>` +
      `</label>${hint}`
  }
  if (name === 'cls') {
    return `<div class="field"><label>${label}</label>` +
      `<select data-field="${name}">${(schema.doors || []).map(c =>
        `<option value="${c}"${c === obj[name] ? ' selected' : ''}>` +
        `${c.replace(/^BP_Door_|_C$/g, '')}</option>`).join('')}</select></div>${hint}`
  }
  if (spec.kind === 'choice') {
    return `<div class="field"><label>${label}</label>` +
      `<select data-field="${name}">${(schema.kinds || []).map(c =>
        `<option value="${c}"${c === obj[name] ? ' selected' : ''}>${c}</option>`)
        .join('')}</select></div>${hint}`
  }
  return `<div class="field"><label>${label}</label>` +
    `<input data-field="${name}" type="number" step="${step || 1}"` +
    ` value="${obj[name]}"></div>${hint}`
}

/** The branch editor: where this point sends a wave next, and how often.
 *
 * A branch's weight is a probability, and every fork in every shipped level sums to
 * exactly 1.00. The length the game also stores is just the distance between the two
 * points, so it is never typed here. */
function branchEditor (list, self) {
  const total = list.reduce((sum, b) => sum + b.weight, 0)
  const rows = list.map((b, i) =>
    '<div class="branch">' +
    `<select data-branch="${i}" data-part="to">${refOptions(b.to, self, true)}</select>` +
    '<div class="field"><label>Weight</label>' +
    `<input data-branch="${i}" data-part="weight" type="number" step="0.1"` +
    ` min="0" max="1" value="${b.weight}">` +
    `<button data-dropbranch="${i}" style="width:auto;padding:2px 8px;margin:0">` +
    'Remove</button></div></div>').join('')
  const [low, high] = SHIPPED_WEIGHT_SUM
  const warn = list.length && (total < low - 0.01 || total > high + 0.01)
    ? `<div class="hint" style="color:#ffb03a">Weights add up to ${total.toFixed(2)}; ` +
      `shipped levels use ${low.toFixed(1)} to ${high.toFixed(1)}.</div>`
    : ''
  const report = graphReport(plan, level)
  const key = report.key(self)
  const notes = report.stranded.has(key)
    ? '<div class="hint bad">Enemies cannot reach the rift from this point.</div>'
    : ''
  const empty = `None: enemies go on to ${escapeText(riftName('rift'))}.`
  return '<h2>Goes Next To</h2>' + (rows || `<div class="hint">${empty}</div>`) + warn +
    notes +
    '<div class="grid2"><button id="addbranch">Add Branch</button>' +
    '<button id="evenbranch">Even Weights</button></div>'
}

/** <option>s for a point Ref: (optionally) the rift, the plan's points, the level's. */
function refOptions (current, self, withRift) {
  const opt = (value, label) =>
    `<option value="${escapeText(value)}"${String(value) === String(current) ? ' selected' : ''}>` +
    `${escapeText(label)}</option>`
  const exits = riftExits(plan, level)
  const mine = (plan.points || []).filter(p => p.id !== self)
  const own = Object.values((level && level.points) || {})
    .filter(p => p.export !== self && !isGone(plan, p.export))
    .sort((a, b) => (a.index || 0) - (b.index || 0))
  //: with rifts placed, each rift's exit point is a choice of its own
  const to = riftTarget(plan, level)
  const rifts = withRift && exits.size
    ? '<optgroup label="Rifts">' + opt('rift', riftName('rift')) +
      [...exits].filter(([pid]) => pid !== to || String(current) === String(pid))
        .map(([pid, rid]) => opt(pid, 'Rift ' + rid)).join('') + '</optgroup>'
    : withRift ? opt('rift', 'The Rift (Level Exit)') : opt('', 'None')
  return rifts +
    (mine.length
      ? '<optgroup label="Points You Placed">' +
        mine.map(p => opt(p.id, 'Point ' + p.id +
          (exits.has(p.id) ? ` (Rift ${exits.get(p.id)} Exit)` : ''))).join('') +
        '</optgroup>'
      : '') +
    (own.length
      ? '<optgroup label="Original Points">' +
        own.map(p => opt(p.export, p.export + (p.exit ? ' (Exit)' : ''))).join('') +
        '</optgroup>'
      : '')
}

/** What a route end is called: 'rift' (where it leads), a rift's own exit point, or the
 * level's exit point. */
function riftName (ref) {
  if (ref === 'rift') ref = riftTarget(plan, level)
  const rid = riftExits(plan, level).get(ref)
  if (rid !== undefined) return 'Rift ' + rid
  if (movesExit(plan, level)) return 'Rift ' + plan.rifts[0].id
  return riftExits(plan, level).size ? 'Original Rift' : 'the level’s exit (the rift)'
}

/** The rifts the routes from these Refs end at, as a panel line; nothing for no Refs. */
function endsAt (refs) {
  const reached = riftsReached(plan, level)
  const { key } = pathGraph(plan, level)
  const got = new Set()
  for (const ref of refs) {
    if (ref === null || ref === undefined) continue
    for (const r of reached.get(key(ref)) || []) got.add(r)
  }
  if (!refs.some(r => r !== null && r !== undefined)) return ''
  const names = [...got].map(r => r === 'level' ? riftName(exitName(level)) : 'Rift ' + r)
  return '<div class="field"><label>Ends At</label>' + (names.length
    ? `<span data-endsat>${escapeText(names.join(', '))}</span>`
    : '<span data-endsat style="color:#e05252">no rift</span>') + '</div>'
}

/** <option>s for a spawner's door: none, the plan's doors, the level's own. */
function doorOptions (current) {
  const opt = (value, label) =>
    `<option value="${escapeText(value)}"${String(value) === String(current ?? '') ? ' selected' : ''}>` +
    `${escapeText(label)}</option>`
  const own = Object.values((level && level.doors) || {}).filter(d => !isGone(plan, d.export))
  return opt('', 'None') +
    (plan.doors.length
      ? '<optgroup label="Doors You Placed">' +
        plan.doors.map(d => opt(d.id, 'Door ' + d.id)).join('') + '</optgroup>'
      : '') +
    (own.length
      ? '<optgroup label="Original Doors">' +
        own.map(d => opt(d.export, d.export)).join('') + '</optgroup>'
      : '')
}

/** A spawner's Door, Ground Route and Flyer Route pickers. `get(key)` reads a value. */
function routeSection (get, unstable) {
  if (unstable) {
    return '<h2>Routes</h2><label class="pick"><span>Ground Route Starts At</span>' +
      `<select data-route="start">${refOptions(get('start'), null, false)}</select></label>` +
      endsAt([get('start')])
  }
  return '<h2>Routes</h2>' +
    '<label class="pick"><span>Door</span>' +
    `<select data-route="door">${doorOptions(get('door'))}</select></label>` +
    '<label class="pick"><span>Ground Route Starts At</span>' +
    `<select data-route="start">${refOptions(get('start'), null, false)}</select></label>` +
    '<label class="pick"><span>Flyer Route Starts At</span>' +
    `<select data-route="flyerStart">${refOptions(get('flyerStart'), null, false)}` +
    '</select></label>' + endsAt([get('start'), get('flyerStart')]) +
    '<div class="hint">A spawner with no door has spawn invulnerability switched ' +
    'off, as the shipped doorless spawners do.</div>'
}

/** Wire routeSection's pickers; `set(key, value)` stores a change. */
function wireRoutes (body, set) {
  for (const node of body.querySelectorAll('[data-route]')) {
    node.addEventListener('change', () => {
      snapshot()
      set(node.dataset.route, asRef(node.value))
      drawPlan()
      showProps()
    })
  }
}

/** Wire branchEditor's controls. `get()` returns the list, `set(list)` stores it. */
function wireBranches (body, get, set) {
  const edit = (fn) => {
    snapshot()
    const list = get().map(b => ({ ...b }))
    fn(list)
    set(list)
    drawPlan()
    showProps()
  }
  for (const control of body.querySelectorAll('[data-branch]')) {
    control.addEventListener('change', (ev) => edit((list) => {
      const b = list[+ev.target.dataset.branch]
      if (ev.target.dataset.part === 'to') b.to = asRef(ev.target.value)
      else b.weight = +ev.target.value
    }))
  }
  for (const button of body.querySelectorAll('[data-dropbranch]')) {
    button.addEventListener('click', (ev) => edit((list) =>
      list.splice(+ev.currentTarget.dataset.dropbranch, 1)))
  }
  if (el('addbranch')) {
    el('addbranch').addEventListener('click', () => edit((list) => {
      list.push({ to: 'rift', weight: 1.0 })
      evenBranches({ next: list })
    }))
  }
  if (el('evenbranch')) {
    el('evenbranch').addEventListener('click', () => edit((list) =>
      evenBranches({ next: list })))
  }
}

/** Which spawners start a route at this point, as a line for the panel. */
function startedBy (ref) {
  const names = []
  for (const sp of plan.spawners) {
    if (sp.start === ref) names.push('spawner ' + sp.id + ' (ground)')
    if (sp.flyerStart === ref) names.push('spawner ' + sp.id + ' (flyer)')
  }
  for (const s of (level && level.spawners) || []) {
    const w = wiringOf(plan, s.export) || {}
    if (w.gone) continue
    const start = 'start' in w ? w.start : (s.start || '').split('.').pop() || null
    const flyer = 'flyerStart' in w ? w.flyerStart : (s.flyer || '').split('.').pop() || null
    if (start === ref) names.push(s.export + ' (ground)')
    if (flyer === ref) names.push(s.export + ' (flyer)')
  }
  return names.length
    ? `<div class="hint">Route start for ${escapeText(names.join(', '))}.</div>` : ''
}

/** A player start's phase picker. */
function phaseSection (phase) {
  return '<label class="pick"><span>Used</span><select data-phase="1">' +
    PHASES.map(([v, label]) =>
      `<option value="${v}"${v === phase ? ' selected' : ''}>${label}</option>`).join('') +
    '</select></label>' +
    `<div class="hint">${escapeText((PHASES.find(([v]) => v === phase) || [])[2] || '')}` +
    '</div>'
}

/** The viewport's upper-left line, which follows whatever is selected.
 *
 * It used to be written only where a level piece or the ground was clicked, so it
 * went stale the moment anything else happened: deselecting left the last thing's
 * details on screen, and selecting something you had placed left the PREVIOUS
 * thing's. Driven from `showProps`, which every selection path already calls, so
 * there is one source of truth instead of three writers and no clearer.
 */
function updateReadout () {
  const box = el('readout')
  if (!box) return
  if (wiringPick) {
    const d = wiringPick.userData
    box.innerHTML = `<b>${d.name}</b> &middot; original ${d.kind}`
    return
  }
  const found = findObject(selected)
  if (found) {
    //: NAME IT. This read `found.cls`, which `planObjects` does not set -- it carries
    //: {kind, obj, owner} -- so the class was never shown and a placed object was only
    //: ever "door 3". The class lives on the object, and the mesh it draws as comes
    //: from the class's own components.
    const cls = found.obj.cls || CLASS_OF[selected.kind] || null
    const mesh = cls ? classMesh(cls) : null
    box.innerHTML = `<b>${mesh || cls || selected.kind}</b> &middot; ` +
      `${selected.kind} ${selected.id}` +
      (mesh && cls ? ` &middot; ${cls}` : '')
    return
  }
  if (picked.length === 1) {
    const p = picked[0]
    box.innerHTML = `<b>${escapeText(p.mesh)}</b>` +
      (p.added || p.levelActor ? '' : ` &middot; piece ${p.id}`) +
      (p.actor ? ` of ${p.actor}` : '')
    return
  }
  if (picked.length) {
    //: One assembly reads as what it is, not as a count: clicking a single wall
    //: selecting "16 pieces" looked like a selection bug rather than one actor.
    const one = picked[0]
    const same = picked.every(p => p.export !== undefined &&
                                   p.export === one.export)
    box.innerHTML = same && one.actor
      ? `<b>${one.actor}</b> &middot; ${picked.length} parts, one actor`
      : `<b>${picked.length} pieces</b> selected`
    return
  }
  box.innerHTML = ''
}

function showProps () {
  updateReadout()
  const body = el('propsbody')
  const app = el('app')
  const found = findObject(selected)
  //: a Crystal group waiting for a rift takes the clicked unstable rift, and the Crystal's
  //: panel comes back (crystalui.js)
  if (found && crystalPicking() && takeCrystalPick(found, plan, snapshot)) {
    selected = null
    picked = [addedRecord(planCrystal(plan))]
    refreshPicked()
    return showProps()
  }
  //: a selection takes the sidebar back from the Mission Events list
  if (found || wiringPick || picked.length || !level) eventsOpen = false
  if (eventsOpen) return showEventsPanel()
  if (decor.panel(!!(found || wiringPick || picked.length || !level))) return
  el('propshead').textContent = found || wiringPick || fxPick || picked.length || !level
    ? 'Selected' : 'Map Settings'
  if (!found) {
    if (wiringPick) {
      return wiringPick.userData.kind === 'volume'
        ? showVolumeProps(wiringPick) : showWiringProps()
    }
    if (picked.length) return showPickedProps()
    if (level) return showMapSettings()
    app.classList.remove('props')
    body.innerHTML = ''
    return
  }
  app.classList.add('props')
  const { kind, obj } = found
  const fields = (schema.schema && schema.schema[kind]) || {}
  const cls = obj.cls || CLASS_OF[kind] || null
  const mesh = cls ? classMesh(cls) : null
  const label = kind === 'spawner' && obj.unstable ? UNSTABLE_LABEL : KIND_LABEL[kind] || kind
  //: an Unstable Rift is numbered by its index in level, as the Onslaught Editor numbers it
  const number = kind === 'spawner' && obj.unstable && obj.index ? obj.index : obj.id
  const rows = [`<div class="card sel"><b>${mesh || cls || label} ${number}</b>` +
    '<div class="meta">' + [
      label + ' ' + number,
      mesh && cls ? cls : null,
    ].filter(Boolean).join(' &middot; ') + '</div></div>',
    `<div class="hint">${ABOUT[kind] || ''}</div>`]
  if (kind === 'point') rows.push(startedBy(obj.id))

  rows.push('<h2>Handles</h2><div id="gizmomode">' +
    `<button data-gmode="translate"${gizmo.mode === 'translate' ? ' class="on"' : ''}>move</button>` +
    `<button data-gmode="rotate"${gizmo.mode === 'rotate' ? ' class="on"' : ''}>turn</button>` +
    //: Every DOOR has a size -- a portcullis that does not quite fill its archway is
    //: the ordinary reason to want one. A spawner, a point and a rift have no size the
    //: game reads, so they still get only move and turn.
    (kind === 'door'
      ? `<button data-gmode="scale"${gizmo.mode === 'scale' ? ' class="on"' : ''}>size</button>`
      : '') +
    '</div>')
  rows.push('<h2>Position</h2>' + ['x', 'y', 'z'].map((axis, i) =>
    `<div class="field"><label>${axis.toUpperCase()}</label>` +
    `<input data-at="${i}" type="number" value="${Math.round(obj.at[i])}">` +
    '</div>').join(''))

  // Only the fields this class actually reads: a force field door has no health, so
  // offering one would be inventing a setting.
  const shown = Object.entries(fields).filter(([name]) => {
    if (name === 'isStart' || name === 'isExit' || name === 'unstable') return false
    if (name === 'kind' && obj.unstable) return false
    const only = (schema.onlyFor || {})[name]
    return !only || only.includes(obj.cls)
  })
  if (shown.length) {
    rows.push('<h2>Settings</h2>')
    for (const [name, spec] of shown) rows.push(field(obj, name, spec, fields))
  }
  if (kind === 'playerStart') {
    rows.push('<h2>Settings</h2>' + field(obj, 'rotation', { kind: 'float' }, {}) +
      phaseSection(obj.phase))
  }

  if (kind === 'spawner') rows.push(routeSection((k) => obj[k], obj.unstable))
  const exitOf = kind === 'point' ? riftExits(plan, level).get(obj.id) : undefined
  if (exitOf !== undefined) {
    rows.push('<h2>Exit Point</h2><div class="field"><label>Rift</label>' +
      `<span data-exitof>Rift ${exitOf}</span></div>`)
  } else if (kind === 'point') rows.push(branchEditor(obj.next || [], obj.id))
  if (kind === 'rift') rows.push(exitSection(obj))

  rows.push('<h2></h2><button id="removesel">Delete</button>')
  body.innerHTML = rows.join('')

  el('gizmomode').addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-gmode]')
    if (!b) return
    gizmo.setMode(b.dataset.gmode)
    showProps()
  })
  for (const input of body.querySelectorAll('[data-at]')) {
    input.addEventListener('change', (ev) => {
      snapshot()
      const before = obj.at.slice()
      obj.at[+ev.target.dataset.at] = +ev.target.value
      if (kind === 'rift') followRift(obj, before)
      drawPlan()
      //: a ground spawner or ground path point given a position stands on its floor
      //: (shift: as typed)
      if (!shiftDown && floorSnapOf(obj)) snapRiftHeight(obj, floorSnapOf(obj))
    })
  }
  for (const input of body.querySelectorAll('[data-field]')) {
    input.addEventListener('change', (ev) => {
      snapshot()
      const name = ev.target.dataset.field
      const spec = fields[name] || { kind: 'float' }
      obj[name] = ev.target.type === 'checkbox' ? ev.target.checked
        : (spec.kind === 'int' || spec.kind === 'float') ? +ev.target.value
          : ev.target.value
      //: a spawner given a yaw by hand keeps it (aimSpawners)
      if (kind === 'spawner' && name === 'rotation') obj.turned = true
      drawPlan()
      showProps()
      //: a spawner made a ground spawner stands on its floor
      if (name === 'kind' && isGroundSpawner(obj)) snapRiftHeight(obj, 'spawner')
    })
  }
  if (kind === 'spawner') wireRoutes(body, (k, v) => { obj[k] = v })
  if (kind === 'point') {
    wireBranches(body, () => obj.next || [], (list) => { obj.next = list })
  }
  const exitPick = body.querySelector('[data-riftexit]')
  if (exitPick) {
    exitPick.addEventListener('change', () => {
      snapshot()
      obj.exit = asRef(exitPick.value)
      drawPlan()
      showProps()
    })
  }
  const phase = body.querySelector('[data-phase]')
  if (phase) {
    phase.addEventListener('change', () => {
      const cover = startsCover({ ...plan, playerStarts: plan.playerStarts.map(s =>
        s === obj ? { ...s, phase: phase.value } : s) }, level)
      if (!cover.breaks || !cover.waves) {
        flash('Keep at least one player start for between waves and one for during waves.')
        showProps()
        return
      }
      snapshot()
      obj.phase = phase.value
      showProps()
    })
  }
  el('removesel').addEventListener('click', () => removeSelected())
  if (kind === 'rift') reconnectSection(body)
}

/** A placed rift's Exit Point picker: the added points with no branches that no other rift
 * ends at, and its own. */
function exitSection (rift) {
  if (plan.rifts[0] === rift && movesExit(plan, level)) {
    return '<h2>Exit Point</h2><div class="field"><label>Point</label>' +
      `<span data-riftexitname>${escapeText(exitName(level))}</span></div>`
  }
  const taken = new Map([...riftExits(plan, level)].filter(([, rid]) => rid !== rift.id))
  const free = (plan.points || []).filter(p => !taken.has(p.id) &&
    (!(p.next || []).length || p.id === rift.exit))
  const opt = (p) => `<option value="${p.id}"${p.id === rift.exit ? ' selected' : ''}>` +
    `Point ${p.id}</option>`
  const missing = !free.some(p => p.id === rift.exit)
  return '<h2>Exit Point</h2><label class="pick"><span>Point</span>' +
    '<select data-riftexit>' + (missing ? '<option value="" selected>None</option>' : '') +
    free.map(opt).join('') + '</select></label>' +
    (missing ? '<div class="hint bad">No route can end at this rift.</div>' : '')
}

/** Split a point's weights evenly, which is what a fork sums to in the shipped data. */
function evenBranches (pt) {
  const n = (pt.next || []).length
  if (!n) return
  const share = Math.round((1 / n) * 100) / 100
  pt.next.forEach((b, i) => {
    b.weight = i === n - 1
      ? Math.round((1 - share * (n - 1)) * 100) / 100
      : share
  })
}

/** The panel for one of the level's own gameplay actors: its shipped values, and the
 * edits the plan makes to it (place, facing, routes, branches, phase, removal). */
function showWiringProps () {
  const body = el('propsbody')
  el('app').classList.add('props')
  const d = wiringPick.userData
  const info = d.data || {}
  const w = wiringOf(plan, d.name) || {}
  const at = [wiringPick.position.x, wiringPick.position.y, wiringPick.position.z]
  const label = KIND_LABEL[d.kind] || d.kind
  const turnable = typeof d.yaw === 'number'
  const rows = [`<div class="card sel"><b>${escapeText(d.name)}</b>` +
    `<div class="meta">Original ${label.toLowerCase()}</div></div>`]
  if (d.kind === 'point') rows.push(startedBy(d.name))
  rows.push('<h2>Handles</h2><div id="gizmomode">' +
    `<button data-gmode="translate"${gizmo.mode === 'translate' ? ' class="on"' : ''}>move</button>` +
    (turnable
      ? `<button data-gmode="rotate"${gizmo.mode === 'rotate' ? ' class="on"' : ''}>turn</button>`
      : '') +
    '</div>')
  rows.push('<h2>Position</h2>' + ['x', 'y', 'z'].map((axis, i) =>
    `<div class="field"><label>${axis.toUpperCase()}</label>` +
    `<input data-wat="${i}" type="number" value="${Math.round(at[i])}"></div>`).join(''))
  if (turnable) {
    const yaw = typeof w.rotation === 'number' ? w.rotation : d.yaw
    rows.push(`<div class="field"><label>Yaw</label><input data-wyaw="1" type="number" ` +
      `step="15" value="${Math.round(yaw)}"></div>`)
  }
  if (d.kind === 'spawner') {
    const shipped = { door: (info.door || '').split('.').pop() || null,
                      start: (info.start || '').split('.').pop() || null,
                      flyerStart: (info.flyer || '').split('.').pop() || null }
    rows.push(routeSection((k) => (k in w ? w[k] : shipped[k])))
  }
  if (d.kind === 'point') {
    rows.push(branchEditor(nextOf(plan, level, d.name), d.name))
    if (Array.isArray(w.next)) {
      rows.push('<button id="shippednext">Reset Branches</button>')
    }
  }
  if (d.kind === 'playerStart') rows.push('<h2>Settings</h2>' + phaseSection(w.phase || info.phase))
  const facts = []
  if (info.index !== undefined && info.index !== null) {
    facts.push(['index in level', info.index])
  }
  if (d.kind === 'point') {
    if (info.reachRadius !== undefined) facts.push(['reach radius', info.reachRadius])
    if (info.formationSize !== undefined) {
      facts.push(['formation size', info.formationSize])
    }
    if (info.formationSpacing !== undefined) {
      facts.push(['formation spacing', info.formationSpacing])
    }
    if (info.flightHeight !== undefined) {
      facts.push(['flight height', info.flightHeight])
    }
    if (info.start) facts.push(['start point', 'yes'])
    if (info.exit) facts.push(['exit point', 'yes'])
    if (info.groundOnly) facts.push(['ground only', 'yes'])
    if (info.flyersOnly) facts.push(['flyers only', 'yes'])
  }
  if (d.kind === 'spawner' && info.maxRow) facts.push(['max per row', info.maxRow])
  if (d.kind === 'door' && info.cls) facts.push(['class', String(info.cls).replace(/^BP_Door_|_C$/g, '')])
  rows.push('<h2>As Shipped</h2>' + (facts.length
    ? facts.map(([k, v]) =>
      `<div class="row"><span>${k}</span><span>${escapeText(v)}</span></div>`).join('')
    : '<div class="hint">No properties beyond its place.</div>'))
  if (Object.keys(w).some(k => k !== 'name' && k !== 'kind')) {
    rows.push('<button id="restorewiring">Reset</button>')
  }
  rows.push('<button id="removewiring">Delete</button>')
  body.innerHTML = rows.join('')
  el('gizmomode').addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-gmode]')
    if (!b) return
    gizmo.setMode(b.dataset.gmode)
    showProps()
  })
  for (const input of body.querySelectorAll('[data-wat]')) {
    input.addEventListener('change', () => {
      snapshot()
      const e = wiringEntry(d.name, d.kind)
      const now = (e.at || at).slice()
      now[+input.dataset.wat] = +input.value
      e.at = now
      drawPlan()
      showProps()
    })
  }
  const yaw = body.querySelector('[data-wyaw]')
  if (yaw) {
    yaw.addEventListener('change', () => {
      snapshot()
      wiringEntry(d.name, d.kind).rotation = +yaw.value
      drawPlan()
      showProps()
    })
  }
  if (d.kind === 'spawner') {
    wireRoutes(body, (k, v) => { wiringEntry(d.name, d.kind)[k] = v })
  }
  if (d.kind === 'point') {
    wireBranches(body, () => nextOf(plan, level, d.name),
      (list) => { wiringEntry(d.name, d.kind).next = list })
    if (el('shippednext')) {
      el('shippednext').addEventListener('click', () => {
        snapshot()
        delete wiringEntry(d.name, d.kind).next
        drawPlan()
        showProps()
      })
    }
  }
  const phase = body.querySelector('[data-phase]')
  if (phase) {
    phase.addEventListener('change', () => {
      const trial = { ...plan, wiring: (plan.wiring || []).filter(o => o.name !== d.name)
        .concat([{ ...w, name: d.name, kind: d.kind, phase: phase.value }]) }
      const cover = startsCover(trial, level)
      if (!cover.breaks || !cover.waves) {
        flash('Keep at least one player start for between waves and one for during waves.')
        showProps()
        return
      }
      snapshot()
      wiringEntry(d.name, d.kind).phase = phase.value
      showProps()
    })
  }
  if (el('restorewiring')) {
    el('restorewiring').addEventListener('click', () => {
      snapshot()
      plan.wiring = plan.wiring.filter(o => o.name !== d.name)
      wiringPick.position.set(...(info.location || info.loc || at))
      if (typeof d.yaw === 'number') wiringPick.rotation.z = THREE.MathUtils.degToRad(d.yaw)
      drawPlan()
      refreshPicked()
      showProps()
    })
  }
  el('removewiring').addEventListener('click', () => removeWiringPick())
  if (d.kind === 'rift' || (d.kind === 'point' && info.exit)) reconnectSection(body)
  gizmo.attach(wiringPick)
}

/** Copy a dragged level actor's place (and facing) into the plan. */
function recordWiringMove () {
  if (wiringPick.userData.kind === 'volume') return recordVolumeMove(wiringPick)
  const d = wiringPick.userData
  const e = wiringEntry(d.name, d.kind)
  const p = wiringPick.position
  e.at = [p.x, p.y, p.z].map(v => Math.round(v * 100) / 100)
  if (typeof d.yaw === 'number' && gizmo.mode === 'rotate') {
    e.rotation = Math.round(THREE.MathUtils.radToDeg(wiringPick.rotation.z))
  }
  drawLevelRoutes()
  refreshPicked()
}

/** Why one of the level's own actors may not be removed, or null. */
function cannotRemove (d) {
  if (d.kind === 'point' && d.data && d.data.exit && !plan.rifts.length) {
    return 'Place a rift first: removing the exit point moves the exit to the rift ' +
           'you placed. Every rift needs an exit.'
  }
  if (d.kind === 'rift' && riftsInPlay() <= 1) {
    return 'A level needs one rift in play. Place another before removing this one.'
  }
  if (d.kind === 'playerStart') {
    const trial = { ...plan, wiring: (plan.wiring || []).filter(o => o.name !== d.name)
      .concat([{ name: d.name, kind: d.kind, gone: true }]) }
    const cover = startsCover(trial, level)
    if (!cover.breaks || !cover.waves) {
      return 'Keep at least one player start for between waves and one for during waves.'
    }
  }
  return null
}

/** The panel for a picked piece of the level's own geometry. */
function showPickedProps () {
  const body = el('propsbody')
  el('app').classList.add('props')
  const one = picked[0]
  body.innerHTML =
    `<div class="card sel"><b>${one.mesh}</b><div class="meta">` +
    `${picked.length > 1
       ? (picked.every(p => p.export !== undefined && p.export === one.export)
          ? picked.length + ' parts of one actor'
          : picked.some(p => p.marker)
            ? `${picked.filter(p => !p.marker).length} piece(s) and ` +
              `${picked.filter(p => p.marker).length} marker(s) picked`
            : picked.length + ' pieces picked')
       : (one.levelActor ? (one.kind === 'light' ? 'original light'
         : one.sound ? 'original sound' : 'original effect')
         : one.added ? '' : 'piece ' + one.id)}` +
    `${one.actor ? (picked.length > 1 || !one.added ? ' &middot; ' : '') + one.actor : ''}</div></div>` +
    '<h2>Handles</h2><div id="gizmomode">' +
    `<button data-gmode="translate"${gizmo.mode === 'translate' ? ' class="on"' : ''}>move</button>` +
    //: the level's own lights and effects are only moved
    (picked.every(p => p.levelActor) ? ''
      : `<button data-gmode="rotate"${gizmo.mode === 'rotate' ? ' class="on"' : ''}>turn</button>` +
        //: Geometry has all three. The emitter decomposes the matrix it is handed, so a
        //: turn and a resize both reach the level. The Crystal is only moved and turned.
        (picked.some(p => isCrystal(p.added)) ? ''
          : `<button data-gmode="scale"${gizmo.mode === 'scale' ? ' class="on"' : ''}>size</button>`)) +
    '</div>' +
    `<h2>Position</h2><div class="hint">${[12, 13, 14]
      .map(i => Math.round(one.matrix[i])).join(', ')}</div>` +
    planeSection(picked.length === 1 ? one : null) +
    paintSection() +
    collisionSection() +
    fxSection(picked.length === 1 ? one : null) +
    teleportSection(picked.length === 1 ? one : null) +
    controllersHtml(picked) +
    eventsSection(picked.length === 1 ? one : null) +
    crystalHtml(picked.length === 1 ? one : null, plan) +
    trapGridSection() +
    (picked.some(p => isCrystal(p.added)) ? '' : flipSection()) +
    levelActorSection() +
    (teleportPartners(picked.filter(p => !p.added)).length
      ? '<div class="hint">A teleporter end: removing it also removes the end it links ' +
        'to.</div>'
      : '') +
    compounds.section(picked) +
    '<h2></h2><button id="removepicked">' +
    'Delete</button>'
  el('gizmomode').addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-gmode]')
    if (!b) return
    gizmo.setMode(b.dataset.gmode)
    showProps()
  })
  el('removepicked').addEventListener('click', () => removePicked())
  if (el('tplink')) {
    el('tplink').addEventListener('change', (ev) => {
      linkTeleporter(one.added, ev.target.value === '' ? null : +ev.target.value)
      showProps()
    })
  }
  wireFabProps(body)
  compounds.wire(body, picked)
  if (el('planeliquid')) {
    el('planeliquid').addEventListener('change', (ev) =>
      setPlaneMaterial(one, ev.target.value && ev.target.value !== levelLiquid()
        ? ev.target.value : null))
  }
  wirePaint(body)
  wireCollision()
  wireEventsList(body)
  wireCrystal(body, picked.length === 1 ? one : null,
    { snapshot, showProps, plan: () => plan,
      load: () => crystalInfo(json, level.level, planRift()),
      fit: (matrix) => crystalFit(json, level.level, matrix) })
  wireControllers(body, picked)
  for (const b of body.querySelectorAll('[data-flip]')) {
    b.addEventListener('click', () => flipPicked(b.dataset.flip))
  }
  if (el('lightcolour')) {
    el('lightcolour').addEventListener('change', (ev) => setLightColour(ev.target.value))
  }
  if (el('lightcolourreset')) {
    el('lightcolourreset').addEventListener('click', () => { setLightColour(null); showProps() })
  }
  if (el('lightsame')) el('lightsame').addEventListener('click', () => pickMarkers(sameLights(one)))
  if (el('levelfxkind')) {
    el('levelfxkind').addEventListener('change', (ev) => { setEffectTemplate(ev.target.value); showProps() })
  }
  if (el('fxsame')) {
    el('fxsame').addEventListener('click', () => {
      const keys = new Set(sameEffects(one))
      pickMarkers(groups.vfx.children.filter(m => m.visible && keys.has(m.userData.levelActor) &&
        !m.userData.sound))
    })
  }
  if (el('fxremoveall')) {
    el('fxremoveall').addEventListener('click', () => {
      const keys = sameEffects(one)
      picked = []
      setRemovedFx(keys)
      gizmo.detach()
      refreshPicked()
      showProps()
    })
  }
  if (el('fxkind')) {
    el('fxkind').addEventListener('change', async (ev) => {
      snapshot()
      const row = catalogue.find(r => r.kind === 'fx' && r.name === ev.target.value)
      one.added.fx = ev.target.value
      one.added.name = (row && row.mesh) || ev.target.value
      await redrawAdded()
      showProps()
    })
  }
}

/** The Trap Grid section for the picked piece or assembly, or '' for a mixed pick. */
function trapGridSection () {
  //: Fab pieces carry suggested grids from their library (fabui.js)
  const fabs = pickedFab()
  if (fabs.length) {
    if (fabs.some(e => !fab.packReady(e.pack))) {
      Promise.all(fabs.map(e => fab.loadPack(e.pack, json))).then((got) => {
        if (got.every(Boolean) && pickedFab().length) showProps()
      })
    }
    return fab.propsHtml(fabs, plan, json)
  }
  const kinds = new Set(picked.filter(p => !p.marker).map(p => p.added
    ? (p.added.kind === 'kit' ? 'kit:' + p.added.name : 'mesh')
    : (p.source === 'blueprint' && p.actor ? 'kit:' + p.actor : 'mesh')))
  if (kinds.size !== 1) return ''
  const kind = [...kinds][0]
  if (kind === 'mesh') return ''
  const cls = kind.slice(4)
  const grids = cachedGrids(cls)
  //: one piece shows its grid as its own scale stretches it
  const one = singleKitUnit()
  if (grids) {
    return trapGridHtml('kit', one ? scaledGrids(grids, one.actor) : grids,
                        groups.trapGrids.visible)
  }
  kitGrids(cls, json).then(() => { if (picked.length) showProps() })
  return trapGridHtml('loading', null, groups.trapGrids.visible)
}

/** The Liquid Material picker for one picked pool plane, or '' for anything else. */
function planeSection (piece) {
  if (!piece || piece.added || !piece.liquid || !paintablePlane(piece)) return ''
  const own = ((plan.look && plan.look.planes) || {})[String(piece.id)] || levelLiquid()
  const base = lookNames.liquid || []
  const names = own && !base.includes(own) ? [own, ...base] : base
  return '<h2>Liquid Material</h2><select id="planeliquid">' +
    names.map(n => `<option value="${escapeText(n)}"${n === own ? ' selected' : ''}>` +
      `${escapeText(n)}</option>`).join('') + '</select>'
}

/** The liquid material every pool without one of its own gets. */
function levelLiquid () {
  return (plan.look && plan.look.liquid) || levelLook.liquid || ''
}

/** Put the gizmo on whatever the plan selection names. */
function attachTo (sel) {
  if (!sel) { gizmo.detach(); return }
  const found = groups.added.children.find(c =>
    c.userData.plan && c.userData.kind === sel.kind && c.userData.id === sel.id)
  if (!found) { gizmo.detach(); return }
  // The marker was created moments ago, so its world matrix has not been computed yet.
  // TransformControls reads it on attach, and attaching to a stale one leaves handles
  // that do not appear.
  world.updateMatrixWorld(true)
  gizmo.attach(found)
}

// -- picking -----------------------------------------------------------------

const ray = new THREE.Raycaster()

function pick (ev, objects) {
  const r = renderer.domElement.getBoundingClientRect()
  const pt = new THREE.Vector2(
    ((ev.clientX - r.left) / r.width) * 2 - 1,
    -((ev.clientY - r.top) / r.height) * 2 + 1)
  ray.setFromCamera(pt, camera)
  return ray.intersectObjects(objects, false).filter(h => !cutHides(h))
}

//: Where the pointer went down, so a drag can be told from a click. Without this,
//: releasing the mouse after any camera move also picked whatever was under it.
let downAt = null
renderer.domElement.addEventListener('pointerdown', (ev) => {
  downAt = [ev.clientX, ev.clientY]
})

renderer.domElement.addEventListener('dblclick', (ev) => {
  const hits = pick(ev, levelPickables())
  if (!hits.length) return
  const at = hits[0].point.clone()
  const offset = camera.position.clone().sub(controls.target)
  controls.target.copy(at)
  camera.position.copy(at).add(offset.clampLength(0, 8000))
  controls.update()
})

/** Highlight what is picked, and put the gizmo on it.
 *
 * An outline per picked piece, not a tint on the material: an InstancedMesh shares one
 * material across every copy, so tinting it lit up all 130 columns when one was
 * clicked. */
function refreshPicked () {
  drawPickOutlines()
  trapGridAt = 0
  if (!picked.length) { handle.visible = false; return }
  askPickedParts()
  placeHandle()
}

/** The selection's outlines, where the picked pieces are now. */
function drawPickOutlines () {
  clear(groups.outline)
  if (fxPick) {
    const ring = new THREE.Mesh(
      new THREE.SphereGeometry(160, 14, 10),
      new THREE.MeshBasicMaterial({ color: 0x53e0a0, wireframe: true, depthTest: false }))
    ring.position.copy(fxPick.position)
    groups.outline.add(ring)
  }
  if (wiringPick) {
    const ring = new THREE.Mesh(
      new THREE.SphereGeometry(320, 18, 12),
      new THREE.MeshBasicMaterial({ color: 0x53e0a0, wireframe: true,
                                    depthTest: false }))
    ring.position.copy(wiringPick.position)
    groups.outline.add(ring)
  }
  for (const p of picked) {
    let box = p.marker || p.levelActor
      ? new THREE.Box3().setFromCenterAndSize(new THREE.Vector3(),
        new THREE.Vector3().setScalar(2 * MARKER_PICK_HALF))
      : meshBounds.get(p.mesh)
    let at = p.matrix
    if (p.added) {
      //: Measured off the drawn parts. An added piece's mesh need not appear in the
      //: level at all, so `meshBounds` has nothing for it, and a kit piece is several
      //: components rather than one box. `m.matrix` is the part's transform within the
      //: world group -- the same space `p.matrix` is in -- so the box is already
      //: placed and the outline carries no transform of its own.
      box = new THREE.Box3()
      for (const m of addedMeshes(p.added)) {
        m.updateMatrix()
        if (!m.geometry.boundingBox) m.geometry.computeBoundingBox()
        box.union(m.geometry.boundingBox.clone().applyMatrix4(m.matrix))
      }
      if (box.isEmpty()) continue
      at = new THREE.Matrix4().toArray()
    }
    if (!box) continue
    // Built as its own wireframe rather than with Box3Helper: that helper derives its
    // transform from the Box3 every frame and discards anything applied to it, so the
    // outline appeared around a different copy of the mesh than the one picked.
    const size = box.getSize(new THREE.Vector3())
    const mid = box.getCenter(new THREE.Vector3())
    const geo = new THREE.BoxGeometry(Math.max(size.x, 1), Math.max(size.y, 1),
                                      Math.max(size.z, 1))
    geo.translate(mid.x, mid.y, mid.z)
    const outline = new THREE.LineSegments(
      new THREE.EdgesGeometry(geo),
      new THREE.LineBasicMaterial({ color: 0x53e0a0, depthTest: false }))
    outline.matrixAutoUpdate = false
    outline.matrix.fromArray(at)
    groups.outline.add(outline)
  }
}

/** Put the handle at the middle of what is picked and attach the gizmo to it. */
function placeHandle () {
  // The handle starts at the middle of what is picked, so a rotation turns the
  // group about its own centre rather than about whichever piece was clicked first.
  const mid = new THREE.Vector3()
  for (const p of picked) {
    mid.add(new THREE.Vector3(p.matrix[12], p.matrix[13], p.matrix[14]))
  }
  mid.multiplyScalar(1 / picked.length)
  handle.position.copy(mid)
  handle.rotation.set(0, 0, 0)
  //: one kit piece is turned and resized along its own axes, which its trap grid follows
  const kit = singleKitUnit()
  if (kit) {
    const turn = new THREE.Quaternion()
    kit.actor.clone().decompose(new THREE.Vector3(), turn, new THREE.Vector3())
    handle.quaternion.copy(turn)
  }
  handle.scale.set(1, 1, 1)
  handle.updateMatrix()
  handleFrom = handle.matrix.clone()
  gizmo.attach(handle)
}

/** Take the picked pieces out of the level.
 *
 * Recorded as a removal rather than hidden: carving a smaller mission out of a War
 * Scenario means the unused parts are gone from the level, and the emitter parks them
 * out of the world -- parked rather than deleted only because removing an export
 * renumbers every reference to it.
 */
function removePicked () {
  if (!picked.length) return
  snapshot()
  const marks = picked.filter(p => p.marker)
  const refused = marks.length ? removeMarkers(marks) : []
  picked = picked.filter(p => !p.marker)
  //: the level's own lights and effects are parked by the build (plan `removedFx`)
  const actors = picked.filter(p => p.levelActor)
  if (actors.length) {
    plan.removedFx = [...new Set((plan.removedFx || []).concat(actors.map(p => p.levelActor)))]
      .sort()
    picked = picked.filter(p => !p.levelActor)
    applyActorEdits()
  }
  //: One of OUR added meshes is deleted outright -- it exists only in the plan. One of
  //: the level's own is recorded as a removal instead, and the emitter parks it.
  const mine = picked.filter(p => p.added).map(p => p.added)
  //: a whole group's door goes with it (compounds.js)
  compounds.removeWith(mine)
  if (mine.length) plan.added = plan.added.filter(e => !mine.includes(e))
  //: a teleporter whose other end was deleted sends nowhere
  for (const t of teleporters()) {
    if (t.link !== null && t.link !== undefined && !teleporters().some(o => o.tid === t.link)) {
      t.link = null
    }
  }
  for (const p of picked) {
    if (p.added) continue
    if (!plan.removed.includes(p.id)) plan.removed.push(p.id)
  }
  const partners = teleportPartners(picked.filter(p => !p.added))
  for (const p of partners) if (!plan.removed.includes(p.id)) plan.removed.push(p.id)
  if (partners.length) {
    flash('A teleporter works only in pairs, so its other end was removed too. ' +
          'Ctrl+Z restores both.')
  }
  applyRemoved()
  if (marks.length) {
    gizmo.detach()
    drawPlan()
    showProps()
    if (refused.length) {
      flash(`Kept ${refused.join(', ')}: a level needs a rift in play, an exit, and ` +
            'a player start for each phase.')
    }
  }
  picked = []
  gizmo.detach()
  refreshPicked()
  showProps()
  if (mine.length) redrawAdded()
}

/** The level pieces of each teleporter end linked to one of `pieces` and not among
 * them (level.teleports, riftlord.leveldata.teleport_pairs). */
function teleportPartners (pieces) {
  const links = (level && level.teleports) || {}
  const keyOf = (p) => (p.sub || 'persistent') + '|' + p.export
  const mine = new Set(pieces.filter(p => p.export !== undefined).map(keyOf))
  const want = new Set()
  for (const k of mine) if (links[k] && !mine.has(links[k])) want.add(links[k])
  if (!want.size) return []
  return ((level && level.pieces) || []).filter(p => p.export !== undefined &&
    want.has(keyOf(p)))
}

//: A removed piece is collapsed to nothing rather than taken out of the instanced
//: mesh, because an InstancedMesh is a fixed-size array and rebuilding it per deletion
//: would redraw the level every time.
const GONE = new THREE.Matrix4().makeScale(0, 0, 0)

/** After setMatrixAt: upload the matrices and drop the cached culling bounds. */
function instancesMoved (inst) {
  inst.instanceMatrix.needsUpdate = true
  //: three.js computes an InstancedMesh's bounds once, from the matrices it had then;
  //: stale bounds frustum-cull a moved instance from most camera angles.
  inst.boundingSphere = null
  inst.boundingBox = null
}

/** Export numbers of the level's own doors the plan removes. */
function goneDoorExports () {
  const out = new Set()
  for (const w of plan.wiring || []) {
    const d = w.gone && ((level && level.doors) || {})[w.name]
    if (d && d.index) out.add(d.index)
  }
  return out
}

/** Whether a level piece is a drawn part of a removed level door. */
const partOfGoneDoor = (piece, doors) => !!doors.size && piece.source === 'blueprint' &&
  (piece.sub || 'persistent') === 'persistent' && doors.has(piece.export)

//: The removed doors applyRemoved last hid, as a sorted key.
let hiddenDoorsKey = ''

function applyRemoved () {
  const gone = new Set(plan.removed || [])
  const doors = goneDoorExports()
  hiddenDoorsKey = [...doors].sort().join(',')
  syncCrystalHidden()
  //: the level pieces the Crystal parks (crystalui.js)
  const parked = planCrystal(plan) ? crystalHidden : new Set()
  for (const [name, inst] of instByMesh) {
    const list = piecesByMesh.get(name) || []
    list.forEach((piece, i) => {
      setPieceMatrix(inst, i, gone.has(piece.id) || parked.has(piece.id) || planeMeshes.has(piece.id) ||
        partOfGoneDoor(piece, doors) || plateHidden(piece)
        ? GONE : new THREE.Matrix4().fromArray(piece.matrix))
    })
    instancesMoved(inst)
  }
  for (const m of groups.forcefield.children) {
    m.visible = !partOfGoneDoor(m.userData.piece || {}, doors)
  }
  syncFlipped()
  applyActorEdits()
}

// -- per-plane liquid materials ----------------------------------------------

//: Pool planes painted with a material of their own (plan.look.planes), drawn as
//: separate meshes while their instance in the level's InstancedMesh is hidden.
const planeGroup = new THREE.Group()
world.add(planeGroup)
//: piece id -> the mesh drawn for it
const planeMeshes = new Map()
//: Opacity of a repainted pool plane, matching the level's own liquids.
const PLANE_OPACITY = 0.72

/** Whether a picked level piece can carry its own liquid material. */
const paintablePlane = (p) => !!(p && !p.added && p.liquid && p.source === 'component')

function dropPlaneMeshes () {
  for (const m of planeGroup.children) m.material.dispose()
  planeGroup.clear()
  planeMeshes.clear()
}

/** Draw every plane the plan paints, then hide those planes' instances. */
async function previewPlanes () {
  const gen = levelGen
  const planes = (plan.look && plan.look.planes) || {}
  const made = []
  for (const [key, material] of Object.entries(planes)) {
    const piece = level && level.pieces[+key]
    const inst = piece && instByMesh.get(piece.group || piece.mesh)
    if (!inst || (plan.removed || []).includes(piece.id)) continue
    let map = null
    try {
      const got = await json('api/look/' + encodeURIComponent(material))
      if (got && got.texture) map = await loadTexture('api/texture' + got.texture)
    } catch (err) {
      map = null
    }
    const mesh = new THREE.Mesh(inst.geometry, new THREE.MeshLambertMaterial({
      map: map || undefined, color: map ? 0xffffff : LIQUID_TINT.water,
      transparent: true, opacity: PLANE_OPACITY, depthWrite: false,
      side: THREE.DoubleSide }))
    mesh.matrixAutoUpdate = false
    mesh.matrix.fromArray(piece.matrix)
    mesh.userData = { planePiece: piece, material, textured: !!map }
    made.push([piece.id, mesh])
  }
  if (gen !== levelGen) return
  dropPlaneMeshes()
  for (const [id, mesh] of made) {
    planeGroup.add(mesh)
    planeMeshes.set(id, mesh)
  }
  applyRemoved()
}

/** Give one plane its own material, or (null) hand it back to the level-wide one. */
function setPlaneMaterial (piece, material) {
  snapshot()
  plan.look = plan.look || {}
  const planes = Object.assign({}, plan.look.planes || {})
  if (material) planes[String(piece.id)] = material
  else delete planes[String(piece.id)]
  plan.look.planes = planes
  previewPlanes().catch(err => report('liquid', err))
}

/** Make every kit assembly whole in the plan: a move or removal of one of its parts
 * applies to all of them (the first edited part's movement wins), so no saved plan can
 * leave an assembly drawn apart or built from a different part than the one shown. */
function unifyAssemblies () {
  if (!level || !level.pieces) return
  const kin = new Map()
  for (const p of level.pieces) {
    if (p.source !== 'blueprint' || p.export === undefined) continue
    const key = (p.sub || 'persistent') + '|' + p.export
    if (!kin.has(key)) kin.set(key, [])
    kin.get(key).push(p)
  }
  const edits = new Map((plan.edits || []).map(e => [e.id, e]))
  const removed = new Set(plan.removed || [])
  for (const parts of kin.values()) {
    if (parts.length < 2) continue
    if (parts.some(p => removed.has(p.id))) for (const p of parts) removed.add(p.id)
    const lead = parts.find(p => edits.has(p.id))
    if (!lead) continue
    const delta = new THREE.Matrix4().fromArray(edits.get(lead.id).matrix)
      .multiply(new THREE.Matrix4().fromArray(lead.original || lead.matrix).invert())
    for (const p of parts) {
      const m = delta.clone().multiply(new THREE.Matrix4().fromArray(p.original || p.matrix))
        .toArray()
      if (edits.has(p.id)) edits.get(p.id).matrix = m
      else edits.set(p.id, { id: p.id, mesh: p.mesh, matrix: m })
    }
  }
  plan.edits = [...edits.values()]
  plan.removed = [...removed]
}

/** Put the level's own geometry back the way the plan says it is. */
function applyEdits () {
  unifyAssemblies()
  const byId = new Map((plan.edits || []).map(e => [e.id, e.matrix]))
  for (const [name, inst] of instByMesh) {
    const list = piecesByMesh.get(name) || []
    list.forEach((piece, i) => {
      const edited = byId.get(piece.id)
      piece.matrix = edited || piece.original || piece.matrix
      setPieceMatrix(inst, i, new THREE.Matrix4().fromArray(piece.matrix))
    })
    instancesMoved(inst)
  }
  applyRemoved()
  applyPaintTint()
}

/** Square a matrix's axes in place, keeping X, Y's plane and each axis's length: an
 * Unreal transform holds no shear (riftlord/apply.py unsheared). */
function squareUp (m) {
  const e = m.elements
  const x = new THREE.Vector3(e[0], e[1], e[2])
  const y = new THREE.Vector3(e[4], e[5], e[6])
  const z = new THREE.Vector3(e[8], e[9], e[10])
  const len = [x.length(), y.length(), z.length()]
  if (Math.min(...len) < 1e-9) return m
  const sign = Math.sign(m.determinant()) || 1
  x.normalize()
  y.addScaledVector(x, -y.dot(x)).normalize()
  z.crossVectors(x, y).multiplyScalar(sign)
  const axes = [x, y, z]
  for (let c = 0; c < 3; c++) {
    e[c * 4] = axes[c].x * len[c]
    e[c * 4 + 1] = axes[c].y * len[c]
    e[c * 4 + 2] = axes[c].z * len[c]
  }
  return m
}

/** Apply the handle's movement to every picked piece and record it. */
function movePicked () {
  handle.updateMatrix()
  const delta = handle.matrix.clone().multiply(handleFrom.clone().invert())
  const scratch = new THREE.Matrix4()
  for (const p of picked) {
    scratch.fromArray(p.matrix).premultiply(delta)
    if (p.added && (p.added.kind === 'kit' || p.added.kind === 'mesh')) squareUp(scratch)
    p.matrix = scratch.toArray()
    if (p.marker) {
      moveMarker(p)
      continue
    }
    if (p.levelActor) {
      moveLevelActor(p)
      continue
    }
    if (p.added) {
      //: One of ours: the plan IS the record, so there is no edit to file against an
      //: id and no instanced copy to update -- the entry moves and its parts follow.
      //: A Fab architecture piece keeps its pack scale (fabui.lockScale).
      if (p.added.kind === 'fab') p.matrix = fab.lockScale(p.added, p.matrix, fab.pieceOf(p.added))
      p.added.matrix = p.matrix
      moveAdded(p.added)
      if (isTeleporter(p.added)) drawTeleportLinks()
      continue
    }
    const inst = instByMesh.get(p.group || p.mesh)
    const ownPlane = planeMeshes.get(p.id) || flipMeshes.get(p.id)
    if (ownPlane) ownPlane.matrix.fromArray(p.matrix)
    else if (inst) {
      setPieceMatrix(inst, p.instance, scratch)
      instancesMoved(inst)
    }
    const found = plan.edits.find(e => e.id === p.id)
    if (found) found.matrix = p.matrix
    else plan.edits.push({ id: p.id, mesh: p.mesh, matrix: p.matrix })
  }
  //: a moved group's door follows it (compounds.js)
  compounds.follow(delta, picked)
  handleFrom = handle.matrix.clone()
}

//: How far below a picked piece F looks for something to rest it on, in Unreal units.
const DROP_RAY_FAR = 200000
//: Where F's downward rays start across a piece's footprint, as a fraction of its
//: half-width from the centre (0 is the centre alone).
const DROP_FOOTPRINT = 0.8
const dropRay = new THREE.Raycaster()

/** A picked piece's world box in Unreal space, or null. */
function pickedBox (p) {
  if (p.added) {
    const box = new THREE.Box3()
    for (const m of addedMeshes(p.added)) {
      m.updateMatrix()
      if (!m.geometry.boundingBox) m.geometry.computeBoundingBox()
      box.union(m.geometry.boundingBox.clone().applyMatrix4(m.matrix))
    }
    return box.isEmpty() ? null : box
  }
  const own = meshBounds.get(p.mesh)
  return own ? own.clone().applyMatrix4(new THREE.Matrix4().fromArray(p.matrix)) : null
}

/** Move one picked piece by `delta` (a Matrix4 in Unreal space) and record it. */
function shiftPicked (p, delta) {
  const m = new THREE.Matrix4().fromArray(p.matrix).premultiply(delta)
  p.matrix = m.toArray()
  if (p.added) {
    p.added.matrix = p.matrix
    moveAdded(p.added)
    return
  }
  const inst = instByMesh.get(p.group || p.mesh)
  const ownPlane = planeMeshes.get(p.id) || flipMeshes.get(p.id)
  if (ownPlane) ownPlane.matrix.fromArray(p.matrix)
  else if (inst) {
    setPieceMatrix(inst, p.instance, m)
    instancesMoved(inst)
  }
  const found = plan.edits.find(e => e.id === p.id)
  if (found) found.matrix = p.matrix
  else plan.edits.push({ id: p.id, mesh: p.mesh, matrix: p.matrix })
}

/** The highest surface under `box` (Unreal space) that is not part of `skip`, as an
 * Unreal height, or null. Rays start at the box's mid-height. */
function surfaceUnder (box, skip) {
  world.updateMatrixWorld(true)
  const down = new THREE.Vector3(0, 0, -1).transformDirection(world.matrixWorld)
  const mid = box.getCenter(new THREE.Vector3())
  const half = box.getSize(new THREE.Vector3()).multiplyScalar(DROP_FOOTPRINT / 2)
  const targets = levelPickables().concat(groups.newGeo.children)
  let best = null
  for (const [sx, sy] of [[0, 0], [-1, -1], [-1, 1], [1, -1], [1, 1]]) {
    const from = world.localToWorld(
      new THREE.Vector3(mid.x + sx * half.x, mid.y + sy * half.y, mid.z))
    dropRay.set(from, down)
    dropRay.far = DROP_RAY_FAR
    for (const hit of dropRay.intersectObjects(targets, false)) {
      if (skip(hit)) continue
      const z = world.worldToLocal(hit.point.clone()).z
      if (z <= mid.z && (best === null || z > best)) best = z
      break
    }
  }
  return best
}

//: F twice within this many milliseconds (the common desktop double-click interval)
//: sinks the selection into what is below instead of resting it on top.
const DOUBLE_TAP_MS = 500
//: How far a double F sinks a piece into the surface below, in uu: about the 25-59 uu a
//: kit floor tile is thick, so it is set into the floor rather than through it.
const EMBED_DEPTH = 50
//: A sunk piece keeps at least this share of its own height above the surface.
const EMBED_KEEP_SHARE = 0.5
//: When F was last pressed (performance.now()), for the double tap.
let lastDropAt = 0

/** The picked geometry grouped into what moves as one: a whole level assembly, one added
 * entry, or one component piece. Markers and level actors are left out. */
function pickUnits () {
  const units = new Map()
  for (const p of picked) {
    if (p.marker || p.levelActor) continue
    const key = p.added || (p.source === 'blueprint' && p.export !== undefined
      ? `${p.sub || 'persistent'}:${p.export}` : `id:${p.id}`)
    if (!units.has(key)) units.set(key, [])
    units.get(key).push(p)
  }
  return [...units.values()]
}

/** F: rest each picked piece (a whole assembly or added piece at a time) on the top
 * surface beneath it; with `embed`, sunk EMBED_DEPTH into it. Undoable. */
function dropPicked (embed = false) {
  if (!picked.length) return false
  const units = pickUnits()
  const entries = new Set(picked.filter(p => p.added).map(p => p.added))
  const ids = new Set(picked.filter(p => !p.added).map(p => p.id))
  const skip = (hit) => {
    const entry = hit.object.userData.addedEntry
    if (entry) return entries.has(entry)
    if (hit.object.userData.planePiece) return ids.has(hit.object.userData.planePiece.id)
    const list = piecesByMesh.get(hit.object.userData.group || hit.object.userData.mesh)
    const piece = list && list[hit.instanceId ?? 0]
    return !!piece && ids.has(piece.id)
  }
  const moves = []
  let missed = 0
  for (const parts of units) {
    const box = new THREE.Box3()
    for (const p of parts) {
      const b = pickedBox(p)
      if (b) box.union(b)
    }
    const top = box.isEmpty() ? null : surfaceUnder(box, skip)
    if (top === null) { missed += 1; continue }
    const sink = embed
      ? Math.min(EMBED_DEPTH, (box.max.z - box.min.z) * (1 - EMBED_KEEP_SHARE)) : 0
    const dz = top - sink - box.min.z
    if (Math.abs(dz) > 0.01) moves.push([parts, dz])
  }
  if (moves.length) {
    snapshot()
    for (const [parts, dz] of moves) {
      const delta = new THREE.Matrix4().makeTranslation(0, 0, dz)
      for (const p of parts) shiftPicked(p, delta)
    }
    syncFlipped()
    refreshPicked()
  }
  flash(`${moves.length} ${embed ? 'sunk' : 'dropped'}` +
    (missed ? `, ${missed} with nothing beneath ${missed === 1 ? 'it' : 'them'}` : '') + '.')
  return true
}

// -- flipping and inverting a turn --------------------------------------------

//: What the flip buttons and keys do: [action, label, key, with shift].
const FLIPS = [
  ['mirrorH', 'Flip Left/Right', 'm', false], ['mirrorV', 'Flip Upside Down', 'm', true],
  ['turnH', 'Invert Turn', 'n', false],
  ['turnV', 'Invert Tilt', 'n', true]]

/** One unit's base matrix: the actor of a level assembly or an added kit piece, else the
 * piece's own. Unreal space. */
function unitMatrix (parts) {
  const p = parts[0]
  if (p.added) return new THREE.Matrix4().fromArray(p.added.matrix)
  if (p.source === 'blueprint' && p.actor && partsCache.has(p.actor)) {
    const part = partsCache.get(p.actor).find(pt => pt.mesh === p.mesh)
    if (part) return new THREE.Matrix4().fromArray(p.matrix).multiply(partLocal(part).invert())
  }
  return new THREE.Matrix4().fromArray(p.matrix)
}

/** The world box of one unit, or null. */
function unitBox (parts) {
  const box = new THREE.Box3()
  for (const p of parts) {
    const b = pickedBox(p)
    if (b) box.union(b)
  }
  return box.isEmpty() ? null : box
}

/** The reflection across the plane through the origin with unit normal `n`. */
function reflection (n) {
  const m = new THREE.Matrix4()
  m.set(1 - 2 * n.x * n.x, -2 * n.x * n.y, -2 * n.x * n.z, 0,
        -2 * n.y * n.x, 1 - 2 * n.y * n.y, -2 * n.y * n.z, 0,
        -2 * n.z * n.x, -2 * n.z * n.y, 1 - 2 * n.z * n.z, 0,
        0, 0, 0, 1)
  return m
}

/** The level axis (x or y) nearest the camera's right, for flipping several units. */
function viewSideAxis () {
  world.updateMatrixWorld(true)
  const right = new THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion)
    .transformDirection(world.matrixWorld.clone().invert())
  return Math.abs(right.x) >= Math.abs(right.y) ? new THREE.Vector3(1, 0, 0)
    : new THREE.Vector3(0, 1, 0)
}

/** Flip or invert the turn of every picked unit about its own middle (`how` is one of
 * FLIPS). A flip mirrors (a negative scale on one axis); an inverted turn mirrors the
 * rotation and keeps the scale. Undoable. */
function flipPicked (how) {
  const units = pickUnits()
  if (!units.length) { flash('Pick something to flip first.'); return false }
  snapshot()
  const vertical = how === 'mirrorV' || how === 'turnV'
  const side = units.length === 1 ? null : viewSideAxis()
  for (const parts of units) {
    const box = unitBox(parts)
    if (!box) continue
    const mid = box.getCenter(new THREE.Vector3())
    const base = unitMatrix(parts)
    const turn = how === 'turnH' || how === 'turnV'
    let n
    if (vertical) n = new THREE.Vector3(0, 0, 1)
    else if (turn) n = new THREE.Vector3(0, 1, 0)
    else if (side) n = side.clone()
    //: one unit flips across its own left/right axis (Unreal's local y)
    else n = new THREE.Vector3().setFromMatrixColumn(base, 1).normalize()
    const about = new THREE.Matrix4().makeTranslation(mid.x, mid.y, mid.z)
      .multiply(reflection(n)).multiply(new THREE.Matrix4().makeTranslation(-mid.x, -mid.y, -mid.z))
    //: an inverted turn mirrors again in the unit's own frame, S.R.S: the yaw (or the
    //: pitch and roll) changes sign and the scale stays positive
    const next = turn ? about.clone().multiply(base).multiply(reflection(n))
      : about.clone().multiply(base)
    const delta = next.multiply(base.clone().invert())
    for (const p of parts) shiftPicked(p, delta)
    for (const p of parts) if (p.added && isTeleporter(p.added)) drawTeleportLinks()
  }
  syncFlipped()
  refreshPicked()
  showProps()
  flash((FLIPS.find(f => f[0] === how) || [, 'Flipped'])[1] + '.')
  return true
}

/** The keys of every effect of the level placing the same particle system as picked
 * level effect `rec`, still in the level. */
function sameEffects (rec) {
  if (!rec || !rec.levelActor || rec.kind !== 'fx') return []
  const template = rec.obj.userData.vfx
  return [...new Set(groups.vfx.children.filter(c => c.visible && c.userData.vfx === template &&
    c.userData.levelActor).map(c => c.userData.levelActor))]
}

//: The level light kinds a colour is set on (riftlord/build/world/levelretint.py LIGHT_KINDS).
const RECOLOUR_KINDS = ['PointLight', 'SpotLight', 'RectLight']

/** Whether a picked record is one of the level's own point, spot or rect lights. */
const isLevelLight = (p) => !!p.levelActor && p.kind === 'light' &&
  RECOLOUR_KINDS.includes(p.obj.userData.entry.kind)
/** Whether a picked record is one of the level's own Emitter actors, whose particle system
 * can be swapped (`fxSwaps`). */
const isLevelEmitter = (p) => !!p.levelActor && p.kind === 'fx' && !p.sound &&
  p.obj.userData.entry.actorClass === 'Emitter'

/** The markers of every level light still in the level that ships in picked light `rec`'s
 * colour and kind, picked on their own (not as a Blueprint's). */
function sameLights (rec) {
  if (!rec || !isLevelLight(rec)) return []
  const { colour, kind } = rec.obj.userData.entry
  return groups.lights.children.filter(m => m.visible && m.userData.kind === 'light' &&
    m.userData.levelActor && m.userData.entry.kind === kind &&
    rgbHex(m.userData.entry.colour) === rgbHex(colour) &&
    !actorPieces(m.userData.levelActor).length)
}

/** The sections for picked level lights and effects: a light's colour, an emitter's
 * particle system, and picking or deleting every one like the picked one. */
function levelActorSection () {
  if (!picked.length || !picked.every(p => p.levelActor)) return ''
  const one = picked[0]
  const out = []
  if (picked.every(isLevelLight)) {
    const now = rgbHex(lightColour(one.levelActor, one.obj.userData.entry.colour))
    out.push('<h2>Light</h2><label class="pick"><span>Colour</span>' +
      `<input type="color" id="lightcolour" value="${now}"></label>`)
    if (picked.some(p => (plan.lightColours || {})[p.levelActor])) {
      out.push('<button id="lightcolourreset">Reset</button>')
    }
    const n = picked.length === 1 ? sameLights(one).length : 0
    if (n > 1) out.push(`<button id="lightsame">Select All ${n}</button>`)
  }
  if (picked.every(isLevelEmitter)) {
    const rows = catalogue.filter(r => r.kind === 'fx' && r.template && r.actorClass === 'Emitter')
    const now = one.obj.userData.vfx
    if (!rows.some(r => r.template === now)) rows.push({ template: now, label: fxLabel(now), group: 'Other' })
    const fxGroups = [...new Set(rows.map(r => r.group || 'Other'))].sort()
    out.push('<h2>Effect</h2><select id="levelfxkind">' + fxGroups.map(g =>
      `<optgroup label="${escapeText(g)}">` + rows.filter(r => (r.group || 'Other') === g)
        .sort((a, b) => String(a.label).localeCompare(String(b.label))).map(r =>
          `<option value="${escapeText(r.template)}"${r.template === now ? ' selected' : ''}>` +
          `${escapeText(r.label || r.template)}</option>`).join('') + '</optgroup>').join('') +
      '</select>')
  }
  if (picked.length === 1 && one.kind === 'fx') {
    const n = sameEffects(one).length
    if (n > 1) {
      out.push(`<button id="fxsame">Select All ${n}</button>` +
        `<button id="fxremoveall">Delete All ${n}</button>`)
    }
  }
  return out.join('')
}

/** Set (or with null, reset) the colour of every picked level light; the light's own
 * colour stores nothing. */
function setLightColour (hex) {
  snapshot()
  const colours = { ...(plan.lightColours || {}) }
  for (const p of picked.filter(isLevelLight)) {
    if (hex && hex !== rgbHex(p.obj.userData.entry.colour)) colours[p.levelActor] = hex
    else delete colours[p.levelActor]
  }
  if (Object.keys(colours).length) plan.lightColours = colours
  else delete plan.lightColours
  applyActorEdits()
}

/** Play particle system `template` from every picked level emitter; its own stores
 * nothing. */
function setEffectTemplate (template) {
  snapshot()
  const swaps = { ...(plan.fxSwaps || {}) }
  for (const p of picked.filter(isLevelEmitter)) {
    if (template !== p.obj.userData.entry.template) swaps[p.levelActor] = template
    else delete swaps[p.levelActor]
  }
  if (Object.keys(swaps).length) plan.fxSwaps = swaps
  else delete plan.fxSwaps
  applyActorEdits()
}

/** Pick every marker in `marks` on its own. */
function pickMarkers (marks) {
  picked = marks.map(levelActorRecord)
  wiringPick = null
  selected = null
  gizmo.detach()
  refreshPicked()
  showProps()
}

//: The flip buttons' icons (24 x 24 SVG bodies): a shape and its mirror image across a
//: dashed axis, and a turn arrow and its mirror image.
const FLIP_ICONS = {
  mirrorH: '<path d="M12 3v18" stroke-dasharray="2 2"/><path d="M9 6 3 18h6z" fill="currentColor"/>' +
    '<path d="M15 6l6 12h-6z"/>',
  mirrorV: '<path d="M3 12h18" stroke-dasharray="2 2"/><path d="M6 9 18 3v6z" fill="currentColor"/>' +
    '<path d="M6 15l12 6v-6z"/>',
  turnH: '<path d="M12 3v18" stroke-dasharray="2 2"/><path d="M9 7a5 5 0 0 0 0 10"/>' +
    '<path d="M9 17l-2.5-1.5M9 17l-1 2.5"/><path d="M15 7a5 5 0 0 1 0 10"/>' +
    '<path d="M15 17l2.5-1.5M15 17l1 2.5"/>',
  turnV: '<path d="M3 12h18" stroke-dasharray="2 2"/><path d="M7 9a5 5 0 0 1 10 0"/>' +
    '<path d="M17 9l1.5-2.5M17 9l-2.5-1"/><path d="M7 15a5 5 0 0 0 10 0"/>' +
    '<path d="M17 15l1.5 2.5M17 15l-2.5 1"/>',
}
//: The flip buttons' short labels.
const FLIP_SHORT = { mirrorH: 'Left/Right', mirrorV: 'Upside Down', turnH: 'Invert Turn',
  turnV: 'Invert Tilt' }

/** The Flip section of the Selected panel, for picked geometry. */
/** The added kit and mesh pieces picked, once each, or [] when anything else is picked. */
function pickedCollisionPieces () {
  const items = new Set()
  for (const p of picked) {
    if (p.marker) continue
    if (!p.added || (p.added.kind !== 'kit' && p.added.kind !== 'mesh')) return []
    items.add(p.added)
  }
  return [...items]
}

/** The Collision switch for picked added pieces (riftlord/mapeditor/piececollision.py). */
function collisionSection () {
  const items = pickedCollisionPieces()
  if (!items.length) return ''
  const off = items.filter(a => a.collision === false).length
  return '<h2>Collision</h2><label class="row"><span>Collides</span>' +
    `<input id="piececollision" type="checkbox"${off ? '' : ' checked'}` +
    `${off && off < items.length ? ' data-mixed="1"' : ''}></label>`
}

function wireCollision () {
  const box = el('piececollision')
  if (!box) return
  if (box.dataset.mixed) box.indeterminate = true
  box.addEventListener('change', (ev) => {
    snapshot()
    for (const a of pickedCollisionPieces()) {
      if (ev.target.checked) delete a.collision
      else a.collision = false
    }
    showProps()
  })
}

function flipSection () {
  if (!pickUnits().length) return ''
  return '<h2>Flip</h2><div class="flips">' + FLIPS.map(([how, label, key, shift]) =>
    `<button data-flip="${how}" title="${label} (${shift ? 'Shift+' : ''}${key.toUpperCase()})"` +
    ` aria-label="${label}"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" ` +
    'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" ' +
    `aria-hidden="true">${FLIP_ICONS[how]}</svg><span>${FLIP_SHORT[how]}</span></button>`)
    .join('') + '</div>'
}

// -- level pieces drawn on their own ------------------------------------------

//: Level pieces whose matrix mirrors when their InstancedMesh does not (or the other
//: way round) are drawn as meshes of their own: three.js picks the front face from the
//: object's determinant, never an instance's.
const flipGroup = new THREE.Group()
world.add(flipGroup)
//: piece id -> its own mesh
const flipMeshes = new Map()

/** Draw every level piece whose handedness no longer matches its InstancedMesh as a mesh
 * of its own, and hide its instance. */
function syncFlipped () {
  const gone = new Set(plan.removed || [])
  const keep = new Set()
  for (const [key, inst] of instByMesh) {
    const mirroredInst = !!(inst.userData && inst.userData.mirrored)
    ;(piecesByMesh.get(key) || []).forEach((piece, i) => {
      if (gone.has(piece.id) || planeMeshes.has(piece.id)) return
      if (isMirrored(piece.matrix) === mirroredInst) return
      keep.add(piece.id)
      let mesh = flipMeshes.get(piece.id)
      if (!mesh) {
        mesh = new THREE.Mesh(inst.geometry, inst.material)
        mesh.matrixAutoUpdate = false
        mesh.userData = { planePiece: piece, flipped: true }
        flipMeshes.set(piece.id, mesh)
        flipGroup.add(mesh)
      }
      mesh.matrix.fromArray(piece.matrix)
      mesh.visible = inst.visible && !!inst.parent && inst.parent.visible
      inst.setMatrixAt(i, GONE)
      instancesMoved(inst)
    })
  }
  for (const [id, mesh] of flipMeshes) {
    if (keep.has(id)) continue
    flipGroup.remove(mesh)
    flipMeshes.delete(id)
  }
}

// -- snapping a move to a neighbour's edge --------------------------------------

//: A move snaps the selection's box to a neighbour's face or edge within this many uu:
//: half the grid snap's step, so any edge is reachable between two grid positions.
const EDGE_SNAP_RANGE = TRAP_CELL * SNAP_SHARE / 2
//: How far around the selection neighbours are looked for when a move begins, in uu.
const EDGE_SNAP_REACH = 4 * TRAP_CELL
//: Colour of the outline on the neighbour a move has snapped to.
const EDGE_CUE_COLOUR = 0xffd04a
//: Local axes the gizmo is dragging count as a level axis when this close to it.
const EDGE_AXIS_COS = 0.9
//: The move under way: {box, from, near} (null while not moving geometry).
let edgeSnap = null
//: The drawn cue of the current snap.
const edgeCue = new THREE.Group()
edgeCue.name = 'edge-snap-cue'
world.add(edgeCue)

/** The world (Unreal space) triangles a picked record draws, nine floats each (three
 * corners), as a Float32Array, or null: a level piece's mesh under its matrix, or every
 * drawn part of an added piece (invisible pick targets, sprites and clouds left out). */
function worldTris (p) {
  const out = []
  const take = (geo, m) => {
    const pos = geo && geo.attributes && geo.attributes.position
    if (!pos) return
    const e = m.elements
    const corner = (i) => {
      const x = pos.getX(i)
      const y = pos.getY(i)
      const z = pos.getZ(i)
      out.push(e[0] * x + e[4] * y + e[8] * z + e[12], e[1] * x + e[5] * y + e[9] * z + e[13],
               e[2] * x + e[6] * y + e[10] * z + e[14])
    }
    const index = geo.index
    const n = index ? index.count : pos.count
    for (let i = 0; i < n - n % 3; i += 1) corner(index ? index.getX(i) : i)
  }
  if (p.added) {
    for (const m of addedMeshes(p.added)) {
      if (!m.isMesh || !m.geometry || (m.material && m.material.opacity === 0)) continue
      m.updateMatrix()
      take(m.geometry, m.matrix)
    }
  } else {
    const drawn = levelGeo.get(p.mesh)
    if (drawn) take(drawn.geo, new THREE.Matrix4().fromArray(p.matrix))
  }
  return out.length ? Float32Array.from(out) : null
}

/** The box around triangles `tris`. */
function trisBox (tris) {
  const box = new THREE.Box3()
  const v = new THREE.Vector3()
  for (let i = 0; i < tris.length; i += 3) box.expandByPoint(v.set(tris[i], tris[i + 1], tris[i + 2]))
  return box
}

/** The highest (`hi`) or lowest coordinate on axis `a` of the surface of triangles
 * `tris`, moved by `d` ([x, y, z]), where it lies inside `band` (a Box3 whose extent on
 * axis `a` is ignored): each triangle clipped to the band on the other two axes. Null
 * when no surface lies inside it. */
function bandExtreme (tris, d, a, band, hi) {
  const others = [0, 1, 2].filter(o => o !== a)
  const lo = others.map(o => band.min.getComponent(o) - d[o])
  const up = others.map(o => band.max.getComponent(o) - d[o])
  let got = null
  const take = (v) => { if (got === null || (hi ? v > got : v < got)) got = v }
  for (let t = 0; t < tris.length; t += 9) {
    let poly = [[tris[t], tris[t + 1], tris[t + 2]], [tris[t + 3], tris[t + 4], tris[t + 5]],
                [tris[t + 6], tris[t + 7], tris[t + 8]]]
    let inside = true
    let outside = false
    for (let k = 0; k < 2 && !outside; k += 1) {
      const o = others[k]
      const vs = poly.map(c => c[o])
      if (Math.max(...vs) < lo[k] || Math.min(...vs) > up[k]) outside = true
      else if (Math.min(...vs) < lo[k] || Math.max(...vs) > up[k]) inside = false
    }
    if (outside) continue
    if (!inside) {
      //: Sutherland-Hodgman against the four sides of the band
      for (let k = 0; k < 2 && poly.length; k += 1) {
        const o = others[k]
        for (const [limit, keep] of [[lo[k], (v) => v >= lo[k]], [up[k], (v) => v <= up[k]]]) {
          const next = []
          for (let i = 0; i < poly.length; i += 1) {
            const p = poly[i]
            const q = poly[(i + 1) % poly.length]
            if (keep(p[o])) next.push(p)
            if (keep(p[o]) !== keep(q[o])) {
              const s = (limit - p[o]) / (q[o] - p[o])
              next.push([0, 1, 2].map(c => p[c] + (q[c] - p[c]) * s))
            }
          }
          poly = next
          if (!poly.length) break
        }
      }
    }
    for (const c of poly) take(c[a])
  }
  return got === null ? null : got + d[a]
}

/** Start a move: remember the selection's triangles and the neighbours near it, each as
 * {box, pts}: the box around its real triangles and the triangles themselves (a piece
 * whose geometry is not drawn has its box alone). */
function edgeSnapBegin () {
  edgeSnap = null
  const mine = picked.filter(p => !p.marker && !p.levelActor)
  if (!mine.length) return
  const shape = (p) => {
    const pts = worldTris(p)
    if (pts) return { box: trisBox(pts), pts }
    const box = pickedBox(p)
    return box ? { box, pts: null } : null
  }
  const box = new THREE.Box3()
  const parts = []
  for (const p of mine) {
    const s = shape(p)
    if (!s) continue
    box.union(s.box)
    parts.push(s)
  }
  if (box.isEmpty()) return
  const reach = box.clone().expandByScalar(EDGE_SNAP_REACH)
  const ids = new Set(mine.filter(p => !p.added).map(p => p.id))
  const entries = new Set(mine.filter(p => p.added).map(p => p.added))
  const gone = new Set(plan.removed || [])
  const near = []
  const size = box.getSize(new THREE.Vector3())
  for (const [key, inst] of instByMesh) {
    if (!inst.visible || !inst.parent || !inst.parent.visible) continue
    for (const piece of piecesByMesh.get(key) || []) {
      if (gone.has(piece.id) || ids.has(piece.id)) continue
      const m = piece.matrix
      //: a cheap reject on the placement before the box is worked out
      if (Math.abs(m[12] - reach.min.x - (reach.max.x - reach.min.x) / 2) >
          (reach.max.x - reach.min.x) / 2 + 20 * size.x + 20000) continue
      const b = pickedBox(piece)
      if (!b || !b.intersectsBox(reach)) continue
      const s = shape(piece)
      if (s && s.box.intersectsBox(reach)) near.push(s)
    }
  }
  for (const entry of plan.added || []) {
    if (entries.has(entry)) continue
    const s = shape(addedRecord(entry))
    if (s && s.box.intersectsBox(reach)) near.push(s)
  }
  const pts = parts.every(s => s.pts)
    ? Float32Array.from(parts.flatMap(s => Array.from(s.pts))) : null
  edgeSnap = { box, pts, from: handle.position.clone(), near }
}

/** The level axes (0 x, 1 y, 2 z) the gizmo is dragging along. */
function draggedAxes () {
  const out = new Set()
  const axis = gizmo.axis || ''
  for (const [letter, i] of [['X', 0], ['Y', 1], ['Z', 2]]) {
    if (!axis.includes(letter)) continue
    const v = new THREE.Vector3().setComponent(i, 1).applyQuaternion(handle.quaternion)
    for (let a = 0; a < 3; a += 1) {
      if (Math.abs(v.getComponent(a)) >= EDGE_AXIS_COS) out.add(a)
    }
  }
  return out
}

/** Snap the handle so the moved selection's box meets a neighbour's face or lines up
 * with its edge, along the axes being dragged; alt or shift held turns it off. */
function edgeSnapMove () {
  clear(edgeCue)
  if (!edgeSnap || altDown || shiftDown) return
  const along = draggedAxes()
  if (!along.size) return
  const d = handle.position.clone().sub(edgeSnap.from)
  const moved = edgeSnap.box.clone().translate(d)
  const offset = [d.x, d.y, d.z]
  const none = [0, 0, 0]
  const keys = ['x', 'y', 'z']
  const best = [null, null, null]
  for (const n of edgeSnap.near) {
    const b = n.box
    for (const a of along) {
      const k = keys[a]
      let beside = true
      for (let o = 0; o < 3 && beside; o += 1) {
        if (o === a) continue
        const ko = keys[o]
        beside = b.max[ko] >= moved.min[ko] - EDGE_SNAP_RANGE &&
                 b.min[ko] <= moved.max[ko] + EDGE_SNAP_RANGE
      }
      if (!beside) continue
      //: faces that meet: each side's surface facing the other, taken only across the
      //: other side's extent, so the empty corners of a box never count
      const meet = []
      if (n.pts && edgeSnap.pts) {
        const theirsHi = bandExtreme(n.pts, none, a, moved, true)
        const mineLo = bandExtreme(edgeSnap.pts, offset, a, b, false)
        const theirsLo = bandExtreme(n.pts, none, a, moved, false)
        const mineHi = bandExtreme(edgeSnap.pts, offset, a, b, true)
        if (theirsHi !== null && mineLo !== null) meet.push(theirsHi - mineLo)
        if (theirsLo !== null && mineHi !== null) meet.push(theirsLo - mineHi)
      } else {
        meet.push(b.max[k] - moved.min[k], b.min[k] - moved.max[k])
      }
      for (const gap of [...meet, b.min[k] - moved.min[k], b.max[k] - moved.max[k]]) {
        if (Math.abs(gap) <= EDGE_SNAP_RANGE &&
            (!best[a] || Math.abs(gap) < Math.abs(best[a].gap))) best[a] = { gap, box: b }
      }
    }
  }
  const shift = new THREE.Vector3()
  const hit = []
  best.forEach((got, a) => {
    if (!got) return
    shift.setComponent(a, got.gap)
    hit.push(got.box)
  })
  if (!hit.length) return
  handle.position.add(shift)
  handle.updateMatrix()
  gizmo.pull()
  for (const b of hit) edgeCue.add(boxLines(b, EDGE_CUE_COLOUR))
  edgeCue.add(boxLines(moved.translate(shift), EDGE_CUE_COLOUR))
}

/** End a move: the cue goes. */
function edgeSnapEnd () {
  edgeSnap = null
  clear(edgeCue)
}

/** An always-on-top wireframe of a box in Unreal space. */
function boxLines (box, colour) {
  const size = box.getSize(new THREE.Vector3())
  const mid = box.getCenter(new THREE.Vector3())
  const geo = new THREE.BoxGeometry(Math.max(size.x, 1), Math.max(size.y, 1), Math.max(size.z, 1))
  geo.translate(mid.x, mid.y, mid.z)
  const lines = new THREE.LineSegments(new THREE.EdgesGeometry(geo),
    new THREE.LineBasicMaterial({ color: colour, depthTest: false }))
  geo.dispose()
  lines.renderOrder = 4
  return lines
}

// -- resizing a kit piece by its trap grid --------------------------------------

//: A resize of a kit piece with a trap grid moves each grid edge in steps of this many
//: uu: half a trap cell.
const GRID_RESIZE_STEP = TRAP_CELL / 2
//: A grid length counts as a whole number of steps within this share of a step.
const GRID_STEP_TOLERANCE = 1e-3
//: The resize under way: {step: [scale step per local axis, 0 for free], scale: [the
//: actor's scale per local axis when it began]} (null for a free resize).
let scaleSnap = null

/** The one kit piece the selection is, as {cls, actor (Matrix4)}, or null. */
function singleKitUnit () {
  const units = pickUnits()
  if (units.length !== 1 || picked.some(p => p.marker || p.levelActor)) return null
  const parts = units[0]
  const p = parts[0]
  if (p.added) return p.added.kind === 'kit' ? { cls: p.added.name, actor: unitMatrix(parts) } : null
  if (p.source !== 'blueprint' || !p.actor || !partsCache.has(p.actor)) return null
  return { cls: p.actor, actor: unitMatrix(parts) }
}

/** The length of each of a grid's two surface edges along each of the actor's axes:
 * [[x, y, z] for local axis 1, [x, y, z] for local axis 2], unscaled. */
function gridEdges (grid) {
  return [1, 2].map(c => [0, 1, 2].map(i => Math.abs(grid.axes[c][i]) * 2 * grid.extent[c]))
}

/** The scale steps that keep every grid of `grids` on whole GRID_RESIZE_STEPs along
 * each actor axis ([step or 0] per axis). */
function gridScaleSteps (grids) {
  const gcd = (a, b) => (b ? gcd(b, a % b) : a)
  const steps = [0, 0, 0]
  for (let i = 0; i < 3; i += 1) {
    const lengths = []
    for (const g of grids) {
      if (!g.axes || !g.extent) continue
      for (const edge of gridEdges(g)) if (edge[i] > 1) lengths.push(edge[i])
    }
    if (!lengths.length) continue
    const counts = lengths.map(l => l / GRID_RESIZE_STEP)
    if (counts.every(n => Math.abs(n - Math.round(n)) < GRID_STEP_TOLERANCE && Math.round(n) > 0)) {
      steps[i] = 1 / counts.map(Math.round).reduce(gcd)
    } else {
      steps[i] = GRID_RESIZE_STEP / Math.max(...lengths)
    }
  }
  return steps
}

/** Start a resize: a single kit piece with a trap grid resizes in grid steps. */
function scaleSnapBegin () {
  scaleSnap = null
  const unit = singleKitUnit()
  const grids = unit && cachedGrids(unit.cls)
  if (!grids || !grids.length) return
  const s = new THREE.Vector3()
  unit.actor.decompose(new THREE.Vector3(), new THREE.Quaternion(), s)
  scaleSnap = { step: gridScaleSteps(grids), scale: [Math.abs(s.x), Math.abs(s.y), Math.abs(s.z)] }
}

/** The gizmo's resize hook: the handle's scale factors, snapped so the piece's trap grid
 * grows or shrinks by whole GRID_RESIZE_STEPs along each grid axis. */
function snapPickedScale (scale) {
  if (!scaleSnap || shiftDown || gizmo.object !== handle) return
  for (const [i, k] of [[0, 'x'], [1, 'y'], [2, 'z']]) {
    const step = scaleSnap.step[i]
    if (!step) continue
    const want = scaleSnap.scale[i] * scale[k]
    scale[k] = Math.max(step, Math.round(want / step) * step) / scaleSnap.scale[i]
  }
}

/** A kit class's grids as the piece's actor scale `actor` (Matrix4) stretches them. */
function scaledGrids (grids, actor) {
  const s = new THREE.Vector3()
  actor.decompose(new THREE.Vector3(), new THREE.Quaternion(), s)
  const k = [Math.abs(s.x), Math.abs(s.y), Math.abs(s.z)]
  return grids.map(g => (g.axes && g.extent ? {
    ...g,
    extent: g.extent.map((e, c) => e * Math.hypot(...[0, 1, 2].map(i => g.axes[c][i] * k[i]))),
  } : g))
}

// -- marquee selection -------------------------------------------------------

//: Half the side of the box outlined around a gameplay marker in a selection, in
//: Unreal units.
const MARKER_PICK_HALF = 160

/** A picked record for a gameplay marker: one of the plan's, or one of the level's. */
function markerRecord (obj) {
  const d = obj.userData
  const p = obj.position
  return {
    marker: d.plan ? `plan:${d.kind}:${d.id}` : `wiring:${d.name}`,
    kind: d.kind, planId: d.plan ? d.id : undefined, name: d.plan ? undefined : d.name,
    mesh: d.plan ? `${KIND_LABEL[d.kind] || d.kind} ${d.id}` : d.name,
    matrix: new THREE.Matrix4().makeTranslation(p.x, p.y, p.z).toArray(),
  }
}

/** What a click on one of the level's light or effect markers picks: the whole
 * Blueprint it belongs to when that is drawn as level pieces (a torch, a brazier), else
 * the light or effect itself; [] when it can be neither moved nor removed. */
function levelActorRecords (obj) {
  const d = obj.userData
  const parts = actorPieces(actorKey(d.entry))
  if (parts.length) return assemblyOf(parts[0])
  return d.levelActor ? [levelActorRecord(obj)] : []
}

/** A picked record for one of the level's lights or effects (moved by the plan's
 * `actorMoves`, removed by its `removedFx`). */
function levelActorRecord (obj) {
  const d = obj.userData
  const p = obj.position
  return { levelActor: d.levelActor, kind: d.kind, obj, sound: !!d.sound,
           mesh: d.kind === 'light' ? (d.entry.kind || 'Light')
             : d.sound ? soundLabel(d.entry.cue) : fxLabel(d.entry.template),
           matrix: new THREE.Matrix4().makeTranslation(p.x, p.y, p.z).toArray() }
}

/** An effect's name as the library lists it, or its particle system's. */
function fxLabel (template) {
  const row = catalogue.find(r => r.kind === 'fx' && r.template === template)
  return row ? row.label : String(template || 'Effect')
}

/** Write a moved level light or effect into the plan's `actorMoves`; every marker of
 * its actor follows. */
function moveLevelActor (rec) {
  const base = rec.obj.userData.base
  const delta = [0, 1, 2].map(i => Math.round((rec.matrix[12 + i] - base[i]) * 100) / 100)
  plan.actorMoves = { ...(plan.actorMoves || {}) }
  if (delta.every(v => v === 0)) delete plan.actorMoves[rec.levelActor]
  else plan.actorMoves[rec.levelActor] = delta
  applyActorEdits()
}

/** The drawn marker a marker record stands for, or null. */
function markerObject (rec) {
  const list = rec.planId !== undefined ? groups.added.children : groups.wiring.children
  return list.find(c => c.userData && (rec.planId !== undefined
    ? c.userData.plan && c.userData.kind === rec.kind && c.userData.id === rec.planId
    : c.userData.wiring && c.userData.name === rec.name)) || null
}

/** Write a moved marker record's place into the plan and onto its marker. */
function moveMarker (rec) {
  const at = [rec.matrix[12], rec.matrix[13], rec.matrix[14]]
    .map(v => Math.round(v * 100) / 100)
  if (rec.planId !== undefined) {
    const found = findObject({ kind: rec.kind, id: rec.planId })
    if (found) {
      const before = found.obj.at.slice()
      found.obj.at = at
      if (found.kind === 'rift') followRift(found.obj, before)
    }
  } else {
    wiringEntry(rec.name, rec.kind).at = at
  }
  const obj = markerObject(rec)
  if (obj) obj.position.set(at[0], at[1], at[2])
}

/** Remove the marker records in `recs` as their own Remove buttons would; refused
 * ones are named in the returned list. Call inside one snapshot. */
function removeMarkers (recs) {
  const refused = []
  for (const rec of recs) {
    if (rec.planId !== undefined) {
      const found = findObject({ kind: rec.kind, id: rec.planId })
      if (!found) continue
      if (rec.kind === 'rift' && riftsInPlay() <= 1) { refused.push(rec.mesh); continue }
      if (rec.kind === 'playerStart') {
        const keep = plan.playerStarts.filter(s => s.id !== rec.planId)
        const cover = startsCover({ ...plan, playerStarts: keep }, level)
        if (!cover.breaks || !cover.waves) { refused.push(rec.mesh); continue }
      }
      if (rec.kind === 'rift') dropRift(rec.planId)
      else if (rec.kind === 'door') {
        plan.doors = plan.doors.filter(d => d.id !== rec.planId)
        for (const sp of plan.spawners) if (sp.door === rec.planId) sp.door = null
        for (const w of plan.wiring || []) if (w.door === rec.planId) w.door = null
      } else if (rec.kind === 'spawner') {
        plan.spawners = plan.spawners.filter(sp => sp.id !== rec.planId)
      } else if (rec.kind === 'playerStart') {
        plan.playerStarts = plan.playerStarts.filter(s => s.id !== rec.planId)
      } else dropPoint(rec.planId)
    } else {
      const obj = markerObject(rec)
      const why = obj ? cannotRemove(obj.userData) : null
      if (why) { refused.push(rec.mesh); continue }
      wiringEntry(rec.name, rec.kind).gone = true
    }
  }
  return refused
}

/** Select what lies inside client rect `r`: level pieces (whole Blueprint assemblies),
 * added pieces and gameplay markers, skipping hidden layers, hidden sublevels, removed
 * pieces and whatever the cutaway hides. `how` is replace, add or remove. */
function marqueeSelect (r, how) {
  const canvas = renderer.domElement.getBoundingClientRect()
  camera.updateMatrixWorld()
  world.updateMatrixWorld(true)
  const inside = (at) => at[2] <= cutPlane.constant && inRect(at, world, camera, canvas, r)
  const centreOf = (box) => {
    const c = box.getCenter(new THREE.Vector3())
    return [c.x, c.y, c.z]
  }
  const found = []
  const gone = new Set(plan.removed || [])
  const doors = goneDoorExports()
  for (const [key, inst] of instByMesh) {
    if (!inst.visible || !inst.parent || !inst.parent.visible) continue
    for (const piece of piecesByMesh.get(key) || []) {
      if (gone.has(piece.id) || partOfGoneDoor(piece, doors)) continue
      const box = pickedBox(piece)
      if (box && inside(centreOf(box))) found.push(...assemblyOf(piece))
    }
  }
  for (const m of planeGroup.children) {
    const piece = m.userData.planePiece
    const box = piece && pickedBox(piece)
    if (box && inside(centreOf(box))) found.push(piece)
  }
  if (groups.newGeo.visible) {
    for (const entry of new Set(groups.newGeo.children.map(m => m.userData.addedEntry))) {
      if (!entry) continue
      const rec = addedRecord(entry)
      const box = pickedBox(rec)
      //: a whole group; added foliage while its layer shows (compounds.js)
      if (box && inside(centreOf(box)) && compounds.shown(entry) &&
          (entry.kind !== 'fx' || groups.vfx.visible)) found.push(...compounds.kin(rec))
    }
  }
  const markers = []
  if (groups.added.visible) {
    for (const m of groups.added.children) {
      if (m.userData.plan && m.visible) markers.push(m)
    }
  }
  if (groups.wiring.visible) {
    for (const m of groups.wiring.children) {
      if (m.userData.wiring && m.visible) markers.push(m)
    }
  }
  for (const m of markers) {
    const p = m.position
    if (inside([p.x, p.y, p.z])) found.push(markerRecord(m))
  }
  //: the level's own lights and effects, while their layers show
  for (const g of [groups.lights, groups.vfx]) {
    if (!g.visible) continue
    for (const m of g.children) {
      const p = m.position
      if (m.visible && m.userData.entry && inside([p.x, p.y, p.z])) {
        found.push(...levelActorRecords(m))
      }
    }
  }
  const unique = []
  for (const p of found) if (!unique.some(q => samePick(q, p))) unique.push(p)
  const base = how === 'replace' ? [] : picked.slice()
  if (how === 'remove') {
    picked = base.filter(p => !unique.some(q => samePick(q, p)))
  } else {
    picked = base.concat(unique.filter(p => !base.some(q => samePick(q, p))))
  }
  wiringPick = null
  selected = null
  //: a single marker gets its own full panel, as a click on it does
  if (picked.length === 1 && picked[0].marker) {
    const obj = markerObject(picked[0])
    picked = []
    if (obj && obj.userData.plan) {
      selected = { kind: obj.userData.kind, id: obj.userData.id }
      attachTo(selected)
    } else if (obj) {
      wiringPick = obj
    }
  }
  if (!picked.length && !selected && !wiringPick) gizmo.detach()
  refreshPicked()
  showProps()
}

useMarquee({
  dom: renderer.domElement,
  gizmo,
  enabled: () => !!level && mode === 'none',
  onSelect: marqueeSelect,
})

renderer.domElement.addEventListener('click', (ev) => {
  if (!level) return
  if (downAt && Math.hypot(ev.clientX - downAt[0], ev.clientY - downAt[1]) > 4) {
    return                                  // that was a drag, not a pick
  }
  //: an armed decoration tool takes the click (decortools.js)
  if (decor.click(ev)) return
  fxPick = null
  //: one of the level's own lights or effects, while its layer shows: small markers
  //: inside the geometry, so they win a tie against the wall behind them
  if (mode === 'none') {
    const marks = [groups.lights, groups.vfx].filter(g => g.visible)
      .flatMap(g => g.children.filter(c => c.visible && c.userData.entry))
    //: the nearest marker that can be picked: an effect inside a trap or a door (no
    //: actor of its own, no pieces) lets the click through, and so does one behind the
    //: geometry in front of it (a marker inside the piece it hangs off still wins); a
    //: liquid surface, drawn see-through, is not in front of anything
    let recs = []
    const markHits = marks.length ? pick(ev, marks) : []
    const wall = markHits.length
      ? pick(ev, levelPickables().concat(groups.newGeo.children))
        .find(h => !h.object.userData.liquid) : null
    for (const h of markHits) {
      if (wall && h.distance > wall.distance + 2 * EFFECT_PICK_RADIUS) break
      recs = levelActorRecords(h.object)
      if (recs.length) break
    }
    if (recs.length) {
      selected = null
      wiringPick = null
      const already = picked.some(p => recs.some(q => samePick(p, q)))
      if (ev.shiftKey || ev.ctrlKey || ev.metaKey) {
        picked = already ? picked.filter(p => !recs.some(q => samePick(p, q)))
          : picked.concat(recs.filter(q => !picked.some(p => samePick(p, q))))
      } else {
        picked = recs
      }
      if (!picked.length) gizmo.detach()
      refreshPicked()
      showProps()
      return
    }
  }
  // The level's own wiring first: its markers are small and sit inside the geometry,
  // so they have to win a tie against the wall behind them.
  if (mode === 'none') {
    const wired = pick(ev, groups.wiring.children.filter(c => c.userData.wiring && c.visible))
    if (wired.length) {
      picked = []
      selected = null
      wiringPick = wired[0].object
      refreshPicked()
      showProps()
      return
    }
  }
  // A click on something already planned selects it; only an empty click places.
  // Guarded by the mode, like the wiring branch above: a path point marker is a
  // 110uu sphere and a spawner's is 240uu, and points are placed in sequence right
  // beside each other, so while placing, a click aimed at open space very often
  // lands on the PREVIOUS point or on the spawner. Unguarded, that selected the old
  // object and returned without placing anything -- which read as the sidebar
  // showing the wrong thing's name and settings.
  const mine = mode === 'none'
    ? pick(ev, groups.added.children.filter(c => c.userData.plan))
    : []
  if (mine.length) {
    wiringPick = null
    const d = mine[0].object.userData
    selected = { kind: d.kind, id: d.id, index: d.index }
    attachTo(selected)
    drawPlan()
    showProps()
    return
  }
  //: a blocking volume, while its layer shows, when nearer than the level behind it
  if (mode === 'none' && groups.volumes.visible) {
    const vol = pick(ev, groups.volumes.children.filter(c => c.visible))
    const behind = vol.length ? pick(ev, levelPickables()) : []
    if (vol.length && (!behind.length || vol[0].distance <= behind[0].distance)) {
      picked = []
      selected = null
      wiringPick = vol[0].object
      refreshPicked()
      showProps()
      return
    }
  }
  //: The level's own geometry and OUR added meshes are picked TOGETHER so the
  //: nearest wins. Picking them in sequence would have let a wall behind a pasted
  //: copy take the click.
  const hits = pick(ev, levelPickables().concat(groups.newGeo.children,
                                                     planeGroup.children))
  if (!hits.length) {
    //: Clicking the sky -- or any empty space -- DESELECTS EVERYTHING. It used to
    //: clear only the geometry selection, so a planned object or a piece of the
    //: level's wiring stayed selected and the sidebar kept showing it, with the
    //: gizmo still attached to something the user had plainly clicked away from.
    if (!ev.shiftKey) {
      picked = []
      selected = null
      wiringPick = null
      eventsOpen = false
      gizmo.detach()
      refreshPicked()
      showProps()
    }
    return
  }
  // Nothing to place: a click is a selection of the level's own geometry.
  if (mode === 'none') {
    const hit = hits[0]
    const entry = hit.object.userData.addedEntry
    let piece = null
    if (entry) {
      piece = addedRecord(entry)
    } else if (hit.object.userData.planePiece) {
      piece = hit.object.userData.planePiece
    } else {
      const name = hit.object.userData.mesh
      const list = piecesByMesh.get(hit.object.userData.group || name) || []
      piece = list[hit.instanceId ?? 0]
    }
    if (piece) {
      wiringPick = null
      const already = picked.findIndex(p => samePick(p, piece))
      const kin = entry ? compounds.kin(piece) : assemblyOf(piece)
      if (ev.shiftKey || ev.ctrlKey || ev.metaKey) {
        if (already >= 0) picked = picked.filter(p => !kin.includes(p))
        else picked = picked.concat(kin.filter(p => !picked.includes(p)))
      } else {
        picked = kin
      }
      selected = null
      refreshPicked()
      showProps()
    }
    return
  }
  const at = world.worldToLocal(hits[0].point.clone())
  el('readout').innerHTML =
    `<b>${Math.round(at.x)}, ${Math.round(at.y)}, ${Math.round(at.z)}</b>` +
    ' &middot; surface clicked'
  if (mode === 'none') return
  // Placed where it was clicked, on the surface, then raised by eye with the
  // gizmo's vertical arrow or the bracket keys. Typing a height meant knowing one
  // in advance, and a route through the air is judged by looking at it.
  const where = [Math.round(at.x), Math.round(at.y), Math.round(at.z)]
  place(mode, where)
})

addEventListener('keydown', (ev) => {
  if (!viewFocused()) return
  //: NO SHORTCUT FOR SIZE, because `S` IS THE FLY-BACKWARD KEY.
  //:
  //: `FLY` binds w/a/s/d/q/e and this handler bound s to scale, so every time anyone
  //: flew backwards the gizmo quietly became a size gizmo -- "it keeps switching to
  //: size mode when I do things". Move is the default and the mode now changes only
  //: when the user says so: the handle buttons, or g and r, neither of which the
  //: camera uses.
  if (ev.key === 'g' || ev.key === 'G') { gizmo.setMode('translate'); showProps() }
  if (ev.key === 'r' || ev.key === 'R') { gizmo.setMode('rotate'); showProps() }
  if ((ev.key === 'f' || ev.key === 'F') && !ev.ctrlKey && !ev.metaKey && !ev.altKey &&
      !ev.repeat) {
    //: a second F straight after the first sinks the selection into the surface
    const now = performance.now()
    const twice = now - lastDropAt < DOUBLE_TAP_MS
    lastDropAt = twice ? 0 : now
    if (dropPicked(twice)) ev.preventDefault()
  }
  //: M flips left/right (shift: upside down); N inverts the turn (shift: vertically)
  const flip = !ev.ctrlKey && !ev.metaKey && !ev.altKey && !ev.repeat &&
    FLIPS.find(([, , key, shift]) => key === ev.key.toLowerCase() && shift === ev.shiftKey)
  if (flip && pickUnits().length) {
    flipPicked(flip[0])
    ev.preventDefault()
  }
  // Raising a point is the commonest thing to do to it, so it gets keys of its own:
  // a flyer route is placed on the ground it should pass over and then lifted.
  if (ev.key === '[' || ev.key === ']') {
    const step = (ev.key === ']' ? 1 : -1) * (ev.shiftKey ? 500 : 100)
    if (raiseSelected(step)) ev.preventDefault()
  }
  if ((ev.key === 'z' || ev.key === 'Z') && (ev.ctrlKey || ev.metaKey)) {
    undo()
    ev.preventDefault()
    return
  }
  //: COPY and PASTE, for duplicating what is selected.
  //:
  //: A copy of one of the LEVEL's own pieces becomes an ADDED mesh, because the
  //: level is never modified -- the duplicate is ours and the original stays where
  //: it was. Pasted copies are offset so they do not land exactly inside the thing
  //: they came from, where they would be impossible to pick apart.
  if ((ev.key === 'c' || ev.key === 'C') && (ev.ctrlKey || ev.metaKey)) {
    copySelection()
    ev.preventDefault()
    return
  }
  if ((ev.key === 'v' || ev.key === 'V') && (ev.ctrlKey || ev.metaKey)) {
    //: with shift, exactly where the original is
    pasteClipboard(ev.shiftKey)
    ev.preventDefault()
    return
  }
  //: Escape deselects (user 2026-10-21)
  if (ev.key === 'Escape') {
    picked = []
    selected = null
    wiringPick = null
    gizmo.detach()
    refreshPicked()
    showProps()
    ev.preventDefault()
    return
  }
  //: Delete reaches ALL THREE kinds of selection: picked geometry, planned objects and
  //: the level's own wiring.
  if (ev.key === 'Delete' || ev.key === 'Backspace') {
    if (picked.length) removePicked()
    else if (selected) removeSelected()
    else if (wiringPick) removeWiringPick()
    ev.preventDefault()
  }
})

/** Remove one of the level's own spawners/doors/points, recorded in the plan.
 *
 * The same edit the sidebar's button makes, lifted out so a key can do it: the plan
 * carries a `gone` marker rather than the object being dropped, because the level is
 * never modified and the emitter needs to know what to leave out.
 */
function removeWiringPick () {
  if (!wiringPick) return
  if (wiringPick.userData.kind === 'volume') return removeVolume(wiringPick)
  const d = wiringPick.userData
  const why = cannotRemove(d)
  if (why) {
    flash(why)
    return
  }
  snapshot()
  wiringEntry(d.name, d.kind).gone = true
  wiringPick.visible = false
  wiringPick = null
  gizmo.detach()
  drawPlan()
  refreshPicked()
  showProps()
  flash(`${d.name} removed.` + (d.kind === 'point' && d.data && d.data.exit
    ? (movesExit(plan, level) ? ' The exit moves to the rift you placed.'
      : ` Routes into it end at ${riftName('rift')}.`)
    : d.kind === 'point' ? ' Branches into it now go where it went.' : ''))
  if (d.kind === 'spawner') flashSpawnerWaves(d.name, (d.data || {}).index)
}

/** Say how many waves and spawns go with a removed spawner. */
async function flashSpawnerWaves (name, index) {
  if (!level || !index) return
  const query = editing ? '?mission=' + encodeURIComponent(editing) : ''
  try {
    const got = await json('api/spawnerwaves/' + encodeURIComponent(level.level) + query)
    const mine = got[String(index)]
    flash(mine
      ? `${name} removed, and its ${mine.spawns} spawn(s) over ${mine.waves} wave(s) ` +
        'go with it when the mission is built.'
      : `${name} removed. It had no waves.`)
  } catch (err) {
    // the removal stands; only the count is missing
  }
}

// -- the catalogue -----------------------------------------------------------

const KIT_PAGE = 160
//: how near the end of the New Object list (px) the next page is drawn
const KIT_MORE_AT = 600

/** Draw a mesh or a whole kit piece into the level, and record it in the plan. */
let clipboard = []

/** Copy the current selection: level pieces, added meshes, or both.
 *
 * Both kinds come out of `picked`. An added piece keeps its KIND, so copying a kit
 * piece gives back a kit piece with all of its components rather than one mesh.
 */
function copySelection () {
  //: a level kit assembly is copied as ONE kit piece at its actor's matrix, so the copy
  //: moves as a whole; its parts are copied as meshes only while the class is unread
  const kits = []
  const seen = new Set()
  const loose = []
  for (const p of picked) {
    if (p.marker || p.levelActor) continue
    if (p.added || p.source !== 'blueprint' || !p.actor || p.export === undefined) {
      loose.push(p)
      continue
    }
    const key = (p.sub || 'persistent') + '|' + p.export
    const parts = partsCache.get(p.actor)
    const part = parts && parts.find(pt => pt.mesh === p.mesh)
    if (!part) { loose.push(p); continue }
    if (seen.has(key)) continue
    seen.add(key)
    kits.push({ name: p.actor, kind: 'kit',
                matrix: new THREE.Matrix4().fromArray(p.matrix)
                  .multiply(partLocal(part).invert()).toArray() })
  }
  //: the Crystal is one per mission, so it is not copied
  const out = kits.concat(loose.filter(p => !isCrystal(p.added)).map(p => ({
    //: a Fab piece keeps its pack, object, role and grid choices
    ...(p.added && p.added.kind === 'fab'
      ? { pack: p.added.pack, object: p.added.object, role: p.added.role,
          grids: (p.added.grids || []).slice() }
      : {}),
    //: an effect keeps which effect it is
    ...(p.added && p.added.kind === 'fx' ? { fx: p.added.fx } : {}),
    //: a whole copied group stays a group (compounds.js)
    ...(p.added ? compounds.copyFields(p.added, picked) : {}),
    name: p.added ? p.added.name : p.mesh,
    kind: p.added ? p.added.kind : 'mesh',
    matrix: (p.matrix || []).slice(),
  })))
  if (!out.length) {
    flash('Nothing selected to copy.')
    return
  }
  clipboard = out.concat(compounds.copiedDoors(picked))
  flash(`Copied ${out.length} piece(s). Ctrl+V to paste.`)
}

/** Paste the clipboard as added meshes, one cell over (or `inPlace`, exactly where the
 * originals are), selected.
 *
 * Offset by ONE KIT CELL, which is the smallest offset that reads as a separate piece
 * rather than as z-fighting: coincident copies looked like nothing had happened. They
 * arrive SELECTED, with the gizmo already on them, so putting them where they belong
 * is one drag.
 */
const PASTE_OFFSET = 300

async function pasteClipboard (inPlace = false) {
  if (!clipboard.length) {
    flash('Nothing copied yet.')
    return
  }
  snapshot()
  const step = inPlace ? 0 : PASTE_OFFSET
  const shift = new THREE.Matrix4().makeTranslation(step, step, 0)
  let tid = nextTeleporterId()
  const made = clipboard.map(item => ({
    ...item, grids: item.grids ? item.grids.slice() : undefined,
    name: item.name, kind: item.kind,
    //: a pasted teleporter is a new one, linked to nothing yet
    ...(isTeleporter(item) ? { tid: tid++, link: null } : {}),
    //: premultiplied, so the offset is along the LEVEL's axes and a turned copy still
    //: lands beside its original rather than off along its own rotated x
    matrix: new THREE.Matrix4().fromArray(item.matrix)
      .premultiply(shift).toArray() }))
  //: copied groups get new ids; their doors become plan doors (compounds.js)
  compounds.pasted(made)
  for (const entry of made) plan.added.push(entry)
  //: ALL AT ONCE, and before the selection is refreshed -- an added piece's outline
  //: is measured off its drawn parts. Awaiting each copy in turn drew them one after
  //: another, so a paste of several pieces appeared as a trickle; their meshes are
  //: already cached from the originals, so in parallel they land together.
  await Promise.all(made.map(drawAdded))
  picked = made.map(addedRecord)
  selected = null
  wiringPick = null
  refreshPicked()
  showProps()
  flash(`Pasted ${made.length} piece(s)${inPlace ? ' in place' : ''}.`)
}

async function addPiece (row, at) {
  //: a compound kit places its members as a group (compounds.js)
  if (row.kind === 'compound') return compounds.place(row.name, at)
  //: a kit piece with a trap grid lands by its grid: a wall turned to face the
  //: viewer, its grid's base on the clicked surface
  let grids = row.kind === 'kit' ? await kitGrids(row.name, json) : []
  //: a Fab piece (fabui.js): by its main suggested grid like a kit piece, else stood on
  //: the clicked point
  let entry
  if (row.kind === 'fab') {
    const pack = await fab.loadPack(row.pack, json)
    const piece = pack && pack.byObject.get(row.object)
    if (!piece) { flash('That Fab library is not readable now.'); return }
    grids = piece.grids
    entry = fab.newEntry(row.pack, piece, at,
      placeMatrix(grids, at, world.worldToLocal(camera.position.clone())))
  } else {
    const matrix = placeMatrix(grids, at, world.worldToLocal(camera.position.clone())) ||
      new THREE.Matrix4().makeTranslation(at[0], at[1], at[2])
    entry = row.kind === 'fx'
      //: an effect draws as its mesh when it has one (pillow lava, a lava fall), else
      //: as a marker; the build lifts the effect by `fx` (riftlord/lavafx.py)
      ? { name: row.mesh || row.name, kind: 'fx', fx: row.name, matrix: matrix.toArray() }
      : { name: row.name, kind: row.kind, matrix: matrix.toArray() }
  }
  if (row.kind === 'fab') snapshot()
  if (row.kind === 'fx') showEffects()
  plan.added.push(entry)
  await drawAdded(entry)
  //: on a scratch level a piece lands with its footprint on the trap grid, unless
  //: Snap to Grid is off or shift is held
  const nudge = !snapToGrid || shiftDown ? null : grids.length
    ? snapGrid(grids, new THREE.Matrix4().fromArray(entry.matrix), scratchUi.lattice())
    : scratchUi.snapDelta(addedMeshes(entry), entry.matrix[14])
  if (nudge) {
    entry.matrix[12] += nudge[0]
    entry.matrix[13] += nudge[1]
    entry.matrix[14] += nudge[2]
    moveAdded(entry)
  }
  //: Selected on arrival, like a paste: a piece dropped from the catalogue lands at
  //: the clicked point and the next thing anyone does to it is move or turn it.
  picked = [addedRecord(entry)]
  selected = null
  wiringPick = null
  refreshPicked()
  showProps()
}

/** One added entry, drawn. A kit piece is all of its parts, not one mesh.
 *
 * Both caches matter here, because this runs again on every paste, move and undo:
 * the components are fetched per CLASS rather than per copy, and the geometry is
 * SHARED, so pasting a copy of a 128k-vertex portcullis hands out buffers that are
 * already built instead of rebuilding them from the JSON. A group of shared
 * geometry must be emptied with `detach`, never `clear`.
 */
//: Fab pieces whose mesh the library's current build no longer has (the server marks them
//: `missing`): kept in the plan, left out of the scene, named once in one line
const missingFab = new Set()
let missingFabTimer = null

function noteMissingFab (entry) {
  missingFab.add(`${entry.pack}: ${(entry.mesh || entry.object || entry.name).split('/').pop().split('.')[0]}`)
  clearTimeout(missingFabTimer)
  missingFabTimer = setTimeout(() => {
    const names = [...missingFab]
    missingFab.clear()
    flash(`Not in its Fab library any more: ${names.slice(0, 4).join(', ')}${names.length > 4 ? ` and ${names.length - 4} more` : ''}`)
  }, 300)
}

async function drawAdded (entry) {
  if (entry.kind === 'fab' && entry.missing) {
    noteMissingFab(entry)
    return
  }
  const base = new THREE.Matrix4().fromArray(entry.matrix)
  //: the Crystal (crystalui.js): the pieces it brings and its floating crystal
  if (isCrystal(entry)) {
    let got = crystalKnown()
    if (!got) {
      try { got = await crystalInfo(json, level.level, planRift()) } catch (err) { got = null }
    }
    const mesh = await crystalModel(got, partMesh, partLocal, (part, texture) => {
      part.material = texturedMaterial({ side: THREE.DoubleSide, clippingPlanes: CUT_PLANES },
        'api/texture' + texture, null, texture)
    })
    base.decompose(mesh.position, mesh.quaternion, mesh.scale)
    mesh.userData = { addedEntry: entry, mesh: CRYSTAL_LABEL,
                      local: new THREE.Matrix4().toArray() }
    groups.newGeo.add(mesh)
    syncCrystalHidden()
    return
  }
  let parts =[{ mesh: entry.name, loc: [0, 0, 0], rot: [0, 0, 0],
                 scale: [1, 1, 1] }]
  if (entry.kind === 'kit') parts = (await classParts(entry.name)).filter(p => drawsPart(entry, p))
  let drewAny = false
  for (const part of parts) {
    const fxRow = entry.kind === 'fx' && catalogue.find(r => r.kind === 'fx' && r.name === entry.fx)
    //: an effect draws as its own mesh (pillow lava, a lava fall) whatever the entry is
    //: named, else as a marker
    const mesh = entry.kind === 'fx'
      ? (fxRow && fxRow.mesh ? await partMesh({ ...part, mesh: fxRow.mesh }, true) : fxMarker(entry))
      : entry.kind === EVENTS_KIND ? fxMarker(entry) : await partMesh(part, true)
    if (!mesh) continue
    if (part.tint && !part.sprite) tintPart(mesh, part.tint)
    const local = partLocal(part)
    base.clone().multiply(local).decompose(
      mesh.position, mesh.quaternion, mesh.scale)
    //: The component's offset inside the piece is kept, so moving the piece
    //: recomposes its parts from the entry's new matrix rather than redrawing it.
    mesh.userData = { addedEntry: entry, mesh: part.mesh || part.sprite, local: local.toArray() }
    if (entry.kind === 'mesh' || entry.kind === 'kit') {
      tintAddedPart(mesh, entry, entry.kind === 'mesh' ? MESH_PART : part.component)
    }
    groups.newGeo.add(mesh)
    drewAny = true
  }
  //: an actor with nothing to draw (a light, a sound) stands as a marker of its kind
  if (!drewAny && entry.kind === 'kit') {
    const mesh = actorStandIn(entry.name)
    base.decompose(mesh.position, mesh.quaternion, mesh.scale)
    mesh.userData = { addedEntry: entry, mesh: entry.name,
                      local: new THREE.Matrix4().toArray() }
    groups.newGeo.add(mesh)
  }
  if (entry.kind === 'fx') syncAddedEffects()
}

//: The layer an added effect is moved to while the effects layer is off: no camera or
//: pick ray sees it (decortools.js hides added foliage the same way).
const HIDDEN_FX_LAYER = 31

/** Show the plan's added effects only while the effects layer shows. */
function syncAddedEffects () {
  const shown = groups.vfx.visible
  for (const m of groups.newGeo.children) {
    const e = m.userData.addedEntry
    if (!e || e.kind !== 'fx') continue
    m.visible = shown
    m.layers.set(shown ? 0 : HIDDEN_FX_LAYER)
  }
}

/** The drawn meshes belonging to one added entry. */
const addedMeshes = (entry) =>
  groups.newGeo.children.filter(m => m.userData.addedEntry === entry)

/** A picked record for one of OUR added meshes.
 *
 * Added meshes live in `picked` beside the level's own pieces because every operation
 * is the same one -- outline, gizmo, copy, delete -- and the handful of places where
 * they differ branch on `added`. They were in no pick list at all before this, so a
 * placed or pasted piece could not be selected again once the gizmo let go of it.
 */
const addedRecord = (entry) =>
  ({ added: entry, mesh: entry.name, matrix: entry.matrix })

const samePick = (a, b) =>
  (a.marker || b.marker) ? a.marker === b.marker
    : (a.levelActor || b.levelActor) ? a.levelActor === b.levelActor
      : (a.added || b.added) ? a.added === b.added : a.id === b.id

//: A BLUEPRINT ASSEMBLY IS ONE ACTOR IN THE LEVEL.
//:
//: A kit piece -- a wall, an archway, a stair -- is one actor carrying up to 28 drawn
//: parts, and those parts come from its Blueprint CLASS rather than from the level, so
//: the level can only record where the whole assembly sits. Picking one part and
//: dragging it moved that part on screen and saved the whole wall moved: the viewport
//: disagreeing with the file, which is the one thing an editor must not do. Picking a
//: part now picks the assembly. A `component` piece is its own export and is picked
//: alone, which is 1,017 of Scenario_05's 1,370.
function assemblyOf (piece) {
  if (!piece || piece.source !== 'blueprint' || piece.export === undefined) {
    return [piece]
  }
  const tag = piece.sub || 'persistent'
  const kin = ((level && level.pieces) || []).filter(
    p => p.export === piece.export && (p.sub || 'persistent') === tag)
  return kin.length ? kin : [piece]
}

/** Put an added piece's parts where its matrix now says they are. */
function moveAdded (entry) {
  const base = new THREE.Matrix4().fromArray(entry.matrix)
  for (const m of addedMeshes(entry)) {
    base.clone().multiply(new THREE.Matrix4().fromArray(m.userData.local))
      .decompose(m.position, m.quaternion, m.scale)
  }
  if (isCrystal(entry)) syncCrystalHidden()
}

/** Ask again which level pieces the plan's Crystal parks (none without one). */
function syncCrystalHidden () {
  if (!level) return
  const c = planCrystal(plan)
  refreshCrystalHidden(json, level.level, c ? c.matrix : null, applyRemoved)
}

//: An Unreal rotator is (pitch, yaw, roll) about (Y, Z, X), which is NOT a three.js
//: Euler in any default order. Getting this wrong put kit pieces at plausible but
//: wrong angles twice, so it lives in ONE place and everything that draws a
//: Blueprint's component -- an added kit piece, a door -- goes through it.
function partLocal (part) {
  return new THREE.Matrix4().compose(
    new THREE.Vector3(part.loc[0], part.loc[1], part.loc[2]),
    new THREE.Quaternion().setFromEuler(new THREE.Euler(
      //: Unreal's pitch and roll turn the opposite way to three.js's, as in
      //: omd3level._matrix: Rz(yaw) Ry(-pitch) Rx(-roll)
      THREE.MathUtils.degToRad(-part.rot[2]),
      THREE.MathUtils.degToRad(-part.rot[0]),
      THREE.MathUtils.degToRad(part.rot[1]), 'ZYX')),
    new THREE.Vector3(part.scale[0], part.scale[1], part.scale[2]))
}

const meshCache = new Map()
const partsCache = new Map()
const geoCache = new Map()

/** The geometry for one Blueprint component, textured, or null if it will not load.
 *
 * `shared` reuses one geometry and material per mesh name across every instance, for
 * a layer that is redrawn often. It matters here: an interior portcullis is 128k to
 * 173k vertices, and rebuilding that on every plan edit stutters. A group holding
 * shared meshes must be emptied with `detach`, never `clear`.
 */
/** How many material slots a served mesh has: its slot textures, or its sections'. */
function slotCount (m) {
  return Math.max(1, (m.slotTextures || []).length,
                  ...(m.sections || []).map(sec => (sec.slot || 0) + 1))
}

//: pickup sprite textures by path (kitactors.sprite_png), shared by every sprite
const spriteMaps = new Map()

/** A pickup's particle sprite (kitactors.pickup_visual `sprite` part): a camera-facing
 * card of its texture in its tint, 1 uu square before the part's scale. Picked by the
 * view's camera rays only (a sprite cannot be hit by a ray without a camera). */
function spriteMesh (part) {
  if (!spriteMaps.has(part.sprite)) {
    const map = new THREE.TextureLoader().load('api/pickup/sprite?path=' + encodeURIComponent(part.sprite))
    map.colorSpace = THREE.SRGBColorSpace
    spriteMaps.set(part.sprite, map)
  }
  const tint = part.tint || [1, 1, 1]
  const mat = new THREE.SpriteMaterial({ map: spriteMaps.get(part.sprite), transparent: true,
    depthWrite: false, fog: false, clippingPlanes: CUT_PLANES })
  mat.color.setRGB(tint[0], tint[1], tint[2], THREE.SRGBColorSpace)
  const sprite = new THREE.Sprite(mat)
  const cast = sprite.raycast.bind(sprite)
  sprite.raycast = (ray, hits) => { if (ray.camera) cast(ray, hits) }
  return sprite
}

/** A mesh part in its own colour (kitactors.pickup_visual `tint`: a potion in its
 * light's colour): `mesh` with a copy of its material, which a texture still on its way
 * reaches too. */
function tintPart (mesh, tint) {
  if (!tint) return
  const copy = (m) => {
    if (!m || !m.color) return m
    const mat = m.clone()
    mat.color.setRGB(tint[0], tint[1], tint[2], THREE.SRGBColorSpace)
    const waits = textureWaits.get(m)
    if (waits) waits.push(mat)
    return mat
  }
  mesh.material = Array.isArray(mesh.material) ? mesh.material.map(copy) : copy(mesh.material)
}

async function partMesh (part, shared) {
  if (part.sprite) return spriteMesh(part)
  //: Already drawn as part of the level: share what is on screen rather than fetching
  //: and rebuilding it. Looked up fresh every time instead of being copied into
  //: `geoCache`, so it cannot outlive the level it belongs to. Not for a part with an
  //: override material, which the level's copy may not wear.
  if (!part.material && levelGeo.has(part.mesh)) {
    const hit = levelGeo.get(part.mesh)
    return new THREE.Mesh(hit.geo, hit.mat)
  }
  //: shared per mesh and override material
  const key = part.material ? part.mesh + '\u0000' + part.material : part.mesh
  if (shared && geoCache.has(key)) {
    const hit = geoCache.get(key)
    return hit ? new THREE.Mesh(hit.geo, hit.mat) : null
  }
  let m = meshCache.get(part.mesh)
  if (m === undefined) {
    try {
      m = await json('api/mesh/' + encodeURIComponent(part.mesh))
    } catch (err) {
      m = null
    }
    meshCache.set(part.mesh, m)
  }
  if (!m || !m.verts || !m.tris) {
    if (shared) geoCache.set(key, null)
    return null
  }
  noteSlots(part.mesh, m)
  const geo = new THREE.BufferGeometry()
  geo.setAttribute('position',
    new THREE.Float32BufferAttribute(m.verts.flat(), 3))
  if (m.uvs && m.uvs.length === m.verts.length) {
    geo.setAttribute('uv', new THREE.Float32BufferAttribute(m.uvs.flat(), 2))
  }
  geo.setIndex(m.tris)
  geo.computeVertexNormals()
  let mat
  //: each section with its slot's texture, as the level draws it: a Fab library mesh
  //: (riftlord/fablibrary.py), or a shipped mesh with several textured slots
  const multi = (m.sections || []).length && (m.fab ||
    ((m.sections || []).length > 1 && (m.slotTextures || []).some(t => t)))
  if (part.material) {
    //: a kit part's override material over the whole mesh, as the level's pieces wear
    //: theirs: its texture (else the mesh's own) and its tint (api/kit materialTexture,
    //: materialTint)
    const tex = part.materialTexture || m.texture
    mat = texturedMaterial({ side: meshSide(m), clippingPlanes: CUT_PLANES },
      tex && ('api/texture' + tex), tintColour(part.materialTint), tex)
    mat.userData.override = part.material
  } else if (multi) {
    geo.clearGroups()
    for (const sec of m.sections) geo.addGroup(sec.first, sec.tris * 3, sec.slot)
    mat = (m.slotTextures || []).map((t, slot) => texturedMaterial(
      { side: m.fab ? THREE.DoubleSide : meshSide(m), clippingPlanes: CUT_PLANES },
      t && ('api/texture' + t + (m.fab ? '?size=' + fab.FAB_TEXTURE_SIZE : '')),
      tintColour((m.slotTints || [])[slot]), t))
  } else {
    mat = texturedMaterial({ side: meshSide(m), clippingPlanes: CUT_PLANES },
      m.texture && ('api/texture' + m.texture), tintColour(m.tint), m.texture)
  }
  if (shared) geoCache.set(key, { geo, mat })
  return new THREE.Mesh(geo, mat)
}

//: The colour a textured material draws in while its texture is on its way, and an
//: untextured one always: a texture that has not arrived samples as black.
const UNTEXTURED = 0x8d9199

//: material -> the materials that take its texture when it arrives: itself and the
//: copies made of it meanwhile (tintAddedPart)
const textureWaits = new WeakMap()

/** A lit material for an added part: grey until texture `url` arrives, then the
 * texture times `tint`; an `_EM` texture (`path`) lights it rather than colouring it. */
function texturedMaterial (opts, url, tint, path) {
  const mat = new THREE.MeshLambertMaterial({ ...opts, color: UNTEXTURED })
  mat.userData.texture = path || null
  if (!url) return mat
  textureWaits.set(mat, [mat])
  new THREE.TextureLoader().load(url, (map) => {
    map.flipY = UE_TEXTURE_FLIP_Y
    map.wrapS = map.wrapT = THREE.RepeatWrapping
    map.colorSpace = THREE.SRGBColorSpace
    const glow = /_EM$/.test(path || '')
    for (const m of textureWaits.get(mat) || [mat]) {
      //: under a look override, the texture is what the override restores to
      const was = m.userData && m.userData[LOOK_WAS]
      const into = was || m
      into.map = map
      if (m === mat && into.color) into.color.set(tint || 0xffffff)
      if (glow) {
        into.emissiveMap = map
        if (into.emissive) into.emissive.set(0xffffff)
      }
      m.needsUpdate = true
    }
    textureWaits.delete(mat)
  })
  return mat
}

/** The components of a Blueprint class, cached. [] when it has none. */
async function classParts (cls) {
  if (!cls) return []
  if (!partsCache.has(cls)) {
    let got = []
    try {
      const r = await json('api/kit/' + encodeURIComponent(cls))
      got = (r && r.parts) || []
    } catch (err) {
      got = []
    }
    partsCache.set(cls, got)
  }
  return partsCache.get(cls)
}

/** Draw every door as the mesh it actually is.
 *
 * A door was a box marker, which says where it is and nothing about which way it
 * faces or how much of the archway it fills -- and facing is the whole question for
 * a door. The class names the portcullis, and the components carry their own offsets
 * inside the Blueprint, so this is the actor transform composed with each part.
 *
 * The marker stays: it is the click target and it draws with depth testing off, so a
 * door is still selectable through the mesh now wrapped around it.
 */
let doorDraw = 0

async function drawDoorMeshes () {
  //: ONLY the plan's doors.
  //:
  //: The level's own doors are already drawn: a placed Blueprint's components are
  //: level geometry, so the portcullis and the force-field plane arrive in `pieces`
  //: like any other mesh. Drawing them here as well put a second copy on top of
  //: every shipped door -- which is why one appeared to persist after the layer was
  //: switched off: the surviving copy was the level's, and had been there all along.
  //:
  //: A plan edit calls this, and it awaits -- so two runs can overlap and the later
  //: one's `detach` can land between the earlier one's awaits, leaving a half-drawn
  //: layer or a duplicated door. The generation counter makes the newest run the only
  //: one allowed to add, which is the difference between a tool that looks reliable
  //: and one that occasionally loses a door for no reason the user can see.
  const mine = ++doorDraw
  detach(groups.doorMesh)
  const want = (plan.doors || []).map(d => ({
    at: d.at,
    //: A PLAN rotation is a scalar yaw in degrees, not a rotator triple. Reading it
    //: as `rot[2]` gave undefined, so a force field never turned with its door --
    //: the marker rotated and the pane stayed put.
    yaw: typeof d.rotation === 'number'
      ? d.rotation
      : ((d.rotation && d.rotation[1]) || 0),
    cls: d.cls, name: 'door ' + d.id, planned: true,
    //: EVERY door class takes the plan's size. It was the force field's alone, so
    //: resizing a portcullis changed the plan and nothing on screen.
    meshScale: [d.scaleX || 1, d.scaleY || 1, d.scaleZ || 1],
  }))
  for (const d of want) {
    const parts = await classParts(d.cls)
    if (mine !== doorDraw) return
    if (!parts.length) continue
    const base = new THREE.Matrix4().compose(
      new THREE.Vector3(d.at[0], d.at[1], d.at[2]),
      new THREE.Quaternion().setFromEuler(
        new THREE.Euler(0, 0, THREE.MathUtils.degToRad(d.yaw || 0), 'ZYX')),
      new THREE.Vector3(1, 1, 1))
    for (const part of parts) {
      //: A planned force field gets the same translucent treatment as a shipped one,
      //: or the two would read as different objects.
      const field = /forcefield/i.test(part.material || '')
      const mesh = field ? await fieldMesh() : await partMesh(part, true)
      if (mine !== doorDraw) return
      if (!mesh) continue
      //: MULTIPLIED onto the component's own scale, not written over it: both
      //: exterior classes hold their DoorMesh at (1, 1.5, 1), and replacing that
      //: squashed the door by a third as soon as the size handle was touched.
      const use = (d.meshScale && part.component === 'DoorMesh')
        ? { ...part,
            scale: (part.scale || [1, 1, 1]).map((v, i) => v * d.meshScale[i]) }
        : part
      base.clone().multiply(partLocal(use)).decompose(
        mesh.position, mesh.quaternion, mesh.scale)
      mesh.userData = { doorMesh: true, name: d.name, planned: !!d.planned,
                        mesh: part.mesh, component: part.component }
      groups.doorMesh.add(mesh)
    }
  }
}

//: The New Object browser's gameplay cards: [place() kind, label, marker colour, the
//: supported-mod feature it needs (riftlord/core/supportedmods.py) or none].
const GAMEPLAY_OBJECTS = [
  ['rift', 'Rift', '#dc50c8'], ['spawner', 'Spawner', '#e05252'],
  ['door', 'Door', '#e0a040'], ['forcefield', 'Force Field', '#58b8e8'],
  ['point', 'Path Point', '#ffd479'], ['playerStart', 'Player Start', '#9be86a'],
  [UNSTABLE_KIND, UNSTABLE_LABEL, '#d6357a'], [TELEPORTER_KIND, 'Teleporter', '#40d0d0'],
  [CRYSTAL_KIND, CRYSTAL_LABEL, CRYSTAL_CARD_COLOUR, 'crystal'],
  [VOLUME_KIND, 'Blocking Volume', '#ff5a4f']]

//: {feature id: on} of the supported mods detected when New Object last opened
let modFeatures = {}
//: Whether a card that needs `feature` shows: always for one that needs none.
const featureOn = (feature) => !feature || !!modFeatures[feature]

/** Ask which supported mods are detected now (api/supportedmods). */
async function readModFeatures () {
  try {
    modFeatures = (await json('api/supportedmods')).features || {}
  } catch (err) {
    modFeatures = {}
  }
}

/** Place a gameplay card's object at `at`. */
function placeCard (name, at) {
  if (name === TELEPORTER_KIND) return addTeleporter(at)
  if (name === CRYSTAL_KIND) return addCrystal()
  if (name === VOLUME_KIND) return addBlockingVolume(at)
  place(name, at)
}

/** Add a blocking volume standing on `at` (volumes.js addVolume), show the volumes layer
 * and pick it. */
async function addBlockingVolume (at) {
  if (!level) return
  const box = el('layers').querySelector('input[data-layer="volumes"]')
  if (box && !box.checked) {
    box.checked = true
    box.dispatchEvent(new Event('change', { bubbles: true }))
  }
  groups.volumes.visible = true
  const m = await addVolume(groups.volumes, at)
  if (!m) return flash('The blocking volume cannot be read now.')
  picked = []
  selected = null
  wiringPick = m
  refreshPicked()
  showProps()
}

// -- the Crystal (crystalui.js, riftlord/mapeditor/crystal.py) ----------------------

/** The rift a new Crystal stands on: the selected rift (placed or the level's), else
 * planRift(). */
function crystalRiftAt () {
  const found = findObject(selected)
  if (found && found.kind === 'rift') return found.obj.at
  if (wiringPick && wiringPick.userData.kind === 'rift') {
    const p = wiringPick.position
    return [p.x, p.y, p.z]
  }
  return planRift()
}

/** Place the Crystal on the rift, facing the routes that reach it, and pick it; a mission
 * has one, so with one placed that one is picked. */
async function addCrystal () {
  if (!level) return
  const have = planCrystal(plan)
  if (!have) {
    let got
    try {
      got = await crystalInfo(json, level.level, crystalRiftAt())
    } catch (err) {
      return report('crystal', err)
    }
    if (!got || !got.spot) return flash('This level has no rift for the Crystal.')
    snapshot()
    const entry = { name: CRYSTAL_CLASS, kind: CRYSTAL_KIND, matrix: got.spot.matrix.slice(),
                    crystal: { ...structuredClone(got.defaults), rifts: [] } }
    plan.added.push(entry)
    await drawAdded(entry)
  }
  picked = [addedRecord(planCrystal(plan))]
  selected = null
  wiringPick = null
  refreshPicked()
  showProps()
}

// -- teleporters (riftlord/apply.py link_teleporters) -----------------------------

/** Whether a plan `added` entry is a teleporter. */
const isTeleporter = (a) => !!a && a.kind === 'kit' && a.name === TELEPORT_CLASS
const teleporters = () => (plan.added || []).filter(isTeleporter)
const nextTeleporterId = () => 1 + teleporters().reduce((m, a) => Math.max(m, a.tid || 0), 0)

/** Place a teleporter; one placed while another stands unlinked is linked to it. */
/** Add a plan entry a side panel made (a chest spot, controllers.js), drawn and picked. */
async function addPickedEntry (entry) {
  snapshot()
  plan.added.push(entry)
  await drawAdded(entry)
  picked = [addedRecord(entry)]
  selected = null
  wiringPick = null
  refreshPicked()
  showProps()
}

async function addTeleporter (at) {
  if (!level) return
  snapshot()
  const lonely = [...teleporters()].reverse().find(a => a.link === null || a.link === undefined)
  const entry = { name: TELEPORT_CLASS, kind: 'kit', tid: nextTeleporterId(),
                  link: lonely ? lonely.tid : null,
                  matrix: new THREE.Matrix4().makeTranslation(at[0], at[1], at[2]).toArray() }
  if (lonely) lonely.link = entry.tid
  plan.added.push(entry)
  await drawAdded(entry)
  picked = [addedRecord(entry)]
  selected = null
  wiringPick = null
  refreshPicked()
  showProps()
  drawTeleportLinks()
  flash(lonely ? `Teleporter ${entry.tid} placed and linked with teleporter ${lonely.tid}.`
    : `Teleporter ${entry.tid} placed. Place another to link the pair, or pick where it ` +
      'sends players under Teleporter.')
}

/** Link teleporter `a` with the one whose id is `tid` (null unlinks it); a teleporter
 * links to one other only, so either end's old partner is left unlinked. */
function linkTeleporter (a, tid) {
  snapshot()
  const all = teleporters()
  for (const t of all) if (t.link === a.tid || (tid !== null && t.link === tid)) t.link = null
  a.link = tid
  const b = all.find(t => t.tid === tid)
  if (b) b.link = a.tid
  drawTeleportLinks()
}

//: The lines joining linked teleporters.
const teleportLines = new THREE.Group()
teleportLines.name = 'teleport-links'

/** Draw a line between each linked pair. */
function drawTeleportLinks () {
  if (teleportLines.parent !== world) world.add(teleportLines)
  clear(teleportLines)
  const all = teleporters()
  for (const a of all) {
    const b = all.find(t => t.tid === a.link)
    if (!b || b.tid < a.tid) continue
    const pa = [a.matrix[12], a.matrix[13], a.matrix[14] + 150]
    const pb = [b.matrix[12], b.matrix[13], b.matrix[14] + 150]
    teleportLines.add(line(pa, pb, 0x40d0d0))
  }
}

/** The Teleporter section for one picked teleporter, or ''. */
function teleportSection (piece) {
  if (!piece || !isTeleporter(piece.added)) return ''
  const a = piece.added
  const others = teleporters().filter(t => t !== a)
  return `<h2>Teleporter ${a.tid}</h2><label class="pick"><span>Sends players to</span>` +
    '<select id="tplink"><option value="">Nowhere (not linked)</option>' +
    others.map(t => `<option value="${t.tid}"${t.tid === a.link ? ' selected' : ''}>` +
      `Teleporter ${t.tid}</option>`).join('') + '</select></label>' +
    '<div class="hint">A link goes both ways: the other end sends players back here.' +
    (others.length ? '' : ' Place another teleporter to link to.') + '</div>'
}

/** Whether catalogue row `r` is under New Object filter `cat`. */
function inKitFilter (r, cat) {
  if (cat === 'all') return true
  if (cat === 'trapgrid') return !!r.trapgrid
  //: effect meshes (meshcat 'effect') sit with the placeable effects
  if (cat === 'effects') return r.category === 'effects' || r.category === 'effect'
  return r.category === cat
}

/** Hide the New Object filters nothing is under (the current one falls back to all). */
function syncKitFilters () {
  for (const b of el('kitfilters').querySelectorAll('[data-cat]')) {
    const cat = b.dataset.cat
    b.hidden = cat !== 'all' && !(cat === 'gameplay'
      ? GAMEPLAY_OBJECTS.some(([, , , feature]) => featureOn(feature))
      : catalogue.some(r => featureOn(r.feature) && inKitFilter(r, cat)))
    if (b.hidden && cat === kitFilter) kitFilter = 'all'
  }
  for (const b of el('kitfilters').querySelectorAll('[data-cat]')) {
    b.classList.toggle('on', b.dataset.cat === kitFilter)
  }
}

/** Fill the compound filter's Group and Art Set dropdowns with the values some compound
 * carries under the other one's choice, and show them only under that filter. */
function syncCompoundTags () {
  const box = el('compoundtags')
  box.hidden = kitFilter !== 'compound'
  if (box.hidden) return
  const rows = catalogue.filter(r => r.kind === 'compound' && featureOn(r.feature))
  //: a compound offering walls of several art sets is filed under each
  const has = (r, field, v) => [].concat(r[field] || []).includes(v)
  const fill = (id, field, value, other, otherValue) => {
    const values = [...new Set(rows.filter(r => otherValue === 'all' || has(r, other, otherValue))
      .flatMap(r => [].concat(r[field] || [])))].sort()
    if (value !== 'all' && !values.includes(value)) value = 'all'
    el(id).innerHTML = '<option value="all">All</option>' + values.map(v =>
      `<option value="${escapeText(v)}"${v === value ? ' selected' : ''}>${escapeText(v)}</option>`).join('')
    return value
  }
  compoundGroup = fill('cpgroup', 'group', compoundGroup, 'set', compoundSet)
  compoundSet = fill('cpset', 'set', compoundSet, 'group', compoundGroup)
}

//: The preview drawing's version (omd3thumb.STYLE), in every preview URL: the browser keeps
//: previews a day (server.py THUMB_CACHE), so a new drawing needs a new URL.
const THUMB_STYLE = 'tex1'

/** A New Object preview's URL (/api/thumb/<name>). */
const thumbUrl = (name) => 'api/thumb/' + encodeURIComponent(name) + '?v=' + THUMB_STYLE

/** One New Object card. */
function kitCard (r) {
  return `<div class="kitcard${r.trapgrid ? ' grid' : ''}" draggable="true"` +
    ` data-name="${r.name}" data-kind="${r.kind}" title="${r.name}">` +
    (r.thumb
      //: eager: previews are cached on disk and by the browser (riftlord/server.py); a
      //: compound's is drawn when its card scrolls into view
      ? `<img src="${thumbUrl(r.thumb)}"${r.kind === 'compound' ? ' loading="lazy"' : ''} alt="">`
      : r.category === 'actors'
        ? '<div style="aspect-ratio:1;display:grid;place-items:center;background:var(--thumb);' +
          'border-radius:3px"><span style="width:30%;aspect-ratio:1;border-radius:50%;' +
          `background:${actorCardColour(r)}"></span></div>`
        : '<div style="height:46px;display:grid;place-items:center;font-size:10px;' +
          `color:var(--dim)">${r.parts || 0} parts</div>`) +
    `<div class="n">${r.label || r.name.replace(/_C$/, '')}</div>` +
    `<div class="d">${r.trapgrid ? (r.surfaces || []).join('/') + ' grid'
      : r.kind === 'compound' ? `${r.group || 'compound'} &middot; ${[].concat(r.set || []).join('/')}`
        : r.kind === 'fx' || r.category === 'actors' ? (r.group || 'effect') : r.category}` +
    `${r.kind === 'kit' && r.category !== 'actors' ? ` &middot; ${r.parts}p` : ''}` +
    '</div></div>'
}

/** Draw the next page of the shown rows, and read the page after it ahead (not compound
 * previews, which may be drawn for the first time). */
function drawMoreCards () {
  const next = kitShown.slice(kitDrawn, kitDrawn + KIT_PAGE)
  if (!next.length) return
  el('kitgrid').insertAdjacentHTML('beforeend', next.map(kitCard).join(''))
  kitDrawn += next.length
  for (const r of kitShown.slice(kitDrawn, kitDrawn + KIT_PAGE)) {
    if (r.thumb && r.kind !== 'compound') new Image().src = thumbUrl(r.thumb)
  }
}

function listCatalogue () {
  syncKitFilters()
  syncCompoundTags()
  const wanted = el('kitsearch').value.toLowerCase().trim()
  const terms = wanted ? wanted.split(/\s+/) : []
  const gameplay = kitFilter === 'all' || kitFilter === 'gameplay'
    ? GAMEPLAY_OBJECTS.filter(([, label, , feature]) => featureOn(feature) &&
        terms.every(t => label.toLowerCase().includes(t)))
    : []
  const cards = gameplay.map(([kind, label, colour]) =>
    `<div class="kitcard" draggable="true" data-name="${kind}" data-kind="gameplay"` +
    ` title="${label}">` +
    //: the Crystal shows its model (crystal.thumbnail), the rest a marker dot
    (kind === CRYSTAL_KIND
      ? `<img src="${thumbUrl(CRYSTAL_THUMB)}" alt="">`
      : '<div style="aspect-ratio:1;display:grid;place-items:center;' +
        'background:var(--thumb);border-radius:3px"><span style="width:38%;aspect-ratio:1;' +
        `border-radius:50%;background:${colour}"></span></div>`) +
    `<div class="n">${label}</div><div class="d">gameplay</div></div>`).join('')
  if (kitFilter === 'gameplay') {
    el('kitgrid').innerHTML = cards
    el('kitcount').textContent = ''
    kitShown = []
    kitDrawn = 0
    return
  }
  kitShown = catalogue.filter(r => {
    if (!featureOn(r.feature)) return false
    if (!inKitFilter(r, kitFilter)) return false
    if (kitFilter === 'compound' && ((compoundGroup !== 'all' && r.group !== compoundGroup) ||
                                     (compoundSet !== 'all' && ![].concat(r.set || []).includes(compoundSet)))) return false
    const name = (r.name + ' ' + (r.label || '') + ' ' + (r.group || '') + ' ' + [].concat(r.set || []).join(' ')).toLowerCase()
    return terms.every(t => name.includes(t))
  })
  el('kitgrid').innerHTML = cards
  el('kitgrid').scrollTop = 0
  kitDrawn = 0
  drawMoreCards()
  el('kitcount').textContent = `${kitShown.length} of ${catalogue.length}`
}

/** A New Object card's dot for an actor with no preview: its light's colour. */
function actorCardColour (r) {
  const l = (r.lights || [])[0]
  if (!l) return '#' + new THREE.Color(ACTOR_MARK_COLOUR[r.group] || ACTOR_MARK_DEFAULT).getHexString()
  return '#' + new THREE.Color().setRGB(l.colour[0], l.colour[1], l.colour[2],
                                        THREE.LinearSRGBColorSpace).getHexString()
}

// -- Fab libraries (fabui.js) --------------------------------------------------

let fabPack = null
let fabKind = 'all'

/** Fill the Fab browser for the chosen pack and filters. */
async function listFab () {
  const list = await fab.libraries(json).catch(() => [])
  if (!list.length) {
    el('fabpack').innerHTML = ''
    el('fabcount').textContent = 'No Fab libraries are installed. Convert a pack from your ' +
      'Epic vault first; its library appears here.'
    el('fabgrid').innerHTML = ''
    return
  }
  if (!fabPack || !list.some(l => l.id === fabPack && l.status === 'ready')) {
    fabPack = (list.find(l => l.status === 'ready') || {}).id || null
  }
  el('fabpack').innerHTML = fab.packOptions(list, fabPack)
  const off = list.filter(l => l.status !== 'ready')
  if (!fabPack) {
    el('fabcount').textContent = off.map(l => l.detail).join(' ')
    el('fabgrid').innerHTML = ''
    return
  }
  el('fabcount').textContent = 'reading the library…'
  const pack = await fab.loadPack(fabPack, json)
  if (!pack) {
    el('fabcount').textContent = 'That library cannot be read now (being rebuilt?).'
    el('fabgrid').innerHTML = ''
    return
  }
  //: the filters no piece of this library is under are hidden
  for (const b of el('fabfilters').querySelectorAll('[data-fabkind]')) {
    const k = b.dataset.fabkind
    b.hidden = k !== 'all' && !(pack.pieces || []).some(p => (p.kinds || []).includes(k))
    if (b.hidden && k === fabKind) fabKind = 'all'
  }
  for (const b of el('fabfilters').querySelectorAll('[data-fabkind]')) {
    b.classList.toggle('on', b.dataset.fabkind === fabKind)
  }
  const got = fab.cardsHtml(pack, fabKind, el('fabsearch').value)
  el('fabgrid').innerHTML = got.html
  el('fabcount').textContent = `${got.shown} of ${got.total}` +
    (got.shown > fab.FAB_PAGE ? ` (showing ${fab.FAB_PAGE})` : '') +
    ` · pack scale ${pack.scale}`
  const laid = (plan.fabGrids || []).length
  el('fablaid').textContent = laid ? `${laid} grid(s) laid across runs of pieces` : ''
}

/** The kit panel's source switch and the Fab browser's controls. */
function wireFabBrowser () {
  el('fabfilters').innerHTML = fab.FAB_KINDS.map(([k, label]) =>
    `<button data-fabkind="${k}"${k === fabKind ? ' class="on"' : ''}>${label}</button>`).join('')
  el('kitsource').addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-source]')
    if (!b) return
    const isFab = b.dataset.source === 'fab'
    for (const o of el('kitsource').querySelectorAll('[data-source]')) {
      o.classList.toggle('on', o === b)
    }
    el('fabpanel').hidden = !isFab
    el('kitpanel').hidden = isFab
    if (isFab) listFab().catch(err => report('Fab libraries', err))
  })
  el('fabpack').addEventListener('change', (ev) => {
    fabPack = ev.target.value
    listFab().catch(err => report('Fab libraries', err))
  })
  el('fabsearch').addEventListener('input', () => listFab())
  el('fabfilters').addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-fabkind]')
    if (!b) return
    fabKind = b.dataset.fabkind
    for (const o of el('fabfilters').querySelectorAll('[data-fabkind]')) {
      o.classList.toggle('on', o.dataset.fabkind === fabKind)
    }
    listFab()
  })
  el('fabgrid').addEventListener('click', (ev) => {
    const card = ev.target.closest('[data-fab]')
    if (!card || !level) return
    addPiece({ kind: 'fab', pack: card.dataset.pack, object: card.dataset.object },
             viewDropPoint())
  })
  el('fabgrid').addEventListener('dragstart', (ev) => {
    const card = ev.target.closest('[data-fab]')
    if (!card) return
    ev.dataTransfer.setData('text/riftlord-piece', JSON.stringify(
      { kind: 'fab', pack: card.dataset.pack, object: card.dataset.object }))
    ev.dataTransfer.effectAllowed = 'copy'
  })
}

/** The picked Fab entries, when every picked thing is one. */
const pickedFab = () => picked.length && picked.every(p => p.added && p.added.kind === 'fab')
  ? picked.map(p => p.added) : []

/** Wire the Fab section of the properties panel (fabui.propsHtml). */
function wireFabProps (body) {
  const entries = pickedFab()
  const list = body.querySelector('#fabgridlist')
  if (list && entries.length === 1) {
    list.addEventListener('change', (ev) => {
      const box = ev.target.closest('[data-fabgrid]')
      if (!box) return
      snapshot()
      const e = entries[0]
      const on = new Set(e.grids || [])
      if (box.checked) on.add(box.dataset.fabgrid)
      else on.delete(box.dataset.fabgrid)
      const piece = fab.pieceOf(e)
      e.grids = piece ? piece.grids.map(g => g.id).filter(id => on.has(id)) : [...on]
      trapGridAt = 0
    })
  }
  const run = body.querySelector('#fabrun')
  if (run) {
    run.addEventListener('click', () => {
      const got = fab.runFor(entries, json)
      if (!got.grid) { flash('No run grid: ' + got.why + '.'); return }
      snapshot()
      plan.fabGrids = plan.fabGrids || []
      const id = plan.fabGrids.reduce((m, g) => Math.max(m, g.id || 0), 0) + 1
      const used = [...new Set(got.used.map(u => u.entry))]
      plan.fabGrids.push({ ...got.grid, id,
                           pieces: used.map(e => plan.added.indexOf(e)).filter(i => i >= 0) })
      for (const u of got.used) u.entry.grids = (u.entry.grids || []).filter(g => g !== u.id)
      trapGridAt = 0
      flash(`Laid one ${2 * got.grid.extent[1] / 300} × ${2 * got.grid.extent[2] / 300} ` +
            'grid across the run.')
      showProps()
    })
  }
  for (const b of body.querySelectorAll('[data-fabunlay]')) {
    b.addEventListener('click', () => {
      snapshot()
      plan.fabGrids = (plan.fabGrids || []).filter(g => String(g.id) !== b.dataset.fabunlay)
      trapGridAt = 0
      showProps()
    })
  }
}

// -- loading and saving ------------------------------------------------------

function frameOn (bounds) {
  const [lo, hi] = bounds
  const mid = new THREE.Vector3((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2,
                                (lo[2] + hi[2]) / 2)
  const span = Math.max(hi[0] - lo[0], hi[1] - lo[1], 6000)
  const at = world.localToWorld(mid.clone())
  controls.target.copy(at)
  camera.position.copy(at).add(
    new THREE.Vector3(span * 0.45, span * 0.6, span * 0.45))
  controls.update()
}

//: A loaded texture, or null, as a promise. `TextureLoader.load` returns before the
//: image arrives, so a material that failed to load and one still loading looked the
//: same -- and the look pickers reported neither.
const lookTex = new Map()

function loadTexture (url) {
  if (lookTex.has(url)) return Promise.resolve(lookTex.get(url))
  return new Promise((resolve) => {
    new THREE.TextureLoader().load(url, (t) => {
      t.wrapS = t.wrapT = THREE.RepeatWrapping
      t.colorSpace = THREE.SRGBColorSpace
      t.flipY = UE_TEXTURE_FLIP_Y
      t.needsUpdate = true
      lookTex.set(url, t)
      resolve(t)
    }, undefined, () => { lookTex.set(url, null); resolve(null) })
  })
}

//: What a material looked like before a look override painted over it, kept ON the
//: material so that switching the picker back to (unchanged) puts it back. A side
//: table would go stale: the materials are rebuilt whenever the level is redrawn.
const LOOK_WAS = '__rl_lookWas'

/** Paint `map` over these meshes' materials, or restore them when `map` is null. */
function paintMaterials (meshes, map) {
  for (const mesh of meshes) {
    const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material]
    for (const mat of mats) {
      if (!mat) continue
      mat.userData = mat.userData || {}
      if (!mat.userData[LOOK_WAS]) {
        mat.userData[LOOK_WAS] = {
          map: mat.map || null,
          emissiveMap: mat.emissiveMap || null,
          color: mat.color ? mat.color.clone() : null,
          emissive: mat.emissive ? mat.emissive.clone() : null,
        }
      }
      const was = mat.userData[LOOK_WAS]
      if (map) {
        mat.map = map
        //: The tint and the emissive glow stand in for a material we could not read.
        //: With a real one painted over them they would colour it, so both go.
        if (mat.color) mat.color.set(0xffffff)
        if (mat.emissive) mat.emissive.set(0x000000)
        mat.emissiveMap = null
      } else {
        mat.map = was.map
        mat.emissiveMap = was.emissiveMap
        if (was.color && mat.color) mat.color.copy(was.color)
        if (was.emissive && mat.emissive) mat.emissive.copy(was.emissive)
      }
      mat.needsUpdate = true
    }
  }
}

//: Only two of OMD3's forty skies have a sphere in the level, so on most maps there is
//: nothing in the viewport to repaint and choosing a sky looked like it did nothing.
//: The game paints the level's own sky component either way, so a plain dome stands in
//: for the preview -- and is taken away again when the picker goes back to
//: (unchanged). Its UVs are a sphere's, not the shipped skybox's, so a seam here is
//: the stand-in and not the material.
function lookDome () {
  if (skyMesh) return skyMesh
  const dome = new THREE.Mesh(
    new THREE.SphereGeometry(SKY_RADIUS, 32, 20),
    new THREE.MeshBasicMaterial({ side: THREE.BackSide, fog: false,
                                  depthWrite: false, color: 0xffffff }))
  dome.renderOrder = -1
  dome.userData = { sky: true, fromLook: true }
  groups.sky.add(dome)
  skyMesh = dome
  return dome
}

function dropDome () {
  if (!skyMesh || !skyMesh.userData.fromLook) return
  groups.sky.remove(skyMesh)
  skyMesh.geometry.dispose()
  skyMesh.material.dispose()
  skyMesh = null
}

/** Repaint one set of meshes, and say what happened. Null material restores them. */
async function repaint (material, meshes, what) {
  if (!meshes.length) {
    return material ? 'this level has no ' + what + ' drawn' : null
  }
  if (!material) {
    paintMaterials(meshes, null)
    return what + ' back as the level has it'
  }
  let got
  try {
    got = await json('api/look/' + encodeURIComponent(material))
  } catch (err) {
    return 'could not read ' + material
  }
  if (!got || !got.texture) return material + ' has no texture to show'
  const map = await loadTexture('api/texture' + got.texture)
  if (!map) return material + "'s texture would not load"
  paintMaterials(meshes, map)
  return what + ': ' + material
}

/** Show the plan's chosen sky and pool materials on what is already drawn.
 *
 * Both halves run at once and both report, because the two failures that look
 * identical from outside -- nothing drawn to repaint, and a texture that will not
 * load -- are the two this has to tell apart out loud.
 */
async function previewLook () {
  const look = plan.look || {}
  const pools = []
  for (const [key, inst] of instByMesh) {
    if (piecesAreLiquid(piecesByMesh.get(key) || [])) pools.push(inst)
  }
  const sky = look.sky ? [lookDome()] : (skyMesh ? [skyMesh] : [])
  const said = await Promise.all([
    repaint(look.sky, sky, 'sky'),
    repaint(look.liquid, pools, 'pools'),
  ])
  if (!look.sky) dropDome()
  await previewSkyFog(look.sky)
  const words = said.filter(Boolean)
  if (words.length) flash(words.join(' \u00b7 '))
}

/** The horizon and distance tint of a chosen sky: its home level's fog colour, which
 * the build writes too unless Map Settings sets a fog colour. No sky: the level's own. */
async function previewSkyFog (material) {
  const own = ((plan.world || {}).fogColour)
  let rgba = own || null
  if (!rgba && material) {
    try {
      const got = await json('api/look/' + encodeURIComponent(material))
      rgba = (got && got.fog && got.fog.fogColour) || null
    } catch (err) {
      rgba = null
    }
  }
  if (!rgba) {
    if (level && level.sky) applySky(level.sky)
    return
  }
  const horizon = linear(rgba, 0x0d1016)
  scene.background = horizon
  if (scene.fog) scene.fog.color.copy(horizon)
}

//: /api/mapinfo for the open level: world settings, the mission's rows and Guid.
let mapInfo = null
//: /api/guid/check's answer for `mission.guid`, or null while it is asked.
let guidCheck = null

/** Take a Guid typed in Details ('' keeps the mission's own); /api/guid/check's answer,
 * or null for ''. */
async function setGuid (text) {
  mission.guid = text
  guidCheck = null
  if (!text) return null
  const got = await json('api/guid/check?guid=' + encodeURIComponent(text) +
    (editing ? '&mission=' + encodeURIComponent(editing) : ''))
  if (mission.guid !== text) return got
  if (got.guid && !got.error) mission.guid = got.guid
  guidCheck = got
  return got
}

//: The Details dialog's Guid field (web/shared/MissionDetails.svelte `guidTools`).
function guidTools () {
  const own = (mapInfo && mapInfo.guid) || ''
  return {
    own,
    value: mission.guid || own,
    check: (text) => setGuid(text === own ? '' : text),
    generate: async () => (await json('api/guid/new')).guid,
  }
}

/** Fetch the open level's map settings, then redraw the panel if it is showing. */
async function loadMapInfo () {
  if (!level) return
  const name = level.level
  mapInfo = null
  let got
  try {
    got = await json('api/mapinfo/' + encodeURIComponent(name) +
      (editing ? '?mission=' + encodeURIComponent(editing) : ''))
  } catch (err) {
    got = { worldError: err.message }
  }
  if (!level || level.level !== name) return
  mapInfo = got
  takeNewMissionDefaults()
  if (!findObject(selected) && !wiringPick && !picked.length) showProps()
}

//: The level whose plan is a new mission still owed its world defaults
//: (mapsettings.newMissionWorld); null once given or when a saved plan was resumed.
let newMissionFor = null

/** Give a new mission's plan its world defaults once the map info is in. */
function takeNewMissionDefaults () {
  if (!level || newMissionFor !== level.level || !mapInfo || mapInfo.worldError) return
  newMissionFor = null
  plan.world = plan.world || {}
  if (newMissionWorld(mapInfo, plan.world) && showingMapSettings()) showProps()
}

/** The right sidebar with nothing selected: the level's map settings. */
function showMapSettings () {
  const body = el('propsbody')
  el('app').classList.add('props')
  const own = allLevels.find(l => l.name === level.level)
  const title = el('modtitle').value.trim()
  //: a level from scratch is its own map: its title, not its host's; a mission shows
  //: its own title and level, and the shipped level it is built on below them
  const ctx = plan.scratch
    ? { level, plan, info: mapInfo, levelName: missionMap,
        levelTitle: title || (plan.mission && plan.mission.title) || plan.scratch.name || 'New Level' }
    : editing
      ? { level, plan, info: mapInfo, levelName: missionMap,
          levelTitle: title || (own && own.title) || level.level,
          source: { title: (own && own.title) || '', name: level.level } }
      : { level, plan, info: mapInfo, levelTitle: own && own.title }
  body.innerHTML = mapSettingsHtml(ctx)
  wireMapSettings(body, ctx, {
    onWorld: (world, events) => {
      snapshot()
      plan.world = world
      if (events) plan.events = events
      showProps()
      previewSkyFog((plan.look || {}).sky).catch(err => report('fog', err))
    },
  })
  reconnectSection(body, true)
  minimapSection(body)
  levelFxSection(body)
}

//: the weather presets (riftlord/weather.py), fetched once; null until asked
let weatherChoices = null

/** Whether the right sidebar is showing Map Settings. */
const showingMapSettings = () =>
  !!level && !findObject(selected) && !wiringPick && !fxPick && !picked.length

/** Append Weather to Map Settings (levelfxui.js). */
function levelFxSection (body) {
  if (weatherChoices === null) {
    weatherChoices = []
    json('api/weather').then((got) => {
      weatherChoices = got || []
      if (showingMapSettings()) showProps()
    }).catch(() => {})
  }
  //: the catalogue says which effects are weather
  if (!catalogue.length && !catalogueReading) {
    catalogueReading = json('api/catalogue').then((got) => {
      if (!catalogue.length) catalogue = got || []
      if (showingMapSettings()) showProps()
    }).catch(() => {}).finally(() => { catalogueReading = null })
  }
  const box = document.createElement('div')
  box.innerHTML = levelFxHtml(level, plan, catalogue, weatherChoices)
  body.appendChild(box)
  wireLevelFx(box, {
    level, plan, catalogue, weather: weatherChoices,
    onWeather: async ({ kind, keep, drop }) => {
      let got = null
      if (kind) {
        try {
          got = await json('api/weather/suggest/' + encodeURIComponent(level.level), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Rift-Lord': '1' },
            body: JSON.stringify({ kind, plan }),
          })
        } catch (err) {
          flash('Weather: ' + err.message)
          showProps()
          return
        }
        if (!got.added.length) {
          flash('No play area to put weather over yet: place a rift, a route or player starts.')
          showProps()
          return
        }
      }
      snapshot()
      plan.added = plan.added.filter(a => !isWeatherItem(a, catalogue))
      const gone = new Set(plan.removedFx || [])
      for (const k of drop) gone.add(k)
      for (const k of keep) gone.delete(k)
      plan.removedFx = [...gone].sort()
      if (got) for (const item of got.added) plan.added.push(item)
      applyRemovedFx()
      picked = picked.filter(p => !p.added || plan.added.includes(p.added))
      await redrawAdded()
      refreshPicked()
      showProps()
    },
  })
}

//: the catalogue being read for Map Settings, or null
let catalogueReading = null

//: the redrawn minimap's preview for the open plan (minimapui.js)
const minimapPreview = previewCache(json, () => {
  if (level && !findObject(selected) && !wiringPick && !picked.length) showProps()
})

/** Append the Minimap section to Map Settings when the plan changes floor or routes. */
function minimapSection (body) {
  if (!minimapOffered(plan, level)) return
  const on = minimapPreviewed(plan, level)
  const box = document.createElement('div')
  box.innerHTML = minimapHtml(plan, on ? minimapPreview.get(level.level, plan) : null, level)
  body.appendChild(box)
  wireMinimap(box, plan, (block) => {
    snapshot()
    if (block) plan.minimap = block
    else delete plan.minimap
    showProps()
  })
}

/** Append Reconnect Routes to a sidebar panel when the rift or exit point has moved. */
function reconnectSection (body, every) {
  if (!level) return
  const prop = reconnectProposal(plan, level)
  if (!prop && !every) return
  //: Map Settings lists every route; a rift or point panel only those to reconnect
  const list = levelRoutes(plan, level).filter(r => every ||
    ((prop && prop.steps) || []).some(st => r.refs.has(st.ref)))
  const box = document.createElement('div')
  box.innerHTML = reconnectHtml(prop, list)
  body.appendChild(box)
  wireReconnect(box, (chosen) => {
    snapshot()
    applyProposal(plan, level, prop, chosen)
    drawPlan()
    showProps()
    flash('Routes reconnected. Ctrl+Z undoes it.')
  })
}

//: Plan changes since the last save, counted by snapshot().
let changes = 0
let savedChanges = 0
//: the shell asks before it opens a Missions item here (web/shell App.svelte openState):
//: whether a level is open and whether it has changes not saved
window.addEventListener('message', (e) => {
  if (e.source !== window.parent || !e.data || e.data.type !== 'riftlord-open-state') return
  window.parent.postMessage({ type: 'riftlord-open-state', id: e.data.id, open: !!level,
                              unsaved: !!level && changes !== savedChanges }, '*')
})
//: How long Close waits for its confirming second click, in milliseconds.
const CLOSE_CONFIRM_MS = 4000
let closeArmedUntil = 0
//: Bumped whenever a level opens or closes, so a slow draw of the last one stops.
let levelGen = 0

/** Close the open level; with unsaved changes, only on a second click. */
function requestClose () {
  if (!level) return
  const button = el('closelevel')
  if (changes !== savedChanges && Date.now() > closeArmedUntil) {
    closeArmedUntil = Date.now() + CLOSE_CONFIRM_MS
    button.textContent = 'Discard Changes?'
    button.classList.add('warn')
    flash('Unsaved changes. Click again to close without saving.')
    setTimeout(() => {
      if (Date.now() >= closeArmedUntil) {
        button.textContent = 'Close'
        button.classList.remove('warn')
      }
    }, CLOSE_CONFIRM_MS)
    return
  }
  closeArmedUntil = 0
  button.textContent = 'Close'
  button.classList.remove('warn')
  closeLevel()
}

/** Back to the state before any level was opened. */
function closeLevel () {
  levelGen += 1
  level = null
  mapInfo = null
  editing = null
  plan = { level: null, version: 2, look: {}, world: {}, rifts: [], doors: [], spawners: [],
           points: [], playerStarts: [], wiring: [], removed: [], waves: [], edits: [],
           added: [] }
  history.length = 0
  changes = savedChanges = 0
  selected = null
  picked = []
  wiringPick = null
  fxPick = null
  gizmo.detach()
  setMode('none')
  for (const g of ['sky', 'level', 'lights', 'vfx', 'outline', 'routes', 'wiring',
                   'coverage', 'added', 'volumes']) clear(groups[g])
  for (const g of ['newGeo', 'doorMesh', 'forcefield']) detach(groups[g])
  dropPlaneMeshes()
  trace.sync(null)
  skyMesh = null
  scratchUi.hide()
  levelGeo.clear()
  piecesByMesh.clear()
  instByMesh.clear()
  scene.background = new THREE.Color(0x0d1016)
  scene.fog = null
  mission = blankMission()
  missionMap = null
  el('modtitle').value = ''
  for (const id of ['wiring', 'readout', 'saved']) {
    const node = el(id)
    if (node) node.textContent = ''
  }
  el('layers').innerHTML = ''
  el('app').classList.remove('kit')
  el('level').value = ''
  el('load').disabled = true
  setEnabled(false)
  showProps()
  busy('Pick a level and open it.')
  loadCampaigns().then(applyDefaults).catch(err => report('defaults', err))
}

async function openLevel (name, fromScratch) {
  return whileLoading('Reading ' + name + '…', () => readLevel(name, fromScratch))
}

async function readLevel (name, fromScratch) {
  const gen = ++levelGen
  busy('reading ' + name)
  //: a level from scratch draws its host's sky and kept actors, and no geometry
  const got = await json((fromScratch ? 'api/scratch/level/' : 'api/level/') +
                         encodeURIComponent(name))
  if (gen !== levelGen) return
  level = got
  //: read ahead for the Details dialog, which opens on them
  sourcePicturesOf(name)
  if (level.scratch) scratchUi.show(level.scratch, world)
  else scratchUi.hide()
  // A clean slate. The plan file is how the packager receives the work, not a document
  // the tool reopens: a level that came back with yesterday's edits already in it read
  // as though the level itself had been changed.
  plan = { level: name, version: 2, look: {}, world: {}, rifts: [], doors: [], spawners: [],
           points: [], playerStarts: [], wiring: [], removed: [], waves: [], edits: [],
           //: the Crystal a built level places, as its plan item (crystal.adopt)
           added: level.crystal ? [structuredClone(level.crystal)] : [] }
  newMissionFor = name
  // the pickers start on what this level is painted with
  levelLook = ownLook(level)
  fillLook('sky')
  fillLook('liquid')
  nextId = 1 + planObjects().reduce((m, o) => Math.max(m, o.obj.id || 0), 0)
  history.length = 0
  changes = savedChanges = 0
  dropPlaneMeshes()
  loadMapInfo()
  selected = null
  picked = []
  fxPick = null
  gizmo.detach()
  applySky(level.sky)
  drawLights(level)
  drawVfx(level)
  //: the level's lights at their own brightness, for the lighting preview (syncLamps)
  lightRig = null
  json('api/lights/' + encodeURIComponent(name))
    .then((rig) => { if (gen === levelGen) { lightRig = rig; lampsAt = 0 } })
    .catch(() => {})
  buildLayers(level)
  resetCutaway()
  drawWiring(level)
  clear(groups.volumes)
  drawVolumes(groups.volumes, level)
  applyVolumes(groups.volumes, plan)
  // Coverage is read separately and must never stop a level opening: it parses the
  // navgraph buffers, which is the newest and least proven reader here.
  //: a scratch level's walkable graph is made from its floors when it is built
  ;(level.scratch ? Promise.reject(new Error('made from the floors when built'))
    : json('api/coverage/' + encodeURIComponent(name)))
    .then(drawCoverage)
    .catch(() => clear(groups.coverage))
  // Framed on the play area, not the true bounds: one stray Plane sits at z=976,002
  // in some levels, so the real bounds are 200x the level and it opens as a speck.
  frameOn(level.focus
    ? [[level.focus[0][0], level.focus[1][0], level.focus[2][0]],
       [level.focus[0][1], level.focus[1][1], level.focus[2][1]]]
    : level.bounds)
  drawPlan()
  showProps()
  setEnabled(true)
  await redrawAdded()
  await drawLevel(level)
  //: Last, after the geometry: the pickers keep their value across levels, so a level
  //: opened with a sky or a pool material already chosen should open wearing it.
  //: There are no pools to find before the slices land.
  if (plan.look.sky || plan.look.liquid) await previewLook()
}

//: Where a mission can be listed (customcampaign.choices): no campaign, a new or
//: installed custom campaign, or a shipped one. Fetched at boot and after a save.
let campaigns = []

const escapeText = (text) => String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/>/g, '&gt;').replace(/"/g, '&quot;')

//: The mission being built: what OrcsMustUI shows for it and where it is listed. The
//: name is on the page; the rest is in the Details dialog, the one Rift Lord's pages
//: share (web/shared/MissionDetails.svelte).
let mission = blankMission()
//: The open mission's own level (`<mod>_Map01`, bucket.save_map), or null before its
//: first save.
let missionMap = null
//: the Map Settings header follows the mission's name as it is typed
el('modtitle').addEventListener('input', () => {
  const head = document.querySelector('[data-mapsettings] b')
  const name = el('modtitle').value.trim()
  if (head && name && level && (editing || plan.scratch)) head.textContent = name
})

function blankMission () {
  return { author: '', version: '', description: '', placement: 'CustomMaps', endless: false,
           endlessOnly: false, resetDoors: false, guid: '', hub: '', scramble: scrambleValue(null),
           newCampaign: { name: '', description: '', banner: null, hub: '' } }
}

/** A saved `scramble` ({tiers, rows}, an older {tier, rows, extra}, or null;
 * missionscramble.clean) as the Details dialog edits it. */
function scrambleValue (saved) {
  return saved ? { on: true, ...saved, rows: { ...(saved.rows || {}) } }
    : { on: false, tiers: [], rows: {} }
}

/** The Details dialog's `scramble` as a plan and a save keep it, or null. */
function scrambleSaved (value) {
  if (!value || !value.on) return null
  const { on, ...kept } = value
  return kept
}

async function loadCampaigns () {
  try {
    //: each campaign's Campaign Index range for the open mission (bucket.choices_for)
    campaigns = await json('api/campaigns' + (editing ? '?mission=' + encodeURIComponent(editing) : ''))
  } catch (err) {
    campaigns = []
  }
  if (!campaigns.some(c => c.name === mission.placement)) mission.placement = 'CustomMaps'
}

/** The Details dialog, mounted over the page while it is open. */
let detailsDialog = null
//: level -> the promise of its own pictures (sourcepictures.py)
const sourcePictureCache = new Map()

/** A level's own loading screen and hub portal pictures, or null. */
function sourcePicturesOf (name) {
  if (!name) return Promise.resolve(null)
  if (!sourcePictureCache.has(name)) {
    sourcePictureCache.set(name, json('api/sourcepictures/' + encodeURIComponent(name))
      .catch(() => null))
  }
  return sourcePictureCache.get(name)
}

async function openDetails () {
  if (detailsDialog) return
  detailsDialog = 'opening'
  const source = await sourcePicturesOf(level && level.level)
  //: the live Scramble tiers (missionscramble.view)
  const tiers = await json('api/scramble').catch(() => null)
  const host = document.createElement('div')
  document.body.appendChild(host)
  detailsDialog = mount(MissionDetails, {
    target: host,
    props: {
      values: structuredClone(mission),
      campaigns,
      allowPicture: true,
      title: el('modtitle').value,
      sourcePictures: source,
      scrambleTiers: tiers,
      guidTools: guidTools(),
      //: a Map Editor mission may be an Endless Variant (bucket._endless_variant)
      allowEndlessVariant: true,
      //: an installed campaign's own settings, saved at once (bucket.update_campaign)
      campaignTools: {
        save: async (key, values) => {
          const got = await json('api/campaign', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Rift-Lord': '1' },
            body: JSON.stringify({ key, values }),
          })
          loadCampaigns()
          return got
        },
      },
      //: the Guid is kept by setGuid, never by the dialog's copy
      onchange: (v) => { mission = { ...v, guid: mission.guid } },
      onclose: () => {
        unmount(detailsDialog)
        host.remove()
        detailsDialog = null
        showProps()
      },
    },
  })
}

/** The mission fields as the plan keeps them (leveldata.MISSION_FIELDS). */
function missionFields () {
  return {
    title: el('modtitle').value,
    author: mission.author,
    version: mission.version,
    description: mission.description,
    placement: mission.placement,
    campaignName: mission.newCampaign.name,
    campaignDescription: mission.newCampaign.description,
    hub: mission.hub || '',
    campaignHub: mission.newCampaign.hub || '',
    endless: mission.endless ? 'true' : '',
    endlessOnly: mission.endlessOnly ? 'true' : '',
    resetDoors: mission.resetDoors ? 'true' : '',
    scramble: scrambleSaved(mission.scramble),
  }
}

/** Take a saved mission's fields back; blank ones keep what is there. */
function fillMission (m) {
  if (m.title) el('modtitle').value = m.title
  for (const k of ['author', 'version', 'description']) if (m[k]) mission[k] = m[k]
  if (m.campaignName) mission.newCampaign.name = m.campaignName
  if (m.campaignDescription) mission.newCampaign.description = m.campaignDescription
  if (m.hub) mission.hub = m.hub
  if (m.campaignHub) mission.newCampaign.hub = m.campaignHub
  // a campaign the mission once created is an installed one by now
  const made = m.placement === 'new' && campaigns.find(c => c.kind === 'custom' &&
    c.label === (m.campaignName || '').trim())
  if (made) mission.placement = made.name
  else if (m.placement && campaigns.some(c => c.name === m.placement)) mission.placement = m.placement
  if (m.endless !== undefined) mission.endless = m.endless === true || m.endless === 'true'
  if (m.endlessOnly !== undefined) mission.endlessOnly = m.endlessOnly === true || m.endlessOnly === 'true'
  if (m.resetDoors !== undefined) mission.resetDoors = m.resetDoors === true || m.resetDoors === 'true'
  if (m.scramble !== undefined) mission.scramble = scrambleValue(m.scramble)
}

/** Fill a fresh mission from Rift Lord's Settings (empty when run on its own). */
async function applyDefaults () {
  const d = await json('api/defaults')
  if (!d || !Object.keys(d).length) return
  if (d.author && !mission.author) mission.author = d.author
  if (d.version && !mission.version) mission.version = d.version
  if (d.placement && campaigns.some(c => c.name === d.placement)) mission.placement = d.placement
  mission.endless = !!d.endless
  if (d.hub !== undefined) mission.hub = d.hub
}

/** Save the mission into Rift Lord's Missions as its plan over the level, which Install
 * and Package build into the game's files. The plan file is saved first: the server
 * reads the file, not the page. */
async function buildMod () {
  if (!level) { flash('Open a level first.'); return }
  const m = missionFields()
  //: an existing mission, whose waves the save keeps unless its spawners changed
  const intoExisting = editing
  if (m.placement === 'new' && !m.campaignName.trim()) {
    flash('Name the new campaign under Details.')
    return
  }
  if (mission.guid && !(guidCheck && guidCheck.ok)) {
    flash('Fix the Guid under Details.')
    return
  }
  if (!await save()) return
  const button = el('build')
  button.disabled = true
  button.innerHTML = '<span class="spinner"></span> Saving…'
  el('saved').className = 'hint'
  el('saved').textContent = ''
  try {
    const got = await json('api/build/' + encodeURIComponent(level.level), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Rift-Lord': '1' },
      body: JSON.stringify({
        install: true,
        mission: editing,
        guid: mission.guid || undefined,
        title: m.title,
        placement: m.placement,
        details: {
          author: m.author, version: m.version, description: m.description,
          endless: !!m.endless,
          endlessOnly: !!m.endlessOnly,
          resetDoors: !!m.resetDoors,
          scramble: mission.scramble,
          campaignIndex: mission.campaignIndex ?? null,
          hub: m.hub,
          picture: sendable(mission.picture),
          portalPicture: sendable(mission.portalPicture),
          newCampaign: { name: mission.newCampaign.name,
                         description: mission.newCampaign.description,
                         banner: mission.newCampaign.banner,
                         hub: m.campaignHub },
        },
      }),
    })
    if (got.checks_failed && got.checks_failed.length) {
      el('saved').className = 'hint bad'
      el('saved').innerHTML = '<b>Not saved.</b>' +
        got.checks_failed.map(c => `<br>${escapeText(c)}`).join('')
      return
    }
    if (got.mission) editing = got.mission
    else if (got.written) editing = got.written.split(/[\\/]/).pop().replace(/\.pak$/, '')
    if (got.map) missionMap = got.map
    savedChanges = changes
    mission.guid = ''
    guidCheck = null
    //: a Campaign Index is applied by the save that sent it
    mission.campaignIndex = null
    loadCampaigns()
    loadMapInfo()
    el('saved').className = 'hint ok'
    el('saved').innerHTML = `<b>Saved to Missions.</b>` +
      (got.mission ? ' Install it from Missions to play it.' : '') +
      (/rebuilt/.test(got.coordinator || '') && intoExisting
        ? `<br>${escapeText(got.coordinator)}` : '') +
      (got.campaign ? ` Listed in ${escapeText(got.campaign)}.` : '') +
      (got.guidShared ? `<br>Its Guid is also used by ${escapeText(got.guidShared.join(', '))}.`
        : '') +
      (got.removed_waves || []).map(n => `<br>${escapeText(n)}.`).join('') +
      ((got.not_applied || []).length
        ? '<br>Not applied to the level yet:' +
          got.not_applied.map(n => `<br>&bull; ${escapeText(n)}`).join('')
        : '') + warningsHtml()
  } catch (err) {
    el('saved').className = 'hint bad'
    el('saved').textContent = 'Not saved: ' + err.message
  } finally {
    button.disabled = !level
    button.textContent = 'Save Mission'
  }
  // a save can create a campaign or change what one lists; a new one is now installed
  const madeNew = m.placement === 'new'
  await loadCampaigns()
  if (madeNew) {
    const c = campaigns.find(x => x.kind === 'custom' && x.label === m.campaignName.trim())
    if (c) mission.placement = c.name
  }
  refreshMissionList()
}

/** Save As: the open mission saved as a new mission in Missions under `title`, with its
 * own mod and Guid (riftlord/server.py /api/saveas): waves and all from the mission it
 * was opened from; the page then edits the new one. */
async function saveAsMission (title) {
  if (!level) return false
  const name = String(title || '').trim()
  if (!name) { flash('Enter a name.'); return false }
  const m = missionFields()
  if (m.placement === 'new' && !m.campaignName.trim()) {
    flash('Name the new campaign under Details.')
    return false
  }
  el('modtitle').value = name
  if (!await save()) return false
  try {
    const got = await json('api/saveas/' + encodeURIComponent(level.level), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Rift-Lord': '1' },
      body: JSON.stringify({
        mission: editing, title: name, placement: m.placement,
        details: {
          author: m.author, version: m.version, description: m.description,
          endless: !!m.endless, endlessOnly: !!m.endlessOnly, resetDoors: !!m.resetDoors,
          hub: m.hub,
          scramble: mission.scramble,
          campaignIndex: mission.campaignIndex ?? null,
          picture: sendable(mission.picture), portalPicture: sendable(mission.portalPicture),
          newCampaign: { name: mission.newCampaign.name,
                         description: mission.newCampaign.description,
                         banner: mission.newCampaign.banner, hub: m.campaignHub },
        },
      }),
    })
    editing = got.mission
    missionMap = got.map || null
    savedChanges = changes
    mission.guid = ''
    guidCheck = null
    //: a Campaign Index is applied by the save that sent it
    mission.campaignIndex = null
    loadCampaigns()
    loadMapInfo()
    el('saved').className = 'hint ok'
    el('saved').innerHTML = `<b>Saved as ${escapeText(got.title)}.</b>` +
      (got.campaign ? ` Listed in ${escapeText(got.campaign)}.` : '')
    await refreshMissionList()
    el('level').value = MISSION_PREFIX + got.mission
    return true
  } catch (err) {
    el('saved').className = 'hint bad'
    el('saved').textContent = 'Not saved: ' + err.message
    return false
  }
}

/** The Save As button and its name field, under Save Mission. */
function wireSaveAs () {
  const build = el('build')
  if (!build || el('saveas')) return
  const button = document.createElement('button')
  button.id = 'saveas'
  button.textContent = 'Save As\u2026'
  button.disabled = true
  const form = document.createElement('div')
  form.id = 'saveasform'
  form.hidden = true
  form.innerHTML = '<div class="field"><label>New Name</label>' +
    '<input id="saveastitle" type="text"></div>' +
    '<div class="grid2"><button id="saveasgo" class="go">Save</button>' +
    '<button id="saveascancel">Cancel</button></div>'
  build.after(button, form)
  button.addEventListener('click', () => {
    form.hidden = false
    const input = el('saveastitle')
    input.value = (el('modtitle').value || '') + ' copy'
    input.focus()
    input.select()
  })
  el('saveascancel').addEventListener('click', () => { form.hidden = true })
  const go = async () => {
    el('saveasgo').disabled = true
    try {
      if (await saveAsMission(el('saveastitle').value)) form.hidden = true
    } finally {
      el('saveasgo').disabled = false
    }
  }
  el('saveasgo').addEventListener('click', go)
  el('saveastitle').addEventListener('keydown', (ev) => { if (ev.key === 'Enter') go() })
}

//: The level's own sky and liquid materials, which the pickers start on.
let levelLook = { sky: null, liquid: null }
//: Every material a plan may repaint with (leveldata.looks).
let lookNames = { sky: [], liquid: [] }

/** What an open level is painted with: its sky's material, and the material most of
 * its liquid surfaces use. */
function ownLook (lvl) {
  const counts = new Map()
  for (const p of lvl.pieces || []) {
    if (p.liquid && p.material) counts.set(p.material, (counts.get(p.material) || 0) + 1)
  }
  const liquid = [...counts].sort((x, y) => y[1] - x[1])[0]
  return { sky: (lvl.sky && lvl.sky.material) || null, liquid: liquid ? liquid[0] : null }
}

// -- effects and painted materials ------------------------------------------------

//: An effect with no mesh of its own (an emitter) is drawn as a glowing ball this big,
//: in uu, coloured per effect.
const FX_MARKER_RADIUS = 60
const FX_MARKER_COLOUR = {
  LavaBurst: 0xff6a1a, LavaEmbers: 0xffa040, AshEmbers: 0xb0a090, AshHigh: 0x8a8070,
  DoorSmoke: 0x707070,
}
//: The marker colour of a surveyed effect (riftlord/fxsurvey.py), by its group.
const FX_GROUP_COLOUR = {
  Weather: 0x8fd0ff, Water: 0x3f8fe0, Acid: 0x8be04f, 'Fog and dust': 0xc8c8c8,
  'Lava and ash': 0xff8040, 'Fire and smoke': 0xffb030, Magic: 0xb070ff, Nature: 0x70d070,
}
//: The colour of an effect in no surveyed group.
const FX_DEFAULT_COLOUR = 0xff8040
//: How a painted piece is tinted in the viewport: lava rock, and any other material.
const PAINT_TINT = { lava: new THREE.Color(0.55, 0.22, 0.16), other: new THREE.Color(0.7, 0.75, 1.0) }
const NO_TINT = new THREE.Color(1, 1, 1)
let materialNames = null

//: the Mission Events piece's plan kind and name (riftlord/missionevents.py)
const EVENTS_KIND = 'events'
const EVENTS_LABEL = 'Mission Events'
const EVENTS_COLOUR = 0x40c0ff

/** The marker an emitter effect, or the Mission Events piece, is drawn as. */
function fxMarker (entry) {
  const row = entry.kind === 'fx' && catalogue.find(r => r.kind === 'fx' && r.name === entry.fx)
  if (entry.kind === 'fx') {
    //: an emitter draws as an effect cloud of its group (effectStandIn)
    const group = row ? row.group : guessFxGroup(entry.fx)
    return effectStandIn(group, FX_MARKER_COLOUR[entry.fx] || FX_GROUP_COLOUR[group] ||
      FX_DEFAULT_COLOUR)
  }
  const geo = new THREE.SphereGeometry(FX_MARKER_RADIUS, 12, 8)
  const colour = entry.kind === EVENTS_KIND ? EVENTS_COLOUR : FX_DEFAULT_COLOUR
  const mat = new THREE.MeshBasicMaterial({ color: colour, transparent: true, opacity: 0.8 })
  return new THREE.Mesh(geo, mat)
}

//: The part key of an added mesh's own component in its `paints` (riftlord/lavafx.py
//: MESH_PART).
const MESH_PART = ''
//: A single piece shows one Material field per part and slot up to this many; more, and
//: one field paints them all.
const MAX_PAINT_ROWS = 24
//: mesh name -> how many material slots it has, as its geometry says
const meshSlots = new Map()
//: mesh name -> the material each of its slots wears as shipped, by asset name ('' unknown)
const meshSlotMats = new Map()

/** Remember a served mesh's slot count and its slots' own materials. */
function noteSlots (name, m) {
  meshSlots.set(name, slotCount(m))
  meshSlotMats.set(name, (m.slots && m.slots.length ? m.slots : [m.material]).map(s =>
    String(s || '').split('/').pop().split('.')[0]))
}

/** Whether a `paints` value (a material, or {slot: material}) names a lava rock. */
const paintIsLava = (value) => (typeof value === 'object' && value
  ? Object.values(value) : [value]).some(m => /_Lava/.test(String(m || '')))

//: A placed actor with nothing to draw (a light, a sound) is a marker this big, in uu.
const ACTOR_MARK_RADIUS = 70
//: Its colour by New Object group, when it brings no light of its own.
const ACTOR_MARK_COLOUR = { Sound: 0x7ad4ff, Lights: 0xffd27a }
const ACTOR_MARK_DEFAULT = 0xc0c0c0

/** The marker of a placed actor with nothing to draw: a bulb in its light's colour, or
 * a diamond for a sound. */
function actorStandIn (cls) {
  const row = catalogue.find(r => r.name === cls) || {}
  const light = (row.lights || [])[0]
  const colour = light
    ? new THREE.Color().setRGB(light.colour[0], light.colour[1], light.colour[2],
                               THREE.LinearSRGBColorSpace)
    : new THREE.Color(ACTOR_MARK_COLOUR[row.group] || ACTOR_MARK_DEFAULT)
  const geo = row.group === 'Sound' ? new THREE.OctahedronGeometry(ACTOR_MARK_RADIUS)
    : new THREE.SphereGeometry(ACTOR_MARK_RADIUS, 12, 8)
  return new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: colour, fog: false }))
}

/** Tint the level's pieces the plan paints (`look.paints`), so a paint shows before a build. */
function applyPaintTint () {
  const paints = (plan.look && plan.look.paints) || {}
  for (const [name, inst] of instByMesh) {
    const list = piecesByMesh.get(name) || []
    let any = false
    list.forEach((piece, i) => {
      const m = paints[String(piece.id)]
      if (!m && !inst.instanceColor) return
      inst.setColorAt(i, m ? (paintIsLava(m) ? PAINT_TINT.lava : PAINT_TINT.other) : NO_TINT)
      any = true
    })
    if (any && inst.instanceColor) inst.instanceColor.needsUpdate = true
  }
}

/** Tint one drawn part of an added entry when its `paints` names a material for it. */
function tintAddedPart (mesh, entry, part) {
  const value = ((entry && entry.paints) || {})[part]
  if (!value || Array.isArray(mesh.material)) return
  const mat = mesh.material.clone()
  if (mat.color) mat.color.copy(paintIsLava(value) ? PAINT_TINT.lava : PAINT_TINT.other)
  const waits = textureWaits.get(mesh.material)
  if (waits) waits.push(mat)
  mesh.material = mat
}

/** The material slots of a mesh (1 until its geometry has said otherwise). */
const slotsOf = (mesh) => Math.max(1, meshSlots.get(mesh) || 1)

/** What one picked record can be painted on: [{owner (a level piece or an added entry),
 * part, mesh, slots}]; [] for anything that takes no material. */
function paintTargets (p) {
  if (!p || p.marker || p.levelActor) return []
  if (p.added) {
    const e = p.added
    if (e.kind === 'mesh') return [{ owner: e, part: MESH_PART, mesh: e.name, slots: slotsOf(e.name) }]
    if (e.kind !== 'kit' || isTeleporter(e)) return []
    return (partsCache.get(e.name) || []).filter(pt => pt.component && pt.mesh && !pt.skeletal && !pt.particle)
      .map(pt => ({ owner: e, part: pt.component, mesh: pt.mesh, slots: slotsOf(pt.mesh) }))
  }
  if (p.liquid || p.skeletal) return []
  //: a component piece is its own export; a Blueprint part needs one in the level
  if (p.source === 'component' || (p.source === 'blueprint' && p.component)) {
    return [{ owner: p, part: null, mesh: p.mesh, slots: slotsOf(p.mesh) }]
  }
  return []
}

/** The material one target's slot wears without a paint: the level's own override on a
 * level piece's first slot, else the mesh's slot material; '' when unknown. */
function ownPaint (t, slot) {
  if (t.part === null && slot === 0 && t.owner.material) return t.owner.material
  return (meshSlotMats.get(t.mesh) || [])[slot] || ''
}

/** The material one target's slot wears now: the plan's paint, else its own. */
function paintOf (t, slot) {
  const value = t.part === null
    ? ((plan.look && plan.look.paints) || {})[String(t.owner.id)]
    : ((t.owner.paints || {})[t.part])
  const painted = !value ? '' : typeof value === 'string' ? (slot === 0 ? value : '')
    : value[String(slot)] || ''
  return painted || ownPaint(t, slot)
}

//: the Material options' HTML for each list of names, built once per list
let paintOptions = { names: null, html: '' }

/** A Material select: every material, `now` selected (listed first when the list lacks
 * it); `now` '' shows Mixed. */
function paintSelect (key, now) {
  const names = materialNames || []
  if (paintOptions.names !== names) {
    paintOptions = { names, html: names.map(n => `<option value="${escapeText(n)}">` +
      `${escapeText(n)}</option>`).join('') }
  }
  if (!now) {
    return `<select data-paint="${key}"><option value="" selected disabled>Mixed</option>` +
      `${paintOptions.html}</select>`
  }
  const own = `<option value="${escapeText(now)}">`
  const html = names.includes(now)
    ? paintOptions.html.replace(own, `<option value="${escapeText(now)}" selected>`)
    : `<option value="${escapeText(now)}" selected>${escapeText(now)}</option>${paintOptions.html}`
  return `<select data-paint="${key}">${html}</select>`
}

/** Paint one target's slot (`slot` null: every slot) with `material` ('' clears it).
 * A one-slot mesh stores a plain material, a several-slot one {slot: material}. */
function setTargetPaint (t, slot, material) {
  const read = () => t.part === null
    ? ((plan.look && plan.look.paints) || {})[String(t.owner.id)]
    : (t.owner.paints || {})[t.part]
  let value = read()
  if (t.slots <= 1) value = material || null
  else {
    const slots = typeof value === 'object' && value ? { ...value }
      : (typeof value === 'string' && value ? { 0: value } : {})
    for (const k of slot === null ? [...Array(t.slots).keys()] : [slot]) {
      if (material) slots[String(k)] = material
      else delete slots[String(k)]
    }
    value = Object.keys(slots).length ? slots : null
  }
  //: the piece's own material is no paint at all
  if (t.part === null && typeof value === 'string' && value === t.owner.material) value = null
  if (t.part === null) {
    plan.look = plan.look || {}
    const paints = Object.assign({}, plan.look.paints || {})
    if (value) paints[String(t.owner.id)] = value
    else delete paints[String(t.owner.id)]
    plan.look.paints = paints
  } else {
    const paints = Object.assign({}, t.owner.paints || {})
    if (value) paints[t.part] = value
    else delete paints[t.part]
    if (Object.keys(paints).length) t.owner.paints = paints
    else delete t.owner.paints
  }
}

/** The Material section for the selection: one field per part and slot of a single
 * piece, or one field for everything when several are picked; '' unless every picked
 * thing takes a material. */
function paintSection () {
  if (!picked.length) return ''
  const per = picked.map(paintTargets)
  if (per.some(t => !t.length)) return ''
  if (!materialNames) {
    json('api/materials').then((got) => { materialNames = got; if (picked.length) showProps() })
      .catch(() => { materialNames = [] })
  }
  //: an added kit piece's parts arrive with its class
  for (const p of picked) {
    if (p.added && p.added.kind === 'kit' && !partsCache.has(p.added.name)) {
      classParts(p.added.name).then(() => { if (picked.length) showProps() })
    }
  }
  const targets = per.flat()
  const rows = paintRows(targets)
  const single = pickUnits().length === 1
  if (single && rows.length <= MAX_PAINT_ROWS) {
    return '<h2>Material</h2>' + rows.map(([t, k], i) => (rows.length > 1
      ? `<label class="pick"><span>${escapeText(t.mesh)}${t.slots > 1 ? ` &middot; slot ${k}` : ''}` +
        '</span>' : '') + paintSelect(i, paintOf(t, k)) +
      (rows.length > 1 ? '</label>' : '')).join('')
  }
  const values = new Set(rows.map(([t, k]) => paintOf(t, k)))
  const same = values.size === 1 ? [...values][0] : ''
  return '<h2>Material</h2>' + paintSelect('all', same)
}

/** [[target, slot]] for every slot of every target. */
function paintRows (targets) {
  const rows = []
  for (const t of targets) for (let k = 0; k < t.slots; k += 1) rows.push([t, k])
  return rows
}

/** Wire paintSection's fields. */
function wirePaint (body) {
  const fields = body.querySelectorAll('[data-paint]')
  if (!fields.length) return
  const targets = picked.flatMap(paintTargets)
  const rows = paintRows(targets)
  //: a slot given its own material stores no paint
  const paint = (t, k, v) => setTargetPaint(t, k, v === ownPaint(t, k) ? '' : v)
  for (const input of fields) {
    input.addEventListener('change', async () => {
      const v = input.value
      if (!v) return
      snapshot()
      if (input.dataset.paint === 'all') {
        for (const t of targets) for (let k = 0; k < t.slots; k += 1) paint(t, k, v)
      } else {
        const [t, k] = rows[+input.dataset.paint]
        paint(t, k, v)
      }
      applyPaintTint()
      if (targets.some(t => t.part !== null)) await redrawAdded()
      refreshPicked()
      showProps()
    })
  }
}

/** The Effect picker for one picked effect, or ''. */
function fxSection (piece) {
  if (!piece || !piece.added || piece.added.kind !== 'fx') return ''
  const rows = catalogue.filter(r => r.kind === 'fx')
  const groups = [...new Set(rows.map(r => r.group || 'Other'))]
  const now = rows.find(r => r.name === piece.added.fx)
  return '<h2>Effect</h2><select id="fxkind">' + groups.map(g =>
    `<optgroup label="${escapeText(g)}">` + rows.filter(r => (r.group || 'Other') === g).map(r =>
      `<option value="${r.name}"${r.name === piece.added.fx ? ' selected' : ''}>` +
      `${escapeText(r.label || r.name)}</option>`).join('') + '</optgroup>').join('') +
    '</select>' + (now && (now.levels || []).length
    ? `<div class="hint">As placed on ${escapeText(now.levels.slice(0, 3).map(levelTitle).join(', '))}.</div>`
    : '')
}

/** A level's title for a hint, or its name. */
function levelTitle (name) {
  const own = allLevels.find(l => l.name === name)
  return (own && own.title) || name
}

//: Whether the right sidebar shows the Mission Events list (the Mission Events button).
let eventsOpen = false
//: /api/events for the open level and the plan's unstable rifts: {key, got}, got null
//: while it is asked
let eventChoices = null

/** The open level's Mission Events choices (missionevents.editor_choices), or null while
 * they are read; the panel is redrawn when they arrive. */
function eventsChoices () {
  if (!level) return null
  const rifts = (plan.spawners || []).filter(sp => sp.unstable && sp.index)
    .map(sp => sp.index).sort((a, b) => a - b).join(',')
  const key = level.level + '|' + rifts
  if (!eventChoices || eventChoices.key !== key) {
    eventChoices = { key, got: null }
    json('api/events/' + encodeURIComponent(level.level) + '?rifts=' + rifts)
      .then((got) => {
        if (!eventChoices || eventChoices.key !== key) return
        eventChoices.got = got
        if (eventsOpen || pickedEventsPiece()) showProps()
      })
      .catch((err) => { if (eventChoices && eventChoices.key === key) flash('Mission events: ' + err.message) })
  }
  return eventChoices.got
}

/** The spawners a boss may come through: the level's own and the plan's, by index. */
function eventSpawners () {
  const out = []
  for (const sp of (level && level.spawners) || []) {
    if (!sp.index || isGone(plan, sp.export)) continue
    out.push({ index: sp.index, label: `Spawner ${sp.index} (${sp.export})` })
  }
  for (const sp of plan.spawners || []) {
    if (sp.index && !sp.unstable) out.push({ index: sp.index, label: `Spawner ${sp.index}` })
  }
  return out.sort((a, b) => a.index - b.index)
}

/** Whether the picked thing is the Mission Events piece. */
const pickedEventsPiece = () => picked.length === 1 && picked[0].added &&
  picked[0].added.kind === EVENTS_KIND

/** The Mission Events list for the piece's panel, or ''. */
function eventsSection (piece) {
  if (!piece || !piece.added || piece.added.kind !== EVENTS_KIND) return ''
  return eventsHtml(plan.events || [], eventsChoices(), eventSpawners())
}

/** Wire the Mission Events list wherever it is drawn. */
function wireEventsList (body) {
  const choices = eventChoices && eventChoices.got
  if (!choices || !body.querySelector('[data-evadd]')) return
  wireEvents(body, {
    get: () => plan.events || [],
    set: (list) => {
      snapshot()
      plan.events = list
      //: the piece is placed (once per level) as soon as an event needs it
      if (needsPiece(list, choices)) placeEventsPiece()
      showProps()
    },
    choices,
    spawners: eventSpawners(),
  })
}

/** The right sidebar as the Mission Events list. */
function showEventsPanel () {
  const body = el('propsbody')
  el('app').classList.add('props')
  el('propshead').textContent = EVENTS_LABEL
  body.innerHTML = eventsHtml(plan.events || [], eventsChoices(), eventSpawners())
  wireEventsList(body)
}

/** The Mission Events button: the list of the mission's events in the right sidebar. */
function openMissionEvents () {
  if (!level) return
  picked = []
  selected = null
  wiringPick = null
  gizmo.detach()
  refreshPicked()
  eventsOpen = true
  showProps()
}

/** Place the Mission Events piece near the view, unless the level has it; one per level. */
function placeEventsPiece () {
  if ((plan.added || []).some(a => a.kind === EVENTS_KIND)) return
  const at = viewDropPoint()
  const entry = { name: EVENTS_LABEL, kind: EVENTS_KIND,
                  matrix: new THREE.Matrix4().makeTranslation(at[0], at[1], at[2]).toArray() }
  plan.added.push(entry)
  drawAdded(entry)
  flash(`${EVENTS_LABEL} placed: this level needs it for these events.`)
}

//: New Object places a piece this far in front of the camera, in uu (a few trap cells,
//: so it lands in view rather than at the far end of the orbit).
const PLACE_DISTANCE = 5 * TRAP_CELL
//: A placed piece stops this far short of a wall the camera looks at, in uu.
const PLACE_CLEARANCE = TRAP_CELL / 2
const placeRay = new THREE.Raycaster()

/** Where New Object puts a piece: PLACE_DISTANCE in front of the camera (or short of a
 * surface nearer than that), dropped onto the surface below; Unreal [x, y, z]. */
function viewDropPoint () {
  world.updateMatrixWorld(true)
  const targets = levelPickables().concat(groups.newGeo.children)
  const ahead = new THREE.Vector3()
  camera.getWorldDirection(ahead)
  placeRay.set(camera.position, ahead)
  placeRay.far = PLACE_DISTANCE
  const wall = placeRay.intersectObjects(targets, false).filter(h => !cutHides(h))[0]
  const spot = wall
    ? wall.point.clone().addScaledVector(ahead, -Math.min(PLACE_CLEARANCE, wall.distance / 2))
    : camera.position.clone().addScaledVector(ahead, PLACE_DISTANCE)
  const down = new THREE.Vector3(0, 0, -1).transformDirection(world.matrixWorld)
  placeRay.set(spot, down)
  placeRay.far = DROP_RAY_FAR
  const floor = placeRay.intersectObjects(targets, false).filter(h => !cutHides(h))[0]
  const at = world.worldToLocal((floor ? floor.point : spot).clone())
  return [Math.round(at.x), Math.round(at.y), Math.round(at.z)]
}

/** Show the level's own material, or the plan's repaint of it, in a picker; with
 * "None Selected" when the level has none. */
function fillLook (kind) {
  const node = el(kind === 'sky' ? 'looksky' : 'lookliquid')
  if (!node) return
  const own = levelLook[kind]
  const names = own && !lookNames[kind].includes(own) ? [own, ...lookNames[kind]] : lookNames[kind]
  node.innerHTML = (own ? '' : '<option value="">None Selected</option>') +
    names.map(n => `<option value="${escapeText(n)}">${escapeText(n)}</option>`).join('')
  node.value = (plan.look && plan.look[kind]) || own || ''
}

/** Put the level's own wiring where the plan says it is: moved, or taken out.
 *
 * `drawWiring` draws the level as shipped and never consulted the plan, so a spawner
 * the plan had removed came back visible on any redraw -- invisible until now because
 * nothing redrew the wiring mid-session. Resuming a saved plan does.
 */
function applyWiring () {
  const by = new Map((plan.wiring || []).map(w => [w.name, w]))
  for (const m of groups.wiring.children) {
    const d = m.userData || {}
    if (!d.wiring) continue
    const w = by.get(d.name)
    m.visible = !(w && w.gone)
    const at = (w && w.at) || (d.data && (d.data.location || d.data.loc))
    if (at) m.position.set(at[0], at[1], at[2])
    const yaw = w && typeof w.rotation === 'number' ? w.rotation : d.yaw
    if (typeof yaw === 'number') m.rotation.z = THREE.MathUtils.degToRad(yaw)
  }
  applyVolumes(groups.volumes, plan)
  if ([...goneDoorExports()].sort().join(',') !== hiddenDoorsKey) applyRemoved()
}

/** Load the plan saved for this level and show it. Explicit, by a button.
 *
 * Opening a level always starts clean, by design -- a level that reopened wearing
 * yesterday's edits read as though the shipped level had been changed. That makes an
 * explicit resume the other half of being able to save at all.
 */
async function resumePlan (given) {
  if (!level) { flash('Open a level first.'); return }
  let got = given
  if (!got) {
    try {
      got = await json('api/plan/' + encodeURIComponent(level.level))
    } catch (err) {
      flash('could not read the saved plan: ' + err.message)
      return
    }
  }
  const KEYS = ['rifts', 'doors', 'spawners', 'points', 'playerStarts', 'wiring',
                'removed', 'waves', 'edits', 'added']
  got = migratePlan(JSON.parse(JSON.stringify(got)))
  const n = KEYS.reduce((a, k) => a + ((got[k] || []).length), 0)
  const look = got.look || {}
  const world = got.world || {}
  if (!n && !look.sky && !look.liquid && !Object.keys(look.planes || {}).length &&
      !Object.keys(world).length) {
    flash('Nothing saved for ' + level.level + ' yet.')
    return
  }
  snapshot()
  plan = got
  plan.level = level.level
  for (const k of KEYS) plan[k] = plan[k] || []
  plan.look = look
  plan.world = world
  newMissionFor = null
  fillMission(plan.mission || {})
  nextId = 1 + planObjects().reduce((m, o) => Math.max(m, o.obj.id || 0), 0)
  giveRiftsExits()
  selected = null
  picked = []
  wiringPick = null
  gizmo.detach()
  //: the pickers show what the PLAN carries, not what was last chosen in this session
  fillLook('sky')
  fillLook('liquid')
  drawPlan()
  applyEdits()
  applyWiring()
  await redrawAdded()
  await drawDoorMeshes()
  refreshPicked()
  showProps()
  if (look.sky || look.liquid) await previewLook()
  await previewPlanes()
}

async function save () {
  try {
    const got = await json('api/plan/' + encodeURIComponent(level.level), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Rift-Lord': '1' },
      body: JSON.stringify({ ...plan, mission: missionFields() }),
    })
    saveWarnings = got.warnings || []
    el('saved').innerHTML =
      `${got.rifts} rift &middot; ${got.doors} door &middot; ` +
      `${got.spawners} spawner &middot; ${got.points} point &middot; ` +
      `${got.added} added &middot; ${got.edits} moved &middot; ` +
      `${got.removed} removed` + warningsHtml() + scratchUi.neededHtml(got.needed)
    return true
  } catch (err) {
    saveWarnings = []
    el('saved').className = 'hint bad'
    el('saved').textContent = 'Not saved: ' + err.message
    return false
  }
}

//: What the last plan save warned about (planactors.check errors, then warnings).
let saveWarnings = []

/** The last save's warnings as lines for the notice under Save Mission. */
function warningsHtml () {
  return saveWarnings.length
    ? '<br><span style="color:#ffb03a">Check before playing:</span>' +
      saveWarnings.map(w => `<br>&bull; ${escapeText(w)}`).join('')
    : ''
}

/** A checkbox per drawable layer, and one per streaming sublevel the level has. */
function buildLayers (data) {
  shownSubs.clear()
  for (const tag of data.sublevels || []) shownSubs.add(tag)
  const rows = LAYERS.map(([key, label]) =>
    `<label class="row"><span>${label}</span>` +
    `<input type="checkbox" data-layer="${key}"` +
    `${HIDDEN_ON_OPEN.includes(key) ? '' : ' checked'}></label>`)
  if ((data.sublevels || []).length) {
    rows.push('<div class="hint" style="margin-top:6px">streaming sublevels</div>')
  }
  for (const tag of data.sublevels || []) {
    rows.push(`<label class="row"><span>${tag}</span>` +
      `<input type="checkbox" data-sub="${tag}" checked></label>`)
  }
  el('layers').innerHTML = rows.join('')
  for (const key of HIDDEN_ON_OPEN) groups[key].visible = false
  syncAddedEffects()
}

/** Turn the Effects layer on (placing an effect shows it). */
function showEffects () {
  const box = el('layers').querySelector('input[data-layer="vfx"]')
  if (box) box.checked = true
  groups.vfx.visible = true
  syncAddedEffects()
}

/** Redraw the level with only the chosen sublevels in it. */
async function applySubs () {
  if (!level) return
  const wanted = new Set(shownSubs)
  await drawLevel({ ...level,
    pieces: level.pieces.filter(p => wanted.has(p.sub || 'persistent')) })
  picked = []
  refreshPicked()
  await previewPlanes()
}

//: Everything that needs a level open before it means anything.
const NEEDS_LEVEL = ['openkit', 'missionevents', 'build', 'saveas', 'modtitle', 'details', 'flyspeed', 'cutaway',
                     'closelevel',
                     'looksky', 'lookliquid']

/** Turn the tool on or off, so nothing is clickable before a level is there. */
function setEnabled (on) {
  for (const id of NEEDS_LEVEL) {
    const node = el(id)
    if (node) node.disabled = !on
  }
  for (const box of el('layers').querySelectorAll('input')) box.disabled = !on
  // the level's assets, its details and the mission only appear with a level open
  el('levelonly').hidden = !on
  el('closelevel').hidden = !on
  el('openclose').classList.toggle('solo', !on)
}

function setMode (next) {
  mode = next
  for (const b of document.querySelectorAll('[data-mode]')) {
    b.classList.toggle('on', b.dataset.mode === next)
  }
}

/** Turn the neutral rig down when the level's own lights are showing. */
/** With the lights layer on, the level is lit as it is in game as far as the view can:
 * its own sun and sky light, the sun's shadows, and the level's lights nearest the view
 * as real lights (syncLamps). Off, the same sun and sky without shadows or lamps. */
function useLevelLighting (on) {
  fill.intensity = levelRig.fill
  sun.intensity = levelRig.sun
  sun.castShadow = on
  lampsAt = 0
}

/** Keep the sun's shadow square on where the camera looks. */
function followSun () {
  if (!sun.castShadow) return
  sun.target.position.copy(controls.target)
  sun.position.copy(controls.target).addScaledVector(sunFrom, SUN_DISTANCE)
  sun.target.updateMatrixWorld()
}

function resize () {
  const r = el('view').getBoundingClientRect()
  renderer.setSize(r.width, r.height, false)
  camera.aspect = r.width / Math.max(r.height, 1)
  camera.updateProjectionMatrix()
}
addEventListener('resize', resize)

// The viewport also changes size when a side panel opens or closes, which is not a
// window resize, so the canvas is re-measured from the element itself.
new ResizeObserver(() => resize()).observe(el('view'))

let lastFrame = performance.now()

//: Blueprint classes whose parts have been asked for
const partsAsked = new Set()

/** Ask for the parts and trap grids of every picked kit piece, so flipping, resizing by
 * the grid and the Material section know them. */
function askPickedParts () {
  for (const p of picked) {
    const cls = p.added ? (p.added.kind === 'kit' ? p.added.name : null)
      : (p.source === 'blueprint' ? p.actor : null)
    if (!cls) continue
    kitGrids(cls, json)
    if (!partsCache.has(cls) && !partsAsked.has(cls)) {
      partsAsked.add(cls)
      classParts(cls).then(() => { if (picked.length) showProps() })
    }
  }
}

//: the trap grids layer (trapgrids.js), re-read every TRAP_GRID_SYNC milliseconds
const trapGridLayer = new TrapGridLayer(groups.trapGrids)
let trapGridAt = 0

/** The kit pieces whose grids the layer draws: each level Blueprint actor once, at the
 * matrix its drawn parts imply, and every added kit piece. */
function trapGridItems () {
  const out = []
  if (!level) return out
  const gone = new Set(plan.removed || [])
  const chosen = new Set()
  for (const p of picked) {
    if (p.added) chosen.add(p.added)
    else if (p.export !== undefined) chosen.add((p.sub || 'persistent') + '|' + p.export)
  }
  const byActor = new Map()
  for (const p of level.pieces || []) {
    if (p.source !== 'blueprint' || p.export === undefined || !p.actor || gone.has(p.id)) continue
    if (!shownSubs.has(p.sub || 'persistent') && (p.sub || 'persistent') !== 'persistent') continue
    const key = (p.sub || 'persistent') + '|' + p.export
    if (!byActor.has(key)) byActor.set(key, [])
    byActor.get(key).push(p)
  }
  for (const [key, kin] of byActor) {
    const cls = kin[0].actor
    const grids = cachedGrids(cls)
    if (!grids) { kitGrids(cls, json); continue }
    if (!grids.length) continue
    if (!partsCache.has(cls)) {
      if (!partsAsked.has(cls)) { partsAsked.add(cls); classParts(cls) }
      continue
    }
    const parts = partsCache.get(cls)
    const once = (mesh) => parts.filter(pt => pt.mesh === mesh).length === 1
    const p = kin.find(q => once(q.mesh)) || kin[0]
    const part = parts.find(pt => pt.mesh === p.mesh)
    if (!part) continue
    const actor = new THREE.Matrix4().fromArray(p.matrix).multiply(partLocal(part).invert())
    out.push({ key, matrix: actor.toArray(), grids, selected: chosen.has(key) })
  }
  ;(plan.added || []).forEach((e, i) => {
    if (e.kind !== 'kit') return
    const grids = cachedGrids(e.name)
    if (!grids) { kitGrids(e.name, json); return }
    if (grids.length) {
      out.push({ key: 'added|' + i, matrix: e.matrix, grids, selected: chosen.has(e) })
    }
  })
  //: Fab pieces' chosen suggested grids and the grids laid across runs (fabui.js)
  out.push(...fab.layerItems(plan, chosen, json))
  return out
}

function syncTrapGrids () {
  if (!groups.trapGrids.visible) return
  const now = performance.now()
  if (now - trapGridAt < TRAP_GRID_SYNC) return
  trapGridAt = now
  trapGridLayer.sync(trapGridItems())
}

//: The plan's world keys the view's height fog follows; the colour goes through
//: previewSkyFog with the sky's.
const FOG_VIEW_KEYS = ['fogDensity', 'fogFalloff', 'fogOpacity', 'fogStart']

/** The fog values a plan's `world` sets, for setHeightFog. */
function fogOverrides (w) {
  const out = {}
  for (const k of FOG_VIEW_KEYS) if (w && typeof w[k] === 'number') out[k] = w[k]
  return out
}

//: openings an ogre fits through on placed Fab pieces (fabui.js), re-read like the grids
const fabOpenings = new fab.OpeningMarkers(world)
let fabOpeningsAt = 0

function syncFabOpenings () {
  const now = performance.now()
  if (now - fabOpeningsAt < TRAP_GRID_SYNC) return
  fabOpeningsAt = now
  fabOpenings.sync(plan, json)
}

function tick () {
  syncTrapGrids()
  syncFabOpenings()
  syncLamps()
  followSun()
  coverCut()
  //: The sky is centred on the viewer, so it never clips and never moves relative
  //: to the horizon however far the camera flies.
  if (skyMesh) skyMesh.position.copy(camera.position)
  const now = performance.now()
  fly(Math.min((now - lastFrame) / 1000, 0.1))
  effectClock.value += Math.min((now - lastFrame) / 1000, 0.1)
  lastFrame = now
  controls.update()
  //: the level's height fog with the plan's changes over it (riftlord/levelfx.py keys)
  setHeightFog({ ...((mapInfo && mapInfo.scene) || {}), ...fogOverrides(plan.world) })
  updateHeightFog(world, groups.fog.visible && !!scene.fog && !!mapInfo)
  renderer.render(scene, camera)
  requestAnimationFrame(tick)
}

async function boot () {
  resize()
  tick()
  levelPicker(el('level'), el('openclose'))
  await whileLoading('Filling the level list…', async () => {
    allLevels = await json('api/levels')
    await refreshMissionList()
  })
  //: the New Object library, read ahead: effects take their group's look from it and
  //: placed lights their brightness
  json('api/catalogue').then((got) => {
    if (catalogue.length) return
    catalogue = got
    if (level) drawVfx(level)
  }).catch(() => {})
  //: The sky and pool materials a plan may repaint with. Loaded once; the lists are
  //: 18 and 84 entries, read from the asset index rather than from any level.
  try {
    lookNames = await json('api/looks')
    const fill = (id, kind) => {
      const node = el(id)
      if (!node) return
      node.addEventListener('change', () => {
        plan.look = plan.look || {}
        // the level's own material is no repaint at all
        plan.look[kind] = node.value && node.value !== levelLook[kind] ? node.value : null
        //: Repaint in place. Reopening the level would be the easy way and would
        //: also THROW THE PLAN AWAY -- `openLevel` resets it by design -- so a
        //: material preview must never go through it.
        previewLook().catch(err => report('look', err))
      })
    }
    fill('looksky', 'sky')
    fill('lookliquid', 'liquid')
  } catch (err) {
    // a missing look list is not worth refusing to start over
  }
  units = await json('api/units')
  schema = await json('api/schema')
  //: Worked out once per door class, because the size handle needs it the instant a
  //: door is selected and `writeBackSelected` is synchronous.
  for (const cls of schema.doors || []) {
    classParts(cls).then(parts => doorAxes.set(cls, doorAxisMap(parts)))
      .catch(() => doorAxes.set(cls, [0, 1, 2]))
  }
  //: Primed so `classMesh` can name a placed object the moment it is selected.
  for (const cls of Object.values(CLASS_OF)) classParts(cls).catch(() => {})
  el('load').addEventListener('click', () => {
    const name = el('level').value
    if (!name) { flash('Choose a level first.'); return }
    // a level, not a mission, starts a new one
    editing = null
    if (name.startsWith(MISSION_PREFIX)) {
      openBucketItem(name.slice(MISSION_PREFIX.length)).catch(err => report('open', err))
    } else {
      openLevel(name).catch(err => report('open', err))
    }
  })
  el('layers').addEventListener('change', (ev) => {
    const box = ev.target
    if (box.dataset.layer) {
      groups[box.dataset.layer].visible = box.checked
      if (box.dataset.layer === 'vfx') syncAddedEffects()
      // With the level's own lights on, the neutral rig is turned down so they are
      // what is actually lighting the room rather than a wash over the top of them.
      if (box.dataset.layer === 'lights') useLevelLighting(box.checked)
      if (box.dataset.layer === 'trapGrids' && picked.length) showProps()
      return
    }
    if (box.dataset.sub) {
      if (box.checked) shownSubs.add(box.dataset.sub)
      else shownSubs.delete(box.dataset.sub)
      applySubs()
    }
  })
  el('flyspeed').addEventListener('input', (ev) => {
    flySpeed = +ev.target.value / 100
    el('flylabel').textContent = flySpeed.toFixed(2)
  })
  //: the slider spans what is drawn when it is taken hold of
  for (const type of ['pointerdown', 'focus']) {
    el('cutaway').addEventListener(type, refreshCutRange)
  }
  el('cutaway').addEventListener('input', (ev) => {
    setCutaway(+ev.target.value)
    cutCoverAt = 0
  })
  for (const g of [groups.sky, groups.outline, edgeCue]) UNCUT.add(g)
  el('openkit').addEventListener('click', async () => {
    el('app').classList.add('kit')
    const features = readModFeatures()
    if (!catalogue.length) {
      el('kitcount').textContent = 'reading the catalogue\u2026'
      catalogue = await json('api/catalogue')
    }
    await features
    //: the server draws any preview it has not got yet, in the background
    json('api/thumbs/warm').catch(() => {})
    listCatalogue()
  })
  el('closekit').addEventListener('click',
    () => el('app').classList.remove('kit'))
  el('missionevents').addEventListener('click', openMissionEvents)
  el('snapgrid').checked = snapToGrid
  el('snapgrid').addEventListener('change', (ev) => {
    snapToGrid = ev.target.checked
    localStorage.setItem(SNAP_KEY, snapToGrid ? 'on' : 'off')
  })
  wireFabBrowser()
  el('kitsearch').addEventListener('input', () => listCatalogue())
  el('cpgroup').addEventListener('change', (ev) => { compoundGroup = ev.target.value; listCatalogue() })
  el('cpset').addEventListener('change', (ev) => { compoundSet = ev.target.value; listCatalogue() })
  //: the next page of cards when the list is scrolled near its end
  el('kitgrid').addEventListener('scroll', () => {
    const g = el('kitgrid')
    if (g.scrollTop + g.clientHeight > g.scrollHeight - KIT_MORE_AT) drawMoreCards()
  })
  el('kitfilters').addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-cat]')
    if (!b) return
    kitFilter = b.dataset.cat
    for (const other of el('kitfilters').querySelectorAll('[data-cat]')) {
      other.classList.toggle('on', other.dataset.cat === kitFilter)
    }
    listCatalogue()
  })
  el('kitgrid').addEventListener('click', (ev) => {
    const card = ev.target.closest('[data-name]')
    if (!card || !level) return
    //: in front of the camera, on the surface below that spot
    const where = viewDropPoint()
    if (card.dataset.kind === 'gameplay') placeCard(card.dataset.name, where)
    else addPiece({ name: card.dataset.name, kind: card.dataset.kind }, where)
  })
  el('kitgrid').addEventListener('dragstart', (ev) => {
    const card = ev.target.closest('[data-name]')
    if (!card) return
    ev.dataTransfer.setData('text/riftlord-piece', JSON.stringify(
      { name: card.dataset.name, kind: card.dataset.kind }))
    ev.dataTransfer.effectAllowed = 'copy'
  })
  renderer.domElement.addEventListener('dragover', (ev) => {
    if (ev.dataTransfer.types.includes('text/riftlord-piece')) {
      ev.preventDefault()
      ev.dataTransfer.dropEffect = 'copy'
    }
  })
  renderer.domElement.addEventListener('drop', (ev) => {
    const raw = ev.dataTransfer.getData('text/riftlord-piece')
    if (!raw || !level) return
    ev.preventDefault()
    const hits = pick(ev, levelPickables())
    const at = hits.length
      ? world.worldToLocal(hits[0].point.clone())
      : world.worldToLocal(controls.target.clone())
    const row = JSON.parse(raw)
    const where = [Math.round(at.x), Math.round(at.y), Math.round(at.z)]
    if (row.kind === 'gameplay') placeCard(row.name, where)
    else addPiece(row, where)
  })
  el('build').addEventListener('click', () => buildMod())
  wireSaveAs()
  el('closelevel').addEventListener('click', requestClose)
  el('newlevel').addEventListener('click', () => newLevel().catch(err => report('new level', err)))
  el('newlevel').addEventListener('pointerenter', () => newLevelChoices().catch(() => {}))
  el('details').addEventListener('click', openDetails)
  // Rift Lord's mission defaults fill a fresh form; a bucket item brings its own
  if (!new URLSearchParams(location.search).get('bucket')) {
    loadCampaigns().then(applyDefaults).catch(err => report('defaults', err))
  } else {
    loadCampaigns()
  }
  // Nothing but Open does anything until a level is loaded, and Open does nothing until
  // one is chosen.
  el('level').addEventListener('change', () => {
    el('load').disabled = !el('level').value
  })
  // something picked while the page was still starting stays openable
  el('load').disabled = !el('level').value
  setEnabled(false)
  busy('Pick a level and open it.')
  // Set last: the self-test waits for this rather than for the level list, because the
  // list is filled before the buttons are wired and a click in between does nothing.
  window.__rl_ready = true
}

// Hooks for the headless self-test, so it drives this code rather than a copy of it.
window.__rl_place = (kind, at) => place(kind, at)
window.__rl_gizmoAttached = () => !!gizmo.object
//: Where a placed marker sits on screen, so a test can click it for real rather than
//: calling the selection code directly. Selecting by clicking goes through pick(), and
//: a test that bypasses pick() cannot see a picking failure at all.
window.__rl_screenOf = (kind, id) => {
  const m = groups.added.children.find(c =>
    c.userData.plan && c.userData.kind === kind && c.userData.id === id)
  if (!m) return null
  world.updateMatrixWorld(true)
  const v = m.getWorldPosition(new THREE.Vector3()).project(camera)
  const r = renderer.domElement.getBoundingClientRect()
  return { x: Math.round(r.left + (v.x * 0.5 + 0.5) * r.width),
           y: Math.round(r.top + (-v.y * 0.5 + 0.5) * r.height),
           behind: v.z > 1 }
}
window.__rl_selectedNow = () => selected && { kind: selected.kind, id: selected.id }
//: Puts the camera at `pos` looking at `target`, both in Unreal coordinates.
window.__rl_view = (pos, target) => {
  const v = (p) => new THREE.Vector3(p[0], p[2], p[1])
  controls.target.copy(v(target))
  camera.position.copy(v(pos))
  controls.update()
  return true
}
//: What the level layer holds: instanced meshes, instances, and pieces per source.
window.__rl_levelReport = () => {
  let meshes = 0
  let instances = 0
  let mirrored = 0
  let foliage = 0
  for (const g of [groups.level, groups.foliage]) {
    g.traverse((o) => {
      if (o.isInstancedMesh) {
        meshes += 1
        instances += o.count
        if (o.userData.mirrored) mirrored += o.count
        if (g === groups.foliage) foliage += o.count
      }
    })
  }
  const bySource = {}
  for (const p of (level && level.pieces) || []) {
    bySource[p.source] = (bySource[p.source] || 0) + 1
  }
  return { meshes, instances, mirrored, foliage,
           foliageShown: groups.foliage.visible,
           pieces: ((level && level.pieces) || []).length,
           bySource, cut: cutPlane.constant }
}
//: the Trace tool's ghost: source, visible, triangles, opacity, mode (traceui.js)
window.__rl_trace = () => trace.report()
//: Pixels the Trace ghost alone covers from the current view (everything else hidden, on
//: black), with its depth passes or without them; and whether every ghost material
//: takes the cutaway's plane.
window.__rl_traceCoverage = (withDepth = true) => {
  const size = renderer.getDrawingBufferSize(new THREE.Vector2())
  const w = Math.max(1, size.x >> 1)
  const h = Math.max(1, size.y >> 1)
  const target = new THREE.WebGLRenderTarget(w, h, { depthBuffer: true, stencilBuffer: true })
  const shown = new Map([...scene.children, ...world.children].map(o => [o, o.visible]))
  for (const o of scene.children) o.visible = o === world
  for (const o of world.children) o.visible = o === trace.object
  const [background, fog] = [scene.background, scene.fog]
  scene.background = new THREE.Color(0, 0, 0)
  scene.fog = null
  trace.depthPass(withDepth)
  const px = new Uint8Array(w * h * 4)
  try {
    renderer.setRenderTarget(target)
    renderer.clear()
    renderer.render(scene, camera)
    renderer.readRenderTargetPixels(target, 0, 0, w, h, px)
  } finally {
    renderer.setRenderTarget(null)
    target.dispose()
    for (const [o, v] of shown) o.visible = v
    scene.background = background
    scene.fog = fog
    trace.depthPass(null)
  }
  let covered = 0
  for (let i = 0; i < px.length; i += 4) if (px[i] || px[i + 1] || px[i + 2]) covered++
  let clipped = true
  trace.object.traverse((o) => {
    if (o.material && o.material.clippingPlanes !== CUT_PLANES) clipped = false
  })
  return { covered, pixels: w * h, clipped }
}
//: The level's materials: how many, how many are drawn opaque (opacity 1, not
//: transparent, writing depth), and a signature of every one's opacity settings.
window.__rl_levelMaterials = () => {
  const seen = new Set()
  const sig = []
  let opaque = 0
  for (const g of [groups.level, groups.foliage]) {
    g.traverse((o) => {
      for (const m of Array.isArray(o.material) ? o.material : o.material ? [o.material] : []) {
        if (seen.has(m)) continue
        seen.add(m)
        if (m.opacity === 1 && !m.transparent && m.depthWrite) opaque += 1
        sig.push(`${m.opacity}/${m.transparent ? 1 : 0}/${m.depthWrite ? 1 : 0}`)
      }
    })
  }
  return { total: seen.size, opaque, signature: sig.join(',') }
}
window.__rl_wiringCount = () => groups.wiring.children
  .filter(c => c.userData && c.userData.wiring).length
//: What the door-mesh layer actually ended up holding, so "doors draw as their mesh"
//: is a measurement rather than a claim. A door whose class has no components, or
//: whose mesh will not load, shows up here as a missing entry.
//: How many objects each layer actually holds. "The routes toggle does nothing" and
//: "coverage shows nothing" are both answerable from this and were not before.
window.__rl_layerCounts = () => {
  const out = {}
  for (const [k, g] of Object.entries(groups)) {
    out[k] = { children: g.children.length, visible: g.visible }
  }
  return out
}
window.__rl_forcefields = () => ({
  level: groups.forcefield.children.length,
  planned: groups.doorMesh.children.filter(
    c => c.material && c.material.transparent).length,
})
window.__rl_doorMeshes = () => ({
  drawn: groups.doorMesh.children.length,
  //: the level's own doors are drawn as level geometry, so this layer holds only
  //: the plan's -- `drawn` is expected to be 0 until a door is added
  planned: (plan.doors || []).length,
  visible: groups.doorMesh.visible,
  names: groups.doorMesh.children.map(c => String((c.userData || {}).name)),
  doors: Object.values((level && level.doors) || {}).map(
    d => ({ name: String(d.export), cls: d.cls ? String(d.cls) : null,
            rot: (d.rotation || []).map(Number) })),
})
//: The added-mesh selection, so "I cannot select a mesh I just placed" is answerable
//: from the outside. The probe places through `addPiece`, projects the drawn part to
//: screen, and clicks it -- so a picking failure is visible rather than inferred.
window.__rl_addMesh = async (name, at) => {
  await addPiece({ name: name, kind: 'mesh' }, at)
  return plan.added.length
}
window.__rl_addKit = async (name, at) => {
  await addPiece({ name: name, kind: 'kit' }, at)
  return plan.added.length
}
//: Place New Object's effect `name` (a catalogue fx row) at `at`.
window.__rl_addEffect = async (name, at) => {
  const row = catalogue.find(r => r.kind === 'fx' && r.name === name)
  if (!row) return 0
  await addPiece(row, at)
  return plan.added.length
}
//: How many effects (the level's own, its sounds and the plan's added ones) are drawn now,
//: counting an object only when it and every group above it are visible.
window.__rl_effectsShown = () => {
  const drawn = (o) => { for (let x = o; x; x = x.parent) if (!x.visible) return false; return true }
  const own = groups.vfx.children.filter(m => m.userData.entry && drawn(m)).length
  const added = groups.newGeo.children.filter(m => m.userData.addedEntry &&
    m.userData.addedEntry.kind === 'fx' && drawn(m) && m.layers.test(camera.layers)).length
  return { own, added }
}
//: Each drawn part of added entry `i` (all entries when omitted): its materials' colour,
//: emissive and whether a texture is set and has arrived
window.__rl_addedLook = (i) => groups.newGeo.children
  .filter(m => i === undefined || m.userData.addedEntry === plan.added[i])
  .map(m => ({ mesh: m.userData.mesh, parts: m.children.length,
               glowing: m.children.filter(c => c.material && c.material.emissiveMap).length,
               mats: (Array.isArray(m.material) ? m.material : [m.material]).map(x => ({
                 type: x.type, color: x.color ? x.color.getHexString() : null,
                 emissive: x.emissive ? x.emissive.getHexString() : null,
                 map: !!x.map, loaded: !!(x.map && x.map.image),
                 override: (x.userData && x.userData.override) || null,
                 texture: (x.userData && x.userData.texture) || null })) }))
//: Fab pieces: the plan's entries (pack, object, chosen grids, scale), laid grids, the
//: trap-grid layer items they give and the drawn opening markers
window.__rl_fabState = () => ({
  entries: (plan.added || []).filter(e => e.kind === 'fab').map(e => ({
    pack: e.pack, object: e.object, grids: (e.grids || []).slice(),
    scale: fab.scaleOf(e.matrix).map(v => Math.round(v * 1000) / 1000),
    drawn: addedMeshes(e).length })),
  laid: (plan.fabGrids || []).length,
  layerItems: fab.layerItems(plan, new Set(), json).length,
  openings: fabOpenings.group.children.length,
})
//: the trap grids layer: lines drawn per surface ({Floor: n, ...}) and selected pieces
window.__rl_trapGrids = () => {
  trapGridAt = 0
  syncTrapGrids()
  const out = {}
  for (const o of groups.trapGrids.children) {
    const k = o.isLineSegments ? 'lines' : 'fills'
    out[k] = (out[k] || 0) + o.geometry.attributes.position.count / (o.isLineSegments ? 2 : 6)
  }
  return out
}
window.__rl_anyMeshName = () => [...piecesByMesh.keys()][0] || null
//: The LARGEST mesh in the level, for a probe that has to hit what it placed with a
//: single ray. Aiming at the first mesh in the table picked a rivet, the ray missed it
//: and hit the wall behind -- which read as "an added mesh cannot be selected" when
//: the selection code was fine.
window.__rl_bigMeshName = () => {
  let best = null
  for (const [name, box] of meshBounds) {
    const d = box.getSize(new THREE.Vector3()).length()
    if (!best || d > best[1]) best = [name, d]
  }
  return best && best[0]
}
window.__rl_addedScreen = (i) => {
  const entry = (plan.added || [])[i]
  const m = entry && addedMeshes(entry)[0]
  if (!m) return null
  world.updateMatrixWorld(true)
  const v = m.getWorldPosition(new THREE.Vector3()).project(camera)
  const r = renderer.domElement.getBoundingClientRect()
  return { x: Math.round(r.left + (v.x * 0.5 + 0.5) * r.width),
           y: Math.round(r.top + (-v.y * 0.5 + 0.5) * r.height),
           behind: v.z > 1 }
}
window.__rl_pickedNow = () => picked.map(p => ({
  mesh: String(p.mesh), added: !!p.added, id: p.id === undefined ? null : p.id }))
window.__rl_addedState = () => ({
  entries: (plan.added || []).length,
  drawn: groups.newGeo.children.length,
  outlines: groups.outline.children.length,
  at: (plan.added || []).map(e => e.matrix.slice(12, 15).map(Math.round)),
})

//: What the look override actually did, so "it did not change" can be told apart from
//: "there was nothing drawn to change" and "the texture never loaded".
window.__rl_lookReport = () => {
  const pools = []
  for (const [key, inst] of instByMesh) {
    if (piecesAreLiquid(piecesByMesh.get(key) || [])) pools.push(inst)
  }
  const painted = (mesh) => {
    const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material]
    return mats.some(m => m && m.map && m.userData && m.userData[LOOK_WAS] &&
                          m.map !== m.userData[LOOK_WAS].map)
  }
  return {
    look: Object.assign({}, plan.look || {}),
    skyMesh: !!skyMesh,
    skyFromLook: !!(skyMesh && skyMesh.userData.fromLook),
    skyMap: !!(skyMesh && skyMesh.material && skyMesh.material.map &&
               skyMesh.material.map.image && skyMesh.material.map.image.width),
    pools: pools.length,
    poolsPainted: pools.filter(painted).length,
  }
}

window.__rl_gizmoMode = () => gizmo.mode
//: A gizmo drag driven through TransformControls' own pointer handlers (NDC points), as a
//: mouse drag on `axis` would: {before, after} handle turns in degrees about Unreal z.
window.__rl_gizmoDrag = (mode, axis, from, to, shift) => {
  const c = gizmo.controls
  gizmo.setMode(mode)
  const yaw = () => {
    const e = new THREE.Euler().setFromQuaternion(handle.quaternion, 'ZYX')
    return THREE.MathUtils.radToDeg(e.z)
  }
  const before = yaw()
  shiftDown = !!shift
  c.rotationSnap = rotateStep() || null
  c.axis = axis
  c.getHelper().updateMatrixWorld(true)
  c.pointerDown({ x: from[0], y: from[1], button: 0 })
  c.rotationSnap = rotateStep() || null
  c.pointerMove({ x: to[0], y: to[1], button: -1 })
  c.pointerUp({ x: to[0], y: to[1], button: 0 })
  shiftDown = false
  return { before, after: yaw(), dragged: true }
}
//: The yaw of a plan `added` entry's matrix, in degrees, and its determinant's sign.
window.__rl_addedTurn = (i) => {
  const e = (plan.added || [])[i]
  if (!e) return null
  const m = new THREE.Matrix4().fromArray(e.matrix)
  return { yaw: THREE.MathUtils.radToDeg(Math.atan2(e.matrix[1], e.matrix[0])),
           det: Math.sign(m.determinant()) }
}
window.__rl_flip = (how) => flipPicked(how)
//: The column lengths (scale per axis) of a plan `added` entry's matrix.
window.__rl_addedScale = (i) => {
  const e = (plan.added || [])[i]
  return e ? [0, 1, 2].map(c => Math.hypot(e.matrix[c * 4], e.matrix[c * 4 + 1], e.matrix[c * 4 + 2]))
    : null
}
//: Edge snapping of the picked selection moved `dx` uu along x (alt not held; shift held
//: when `shift`): the move it ends up making, and the cue's outline count.
window.__rl_edgeSnapTry = (dx, shift) => {
  const from = handle.position.clone()
  edgeSnapBegin()
  gizmo.controls.axis = 'X'
  handle.position.x += dx
  handle.updateMatrix()
  shiftDown = !!shift
  edgeSnapMove()
  shiftDown = false
  const moved = handle.position.x - from.x
  const cue = edgeCue.children.length
  edgeSnapEnd()
  handle.position.copy(from)
  handle.updateMatrix()
  gizmo.controls.axis = null
  return { moved, cue }
}
//: The opacity of each door box marker of the level's own and the plan's doors.
window.__rl_doorBoxOpacity = () => groups.wiring.children.concat(groups.added.children)
  .filter(c => c.geometry && c.geometry.type === 'BoxGeometry' && c.material &&
    c.userData && c.userData.kind === 'door')
  .map(c => c.material.opacity)
//: Add a blocking volume at `at` as its New Object card does.
window.__rl_addVolume = (at) => addBlockingVolume(at)
//: The level's blocking and kill volumes by name, and the plan's edits of them.
window.__rl_volumeNames = () => groups.volumes.children.map(c => c.userData.name)
//: [name, what it can be made to stop as shipped or null] of each volume box
window.__rl_volumeBlocks = () => groups.volumes.children.map(c =>
  [c.userData.name, c.userData.data.added ? null : (c.userData.data.blocks || null)])
window.__rl_planVolumes = () => JSON.parse(JSON.stringify(plan.volumes || []))
//: Which drawn layers show now.
window.__rl_layersShown = () => Object.fromEntries(Object.entries(groups)
  .map(([k, g]) => [k, !!g.visible]))
//: Set plan `added` entry `i`'s matrix (column-major, Unreal space) and redraw it.
window.__rl_setAddedMatrix = (i, matrix) => {
  const entry = (plan.added || [])[i]
  if (!entry) return false
  entry.matrix = matrix.slice()
  moveAdded(entry)
  return true
}
//: The trap grids layer's drawn line counts per surface, re-synced now.
window.__rl_gridLines = () => { trapGridAt = 0; syncTrapGrids(); return trapGridLayer.signature.length }
window.__rl_savePlan = () => save()
window.__rl_levelName = () => level && level.level
//: The level's light (kind 'light'), effect ('fx') or sound ('sound') markers that can be
//: picked on their own, with where each is on screen.
window.__rl_levelActorMarks = (kind) => {
  world.updateMatrixWorld(true)
  const r = renderer.domElement.getBoundingClientRect()
  const g = kind === 'light' ? groups.lights : groups.vfx
  return g.children.filter(m => m.userData.entry && m.visible && m.userData.levelActor &&
    (kind === 'light' || !!m.userData.sound === (kind === 'sound')) &&
    !actorPieces(actorKey(m.userData.entry)).length).map(m => {
    const v = m.getWorldPosition(new THREE.Vector3()).project(camera)
    return { key: m.userData.levelActor, at: [m.position.x, m.position.y, m.position.z],
             x: Math.round(r.left + (v.x * 0.5 + 0.5) * r.width),
             y: Math.round(r.top + (-v.y * 0.5 + 0.5) * r.height), behind: v.z > 1,
             //: what it draws as: a light's colour, an effect's system and group
             colour: kind === 'light' ? rgbHex(m.material.color.toArray()) : null,
             template: m.userData.vfx, group: m.userData.group || null,
             actorClass: m.userData.entry.actorClass || null, lightKind: m.userData.entry.kind }
  })
}
//: The plan fields this round's tools write.
window.__rl_planBits = () => JSON.parse(JSON.stringify({
  removedFx: plan.removedFx || [], actorMoves: plan.actorMoves || {},
  lightColours: plan.lightColours || {}, fxSwaps: plan.fxSwaps || {},
  events: plan.events || [], added: (plan.added || []).map(a => ({ kind: a.kind, name: a.name,
    paints: a.paints || null })), paints: (plan.look || {}).paints || {} }))
//: Nothing picked or selected, the handles away, as Escape leaves it
window.__rl_deselect = () => {
  picked = []
  selected = null
  wiringPick = null
  gizmo.detach()
  refreshPicked()
  showProps()
}
//: the plan's Crystal entry as it stands (crystalui.js), or null
window.__rl_crystalEntry = () => JSON.parse(JSON.stringify(planCrystal(plan) || null))
window.__rl_pickedKinds = () => picked.map(p => p.levelActor ? 'actor:' + p.kind
  : p.added ? 'added:' + p.added.kind : p.marker ? 'marker' : 'piece')
window.__rl_eventsOpen = () => eventsOpen
window.__rl_cut = () => ({ at: cutPlane.constant, range: cutBand() })
//: The share of play-area pieces reaching above the cut at slider steps 999 and 500
window.__rl_cutShares = () => {
  const boxes = playBoxes()
  const above = (step) => boxes.filter(b => b.max.y > cutHeight(step)).length / Math.max(1, boxes.length)
  const tops = boxes.map(b => b.max.y).sort((a, b) => a - b)
  return { pieces: boxes.length, above999: above(999), above500: above(500),
           tops: [0.5, 0.9, 0.95, 0.98, 0.99, 0.995, 1].map(q => Math.round(quantile(tops, q))) }
}
window.__rl_planWorld = () => Object.assign({}, plan.world || {})
window.__rl_planMinimap = () => JSON.parse(JSON.stringify(plan.minimap || {}))
window.__rl_planLook = () => JSON.parse(JSON.stringify(plan.look || {}))
window.__rl_levelOpen = () => !!level
//: Liquid planes that can take their own material, and what the plane layer drew.
window.__rl_planes = () => ({
  paintable: ((level && level.pieces) || []).filter(paintablePlane).map(p => p.id),
  drawn: [...planeMeshes.entries()].map(([id, m]) =>
    ({ id, material: m.userData.material, textured: m.userData.textured })),
})
window.__rl_pickPiece = (id) => {
  picked = [level.pieces[id]]
  selected = null
  wiringPick = null
  refreshPicked()
  showProps()
}
window.__rl_doorAxes = () => Object.fromEntries(doorAxes)
window.__rl_planCounts = () => {
  const out = {}
  for (const k of ['rifts','doors','spawners','wiring','removed','edits','added']) {
    out[k] = (plan[k] || []).length
  }
  out.look = Object.assign({}, plan.look || {})
  return out
}

//: Every drawn door part with its mesh and final scale, so "resizing a door does
//: nothing on screen" is a measurement.
window.__rl_doorMeshScales = () => groups.doorMesh.children.map(m => ({
  name: String((m.userData || {}).name),
  mesh: String((m.userData || {}).mesh || ''),
  scale: [m.scale.x, m.scale.y, m.scale.z].map(v => Math.round(v * 1000) / 1000),
}))
window.__rl_readout = () => el('readout').textContent.replace(/\s+/g, ' ').trim()

//: Route tubes actually drawn, and what each plan branch resolves to, so "the line
//: does not appear" separates a drawing bug from an unresolved target.
window.__rl_routeTubes = () => groups.added.children
  .filter(c => c.geometry && c.geometry.type === 'TubeGeometry').length
window.__rl_branchTargets = () => {
  const out = []
  for (const pt of plan.points || []) {
    for (const b of pt.next || []) {
      out.push({ from: pt.id, to: String(b.to),
                 resolved: b.to === 'rift' ? !!planRift() : !!branchTarget(b.to) })
    }
  }
  return out
}
//: The plan's actor graph as the page holds it, for probes.
window.__rl_planActors = () => JSON.parse(JSON.stringify({
  version: plan.version, points: plan.points, spawners: plan.spawners,
  playerStarts: plan.playerStarts, wiring: plan.wiring, rifts: plan.rifts }))
//: {point id: rift id} of the plan's rifts' own exit points (actors.js riftExits).
window.__rl_riftExits = () => Object.fromEntries(riftExits(plan, level))
//: Each plan spawner's drawn marker: {id, yaw (degrees, its rotation about Unreal z),
//: nose (a facing cone is drawn)}.
window.__rl_spawnerMarkers = () => groups.added.children
  .filter(c => c.userData.plan && c.userData.kind === 'spawner')
  .map(c => ({ id: c.userData.id, yaw: THREE.MathUtils.radToDeg(c.rotation.z),
               nose: c.children.some(n => n.geometry === SPAWNER_NOSE_GEO) }))
//: Where a Ref (an added point's id, a level point's name) stands after the plan's edits.
window.__rl_pointAt = (ref) => refLocation(ref)
window.__rl_graph = () => {
  const r = graphReport(plan, level)
  return { cycle: [...r.cycle], stranded: [...r.stranded] }
}
//: Select one of the level's own actors by name, as a click on its marker does.
window.__rl_pickWiring = (name) => {
  const m = groups.wiring.children.find(c => c.userData.wiring && c.userData.name === name)
  if (!m) return false
  picked = []
  selected = null
  wiringPick = m
  refreshPicked()
  showProps()
  return true
}
window.__rl_pickVolume = (name) => {
  const m = groups.volumes.children.find(c => c.userData.name === name)
  if (!m) return false
  picked = []
  selected = null
  wiringPick = m
  refreshPicked()
  showProps()
  return true
}
window.__rl_removeWiringPick = () => removeWiringPick()
window.__rl_undo = () => undo()
window.__rl_dropPicked = () => dropPicked()
window.__rl_levelDoorNames = () => Object.keys((level && level.doors) || {})
window.__rl_wiringVisible = (name) => {
  const m = groups.wiring.children.find(c => c.userData.wiring && c.userData.name === name)
  return m ? m.visible : null
}
//: {drawn, hidden} parts of one of the level's own doors, force-field panes included.
window.__rl_doorParts = (name) => {
  const d = ((level && level.doors) || {})[name]
  const out = { index: d ? d.index : null, drawn: 0, hidden: 0 }
  if (!d || !d.index) return out
  const m = new THREE.Matrix4()
  for (const [mesh, inst] of instByMesh) {
    (piecesByMesh.get(mesh) || []).forEach((p, i) => {
      if (p.export !== d.index || (p.sub || 'persistent') !== 'persistent') return
      inst.getMatrixAt(i, m)
      if (m.determinant() === 0) out.hidden += 1
      else out.drawn += 1
    })
  }
  for (const f of groups.forcefield.children) {
    if ((f.userData.piece || {}).export !== d.index) continue
    if (f.visible) out.drawn += 1
    else out.hidden += 1
  }
  return out
}
//: The floor snap's answer for `at` on the open plan (askFloor)
window.__rl_askFloor = (at, mode) => askFloor(at, mode)
window.__rl_riftAt = () => ((level && level.rifts) || [])[0]?.loc || null
window.__rl_pickAdded = (i) => {
  const entry = (plan.added || [])[i]
  picked = entry ? [addedRecord(entry)] : []
  selected = null
  wiringPick = null
  refreshPicked()
  showProps()
  return picked.length
}
window.__rl_addedBottom = (i) => {
  const entry = (plan.added || [])[i]
  const box = entry && pickedBox(addedRecord(entry))
  return box ? Math.round(box.min.z) : null
}
window.__rl_select = (kind, id) => {
  selected = { kind, id }
  wiringPick = null
  picked = []
  attachTo(selected)
  showProps()
}

window.__rl_skyReport = () => {
  const mesh = groups.sky.children[0]
  const map = mesh && mesh.material && mesh.material.map
  return {
    children: groups.sky.children.length,
    visible: groups.sky.visible,
    wanted: (level && level.sky && level.sky.texture) || null,
    hasMap: !!map,
    loaded: !!(map && map.image && map.image.width),
    size: map && map.image ? [map.image.width, map.image.height] : null,
  }
}

//: The mission a save goes into: the one opened from Missions, or the one the first
//: save of a new mission made. Null means a new mission.
let editing = null

//: A Missions entry in the level picker is `mission:<id>`.
const MISSION_PREFIX = 'mission:'
//: The levels the server offers, kept for when the picker is rebuilt.
let allLevels = []

/** The level picker: Missions first (what the Map Editor saved, which reopen with
 * their plan), then every level. A mission's own copy of its level is left out of the
 * levels: it is reached through its mission. */
async function refreshMissionList () {
  let missions = []
  try { missions = await json('api/missions') } catch (err) { missions = [] }
  const ownMaps = new Set(missions.map(m => m.map).filter(Boolean))
  const node = el('level')
  const was = node.value
  const opt = (value, label, off, file) =>
    `<option value="${escapeText(value)}"${off ? ' disabled' : ''}` +
    `${file ? ` title="${escapeText(file)}"` : ''}>${escapeText(label)}</option>`
  node.innerHTML = '<option value="">Choose a mission</option>' +
    (missions.length
      ? '<optgroup label="Custom Missions">' + missions.map(m => opt(MISSION_PREFIX + m.id,
          m.editable ? m.title : `${m.title} (its level is unknown)`, !m.editable)).join('') +
        '</optgroup>'
      : '') +
    levelGroups(allLevels.filter(l => !ownMaps.has(l.name))).map(([label, rows]) =>
      `<optgroup label="${escapeText(label)}">` + rows.map(l =>
        opt(l.name, l.title || levelLabel(l.name), false, l.name)).join('') + '</optgroup>')
      .join('')
  if ([...node.options].some(o => o.value === was)) node.value = was
}

//: The group of a level the server files under no campaign (levelgroups.CUSTOM_MAPS).
const CUSTOM_MAPS = 'Custom Maps'

/** The level picker's categories: [campaign, levels], in the server's order (api/levels:
 * each level's `campaign`, from the game's campaign tables, riftlord/levelgroups.py). */
function levelGroups (levels) {
  const out = new Map()
  for (const l of levels) {
    const key = l.campaign || CUSTOM_MAPS
    if (!out.has(key)) out.set(key, [])
    out.get(key).push(l)
  }
  return [...out]
}

/** A readable name for a level without a mission name: RiftLordPierPressure_Map01 ->
 * Pier Pressure. */
const levelLabel = (name) => name.replace(/^RiftLord/, '').replace(/_Map\d+$/, '')
  .replace(/_/g, ' ').replace(/([a-z])([A-Z0-9])/g, '$1 $2').trim() || name

//: The New Level dialog's [bases, skies], fetched once (read ahead when the button is
//: hovered).
let newLevelLists = null
const newLevelChoices = () => {
  newLevelLists = newLevelLists || Promise.all([json('api/scratch/bases'),
    json('api/scratch/skies').catch(() => [])])
  newLevelLists.catch(() => { newLevelLists = null })
  return newLevelLists
}

/** A new level from scratch (riftlord/scratch.py): named in a dialog, opened on an
 * empty grid with its rift, exit and player starts already placed. Saving makes it a
 * mission like any other. */
async function newLevel () {
  const [[bases, skies], modes] = await whileLoading('Starting a new level…', () =>
    Promise.all([newLevelChoices(), json('api/scratch/modes').catch(() => [])]))
  const want = await scratchUi.ask(bases, skies, modes)
  if (!want) return
  const { gameType, ...asked } = want
  const fresh = await whileLoading('Starting ' + want.name + '…', () => json('api/scratch/new', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Rift-Lord': '1' },
    body: JSON.stringify(asked),
  }))
  editing = null
  mission = blankMission()
  await loadCampaigns()
  await applyDefaults().catch(() => {})
  el('level').value = ''
  await openLevel(want.base, true)
  await resumePlan(fresh)
  //: the chosen game mode's piece, placed as its New Object card places it
  if (MODE_PIECES[gameType]) await MODE_PIECES[gameType]()
  newMissionFor = level && level.level
  takeNewMissionDefaults()
  fillMission({ title: want.name })
}

//: GameType (riftlord/onslaught/gamemode.py MODES) -> what a new level of that mode
//: places beside its rift.
const MODE_PIECES = { CrystalDefense: () => addCrystal() }

/** Open a mission from Missions (`?bucket=<id>`, or the picker): the level it was built
 * on, the plan it was built from, and its mission fields. */
async function openBucketItem (id) {
  return whileLoading('Reading the mission…', () => readBucketItem(id))
}

async function readBucketItem (id) {
  const rec = await json('api/bucket/item/' + encodeURIComponent(id))
  // the level it was built on; a mission made in the Onslaught Editor has no layout of
  // its own yet, only the map its waves were authored on
  const source = rec.level || (rec.spec && rec.spec.map)
  if (!source) {
    flash(`${rec.title || id} does not say which level it was built on.`)
    return
  }
  const d = rec.details || {}
  const fields = {
    title: rec.title, author: d.author, version: d.version, description: d.description,
    placement: rec.placement,
    campaignName: (d.newCampaign || {}).name,
    campaignDescription: (d.newCampaign || {}).description,
    hub: d.hub,
    campaignHub: (d.newCampaign || {}).hub,
    endless: rec.endless === true || d.endless === true,
    endlessOnly: rec.endlessOnly === true,
    resetDoors: rec.resetDoors === true,
    scramble: rec.scramble ?? null,
  }
  // the fields first: drawing a level takes a while, and they are what says which
  // mission this is
  mission = blankMission()
  await loadCampaigns()
  fillMission(fields)
  el('level').value = MISSION_PREFIX + id
  el('load').disabled = false
  await openLevel(source, !!(rec.plan && rec.plan.scratch))
  if (rec.plan) await resumePlan(rec.plan)
  fillMission(fields)
  //: its loading-screen and hub portal pictures (bucket.picture_view), or null
  mission.picture = rec.pictureView || null
  mission.portalPicture = rec.portalPictureView || null
  // saves go back into this mission, whichever editor made it, keeping its waves
  editing = id
  missionMap = rec.map || (rec.mod ? rec.mod + '_Map01' : null)
  if (showingMapSettings()) showProps()
  trace.sync(plan)
  savedChanges = changes
  loadMapInfo()
}

//: compound kits and decoration tools (compounds.js, decortools.js)
const decorHooks = {
  json, report, flash, snapshot, drawPlan, blank, addedRecord, drawAdded, redrawAdded,
  refreshPicked, showProps, pick, showEffects,
  plan: () => plan, level: () => level, picked: () => picked, world: () => world,
  groups: () => groups,
  eye: () => world.worldToLocal(camera.position.clone()),
  targets: () => levelPickables().concat(groups.newGeo.children),
  setPicked: (list) => {
    picked = list
    selected = null
    wiringPick = null
    refreshPicked()
    showProps()
  },
  clearSelection: () => {
    picked = []
    selected = null
    wiringPick = null
    eventsOpen = false
    gizmo.detach()
    refreshPicked()
  },
}
compounds.install(decorHooks)
decor.install(decorHooks)

boot()
  .then(() => {
    const id = new URLSearchParams(location.search).get('bucket')
    if (id) return openBucketItem(id)
  })
  .catch(err => report('boot', err))
