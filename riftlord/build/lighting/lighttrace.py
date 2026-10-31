"""Hardware ray tracing for the lightmap baker: Mitsuba 3 / Dr.Jit on OptiX (RT cores).

The OpenGL baker (lightbaker.TRACE_CS) traces through its own BVH in a compute shader.
This module does the same texel lighting with every ray handed to OptiX through
Mitsuba's `ray_test` (any hit) and `ray_intersect_preliminary` (closest hit), batched by
the million; the lighting maths around the rays (falloff, cones, rect lights, the sky
gather, the emitters, Lightmass's SH sample) runs vectorised in numpy and mirrors the
shader line for line, so the two backends give the same light up to sampling noise.

    tracer = OptixScene(components, meshes)          # occluders, once per bake
    sh, inc, extra = trace_texels(tracer, pos, nrm, geo, light_array, sky, cube, ...)

Needs the render venv with `mitsuba` (BSD-3-Clause) and `drjit` (BSD-3-Clause) and an
NVIDIA driver with OptiX; `available()` says whether it can run. lightbaker falls back
to OpenGL when it cannot.
"""

import math
import time

import numpy as np

#: Mitsuba variant: CUDA with OptiX ray tracing.
VARIANT = 'cuda_ad_rgb'
#: Rays per OptiX batch (host <-> device copies and Dr.Jit memory scale with it).
RAY_BATCH = 1 << 21
#: Texel-light pairs handled per batch of shadow rays (expanded by their ray counts).
PAIR_BATCH = 1 << 20
#: Run the per-texel ray loops as Dr.Jit symbolic loops on the GPU (one kernel per batch)
#: instead of expanding every ray on the CPU.
GPU_LOOPS = True
#: Texels per hemisphere kernel (each loops over all its sky rays on the GPU).
HEMI_TEXELS = 1 << 16
#: Lightmass constants, as in lightbaker.
LUMA = np.array([0.3, 0.59, 0.11])
SH_C0 = 0.282095
SH_C1 = 0.488603
SH_DIFFUSE_C1 = 0.325735

_mi = None


def available():
    """True when Mitsuba's CUDA/OptiX variant loads on this machine."""
    global _mi
    if _mi is not None:
        return True
    try:
        import mitsuba as mi
        mi.set_variant(VARIANT)
        import drjit as dr
        dr.eval(mi.Float(1.0))
        _mi = mi
        return True
    except Exception:                                # noqa: BLE001
        return False


