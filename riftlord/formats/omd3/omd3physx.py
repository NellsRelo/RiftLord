"""Cooked PhysX collision for OMD3 BodySetups, built offline.

A cooked UBodySetup carries one `PhysXPC` bulk blob (FDerivedDataPhysXCooker::Build):

    u8  bLittleEndian (1)
    i32 NumConvex, i32 NumMirroredConvex, i32 NumTriMeshes
    per convex:   u8 cooked flag, then the PhysX convex stream ("NXS" "CVXM" ...)
    per mirrored: the same, built from the hull with X negated
    per trimesh:  the PhysX triangle-mesh stream ("NXS" "MESH" ...)
    FBodySetupUVInfo: three empty TArrays (12 bytes) unless UV hit results are on

The PhysX streams come from `pxcook` (native/pxcook/pxcook.cpp), a small CLI with
FPhysXCooking's parameters (tolerance scale 100/1000, weld 0.1, eWELD_VERTICES, BVH33,
PC). It is never bundled (no Epic engine code in the release): each user's Rift Lord
compiles it on first use against their own UE 4.27 (riftlord.core.ueroot), into the user
cache under a digest of the engine folder, pxcook.cpp and the compiler (`build_tool`),
under a file lock, by a temporary file renamed into place.
* Linux (a source build: UE links PhysX statically there): the static PhysX libraries of
  Engine/Source/ThirdParty/PhysX3/Lib/Linux (Setup.sh's dependencies), with the engine's
  own clang and libc++ when present, else the system clang++ and libstdc++.
* Windows (UNTESTED until a Windows run): MSVC found by vswhere, linking the install's
  PhysX import libraries (PhysX3/Lib/Win64/VS2015); pxcook.exe runs with the install's
  PhysX DLLs on its PATH.
`tool_status()` says whether it is ready, buildable or missing without building.
`python3 omd3physx.py build` compiles it; `python3 omd3physx.py verify N` re-cooks N
shipped body setups and compares bytes.
"""

import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile

import numpy as np
from riftlord.core import gamepath, ueroot

HERE = os.path.dirname(os.path.abspath(__file__))
#: pxcook's source: in the tree, or carried at native/pxcook in the frozen app.
SOURCE = os.path.join(gamepath.REPO_ROOT, 'native', 'pxcook', 'pxcook.cpp')
#: A pxcook to use as it is (a test's or a caller's), instead of the cached build.
BINARY = None
#: The user cache folder the builds go to (out of the game folder: UE4SS walks it).
CACHE = os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'rift-lord',
                     'pxcook') if os.name == 'nt' else \
    os.path.join(os.path.expanduser('~'), '.cache', 'rift-lord', 'pxcook')
#: The physics format name OMD3 cooks for (FPlatformProperties::GetPhysicsFormat on PC).
FORMAT = 'PhysXPC'
#: FFormatContainer bulk flags on every shipped PhysXPC blob (ForceInlinePayload | SingleUse).
BULK_FLAGS = 0x48
#: An empty FBodySetupUVInfo: IndexBuffer, VertPositions, VertUVs (all TArray, count 0).
EMPTY_UV_INFO = b'\0' * 12

#: Inside the engine folder.
PHYSX = os.path.join('Engine', 'Source', 'ThirdParty', 'PhysX3')
LINUX_LIBS = os.path.join(PHYSX, 'Lib', 'Linux', 'x86_64-unknown-linux-gnu')
WIN_LIBS = os.path.join(PHYSX, 'Lib', 'Win64', 'VS2015')
WIN_DLLS = os.path.join('Engine', 'Binaries', 'ThirdParty', 'PhysX3', 'Win64', 'VS2015')
LINUX_TOOLCHAINS = os.path.join('Engine', 'Extras', 'ThirdPartyNotUE', 'SDKs', 'HostLinux',
                                'Linux_x64')
LIBCXX = os.path.join('Engine', 'Source', 'ThirdParty', 'Linux', 'LibCxx')
#: The PhysX libraries pxcook links, static (Linux) or import libraries (Windows).
LINUX_LINK = ['PhysX3Cooking', 'PhysX3Extensions', 'PhysX3', 'PhysX3Common', 'PxFoundation',
              'PxPvdSDK']
