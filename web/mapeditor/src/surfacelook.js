// Surface weathering in the viewport: each level material whose master reads
// MPC_Enviro_Level (riftlord/mapeditor/surfacelook.py) draws its snow, sand, ice, moss and
// stone colour the way that master blends them, from the Surfaces values the map has
// (Map Settings, plan.world.surfaces over the level's own). The values are uniforms
// shared by every such material, so a change in the panel redraws without recompiling.
// A placement's vertex paint (its mean colour) rides on an instanced attribute.
import * as THREE from 'three'

//: MPC_Enviro_Level's values, one uniform each, shared by every weathering material.
const mpc = {}
const scalar = (name) => (mpc[name] = mpc[name] || { value: 0 })
const colour = (name) => (mpc[name] = mpc[name] || { value: new THREE.Color(1, 1, 1) })

/** Set the shared values: `fields` from /api/mapinfo (surfaceFields, with defaults),
 * `shown` the level's own (mapinfo surfaces), `own` the plan's world.surfaces. */
export function setValues (fields, shown, own) {
  for (const f of fields || []) {
    const v = (own && f.key in own) ? own[f.key]
      : (shown && f.key in shown) ? shown[f.key] : f.default
    if (f.kind === 'colour') {
      const c = Array.isArray(v) ? v : [1, 1, 1]
      colour(f.key).value.setRGB(c[0], c[1], c[2], THREE.LinearSRGBColorSpace)
    } else scalar(f.key).value = Number(v) || 0
  }
}

/** A shared value as drawn (for the UI smoke suite). */
export function value (name) {
  const u = mpc[name]
  return u ? (u.value.isColor ? u.value.toArray() : u.value) : null
}

const vec3 = (a) => new THREE.Vector3(a[0], a[1], a[2])

/** Uniforms and defines of one material's description (surfacelook.describe). */
function program (d) {
  const u = {}
  const defs = { SURF_SNOW_ALL: 0, SURF_SAND_ALL: 0 }
  for (const layer of ['snow', 'sand']) {
    const s = d[layer]
    if (!s) continue
    const L = layer.toUpperCase()
    defs['SURF_' + L] = ''
    if (s.up === 'blend') defs['SURF_' + L + '_BLEND'] = ''
    if (s.all) defs['SURF_' + L + '_ALL'] = s.all === 'b' ? 2 : 1
    if (s.clamp) defs['SURF_' + L + '_CLAMP'] = ''
    if (d.family === 'Master_SnowTrees' || d.family === 'Master_Snow_Mountains') {
      defs['SURF_' + L + '_NOPAINT'] = ''
    }
    u['s' + L + 'Amount'] = scalar(s.mpc)
    u['s' + L + 'Over'] = { value: s.over }
    u['s' + L + 'Blend'] = { value: new THREE.Vector2(s.blend, s.contrast) }
    u['s' + L + 'All'] = { value: new THREE.Vector2(s.allMean || 0, s.power || 1) }
    u['s' + L + 'Col'] = { value: vec3(s.colour) }
    u['s' + L + 'Tint'] = s.mpcColour ? colour(s.mpcColour) : { value: new THREE.Color(1, 1, 1) }
  }
  if (d.ice) {
    defs.SURF_ICE = ''
    u.sIce = scalar('UseIceCastle')
    u.sIceTint = { value: vec3(d.ice.tint) }
    u.sIceInner = { value: vec3(d.ice.inner).multiplyScalar(d.ice.luminance) }
    u.sIceOuter = { value: vec3(d.ice.outer) }
    u.sIceFresnel = { value: d.ice.fresnel }
  }
  if (d.moss) {
    defs[d.moss.kind === 'rock' ? 'SURF_MOSS_ROCK' : 'SURF_MOSS_CASTLE'] = ''
    u.sMoss = scalar(d.moss.kind === 'rock' ? 'UseMossRock' : 'UseMossCastle')
    u.sMossCol = { value: vec3(d.moss.colour) }
    u.sMossFuzz = { value: new THREE.Vector3(d.moss.fuzzPower, d.moss.fuzzBright, d.moss.fuzzDark) }
    u.sMossBlend = {
      value: d.moss.kind === 'rock'
        ? new THREE.Vector3(d.moss.falloff, d.moss.max, d.moss.opacity)
        : new THREE.Vector3(d.moss.scale, 0, 0),
    }
  }
  if (d.stone) {
    defs.SURF_STONE = ''
    u.sDesat = scalar('DesaturateCastleStone')
    u.sStone = colour('ColorStoneCastle')
    u.sFinal = { value: vec3(d.stone.finalBrightness) }
  }
  return { u, defs }
}

