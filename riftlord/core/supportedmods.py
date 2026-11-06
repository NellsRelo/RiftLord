"""Supported Mods: the partner mods Rift Lord detects in the game install, and the
features each one gates.

A mod is detected by its content, not a file name: any pak in ~mods or LogicMods (subfolders
too) whose index holds an entry under the mod's folder, OMD/Content/Mods/<folder>/, carries
it (so a merged pak, e.g. Fear Itself carrying Crystal Defense, counts). A carrying pak's
index must also hold the package of every class a feature needs, and that package's header
must parse and export the class; a mod carried but failing either is "Detected (incompatible
version)" (the reason logged once) and that feature stays off. Only pak indexes are read,
cached by each pak's size and modification time.

A gated feature's pieces (New Object cards) show only while its mod is detected. A plan
that holds one while the mod is not is refused at build (build_problems). Nothing of a
supported mod is copied into a mission: the mission imports the installed classes.

    python3 -m riftlord.core.supportedmods            # detection on this machine
    RIFTLORD_SUPPORTED_PAKS=DIR                         # detect under DIR instead of the game's Paks
"""

import argparse
import json
import os
from collections import namedtuple

from riftlord.core import gamepath

#: Environment variable: a folder used in place of the game's OMD/Content/Paks (tests).
PAKS_ENV = 'RIFTLORD_SUPPORTED_PAKS'

#: One gated feature: id, label, the classes it needs ('/Game/...Package.Class'), and the
#: plan `added` items it covers as (kind, name); name None matches any item of the kind.
Feature = namedtuple('Feature', 'id label classes items')
#: One supported mod: id, display name, its content folder (OMD/Content/Mods/<folder>/, the
#: signature it is detected by), the file(s) it ships as relative to OMD/Content/Paks (named
#: in messages and stand-ins; not used to detect), its page link (None until there is one),
#: its gated features, and what Rift Lord output depends on it in the player's game without
#: being gated here (`runtime`).
Mod = namedtuple('Mod', 'id name folder files link features runtime')

#: Feature ids.
STAMINA_POTION = 'staminaPotion'
CRYSTAL = 'crystal'
TREASURE_CHEST = 'treasureChest'

MODS = (
    Mod('OrcsMustUI', 'OrcsMustUI', 'OrcsMustUI', ('LogicMods/OrcsMustUI.pak',), None,
        (Feature(STAMINA_POTION, 'Stamina Potion',
                 ('/Game/Mods/OrcsMustUI/BP_Pickup_StaminaPotion.BP_Pickup_StaminaPotion_C',),
                 (('kit', 'BP_Pickup_StaminaPotion_C'),)),),
        ('ProtoRegistry registers every mission table from the mission pak',
         'Custom Maps tab and custom campaigns (CustomMaps.yaml inside the pak)',
         'Scramble and Endless entries (Extends tables)',
         'Entitlement declarations (Extends tables)',
         'Game types (CustomMaps.yaml GameType)')),
    Mod('CrystalDefense', 'Crystal Defense', 'CrystalDefense', ('~mods/CrystalDefense_P.pak',), None,
        (Feature(CRYSTAL, 'Crystal',
                 ('/Game/Mods/CrystalDefense/BP_Crystal.BP_Crystal_C',),
                 (('crystal', None),)),),
        ()),
    Mod('TreasureChests', 'Treasure Chests', 'TreasureChests', ('LogicMods/TreasureChests.pak',), None,
        (Feature(TREASURE_CHEST, 'Treasure Chest',
                 ('/Game/Mods/TreasureChests/BP_TreasureChest.BP_TreasureChest_C',),
                 (('kit', 'BP_TreasureChest_C'),)),),
        ()),
)
BY_ID = {m.id: m for m in MODS}
FEATURES = {f.id: (m, f) for m in MODS for f in m.features}

#: Status values.
INSTALLED = 'installed'
MISSING = 'missing'
INCOMPATIBLE = 'incompatible'
LABELS = {INSTALLED: 'Detected', MISSING: 'Not detected',
          INCOMPATIBLE: 'Detected (incompatible version)'}


#: A folder of staged (not installed) paks read before the installed ones; None for none.
#: Set for one probe run by `riftlord.mapeditor.probe --mods-dir`.
STAGED_DIR = None


def paks_root():
    """The folder mod files are looked for in: $RIFTLORD_SUPPORTED_PAKS, else the game's
    OMD/Content/Paks, else None."""
    return gamepath.mod_paks()


def staged_paks():
    """Every `.pak` under STAGED_DIR (subfolders too), sorted; [] without one."""
    if not STAGED_DIR or not os.path.isdir(STAGED_DIR):
        return []
    out = []
    for base, dirs, names in os.walk(STAGED_DIR):
        dirs.sort()
        out += [os.path.join(base, n) for n in sorted(names) if n.endswith('.pak')]
    return out


#: The folders under paks_root() the game mounts mod paks from.
MOD_FOLDERS = ('~mods', 'LogicMods')


