"""A built mod is either a `.pak` or a loose folder of cooked files; read and write both.

Rift Lord keeps what its editors build as loose `.uasset`/`.uexp`/`.umap` files under
`<folder>/OMD/Content/...` -- the same paths a pak holds -- and only packs them into a
`.pak` when they are installed. Everything that reads or rewrites a build goes through
here, so it does not care which of the two it was given.

A source is named by its path: `.../900_MyMap_P.pak` (a pak) or `.../900_MyMap_P` (a
folder). Its stem, `900_MyMap_P`, is what a loose build's notes beside it are named after;
a pak holds them inside (makemod.OMUI_DIR).
"""

import hashlib
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

#: Where cooked files sit inside a source, as inside a pak.
CONTENT = 'OMD/Content'


def is_loose(path):
    return os.path.isdir(path)


def stem(path):
    name = os.path.basename(path.rstrip('/\\'))
    return name[:-4] if name.endswith('.pak') else name


def sidecar(path, suffix):
    """`<folder>/<stem><suffix>`: a file that belongs to a source and sits beside it."""
    return os.path.join(os.path.dirname(path.rstrip('/\\')), stem(path) + suffix)


#: Folders that hold cooked files but are not builds: one being written, the one it
#: replaces, and one set aside (a campaign left with no missions).
NOT_BUILDS = ('.writing', '.old', '.disabled')


def is_source(path):
    """A pak, or a folder holding cooked files the way a pak does."""
    name = os.path.basename(path.rstrip('/\\'))
    if name.startswith('.') or name.endswith(NOT_BUILDS):
        return False
    if path.endswith('.pak') and os.path.isfile(path):
        return True
    return os.path.isdir(os.path.join(path, *CONTENT.split('/')))


def sources(folder):
    """Every pak and loose build directly inside `folder`, by name."""
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    return [os.path.join(folder, n) for n in names if is_source(os.path.join(folder, n))]


def names(path):
    """Inner paths (`OMD/Content/...`) of everything a source holds."""
    if is_loose(path):
        out = []
        for base, _dirs, files in os.walk(path):
            for f in files:
                rel = os.path.relpath(os.path.join(base, f), path).replace(os.sep, '/')
                if rel.startswith(CONTENT + '/'):
                    out.append(rel)
        return sorted(out)
    from riftlord.formats.ue import pak as _pak
    return [k[k.index('OMD/'):] if 'OMD/' in k else k for k in _pak.Pak(path).files]


def read(path, inner):
    """One file out of a source, by inner path."""
    if is_loose(path):
        inner = inner[inner.index('OMD/'):] if 'OMD/' in inner else inner
        with open(os.path.join(path, *inner.split('/')), 'rb') as fh:
            return fh.read()
    from riftlord.build.navigation import navpatch
    return navpatch.read_from_pak(path, inner)


def entries(path):
    """{inner path: bytes} for everything a source holds."""
    if is_loose(path):
        return {n: read(path, n) for n in names(path)}
    from riftlord.formats.ue import pak as _pak
    src = _pak.Pak(path)
    out = {}
    for key in src.files:
        cut = key.index('OMD/') if 'OMD/' in key else 0
        out[key[cut:]] = src.read_file(key)
    return out


#: Which writer makes paks: RIFTLORD_PAK_WRITER 'unrealpak' (UE 4.27's own UnrealPak, when
#: the engine is installed: riftlord.core.unrealpak), else ours (formats.ue.pakv4).
PAK_WRITER_ENV = 'RIFTLORD_PAK_WRITER'


def write_pak(path, files):
    """`files` ({inner path: bytes}) written as the pak `path` by the chosen writer."""
    if os.environ.get(PAK_WRITER_ENV) == 'unrealpak':
        from riftlord.core import unrealpak
        if unrealpak.available():
            return unrealpak.write(path, files)
    from riftlord.formats.ue import pakv4
    return pakv4.write(path, files)


