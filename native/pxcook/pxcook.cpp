// Offline PhysX 3.4 cooker with UE 4.27's FPhysXCooking parameters.
//   pxcook convex  IN OUT [inflate]   IN = float32 xyz triples
//   pxcook trimesh IN OUT [flip]      IN = u32 nverts, u32 ntris, nverts*3 f32, ntris*3 u32[, ntris u16 materials]
//   pxcook hull    IN OUT              IN = a convex stream from `pxcook convex`; OUT = the hull PhysX
//                                     loads from it: u32 nverts, f32 xyz..., u32 npolys, per polygon
//                                     u32 n then n u32 vertex indices (what Recast's nav export walks)
// OUT receives the raw PhysX cooked stream (what UE appends after its own header).
//
// Built on the user's machine against their own UE 4.27 (riftlord/formats/omd3/omd3physx.py
// build_tool): Linux links the source tree's static PhysX libraries, Windows the install's
// PhysX import libraries (its DLLs on PATH at run time). Its own allocator, error callback
// and memory input stand in for PhysX's defaults (the same bytes; they live in the static
// Extensions library, which a Windows install's import libraries do not cover).
#include <PxPhysicsAPI.h>
#include <cooking/PxCooking.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#ifdef _WIN32
#include <malloc.h>
#endif
using namespace physx;

static const float TOLERANCE_LENGTH = 100.f;   // p.ToleranceScale_Length
static const float TOLERANCE_SPEED = 1000.f;   // p.ToleranceScale_Speed
static const float MESH_WELD_TOLERANCE = 0.1f; // FPhysXCooking: weld to 1mm

struct Out : PxOutputStream {
    std::vector<unsigned char> b;
    PxU32 write(const void* src, PxU32 n) override {
        const unsigned char* p = (const unsigned char*)src; b.insert(b.end(), p, p + n); return n; }
};

// A memory input (PxDefaultMemoryInputData's job).
struct In : PxInputData {
    const unsigned char* d; PxU32 n, at = 0;
    In(const unsigned char* data, PxU32 size) : d(data), n(size) {}
    PxU32 read(void* dest, PxU32 count) override {
        PxU32 k = count < n - at ? count : n - at; memcpy(dest, d + at, k); at += k; return k; }
    PxU32 getLength() const override { return n; }
    void seek(PxU32 offset) override { at = offset < n ? offset : n; }
    PxU32 tell() const override { return at; }
};

// 16-byte aligned allocations, as PhysX requires (PxDefaultAllocator's job).
struct Alloc : PxAllocatorCallback {
    void* allocate(size_t size, const char*, const char*, int) override {
#ifdef _WIN32
        return _aligned_malloc(size, 16);
#else
        void* p = nullptr; return posix_memalign(&p, 16, size) ? nullptr : p;
#endif
    }
    void deallocate(void* ptr) override {
#ifdef _WIN32
        _aligned_free(ptr);
#else
        free(ptr);
#endif
    }
};

// Errors to stderr (PxDefaultErrorCallback's job).
struct Err : PxErrorCallback {
    void reportError(PxErrorCode::Enum code, const char* message, const char* file, int line) override {
        fprintf(stderr, "physx %d: %s (%s:%d)\n", (int)code, message, file, line); }
};

static Err gErr;
static Alloc gAlloc;

static std::vector<unsigned char> slurp(const char* path) {
    std::vector<unsigned char> v; FILE* f = fopen(path, "rb"); if (!f) return v;
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET); v.resize(n);
    if (fread(v.data(), 1, n, f) != (size_t)n) v.clear(); fclose(f); return v; }

int main(int argc, char** argv) {
    if (argc < 4) { fprintf(stderr, "usage: pxcook convex|trimesh|hull IN OUT [inflate|flip]\n"); return 2; }
    PxFoundation* fnd = PxCreateFoundation(PX_FOUNDATION_VERSION, gAlloc, gErr);
    if (!fnd) { fprintf(stderr, "no foundation\n"); return 3; }
    PxTolerancesScale scale; scale.length = TOLERANCE_LENGTH; scale.speed = TOLERANCE_SPEED;
    PxCookingParams params(scale);
    params.meshWeldTolerance = MESH_WELD_TOLERANCE;
    params.meshPreprocessParams = PxMeshPreprocessingFlags(PxMeshPreprocessingFlag::eWELD_VERTICES);
    params.targetPlatform = PxPlatform::ePC;
    params.midphaseDesc = PxMeshMidPhase::eBVH33;
    PxCooking* cook = PxCreateCooking(PX_PHYSICS_VERSION, *fnd, params);
    if (!cook) { fprintf(stderr, "no cooking\n"); return 3; }
    std::vector<unsigned char> in = slurp(argv[2]);
    Out out; bool ok = false;
    bool extra = argc > 4;
    if (!strcmp(argv[1], "hull")) {
        PxPhysics* phys = PxCreatePhysics(PX_PHYSICS_VERSION, *fnd, PxTolerancesScale());
        In stream(in.data(), (PxU32)in.size());
        PxConvexMesh* m = phys ? phys->createConvexMesh(stream) : nullptr;
        if (!m) { fprintf(stderr, "hull load failed\n"); return 1; }
        PxU32 nv = m->getNbVertices(), np = m->getNbPolygons();
        const PxVec3* v = m->getVertices(); const PxU8* ib = m->getIndexBuffer();
        out.write(&nv, 4); out.write(v, nv * 12); out.write(&np, 4);
        for (PxU32 i = 0; i < np; ++i) {
            PxHullPolygon poly; m->getPolygonData(i, poly);
            PxU32 n = poly.mNbVerts; out.write(&n, 4);
            for (PxU32 j = 0; j < n; ++j) { PxU32 k = ib[poly.mIndexBase + j]; out.write(&k, 4); }
        }
        ok = true;
    } else if (!strcmp(argv[1], "convex")) {
        PxConvexMeshDesc d;
        d.points.data = in.data(); d.points.count = in.size() / 12; d.points.stride = 12;
        d.flags = PxConvexFlag::eCOMPUTE_CONVEX | PxConvexFlag::eSHIFT_VERTICES;
        if (extra) d.flags |= PxConvexFlag::eINFLATE_CONVEX;
        ok = cook->cookConvexMesh(d, out);
        if (!ok && !extra) { d.flags |= PxConvexFlag::eINFLATE_CONVEX; out.b.clear(); ok = cook->cookConvexMesh(d, out); if (ok) fprintf(stderr, "inflated\n"); }
    } else {
        PxU32 nv, nt; memcpy(&nv, in.data(), 4); memcpy(&nt, in.data() + 4, 4);
        PxTriangleMeshDesc d;
        d.points.data = in.data() + 8; d.points.count = nv; d.points.stride = 12;
        d.triangles.data = in.data() + 8 + nv * 12; d.triangles.count = nt; d.triangles.stride = 12;
        d.flags = extra ? PxMeshFlags(PxMeshFlag::eFLIPNORMALS) : PxMeshFlags(0);
        size_t mats = 8 + nv * 12 + nt * 12;
        if (in.size() >= mats + nt * 2) {          // per-triangle material indices (u16)
            d.materialIndices.data = (const PxMaterialTableIndex*)(in.data() + mats); d.materialIndices.stride = 2; }
        ok = cook->cookTriangleMesh(d, out);
    }
    if (!ok) { fprintf(stderr, "cook failed\n"); return 1; }
    FILE* f = fopen(argv[3], "wb"); fwrite(out.b.data(), 1, out.b.size(), f); fclose(f);
    cook->release(); fnd->release();
    return 0;
}
