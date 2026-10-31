# Generated from OMD2-Converter/thirdparty/survey-tools/report.py by thirdparty/fabconvert/port_riftlord.py; edit the source.
"""Summarise a raw inventory into the shipped per-pack JSON and print a text digest.
python3 -I report.py <raw.json> <out.json> <vault_title> <vault_version>"""
import sys, os, json, re, collections, statistics

CELL = 300.0          # OMD3 trap cell, uu
SNAP_TOL = 2.0        # uu: an extent counts as on a grid step when within this of a multiple

ROLE_RULES = [
    ('sky_fx', r'(^sm_sky|cloud|god_?ray|decal|^sm_plane$|fog|^sm_wind|bird|fish|sea_?gull|water|plasma|lightbeam|skyline|backg)'),
    ('foliage', r'(tree|bush|grass|plant|flower|fern|ivy|vine|bamboo|leaf|foliage|oats|wheat|pine(?!_?cone)|spruce|shrub|branch|stump|mushroom|sapling|damdelion|dandelion|climbing|moss|reed|clover|hedge)'),
    ('cliff_rock', r'(cliff|rock|boulder|^sm_stone_?\d|^sm_stone_[a-e]$|^sm_stone\d|scatter)'),
    ('terrain', r'(terrain|landscape|ground|island|snowdrift|drift|sand|hill|mountain|flying_broken_block|^sm_block_broken)'),
    ('stairs', r'(stair|steps?(_|$))'),
    ('roof', r'(roof|dome|chimney)'),
    ('door_window_arch', r'(door|window|arch|gate|portcullis|entrance|entrace|shutter|exit)'),
    ('floor', r'(floor|tile|platform|platfrom|plank|deck|pier|walkway|pavement|span|bridge_floor|balcon|balkony|curb)'),
    ('wall', r'(wall|fence|facade|railing|parapet|corner)'),
    ('column', r'(column|pillar|post(?!er)|pole|beam|support)'),
    ('structure', r'(building|block|part|tower|foundation|module|cube|house|fortress|pyramid|rotunda|pediment|bridge|ruins|arena|shrine|land_?mark|stall|tent|border|molding|ornament|decor|trim|strip|frame|cap(_|$))'),
]
ARCH = ('floor', 'wall', 'stairs', 'structure', 'roof', 'column', 'door_window_arch')


#: A rule word must start a word of the name: "street" is no tree, "spier" (spear) no pier
#: (user, Docks showcase 2026-10-15: SM_wood_street_roof_01 was foliage and took no grids).
WORD_START = r'(?<![a-z])'


def name_words(stem):
    """The name lowercased with its camelCase words split by '_' (PineTree -> pine_tree)."""
    return re.sub(r'(?<=[a-z])(?=[A-Z])', '_', stem).lower()


#: Roles that name a building part. A foliage word does not win over one of these in the same
#: name: moss-covered architecture is architecture (Stylized Dungeon SM_Moss_Floor_3x3,
#: SM_Moss_Wall_2x3 were foliage and took no grids, 2026-10-23).
OVERGROWN_ARCH = ('stairs', 'roof', 'door_window_arch', 'floor', 'wall', 'column')


def role(path):
    n = name_words(os.path.splitext(os.path.basename(path))[0])
    folder = path.lower()
    hits = [r for r, rx in ROLE_RULES if re.search(WORD_START + rx, n)]
    if hits and hits[0] == 'foliage':
        arch = [r for r in hits if r in OVERGROWN_ARCH]
        if arch:
            return arch[0]
    if hits:
        return hits[0]
    for r, key in (('foliage', '/trees'), ('foliage', '/plants'), ('foliage', 'foliage'), ('cliff_rock', '/cliff'),
                   ('cliff_rock', '/rock'), ('cliff_rock', '/stones'), ('structure', '/building'), ('structure', 'house_modules'),
                   ('structure', '/modular')):
        if key in folder:
            return r
    return 'prop'


def on_step(v, step):
    return abs(v - round(v / step) * step) <= SNAP_TOL and v >= step - SNAP_TOL


