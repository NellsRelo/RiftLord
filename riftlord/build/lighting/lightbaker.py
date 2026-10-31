"""Bake a level's light maps on the GPU: direct light from Static lights and the sky light.

    VENV/python lightbaker.py bake Scenario_09 --tag Castle --out DIR [--spec spec.json]
    VENV/python lightbaker.py compare Scenario_09 --tag Castle --out DIR

Step 3 of the lightmap baker (workshop/research/LIGHTMAP-BAKER.md). Runs in the
render venv (moderngl over EGL, `VENV_PYTHON`); the build hook in
riftlord/lightbake.py starts it as a subprocess.

What it computes, per light map texel, is Lightmass's direct lighting
(UnrealLightmass: LightmassScene.cpp `GetDirectIntensity`, TextureMapping.cpp area
shadows, LightingSystem.inl `PointLightWorldSpace`, FinalGather.cpp sky gather) and
the conversion to light map coefficients (LightingSystem.cpp
`ConvertToLightSampleHelper`):

* **Texels.** Each atlas of the sublevel is rasterised in light map UV space
  (`uv * CoordinateScale + CoordinateBias`), one centre pass and `JITTER` offset
  passes standing in for conservative rasterisation; each texel gets a world
  position, an interpolated vertex normal and a texel radius.
* **Rays.** The level's occluders (lightscene.components) form a two-level BVH:
  one per distinct mesh, one over the instances. A compute shader traces shadow
  rays to `SHADOW_RAYS` points on each light's surface (shadow factor raised to
  `SHADOW_EXPONENT`) and `SKY_RAYS` uniform hemisphere rays for the sky light.
* **Samples.** Each contribution is gathered as Lightmass does: luminance SH (4),
  SH correction along the normal (1), incident light times N.L (3); the CPU turns
  them into Coefficients[0] and [1] and lightcodec quantises them.

Bounced light is not computed (DIRECT_ONLY); see the research note.
"""

import argparse
import json
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))

#: The render venv: moderngl over the system's numpy.
VENV_PYTHON = os.path.expanduser('~/.cache/rift-lord/render-venv/bin/python')
#: Triangles (or instances) per BVH leaf.
LEAF_SIZE = 4
#: Bits per axis of the Morton code the BVH sorts primitives by.
MORTON_BITS = 10
#: Shadow rays per light per texel (Lightmass: NumShadowRays 8 + NumPenumbraShadowRays 8).
SHADOW_RAYS = 16
#: Angular size (source radius / distance) at which a light gets all SHADOW_RAYS; smaller
#: sources get proportionally fewer, at least one (a 1 uu point light casts hard shadows).
PENUMBRA_ANGLE = 0.05
#: Lightmass FLightingSettings::ShadowExponent default: shadow factor ** this.
SHADOW_EXPONENT = 2.0
#: Uniform hemisphere rays per texel for the sky light.
SKY_RAYS = 256
#: Of those, how many find their closest hit, to count back faces (inside geometry);
#: the rest only test for any hit, which is cheaper.
BACKFACE_RAYS = 32
#: Ray start offsets (BaseLightmass.ini [DevOptions.StaticLightingSceneConstants]):
#: along the ray, and along the normal as a fraction of the texel radius.
RAY_OFFSET = 0.1
NORMAL_OFFSET_SCALE = 0.5
#: Smallest texel radius (SmallestTexelRadius) and the smallest normal offset we use.
SMALLEST_TEXEL_RADIUS = 0.1
MIN_NORMAL_OFFSET = 0.5
#: Sub-texel offsets (in texels) of the extra rasterisation passes that pick up texels a
#: triangle touches without covering the centre; earlier passes win.
JITTER = ((0.0, 0.0), (0.3, 0.3), (-0.3, 0.3), (0.3, -0.3), (-0.3, -0.3),
          (0.49, 0.0), (-0.49, 0.0), (0.0, 0.49), (0.0, -0.49))
#: Most texels traced per compute dispatch, and most rays (closest-hit rays counted
#: CLOSEST_COST times; every light in reach counted SHADOW_RAYS times, an upper bound).
#: A dispatch over ~2 s trips the NVIDIA driver's context-switch timeout (Xid 109, seen
#: 2026-10-16 with 154 lava lights) and returns NaNs; this keeps each one short.
DISPATCH_TEXELS = 1 << 15
DISPATCH_RAYS = 1 << 22
CLOSEST_COST = 2
MIN_DISPATCH_TEXELS = 256
#: Compute work group size.
GROUP = 64
#: Far end of a sky ray (uu); beyond the farthest occluder of any shipped level.
SKY_RAY_LENGTH = 1.0e6
#: Emissive instances the shader tests one by one; more fall back to closest hits.
MAX_EMITTERS = 32
#: Ray tracing backend: 'auto' (OptiX through Mitsuba when it loads, else OpenGL),
#: 'optix' or 'opengl'. Env LIGHTBAKER_BACKEND overrides.
BACKEND = 'auto'
#: Texels handed to the OptiX kernel at once (bounds its host memory).
OPTIX_TEXELS = 1 << 16


def choose_backend():
    want = os.environ.get('LIGHTBAKER_BACKEND', BACKEND)
    if want == 'opengl':
        return 'opengl'
    from riftlord.build.lighting import lighttrace
    if lighttrace.available():
        return 'optix'
    if want == 'optix':
        raise SystemExit('lightbaker: the OptiX backend does not load here')
    return 'opengl'


#: Instance flag bit (beside the BLAS root) for two-sided meshes, whose back faces do not
#: count as inside geometry.
TWO_SIDED_BIT = 0x40000000
#: Lightmass's SH and luminance constants.
LUMA = np.array([0.3, 0.59, 0.11])
SH_C0 = 0.282095
SH_C1 = 0.488603
SH_DIFFUSE_C1 = 0.325735
#: Shipped luminance below which relative errors are measured against this floor.
REL_FLOOR = 0.05
#: A texel is dominated by the Static lights when their shading exceeds this many times
#: the sky's (comparison only).
LIGHT_DOMINATED = 3.0
#: ours / shipped above this counts as overshoot: indirect light is never negative, so a
#: direct-only bake should not exceed the shipped total by more than noise.
OVERSHOOT = 1.25
#: Ratio samples kept per mesh for the light-dominated statistics, and the bins of
#: lights-only shading they are reported in.
LIT_SAMPLES = 400
LIT_BINS = ((0.0, 0.1), (0.1, 0.3), (0.3, 1.0), (1.0, 3.0), (3.0, 1e9))
#: Comparison images: meshes with more mapped texels than this, and a second set of
#: meshes whose texels are mostly dominated by the Static lights.
IMAGE_MIN_TEXELS = 400
IMAGE_LIT_SHARE = 0.5
#: Lava calibration: a texel counts when the lava's shading exceeds LAVA_MIN and
#: LAVA_DOMINATED times everything else's.
LAVA_MIN = 0.02
LAVA_DOMINATED = 2.0
#: Lava surface radiance (linear rgb), fitted on North Wing by `calibrate` (joint fit,
#: research note section 3c): what an open lava surface emits into the gather.
LAVA_RADIANCE = (0.547, 0.339, 0.198)
#: North Wing's lava lights, measured: PointLights 17-105 uu over the lava (median 34),
#: nearest neighbour median 925 uu, Intensity 10, AttenuationRadius 1600, no inverse
#: square, LightFalloffExponent 3, colour linear (1.0, 0.26, 0.0). Bakes place lights like
#: them over open lava (riftlord.lightbake.bake_spec) when a plan makes lava.
LAVA_LIGHT = {'kind': 'point', 'brightness': 10.0, 'radius': 1600.0, 'falloff': 3.0,
              'inverse_squared': False, 'colour': [1.0, 0.26, 0.0], 'source_radius': 1.0,
              'source_length': 0.0, 'cast_shadows': True, 'indirect': 1.5}
#: Denoise Coefficients[0] per UV chart with Intel Open Image Denoise (pyoidn, RTLightmap
#: filter) before quantising; charts under MIN_DENOISE_TEXELS are left as they are.
DENOISE = False
MIN_DENOISE_TEXELS = 16
#: Texels of neighbourhood a chart's crop keeps (filled from the chart by dilation).
DENOISE_MARGIN = 4
#: Chart ids are packed as component * CHART_STRIDE + chart.
CHART_STRIDE = 1 << 20
#: Welding scales for chart detection: UV to 1/65536, positions to 1/100 uu.
UV_WELD_SCALE = 65536.0
POSITION_WELD_SCALE = 100.0
#: Quality presets (measured on Front Lawn, research note section 3d): FINAL halves the
#: old settings' sampling error for about 1.5x the time on OptiX; PREVIEW is the old
#: setting. Denoising is off in both: on OptiX tracing more rays is cheaper and better.
#: On the OpenGL fallback the same names cost 15-80x more, so they trace fewer rays.
QUALITIES = {('optix', 'final'): {'sky_rays': 1024, 'shadow_rays': 32, 'denoise': False},
             ('optix', 'preview'): {'sky_rays': 256, 'shadow_rays': 16, 'denoise': False},
             ('opengl', 'final'): {'sky_rays': 256, 'shadow_rays': 16, 'denoise': False},
             ('opengl', 'preview'): {'sky_rays': 64, 'shadow_rays': 8, 'denoise': False}}
#: Quality a bake uses unless its spec names one.
QUALITY = 'final'
CURRENT_QUALITY = None
#: How far (uu) past a sublevel's receivers an occluder or light still counts toward its
#: cache key.
SUBLEVEL_REACH = 5000.0
#: Light the sky term from the chosen sky material's own texture (sky_from_texture)
#: rather than the home level's cubemap (user decision 2026-10-16).
SKY_FROM_TEXTURE = True
#: Mix the sky texture with the height fog it is seen through (sky_from_texture). Off:
#: the user's choice is the sky's own texture; kept for comparison.
SKY_FOG = False
#: Bounced light is not baked yet.
DIRECT_ONLY = True
#: Share of a texel's sky rays hitting back faces above which it lies inside geometry
#: (Lightmass FinalGather.cpp: BackfacingHitsFraction > .5).
INSIDE_GEOMETRY_SHARE = 0.5
#: Dilation steps into unmapped texels of an allocation before nearest-fill.
DILATE_STEPS = 4

#: Light kinds as the shader numbers them.
KIND_CODE = {'point': 0, 'spot': 1, 'rect': 2}


# ----------------------------------------------------------------------- BVH

def _morton(c, lo, hi):
    span = np.maximum(hi - lo, 1e-9)
    q = np.clip(((c - lo) / span * ((1 << MORTON_BITS) - 1)).astype(np.int64),
                0, (1 << MORTON_BITS) - 1)
    code = np.zeros(len(c), np.int64)
    for bit in range(MORTON_BITS):
        for axis in range(3):
            code |= ((q[:, axis] >> bit) & 1) << (3 * bit + axis)
    return code


def build_bvh(lo, hi, leaf=LEAF_SIZE):
    """(node_lo (k, 4) f32, node_hi (k, 4) f32, order) over primitive boxes.

    Primitives are sorted by the Morton code of their centres and split at the middle
    of their range, level by level; children of a node are adjacent. `lo.w` holds the
    left child (internal) or first primitive (leaf) and `hi.w` the primitive count
    (0 for internal), both as int32 bits; `order` is the primitive order leaves use.
    """
    n = len(lo)
    c = (lo + hi) * 0.5
    order = np.argsort(_morton(c, c.min(0), c.max(0)), kind='stable')
    slo, shi = lo[order], hi[order]
    cap = max(2 * n, 1)
    left = np.zeros(cap, np.int32)
    count = np.zeros(cap, np.int32)
    nlo = np.zeros((cap, 3), np.float32)
    nhi = np.zeros((cap, 3), np.float32)
    ids, s, e = np.array([0]), np.array([0]), np.array([n])
    total = 1
    internal_levels = []
    while len(ids):
        size = e - s
        is_leaf = size <= leaf
        lid = ids[is_leaf]
        left[lid] = s[is_leaf]
        count[lid] = size[is_leaf]
        sid, ss, se = ids[~is_leaf], s[~is_leaf], e[~is_leaf]
        k = len(sid)
        if not k:
            break
        child = total + 2 * np.arange(k)
        total += 2 * k
        left[sid] = child
        internal_levels.append(sid)
        mid = ss + (se - ss) // 2
        ids = np.stack([child, child + 1], 1).ravel()
        s = np.stack([ss, mid], 1).ravel()
        e = np.stack([mid, se], 1).ravel()
    leaves = np.nonzero(count[:total])[0]
    firsts = left[leaves]
    srt = np.argsort(firsts)
    leaves, firsts = leaves[srt], firsts[srt]
    nlo[leaves] = np.minimum.reduceat(slo, firsts, axis=0)
    nhi[leaves] = np.maximum.reduceat(shi, firsts, axis=0)
    for sid in reversed(internal_levels):
        ch = left[sid]
        nlo[sid] = np.minimum(nlo[ch], nlo[ch + 1])
        nhi[sid] = np.maximum(nhi[ch], nhi[ch + 1])
    out_lo = np.zeros((total, 4), np.float32)
    out_hi = np.zeros((total, 4), np.float32)
    out_lo[:, :3], out_hi[:, :3] = nlo[:total], nhi[:total]
    out_lo[:, 3] = left[:total].view(np.float32)
    out_hi[:, 3] = count[:total].view(np.float32)
    return out_lo, out_hi, order


