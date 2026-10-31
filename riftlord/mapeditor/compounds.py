"""Compound kits and decoration tools for the Map Editor (riftlord/web/src/compounds.js,
decortools.js).

A compound places its members as ordinary plan items -- `added` kit pieces and meshes,
and for a gated archway one `doors` entry -- that share a `group` id, so they move
together and can be ungrouped. Nothing is generated at build time: every member is a
visible, editable item. The anchor member also carries `compound` (a COMPOUNDS key) and
`params` (stack height, chain length, hung item) so the editor can rebuild the group
when a parameter changes.

The hand-made compounds below are joined by the mined ones (MINED, read from
riftlord/data/compounds.json, written by riftlord/mapeditor/compoundmine.py): fixed
assemblies found whole on the shipped levels, each with a label, a role group and an art set.

Every compound carries `source`: the shipped occurrence its default layout equals (level, the
sublevel and actor name of each piece, the anchor's world transform), with `copies` and
`levels` counting where the default occurs whole; a parametric one carries `seen`, its params'
values on the shipped levels. compoundmine writes them, the hand-made ones' under `hand`.

Every offset is a member's transform in its anchor's frame (`t` in uu, `r` the 3x3
rotation-and-scale rows, column vectors), measured over all 33 shipped levels by
/mnt/2tb_pro_a/tmp/levelpatterns/q4_compounds.py (q4_compounds.json); placement rules
are from workshop/research/LEVEL-CONTROLLERS-AND-PATTERNS.md parts 2 and 3. Foliage and
rock statistics are in decor-brush.json (q5_brushdata.py).
"""

import json
import os
from riftlord.core import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))
BRUSH_PATH = gamepath.data('riftlord/decor-brush.json')

I3 = ((1, 0, 0), (0, 1, 0), (0, 0, 1))
#: a quarter turn: the member's x axis is the anchor's -y (a door in its archway)
QUARTER = ((0, 1, 0), (-1, 0, 0), (0, 0, 1))
#: the anchor's frame turned over about x (a chain hung from an upside-down post)
FLIP_X = ((1, 0, 0), (0, -1, 0), (0, 0, -1))


def scaled(r, sx, sy, sz):
    return tuple(tuple(r[i][j] * (sx, sy, sz)[j] for j in range(3)) for i in range(3))


def member(name, kind, t, r=I3):
    return {'name': name, 'kind': kind, 't': list(t), 'r': [list(row) for row in r]}


# -- doors in archway gates (q4: ArchwayGate_*>BP_Door_Exterior_*) ----------------
#: 2-wide exterior door in ArchwayGate_1x1x3_RB: n9 in 6 levels, spread <= 1.3 uu
DOOR_IN_GATE_2W = member('BP_Door_Exterior_2W_C', 'door', (98.0, 299.8, 250.0), QUARTER)
#: 3-wide exterior door in ArchwayGate_1x1x4_RB: n4 in 3 levels, spread <= 1.6 uu
DOOR_IN_GATE_3W = member('BP_Door_Exterior_3W_C', 'door', (97.5, 500.0, 251.2), QUARTER)
#: With the door so (x 95..160, turned -90), its spawner stands on the gate's -x side on
#: all 34 shipped gate doors placed so, and the rift on its +x side on 32 of them; the
#: spawner is on the door's own -y side on all 44 gate doors (q6_gate_facing.py). A new
#: gate turns its +x side towards the viewer.
GATE_DEFENDED = (1, 0)

# -- grinder pit (Scenario_09 Front Lawn, both BP_GrinderPit_3X) --------------------------
GRINDER = 'BP_GrinderPit_3X_C'
#: the pit insert's footprint half-size in its frame (GrinderPit_3X_SKM: 604 x 904 uu)
PIT_HALF = (302.0, 452.0)
#: the floor beside a shipped pit stands this far above the grinder (Floor_SB_3x1, +10)
FLOOR_ABOVE_PIT = 10.0
#: the kill volume under each Front Lawn pit, in the pit's frame: offset, a quarter turn
#: and its brush scale (OMDCharacterKillVolume_1 at (1349.8, 4778.5, 743.9), scale
#: (3.9759, 2.0193, 1.0), under the pit at (1351.0, 4775.0, 890.0) turned -90)
KILL_VOLUME_CLASS = 'OMDCharacterKillVolume'
KILL_VOLUME = member(KILL_VOLUME_CLASS, 'kit', (-3.46, -1.19, -146.14),
                     scaled(((0, -1, 0), (1, 0, 0), (0, 0, 1)), 3.9759, 2.0193, 1.0))
#: that volume is lifted as the donor: (package, object name, FName number)
KILL_VOLUME_DONOR = ('/Game/Maps/Campaign/Scenario_09/Scenario_09', 'OMDCharacterKillVolume', 2)

# -- column stacks (q4: ColumnTile*_CastStone_*) -----------------------------------
#: base to first mid, mid to mid, last mid to top, uu up the anchor's z
COLUMN_A_PITCH = (300.0, 299.9, 299.8)      # n75 L8 / n479 L11 / n142 L13
COLUMN_B_PITCH = (299.9, 300.0, 300.0)      # n52 L8 / n417 L10 / n248 L11
#: mids in a new stack: the 3-tier stack (base to top +900) is on 35 shipped columns
COLUMN_MIDS = 2
#: the most mids on a shipped stack: ColumnTileA 4, ColumnTileB 6 (compounds.json `seen`)
COLUMN_A_MIDS_MAX = 4
COLUMN_MIDS_MAX = 6

# -- hanging chains (q4: Metal_PostBase>Chain_*, Chain_Short_x*>hung items) ---------
#: Metal_PostBase hangs upside down on the ceiling: its z axis is world -z on 139 of 147
POST_TURN = FLIP_X
RING = member('Chain_Ring_End', 'mesh', (-2.3, -3.0, 1.7), FLIP_X)       # n74 L10
#: chain segment pivot in the post's frame, the same for every length (n12..n19)
CHAIN_IN_POST = ((0.2, -0.1, 25.6), FLIP_X)
#: segments by links: mesh, and the drop from its pivot to the hook of what hangs
#: below it (chandeliers: Chain_Short n5, x2 n10, x4 n13, x6 n13)
SEGMENTS = ((6, 'Chain_Short_x6', 628.5), (4, 'Chain_Short_x4', 418.5),
            (2, 'Chain_Short_x2', 207.3), (1, 'Chain_Short', 103.7))
