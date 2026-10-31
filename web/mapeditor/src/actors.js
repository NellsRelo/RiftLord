// The plan's gameplay-actor graph, version 2 (riftlord/PLAN-ACTORS.md): shared path
// points, spawner start references, player starts and edits to the level's own actors.
//
// A Ref is an added point's id (number) or a level point's export name (string);
// 'rift' in a branch means the level's exit point.

export const PLAN_VERSION = 2

//: The range shipped levels' branch weights add up to at one point. Mirrors
//: SHIPPED_WEIGHT_SUM in riftlord/planactors.py.
export const SHIPPED_WEIGHT_SUM = [0.4, 2.0]

//: Player start phases, in the order the picker lists them: value, label, hint.
//: bAllowDuringBreaks marks a "breaks" start, bAllowDuringWaves a "waves" start.
export const PHASES = [
  ['breaks', 'Between Waves',
   'Players respawn here between waves. Shipped levels have two, side by side.'],
  ['waves', 'During Waves',
   'Players respawn here while a wave is running. Shipped levels have one, near the rift.'],
  ['both', 'Always',
   'Players respawn here between waves and while a wave is running.'],
]

/** Version 1 (points nested under spawners) to version 2, in place. Mirrors
 * riftlord/planactors.py migrate(). */
export function migratePlan (plan) {
  plan.points = plan.points || []
  plan.playerStarts = plan.playerStarts || []
  plan.wiring = plan.wiring || []
  for (const sp of plan.spawners || []) {
    const nested = (sp.points || []).filter(p => p && typeof p === 'object')
    delete sp.points
    if (nested.length) {
      const first = nested.find(p => p.isStart) || nested[0]
      const key = (sp.kind || 'flyer') === 'flyer' ? 'flyerStart' : 'start'
      if (sp[key] === undefined || sp[key] === null || sp[key] === '') sp[key] = first.id
    }
    for (const p of nested) {
      delete p.isStart
      delete p.isExit
      plan.points.push(p)
    }
    if (!('start' in sp)) sp.start = null
    if (!('flyerStart' in sp)) sp.flyerStart = null
  }
  plan.version = PLAN_VERSION
  return plan
}

/** A Ref as the plan stores it: a number for an added point, else the name. */
export function asRef (value) {
  if (value === null || value === undefined || value === '') return null
  if (value === 'rift') return 'rift'
  return /^-?\d+$/.test(String(value)) ? +value : String(value)
}

/** {index: export name} for the level's own points; a missing index is 0. */
export function levelIndex (level) {
  const out = new Map()
  for (const p of Object.values((level && level.points) || {})) {
    const i = typeof p.index === 'number' ? p.index : 0
    if (!out.has(i)) out.set(i, p.export)
  }
  return out
}

/** The level's exit point's name, or null. */
export function exitName (level) {
  const hit = Object.values((level && level.points) || {}).find(p => p.exit)
  return hit ? hit.export : null
}

/** One level point's branches as the level ships them, as Refs. */
export function shippedNext (level, name) {
  const p = ((level && level.points) || {})[name]
  if (!p) return []
  const byIndex = levelIndex(level)
  return (p.next || []).map(b => ({ to: byIndex.get(b.PointIndex) ?? String(b.PointIndex),
                                    weight: b.BranchWeight ?? 1 }))
}

/** The plan's edit of one of the level's own actors, or undefined. */
export const wiringOf = (plan, name) => (plan.wiring || []).find(w => w.name === name)

/** Where a Ref sends a wave next, after the plan's edits. */
export function nextOf (plan, level, ref) {
  if (typeof ref === 'number') {
    const p = (plan.points || []).find(o => o.id === ref)
    return p ? (p.next || []) : []
  }
  const w = wiringOf(plan, ref)
  return w && Array.isArray(w.next) ? w.next : shippedNext(level, ref)
}

//: A spawner's first point closer than this (horizontally, uu) is aimed past when the point
//: carries no reach radius. Mirrors DEFAULT_REACH in riftlord/mapeditor/spawnerfacing.py.
export const SPAWNER_DEFAULT_REACH = 300
//: Below this horizontal distance (uu) there is no direction to face (MIN_LEG there).
const SPAWNER_MIN_LEG = 1

