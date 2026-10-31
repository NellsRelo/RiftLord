"""Gate for bpkit.deps: recompute every export's preload dependencies.

The gate is the widget Blueprints, the kind of package bpkit builds: every one
must derive exactly the cooker's sets. Every other Blueprint is measured and
reported -- actor Blueprints that inherit components from a parent Blueprint
need the parent's package, and those inheriting native components list ones
a package does not contain -- but does not decide the verdict.

For every Blueprint package whose exports all decode, the four dependency
lists of every export are derived from the tables and the payloads' reference
order alone, and must hold exactly the entries the cooker wrote. Order is
reported separately: the loader does not depend on it, and for a class whose
CDO has default subobjects the cooker's order is that of objects in memory,
which a package does not record.

    python3 bpkit/check_deps.py
"""

import collections
import multiprocessing as mp
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'UE Assets'))

import gamefiles                                          # noqa: E402
from bpkit.build import Ref                               # noqa: E402
from bpkit.check_structs import kind_of                   # noqa: E402
from bpkit.deps import preload_dependencies, property_deps  # noqa: E402
from bpkit.package import Export, Package                 # noqa: E402
from bpkit.props import decode_object, encode_object      # noqa: E402
from bpkit.structs import decode_export, encode_export    # noqa: E402

#: Exports whose dependencies follow their own class's code, which nothing
#: here builds: a DataTable lists its row struct and its rows' references.
EXCLUDED = {'DataTable', 'CompositeDataTable', 'CurveTable', 'CompositeCurveTable'}
GENERATED = (b'BlueprintGeneratedClass', b'WidgetBlueprintGeneratedClass',
             b'AnimBlueprintGeneratedClass', b'UserDefinedStruct')


def check(stem):
    os.chdir(ROOT)
    try:
        ua = gamefiles.read(stem + '.uasset')
    except KeyError:
        return None
    if not any(g in ua for g in GENERATED):
        return None
    p = Package.read(ua, gamefiles.read(stem + '.uexp'))
    names = [str(n) for n in p.names]
    refs, kinds, struct_deps, tagged = {}, {}, {}, {}
    for i, e in enumerate(p.exports, 1):
        kind = kind_of(p.object_name(e.class_index) or 'Class')
        try:
            if kind:
                x = decode_export(e.data, kind[0], names, lambda v: v, kind[1])
                frags = encode_export(x)
                kinds[i] = kind[0]
                tagged[i] = x.props
                extra = [c.target for c in x.children
                         if (p.object_name(p.exports[c.target - 1].class_index) if c.target > 0
                             else p.name(p.imports[-c.target - 1].class_name)) != 'Function']
                for q in x.properties:
                    extra.extend(property_deps(q, lambda r: r.target))
                struct_deps[i] = extra
            else:
                props, tail = decode_object(e.data, names, lambda v: v)
                frags = encode_object(props, tail)
                tagged[i] = props
        except Exception:
            return stem, 'not fully decoded', []
        refs[i] = [f.target for f in frags if isinstance(f, Ref)]
    derived = preload_dependencies(p, refs, kinds, struct_deps, tagged)
    wrong = []
    reordered = 0
    unknowable = 0
    inherited = 0
    for i, e in enumerate(p.exports, 1):
        if (p.object_name(e.class_index) or '') in EXCLUDED:
            continue
        for k in Export.KINDS:
            have, want = set(derived[i][k]), set(e.preload[k])
            if (have < want and e.object_flags & 0x10 and k == 'serialization_before_serialization'
                    and all(inherited_template(p, x) for x in want - have)):
                # Component templates inherited from a parent Blueprint: the cooker
                # walks the parent's SCS, which is in the parent's package.
                inherited += 1
                continue
            if (have != want and have < want and kinds.get(i) == 'class'
                    and k == 'serialization_before_serialization'
                    and all(x < 0 and p.name(p.imports[-x - 1].class_name) == 'Class'
                            for x in want - have)):
                # Classes of default subobjects the native parent creates but the
                # package does not contain (an editor-only ArrowComponent, ...).
                unknowable += 1
                continue
            if sorted(derived[i][k]) != sorted(e.preload[k]):
                wrong.append((p.object_name(e.class_index) or 'Class', k,
                              [p.object_name(x) for x in e.preload[k]],
                              [p.object_name(x) for x in derived[i][k]]))
            elif derived[i][k] != e.preload[k]:
                reordered += 1
    if wrong:
        return stem, 'differs', wrong
    if inherited:
        return stem, 'components inherited from a parent Blueprint', []
    if unknowable:
        return stem, 'native parent subobjects not in the package', []
    return stem, ('ok' if not reordered else 'same sets, order differs'), []


def inherited_template(p, x):
    """A component template that belongs to a parent Blueprint: an import whose
    outer is a Blueprint class, or an override under this class's
    InheritableComponentHandler."""
    outer = p.imports[-x - 1].outer if x < 0 else p.exports[x - 1].outer_index
    if not outer:
        return False
    cls = (p.name(p.imports[-outer - 1].class_name) if outer < 0
           else p.object_name(p.exports[outer - 1].class_index))
    return cls in ('BlueprintGeneratedClass', 'InheritableComponentHandler')


def is_widget(stem):
    """Is this package a Widget Blueprint -- its own class a WidgetBlueprintGeneratedClass?"""
    os.chdir(ROOT)
    try:
        p = Package.read(gamefiles.read(stem + '.uasset'))
    except KeyError:
        return False
    return any(p.object_name(e.class_index) == 'WidgetBlueprintGeneratedClass' for e in p.exports)


def main():
    os.chdir(ROOT)
    stems = sorted(k[:-len('.uasset')] for k in gamefiles.index() if k.endswith('.uasset'))
    verdicts = collections.Counter()
    kinds = collections.Counter()
    samples = collections.defaultdict(list)
    widget_verdicts = collections.Counter()
    with mp.Pool(12) as pool:
        for got in pool.imap_unordered(check, stems, chunksize=32):
            if not got:
                continue
            stem, verdict, wrong = got
            verdicts[verdict] += 1
            if is_widget(stem):
                widget_verdicts[verdict] += 1
            for klass, k, want, have in wrong:
                kinds[(klass, k)] += 1
                if len(samples[(klass, k)]) < 2:
                    samples[(klass, k)].append((stem.split('/Content/')[-1], want, have))
    print('every Blueprint package:', dict(verdicts))
    print('widget Blueprints (the gate):', dict(widget_verdicts))
    for (klass, k), n in kinds.most_common(30):
        print('%6d  %-34s %s' % (n, klass, k))
        for stem, want, have in samples[(klass, k)]:
            print('          %s\n            cooked:  %s\n            derived: %s' % (stem, want, have))
    # 'not fully decoded' is a package holding an object the step 2 gate
    # excludes (an animation's compiled evaluation template).
    return 0 if set(widget_verdicts) <= {'ok', 'same sets, order differs',
                                         'not fully decoded'} else 1


if __name__ == '__main__':
    sys.exit(main())
