#include "internal.h"
#include <math.h>

#define TAG(s) LW_TAG((s)[0],(s)[1],(s)[2],(s)[3])
static int ensure_layer(LWObject *o,uint32_t *layer,LWError *e) {
    if(*layer==LW_NONE) {
        LWLayer l={0}; l.parent=LW_NONE;
        LW_TRY(LW_ADD(o->layers,l,e)); *layer=(uint32_t)o->layers.n-1;
    }
    return 1;
}
static LWString unpadded_string(LWReader r) {
    LWString s; const unsigned char *end=memchr(r.data+r.pos,0,r.size-r.pos);
    s.data=r.data+r.pos; s.size=end?(size_t)(end-s.data):r.size-r.pos; return s;
}
static int image_ref(LWObject *o,LWReader r,uint32_t clip) {
    LWImageReference ref={0}; ref.path=unpadded_string(r); ref.offset=r.base+r.pos; ref.clip=clip;
    if(!ref.path.size||lw_string_is(ref.path,"(none)")||lw_string_is(ref.path,"<none>")) return 1;
    return LW_ADD(o->images,ref,r.error);
}
static int material_chunks(LWObject *o,LWMaterial *m,LWReader r,unsigned depth,int direct,uint32_t clip) {
    if(depth>32) return lw_error(r.error,r.base,"SURF","subchunk nesting exceeds 32");
    while(r.pos<r.size) {
        LWReader c; uint32_t tag,v; size_t off;
        LW_TRY(lw_chunk(&r,1,&tag,&c,&off));
        if(tag==TAG("STIL")|| (o->format==TAG("LWOB")&&(tag==TAG("TIMG")||tag==TAG("RIMG")))) LW_TRY(image_ref(o,c,clip));
        if(tag==TAG("BLOK")||tag==TAG("TMAP")) {
            if(tag==TAG("BLOK")) o->texture_blocks++;
            LW_TRY(material_chunks(o,m,c,depth+1,0,clip));
        } else if(!direct&&(tag==TAG("IMAP")||tag==TAG("PROC")||tag==TAG("GRAD")||tag==TAG("SHDR"))) {
            LWString ordinal; LW_TRY(lw_s0(&c,&ordinal));
            LW_TRY(material_chunks(o,m,c,depth+1,0,clip));
        }
        if(o->format==TAG("LWOB") && (tag==TAG("CTEX")||tag==TAG("DTEX")||tag==TAG("STEX")||tag==TAG("RTEX")||tag==TAG("TTEX")||tag==TAG("LTEX")||tag==TAG("BTEX"))) o->legacy_textures++;
        if(!m||!direct) continue;
        if(tag==TAG("COLR")) {
            if(o->format==TAG("LWOB")) {
                const unsigned char *rgb; LW_TRY(lw_take(&c,3,&rgb));
                m->color[0]=rgb[0]/255.f; m->color[1]=rgb[1]/255.f; m->color[2]=rgb[2]/255.f;
            } else { LW_TRY(lw_float(&c,&m->color[0])); LW_TRY(lw_float(&c,&m->color[1])); LW_TRY(lw_float(&c,&m->color[2])); }
            m->present|=1;
        } else if(tag==TAG("FLAG")) { LW_TRY(lw_u16(&c,&m->flags)); }
        else if(tag==TAG("SIDE")) { LW_TRY(lw_u16(&c,&m->side)); }
        else if(tag==TAG("SMAN")) { LW_TRY(lw_float(&c,&m->smoothing)); m->present|=32; }
        else {
            float *field=NULL; uint32_t bit=0; int is_float=o->format==TAG("LWO2");
            if(tag==TAG("DIFF")||tag==TAG("VDIF")) { field=&m->diffuse; bit=2; }
            if(tag==TAG("SPEC")||tag==TAG("VSPC")) { field=&m->specular; bit=4; }
            if(tag==TAG("LUMI")||tag==TAG("VLUM")) { field=&m->luminosity; bit=8; }
            if(tag==TAG("TRAN")||tag==TAG("VTRN")) { field=&m->transparency; bit=16; }
            if(field) {
                float f;
                if((tag>>24)=='V') is_float=1;
                if(is_float) { LW_TRY(lw_float(&c,&f)); *field=f; m->float_fields|=bit; }
                else { LW_TRY(lw_u16(&c,&v)); if(!(m->float_fields&bit)) *field=v/256.f; }
                m->present|=bit;
            }
        }
    }
    return 1;
}
static int primitive(LWObject *o,LWReader *r,uint32_t block,uint32_t type,int modern,uint32_t parent,unsigned depth) {
    LWPrimitive p={0}; LWPolygonBlock *b=&o->polygon_blocks.v[block];
    LWPointBlock *points=&o->point_blocks.v[b->point_block];
    uint32_t n,i,index,surface,ndetail=0,id;
    if(depth>32) return lw_error(r->error,r->base+r->pos,"POLS","detail polygon nesting exceeds 32");
    LW_TRY(lw_u16(r,&n)); p.flags=modern?(n>>10):0; p.count=modern?(n&1023):n;
    p.first=(uint32_t)o->indices.n; p.type=type; p.block=block;
    p.material=LW_NONE; p.tag_index=LW_NONE; p.detail_parent=parent;
    for(i=0;i<p.count;i++) {
        LW_TRY(modern?lw_vx(r,&index):lw_u16(r,&index));
        if(index>=points->count) return lw_error(r->error,r->base+r->pos,"POLS","point index %u exceeds point block (%u)",index,points->count);
        index+=points->first; LW_TRY(LW_ADD(o->indices,index,r->error));
    }
    if(!modern) {
        LW_TRY(lw_u16(r,&surface));
        p.legacy_surface=surface>=32768?(int32_t)surface-65536:(int32_t)surface;
        if(p.legacy_surface) p.tag_index=(uint32_t)(p.legacy_surface<0?-p.legacy_surface:p.legacy_surface)-1;
        if(p.legacy_surface<0) LW_TRY(lw_u16(r,&ndetail));
    }
    id=(uint32_t)o->primitives.n; LW_TRY(LW_ADD(o->primitives,p,r->error));
    for(i=0;i<ndetail;i++) LW_TRY(primitive(o,r,block,type,0,id,depth+1));
    return 1;
}
static int map_chunk(LWObject *o,LWReader r,int discontinuous,uint32_t point_block,uint32_t polygon_block) {
    LWMap m={0}; uint32_t i; LWError *e=r.error;
    if(point_block==LW_NONE) return lw_error(e,r.base,"VMAP","map without a point block");
    m.point_block=point_block; m.polygon_block=discontinuous?polygon_block:LW_NONE; m.discontinuous=discontinuous;
    LW_TRY(lw_u32(&r,&m.type)); LW_TRY(lw_u16(&r,&m.dimension)); LW_TRY(lw_s0(&r,&m.name));
    /* Own the allocations in the object immediately so every error path frees them. */
    LW_TRY(LW_ADD(o->maps,m,e));
    while(r.pos<r.size) {
        LWMapEntry entry={0}; LWMap *map=&o->maps.v[o->maps.n-1];
        entry.polygon=LW_NONE; LW_TRY(lw_vx(&r,&entry.point));
        if(discontinuous) LW_TRY(lw_vx(&r,&entry.polygon));
        LW_TRY(LW_ADD(map->entries,entry,e));
        for(i=0;i<map->dimension;i++) {
            float value; uint32_t bits; LW_TRY(lw_u32(&r,&bits)); memcpy(&value,&bits,4);
            if(!isfinite(value)) o->non_finite_map_values++;
            LW_TRY(LW_ADD(map->values,value,e));
        }
    }
    return 1;
}
static int parse_form(LWObject *o,LWReader r) {
    uint32_t layer=LW_NONE,point_block=LW_NONE,polygon_block=LW_NONE,tag_base=0;
    int modern=o->format==TAG("LWO2");
    while(r.pos<r.size) {
        uint32_t tag; LWReader c; size_t off; LWChunk chunk; int handled=1;
        LW_TRY(lw_chunk(&r,0,&tag,&c,&off));
        chunk.tag=tag; chunk.offset=off; chunk.size=c.size; chunk.status="parsed";
        if(tag==TAG("LAYR")&&modern) {
            LWLayer l={0}; unsigned j; l.parent=LW_NONE;
            LW_TRY(lw_u16(&c,&l.id)); LW_TRY(lw_u16(&c,&l.flags));
            for(j=0;j<3;j++) LW_TRY(lw_float(&c,&l.pivot[j]));
            LW_TRY(lw_s0(&c,&l.name));
            if(c.pos<c.size) { LW_TRY(lw_u16(&c,&l.parent)); if(l.parent==65535) l.parent=LW_NONE; }
            if(c.pos!=c.size) return lw_error(c.error,c.base+c.pos,"LAYR","unexpected trailing layer data");
            LW_TRY(LW_ADD(o->layers,l,c.error)); layer=(uint32_t)o->layers.n-1;
            point_block=polygon_block=LW_NONE;
        } else if(tag==TAG("PNTS")) {
            LWPointBlock b; size_t i;
            LW_TRY(ensure_layer(o,&layer,c.error));
            if(c.size%12) return lw_error(c.error,c.base,"PNTS","size is not a multiple of 12");
            b.layer=layer; b.first=(uint32_t)(o->positions.n/3); b.count=(uint32_t)(c.size/12);
            LW_TRY(LW_ADD(o->point_blocks,b,c.error)); point_block=(uint32_t)o->point_blocks.n-1;
            polygon_block=LW_NONE;
            for(i=0;i<c.size/4;i++) { float value; LW_TRY(lw_float(&c,&value)); LW_TRY(LW_ADD(o->positions,value,c.error)); }
        } else if(tag==TAG("POLS")||(!modern&&tag==TAG("PCHS"))) {
            LWPolygonBlock b={0};
            if(point_block==LW_NONE) return lw_error(c.error,c.base,"POLS","polygons without a point block");
            b.layer=layer; b.point_block=point_block; b.first=(uint32_t)o->primitives.n;
            b.type=tag==TAG("PCHS")?TAG("PCHS"):TAG("FACE");
            if(modern) LW_TRY(lw_u32(&c,&b.type));
            LW_TRY(LW_ADD(o->polygon_blocks,b,c.error)); polygon_block=(uint32_t)o->polygon_blocks.n-1;
            while(c.pos<c.size) LW_TRY(primitive(o,&c,polygon_block,b.type,modern,LW_NONE,0));
            o->polygon_blocks.v[polygon_block].count=(uint32_t)o->primitives.n-b.first;
        } else if(tag==TAG("TAGS")||tag==TAG("SRFS")) {
            tag_base=(uint32_t)o->tags.n;
            while(c.pos<c.size) { LWString s; LW_TRY(lw_s0(&c,&s)); LW_TRY(LW_ADD(o->tags,s,c.error)); }
        } else if(tag==TAG("PTAG")&&modern) {
            uint32_t kind; LW_TRY(lw_u32(&c,&kind));
            while(c.pos<c.size) {
                LWTagAssignment a={0}; a.type=kind; a.block=polygon_block;
                LW_TRY(lw_vx(&c,&a.polygon)); LW_TRY(lw_u16(&c,&a.tag)); a.tag+=tag_base;
                LW_TRY(LW_ADD(o->assignments,a,c.error));
            }
        } else if((tag==TAG("VMAP")||tag==TAG("VMAD"))&&modern) {
            LW_TRY(map_chunk(o,c,tag==TAG("VMAD"),point_block,polygon_block));
        } else if(tag==TAG("SURF")) {
            LWMaterial m={0}; m.color[0]=m.color[1]=m.color[2]=.784313725f; m.diffuse=1; m.side=1;
            LW_TRY(lw_s0(&c,&m.name)); if(modern) LW_TRY(lw_s0(&c,&m.source));
            LW_TRY(material_chunks(o,&m,c,0,1,LW_NONE)); LW_TRY(LW_ADD(o->materials,m,c.error)); chunk.status="partial";
        } else if(tag==TAG("CLIP")&&modern) {
            uint32_t id; LW_TRY(lw_u32(&c,&id)); LW_TRY(material_chunks(o,NULL,c,0,1,id)); chunk.status="partial";
        } else handled=0;
        if(!handled) { chunk.status="preserved-opaque"; o->opaque_chunks++; }
        LW_TRY(LW_ADD(o->chunks,chunk,c.error));
    }
    return 1;
}
static void bind_object(LWObject *o) {
    size_t i,j;
    for(i=0;i<o->assignments.n;i++) {
        LWTagAssignment *a=&o->assignments.v[i];
        if(a->block>=o->polygon_blocks.n||a->polygon>=o->polygon_blocks.v[a->block].count||a->tag>=o->tags.n) { o->invalid_map_references++; continue; }
        if(a->type==TAG("SURF")) o->primitives.v[o->polygon_blocks.v[a->block].first+a->polygon].tag_index=a->tag;
    }
    for(i=0;i<o->primitives.n;i++) {
        LWPrimitive *p=&o->primitives.v[i]; uint32_t x,y;
        if(p->tag_index<o->tags.n) for(j=0;j<o->materials.n;j++) if(lw_string_equal(o->tags.v[p->tag_index],o->materials.v[j].name)) { p->material=(uint32_t)j; break; }
        if(p->tag_index!=LW_NONE&&p->material==LW_NONE) o->missing_materials++;
        for(x=0;x<p->count;x++) {
            for(y=0;y<x;y++) if(o->indices.v[p->first+x]==o->indices.v[p->first+y]) break;
            if(y<x) { o->repeated_primitives++; break; }
        }
    }
    for(i=0;i<o->maps.n;i++) {
        LWMap *m=&o->maps.v[i];
        for(j=0;j<m->entries.n;j++) {
            LWMapEntry v=m->entries.v[j];
            if(v.point>=o->point_blocks.v[m->point_block].count) o->invalid_map_references++;
            if(m->discontinuous) {
                if(m->polygon_block>=o->polygon_blocks.n||v.polygon>=o->polygon_blocks.v[m->polygon_block].count) o->invalid_map_references++;
                else {
                    LWPrimitive p=o->primitives.v[o->polygon_blocks.v[m->polygon_block].first+v.polygon]; uint32_t k;
                    for(k=0;k<p.count;k++) if(o->indices.v[p.first+k]==o->point_blocks.v[m->point_block].first+v.point) break;
                    if(k==p.count) o->invalid_map_references++;
                }
            }
        }
    }
}
int lw_parse_object(LWObject *o,LWError *e) {
    LWReader r={o->source.data,o->source.size,0,0,e},form; uint32_t tag,type; size_t off;
    LW_TRY(lw_chunk(&r,0,&tag,&form,&off));
    if(tag!=TAG("FORM")||r.pos!=r.size) return lw_error(e,0,"FORM","expected exactly one complete FORM");
    LW_TRY(lw_u32(&form,&type));
    if(type==TAG("PST_")) {
        int found=0;
        while(form.pos<form.size) {
            LWReader c; LWChunk chunk;
            LW_TRY(lw_chunk(&form,0,&tag,&c,&off));
            chunk=(LWChunk){tag,off,c.size,"preserved-opaque"};
            LW_TRY(LW_ADD(o->chunks,chunk,e));
            if(tag==TAG("PDAT")) {
                LWReader embedded; uint32_t ft; size_t fo;
                if(found++) return lw_error(e,off,"PST_","multiple PDAT payloads are not supported");
                LW_TRY(lw_chunk(&c,0,&ft,&embedded,&fo));
                if(ft!=TAG("FORM")||c.pos!=c.size) return lw_error(e,fo,"PDAT","expected a complete nested FORM");
                LW_TRY(lw_u32(&embedded,&o->format)); o->form_offset=fo;
                if(o->format!=TAG("LWOB")&&o->format!=TAG("LWO2")) return lw_error(e,fo,"PDAT","unsupported embedded object type");
                LW_TRY(parse_form(o,embedded));
            }
        }
        if(!found) return lw_error(e,0,"PST_","surface preset has no PDAT");
    } else {
        if(type!=TAG("LWOB")&&type!=TAG("LWO2")) return lw_error(e,8,"FORM","supported object types are LWOB, LWO2 and PST_");
        o->format=type; LW_TRY(parse_form(o,form));
    }
    bind_object(o); return 1;
}
int lw_load_object(const char *path,LWObject *o,LWError *e) {
    memset(o,0,sizeof *o);
    LW_TRY(lw_read_source(path,&o->source,e));
    if(!lw_parse_object(o,e)) { lw_free_object(o); return 0; }
    return 1;
}
void lw_free_object(LWObject *o) {
    size_t i;
    for(i=0;i<o->maps.n;i++) { LW_FREE(o->maps.v[i].entries); LW_FREE(o->maps.v[i].values); }
    LW_FREE(o->maps); LW_FREE(o->layers); LW_FREE(o->point_blocks); LW_FREE(o->polygon_blocks);
    LW_FREE(o->positions); LW_FREE(o->indices); LW_FREE(o->primitives); LW_FREE(o->tags);
    for(i=0;i<o->images.n;i++) lw_free_image(&o->images.v[i]);
    LW_FREE(o->materials); LW_FREE(o->images); LW_FREE(o->assignments); LW_FREE(o->chunks);
    lw_free_source(&o->source); memset(o,0,sizeof *o);
}
void lw_object_summary(FILE *f,const LWObject *o) {
    char format[5]; size_t i,details=0; lw_tag_text(o->format,format);
    for(i=0;i<o->primitives.n;i++) details+=o->primitives.v[i].detail_parent!=LW_NONE;
    fprintf(f,"{\"kind\":\"object\",\"format\":\"%s\",\"sha256\":\"%s\",\"points\":%zu,\"primitives\":%zu,\"layers\":%zu,\"materials\":%zu,\"maps\":%zu,\"detail_polygons\":%zu,\"repeated_primitives\":%zu,\"invalid_map_references\":%zu,\"missing_materials\":%zu,\"image_references\":%zu,\"opaque_chunks\":%zu}\n",
        format,o->source.sha256,o->positions.n/3,o->primitives.n,o->layers.n,o->materials.n,o->maps.n,details,o->repeated_primitives,o->invalid_map_references,o->missing_materials,o->images.n,o->opaque_chunks);
}
