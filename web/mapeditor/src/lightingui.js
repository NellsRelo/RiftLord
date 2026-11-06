// A map's lighting in the Map Editor: Map Settings' Sun, Sky Light, Atmospheric Fog,
// Height Fog and Post Process sections, the Light section of picked lights, and the
// values the viewport previews them with.
//
// Plan data (riftlord/build/world/lighting.py): `plan.lighting` {section: {Property:
// value}}, `plan.lights` {'<sublevel>|<actor export>': {Property: value}}, an added light
// actor's `light` {Property: value}; a level sun in `plan.removedFx` is off. A value equal
// to the shipped one stores nothing. /api/mapinfo's `lighting` holds the level's own
// values and the fields ({suns, sections, fields, titles}).

const esc = (text) => String(text ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/>/g, '&gt;').replace(/"/g, '&quot;')

export const STATIC = 'EComponentMobility::Static'
export const MOVABLE = 'EComponentMobility::Movable'
//: The class an added sun is (leveldata.ACTOR_GROUPS 'Lights').
export const SUN_CLASS = 'DirectionalLight'
//: Level-wide sections, in the order Map Settings shows them.
export const SECTIONS = ['skyLight', 'atmosphere', 'heightFog', 'postProcess']
//: Post-process settings come with an override switch of this prefix.
const OVERRIDE = 'bOverride_'
//: Degrees within which two sun directions are the same.
const SAME_ANGLE = 0.01

const toSrgb = (c) => (c <= 0.0031308 ? 12.92 * c : 1.055 * Math.pow(c, 1 / 2.4) - 0.055)
const toLinear = (c) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4))
const clamp01 = (v) => Math.min(1, Math.max(0, v))

/** '#rrggbb' for a linear [r, g, b, a]. */
export function linearHex (rgba) {
  return '#' + (rgba || [0, 0, 0]).slice(0, 3).map(c =>
    Math.round(clamp01(toSrgb(c)) * 255).toString(16).padStart(2, '0')).join('')
}

/** A linear [r, g, b, a] for '#rrggbb', keeping alpha `a`. */
export function hexLinear (hex, a) {
  const n = parseInt(hex.slice(1, 7), 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
    .map(v => Math.round(toLinear(v / 255) * 10000) / 10000).concat([a ?? 1])
}

/** [r, g, b] (0-1, linear) of an FColor '#rrggbb' (sRGB). */
export function colourLinear (hex) {
  return [1, 3, 5].map(i => toLinear(parseInt(String(hex || '#ffffff').slice(i, i + 2), 16) / 255))
}

/** Unreal's forward (X axis) of a rotator [pitch, yaw, roll], in level space. */
export function forward (rot) {
  const p = (rot[0] || 0) * Math.PI / 180
  const y = (rot[1] || 0) * Math.PI / 180
  return [Math.cos(p) * Math.cos(y), Math.cos(p) * Math.sin(y), Math.sin(p)]
}

/** {pitch, yaw} (degrees, pitch -90..90, yaw -180..180) of a rotator's forward. */
export function pitchYaw (rot) {
  const d = forward(rot || [0, 0, 0])
  const round = (v) => Math.round(v * 100) / 100
  return { pitch: round(Math.asin(Math.max(-1, Math.min(1, d[2]))) * 180 / Math.PI),
           yaw: round(Math.atan2(d[1], d[0]) * 180 / Math.PI) }
}

/** The rotator pointing along level direction `d`, keeping `roll`. */
export function rotatorOf (d, roll) {
  const n = Math.hypot(d[0], d[1], d[2]) || 1
  const round = (v) => Math.round(v * 1000) / 1000
  return [round(Math.asin(d[2] / n) * 180 / Math.PI), round(Math.atan2(d[1], d[0]) * 180 / Math.PI),
    roll || 0]
}

/** Whether two plan values are the same (rotators by where they point). */
function same (a, b, prop) {
  if (a === undefined || b === undefined) return a === b
  if (prop === 'RelativeRotation' && Array.isArray(a) && Array.isArray(b)) {
    const [p, q] = [forward(a), forward(b)]
    return Math.acos(Math.min(1, p[0] * q[0] + p[1] * q[1] + p[2] * q[2])) * 180 / Math.PI < SAME_ANGLE
  }
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((v, i) => Math.abs(v - b[i]) < 1e-4)
  }
  if (typeof a === 'number' && typeof b === 'number') return Math.abs(a - b) < 1e-6
  if (typeof a === 'string' && typeof b === 'string') return a.toLowerCase() === b.toLowerCase()
  return a === b
}

