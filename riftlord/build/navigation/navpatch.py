"""Patch an OMD2 NavPower navgraph into an OMD3 map, to test the version gate.

The question this answers: does OMD3's NavPower runtime accept a graph built by
OMD2's (older) NavPower builder? Both write the same 24-byte header with the same
format word (2), but a matching header version does not guarantee the node and edge
structures match.

The trick that keeps this cheap: OMD3 stores its navgraph as a raw buffer at the end
of the ``NavPowerNavigationData`` export, sized by the ``NavMeshBufferSize`` property.
If the replacement is written into a buffer **at least as large** and zero-padded to
exactly the original size, then nothing else in the package moves -- no export offset
surgery, no summary edits. NavPower reads the length from the graph's own header, so
the trailing padding is ignored.

    python3 navpatch.py --list
    python3 navpatch.py --source DLC001_DoubleDecker --target Scenario_18 --out patched/

The output is a .umap/.uexp pair to be packaged into a _P.pak.
"""

import argparse
import glob
import os
import struct
import sys
import threading
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.formats.ue.pak import Pak  # noqa: E402
from riftlord.formats.ue import ue  # noqa: E402
from riftlord.formats.ue.uheader import parse as parse_header  # noqa: E402
from riftlord.formats.ue.parseuasset import R  # noqa: E402
from riftlord.formats.ue import pakv4  # noqa: E402

from riftlord.core import gamepath  # noqa: E402

#: The OMD2 editor project, for the porting side. Optional -- the onslaught
#: tooling never opens it.
OMD2_ZIP = gamepath.other_game('Orcs Must Die 2', 'data.zip')

#: Resolved when this module loads, and settable afterwards by a caller that
#: knows better (the editor does, once the author has chosen an install).
PAK_DIR = gamepath.paks()


def mods_dir():
    """The ~mods folder whose paks load after the game's: under RIFTLORD_SUPPORTED_PAKS when
    that is set (a test Paks folder, as riftlord.core.supportedmods reads), else PAK_DIR's."""
    return os.path.join(os.environ.get('RIFTLORD_SUPPORTED_PAKS') or PAK_DIR or '', '~mods')


NAV_CLASS = 'NavPowerNavigationData'
HEADER_WORDS = 8            # the 24-byte NavGraph header is 6 words; 8 is a safe read
OMD2_WRAPPER = 8            # .path prefixes the graph with (total size, 0)
OMD3_FLAGS = 0x01000001     # OMD2 writes 0 here; every OMD3 graph writes this


def omd2_graphs(zip_path=OMD2_ZIP):
    """Every OMD2 .path, as {scene: (name, payload_bytes)} sorted by size."""
    out = []
    with zipfile.ZipFile(zip_path) as zf:
        for n in zf.namelist():
            if not n.endswith('.path'):
                continue
            data = zf.read(n)
            graph = data[OMD2_WRAPPER:]
            w = struct.unpack_from('<%dI' % HEADER_WORDS, graph, 0)
            if w[0] != 0 or w[1] != 2:
                continue
            out.append({
                'scene': os.path.basename(n)[:-len('.path')],
                'path': n,
                'graph': graph,
                'payload': w[2],
                'needs': w[2] + 24,
            })
    return sorted(out, key=lambda g: g['needs'])


#: The last `_index` result, with the (path, size, mtime) of every pak it read.
_INDEX_CACHE = {}


def _index():
    """`/Game/...` -> (pak path, entry name) for the installed paks. Kept until a pak is
    added, removed or rewritten: reading every index took 0.4 s and a build asked ~150 times."""
    if not PAK_DIR:
        return _read_index()
    paks = sorted(glob.glob(os.path.join(PAK_DIR, '*.pak')))
    key = []
    for p in paks:
        try:
            st = os.stat(p)
        except OSError:
            continue
        key.append((p, st.st_size, st.st_mtime_ns))
    key = tuple(key)
    cached = _INDEX_CACHE.get('entry')
    if cached is None or cached[0] != key:
        # key and index stored together: another request thread must never see the new
        # key before its index exists (KeyError: 'index' on the Onslaught Editor's first page)
        cached = (key, _read_index())
        _INDEX_CACHE['entry'] = cached
    return cached[1]


