#include "internal.h"
#include "mikktspace.h"
#include <math.h>
#include <limits.h>

#define TAG(s) LW_TAG((s)[0],(s)[1],(s)[2],(s)[3])
#define PI 3.14159265358979323846
/* Bounded CPU baker, deliberately independent of a native LightWave host.
   RGB normal vectors are LINEAR data, including when the source is a JPEG. */
const char *lw_normal_space_name(int space) {
    static const char *names[]={"auto","object","world","tangent","off"};
    return space>=0&&space<5?names[space]:"unknown";
}
static double dot(const double a[3],const double b[3]) { return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]; }
static void cross(const double a[3],const double b[3],double c[3]) {
    c[0]=a[1]*b[2]-a[2]*b[1]; c[1]=a[2]*b[0]-a[0]*b[2]; c[2]=a[0]*b[1]-a[1]*b[0];
}
static int normalize(double v[3]) {
    double len=sqrt(dot(v,v)); size_t k;
    if(!isfinite(len)||len==0) return 0;
    for(k=0;k<3;k++) v[k]/=len;
    return 1;
}
static double degrees(double cosine) { return acos(fmax(-1,fmin(1,cosine)))*180/PI; }
int lw_render_triangle(const LWObject *o,uint32_t polygon,const uint32_t corners[3],const LWUV *uv,const LWNormals *normals,LWRenderVertex v[3],LWError *e) {
    double a[3],b[3],n[3]; size_t i,j;
    memset(v,0,3*sizeof *v);
    for(i=0;i<3;i++) {
        uint32_t index=o->primitives.v[polygon].first+corners[2-i];
        v[i].polygon=polygon; v[i].corner=corners[2-i]; v[i].point=o->indices.v[index];
        memcpy(v[i].p,o->positions.v+3*v[i].point,sizeof v[i].p); v[i].p[2]=-v[i].p[2];
        if(uv) { v[i].uv[0]=uv[index].u; v[i].uv[1]=(float)(1.0-uv[index].v); }
    }
    for(i=0;i<3;i++) { a[i]=(double)v[1].p[i]-v[0].p[i]; b[i]=(double)v[2].p[i]-v[0].p[i]; }
    cross(a,b,n);
    if(!normalize(n)) return lw_error(e,0,"glTF","triangulator returned a degenerate triangle");
    for(i=0;i<3;i++) {
        const LWNormal *source=&normals->corners[o->primitives.v[polygon].first+corners[2-i]];
        for(j=0;j<3;j++) v[i].n[j]=source->valid?source->v[j]*(j==2?-1.f:1.f):(float)n[j];
    }
    return 1;
}
static int faces(const SMikkTSpaceContext *c) { return (int)(((LWRenderVertices *)c->m_pUserData)->n/3); }
static int vertices(const SMikkTSpaceContext *c,int face) { (void)c; (void)face; return 3; }
static LWRenderVertex *mikk_vertex(const SMikkTSpaceContext *c,int face,int corner) { return &((LWRenderVertices *)c->m_pUserData)->v[(size_t)face*3+corner]; }
static void position(const SMikkTSpaceContext *c,float v[],int face,int corner) { memcpy(v,mikk_vertex(c,face,corner)->p,3*sizeof *v); }
static void normal(const SMikkTSpaceContext *c,float v[],int face,int corner) { memcpy(v,mikk_vertex(c,face,corner)->n,3*sizeof *v); }
static void texcoord(const SMikkTSpaceContext *c,float v[],int face,int corner) { memcpy(v,mikk_vertex(c,face,corner)->uv,2*sizeof *v); }
static void tangent(const SMikkTSpaceContext *c,const float v[],float sign,int face,int corner) {
    LWRenderVertex *p=mikk_vertex(c,face,corner); memcpy(p->tangent,v,3*sizeof *v); /* Mikk receives stored glTF UVs (V down). Normal-map +Y follows the
       conventional V-up frame used by Blender and OpenGL normal-map bakers.
       Export that choice explicitly: no implicit bitangent reconstruction. */
    p->tangent[3]=-sign;
}
static int mikktspace(LWRenderVertices *mesh) {
    SMikkTSpaceInterface interface={faces,vertices,position,normal,texcoord,tangent,NULL};
    SMikkTSpaceContext context={&interface,mesh};
    if(!mesh->n||mesh->n/3>INT_MAX/3) return 0;
    return genTangSpaceDefault(&context);
}
static const char *qualify(const LWObject *o,const LWTexture *t,const LWOptions *opts) {
    size_t i; const LWMaterial *m=&o->materials.v[t->material];
    if(t->issue[0]) return t->issue;
    if(opts->normal_space==4) return "normal conversion disabled by --normal-space off";
    if(!t->enabled) return "disabled NormalShader or normal image layer";
    if(t->projection!=5||!t->uv_map.size) return "normal conversion requires a named UV image map";
    if(t->has_envelopes||t->opacity_type||t->opacity!=1||t->flags&16) return "animated, blended or negated normal layers are not evaluated";
    if(t->wrap[0]!=1||t->wrap[1]!=1) return "normal conversion currently requires repeat wrapping";
    if(t->coordinate_system||(t->reference_object.size&&!lw_string_is(t->reference_object,"(none)"))) return "normal texture mapping uses a reference object/world projection";
    for(i=0;i<3;i++) if(t->size[i]!=1||t->center[i]||t->rotation[i]||t->falloff[i]) return "transformed normal texture mapping is not evaluated";
    if(opts->uv_map&&!lw_string_is(t->uv_map,opts->uv_map)) return "explicit --uv-map differs from normal shader UVs";
    if(m->textured&&!lw_same_mapping(t,&o->textures.v[m->projection_texture])) return "normal and color maps require different UV sets; preserved only";
    if(!lw_texture_pixels(o,t->image)) return "normal image unresolved or cannot be decoded; see image_references";
    for(i=0;i<o->textures.n;i++) {
        const LWTexture *other=&o->textures.v[i];
        if(other!=t&&other->enabled&&other->material==t->material&&
           (other->clip_scope||(other->block_type==TAG("SHDR")&&lw_string_is(other->shader,"NormalShader")&&other->offset!=t->clip_scope-1))) return "multiple normal shader bindings; compositing is not evaluated";
    }
    return NULL;
}
/* Interpolate the per-vertex glTF basis, then normalize its three columns.
   They need not be orthogonal: the baker uses the actual inverse, not transpose.
   B_i = tangent.w * cross(N_i,T_i). No tangent welding after MikkTSpace. */