/** Yaw in degrees from `a` to `b` (Unreal: 0 along +x, 90 along +y), to 0.1, or null
 * under SPAWNER_MIN_LEG apart. */
function headingOf (a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1]
  if (Math.hypot(dx, dy) < SPAWNER_MIN_LEG) return null
  return Math.round(Math.atan2(dy, dx) * 1800 / Math.PI) / 10
}

/** The yaw a plan spawner faces when not turned by hand: toward its route's first point
 * (a flyer spawner's flyer route first), or the point after it when the first is within
 * its reach radius; null with no route. `locate(ref)` is where a Ref stands. Mirrors
 * riftlord/mapeditor/spawnerfacing.py facing(). */
export function spawnerFacing (plan, level, sp, locate) {
  const keys = sp.kind === 'flyer' ? ['flyerStart', 'start'] : ['start', 'flyerStart']
  const exit = riftTarget(plan, level)
  const riftEnds = riftExits(plan, level)
  for (const key of keys) {
    const ref = sp[key]
    if (ref === null || ref === undefined || isGone(plan, ref)) continue
    const first = locate(ref)
    if (!first) continue
    const own = typeof ref === 'number'
      ? (plan.points || []).find(o => o.id === ref)
      : ((level && level.points) || {})[ref]
    const reach = (own && typeof own.reachRadius === 'number') ? own.reachRadius
      : SPAWNER_DEFAULT_REACH
    if (Math.hypot(first[0] - sp.at[0], first[1] - sp.at[1]) < reach) {
      //: a rift's own exit point leads nowhere
      const b = riftEnds.has(ref) ? null : nextOf(plan, level, ref)[0]
      const to = b ? (b.to === 'rift' ? exit : b.to)
        : (typeof ref === 'number' && !riftEnds.has(ref) ? exit : null)
      const after = to !== null && to !== undefined ? locate(to) : null
      const yaw = after ? headingOf(sp.at, after) : null
      if (yaw !== null) return yaw
    }
    const yaw = headingOf(sp.at, first)
    if (yaw !== null) return yaw
  }
  return null
}

/** Whether a Ref is removed from play by the plan. */
export const isGone = (plan, ref) =>
  typeof ref === 'string' && !!(wiringOf(plan, ref) || {}).gone

//: How far a rift's exit point stands below the rift (uu), and BP_OMDRift's RiftEnterArea
//: capsule (radius, half-height, its centre's drop below the rift). Mirror RIFT_OVER_EXIT
//: and RIFT_ENTER_* in riftlord/mapeditor/planactors.py.
export const RIFT_OVER_EXIT = 258.2
export const RIFT_ENTER = { radius: 200, half: 400, drop: 200 }

/** Whether `at` stands inside the RiftEnterArea of a rift at `riftAt`. Mirrors in_rift. */
export function inRift (at, riftAt) {
  const dx = at[0] - riftAt[0], dy = at[1] - riftAt[1]
  const dz = at[2] - (riftAt[2] - RIFT_ENTER.drop)
  const past = Math.max(0, Math.abs(dz) - (RIFT_ENTER.half - RIFT_ENTER.radius))
  return dx * dx + dy * dy + past * past <= RIFT_ENTER.radius * RIFT_ENTER.radius
}

/** Whether the plan removes the level's exit point and its first rift, naming no exit
 * point of its own, takes that exit (moved to it). Mirrors moves_exit. */
export function movesExit (plan, level) {
  const exit = exitName(level)
  const first = (plan.rifts || [])[0]
  return !!(exit && first && (first.exit === undefined || first.exit === null) && isGone(plan, exit))
}

/** Map of added point id -> rift id: each of the plan's rifts' exit point, its `exit`, else
 * the nearest added point with no branches inside it (not for the first rift when it takes
 * the level's moved exit). Mirrors rift_exits. */
