"""A level's intro card: the scenario title widget its script shows at mission start,
and removing it at build.

`BP_PlayIntro` in the persistent level's script creates a `UBP_ScenarioIntro_NN` or
`UBP_GenericScenarioIntro` widget (input UI only, ten seconds, Skip button), adds it to
the viewport and binds its `AnimationFinished` to a handler ("Widget Intro Finished").
The handler starts the story dialogue (gated by the player's dialogue setting) and, on
Scenario_06 and _09, swaps the intro tint sequences. 23 of the 33 shipped levels have
one; the Ice, Sand and Other levels have none.

The intro is a five-statement block in the ubergraph followed by `PopExecutionFlow`:

    [Push(X)]  GetPlayerController  Create(widget)  AddToViewport  BindDelegate(H)
               AddMulticastDelegate(AnimationFinished)  Pop

Removing it replaces the block, in place and at the same in-memory size, with a push
of the handler's entry, so the pop runs the handler at once. When the block follows a
`Push(X)` (war levels: X plays `MusicIntroStart` and, on 06/09, starts the tint), that
push is retargeted to the handler and the block pushes X, so X still runs first.
No other statement moves. Plan key: `plan['world']['intro']` = 'remove'.
"""

import functools
import struct

#: The plan's world setting, and its values.
PLAN_KEY = 'intro'
KEEP, REMOVE = 'keep', 'remove'

#: Fragment of every intro widget's class name.
WIDGET_MARK = 'ScenarioIntro'

_NOTHING, _PUSH = 0x0B, 0x4C


def _resolver(pkg, names):
    def resolve(i):
        if i < 0:
            return names[struct.unpack_from('<I', pkg.imports[-i - 1], 20)[0]]
        if i > 0:
            return names[struct.unpack_from('<I', pkg.exports[i - 1]['meta'], 16)[0]]
        return 'null'
    return resolve


def _scripts(pkg):
    """{function name: (export, (offset, mem, size, walker))} plus (names, resolve)."""
    from riftlord.onslaught import bppatch
    from riftlord.formats.ue import kismet
    names = [n for n, _h in pkg.names]
    resolve = _resolver(pkg, names)
    out = {}
    for export, fname in bppatch.functions(pkg, names, resolve):
        found = kismet.find_bytecode(export['data'], names, resolve)
        if found:
            out[fname] = (export, found)
    return out, names, resolve


def _entry(found):
    """The ubergraph entry an event stub calls, or None."""
    nodes = found[3].nodes
    if not nodes or nodes[0].name != 'LocalFinalFunction':
        return None
    for x in nodes[0].walk():
        if x.name == 'IntConst':
            return x.ops[0][1]
    return None


def _creates_intro(top, resolve):
    for x in top.walk():
        if x.name == 'FinalFunction' and resolve(x.ops[0][1]) == 'Create':
            for y in x.walk():
                if y.name == 'ObjectConst':
                    cls = resolve(y.ops[0][1])
                    if WIDGET_MARK in cls and cls.endswith('_C'):
                        return cls
    return None


def _calls(top, resolve, fn):
    return any(x.name in ('CallMath', 'FinalFunction') and resolve(x.ops[0][1]) == fn
               for x in top.walk())


