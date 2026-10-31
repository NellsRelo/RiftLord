"""The pictures a mission shows when it supplies none: its source level's own loading
screen screenshot and hub portal picture, as the Details dialog previews them.

A mission row is cloned from the source map's (makemod.mission_row_for), so a mission
without a picture of its own keeps that row's loading screen (loadscreen.screenshot_of
on its MissionLoadScreen widget) and hub gate (hubportal: PortalDisplayClass ->
material -> Texture). Base-campaign rows load on trap tips with no screenshot.

    python3 sourcepictures.py Scenario_05      # which textures, and their sizes
"""

import base64
import functools
import struct
import sys

#: Longest side of a preview sent to the dialog, in pixels.
PREVIEW_SIZE = {'picture': 960, 'portalPicture': 512}


def _soft_path(table, row, field):
    """A row's soft object path field as text, or None."""
    f = table.field(table.row(row), field) if table.row(row) else None
    if f is None or len(f.payload) < 4:
        return None
    at = struct.unpack_from('<i', f.payload, 0)[0]
    name = table.names[at] if 0 <= at < len(table.names) else None
    return name if name and name != 'None' else None


@functools.lru_cache(maxsize=64)
def textures(level):
    """{'picture': texture game path or None, 'portalPicture': ...} of a level's own row."""
    from riftlord.build.world import hubportal
    from riftlord.build.world import loadscreen
    from riftlord.build import makemod
    from riftlord.build import missionproto
    out = {'picture': None, 'portalPicture': None}
    row = makemod.mission_row_for(level) if level else None
    if not row:
        return out
    table = missionproto.read()
    widget = _soft_path(table, row, 'MissionLoadScreen')
    if widget:
        try:
            shot = loadscreen.screenshot_of(loadscreen._load(loadscreen.soft_class(widget)))
            out['picture'] = shot[0] if shot else None
        except (Exception, SystemExit):              # noqa: BLE001
            pass
    gate = _soft_path(table, row, 'PortalDisplayClass')
    if gate:
        try:
            mat = hubportal._material_of(hubportal._load(gate.split('.', 1)[0]))
            tex = hubportal._texture_of(hubportal._load(mat)) if mat else None
            out['portalPicture'] = tex[0] if tex else None
        except (Exception, SystemExit):              # noqa: BLE001
            pass
    return out


def view(level):
    """{'picture': PNG data URL or None, 'portalPicture': ..., 'level': level} for the
    Details dialog."""
    from riftlord.mapeditor import omd3serve
    out = {'level': level}
    for slot, path in textures(level).items():
        png = omd3serve.texture_png(path, PREVIEW_SIZE[slot]) if path else None
        out[slot] = ('data:image/png;base64,' + base64.b64encode(png).decode()) if png else None
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    for level in argv:
        print(level, textures(level))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