class OptixScene:
    """The occluders as one OptiX scene: one shape group per distinct mesh, one instance
    per placement, plus the tables that turn a hit into what the baker needs (back face,
    two-sided, emitted radiance)."""

    def __init__(self, comps, meshes, log=print):
        import drjit as dr
        mi = _mi
        t0 = time.time()
        self.dr, self.mi = dr, mi
        scene = {'type': 'scene'}
        mesh_index, mesh_objs, cross, tri_base = {}, [], [], []
        base = 0
        for name in sorted({c['mesh'] for c in comps if c['occluder']}):
            m = meshes.get(name)
            if m is None or not len(m['triangles']):
                continue
            obj = mi.Mesh(name, len(m['positions']), len(m['triangles']))
            p = mi.traverse(obj)
            p['vertex_positions'] = mi.Float(np.ascontiguousarray(
                m['positions'].astype(np.float32).ravel()))
            # Mitsuba winds front faces counter-clockwise, UE clockwise
            p['faces'] = mi.UInt32(np.ascontiguousarray(
                m['triangles'][:, ::-1].astype(np.uint32).ravel()))
            p.update()
            k = len(mesh_objs)
            mesh_index[name] = k
            mesh_objs.append(obj)
            scene['g%d' % k] = {'type': 'shapegroup', 'shape': obj}
            v = m['positions'][m['triangles']].astype(np.float64)
            cross.append(np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0]))   # UE back side
            tri_base.append(base)
            base += len(v)
        w2l, flags, emit = [], [], []
        for c in comps:
            if not c['occluder'] or c['mesh'] not in mesh_index:
                continue
            for mat in c['matrices']:
                det = np.linalg.det(mat[:3, :3])
                if not np.isfinite(det) or abs(det) < 1e-12:
                    continue
                k = len(w2l)
                scene['i%d' % k] = {'type': 'instance',
                                    'shapegroup': {'type': 'ref',
                                                   'id': 'g%d' % mesh_index[c['mesh']]},
                                    'to_world': mi.ScalarTransform4f(mat.tolist())}
                w2l.append(np.linalg.inv(mat)[:3, :3])
                flags.append(1 if c.get('two_sided') else 0)
                emit.append(c.get('emitter') or (0.0, 0.0, 0.0))
        self.scene = mi.load_dict(scene)
        # registry ids of shapes and instances -> our tables
        ids = [int(dr.reinterpret_array(mi.UInt32, mi.ShapePtr(o))[0]) for o in mesh_objs]
        self.shape_mesh = np.full(max(ids) + 1, -1, np.int64)
        self.shape_mesh[ids] = np.arange(len(ids))
        inst_ids = {}
        for s in self.scene.shapes():
            key = s.id()
            if key.startswith('i') and key[1:].isdigit():
                inst_ids[int(key[1:])] = int(dr.reinterpret_array(mi.UInt32, mi.ShapePtr(s))[0])
        top = max(inst_ids.values()) + 1 if inst_ids else 1
        self.inst_row = np.full(top, -1, np.int64)
        for k, rid in inst_ids.items():
            self.inst_row[rid] = k
        self.cross = np.concatenate(cross)
        self.tri_base = np.array(tri_base, np.int64)
        self.w2l = np.array(w2l)
        self.two_sided = np.array(flags, bool)
        self.emit = np.array(emit, np.float64)
        self.emitters = int((self.emit.sum(1) > 0).sum())
        self.count = len(w2l)
        # the same tables on the GPU, for the symbolic (megakernel) loops
        u32 = lambda a: mi.UInt32(np.ascontiguousarray(a, np.uint32))   # noqa: E731
        f32 = lambda a: mi.Float(np.ascontiguousarray(np.ravel(a), np.float32))   # noqa: E731
        self.g_shape_mesh = u32(np.where(self.shape_mesh < 0, 0, self.shape_mesh))
        self.g_inst_row = u32(np.where(self.inst_row < 0, 0, self.inst_row))
        self.g_tri_base = u32(self.tri_base)
        self.g_cross = f32(self.cross)
        self.g_w2l = f32(self.w2l.reshape(-1, 9))
        self.g_two = f32(self.two_sided.astype(np.float32))
        self.g_emit = f32(self.emit)
        log('optix: %d instances of %d meshes, scene %.1fs'
            % (self.count, len(mesh_objs), time.time() - t0))

    def _ray(self, o, d, tmax):
        mi = self.mi
        O = mi.Point3f(*[mi.Float(np.ascontiguousarray(o[:, k], np.float32)) for k in range(3)])
        D = mi.Vector3f(*[mi.Float(np.ascontiguousarray(d[:, k], np.float32)) for k in range(3)])
        return mi.Ray3f(mi.Ray3f(O, D), mi.Float(np.ascontiguousarray(tmax, np.float32)))

    def test(self, o, d, tmax):
        """bool (n,): something lies along each ray before tmax."""
        out = np.zeros(len(o), bool)
        for a in range(0, len(o), RAY_BATCH):
            b = a + RAY_BATCH
            hit = self.scene.ray_test(self._ray(o[a:b], d[a:b], tmax[a:b]))
            out[a:b] = np.asarray(hit, bool)
        return out

    def closest(self, o, d, tmax):
        """(hit, t, back, emitted rgb) per ray; `back` is a back face of a one-sided mesh."""
        n = len(o)
        hit = np.zeros(n, bool)
        t = np.full(n, np.inf)
        back = np.zeros(n, bool)
        emitted = np.zeros((n, 3))
        dr, mi = self.dr, self.mi
        for a in range(0, n, RAY_BATCH):
            b = min(n, a + RAY_BATCH)
            pi = self.scene.ray_intersect_preliminary(self._ray(o[a:b], d[a:b], tmax[a:b]))
            tt = np.asarray(pi.t, np.float64)
            prim = np.asarray(pi.prim_index, np.int64)
            sid = np.asarray(dr.reinterpret_array(mi.UInt32, pi.shape), np.int64)
            iid = np.asarray(dr.reinterpret_array(mi.UInt32, pi.instance), np.int64)
            ok = np.isfinite(tt) & (sid > 0) & (sid < len(self.shape_mesh))
            ok &= (iid >= 0) & (iid < len(self.inst_row))
            idx = np.nonzero(ok)[0]
            mesh = self.shape_mesh[sid[idx]]
            row = self.inst_row[iid[idx]]
            good = (mesh >= 0) & (row >= 0)
            idx, mesh, row = idx[good], mesh[good], row[good]
            c = self.cross[self.tri_base[mesh] + prim[idx]]
            dl = np.einsum('nij,nj->ni', self.w2l[row], d[a + idx])
            bk = (dl * c).sum(1) < 0.0
            bk &= ~self.two_sided[row]
            hit[a + idx] = True
            t[a + idx] = tt[idx]
            back[a + idx] = bk
            emitted[a + idx] = np.where(bk[:, None], 0.0, self.emit[row])
        return hit, t, back, emitted


