// The Mission Events list: the plan's `events` (riftlord/hostevents.py clean), edited
// lightly here -- a music change (wave, track), a boss (wave, unit, door, reaction), an
// unstable rift opening or closing (wave, rift) -- while the Onslaught Editor's Timeline
// keeps the rest of a boss row and the dialogue lines (only counted here). `choices` is /api/events/<level> (missionevents.
// editor_choices): what the host can do itself ('host'), what it can with the Mission
// Events piece ('piece'), the music tracks and the bosses.

const esc = (text) => String(text ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/>/g, '&gt;').replace(/"/g, '&quot;')

//: The event kinds, with their row labels and Add buttons.
export const EVENT_KINDS = [['music', 'Music Change'], ['boss', 'Boss'], ['rift', 'Unstable Rift']]
//: Waves an event may name (riftlord/hostevents.py MAX_WAVE).
const MAX_WAVE = 99
//: The wave a new event starts on.
const FIRST_WAVE = 1

/** The reaction lines for a boss, the host's or the piece's, or null. */
function reactionFor (unit, choices) {
  for (const side of ['host', 'piece']) {
    const hit = ((choices && choices[side] && choices[side].reactions) || [])
      .find(r => r.unit === unit)
    if (hit) return hit
  }
  return null
}

/** Unstable rifts an event may name: the host's own and the plan's. */
export function riftChoices (choices) {
  return ((choices && choices.piece && choices.piece.rift && choices.piece.rift.rifts) || [])
}

/** A new event of `kind`. `spawners` is [{index, label}]. */
export function newEvent (kind, choices, spawners) {
  if (kind === 'music') {
    const host = (choices && choices.host && choices.host.music) || {}
    const first = ((choices && choices.tracks) || [])[0]
    return { kind, wave: FIRST_WAVE, track: host.change || (first && first.key) || 'Battle_01' }
  }
  if (kind === 'rift') {
    return { kind, wave: FIRST_WAVE, rift: riftChoices(choices)[0] || 1, open: true }
  }
  const bosses = (choices && choices.bosses) || []
  const unit = (bosses.find(b => reactionFor(b.name, choices)) || bosses[0] || {}).name || ''
  return { kind: 'boss', wave: FIRST_WAVE, unit, door: (spawners[0] || {}).index || 1,
           count: 1, reaction: !!reactionFor(unit, choices) }
}

/** Whether the plan's events need the Mission Events piece: an event the host cannot do
 * itself (riftlord/missionevents.py duties). */
export function needsPiece (events, choices) {
  const host = (choices && choices.host) || {}
  const native = new Set((host.reactions || []).map(r => r.unit))
  const hostRifts = new Set(((host.rift || {}).rifts) || [])
  return (events || []).some(ev => ev.kind === 'dialogue' ||
    (ev.kind === 'music' && !(host.music || {}).available) ||
    (ev.kind === 'boss' && ev.reaction && !native.has(ev.unit)) ||
    (ev.kind === 'rift' && !hostRifts.has(ev.rift)))
}

const options = (list, current) => list.map(([value, label]) =>
  `<option value="${esc(value)}"${String(value) === String(current) ? ' selected' : ''}>` +
  `${esc(label)}</option>`).join('')

/** The list's HTML. `spawners` is [{index, label}] for a boss's door. */
export function eventsHtml (events, choices, spawners) {
  if (!choices) return '<h2>Mission Events</h2><div class="hint">Reading the level…</div>'
  const tracks = (choices.tracks || []).map(t => [t.key, t.label])
  const bosses = (choices.bosses || []).map(b => [b.name, b.label])
  const rifts = riftChoices(choices).map(r => [r, 'Rift ' + r])
  const doors = spawners.map(s => [s.index, s.label])
  //: dialogue lines are edited on the Onslaught Editor's Timeline; here they are counted
  const lines = (events || []).filter(ev => ev.kind === 'dialogue').length
  const rows = (events || []).map((ev, i) => {
    if (ev.kind === 'dialogue') return ''
    const head = (EVENT_KINDS.find(([k]) => k === ev.kind) || [, ev.kind])[1]
    let fields = ''
    if (ev.kind === 'music') {
      fields = `<label class="pick"><span>Track</span><select data-ev="${i}" data-field="track">` +
        options(tracks.some(([k]) => k === ev.track) ? tracks : [[ev.track, ev.track], ...tracks],
                ev.track) + '</select></label>'
    } else if (ev.kind === 'boss') {
      const react = reactionFor(ev.unit, choices)
      fields = `<label class="pick"><span>Boss</span><select data-ev="${i}" data-field="unit">` +
        options(bosses.some(([k]) => k === ev.unit) ? bosses : [[ev.unit, ev.unit], ...bosses],
                ev.unit) + '</select></label>' +
        `<label class="pick"><span>Spawner</span><select data-ev="${i}" data-field="door">` +
        options(doors.some(([k]) => +k === +ev.door) ? doors
          : [[ev.door, 'Spawner ' + ev.door], ...doors], ev.door) + '</select></label>' +
        '<label class="row"><span>Reaction</span>' +
        `<input type="checkbox" data-ev="${i}" data-field="reaction"` +
        `${ev.reaction ? ' checked' : ''}${react ? '' : ' disabled'}></label>`
    } else if (ev.kind === 'rift') {
      fields = `<label class="pick"><span>Rift</span><select data-ev="${i}" data-field="rift">` +
        options(rifts.some(([k]) => +k === +ev.rift) ? rifts : [[ev.rift, 'Rift ' + ev.rift], ...rifts],
                ev.rift) + '</select></label>' +
        `<label class="pick"><span>Does</span><select data-ev="${i}" data-field="open">` +
        options([['1', 'Opens'], ['0', 'Closes']], ev.open ? '1' : '0') + '</select></label>'
    }
    return `<div class="card evrow" data-evrow="${i}"><div class="row"><b>${esc(head)}</b>` +
      `<button class="iconlink" data-evremove="${i}">Remove</button></div>` +
      `<div class="field"><label>Wave</label><input type="number" min="1" max="${MAX_WAVE}" ` +
      `step="1" data-ev="${i}" data-field="wave" value="${ev.wave}"></div>` + fields + '</div>'
  })
  const adds = EVENT_KINDS.filter(([k]) => k !== 'rift' || rifts.length)
    .map(([k, label]) => `<button data-evadd="${k}">Add ${label}</button>`).join('')
  const said = lines ? `<div class="card evrow" data-evdialogue="${lines}"><div class="row">` +
    `<b>Dialogue</b><span>${lines} line${lines === 1 ? '' : 's'}</span></div></div>` : ''
  return '<h2>Mission Events</h2>' + rows.join('') + said + `<div class="grid2">${adds}</div>`
}

/** Wire the list. `get()` returns the events, `set(list)` stores a changed copy. */
export function wireEvents (box, { get, set, choices, spawners }) {
  const copy = () => (get() || []).map(ev => ({ ...ev }))
  for (const b of box.querySelectorAll('[data-evadd]')) {
    b.addEventListener('click', () => set(copy().concat([newEvent(b.dataset.evadd, choices, spawners)])))
  }
  for (const b of box.querySelectorAll('[data-evremove]')) {
    b.addEventListener('click', () => set(copy().filter((_ev, i) => i !== +b.dataset.evremove)))
  }
  for (const input of box.querySelectorAll('[data-ev][data-field]')) {
    input.addEventListener('change', () => {
      const list = copy()
      const ev = list[+input.dataset.ev]
      if (!ev) return
      const f = input.dataset.field
      if (f === 'wave') ev.wave = Math.max(1, Math.min(MAX_WAVE, Math.round(+input.value || 1)))
      else if (f === 'door' || f === 'rift') ev[f] = +input.value
      else if (f === 'open') ev.open = input.value === '1'
      else if (f === 'reaction') ev.reaction = input.checked
      else ev[f] = input.value
      //: a boss picked: its reaction follows whether it has lines here
      if (f === 'unit') ev.reaction = !!reactionFor(ev.unit, choices)
      set(list)
    })
  }
}
