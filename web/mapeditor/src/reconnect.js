// The Routes section: one row per spawner route (riftlord's plan after its edits), and
// Reconnect Routes: after the rift (or the exit point) has moved, find the route steps
// that now lead away from it and send them to the rift instead.
//
// A step P -> T "leads away" when T is more than AWAY_THRESHOLD farther from the rift
// than P is (horizontal distance), and that was not already so with the rift where the
// level ships it. An exit point left more than EXIT_NEAR_RIFT from a moved rift is
// moved beside it, keeping its shipped offset from the rift.

import { exitName, nextOf, isGone, wiringOf, movesExit, riftExits, riftTarget, riftsReached,
         pathGraph } from './actors.js'

//: How much farther from the rift a route step may end than it starts, in Unreal units.
export const AWAY_THRESHOLD = 300
//: How far the rift or exit point must stand from its shipped place to count as moved,
//: in Unreal units.
export const MOVED_MIN = 100
//: How far the exit point may stand from the rift before it is moved beside it, in
//: Unreal units.
export const EXIT_NEAR_RIFT = 600

const flat = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1])

/** Where one of the level's own actors stands after the plan's moves. */
function levelAt (plan, name, shipped) {
  const w = wiringOf(plan, name)
  return (w && w.at) || shipped || null
}

/** {now, shipped} of the rift routes end at, or null when the level has none. */
export function riftPlaces (plan, level) {
  const own = ((level && level.rifts) || [])[0]
  const exit = exitName(level)
  const exitShipped = exit ? level.points[exit].location : null
  const shipped = (own && own.loc) || exitShipped
  if (!shipped) return null
  //: the level's rift while it is in play; else the placed rift standing in for it
  if ((plan.rifts || []).length && !(own && !isGone(plan, own.name))) {
    return { now: plan.rifts[0].at, shipped }
  }
  return { now: own ? levelAt(plan, own.name, own.loc) : shipped, shipped }
}

/** What Reconnect Routes would change, or null when neither the rift nor the exit
 * point has moved: {exit: {name, from, to} | null, steps: [{ref, label, to, toLabel,
 * by}]}. */
export function proposal (plan, level) {
  const rift = riftPlaces(plan, level)
  const exit = exitName(level)
  if (!rift || !exit) return null
  const exitShipped = level.points[exit].location
  const exitGone = isGone(plan, exit)
  const exitNow = exitGone && movesExit(plan, level)
    ? plan.rifts[0].at : levelAt(plan, exit, exitShipped)
  const moved = flat(rift.now, rift.shipped) > MOVED_MIN ||
    flat(exitNow, exitShipped) > MOVED_MIN
  if (!moved) return null
  let exitMove = null
  let exitTo = exitNow
  if (!exitGone && flat(exitNow, rift.now) > EXIT_NEAR_RIFT) {
    exitTo = [0, 1, 2].map(k => Math.round(
      (rift.now[k] + exitShipped[k] - rift.shipped[k]) * 100) / 100)
    exitMove = { name: exit, from: exitNow, to: exitTo }
  }
  const at = (ref) => {
    if (ref === 'rift') return exitTo
    if (typeof ref === 'number') {
      const p = (plan.points || []).find(o => o.id === ref)
      return p ? p.at : null
    }
    if (ref === exit) return exitTo
    const p = ((level && level.points) || {})[ref]
    return p && !isGone(plan, ref) ? levelAt(plan, ref, p.location) : null
  }
  const label = (ref) => ref === 'rift' || ref === exit ? 'the rift'
    : typeof ref === 'number' ? 'point ' + ref : ref
  const away = (from, to, centre) => flat(to, centre) - flat(from, centre)
  const steps = []
  const refs = (plan.points || []).map(p => p.id).concat(
    Object.keys((level && level.points) || {}).filter(n => n !== exit && !isGone(plan, n)))
  for (const ref of refs) {
    const from = at(ref)
    if (!from) continue
    const next = nextOf(plan, level, ref)
    for (const b of next.length ? next : [{ to: 'rift', weight: 1 }]) {
      const to = at(b.to)
      if (!to) continue
      const by = away(from, to, rift.now)
      if (by > AWAY_THRESHOLD && away(from, to, rift.shipped) <= AWAY_THRESHOLD) {
        steps.push({ ref, label: label(ref), to: b.to, toLabel: label(b.to),
                     by: Math.round(by) })
      }
    }
  }
  return { exit: exitMove, steps }
}