static int basis(const LWRenderVertex v[3],const double weights[3],double frame[3][3]) {
    size_t i,k; memset(frame,0,9*sizeof(double));
    for(i=0;i<3;i++) {
        double n[3],t[3],b[3];
        for(k=0;k<3;k++) { n[k]=v[i].n[k]; t[k]=v[i].tangent[k]; }
        cross(n,t,b);
        for(k=0;k<3;k++) { frame[0][k]+=weights[i]*t[k]; frame[1][k]+=weights[i]*b[k]*v[i].tangent[3]; frame[2][k]+=weights[i]*n[k]; }
    }
    return normalize(frame[0])&&normalize(frame[1])&&normalize(frame[2]);
}
static int inverse(double f[3][3],const double source[3],double target[3]) {
    double rows[3][3],det; size_t k;
    cross(f[1],f[2],rows[0]); cross(f[2],f[0],rows[1]); cross(f[0],f[1],rows[2]); det=dot(f[0],rows[0]);
    if(fabs(det)<1e-6||!isfinite(det)) return 0;
    for(k=0;k<3;k++) target[k]=dot(rows[k],source)*(det<0?-1:1);
    return normalize(target);
}
typedef struct {
    LWTexture *texture; const LWImageReference *image;
    unsigned char *covered,*rgba; double scores[48],sum_normal[3]; size_t interior,valid;
    size_t object_hemisphere,tangent_hemisphere;
    const char *issue;
} Bake;
static int decode(const Bake *b,size_t index,double n[3],double *length) {
    size_t k; const unsigned char *p=b->image->rgba+4*index;
    for(k=0;k<3;k++) n[k]=2*p[k]/255.0-1;
    if(b->texture->normal->green_negative) n[1]=-n[1];
    *length=sqrt(dot(n,n));
    return normalize(n);
}
static void score(Bake *b,const LWRenderVertex v[3],const double w[3],size_t index) {
    static const unsigned permutations[6][3]={{0,1,2},{0,2,1},{1,0,2},{1,2,0},{2,0,1},{2,1,0}};
    double n[3],ng[3]={0},length; size_t i,k,s;
    if(w[0]<.03||w[1]<.03||w[2]<.03||b->covered[index]) return;
    b->covered[index]=1; b->interior++;
    if(!decode(b,index,n,&length)||length<.7||length>1.3) return;
    for(i=0;i<3;i++) for(k=0;k<3;k++) ng[k]+=w[i]*v[i].n[k]*(k==2?-1:1);
    if(!normalize(ng)) return;
    b->valid++; b->object_hemisphere+=dot(n,ng)>0; b->tangent_hemisphere+=n[2]>0;
    for(k=0;k<3;k++) b->sum_normal[k]+=ng[k];
    for(i=0;i<6;i++) for(s=0;s<8;s++) {
        double candidate[3]; for(k=0;k<3;k++) candidate[k]=n[permutations[i][k]]*((s&((size_t)1<<k))?-1:1);
        b->scores[8*i+s]+=degrees(dot(candidate,ng));
    }
}
static int bake_pixel(Bake *b,const LWRenderVertex v[3],const double w[3],size_t index) {
    LWNormalConversion *stats=b->texture->normal;
    double f[3][3],n[3],source[3],length,target[3]; unsigned char encoded[4]; size_t k,j;
    if(b->image->rgba[4*index+3]!=255) { b->issue="transparent normal image requires native VParm compositing"; return 0; }
    if(!decode(b,index,n,&length)||length<.1) { stats->invalid++; return 1; }
    if(!basis(v,w,f)) { b->issue="singular interpolated tangent basis"; return 0; }
    if(stats->effective_space==3) {
        /* Explicit tangent mode asserts an already compatible final glTF/Mikk
           basis; the unknown tangent basis of an old baker cannot be inferred. */
        memcpy(target,n,sizeof n);
        for(k=0;k<3;k++) source[k]=f[0][k]*n[0]+f[1][k]*n[1]+f[2][k]*n[2];
        if(!normalize(source)) { b->issue="invalid tangent-space normal"; return 0; }
    } else {
        if(stats->effective_space==2) {
            double scale=0; for(k=0;k<9;k++) scale=fmax(scale,fabs(stats->world_matrix[k]));
            for(k=0;k<3;k++) { source[k]=0; for(j=0;j<3;j++) source[k]+=(stats->world_matrix[3*j+k]/scale)*n[j]; }
            if(!normalize(source)) { b->issue="invalid world-to-object normal transform"; return 0; }
        } else memcpy(source,n,sizeof n);
        source[2]=-source[2]; /* Source LightWave to final glTF coordinates. */
        if(!inverse(f,source,target)) { b->issue="singular interpolated tangent basis"; return 0; }
    }
    for(k=0;k<3;k++) encoded[k]=(unsigned char)(fmax(0,fmin(1,.5+.5*target[k]))*255+.5);
    encoded[3]=255;
    if(b->covered[index]) {
        double previous[3]; for(k=0;k<3;k++) previous[k]=2*b->rgba[4*index+k]/255.0-1;
        if(!normalize(previous)) { b->issue="invalid previous normal texel"; return 0; }
        stats->overlaps++;
        if(dot(previous,target)<cos(3*PI/180)) { stats->conflicts++; return 1; }
    } else {
        b->covered[index]=1; stats->covered++; memcpy(b->rgba+4*index,encoded,4);
    }
    /* Independent reconstruction of the quantized value through F, measuring
       encoding error, not fidelity to the original native shader/render. */
    for(k=0;k<3;k++) target[k]=2*b->rgba[4*index+k]/255.0-1;
    for(k=0;k<3;k++) n[k]=f[0][k]*target[0]+f[1][k]*target[1]+f[2][k]*target[2];
    if(normalize(n)) stats->roundtrip_max_degrees=fmax(stats->roundtrip_max_degrees,degrees(dot(n,source)));
    return 1;
}
static double edge(double ax,double ay,double bx,double by,double x,double y) { return (bx-ax)*(y-ay)-(by-ay)*(x-ax); }
static int raster(Bake *b,const LWRenderVertices *mesh,int inference) {
    size_t i,k,work=0; int width=b->image->width,height=b->image->height;
    for(i=0;i<mesh->n;i+=3) {
        const LWRenderVertex *v=mesh->v+i; double x[3],y[3],area,lowx,highx,lowy,highy; int x0,x1,y0,y1,ix,iy;
        for(k=0;k<3;k++) { x[k]=(double)v[k].uv[0]*width; y[k]=(double)v[k].uv[1]*height; }
        area=edge(x[0],y[0],x[1],y[1],x[2],y[2]);
        if(!isfinite(area)||fabs(area)<1e-10) { b->texture->normal->degenerate++; b->issue="degenerate UV triangle; normal conversion withheld"; return 0; }
        lowx=fmin(x[0],fmin(x[1],x[2])); highx=fmax(x[0],fmax(x[1],x[2]));
        lowy=fmin(y[0],fmin(y[1],y[2])); highy=fmax(y[0],fmax(y[1],y[2]));
        if(lowx< -1e8||lowy< -1e8||highx>1e8||highy>1e8||(highx-lowx+1)*(highy-lowy+1)>64000000) { b->issue="normal UV raster budget exceeded"; return 0; }
        x0=(int)ceil(lowx-.5); x1=(int)floor(highx-.5); y0=(int)ceil(lowy-.5); y1=(int)floor(highy-.5);
        if(x1<x0||y1<y0) continue;
        work+=(size_t)(x1-x0+1)*(size_t)(y1-y0+1);
        if(work>128000000) { b->issue="normal UV raster budget exceeded"; return 0; }
        for(iy=y0;iy<=y1;iy++) for(ix=x0;ix<=x1;ix++) {
            double w[3]; int tx,ty; size_t index;
            w[0]=edge(x[1],y[1],x[2],y[2],ix+.5,iy+.5)/area;
            w[1]=edge(x[2],y[2],x[0],y[0],ix+.5,iy+.5)/area; w[2]=1-w[0]-w[1];
            if(w[0]<-1e-9||w[1]<-1e-9||w[2]<-1e-9) continue;
            /* Deterministic half-open ownership for exact shared edges. */
            for(k=0;k<3;k++) if(fabs(w[k])<=1e-9) {
                size_t a=(k+1)%3,c=(k+2)%3; double dx=x[c]-x[a],dy=y[c]-y[a];
                if(area<0) { dx=-dx; dy=-dy; }
                if(!(dy>0||(dy==0&&dx<0))) break;
            }
            if(k<3) continue;
            tx=ix%width; ty=iy%height; if(tx<0) tx+=width; if(ty<0) ty+=height;
            index=(size_t)ty*width+tx;
            if(inference) score(b,v,w,index); else if(!bake_pixel(b,v,w,index)) return 0;
        }
    }
    return 1;
}
static void inference(Bake *b) {
    LWNormalConversion *s=b->texture->normal; size_t k;
    s->samples=b->valid;
    if(!b->valid) return;
    s->object_mean_degrees=b->scores[0]/b->valid; s->runner_up_degrees=180;
    for(k=1;k<48;k++) s->runner_up_degrees=fmin(s->runner_up_degrees,b->scores[k]/b->valid);
    s->object_hemisphere=(double)b->object_hemisphere/b->valid; s->tangent_hemisphere=(double)b->tangent_hemisphere/b->valid;
    s->normal_diversity=1-sqrt(dot(b->sum_normal,b->sum_normal))/b->valid;
    if(b->valid>=128&&b->valid>=.9*b->interior&&s->normal_diversity>.2&&s->object_mean_degrees<40&&
       s->runner_up_degrees-s->object_mean_degrees>10&&s->object_hemisphere>.95&&s->tangent_hemisphere<.9) s->effective_space=1;
}
static int pad(Bake *b,LWError *e) {
    int w=b->image->width,h=b->image->height,pass,x,y; size_t count=(size_t)w*h;
    unsigned char *next=malloc(count);
    if(!next) return lw_error(e,0,"allocation","out of memory");
    /* Four rings, nearest existing texel, no averaging opposite island vectors.
       Synchronous rounds make the choice deterministic; repeat across edges. */
    for(pass=0;pass<4;pass++) {
        memcpy(next,b->covered,count);
        for(y=0;y<h;y++) for(x=0;x<w;x++) {
            size_t at=(size_t)y*w+x,neighbors[4]={(size_t)y*w+(x?x-1:w-1),(size_t)y*w+(x+1==w?0:x+1),(size_t)(y?y-1:h-1)*w+x,(size_t)(y+1==h?0:y+1)*w+x},k;
            if(b->covered[at]) continue;
            for(k=0;k<4;k++) if(b->covered[neighbors[k]]) { memcpy(b->rgba+4*at,b->rgba+4*neighbors[k],4); next[at]=1; b->texture->normal->padded++; break; }
        }
        memcpy(b->covered,next,count);
    }
    free(next); return 1;
}
int lw_prepare_normal_maps(const char *dir,const char *output,LWObject *o,const LWOptions *opts,LWError *e) {
    LWNormals normals={0}; size_t ti,i,j; int ok=0;
    for(ti=0;ti<o->textures.n;ti++) if(o->textures.v[ti].clip_scope) break;
    if(ti==o->textures.n) return 1;
    if(!lw_corner_normals(o,&normals,e)) return 0;
    o->normal_first=malloc((o->primitives.n+1)*sizeof *o->normal_first);
    if(!o->normal_first) { lw_error(e,0,"allocation","out of memory"); goto done; }
    for(i=0;i<o->primitives.n;i++) o->normal_first[i]=SIZE_MAX;
    for(;ti<o->textures.n;ti++) {
        LWTexture *t=&o->textures.v[ti]; LWMaterial *m; LWNormalConversion *s; Bake b={0};
        LWRenderVertices mesh={0}; LWUV *uv=NULL; char *name; const char *issue; size_t count,start=o->normal_vertices.n; int fatal=0;
        if(!t->clip_scope) continue;
        m=&o->materials.v[t->material]; s=t->normal=calloc(1,sizeof *s);
        if(!s) { lw_error(e,0,"allocation","out of memory"); goto done; }
        s->requested_space=opts->normal_space; s->effective_space=opts->normal_space==0?0:opts->normal_space;
        s->green_negative=opts->normal_green_negative; s->world_matrix_set=opts->normal_world_matrix_set;
        memcpy(s->world_matrix,opts->normal_world_matrix,sizeof s->world_matrix);
        issue=qualify(o,t,opts);
        if(issue) { if(issue!=t->issue) snprintf(t->issue,sizeof t->issue,"%s",issue); continue; }
        name=lw_text(t->uv_map); if(!name) { lw_error(e,0,"allocation","out of memory"); goto done; }
        uv=lw_corner_uvs(o,name,e); free(name); if(!uv) goto done;
        for(i=0;i<o->primitives.n&&!issue;i++) {
            const LWPrimitive *p=&o->primitives.v[i]; LWTriangulation tri={0}; int status;
            if(p->material!=t->material||p->detail_parent!=LW_NONE||p->legacy_surface<0) continue;
            if(p->count<3||(p->type!=TAG("FACE")&&p->type!=TAG("PTCH")&&p->type!=TAG("PCHS"))) continue;
            for(j=0;j<p->count;j++) if(!uv[p->first+j].valid) { issue="missing TXUV/VMAD corners for normal map"; break; }
            if(issue) break;
            status=lw_triangulate(o,p,&tri,e);
            if(status<=0) { lw_free_triangulation(&tri); if(status<0) fatal=1; else issue="normal material contains an untriangulatable polygon"; break; }
            for(j=0;j<tri.corners.n;j+=3) {
                LWRenderVertex v[3]; size_t k;
                if(!lw_render_triangle(o,(uint32_t)i,tri.corners.v+j,uv,&normals,v,e)) { fatal=1; break; }
                for(k=0;k<3;k++) if(!LW_ADD(mesh,v[k],e)) { fatal=1; break; }
                if(fatal) break;
            }
            lw_free_triangulation(&tri); if(fatal) break;
        }
        free(uv);
        if(fatal) { LW_FREE(mesh); goto done; }
        if(!issue&&!mikktspace(&mesh)) issue="cannot generate MikkTSpace tangents for normal material";
        b.texture=t; b.image=lw_texture_pixels(o,t->image);
        count=(size_t)b.image->width*b.image->height;
        if(!issue&&count>16777216) issue="normal bake exceeds 16 megapixel budget";
        if(!issue) {
            b.covered=calloc(count,1); b.rgba=malloc(count*4);
            if(!b.covered||!b.rgba) { lw_error(e,0,"allocation","out of memory"); fatal=1; }
            else {
                for(i=0;i<count;i++) { b.rgba[4*i]=b.rgba[4*i+1]=128; b.rgba[4*i+2]=b.rgba[4*i+3]=255; }
                if(!raster(&b,&mesh,1)) issue=b.issue;
                else {
                    if(opts->normal_space==0) inference(&b);
                    else { int selected=s->effective_space; inference(&b); s->effective_space=selected; }
                    if(!s->effective_space) issue="normal space ambiguous; use --normal-space object/world/tangent with known source conventions";
                }
                memset(b.covered,0,count);
                if(!issue&&!raster(&b,&mesh,0)) issue=b.issue;
                if(!issue&&s->conflicts) issue="overlapping/repeated UVs require conflicting tangent normals; conversion withheld";
                if(!issue&&(!s->covered||s->invalid)) issue="normal texture has no valid coverage or invalid covered vectors";
                if(!issue&&!pad(&b,e)) fatal=1;
                if(!issue&&!fatal&&!lw_save_texture(dir,output,b.rgba,b.image->width,b.image->height,&m->normal_texture,e)) fatal=1;
            }
        }
        if(!issue&&!fatal) {
            for(i=0;i<mesh.n;i++) {
                if(!LW_ADD(o->normal_vertices,mesh.v[i],e)) { fatal=1; break; }
                if(i==0||mesh.v[i].polygon!=mesh.v[i-1].polygon) o->normal_first[mesh.v[i].polygon]=start+i;
            }
            if(!fatal) {
                t->supported=1; m->textured=1;
                if(m->projection_texture==SIZE_MAX) m->projection_texture=ti;
                for(i=0;i<o->textures.n;i++) if(o->textures.v[i].offset==t->clip_scope-1) { o->textures.v[i].supported=1; o->textures.v[i].issue[0]=0; }
            }
        }
        if(issue) snprintf(t->issue,sizeof t->issue,"%s",issue);
        free(b.covered); free(b.rgba); LW_FREE(mesh);
        if(fatal) goto done;
    }
    ok=1;
done:
    lw_free_normals(&normals); return ok;
}
void lw_json_normal_conversion(FILE *f,const LWNormalConversion *s) {
    size_t k;
    fprintf(f,"{\"profile\":\"NormalShader-UV-MikkTSpace-v1\",\"requested_space\":\"%s\",\"effective_space\":\"%s\",\"basis\":\"interpolated vertex T,B,N; normalized columns; full inverse; V-up bitangent\",\"source_green\":\"%s\",\"space_evidence\":\"%s\",\"source_world_matrix\":",lw_normal_space_name(s->requested_space),s->effective_space?lw_normal_space_name(s->effective_space):"undetermined",s->green_negative?"negative":"positive",s->requested_space?"explicit override":s->effective_space==1?"object-aligned hypothesis; equivalent world bake cannot be distinguished":"abstained");
    if(s->world_matrix_set) { fputc('[',f); for(k=0;k<9;k++) fprintf(f,"%s%.17g",k?",":"",s->world_matrix[k]); fputc(']',f); } else fputs("null",f);
    fprintf(f,",\"inference\":{\"samples\":%zu,\"object_mean_degrees\":%.9g,\"best_alternative_axes_degrees\":%.9g,\"object_hemisphere\":%.9g,\"tangent_positive_z\":%.9g,\"normal_diversity\":%.9g},\"raster\":{\"covered_texels\":%zu,\"overlap_samples\":%zu,\"conflicts\":%zu,\"invalid_vectors\":%zu,\"degenerate_uvs\":%zu,\"padding_texels\":%zu,\"quantized_roundtrip_max_degrees\":%.9g}}",s->samples,s->object_mean_degrees,s->runner_up_degrees,s->object_hemisphere,s->tangent_hemisphere,s->normal_diversity,s->covered,s->overlaps,s->conflicts,s->invalid,s->degenerate,s->padded,s->roundtrip_max_degrees);
}