def _read_index():
    if not PAK_DIR:
        raise SystemExit(
            'Orcs Must Die 3 was not found. Set OMD3_INSTALL to the install '
            'folder, or choose it in the editor; %s remembers it.'
            % gamepath.settings_file())
    idx = {}
    for p in sorted(glob.glob(os.path.join(PAK_DIR, '*.pak'))):
        try:
            pk = Pak(p)
        except (Exception, SystemExit):
            continue
        for name in pk.files:
            rel = '/Game/' + name.split('Content/', 1)[-1].replace('//', '/')
            idx.setdefault(rel, (p, name))
    return idx


def pak_names(pak_path):
    """Everything a pak holds, `/Game/...` key -> (pak path, entry name).

    `pak.Pak` handles both the shipped v9+ paks with their encoded index and the v4 ones
    these tools write (measured: it opens `pakv4.write` output and reports version 4).
    The `pakv4` fallback is kept for a pak it cannot, since a tool that reads a custom
    mission is reading something it wrote.
    """
    if os.path.isdir(pak_path):
        # a loose build: the same cooked files a pak would hold, in a folder
        from riftlord.core import paksource
        names = paksource.names(pak_path)
    else:
        names = None
    try:
        if names is None:
            names = list(Pak(pak_path).files)
    except (Exception, SystemExit):                                # noqa: BLE001
        try:
            from riftlord.formats.ue import pakv4
            _mount, _version, entries = pakv4.read(pak_path)
            names = [str(n).replace('\\', '/') for n, _o, _s in entries]
        except (Exception, SystemExit):                            # noqa: BLE001
            # Neither reader recognised it, so say that rather than let a struct
            # error about buffer sizes reach whoever picked the file.
            raise SystemExit('%s is not a pak file'
                             % os.path.basename(pak_path))
    out = {}
    for name in names:
        rel = '/Game/' + name.split('Content/', 1)[-1].replace('//', '/')
        out[rel] = (pak_path, name)
    return out


def maps_in_pak(pak_path):
    """Maps a single pak holds, name -> package path without extension.

    Found by shape -- a `.umap` in a folder of the same name -- with no root test, so a
    pak that lays its folders out its own way still reads. Every other umap beside it is
    a sublevel.
    """
    found = {}
    for key in pak_names(pak_path):
        if not key.endswith('.umap'):
            continue
        stem = key[:-len('.umap')]
        parts = stem.split('/')
        if len(parts) >= 2 and parts[-2] == parts[-1]:
            found[parts[-1]] = stem
    return found


#: Paks opened by name rather than found in `~mods`, oldest first. They are read after
#: the installed ones, so an opened pak wins where both hold the same asset.
_OPENED = []


def open_pak(pak_path):
    """Read a pak from anywhere on disk, and return the maps in it.

    A mission that is not installed -- one just built, or one somebody sent -- is a pak
    sitting in a folder. Registering it here puts its assets in `live_index`, so the map
    opens exactly like an installed one and nothing has to be copied into `~mods` first.
    """
    path = os.path.abspath(pak_path)
    if not os.path.exists(path):
        raise SystemExit('no such file: %s' % path)
    found = maps_in_pak(path)
    if not found:
        raise SystemExit('%s holds no map' % os.path.basename(path))
    with _OPEN_LOCK:
        _HOLDS[path] = _HOLDS.get(path, 0) + 1
        # one assignment, so a reader in another thread never sees the pak missing
        _OPENED[:] = [p for p in _OPENED if p != path] + [path]
    return found


def close_pak(pak_path=None, every=False):
    """Undo one open_pak of a pak, or forget it outright (`every`), or forget all of
    them. A pak stays open while any opener still holds it."""
    with _OPEN_LOCK:
        if pak_path is None:
            _OPENED.clear()
            _HOLDS.clear()
            return
        path = os.path.abspath(pak_path)
        _HOLDS[path] = 0 if every else _HOLDS.get(path, 1) - 1
        if _HOLDS[path] <= 0:
            _HOLDS.pop(path, None)
            _OPENED[:] = [p for p in _OPENED if p != path]


