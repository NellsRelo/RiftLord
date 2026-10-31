"""The app's third-party notices, from what a build actually bundled, and the licence gate.

    python3 tools/notices.py WORK OUT.txt [--web DIST ...] [--native FILE ...] [--zip PKGDIR ...]

Run by tools/build.py after PyInstaller (with the build venv's Python, whose distributions
were frozen). Components:
* Python: every distribution with a file in PyInstaller's analysis (WORK/PKG-00.toc and
  PYZ-00.toc), matched by the distributions' RECORD, plus the packages staged whole into the
  baker zip (--zip); name, version, licence (License-Expression, License, classifiers) and
  every licence file its dist-info lists. CPython's own modules and extensions count as
  CPython.
* System libraries PyInstaller collected (/usr/lib and the like): SYSTEM, by file name.
* JS: each UI's dist/licenses.json (web/shared/licenses.js): the npm packages in its bundle.
* Native: DECLARED, by file name (the libraries and tools build.py carries, the bootloader).
  PyInstaller's files in the analysis (its loader modules and run-time hooks) count as the
  bootloader; the pyinstaller package itself is not listed.
Every component's licence must be on ALLOWED (an SPDX-style expression: AND needs every
part allowed, OR any). One with no licence found, or one off the list, fails the run
(exit 1) and is named. Writes OUT (the notices) and OUT.json (the list, for the gate).

OUT has three parts: the component index (name, version, licence, where it is used); each
component's copyright lines, NOTICE files and the licence texts it refers to; each licence
text once. A licence file that holds a SHARED text word for word refers to it; what is
left of the file (its own wording) is printed once however many components carry it.
"""

import argparse
import ast
import fnmatch
import importlib.metadata as md
import json
import os
import re
import sys
import sysconfig

#: Licences a bundled component may carry. LGPL only as a dynamically loaded library;
#: GPL only with the exception that allows it (PyInstaller's bootloader, GCC's runtime).
ALLOWED = {'MIT', 'MIT-CMU', 'MIT-open-group', 'X11', 'BSD-2-Clause', 'BSD-3-Clause', '0BSD',
           'Apache-2.0', 'ISC', 'Zlib', 'PSF-2.0', 'HPND', 'CC0-1.0', 'MPL-2.0', 'bzip2-1.0.6',
           'LGPL-2.1-or-later (dynamic)', 'LGPL-3.0-or-later (dynamic)',
           'GPL-2.0-or-later WITH Bootloader-exception',
           'GPL-3.0-or-later WITH GCC-exception-3.1', 'Unicode-3.0', 'Unlicense'}
#: Classifier and free-text licence names, as SPDX ids.
ALIASES = {'MIT License': 'MIT', 'BSD License': 'BSD-3-Clause', 'BSD': 'BSD-3-Clause',
           'Apache Software License': 'Apache-2.0', 'Apache 2.0': 'Apache-2.0',
           'Python Software Foundation License': 'PSF-2.0', 'ISC License (ISCL)': 'ISC',
           'zlib/libpng License': 'Zlib', 'Historical Permission Notice and Disclaimer (HPND)': 'HPND'}

