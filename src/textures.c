#include "internal.h"
#include <math.h>

#define TAG(s) LW_TAG((s)[0],(s)[1],(s)[2],(s)[3])
#define PI 3.14159265358979323846
static double unit(double x) { return x<0?0:x>1?1:x; }
static unsigned char byte(double x) { return (unsigned char)(unit(x)*255+.5); }
static int spherical(const LWTexture *t) { return t->block_type?t->projection==2:lw_string_is(t->type,"Spherical Image Map"); }
static int planar(const LWTexture *t) { return t->block_type?t->projection==0:lw_string_is(t->type,"Planar Image Map"); }
static int native_uv(const LWTexture *t) { return t->block_type==TAG("IMAP")&&t->projection==5; }
const LWImageReference *lw_texture_pixels(const LWObject *o,size_t index) {
    size_t i; const LWImageReference *ref;
    if(index>=o->images.n) return NULL;
    ref=&o->images.v[index];
    if(ref->rgba) return ref;
    if(ref->uri) for(i=0;i<index;i++) if(o->images.v[i].rgba&&o->images.v[i].uri&&!strcmp(o->images.v[i].uri,ref->uri)) return &o->images.v[i];
    return NULL;
}
int lw_same_mapping(const LWTexture *a,const LWTexture *b) {
    if(native_uv(a)||native_uv(b)) return native_uv(a)&&native_uv(b)&&lw_string_equal(a->uv_map,b->uv_map)&&!memcmp(a->wrap,b->wrap,sizeof a->wrap);
    return spherical(a)==spherical(b)&&planar(a)==planar(b)&&(a->flags&7)==(b->flags&7)&&
        (spherical(a)||!memcmp(a->size,b->size,sizeof a->size))&&!memcmp(a->center,b->center,sizeof a->center)&&
        !memcmp(a->rotation,b->rotation,sizeof a->rotation)&&
        !memcmp(a->wrap,b->wrap,sizeof a->wrap)&&(!spherical(a)||!memcmp(a->tiles,b->tiles,sizeof a->tiles));
}
static int qualify(LWTexture *t,const LWObject *o,const LWOptions *opts,LWError *e) {
    size_t i; const char *issue=NULL;
    if(t->clip_scope) return 1; /* NormalShader uses a separate qualification/bake pass. */
    if(t->block_type) {
        const uint32_t channels[]={TAG("COLR"),TAG("DIFF"),TAG("LUMI"),TAG("SPEC"),TAG("TRAN"),TAG("BUMP")};
        if(!t->enabled) issue="disabled LWO2 texture block; preserved only";
        else if(t->block_type!=TAG("IMAP")&&t->issue[0]) return 1;
        else if(t->block_type!=TAG("IMAP")) issue="LWO2 procedural, gradient or shader plugin; preserved only";
        else if(t->issue[0]) return 1;
        else if(!native_uv(t)&&!planar(t)&&!spherical(t)) issue="LWO2 projection not supported; planar, spherical and UV image maps only";
        else if(t->opacity_type!=0||t->opacity!=1) issue="LWO2 blending requires normal mode at 100% opacity";
        else if(t->has_envelopes&&!t->constant_envelopes) issue="animated or unqualified LWO2 texture envelopes require evaluation";
        else if(t->wrap[0]>3||t->wrap[1]>3) issue="unsupported image wrapping mode";
        else if(t->coordinate_system||(t->reference_object.size&&!lw_string_is(t->reference_object,"(none)"))) issue="LWO2 texture reference object/world coordinates require evaluation";
        else if(native_uv(t)&&!t->uv_map.size) issue="LWO2 UV image map has no named TXUV map";
        else if(opts->uv_map&&(!native_uv(t)||!lw_string_is(t->uv_map,opts->uv_map))) issue="explicit --uv-map differs from native texture binding";
        else if(!lw_texture_pixels(o,t->image)) issue="image unresolved or cannot be decoded; see image_references";
        if(!issue&&!native_uv(t)&&(t->flags&7)!=1&&(t->flags&7)!=2&&(t->flags&7)!=4) issue="invalid projection axis";
        for(i=0;i<3&&!issue;i++) {
            if(t->falloff[i]||(native_uv(t)&&(t->size[i]!=1||t->center[i]||t->rotation[i]))) issue="LWO2 UV transforms/falloff require evaluation";
            else if(planar(t)&&!t->size[i]&&(t->flags&7)!=(1u<<i)) issue="zero planar texture size";
        }
        for(i=0;i<sizeof channels/sizeof *channels;i++) if(t->channel==channels[i]) break;
        if(!issue&&i==sizeof channels/sizeof *channels) issue="LWO2 texture channel not supported by target material";
        /* Do not choose an arbitrary layer from a stack we cannot composite. */
        for(i=0;i<o->textures.n&&!issue;i++) {
            const LWTexture *other=&o->textures.v[i];
            if(other!=t&&other->block_type&&other->block_type!=TAG("SHDR")&&other->enabled&&other->material==t->material&&other->channel==t->channel)
                issue="multiple enabled LWO2 layers on one channel require compositing";
        }
        if(!issue&&native_uv(t)) {
            char *name=lw_text(t->uv_map); LWUV *uv; size_t j;
            if(!name) return lw_error(e,0,"allocation","out of memory");
            uv=lw_corner_uvs(o,name,e); free(name); if(!uv) return 0;
            for(i=0;i<o->primitives.n&&!issue;i++) if(o->primitives.v[i].material==t->material) {
                const LWPrimitive *p=&o->primitives.v[i];
                for(j=0;j<p->count;j++) if(!uv[p->first+j].valid) { issue="named TXUV map missing, incomplete or non-finite for material corners"; break; }
            }
            free(uv);
        }
        if(issue) snprintf(t->issue,sizeof t->issue,"%s",issue); else t->supported=1;
        return 1;
    }
    if(t->channel==TAG("REFL")) issue="environment/reflection requires target-specific lighting; preserved only";
    else if(!spherical(t)&&!planar(t)) issue="procedural or unsupported projection; preserved only";
    else if(!lw_texture_pixels(o,t->image)) issue="image unresolved or cannot be decoded; see image_references";
    else if(opts->uv_map) issue="explicit --uv-map overrides projection; automatic texture binding disabled";
    else if(t->flags&~127u) issue="unsupported texture flags";
    else if(t->flags&8) issue="world-coordinate projection requires per-instance baking";
    else if((t->flags&7)!=1&&(t->flags&7)!=2&&(t->flags&7)!=4) issue="invalid projection axis";
    else if(t->wrap[0]>3||t->wrap[1]>3) issue="unsupported image wrapping mode";
    for(i=0;i<3&&!issue;i++) {
        if(planar(t)&&!t->size[i]&&(t->flags&7)!=(1u<<i)) issue="zero planar texture size";
        else if(t->falloff[i]||t->velocity[i]) issue="texture falloff/velocity requires a native evaluator";
    }
    if(issue) snprintf(t->issue,sizeof t->issue,"%s",issue);
    else t->supported=1;
    return 1;
}
/* Material maps share one generated UV set only when their native projections
   agree. Scalar maps interpolate the surface value (black) toward TVAL (white).
   This is a documented preview approximation, not a LightWave shader evaluator. */