const VERTEX_HEAD = `
attribute vec4 aPaint;
uniform vec4 sObjPaint;
varying float vSurfUp;
varying vec4 vSurfPaint;
`
//: Unreal's up is the view's +Y (main.js world), and the viewport's normals point
//: inward (Unreal winds the other way), so up is -y of the world normal.
const VERTEX_BODY = `
{
  mat3 surfM = mat3(modelMatrix);
#ifdef USE_INSTANCING
  surfM = surfM * mat3(instanceMatrix);
#endif
  vSurfUp = -normalize(transpose(inverse(surfM)) * objectNormal).y;
#if defined(USE_INSTANCING) && defined(SURF_PAINT)
  vSurfPaint = aPaint;
#elif defined(USE_INSTANCING)
  vSurfPaint = vec4(1.0);
#else
  vSurfPaint = sObjPaint;
#endif
}
`
const FRAGMENT_HEAD = `
varying float vSurfUp;
varying vec4 vSurfPaint;
uniform float sSNOWAmount, sSNOWOver, sSANDAmount, sSANDOver, sIce, sMoss, sDesat, sIceFresnel;
uniform vec3 sIceInner, sIceOuter;
uniform vec2 sSNOWBlend, sSNOWAll, sSANDBlend, sSANDAll;
uniform vec3 sSNOWCol, sSNOWTint, sSANDCol, sSANDTint, sIceTint, sMossCol, sMossFuzz,
  sMossBlend, sStone, sFinal;
float surfUp (float n, vec2 blend, bool useBlend) {
  if (useBlend) return n > 0.0 ? abs(n) * clamp(pow(n, blend.x) * blend.y, 0.0, 1.0) : 0.0;
  return abs(n) * min(3.0 * max(n, 0.0), 1.0);
}
`
//: The masters' order (surfacelook.py): stone, moss, ice, snow, sand. surfGlow is the
//: ice's emissive, added after three's own (FRAGMENT_GLOW).
const FRAGMENT_BODY = `
vec3 surfGlow = vec3(0.0);
{
  float n = vSurfUp;
  vec4 P = vSurfPaint;
  vec3 c = diffuseColor.rgb;
#ifdef SURF_STONE
  c /= max(sFinal, vec3(1e-4));
  c = mix(c, vec3(dot(c, vec3(0.3, 0.59, 0.11))), sDesat);
  c = mix(2.0 * c * sStone, 1.0 - 2.0 * (1.0 - c) * (1.0 - sStone), step(0.5, c));
  c *= sFinal;
#endif
#if defined(SURF_MOSS_ROCK) || defined(SURF_MOSS_CASTLE)
#ifdef SURF_MOSS_ROCK
  float mm = clamp((n + 1.0) * 0.5 * (sMossBlend.y - sMossBlend.x) + sMossBlend.x, 0.0, 1.0)
    * P.a * sMossBlend.z * sMoss;
#else
  float mm = clamp(sMossBlend.x * sMoss, 0.0, 1.0);
#endif
  float vv = clamp(dot(normal, normalize(vViewPosition)), 0.0, 1.0);
  float fz = 1.0 + pow(1.0 - vv, sMossFuzz.x) * sMossFuzz.y - vv * sMossFuzz.z;
  c = mix(c, sMossCol * fz, mm);
#endif
#ifdef SURF_ICE
  float im = sIce * (1.0 - P.g);
  c = mix(c, sIceTint, im);
  float iv = clamp(dot(normal, normalize(vViewPosition)), 0.0, 1.0);
  surfGlow = im * mix(sIceOuter, sIceInner, iv > 0.0 ? pow(iv, sIceFresnel) : 0.0);
#endif
#ifdef SURF_SNOW
  {
#ifdef SURF_SNOW_BLEND
    float up = surfUp(n, sSNOWBlend, true);
#else
    float up = surfUp(n, sSNOWBlend, false);
#endif
#ifndef SURF_SNOW_NOPAINT
    up *= P.r;
#endif
    float all = 0.0;
#if SURF_SNOW_ALL == 1
    all = sSNOWAll.x * pow(1.0 - P.b, sSNOWAll.y);
#elif SURF_SNOW_ALL == 2
    all = sSNOWAll.x * pow(P.b, sSNOWAll.y);
#endif
    float sm = sSNOWAmount * sSNOWOver * (up + all);
    c = mix(c, sSNOWCol * sSNOWTint, sm);
    surfGlow *= 1.0 - sm;
#ifdef SURF_SNOW_CLAMP
    c = clamp(c, 0.0, 1.0);
#endif
  }
#endif
#ifdef SURF_SAND
  {
#ifdef SURF_SAND_BLEND
    float up = surfUp(n, sSANDBlend, true);
#else
    float up = surfUp(n, sSANDBlend, false);
#endif
    float all = 0.0;
#if SURF_SAND_ALL == 1
    all = sSANDAll.x * pow(1.0 - P.b, sSANDAll.y);
#elif SURF_SAND_ALL == 2
    all = sSANDAll.x * pow(P.b, sSANDAll.y);
#endif
    float dm = sSANDAmount * sSANDOver * clamp(P.r * up + all, 0.0, 1.0);
    c = clamp(mix(c, sSANDCol * sSANDTint, dm), 0.0, 1.0);
    surfGlow *= 1.0 - dm;
  }
#endif
  diffuseColor.rgb = c;
}
`

