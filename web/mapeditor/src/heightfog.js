/** Unreal's exponential height fog, drawn by every three.js material that takes fog.
 *
 * The math is UE 4.27's (Shaders/Private/HeightFogCommon.ush, one fog layer): density
 * and height falloff are the component's values / 1000; the fog's line integral from
 * StartDistance to the pixel, in exp2; opacity capped at FogMaxOpacity. Directional
 * inscattering (the glow towards the sun) and volumetric fog are left out. The colour
 * is the scene fog's colour, so a sky choice's tint carries through.
 *
 * All materials share one Float32Array uniform, so a change reaches every one of them.
 */
import * as THREE from 'three'

//: Unreal's own defaults for a fog component that leaves a value unset.
export const FOG_DEFAULTS = { fogDensity: 0.02, fogFalloff: 0.2, fogOpacity: 1.0,
                              fogStart: 0.0, fogHeight: 0.0 }

//: Unreal divides the component's density and height falloff by this.
const UE_FOG_SCALE = 1000

//: [0..15] three-world to level matrix, 16 density, 17 falloff, 18 height,
//: 19 start distance, 20 least see-through, 21 on (1) or off (0).
const params = new Float32Array(24)
const inv = new THREE.Matrix4()

const PARS_VERTEX = `
#ifdef USE_FOG
  varying vec3 vRlFogPos;
  uniform float rlHeightFog[24];
#endif
`
const VERTEX = `
#ifdef USE_FOG
  mat4 rlM = mat4(rlHeightFog[0], rlHeightFog[1], rlHeightFog[2], rlHeightFog[3],
    rlHeightFog[4], rlHeightFog[5], rlHeightFog[6], rlHeightFog[7],
    rlHeightFog[8], rlHeightFog[9], rlHeightFog[10], rlHeightFog[11],
    rlHeightFog[12], rlHeightFog[13], rlHeightFog[14], rlHeightFog[15]);
  vRlFogPos = (rlM * (inverse(viewMatrix) * mvPosition)).xyz;
#endif
`
const PARS_FRAGMENT = `
#ifdef USE_FOG
  uniform vec3 fogColor;
  varying vec3 vRlFogPos;
  uniform float rlHeightFog[24];
#endif
`
const FRAGMENT = `
#ifdef USE_FOG
  if (rlHeightFog[21] > 0.5) {
    mat4 rlM = mat4(rlHeightFog[0], rlHeightFog[1], rlHeightFog[2], rlHeightFog[3],
      rlHeightFog[4], rlHeightFog[5], rlHeightFog[6], rlHeightFog[7],
      rlHeightFog[8], rlHeightFog[9], rlHeightFog[10], rlHeightFog[11],
      rlHeightFog[12], rlHeightFog[13], rlHeightFog[14], rlHeightFog[15]);
    vec3 rlCam = (rlM * vec4(cameraPosition, 1.0)).xyz;
    vec3 rlRay = vRlFogPos - rlCam;
    float rlLen = length(rlRay);
    float rlSee = 1.0;
    if (rlLen > rlHeightFog[19]) {
      float rlT = rlHeightFog[19] / max(rlLen, 0.001);
      float rlZ0 = rlCam.z + rlRay.z * rlT;
      float rlOrigin = rlHeightFog[16] * exp2(-rlHeightFog[17] * (rlZ0 - rlHeightFog[18]));
      float rlFall = max(-127.0, rlHeightFog[17] * rlRay.z * (1.0 - rlT));
      float rlLine = abs(rlFall) > 0.0001 ? (1.0 - exp2(-rlFall)) / rlFall
                                         : log(2.0) - 0.5 * log(2.0) * log(2.0) * rlFall;
      rlSee = max(clamp(exp2(-rlOrigin * rlLine * rlLen * (1.0 - rlT)), 0.0, 1.0),
                  rlHeightFog[20]);
    }
    gl_FragColor.rgb = gl_FragColor.rgb * rlSee +
      linearToOutputTexel(vec4(fogColor, 1.0)).rgb * (1.0 - rlSee);
  }
#endif
`

/** Swap three.js's fog for the height fog. Call once, before any material is drawn. */
export function installHeightFog () {
  THREE.ShaderChunk.fog_pars_vertex = PARS_VERTEX
  THREE.ShaderChunk.fog_vertex = VERTEX
  THREE.ShaderChunk.fog_pars_fragment = PARS_FRAGMENT
  THREE.ShaderChunk.fog_fragment = FRAGMENT
  for (const shader of Object.values(THREE.ShaderLib)) {
    if (shader.uniforms && shader.uniforms.fogColor) {
      shader.uniforms.rlHeightFog = { value: params }
    }
  }
}

/** Set the fog from a level's values ({fogDensity, fogFalloff, fogOpacity, fogStart,
 * fogHeight}, missing ones at Unreal's defaults). */
export function setHeightFog (values) {
  const v = { ...FOG_DEFAULTS, ...(values || {}) }
  params[16] = v.fogDensity / UE_FOG_SCALE
  params[17] = v.fogFalloff / UE_FOG_SCALE
  params[18] = v.fogHeight
  params[19] = v.fogStart
  params[20] = 1 - v.fogOpacity
}

/** Keep the level frame current and switch the fog on or off. `level` is the group the
 * level is drawn in (its local space is Unreal's). */
export function updateHeightFog (level, on) {
  inv.copy(level.matrixWorld).invert()
  params.set(inv.elements, 0)
  params[21] = on ? 1 : 0
}