/** An enum entry as the panel names it: 'EComponentMobility::Movable' -> 'Movable',
 * 'SLS_CapturedScene' -> 'Captured Scene'. */
export function enumLabel (value) {
  const bare = String(value).replace(/^[A-Za-z]+::/, '').replace(/^SLS_/, '')
  return bare.replace(/([a-z])([A-Z])/g, '$1 $2')
}

/** An engine property's name as the panel shows it: 'bOverride_FilmToe' -> 'Override
 * Film Toe'. */
export function propLabel (prop) {
  return String(prop).replace(/^b(?=[A-Z])/, '').replace(/_/g, ' ')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2').replace(/\s+/g, ' ').trim()
}

/** The kind of an engine property the plan stores without a field: from its value. */
function guessKind (value) {
  if (typeof value === 'boolean') return 'bool'
  if (typeof value === 'number') return 'float'
  if (typeof value === 'string') return value.startsWith('#') ? 'color' : 'enum'
  if (Array.isArray(value)) return value.length === 4 ? 'vector4' : 'vector'
  return 'unknown'
}

// -- targets ---------------------------------------------------------------------------
//
// A target is what one set of fields writes: {id, kind ('section', 'sun', 'light'),
// fields, base (shipped values), get () -> the plan's values or undefined,
// set (values | null)}. Several targets may share an id (several picked lights): a change
// is written to each.

/** The targets of the plan's lighting in Map Settings: each level-wide section the level
 * has, then each sun (the level's, less the removed ones, then the added). */
export function mapTargets (info, plan, kitSun) {
  const lit = info && info.lighting
  if (!lit) return []
  const fields = lit.fields || {}
  const out = []
  for (const sec of SECTIONS) {
    if (!(lit.sections || {})[sec]) continue
    out.push({ id: 'section:' + sec, kind: 'section', section: sec, title: lit.titles[sec],
      fields: fields[sec] || [], base: lit.sections[sec],
      get: () => ((plan.lighting || {})[sec]),
      set: (v) => {
        plan.lighting = { ...(plan.lighting || {}) }
        if (v) plan.lighting[sec] = v
        else delete plan.lighting[sec]
      } })
  }
  const gone = new Set(plan.removedFx || [])
  for (const sun of lit.suns || []) {
    if (gone.has(sun.key)) continue
    out.push({ id: 'sun:' + sun.key, kind: 'sun', key: sun.key, title: sun.name,
      fields: fields.sun || [], base: sun.values, ...lightsTarget(plan, sun.key) })
  }
  ;(plan.added || []).forEach((item, n) => {
    if (item.kind !== 'kit' || item.name !== SUN_CLASS) return
    out.push({ id: 'added:' + n, kind: 'sun', item, title: 'Added Sun ' + (n + 1),
      fields: fields.sun || [], base: kitSun || {}, ...addedTarget(item) })
  })
  return out
}

/** get/set of `plan.lights[key]`. */
export function lightsTarget (plan, key) {
  return {
    get: () => ((plan.lights || {})[key]),
    set: (v) => {
      plan.lights = { ...(plan.lights || {}) }
      if (v) plan.lights[key] = v
      else delete plan.lights[key]
      if (!Object.keys(plan.lights).length) delete plan.lights
    },
  }
}

/** get/set of an added item's `light`. */
export function addedTarget (item) {
  return {
    get: () => item.light,
    set: (v) => { if (v) item.light = v; else delete item.light },
  }
}

/** A target's values as the panel shows them: the shipped ones under the plan's. */
export function shown (target) {
  return { ...(target.base || {}), ...(target.get() || {}) }
}

/** Write `value` to `prop` of a target: the shipped value stores nothing. */
export function setProp (target, prop, value) {
  const now = { ...(target.get() || {}) }
  if (same(value, (target.base || {})[prop], prop)) delete now[prop]
  else now[prop] = value
  target.set(Object.keys(now).length ? now : null)
}

