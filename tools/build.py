"""Build Rift Lord -- the Map Editor, the Onslaught Editor and Missions -- into one
application: the Python server frozen by PyInstaller, inside a native window (Rust,
shell/window) that starts it, shows it, and stops it when closed.

    python3 tools/build.py            # build all three UIs, then both halves
    python3 tools/build.py --skip-ui  # reuse the last UI builds
    python3 tools/build.py --windows  # on Linux: RiftLord.exe, under Wine

PyInstaller does not cross-compile. Run this on Windows for RiftLord.exe and on Linux
for RiftLord; on Linux, `--windows` builds the .exe too, under Wine.

The binaries go to DIST (RiftLordEditor/dist); PyInstaller's work to OUTPUT; venv, staged
UIs and Rust targets to BUILD_CACHE.
"""

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
#: Build tools and caches (venv, staged UIs, Rust targets, Wine, llvm-mingw), outside the
#: game folder: UE4SS walks Binaries/Win64 on every start.
BUILD_CACHE = os.path.join(os.path.expanduser('~'), '.cache', 'rift-lord')
#: PyInstaller's work folders: the scratch disk when there is one. RIFTLORD_BUILD_OUT
#: moves them.
OUTPUT = os.environ.get('RIFTLORD_BUILD_OUT') or (
    '/mnt/2tb_pro_a/tmp/riftlord-editor' if os.path.isdir('/mnt/2tb_pro_a/tmp')
    else os.path.join(BUILD_CACHE, 'editor'))
#: The built binaries.
DIST = os.path.join(ROOT, 'dist')
#: The app (the window, which carries the server) and the frozen server inside it.
NAME = 'RiftLord'
SERVER = 'RiftLordServer'
APP = os.path.join(ROOT, 'shell', 'window')
#: PyInstaller's work folder, one per platform so a Windows build under Wine and a Linux
#: build can run side by side.
WORK = os.path.join(OUTPUT, 'build-windows' if os.name == 'nt' else 'build')
LAUNCHER = os.path.join(ROOT, 'shell', 'launcher')
#: The build Python's venv (requirements-build.txt).
VENV = os.path.join(BUILD_CACHE, 'editor-buildenv')
#: Where each UI is built: its sources and web/shared copied in, node_modules kept there.
WEB_STAGE = os.path.join(BUILD_CACHE, 'editor-web')
#: The Python package frozen, and the script that starts it.
PACKAGE = os.path.join(ROOT, 'riftlord')
ENTRY = os.path.join(PACKAGE, '__main__.py')

#: Each page, built into its own dist and bundled at web/<ui>/dist (gamepath.web_dist).
UIS = {ui: os.path.join(ROOT, 'web', ui) for ui in ('shell', 'mapeditor', 'onslaught')}

#: Data folders inside subpackages, carried whole at the same place (riftlord/<folder>):
#: the Fab converter reads its own beside its modules.
PACKAGE_DATA = ['fab/data']
#: The terrain kit sources its keys digest (riftlord.terrainkit.build.code_hash,
#: sources.paks_stamp), as paths under PACKAGE.
TERRAIN_CODE = ['terrainkit/build.py', 'terrainkit/landscape.py', 'terrainkit/mics.py', 'terrainkit/paint.py',
                'terrainkit/sources.py', 'terrainkit/tiles.py']


def fab_code_files():
    """The source files the Fab converter and the terrain kits digest for their cache keys
    and build ids (riftlord.fab.library BAKE_CODE, MESH_CODE and PIECE_CODE; TERRAIN_CODE),
    as paths under PACKAGE: the frozen app carries them as text at the same places, since
    its modules are compiled."""
    sys.path.insert(0, ROOT)
    from riftlord.fab import library
    from riftlord.build import texpatch
    fab = os.path.join(PACKAGE, 'fab')
    return sorted({os.path.relpath(os.path.normpath(os.path.join(fab, rel)), PACKAGE)
                   for rel in library.BAKE_CODE + library.MESH_CODE + library.PIECE_CODE} |
                  set(texpatch.ENCODER_SOURCES) |          # texpatch.encoder_id digests them
                  set(TERRAIN_CODE))