#: Licence texts kept in the tree for components whose own install carries none.
TEXTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'licenses')
#: System libraries PyInstaller collects, by file name: (component, licence, licence file).
#: The library's own licence, not its distribution package's (lz4's package is GPL for the
#: command line tool; liblz4 is BSD-2-Clause).
SYSTEM = [
    ('libpython3*.so*', 'CPython', 'PSF-2.0', 'PYTHON_LICENSE'),
    ('python3*.dll', 'CPython', 'PSF-2.0', 'PYTHON_LICENSE'),
    ('libbrotli*.so*', 'Brotli', 'MIT', '/usr/share/licenses/brotli/LICENSE'),
    ('libbz2.so*', 'bzip2', 'bzip2-1.0.6', '/usr/share/licenses/bzip2/LICENSE'),
    ('libcrypto.so*', 'OpenSSL', 'Apache-2.0', '/usr/share/licenses/openssl/LICENSE.txt'),
    ('libssl.so*', 'OpenSSL', 'Apache-2.0', '/usr/share/licenses/openssl/LICENSE.txt'),
    ('libexpat.so*', 'Expat', 'MIT', '/usr/share/licenses/expat/COPYING'),
    ('libffi.so*', 'libffi', 'MIT', '/usr/share/licenses/libffi/LICENSE'),
    ('libgcc_s.so*', 'GCC runtime (libgcc)', 'GPL-3.0-or-later WITH GCC-exception-3.1',
     ('/usr/share/licenses/libgcc/RUNTIME.LIBRARY.EXCEPTION', os.path.join(TEXTS, 'GPL-3.0.txt'))),
    ('libstdc++.so*', 'GCC runtime (libstdc++)', 'GPL-3.0-or-later WITH GCC-exception-3.1',
     ('/usr/share/licenses/libstdc++/RUNTIME.LIBRARY.EXCEPTION', os.path.join(TEXTS, 'GPL-3.0.txt'))),
    ('liblz4.so*', 'LZ4 library', 'BSD-2-Clause', os.path.join(TEXTS, 'lz4-lib-LICENSE.txt')),
    ('liblzma.so*', 'liblzma (XZ Utils)', '0BSD', '/usr/share/licenses/xz/COPYING'),
    ('libmpdec.so*', 'mpdecimal', 'BSD-2-Clause', '/usr/share/licenses/mpdecimal/COPYRIGHT.txt'),
    ('libuuid.so*', 'libuuid (util-linux)', 'BSD-3-Clause',
     '/usr/share/licenses/util-linux-libs/COPYING.BSD-3-Clause'),
    ('libz.so*', 'zlib-ng', 'Zlib', '/usr/share/licenses/zlib-ng-compat/LICENSE.md'),
    ('libzstd.so*', 'Zstandard', 'BSD-3-Clause', '/usr/share/licenses/zstd/LICENSE'),
    ('libncursesw.so*', 'ncurses', 'MIT-open-group', '/usr/share/licenses/ncurses/COPYING'),
]
#: The native components build.py carries and the bootloader: (file name, component,
#: licence, licence file or None to look beside the file). The bootloader entry covers
#: PyInstaller's loader modules (GPL with the exception) and run-time hooks (Apache-2.0).
DECLARED = [
    ('libispc_texcomp.so', 'ISPC Texture Compressor (GameTechDev)', 'MIT', 'LICENSE-ispc_texcomp.txt'),
    ('ispc_texcomp.dll', 'ISPC Texture Compressor (GameTechDev)', 'MIT', 'LICENSE-ispc_texcomp.txt'),
    ('recastgen-upstream', 'recastnavigation (recastgen-upstream)', 'Zlib',
     'LICENSE-recastnavigation.txt'),
    ('<bootloader>', 'PyInstaller bootloader', 'GPL-2.0-or-later WITH Bootloader-exception AND Apache-2.0',
     'pyinstaller:COPYING.txt'),
]
#: Where each kind of component is used in the app.
USED = {'Rust': 'window', 'Python': 'server', 'Python (baker)': 'baker',
        'System library': 'server', 'Native': 'server', 'JavaScript': 'web'}
#: Our own code: listed, not licence-checked.
OWN = ('riftlord', 'bpkit')
#: Bundled data with no licence of its own whose shipping is being decided: {archive
#: name: why}. Listed as warnings, not failures, until the decision lands.
WAIVED = {}

LICENCE_NAME = re.compile(r'(^|/)(licen[cs]e|copying|notice|copyright)[^/]*$', re.I)


def _expr(text):
    """A licence string as an SPDX-style expression (aliases applied), or None."""
    if not text or len(text) > 200:
        return None
    text = text.strip()
    return ALIASES.get(text, text)


def allowed(expr):
    """True when an expression is acceptable: each AND part allowed, OR needs any (an old
    Cargo `A/B` reads as A OR B)."""
    if not expr:
        return False
    expr = expr.strip()
    if '/' in expr and ' ' not in expr:
        expr = expr.replace('/', ' OR ')
    if expr in ALLOWED:
        return True
    for op, need in ((' OR ', any), (' AND ', all)):
        if op in expr:
            return need(allowed(p.strip(' ()')) for p in expr.split(op))
    return False