#: a segment hung below a Chain_Short_x6 starts this far below its pivot (n11 L4)
STACK_BELOW_X6 = 642.2
#: links in a new chain: the x6 drop is exact (sd 0) on 15 shipped chandeliers
CHAIN_LINKS = 6
#: the longest shipped chain under a post: 6 links (shipped chains on posts: 1, 2, 4 or 6)
CHAIN_LINKS_MAX = 6
#: what hangs from the last segment, offset from that segment's hook
HUNG = {
    'none': None,
    #: `above`: how high the hung item's origin sits over the floor on shipped levels
    #: (q3_summary by_key h p50), where a chain placed from New Object hangs it
    'chandelier': {'name': 'BP_Chandelier_LargeRadiusCeiling_C', 'kind': 'kit',
                   'r': I3, 'xy': (4.4, 5.2), 'above': 1112.3},
    'chandelier_small': {'name': 'BP_Chandelier_Normal_C', 'kind': 'kit',
                         'r': I3, 'xy': (4.4, 5.3), 'above': 898.2},
    #: a falling boom barrel hangs by its own anchor from the chain's lower end, placed by
    #: controllers.js barrelUnder (riftlord/envtraps.py HANG_ANCHOR)
    'boom_barrel': {'name': 'BP_FallingBoomBarrel_C', 'kind': 'kit', 'r': I3,
                    'xy': (0.0, 0.0), 'via': 'barrelUnder', 'above': 1130.5},
    #: the Chandelier trap hangs by its hook, its origin, at the boom barrels' height
    #: (riftlord/mapeditor/chandelier.py)
    'chandelier_trap': {'name': 'BP_RiftLordChandelier_C', 'kind': 'kit', 'r': I3,
                        'xy': (0.0, 0.0), 'above': 1130.5},
}
CHAIN_ITEM = 'chandelier'
#: a chain placed from New Object hangs its chandelier this high above the floor below
#: (BP_Chandelier_LargeRadiusCeiling height p50, research 3.3)
CHANDELIER_ABOVE_FLOOR = 1112.3

# -- windows (q4: Window_*, Wall_FB_2x2_*_Window>Window_Wood_Shutter) ----------------
WINDOW_SMALL = [member('Window_CastStone_Arch350', 'mesh', (0.0, 0.0, 299.8)),    # n62 L4
                member('Window_CastStone_Sill_Small', 'mesh', (0.0, 0.0, 0.0))]  # n28 L4
WINDOW_LARGE = [member('Window_CastStone_Arch700', 'mesh', (0.0, 150.0, 300.0))]  # n29 L3
SHUTTERS_2X = [   # Scenario_Sand_04 Wall_FB_2x2_2x_Window; the left one is mirrored
    member('Window_Wood_Shutter', 'mesh', (357.6, -30.1, 289.0), scaled(I3, 1, 1, 1.15)),
    member('Window_Wood_Shutter', 'mesh', (241.6, -30.1, 289.0), scaled(I3, -1, 1, 1.15))]
SHUTTERS_DOUBLE = [   # n19..n20 L4..L5
    member('Window_Wood_Shutter', 'mesh', (93.0, -34.0, 287.1), scaled(I3, -1, 1, 1.076)),
    member('Window_Wood_Shutter', 'mesh', (209.2, -34.2, 287.0), scaled(I3, 1, 1, 1.075)),
    member('Window_Wood_Shutter', 'mesh', (393.3, -34.0, 287.1), scaled(I3, -1, 1, 1.076)),
    member('Window_Wood_Shutter', 'mesh', (509.2, -34.0, 287.1), scaled(I3, 1, 1, 1.076))]
SHUTTERS_POINTED = [   # n59 L4 each side
    member('Window_Wood_Shutter', 'mesh', (51.8, 8.6, 124.0), scaled(I3, 0.9, 0.9, 0.9)),
    member('Window_Wood_Shutter', 'mesh', (-53.2, 8.6, 124.0), scaled(I3, -0.9, 0.9, 0.9))]

