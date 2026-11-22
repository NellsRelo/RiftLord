"""Offline checks on pxcook's compile command (riftlord/formats/omd3/omd3physx.py _command):
the Windows command reaches cmd.exe with its quotes as written, and the Linux command
stays a list.

    python3 test_pxcook_command.py
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from riftlord.formats.omd3 import omd3physx  # noqa: E402

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


BAT = r'C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat'
UE = r'C:\Program Files\Epic Games\UE_4.27'
OUT = r'C:\Users\Some One\AppData\Local\rift-lord\pxcook\key\pxcook.exe.1.part.exe'

# --- Windows: one string, quotes as written
got = omd3physx._command(UE, ('msvc', BAT, 'MSVC 18.11'), OUT)
check('the Windows command is one string, which subprocess passes to CreateProcess unchanged',
      isinstance(got, str), type(got))
head = 'cmd.exe /d /s /c '
line = got[len(head):] if isinstance(got, str) and got.startswith(head) else ''
check('it runs cmd.exe /d /s /c on one quoted line', line[:1] == '"' and line[-1:] == '"', got)
inner = line[1:-1]
check('the line cmd.exe runs after /s strips the outer quotes calls vcvars64 by its quoted path',
      inner.startswith('call "%s" >nul && cl ' % BAT), inner[:160])
check('no quote in the line is backslash-escaped (cmd.exe reads \\" as two characters)',
      '\\"' not in inner, inner)
check('every quoted path in the line is closed', inner.count('"') % 2 == 0, inner)
check('the output path is quoted whole', '/Fe:"%s"' % OUT in inner, inner)
old = subprocess.list2cmdline(['cmd.exe', '/d', '/s', '/c', inner])
check('the 1.0.4 list form is what wrote \\" (so this check would have caught it)',
      '\\"' in old, old[:120])

# --- Linux: a list
got = omd3physx._command('/opt/UE_4.27', ('clang', '/usr/bin/clang++', 'clang 19'), '/tmp/pxcook')
check('the Linux command stays a list', isinstance(got, list) and got[0] == '/usr/bin/clang++', got[:2])

bad = [c for c in checks if not c[1]]
for name, ok, detail in checks:
    print('%s %s%s' % ('ok  ' if ok else 'FAIL', name, '' if ok else ': %s' % (detail,)))
print('%d of %d checks passed' % (len(checks) - len(bad), len(checks)))
sys.exit(1 if bad else 0)
