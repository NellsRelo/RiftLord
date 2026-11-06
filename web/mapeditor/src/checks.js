// The Checks list under Save Mission (riftlord/mapeditor/checks.py, POST /api/checks/<level>):
// what the open plan needs changed before it builds, what the last build of its mission
// stopped on, what is worth a look, and what a build fixes by itself. A row names the piece,
// route point or spawner; clicking it selects and frames that thing (main.js focusCheck).

const esc = (s) => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c])

//: Row levels in the order shown, with each one's colour.
export const LEVELS = [
  ['error', '#ff6b6b'],
  ['build', '#ff9a4a'],
  ['warning', '#ffc247'],
  ['fixed', '#8fd18f'],
]

/** The rows to show, in LEVELS order, each with its index in `rows`. */
export function ordered (rows) {
  const rank = (r) => {
    const i = LEVELS.findIndex(([l]) => l === r.level)
    return i < 0 ? LEVELS.length : i
  }
  return (rows || []).map((r, i) => ({ ...r, n: i })).sort((a, b) => rank(a) - rank(b) || a.n - b.n)
}

/** The Checks section's HTML: empty when there is nothing to show. */
export function checksHtml (rows) {
  const list = ordered(rows)
  if (!list.length) return ''
  const colour = Object.fromEntries(LEVELS)
  return '<h2>Checks</h2>' + list.map(r =>
    `<button class="checkrow" data-check="${r.n}" style="border-left:3px solid ${colour[r.level] || '#888'}"` +
    `${r.target || r.at ? '' : ' data-still="1"'}>${esc(r.text)}</button>`).join('')
}

/** Call `focus(row)` when a row is clicked. */
export function wireChecks (box, rows, focus) {
  for (const b of box.querySelectorAll('[data-check]')) {
    b.addEventListener('click', () => focus(rows[+b.dataset.check]))
  }
}

/** What a row points at, in main.js's terms: {plan: {kind, id}} for the plan's spawners,
 * points, rifts and doors; {wiring: name} for the level's own; {added: n} for an added
 * piece; {at} alone for a position; null for nothing. */
export function targetOf (row) {
  const t = (row && row.target) || {}
  if (t.kind && t.id !== undefined && t.id !== null) return { plan: { kind: t.kind, id: t.id }, at: row.at || null }
  if (t.name) return { wiring: t.name, at: row.at || null }
  if (Number.isInteger(t.added)) return { added: t.added, at: row.at || null }
  if (row && row.at) return { at: row.at }
  return null
}