# --------------------------------------------------------------------- scene

class Occluders:
    """The level's occluding geometry as flat arrays for the GPU (two-level BVH)."""

    def __init__(self, comps, meshes):
        blo, bhi, tris, roots = [], [], [], {}
        node_base = tri_base = 0
        for name in sorted({c['mesh'] for c in comps if c['occluder']}):
            m = meshes.get(name)
            if m is None or not len(m['triangles']):
                continue
            p = m['positions'][m['triangles']]          # (t, 3, 3)
            nlo, nhi, order = build_bvh(p.min(1), p.max(1))
            p = p[order]
            # leaves index the concatenated triangle array; children the node array
            leaf = nhi[:, 3].view(np.int32) > 0
            first = nlo[:, 3].view(np.int32).copy()
            first[leaf] += tri_base
            first[~leaf] += node_base
            nlo[:, 3] = first.view(np.float32)
            t = np.zeros((len(p), 3, 4), np.float32)
            t[:, 0, :3] = p[:, 0]
            t[:, 1, :3] = p[:, 1] - p[:, 0]
            t[:, 2, :3] = p[:, 2] - p[:, 0]
            blo.append(nlo)
            bhi.append(nhi)
            tris.append(t.reshape(-1, 4))
            roots[name] = (node_base, m['positions'].min(0), m['positions'].max(0))
            node_base += len(nlo)
            tri_base += len(p)
        self.blas_lo = np.concatenate(blo)
        self.blas_hi = np.concatenate(bhi)
        self.tris = np.concatenate(tris)
        inst, ilo, ihi = [], [], []
        emitters = 0
        self.emitter_ids = []
        for c in comps:
            if not c['occluder'] or c['mesh'] not in roots:
                continue
            root, mlo, mhi = roots[c['mesh']]
            corners = np.array([[x, y, z, 1.0] for x in (mlo[0], mhi[0])
                                for y in (mlo[1], mhi[1]) for z in (mlo[2], mhi[2])])
            for m in c['matrices']:
                det = np.linalg.det(m[:3, :3])
                if not np.isfinite(det) or abs(det) < 1e-12:
                    continue
                w = (m @ corners.T).T[:, :3]
                inv = np.linalg.inv(m)
                rec = np.zeros((4, 4), np.float32)
                rec[:3, :] = inv[:3, :]
                flags = root | (TWO_SIDED_BIT if c.get('two_sided') else 0)
                rec[3, :1] = np.array([flags], np.int32).view(np.float32)
                rec[3, 1:] = c.get('emitter') or (0.0, 0.0, 0.0)
                if c.get('emitter'):
                    emitters += 1
                    self.emitter_ids.append(len(inst))
                inst.append(rec)
                ilo.append(w.min(0))
                ihi.append(w.max(0))
        self.instances = np.array(inst, np.float32).reshape(-1, 4)
        tlo, thi, order = build_bvh(np.array(ilo), np.array(ihi))
        self.tlas_lo, self.tlas_hi = tlo, thi
        self.instance_order = order.astype(np.int32)
        self.count = len(inst)
        self.triangles = tri_base
        self.emitters = emitters


def load_meshes(names):
    from riftlord.build.lighting import lightscene
    out = {}
    for n in sorted(names):
        m = lightscene.mesh(n)
        if m is not None:
            out[n] = m
    return out


# ------------------------------------------------------------------- shaders

RASTER_VS = '''
#version 430
uniform mat4 world;
uniform mat3 normal_world;
uniform vec4 scale_bias;      // lightmap uv -> atlas uv
uniform vec2 jitter;          // in clip units
uniform float depth;
in vec3 pos; in vec3 nrm; in vec3 fn; in vec2 uv; in float chart;
out vec3 v_pos; out vec3 v_nrm; flat out vec3 v_fn; flat out float v_chart;
void main() {
    vec2 a = uv * scale_bias.xy + scale_bias.zw;
    gl_Position = vec4(a * 2.0 - 1.0 + jitter, depth, 1.0);
    v_pos = (world * vec4(pos, 1.0)).xyz;
    v_nrm = normal_world * nrm;
    v_fn = normal_world * fn;
    v_chart = chart;
}
'''
RASTER_FS = '''
#version 430
uniform float texel_radius;
uniform float component;
in vec3 v_pos; in vec3 v_nrm; flat in vec3 v_fn; flat in float v_chart;
layout(location = 0) out vec4 o_pos;
layout(location = 1) out vec4 o_nrm;
layout(location = 2) out vec4 o_geo;
void main() {
    o_pos = vec4(v_pos, texel_radius);
    vec3 n = v_nrm;
    float l = length(n);
    o_nrm = vec4(l > 0.0 ? n / l : vec3(0.0, 0.0, 1.0), component);
    float g = length(v_fn);
    o_geo = vec4(g > 0.0 ? v_fn / g : o_nrm.xyz, v_chart + 1.0);
}
'''