#: key: (label, anchor name, anchor kind, layout, members or layout settings)
COMPOUNDS = {
    'gate_door_2w': {'label': 'Archway Gate with Door (2-wide)', 'type': 'fixed',
                     'anchor': {'name': 'ArchwayGate_1x1x3_RB_C', 'kind': 'kit'},
                     'members': [DOOR_IN_GATE_2W], 'defended': GATE_DEFENDED,
                     'centre': DOOR_IN_GATE_2W['t'][:2]},
    'gate_door_3w': {'label': 'Archway Gate with Door (3-wide)', 'type': 'fixed',
                     'anchor': {'name': 'ArchwayGate_1x1x4_RB_C', 'kind': 'kit'},
                     'members': [DOOR_IN_GATE_3W], 'defended': GATE_DEFENDED,
                     'centre': DOOR_IN_GATE_3W['t'][:2]},
    'grinder_pit': {'label': 'Grinder Pit', 'type': 'fixed',
                    'anchor': {'name': GRINDER, 'kind': 'kit'},
                    'members': [KILL_VOLUME], 'below': FLOOR_ABOVE_PIT},
    'column_a': {'label': 'Column Stack A', 'type': 'stack',
                 'anchor': {'name': 'ColumnTileA_CastStone_Base', 'kind': 'mesh'},
                 'mid': 'ColumnTileA_CastStone_Mid', 'top': 'ColumnTileA_CastStone_Top',
                 'pitch': COLUMN_A_PITCH, 'params': {'mids': COLUMN_MIDS},
                 'midsMax': COLUMN_A_MIDS_MAX},
    'column_b': {'label': 'Column Stack B', 'type': 'stack',
                 'anchor': {'name': 'ColumnTileB_CastStone_Base', 'kind': 'mesh'},
                 'mid': 'ColumnTileB_CastStone_Mid', 'top': 'ColumnTileB_CastStone_Top',
                 'pitch': COLUMN_B_PITCH, 'params': {'mids': COLUMN_MIDS},
                 'midsMax': COLUMN_MIDS_MAX},
    'chain': {'label': 'Hanging Chain', 'type': 'chain',
              'anchor': {'name': 'Metal_PostBase', 'kind': 'mesh'},
              'params': {'links': CHAIN_LINKS, 'item': CHAIN_ITEM}},
    'chandelier_trap': {'label': 'Chandelier Trap', 'type': 'chain',
                        'group': 'Environmental Traps',
                        'anchor': {'name': 'Metal_PostBase', 'kind': 'mesh'},
                        'params': {'links': CHAIN_LINKS, 'item': 'chandelier_trap'}},
    'window_small': {'label': 'Arched Window', 'type': 'fixed',
                     'anchor': {'name': 'Window_CastStone_Tile350', 'kind': 'mesh'},
                     'members': WINDOW_SMALL},
    'window_large': {'label': 'Arched Window, Large', 'type': 'fixed',
                     'anchor': {'name': 'Window_CastStone_Tile700', 'kind': 'mesh'},
                     'members': WINDOW_LARGE},
    'window_wall_2x': {'label': 'Window Wall with Shutters', 'type': 'fixed',
                       'anchor': {'name': 'Wall_FB_2x2_2x_Window_C', 'kind': 'kit'},
                       'members': SHUTTERS_2X},
    'window_wall_double': {'label': 'Double Window Wall with Shutters', 'type': 'fixed',
                           'anchor': {'name': 'Wall_FB_2x2_Double_Window_C', 'kind': 'kit'},
                           'members': SHUTTERS_DOUBLE},
    'window_pointed': {'label': 'Pointed Window with Shutters', 'type': 'fixed',
                       'anchor': {'name': 'Window_Pointed_1xB', 'kind': 'mesh'},
                       'members': SHUTTERS_POINTED},
    # -- wall assemblies measured by /mnt/2tb_pro_a/tmp/levelpatterns/q8_star_assemblies.py
    # (q8_star_assemblies.json; written by q8_compounds_out.py): an anchor and the pieces at
    # one transform in its frame on at least 30% of its placements in 2+ levels, kept when
    # 6+ placements carry them all. nN/P LL: N of P anchor placements carry them, in L
    # levels. The compound's frame is upright at the anchor's origin; anchor `r`: the
    # anchor's own rotation-and-scale rows in that frame (its commonest shipped one).
    'cren_column_pointed': {'label': 'Column with Pointed Crenellation', 'type': 'fixed',   # q8 n105/278 L5
        'anchor': {'name': 'Column_Sec_1_4_FB', 'kind': 'mesh', 'r': [[0.7498, 0.0, 0.0], [0.0, 0.7498, 0.0], [0.0, 0.0, 0.7498]]},
        'members': [
            member('TowerTop_Pointed_Trim_Square_SM', 'mesh', (0.0, 0.0, 891.0), ((0.92, 0.0, 0.0), (0.0, 0.92, 0.0), (0.0, 0.0, 0.92))),
            member('Crenellation_Pointed_Corbel_Square_SM', 'mesh', (0.0, 0.0, 900.0), ((0.7498, 0.0, 0.0), (0.0, 0.7498, 0.0), (0.0, 0.0, 0.7498))),
            member('Crenellation_Pointed_Square_SM', 'mesh', (0.0, 0.0, 900.0), ((0.7498, 0.0, 0.0), (0.0, 0.7498, 0.0), (0.0, 0.0, 0.7498))),
        ]},
    'cren_column_spire': {'label': 'Column with Pointed Crenellation and Spire', 'type': 'fixed',   # q8 n64/150 L5
        'anchor': {'name': 'Column_Sec_1_4_FB', 'kind': 'mesh', 'r': [[0.7501, 0.0, 0.0], [0.0, 0.7501, 0.0], [0.0, 0.0, 0.75]]},
        'members': [
            member('TowerTop_Pointed_A_SM', 'mesh', (0.0, 0.0, 950.0), ((0.45, 0.0, 0.0), (0.0, 0.45, 0.0), (0.0, 0.0, 0.75))),
            member('Crenellation_Pointed_Corbel_Square_SM', 'mesh', (0.0, 0.0, 900.0), ((0.7501, 0.0, 0.0), (0.0, 0.7501, 0.0), (0.0, 0.0, 0.75))),
            member('Crenellation_Pointed_Square_SM', 'mesh', (0.0, 0.0, 900.0), ((0.7501, 0.0, 0.0), (0.0, 0.7501, 0.0), (0.0, 0.0, 0.75))),
            member('TowerTop_Pointed_Trim_Square_SM', 'mesh', (0.0, 0.0, 891.0), ((0.9198, 0.0, 0.0), (0.0, 0.9198, 0.0), (0.0, 0.0, 0.9203))),
        ]},
    'arcade_column': {'label': 'Pointed Arcade Column', 'type': 'fixed',   # q8 n102/353 L2
        'anchor': {'name': 'ColumnTileB_CastStone_Mid', 'kind': 'mesh'},
        'members': [
            member('ColumnTileB_CastStone_Top', 'mesh', (0.0, 0.0, 300.0), ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))),
            member('Archway_Pointed_CapB_SM', 'mesh', (125.0, 0.0, 630.0), ((0.0, 0.5, 0.0), (-0.5, 0.0, 0.0), (0.0, 0.0, 0.5))),
            member('Column_Pointed_Deco_B_SM', 'mesh', (94.0, -31.0, 662.0), ((0.55, 0.0, 0.0), (0.0, 0.0, 0.525), (0.0, -0.525, 0.0))),
            member('Column_Pointed_Deco_B_SM', 'mesh', (94.0, 33.0, 662.0), ((0.55, 0.0, 0.0), (0.0, 0.0, -0.525), (0.0, 0.525, 0.0))),
            member('Trim_OrnateGrpb_SM', 'mesh', (0.0, 0.0, 465.0), ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))),
        ]},
    'pointed_column_cap': {'label': 'Pointed Column Cap', 'type': 'fixed',   # q8 n112/307 L4
        'anchor': {'name': 'TowerTop_Pointed_F_SM', 'kind': 'mesh', 'r': [[2.25, 0.0, 0.0], [0.0, 2.25, 0.0], [0.0, 0.0, 3.0]]},
        'members': [
            member('Column_Pointed_Deco_B_SM', 'mesh', (0.0, 1.3, -70.0), ((0.7493, 0.0, 0.0), (0.0, 0.0, 0.999), (0.0, -0.999, 0.0))),
        ]},
    'octagon_tower_top': {'label': 'Octagonal Tower Top', 'type': 'fixed',   # q8 n15/44 L3
        'anchor': {'name': 'Column_2x2x1_FB_C', 'kind': 'kit'},
        'members': [
            member('Cren_Bottom_Octogon', 'mesh', (-5.0, -5.0, 1195.0), ((1.35, 0.0, 0.0), (0.0, -1.35, 0.0), (0.0, 0.0, -1.35))),
            member('Column_2x2x1_FB_C', 'kit', (0.0, 0.0, 600.0), ((1.0004, 0.0, 0.0), (0.0, 1.0004, 0.0), (0.0, 0.0, 1.0004))),
            member('Crenellation_Pointed_Merlon_SM', 'mesh', (-290.0, -5.0, 1315.0), ((0.0, -1.0004, 0.0), (1.0004, 0.0, 0.0), (0.0, 0.0, 1.1502))),
            member('Crenellation_Pointed_Merlon_SM', 'mesh', (-230.0, 230.0, 1315.0), ((-0.7074, 0.7074, 0.0), (-0.7074, -0.7074, 0.0), (0.0, 0.0, 1.1502))),
            member('Crenellation_Pointed_Merlon_SM', 'mesh', (-230.0, -240.0, 1315.0), ((0.7074, 0.7074, 0.0), (-0.7074, 0.7074, 0.0), (0.0, 0.0, 1.1502))),
            member('Crenellation_Pointed_Merlon_SM', 'mesh', (-5.0, 280.0, 1315.0), ((-1.0004, 0.0, 0.0), (0.0, -1.0004, 0.0), (0.0, 0.0, 1.1502))),
            member('Crenellation_Pointed_Merlon_SM', 'mesh', (-5.0, -290.0, 1315.0), ((1.0004, 0.0, 0.0), (0.0, 1.0004, 0.0), (0.0, 0.0, 1.1502))),
            member('Crenellation_Pointed_Merlon_SM', 'mesh', (220.0, 230.0, 1315.0), ((-0.7074, -0.7074, 0.0), (0.7074, -0.7074, 0.0), (0.0, 0.0, 1.1502))),
            member('Crenellation_Pointed_Merlon_SM', 'mesh', (220.0, -240.0, 1315.0), ((0.7074, -0.7074, 0.0), (0.7074, 0.7074, 0.0), (0.0, 0.0, 1.1502))),
            member('Crenellation_Pointed_Merlon_SM', 'mesh', (280.0, 0.0, 1315.0), ((0.0, -1.0004, 0.0), (1.0004, 0.0, 0.0), (0.0, 0.0, 1.1502))),
        ]},
    'octagon_cren': {'label': 'Octagonal Stepped Crenellation', 'type': 'fixed',   # q8 n25/46 L2
        'anchor': {'name': 'SM_MERGED_Cren_Stepped_Octogon', 'kind': 'mesh'},
        'members': [
            member('Cren_Top_Octogon', 'mesh', (100.0, 280.0, -10.0), ((1.612, 0.0, 0.0), (0.0, 1.612, 0.0), (0.0, 0.0, 2.15))),
        ]},
    'round_tower_turret': {'label': 'Round Tower with Turret', 'type': 'fixed',   # q8 n22/61 L3
        'anchor': {'name': 'Tower_Round_RB', 'kind': 'mesh', 'r': [[0.6175, 0.0, 0.0], [0.0, 0.6175, 0.0], [0.0, 0.0, 0.6175]]},
        'members': [
            member('Turret_Round_Base', 'mesh', (0.0, 0.0, 720.3), ((0.82, 0.0, 0.0), (0.0, 0.82, 0.0), (0.0, 0.0, 0.93))),
            member('Tower_Round_RB', 'mesh', (0.0, 0.0, 370.3), ((0.6175, 0.0, 0.0), (0.0, 0.6175, 0.0), (0.0, 0.0, 0.6175))),
            member('Turret_Round', 'mesh', (0.0, 0.0, 720.3), ((0.8692, 0.0, 0.0), (0.0, 0.8692, 0.0), (0.0, 0.0, 0.8686))),
        ]},
    'square_turret_wood': {'label': 'Square Turret with Wood Top', 'type': 'fixed',   # q8 n30/94 L3
        'anchor': {'name': 'Turret_Square_TiledEdge', 'kind': 'mesh', 'r': [[1.167, 0.0, 0.0], [0.0, 1.167, 0.0], [0.0, 0.0, 1.217]]},
        'members': [
            member('SM_MERGED_WoodTowerTop', 'mesh', (2.0, -6.0, 50.0), ((0.7282, 0.0, 0.0), (0.0, 0.7282, 0.0), (0.0, 0.0, 0.729))),
        ]},
    'wall_stepped_cren': {'label': 'Wall Section with Stepped Crenellation', 'type': 'fixed',   # q8 n8/22 L3
        'anchor': {'name': 'Wall_Sec_1_3_V2_RB', 'kind': 'mesh', 'r': [[1.0, 0.0, 0.0], [0.0, 0.825, 0.565], [0.0, -0.565, 0.825]]},
        'members': [
            member('Cren_Stepped_Square_Group_CS', 'mesh', (-149.5, 57.6, 189.0), ((1.0, 0.0, 0.0), (0.0, 0.9998, 0.0), (0.0, 0.0, 0.9998))),
        ]},
    'stepped_cren_three': {'label': 'Stepped Crenellation with Half Merlon', 'type': 'fixed',   # q8 n10/16 L2
        'anchor': {'name': 'Cren_Stepped_Group_Three_CS', 'kind': 'mesh'},
        'members': [
            member('Cren_1_Half_RB', 'mesh', (-30.0, 85.0, -10.0), ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))),
        ]},
    'window_slanted': {'label': 'Slanted Arched Window', 'type': 'fixed',   # q8 n8/24 L2
        'anchor': {'name': 'Window_CastStone_Tile700_Slanted', 'kind': 'mesh'},
        'members': [
            member('Window_CastStone_Arch700_Slanted', 'mesh', (0.0, 0.0, 600.0), ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))),
        ]},
    'pointed_tower_shutters': {'label': 'Pointed Tower Top with Shutters', 'type': 'fixed',   # q8 n13/42 L4
        'anchor': {'name': 'TowerTop_Pointed_SM', 'kind': 'mesh', 'r': [[0.85, 0.0, 0.0], [0.0, 0.85, 0.0], [0.0, 0.0, 0.85]]},
        'members': [
            member('Window_Wood_Shutter', 'mesh', (-59.2, 168.6, 339.0), ((-0.9996, 0.0, 0.0), (0.0, 0.9996, 0.0), (0.0, 0.0, 1.1501))),
            member('Window_Wood_Shutter', 'mesh', (56.8, 168.6, 339.0), ((0.9996, 0.0, 0.0), (0.0, 0.9996, 0.0), (0.0, 0.0, 1.1501))),
        ]},
    'sand_gold_column': {'label': 'Gold Column on Block', 'type': 'fixed',   # q8 n15/31 L2
        'anchor': {'name': 'Column_Sec_1_1_FB_C', 'kind': 'kit'},
        'members': [
            member('ColumnTileC_CastStone_Gold_SAND_C', 'kit', (150.0, -150.0, 450.0), ((0.0, 1.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0))),
            member('Column_Pointed_Deco_C_SM', 'mesh', (150.0, -150.0, 1075.0), ((-1.7, 0.0, 0.0), (0.0, -2.5, 0.0), (0.0, 0.0, 1.65))),
            member('Column_Pointed_Deco_C_SM', 'mesh', (150.0, -150.0, 1075.0), ((0.0, 2.5, 0.0), (-1.7, 0.0, 0.0), (0.0, 0.0, 1.65))),
        ]},
}