def types_code_files():
    """The sources Setup's keys digest (riftlord.types.setup.CODE), as [(source, bundle
    folder)]: the frozen app carries them as text where setup._code_hash reads them
    (riftlord/types, and bpkit's beside its modules), so its keys are a source run's."""
    sys.path.insert(0, ROOT)
    from riftlord.types import setup
    out = set()
    for names in setup.CODE.values():
        for name in names:
            if name.startswith('bpkit:'):
                out.add((os.path.join(BPKIT, name[6:]), 'bpkit'))
            else:
                out.add((os.path.join(PACKAGE, 'types', name), os.path.join('riftlord', 'types')))
    return sorted(out)


#: Modules that only build, test or probe, left out of the frozen app.
DEV_MODULES = {'riftlord.mapeditor.selftest', 'riftlord.onslaught.selftest',
               'riftlord.onslaught.savetest', 'riftlord.build.minimap.minimapscore',
               'riftlord.missions.records'}
#: Third-party modules the code imports inside functions.
THIRD_PARTY = ['numpy', 'PIL', 'PIL.Image', 'yaml']


#: Optional native libraries carried in the app's `native/` when built (with their
#: licences): Intel's ISPC Texture Compressor (native/ispctexcomp/build.py; MIT).
NATIVE = os.path.join(BUILD_CACHE, 'ispctc', 'out')


def native_libraries():
    """The native libraries and tools carried in the app's `native/` for this platform,
    with their licences: the ISPC Texture Compressor when built, and the tools compiled
    here (native_tools)."""
    out = []
    lib = 'ispc_texcomp.dll' if os.name == 'nt' else 'libispc_texcomp.so'
    path = os.path.join(NATIVE, lib)
    if os.path.isfile(path):
        out += [path, os.path.join(NATIVE, 'LICENSE-ispc_texcomp.txt')]
    else:
        print('ISPC Texture Compressor: not built (native/ispctexcomp/build.py); not carried')
    return out + native_tools()


#: Where native_tools compiles into.
TOOLS_OUT = os.path.join(WORK, 'native-tools')


#: pxcook is never carried, on any platform (no Epic engine code in the release): its
#: source is (PXCOOK_SOURCE), and each user's Rift Lord compiles it on first use against
#: their own UE 4.27 (riftlord/formats/omd3/omd3physx.py build_tool).
PXCOOK_SOURCE = os.path.join('native', 'pxcook', 'pxcook.cpp')


def native_tools():
    """Tools the app runs, compiled for this platform at build time (Linux):
    recastgen-upstream (walkable-graph regeneration, recastnavigation, zlib licence).
    Returns their paths and licences; one that cannot be built is reported, not carried."""
    if os.name == 'nt':
        print('recastgen: not built for Windows yet')
        return []
    sys.path.insert(0, ROOT)
    out = []
    os.makedirs(TOOLS_OUT, exist_ok=True)
    recast = os.path.join(ROOT, 'native', 'recastgen', 'build.py')
    got = subprocess.run([sys.executable, recast, '--upstream', '--out', TOOLS_OUT],
                         capture_output=True, text=True)
    binary = os.path.join(TOOLS_OUT, 'recastgen-upstream')
    if got.returncode == 0 and os.path.isfile(binary):
        out += [binary] + [p for p in [os.path.join(TOOLS_OUT, 'LICENSE-recastnavigation.txt')]
                           if os.path.isfile(p)]
    else:
        print('recastgen-upstream: not built (%s); not carried: walkable-graph regeneration '
              'unavailable' % (got.stdout + got.stderr).strip()[-300:])
    return out


#: The notices file, written beside the binaries (and copied to docs/) from what the build
#: bundled (tools/notices.py); the build fails on a component without an allowed licence.
NOTICES = 'THIRD-PARTY-NOTICES.txt'


def third_party_notices(carried):
    """Run tools/notices.py on this build's analysis: DIST/NOTICES (+ .json) and a copy in
    docs/. SystemExit naming each component the licence gate refuses."""
    sys.path.insert(0, HERE)
    import notices
    out = os.path.join(DIST, NOTICES)
    argv = [os.path.join(WORK, SERVER), out, '--zip', os.path.join(WORK, 'baker-packages')]
    argv += sum((['--web', os.path.join(web, 'dist')] for web in UIS.values()), [])
    argv += sum((['--native', p] for p in carried if not p.endswith('.txt')), [])
    if os.name != 'nt':                       # the window's crates (no cargo under Wine)
        argv += ['--cargo', os.path.join(APP, 'Cargo.toml'), '--target', 'x86_64-unknown-linux-gnu']
    if notices.main(argv) != 0:
        raise SystemExit('third-party notices: a bundled component has no allowed licence '
                         '(see the NOTICE PROBLEM lines above; %s.json)' % out)
    shutil.copy2(out, os.path.join(ROOT, 'docs', NOTICES))
    return out


