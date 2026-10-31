"""Build Intel's ISPC Texture Compressor (BC1/BC3/BC6H/BC7/ASTC) as a shared library.

    python3 native/ispctexcomp/build.py [--windows]

Source: github.com/GameTechDev/ISPCTextureCompressor at SOURCE_COMMIT (MIT licence,
shipped beside the library as LICENSE-ispc_texcomp.txt). Compiler: ispc SOURCE_ISPC
(BSD-3, a build tool only). Both are downloaded once into CACHE. Output:
CACHE/out/libispc_texcomp.so (Linux) and, with --windows, ispc_texcomp.dll (llvm-mingw,
the one tools/build.py downloads).
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
ISPC_URL = ('https://github.com/ispc/ispc/releases/download/%s/ispc-%s-linux.tar.gz'
            % (SOURCE_ISPC, SOURCE_ISPC))
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
    exe = glob.glob(os.path.join(root, '*', 'bin', 'ispc'))
    if not exe:
        with tarfile.open(archive) as t:
            t.extractall(root, filter='data')
        exe = glob.glob(os.path.join(root, '*', 'bin', 'ispc'))
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
    src = sources()
    work = os.path.join(CACHE, 'work-' + ('windows' if windows else 'linux'))
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    objs = kernels(src, work, 'windows' if windows else 'linux')
    tc = os.path.join(src, 'ispc_texcomp')
    os.makedirs(OUT, exist_ok=True)
    cpp = [os.path.join(tc, 'ispc_texcomp.cpp'), os.path.join(tc, 'ispc_texcomp_astc.cpp')]
    if windows:
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), 'tools'))
        import build as app_build
        clang = os.path.join(app_build.llvm_mingw(), 'x86_64-w64-mingw32-clang++')
        out = os.path.join(OUT, 'ispc_texcomp.dll')
        run([clang, '-O2', '-shared', '-I', work, '-o', out] + cpp + objs +
            [os.path.join(tc, 'ispc_texcomp.def'), '-static'])
    else:
        out = os.path.join(OUT, 'libispc_texcomp.so')
        run(['g++', '-O2', '-fPIC', '-shared', '-I', work, '-o', out] + cpp + objs)
    shutil.copy2(os.path.join(src, 'license.txt'), os.path.join(OUT, 'LICENSE-ispc_texcomp.txt'))
    print(out)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--windows', action='store_true')
    args = ap.parse_args()
    build(args.windows)


if __name__ == '__main__':
    main()