def _sites(scripts, names, resolve):
    """[(ubergraph name, site)] for each intro block; site = {start, end (statement
    indices of the block), widget, handler, entry, push (index of a Push before the
    block or None), problem}."""
    from riftlord.formats.ue import kismet
    out = []
    for fname, (_export, found) in scripts.items():
        if not fname.startswith('ExecuteUbergraph'):
            continue
        nodes = found[3].nodes
        for i, top in enumerate(nodes):
            cls = _creates_intro(top, resolve)
            if not cls:
                continue
            site = {'widget': cls, 'handler': None, 'entry': None, 'push': None,
                    'start': i, 'end': None, 'problem': None}
            out.append((fname, site))
            if i and _calls(nodes[i - 1], resolve, 'GetPlayerController'):
                site['start'] = i - 1
            for j in range(i + 1, min(i + 5, len(nodes) - 2)):
                if nodes[j].name == 'BindDelegate':
                    site['handler'] = names[nodes[j].ops[0][1]]
                    if nodes[j + 1].name == 'AddMulticastDelegate' \
                            and nodes[j + 2].name == 'PopExecutionFlow':
                        site['end'] = j + 1
                    break
            if site['end'] is None:
                site['problem'] = 'the intro block is not followed by a pop'
                continue
            handler = scripts.get(site['handler'])
            site['entry'] = _entry(handler[1]) if handler else None
            if site['entry'] is None:
                site['problem'] = 'no entry point for %s' % site['handler']
                continue
            s = site['start']
            if s and nodes[s - 1].name == 'PushExecutionFlow':
                site['push'] = s - 1
            mm = kismet.memory_map(nodes)
            lo = mm[s][0] if site['push'] is None else mm[s - 1][0]
            hi = mm[site['end'] + 1][0]
            entries = {_entry(f) for n, (_e, f) in scripts.items() if n != fname}
            targets = ({n.ops[k][1] for n, k in kismet.code_offsets(nodes)} | entries) - {None}
            if site['push'] is None:
                inside = [t for t in targets if lo < t < hi]
            else:
                inside = [t for t in targets if lo + 5 <= t < hi]
            if inside:
                site['problem'] = 'code jumps into the intro block (%s)' % inside[:3]
    return out


def scan(pkg):
    """[{widget, handler, entry, ok, problem}] for each intro block in a level package."""
    scripts, names, resolve = _scripts(pkg)
    return [{'widget': s['widget'], 'handler': s['handler'], 'entry': s['entry'],
             'war': s['push'] is not None, 'problem': s['problem']}
            for _f, s in _sites(scripts, names, resolve)]


@functools.lru_cache(maxsize=64)
def _shipped(level):
    from riftlord.core import gamefiles
    from riftlord.build.minimap import mapdata
    from riftlord.formats.ue.uassetio import Package
    pkg = Package(*gamefiles.package(mapdata.map_path(level)))
    pkg.load_payloads()
    return pkg


@functools.lru_cache(maxsize=64)
def survey(level):
    """The shipped level's intro: {widget, handler, war} or None when it has none."""
    got = scan(_shipped(level))
    if not got:
        return None
    s = got[0]
    return {'widget': s['widget'], 'handler': s['handler'], 'war': s['war'],
            'removable': not any(g['problem'] for g in got)}


def has_intro(level):
    """True when the level's script shows an intro card."""
    return survey(level) is not None


def remove(pkg):
    """Remove the intro card from a level package in place. Returns
    {'removed': n, 'skipped': [reason]}."""
    from riftlord.formats.ue import kismet
    done = {'removed': 0, 'skipped': []}
    scripts, names, resolve = _scripts(pkg)
    by_graph = {}
    for fname, site in _sites(scripts, names, resolve):
        if site['problem']:
            done['skipped'].append(site['problem'])
            continue
        by_graph.setdefault(fname, []).append(site)
    for fname, sites in by_graph.items():
        export, (off, mem, size, walker) = scripts[fname]
        nodes = list(walker.nodes)
        before = kismet.memory_map(nodes)
        kept = {id(n): m for m, _s, n in before}
        for site in sorted(sites, key=lambda s: -s['start']):
            s, e = site['start'], site['end']
            span = before[e + 1][0] - before[s][0]
            push = kismet.Node(_PUSH, 'PushExecutionFlow', 0)
            if site['push'] is None:
                push.ops = [('u32', site['entry'])]
            else:
                outer = nodes[site['push']]
                k = next(j for j, (kind, _v) in enumerate(outer.ops) if kind == 'u32')
                push.ops = [('u32', outer.ops[k][1])]
                outer.ops[k] = ('u32', site['entry'])
            pad = span - kismet.node_sizes(push)[1]
            nodes[s:e + 1] = [push] + [kismet.Node(_NOTHING, 'Nothing', 0)
                                       for _ in range(pad)]
            done['removed'] += 1
        after = kismet.memory_map(nodes)
        moved = [n for m, _s, n in after if id(n) in kept and kept[id(n)] != m]
        total = sum(kismet.node_sizes(n)[1] for n in nodes)
        if moved or total != mem:
            raise SystemExit('intro edit would move %d statement(s); not written' % len(moved))
        script = kismet.emit(nodes)
        data = bytes(export['data'])
        export['data'] = (data[:off] + struct.pack('<ii', mem, len(script)) + script
                          + data[off + 8 + size:])
        export['size'] = len(export['data'])
    return done


