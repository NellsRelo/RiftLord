"""Custom campaigns and the notes OrcsMustUI shows for custom maps.

OrcsMustUI gives every row of a mod's campaign table (makemod.campaign_slot_path) a
hub tab of its own, and lists any mission no campaign names under Custom Maps. The
authoring tools build one pak per mission, so in Missions a campaign is a small pak of its
own, `900_<Name>_Campaign_P.pak`, holding only its campaign table (Install and Package
pack it with its missions as one pak: bucket._pack_campaign), in its own folder
/Game/Mods/<Name>_Campaign/Tables, which OrcsMustUI registers from there: its MissionList names
missions by Guid, wherever their tables live. Adding a mission to an installed
campaign rewrites that one table and nothing else in the pak.

The notes are the CustomMaps.yaml OrcsMustUI reads inside a pak, at
OMD/Content/Mods/<ModKey>/OrcsMustUI/CustomMaps.yaml, with a campaign's banner beside it
(`Banner: Tab.png`): author, version, description and hub style per mission, and a
description, hub style and banner per campaign, set with edit()
(Mods/OrcsMustUI/Docs/Registry.md, CustomMaps.md, Campaigns.md, HubStyle.md).
A loose build in Rift Lord's bucket keeps them beside it as `<stem>.CustomMaps.yaml` and
`<stem>.Tab.png` until it is packed (bucket._pack); a pak built before 2026-10-16 may
still have them beside it, and is read that way until it is rewritten.

    python3 customcampaign.py --list
"""

import argparse
import base64
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.build import makemod  # noqa: E402
from riftlord.build import missionproto  # noqa: E402
from riftlord.build import modtable  # noqa: E402
from riftlord.core import paksource  # noqa: E402
from riftlord.core import pkgcheck  # noqa: E402
from riftlord.formats.ue.uassetio import Package  # noqa: E402

#: A mod's campaign table inside a pak, as navpatch.pak_names keys it; or a retired
#: slot's, so a pak built before slots were retired still reads.
SLOT_TABLE = re.compile(r'^/Game/(?:CustomMaps/Slot(\d{3})/Slot\1|Mods/([^/]+)/Tables/\2)'
                        r'_CampaignProtos\.uasset$')
#: What the tools call "a new campaign" in a placement choice.
NEW = 'new'
#: The placement that lists a mission in the Endless hub alone: its own row is the Endless
#: entry, with no campaign row and no twin (makemod.build `endless_only`; a record keeps it
#: as `endlessOnly`, bucket._endless_variant). Offered to Map Editor missions.
ENDLESS = 'Endless'
#: Image types OrcsMustUI's banner loader (ImportFileAsTexture2D) reads.
BANNER_TYPES = ('.png', '.jpg', '.jpeg')


def slug(name):
    """A campaign name as asset-name letters and digits."""
    return ''.join(c for c in str(name or '') if c.isalnum()) or 'MyCampaign'


def pak_for(name, mods=None):
    """Where a new campaign's pak goes."""
    from riftlord.core import gamepath
    mods = mods or gamepath.target_dir()
    if not mods:
        raise SystemExit('Orcs Must Die 3 was not found, so there is nowhere to '
                         'install the campaign')
    return gamepath.path_in(mods, '900_%s_Campaign_P' % slug(name))


def notes_path(pak_path):
    """The notes file beside a loose build (or beside a pak built before 2026-10-16)."""
    return paksource.sidecar(pak_path, '.CustomMaps.yaml')


#: The notes' name inside a pak (makemod.OMUI_DIR).
NOTES_NAME = 'CustomMaps.yaml'


def _inner_notes(pak_path):
    """The notes text inside a pak, or None (a loose build, or none inside)."""
    if paksource.is_loose(pak_path) or not os.path.isfile(pak_path):
        return None
    try:
        inner = makemod.omui_files(paksource.names(pak_path)).get(NOTES_NAME)
        return paksource.read(pak_path, inner).decode('utf-8', 'replace') if inner else None
    except Exception:                                # noqa: BLE001
        return None


def build_notes(pak_path):
    """read_notes of a build's notes: inside a pak, else beside it."""
    text = _inner_notes(pak_path)
    return read_notes(notes_path(pak_path)) if text is None else parse_notes(text)


def read_game_type(pak_path, mission):
    """The GameType a build's notes give mission row `mission`, read back, or ''."""
    for e in build_notes(pak_path).get('Maps', []):
        if e.get('Mission') == mission:
            return e.get('GameType') or ''
    return ''


#: Hub scenes a map or campaign may name in its notes (OrcsMustUI Docs/HubStyle.md).
HUBS = ('OldFriends', 'DrasticSteps', 'ColdAsEyes', 'TippingTheScales')


def hub_name(value):
    """A hub style as the notes spell it, or '' for none (the level decides). Case,
    spaces, underscores and hyphens are ignored; anything else is refused."""
    key = ''.join(c for c in str(value or '') if c.isalnum()).lower()
    if not key:
        return ''
    for hub in HUBS:
        if hub.lower() == key:
            return hub
    raise SystemExit('%s is not a hub style; use one of %s' % (value, ', '.join(HUBS)))


def _entries(pak_path):
    """{inner path: bytes} for every file in a pak or loose build."""
    return paksource.entries(pak_path)


def _inner(game_path, ext):
    return game_path.replace('/Game/', 'OMD/Content/', 1) + ext


def _table(ua, ux):
    pkg = Package(ua, ux)
    pkg.load_payloads()
    return missionproto.Table(pkg)


def _text(table, row, field):
    """The display string of an FText field: its last printable run."""
    f = table.field(row, field)
    runs = ([b.decode('ascii', 'ignore') for b in re.findall(rb'[ -~]{3,}', bytes(f.payload))]
            if f is not None else [])
    return runs[-1] if runs else ''


def _guids(table, row):
    """The 16-byte Guids a campaign row lists, in order."""
    _n, _t, items = table.struct_array(table.field(row, 'MissionList'))
    return [bytes(it[-24:-8]) for it in items]


