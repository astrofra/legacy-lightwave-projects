#include "internal.h"
#include <math.h>

/* Direct glTF 2.0 writer: native geometry and sampled local scene transforms. */
typedef LWRenderVertex GVertex;
typedef struct { uint32_t material,mode; int uv; LW_ARRAY(GVertex) vertices; } GGroup;
typedef LW_ARRAY(GGroup) GGroups;
typedef struct {
    char *name;
    uint32_t kind,external_item;
    float *positions;
    LWNormals normals;
} GMorphTarget;
typedef LW_ARRAY(GMorphTarget) GMorphTargets;
typedef struct {
    size_t offset,map_offset,count,accessor,material; uint32_t mode,stride;
    size_t skin_offset,skin_view,skin_accessor,skin_sets;
    size_t morph_offset,morph_view,morph_accessor,morph_targets,morph_stride;
    float *morph_low,*morph_high;
    int uv,tangent; float low[3],high[3];
} GPrimitive;
typedef struct { size_t asset,first,count; uint32_t layer,node; GMorphTargets targets; } GMesh;
typedef struct { size_t asset; uint32_t index; int uv; size_t base,emissive,specular,normal; const LWClipBinding *clip; } GMaterial;
typedef struct { size_t source,mesh,parent; int skinned,animated; double matrix[16],trs[10]; float *weights; size_t weight_count; } GNode;
typedef struct { size_t node,offset[3],accessor[3]; float *samples; } GAnimation;
typedef struct { size_t node,offset,accessor,count; float *samples; } GMorphAnimation;
typedef struct {
    const LWPackage *package; const LWOptions *options; LWGltfStats *stats;
    FILE *bin; size_t bytes,accessors;
    const LWRig *rig;
    int autonomous;
    size_t skin_views,morph_views,bind_offset,bind_accessor;
    LW_ARRAY(GPrimitive) primitives;
    LW_ARRAY(GMesh) meshes;
    LW_ARRAY(GMaterial) materials;
    LW_ARRAY(const char *) textures;
    LW_ARRAY(GNode) nodes;
    LW_ARRAY(GAnimation) animation;
    LW_ARRAY(GMorphAnimation) morph_animation;
    size_t sample_count,time_offset,time_accessor;
    double first_frame,last_frame;
    float duration;
} GDocument;

