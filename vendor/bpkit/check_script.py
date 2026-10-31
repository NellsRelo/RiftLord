"""Checks a compiled package's logic by means that share no code with the compiler.

    * kismet.py (the disassembler every whole-game check uses) must walk each
      function's bytecode exactly, and its own in-memory size must equal the
      size the function declares;
    * every jump lands on a statement boundary, and every Context's skip size
      equals the in-memory size of the expression it skips;
    * every call, argument type and property reference resolves against the
      type database plus this package's own class and functions
      (check_typedb.check_package);
    * every call by reference uses the opcode the Blueprint compiler would
      (bpkit.calls).
"""

import struct

import kismet
from bpkit.calls import opcode
from bpkit.typedb import add_package, load, object_key

JUMPS = ('Jump', 'JumpIfNot', 'PushExecutionFlow')


def verify_logic(p, path, base=None):
    """Problems with the package's bytecode; empty when sound. `base` is the
    type database to check against (the build's, holding the assets built
    before this one); the cached one by default."""
    from bpkit import check_typedb
    db = base if base is not None else load()
    overlay = {k: (dict(v) if isinstance(v, dict) else v) for k, v in db.items()}
    add_package(overlay, p, path)
    names = [str(n) for n in p.names]

    def resolve(i):
        return p.object_name(i) if i else 'null'

    problems = []
    for i, e in enumerate(p.exports, 1):
        if p.object_name(e.class_index) != 'Function':
            continue
        fn = p.object_name(i)
        found = kismet.find_bytecode(e.data, names, resolve)
        if not found:
            problems.append('%s: kismet cannot walk the bytecode' % fn)
            continue
        _off, declared, _size, w = found
        computed = sum(kismet.node_sizes(n)[1] for n in w.nodes)
        if computed != declared:
            problems.append('%s: declares %d bytes in memory, kismet counts %d'
                            % (fn, declared, computed))
        starts = {m for m, _s, _n in kismet.memory_map(w.nodes)}
        for top in w.nodes:
            for n in top.walk():
                if n.name in JUMPS:
                    target = next(v for k, v in n.ops if k == 'u32')
                    if target not in starts:
                        problems.append('%s: %s to %d, not a statement' % (fn, n.name, target))
                if n.name in ('Context', 'Context_FailSilent'):
                    skip = next(v for k, v in n.ops if k == 'u32')
                    member = [v for k, v in n.ops if k == 'expr'][-1]
                    if skip != kismet.node_sizes(member)[1]:
                        problems.append('%s: Context skips %d, its expression is %d'
                                        % (fn, skip, kismet.node_sizes(member)[1]))
                if n.name in ('FinalFunction', 'LocalFinalFunction', 'CallMath'):
                    target = object_key(p, n.ops[0][1], path)
                    entry = overlay['functions'].get(target)
                    # Super: an override calling the parent's version of itself,
                    # which the compiler always calls as final.
                    parent = p.object_name(n.ops[0][1]) == fn
                    if entry and entry.get('source') == 'headers' and \
                            opcode(entry, overlay, parent_context=parent) != n.name:
                        problems.append('%s: calls %s with %s, the compiler would use %s'
                                        % (fn, target, n.name, opcode(entry, overlay)))
    out, samples = check_typedb.check_package(p, path, overlay)
    for verdict, n in out.items():
        if not verdict.endswith('ok'):
            problems.append('%d x %s' % (n, verdict))
    problems += ['   %s: %s' % s for s in samples]
    return problems
