// Fab art libraries in the Map Editor: the library browser beside the kit catalogue, the
// plan entries for placed Fab pieces (`added` entries of kind 'fab', PLAN-ACTORS.md "Fab
// pieces"), their suggested trap grids and laid run grids for the trap-grid layer, and the
// markers on openings an ogre fits through. Nothing here runs until a library is opened or a
// plan holds a Fab piece.

import * as THREE from 'three'

//: Cards shown at once in the browser.
export const FAB_PAGE = 160
//: Texture size asked for a Fab mesh's base colour, in pixels: the library preview size.
export const FAB_TEXTURE_SIZE = 256
//: Opening marker colour and opacity.
const OPENING_COLOUR = 0x53e0a0
const OPENING_OPACITY = 0.18
//: A column length further than this from 1 counts as a rescale.
const SCALE_TOLERANCE = 1e-3
//: Run grids: same plane within this (uu), same facing within RUN_NORMAL_COS, a tiled
//: rectangle within RUN_COVER_TOLERANCE of its area (riftlord/fabemit.py merge_run).
const RUN_PLANE_TOLERANCE = 15
const RUN_NORMAL_COS = 0.999
const RUN_COVER_TOLERANCE = 0.01
//: Grid half thickness and the half-cell step of its other half-extents, in uu.
const GRID_HALF_THICKNESS = 10
const GRID_HALF_STEP = 75
//: The browser's kind filters and their labels.
export const FAB_KINDS = [['all', 'all'], ['architecture', 'architecture'], ['prop', 'prop'],
  ['opening', 'opening'], ['stairs', 'stairs'], ['roof', 'roof'], ['foliage', 'foliage'],
  ['terrain', 'terrain']]

//: pack -> {pieces, byObject} once read, or the pending request
const packCache = new Map()
//: pack -> when its last read failed (performance.now ms)
const packFailed = new Map()
//: How long a library that failed to read is left before it is asked for again, in ms.
const FAB_RETRY_MS = 10000
let libraryList = null