export function riftExits (plan, level) {
  const points = new Map((plan.points || []).filter(p => typeof p.id === 'number').map(p => [p.id, p]))
  const rifts = plan.rifts || []
  const out = new Map()
  for (const r of rifts) {
    if (typeof r.exit === 'number' && points.has(r.exit) && !out.has(r.exit)) out.set(r.exit, r.id)
  }
  const movedFirst = movesExit(plan, level)
  rifts.forEach((r, n) => {
    if ((r.exit !== undefined && r.exit !== null) || (n === 0 && movedFirst) || !r.at) return
    let best = null, far = Infinity
    for (const [pid, p] of points) {
      if (out.has(pid) || (p.next || []).length || !inRift(p.at, r.at)) continue
      const d = Math.hypot(p.at[0] - r.at[0], p.at[1] - r.at[1], p.at[2] - r.at[2])
      if (d < far) { best = pid; far = d }
    }
    if (best !== null) out.set(best, r.id)
  })
  return out
}

/** Where 'rift' (and an added point with no branches) leads: the level's exit point's name,
 * or with it removed the first rift's own exit point's id; null when neither. */
export function riftTarget (plan, level) {
  const exit = exitName(level)
  if (!exit || !isGone(plan, exit) || movesExit(plan, level)) return exit
  const own = riftExits(plan, level)
  for (const r of plan.rifts || []) {
    for (const [pid, rid] of own) if (rid === r.id) return pid
  }
  return exit
}

/** Every node of the path graph, with its outgoing edges resolved, removed points
 * spliced out. Keys are `p<id>` for added points and `l<name>` for the level's. `exits` are
 * the exit nodes' keys: the level's exit point and each rift's own (riftExits). */
export function pathGraph (plan, level) {
  const exit = exitName(level)
  const key = (ref) => (typeof ref === 'number' ? 'p' + ref : 'l' + ref)
  const moved = movesExit(plan, level)
  const own = riftExits(plan, level)
  const to = riftTarget(plan, level)
  //: a removed exit moves to the plan's first rift, so it stays the end of every route
  const gone = (ref) => isGone(plan, ref) && !(ref === exit && moved)
  const nodes = new Map()
  const resolve = (ref, seen = new Set()) => {
    if (ref === 'rift') ref = to
    if (ref === null || ref === undefined) return []
    if (!gone(ref)) return [key(ref)]
    //: a branch into the removed level exit goes on to where 'rift' leads
    if (ref === exit && to !== exit) return resolve(to, seen)
    if (seen.has(ref)) return []
    seen.add(ref)
    return nextOf(plan, level, ref).flatMap(b => resolve(b.to, seen))
  }
  const add = (ref) => {
    if (gone(ref)) return
    if (own.has(ref)) { nodes.set(key(ref), []); return }
    let out = nextOf(plan, level, ref).flatMap(b => resolve(b.to))
    //: a point with no branches goes on to the exit (the emitter adds the branch)
    if (ref !== exit && !out.length && to !== null && to !== undefined) out = [key(to)]
    nodes.set(key(ref), out)
  }
  for (const p of plan.points || []) add(p.id)
  for (const name of Object.keys((level && level.points) || {})) add(name)
  const exits = [...own.keys()].map(key)
  if (exit && !gone(exit)) exits.unshift(key(exit))
  return { nodes, exit: exit ? key(exit) : null, exits, key }
}

/** Map of node key -> the rift ids its routes end at ('level' for the level's own rift),
 * for every node of pathGraph. */
export function riftsReached (plan, level) {
  const { nodes, key } = pathGraph(plan, level)
  const exit = exitName(level)
  const ends = new Map()
  if (exit) ends.set(key(exit), movesExit(plan, level) ? plan.rifts[0].id : 'level')
  for (const [pid, rid] of riftExits(plan, level)) ends.set(key(pid), rid)
  const out = new Map()
  for (const start of nodes.keys()) {
    const got = new Set(), seen = new Set(), todo = [start]
    while (todo.length) {
      const v = todo.pop()
      if (seen.has(v)) continue
      seen.add(v)
      if (ends.has(v)) got.add(ends.get(v))
      for (const w of nodes.get(v) || []) todo.push(w)
    }
    out.set(start, [...got])
  }
  return out
}

