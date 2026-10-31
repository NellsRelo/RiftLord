// The left sidebar's Trace section: a level from another Orcs Must Die game drawn as a
// ghost in the 3D view, to rebuild it by hand. A development tool, not mission data: the
// plan never holds it. Its state ({source: {game, name}, at: [x, y, z], rotation, scale,
// opacity, visible, mode, grid}) is kept per level or mission by riftlord/trace.py
// (/api/trace/state/<key>), which also builds the geometry (/api/trace/geometry/...), the
// Textured display (/api/trace/textured/...) and the source's trap grids
// (/api/trace/grids/...).
//
// The ghost's frame: the source's `anchor` is put on `at`, turned `rotation` degrees
// about Z and scaled by `scale`. Faces are shaded from their own slope (horizontal ones
// in FLOOR_COLOUR, the rest in WALL_COLOUR), so the file carries positions only. Never
// picked: the meshes have no raycast, and the ghost is in no pick list.
//
// Drawing: the level is opaque and drawn first. Every ghost part is drawn twice, after
// it: a depth pass (no colour) and then its colour at the trace's opacity, so only the
// ghost's nearest surface shows and the level hides the ghost behind it. The depth pass
// is a copy of the colour pass's own material (same shader program, so the same
// positions) with colour writes off. It sits DEPTH_OFFSET behind in depth and the colour
// pass COLOUR_OFFSET, nearer than that by a margin, so a colour fragment always passes
// the less-or-equal test against its own pre-pass, and ghost faces within that margin
// of the nearest one (coplanar copies) are told apart by draw order, not by depth noise.
// The stencil lets each pixel take one colour fragment, so overlapping copies never
// blend twice; the colour parts draw in a fixed order (renderOrder by part). Where a
// ghost face lies on a level face the level's shows. The opacity is the ghost's alone;
// no level material is touched.

import * as THREE from 'three'

//: Opacity a new trace starts at (trace.py DEFAULT_OPACITY).
const DEFAULT_OPACITY = 0.35
//: Display modes, in the order the picker lists them.
const MODES = [['ghost', 'Ghost'], ['wire', 'Wireframe'], ['textured', 'Textured']]
//: Input steps: position (uu), height (uu), turn (degrees), scale.
const STEP = { xy: 100, z: 50, rotation: 1, scale: 0.01 }
//: renderOrder of the ghost's depth pass and colour pass (after the level), and of its
//: trap grids (after the ghost). Colour part i of n draws at GHOST_ORDER + (i + 1) / (n + 2)
//: (partOrder).
const DEPTH_ORDER = 10
const GHOST_ORDER = 11
const GRID_ORDER = 12
//: polygonOffset (factor, units) of the depth pass (behind a coplanar level face) and of
//: the colour pass (nearer than the depth pass by one slope unit and DEPTH_MARGIN units).
const DEPTH_MARGIN = 6
const DEPTH_OFFSET = [2, 8]
const COLOUR_OFFSET = [DEPTH_OFFSET[0] - 1, DEPTH_OFFSET[1] - DEPTH_MARGIN]
const offset = ([factor, units]) => ({ polygonOffset: true, polygonOffsetFactor: factor,
                                        polygonOffsetUnits: units })
//: The colour pass's stencil: a pixel takes the first colour fragment that passes depth
//: and refuses the rest (the renderer clears the stencil every frame).
const ONCE = { stencilWrite: true, stencilRef: 1, stencilFunc: THREE.NotEqualStencilFunc,
               stencilFail: THREE.KeepStencilOp, stencilZFail: THREE.KeepStencilOp,
               stencilZPass: THREE.ReplaceStencilOp }
//: Every colour-pass material's depth test and offset.
const COLOUR_PASS = { depthTest: true, depthFunc: THREE.LessEqualDepth, ...offset(COLOUR_OFFSET),
                      ...ONCE }
//: Trap grid line colours by surface (trapgrids.js SURFACE_COLOUR) and their opacity.
const GRID_COLOUR = { Floor: 0x4fd1ff, Wall: 0xffb347, Ceiling: 0xc77dff }
const GRID_OPACITY = 0.9
//: Face colours (sRGB): horizontal faces, the rest, and wireframe lines.
const FLOOR_COLOUR = 0x5fd0ff
const WALL_COLOUR = 0xffb35c
const WIRE_COLOUR = 0x9fe8ff
//: |normal . up| above which a face reads as floor.
const FLOOR_SLOPE = [0.55, 0.8]
//: Milliseconds the state waits after the last change before it is saved.
const SAVE_DELAY = 400
//: Fetched geometry files kept, so switching back is instant.
const KEEP_FILES = 3
//: Most ghost vertices heights() samples for the cutaway's range.
const HEIGHT_SAMPLES = 20000
//: The binaries' magic (trace.py pack, pack_textured).
const MAGIC = 0x52544c52
const TEXTURED_MAGIC = 0x58544c52

