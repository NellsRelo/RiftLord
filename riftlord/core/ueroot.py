"""Where the user's Unreal Engine 4.27 is: one answer for UnrealPak and pxcook.

    root = ueroot.find()          # a folder holding Engine/Build/Build.version 4.27, or None

Looked for, in order: RIFTLORD_UE_ROOT; the app setting SETTING (gamepath settings);
then where the engine registers itself:
* Windows: the Epic launcher's installs (LauncherInstalled.dat, AppName UE_4.27), source
  builds registered by UnrealVersionSelector (HKCU Software\\Epic Games\\Unreal Engine\\
  Builds), and C:\\Program Files\\Epic Games\\UE_4.27;
* Linux: source builds registered by UnrealVersionSelector (~/.config/Epic/UnrealEngine/
  Install.ini, [Installations]). Epic ships no Linux 4.27 binary build.
"""

import json
import os

from riftlord.core import gamepath

#: The environment variable and the app setting naming the engine folder.
ENV = 'RIFTLORD_UE_ROOT'
SETTING = 'ue_root'
#: The engine version wanted.
VERSION = (4, 27)


def version(root):
    """(major, minor) of the engine at `root`, or None."""
    try:
        with open(os.path.join(root, 'Engine', 'Build', 'Build.version')) as fh:
            got = json.load(fh)
        return int(got['MajorVersion']), int(got['MinorVersion'])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _launcher_installs():
    data = os.path.join(os.environ.get('PROGRAMDATA') or r'C:\ProgramData', 'Epic',
                        'UnrealEngineLauncher', 'LauncherInstalled.dat')
    try:
        with open(data, encoding='utf-8') as fh:
            rows = json.load(fh).get('InstallationList') or []
    except (OSError, ValueError):
        return []
    return [r.get('InstallLocation') for r in rows
            if str(r.get('AppName', '')).startswith('UE_4.27') and r.get('InstallLocation')]


def _registered_builds():
    """Source builds UnrealVersionSelector registered."""
    if os.name == 'nt':
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r'Software\Epic Games\Unreal Engine\Builds')
        except OSError:
            return []
        out, i = [], 0
        while True:
            try:
                out.append(winreg.EnumValue(key, i)[1])
            except OSError:
                return out
            i += 1
    ini = os.path.join(os.path.expanduser('~'), '.config', 'Epic', 'UnrealEngine', 'Install.ini')
    out, section = [], None
    try:
        with open(ini, encoding='utf-8', errors='replace') as fh:
            for line in fh:
                line = line.strip()
                if line.startswith('['):
                    section = line.strip('[]')
                elif section == 'Installations' and '=' in line:
                    out.append(line.split('=', 1)[1].strip())
    except OSError:
        pass
    return out


def candidates():
    """Every folder looked at, in order (unchecked)."""
    out = [os.environ.get(ENV), gamepath.load_settings().get(SETTING)]
    if os.name == 'nt':
        out += _launcher_installs() + _registered_builds()
        out.append(os.path.join(os.environ.get('PROGRAMFILES') or r'C:\Program Files',
                                'Epic Games', 'UE_4.27'))
    else:
        out += _registered_builds()
    seen, uniq = set(), []
    for c in out:
        if c and os.path.normcase(os.path.abspath(c)) not in seen:
            seen.add(os.path.normcase(os.path.abspath(c)))
            uniq.append(os.path.abspath(c))
    return uniq


def find():
    """The first candidate holding UE 4.27, or None."""
    for c in candidates():
        if version(c) == VERSION:
            return c
    return None