#: The role group and art set each hand-made compound is filed under in New Object.
HAND_FILING = {
    'gate_door_2w': ('Arches and Gates', 'Castle'), 'gate_door_3w': ('Arches and Gates', 'Castle'),
    'grinder_pit': ('Environmental Traps', 'Castle'),
    'column_a': ('Columns and Pillars', 'Castle'), 'column_b': ('Columns and Pillars', 'Castle'),
    'chain': ('Lights and Braziers', 'Castle'),
    'chandelier_trap': ('Environmental Traps', 'Castle'),
    'window_small': ('Windows', 'Castle'), 'window_large': ('Windows', 'Castle'),
    'window_wall_2x': ('Windows', 'Castle'), 'window_wall_double': ('Windows', 'Castle'),
    'window_pointed': ('Windows', 'Castle'),
    'cren_column_pointed': ('Crenellations', 'Sand'), 'cren_column_spire': ('Crenellations', 'Sand'),
    'arcade_column': ('Columns and Pillars', 'Sand'),
    'pointed_column_cap': ('Columns and Pillars', 'Sand'),
    'octagon_tower_top': ('Towers and Turrets', 'Castle'),
    'octagon_cren': ('Crenellations', 'Castle'),
    'round_tower_turret': ('Towers and Turrets', 'Castle'),
    'square_turret_wood': ('Towers and Turrets', 'Castle'),
    'wall_stepped_cren': ('Crenellations', 'Castle'),
    'stepped_cren_three': ('Crenellations', 'Castle'),
    'window_slanted': ('Windows', 'Castle'),
    'pointed_tower_shutters': ('Towers and Turrets', 'Sand'),
    'sand_gold_column': ('Columns and Pillars', 'Sand'),
}
#: The mined compounds (riftlord/mapeditor/compoundmine.py --write): fixed assemblies with
#: a label, a role group and an art set; a hand-made key wins.
MINED_PATH = gamepath.data('compounds.json')


