"""Read reflected declarations out of C++ headers, the way UnrealHeaderTool sees them.

Two sources feed this: the game's UHT dump (`Win64/UHTHeaderDump/<Module>/`),
which is regular and machine-written, and the stock 4.27.2 headers for the two
modules the dump lacks, Engine and CoreUObject, which are hand-written and
messier. The same parser reads both.

It keeps what a shipping game reflects. A block under `#if WITH_EDITOR` or
`#if WITH_EDITORONLY_DATA` (or `#if 0`) is compiled out of the game and is
dropped; `#if !CPP` is what UHT reads in NoExportTypes.h and is kept; every
other condition is kept, as UHT does.

Output is plain data:

    {'classes': {name: {'super', 'kind', 'functions': {...}, 'properties': [...]}},
     'structs': {name: {'super', 'properties': [...]}},
     'enums':   {name: [value names]},
     'delegates': {name: [params]}}

Names are reflection names: the C++ prefix (U, A, F, I) is dropped for
classes and structs, kept for enums. A function is {'flags': [...],
'params': [{'name', 'type', 'out', 'ref', 'const', 'default'}], 'return':
type or None, 'static', 'const'}. Types are the C++ text, normalised later.
"""

import os
import re

EDITOR_ONLY = re.compile(r'\bWITH_EDITOR(ONLY_DATA)?\b|^\s*0\s*$|WITH_DEV_AUTOMATION_TESTS')
MACROS = re.compile(r'\b(UCLASS|USTRUCT|UENUM|UINTERFACE|UFUNCTION|UPROPERTY|'
                    r'DECLARE_DYNAMIC_(?:MULTICAST_)?(?:SPARSE_)?DELEGATE\w*)\s*\(')
#: Words that may come before a declaration and are not part of its type.
#: The C++ class holding an interface's functions: "class X_API IFoo : ... {".
INTERFACE_CLASS = re.compile(r'\bclass\s+(?:[A-Z0-9]+_API\s+)?I([A-Z]\w*)\s*(?::[^{;]*)?\{')
SPECIFIER_WORDS = {'virtual', 'static', 'inline', 'FORCEINLINE', 'FORCENOINLINE',
                   'explicit', 'friend', 'mutable', 'constexpr'}


# -- preprocessing --------------------------------------------------------

def strip_comments(text):
    """Remove // and /* */ comments, keeping string literals intact."""
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"' or c == "'":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == '\\' else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith('//', i):
            j = text.find('\n', i)
            i = n if j < 0 else j
        elif text.startswith('/*', i):
            j = text.find('*/', i + 2)
            i = n if j < 0 else j + 2
        else:
            out.append(c)
            i += 1
    return ''.join(out)


def strip_inactive(text):
    """Drop lines inside preprocessor blocks a shipping build compiles out."""
    out = []
    stack = []                       # per #if: (this branch active, parent active, taken)
    active = True
    for line in text.split('\n'):
        s = line.strip()
        m = re.match(r'#\s*(if|ifdef|ifndef|elif|else|endif)\b(.*)', s)
        if m:
            kind, cond = m.group(1), m.group(2).strip()
            if kind in ('if', 'ifdef', 'ifndef'):
                drop = bool(EDITOR_ONLY.search(cond)) and not cond.startswith('!')
                stack.append((active, not drop))
                active = active and not drop
            elif kind in ('elif', 'else') and stack:
                parent, was = stack[-1]
                # The other branch of an editor-only block is the game's branch.
                stack[-1] = (parent, not was)
                active = parent and not was
            elif kind == 'endif' and stack:
                active = stack.pop()[0]
            out.append('')
            continue
        out.append(line if active else '')
    return '\n'.join(out)


# -- scanning -------------------------------------------------------------

def matching(text, i, open_ch='(', close_ch=')'):
    """Index just past the bracket that closes the one at text[i]."""
    depth = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i = text.index('"', i + 1) if '"' in text[i + 1:] else n
            while text[i - 1] == '\\' and text[i - 2] != '\\':
                i = text.index('"', i + 1)
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError('unbalanced %s' % open_ch)


def split_top(text, sep=','):
    """Split on `sep` outside (), <>, {}, [] and strings."""
    parts, depth, cur, i = [], 0, [], 0
    while i < len(text):
        c = text[i]
        if c == '"':
            j = i + 1
            while j < len(text) and text[j] != '"':
                j += 2 if text[j] == '\\' else 1
            cur.append(text[i:j + 1])
            i = j + 1
            continue
        if c in '(<{[':
            depth += 1
        elif c in ')>}]':
            depth -= 1
        if c == sep and depth == 0:
            parts.append(''.join(cur).strip())
            cur = []
        else:
            cur.append(c)
        i += 1
    if ''.join(cur).strip():
        parts.append(''.join(cur).strip())
    return parts


def specifiers(text):
    """UFUNCTION(...) arguments as a set of words, meta keys included; values dropped."""
    text = re.sub(r'"(?:[^"\\]|\\.)*"', '""', text)
    words = set(re.findall(r'\b([A-Za-z_]\w*)\b', text))
    return {w for w in words if w not in ('meta', 'true', 'false')}


