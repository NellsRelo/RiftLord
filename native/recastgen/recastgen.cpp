// recastgen: UE 4.27's Recast, run offline on a triangle soup, writing its polygon mesh.
//
// Built by recastgen/build.py against the engine's own Recast sources (not shipped here).
// Input and output are little-endian binary files; recastgen.py writes and reads them.
//
// in:  int32 nverts, int32 ntris, float32 params[PARAMS], int32 nvp,
//      float32 verts[nverts*3] (Recast space: x, y up, z), int32 tris[ntris*3],
//      uint8 walk[ntris] (0: the triangle blocks but is never floor; 2: floor of a ramp,
//      which gets an area of its own)
//
// DECOR_MODE 1: walk-0 (decor) triangles are not rasterised with the floor. They go into a
// second heightfield, and AFTER the floor is eroded by the agent radius, every floor span with
// decor rising more than DECOR_PROUD above it (and starting below the agent's head) becomes
// null, widened by DECOR_CLEAR. NavPower keeps far less clearance from decor than from walls
// (Front Lawn: ~0 / ~50 uu for the 42 / 80 agents), which one erosion radius cannot express.
// out: int32 nverts, int32 npolys, int32 nvp, float32 bmin[3], float32 cs, float32 ch,
//      uint16 verts[nverts*3] (cells), uint16 polys[npolys*nvp*2] (vertices then neighbours),
//      uint16 regs[npolys],
//      int32 nmeshes, int32 ndverts, int32 ndtris, uint32 meshes[nmeshes*4] (vert base, vert
//      count, tri base, tri count per polygon), float32 dverts[ndverts*3] (world, Recast
//      space), uint8 dtris[ndtris*4] (three vertex indices within the polygon's verts, flags),
//      uint8 areas[npolys] (RC_WALKABLE_AREA: a ramp's polygon)

#if __has_include("Recast/Recast.h")
#include "Recast/Recast.h"      // UE 4.27
#else
#include "Recast.h"             // recastnavigation
#endif
#include <cmath>
#include <cstdio>

// UE 4.27 keeps a span's limits in rcSpan::data; recastnavigation has them on the span
#if __has_include("Recast/Recast.h")
static int spanMin(const rcSpan* s) { return (int)s->data.smin; }
static int spanMax(const rcSpan* s) { return (int)s->data.smax; }
#else
static int spanMin(const rcSpan* s) { return (int)s->smin; }
static int spanMax(const rcSpan* s) { return (int)s->smax; }
#endif
#include <vector>

enum { CS, CH, SLOPE, HEIGHT, CLIMB, RADIUS, MAX_ERROR, MAX_EDGE, MIN_REGION, MERGE_REGION,
       DETAIL_DIST, DETAIL_ERROR, DECOR_MODE, DECOR_PROUD, DECOR_CLEAR, PARAMS };

// The area of walk-1 floor. Walk-2 (ramp) floor keeps RC_WALKABLE_AREA, so regions, and so
// polygons, never span a ramp and the floor beside it, and where a ramp's foot merges with the
// floor under it the span takes the larger area: the ramp's.
static const unsigned char FLOOR_AREA = 1;

static bool readAll(FILE* f, void* p, size_t n) { return fread(p, 1, n, f) == n; }