static void sample_uv(const LWImageReference *image,const LWTexture *t,double uv[2],double color[4]) {
    size_t k; int reset=0;
    size_t sx,sy;
    for(k=0;k<2;k++) {
        if(t->wrap[k]==0) { if(uv[k]<0||uv[k]>1) reset=1; uv[k]=unit(uv[k]); }
        else if(t->wrap[k]==3) uv[k]=unit(uv[k]);
        else if(t->wrap[k]==2) { uv[k]-=2*floor(uv[k]/2); if(uv[k]>1) uv[k]=2-uv[k]; }
        else uv[k]-=floor(uv[k]);
    }
    sx=(size_t)(uv[0]*image->width); sy=(size_t)((1-uv[1])*image->height);
    if(sx>=(size_t)image->width) sx=(size_t)image->width-1;
    if(sy>=(size_t)image->height) sy=(size_t)image->height-1;
    for(k=0;k<4;k++) color[k]=reset?(k==3?1:0):image->rgba[4*(sy*(size_t)image->width+sx)+k]/255.0;
    if(t->flags&16) for(k=0;k<3;k++) color[k]=1-color[k];
}
static void sample(const LWObject *o,const LWTexture *t,int x,int y,int w,int h,double color[4]) {
    const LWImageReference *image=lw_texture_pixels(o,t->image); size_t k;
    const LWMaterial *m=&o->materials.v[t->material];
    double uv[2]={((double)x+.5)/w,1-((double)y+.5)/h};
    if(!m->texture_atlas) {
        size_t sx=((size_t)x*2+1)*(size_t)image->width/((size_t)w*2);
        size_t sy=((size_t)y*2+1)*(size_t)image->height/((size_t)h*2);
        for(k=0;k<4;k++) color[k]=image->rgba[4*(sy*(size_t)image->width+sx)+k]/255.0;
        if(t->flags&16) for(k=0;k<3;k++) color[k]=1-color[k];
        return;
    }
    for(k=0;k<2;k++) uv[k]=m->texture_domain[k]+uv[k]*m->texture_domain[k+2];
    sample_uv(image,t,uv,color);
}
/* Bake non-repeat addressing into a finite image domain shared by OBJ/glTF.
   Mirroring needs two tiles. Reset/edge include all rendered UVs and a gutter;
   interpolation of the UV affine remap is exact, geometry remains unchanged. */
