# Generated from OMD2-Converter/thirdparty/survey-tools/inventory.py by thirdparty/fabconvert/port_riftlord.py; edit the source.
"""Inventory one vault pack (read-only): python3 -I inventory.py <pack_dir> <out.json>"""
import sys, os, json, glob, collections, struct, re, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from riftlord.fab.survey import pkgread
from riftlord.fab.survey import meshgeo
from riftlord.fab.survey.pkgread import find, walk

LANDSCAPE_EXPR = {'MaterialExpressionLandscapeLayerBlend', 'MaterialExpressionLandscapeLayerWeight',
                  'MaterialExpressionLandscapeLayerSample', 'MaterialExpressionLandscapeLayerSwitch',
                  'MaterialExpressionLandscapeGrassOutput', 'MaterialExpressionLandscapeLayerCoords',
                  'MaterialExpressionLandscapeVisibilityMask', 'MaterialExpressionLandscapePhysicalMaterialOutput'}
LIGHT_ACTORS = {'PointLight', 'SpotLight', 'DirectionalLight', 'SkyLight', 'RectLight'}


def stem(f):
    return os.path.splitext(os.path.basename(f))[0]


def main_export(p, f):
    s = stem(f)
    for e in p.exports:
        if e['outer'] == 0 and e['name'] == s:
            return e
    for e in p.exports:
        if e['outer'] == 0 and p.class_of(e) not in ('MetaData', 'ObjectRedirector', 'Package'):
            return e
    return None


def safe_props(p, e):
    try:
        return p.props(e)
    except Exception:
        return [], None


def ref(p, idx):
    if not isinstance(idx, int) or idx == 0:
        return None
    try:
        if idx < 0:
            pk = p.import_package(idx)
            return '%s.%s' % (pk, p.obj_name(idx)) if pk else p.obj_name(idx)
        return 'self:' + p.obj_name(idx)
    except Exception:
        return None


def child(t, name):
    for c in (t or {}).get('children', []) or []:
        if c['name'] == name:
            return c
    return None


def static_mesh(p, e, f, info):
    props, end = safe_props(p, e)
    sm = find(props, 'StaticMaterials')
    mats = []
    if sm and sm.get('items'):
        for it in sm['items']:
            mi = find(it, 'MaterialInterface')
            sl = find(it, 'MaterialSlotName') or find(it, 'ImportedMaterialSlotName')
            mats.append(dict(material=ref(p, mi.get('value')) if mi else None,
                             slot=sl.get('value') if sl else None))
    info['materials'] = mats
    srcm = find(props, 'SourceModels')
    info['lods'] = srcm.get('count') if srcm else None
    eb = find(props, 'ExtendedBounds')
    if eb:
        o = child(eb, 'Origin'); x = child(eb, 'BoxExtent')
        if o and x and o.get('value') and x.get('value'):
            info['ext_bounds'] = dict(origin=[round(v, 2) for v in o['value']], extent=[round(v, 2) for v in x['value']])
    nan = find(props, 'NaniteSettings')
    if nan:
        en = child(nan, 'bEnabled')
        info['nanite'] = bool(en and en.get('value'))
    else:
        info['nanite'] = False
    for k in ('LightMapCoordinateIndex', 'LightMapResolution'):
        t = find(props, k)
        if t:
            info[k] = t.get('value')
    # collision
    col = dict(convex=0, box=0, sphyl=0, sphere=0, tapered=0, trace_flag='CTF_UseDefault', bodysetup=False)
    for b in p.exports:
        if p.class_of(b) == 'BodySetup':
            col['bodysetup'] = True
            bp, _ = safe_props(p, b)
            ag = find(bp, 'AggGeom')
            for c in (ag or {}).get('children', []) or []:
                key = {'ConvexElems': 'convex', 'BoxElems': 'box', 'SphylElems': 'sphyl',
                       'SphereElems': 'sphere', 'TaperedCapsuleElems': 'tapered'}.get(c['name'])
                if key:
                    col[key] += c.get('count') or 0
            tf = find(bp, 'CollisionTraceFlag')
            if tf:
                col['trace_flag'] = tf.get('value')
            break
    col['simple_shapes'] = col['convex'] + col['box'] + col['sphyl'] + col['sphere'] + col['tapered']
    info['collision'] = col
    # import data: UCX evidence
    for b in p.exports:
        if p.class_of(b) == 'FbxStaticMeshImportData':
            bp, _ = safe_props(p, b)
            t = find(bp, 'bAutoGenerateCollision')
            info['fbx_auto_collision'] = t.get('value') if t else None
            t = find(bp, 'ImportUniformScale')
            if t:
                info['fbx_scale'] = t.get('value')
    try:
        g = meshgeo.read_static_mesh(f, p)
        info['geo'] = meshgeo.summary(g)
    except Exception as ex:
        info['geo_error'] = repr(ex)[:200]