/** Take `prop` out of a target's plan values. */
export function resetProp (target, prop) {
  const now = { ...(target.get() || {}) }
  delete now[prop]
  target.set(Object.keys(now).length ? now : null)
}

// -- fields ----------------------------------------------------------------------------

/** One field's control. `t`: the target id; `f`: {prop, label, kind, min, max, step,
 * options, of, axis}; `value`: its shown value; `own`: whether the plan stores it. */
function control (t, f, value, extra = '') {
  const at = `data-lit-target="${esc(t)}" data-lit-prop="${esc(f.prop)}"${extra}`
  const range = f.min !== undefined ? ` min="${f.min}" max="${f.max}" step="${f.step}"` : ' step="any"'
  if (value === null || value === undefined) {
    return `<input type="text" disabled value="Default" ${at}>`
  }
  switch (f.kind) {
    case 'color':
      return `<input type="color" ${at} value="${esc(String(value).slice(0, 7))}">`
    case 'linear':
      return `<input type="color" ${at} value="${linearHex(value)}">`
    case 'bool':
      return `<input type="checkbox" class="switch" role="switch" ${at}${value ? ' checked' : ''}>`
    case 'vector4':
    case 'vector':
    case 'rotator':
      return `<div class="litvec">` + value.map((v, i) =>
        `<input type="number"${range} ${at} data-lit-i="${i}" value="${esc(v)}">`).join('') + '</div>'
    case 'int':
      return `<input type="number" step="1" ${at} value="${esc(value)}">`
    case 'float':
      return `<input type="number"${range} ${at} value="${esc(value)}">`
    default: {
      const opts = [...new Set([...(f.options || []), value])]
      return `<select ${at}>` + opts.map(o =>
        `<option value="${esc(o)}"${o === value ? ' selected' : ''}>${esc(enumLabel(o))}</option>`).join('') +
        '</select>'
    }
  }
}

/** One field with its label and, when the plan stores it, Reset. */
function fieldHtml (t, f, value, own) {
  const reset = own ? `<button type="button" class="litreset" data-lit-reset="${esc(t)}" ` +
    `data-lit-prop="${esc(f.of || f.prop)}">Reset</button>` : ''
  if (f.kind === 'bool') {
    return `<label class="row toggle litbool"><span>${esc(f.label)}${reset}</span>` +
      control(t, f, value) + '</label>'
  }
  return `<label class="pick"><span>${esc(f.label)}${reset}</span>${control(t, f, value)}</label>`
}

/** The fields of one target, laid out: colours on one row, numbers and choices two to a
 * row, vectors and switches on rows of their own; then any value the plan stores that
 * no field shows (an older plan's, lightmigrate). `lightKind` keeps the fields of one
 * kind of light (a spot light's cones). */
export function targetHtml (target, lightKind) {
  const t = target.id
  const values = shown(target)
  const own = target.get() || {}
  const fields = (target.fields || []).filter(f => !f.kinds || f.kinds.includes(lightKind))
  const shownOf = (f) => {
    if (f.of === 'RelativeRotation') {
      const py = pitchYaw(values.RelativeRotation)
      return f.axis === 0 ? py.pitch : py.yaw
    }
    return values[f.prop]
  }
  const isOwn = (f) => (f.of || f.prop) in own
  const colours = fields.filter(f => f.kind === 'color' || f.kind === 'linear')
  const singles = fields.filter(f => ['float', 'int', 'enum'].includes(f.kind) ||
    String(f.kind).startsWith('byte:') || String(f.kind).startsWith('enum:'))
  const vectors = fields.filter(f => ['vector4', 'vector', 'rotator'].includes(f.kind))
  const bools = fields.filter(f => f.kind === 'bool')
  const out = []
  const row = (list) => out.push(`<div class="pair" style="grid-template-columns: repeat(${list.length}, minmax(0, 1fr))">` +
    list.map(f => fieldHtml(t, f, shownOf(f), isOwn(f))).join('') + '</div>')
  if (colours.length) row(colours)
  for (let i = 0; i < singles.length; i += 2) row(singles.slice(i, i + 2))
  for (const f of vectors) out.push(fieldHtml(t, f, shownOf(f), isOwn(f)))
  for (const f of bools) out.push(fieldHtml(t, f, shownOf(f), isOwn(f)))
  const known = new Set(fields.map(f => f.of || f.prop))
  for (const prop of Object.keys(own).filter(p => !known.has(p)).sort()) {
    const f = { prop, label: propLabel(prop), kind: guessKind(own[prop] ?? (target.base || {})[prop]) }
    out.push(fieldHtml(t, f, own[prop], true))
  }
  return out.join('')
}