TRACE_CS = '''
#version 430
layout(local_size_x = %(GROUP)d) in;
#define MAX_EMITTERS %(MAX_EMITTERS)d
layout(std430, binding = 0) readonly buffer BlasLo { vec4 blas_lo[]; };
layout(std430, binding = 1) readonly buffer BlasHi { vec4 blas_hi[]; };
layout(std430, binding = 2) readonly buffer Tris { vec4 tris[]; };
layout(std430, binding = 3) readonly buffer Inst { vec4 inst[]; };
layout(std430, binding = 4) readonly buffer TlasLo { vec4 tlas_lo[]; };
layout(std430, binding = 5) readonly buffer TlasHi { vec4 tlas_hi[]; };
layout(std430, binding = 6) readonly buffer InstOrder { int inst_order[]; };
layout(std430, binding = 7) readonly buffer TexPos { vec4 tex_pos[]; };
layout(std430, binding = 8) readonly buffer TexNrm { vec4 tex_nrm[]; };
layout(std430, binding = 9) readonly buffer Lights { vec4 lights[]; };   // 5 vec4 a light
layout(std430, binding = 10) readonly buffer LightList { int light_list[]; };
layout(std430, binding = 11) writeonly buffer OutA { vec4 out_a[]; };   // SH luma 0..3
layout(std430, binding = 12) writeonly buffer OutB { vec4 out_b[]; };   // incident rgb, corr
layout(std430, binding = 13) writeonly buffer OutC { vec4 out_c[]; };   // backface share, sky seen, emitters' incident r g
layout(std430, binding = 14) writeonly buffer OutD { vec4 out_d[]; };   // lights' incident rgb, emitters' incident b
layout(std430, binding = 15) readonly buffer TexGeo { vec4 tex_geo[]; };   // triangle normal
uniform int texel_count;
uniform int light_count;
uniform int sky_rays;
uniform int backface_rays;
uniform int has_emitters;         // 1: the scene has emissive instances
uniform int emitter_count;        // how many (emitter_ids); -1: too many, use closest hits
uniform int emitter_ids[MAX_EMITTERS];
const float EMITTER_T_SHRINK = 0.999;        // the first this many sky rays find their closest hit
uniform vec3 sky_colour;          // linear colour * brightness
uniform int sky_on;
uniform int sky_lower_black;
uniform vec3 sky_lower_colour;
uniform int sky_uniform;          // 1: radiance is sky_colour alone (no cubemap)
uniform sampler2DArray sky_cube;
uniform int shadow_rays;
uniform float penumbra_angle;     // source size / distance that earns all shadow_rays
uniform float shadow_exponent;
uniform float ray_offset;
uniform float normal_offset_scale;
uniform float min_normal_offset;
uniform float sky_ray_length;
uniform uint seed;

const float PI = 3.14159265358979;
const int ROOT_MASK = 0x3FFFFFFF;
const int TWO_SIDED = 0x40000000;
const vec3 LUMA = vec3(0.3, 0.59, 0.11);

vec3 safe_inv(vec3 d) {
    return vec3(abs(d.x) > 1e-12 ? 1.0 / d.x : 1e30 * sign(d.x + 1e-30),
                abs(d.y) > 1e-12 ? 1.0 / d.y : 1e30 * sign(d.y + 1e-30),
                abs(d.z) > 1e-12 ? 1.0 / d.z : 1e30 * sign(d.z + 1e-30));
}

float box_t(vec3 lo, vec3 hi, vec3 o, vec3 inv, float tmax) {
    vec3 a = (lo - o) * inv, b = (hi - o) * inv;
    vec3 mn = min(a, b), mx = max(a, b);
    float t0 = max(max(mn.x, mn.y), max(mn.z, 0.0));
    float t1 = min(min(mx.x, mx.y), min(mx.z, tmax));
    return t0 <= t1 ? t0 : 1e38;
}

bool tri_hit(int t, vec3 o, vec3 d, float tmin, float tmax) {
    vec3 v0 = tris[3 * t].xyz, e1 = tris[3 * t + 1].xyz, e2 = tris[3 * t + 2].xyz;
    vec3 p = cross(d, e2);
    float det = dot(e1, p);
    if (abs(det) < 1e-12) return false;
    float inv = 1.0 / det;
    vec3 s = o - v0;
    float u = dot(s, p) * inv;
    if (u < 0.0 || u > 1.0) return false;
    vec3 q = cross(s, e1);
    float v = dot(d, q) * inv;
    if (v < 0.0 || u + v > 1.0) return false;
    float tt = dot(e2, q) * inv;
    return tt > tmin && tt < tmax;
}

bool blas_any(int root, vec3 o, vec3 d, float tmin, float tmax) {
    vec3 inv = safe_inv(d);
    int stack[48];
    int sp = 0;
    int node = root;
    if (box_t(blas_lo[node].xyz, blas_hi[node].xyz, o, inv, tmax) > 1e37) return false;
    while (true) {
        vec4 a = blas_lo[node], b = blas_hi[node];
        int cnt = floatBitsToInt(b.w), idx = floatBitsToInt(a.w);
        if (cnt > 0) {
            for (int i = 0; i < cnt; i++) if (tri_hit(idx + i, o, d, tmin, tmax)) return true;
            if (sp == 0) break;
            node = stack[--sp];
            continue;
        }
        float t0 = box_t(blas_lo[idx].xyz, blas_hi[idx].xyz, o, inv, tmax);
        float t1 = box_t(blas_lo[idx + 1].xyz, blas_hi[idx + 1].xyz, o, inv, tmax);
        bool h0 = t0 < 1e37, h1 = t1 < 1e37;
        if (h0 && h1) {
            node = t0 <= t1 ? idx : idx + 1;
            if (sp < 48) stack[sp++] = t0 <= t1 ? idx + 1 : idx;
        } else if (h0) node = idx;
        else if (h1) node = idx + 1;
        else { if (sp == 0) break; node = stack[--sp]; }
    }
    return false;
}

bool occluded(vec3 o, vec3 d, float tmin, float tmax) {
    vec3 inv = safe_inv(d);
    int stack[48];
    int sp = 0;
    int node = 0;
    if (box_t(tlas_lo[0].xyz, tlas_hi[0].xyz, o, inv, tmax) > 1e37) return false;
    while (true) {
        vec4 a = tlas_lo[node], b = tlas_hi[node];
        int cnt = floatBitsToInt(b.w), idx = floatBitsToInt(a.w);
        if (cnt > 0) {
            for (int i = 0; i < cnt; i++) {
                int k = inst_order[idx + i];
                vec4 r0 = inst[4 * k], r1 = inst[4 * k + 1], r2 = inst[4 * k + 2];
                int root = floatBitsToInt(inst[4 * k + 3].x) & ROOT_MASK;
                vec3 lo_ = vec3(dot(r0.xyz, o) + r0.w, dot(r1.xyz, o) + r1.w, dot(r2.xyz, o) + r2.w);
                vec3 ld = vec3(dot(r0.xyz, d), dot(r1.xyz, d), dot(r2.xyz, d));
                if (blas_any(root, lo_, ld, tmin, tmax)) return true;
            }
            if (sp == 0) break;
            node = stack[--sp];
            continue;
        }
        float t0 = box_t(tlas_lo[idx].xyz, tlas_hi[idx].xyz, o, inv, tmax);
        float t1 = box_t(tlas_lo[idx + 1].xyz, tlas_hi[idx + 1].xyz, o, inv, tmax);
        bool h0 = t0 < 1e37, h1 = t1 < 1e37;
        if (h0 && h1) {
            node = t0 <= t1 ? idx : idx + 1;
            if (sp < 48) stack[sp++] = t0 <= t1 ? idx + 1 : idx;
        } else if (h0) node = idx;
        else if (h1) node = idx + 1;
        else { if (sp == 0) break; node = stack[--sp]; }
    }
    return false;
}

bool tri_closest(int t, vec3 o, vec3 d, float tmin, inout float tbest) {
    vec3 v0 = tris[3 * t].xyz, e1 = tris[3 * t + 1].xyz, e2 = tris[3 * t + 2].xyz;
    vec3 p = cross(d, e2);
    float det = dot(e1, p);
    if (abs(det) < 1e-12) return false;
    float inv = 1.0 / det;
    vec3 s = o - v0;
    float u = dot(s, p) * inv;
    if (u < 0.0 || u > 1.0) return false;
    vec3 q = cross(s, e1);
    float v = dot(d, q) * inv;
    if (v < 0.0 || u + v > 1.0) return false;
    float tt = dot(e2, q) * inv;
    if (tt > tmin && tt < tbest) { tbest = tt; return true; }
    return false;
}

// closest hit inside one BLAS; updates tbest and the hit triangle, returns true on a closer hit
bool blas_closest(int root, vec3 o, vec3 d, float tmin, inout float tbest, inout int tri) {
    vec3 inv = safe_inv(d);
    int stack[48];
    int sp = 0;
    int node = root;
    bool got = false;
    if (box_t(blas_lo[node].xyz, blas_hi[node].xyz, o, inv, tbest) > 1e37) return false;
    while (true) {
        vec4 a = blas_lo[node], b = blas_hi[node];
        int cnt = floatBitsToInt(b.w), idx = floatBitsToInt(a.w);
        if (cnt > 0) {
            for (int i = 0; i < cnt; i++) if (tri_closest(idx + i, o, d, tmin, tbest)) { tri = idx + i; got = true; }
            if (sp == 0) break;
            node = stack[--sp];
            continue;
        }
        float t0 = box_t(blas_lo[idx].xyz, blas_hi[idx].xyz, o, inv, tbest);
        float t1 = box_t(blas_lo[idx + 1].xyz, blas_hi[idx + 1].xyz, o, inv, tbest);
        bool h0 = t0 < 1e37, h1 = t1 < 1e37;
        if (h0 && h1) {
            node = t0 <= t1 ? idx : idx + 1;
            if (sp < 48) stack[sp++] = t0 <= t1 ? idx + 1 : idx;
        } else if (h0) node = idx;
        else if (h1) node = idx + 1;
        else { if (sp == 0) break; node = stack[--sp]; }
    }
    return got;
}

// closest hit in the scene: true when hit; `back` when it is a triangle's back face
// closest hit in the scene: true when hit; `back` when it is a back face of a one-sided
// mesh; `hit_inst` the instance hit
bool closest(vec3 o, vec3 d, float tmax, out float thit, out bool back, out int hit_inst) {
    vec3 inv = safe_inv(d);
    int stack[48];
    int sp = 0;
    int node = 0;
    float tbest = tmax;
    int tri = -1;
    vec3 dl_hit = d;
    back = false;
    thit = tmax;
    hit_inst = -1;
    if (box_t(tlas_lo[0].xyz, tlas_hi[0].xyz, o, inv, tmax) > 1e37) return false;
    while (true) {
        vec4 a = tlas_lo[node], b = tlas_hi[node];
        int cnt = floatBitsToInt(b.w), idx = floatBitsToInt(a.w);
        if (cnt > 0) {
            for (int i = 0; i < cnt; i++) {
                int k = inst_order[idx + i];
                vec4 r0 = inst[4 * k], r1 = inst[4 * k + 1], r2 = inst[4 * k + 2];
                int root = floatBitsToInt(inst[4 * k + 3].x) & ROOT_MASK;
                vec3 lo_ = vec3(dot(r0.xyz, o) + r0.w, dot(r1.xyz, o) + r1.w, dot(r2.xyz, o) + r2.w);
                vec3 ld = vec3(dot(r0.xyz, d), dot(r1.xyz, d), dot(r2.xyz, d));
                if (blas_closest(root, lo_, ld, 0.0, tbest, tri)) { dl_hit = ld; hit_inst = k; }
            }
            if (sp == 0) break;
            node = stack[--sp];
            continue;
        }
        float t0 = box_t(tlas_lo[idx].xyz, tlas_hi[idx].xyz, o, inv, tbest);
        float t1 = box_t(tlas_lo[idx + 1].xyz, tlas_hi[idx + 1].xyz, o, inv, tbest);
        bool h0 = t0 < 1e37, h1 = t1 < 1e37;
        if (h0 && h1) {
            node = t0 <= t1 ? idx : idx + 1;
            if (sp < 48) stack[sp++] = t0 <= t1 ? idx + 1 : idx;
        } else if (h0) node = idx;
        else if (h1) node = idx + 1;
        else { if (sp == 0) break; node = stack[--sp]; }
    }
    if (tri < 0) return false;
    // UE winds front faces clockwise: the front normal is -cross(e1, e2), in mesh space
    back = dot(dl_hit, cross(tris[3 * tri + 1].xyz, tris[3 * tri + 2].xyz)) < 0.0;
    if ((floatBitsToInt(inst[4 * hit_inst + 3].x) & TWO_SIDED) != 0) back = false;
    thit = tbest;
    return true;
}

uint hash(uint x) {
    x ^= x >> 16; x *= 0x7feb352du; x ^= x >> 15; x *= 0x846ca68bu; x ^= x >> 16;
    return x;
}
float rnd(inout uint s) { s = hash(s); return float(s >> 8) * (1.0 / 16777216.0); }

float radical_inverse(uint b) {
    b = (b << 16u) | (b >> 16u);
    b = ((b & 0x55555555u) << 1u) | ((b & 0xAAAAAAAAu) >> 1u);
    b = ((b & 0x33333333u) << 2u) | ((b & 0xCCCCCCCCu) >> 2u);
    b = ((b & 0x0F0F0F0Fu) << 4u) | ((b & 0xF0F0F0F0u) >> 4u);
    b = ((b & 0x00FF00FFu) << 8u) | ((b & 0xFF00FF00u) >> 8u);
    return float(b) * 2.3283064365386963e-10;
}

void frame(vec3 n, out vec3 t, out vec3 b) {
    vec3 up = abs(n.z) < 0.999 ? vec3(0.0, 0.0, 1.0) : vec3(1.0, 0.0, 0.0);
    t = normalize(cross(up, n));
    b = cross(n, t);
}

vec3 polygon_irradiance(vec3 p0, vec3 p1, vec3 p2, vec3 p3) {
    vec3 l0 = normalize(p0), l1 = normalize(p1), l2 = normalize(p2), l3 = normalize(p3);
    float c01 = dot(l0, l1), c12 = dot(l1, l2), c23 = dot(l2, l3), c30 = dot(l3, l0);
    float w01 = (1.5708 - 0.175 * c01) * inversesqrt(c01 + 1.0);
    float w12 = (1.5708 - 0.175 * c12) * inversesqrt(c12 + 1.0);
    float w23 = (1.5708 - 0.175 * c23) * inversesqrt(c23 + 1.0);
    float w30 = (1.5708 - 0.175 * c30) * inversesqrt(c30 + 1.0);
    vec3 l = cross(l1, -w01 * l0 + w12 * l2);
    l += cross(l3, w30 * l0 - w23 * l2);
    return 0.5 * l;
}

vec3 sky_radiance(vec3 d) {
    // the captured cube takes LowerHemisphereColor below the horizon; Lightmass then
    // scales the whole cube by the light's colour and brightness (GetPathLighting)
    if (sky_lower_black == 1 && d.z < 0.0) return sky_lower_colour * sky_colour;
    if (sky_uniform == 1) return sky_colour;
    vec3 a = abs(d);
    int face; vec2 uv; float m;
    if (a.z > a.y && a.z > a.x) { m = a.z; face = d.z < 0.0 ? 5 : 4;
        uv = d.z < 0.0 ? vec2(-d.x, -d.y) : vec2(d.x, -d.y); }
    else if (a.y > a.x) { m = a.y; face = d.y < 0.0 ? 3 : 2;
        uv = d.y < 0.0 ? vec2(d.x, -d.z) : vec2(d.x, d.z); }
    else { m = a.x; face = d.x < 0.0 ? 1 : 0;
        uv = d.x < 0.0 ? vec2(d.z, -d.y) : vec2(-d.z, -d.y); }
    uv = uv / m * 0.5 + 0.5;
    return texture(sky_cube, vec3(uv, float(face))).rgb * sky_colour;
}

void gather(inout vec4 sh, inout vec4 inc, vec3 c, vec3 wdir, float tz) {
    // PointLightWorldSpace: SH of luminance, SH correction along the normal, N.L light
    if (tz < 0.0) return;
    float lum = dot(c, LUMA);
    sh += lum * vec4(0.282095, -0.488603 * wdir.y, 0.488603 * wdir.z, -0.488603 * wdir.x);
    inc.w += lum * (0.282095 * 0.282095 + 0.325735 * 0.488603 * tz);
    inc.xyz += c * tz;
}

void main() {
    int i = int(gl_GlobalInvocationID.x);
    if (i >= texel_count) return;
    vec4 pr = tex_pos[i], nr = tex_nrm[i];
    vec3 P = pr.xyz;
    vec3 N = normalize(nr.xyz);            // smoothed vertex normal: gathers light (TangentZ)
    vec3 G = normalize(tex_geo[i].xyz);    // triangle normal: ray offsets and hemisphere
    float radius = max(pr.w, 0.1);
    float noff = max(min_normal_offset, normal_offset_scale * radius);
    // never offset through nearby geometry (thin rails, pieces sunk into others)
    float th0; bool bk0; int hi0;
    if (closest(P + G * 0.01, G, noff, th0, bk0, hi0)) noff = 0.5 * th0;
    vec3 O = P + G * noff;
    uint s = hash(uint(i) * 9781u + seed);
    vec4 sh = vec4(0.0), inc = vec4(0.0);

    for (int li = 0; li < light_count; li++) {
        int L = light_list[li] * 5;
        vec4 a = lights[L], b = lights[L + 1], c = lights[L + 2], t = lights[L + 3], q = lights[L + 4];
        vec3 lp = a.xyz; float R = a.w;
        vec3 colour = b.xyz; int kind = int(b.w);
        vec3 ldir = c.xyz; float falloff = c.w;
        vec3 tangent = t.xyz; float src_r = t.w;
        float src_l = q.x, cos_outer = q.y, cos_inner = q.z; int flags = int(q.w);
        bool inv_sq = (flags & 1) != 0, shadows = (flags & 2) != 0;
        vec3 to_l = lp - P;
        float d2 = dot(to_l, to_l);
        if (d2 >= R * R) continue;
        float att;
        vec3 wl;
        vec3 ax = vec3(0.0), ay = vec3(0.0);
        if (kind == 2) {
            ay = tangent; vec3 az = -ldir; ax = cross(ay, az);
            if (dot(to_l, az) < 0.0) continue;
            vec3 p0 = to_l - ax * src_r - ay * src_l, p1 = to_l + ax * src_r - ay * src_l;
            vec3 p2 = to_l + ax * src_r + ay * src_l, p3 = to_l - ax * src_r + ay * src_l;
            vec3 irr = polygon_irradiance(p0, p1, p2, p3);
            att = length(irr);
            if (!(att == att) || isinf(att)) att = 0.0;
            att /= 2.0 * src_r * src_l;
            float mask = max(0.0, 1.0 - (d2 / (R * R)) * (d2 / (R * R)));
            att *= mask * mask;
            wl = irr;
        } else {
            if (inv_sq) {
                float mask = max(0.0, 1.0 - (d2 / (R * R)) * (d2 / (R * R)));
                att = mask * mask / (d2 + 1.0);
            } else {
                att = pow(max(1.0 - d2 / (R * R), 0.0), falloff);
            }
            if (kind == 1) {
                float sp = clamp((dot(normalize(-to_l), ldir) - cos_outer) / max(cos_inner - cos_outer, 1e-4), 0.0, 1.0);
                att *= sp * sp;
            }
            wl = (lp + N * src_r) - P;
        }
        if (att <= 0.0) continue;
        float wlen = length(wl);
        if (wlen <= 0.0) continue;
        wl /= wlen;
        float tz = dot(wl, N);
        if (tz < 0.0) continue;
        float vis = 1.0;
        if (shadows) {
            int unshadowed = 0;
            vec3 u, v;
            frame(normalize(to_l), u, v);
            // a source that looks small from here casts a hard shadow: fewer rays
            float angular = max(src_r, src_l) / max(sqrt(d2), 1.0);
            int nrays = clamp(int(ceil(float(shadow_rays) * angular / penumbra_angle)),
                              1, shadow_rays);
            for (int k = 0; k < nrays; k++) {
                vec3 target;
                float e1 = (float(k) + rnd(s)) / float(nrays), e2 = rnd(s);
                if (kind == 2) {
                    target = lp + ax * src_r * (e1 * 2.0 - 1.0) + ay * src_l * (e2 * 2.0 - 1.0);
                } else {
                    float rr = src_r * sqrt(e1), ph = 2.0 * PI * e2;
                    target = lp + (u * cos(ph) + v * sin(ph)) * rr;
                }
                vec3 dd = target - O;
                float len = length(dd);
                vec3 dn = dd / len;
                if (!occluded(O + dn * ray_offset, dn, 0.0, len - ray_offset - 0.5)) unshadowed++;
            }
            vis = pow(float(unshadowed) / float(nrays), shadow_exponent);
        }
        if (vis <= 0.0) continue;
        gather(sh, inc, colour * att * vis, wl, tz);
    }

    vec3 lights_inc = inc.xyz;
    vec3 lava = vec3(0.0);
    if (sky_on == 1 || has_emitters == 1) {
        vec3 tu, tv;
        frame(G, tu, tv);
        float w = 2.0 * PI / float(sky_rays);
        float rot = rnd(s), rot2 = rnd(s);
        int backs = 0, seen = 0;
        for (int k = 0; k < sky_rays; k++) {
            float e1 = fract((float(k) + 0.5) / float(sky_rays) + rot);
            float e2 = fract(radical_inverse(uint(k)) + rot2);
            float z = e1;                          // uniform hemisphere: cos theta uniform
            float r = sqrt(max(0.0, 1.0 - z * z));
            float ph = 2.0 * PI * e2;
            vec3 d = tu * (r * cos(ph)) + tv * (r * sin(ph)) + G * z;
            vec3 ro = O + d * ray_offset;
            if (k < backface_rays || emitter_count < 0) {
                float th; bool back; int hit;
                if (closest(ro, d, sky_ray_length, th, back, hit)) {
                    if (back && k < backface_rays) backs++;
                    // an emissive surface seen from its front adds its radiance, as
                    // Lightmass's final gather does (FinalGather.cpp, GLM_GatherLightEmitted)
                    vec3 e = inst[4 * hit + 3].yzw;
                    if (!back && dot(e, e) > 0.0) {
                        float tzn = dot(d, N);
                        gather(sh, inc, e * w, d, tzn);
                        if (tzn > 0.0) lava += e * w * tzn;
                    }
                    continue;
                }
            } else if (occluded(ro, d, 0.0, sky_ray_length)) {
                // blocked: if the nearest emitter along the ray is in front of everything
                // else, it is what the ray sees (few emitters: test them alone)
                float te = sky_ray_length; int he = -1; vec3 dle = d; int tri_e = -1;
                for (int q = 0; q < emitter_count; q++) {
                    int k2 = emitter_ids[q];
                    vec4 r0 = inst[4 * k2], r1 = inst[4 * k2 + 1], r2 = inst[4 * k2 + 2];
                    int root = floatBitsToInt(inst[4 * k2 + 3].x) & ROOT_MASK;
                    vec3 lo_ = vec3(dot(r0.xyz, ro) + r0.w, dot(r1.xyz, ro) + r1.w, dot(r2.xyz, ro) + r2.w);
                    vec3 ld = vec3(dot(r0.xyz, d), dot(r1.xyz, d), dot(r2.xyz, d));
                    if (blas_closest(root, lo_, ld, 0.0, te, tri_e)) { he = k2; dle = ld; }
                }
                if (he >= 0 && dot(dle, cross(tris[3 * tri_e + 1].xyz, tris[3 * tri_e + 2].xyz)) >= 0.0
                        && !occluded(ro, d, 0.0, te * EMITTER_T_SHRINK)) {
                    vec3 e = inst[4 * he + 3].yzw;
                    float tzn = dot(d, N);
                    gather(sh, inc, e * w, d, tzn);
                    if (tzn > 0.0) lava += e * w * tzn;
                }
                continue;
            }
            seen++;
            if (sky_on == 1) gather(sh, inc, sky_radiance(d) * w, d, dot(d, N));
        }
        out_c[i] = vec4(float(backs) / float(min(backface_rays, sky_rays)),
                        float(seen) / float(sky_rays), lava.r, lava.g);
    } else {
        out_c[i] = vec4(0.0);
    }
    out_d[i] = vec4(lights_inc, lava.b);
    out_a[i] = sh;
    out_b[i] = inc;
}
'''