def texture(p, e, info):
    props, _ = safe_props(p, e)
    src = find(props, 'Source')
    for k in ('SizeX', 'SizeY', 'NumMips', 'NumSlices', 'Format', 'CompressionFormat', 'bPNGCompressed'):
        c = child(src, k)
        if c:
            info[k] = c.get('value')
    for k in ('CompressionSettings', 'LODGroup', 'SRGB', 'MipGenSettings'):
        t = find(props, k)
        if t:
            info[k] = t.get('value')


def material(p, e, info):
    props, _ = safe_props(p, e)
    exprs = collections.Counter()
    funcs = collections.Counter()
    for x in p.exports:
        c = p.class_of(x)
        if c.startswith('MaterialExpression'):
            exprs[c] += 1
            if c in ('MaterialExpressionMaterialFunctionCall', 'MaterialExpressionMaterialAttributeLayers'):
                xp, _ = safe_props(p, x)
                mf = find(xp, 'MaterialFunction')
                if mf:
                    funcs[ref(p, mf.get('value')) or '?'] += 1
    info['expressions'] = dict(exprs)
    info['n_expressions'] = sum(exprs.values())
    info['functions'] = dict(funcs)
    allprops = []
    for x in p.exports:
        if p.class_of(x) in ('Material', 'MaterialEditorOnlyData'):
            pr, _ = safe_props(p, x)
            allprops += pr
    names = {t['name'] for t in allprops}
    info['inputs'] = sorted(n for n in names if n in (
        'BaseColor', 'Metallic', 'Specular', 'Roughness', 'Normal', 'EmissiveColor', 'Opacity', 'OpacityMask',
        'WorldPositionOffset', 'WorldDisplacement', 'TessellationMultiplier', 'SubsurfaceColor', 'AmbientOcclusion',
        'Refraction', 'PixelDepthOffset', 'MaterialAttributes', 'CustomizedUVs', 'Anisotropy', 'Tangent'))
    for k in ('BlendMode', 'ShadingModel', 'TwoSided', 'MaterialDomain', 'bUsedWithInstancedStaticMeshes',
              'bUsedWithFoliage', 'bUseMaterialAttributes', 'bUsedWithLandscape', 'D3D11TessellationMode'):
        t = find(allprops, k)
        if t:
            info[k] = t.get('value')
    ex = set(exprs)
    fl = ' '.join(funcs).lower()
    tags = []
    if ex & LANDSCAPE_EXPR:
        tags.append('landscape_layers')
    if 'MaterialExpressionWorldPosition' in ex or 'worldaligned' in fl or 'world_aligned' in fl or 'triplanar' in fl:
        tags.append('world_aligned')
    if 'MaterialExpressionVertexColor' in ex:
        tags.append('vertex_colour')
    if 'WorldPositionOffset' in names or 'wind' in fl or 'MaterialExpressionTime' in ex or 'pivotpainter' in fl:
        tags.append('wpo_or_time')
    if 'MaterialExpressionSceneTexture' in ex or info.get('MaterialDomain') == 'MD_PostProcess':
        tags.append('postprocess')
    if 'MaterialExpressionCustom' in ex:
        tags.append('custom_hlsl')
    if 'MaterialExpressionPixelDepthOffset' in ex or 'PixelDepthOffset' in names:
        tags.append('pdo')
    if ex & {'MaterialExpressionDepthFade', 'MaterialExpressionSceneDepth', 'MaterialExpressionPixelDepth'}:
        tags.append('depth')
    if ex & {'MaterialExpressionPanner', 'MaterialExpressionRotator'}:
        tags.append('animated_uv')
    if 'MaterialExpressionFresnel' in ex:
        tags.append('fresnel')
    if any(c.startswith('MaterialExpressionNoise') or c == 'MaterialExpressionNoise' for c in ex):
        tags.append('noise')
    if ex & {'MaterialExpressionRuntimeVirtualTextureSample', 'MaterialExpressionRuntimeVirtualTextureOutput',
             'MaterialExpressionRuntimeVirtualTextureSampleParameter'}:
        tags.append('rvt')
    if ex & {'MaterialExpressionStaticSwitchParameter', 'MaterialExpressionStaticBoolParameter'}:
        tags.append('static_switches')
    nt = sum(v for k, v in exprs.items() if 'TextureSample' in k or 'TextureObject' in k)
    info['n_texture_samples'] = nt
    info['tags'] = tags