def dist_licence(dist):
    m = dist.metadata
    expr = _expr(m.get('License-Expression')) or _expr(m.get('License'))
    if not expr or expr.lower().startswith('gplv2-or-later with a special exception'):
        cls = [c.split(' :: ')[-1] for c in (m.get_all('Classifier') or []) if c.startswith('License ::')]
        cls = [c for c in cls if c != 'OSI Approved']
        if m['Name'].lower() == 'pyinstaller':
            expr = 'GPL-2.0-or-later WITH Bootloader-exception'
        elif cls:
            expr = ' OR '.join(sorted({ALIASES.get(c, c) for c in cls}))
    return expr


def _pypi(dist):
    """A distribution's page on PyPI, where its source is."""
    return 'https://pypi.org/project/%s/%s/' % (dist.metadata['Name'], dist.version)


def dist_texts(dist):
    out = []
    for f in dist.files or []:
        if LICENCE_NAME.search(str(f)):
            try:
                with open(dist.locate_file(f), encoding='utf-8', errors='replace') as fh:
                    out.append((str(f), fh.read().strip()))
            except OSError:
                pass
    return out


def _toc_paths(work):
    """Every source path in the analysis: (archive name, source path, type)."""
    out = []
    for name in ('PKG-00.toc', 'PYZ-00.toc'):
        path = os.path.join(work, name)
        if not os.path.isfile(path):
            continue
        with open(path) as fh:
            toc = ast.literal_eval(fh.read())
        for item in toc:
            if isinstance(item, list):
                out += [e for e in item if isinstance(e, tuple) and len(e) == 3]
    return out


def collect(work, zips=()):
    """{component: {name, version, license, texts, files}} for the analysis and zips."""
    by_file = {}
    for dist in md.distributions():
        for f in dist.files or []:
            by_file[os.path.normcase(os.path.realpath(dist.locate_file(f)))] = dist
    stdlib = {os.path.normcase(os.path.realpath(sysconfig.get_paths()[k]))
              for k in ('stdlib', 'platstdlib')}
    comps, own, unknown, waived = {}, set(), [], []
    vendored = {}                               # auditwheel's <dist>.libs folders
    for dist in md.distributions():
        for f in dist.files or []:
            part = str(f).split('/')[0]
            if part.endswith('.libs'):
                vendored[part] = dist

    def add(key, **kw):
        if key not in comps:
            comps[key] = dict(kw, files=0)
            if callable(comps[key].get('texts')):
                comps[key]['texts'] = comps[key]['texts']()
        comps[key]['files'] += 1

    boot = DECLARED[-1]
    for name, src, kind in _toc_paths(work):
        if kind in ('OPTION', 'PYZ', 'SPLASH', 'SYMLINK') or not src:
            continue                               # a symlink names a file listed itself
        real = os.path.normcase(os.path.realpath(src))
        top = name.replace('\\', '/').split('/')[0].split('.')[0]
        dist = by_file.get(real) or vendored.get(name.replace('\\', '/').split('/')[0])
        if name in WAIVED:
            waived.append((name, WAIVED[name]))
            continue
        if os.path.basename(os.path.dirname(src)) == 'localpycs' and name.startswith('pyi'):
            dist = md.distribution('pyinstaller')    # PyInstaller's loader modules, compiled
        if dist is not None and dist.metadata['Name'].lower() == 'pyinstaller':
            add('native:' + boot[1], kind='Native', name=boot[1], version='', license=boot[2],
                texts=[], licence_file=boot[3])
            continue
        if real.startswith(os.path.normcase(os.path.realpath(os.path.dirname(work))) + os.sep):
            # the build's own products: the stdlib archive is CPython's, the baker zip's
            # packages come from --zip, the rest (notices, staged sources) is ours
            if os.path.basename(src) == 'base_library.zip':
                add('py:CPython', kind='Python', name='CPython', version=sys.version.split()[0],
                    license='PSF-2.0', texts=[])
            elif os.path.basename(src) != 'baker-packages.zip':
                own.add('build')
            continue
        if dist is not None:
            add('py:' + dist.metadata['Name'], kind='Python', name=dist.metadata['Name'],
                version=dist.version, license=dist_licence(dist), texts=lambda d=dist: dist_texts(d),
                source=_pypi(dist))
        elif any(real.startswith(s + os.sep) for s in stdlib) or top in sys.stdlib_module_names:
            add('py:CPython', kind='Python', name='CPython', version=sys.version.split()[0],
                license='PSF-2.0', texts=[])
        elif top in OWN or name.startswith(OWN):
            own.add(top)
        else:
            base = os.path.basename(src)
            hit = next((s for s in SYSTEM if fnmatch.fnmatch(base, s[0])), None)
            dec = next((d for d in DECLARED if d[0] == base), None)
            if hit and hit[1] == 'CPython':
                add('py:CPython', kind='Python', name='CPython', version=sys.version.split()[0],
                    license='PSF-2.0', texts=[])
            elif hit:
                add('sys:' + hit[1], kind='System library', name=hit[1], version='',
                    license=hit[2], texts=[], licence_file=hit[3])
            elif dec:
                add('native:' + dec[1], kind='Native', name=dec[1], version='', license=dec[2],
                    texts=[], licence_file=os.path.join(os.path.dirname(src), dec[3]))
            elif _ours(src) or name.startswith('native/LICENSE-'):
                own.add('data' if kind == 'DATA' else top)
            else:
                unknown.append((name, src, kind))
    for folder in zips:
        for entry in sorted(os.listdir(folder)):
            top = entry.split('.')[0].lstrip('_')
            dist = next((d for d in md.distributions()
                         if top in ((d.read_text('top_level.txt') or '').split() or
                                    [d.metadata['Name'].replace('-', '_')])), None)
            if dist is not None:
                add('py:' + dist.metadata['Name'], kind='Python (baker)', name=dist.metadata['Name'],
                    version=dist.version, license=dist_licence(dist), texts=lambda d=dist: dist_texts(d),
                    source=_pypi(dist))
            else:
                unknown.append((entry, folder, 'ZIP'))
    return comps, sorted(own), unknown, waived