def meta(text):
    """The meta=(...) part of a specifier list, as {key: value or ''}."""
    m = re.search(r'\bmeta\s*=\s*\(', text, re.I)
    if not m:
        return {}
    body = text[m.end():matching(text, m.end() - 1) - 1]
    out = {}
    for item in split_top(body):
        key, _, value = item.partition('=')
        out[key.strip()] = value.strip().strip('"')
    return out


def reflection_name(cpp):
    """UWidget -> Widget, AActor -> Actor, FVector -> Vector, IFoo -> Foo; enums keep E."""
    if len(cpp) > 1 and cpp[0] in 'UAFI' and cpp[1].isupper():
        return cpp[1:]
    return cpp


def _declaration_end(text, i):
    """End of a declaration starting at i: past ';' or a balanced {...} body."""
    depth = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
        elif depth == 0 and c == ';':
            return i + 1, text[:i]
        elif depth == 0 and c == '{':
            return matching(text, i, '{', '}'), text[:i]
        i += 1
    return n, text


def parse_param(text):
    """One parameter: 'UPARAM(ref) FFoo& Out = Default' -> dict."""
    ref = False
    m = re.match(r'\s*UPARAM\s*\(', text)
    if m:
        end = matching(text, text.index('(', m.start()))
        ref = 'ref' in text[m.end():end - 1].lower()
        text = text[end:]
    default = None
    parts = split_top(text, '=')
    if len(parts) > 1:
        text, default = parts[0], '='.join(parts[1:]).strip()
    text = text.strip()
    m = re.match(r'(.*?)\b([A-Za-z_]\w*)\s*$', text, re.S)
    if not m or not m.group(1).strip():
        return {'name': '', 'type': text, 'out': False, 'ref': ref, 'const': False,
                'default': default}
    typ, name = m.group(1).strip(), m.group(2)
    is_ref = typ.endswith('&')
    is_const = bool(re.match(r'const\b', typ))
    return {'name': name, 'type': typ, 'out': is_ref and not is_const, 'ref': ref,
            'const': is_const, 'default': default}


def parse_function(decl, spec):
    """A UFUNCTION declaration -> (name, function dict) or None."""
    decl = re.sub(r'\bUE_DEPRECATED\s*\([^)]*\)', '', decl)
    decl = re.sub(r'\bDEPRECATED\s*\([^)]*\)', '', decl)
    decl = re.sub(r'\b[A-Z0-9]+_API\b', '', decl).strip()
    # The parameter list is the last top-level (...) group before trailing qualifiers.
    tail = re.search(r'\)\s*((?:const|override|final|PURE_VIRTUAL\s*\(.*\)|=\s*0|\s)*)$',
                     decl, re.S)
    if not tail:
        return None
    close = tail.start() + 1
    depth, open_at = 0, None
    for j in range(close - 1, -1, -1):
        if decl[j] == ')':
            depth += 1
        elif decl[j] == '(':
            depth -= 1
            if depth == 0:
                open_at = j
                break
    if open_at is None:
        return None
    head = decl[:open_at].strip()
    m = re.match(r'(.*?)\b([A-Za-z_]\w*)\s*$', head, re.S)
    if not m:
        return None
    words = m.group(1).split()
    flags = {w for w in words if w in SPECIFIER_WORDS}
    ret = ' '.join(w for w in words if w not in SPECIFIER_WORDS).strip()
    params = [parse_param(p) for p in split_top(decl[open_at + 1:close - 1]) if p.strip()]
    return m.group(2), {
        'flags': sorted(spec[0]), 'meta': spec[1], 'static': 'static' in flags,
        'virtual': 'virtual' in flags,
        'const': 'const' in tail.group(1),
        'return': None if ret in ('void', '') else ret, 'params': params}


def parse_property(decl):
    """A UPROPERTY declaration -> (name, type) or None."""
    parts = split_top(decl, '=')                               # drop a default initialiser
    if not parts:
        return None
    decl = parts[0]
    # "uint8 bFlag : 1" is a one-bit bitfield, which UHT reflects as a bool.
    bitfield = re.search(r':\s*1\s*$', decl.strip())
    decl = re.sub(r':\s*\d+\s*$', '', decl.strip())
    m = re.match(r'(.*?)\b([A-Za-z_]\w*)\s*(\[[^\]]*\])?\s*$', decl, re.S)
    if not m:
        return None
    typ = ' '.join(w for w in m.group(1).split() if w not in ('mutable', 'static'))
    if bitfield:
        typ = 'bool'
    # UHT reflects "X_DEPRECATED" as "X" (and flags it CPF_Deprecated).
    name = m.group(2)[:-len('_DEPRECATED')] if m.group(2).endswith('_DEPRECATED') else m.group(2)
    return name, typ.strip()


def parse_enum(body):
    """(names, values): explicit '= N' values honoured, the rest counting on."""
    names, values = [], []
    nxt = 0
    # UMETA first, before the split: a DisplayName may hold commas and brackets
    body = re.sub(r'UMETA\s*\((?:[^()"]|"[^"]*")*\)', '', body)
    for item in split_top(body):
        item = item.strip()
        if not item:
            continue
        name, _, value = item.partition('=')
        try:
            nxt = int(value.strip(), 0) if value.strip() else nxt
        except ValueError:
            pass
        names.append(name.strip())
        values.append(nxt)
        nxt += 1
    return names, values