def material_instance(p, e, info):
    props, _ = safe_props(p, e)
    par = find(props, 'Parent')
    info['parent'] = ref(p, par.get('value')) if par else None
    tps = []
    t = find(props, 'TextureParameterValues')
    for it in (t or {}).get('items', []) or []:
        pi = find(it, 'ParameterInfo'); nm = child(pi, 'Name') if pi else find(it, 'ParameterName')
        pv = find(it, 'ParameterValue')
        tps.append(dict(name=nm.get('value') if nm else None, texture=ref(p, pv.get('value')) if pv else None))
    info['texture_params'] = tps
    for k in ('ScalarParameterValues', 'VectorParameterValues'):
        t = find(props, k)
        info[k] = t.get('count') if t else 0
    sp = find(props, 'StaticParameters')
    nsw = 0
    if sp:
        for c in sp.get('children', []) or []:
            if c.get('count'):
                nsw += c['count']
    info['static_params'] = nsw
    bp = find(props, 'BasePropertyOverrides')
    if bp:
        info['overrides'] = [c['name'] for c in bp.get('children', []) or []]


def blueprint(p, e, info):
    props, _ = safe_props(p, e)
    pc = find(props, 'ParentClass')
    info['parent_class'] = ref(p, pc.get('value')) if pc else None
    graphs = {}
    for x in p.exports:
        if p.class_of(x) == 'EdGraph':
            graphs[x['index']] = x['name']
    nodes = collections.Counter(); per_graph = collections.Counter()
    for x in p.exports:
        c = p.class_of(x)
        if c.startswith('K2Node') or c.startswith('AnimGraphNode'):
            nodes[c] += 1
            if c not in ('K2Node_Event', 'K2Node_FunctionEntry', 'K2Node_FunctionResult'):
                per_graph[graphs.get(x['outer'], '?')] += 1
    info['nodes'] = dict(nodes)
    info['logic_nodes'] = sum(per_graph.values())
    info['logic_by_graph'] = dict(per_graph)
    comps = collections.Counter()
    meshes = collections.Counter()
    for x in p.exports:
        c = p.class_of(x)
        if x['name'].endswith('_GEN_VARIABLE'):
            comps[c] += 1
            if c in ('StaticMeshComponent', 'InstancedStaticMeshComponent', 'HierarchicalInstancedStaticMeshComponent'):
                xp, _ = safe_props(p, x)
                sm = find(xp, 'StaticMesh')
                if sm:
                    meshes[ref(p, sm.get('value')) or '?'] += 1
    info['components'] = dict(comps)
    info['component_meshes'] = dict(meshes)


def isc_instances(p, x, end):
    """Instance count of an (H)ISM component: PerInstanceSMData bulk array (element size 64)."""
    if end is None:
        return None
    d = p.d; lim = x['offset'] + x['size']
    o = end
    while o + 8 <= lim:
        es, n = struct.unpack_from('<ii', d, o)
        if es == 64 and 0 < n and o + 8 + 64 * n <= lim:
            m = struct.unpack_from('<16f', d, o + 8)
            if abs(m[15] - 1.0) < 1e-4 and abs(m[3]) < 1e-4 and abs(m[7]) < 1e-4 and abs(m[11]) < 1e-4:
                return n
        if es == 128 and 0 < n and o + 8 + 128 * n <= lim:     # UE5 double matrices
            m = struct.unpack_from('<16d', d, o + 8)
            if abs(m[15] - 1.0) < 1e-6 and abs(m[3]) < 1e-6:
                return n
        o += 1
        if o > end + 4096:
            break
    return 0