# ------------------------------------------------------------------ the kernel

def _frame(n):
    up = np.where(np.abs(n[:, 2:3]) < 0.999, [[0.0, 0.0, 1.0]], [[1.0, 0.0, 0.0]])
    t = np.cross(up, n)
    t /= np.linalg.norm(t, axis=1, keepdims=True)
    return t, np.cross(n, t)


def _norm(v):
    return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-20)


def _polygon_irradiance(p0, p1, p2, p3):
    l0, l1, l2, l3 = _norm(p0), _norm(p1), _norm(p2), _norm(p3)

    def w(a, b):
        c = (a * b).sum(1)
        return ((1.5708 - 0.175 * c) / np.sqrt(np.maximum(c + 1.0, 1e-12)))[:, None]
    w01, w12, w23, w30 = w(l0, l1), w(l1, l2), w(l2, l3), w(l3, l0)
    lv = np.cross(l1, -w01 * l0 + w12 * l2) + np.cross(l3, w30 * l0 - w23 * l2)
    return 0.5 * lv


def sky_radiance(d, sky, cube):
    """Radiance (n, 3) of the sky light along directions d, as lightbaker's shader."""
    colour = np.asarray(sky['colour']) * sky['brightness']
    out = np.empty((len(d), 3))
    if cube is None:
        out[:] = colour
    else:
        a = np.abs(d)
        face = np.zeros(len(d), np.int64)
        u = np.zeros(len(d))
        v = np.zeros(len(d))
        m = np.ones(len(d))
        zmax = (a[:, 2] > a[:, 1]) & (a[:, 2] > a[:, 0])
        ymax = ~zmax & (a[:, 1] > a[:, 0])
        xmax = ~zmax & ~ymax
        neg = d < 0
        s = zmax
        face[s] = np.where(neg[s, 2], 5, 4)
        u[s] = np.where(neg[s, 2], -d[s, 0], d[s, 0])
        v[s] = -d[s, 1]
        m[s] = a[s, 2]
        s = ymax
        face[s] = np.where(neg[s, 1], 3, 2)
        u[s] = d[s, 0]
        v[s] = np.where(neg[s, 1], -d[s, 2], d[s, 2])
        m[s] = a[s, 1]
        s = xmax
        face[s] = np.where(neg[s, 0], 1, 0)
        u[s] = np.where(neg[s, 0], d[s, 2], -d[s, 2])
        v[s] = -d[s, 1]
        m[s] = a[s, 0]
        size = cube.shape[1]
        fx = np.clip((u / m * 0.5 + 0.5) * size - 0.5, 0, size - 1)
        fy = np.clip((v / m * 0.5 + 0.5) * size - 0.5, 0, size - 1)
        x0, y0 = np.floor(fx).astype(np.int64), np.floor(fy).astype(np.int64)
        x1, y1 = np.minimum(x0 + 1, size - 1), np.minimum(y0 + 1, size - 1)
        tx, ty = (fx - x0)[:, None], (fy - y0)[:, None]
        out = ((cube[face, y0, x0] * (1 - tx) + cube[face, y0, x1] * tx) * (1 - ty)
               + (cube[face, y1, x0] * (1 - tx) + cube[face, y1, x1] * tx) * ty) * colour
    if sky['lower_black']:
        low = d[:, 2] < 0
        out[low] = np.asarray(sky['lower_colour']) * colour
    return out


class _Acc:
    """Per-texel accumulators of Lightmass's gathered sample."""

    def __init__(self, n):
        self.n = n
        self.sh = np.zeros((n, 4))
        self.inc = np.zeros((n, 4))

    def gather(self, texel, c, wdir, tz):
        keep = tz >= 0.0
        texel, c, wdir, tz = texel[keep], c[keep], wdir[keep], tz[keep]
        lum = c @ LUMA
        basis = np.stack([np.full(len(lum), SH_C0), -SH_C1 * wdir[:, 1], SH_C1 * wdir[:, 2],
                          -SH_C1 * wdir[:, 0]], 1)
        n = self.n
        for k in range(4):
            self.sh[:, k] += np.bincount(texel, lum * basis[:, k], n)
        self.inc[:, 3] += np.bincount(texel, lum * (SH_C0 * SH_C0 + SH_DIFFUSE_C1 * SH_C1 * tz), n)
        for k in range(3):
            self.inc[:, k] += np.bincount(texel, c[:, k] * tz, n)


