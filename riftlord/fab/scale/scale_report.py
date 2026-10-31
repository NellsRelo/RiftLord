# Generated from OMD2-Converter/thirdparty/scale-tools/scale_report.py by thirdparty/fabconvert/port_riftlord.py; edit the source.
"""Turn the measured features into per-pack scale candidates, fit and clearance checks.

    python3 scale_report.py <scale dir>      # reads omd3-features.json, pack-*.json
                                             # writes scale-summary.json, prints tables

Every tunable is a named constant below. Exclusions are listed per pack with the reason;
they remove pieces the name rules caught but that are not the feature (a wall section
named "fence", a whole storey named "railing", a ladder in the stairs role).
"""

import glob
import json
import math
import os
import re
import statistics
import sys

# ---------------------------------------------------------------- OMD3 constants
#: One trap cell, uu (kit-catalogue.json, PLAYABLE-AREA.md).
CELL = 300.0
#: NavPower/Recast agent limits from DefaultEngine.ini (measured): max step and slope.
MAX_STEP = 48.0
MAX_SLOPE_DEG = 45.0
#: Largest walking enemy capsule (characters.json, measured on BP_Ogre_*/Troll/EarthLord).
BIG_CAPSULE_H = 360.0
BIG_CAPSULE_R = 80.0
#: The OMD3 "human-scale" doorways: shipped, placed in gameplay levels at scale 1.0
#: (Door_Catacomb x16, Wall_Sec_Arch x26) plus SM_Door_01 (in the catalogue, unplaced).
OMD3_HUMAN_DOORS = ('Door_Catacomb', 'Wall_Sec_Arch', 'SM_Door_01')

# ---------------------------------------------------------------- fit constants
#: A scaled footprint axis "lands on the grid" when it is within this many uu of a whole
#: number of cells (>= 1). 15uu = 5% of a cell, under OMD3's own trap-box offsets
#: (grid mid-plane within 10uu of the collider for 100% of shipped own-box grids).
FIT_TOL = 15.0
#: Axes shorter than this after scaling are thickness (walls, slabs) and are not tested.
FIT_MIN_AXIS = 100.0
#: Candidate uniform scales tested for every pack, besides its character-match scale.
CANDIDATES = (0.75, 1.0, 1.5)

# ---------------------------------------------------------------- pack curation
#: Pieces that are not a door/window opening: gaps in fences, grilles, low arcades, a
#: portcullis grille's bars, and monumental palace doors (not domestic doors).
NOT_DOOR_RX = r'(?i)fence|grid|railing|fencing|chains|curved|wooden_arch|portcullis'
#: A door opening must be at least this wide and tall (pack units); smaller is a gap.
DOOR_MIN_W = 60.0
DOOR_MIN_H = 150.0
#: Leaves that are not doors (a window shutter).
NOT_LEAF_RX = r'(?i)window_door'
#: Stairs that are walkable flights: tread centroids follow height, a real going, wide
#: enough for a person (excludes ladders and 28-36uu wall ledges).
STAIR_MIN_CORR = 0.85
STAIR_MIN_GOING = 10.0
STAIR_MIN_WIDTH = 60.0
#: ...and a riser no real flight has (a balcony or wall piece in the stairs role).
STAIR_MAX_RISER = 60.0

PER_PACK = {
    'City_of_Brass_Enviroment': {
        # 1,276-1,396uu palace doors and upper-storey corridor openings: monumental
        'door_exclude': r'^(Room_A_Door300A|Room_A_DoorA|Corridor_Palace|Corridor_B|Room_A_WallShort)',
        # every "railing" here is a whole wall section 450-1,545uu tall
        'railing_exclude': r'.',
        # "Box" pieces are 250-690uu architectural blocks, not crates
        'prop_exclude': {'crate': r'.'},
    },
    'Asian_Village': {
        # SM_wall_fence_* are 565uu wall sections; *_column_* are posts
        'railing_exclude': r'wall_fence|column|fence_06',
    },
    'Stylized_Egypt': {
        'railing_exclude': r'wall_fence',     # 800uu walls
    },
    'Medieval_Dock': {
        'prop_exclude': {'crate': r'barrel_box'},   # a barrel-and-box stack, 177uu
    },
}