def world(p, info):
    cls = collections.Counter(p.class_of(x) for x in p.exports)
    level = [x for x in p.exports if p.class_of(x) == 'Level']
    lvl = level[0]['index'] if level else None
    actors = collections.Counter(); bp = collections.Counter()
    for x in p.exports:
        if x['outer'] == lvl and lvl:
            c = p.class_of(x)
            actors[c] += 1
            if x['cls'] < 0 and p.imports[-x['cls'] - 1]['class_name'] == 'BlueprintGeneratedClass':
                bp[c] += 1
    info['actors'] = dict(actors.most_common())
    info['bp_instances'] = dict(bp.most_common())
    info['n_actors'] = sum(actors.values())
    info['export_classes'] = dict(cls.most_common(60))
    meshes = collections.Counter(); scales = []
    inst = collections.Counter(); foliage_comp = 0; foliage_inst = 0
    for x in p.exports:
        c = p.class_of(x)
        if c == 'StaticMeshComponent':
            par = p.exports[x['outer'] - 1] if x['outer'] > 0 else None
            if par and p.class_of(par) == 'StaticMeshActor':
                xp, _ = safe_props(p, x)
                sm = find(xp, 'StaticMesh')
                meshes[ref(p, sm.get('value')) if sm else None] += 1
                sc = find(xp, 'RelativeScale3D')
                if sc and sc.get('value'):
                    scales.append([round(v, 3) for v in sc['value']])
                else:
                    scales.append([1.0, 1.0, 1.0])
        elif c in ('FoliageInstancedStaticMeshComponent', 'HierarchicalInstancedStaticMeshComponent',
                   'InstancedStaticMeshComponent'):
            xp, end = safe_props(p, x)
            sm = find(xp, 'StaticMesh')
            n = isc_instances(p, x, end)
            inst[(c, ref(p, sm.get('value')) if sm else None)] += n or 0
            if c == 'FoliageInstancedStaticMeshComponent':
                foliage_comp += 1; foliage_inst += n or 0
    info['sm_actor_meshes'] = {str(k): v for k, v in meshes.most_common()}
    info['n_sm_actors'] = sum(meshes.values())
    nonunit = [s for s in scales if any(abs(v - 1) > 0.01 for v in s)]
    info['sm_actor_nonunit_scale'] = len(nonunit)
    info['sm_actor_nonuniform_scale'] = sum(1 for s in scales if max(s) - min(s) > 0.01)
    info['instanced'] = [dict(component=k[0], mesh=k[1], instances=v) for k, v in inst.most_common()]
    info['foliage_components'] = foliage_comp
    info['foliage_instances'] = foliage_inst
    # landscape
    land = {}
    for x in p.exports:
        c = p.class_of(x)
        if c in ('Landscape', 'LandscapeProxy', 'LandscapeStreamingProxy'):
            xp, _ = safe_props(p, x)
            d = {}
            for k in ('ComponentSizeQuads', 'SubsectionSizeQuads', 'NumSubsections', 'LandscapeMaterial'):
                t = find(xp, k)
                if t:
                    d[k] = ref(p, t['value']) if k == 'LandscapeMaterial' else t.get('value')
            land.setdefault(c, []).append(d)
    ncomp = cls.get('LandscapeComponent', 0)
    if land or ncomp:
        comps = [x for x in p.exports if p.class_of(x) == 'LandscapeComponent']
        xs, ys, csq = [], [], 0
        for x in comps:
            xp, _ = safe_props(p, x)
            sx = find(xp, 'SectionBaseX'); sy = find(xp, 'SectionBaseY'); cq = find(xp, 'ComponentSizeQuads')
            xs.append(sx['value'] if sx else 0); ys.append(sy['value'] if sy else 0)
            if cq:
                csq = cq['value']
        roots = []
        for x in p.exports:
            if p.class_of(x) in ('Landscape', 'LandscapeProxy', 'LandscapeStreamingProxy'):
                xp, _ = safe_props(p, x)
                rc = find(xp, 'RootComponent')
                if rc and isinstance(rc.get('value'), int) and rc['value'] > 0:
                    rp, _ = safe_props(p, p.exports[rc['value'] - 1])
                    sc = find(rp, 'RelativeScale3D'); lo = find(rp, 'RelativeLocation')
                    roots.append(dict(scale=list(sc['value']) if sc else [1, 1, 1],
                                      location=list(lo['value']) if lo else [0, 0, 0]))
        qx = (max(xs) - min(xs) + csq) if xs else None
        qy = (max(ys) - min(ys) + csq) if ys else None
        sc = roots[0]['scale'] if roots else [1, 1, 1]
        info['landscape'] = dict(actors=land, roots=roots, components=ncomp, quads_x=qx, quads_y=qy,
                                 world_size_x=qx * sc[0] if qx else None, world_size_y=qy * sc[1] if qy else None,
                                 textures_in_map=sum(1 for x in p.exports if p.class_of(x) == 'Texture2D'))
    info['lights'] = {k: v for k, v in actors.items() if k in LIGHT_ACTORS}
    info['world_partition'] = 'WorldPartition' in cls