#: bpkit's type data is never carried (parts are derived from UE 4.27's headers): each
#: user's Rift Lord builds it from their own UE 4.27 and game (riftlord.types, the Setup
#: page).


def package_modules():
    """Every module of the riftlord package, less DEV_MODULES (hidden imports: some are
    reached only by name or from a function PyInstaller does not follow)."""
    out = []
    for base, dirs, files in os.walk(PACKAGE):
        dirs[:] = sorted(d for d in dirs if d != '__pycache__' and
                         os.path.isfile(os.path.join(base, d, '__init__.py')))
        for f in sorted(files):
            if not f.endswith('.py'):
                continue
            rel = os.path.relpath(os.path.join(base, f), ROOT)[:-3].replace(os.sep, '.')
            name = rel[:-len('.__init__')] if rel.endswith('.__init__') else rel
            if name not in DEV_MODULES:
                out.append(name)
    return out


#: The light baker's sources, bundled as data beside the frozen modules as well: the
#: bake cache keys hash them (lightbake._source_hash, lightbaker._source_digest).
BAKER_SOURCES = ['lightbaker.py', 'lighttrace.py', 'lightscene.py', 'lightcodec.py',
                 'lightmaps.py']
#: Where they are, and where the frozen app keeps them.
BAKER_DIR = 'riftlord/build/lighting'
#: The baker's native packages, frozen in whole when the build Python has them: OptiX
#: tracing through Mitsuba/Dr.Jit, and the OpenGL fallback through moderngl. Without
#: both of the first pair and the second, the app builds without baking.
BAKER_PACKAGES = {'optix': ['mitsuba', 'drjit'], 'opengl': ['moderngl', 'glcontext']}
#: Mitsuba variants the baker uses (lighttrace.VARIANT, and scalar_rgb, which Mitsuba
#: imports first); the other twelve are left out of the bundle.
MITSUBA_VARIANTS = ('cuda_ad_rgb', 'scalar_rgb')

#: Data files the code reads (gamepath.data), under riftlord/data at these paths.
DATA = ['kit-catalogue.json', 'omd3-actor-meshes.json', 'omd3-actor-parts.json',
        'omd3-actors.json', 'omd3-meshes.json', 'omd3-skies.json', 'liquid-lights.json',
        'kit-geometry.json',
        # navregen's walkable-graph regeneration (omd3 collision hulls), navsurffloors'
        # mesh roles and stairfit's measured stair ascents
        'omd3-hulls.json', 'mesh-map.json', 'stair-ascent.json',
        # the effects New Object offers (riftlord/fxsurvey.py) and kit pieces as their
        # donor placements build them (riftlord/kitdonors.py)
        'riftlord/fx-donors.json', 'kit-donor-parts.json',
        # controller settings' donors and choices (riftlord/controllers.py --survey)
        'controller-donors.json',
        # foliage brush and rock scatter defaults, added foliage cull distances
        # (riftlord/compounds.py, foliageadd.py --survey)
        'riftlord/decor-brush.json', 'riftlord/foliage-cull.json',
        # the mined compounds (riftlord/compoundmine.py --write)
        'compounds.json',
        # the silence dialogue lines are cut from (riftlord/build/world/captions.py)
        'riftlord/caption-silence.ogg']


def venv_python():
    exe = os.path.join(VENV, 'Scripts' if os.name == 'nt' else 'bin',
                       'python.exe' if os.name == 'nt' else 'python')
    return exe if os.path.exists(exe) else None


def check_tools(skip_ui):
    problems = []
    if not skip_ui and shutil.which('npm') is None:
        problems.append('npm is not on PATH (needed to build the UIs; --skip-ui reuses '
                        'the last builds)')
    try:
        subprocess.check_output([sys.executable, '-c', 'import PyInstaller'],
                                stderr=subprocess.STDOUT)
    except (subprocess.CalledProcessError, OSError):
        alt = venv_python()
        problems.append('PyInstaller is not installed for %s.%s' % (
            sys.executable,
            '\n    build with: %s tools/build.py' % alt if alt else
            '\n    python3 -m venv %s\n    %s/bin/pip install -r requirements-build.txt'
            % (VENV, VENV)))
    if problems:
        raise SystemExit('cannot build:\n\n  ' + '\n\n  '.join(problems))


