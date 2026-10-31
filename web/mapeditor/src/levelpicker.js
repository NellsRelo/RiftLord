// The Level picker: the `<select id="level">` drawn as a button and a list whose
// categories (the select's optgroups) open and close.
//
// The select stays the value: main.js reads and sets `select.value` and listens for its
// `change`; a pick here sets it and fires `change`. Categories remember being closed
// (localStorage, per viewer).

//: localStorage key of the closed categories.
const CLOSED_KEY = 'riftlord.levelpicker.closed'
const CARET = { open: '▾', closed: '▸' }

const esc = (text) => String(text ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/>/g, '&gt;').replace(/"/g, '&quot;')

function readClosed () {
  try { return new Set(JSON.parse(localStorage.getItem(CLOSED_KEY) || '[]')) } catch { return new Set() }
}

function writeClosed (closed) {
  try { localStorage.setItem(CLOSED_KEY, JSON.stringify([...closed])) } catch { /* no storage */ }
}

/** Draw `select` as the picker, in `row` (the element the list opens under). */
export function levelPicker (select, row) {
  const closed = readClosed()
  const button = document.createElement('button')
  button.type = 'button'
  button.className = 'lp-btn'
  button.setAttribute('aria-haspopup', 'listbox')
  const pop = document.createElement('div')
  pop.className = 'lp-pop'
  pop.hidden = true
  pop.setAttribute('role', 'listbox')
  select.hidden = true
  select.insertAdjacentElement('afterend', button)
  row.appendChild(pop)

  const proto = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')
  Object.defineProperty(select, 'value', {
    configurable: true,
    get () { return proto.get.call(select) },
    set (v) { proto.set.call(select, v); paint() },
  })

  function paint () {
    const o = select.selectedOptions[0] || select.options[0]
    button.innerHTML = `<span class="lp-label">${esc(o ? o.textContent : '')}</span>` +
      `<span class="lp-caret">${CARET.open}</span>`
    button.title = (o && o.title) || ''
    button.disabled = select.disabled
    button.classList.toggle('lp-empty', !select.value)
  }

  function fill () {
    const now = proto.get.call(select)
    const out = []
    for (const g of select.querySelectorAll('optgroup')) {
      const opts = [...g.querySelectorAll('option')]
      if (!opts.length) continue
      const shut = closed.has(g.label)
      out.push(`<div class="lp-group"><button type="button" class="lp-head" ` +
        `data-group="${esc(g.label)}" aria-expanded="${!shut}">` +
        `<span>${shut ? CARET.closed : CARET.open}</span>${esc(g.label)}` +
        `<span class="lp-count">${opts.length}</span></button>`)
      if (!shut) {
        out.push(opts.map(o => `<button type="button" class="lp-item${o.value === now ? ' on' : ''}" ` +
          `role="option" data-value="${esc(o.value)}"${o.disabled ? ' disabled' : ''}` +
          `${o.title ? ` title="${esc(o.title)}"` : ''}>${esc(o.textContent)}</button>`).join(''))
      }
      out.push('</div>')
    }
    pop.innerHTML = out.join('')
  }

  function open (on) {
    pop.hidden = !on
    if (on) {
      fill()
      const hit = pop.querySelector('.lp-item.on')
      if (hit) hit.scrollIntoView({ block: 'nearest' })
    }
  }

  button.addEventListener('click', () => open(pop.hidden))
  pop.addEventListener('click', (ev) => {
    const head = ev.target.closest('.lp-head')
    if (head) {
      const g = head.dataset.group
      if (closed.has(g)) closed.delete(g)
      else closed.add(g)
      writeClosed(closed)
      fill()
      return
    }
    const item = ev.target.closest('.lp-item')
    if (!item || item.disabled) return
    select.value = item.dataset.value
    select.dispatchEvent(new Event('change', { bubbles: true }))
    open(false)
  })
  document.addEventListener('pointerdown', (ev) => {
    if (!pop.hidden && !pop.contains(ev.target) && !button.contains(ev.target)) open(false)
  })
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && !pop.hidden) open(false)
  })
  new MutationObserver(() => { paint(); if (!pop.hidden) fill() })
    .observe(select, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled'] })
  select.addEventListener('change', paint)
  paint()
}
