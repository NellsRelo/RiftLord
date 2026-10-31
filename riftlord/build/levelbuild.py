"""Author a cooked level package: an empty one, or one with actors lifted into it.

A converted OMD2 level needs new geometry, and geometry lives in a level's
`_Castle` sublevel. `makelevel.py` copies a persistent level and leaves the
geometry alone; this is the other half -- building the geometry package itself.

Two facts make it tractable, both measured (see
`Navigation-and-Geometry-Research.md`):

* **A loadable level is five exports.** The smallest one the game ships is
  2,931 bytes: a `World`, a `Level` named `PersistentLevel`, an empty `Model`,
  `OMDWorldSettings`, and a `NavigationSystemModuleConfig` under it. That is the
  skeleton everything else is appended to.
* **`uassetio.Package` round-trips level packages byte for byte**, including a
  2.7 MB castle level, so the container end is already solved.

The approach for actors is the one `missionproto.adopt` uses for table rows:
**lift them from a level that already has them** rather than synthesising the
bytes. An actor's own serialisation is trivial -- two object references and a
None -- but its `StaticMeshComponent` carries a 26-byte tail with a GUID in it,
and inventing that is a good way to produce something that parses offline and
crashes in game. Copying keeps whatever the cooker wrote, and only the FName
indices and package indices have to be rewritten.

    python3 levelbuild.py --empty MyLevel --out .
    python3 levelbuild.py --demo --out .        # skeleton + a lifted floor

Everything written here goes through `pkgcheck` before it is returned, so a
package that does not read back the way the engine reads it never reaches a pak.
"""

import argparse
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.core import gamefiles  # noqa: E402
from riftlord.core import pkgcheck  # noqa: E402
from riftlord.formats.ue.uassetio import Package  # noqa: E402

#: The smallest complete level the game ships, used as the skeleton.
SKELETON = '../../../OMD/Content/Maps/CampaignDLC/ScenarioDLC_03//ScenarioDLC_03_VFX'

#: Where an export's ObjectName FName sits inside its 104-byte meta.
NAME_AT = 16
CLASS_AT = 0
OUTER_AT = 12


def _name_of(pkg, index):
    """The string an export's ObjectName resolves to.

    A NEGATIVE index is refused rather than indexed. Python would wrap it onto the last
    export, and a shipped level's actor list legitimately holds null entries -- the
    skeleton's is `[4, 0]` -- so printing one as `_name_of(0 - 1)` named whatever had
    been lifted last and read as a duplicated actor that was never there.
    """
    if not 0 <= index < len(pkg.exports):
        return '<none>'
    i = struct.unpack_from('<i', pkg.exports[index]['meta'], NAME_AT)[0]
    return pkg.names[i][0] if 0 <= i < len(pkg.names) else '?'


def _import_name(pkg, i):
    return pkg.names[struct.unpack_from('<I', pkg.imports[i], 20)[0]][0]


def resolve(pkg, index):
    """What an FPackageIndex points at: (kind, name)."""
    if index > 0:
        return 'export', _name_of(pkg, index - 1)
    if index < 0:
        return 'import', _import_name(pkg, -index - 1)
    return 'null', None


