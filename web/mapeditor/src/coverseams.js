// Map Settings' Seams section: the Cover Seams command (riftlord/mapeditor/coverseams.py,
// POST /api/coverseams/<level>). It lists the open map's seams that a cover closes, each
// with Accept and Skip; picking one draws its cover in the viewport and looks at it, and
// Accept adds its meshes to the plan as ordinary added meshes. The seams no cover closes
// are under Not Placed, each with its reason; picking one looks at it.

const esc = (s) => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c])

//: Whether Not Placed is open; kept across redraws, closed when the editor starts.
let notPlacedOpen = false

/** A fresh state: nothing asked yet. */
export function seamState () {
  return { asked: false, busy: false, proposals: [], problems: [], skipped: new Set(),
           accepted: new Set(), picked: null, error: '' }
}

/** The proposals still open: neither accepted nor skipped. */
export function openProposals (state) {
  return state.proposals.filter(p => !state.skipped.has(p.id) && !state.accepted.has(p.id))
}

/** A seam's row text: its kind and its opening's width. */
export function seamText (seam) {
  return `${seam.label} · ${Math.round(seam.width)} uu`
}

/** One proposal's cover text: its mesh and how many pieces. */
export function coverText (p) {
  return `${p.cover.mesh} ×${p.items.length}`
}

/** The proposal or not-placed seam with id `id`, or null. */
export function seamById (state, id) {
  return openProposals(state).find(q => q.id === id) || state.problems.find(q => q.id === id) || null
}

/** The Seams section's HTML. */
export function seamsHtml (state) {
  const out = ['<h2>Seams</h2>']
  out.push(`<button type="button" id="seamfind"${state.busy ? ' disabled' : ''}>` +
    `${state.busy ? 'Finding Seams…' : 'Cover Seams'}</button>`)
  if (state.error) out.push(`<div class="seamcount bad">${esc(state.error)}</div>`)
  if (!state.asked || state.busy) return out.join('')
  const open = openProposals(state)
  out.push(`<div class="seamcount">${open.length} to cover · ${state.accepted.size} accepted · ` +
    `${state.problems.length} not placed</div>`)
  if (open.length > 1) out.push('<button type="button" id="seamall">Accept All</button>')
  for (const p of open) {
    const on = state.picked === p.id
    out.push(`<div class="litsun seamrow${on ? ' on' : ''}" data-seam="${esc(p.id)}"><div class="sunhead">` +
      `<button type="button" class="sunpick${on ? ' on' : ''}" data-seam-pick="${esc(p.id)}">` +
      `${esc(seamText(p.seam))}</button>` +
      `<button type="button" data-seam-accept="${esc(p.id)}">Accept</button>` +
      `<button type="button" data-seam-skip="${esc(p.id)}">Skip</button></div>` +
      (on ? `<div class="meta">${esc(coverText(p))}</div>` : '') + '</div>')
  }
  if (state.problems.length) {
    out.push(`<details id="seamsno"${notPlacedOpen ? ' open' : ''}><summary>Not Placed</summary>`)
    for (const q of state.problems) {
      const on = state.picked === q.id
      out.push(`<div class="litsun seamno${on ? ' on' : ''}"><div class="sunhead">` +
        `<button type="button" class="sunpick${on ? ' on' : ''}" data-seam-pick="${esc(q.id)}">` +
        `${esc(seamText(q.seam))}</button></div><div class="meta bad">${esc(q.why)}</div></div>`)
    }
    out.push('</details>')
  }
  return out.join('')
}

/** Wire the section's buttons to `api`: {find(), pick(id), accept([ids]), skip(id)}. */
export function wireSeams (box, state, api) {
  const on = (sel, fn) => box.querySelectorAll(sel).forEach(b => b.addEventListener('click', fn))
  on('#seamfind', () => api.find())
  on('#seamall', () => api.accept(openProposals(state).map(p => p.id)))
  on('[data-seam-pick]', (e) => api.pick(e.currentTarget.dataset.seamPick))
  on('[data-seam-accept]', (e) => api.accept([e.currentTarget.dataset.seamAccept]))
  on('[data-seam-skip]', (e) => api.skip(e.currentTarget.dataset.seamSkip))
  const no = box.querySelector('#seamsno')
  if (no) no.addEventListener('toggle', () => { notPlacedOpen = no.open })
}

/** Take a /api/coverseams answer into `state`. */
export function takeAnswer (state, got) {
  state.asked = true
  state.busy = false
  state.error = ''
  state.proposals = got.proposals || []
  state.problems = got.problems || []
  state.skipped = new Set()
  state.accepted = new Set()
  state.picked = null
}

/** The plan entries accepting proposals `ids` adds, each a fresh copy. */
export function acceptedItems (state, ids) {
  const out = []
  for (const id of ids) {
    const p = state.proposals.find(q => q.id === id)
    if (!p || state.accepted.has(id) || state.skipped.has(id)) continue
    for (const item of p.items) out.push(JSON.parse(JSON.stringify(item)))
    state.accepted.add(id)
  }
  if (ids.includes(state.picked)) state.picked = null
  return out
}