def run(cmd, cwd=None):
    print('$', ' '.join(cmd))
    subprocess.check_call(cmd, cwd=cwd)


#: bpkit as the frozen app carries it: the vendored copy (riftlord.core.bpkitlink.VENDORED,
#: tools/vendor_bpkit.py), found through its folder's parent (a --paths).
BPKIT = os.path.join(ROOT, 'vendor', 'bpkit')


def bpkit_modules():
    """bpkit and its modules, as vendored."""
    return ['bpkit'] + ['bpkit.' + f[:-3] for f in sorted(os.listdir(BPKIT))
                        if f.endswith('.py') and f != '__init__.py']


#: Shared Svelte components, imported by relative path from each UI's sources.
WEBSHARED = os.path.join(ROOT, 'web', 'shared')
#: What a UI's staged copy leaves out.
WEB_SKIP = ('node_modules', 'dist', '.vite')


def _stage_tree(src, dst):
    """Make `dst` a copy of `src` less WEB_SKIP, keeping dst's node_modules."""
    os.makedirs(dst, exist_ok=True)
    for name in os.listdir(dst):
        if name != 'node_modules':
            path = os.path.join(dst, name)
            shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
    for name in os.listdir(src):
        if name in WEB_SKIP:
            continue
        path = os.path.join(src, name)
        if os.path.isdir(path):
            shutil.copytree(path, os.path.join(dst, name),
                            ignore=shutil.ignore_patterns(*WEB_SKIP))
        else:
            shutil.copy2(path, dst)


def build_uis():
    """Build each UI in a staged copy under WEB_STAGE (same relative layout, so the
    webshared imports resolve), then copy its dist back beside its sources."""
    npm = shutil.which('npm')
    _stage_tree(WEBSHARED, os.path.join(WEB_STAGE, os.path.relpath(WEBSHARED, ROOT)))
    for web in UIS.values():
        stage = os.path.join(WEB_STAGE, os.path.relpath(web, ROOT))
        _stage_tree(web, stage)
        if not os.path.isdir(os.path.join(stage, 'node_modules')):
            run([npm, 'ci'], cwd=stage)
        run([npm, 'run', 'build'], cwd=stage)
        shutil.rmtree(os.path.join(web, 'dist'), ignore_errors=True)
        shutil.copytree(os.path.join(stage, 'dist'), os.path.join(web, 'dist'))


def build_server(onefile=True):
    """Freeze the Python side -- both editors' servers, Missions and the shell page --
    into `dist/RiftLordServer[.exe]`. Nobody runs it directly: the app window carries it
    and starts it with `--sidecar`."""
    sep = ';' if os.name == 'nt' else ':'
    cmd = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
           '--name', SERVER,
           '--distpath', DIST,
           '--workpath', WORK,
           '--specpath', WORK,
           '--paths', ROOT,
           '--paths', os.path.dirname(BPKIT),
           # a console program: its output is the app window's log, and the window
           # starts it with no console of its own
           '--console']
    for ui, web in UIS.items():
        built = os.path.join(web, 'dist')
        if not os.path.isfile(os.path.join(built, 'index.html')):
            raise SystemExit('no UI build at %s' % built)
        cmd += ['--add-data', '%s%s%s' % (built, sep, os.path.join('web', ui, 'dist'))]
    for rel in DATA:
        src = os.path.join(PACKAGE, 'data', rel)
        if not os.path.exists(src):
            raise SystemExit('data file missing: %s' % rel)
        cmd += ['--add-data', '%s%s%s' % (src, sep, os.path.join('riftlord', 'data',
                                                                  os.path.dirname(rel)))]
    cmd += ['--add-data', '%s%s%s' % (os.path.join(ROOT, PXCOOK_SOURCE), sep,
                                      os.path.dirname(PXCOOK_SOURCE))]
    for rel in fab_code_files():
        cmd += ['--add-data', '%s%s%s' % (os.path.join(PACKAGE, rel), sep,
                                          os.path.join('riftlord', os.path.dirname(rel)))]
    for src, folder in types_code_files():
        if not os.path.isfile(src):
            raise SystemExit('Setup key source missing: %s' % src)
        cmd += ['--add-data', '%s%s%s' % (src, sep, folder)]
    for folder in PACKAGE_DATA:
        src = os.path.join(PACKAGE, folder)
        if not os.path.isdir(src):
            raise SystemExit('package data folder missing: %s' % folder)
        cmd += ['--add-data', '%s%s%s' % (src, sep, os.path.join('riftlord', folder))]
    for mod in package_modules() + bpkit_modules() + THIRD_PARTY:
        cmd += ['--hidden-import', mod]
    for rel in BAKER_SOURCES:
        cmd += ['--add-data', '%s%s%s' % (os.path.join(ROOT, BAKER_DIR, rel), sep, BAKER_DIR)]
    cmd += baker_options()
    carried = native_libraries()
    for src in carried:
        kind = '--add-data' if src.endswith('.txt') else '--add-binary'
        cmd += [kind, '%s%snative' % (src, sep)]
    cmd.append('--onefile' if onefile else '--onedir')
    cmd.append(ENTRY)
    run(cmd)
    out = os.path.join(DIST, SERVER + ('.exe' if os.name == 'nt' else ''))
    if not os.path.isfile(out):
        raise SystemExit('the server build did not produce %s' % out)
    print('\nbuilt %s (%.1f MB)' % (out, os.path.getsize(out) / 1e6))
    third_party_notices(carried)
    return out


