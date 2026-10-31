"""Terrain kits: 4x4 terrain tiles cut from the shipped maps' painted ground meshes, built on
the user's machine into Fab-format libraries (riftlord.fab.library.write_pieces).

    python3 -m riftlord.terrainkit list [--out DIR] [--json] [--events]
    python3 -m riftlord.terrainkit build MAP [--out DIR] [--jobs N] [--json] [--events]
    python3 -m riftlord.terrainkit delete MAP [--out DIR] [--json]

`sources` finds each map's ground meshes, `paint` reads their material's layers, `tiles`
cuts flat and ramp tiles on the shipped Floor_4x4 footprint, and `build` writes one
library per map ("Terrain: <mission>").
"""