/** Make `mat` (a MeshLambertMaterial) draw description `d`'s weathering; `painted` when
 * its instanced meshes carry the aPaint attribute. */
function weather (mat, d, painted) {
  const { u, defs } = program(d)
  if (painted) defs.SURF_PAINT = ''
  //: the uniforms a description leaves out still need a value to declare
  for (const k of ['sSNOWAmount', 'sSNOWOver', 'sSANDAmount', 'sSANDOver', 'sIce', 'sMoss', 'sDesat',
    'sIceFresnel']) {
    if (!u[k]) u[k] = { value: 0 }
  }
  for (const k of ['sSNOWBlend', 'sSNOWAll', 'sSANDBlend', 'sSANDAll']) {
    if (!u[k]) u[k] = { value: new THREE.Vector2() }
  }
  for (const k of ['sSNOWCol', 'sSNOWTint', 'sSANDCol', 'sSANDTint', 'sIceTint', 'sIceInner', 'sIceOuter',
    'sMossCol',
    'sMossFuzz', 'sMossBlend', 'sStone', 'sFinal']) {
    if (!u[k]) u[k] = { value: new THREE.Vector3(1, 1, 1) }
  }
  u.sObjPaint = { value: new THREE.Vector4(1, 1, 1, 1) }
  mat.userData.surfUniforms = u
  mat.defines = Object.assign({}, mat.defines || {}, defs)
  const key = 'surf:' + JSON.stringify(defs)
  mat.customProgramCacheKey = () => key
  mat.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, u)
    shader.vertexShader = VERTEX_HEAD + shader.vertexShader.replace(
      '#include <beginnormal_vertex>', '#include <beginnormal_vertex>\n' + VERTEX_BODY)
    shader.fragmentShader = FRAGMENT_HEAD + shader.fragmentShader.replace(
      '#include <normal_fragment_maps>', '#include <normal_fragment_maps>\n' + FRAGMENT_BODY)
      .replace('#include <emissivemap_fragment>',
        '#include <emissivemap_fragment>\ntotalEmissiveRadiance += surfGlow;')
  }
  mat.userData.surfaceDone = true
  mat.needsUpdate = true
}

/** Give an InstancedMesh its own geometry, sharing the level's buffers, with paint `arr`. */
function withPaint (inst, arr) {
  const was = inst.geometry
  const geo = new THREE.BufferGeometry()
  for (const [k, a] of Object.entries(was.attributes)) geo.setAttribute(k, a)
  geo.setIndex(was.index)
  for (const g of was.groups) geo.addGroup(g.start, g.count, g.materialIndex)
  geo.boundingBox = was.boundingBox
  geo.boundingSphere = was.boundingSphere
  geo.setAttribute('aPaint', new THREE.InstancedBufferAttribute(arr, 4))
  inst.geometry = geo
}

//: material name -> description, or null once asked and not weathering
const described = new Map()
//: piece id -> [r, g, b, a] of the open level
let paint = {}
let paintLevel = null

/** Give every drawn material under `root` tagged `userData.surfacePath` its weathering.
 * `pieceOf(inst, i)` is the piece instance i of an InstancedMesh draws. */