def _mined():
    try:
        with open(MINED_PATH) as fh:
            return json.load(fh).get('compounds') or {}
    except (OSError, ValueError):
        return {}


def _hand_sources():
    try:
        with open(MINED_PATH) as fh:
            return json.load(fh).get('hand') or {}
    except (OSError, ValueError):
        return {}


#: The hand-made compounds, each with its shipped source (compounds.json `hand`).
HAND = dict(COMPOUNDS)
for _key, _got in _hand_sources().items():
    if _key in HAND:
        HAND[_key].update(_got)
MINED = {k: v for k, v in _mined().items() if k not in COMPOUNDS}
COMPOUNDS.update(MINED)


def filing(key):
    """(role group, art set) a compound is filed under."""
    c = COMPOUNDS[key]
    if key in HAND_FILING:
        return HAND_FILING[key]
    return c.get('group') or 'compound', c.get('set') or 'Castle'

# -- decoration tools ----------------------------------------------------------------
#: Wall Run dressings: (spacing, height above the floor, offset from the face), uu.
#: Medians from research 3.3 (q3_summary by_key: nn same key, h, dw). Every one faces
#: out along its local +y (q4 dressing: +Y on 425 of 428 wall torches).
WALL_RUN = {
    'BP_WallTorch_Normal_C': {'label': 'Wall Torch', 'kind': 'kit',
                              'spacing': 830.0, 'height': 347.0, 'offset': 21.0},
    'BP_WallTorch_LargeRadius_C': {'label': 'Wall Torch, Large Radius', 'kind': 'kit',
                                   'spacing': 900.0, 'height': 353.8, 'offset': 12.1},
    'BP_WallTorch_SmallRadius_C': {'label': 'Wall Torch, Small Radius', 'kind': 'kit',
                                   'spacing': 744.6, 'height': 350.1, 'offset': 24.7},
    'BP_WallTorch_Rain_C': {'label': 'Wall Torch, Rain', 'kind': 'kit',
                            'spacing': 1095.0, 'height': 361.4, 'offset': 21.8},
    'Banner_BP_C': {'label': 'Banner', 'kind': 'kit',
                    'spacing': 1040.0, 'height': 633.0, 'offset': 2.0},
    'BannerLong_BP_C': {'label': 'Long Banner', 'kind': 'kit',
                        'spacing': 1200.0, 'height': 1550.0, 'offset': 5.0},
    'Crest_Raven_SM': {'label': 'Raven Crest', 'kind': 'mesh',
                       'spacing': 718.3, 'height': 385.1, 'offset': 0.7},
    'OrderShield': {'label': 'Order Shield', 'kind': 'mesh',
                    'spacing': 900.0, 'height': 209.9, 'offset': 0.0},
    'Lionhead_SM': {'label': 'Lion Head', 'kind': 'mesh',
                    'spacing': 900.0, 'height': 170.1, 'offset': 0.0},
}
WALL_RUN_DEFAULT = 'BP_WallTorch_Normal_C'
#: Rock Scatter: rocks per click and the radius they spread over (research 3.3: pairs
#: are the commonest cluster, radius p50 133.7)
ROCK_COUNT = 2
ROCK_RADIUS = 133.7
#: uniform scale range (research 3.5 table: 1-6 for the dominant Big_Stone_03_A)
ROCK_SCALE = (1.0, 6.0)
ROCK_MIX = ('Big_Stone_03_A', 'Big_stone_01A', 'Big_Stone_02A')
#: small stones rest on their pivot, scale 1-4 (research 3.3). The research's 36..41 uu
#: sink measured most of them against an overlapping stone (q3 counts rocks as support:
#: 182 of 242 have a Small_stone_01* as the top under them), and applied it buried them.
SMALL_STONE_MIX = ('Small_stone_01A_A', 'Small_stone_01B_A')
SMALL_STONE_SCALE = (1.0, 4.0)
#: Foliage brush: align to the surface where shipped instances follow the slope on more
#: than this share (research 3.4)
ALIGN_TILT_SHARE = 0.5
FOLIAGE_DEFAULT = 'Grass_Clumped_1'
#: Beam: WoodBeam7 runs along +x for 599.5 uu at scale 1 (meshcat bounds)
BEAM_MESH = 'WoodBeam7'
BEAM_LENGTH = 599.5
#: Railing Run: shipped railings repeat every 300 uu along +x (q4: RailingB>RailingC n30 L8)
RAILING_PITCH = 300.0
RAILINGS = ('RailingA', 'RailingB', 'RailingC')