const esc = (text) => String(text ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
const round = (v, n = 2) => Math.round(v * 10 ** n) / 10 ** n
const thousands = (v) => Math.round(v).toLocaleString('en-US')
const sourceKey = (s) => s ? `${s.game}\u0001${s.name}` : ''

const VERTEX = /* glsl */`
#include <clipping_planes_pars_vertex>
varying vec3 vWorld;
void main () {
  vec4 p = vec4(position, 1.0);
#ifdef USE_INSTANCING
  p = instanceMatrix * p;
#endif
  vec4 w = modelMatrix * p;
  vWorld = w.xyz;
  vec4 mvPosition = viewMatrix * w;
#include <clipping_planes_vertex>
  gl_Position = projectionMatrix * mvPosition;
}`
const FRAGMENT = /* glsl */`
#include <clipping_planes_pars_fragment>
uniform float opacity;
uniform float wire;
uniform vec3 floorColour;
uniform vec3 wallColour;
uniform vec3 wireColour;
uniform vec2 floorSlope;
varying vec3 vWorld;
void main () {
#include <clipping_planes_fragment>
  if (wire > 0.5) {
    gl_FragColor = vec4(wireColour, opacity);
    return;
  }
  vec3 n = normalize(cross(dFdx(vWorld), dFdy(vWorld)));
  vec3 c = mix(wallColour, floorColour, smoothstep(floorSlope.x, floorSlope.y, abs(n.y)));
  float shade = 0.6 + 0.4 * abs(dot(n, normalize(vec3(0.4, 0.8, 0.3))));
  gl_FragColor = vec4(c * shade, opacity);
}`

/** {meta, parts: [{pos, idx, mats}]} from a trace.py file. */
export function parseTrace (buf) {
  const dv = new DataView(buf)
  if (dv.getUint32(0, true) !== MAGIC) throw new Error('not a trace file')
  const n = dv.getUint32(8, true)
  const meta = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 12, n)))
  let off = 12 + n
  const parts = meta.parts.map((p) => {
    const pos = new Float32Array(buf, off, p.verts * 3)
    off += p.verts * 12
    const idx = new Uint32Array(buf, off, p.tris * 3)
    off += p.tris * 12
    let mats = null
    if (p.instances) {
      mats = new Float32Array(buf, off, p.instances * 16)
      off += p.instances * 64
    }
    return { pos, idx, mats }
  })
  return { meta, parts }
}

/** {meta, parts: [{pos, uv, idx, mats}], images: [Blob]} from a trace.py textured file. */
export function parseTextured (buf) {
  const dv = new DataView(buf)
  if (dv.getUint32(0, true) !== TEXTURED_MAGIC) throw new Error('not a textured trace file')
  const n = dv.getUint32(8, true)
  const meta = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 12, n)))
  let off = 12 + n
  const parts = meta.parts.map((p) => {
    const pos = new Float32Array(buf, off, p.verts * 3)
    off += p.verts * 12
    const uv = new Float32Array(buf, off, p.verts * 2)
    off += p.verts * 8
    const idx = new Uint32Array(buf, off, p.tris * 3)
    off += p.tris * 12
    let mats = null
    if (p.instances) {
      mats = new Float32Array(buf, off, p.instances * 16)
      off += p.instances * 64
    }
    return { pos, uv, idx, mats }
  })
  const images = (meta.textures || []).map((t) => {
    const blob = new Blob([new Uint8Array(buf, off, t.bytes)], { type: 'image/png' })
    off += t.bytes + ((4 - (t.bytes % 4)) % 4)
    return blob
  })
  return { meta, parts, images }
}

