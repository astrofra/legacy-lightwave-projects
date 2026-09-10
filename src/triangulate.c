#include "internal.h"
#include <float.h>
#include <math.h>

/* Ear clipping of a projected boundary, including holes connected by paired
   reverse edges. Results refer to source corners, never welded point indices.
   Invalid contours fail explicitly; the source polygon remains in LWIR. */
typedef struct { double x,y; uint32_t prev,next; int active; } Corner;
static double orient(const Corner *a,const Corner *b,const Corner *c) {
    return (b->x-a->x)*(c->y-a->y)-(b->y-a->y)*(c->x-a->x);
}
static int same(const Corner *a,const Corner *b) { return a->x==b->x&&a->y==b->y; }
static int between(double a,double b,double x) { return x>=fmin(a,b)&&x<=fmax(a,b); }
static int on_segment(const Corner *a,const Corner *b,const Corner *p,double epsilon) {
    return fabs(orient(a,b,p))<=epsilon&&between(a->x,b->x,p->x)&&between(a->y,b->y,p->y);
}
static int crossing(const Corner *a,const Corner *b,const Corner *c,const Corner *d,double epsilon) {
    double ab_c=orient(a,b,c),ab_d=orient(a,b,d),cd_a=orient(c,d,a),cd_b=orient(c,d,b);
    if(((ab_c>epsilon&&ab_d<-epsilon)||(ab_c<-epsilon&&ab_d>epsilon))&&
       ((cd_a>epsilon&&cd_b<-epsilon)||(cd_a<-epsilon&&cd_b>epsilon))) return 1;
    /* A non-endpoint touching an edge also prevents a valid diagonal. */
    if(!same(c,a)&&!same(c,b)&&on_segment(a,b,c,epsilon)) return 1;
    if(!same(d,a)&&!same(d,b)&&on_segment(a,b,d,epsilon)) return 1;
    if(!same(a,c)&&!same(a,d)&&on_segment(c,d,a,epsilon)) return 1;
    if(!same(b,c)&&!same(b,d)&&on_segment(c,d,b,epsilon)) return 1;
    return 0;
}
static int reject(LWTriangulation *t,const char *reason) {
    snprintf(t->issue,sizeof t->issue,"%s",reason); t->corners.n=0; return 0;
}
static void remove_corner(Corner *v,uint32_t i) {
    v[v[i].prev].next=v[i].next; v[v[i].next].prev=v[i].prev; v[i].active=0;
}
static int inside_triangle(const Corner *a,const Corner *b,const Corner *c,const Corner *p,double sign,double epsilon) {
    return sign*orient(a,b,p)>=-epsilon&&sign*orient(b,c,p)>=-epsilon&&sign*orient(c,a,p)>=-epsilon;
}
static int ear(const Corner *v,uint32_t i,uint32_t count,double sign,double epsilon) {
    uint32_t a=v[i].prev,b=v[i].next,j;
    if(sign*orient(&v[a],&v[i],&v[b])<=epsilon) return 0;
    for(j=0;j<count;j++) if(v[j].active&&j!=a&&j!=i&&j!=b) {
        /* Duplicate bridge endpoints are separate corners of the same boundary. */
        if(same(&v[j],&v[a])||same(&v[j],&v[i])||same(&v[j],&v[b])) continue;
        if(inside_triangle(&v[a],&v[i],&v[b],&v[j],sign,epsilon)) return 0;
    }
    for(j=0;j<count;j++) if(v[j].active) {
        uint32_t k=v[j].next;
        if(j==a||j==b||k==a||k==b) continue;
        if(crossing(&v[a],&v[b],&v[j],&v[k],epsilon)) return 0;
    }
    return 1;
}
int lw_triangulate(const LWObject *o,const LWPrimitive *p,LWTriangulation *t,LWError *e) {
    Corner *v=NULL; double normal[3]={0},low[3],high[3],scale=0,length,area=0,sign,sum=0,epsilon;
    const float *origin; uint32_t i,j,axis=0,x,y,remaining,cursor; int result=0;
    memset(t,0,sizeof *t);
    if(p->count<3) return reject(t,"fewer than three corners");
    if(p->count>4096) return reject(t,"contour exceeds the 4096-corner triangulation limit");
    origin=o->positions.v+3*o->indices.v[p->first];
    for(j=0;j<3;j++) low[j]=high[j]=origin[j];
    for(i=0;i<p->count;i++) {
        const float *a=o->positions.v+3*o->indices.v[p->first+i];
        const float *b=o->positions.v+3*o->indices.v[p->first+(i+1)%p->count];
        double u[3],w[3];
        for(j=0;j<3;j++) { low[j]=fmin(low[j],a[j]); high[j]=fmax(high[j],a[j]); u[j]=(double)a[j]-origin[j]; w[j]=(double)b[j]-origin[j]; }
        normal[0]+=u[1]*w[2]-u[2]*w[1]; normal[1]+=u[2]*w[0]-u[0]*w[2]; normal[2]+=u[0]*w[1]-u[1]*w[0];
    }
    for(j=0;j<3;j++) { scale=fmax(scale,high[j]-low[j]); if(fabs(normal[j])>fabs(normal[axis])) axis=j; }
    if(!scale||!normal[axis]) return reject(t,"zero projected area or cancelling boundary");
    length=sqrt(normal[0]*normal[0]+normal[1]*normal[1]+normal[2]*normal[2]);
    for(i=0;i<p->count;i++) {
        const float *a=o->positions.v+3*o->indices.v[p->first+i]; double distance=0;
        for(j=0;j<3;j++) distance+=((double)a[j]-origin[j])*normal[j]/length;
        if(fabs(distance)>scale*1e-5) t->nonplanar=1;
    }
    x=(axis+1)%3; y=(axis+2)%3;
    epsilon=64*DBL_EPSILON;
    v=calloc(p->count,sizeof *v);
    if(!v||!lw_grow((void **)&t->corners.v,&t->corners.cap,3*((size_t)p->count-2),sizeof *t->corners.v,e)) {
        free(v); lw_error(e,0,"triangulation","out of memory"); return -1;
    }
    for(i=0;i<p->count;i++) {
        const float *a=o->positions.v+3*o->indices.v[p->first+i];
        v[i].x=((double)a[x]-origin[x])/scale; v[i].y=((double)a[y]-origin[y])/scale;
        v[i].prev=(i+p->count-1)%p->count; v[i].next=(i+1)%p->count; v[i].active=1;
    }
    remaining=p->count;
    /* Adjacent copies of the same source point carry the same polygon UV.
       Distinct points at the same position are not merged. */
    for(i=0;i<p->count&&remaining>3;i++) if(v[i].active) {
        uint32_t next=v[i].next;
        while(remaining>3&&o->indices.v[p->first+i]==o->indices.v[p->first+next]) {
            remove_corner(v,next); remaining--; t->removed_corners++; next=v[i].next;
        }
    }
    for(i=0;i<p->count;i++) if(v[i].active) {
        uint32_t next=v[i].next; area+=v[i].x*v[next].y-v[next].x*v[i].y;
        for(j=i+1;j<p->count;j++) if(v[j].active&&j!=next&&v[j].next!=i) {
            uint32_t after=v[j].next;
            if(same(&v[i],&v[after])&&same(&v[next],&v[j])) { t->bridges++; continue; }
            if((same(&v[i],&v[j])&&same(&v[next],&v[after]))||crossing(&v[i],&v[next],&v[j],&v[after],epsilon)) {
                reject(t,"self-intersecting, overlapping or touching boundary edges"); goto done;
            }
        }
    }
    if(fabs(area)<=epsilon) { reject(t,"projected area is numerically degenerate"); goto done; }
    sign=area>0?1:-1; cursor=0;
    while(!v[cursor].active) cursor++;
    while(remaining>3) {
        uint32_t start=cursor; int found=0;
        do {
            if(ear(v,cursor,p->count,sign,epsilon)) {
                uint32_t a=v[cursor].prev,b=v[cursor].next;
                t->corners.v[t->corners.n++]=a; t->corners.v[t->corners.n++]=cursor; t->corners.v[t->corners.n++]=b;
                sum+=sign*orient(&v[a],&v[cursor],&v[b]);
                remove_corner(v,cursor); remaining--; cursor=b; found=1; break;
            }
            cursor=v[cursor].next;
        } while(cursor!=start);
        if(!found) { reject(t,"no valid ear; contour is degenerate or outside the supported boundary profile"); goto done; }
    }
    {
        uint32_t a=cursor,b=v[a].next,c=v[b].next; double last=sign*orient(&v[a],&v[b],&v[c]);
        if(last<=epsilon) { reject(t,"degenerate final triangle"); goto done; }
        t->corners.v[t->corners.n++]=a; t->corners.v[t->corners.n++]=b; t->corners.v[t->corners.n++]=c; sum+=last;
    }
    if(fabs(sum-fabs(area))>1e-9*fabs(area)) { reject(t,"triangle area does not match the source boundary"); goto done; }
    result=1;
done:
    free(v); return result;
}
void lw_free_triangulation(LWTriangulation *t) { LW_FREE(t->corners); }
