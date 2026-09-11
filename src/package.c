#include "internal.h"

static void normalize(char *s) { for(;*s;s++) if(*s=='\\') *s='/'; }
static int object_file(const char *path) {
    unsigned char b[12]; FILE *f=lw_fopen(path,"rb"); size_t n;
    if(!f) return 0;
    n=fread(b,1,12,f); fclose(f);
    return n==12&&!memcmp(b,"FORM",4)&&(!memcmp(b+8,"LWOB",4)||!memcmp(b+8,"LWO2",4));
}
static unsigned suffix_score(const char *a,const char *b) {
    size_t ae=strlen(a),be=strlen(b); unsigned score=0;
    while(ae&&be) {
        size_t as=ae,bs=be; char *x,*y; int same;
        while(as&&a[as-1]!='/'&&a[as-1]!=':') as--;
        while(bs&&b[bs-1]!='/'&&b[bs-1]!=':') bs--;
        x=malloc(ae-as+1); y=malloc(be-bs+1); if(!x||!y) { free(x); free(y); return 0; }
        memcpy(x,a+as,ae-as); x[ae-as]=0; memcpy(y,b+bs,be-bs); y[be-bs]=0;
        same=lw_path_equal(x,y); free(x); free(y); if(!same) break;
        score++; ae=as?as-1:0; be=bs?bs-1:0;
    }
    return score;
}
static int prefix_match(const char *path,const char *prefix) {
    size_t n=strlen(prefix),len=strlen(path); char *head; int same;
    if(!n||n>len) return 0;
    if(n<len&&prefix[n-1]!='/'&&prefix[n-1]!=':'&&path[n]!='/') return 0;
    head=malloc(n+1); if(!head) return 0;
    memcpy(head,path,n); head[n]=0; same=lw_path_equal(head,prefix); free(head); return same;
}
static int candidate(LWNode *n,const char *path,LWError *e) {
    char *p=lw_dup(path); if(!p) return lw_error(e,0,"allocation","out of memory");
    if(!LW_ADD(n->candidates,p,e)) { free(p); return 0; } return 1;
}
static int resolve_node(LWNode *n,const LWOptions *opts,const LWPaths *files,LWError *e) {
    char *name=lw_text(n->object_path),*path=NULL; size_t i,best_rule=SIZE_MAX,best_length=0; unsigned best=0;
    if(!name) return lw_error(e,0,"allocation","out of memory");
    normalize(name);
    for(i=0;i<opts->rules.n;i++) {
        size_t len=strlen(opts->rules.v[i].prefix);
        if(len>best_length&&prefix_match(name,opts->rules.v[i].prefix)) { best_rule=i; best_length=len; }
    }
    if(best_rule!=SIZE_MAX) {
        const char *tail=name+best_length; while(*tail=='/') tail++;
        path=lw_join(opts->rules.v[best_rule].destination,tail);
        if(!path) { free(name); return lw_error(e,0,"allocation","out of memory"); }
        if(object_file(path)) {
            n->resolved_path=lw_absolute(path);
            if(!n->resolved_path) { free(path); free(name); return lw_error(e,0,"allocation","cannot resolve mapped object path"); }
            snprintf(n->resolution,sizeof n->resolution,"mapped-prefix");
        }
        else snprintf(n->resolution,sizeof n->resolution,"missing-mapped-object");
        if(!candidate(n,path,e)) { free(path); free(name); return 0; }
        free(path); free(name); return 1;
    }
    /* Exact virtual-root path precedes suffix search; historic drives are not OS paths. */
    if(!strchr(name,':')&&name[0]!='/') {
        path=lw_join(opts->root,name);
        if(!path) { free(name); return lw_error(e,0,"allocation","out of memory"); }
        if(object_file(path)) {
            char *full=lw_absolute(path);
            if(full&&lw_path_inside(full,opts->root)) { n->resolved_path=full; snprintf(n->resolution,sizeof n->resolution,"content-root"); }
            else free(full);
        }
        free(path);
        if(n->resolved_path) { int ok=candidate(n,n->resolved_path,e); free(name); return ok; }
    }
    for(i=0;i<files->n;i++) {
        unsigned score=suffix_score(name,files->v[i]);
        if(score>best) {
            size_t j; for(j=0;j<n->candidates.n;j++) free(n->candidates.v[j]); n->candidates.n=0; best=score;
        }
        if(score&&score==best&&!candidate(n,files->v[i],e)) { free(name); return 0; }
    }
    if(n->candidates.n==1) {
        n->resolved_path=lw_dup(n->candidates.v[0]);
        if(!n->resolved_path) { free(name); return lw_error(e,0,"allocation","out of memory"); }
        snprintf(n->resolution,sizeof n->resolution,best>=2?"unique-suffix-candidate":"unique-basename-candidate");
    } else snprintf(n->resolution,sizeof n->resolution,n->candidates.n?"ambiguous":"missing");
    free(name); return 1;
}
static int collect(LWPackage *p,const LWOptions *opts,LWError *e) {
    FILE *f=lw_fopen(opts->input,"rb"); unsigned char sig[4]; size_t n,i;
    if(!f) return lw_error(e,0,"input","cannot open %s",opts->input);
    n=fread(sig,1,4,f); fclose(f);
    p->is_scene=n==4&&!memcmp(sig,"LWSC",4);
    if(!p->is_scene) {
        LWObject object;
        LW_TRY(lw_load_object(opts->input,&object,e));
        if(!LW_ADD(p->objects,object,e)) { lw_free_object(&object); return 0; }
        return 1;
    }
    LW_TRY(lw_load_scene(opts->input,&p->scene,e));
    LW_TRY(lw_walk(opts->root,&p->files,e));
    n=0;
    for(i=0;i<p->files.n;i++) {
        if(object_file(p->files.v[i])) p->files.v[n++]=p->files.v[i]; else free(p->files.v[i]);
    }
    p->files.n=n;
    for(i=0;i<p->scene.nodes.n;i++) {
        LWNode *node=&p->scene.nodes.v[i]; size_t j; LWObject object; LWError local={0};
        if(!node->object_path.size) continue;
        LW_TRY(resolve_node(node,opts,&p->files,e));
        if(!node->resolved_path) { p->unresolved++; continue; }
        for(j=0;j<p->objects.n;j++) if(lw_path_equal(p->objects.v[j].source.path,node->resolved_path)) break;
        if(j==p->objects.n) {
            if(!lw_load_object(node->resolved_path,&object,&local)) {
                snprintf(node->resolution,sizeof node->resolution,"malformed-or-unsupported");
                snprintf(node->issue,sizeof node->issue,"%s at byte %zu: %.180s",local.context,local.offset,local.message);
                p->unresolved++; continue;
            }
            for(j=0;j<p->objects.n;j++) if(!strcmp(p->objects.v[j].source.sha256,object.source.sha256)) break;
            if(j<p->objects.n) lw_free_object(&object);
            else if(!LW_ADD(p->objects,object,e)) { lw_free_object(&object); return 0; }
        }
        if(node->layer!=LW_NONE) {
            size_t k,matches=0;
            for(k=0;k<p->objects.v[j].layers.n;k++) if(p->objects.v[j].layers.v[k].id==node->layer-1) matches++;
            if(matches!=1) {
                snprintf(node->resolution,sizeof node->resolution,matches?"ambiguous-layer":"missing-layer");
                snprintf(node->issue,sizeof node->issue,"scene requests layer %u; expected one LAYR with ID %u, found %zu",node->layer,node->layer-1,matches);
                p->unresolved++; continue;
            }
        }
        node->asset=j;
    }
    return 1;
}
static int assign_names(LWPackage *p,LWError *e) {
    LWPaths bases={0}; size_t i,j; int ok=0;
    for(i=0;i<p->objects.n+(p->is_scene?1:0);i++) {
        char *name=lw_output_name(i<p->objects.n?p->objects.v[i].source.path:p->scene.source.path);
        if(!name) { lw_error(e,0,"allocation","out of memory"); goto done; }
        if(!LW_ADD(bases,name,e)) { free(name); goto done; }
    }
    for(i=0;i<bases.n;i++) {
        char *name=lw_dup(bases.v[i]); size_t suffix=1;
        for(;;) {
            int collision=0;
            if(!name) { lw_error(e,0,"allocation","out of memory"); goto done; }
            for(j=0;j<p->names.n;j++) if(lw_path_equal(name,p->names.v[j])) collision=1;
            /* A generated suffix must not consume another source's natural name. */
            if(suffix>1) for(j=0;j<bases.n;j++) if(lw_path_equal(name,bases.v[j])) collision=1;
            if(!collision) break;
            free(name); suffix++;
            name=malloc(strlen(bases.v[i])+32);
            if(name) snprintf(name,strlen(bases.v[i])+32,"%s-%zu",bases.v[i],suffix);
        }
        if(i==p->objects.n) p->scene_name=name;
        else if(!LW_ADD(p->names,name,e)) { free(name); goto done; }
    }
    ok=1;
done:
    lw_free_paths(&bases); return ok;
}
static int json_output_path(FILE *f,const char *format,const char *name,const char *suffix,LWError *e) {
    char *path=lw_named_path(format,name,suffix);
    if(!path) return lw_error(e,0,"allocation","out of memory");
    lw_json_string(f,path); free(path); return 1;
}
static int write_manifest(const LWOptions *opts,const LWPackage *p,const LWExportStats *stats,const LWGltfStats *gltf,int partial,LWError *e) {
    char *path=lw_join(opts->output,"manifest.json"); FILE *f; size_t i,j,packaged_images=0,unresolved_images=0;
    if(!path) return lw_error(e,0,"allocation","out of memory");
    f=lw_fopen(path,"wb"); if(!f) { free(path); return lw_error(e,0,"output","cannot create package manifest"); }
    fprintf(f,"{\n\"schema_version\":\"0.1\",\"generator\":\"lwconvert %s\",\"status\":\"%s\",\n\"input\":",LWCONVERT_VERSION,partial?"partial":"converted-supported-subset"); lw_json_string(f,opts->input);
    fputs(",\"layout_version\":\"0.2\",\"formats\":{\"obj\":\"generated\",\"IR\":\"generated\",\"gltf\":\"generated\",\"blender\":\"not-implemented\"}",f);
    fputs(",\n\"content_root\":",f); lw_json_string(f,opts->root); fputs(",\n\"path_rules\":[",f);
    for(i=0;i<opts->rules.n;i++) { if(i) fputc(',',f); fputs("{\"prefix\":",f); lw_json_string(f,opts->rules.v[i].prefix); fputs(",\"destination\":",f); lw_json_string(f,opts->rules.v[i].destination); fputc('}',f); }
    for(i=0;i<=p->objects.n;i++) {
        const LWImageReference *refs=i<p->objects.n?p->objects.v[i].images.v:p->scene.images.v;
        size_t count=i<p->objects.n?p->objects.v[i].images.n:p->scene.images.n;
        for(j=0;j<count;j++) { if(refs[j].uri) packaged_images++; else unresolved_images++; }
    }
    fprintf(f,"],\n\"image_references_packaged\":%zu,\"image_references_unresolved\":%zu,\"image_resolution_scope\":\"owner directory and descendants; image filename extensions; exact filename before alternative extension; unique best suffix; original image bytes copied into owning IR/textures; no texture evaluation or material bindings\",\n\"assets\":[",packaged_images,unresolved_images);
    for(i=0;i<p->objects.n;i++) {
        const LWObject *o=&p->objects.v[i]; if(i) fputc(',',f);
        fprintf(f,"{\"index\":%zu,\"id\":\"%s\",\"name\":",i,o->source.sha256); lw_json_string(f,p->names.v[i]);
        fputs(",\"uri\":",f); if(!json_output_path(f,"IR",p->names.v[i],"/object.json",e)) goto failed;
        fputs(",\"obj\":",f); if(!json_output_path(f,"obj",p->names.v[i],".obj",e)) goto failed;
        fputs(",\"mtl\":",f); if(!json_output_path(f,"obj",p->names.v[i],".mtl",e)) goto failed;
        fputs(",\"gltf\":",f); if(!json_output_path(f,"gltf",p->names.v[i],".gltf",e)) goto failed;
        fputs(",\"gltf_bin\":",f); if(!json_output_path(f,"gltf",p->names.v[i],".bin",e)) goto failed;
        fputs(",\"source_path\":",f); lw_json_string(f,o->source.path);
        fprintf(f,",\"images_not_exported\":%zu,\"texture_blocks_not_evaluated\":%zu,\"invalid_map_references\":%zu,\"missing_materials\":%zu,\"opaque_chunks\":%zu,\"non_finite_map_values\":%zu}",o->images.n,o->texture_blocks+o->legacy_textures,o->invalid_map_references,o->missing_materials,o->opaque_chunks,o->non_finite_map_values);
    }
    fputs("],\n\"scene\":",f);
    if(p->is_scene) { if(!json_output_path(f,"IR",p->scene_name,"/scene.json",e)) goto failed; } else fputs("null",f);
    fputs(",\"scene_obj\":",f);
    if(stats->scene_written) { if(!json_output_path(f,"obj",p->scene_name,".obj",e)) goto failed; } else fputs("null",f);
    fputs(",\"scene_mtl\":",f);
    if(stats->scene_written) { if(!json_output_path(f,"obj",p->scene_name,".mtl",e)) goto failed; } else fputs("null",f);
    fputs(",\"scene_obj_issue\":",f); lw_json_string(f,stats->scene_issue);
    fputs(",\"scene_gltf\":",f);
    if(gltf->geometry.scene_written) { if(!json_output_path(f,"gltf",p->scene_name,".gltf",e)) goto failed; } else fputs("null",f);
    fputs(",\"scene_gltf_bin\":",f);
    if(gltf->geometry.scene_written) { if(!json_output_path(f,"gltf",p->scene_name,".bin",e)) goto failed; } else fputs("null",f);
    fputs(",\"scene_gltf_issue\":",f); lw_json_string(f,gltf->geometry.scene_issue);
    fprintf(f,",\"gltf_unsupported_sidedness\":%zu",gltf->unsupported_sidedness);
    fprintf(f,",\n\"gltf_files\":%zu,\"gltf_triangles\":%zu,\"gltf_points\":%zu,\"gltf_line_segments\":%zu,\"gltf_skipped_primitives\":%zu,\"gltf_triangulation_failures\":%zu,\"gltf_nonplanar_faces\":%zu,\"gltf_patch_cages\":%zu,\"gltf_curve_control_polylines\":%zu,\"gltf_unmapped_uv_corners\":%zu,\"gltf_removed_duplicate_corners\":%zu,\"gltf_material_approximations\":%zu,\"gltf_animated_channels_not_exported\":%zu,\"gltf_scene_nodes_not_exported\":%zu,\"gltf_profile\":\"static-base-geometry-0.1; source Z reflected; flat triangle normals; UV (u,1-v); scalar rough dielectric materials; geometry and parent nodes at snapshot frame; no animation, texture bindings, cameras, lights, skinning or morph evaluation\"",gltf->files,gltf->geometry.triangles,gltf->points,gltf->lines,gltf->geometry.skipped,gltf->geometry.triangulation_failures,gltf->geometry.nonplanar_faces,gltf->geometry.cages,gltf->geometry.control_curves,gltf->geometry.uv_missing,gltf->geometry.removed_corners,gltf->materials,gltf->animated_channels,gltf->omitted_nodes);
    fprintf(f,",\n\"frame\":%.17g,\"unresolved_object_instances\":%zu,\"skipped_obj_primitives\":%zu,\"exported_patch_cages\":%zu,\"exported_curve_control_polylines\":%zu,\"unmapped_uv_corners\":%zu,\n\"obj_coordinates\":\"right-handed Y-up; source Z reflected; winding adjusted for transform determinant\",\"uv_map\":",opts->frame,p->unresolved,stats->skipped,stats->cages,stats->control_curves,stats->uv_missing);
    if(opts->uv_map) lw_json_string(f,opts->uv_map); else fputs("null",f);
    fprintf(f,",\n\"obj_triangulated_faces\":%zu,\"obj_triangles\":%zu,\"obj_bridged_hole_faces\":%zu,\"obj_triangulation_failures\":%zu,\"obj_nonplanar_faces\":%zu,\"obj_removed_duplicate_corners\":%zu,\"obj_triangulation\":\"projected ear clipping of FACE boundaries, including paired reverse-edge hole bridges; source corners and native LWIR polygons preserved\"",stats->triangulated_faces,stats->triangles,stats->bridged_faces,stats->triangulation_failures,stats->nonplanar_faces,stats->removed_corners);
    fprintf(f,",\"scene_plugins_not_evaluated\":%zu,\"scene_deformation_features_not_evaluated\":%zu,\n\"scope\":\"native extraction, OBJ/MTL and glTF 2.0 static base geometry; scalar material approximation; no texture decoding/projection, subdivision evaluation, native normals/smoothing, rig/deformation evaluation or Blender backend yet\",\n\"source_policy\":\"parsed input files copied byte-for-byte; unresolved or malformed scene dependencies are reported, not bundled\"\n}\n",p->scene.plugins.n,p->scene.unsupported_features);
    { int ok=lw_close(f,path,e); free(path); return ok; }
failed:
    fclose(f); free(path); return 0;
}
int lw_convert(const LWOptions *opts,LWError *e) {
    LWPackage p={0}; LWExportStats stats={0}; LWGltfStats gltf={0}; size_t i; char *assets=NULL,*dir=NULL; int ok=0,partial=0;
    LWOptions effective=*opts;
    if(lw_path_exists(opts->output)) { lw_error(e,0,"output","output directory already exists; choose a new path"); return -1; }
    if(lw_path_inside(opts->output,opts->root)) { lw_error(e,0,"output","output must be outside the content root"); return -1; }
    if(!collect(&p,opts,e)) goto done;
    if(!assign_names(&p,e)) goto done;
    if(p.is_scene&&!opts->frame_set) effective.frame=p.scene.first_frame;
    opts=&effective;
    if(!lw_mkdir(opts->output,e)) goto done;
    {
        const char *formats[]={"obj","gltf","blender"};
        for(i=0;i<3;i++) {
            dir=lw_join(opts->output,formats[i]); if(!dir) { lw_error(e,0,"allocation","out of memory"); goto done; }
            if(!lw_mkdir(dir,e)) goto done;
            free(dir); dir=NULL;
        }
    }
    assets=lw_join(opts->output,"IR"); if(!assets) { lw_error(e,0,"allocation","out of memory"); goto done; }
    if(!lw_mkdir(assets,e)) goto done;
    for(i=0;i<p.objects.n;i++) {
        LWObject *o=&p.objects.v[i]; size_t k;
        dir=lw_join(assets,p.names.v[i]); if(!dir) { lw_error(e,0,"allocation","out of memory"); goto done; }
        if(!lw_mkdir(dir,e)||!lw_package_images(dir,o->source.path,o->images.v,o->images.n,e)||!lw_write_object(dir,o,e)) goto done;
        free(dir); dir=NULL;
        if(o->images.n||o->texture_blocks||o->legacy_textures||o->invalid_map_references||o->missing_materials||o->non_finite_map_values) partial=1;
        for(k=0;k<o->chunks.n;k++) if(o->chunks.v[k].tag==LW_TAG('C','R','V','S')) partial=1;
        for(k=0;k<o->materials.n;k++) if(o->materials.v[k].source.size) partial=1;
    }
    if(p.is_scene) {
        for(i=0;i<p.scene.nodes.n;i++) if(p.scene.nodes.v[i].unsupported_transform) partial=1;
        dir=lw_join(assets,p.scene_name); if(!dir) { lw_error(e,0,"allocation","out of memory"); goto done; }
        if(!lw_mkdir(dir,e)||!lw_package_images(dir,p.scene.source.path,p.scene.images.v,p.scene.images.n,e)||!lw_write_scene(dir,&p.scene,e)) goto done;
        if(p.scene.images.n) partial=1;
        free(dir); dir=NULL;
    }
    if(!lw_write_obj(opts->output,&p,opts,&stats,e)) goto done;
    if(!lw_write_gltf(opts->output,&p,opts,&gltf,e)) goto done;
    if(p.unresolved||stats.skipped||stats.cages||stats.control_curves||stats.uv_missing||stats.nonplanar_faces||stats.removed_corners||stats.scene_issue[0]||p.scene.plugins.n||p.scene.unsupported_features) partial=1;
    if(gltf.geometry.skipped||gltf.geometry.cages||gltf.geometry.control_curves||gltf.geometry.uv_missing||gltf.geometry.nonplanar_faces||gltf.geometry.removed_corners||gltf.geometry.scene_issue[0]||gltf.unsupported_sidedness) partial=1;
    if(!write_manifest(opts,&p,&stats,&gltf,partial,e)) goto done;
    printf("{\"status\":\"%s\",\"assets\":%zu,\"unresolved_object_instances\":%zu,\"scene_obj\":%s,\"output\":",partial?"partial":"converted-supported-subset",p.objects.n,p.unresolved,stats.scene_written?"true":"false");
    lw_json_string(stdout,opts->output); fputs("}\n",stdout); ok=1;
done:
    free(dir); free(assets); lw_free_package(&p); return ok?(partial?2:0):-1;
}
void lw_free_package(LWPackage *p) {
    size_t i; for(i=0;i<p->objects.n;i++) lw_free_object(&p->objects.v[i]);
    LW_FREE(p->objects); lw_free_scene(&p->scene); lw_free_paths(&p->files); lw_free_paths(&p->names); free(p->scene_name);
}