def is_opened(pak_path):
    return os.path.abspath(pak_path) in _OPENED


#: How many open_pak calls hold each opened pak.
_HOLDS = {}
_OPEN_LOCK = __import__('threading').Lock()


def opened_paks():
    """The paks opened by name, oldest first."""
    return list(_OPENED)


def opened_maps():
    """{map name: pak path} for every map an opened pak brought in."""
    out = {}
    for path in list(_OPENED):
        try:
            for name in maps_in_pak(path):
                out[name] = path
        except (Exception, SystemExit):                            # noqa: BLE001
            continue
    return out


def live_index(exclude=()):
    """The assets the GAME sees: base paks, then ~mods overriding them.

    Mod paks load after the base and later ones win, so a tool that patches a
    shipped table must start from whatever is installed rather than from stock
    -- otherwise it silently reverts every other mod's changes to that file.
    `exclude` skips paks by basename, so a tool does not merge with its own
    previous output.
    """
    idx = dict(_index())
    mods = sorted(glob.glob(os.path.join(mods_dir(), '*.pak')))
    for p in mods + list(_OPENED):
        if os.path.basename(p) in exclude:
            continue
        try:
            entries = pak_names(p)
        except (Exception, SystemExit):                            # noqa: BLE001
            continue
        idx.update(entries)                     # later pak wins
    return idx


def overrides(exclude=()):
    """{asset path: pak basename} for everything a mod currently replaces."""
    base = _index()
    out = {}
    for path, (p, _n) in live_index(exclude).items():
        if path in base and base[path][0] != p:
            out[path] = os.path.basename(p)
    return out


#: Package roots holding playable maps: the base game and the three DLC
#: campaigns, plus the standalone scenarios.
MAP_ROOTS = ('/Game/Maps/Campaign/', '/Game/Maps/CampaignDLC/',
             '/Game/Maps/CampaignDLC_Ice/', '/Game/Maps/CampaignDLC_Sand/',
             '/Game/Maps/OtherScenarios/')

#: Installed mods keep their maps here, at a depth the shipped roots do not match:
#: `/Game/Mods/<mod>/Maps/<map>/<map>.umap`. Found by shape rather than by root so a
#: mod can lay its folders out however it likes.
MOD_ROOT = '/Game/Mods/'

_MAPS = None


def mod_maps():
    """Maps installed by mods, name -> package path.

    Every pak in `~mods` is scanned by shape rather than by root: `/Game/Mods/` is only
    where these tools put a map, and a mod is free to add one anywhere -- Endless War
    adds thirty under the base game's own campaign folder, which a root test misses.

    A pak that merely *replaces* a shipped map adds nothing to the list, so North Wing
    stays under its own campaign instead of moving to the mod group.
    """
    shipped = maps(include_mods=False)
    found = {}
    for path in sorted(glob.glob(os.path.join(mods_dir(), '*.pak'))):
        try:
            for name, stem in maps_in_pak(path).items():
                if name not in shipped:
                    found[name] = stem
        except (Exception, SystemExit):                            # noqa: BLE001
            continue
    return found


def maps(include_mods=True):
    """Every playable map, name -> package path without extension.

    A map's own package sits in a folder of the same name; every other umap
    beside it is a sublevel (Castle, Environment, Lighting, Audio). Installed mods are
    included, so a custom mission can be opened and edited like a shipped one.
    """
    global _MAPS
    if _MAPS is None:
        idx = _index()
        found = {}
        for root in MAP_ROOTS:
            for key in idx:
                if not key.startswith(root) or not key.endswith('.umap'):
                    continue
                name = key[len(root):].split('/')[0]
                path = '%s%s/%s' % (root, name, name)
                if path + '.umap' in idx:
                    found[name] = path
        _MAPS = dict(sorted(found.items()))
    if not include_mods:
        return _MAPS
    # Merged rather than cached with the shipped ones: a mod can be installed or removed
    # while the tool is open, and a stale list would offer a map that is no longer there.
    out = dict(_MAPS)
    try:
        out.update(mod_maps())
    except (Exception, SystemExit):                                # noqa: BLE001
        pass
    for path in list(_OPENED):
        try:
            out.update(maps_in_pak(path))
        except (Exception, SystemExit):                            # noqa: BLE001
            continue
    return dict(sorted(out.items()))