static double unit(double x) { return x<0?0:x>1?1:x; }
static int selected(const LWObject *o,uint32_t layer,uint32_t request) {
    return request==LW_NONE||o->layers.v[layer].id==request-1;
}
static void u32(FILE *f,uint32_t v) {
    unsigned char b[4]={(unsigned char)v,(unsigned char)(v>>8),(unsigned char)(v>>16),(unsigned char)(v>>24)};
    fwrite(b,1,4,f);
}
static void f32(FILE *f,float v) { uint32_t bits; memcpy(&bits,&v,4); u32(f,bits); }
static void u16(FILE *f,uint16_t v) { fputc(v&255,f); fputc(v>>8,f); }
static GGroup *group(GGroups *groups,uint32_t material,uint32_t mode,int uv,LWError *e) {
    size_t i; GGroup g={0};
    for(i=0;i<groups->n;i++) if(groups->v[i].material==material&&groups->v[i].mode==mode&&groups->v[i].uv==uv) return &groups->v[i];
    g.material=material; g.mode=mode; g.uv=uv;
    if(!LW_ADD(*groups,g,e)) return NULL;
    return &groups->v[groups->n-1];
}
static GVertex vertex(const LWObject *o,uint32_t polygon,uint32_t corner,const LWUV *uv) {
    GVertex v={0}; uint32_t index=o->primitives.v[polygon].first+corner;
    v.point=o->indices.v[index]; v.polygon=polygon; v.corner=corner;
    memcpy(v.p,o->positions.v+3*v.point,sizeof v.p); v.p[2]=-v.p[2];
    if(uv) { v.uv[0]=uv[index].u; v.uv[1]=(float)(1.0-uv[index].v); }
    return v;
}
static int triangle(GGroup *g,const LWObject *o,uint32_t polygon,const uint32_t corners[3],size_t triangle_corner,const LWUV *uv,const LWNormals *normals,LWError *e) {
    GVertex v[3]; size_t i;
    if(uv&&o->normal_first&&o->normal_first[polygon]!=SIZE_MAX) {
        memcpy(v,o->normal_vertices.v+o->normal_first[polygon]+triangle_corner,sizeof v);
    } else if(!lw_render_triangle(o,polygon,corners,uv,normals,v,e)) return 0;
    for(i=0;i<3;i++) if(!LW_ADD(g->vertices,v[i],e)) return 0;
    return 1;
}
static int derive(GDocument *doc,size_t asset,uint32_t request,GGroups *groups,LWError *e) {
    const LWObject *o=&doc->package->objects.v[asset]; LWExportStats *s=&doc->stats->geometry;
    LWUV *uv=NULL; LWNormals normals={0}; unsigned char *used=calloc(o->positions.n/3+1,1); size_t i,j; int ok=0;
    if(!used) return lw_error(e,0,"allocation","out of memory");
    uv=doc->options->uv_map?lw_corner_uvs(o,doc->options->uv_map,e):lw_texture_uvs(o,e); if(!uv) goto done;
    if(!lw_corner_normals(o,&normals,e)) goto done;
    s->normal_issues+=normals.issues;
    for(i=0;i<o->primitives.n;i++) {
        const LWPrimitive *p=&o->primitives.v[i]; GGroup *g; int has_uv=doc->options->uv_map||(p->material<o->materials.n&&o->materials.v[p->material].textured);
        int curve=p->type==LW_TAG('C','U','R','V'),face=p->type==LW_TAG('F','A','C','E');
        int cage=p->type==LW_TAG('P','C','H','S')||p->type==LW_TAG('P','T','C','H');
        if(!selected(o,o->polygon_blocks.v[p->block].layer,request)) continue;
        if(!p->count||p->detail_parent!=LW_NONE||p->legacy_surface<0||(!face&&!curve&&!cage)) { s->skipped++; continue; }
        if(cage) s->cages++;
        if(curve) s->control_curves++;
        if((face||cage)&&p->count>=3) {
            LWTriangulation t={0}; int status=lw_triangulate(o,p,&t,e);
            if(status<0) { lw_free_triangulation(&t); goto done; }
            if(!status) { s->skipped++; s->triangulation_failures++; lw_free_triangulation(&t); continue; }
            if(has_uv) for(j=0;j<p->count;j++) if(!uv[p->first+j].valid) { has_uv=0; s->uv_missing++; }
            g=group(groups,p->material,4,has_uv,e);
            if(!g) { lw_free_triangulation(&t); goto done; }
            for(j=0;j<t.corners.n;j+=3) if(!triangle(g,o,(uint32_t)i,t.corners.v+j,j,has_uv?uv:NULL,&normals,e)) { lw_free_triangulation(&t); goto done; }
            for(j=0;j<t.corners.n;j++) used[o->indices.v[p->first+t.corners.v[j]]]=1;
            s->triangles+=t.corners.n/3; s->triangulated_faces+=p->count>3;
            s->bridged_faces+=t.bridges!=0; s->nonplanar_faces+=t.nonplanar!=0; s->removed_corners+=t.removed_corners;
            lw_free_triangulation(&t);
        } else {
            uint32_t mode=p->count==1?0:1;
            g=group(groups,p->material,mode,0,e); if(!g) goto done;
            if(mode==0) {
                GVertex v=vertex(o,(uint32_t)i,0,NULL);
                if(!LW_ADD(g->vertices,v,e)) goto done;
                used[v.point]=1; doc->stats->points++;
            } else for(j=1;j<p->count;j++) {
                GVertex a=vertex(o,(uint32_t)i,(uint32_t)j-1,NULL),b=vertex(o,(uint32_t)i,(uint32_t)j,NULL);
                if(!LW_ADD(g->vertices,a,e)||!LW_ADD(g->vertices,b,e)) goto done;
                used[a.point]=used[b.point]=1; doc->stats->lines++;
            }
        }
    }
    for(i=0;i<o->point_blocks.n;i++) {
        const LWPointBlock *block=&o->point_blocks.v[i];
        if(!selected(o,block->layer,request)) continue;
        for(j=0;j<block->count;j++) if(!used[block->first+j]) {
            GGroup *g=group(groups,LW_NONE,0,0,e); GVertex v={0};
            if(!g) goto done;
            v.point=block->first+(uint32_t)j; v.polygon=v.corner=LW_NONE;
            memcpy(v.p,o->positions.v+3*v.point,sizeof v.p); v.p[2]=-v.p[2];
            if(!LW_ADD(g->vertices,v,e)) goto done;
            doc->stats->points++;
        }
    }
    for(i=0;i<o->chunks.n;i++) if(o->chunks.v[i].tag==LW_TAG('C','R','V','S')) s->skipped++;
    ok=1;
done:
    free(uv); free(used); lw_free_normals(&normals); return ok;
}
static int target_name_equal(const GMorphTarget *target,LWString name) {
    size_t n=strlen(target->name);
    return n==name.size&&!memcmp(target->name,name.data,n);
}
static int target_normals(const LWObject *base,GMorphTarget *target,LWError *e) {
    LWObject deformed=*base;
    deformed.positions.v=target->positions;
    deformed.maps.n=0;
    deformed.normal_first=NULL;
    deformed.normal_vertices.v=NULL; deformed.normal_vertices.n=deformed.normal_vertices.cap=0;
    return lw_corner_normals(&deformed,&target->normals,e);
}
static int add_map_target(GDocument *doc,size_t asset,uint32_t request,const LWMap *map,GMorphTargets *targets,LWError *e) {
    const LWObject *o=&doc->package->objects.v[asset]; const LWPointBlock *block; GMorphTarget *target=NULL; size_t i,j,points=o->positions.n/3;
    if(map->dimension!=3||map->discontinuous||map->point_block>=o->point_blocks.n) { doc->stats->morph_issues++; return 1; }
    block=&o->point_blocks.v[map->point_block];
    if(!selected(o,block->layer,request)) return 1;
    for(i=0;i<targets->n;i++) if(target_name_equal(&targets->v[i],map->name)) {
        if(targets->v[i].kind!=map->type) { doc->stats->morph_issues++; return 1; }
        target=&targets->v[i]; break;
    }
    if(!target) {
        GMorphTarget value={0}; value.kind=map->type; value.external_item=LW_NONE;
        value.name=lw_text(map->name); value.positions=malloc((points?points:1)*3*sizeof(float));
        if(!value.name||!value.positions) { free(value.name); free(value.positions); return lw_error(e,0,"allocation","out of memory building morph target"); }
        memcpy(value.positions,o->positions.v,points*3*sizeof(float));
        if(!LW_ADD(*targets,value,e)) { free(value.name); free(value.positions); return 0; }
        target=&targets->v[targets->n-1];
    }
    for(j=0;j<map->entries.n;j++) {
        uint32_t local=map->entries.v[j].point; size_t point,k;
        if(local>=block->count) { doc->stats->morph_issues++; continue; }
        point=block->first+local;
        for(k=0;k<3;k++) if(!isfinite(map->values.v[3*j+k])) break;
        if(k<3) { doc->stats->morph_issues++; continue; }
        for(k=0;k<3;k++) {
            float value=map->values.v[3*j+k];
            target->positions[3*point+k]=map->type==LW_TAG('M','O','R','F')?o->positions.v[3*point+k]+value:value;
        }
    }
    return 1;
}
static int compatible_external(const LWObject *a,const LWObject *b) {
    size_t i;
    if(a->positions.n!=b->positions.n||a->indices.n!=b->indices.n||a->primitives.n!=b->primitives.n) return 0;
    if(a->indices.n&&memcmp(a->indices.v,b->indices.v,a->indices.n*sizeof(uint32_t))) return 0;
    for(i=0;i<a->primitives.n;i++) if(a->primitives.v[i].first!=b->primitives.v[i].first||a->primitives.v[i].count!=b->primitives.v[i].count||a->primitives.v[i].type!=b->primitives.v[i].type) return 0;
    return 1;
}
static int build_morph_targets(GDocument *doc,size_t asset,uint32_t request,uint32_t node,GMorphTargets *targets,LWError *e) {
    const LWObject *o=&doc->package->objects.v[asset]; size_t i;
    for(i=0;i<o->maps.n;i++) {
        const LWMap *map=&o->maps.v[i];
        if(map->type==LW_TAG('M','O','R','F')||map->type==LW_TAG('S','P','O','T')) if(!add_map_target(doc,asset,request,map,targets,e)) return 0;
    }
    if(node!=LW_NONE&&doc->package->is_scene) {
        const LWScene *scene=&doc->package->scene; const LWNode *owner=NULL,*other=NULL;
        for(i=0;i<scene->nodes.n;i++) if(scene->nodes.v[i].id==node) { owner=&scene->nodes.v[i]; break; }
        if(owner&&owner->morph_target!=LW_NONE) {
            for(i=0;i<scene->nodes.n;i++) if(scene->nodes.v[i].id==owner->morph_target) { other=&scene->nodes.v[i]; break; }
            if(owner->mtse_morphing||!other||other->asset==SIZE_MAX||!compatible_external(o,&doc->package->objects.v[other->asset])) doc->stats->morph_issues++;
            else {
                GMorphTarget target={0}; size_t bytes=o->positions.n*sizeof(float),length=48;
                target.kind=LW_TAG('O','B','J','T'); target.external_item=owner->morph_target;
                target.name=malloc(length); target.positions=malloc(bytes?bytes:sizeof(float));
                if(!target.name||!target.positions) { free(target.name); free(target.positions); return lw_error(e,0,"allocation","out of memory building external morph target"); }
                snprintf(target.name,length,"ObjectMorph_%08x",owner->morph_target);
                memcpy(target.positions,doc->package->objects.v[other->asset].positions.v,bytes);
                if(!LW_ADD(*targets,target,e)) { free(target.name); free(target.positions); return 0; }
                if(owner->morph_surfaces) doc->stats->morph_issues++;
            }
        }
    }
    if(targets->n>256) return lw_error(e,0,"morph","more than 256 morph targets on one mesh");
    for(i=0;i<targets->n;i++) if(!target_normals(o,&targets->v[i],e)) return 0;
    return 1;
}
static void free_morph_targets(GMorphTargets *targets) {
    size_t i;
    for(i=0;i<targets->n;i++) { free(targets->v[i].name); free(targets->v[i].positions); lw_free_normals(&targets->v[i].normals); }
    LW_FREE(*targets);
}
static int same_targets(const GMesh *mesh,const GMorphTargets *targets) {
    size_t i;
    if(mesh->targets.n!=targets->n) return 0;
    for(i=0;i<targets->n;i++) if(mesh->targets.v[i].kind!=targets->v[i].kind||mesh->targets.v[i].external_item!=targets->v[i].external_item||strcmp(mesh->targets.v[i].name,targets->v[i].name)) return 0;
    return 1;
}
static int texture_index(GDocument *doc,const char *uri,size_t *index,LWError *e) {
    size_t i; *index=SIZE_MAX; if(!uri) return 1;
    for(i=0;i<doc->textures.n;i++) if(!strcmp(doc->textures.v[i],uri)) { *index=i; return 1; }
    *index=doc->textures.n; return LW_ADD(doc->textures,uri,e);
}
static int same_clip(const LWClipBinding *a,const LWClipBinding *b) {
    return (!a&&!b)||(a&&b&&!strcmp(a->base_texture,b->base_texture));
}
static int material_index(GDocument *doc,size_t asset,uint32_t index,int uv,uint32_t node,size_t *result,LWError *e) {
    GMaterial m={0}; size_t i; m.asset=asset; m.index=index; m.uv=uv; m.base=m.emissive=m.specular=m.normal=SIZE_MAX;
    m.clip=uv?lw_clip_binding(&doc->package->objects.v[asset],node,index):NULL;
    for(i=0;i<doc->materials.n;i++) if(doc->materials.v[i].asset==asset&&doc->materials.v[i].index==index&&doc->materials.v[i].uv==uv&&same_clip(doc->materials.v[i].clip,m.clip)) { *result=i; return 1; }
    *result=doc->materials.n;
    if(index<doc->package->objects.v[asset].materials.n) {
        uint32_t side=doc->package->objects.v[asset].materials.v[index].side;
        const LWMaterial *native=&doc->package->objects.v[asset].materials.v[index];
        if(side!=1&&side!=3) doc->stats->unsupported_sidedness++;
        if(uv&&(!texture_index(doc,m.clip?m.clip->base_texture:native->base_texture,&m.base,e)||!texture_index(doc,native->emissive_texture,&m.emissive,e)||!texture_index(doc,native->specular_texture,&m.specular,e)||!texture_index(doc,native->normal_texture,&m.normal,e))) return 0;
    }
    return LW_ADD(doc->materials,m,e);
}
static int write_group(GDocument *doc,const GGroup *g,size_t asset,uint32_t node,const GMorphTargets *targets,LWError *e) {
    GPrimitive p={0}; size_t i,j,bytes; uint32_t stride=12+(g->mode==4?12:0)+(g->uv?8:0);
    if(!g->vertices.n) return 1;
    p.tangent=g->mode==4&&g->uv&&g->material<doc->package->objects.v[asset].materials.n&&doc->package->objects.v[asset].materials.v[g->material].normal_texture!=NULL;
    if(p.tangent) stride+=16;
    if(g->vertices.n>UINT32_MAX/(stride+12)) return lw_error(e,0,"glTF","primitive buffer exceeds 4 GiB");
    bytes=g->vertices.n*(stride+12);
    if(doc->rig&&doc->rig->weighted) {
        size_t skin_stride;
        p.skin_sets=doc->rig->influence_sets;
        if(p.skin_sets>UINT32_MAX/24) return lw_error(e,0,"skin","too many influence sets");
        skin_stride=24*p.skin_sets;
        if(g->vertices.n>(UINT32_MAX-bytes)/skin_stride) return lw_error(e,0,"skin","skin buffer exceeds 4 GiB");
        bytes+=g->vertices.n*skin_stride;
    }
    p.morph_targets=targets->n; p.morph_stride=g->mode==4?24:12;
    if(p.morph_targets) {
        size_t morph_bytes;
        if(g->vertices.n>UINT32_MAX/p.morph_stride||p.morph_targets>UINT32_MAX/(g->vertices.n*p.morph_stride)) return lw_error(e,0,"morph","morph buffer exceeds 4 GiB");
        morph_bytes=p.morph_targets*g->vertices.n*p.morph_stride;
        if(morph_bytes>UINT32_MAX-bytes) return lw_error(e,0,"morph","morph buffer exceeds 4 GiB");
        bytes+=morph_bytes;
    }
    if(doc->bytes>UINT32_MAX-bytes) return lw_error(e,0,"glTF","document buffer exceeds 4 GiB");
    p.offset=doc->bytes; p.map_offset=p.offset+g->vertices.n*stride; p.count=g->vertices.n;
    p.mode=g->mode; p.uv=g->uv; p.stride=stride; p.accessor=doc->accessors;
    if(!material_index(doc,asset,g->material,g->uv,node,&p.material,e)) return 0;
    memcpy(p.low,g->vertices.v[0].p,sizeof p.low); memcpy(p.high,p.low,sizeof p.high);
    for(i=0;i<g->vertices.n;i++) {
        const GVertex *v=&g->vertices.v[i];
        for(j=0;j<3;j++) { f32(doc->bin,v->p[j]); p.low[j]=fminf(p.low[j],v->p[j]); p.high[j]=fmaxf(p.high[j],v->p[j]); }
        if(g->mode==4) for(j=0;j<3;j++) f32(doc->bin,v->n[j]);
        if(g->uv) for(j=0;j<2;j++) f32(doc->bin,v->uv[j]);
        if(p.tangent) for(j=0;j<4;j++) f32(doc->bin,v->tangent[j]);
    }
    /* Source mapping is application data in the same binary buffer. It is not
       a glTF vertex attribute (uint32 is not a portable attribute encoding). */
    for(i=0;i<g->vertices.n;i++) {
        u32(doc->bin,g->vertices.v[i].polygon); u32(doc->bin,g->vertices.v[i].corner); u32(doc->bin,g->vertices.v[i].point);
    }
    if(p.skin_sets) {
        size_t set,k; p.skin_offset=p.map_offset+12*p.count; p.skin_view=doc->skin_views;
        p.skin_accessor=doc->accessors+1+(g->mode==4?1:0)+(g->uv?1:0)+(p.tangent?1:0);
        for(set=0;set<p.skin_sets;set++) for(i=0;i<g->vertices.n;i++) {
            const LWRigPoint *point=&doc->rig->points[g->vertices.v[i].point];
            for(k=0;k<4;k++) { size_t index=4*set+k; u16(doc->bin,index<point->count?doc->rig->influences.v[point->first+index].joint:0); }
            for(k=0;k<4;k++) { size_t index=4*set+k; f32(doc->bin,index<point->count?doc->rig->influences.v[point->first+index].weight:0); }
        }
        doc->skin_views+=p.skin_sets;
    }
    if(p.morph_targets) {
        size_t target_index; p.morph_offset=p.offset+g->vertices.n*stride+12*p.count+g->vertices.n*24*p.skin_sets;
        p.morph_low=malloc(p.morph_targets*3*sizeof(float)); p.morph_high=malloc(p.morph_targets*3*sizeof(float));
        if(!p.morph_low||!p.morph_high) { free(p.morph_low); free(p.morph_high); return lw_error(e,0,"allocation","out of memory computing morph bounds"); }
        p.morph_view=doc->morph_views;
        p.morph_accessor=doc->accessors+1+(g->mode==4?1:0)+(g->uv?1:0)+(p.tangent?1:0)+2*p.skin_sets;
        for(target_index=0;target_index<p.morph_targets;target_index++) {
            const GMorphTarget *target=&targets->v[target_index];
            for(i=0;i<g->vertices.n;i++) {
                const GVertex *v=&g->vertices.v[i]; const float *position=target->positions+3*v->point;
                float delta[3]={position[0]-v->p[0],position[1]-v->p[1],-position[2]-v->p[2]};
                for(j=0;j<3;j++) {
                    if(!isfinite(delta[j])) { free(p.morph_low); free(p.morph_high); return lw_error(e,0,"morph","position delta exceeds float32 range"); }
                    if(!i) p.morph_low[3*target_index+j]=p.morph_high[3*target_index+j]=delta[j];
                    else { p.morph_low[3*target_index+j]=fminf(p.morph_low[3*target_index+j],delta[j]); p.morph_high[3*target_index+j]=fmaxf(p.morph_high[3*target_index+j],delta[j]); }
                    f32(doc->bin,delta[j]);
                }
                if(g->mode==4) {
                    const LWPrimitive *source=&doc->package->objects.v[asset].primitives.v[v->polygon];
                    const LWNormal *normal=&target->normals.corners[source->first+v->corner];
                    float converted[3]={v->n[0],v->n[1],v->n[2]};
                    if(normal->valid) { converted[0]=normal->v[0]; converted[1]=normal->v[1]; converted[2]=-normal->v[2]; }
                    for(j=0;j<3;j++) f32(doc->bin,converted[j]-v->n[j]);
                }
            }
        }
        doc->morph_views+=p.morph_targets;
    }
    if(ferror(doc->bin)) { free(p.morph_low); free(p.morph_high); return lw_error(e,0,"glTF","cannot write geometry buffer"); }
    doc->bytes+=bytes; doc->accessors+=1+(g->mode==4?1:0)+(g->uv?1:0)+(p.tangent?1:0)+2*p.skin_sets+p.morph_targets*(g->mode==4?2:1);
    if(!LW_ADD(doc->primitives,p,e)) { free(p.morph_low); free(p.morph_high); return 0; }
    return 1;
}
static int mesh_index(GDocument *doc,size_t asset,uint32_t layer,uint32_t node,size_t *result,LWError *e) {
    size_t i,j; GGroups groups={0}; GMesh mesh={0}; int ok=0;
    const LWObject *o=&doc->package->objects.v[asset];
    mesh.asset=asset; mesh.first=doc->primitives.n; mesh.layer=layer; mesh.node=node;
    if(!build_morph_targets(doc,asset,layer,node,&mesh.targets,e)) goto done;
    for(i=0;i<doc->meshes.n;i++) if(doc->meshes.v[i].asset==asset&&doc->meshes.v[i].layer==layer) {
        for(j=0;j<o->materials.n;j++) if(!same_clip(lw_clip_binding(o,node,(uint32_t)j),lw_clip_binding(o,doc->meshes.v[i].node,(uint32_t)j))) break;
        if(j==o->materials.n&&same_targets(&doc->meshes.v[i],&mesh.targets)) { *result=i; free_morph_targets(&mesh.targets); return 1; }
    }
    if(!derive(doc,asset,layer,&groups,e)) goto done;
    for(i=0;i<groups.n;i++) if(!write_group(doc,&groups.v[i],asset,node,&mesh.targets,e)) goto done;
    mesh.count=doc->primitives.n-mesh.first;
    *result=SIZE_MAX;
    if(mesh.count) {
        *result=doc->meshes.n;
        if(!LW_ADD(doc->meshes,mesh,e)) goto done;
        doc->stats->morph_targets+=mesh.targets.n;
        mesh.targets.v=NULL; mesh.targets.n=mesh.targets.cap=0;
    }
    ok=1;
done:
    for(i=0;i<groups.n;i++) LW_FREE(groups.v[i].vertices);
    LW_FREE(groups); free_morph_targets(&mesh.targets); return ok;
}
static int morph_value(const LWScene *scene,const LWNode *node,const GMorphTarget *target,double frame,double *value) {
    size_t i; double time=scene->version==1?frame:frame/scene->fps;
    *value=0;
    if(target->kind==LW_TAG('O','B','J','T')) {
        if(node->morph_amount_envelope) return lw_channel_value(&node->morph_amount_channel,time,value);
        if(node->morph_amount_present) *value=node->morph_amount;
        return 1;
    }
    for(i=0;i<node->morph_forms.n;i++) {
        const LWMorphForm *form=&node->morph_forms.v[i]; size_t length=strlen(target->name);
        if(form->name.size==length&&!memcmp(form->name.data,target->name,length)) {
            if(form->has_envelope) return lw_channel_value(&form->envelope,time,value);
            *value=form->value; return isfinite(*value);
        }
    }
    return 1;
}
static int node_weights(GDocument *doc,GNode *node,double frame,LWError *e) {
    const GMesh *mesh; const LWNode *source; size_t i;
    if(node->mesh==SIZE_MAX) return 1;
    mesh=&doc->meshes.v[node->mesh]; if(!mesh->targets.n) return 1;
    node->weights=calloc(mesh->targets.n,sizeof(float));
    if(!node->weights) return lw_error(e,0,"allocation","out of memory building morph weights");
    node->weight_count=mesh->targets.n; source=&doc->package->scene.nodes.v[node->source];
    for(i=0;i<mesh->targets.n;i++) {
        double value;
        if(!morph_value(&doc->package->scene,source,&mesh->targets.v[i],frame,&value)||!isfinite((float)value)) {
            doc->stats->morph_issues++; value=0;
        }
        node->weights[i]=(float)value;
    }
    return 1;
}
static int fully_dissolved(const LWNode *node) {
    return node->object_dissolve_static&&node->object_dissolve_value>=1.0;
}
static int scene_nodes(GDocument *doc,LWError *e) {
    const LWScene *s=&doc->package->scene; size_t i,j,k,*map=NULL,*parents=NULL;
    unsigned char *active=NULL; int ok=0;
    map=malloc((s->nodes.n+1)*sizeof *map); parents=malloc((s->nodes.n+1)*sizeof *parents); active=calloc(s->nodes.n+1,1);
    if(!map||!parents||!active) { lw_error(e,0,"allocation","out of memory"); goto done; }
    for(i=0;i<s->nodes.n;i++) {
        map[i]=parents[i]=SIZE_MAX;
        if(s->nodes.v[i].parent!=LW_NONE) for(j=0;j<s->nodes.n;j++) if(s->nodes.v[j].id==s->nodes.v[i].parent) { parents[i]=j; break; }
    }
    /* The same resolved geometry and its parent chains are evaluated by OBJ. */
    for(i=0;i<s->nodes.n;i++) if(s->nodes.v[i].asset!=SIZE_MAX) {
        for(j=i,k=0;j!=SIZE_MAX&&!active[j];j=parents[j],k++) {
            if(k>s->nodes.n) { lw_error(e,0,"hierarchy","cyclic parent chain"); goto done; }
            active[j]=1;
        }
    }
    for(i=0;i<s->nodes.n;i++) if(active[i]) {
        GNode n={0}; const LWNode *source=&s->nodes.v[i]; double m[16];
        n.source=i; n.mesh=n.parent=SIZE_MAX;
        if(doc->autonomous) {
            lw_identity(m);
            for(j=0;j<10;j++) n.trs[j]=doc->package->bake.poses[i*20+j]*((j==2||j==3||j==4)?-1:1);
            n.animated=1;
        } else {
            double native[10];
            if(!lw_scene_node_matrix(s,i,doc->options->frame,m,e)||!lw_scene_node_trs(s,i,doc->options->frame,native,e)) goto done;
            for(j=0;j<10;j++) n.trs[j]=native[j]*((j==2||j==3||j==4)?-1:1);
            n.animated=1;
        }
        /* C M C with C=diag(1,1,-1,1), while geometry already uses C p. */
        for(j=0;j<16;j++) n.matrix[j]=m[j]*((j%4==2)^(j/4==2)?-1:1);
        if(source->asset!=SIZE_MAX&&!fully_dissolved(source)&&!mesh_index(doc,source->asset,source->layer,source->id,&n.mesh,e)) goto done;
        if(source->asset!=SIZE_MAX&&fully_dissolved(source)) doc->stats->hidden_dissolved_nodes++;
        if(n.mesh!=SIZE_MAX&&!node_weights(doc,&n,doc->options->frame,e)) goto done;
        map[i]=doc->nodes.n;
        if(!LW_ADD(doc->nodes,n,e)) { free(n.weights); goto done; }
    }
    for(i=0;i<s->nodes.n;i++) if(active[i]&&parents[i]!=SIZE_MAX) doc->nodes.v[map[i]].parent=map[parents[i]];
    doc->stats->omitted_nodes+=s->nodes.n-doc->nodes.n;
    ok=1;
done:
    free(map); free(parents); free(active); return ok;
}
static int rig_nodes(GDocument *doc,LWError *e) {
    const LWRig *rig=doc->rig; const LWNode *owner=&doc->package->scene.nodes.v[rig->owner];
    GNode root={0},mesh={0}; size_t i,j;
    root.source=rig->owner; root.mesh=root.parent=SIZE_MAX; lw_identity(root.matrix);
    LW_TRY(LW_ADD(doc->nodes,root,e));
    for(i=0;i<rig->joints.n;i++) {
        const LWRigJoint *joint=&rig->joints.v[i]; GNode node={0};
        node.source=joint->source; node.mesh=SIZE_MAX; node.parent=joint->parent==SIZE_MAX?0:joint->parent+1;
        for(j=0;j<16;j++) node.matrix[j]=joint->local[j]*((j%4==2)^(j/4==2)?-1:1);
        LW_TRY(LW_ADD(doc->nodes,node,e));
    }
    mesh.source=rig->owner; mesh.parent=SIZE_MAX; mesh.skinned=rig->weighted; lw_identity(mesh.matrix);
    LW_TRY(mesh_index(doc,rig->asset,owner->layer,owner->id,&mesh.mesh,e));
    LW_TRY(node_weights(doc,&mesh,doc->options->frame,e));
    if(!LW_ADD(doc->nodes,mesh,e)) { free(mesh.weights); return 0; }
    if(rig->weighted) {
        size_t bytes=(rig->joints.n+1)*64;
        if(bytes>UINT32_MAX-doc->bytes) return lw_error(e,0,"skin","inverse bind buffer exceeds 4 GiB");
        doc->bind_offset=doc->bytes; doc->bind_accessor=doc->accessors++;
        for(j=0;j<16;j++) f32(doc->bin,j%5==0?1.f:0.f);
        for(i=0;i<rig->joints.n;i++) for(j=0;j<16;j++) {
            double value=rig->joints.v[i].inverse_bind[j]*((j%4==2)^(j/4==2)?-1:1);
            if(!isfinite((float)value)) return lw_error(e,0,"skin","inverse bind matrix exceeds float32 range");
            f32(doc->bin,(float)value);
        }
        doc->bytes+=bytes;
        if(ferror(doc->bin)) return lw_error(e,0,"skin","cannot write inverse bind matrices");
    }
    return 1;
}
static int animation_trs(const LWScene *scene,size_t source,double frame,float trs[10],LWError *e) {
    double native[10]; size_t j;
    LW_TRY(lw_scene_node_trs(scene,source,frame,native,e));
    for(j=0;j<10;j++) {
        /* Reflect Z for translation, and X/Y for the rotation quaternion. */
        trs[j]=(float)(native[j]*((j==2||j==3||j==4)?-1:1));
        if(!isfinite(trs[j])) return lw_error(e,0,"animation","TRS exceeds float32 range");
    }
    return 1;
}
static void clear_animation(GDocument *doc) {
    size_t i;
    for(i=0;i<doc->animation.n;i++) free(doc->animation.v[i].samples);
    LW_FREE(doc->animation);
}
static int baked_animation(GDocument *doc,LWError *e) {
    const LWIKBake *b=&doc->package->bake; size_t i,j,k,sample;
    size_t count=doc->rig?doc->rig->joints.n+1:doc->nodes.n;
    doc->first_frame=b->first; doc->last_frame=b->last; doc->sample_count=b->samples;
    doc->duration=(float)((b->last-b->first)/doc->package->scene.fps);
    if((b->samples*10*count+b->samples)*4>UINT32_MAX-doc->bytes)
        return lw_error(e,0,"animation","skeletal animation exceeds 4 GiB");
    for(i=0;i<count;i++) {
        GNode *n=&doc->nodes.v[i]; GAnimation track={0}; track.node=i;
        track.samples=malloc(b->samples*10*sizeof(float));
        if(!track.samples) return lw_error(e,0,"allocation","cannot allocate skeletal animation");
        if(!LW_ADD(doc->animation,track,e)) { free(track.samples); return 0; }
        for(sample=0;sample<b->samples;sample++) {
            const float *source=b->poses+(sample*b->nodes+n->source)*20+(doc->rig&&i==0?10:0);
            float *dest=track.samples+sample*10; double dot=0;
            for(j=0;j<10;j++) dest[j]=source[j]*((j==2||j==3||j==4)?-1.f:1.f);
            if(sample) {
                for(j=3;j<7;j++) dot+=(double)dest[j]*(dest-10)[j];
                if(dot<0) for(j=3;j<7;j++) dest[j]=-dest[j];
            }
        }
        n->animated=1;
        for(j=0;j<10;j++) n->trs[j]=track.samples[j];
    }
    doc->time_offset=doc->bytes; doc->time_accessor=doc->accessors++;
    for(sample=0;sample<b->samples;sample++) f32(doc->bin,(float)((fmin(b->first+(double)sample,b->last)-b->first)/doc->package->scene.fps));
    doc->bytes+=b->samples*4;
    for(i=0;i<doc->animation.n;i++) {
        GAnimation *track=&doc->animation.v[i];
        for(k=0;k<3;k++) {
            size_t start=k==0?0:k==1?3:7,components=k==1?4:3;
            track->offset[k]=doc->bytes; track->accessor[k]=doc->accessors++;
            for(sample=0;sample<b->samples;sample++) for(j=0;j<components;j++) f32(doc->bin,track->samples[10*sample+start+j]);
            doc->bytes+=b->samples*components*4;
        }
    }
    doc->stats->animation_channels+=3*doc->animation.n; doc->stats->animation_samples=b->samples;
    if(ferror(doc->bin)) return lw_error(e,0,"animation","cannot write skeletal animation");
    return 1;
}
static int scene_animation(GDocument *doc,LWError *e) {
    const LWScene *scene=&doc->package->scene;
    size_t i,j,k,sample,handled=0,candidates=0; double first=scene->first_frame,last=scene->last_frame;
    LWError local={0}; float *times=NULL; size_t memory=0;
    for(i=0;i<doc->nodes.n;i++) {
        const LWNode *node=&scene->nodes.v[doc->nodes.v[i].source];
        if(node->mirrored_bank_follower) candidates++;
        for(j=0;j<node->channels.n;j++) if(node->channels.v[j].index<9&&node->channels.v[j].keys.n>1) candidates++;
    }
    if(!candidates) return 1;
    /* Scenes without a playback range use their exported nodes' key range. */
    if(first>=last) {
        int found=0;
        for(i=0;i<doc->nodes.n;i++) {
            const LWNode *node=&scene->nodes.v[doc->nodes.v[i].source];
            for(j=0;j<node->channels.n;j++) {
                const LWChannel *c=&node->channels.v[j];
                if(c->index>=9) continue;
                for(k=0;k<c->keys.n;k++) {
                    double frame=(c->keys.v[k].time+c->offset)*(scene->version==1?1:scene->fps);
                    if(!found||frame<first) first=frame;
                    if(!found||frame>last) last=frame;
                    found=1;
                }
            }
        }
    }
    if(!(last>first)||!isfinite(last-first)||last-first>100000) {
        lw_error(&local,0,"animation","playback range must span 1 to 100000 source-frame intervals"); goto unsupported;
    }
    doc->first_frame=first; doc->last_frame=last;
    doc->sample_count=(size_t)ceil(last-first)+1;
    times=malloc(doc->sample_count*sizeof *times);
    if(!times) return lw_error(e,0,"allocation","out of memory");
    for(sample=0;sample<doc->sample_count;sample++) {
        double frame=fmin(first+(double)sample,last);
        times[sample]=(float)((frame-first)/scene->fps);
        if(!isfinite(times[sample])||(sample&&times[sample]<=times[sample-1])) {
            lw_error(&local,0,"animation","sample times cannot be represented as increasing float32 seconds"); goto unsupported;
        }
    }
    doc->duration=times[doc->sample_count-1];
    for(i=0;i<doc->nodes.n;i++) {
        GAnimation track={0}; const LWNode *node=&scene->nodes.v[doc->nodes.v[i].source];
        int candidate=node->mirrored_bank_follower,changed=0;
        for(j=0;j<node->channels.n;j++) {
            const LWChannel *c=&node->channels.v[j];
            if(c->index>=9) continue;
            if(c->keys.n>1) { candidate=1; handled++; }
            /* Exact-key sampling alone must not hide unsupported interpolation. */
            for(k=1;k<c->keys.n;k++) if(c->keys.v[k].shape!=0&&c->keys.v[k].shape!=3&&c->keys.v[k].shape!=4) {
                lw_error(&local,node->source_offset,"animation","unsupported envelope shape %u",c->keys.v[k].shape); goto unsupported;
            }
        }
        if(!candidate) continue;
        if(doc->sample_count*10*sizeof(float)>64*1024*1024-memory) {
            lw_error(&local,0,"animation","sample buffer exceeds 64 MiB"); goto unsupported;
        }
        track.node=i; track.samples=malloc(doc->sample_count*10*sizeof(float));
        if(!track.samples) { lw_error(e,0,"allocation","out of memory"); goto failed; }
        if(!LW_ADD(doc->animation,track,e)) { free(track.samples); goto failed; }
        memory+=doc->sample_count*10*sizeof(float);
        for(sample=0;sample<doc->sample_count;sample++) {
            float *row=track.samples+sample*10;
            if(!animation_trs(scene,doc->nodes.v[i].source,fmin(first+(double)sample,last),row,&local)) goto unsupported;
            if(sample) {
                float *previous=row-10; double dot=0;
                for(j=3;j<7;j++) dot+=(double)row[j]*previous[j];
                if(dot<0) for(j=3;j<7;j++) row[j]=-row[j];
                for(j=0;j<10;j++) if(row[j]!=track.samples[j]) changed=1;
            }
        }
        if(!changed) { free(track.samples); doc->animation.n--; memory-=doc->sample_count*10*sizeof(float); }
    }
    for(i=0;i<doc->animation.n;i++) {
        GNode *node=&doc->nodes.v[doc->animation.v[i].node];
        if(!lw_scene_node_trs(scene,node->source,doc->options->frame,node->trs,&local)) goto unsupported;
        node->trs[2]=-node->trs[2]; node->trs[3]=-node->trs[3]; node->trs[4]=-node->trs[4];
    }
    if(!doc->animation.n) {
        doc->stats->animated_channels-=handled;
        doc->sample_count=0; doc->duration=0; doc->first_frame=doc->last_frame=0;
        free(times); return 1;
    }
    if(memory+doc->sample_count*4>UINT32_MAX-doc->bytes) {
        lw_error(&local,0,"animation","animation buffer exceeds 4 GiB"); goto unsupported;
    }
    doc->time_offset=doc->bytes; doc->time_accessor=doc->accessors++;
    for(sample=0;sample<doc->sample_count;sample++) f32(doc->bin,times[sample]);
    doc->bytes+=doc->sample_count*4;
    for(i=0;i<doc->animation.n;i++) {
        GAnimation *track=&doc->animation.v[i];
        doc->nodes.v[track->node].animated=1;
        for(k=0;k<3;k++) {
            size_t start=k==0?0:k==1?3:7,components=k==1?4:3;
            track->offset[k]=doc->bytes; track->accessor[k]=doc->accessors++;
            for(sample=0;sample<doc->sample_count;sample++) for(j=0;j<components;j++) f32(doc->bin,track->samples[10*sample+start+j]);
            doc->bytes+=doc->sample_count*components*4;
        }
    }
    if(ferror(doc->bin)) { lw_error(e,0,"animation","cannot write animation samples"); goto failed; }
    doc->stats->animated_channels-=handled;
    doc->stats->animation_channels=3*doc->animation.n; doc->stats->animation_samples=doc->sample_count;
    free(times); return 1;
unsupported:
    snprintf(doc->stats->animation_issue,sizeof doc->stats->animation_issue,"%.40s: %.210s",local.context,local.message);
    clear_animation(doc); doc->sample_count=0; doc->duration=0; free(times); return 1;
failed:
    free(times); return 0;
}
static void json_material(FILE *f,const GDocument *doc,const GMaterial *entry) {
    const LWObject *o=&doc->package->objects.v[entry->asset];
    const LWMaterial *m=entry->index<o->materials.n?&o->materials.v[entry->index]:NULL;
    double color[3]={.8,.8,.8},emissive[3]={0},alpha=1; size_t j; int two_sided=0;
    fputs("{\"name\":",f);
    if(m) { char *name=lw_text(m->name); lw_json_string(f,name?name:"surface"); free(name); }
    else lw_json_string(f,"Default surface");
    if(m) {
        for(j=0;j<3;j++) { color[j]=unit((double)m->color[j]*m->diffuse); emissive[j]=unit((double)m->color[j]*m->luminosity); }
        alpha=unit(1.0-m->transparency); two_sided=m->side==3||(o->format==LW_TAG('L','W','O','B')&&(m->flags&256));
    }
    if(entry->base!=SIZE_MAX) { for(j=0;j<3;j++) color[j]=1; alpha=1; }
    if(entry->emissive!=SIZE_MAX) for(j=0;j<3;j++) emissive[j]=1;
    fprintf(f,",\"pbrMetallicRoughness\":{\"baseColorFactor\":[%.9g,%.9g,%.9g,%.9g],\"metallicFactor\":0,\"roughnessFactor\":1",color[0],color[1],color[2],alpha);
    if(entry->base!=SIZE_MAX) fprintf(f,",\"baseColorTexture\":{\"index\":%zu}",entry->base);
    fprintf(f,"},\"emissiveFactor\":[%.9g,%.9g,%.9g],\"alphaMode\":\"%s\",\"doubleSided\":%s",emissive[0],emissive[1],emissive[2],entry->clip?"MASK":alpha<1||(entry->base!=SIZE_MAX&&m->texture_alpha)?"BLEND":"OPAQUE",two_sided?"true":"false");
    if(entry->clip) fputs(",\"alphaCutoff\":0.5",f);
    if(entry->normal!=SIZE_MAX) fprintf(f,",\"normalTexture\":{\"index\":%zu}",entry->normal);
    if(entry->emissive!=SIZE_MAX) fprintf(f,",\"emissiveTexture\":{\"index\":%zu}",entry->emissive);
    if(entry->specular!=SIZE_MAX) fprintf(f,",\"extensions\":{\"KHR_materials_specular\":{\"specularFactor\":1,\"specularTexture\":{\"index\":%zu}}}",entry->specular);
    fprintf(f,",\"extras\":{\"source_asset_index\":%zu,\"source_surface_index\":",entry->asset);
    if(m) fprintf(f,"%u",entry->index); else fputs("null",f);
    fprintf(f,",\"source_sha256\":\"%s\",\"source_sidedness\":%u",o->source.sha256,m?m->side:1);
    if(entry->clip) fprintf(f,",\"derived_clip_map\":{\"profile\":\"projected-image-clip-0.2\",\"scope\":\"%s\",\"evidence_count\":%zu,\"details\":\"object IR derived_clip_maps\"}",entry->clip->node==LW_NONE?"unanimous-object-preview":"scene-instance",entry->clip->evidence_count);
    fputs(",\"native_texture_bindings\":",f); lw_json_textures(f,o,entry->index);
    fprintf(f,",\"source_smoothing_angle_radians\":%.9g,\"effective_smoothing_angle_radians\":%.9g",m?m->smoothing:0,lw_smoothing_angle(o,entry->index,NULL,NULL));
    fputs(",\"interpretation\":\"color/diffuse/emission/opacity approximation; neutral rough dielectric; compatible LWOB projections and LWO2 UV image maps; height bump and environment reflection preserved in IR; source-corner normals carry smoothing\"}}",f);
}
static void texture_uri(FILE *f,const char *name) {
    const unsigned char *p=(const unsigned char *)name;
    fputc('"',f);
    for(;*p;p++) {
        if((*p>='a'&&*p<='z')||(*p>='A'&&*p<='Z')||(*p>='0'&&*p<='9')||strchr("/-._~",*p)) fputc(*p,f);
        else fprintf(f,"%%%02X",(unsigned)*p);
    }
    fputc('"',f);
}
static void clear_morph_animation(GDocument *doc) {
    size_t i;
    for(i=0;i<doc->morph_animation.n;i++) free(doc->morph_animation.v[i].samples);
    LW_FREE(doc->morph_animation);
}
static const LWChannel *morph_channel(const LWNode *node,const GMorphTarget *target) {
    size_t i;
    if(target->kind==LW_TAG('O','B','J','T')) return node->morph_amount_envelope?&node->morph_amount_channel:NULL;
    for(i=0;i<node->morph_forms.n;i++) {
        const LWMorphForm *form=&node->morph_forms.v[i]; size_t length=strlen(target->name);
        if(form->name.size==length&&!memcmp(form->name.data,target->name,length)) return form->has_envelope?&form->envelope:NULL;
    }
    return NULL;
}
static int morph_animation(GDocument *doc,LWError *e) {
    const LWScene *scene=&doc->package->scene; size_t i,j,k,sample,candidates=0; double first=scene->first_frame,last=scene->last_frame;
    for(i=0;i<doc->nodes.n;i++) if(doc->nodes.v[i].mesh!=SIZE_MAX) {
        const GMesh *mesh=&doc->meshes.v[doc->nodes.v[i].mesh]; const LWNode *node=&scene->nodes.v[doc->nodes.v[i].source];
        for(j=0;j<mesh->targets.n;j++) { const LWChannel *channel=morph_channel(node,&mesh->targets.v[j]); if(channel&&channel->keys.n>1) candidates++; }
    }
    if(!candidates) return 1;
    if(first>=last) {
        int found=0;
        for(i=0;i<doc->nodes.n;i++) if(doc->nodes.v[i].mesh!=SIZE_MAX) {
            const GMesh *mesh=&doc->meshes.v[doc->nodes.v[i].mesh]; const LWNode *node=&scene->nodes.v[doc->nodes.v[i].source];
            for(j=0;j<mesh->targets.n;j++) {
                const LWChannel *channel=morph_channel(node,&mesh->targets.v[j]); if(!channel) continue;
                for(k=0;k<channel->keys.n;k++) {
                    double frame=(channel->keys.v[k].time+channel->offset)*(scene->version==1?1:scene->fps);
                    if(!found||frame<first) first=frame;
                    if(!found||frame>last) last=frame;
                    found=1;
                }
            }
        }
    }
    if(!(last>first)||!isfinite(last-first)||last-first>100000) { doc->stats->morph_issues++; return 1; }
    if(doc->time_accessor==SIZE_MAX) {
        doc->first_frame=first; doc->last_frame=last; doc->sample_count=(size_t)ceil(last-first)+1;
        doc->duration=(float)((last-first)/scene->fps); doc->time_offset=doc->bytes; doc->time_accessor=doc->accessors++;
        if(doc->sample_count*4>UINT32_MAX-doc->bytes) return lw_error(e,0,"morph","morph time buffer exceeds 4 GiB");
        for(sample=0;sample<doc->sample_count;sample++) f32(doc->bin,(float)((fmin(first+(double)sample,last)-first)/scene->fps));
        doc->bytes+=doc->sample_count*4;
    } else { first=doc->first_frame; last=doc->last_frame; }
    for(i=0;i<doc->nodes.n;i++) if(doc->nodes.v[i].mesh!=SIZE_MAX) {
        const GMesh *mesh=&doc->meshes.v[doc->nodes.v[i].mesh]; const LWNode *node=&scene->nodes.v[doc->nodes.v[i].source]; GMorphAnimation track={0}; int candidate=0,changed=0,valid=1;
        for(j=0;j<mesh->targets.n;j++) { const LWChannel *channel=morph_channel(node,&mesh->targets.v[j]); if(channel&&channel->keys.n>1) candidate=1; }
        if(!candidate) continue;
        if(mesh->targets.n&&doc->sample_count>(64*1024*1024)/(mesh->targets.n*sizeof(float))) { doc->stats->morph_issues++; continue; }
        track.node=i; track.count=mesh->targets.n; track.samples=malloc(doc->sample_count*track.count*sizeof(float));
        if(!track.samples) return lw_error(e,0,"allocation","out of memory building morph animation");
        for(sample=0;sample<doc->sample_count&&valid;sample++) for(j=0;j<track.count;j++) {
            double value; float *destination=&track.samples[sample*track.count+j];
            if(!morph_value(scene,node,&mesh->targets.v[j],fmin(first+(double)sample,last),&value)||!isfinite((float)value)) { valid=0; break; }
            *destination=(float)value; if(sample&&*destination!=track.samples[j]) changed=1;
        }
        if(!valid||!changed) { doc->stats->morph_issues+=!valid; free(track.samples); continue; }
        if(doc->sample_count*track.count*4>UINT32_MAX-doc->bytes) { free(track.samples); return lw_error(e,0,"morph","morph animation exceeds 4 GiB"); }
        track.offset=doc->bytes; track.accessor=doc->accessors++;
        for(sample=0;sample<doc->sample_count*track.count;sample++) f32(doc->bin,track.samples[sample]);
        doc->bytes+=doc->sample_count*track.count*4;
        if(!LW_ADD(doc->morph_animation,track,e)) { free(track.samples); return 0; }
    }
    doc->stats->morph_animation_channels+=doc->morph_animation.n;
    if(doc->morph_animation.n&&doc->stats->animation_samples<doc->sample_count) doc->stats->animation_samples=doc->sample_count;
    if(ferror(doc->bin)) return lw_error(e,0,"morph","cannot write morph animation");
    return 1;
}
static void buffer_uri(FILE *f,const char *name) {
    const unsigned char *p=(const unsigned char *)name;
    fputc('"',f);
    for(;*p;p++) {
        if((*p>='a'&&*p<='z')||(*p>='A'&&*p<='Z')||(*p>='0'&&*p<='9')||strchr("-._~",*p)) fputc(*p,f);
        else fprintf(f,"%%%02X",(unsigned)*p);
    }
    fputs(".bin\"",f);
}
static void accessor(FILE *f,size_t view,size_t offset,size_t count,unsigned components,const float *low,const float *high) {
    fprintf(f,"{\"bufferView\":%zu,\"byteOffset\":%zu,\"componentType\":5126,\"count\":%zu,\"type\":\"VEC%u\"",view,offset,count,components);
    if(low) fprintf(f,",\"min\":[%.9g,%.9g,%.9g],\"max\":[%.9g,%.9g,%.9g]",low[0],low[1],low[2],high[0],high[1],high[2]);
    fputc('}',f);
}
static void json_animation(FILE *f,const GDocument *doc,const char *name) {
    static const char *paths[]={"translation","rotation","scale"}; size_t i,j,index=0;
    const char *profile=doc->autonomous?(doc->morph_views?"autonomous-hpb-ik-morph-0.3":"autonomous-hpb-ik-0.2"):
        doc->morph_views?"sampled-scene-and-morph-0.2":"sampled-scene-transforms-0.1";
    if(!doc->animation.n&&!doc->morph_animation.n) return;
    fputs(",\n\"animations\":[{\"name\":",f); lw_json_string(f,name);
    fputs(",\"samplers\":[",f);
    for(i=0;i<doc->animation.n;i++) for(j=0;j<3;j++) {
        if(index++) fputc(',',f);
        fprintf(f,"{\"input\":%zu,\"output\":%zu,\"interpolation\":\"LINEAR\"}",doc->time_accessor,doc->animation.v[i].accessor[j]);
    }
    for(i=0;i<doc->morph_animation.n;i++) {
        if(index++) fputc(',',f);
        fprintf(f,"{\"input\":%zu,\"output\":%zu,\"interpolation\":\"LINEAR\"}",doc->time_accessor,doc->morph_animation.v[i].accessor);
    }
    fputs("],\"channels\":[",f);
    index=0;
    for(i=0;i<doc->animation.n;i++) for(j=0;j<3;j++) {
        if(index) fputc(',',f);
        fprintf(f,"{\"sampler\":%zu,\"target\":{\"node\":%zu,\"path\":\"%s\"}}",3*i+j,doc->animation.v[i].node,paths[j]);
        index++;
    }
    for(i=0;i<doc->morph_animation.n;i++) {
        if(index) fputc(',',f);
        fprintf(f,"{\"sampler\":%zu,\"target\":{\"node\":%zu,\"path\":\"weights\"}}",3*doc->animation.n+i,doc->morph_animation.v[i].node); index++;
    }
    fprintf(f,"],\"extras\":{\"profile\":\"%s\",\"first_frame\":%.17g,\"last_frame\":%.17g,\"fps\":%.17g,\"samples\":%zu,\"morph_channels\":%zu,\"sampling\":\"one source-frame interval; LINEAR translation/scale, quaternion slerp and morph weights between samples; includes pivot offsets; skeletal TRS when a skin is present; material animation is not exported\"}}]",profile,doc->first_frame,doc->last_frame,doc->package->scene.fps,doc->sample_count,doc->morph_animation.n);
}
static void json_document(FILE *f,const GDocument *doc,const char *name,int scene,size_t asset) {
    const LWSource *source=scene?&doc->package->scene.source:&doc->package->objects.v[asset].source;
    size_t i,j; int comma=0,morph=doc->morph_views!=0; const char *profile;
    if(doc->autonomous) profile=morph?"autonomous-hpb-ik-morph-0.3":"autonomous-hpb-ik-0.2";
    else if(doc->rig) profile=morph?"rest-skeleton-morph-0.2":"rest-skeleton-0.1";
    else if(doc->animation.n||doc->morph_animation.n) profile=morph?"sampled-scene-and-morph-0.2":"sampled-scene-transforms-0.1";
    else profile=morph?"static-morph-geometry-0.2":"static-base-geometry-0.1";
    fprintf(f,"{\n\"asset\":{\"version\":\"2.0\",\"generator\":\"lwconvert %s\"},\n\"extras\":{\"profile\":\"%s\",\"source_sha256\":\"%s\",\"source_path\":",LWCONVERT_VERSION,profile,source->sha256); lw_json_string(f,source->path);
    if(doc->rig) {
        fprintf(f,",\"pose\":\"%s\",\"skin_status\":\"%s\",\"missing_map_procedural_fallbacks\":%zu,\"unweighted_points_on_object_anchor\":%zu,\"skin_issue\":",doc->autonomous?"sampled autonomous IK/FK; approximation":"native-rest; object-local",doc->rig->weighted?(doc->rig->procedural_bones?(doc->package->legacy_bone_maps?"lightwave6-procedural-weights-0.1; approximation":"lightwave96-procedural-weights-0.1; approximation"):"explicit-normalized-weight-maps"):"skeleton-only; native influences not evaluated",doc->package->legacy_bone_maps?doc->rig->missing_maps:0,doc->rig->unweighted_points);
        lw_json_string(f,doc->rig->issue);
        if(doc->rig->weighted&&doc->rig->procedural_bones) fprintf(f,",\"skin_approximation\":true,\"volume_corrections_omitted\":%zu,\"skin_limitations\":\"fixed weights derived from the native rest cage; joint compensation and muscle flexing omitted; morph targets are evaluated before skinning by glTF; IK profile described in animation metadata\"",doc->rig->volume_corrections);
    }
    if(!doc->rig) fprintf(f,",\"snapshot_frame\":%.17g",doc->options->frame);
    fputs(",\"normal_profile\":\"source-corner-normals-0.1\",\"normals\":\"unique NORM map per point block with VMAD precedence, otherwise unit polygon normals averaged by owner SMAN and SMGP among smoothing-enabled faces; angle cuts retained at source corners\"",f);
    if(scene&&!doc->rig&&doc->stats->animation_issue[0]) { fputs(",\"animation_issue\":",f); lw_json_string(f,doc->stats->animation_issue); }
    fprintf(f,",\"coordinates\":\"right-handed Y-up; source Z reflected\",\"uv_conversion\":\"u, 1-v\",\"animation\":\"%s\",\"morphing\":\"LWO2 MORF/SPOT and compatible external object targets; sampled LW_MorphMixer/MorphAmount weights\",\"subdivision\":\"control cage retained; never baked\",\"textures\":\"LWOB compatible planar/spherical image maps; repeat sampling; PNG derivatives; approximate scalar channels\"},\n\"scene\":0,\"scenes\":[{\"name\":",(doc->animation.n||doc->morph_animation.n)?"sampled local transforms and morph weights":doc->rig?"native rest pose":"static pose"); lw_json_string(f,name);
    for(i=0;i<doc->nodes.n;i++) if(doc->nodes.v[i].parent==SIZE_MAX) {
        if(!comma) fputs(",\"nodes\":[",f); else fputc(',',f);
        fprintf(f,"%zu",i); comma=1;
    }
    if(comma) fputc(']',f);
    fputs("}]",f);
    if(doc->nodes.n) {
        fputs(",\n\"nodes\":[",f);
        for(i=0;i<doc->nodes.n;i++) {
            const GNode *n=&doc->nodes.v[i]; int identity=1; if(i) fputc(',',f);
            fputs("{\"name\":",f);
            if(scene) { char *text=lw_text(doc->package->scene.nodes.v[n->source].name); lw_json_string(f,text&&*text?text:"LightWave node"); free(text); }
            else lw_json_string(f,name);
            if(n->mesh!=SIZE_MAX) fprintf(f,",\"mesh\":%zu",n->mesh);
            if(n->skinned) fputs(",\"skin\":0",f);
            if(n->weight_count) { fputs(",\"weights\":[",f); for(j=0;j<n->weight_count;j++) { if(j) fputc(',',f); fprintf(f,"%.9g",n->weights[j]); } fputc(']',f); }
            for(j=0;j<16;j++) if(n->matrix[j]!=(j%5==0?1:0)) identity=0;
            if(n->animated) {
                fprintf(f,",\"translation\":[%.17g,%.17g,%.17g],\"rotation\":[%.17g,%.17g,%.17g,%.17g],\"scale\":[%.17g,%.17g,%.17g]",n->trs[0],n->trs[1],n->trs[2],n->trs[3],n->trs[4],n->trs[5],n->trs[6],n->trs[7],n->trs[8],n->trs[9]);
            } else if(!identity) { fputs(",\"matrix\":[",f); for(j=0;j<16;j++) { if(j) fputc(',',f); fprintf(f,"%.17g",n->matrix[j]); } fputc(']',f); }
            comma=0;
            for(j=0;j<doc->nodes.n;j++) if(doc->nodes.v[j].parent==i) { if(!comma) fputs(",\"children\":[",f); else fputc(',',f); fprintf(f,"%zu",j); comma=1; }
            if(comma) fputc(']',f);
            fprintf(f,",\"extras\":{\"source_node_index\":%zu",n->source);
            if(scene) fprintf(f,",\"source_node_id\":%u",doc->package->scene.nodes.v[n->source].id);
            if(doc->rig&&i>0&&i<=doc->rig->joints.n) {
                const LWBone *bone=&doc->package->scene.nodes.v[n->source].bone;
                fprintf(f,",\"native_bone\":true,\"active\":%s,\"rest_length\":%.17g,\"weight_map\":",bone->active?"true":"false",bone->rest_length);
                lw_json_name(f,bone->weight_map); fputs(",\"weight_map_status\":",f); lw_json_string(f,bone->weight_map_status);
                fprintf(f,",\"weight_map_only\":%s",bone->weight_map_only?"true":"false");
            }
            if(doc->rig) {
                const LWNode *native=&doc->package->scene.nodes.v[n->source];
                fputs(",\"native_rig_parameters\":[",f);
                for(j=0;j<native->rig_parameters.n;j++) {
                    const LWTextureField *field=&native->rig_parameters.v[j]; if(j) fputc(',',f);
                    fputs("{\"name\":",f); lw_json_name(f,field->name); fputs(",\"value\":",f); lw_json_name(f,field->value);
                    fprintf(f,",\"source_offset\":%zu}",field->offset);
                }
                fputc(']',f);
            }
            fputs("}}",f);
        }
        fputc(']',f);
    }
    if(doc->materials.n) {
        fputs(",\n\"materials\":[",f);
        for(i=0;i<doc->materials.n;i++) { if(i) fputc(',',f); json_material(f,doc,&doc->materials.v[i]); }
        fputc(']',f);
    }
    if(doc->textures.n) {
        fputs(",\n\"samplers\":[{\"magFilter\":9729,\"minFilter\":9987,\"wrapS\":10497,\"wrapT\":10497}],\"images\":[",f);
        for(i=0;i<doc->textures.n;i++) { if(i) fputc(',',f); fputs("{\"uri\":",f); texture_uri(f,doc->textures.v[i]); fprintf(f,",\"extras\":{\"sha256\":\"%s\"}}",lw_texture_digest(doc->package,doc->textures.v[i])); }
        fputs("],\"textures\":[",f);
        for(i=0;i<doc->textures.n;i++) { if(i) fputc(',',f); fprintf(f,"{\"source\":%zu,\"sampler\":0}",i); }
        fputc(']',f);
        for(i=0;i<doc->materials.n;i++) if(doc->materials.v[i].specular!=SIZE_MAX) { fputs(",\"extensionsUsed\":[\"KHR_materials_specular\"]",f); break; }
    }
    if(doc->meshes.n) {
        fputs(",\n\"meshes\":[",f);
        for(i=0;i<doc->meshes.n;i++) {
            const GMesh *m=&doc->meshes.v[i]; if(i) fputc(',',f);
            fputs("{\"name\":",f); lw_json_string(f,doc->package->names.v[m->asset]);
            fprintf(f,",\"extras\":{\"source_asset_index\":%zu,\"source_sha256\":\"%s\",\"source_layer_request\":",m->asset,doc->package->objects.v[m->asset].source.sha256);
            if(m->layer==LW_NONE) fputs("null",f); else fprintf(f,"%u",m->layer);
            fputs(",\"targetNames\":[",f); for(j=0;j<m->targets.n;j++) { if(j) fputc(',',f); lw_json_string(f,m->targets.v[j].name); } fputs("]}",f);
            fputs(",\"primitives\":[",f);
            for(j=0;j<m->count;j++) {
                const GPrimitive *p=&doc->primitives.v[m->first+j]; if(j) fputc(',',f);
                fprintf(f,"{\"attributes\":{\"POSITION\":%zu",p->accessor);
                if(p->mode==4) fprintf(f,",\"NORMAL\":%zu",p->accessor+1);
                if(p->uv) fprintf(f,",\"TEXCOORD_0\":%zu",p->accessor+2);
                if(p->tangent) fprintf(f,",\"TANGENT\":%zu",p->accessor+3);
                { size_t set; for(set=0;set<p->skin_sets;set++) fprintf(f,",\"JOINTS_%zu\":%zu,\"WEIGHTS_%zu\":%zu",set,p->skin_accessor+2*set,set,p->skin_accessor+2*set+1); }
                fputc('}',f);
                if(p->morph_targets) {
                    size_t target,access=p->morph_accessor; fputs(",\"targets\":[",f);
                    for(target=0;target<p->morph_targets;target++) {
                        if(target) fputc(',',f); fprintf(f,"{\"POSITION\":%zu",access++);
                        if(p->mode==4) fprintf(f,",\"NORMAL\":%zu",access++);
                        fputc('}',f);
                    }
                    fputc(']',f);
                }
                fprintf(f,",\"mode\":%u,\"material\":%zu,\"extras\":{\"source_map\":{\"buffer\":0,\"byteOffset\":%zu,\"byteLength\":%zu,\"count\":%zu,\"stride\":12,\"component_type\":\"uint32\",\"byte_order\":\"little\",\"fields\":[\"polygon\",\"corner\",\"point\"],\"null_index\":4294967295}}}",p->mode,p->material,p->map_offset,p->count*12,p->count);
            }
            fputs("]}",f);
        }
        fputc(']',f);
    }
    if(doc->rig&&doc->rig->weighted) {
        fprintf(f,",\n\"skins\":[{\"inverseBindMatrices\":%zu,\"skeleton\":0,\"joints\":[0",doc->bind_accessor);
        for(i=0;i<doc->rig->joints.n;i++) fprintf(f,",%zu",i+1);
        fputs("],\"extras\":{\"joint_zero\":\"identity object anchor for otherwise unweighted vertices\",\"weights\":\"normalized explicit or procedural weights; see skin profile\",\"bind_pose\":\"native rest\"}}]",f);
    }
    json_animation(f,doc,name);
    if(doc->bytes) {
        fprintf(f,",\n\"buffers\":[{\"byteLength\":%zu,\"uri\":",doc->bytes); buffer_uri(f,name); fputs("}],\n\"bufferViews\":[",f);
        for(i=0;i<doc->primitives.n;i++) {
            const GPrimitive *p=&doc->primitives.v[i]; if(i) fputc(',',f);
            fprintf(f,"{\"buffer\":0,\"byteOffset\":%zu,\"byteLength\":%zu,\"byteStride\":%u,\"target\":34962}",p->offset,p->count*p->stride,p->stride);
        }
        for(i=0;i<doc->primitives.n;i++) {
            const GPrimitive *p=&doc->primitives.v[i]; size_t set;
            for(set=0;set<p->skin_sets;set++) fprintf(f,",{\"buffer\":0,\"byteOffset\":%zu,\"byteLength\":%zu,\"byteStride\":24,\"target\":34962}",p->skin_offset+set*p->count*24,p->count*24);
        }
        for(i=0;i<doc->primitives.n;i++) {
            const GPrimitive *p=&doc->primitives.v[i]; size_t target;
            for(target=0;target<p->morph_targets;target++) fprintf(f,",{\"buffer\":0,\"byteOffset\":%zu,\"byteLength\":%zu,\"byteStride\":%zu,\"target\":34962}",p->morph_offset+target*p->count*p->morph_stride,p->count*p->morph_stride,p->morph_stride);
        }
        if(doc->rig&&doc->rig->weighted) fprintf(f,",{\"buffer\":0,\"byteOffset\":%zu,\"byteLength\":%zu}",doc->bind_offset,(doc->rig->joints.n+1)*64);
        if(doc->animation.n||doc->morph_animation.n) {
            if(doc->primitives.n||doc->skin_views||doc->morph_views||(doc->rig&&doc->rig->weighted)) fputc(',',f);
            fprintf(f,"{\"buffer\":0,\"byteOffset\":%zu,\"byteLength\":%zu}",doc->time_offset,doc->sample_count*4);
            for(i=0;i<doc->animation.n;i++) for(j=0;j<3;j++) fprintf(f,",{\"buffer\":0,\"byteOffset\":%zu,\"byteLength\":%zu}",doc->animation.v[i].offset[j],doc->sample_count*(j==1?4:3)*4);
            for(i=0;i<doc->morph_animation.n;i++) fprintf(f,",{\"buffer\":0,\"byteOffset\":%zu,\"byteLength\":%zu}",doc->morph_animation.v[i].offset,doc->sample_count*doc->morph_animation.v[i].count*4);
        }
        fputs("],\n\"accessors\":[",f);
        for(i=0;i<doc->primitives.n;i++) {
            const GPrimitive *p=&doc->primitives.v[i]; if(i) fputc(',',f);
            accessor(f,i,0,p->count,3,p->low,p->high);
            if(p->mode==4) { fputc(',',f); accessor(f,i,12,p->count,3,NULL,NULL); }
            if(p->uv) { fputc(',',f); accessor(f,i,24,p->count,2,NULL,NULL); }
            if(p->tangent) { fputc(',',f); accessor(f,i,32,p->count,4,NULL,NULL); }
            { size_t set; for(set=0;set<p->skin_sets;set++) {
                size_t view=doc->primitives.n+p->skin_view+set;
                fprintf(f,",{\"bufferView\":%zu,\"byteOffset\":0,\"componentType\":5123,\"count\":%zu,\"type\":\"VEC4\"},",view,p->count);
                accessor(f,view,8,p->count,4,NULL,NULL);
            } }
            { size_t target; for(target=0;target<p->morph_targets;target++) {
                size_t view=doc->primitives.n+doc->skin_views+p->morph_view+target;
                fputc(',',f); accessor(f,view,0,p->count,3,p->morph_low+3*target,p->morph_high+3*target);
                if(p->mode==4) { fputc(',',f); accessor(f,view,12,p->count,3,NULL,NULL); }
            } }
        }
        if(doc->rig&&doc->rig->weighted) fprintf(f,",{\"bufferView\":%zu,\"componentType\":5126,\"count\":%zu,\"type\":\"MAT4\"}",doc->primitives.n+doc->skin_views+doc->morph_views,doc->rig->joints.n+1);
        if(doc->animation.n||doc->morph_animation.n) {
            size_t view=doc->primitives.n+doc->skin_views+doc->morph_views+(doc->rig&&doc->rig->weighted?1:0);
            if(doc->primitives.n||(doc->rig&&doc->rig->weighted)) fputc(',',f);
            fprintf(f,"{\"bufferView\":%zu,\"componentType\":5126,\"count\":%zu,\"type\":\"SCALAR\",\"min\":[0],\"max\":[%.9g]}",view,doc->sample_count,doc->duration);
            for(i=0;i<doc->animation.n;i++) for(j=0;j<3;j++) { fputc(',',f); accessor(f,++view,0,doc->sample_count,j==1?4:3,NULL,NULL); }
            for(i=0;i<doc->morph_animation.n;i++) { fputc(',',f); fprintf(f,"{\"bufferView\":%zu,\"componentType\":5126,\"count\":%zu,\"type\":\"SCALAR\"}",++view,doc->sample_count*doc->morph_animation.v[i].count); }
        }
        fputc(']',f);
    }
    fputs("\n}\n",f);
}
static int write_document(const char *dir,const char *name,const LWPackage *package,const LWOptions *opts,int scene,size_t asset,const LWRig *rig,LWGltfStats *stats,LWError *e) {
    GDocument doc={0}; char *bin=NULL,*json=NULL; FILE *f=NULL; size_t i; int ok=0;
    doc.package=package; doc.options=opts; doc.stats=stats; doc.rig=rig;
    doc.bind_accessor=doc.time_accessor=SIZE_MAX;
    doc.autonomous=scene&&package->bake.poses&&(!rig||scene==2);
    bin=lw_named_path(dir,name,".bin"); json=lw_named_path(dir,name,".gltf");
    if(!bin||!json) { lw_error(e,0,"allocation","out of memory"); goto done; }
    doc.bin=lw_fopen(bin,"wb"); if(!doc.bin) { lw_error(e,0,"glTF","cannot create %s",bin); goto done; }
    if(rig) { if(!rig_nodes(&doc,e)||(doc.autonomous&&!baked_animation(&doc,e))||!morph_animation(&doc,e)) goto done; }
    else if(scene) { if(!scene_nodes(&doc,e)||!(doc.autonomous?baked_animation(&doc,e):scene_animation(&doc,e))||!morph_animation(&doc,e)) goto done; }
    else {
        GNode node={0}; node.parent=SIZE_MAX; lw_identity(node.matrix);
        if(!mesh_index(&doc,asset,LW_NONE,LW_NONE,&node.mesh,e)||!LW_ADD(doc.nodes,node,e)) goto done;
        /* A geometry-free surface preset still exports its material library. */
        if(!doc.meshes.n) for(i=0;i<package->objects.v[asset].materials.n;i++) { size_t index; if(!material_index(&doc,asset,(uint32_t)i,0,LW_NONE,&index,e)) goto done; }
    }
    { int closed=lw_close(doc.bin,bin,e); doc.bin=NULL; if(!closed) goto done; }
    f=lw_fopen(json,"wb"); if(!f) { lw_error(e,0,"glTF","cannot create %s",json); goto done; }
    json_document(f,&doc,name,scene,asset);
    { int closed=lw_close(f,json,e); f=NULL; if(!closed) goto done; }
    stats->files++; stats->materials+=doc.materials.n; ok=1;
done:
    if(f) fclose(f);
    if(doc.bin) fclose(doc.bin);
    clear_animation(&doc);
    clear_morph_animation(&doc);
    for(i=0;i<doc.meshes.n;i++) free_morph_targets(&doc.meshes.v[i].targets);
    for(i=0;i<doc.nodes.n;i++) free(doc.nodes.v[i].weights);
    for(i=0;i<doc.primitives.n;i++) { free(doc.primitives.v[i].morph_low); free(doc.primitives.v[i].morph_high); }
    LW_FREE(doc.primitives); LW_FREE(doc.meshes); LW_FREE(doc.materials); LW_FREE(doc.nodes); LW_FREE(doc.textures);
    free(bin); free(json); return ok;
}
int lw_write_gltf(const char *dir,const LWPackage *p,const LWOptions *opts,LWGltfStats *stats,LWError *e) {
    char *gltf=lw_join(dir,"gltf"); double *matrices=NULL; size_t i,j,bad=SIZE_MAX,instances=0; LWError local={0}; int ok=0;
    if(!gltf) return lw_error(e,0,"allocation","out of memory");
    for(i=0;i<p->objects.n;i++) if(!write_document(gltf,p->names.v[i],p,opts,0,i,NULL,stats,e)) goto done;
    if(!p->is_scene) { ok=1; goto done; }
    for(i=0;i<p->scene.nodes.n;i++) if(p->scene.nodes.v[i].asset!=SIZE_MAX) instances++;
    if(p->bake.issue[0]) snprintf(stats->animation_issue,sizeof stats->animation_issue,"%s",p->bake.issue);
    stats->rigs=calloc(1,sizeof *stats->rigs);
    if(!stats->rigs) { lw_error(e,0,"allocation","out of memory"); goto done; }
    for(i=0;i<p->scene.nodes.n;i++) if(p->scene.nodes.v[i].asset!=SIZE_MAX) {
        LWRig rig={0}; LWRigExport result={0}; LWError rig_error={0}; int built;
        for(j=0;j<p->scene.nodes.n;j++) if(p->scene.nodes.v[j].bone.owner==p->scene.nodes.v[i].id) break;
        if(j==p->scene.nodes.n) continue;
        result.owner=i; built=lw_build_rig(p,i,&rig,&rig_error);
        for(j=0;j<p->scene.nodes.n;j++) if(p->scene.nodes.v[j].bone.owner==p->scene.nodes.v[i].id) result.bones++;
        result.sets=rig.influence_sets; result.weighted=rig.weighted;
        result.missing_maps=rig.missing_maps; result.procedural_bones=rig.procedural_bones; result.unweighted_points=rig.unweighted_points; result.volume_corrections=rig.volume_corrections;
        if(built&&rig.weighted&&rig.procedural_bones&&!lw_write_derived_skin(dir,p,&rig,e)) { lw_free_rig(&rig); goto done; }
        snprintf(result.issue,sizeof result.issue,"%s",built?rig.issue:rig_error.message);
        result.available=built;
        if(built&&(rig.weighted||opts->gltf_all_rigs)) {
            size_t length=strlen(p->scene_name)+48; result.name=malloc(length);
            if(!result.name) { lw_free_rig(&rig); lw_error(e,0,"allocation","out of memory"); goto done; }
            result.animated=p->bake.poses&&rig.weighted;
            if(result.animated&&instances==1&&!p->unresolved) snprintf(result.name,length,"%s",p->scene_name);
            else snprintf(result.name,length,"%s.rig-%08x",p->scene_name,p->scene.nodes.v[i].id);
            result.written=write_document(gltf,result.name,p,opts,result.animated?2:1,rig.asset,&rig,stats,e);
            if(!result.written) { free(result.name); lw_free_rig(&rig); goto done; }
            if(result.animated&&instances==1&&!p->unresolved) stats->geometry.scene_written=1;
        } else if(!strcmp(rig_error.context,"allocation")) { lw_free_rig(&rig); *e=rig_error; goto done; }
        lw_free_rig(&rig);
        if(!LW_ADD(*stats->rigs,result,e)) { free(result.name); goto done; }
    }
    if(stats->geometry.scene_written) { ok=1; goto done; }
    for(i=0;i<p->scene.nodes.n;i++) for(j=0;j<p->scene.nodes.v[i].channels.n;j++) if(p->scene.nodes.v[i].channels.v[j].keys.n>1) stats->animated_channels++;
    matrices=calloc(p->scene.nodes.n?p->scene.nodes.n:1,16*sizeof *matrices);
    if(!matrices) { lw_error(e,0,"allocation","out of memory"); goto done; }
    if(!p->bake.poses&&!lw_scene_matrices(&p->scene,opts->frame,matrices,&bad,&local)) {
        stats->omitted_nodes=p->scene.nodes.n;
        snprintf(stats->geometry.scene_issue,sizeof stats->geometry.scene_issue,"%.40s: %.210s",local.context,local.message); ok=1; goto done;
    }
    for(i=0;i<p->scene.nodes.n;i++) {
        const LWNode *n=&p->scene.nodes.v[i];
        if(n->asset==SIZE_MAX) continue;
        for(j=0;j<p->objects.v[n->asset].layers.n;j++) {
            const LWLayer *l=&p->objects.v[n->asset].layers.v[j];
            if(selected(&p->objects.v[n->asset],(uint32_t)j,n->layer)&&(l->pivot[0]||l->pivot[1]||l->pivot[2]||l->parent!=LW_NONE)) {
                stats->omitted_nodes=p->scene.nodes.n;
                snprintf(stats->geometry.scene_issue,sizeof stats->geometry.scene_issue,"layer pivot/parent semantics require qualification; instance %zu",i); ok=1; goto done;
            }
        }
    }
    if(!write_document(gltf,p->scene_name,p,opts,1,0,NULL,stats,e)) goto done;
    stats->geometry.scene_written=1; ok=1;
done:
    free(matrices); free(gltf); return ok;
}
