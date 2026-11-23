"""Offline checks on Rift Lord's Deathtrap writer (riftlord/formats/omdd: zenpackage,
unversioned_write, iostore_write): hashes, headers and a container written and read back
without the game; with Deathtrap installed, shipped packages round-trip byte for byte and the
S2 probe built from them matches retoc's chunks (measured hashes below).

    python3 test_omdd_write.py
"""
import hashlib
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from riftlord.formats.omdd import iostore_write, trace_omdd, unversioned_write, zenpackage  # noqa: E402

SCRATCH_PARENT = '/mnt/2tb_pro_a/tmp' if os.path.isdir('/mnt/2tb_pro_a/tmp') else None
#: Package chunk hashes (BLAKE3, first 20 bytes) of retoc's S2 probe (s1/make_s2.py, retoc 0.1.5).
RETOC_S2 = {'/Game/Challenges/DesignerCreated/DA_DCR_EnduranceCrunch':
            '432d38b57ebff9b8587102b252b27f6fc05b10c6',
            '/Game/Challenges/Missions/MD_Castle_Crunch_RiftLordProbeTest1':
            '186f052a3f26da5ae666c7ae2f818454df57b1d6'}
#: SHA-1 of retoc's empty companion .pak.
RETOC_PAK_SHA1 = '4a7a8d8aa87652a38eb04358368647dcb4c4a9ce'
#: Shipped packages round-tripped: a challenge, a mission, a level, a skin material.
SHIPPED = ('/Game/Challenges/DesignerCreated/DA_DCR_EnduranceCrunch',
           '/Game/Challenges/Missions/MD_Castle_Crunch_ChallengeEndurance',
           '/Game/Levels/BaseGame/Castle/Crunch/Level_Castle_Crunch',
           '/Game/UltraDynamicSky/Blueprints/Weather_Effects/Weather_Mask_Brush')

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