# ------------------------------------------------------------- coefficients

def coefficients(sh, inc):
    """ConvertToLightSampleHelper: (Coefficients[0], Coefficients[1]) per texel from
    the gathered SH luminance (n, 4) and incident light + SH correction (n, 4)."""
    corr = inc[:, 3]
    dir_corr = 1.0 / np.maximum(0.0001, corr)
    dir_luma = sh * (dir_corr / math.pi)[:, None]
    dir_scale = 1.0 / np.maximum(0.0001, dir_luma[:, 0])
    colour_scale = dir_luma[:, 0]
    c0 = colour_scale[:, None] * inc[:, :3]
    c1 = np.stack([-SH_DIFFUSE_C1 * dir_luma[:, 1] * dir_scale,
                   SH_DIFFUSE_C1 * dir_luma[:, 2] * dir_scale,
                   -SH_DIFFUSE_C1 * dir_luma[:, 3] * dir_scale], 1)
    return c0, c1


def shade(c0, c1, normal):
    """What the shader's directionality makes of float coefficients (no quantisation)."""
    n = np.asarray(normal)
    d = np.maximum(0.0, (c1 * np.stack([n[..., 1], n[..., 2], n[..., 0]], -1)).sum(-1)
                   + SH_C0)
    return c0 * d[..., None]


# --------------------------------------------------------------------- baker

