"""Find the Orcs Must Die 3 install, wherever the tool is run from.

The converter grew up living inside the install and could take its paths for
granted. A shipped editor cannot: it may sit anywhere, so the install is found
from where the tool is, then from what the author chose last, then from Steam's
own library list.
"""

import json
import os
import re
import sys

#: What proves a directory is the install rather than merely named like it.
MARKER = os.path.join('OMD', 'Content', 'Paks')

#: The folder Steam gives the game, on every platform.
GAME_DIR = 'Orcs Must Die 3'

APP_NAME = 'OnslaughtEditor'
#: The folder user_data() keeps work in: Rift Lord's, which the bucket shares.
DATA_NAME = 'RiftLord'


def is_install(path):
    """True when `path` is an OMD3 install with paks in it."""
    if not path:
        return False
    paks = os.path.join(path, MARKER)
    try:
        return os.path.isdir(paks) and any(f.endswith('.pak')
                                           for f in os.listdir(paks))
    except OSError:
        return False


def settings_file():
    """Where the chosen install is remembered, per platform."""
    if os.name == 'nt':
        base = os.environ.get('APPDATA') or os.path.expanduser('~')
    else:
        base = os.environ.get('XDG_CONFIG_HOME') or \
            os.path.join(os.path.expanduser('~'), '.config')
    return os.path.join(base, APP_NAME, 'settings.json')


def load_settings():
    try:
        with open(settings_file()) as fh:
            return json.load(fh)
    except Exception:                                     # noqa: BLE001
        return {}


def save_install(path):
    """Remember an install for next time. Returns True if it was written."""
    return save_setting('install', os.path.abspath(path))


def save_setting(key, value):
    """Remember one setting. Returns True if it was written."""
    data = load_settings()
    data[key] = value
    try:
        os.makedirs(os.path.dirname(settings_file()), exist_ok=True)
        with open(settings_file(), 'w') as fh:
            json.dump(data, fh, indent=1)
        return True
    except OSError:
        return False


def beside_us():
    """The install this file sits inside, if it does.

    The converter lives at <install>/OMD/Binaries/Win64/OMD2-Converter, and a
    frozen build may sit anywhere, so every parent is tried.
    """
    here = os.path.dirname(os.path.abspath(
        sys.executable if getattr(sys, 'frozen', False) else __file__))
    while True:
        if is_install(here):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            return None
        here = parent


def steam_roots():
    """Steam installation roots worth searching, most likely first."""
    out = []
    if os.name == 'nt':
        try:
            import winreg
            for root, key in ((winreg.HKEY_CURRENT_USER,
                               r'Software\Valve\Steam'),
                              (winreg.HKEY_LOCAL_MACHINE,
                               r'SOFTWARE\WOW6432Node\Valve\Steam')):
                try:
                    with winreg.OpenKey(root, key) as k:
                        out.append(winreg.QueryValueEx(k, 'SteamPath')[0])
                except OSError:
                    pass
        except ImportError:
            pass
        out += [r'C:\Program Files (x86)\Steam', r'C:\Program Files\Steam']
    else:
        home = os.path.expanduser('~')
        out += [os.path.join(home, '.steam', 'steam'),
                os.path.join(home, '.local', 'share', 'Steam'),
                os.path.join(home, '.steam', 'root'),
                os.path.join(home, '.var', 'app', 'com.valvesoftware.Steam',
                             'data', 'Steam')]
    return [p for p in out if p and os.path.isdir(p)]


def steam_libraries():
    """Every Steam library folder, read from libraryfolders.vdf."""
    out = []
    for root in steam_roots():
        out.append(root)
        vdf = os.path.join(root, 'steamapps', 'libraryfolders.vdf')
        try:
            with open(vdf, encoding='utf-8', errors='replace') as fh:
                text = fh.read()
        except OSError:
            continue
        # entries look like:  "path"   "D:\\SteamLibrary"
        for hit in re.findall(r'"path"\s+"([^"]+)"', text):
            out.append(hit.replace('\\\\', os.sep).replace('\\', os.sep))
    seen, uniq = set(), []
    for p in out:
        if p not in seen and os.path.isdir(p):
            seen.add(p)
            uniq.append(p)
    return uniq