#: OMD3 prop references (catalogue mesh height at scale 1). OMD3 itself places props at
#: 0.7-2.0 scale (Barrel, Crate in survey-raw), so props corroborate, they do not pin.
OMD3_PROPS = {'barrel': ('Barrel_SM', 'Barrel'), 'crate': ('Crate',),
              'table': ('SM_Table_01', 'SM_Table_02')}

#: Packs with characters of their own (packchars.py output in the scale dir). Their
#: highest reference-skeleton bone is compared with OMD3's hero mesh top [inferred: the
#: top bone stands in for the top of the head].
PACK_CHARACTERS = {'City_of_Brass_Enviroment': 'pack-chars-CityofBrass_Enemies.json',
                   'City_of_Brass_Props': 'pack-chars-CityofBrass_Enemies.json'}

#: Real-world proportions used ONLY to infer each pack's own human height [inferred]:
#: riser 17.5cm and door 2.10m for a 1.80m person.
REAL_RISER_PER_HUMAN = 17.5 / 180.0
REAL_DOOR_PER_HUMAN = 210.0 / 180.0


def med(v):
    v = [x for x in v if x is not None]
    return statistics.median(v) if v else None


def dist(vals):
    vals = sorted(float(v) for v in vals if v is not None)
    if not vals:
        return None
    q = lambda f: vals[min(len(vals) - 1, int(round(f * (len(vals) - 1))))]
    return {'n': len(vals), 'min': round(vals[0], 1), 'p25': round(q(0.25), 1),
            'median': round(statistics.median(vals), 1), 'p75': round(q(0.75), 1),
            'max': round(vals[-1], 1)}


def fits(extent):
    k = round(extent / CELL)
    return k >= 1 and abs(extent - k * CELL) <= FIT_TOL


def grid_fit(footprints, s):
    """Share of architectural pieces whose every long footprint axis lands on whole cells."""
    tested = ok = 0
    for name, d in footprints.items():
        axes = [d[0] * s, d[1] * s]
        long_axes = [a for a in axes if a >= FIT_MIN_AXIS]
        if not long_axes:
            continue
        tested += 1
        if all(fits(a) for a in long_axes):
            ok += 1
    return {'tested': tested, 'fit': ok, 'share': round(ok / tested, 3) if tested else None}


def clear_height(o, s, width):
    """Clear height (uu, after scaling by s) of an opening for a body `width` wide."""
    run = 0
    for w in o['profile']:
        if w * s < width:
            break
        run += 1
    return run * o['profile_res'] * s


def curate(p, name):
    cfg = PER_PACK.get(name, {})
    doors = [o for o in p['openings'] if o['kind'] == 'door' and not re.search(NOT_DOOR_RX, o['source'])
             and o['width_min_lower_half'] >= DOOR_MIN_W and o['height'] >= DOOR_MIN_H
             and not (cfg.get('door_exclude') and re.search(cfg['door_exclude'], o['source']))]
    leaves = [l for l in p['door_leaves'] if not re.search(NOT_LEAF_RX, l['source'])
              and not (cfg.get('door_exclude') and re.search(cfg['door_exclude'], l['source']))]
    windows = [o for o in p['openings'] if o['kind'] == 'window' and not re.search(NOT_DOOR_RX, o['source'])
               and o['width_min_lower_half'] >= 40 and o['height'] >= 60]
    stairs = [s for s in p['stairs'] if s['axis_corr'] >= STAIR_MIN_CORR and s['going_median'] >= STAIR_MIN_GOING
              and s['width'] >= STAIR_MIN_WIDTH and s['riser_median'] <= STAIR_MAX_RISER]
    rails = {k: v for k, v in p['railings'].items()
             if not (cfg.get('railing_exclude') and re.search(cfg['railing_exclude'], k))}
    props = {}
    for fam, got in p['props'].items():
        rx = cfg.get('prop_exclude', {}).get(fam)
        props[fam] = {k: v for k, v in got['meshes'].items() if not (rx and re.search(rx, k))}
    return doors, leaves, windows, stairs, rails, props