class Baker:
    """GPU state for one level: occluders uploaded once, atlases baked one at a time."""

    def __init__(self, level, lights, sky, tags=None, plan=None, emitters=None, log=print):
        from riftlord.build.lighting import lightscene
        import moderngl
        self.level = level
        self.log = log
        self.ctx = moderngl.create_standalone_context(backend='egl', require=430)
        self.packages = lightscene.packages(level)
        self.comps = {}
        self.bds = {}
        for path in self.packages:
            bd = lightscene.registry(path)
            if bd is None:
                continue
            self.comps[path] = lightscene.components(level, path, bd)
            if tags is None or lightscene.tag_of(level, path) in tags:
                self.bds[path] = bd
            del bd
        self.plan_counts = None
        if plan:
            self.plan_counts = lightscene.apply_plan(level, self.comps, plan)
            log('plan: %s' % self.plan_counts)
        self.emitter_count = 0
        if emitters:
            ids, radiance = emitters
            self.emitter_count = lightscene.mark_emitters(level, self.comps, ids, radiance)
            log('emitters: %d instances at radiance %s' % (self.emitter_count, list(radiance)))
        every = [c for cs in self.comps.values() for c in cs]
        self.meshes = load_meshes({c['mesh'] for c in every})
        ctx = self.ctx
        self.tracer = None
        self.backend = choose_backend()
        if self.backend == 'optix':
            from riftlord.build.lighting import lighttrace
            self.tracer = lighttrace.OptixScene(every, self.meshes, log=log)
            self.has_emitters = 1 if self.tracer.emitters else 0
            self.emitter_ids = []
        else:
            t0 = time.time()
            occ = Occluders(every, self.meshes)
            log('occluders: %d instances, %d distinct triangles, BVH %.1fs'
                % (occ.count, occ.triangles, time.time() - t0))
            self.has_emitters = 1 if occ.emitters else 0
            self.emitter_ids = occ.emitter_ids
            self.buffers = [ctx.buffer(np.ascontiguousarray(a).tobytes()) for a in (
                occ.blas_lo, occ.blas_hi, occ.tris, occ.instances, occ.tlas_lo, occ.tlas_hi,
                occ.instance_order)]
            del occ
            self.trace = ctx.compute_shader(TRACE_CS % {'GROUP': GROUP,
                                                        'MAX_EMITTERS': MAX_EMITTERS})
        log('tracing backend: %s' % self.backend)
        self.raster = ctx.program(vertex_shader=RASTER_VS, fragment_shader=RASTER_FS)
        self.lights = lights
        self.light_array = self._light_array(lights)
        self.lights_buffer = ctx.buffer(self.light_array.tobytes() if len(lights)
                                        else np.zeros(20, np.float32).tobytes())
        self.sky = sky
        self._sky_texture()

    # -- inputs
    @staticmethod
    def _light_array(lights):
        out = np.zeros((max(len(lights), 1), 5, 4), np.float32)
        for k, l in enumerate(lights):
            colour = np.asarray(l['colour']) * l['brightness']
            flags = (1 if l['inverse_squared'] else 0) | (2 if l['cast_shadows'] else 0)
            cos_outer = math.cos(math.radians(l.get('outer', 44.0)))
            cos_inner = math.cos(math.radians(l.get('inner', 0.0)))
            out[k, 0] = list(l['position']) + [l['radius']]
            out[k, 1] = list(colour) + [KIND_CODE[l['kind']]]
            out[k, 2] = list(l['direction']) + [l['falloff']]
            out[k, 3] = list(l['tangent']) + [l['source_radius']]
            out[k, 4] = [l['source_length'], cos_outer, cos_inner, flags]
        return out

    def _sky_texture(self):
        from riftlord.build.lighting import lightscene
        sky = self.sky
        face = lightscene.SKY_FACE_SIZE
        data = np.ones((6, face, face, 4), np.float32)
        self.sky_uniform = 1
        self.sky_cube = None
        if sky and sky.get('texture_cube') is not None:
            data[..., :3] = np.asarray(sky['texture_cube'], np.float32)
            self.sky_uniform = 0
            self.sky_cube = data[..., :3].astype(np.float64)
        elif sky and sky.get('cubemap'):
            data[..., :3] = lightscene.cubemap_faces(sky['cubemap'], face)
            self.sky_uniform = 0
            self.sky_cube = data[..., :3].astype(np.float64)
        self.sky_tex = self.ctx.texture_array((face, face, 6), 4, data.tobytes(), dtype='f4')
        import moderngl
        self.sky_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)

    # -- texels
    def gbuffer(self, comps, size_x, half_y):
        """(pos, nrm, geo), each (h, w, 4), of one atlas: world position + texel radius,
        vertex normal + component index (+1; 0 = unmapped), triangle normal."""
        ctx = self.ctx
        pos_t = ctx.texture((size_x, half_y), 4, dtype='f4')
        nrm_t = ctx.texture((size_x, half_y), 4, dtype='f4')
        geo_t = ctx.texture((size_x, half_y), 4, dtype='f4')
        depth = ctx.depth_renderbuffer((size_x, half_y))
        fbo = ctx.framebuffer([pos_t, nrm_t, geo_t], depth)
        fbo.use()
        fbo.clear(0.0, 0.0, 0.0, 0.0, depth=1.0)
        ctx.enable(ctx.DEPTH_TEST)
        ctx.disable(ctx.CULL_FACE)
        prog = self.raster
        draws, vaos = [], {}
        for ci, c in enumerate(comps):
            m = self.meshes.get(c['mesh'])
            if m is None:
                continue
            if c['mesh'] not in vaos:
                tri = m['triangles'].ravel()
                p = m['positions'][m['triangles']].astype(np.float64)
                # UE winds front faces clockwise: the front normal is -cross(e1, e2)
                fn = -np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
                fn = np.repeat(fn / np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-20),
                               3, axis=0)
                chart = np.repeat(uv_charts(m).astype(np.float64), 3)[:, None]
                data = np.concatenate([m['positions'][tri], m['normals'][tri], fn,
                                       m['uv'][tri], chart], 1)
                vbo = ctx.buffer(np.ascontiguousarray(data, np.float32).tobytes())
                vaos[c['mesh']] = ctx.vertex_array(
                    prog, [(vbo, '3f 3f 3f 2f 1f', 'pos', 'nrm', 'fn', 'uv', 'chart')])
                draws.append(vbo)
            vao = vaos[c['mesh']]
            lm = c['lightmap']
            for k, mat in enumerate(c['matrices']):
                bias = lm['coord_bias']
                if c['instances'] and k < len(c['instances']):
                    bias = c['instances'][k][:2]
                radius = _texel_radius(m, mat, lm['coord_scale'], size_x, half_y)
                draws.append((vao, mat, (lm['coord_scale'][0], lm['coord_scale'][1],
                                         bias[0], bias[1]), radius, ci))
        for k, (jx, jy) in enumerate(JITTER):
            prog['jitter'].value = (2.0 * jx / size_x, 2.0 * jy / half_y)
            prog['depth'].value = -1.0 + 2.0 * (k + 0.5) / (len(JITTER) + 1)
            for d in draws:
                if not isinstance(d, tuple):
                    continue
                vao, mat, sb, radius, ci = d
                prog['world'].write(np.ascontiguousarray(mat.T, np.float32).tobytes())
                nm = np.linalg.inv(mat[:3, :3]).T
                prog['normal_world'].write(np.ascontiguousarray(nm.T, np.float32).tobytes())
                prog['scale_bias'].value = tuple(float(v) for v in sb)
                prog['texel_radius'].value = float(radius)
                prog['component'].value = float(ci + 1)
                vao.render()
        pos = np.frombuffer(pos_t.read(), np.float32).reshape(half_y, size_x, 4).copy()
        nrm = np.frombuffer(nrm_t.read(), np.float32).reshape(half_y, size_x, 4).copy()
        geo = np.frombuffer(geo_t.read(), np.float32).reshape(half_y, size_x, 4).copy()
        for vao in vaos.values():
            vao.release()
        for d in draws:
            if isinstance(d, tuple):
                continue
            d.release()
        for obj in (fbo, depth, pos_t, nrm_t, geo_t):
            obj.release()
        return pos, nrm, geo

    # -- light
    def light_texels(self, pos, nrm, geo, use_lights=True, use_sky=True, seed=1):
        """(sh (n, 4), inc (n, 4), extra (n, 4)) gathered for texels pos (n, 4) / nrm (n, 4);
        extra (n, 12) = (share of sky rays that hit a back face, share that saw the sky, 0, 0,
        incident light of the Static lights alone rgb, 0, of the emitters alone rgb, 0)."""
        ctx = self.ctx
        n = len(pos)
        sh = np.zeros((n, 4), np.float32)
        inc = np.zeros((n, 4), np.float32)
        extra = np.zeros((n, 12), np.float32)
        if not n:
            return sh, inc, extra
        if self.tracer is not None:
            return self._optix_texels(pos, nrm, geo, use_lights, use_sky, seed)
        order = np.argsort(_morton(pos[:, :3], pos[:, :3].min(0), pos[:, :3].max(0)))
        lp = self.light_array[:, 0, :3]
        lr = self.light_array[:, 0, 3]
        cs = self.trace
        for k, binding in enumerate(range(7)):
            self.buffers[k].bind_to_storage_buffer(binding)
        self.lights_buffer.bind_to_storage_buffer(9)
        self.sky_tex.use(0)
        cs['sky_cube'].value = 0
        sky = self.sky
        cs['sky_on'].value = 1 if (use_sky and sky) else 0
        if sky:
            cs['sky_colour'].value = tuple(float(v) * sky['brightness'] for v in sky['colour'])
            cs['sky_lower_black'].value = 1 if sky['lower_black'] else 0
            cs['sky_lower_colour'].value = tuple(float(v) for v in sky['lower_colour'])
            cs['sky_uniform'].value = self.sky_uniform
        cs['sky_rays'].value = SKY_RAYS
        cs['backface_rays'].value = BACKFACE_RAYS
        cs['has_emitters'].value = self.has_emitters
        ids = self.emitter_ids
        cs['emitter_count'].value = len(ids) if len(ids) <= MAX_EMITTERS else -1
        if 0 < len(ids) <= MAX_EMITTERS:
            cs['emitter_ids'].value = list(ids) + [0] * (MAX_EMITTERS - len(ids))
        cs['shadow_rays'].value = SHADOW_RAYS
        cs['penumbra_angle'].value = PENUMBRA_ANGLE
        cs['shadow_exponent'].value = SHADOW_EXPONENT
        cs['ray_offset'].value = RAY_OFFSET
        cs['normal_offset_scale'].value = NORMAL_OFFSET_SCALE
        cs['min_normal_offset'].value = MIN_NORMAL_OFFSET
        cs['sky_ray_length'].value = SKY_RAY_LENGTH
        cs['seed'].value = seed
        hemi = (SKY_RAYS if (use_sky and sky) or self.has_emitters else 0) * (
            CLOSEST_COST if self.has_emitters else 1)

        def lights_for(ix):
            if not (use_lights and len(self.lights)):
                return np.zeros(0, np.int64)
            pp = pos[ix, :3]
            near = np.clip(lp, pp.min(0), pp.max(0))
            return np.nonzero(((near - lp) ** 2).sum(1) < lr ** 2)[0]

        at = 0
        while at < n:
            # A dispatch that runs too long trips the driver's context-switch timeout
            # (Xid 109) and returns garbage: size each one to DISPATCH_RAYS.
            size = DISPATCH_TEXELS
            while True:
                idx = order[at:at + size]
                hit = lights_for(idx)
                cost = len(idx) * (hemi + SHADOW_RAYS * len(hit) + 1)
                if cost <= DISPATCH_RAYS or size <= MIN_DISPATCH_TEXELS:
                    break
                size //= 2
            at += len(idx)
            p = np.ascontiguousarray(pos[idx], np.float32)
            q = np.ascontiguousarray(nrm[idx], np.float32)
            g = np.ascontiguousarray(geo[idx], np.float32)
            ll = ctx.buffer(np.ascontiguousarray(
                hit if len(hit) else np.zeros(1), np.int32).tobytes())
            bp, bn = ctx.buffer(p.tobytes()), ctx.buffer(q.tobytes())
            bg = ctx.buffer(g.tobytes())
            bg.bind_to_storage_buffer(15)
            oa, ob = ctx.buffer(reserve=len(p) * 16), ctx.buffer(reserve=len(p) * 16)
            oc = ctx.buffer(reserve=len(p) * 16)
            oc.bind_to_storage_buffer(13)
            od = ctx.buffer(reserve=len(p) * 16)
            od.bind_to_storage_buffer(14)
            bp.bind_to_storage_buffer(7)
            bn.bind_to_storage_buffer(8)
            ll.bind_to_storage_buffer(10)
            oa.bind_to_storage_buffer(11)
            ob.bind_to_storage_buffer(12)
            cs['texel_count'].value = len(p)
            cs['light_count'].value = len(hit)
            cs.run(group_x=(len(p) + GROUP - 1) // GROUP)
            ctx.finish()
            sh[idx] = np.frombuffer(oa.read(), np.float32).reshape(-1, 4)
            inc[idx] = np.frombuffer(ob.read(), np.float32).reshape(-1, 4)
            extra[idx, :4] = np.frombuffer(oc.read(), np.float32).reshape(-1, 4)
            extra[idx, 4:8] = np.frombuffer(od.read(), np.float32).reshape(-1, 4)
            extra[idx, 8:10] = extra[idx, 2:4]
            extra[idx, 10] = extra[idx, 7]
            extra[idx, 2:4] = 0.0
            extra[idx, 7] = 0.0
            for b in (ll, bp, bn, bg, oa, ob, oc, od):
                b.release()
        if not (np.isfinite(sh).all() and np.isfinite(inc).all() and np.isfinite(extra).all()):
            raise ValueError('the GPU returned non-finite light (a dispatch timed out?)')
        return sh, inc, extra

    def _optix_texels(self, pos, nrm, geo, use_lights, use_sky, seed):
        from riftlord.build.lighting import lighttrace
        n = len(pos)
        sh = np.zeros((n, 4), np.float32)
        inc = np.zeros((n, 4), np.float32)
        extra = np.zeros((n, 12), np.float32)
        order = np.argsort(_morton(pos[:, :3], pos[:, :3].min(0), pos[:, :3].max(0)))
        settings = {'sky_rays': SKY_RAYS, 'backface_rays': BACKFACE_RAYS,
                    'shadow_rays': SHADOW_RAYS, 'penumbra_angle': PENUMBRA_ANGLE,
                    'shadow_exponent': SHADOW_EXPONENT, 'ray_offset': RAY_OFFSET,
                    'normal_offset_scale': NORMAL_OFFSET_SCALE,
                    'min_normal_offset': MIN_NORMAL_OFFSET, 'sky_ray_length': SKY_RAY_LENGTH}
        for at in range(0, n, OPTIX_TEXELS):
            idx = order[at:at + OPTIX_TEXELS]
            a, b, c = lighttrace.trace_texels(
                self.tracer, pos[idx], nrm[idx], geo[idx], self.light_array if len(self.lights)
                else np.zeros((0, 5, 4), np.float32), self.sky if use_sky else None,
                self.sky_cube, settings, use_lights=use_lights, use_sky=use_sky,
                seed=seed + at)
            sh[idx], inc[idx], extra[idx] = a, b, c
        return sh, inc, extra

    # -- one sublevel
    def atlases(self, path):
        """{texture export index: [component]} for the components of one package."""
        bd = self.bds[path]
        out = {}
        for c in self.comps[path]:
            lm = c['lightmap']
            if lm['type'] != 2:
                continue
            ti = bd.texture_ref(lm['textures'][0])
            if ti is None or ti not in bd.textures:
                continue
            out.setdefault(ti, []).append(c)
        return out

    def bake_atlas(self, path, ti, comps, **kw):
        """[(component, rect, c0 (h, w, 3), c1, mapped (h, w), normal (h, w, 3),
        lights (h, w, 3): the Static lights' share of the shading, emitters (h, w, 3):
        the emissive surfaces' share)]."""
        from riftlord.build.lighting import lightcodec
        bd = self.bds[path]
        dims = bd.textures[ti]['formats'][0]['data']['mips'][0]['dims']
        size_x, half_y = dims[0], dims[1] // 2
        pos, nrm, geo = self.gbuffer(comps, size_x, half_y)
        mapped = nrm[..., 3] > 0
        sh, inc, extra = self.light_texels(pos[mapped], nrm[mapped], geo[mapped], **kw)
        # Texels whose sky rays mostly hit back faces lie inside other geometry (rocks
        # sunk into terrain): Lightmass treats them as inside geometry; nobody sees them.
        # They are dropped and refilled from their visible neighbours by dilation.
        inside = np.zeros(mapped.shape, bool)
        inside[mapped] = extra[:, 0] > INSIDE_GEOMETRY_SHARE
        c0f, c1f = coefficients(sh.astype(np.float64), inc.astype(np.float64))
        c0 = np.zeros((half_y, size_x, 3))
        c1 = np.zeros((half_y, size_x, 3))
        c0[mapped], c1[mapped] = c0f, c1f
        #: the Static lights' share of the shading at the vertex normal (Incident / pi:
        #: shading at the normal the samples were gathered with is linear in the lights)
        lit = np.zeros((half_y, size_x, 3))
        lit[mapped] = extra[:, 4:7] / math.pi
        lava = np.zeros((half_y, size_x, 3))
        lava[mapped] = extra[:, 8:11] / math.pi
        owner = np.rint(nrm[..., 3]).astype(np.int64) - 1
        owner[inside] = -1
        if DENOISE:
            # chart id unique across the atlas: component and chart together
            chart = owner * CHART_STRIDE + np.rint(geo[..., 3]).astype(np.int64)
            t0 = time.time()
            c0 = denoise_charts(c0, owner >= 0, chart)
            self.denoise_seconds = getattr(self, 'denoise_seconds', 0.0) + time.time() - t0
        out = []
        for ci, c in enumerate(comps):
            lm = c['lightmap']
            x0, y0, x1, y1 = lightcodec.allocation_rect(
                lm['coord_scale'], lm['coord_bias'], size_x, half_y)
            if x1 <= x0 or y1 <= y0:
                continue
            own = owner[y0:y1, x0:x1] == ci
            out.append((c, (x0, y0, x1, y1), c0[y0:y1, x0:x1].copy(),
                        c1[y0:y1, x0:x1].copy(), own, nrm[y0:y1, x0:x1, :3].copy(),
                        lit[y0:y1, x0:x1].copy(), lava[y0:y1, x0:x1].copy()))
        return out


def uv_charts(m):
    """Chart number per triangle: triangles joined through corners that share a light
    map UV and a position (vertices split only by normals or materials are welded)."""
    key = np.concatenate([np.round(m['uv'] * UV_WELD_SCALE),
                          np.round(m['positions'] * POSITION_WELD_SCALE)], 1)
    _u, weld = np.unique(key, axis=0, return_inverse=True)
    weld = weld.ravel()
    tri = weld[m['triangles']]
    labels = np.arange(weld.max() + 1)
    while True:
        low = labels[tri].min(1)
        before = labels.copy()
        for k in range(3):
            np.minimum.at(labels, tri[:, k], low)
        labels = labels[labels]
        if np.array_equal(labels, before):
            break
    _c, chart = np.unique(labels[tri[:, 0]], return_inverse=True)
    return chart.ravel()


def denoise_charts(c0, mapped, chart):
    """c0 (h, w, 3) with each chart's texels denoised on their own (OIDN's RTLightmap
    filter on a crop of the chart, unmapped texels filled by dilation from the chart),
    so light never bleeds across charts, meshes or into unmapped texels."""
    out = c0.copy()
    ids = np.unique(chart[mapped])
    for cid in ids:
        sel = mapped & (chart == cid)
        count = int(sel.sum())
        if count < MIN_DENOISE_TEXELS:
            continue
        ys, xs = np.nonzero(sel)
        y0, y1 = max(ys.min() - DENOISE_MARGIN, 0), min(ys.max() + 1 + DENOISE_MARGIN, sel.shape[0])
        x0, x1 = max(xs.min() - DENOISE_MARGIN, 0), min(xs.max() + 1 + DENOISE_MARGIN, sel.shape[1])
        crop = c0[y0:y1, x0:x1]
        own = sel[y0:y1, x0:x1]
        filled, _ = dilate(crop, np.zeros_like(crop), own, steps=DENOISE_MARGIN + 2)
        got = _oidn(filled)
        if got is not None:
            out[y0:y1, x0:x1][own] = got[own]
    return out


_OIDN = {}


def _oidn(img):
    """OIDN RTLightmap (HDR, no guides) on one float image, CPU device; None without
    pyoidn."""
    try:
        import pyoidn
    except ImportError:
        return None
    if 'device' not in _OIDN:
        dev = pyoidn.Device(pyoidn.OIDN_DEVICE_TYPE_CPU)
        dev.commit()
        _OIDN['device'] = dev
    src = np.ascontiguousarray(np.maximum(img, 0.0), np.float32)
    dst = np.zeros_like(src)
    f = pyoidn.Filter(_OIDN['device'], pyoidn.OIDN_FILTER_TYPE_RT_LIGHTMAP)
    f.set_image(pyoidn.OIDN_IMAGE_COLOR, src, pyoidn.OIDN_FORMAT_FLOAT3)
    f.set_image(pyoidn.OIDN_IMAGE_OUTPUT, dst, pyoidn.OIDN_FORMAT_FLOAT3)
    f.commit()
    f.execute()
    f.release()
    return dst


def _texel_radius(m, mat, coord_scale, size_x, half_y):
    """World radius of one light map texel on this placement (sqrt of world area per
    texel area), floored at SMALLEST_TEXEL_RADIUS."""
    p = m['positions'][m['triangles']].astype(np.float64)
    w = p @ mat[:3, :3].T
    wa = 0.5 * np.linalg.norm(np.cross(w[:, 1] - w[:, 0], w[:, 2] - w[:, 0]), axis=1).sum()
    uv = m['uv'][m['triangles']].astype(np.float64)
    e1, e2 = uv[:, 1] - uv[:, 0], uv[:, 2] - uv[:, 0]
    ua = 0.5 * np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]).sum()
    texels = ua * coord_scale[0] * size_x * coord_scale[1] * half_y
    if texels <= 0 or wa <= 0:
        return SMALLEST_TEXEL_RADIUS
    return max(SMALLEST_TEXEL_RADIUS, math.sqrt(wa / texels) * 0.5)


