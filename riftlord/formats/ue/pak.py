import struct, os, sys, io, threading, zlib

MAGIC = 0x5A6F12E1

try:
    import lz4.block as _lz4_wheel
except ImportError:
    _lz4_wheel = None

_LIBLZ4 = []


def _liblz4():
    """The system liblz4 (ctypes), or None."""
    if not _LIBLZ4:
        import ctypes
        import ctypes.util
        lib = None
        for cand in [ctypes.util.find_library('lz4'), 'liblz4.so.1', 'liblz4.dylib', 'lz4.dll']:
            if not cand:
                continue
            try:
                lib = ctypes.CDLL(cand)
                lib.LZ4_decompress_safe.restype = ctypes.c_int
                lib.LZ4_decompress_safe.argtypes = (ctypes.c_char_p, ctypes.c_char_p,
                                                    ctypes.c_int, ctypes.c_int)
                break
            except (OSError, AttributeError):
                lib = None
        _LIBLZ4.append(lib)
    return _LIBLZ4[0]


def _lz4(chunk, expect):
    """Decompress one LZ4 block, preferring the wheel and falling back to ours.

    `expect` is the size this particular block must decode to, which is the block
    size for every block but the last. Passing the nominal block size for all of
    them makes the final short block look corrupt.
    """
    lib = _liblz4() if _lz4_wheel is None and expect else None
    if _lz4_wheel is not None:
        out = _lz4_wheel.decompress(chunk, uncompressed_size=max(expect, 1))
    elif lib is not None:
        import ctypes
        buf = ctypes.create_string_buffer(expect)
        got = lib.LZ4_decompress_safe(bytes(chunk), buf, len(chunk), expect)
        out = buf.raw[:max(got, 0)]
    else:
        from riftlord.formats.ue import lz4block
        out = lz4block.decompress(chunk)
    if expect and len(out) != expect:
        raise RuntimeError('LZ4 block decoded to %d bytes, expected %d'
                           % (len(out), expect))
    return out


class R:
    def __init__(self, b): self.b=b; self.o=0
    def u8(self):  v=self.b[self.o]; self.o+=1; return v
    def i32(self): v=struct.unpack_from('<i',self.b,self.o)[0]; self.o+=4; return v
    def u32(self): v=struct.unpack_from('<I',self.b,self.o)[0]; self.o+=4; return v
    def i64(self): v=struct.unpack_from('<q',self.b,self.o)[0]; self.o+=8; return v
    def u64(self): v=struct.unpack_from('<Q',self.b,self.o)[0]; self.o+=8; return v
    def raw(self,n): v=self.b[self.o:self.o+n]; self.o+=n; return v
    def fstr(self):
        n=self.i32()
        if n==0: return ''
        if n<0:
            d=self.raw(-n*2); return d.decode('utf-16-le').rstrip('\0')
        d=self.raw(n); return d.decode('utf-8','replace').rstrip('\0')

