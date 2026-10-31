"""Kit pieces as their donor placements build them, and previews drawn from all their parts.

A kit piece added to a level is LIFTED from a shipped level that places it (apply._add,
apply.donor_for: omd3actors' donor, export and all, instance overrides included), so what
the game shows is that placement, not the bare Blueprint class. The Map Editor drew the
class's component templates instead (omd3actors.parts_of), which leave out each
component's AttachParent: a part held under a turned or scaled parent component came out
on its side -- Stairs_MultiTiered_B's stairs stood rolled 90 degrees and squashed. The
donor level's own reader composes the attach chain (omd3level.read_package), so each part
is read there relative to its actor and kept here.

    python3 -m riftlord.kitdonors              # rebuild kit-donor-parts.json
    python3 -m riftlord.kitdonors --thumbs     # and render every kit preview
    python3 -m riftlord.kitdonors --check Stairs_MultiTiered_B_C
"""

import argparse
import functools
import io
import json
import math
import os
import sys
from riftlord.core import gamepath

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

OUT = gamepath.data('kit-donor-parts.json')
VERSION = 1
#: Classes read besides the kit catalogue's: what the Map Editor places as kit pieces.
EXTRA_CLASSES = ('BP_OMDTeleportRift_C',)
#: Preview size in pixels (omd3thumb).
THUMB_SIZE = 96
#: Name prefix of a kit preview among omd3serve's previews.
THUMB_PREFIX = 'kit__'
#: A catalogue row's `thumb` for a kit class: /api/thumb/kit:<class>.
THUMB_NAME = 'kit:'


def kit_classes():
    """Every kit class the Map Editor offers (kit-catalogue.json), plus EXTRA_CLASSES."""
    try:
        with open(gamepath.data('kit-catalogue.json')) as fh:
            kit = json.load(fh)
    except (OSError, ValueError):
        kit = {}
    names = [n if n.endswith('_C') else n + '_C' for n in kit]
    return sorted(set(names) | set(EXTRA_CLASSES))


def _rotator_of(flat):
    """(loc, (pitch, yaw, roll), scale) of a column-major matrix (apply.transform_of)."""
    from riftlord.build import apply
    loc, rot, scale = apply.transform_of(flat)
    return ([round(v, 4) for v in loc], [round(v, 5) for v in rot],
            [round(v, 5) for v in scale])


def survey(classes=None, log=lambda _s: None):
    """{class: [part]} read from each class's donor placement; a class whose donor cannot
    be read is left out (the class templates stand in for it)."""
    from riftlord.formats.omd3 import omd3actors
    from riftlord.formats.omd3 import omd3level
    actors = omd3actors.load()
    by_package = {}
    for cls in classes or kit_classes():
        info = actors.get(cls)
        if not info or not info.get('level'):
            continue
        path = info['level'].replace('../../../OMD/Content', '/Game')
        by_package.setdefault(path, []).append((cls, int(info['export'])))
    out = {}
    for path, wanted in sorted(by_package.items()):
        try:
            pieces, _l, _v, _a = omd3level.read_package(path, keep_local=True)
        except (Exception, SystemExit) as exc:       # noqa: BLE001
            log('%s: %s' % (path, exc))
            continue
        for cls, export in wanted:
            mine = [p for p in pieces if p.get('source') == 'blueprint'
                    and p.get('actor') == cls and p.get('export') == export + 1]
            if not mine:
                #: the recorded export is not that class's: any placement of it will do
                firsts = sorted({p['export'] for p in pieces if p.get('actor') == cls
                                 and p.get('source') == 'blueprint'})
                mine = [p for p in pieces if p.get('source') == 'blueprint'
                        and p.get('actor') == cls and firsts and p['export'] == firsts[0]]
            if not mine:
                continue
            parts = []
            for p in mine:
                loc, rot, scale = _rotator_of(p['local'])
                part = {'component': p.get('componentName') or p['mesh'], 'mesh': p['mesh'],
                        'loc': loc, 'rot': rot, 'scale': scale,
                        'skeletal': bool(p.get('skeletal'))}
                if p.get('material'):
                    part['material'] = p['material']
                if p.get('hidden'):
                    part['hidden'] = True
                parts.append(part)
            out[cls] = parts
        log('%s: %d of %d class(es)' % (path.rsplit('/', 1)[-1],
                                        sum(1 for c, _e in wanted if c in out), len(wanted)))
    return out


