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
static int resolve_node(LWNode *n,const LWOptions *opts,const LWPackage *p,LWError *e) {
    const LWPaths *files=&p->files;
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
    if(p->inferred_content_root&&!strchr(name,':')&&name[0]!='/') {
        char *full;
        path=lw_join(p->inferred_content_root,name); full=path?lw_absolute(path):NULL; free(path);
        if(full&&lw_path_inside(full,p->content_search_root)) for(i=0;i<files->n;i++) if(lw_path_equal(full,files->v[i])) {
            n->resolved_path=lw_dup(files->v[i]);
            if(!n->resolved_path) { free(full); free(name); return lw_error(e,0,"allocation","out of memory"); }
            snprintf(n->resolution,sizeof n->resolution,"inferred-content-root"); break;
        }
        free(full);
        if(n->resolved_path) { int ok=candidate(n,n->resolved_path,e); free(name); return ok; }
    }
    for(i=0;i<files->n;i++) {
        unsigned score=suffix_score(name,files->v[i]);
        if(p->inferred_content_root&&!lw_path_inside(files->v[i],p->inferred_content_root)) continue;
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
    {
        char *dir=lw_dirname(opts->input),*parent=dir?lw_dirname(dir):NULL;
        free(dir);
        if(!parent) return lw_error(e,0,"allocation","out of memory");
        p->content_search_root=lw_dup(lw_path_inside(parent,opts->root)?opts->root:parent);
        if(!p->content_search_root) { free(parent); return lw_error(e,0,"allocation","out of memory"); }
        free(parent);
    }
    n=0;
    for(i=0;i<p->files.n;i++) {
        if(object_file(p->files.v[i])) p->files.v[n++]=p->files.v[i]; else free(p->files.v[i]);
    }
    p->files.n=n;
    LW_TRY(lw_infer_content_root(p,opts,e));
    if(p->content_matches<p->content_references&&!lw_path_inside(p->content_search_root,opts->root)) {
        LWPaths extra={0}; size_t j;
        if(!lw_walk(p->content_search_root,&extra,e)) { lw_free_paths(&extra); return 0; }
        for(j=0;j<extra.n;j++) {
            if(lw_path_inside(extra.v[j],opts->root)||!object_file(extra.v[j])) { free(extra.v[j]); continue; }
            if(!LW_ADD(p->files,extra.v[j],e)) { size_t k; for(k=j;k<extra.n;k++) free(extra.v[k]); LW_FREE(extra); return 0; }
        }
        LW_FREE(extra);
        LW_TRY(lw_infer_content_root(p,opts,e));
    }
    for(i=0;i<p->scene.nodes.n;i++) {
        LWNode *node=&p->scene.nodes.v[i]; size_t j; LWObject object; LWError local={0};
        if(!node->object_path.size) continue;
        LW_TRY(resolve_node(node,opts,p,e));
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
/* A standalone LWO has no native clip-map channel. Inspect only neighboring
   LWS files (owner directory and descendants), resolving actual object links.
   All uses participate in consensus, including uses without a clip map. */
static int clip_context(LWPackage *p,const LWOptions *opts,LWError *e) {
    LWPaths files={0}; LWPackage lookup={0}; LWObject *o=&p->objects.v[0];
    char *root=lw_dirname(o->source.path); size_t i,j,scenes=0; int ok=0;
    if(!root) return lw_error(e,0,"allocation","out of memory");
    for(i=0;i<o->materials.n;i++) if(o->materials.v[i].base_texture) break;
    if(i==o->materials.n) { free(root); return 1; }
    if(!lw_walk(root,&files,e)) goto done;
    for(i=0;i<files.n;i++) if(object_file(files.v[i])) {
        char *path=lw_dup(files.v[i]);
        if(!path) { lw_error(e,0,"allocation","out of memory"); goto done; }
        if(!LW_ADD(lookup.files,path,e)) { free(path); goto done; }
    }
    for(i=0;i<files.n;i++) {
        LWScene scene={0}; LWError local={0}; FILE *f=lw_fopen(files.v[i],"rb"); char sig[4]; size_t n; int uses=0,clips=0;
        if(!f) continue;
        n=fread(sig,1,4,f); fclose(f); if(n!=4||memcmp(sig,"LWSC",4)) continue;
        if(++scenes>128) { snprintf(o->clip_context_issue,sizeof o->clip_context_issue,"more than 128 neighboring scenes; object clip inference disabled"); break; }
        if(!lw_load_scene(files.v[i],&scene,&local)) {
            lw_free_scene(&scene);
            snprintf(o->clip_context_issue,sizeof o->clip_context_issue,"an unreadable neighboring scene prevents unanimous clip inference"); continue;
        }
        for(j=0;j<scene.nodes.n;j++) {
            LWNode *node=&scene.nodes.v[j]; if(!node->object_path.size) continue;
            if(!resolve_node(node,opts,&lookup,e)) { lw_free_scene(&scene); goto done; }
            if(node->resolved_path&&lw_path_equal(node->resolved_path,o->source.path)) {
                node->asset=0; uses=1; if(node->clip_maps.n) clips=1;
            }
            else for(n=0;n<node->candidates.n;n++) if(lw_path_equal(node->candidates.v[n],o->source.path))
                snprintf(o->clip_context_issue,sizeof o->clip_context_issue,"ambiguous neighboring object reference prevents clip inference");
        }
        if(uses) {
            if(clips) {
                char *ir=lw_join(opts->output,"IR"),*asset=ir?lw_join(ir,p->names.v[0]):NULL;
                char *folder=asset?lw_join(asset,"clipmaps"):NULL,*dir=NULL;
                free(ir); free(asset);
                if(!folder) { lw_free_scene(&scene); lw_error(e,0,"allocation","out of memory"); goto done; }
                if(!lw_path_exists(folder)&&!lw_mkdir(folder,e)) { free(folder); lw_free_scene(&scene); goto done; }
                { char label[96]; snprintf(label,sizeof label,"%s-%zu",scene.source.sha256,i); dir=lw_join(folder,label); } free(folder);
                if(!dir) { lw_free_scene(&scene); lw_error(e,0,"allocation","out of memory"); goto done; }
                if(!lw_mkdir(dir,e)||!lw_package_images(dir,scene.source.path,scene.images.v,scene.images.n,e)||!lw_write_scene(dir,&scene,e)) { free(dir); lw_free_scene(&scene); goto done; }
                free(dir);
            }
            if(!lw_prepare_clip_scene(opts->output,p,&scene,e)) { lw_free_scene(&scene); goto done; }
        }
        lw_free_scene(&scene);
    }
    ok=1;
done:
    free(root); lw_free_paths(&files); lw_free_paths(&lookup.files); return ok;
}
static char *source_output_name(const LWSource *source) {
    char *name=lw_output_name(source->path),*extended;
    const char *dot,*extension=NULL; size_t n;
    if(!name) return NULL;
    dot=strrchr(name,'.');
    if(dot&&dot!=name) return name;
    /* Amiga filenames need no extension. Infer only actual objects/scenes;
       a PST_ wrapper containing an object is still a surface preset. */
    if(source->size>=4&&!memcmp(source->data,"LWSC",4)) extension=".lws";
    else if(source->size>=12&&!memcmp(source->data,"FORM",4)&&
            (!memcmp(source->data+8,"LWOB",4)||!memcmp(source->data+8,"LWO2",4))) extension=".lwo";
    if(!extension) return name;
    n=strlen(name);
    if(n>SIZE_MAX-5) { free(name); return NULL; }
    extended=realloc(name,n+5);
    if(!extended) { free(name); return NULL; }
    memcpy(extended+n,extension,5);
    return extended;
}
static int assign_names(LWPackage *p,LWError *e) {
    LWPaths bases={0}; size_t i,j; int ok=0;
    for(i=0;i<p->objects.n+(p->is_scene?1:0);i++) {
        char *name=source_output_name(i<p->objects.n?&p->objects.v[i].source:&p->scene.source);
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
    char *path=lw_join(opts->output,"manifest.json"); FILE *f; size_t i,j,packaged_images=0,unresolved_images=0,clip_maps=0,decoded_images=0,png_images=0,opaque_plugins=0,clips_evaluated=0;
    if(!path) return lw_error(e,0,"allocation","out of memory");
    f=lw_fopen(path,"wb"); if(!f) { free(path); return lw_error(e,0,"output","cannot create package manifest"); }
    fprintf(f,"{\n\"schema_version\":\"0.1\",\"generator\":\"lwconvert %s\",\"status\":\"%s\",\n\"input\":",LWCONVERT_VERSION,partial?"partial":"converted-supported-subset"); lw_json_string(f,opts->input);
    fputs(",\"layout_version\":\"0.2\",\"formats\":{\"obj\":\"generated\",\"IR\":\"generated\",\"gltf\":\"generated\",\"blender\":\"not-implemented\"}",f);
    fputs(",\n\"content_root\":",f); lw_json_string(f,opts->root);
    fprintf(f,",\"skin_profile\":\"%s\",\"skin_profile_policy\":\"%s\"",p->legacy_bone_maps?"lightwave6":"lightwave96",opts->skin_profile_set?"explicit":"oldest-supported-for-file");
    fputs(",\n\"content_root_inference\":{\"search_root\":",f); if(p->content_search_root) lw_json_string(f,p->content_search_root); else fputs("null",f);
    fputs(",\"selected_root\":",f); if(p->inferred_content_root) lw_json_string(f,p->inferred_content_root); else fputs("null",f);
    fprintf(f,",\"status\":\"%s\",\"distinct_references\":%zu,\"matched_references\":%zu,\"candidates\":[",p->inferred_content_root?"unique-best-root":p->content_matches?"ambiguous":"no-relative-path-evidence",p->content_references,p->content_matches);
    for(i=0;i<p->content_candidates.n;i++) { if(i) fputc(',',f); fputs("{\"path\":",f); lw_json_string(f,p->content_candidates.v[i]); fprintf(f,",\"matched_references\":%zu}",p->content_scores.v[i]); }
    fputs("]},\n\"path_rules\":[",f);
    for(i=0;i<opts->rules.n;i++) { if(i) fputc(',',f); fputs("{\"prefix\":",f); lw_json_string(f,opts->rules.v[i].prefix); fputs(",\"destination\":",f); lw_json_string(f,opts->rules.v[i].destination); fputc('}',f); }
    for(i=0;i<=p->objects.n;i++) {
        const LWImageReference *refs=i<p->objects.n?p->objects.v[i].images.v:p->scene.images.v;
        size_t count=i<p->objects.n?p->objects.v[i].images.n:p->scene.images.n;
        for(j=0;j<count;j++) { if(refs[j].uri) packaged_images++; else unresolved_images++; if(refs[j].width) decoded_images++; if(refs[j].png_uri) png_images++; }
    }
    for(i=0;i<p->scene.nodes.n;i++) for(j=0;j<p->scene.nodes.v[i].clip_maps.n;j++) {
        const LWClipMap *c=&p->scene.nodes.v[i].clip_maps.v[j];
        clip_maps+=!c->evaluated_materials||c->skipped_materials; clips_evaluated+=c->evaluated_materials!=0;
    }
    for(i=0;i<p->scene.plugins.n;i++) opaque_plugins+=!p->scene.plugins.v[i].interpreted;
    fprintf(f,"],\n\"scene_clip_maps_not_evaluated\":%zu,\"scene_clip_maps_evaluated\":%zu,\"clip_map_targets\":{\"obj\":{\"representation\":\"MTL map_d with thresholded coverage\",\"portable_binary_cutoff\":false,\"status\":\"aligned-static-image-subset\"},\"gltf\":{\"representation\":\"baseColorTexture alpha with alphaMode MASK and alphaCutoff\",\"requires_extension\":false,\"alphaCutoff\":0.5,\"status\":\"aligned-static-image-subset\"},\"blender\":{\"status\":\"deferred\"}}",clip_maps,clips_evaluated);
    {
        size_t mismatches=0,followers=0;
        for(i=0;i<p->scene.nodes.n;i++) { mismatches+=p->scene.nodes.v[i].key_count_mismatches; followers+=p->scene.nodes.v[i].mirrored_bank_follower!=0; }
        fprintf(f,",\"scene_envelope_key_count_mismatches\":%zu,\"scene_follower_preview_profiles\":%zu,\"scene_snapshot_evaluator\":\"exact keys, constant, linear, stepped, TCB, repeat; qualified mirrored-bank Follower preview; original keys and plugins retained\"",mismatches,followers);
    }
    fprintf(f,",\n\"image_references_packaged\":%zu,\"image_references_unresolved\":%zu,\"image_references_decoded\":%zu,\"image_references_converted_to_png\":%zu,\"image_resolution_scope\":\"owner directory and descendants; image filename extensions; exact filename before alternative extension; unique best suffix; original bytes in owning IR/textures; ILBM decoded to PNG with original retained\",\n\"assets\":[",packaged_images,unresolved_images,decoded_images,png_images);
    for(i=0;i<p->objects.n;i++) {
        const LWObject *o=&p->objects.v[i]; size_t bindings=0,not_exported=0,parsed_blocks=0,k; if(i) fputc(',',f);
        for(j=0;j<o->textures.n;j++) { bindings+=o->textures.v[j].supported!=0; parsed_blocks+=o->textures.v[j].block_type!=0; }
        for(j=0;j<o->images.n;j++) {
            for(k=0;k<o->textures.n;k++) if(o->textures.v[k].supported&&o->textures.v[k].image==j) break;
            not_exported+=k==o->textures.n;
        }
        fprintf(f,"{\"index\":%zu,\"id\":\"%s\",\"name\":",i,o->source.sha256); lw_json_string(f,p->names.v[i]);
        fputs(",\"uri\":",f); if(!json_output_path(f,"IR",p->names.v[i],"/object.json",e)) goto failed;
        fputs(",\"obj\":",f); if(!json_output_path(f,"obj",p->names.v[i],".obj",e)) goto failed;
        fputs(",\"mtl\":",f); if(!json_output_path(f,"obj",p->names.v[i],".mtl",e)) goto failed;
        fputs(",\"gltf\":",f); if(!json_output_path(f,"gltf",p->names.v[i],".gltf",e)) goto failed;
        fputs(",\"gltf_bin\":",f); if(!json_output_path(f,"gltf",p->names.v[i],".bin",e)) goto failed;
        fputs(",\"source_path\":",f); lw_json_string(f,o->source.path);
        fprintf(f,",\"texture_bindings_approximated\":%zu,\"images_not_exported\":%zu,\"texture_blocks_not_evaluated\":%zu,\"invalid_map_references\":%zu,\"missing_materials\":%zu,\"opaque_chunks\":%zu,\"non_finite_map_values\":%zu}",bindings,not_exported,o->texture_blocks+o->textures.n-parsed_blocks-bindings,o->invalid_map_references,o->missing_materials,o->opaque_chunks,o->non_finite_map_values);
    }
    fputs("],\n\"scene\":",f);
    if(p->is_scene) { if(!json_output_path(f,"IR",p->scene_name,"/scene.json",e)) goto failed; } else fputs("null",f);
    fputs(",\"scene_format\":",f);
    if(p->is_scene) fprintf(f,"{\"format\":\"LWSC\",\"version\":%u,\"reader_profile\":\"%s\",\"support\":\"%s\",\"uninterpreted_statements\":%zu}",p->scene.version,lw_scene_reader_profile(&p->scene),p->scene.version==5?"partial":"supported-subset",p->scene.uninterpreted_statements.n);
    else fputs("null",f);
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
    fputs(",\"autonomous_animation\":{\"profile\":\"autonomous-hpb-ik-0.2\",\"status\":",f);
    lw_json_string(f,p->bake.poses?"baked-approximation":opts->no_bake_ik?"disabled":p->bake.issue[0]?"unsupported":"not-needed");
    fprintf(f,",\"scene_exported\":%s",p->bake.poses&&gltf->geometry.scene_written?"true":"false");
    fprintf(f,",\"samples\":%zu,\"goals\":%zu,\"maximum_goal_position_error\":%.17g,\"uri\":",p->bake.samples,p->bake.goals,p->bake.max_goal_error);
    if(p->bake.poses) { if(!json_output_path(f,"IR",p->scene_name,"/baked-animation.json",e)) goto failed; } else fputs("null",f);
    fputs(",\"issue\":",f); lw_json_string(f,p->bake.issue); fputc('}',f);
    fprintf(f,",\"gltf_rig_policy\":\"%s\",\"gltf_rigs\":[",opts->gltf_all_rigs?"all":"skins");
    if(gltf->rigs) for(i=0;i<gltf->rigs->n;i++) {
        const LWRigExport *rig=&gltf->rigs->v[i]; if(i) fputc(',',f);
        fprintf(f,"{\"owner_node\":%zu,\"owner_item\":%u,\"bones\":%zu,\"status\":\"%s\",\"export_status\":\"%s\",\"pose\":\"%s\",\"influence_sets\":%zu,\"unweighted_points_on_object_anchor\":%zu,\"missing_weight_maps\":%zu,\"procedural_bones_not_evaluated\":%zu,\"procedural_bones_converted\":%zu,\"volume_corrections_omitted\":%zu,\"gltf\":",rig->owner,p->scene.nodes.v[rig->owner].id,rig->bones,rig->available?(rig->weighted?(rig->procedural_bones?"procedural-weight-skin-approximation":"explicit-weight-map-skin"):"skeleton-only"):"blocked",rig->written?"written":rig->available?"omitted-by-policy":"blocked",rig->animated?"autonomous animated approximation":"rest; object-local; scene motion and IK not evaluated",rig->sets,rig->unweighted_points,rig->missing_maps,rig->weighted?0:rig->procedural_bones,rig->weighted?rig->procedural_bones:0,rig->weighted?rig->volume_corrections:0);
        if(rig->written) { if(!json_output_path(f,"gltf",rig->name,".gltf",e)) goto failed; } else fputs("null",f);
        fputs(",\"gltf_bin\":",f);
        if(rig->written) { if(!json_output_path(f,"gltf",rig->name,".bin",e)) goto failed; } else fputs("null",f);
        fputs(",\"derived_skin\":",f);
        if(rig->weighted&&rig->procedural_bones) { char suffix[64]; snprintf(suffix,sizeof suffix,"/skin-%08x.json",p->scene.nodes.v[rig->owner].id); if(!json_output_path(f,"IR",p->scene_name,suffix,e)) goto failed; } else fputs("null",f);
        fprintf(f,",\"animated\":%s",rig->animated?"true":"false");
        fputs(",\"issue\":",f); lw_json_string(f,rig->issue); fputc('}',f);
    }
    fputc(']',f);
    fprintf(f,",\"gltf_unsupported_sidedness\":%zu",gltf->unsupported_sidedness);
    fprintf(f,",\"obj_normal_issues\":%zu,\"gltf_normal_issues\":%zu,\"normal_profile\":\"source-corner-normals-0.1\"",stats->normal_issues,gltf->geometry.normal_issues);
    fprintf(f,",\n\"gltf_files\":%zu,\"gltf_triangles\":%zu,\"gltf_points\":%zu,\"gltf_line_segments\":%zu,\"gltf_skipped_primitives\":%zu,\"gltf_triangulation_failures\":%zu,\"gltf_nonplanar_faces\":%zu,\"gltf_patch_cages\":%zu,\"gltf_curve_control_polylines\":%zu,\"gltf_unmapped_uv_corners\":%zu,\"gltf_removed_duplicate_corners\":%zu,\"gltf_material_approximations\":%zu,\"gltf_animated_channels_not_exported\":%zu,\"gltf_scene_nodes_not_exported\":%zu,\"gltf_profile\":\"static-base-geometry-0.1 plus sampled-scene-and-morph-0.2; source Z reflected; source-corner normals with NORM, SMAN and SMGP; LWO2 MORF/SPOT plus compatible external-object morph targets; UV (u,1-v); rough dielectric materials and compatible LWOB image bindings and LWO2 UV image layers; autonomous-hpb-ik-morph-0.3 exports skeletal FK/IK with morph-before-skin when supported; cameras and lights are not exported\"",gltf->files,gltf->geometry.triangles,gltf->points,gltf->lines,gltf->geometry.skipped,gltf->geometry.triangulation_failures,gltf->geometry.nonplanar_faces,gltf->geometry.cages,gltf->geometry.control_curves,gltf->geometry.uv_missing,gltf->geometry.removed_corners,gltf->materials,gltf->animated_channels,gltf->omitted_nodes);
    fprintf(f,",\"gltf_animation_channels\":%zu,\"gltf_animation_samples\":%zu,\"gltf_animation_issue\":",gltf->animation_channels,gltf->animation_samples);
    lw_json_string(f,gltf->animation_issue);
    fprintf(f,",\"gltf_morph_targets\":%zu,\"gltf_morph_animation_channels\":%zu,\"gltf_morph_issues\":%zu,\"gltf_hidden_fully_dissolved_nodes\":%zu",gltf->morph_targets,gltf->morph_animation_channels,gltf->morph_issues,gltf->hidden_dissolved_nodes);
    fprintf(f,",\n\"frame\":%.17g,\"unresolved_object_instances\":%zu,\"skipped_obj_primitives\":%zu,\"exported_patch_cages\":%zu,\"exported_curve_control_polylines\":%zu,\"unmapped_uv_corners\":%zu,\n\"obj_coordinates\":\"right-handed Y-up; source Z reflected; winding adjusted for transform determinant\",\"uv_map\":",opts->frame,p->unresolved,stats->skipped,stats->cages,stats->control_curves,stats->uv_missing);
    if(opts->uv_map) lw_json_string(f,opts->uv_map); else fputs("null",f);
    fprintf(f,",\"normal_space\":\"%s\",\"normal_green\":\"%s\"",lw_normal_space_name(opts->normal_space),opts->normal_green_negative?"negative":"positive");
    fprintf(f,",\n\"obj_triangulated_faces\":%zu,\"obj_triangles\":%zu,\"obj_bridged_hole_faces\":%zu,\"obj_triangulation_failures\":%zu,\"obj_nonplanar_faces\":%zu,\"obj_removed_duplicate_corners\":%zu,\"obj_triangulation\":\"projected ear clipping of FACE boundaries, including paired reverse-edge hole bridges; source corners and native LWIR polygons preserved\"",stats->triangulated_faces,stats->triangles,stats->bridged_faces,stats->triangulation_failures,stats->nonplanar_faces,stats->removed_corners);
    fprintf(f,",\"scene_plugins_not_evaluated\":%zu,\"scene_deformation_features_not_evaluated\":%zu,\n\"scope\":\"native extraction, OBJ/MTL and glTF 2.0 base geometry plus separate object-local rest rigs with explicit normalized maps or measured procedural weight approximations; LWO2 MORF/SPOT and compatible external-object morph targets with sampled LW_MorphMixer/MorphAmount weights; LWOB planar/spherical and LWO2 UV image maps with PNG derivatives; approximate materials; source-corner normals and native smoothing parameters; autonomous-hpb-ik-morph-0.3 provides approximate skeletal animation with glTF morph-before-skin; no baked subdivision, volume corrections, morph-surface animation or Blender backend\",\n\"source_policy\":\"parsed input files copied byte-for-byte; unresolved or malformed scene dependencies are reported, not bundled\"\n}\n",opaque_plugins,p->scene.unsupported_features);
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
    /* File versions establish a lower bound, not the producer's release.
       LWSC 1/3 use the oldest measured profile; LWSC 5 postdates LW6.
       No separate 9.5 profile is qualified, so use the measured 9.6 profile. */
    if(!effective.skin_profile_set) effective.legacy_bone_maps=!p.is_scene||p.scene.version<=3;
    p.legacy_bone_maps=effective.legacy_bone_maps;
    if(!assign_names(&p,e)) goto done;
    lw_resolve_bone_maps(&p);
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
        if(!lw_mkdir(dir,e)||!lw_package_images(dir,o->source.path,o->images.v,o->images.n,e)||!lw_prepare_textures(dir,opts->output,o,opts,e)||!lw_prepare_normal_maps(dir,opts->output,o,opts,e)) goto done;
        free(dir); dir=NULL;
        if(o->images.n||o->texture_blocks||o->legacy_textures||o->invalid_map_references||o->missing_materials||o->non_finite_map_values) partial=1;
        for(k=0;k<o->chunks.n;k++) if(o->chunks.v[k].tag==LW_TAG('C','R','V','S')) partial=1;
        for(k=0;k<o->materials.n;k++) if(o->materials.v[k].source.size) partial=1;
    }
    if(p.is_scene) {
        dir=lw_join(assets,p.scene_name);
        if(!dir) { lw_error(e,0,"allocation","out of memory"); goto done; }
        if(!lw_mkdir(dir,e)||!lw_package_images(dir,p.scene.source.path,p.scene.images.v,p.scene.images.n,e)||!lw_prepare_clip_scene(opts->output,&p,&p.scene,e)) goto done;
        free(dir); dir=NULL;
    } else if(!clip_context(&p,opts,e)) goto done;
    for(i=0;i<p.objects.n;i++) if(!lw_clip_consensus(&p.objects.v[i],e)) goto done;
    if(!lw_name_textures(opts->output,&p,e)) goto done;
    for(i=0;i<p.objects.n;i++) {
        dir=lw_join(assets,p.names.v[i]);
        if(!dir) { lw_error(e,0,"allocation","out of memory"); goto done; }
        if(!lw_write_object(dir,&p.objects.v[i],e)) goto done;
        free(dir); dir=NULL;
    }
    if(p.is_scene) {
        for(i=0;i<p.scene.nodes.n;i++) {
            const LWNode *node=&p.scene.nodes.v[i];
            if(node->unsupported_transform||node->key_count_mismatches||node->clip_maps.n||node->morph_issue[0]||
               (node->object_dissolve.size&&(!node->object_dissolve_static||(node->object_dissolve_value>0&&node->object_dissolve_value<1)))) partial=1;
        }
        dir=lw_join(assets,p.scene_name); if(!dir) { lw_error(e,0,"allocation","out of memory"); goto done; }
        if(!lw_write_scene(dir,&p.scene,e)) goto done;
        if(!opts->no_bake_ik) {
            for(i=0;i<p.scene.nodes.n;i++) {
                size_t k; const LWNode *n=&p.scene.nodes.v[i];
                if(n->bone.owner!=LW_NONE) break;
                for(k=0;k<n->rig_parameters.n;k++) if(lw_string_is(n->rig_parameters.v[k].name,"GoalObject")) break;
                if(k<n->rig_parameters.n) break;
            }
            if(i<p.scene.nodes.n) {
                if(!lw_bake_ik(&p.scene,&p.bake,e)) goto done;
                if(p.bake.poses&&!lw_write_ik_bake(dir,&p.scene,&p.bake,e)) goto done;
                if(p.bake.issue[0]||p.bake.goals) partial=1;
            }
        }
        if(p.scene.images.n) partial=1;
        free(dir); dir=NULL;
    }
    if(!lw_write_obj(opts->output,&p,opts,&stats,e)) goto done;
    if(!lw_write_gltf(opts->output,&p,opts,&gltf,e)) goto done;
    if(p.unresolved||stats.skipped||stats.cages||stats.control_curves||stats.uv_missing||stats.nonplanar_faces||stats.removed_corners||stats.scene_issue[0]||p.scene.plugins.n||p.scene.unsupported_features) partial=1;
    /* LWSC 5 is an explicitly qualified extraction subset, not a full scene evaluator. */
    if(p.is_scene&&p.scene.version==5) partial=1;
    if(gltf.geometry.skipped||gltf.geometry.cages||gltf.geometry.control_curves||gltf.geometry.uv_missing||gltf.geometry.nonplanar_faces||gltf.geometry.removed_corners||gltf.geometry.scene_issue[0]||gltf.unsupported_sidedness) partial=1;
    if(gltf.animation_issue[0]) partial=1;
    if(gltf.morph_issues) partial=1;
    if(stats.normal_issues||gltf.geometry.normal_issues) partial=1;
    if(!write_manifest(opts,&p,&stats,&gltf,partial,e)) goto done;
    printf("{\"status\":\"%s\",\"assets\":%zu,\"unresolved_object_instances\":%zu,\"scene_obj\":%s,\"output\":",partial?"partial":"converted-supported-subset",p.objects.n,p.unresolved,stats.scene_written?"true":"false");
    lw_json_string(stdout,opts->output); fputs("}\n",stdout); ok=1;
done:
    free(dir); free(assets); lw_free_gltf_stats(&gltf); lw_free_package(&p); return ok?(partial?2:0):-1;
}
void lw_free_package(LWPackage *p) {
    free(p->bake.poses);
    size_t i; for(i=0;i<p->objects.n;i++) lw_free_object(&p->objects.v[i]);
    LW_FREE(p->objects); lw_free_scene(&p->scene); lw_free_paths(&p->files); lw_free_paths(&p->names); free(p->scene_name);
    free(p->content_search_root); free(p->inferred_content_root); lw_free_paths(&p->content_candidates); LW_FREE(p->content_scores);
}
