"""Offline check on the text Rift Lord shows people: no "own" phrasing ("the level's own
track", "its own folder", "Rift Lord's own page") in any user-visible string.

Scanned: the web UIs' sources (web/*/src/**/*.svelte and *.js, web/shared, each UI's
index.html): Svelte and HTML markup text and the shown attributes (title, aria-label,
placeholder, alt, label), and JS string and template literals; and the Python package's
string literals (riftlord/, docstrings left out), which hold server messages, build console
lines and errors. Comments are not scanned. A string counts as text when it has a space or
starts with a capital letter, so keys such as 'own' are not text.

    python3 test_ui_text.py [--list]      # --list: every hit, one per line, and exit 0
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

#: The phrasing refused: the word "own" in shown text (not part of a name such as data-own).
BANNED = re.compile(r"(?<![\w$.-])own(?![\w=-])", re.IGNORECASE)
#: Folders of web sources, and the folder names skipped under them.
WEB = os.path.join(REPO, 'web')
WEB_SKIP = {'node_modules', 'dist', 'test'}
#: The Python package scanned, and its folders left out (riftlord/fab: the Fab converter,
#: kept by another session).
PY_ROOT = os.path.join(REPO, 'riftlord')
PY_SKIP = {'__pycache__', 'fab'}
#: Strings that are not shown: (file, text) of search keys matching text another program
#: writes (onslaught.py reads a UE4SS log for this line).
NOT_SHOWN = {('riftlord/onslaught/onslaught.py', "watching the coordinator's own calls")}
#: Markup attributes whose values are shown.
SHOWN_ATTRS = ('title', 'aria-label', 'placeholder', 'alt', 'label')


def is_text(s):
    s = s.strip()
    return bool(s) and (' ' in s or s[:1].isupper())


#: Characters after which a `/` starts a regular expression rather than a division.
REGEX_AFTER = set('(,=:[!&|?{};+-*%<>~^') | {''}
REGEX_WORDS = ('return', 'typeof', 'case', 'in', 'of', 'void', 'delete', 'throw')


def _template_end(src, i):
    """The index just past the template literal starting at src[i] (a backtick), its
    ${...} parts skipped with their own strings and nesting."""
    n, j = len(src), i + 1
    while j < n:
        c = src[j]
        if c == '\\':
            j += 2
        elif c == '`':
            return j + 1
        elif src.startswith('${', j):
            depth, j = 1, j + 2
            while j < n and depth:
                c = src[j]
                if c in '\'"':
                    j = _quote_end(src, j)
                    continue
                if c == '`':
                    j = _template_end(src, j)
                    continue
                depth += {'{': 1, '}': -1}.get(c, 0)
                j += 1
        else:
            j += 1
    return n


def _quote_end(src, i):
    n, j, q = len(src), i + 1, src[i]
    while j < n and src[j] != q and src[j] != '\n':
        j += 2 if src[j] == '\\' else 1
    return min(n, j + 1)


def _regex_end(src, i):
    n, j, cls = len(src), i + 1, False
    while j < n and src[j] != '\n':
        c = src[j]
        if c == '\\':
            j += 2
            continue
        if c == '[':
            cls = True
        elif c == ']':
            cls = False
        elif c == '/' and not cls:
            return j + 1
        j += 1
    return j


def js_literals(src):
    """(line, text) of each string literal and each template literal's text parts in JS
    source; comments and regular expressions skipped."""
    i, n, last, word = 0, len(src), '', ''
    while i < n:
        c = src[i]
        if c in '\'"':
            j = _quote_end(src, i)
            yield src.count('\n', 0, i) + 1, src[i + 1:j - 1]
            i, last = j, 'x'
        elif c == '`':
            j = _template_end(src, i)
            body = src[i + 1:j - 1]
            yield src.count('\n', 0, i) + 1, _template_text(body)
            i, last = j, 'x'
        elif src.startswith('//', i):
            j = src.find('\n', i)
            i = n if j < 0 else j
        elif src.startswith('/*', i):
            j = src.find('*/', i + 2)
            i = n if j < 0 else j + 2
        elif c == '/' and (last in REGEX_AFTER or word in REGEX_WORDS):
            i, last = _regex_end(src, i), 'x'
        else:
            if not c.isspace():
                if c.isalnum() or c in '_$':
                    word = word + c if last and (last.isalnum() or last in '_$') else c
                else:
                    word = ''
                last = c
            i += 1


def _template_text(body):
    """A template literal's text with its ${...} parts replaced by a space."""
    out, j, n = [], 0, len(body)
    while j < n:
        if body.startswith('${', j):
            depth, j = 1, j + 2
            while j < n and depth:
                c = body[j]
                if c in '\'"':
                    j = _quote_end(body, j)
                    continue
                if c == '`':
                    j = _template_end(body, j)
                    continue
                depth += {'{': 1, '}': -1}.get(c, 0)
                j += 1
            out.append(' ')
        elif body[j] == '\\' and j + 1 < n:
            out.append(body[j:j + 2])
            j += 2
        else:
            out.append(body[j])
            j += 1
    return ''.join(out)