def parse_header(text, out, module):
    """Add one header's reflected declarations to `out`."""
    text = strip_inactive(strip_comments(text))
    scopes = []                       # (name, kind, brace depth of its body)
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        m = MACROS.search(text, i)
        # Track braces between macros so we know which scope we are in. An
        # interface's functions live in its I-class, which has no macro of its
        # own, so its opening brace opens that interface's scope.
        upto = m.start() if m else n
        opens = {}
        for im in INTERFACE_CLASS.finditer(text, i, upto):
            name = im.group(1)
            if out['classes'].get(name, {}).get('kind') == 'interface':
                opens[im.end() - 1] = name
        for j in range(i, upto):
            if text[j] == '{':
                depth += 1
                if j in opens:
                    scopes.append((opens[j], 'class', depth))
            elif text[j] == '}':
                depth -= 1
                while scopes and depth < scopes[-1][2]:
                    scopes.pop()
        if not m:
            break
        macro = m.group(1)
        args_end = matching(text, m.end() - 1)
        args = text[m.end():args_end - 1]
        i = args_end
        if macro.startswith('DECLARE_DYNAMIC'):
            parts = split_top(args)
            body = parts[1:] if 'SPARSE' not in macro else parts[3:]
            ret = None
            if '_RetVal' in macro:
                ret, body = parts[0], parts[2:]
            name = parts[1] if '_RetVal' in macro else parts[0]
            params = [{'type': body[k], 'name': body[k + 1]} for k in range(0, len(body) - 1, 2)]
            out['delegates'][name] = {'params': params, 'return': ret,
                                      'multicast': 'MULTICAST' in macro,
                                      'owner': scopes[-1][0] if scopes else None,
                                      'module': module}
            continue
        if macro in ('UCLASS', 'USTRUCT', 'UINTERFACE'):
            hm = re.compile(r'\s*(class|struct)\s+(?:UE_DEPRECATED\s*\([^)]*\)\s*)?'
                            r'(?:[A-Z0-9]+_API\s+)?(\w+)(?:\s+final)?'
                            r'\s*(?::\s*([^{]*))?\{').match(text, i)
            if not hm:
                continue
            cpp = hm.group(2)
            supers = [s.split()[-1] for s in split_top(hm.group(3) or '')]
            name = reflection_name(cpp)
            super_name = reflection_name(supers[0]) if supers else None
            if macro == 'USTRUCT':
                out['structs'][name] = {'super': super_name, 'properties': [],
                                        'module': module}
                kind = 'struct'
            else:
                entry = out['classes'].setdefault(name, {'functions': {}, 'properties': []})
                entry.update({'super': super_name, 'module': module,
                              'kind': 'interface' if macro == 'UINTERFACE' else 'class',
                              'specifiers': sorted(specifiers(args)),
                              'interfaces': [reflection_name(s) for s in supers[1:]]})
                kind = 'class'
            depth += 1
            scopes.append((name, kind, depth))
            i = hm.end()
            continue
        if macro == 'UENUM':
            em = re.compile(r'\s*(?:enum\s+(class\s+)?(\w+)(?:\s*:\s*\w+)?\s*\{|'
                            r'namespace\s+(\w+)\s*\{\s*enum\s+\w+\s*\{)').match(text, i)
            if not em:
                continue
            brace = text.index('{', em.end() - 1)
            end = matching(text, brace, '{', '}')
            names, numbers = parse_enum(text[brace + 1:end - 1])
            kind = 'namespace' if em.group(3) else ('class' if em.group(1) else 'plain')
            out['enums'][em.group(2) or em.group(3)] = {
                'values': names, 'numbers': numbers, 'kind': kind, 'module': module}
            i = end
            continue
        end, decl = _declaration_end(text[i:], 0)
        i += end
        if not scopes:
            continue
        owner, kind, _ = scopes[-1]
        if macro == 'UFUNCTION' and kind == 'class':
            fn = parse_function(decl.strip(), (specifiers(args), meta(args)))
            if fn:
                out['classes'][owner]['functions'][fn[0]] = fn[1]
        elif macro == 'UPROPERTY':
            prop = parse_property(decl.strip())
            if prop:
                table = out['classes'] if kind == 'class' else out['structs']
                table[owner]['properties'].append(
                    {'name': prop[0], 'type': prop[1], 'specifiers': sorted(specifiers(args))})
    return out


def empty():
    return {'classes': {}, 'structs': {}, 'enums': {}, 'delegates': {}}


def parse_tree(root, module, out=None):
    """Every header under `root`, as module `module`."""
    out = out or empty()
    for base, _dirs, files in os.walk(root):
        for f in sorted(files):
            if f.endswith('.h'):
                with open(os.path.join(base, f), encoding='utf-8', errors='replace') as fh:
                    parse_header(fh.read(), out, module)
    return out