def write(path, files, keep_omui=True):
    """Write `files` ({inner path: bytes}) as a pak when `path` ends in `.pak`, else as a
    loose folder that holds exactly them -- written beside it, then swapped in, so a
    failure part-way leaves the old build whole.

    A pak carries the files OrcsMustUI reads inside it (makemod.OMUI_DIR). Writing one
    keeps those the pak it replaces held and `files` does not name (unless `keep_omui`
    is False: the caller passes the full set), takes in the old sidecar files beside it
    (`<stem>.CustomMaps.yaml`, `.Tab.png`, ...), and deletes them and any Registry.yaml.
    A loose build (Rift Lord's bucket) keeps its notes beside it; they go into the pak
    when it is packed (bucket._pack)."""
    if path.endswith('.pak'):
        from riftlord.build import makemod
        files = dict(files)
        if keep_omui and os.path.isfile(path):
            try:
                old = names(path)
            except Exception:                        # noqa: BLE001
                old = []
            have = makemod.omui_files(files)
            for name, inner in makemod.omui_files(old).items():
                if name not in have:
                    files[inner] = read(path, inner)
        key = makemod.pak_mod_key(files)
        if key:
            makemod.embed_loose_sidecars(path, files, key)
        # Written beside it and renamed into place, so nothing reading ~mods -- the game,
        # or another tool -- ever sees a half-written pak; a pak the running game may have
        # mounted is replaced once it has closed (riftlord.core.pakguard)
        from riftlord.core import pakguard
        part = path + '.part'
        write_pak(part, files)
        pakguard.replace(part, path)
        if key:
            makemod.remove_loose_sidecars(path)
        else:
            makemod.write_registry_sidecar(path)
        return path
    tmp = path.rstrip('/\\') + '.writing'
    if os.path.exists(tmp):
        shutil.rmtree(tmp)
    for inner, blob in files.items():
        target = os.path.join(tmp, *inner.split('/'))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if not isinstance(blob, (bytes, bytearray)):
            with open(blob, 'rb') as fh:
                blob = fh.read()
        with open(target, 'wb') as fh:
            fh.write(blob)
    old = path.rstrip('/\\') + '.old'
    if os.path.exists(path):
        if os.path.exists(old):
            shutil.rmtree(old)
        os.replace(path, old)
    os.replace(tmp, path)
    if os.path.exists(old):
        shutil.rmtree(old)
    return path


def remove(path):
    """Delete a build: a loose folder, or a pak (once the game has closed when it is one
    the running game may have mounted: riftlord.core.pakguard)."""
    if is_loose(path):
        shutil.rmtree(path)
    elif os.path.exists(path):
        from riftlord.core import pakguard
        pakguard.remove(path)


def backup(path, suffix):
    """Keep a copy of a source before it is rewritten in place. A loose build's copy
    goes in a hidden `.backups` folder beside it, so it is not taken for a build."""
    keep = path.rstrip('/\\') + suffix
    if is_loose(path):
        keep = os.path.join(os.path.dirname(path.rstrip('/\\')), '.backups',
                            stem(path) + suffix)
        os.makedirs(os.path.dirname(keep), exist_ok=True)
        if os.path.exists(keep):
            shutil.rmtree(keep)
        shutil.copytree(path, keep)
    else:
        shutil.copy2(path, keep)
    return keep


def size(path):
    if not is_loose(path):
        return os.path.getsize(path)
    return sum(os.path.getsize(os.path.join(b, f))
               for b, _d, fs in os.walk(path) for f in fs)


_HASHES = {}


def digest(path):
    """sha1 over a source's contents: a pak's bytes, or a folder's files by path.
    Cached on what the file system says about it, since paks run to tens of MB."""
    if is_loose(path):
        stats = []
        for base, _d, fs in os.walk(path):
            for f in fs:
                st = os.stat(os.path.join(base, f))
                stats.append((os.path.join(base, f), st.st_size, st.st_mtime_ns))
        key = (path, tuple(sorted(stats)))
    else:
        st = os.stat(path)
        key = (path, st.st_size, st.st_mtime_ns)
    if key not in _HASHES:
        h = hashlib.sha1()
        if is_loose(path):
            for n in names(path):
                h.update(n.encode('utf-8') + b'\0')
                h.update(read(path, n))
        else:
            with open(path, 'rb') as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b''):
                    h.update(chunk)
        _HASHES[key] = h.hexdigest()
    return _HASHES[key]
