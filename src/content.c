#include "internal.h"

/* Infer one virtual content directory from distinct, relative scene paths.
   Native names are never rewritten; explicit remapping remains authoritative. */
static int add_path(LWPaths *paths,const char *path,LWError *e) {
    char *copy; size_t i;
    for(i=0;i<paths->n;i++) if(lw_path_equal(paths->v[i],path)) return 1;
    copy=lw_dup(path); if(!copy) return lw_error(e,0,"allocation","out of memory");
    if(!LW_ADD(*paths,copy,e)) { free(copy); return 0; }
    return 1;
}
static int mapped(const char *name,const LWOptions *opts) {
    size_t i,n;
    for(i=0;i<opts->rules.n;i++) {
        char *prefix; int same;
        n=strlen(opts->rules.v[i].prefix);
        if(n>strlen(name)) continue;
        if(name[n]&&opts->rules.v[i].prefix[n-1]!='/'&&opts->rules.v[i].prefix[n-1]!=':'&&name[n]!='/') continue;
        prefix=lw_dup(name); if(!prefix) return 1;
        prefix[n]=0; same=lw_path_equal(prefix,opts->rules.v[i].prefix); free(prefix);
        if(same) return 1;
    }
    return 0;
}
static int present(const LWPaths *files,const char *path) {
    size_t i; for(i=0;i<files->n;i++) if(lw_path_equal(files->v[i],path)) return 1;
    return 0;
}
int lw_infer_content_root(LWPackage *p,const LWOptions *opts,LWError *e) {
    LWPaths names={0}; char *scene_dir=NULL,*parent=NULL; size_t i,j,winners=0,winner=0; int ok=0;
    lw_free_paths(&p->content_candidates); LW_FREE(p->content_scores);
    free(p->inferred_content_root); p->inferred_content_root=NULL; p->content_matches=0; p->content_references=0;
    scene_dir=lw_dirname(opts->input); parent=scene_dir?lw_dirname(scene_dir):NULL;
    if(!scene_dir||!parent) { lw_error(e,0,"allocation","out of memory"); goto done; }
    if(!add_path(&p->content_candidates,opts->root,e)||!add_path(&p->content_candidates,scene_dir,e)||!add_path(&p->content_candidates,parent,e)) goto done;
    for(i=0;i<p->scene.nodes.n;i++) {
        char *name; size_t k;
        if(!p->scene.nodes.v[i].object_path.size) continue;
        name=lw_text(p->scene.nodes.v[i].object_path);
        if(!name) { lw_error(e,0,"allocation","out of memory"); goto done; }
        for(k=0;name[k];k++) if(name[k]=='\\') name[k]='/';
        if(name[0]!='/'&&!strchr(name,':')&&!mapped(name,opts)&&!add_path(&names,name,e)) { free(name); goto done; }
        free(name);
    }
    p->content_references=names.n;
    /* A complete trailing path supplies evidence for its root; a bare filename
       alone can suggest several roots but cannot break a tie between copies. */
    for(i=0;i<names.n;i++) for(j=0;j<p->files.n;j++) {
        size_t a=strlen(names.v[i]),b=strlen(p->files.v[j]); char *root;
        if(a>=b||p->files.v[j][b-a-1]!='/'||!lw_path_equal(names.v[i],p->files.v[j]+b-a)) continue;
        root=lw_dup(p->files.v[j]); if(!root) { lw_error(e,0,"allocation","out of memory"); goto done; }
        root[b-a-1]=0;
        if(lw_path_inside(root,p->content_search_root)&&!add_path(&p->content_candidates,root,e)) { free(root); goto done; }
        free(root);
    }
    for(i=0;i<p->content_candidates.n;i++) {
        size_t score=0;
        for(j=0;j<names.n;j++) {
            char *joined=lw_join(p->content_candidates.v[i],names.v[j]),*full=joined?lw_absolute(joined):NULL;
            free(joined);
            if(full&&lw_path_inside(full,p->content_search_root)&&present(&p->files,full)) score++;
            free(full);
        }
        if(!LW_ADD(p->content_scores,score,e)) goto done;
        if(score>p->content_matches) { p->content_matches=score; winners=1; winner=i; }
        else if(score&&score==p->content_matches) winners++;
    }
    if(winners==1) {
        p->inferred_content_root=lw_dup(p->content_candidates.v[winner]);
        if(!p->inferred_content_root) { lw_error(e,0,"allocation","out of memory"); goto done; }
    }
    ok=1;
done:
    free(scene_dir); free(parent); lw_free_paths(&names); return ok;
}