def brush():
    try:
        with open(BRUSH_PATH) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {'foliage': {}, 'rocks': {}}


def decor():
    """Everything compounds.js and decortools.js need, for /api/decor."""
    b = brush()
    return {
        'compounds': COMPOUNDS,
        'chain': {'post': POST_TURN, 'ring': RING, 'chainInPost': CHAIN_IN_POST,
                  'segments': SEGMENTS, 'stackBelowX6': STACK_BELOW_X6,
                  'hung': HUNG,
                  'linksMax': CHAIN_LINKS_MAX, 'aboveFloor': CHANDELIER_ABOVE_FLOOR},
        'wallRun': {'items': WALL_RUN, 'default': WALL_RUN_DEFAULT},
        'rocks': {'mix': {k: (b.get('rocks', {}).get(k) or {}).get('units', 1)
                          for k in ROCK_MIX},
                  'small': {k: (b.get('rocks', {}).get(k) or {}).get('units', 1)
                            for k in SMALL_STONE_MIX},
                  'count': ROCK_COUNT, 'radius': ROCK_RADIUS, 'scale': ROCK_SCALE,
                  'smallScale': SMALL_STONE_SCALE},
        'foliage': {'meshes': b.get('foliage', {}), 'alignShare': ALIGN_TILT_SHARE,
                    'default': FOLIAGE_DEFAULT},
        'beam': {'mesh': BEAM_MESH, 'length': BEAM_LENGTH},
        'railing': {'meshes': RAILINGS, 'pitch': RAILING_PITCH},
    }


#: Prefix of a compound's preview name (/api/thumb/compound:<key>).
THUMB_PREFIX = 'compound:'
#: Bumped when the preview drawing changes, so cached previews are redrawn.
THUMB_VERSION = 1


def catalogue_rows(rows):
    """New Object rows for the compounds, each previewed by its whole assembly (thumbnail)."""
    out = []
    for key, c in COMPOUNDS.items():
        group, art = filing(key)
        out.append({'name': key, 'kind': 'compound', 'category': 'compound',
                    'label': c['label'], 'group': group, 'set': art,
                    'thumb': THUMB_PREFIX + key,
                    'trapgrid': False, 'cells': [0, 0, 0]})
    return out


# -- the whole assembly (previews; compounds.js build is the editor's copy) ---------------

def _mat(r=I3, t=(0.0, 0.0, 0.0)):
    return [list(r[0]) + [t[0]], list(r[1]) + [t[1]], list(r[2]) + [t[2]], [0.0, 0.0, 0.0, 1.0]]


def _mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _lift(z):
    return _mat(I3, (0.0, 0.0, z))


# -- pattern compounds (parametric: repeated, stretched and optional parts) --------------
#
# A 'pattern' compound's members are `parts`, each {name, kind, r, t} and optionally:
#   stretch {axis, by}  column `axis` of r multiplied by the value `by`
#   repeat {count, axis, span: [a, b]}  `count` copies with t[axis] spread evenly from a to b
#       (one copy: the middle); {count, axis, step, at}: copies `step` apart centred on `at`;
#       {count, axis, across, at}: copies spread evenly over `across` centred on `at`
#   when  the part is placed only while this value is above 0
# A value is a number or [param, factor, offset] = params[param] * factor + offset.
# `params` holds the defaults and `paramSpec` {name: {label, min, max, step}} the ranges.

def value(e, params):
    """A pattern value: a number, or [param, factor, offset]."""
    if isinstance(e, (int, float)):
        return float(e)
    name, factor, offset = e
    return float(params.get(name) or 0.0) * float(factor) + float(offset)


def pattern_parts(c, params):
    """[(name, kind, 4x4)] of a pattern compound's parts at `params`, in its frame."""
    params = dict(c.get('params') or {}, **(params or {}))
    out = []
    for part in c['parts']:
        if 'when' in part and value(part['when'], params) <= 0:
            continue
        r = [list(row) for row in part['r']]
        if 'stretch' in part:
            k = part['stretch']['axis']
            s = value(part['stretch']['by'], params)
            for i in range(3):
                r[i][k] *= s
        t = [value(e, params) for e in part['t']]
        spots = [t]
        if 'repeat' in part:
            rep = part['repeat']
            n = max(0, int(round(value(rep['count'], params))))
            axis = rep['axis']
            spots = []
            for i in range(n):
                at = list(t)
                if 'across' in rep:
                    step = value(rep['across'], params) / (n - 1) if n > 1 else 0.0
                    at[axis] = value(rep['at'], params) + (i - (n - 1) / 2) * step
                elif 'step' in rep:
                    at[axis] = value(rep['at'], params) + (i - (n - 1) / 2) * value(rep['step'], params)
                else:
                    a, b = (value(e, params) for e in rep['span'])
                    at[axis] = (a + b) / 2 if n == 1 else a + i * (b - a) / (n - 1)
                spots.append(at)
        for at in spots:
            out.append((part['name'], part['kind'], _mat(r, at)))
    return out


def pattern_params(c, raw):
    """`raw` params kept within a pattern compound's paramSpec, or {}."""
    out = {}
    for name, spec in (c.get('paramSpec') or {}).items():
        v = (raw or {}).get(name)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            v = max(spec['min'], min(spec['max'], float(v)))
            out[name] = int(round(v)) if float(spec.get('step', 1)).is_integer() else v
    return out


