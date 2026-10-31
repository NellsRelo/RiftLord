"""Gate for bpkit.widget: every pure-layout widget Blueprint survives extract -> compile.

A widget Blueprint with no functions is a layout and nothing else, which is
what bpkit.widget builds. Each one is reduced to a description that holds only
what an author would write, compiled back, and must come out byte-identical:
proof that the compiler derives every tag, flag, import, export order and
preload dependency the editor did.

    python3 bpkit/check_widget.py
    python3 bpkit/check_widget.py --show NAME     # print one description
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'UE Assets'))

import gamefiles                                          # noqa: E402
from bpkit.package import Export, Package                 # noqa: E402
from bpkit.widget import compile, extract                 # noqa: E402


def candidates():
    for k in sorted(gamefiles.index()):
        if not k.endswith('.uasset'):
            continue
        ua = gamefiles.read(k)
        if b'WidgetBlueprintGeneratedClass' not in ua:
            continue
        p = Package.read(ua)
        if not any(p.object_name(e.class_index) == 'WidgetBlueprintGeneratedClass' for e in p.exports):
            continue
        if any(p.object_name(e.class_index) == 'Function' for e in p.exports):
            continue
        if any(p.object_name(e.class_index) == 'WidgetAnimation' for e in p.exports):
            ANIMATED.append(k[:-len('.uasset')])
            continue
        yield k[:-len('.uasset')]


#: Layout widgets left out because they hold an animation, whose compiled
#: evaluation template step 2 excludes.
ANIMATED = []


def compare(original, built):
    """Where two packages first disagree, structurally."""
    a, b = original, built
    if [str(n) for n in a.names] != [str(n) for n in b.names]:
        extra = sorted(set(map(str, b.names)) - set(map(str, a.names)))
        missing = sorted(set(map(str, a.names)) - set(map(str, b.names)))
        return 'names: missing %s, extra %s' % (missing[:6], extra[:6])
    ai = [a.path(-(i + 1)) + ' (' + a.name(x.class_name) + ')' for i, x in enumerate(a.imports)]
    bi = [b.path(-(i + 1)) + ' (' + b.name(x.class_name) + ')' for i, x in enumerate(b.imports)]
    if ai != bi:
        return 'imports: missing %s, extra %s' % (sorted(set(ai) - set(bi))[:6],
                                                  sorted(set(bi) - set(ai))[:6])
    ae = [a.object_name(i + 1) for i in range(len(a.exports))]
    be = [b.object_name(i + 1) for i in range(len(b.exports))]
    if ae != be:
        return 'export order: %s vs %s' % (ae, be)
    for i, (x, y) in enumerate(zip(a.exports, b.exports), 1):
        for f in ('class_index', 'super_index', 'template_index', 'outer_index', 'object_flags',
                  'is_asset', 'package_flags', 'not_always_loaded_for_editor_game'):
            if getattr(x, f) != getattr(y, f):
                return 'export %s: %s %r vs %r' % (ae[i - 1], f, getattr(x, f), getattr(y, f))
        if x.data != y.data:
            at = next(j for j, (u, v) in enumerate(zip(x.data + b'\0', y.data + b'\1')) if u != v)
            return 'export %s payload at byte %d of %d/%d' % (ae[i - 1], at, len(x.data), len(y.data))
        for k in Export.KINDS:
            if x.preload[k] != y.preload[k]:
                return 'export %s %s: %s vs %s' % (ae[i - 1], k, [a.object_name(t) for t in x.preload[k]],
                                                   [b.object_name(t) for t in y.preload[k]])
    s1, s2 = original.summary, built.summary
    for f in ('guid', 'flags', 'package_source'):
        if getattr(s1, f) != getattr(s2, f):
            return 'summary %s' % f
    return 'bytes differ (structure agrees)'


def check(stem):
    os.chdir(ROOT)
    ua, ux = gamefiles.read(stem + '.uasset'), gamefiles.read(stem + '.uexp')
    path = '/Game/' + stem.split('/Content/', 1)[1]
    try:
        desc = extract(ua, ux, path)
    except Exception as ex:
        return 'extract failed', repr(ex)[:150]
    want_vars = desc.pop('_variables')
    try:
        built = compile(desc)
    except Exception as ex:
        import traceback
        return 'compile failed', traceback.format_exc()[-300:]
    original = Package.read(ua, ux)
    built.summary.guid = original.summary.guid        # random per cook; ours is derived
    head, body = built.write()
    if head == ua and body == ux:
        return 'identical', None
    if built.variables != want_vars:
        return 'differs', 'variables %s vs %s' % (want_vars, built.variables)
    return 'differs', compare(original, Package.read(head, body))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--show')
    args = ap.parse_args()
    os.chdir(ROOT)
    stems = list(candidates())
    if args.show:
        stem = next(s for s in stems if s.endswith('/' + args.show))
        path = '/Game/' + stem.split('/Content/', 1)[1]
        print(json.dumps(extract(gamefiles.read(stem + '.uasset'), gamefiles.read(stem + '.uexp'),
                                 path), indent=2))
        return 0
    results = {}
    for stem in stems:
        verdict, detail = check(stem)
        results[stem] = verdict
        print('%-14s %-50s %s' % (verdict, stem.split('/Content/')[-1], detail or ''))
    ok = sum(1 for v in results.values() if v == 'identical')
    print('\n%d of %d identical' % (ok, len(results)))
    for stem in ANIMATED:
        print('excluded (animated): %s' % stem.split('/Content/')[-1])
    return 0 if ok == len(results) else 1


if __name__ == '__main__':
    sys.exit(main())