def cargo_crates(manifest, target):
    """The crates linked into the binary `manifest` builds for `target` (cargo metadata,
    offline): normal dependencies only, proc-macros left out (they run at build time)."""
    import subprocess
    got = subprocess.run(['cargo', 'metadata', '--format-version', '1', '--offline',
                          '--filter-platform', target, '--manifest-path', manifest],
                         capture_output=True, text=True, timeout=600)
    if got.returncode:
        raise SystemExit('cargo metadata failed: %s' % got.stderr.strip()[-300:])
    meta = json.loads(got.stdout)
    pkgs = {p['id']: p for p in meta['packages']}
    nodes = {n['id']: n for n in meta['resolve']['nodes']}
    root = meta['resolve']['root']
    seen, todo = set(), [root]
    while todo:
        i = todo.pop()
        if i in seen:
            continue
        seen.add(i)
        for d in nodes[i]['deps']:
            if any(k['kind'] is None for k in d['dep_kinds']):
                todo.append(d['pkg'])
    out = []
    for i in sorted(seen - {root}):
        p = pkgs[i]
        if all('proc-macro' in t['kind'] for t in p['targets']):
            continue
        folder = os.path.dirname(p['manifest_path'])
        texts = []
        for f in sorted(os.listdir(folder)):
            if LICENCE_NAME.search(f) and os.path.isfile(os.path.join(folder, f)):
                with open(os.path.join(folder, f), encoding='utf-8', errors='replace') as fh:
                    texts.append((f, fh.read().strip()))
        if not texts:
            texts = _standard_text(p.get('license') or '', p.get('authors') or [])
        out.append(dict(kind='Rust', name=p['name'], version=p['version'],
                        license=p.get('license'), texts=texts, files=1,
                        source='https://crates.io/crates/%s/%s' % (p['name'], p['version'])))
    return out


