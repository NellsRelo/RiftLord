// Map Settings' Minimap section: Source Level (the default), Cut or Regenerate. The
// choice is kept in `plan.minimap`: absent for Source Level, {mode: 'cut'} or
// {mode: 'regenerate'}; older records' {cut} reads as Cut and {redraw} as Regenerate.
// Regenerate draws all reachable floor; its Cut at Doors switch ({cutAtDoors: true})
// stops the drawing at the doors.
// A scratch level always draws its own minimap: the section shows only its preview.
// riftlord/minimapredraw.py draws the preview (POST /api/minimap/<level>) and the build;
// a multi-floor level's preview is one picture per floor.

//: Preview image width in the panel, in CSS pixels.
const PREVIEW_WIDTH = 240
//: Wait this long after the last edit before asking for a new preview, in ms.
const PREVIEW_DELAY = 600
//: A preview with no picture after this long is shown as an error, in ms (the first
//: full drawing of a level with empty caches on a loaded machine: up to 3 minutes).
const PREVIEW_TIMEOUT = 300000
//: Spinner size and colour.
const SPINNER_SIZE = 12
const SPINNER_COLOUR = '#9fb4c8'

const esc = (text) => String(text ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/>/g, '&gt;').replace(/"/g, '&quot;')

/** True for every open level: shipped levels choose, scratch levels preview their own. */
export function minimapOffered (plan, level) {
  return !!(plan && level)
}

/** The plan's minimap choice: 'source', 'cut' or 'regenerate'. */
export function minimapMode (plan) {
  const m = (plan && plan.minimap) || {}
  if (m.mode === 'cut' || m.mode === 'regenerate' || m.mode === 'source') return m.mode
  return m.cut ? 'cut' : m.redraw ? 'regenerate' : 'source'
}

/** True when the section shows a preview (Cut, Regenerate, or any scratch level). */
export function minimapPreviewed (plan, level) {
  return !!(level && level.scratch) || minimapMode(plan) !== 'source'
}

const MODES = [
  ['source', 'Source Level'],
  ['cut', 'Cut'],
  ['regenerate', 'Regenerate'],
]

function spinner () {
  return `<span style="display:inline-block;width:${SPINNER_SIZE}px;height:${SPINNER_SIZE}px;` +
    `border:2px solid ${SPINNER_COLOUR};border-right-color:transparent;border-radius:50%;` +
    'vertical-align:middle;margin-right:6px;animation:mmspin 0.8s linear infinite"></span>' +
    '<style>@keyframes mmspin{to{transform:rotate(360deg)}}</style>'
}

function pictures (preview) {
  const list = preview.images && preview.images.length
    ? preview.images : [{ floor: 1, image: preview.image }]
  return list.map((p) => (list.length > 1
    ? `<div class="hint" style="margin-top:4px">Floor ${esc(p.floor)}</div>` : '') +
    `<img class="mmpreview" src="${p.image}" alt="minimap preview" ` +
    `style="width:${PREVIEW_WIDTH}px;max-width:100%;display:block;margin:6px 0;` +
    'background:#3e4f60;border-radius:4px">').join('')
}

/** The section's HTML. `preview` is {loading} | {error} | /api/minimap's answer | null. */
export function minimapHtml (plan, preview, level) {
  const scratch = !!(level && level.scratch)
  const mode = minimapMode(plan)
  const out = ['<h2>Minimap</h2>']
  if (!scratch) {
    out.push('<select id="mmmode">' + MODES.map(([v, label]) =>
      `<option value="${v}"${v === mode ? ' selected' : ''}>${label}</option>`).join('') +
      '</select>')
    if (mode === 'source') return out.join('')
    if (mode === 'regenerate') {
      const on = !!(plan.minimap && plan.minimap.cutAtDoors)
      out.push('<label class="row toggle"><span>Cut at Doors</span>' +
        `<input id="mmcutdoors" type="checkbox" class="switch" role="switch"${on ? ' checked' : ''}></label>`)
    }
  }
  if (!preview || preview.loading) {
    out.push(`<div class="hint">${spinner()}Drawing the preview…</div>`)
  } else if (preview.error) {
    out.push(`<div class="hint" style="color:#ffb03a">${esc(preview.error)}</div>`)
  } else {
    if (preview.refining) out.push(`<div class="hint">${spinner()}</div>`)
    out.push(pictures(preview))
  }
  return out.join('')
}

/** Wire the section. `onChange(block)` gets the new `plan.minimap` (null for Source
 * Level). An `area` and Cut at Doors the record carries are kept through a change to
 * Regenerate. */
export function wireMinimap (box, plan, onChange) {
  const mode = box.querySelector('#mmmode')
  if (!mode) return
  const regenerate = (cutAtDoors) => {
    const block = { mode: 'regenerate' }
    const area = plan.minimap && plan.minimap.area
    if (area) block.area = area
    if (cutAtDoors) block.cutAtDoors = true
    return block
  }
  mode.addEventListener('change', () => {
    if (mode.value === 'source') return onChange(null)
    if (mode.value === 'regenerate') {
      return onChange(regenerate(!!(plan.minimap && plan.minimap.cutAtDoors)))
    }
    onChange({ mode: mode.value })
  })
  const doors = box.querySelector('#mmcutdoors')
  if (doors) doors.addEventListener('change', () => onChange(regenerate(doors.checked)))
}

/** A cached preview fetcher: `get(level, plan)` returns the answer for this plan or
 * {loading} while it is drawn, and calls `onReady` when a new answer arrives. A request
 * waits PREVIEW_DELAY ms after the last change, so a burst of edits asks once. */
export function previewCache (fetchJson, onReady) {
  let key = ''
  let answer = null
  let timer = null
  return {
    get (levelName, plan) {
      const want = levelName + '\u0001' + JSON.stringify(plan)
      if (want === key) return answer
      key = want
      answer = { loading: true }
      if (timer) clearTimeout(timer)
      // a quick small drawing first (or the full one when the server keeps it), then
      // the full one; the panel shows whichever has arrived
      const ask = (quick) => fetchJson('api/minimap/' + encodeURIComponent(levelName), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Rift-Lord': '1' },
        body: JSON.stringify({ plan, quick }),
      }).then(got => got, err => ({ error: err.message || String(err) }))
        .then(got => {
          if (key !== want) return
          got = got || { error: 'The preview could not be drawn.' }
          if (quick && !got.error && got.final === false) {
            answer = Object.assign({ refining: true }, got)
            onReady()
            ask(false)
            return
          }
          answer = got
          onReady()
        })
      timer = setTimeout(() => {
        timer = null
        if (key !== want) return
        ask(true)
        // never spin for ever: an answer that does not come is shown as an error
        setTimeout(() => {
          if (key === want && answer && answer.loading) {
            answer = { error: 'The preview took too long to draw.' }
            onReady()
          }
        }, PREVIEW_TIMEOUT)
      }, PREVIEW_DELAY)
      return answer
    },
  }
}