/** {cycle: Set, stranded: Set} of node keys: on a loop, and unable to reach the exit. */
export function graphReport (plan, level) {
  const { nodes, exits, key } = pathGraph(plan, level)
  // Tarjan's strongly connected components
  let n = 0
  const index = new Map(), low = new Map(), stack = [], on = new Set()
  const cycle = new Set()
  const visit = (v) => {
    index.set(v, n); low.set(v, n); n += 1
    stack.push(v); on.add(v)
    for (const w of nodes.get(v) || []) {
      if (!nodes.has(w)) continue
      if (!index.has(w)) { visit(w); low.set(v, Math.min(low.get(v), low.get(w))) }
      else if (on.has(w)) low.set(v, Math.min(low.get(v), index.get(w)))
    }
    if (low.get(v) === index.get(v)) {
      const comp = []
      let w
      do { w = stack.pop(); on.delete(w); comp.push(w) } while (w !== v)
      if (comp.length > 1 || (nodes.get(v) || []).includes(v)) comp.forEach(c => cycle.add(c))
    }
  }
  for (const v of nodes.keys()) if (!index.has(v)) visit(v)
  // reverse reachability from the exits
  const into = new Map()
  for (const [v, outs] of nodes) for (const w of outs) {
    if (!into.has(w)) into.set(w, [])
    into.get(w).push(v)
  }
  const reach = new Set()
  const todo = [...exits]
  while (todo.length) {
    const v = todo.pop()
    if (reach.has(v)) continue
    reach.add(v)
    for (const u of into.get(v) || []) todo.push(u)
  }
  const stranded = new Set([...nodes.keys()].filter(v => !reach.has(v)))
  return { cycle, stranded, key }
}

/** Take an added point out of the plan, sending its incoming branches on to its own
 * branches (weights multiplied), as the emitter does for a removed level point. */
export function removePlanPoint (plan, id) {
  const gone = (plan.points || []).find(p => p.id === id)
  if (!gone) return
  const onward = gone.next || []
  //: `self` is the point whose list this is: a redirect never makes it branch to itself,
  //: and what is left is scaled back to the list's own total
  const redirect = (list, self) => {
    const out = []
    const before = (list || []).reduce((s, b) => s + b.weight, 0)
    for (const b of list || []) {
      if (b.to !== id) { out.push({ ...b }); continue }
      for (const o of onward) {
        if (o.to === id || o.to === self) continue
        const hit = out.find(x => x.to === o.to)
        const w = b.weight * o.weight
        if (hit) hit.weight += w
        else out.push({ to: o.to, weight: w })
      }
    }
    const after = out.reduce((s, b) => s + b.weight, 0)
    for (const b of out) {
      b.weight = Math.round((after > 0 ? b.weight * before / after : b.weight) * 100) / 100
    }
    return out
  }
  plan.points = plan.points.filter(p => p.id !== id)
  for (const p of plan.points) p.next = redirect(p.next, p.id)
  for (const w of plan.wiring || []) {
    if (Array.isArray(w.next)) w.next = redirect(w.next, w.name)
  }
  const first = onward.find(o => o.to !== 'rift' && o.to !== id)
  for (const sp of plan.spawners || []) {
    for (const k of ['start', 'flyerStart']) if (sp[k] === id) sp[k] = first ? first.to : null
  }
  for (const w of plan.wiring || []) {
    for (const k of ['start', 'flyerStart']) {
      if (w[k] === id) w[k] = first ? first.to : null
    }
  }
}

/** Whether the level still has a player start usable in each phase after a change.
 * Both true when the level's own starts are not known (no `player_starts`). */
export function startsCover (plan, level) {
  if (!level || !Array.isArray(level.player_starts)) return { breaks: true, waves: true }
  const phases = []
  for (const s of (level && level.player_starts) || []) {
    const w = wiringOf(plan, s.export)
    if (w && w.gone) continue
    phases.push((w && w.phase) || s.phase)
  }
  for (const s of plan.playerStarts || []) phases.push(s.phase)
  return {
    breaks: phases.some(p => p === 'breaks' || p === 'both'),
    waves: phases.some(p => p === 'waves' || p === 'both'),
  }
}
