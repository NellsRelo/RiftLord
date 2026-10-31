// Map Settings' Weather section, and the level's own particle effects by system.
//
// Weather is rain or snow over the level's play area as ordinary effect items
// (riftlord/weather.py: POST /api/weather/suggest/<level>), edited like any placed effect.
// The level's own weather effects are taken out through the plan's `removedFx`
// ('<sublevel>|<actor export>'; the build parks those actors, riftlord/apply.py removed_fx).

const esc = (text) => String(text ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/>/g, '&gt;').replace(/"/g, '&quot;')

//: The group a surveyed effect is listed under when nothing says otherwise.
const OTHER = 'Other'
//: Effect Blueprints whose particles are the actor's whole point (riftlord/fxsurvey.py
//: FX_BLUEPRINT_WORDS). Any other Blueprint with a particle system is a trap, a door or
//: a character, which taking out "the effect" would take out whole.
const FX_BLUEPRINT = /(Burst|FX|Fx|VFX|Smoke|Fire|Torch|Rain|Lightning|Waterfall|Fog|Steam)/
//: The weather group (riftlord/fxsurvey.py GROUPS).
export const WEATHER_GROUP = 'Weather'
//: The Weather value of a level's own weather that no preset makes.
const OWN_PREFIX = 'own:'
//: The Weather value of more than one kind at once.
const MIXED = 'mixed'

/** Whether one of the level's own effects may be taken out on its own. */
export function removable (f) {
  const cls = f.actorClass || ''
  return !!f.actor && (cls === 'Emitter' || cls === 'NiagaraActor' || cls === 'AmbientSound' ||
    (/_C$/.test(cls) && FX_BLUEPRINT.test(cls)))
}

/** [{template, name, label, group, keys, gone}] of the level's removable effects, by
 * system; `name` is the catalogue's effect key. */
export function levelEffects (level, plan, catalogue) {
  const gone = new Set(plan.removedFx || [])
  const byTemplate = new Map()
  for (const f of (level && level.vfx) || []) {
    if (!removable(f)) continue
    const key = `${f.sub || 'persistent'}|${f.actor}`
    if (!byTemplate.has(f.template)) {
      const row = catalogue.find(r => r.kind === 'fx' && r.template === f.template)
      byTemplate.set(f.template, { template: f.template, name: row ? row.name : null,
        label: row ? row.label : f.template, group: row ? row.group || OTHER : OTHER,
        keys: [], gone: 0 })
    }
    const rec = byTemplate.get(f.template)
    if (!rec.keys.includes(key)) {
      rec.keys.push(key)
      if (gone.has(key)) rec.gone += 1
    }
  }
  return [...byTemplate.values()].sort((a, b) =>
    a.group.localeCompare(b.group) || a.label.localeCompare(b.label))
}

/** Whether a plan `added` item is a weather effect. */
export const isWeatherItem = (a, catalogue) => a.kind === 'fx' &&
  (catalogue.find(r => r.kind === 'fx' && r.name === a.fx) || {}).group === WEATHER_GROUP

/** What the weather is now: {value, own: [{kind, label, keys, live}], placed} where
 * `value` is a preset kind, OWN_PREFIX + label, MIXED or '' (none). */
function weatherNow (level, plan, catalogue, weather) {
  const kindOf = (name) => (weather.find(w => (w.effects || []).includes(name)) || {}).kind
  const own = levelEffects(level, plan, catalogue).filter(r => r.group === WEATHER_GROUP)
    .map(r => ({ kind: kindOf(r.name) || OWN_PREFIX + r.label, label: r.label, keys: r.keys,
                 live: r.keys.length - r.gone }))
  const placed = (plan.added || []).filter(a => isWeatherItem(a, catalogue))
  const kinds = new Set(own.filter(r => r.live > 0).map(r => r.kind))
  for (const a of placed) kinds.add(kindOf(a.fx) || OWN_PREFIX + a.fx)
  const value = kinds.size > 1 ? MIXED : kinds.size ? [...kinds][0] : ''
  return { value, own, placed }
}

/** The Weather section's HTML. `weather` is /api/weather ([{kind, label, effects}]). */
export function levelFxHtml (level, plan, catalogue, weather) {
  if (!(weather || []).length) return ''
  const now = weatherNow(level, plan, catalogue, weather)
  const opt = (value, label) =>
    `<option value="${esc(value)}"${now.value === value ? ' selected' : ''}>${esc(label)}</option>`
  const extra = []
  if (now.value === MIXED) extra.push(opt(MIXED, 'Mixed'))
  if (now.value.startsWith(OWN_PREFIX)) extra.push(opt(now.value, now.value.slice(OWN_PREFIX.length)))
  return '<h2>Weather</h2><label class="pick"><span>Weather</span><select id="weather">' +
    opt('', 'None') + weather.map(w => opt(w.kind, w.label)).join('') + extra.join('') +
    '</select></label>'
}

/** Wire the section. `onWeather({kind, keep, drop})`: take out every placed weather item,
 * put back the level's own weather effects `keep` and take out `drop` (plan `removedFx`
 * keys), then place preset `kind` over the play area when it is not null. */
export function wireLevelFx (box, { level, plan, catalogue, weather, onWeather }) {
  const sel = box.querySelector('#weather')
  if (!sel) return
  sel.addEventListener('change', () => {
    const now = weatherNow(level, plan, catalogue, weather || [])
    const want = sel.value
    if (want === MIXED || want.startsWith(OWN_PREFIX)) return
    const mine = now.own.filter(r => want && r.kind === want)
    const keep = mine.flatMap(r => r.keys)
    const drop = now.own.filter(r => !mine.includes(r)).flatMap(r => r.keys)
    sel.disabled = true
    onWeather({ kind: want && !mine.length ? want : null, keep, drop })
  })
}
