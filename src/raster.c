#include "internal.h"
#include "byterun.h"
#include "stb_image.h"
#include "stb_image_write.h"
#include <limits.h>

static unsigned be16(const unsigned char *p) { return ((unsigned)p[0]<<8)|p[1]; }
static int dimensions(unsigned w,unsigned h) { return w&&h&&w<=16384&&h<=16384&&(size_t)w*h<=16777216; }
static int ilbm(const LWSource *s,LWImageReference *image,LWError *e) {
    LWReader r={s->data,s->size,0,0,e},form; LWString header={0},palette={0},body={0};
    uint32_t tag,kind,camg=0; size_t offset,row_size,position=0,x,y,plane; unsigned width,height,planes,mask,compression;
    unsigned char *row=NULL,*rgba=NULL; int ok=0;
    LW_TRY(lw_chunk(&r,0,&tag,&form,&offset));
    if(tag!=LW_TAG('F','O','R','M')||r.pos!=r.size) return lw_error(e,0,"ILBM","expected a single FORM");
    LW_TRY(lw_u32(&form,&kind));
    if(kind!=LW_TAG('I','L','B','M')) return lw_error(e,8,"ILBM","unsupported IFF image form");
    /* Some legacy writers exclude the last BODY pad from FORM's byte count.
       lw_chunk already validated/consumed this outer pad, so exposing that one
       byte to the child reader stays within the original input allocation. */
    if(form.size&1) form.size++;
    while(form.pos<form.size) {
        LWReader c;
        LW_TRY(lw_chunk(&form,0,&tag,&c,&offset));
        if(tag==LW_TAG('B','M','H','D')) {
            if(header.data||c.size<20) return lw_error(e,offset,"ILBM","duplicate or short BMHD");
            header.data=c.data; header.size=c.size;
        } else if(tag==LW_TAG('C','M','A','P')) { palette.data=c.data; palette.size=c.size; }
        else if(tag==LW_TAG('C','A','M','G')) LW_TRY(lw_u32(&c,&camg));
        else if(tag==LW_TAG('B','O','D','Y')) {
            if(body.data) return lw_error(e,offset,"ILBM","duplicate BODY");
            body.data=c.data; body.size=c.size;
        }
    }
    if(!header.data||!body.data) return lw_error(e,0,"ILBM","missing BMHD or BODY");
    width=be16(header.data); height=be16(header.data+2); planes=header.data[8]; mask=header.data[9]; compression=header.data[10];
    if(!dimensions(width,height)) return lw_error(e,0,"ILBM","invalid or excessive image dimensions");
    if(!((planes>=1&&planes<=8)||planes==24||planes==32)||mask>2||compression>1)
        return lw_error(e,0,"ILBM","unsupported plane count, masking or compression");
    if(camg&0x800) return lw_error(e,0,"ILBM","HAM images require a dedicated color evaluator");
    if((camg&0x80)&&planes!=6) return lw_error(e,0,"ILBM","invalid EHB plane count");
    if(palette.size%3) return lw_error(e,0,"ILBM","incomplete palette entry");
    row_size=((size_t)width+15)/16*2;
    row=malloc(row_size*(planes+(mask==1))); rgba=malloc((size_t)width*height*4);
    if(!row||!rgba) { lw_error(e,0,"allocation","out of memory"); goto done; }
    for(y=0;y<height;y++) {
        for(plane=0;plane<planes+(mask==1);plane++) {
            unsigned char *destination=row+plane*row_size;
            if(compression) {
                if(!ilbm_unpack_row(body.data,body.size,&position,destination,row_size)) { lw_error(e,position,"ILBM","invalid or truncated ByteRun1 row"); goto done; }
            } else {
                if(row_size>body.size-position) { lw_error(e,position,"ILBM","truncated BODY"); goto done; }
                memcpy(destination,body.data+position,row_size); position+=row_size;
            }
        }
        for(x=0;x<width;x++) {
            uint32_t value=0; unsigned char *pixel=rgba+4*(y*width+x); unsigned bit=7-(unsigned)(x%8);
            for(plane=0;plane<planes;plane++) value|=(uint32_t)((row[plane*row_size+x/8]>>bit)&1)<<plane;
            pixel[3]=255;
            if(planes>=24) { pixel[0]=(unsigned char)value; pixel[1]=(unsigned char)(value>>8); pixel[2]=(unsigned char)(value>>16); if(planes==32) pixel[3]=(unsigned char)(value>>24); }
            else if(palette.data) {
                uint32_t index=(camg&0x80)?value&31:value; unsigned k;
                if(3*(size_t)index+3>palette.size) { lw_error(e,0,"ILBM","palette index out of range"); goto done; }
                for(k=0;k<3;k++) pixel[k]=(unsigned char)(palette.data[3*index+k]/((camg&0x80)&&(value&32)?2:1));
            } else { pixel[0]=pixel[1]=pixel[2]=(unsigned char)(value*255/((1u<<planes)-1)); }
            if(mask==1&&!((row[planes*row_size+x/8]>>bit)&1)) pixel[3]=0;
            if(mask==2&&value==be16(header.data+12)) pixel[3]=0;
        }
    }
    /* Some encoders finish with ByteRun1 no-ops. Any other surplus is suspect. */
    while(compression&&position<body.size&&body.data[position]==128) position++;
    if(position!=body.size) { lw_error(e,position,"ILBM","unexpected data after final row"); goto done; }
    image->rgba=rgba; image->width=(int)width; image->height=(int)height; rgba=NULL; ok=1;
done:
    free(row); free(rgba); return ok;
}
int lw_decode_raster(const LWSource *source,LWImageReference *image,LWError *e) {
    int width,height,channels;
    if(source->size>=4&&!memcmp(source->data,"FORM",4)) return ilbm(source,image,e);
    if(source->size>INT_MAX||!stbi_info_from_memory(source->data,(int)source->size,&width,&height,&channels)||!dimensions((unsigned)width,(unsigned)height))
        return lw_error(e,0,"image","unsupported image or excessive dimensions");
    image->rgba=stbi_load_from_memory(source->data,(int)source->size,&width,&height,&channels,4);
    if(!image->rgba) return lw_error(e,0,"image","cannot decode raster pixels");
    image->width=width; image->height=height; return 1;
}
typedef struct { LW_ARRAY(unsigned char) bytes; int failed; LWError *error; } PNGBuffer;
static void png_write(void *context,void *data,int size) {
    PNGBuffer *b=context;
    if(size<0||b->failed) { b->failed=1; return; }
    if(!lw_grow((void **)&b->bytes.v,&b->bytes.cap,b->bytes.n+(size_t)size,1,b->error)) { b->failed=1; return; }
    memcpy(b->bytes.v+b->bytes.n,data,(size_t)size); b->bytes.n+=(size_t)size;
}
int lw_encode_png(const unsigned char *rgba,int width,int height,LWSource *png,LWError *e) {
    PNGBuffer buffer={0}; int ok; buffer.error=e;
    ok=stbi_write_png_to_func(png_write,&buffer,width,height,4,rgba,width*4);
    if(!ok||buffer.failed) { LW_FREE(buffer.bytes); return lw_error(e,0,"PNG","cannot encode image"); }
    lw_sha256(buffer.bytes.v,buffer.bytes.n,png->sha256);
    png->data=buffer.bytes.v; png->size=buffer.bytes.n; return 1;
}
int lw_save_png(const char *path,const unsigned char *rgba,int width,int height,char sha256[65],LWError *e) {
    LWSource png={0}; int ok;
    if(!lw_encode_png(rgba,width,height,&png,e)) return 0;
    memcpy(sha256,png.sha256,65); ok=lw_write_bytes(path,png.data,png.size,e); lw_free_source(&png); return ok;
}