def from_steam():
    """The install, found through Steam's library list."""
    for lib in steam_libraries():
        candidate = os.path.join(lib, 'steamapps', 'common', GAME_DIR)
        if is_install(candidate):
            return candidate
    return None


def find(explicit=None):
    """The install to use, or None. Order: given, saved, beside us, Steam."""
    for candidate in (explicit,
                      os.environ.get('OMD3_INSTALL'),
                      load_settings().get('install'),
                      beside_us(),
                      from_steam()):
        if is_install(candidate):
            return os.path.abspath(candidate)
    return None


def paks(explicit=None):
    """<install>/OMD/Content/Paks, or None when the install is not found."""
    install = find(explicit)
    return os.path.join(install, MARKER) if install else None


#: The game's process: its executable's file name.
GAME_EXE = 'OMD-Win64-Shipping.exe'
#: A file whose presence stands for the game running, instead of looking for its process
#: (tests: riftlord.core.pakguard).
GAME_FLAG_ENV = 'RIFTLORD_GAME_FLAG'


def _is_game_args(args):
    """Whether a process's first arguments run GAME_EXE: the first argument, or one of the
    next two given as a path (Proton and Wine pass the exe's Windows path first, or after
    the loader); a command that only names it (`grep OMD-Win64-Shipping.exe`) does not."""
    exe = GAME_EXE.lower()
    return any(a.replace('\\', '/').rsplit('/', 1)[-1].lower() == exe
               and (k == 0 or '/' in a or '\\' in a) for k, a in enumerate(args[:3]))


def game_running():
    """Whether Orcs Must Die 3 is running: GAME_FLAG_ENV's file exists when it is set;
    else a process running GAME_EXE (its command line, from /proc on Linux, the process
    list on Windows)."""
    flag = os.environ.get(GAME_FLAG_ENV)
    if flag:
        return os.path.exists(flag)
    if os.name == 'nt':
        import subprocess
        try:
            got = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq %s' % GAME_EXE, '/NH'],
                                 capture_output=True, text=True, timeout=10,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.SubprocessError):
            return False
        return GAME_EXE.lower() in got.stdout.lower()
    try:
        pids = [p for p in os.listdir('/proc') if p.isdigit()]
    except OSError:
        return False
    for pid in pids:
        try:
            with open('/proc/%s/cmdline' % pid, 'rb') as fh:
                raw = fh.read()
        except OSError:
            continue
        if raw and _is_game_args(raw.decode('utf-8', 'replace').split('\0')):
            return True
    return False


#: A Paks folder standing in for the game's mod folders (~mods, LogicMods): the tests and the
#: gate set it (riftlord.core.supportedmods reads it too), so they never read the user's mods.
MOD_PAKS_ENV = 'RIFTLORD_SUPPORTED_PAKS'


def mod_paks(explicit=None):
    """The Paks folder whose ~mods and LogicMods hold the installed mods: MOD_PAKS_ENV, else
    paks() (`explicit` names an install and wins over the variable)."""
    return (None if explicit else os.environ.get(MOD_PAKS_ENV)) or paks(explicit)


def mods_dir(explicit=None):
    """Where a built pak is installed (mod_paks()'s ~mods), created if need be."""
    p = mod_paks(explicit)
    if not p:
        return None
    out = os.path.join(p, '~mods')
    os.makedirs(out, exist_ok=True)
    return out


#: Where tools write what they build, when something other than `~mods` should
#: receive it -- Rift Lord points this at its bucket. None means `~mods`.
TARGET = None


def target_dir(explicit=None):
    """Where a built pak goes: Rift Lord's bucket while it runs, else ~mods."""
    if TARGET:
        os.makedirs(TARGET, exist_ok=True)
        return TARGET
    return mods_dir(explicit)


