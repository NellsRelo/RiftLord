"""Build the recastgen binaries: from UE 4.27's Recast, and from open-source Recast.

    python3 recastgen/build.py [--engine /path/to/UnrealEngine-4.27.2-release]
    python3 recastgen/build.py --upstream

UE: the engine's Recast needs only CoreMinimal.h, which a small shim stands in for; the
binary (~/.cache/rift-lord/recastgen/recastgen) is for local use, not for shipping.
Upstream: recastnavigation UPSTREAM_TAG (zlib licence, shippable), its tagged archive
unpacked into ~/.cache/rift-lord/recastgen/src; binary recastgen-upstream (tools/build.py
builds and bundles it; --out).
"""

import argparse
import glob
import os
import shutil
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = '/mnt/2tb_pro_a/UnrealEngine-4.27.2-release'
OUT = os.path.expanduser('~/.cache/rift-lord/recastgen')
UPSTREAM_URL = 'https://github.com/recastnavigation/recastnavigation.git'
UPSTREAM_TAG = 'v1.6.0'
UPSTREAM_DIR = os.path.join(OUT, 'src', 'recastnavigation')

#: What the Recast sources use of CoreMinimal.h.
SHIM = r'''#pragma once
#include <cmath>
#include <cstring>
#include <cstdlib>
#include <cstdint>
#include <cfloat>
#include <cassert>
#include <cstdarg>
#include <cstdio>
#include <new>
#define NAVMESH_API
#define FORCEINLINE inline
typedef int32_t int32;
typedef uint32_t uint32;
#define DECLARE_LOG_CATEGORY_EXTERN(a,b,c)
#define DEFINE_LOG_CATEGORY(a)
#define UE_LOG(...)
#define TEXT(x) x
#define check(x) assert(x)
#define ensure(x) (x)
#define CA_ASSUME(x)
#define CA_SUPPRESS(x)
#define WITH_NAVMESH_CLUSTER_LINKS 0
struct FCStringAnsi { static int GetVarArgs(char* d, size_t n, const char* f, va_list a)
    { return vsnprintf(d, n, f, a); } };
'''


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--engine', default=ENGINE)
    ap.add_argument('--upstream', action='store_true', help='build from open-source Recast')
    ap.add_argument('--out', default=OUT, help='where the upstream build goes (default %s)' % OUT)
    args = ap.parse_args()
    if args.upstream:
        return build_upstream(args.out)
    nav = os.path.join(args.engine, 'Engine', 'Source', 'Runtime', 'Navmesh')
    sources = sorted(glob.glob(os.path.join(nav, 'Private', 'Recast', '*.cpp')))
    if not sources:
        raise SystemExit('no Recast sources under %s' % nav)
    work = tempfile.mkdtemp(dir='/mnt/2tb_pro_a/tmp' if os.path.isdir('/mnt/2tb_pro_a/tmp') else None)
    try:
        with open(os.path.join(work, 'CoreMinimal.h'), 'w') as fh:
            fh.write(SHIM)
        os.makedirs(OUT, exist_ok=True)
        cmd = ['g++', '-O2', '-std=c++17', '-w', '-I' + work, '-I' + os.path.join(nav, 'Public'),
               os.path.join(HERE, 'recastgen.cpp')] + sources + ['-o', os.path.join(OUT, 'recastgen')]
        subprocess.run(cmd, check=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(os.path.join(OUT, 'recastgen'))


#: The tagged source archive (fetched without git).
UPSTREAM_ARCHIVE = ('https://codeload.github.com/recastnavigation/recastnavigation/tar.gz/'
                    'refs/tags/%s' % UPSTREAM_TAG)


def upstream_source():
    """recastnavigation UPSTREAM_TAG's source folder, downloaded and unpacked once."""
    if os.path.isdir(os.path.join(UPSTREAM_DIR, 'Recast', 'Source')):
        return UPSTREAM_DIR
    import tarfile
    import urllib.request
    os.makedirs(os.path.dirname(UPSTREAM_DIR), exist_ok=True)
    archive = UPSTREAM_DIR + '-%s.tar.gz' % UPSTREAM_TAG
    if not os.path.exists(archive):
        urllib.request.urlretrieve(UPSTREAM_ARCHIVE, archive + '.part')
        os.replace(archive + '.part', archive)
    unpack = UPSTREAM_DIR + '.part'
    shutil.rmtree(unpack, ignore_errors=True)
    with tarfile.open(archive) as t:
        t.extractall(unpack, filter='data')
    inner = [os.path.join(unpack, n) for n in os.listdir(unpack)][0]
    os.replace(inner, UPSTREAM_DIR)
    shutil.rmtree(unpack, ignore_errors=True)
    return UPSTREAM_DIR


def build_upstream(out_dir=OUT):
    """recastgen-upstream (and its licence) in `out_dir`, the C++ runtime linked in."""
    src = upstream_source()
    sources = sorted(glob.glob(os.path.join(src, 'Recast', 'Source', '*.cpp')))
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, 'recastgen-upstream')
    subprocess.run(['g++', '-O2', '-std=c++17', '-w', '-static-libstdc++', '-static-libgcc',
                    '-I' + os.path.join(src, 'Recast', 'Include'),
                    os.path.join(HERE, 'recastgen.cpp')] + sources + ['-o', out], check=True)
    licence = os.path.join(src, 'License.txt')
    if os.path.isfile(licence):
        shutil.copy2(licence, os.path.join(out_dir, 'LICENSE-recastnavigation.txt'))
    print(out)


if __name__ == '__main__':
    main()
