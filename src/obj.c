#include "internal.h"
#include <math.h>

static int selected(const LWObject *o,uint32_t layer,uint32_t request) {
    return request==LW_NONE||o->layers.v[layer].id==request-1;
}
static FILE *create_file(const char *dir,const char *name,const char *suffix,LWError *e) {
    char *path=lw_named_path(dir,name,suffix); FILE *f;
    if(!path) { lw_error(e,0,"allocation","out of memory"); return NULL; }
    f=lw_fopen(path,"wb");
    if(!f) lw_error(e,0,"output","cannot create %s",path);
    free(path); return f;
}
static double unit(double x) { return x<0?0:x>1?1:x; }
static void materials(FILE *f,const LWObject *o,size_t asset) {
    size_t i,j;
    fprintf(f,"newmtl a%zu_default\nKd 0.8 0.8 0.8\nillum 1\n\n",asset);
    for(i=0;i<o->materials.n;i++) {
        const LWMaterial *m=&o->materials.v[i];
        fprintf(f,"newmtl a%zu_m%zu\nKd %.9g %.9g %.9g\nKs %.9g %.9g %.9g\nKe %.9g %.9g %.9g\nd %.9g\nillum 2\n",asset,i,
            m->base_texture?1:unit(m->color[0]*m->diffuse),m->base_texture?1:unit(m->color[1]*m->diffuse),m->base_texture?1:unit(m->color[2]*m->diffuse),
            m->specular_texture?1:unit(m->specular),m->specular_texture?1:unit(m->specular),m->specular_texture?1:unit(m->specular),
            m->emissive_texture?1:(double)m->color[0]*m->luminosity,m->emissive_texture?1:(double)m->color[1]*m->luminosity,m->emissive_texture?1:(double)m->color[2]*m->luminosity,m->opacity_texture?1:unit(1-m->transparency));
        if(m->base_texture) fprintf(f,"map_Kd %s\n",m->base_texture);
        if(m->opacity_texture) fprintf(f,"map_d %s\n",m->opacity_texture);
        if(m->emissive_texture) fprintf(f,"map_Ke %s\n",m->emissive_texture);
        if(m->specular_texture) fprintf(f,"map_Ks %s\n",m->specular_texture);
        if(m->bump_texture) for(j=0;j<o->textures.n;j++) if(o->textures.v[j].material==i&&o->textures.v[j].supported&&o->textures.v[j].channel==LW_TAG('B','U','M','P')) {
            fprintf(f,"bump -bm %.9g %s\n",o->textures.v[j].amplitude,m->bump_texture); break;
        }
        fputc('\n',f);
    }
}
static int mesh(FILE *f,const LWObject *o,size_t asset,size_t instance,uint32_t request,const double m[16],const char *uv_name,size_t *vertex_base,size_t *uv_base,size_t *normal_base,LWExportStats *stats,LWError *e) {
    size_t i,j,k,count=o->positions.n/3; size_t *vertices=calloc(count+1,sizeof *vertices);
    unsigned char *used=calloc(count+1,1); LWUV *uv=NULL; LWNormals normals={0}; int ok=0;
    double determinant=m[0]*(m[5]*m[10]-m[9]*m[6])-m[4]*(m[1]*m[10]-m[9]*m[2])+m[8]*(m[1]*m[6]-m[5]*m[2]);
    if(!vertices||!used) { lw_error(e,0,"allocation","out of memory"); goto done; }
    uv=uv_name?lw_corner_uvs(o,uv_name,e):lw_texture_uvs(o,e); if(!uv) goto done;
    if(!lw_corner_normals(o,&normals,e)) goto done;
    stats->normal_issues+=normals.issues;
    fprintf(f,"o instance_%zu_asset_%zu\n",instance,asset);
    for(i=0;i<o->point_blocks.n;i++) {
        const LWPointBlock *pb=&o->point_blocks.v[i];
        if(!selected(o,pb->layer,request)) continue;
        for(j=0;j<pb->count;j++) {
            size_t index=pb->first+j; const float *p=o->positions.v+3*index;
            double x=m[0]*p[0]+m[4]*p[1]+m[8]*p[2]+m[12],y=m[1]*p[0]+m[5]*p[1]+m[9]*p[2]+m[13],z=m[2]*p[0]+m[6]*p[1]+m[10]*p[2]+m[14];
            if(!isfinite(x)||!isfinite(y)||!isfinite(z)) { lw_error(e,0,"OBJ","non-finite transformed point"); goto done; }
            vertices[index]=++*vertex_base; fprintf(f,"v %.17g %.17g %.17g\n",x,y,-z);
        }
    }
    for(i=0;i<o->primitives.n;i++) {
        const LWPrimitive *p=&o->primitives.v[i]; const LWPolygonBlock *block=&o->polygon_blocks.v[p->block];
        int repeated=0,has_uv=uv_name||(p->material<o->materials.n&&o->materials.v[p->material].textured),is_curve=p->type==LW_TAG('C','U','R','V'); size_t first_uv=*uv_base+1;
        int triangulated=p->type==LW_TAG('F','A','C','E')&&p->count>=3;
        int has_normals=p->count>=3&&!is_curve; size_t first_normal=*normal_base+1;
        LWTriangulation triangles={0};
        if(!selected(o,block->layer,request)) continue;
        for(j=0;j<p->count;j++) for(k=0;k<j;k++) if(o->indices.v[p->first+j]==o->indices.v[p->first+k]) repeated=1;
        if(!p->count||p->detail_parent!=LW_NONE||p->legacy_surface<0||(repeated&&!triangulated)||
           (p->type!=LW_TAG('F','A','C','E')&&p->type!=LW_TAG('P','C','H','S')&&p->type!=LW_TAG('P','T','C','H')&&!is_curve)) {
            fprintf(f,"# omitted primitive %zu; preserved in geometry.bin\n",i); stats->skipped++; continue;
        }
        if(triangulated) {
            int status=lw_triangulate(o,p,&triangles,e);
            if(status<0) { lw_free_triangulation(&triangles); goto done; }
            if(!status) {
                fprintf(f,"# omitted primitive %zu: %s; preserved in geometry.bin\n",i,triangles.issue);
                stats->skipped++; stats->triangulation_failures++; lw_free_triangulation(&triangles); continue;
            }
            stats->triangulated_faces+=p->count>3; stats->triangles+=triangles.corners.n/3;
            stats->bridged_faces+=triangles.bridges!=0; stats->nonplanar_faces+=triangles.nonplanar!=0;
            stats->removed_corners+=triangles.removed_corners;
        }
        if(p->type==LW_TAG('P','C','H','S')||p->type==LW_TAG('P','T','C','H')) stats->cages++;
        if(is_curve) stats->control_curves++;
        if(has_uv) for(j=0;j<p->count;j++) if(!uv[p->first+j].valid) { has_uv=0; stats->uv_missing++; }
        if(p->count<3||is_curve) has_uv=0;
        if(has_uv) for(j=0;j<p->count;j++) { fprintf(f,"vt %.9g %.9g\n",uv[p->first+j].u,uv[p->first+j].v); ++*uv_base; }
        if(has_normals) for(j=0;j<p->count;j++) if(!normals.corners[p->first+j].valid) { has_normals=0; stats->normal_issues++; break; }
        if(has_normals) for(j=0;j<p->count;j++) {
            double n[3];
            if(!lw_transform_normal(m,normals.corners[p->first+j].v,n)) {
                lw_error(e,0,"OBJ normals","singular or non-finite normal transform"); lw_free_triangulation(&triangles); goto done;
            }
            fprintf(f,"vn %.17g %.17g %.17g\n",n[0],n[1],n[2]); ++*normal_base;
        }
        fprintf(f,"g instance_%zu_layer_%u\n",instance,o->layers.v[block->layer].id);
        if(p->material!=LW_NONE) fprintf(f,"usemtl a%zu_m%u\n",asset,p->material); else fprintf(f,"usemtl a%zu_default\n",asset);
        fprintf(f,"# source_primitive %zu\n",i);
        if(has_normals) {
            int smooth=lw_smoothing_angle(o,p->material,NULL,NULL)>0;
            for(j=0;j<p->count;j++) smooth|=normals.corners[p->first+j].explicit_value!=0;
            /* OBJ's single s number cannot encode every angle-based corner
               discontinuity. Explicit vn values carry the authoritative shading. */
            if(smooth) fprintf(f,"s %llu\n",normals.groups[i]==LW_NONE?1ULL:(unsigned long long)normals.groups[i]+2);
            else fputs("s off\n",f);
            if(normals.groups[i]!=LW_NONE) fprintf(f,"# source_smoothing_group_tag %u\n",normals.groups[i]);
        }
        if(triangulated) {
            for(k=0;k<triangles.corners.n;k+=3) {
                fputc('f',f);
                for(j=0;j<3;j++) {
                    size_t corner=triangles.corners.v[k+(determinant>0?2-j:j)];
                    uint32_t point=o->indices.v[p->first+corner];
                    fprintf(f," %zu",vertices[point]);
                    if(has_uv) fprintf(f,"/%zu",first_uv+corner); else if(has_normals) fputc('/',f);
                    if(has_normals) fprintf(f,"/%zu",first_normal+corner);
                    used[point]=1;
                }
                fputc('\n',f);
            }
        } else {
            if(p->count==3&&!is_curve) stats->triangles++;
            fputc(p->count==1?'p':p->count==2||is_curve?'l':'f',f);
            for(j=0;j<p->count;j++) {
                /* Reflection C=diag(1,1,-1) changes the determinant sign. */
                size_t corner=p->count>=3&&!is_curve&&determinant>0?p->count-1-j:j;
                uint32_t point=o->indices.v[p->first+corner];
                fprintf(f," %zu",vertices[point]);
                if(has_uv) fprintf(f,"/%zu",first_uv+corner); else if(has_normals) fputc('/',f);
                if(has_normals) fprintf(f,"/%zu",first_normal+corner);
                used[point]=1;
            }
            fputc('\n',f);
        }
        lw_free_triangulation(&triangles);
    }
    for(i=0;i<count;i++) if(vertices[i]&&!used[i]) fprintf(f,"p %zu\n",vertices[i]);
    for(i=0;i<o->chunks.n;i++) if(o->chunks.v[i].tag==LW_TAG('C','R','V','S')) { fputs("# CRVS preserved in source.bin; not interpreted\n",f); stats->skipped++; }
    ok=1;
done:
    free(uv); free(vertices); free(used); lw_free_normals(&normals); return ok;
}
int lw_write_obj(const char *dir,const LWPackage *p,const LWOptions *opts,LWExportStats *stats,LWError *e) {
    size_t i,j,v=0,vt=0,vn=0,bad=SIZE_MAX; double identity[16],*matrices=NULL; FILE *f=NULL,*mtl=NULL; char *obj_dir=NULL; int ok=0; LWError local={0};
    lw_identity(identity); obj_dir=lw_join(dir,"obj");
    if(!obj_dir) return lw_error(e,0,"allocation","out of memory");
    for(i=0;i<p->objects.n;i++) {
        mtl=create_file(obj_dir,p->names.v[i],".mtl",e); if(!mtl) goto done;
        materials(mtl,&p->objects.v[i],i);
        { int closed=lw_close(mtl,"materials.mtl",e); mtl=NULL; if(!closed) goto done; }
        f=create_file(obj_dir,p->names.v[i],".obj",e); if(!f) goto done;
        fprintf(f,"# lwconvert: FACE polygons triangulated; patches are control cages\n# source-corner-normals-0.1: explicit vn preserve angle cuts; s preserves native group partitions\nmtllib %s.mtl\n",p->names.v[i]); v=vt=vn=0;
        if(!mesh(f,&p->objects.v[i],i,0,LW_NONE,identity,opts->uv_map,&v,&vt,&vn,stats,e)) goto done;
        { int closed=lw_close(f,"mesh.obj",e); f=NULL; if(!closed) goto done; }
    }
    if(!p->is_scene) { ok=1; goto done; }
    matrices=calloc(p->scene.nodes.n?p->scene.nodes.n:1,16*sizeof *matrices);
    if(!matrices) { lw_error(e,0,"allocation","out of memory"); goto done; }
    if(!lw_scene_matrices(&p->scene,opts->frame,matrices,&bad,&local)) {
        snprintf(stats->scene_issue,sizeof stats->scene_issue,"%.40s: %.210s",local.context,local.message); ok=1; goto done;
    }
    for(i=0;i<p->scene.nodes.n;i++) {
        const LWNode *n=&p->scene.nodes.v[i];
        if(n->asset==SIZE_MAX) continue;
        {
            const float normal[3]={0,0,1}; double transformed[3];
            if(!lw_transform_normal(matrices+16*i,normal,transformed)) {
                snprintf(stats->scene_issue,sizeof stats->scene_issue,"singular normal transform; instance %zu",i); ok=1; goto done;
            }
        }
        for(j=0;j<p->objects.v[n->asset].layers.n;j++) {
            const LWLayer *layer=&p->objects.v[n->asset].layers.v[j];
            if(selected(&p->objects.v[n->asset],(uint32_t)j,n->layer)&&(layer->pivot[0]||layer->pivot[1]||layer->pivot[2]||layer->parent!=LW_NONE)) {
                snprintf(stats->scene_issue,sizeof stats->scene_issue,"layer pivot/parent semantics require qualification; instance %zu",i); ok=1; goto done;
            }
        }
    }
    mtl=create_file(obj_dir,p->scene_name,".mtl",e); if(!mtl) goto done;
    for(i=0;i<p->objects.n;i++) materials(mtl,&p->objects.v[i],i);
    { int closed=lw_close(mtl,"scene.mtl",e); mtl=NULL; if(!closed) goto done; }
    f=create_file(obj_dir,p->scene_name,".obj",e); if(!f) goto done;
    fprintf(f,"# base geometry snapshot, frame %.17g; see conversion manifest\nmtllib %s.mtl\n",opts->frame,p->scene_name); v=vt=vn=0;
    for(i=0;i<p->scene.nodes.n;i++) {
        const LWNode *n=&p->scene.nodes.v[i];
        if(n->asset!=SIZE_MAX&&!mesh(f,&p->objects.v[n->asset],n->asset,i,n->layer,matrices+16*i,opts->uv_map,&v,&vt,&vn,stats,e)) goto done;
    }
    { int closed=lw_close(f,"scene.obj",e); f=NULL; if(!closed) goto done; }
    stats->scene_written=1; ok=1;
done:
    if(f) fclose(f);
    if(mtl) fclose(mtl);
    free(matrices); free(obj_dir); return ok;
}