class Builder:
    """A level package under construction."""

    def __init__(self, name):
        self._missed = []
        #: (our package index, donor export name) of every preload dependency a lift
        #: dropped because its target is not in this package.
        self.preload_dropped = []
        ua, ux = gamefiles.package(SKELETON)
        self.pkg = Package(ua, ux)
        self.pkg.load_payloads()
        self.name = name
        self._rename_world(name)

    @classmethod
    def around(cls, uasset, uexp, name='level'):
        """Wrap a level that already exists, to add actors to it.

        The simplest way to give a custom mission its own geometry is to put the
        geometry in the mission's own persistent level -- which `makemod` has
        already copied and renamed. No new package, no sublevel to stream, one
        file to get right instead of two.
        """
        b = cls.__new__(cls)
        b._missed = []
        b.preload_dropped = []
        b.pkg = Package(uasset, uexp)
        b.pkg.load_payloads()
        b.name = name
        return b

    def stream_sublevels(self, paths, donor=None):
        """Make this level stream `paths`, authoring its own streaming entries.

        A persistent level references its sublevels through one
        `LevelStreamingAlwaysLoaded` export each, plus a list in the World export's
        **native** tail -- there is no tagged `StreamingLevels` property, which is why
        a property walk over the World finds only a terminator in 48 bytes.

        The tail, measured on Scenario_01 (persistent 136, 6 sublevels) and Scenario_16
        (331, 5), and identical on both:

            +0   the None property terminator, 8 bytes
            +8   int32 0
            +12  int32 the PersistentLevel's package index
            +16  int32 0
            +20  int32 how many streaming levels
            +24  int32 x count, each streaming export's package index

        A streaming export itself carries just two properties -- `WorldAsset`, a soft
        path to the sublevel, and a cosmetic `LevelColor` -- so one is lifted from a
        donor and re-pointed rather than built from nothing.

        This is what lets a converted map **author its own sublevels** instead of
        copying a shipped level and re-pointing the copy's.
        """
        from riftlord.build import apply as _apply
        donor = donor or _apply.donor_for('LevelStreamingAlwaysLoaded')
        if donor is None:
            raise SystemExit('no LevelStreamingAlwaysLoaded donor available')
        src, src_index = donor
        made = []
        for n, path in enumerate(paths):
            idx = self.lift_actor(src, src_index, rename='Stream_%d' % n)
            if idx is None:
                raise SystemExit('could not lift a streaming entry for %s' % path)
            self.set_world_asset(idx, path)
            made.append(idx)
        self._write_world_streams(made)
        return made

    def set_world_asset(self, package_index, path):
        """Point a streaming entry's `WorldAsset` at a sublevel package.

        The payload is 12 bytes: an FName holding `<package path>.<asset name>`, then an
        empty FString for the sub-object path. `riftlord.apply.soft_ref` is the wrong
        tool here -- it always appends `PersistentLevel.<sub>`, so passing no sub-object
        writes the literal text `PersistentLevel.None` and a 33-byte payload that the
        package reader rejects. A world asset names a package, not an actor inside one.
        """
        import struct as _struct
        asset = path.rsplit('/', 1)[-1]
        name_index = self.pkg.add_name('%s.%s' % (path, asset))
        payload = _struct.pack('<ii', name_index, 0) + _struct.pack('<i', 0)
        self.set_property(package_index, 'WorldAsset', 'SoftObjectProperty', payload)

    def _write_world_streams(self, indices):
        """Rewrite the World export's native tail to list `indices`."""
        import struct as _struct
        from riftlord.build import apply as _apply
        world = level = None
        for i in range(len(self.pkg.exports)):
            cls = _apply._class_at(self.pkg, i)
            if cls == 'World':
                world = i
            elif cls == 'Level':
                level = i
        if world is None or level is None:
            raise SystemExit('the skeleton has no World/Level pair to wire')
        data = bytearray(self.pkg.exports[world]['data'])
        #: keep the property terminator, replace everything after it
        tail = _struct.pack('<4i', 0, level + 1, 0, len(indices))
        tail += b''.join(_struct.pack('<i', ix) for ix in indices)
        self.pkg.exports[world]['data'] = bytes(data[:8]) + tail
        self.pkg.exports[world]['size'] = 8 + len(tail)

    # -- the skeleton ----------------------------------------------------
    def _rename_world(self, name):
        """Point the World export's ObjectName at the new level's name.

        A level package's top-level object is a `World` named after the package,
        and the game finds it that way. Renaming is one FName index, because the
        name table is rewritten wholesale on write.
        """
        idx = self.pkg.add_name(name)
        for e in self.pkg.exports:
            if _name_of(self.pkg, self.pkg.exports.index(e)) == 'ScenarioDLC_03_VFX':
                meta = bytearray(e['meta'])
                struct.pack_into('<i', meta, NAME_AT, idx)
                e['meta'] = bytes(meta)

    # -- proving a lift kept its meaning ---------------------------------
    #
    # Structure is not meaning. A lifted export can be perfectly well formed and
    # still carry name indices that point into the DONOR's table -- which is how
    # a level that passed every offline check loaded as corrupt data. The tags
    # all matched; only the contents were wrong, and nothing that walks tags can
    # see that.
    #
    # So every lift is compared against its source, resolved to strings, all the
    # way down through arrays. This is `reconcile.fingerprint` for level exports,
    # and it exists for the same reason.

    #: Structs the engine serialises as raw bytes, so the comparator must NOT walk
    #: into them as a property stream. `Vector4` was the gap: `FPostProcessSettings`
    #: holds three of them (`ColorSaturation`, `ColorGamma`, `ColorOffsetShadows`) and
    #: descending into 16 bytes of floats read them as FName indices, which refused a
    #: post-process volume lift that was in fact correct.
    NATIVE = {'Guid': 16, 'Vector': 12, 'Rotator': 12, 'LinearColor': 16,
              'Color': 4, 'IntPoint': 8, 'Quat': 16, 'Vector2D': 8, 'Box': 25,
              'DateTime': 8, 'Vector4': 16}

    @classmethod
    def _deep(cls, pkg, data, o=0, end=None, out=None, depth=0):
        """Every tag and every resolved name in a property stream."""
        names = [n for n, _h in pkg.names]
        out = [] if out is None else out
        end = len(data) if end is None else end

        def nm(at):
            i = struct.unpack_from('<i', data, at)[0]
            return names[i] if 0 <= i < len(names) else 'OUT-OF-RANGE(%d)' % i

        while o + 8 <= end:
            name = nm(o)
            if name == 'None' or name.startswith('OUT-OF-RANGE'):
                out.append((depth, name, '', ''))
                return out
            kind = nm(o + 8)
            size = struct.unpack_from('<i', data, o + 16)[0]
            head, sub = 25, ''
            if kind == 'BoolProperty':
                head, size = 26, 0
            elif kind == 'StructProperty':
                sub, head = nm(o + 24), 49
            elif kind in ('ArrayProperty', 'ByteProperty', 'EnumProperty'):
                sub, head = nm(o + 24), 33
            value = ''
            if size and kind in ('NameProperty', 'SoftObjectProperty',
                                 'SoftClassProperty'):
                value = nm(o + head)
            elif size == 8 and kind in ('ByteProperty', 'EnumProperty'):
                value = nm(o + head)
            elif size == 4 and kind in ('ObjectProperty', 'ClassProperty'):
                # Object references are resolved to what they NAME, not left as
                # raw indices. Comparing indices between packages is meaningless
                # and comparing nothing at all was the gap: a reference pointing
                # at the wrong object is invisible to a walk that only reads tags.
                value = '->%s' % (resolve(pkg, struct.unpack_from(
                    '<i', data, o + head)[0])[1],)
            elif kind == 'ArrayProperty' and sub in ('ObjectProperty',
                                                     'ClassProperty') \
                    and size >= 8:
                cnt = struct.unpack_from('<i', data, o + head)[0]
                refs = []
                for k in range(min(cnt, 64)):
                    refs.append(resolve(pkg, struct.unpack_from(
                        '<i', data, o + head + 4 + k * 4)[0])[1])
                value = '[%d]%s' % (cnt, ','.join(str(r) for r in refs))
            out.append((depth, name, kind + (('<%s>' % sub) if sub else ''), value))
            if kind == 'StructProperty' and sub not in cls.NATIVE:
                cls._deep(pkg, data, o + head, o + head + size, out, depth + 1)
            elif kind == 'ArrayProperty' and sub == 'StructProperty' \
                    and size >= 4 + cls.INNER_TAG:
                at = o + head
                count = struct.unpack_from('<i', data, at)[0]
                if count > 0:
                    out.append((depth + 1, '<element tag>',
                                nm(at + 4) + '/' + nm(at + 12), nm(at + 28)))
                    body = at + 4 + cls.INNER_TAG
                    each = (size - 4 - cls.INNER_TAG) // count
                    for k in range(count):
                        cls._deep(pkg, data, body + each * k,
                                  body + each * (k + 1), out, depth + 2)
            o += head + size
        return out

    def _check_lift(self, src, src_index, our_index, label, lifted=(),
                    renames=None):
        before = self._deep(src, src.exports[src_index]['data'])
        after = self._deep(self.pkg, self.pkg.exports[our_index - 1]['data'])
        if len(before) != len(after):
            raise SystemExit('%s: lifted stream has %d entries, the source has %d'
                             % (label, len(after), len(before)))
        for a, b in zip(before, after):
            if a == b:
                continue
            if self._detached(a, b, lifted):
                continue
            if self._followed_rename(a, b, renames or {}):
                continue
            raise SystemExit('%s: lift changed meaning\n  source %s\n  ours   %s'
                             % (label, a, b))

    @staticmethod
    def _followed_rename(source_entry, our_entry, renames):
        """True when a reference still points at the same thing under its new name.

        A Blueprint actor can hold a reference to itself -- `BP_OMDRift_C`'s
        `Empty rift Timeline` has a `PropertySetObject` pointing at the rift that owns
        it -- so renaming the actor on the way in legitimately changes what that
        reference renders as. Narrow on purpose: the old and new names have to be
        exactly the pair this lift renamed, so a reference that moved to some other
        object still fails.
        """
        (_d, name, kind, was), (_d2, name2, kind2, now) = source_entry, our_entry
        if (name, kind) != (name2, kind2):
            return False
        if not (isinstance(was, str) and was.startswith('->')
                and isinstance(now, str) and now.startswith('->')):
            return False
        return renames.get(was[2:]) == now[2:]

    @staticmethod
    def _detached(source_entry, our_entry, lifted):
        """True when a reference became null because its target was not lifted.

        Narrow on purpose. It allows exactly one shape -- an object reference
        that pointed at something outside this lift and is now null -- so a
        reference that was lost for any other reason still fails.
        """
        (_d, name, kind, was), (_d2, name2, kind2, now) = source_entry, our_entry
        if (name, kind) != (name2, kind2):
            return False
        if not isinstance(was, str) or not isinstance(now, str):
            return False
        if was.startswith('->'):
            if now != '->None':
                return False
            return was[2:] not in lifted
        # The same thing in array form. An `OMDAIPath` lifted on its own has a
        # `PathPoints` array naming the donor level's points, none of which come with
        # it, so every entry nulls; the caller then fills the array with its own. Every
        # entry has to null and none of the targets may have been lifted, so an array
        # that lost only some of its references still fails.
        head = re.match(r'^\[(\d+)\](.*)$', was)
        head2 = re.match(r'^\[(\d+)\](.*)$', now)
        if not head or not head2 or head.group(1) != head2.group(1):
            return False
        source_targets = [t for t in head.group(2).split(',') if t]
        our_targets = [t for t in head2.group(2).split(',') if t]
        if not source_targets or len(source_targets) != len(our_targets):
            return False
        if any(t != 'None' for t in our_targets):
            return False
        return all(t not in lifted for t in source_targets)

    # -- the level's actor list ------------------------------------------
    #
    # An actor export that is not in this list is in the FILE but not in the
    # WORLD. `ULevel` writes it straight after its tagged properties, and the
    # layout is the same in every level checked (2, 256 and 768 actors):
    #
    #     int32 0            always zero
    #     int32 N            how many actors
    #     int32 * N          FPackageIndex each; [0] is the WorldSettings actor
    #                        and [1] is a null slot (the default brush, absent
    #                        from a cooked level with no BSP)
    #     151 bytes          trailer, byte-identical across levels
    #
    def _level_export(self):
        for i, e in enumerate(self.pkg.exports):
            cls = struct.unpack_from('<i', e['meta'], CLASS_AT)[0]
            if cls < 0 and _import_name(self.pkg, -cls - 1) == 'Level':
                return i, e
        raise SystemExit('this package has no Level export')

    def _actor_list(self):
        """(export index, data, array offset, count) for the Level."""
        i, e = self._level_export()
        data = bytearray(e['data'])
        w = pkgcheck.Walk(bytes(data), [n for n, _h in self.pkg.names])
        pkgcheck.properties(w, 'Level')
        at = w.o + 4
        return i, data, at, struct.unpack_from('<i', data, at)[0]

    def preload_listed_actors(self):
        """Put every listed actor into the Level export's create-before-serialization group.

        Shipped levels list every actor of `ULevel::Actors` in that group (Scenario_01:
        77 of 77; Scenario_01_Castle: 767 of 767). Appended at the end of the group, in
        list order; the slices of later exports shift by the number added. Returns how
        many were added.
        """
        li, e = self._level_export()
        pkg = self.pkg
        meta = bytearray(e['meta'])
        first = struct.unpack_from('<i', meta, 84)[0]
        counts = list(struct.unpack_from('<4i', meta, 88))
        if first < 0:
            first, counts = len(pkg.preload), [0, 0, 0, 0]
        end = first + counts[0] + counts[1]
        have = set(pkg.preload[first + counts[0]:end])
        missing = []
        for a in self.actor_list():
            if a > 0 and a not in have:
                missing.append(a)
                have.add(a)
        if not missing:
            return 0
        pkg.preload[end:end] = missing
        for i, other in enumerate(pkg.exports):
            if i == li:
                continue
            at = struct.unpack_from('<i', other['meta'], 84)[0]
            if at >= end:
                m = bytearray(other['meta'])
                struct.pack_into('<i', m, 84, at + len(missing))
                other['meta'] = bytes(m)
        counts[1] += len(missing)
        struct.pack_into('<i', meta, 84, first)
        struct.pack_into('<4i', meta, 88, *counts)
        pkg.exports[li]['meta'] = bytes(meta)
        pkg.preload_count = len(pkg.preload)
        return len(missing)

    def level_preload_gaps(self):
        """Listed actors missing from the Level export's create-before-serialization group."""
        li, e = self._level_export()
        first = struct.unpack_from('<i', e['meta'], 84)[0]
        counts = struct.unpack_from('<4i', e['meta'], 88)
        have = (set(self.pkg.preload[first + counts[0]:first + counts[0] + counts[1]])
                if first >= 0 else set())
        return [a for a in self.actor_list() if a > 0 and a not in have]

    def register_actor(self, package_index):
        """Put an actor into the level's list, so the world actually has it.

        `write` adds it to the Level export's preload group (preload_listed_actors)."""
        i, data, at, n = self._actor_list()
        insert = at + 4 + n * 4
        data[insert:insert] = struct.pack('<i', package_index)
        struct.pack_into('<i', data, at, n + 1)
        self.pkg.exports[i]['data'] = bytes(data)
        self.pkg.exports[i]['size'] = len(data)
        return n + 1

    def unregister_actor(self, package_index):
        """Take an actor out of the level's list. Returns True when it was listed.

        The export stays in the package, so no package index moves; the world never
        registers its components or begins play on it.
        """
        i, data, at, n = self._actor_list()
        listed = list(struct.unpack_from('<%di' % n, data, at + 4))
        if package_index not in listed:
            return False
        k = listed.index(package_index)
        del data[at + 4 + 4 * k:at + 8 + 4 * k]
        struct.pack_into('<i', data, at, n - 1)
        self.pkg.exports[i]['data'] = bytes(data)
        self.pkg.exports[i]['size'] = len(data)
        return True

    def actor_list(self):
        """What the level currently says its actors are, for checking."""
        _i, data, at, n = self._actor_list()
        return list(struct.unpack_from('<%di' % n, data, at + 4))

    def index_of(self, object_name):
        for i in range(len(self.pkg.exports)):
            if _name_of(self.pkg, i) == object_name:
                return i + 1                       # FPackageIndex is 1-based
        return 0

    # -- lifting ---------------------------------------------------------
    def lift_actor(self, src, actor_index, rename=None):
        """Lift an actor and everything under it, in two passes.

        Ordering here pulls in three directions at once, and trying to satisfy
        them by choosing a clever sequence failed three times: an actor names its
        components, a component's OUTER is its actor, and a component's
        `AttachParent` is a SIBLING component. Whatever order you lift in,
        something refers to something that does not exist yet -- and the last one
        is silent, because an unattached component still renders. It just does
        not collide, and nothing structural notices.

        So the sequence is abandoned. **Every export is placed first, with its
        bytes untouched, and only then is anything rewritten** -- by which point
        the map from the donor's indices to ours is complete and no reference can
        resolve to null for want of existing yet.
        """
        kids = self._closure(src, actor_index)
        self._missed = []

        # pass one: reserve an export for each, still carrying donor bytes
        mapping = {}
        for j in kids + [actor_index]:
            self.pkg.exports.append({'meta': bytes(src.exports[j]['meta']),
                                     'data': bytes(src.exports[j]['data'] or b''),
                                     'size': 0, 'offset': 0})
            mapping[j] = len(self.pkg.exports)
        actor = mapping[actor_index]

        # pass two: now every reference has somewhere to land
        for j, ours in mapping.items():
            self._fixup(src, j, ours, mapping,
                        rename if j == actor_index else None)
        #: `_fixup` already maps each export's OUTER through the lift, which for a
        #: direct child is the actor and for a GRANDCHILD is its own parent component.
        #: This used to force every lifted export's outer to the actor, which was the
        #: same answer for children and wrong for anything deeper.

        self.register_actor(actor)

        # An actor may be attached to a DIFFERENT actor in the donor level --
        # kit pieces get parented to each other by level designers. That parent
        # is not part of this lift, so the reference comes out null, and that is
        # correct: the copy is being placed somewhere else entirely and must not
        # stay bolted to an actor it no longer sits beside. The check is told
        # which names were lifted so it can tell a deliberate detach from a
        # reference that was lost by mistake.
        #: A reference that nulled because its target is OWNED by this actor is a
        #: lift that lost part of itself, and the package still reads back perfectly --
        #: so it is refused here rather than left for the game to crash on.
        #:
        #: Ownership is read from the donor's outer chain, NOT from `_closure`. Asking
        #: the closure whether it should have included something is circular: the first
        #: version of this guard did exactly that and was dead code, which only showed
        #: up because reverting `_closure` to the one-level version failed to trip it.
        for index, name in self._missed:
            if self._owns(src, actor_index, index - 1):
                raise SystemExit(
                    '%s: lift dropped %r, which the actor owns -- it should have been '
                    'in the closure' % (_name_of(src, actor_index), name))
        lifted = {_name_of(src, j) for j in mapping}
        renames = ({_name_of(src, actor_index): rename} if rename else {})
        for donor_index, ours in mapping.items():
            self._check_lift(src, donor_index, ours, _name_of(src, donor_index),
                             lifted, renames)
        return actor

    def _closure(self, src, actor_index):
        """Every export UNDER an actor, transitively, excluding the actor itself.

        An actor's own components are its direct children, but a component may own
        sub-objects of its own: a `BrushComponent` owns a `BodySetup`, whose outer is
        the COMPONENT. Lifting one level deep left that reference pointing at a donor
        export this package does not have, `_reindex` fell back to resolving it by name,
        found nothing, and wrote **null** -- and a brush component with no body setup
        crashes the game while the level loads, with the package reading back perfect
        offline. Measured on a `PostProcessVolume` lift: two nulled `BodySetup`s.

        Breadth first and in export order at each level, so the direct children keep
        exactly the order the one-level version produced and a lift with no
        grandchildren comes out byte for byte the same.
        """
        found, frontier = [], {actor_index + 1}
        seen = {actor_index}
        while frontier:
            nxt = set()
            for j, k in enumerate(src.exports):
                if j in seen:
                    continue
                if struct.unpack_from('<i', k['meta'], OUTER_AT)[0] in frontier:
                    found.append(j)
                    seen.add(j)
                    nxt.add(j + 1)
            frontier = nxt
        return found

    @staticmethod
    def _owns(src, actor_index, j):
        """True when donor export `j`'s outer chain reaches the actor."""
        if not 0 <= j < len(src.exports):
            return False
        o = struct.unpack_from('<i', src.exports[j]['meta'], OUTER_AT)[0]
        for _hop in range(16):
            if o <= 0:
                return False
            if o - 1 == actor_index:
                return True
            o = struct.unpack_from('<i', src.exports[o - 1]['meta'], OUTER_AT)[0]
        return False

    #: Classes whose bytes after the property terminator are opaque data, MEASURED to
    #: carry no FName and no object reference:
    #:
    #: * `Model` -- parsed in full (bounds, vectors, points, nodes, surfs, verts); its
    #:   six `FBspSurf` entries all hold `Material=0, Actor=0`.
    #: * `AtmosphericFogComponent` -- three inline `FByteBulkData` payloads of
    #:   precomputed atmosphere tables, 594 KB, byte-identical after a lift.
    #: * `StaticMeshComponent` -- 14 bytes; proven in game across 8,389 lifted actors.
    #: * `SplineMeshComponent` -- serialises nothing after `UStaticMeshComponent::Serialize`
    #:   (SplineMeshComponent.cpp:500); the rain lightning's are 8 zero bytes (no LODData).
    NATIVE_OPAQUE = frozenset({'Model', 'AtmosphericFogComponent', 'StaticMeshComponent',
                               'SplineMeshComponent'})

    #: Classes whose native payload DOES need rewriting, and the method that does it.
    NATIVE_FIXERS = {'BodySetup': '_fix_bodysetup'}

    #: What every cooked export carries after its terminator and nothing else: one
    #: zero int32. Measured on 410 of the 481 exports of a shipped sublevel.
    NATIVE_TRIVIAL = b'\0\0\0\0'

    def _native_tail(self, src, data, our_index, label):
        """Rewrite -- or refuse -- the bytes after an export's property terminator.

        `_rewrite` stops at the terminator, so anything a class serialises NATIVELY is
        copied verbatim. That is right for opaque data and **fatal for an FName**, whose
        value is an index into the donor's name table: `UBodySetup` stores its cooked
        physics format as a native `TMap<FName, FByteBulkData>` whose key was index 266
        (`PhysXPC`) in a 459-name donor, copied into a 222-name package, and the game
        crashed opening the map with every offline check passing.

        So a native payload is never ignored. It is rewritten if we know how, and
        otherwise the lift is REFUSED -- which is what turns this class of bug into a
        build failure instead of a crash the user has to find.
        """
        at = self._terminator(data)
        if at is None:
            return
        payload = at + 8
        if len(data) - payload <= len(self.NATIVE_TRIVIAL):
            return
        cls = label
        if cls in self.NATIVE_OPAQUE:
            return
        fixer = self.NATIVE_FIXERS.get(cls)
        if fixer is None:
            raise SystemExit(
                '%s: %d bytes of NATIVE payload after the property terminator and no '
                'handler for class %r. `_rewrite` cannot see it, so an FName or object '
                'reference in there would be copied verbatim and the game would crash '
                'on load. Study it and add it to NATIVE_OPAQUE or NATIVE_FIXERS.'
                % (label, len(data) - payload, cls))
        got = getattr(self, fixer)(src, data, payload)
        if got is None:
            raise SystemExit(
                '%s: the %r native payload did not parse the way this code expects, so '
                'nothing was rewritten. Refusing rather than shipping it.' % (label, cls))

    def _fix_bodysetup(self, src, data, at):
        """Remap the FName keys of `UBodySetup`'s native `CookedFormatData`.

        Layout, from `UBodySetup::Serialize` in 4.27.2 -- the universal zero int32 that
        ends every cooked export, then `BodySetupGuid`, `bCooked`,
        `bHasCookedCollisionData`, then `FFormatContainer`: a count and, per format, an
        FName and an `FByteBulkData` whose payload is inline unless
        `BULKDATA_PayloadAtEndOfFile` is set.

        Self-validating: it must consume the export EXACTLY, or it returns None and the
        caller refuses the lift. A misunderstood layout then fails the build instead of
        quietly rewriting the wrong four bytes.
        """
        o = at + 4 + 16 + 4 + 4
        if o + 4 > len(data):
            return None
        count = struct.unpack_from('<i', data, o)[0]
        o += 4
        if not 0 <= count <= 8:
            return None
        done = []
        for _k in range(count):
            if o + 28 > len(data):
                return None
            ni = struct.unpack_from('<i', data, o)[0]
            if not 0 <= ni < len(src.names):
                return None
            text = src.names[ni][0]
            struct.pack_into('<i', data, o, self.pkg.add_name(text))
            done.append(text)
            o += 8
            flags, _elements, on_disk = struct.unpack_from('<iii', data, o)
            o += 20 + (0 if (flags & 0x0001) else max(on_disk, 0))
        return done if o == len(data) else None

    def _fixup(self, src, src_index, our_index, mapping, rename=None):
        """Rewrite one placed export's meta and data into this package."""
        e = src.exports[src_index]
        meta = bytearray(e['meta'])
        data = bytearray(self.pkg.exports[our_index - 1]['data'])

        for at in (CLASS_AT, 4, 8, OUTER_AT):
            old = struct.unpack_from('<i', meta, at)[0]
            struct.pack_into('<i', meta, at, self._reindex(src, old, mapping))
        old_name = struct.unpack_from('<i', meta, NAME_AT)[0]
        text = src.names[old_name][0] if 0 <= old_name < len(src.names) else 'None'
        struct.pack_into('<i', meta, NAME_AT, self.pkg.add_name(rename or text))
        if rename:
            struct.pack_into('<i', meta, NAME_AT + 4, 0)

        self._rewrite(src, data, mapping=mapping)
        self._native_tail(src, data, our_index,
                          resolve(src, struct.unpack_from('<i', e['meta'], CLASS_AT)[0])[1])

        # Preload dependencies, copied and translated. Not optional and not
        # visible offline: an export declares what the loader must create before
        # it, and writing "nothing" lets the loader use objects before it has
        # made them -- the game dies during level load with no message and no
        # dump, and `pkgcheck` calls the file perfect either way.
        #
        # A dependency on a donor export that is neither lifted nor found here by name
        # is DROPPED from its group, never written as null: the loader indexes a node
        # table with every entry, and no shipped level carries a null one (134,992
        # entries over 31 maps). A path point depends on the donor's `OMDAIPath` and a
        # door on the donor's level script class, and those names differ per level.
        first = struct.unpack_from('<i', e['meta'], 84)[0]
        counts = list(struct.unpack_from('<4i', e['meta'], 88))
        groups = []
        if first >= 0:
            at = first
            for n in counts:
                group = []
                for _ in range(n):
                    if at < len(src.preload):
                        group.append(src.preload[at])
                    at += 1
                groups.append(group)
        kept = []
        for group in groups:
            out = []
            for v in group:
                got = self._reindex(src, v, mapping)
                if got == 0 and v != 0:
                    self.preload_dropped.append(
                        (our_index, _name_of(src, v - 1) if v > 0 else str(v)))
                    continue
                out.append(got)
            kept.append(out)
        if any(kept):
            struct.pack_into('<i', meta, 84, len(self.pkg.preload))
            for out in kept:
                self.pkg.preload.extend(out)
            struct.pack_into('<4i', meta, 88, *[len(out) for out in kept])
            self.pkg.preload_count = len(self.pkg.preload)
        else:
            struct.pack_into('<i', meta, 84, -1)
            struct.pack_into('<4i', meta, 88, 0, 0, 0, 0)

        self.pkg.exports[our_index - 1]['meta'] = bytes(meta)
        self.pkg.exports[our_index - 1]['data'] = bytes(data)
        self.pkg.exports[our_index - 1]['size'] = len(data)

    def _reindex(self, src, index, mapping=None):
        """Translate one FPackageIndex from the donor into this package."""
        if index == 0:
            return 0
        if mapping and index > 0 and (index - 1) in mapping:
            return mapping[index - 1]
        if index < 0:
            im = src.imports[-index - 1]
            cls_pkg = src.names[struct.unpack_from('<I', im, 0)[0]][0]
            cls = src.names[struct.unpack_from('<I', im, 8)[0]][0]
            outer = struct.unpack_from('<i', im, 16)[0]
            name = src.names[struct.unpack_from('<I', im, 20)[0]][0]
            #: the ObjectName's FName number: `P_CauldronPour_650` is stored as
            #: `P_CauldronPour` number 651, and dropping it imports another asset
            number = struct.unpack_from('<i', im, 24)[0]
            return self._import(cls_pkg, cls, self._reindex(src, outer, mapping), name,
                                number)
        # An export reference: it must already have been lifted, by name.
        #
        # A MISS here writes null, and a null is sometimes right (an actor bolted to a
        # sibling actor that is not part of this lift) and sometimes fatal (a component's
        # own `BodySetup`). The miss is recorded so `lift_actor` can tell the two apart
        # by asking whether the target was OWNED by the actor -- see `_closure`.
        got = self.index_of(_name_of(src, index - 1))
        if not got:
            self._missed.append((index, _name_of(src, index - 1)))
        return got or 0

    def _import(self, class_package, class_name, outer, object_name, number=0):
        """Add an import, or return the one that is already there. `number` is the
        ObjectName's FName number (0: none)."""
        for i, rec in enumerate(self.pkg.imports):
            got = (self.pkg.names[struct.unpack_from('<I', rec, 0)[0]][0],
                   self.pkg.names[struct.unpack_from('<I', rec, 8)[0]][0],
                   struct.unpack_from('<i', rec, 16)[0],
                   self.pkg.names[struct.unpack_from('<I', rec, 20)[0]][0],
                   struct.unpack_from('<i', rec, 24)[0])
            if got == (class_package, class_name, outer, object_name, number):
                return -(i + 1)
        got = self.pkg.add_import(class_package, class_name, outer, object_name)
        if number:
            rec = bytearray(self.pkg.imports[-got - 1])
            struct.pack_into('<i', rec, 24, number)
            self.pkg.imports[-got - 1] = bytes(rec)
        return got

    def _rewrite(self, src, buf, at=0, limit=None, mapping=None):
        """Rewrite every FName and object reference in a property stream."""
        end = len(buf) if limit is None else limit
        o = at
        while o + 8 <= end:
            ni = struct.unpack_from('<i', buf, o)[0]
            if not 0 <= ni < len(src.names):
                return
            name = src.names[ni][0]
            struct.pack_into('<i', buf, o, self.pkg.add_name(name))
            if name == 'None':
                return
            ti = struct.unpack_from('<i', buf, o + 8)[0]
            kind = src.names[ti][0] if 0 <= ti < len(src.names) else ''
            struct.pack_into('<i', buf, o + 8, self.pkg.add_name(kind))
            size = struct.unpack_from('<i', buf, o + 16)[0]
            head, inner = 24 + 1, ''
            if kind == 'BoolProperty':
                head, size = 26, 0
            elif kind == 'StructProperty':
                head = 24 + 8 + 17
                self._name_at(src, buf, o + 24)
            elif kind in ('ArrayProperty', 'ByteProperty', 'EnumProperty'):
                head = 24 + 8 + 1
                inner = self._text(src, buf, o + 24)
                self._name_at(src, buf, o + 24)
            if kind == 'ArrayProperty':
                self._array(src, buf, o + head, size, inner, mapping)
            else:
                self._payload(src, buf, o + head, size, kind, mapping)
            o += head + size

    #: An array of structs carries an FPropertyTag of its own before the
    #: elements: name, type, size, index, struct name, guid, HasPropertyGuid.
    INNER_TAG = 8 + 8 + 4 + 4 + 8 + 16 + 1

    def _text(self, src, buf, at):
        i = struct.unpack_from('<i', buf, at)[0]
        return src.names[i][0] if 0 <= i < len(src.names) else ''

    def _array(self, src, buf, at, size, inner, mapping=None):
        """Rewrite the contents of a TArray, which is where names hide deepest.

        Leaving this out is what made a lifted component load as corrupt data.
        `BodyInstance.CollisionResponses.ResponseArray` is an array of structs
        each holding a `Channel` NameProperty, and an element whose name index
        still points at the donor's table makes the engine read a property that
        is not there. The tags all match; only the contents are wrong, which is
        why comparing tag by tag showed nothing.
        """
        if size < 4:
            return
        count = struct.unpack_from('<i', buf, at)[0]
        if count <= 0:
            return
        if inner == 'StructProperty':
            if size < 4 + self.INNER_TAG:
                return
            tag = at + 4
            self._name_at(src, buf, tag)            # the element tag's name
            self._name_at(src, buf, tag + 8)        # its type
            self._name_at(src, buf, tag + 24)       # the struct's name
            body = at + 4 + self.INNER_TAG
            each = (size - 4 - self.INNER_TAG) // count
            for k in range(count):
                start = body + each * k
                self._rewrite(src, buf, start, start + each, mapping)
        elif inner in ('ObjectProperty', 'ClassProperty'):
            for k in range(count):
                slot = at + 4 + k * 4
                old = struct.unpack_from('<i', buf, slot)[0]
                struct.pack_into('<i', buf, slot, self._reindex(src, old, mapping))
        elif inner in ('NameProperty', 'SoftObjectProperty', 'SoftClassProperty'):
            for k in range(count):
                self._name_at(src, buf, at + 4 + k * 8)

    def _name_at(self, src, buf, at):
        i = struct.unpack_from('<i', buf, at)[0]
        if 0 <= i < len(src.names):
            struct.pack_into('<i', buf, at, self.pkg.add_name(src.names[i][0]))

    def _payload(self, src, buf, at, size, kind, mapping=None):
        if size <= 0:
            return
        if kind in ('NameProperty', 'SoftObjectProperty', 'SoftClassProperty'):
            self._name_at(src, buf, at)
        elif kind in ('ObjectProperty', 'ClassProperty'):
            old = struct.unpack_from('<i', buf, at)[0]
            struct.pack_into('<i', buf, at, self._reindex(src, old, mapping))
        elif kind in ('ByteProperty', 'EnumProperty') and size == 8:
            self._name_at(src, buf, at)
        elif kind == 'DelegateProperty' and size == 12:
            #: object, then function name (a timeline's update and finished calls)
            old = struct.unpack_from('<i', buf, at)[0]
            struct.pack_into('<i', buf, at, self._reindex(src, old, mapping))
            self._name_at(src, buf, at + 4)
        elif kind == 'StructProperty':
            chunk = bytes(buf[at:at + size])
            if len(chunk) >= 8:
                i = struct.unpack_from('<i', chunk, 0)[0]
                if 0 <= i < len(src.names) and (
                        src.names[i][0] == 'None'
                        or (len(chunk) >= 24 and src.names[
                            struct.unpack_from('<i', chunk, 8)[0]
                            if 0 <= struct.unpack_from('<i', chunk, 8)[0]
                            < len(src.names) else 0][0].endswith('Property'))):
                    self._rewrite(src, buf, at, at + size, mapping)

    def _root_component(self, package_index):
        """The package index named by the actor's RootComponent, or None.

        That is the one component whose relative transform IS the actor's world
        transform; every other component hangs off it.
        """
        data = bytearray(self.pkg.exports[package_index - 1]['data'])
        span = self._tag_span(data, 'RootComponent')
        if not span:
            return None
        _start, _end, payload = span
        # ObjectProperty's payload is the package index it points at.
        value = struct.unpack_from('<i', data, payload)[0]
        return value if value > 0 else None

    def place(self, package_index, location=None, rotation=None, scale=None):
        """Set a lifted actor's transform, on the component that carries it.

        A lifted actor arrives wearing the donor's transform. The transform lives
        on the actor's root component as `RelativeLocation` / `RelativeRotation`
        / `RelativeScale3D`, and a component that never had the property cannot
        gain one here -- rewriting a tagged stream to insert a property means
        moving every size field after it. Every kit piece and static mesh actor
        checked has all three, because the cooker writes them whenever they
        differ from the default.
        """
        e = self.pkg.exports[package_index - 1]
        # ONLY the root component, never every component under the actor.
        #
        # A child component's RelativeLocation/RelativeRotation are relative to its
        # PARENT. Writing the actor's world transform into all of them put the child
        # at root + location (double-offset) and replaced its own rotation with the
        # actor's. Measured on an emitted force field door: its DoorMesh ended up at
        # the absolute world position with rotation (0,0,0) instead of the donor's
        # (-90, 0, 90) -- so the panel sat in the wrong place, lying flat, and flyers
        # collided with it.
        targets = [package_index]
        root = self._root_component(package_index)
        if root:
            targets.append(root)
        else:
            # No RootComponent to read: fall back to the single child that carries a
            # transform. Several children and we would be guessing, so take none and
            # say so rather than scatter the transform over all of them.
            kids = [i + 1 for i, k in enumerate(self.pkg.exports)
                    if struct.unpack_from('<i', k['meta'], OUTER_AT)[0] == package_index
                    and self._find_vector(bytearray(k['data']), 'RelativeLocation')
                    is not None]
            if len(kids) == 1:
                targets.append(kids[0])
        wrote = []
        for t in targets:
            data = bytearray(self.pkg.exports[t - 1]['data'])
            for field, value in (('RelativeLocation', location),
                                 ('RelativeRotation', rotation),
                                 ('RelativeScale3D', scale)):
                if value is None:
                    continue
                at = self._find_vector(data, field)
                if at is None:
                    continue
                struct.pack_into('<3f', data, at, *value)
                wrote.append('%s on export %d' % (field, t))
            self.pkg.exports[t - 1]['data'] = bytes(data)
        return wrote

    #: A property tag is name, type, size, array index, then a type-specific
    #: header and the payload. These are the header lengths, which `_find_vector`
    #: and `add_property` both need to agree on.
    HEAD = {'BoolProperty': 26, 'StructProperty': 49,
            'ArrayProperty': 33, 'ByteProperty': 33, 'EnumProperty': 33}
    HEAD_DEFAULT = 25

    def add_property(self, package_index, name, kind, payload,
                     struct_name=None):
        """Insert a property an export does not have, before its None.

        This turned out to be far easier than feared. A tag's `size` covers only
        its own payload, and a component's properties sit at the top level with
        no enclosing struct, so nothing else's size has to be adjusted -- the new
        tag is simply spliced in ahead of the terminator. What makes it necessary
        is that a blueprint instance stores only OVERRIDES: a lifted trap grid
        carries four properties and none of them is `BoxExtent`, because the
        extent lives in the class. Giving a converted level a trap grid of its
        own size means adding the property, not editing it.

        Splicing before the None matters: a component export has bytes after the
        terminator, and appending would land the tag in them.
        """
        data = bytearray(self.pkg.exports[package_index - 1]['data'])
        at = self._terminator(data)
        if at is None:
            raise SystemExit('export %d has no None terminator' % package_index)
        tag = bytearray()
        tag += struct.pack('<ii', self.pkg.add_name(name), 0)
        tag += struct.pack('<ii', self.pkg.add_name(kind), 0)
        tag += struct.pack('<ii', len(payload), 0)
        if kind == 'StructProperty':
            tag += struct.pack('<ii', self.pkg.add_name(struct_name), 0)
            tag += b'\0' * 16
        elif kind in ('ArrayProperty', 'ByteProperty', 'EnumProperty'):
            tag += struct.pack('<ii', self.pkg.add_name(struct_name or 'None'), 0)
        tag += b'\0'                                # HasPropertyGuid
        tag += payload
        data[at:at] = tag
        self.pkg.exports[package_index - 1]['data'] = bytes(data)
        self.pkg.exports[package_index - 1]['size'] = len(data)
        return len(tag)


    def _tag_span(self, data, field):
        """(start, end, payload start) of one property's tag, or None."""
        names = [n for n, _h in self.pkg.names]
        o = 0
        while o + 8 <= len(data):
            ni = struct.unpack_from('<i', data, o)[0]
            if not 0 <= ni < len(names):
                return None
            name = names[ni]
            if name == 'None':
                return None
            ti = struct.unpack_from('<i', data, o + 8)[0]
            kind = names[ti] if 0 <= ti < len(names) else ''
            size = struct.unpack_from('<i', data, o + 16)[0]
            head = 24 + 1
            if kind == 'BoolProperty':
                head, size = 26, 0
            elif kind == 'StructProperty':
                head = 24 + 8 + 17
            elif kind in ('ArrayProperty', 'ByteProperty', 'EnumProperty'):
                head = 24 + 8 + 1
            if name == field:
                return o, o + head + size, o + head
            o += head + size
        return None

    def set_property(self, package_index, name, kind, payload,
                     struct_name=None):
        """Set a property whether or not the export already has one.

        An existing tag is removed and a new one spliced in rather than written
        over, because the replacement is usually a different length -- a path
        point lifted from a shipped level carries one branch and may need three.
        """
        data = bytearray(self.pkg.exports[package_index - 1]['data'])
        span = self._tag_span(data, name)
        if span is not None:
            start, end, _at = span
            del data[start:end]
            self.pkg.exports[package_index - 1]['data'] = bytes(data)
            self.pkg.exports[package_index - 1]['size'] = len(data)
        return self.add_property(package_index, name, kind, payload,
                                 struct_name)

    def clear_property(self, package_index, name):
        """Remove a property so the export falls back to its class default.

        A lifted actor keeps every property the donor had, which is usually what makes
        it work -- and occasionally exactly wrong: a spawner lifted from a shipped level
        arrives still naming that level's door and route, so a new spawner with no door
        of its own claimed the donor's. Removing the tag is right rather than writing an
        empty one, because that is how the shipped data says "none": the flyer spawner in
        the Ice DLC has no `StartingPathPoint` property at all.
        """
        data = bytearray(self.pkg.exports[package_index - 1]['data'])
        span = self._tag_span(data, name)
        if span is None:
            return False
        start, end, _at = span
        del data[start:end]
        self.pkg.exports[package_index - 1]['data'] = bytes(data)
        self.pkg.exports[package_index - 1]['size'] = len(data)
        return True

    def set_bool(self, package_index, name, value):
        """Set a BoolProperty, whose value lives in the tag and not in a payload."""
        data = bytearray(self.pkg.exports[package_index - 1]['data'])
        span = self._tag_span(data, name)
        if span is not None:
            start, _end, _at = span
            data[start + 24] = 1 if value else 0
            self.pkg.exports[package_index - 1]['data'] = bytes(data)
            return 'set'
        at = self._terminator(data)
        if at is None:
            raise SystemExit('export %d has no None terminator' % package_index)
        tag = bytearray()
        tag += struct.pack('<ii', self.pkg.add_name(name), 0)
        tag += struct.pack('<ii', self.pkg.add_name('BoolProperty'), 0)
        tag += struct.pack('<ii', 0, 0)
        tag += bytes([1 if value else 0])           # the value
        tag += b'\0'                                # HasPropertyGuid
        data[at:at] = tag
        self.pkg.exports[package_index - 1]['data'] = bytes(data)
        self.pkg.exports[package_index - 1]['size'] = len(data)
        return 'added'

    def set_object(self, package_index, name, target_index):
        """Point an ObjectProperty at another export, by package index."""
        return self.set_property(package_index, name, 'ObjectProperty',
                                 struct.pack('<i', target_index))

    def object_array(self, package_index, name):
        """The package indices an ArrayProperty of ObjectProperty already holds.

        Needed to APPEND to one. A level has a single `OMDAIPath` listing every point in
        it, so adding a route means extending that array rather than replacing it.
        """
        data = bytearray(self.pkg.exports[package_index - 1]['data'])
        span = self._tag_span(data, name)
        if span is None:
            return []
        _start, end, at = span
        count = struct.unpack_from('<i', data, at)[0]
        if count < 0 or at + 4 + count * 4 > end:
            return []
        return [struct.unpack_from('<i', data, at + 4 + i * 4)[0]
                for i in range(count)]

    def set_object_array(self, package_index, name, targets):
        """Set an ArrayProperty of ObjectProperty -- a count then package indices."""
        payload = struct.pack('<i', len(targets))
        payload += b''.join(struct.pack('<i', t) for t in targets)
        return self.set_property(package_index, name, 'ArrayProperty', payload,
                                 'ObjectProperty')

    def struct_array(self, package_index, name, struct_name, elements):
        """Set an ArrayProperty of StructProperty from pre-built element bodies.

        The inner tag is 49 bytes -- name, type, size, index, struct name, a
        16-byte guid and the has-guid byte -- and **its size field covers the
        element bodies only**, not the count and not itself. Getting that wrong is
        what made a merged pak crash the game with "corrupt data found".
        """
        bodies = b''.join(elements)
        inner = bytearray()
        inner += struct.pack('<ii', self.pkg.add_name(name), 0)
        inner += struct.pack('<ii', self.pkg.add_name('StructProperty'), 0)
        inner += struct.pack('<ii', len(bodies), 0)
        inner += struct.pack('<ii', self.pkg.add_name(struct_name), 0)
        inner += b'\0' * 16
        inner += b'\0'
        payload = struct.pack('<i', len(elements)) + bytes(inner) + bodies
        return self.set_property(package_index, name, 'ArrayProperty', payload,
                                 'StructProperty')

    def tagged(self, fields):
        """Build a tagged-property body: [(name, kind, payload)] then a None.

        This is how one element of a struct array is written -- an
        `OMDPathBranch` is three tags and a terminator, 95 bytes.
        """
        out = bytearray()
        for name, kind, payload in fields:
            out += struct.pack('<ii', self.pkg.add_name(name), 0)
            out += struct.pack('<ii', self.pkg.add_name(kind), 0)
            out += struct.pack('<ii', len(payload), 0)
            out += b'\0'
            out += payload
        out += struct.pack('<ii', self.pkg.add_name('None'), 0)
        return bytes(out)

    def _terminator(self, data):
        """Offset of the property stream's None, or None if it has no end."""
        names = [n for n, _h in self.pkg.names]
        o = 0
        while o + 8 <= len(data):
            ni = struct.unpack_from('<i', data, o)[0]
            if not 0 <= ni < len(names):
                return None
            if names[ni] == 'None':
                return o
            ti = struct.unpack_from('<i', data, o + 8)[0]
            kind = names[ti] if 0 <= ti < len(names) else ''
            size = struct.unpack_from('<i', data, o + 16)[0]
            head = self.HEAD.get(kind, self.HEAD_DEFAULT)
            if kind == 'BoolProperty':
                size = 0
            o += head + size
        return None

    def set_vector(self, package_index, name, xyz, struct_name='Vector'):
        """Set a three-float property, adding it if the export lacks one."""
        data = bytearray(self.pkg.exports[package_index - 1]['data'])
        at = self._find_vector(data, name)
        if at is not None:
            struct.pack_into('<3f', data, at, *xyz)
            self.pkg.exports[package_index - 1]['data'] = bytes(data)
            return 'set'
        self.add_property(package_index, name, 'StructProperty',
                          struct.pack('<3f', *xyz), struct_name)
        return 'added'

    def _find_vector(self, data, field):
        """Offset of a 12-byte struct property's payload, or None."""
        names = [n for n, _h in self.pkg.names]
        o = 0
        while o + 8 <= len(data):
            ni = struct.unpack_from('<i', data, o)[0]
            if not 0 <= ni < len(names):
                return None
            name = names[ni]
            if name == 'None':
                return None
            ti = struct.unpack_from('<i', data, o + 8)[0]
            kind = names[ti] if 0 <= ti < len(names) else ''
            size = struct.unpack_from('<i', data, o + 16)[0]
            head, inner = 24 + 1, ''
            if kind == 'BoolProperty':
                head, size = 26, 0
            elif kind == 'StructProperty':
                head = 24 + 8 + 17
            elif kind in ('ArrayProperty', 'ByteProperty', 'EnumProperty'):
                head = 24 + 8 + 1
            if name == field and kind == 'StructProperty' and size == 12:
                return o + head
            o += head + size
        return None

    def tile(self, src, actor_index, x0, y0, x1, y1, z, step=1200.0,
             prefix='Floor'):
        """Lift one actor repeatedly to cover a rectangle, on a `step` grid.

        1200 is four cells of the 300uu grid the kit is authored against, which
        is the footprint of a `Floor_SB_4x4`. The piece's own origin is a corner
        rather than its centre, so a tile placed at (x, y) covers (x, y) to
        (x+1200, y+1200) and the rectangle is walked from its minimum.
        """
        made = []
        y = y0
        while y < y1:
            x = x0
            while x < x1:
                idx = self.lift_actor(src, actor_index,
                                      rename='%s_%d' % (prefix, len(made)))
                self.place(idx, location=(x, y, z))
                made.append(idx)
                x += step
            y += step
        return made

    # -- output ----------------------------------------------------------
    def write(self):
        self.preload_listed_actors()
        ua, ux = self.pkg.write()
        bad = pkgcheck.problems(ua, ux, self.name, 'object')
        if bad:
            raise SystemExit('the level does not read back:\n  '
                             + '\n  '.join(bad))
        return ua, ux


