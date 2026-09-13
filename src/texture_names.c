#include "internal.h"

/* Names are presentation; full content identities remain in the material IR. */
const char *lw_texture_roles[6]={"base_color","opacity","emissive","specular","bump","normal"};
char **lw_texture_slot(LWMaterial *m,size_t role) {
    switch(role) {
        case 0:return &m->base_texture; case 1:return &m->opacity_texture;
        case 2:return &m->emissive_texture; case 3:return &m->specular_texture;
        case 4:return &m->bump_texture; default:return &m->normal_texture;
    }
}
const char *lw_texture_digest(const LWPackage *p,const char *uri) {
    size_t i,j,k;
    for(i=0;i<p->objects.n;i++) for(j=0;j<p->objects.v[i].materials.n;j++) {
        const LWMaterial *m=&p->objects.v[i].materials.v[j];
        const char *uris[]={m->base_texture,m->opacity_texture,m->emissive_texture,m->specular_texture,m->bump_texture,m->normal_texture};
        for(k=0;k<6;k++) if(uris[k]&&!strcmp(uris[k],uri)) return m->texture_sha256[k];
    }
    return "";
}
typedef struct {
    char **slot,*old,*name,*directory,*image,*object,*material,*ir;
    char *digest;
    size_t role;
    unsigned level,promote;
} TextureName;
typedef LW_ARRAY(TextureName) TextureNames;

