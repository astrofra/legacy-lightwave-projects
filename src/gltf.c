#include "internal.h"
#include <math.h>

/* Direct glTF 2.0 writer. Geometry is derived from native corners, never OBJ.
   The initial scene profile is a static pose with local transforms/instancing. */
typedef struct { float p[3],n[3],uv[2]; uint32_t polygon,corner,point; } GVertex;
typedef struct { uint32_t material,mode; int uv; LW_ARRAY(GVertex) vertices; } GGroup;
typedef LW_ARRAY(GGroup) GGroups;
typedef struct {
    size_t offset,map_offset,count,accessor,material; uint32_t mode,stride;
    size_t skin_offset,skin_view,skin_accessor,skin_sets;
    int uv; float low[3],high[3];
} GPrimitive;
typedef struct { size_t asset,first,count; uint32_t layer; } GMesh;
typedef struct { size_t asset; uint32_t index; int uv; size_t base,emissive,specular; } GMaterial;
typedef struct { size_t source,mesh,parent; int skinned; double matrix[16]; } GNode;
typedef struct {
    const LWPackage *package; const LWOptions *options; LWGltfStats *stats;
    FILE *bin; size_t bytes,accessors;
    const LWRig *rig;
    size_t skin_views,bind_offset,bind_accessor;
    LW_ARRAY(GPrimitive) primitives;
    LW_ARRAY(GMesh) meshes;
    LW_ARRAY(GMaterial) materials;
    LW_ARRAY(const char *) textures;
    LW_ARRAY(GNode) nodes;
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
static int triangle(GGroup *g,const LWObject *o,uint32_t polygon,const uint32_t corners[3],const LWUV *uv,LWError *e) {
    GVertex v[3]; double a[3],b[3],n[3],length; size_t i,j;
    for(i=0;i<3;i++) v[i]=vertex(o,polygon,corners[2-i],uv);
    for(i=0;i<3;i++) { a[i]=(double)v[1].p[i]-v[0].p[i]; b[i]=(double)v[2].p[i]-v[0].p[i]; }
    n[0]=a[1]*b[2]-a[2]*b[1]; n[1]=a[2]*b[0]-a[0]*b[2]; n[2]=a[0]*b[1]-a[1]*b[0];
    length=sqrt(n[0]*n[0]+n[1]*n[1]+n[2]*n[2]);
    if(!length||!isfinite(length)) return lw_error(e,0,"glTF","triangulator returned a degenerate triangle");
    for(i=0;i<3;i++) {
        for(j=0;j<3;j++) v[i].n[j]=(float)(n[j]/length);
        if(!LW_ADD(g->vertices,v[i],e)) return 0;
    }
    return 1;
}
static int derive(GDocument *doc,size_t asset,uint32_t request,GGroups *groups,LWError *e) {
    const LWObject *o=&doc->package->objects.v[asset]; LWExportStats *s=&doc->stats->geometry;
    LWUV *uv=NULL; unsigned char *used=calloc(o->positions.n/3+1,1); size_t i,j; int ok=0;
    if(!used) return lw_error(e,0,"allocation","out of memory");
    uv=doc->options->uv_map?lw_corner_uvs(o,doc->options->uv_map,e):lw_texture_uvs(o,e); if(!uv) goto done;
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
            for(j=0;j<t.corners.n;j+=3) if(!triangle(g,o,(uint32_t)i,t.corners.v+j,has_uv?uv:NULL,e)) { lw_free_triangulation(&t); goto done; }
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
    free(uv); free(used); return ok;
}
static int texture_index(GDocument *doc,const char *uri,size_t *index,LWError *e) {
    size_t i; *index=SIZE_MAX; if(!uri) return 1;
    for(i=0;i<doc->textures.n;i++) if(!strcmp(doc->textures.v[i],uri)) { *index=i; return 1; }
    *index=doc->textures.n; return LW_ADD(doc->textures,uri,e);
}
static int material_index(GDocument *doc,size_t asset,uint32_t index,int uv,size_t *result,LWError *e) {
    GMaterial m={0}; size_t i; m.asset=asset; m.index=index; m.uv=uv; m.base=m.emissive=m.specular=SIZE_MAX;
    for(i=0;i<doc->materials.n;i++) if(doc->materials.v[i].asset==asset&&doc->materials.v[i].index==index&&doc->materials.v[i].uv==uv) { *result=i; return 1; }
    *result=doc->materials.n;
    if(index<doc->package->objects.v[asset].materials.n) {
        uint32_t side=doc->package->objects.v[asset].materials.v[index].side;
        const LWMaterial *native=&doc->package->objects.v[asset].materials.v[index];
        if(side!=1&&side!=3) doc->stats->unsupported_sidedness++;
        if(uv&&(!texture_index(doc,native->base_texture,&m.base,e)||!texture_index(doc,native->emissive_texture,&m.emissive,e)||!texture_index(doc,native->specular_texture,&m.specular,e))) return 0;
    }
    return LW_ADD(doc->materials,m,e);
}
static int write_group(GDocument *doc,const GGroup *g,size_t asset,LWError *e) {
    GPrimitive p={0}; size_t i,j,bytes; uint32_t stride=12+(g->mode==4?12:0)+(g->uv?8:0);
    if(!g->vertices.n) return 1;
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
    if(doc->bytes>UINT32_MAX-bytes) return lw_error(e,0,"glTF","document buffer exceeds 4 GiB");
    p.offset=doc->bytes; p.map_offset=p.offset+g->vertices.n*stride; p.count=g->vertices.n;
    p.mode=g->mode; p.uv=g->uv; p.stride=stride; p.accessor=doc->accessors;
    if(!material_index(doc,asset,g->material,g->uv,&p.material,e)) return 0;
    memcpy(p.low,g->vertices.v[0].p,sizeof p.low); memcpy(p.high,p.low,sizeof p.high);
    for(i=0;i<g->vertices.n;i++) {
        const GVertex *v=&g->vertices.v[i];
        for(j=0;j<3;j++) { f32(doc->bin,v->p[j]); p.low[j]=fminf(p.low[j],v->p[j]); p.high[j]=fmaxf(p.high[j],v->p[j]); }
        if(g->mode==4) for(j=0;j<3;j++) f32(doc->bin,v->n[j]);
        if(g->uv) for(j=0;j<2;j++) f32(doc->bin,v->uv[j]);
    }
    /* Source mapping is application data in the same binary buffer. It is not
       a glTF vertex attribute (uint32 is not a portable attribute encoding). */
    for(i=0;i<g->vertices.n;i++) {
        u32(doc->bin,g->vertices.v[i].polygon); u32(doc->bin,g->vertices.v[i].corner); u32(doc->bin,g->vertices.v[i].point);
    }
    if(p.skin_sets) {
        size_t set,k; p.skin_offset=p.map_offset+12*p.count; p.skin_view=doc->skin_views;
        p.skin_accessor=doc->accessors+1+(g->mode==4?1:0)+(g->uv?1:0);
        for(set=0;set<p.skin_sets;set++) for(i=0;i<g->vertices.n;i++) {
            const LWRigPoint *point=&doc->rig->points[g->vertices.v[i].point];
            for(k=0;k<4;k++) { size_t index=4*set+k; u16(doc->bin,index<point->count?doc->rig->influences.v[point->first+index].joint:0); }
            for(k=0;k<4;k++) { size_t index=4*set+k; f32(doc->bin,index<point->count?doc->rig->influences.v[point->first+index].weight:0); }
        }
        doc->skin_views+=p.skin_sets;
    }
    if(ferror(doc->bin)) return lw_error(e,0,"glTF","cannot write geometry buffer");
    doc->bytes+=bytes; doc->accessors+=1+(g->mode==4?1:0)+(g->uv?1:0)+2*p.skin_sets;
    return LW_ADD(doc->primitives,p,e);
}
static int mesh_index(GDocument *doc,size_t asset,uint32_t layer,size_t *result,LWError *e) {
    size_t i; GGroups groups={0}; GMesh mesh={asset,doc->primitives.n,0,layer}; int ok=0;
    for(i=0;i<doc->meshes.n;i++) if(doc->meshes.v[i].asset==asset&&doc->meshes.v[i].layer==layer) { *result=i; return 1; }
    if(!derive(doc,asset,layer,&groups,e)) goto done;
    for(i=0;i<groups.n;i++) if(!write_group(doc,&groups.v[i],asset,e)) goto done;
    mesh.count=doc->primitives.n-mesh.first;
    *result=SIZE_MAX;
    if(mesh.count) { *result=doc->meshes.n; if(!LW_ADD(doc->meshes,mesh,e)) goto done; }
    ok=1;
done:
    for(i=0;i<groups.n;i++) LW_FREE(groups.v[i].vertices);
    LW_FREE(groups); return ok;
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
        if(!lw_scene_node_matrix(s,i,doc->options->frame,m,e)) goto done;
        /* C M C with C=diag(1,1,-1,1), while geometry already uses C p. */
        for(j=0;j<16;j++) n.matrix[j]=m[j]*((j%4==2)^(j/4==2)?-1:1);
        if(source->asset!=SIZE_MAX&&!mesh_index(doc,source->asset,source->layer,&n.mesh,e)) goto done;
        map[i]=doc->nodes.n; if(!LW_ADD(doc->nodes,n,e)) goto done;
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
    LW_TRY(mesh_index(doc,rig->asset,owner->layer,&mesh.mesh,e));
    LW_TRY(LW_ADD(doc->nodes,mesh,e));
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
    fprintf(f,"},\"emissiveFactor\":[%.9g,%.9g,%.9g],\"alphaMode\":\"%s\",\"doubleSided\":%s",emissive[0],emissive[1],emissive[2],alpha<1||(entry->base!=SIZE_MAX&&m->texture_alpha)?"BLEND":"OPAQUE",two_sided?"true":"false");
    if(entry->emissive!=SIZE_MAX) fprintf(f,",\"emissiveTexture\":{\"index\":%zu}",entry->emissive);
    if(entry->specular!=SIZE_MAX) fprintf(f,",\"extensions\":{\"KHR_materials_specular\":{\"specularFactor\":1,\"specularTexture\":{\"index\":%zu}}}",entry->specular);
    fprintf(f,",\"extras\":{\"source_asset_index\":%zu,\"source_surface_index\":",entry->asset);
    if(m) fprintf(f,"%u",entry->index); else fputs("null",f);
    fprintf(f,",\"source_sha256\":\"%s\",\"source_sidedness\":%u",o->source.sha256,m?m->side:1);
    fputs(",\"native_texture_bindings\":",f); lw_json_textures(f,o,entry->index);
    fputs(",\"interpretation\":\"color/diffuse/emission/opacity approximation; neutral rough dielectric; compatible LWOB image projections; height bump and environment reflection preserved in IR; no native smoothing\"}}",f);
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
static void json_document(FILE *f,const GDocument *doc,const char *name,int scene,size_t asset) {
    const LWSource *source=scene?&doc->package->scene.source:&doc->package->objects.v[asset].source;
    size_t i,j; int comma=0;
    fprintf(f,"{\n\"asset\":{\"version\":\"2.0\",\"generator\":\"lwconvert %s\"},\n\"extras\":{\"profile\":\"%s\",\"source_sha256\":\"%s\",\"source_path\":",LWCONVERT_VERSION,doc->rig?"rest-skeleton-0.1":"static-base-geometry-0.1",source->sha256); lw_json_string(f,source->path);
    if(doc->rig) {
        fprintf(f,",\"pose\":\"native-rest; object-local; no scene animation or IK evaluation\",\"skin_status\":\"%s\",\"unweighted_points_on_object_anchor\":%zu,\"skin_issue\":",doc->rig->weighted?"explicit-normalized-weight-maps":"skeleton-only; native influences not evaluated",doc->rig->unweighted_points);
        lw_json_string(f,doc->rig->issue);
    }
    if(!doc->rig) fprintf(f,",\"snapshot_frame\":%.17g",doc->options->frame);
    fprintf(f,",\"coordinates\":\"right-handed Y-up; source Z reflected\",\"uv_conversion\":\"u, 1-v\",\"animation\":\"not-exported; %s\",\"subdivision\":\"control cage retained; never baked\",\"textures\":\"LWOB compatible planar/spherical image maps; repeat sampling; PNG derivatives; approximate scalar channels\"},\n\"scene\":0,\"scenes\":[{\"name\":",doc->rig?"native rest pose":"snapshot only"); lw_json_string(f,name);
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
            for(j=0;j<16;j++) if(n->matrix[j]!=(j%5==0?1:0)) identity=0;
            if(!identity) { fputs(",\"matrix\":[",f); for(j=0;j<16;j++) { if(j) fputc(',',f); fprintf(f,"%.17g",n->matrix[j]); } fputc(']',f); }
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
        for(i=0;i<doc->textures.n;i++) { if(i) fputc(',',f); fputs("{\"uri\":",f); lw_json_string(f,doc->textures.v[i]); fputc('}',f); }
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
            fputs("},\"primitives\":[",f);
            for(j=0;j<m->count;j++) {
                const GPrimitive *p=&doc->primitives.v[m->first+j]; if(j) fputc(',',f);
                fprintf(f,"{\"attributes\":{\"POSITION\":%zu",p->accessor);
                if(p->mode==4) fprintf(f,",\"NORMAL\":%zu",p->accessor+1);
                if(p->uv) fprintf(f,",\"TEXCOORD_0\":%zu",p->accessor+2);
                { size_t set; for(set=0;set<p->skin_sets;set++) fprintf(f,",\"JOINTS_%zu\":%zu,\"WEIGHTS_%zu\":%zu",set,p->skin_accessor+2*set,set,p->skin_accessor+2*set+1); }
                fprintf(f,"},\"mode\":%u,\"material\":%zu,\"extras\":{\"source_map\":{\"buffer\":0,\"byteOffset\":%zu,\"byteLength\":%zu,\"count\":%zu,\"stride\":12,\"component_type\":\"uint32\",\"byte_order\":\"little\",\"fields\":[\"polygon\",\"corner\",\"point\"],\"null_index\":4294967295}}}",p->mode,p->material,p->map_offset,p->count*12,p->count);
            }
            fputs("]}",f);
        }
        fputc(']',f);
    }
    if(doc->rig&&doc->rig->weighted) {
        fprintf(f,",\n\"skins\":[{\"inverseBindMatrices\":%zu,\"skeleton\":0,\"joints\":[0",doc->bind_accessor);
        for(i=0;i<doc->rig->joints.n;i++) fprintf(f,",%zu",i+1);
        fputs("],\"extras\":{\"joint_zero\":\"identity object anchor for otherwise unweighted vertices\",\"weights\":\"normalized explicit WGHT maps; all positive influences retained\",\"pose\":\"native rest\"}}]",f);
    }
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
        if(doc->rig&&doc->rig->weighted) fprintf(f,",{\"buffer\":0,\"byteOffset\":%zu,\"byteLength\":%zu}",doc->bind_offset,(doc->rig->joints.n+1)*64);
        fputs("],\n\"accessors\":[",f);
        for(i=0;i<doc->primitives.n;i++) {
            const GPrimitive *p=&doc->primitives.v[i]; if(i) fputc(',',f);
            accessor(f,i,0,p->count,3,p->low,p->high);
            if(p->mode==4) { fputc(',',f); accessor(f,i,12,p->count,3,NULL,NULL); }
            if(p->uv) { fputc(',',f); accessor(f,i,24,p->count,2,NULL,NULL); }
            { size_t set; for(set=0;set<p->skin_sets;set++) {
                size_t view=doc->primitives.n+p->skin_view+set;
                fprintf(f,",{\"bufferView\":%zu,\"byteOffset\":0,\"componentType\":5123,\"count\":%zu,\"type\":\"VEC4\"},",view,p->count);
                accessor(f,view,8,p->count,4,NULL,NULL);
            } }
        }
        if(doc->rig&&doc->rig->weighted) fprintf(f,",{\"bufferView\":%zu,\"componentType\":5126,\"count\":%zu,\"type\":\"MAT4\"}",doc->primitives.n+doc->skin_views,doc->rig->joints.n+1);
        fputc(']',f);
    }
    fputs("\n}\n",f);
}
static int write_document(const char *dir,const char *name,const LWPackage *package,const LWOptions *opts,int scene,size_t asset,const LWRig *rig,LWGltfStats *stats,LWError *e) {
    GDocument doc={0}; char *bin=NULL,*json=NULL; FILE *f=NULL; size_t i; int ok=0;
    doc.package=package; doc.options=opts; doc.stats=stats; doc.rig=rig;
    bin=lw_named_path(dir,name,".bin"); json=lw_named_path(dir,name,".gltf");
    if(!bin||!json) { lw_error(e,0,"allocation","out of memory"); goto done; }
    doc.bin=lw_fopen(bin,"wb"); if(!doc.bin) { lw_error(e,0,"glTF","cannot create %s",bin); goto done; }
    if(rig) { if(!rig_nodes(&doc,e)) goto done; }
    else if(scene) { if(!scene_nodes(&doc,e)) goto done; }
    else {
        GNode node={0}; node.parent=SIZE_MAX; lw_identity(node.matrix);
        if(!mesh_index(&doc,asset,LW_NONE,&node.mesh,e)||!LW_ADD(doc.nodes,node,e)) goto done;
        /* A geometry-free surface preset still exports its material library. */
        if(!doc.meshes.n) for(i=0;i<package->objects.v[asset].materials.n;i++) { size_t index; if(!material_index(&doc,asset,(uint32_t)i,0,&index,e)) goto done; }
    }
    { int closed=lw_close(doc.bin,bin,e); doc.bin=NULL; if(!closed) goto done; }
    f=lw_fopen(json,"wb"); if(!f) { lw_error(e,0,"glTF","cannot create %s",json); goto done; }
    json_document(f,&doc,name,scene,asset);
    { int closed=lw_close(f,json,e); f=NULL; if(!closed) goto done; }
    stats->files++; stats->materials+=doc.materials.n; ok=1;
done:
    if(f) fclose(f);
    if(doc.bin) fclose(doc.bin);
    LW_FREE(doc.primitives); LW_FREE(doc.meshes); LW_FREE(doc.materials); LW_FREE(doc.nodes); LW_FREE(doc.textures);
    free(bin); free(json); return ok;
}
int lw_write_gltf(const char *dir,const LWPackage *p,const LWOptions *opts,LWGltfStats *stats,LWError *e) {
    char *gltf=lw_join(dir,"gltf"); double *matrices=NULL; size_t i,j,bad=SIZE_MAX; LWError local={0}; int ok=0;
    if(!gltf) return lw_error(e,0,"allocation","out of memory");
    for(i=0;i<p->objects.n;i++) if(!write_document(gltf,p->names.v[i],p,opts,0,i,NULL,stats,e)) goto done;
    if(!p->is_scene) { ok=1; goto done; }
    stats->rigs=calloc(1,sizeof *stats->rigs);
    if(!stats->rigs) { lw_error(e,0,"allocation","out of memory"); goto done; }
    for(i=0;i<p->scene.nodes.n;i++) if(p->scene.nodes.v[i].asset!=SIZE_MAX) {
        LWRig rig={0}; LWRigExport result={0}; LWError rig_error={0}; int built;
        for(j=0;j<p->scene.nodes.n;j++) if(p->scene.nodes.v[j].bone.owner==p->scene.nodes.v[i].id) break;
        if(j==p->scene.nodes.n) continue;
        result.owner=i; built=lw_build_rig(p,i,&rig,&rig_error);
        for(j=0;j<p->scene.nodes.n;j++) if(p->scene.nodes.v[j].bone.owner==p->scene.nodes.v[i].id) result.bones++;
        result.sets=rig.influence_sets; result.weighted=rig.weighted;
        result.missing_maps=rig.missing_maps; result.procedural_bones=rig.procedural_bones; result.unweighted_points=rig.unweighted_points;
        snprintf(result.issue,sizeof result.issue,"%s",built?rig.issue:rig_error.message);
        if(built) {
            size_t length=strlen(p->scene_name)+48; result.name=malloc(length);
            if(!result.name) { lw_free_rig(&rig); lw_error(e,0,"allocation","out of memory"); goto done; }
            snprintf(result.name,length,"%s.rig-%08x",p->scene_name,p->scene.nodes.v[i].id);
            result.written=write_document(gltf,result.name,p,opts,1,rig.asset,&rig,stats,e);
            if(!result.written) { free(result.name); lw_free_rig(&rig); goto done; }
        } else if(!strcmp(rig_error.context,"allocation")) { lw_free_rig(&rig); *e=rig_error; goto done; }
        lw_free_rig(&rig);
        if(!LW_ADD(*stats->rigs,result,e)) { free(result.name); goto done; }
    }
    for(i=0;i<p->scene.nodes.n;i++) for(j=0;j<p->scene.nodes.v[i].channels.n;j++) if(p->scene.nodes.v[i].channels.v[j].keys.n>1) stats->animated_channels++;
    matrices=calloc(p->scene.nodes.n?p->scene.nodes.n:1,16*sizeof *matrices);
    if(!matrices) { lw_error(e,0,"allocation","out of memory"); goto done; }
    if(!lw_scene_matrices(&p->scene,opts->frame,matrices,&bad,&local)) {
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
