"""The Chandelier trap (riftlord/mapeditor/chandelier.py): its measured constants are what
the meshes measure; the kit builds byte for byte the same twice; its Blueprint declares the
replicated fall and the overrides it needs; its table row and class name each other; the
Chandelier Trap compound hangs it from its hook with the chain length the build writes, and
one off its chain gets none; it is three times as wide and still clears the tallest enemy at
the barrels' height; shots block on it and its chain box and reach the trap base's OnHit; and the offline check places two chains on Scenario_08 with
every gate passing.

    python3 tests/offline/test_chandelier.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from riftlord.mapeditor import chandelier as C  # noqa: E402
from riftlord.mapeditor import compounds, kitactors, leveldata  # noqa: E402

checks = []


def check(name, ok, detail=''):
    checks.append(bool(ok))
    print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, (': %s' % (detail,)) if detail else ''))


def measured():
    m = C.measure()
    check('the hook, bottom, half height and half width are what the mesh measures',
          (m['hook'], m['bottom'], m['halfHeight'], m['halfWidth'])
          == (C.HOOK, C.BOTTOM, C.HALF_HEIGHT, C.HALF_WIDTH), m)
    check('the twelve sconces are the mesh\'s pans', sorted(m['sconces']) == sorted(C.SCONCES)
          and len(C.SCONCES) == 12, m['sconces'])
    check('the candle tip and width are candle02\'s',
          (m['candleTip'], m['candleWidth']) == (C.CANDLE_TIP, C.CANDLE_WIDTH))
    check('flame, damage and light follow from the shipped numbers',
          C.FLAME_SCALE == round(C.CANDLE_WIDTH / C.TORCH_FLAME_WIDTH, 3)
          and C.PHYSICAL_DAMAGE + C.FIRE_DAMAGE == C.BARREL_DAMAGE
          and C.DAMAGE_RADIUS == round(C.BARREL_RADIUS * C.HALF_WIDTH * C.SCALE
                                       / C.BARREL_BODY_RADIUS, 1)
          and abs(C.CANDLE_INTENSITY * len(C.SCONCES) - C.SHIPPED_LIGHT['intensity']) < 1e-3)


def size():
    check('the chandelier is three times as wide, uniformly', C.SCALE == 3.0)
    parts = kitactors.view_parts(C.CLASS)
    check('the editor draws it and its candles at that scale, hung from its hook',
          all(p['scale'] == [C.SCALE] * 3 for p in parts)
          and parts[0]['loc'] == [round(-c * C.SCALE, 3) for c in C.HOOK])
    check('at the boom barrels\' height it clears the tallest enemy (%g over the floor at '
          'least)' % C.MIN_HOOK_ABOVE, C.MIN_HOOK_ABOVE <= C.HANG_ABOVE)


def shots():
    bad = C.hit_problems()
    check('shots block on the chandelier and its chain box both ways, and their hits go to '
          'the trap base\'s OnHit', not bad, bad)
    keep = C.HIT_PROFILE
    C.HIT_PROFILE = 'OMDTrapProjectile'
    try:
        caught = C.hit_problems({C.inner(C.PACKAGE) + '.uasset': b'OnComponentHit OnHit'})
    finally:
        C.HIT_PROFILE = keep
    check('a profile that shots pass through is caught', caught, caught)


def kit():
    first = C.kit()
    C._KIT.clear()
    second = C.kit()
    check('the kit builds byte for byte the same twice (%d files)' % len(first),
          first == second, sorted(k for k in first if first[k] != second.get(k))[:3])
    want = {C.inner(p) + ext for p in (C.PACKAGE, C.TABLE, C.FADE_MIC, C.CANDLE_FADE_MIC,
                                       C.MRA_TEXTURE) for ext in ('.uasset', '.uexp')}
    check('the kit is the class, its table, the two fade copies and the MRA texture',
          want <= set(first) and all(k.startswith(C.inner(C.KIT_ROOT) + '/') for k in first),
          sorted(set(first) ^ want))
    head = first[C.inner(C.PACKAGE) + '.uasset']
    needed = (b'OnRep_Fallen', b'Fallen', b'LandZ', b'FellAt', b'BP_CustomInteraction',
              b'BP_CanInteract', b'BP_CanSell', b'ChainLength', b'Report')
    check('the Blueprint declares its replicated fall and overrides',
          all(n in head for n in needed), [n for n in needed if n not in head])
    check('the table row and the class name each other', not C._row_problems(first),
          C._row_problems(first))


def compound():
    layout = compounds.layout(C.COMPOUND)
    hook = [m for name, _k, m in layout if name == C.CLASS]
    check('the Chandelier Trap compound hangs one chandelier', len(hook) == 1,
          [name for name, _k, _m in layout])
    items = C.hanging((1000.0, 2000.0, 1500.0), 1900.0, group='g')
    trap = next(i for i in items if C.is_item(i))
    plan = {'added': items}
    drop = C._hook_drop(items[0]['params']['links'])
    check('the build\'s chain length is the compound\'s drop from the post to the hook',
          abs(C.chain_length(trap, plan) - drop) < 0.01, (C.chain_length(trap, plan), drop))
    check('the hook lands where asked', [round(v, 2) for v in trap['matrix'][12:15]]
          == [1000.0, 2000.0, 1500.0], trap['matrix'][12:15])
    alone = dict(trap, group=None)
    check('a chandelier with no chain of its own gets no chain box',
          C.chain_length(alone, {'added': [alone]}) == 0.0)
    moved = dict(trap, matrix=list(trap['matrix']))
    moved['matrix'][12] += 2 * C.CHAIN_XY_TOLERANCE
    check('a chandelier moved off its chain gets no chain box',
          C.chain_length(moved, {'added': items[:-1] + [moved]}) == 0.0)
    kinds = {r['name']: r['kind'] for r in leveldata.catalogue()}
    check('the chandelier is a New Object kit piece', kinds.get(C.CLASS) == 'kit')
    rows = [r for r in compounds.catalogue_rows([]) if r['name'] == C.COMPOUND]
    check('the Chandelier Trap card is under Environmental Traps',
          rows and rows[0]['group'] == kitactors.TRAPS_GROUP)
    check('the editor draws the chandelier and twelve candles',
          [p['mesh'] for p in kitactors.view_parts(C.CLASS)] == [C.MESH] + [C.CANDLE] * 12)
    check('the hung item hangs at the boom barrels\' height',
          compounds.HUNG[C.COMPOUND]['above'] == C.HANG_ABOVE)


def placed():
    with tempfile.TemporaryDirectory(prefix='chandelier-check-') as out:
        fails = C.check(out, log=lambda s: print('   ' + s))
    check('the offline check places two chains with every gate passing', not fails, fails[:3])


if __name__ == '__main__':
    measured()
    size()
    shots()
    kit()
    compound()
    placed()
    print('%d of %d checks passed' % (sum(checks), len(checks)))
    raise SystemExit(0 if all(checks) else 1)