def main(raw, out, title, version):
    J = json.load(open(raw))
    R = J['files']
    roots = J['roots']
    by = collections.defaultdict(list)
    for r in R:
        by[r.get('class')].append(r)
    S = dict(title=title, vault_engine=version, roots=roots, n_files=len(R))
    S['saved_by'] = dict(collections.Counter(r.get('saved_by') for r in R))
    S['classes'] = {k: dict(count=len(v), bytes=sum(x['bytes'] for x in v)) for k, v in sorted(by.items(), key=lambda kv: -len(kv[1]))}
    S['total_bytes'] = sum(r['bytes'] for r in R)
    # ---------------- static meshes
    sm = by.get('StaticMesh', [])
    geo_ok = [r for r in sm if 'geo' in r]
    roles = collections.Counter()
    role_of = {}
    dims_by_role = collections.defaultdict(list)
    for r in geo_ok:
        g = r['geo']
        d = [g['bmax'][i] - g['bmin'][i] for i in range(3)]
        r['dims'] = [round(x, 1) for x in d]
        ro = role(r['path'])
        r['role'] = ro
        roles[ro] += 1
        dims_by_role[ro].append(d)
    tris = [r['geo']['tris'] for r in geo_ok]
    S['static_mesh'] = dict(
        count=len(sm), geometry_read=len(geo_ok),
        geometry_formats=dict(collections.Counter(r['geo']['fmt'] for r in geo_ok)),
        geometry_failures=[(r['path'], r.get('geo_error')) for r in sm if 'geo' not in r],
        tris_total=sum(tris), tris_median=int(statistics.median(tris)) if tris else 0, tris_max=max(tris) if tris else 0,
        tris_over_50k=sum(1 for t in tris if t > 50000),
        nanite_enabled=sum(1 for r in sm if r.get('nanite')),
        multi_lod=sum(1 for r in sm if (r.get('lods') or 0) > 1),
        uv_channels=dict(collections.Counter(r['geo']['uv_channels'] for r in geo_ok)),
        sections=dict(collections.Counter(len(r['geo']['sections']) for r in geo_ok)),
        roles=dict(roles.most_common()),
    )
    # bounds distribution
    md = [max(r['dims']) for r in geo_ok]
    bins = [(0, 50), (50, 150), (150, 300), (300, 600), (600, 1200), (1200, 3000), (3000, 1e9)]
    S['static_mesh']['max_dim_hist'] = {'%d-%s' % (a, ('inf' if b > 1e8 else int(b))): sum(1 for x in md if a <= x < b) for a, b in bins}
    # pivots
    piv = collections.Counter()
    for r in geo_ok:
        g = r['geo']
        cx = (g['bmin'][0] + g['bmax'][0]) / 2; cy = (g['bmin'][1] + g['bmax'][1]) / 2
        z0 = abs(g['bmin'][2]) < 1.0
        if abs(cx) < 1 and abs(cy) < 1:
            piv['centre_xy' + ('_bottom' if z0 else '')] += 1
        elif min(abs(g['bmin'][0]), abs(g['bmax'][0])) < 1 and min(abs(g['bmin'][1]), abs(g['bmax'][1])) < 1:
            piv['corner_xy' + ('_bottom' if z0 else '')] += 1
        elif min(abs(g['bmin'][0]), abs(g['bmax'][0]), abs(g['bmin'][1]), abs(g['bmax'][1])) < 1:
            piv['edge' + ('_bottom' if z0 else '')] += 1
        else:
            piv['other'] += 1
    S['static_mesh']['pivots'] = dict(piv.most_common())
    # grid: footprint extents of architectural pieces
    grid = {}
    arch = [r for r in geo_ok if r['role'] in ARCH]
    ext = []
    for r in arch:
        ext += [x for x in r['dims'][:2] if x >= 40]
    hist = collections.Counter(int(round(x / 10.0) * 10) for x in ext)
    grid['arch_pieces'] = len(arch)
    grid['footprint_extents'] = len(ext)
    for step in (50, 100, 200, 250, 300, 400, 500):
        grid['on_%d' % step] = round(sum(1 for x in ext if on_step(x, step)) / len(ext), 3) if ext else None
    grid['top_extents'] = hist.most_common(15)
    wh = collections.Counter(int(round(r['dims'][2] / 10.0) * 10) for r in arch if r['role'] in ('wall', 'structure', 'door_window_arch'))
    grid['top_wall_heights'] = wh.most_common(8)
    S['grid'] = grid
    # collision
    col = collections.Counter(); col_role = collections.defaultdict(collections.Counter)
    for r in sm:
        c = r.get('collision') or {}
        k = ('simple' if c.get('simple_shapes') else 'none_simple') + '|' + str(c.get('trace_flag'))
        if not c.get('bodysetup'):
            k = 'no_bodysetup'
        col[k] += 1
        col_role[r.get('role', '?')]['simple' if c.get('simple_shapes') else 'no_simple'] += 1
    S['collision'] = dict(by_kind=dict(col.most_common()), by_role={k: dict(v) for k, v in col_role.items()},
                          shapes=dict(convex=sum((r.get('collision') or {}).get('convex', 0) for r in sm),
                                      box=sum((r.get('collision') or {}).get('box', 0) for r in sm),
                                      sphyl=sum((r.get('collision') or {}).get('sphyl', 0) for r in sm),
                                      sphere=sum((r.get('collision') or {}).get('sphere', 0) for r in sm)),
                          fbx_auto_collision=dict(collections.Counter(str(r.get('fbx_auto_collision')) for r in sm)))
    # ---------------- textures
    tx = by.get('Texture2D', [])
    res = [max(r.get('SizeX') or 0, r.get('SizeY') or 0) for r in tx]
    S['texture2d'] = dict(count=len(tx), bytes=sum(r['bytes'] for r in tx), max_res=max(res) if res else 0,
                          res_hist=dict(sorted(collections.Counter(res).items())),
                          formats=dict(collections.Counter(str(r.get('Format')) for r in tx)),
                          compression=dict(collections.Counter(str(r.get('CompressionSettings')) for r in tx)))
    # ---------------- materials
    mats = by.get('Material', [])
    mfs = by.get('MaterialFunction', [])
    mics = by.get('MaterialInstanceConstant', [])
    HARD = {'landscape_layers', 'world_aligned', 'vertex_colour', 'wpo_or_time', 'custom_hlsl', 'rvt', 'postprocess',
            'depth', 'animated_uv', 'pdo', 'noise'}
    def mclass(r):
        t = set(r.get('tags') or [])
        if t & HARD:
            return 'custom'
        if (r.get('n_texture_samples') or 0) <= 6 and (r.get('n_expressions') or 0) <= 40:
            return 'simple'
        return 'complex_no_flags'
    root = {}
    paths = {}
    for r in mats:
        key = '/Game/' + os.path.splitext(r['path'])[0]
        paths[key] = r
    mic_by = {('/Game/' + os.path.splitext(r['path'])[0]): r for r in mics}
    def master_of(r, depth=0):
        p = r.get('parent') or ''
        p = p.split('.')[0]
        if p in paths:
            return p
        if p in mic_by and depth < 10:
            return master_of(mic_by[p], depth + 1)
        return 'external:' + p if p else None
    mic_master = collections.Counter()
    for r in mics:
        m = master_of(r)
        r['master'] = m
        mic_master[m] += 1
    tagc = collections.Counter(); kinds = collections.Counter(); weighted = collections.Counter()
    for k, r in paths.items():
        c = mclass(r)
        r['complexity'] = c
        kinds[c] += 1
        for t in r.get('tags') or []:
            tagc[t] += 1
        weighted[c] += mic_master.get(k, 0)
    weighted['external_master'] = sum(v for k, v in mic_master.items() if k and k.startswith('external:'))
    S['materials'] = dict(masters=len(mats), functions=len(mfs), instances=len(mics),
                          master_kinds=dict(kinds), master_tags=dict(tagc.most_common()),
                          instances_by_master_kind=dict(weighted),
                          blend=dict(collections.Counter(str(r.get('BlendMode') or 'BLEND_Opaque') for r in mats)),
                          two_sided=sum(1 for r in mats if r.get('TwoSided')),
                          top_masters=[(k, v, paths[k].get('complexity') if k in paths else None,
                                        paths[k].get('tags') if k in paths else None,
                                        paths[k].get('n_expressions') if k in paths else None)
                                       for k, v in mic_master.most_common(8)],
                          engine_functions=dict(collections.Counter(f.split('.')[0] for r in mats + mfs
                                                for f in (r.get('functions') or {}) if f.startswith('/Engine')).most_common(15)))
    # ---------------- blueprints / fx / landscape / foliage
    bps = by.get('Blueprint', []) + by.get('EditorUtilityBlueprint', []) + by.get('EditorUtilityWidgetBlueprint', [])
    S['blueprints'] = [dict(path=r['path'], cls=r['class'], parent=r.get('parent_class'), logic_nodes=r.get('logic_nodes'),
                            logic_by_graph=r.get('logic_by_graph'), components=r.get('components'),
                            meshes=list((r.get('component_meshes') or {}).keys())) for r in bps]
    S['fx'] = dict(cascade=len(by.get('ParticleSystem', [])), niagara_systems=len(by.get('NiagaraSystem', [])),
                   niagara_emitters=len(by.get('NiagaraEmitter', [])))
    S['landscape_assets'] = dict(layer_infos=len(by.get('LandscapeLayerInfoObject', [])),
                                 grass_types=len(by.get('LandscapeGrassType', [])),
                                 rvt=len(by.get('RuntimeVirtualTexture', [])),
                                 landscape_masters=sum(1 for r in mats if 'landscape_layers' in (r.get('tags') or [])))
    S['foliage_assets'] = dict(foliage_types=sum(len(v) for k, v in by.items() if k and k.startswith('FoliageType')),
                               procedural_spawners=len(by.get('ProceduralFoliageSpawner', [])))
    maps = []
    for r in by.get('World', []):
        maps.append(dict(path=r['path'], bytes=r['bytes'], n_actors=r.get('n_actors'), actors=r.get('actors'),
                         bp_instances=r.get('bp_instances'), n_sm_actors=r.get('n_sm_actors'),
                         distinct_sm=len(r.get('sm_actor_meshes') or {}),
                         nonunit_scale=r.get('sm_actor_nonunit_scale'), nonuniform_scale=r.get('sm_actor_nonuniform_scale'),
                         foliage_components=r.get('foliage_components'), foliage_instances=r.get('foliage_instances'),
                         instanced=r.get('instanced'), landscape=r.get('landscape'), lights=r.get('lights'),
                         world_partition=r.get('world_partition')))
    S['maps'] = maps
    # usage of meshes by maps
    use = collections.Counter()
    for r in by.get('World', []):
        for k, v in (r.get('sm_actor_meshes') or {}).items():
            use[k.split('.')[0]] += v
    # ---------------- dependencies
    dep = collections.Counter()
    internal_missing = collections.Counter()
    content = os.path.join(J['pack_dir'], 'data', 'Content')
    for r in R:
        for d in r.get('deps') or []:
            if d.startswith('/Script/'):
                dep['script:' + d[8:]] += 1
            elif d.startswith('/Game/'):
                top = d.split('/')[2]
                if top in roots:
                    rel = d[6:]
                    if not (os.path.exists(os.path.join(content, rel + '.uasset')) or os.path.exists(os.path.join(content, rel + '.umap'))):
                        internal_missing[d] += 1
                else:
                    dep['game:' + d] += 1
            elif d.startswith('/Engine/'):
                dep['engine:' + d] += 1
            else:
                dep['plugin:' + d] += 1
    S['dependencies'] = dict(
        script_modules=sorted({k[7:] for k in dep if k.startswith('script:')}),
        engine_content={k[7:]: v for k, v in dep.most_common() if k.startswith('engine:')},
        other_game_content={k[5:]: v for k, v in dep.most_common() if k.startswith('game:')},
        plugin_content={k[7:]: v for k, v in dep.most_common() if k.startswith('plugin:')},
        internal_missing=dict(internal_missing.most_common()))
    # ---------------- per-asset table (compact)
    assets = []
    for r in R:
        a = dict(path=r['path'], cls=r.get('class'), bytes=r['bytes'])
        if r.get('class') == 'StaticMesh':
            g = r.get('geo') or {}
            a.update(role=r.get('role'), dims=r.get('dims'), bmin=g.get('bmin'), bmax=g.get('bmax'), tris=g.get('tris'),
                     verts=g.get('verts'), uv=g.get('uv_channels'), sections=len(g.get('sections') or {}),
                     geo_fmt=g.get('fmt'), lods=r.get('lods'), nanite=r.get('nanite'),
                     collision=r.get('collision'), materials=[m.get('material') for m in r.get('materials') or []],
                     placed_in_maps=use.get('/Game/' + os.path.splitext(r['path'])[0], 0))
        elif r.get('class') == 'Texture2D':
            a.update(size=[r.get('SizeX'), r.get('SizeY')], fmt=r.get('Format'), compression=r.get('CompressionSettings'),
                     srgb=r.get('SRGB', True))
        elif r.get('class') in ('Material', 'MaterialFunction'):
            a.update(n_expressions=r.get('n_expressions'), n_texture_samples=r.get('n_texture_samples'), tags=r.get('tags'),
                     complexity=r.get('complexity'), blend=r.get('BlendMode'), inputs=r.get('inputs'),
                     functions=r.get('functions'), instances=mic_master.get('/Game/' + os.path.splitext(r['path'])[0], 0))
        elif r.get('class') == 'MaterialInstanceConstant':
            a.update(parent=r.get('parent'), master=r.get('master'), texture_params=r.get('texture_params'),
                     static_params=r.get('static_params'))
        elif r.get('class') and r['class'].startswith('FoliageType'):
            a.update(mesh=r.get('mesh'))
        assets.append(a)
    json.dump(dict(summary=S, assets=assets, generated_by='scratch vault/tools (pkgread.py, meshgeo.py, inventory.py, report.py)'),
              open(out, 'w'), indent=1)
    print(json.dumps(S, indent=1)[:200000])


if __name__ == '__main__':
    main(*sys.argv[1:5])