def offline(scratch):
    """Checks that need no game."""
    z, w, uw = zenpackage, iostore_write, unversioned_write
    check('container id of pakchunk280-Windows is CityHash64 of its name',
          z.container_id('pakchunk280-Windows') == 0x3321b4aae9e97a78)
    check('package id of a shipped challenge', z.package_id(
        '/Game/Challenges/DesignerCreated/DA_DCR_EnduranceCrunch') == 0x8988a0f4bb928de9)
    check('public export hash of a shipped mission',
          z.export_hash('MD_Castle_Crunch_ChallengeEndurance') == 0x8c8be84de9c02987)
    long = ('/Content_Hero_Skins/Characters/Heroes/Hero_Daggers/Skins/Sagefoot/Fragment/Mat/'
            'Daggers_Skeleton_Clothes_INST')
    check('the name hash of a shipped 109-byte name (CityHash64 over 64 bytes)',
          z.name_hash(long) == 0xa79819dd67bf1e5f)
    names = ['None', 'Weather', '\U0001F4D8 Weather Mask Tools', 'Rocket_04']
    got, _ = trace_omdd.name_batch(z.write_name_batch(names), 0)
    check('a name batch reads back, UTF-16 surrogate pairs included', got == names, got)
    check('names split as UE splits them', [z.split_name(n) for n in ('Rocket_04', 'Foo_3', 'Bar')]
          == [('Rocket_04', 0), ('Foo', 4), ('Bar', 0)])
    check('BLAKE3 (Python) of nothing',
          w._blake3_py(b'').hex().startswith('af1349b9f5f9a1a6a0404dea36dcc949'))
    data = bytes(range(256)) * 300
    check('BLAKE3 over several chunks: library and Python agree',
          w.blake3(data) == w._blake3_py(data))
    check('an empty struct header skips its whole schema', uw.header([], 38) == bytes.fromhex('2601'))
    check('a header with a zero-masked value', uw.header([(0, False), (1, True), (5, False)], 10)
          == bytes.fromhex('8004030302'))
    check('a skip over 127 takes two fragments',
          uw.header([(200, False)], 300) == bytes.fromhex('7f004903'))
    ids = [w.chunk_id(z.package_id('/Game/Test/P%d' % k), 1) for k in range(500)]
    slots, seeds = w.perfect_hash(ids)
    toc = [ids[i] for i in slots]
    check('the perfect hash finds all of 500 chunks',
          all(w.lookup(toc, seeds, c) == k for k, c in enumerate(toc)))
    check('the empty .pak is byte for byte retoc\'s',
          hashlib.sha1(w.empty_pak()).hexdigest() == RETOC_PAK_SHA1)
    # a small container written and read back with the reader
    big = bytes(range(256)) * 1024                               # compresses, 4 blocks
    a, bc = z.package_id('/Game/A'), z.package_id('/Game/B/C')
    chunks = [w.Chunk(w.chunk_id(a, 1), big, 'OMDD/Content/A.uasset'),
              w.Chunk(w.chunk_id(bc, 1), b'small', 'OMDD/Content/B/C.uasset'),
              w.Chunk(w.chunk_id(a, 2), os.urandom(70000), 'OMDD/Content/A.ubulk')]
    header = w.ContainerHeader(z.container_id('TestP1_P'), {a: ([bc], []), bc: ([], [])})
    utoc = os.path.join(scratch, 'TestP1_P.utoc')
    w.write_container(utoc, chunks, header)
    c = trace_omdd.Container(utoc)
    check('the reader lists every file of our container',
          sorted(c.files) == sorted('../../../' + x.path for x in chunks), sorted(c.files))
    check('every chunk reads back',
          all(c.read(c.files['../../../' + x.path]) == x.data for x in chunks))
    hi = next(i for i, x in enumerate(c.ids) if x[11] == w.CHUNK_HEADER)
    back = w.ContainerHeader.parse(c.read(hi))
    check('the container header reads back',
          back.packages == header.packages and back.to_bytes() == header.to_bytes())
    check('the .pak is written beside the container',
          os.path.getsize(utoc[:-5] + '.pak') == len(w.empty_pak()))
    seeds = int.from_bytes(open(utoc, 'rb').read()[84:88], 'little')
    ids = [x.id for x in chunks] + [w.chunk_id(header.container_id, w.CHUNK_HEADER)]
    check('four chunks two of which share a slot under every seed: no perfect hash, read anyway',
          w.perfect_hash(ids) is None and seeds == 0, seeds)