class Pak:
    def __init__(self, path):
        self.path=path
        self.f=open(path,'rb')
        # One file handle serves every caller, and reading an entry is a seek followed by
        # a read. Two threads interleaving those give each other's bytes: with eight
        # workers this surfaced as `LZ4Error: match offset 0 is outside the output`, and
        # an uncompressed entry would have come back silently wrong instead.
        self.lock=threading.Lock()
        sz=os.path.getsize(path)
        self.f.seek(max(0,sz-400)); tail=self.f.read(400)
        i=tail.rfind(struct.pack('<I',MAGIC))
        if i<0: raise RuntimeError('no magic '+path)
        base=(sz-400)+i
        self.encrypted = tail[i-1]!=0
        r=R(tail[i:])
        r.u32()
        self.version=r.u32()
        self.index_off=r.i64(); self.index_size=r.i64(); r.raw(20)
        self.methods=['None']
        for k in range(5):
            nm=r.raw(32)
            if r.o>len(tail[i:]): break
            nm=nm.split(b'\0')[0].decode('ascii','replace')
            self.methods.append(nm)
        self.files={}
        self._parse_index()

    def _parse_index(self):
        self.f.seek(self.index_off); idx=self.f.read(self.index_size)
        r=R(idx)
        self.mount=r.fstr()
        self.num=r.i32()
        if self.version>=10:
            r.u64() # path hash seed
            has_ph=r.i32()
            if has_ph: self.ph_off=r.i64(); self.ph_size=r.i64(); r.raw(20)
            has_fd=r.i32()
            if not has_fd: self.fd_off=None
            else: self.fd_off=r.i64(); self.fd_size=r.i64(); r.raw(20)
            n=r.i32(); self.encoded=r.raw(n)
            # full directory index
            self.f.seek(self.fd_off); fd=self.f.read(self.fd_size)
            fr=R(fd)
            ndir=fr.i32()
            for _ in range(ndir):
                d=fr.fstr(); nf=fr.i32()
                for __ in range(nf):
                    fn=fr.fstr(); off=fr.i32()
                    self.files[self.mount+d+fn]=off
        else:
            self._parse_legacy_index(r)

    #: The compression enum before v8 named methods by number rather than by a
    #: table in the footer. 0 is uncompressed, which is what mod paks use.
    LEGACY_METHODS = {0: 'None', 1: 'Zlib', 2: 'Gzip', 3: 'Custom'}

    def _read_entry(self, r):
        """One FPakEntry, as every pak version before 10 writes it.

        Used for both the index copy and the copy stored inline ahead of each
        file's data, which is how the header's length is known.
        """
        start = r.o
        offset = r.i64()
        size = r.i64()
        usize = r.i64()
        method = r.i32()
        if self.version <= 1:
            r.i64()                              # timestamp, dropped after v1
        r.raw(20)                                # hash
        blocks, enc = [], False
        if self.version >= 3:
            if method != 0:
                for _ in range(r.i32()):
                    blocks.append((r.i64(), r.i64()))
            enc = bool(r.u8())
            r.u32()                              # compression block size
        return dict(offset=offset, size=size, usize=usize, method=method,
                    enc=enc, blocks=blocks, header=r.o - start)

    def _parse_legacy_index(self, r):
        """v3..v9: a flat list of name-then-entry, no path hash index."""
        for _ in range(self.num):
            name = r.fstr()
            self.files[self.mount + name] = self._read_entry(r)

    def entry(self, encoded_off):
        """Decode one entry of a v9+ pak's encoded index.

        The bit layout, from UE4's own DecodePakEntry, and the part that is easy
        to get wrong: the LOW six bits are the compression BLOCK SIZE, not a
        block count. The count is bits 6..21. When the low six bits read 0x3f the
        block size is spelled out in a uint32 that comes before the offset, so
        reading the offset straight after the flags desynchronises the whole
        entry for any file that uses one.

            bit 31    offset fits in 32 bits
            bit 30    uncompressed size fits in 32 bits
            bit 29    size fits in 32 bits
            bits 23-28  compression method index
            bit 22    encrypted
            bits 6-21   number of compression blocks
            bits 0-5    block size >> 11, or 0x3f meaning "a uint32 follows"

        Block offsets are reconstructed as absolute positions in the file,
        starting after the entry's own inline header.
        """
        b = self.encoded
        o = encoded_off
        flags = struct.unpack_from('<I', b, o)[0]
        o += 4
        block_size = (flags & 0x3f) << 11
        if (flags & 0x3f) == 0x3f:
            block_size = struct.unpack_from('<I', b, o)[0]
            o += 4
        nblocks = (flags >> 6) & 0xffff
        enc = bool(flags & (1 << 22))
        method = (flags >> 23) & 0x3f

        def rd(bit32):
            nonlocal o
            if bit32:
                v = struct.unpack_from('<I', b, o)[0]
                o += 4
            else:
                v = struct.unpack_from('<Q', b, o)[0]
                o += 8
            return v

        offset = rd(flags & (1 << 31))
        usize = rd(flags & (1 << 30))
        size = rd(flags & (1 << 29)) if method != 0 else usize

        header = 8 + 8 + 8 + 4 + 20 + 1 + 4
        if method != 0:
            header += 4 + nblocks * 16
        blocks = []
        if nblocks:
            at = offset + header
            if nblocks == 1 and not enc:
                blocks = [(at, at + size)]
            else:
                for _ in range(nblocks):
                    n = struct.unpack_from('<I', b, o)[0]
                    o += 4
                    blocks.append((at, at + n))
                    at += (n + 15) // 16 * 16 if enc else n
        return dict(offset=offset, size=size, usize=usize, method=method,
                    enc=enc, blocks=blocks, blockcount=nblocks,
                    header=header, block_size=block_size)

    def read_file(self, name):
        with self.lock:
            return self._read_file(name)

    def _read_file(self, name):
        got = self.files[name]
        if isinstance(got, dict):
            return self._read_legacy(got)
        e = self.entry(got)
        if e['method'] == 0:
            self.f.seek(e['offset'] + e['header'])
            return self.f.read(e['usize'])
        method = self.methods[e['method']] if e['method'] < len(self.methods) else '?'
        out, have = [], 0
        for start, end in e['blocks']:
            self.f.seek(start)
            chunk = self.f.read(end - start)
            if method == 'Zlib':
                out.append(zlib.decompress(chunk))
            elif method == 'LZ4':
                out.append(_lz4(chunk, min(e['block_size'] or 65536, e['usize'] - have)))
            else:
                raise RuntimeError('compression method %r' % method)
            have += len(out[-1])
        return b''.join(out)[:e['usize']]

    def _read_legacy(self, e):
        """Read one file out of a pre-v10 pak."""
        self.f.seek(e['offset'])
        head = R(self.f.read(64 + 16 * (len(e['blocks']) or 1) + 4096))
        inline = self._read_entry(head)          # the copy stored with the data
        body = e['offset'] + inline['header']
        if e['method'] == 0:
            self.f.seek(body)
            return self.f.read(e['usize'])
        name = self.LEGACY_METHODS.get(e['method'], '?') if self.version < 8 \
            else (self.methods[e['method']] if e['method'] < len(self.methods)
                  else '?')
        # From v5 a block's offsets are relative to the entry; before that they
        # are absolute positions in the file.
        base = e['offset'] if self.version >= 5 else 0
        out, have = [], 0
        for start, end in e['blocks']:
            self.f.seek(base + start)
            chunk = self.f.read(end - start)
            if name == 'Zlib':
                out.append(zlib.decompress(chunk))
            elif name == 'LZ4':
                out.append(_lz4(chunk, min(e['block_size'] or 65536, e['usize'] - have)))
            else:
                raise RuntimeError('compression method %r' % name)
            have += len(out[-1])
        return b''.join(out)[:e['usize']]


if __name__=='__main__':
    p=Pak(sys.argv[1])
    print(p.path, 'v%d'%p.version, 'enc',p.encrypted, 'mount',p.mount, 'files',len(p.files), p.methods[:3])
