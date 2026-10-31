"""BC1/BC3/BC4/BC5 block encoding through Intel's ISPC Texture Compressor (MIT licence;
native/ispctexcomp/build.py builds it, the frozen app carries it): the default BC encoder
of bcn.encode and lightcodec.encode (user decision 2026-10-24).

    if ispctex.chosen('PF_DXT5'):
        data = ispctex.encode(rgba, 'PF_DXT5')    # rgba: uint8 (h, w, 4)

RIFTLORD_BC_ENCODER=python keeps the Python encoder; when the library cannot load, the
Python encoder runs instead. Minimap textures (PYTHON_KINDS) always take the Python encoder:
measured on Beset's minimap, ISPC's colour error was up to 1.4 RMSE higher on its mips
(docs/PLAN.md round 10). Each process says once per texture class (stderr) which encoder ran.
RIFTLORD_BC_RECORD=DIR keeps, per ISPC encode, the source and the output (the build
comparison's certificate: tools/bccertify.py).
"""

import ctypes
import functools
import os
import sys

#: The library's file name per platform.
LIBRARY = 'ispc_texcomp.dll' if os.name == 'nt' else 'libispc_texcomp.so'
#: Where it is looked for: RIFTLORD_ISPC_TEXCOMP (a file), the frozen bundle, the build cache.
SEARCH = [os.path.join(getattr(sys, '_MEIPASS', ''), 'native', LIBRARY),
          os.path.expanduser(os.path.join('~', '.cache', 'rift-lord', 'ispctc', 'out', LIBRARY))]
#: Block encoder and bytes per 4x4 block, per pixel format.
FORMATS = {'PF_DXT1': ('CompressBlocksBC1', 8), 'PF_DXT5': ('CompressBlocksBC3', 16),
           'PF_BC4': ('CompressBlocksBC4', 8), 'PF_BC5': ('CompressBlocksBC5', 16)}


class _Surface(ctypes.Structure):
    _fields_ = [('ptr', ctypes.POINTER(ctypes.c_uint8)), ('width', ctypes.c_int32),
                ('height', ctypes.c_int32), ('stride', ctypes.c_int32)]


@functools.lru_cache(maxsize=1)
def _lib():
    for path in [os.environ.get('RIFTLORD_ISPC_TEXCOMP')] + SEARCH:
        if path and os.path.isfile(path):
            try:
                lib = ctypes.CDLL(path)
            except OSError:
                continue
            for name, _size in FORMATS.values():
                getattr(lib, name).argtypes = [ctypes.POINTER(_Surface),
                                               ctypes.POINTER(ctypes.c_uint8)]
                getattr(lib, name).restype = None
            return lib
    return None


#: RIFTLORD_BC_ENCODER: 'python' keeps the Python encoder; unset or 'ispc', this library.
ENCODER_ENV = 'RIFTLORD_BC_ENCODER'
#: RIFTLORD_BC_RECORD: a folder each ISPC encode's source and output are kept in.
RECORD_ENV = 'RIFTLORD_BC_RECORD'
_SAID = set()


#: Texture classes the Python encoder always encodes (better on their line art).
PYTHON_KINDS = ('minimap',)


def _say(kind, which, why=''):
    if (kind, which) not in _SAID:
        _SAID.add((kind, which))
        print('BC encoder for %s: %s%s' % (kind, which, (' (%s)' % why) if why else ''),
              file=sys.stderr, flush=True)


def chosen(fmt, kind='texture'):
    """True when this library encodes `fmt` for a texture of class `kind` ('lightmap',
    'minimap', 'texture'): not a PYTHON_KINDS class, not switched off
    (RIFTLORD_BC_ENCODER=python), it loads, and it does `fmt`. Says once per class which
    encoder runs."""
    if kind in PYTHON_KINDS:
        _say(kind, 'Python', 'its class is kept on the Python encoder')
        return False
    if os.environ.get(ENCODER_ENV, 'ispc') == 'python':
        _say(kind, 'Python', '%s=python' % ENCODER_ENV)
        return False
    if not available():
        _say(kind, 'Python', 'the ISPC library did not load')
        return False
    if fmt not in FORMATS:
        return False
    _say(kind, 'ISPC', library_path())
    return True


@functools.lru_cache(maxsize=1)
def library_path():
    """The library file that loads, or None."""
    for path in [os.environ.get('RIFTLORD_ISPC_TEXCOMP')] + SEARCH:
        if path and os.path.isfile(path):
            try:
                ctypes.CDLL(path)
            except OSError:
                continue
            return path
    return None


def available():
    """True when the library loads."""
    return _lib() is not None


def encode(rgba, fmt):
    """BC blocks of an RGBA uint8 array (h, w, 4), padded to whole blocks by repeating the
    last row and column, in row-major block order."""
    import numpy as np
    name, size = FORMATS[fmt]
    a = np.asarray(rgba, dtype=np.uint8)
    h, w = a.shape[:2]
    if h % 4 or w % 4:
        a = np.pad(a, ((0, -h % 4), (0, -w % 4), (0, 0)), mode='edge')
        h, w = a.shape[:2]
    a = np.ascontiguousarray(a)
    out = np.zeros((h // 4) * (w // 4) * size, dtype=np.uint8)
    surface = _Surface(a.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)), w, h, w * 4)
    getattr(_lib(), name)(ctypes.byref(surface), out.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)))
    data = out.tobytes()
    if os.environ.get(RECORD_ENV):
        _record(os.environ[RECORD_ENV], a, fmt, data)
    return data


def _record(folder, rgba, fmt, data):
    """Keep one encode: <sha1 of the output>.npz holding the (padded) source and the format."""
    import hashlib
    import numpy as np
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, hashlib.sha1(data).hexdigest() + '.npz')
    if not os.path.exists(path):
        part = path + '.%d.part.npz' % os.getpid()
        np.savez_compressed(part, rgba=rgba, fmt=np.array(fmt))
        os.replace(part, path)
