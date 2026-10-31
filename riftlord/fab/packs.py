# Generated from OMD2-Converter/thirdparty/fabconvert/packs.py by thirdparty/fabconvert/port_riftlord.py; edit the source.
"""Known Fab packs: vault folder -> library id, title and scale (user decisions 2026-10-14).

Scale: Docks, Eastern Village, Egypt, Medieval Village x1.5 (props included; authors rescale per
placement); Snowy and Orasot x1.0. The SCALE.md study measured
why (s_char per pack). Trap-grid actors always stay at scale 1.0.
City of Brass x1.5 (user 2026-10-14): its 400 uu module then lands on 2 whole trap cells, as
Docks' does; x1.0 left 92% of its architecture off the half-cell lattice (P09 405 uu = 1.35
cells). The larger of the cell-fitting scales was chosen; trims stay off-module as they are in
the pack. City of Brass: Props follows, as it shares that world.
Every pack with a building kit is on a 400 uu module (200 halves), measured 2026-10-14 from the
inventories, so the same rule puts Sky Dungeon and Winter City at x1.5 too. Snowy and Orasot
have no building kit and keep their character scale, x1.0.
Second batch (2026-10-23, measured with survey-tools + scale-tools/scale_report.py, outputs in
/mnt/2tb_pro_a/tmp/fabnew): Stylized Castle is on a 300 uu module (walls 300, halves 150):
x2.0, the larger whole-cell scale (39% of pieces on cells against 33% at x1.0), nearest its
character scale 1.68; walls become 600 (2 cells) as in Docks, doors 586 (vault.choose agrees). Desert Castle is on a 100 uu module with 300 walls:
x1.5 puts every 100 on a half cell, doors 323 (OMD3's own human doors 324). Stylized Dungeon
fits no scale; x1.5 is its character scale (s_char 1.45, doors 318). Wild West is modelled
small (doors 90, barrels 47, s_char 3.6): x3.0 puts its 100 module on whole cells, doors 271.
Stylized Desert is nature plus a 200/100 ruins set: x1.5 puts the ruins on cells, as Desert
Castle beside it.
Third batch, measured by `fabconvert measure`: Modular Dungeon 2 is on OMD3's own 300 uu cell
(60% of pieces on whole cells at x1, doors 270): x1.0. Medieval Dungeon: x1.0 (best fit 24%,
doors 272). Modular Medieval Sewer Dungeon (UE 4.15) has no doorways or stairs to measure: x1.0.
Mediterranean Asset Pack (a zip of FBX + PNG, read by fbxpack) is modelled in real-world
centimetres from Blender (the portal 11.5 m, the brick pole 6 m) with no building module: x1.0.
"""

import os
import re

#: vault folder -> (PackId, title, scale, priority). PackId names /Game/Mods/Fab/<PackId>/.
PACKS = {
    'dsa60895b3c928aV1': ('Docks', 'Modular Medieval Docks', 1.5, 1),
    'CityofBra13001ef61fcV1': ('CityOfBrass', 'City of Brass: Environments', 1.5, 1),
    'Stylized84f706514444V1': ('EasternVillage', 'Stylized Eastern Village', 1.5, 2),
    'Stylized9ec755cb81bdV1': ('Egypt', 'Stylized Egypt', 1.5, 3),
    'Stylized76f408980eceV1': ('MedievalVillage', 'Stylized Medieval Village', 1.5, 3),
    'Stylized0ab6cb4dd19eV1': ('SkyDungeon', 'Stylized Sky Dungeon', 1.5, 3),
    'Stylized7d62d9588286V2': ('SnowyLandscape', 'Stylized Snowy Landscape', 1.0, 3),
    'Stylizedb73f559b7a9fV1': ('Orasot', 'Orasot Bundle', 1.0, 3),
    'CityofBr865496a22c15V1': ('CityOfBrassProps', 'City of Brass: Props', 1.5, 3),
    'Stylizedb63a1df96f9eV1': ('WinterCity', 'Stylized Winter City', 1.5, 4),
    'Stylizedeed9551e1b47V1': ('StylizedCastle', 'Stylized Castle', 2.0, 5),
    'DesertCa3432654395afV1': ('DesertCastle', 'Desert Castle', 1.5, 5),
    'Untitled0ffc0af7531cV1': ('StylizedDungeon', 'Stylized Dungeon Pack', 1.5, 5),
    'TheSapphe4f773674b53V1': ('WildWest', 'Stylized Wild West', 3.0, 5),
    'stylized552fe48b8933V1': ('StylizedDesert', 'Stylized Desert', 1.5, 5),
    'ModularM60895b3c928aV2': ('DocksUE5', 'Modular Medieval Docks (UE5 copy)', 1.5, 9),
    # third batch (2026-10-24), scales from `fabconvert measure` (vault.choose)
    'ModularDf4cbc05a0763V1': ('ModularDungeon2', 'Modular Dungeon 2', 1.0, 5),
    'Medieval9adb34ff933eV1': ('MedievalDungeon', 'Medieval Dungeon', 1.0, 5),
    'ModularMedievalSewer': ('SewerDungeon', 'Modular Medieval Sewer Dungeon', 1.0, 5),
    # loose model packs (FBX + images), keyed by their zip or folder name
    'mediterranean_asset_pack': ('Mediterranean', 'Mediterranean Asset Pack', 1.0, 5),
}
#: Scale for a pack not in the table (reported as a warning).
DEFAULT_SCALE = 1.0
#: Content folders that are never a pack's art.
NOT_ART = ('Collections', 'Developers', '__ExternalActors__', '__ExternalObjects__')


