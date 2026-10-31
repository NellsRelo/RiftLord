"""Write a pak with Unreal Engine 4.27's own UnrealPak, called in the engine install (never
bundled: the UE EULA governs who may receive engine tools).

    if unrealpak.available():
        unrealpak.write('/path/X.pak', {'OMD/Content/...uasset': b'...', ...})

The engine is the one riftlord.core.ueroot finds (shared with pxcook). The pak is version 11
(UnrealPak's default), uncompressed, mounted at ../../../ like the game's own.
"""

import os
import shutil
import subprocess
import tempfile

#: Seconds one pak may take.
TIMEOUT = 600


def root():
    """The UE 4.27 folder (riftlord.core.ueroot), or None."""
    from riftlord.core import ueroot
    return ueroot.find()


def binary():
    """UnrealPak in the engine install, or None."""
    if not root():
        return None
    name = 'UnrealPak.exe' if os.name == 'nt' else 'UnrealPak'
    folder = 'Win64' if os.name == 'nt' else 'Linux'
    path = os.path.join(root(), 'Engine', 'Binaries', folder, name)
    return path if os.path.isfile(path) else None


def available():
    return binary() is not None


def write(pak_path, files):
    """Write `files` ({entry under the mount point: bytes}) to `pak_path` with UnrealPak."""
    exe = binary()
    if exe is None:
        raise SystemExit('UnrealPak is not built in %s' % root())
    tmp = tempfile.mkdtemp(prefix='unrealpak-')
    try:
        staged = os.path.join(tmp, 'files')
        lines = []
        for entry, data in sorted(files.items()):
            src = os.path.join(staged, *entry.split('/'))
            os.makedirs(os.path.dirname(src), exist_ok=True)
            with open(src, 'wb') as fh:
                fh.write(data)
            lines.append('"%s" "../../../%s"' % (src, entry))
        response = os.path.join(tmp, 'files.txt')
        with open(response, 'w') as fh:
            fh.write('\n'.join(lines) + '\n')
        part = pak_path + '.part'
        got = subprocess.run([exe, part, '-create=' + response], capture_output=True,
                             text=True, timeout=TIMEOUT)
        if got.returncode != 0 or not os.path.isfile(part):
            raise SystemExit('UnrealPak failed (%s): %s' % (got.returncode,
                                                            (got.stdout + got.stderr)[-600:]))
        from riftlord.core import pakguard
        pakguard.replace(part, pak_path)
        return pak_path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