def _yaml_text(value):
    """A double-quoted YAML scalar OrcsMustUI's reader accepts: it unescapes
    backslash, quote, newline and tab and nothing else."""
    text = str(value).replace('\\', '\\\\').replace('"', '\\"')
    return '"%s"' % text.replace('\r\n', '\n').replace('\n', '\\n').replace('\t', '\\t')


def _yaml_unquote(text):
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        out, i, body = [], 0, text[1:-1]
        while i < len(body):
            if body[i] == '\\' and i + 1 < len(body):
                out.append({'n': '\n', 't': '\t'}.get(body[i + 1], body[i + 1]))
                i += 2
            else:
                out.append(body[i])
                i += 1
        return ''.join(out)
    return text


def read_notes(path):
    """{'Maps': [{...}], 'Campaigns': [{...}]} from a notes file these tools wrote."""
    if not path or not os.path.exists(path):
        return parse_notes('')
    with open(path, encoding='utf-8', errors='replace') as fh:
        return parse_notes(fh.read())


def parse_notes(text):
    """{'Maps': [{...}], 'Campaigns': [{...}]} from notes text these tools wrote.

    Only the shape write_notes produces is understood: a top-level key, then list
    items of `Key: value` lines. Anything else is skipped, never guessed at."""
    out = {'Maps': [], 'Campaigns': []}
    section, item = None, None
    for line in text.splitlines(True):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        top = re.match(r'^(\w+):\s*$', line)
        if top:
            section = out.setdefault(top.group(1), [])
            item = None
            continue
        m = re.match(r'^\s*(-\s+)?(\w+):\s*(.*)$', line)
        if not m or section is None:
            continue
        if m.group(1) or item is None:
            item = {}
            section.append(item)
        item[m.group(2)] = _yaml_unquote(m.group(3))
    return out


#: The fields each notes entry may carry, in the order they are written.
NOTE_FIELDS = {'Maps': ('Mission', ('Author', 'Version', 'Description', 'Hub', 'GameType')),
               'Campaigns': ('Campaign', ('Banner', 'Card', 'CardHovered', 'Description',
                                          'Hub'))}


def notes_text(notes):
    """The notes file's text for {'Maps': [...], 'Campaigns': [...]} (parse_notes' shape),
    or None when no entry has a field. Empty fields are left out."""
    blocks = []
    for key, (ident, fields) in NOTE_FIELDS.items():
        lines = []
        for e in notes.get(key) or []:
            kept = [(f, e.get(f)) for f in fields if str(e.get(f) or '').strip()]
            if not kept:
                continue
            lines.append('  - %s: %s' % (ident, _yaml_text(e[ident])))
            lines += ['    %s: %s' % (f, _yaml_text(v)) for f, v in kept]
        if lines:
            blocks.append('%s:\n%s\n' % (key, '\n'.join(lines)))
    return ('# Written by the OMD3 authoring tools for OrcsMustUI. Safe to edit.\n'
            + '\n'.join(blocks)) if blocks else None


def write_notes(pak_path, maps=None, campaigns=None, images=None):
    """Write a build's notes, keeping any entry for another mission or campaign: inside
    a pak (makemod.OMUI_DIR, with `images` {name: bytes} beside them), or beside a loose
    build.

    `maps`: [{'Mission', 'Author', 'Version', 'Description', 'Hub', 'GameType'}];
    `campaigns`: [{'Campaign', 'Banner', 'Description', 'Hub'}]. Empty fields are left out, and a
    file left with nothing in it is removed. A map entry without a `GameType` key keeps
    the one the notes had. Returns where the notes are (a pak's `<pak>:<inner path>`,
    or the loose file), or None."""
    path = notes_path(pak_path)
    have = build_notes(pak_path)
    rows = {}
    for key, ident, fresh in (('Maps', 'Mission', maps or []),
                              ('Campaigns', 'Campaign', campaigns or [])):
        old = {e.get(ident): e for e in have.get(key, [])}
        fresh = [dict(e, GameType=old[e[ident]]['GameType'])
                 if key == 'Maps' and 'GameType' not in e
                 and (old.get(e[ident]) or {}).get('GameType') else e for e in fresh]
        mine = {e[ident] for e in fresh}
        rows[key] = [e for e in have.get(key, []) if e.get(ident) not in mine] + list(fresh)
    text = notes_text(rows)
    if not paksource.is_loose(pak_path) and os.path.isfile(pak_path):
        return _notes_into_pak(pak_path, text, images or {})
    if text is None:
        if os.path.exists(path):
            os.remove(path)
        return None
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(text)
    return path


def _notes_into_pak(pak_path, text, images):
    """Rewrite a pak with notes `text` (None: none) and `images` inside its mod folder,
    every other file kept; its old sidecars are taken in and removed (paksource.write)."""
    files = _entries(pak_path)
    key = makemod.pak_mod_key(files)
    if key is None:
        raise SystemExit('%s has no mod folder (/Game/Mods/<mod>/Tables) to hold its notes'
                         % os.path.basename(pak_path))
    #: the old loose notes are superseded by `text`; the rest (tab images) are kept
    for name, inner in makemod.omui_files(files).items():
        if name.startswith('CustomMaps.'):
            del files[inner]
    loose = notes_path(pak_path)
    if os.path.exists(loose):
        os.remove(loose)
    makemod.embed_loose_sidecars(pak_path, files, key)
    for name, blob in images.items():
        makemod.add_omui_file(files, key, name, blob)
    if text is not None:
        #: a banner or card beside the pak was `<stem>.Tab.png`; inside it is `Tab.png`
        for image in ('Tab.', 'Card.', 'CardHovered.'):
            text = text.replace('"%s.%s' % (paksource.stem(pak_path), image), '"' + image)
        makemod.add_omui_file(files, key, NOTES_NAME, text)
    paksource.write(pak_path, files, keep_omui=False)
    return '%s:%s' % (pak_path, makemod.omui_inner(key, NOTES_NAME)) if text else None


