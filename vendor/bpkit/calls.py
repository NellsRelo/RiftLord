"""How the Blueprint compiler calls a function (KismetCompilerVMBackend.cpp,
EmitFunctionCall), shared by the logic compiler and its gate.

    final       FUNC_Final, or a call to the parent's version (Super)
    math        final, and static + final + native, and not networked,
                authority-only, cosmetic, on an interface, or with wildcard
                parameters -> CallMath, which takes no context
    local       not native (a Blueprint's own function) -> the Local* forms
    otherwise   FinalFunction (by reference) or VirtualFunction (by name)

A call that is not math and has a target object other than self runs inside
a Context; a static non-math call's context is the function's class default
object.
"""

FUNC_FINAL = 0x1
FUNC_BLUEPRINT_AUTHORITY_ONLY = 0x4
FUNC_BLUEPRINT_COSMETIC = 0x8
FUNC_NET_FUNC_FLAGS = 0x40 | 0x80 | 0x4000 | 0x200000 | 0x1000000      # Net, Reliable, Multicast, Server, Client
FUNC_NET_REQUEST = 0x100
FUNC_NET_RESPONSE = 0x1000
FUNC_NATIVE = 0x400
FUNC_STATIC = 0x2000


def opcode(fn, db, parent_context=False):
    """The opcode the compiler emits to call `fn` (a typedb function entry)."""
    flags = fn.get('function_flags', 0)
    final = bool(flags & FUNC_FINAL) or parent_context
    owner = db['classes'].get(fn['owner'], {}) if fn.get('owner') else {}
    wildcard = any(p.get('wildcard') for p in fn['params'])
    math = (final and flags & (FUNC_STATIC | FUNC_FINAL | FUNC_NATIVE) == (FUNC_STATIC | FUNC_FINAL | FUNC_NATIVE)
            and not flags & (FUNC_NET_FUNC_FLAGS | FUNC_BLUEPRINT_AUTHORITY_ONLY |
                             FUNC_BLUEPRINT_COSMETIC | FUNC_NET_REQUEST | FUNC_NET_RESPONSE)
            and owner.get('kind') != 'interface'
            and not wildcard)
    local = not flags & (FUNC_NATIVE | FUNC_NET_FUNC_FLAGS | FUNC_BLUEPRINT_AUTHORITY_ONLY |
                         FUNC_BLUEPRINT_COSMETIC | FUNC_NET_REQUEST | FUNC_NET_RESPONSE)
    if final:
        if math:
            return 'CallMath'
        return 'LocalFinalFunction' if local else 'FinalFunction'
    return 'LocalVirtualFunction' if local else 'VirtualFunction'