int main(int argc, char** argv)
{
	if (argc != 3) { fprintf(stderr, "usage: recastgen in.bin out.bin\n"); return 2; }
	FILE* in = fopen(argv[1], "rb");
	if (!in) { fprintf(stderr, "cannot read %s\n", argv[1]); return 2; }
	int nv = 0, nt = 0, nvp = 0;
	float prm[PARAMS];
	if (!readAll(in, &nv, 4) || !readAll(in, &nt, 4) || !readAll(in, prm, sizeof prm)
		|| !readAll(in, &nvp, 4)) { fprintf(stderr, "short header\n"); return 2; }
	std::vector<float> verts(nv * 3);
	std::vector<int> tris(nt * 3);
	std::vector<unsigned char> walk(nt);
	if (!readAll(in, verts.data(), verts.size() * 4) || !readAll(in, tris.data(), tris.size() * 4)
		|| !readAll(in, walk.data(), walk.size()))
	{ fprintf(stderr, "short body\n"); return 2; }
	fclose(in);

	rcContext ctx(false);
	float bmin[3], bmax[3];
	rcCalcBounds(verts.data(), nv, bmin, bmax);
	const float cs = prm[CS], ch = prm[CH];
	const int height = (int)ceilf(prm[HEIGHT] / ch);
	const int climb = (int)floorf(prm[CLIMB] / ch);
	const int radius = (int)ceilf(prm[RADIUS] / cs);
	int w = 0, h = 0;
	rcCalcGridSize(bmin, bmax, cs, &w, &h);

	rcHeightfield* hf = rcAllocHeightfield();
	if (!rcCreateHeightfield(&ctx, *hf, w, h, bmin, bmax, cs, ch))
	{ fprintf(stderr, "heightfield %dx%d failed\n", w, h); return 1; }
	std::vector<unsigned char> areas(nt, 0);
	rcMarkWalkableTriangles(&ctx, prm[SLOPE], verts.data(), nv, tris.data(), nt, areas.data());
	for (int i = 0; i < nt; ++i)
		if (!walk[i]) areas[i] = RC_NULL_AREA;
		else if (walk[i] != 2 && areas[i] == RC_WALKABLE_AREA) areas[i] = FLOOR_AREA;
	const bool decorMode = prm[DECOR_MODE] > 0.5f;
	rcHeightfield* decor = 0;
	if (decorMode)
	{
		// floor geometry only in hf; decor alone in its own heightfield on the same grid
		std::vector<int> floorTris, decorTris;
		std::vector<unsigned char> floorAreas, decorAreas;
		for (int i = 0; i < nt; ++i)
		{
			std::vector<int>& t = walk[i] ? floorTris : decorTris;
			t.insert(t.end(), &tris[i * 3], &tris[i * 3 + 3]);
			(walk[i] ? floorAreas : decorAreas).push_back(walk[i] ? areas[i] : RC_WALKABLE_AREA);
		}
		rcRasterizeTriangles(&ctx, verts.data(), nv, floorTris.data(), floorAreas.data(),
			(int)floorAreas.size(), *hf, climb);
		decor = rcAllocHeightfield();
		if (!rcCreateHeightfield(&ctx, *decor, w, h, bmin, bmax, cs, ch))
		{ fprintf(stderr, "decor heightfield failed\n"); return 1; }
		rcRasterizeTriangles(&ctx, verts.data(), nv, decorTris.data(), decorAreas.data(),
			(int)decorAreas.size(), *decor, climb);
	}
	else
		rcRasterizeTriangles(&ctx, verts.data(), nv, tris.data(), areas.data(), nt, *hf, climb);
	rcFilterLowHangingWalkableObstacles(&ctx, climb, *hf);
	rcFilterLedgeSpans(&ctx, height, climb, *hf);
	rcFilterWalkableLowHeightSpans(&ctx, height, *hf);

	rcCompactHeightfield* chf = rcAllocCompactHeightfield();
	if (!rcBuildCompactHeightfield(&ctx, height, climb, *hf, *chf))
	{ fprintf(stderr, "compact heightfield failed\n"); return 1; }
	if (!rcErodeWalkableArea(&ctx, radius, *chf))
	{ fprintf(stderr, "erode failed\n"); return 1; }
	if (decor)
	{
		const int proud = (int)floorf(prm[DECOR_PROUD] / ch);
		const int clear = (int)roundf(prm[DECOR_CLEAR] / cs);
		std::vector<unsigned char> blocked(chf->spanCount, 0);
		for (int y = 0; y < h; ++y)
			for (int x = 0; x < w; ++x)
			{
				const rcCompactCell& c = chf->cells[x + y * w];
				for (int i = (int)c.index, ni = (int)(c.index + c.count); i < ni; ++i)
				{
					const int floor = chf->spans[i].y;
					for (const rcSpan* d = decor->spans[x + y * w]; d; d = d->next)
						if (spanMax(d) > floor + proud && spanMin(d) < floor + height)
						{ blocked[i] = 1; break; }
				}
			}
		// widen by `clear` cells, one ring of 4-connected neighbours per pass
		for (int pass = 0; pass < clear; ++pass)
		{
			std::vector<unsigned char> next = blocked;
			for (int y = 0; y < h; ++y)
				for (int x = 0; x < w; ++x)
				{
					const rcCompactCell& c = chf->cells[x + y * w];
					for (int i = (int)c.index, ni = (int)(c.index + c.count); i < ni; ++i)
					{
						if (!blocked[i]) continue;
						const rcCompactSpan& s = chf->spans[i];
						for (int dir = 0; dir < 4; ++dir)
						{
							if (rcGetCon(s, dir) == RC_NOT_CONNECTED) continue;
							const int ax = x + rcGetDirOffsetX(dir), ay = y + rcGetDirOffsetY(dir);
							next[(int)chf->cells[ax + ay * w].index + rcGetCon(s, dir)] = 1;
						}
					}
				}
			blocked.swap(next);
		}
		for (int i = 0; i < chf->spanCount; ++i)
			if (blocked[i]) chf->areas[i] = RC_NULL_AREA;
		rcFreeHeightField(decor);
	}
	if (!rcBuildDistanceField(&ctx, *chf)
		|| !rcBuildRegions(&ctx, *chf, 0, (int)prm[MIN_REGION], (int)prm[MERGE_REGION]))
	{ fprintf(stderr, "regions failed\n"); return 1; }

	rcContourSet* cset = rcAllocContourSet();
	if (!rcBuildContours(&ctx, *chf, prm[MAX_ERROR], (int)(prm[MAX_EDGE] / cs), *cset))
	{ fprintf(stderr, "contours failed\n"); return 1; }
	rcPolyMesh* pm = rcAllocPolyMesh();
	if (!rcBuildPolyMesh(&ctx, *cset, nvp, *pm))
	{ fprintf(stderr, "poly mesh failed\n"); return 1; }

	rcPolyMeshDetail* dm = rcAllocPolyMeshDetail();
	if (!rcBuildPolyMeshDetail(&ctx, *pm, *chf, prm[DETAIL_DIST], prm[DETAIL_ERROR], *dm))
	{ fprintf(stderr, "detail mesh failed\n"); return 1; }

	FILE* out = fopen(argv[2], "wb");
	if (!out) { fprintf(stderr, "cannot write %s\n", argv[2]); return 2; }
	fwrite(&pm->nverts, 4, 1, out);
	fwrite(&pm->npolys, 4, 1, out);
	fwrite(&pm->nvp, 4, 1, out);
	fwrite(pm->bmin, 4, 3, out);
	fwrite(&pm->cs, 4, 1, out);
	fwrite(&pm->ch, 4, 1, out);
	fwrite(pm->verts, 2, pm->nverts * 3, out);
	fwrite(pm->polys, 2, pm->npolys * pm->nvp * 2, out);
	fwrite(pm->regs, 2, pm->npolys, out);
	fwrite(&dm->nmeshes, 4, 1, out);
	fwrite(&dm->nverts, 4, 1, out);
	fwrite(&dm->ntris, 4, 1, out);
	fwrite(dm->meshes, 4, dm->nmeshes * 4, out);
	fwrite(dm->verts, 4, dm->nverts * 3, out);
	fwrite(dm->tris, 1, dm->ntris * 4, out);
	fwrite(pm->areas, 1, pm->npolys, out);
	fclose(out);
	rcFreePolyMeshDetail(dm);
	printf("%d %d %d %d\n", w, h, pm->nverts, pm->npolys);
	rcFreePolyMesh(pm);
	rcFreeContourSet(cset);
	rcFreeCompactHeightfield(chf);
	rcFreeHeightField(hf);
	return 0;
}
