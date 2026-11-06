"""Rift Lord: `python -m riftlord`, the `riftlord` command, and the frozen server's entry.

`--lightbake ARGS...` runs the light baker (lightbaker.main) instead of the server:
riftlord.build.lighting.lightbake starts the frozen server that way to bake in its own
process. `--fab ARGS...` runs the Fab converter (riftlord.fab) the same way, for the Fab
Kits tab (riftlord.app.fabkits). `--terrain ARGS...` runs the terrain kit builder
(riftlord.terrainkit) for Setup. `--types ARGS...` runs Setup (riftlord.types.setup).
"""

import sys


def lightbake(args):
    """lightbaker.main(args), with the baker's native packages unpacked from the bundle
    (riftlord.lightbake.frozen_packages) and Rift Lord's cache folders. Off Linux
    moderngl has no EGL, so the OpenGL fallback's context is the platform's default one
    (WGL on Windows)."""
    from riftlord.core import gamepath
    gamepath.APP_NAME = 'RiftLord'           # the server's caches (workshop/__main__.py)
    from riftlord.build.lighting import lightbake
    lightbake.frozen_packages()
    if not sys.platform.startswith('linux'):
        try:
            import moderngl
        except ImportError:
            moderngl = None
        if moderngl is not None:
            standalone = moderngl.create_standalone_context

            def platform_context(*a, **kw):
                if kw.get('backend') == 'egl':
                    del kw['backend']
                return standalone(*a, **kw)
            moderngl.create_standalone_context = platform_context
    from riftlord.build.lighting import lightbaker
    lightbaker.main(args)
    return 0


def fab(args):
    """The Fab converter's command line (riftlord.fab), with Rift Lord's cache folders:
    riftlord.app.fabkits starts the frozen server this way."""
    from riftlord.core import gamepath
    gamepath.APP_NAME = 'RiftLord'
    from riftlord.app import fabkits
    return fabkits.frozen_main(args)


def terrain(args):
    """The terrain kit builder's command line (riftlord.terrainkit), with Rift Lord's cache
    folders: riftlord.terrainkit.setup starts the frozen server this way."""
    from riftlord.core import gamepath
    gamepath.APP_NAME = 'RiftLord'
    from riftlord.terrainkit.__main__ import main
    return main(list(args))


def types(args):
    """Setup's command line (riftlord.types.setup): riftlord.types.setup.start runs the
    frozen server this way."""
    from riftlord.core import gamepath
    gamepath.APP_NAME = 'RiftLord'
    from riftlord.types import setup
    return setup.main(list(args))


def main(argv=None):
    """The app (riftlord.app), the light baker with --lightbake, the Fab converter with --fab,
    the terrain kit builder with --terrain, Setup with --types."""
    if getattr(sys, 'frozen', False):
        # a frozen worker process (the Fab converter's pool) runs its task here and exits
        import multiprocessing
        multiprocessing.freeze_support()
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ['--lightbake']:
        return lightbake(argv[1:])
    if argv[:1] == ['--fab']:
        return fab(argv[1:])
    if argv[:1] == ['--terrain']:
        return terrain(argv[1:])
    if argv[:1] == ['--types']:
        return types(argv[1:])
    #: SIGUSR1 prints every thread's stack to stderr (server.log): where a server that stopped
    #: answering is stuck, with no debugger (ptrace is off)
    import faulthandler
    import signal
    if hasattr(signal, 'SIGUSR1'):
        faulthandler.register(signal.SIGUSR1, all_threads=True)
    from riftlord.app.__main__ import main as app_main
    return app_main(argv)


if __name__ == '__main__':
    raise SystemExit(main())