def write(found, path=OUT):
    with open(path, 'w') as fh:
        json.dump({'version': VERSION, 'parts': found}, fh, indent=1, sort_keys=True)


@functools.lru_cache(maxsize=1)
def load(path=OUT):
    """{class: [part]}, or {} when the file is missing or of another version."""
    try:
        with open(path) as fh:
            got = json.load(fh)
    except (OSError, ValueError):
        return {}
    return got.get('parts') or {} if got.get('version') == VERSION else {}


def parts(cls):
    """A class's parts as its donor placement builds them, or None."""
    return load().get(cls)


# -- previews -------------------------------------------------------------------------

def _matrix(part):
    from riftlord.formats.omd3 import omd3level
    return omd3level._matrix(part['loc'], part['rot'], part['scale'])


def model(cls):
    """Every drawn part of a kit class in actor space, each with its own materials (its
    override material, else its mesh's), as omd3thumb.merge gives it ('verts', 'tris',
    'uvs', 'faces', 'surfaces'), or None."""
    from riftlord.mapeditor import omd3serve
    from riftlord.mapeditor import omd3thumb
    from riftlord.mapeditor import leveldata
    got = parts(cls) or leveldata.kit_parts(cls).get('parts') or []
    placed = []
    for part in got:
        if part.get('hidden'):
            continue
        mesh = omd3serve.mesh(part['mesh'])
        if not mesh or not mesh.get('verts') or not mesh.get('tris'):
            continue
        placed.append(omd3thumb.place(mesh, _matrix(part), material=part.get('material')))
    return omd3thumb.merge(placed)


def thumbnail(cls, size=THUMB_SIZE):
    """A kit class's preview as PNG, drawn from all its parts, kept on disk; or None."""
    from riftlord.mapeditor import omd3serve
    from riftlord.mapeditor import omd3thumb
    cached = os.path.join(omd3serve.PREVIEWS, '%s%s__%s.png' % (
        THUMB_PREFIX, omd3serve.safe_name(cls), omd3thumb.STYLE))
    if os.path.exists(cached):
        with open(cached, 'rb') as fh:
            return fh.read()
    got = model(cls)
    if got is None:
        return None
    image = omd3thumb.render(got, size)
    if image is None:
        return None
    buf = io.BytesIO()
    image.save(buf, format='PNG')
    png = buf.getvalue()
    omd3serve.keep(cached, png)
    return png


def check(cls):
    """Lines comparing a class's donor parts with its class templates (omd3actors)."""
    from riftlord.formats.omd3 import omd3actors
    donor = {p['component']: p for p in parts(cls) or []}
    lines = []
    for p in omd3actors.parts_of(cls):
        d = donor.get(p['component'])
        if d is None:
            lines.append('%-34s not in the donor placement' % p['component'])
            continue
        drot = max(abs(((a - b + 180) % 360) - 180) for a, b in zip(p['rot'], d['rot']))
        dloc = math.dist(p['loc'], d['loc'])
        dsc = max(abs(a - b) for a, b in zip(p['scale'], d['scale']))
        flag = '  <-- differs' if drot > 0.5 or dloc > 1 or dsc > 1e-3 else ''
        lines.append('%-34s rot %s -> %s  loc moved %.0f  scale %s -> %s%s' % (
            p['component'], [round(v) for v in p['rot']], [round(v) for v in d['rot']],
            dloc, p['scale'], d['scale'], flag))
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--thumbs', action='store_true', help='also render every kit preview')
    ap.add_argument('--check', help='compare one class with its class templates')
    args = ap.parse_args(argv)
    if args.check:
        print('\n'.join(check(args.check)) or 'no parts')
        return 0
    found = survey(log=print)
    write(found)
    load.cache_clear()
    print('wrote %s: %d classes' % (OUT, len(found)))
    if args.thumbs:
        from riftlord.mapeditor import omd3serve
        done = 0
        for cls in sorted(found):
            stale = os.path.join(omd3serve.PREVIEWS, '%s%s.png' % (
                THUMB_PREFIX, omd3serve.safe_name(cls)))
            if os.path.exists(stale):
                os.remove(stale)
            if thumbnail(cls):
                done += 1
        print('rendered %d previews' % done)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