def _walk_paks(top):
    """Every `.pak` under `top` (subfolders too), sorted; [] without the folder."""
    out = []
    for base, dirs, names in os.walk(top):
        dirs.sort()
        out += [os.path.join(base, n) for n in sorted(names) if n.endswith('.pak')]
    return out


def candidate_paks():
    """Every pak a supported mod is looked for in: the staged ones (STAGED_DIR) first, then
    the installed ones in ~mods and LogicMods of paks_root(), an installed pak of a staged
    pak's file name left out (the staged one replaces it)."""
    staged = staged_paks()
    names = {os.path.basename(p) for p in staged}
    root = paks_root()
    installed = []
    for folder in MOD_FOLDERS if root else ():
        installed += [p for p in _walk_paks(os.path.join(root, folder))
                      if os.path.basename(p) not in names]
    return staged + installed


def shown_path(full):
    """A pak's path as shown: relative to paks_root() ('~mods/X_P.pak'), else its file name."""
    root = paks_root()
    if root and os.path.abspath(full).startswith(os.path.join(os.path.abspath(root), '')):
        return os.path.relpath(full, root).replace(os.sep, '/')
    return os.path.basename(full)


def package_entry(class_path):
    """'/Game/X/Y.Y_C' -> 'OMD/Content/X/Y' (the package's entry stem in a pak)."""
    package = class_path.split('.', 1)[0]
    return 'OMD/Content' + package[len('/Game'):]


#: {path: ((size, mtime_ns), frozenset of inner names or None, frozenset of Mods folders)}
_INDEX = {}
#: An inner name's mod folder: 'OMD/Content/Mods/<folder>/...'.
_MODS_PREFIX = 'OMD/Content/Mods/'


def _index(path):
    """The inner names of the pak at `path` (frozenset), None when it is unreadable, or
    False when there is no file. Read again only when its size or time changes."""
    try:
        st = os.stat(path)
    except OSError:
        _INDEX.pop(path, None)
        return False
    if not os.path.isfile(path):
        return False
    stamp = (st.st_size, st.st_mtime_ns)
    hit = _INDEX.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    from riftlord.core import paksource
    try:
        names = frozenset(paksource.names(path))
    except (Exception, SystemExit):                  # noqa: BLE001
        names = None
    folders = frozenset(n[len(_MODS_PREFIX):].split('/', 1)[0] for n in names or ()
                        if n.startswith(_MODS_PREFIX) and '/' in n[len(_MODS_PREFIX):])
    _INDEX[path] = (stamp, names, folders)
    return names


def carriers(mod):
    """[path] of the candidate paks (candidate_paks order) whose index holds an entry under
    the mod's folder."""
    out = []
    for path in candidate_paks():
        if _index(path) and mod.folder in _INDEX[path][2]:
            out.append(path)
    return out


#: {(path, (size, mtime_ns), class path): None, or why the class package does not parse}
_PARSED = {}


def _parse_problem(path, class_path):
    """None when the class package in the pak at `path` parses (its summary, name, import
    and export tables) and exports the class, else the reason."""
    import struct
    from riftlord.core import paksource
    from riftlord.formats.ue.uassetio import Package
    leaf = class_path.rsplit('.', 1)[-1]
    try:
        pkg = Package(paksource.read(path, package_entry(class_path) + '.uasset'))
        names = [n for n, _h in pkg.names]
        for e in pkg.exports:
            at = struct.unpack_from('<i', e['meta'], 16)[0]
            if 0 <= at < len(names) and names[at] == leaf:
                return None
    except (Exception, SystemExit) as exc:           # noqa: BLE001
        return 'does not parse: %s' % (str(exc) or type(exc).__name__)
    return 'exports no %s' % leaf


def _class_problem(path, class_path):
    """_parse_problem, cached by the pak's size and time; a problem is logged once."""
    key = (path, _INDEX[path][0], class_path)
    if key not in _PARSED:
        _PARSED[key] = _parse_problem(path, class_path)
        if _PARSED[key]:
            from riftlord.core import console
            console.warn('Supported Mods: %s in %s %s' % (class_path, path, _PARSED[key]),
                         console.SERVER)
    return _PARSED[key]


def detect_mod(mod):
    """{id, name, link, status, label, pak, files: [{path, full, present}], missing: [class,
    or 'class (reason)'], features: {feature id: on}} of one mod. `files` are the paks
    carrying it, the one its classes are read from (`pak`) first; [] when not detected."""
    held = [(p, _INDEX[p][1]) for p in carriers(mod)]
    present = bool(held)
    chosen, missing, features = None, [], {}
    for feat in mod.features:
        lacks = []
        for c in feat.classes:
            stem = package_entry(c)
            have = [p for p, names in held
                    if stem + '.uasset' in names and stem + '.uexp' in names]
            good = next((p for p in have if not _class_problem(p, c)), None)
            if good:
                chosen = chosen or good
            elif have:
                lacks.append('%s (%s)' % (c, _class_problem(have[0], c)))
            else:
                lacks.append(c)
        missing += lacks
        features[feat.id] = present and not lacks
    if held and not chosen:
        chosen = held[0][0]
    if not present:
        status = MISSING
    elif missing:
        status = INCOMPATIBLE
    else:
        status = INSTALLED
    order = ([chosen] if chosen else []) + [p for p, _n in held if p != chosen]
    return {'id': mod.id, 'name': mod.name, 'link': mod.link, 'status': status,
            'label': LABELS[status], 'pak': chosen,
            'files': [{'path': shown_path(p), 'full': p, 'present': True} for p in order],
            'missing': missing if present else [],
            'features': features}