def installed(mods=None):
    """Every custom campaign row in ~mods, as
    [{key, pak, slot, row, title, missions, description, banner}].

    `key` is `<pak file>:<row>`, which is how a tool names one in a placement."""
    from riftlord.core import gamepath
    from riftlord.build.navigation import navpatch
    mods = mods or gamepath.target_dir()
    out = []
    for pak_path in paksource.sources(mods or ''):
        try:
            names = navpatch.pak_names(pak_path)
        except (Exception, SystemExit):              # noqa: BLE001
            continue
        tables = [(k, SLOT_TABLE.match(k)) for k in names]
        notes = build_notes(pak_path)
        about = {e.get('Campaign'): e for e in notes.get('Campaigns', [])}
        for key, m in tables:
            if not m or key[:-7] + '.uexp' not in names:
                continue
            try:
                t = _table(navpatch.read_from_pak(pak_path, names[key][1]),
                           navpatch.read_from_pak(pak_path, names[key[:-7] + '.uexp'][1]))
            except (Exception, SystemExit):          # noqa: BLE001
                continue
            for r in t.rows:
                if r['name'] == makemod.ENDLESS_EXTENSION:
                    continue                 # adds to Endless; not a campaign of its own
                info = about.get(r['name'], {})
                out.append({
                    'key': '%s:%s' % (os.path.basename(pak_path), r['name']),
                    'pak': pak_path, 'slot': int(m.group(1)) if m.group(1) else m.group(2),
                    'row': r['name'],
                    'title': _text(t, r, 'CampaignName') or r['name'],
                    'missions': [g.hex() for g in _guids(t, r)],
                    'description': info.get('Description', ''),
                    'banner': info.get('Banner', ''),
                    'hub': info.get('Hub', ''),
                    'own': paksource.stem(pak_path).endswith('_Campaign_P')})
    return out


def campaign_key(name):
    """A new campaign's mod key: its pak's name without the priority prefix and `_P`
    (OrcsMustUI Docs/Registry.md), so its table folder is its own."""
    return '%s_Campaign' % slug(name)


def _new_table(name, key, guids):
    """(path, row, (uasset, uexp)) for campaign mod `key`'s table holding one row,
    named the key itself."""
    path = makemod.campaign_slot_path(key)
    row = key
    c = missionproto.read(missionproto.CAMPAIGN_TABLE)
    new = c.clone(makemod.CAMPAIGN_TEMPLATE, row)
    c.set_text(c.field(new, 'CampaignName'), 'CampaignProtos', '%s_Name' % row, name)
    c.set_missions(new, guids)
    c.set_guid(c.field(new, 'Guid'))
    # Unlock chains, intros, cinematics and the tutorial all expect assets a mod
    # mission has not got; OrcsMustUI unlocks the first mission itself.
    for flag in ('bUseMissionUnlocks', 'bUseLevelIntros', 'bUseLevelCinematics',
                 'bUseNPE', 'bRequiresEntitlement'):
        f = c.field(new, flag)
        if f is not None:
            c.set_bool(f, False)
    c.rows = [r for r in c.rows if r['name'] == row]
    modtable.rename_package(c.pkg, missionproto.CAMPAIGN_TABLE, 'CampaignProtos',
                            path, path.rsplit('/', 1)[-1])
    c.export['data'] = c.serialise()
    return path, row, c.pkg.write()


def _with_list(ua, ux, row, guids):
    """The table with one row's MissionList replaced, every other byte kept."""
    t = _table(ua, ux)
    r = t.row(row)
    if r is None:
        raise SystemExit('no campaign row %s' % row)
    _n, tag, items = t.struct_array(t.field(r, 'MissionList'))
    shape = items[0] if items else None
    if shape is None:
        stock = missionproto.read(missionproto.CAMPAIGN_TABLE)
        srow = stock.row(makemod.CAMPAIGN_TEMPLATE)
        shape = stock.struct_array(stock.field(srow, 'MissionList'))[2][0]
    out = []
    for g in guids:
        e = bytearray(shape)
        e[-24:-8] = g
        out.append(bytes(e))
    t.field(r, 'MissionList').payload = t._pack_array(tag, out)
    return t.write()


def _check(before, after, table_key, row, want):
    """Refuse a rewrite that changed anything but one row's MissionList."""
    problems = []
    if set(before) != set(after):
        problems.append('the pak gained or lost files')
    for k in before:
        if k in after and before[k] != after[k] and not k.startswith(table_key[:-len('.uasset')]):
            problems.append('%s changed' % k)
    ua, ux = after[table_key], after[table_key[:-7] + '.uexp']
    t = _table(ua, ux)
    old = _table(before[table_key], before[table_key[:-7] + '.uexp']) \
        if table_key in before else None
    r = t.row(row)
    if r is None or _guids(t, r) != list(want):
        problems.append('%s does not list exactly the missions asked for' % row)
    if old is not None:
        for o in old.rows:
            n = t.row(o['name'])
            if n is None:
                problems.append('%s lost row %s' % (table_key, o['name']))
            elif o['name'] != row and [f.bytes() for f in o['fields']] != \
                    [f.bytes() for f in n['fields']]:
                problems.append('row %s changed' % o['name'])
    name = table_key[:-7].rsplit('/', 1)[-1]
    problems += pkgcheck.problems(ua, ux, name, 'table')
    if problems:
        raise SystemExit('campaign not written -- ' + '; '.join(problems[:3]))


def _rewrite(pak_path, table_key, row, guids):
    """Rewrite one campaign row's list in place, keeping a copy of the pak."""
    before = _entries(pak_path)
    inner_a = 'OMD/Content' + table_key[len('/Game'):]
    after = dict(before)
    after[inner_a], after[inner_a[:-7] + '.uexp'] = _with_list(
        before[inner_a], before[inner_a[:-7] + '.uexp'], row, guids)
    _check(before, after, inner_a, row, guids)
    paksource.backup(pak_path, '.before-campaign')
    paksource.write(pak_path, after)


