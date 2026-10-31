import sys, struct, zlib, os
from riftlord.formats.ue.pak import Pak, R, _lz4

def decode(b, o):
    v = struct.unpack_from('<I', b, o)[0]; o += 4
    method = (v >> 23) & 0x3f
    def rd(bit):
        nonlocal o
        if v & bit: r = struct.unpack_from('<I', b, o)[0]; o += 4
        else:       r = struct.unpack_from('<Q', b, o)[0]; o += 8
        return r
    if (v & 0x3f) == 0x3f:
        bsize = struct.unpack_from('<I', b, o)[0]; o += 4
    else:
        bsize = (v & 0x3f) << 11
    off   = rd(1 << 31)
    usize = rd(1 << 30)
    size  = rd(1 << 29) if method != 0 else usize
    return off, size, usize, method

def lz4d(src, maxout=0):
    out = bytearray(); i = 0; n = len(src)
    while i < n:
        tok = src[i]; i += 1
        ll = tok >> 4
        if ll == 15:
            while True:
                b = src[i]; i += 1; ll += b
                if b != 255: break
        out += src[i:i+ll]; i += ll
        if i >= n: break
        offset = src[i] | (src[i+1] << 8); i += 2
        ml = tok & 0xf
        if ml == 15:
            while True:
                b = src[i]; i += 1; ml += b
                if b != 255: break
        ml += 4
        st = len(out) - offset
        for k in range(ml): out.append(out[st + k])
    return bytes(out)

def read(pk, name):
    off, size, usize, method = decode(pk.encoded, pk.files[name])
    hlen = 4096
    while True:
        pk.f.seek(off); hdr = pk.f.read(hlen)
        try:
            hr = R(hdr)
            hr.u64(); dsize = hr.u64(); dusize = hr.u64()
            m = hr.u32(); hr.raw(20)
            blocks = []
            if m != 0:
                nb = hr.i32()
                if nb * 16 + 60 > hlen: raise IndexError
                for _ in range(nb):
                    s = hr.u64(); e = hr.u64(); blocks.append((s, e))
            hr.u8(); bsize = hr.u32(); hsize = hr.o
            break
        except Exception:
            hlen *= 8
            if hlen > 1 << 26: raise
    if m == 0:
        pk.f.seek(off + hsize); return pk.f.read(dusize)
    nm = pk.methods[m]; out = []; have = 0
    for (s, e) in blocks:
        pk.f.seek(off + s); d = pk.f.read(e - s)
        if nm == 'Zlib':
            out.append(zlib.decompress(d))
        else:
            try:
                out.append(_lz4(d, min(bsize, dusize - have)))
            except RuntimeError:
                out.append(lz4d(d, bsize))
        have += len(out[-1])
    return b''.join(out)[:dusize]
