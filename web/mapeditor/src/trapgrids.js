// The "trap grids" layer: each kit piece's trap surfaces drawn as a grid of cells,
// floor, wall and ceiling grids in their own colours, and the selected piece's grids
// stronger. Grids come from /api/kit (kit-geometry.json): a box per grid whose local X
// is the trap side and whose local Y and Z run along the cells.

import * as THREE from 'three'

//: One trap cell, in uu.
export const CELL = 300
//: Grid colours by surface.
export const SURFACE_COLOUR = { Floor: 0x4fd1ff, Wall: 0xffb347, Ceiling: 0xc77dff }
//: Grid line opacity, and the selected piece's.
const LINE_OPACITY = 0.6
const SELECTED_LINE_OPACITY = 1.0
//: Fill opacity of the selected piece's trap surfaces.
const SELECTED_FILL_OPACITY = 0.22
//: The grid sits this far in front of its trap face, in uu.
const LIFT = 12
//: How often the layer re-reads the level and the plan, in milliseconds.
export const SYNC_INTERVAL = 250

/** One grid's trap face under `matrix` (a Matrix4): {origin, along, up, n, m}, a
 * corner, the face's two full edges and the cell count along each, measured after the
 * piece's own scale (a resized kit piece's grid stretches with it). */
function face (grid, matrix) {
  const [ex, ey, ez] = grid.extent
  const ax = new THREE.Vector3(...grid.axes[0])
  const ay = new THREE.Vector3(...grid.axes[1])
  const az = new THREE.Vector3(...grid.axes[2])
  const centre = new THREE.Vector3(...grid.origin).addScaledVector(ax, ex + LIFT)
  const corner = centre.clone().addScaledVector(ay, -ey).addScaledVector(az, -ez)
  const origin = corner.clone().applyMatrix4(matrix)
  const along = corner.clone().addScaledVector(ay, 2 * ey).applyMatrix4(matrix).sub(origin)
  const up = corner.clone().addScaledVector(az, 2 * ez).applyMatrix4(matrix).sub(origin)
  return { origin, along, up, n: along.length() / CELL, m: up.length() / CELL }
}

/** Line-segment endpoints of a face's cell lines, pushed onto `out`. */
function cellLines (f, out) {
  const push = (a, b) => out.push(a.x, a.y, a.z, b.x, b.y, b.z)
  const lines = (count, step, span) => {
    const k = Math.floor(count + 1e-3)
    const at = []
    for (let i = 0; i <= k; i++) at.push(i / count)
    if (at[at.length - 1] < 1 - 1e-3) at.push(1)
    for (const t of at) {
      const a = f.origin.clone().addScaledVector(step, t)
      push(a, a.clone().add(span))
    }
  }
  lines(f.n, f.along, f.up)
  lines(f.m, f.up, f.along)
}

/** "4 × 4" cells of one grid. */
export function cellsText (grid) {
  const fmt = (v) => String(Math.round(v * 10) / 10)
  return `${fmt((2 * grid.extent[1]) / CELL)} × ${fmt((2 * grid.extent[2]) / CELL)}`
}

/** The selected piece's trap grid section for the side panel.
 *
 * `kind` is 'kit' (a Blueprint) or 'loading'; `grids` the kit class's grids. Nothing
 * for a piece without a grid. */
export function trapGridHtml (kind, grids) {
  if (kind === 'loading' || !grids || !grids.length) return ''
  const by = {}
  for (const g of grids) (by[g.surface] = by[g.surface] || []).push(cellsText(g))
  return '<h2>Trap Grid</h2>' + Object.keys(by).map(s =>
    `<div class="row"><span><span style="color:#${SURFACE_COLOUR[s].toString(16)
      .padStart(6, '0')}">&#9632;</span> ${s}</span><span>${by[s].join(', ')}</span></div>`)
    .join('')
}

/** The layer's drawing, rebuilt when the pieces or the selection change. */
export class TrapGridLayer {
  constructor (group) {
    this.group = group
    this.signature = ''
    this.lineMats = {}
    this.fillMats = {}
    for (const [s, c] of Object.entries(SURFACE_COLOUR)) {
      this.lineMats[s] = [
        new THREE.LineBasicMaterial({ color: c, transparent: true, opacity: LINE_OPACITY,
                                      depthWrite: false }),
        //: the selected piece's grid shows through whatever covers it
        new THREE.LineBasicMaterial({ color: c, transparent: true,
                                      opacity: SELECTED_LINE_OPACITY, depthWrite: false,
                                      depthTest: false })]
      this.fillMats[s] = new THREE.MeshBasicMaterial({
        color: c, transparent: true, opacity: SELECTED_FILL_OPACITY,
        side: THREE.DoubleSide, depthWrite: false, depthTest: false })
    }
  }

  /** Draw `items` [{key, matrix (flat, column-major), grids, selected}]. */
  sync (items) {
    const signature = items.map(i => i.key + (i.selected ? '*' : '') + i.matrix.join(','))
      .join('|')
    if (signature === this.signature) return
    this.signature = signature
    this.clear()
    const lines = {}
    for (const item of items) {
      const matrix = new THREE.Matrix4().fromArray(item.matrix)
      for (const grid of item.grids) {
        if (!grid.axes || !grid.extent || !grid.origin) continue
        const f = face(grid, matrix)
        const bucket = (lines[grid.surface + (item.selected ? '*' : '')] =
          lines[grid.surface + (item.selected ? '*' : '')] || [])
        cellLines(f, bucket)
        if (item.selected) this._fill(f, grid.surface)
      }
    }
    for (const [key, pts] of Object.entries(lines)) {
      const surface = key.replace('*', '')
      const geo = new THREE.BufferGeometry()
      geo.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3))
      const seg = new THREE.LineSegments(geo,
        this.lineMats[surface][key.endsWith('*') ? 1 : 0])
      seg.raycast = () => {}
      seg.renderOrder = key.endsWith('*') ? 3 : 2
      this.group.add(seg)
    }
  }

  _fill (f, surface) {
    const geo = new THREE.BufferGeometry()
    const a = f.origin
    const b = a.clone().add(f.along)
    const c = b.clone().add(f.up)
    const d = a.clone().add(f.up)
    geo.setAttribute('position', new THREE.Float32BufferAttribute(
      [a, b, c, a, c, d].flatMap(v => [v.x, v.y, v.z]), 3))
    const mesh = new THREE.Mesh(geo, this.fillMats[surface])
    mesh.raycast = () => {}
    mesh.renderOrder = 3
    this.group.add(mesh)
  }

  clear () {
    for (const o of [...this.group.children]) {
      this.group.remove(o)
      if (o.geometry) o.geometry.dispose()
    }
  }
}