def anchor_turn(c):
    """The anchor's own rotation-and-scale rows in the compound's frame (its members are
    placed in that frame), identity when it has none."""
    return c['anchor'].get('r') or I3


def chain_layout(post, params):
    """[(name, kind, 4x4)] a hanging chain hangs under its post (the post left out), as
    compounds.js chainMembers places them; a hung item placed by `via` is left out."""
    out = [(RING['name'], 'mesh', _mul(post, _mat(RING['r'], RING['t'])))]
    pivot = _mul(post, _mat(CHAIN_IN_POST[1], CHAIN_IN_POST[0]))
    segs = []
    left = max(1, int(params.get('links') or 1))
    for n, mesh, drop in SEGMENTS:
        while left >= n:
            segs.append((n, mesh, drop))
            left -= n
    for i, (n, mesh, drop) in enumerate(segs):
        if i:
            prev = segs[i - 1]
            pivot = _mul(pivot, _lift(-(STACK_BELOW_X6 if prev[0] == 6 else prev[2])))
        out.append((mesh, 'mesh', pivot))
    hook = _mul(pivot, _lift(-segs[-1][2]))
    hung = HUNG.get(params.get('item'))
    if hung and not hung.get('via'):
        at = _mul(_mul(hook, _mat(I3, (hung['xy'][0], hung['xy'][1], 0.0))), _mat(hung['r']))
        out.append((hung['name'], hung['kind'], at))
    return out


def layout(key, params=None):
    """[(name, kind, 4x4 column-vector matrix)] of a compound's anchor and members at its
    default params (or `params`), anchored at the origin as compounds.js build places them."""
    return layout_of(COMPOUNDS[key], params)


def layout_of(c, params=None):
    """layout() of a compound definition."""
    params = dict(c.get('params') or {}, **(params or {}))
    anchor = _mat(POST_TURN) if c['type'] == 'chain' else _mat()
    head = c['anchor']
    out = [(head['name'], head['kind'], _mul(anchor, _mat(head.get('r') or I3)))]
    if c['type'] == 'fixed':
        out += [(m['name'], m['kind'], _mul(anchor, _mat(m['r'], m['t']))) for m in c['members']]
    elif c['type'] == 'stack':
        first, step, top = c['pitch']
        at = anchor
        mids = max(0, min(c.get('midsMax', COLUMN_MIDS_MAX), int(params.get('mids') or 0)))
        for k in range(mids):
            at = _mul(at, _lift(first if k == 0 else step))
            out.append((c['mid'], 'mesh', at))
        out.append((c['top'], 'mesh', _mul(at, _lift(top if mids else first))))
    elif c['type'] == 'chain':
        out += chain_layout(anchor, params)
    elif c['type'] == 'pattern':
        out += [(n, k, _mul(anchor, m)) for n, k, m in pattern_parts(c, params)]
    return out


def model(key, params=None):
    """A compound's whole assembly (kit pieces and doors by their parts, meshes as they are,
    each member with its own materials) as omd3thumb.merge gives it ('verts', 'tris', 'uvs',
    'faces', 'surfaces'), or None when nothing draws."""
    from riftlord.mapeditor import kitdonors
    from riftlord.mapeditor import omd3serve
    from riftlord.mapeditor import omd3thumb
    placed = []
    for name, kind, m in layout(key, params):
        if kind == 'fx':
            continue
        if kind in ('kit', 'door'):
            got = kitdonors.model(name)
        else:
            got = omd3serve.mesh(name)
        if not got or not got.get('verts') or not got.get('tris'):
            continue
        placed.append(omd3thumb.place(got, m))
    return omd3thumb.merge(placed)


def thumbnail(key, size=None):
    """A compound's New Object preview as PNG: the whole assembly at its default params,
    drawn as every mesh preview is (omd3thumb), kept on disk; None for an unknown key."""
    import hashlib
    import io
    from riftlord.mapeditor import kitdonors
    from riftlord.mapeditor import omd3serve
    from riftlord.mapeditor import omd3thumb
    if key not in COMPOUNDS:
        return None
    size = size or kitdonors.THUMB_SIZE
    from riftlord.mapeditor import kitactors
    #: a carried kit piece (the Chandelier) is drawn from its parts, which change with it
    carried = [kitactors.parts(n) for n, k, _m in layout(key)
               if k == 'kit' and kitactors.CLASSES.get(n, {}).get('carried')]
    tag = hashlib.sha1(json.dumps([THUMB_VERSION, omd3thumb.STYLE, size, layout(key)] + carried)
                       .encode()).hexdigest()[:12]
    cached = os.path.join(omd3serve.PREVIEWS, 'compound__%s__%s.png' % (key, tag))
    if os.path.exists(cached):
        with open(cached, 'rb') as fh:
            return fh.read()
    got = model(key)
    image = omd3thumb.render(got, size) if got else None
    if image is None:
        return None
    buf = io.BytesIO()
    image.save(buf, format='PNG')
    png = buf.getvalue()
    omd3serve.keep(cached, png)
    return png


# -- the plan ------------------------------------------------------------------------

#: params a compound may carry, with their type and bounds
PARAMS = {'mids': (int, 0, COLUMN_MIDS_MAX), 'links': (int, 1, CHAIN_LINKS_MAX),
          'item': (str, None, None)}
#: labels a tool group carries in `compound` (decortools.js)
TOOL_GROUPS = ('wallrun', 'rocks', 'foliage', 'beam', 'railing')


def clean_member(raw):
    """The group fields of one `added` item worth keeping, or {}."""
    out = {}
    g = raw.get('group')
    if isinstance(g, int) and not isinstance(g, bool) and g > 0:
        out['group'] = g
    else:
        return out
    key = raw.get('compound')
    if isinstance(key, str) and (key in COMPOUNDS or key in TOOL_GROUPS):
        out['compound'] = key
    if raw.get('anchor') is True:
        out['anchor'] = True
    params = raw.get('params')
    if isinstance(params, dict) and COMPOUNDS.get(key or '', {}).get('type') == 'pattern':
        kept = pattern_params(COMPOUNDS[key], params)
        if kept:
            out['params'] = kept
    elif isinstance(params, dict):
        kept = {}
        for name, (kind, lo, hi) in PARAMS.items():
            v = params.get(name)
            if kind is int and isinstance(v, (int, float)) and not isinstance(v, bool):
                if name == 'mids':
                    hi = COMPOUNDS.get(key or '', {}).get('midsMax', hi)
                kept[name] = max(lo, min(hi, int(v)))
            elif kind is str and isinstance(v, str) and (name != 'item' or v in HUNG):
                kept[name] = v
        if kept:
            out['params'] = kept
    return out


