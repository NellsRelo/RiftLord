# Generated from OMD2-Converter/thirdparty/fabconvert/matconvert.py by thirdparty/fabconvert/port_riftlord.py; edit the source.
"""Turn one pack material (MIC chain or Material) into an OMD3 material plan and baked textures.

    plan = analyse(ctx, '/Game/Medieval_Dock/Material/Furniture/MI_Furniture_01', vc=None)
    images = bake(ctx, plan)

analyse() resolves the instance chain, probes the graph (matgraph) to find which coordinate
each texture sample reads, chooses the bake frame and the OMD3 route, and grades it:

  exact        the baked texel equals the pack's material at that texel (up to compression
               and resize), on a shipped master the conversion rules have proven
  close        same inputs, a small, named difference (planar instead of triplanar projection,
               binary alpha cut, two-sided by duplicated faces, a mesh-paint blend shape)
  approximate  a visible feature is lost or frozen (view-dependent terms, motion, a texture read
               as its mean, a shading model OMD3's masters lack)
  fallback     not convertible: the section is dropped or drawn with a neutral material

Every reason is kept in plan['reasons'].
"""

import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from riftlord.fab import matgraph as mg                       # noqa: E402
from riftlord.fab.matgraph import Params, Unsupported   # noqa: E402
from riftlord.fab import packgraph as pg                      # noqa: E402

