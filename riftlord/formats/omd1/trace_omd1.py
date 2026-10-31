"""Orcs Must Die! (OMD1) levels for the Trace tool (riftlord/trace.py).

OMD1 is Vision Engine 8.1, the same engine and file formats as OMD2, measured
2026-10-17 on the Steam install (`Orcs Must Die!`, AppID 102600):

- The game data is in seven plain zips beside the exe: `data.zip` (21 campaign scenes'
  Layers and most level meshes), `datademo.zip` (the three demo scenes -- Hallway, The
  Fork, Corner -- and all 256 `.prefab` files under `Prefabs/`), `datademoart.zip` and
  `dataart.zip` (character and prop art), `datadlc01.zip`/`datadlc02.zip` (content
  only) and `datadlc03.zip` (the five DLC scenes, `DLC020_DoubleTrouble` to
  `DLC024_TripleDown`, with their meshes). A scene's Layers, its prefabs and its meshes
  are routinely in DIFFERENT zips, so every lookup goes through one index of all of
  them (`Files`); the first zip in ZIP_ORDER holding a path wins.
- A scene is `scenes/<name>/Master/<...>.scene.Layers/*.Layer`, MS-NRBF exactly like
  OMD2's: `VisionEditorPlugin.Shapes.StaticMeshShape` (`_meshFile`, `_position`,
  `_orientation` (yaw, pitch, roll), `_scale`, `_bVisible`) and
  `VisionEditorPlugin.Shapes.PrefabShape` (`_fileName`, no children). The Bridge: 2,559
  mesh shapes of 93 meshes and 123 prefab placements of 14 prefabs in its Layers.
- `.prefab` is OMD2's XML (`<shape class="StaticMeshShape">` with `MeshFileName`,
  `Position`, `Orientation`, `Scaling`, nested `Group3DShape`/`PrefabShape`), read by
  omd2prefab.expand with this module's zip index.
- `.vmesh` reads with omd2vmesh.read (omd2model.read_vmsh; 6,333 of 6,333 OMD1 meshes
  parse).
- Vision is right-handed: placements are mirrored through Y into Unreal handedness as
  OMD2's are (trace.mirror_y). World units are taken as 1:1 with OMD3, as OMD2's are.
- What a scene draws is its shipping zone bake, read as OMD2's is
  (riftlord/trace_vision.py): every one of the 29 scenes has one. The Layers above are
  read only by the checker (`instances`) and for trap grids.

There are no trap-grid layers in OMD1 (placement comes from delve Areas), which the
Trace tool does not need.
"""

import functools
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

from riftlord.core import gamepath  # noqa: E402

#: OMD1's Steam folder.
GAME_FOLDER = 'Orcs Must Die!'
#: The zips, in the order a path is looked up; any other data*.zip follows.
ZIP_ORDER = ('data.zip', 'datadlc03.zip', 'datadlc01.zip', 'datadlc02.zip',
             'datademo.zip', 'datademoart.zip', 'dataart.zip')


def install():
    """OMD1's install folder, or None."""
    return gamepath.other_game(GAME_FOLDER)


def _key(name):
    return str(name).replace('\\', '/').lower()


def Files(folder):
    """Every file in OMD1's zips (trace_vision.Files), in ZIP_ORDER."""
    from riftlord.formats.vision import trace_vision
    names = [z for z in ZIP_ORDER if os.path.isfile(os.path.join(folder, z))]
    names += sorted(z for z in os.listdir(folder)
                    if z.lower().startswith('data') and z.lower().endswith('.zip')
                    and z not in names)
    return trace_vision.Files([os.path.join(folder, z) for z in names])


@functools.lru_cache(maxsize=1)
def files():
    folder = install()
    if not folder:
        raise SystemExit('Orcs Must Die! is not installed')
    return Files(folder)


def scenes():
    """OMD1 scene names: folders under scenes/ that hold Layers."""
    if not install():
        return []
    out = set()
    for n in files().names():
        if n.startswith('scenes/') and n.endswith('.Layer'):
            out.add(n.split('/')[1])
    return sorted(out)


def _layers(scene):
    prefix = 'scenes/%s/' % scene
    return sorted(n for n in files().names() if n.startswith(prefix) and n.endswith('.Layer'))


class Prefabs:
    """omd2prefab's library protocol over every OMD1 zip."""

    def __init__(self, fs):
        self.fs = fs
        self._cache = {}

    def contents(self, file_name, depth=0, seen=()):
        from riftlord.formats.omd2 import omd2prefab
        key = _key(file_name)
        if depth == 0 and key in self._cache:
            return self._cache[key]
        data = self.fs.read(key)
        got = omd2prefab.expand(data, self, depth, seen) if data else []
        if depth == 0:
            self._cache[key] = got
        return got


def instances(scene):
    """[(mesh key, 4x4 row-major list)] for every visible mesh one scene's Layers and
    prefabs place, in OMD1's own (right-handed) world (the checker's comparison)."""
    from riftlord.formats.vision import trace_vision
    fs = files()
    return trace_vision.editor_instances(fs, scene, _layers(scene), Prefabs(fs))


def models(scene):
    """({mesh: trace_vision model}, [(mesh, 4x4)]) in Unreal handedness (trace.py)."""
    from riftlord.mapeditor import trace
    from riftlord.formats.vision import trace_vision
    if scene not in scenes():
        raise SystemExit('no such OMD1 scene: %r' % (scene,))
    return trace_vision.read(files(), scene, trace.mirror_y)