def map_path(name):
    """A map's package path without extension."""
    try:
        return maps()[name]
    except KeyError:
        raise SystemExit('map %s was not found in the game, its mods or Missions; if it '
                         'belongs to a mission, check the mission is still in Missions'
                         % name)


def read_from_pak(pak_path, name):
    """One file out of a pak, whichever version it is.

    `pak.Pak` reads the shipped v9+ paks and the v4 ones these tools write alike, so it
    is tried first. The `pakv4` path below stays as the fallback for a pak it cannot
    open: it walks the plain index and reads the payload after the 53-byte record
    header, which is only correct for the uncompressed entries `pakv4.write` produces.
    A folder is a loose build (paksource) and is read as files.
    """
    if os.path.isdir(pak_path):
        from riftlord.core import paksource
        return paksource.read(pak_path, name)
    try:
        pk = Pak(pak_path)
        # an entry keeps the pak's mount ('../../../OMD/...'); asked without it, the
        # fallback below would read the whole pak
        key = name if name in pk.files else pk.mount + name
        if isinstance(pk.files.get(key), dict):                    # a v4 (legacy) entry
            return pk.read_file(key)
        return ue.read(pk, key)
    except (Exception, SystemExit):                                # noqa: BLE001
        pass
    from riftlord.formats.ue import pakv4
    _mount, _version, entries = pakv4.read(pak_path)

    def tail(text):
        # The two readers spell the same entry differently -- one keeps the mount's
        # `../../../OMD/Content/...`, the other does not -- so they are compared from
        # `Content/` onwards, which both agree on.
        text = str(text).replace('\\', '/')
        return text.split('Content/', 1)[-1]

    want = tail(name)
    for entry_name, offset, size in entries:
        if tail(entry_name) != want:
            continue
        with open(pak_path, 'rb') as fh:
            # The entry offset points at the record header; the payload follows it, and
            # v4 written here is stored uncompressed.
            fh.seek(offset)
            head = fh.read(53)
            data_size = struct.unpack_from('<q', head, 8)[0]
            method = struct.unpack_from('<I', head, 24)[0]
            if method != 0:
                raise SystemExit('%s in %s is compressed; not supported'
                                 % (name, pak_path))
            fh.seek(offset + 53)
            return fh.read(data_size or size)
    raise SystemExit('%s is not in %s' % (name, pak_path))


def fetch_map(game_path, out_dir, exclude=()):
    """Pull a map's .umap and .uexp out of the paks.

    The shipped paks first, then the index the GAME sees, so a map installed by a mod can
    be opened like any other. Without the fallback a custom mission was listed and then
    refused when something tried to read it.

    `exclude` skips mod paks by basename. A tool that patches a map and installs the
    result MUST exclude its own output, or the next run reads back its own patched map
    and edits it a second time -- which looks like the source map having already been
    changed.
    """
    idx = _index()
    # An opened pak is meant to win even where it replaces a shipped map, so its index is
    # consulted first rather than only as a fallback for something the base paks lack.
    if _OPENED or exclude or game_path + '.umap' not in idx:
        try:
            idx = live_index(exclude)
        except (Exception, SystemExit):                            # noqa: BLE001
            pass
    got = {}
    for ext in ('.umap', '.uexp'):
        key = game_path + ext
        if key not in idx:
            raise SystemExit('not found in any pak: %s' % key)
        pak_path, name = idx[key]
        data = read_from_pak(pak_path, name)
        dest = os.path.join(out_dir, os.path.basename(game_path) + ext)
        os.makedirs(out_dir, exist_ok=True)
        write_whole(dest, data)
        got[ext] = dest
    return got