static int atlas(LWObject *o,LWMaterial *m,int *w,int *h,LWError *e) {
    const LWTexture *t=&o->textures.v[m->projection_texture]; LWUV *uv;
    double low[2]={0,0},high[2]={1,1},size[2]; size_t i,j,k;
    if(t->wrap[0]==1&&t->wrap[1]==1) return 1;
    uv=lw_texture_uvs(o,e); if(!uv) return -1;
    for(i=0;i<o->primitives.n;i++) {
        const LWPrimitive *p=&o->primitives.v[i];
        if(p->material!=(uint32_t)(m-o->materials.v)) continue;
        for(j=0;j<p->count;j++) {
            const LWUV *v=&uv[p->first+j]; double coord[2]={v->u,v->v};
            if(!v->valid) { free(uv); return 0; }
            for(k=0;k<2;k++) { if(coord[k]<low[k]) low[k]=coord[k]; if(coord[k]>high[k]) high[k]=coord[k]; }
        }
    }
    free(uv);
    for(k=0;k<2;k++) {
        double pixels=k?*h:*w,span;
        if(t->wrap[k]==1||t->wrap[k]==2) { low[k]=0; high[k]=t->wrap[k]==2?2:1; }
        else { low[k]=floor(low[k])-1/pixels; high[k]=ceil(high[k])+1/pixels; }
        span=high[k]-low[k]; size[k]=ceil(span*pixels-1e-7);
        if(!isfinite(size[k])||size[k]<1||size[k]>16384) return 0;
        m->texture_domain[k]=low[k]; m->texture_domain[k+2]=span;
    }
    if(size[0]*size[1]>16777216) return 0;
    *w=(int)size[0]; *h=(int)size[1]; m->texture_atlas=1; return 1;
}
static double scalar(const LWObject *o,const LWTexture *t,double initial,int x,int y,int w,int h) {
    double c[4],brightness;
    if(!t) return initial;
    sample(o,t,x,y,w,h,c); brightness=(c[0]+c[1]+c[2])/3;
    if(t->block_type) return initial+(brightness-initial)*c[3];
    return initial+(t->value-initial)*brightness*c[3];
}
/* Preserve sRGB pixels when intensity is one; apply scalar intensity in linear
   light. Alpha and the specular factor remain linear data. */
