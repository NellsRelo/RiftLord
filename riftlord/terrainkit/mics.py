"""The mesh-paint terrain instances a terrain library carries (no shipped instance has snow or a
paintable sand), as library.write_carried_mic specs, and their layers in paint.layers' form.

Every texture is a shipped one, imported by the instance. Slots are set by ExpressionGUID: the
master names each layer's parameters alike (BC, MRH, NM with FName numbers 1-4).
"""

from riftlord.terrainkit import paint

#: The shipped instance each carried one is cloned from (parent Master_Enviro_MeshPaint_Terrain).
DONOR = '/Game/Environments/LandScapes/Layered_Mats/Ground_Dirt_Rock_MP'
#: Per BC slot of the master: the ExpressionGUID of its BC, MRH and NM parameters (measured on the
#: donor's TextureParameterValues; the BC GUIDs match paint._slot_guids).
GUIDS = {'BC_1': {'BC': '86f47c626bb7ac4cacb531de0d811e42', 'MRH': '398131f013b9224a8dbbea2297c7a100',
                  'NM': '76c66e265147e440b53bac90c0db66d9'},
         'BC_2': {'BC': 'e3f791d8b14a7e4e9d2de4bc0363244e', 'MRH': 'eebb9a38e1404a47b540dd60319a484c',
                  'NM': 'f2647f39e4247c44a6ad303089920347'},
         'BC_3': {'BC': 'c5bd892735e66b4481a90c5efcd462ef', 'MRH': '7c75c5b3496c814c99b3e8ab3d1d0ed3',
                  'NM': '29c24d62990a944f9f0f5330ecebd5d0'},
         'BC_4': {'BC': '817a9a751585014b8b897ff9fdd278ee', 'MRH': '88e22eda48d7e04482f0bb4030752599',
                  'NM': '4905769df55a5a4cbfd61688af518786'}}
SNOW = '/Game/Environments/SnowIce/Mat/Snow_02_'
RED_DIRT = '/Game/Environments/LandScapes/GroundTextures/Dirt/Mat/Dirt_Red_01_'
ROCK = '/Game/Environments/LandScapes/GroundTextures/Set1/Mat/Set1_Rock_01_'
SAND = '/Game/Environments/LandScapes/Sand/Mat/Sand_Base_01_'
SAND_DETAIL = '/Game/Environments/LandScapes/Sand/Mat/Sand_Detail_'
DIRT_SAND = '/Game/Environments/LandScapes/GroundTextures/Dirt/Mat/DirtSand_1_'
#: Per carried instance, per BC slot: its BC, MRH and NM textures (the sand set's MRA stands in
#: for MRH; DirtSand_1 has no normal map of its own).
INSTANCES = {
    'MI_Terrain_Snow': {
        'BC_1': {'BC': SNOW + 'BC', 'MRH': SNOW + 'MRH', 'NM': SNOW + 'NM'},
        'BC_2': {'BC': RED_DIRT + 'BC', 'MRH': RED_DIRT + 'MRH', 'NM': RED_DIRT + 'NM'},
        'BC_3': {'BC': ROCK + 'BC', 'MRH': ROCK + 'MRH', 'NM': ROCK + 'NM'},
        'BC_4': {'BC': SNOW + 'BC', 'MRH': SNOW + 'MRH', 'NM': SNOW + 'NM'}},
    'MI_Terrain_Sand': {
        'BC_1': {'BC': SAND + 'BC', 'MRH': SAND + 'MRA', 'NM': SAND + 'NM'},
        'BC_2': {'BC': SAND_DETAIL + 'BC', 'MRH': SAND_DETAIL + 'MRA', 'NM': SAND_DETAIL + 'NM'},
        'BC_3': {'BC': DIRT_SAND + 'BC', 'MRH': DIRT_SAND + 'MRH', 'NM': SAND_DETAIL + 'NM'},
        'BC_4': {'BC': ROCK + 'BC', 'MRH': ROCK + 'MRH', 'NM': ROCK + 'NM'}},
}
#: The donor's TileSize_U / TileSize_V on every layer.
TILE_SIZE = 0.35
#: Landscape master -> the carried instance its paintable tiles are drawn with.
CARRIED = {'/Game/MasterMaterials/Enviro/Terrain/Master_Enviro_Landscape_Ice_MP': 'MI_Terrain_Snow',
           '/Game/MasterMaterials/Enviro/Sand/Master_Landscape_Sand': 'MI_Terrain_Sand'}


def carried(material):
    """The carried instance a landscape material's paintable tiles use, or None."""
    return CARRIED.get(paint.master_of(material))


def used(level_sources):
    """Sorted names of the carried instances a map's sources need."""
    return sorted({n for s in level_sources if s.get('kind') == 'landscape'
                   for n in [carried(s['material'])] if n})


def spec(name):
    """The write_carried_mic spec of a carried instance, plus 'name'."""
    textures = [(kind, GUIDS[slot][kind], path) for slot, maps in sorted(INSTANCES[name].items())
                for kind, path in sorted(maps.items())]
    return {'name': name, 'donor': DONOR, 'textures': textures}


SPECS = [spec(n) for n in sorted(INSTANCES)]


def layers(name):
    """paint.layers of a carried instance: [{slot, channel, texture, tint, name}], base first."""
    out = []
    for slot, channel, _tint in paint.BLENDS[paint.MASTER]:
        tex = INSTANCES[name][slot]['BC']
        out.append({'slot': slot, 'channel': channel, 'texture': tex, 'tint': [1.0, 1.0, 1.0],
                    'name': paint.layer_name(tex)})
    return out


def tile_sizes(name):
    """paint.tile_sizes of a carried instance: TileSize_U and TileSize_V of the four layers."""
    return [TILE_SIZE] * 8