def find_actor(src, class_name, nth=0):
    """The nth export of a class, and the components under it."""
    seen = 0
    for i, e in enumerate(src.exports):
        cls = struct.unpack_from('<i', e['meta'], CLASS_AT)[0]
        if resolve(src, cls)[1] != class_name:
            continue
        if seen < nth:
            seen += 1
            continue
        kids = [j for j, k in enumerate(src.exports)
                if struct.unpack_from('<i', k['meta'], OUTER_AT)[0] == i + 1]
        return i, kids
    return None, []


def demo(name='PlanBTestLevel'):
    """The skeleton plus one lifted floor actor, as a pipeline exercise."""
    b = Builder(name)
    ua, ux = gamefiles.package(
        '../../../OMD/Content/Maps/Campaign/Scenario_01/Scenario_01_Castle')
    src = Package(ua, ux)
    src.load_payloads()
    actor, _kids = find_actor(src, 'StaticMeshActor')
    if actor is None:
        raise SystemExit('no StaticMeshActor in the donor')
    placed = b.lift_actor(src, actor)
    # somewhere obvious, on the 300uu grid the kit is authored against
    wrote = b.place(placed, location=(900.0, 1200.0, 0.0),
                    rotation=(0.0, 0.0, 0.0))
    print('placed: %s' % (', '.join(wrote) or 'nothing -- no transform property'))
    return b


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--empty', metavar='NAME', help='an empty level of this name')
    ap.add_argument('--demo', action='store_true',
                    help='the skeleton plus one lifted floor actor')
    ap.add_argument('--out', default='.')
    args = ap.parse_args(argv)

    if args.demo:
        b = demo()
    elif args.empty:
        b = Builder(args.empty)
    else:
        ap.error('need --empty NAME or --demo')

    ua, ux = b.write()
    os.makedirs(args.out, exist_ok=True)
    base = os.path.join(args.out, b.name)
    with open(base + '.umap', 'wb') as fh:
        fh.write(ua)
    with open(base + '.uexp', 'wb') as fh:
        fh.write(ux)
    print('wrote %s.umap (%d) and .uexp (%d) -- %d export(s), %d import(s)'
          % (base, len(ua), len(ux), len(b.pkg.exports), len(b.pkg.imports)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())


def world_streams(pkg):
    """(world export, persistent package index, [streaming package index]) for a package.

    Reads the World export's native tail, whose layout `Builder.stream_sublevels`
    documents. Works on a shipped level's own copy as well as an authored skeleton,
    which is what lets a borrowed host drop the sublevels it must not bring along.

    The list is in **streaming order, not export order** -- Scenario_01 lists
    137, 139, 140, 141, 138, 142 -- so it is filtered, never rebuilt from a scan.
    """
    import struct as _struct
    from riftlord.build import apply as _apply
    world = None
    for i in range(len(pkg.exports)):
        if _apply._class_at(pkg, i) == 'World':
            world = i
            break
    if world is None:
        return None, None, []
    data = bytes(pkg.exports[world]['data'])
    _zero, level, _zero2, count = _struct.unpack_from('<4i', data, 8)
    refs = list(_struct.unpack_from('<%di' % count, data, 24))
    return world, level, refs


def stream_asset_of(pkg, package_index):
    """The sublevel package path a streaming export's `WorldAsset` names, or None."""
    import struct as _struct
    from riftlord.build.navigation import navvolinject
    i = package_index - 1
    if not 0 <= i < len(pkg.exports):
        return None
    data = bytes(pkg.exports[i]['data'])
    for name, _kind, start, _end in navvolinject.walk_properties(data, pkg.names):
        if name != 'WorldAsset':
            continue
        ni = _struct.unpack_from('<i', data, start + 25)[0]
        if 0 <= ni < len(pkg.names):
            text = pkg.names[ni][0]
            return text.split('.', 1)[0] if '.' in text else text
    return None


def keep_world_streams(pkg, keep):
    """Drop every streamed sublevel whose package path is not in `keep`. Returns the pair
    (dropped paths, kept paths).

    Removing an entry from the World's list is enough to stop a sublevel loading:
    `UWorld` streams what this array names, and the `LevelStreaming` export left behind
    is then an orphan nothing asks to load. The export is deliberately NOT removed --
    that would shift every later package index and invalidate the preload dependencies
    ([[omd3-package-header-offsets]]).
    """
    import struct as _struct
    world, level, refs = world_streams(pkg)
    if world is None:
        return [], []
    want = {p.rstrip('/') for p in keep}
    kept, dropped = [], []
    for ref in refs:
        path = stream_asset_of(pkg, ref)
        (kept if path in want else dropped).append((ref, path))
    data = bytes(pkg.exports[world]['data'])
    tail = _struct.pack('<4i', 0, level, 0, len(kept))
    tail += b''.join(_struct.pack('<i', ref) for ref, _p in kept)
    pkg.exports[world]['data'] = data[:8] + tail
    pkg.exports[world]['size'] = 8 + len(tail)
    return [p for _r, p in dropped], [p for _r, p in kept]