def _hash_rng(seed, n):
    return np.random.default_rng(seed + 7919 * n)


def trace_texels(tracer, pos, nrm, geo, lights, sky, cube, settings, use_lights=True,
                 use_sky=True, seed=1):
    """(sh (n, 4), inc (n, 4), extra (n, 12)) for texels, as lightbaker's TRACE_CS gives
    them. `lights` is lightbaker's (L, 5, 4) light array; `settings` a dict of the
    shader's uniforms (sky_rays, backface_rays, shadow_rays, penumbra_angle,
    shadow_exponent, ray_offset, normal_offset_scale, min_normal_offset, sky_ray_length)."""
    n = len(pos)
    rng = _hash_rng(seed, n)
    P = pos[:, :3].astype(np.float64)
    N = _norm(nrm[:, :3].astype(np.float64))
    G = _norm(geo[:, :3].astype(np.float64))
    radius = np.maximum(pos[:, 3], 0.1)
    noff = np.maximum(settings['min_normal_offset'], settings['normal_offset_scale'] * radius)
    hit, t, _b, _e = tracer.closest(P + G * 0.01, G, noff)
    noff = np.where(hit, 0.5 * t, noff)
    O = P + G * noff[:, None]
    acc = _Acc(n)
    extra = np.zeros((n, 12))
    ray_offset = settings['ray_offset']

    # -- lights: texel-light pairs within reach
    if use_lights and len(lights):
        lp = lights[:, 0, :3].astype(np.float64)
        lr = lights[:, 0, 3].astype(np.float64)
        lo, hi = P.min(0), P.max(0)
        near = np.clip(lp, lo, hi)
        cand = np.nonzero(((near - lp) ** 2).sum(1) < lr ** 2)[0]
        pairs_t, pairs_l = [], []
        for j in cand:
            d2 = ((P - lp[j]) ** 2).sum(1)
            ti = np.nonzero(d2 < lr[j] ** 2)[0]
            pairs_t.append(ti)
            pairs_l.append(np.full(len(ti), j))
        if pairs_t:
            pt = np.concatenate(pairs_t)
            pl = np.concatenate(pairs_l)
            for a in range(0, len(pt), PAIR_BATCH):
                _light_pairs(tracer, acc, extra, pt[a:a + PAIR_BATCH], pl[a:a + PAIR_BATCH],
                             lights, P, N, O, settings, rng)
    extra[:, 4:7] = acc.inc[:, :3]

    # -- the sky and the emitters: uniform hemisphere rays about the triangle normal
    rays = settings['sky_rays']
    if ((use_sky and sky) or tracer.emitters) and GPU_LOOPS:
        rot, rot2 = rng.random(n), rng.random(n)
        for a in range(0, n, HEMI_TEXELS):
            b = min(n, a + HEMI_TEXELS)
            tu_ = None
            s_, i_, lava, backs, seen = gpu_hemisphere(
                tracer, O[a:b], G[a:b], N[a:b], rot[a:b], rot2[a:b], settings, sky, cube,
                use_sky)
            acc.sh[a:b] += s_
            acc.inc[a:b] += i_
            extra[a:b, 8:11] = lava
            extra[a:b, 0] = backs / max(1, min(settings['backface_rays'], rays))
            extra[a:b, 1] = seen / rays
    elif (use_sky and sky) or tracer.emitters:
        tu, tv = _frame(G)
        w = 2.0 * math.pi / rays
        rot = rng.random(n)
        rot2 = rng.random(n)
        k = np.arange(rays)
        rad_inv = np.array([_radical_inverse(int(x)) for x in k])
        backs = np.zeros(n)
        seen = np.zeros(n)
        lava = np.zeros((n, 3))
        per = max(1, (RAY_BATCH // rays))
        for a in range(0, n, per):
            b = min(n, a + per)
            m = b - a
            e1 = np.mod((k[None, :] + 0.5) / rays + rot[a:b, None], 1.0)
            e2 = np.mod(rad_inv[None, :] + rot2[a:b, None], 1.0)
            z = e1.ravel()
            r = np.sqrt(np.maximum(0.0, 1.0 - z * z))
            ph = 2.0 * math.pi * e2.ravel()
            tex = np.repeat(np.arange(a, b), rays)
            d = (tu[tex] * (r * np.cos(ph))[:, None] + tv[tex] * (r * np.sin(ph))[:, None]
                 + G[tex] * z[:, None])
            ro = O[tex] + d * ray_offset
            hit, _t, back, em = tracer.closest(ro, d, np.full(len(d), settings['sky_ray_length']))
            first = np.tile(k < settings['backface_rays'], m)
            backs[a:b] += np.bincount(tex - a, (hit & back & first).astype(float), m)
            tz = (d * N[tex]).sum(1)
            emits = hit & (em.sum(1) > 0)
            if emits.any():
                acc.gather(tex[emits], em[emits] * w, d[emits], tz[emits])
                pos_tz = emits & (tz > 0)
                for c in range(3):
                    lava[a:b, c] += np.bincount(tex[pos_tz] - a, em[pos_tz, c] * w * tz[pos_tz], m)
            open_ = ~hit
            seen[a:b] += np.bincount(tex[open_] - a, np.ones(open_.sum()), m)
            if use_sky and sky and open_.any():
                acc.gather(tex[open_], sky_radiance(d[open_], sky, cube) * w, d[open_], tz[open_])
        extra[:, 0] = backs / max(1, min(settings['backface_rays'], rays))
        extra[:, 1] = seen / rays
        extra[:, 8:11] = lava
    return acc.sh.astype(np.float32), acc.inc.astype(np.float32), extra.astype(np.float32)


def _radical_inverse(b):
    b = ((b << 16) | (b >> 16)) & 0xFFFFFFFF
    b = (((b & 0x55555555) << 1) | ((b & 0xAAAAAAAA) >> 1)) & 0xFFFFFFFF
    b = (((b & 0x33333333) << 2) | ((b & 0xCCCCCCCC) >> 2)) & 0xFFFFFFFF
    b = (((b & 0x0F0F0F0F) << 4) | ((b & 0xF0F0F0F0) >> 4)) & 0xFFFFFFFF
    b = (((b & 0x00FF00FF) << 8) | ((b & 0xFF00FF00) >> 8)) & 0xFFFFFFFF
    return b * 2.3283064365386963e-10


def _light_pairs(tracer, acc, extra, pt, pl, lights, P, N, O, settings, rng):
    """Direct light of light pl[i] at texel pt[i], with area shadows."""
    L = lights[pl].astype(np.float64)
    lp, R = L[:, 0, :3], L[:, 0, 3]
    colour, kind = L[:, 1, :3], L[:, 1, 3].astype(np.int64)
    ldir, falloff = L[:, 2, :3], L[:, 2, 3]
    tangent, src_r = L[:, 3, :3], L[:, 3, 3]
    src_l, cos_outer, cos_inner = L[:, 4, 0], L[:, 4, 1], L[:, 4, 2]
    flags = L[:, 4, 3].astype(np.int64)
    inv_sq, shadows = (flags & 1) != 0, (flags & 2) != 0
    p, nn = P[pt], N[pt]
    to_l = lp - p
    d2 = (to_l * to_l).sum(1)
    r2 = R * R
    mask = np.maximum(0.0, 1.0 - (d2 / r2) ** 2) ** 2
    att = np.where(inv_sq, mask / (d2 + 1.0),
                   np.maximum(1.0 - d2 / r2, 0.0) ** falloff)
    wl = (lp + nn * src_r[:, None]) - p
    spot = kind == 1
    if spot.any():
        sp = np.clip(((_norm(-to_l) * ldir).sum(1) - cos_outer)
                     / np.maximum(cos_inner - cos_outer, 1e-4), 0.0, 1.0)
        att = np.where(spot, att * sp * sp, att)
    rect = kind == 2
    ax = np.zeros_like(lp)
    ay = np.zeros_like(lp)
    if rect.any():
        ay_r = tangent[rect]
        az_r = -ldir[rect]
        ax_r = np.cross(ay_r, az_r)
        tl = to_l[rect]
        sr, sl = src_r[rect, None], src_l[rect, None]
        irr = _polygon_irradiance(tl - ax_r * sr - ay_r * sl, tl + ax_r * sr - ay_r * sl,
                                  tl + ax_r * sr + ay_r * sl, tl - ax_r * sr + ay_r * sl)
        a_r = np.linalg.norm(irr, axis=1)
        a_r = np.where(np.isfinite(a_r), a_r, 0.0) / (2.0 * src_r[rect] * src_l[rect])
        a_r *= mask[rect]
        a_r = np.where((tl * az_r).sum(1) < 0.0, 0.0, a_r)
        att[rect] = a_r
        wl[rect] = irr
        ax[rect], ay[rect] = ax_r, ay_r
    wlen = np.linalg.norm(wl, axis=1)
    ok = (att > 0) & (wlen > 0)
    wl = wl / np.maximum(wlen, 1e-20)[:, None]
    tz = (wl * nn).sum(1)
    ok &= tz >= 0
    idx = np.nonzero(ok)[0]
    vis = np.ones(len(pt))
    sh_idx = idx[shadows[idx]]
    if len(sh_idx):
        angular = np.maximum(src_r[sh_idx], src_l[sh_idx]) / np.maximum(np.sqrt(d2[sh_idx]), 1.0)
        nrays = np.clip(np.ceil(settings['shadow_rays'] * angular / settings['penumbra_angle']),
                        1, settings['shadow_rays']).astype(np.int64)
        if GPU_LOOPS:
            share = gpu_shadow(tracer, O[pt[sh_idx]], lp[sh_idx], to_l[sh_idx], kind[sh_idx],
                               src_r[sh_idx], src_l[sh_idx], ax[sh_idx], ay[sh_idx], nrays,
                               settings, int(rng.integers(1 << 30)))
            vis[sh_idx] = share ** settings['shadow_exponent']
            nrays = None
    if len(sh_idx) and nrays is not None:
        owner = np.repeat(np.arange(len(sh_idx)), nrays)
        k = np.arange(len(owner)) - np.repeat(np.cumsum(nrays) - nrays, nrays)
        pi = sh_idx[owner]
        e1 = (k + rng.random(len(owner))) / nrays[owner]
        e2 = rng.random(len(owner))
        u, v = _frame(_norm(to_l[pi]))
        rr = src_r[pi] * np.sqrt(e1)
        ph = 2.0 * math.pi * e2
        target = lp[pi] + (u * np.cos(ph)[:, None] + v * np.sin(ph)[:, None]) * rr[:, None]
        rsel = kind[pi] == 2
        if rsel.any():
            target[rsel] = (lp[pi][rsel] + ax[pi][rsel] * (src_r[pi][rsel] * (e1[rsel] * 2 - 1))[:, None]
                            + ay[pi][rsel] * (src_l[pi][rsel] * (e2[rsel] * 2 - 1))[:, None])
        o = O[pt[pi]]
        dd = target - o
        ln = np.linalg.norm(dd, axis=1)
        dn = dd / np.maximum(ln, 1e-20)[:, None]
        blocked = tracer.test(o + dn * settings['ray_offset'], dn,
                              np.maximum(ln - settings['ray_offset'] - 0.5, 0.0))
        free = np.bincount(owner, (~blocked).astype(float), len(sh_idx))
        vis[sh_idx] = (free / nrays) ** settings['shadow_exponent']
    good = idx[vis[idx] > 0]
    acc.gather(pt[good], colour[good] * (att[good] * vis[good])[:, None], wl[good], tz[good])


# ------------------------------------------------------- symbolic GPU loops (Dr.Jit)

def _frac(x):
    import drjit as dr
    return x - dr.floor(x)


def _vec(mi, a):
    return mi.Vector3f(*[mi.Float(np.ascontiguousarray(a[:, k], np.float32)) for k in range(3)])


def _cube_lookup(mi, dr, d, cube_g, size):
    """Bilinear radiance from the 6 x size x size x 3 cube (flat Float) along d."""
    ax, ay, az = dr.abs(d.x), dr.abs(d.y), dr.abs(d.z)
    zmax = (az > ay) & (az > ax)
    ymax = ~zmax & (ay > ax)
    face = dr.select(zmax, dr.select(d.z < 0, 5, 4),
                     dr.select(ymax, dr.select(d.y < 0, 3, 2), dr.select(d.x < 0, 1, 0)))
    u = dr.select(zmax, dr.select(d.z < 0, -d.x, d.x),
                  dr.select(ymax, d.x, dr.select(d.x < 0, d.z, -d.z)))
    v = dr.select(zmax, -d.y, dr.select(ymax, dr.select(d.y < 0, -d.z, d.z), -d.y))
    m = dr.select(zmax, az, dr.select(ymax, ay, ax))
    fx = dr.clip((u / m * 0.5 + 0.5) * size - 0.5, 0.0, size - 1.0)
    fy = dr.clip((v / m * 0.5 + 0.5) * size - 0.5, 0.0, size - 1.0)
    x0, y0 = mi.UInt32(dr.floor(fx)), mi.UInt32(dr.floor(fy))
    x1, y1 = dr.minimum(x0 + 1, size - 1), dr.minimum(y0 + 1, size - 1)
    tx, ty = fx - mi.Float(x0), fy - mi.Float(y0)
    f = mi.UInt32(face)

    def at(x, y):
        return dr.gather(mi.Vector3f, cube_g, (f * size + y) * size + x)
    return ((at(x0, y0) * (1 - tx) + at(x1, y0) * tx) * (1 - ty)
            + (at(x0, y1) * (1 - tx) + at(x1, y1) * tx) * ty)


def gpu_hemisphere(tracer, O, G, N, rot, rot2, settings, sky, cube, use_sky):
    """The sky and emitter gather of TRACE_CS as one OptiX megakernel per batch.
    Returns numpy (sh (n,4), inc (n,4), lava (n,3), backs (n,), seen (n,))."""
    mi, dr = tracer.mi, tracer.dr
    rays = int(settings['sky_rays'])
    w = 2.0 * math.pi / rays
    radinv = mi.Float(np.array([_radical_inverse(k) for k in range(rays)], np.float32))
    sky_on = bool(use_sky and sky)
    colour = mi.Vector3f(*[float(x) for x in np.asarray(sky['colour']) * sky['brightness']]) if sky_on else None
    lower = (mi.Vector3f(*[float(x) for x in sky['lower_colour']]) * colour
             if sky_on and sky['lower_black'] else None)
    cube_g = mi.Float(np.ascontiguousarray(cube.ravel(), np.float32)) if (
        sky_on and cube is not None) else None
    size = cube.shape[1] if cube is not None else 1
    o, g, nn = _vec(mi, O), _vec(mi, G), _vec(mi, N)
    up = dr.select(dr.abs(g.z) < 0.999, mi.Vector3f(0, 0, 1), mi.Vector3f(1, 0, 0))
    tu = dr.normalize(dr.cross(up, g))
    tv = dr.cross(g, tu)
    r1 = mi.Float(np.ascontiguousarray(rot, np.float32))
    r2 = mi.Float(np.ascontiguousarray(rot2, np.float32))
    n = len(O)
    z0 = dr.zeros(mi.Float, n)
    luma = mi.Vector3f(*[float(x) for x in LUMA])

    def cond(k, *_):
        return k < rays

    def body(k, s0, s1, s2, s3, i0, i1, i2, i3, l0, l1, l2, backs, seen):
        kf = mi.Float(k)
        e1 = _frac((kf + 0.5) / rays + r1)
        e2 = _frac(dr.gather(mi.Float, radinv, k) + r2)
        rr = dr.safe_sqrt(1.0 - e1 * e1)
        ph = 2.0 * math.pi * e2
        d = tu * (rr * dr.cos(ph)) + tv * (rr * dr.sin(ph)) + g * e1
        ray = mi.Ray3f(mi.Ray3f(o + d * settings['ray_offset'], d),
                       mi.Float(settings['sky_ray_length']))
        pi = tracer.scene.ray_intersect_preliminary(ray)
        hit = pi.is_valid()
        sid = dr.reinterpret_array(mi.UInt32, pi.shape)
        iid = dr.reinterpret_array(mi.UInt32, pi.instance)
        mesh = dr.gather(mi.UInt32, tracer.g_shape_mesh, sid, hit)
        row = dr.gather(mi.UInt32, tracer.g_inst_row, iid, hit)
        tri = dr.gather(mi.UInt32, tracer.g_tri_base, mesh, hit) + pi.prim_index
        c = dr.gather(mi.Vector3f, tracer.g_cross, tri, hit)
        w0 = dr.gather(mi.Vector3f, tracer.g_w2l, row * 3, hit)
        w1 = dr.gather(mi.Vector3f, tracer.g_w2l, row * 3 + 1, hit)
        w2 = dr.gather(mi.Vector3f, tracer.g_w2l, row * 3 + 2, hit)
        dl = mi.Vector3f(dr.dot(w0, d), dr.dot(w1, d), dr.dot(w2, d))
        two = dr.gather(mi.Float, tracer.g_two, row, hit) > 0.5
        back = hit & (dr.dot(dl, c) < 0.0) & ~two
        e = dr.gather(mi.Vector3f, tracer.g_emit, row, hit & ~back)
        emitting = hit & ~back & ((e.x + e.y + e.z) > 0.0)
        tz = dr.dot(d, nn)
        add = dr.select(emitting, e * w, mi.Vector3f(0.0))
        if sky_on:
            rad = colour if cube_g is None else _cube_lookup(mi, dr, d, cube_g, size) * colour
            if lower is not None:
                rad = dr.select(d.z < 0.0, lower, rad)
            add = dr.select(~hit, rad * w, add)
        ok = tz >= 0.0
        lum = dr.select(ok, dr.dot(add, luma), 0.0)
        s0 += lum * SH_C0
        s1 += lum * (-SH_C1 * d.y)
        s2 += lum * (SH_C1 * d.z)
        s3 += lum * (-SH_C1 * d.x)
        i3 += lum * (SH_C0 * SH_C0 + SH_DIFFUSE_C1 * SH_C1 * tz)
        inc = dr.select(ok, add * tz, mi.Vector3f(0.0))
        i0 += inc.x
        i1 += inc.y
        i2 += inc.z
        lv = dr.select(emitting & (tz > 0.0), e * (w * tz), mi.Vector3f(0.0))
        l0 += lv.x
        l1 += lv.y
        l2 += lv.z
        backs += dr.select(back & (k < settings['backface_rays']), 1.0, 0.0)
        seen += dr.select(~hit, 1.0, 0.0)
        return (k + 1, s0, s1, s2, s3, i0, i1, i2, i3, l0, l1, l2, backs, seen)

    state = (mi.UInt32(0),) + tuple(dr.zeros(mi.Float, n) for _ in range(13))
    out = dr.while_loop(state, cond, body)
    dr.eval(out)
    arr = [np.asarray(v, np.float64) for v in out[1:]]
    del z0
    return (np.stack(arr[0:4], 1), np.stack(arr[4:8], 1), np.stack(arr[8:11], 1),
            arr[11], arr[12])


def gpu_shadow(tracer, O, lp, to_l, kind, src_r, src_l, ax, ay, nrays, settings, seed):
    """Unshadowed share of nrays[i] rays from O[i] to points on light i's surface."""
    mi, dr = tracer.mi, tracer.dr
    n = len(O)
    o = _vec(mi, O)
    lpv = _vec(mi, lp)
    fwd = _vec(mi, to_l / np.maximum(np.linalg.norm(to_l, axis=1, keepdims=True), 1e-20))
    up = dr.select(dr.abs(fwd.z) < 0.999, mi.Vector3f(0, 0, 1), mi.Vector3f(1, 0, 0))
    u = dr.normalize(dr.cross(up, fwd))
    v = dr.cross(fwd, u)
    axv, ayv = _vec(mi, ax), _vec(mi, ay)
    sr = mi.Float(np.ascontiguousarray(src_r, np.float32))
    sl = mi.Float(np.ascontiguousarray(src_l, np.float32))
    rect = mi.Bool(np.ascontiguousarray(kind == 2))
    cnt = mi.UInt32(np.ascontiguousarray(nrays, np.uint32))
    rng = mi.PCG32(size=n, initstate=mi.UInt64(seed) + dr.arange(mi.UInt64, n))
    off = settings['ray_offset']

    def cond(k, free, rng):
        return k < cnt

    def body(k, free, rng):
        e1 = (mi.Float(k) + rng.next_float32()) / mi.Float(cnt)
        e2 = rng.next_float32()
        rr = sr * dr.sqrt(e1)
        ph = 2.0 * math.pi * e2
        t_disc = lpv + (u * dr.cos(ph) + v * dr.sin(ph)) * rr
        t_rect = lpv + axv * (sr * (e1 * 2.0 - 1.0)) + ayv * (sl * (e2 * 2.0 - 1.0))
        target = dr.select(rect, t_rect, t_disc)
        dd = target - o
        ln = dr.norm(dd)
        dn = dd / dr.maximum(ln, 1e-20)
        ray = mi.Ray3f(mi.Ray3f(o + dn * off, dn), dr.maximum(ln - off - 0.5, 0.0))
        blocked = tracer.scene.ray_test(ray)
        free += dr.select(blocked, 0.0, 1.0)
        return k + 1, free, rng

    k, free, rng = dr.while_loop((mi.UInt32(0), dr.zeros(mi.Float, n), rng), cond, body)
    return np.asarray(free, np.float64) / np.maximum(nrays, 1)