#: One write of a cache file at a time in this process (write_whole).
_WRITE_LOCK = threading.Lock()


def write_whole(dest, data):
    """Write `data` to `dest` so a concurrent reader sees the old file or the new one,
    never a truncated one: the server threads share these cache files. A file already
    holding `data` is left alone, and writes take turns: on a FUSE disk (ntfs-3g, where the
    test servers' caches live) a rename over a file unlinks it first, and a thread opening
    it between the two found nothing (measured 2026-10-31: 2-4 of 160 reads by 8 threads)."""
    with _WRITE_LOCK:
        try:
            if os.path.getsize(dest) == len(data):
                with open(dest, 'rb') as fh:
                    if fh.read() == data:
                        return
        except OSError:
            pass
        _write_whole(dest, data)


def _write_whole(dest, data):
    part = '%s.%d.%d.part' % (dest, os.getpid(), threading.get_ident())
    with open(part, 'wb') as fh:
        fh.write(data)
    # Windows refuses to replace a file another thread holds open
    for _ in range(WRITE_RETRIES):
        try:
            os.replace(part, dest)
            return
        except PermissionError:
            time.sleep(WRITE_RETRY_WAIT)
    try:
        with open(dest, 'rb') as fh:
            same = fh.read() == data
    except OSError:
        same = False
    os.remove(part)
    if not same:
        raise SystemExit('could not write %s: another program holds it open' % dest)


#: Attempts, and seconds between them, to replace a cache file that is held open.
WRITE_RETRIES = 40
WRITE_RETRY_WAIT = 0.05


def nav_export(umap_path, uexp_path):
    """Locate the navgraph buffer: (export, buffer_offset_in_uexp, buffer_size).

    A map carries one nav-data export per agent size. Only the primary agent's
    export actually holds a graph -- the large-agent one is a few hundred bytes with
    no buffer -- so the candidate with the largest buffer is the one to patch.

    The export holds the surface graph FOLLOWED BY the volumetric (flight) graph,
    so the surface buffer is NOT at the end on the ten levels that have flight
    volumes. Slicing the last NavMeshBufferSize bytes straddles the boundary there
    and yields a misaligned graph. Measured on all 33 shipped maps: placing the
    surface buffer at end - volumes - mesh validates everywhere (word[8] ==
    payloadSize), placing it last validates only where volumes == 0.
    """
    info = parse_header(umap_path)
    header_size = os.path.getsize(umap_path)
    with open(uexp_path, 'rb') as fh:
        uexp = fh.read()

    candidates = []
    for e in info['exports']:
        cls = str(e.get('class') or e.get('cls'))
        if cls != NAV_CLASS:
            continue
        start = e['off'] - header_size
        size = e['size']
        # NavMeshBufferSize is a plain UInt32Property on this export
        r = R(uexp, start)
        buf_size = None
        vol_size = 0
        try:
            while r.o < start + size:
                ni = r.i32(); r.i32()
                nm = info['names'][ni] if 0 <= ni < len(info['names']) else ''
                if nm == 'None':
                    break
                ti = r.i32(); r.i32()
                tp = info['names'][ti] if 0 <= ti < len(info['names']) else ''
                psize = r.i32(); r.i32()
                if tp == 'BoolProperty':
                    r.u8(); r.u8()
                    continue
                if tp == 'StructProperty':
                    r.i32(); r.i32(); r.raw(17)
                elif tp in ('ArrayProperty', 'ByteProperty', 'EnumProperty'):
                    r.i32(); r.i32(); r.u8()
                else:
                    r.u8()
                payload = r.raw(psize)
                if nm == 'NavMeshBufferSize':
                    buf_size = struct.unpack('<I', payload)[0]
                elif nm == 'NavVolumesBufferSize':
                    vol_size = struct.unpack('<I', payload)[0]
        except (Exception, SystemExit):
            pass
        if buf_size:
            off = start + size - vol_size - buf_size
            candidates.append((e, off, buf_size, vol_size))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[2])
    return candidates[-1][:3]