#: The size a cropped campaign banner is written at: twice the hub tab's 384 x 120 at
#: 1920 x 1080 (OrcsMustUI Docs/Campaigns.md), so the tab shows all of it.
BANNER_SIZE = (768, 240)
#: Beside a loose campaign build: the picture its banner was cut from (`<stem><this><ext>`)
#: and `{name, crop, cardCrop}` (`<stem><this>.json`), so the crops can be changed later.
BANNER_SOURCE = '.BannerSource'
#: The notes' mode select card fields (OrcsMustUI CustomMaps.lua), each a 2048 x 2048 PNG
#: named `<field>.png` beside the notes.
CARD_FIELDS = ('Card', 'CardHovered')


def _modeart():
    """hudart/modeart, which draws the mode select cards."""
    hudart = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hudart')
    from riftlord.missions import modeart
    return modeart


def _crop_value(crop, what):
    """`crop` checked as [x, y, width, height], or None."""
    if crop is not None:
        if (not isinstance(crop, (list, tuple)) or len(crop) != 4
                or not all(isinstance(v, (int, float)) for v in crop)):
            raise SystemExit('a %s crop is [x, y, width, height]' % what)
    return crop


def _banner_parts(banner):
    """(extension, bytes, crop or None, card crop or None) of a banner: a path on disk, or
    {'name', 'data', 'crop', 'cardCrop'} with the bytes base64-encoded, which is how a
    browser hands over a file."""
    if isinstance(banner, dict):
        ext = os.path.splitext(banner.get('name') or '')[1].lower()
        blob = base64.b64decode(banner.get('data') or '')
        crop = banner.get('crop')
        card_crop = banner.get('cardCrop')
    else:
        ext = os.path.splitext(banner)[1].lower()
        with open(banner, 'rb') as fh:
            blob = fh.read()
        crop = card_crop = None
    if ext not in BANNER_TYPES:
        raise SystemExit('a campaign banner must be PNG or JPG, not %s' % (ext or 'that'))
    return ext, blob, _crop_value(crop, 'banner'), _crop_value(card_crop, 'button')


def _open_picture(blob):
    """Picture bytes as an RGB image the way it displays (EXIF orientation applied), so
    crops made in a browser land where they were drawn."""
    import io
    from PIL import Image, ImageOps
    with Image.open(io.BytesIO(blob)) as im:
        return ImageOps.exif_transpose(im).convert('RGB')


def card_crop(size, crop=None, card=None):
    """The mode select card's crop ([x, y, w, h] fractions) of a picture of `size`: `card`
    made the card window's shape, or with none the largest such box centred on the tab
    crop `crop` (on the picture when that is None too)."""
    centre = None if crop is None else (crop[0] + crop[2] / 2, crop[1] + crop[3] / 2)
    return _modeart().card_box(size[0], size[1], card, centre)


