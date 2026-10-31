import struct, sys

class R:
    def __init__(s,b,o=0): s.b=b; s.o=o
    def i32(s): v=struct.unpack_from('<i',s.b,s.o)[0]; s.o+=4; return v
    def u32(s): v=struct.unpack_from('<I',s.b,s.o)[0]; s.o+=4; return v
    def i64(s): v=struct.unpack_from('<q',s.b,s.o)[0]; s.o+=8; return v
    def u8(s):  v=s.b[s.o]; s.o+=1; return v
    def f32(s): v=struct.unpack_from('<f',s.b,s.o)[0]; s.o+=4; return v
    def raw(s,n): v=s.b[s.o:s.o+n]; s.o+=n; return v
    def fstr(s):
        n=s.i32()
        if n==0: return ''
        if n<0: return s.raw(-n*2).decode('utf-16-le').rstrip('\0')
        return s.raw(n).decode('utf-8','replace').rstrip('\0')

def names_of(uasset):
    b=open(uasset,'rb').read()
    r=R(b)
    assert r.u32()==0x9E2A83C1
    legacy=r.i32(); r.i32(); ue4=r.i32(); lic=r.i32()
    ncv=r.i32()
    for _ in range(ncv): r.raw(16); r.i32()
    total=r.i32(); folder=r.fstr(); flags=r.u32()
    ncount=r.i32(); noff=r.i32()
    nr=R(b,noff); out=[]
    for i in range(ncount):
        s=nr.fstr(); nr.raw(4)   # two uint16 hashes
        out.append(s)
    return out

def parse_props(r, names, depth=0):
    res=[]
    while True:
        ni=r.i32(); r.i32()
        nm=names[ni] if ni<len(names) else '?%d'%ni
        if nm=='None': break
        ti=r.i32(); r.i32()
        tp=names[ti] if ti<len(names) else '?%d'%ti
        size=r.i32(); idx=r.i32()
        if tp=='ArrayProperty':
            ii=r.i32(); r.i32(); inner=names[ii]
            r.u8()
            end=r.o+size
            n=r.i32()
            items=[]
            if inner=='StructProperty':
                # inner tag
                r.i32(); r.i32(); r.i32(); r.i32(); r.i32(); r.i32()
                si=r.i32(); r.i32(); sname=names[si]
                r.raw(16+1)
                for k in range(n):
                    items.append(parse_props(r,names,depth+1))
            res.append((nm,tp,n,items)); r.o=end
        elif tp=='StructProperty':
            si=r.i32(); r.i32(); sname=names[si]
            r.raw(16+1)
            end=r.o+size
            if sname=='LinearColor':
                v=[r.f32() for _ in range(4)]; res.append((nm,sname,v))
            elif sname=='Guid':
                res.append((nm,sname,r.raw(16).hex()))
            else:
                res.append((nm,sname,'<%d bytes>'%size))
            r.o=end
        elif tp=='FloatProperty':
            r.u8(); res.append((nm,tp,r.f32()))
        elif tp=='NameProperty':
            r.u8(); i2=r.i32(); r.i32(); res.append((nm,tp,names[i2]))
        elif tp=='BoolProperty':
            v=r.u8(); r.u8(); res.append((nm,tp,bool(v)))
        else:
            r.u8(); res.append((nm,tp,r.raw(size).hex())); 
    return res

if __name__=='__main__':
    ua=sys.argv[1]; ux=sys.argv[2]
    names=names_of(ua)
    b=open(ux,'rb').read()
    r=R(b)
    props=parse_props(r,names)
    import pprint; pprint.pprint(props)