/** Map Settings' lighting sections. `selectedSun`: the id of the sun whose handle the
 * viewport shows; `afterSun`: what follows the Sun section (the Moon, moonsui.js). */
export function mapLightingHtml (targets, selectedSun, afterSun = '') {
  if (!targets.length) return ''
  const out = []
  const suns = targets.filter(t => t.kind === 'sun')
  out.push('<h2>Sun</h2>')
  for (const t of suns) {
    out.push(`<div class="litsun${t.id === selectedSun ? ' on' : ''}"><div class="sunhead">` +
      `<button type="button" class="sunpick${t.id === selectedSun ? ' on' : ''}" data-sun-pick="${esc(t.id)}">` +
      `${esc(t.title)}</button><button type="button" data-sun-del="${esc(t.id)}">Delete</button></div>` +
      targetHtml(t) + '</div>')
  }
  out.push('<button type="button" id="sunadd">Add Sun</button>')
  if (afterSun) out.push(afterSun)
  for (const t of targets.filter(x => x.kind === 'section')) {
    out.push(`<h2>${esc(t.title)}</h2>` + targetHtml(t))
  }
  return out.join('')
}

/** The value a control holds, as the plan stores it, or undefined when it holds none. */
function readControl (input, target, f) {
  const now = shown(target)
  const prop = input.dataset.litProp
  const kind = f ? f.kind : guessKind(now[prop])
  if (input.dataset.litI !== undefined) {
    const v = +input.value
    if (input.value.trim() === '' || !Number.isFinite(v)) return undefined
    const arr = (now[prop] || []).slice()
    arr[+input.dataset.litI] = v
    return arr
  }
  if (kind === 'bool') return !!input.checked
  if (kind === 'color') return input.value.toLowerCase()
  if (kind === 'linear') return hexLinear(input.value, (now[prop] || [])[3])
  if (kind === 'float' || kind === 'int') {
    const v = +input.value
    if (input.value.trim() === '' || !Number.isFinite(v)) return undefined
    const lo = f && f.min !== undefined ? f.min : -Infinity
    const hi = f && f.max !== undefined ? f.max : Infinity
    return Math.min(hi, Math.max(lo, kind === 'int' ? Math.round(v) : v))
  }
  return input.value
}

/** Wire the fields of `targets` in `body`. `change(fn)` runs `fn` (which edits the plan)
 * as one undoable change. */
export function wireTargets (body, targets, change) {
  const byId = (id) => targets.filter(t => t.id === id)
  for (const input of body.querySelectorAll('[data-lit-target][data-lit-prop]')) {
    if (input.tagName === 'BUTTON') continue
    input.addEventListener('change', () => {
      const group = byId(input.dataset.litTarget)
      if (!group.length) return
      const prop = input.dataset.litProp
      change(() => {
        for (const target of group) {
          const f = (target.fields || []).find(x => x.prop === prop)
          if (f && f.of === 'RelativeRotation') {
            const v = +input.value
            if (input.value.trim() === '' || !Number.isFinite(v)) continue
            const rot = (shown(target).RelativeRotation || [0, 0, 0])
            const py = pitchYaw(rot)
            if (f.axis === 0) py.pitch = Math.min(f.max, Math.max(f.min, v))
            else py.yaw = Math.min(f.max, Math.max(f.min, v))
            setProp(target, 'RelativeRotation', [py.pitch, py.yaw, rot[2] || 0])
            continue
          }
          const value = readControl(input, target, f)
          if (value !== undefined) setProp(target, prop, value)
        }
      })
    })
  }
  for (const b of body.querySelectorAll('[data-lit-reset]')) {
    b.addEventListener('click', (ev) => {
      ev.preventDefault()
      const group = byId(b.dataset.litReset)
      change(() => { for (const t of group) resetProp(t, b.dataset.litProp) })
    })
  }
}

