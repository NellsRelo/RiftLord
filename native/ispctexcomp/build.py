"""Build Intel's ISPC Texture Compressor (BC1/BC3/BC6H/BC7/ASTC) as a shared library.

    python3 native/ispctexcomp/build.py

Source: github.com/GameTechDev/ISPCTextureCompressor at SOURCE_COMMIT (MIT licence,
shipped beside the library as LICENSE-ispc_texcomp.txt). Compiler: ispc SOURCE_ISPC
(BSD-3, a build tool only), the release for this host. Both are downloaded once into CACHE.
Output: CACHE/out/libispc_texcomp.so on Linux (g++), ispc_texcomp.dll on Windows (clang++).
The Linux ispc release targets only Linux, so the .dll is built on Windows.
"""

import argparse
import glob
import os
import shutil
import subprocess
import tarfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.expanduser('~/.cache/rift-lord/ispctc')
SOURCE_COMMIT = '79ddbc90334fc31edd438e68ccb0fe99b4e15aab'
SOURCE_URL = ('https://raw.githubusercontent.com/GameTechDev/ISPCTextureCompressor/%s/'
              % SOURCE_COMMIT)
SOURCE_FILES = ['ispc_texcomp/ispc_texcomp.cpp', 'ispc_texcomp/ispc_texcomp_astc.cpp',
                'ispc_texcomp/ispc_texcomp.h', 'ispc_texcomp/ispc_texcomp.def',
                'ispc_texcomp/kernel.ispc', 'ispc_texcomp/kernel_astc.ispc', 'license.txt']
SOURCE_ISPC = 'v1.32.0'
#: The compiler for this host: the Linux release targets only Linux, so the Windows library
#: is built on Windows with the Windows release.
ISPC_URL = ('https://github.com/ispc/ispc/releases/download/%s/ispc-%s-%s'
            % (SOURCE_ISPC, SOURCE_ISPC, 'windows.zip' if os.name == 'nt' else 'linux.tar.gz'))
#: Instruction sets compiled in; the library picks the best one the CPU has.
TARGETS = 'sse2,sse4,avx,avx2'
OUT = os.path.join(CACHE, 'out')


def fetch(url, dest):
    if not os.path.exists(dest):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        print('downloading %s' % url, flush=True)
        urllib.request.urlretrieve(url, dest + '.part')
        os.replace(dest + '.part', dest)
    return dest


def sources():
    src = os.path.join(CACHE, 'src', SOURCE_COMMIT[:12])
    for rel in SOURCE_FILES:
        fetch(SOURCE_URL + rel, os.path.join(src, rel))
    return src


def ispc():
    archive = fetch(ISPC_URL, os.path.join(CACHE, os.path.basename(ISPC_URL)))
    root = os.path.join(CACHE, 'ispc-' + SOURCE_ISPC)
    name = 'ispc.exe' if os.name == 'nt' else 'ispc'
    exe = glob.glob(os.path.join(root, '*', 'bin', name))
    if not exe:
        if archive.endswith('.zip'):
            import zipfile
            with zipfile.ZipFile(archive) as z:
                z.extractall(root)
        else:
            with tarfile.open(archive) as t:
                t.extractall(root, filter='data')
        exe = glob.glob(os.path.join(root, '*', 'bin', name))
    return exe[0]


def run(cmd, cwd=None):
    print('$', ' '.join(cmd), flush=True)
    subprocess.check_call(cmd, cwd=cwd)


def kernels(src, work, target_os):
    """Compile both kernels for TARGETS; returns the object files."""
    tc = os.path.join(src, 'ispc_texcomp')
    objs = []
    for name in ('kernel', 'kernel_astc'):
        obj = os.path.join(work, name + '.o')
        run([ispc(), '-O2', '--arch=x86-64', '--target=' + TARGETS, '--opt=fast-math',
             '--pic', '--target-os=' + target_os, os.path.join(tc, name + '.ispc'),
             '-o', obj, '-h', os.path.join(work, name + '_ispc.h')])
        objs += [obj] + sorted(glob.glob(os.path.join(work, name + '_*.o')))
    return objs


def build(windows=False):
    if windows != (os.name == 'nt'):
        raise SystemExit('ispc builds only for its own host: run this on Windows for '
                         'ispc_texcomp.dll, on Linux for libispc_texcomp.so')
    src = sources()
    work = os.path.join(CACHE, 'work-' + ('windows' if windows else 'linux'))
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    objs = kernels(src, work, 'windows' if windows else 'linux')
    tc = os.path.join(src, 'ispc_texcomp')
    os.makedirs(OUT, exist_ok=True)
    cpp = [os.path.join(tc, 'ispc_texcomp.cpp'), os.path.join(tc, 'ispc_texcomp_astc.cpp')]
    if windows:
        out = os.path.join(OUT, 'ispc_texcomp.dll')
        run(['clang++', '-O2', '-shared', '-I', work, '-o', out] + cpp + objs +
            ['-Wl,/DEF:' + os.path.join(tc, 'ispc_texcomp.def')])
    else:
        out = os.path.join(OUT, 'libispc_texcomp.so')
        run(['g++', '-O2', '-fPIC', '-shared', '-I', work, '-o', out] + cpp + objs)
    shutil.copy2(os.path.join(src, 'license.txt'), os.path.join(OUT, 'LICENSE-ispc_texcomp.txt'))
    print(out)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.parse_args()
    build(os.name == 'nt')


if __name__ == '__main__':
    main()