def nav_buffers(umap_path, uexp_path):
    """(export, surface_offset, surface_size, volumes_offset, volumes_size).

    The volumetric graph is what flyers need and it only exists on the ten levels
    that ship flight volumes; elsewhere its size is 0.
    """
    info = parse_header(umap_path)
    header_size = os.path.getsize(umap_path)
    with open(uexp_path, 'rb') as fh:
        uexp = fh.read()
    best = None
    for e in info['exports']:
        if str(e.get('class') or e.get('cls')) != NAV_CLASS:
            continue
        start, size = e['off'] - header_size, e['size']
        r = R(uexp, start)
        mesh = 0
        vols = 0
        try:
            while r.o < start + size:
                ni = r.i32(); r.i32()
                nm = info['names'][ni] if 0 <= ni < len(info['names']) else ''
                if nm == 'None':
                    break
                ti = r.i32(); r.i32()
                tp = info['names'][ti] if 0 <= ti < len(info['names']) else ''
                psize = r.i32(); r.i32()
                if tp == 'BoolProperty':
                    r.u8(); r.u8()
                    continue
                if tp == 'StructProperty':
                    r.i32(); r.i32(); r.raw(17)
                elif tp in ('ArrayProperty', 'ByteProperty', 'EnumProperty'):
                    r.i32(); r.i32(); r.u8()
                else:
                    r.u8()
                payload = r.raw(psize)
                if nm == 'NavMeshBufferSize':
                    mesh = struct.unpack('<I', payload)[0]
                elif nm == 'NavVolumesBufferSize':
                    vols = struct.unpack('<I', payload)[0]
        except (Exception, SystemExit):
            pass
        if mesh and (best is None or mesh > best[2]):
            end = start + size
            best = (e, end - vols - mesh, mesh, end - vols, vols)
    return best


def omd3_graph(map_name, tmp):
    """Another OMD3 map's navgraph, for use as a control donor.

    Transplanting a *known-good* OMD3 graph with the same zero-padding proves
    whether the padding itself is survivable. Without this control, a failed OMD2
    transplant cannot be told apart from a packaging bug.
    """
    files = fetch_map(map_path(map_name), tmp)
    found = nav_export(files['.umap'], files['.uexp'])
    if not found:
        raise SystemExit('no navgraph in %s' % map_name)
    _e, off, size = found
    with open(files['.uexp'], 'rb') as fh:
        return fh.read()[off:off + size]