def detect():
    """[detect_mod] of every supported mod, in MODS order."""
    return [detect_mod(m) for m in MODS]


def features():
    """{feature id: on} over every supported mod."""
    out = {}
    for row in detect():
        out.update(row['features'])
    return out


def available(feature_id):
    """Whether one feature's mod is detected with the feature's classes."""
    mod, _feat = FEATURES[feature_id]
    return detect_mod(mod)['features'][feature_id]


def file_path(mod_id):
    """The pak a mod's classes are read from (a carrying pak), or None when not detected."""
    return detect_mod(BY_ID[mod_id])['pak']


def paks_of(mod_id):
    """[path] of every pak carrying a mod (carriers)."""
    return carriers(BY_ID[mod_id])


def mod_file(feature_id):
    """The pak a feature's mod is read from, or None when the feature is off."""
    mod, _feat = FEATURES[feature_id]
    return file_path(mod.id) if available(feature_id) else None


def holder(inner):
    """The path of the pak whose index holds `inner` ('OMD/Content/...'): a staged pak
    (STAGED_DIR) first, then the paks carrying a supported mod; None when none does."""
    for full in staged_paks():
        got = _index(full)
        if got and inner in got:
            return full
    for mod in MODS:
        for full in carriers(mod):
            if inner in _INDEX[full][1]:
                return full
    return None


def feature_of(item):
    """The feature id a plan `added` item needs, or None."""
    if not isinstance(item, dict):
        return None
    for fid, (_mod, feat) in FEATURES.items():
        for kind, name in feat.items:
            if item.get('kind') == kind and (name is None or item.get('name') == name):
                return fid
    return None


def plan_features(plan):
    """{feature id: count} of the plan's added items that need a supported mod."""
    out = {}
    for item in (plan or {}).get('added') or []:
        fid = feature_of(item)
        if fid:
            out[fid] = out.get(fid, 0) + 1
    return out


def required_mods(plan):
    """Display names of the supported mods a plan's pieces need, in MODS order."""
    used = plan_features(plan)
    return [m.name for m in MODS if any(f.id in used for f in m.features)]


def build_problems(plan):
    """[text]: one line per supported mod a plan's pieces need that is not detected (or
    lacks a needed class). Detection is read afresh."""
    used = plan_features(plan)
    out = []
    for mod in MODS:
        need = [f for f in mod.features if f.id in used]
        if not need:
            continue
        row = detect_mod(mod)
        off = [f for f in need if not row['features'][f.id]]
        if not off:
            continue
        pieces = ', '.join('%s x%d' % (f.label, used[f.id]) for f in off)
        if row['status'] == MISSING:
            out.append('%s is not installed (%s needs %s). Install it, or remove those '
                       'pieces.' % (mod.name, pieces, ' or '.join(mod.files)))
        else:
            print('%s lacks %s' % (mod.name, ', '.join(row['missing'])))
            out.append('%s is an incompatible version (%s needs a version that works with '
                       'Rift Lord). Update it, or remove those pieces.' % (mod.name, pieces))
    return out


def note_build(plan, report):
    """Add the supported mods a mission's pieces need to a build report's `requires`."""
    names = required_mods(plan)
    if names:
        report['requires'] = sorted(set(report.get('requires') or []) | set(names))


def state():
    """What Settings > Supported Mods shows: {root, override, mods: [detect_mod of each
    detected mod], features: {id: on}}."""
    rows = detect()
    feats = {}
    for row in rows:
        feats.update(row['features'])
    return {'root': paks_root(), 'override': bool(os.environ.get(PAKS_ENV)),
            'mods': [r for r in rows if r['status'] != MISSING], 'features': feats}


def open_link(mod_id):
    """Open a supported mod's page in the system browser. SystemExit when it has none."""
    mod = BY_ID.get(mod_id)
    if not mod or not mod.link:
        raise SystemExit('%s has no page' % (mod.name if mod else mod_id))
    import webbrowser
    webbrowser.open(mod.link)
    return {'opened': mod.link}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args(argv)
    got = state()
    got['mods'] = detect()
    if args.json:
        print(json.dumps(got, indent=1))
        return 0
    print('Paks folder: %s%s' % (got['root'], ' (%s)' % PAKS_ENV if got['override'] else ''))
    for row in got['mods']:
        print('%-16s %-34s %s' % (row['name'], row['label'],
                                  ', '.join(f['path'] for f in row['files'])))
        for c in row['missing']:
            print('    missing %s' % c)
        for fid, on in row['features'].items():
            print('    %-16s %s' % (fid, 'on' if on else 'off'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
