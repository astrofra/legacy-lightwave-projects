#include "internal.h"
#include <math.h>

#define TAG(s) LW_TAG((s)[0],(s)[1],(s)[2],(s)[3])

/* Source-corner normals, before triangulation or UV/material splitting.
   The legacy smoothing model follows the documented SMAN/SMGP semantics and
   the algorithm described by NewTek's lwobject SDK example: equal-weight unit
   polygon normals at a shared point, tested against the owning face's angle.
   Native 9.6 probes additionally exclude neighbours with smoothing disabled.
   This is an independent implementation; no SDK code is linked or vendored. */
float lw_smoothing_angle(const LWObject *o,uint32_t material,const char **origin,int *issue) {
    size_t depth,j; int inherited=0;
    if(origin) *origin="absent";
    if(issue) *issue=0;
    for(depth=0;material<o->materials.n&&depth<=o->materials.n;depth++) {
        const LWMaterial *m=&o->materials.v[material];
        if(m->present&32) {
            if(origin) *origin=inherited?"inherited-SMAN":"SMAN";
            return m->smoothing>0?m->smoothing:0;
        }
        if(o->format==TAG("LWOB")) {
            if(m->flags&4) { if(origin) *origin="LWOB-FLAG-4-default"; return 1.56207f; }
            return 0;
        }
        if(!m->source.size) return 0;
        for(j=0;j<o->materials.n;j++) if(lw_string_equal(m->source,o->materials.v[j].name)) break;
        if(j==o->materials.n) break;
        material=(uint32_t)j; inherited=1;
    }
    if(material<o->materials.n) {
        if(origin) *origin="unresolved-surface-inheritance";
        if(issue) *issue=1;
    }
    return 0;
}

static int unit_vector(const double v[3],float out[3]) {
    double length=sqrt(v[0]*v[0]+v[1]*v[1]+v[2]*v[2]); size_t k;
    if(!(length>0)||!isfinite(length)) return 0;
    for(k=0;k<3;k++) out[k]=(float)(v[k]/length);
    return 1;
}
static int face(const LWPrimitive *p) {
    return p->count>=3&&p->detail_parent==LW_NONE&&p->legacy_surface>=0&&
        (p->type==TAG("FACE")||p->type==TAG("PCHS")||p->type==TAG("PTCH"));
}
static LWNormal polygon_normal(const LWObject *o,const LWPrimitive *p,size_t *fallbacks) {
    LWNormal n={0}; double a[3],b[3],first[3],area[3]={0}; size_t i,k;
    const float *origin=o->positions.v+3*o->indices.v[p->first];
    const float *second=o->positions.v+3*o->indices.v[p->first+1];
    const float *last=o->positions.v+3*o->indices.v[p->first+p->count-1];
    for(k=0;k<3;k++) { a[k]=(double)second[k]-origin[k]; b[k]=(double)last[k]-origin[k]; }
    first[0]=a[1]*b[2]-a[2]*b[1]; first[1]=a[2]*b[0]-a[0]*b[2]; first[2]=a[0]*b[1]-a[1]*b[0];
    for(i=0;i<p->count;i++) {
        const float *u=o->positions.v+3*o->indices.v[p->first+i];
        const float *v=o->positions.v+3*o->indices.v[p->first+(i+1)%p->count];
        for(k=0;k<3;k++) { a[k]=(double)u[k]-origin[k]; b[k]=(double)v[k]-origin[k]; }
        area[0]+=a[1]*b[2]-a[2]*b[1]; area[1]+=a[2]*b[0]-a[0]*b[2]; area[2]+=a[0]*b[1]-a[1]*b[0];
    }
    n.valid=(unsigned char)unit_vector(first,n.v);
    /* Bridged holes, duplicate/collinear first corners and a reflex starting
       corner need an oriented whole-boundary fallback, not an arbitrary flip. */
    if(!n.valid||first[0]*area[0]+first[1]*area[1]+first[2]*area[2]<=0) {
        n.valid=(unsigned char)unit_vector(area,n.v); (*fallbacks)++;
    }
    return n;
}
static LWNormal mapped_normal(const LWMap *m,size_t i,size_t *issues) {
    LWNormal n={0}; double v[3]; size_t k;
    for(k=0;k<3;k++) v[k]=m->values.v[3*i+k];
    n.valid=(unsigned char)unit_vector(v,n.v); n.explicit_value=n.valid;
    if(!n.valid) (*issues)++;
    return n;
}