#: The MIT licence, for a crate that names it but ships no licence file.
MIT_TEXT = """Copyright (c) {authors}

Permission is hereby granted, free of charge, to any person obtaining a copy of this
software and associated documentation files (the "Software"), to deal in the Software
without restriction, including without limitation the rights to use, copy, modify,
merge, publish, distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to the following
conditions:

The above copyright notice and this permission notice shall be included in all copies
or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF
CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR
THE USE OR OTHER DEALINGS IN THE SOFTWARE."""


def _standard_text(expr, authors):
    """The standard text of a licence a package names but ships no file for: MIT (with its
    authors) or Apache-2.0 (tools/licenses/Apache-2.0.txt); [] for any other."""
    names = ' '.join(a.split('<')[0].strip() for a in authors) or 'the authors'
    if 'MIT' in expr:
        return [('MIT (standard text; the package ships none)', MIT_TEXT.format(authors=names))]
    if 'Apache-2.0' in expr:
        text = _read(os.path.join(TEXTS, 'Apache-2.0.txt'))
        return [('Apache-2.0 (standard text; the package ships none)', text)] if text else []
    return []


def _ours(path):
    """A file of this tree (our code, data, pages; their npm parts are listed from
    licenses.json)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.realpath(path).startswith(os.path.realpath(root) + os.sep)


def _read(path):
    if path == 'PYTHON_LICENSE':
        path = os.path.join(sysconfig.get_paths()['stdlib'], 'LICENSE.txt')
    if path and path.startswith('pyinstaller:'):
        dist = md.distribution('pyinstaller')
        path = next((str(dist.locate_file(f)) for f in dist.files or []
                     if str(f).endswith(path.split(':', 1)[1])), None)
    try:
        with open(path, encoding='utf-8', errors='replace') as fh:
            return fh.read().strip()
    except (OSError, TypeError):
        return None


#: Licence texts printed once and referred to by each component whose licence file holds
#: one word for word: (id, file in TEXTS, or None for MIT_TEXT without its copyright line).
SHARED = [('Apache-2.0', 'Apache-2.0.txt'), ('MIT', None), ('Zlib', 'Zlib.txt'),
          ('MPL-2.0', 'MPL-2.0.txt'), ('GPL-2.0', 'GPL-2.0.txt'), ('GPL-3.0', 'GPL-3.0.txt'),
          ('GCC-exception-3.1', 'GCC-exception-3.1.txt')]
#: The part of a shared text a licence file must hold, by its first and last words, where
#: copies differ outside it (title lines).
CORE = {'Apache-2.0': ('terms and conditions for use', 'or additional liability')}
#: Instructions printed after a shared text; beside its match in a licence file they are
#: dropped (their filled-in copyright line is kept as the component's). Matched in pieces
#: split at the lines that vary between copies (APPENDIX_SPLIT, which are TITLES).
APPENDIX = {'Apache-2.0': 'Apache-2.0-APPENDIX.txt'}
APPENDIX_SPLIT = re.compile(r'\n[ \t]*(?:copyright \[yyyy\][^\n]*|https?://www\.apache\.org/'
                            r'licenses/license-2\.0)[ \t]*\n', re.I)
#: Lines that only title a shared text or stand in its appendix, dropped when only blank,
#: copyright and other such lines lie between them and its match.
TITLES = {
    'Apache-2.0': r'(the )?apache license(,? version 2\.0)?|version 2\.0, january 2004|'
                  r'<?https?://www\.apache\.org/licenses/?(license-2\.0(\.txt)?)?>?|'
                  r'end of terms and conditions|'
                  r'copyright [\[{]yyyy[\]}] [\[{]name of copyright owner[\]}]',
    'MIT': r'(the )?mit license( \(mit\))?|mit',
    'Zlib': r'(the )?zlib license|zlib',
}
#: A component's copyright line, and an indented line that continues one.
COPYRIGHT = re.compile(r'^\s*(copyright\b|\(c\)|©|all rights reserved)', re.I)
#: A licence file printed whole beside its component (Apache-2.0 section 4(d)).
NOTICE_FILE = re.compile(r'(^|/)notice[^/]*$', re.I)
#: The pointer left where a shared text was.
MARK = '[The %s licence text, given once in part 3]'
MARK_RE = re.compile(re.escape(MARK).replace(re.escape('%s'), r'(\S+)'))
WORD = re.compile(r'[A-Za-z0-9]+')
RULE, THIN = '=' * 78, '-' * 78


def _words(text):
    return [w.lower() for w in WORD.findall(text)]


def _shared():
    """{id: (printed text, [word lists to find], [appendix word lists])}, read once."""
    if _shared.cache is None:
        _shared.cache = {}
        for sid, name in SHARED:
            text = _read(os.path.join(TEXTS, name)) if name else MIT_TEXT.split('\n\n', 1)[1]
            words = _words(text)
            if sid in CORE:
                a, b = (p.split() for p in CORE[sid])
                i = next(k for k in range(len(words)) if words[k:k + len(a)] == a)
                j = next(k for k in range(len(words), 0, -1) if words[k - len(b):k] == b)
                words = words[i:j]
            extra = []
            if sid in APPENDIX:
                more = _read(os.path.join(TEXTS, APPENDIX[sid]))
                text += '\n\n' + more
                extra = [_words(p) for p in APPENDIX_SPLIT.split(more)]
            _shared.cache[sid] = (text, words, extra)
    return _shared.cache


_shared.cache = None


def _spans(text, pattern):
    """The character spans of `text` holding the words of `pattern` in a row, each widened
    over the punctuation beside it on its first and last lines."""
    found = list(WORD.finditer(text))
    hay = ' ' + ' '.join(m.group().lower() for m in found) + ' '
    at, pos = {}, 1
    for k, m in enumerate(found):
        at[pos] = k
        pos += len(m.group()) + 1
    needle = ' ' + ' '.join(pattern) + ' '
    out, i = [], hay.find(needle)
    while i >= 0 and pattern:
        k = at[i + 1]
        a, b = found[k].start(), found[k + len(pattern) - 1].end()
        while a > 0 and text[a - 1] not in '\n' and not text[a - 1].isalnum():
            a -= 1
        while b < len(text) and text[b] not in '\n' and not text[b].isalnum():
            b += 1
        out.append((a, b))
        i = hay.find(needle, i + len(needle) - 1)
    return out


def split_text(text, titles=True):
    """A licence file as (what is left of it, the SHARED ids it holds): each shared text it
    holds word for word becomes a MARK, its appendix is dropped with the title lines beside
    it, and with `titles` the title lines beside each MARK too."""
    cuts, refs = [], []
    for sid, (_, words, extra) in _shared().items():
        spans = _spans(text, words)
        if spans:
            refs.append(sid)
            cuts += [(a, b, sid) for a, b in spans]
            cuts += [(a, b, None) for part in extra for a, b in _spans(text, part)]
    out, end = [], 0
    for a, b, sid in sorted(cuts):
        if a < end:
            continue
        out.append(text[end:a])
        out.append(MARK % sid if sid else CUT)
        end = b
    out.append(text[end:])
    lines = ''.join(out).split('\n')
    pats = [TITLES[r] for r in refs if r in TITLES]
    if pats:
        lines = _drop_titles(lines, re.compile(r'^\s*(%s)\s*$' % '|'.join(pats), re.I), titles)
    rest = '\n'.join(ln.replace(CUT, '').rstrip() for ln in lines)
    return re.sub(r'\n\s*\n(\s*\n)+', '\n\n', rest).strip('\n'), refs


#: Where an appendix was cut out (removed with the lines' other text).
CUT = '\x00'


def _drop_titles(lines, title, marks):
    """`lines` less the title lines in a run of blank, title and copyright lines that
    touches a cut (a CUT, or with `marks` a MARK; with `marks` rule lines join a run)."""
    kinds = []
    for ln in lines:
        bare = ln.replace(CUT, '')
        kinds.append('cut' if CUT in ln or (marks and MARK_RE.search(ln)) else
                     'title' if title.match(bare) else
                     'gap' if not bare.strip() or COPYRIGHT.match(bare) or MARK_RE.search(bare) or
                     (marks and not WORD.search(bare)) else 'text')
    keep, i = [], 0
    while i < len(lines):
        j = i
        while j < len(lines) and kinds[j] != 'text':
            j += 1
        run = range(i, j) if j > i else range(i, i + 1)
        touches = any(kinds[k] == 'cut' for k in run)
        keep += [lines[k] for k in run if not (touches and kinds[k] == 'title')]
        i = run.stop
    return keep


def _copyright_only(rest):
    """True when every line left with words is a copyright line or continues one."""
    prev = False
    for ln in MARK_RE.sub('', rest).split('\n'):
        if not WORD.search(ln):
            continue
        prev = bool(COPYRIGHT.match(ln)) or (prev and ln[:1].isspace())
        if not prev:
            return False
    return True


def arrange(rows):
    """Each component's notice lines and licence text ids, and the texts printed once:
    (per component [(lines, ids)], [(id, title, text)])."""
    shared_used, own, per = set(), {}, []
    for c in rows:
        lines, ids, notices = [], [], []
        for label, text in c['texts']:
            if NOTICE_FILE.search(label):
                notices.append((label, text))
                continue
            rest, refs = split_text(text)
            shared_used.update(refs)
            if _copyright_only(rest):
                lines += [ln for ln in MARK_RE.sub('', rest).split('\n')
                          if WORD.search(ln) and ln not in lines]
            else:
                rest = split_text(text, titles=False)[0]
                if rest not in own:
                    own[rest] = dict(id='T%d' % (len(own) + 1), label=label, users=[])
                own[rest]['users'].append(c['name'])
                ids.append(own[rest]['id'])
            ids += refs
        per.append(dict(lines=lines, ids=list(dict.fromkeys(ids)), notices=notices))
    order = [sid for sid, _ in SHARED if sid in shared_used]
    texts = [(sid, sid, _shared()[sid][0]) for sid in order]
    for rest, o in own.items():
        users = list(dict.fromkeys(o['users']))
        texts.append((o['id'], '%s (%s)' % (o['label'], ', '.join(users)), rest))
    return per, texts


def write_notices(path, rows):
    """OUT: the index, each component's notices, each licence text once."""
    per, texts = arrange(rows)
    w = []
    w.append(
        'Rift Lord third-party notices\n\n'
        'Rift Lord carries the third-party components below.\n'
        'Part 1 lists them: name, version, where each is used (the window, the server, the\n'
        'light baker, the web pages) and its licence.\n'
        'Part 2 gives each one\'s copyright lines and NOTICE files and names its licence texts;\n'
        'together with those texts they are that component\'s licence notice. A component under\n'
        'the MPL or LGPL names where its source is.\n'
        'Part 3 gives each licence text once. Where a component\'s own licence file holds a\n'
        'standard text (Apache-2.0, MIT, ...), the file is given with a pointer in its place.\n')
    w.append('%s\nPART 1. COMPONENTS\n%s\n' % (RULE, RULE))
    w.append('%-40s %-14s %-8s %s' % ('Component', 'Version', 'Used in', 'Licence'))
    for c in rows:
        w.append('%-40s %-14s %-8s %s' % (c['name'], c['version'] or '-', USED[c['kind']],
                                          c['license']))
    w.append('\n%s\nPART 2. COPYRIGHT AND NOTICES\n%s' % (RULE, RULE))
    for c, p in zip(rows, per):
        w.append('%s\n%s\nKind: %s. Used in: %s. Licence: %s' % (
            THIN, ('%s %s' % (c['name'], c['version'])).strip(), c['kind'], c['used'], c['license']))
        w += p['lines']
        for label, text in p['notices']:
            w.append('-- %s --\n%s' % (label, text))
        w.append('Licence texts: %s' % ', '.join(p['ids']))
        if c.get('source') and re.search(r'MPL|LGPL', c['license'] or ''):
            w.append('Source: %s' % c['source'])
    w.append('\n%s\nPART 3. LICENCE TEXTS\n%s' % (RULE, RULE))
    for tid, title, text in texts:
        w.append('\n%s\n%s: %s\n%s\n%s' % (RULE, tid, title, RULE, text) if tid != title else
                 '\n%s\n%s\n%s\n%s' % (RULE, tid, RULE, text))
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(w) + '\n')
    return per


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('work')
    ap.add_argument('out')
    ap.add_argument('--web', action='append', default=[], help="a UI's dist folder")
    ap.add_argument('--zip', action='append', default=[], help='a staged package folder')
    ap.add_argument('--native', action='append', default=[], help='a carried native file')
    ap.add_argument('--cargo', help="the window's Cargo.toml: its linked crates")
    ap.add_argument('--target', default='x86_64-unknown-linux-gnu', help='its Rust target')
    args = ap.parse_args(argv)
    comps, own, unknown, waived = collect(args.work, args.zip)
    boot = DECLARED[-1]
    comps.setdefault('native:' + boot[1], dict(kind='Native', name=boot[1], version='',
                                               license=boot[2], texts=[], licence_file=boot[3],
                                               files=0))['files'] += 1
    for path in args.native:
        dec = next((d for d in DECLARED if d[0] == os.path.basename(path)), None)
        if dec:
            comps.setdefault('native:' + dec[1], dict(
                kind='Native', name=dec[1], version='', license=dec[2], texts=[],
                licence_file=os.path.join(os.path.dirname(path), dec[3]), files=1))
    if args.cargo:
        for c in cargo_crates(args.cargo, args.target):
            comps['rust:' + c['name'] + ' ' + c['version']] = c
    for dist in args.web:
        try:
            with open(os.path.join(dist, 'licenses.json')) as fh:
                pkgs = json.load(fh)
        except (OSError, ValueError):
            unknown.append(('licenses.json', dist, 'WEB'))
            continue
        for p in pkgs:
            c = comps.setdefault('js:' + p['name'], dict(
                kind='JavaScript', name=p['name'], version=p['version'], license=_expr(p['license']),
                texts=[('package', p['text'])] if p.get('text') else [], files=0, uis=[]))
            c['files'] += 1
            c.setdefault('uis', []).append(os.path.basename(os.path.dirname(dist)))
    problems = ['%s: no component known for %s (%s)' % (k, n, s) for n, s, k in unknown]
    for c in comps.values():
        if not c['texts'] and c.get('licence_file'):
            files = c['licence_file']
            files = [files] if isinstance(files, str) else files
            c['texts'] = [(os.path.basename(f.split(':')[-1]), t)
                          for f, t in ((f, _read(f)) for f in files) if t]
        if not allowed(c['license']):
            problems.append('%s %s: licence %r is not on the allow-list' % (c['kind'], c['name'],
                                                                            c['license']))
        if not c['texts'] and c['name'] != 'CPython':
            problems.append('%s %s: no licence text found' % (c['kind'], c['name']))
        if c['name'] == 'CPython' and not c['texts']:
            text = _read('PYTHON_LICENSE')
            c['texts'] = [('LICENSE.txt', text)] if text else []
    rows = sorted(comps.values(), key=lambda c: (c['kind'], c['name'].lower()))
    for c in rows:
        c['used'] = USED[c['kind']] + (' (%s)' % ', '.join(c['uis']) if c.get('uis') else '')
    per = write_notices(args.out, rows)
    summary = {'ok': not problems, 'problems': problems, 'own': own,
               'waived': [{'file': n, 'why': w} for n, w in waived],
               'components': [dict({k: c.get(k) for k in ('kind', 'name', 'version', 'license',
                                                          'files', 'used')}, texts=p['ids'])
                              for c, p in zip(rows, per)]}
    with open(args.out + '.json', 'w') as fh:
        json.dump(summary, fh, indent=1)
    for c in rows:
        print('  %-16s %-40s %-12s %s' % (c['kind'], c['name'], c['version'], c['license']))
    for n, w in waived:
        print('NOTICE WAIVED: %s: %s' % (n, w))
    for p in problems:
        print('NOTICE PROBLEM: ' + p)
    print('%d components, %d problem(s); %s' % (len(rows), len(problems), args.out))
    return 0 if not problems else 1


if __name__ == '__main__':
    raise SystemExit(main())
