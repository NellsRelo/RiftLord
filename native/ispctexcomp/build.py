"""Build Intel's ISPC Texture Compressor (BC1/BC3/BC6H/BC7/ASTC) as a shared library.

    python3 native/ispctexcomp/build.py

Source: github.com/GameTechDev/ISPCTextureCompressor at SOURCE_COMMIT (MIT licence,
shipped beside the library as LICENSE-ispc_texcomp.txt). Compiler: ispc SOURCE_ISPC
(BSD-3, a build tool only), the release for this host. Both are downloaded once into CACHE.
Output: CACHE/out/libispc_texcomp.so on Linux (g++), ispc_texcomp.dll on Windows (MSVC's
cl, through vcvars64.bat when cl is not on PATH). The Linux ispc release targets only Linux,
so the .dll is built on Windows. Windows uses cl rather than clang++ because MSVC's headers
refuse a clang older than they expect (windows-latest, 2026-11: MSVC 14.52 wants clang 22,
the runner's LLVM is 20); cl always matches its own headers. /MT links the C runtime in, as
clang++ did, so the .dll needs no Visual C++ runtime installed.
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
    print('$', cmd if isinstance(cmd, str) else ' '.join(cmd), flush=True)
    subprocess.check_call(cmd, cwd=cwd)


def vcvars():
    """The newest Visual Studio's vcvars64.bat with the C++ tools, by vswhere."""
    where = os.path.join(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'),
                         'Microsoft Visual Studio', 'Installer', 'vswhere.exe')
    path = ''
    if os.path.isfile(where):
        path = subprocess.run([where, '-latest', '-products', '*', '-requires',
                               'Microsoft.VisualStudio.Component.VC.Tools.x86.x64',
                               '-property', 'installationPath'],
                              capture_output=True, text=True).stdout.strip()
    bat = os.path.join(path, 'VC', 'Auxiliary', 'Build', 'vcvars64.bat') if path else ''
    if not os.path.isfile(bat):
        raise SystemExit('ispc_texcomp.dll needs MSVC: no cl on PATH and no Visual Studio '
                         'with the C++ tools found by vswhere')
    return bat


def msvc(cmd):
    """`cmd` (a cl command list) as run here: itself when cl is on PATH, else one cmd.exe
    string through vcvars64.bat. A string, not a list: subprocess would write the line's
    quotes as \\" and cmd.exe does not read those; /s strips only the outer pair."""
    if shutil.which('cl'):
        return cmd
    return 'cmd.exe /d /s /c "call "%s" >nul && %s"' % (vcvars(), subprocess.list2cmdline(cmd))


def kernels(src, work, target_os):
    """Compile both kernels for TARGETS; returns the object files (.obj on Windows, which
    MSVC's linker takes without a warning)."""
    tc = os.path.join(src, 'ispc_texcomp')
    ext = '.obj' if target_os == 'windows' else '.o'
    objs = []
    for name in ('kernel', 'kernel_astc'):
        obj = os.path.join(work, name + ext)
        run([ispc(), '-O2', '--arch=x86-64', '--target=' + TARGETS, '--opt=fast-math',
             '--pic', '--target-os=' + target_os, os.path.join(tc, name + '.ispc'),
             '-o', obj, '-h', os.path.join(work, name + '_ispc.h')])
        objs += [obj] + sorted(glob.glob(os.path.join(work, name + '_*' + ext)))
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
        run(msvc(['cl', '/nologo', '/O2', '/EHsc', '/MT', '/LD', '/I', work] + cpp + objs +
                 ['/Fe:' + out, '/link', '/DEF:' + os.path.join(tc, 'ispc_texcomp.def')]),
            cwd=work)
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