def deps(p, root):
    out = collections.Counter()
    for im in p.imports:
        if im['outer'] == 0 and im['class_name'] == 'Package':
            out[im['name']] += 1
    return list(out)


def run(pack_dir, out):
    t0 = time.time()
    content = os.path.join(pack_dir, 'data', 'Content')
    roots = [d for d in os.listdir(content) if os.path.isdir(os.path.join(content, d))]
    files = sorted(glob.glob(os.path.join(content, '**', '*.uasset'), recursive=True) +
                   glob.glob(os.path.join(content, '**', '*.umap'), recursive=True))
    recs = []
    for f in files:
        rel = os.path.relpath(f, content)
        rec = dict(path=rel, bytes=os.path.getsize(f))
        try:
            p = pkgread.Package(f)
        except Exception as ex:
            rec['error'] = repr(ex); recs.append(rec); continue
        rec['saved_by'] = p.saved_by.split('+')[0]
        e = main_export(p, f)
        c = p.class_of(e) if e else None
        if f.endswith('.umap'):
            c = 'World'
        rec['class'] = c
        rec['deps'] = deps(p, roots)
        try:
            if c == 'StaticMesh':
                static_mesh(p, e, f, rec)
            elif c in ('Texture2D', 'TextureCube', 'VirtualTexture2D'):
                texture(p, e, rec)
            elif c in ('Material',):
                material(p, e, rec)
            elif c == 'MaterialFunction' or c == 'MaterialFunctionMaterialLayer' or c == 'MaterialFunctionMaterialLayerBlend':
                material(p, e, rec)
            elif c in ('MaterialInstanceConstant',):
                material_instance(p, e, rec)
            elif c in ('Blueprint', 'AnimBlueprint', 'WidgetBlueprint'):
                blueprint(p, e, rec)
            elif c == 'World':
                world(p, rec)
            elif c and c.startswith('FoliageType'):
                pr, _ = safe_props(p, e)
                m = find(pr, 'Mesh')
                rec['mesh'] = ref(p, m.get('value')) if m else None
            elif c == 'ParticleSystem':
                rec['emitters'] = sum(1 for x in p.exports if p.class_of(x).startswith('ParticleSpriteEmitter') or p.class_of(x) == 'ParticleEmitter')
                rec['modules'] = dict(collections.Counter(p.class_of(x) for x in p.exports if p.class_of(x).startswith('ParticleModule')))
            elif c in ('NiagaraSystem', 'NiagaraEmitter'):
                rec['n_exports'] = len(p.exports)
        except Exception as ex:
            rec['detail_error'] = repr(ex)[:200]
        recs.append(rec)
    # external actors (UE5 one-file-per-actor) are ordinary packages under __ExternalActors__
    json.dump(dict(pack_dir=pack_dir, roots=roots, n_files=len(files), seconds=round(time.time() - t0, 1),
                   files=recs), open(out, 'w'), indent=0)


if __name__ == '__main__':
    run(sys.argv[1], sys.argv[2])