# ------------------------------------------------------------------ encoding

def dilate(c0, c1, mapped, steps=DILATE_STEPS):
    """Fill unmapped texels of one allocation from mapped neighbours (3x3 average,
    `steps` times), then the rest with the mean, so bilinear filtering and mips do
    not pull in black."""
    c0, c1, have = c0.copy(), c1.copy(), mapped.copy()
    if not have.any():
        return c0, c1
    for _ in range(steps):
        if have.all():
            break
        acc0 = np.zeros_like(c0)
        acc1 = np.zeros_like(c1)
        cnt = np.zeros(have.shape)
        pad = lambda a: np.pad(a, [(1, 1), (1, 1)] + [(0, 0)] * (a.ndim - 2))  # noqa: E731
        h0, h1, hv = pad(c0 * have[..., None]), pad(c1 * have[..., None]), pad(have.astype(float))
        hh, ww = have.shape
        for dy in (0, 1, 2):
            for dx in (0, 1, 2):
                acc0 += h0[dy:dy + hh, dx:dx + ww]
                acc1 += h1[dy:dy + hh, dx:dx + ww]
                cnt += hv[dy:dy + hh, dx:dx + ww]
        grow = ~have & (cnt > 0)
        c0[grow] = acc0[grow] / cnt[grow][:, None]
        c1[grow] = acc1[grow] / cnt[grow][:, None]
        have = have | grow
    if not have.all():
        c0[~have] = c0[have].mean(0)
        c1[~have] = c1[have].mean(0)
    return c0, c1