int lw_corner_normals(const LWObject *o,LWNormals *result,LWError *e) {
    size_t points=o->positions.n/3,i,j,k,*offset=NULL,*cursor=NULL,*seen=NULL;
    uint32_t *adjacent=NULL; LWNormal *faces=NULL,*mapped=NULL;
    LWString *names=NULL; unsigned char *choice=NULL; float *angles=NULL; int ok=0;
    memset(result,0,sizeof *result);
    result->corners=calloc(o->indices.n+1,sizeof *result->corners);
    result->groups=malloc((o->primitives.n+1)*sizeof *result->groups);
    offset=calloc(points+2,sizeof *offset); cursor=calloc(points+1,sizeof *cursor); seen=calloc(points+1,sizeof *seen);
    adjacent=malloc((o->indices.n+1)*sizeof *adjacent); faces=calloc(o->primitives.n+1,sizeof *faces);
    mapped=calloc(points+1,sizeof *mapped); names=calloc(o->point_blocks.n+1,sizeof *names);
    choice=calloc(o->point_blocks.n+1,1); angles=calloc(o->materials.n+1,sizeof *angles);
    if(!result->corners||!result->groups||!offset||!cursor||!seen||!adjacent||!faces||!mapped||!names||!choice||!angles) {
        lw_error(e,0,"allocation","out of memory computing normals"); goto done;
    }
    for(i=0;i<o->materials.n;i++) { int issue=0; angles[i]=lw_smoothing_angle(o,(uint32_t)i,NULL,&issue); result->issues+=issue!=0; }
    for(i=0;i<o->primitives.n;i++) result->groups[i]=LW_NONE;
    for(i=0;i<o->assignments.n;i++) {
        const LWTagAssignment *a=&o->assignments.v[i];
        if(a->type!=TAG("SMGP")) continue;
        if(a->block>=o->polygon_blocks.n||a->polygon>=o->polygon_blocks.v[a->block].count||a->tag>=o->tags.n) { result->issues++; continue; }
        result->groups[o->polygon_blocks.v[a->block].first+a->polygon]=a->tag;
    }
    /* Select only an unambiguous NORM name per native point block. VMAP and
       VMAD with that name form one map. Other dimensions remain opaque IR. */
    for(i=0;i<o->maps.n;i++) {
        const LWMap *m=&o->maps.v[i]; uint32_t pb=m->point_block;
        if(m->type!=TAG("NORM")||pb>=o->point_blocks.n) continue;
        if(m->dimension!=3) { result->issues++; continue; }
        if(!choice[pb]) { names[pb]=m->name; choice[pb]=1; }
        else if(choice[pb]==1&&!lw_string_equal(names[pb],m->name)) { choice[pb]=2; result->issues++; }
    }
    for(i=0;i<o->maps.n;i++) {
        const LWMap *m=&o->maps.v[i]; const LWPointBlock *pb;
        if(m->type!=TAG("NORM")||m->dimension!=3||m->discontinuous||m->point_block>=o->point_blocks.n||choice[m->point_block]!=1) continue;
        pb=&o->point_blocks.v[m->point_block];
        for(j=0;j<m->entries.n;j++) {
            if(m->entries.v[j].point>=pb->count) { result->issues++; continue; }
            mapped[pb->first+m->entries.v[j].point]=mapped_normal(m,j,&result->issues);
        }
    }
    for(i=0;i<o->indices.n;i++) result->corners[i]=mapped[o->indices.v[i]];
    for(i=0;i<o->maps.n;i++) {
        const LWMap *m=&o->maps.v[i]; const LWPointBlock *pb; const LWPolygonBlock *block;
        if(m->type!=TAG("NORM")||m->dimension!=3||!m->discontinuous||m->point_block>=o->point_blocks.n||choice[m->point_block]!=1) continue;
        if(m->polygon_block>=o->polygon_blocks.n) { result->issues++; continue; }
        pb=&o->point_blocks.v[m->point_block]; block=&o->polygon_blocks.v[m->polygon_block];
        for(j=0;j<m->entries.n;j++) {
            const LWMapEntry *entry=&m->entries.v[j]; const LWPrimitive *p; LWNormal n; int found=0;
            if(entry->point>=pb->count||entry->polygon>=block->count) { result->issues++; continue; }
            p=&o->primitives.v[block->first+entry->polygon]; n=mapped_normal(m,j,&result->issues);
            for(k=0;k<p->count;k++) if(o->indices.v[p->first+k]==pb->first+entry->point) { result->corners[p->first+k]=n; found=1; }
            if(!found) result->issues++;
        }
    }
    for(i=0;i<o->primitives.n;i++) if(face(&o->primitives.v[i])) {
        const LWPrimitive *p=&o->primitives.v[i]; faces[i]=polygon_normal(o,p,&result->polygon_fallbacks);
        if(!faces[i].valid) continue;
        for(j=0;j<p->count;j++) { uint32_t point=o->indices.v[p->first+j]; if(seen[point]!=i+1) { offset[point+1]++; seen[point]=i+1; } }
    }
    for(i=1;i<=points;i++) offset[i]+=offset[i-1];
    memcpy(cursor,offset,(points+1)*sizeof *cursor); memset(seen,0,(points+1)*sizeof *seen);
    for(i=0;i<o->primitives.n;i++) if(faces[i].valid) {
        const LWPrimitive *p=&o->primitives.v[i];
        for(j=0;j<p->count;j++) { uint32_t point=o->indices.v[p->first+j]; if(seen[point]!=i+1) { adjacent[cursor[point]++]=(uint32_t)i; seen[point]=i+1; } }
    }
    for(i=0;i<o->primitives.n;i++) if(face(&o->primitives.v[i])) {
        const LWPrimitive *p=&o->primitives.v[i]; double angle=p->material<o->materials.n?angles[p->material]:0;
        double limit=cos(fmin(angle,3.14159265358979323846));
        for(j=0;j<p->count;j++) {
            LWNormal *n=&result->corners[p->first+j]; uint32_t point=o->indices.v[p->first+j]; double sum[3];
            if(n->valid) { result->explicit_corners++; continue; }
            *n=faces[i]; if(!n->valid) continue;
            for(k=0;k<3;k++) sum[k]=n->v[k];
            if(angle>0) for(k=offset[point];k<offset[point+1];k++) {
                uint32_t other=adjacent[k]; size_t axis; double dot=0;
                if(other==i||result->groups[other]!=result->groups[i]) continue;
                if(o->primitives.v[other].material>=o->materials.n||angles[o->primitives.v[other].material]<=0) continue;
                for(axis=0;axis<3;axis++) dot+=(double)faces[i].v[axis]*faces[other].v[axis];
                if(dot+1e-7<limit) continue;
                for(axis=0;axis<3;axis++) sum[axis]+=faces[other].v[axis];
                n->smoothed=1;
            }
            if(!unit_vector(sum,n->v)) { *n=faces[i]; result->issues++; }
            result->smoothed_corners+=n->smoothed!=0;
        }
    }
    ok=1;
done:
    free(offset); free(cursor); free(seen); free(adjacent); free(faces); free(mapped); free(names); free(choice); free(angles);
    if(!ok) lw_free_normals(result);
    return ok;
}
void lw_free_normals(LWNormals *normals) {
    free(normals->corners); free(normals->groups); memset(normals,0,sizeof *normals);
}
int lw_transform_normal(const double m[16],const float n[3],double out[3]) {
    double a=m[0],b=m[4],c=m[8],d=m[1],f=m[5],g=m[9],h=m[2],i=m[6],j=m[10];
    double determinant=a*(f*j-g*i)-b*(d*j-g*h)+c*(d*i-f*h),length; size_t k;
    if(!determinant||!isfinite(determinant)) return 0;
    out[0]=(f*j-g*i)*n[0]+(g*h-d*j)*n[1]+(d*i-f*h)*n[2];
    out[1]=(c*i-b*j)*n[0]+(a*j-c*h)*n[1]+(b*h-a*i)*n[2];
    out[2]=(b*g-c*f)*n[0]+(c*d-a*g)*n[1]+(a*f-b*d)*n[2];
    length=sqrt(out[0]*out[0]+out[1]*out[1]+out[2]*out[2]);
    if(!(length>0)||!isfinite(length)) return 0;
    for(k=0;k<3;k++) out[k]/=determinant<0?-length:length;
    out[2]=-out[2]; return 1; /* inverse transpose, then source Z reflection */
}