#: What a staged baker package leaves out: build headers, stubs, tests, caches.
BAKER_SKIP = ('__pycache__', 'include', 'cmake', 'pkgconfig', 'test', 'tests',
              'mitsuba_stubs', 'moderngl-stubs')


def _stage_package(mod, dest):
    """Copy package `mod` (as the build Python finds it) into `dest`, trimmed: no
    BAKER_SKIP folders, no .pyi, and of Mitsuba only MITSUBA_VARIANTS. Returns the
    stdlib modules its Python files import."""
    import ast
    import importlib.util
    spec = importlib.util.find_spec(mod)
    src = os.path.dirname(spec.origin)
    out = os.path.join(dest, mod)
    std = set()

    def skip(folder, names):
        drop = [n for n in names if n in BAKER_SKIP or n.endswith('.pyi')]
        if mod == 'mitsuba' and os.path.abspath(folder) == os.path.abspath(src):
            for n in names:
                variant = n.split('.')[0]
                if (n.endswith(('.so', '.pyd')) and ('_ad_' in variant or
                        variant.startswith('scalar_')) and variant not in MITSUBA_VARIANTS):
                    drop.append(n)
        return drop
    shutil.copytree(src, out, ignore=skip)
    for folder, _dirs, files in os.walk(out):
        for name in files:
            if not name.endswith('.py'):
                continue
            with open(os.path.join(folder, name), 'rb') as fh:
                try:
                    tree = ast.parse(fh.read())
                except SyntaxError:
                    continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    std.update(a.name for a in node.names)
                elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                    std.add(node.module)
    return {m for m in std if m.split('.')[0] in sys.stdlib_module_names}


def baker_options():
    """PyInstaller options carrying the baker's native packages, and a note saying which
    backends the build carries (printed, and kept as dist/<server>.baker.txt).

    The packages go in as one zip (lightbake.FROZEN_PACKAGES), which the frozen server
    unpacks into the user's cache the first time it bakes (lightbake.frozen_packages),
    rather than through PyInstaller's analysis: collecting Mitsuba's submodules imports
    every one of them, which ran 12 minutes and 6 GB without finishing, and loose .so
    files get their dependencies copied to the bundle's top level, where Mitsuba then
    looks for its plugins and finds none (both 2026-10-16). The zip also keeps the
    server's own start quick: it is one file to unpack. The standard library modules
    the packages' code imports are named as hidden imports."""
    import importlib.util
    import zipfile
    have = {k: all(importlib.util.find_spec(m) for m in mods)
            for k, mods in BAKER_PACKAGES.items()}
    stage = os.path.join(WORK, 'baker-packages')
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    sep = ';' if os.name == 'nt' else ':'
    opts, std = [], set()
    for backend, mods in BAKER_PACKAGES.items():
        for mod in mods:
            opts += ['--exclude-module', mod]
            if have[backend]:
                std |= _stage_package(mod, stage)
    if have['opengl']:
        # moderngl's Python half is a top-level module beside the package
        spec = importlib.util.find_spec('_moderngl')
        shutil.copy2(spec.origin, os.path.join(stage, '_moderngl.py'))
        opts += ['--exclude-module', '_moderngl']
    carried = [k for k in BAKER_PACKAGES if have[k]]
    if carried:
        archive = os.path.join(WORK, 'baker-packages.zip')
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
            for folder, _dirs, files in os.walk(stage):
                for name in sorted(files):
                    path = os.path.join(folder, name)
                    z.write(path, os.path.relpath(path, stage).replace(os.sep, '/'))
        opts += ['--add-data', '%s%s.' % (archive, sep)]
    for mod in sorted(std):
        opts += ['--hidden-import', mod]
    opts += ['--exclude-module', 'pyoidn']        # lightbaker.DENOISE is off
    # readline would bring the system's GPL libreadline (tools/notices.py); nothing uses it
    opts += ['--exclude-module', 'readline']
    note = ('light baker: %s' % ' + '.join(carried) if carried else
            'light baker: NOT carried (the build Python has neither Mitsuba/Dr.Jit nor '
            'moderngl); missions build with the shipped light maps')
    print(note, flush=True)
    os.makedirs(DIST, exist_ok=True)
    with open(os.path.join(DIST, SERVER + ('.exe' if os.name == 'nt' else '') + '.baker.txt'),
              'w') as fh:
        fh.write(note + '\n')
    return opts