const escapeText = (text) => String(text ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/"/g, '&quot;')

/** The installed libraries (/api/fab/libraries), read once. */
export async function libraries (fetchJson, fresh) {
  if (!libraryList || fresh) libraryList = await fetchJson('api/fab/libraries')
  return libraryList
}

/** Drop the library list, and one pack's pieces (every pack's when `pack` is null): the
 *  shell's Fab Kits tab converted or deleted a library. */
export function forget (pack) {
  libraryList = null
  for (const key of pack ? [pack] : [...packCache.keys()]) {
    packCache.delete(key)
    packFailed.delete(key)
  }
}

/** One pack's pieces, cached: {pack, title, scale, pieces, byObject}. */
export function loadPack (pack, fetchJson) {
  if (!pack) return Promise.resolve(null)
  const failed = packFailed.get(pack)
  if (failed && performance.now() - failed < FAB_RETRY_MS) return Promise.resolve(null)
  if (!packCache.has(pack)) {
    const pending = fetchJson('api/fab/pieces/' + encodeURIComponent(pack))
      .then(r => {
        const got = { ...r, byObject: new Map((r.pieces || []).map(p => [p.object, p])),
                      bySource: new Map((r.pieces || []).filter(p => p.source).map(p => [p.source, p])) }
        packCache.set(pack, got)
        packFailed.delete(pack)
        return got
      }, () => { packCache.delete(pack); packFailed.set(pack, performance.now()); return null })
    packCache.set(pack, pending)
  }
  const hit = packCache.get(pack)
  return hit && !(hit instanceof Promise) ? Promise.resolve(hit) : hit
}

/** Whether a pack's pieces have arrived. */
export const packReady = (pack) => {
  const hit = packCache.get(pack)
  return !!hit && !(hit instanceof Promise)
}

/** A placed entry's catalogue piece if its pack has arrived, else null (and asks for it). */
export function pieceOf (entry, fetchJson) {
  const hit = packCache.get(entry.pack)
  if (!hit) { if (fetchJson) loadPack(entry.pack, fetchJson); return null }
  if (hit instanceof Promise) return null
  return (entry.source && hit.bySource.get(entry.source)) || hit.byObject.get(entry.object) || null
}

/** Cards for the browser, filtered. */
export function cardsHtml (pack, kind, search) {
  const terms = (search || '').toLowerCase().trim().split(/\s+/).filter(Boolean)
  const shown = (pack.pieces || []).filter(p =>
    (kind === 'all' || (p.kinds || []).includes(kind)) &&
    terms.every(t => (p.display + ' ' + p.name).toLowerCase().includes(t)))
  const html = shown.slice(0, FAB_PAGE).map(p => {
    const ogre = p.opening && p.opening.ogre_passable
    const sub = [p.role, p.grids.length ? `${p.grids.length} grid${p.grids.length > 1 ? 's' : ''}` : '',
      ogre ? 'ogre passes' : ''].filter(Boolean).join(' &middot; ')
    return `<div class="kitcard${p.grids.length ? ' grid' : ''}" draggable="true"` +
      ` data-fab="1" data-pack="${escapeText(pack.pack)}" data-object="${escapeText(p.object)}"` +
      ` title="${escapeText(p.name)}">` +
      (p.thumb
        ? `<img loading="lazy" src="api/fab/thumb/${encodeURIComponent(pack.pack)}/` +
          `${p.thumb.split('/').map(encodeURIComponent).join('/')}" alt="">`
        : '<div style="height:46px"></div>') +
      `<div class="n">${escapeText(p.display)}</div><div class="d">${sub}</div></div>`
  }).join('')
  return { html, shown: shown.length, total: (pack.pieces || []).length }
}

/** The pack dropdown's options. */
export function packOptions (list, current) {
  return (list || []).map(l =>
    `<option value="${escapeText(l.id)}"${l.id === current ? ' selected' : ''}` +
    `${l.status !== 'ready' ? ' disabled' : ''}>${escapeText(l.title)}` +
    `${l.status !== 'ready' ? ' (unavailable)' : ` (${l.meshes})`}</option>`).join('')
}

/** A new plan entry for `piece` dropped at `at`: turned as `turn` (a Matrix4, of which
 * only the rotation is used; kitplace.placeMatrix faces a wall's trap side to the viewer)
 * and stood with its bounds on the point, centred on it. Suggested grids start on. */
export function newEntry (pack, piece, at, turn) {
  const b = piece.bounds || { min: [0, 0, 0], max: [0, 0, 0] }
  const m = new THREE.Matrix4().extractRotation(turn || new THREE.Matrix4())
  const box = new THREE.Box3(new THREE.Vector3(...b.min), new THREE.Vector3(...b.max)).applyMatrix4(m)
  const mid = box.getCenter(new THREE.Vector3())
  m.setPosition(at[0] - mid.x, at[1] - mid.y, at[2] - box.min.z)
  return { name: piece.mesh, kind: 'fab', pack, object: piece.object, source: piece.source, role: piece.role,
           grids: piece.grids.map(g => g.id), matrix: m.toArray() }
}

/** Column lengths of a flat column-major matrix. */
export function scaleOf (flat) {
  return [0, 1, 2].map(c => Math.hypot(flat[c * 4], flat[c * 4 + 1], flat[c * 4 + 2]))
}

export const isUnscaled = (flat) => scaleOf(flat).every(s => Math.abs(s - 1) <= SCALE_TOLERANCE)

/** `flat` with its scale removed when the entry may not be rescaled (fablibrary.scalable;
 * every role may since 2026-10-19). */
export function lockScale (entry, flat, piece) {
  const scalable = piece ? piece.scalable : true
  if (scalable || isUnscaled(flat)) return flat
  const out = flat.slice()
  scaleOf(flat).forEach((s, c) => { if (s > 1e-9) for (let r = 0; r < 3; r++) out[c * 4 + r] /= s })
  return out
}

/** A piece's chosen suggested grids in world space: [{entry, id, center, axes, extent, bits,
 * warmachine, surface}]. None for a rescaled piece. */
export function worldGrids (entry, piece) {
  if (!piece || !isUnscaled(entry.matrix)) return []
  const m = new THREE.Matrix4().fromArray(entry.matrix)
  const on = new Set(entry.grids || [])
  return piece.grids.filter(g => on.has(g.id)).map(g => ({
    entry, id: g.id, surface: g.surface, bits: g.bits, warmachine: g.warmachine,
    extent: g.extent.slice(),
    center: new THREE.Vector3(...g.origin).applyMatrix4(m).toArray(),
    axes: g.axes.map(a => new THREE.Vector3(...a).transformDirection(m).toArray()),
  }))
}

/** A laid grid as the trap-grid layer draws it (identity matrix, world coordinates). */
export function layerGrid (g) {
  const ax = Array.isArray(g.axes) ? g.axes : [g.axes.x, g.axes.y, g.axes.z]
  const n = ax[0]
  const surface = n[2] > 0.7 ? 'Floor' : (n[2] < -0.7 ? 'Ceiling' : 'Wall')
  return { origin: g.center, axes: ax, extent: g.extent, surface }
}

const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

/** One grid across `grids` (world space), or {why}. Mirrors fabemit.merge_run. */
export function mergeRun (grids) {
  if (grids.length < 2) return { why: 'pick two or more pieces whose grids share a surface' }
  const first = grids[0]
  const [n, u, v] = first.axes
  for (const g of grids.slice(1)) {
    if (dot(g.axes[0], n) < RUN_NORMAL_COS) return { why: 'the grids face different ways' }
    if (g.bits !== first.bits || (g.warmachine ?? null) !== (first.warmachine ?? null)) {
      return { why: 'the grids take different traps' }
    }
  }
  const depth = grids.map(g => dot(n, g.center))
  if (Math.max(...depth) - Math.min(...depth) > RUN_PLANE_TOLERANCE) {
    return { why: 'the grids are not on one plane' }
  }
  let area = 0
  const rects = grids.map(g => {
    const cu = dot(u, g.center)
    const cv = dot(v, g.center)
    const hu = [1, 2].reduce((s, i) => s + Math.abs(dot(u, g.axes[i])) * g.extent[i], 0)
    const hv = [1, 2].reduce((s, i) => s + Math.abs(dot(v, g.axes[i])) * g.extent[i], 0)
    area += 4 * hu * hv
    return [cu - hu, cu + hu, cv - hv, cv + hv]
  })
  const u0 = Math.min(...rects.map(r => r[0]))
  const u1 = Math.max(...rects.map(r => r[1]))
  const v0 = Math.min(...rects.map(r => r[2]))
  const v1 = Math.max(...rects.map(r => r[3]))
  const box = (u1 - u0) * (v1 - v0)
  if (Math.abs(box - area) > RUN_COVER_TOLERANCE * box) {
    return { why: 'the grids do not tile one rectangle (gaps or overlaps)' }
  }
  const half = [(u1 - u0) / 2, (v1 - v0) / 2]
  if (half.some(e => Math.abs(e / GRID_HALF_STEP - Math.round(e / GRID_HALF_STEP)) > 1e-2)) {
    return { why: 'the run is not a whole number of half cells' }
  }
  const [eu, ev] = half.map(e => Math.round(e / GRID_HALF_STEP) * GRID_HALF_STEP)
  const d = Math.max(...depth)
  const mu = (u0 + u1) / 2
  const mv = (v0 + v1) / 2
  const center = [0, 1, 2].map(k => n[k] * d + u[k] * mu + v[k] * mv)
  return { grid: { center, axes: { x: n, y: u, z: v }, extent: [GRID_HALF_THICKNESS, eu, ev],
                   bits: first.bits, warmachine: first.warmachine ?? null } }
}

/** The best run grid over the picked Fab entries: the largest group of their chosen grids
 * on one plane that tiles a rectangle. {grid, used: [{entry, id}]} or {why}. */
export function runFor (entries, fetchJson) {
  const all = []
  for (const e of entries) all.push(...worldGrids(e, pieceOf(e, fetchJson)))
  const groups = []
  for (const g of all) {
    const home = groups.find(gr => dot(gr[0].axes[0], g.axes[0]) >= RUN_NORMAL_COS &&
      Math.abs(dot(gr[0].axes[0], g.center) - dot(gr[0].axes[0], gr[0].center)) <= RUN_PLANE_TOLERANCE &&
      gr[0].bits === g.bits)
    if (home) home.push(g)
    else groups.push([g])
  }
  groups.sort((a, b) => b.length - a.length)
  let why = 'the picked pieces have no grids on a shared surface'
  for (const gr of groups) {
    if (new Set(gr.map(g => g.entry)).size < 2) continue
    const got = mergeRun(gr)
    if (got.grid) return { grid: got.grid, used: gr.map(g => ({ entry: g.entry, id: g.id })) }
    why = got.why
  }
  return { why }
}

/** Trap-grid layer items for the plan's Fab pieces and laid grids. */
export function layerItems (plan, chosen, fetchJson) {
  const out = []
  ;(plan.added || []).forEach((e, i) => {
    if (e.kind !== 'fab' || !isUnscaled(e.matrix)) return
    const piece = pieceOf(e, fetchJson)
    if (!piece) return
    const on = new Set(e.grids || [])
    const grids = piece.grids.filter(g => on.has(g.id))
    if (grids.length) out.push({ key: 'fab|' + i + '|' + [...on].join(','), matrix: e.matrix, grids, selected: chosen.has(e) })
  })
  ;(plan.fabGrids || []).forEach((g, i) => {
    out.push({ key: 'fabgrid|' + i, matrix: new THREE.Matrix4().toArray(), grids: [layerGrid(g)],
               selected: false })
  })
  return out
}

/** Markers on the openings of placed pieces an ogre fits through, kept in step with the plan. */
export class OpeningMarkers {
  constructor (parent) {
    this.group = new THREE.Group()
    this.group.name = 'fab-openings'
    parent.add(this.group)
    this.signature = ''
    this.material = new THREE.MeshBasicMaterial({ color: OPENING_COLOUR, transparent: true,
      opacity: OPENING_OPACITY, side: THREE.DoubleSide, depthWrite: false })
    this.edges = new THREE.LineBasicMaterial({ color: OPENING_COLOUR })
  }

  sync (plan, fetchJson) {
    const items = []
    for (const e of plan.added || []) {
      if (e.kind !== 'fab') continue
      const piece = pieceOf(e, fetchJson)
      if (piece && piece.opening && piece.opening.ogre_passable) items.push([e, piece])
    }
    const signature = items.map(([e]) => e.object + e.matrix.join(',')).join('|')
    if (signature === this.signature) return
    this.signature = signature
    for (const o of [...this.group.children]) {
      this.group.remove(o)
      if (o.geometry) o.geometry.dispose()
    }
    for (const [e, piece] of items) {
      const o = piece.opening
      const b = piece.bounds
      const across = o.axis === 'x' ? 0 : 1
      const thin = 1 - across
      const size = [0, 0, o.clear_height]
      size[across] = o.clear_width
      size[thin] = b.max[thin] - b.min[thin]
      const centre = [0, 0, b.min[2] + o.clear_height / 2]
      centre[across] = o.center_offset
      centre[thin] = (b.min[thin] + b.max[thin]) / 2
      const geo = new THREE.BoxGeometry(...size)
      geo.translate(...centre)
      const m = new THREE.Matrix4().fromArray(e.matrix)
      const fill = new THREE.Mesh(geo, this.material)
      fill.matrixAutoUpdate = false
      fill.matrix.copy(m)
      fill.raycast = () => {}
      const lines = new THREE.LineSegments(new THREE.EdgesGeometry(geo), this.edges)
      lines.matrixAutoUpdate = false
      lines.matrix.copy(m)
      lines.raycast = () => {}
      this.group.add(fill, lines)
    }
  }
}

/** The Fab section of the properties panel for the picked Fab entries. */
export function propsHtml (entries, plan, fetchJson) {
  if (!entries.length) return ''
  const rows = ['<h2>Fab piece</h2>']
  if (entries.length === 1) {
    const e = entries[0]
    const p = pieceOf(e, fetchJson)
    if (!p) {
      return rows.concat([`<div class="hint">${packReady(e.pack)
        ? 'This piece is not in its library any more (rebuilt?).'
        : 'Reading its library…'}</div>`]).join('')
    }
    rows.push(`<div class="hint"><b>${escapeText(p.display)}</b> &middot; ${escapeText(e.pack)}` +
      ` &middot; ${escapeText(p.role)}</div>`)
    rows.push(`<div class="hint">${p.scalable
      ? 'The size handle rescales it. Its suggested trap grids are left out while it is rescaled.'
      : 'This piece keeps its pack scale; the size handle does nothing to it.'}</div>`)
    if (p.opening && p.opening.has_opening) {
      rows.push(`<div class="hint">Opening ${Math.round(p.opening.clear_width)} &times; ` +
        `${Math.round(p.opening.clear_height)} uu: ${p.opening.ogre_passable
          ? 'an ogre fits through, so an enemy route may use it (green box).'
          : 'too small for an ogre; keep enemy routes out of it.'}</div>`)
    }
    for (const w of p.warnings || []) rows.push(`<div class="hint">&#9888; ${escapeText(w)}</div>`)
    if (p.grids.length) {
      const on = new Set(e.grids || [])
      rows.push('<h2>Suggested Trap Grids</h2>')
      if (!isUnscaled(e.matrix)) {
        rows.push('<div class="hint">Left out while the piece is rescaled.</div>')
      }
      rows.push('<div id="fabgridlist">' + p.grids.map(g =>
        `<label class="row"><span><input type="checkbox" data-fabgrid="${escapeText(g.id)}"` +
        `${on.has(g.id) ? ' checked' : ''}> ${escapeText(g.kind)}</span>` +
        `<span>${g.cells ? g.cells.join(' &times; ') : ''}</span></label>`).join('') + '</div>')
      rows.push('<div class="hint">Untick a grid on a surface another piece covers. Turn on ' +
        '<b>trap grids</b> in Display Options to see them.</div>')
    } else {
      rows.push('<div class="hint">No suggested trap grid (stairs take no floor grids, door ' +
        'leaves and foliage none).</div>')
    }
  } else {
    rows.push(`<div class="hint">${entries.length} Fab pieces picked.</div>`)
  }
  if (entries.length > 1) {
    rows.push('<button id="fabrun">Merge Trap Grids</button>')
    rows.push('<div class="hint">Replaces their suggested grids on the shared surface with one ' +
      'grid over the whole run. It stays where it is laid if the pieces move.</div>')
  }
  const laid = (plan.fabGrids || []).filter(g =>
    (g.pieces || []).some(i => entries.includes((plan.added || [])[i])))
  if (laid.length) {
    rows.push('<h2>Laid Grids</h2>' + laid.map(g =>
      `<div class="row"><span>${(2 * g.extent[1] / 300)} &times; ${(2 * g.extent[2] / 300)} cells</span>` +
      `<button data-fabunlay="${g.id}" style="width:auto">Delete</button></div>`).join(''))
  }
  return rows.join('')
}
