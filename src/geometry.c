#include "internal.h"
#include <math.h>

static int map_named(const LWMap *m,const char *name) {
    char *text; int match;
    if(m->type!=LW_TAG('T','X','U','V')||m->dimension!=2) return 0;
    text=lw_text(m->name); if(!text) return 0;
    match=!strcmp(text,name); free(text); return match;
}
LWUV *lw_corner_uvs(const LWObject *o,const char *name,LWError *e) {
    LWUV *points=calloc(o->positions.n/3+1,sizeof *points),*corners=calloc(o->indices.n+1,sizeof *corners); size_t i,j,k;
    if(!points||!corners) { free(points); free(corners); lw_error(e,0,"allocation","out of memory"); return NULL; }
    for(i=0;i<o->maps.n;i++) {
        const LWMap *m=&o->maps.v[i]; const LWPointBlock *pb;
        if(m->discontinuous||!map_named(m,name)||m->point_block>=o->point_blocks.n) continue;
        pb=&o->point_blocks.v[m->point_block];
        for(j=0;j<m->entries.n;j++) if(m->entries.v[j].point<pb->count) {
            if(!isfinite(m->values.v[2*j])||!isfinite(m->values.v[2*j+1])) continue;
            LWUV uv={m->values.v[2*j],m->values.v[2*j+1],1}; points[pb->first+m->entries.v[j].point]=uv;
        }
    }
    for(i=0;i<o->indices.n;i++) corners[i]=points[o->indices.v[i]];
    free(points);
    for(i=0;i<o->maps.n;i++) {
        const LWMap *m=&o->maps.v[i]; const LWPointBlock *pb; const LWPolygonBlock *pols;
        if(!m->discontinuous||!map_named(m,name)||m->point_block>=o->point_blocks.n||m->polygon_block>=o->polygon_blocks.n) continue;
        pb=&o->point_blocks.v[m->point_block]; pols=&o->polygon_blocks.v[m->polygon_block];
        for(j=0;j<m->entries.n;j++) {
            const LWMapEntry *entry=&m->entries.v[j]; const LWPrimitive *p;
            if(entry->point>=pb->count||entry->polygon>=pols->count) continue;
            p=&o->primitives.v[pols->first+entry->polygon];
            for(k=0;k<p->count;k++) if(o->indices.v[p->first+k]==pb->first+entry->point) {
                LWUV uv={m->values.v[2*j],m->values.v[2*j+1],0}; uv.valid=(unsigned char)(isfinite(uv.u)&&isfinite(uv.v)); corners[p->first+k]=uv;
            }
        }
    }
    return corners;
}
