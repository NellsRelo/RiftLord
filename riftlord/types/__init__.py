"""bpkit's type data, built on the user's machine from the user's own installs.

    exe        the game's native reflection, read from OMD-Win64-Shipping.exe
    engine     the Engine and CoreUObject declarations, read from the user's UE 4.27 headers
    blueprints the game's Blueprint classes, structs and functions, read from its paks
    merge      the three as one type database in the form bpkit.typedb.load() reads
    setup      the caches, their keys, the steps and bpkit's paths pointed at them
"""