def write_atlas(bd, ti, results, light_guids=None):
    """Quantise each mesh (Lightmass per-mesh scale/add), place its texels in the HQ and
    LQ atlases, rebuild the mips and re-encode at the shipped sizes."""
    from riftlord.build.lighting import lightcodec
    from riftlord.build.lighting import lightmaps
    hq = bd.textures[ti]['formats'][0]['data']['mips'][0]['dims']
    size_x, half_y = hq[0], hq[1] // 2
    images = {}
    for c, (x0, y0, x1, y1), c0, c1, mapped, _n, _lit, _lava in results:
        lm = c['lightmap']
        if lm['type'] != lightmaps.LMT_2D:
            continue
        d0, d1 = dilate(c0, c1, mapped)
        texels, scale, add = lightcodec.quantize(d0, d1, mapped=mapped if mapped.any() else None)
        for slot, coef in ((0, 0), (1, 2)):
            t = bd.texture_ref(lm['textures'][slot])
            if t is None or t not in bd.textures:
                continue
            dims = bd.textures[t]['formats'][0]['data']['mips'][0]['dims']
            if (dims[0], dims[1] // 2) != (size_x, half_y):
                raise ValueError('HQ and LQ atlases differ in size')
            entry = images.setdefault(t, {
                'img': np.zeros((half_y * 2, size_x, 4), np.uint8),
                'mask': np.zeros((half_y * 2, size_x), bool)})
            entry['img'][y0:y1, x0:x1] = texels[..., coef, :]
            entry['img'][half_y + y0:half_y + y1, x0:x1] = texels[..., coef + 1, :]
            entry['mask'][y0:y1, x0:x1] = True
            entry['mask'][half_y + y0:half_y + y1, x0:x1] = True
        for k in range(4):
            lm['scale'][k] = tuple(float(v) for v in scale[k])
            lm['add'][k] = tuple(float(v) for v in add[k])
        if light_guids is not None:
            lm['light_guids'] = list(light_guids.get(c['guid'], []))
    for t, entry in images.items():
        fmt_entry = bd.textures[t]['formats'][0]
        fmt = fmt_entry['pixel_format']
        chain = fmt_entry['data']['mips']
        # keep the texels of meshes not rebaked here: start from the shipped top mip
        shipped = lightcodec.decode(fmt, chain[0]['dims'][0], chain[0]['dims'][1],
                                    chain[0]['payload'])
        img = np.where(entry['mask'][..., None], entry['img'], shipped)
        mask = np.ones(entry['mask'].shape, bool)
        levels = lightcodec.mips(img, len(chain), mask)
        top_w, top_h = chain[0]['dims'][:2]
        for k, (mip, im) in enumerate(zip(chain, levels)):
            w, h = max(top_w >> k, 1), max(top_h >> k, 1)
            if im.shape[:2] != (h, w):
                raise ValueError('mip %dx%d, built %dx%d' % (w, h, im.shape[1], im.shape[0]))
            payload = lightcodec.encode(fmt, im)
            if len(payload) != len(mip['payload']):
                raise ValueError('mip %dx%d encodes to %d bytes, shipped %d'
                                 % (w, h, len(payload), len(mip['payload'])))
            mip['payload'] = payload


# ---------------------------------------------------------------- comparison

def shipped_shading(bd, c, rect, normal):
    """Shipped light map of one component decoded and shaded with `normal` (h, w, 3)."""
    from riftlord.build.lighting import lightcodec
    lm = c['lightmap']
    ti = bd.texture_ref(lm['textures'][0])
    fmt = bd.textures[ti]['formats'][0]
    top = fmt['data']['mips'][0]
    key = (id(bd), ti)
    img = _DECODED.get(key)
    if img is None:
        _DECODED.clear()
        img = lightcodec.decode(fmt['pixel_format'], top['dims'][0], top['dims'][1],
                                top['payload'])
        _DECODED[key] = img
    half_y = top['dims'][1] // 2
    x0, y0, x1, y1 = rect
    t0 = img[y0:y1, x0:x1]
    t1 = img[half_y + y0:half_y + y1, x0:x1]
    n = normal
    out = np.zeros(t0.shape[:2] + (3,))
    s0 = np.asarray(lm['scale']), np.asarray(lm['add'])
    t0f, t1f = t0.astype(np.float64) / 255.0, t1.astype(np.float64) / 255.0
    log_l = (t0f[..., 3] + t1f[..., 3] / 255.0 - 0.5 / 255.0) * s0[0][0][3] + s0[1][0][3]
    uvw = t0f[..., :3] ** 2 * s0[0][0][:3] + s0[1][0][:3]
    lum = 2.0 ** log_l - 0.01858136
    sh = t1f * s0[0][1] + s0[1][1]
    basis = np.stack([n[..., 1], n[..., 2], n[..., 0], np.ones(n.shape[:2])], -1)
    d = np.maximum(0.0, (sh * basis).sum(-1))
    out = (lum * d)[..., None] * uvw
    return out


_DECODED = {}


def tonemap(rgb, exposure=1.0):
    """8-bit sRGB of linear light, Reinhard on luminance-free channels."""
    x = np.maximum(rgb * exposure, 0.0)
    x = x / (1.0 + x)
    return np.clip(255.0 * np.where(x <= 0.0031308, 12.92 * x,
                                    1.055 * x ** (1 / 2.4) - 0.055), 0, 255).astype(np.uint8)


def compare(level, tags, out_dir, lights_on=True, sky_on=True, images=12, atlases=None,
            log=print):
    """Re-bake `tags` of an unedited level and compare with its shipped light maps.

    Writes stats.json and side-by-side PNGs (shipped | ours | ours, Static lights only |
    ours/shipped) into
    out_dir; returns the stats."""
    from riftlord.build.lighting import lightscene
    from PIL import Image
    os.makedirs(out_dir, exist_ok=True)
    guids = set()
    for path in lightscene.packages(level):
        bd = lightscene.registry(path)
        if bd is not None:
            guids |= lightscene.static_light_guids(bd)
    lights = lightscene.lights(level, guids)
    sky = lightscene.sky_light(level)
    baker = Baker(level, lights, sky, tags=tags, log=log)
    stats = {'level': level, 'lights': len(lights), 'sky': sky, 'tags': {},
             'settings': {'SKY_RAYS': SKY_RAYS, 'SHADOW_RAYS': SHADOW_RAYS,
                          'DENOISE': DENOISE, 'backend': None,
                          'lights_on': lights_on, 'sky_on': sky_on}}
    stats['settings']['backend'] = baker.backend
    for path in baker.packages:
        tag = lightscene.tag_of(level, path)
        if tag not in tags or path not in baker.bds:
            continue
        bd = baker.bds[path]
        rows = []
        t0 = time.time()
        for ti, comps in sorted(baker.atlases(path).items())[:atlases]:
            for c, rect, c0, c1, mapped, n, lit, _lava in baker.bake_atlas(
                    path, ti, comps, use_lights=lights_on, use_sky=sky_on):
                if not mapped.any():
                    continue
                ours = shade(c0, c1, n)
                ship = shipped_shading(bd, c, rect, n)
                lo = (ours @ LUMA)[mapped]
                ls = (ship @ LUMA)[mapped]
                err = np.abs(ours - ship).max(-1)[mapped]
                rel = np.abs(lo - ls) / np.maximum(ls, REL_FLOOR)
                ll = (lit @ LUMA)[mapped]
                ratio = lo / np.maximum(ls, REL_FLOOR)
                lights_dom = ll > LIGHT_DOMINATED * np.maximum(lo - ll, 0.0)
                rows.append({'mesh': c['mesh'], 'export': c['export'], 'atlas': ti,
                             'texels': int(mapped.sum()),
                             'median_abs': float(np.median(err)),
                             'p90_abs': float(np.percentile(err, 90)),
                             'median_rel': float(np.median(rel)),
                             'p90_rel': float(np.percentile(rel, 90)),
                             'shipped_lum': float(ls.mean()), 'ours_lum': float(lo.mean()),
                             'ratio': float(lo.sum() / max(ls.sum(), 1e-9)),
                             'over': int((ratio > OVERSHOOT).sum()),
                             'lights_lum': float(ll.mean()),
                             'lit_texels': int(lights_dom.sum()),
                             'lit_ratio': _sample(np.stack([ll, ratio], 1)[lights_dom]),
                             'rect': list(rect)})
                lit_share = rows[-1]['lit_texels'] / max(rows[-1]['texels'], 1)
                kind = 'lit' if lit_share > IMAGE_LIT_SHARE else 'any'
                if len([r for r in rows if r.get('image_kind') == kind]) < images \
                        and mapped.sum() > IMAGE_MIN_TEXELS and _worth_image(c['mesh'], rows):
                    rows[-1]['image_kind'] = kind
                    img = np.concatenate([tonemap(ship), tonemap(ours), tonemap(lit),
                                          _ratio_image(ours, ship, mapped)], 1)
                    name = '%s-%s-%d.png' % (tag, c['mesh'], c['export'])
                    big = Image.fromarray(img).resize(
                        (img.shape[1] * max(1, 256 // img.shape[0]),
                         img.shape[0] * max(1, 256 // img.shape[0])), Image.NEAREST)
                    big.save(os.path.join(out_dir, name))
                    rows[-1]['image'] = name
            log('%s atlas %d: %d meshes so far (%.0fs)' % (tag, ti, len(rows), time.time() - t0))
        stats['tags'][tag] = _summary(rows)
        stats['tags'][tag]['seconds'] = round(time.time() - t0, 1)
        for r in rows:
            r.pop('lit_ratio', None)
        stats['tags'][tag]['meshes'] = rows
    with open(os.path.join(out_dir, 'stats.json'), 'w') as fh:
        json.dump(stats, fh, indent=1)
    return stats


def _sample(pairs):
    if len(pairs) > LIT_SAMPLES:
        pairs = pairs[np.random.default_rng(0).choice(len(pairs), LIT_SAMPLES, replace=False)]
    return pairs.tolist()


def _worth_image(mesh, rows):
    return sum(1 for r in rows[:-1] if r['mesh'] == mesh and r.get('image')) == 0


def _ratio_image(ours, ship, mapped):
    """ours/shipped luminance: grey 1:1, red brighter, blue darker (log2, +-2 stops)."""
    r = np.log2(np.maximum(ours @ LUMA, 1e-4) / np.maximum(ship @ LUMA, 1e-4))
    t = np.clip(r / 2.0, -1, 1)
    img = np.zeros(ours.shape[:2] + (3,), np.uint8)
    img[..., 0] = np.clip(128 + 127 * t, 0, 255)
    img[..., 1] = np.clip(128 - 127 * np.abs(t), 0, 255)
    img[..., 2] = np.clip(128 - 127 * t, 0, 255)
    img[~mapped] = 0
    return img


def _summary(rows):
    if not rows:
        return {}
    w = np.array([r['texels'] for r in rows], np.float64)

    def wq(key, q):
        v = np.array([r[key] for r in rows])
        o = np.argsort(v)
        cw = np.cumsum(w[o]) / w.sum()
        return float(v[o][np.searchsorted(cw, q)])
    ratio = np.array([r['ratio'] for r in rows])
    lit = np.concatenate([np.asarray(r['lit_ratio']).reshape(-1, 2) for r in rows]
                         + [np.zeros((0, 2))])
    bins = {}
    for lo_, hi_ in LIT_BINS:
        sel = (lit[:, 0] >= lo_) & (lit[:, 0] < hi_)
        if sel.sum():
            bins['%g-%g' % (lo_, hi_)] = [int(sel.sum())] + [
                round(float(np.percentile(lit[sel, 1], q)), 3) for q in (10, 50, 90)]
    lit = lit[:, 1]
    return {'count': len(rows), 'texels': int(w.sum()),
            'overshoot_share': float(sum(r['over'] for r in rows) / max(w.sum(), 1)),
            'light_dominated_texels': int(sum(r['lit_texels'] for r in rows)),
            'light_dominated_ratio_p10_p50_p90': [float(np.percentile(lit, q)) for q in (10, 50, 90)]
            if len(lit) else None,
            'light_dominated_ratio_by_light_level': bins,
            'median_of_mesh_median_rel': float(np.median([r['median_rel'] for r in rows])),
            'median_of_mesh_p90_rel': float(np.median([r['p90_rel'] for r in rows])),
            'median_of_mesh_median_abs': float(np.median([r['median_abs'] for r in rows])),
            'median_of_mesh_p90_abs': float(np.median([r['p90_abs'] for r in rows])),
            'texel_weighted_median_rel': wq('median_rel', 0.5),
            'median_ratio': float(np.median(ratio)),
            'ratio_p10_p90': [float(np.percentile(ratio, 10)), float(np.percentile(ratio, 90))],
            'total_ratio': float((np.array([r['ours_lum'] for r in rows]) * w).sum()
                                 / max((np.array([r['shipped_lum'] for r in rows]) * w).sum(), 1e-9))}


# --------------------------------------------------------------- calibration

def calibrate(level, tags, out_dir, images=12, log=print):
    """Fit the emissive radiance of a level's lava against its shipped light maps.

    Bakes `tags` with the level's Static lights, its sky light and its lava surfaces
    (lightscene.lava_piece_ids) emitting unit white radiance, so each texel has the rest
    of its light and the lava's share separately. The fit used (LAVA_RADIANCE) is joint,
    per channel, over every texel the lava reaches (lava shading > LAVA_MIN):
    shipped = a * rest + E * lava, `a` absorbing the bounce light we do not bake. Also
    reported: least squares and a per-texel median over texels the lava dominates
    (LAVA_DOMINATED). Writes calibration.json and images (shipped | fitted | lava share
    | log ratio)"""
    from riftlord.build.lighting import lightscene
    from PIL import Image
    os.makedirs(out_dir, exist_ok=True)
    guids = set()
    for path in lightscene.packages(level):
        bd = lightscene.registry(path)
        if bd is not None:
            guids |= lightscene.static_light_guids(bd)
    lights = lightscene.lights(level, guids)
    sky = lightscene.sky_light(level)
    ids = lightscene.lava_piece_ids(level)
    baker = Baker(level, lights, sky, tags=tags, emitters=(ids, (1.0, 1.0, 1.0)), log=log)
    num, den = np.zeros(3), np.zeros(3)
    samples, est, joint = [], [], []
    for path in baker.packages:
        tag = lightscene.tag_of(level, path)
        if tag not in tags or path not in baker.bds:
            continue
        bd = baker.bds[path]
        for ti, comps in sorted(baker.atlases(path).items()):
            for c, rect, c0, c1, mapped, n, _lit, lava in baker.bake_atlas(path, ti, comps):
                if not mapped.any():
                    continue
                ours = shade(c0, c1, n)
                rest = ours - lava
                ship = shipped_shading(bd, c, rect, n)
                ll, lr = lava @ LUMA, rest @ LUMA
                near = mapped & (ll > LAVA_MIN)
                if near.any():
                    joint.append(_sample(np.concatenate(
                        [rest[near], lava[near], ship[near]], 1)))
                dom = mapped & (ll > LAVA_MIN) & (ll > LAVA_DOMINATED * lr)
                if not dom.any():
                    continue
                u, r, t = lava[dom], rest[dom], ship[dom]
                num += (u * (t - r)).sum(0)
                den += (u * u).sum(0)
                ul = (u @ LUMA)
                est.append(_sample(np.concatenate(
                    [((t - r) @ LUMA / ul)[:, None], (t / np.maximum(t @ LUMA, 1e-6)[:, None])],
                    1)))
                samples.append((c, rect, ship, rest, lava, mapped, int(dom.sum()), tag))
            log('%s atlas %d: %d lava-lit meshes' % (tag, ti, len(samples)))
            _DECODED.clear()
    lsq = num / np.maximum(den, 1e-12)
    #: joint fit over every texel the lava reaches (lava > LAVA_MIN), per channel:
    #: shipped = a * rest + E * lava, `a` absorbing the bounce light we do not bake
    jj = np.concatenate([np.asarray(x).reshape(-1, 9) for x in joint if len(x)])
    joint_a, joint_e = [], []
    for ch in range(3):
        A = np.stack([jj[:, ch], jj[:, 3 + ch]], 1)
        sol = np.linalg.lstsq(A, jj[:, 6 + ch], rcond=None)[0]
        joint_a.append(float(sol[0]))
        joint_e.append(float(sol[1]))
    #: robust fit: the median per-texel luminance (shipped - rest) / lava, in the median
    #: shipped chromaticity of those texels
    e = np.concatenate([np.asarray(x).reshape(-1, 4) for x in est if len(x)])
    chroma = np.median(e[:, 1:], 0)
    chroma = chroma / max(float(chroma @ LUMA), 1e-6)
    robust = float(np.median(e[:, 0])) * chroma
    rows, ratios = [], []
    samples.sort(key=lambda r: -r[6])
    #: the joint fit is the one used (LAVA_RADIANCE); the images and ratios show it
    radiance_used, scale_used = np.asarray(joint_e), np.asarray(joint_a)
    for k, (c, rect, ship, rest, lava, mapped, count, tag) in enumerate(samples):
        fit = rest * scale_used + lava * radiance_used
        ll = lava @ LUMA
        dom = mapped & (ll > LAVA_MIN)
        ratio = (fit @ LUMA)[dom] / np.maximum((ship @ LUMA)[dom], REL_FLOOR)
        ratios.append(ratio)
        row = {'mesh': c['mesh'], 'export': c['export'], 'tag': tag, 'texels': count,
               'fitted_over_shipped_median': float(np.median(ratio))}
        if k < images:
            img = np.concatenate([tonemap(ship), tonemap(fit), tonemap(lava * radiance_used),
                                  _ratio_image(fit, ship, mapped)], 1)
            name = 'lava-%s-%s-%d.png' % (tag, c['mesh'], c['export'])
            s_ = max(1, 256 // img.shape[0])
            Image.fromarray(img).resize((img.shape[1] * s_, img.shape[0] * s_),
                                        Image.NEAREST).save(os.path.join(out_dir, name))
            row['image'] = name
        rows.append(row)
    allr = np.concatenate(ratios) if ratios else np.zeros(1)
    result = {'level': level, 'emitter_pieces': len(ids),
              'emitter_instances': baker.emitter_count,
              'radiance_robust_median': [float(v) for v in robust],
              'radiance_least_squares': [float(v) for v in lsq],
              'joint_fit': {'texels_sampled': int(len(jj)), 'rest_scale': joint_a,
                            'radiance': joint_e},
              'per_texel_luminance_p10_p50_p90': [float(np.percentile(e[:, 0], q))
                                                  for q in (10, 50, 90)],
              'shipped_chromaticity': [float(v) for v in chroma],
              'lava_dominated_texels': int(sum(r['texels'] for r in rows)),
              'joint_fitted_over_shipped_p10_p50_p90_lava_reached': [float(np.percentile(allr, q))
                                                  for q in (10, 50, 90)],
              'settings': {'SKY_RAYS': SKY_RAYS, 'LAVA_MIN': LAVA_MIN,
                           'LAVA_DOMINATED': LAVA_DOMINATED},
              'meshes': rows}
    with open(os.path.join(out_dir, 'calibration.json'), 'w') as fh:
        json.dump(result, fh, indent=1)
    return result


# --------------------------------------------------------------------- build

def bake(level, tags, out_dir, spec=None, log=print):
    """Bake `tags` of `level` and write each one's BuiltData blobs into out_dir as
    `<leaf>_BuiltData.{uasset,uexp,ubulk}` (still under the shipped package name; the
    build hook renames it). `spec` (all optional):

    * 'plan': a Rift Lord plan (its removed, edits and added pieces change the scene);
    * 'light_colours': {'<tag>|<export>': [r, g, b]} new LightColor of a light component,
      as riftlord.liquidlights gives it (FColor / 255, sRGB);
    * 'sky_level': take the sky light from this level instead (a chosen sky's home).

    Returns a report."""
    from riftlord.build.lighting import lightscene
    spec = spec or {}
    os.makedirs(out_dir, exist_ok=True)
    guids = set()
    for path in lightscene.packages(level):
        bd = lightscene.registry(path)
        if bd is not None:
            guids |= lightscene.static_light_guids(bd)
    lights = lightscene.lights(level, guids)
    recolour = spec.get('light_colours') or {}
    done = 0
    for l in lights:
        key = '%s|%d' % (lightscene.tag_of(level, l['package']), l['export'])
        if key in recolour:
            l['colour'] = lightscene.linear_colour([v * 255.0 for v in recolour[key]])
            done += 1
    sky = lightscene.sky_light(spec.get('sky_level') or level)
    if spec.get('sky') and SKY_FROM_TEXTURE and sky:
        sky = sky_from_texture(sky, spec['sky'], level, fog=spec.get('sky_fog'), log=log)
    for k, at in enumerate(spec.get('lava_lights') or []):
        light = dict(LAVA_LIGHT, position=[float(v) for v in at], guid=None,
                     package='lava', export=k + 1, direction=[1.0, 0.0, 0.0],
                     tangent=[0.0, 0.0, 1.0])
        lights.append(light)
    emitters = None
    if spec.get('lava_emitters', True):
        ids = lightscene.lava_piece_ids(level, spec.get('plan'))
        if ids:
            emitters = (ids, spec.get('lava_radiance') or LAVA_RADIANCE)
    set_quality(spec.get('quality') or CURRENT_QUALITY)
    baker = Baker(level, lights, sky, tags=tags, plan=spec.get('plan'), emitters=emitters,
                  log=log)
    cache = spec.get('sublevel_cache')
    report = {'level': level, 'lights': len(lights), 'recoloured': done,
              'recolour_asked': len(recolour), 'sky_level': spec.get('sky_level') or level,
              'sky': {k: v for k, v in (sky or {}).items() if k != 'texture_cube'},
              'plan': baker.plan_counts, 'tags': {},
              'lava_lights': len(spec.get('lava_lights') or []),
              'lava_emitter_instances': baker.emitter_count,
              'lava_radiance': list(emitters[1]) if emitters else None,
              'settings': {'SKY_RAYS': SKY_RAYS, 'SHADOW_RAYS': SHADOW_RAYS,
                           'DENOISE': DENOISE, 'quality': CURRENT_QUALITY,
                           'backend': baker.backend, 'DIRECT_ONLY': DIRECT_ONLY}}
    for path in baker.packages:
        tag = lightscene.tag_of(level, path)
        if tag not in tags or path not in baker.bds:
            continue
        bd = baker.bds[path]
        t0 = time.time()
        leaf = path.rsplit('/', 1)[-1] + lightscene.BUILT_DATA
        key = sublevel_key(baker, path, sky, spec) if cache else None
        if key and _cached(cache, leaf, key, out_dir):
            report['tags'][tag] = {'package': path + lightscene.BUILT_DATA, 'cached': key,
                                   'seconds': round(time.time() - t0, 1)}
            log('%s: cached (%s)' % (tag, key))
            continue
        meshes = 0
        errors = []
        for ti, comps in sorted(baker.atlases(path).items()):
            results = baker.bake_atlas(path, ti, comps)
            guids_of = _lights_reaching(results, lights)
            write_atlas(bd, ti, results, guids_of)
            errors.append(encode_error(bd, results))
            meshes += len(results)
            log('%s atlas %d: %d meshes (%.0fs)' % (tag, ti, meshes, time.time() - t0))
        blobs = bd.write()
        for ext, blob in zip(('.uasset', '.uexp', '.ubulk'), blobs):
            with open(os.path.join(out_dir, leaf + ext), 'wb') as fh:
                fh.write(blob)
        if key:
            _store(cache, leaf, key, out_dir)
        err = np.concatenate([e for e in errors if len(e)] or [np.zeros(1)])
        report['tags'][tag] = {'meshes': meshes, 'package': path + lightscene.BUILT_DATA,
                               'seconds': round(time.time() - t0, 1),
                               'encode_error_rel_p50_p90_p99': [
                                   round(float(np.percentile(err, q)), 4) for q in (50, 90, 99)]}
    with open(os.path.join(out_dir, 'report.json'), 'w') as fh:
        json.dump(report, fh, indent=1, default=str)
    return report


def sky_from_texture(sky, material, level, fog=None, log=print):
    """The home sky light lit by the chosen sky material's own texture.

    The texture is laid on the level's skybox mesh as the material samples it
    (lightscene.sky_texture_cube: UV0, Desaturation, Color) and scaled so its upper
    hemisphere's mean luminance equals the home sky light's (its cubemap's upper mean
    times its LightColor's luminance): the texture gives the colour and where the light
    comes from; the home SkyLight keeps the brightness, Intensity and
    IndirectLightingIntensity. The colour is then carried by the texture alone.

    With `fog` ({'colour', 'opacity'}: the height fog the sky is seen through, which on
    every shipped level hides 90% of it), the sky is mixed with the fog colour by that
    opacity before scaling, as a sky light capturing the scene would see it (SKY_FOG)."""
    from riftlord.build.lighting import lightscene
    face = lightscene.SKY_FACE_SIZE
    cube = lightscene.sky_texture_cube(material, level, face)
    if cube is None or lightscene.upper_mean_luminance(cube) <= 0:
        log('sky texture of %s unreadable: keeping the home cubemap' % material)
        return sky
    fog_mix = 0.0
    if fog and SKY_FOG:
        fog_mix = min(max(float(fog['opacity']), 0.0), 1.0)
        tex = cube / lightscene.upper_mean_luminance(cube)
        fc = np.asarray(fog['colour'], np.float64)
        fl = float(fc @ LUMA)
        if fl > 0:
            cube = (1.0 - fog_mix) * tex + fog_mix * (fc / fl)
        else:
            fog_mix = 0.0
    if sky.get('cubemap'):
        home = lightscene.cubemap_faces(sky['cubemap'], face) * np.asarray(sky['colour'])
    else:
        home = np.ones((6, face, face, 3)) * np.asarray(sky['colour'])
    scale = lightscene.upper_mean_luminance(home) / lightscene.upper_mean_luminance(cube)
    log('sky: %s texture, fog mix %.2f, scaled x%.2f to the home sky light'
        % (material, fog_mix, scale))
    return dict(sky, texture_cube=cube * scale, colour=[1.0, 1.0, 1.0],
                sky_material=material, texture_scale=float(scale), fog_mix=fog_mix,
                fog=fog,
                home_colour=sky['colour'], lower_black=False)


def set_quality(name, backend=None):
    """Apply a QUALITIES preset (for the backend that will trace) to the module's ray
    counts and denoising."""
    global SKY_RAYS, SHADOW_RAYS, DENOISE, CURRENT_QUALITY
    q = QUALITIES[(backend or choose_backend(), name)]
    SKY_RAYS, SHADOW_RAYS, DENOISE = q['sky_rays'], q['shadow_rays'], q['denoise']
    CURRENT_QUALITY = name


def _digest(obj):
    import hashlib
    return hashlib.sha1(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _source_digest():
    """sha1 of every file of riftlord/build/lighting, by name and content."""
    import hashlib
    h = hashlib.sha1()
    for name in sorted(n for n in os.listdir(HERE) if n.endswith('.py')):
        with open(os.path.join(HERE, name), 'rb') as fh:
            h.update(name.encode() + b'\0' + fh.read())
    return h.hexdigest()


def sublevel_key(baker, path, sky, spec):
    """What one sublevel's bake depends on, hashed: its receivers and their placements,
    every occluder and light that reaches its bounds (padded by SUBLEVEL_REACH), the sky,
    the emitters, the quality and the baker's own source. Editing pieces elsewhere on the
    map leaves the key, and the cached bake, alone."""
    recv = [(c['export'], c['mesh'], [np.round(m, 2).tolist() for m in c['matrices']])
            for c in baker.comps[path]]
    pts = [m[:3, 3] for c in baker.comps[path] for m in c['matrices']]
    if not pts:
        return _digest(['empty', path, CURRENT_QUALITY, _source_digest()])
    lo = np.min(pts, 0) - SUBLEVEL_REACH
    hi = np.max(pts, 0) + SUBLEVEL_REACH
    occ = []
    for p, cs in sorted(baker.comps.items()):
        for c in cs:
            if not c['occluder']:
                continue
            for m in c['matrices']:
                t = m[:3, 3]
                if np.all(t >= lo) and np.all(t <= hi):
                    occ.append((c['mesh'], np.round(m, 2).tolist(), c.get('emitter'),
                                bool(c.get('two_sided'))))
    occ.sort(key=str)
    lights = [np.round(baker.light_array[k], 4).tolist() for k in range(len(baker.lights))
              if np.all(baker.light_array[k, 0, :3] >= lo - baker.light_array[k, 0, 3])
              and np.all(baker.light_array[k, 0, :3] <= hi + baker.light_array[k, 0, 3])]
    return _digest([path, recv, occ, lights, sky, CURRENT_QUALITY, SKY_RAYS, SHADOW_RAYS,
                    DENOISE, spec.get('lava_radiance') or LAVA_RADIANCE, _source_digest()])[:20]


def _cached(cache, leaf, key, out_dir):
    """Copy a cached sublevel bake into out_dir when it and its recorded inputs
    (bakeinputs) are unchanged; returns whether it did."""
    import shutil
    from riftlord.build.lighting import bakeinputs
    src = os.path.join(cache, '%s-%s' % (leaf, key))
    if not all(os.path.exists(src + ext) for ext in ('.uasset', '.uexp', '.ubulk')):
        return False
    if not bakeinputs.unchanged(bakeinputs.load(src + '.inputs.json'))[0]:
        return False
    for ext in ('.uasset', '.uexp', '.ubulk'):
        shutil.copyfile(src + ext, os.path.join(out_dir, leaf + ext))
    return True


def _store(cache, leaf, key, out_dir):
    """Keep a sublevel bake and its inputs so far (bakeinputs) under its key."""
    import shutil
    from riftlord.build.lighting import bakeinputs
    os.makedirs(cache, exist_ok=True)
    dst = os.path.join(cache, '%s-%s' % (leaf, key))
    for ext in ('.uasset', '.uexp', '.ubulk'):
        shutil.copyfile(os.path.join(out_dir, leaf + ext), dst + ext + '.part')
        os.replace(dst + ext + '.part', dst + ext)
    bakeinputs.write(dst + '.inputs.json')


def encode_error(bd, results):
    """Relative luminance error, per mapped texel, of the written HQ atlas (BC3 decoded,
    the shader's formula) against the float light it encodes."""
    out = []
    for c, rect, c0, c1, mapped, n, _lit, _lava in results:
        if not mapped.any() or c['lightmap']['type'] != 2:
            continue
        want = shade(c0, c1, n) @ LUMA
        got = shipped_shading(bd, c, rect, n) @ LUMA
        out.append((np.abs(got - want) / np.maximum(want, REL_FLOOR))[mapped])
    _DECODED.clear()
    return np.concatenate(out) if out else np.zeros(0)


def _lights_reaching(results, lights):
    """{component guid: [light guid]} for lights whose sphere reaches a mapped texel's
    neighbourhood (component bounds), for FLightMap2D.LightGuids."""
    out = {}
    for c, _rect, c0, _c1, mapped, _n, _lit, _lava in results:
        got = []
        if mapped.any() and c0[mapped].max() > 0:
            for m in c['matrices'][:1]:
                centre = m[:3, 3]
                for l in lights:
                    if l.get('guid') and np.linalg.norm(
                            np.asarray(l['position']) - centre) < l['radius'] + 2000:
                        got.append(l['guid'])
        out[c['guid']] = got
    return out


SKY_RAYS, SHADOW_RAYS, DENOISE = (QUALITIES[('optix', QUALITY)][k]
                                  for k in ('sky_rays', 'shadow_rays', 'denoise'))
CURRENT_QUALITY = QUALITY


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('bake', 'compare', 'calibrate'):
        p = sub.add_parser(name)
        p.add_argument('level')
        p.add_argument('--tag', action='append', required=True)
        p.add_argument('--out', required=True)
        p.add_argument('--spec')
        p.add_argument('--no-lights', action='store_true')
        p.add_argument('--no-sky', action='store_true')
        p.add_argument('--sky-rays', type=int)
        p.add_argument('--atlases', type=int, help='compare: only the first N atlases')
        p.add_argument('--quality', choices=sorted({n for _b, n in QUALITIES}))
    a = ap.parse_args(argv)
    global SKY_RAYS
    if a.quality:
        set_quality(a.quality)
    if a.sky_rays:
        SKY_RAYS = a.sky_rays
    if a.cmd == 'calibrate':
        r = calibrate(a.level, a.tag, a.out)
        print(json.dumps({k: v for k, v in r.items() if k != 'meshes'}, indent=1))
    elif a.cmd == 'compare':
        s = compare(a.level, a.tag, a.out, lights_on=not a.no_lights, sky_on=not a.no_sky,
                    atlases=a.atlases)
        print(json.dumps({t: {k: v for k, v in d.items() if k != 'meshes'}
                          for t, d in s['tags'].items()}, indent=1))
    else:
        from riftlord.build.lighting import bakeinputs
        spec = None
        if a.spec:
            with open(a.spec) as fh:
                spec = json.load(fh)
        bakeinputs.watch(skip=[a.out] + ([spec['sublevel_cache']] if (spec or {}).get(
            'sublevel_cache') else []))
        got = bake(a.level, a.tag, a.out, spec)
        bakeinputs.write(os.path.join(a.out, 'inputs.json'))
        print(json.dumps(got, indent=1, default=str))


if __name__ == '__main__':
    main()