#: A loose model pack (FBX files and images, no Unreal packages): a folder holding .fbx files,
#: or a zip of one, extracted into the converter cache (imports/<zip stem>-<hash>).
LOOSE_MODEL_EXT = ('.fbx',)


def _has_models(path):
    return any(f.lower().endswith(LOOSE_MODEL_EXT) for _dp, _dn, fns in os.walk(path) for f in fns)


def _extract_zip(path):
    """The folder a zip of a loose pack is extracted to (once per zip content), members checked
    to stay inside it."""
    import hashlib
    import zipfile
    from riftlord.fab import library
    with open(path, 'rb') as fh:
        digest = hashlib.sha1(fh.read()).hexdigest()[:10]
    stem = re.sub(r'[^A-Za-z0-9_]', '_', os.path.splitext(os.path.basename(path))[0])
    dest = os.path.join(library.cache_root(), 'imports', '%s-%s' % (stem, digest))
    if not os.path.isdir(dest):
        tmp = dest + '.part'
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        with zipfile.ZipFile(path) as z:
            for m in z.namelist():
                target = os.path.abspath(os.path.join(tmp, m))
                if not target.startswith(os.path.abspath(tmp) + os.sep) and target != os.path.abspath(tmp):
                    raise ValueError('%s: member %r escapes the folder' % (path, m))
            z.extractall(tmp)
        os.replace(tmp, dest)
    return dest


def locate(path):
    """(vault folder name or None, Content dir, [pack root dirs]) for a vault pack folder, its
    data/Content folder, or one pack root under Content; for a loose model pack (a folder of
    .fbx files and images, or a zip of one), (its name, the folder, [the folder])."""
    path = os.path.abspath(path)
    if os.path.isfile(path) and path.lower().endswith('.zip'):
        folder = _extract_zip(path)
        return os.path.splitext(os.path.basename(path))[0], folder, [folder]
    if os.path.isdir(path) and not os.path.isdir(os.path.join(path, 'data', 'Content')) \
            and os.path.basename(path) != 'Content' and _has_models(path):
        return os.path.basename(path), path, [path]
    if os.path.isdir(os.path.join(path, 'data', 'Content')):
        content, item = os.path.join(path, 'data', 'Content'), os.path.basename(path)
    elif os.path.basename(path) == 'Content':
        content, item = path, os.path.basename(os.path.dirname(os.path.dirname(path)))
    elif os.path.basename(os.path.dirname(path)) == 'Content':
        content, item = os.path.dirname(path), os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(path))))
        return (item if item in PACKS else None), content, [path]
    else:
        raise FileNotFoundError('%s is not a vault pack folder (no data/Content)' % path)
    roots = sorted(os.path.join(content, d) for d in os.listdir(content)
                   if os.path.isdir(os.path.join(content, d)) and d not in NOT_ART)
    if not roots:
        raise FileNotFoundError('%s has no pack folder under Content' % content)
    return (item if item in PACKS else None), content, roots


def identity(item, roots):
    """{'id', 'title', 'scale', 'known'} of a pack."""
    if item in PACKS:
        pid, title, scale, _prio = PACKS[item]
        return {'id': pid, 'title': title, 'scale': scale, 'known': True, 'vault_item': item}
    if os.path.basename(os.path.dirname(roots[0])) == 'Content' or not item:
        name = os.path.basename(roots[0])                  # a vault pack: its Content folder
        pid = re.sub(r'[^A-Za-z0-9]', '', name)[:40] or 'Pack'
    else:
        name = item                                        # a loose pack: its folder or zip name
        pid = ''.join(w[:1].upper() + w[1:] for w in re.split(r'[^A-Za-z0-9]+', name) if w)[:40] or 'Pack'
    return {'id': pid, 'title': name, 'scale': DEFAULT_SCALE, 'known': False, 'vault_item': item}
