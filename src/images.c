#include "internal.h"

/* This allowlist identifies filename formats, not decoder support or visual
   equivalence. The selected file is preserved verbatim, never transcoded. */
static const char *image_extension(const char *path) {
    static const char *extensions[]={
        ".jpg", ".jpeg", ".jpe", ".png", ".tga", ".tif", ".tiff", ".bmp",
        ".gif", ".psd", ".iff", ".ilbm", ".lbm", ".pic", ".pct", ".pict",
        ".sgi", ".rgb", ".rgba", ".bw", ".hdr", ".exr", ".webp", ".dds",
        ".pcx", ".pnm", ".pbm", ".pgm", ".ppm", ".pam"
    };
    const char *ext=strrchr(lw_basename(path),'.'); size_t i;
    if(ext) for(i=0;i<sizeof extensions/sizeof *extensions;i++) if(lw_path_equal(ext,extensions[i])) return ext;
    return NULL;
}
static unsigned image_priority(const char *extension) {
    /* Archive preference, not a claim about a particular file's compression. */
    static const char *preferred[]={".psd", ".tga", ".png", ".jpeg", ".jpg", ".gif", ".tiff"};
    size_t i;
    if(lw_path_equal(extension,".tif")) extension=".tiff";
    if(lw_path_equal(extension,".jpe")) extension=".jpeg";
    for(i=0;i<sizeof preferred/sizeof *preferred;i++) if(lw_path_equal(extension,preferred[i])) return (unsigned)(sizeof preferred/sizeof *preferred-i);
    return 0;
}
static int separator(char c) { return c=='/'||c=='\\'||c==':'; }
static unsigned suffix_score(char *a,char *b) {
    size_t ae=strlen(a),be=strlen(b); unsigned score=0;
    while(ae&&be) {
        size_t as=ae,bs=be; char ac=a[ae],bc=b[be]; int same;
        while(as&&!separator(a[as-1])) as--;
        while(bs&&!separator(b[bs-1])) bs--;
        a[ae]=0; b[be]=0; same=lw_path_equal(a+as,b+bs); a[ae]=ac; b[be]=bc;
        if(!same) break;
        score++; ae=as?as-1:0; be=bs?bs-1:0;
    }
    return score;
}
static void clear_candidates(LWImageReference *ref) {
    size_t i; for(i=0;i<ref->candidates.n;i++) free(ref->candidates.v[i]);
    ref->candidates.n=0;
}
static int resolve_image(LWImageReference *ref,const char *root,const LWPaths *files,LWError *e) {
    char *name=lw_text(ref->path),*exact=NULL; size_t i;
    unsigned best_score=0,best_priority=0; int best_kind=0,ok=0;
    const char *extension;
    if(!name) return lw_error(e,0,"allocation","out of memory");
    for(i=0;name[i];i++) if(name[i]=='\\') name[i]='/';
    extension=image_extension(name);
    if(!strchr(name,':')&&name[0]!='/') {
        exact=lw_join(root,name);
        if(!exact) { lw_error(e,0,"allocation","out of memory"); goto done; }
    }
    for(i=0;i<files->n;i++) {
        char *target; const char *target_ext; unsigned score,priority; int kind;
        target_ext=image_extension(files->v[i]);
        if(!target_ext) continue;
        priority=image_priority(target_ext);
        target=lw_dup(files->v[i]);
        if(!target) { lw_error(e,0,"allocation","out of memory"); goto done; }
        score=suffix_score(name,target);
        kind=score?2:0;
        if(!score&&extension) {
            size_t a=(size_t)(extension-name),b=(size_t)(target_ext-files->v[i]);
            char ac=name[a],bc=target[b]; name[a]=0; target[b]=0;
            score=suffix_score(name,target); name[a]=ac; target[b]=bc;
            if(score) kind=1;
        }
        if(exact&&lw_path_equal(exact,target)) kind=3;
        free(target);
        if(!kind||kind<best_kind||(kind==best_kind&&score<best_score)) continue;
        if(kind==best_kind&&score==best_score&&priority<best_priority) continue;
        if(kind>best_kind||score>best_score||priority>best_priority) {
            clear_candidates(ref); best_kind=kind; best_score=score; best_priority=priority;
        }
        target=lw_dup(files->v[i]);
        if(!target) { lw_error(e,0,"allocation","out of memory"); goto done; }
        if(!LW_ADD(ref->candidates,target,e)) { free(target); goto done; }
    }
    if(ref->candidates.n==1) {
        ref->resolved_path=lw_dup(ref->candidates.v[0]);
        if(!ref->resolved_path) { lw_error(e,0,"allocation","out of memory"); goto done; }
        snprintf(ref->resolution,sizeof ref->resolution,"%s",best_kind==3?"source-relative":
            best_kind==1?(best_score>1?"unique-image-stem-suffix":"unique-image-stem"):
            (best_score>1?"unique-image-suffix":"unique-image-basename"));
    } else snprintf(ref->resolution,sizeof ref->resolution,"%s",ref->candidates.n?"ambiguous":"missing");
    ok=1;
done:
    free(name); free(exact); return ok;
}
int lw_package_images(const char *dir,const char *source,LWImageReference *refs,size_t count,LWError *e) {
    char *root=NULL,*textures=NULL; LWPaths files={0}; size_t i,j; int ok=0,created=0;
    if(!count) return 1;
    root=lw_dirname(source); textures=lw_join(dir,"textures");
    if(!root||!textures) { lw_error(e,0,"allocation","out of memory"); goto done; }
    /* Each owner supplies its own subtree. Never ascend to the content root or
       another scene's directory; lw_walk also excludes links/reparse points. */
    if(!lw_walk(root,&files,e)) goto done;
    for(i=0;i<count;i++) {
        LWImageReference *ref=&refs[i]; LWSource data={0}; LWError local={0};
        char *base,*filename,*path; size_t length;
        if(!resolve_image(ref,root,&files,e)) goto done;
        if(!ref->resolved_path) continue;
        for(j=0;j<i;j++) if(refs[j].uri&&lw_path_equal(refs[j].resolved_path,ref->resolved_path)) break;
        if(j<i) {
            ref->uri=lw_dup(refs[j].uri); memcpy(ref->sha256,refs[j].sha256,sizeof ref->sha256);
            if(!ref->uri) { lw_error(e,0,"allocation","out of memory"); goto done; }
            continue;
        }
        if(!lw_read_source(ref->resolved_path,&data,&local)) {
            snprintf(ref->issue,sizeof ref->issue,"%.40s: %.210s",local.context,local.message);
            snprintf(ref->resolution,sizeof ref->resolution,"unreadable");
            continue;
        }
        base=lw_output_name(ref->resolved_path);
        if(!base) { lw_free_source(&data); lw_error(e,0,"allocation","out of memory"); goto done; }
        length=strlen(base)+32; filename=malloc(length);
        if(filename) snprintf(filename,length,"%zu-%s",i+1,base);
        free(base);
        ref->uri=filename?lw_join("textures",filename):NULL; free(filename);
        path=ref->uri?lw_join(dir,ref->uri):NULL;
        if(!path) { lw_free_source(&data); lw_error(e,0,"allocation","out of memory"); goto done; }
        if(!created) {
            if(!lw_mkdir(textures,e)) { free(path); lw_free_source(&data); goto done; }
            created=1;
        }
        if(!lw_write_bytes(path,data.data,data.size,e)) { free(path); lw_free_source(&data); goto done; }
        memcpy(ref->sha256,data.sha256,sizeof ref->sha256); free(path); lw_free_source(&data);
    }
    ok=1;
done:
    lw_free_paths(&files); free(root); free(textures); return ok;
}
void lw_free_image(LWImageReference *ref) {
    clear_candidates(ref); LW_FREE(ref->candidates); free(ref->resolved_path); free(ref->uri);
}
void lw_json_images(FILE *f,const LWImageReference *refs,size_t count) {
    size_t i,j; fputc('[',f);
    for(i=0;i<count;i++) {
        const LWImageReference *ref=&refs[i];
        if(i) fputc(',',f);
        fputs("{\"path\":",f); lw_json_name(f,ref->path);
        fprintf(f,",\"source_offset\":%zu,\"clip\":",ref->offset);
        if(ref->clip==LW_NONE) fputs("null",f); else fprintf(f,"%u",ref->clip);
        fputs(",\"status\":\"not-evaluated\",\"resolution\":",f);
        lw_json_string(f,ref->resolution[0]?ref->resolution:"not-evaluated");
        fputs(",\"resolved_path\":",f); if(ref->resolved_path) lw_json_string(f,ref->resolved_path); else fputs("null",f);
        fputs(",\"uri\":",f); if(ref->uri) lw_json_string(f,ref->uri); else fputs("null",f);
        fputs(",\"sha256\":",f); if(ref->uri) lw_json_string(f,ref->sha256); else fputs("null",f);
        fputs(",\"issue\":",f); lw_json_string(f,ref->issue);
        fputs(",\"candidates\":[",f);
        for(j=0;j<ref->candidates.n;j++) { if(j) fputc(',',f); lw_json_string(f,ref->candidates.v[j]); }
        fputs("]}",f);
    }
    fputc(']',f);
}
