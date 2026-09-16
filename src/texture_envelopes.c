#include "internal.h"

#define TAG(s) LW_TAG((s)[0],(s)[1],(s)[2],(s)[3])

/* Prove the narrow constant-at-native-value case, without replacing native
   values or discarding ENVL bytes. Equal key values alone are insufficient:
   interpolation, outside behavior, modifiers and duplicate IDs also matter. */
static int constant_keys(LWReader r,float expected,uint32_t component_type) {
    uint32_t pre=0,post=0,type=0; unsigned seen=0;
    size_t keys=0; float previous=0; int span=0;
    while(r.pos<r.size) {
        LWReader c; size_t offset; uint32_t tag;
        if(!lw_chunk(&r,1,&tag,&c,&offset)) return 0;
        if(tag==TAG("KEY ")) {
            float time,value;
            if(c.size!=8||!lw_float(&c,&time)||!lw_float(&c,&value)) return 0;
            if(value!=expected||(keys&&(!span||time<=previous))) return 0;
            previous=time; keys++; span=0;
        } else if(tag==TAG("SPAN")) {
            uint32_t shape;
            if(!keys||span||c.size!=4||!lw_u32(&c,&shape)) return 0;
            if(shape!=TAG("LINE")&&shape!=TAG("STEP")) return 0;
            span=1;
        } else if(tag==TAG("PRE ")||tag==TAG("POST")||tag==TAG("TYPE")) {
            unsigned flag=tag==TAG("PRE ")?1:tag==TAG("POST")?2:4;
            uint32_t *value=flag==1?&pre:flag==2?&post:&type;
            if(seen&flag||c.size!=2||!lw_u16(&c,value)) return 0;
            seen|=flag;
        } else if(tag==TAG("NAME")) {
            LWString name;
            if(seen&8||!lw_s0(&c,&name)||c.pos!=c.size) return 0;
            seen|=8;
        } else return 0; /* Includes channel plugins and unknown attributes. */
    }
    if(keys<2||!span||(seen&3)!=3||pre>5||post>5) return 0;
    if((!pre||!post)&&expected!=0) return 0; /* Reset changes a nonzero value. */
    /* Require typed X/Y/Z siblings when resolving a vector's base index. */
    if(component_type&&(!(seen&4)||(type&255)!=component_type)) return 0;
    return 1;
}

static int constant_reference(const LWObject *o,const LWReader *form,const LWTextureEnvelope *binding,float expected) {
    size_t i,matches=0;
    for(i=0;i<o->chunks.n;i++) {
        const LWChunk *chunk=&o->chunks.v[i]; LWError ignored={0}; LWReader r; uint32_t index;
        if(chunk->tag!=TAG("ENVL")) continue;
        if(chunk->offset<form->base+4||chunk->offset+8+chunk->size>form->base+form->size) continue;
        r=(LWReader){o->source.data+chunk->offset+8,chunk->size,0,chunk->offset+8,&ignored};
        if(!lw_vx(&r,&index)) return 0;
        if(index!=binding->index) continue;
        if(++matches!=1||!constant_keys(r,expected,binding->component_type)) return 0;
    }
    return matches==1;
}

void lw_qualify_constant_texture_envelopes(LWObject *o) {
    LWError ignored={0}; LWReader source={o->source.data,o->source.size,o->form_offset,0,&ignored},form;
    uint32_t tag; size_t i,j,offset;
    if(o->format!=TAG("LWO2")||!lw_chunk(&source,0,&tag,&form,&offset)||tag!=TAG("FORM")) return;
    for(i=0;i<o->textures.n;i++) {
        LWTexture *t=&o->textures.v[i];
        if(t->block_type!=TAG("IMAP")||t->clip_scope||!t->has_envelopes||
           t->unknown_envelope_bindings||!t->envelope_count) continue;
        for(j=0;j<t->envelope_count;j++) {
            float expected;
            memcpy(&expected,(const unsigned char *)t+t->envelopes[j].value_offset,sizeof expected);
            if(!constant_reference(o,&form,&t->envelopes[j],expected)) break;
        }
        t->constant_envelopes=j==t->envelope_count;
    }
}
