// A map's moons in the Map Editor: Map Settings' Moon section (one per moon; Add Moon,
// Delete), the direction handle's rotator, and the moons the viewport draws on the sky.
//
// Plan data (riftlord/build/world/moons.py): `plan.moons` {'<sublevel>|<actor export>':
// {Property: value}} for the level's own moons, `plan.addedMoons` [{Property: value}] for
// added ones (a property not set is the donor moon's); a level moon in `plan.removedFx` is
// gone. Properties: RelativeLocation, RelativeRotation, RelativeScale3D, Material. The
// fields are Pitch and Yaw (RelativeLocation's direction), Distance (its length), Scale
// (RelativeScale3D) and Material; a direction or distance change also turns the moon so it
// keeps the face it showed the level. A value equal to the shipped one stores nothing.
// /api/mapinfo's `moons` holds {moons: [{key, name, values}], added, fields, materials,
// looks}.

import * as THREE from 'three'

const esc = (text) => String(text ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/>/g, '&gt;').replace(/"/g, '&quot;')

//: The location fields and the rotation that moves with them.
const LOCATION = 'RelativeLocation'
const ROTATION = 'RelativeRotation'
const SCALE = 'RelativeScale3D'
const MATERIAL = 'Material'
//: Two values within these are the same (uu, scale, degrees).
const SAME_UU = 0.5
const SAME_SCALE = 1e-3
const SAME_ANGLE = 0.01
//: The view draws the moons on a sphere of this radius around the camera: inside the
//: sky dome the view draws (main.js SKY_RADIUS, 300,000).
export const VIEW_RADIUS = 280000

const rad = (d) => d * Math.PI / 180
const deg = (r) => r * 180 / Math.PI
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
const unit = (v) => { const n = Math.hypot(v[0], v[1], v[2]); return n > 0 ? v.map(c => c / n) : null }
const round = (v, k) => Math.round(v * k) / k

/** (pitch, yaw, distance) of a location from the level's origin (FVector::Rotation). */
export function direction (loc) {
  const d = Math.hypot(loc[0], loc[1], loc[2])
  if (!(d > 0)) return { pitch: 0, yaw: 0, distance: 0 }
  return { pitch: deg(Math.asin(Math.max(-1, Math.min(1, loc[2] / d)))),
           yaw: deg(Math.atan2(loc[1], loc[0])), distance: d }
}

/** The location `distance` from the origin along (pitch, yaw). */
export function locationOf (pitch, yaw, distance) {
  const p = rad(pitch)
  const y = rad(yaw)
  return [distance * Math.cos(p) * Math.cos(y), distance * Math.cos(p) * Math.sin(y),
    distance * Math.sin(p)]
}

/** The X, Y and Z axes of a rotator (Unreal's FRotationMatrix). */
function rows (rot) {
  const [p, y, r] = rot.map(rad)
  const [sp, cp, sy, cy, sr, cr] = [Math.sin(p), Math.cos(p), Math.sin(y), Math.cos(y), Math.sin(r), Math.cos(r)]
  return [[cp * cy, cp * sy, sp],
    [sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp],
    [-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp]]
}

/** The rotator of three axes (Unreal's FMatrix::Rotator). */
function rotatorOfRows ([x, y, z]) {
  const pitch = deg(Math.atan2(x[2], Math.hypot(x[0], x[1])))
  const yaw = deg(Math.atan2(x[1], x[0]))
  const sy = rows([pitch, yaw, 0])[1]
  return [pitch, yaw, deg(Math.atan2(dot(z, sy), dot(y, sy)))]
}

/** `v` turned `angle` radians about unit `axis`. */
function turn (v, axis, angle) {
  const c = Math.cos(angle)
  const s = Math.sin(angle)
  const cross = [axis[1] * v[2] - axis[2] * v[1], axis[2] * v[0] - axis[0] * v[2], axis[0] * v[1] - axis[1] * v[0]]
  const k = dot(axis, v) * (1 - c)
  return v.map((c0, i) => c0 * c + cross[i] * s + axis[i] * k)
}

/** `rot` turned by the least turn taking direction `was` to `now` (moons.carried_rotation). */
export function carried (rot, was, now) {
  const a = unit(was)
  const b = unit(now)
  if (!a || !b) return rot.slice()
  const angle = Math.acos(Math.max(-1, Math.min(1, dot(a, b))))
  if (deg(angle) < SAME_ANGLE) return rot.slice()
  const axis = unit([a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]) ||
    unit([-a[1], a[0], 0]) || [1, 0, 0]
  return rotatorOfRows(rows(rot).map(r => turn(r, axis, angle))).map(v => round(v, 10000))
}

function same (a, b, prop) {
  if (a === undefined || b === undefined) return a === b
  if (prop === MATERIAL) return a === b
  const tol = prop === LOCATION ? SAME_UU : prop === SCALE ? SAME_SCALE : SAME_ANGLE
  return a.length === b.length && a.every((v, i) => Math.abs(v - b[i]) <= tol)
}

// -- targets -----------------------------------------------------------------------------
//
// A target is one moon: {id ('moon:<key>' or 'addedmoon:<n>'), title, base (its shipped
// values), get () -> the plan's values or undefined, set (values | null)}.

/** The moons Map Settings lists: the level's own (less the deleted ones), then the added. */
export function moonTargets (info, plan) {
  const m = info && info.moons
  if (!m) return []
  const gone = new Set(plan.removedFx || [])
  const out = []
  const own = (m.moons || []).filter(x => !gone.has(x.key))
  own.forEach((moon, i) => {
    out.push({ id: 'moon:' + moon.key, key: moon.key, title: own.length > 1 ? `Moon ${i + 1}` : 'Moon',
      base: moon.values,
      get: () => (plan.moons || {})[moon.key],
      set: (v) => {
        plan.moons = { ...(plan.moons || {}) }
        if (v) plan.moons[moon.key] = v
        else delete plan.moons[moon.key]
      } })
  })
  ;(plan.addedMoons || []).forEach((entry, n) => {
    out.push({ id: 'addedmoon:' + n, n, title: 'Added Moon ' + (n + 1), base: m.added || {},
      get: () => plan.addedMoons[n],
      set: (v) => { plan.addedMoons[n] = v || {} } })
  })
  return out
}

/** A moon's values: the shipped ones under the plan's. */
export function shown (target) {
  return { ...(target.base || {}), ...(target.get() || {}) }
}

/** Write `props` ({Property: value}) to a moon: a value equal to the shipped one stores
 * nothing. */
function setProps (target, props) {
  const now = { ...(target.get() || {}) }
  for (const [prop, value] of Object.entries(props)) {
    if (value === undefined || same(value, (target.base || {})[prop], prop)) delete now[prop]
    else now[prop] = value
  }
  target.set(Object.keys(now).length ? now : null)
}

/** Set one field: Pitch, Yaw, Distance (with the turn that keeps the moon's face to the
 * level), Scale or Material. */
export function setField (target, field, value) {
  const v = shown(target)
  if (field === MATERIAL) return setProps(target, { [MATERIAL]: value })
  if (field === 'Scale') return setProps(target, { [SCALE]: [value, value, value] })
  const d = direction(v[LOCATION] || [0, 0, 0])
  if (field === 'Pitch') d.pitch = value
  if (field === 'Yaw') d.yaw = value
  if (field === 'Distance') d.distance = value
  //: the shipped direction and distance as the fields show them: the shipped location
  const base = target.base || {}
  const now = { [LOCATION]: locationOf(d.pitch, d.yaw, d.distance) }
  if (base[LOCATION] && ['Pitch', 'Yaw', 'Distance'].every(f => fieldValue(now, f) === fieldValue(base, f))) {
    return resetField(target, 'Pitch')
  }
  setLocation(target, now[LOCATION])
}

/** Move a moon to `loc`, turned with it. */
export function setLocation (target, loc) {
  const v = shown(target)
  const was = v[LOCATION] || [0, 0, 0]
  const now = loc.map(c => round(c, 100))
  setProps(target, { [LOCATION]: now, [ROTATION]: carried(v[ROTATION] || [0, 0, 0], was, now) })
}

/** Take a field's values out of the plan (Pitch, Yaw and Distance are the location and
 * its turn). */
export function resetField (target, field) {
  const now = { ...(target.get() || {}) }
  const props = field === MATERIAL ? [MATERIAL] : field === 'Scale' ? [SCALE] : [LOCATION, ROTATION]
  for (const p of props) delete now[p]
  target.set(Object.keys(now).length ? now : null)
}

/** The value a field shows. */
export function fieldValue (values, field) {
  if (field === MATERIAL) return values[MATERIAL]
  if (field === 'Scale') return round((values[SCALE] || [1])[0], 100)
  const d = direction(values[LOCATION] || [0, 0, 0])
  if (field === 'Pitch') return round(d.pitch, 100)
  if (field === 'Yaw') return round(d.yaw, 100)
  return Math.round(d.distance)
}

/** Whether the plan stores what a field shows. */
function isOwn (own, field) {
  if (field === MATERIAL) return MATERIAL in own
  if (field === 'Scale') return SCALE in own
  return LOCATION in own || ROTATION in own
}

/** The rotator the direction handle shows for a moon: from the moon toward the level, so
 * its ball stands the way the moon is (sunhandle.js). */
export function handleRotator (values) {
  const d = direction(values[LOCATION] || [0, 0, 1])
  return [-d.pitch, d.yaw + 180, 0]
}

/** The (pitch, yaw) a dragged handle rotator puts the moon at. */
export function fromHandle (rot) {
  return { pitch: -rot[0], yaw: ((rot[1] + 360) % 360) - 180 }
}

// -- the section -------------------------------------------------------------------------

function fieldHtml (t, f, value, own, materials) {
  const at = `data-moon-target="${esc(t)}" data-moon-prop="${esc(f.prop)}"`
  const reset = own ? `<button type="button" class="litreset" data-moon-reset="${esc(t)}" ` +
    `data-moon-prop="${esc(f.prop)}">Reset</button>` : ''
  let input
  if (f.kind === 'enum') {
    const opts = [...new Set([...(materials || []), value])]
    input = `<select ${at}>` + opts.map(o =>
      `<option value="${esc(o)}"${o === value ? ' selected' : ''}>${esc(o)}</option>`).join('') + '</select>'
  } else {
    const range = (f.min !== undefined && f.min !== null ? ` min="${f.min}"` : '') +
      (f.max !== undefined && f.max !== null ? ` max="${f.max}"` : '') + ` step="${f.step || 'any'}"`
    input = `<input type="number"${range} ${at} value="${esc(value)}">`
  }
  return `<label class="pick"><span>${esc(f.label)}${reset}</span>${input}</label>`
}

/** Map Settings' Moon section. `selected`: the id of the moon whose handle shows. */
export function moonsHtml (targets, info, selected) {
  const m = info && info.moons
  if (!m) return ''
  const fields = m.fields || []
  const out = ['<h2>Moon</h2>']
  for (const t of targets) {
    const values = shown(t)
    const own = t.get() || {}
    const html = fields.map(f => fieldHtml(t.id, f, fieldValue(values, f.prop), isOwn(own, f.prop), m.materials))
    const pairs = []
    for (let i = 0; i < html.length; i += 2) {
      const two = html.slice(i, i + 2)
      pairs.push(`<div class="pair" style="grid-template-columns: repeat(${two.length}, minmax(0, 1fr))">` +
        two.join('') + '</div>')
    }
    out.push(`<div class="litsun${t.id === selected ? ' on' : ''}"><div class="sunhead">` +
      `<button type="button" class="sunpick${t.id === selected ? ' on' : ''}" data-moon-pick="${esc(t.id)}">` +
      `${esc(t.title)}</button><button type="button" data-moon-del="${esc(t.id)}">Delete</button></div>` +
      pairs.join('') + '</div>')
  }
  if (m.added) out.push('<button type="button" id="moonadd">Add Moon</button>')
  return out.join('')
}

/** Wire the Moon section. `on`: {change(fn), pick(id), del(id), add()}. */
export function wireMoons (body, targets, info, on) {
  const fields = ((info && info.moons) || {}).fields || []
  for (const input of body.querySelectorAll('[data-moon-target][data-moon-prop]')) {
    if (input.tagName === 'BUTTON') continue
    input.addEventListener('change', () => {
      const target = targets.find(t => t.id === input.dataset.moonTarget)
      const f = fields.find(x => x.prop === input.dataset.moonProp)
      if (!target || !f) return
      let value = input.value
      if (f.kind !== 'enum') {
        value = +input.value
        if (input.value.trim() === '' || !Number.isFinite(value)) return
        if (f.min !== undefined && f.min !== null) value = Math.max(f.min, value)
        if (f.max !== undefined && f.max !== null) value = Math.min(f.max, value)
      }
      on.change(() => setField(target, f.prop, value))
    })
  }
  for (const b of body.querySelectorAll('[data-moon-reset]')) {
    b.addEventListener('click', (ev) => {
      ev.preventDefault()
      const target = targets.find(t => t.id === b.dataset.moonReset)
      if (target) on.change(() => resetField(target, b.dataset.moonProp))
    })
  }
  for (const b of body.querySelectorAll('[data-moon-pick]')) {
    b.addEventListener('click', () => on.pick(b.dataset.moonPick))
  }
  for (const b of body.querySelectorAll('[data-moon-del]')) {
    b.addEventListener('click', () => on.del(b.dataset.moonDel))
  }
  const add = body.querySelector('#moonadd')
  if (add) add.addEventListener('click', () => on.add())
}

/** Delete a moon: a level moon goes into `removedFx`, an added one leaves the plan. */
export function deleteMoon (plan, id) {
  if (id.startsWith('moon:')) {
    plan.removedFx = [...new Set((plan.removedFx || []).concat([id.slice(5)]))].sort()
  } else if (id.startsWith('addedmoon:')) {
    const n = +id.slice(10)
    plan.addedMoons = (plan.addedMoons || []).filter((_e, i) => i !== n)
    if (!plan.addedMoons.length) delete plan.addedMoons
  }
}

/** Add a moon (its values the donor's); returns its id. */
export function addMoon (plan) {
  plan.addedMoons = (plan.addedMoons || []).concat([{}])
  return 'addedmoon:' + (plan.addedMoons.length - 1)
}

// -- the view ----------------------------------------------------------------------------

/** The moons the view draws: [{id, dir (level, unit), angle (radians across), material}]. */
export function sceneMoons (info, plan) {
  return moonTargets(info, plan).map(t => {
    const v = shown(t)
    const d = Math.hypot(...(v[LOCATION] || [0, 0, 0]))
    //: /Engine/BasicShapes/Plane is 100 uu across
    const width = 100 * Math.abs((v[SCALE] || [1])[0])
    return { id: t.id, dir: unit(v[LOCATION] || [0, 0, 1]) || [0, 0, 1],
             angle: d > 0 ? 2 * Math.atan(width / 2 / d) : 0, material: v[MATERIAL] }
  })
}

/** The moons drawn on the sky: each a sprite of its material's texture, tinted by its
 * EM_Color times EM_Brightness, the size it spans from the level, around the camera. */
export class MoonView {
  /** `parent`: the group the sprites live in (the sky layer, level space). */
  constructor (parent) {
    this.parent = parent
    this.group = new THREE.Group()
    this.group.userData = { sky: true }
    this.sprites = []
    this.textures = new Map()
    this.list = []
  }

  texture (path) {
    if (!this.textures.has(path)) {
      const map = new THREE.TextureLoader().load('api/texture' + path)
      map.colorSpace = THREE.SRGBColorSpace
      this.textures.set(path, map)
    }
    return this.textures.get(path)
  }

  /** Draw `list` (sceneMoons); `looks` {material: {texture, colour}}. */
  sync (list, looks) {
    if (this.group.parent !== this.parent) this.parent.add(this.group)
    this.list = list
    while (this.sprites.length < list.length) {
      const s = new THREE.Sprite(new THREE.SpriteMaterial({ transparent: true, depthWrite: false,
        fog: false, blending: THREE.AdditiveBlending }))
      s.renderOrder = -1
      this.sprites.push(s)
      this.group.add(s)
    }
    this.sprites.forEach((s, i) => {
      const m = list[i]
      s.visible = !!m
      if (!m) return
      const look = (looks || {})[m.material] || {}
      const map = look.texture ? this.texture(look.texture) : null
      if (s.material.map !== map) {
        s.material.map = map
        s.material.needsUpdate = true
      }
      const c = look.colour || [1, 1, 1]
      s.material.color.setRGB(c[0], c[1], c[2])
      s.scale.setScalar(2 * VIEW_RADIUS * Math.tan(m.angle / 2))
    })
  }

  /** Keep the moons around `eye` (the camera, level space). */
  follow (eye) {
    this.sprites.forEach((s, i) => {
      const m = this.list[i]
      if (!m) return
      s.position.set(eye.x + m.dir[0] * VIEW_RADIUS, eye.y + m.dir[1] * VIEW_RADIUS,
        eye.z + m.dir[2] * VIEW_RADIUS)
    })
  }
}
