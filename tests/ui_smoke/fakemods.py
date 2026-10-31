"""Stand-in supported-mod paks for the `supportedmods` UI smoke case and
tests/offline/test_supportedmods.py: under a Paks folder, each mod's file (supportedmods.MODS)
written as a small pak in one of these states:

* installed     each feature class's package, real and parseable: a shipped Blueprint
                package (DONOR) renamed to the class (the game's installed mods are never
                read)
* corrupt       the class packages listed in the index, their bytes not a package
* incompatible  no class packages at all
* missing       no file

and merged(): one pak under another name carrying several mods' installed content (as the
merged Fear Itself pak carries Crystal Defense), detected by content.

    python3 tests/ui_smoke/fakemods.py DIR OrcsMustUI=installed CrystalDefense=missing \\
        TreasureChests=corrupt
    python3 tests/ui_smoke/fakemods.py DIR merged:~mods/Merged_P.pak=CrystalDefense,TreasureChests
    RIFTLORD_SUPPORTED_PAKS=DIR python3 -m riftlord ...
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from riftlord.core import paksource  # noqa: E402
from riftlord.core import supportedmods  # noqa: E402

STATES = ('installed', 'corrupt', 'incompatible', 'missing')
#: An entry every stand-in pak holds, so an incompatible one is not empty.
MARKER = 'OMD/Content/Mods/%s/FakeMarker.uasset'
#: The shipped Blueprint package renamed to a class no installed mod pak gives.
DONOR = '/Game/Pickups/BP/BP_Pickup_Coin'


def renamed_package(class_path):
    """(uasset, uexp, source): DONOR's package with its package path, package name, class
    and default object names changed to the class's."""
    from riftlord.core import gamefiles
    from riftlord.formats.ue.uassetio import Package, fname_hashes
    pkg = Package(*gamefiles.package(DONOR))
    new_path = class_path.split('.', 1)[0]
    old_leaf, new_leaf = DONOR.rsplit('/', 1)[-1], new_path.rsplit('/', 1)[-1]
    rename = {DONOR: new_path, old_leaf: new_leaf, old_leaf + '_C': new_leaf + '_C',
              'Default__%s_C' % old_leaf: 'Default__%s_C' % new_leaf}
    pkg.names = [(rename[s], fname_hashes(rename[s])) if s in rename else (s, h)
                 for s, h in pkg.names]
    uasset, uexp = pkg.write()
    return uasset, uexp, DONOR


def class_package(class_path):
    """(uasset, uexp, source) of a parseable package exporting the class: DONOR renamed (never
    the game's installed mods)."""
    return renamed_package(class_path)


def write(root, mod_id, state):
    """Put mod `mod_id` in `state` under Paks folder `root`. Returns its file path."""
    if state not in STATES:
        raise SystemExit('state must be one of %s' % ', '.join(STATES))
    mod = supportedmods.BY_ID[mod_id]
    path = os.path.join(root, *mod.files[0].split('/'))
    if state == 'missing':
        if os.path.exists(path):
            os.remove(path)
        return path
    return _write(path, mod_files(mod_id, state))


def mod_files(mod_id, state):
    """{pak entry: bytes} of mod `mod_id` in `state` (not 'missing')."""
    mod = supportedmods.BY_ID[mod_id]
    files = {MARKER % mod.folder: b'fake'}
    for feat in mod.features:
        for cls in feat.classes:
            stem = supportedmods.package_entry(cls)
            if state == 'installed':
                files[stem + '.uasset'], files[stem + '.uexp'], _src = class_package(cls)
            elif state == 'corrupt':
                files[stem + '.uasset'] = b'not a package'
                files[stem + '.uexp'] = b'not a package'
    return files


def merged(root, rel, mod_ids):
    """One pak at `rel` under Paks folder `root` carrying each of `mod_ids` installed; the
    mods' own files removed. Returns its path."""
    files = {}
    for mod_id in mod_ids:
        write(root, mod_id, 'missing')
        files.update(mod_files(mod_id, 'installed'))
    return _write(os.path.join(root, *rel.split('/')), files)


def _write(path, files):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.part'
    paksource.write_pak(tmp, files)
    os.replace(tmp, path)
    return path


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2:
        raise SystemExit(__doc__)
    root = argv[0]
    for spec in argv[1:]:
        mod_id, _eq, state = spec.partition('=')
        if mod_id.startswith('merged:'):
            print('merged %s %s' % (state, merged(root, mod_id[len('merged:'):],
                                                  state.split(','))))
            continue
        print('%s %s %s' % (mod_id, state, write(root, mod_id, state)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
