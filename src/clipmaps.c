#include "internal.h"
#include <ctype.h>
#include <math.h>

/* One static object-space image mask. Native instance trees remain untouched;
   compatible projections are composed into derived maps only. */
static int numbers(LWString text,double *out,size_t capacity,size_t *count) {
    char buf[256],*p,*end; size_t n=0;
    if(text.size>=sizeof buf) return 0;
    memcpy(buf,text.data,text.size); buf[text.size]=0; p=buf;
    while(*p) {
        while(isspace((unsigned char)*p)) p++;
        if(!*p) break;
        if(n==capacity) return 0;
        out[n]=strtod(p,&end); if(end==p||!isfinite(out[n])) return 0;
        n++; p=end;
    }
    *count=n; return 1;
}
static int values(const LWClipMap *c,size_t i,double *v,size_t expected) {
    const LWTextureField *f=&c->fields.v[i]; size_t n=0,j,k;
    if(!f->block) return numbers(f->value,v,expected,&n)&&n==expected;
    for(j=i+1;j<c->fields.n;j++) if(c->fields.v[j].parent==i) {
        const LWTextureField *child=&c->fields.v[j];
        if(child->block||child->name.size||!numbers(child->value,v+n,expected-n,&k)) return 0;
        n+=k;
    }
    return n==expected;
}
static int named(LWString name,const char *const *names,size_t n) {
    size_t i; for(i=0;i<n;i++) if(lw_string_is(name,names[i])) return 1; return 0;
}
static int parse_legacy_mask(LWClipMap *c,LWTexture *t) {
    const char *issue=NULL; size_t i,j; int axis=-1; unsigned flags=0;
    if(!lw_string_is(c->declaration,"Planar Image Map")) issue="legacy clip projection requires a planar image map";
    if(c->images.n!=1) issue="clip map requires exactly one still image";
    for(i=0;i<c->fields.n&&!issue;i++) {
        const LWTextureField *f=&c->fields.v[i]; double v[3];
        if(f->block) { issue="legacy clip blocks require evaluation"; break; }
        if(lw_string_is(f->name,"TextureImage")) continue;
        if(lw_string_is(f->name,"TextureFlags")) {
            if(!values(c,i,v,1)||v[0]<0||v[0]>15||floor(v[0])!=v[0]) issue="unsupported legacy clip flags";
            else flags=(unsigned)v[0];
        } else if(lw_string_is(f->name,"TextureAxis")) {
            if(!values(c,i,v,1)||(v[0]!=0&&v[0]!=1&&v[0]!=2)) issue="invalid legacy clip axis";
            else axis=(int)v[0];
        } else if(lw_string_is(f->name,"TextureWrapModes")) {
            if(!values(c,i,v,2)||v[0]<0||v[0]>3||v[1]<0||v[1]>3||floor(v[0])!=v[0]||floor(v[1])!=v[1]) issue="invalid clip wrapping";
            else { t->wrap[0]=(uint32_t)v[0]; t->wrap[1]=(uint32_t)v[1]; }
        } else if(lw_string_is(f->name,"TextureSize")||lw_string_is(f->name,"TextureCenter")) {
            float *dst=lw_string_is(f->name,"TextureSize")?t->size:t->center;
            if(!values(c,i,v,3)) issue="malformed legacy clip projection";
            else for(j=0;j<3;j++) { dst[j]=(float)v[j]; if(!isfinite(dst[j])) issue="clip projection exceeds float range"; }
        } else if(lw_string_is(f->name,"TextureFalloff")||lw_string_is(f->name,"TextureVelocity")) {
            if(!values(c,i,v,3)||v[0]!=0||v[1]!=0||v[2]!=0) issue="legacy clip falloff/velocity requires evaluation";
        } else if(lw_string_is(f->name,"TextureValue")) {
            /* LWSC1 image import keeps opacity 1 for values 0, .5 and 1.
               This scalar is neither image opacity nor the alpha cutoff. */
            if(!values(c,i,v,1)) issue="invalid legacy clip value";
        } else issue="unsupported legacy clip parameter; native tree preserved";
    }
    /* LWSC1 flags differ from LWOB TFLG: world=1, negative=2,
       pixel blending=4, antialiasing=8. TextureAxis is independent. */
    t->flags=((flags&2)?16u:0)|((flags&4)?32u:0)|((flags&8)?64u:0);
    c->negative=(flags&2)!=0;
    if(!issue&&axis<0) issue="legacy clip requires an explicit projection axis";
    if(!issue&&(flags&1)) issue="world-space clip map requires evaluation";
    if(!issue) {
        t->flags|=1u<<(unsigned)axis;
        for(j=0;j<3;j++) if(j!=(size_t)axis&&!t->size[j]) issue="zero planar clip size";
    }
    if(issue) { snprintf(c->issue,sizeof c->issue,"%s",issue); return 0; }
    return 1;
}
static int parse_mask(LWClipMap *c,LWTexture *t) {
    size_t i,j,textures=0,images=0; int axis=0,projection=0; const char *issue=NULL;
    const char *const containers[]={"TextureBlock","TextureMap","Image","Clip","Still"};
    const char *const ignored[]={"Channel","AntiAliasing","PixelBlending"};
    memset(t,0,sizeof *t); t->block_type=LW_TAG('I','M','A','P'); t->enabled=1; t->opacity=1;
    t->size[0]=t->size[1]=t->size[2]=1; t->wrap[0]=t->wrap[1]=1; t->tiles[0]=t->tiles[1]=1;
    if(c->declaration.size) return parse_legacy_mask(c,t);
    if(c->images.n!=1) issue="clip map requires exactly one still image";
    for(i=0;i<c->fields.n&&!issue;i++) {
        const LWTextureField *f=&c->fields.v[i]; double v[5]={0};
        if(!f->name.size) continue; /* block values are checked by their owner */
        if(lw_string_is(f->name,"Texture")) textures++;
        else if(lw_string_is(f->name,"ImageMap")) images++;
        else if(lw_string_is(f->name,"Opacity")) {
            if(!values(c,i,v,3)||v[0]!=0||v[1]!=1||v[2]!=0) issue="clip opacity/blending or envelope requires evaluation";
        } else if(lw_string_is(f->name,"Center")||lw_string_is(f->name,"Size")||lw_string_is(f->name,"Rotation")) {
            float *dst=lw_string_is(f->name,"Center")?t->center:lw_string_is(f->name,"Size")?t->size:t->rotation;
            if(!values(c,i,v,4)||v[3]!=0) issue="animated or malformed clip projection";
            else for(j=0;j<3;j++) { dst[j]=(float)v[j]; if(!isfinite(dst[j])) issue="clip projection exceeds float range"; }
        } else if(lw_string_is(f->name,"Falloff")) {
            if(!values(c,i,v,5)||v[0]!=0||v[1]!=0||v[2]!=0||v[3]!=0||v[4]!=0) issue="clip falloff requires evaluation";
        } else if(lw_string_is(f->name,"Coordinates")) {
            if(!values(c,i,v,1)||v[0]!=0) issue="world-space clip map requires evaluation";
        } else if(lw_string_is(f->name,"RefObject")) {
            if(!lw_string_is(f->value,"\"(none)\"")&&!lw_string_is(f->value,"(none)")&&!lw_string_is(f->value,"\"\"")) issue="clip reference object requires evaluation";
        } else if(lw_string_is(f->name,"Projection")) {
            if(!values(c,i,v,1)||(v[0]!=0&&v[0]!=2)) issue="clip projection requires static planar or spherical mapping";
            else { t->projection=(uint32_t)v[0]; projection=1; }
        } else if(lw_string_is(f->name,"Axis")) {
            if(!values(c,i,v,1)||(v[0]!=0&&v[0]!=1&&v[0]!=2)) issue="invalid clip projection axis";
            else { t->flags|=1u<<(unsigned)v[0]; axis=1; }
        } else if(lw_string_is(f->name,"Enable")) {
            if(!values(c,i,v,1)||v[0]!=1) issue="disabled clip image layer";
        } else if(lw_string_is(f->name,"Negative")) {
            if(!values(c,i,v,1)||(v[0]!=0&&v[0]!=1)) issue="invalid clip polarity";
            else if(v[0]) t->flags|=16;
        } else if(lw_string_is(f->name,"WrapOptions")) {
            if(!values(c,i,v,2)||v[0]<0||v[0]>3||v[1]<0||v[1]>3||floor(v[0])!=v[0]||floor(v[1])!=v[1]) issue="invalid clip wrapping";
            else { t->wrap[0]=(uint32_t)v[0]; t->wrap[1]=(uint32_t)v[1]; }
        } else if(lw_string_is(f->name,"WidthWrap")||lw_string_is(f->name,"HeightWrap")) {
            if(!values(c,i,v,2)||v[1]!=0) issue="animated clip tiling requires evaluation";
            else t->tiles[lw_string_is(f->name,"HeightWrap")?1:0]=(float)v[0];
        } else if(!named(f->name,containers,sizeof containers/sizeof *containers)&&!named(f->name,ignored,sizeof ignored/sizeof *ignored))
            issue="unsupported clip parameter/layer; native tree preserved";
    }
    if(!issue&&(textures!=1||images!=1||!axis||!projection)) issue="clip map requires one explicit image projection";
    c->negative=(t->flags&16)!=0;
    if(issue) { snprintf(c->issue,sizeof c->issue,"%s",issue); return 0; }
    return 1;
}
static int close_value(float a,float b) { return fabs((double)a-b)<=1e-6*fmax(1,fmax(fabs(a),fabs(b))); }
static int aligned(const LWTexture *a,const LWTexture *b) {
    size_t i;
    int sphere=b->projection==2;
    if(a->block_type) { if(a->projection!=b->projection) return 0; }
    else if(!lw_string_is(a->type,sphere?"Spherical Image Map":"Planar Image Map")) return 0;
    if((a->flags&7)!=(b->flags&7)||memcmp(a->wrap,b->wrap,sizeof a->wrap)) return 0;
    for(i=0;i<3;i++) if(!close_value(a->center[i],b->center[i])||!close_value(a->rotation[i],b->rotation[i])||(!sphere&&!close_value(a->size[i],b->size[i]))) return 0;
    for(i=0;i<2;i++) if(sphere&&!close_value(a->tiles[i],b->tiles[i])) return 0;
    return 1;
}
static int compatible(const LWMaterial *m,const LWTexture *a,const LWTexture *b,LWClipBinding *binding) {
    size_t i; unsigned axis,axes[2];
    if(aligned(a,b)) return 1;
    /* A Reset/Edge atlas covers the entire rendered domain. Repeat/Mirror
       atlases encode periodicity: a different mask period cannot share them. */
    if(!m->texture_atlas||a->wrap[0]==1||a->wrap[0]==2||a->wrap[1]==1||a->wrap[1]==2) return 0;
    if(b->projection!=0||(a->block_type?a->projection!=0:!lw_string_is(a->type,"Planar Image Map"))) return 0;
    if((a->flags&7)!=(b->flags&7)) return 0;
    for(i=0;i<3;i++) if(a->rotation[i]||b->rotation[i]) return 0;
    axis=(a->flags&1)?0:(a->flags&2)?1:2; axes[0]=axis==0?2:0; axes[1]=axis==1?2:1;
    for(i=0;i<2;i++) {
        unsigned k=axes[i]; double scale,offset;
        if(!b->size[k]) return 0;
        scale=(double)a->size[k]/b->size[k];
        offset=.5-.5*scale+((double)a->center[k]-b->center[k])/b->size[k];
        if(!isfinite(scale)||!isfinite(offset)) return 0;
        binding->mask_uv_transform[i]=scale; binding->mask_uv_transform[i+2]=offset;
    }
    binding->remapped=1; return 1;
}
static int used_material(const LWObject *o,const LWNode *n,size_t material) {
    size_t i;
    for(i=0;i<o->primitives.n;i++) {
        const LWPrimitive *p=&o->primitives.v[i];
        if(p->material==material&&(n->layer==LW_NONE||o->layers.v[o->polygon_blocks.v[p->block].layer].id==n->layer-1)) return 1;
    }
    return 0;
}
static int archive(const char *dir,const LWSource *s,const char *extension,char **uri,LWError *e) {
    char *folder=lw_join(dir,"clipmaps"),*path; int ok;
    if(!folder) return lw_error(e,0,"allocation","out of memory");
    if(!lw_path_exists(folder)&&!lw_mkdir(folder,e)) { free(folder); return 0; }
    free(folder); *uri=lw_named_path("clipmaps",s->sha256,extension); path=*uri?lw_join(dir,*uri):NULL;
    if(!path) return lw_error(e,0,"allocation","out of memory");
    ok=lw_path_exists(path)||lw_write_bytes(path,s->data,s->size,e); free(path); return ok;
}
int lw_prepare_clip_scene(const char *output,LWPackage *p,LWScene *s,LWError *e) {
    size_t i,j,k; LWObject pixels={0};
    pixels.images.v=s->images.v; pixels.images.n=s->images.n;
    for(i=0;i<s->nodes.n;i++) {
        LWNode *n=&s->nodes.v[i]; LWObject *o; LWClipMap *c=NULL; LWTexture mask={0}; const LWImageReference *image=NULL;
        int valid=0; char *ir,*dir;
        if(n->asset>=p->objects.n) continue;
        o=&p->objects.v[n->asset];
        if(n->clip_maps.n==1) {
            c=&n->clip_maps.v[0]; valid=parse_mask(c,&mask);
            if(valid) {
                image=lw_texture_pixels(&pixels,c->images.v[0]);
                if(!image) { valid=0; snprintf(c->issue,sizeof c->issue,"clip image unresolved or undecodable"); }
            }
        } else for(k=0;k<n->clip_maps.n;k++) snprintf(n->clip_maps.v[k].issue,sizeof n->clip_maps.v[k].issue,"multiple clip maps require compositing");
        ir=lw_join(output,"IR"); dir=ir?lw_join(ir,p->names.v[n->asset]):NULL; free(ir);
        if(!dir) return lw_error(e,0,"allocation","out of memory");
        for(j=0;j<o->materials.n;j++) if(used_material(o,n,j)) {
            LWClipBinding b={0},*dst; const LWMaterial *m=&o->materials.v[j];
            b.node=n->id; b.material=(uint32_t)j; b.evidence_count=1;
            if(!LW_ADD(o->clip_bindings,b,e)) { free(dir); return 0; }
            dst=&o->clip_bindings.v[o->clip_bindings.n-1]; dst->scene_path=lw_dup(s->source.path);
            memcpy(dst->scene_sha256,s->source.sha256,sizeof dst->scene_sha256);
            if(c) { dst->offset=c->offset; dst->bytes=c->size; dst->negative=c->negative; }
            if(!dst->scene_path) { free(dir); return lw_error(e,0,"allocation","out of memory"); }
            if(!n->clip_maps.n) snprintf(dst->issue,sizeof dst->issue,"no clip map on this instance");
            else if(!valid) snprintf(dst->issue,sizeof dst->issue,"%s",c?c->issue:"multiple clip maps require compositing");
            else if(!m->base_texture||!m->textured||m->projection_texture>=o->textures.n||!compatible(m,&o->textures.v[m->projection_texture],&mask,dst))
                snprintf(dst->issue,sizeof dst->issue,"clip projection differs from the material image projection");
            if(n->clip_maps.n&&!archive(dir,&s->source,".lws",&dst->scene_uri,e)) { free(dir); return 0; }
            if(dst->issue[0]) { if(c) c->skipped_materials++; continue; }
            dst->offset=c->offset; dst->bytes=c->size; dst->negative=c->negative;
            memcpy(dst->image_sha256,image->sha256,sizeof dst->image_sha256);
            {
                LWSource native={0}; int ok=lw_read_source(image->resolved_path,&native,e);
                if(ok&&strcmp(native.sha256,image->sha256)) ok=lw_error(e,0,"clip-map","mask image changed during conversion");
                if(ok) ok=archive(dir,&native,".image",&dst->image_uri,e);
                lw_free_source(&native); if(!ok) { free(dir); return 0; }
            }
            if(!lw_composite_clip(dir,output,o,j,&mask,image,dst,e)) { free(dir); return 0; }
            c->evaluated_materials++;
        }
        free(dir);
    }
    return 1;
}
int lw_clip_consensus(LWObject *o,LWError *e) {
    size_t i,j,count=o->clip_bindings.n;
    for(i=0;i<count;i++) if(o->clip_bindings.v[i].scene_uri) break;
    if(i==count) { /* Do not invent clip metadata for ordinary unmasked objects. */
        for(i=0;i<count;i++) free(o->clip_bindings.v[i].scene_path);
        LW_FREE(o->clip_bindings); return 1;
    }
    if(o->clip_context_issue[0]) return 1;
    for(i=0;i<o->materials.n;i++) {
        size_t first=SIZE_MAX,uses=0; int agree=1;
        for(j=0;j<count;j++) {
            const LWClipBinding *b=&o->clip_bindings.v[j]; if(b->material!=i) continue;
            uses++; if(!b->base_texture) { agree=0; break; }
            if(first==SIZE_MAX) first=j;
            else if(strcmp(o->clip_bindings.v[first].base_texture,b->base_texture)) { agree=0; break; }
        }
        if(agree&&first!=SIZE_MAX) {
            LWClipBinding b=o->clip_bindings.v[first],*dst;
            /* Native provenance stays in the per-instance evidence records. */
            b.node=LW_NONE; b.scene_path=b.scene_uri=b.image_uri=NULL;
            b.base_texture=b.opacity_texture=NULL; b.evidence_count=uses;
            if(!LW_ADD(o->clip_bindings,b,e)) return 0;
            dst=&o->clip_bindings.v[o->clip_bindings.n-1];
            dst->base_texture=lw_dup(o->clip_bindings.v[first].base_texture);
            dst->opacity_texture=lw_dup(o->clip_bindings.v[first].opacity_texture);
            if(!dst->base_texture||!dst->opacity_texture) return lw_error(e,0,"allocation","out of memory");
        }
    }
    return 1;
}
const LWClipBinding *lw_clip_binding(const LWObject *o,uint32_t node,uint32_t material) {
    size_t i;
    for(i=0;i<o->clip_bindings.n;i++) {
        const LWClipBinding *b=&o->clip_bindings.v[i];
        if(b->node==node&&b->material==material&&b->base_texture) return b;
    }
    return NULL;
}
void lw_json_clip_bindings(FILE *f,const LWObject *o) {
    size_t i;
    fputs("{\"profile\":\"projected-image-clip-0.2\",\"context_issue\":",f); lw_json_string(f,o->clip_context_issue);
    fputs(",\"bindings\":[",f);
    for(i=0;i<o->clip_bindings.n;i++) {
        const LWClipBinding *b=&o->clip_bindings.v[i]; if(i) fputc(',',f);
        fprintf(f,"{\"material\":%u,\"node_id\":",b->material);
        if(b->node==LW_NONE) fputs("null",f); else fprintf(f,"%u",b->node);
        fprintf(f,",\"scope\":\"%s\",\"evidence_count\":%zu,\"status\":\"%s\",\"issue\":",b->node==LW_NONE?"unanimous-object-preview":"scene-instance",b->evidence_count,b->base_texture?"approximated":"not-evaluated");
        lw_json_string(f,b->issue);
        if(b->scene_path) {
            fputs(",\"scene_path\":",f); lw_json_string(f,b->scene_path);
            fprintf(f,",\"scene_sha256\":\"%s\",\"source_offset\":%zu,\"source_bytes\":%zu",b->scene_sha256,b->offset,b->bytes);
        }
        if(b->scene_uri) { fputs(",\"scene_uri\":",f); lw_json_string(f,b->scene_uri); }
        if(b->image_uri) { fputs(",\"image_uri\":",f); lw_json_string(f,b->image_uri); fprintf(f,",\"image_sha256\":\"%s\"",b->image_sha256); }
        if(b->base_texture) {
            if(b->remapped) fprintf(f,",\"mask_uv_transform\":[%.17g,%.17g,%.17g,%.17g]",b->mask_uv_transform[0],b->mask_uv_transform[1],b->mask_uv_transform[2],b->mask_uv_transform[3]);
            fprintf(f,",\"alphaMode\":\"MASK\",\"alphaCutoff\":0.5,\"negative\":%s,\"base_color\":",b->negative?"true":"false");
            lw_json_string(f,b->base_texture); fputs(",\"opacity\":",f); lw_json_string(f,b->opacity_texture);
            fprintf(f,",\"base_color_sha256\":\"%s\",\"opacity_sha256\":\"%s\"",b->texture_sha256[0],b->texture_sha256[1]);
        }
        fputc('}',f);
    }
    fputs("]}",f);
}