def main(d):
    o3 = json.load(open(os.path.join(d, 'omd3-features.json')))
    hero = o3['hero_height']['median']
    # OMD3 references
    human_doors = {x['source']: x['height'] for x in o3['openings'] + o3['door_leaves']
                   if x['source'] in OMD3_HUMAN_DOORS}
    route_doors = [x for x in o3['openings'] if x['source'].startswith(('ArchwayGate', 'Archway_1x2'))
                   and x['width_min_lower_half'] >= DOOR_MIN_W]
    ref = {
        'hero_height': hero,
        'human_door_height': med(human_doors.values()),
        'human_doors': human_doors,
        'route_door_clear_height': dist([x['clear_height_for_width']['r80'] for x in route_doors]),
        'route_door_width': dist([x['width_min_lower_half'] for x in route_doors]),
        'riser': o3['riser']['median'], 'going': o3['going']['median'],
        'railing': o3['railing_height']['median'],
        'props': {f: med([o3['props'][m]['size'][2] for m in ms if m in o3['props']]) for f, ms in OMD3_PROPS.items()},
    }
    ref['route_door_clearance_over_big_capsule'] = round(ref['route_door_clear_height']['min'] - BIG_CAPSULE_H, 1)
    out = {'generated_by': 'scale-tools/scale_report.py', 'omd3_reference': ref,
           'constants': {'CELL': CELL, 'FIT_TOL': FIT_TOL, 'FIT_MIN_AXIS': FIT_MIN_AXIS,
                         'MAX_STEP': MAX_STEP, 'BIG_CAPSULE': [BIG_CAPSULE_H, BIG_CAPSULE_R]},
           'packs': {}}
    print('OMD3 reference', json.dumps(ref, indent=1))
    for f in sorted(x for x in glob.glob(os.path.join(d, 'pack-*.json')) if not os.path.basename(x).startswith('pack-chars-')):
        name = os.path.basename(f)[5:-5]
        p = json.load(open(f))
        doors, leaves, windows, stairs, rails, props = curate(p, name)
        door_h = med([x['height'] for x in doors] + [l['height'] for l in leaves])
        door_w = med([x['width_min_lower_half'] for x in doors])
        riser = med([s['riser_median'] for s in stairs])
        going = med([s['going_median'] for s in stairs])
        rail = med(rails.values())
        prop_h = {fam: med([v[2] for v in got.values()]) for fam, got in props.items()}
        anchors = {}
        if riser:
            anchors['riser'] = ref['riser'] / riser
        if door_h:
            anchors['door'] = ref['human_door_height'] / door_h
        corrob = {}
        for fam, r in ref['props'].items():
            if prop_h.get(fam) and r:
                corrob[fam] = r / prop_h[fam]
        if rail:
            corrob['railing'] = ref['railing'] / rail
        pc = PACK_CHARACTERS.get(name)
        if pc and os.path.exists(os.path.join(d, pc)):
            tops = [m['skeleton_top'] for m in json.load(open(os.path.join(d, pc)))['meshes']
                    if m.get('skeleton_top')]
            if tops:
                corrob['characters'] = hero / statistics.median(tops)
        s_char = math.exp(statistics.mean(math.log(v) for v in anchors.values())) if anchors else None
        rec = {
            'features': {
                'door_height': dist([x['height'] for x in doors] + [l['height'] for l in leaves]),
                'door_width_frames': dist([x['width_min_lower_half'] for x in doors]),
                'door_clear_height_ogre_width': dist([x['clear_height_for_width']['r80'] for x in doors]),
                'window_height': dist([x['height'] for x in windows]),
                'window_sill': dist([x['sill'] for x in windows]),
                'riser': dist([s['riser_median'] for s in stairs]),
                'going': dist([s['going_median'] for s in stairs]),
                'railing_height': dist(rails.values()),
                'wall_height': p['wall_height'], 'floor_thickness': p['floor_thickness'],
                'column_diameter': p['column_diameter'],
                'props': {fam: dist([v[2] for v in got.values()]) for fam, got in props.items()},
            },
            'sources': {'doors': [x['source'] for x in doors], 'leaves': [l['source'] for l in leaves],
                        'stairs': [s['source'] for s in stairs], 'railings': sorted(rails)},
            'implied_human_height_inferred': {
                'from_riser': round(riser / REAL_RISER_PER_HUMAN, 0) if riser else None,
                'from_door': round(door_h / REAL_DOOR_PER_HUMAN, 0) if door_h else None},
            # uniform scale keeps every slope, so this is scale-independent
            'stair_slopes_deg': {s['source']: round(math.degrees(math.atan2(s['riser_median'], s['going_median'])), 1)
                                 for s in stairs},
            'stairs_steeper_than_max_slope': [s['source'] for s in stairs
                                              if math.degrees(math.atan2(s['riser_median'], s['going_median'])) > MAX_SLOPE_DEG],
            'scale_anchors': {k: round(v, 3) for k, v in anchors.items()},
            'scale_corroboration': {k: round(v, 3) for k, v in corrob.items()},
            's_char': round(s_char, 3) if s_char else None,
        }
        scales = sorted(set(CANDIDATES + ((round(s_char, 3),) if s_char else ())))
        checks = {}
        for s in scales:
            c = {'grid_fit': grid_fit(p['arch_footprints'], s)}
            if door_h:
                c['door_height'] = round(door_h * s, 1)
                c['door_over_hero'] = round(door_h * s / hero, 2)
            if doors:
                ch = [clear_height(x, s, 2 * BIG_CAPSULE_R) for x in doors]
                c['doors_passing_big_enemy'] = sum(1 for a in ch if a >= BIG_CAPSULE_H)
                c['doors_tested'] = len(doors)
                c['door_clear_h_big_enemy'] = dist(ch)
                c['door_clear_h_hero'] = dist([clear_height(x, s, 2 * 42) for x in doors])
            if riser:
                c['riser'] = round(riser * s, 1)
                c['riser_max'] = round(max(x['riser_median'] for x in stairs) * s, 1)
                c['riser_ok'] = c['riser_max'] <= MAX_STEP
                c['slope_deg'] = round(math.degrees(math.atan2(riser, going)), 1) if going else None
            if prop_h.get('barrel'):
                c['barrel'] = round(prop_h['barrel'] * s, 1)
            if prop_h.get('table'):
                c['table'] = round(prop_h['table'] * s, 1)
            checks[str(s)] = c
        rec['checks'] = checks
        if s_char:
            rec['char_vs_075'] = round(s_char / 0.75, 2)
            rec['char_vs_150'] = round(s_char / 1.5, 2)
        out['packs'][name] = rec
        print('\n==', name, 'door', door_h, 'riser', riser, 'going', going, 'rail', rail, 'props', prop_h)
        print('   anchors', rec['scale_anchors'], 'corrob', rec['scale_corroboration'], 's_char', rec['s_char'])
        print('   human (inferred)', rec['implied_human_height_inferred'])
        for s, c in checks.items():
            print('   s=%-6s %s' % (s, c))
    json.dump(out, open(os.path.join(d, 'scale-summary.json'), 'w'), indent=1)


if __name__ == '__main__':
    main(sys.argv[1])