def keep_groups(added, marks, total):
    """Copy the group fields onto the cleaned `added` items.

    `marks` is [(len(added) before the raw item was cleaned, raw item)], in order, and
    `total` the final length: a raw item produced an item when the next mark (or the
    end) is past its own."""
    for k, (start, raw) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else total
        if end > start and isinstance(raw, dict):
            added[start].update(clean_member(raw))


def donor(item, cache={}):
    """(package, export) an added item lifts from, or None for the class's catalogue
    donor: a kill volume lifts from the one under a Front Lawn grinder pit, so its brush
    and scale are the shipped pit's (apply._add)."""
    if (item or {}).get('name') != KILL_VOLUME_CLASS:
        return None
    if 'kill' not in cache:
        import struct
        from riftlord.core import gamefiles
        from riftlord.build import levelbuild
        from riftlord.formats.ue.uassetio import Package
        path, name, number = KILL_VOLUME_DONOR
        ua, ux = gamefiles.package(path)
        pkg = Package(ua, ux)
        pkg.load_payloads()
        found = None
        for i, e in enumerate(pkg.exports):
            if levelbuild._name_of(pkg, i) == name and \
                    struct.unpack_from('<i', e['meta'], levelbuild.NAME_AT + 4)[0] == number:
                found = i
                break
        cls = found is not None and levelbuild.resolve(
            pkg, struct.unpack_from('<i', pkg.exports[found]['meta'], 0)[0])[1]
        cache['kill'] = (pkg, found) if cls == KILL_VOLUME_CLASS else None
    return cache['kill']


def pit_floors(plan):
    """[(lo, hi, sloped, yaw)] walkable boxes over the plan's grinder pits, at the floor
    height beside a shipped pit, for a scratch level's walkable graph (scratch._boxes):
    enemies walk over a pit as over the floor around it."""
    import math
    out = []
    for item in (plan or {}).get('added') or []:
        if not isinstance(item, dict) or item.get('name') != GRINDER:
            continue
        m = item.get('matrix') or []
        if len(m) != 16:
            continue
        top = m[14] + FLOOR_ABOVE_PIT
        pts = [(m[0] * x + m[4] * y + m[12], m[1] * x + m[5] * y + m[13])
               for x in (-PIT_HALF[0], PIT_HALF[0]) for y in (-PIT_HALF[1], PIT_HALF[1])]
        out.append(([min(p[0] for p in pts), min(p[1] for p in pts), top - 2 * FLOOR_ABOVE_PIT],
                    [max(p[0] for p in pts), max(p[1] for p in pts), top], False,
                    math.degrees(math.atan2(m[1], m[0]))))
    return out


def door_openings(plan):
    """[((x0, y0), (x1, y1), z_lo, z_hi)] across each plan door's opening: the door's
    widest part along the door's own x axis, in Unreal space. A scratch level keeps the
    floor this crosses walkable (scratch.cover): an archway's frame parts (Metal_DoorTrim)
    span its opening as boxes, and the door is where the route goes through."""
    import math
    from riftlord.formats.omd3 import meshcat
    from riftlord.formats.omd3 import omd3level
    from riftlord.mapeditor import leveldata
    cat = meshcat.load()
    out = []
    for door in (plan or {}).get('doors') or []:
        at = door.get('at') or [0.0, 0.0, 0.0]
        yaw = math.radians(float(door.get('rotation') or 0.0))
        lo_x = lo_z = math.inf
        hi_x = hi_z = -math.inf
        for part in leveldata.kit_parts(door.get('cls') or '').get('parts') or []:
            info = cat.get(part.get('mesh'))
            if not info:
                continue
            m = omd3level._matrix(part.get('loc') or (0, 0, 0), part.get('rot') or (0, 0, 0),
                                  part.get('scale') or (1, 1, 1))
            o, e = info['origin'], info['extent']
            for dx in (-1, 1):
                for dy in (-1, 1):
                    for dz in (-1, 1):
                        c = (o[0] + dx * e[0], o[1] + dy * e[1], o[2] + dz * e[2])
                        x = sum(m[0][k] * c[k] for k in range(3)) + m[0][3]
                        z = sum(m[2][k] * c[k] for k in range(3)) + m[2][3]
                        lo_x, hi_x = min(lo_x, x), max(hi_x, x)
                        lo_z, hi_z = min(lo_z, z), max(hi_z, z)
        if lo_x == math.inf:
            continue
        ends = [(at[0] + math.cos(yaw) * x, at[1] + math.sin(yaw) * x) for x in (lo_x, hi_x)]
        out.append((ends[0], ends[1], at[2] + lo_z, at[2] + hi_z))
    return out


def crosses(quad, openings, below=0.0):
    """True when an opening's segment crosses the quad's XY rectangle and the quad's
    height lies within `below` under the opening's bottom and its top."""
    x0, x1 = min(v[0] for v in quad), max(v[0] for v in quad)
    y0, y1 = min(v[1] for v in quad), max(v[1] for v in quad)
    cz = sum(v[2] for v in quad) / len(quad)
    for (ax, ay), (bx, by), z_lo, z_hi in openings:
        if not z_lo - below <= cz <= z_hi:
            continue
        #: Liang-Barsky: the segment's part inside the rectangle
        t0, t1 = 0.0, 1.0
        dx, dy = bx - ax, by - ay
        ok = True
        for p, q in ((-dx, ax - x0), (dx, x1 - ax), (-dy, ay - y0), (dy, y1 - ay)):
            if p == 0:
                if q < 0:
                    ok = False
                    break
                continue
            r = q / p
            if p < 0:
                t0 = max(t0, r)
            else:
                t1 = min(t1, r)
            if t0 > t1:
                ok = False
                break
        if ok:
            return True
    return False


def keep_door_group(raw, door):
    """A door placed with an archway keeps its group id (leveldata.save_plan)."""
    g = raw.get('group')
    if isinstance(g, int) and not isinstance(g, bool) and g > 0:
        door['group'] = g
    return door