/**
 * The Trace tool: its ghost in the 3D view and its section in the left sidebar.
 *
 * `parent` is the Unreal-space group (Z up); `box` the sidebar's <details>, whose
 * `.tracebody` it fills; `clip` the view's clipping planes; `getKey()` names the open
 * level or mission (null when none is open); `focus()` is the open level's play area
 * {lo: [x, y, z], hi: [x, y, z]} or null; `ownLevel()` the open shipped level's name;
 * `conversion()` the plan's `scratch.source` ({kind, name, shift}) or null; `renderer`
 * the view's WebGLRenderer, made with a stencil buffer (the colour pass's ONCE).
 */
export function createTrace ({ parent, box, clip, getKey, focus, ownLevel, conversion, renderer }) {
  const root = new THREE.Group()
  root.name = 'trace'
  const inner = new THREE.Group()
  root.add(inner)
  //: the Textured display, and the source's trap grids; both in the ghost's frame
  const texInner = new THREE.Group()
  root.add(texInner)
  const gridInner = new THREE.Group()
  root.add(gridInner)
  root.visible = false
  parent.add(root)
  const body = box.querySelector('.tracebody') || box
  const material = new THREE.ShaderMaterial({
    vertexShader: VERTEX,
    fragmentShader: FRAGMENT,
    uniforms: {
      opacity: { value: DEFAULT_OPACITY },
      wire: { value: 0 },
      floorColour: { value: new THREE.Color(FLOOR_COLOUR) },
      wallColour: { value: new THREE.Color(WALL_COLOUR) },
      wireColour: { value: new THREE.Color(WIRE_COLOUR) },
      floorSlope: { value: new THREE.Vector2(...FLOOR_SLOPE) },
    },
    transparent: true,
    depthWrite: false,
    side: THREE.DoubleSide,
    clipping: !!clip,
    clippingPlanes: clip || null,
    ...COLOUR_PASS,
  })
  //: each colour-pass material's depth pass (depthTwin); Ghost and Textured, off in
  //: Wireframe
  const twins = new Map()

  /** The depth pass of colour material `m`: a copy with colour writes off, depth writes
   * on, no stencil and DEPTH_OFFSET, so it runs the same shader program. */
  function depthTwin (m) {
    if (!twins.has(m)) {
      const d = m.clone()
      Object.assign(d, { colorWrite: false, depthWrite: true, depthTest: true,
        depthFunc: THREE.LessEqualDepth, stencilWrite: false, ...offset(DEPTH_OFFSET) })
      d.clippingPlanes = m.clippingPlanes
      twins.set(m, d)
    }
    return twins.get(m)
  }

  /** One ghost part into `group`: its depth pass and its colour pass in `mat`, sharing
   * `geo` and, for an instanced part, the `count` instance matrices `mats`; `order`
   * places its colour pass among the others. */
  function addPart (group, geo, mat, mats, count, order) {
    const make = (m, pass, at) => {
      const mesh = mats ? new THREE.InstancedMesh(geo, m, count) : new THREE.Mesh(geo, m)
      mesh.raycast = () => {}
      mesh.frustumCulled = false
      mesh.renderOrder = at
      mesh.userData = { trace: true, pass }
      group.add(mesh)
      return mesh
    }
    const colour = make(mat, 'colour', order)
    if (mats) {
      colour.instanceMatrix.array.set(mats)
      colour.instanceMatrix.needsUpdate = true
    }
    const depth = make(depthTwin(mat), 'depth', DEPTH_ORDER)
    if (mats) depth.instanceMatrix = colour.instanceMatrix
    return colour
  }
  const partOrder = (i, n) => GHOST_ORDER + (i + 1) / (n + 2)
  const colourParts = (group) => group.children.filter(m => m.userData.pass === 'colour')

  //: the open level's key, and the state kept for it (null: no source)
  let key = null
  let keyLevel = null
  let state = null
  //: the source on screen, its meta, and what went wrong
  let shown = ''
  let meta = null
  let error = null
  let loading = false
  let token = 0
  //: /api/trace/sources, fetched once
  let sources = null
  let sourcesError = null
  let saveTimer = null
  const files = new Map()
  //: the Textured display: the source drawn, its textures, and its state
  let texShown = ''
  let texLoading = false
  let texError = null
  let texToken = 0
  const texMaterials = []
  //: the trap grids: the source drawn, their face count, and their state
  let gridShown = ''
  let gridLoading = false
  let gridError = null
  let gridFaces = 0
  let gridToken = 0
  const gridMaterials = Object.fromEntries(Object.entries(GRID_COLOUR).map(([k, c]) =>
    [k, new THREE.LineBasicMaterial({ color: c, transparent: true, opacity: GRID_OPACITY,
      depthWrite: false, clippingPlanes: clip || null })]))

  async function fetchFile (source) {
    const k = sourceKey(source)
    if (files.has(k)) return files.get(k)
    const r = await fetch(`api/trace/geometry/${source.game}/${encodeURIComponent(source.name)}`)
    if (!r.ok) {
      let msg = `${r.status}`
      try { msg = (await r.json()).error || msg } catch (e) { }
      throw new Error(msg)
    }
    const got = parseTrace(await r.arrayBuffer())
    files.set(k, got)
    while (files.size > KEEP_FILES) files.delete(files.keys().next().value)
    return got
  }

  function clearGroup (group) {
    for (const m of [...group.children]) {
      m.geometry.dispose()
      if (m.dispose) m.dispose()
      group.remove(m)
    }
  }

  function clearGhost () {
    clearGroup(inner)
    clearTextured()
    clearGrid()
  }

  function clearTextured () {
    texToken++
    clearGroup(texInner)
    for (const m of texMaterials.splice(0)) {
      if (m.map) m.map.dispose()
      if (twins.has(m)) twins.get(m).dispose()
      twins.delete(m)
      m.dispose()
    }
    texShown = ''
    texLoading = false
    texError = null
  }

  function clearGrid () {
    gridToken++
    clearGroup(gridInner)
    gridShown = ''
    gridLoading = false
    gridError = null
    gridFaces = 0
  }

  /** Draw the textured file `got`; resolves once its textures are decoded. */
  async function drawTextured (got) {
    const maps = await Promise.all(got.images.map(async (blob) => {
      try {
        const bitmap = await createImageBitmap(blob)
        const t = new THREE.Texture(bitmap)
        t.flipY = false
        t.wrapS = t.wrapT = THREE.RepeatWrapping
        t.colorSpace = THREE.SRGBColorSpace
        t.needsUpdate = true
        return t
      } catch (e) { return null }
    }))
    const byTexture = new Map()
    const materialOf = (i) => {
      if (i < 0 || !maps[i]) return material
      if (!byTexture.has(i)) {
        const m = new THREE.MeshBasicMaterial({ map: maps[i], transparent: true,
          opacity: material.uniforms.opacity.value, depthWrite: false, fog: false,
          side: THREE.DoubleSide, clippingPlanes: clip || null, ...COLOUR_PASS })
        texMaterials.push(m)
        byTexture.set(i, m)
      }
      return byTexture.get(i)
    }
    for (let i = 0; i < got.parts.length; i++) {
      const p = got.parts[i]
      const info = got.meta.parts[i]
      const geo = new THREE.BufferGeometry()
      geo.setAttribute('position', new THREE.BufferAttribute(p.pos, 3))
      geo.setAttribute('uv', new THREE.BufferAttribute(p.uv, 2))
      geo.setIndex(new THREE.BufferAttribute(p.idx, 1))
      addPart(texInner, geo, materialOf(info.texture), p.mats, info.instances,
        partOrder(i, got.parts.length))
    }
    const a = got.meta.anchor
    texInner.position.set(-a[0], -a[1], -a[2])
  }

  /** Fetch and draw the Textured display of the source on screen, once. */
  function loadTextured () {
    const source = state && state.source
    const want = sourceKey(source)
    if (!source || texShown === want) return
    clearTextured()
    texShown = want
    texLoading = true
    const mine = texToken
    render()
    fetch(`api/trace/textured/${source.game}/${encodeURIComponent(source.name)}`)
      .then(async (r) => {
        if (!r.ok) {
          let msg = `${r.status}`
          try { msg = (await r.json()).error || msg } catch (e) { }
          throw new Error(msg)
        }
        return parseTextured(await r.arrayBuffer())
      })
      .then(async (got) => {
        if (mine !== texToken) return
        await drawTextured(got)
        if (mine !== texToken) return
        texLoading = false
        place()
        render()
      }, (err) => {
        if (mine !== texToken) return
        texLoading = false
        texError = err.message || String(err)
        render()
      })
  }

  /** Fetch and draw the source's trap grids, once. */
  function loadGrid () {
    const source = state && state.source
    const want = sourceKey(source)
    if (!source || gridShown === want) return
    clearGrid()
    gridShown = want
    gridLoading = true
    const mine = gridToken
    render()
    fetch(`api/trace/grids/${source.game}/${encodeURIComponent(source.name)}`)
      .then(r => r.json())
      .then((got) => {
        if (mine !== gridToken) return
        if (got.error) throw new Error(got.error)
        gridLoading = false
        gridError = got.reason || null
        drawGrid(got.faces || [])
        place()
        render()
      }).catch((err) => {
        if (mine !== gridToken) return
        gridLoading = false
        gridError = err.message || String(err)
        render()
      })
  }

  /** Cell lines of the grid faces [{surface, origin, along, up, n, m}], one draw per surface. */
  function drawGrid (faces) {
    gridFaces = faces.length
    const lines = {}
    for (const f of faces) {
      const out = (lines[f.surface] = lines[f.surface] || [])
      const at = (s, t) => [0, 1, 2].map(k => f.origin[k] + f.along[k] * s + f.up[k] * t)
      const cuts = (count) => {
        const k = Math.floor(count + 1e-3)
        const got = []
        for (let i = 0; i <= k; i++) got.push(i / count)
        if (got[got.length - 1] < 1 - 1e-3) got.push(1)
        return got
      }
      for (const s of cuts(f.n)) out.push(...at(s, 0), ...at(s, 1))
      for (const t of cuts(f.m)) out.push(...at(0, t), ...at(1, t))
    }
    for (const [surface, pts] of Object.entries(lines)) {
      const geo = new THREE.BufferGeometry()
      geo.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3))
      const seg = new THREE.LineSegments(geo, gridMaterials[surface] || gridMaterials.Floor)
      seg.raycast = () => {}
      seg.renderOrder = GRID_ORDER
      gridInner.add(seg)
    }
    const a = meta ? meta.anchor : [0, 0, 0]
    gridInner.position.set(-a[0], -a[1], -a[2])
  }

  function drawGhost (got) {
    clearGroup(inner)
    for (let i = 0; i < got.parts.length; i++) {
      const p = got.parts[i]
      const geo = new THREE.BufferGeometry()
      geo.setAttribute('position', new THREE.BufferAttribute(p.pos, 3))
      geo.setIndex(new THREE.BufferAttribute(p.idx, 1))
      addPart(inner, geo, material, p.mats, got.meta.parts[i].instances,
        partOrder(i, got.parts.length))
    }
    const a = got.meta.anchor
    inner.position.set(-a[0], -a[1], -a[2])
  }

  function place () {
    if (!state || !meta) { root.visible = false; return }
    root.position.set(state.at[0], state.at[1], state.at[2])
    root.rotation.set(0, 0, (state.rotation || 0) * Math.PI / 180)
    root.scale.setScalar(state.scale || 1)
    const opacity = state.opacity ?? DEFAULT_OPACITY
    material.uniforms.opacity.value = opacity
    const wire = state.mode === 'wire'
    material.uniforms.wire.value = wire ? 1 : 0
    material.wireframe = wire
    for (const m of texMaterials) m.opacity = opacity
    //: a wireframe shows every edge, so it has no depth pass
    for (const m of inner.children) if (m.userData.pass === 'depth') m.visible = !wire
    //: the textured display stands in for the ghost once it has arrived
    const textured = state.mode === 'textured'
    if (textured) loadTextured()
    inner.visible = !textured || texLoading || !!texError
    texInner.visible = textured && !texLoading && !texError
    if (state.grid) loadGrid()
    gridInner.visible = !!state.grid
    if (meta) gridInner.position.set(-meta.anchor[0], -meta.anchor[1], -meta.anchor[2])
    root.visible = state.visible !== false
  }

  /** Draw `state.source`; `fitAfter` places it by fitOf once its meta is known. */
  function load (fitAfter) {
    const source = state.source
    const want = sourceKey(source)
    const mine = ++token
    shown = want
    meta = null
    error = null
    loading = true
    root.visible = false
    clearGhost()
    render()
    fetchFile(source).then((got) => {
      if (mine !== token) return
      drawGhost(got)
      meta = got.meta
      loading = false
      if (fitAfter) Object.assign(state, fitOf(source, meta))
      place()
      if (fitAfter) save()
      render()
    }, (err) => {
      if (mine !== token) return
      loading = false
      error = err.message || String(err)
      render()
    })
  }

  /** Where `source` goes when fitted: {at, rotation, scale}. */
  function fitOf (source, got) {
    const a = got.anchor
    const conv = conversion()
    if (source.game === 'omd3' && source.name === ownLevel()) {
      return { at: [...a], rotation: 0, scale: 1 }
    }
    if (source.game === 'omd2' && conv && conv.kind === 'omd2' && conv.name === source.name &&
        Array.isArray(conv.shift)) {
      // this level was converted from that scene (omd2record): line the original up
      return { at: a.map((v, i) => round(v + (conv.shift[i] || 0))), rotation: 0, scale: 1 }
    }
    const f = focus()
    if (!f) return { at: [...a], rotation: 0, scale: 1 }
    return { at: [round((f.lo[0] + f.hi[0]) / 2), round((f.lo[1] + f.hi[1]) / 2), round(f.lo[2])],
             rotation: 0, scale: 1 }
  }

  function save () {
    if (!key) return
    clearTimeout(saveTimer)
    const k = key
    const body = JSON.stringify(state || {})
    saveTimer = setTimeout(() => {
      fetch('api/trace/state/' + encodeURIComponent(k), {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Rift-Lord': '1' }, body,
      }).catch(() => {})
    }, SAVE_DELAY)
  }

  function choose (source) {
    const old = state || {}
    state = { source, at: [0, 0, 0], rotation: 0, scale: 1,
              opacity: old.opacity ?? DEFAULT_OPACITY, visible: true,
              mode: MODES.some(([v]) => v === old.mode) ? old.mode : 'ghost', grid: !!old.grid }
    load(true)
  }

  function none () {
    token++
    state = null
    shown = ''
    meta = null
    error = null
    loading = false
    clearGhost()
    root.visible = false
    save()
    render()
  }

  function fetchSources () {
    if (sources || sourcesError) return
    sourcesError = null
    fetch('api/trace/sources').then(r => r.json()).then((got) => {
      if (got.error) throw new Error(got.error)
      sources = got.games
      render()
    }).catch((err) => {
      sourcesError = err.message || String(err)
      render()
    })
  }

  /** Match the tool to the open level: hidden with none, its kept state with one. */
  function sync (plan) {
    const k = plan ? getKey() : null
    box.hidden = !k
    if (k === key) return
    const lvl = plan ? plan.level : null
    if (k && key && lvl === keyLevel && state) {
      // the same level under a new key (a first save made it a mission): its own kept
      // state if it has one, else this one carried over
      key = k
      const mine = token
      fetch('api/trace/state/' + encodeURIComponent(k)).then(r => r.json()).then((got) => {
        if (mine !== token || key !== k) return
        if (got && got.state) {
          state = { ...got.state, visible: state ? state.visible : false }
          if (sourceKey(state.source) !== shown) load(false)
          else { place(); render() }
        } else save()
      }).catch(() => {})
      return
    }
    key = k
    keyLevel = lvl
    token++
    state = null
    shown = ''
    meta = null
    error = null
    loading = false
    clearGhost()
    root.visible = false
    if (!k) { render(); return }
    fetchSources()
    const mine = token
    fetch('api/trace/state/' + encodeURIComponent(k)).then(r => r.json()).then((got) => {
      if (mine !== token || !got || !got.state) { render(); return }
      //: a level opens with its trace hidden; Show draws it
      state = { ...got.state, visible: false }
      render()
    }).catch(() => render())
    render()
  }

  function levelsOf (game) {
    const g = (sources || []).find(x => x.id === game)
    return g ? g.levels : []
  }

  function html () {
    const game = state ? state.source.game : ''
    const out = ['<label class="pick"><span>Game</span><select id="trgame">' +
      `<option value=""${game ? '' : ' selected'}>None</option>` +
      (sources || []).map(g => `<option value="${g.id}"${g.id === game ? ' selected' : ''}` +
        `${g.levels.length ? '' : ' disabled'}>${esc(g.label)}</option>`).join('') +
      '</select></label>']
    if (sourcesError) out.push(`<div class="hint bad">${esc(sourcesError)}</div>`)
    else if (!sources) out.push('<div class="hint"><span class="spinner"></span>Loading&hellip;</div>')
    if (!state) return out.join('')
    const levels = levelsOf(game)
    const name = state.source.name
    out.push('<label class="pick"><span>Level</span><select id="trlevel">' +
      (levels.some(l => l.name === name) ? ''
        : `<option value="${esc(name)}" selected>${esc(name)}</option>`) +
      levels.map(l => `<option value="${esc(l.name)}"${l.name === name ? ' selected' : ''}>` +
        `${esc(l.label)}</option>`).join('') + '</select></label>')
    if (loading) out.push('<div class="hint" id="trstatus"><span class="spinner"></span>Loading&hellip;</div>')
    else if (error) out.push(`<div class="hint bad" id="trstatus">${esc(error)}</div>`)
    else if (meta) out.push(`<div class="hint" id="trstatus">${thousands(meta.triangles)} triangles</div>`)
    out.push(`<label class="row"><span>Show</span><input id="trvisible" type="checkbox"` +
      `${state.visible !== false ? ' checked' : ''}></label>`)
    const mode = MODES.some(([v]) => v === state.mode) ? state.mode : 'ghost'
    out.push('<label class="pick"><span>Display</span><select id="trmode">' +
      MODES.map(([v, l]) => `<option value="${v}"${v === mode ? ' selected' : ''}>${l}</option>`)
        .join('') + '</select></label>')
    if (mode === 'textured' && texLoading) {
      out.push('<div class="hint"><span class="spinner"></span>Loading textures&hellip;</div>')
    } else if (mode === 'textured' && texError) {
      out.push(`<div class="hint bad">${esc(texError)}</div>`)
    }
    out.push(`<label class="row"><span>Trap Grid</span><input id="trgrid" type="checkbox"` +
      `${state.grid ? ' checked' : ''}></label>`)
    if (state.grid && gridLoading) {
      out.push('<div class="hint"><span class="spinner"></span>Loading&hellip;</div>')
    } else if (state.grid && gridError) {
      out.push(`<div class="hint bad">${esc(gridError)}</div>`)
    } else if (state.grid && !gridFaces) {
      out.push('<div class="hint">No trap grids</div>')
    }
    out.push('<label class="pick"><span>Opacity</span><input id="tropacity" type="range" ' +
      `min="0" max="1" step="0.05" value="${state.opacity ?? DEFAULT_OPACITY}"></label>`)
    const field = (id, label, value, step) => `<div class="field"><label for="${id}">` +
      `${label}</label><input id="${id}" type="number" step="${step}" value="${value}"></div>`
    out.push(field('trx', 'X', state.at[0], STEP.xy), field('try', 'Y', state.at[1], STEP.xy),
      field('trz', 'Height', state.at[2], STEP.z),
      field('trrot', 'Turn', state.rotation, STEP.rotation),
      field('trscale', 'Scale', state.scale, STEP.scale))
    out.push('<button id="trfit">Align to Level</button>')
    return out.join('')
  }

  function render () {
    body.innerHTML = html()
    wire()
  }

  function wire () {
    const q = (id) => body.querySelector('#' + id)
    q('trgame').addEventListener('change', (ev) => {
      const game = ev.target.value
      if (!game) { none(); return }
      const levels = levelsOf(game)
      const own = ownLevel()
      const name = game === 'omd3' && levels.some(l => l.name === own) ? own
        : (levels[0] && levels[0].name)
      if (name) choose({ game, name })
    })
    if (!state) return
    q('trlevel').addEventListener('change', (ev) => {
      choose({ game: state.source.game, name: ev.target.value })
    })
    q('trvisible').addEventListener('change', (ev) => {
      state.visible = ev.target.checked
      if (state.visible && !loading && (shown !== sourceKey(state.source) || !meta)) load(false)
      else place()
      save()
    })
    q('trmode').addEventListener('change', (ev) => {
      state.mode = ev.target.value
      place()
      save()
      render()
    })
    q('trgrid').addEventListener('change', (ev) => {
      state.grid = ev.target.checked
      place()
      save()
      render()
    })
    q('tropacity').addEventListener('input', (ev) => {
      state.opacity = +ev.target.value
      place()
      save()
    })
    const num = (id, apply) => q(id).addEventListener('input', (ev) => {
      const v = parseFloat(ev.target.value)
      if (!Number.isFinite(v)) return
      apply(v)
      place()
      save()
    })
    num('trx', (v) => { state.at[0] = v })
    num('try', (v) => { state.at[1] = v })
    num('trz', (v) => { state.at[2] = v })
    num('trrot', (v) => { state.rotation = v })
    num('trscale', (v) => { if (v > 0) state.scale = v })
    q('trfit').addEventListener('click', () => {
      if (!meta) return
      Object.assign(state, fitOf(state.source, meta))
      place()
      save()
      render()
    })
  }

  /** What the tool shows now, for tests. */
  function report () {
    let triangles = 0
    for (const m of colourParts(inner)) {
      const n = m.geometry.index ? m.geometry.index.count / 3 : 0
      triangles += m.isInstancedMesh ? n * m.count : n
    }
    const texParts = colourParts(texInner)
    //: every drawn part's colour pass against its depth pass: the same shader program
    //: inputs (vertex shader, map), less-or-equal, nearer by the margin, stencil once
    const pairs = [inner, texInner].flatMap(g => colourParts(g).map((c) => {
      const d = g.children.find(m => m.userData.pass === 'depth' && m.geometry === c.geometry)
      return [c.material, d && d.material]
    }))
    const passes = pairs.every(([c, d]) => !!d && !d.colorWrite && d.depthWrite &&
      c.depthTest && c.depthFunc === THREE.LessEqualDepth && !c.depthWrite &&
      c.type === d.type && c.vertexShader === d.vertexShader && c.map === d.map &&
      c.polygonOffset && d.polygonOffset &&
      c.polygonOffsetFactor < d.polygonOffsetFactor && c.polygonOffsetUnits < d.polygonOffsetUnits &&
      c.stencilWrite && c.stencilFunc === THREE.NotEqualStencilFunc && !d.stencilWrite)
    return { key, source: state ? sourceKey(state.source) : '', shown, visible: root.visible,
             colourPassesDepth: pairs.length > 0 && passes, passPairs: pairs.length,
             heights: ((h) => h.length ? [h.reduce((a, b) => Math.min(a, b)),
               h.reduce((a, b) => Math.max(a, b))] : null)(heights()),
             removed: meta ? meta.removed || 0 : null,
             stencil: !!(renderer && renderer.getContext().getContextAttributes()?.stencil),
             loading, error, triangles, parts: colourParts(inner).length,
             opacity: material.uniforms.opacity.value, mode: state ? state.mode : null,
             wireframe: material.wireframe, depthTest: material.depthTest,
             depthPass: inner.children.some(m => m.userData.pass === 'depth' && m.visible),
             culled: [...inner.children, ...texInner.children].some(m => m.frustumCulled),
             textured: texInner.visible && texParts.length > 0,
             textures: texMaterials.length, texLoading, texError,
             texturedParts: texParts.filter(m => m.material !== material).length,
             untexturedParts: texParts.filter(m => m.material === material).length,
             texOpacity: texMaterials.length ? texMaterials[0].opacity : null,
             grid: gridInner.visible, gridFaces, gridLoading, gridError,
             at: root.position.toArray(), sectionShown: !box.hidden,
             size: meta && meta.bounds ? [0, 1, 2].map(k => meta.bounds[1][k] - meta.bounds[0][k]) : null,
             games: (sources || []).map(g => ({ id: g.id, levels: g.levels.length })) }
  }

  /** For tests: show or hide every depth pass; null puts back what place() shows. */
  function depthPass (on) {
    const want = on === null ? !(state && state.mode === 'wire') : on
    for (const g of [inner, texInner]) {
      for (const m of g.children) if (m.userData.pass === 'depth') m.visible = want
    }
  }

  /** Scene-space heights (three.js y) of up to HEIGHT_SAMPLES vertices of the drawn
   * ghost, spread evenly over its parts and placements; [] when none is drawn. */
  function heights () {
    if (!root.visible || !meta) return []
    root.updateMatrixWorld(true)
    const group = texInner.visible ? texInner : inner
    const parts = colourParts(group)
    let total = 0
    for (const mesh of parts) total += mesh.geometry.attributes.position.count * (mesh.isInstancedMesh ? mesh.count : 1)
    const every = Math.max(1, Math.floor(total / HEIGHT_SAMPLES))
    const out = []
    const v = new THREE.Vector3()
    const m = new THREE.Matrix4()
    let k = 0
    for (const mesh of parts) {
      const pos = mesh.geometry.attributes.position
      const copies = mesh.isInstancedMesh ? mesh.count : 1
      for (let c = 0; c < copies; c++) {
        if (mesh.isInstancedMesh) mesh.getMatrixAt(c, m).premultiply(mesh.matrixWorld)
        else m.copy(mesh.matrixWorld)
        for (let i = (every - k % every) % every; i < pos.count; i += every) {
          out.push(v.fromBufferAttribute(pos, i).applyMatrix4(m).y)
        }
        k += pos.count
      }
    }
    return out
  }

  return { sync, report, choose, depthPass, heights, object: root }
}