WIN_LINK = ['PhysX3_x64.lib', 'PhysX3Common_x64.lib', 'PhysX3Cooking_x64.lib',
            'PxFoundation_x64.lib']
#: What Fab conversion and every collision cooking path say when pxcook cannot be had.
NEEDS = 'Fab conversion needs Unreal Engine 4.27 and a C++ compiler'


def _engine_toolchain(root):
    """The engine's own Linux clang++ and its libc++ folder, or (None, None)."""
    import glob
    hits = sorted(glob.glob(os.path.join(glob.escape(root), LINUX_TOOLCHAINS, '*',
                                         'x86_64-unknown-linux-gnu', 'bin', 'clang++')))
    libcxx = os.path.join(root, LIBCXX)
    if hits and os.path.isdir(os.path.join(libcxx, 'include', 'c++', 'v1')):
        return hits[-1], libcxx
    return None, None


def _vswhere():
    """MSVC's vcvars64.bat and the Visual Studio version, by vswhere, or (None, None)."""
    exe = os.path.join(os.environ.get('ProgramFiles(x86)') or r'C:\Program Files (x86)',
                       'Microsoft Visual Studio', 'Installer', 'vswhere.exe')
    if not os.path.isfile(exe):
        return None, None
    try:
        got = subprocess.run([exe, '-latest', '-products', '*', '-requires',
                              'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property',
                              'installationPath'], capture_output=True, text=True, timeout=60)
        ver = subprocess.run([exe, '-latest', '-products', '*', '-property',
                              'installationVersion'], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None, None
    path = got.stdout.strip().splitlines()[0] if got.stdout.strip() else ''
    bat = os.path.join(path, 'VC', 'Auxiliary', 'Build', 'vcvars64.bat') if path else ''
    return (bat, 'MSVC ' + ver.stdout.strip()) if os.path.isfile(bat) else (None, None)


#: _compiler's answers by engine folder: asking runs the compiler (status is polled).
_COMPILERS = {}


def _compiler(root):
    """(kind, path, version) of the compiler pxcook is built with, or None. Kinds: 'ue'
    (the engine's clang and libc++), 'clang' (the system clang++), 'msvc' (vcvars64.bat)."""
    if root not in _COMPILERS:
        _COMPILERS[root] = _find_compiler(root)
    return _COMPILERS[root]


def _find_compiler(root):
    if os.name == 'nt':
        bat, ver = _vswhere()
        return ('msvc', bat, ver) if bat else None
    clang, _libcxx = _engine_toolchain(root) if root else (None, None)
    kind = 'ue' if clang else 'clang'
    clang = clang or shutil.which('clang++')
    if not clang:
        return None
    try:
        ver = subprocess.run([clang, '--version'], capture_output=True, text=True,
                             timeout=60).stdout.splitlines()[0].strip()
    except (OSError, subprocess.SubprocessError, IndexError):
        return None
    return kind, clang, ver


def _key(root, compiler):
    h = hashlib.sha256()
    for part in (os.path.normcase(os.path.abspath(root)), compiler[0], compiler[2]):
        h.update(part.encode() + b'\0')
    with open(SOURCE, 'rb') as fh:
        h.update(fh.read())
    return h.hexdigest()[:16]


def _built(root, compiler):
    """Where the build for this engine, compiler and source goes."""
    return os.path.join(CACHE, _key(root, compiler), 'pxcook.exe' if os.name == 'nt' else 'pxcook')


def tool_status():
    """Without building: {ue_root, compiler, pxcook: 'ready' | 'buildable' | 'missing',
    detail}. `detail` is one line saying what was found, or what is missing."""
    root = ueroot.find()
    out = {'ue_root': root, 'compiler': None, 'pxcook': 'missing', 'detail': ''}
    if BINARY:
        out.update(pxcook='ready' if os.path.isfile(BINARY) else 'missing',
                   detail='pxcook given: %s' % BINARY)
        return out
    if not root:
        out['detail'] = ('No Unreal Engine 4.27 found (set its folder with %s or the %r '
                         'setting)' % (ueroot.ENV, ueroot.SETTING))
        return out
    libs = os.path.join(root, WIN_LIBS if os.name == 'nt' else LINUX_LIBS)
    if not os.path.isdir(libs) or not os.path.isdir(os.path.join(root, PHYSX, 'PhysX_3.4', 'Include')):
        out['detail'] = ('%s has no PhysX libraries (%s)%s' % (
            root, libs, '' if os.name == 'nt' else ': run its Setup.sh'))
        return out
    comp = _compiler(root)
    if not comp:
        out['detail'] = ('No C++ compiler found (Visual Studio Build Tools with the C++ '
                         'workload)' if os.name == 'nt' else
                         'No C++ compiler found (the engine toolchain or clang++)')
        return out
    out['compiler'] = '%s (%s)' % (comp[2], comp[1])
    if not os.path.isfile(SOURCE):
        out['detail'] = 'pxcook source missing: %s' % SOURCE
        return out
    path = _built(root, comp)
    ready = os.path.isfile(path)
    out.update(pxcook='ready' if ready else 'buildable',
               detail='%s with %s against %s' % ('built' if ready else 'builds on first use',
                                                 comp[2], root))
    return out


def unavailable_message(status=None):
    """NEEDS and the detail line, when pxcook can be neither run nor built; else None."""
    status = status or tool_status()
    return None if status['pxcook'] != 'missing' else '%s. %s.' % (NEEDS, status['detail'])


def available():
    """None when pxcook is ready or buildable here, else the message (unavailable_message)."""
    return unavailable_message()


def _command(root, comp, out_path):
    """The compile command line (a list; Windows: one cmd.exe line through vcvars64)."""
    inc = ['-I' + os.path.join(root, PHYSX, 'PhysX_3.4', 'Include'),
           '-I' + os.path.join(root, PHYSX, 'PxShared', 'include')]
    if comp[0] == 'msvc':
        line = ('call "%s" >nul && cl /nologo /O1 /EHsc /MD /DNDEBUG /I "%s" /I "%s" "%s" '
                '/Fe:"%s" /link /LIBPATH:"%s" %s' % (
                    comp[1], inc[0][2:], inc[1][2:], SOURCE, out_path,
                    os.path.join(root, WIN_LIBS), ' '.join(WIN_LINK)))
        return ['cmd.exe', '/d', '/s', '/c', line]
    link = (['-Wl,--start-group'] + ['-l' + n for n in LINUX_LINK] + ['-Wl,--end-group'])
    cmd = [comp[1], '-O1', '-std=c++14', '-DNDEBUG', '-DPX_PHYSX_STATIC_LIB'] + inc
    if comp[0] == 'ue':
        _clang, libcxx = _engine_toolchain(root)
        sysroot = os.path.dirname(os.path.dirname(comp[1]))
        lib = os.path.join(libcxx, 'lib', 'Linux', 'x86_64-unknown-linux-gnu')
        cmd = [comp[1], '--sysroot=' + sysroot, '-nostdinc++',
               '-I' + os.path.join(libcxx, 'include', 'c++', 'v1')] + cmd[1:]
        tail = ['-nostdlib++', os.path.join(lib, 'libc++.a'), os.path.join(lib, 'libc++abi.a'),
                '-lpthread', '-ldl', '-lm']
    else:
        tail = ['-static-libstdc++', '-static-libgcc', '-lpthread', '-ldl']
    return cmd + [SOURCE, '-o', out_path, '-L' + os.path.join(root, LINUX_LIBS)] + link + tail


class _Lock:
    """An exclusive lock on a file (flock; msvcrt on Windows), held while in the block."""

    def __init__(self, path):
        self.path = path

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.fh = open(self.path, 'a+b')
        if os.name == 'nt':
            import msvcrt
            import time
            while True:
                try:
                    msvcrt.locking(self.fh.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(1)
        else:
            import fcntl
            fcntl.flock(self.fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_exc):
        if os.name == 'nt':
            import msvcrt
            try:
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        self.fh.close()


def compile_tool(out_path, status=None):
    """Compile pxcook to `out_path` for the engine and compiler tool_status() finds: into a
    temporary file beside it, renamed into place. Returns `out_path`; SystemExit with the
    unavailable message when it cannot be built."""
    status = status or tool_status()
    if status['pxcook'] == 'missing':
        raise SystemExit(unavailable_message(status))
    root = status['ue_root']
    comp = _compiler(root)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    part = '%s.%d.part%s' % (out_path, os.getpid(), '.exe' if os.name == 'nt' else '')
    try:
        got = subprocess.run(_command(root, comp, part), capture_output=True, text=True,
                             cwd=os.path.dirname(out_path))
        if got.returncode or not os.path.isfile(part):
            raise SystemExit('pxcook did not build with %s: %s' % (
                comp[2], (got.stdout + got.stderr).strip()[-600:]))
        os.replace(part, out_path)
    finally:
        if os.path.exists(part):
            os.remove(part)
    return out_path


def build_tool(force=False):
    """The pxcook to run: BINARY when set, else this engine, source and compiler's build in
    the user cache, compiled once (under a lock: workers and other trees may ask at once).
    SystemExit with the unavailable message when it can be neither found nor built."""
    if BINARY:
        if not os.path.isfile(BINARY):
            raise SystemExit('pxcook not found: %s' % BINARY)
        return BINARY
    status = tool_status()
    if status['pxcook'] == 'missing':
        raise SystemExit(unavailable_message(status))
    path = _built(status['ue_root'], _compiler(status['ue_root']))
    if os.path.isfile(path) and not force:
        return path
    with _Lock(os.path.join(CACHE, 'build.lock')):
        if not os.path.isfile(path) or force:
            compile_tool(path, status)
    return path


def _env():
    """pxcook's environment: on Windows the install's PhysX DLLs on its PATH."""
    if os.name != 'nt':
        return None
    root = ueroot.find()
    dlls = os.path.join(root, WIN_DLLS) if root else ''
    return dict(os.environ, PATH=dlls + os.pathsep + os.environ.get('PATH', ''))


def _run(mode, payload, extra=()):
    tool = build_tool()
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = os.path.join(tmp, 'in.bin'), os.path.join(tmp, 'out.bin')
        with open(src, 'wb') as fh:
            fh.write(payload)
        got = subprocess.run([tool, mode, src, dst, *extra], capture_output=True, text=True,
                             env=_env())
        if got.returncode:
            raise RuntimeError('pxcook %s failed: %s' % (mode, got.stderr.strip()))
        with open(dst, 'rb') as fh:
            return fh.read(), 'inflated' in got.stderr


def cook_convex(points):
    """PhysX convex stream for a hull's points (body space). Returns (bytes, inflated)."""
    P = np.asarray(points, '<f4').reshape(-1, 3)
    try:
        return _run('convex', P.tobytes())
    except RuntimeError as e:
        lo, hi = (P.min(0).round(2).tolist(), P.max(0).round(2).tolist()) if len(P) else ([], [])
        raise RuntimeError('%s (%d points, bounds %s..%s)' % (e, len(P), lo, hi)) from None


def hull(points):
    """The hull PhysX builds from `points`: (vertices (N,3), [polygon vertex index lists])."""
    stream, _inf = cook_convex(points)
    raw, _ = _run('hull', stream)
    nv = struct.unpack_from('<I', raw, 0)[0]
    verts = np.frombuffer(raw, '<f4', nv * 3, 4).reshape(-1, 3).copy()
    o = 4 + 12 * nv
    npoly = struct.unpack_from('<I', raw, o)[0]
    o += 4
    polys = []
    for _ in range(npoly):
        n = struct.unpack_from('<I', raw, o)[0]
        polys.append(list(struct.unpack_from('<%dI' % n, raw, o + 4)))
        o += 4 + 4 * n
    return verts, polys


def cook_trimesh(vertices, triangles, materials=None, flip=True):
    """PhysX triangle-mesh stream. UStaticMesh always sets bFlipNormals (its winding is
    clockwise); per-triangle material indices are the section MaterialIndex."""
    v = np.asarray(vertices, '<f4').reshape(-1, 3)
    t = np.asarray(triangles, '<u4').reshape(-1, 3)
    payload = struct.pack('<II', len(v), len(t)) + v.tobytes() + t.tobytes()
    if materials is not None:
        payload += np.asarray(materials, '<u2').tobytes()
    return _run('trimesh', payload, ('flip',) if flip else ())[0]


def blob(convex=(), trimesh=None, mirrored=True):
    """The whole PhysXPC blob. convex: list of (N,3) hulls in body space;
    trimesh: (vertices, triangles, materials) or None."""
    out = bytearray(b'\1')
    out += struct.pack('<iii', len(convex), len(convex) if mirrored else 0,
                       1 if trimesh is not None else 0)
    inflated = []
    for flip in ((False, True) if mirrored else (False,)):
        for hull in convex:
            pts = np.asarray(hull, np.float32).reshape(-1, 3).copy()
            if flip:
                pts[:, 0] *= -1
            data, inf = cook_convex(pts)
            inflated.append(inf)
            out += b'\1' + data
    if trimesh is not None:
        out += cook_trimesh(*trimesh)
    out += EMPTY_UV_INFO
    return bytes(out), any(inflated)


def parse_blob(data):
    """Counts and stream tags of a PhysXPC blob (a sanity reader, not a decoder)."""
    le, nc, nm, nt = struct.unpack_from('<Biii', data, 0)
    return {'little_endian': le, 'convex': nc, 'mirrored': nm, 'trimesh': nt,
            'convex_streams': data.count(b'CVXM'), 'mesh_streams': data.count(b'MESH'),
            'size': len(data)}


# -- verification against shipped body setups ------------------------------------------

def _quat_matrix(q):
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def shipped_inputs(path):
    """What UBodySetup::GetCookInfo feeds the cooker for a shipped mesh, and its blob."""
    from riftlord.formats.omd3 import omd3smwrite as SW
    from riftlord.core import uetags
    t = SW.Template(path)
    bs = t.body_setup()
    props = bs['props']
    agg = uetags.get(props, 'AggGeom')
    hulls = []
    if agg is not None:
        conv = uetags.get(agg.value, 'ConvexElems')
        for elem in (conv.value[2] if conv else []):
            vd = uetags.get(elem, 'VertexData').value[2]
            pts = np.frombuffer(b''.join(vd), '<f4').reshape(-1, 3).astype(np.float64)
            tr = uetags.get(elem, 'Transform')
            q, tl, sc = (0, 0, 0, 1), np.zeros(3), np.ones(3)
            if tr is not None:
                for f in tr.value:
                    if f.name == 'Rotation':
                        q = struct.unpack('<4f', f.value)
                    elif f.name == 'Translation':
                        tl = np.array(struct.unpack('<3f', f.value))
                    elif f.name == 'Scale3D':
                        sc = np.array(struct.unpack('<3f', f.value))
            pts = (_quat_matrix(q) @ (pts * sc).T).T + tl
            hulls.append(pts.astype(np.float32))
    ctf = uetags.get(props, 'CollisionTraceFlag')
    ctf = ctf.value if ctf else 'CTF_UseDefault'
    tri = None
    if ctf != 'CTF_UseSimpleAsComplex':
        tri = t.collision_trimesh()
    if ctf == 'CTF_UseComplexAsSimple':
        hulls = []
    return hulls, tri, bs['blob']


def verify(count=40, stride=None):
    import json
    from riftlord.formats.omd3 import omd3smwrite as SW
    paths = SW.all_static_meshes()
    stride = stride or max(1, len(paths) // count)
    same = total = 0
    rows = []
    for p in paths[::stride][:count]:
        try:
            hulls, tri, shipped = shipped_inputs(p)
        except Exception as e:                       # noqa: BLE001
            rows.append((p, 'skip: %s' % e))
            continue
        if shipped is None:
            rows.append((p, 'no cooked data'))
            continue
        mine, _inf = blob(hulls, tri)
        total += 1
        ok = mine == shipped
        same += ok
        rows.append((p, 'identical' if ok else 'differs (%d vs %d bytes)' % (len(mine), len(shipped))))
    for r in rows:
        print('%-90s %s' % r)
    print('byte-identical PhysXPC blobs: %d/%d' % (same, total))
    return same, total


if __name__ == '__main__':
    if sys.argv[1:2] == ['build']:
        print(build_tool(force=True))
    elif sys.argv[1:2] == ['verify']:
        verify(int(sys.argv[2]) if len(sys.argv) > 2 else 40)