static double intensity(double c,double value) {
    double linear=c<=.04045?c/12.92:pow((c+.055)/1.055,2.4);
    linear=unit(linear*unit(value));
    return linear<=.0031308?12.92*linear:1.055*pow(linear,1/2.4)-.055;
}
int lw_save_texture(const char *dir,const char *output,const unsigned char *rgba,int w,int h,char **uri,LWError *e) {
    char *path=NULL; LWSource data={0}; size_t i; int ok=0;
    const char *formats[]={"obj","gltf"};
    if(!lw_encode_png(rgba,w,h,&data,e)) goto done;
    *uri=lw_named_path("textures",data.sha256,".png");
    path=*uri?lw_join(dir,*uri):NULL;
    if(!path) { lw_error(e,0,"allocation","out of memory"); goto done; }
    if(!lw_write_bytes(path,data.data,data.size,e)) goto done;
    free(path); path=NULL;
    for(i=0;i<2;i++) {
        char *format=lw_join(output,formats[i]),*folder=format?lw_join(format,"textures"):NULL;
        if(!folder) { free(format); lw_error(e,0,"allocation","out of memory"); goto done; }
        if(!lw_path_exists(folder)&&!lw_mkdir(folder,e)) { free(format); free(folder); goto done; }
        path=lw_join(format,*uri); free(format); free(folder);
        if(!path) { lw_error(e,0,"allocation","out of memory"); goto done; }
        if(!lw_write_bytes(path,data.data,data.size,e)) goto done;
        free(path); path=NULL;
    }
    ok=1;
done:
    free(path); lw_free_source(&data); return ok;
}
int lw_prepare_textures(const char *dir,const char *output,LWObject *o,const LWOptions *opts,LWError *e) {
    const uint32_t channels[]={TAG("COLR"),TAG("DIFF"),TAG("TRAN"),TAG("LUMI"),TAG("SPEC"),TAG("BUMP")};
    size_t i,j,k;
    for(i=0;i<o->textures.n;i++) LW_TRY(qualify(&o->textures.v[i],o,opts,e));
    for(i=0;i<o->materials.n;i++) {
        LWMaterial *m=&o->materials.v[i]; LWTexture *maps[6]={0}; const LWImageReference *image;
        unsigned char *rgba; int w,h,x,y; size_t pass;
        /* Color, then diffuse, take precedence over auxiliary projections. */
        for(k=0;k<6;k++) for(j=0;j<o->textures.n;j++) {
            LWTexture *t=&o->textures.v[j];
            if(!t->supported||t->material!=i||t->channel!=channels[k]) continue;
            if(m->projection_texture==SIZE_MAX) m->projection_texture=j;
            if(maps[k]||!lw_same_mapping(&o->textures.v[m->projection_texture],t)) {
                t->supported=0; snprintf(t->issue,sizeof t->issue,"additional layer or incompatible projection; preserved only");
            } else maps[k]=t;
        }
        if(m->projection_texture==SIZE_MAX) continue;
        image=lw_texture_pixels(o,o->textures.v[m->projection_texture].image); w=image->width; h=image->height;
        /* Use the largest participating map without changing aspect/UV space. */
        for(k=0;k<6;k++) if(maps[k]) {
            image=lw_texture_pixels(o,maps[k]->image);
            if((size_t)image->width*image->height>(size_t)w*h) { w=image->width; h=image->height; }
        }
        m->textured=1;
        {
            int result=atlas(o,m,&w,&h,e);
            if(result<0) return 0;
            if(!result) {
                for(k=0;k<6;k++) if(maps[k]) { maps[k]->supported=0; snprintf(maps[k]->issue,sizeof maps[k]->issue,"image wrap atlas exceeds raster limits or has invalid projected UVs"); }
                m->textured=0; m->projection_texture=SIZE_MAX; continue;
            }
        }
        rgba=malloc((size_t)w*h*4);
        if(!rgba) return lw_error(e,0,"allocation","out of memory");
        for(pass=0;pass<5;pass++) {
            char **destination=pass==0?&m->base_texture:pass==1?&m->opacity_texture:pass==2?&m->emissive_texture:pass==3?&m->specular_texture:&m->bump_texture;
            if((pass==1&&!maps[2]&&!m->texture_alpha)||(pass==2&&!maps[3]&&!(maps[0]&&m->luminosity))||(pass==3&&!maps[4])||(pass==4&&!maps[5])) continue;
            for(y=0;y<h;y++) for(x=0;x<w;x++) {
                double c[4]={m->color[0],m->color[1],m->color[2],1},value;
                unsigned char *p=rgba+4*((size_t)y*w+x);
                if(maps[0]) sample(o,maps[0],x,y,w,h,c);
                if(pass==0) {
                    value=scalar(o,maps[1],m->diffuse,x,y,w,h);
                    for(k=0;k<3;k++) p[k]=byte(intensity(c[k],value));
                    p[3]=byte(c[3]*(1-unit(scalar(o,maps[2],m->transparency,x,y,w,h))));
                    if(p[3]<255) m->texture_alpha=1;
                } else if(pass==1) {
                    p[0]=p[1]=p[2]=byte(c[3]*(1-unit(scalar(o,maps[2],m->transparency,x,y,w,h)))); p[3]=255;
                } else if(pass==2) {
                    value=scalar(o,maps[3],m->luminosity,x,y,w,h);
                    for(k=0;k<3;k++) p[k]=byte(intensity(c[k],value));
                    p[3]=255;
                } else if(pass==3) {
                    p[0]=p[1]=p[2]=p[3]=byte(scalar(o,maps[4],m->specular,x,y,w,h));
                } else {
                    sample(o,maps[5],x,y,w,h,c); p[0]=p[1]=p[2]=byte((c[0]+c[1]+c[2])/3); p[3]=255;
                }
            }
            if(!lw_save_texture(dir,output,rgba,w,h,destination,e)) { free(rgba); return 0; }
        }
        free(rgba);
    }
    return 1;
}
int lw_composite_clip(const char *dir,const char *output,const LWObject *o,size_t material,const LWTexture *mask,const LWImageReference *image,LWClipBinding *binding,LWError *e) {
    const LWMaterial *m=&o->materials.v[material]; LWSource bytes={0}; LWImageReference base={0};
    LWObject context=*o; LWTexture t=*mask; char *path=lw_join(dir,m->base_texture); int x,y,ok=0;
    if(!path) return lw_error(e,0,"allocation","out of memory");
    if(!lw_read_source(path,&bytes,e)||!lw_decode_raster(&bytes,&base,e)) goto done;
    if(!base.rgba) { lw_error(e,0,"clip-map","cannot decode derived base color"); goto done; }
    context.images.v=(LWImageReference *)image; context.images.n=1; t.image=0; t.material=(uint32_t)material;
    for(y=0;y<base.height;y++) for(x=0;x<base.width;x++) {
        double c[4],coverage; unsigned char *pixel=base.rgba+4*((size_t)y*base.width+x);
        if(binding->remapped) {
            double uv[2]={((double)x+.5)/base.width,1-((double)y+.5)/base.height}; size_t k;
            for(k=0;k<2;k++) {
                uv[k]=m->texture_domain[k]+uv[k]*m->texture_domain[k+2];
                uv[k]=uv[k]*binding->mask_uv_transform[k]+binding->mask_uv_transform[k+2];
            }
            sample_uv(image,&t,uv,c);
        } else sample(&context,&t,x,y,base.width,base.height,c);
        /* LightWave clip white removes the surface; Negative reverses it.
           The image is a scalar mask, sampled without sRGB conversion. */
        coverage=1-(c[0]+c[1]+c[2])/3*c[3];
        pixel[3]=byte(pixel[3]/255.0*coverage);
    }
    if(!lw_save_texture(dir,output,base.rgba,base.width,base.height,&binding->base_texture,e)) goto done;
    for(y=0;y<base.height;y++) for(x=0;x<base.width;x++) {
        unsigned char *pixel=base.rgba+4*((size_t)y*base.width+x);
        pixel[0]=pixel[1]=pixel[2]=pixel[3]>=128?255:0; pixel[3]=255;
    }
    if(!lw_save_texture(dir,output,base.rgba,base.width,base.height,&binding->opacity_texture,e)) goto done;
    ok=1;
done:
    free(path); lw_free_source(&bytes); lw_free_image(&base); return ok;
}
LWUV *lw_texture_uvs(const LWObject *o,LWError *e) {
    LWUV *uv=calloc(o->indices.n?o->indices.n:1,sizeof *uv); size_t i,j,k;
    if(!uv) { lw_error(e,0,"allocation","out of memory"); return NULL; }
    /* Reuse the explicit-map reader: VMAD corner values override VMAP points.
       Materials may select different TXUV maps on the same mesh. */
    for(i=0;i<o->materials.n;i++) {
        const LWMaterial *m=&o->materials.v[i]; const LWTexture *t; LWUV *mapped; char *name;
        if(!m->textured) continue;
        t=&o->textures.v[m->projection_texture]; if(!native_uv(t)) continue;
        name=lw_text(t->uv_map);
        if(!name) { free(uv); lw_error(e,0,"allocation","out of memory"); return NULL; }
        mapped=lw_corner_uvs(o,name,e); free(name);
        if(!mapped) { free(uv); return NULL; }
        for(j=0;j<o->primitives.n;j++) {
            const LWPrimitive *p=&o->primitives.v[j];
            if(p->material==i) memcpy(uv+p->first,mapped+p->first,p->count*sizeof *uv);
        }
        free(mapped);
    }
    for(i=0;i<o->primitives.n;i++) {
        const LWPrimitive *p=&o->primitives.v[i]; const LWMaterial *m; const LWTexture *t;
        double low=1,high=0; int sphere; unsigned axis;
        if(p->material>=o->materials.n) continue;
        m=&o->materials.v[p->material]; if(!m->textured) continue;
        t=&o->textures.v[m->projection_texture]; sphere=spherical(t); axis=(t->flags&1)?0:(t->flags&2)?1:2;
        if(native_uv(t)) goto remap;
        for(j=0;j<p->count;j++) {
            const float *position=o->positions.v+3*o->indices.v[p->first+j]; double q[3],u,v;
            for(k=0;k<3;k++) q[k]=(double)position[k]-t->center[k];
            if(t->block_type) {
                /* Measured Texture Functions convention, not item HPB:
                   texture coordinates = Rx(-P) Ry(H) Rz(-B) (point-center).
                   SIZE is applied afterwards for planar mapping only. */
                double h=t->rotation[0],pitch=t->rotation[1],b=t->rotation[2],x,y,z;
                x=cos(b)*q[0]+sin(b)*q[1]; y=-sin(b)*q[0]+cos(b)*q[1];
                z=-sin(h)*x+cos(h)*q[2]; q[0]=cos(h)*x+sin(h)*q[2];
                q[1]=cos(pitch)*y+sin(pitch)*z; q[2]=-sin(pitch)*y+cos(pitch)*z;
            }
            if(sphere) {
                double a=axis==0?q[2]:axis==1?q[0]:-q[0],b=axis==1?q[2]:q[1];
                double radius=sqrt(a*a+b*b);
                u=(axis==1?1:.5)-atan2(a,b)/(2*PI); if(u>1) u-=1;
                v=.5+atan2(q[axis],radius)/PI;
                if(radius>1e-12) { if(u<low) low=u; if(u>high) high=u; }
            } else {
                unsigned ua=axis==0?2:0,va=axis==1?2:1;
                u=.5+q[ua]/t->size[ua]; v=.5+q[va]/t->size[va];
            }
            uv[p->first+j].u=(float)u; uv[p->first+j].v=(float)v;
            uv[p->first+j].valid=(unsigned char)(isfinite(u)&&isfinite(v)&&fabs(u)<1e20&&fabs(v)<1e20);
        }
        if(sphere) {
            double sum=0; size_t n=0;
            /* Unwrap before tiling; a pole borrows the local polygon longitude. */
            for(j=0;j<p->count;j++) {
                LWUV *v=&uv[p->first+j];
                if(fabs(v->v)<1e-7||fabs(v->v-1)<1e-7) continue;
                if(high-low>.5&&v->u<.5) v->u+=1;
                sum+=v->u; n++;
            }
            for(j=0;j<p->count;j++) {
                LWUV *v=&uv[p->first+j];
                if(n&&(fabs(v->v)<1e-7||fabs(v->v-1)<1e-7)) v->u=(float)(sum/n);
                v->u*=t->tiles[0]; v->v*=t->tiles[1];
                if(!isfinite(v->u)||!isfinite(v->v)) v->valid=0;
            }
        }
remap:
        if(m->texture_atlas) for(j=0;j<p->count;j++) {
            LWUV *v=&uv[p->first+j];
            v->u=(float)((v->u-m->texture_domain[0])/m->texture_domain[2]);
            v->v=(float)((v->v-m->texture_domain[1])/m->texture_domain[3]);
        }
    }
    return uv;
}
static void vector_json(FILE *f,const float v[3]) { fprintf(f,"[%.9g,%.9g,%.9g]",v[0],v[1],v[2]); }
void lw_json_textures(FILE *f,const LWObject *o,uint32_t material) {
    size_t i; int comma=0; fputc('[',f);
    for(i=0;i<o->textures.n;i++) {
        const LWTexture *t=&o->textures.v[i]; char channel[5];
        if(t->material!=material) continue;
        if(comma) fputc(',',f);
        comma=1; lw_tag_text(t->channel,channel);
        fprintf(f,"{\"index\":%zu,\"channel\":",i); lw_json_string(f,channel);
        fputs(",\"type\":",f); lw_json_name(f,t->type);
        fprintf(f,",\"source_offset\":%zu,\"source_bytes\":%zu,\"image_reference\":",t->offset,t->bytes);
        if(t->image==SIZE_MAX) fputs("null",f); else fprintf(f,"%zu",t->image);
        if(t->block_type) {
            fputs(",\"lwo2_block\":{\"ordinal\":",f); lw_json_name(f,t->ordinal);
            fputs(",\"uv_map\":",f); lw_json_name(f,t->uv_map);
            fprintf(f,",\"clip_index\":%u,\"projection\":%u,\"enabled\":%u,\"opacity_type\":%u,\"opacity\":%.9g,\"has_envelopes\":%s,\"coordinate_system\":%u,\"falloff_type\":%u,\"rotation\":",t->clip,t->projection,t->enabled,t->opacity_type,t->opacity,t->has_envelopes?"true":"false",t->coordinate_system,t->falloff_type);
            vector_json(f,t->rotation);
            fprintf(f,",\"constant_envelopes_at_native_values\":%s",t->constant_envelopes?"true":"false");
            fputs(",\"reference_object\":",f); lw_json_name(f,t->reference_object);
            fputs(",\"shader\":",f); lw_json_name(f,t->shader); fputc('}',f);
        }
        if(t->clip_scope) fprintf(f,",\"normal_shader\":{\"scope\":%zu,\"source_shader_offset\":%zu,\"native_NSNS\":%u,\"binding\":\"single private image and UV layer\"}",t->clip_scope,t->clip_scope-1,t->native_normal_space);
        if(t->normal) { fputs(",\"normal_conversion\":",f); lw_json_normal_conversion(f,t->normal); }
        fprintf(f,",\"flags\":%u,\"wrap\":[%u,%u],\"size\":",t->flags,t->wrap[0],t->wrap[1]); vector_json(f,t->size);
        fputs(",\"center\":",f); vector_json(f,t->center); fputs(",\"falloff\":",f); vector_json(f,t->falloff); fputs(",\"velocity\":",f); vector_json(f,t->velocity);
        fprintf(f,",\"value\":%.9g,\"amplitude\":%.9g,\"tiles\":[%.9g,%.9g],\"export_status\":\"%s\",\"issue\":",t->value,t->amplitude,t->tiles[0],t->tiles[1],t->supported?"approximated":"preserved-only"); lw_json_string(f,t->issue);
        fputc('}',f);
    }
    fputc(']',f);
}
