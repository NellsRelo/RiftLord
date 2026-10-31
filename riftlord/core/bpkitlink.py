"""bpkit, the Blueprint toolkit, imported from Rift Lord's own frozen copy (vendor/bpkit).

Source runs load the `bpkit` package from VENDORED; the frozen app carries the same copy
(tools/build.py). The copy changes only through tools/vendor_bpkit.py, never by following
another bpkit folder. bpkit imports some of the converter's modules by their top-level
names (ALIASES); those resolve to Rift Lord's own modules. bpkit's type caches are the ones
Setup built in the user's cache (riftlord.types.setup.point, on the POINTED modules as they
load).
"""

import importlib
import importlib.abc
import importlib.util
import os
import sys

#: The vendored bpkit package folder, in this tree (vendor/bpkit).
VENDORED = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), 'vendor', 'bpkit')
#: Top-level names bpkit imports, and the Rift Lord module each one is.
ALIASES = {'gamefiles': 'riftlord.core.gamefiles', 'gamepath': 'riftlord.core.gamepath',
           'kismet': 'riftlord.formats.ue.kismet', 'pakv4': 'riftlord.formats.ue.pakv4',
           'pkgcheck': 'riftlord.core.pkgcheck', 'usmap': 'riftlord.formats.ue.usmap'}


#: bpkit modules whose cache paths riftlord.types.setup.point sets as they load.
POINTED = ('bpkit.typedb', 'bpkit.replication', 'bpkit.structsize')


class _Pointing(importlib.abc.Loader):
    """A bpkit module's own loader, then riftlord.types.setup.point on the module."""

    def __init__(self, loader):
        self.loader = loader

    def create_module(self, spec):
        return self.loader.create_module(spec)

    def exec_module(self, module):
        self.loader.exec_module(module)
        from riftlord.types import setup
        setup.point(module)


class _Finder(importlib.abc.MetaPathFinder):
    """Finds `bpkit` in VENDORED (source runs) and puts ALIASES in place first; the
    POINTED modules load through _Pointing."""

    def find_spec(self, name, path=None, target=None):
        if name in POINTED:
            for finder in sys.meta_path:
                if finder is self or not hasattr(finder, 'find_spec'):
                    continue
                spec = finder.find_spec(name, path, target)
                if spec is not None:
                    if spec.loader is not None:
                        spec.loader = _Pointing(spec.loader)
                    return spec
            return None
        if name != 'bpkit':
            return None
        for alias, real in ALIASES.items():
            if alias not in sys.modules:
                sys.modules[alias] = importlib.import_module(real)
        if getattr(sys, 'frozen', False):
            return None
        init = os.path.join(VENDORED, '__init__.py')
        if not os.path.isfile(init):
            raise ImportError('bpkit is not at %s (tools/vendor_bpkit.py)' % VENDORED)
        return importlib.util.spec_from_file_location('bpkit', init,
                                                      submodule_search_locations=[VENDORED])


def install():
    """Put the finder first on sys.meta_path (once)."""
    if not any(isinstance(f, _Finder) for f in sys.meta_path):
        sys.meta_path.insert(0, _Finder())