/** Apply `prop` to the plan: move the exit point, and send each chosen step to the
 * rift (keeping its weight, merged with any branch that already goes there). */
export function applyProposal (plan, level, prop, chosen) {
  const keep = (s) => !chosen || chosen.has(stepKey(s))
  const entry = (name, kind) => {
    plan.wiring = plan.wiring || []
    let w = plan.wiring.find(o => o.name === name)
    if (!w) { w = { name, kind }; plan.wiring.push(w) }
    return w
  }
  if (prop.exit && (!chosen || chosen.has('exit'))) {
    entry(prop.exit.name, 'point').at = prop.exit.to.slice()
  }
  const byRef = new Map()
  for (const s of prop.steps.filter(keep)) {
    if (!byRef.has(s.ref)) byRef.set(s.ref, new Set())
    byRef.get(s.ref).add(s.to)
  }
  for (const [ref, targets] of byRef) {
    const now = nextOf(plan, level, ref)
    const list = (now.length ? now : [{ to: 'rift', weight: 1 }]).map(b => ({ ...b }))
    const out = []
    for (const b of list) {
      const to = targets.has(b.to) ? 'rift' : b.to
      const hit = out.find(o => o.to === to)
      if (hit) hit.weight = Math.round((hit.weight + b.weight) * 100) / 100
      else out.push({ ...b, to })
    }
    if (typeof ref === 'number') {
      const p = (plan.points || []).find(o => o.id === ref)
      if (p) p.next = out
    } else {
      entry(ref, 'point').next = out
    }
  }
}

/** A step's checkbox key. */
export const stepKey = (s) => `${s.ref}\u0001${s.to}`

const esc = (text) => String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;')

//: The most route steps followed from one spawner (a guard against loops).
const MAX_ROUTE_STEPS = 500
//: Joins the step keys a route's checkbox stands for.
const KEY_JOIN = '\u0002'

/** Where a Ref stands after the plan's moves, or null. */
function locate (plan, level, ref) {
  if (ref === 'rift') {
    const r = riftPlaces(plan, level)
    return r ? r.now : null
  }
  if (typeof ref === 'number') {
    const p = (plan.points || []).find(o => o.id === ref)
    return p ? p.at : null
  }
  const p = ((level && level.points) || {})[ref]
  return p && !isGone(plan, ref) ? levelAt(plan, ref, p.location) : null
}

const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2])
const leaf = (path) => (path ? String(path).split('.').pop() : null)

/** [{key, name, kind, door, start, points, length, reaches, refs, ends}]: one row per
 * spawner route (ground and flyer), after the plan's edits. `length` follows the likeliest
 * branch from the spawner to a rift; `points` counts every point the route can reach;
 * `ends` names the rifts it can end at when the plan places rifts with exits of their own. */