def install_binary(built, name):
    """Copy a built binary into dist by writing beside it and renaming over it, which
    works while an older copy is running (copying onto it would not, on Linux)."""
    out = os.path.join(DIST, name)
    part = out + '.part'
    shutil.copy2(built, part)
    os.replace(part, out)
    return out


def build_window(server, cargo='cargo', target_dir=None, env=None, exe=''):
    """Build the app window (shell/window, Rust) around a frozen server, into
    `dist/RiftLord[.exe]`: one file, which unpacks and runs the server itself."""
    target_dir = target_dir or os.path.join(CARGO_TARGETS, 'editor-app')
    run_env = dict(os.environ, **(env or {}))
    run_env['RIFTLORD_SIDECAR'] = run_env.get('RIFTLORD_SIDECAR') or server
    run_env['CARGO_TARGET_DIR'] = run_env.get('CARGO_TARGET_DIR') or target_dir
    cmd = [cargo, 'build', '--release', '--manifest-path',
           run_env.get('RIFTLORD_MANIFEST') or os.path.join(APP, 'Cargo.toml')]
    print('$', ' '.join(cmd))
    subprocess.check_call(cmd, env=run_env)
    built = os.path.join(target_dir, 'release', NAME + exe)
    out = install_binary(built, NAME + exe)
    print('\nbuilt %s (%.1f MB)' % (out, os.path.getsize(out) / 1e6))
    return out


#: The Windows build on Linux. The server is frozen by a Windows Python running in Wine,
#: in its own prefix under BUILD_CACHE so it outlives a session. The window is
#: cross-compiled natively with llvm-mingw (an open-source LLVM MinGW toolchain: linker,
#: dlltool, windres) for Rust's `x86_64-pc-windows-gnullvm` target. Rust's own
#: windows-gnu toolchain was tried under Wine first and cannot build it: the `windows`
#: crates need a dlltool it does not ship.
#: Kept outside the game folder: UE4SS walks Binaries/Win64 recursively, and a Wine
#: prefix's dosdevices/z: link to / sends that walk into an endless loop.
#: Rust's build output; this tree's targets are named editor-*.
CARGO_TARGETS = os.path.join(BUILD_CACHE, 'cargo')
WINE_PREFIX = os.path.join(BUILD_CACHE, 'winebuild')
WIN_PYTHON_VERSION = '3.12.8'
WIN_PYTHON_URL = ('https://www.python.org/ftp/python/%s/python-%s-amd64.exe'
                  % (WIN_PYTHON_VERSION, WIN_PYTHON_VERSION))
WIN_PYTHON = r'C:\Program Files\Python312\python.exe'
WIN_TARGET = 'x86_64-pc-windows-gnullvm'
LLVM_MINGW = 'llvm-mingw-20260922-ucrt-ubuntu-22.04-x86_64'
LLVM_MINGW_URL = ('https://github.com/mstorsjo/llvm-mingw/releases/download/20260922/%s.tar.xz'
                  % LLVM_MINGW)