def js_strings(src):
    """(line, text) of each string and template literal in JS source, as shown text: a
    template's markup tags and attributes other than SHOWN_ATTRS left out."""
    for line, text in js_literals(src):
        if '<' in text and '>' in text:
            shown = [t for _l, t in markup_text(text)]
            rest = re.sub(r'<[^<>]*>', ' ', text)
            for t in shown + [rest]:
                yield line, t
        else:
            yield line, text


def markup_text(src):
    """(line, text) of markup text nodes and shown attribute values, outside <script> and
    <style> and HTML comments."""
    blank = lambda m: re.sub(r'[^\n]', ' ', m.group(0))  # noqa: E731
    src = re.sub(r'<script\b.*?</script>', blank, src, flags=re.S | re.I)
    src = re.sub(r'<style\b.*?</style>', blank, src, flags=re.S | re.I)
    src = re.sub(r'<!--.*?-->', blank, src, flags=re.S)
    for m in re.finditer(r'>([^<>]+)<', src):
        text = re.sub(r'\{[^}]*\}', ' ', m.group(1))
        if text.strip():
            yield src.count('\n', 0, m.start()) + 1, text
    for m in re.finditer(r'\b(%s)\s*=\s*"([^"]*)"' % '|'.join(SHOWN_ATTRS), src):
        yield src.count('\n', 0, m.start()) + 1, m.group(2)


def script_blocks(src):
    """(first line, body) of each <script> block of a Svelte or HTML file."""
    for m in re.finditer(r'<script\b[^>]*>(.*?)</script>', src, flags=re.S | re.I):
        yield src.count('\n', 0, m.start(1)), m.group(1)


def web_hits():
    for base, dirs, names in os.walk(WEB):
        dirs[:] = sorted(d for d in dirs if d not in WEB_SKIP)
        for name in sorted(names):
            if not name.endswith(('.svelte', '.js', '.html')):
                continue
            if name.endswith('.config.js'):
                continue
            path = os.path.join(base, name)
            with open(path, encoding='utf-8') as fh:
                src = fh.read()
            found = []
            if name.endswith('.js'):
                found += list(js_strings(src))
            else:
                found += list(markup_text(src))
                for first, body in script_blocks(src):
                    found += [(first + ln - 1, t) for ln, t in js_strings(body)]
            for line, text in found:
                if is_text(text) and BANNED.search(text):
                    yield os.path.relpath(path, REPO), line, text.strip()


def _docstrings(tree):
    """ids of the Constant nodes that are docstrings."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                out.add(id(body[0].value))
    return out


def py_hits():
    for base, dirs, names in os.walk(PY_ROOT):
        dirs[:] = sorted(d for d in dirs if d not in PY_SKIP)
        for name in sorted(names):
            if not name.endswith('.py'):
                continue
            path = os.path.join(base, name)
            with open(path, encoding='utf-8') as fh:
                src = fh.read()
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            docs = _docstrings(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                        and id(node) not in docs:
                    if is_text(node.value) and BANNED.search(node.value):
                        yield os.path.relpath(path, REPO), node.lineno, node.value.strip()


def hits():
    out, seen = [], set()
    for path, line, text in list(web_hits()) + list(py_hits()):
        if (path, text) in NOT_SHOWN or (path, line, text) in seen:
            continue
        seen.add((path, line, text))
        out.append((path, line, text))
    return out


def selftest():
    """The scanner on made-up sources: what it must find, and what it must leave."""
    svelte = ('<script>\n  // the level\'s own track\n  const a = "Uses the level\'s own sky"\n'
              '  const k = \'own\'\n  const r = /\'/g\n  /** \'x\' the level\'s own */\n'
              '  const t = `<b data-own="${k}">${n === own ? \'Ours\' : \'\'}</b> Its own light`\n'
              '</script>\n<p>Its own folder</p>\n<b title="our own">x</b>\n'
              '<style>.own { color: red }</style>\n<p>Owner {ownMaps}</p>\n')
    got = [t for _l, t in markup_text(svelte)] + \
        [t for f, b in script_blocks(svelte) for _l, t in js_strings(b)]
    shown = [t for t in got if is_text(t) and BANNED.search(t)]
    want = ['Its own folder', 'Its own light', 'our own', "Uses the level's own sky"]
    return sorted(s.strip() for s in shown) == sorted(want), shown


def main(argv):
    ok_self, shown = selftest()
    found = hits()
    if '--list' in argv:
        for path, line, text in found:
            print('%s:%d: %s' % (path, line, text[:160].replace('\n', ' ')))
        print('%d hits' % len(found))
        return 0
    print('%s  scanner finds "own" in markup, attributes and script strings, not in comments,'
          ' styles or keys%s' % ('PASS' if ok_self else 'FAIL', '' if ok_self else ': %s' % shown))
    print('%s  no "own" phrasing in shown text (%d hits)' % ('PASS' if not found else 'FAIL',
                                                             len(found)))
    for path, line, text in found[:40]:
        print('      %s:%d: %s' % (path, line, text[:140].replace('\n', ' ')))
    return 0 if ok_self and not found else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