GRADES = ('exact', 'close', 'approximate', 'fallback')
#: Probe points (in base units): three for the affine fit, two to check it is affine.
PROBE_BASE = np.array([(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (0.37, 0.71), (0.81, 0.23)], np.float32)
#: World-planar probe points are PROBE_BASE x this many uu.
WUV_PROBE_UU = 100.0
#: UV channels offered to every graph (the packs carry up to 8).
MAX_UV = 8
#: Affine-fit residual allowed on the check points (in texture repeats).
AFFINE_TOL = 1e-3
#: A sample is periodic in the frame when k x A x F^-1 is integral within this.
FRAME_INT_TOL = 2e-3
#: Largest number of frame-tile repeats tried to make every sample periodic.
MAX_FRAME_REPEAT = 8
#: Bake sizes: default, large surfaces, constant materials, smallest.
SIZE_DEFAULT = 1024
SIZE_LARGE = 2048
SIZE_CONST = 256
SIZE_MIN = 256
#: Alpha (DXT5) textures: no shipped 2048 DXT5 donor, so alpha bakes stop here.
SIZE_ALPHA_MAX = 1024
#: A texel density below source/this is reported.
DENSITY_NOTE = 2.0
#: A frame may cost at most this density factor at SIZE_LARGE before longer-period samples are
#: read as their mean instead (rounds of large world-aligned masks).
DENSITY_MAX = 8.0
#: Pixels per bake chunk (memory bound: ~40 live (P,4) float32 arrays).
CHUNK = 1 << 18
#: Vertex-colour channel counted as read when perturbing it moves a pin by more than this.
VC_SENSITIVITY = 1e-4
#: Emissive below this (linear) is no emissive.
EMISSIVE_EPS = 1e-3
#: Pack AO is multiplied into BC (user decision 2026-10-14: Master_Prop never samples MRA.B).
BAKE_AO = True
#: Shading models OMD3's masters render as DefaultLit.
LIT_MODELS = ('MSM_DefaultLit', None)
#: Blend modes treated as see-through effects.
TRANSLUCENT = ('BLEND_Translucent', 'BLEND_Additive', 'BLEND_Modulate', 'BLEND_AlphaComposite',
               'BLEND_AlphaHoldout')
#: Name fragments (lower case) of a translucent material that is water.
WATER_WORDS = ('water', 'ocean', 'river', 'lake', 'sea')
#: Note fragment -> the grade it costs. Unlisted notes cost 'close'.
NOTE_GRADES = (
    ('Fresnel', 'approximate'), ('Noise', 'approximate'), ('view-dependent', 'approximate'),
    ('frozen at time 0', 'approximate'), ('as its mean', 'approximate'), ('not periodic', 'approximate'),
    ('vertex animation', 'approximate'), ('WorldAlignedBlend', 'approximate'),
    ('PrecomputedAOMask', 'approximate'), ('DepthFade', 'approximate'), ('Transform', 'approximate'),
    ('PixelDepth', 'approximate'), ('SceneDepth', 'approximate'), ('ParticleColor', 'approximate'),
    ('DynamicParameter', 'approximate'), ('not in the pack', 'approximate'), ('ObjectRadius', 'approximate'),
    ('shading model', 'approximate'), ('evaluated at the mean', 'approximate'), ('substitute', 'approximate'),
    ('unlit', 'approximate'), ('not affine', 'approximate'), ('emissive dropped', 'approximate'),
    ('DitherTemporalAA', 'close'), ('world-aligned', 'close'), ('world position', 'close'),
    ('top-only', 'close'), ('not uniform', 'close'), ('texel density', 'close'), ('binary', 'close'),
    ('duplicated faces', 'close'), ('mesh paint', 'close'),
)


def grade_of(reasons, floor='exact'):
    g = GRADES.index(floor)
    for r in reasons:
        cost = 'close'
        for frag, gr in NOTE_GRADES:
            if frag in r:
                cost = gr
                break
        g = max(g, GRADES.index(cost))
    return GRADES[g]


class Context:
    """Per pack: the packgraph Pack, texture cache, and per-material caches."""

    def __init__(self, pack_root):
        self.pack = pg.Pack(pack_root)
        from riftlord.fab import vaulttex
        self.tex = vaulttex.Textures(self.pack)
        self.resolved = {}


# -- resolving a slot material -------------------------------------------------------------------------

def resolve(ctx, gpath):
    """{'master', 'graph', 'params', 'header', 'chain'} of a slot material, or None."""
    gpath = (gpath or '').split('.')[0]
    if gpath in ctx.resolved:
        return ctx.resolved[gpath]
    pack = ctx.pack
    chain, recs = [], []
    cur = gpath
    out = None
    for _ in range(16):
        f = pack.file_of(cur)
        if not f:
            break
        p = pack.package(f)
        if p is None:
            break
        from riftlord.fab.survey.inventory import main_export
        e = main_export(p, f)
        cls = p.class_of(e) if e else None
        chain.append(cur)
        if cls in ('MaterialInstanceConstant', 'MaterialInstanceDynamic'):
            rec = pg.mic_record(pack, p, e, cur)
            recs.append(rec)
            cur = (rec.get('parent') or '').split('.')[0]
            continue
        if cls == 'Material':
            g = pack.graph(cur)
            if g is None:
                break
            hdr = pg.header(g, 0)
            params = Params()
            for p_ in pg.parameters(g, pack):
                pass
            # instances from the master outwards: the nearest override wins
            for rec in reversed(recs):
                params.scalars.update({k: v for k, v in rec['scalars'].items() if v is not None})
                params.vectors.update({k: tuple(v) for k, v in rec['vectors'].items() if v})
                params.textures.update({k: v['path'] for k, v in rec['textures'].items() if v.get('path')})
                for s in rec['static_switches']:
                    if s.get('bOverride', True) and 'Value' in s:
                        params.switches[s['name']] = bool(s['Value'])
                for s in rec['static_masks']:
                    if s.get('bOverride', True) and 'R' in s:
                        params.masks[s['name']] = tuple(bool(s.get(k)) for k in 'RGBA')
                for k, v in rec['base_overrides'].items():
                    hdr[k] = v
            out = {'path': gpath, 'master': cur, 'graph': g, 'params': params, 'header': hdr, 'chain': chain}
        break
    ctx.resolved[gpath] = out
    return out


# -- probing -------------------------------------------------------------------------------------------

def probe_domain(vc):
    P = len(PROBE_BASE)
    return {'uv': [PROBE_BASE.copy() for _ in range(MAX_UV)], 'wuv': PROBE_BASE * WUV_PROBE_UU,
            'vc': np.tile(np.asarray(vc, np.float32), (P, 1)), 'normal': np.tile(np.float32([0, 0, 1]), (P, 1))}


def _pins_values(ev):
    out = ev.pins()
    return {k: v.a for k, v in out.items()}


def vc_channels(ctx, src):
    """Vertex-colour channels that change any pin (perturbation of each channel)."""
    base = np.ones(4, np.float32)
    ev = mg.Evaluator(ctx.pack, src['graph'], src['params'], probe_domain(base), ctx.tex)
    ref = _pins_values(ev)
    if not any('vc' in v.deps for v in ev.pins().values()):
        return []
    read = []
    for i in range(4):
        for val in (0.0, 0.5):
            vc = base.copy()
            vc[i] = val
            ev2 = mg.Evaluator(ctx.pack, src['graph'], src['params'], probe_domain(vc), ctx.tex)
            got = _pins_values(ev2)
            if any(np.abs(got[k] - ref[k]).max() > VC_SENSITIVITY for k in ref if k in got):
                read.append('rgba'[i])
                break
    return read


def _base_of(deps):
    b = sorted(d for d in deps if d.startswith('uv') or d == 'wuv')
    if not b:
        return 'none'
    return b[0] if len(b) == 1 else 'mixed'


def frame(ctx, src, vc):
    """Probe the graph and choose the bake frame. Returns (frame dict | None, overrides, notes,
    sample records)."""
    dom = probe_domain(vc)
    ev = mg.Evaluator(ctx.pack, src['graph'], src['params'], dom, ctx.tex, record=True)
    ev.pins()
    notes = set(ev.notes)
    samples = []
    seen = set()
    for s in ev.samples:
        if s['key'] in seen:
            continue
        seen.add(s['key'])
        base = _base_of(s['deps'])
        rec = dict(s, base=base, A=None, t=None, affine=True)
        if base not in ('none', 'mixed'):
            X = dom['wuv'] if base == 'wuv' else dom['uv'][int(base[2:])]
            Xh = np.concatenate([X, np.ones((len(X), 1), np.float32)], 1).astype(np.float64)
            sol, *_ = np.linalg.lstsq(Xh[:3], s['coord'][:3].astype(np.float64), rcond=None)
            pred = Xh @ sol
            if np.abs(pred - s['coord']).max() > AFFINE_TOL:
                rec['affine'] = False
            rec['A'] = sol[:2].T          # coord = A @ base + t
            rec['t'] = sol[2]
        size = ctx.tex.size(s['texture']) if s['texture'] else None
        rec['size'] = size
        samples.append(rec)
    overrides = {}
    based = [s for s in samples if s['base'] not in ('none',)]
    if not based:
        return None, overrides, notes, samples
    score = {}
    for s in based:
        if s['base'] == 'mixed':
            continue
        w = (s['size'][0] * s['size'][1]) if s['size'] else 1
        score[s['base']] = score.get(s['base'], 0) + w
    if not score:
        for s in based:
            overrides[s['key']] = _mean(ctx, s)
            notes.add('texture %s read through mixed coordinates: sampled as its mean' % _short(s['texture']))
        return None, overrides, notes, samples
    # try every candidate frame and keep the one that preserves the most texture detail: a frame
    # chosen by raw pixel count can leave all its own textures long-period (read as means) while
    # averaging away the other frame's colour mask (Sky Dungeon MI_column_01_Inst: concrete grunge
    # in world space, the blue / gold mask on uv0)
    best = None
    for primary in sorted(score, key=lambda k: (-score[k], k != 'uv0', k)):
        got = _try_frame(ctx, based, primary)
        if got is None:
            continue
        if best is None or got[3] > best[3]:
            best = got
    if best is None:
        return None, overrides, notes, samples
    fr, over, fnotes, _kept = best
    overrides.update(over)
    notes |= fnotes
    return fr, overrides, notes, samples


def _try_frame(ctx, based, primary):
    """Bake frame `primary`: (frame, overrides, notes, texture pixels kept in the frame) or None."""
    overrides, notes = {}, set()
    on = [s for s in based if s['base'] == primary]
    for s in based:
        if s['base'] != primary:
            overrides[s['key']] = _mean(ctx, s)
            notes.add('texture %s on %s (frame is %s): sampled as its mean' % (_short(s['texture']), s['base'], primary))
        elif not s['affine']:
            notes.add('texture %s coordinates not affine in %s' % (_short(s['texture']), primary))
    cand = [s for s in on if s['A'] is not None and abs(np.linalg.det(s['A'])) > 1e-12]
    if not cand:
        return None
    # candidate frames: each distinct sample tile, largest tile first. A candidate is taken
    # when the texel density it leaves is acceptable; samples whose tile is larger than the
    # chosen frame are then read as their mean (their slow variation is lost).
    cand.sort(key=lambda s: (abs(np.linalg.det(s['A'])), str(s['key'])))
    tried = []
    for F in cand:
        if any(abs(abs(np.linalg.det(F['A'])) - d) <= 1e-9 * max(d, 1e-12) for d in tried):
            continue
        tried.append(abs(np.linalg.det(F['A'])))
        dF = abs(np.linalg.det(F['A']))
        inside = [s for s in on if s['A'] is None or abs(np.linalg.det(s['A'])) >= dF * (1 - 1e-6)]
        outside = [s for s in on if s not in inside]
        fr, fnotes = _fit_frame(F, inside, primary)
        loss = (fr['needed_px'] or 0) / SIZE_LARGE
        if loss <= DENSITY_MAX or F is cand[-1]:
            break
    for s in outside:
        overrides[s['key']] = _mean(ctx, s)
        fnotes.add('long-period texture %s (tile %.0f x the frame) sampled as its mean'
                   % (_short(s['texture']), math.sqrt(dF / max(abs(np.linalg.det(s['A'])), 1e-12))))
    kept = sum((s['size'][0] * s['size'][1]) if s['size'] else 1 for s in inside)
    return fr, overrides, notes | fnotes, kept


def _fit_frame(F, on, primary):
    """The frame tile of reference sample F that keeps every sample in `on` periodic (k
    repeats of F's tile, smallest k), its texel need, and notes."""
    notes = set()
    Finv = np.linalg.inv(F['A'])

    def periodic(M):
        return np.abs(M - np.round(M)).max() <= FRAME_INT_TOL * max(1.0, np.abs(M).max())
    best = None
    for k in range(1, MAX_FRAME_REPEAT + 1):
        if all(s['A'] is None or periodic(k * (s['A'] @ Finv)) for s in on):
            best = k
            break
    if best is None:
        best = 1
        for s in on:
            if s['A'] is not None and not periodic(s['A'] @ Finv):
                notes.add('texture %s not periodic in the bake tile (ratio %s): seams'
                          % (_short(s['texture']), np.round(np.diag(s['A'] @ Finv), 3).tolist()))
    G = F['A'] / best
    g = F['t'] / best
    reps = []
    for s in on:
        if s['A'] is None or not s['size']:
            continue
        M = s['A'] @ np.linalg.inv(G)
        rep = np.linalg.norm(M, axis=0)                      # texture repeats per frame axis
        reps.append(max(rep[0] * s['size'][0], rep[1] * s['size'][1]))
    fr = {'base': primary, 'G': G.tolist(), 'g': np.asarray(g).tolist(), 'repeat': best,
          'needed_px': float(max(reps)) if reps else None}
    return fr, notes


def _short(p):
    return (p or '?').rsplit('/', 1)[-1].split('.')[0]


def _mean(ctx, s):
    m = ctx.tex.mean(s['texture'], s['sampler']) if s['texture'] else None
    return np.full(4, mg.MISSING_TEXTURE, np.float32) if m is None else m.astype(np.float32)


# -- analysis --------------------------------------------------------------------------------------------

def analyse(ctx, gpath, vc=None, large=False, foliage_master='alphamask'):
    """The conversion plan of one slot material, evaluated at a constant vertex colour `vc`
    (RGBA 0..1; None = white). Raises nothing: failures come back as a fallback plan."""
    from riftlord.fab import fbxpack
    if fbxpack.is_material(gpath):
        return fbxpack.analyse(fbxpack.sets(), gpath, large)
    plan = {'source': (gpath or '').split('.')[0] or None, 'vc': None if vc is None else [round(float(x), 4) for x in vc],
            'reasons': [], 'route': None, 'grade': None, 'two_sided_mesh': False, 'drop': False}
    src = resolve(ctx, gpath) if gpath else None
    if src is None:
        plan.update(route='fallback', grade='fallback',
                    reasons=['material %s is not in the pack (engine default or missing): neutral grey'
                             % (gpath or 'None')])
        return plan
    hdr = src['header']
    plan['master'] = src['master']
    plan['header'] = {k: hdr.get(k) for k in ('BlendMode', 'TwoSided', 'ShadingModel', 'MaterialDomain',
                                              'OpacityMaskClipValue')}
    domain = hdr.get('MaterialDomain') or 'MD_Surface'
    blend = hdr.get('BlendMode') or 'BLEND_Opaque'
    name = src['master'].lower() + ' ' + plan['source'].lower()
    if domain != 'MD_Surface':
        plan.update(route='drop', grade='fallback', drop=True,
                    reasons=['%s material: section dropped (OMD3 equivalents are level dressing)' % domain])
        return plan
    if blend in TRANSLUCENT:
        if any(w in name for w in WATER_WORDS):
            plan.update(route='water', grade='approximate',
                        reasons=['translucent water: OMD3 water material substitute (shipped MIC)'])
        else:
            plan.update(route='drop', grade='fallback', drop=True,
                        reasons=['translucent effect material (%s): section dropped' % blend])
        return plan
    vcv = np.ones(4, np.float32) if vc is None else np.asarray(vc, np.float32)
    try:
        plan['vc_read'] = vc_channels(ctx, src)
        fr, overrides, notes, samples = frame(ctx, src, vcv)
    except Unsupported as e:
        plan.update(route='fallback', grade='fallback', reasons=['not evaluable: %s' % e])
        return plan
    except (ValueError, IndexError, KeyError, np.linalg.LinAlgError) as e:   # a graph shape we read wrong
        plan.update(route='fallback', grade='fallback', reasons=['evaluation failed: %s: %s' % (type(e).__name__, e)])
        return plan
    reasons = sorted(notes)
    masked = blend == 'BLEND_Masked'
    two_sided = bool(hdr.get('TwoSided'))
    shading = hdr.get('ShadingModel')
    if shading == 'MSM_Unlit':
        reasons.append('unlit material drawn lit (emissive kept)')
    elif shading not in LIT_MODELS:
        reasons.append('shading model %s drawn as DefaultLit' % shading)
    plan['frame'] = fr
    plan['overrides'] = {repr(k): v.tolist() for k, v in overrides.items()}
    plan['_overrides'] = overrides
    plan['samples'] = [{'texture': s['texture'], 'base': s['base'], 'size': s['size'],
                        'A': None if s['A'] is None else np.round(s['A'], 6).tolist()} for s in samples]
    # size
    if fr is None:
        size = SIZE_CONST
    else:
        budget = SIZE_LARGE if large or fr['repeat'] > 1 or fr['base'] == 'wuv' else SIZE_DEFAULT
        need = fr['needed_px'] or SIZE_MIN
        size = SIZE_MIN
        while size < need and size < budget:
            size *= 2
        if need > size * DENSITY_NOTE:
            reasons.append('texel density reduced to 1/%.1f of the source (%d px for %d)' % (need / size, size, need))
    if masked:
        size = min(size, SIZE_ALPHA_MAX)
    plan['size'] = int(size)
    # route
    if masked:
        plan['route'] = 'foliage' if (foliage_master == 'foliage' and two_sided) else 'alphamask'
        plan['clip'] = float(hdr.get('OpacityMaskClipValue') or 0.3333)
        if plan['route'] == 'alphamask':
            reasons.append('opacity mask cut binary at the pack clip value %.3g (Master_Trap_AlphaMask)' % plan['clip'])
            reasons.append('rain only on this master: no snow, sand, moss or ice')
            if two_sided:
                plan['two_sided_mesh'] = True
                reasons.append('two-sided by duplicated faces')
        else:
            reasons.append('opacity mask cut binary in SRK.B (Master_Foliage, clip 0.333 compiled in)')
            reasons.append('rain only on this master: no snow, sand, moss or ice')
    else:
        plan['route'] = 'prop2s' if two_sided else 'prop'
    plan['two_sided'] = two_sided
    plan['masked'] = masked
    plan['reasons'] = reasons
    plan['grade'] = grade_of(reasons)
    plan['_src'] = src
    return plan


# -- baking ----------------------------------------------------------------------------------------------

def bake(ctx, plan, vc=None):
    """{pin: (N, N, C) float32} over the plan's frame tile (rows = v, columns = u)."""
    if plan.get('_fbx'):
        from riftlord.fab import fbxpack
        return fbxpack.bake(plan)
    src = plan['_src']
    N = plan['size']
    fr = plan['frame']
    vcv = np.ones(4, np.float32) if vc is None else np.asarray(vc, np.float32)
    out = {}
    rows_per = max(1, CHUNK // N)
    for r0 in range(0, N, rows_per):
        r1 = min(N, r0 + rows_per)
        jj, ii = np.meshgrid(np.arange(N, dtype=np.float32), np.arange(r0, r1, dtype=np.float32))
        f = np.stack([(jj.ravel() + 0.5) / N, (ii.ravel() + 0.5) / N], 1)
        P = len(f)
        if fr is not None:
            Ginv = np.linalg.inv(np.asarray(fr['G']))
            base = ((f - np.asarray(fr['g'], np.float32)) @ Ginv.T).astype(np.float32)
        else:
            base = f
        zero = np.zeros_like(base)
        primary = fr['base'] if fr else 'uv0'
        dom = {'uv': [base if (primary.startswith('uv') or fr is None) else zero for _ in range(MAX_UV)],
               'wuv': base if primary == 'wuv' else zero,
               'vc': np.tile(vcv, (P, 1)), 'normal': np.tile(np.float32([0, 0, 1]), (P, 1))}
        ev = mg.Evaluator(ctx.pack, src['graph'], src['params'], dom, ctx.tex, sample_override=plan['_overrides'])
        pins = ev.pins()
        for k, v in pins.items():
            out.setdefault(k, []).append(v.a.reshape(r1 - r0, N, -1))
    return {k: np.concatenate(v, 0) for k, v in out.items()}


def images(plan, baked):
    """The textures a route needs, as RGBA uint8 arrays: BC (sRGB; alpha = cut for masked),
    MRA (metal, rough, AO), NM (tangent normal, rotated into the new UV frame), EM (sRGB,
    normalised; plan['emissive_scale'] set), SRK (foliage)."""
    N = plan['size']
    one = np.ones((N, N, 1), np.float32)

    def pin(k, default):
        v = baked.get(k)
        if v is None:
            return np.broadcast_to(np.asarray(default, np.float32), (N, N, len(default))).copy()
        return v
    bc = np.clip(pin('BaseColor', (0, 0, 0))[..., :3], 0, 1)
    ao = np.clip(pin('AmbientOcclusion', (1,))[..., :1], 0, 1)
    metal = np.clip(pin('Metallic', (0,))[..., :1], 0, 1)
    rough = np.clip(pin('Roughness', (0.5,))[..., :1], 0, 1)
    if plan['header'].get('ShadingModel') == 'MSM_Unlit':
        bc = np.zeros_like(bc)
    bc_ao = bc * ao if BAKE_AO else bc
    srgb = mg.linear_to_srgb(bc_ao)
    out = {}
    alpha = one
    if plan.get('masked'):
        op = pin('OpacityMask', (1,))[..., :1]
        alpha = (op >= plan['clip']).astype(np.float32)
    out['BC'] = _u8(np.concatenate([srgb, alpha], -1))
    out['BC_noAO'] = _u8(np.concatenate([mg.linear_to_srgb(bc), alpha], -1))
    out['MRA'] = _u8(np.concatenate([metal, rough, ao, one], -1))
    n = pin('Normal', (0, 0, 1))[..., :3].astype(np.float64)
    if plan.get('frame'):
        G = np.asarray(plan['frame']['G'], np.float64)
        U, _S, Vt = np.linalg.svd(G)
        R = U @ Vt                                           # orthogonal part of G
        xy = n[..., :2] @ R.T
        n = np.concatenate([xy, n[..., 2:3]], -1)
    n = n / np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-9)
    out['NM'] = _u8(np.concatenate([n * 0.5 + 0.5, one], -1).astype(np.float32))
    em = np.maximum(pin('EmissiveColor', (0, 0, 0))[..., :3], 0)
    peak = float(em.max()) if em.size else 0.0
    if peak > EMISSIVE_EPS:
        plan['emissive_scale'] = peak
        out['EM'] = _u8(np.concatenate([mg.linear_to_srgb(em / peak), one], -1))
    else:
        plan['emissive_scale'] = 0.0
    if plan.get('route') == 'foliage':
        srk = np.concatenate([one * 0.5, rough, alpha, one], -1)
        out['SRK'] = _u8(srk)
    return out


def _u8(a):
    return np.clip(np.rint(np.asarray(a, np.float64) * 255.0), 0, 255).astype(np.uint8)


def is_constant(img):
    a = img.reshape(-1, img.shape[-1])
    return bool((a.max(0) - a.min(0)).max() <= 1)