CROSS = os.path.join(BUILD_CACHE, 'crossbuild')


def wine(*args, check=True, env=None):
    run_env = dict(os.environ, WINEPREFIX=WINE_PREFIX, WINEDEBUG='-all', **(env or {}))
    print('$ wine', ' '.join(args))
    return subprocess.run(['wine'] + list(args), env=run_env, check=check)


def winpath(path):
    """A Linux path as Wine's Windows programs see it."""
    return 'Z:' + os.path.abspath(path).replace('/', '\\')


def win_python_ready():
    return wine(WIN_PYTHON, '-c', 'import PyInstaller, PIL', check=False).returncode == 0


#: What the frozen server needs beyond PyInstaller and Pillow: the converter's numpy and
#: YAML, and the light baker's packages (BAKER_PACKAGES), at the versions the Linux
#: render venv runs.
WIN_PACKAGES = ['numpy==2.5.2', 'pyyaml', 'mitsuba==3.9.1', 'drjit==1.5.0',
                'moderngl==5.13.0', 'glcontext==3.1.0']


def win_packages():
    """Install WIN_PACKAGES into the Windows Python when any is missing."""
    probe = 'import numpy, yaml, mitsuba, drjit, moderngl, glcontext'
    if wine(WIN_PYTHON, '-c', probe, check=False).returncode != 0:
        wine(WIN_PYTHON, '-m', 'pip', 'install', *WIN_PACKAGES)


def build_windows_server(onefile=True):
    """Freeze RiftLordServer.exe with the Windows Python in Wine, installing it the
    first time."""
    if shutil.which('wine') is None:
        raise SystemExit('wine is not on PATH; install it, or build on Windows')
    os.makedirs(WINE_PREFIX, exist_ok=True)
    if not win_python_ready():
        installer = os.path.join(WINE_PREFIX, os.path.basename(WIN_PYTHON_URL))
        if not os.path.exists(installer):
            import urllib.request
            print('downloading %s' % WIN_PYTHON_URL)
            urllib.request.urlretrieve(WIN_PYTHON_URL, installer)
        wine(installer, '/quiet', 'InstallAllUsers=1', 'PrependPath=1', 'Include_test=0',
             'Include_doc=0', 'Include_launcher=0')
        wine(WIN_PYTHON, '-m', 'pip', 'install', '--upgrade', 'pyinstaller', 'pillow')
        if not win_python_ready():
            raise SystemExit('the Windows Python in %s cannot import PyInstaller' % WINE_PREFIX)
    win_packages()
    wine(WIN_PYTHON, os.path.join(HERE, 'build.py'), '--skip-ui', '--server-only',
         '--dist', winpath(DIST), '--work', winpath(os.path.join(OUTPUT, 'build-windows')),
         *([] if onefile else ['--onedir']))
    server = os.path.join(DIST, SERVER + '.exe')
    if not os.path.exists(server):
        raise SystemExit('the Windows server build did not produce %s' % server)
    return server


def llvm_mingw():
    """llvm-mingw's bin folder, downloaded and unpacked the first time."""
    bin_dir = os.path.join(CROSS, LLVM_MINGW, 'bin')
    if not os.path.isdir(bin_dir):
        os.makedirs(CROSS, exist_ok=True)
        archive = os.path.join(CROSS, LLVM_MINGW + '.tar.xz')
        if not os.path.exists(archive):
            import urllib.request
            print('downloading %s' % LLVM_MINGW_URL)
            urllib.request.urlretrieve(LLVM_MINGW_URL, archive)
        run(['tar', 'xJf', archive, '-C', CROSS])
    return bin_dir


def _webview2_loader():
    """WebView2Loader.dll (x64) from the webview2-com-sys crate cargo fetched."""
    import glob
    home = os.environ.get('CARGO_HOME') or os.path.join(os.path.expanduser('~'), '.cargo')
    hits = sorted(glob.glob(os.path.join(home, 'registry', 'src', '*', 'webview2-com-sys-*',
                                         'x64', 'WebView2Loader.dll')))
    if not hits:
        raise SystemExit('WebView2Loader.dll not found in the cargo registry')
    return hits[-1]