def target_path(stem, explicit=None):
    """Where a build named `stem` (`900_MyMap_P`) is written: a loose folder in Rift
    Lord's bucket, which keeps builds unpacked until they are installed, or a `.pak`
    in ~mods."""
    folder = target_dir(explicit)
    return path_in(folder, stem) if folder else None


def path_in(folder, stem):
    """A build named `stem` in `folder`: loose in Rift Lord's bucket, a pak elsewhere."""
    loose = TARGET and os.path.abspath(folder) == os.path.abspath(TARGET)
    return os.path.join(folder, stem if loose else stem + '.pak')


def frozen():
    """True when running from a PyInstaller build."""
    return bool(getattr(sys, 'frozen', False))


#: The `riftlord` package folder (in the unpacked bundle when frozen).
CODE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: The repository root (the unpacked bundle when frozen).
REPO_ROOT = os.path.dirname(CODE_ROOT)
#: Binaries/Win64 of the game install a source tree sits in (UE4SS dumps, Mods);
#: RIFTLORD_WIN64 when the tree is a copy elsewhere (the gate's snapshot).
WIN64 = os.environ.get('RIFTLORD_WIN64') or os.path.dirname(REPO_ROOT)


def data(rel):
    """A data file shipped with the code (riftlord/data/<rel>)."""
    return os.path.join(CODE_ROOT, 'data', rel)


def web_dist(ui):
    """The built page of one UI (`shell`, `mapeditor`, `onslaught`)."""
    return os.path.join(REPO_ROOT, 'web', ui, 'dist')
#: Where a source run keeps what older code kept beside itself (caches, plans): outside
#: the game folder. RIFTLORD_SOURCE_CACHE moves it.
SOURCE_CACHE = os.environ.get('RIFTLORD_SOURCE_CACHE') or os.path.join(
    os.path.expanduser('~'), '.cache', 'rift-lord', 'editor-source')


def beside_code(folder, name):
    """`folder`/`name` for a file or folder kept beside the code: there when frozen (the
    unpacked bundle), else at the same relative place under SOURCE_CACHE."""
    if frozen():
        return os.path.join(folder, name)
    rel = os.path.relpath(os.path.abspath(folder), CODE_ROOT)
    return os.path.normpath(os.path.join(SOURCE_CACHE, rel, name))


def scratch(name, beside=None):
    """A writable working directory for caches and extracted assets.

    Run from source with `beside`, it is beside_code(beside, name). Frozen, the code
    lives in a bundle that is unpacked fresh each launch and thrown away, so the cache
    goes to the user's own cache directory and survives.
    """
    if not frozen() and beside:
        path = beside_code(beside, name)
    else:
        if os.name == 'nt':
            base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
        else:
            base = os.environ.get('XDG_CACHE_HOME') or \
                os.path.join(os.path.expanduser('~'), '.cache')
        path = os.path.join(base, APP_NAME, name.lstrip('.'))
    os.makedirs(path, exist_ok=True)
    return path


def user_data(name):
    """A folder for what the author makes and keeps, in the user's data directory --
    not the cache, which a cleaner may empty."""
    if os.name == 'nt':
        base = os.environ.get('APPDATA') or os.path.expanduser('~')
    else:
        base = os.environ.get('XDG_DATA_HOME') or \
            os.path.join(os.path.expanduser('~'), '.local', 'share')
    path = os.path.join(base, DATA_NAME, name)
    os.makedirs(path, exist_ok=True)
    return path


def other_game(folder, *parts):
    """A file inside another Steam game, or None. Used by the OMD2 side."""
    for lib in steam_libraries():
        candidate = os.path.join(lib, 'steamapps', 'common', folder, *parts)
        if os.path.exists(candidate):
            return candidate
    return None


def describe():
    """A short report of what was found, for a first-run screen or the log."""
    return {'install': find(),
            'beside_us': beside_us(),
            'saved': load_settings().get('install'),
            'steam_libraries': steam_libraries(),
            'settings_file': settings_file()}
