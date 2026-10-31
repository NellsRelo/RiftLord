# MS-NRBF Reader

Reads Orcs Must Die 2's vForge `.Layer` files, which are .NET **BinaryFormatter**
streams (MS-NRBF). This is the front end of the OMD2 → OMD3 converter: the Layer
files are where the authoritative level layout lives.

## Files

| File | What it is |
|---|---|
| `nrbf.py` | The format reader. No game knowledge — records in, object graph out. |
| `clr.py` | Helpers for the .NET types vForge serialises (ArrayList, Vector3F, dynamic properties). |
| `trapgrids.py` | Extracts trap-placement volumes to JSON. |
| `dump.py` | Explorer: class census, entity listing, or one class's members. |
| `validate.py` | Renders grids top-down next to the shipped minimap. |

## Quick start

```sh
python3 trapgrids.py --list                        # every scene
python3 trapgrids.py --scene TheEdge               # summary
python3 trapgrids.py --scene TheEdge --out e.json  # full JSON

python3 dump.py --scene TheEdge --layer 'Main layer.Layer' --entities
python3 dump.py --scene TheEdge --class SaberEditorPlugin.TrapPlacementVolumeShape

python3 validate.py --scene TheEdge --types Floor,Wall,Ceiling --out check.png
```

Library use:

```python
from nrbf import parse
import clr

graph = parse('Trap Grid.Layer')
for shape in graph.by_class('SaberEditorPlugin.TrapPlacementVolumeShape'):
    props = clr.dynamic_properties(shape)
    print(shape['_shapeName'], props['mPlacementType'], props['mValidSquares'],
          clr.position(shape))
```

`parse()` accepts a path or a `bytes`, so Layer files can be read straight out of
`data.zip` without extracting.

## Status

**All 397 `.Layer` files across all 50 scenes parse with zero failures**, yielding
**6,405 trap-placement volumes** (Wall 4,286, Floor 1,257, Ceiling 862).

Every volume passes both internal cross-checks — the mask is exactly `W×H`
characters, and `mSize` equals `(W × 100, H × 100)`. That is what establishes
OMD2's **100-unit trap cell**, from named fields rather than a byte offset. OMD3's
cell is 300, so the port scale is exactly **3.0**.

This replaces an earlier byte-offset heuristic that only reached about half the
volumes.

## Two things worth knowing

**`_position` is absolute.** vForge stores world positions on shapes; a
`Group3DShape`'s own position is the group's pivot handle and must **not** be added
to its children. Verified on TheEdge: "Wall Trap Placement50" is at x=13351.8 while
its parent group "Trap Grid" is at x=14600; the scene's "minimap box" spans
x 9325..13275, so the raw value is in frame and the sum (27951) is nowhere near the
level. An earlier version of `clr.py` accumulated the chain and was wrong by a
constant offset in every scene.

**`.Layer.backup` files duplicate the `.Layer` beside them.** `scene_layers()` drops
them; anything counting volumes another way will double.

## What is not yet verified

`validate.py` aligns the grid render to the scene's "minimap box" shape, but that
box is non-square (TheEdge: 3950 × 4625) while `minimap01.dds` is 512 × 512, so the
texture's true world mapping is still unknown — possibly a different framing, an
aspect fit, or a rotation. The overlay is therefore **indicative, not a proof of
handedness**. The Edge also has a `Terrain_NoMiniMap` zone, so some of the level is
deliberately off-minimap and volumes are expected outside the frame.

**This has since been settled a better way.** `../VZone Reader/` reads the cooked
`.vzone` geometry, and `../crosscheck.py` measures the distance from every trap volume
to the nearest static-mesh bounding box. Across all 50 scenes the median distance is
**0.0** and **100%** of volumes land within 300 units — two different formats, written
by two different tools, agreeing on one world space. Running the check with
`--offset 14600` reproduces the old `clr.position()` bug and drops it to 0%, which is
what shows the test can actually fail.

The minimap overlay in `validate.py` is still only indicative, and is now just a
visual aid rather than the validation of record.

## Format notes

MS-NRBF is a record stream. The records that matter here are `ClassWithMembersAndTypes`
(class metadata plus values), `ClassWithId` (reuses earlier metadata by id),
`MemberReference` (forward reference, resolved after the stream is read),
`BinaryObjectString`, the three `ArraySingle*` forms and `BinaryArray`. Strings carry a
7-bit encoded length prefix. Null runs (`ObjectNullMultiple`) fill several members or
array slots from one record, which is the easiest part of the format to get wrong.

vForge writes `vForge, Version=8.1.1.5161` and `VisionEditorPlugin`/`SaberEditorPlugin`
assemblies. Gameplay objects appear as shapes with an `_entityClass`
(`BTrapPlacementVolume`, `BTrigger`, `BPlayerBlocker`, `BMinecartSpawner`,
`BMinecartSwitchVolume`, `BReverbVolume`, `BSoundObject`, `BEntity`) and a dynamic
property bag reached via `clr.dynamic_properties()`.

Useful dynamic properties seen so far:

- `mValidSquares` — `"W|H|<bitmask>"`, the trap grid
- `mPlacementType` — `Floor` / `Wall` / `Ceiling`
- `mSize` — volume extent, `(W × 100, H × 100, 0)`
- `mProtoName` — `playerstart`, `onslaught`, `waypoint`, …
- `mWaypointGroupName` / `mNextWaypoints` — the AI path graph; `mNextWaypoints` is a
  comma-separated list of waypoint ids, so it branches. Across all scenes the fan-out
  distribution is `{1: 1720, 2: 90, 3: 19, 4: 10}` — **OMD2 branches up to 4 ways,
  where OMD3's authored paths never exceed 2.**
- `mTextValue` on an `onslaught` entity names the level's wave set (`"TheEdge"`)