def build_windows_window(server):
    """Cross-compile RiftLord.exe around RiftLordServer.exe.

    The MinGW-built window imports two DLLs Windows does not ship -- WebView2's loader
    (linked statically only by MSVC builds; its static library needs the MSVC C++
    runtime) and llvm-mingw's libunwind -- so what ships as RiftLord.exe is
    shell/launcher, which carries the window and both DLLs and unpacks them on first
    run. One file either way."""
    if shutil.which('rustup') is None:
        raise SystemExit('rustup is not on PATH; install Rust (https://rustup.rs)')
    installed = subprocess.check_output(['rustup', 'target', 'list', '--installed'], text=True)
    if WIN_TARGET not in installed.split():
        run(['rustup', 'target', 'add', WIN_TARGET])
    bin_dir = llvm_mingw()
    clang = os.path.join(bin_dir, 'x86_64-w64-mingw32-clang')
    env = dict(os.environ,
               PATH=bin_dir + os.pathsep + os.environ.get('PATH', ''),
               CARGO_TARGET_X86_64_PC_WINDOWS_GNULLVM_LINKER=clang,
               CC_x86_64_pc_windows_gnullvm=clang,
               AR_x86_64_pc_windows_gnullvm=os.path.join(bin_dir, 'llvm-ar'),
               RC_x86_64_pc_windows_gnullvm=os.path.join(bin_dir, 'x86_64-w64-mingw32-windres'))

    def cargo(manifest, target_dir, **extra):
        cmd = ['cargo', 'build', '--release', '--target', WIN_TARGET, '--manifest-path', manifest]
        print('$', ' '.join(cmd))
        subprocess.check_call(cmd, env=dict(env, CARGO_TARGET_DIR=target_dir, **extra))
        return os.path.join(target_dir, WIN_TARGET, 'release', NAME + '.exe')

    window = cargo(os.path.join(APP, 'Cargo.toml'), os.path.join(CARGO_TARGETS, 'editor-app-windows'),
                   RIFTLORD_SIDECAR=server)
    unwind = os.path.join(os.path.dirname(bin_dir), 'x86_64-w64-mingw32', 'bin', 'libunwind.dll')
    payload = ';'.join('%s=%s' % (os.path.basename(p) if p != window else NAME + '.exe', p)
                       for p in (window, _webview2_loader(), unwind))
    # the launcher itself must load nothing that is not Windows', libunwind included
    launcher = cargo(os.path.join(LAUNCHER, 'Cargo.toml'), os.path.join(CARGO_TARGETS, 'editor-launcher-windows'),
                     RIFTLORD_PAYLOAD=payload, RUSTFLAGS='-C target-feature=+crt-static')
    return install_binary(launcher, NAME + '.exe')


def build_windows(onefile=True):
    """RiftLord.exe, built on Linux."""
    if os.name == 'nt':
        raise SystemExit('--windows is for building the .exe on Linux; on Windows, '
                         'run this script without it')
    server = build_windows_server(onefile)
    out = build_windows_window(server)
    print('\nbuilt %s (%.1f MB)' % (out, os.path.getsize(out) / 1e6))
    return out


def main(argv=None):
    global DIST, WORK
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--skip-ui', action='store_true', help='reuse the existing UI builds')
    ap.add_argument('--onedir', action='store_true',
                    help='freeze the server as a folder rather than one file')
    ap.add_argument('--windows', action='store_true',
                    help='on Linux: build RiftLord.exe under Wine')
    ap.add_argument('--server-only', action='store_true',
                    help='freeze the server and stop (what --windows runs inside Wine)')
    ap.add_argument('--dist', metavar='DIR',
                    help='write the builds here instead of %s (release_check.py builds '
                         'into a staging folder and installs only what passed)' % DIST)
    ap.add_argument('--work', metavar='DIR',
                    help="PyInstaller's work folder (what --windows passes inside Wine)")
    args = ap.parse_args(argv)
    if args.dist:
        DIST = os.path.abspath(args.dist)
        os.makedirs(DIST, exist_ok=True)
    if args.work:
        WORK = os.path.abspath(args.work)
    if args.windows:
        if not args.skip_ui:
            build_uis()
        build_windows(onefile=not args.onedir)
        return 0
    check_tools(args.skip_ui)
    if not args.skip_ui:
        build_uis()
    server = build_server(onefile=not args.onedir)
    if args.server_only:
        return 0
    if shutil.which('cargo') is None:
        raise SystemExit('cargo is not on PATH; install Rust (https://rustup.rs) to build '
                         'the app window')
    build_window(server, exe='.exe' if os.name == 'nt' else '')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