def patch(source_scene, target_map, out_dir, zip_path=OMD2_ZIP, self_test=False,
          omd3_source=None):
    """Transplant a navgraph. With self_test, repack the map unchanged instead.

    The self test is the control: it proves the pak overrides the base game and that
    the buffer surgery is sound, so that a later failure can be blamed on the graph
    rather than the pipeline. Run it before trusting any transplant result.
    """
    src = None
    if omd3_source:
        graph = omd3_graph(omd3_source, os.path.join(out_dir, '.donor'))
        src = {'scene': omd3_source + ' (OMD3 control)', 'graph': graph,
               'payload': struct.unpack_from('<I', graph, 8)[0]}
    elif not self_test:
        graphs = {g['scene']: g for g in omd2_graphs(zip_path)}
        if source_scene not in graphs:
            raise SystemExit('unknown OMD2 scene %r (use --list)' % source_scene)
        src = graphs[source_scene]

    game_path = map_path(target_map)
    files = fetch_map(game_path, out_dir)
    found = nav_export(files['.umap'], files['.uexp'])
    if not found:
        raise SystemExit('no %s export with a buffer in %s' % (NAV_CLASS, target_map))
    export, offset, size = found

    if self_test:
        return {
            'source': '(none -- repacked unchanged)', 'source_payload': 0,
            'target': target_map, 'export': export['name'],
            'buffer_offset_in_uexp': offset, 'buffer_size': size,
            'graph_bytes': size, 'padding': 0,
            'files': files, 'game_path': game_path,
        }

    if len(src['graph']) > size:
        raise SystemExit(
            'graph for %s needs %d bytes but %s only has %d -- pick a bigger target'
            % (source_scene, len(src['graph']), target_map, size))

    graph = bytearray(src['graph'])
    if not omd3_source:
        struct.pack_into('<I', graph, 20, OMD3_FLAGS)   # word[5]: the flags OMD3 writes
    payload = bytes(graph) + b'\x00' * (size - len(graph))
    assert len(payload) == size

    with open(files['.uexp'], 'rb') as fh:
        uexp = bytearray(fh.read())
    before = len(uexp)
    uexp[offset:offset + size] = payload
    assert len(uexp) == before, 'package size changed -- offsets would need fixing'
    with open(files['.uexp'], 'wb') as fh:
        fh.write(uexp)

    return {
        'source': source_scene,
        'source_payload': src['payload'],
        'target': target_map,
        'export': export['name'],
        'buffer_offset_in_uexp': offset,
        'buffer_size': size,
        'graph_bytes': len(src['graph']),
        'padding': size - len(src['graph']),
        'files': files,
        'game_path': game_path,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--zip', default=OMD2_ZIP)
    ap.add_argument('--source', help='OMD2 scene whose navgraph to transplant')
    ap.add_argument('--omd3-source', metavar='MAP',
                    help='CONTROL: use another OMD3 map\'s graph as the donor, padded '
                         'identically, to prove the padding is survivable')
    ap.add_argument('--self', dest='self_test', action='store_true',
                    help='CONTROL: repack the target unchanged, to prove the pipeline')
    ap.add_argument('--target', help='OMD3 map to patch, e.g. Scenario_18')
    ap.add_argument('--out', default=gamepath.beside_code(gamepath.CODE_ROOT, 'patched'))
    ap.add_argument('--pak', metavar='NAME',
                    help='also write a mod pak, e.g. 700-NavProbe_P.pak')
    ap.add_argument('--install', action='store_true',
                    help='write the pak straight into Content/Paks/~mods/')
    ap.add_argument('--list', action='store_true',
                    help='show OMD2 graphs and OMD3 buffers, smallest first')
    args = ap.parse_args()

    if args.list:
        print('OMD2 navgraphs (smallest first) -- "needs" is the buffer size required:')
        for g in omd2_graphs(args.zip)[:8]:
            print('  %-28s payload %-9d needs %d' % (g['scene'], g['payload'], g['needs']))
        print('\nOMD3 buffers: run  python3 navpatch.py --target <map> --source <scene>')
        return

    if not args.target or not (args.source or args.self_test or args.omd3_source):
        ap.error('need --target plus one of --source / --omd3-source / --self')

    r = patch(args.source, args.target, args.out, args.zip, args.self_test,
              args.omd3_source)
    print('source        %s  (%d byte graph, payload %d)'
          % (r['source'], r['graph_bytes'], r['source_payload']))
    print('target        %s  export %s' % (r['target'], r['export']))
    print('buffer        %d bytes at uexp offset %d'
          % (r['buffer_size'], r['buffer_offset_in_uexp']))
    print('padding       %d zero bytes' % r['padding'])
    print('package size  unchanged, so no export offsets needed fixing')
    for ext, p in r['files'].items():
        print('  %s  %s' % (ext, p))
    inner = r['game_path'].replace('/Game/', 'OMD/Content/')
    print('\nPackage these two at:  %s' % inner)

    if args.pak or args.install:
        name = args.pak or '700-NavGraphTest_P.pak'
        dest = os.path.join(PAK_DIR, '~mods', name) if args.install \
            else os.path.join(args.out, name)
        info = pakv4.write(dest, {
            inner + '.umap': r['files']['.umap'],
            inner + '.uexp': r['files']['.uexp'],
        })
        print('\npak           %s' % info['pak'])
        print('              %d files, %d bytes, version 4' % (info['files'], info['size']))
        back = pakv4.read(info['pak'])
        print('              verified: mount %r, %d entries' % (back[0], len(back[2])))
        if not args.install:
            print('\nDrop it in:   %s' % os.path.join(PAK_DIR, '~mods'))


if __name__ == '__main__':
    main()