export async function attach (root, levelName, scratch, fetchJson, pieceOf) {
  const mats = []
  const insts = []
  root.traverse((o) => {
    if (!o.isMesh) return
    for (const m of Array.isArray(o.material) ? o.material : [o.material]) {
      if (!m || !m.userData || !m.userData.surfacePath) continue
      //: a plain mesh sets its own paint as it draws (paintObject), white unless given
      if (!o.isInstancedMesh && !o.userData.surfHooked) paintObject(o, o.userData.surfPaint || null)
      if (!m.userData.surfaceDone) {
        mats.push(m)
        if (o.isInstancedMesh) insts.push(o)
      }
    }
  })
  if (!mats.length) return
  const ask = [...new Set(mats.map(m => m.userData.surfacePath))].filter(p => !described.has(p))
  const wantPaint = paintLevel !== levelName
  if (ask.length || wantPaint) {
    const got = await fetchJson('api/surfacelook/' + encodeURIComponent(levelName), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Rift-Lord': '1' },
      body: JSON.stringify({ materials: ask, scratch: !!scratch }),
    })
    for (const p of ask) described.set(p, (got.materials || {})[p] || null)
    if (wantPaint) {
      paint = got.paint || {}
      paintLevel = levelName
    }
  }
  //: an InstancedMesh whose placements paint gets the attribute; its materials read it
  const paintedMats = new Set()
  for (const inst of new Set(insts)) {
    let any = false
    const arr = new Float32Array(inst.count * 4).fill(1)
    for (let i = 0; i < inst.count; i++) {
      const piece = pieceOf(inst, i)
      const p = piece && paint[String(piece.id)]
      if (p) {
        arr.set(p, i * 4)
        any = true
      }
    }
    if (!any) continue
    withPaint(inst, arr)
    for (const m of Array.isArray(inst.material) ? inst.material : [inst.material]) paintedMats.add(m)
  }
  //: an unpainted InstancedMesh sharing a painted material reads white paint
  for (const inst of new Set(insts)) {
    if (inst.geometry.attributes.aPaint) continue
    const ms = Array.isArray(inst.material) ? inst.material : [inst.material]
    if (!ms.some(m => paintedMats.has(m))) continue
    withPaint(inst, new Float32Array(inst.count * 4).fill(1))
  }
  for (const m of mats) {
    const d = described.get(m.userData.surfacePath)
    if (d) weather(m, d, paintedMats.has(m))
    else m.userData.surfaceDone = true
  }
}

/** The paint a level piece shipped with ([r, g, b, a] 0..1), or null. */
export function shipped (id) {
  return paint[String(id)] || null
}

const materialsOf = (o) => Array.isArray(o.material) ? o.material : [o.material]

/** Draw instance `i` of InstancedMesh `inst` with paint `rgba`; the meshes under `root`
 * sharing its materials get white paint, and those materials start reading it. */
export function paintInstance (root, inst, i, rgba) {
  if (!inst) return
  if (!inst.geometry.attributes.aPaint) {
    withPaint(inst, new Float32Array(inst.count * 4).fill(1))
    const mats = materialsOf(inst).filter(m => m && m.userData.surfUniforms)
    for (const m of mats) {
      if ('SURF_PAINT' in m.defines) continue
      m.defines = Object.assign({}, m.defines, { SURF_PAINT: '' })
      const key = 'surf:' + JSON.stringify(m.defines)
      m.customProgramCacheKey = () => key
      m.needsUpdate = true
    }
    root.traverse((o) => {
      if (!o.isInstancedMesh || o === inst || o.geometry.attributes.aPaint) return
      if (materialsOf(o).some(m => mats.includes(m))) {
        withPaint(o, new Float32Array(o.count * 4).fill(1))
      }
    })
  }
  const a = inst.geometry.attributes.aPaint
  a.setXYZW(i, rgba[0], rgba[1], rgba[2], rgba[3])
  a.needsUpdate = true
}

/** Draw a plain mesh (an added piece) and its children with paint `rgba`. */
export function paintObject (obj, rgba) {
  obj.traverse((o) => {
    if (!o.isMesh) return
    o.userData.surfPaint = rgba
    o.userData.surfHooked = true
    o.onBeforeRender = (_r, _s, _c, _g, material) => {
      const u = material && material.userData.surfUniforms
      if (!u) return
      const p = o.userData.surfPaint || [1, 1, 1, 1]
      u.sObjPaint.value.set(p[0], p[1], p[2], p[3])
      material.uniformsNeedUpdate = true
    }
  })
}