def wanted_removed(plan):
    """True when the plan removes the level's intro card."""
    return ((plan or {}).get('world') or {}).get(PLAN_KEY) == REMOVE


def apply(builder, plan, report):
    """Remove the intro card from the level being built when the plan asks."""
    if not wanted_removed(plan):
        return
    got = remove(builder.pkg)
    if got['removed']:
        report['notes'].append('intro card removed')
    elif not got['skipped']:
        report['notes'].append('this level has no intro card to remove')
    if got['skipped']:
        from riftlord.core import buildmessage
        report['skipped'].append(buildmessage.left_out(
            'the intro card', '; '.join(got['skipped']), effect='it may still show',
            verb='remove'))


def check(pkg, level):
    """[{name, ok, detail}]: a built level package read back against the shipped
    level's intro. The card must be gone, the handler's entry pushed where the block
    was, the pop that runs it in place, and the handler still calling that entry."""
    from riftlord.formats.ue import kismet
    ship = _shipped(level)
    s_scripts, s_names, s_resolve = _scripts(ship)
    s_sites = [(f, s) for f, s in _sites(s_scripts, s_names, s_resolve) if not s['problem']]
    scripts, names, resolve = _scripts(pkg)
    left = [s['widget'] for _f, s in _sites(scripts, names, resolve)]
    out = [{'name': 'intro card gone from the level script', 'ok': not left,
            'detail': ', '.join(left) or '%d removed' % len(s_sites)}]
    graphs = [f for f in scripts if f.startswith('ExecuteUbergraph')]
    bad = []
    for _f, site in s_sites:
        if len(graphs) != 1:
            bad.append('%d ubergraphs' % len(graphs))
            break
        s_found = s_scripts[_f][1]
        found = scripts[graphs[0]][1]
        if found[1] != s_found[1]:
            bad.append('ubergraph size %d, shipped %d' % (found[1], s_found[1]))
            continue
        s_mm = kismet.memory_map(s_found[3].nodes)
        at = {m: n for m, _s, n in kismet.memory_map(found[3].nodes)}
        first = s_mm[site['start'] if site['push'] is None else site['push']][0]
        pushes = []
        m = first
        while m in at and at[m].name == 'PushExecutionFlow':
            pushes.append(at[m].ops[0][1])
            m += kismet.node_sizes(at[m])[1]
        pop = s_mm[site['end'] + 1][0]
        if not pushes or pushes[-1 if site['push'] is None else 0] != site['entry']:
            bad.append('%s entry %d not pushed at %d (%s)'
                       % (site['handler'], site['entry'], first, pushes))
        if site['push'] is not None and pushes[1:2] != [s_found[3].nodes[site['push']].ops[0][1]]:
            bad.append('the music push at %d is lost (%s)' % (first, pushes))
        if pop not in at or at[pop].name != 'PopExecutionFlow':
            bad.append('no pop at %d' % pop)
        handler = scripts.get(site['handler'])
        if handler is None or _entry(handler[1]) != site['entry']:
            bad.append('%s no longer calls entry %d' % (site['handler'], site['entry']))
    out.append({'name': 'intro handler runs in its place', 'ok': not bad and bool(s_sites),
                'detail': '; '.join(bad) or ', '.join(s['handler'] for _f, s in s_sites)})
    return out


def check_files(files, level):
    """check() on the persistent level among built `files` ('OMD/Content/...' ->
    bytes): the .umap whose names include BP_PlayIntro."""
    from riftlord.formats.ue.uassetio import Package
    for key, data in files.items():
        if not key.endswith('.umap') or b'BP_PlayIntro' not in data:
            continue
        ux = files.get(key[:-5] + '.uexp')
        if ux is None:
            continue
        pkg = Package(data, ux)
        pkg.load_payloads()
        return check(pkg, level)
    return [{'name': 'intro card gone from the level script', 'ok': False,
             'detail': 'no built level carries BP_PlayIntro'}]
