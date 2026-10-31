"""A mission's own hub portal picture.

A MissionProtos row's `PortalDisplayClass` names the hub gate shown for the selected
mission, `BP_UI_Portal_<map>`: a data-only child of `BP_UI_Master_Portal` that sets
`Mesh_LevelPortalImage`'s material to `Portal_<map>_INST`, whose `Texture` parameter is
`IMG_Portal_<map>`, a 1024 x 1024 single-mip picture. The gate mesh shows an arch of
that square (PORTAL_ARCH, measured from Mesh_LevelPortal's UVs).

`build` copies the source map's gate and material into the mod and points the copy's
`Texture` at the mission's picture, written as a copy of PICTURE_DONOR. `thumbnail`
writes the same picture as the row's mission-list thumbnail.
"""

import struct

from riftlord.build.world import loadscreen
from riftlord.build.minimap import mapdata
from riftlord.build import modtable
from riftlord.formats.ue.uassetio import Package

#: The texture a portal picture is written over: 1024 x 1024 DXT1, one mip.
PICTURE_DONOR = '/Game/Maps/Campaign/Scenario_09/Images/IMG_Portal_Scenario_09'
#: Width:height of the portal picture.
PICTURE_ASPECT = (1, 1)
#: The texture a mission-list thumbnail is written over (the row's ThumbnailTexture,
#: read by MissionListEntryProto_UMG and OrcsMustUI's tabs): 256 x 144 DXT1, one mip.
THUMBNAIL_DONOR = '/Game/Maps/Campaign/Scenario_09/Images/ICON_MapThumbnail_Scenario_09'
#: Width:height of the thumbnail.
THUMBNAIL_ASPECT = (16, 9)
#: The material parameter holding the picture.
TEXTURE_PARAMETER = 'Texture'
#: Outline of the part of the picture the gate shows, as (u, v) fractions, v down:
#: the convex hull of Mesh_LevelPortal's UVs.
PORTAL_ARCH = [(0.126, 0.32), (0.134, 0.223), (0.163, 0.166), (0.237, 0.086),
               (0.321, 0.025), (0.438, 0.0), (0.561, 0.0), (0.684, 0.022), (0.77, 0.089),
               (0.843, 0.172), (0.867, 0.249), (0.877, 0.351), (0.874, 0.467),
               (0.855, 0.999), (0.807, 1.0), (0.146, 1.0), (0.135, 0.658), (0.129, 0.437)]


def _load(path):
    files = mapdata._fetch(path)
    with open(files['.uasset'], 'rb') as fh:
        ua = fh.read()
    with open(files['.uexp'], 'rb') as fh:
        ux = fh.read()
    pkg = Package(ua, ux)
    pkg.load_payloads()
    return pkg


def _material_of(pkg):
    """The material instance package a portal Blueprint names, or None."""
    names = [s for s, _h in pkg.names]
    mats = [n for n in names if n.startswith('/Game/') and n.endswith('_INST')]
    return mats[0] if len(mats) == 1 else None


def _texture_of(pkg):
    """(package path, object name) of the material's TEXTURE_PARAMETER texture."""
    from riftlord.core import uetags
    from riftlord.build import levelbuild
    names = [s for s, _h in pkg.names]
    props, _end = uetags.read(pkg.exports[0]['data'], names, 0)
    for p in props:
        if p.name != 'TextureParameterValues':
            continue
        for element in p.value[2]:
            info = next((q for q in element if q.name == 'ParameterInfo'), None)
            value = next((q for q in element if q.name == 'ParameterValue'), None)
            name = next((q.value for q in (info.value if info else [])
                         if q.name == 'Name'), None)
            if name == TEXTURE_PARAMETER and value is not None and value.value < 0:
                obj = levelbuild.resolve(pkg, value.value)[1]
                outer = struct.unpack_from('<i', pkg.imports[-value.value - 1], 16)[0]
                return levelbuild.resolve(pkg, outer)[1], obj
    return None


def picture_texture(image_path, crop, new_path):
    """((uasset, uexp), report): the cropped picture over a copy of PICTURE_DONOR."""
    return loadscreen.picture_texture(image_path, crop, new_path, donor=PICTURE_DONOR,
                                      aspect=PICTURE_ASPECT)


def thumbnail_texture(image_path, crop, new_path):
    """((uasset, uexp), report): the mission-list thumbnail, the THUMBNAIL_ASPECT band
    across the middle of the portal crop, over a copy of THUMBNAIL_DONOR."""
    return loadscreen.picture_texture(image_path, crop, new_path, donor=THUMBNAIL_DONOR,
                                      aspect=THUMBNAIL_ASPECT)


def thumbnail(root, mod, picture):
    """({package path: (uasset, uexp)}, texture object path, report) for the row's
    ThumbnailTexture made from the portal picture."""
    path = '%s/%s_Thumbnail' % (root, mod)
    blobs, how = thumbnail_texture(picture['path'], picture['crop'], path)
    return {path: blobs}, '%s.%s' % (path, path.rsplit('/', 1)[-1]), 'thumbnail %s' % how


def build(portal_class, root, mod, picture):
    """(files, soft class path, report) or (None, None, reason).

    `portal_class` is the row's PortalDisplayClass (`/Game/.../BP.BP_C`); `picture`
    is {'path', 'crop'}. `files` is {package path: (uasset, uexp)}."""
    if not picture:
        return None, None, 'no portal picture'
    if not portal_class or portal_class == 'None':
        return None, None, 'the source row names no hub portal'
    bp_path = portal_class.split('.', 1)[0]
    bp = _load(bp_path)
    mat_path = _material_of(bp)
    if mat_path is None:
        return None, None, '%s names no single material' % bp_path.rsplit('/', 1)[-1]
    mat = _load(mat_path)
    tex = _texture_of(mat)
    if tex is None:
        return None, None, '%s has no %s texture' % (mat_path.rsplit('/', 1)[-1],
                                                    TEXTURE_PARAMETER)
    new_bp = '%s/%s_Portal' % (root, mod)
    new_mat = '%s/%s_PortalMat' % (root, mod)
    new_tex = '%s/%s_PortalPicture' % (root, mod)
    files = {}
    files[new_tex], how = picture_texture(picture['path'], picture['crop'], new_tex)
    mat_object = mat_path.rsplit('/', 1)[-1]
    modtable.retarget(mat, exact={mat_path: new_mat, mat_object: new_mat.rsplit('/', 1)[-1],
                                  tex[0]: new_tex, tex[1]: new_tex.rsplit('/', 1)[-1]})
    files[new_mat] = mat.write()
    bp_object = bp_path.rsplit('/', 1)[-1]
    modtable.retarget(bp, exact={bp_path: new_bp, mat_path: new_mat,
                                 mat_object: new_mat.rsplit('/', 1)[-1]},
                      substring=[(bp_object, new_bp.rsplit('/', 1)[-1])])
    files[new_bp] = bp.write()
    leaf = new_bp.rsplit('/', 1)[-1]
    return files, '%s.%s_C' % (new_bp, leaf), 'hub portal from %s; picture %s' % (
        bp_object, how)