def crop_banner(blob, crop):
    """PNG bytes of picture `blob` cut to `crop` ([x, y, w, h], fractions of it) and
    scaled to the tab's shape, at most BANNER_SIZE."""
    import io
    from PIL import Image
    with _open_picture(blob) as im:
        x, y, w, h = (min(1.0, max(0.0, float(v))) for v in crop)
        box = (round(x * im.width), round(y * im.height),
               max(round(x * im.width) + 1, round(min(1.0, x + w) * im.width)),
               max(round(y * im.height) + 1, round(min(1.0, y + h) * im.height)))
        im = im.crop(box)
        tw, th = BANNER_SIZE
        width = min(tw, im.width)
        im = im.resize((width, max(1, round(width * th / tw))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format='PNG', optimize=True)
        return buf.getvalue()


def _drop_banner_files(pak_path):
    """Remove a loose build's banner, its mode select cards and its source picture."""
    for suffix in [s for ext in BANNER_TYPES for s in ('.Tab' + ext, BANNER_SOURCE + ext)] \
            + ['.%s.png' % c for c in CARD_FIELDS]:
        path = paksource.sidecar(pak_path, suffix)
        if os.path.exists(path):
            os.remove(path)
    path = paksource.sidecar(pak_path, BANNER_SOURCE + '.json')
    if os.path.exists(path):
        os.remove(path)


def campaign_cards(source, crop=None):
    """{'Card': png, 'CardHovered': png} -- the campaign's mode select card at rest and
    hovered (hudart/modeart.campaign_card: the Story banner's border and glow round the
    picture), cut to `crop` ([x, y, w, h] fractions; card_crop) from the FULL source
    picture at its own resolution, not the tab crop; {} when it cannot be made."""
    try:
        off, on = _modeart().campaign_card(source, crop)
    except Exception:                                # noqa: BLE001
        return {}
    return dict(zip(CARD_FIELDS, (off, on)))


def card_preview(size=512):
    """What the banner editor draws the card's preview with: {size (the card's pixels),
    window ([x, y, w, h] of the picture on the card), frame, mask (data URLs at `size`)}
    (hudart/modeart.card_frame), or None when the game's banner cannot be read."""
    try:
        modeart = _modeart()
        frame, mask = modeart.card_frame(size)
    except Exception:                                # noqa: BLE001
        return None
    url = lambda png: 'data:image/png;base64,' + base64.b64encode(png).decode()  # noqa: E731
    return {'size': modeart.CARD_SIZE, 'window': list(modeart.CARD_WINDOW),
            'frame': url(frame), 'mask': url(mask)}


def _save_banner(banner, pak_path, entry=None):
    """(the name the notes give an image, {name: bytes} for write_notes' `images`): for
    a pak the image goes inside it as `Tab.<ext>`; for a loose build it is written
    beside it as `<stem>.Tab.<ext>`, with the picture it was cropped from, and `images`
    is empty. `banner` is as _banner_parts takes it; one with a crop is written cropped
    to the tab's shape (crop_banner) as PNG. The mode select cards (campaign_cards, cut
    to the banner's cardCrop or card_crop's default) go beside it the same way, named in
    `entry`'s Card and CardHovered when given."""
    if not banner:
        return None, {}
    ext, blob, crop, card = _banner_parts(banner)
    source = (ext, blob)
    try:
        with _open_picture(blob) as im:
            card = card_crop(im.size, crop, card)
    except Exception:                                # noqa: BLE001
        card = None
    cards = campaign_cards(source[1], card)
    if entry is not None:
        for field in CARD_FIELDS:
            entry.pop(field, None)
    if crop is not None:
        ext, blob = '.png', crop_banner(blob, crop)
    if not paksource.is_loose(pak_path):
        images = {'Tab' + ext: blob}
        for field, png in cards.items():
            images[field + '.png'] = png
            if entry is not None:
                entry[field] = field + '.png'
        return 'Tab' + ext, images
    _drop_banner_files(pak_path)
    with open(paksource.sidecar(pak_path, '.Tab' + ext), 'wb') as fh:
        fh.write(blob)
    for field, png in cards.items():
        with open(paksource.sidecar(pak_path, '.%s.png' % field), 'wb') as fh:
            fh.write(png)
        if entry is not None:
            entry[field] = '%s.%s.png' % (paksource.stem(pak_path), field)
    if crop is not None:
        with open(paksource.sidecar(pak_path, BANNER_SOURCE + source[0]), 'wb') as fh:
            fh.write(source[1])
        name = banner.get('name') if isinstance(banner, dict) else os.path.basename(banner)
        import json
        with open(paksource.sidecar(pak_path, BANNER_SOURCE + '.json'), 'w',
                  encoding='utf-8') as fh:
            about = {'name': name, 'crop': [float(v) for v in crop]}
            if card is not None:
                about['cardCrop'] = [float(v) for v in card]
            json.dump(about, fh)
    return paksource.stem(pak_path) + '.Tab' + ext, {}


def index_problem(index, size, first=1):
    """'' when `index` is a place in a campaign list of `size` missions, from `first`
    (a shipped campaign keeps its own first mission first), else why not."""
    whole = _whole(index)
    if whole is None or not first <= whole <= size:
        if size <= first:
            return 'Campaign Index is %d.' % first
        return 'Campaign Index is a whole number from %d to %d.' % (first, size)
    return ''


def _whole(value):
    """An int, or a string of digits, as an int; anything else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    text = str(value if value is not None else '').strip()
    return int(text) if text.isdigit() else None


def campaign_index(index, size, first=1):
    """`index` as an int, checked by index_problem; SystemExit with its reason."""
    why = index_problem(index, size, first)
    if why:
        raise SystemExit(why)
    return _whole(index)


def _at(listed, guid, index):
    """`listed` with `guid` moved to place `index` (1-based) or, with None, kept where
    it is or added last."""
    rest = [g for g in listed if g != guid]
    if index is None:
        return listed if guid in listed else rest + [guid]
    return rest[:index - 1] + [guid] + rest[index - 1:]


def place(guid, choice, mods=None, note=lambda _s: None, index=None):
    """List one mission in exactly the campaign chosen, or in none.

    `guid` is the mission's 16-byte Guid. `choice` is None (a custom map), a key
    from installed(), or {'new': True, 'name', 'description', 'banner'}. `index` is
    its place in that campaign's MissionList, 1 to the list's length with it in (the
    others move along); None keeps the place it has, or adds it last. The Guid is
    taken out of every other custom campaign that lists it, so a mission moved
    between campaigns is not left showing in both. A campaign left with no missions
    has its pak renamed `.disabled` rather than deleted. Returns a report."""
    from riftlord.core import gamepath
    from riftlord.build.navigation import navpatch
    mods = mods or gamepath.target_dir()
    guid = bytes(guid[:16])
    report = {'campaign': None, 'removed_from': [], 'disabled': []}
    have = installed(mods)
    target = None
    index = None if index in (None, '') or choice is None else index
    if isinstance(choice, dict) and choice.get(NEW):
        name = (choice.get('name') or '').strip()
        if not name:
            raise SystemExit('a new campaign needs a name')
        existing = [c for c in have if c['own'] and slug(c['title']) == slug(name)]
        target = existing[0] if existing else None
        if index is not None:
            listed = [bytes.fromhex(g) for g in (target or {}).get('missions', [])]
            index = campaign_index(index, len([g for g in listed if g != guid]) + 1)
        if target is None:
            pak_path = pak_for(name, mods)
            slot = campaign_key(name)
            path, row, blobs = _new_table(name, slot, [guid])
            files = {_inner(path, '.uasset'): blobs[0], _inner(path, '.uexp'): blobs[1]}
            bad = pkgcheck.problems(blobs[0], blobs[1], path.rsplit('/', 1)[-1], 'table')
            #: the registry's rules: its own mod folder, its own Guid, against ~mods too
            bad += makemod.registry_problems(files, os.path.basename(pak_path), mods=mods)
            if bad:
                raise SystemExit('campaign not written -- %s' % bad[0])
            # OrcsMustUI registers the table from its folder; nothing goes beside the pak
            paksource.write(pak_path, files)
            note('created campaign %s in %s (tables /Game/Mods/%s/Tables)'
                 % (name, os.path.basename(pak_path), slot))
            target = {'key': '%s:%s' % (os.path.basename(pak_path), row), 'pak': pak_path,
                      'slot': slot, 'row': row, 'title': name, 'missions': [guid.hex()]}
            have.append(target)
        _campaign_notes(target, choice)
    elif choice:
        target = next((c for c in have if same_key(c['key'], choice)), None)
        #: a campaign left with no missions was renamed .disabled: listing one revives it
        if target is None and _revive(mods, choice, note):
            have = installed(mods)
            target = next((c for c in have if same_key(c['key'], choice)), None)
        if target is None:
            raise SystemExit('campaign %s is not installed' % choice)
        if index is not None:
            listed = [bytes.fromhex(g) for g in target['missions']]
            index = campaign_index(index, len([g for g in listed if g != guid]) + 1)

    for c in have:
        listed = [bytes.fromhex(g) for g in c['missions']]
        if c is target or (target and c['key'] == target['key']):
            want = _at(listed, guid, index)
            if want == listed:
                continue
        elif guid in listed:
            want = [g for g in listed if g != guid]
        else:
            continue
        key = makemod.campaign_slot_path(c['slot']) + '.uasset'
        if not want:
            gone = c['pak'] + '.disabled'
            #: a pak the running game may have mounted is renamed once it has closed
            from riftlord.core import pakguard
            pakguard.rename(c['pak'], gone)
            # its notes would otherwise name a campaign the game no longer has
            if os.path.exists(notes_path(c['pak'])):
                os.replace(notes_path(c['pak']), notes_path(c['pak']) + '.disabled')
            report['disabled'].append(os.path.basename(gone))
            note('%s has no missions left, so its pak was renamed %s'
                 % (c['title'], os.path.basename(gone)))
            continue
        navpatch.pak_names(c['pak'])               # refuse a pak that does not read
        _rewrite(c['pak'], key, c['row'], want)
        if c is target or (target and c['key'] == target['key']):
            c['missions'] = target['missions'] = [g.hex() for g in want]
            note('listed in %s at %d of %d' % (c['title'], want.index(guid) + 1, len(want)))
        else:
            report['removed_from'].append(c['title'])
            note('taken out of %s' % c['title'])
    if target is not None:
        report['campaign'] = target['title']
        report['campaign_pak'] = target['pak']
        report['campaign_row'] = target['row']
        listed = [bytes.fromhex(g) for g in target['missions']]
        report['campaign_index'] = listed.index(guid) + 1 if guid in listed else None
    return report


def _revive(mods, key, note=lambda _s: None):
    """Rename campaign `key`'s pak (`<pak file>:<row>`) back from `.disabled`, as place()
    left it when its last mission moved out, with its notes. True when one was."""
    from riftlord.core import pakguard
    pak = str(key or '').rpartition(':')[0]
    stem = pak[:-4] if pak.endswith('.pak') else pak
    for name in (stem + '.pak', stem):
        path = os.path.join(mods, name)
        if os.path.exists(path + '.disabled') and not os.path.exists(path):
            pakguard.rename(path + '.disabled', path)
            if os.path.exists(notes_path(path) + '.disabled'):
                os.replace(notes_path(path) + '.disabled', notes_path(path))
            note('%s is listed in again, so its pak is back from .disabled' % name)
            return True
    return False


def move(key, guid, index, mods=None, note=lambda _s: None):
    """Give a mission an installed campaign already lists place `index` (1 to the list's
    length) in its MissionList; the missions between move along by one. Only that row's
    list is rewritten (_rewrite, _check). Returns {campaign, index, missions}."""
    guid = bytes(guid[:16])
    target = next((c for c in installed(mods) if same_key(c['key'], key)), None)
    if target is None:
        raise SystemExit('campaign %s is not installed' % key)
    listed = [bytes.fromhex(g) for g in target['missions']]
    if guid not in listed:
        raise SystemExit('%s does not list that mission' % target['title'])
    index = campaign_index(index, len(listed))
    want = _at(listed, guid, index)
    if want != listed:
        from riftlord.build.navigation import navpatch
        navpatch.pak_names(target['pak'])          # refuse a pak that does not read
        _rewrite(target['pak'], makemod.campaign_slot_path(target['slot']) + '.uasset',
                 target['row'], want)
        note('%s: moved to %d of %d' % (target['title'], index, len(want)))
    return {'campaign': target['title'], 'index': index, 'missions': [g.hex() for g in want]}


def same_key(a, b):
    """Whether two campaign keys (`<pak file>:<row>`) name the same campaign, with or
    without the pak's `.pak` extension (records saved both spellings)."""
    def norm(k):
        pak, _, row = str(k or '').rpartition(':')
        return (pak[:-4] if pak.endswith('.pak') else pak, row)
    return norm(a) == norm(b)


def _campaign_notes(target, choice):
    """A new campaign's description, hub style and banner, in its notes."""
    entry = {'Campaign': target['row'],
             'Description': (choice.get('description') or '').strip(),
             'Hub': hub_name(choice.get('hub'))}
    banner, images = _save_banner(choice.get('banner'), target['pak'], entry)
    if banner:
        entry['Banner'] = banner
    else:
        old = build_notes(target['pak'])
        prev = next((e for e in old.get('Campaigns', [])
                     if e.get('Campaign') == target['row']), {})
        for field in ('Banner',) + CARD_FIELDS:
            if prev.get(field):
                entry[field] = prev[field]
    write_notes(target['pak'], campaigns=[entry], images=images)


def _rename(target, name):
    """Give an installed campaign row a new CampaignName (the tab's title), every other
    byte of the pak kept; a copy of the pak is kept first."""
    before = _entries(target['pak'])
    inner = 'OMD/Content' + makemod.campaign_slot_path(target['slot'])[len('/Game'):] + '.uasset'
    ux_key = inner[:-7] + '.uexp'
    t = _table(before[inner], before[ux_key])
    r = t.row(target['row'])
    if r is None or t.field(r, 'CampaignName') is None:
        raise SystemExit('no campaign row %s' % target['row'])
    t.set_text(t.field(r, 'CampaignName'), 'CampaignProtos', '%s_Name' % target['row'], name)
    after = dict(before)
    after[inner], after[ux_key] = t.write()
    problems = [k for k in before if k not in (inner, ux_key) and before[k] != after.get(k)]
    new = _table(after[inner], after[ux_key])
    old = _table(before[inner], before[ux_key])
    for o in old.rows:
        n = new.row(o['name'])
        keep = [f.bytes() for f in o['fields'] if not (o['name'] == target['row']
                                                       and f.name == 'CampaignName')]
        got = [f.bytes() for f in (n or {}).get('fields', [])
               if not (o['name'] == target['row'] and f.name == 'CampaignName')]
        if n is None or keep != got:
            problems.append('row %s changed' % o['name'])
    if _text(new, new.row(target['row']), 'CampaignName') != name:
        problems.append('the name did not read back')
    problems += pkgcheck.problems(after[inner], after[ux_key],
                                  inner[:-7].rsplit('/', 1)[-1], 'table')
    if problems:
        raise SystemExit('campaign not renamed -- ' + '; '.join(problems[:3]))
    paksource.backup(target['pak'], '.before-campaign')
    paksource.write(target['pak'], after)


def edit(key, mods=None, name=None, description=None, hub=None, banner=None):
    """Change a custom campaign these tools made (a key from installed()): its name (the
    tab's title), description, hub style ('' for none) and banner (as _save_banner takes
    it; False removes it). None leaves a field as it is. Returns the campaign as
    choices() lists it."""
    target = next((c for c in installed(mods) if same_key(c['key'], key)), None)
    if target is None:
        raise SystemExit('campaign %s is not installed' % key)
    if not target.get('own'):
        raise SystemExit('%s was made by another mod; change it there' % target['title'])
    if name is not None:
        name = str(name).strip()
        if not name:
            raise SystemExit('a campaign needs a name')
        if name != target['title']:
            _rename(target, name)
    old = build_notes(target['pak'])
    entry = dict(next((e for e in old.get('Campaigns', [])
                       if e.get('Campaign') == target['row']), {}),
                 Campaign=target['row'])
    if description is not None:
        entry['Description'] = str(description).strip()
    if hub is not None:
        entry['Hub'] = hub_name(hub)
    images = {}
    if banner is False:
        for field in ('Banner',) + CARD_FIELDS:
            entry.pop(field, None)
        if paksource.is_loose(target['pak']):
            _drop_banner_files(target['pak'])
    elif banner:
        entry['Banner'], images = _save_banner(banner, target['pak'], entry)
    write_notes(target['pak'], campaigns=[entry], images=images)
    return next(c for c in choices(shipped=False, mods=mods)
                if c['name'].startswith(CUSTOM) and same_key(c['name'][len(CUSTOM):], key))


#: Widest banner preview sent to the dialogs, in pixels (the tab strip is 384).
BANNER_PREVIEW_WIDTH = 384
#: Widest source picture sent to the dialogs for re-cropping, in pixels.
BANNER_SOURCE_WIDTH = 1024


def _data_url(blob, width):
    """Picture bytes as a JPEG data URL at most `width` pixels wide."""
    import io
    from PIL import Image
    with _open_picture(blob) as im:
        if im.width > width:
            im = im.resize((width, max(1, round(im.height * width / im.width))))
        buf = io.BytesIO()
        im.save(buf, format='JPEG', quality=85)
    return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()


def _banner_bytes(campaign):
    """An installed campaign's banner file as bytes, or None."""
    name = campaign.get('banner')
    if not name:
        return None
    pak = campaign['pak']
    if paksource.is_loose(pak):
        path = os.path.join(os.path.dirname(pak.rstrip('/\\')), name)
        if not os.path.isfile(path):
            return None
        with open(path, 'rb') as fh:
            return fh.read()
    inner = makemod.omui_files(paksource.names(pak)).get(name)
    return paksource.read(pak, inner) if inner else None


def banner_view(campaign):
    """An installed campaign's banner as a JPEG data URL, or None."""
    try:
        blob = _banner_bytes(campaign)
        return _data_url(blob, BANNER_PREVIEW_WIDTH) if blob else None
    except Exception:                                # noqa: BLE001
        return None


def _source_view(name, blob, crop, card):
    """The banner editor's {name, crop, cardCrop, size (the picture's own pixels),
    preview (data URL)} for picture `blob`."""
    with _open_picture(blob) as im:
        size = list(im.size)
    try:
        card = card_crop(size, crop, card)
    except Exception:                                # noqa: BLE001
        card = None
    return {'name': name, 'crop': crop, 'cardCrop': card, 'size': size,
            'preview': _data_url(blob, BANNER_SOURCE_WIDTH)}


def banner_source(campaign):
    """What the banner editor starts from (_source_view): the picture the banner was
    cropped from, or the banner itself, whole -- or None."""
    try:
        pak = campaign['pak']
        if paksource.is_loose(pak):
            import json
            meta = paksource.sidecar(pak, BANNER_SOURCE + '.json')
            pictures = [paksource.sidecar(pak, BANNER_SOURCE + e) for e in BANNER_TYPES]
            picture = next((p for p in pictures if os.path.isfile(p)), None)
            if picture and os.path.isfile(meta) and campaign.get('banner'):
                with open(meta, encoding='utf-8') as fh:
                    about = json.load(fh)
                with open(picture, 'rb') as fh:
                    return _source_view(about.get('name') or os.path.basename(picture),
                                        fh.read(), about.get('crop'), about.get('cardCrop'))
        blob = _banner_bytes(campaign)
        if not blob:
            return None
        return _source_view(campaign['banner'], blob, [0.0, 0.0, 1.0, 1.0], None)
    except Exception:                                # noqa: BLE001
        return None


#: Shipped campaigns an author may list a mission in. Weekly Challenges and the
#: developer campaign are absent: one is a rotating list the game curates, the
#: other is not shipped content. Listing in any of these patches the shipped
#: CampaignProtos, which every other such mod also ships.
SHIPPED = ('OMD3_Main_Story', 'OMD3_Endless', 'DLC3_Main_Story',
           'DLC_Ice_Campaign', 'DLC_Sand_Campaign')
#: Shipped campaigns offered in the editors' lists. Endless is not among them: "Add to
#: Endless" puts any mission there through OrcsMustUI (makemod.ENDLESS_EXTENSION)
#: without patching the shipped table; listing in it directly stays a command-line
#: option for mods that want the old behaviour.
OFFERED = tuple(c for c in SHIPPED if c != 'OMD3_Endless')
#: A placement naming an installed custom campaign: this prefix, then its key.
CUSTOM = 'custom:'


def shipped_names():
    """Shipped campaign row -> the name the game shows."""
    c = missionproto.read(missionproto.CAMPAIGN_TABLE)
    return {r['name']: _text(c, r, 'CampaignName') or r['name'] for r in c.rows}


#: The first Campaign Index a mission may take in a shipped campaign: the game unlocks a
#: campaign's first mission without a save entry (makemod.build, the unlock), so the
#: shipped first mission stays first.
SHIPPED_FIRST = 2


def choices(shipped=True, mods=None, guid=None, shipped_index=None):
    """[{name, label, kind, ...}] -- where a tool may put a mission, in the order
    a picker shows them. `name` is what the tool sends back as its placement.

    Each campaign also gives its Campaign Index range for the mission with 16-byte Guid
    `guid`: `size` (its list's length with the mission in), `first` (the lowest index it
    may take) and `index` (where it is now, or None). `shipped_index` is the index a
    record keeps for its shipped campaign, {row: index}."""
    guid = bytes(guid[:16]).hex() if guid else None
    out = [{'name': makemod.CUSTOM_MAPS, 'label': 'Custom Maps', 'kind': 'none',
            'missions': [], 'size': 0, 'first': 1, 'index': None},
           {'name': ENDLESS, 'label': 'Endless', 'kind': 'endless', 'missions': [],
            'size': 0, 'first': 1, 'index': None},
           {'name': NEW, 'label': 'New Campaign', 'kind': 'new', 'missions': [],
            'size': 1, 'first': 1, 'index': None}]
    for c in installed(mods):
        listed = guid is not None and guid in c['missions']
        out.append({'name': CUSTOM + c['key'], 'label': c['title'], 'kind': 'custom',
                    'missions': c['missions'], 'description': c['description'],
                    'banner': c['banner'], 'bannerView': banner_view(c), 'hub': c['hub'],
                    'bannerSource': banner_source(c) if c.get('own') else None,
                    'own': bool(c.get('own')), 'pak': os.path.basename(c['pak']),
                    'size': len(c['missions']) + (0 if listed else 1), 'first': 1,
                    'index': c['missions'].index(guid) + 1 if listed else None})
    if shipped:
        names = shipped_names()
        for row in OFFERED:
            order = makemod.campaign_order(row)
            if order:
                out.append({'name': row, 'label': names.get(row, row),
                            'kind': 'shipped', 'missions': order,
                            'size': len(order) + 1, 'first': SHIPPED_FIRST,
                            'index': (shipped_index or {}).get(row)})
    return out


def resolve(placement, details=None):
    """(shipped campaign row or None, a choice for place()) for a tool's placement."""
    placement = placement or makemod.CUSTOM_MAPS
    #: Endless lists the mission through its own extension row, in no campaign
    if placement in (makemod.CUSTOM_MAPS, ENDLESS):
        return None, None
    if placement == NEW:
        fresh = dict((details or {}).get('newCampaign') or {})
        fresh[NEW] = True
        return None, fresh
    if placement.startswith(CUSTOM):
        return None, placement[len(CUSTOM):]
    if placement not in SHIPPED:
        raise SystemExit('%s is not a campaign a mission can be added to' % placement)
    return placement, None


def finish(pak_path, report, placement, details=None, note=lambda _s: None):
    """After a mission pak is written: list it in its custom campaign (or in none)
    and write its notes (inside a pak; beside a loose build). Returns what was done, for the report.

    `details` is {author, version, description, hub, campaignIndex, newCampaign: {name,
    description, banner, hub}}; every field is optional, and newCampaign is read only
    when the placement is a new campaign (an installed one is changed with edit()).
    campaignIndex is the mission's place in a custom campaign (place `index`)."""
    details = details or {}
    _into, choice = resolve(placement, details)
    #: a mission placed in Endless is listed there alone, in no campaign
    if report.get('endless_only'):
        choice = None
    done = place(bytes.fromhex(report['mission_guid']), choice, note=note,
                 index=details.get('campaignIndex') if choice is not None else None)
    # what to send next time: a campaign this build created is an installed one now
    done['placement'] = (CUSTOM + '%s:%s' % (os.path.basename(done['campaign_pak']),
                                             done['campaign_row'])
                         if done.get('campaign_pak') else placement or makemod.CUSTOM_MAPS)
    entry = {'Mission': report['mission_row']}
    for key in ('Author', 'Version', 'Description'):
        entry[key] = str(details.get(key.lower()) or '').strip()
    entry['Hub'] = hub_name(details.get('hub'))
    #: OrcsMustUI game type (Docs/GameTypes.md): CrystalDefense with a Crystal placed
    entry['GameType'] = str(report.get('game_type') or '')
    maps = [entry]
    #: the Endless twin row plays the same level, so it gets the same game type
    if report.get('endless_row'):
        maps.append({'Mission': report['endless_row'], 'GameType': entry['GameType']})
    done['notes'] = write_notes(pak_path, maps=maps)
    for row in maps:
        if row['GameType'] and read_game_type(pak_path, row['Mission']) != row['GameType']:
            raise SystemExit('the notes of %s do not give %s GameType %s'
                             % (os.path.basename(pak_path), row['Mission'], row['GameType']))
    if done['notes']:
        note('notes in %s' % os.path.basename(done['notes'].split(':OMD/', 1)[0])
             if ':OMD/' in done['notes'] else 'notes in %s' % os.path.basename(done['notes']))
    return done


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--list', action='store_true', help='list installed custom campaigns')
    args = ap.parse_args()
    if args.list:
        for c in installed():
            where = ('Slot%03d (retired)' % c['slot']) if isinstance(c['slot'], int) else c['slot']
            print('%-40s %-28s %-28s %d mission(s)'
                  % (c['key'], where, c['title'], len(c['missions'])))


if __name__ == '__main__':
    main()