export function routes (plan, level) {
  const exit = exitName(level)
  const own = riftExits(plan, level)
  const target = riftTarget(plan, level)
  const reached = own.size ? riftsReached(plan, level) : null
  const { key: nodeKey } = pathGraph(plan, level)
  const out = []
  const doorName = (d) => typeof d === 'number' ? 'Door ' + d : leaf(d)
  const walk = (key, name, kind, from, start, door) => {
    if (start === null || start === undefined) return
    const refs = new Set()
    const todo = [start]
    while (todo.length && refs.size < MAX_ROUTE_STEPS) {
      const ref = todo.pop()
      if (ref === 'rift' || refs.has(ref) || !locate(plan, level, ref)) continue
      refs.add(ref)
      if (ref === exit || own.has(ref)) continue
      for (const b of nextOf(plan, level, ref)) todo.push(b.to)
    }
    let length = 0
    let at = from
    let ref = start
    let reaches = false
    const seen = new Set()
    for (let i = 0; i < MAX_ROUTE_STEPS; i++) {
      const to = locate(plan, level, ref)
      if (!to || seen.has(ref)) break
      seen.add(ref)
      if (at) length += dist(at, to)
      at = to
      if (ref === 'rift' || ref === exit || own.has(ref)) { reaches = true; break }
      const next = nextOf(plan, level, ref)
      if (!next.length) {
        //: a point with no branches goes on to the exit (the emitter adds the branch)
        ref = target ?? exit ?? 'rift'
        continue
      }
      ref = next.reduce((a, b) => (b.weight > a.weight ? b : a)).to
    }
    refs.delete(exit)
    for (const pid of own.keys()) refs.delete(pid)
    const ends = reached
      ? (reached.get(nodeKey(start)) || []).map(r => r === 'level' ? 'Original Rift' : 'Rift ' + r)
      : null
    out.push({ key, name, kind, door: doorName(door), start, points: refs.size,
               length: Math.round(length), reaches, refs, ends })
  }
  for (const s of (level && level.spawners) || []) {
    const w = wiringOf(plan, s.export) || {}
    if (w.gone) continue
    const from = w.at || s.location
    const door = 'door' in w ? w.door : s.door
    walk('l' + s.export + ':g', s.export, 'Ground', from, 'start' in w ? w.start : leaf(s.start), door)
    walk('l' + s.export + ':f', s.export, 'Flyer', from, 'flyerStart' in w ? w.flyerStart : leaf(s.flyer), door)
  }
  for (const sp of plan.spawners || []) {
    walk('p' + sp.id + ':g', 'Spawner ' + sp.id, 'Ground', sp.at, sp.start, sp.door)
    walk('p' + sp.id + ':f', 'Spawner ' + sp.id, 'Flyer', sp.at, sp.flyerStart, sp.door)
  }
  return out
}

/** The Routes section: a row per route, and with the rift moved, Reconnect Routes for
 * the routes that lead away from it. '' when the level has no routes. */
export function reconnectHtml (prop, list) {
  const rows = []
  const steps = (prop && prop.steps) || []
  const claimed = new Set()
  if (prop && prop.exit) {
    rows.push('<label class="row"><span>Exit point ' + esc(prop.exit.name) +
      ' moves beside the rift</span><input type="checkbox" data-reconnect="exit" checked>' +
      '</label>')
  }
  for (const r of list || []) {
    const mine = steps.filter(st => r.refs.has(st.ref))
    for (const st of mine) claimed.add(stepKey(st))
    const meta = [r.kind, r.door ? esc(r.door) : 'No door', `${r.points} points`,
      `${r.length.toLocaleString('en-US')} uu`]
      .concat(r.ends && r.ends.length ? [`Ends at ${esc(r.ends.join(', '))}`] : [])
      .concat(r.reaches ? [] : ['<span class="bad">Does not reach the rift</span>'])
    rows.push(`<div class="route"><div class="routehead"><b>${esc(r.name)}</b>` +
      (mine.length ? `<label class="routefix">Reconnect <input type="checkbox" ` +
        `data-reconnect="${esc(mine.map(stepKey).join(KEY_JOIN))}" checked></label>` : '') +
      `</div><div class="meta">${meta.join(' &middot; ')}</div></div>`)
  }
  const rest = steps.filter(st => !claimed.has(stepKey(st)))
  for (const st of rest) {
    rows.push(`<label class="row"><span>${esc(st.label)} &rarr; ${esc(st.toLabel)}: ` +
      `to the rift</span><input type="checkbox" data-reconnect="${esc(stepKey(st))}" checked></label>`)
  }
  if (!rows.length) return ''
  const fix = steps.length || (prop && prop.exit)
  return '<h2>Routes</h2>' + rows.join('') +
    (fix ? '<button id="reconnect">Reconnect Routes</button>' : '')
}

/** Wire the section's button; `onApply(chosen)` gets the ticked keys. */
export function wireReconnect (body, onApply) {
  const button = body.querySelector('#reconnect')
  if (!button) return
  button.addEventListener('click', () => {
    const chosen = new Set([...body.querySelectorAll('[data-reconnect]')]
      .filter(b => b.checked).flatMap(b => b.dataset.reconnect.split(KEY_JOIN)))
    onApply(chosen)
  })
}
