"""Build a cooked package from a description, deriving what the cooker derives.

A caller says WHAT is in the package -- which objects it imports, which it
exports, what each export's payload is, which objects each export needs loaded
first -- and refers to objects by handle, never by table position. This module
decides every position the way SavePackage.cpp does, then hands the result to
`bpkit.package` to be laid out:

* names: the set of every name used, sorted by FName::Compare, which is a
  case-insensitive ASCII comparison (FObjectNameSortHelper, StricmpImpl).
  Spellings differing only in case are separate entries that compare equal,
  so their relative order is arbitrary and can be pinned with `tie_order`.
* imports: sorted by full name, "Class /Path.Object", compared the same way
  (FObjectImportSortHelper)
* exports: in the order given. A cook re-sorts them for seek-free loading by
  following serialisation references, but the event-driven loader orders work
  by the preload dependencies, not by table position, so any order whose
  dependencies are declared loads.

Names are given as the text the engine displays. Like FName(TEXT("...")),
a trailing "_N" becomes the instance number, so "Wall_1_1" is stored as the
entry "Wall_1" with number 2; pass `Name(text, number)` to say it exactly.

A payload is a list of fragments, so that FNames and object references inside
it are resolved only once the tables are final:

    [b'raw bytes', Name('Foo'), Ref(handle), ...]

`Name` serialises as (index, number), 8 bytes; `Ref` as an FPackageIndex, 4.
"""

import struct

from bpkit.package import Export, FString, Import, Package, name_hashes


class Name:
    """An FName given exactly: entry text plus the stored number (0 = none)."""

    __slots__ = ('text', 'number')

    def __init__(self, text, number=0):
        self.text, self.number = text, number

    def display(self):
        return self.text if self.number == 0 else '%s_%d' % (self.text, self.number - 1)

    def __eq__(self, other):
        return isinstance(other, Name) and (self.text, self.number) == (other.text, other.number)

    def __hash__(self):
        return hash((self.text, self.number))

    def __repr__(self):
        return 'Name(%r)' % self.display()


def split_name(text):
    """FName's parse of display text into (entry, number): UnrealNames.cpp ParseNumber.

    A trailing run of up to ten digits after an underscore is the number, plus
    one, unless it has more than one digit and starts with 0 ("Rocket_04").
    """
    if isinstance(text, Name):
        return text.text, text.number
    digits = len(text) - len(text.rstrip('0123456789'))
    if 0 < digits < len(text) and digits <= 10 and text[-digits - 1] == '_' \
            and (digits == 1 or text[-digits] != '0'):
        value = int(text[-digits:])
        if value < 2 ** 31 - 1:
            return text[:-digits - 1], value + 1
    return text, 0


def display(text):
    return text.display() if isinstance(text, Name) else text


class Ref:
    """An object reference inside a payload; a None target is the null reference."""

    __slots__ = ('target',)

    def __init__(self, target):
        self.target = target

    def __eq__(self, other):
        return isinstance(other, Ref) and self.target == other.target

    def __hash__(self):
        return hash(self.target)

    def __repr__(self):
        return 'Ref(%r)' % (self.target,)


class ImportRef:
    __slots__ = ('class_package', 'class_name', 'outer', 'name')

    def __init__(self, class_package, class_name, outer, name):
        self.class_package, self.class_name = class_package, class_name
        self.outer, self.name = outer, name

    def key(self):
        return (display(self.class_package), display(self.class_name), id(self.outer),
                display(self.name))


class ExportRef:
    __slots__ = ('name', 'klass', 'super', 'template', 'outer', 'fields', 'data', 'preload')

    def __init__(self, name):
        self.name = name
        self.klass = self.super = self.template = self.outer = None
        self.fields = {}
        self.data = []
        self.preload = {k: [] for k in Export.KINDS}


def name_key(text):
    """FName::Compare order: ASCII lowercased, non-ASCII by code unit."""
    return ''.join(c.lower() if c < '\x80' else c for c in text)


