import struct, sys, os, collections
from riftlord.formats.ue.parseuasset import R

def parse(path):
    b=open(path,'rb').read()
    r=R(b)
    assert r.u32()==0x9E2A83C1, 'bad magic'
    legacy=r.i32(); ue3=r.i32(); v4=r.i32(); lic=r.i32()
    ncv=r.i32()
    for _ in range(ncv): r.raw(16); r.i32()
    total=r.i32(); folder=r.fstr(); flags=r.u32()
    ncount=r.i32(); noff=r.i32()
    r.i32(); r.i32()                 # gatherable text count/offset
    ecount=r.i32(); eoff=r.i32()
    icount=r.i32(); ioff=r.i32()
    depoff=r.i32()
    nr=R(b,noff); names=[]
    for i in range(ncount):
        s=nr.fstr(); nr.raw(4); names.append(s)
    def fname(rr):
        i=rr.i32(); n=rr.i32()
        s=names[i] if i<len(names) else '?%d'%i
        return s+('_%d'%(n-1) if n>0 else '')
    ir=R(b,ioff); imports=[]
    for _ in range(icount):
        cp=fname(ir); cn=fname(ir); outer=ir.i32(); on=fname(ir)
        imports.append(dict(classpkg=cp, cls=cn, outer=outer, name=on))
    esize=(depoff-eoff)//ecount if ecount else 0
    er=R(b,eoff); exports=[]
    for _ in range(ecount):
        st=er.o
        ci=er.i32(); si=er.i32(); er.i32(); outer=er.i32()
        on=fname(er); of=er.u32(); ssize=er.i64(); soff=er.i64()
        exports.append(dict(classidx=ci, outer=outer, name=on, size=ssize, off=soff))
        er.o=st+esize
    def resolve(idx):
        if idx<0:
            i=-idx-1
            return imports[i]['name'] if i<len(imports) else '?imp%d'%i
        if idx>0:
            return exports[idx-1]['name'] if idx-1<len(exports) else '?exp%d'%idx
        return 'None'
    for e in exports: e['cls']=resolve(e['classidx']); e['outername']=resolve(e['outer'])
    return dict(v4=v4, names=names, imports=imports, exports=exports, esize=esize, isize=(eoff-ioff)//icount if icount else 0)

if __name__=='__main__':
    d=parse(sys.argv[1])
    print('exports',len(d['exports']),'esize',d['esize'],'imports',len(d['imports']),'isize',d['isize'])
    print(collections.Counter(e['cls'] for e in d['exports']).most_common(50))