/** Wire Map Settings' lighting: the fields, and the suns' Pick, Delete and Add Sun.
 * `on`: {change(fn), pickSun(id), deleteSun(id), addSun()}. */
export function wireMapLighting (body, targets, on) {
  wireTargets(body, targets, on.change)
  for (const b of body.querySelectorAll('[data-sun-pick]')) {
    b.addEventListener('click', () => on.pickSun(b.dataset.sunPick))
  }
  for (const b of body.querySelectorAll('[data-sun-del]')) {
    b.addEventListener('click', () => on.deleteSun(b.dataset.sunDel))
  }
  const add = body.querySelector('#sunadd')
  if (add) add.addEventListener('click', () => on.addSun())
}

// -- the Light section of picked lights --------------------------------------------------

/** The Light section for picked lights: `targets` all share the id 'light' and the
 * fields of the first; `mobility` lists the Mobility choices; `kind` is the first's
 * (point, spot, rect, sun). */
export function lightPanelHtml (targets, kind, mobility) {
  if (!targets.length) return ''
  const first = targets[0]
  const fields = (first.fields || []).map(f => f.prop === 'Mobility'
    ? { ...f, options: mobility } : f)
  const html = targetHtml({ ...first, fields }, kind)
  //: the colour keeps the id the Select All flow and the tests know it by
  return '<h2>Light</h2>' + html.replace(/<input type="color" data-lit-target="light" data-lit-prop="LightColor"/,
    '<input type="color" id="lightcolour" data-lit-target="light" data-lit-prop="LightColor"')
}

// -- the preview -----------------------------------------------------------------------

/** The lighting the viewport draws: {suns: [{id, dir (level forward), colour (linear),
 * intensity, shadows}], sky: {colour, lower (linear), intensity, lowerBlack} | null,
 * fog: {colour (linear), density, falloff, opacity, start, height} | null}; null without
 * the level's lighting. */
export function sceneLighting (info, plan, kitSun) {
  if (!info || !info.lighting) return null
  const targets = mapTargets(info, plan, kitSun)
  const suns = targets.filter(t => t.kind === 'sun').map(t => {
    const v = shown(t)
    return { id: t.id, dir: forward(v.RelativeRotation || [0, 0, 0]),
             colour: colourLinear(v.LightColor), intensity: +v.Intensity || 0,
             shadows: v.CastShadows !== false, visible: v.bVisible !== false }
  }).filter(s => s.visible)
  const sec = (name) => { const t = targets.find(x => x.id === 'section:' + name); return t ? shown(t) : null }
  const sky = sec('skyLight')
  const fog = sec('heightFog')
  return {
    suns,
    sky: sky && { colour: colourLinear(sky.LightColor), lower: (sky.LowerHemisphereColor || [0, 0, 0]).slice(0, 3),
                  intensity: +sky.Intensity || 0, lowerBlack: sky.bLowerHemisphereIsBlack !== false },
    fog: fog && { colour: (fog.FogInscatteringColor || [0.447, 0.638, 1]).slice(0, 3),
                  density: fog.FogDensity, falloff: fog.FogHeightFalloff, opacity: fog.FogMaxOpacity,
                  start: fog.StartDistance, height: fog.FogHeight },
  }
}

/** A level or added light's preview values with the plan's over them: {colour (linear),
 * brightness, radius}; `row` is leveldata.light_rig's or a catalogue row's light, `base`
 * the Light section's values it ships with, `own` the plan's. */
export function lampValues (row, base, own) {
  if (!own) return row
  const out = { ...row }
  if (own.LightColor) out.colour = colourLinear(own.LightColor)
  if (typeof own.AttenuationRadius === 'number') out.radius = own.AttenuationRadius
  const was = base && +base.Intensity
  if (typeof own.Intensity === 'number' && was > 0) out.brightness = (row.brightness || 0) * own.Intensity / was
  return out
}