class Builder:
    """Collects a package's contents and produces a `bpkit.package.Package`."""

    def __init__(self, summary=None):
        self.imports = []
        self.exports = []
        self.extra_names = set()
        self.tie_order = None    # {entry: rank} among names the cooker calls equal
        self.summary = summary
        self._by_key = {}

    # -- declaring contents -----------------------------------------------
    def import_object(self, class_package, class_name, name, outer=None):
        """An imported object, created once per identity. Returns its handle."""
        ref = ImportRef(class_package, class_name, outer, name)
        existing = self._by_key.get(ref.key())
        if existing is not None:
            return existing
        self._by_key[ref.key()] = ref
        self.imports.append(ref)
        return ref

    def import_package(self, path):
        """A /Script or /Game package, as an import of class Package."""
        return self.import_object('/Script/CoreUObject', 'Package', path)

    def export_object(self, name, klass, outer=None, **fields):
        """An exported object, appended in table order. Returns its handle.

        `fields` sets any of `bpkit.package.Export`'s plain attributes, such as
        object_flags or is_asset; `super`/`template` are handles like `klass`.
        """
        ref = ExportRef(name)
        ref.klass, ref.outer = klass, outer
        ref.super = fields.pop('super', None)
        ref.template = fields.pop('template', None)
        ref.fields = fields
        self.exports.append(ref)
        return ref

    def name(self, text):
        """Register a name a payload uses that nothing else declares."""
        self.extra_names.add(split_name(text)[0])

    # -- deriving the tables ----------------------------------------------
    @staticmethod
    def _path(ref):
        """An import's path as GetPathName gives it.

        The separator before an object is ':' when its outer is a top-level
        object that is not a package (a subobject of an asset), else '.'.
        """
        chain = []
        while ref is not None:
            chain.append(ref)
            ref = ref.outer
        chain.reverse()
        out = display(chain[0].name)
        for i in range(1, len(chain)):
            subobject = (i >= 2 and display(chain[i - 1].class_name) != 'Package'
                         and display(chain[i - 2].class_name) == 'Package')
            out += (':' if subobject else '.') + display(chain[i].name)
        return out

    def full_name(self, ref):
        return '%s %s' % (display(ref.class_name), self._path(ref))

    def _names(self):
        used = set(self.extra_names)
        for imp in self.imports:
            for n in (imp.class_package, imp.class_name, imp.name):
                used.add(split_name(n)[0])
        for exp in self.exports:
            used.add(split_name(exp.name)[0])
            for frag in exp.data:
                if isinstance(frag, Name):
                    used.add(frag.text)
        used.add('None')
        # Spellings that differ only in case are separate entries, and the
        # cooker's comparison calls them equal, so their order is whatever its
        # sort left. `tie_order` fixes it; otherwise they fall back to text order.
        rank = self.tie_order or {}
        return sorted(used, key=lambda n: (name_key(n), rank.get(n, 0), n))

    def build(self):
        """The finished `Package`. Every table position is decided here."""
        names = self._names()
        index_of = {n: i for i, n in enumerate(names)}

        def fname(text):
            entry, number = split_name(text)
            return (index_of[entry], number)

        imports = sorted(self.imports, key=lambda r: name_key(self.full_name(r)))
        position = {}
        for i, r in enumerate(imports):
            position[id(r)] = -(i + 1)
        for i, r in enumerate(self.exports):
            position[id(r)] = i + 1

        def index(ref):
            return 0 if ref is None else position[id(ref)]

        p = Package()
        if self.summary is not None:
            p.summary = self.summary
        p.names = [FString(n) for n in names]
        p.name_hashes = [name_hashes(n) for n in names]
        for r in imports:
            p.imports.append(Import(fname(r.class_package), fname(r.class_name),
                                    index(r.outer), fname(r.name)))
        for r in self.exports:
            e = Export()
            e.class_index, e.super_index = index(r.klass), index(r.super)
            e.template_index, e.outer_index = index(r.template), index(r.outer)
            e.object_name = fname(r.name)
            for k, v in r.fields.items():
                setattr(e, k, v)
            e.preload = {k: [index(t) for t in v] for k, v in r.preload.items()}
            out = bytearray()
            for frag in r.data:
                if isinstance(frag, Name):
                    out += struct.pack('<ii', index_of[frag.text], frag.number)
                elif isinstance(frag, Ref):
                    out += struct.pack('<i', index(frag.target))
                else:
                    out += frag
            e.data = bytes(out)
            p.exports.append(e)
        return p