def with_game(scratch):
    """Checks on the shipped game."""
    z, w, uw = zenpackage, iostore_write, unversioned_write
    g = trace_omdd.game()
    parsed = same = 0
    for name in SHIPPED:
        c, i = g.files[name.lower()]
        raw = c.read(i)
        pkg = z.ZenPackage(raw)
        check('%s round-trips byte for byte' % name.rsplit('/', 1)[1], pkg.to_bytes() == raw)
        view = g.package(name)
        writer = uw.Writer(g.types)
        for k, e in enumerate(view.exports):
            try:
                fields, end = view.props_end(k, raw=True)
            except (trace_omdd.PropError, IndexError, KeyError, ValueError):
                continue
            parsed += 1
            same += writer.struct(view.class_name(k), fields) == view.data[e['at']:end]
    check('every export whose properties read re-encodes identically (%d)' % parsed,
          parsed and same == parsed, '%d of %d' % (same, parsed))
    # imports and exports added, resolved by the reader, removed again
    c, i = g.files['/game/challenges/missions/md_castle_crunch_challengeendurance']
    raw = c.read(i)
    md = z.ZenPackage(raw)
    cls = md.add_script_import('/Script/OMD4.RSTMissionDefinition')
    ref = md.add_export(md.export_name(md.exports[0]), md.exports[0].data, cls=cls)
    view = trace_omdd.Package(g, md.name, None, None, md.to_bytes())
    check('an added export reads with its class', view.class_name(ref - 1) == 'RSTMissionDefinition'
          and sorted(view.props(ref - 1)) == sorted(view.props(0)))
    md.remove_export(ref)
    check('adding then removing an export gives the shipped bytes back', md.to_bytes() == raw)
    mesh = md.add_script_import('/Script/Engine.StaticMesh')
    imp = md.add_import('/Game/Challenges/DesignerCreated/DA_DCR_EnduranceCrunch', 'DA_DCR_EnduranceCrunch')
    view = trace_omdd.Package(g, md.name, None, None, md.to_bytes())
    hit = view.import_target(-imp - 1)
    check('added imports resolve: a native class and another package\'s export',
          view.import_target(-mesh - 1) == ('script', 'StaticMesh')
          and hit and hit[0].exports[hit[1]]['name'] == 'DA_DCR_EnduranceCrunch')
    # the S2 probe from the shipped packages
    md_old = '/Game/Challenges/Missions/MD_Castle_Crunch_ChallengeEndurance'
    md_new = '/Game/Challenges/Missions/MD_Castle_Crunch_RiftLordProbeTest1'
    c, i = g.files['/game/challenges/designercreated/da_dcr_endurancecrunch']
    da = z.ZenPackage(c.read(i))
    fields, cls, rest = uw.read_properties(g, da, 0)
    title = fields['ChallengeTitle']
    uw.set_field(g.types, fields, 'ChallengeTitle',
                 trace_omdd.FText('Rift Lord S2!', title.flags, 0, title.parts[:2] + ('Rift Lord S2!',)))
    uw.write_properties(g, da, 0, fields, cls, rest)
    da.rename_name(md_old, md_new)
    da.rename_name(md_old.rsplit('/', 1)[1], md_new.rsplit('/', 1)[1])
    c, i = g.files[md_old.lower()]
    md = z.ZenPackage(c.read(i))
    check('moving a package gives the package id of its new name',
          md.move(md_new) == z.package_id(md_new) and md.name == md_new)
    pkgs = (da, md)
    chunks = [w.Chunk(w.chunk_id(z.package_id(p.name), w.CHUNK_PACKAGE), p.to_bytes(),
                      'OMDD/Content/%s.uasset' % p.name[len('/Game/'):]) for p in pkgs]
    header = w.ContainerHeader(z.container_id('RiftLordS2_P'),
                               {z.package_id(p.name): w.store_entry(p) for p in pkgs})
    utoc = os.path.join(scratch, 'RiftLordS2_P.utoc')
    entries = w.write_container(utoc, chunks, header)
    got = {cid: h.hex() for cid, _o, _n, _f, h in entries}
    for name, want in RETOC_S2.items():
        cid = w.chunk_id(z.package_id(name), w.CHUNK_PACKAGE)
        check('S2 probe built without retoc: %s has retoc\'s chunk hash' % name.rsplit('/', 1)[1],
              got.get(cid) == want, got.get(cid))
    c = trace_omdd.Container(utoc)
    path = '../../../OMDD/Content/Challenges/DesignerCreated/DA_DCR_EnduranceCrunch.uasset'
    view = trace_omdd.Package(g, da.name, None, None, c.read(c.files[path]))
    check('the retitled challenge reads "Rift Lord S2!"',
          view.props(0).get('ChallengeTitle') == 'Rift Lord S2!')


def main():
    scratch = tempfile.mkdtemp(prefix='omdd-write-test-', dir=SCRATCH_PARENT)
    try:
        offline(scratch)
        why = trace_omdd.missing()
        if why:
            print('skipped the shipped-game checks: %s' % why)
        else:
            with_game(scratch)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    bad = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        print('%s %s%s' % ('ok  ' if ok else 'FAIL', name, '' if ok else ': %s' % (detail,)))
    print('%d of %d checks passed' % (len(checks) - len(bad), len(checks)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