static char *component(const char *text,int stem) {
    char *raw=lw_dup(text),*out,*dot; size_t i,n;
    if(!raw) return NULL;
    if(stem&&(dot=strrchr(raw,'.'))!=NULL&&dot!=raw) *dot=0;
    /* Material names are labels: a slash must not discard their prefix. */
    for(i=0;raw[i];i++) if(raw[i]=='/'||raw[i]=='\\') raw[i]='_';
    out=lw_output_name(raw); free(raw);
    if(!out) return NULL;
    n=strlen(out);
    if(n>36) { n=36; while(n&&((unsigned char)out[n]&0xc0)==0x80) n--; out[n]=0; }
    return out;
}
static char *image_name(const LWObject *o,size_t material,size_t role,const char *fallback) {
    static const uint32_t channels[6][3]={
        {LW_TAG('C','O','L','R'),LW_TAG('D','I','F','F'),LW_TAG('T','R','A','N')},
        {LW_TAG('T','R','A','N'),LW_TAG('C','O','L','R'),0},
        {LW_TAG('C','O','L','R'),LW_TAG('L','U','M','I'),0},
        {LW_TAG('S','P','E','C'),0,0},{LW_TAG('B','U','M','P'),0,0},{LW_TAG('N','O','R','M'),0,0}
    };
    size_t i,j;
    for(i=0;i<3&&channels[role][i];i++) for(j=0;j<o->textures.n;j++) {
        const LWTexture *t=&o->textures.v[j];
        if(t->material==material&&t->supported&&t->channel==channels[role][i]&&t->image<o->images.n) {
            const LWImageReference *ref=&o->images.v[t->image];
            if(ref->resolved_path) return component(lw_basename(ref->resolved_path),1);
        }
    }
    return component(fallback,0);
}
static char *candidate(const TextureName *n) {
    char *s=malloc(256); size_t used;
    if(!s) return NULL;
    used=(size_t)snprintf(s,256,"textures/%s__%s",n->directory,n->image);
    if(n->level>=3) used+=(size_t)snprintf(s+used,256-used,"__%s",n->object);
    if(n->level>=2) used+=(size_t)snprintf(s+used,256-used,"__%s",n->material);
    if(n->level>=1) used+=(size_t)snprintf(s+used,256-used,"__%s",lw_texture_roles[n->role]);
    if(n->level>=4) used+=(size_t)snprintf(s+used,256-used,"__%.*s",n->level==4?12:64,n->digest);
    snprintf(s+used,256-used,".png");
    return s;
}
static void release(TextureName *n) {
    free(n->old); free(n->name); free(n->directory); free(n->image);
    free(n->object); free(n->material); free(n->ir);
}
static int copy_named(const char *directory,const char *old,const char *name,const char *digest,LWError *e) {
    char *source=lw_join(directory,old),*target=lw_join(directory,name); LWSource bytes={0},existing={0}; int ok=0;
    if(!source||!target) { lw_error(e,0,"allocation","out of memory"); goto done; }
    if(!lw_read_source(source,&bytes,e)) goto done;
    if(strcmp(bytes.sha256,digest)) { lw_error(e,0,"texture-name","texture content changed: %s",source); goto done; }
    if(lw_path_exists(target)) {
        if(!lw_read_source(target,&existing,e)) goto done;
        if(strcmp(existing.sha256,digest)) { lw_error(e,0,"texture-name","texture collision: %s",target); goto done; }
    } else if(!lw_write_bytes(target,bytes.data,bytes.size,e)) goto done;
    ok=1;
done:
    free(source); free(target); lw_free_source(&bytes); lw_free_source(&existing); return ok;
}
int lw_name_textures(const char *output,LWPackage *p,LWError *e) {
    TextureNames names={0}; size_t i,j,k; int ok=0; char *formats[2]={NULL,NULL};
    formats[0]=lw_join(output,"obj"); formats[1]=lw_join(output,"gltf");
    if(!formats[0]||!formats[1]) { lw_error(e,0,"allocation","out of memory"); goto done; }
    for(i=0;i<p->objects.n;i++) {
        LWObject *o=&p->objects.v[i];
        for(j=0;j<o->materials.n;j++) for(k=0;k<6;k++) {
            TextureName n={0}; LWMaterial *m=&o->materials.v[j]; char *dir,*mat,*ir;
            n.slot=lw_texture_slot(m,k); if(!*n.slot) continue;
            n.digest=m->texture_sha256[k];
            if(strlen(*n.slot)!=77||strncmp(*n.slot,"textures/",9)) { lw_error(e,0,"texture-name","expected temporary PNG content identity"); goto done; }
            memcpy(n.digest,*n.slot+9,64); n.digest[64]=0;
            n.role=k; n.old=lw_dup(*n.slot);
            dir=lw_dirname(p->is_scene?p->scene.source.path:o->source.path); mat=lw_text(m->name); ir=lw_join(output,"IR");
            if(dir&&mat&&ir) {
                n.directory=component(lw_basename(dir),0); n.material=component(mat,0);
                n.object=component(lw_basename(o->source.path),1); n.image=image_name(o,j,k,*mat?mat:lw_basename(o->source.path));
                n.ir=lw_join(ir,p->names.v[i]);
            }
            free(dir); free(mat); free(ir);
            if(!n.old||!n.directory||!n.material||!n.object||!n.image||!n.ir) { release(&n); lw_error(e,0,"allocation","out of memory"); goto done; }
            if(!LW_ADD(names,n,e)) { release(&n); goto done; }
        }
    }
    for(;;) {
        int changed=0;
        for(i=0;i<names.n;i++) {
            free(names.v[i].name); names.v[i].name=candidate(&names.v[i]); names.v[i].promote=0;
            if(!names.v[i].name) { lw_error(e,0,"allocation","out of memory"); goto done; }
        }
        for(i=0;i<names.n;i++) for(j=0;j<i;j++)
            if(lw_path_equal(names.v[i].name,names.v[j].name)&&strcmp(names.v[i].digest,names.v[j].digest))
                names.v[i].promote=names.v[j].promote=1;
        for(i=0;i<names.n;i++) if(names.v[i].promote) {
            if(names.v[i].level==5) { lw_error(e,0,"texture-name","cannot disambiguate texture names"); goto done; }
            names.v[i].level++; changed=1;
        }
        if(!changed) break;
    }
    /* Copy every alias before removing temporary hash paths, which can be shared. */
    for(i=0;i<names.n;i++) {
        TextureName *n=&names.v[i];
        if(!copy_named(n->ir,n->old,n->name,n->digest,e)) goto done;
        for(j=0;j<2;j++) if(!copy_named(formats[j],n->old,n->name,n->digest,e)) goto done;
    }
    for(i=0;i<names.n;i++) {
        TextureName *n=&names.v[i]; const char *dirs[]={n->ir,formats[0],formats[1]};
        for(j=0;j<3;j++) {
            char *path=lw_join(dirs[j],n->old);
            if(!path) { lw_error(e,0,"allocation","out of memory"); goto done; }
            if(lw_path_exists(path)&&!lw_remove_file(path,e)) { free(path); goto done; }
            free(path);
        }
        free(*n->slot); *n->slot=n->name; n->name=NULL;
    }
    ok=1;
done:
    for(i=0;i<names.n;i++) release(&names.v[i]);
    LW_FREE(names); free(formats[0]); free(formats[1]); return ok;
}
