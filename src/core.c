#include "internal.h"
#include <math.h>
#include <limits.h>

int lw_error(LWError *e, size_t off, const char *context, const char *fmt, ...) {
    if (e) {
        va_list args;
        e->offset=off;
        snprintf(e->context,sizeof e->context,"%s",context);
        va_start(args,fmt); vsnprintf(e->message,sizeof e->message,fmt,args); va_end(args);
    }
    return 0;
}
int lw_grow(void **p, size_t *capacity, size_t count, size_t unit, LWError *e) {
    size_t n; void *q;
    if (count<=*capacity) return 1;
    if (!unit || count>SIZE_MAX/unit || count>UINT32_MAX)
        return lw_error(e,0,"allocation","array size exceeds implementation limits");
    n=*capacity ? *capacity : 16;
    while(n<count) { if(n>SIZE_MAX/2) { n=count; break; } n*=2; }
    if(n>SIZE_MAX/unit) n=count;
    q=realloc(*p,n*unit);
    if(!q) return lw_error(e,0,"allocation","out of memory");
    *p=q; *capacity=n; return 1;
}
int lw_take(LWReader *r, size_t n, const unsigned char **p) {
    if(n>r->size-r->pos) return lw_error(r->error,r->base+r->pos,"reader","truncated field: need %zu bytes, have %zu",n,r->size-r->pos);
    *p=r->data+r->pos; r->pos+=n; return 1;
}
int lw_u16(LWReader *r,uint32_t *v) { const unsigned char *p; LW_TRY(lw_take(r,2,&p)); *v=((uint32_t)p[0]<<8)|p[1]; return 1; }
int lw_u32(LWReader *r,uint32_t *v) { const unsigned char *p; LW_TRY(lw_take(r,4,&p)); *v=LW_TAG(p[0],p[1],p[2],p[3]); return 1; }
int lw_float(LWReader *r,float *v) {
    uint32_t bits; LW_TRY(lw_u32(r,&bits)); memcpy(v,&bits,4);
    if(!isfinite(*v)) return lw_error(r->error,r->base+r->pos-4,"float","non-finite numeric data is not supported; retain the source");
    return 1;
}
int lw_vx(LWReader *r,uint32_t *v) {
    if(r->pos==r->size) return lw_error(r->error,r->base+r->pos,"VX","missing index");
    if(r->data[r->pos]==255) { LW_TRY(lw_u32(r,v)); *v&=0xffffff; return 1; }
    return lw_u16(r,v);
}
int lw_s0(LWReader *r,LWString *s) {
    const unsigned char *end=memchr(r->data+r->pos,0,r->size-r->pos), *p;
    size_t n;
    if(!end) return lw_error(r->error,r->base+r->pos,"S0","unterminated string");
    n=(size_t)(end-(r->data+r->pos)); s->data=r->data+r->pos; s->size=n;
    return lw_take(r,n+1+((n+1)&1),&p);
}
int lw_chunk(LWReader *r,int short_size,uint32_t *tag,LWReader *child,size_t *offset) {
    uint32_t n; const unsigned char *p;
    *offset=r->base+r->pos;
    LW_TRY(lw_u32(r,tag)); LW_TRY(short_size ? lw_u16(r,&n) : lw_u32(r,&n));
    child->base=r->base+r->pos; child->size=n; child->pos=0; child->error=r->error;
    LW_TRY(lw_take(r,n,&p)); child->data=p;
    if(n&1) LW_TRY(lw_take(r,1,&p));
    return 1;
}
LWString lw_string(const char *s) { LWString r={(const unsigned char *)s,strlen(s)}; return r; }
int lw_string_equal(LWString a,LWString b) { return a.size==b.size && (!a.size || memcmp(a.data,b.data,a.size)==0); }
int lw_string_is(LWString a,const char *b) { return lw_string_equal(a,lw_string(b)); }
char *lw_dup(const char *s) { size_t n=strlen(s)+1; char *p=malloc(n); if(p) memcpy(p,s,n); return p; }
static int utf8_valid(LWString s) {
    size_t i=0;
    while(i<s.size) {
        unsigned c=s.data[i++],n,minimum;
        if(c<128) continue;
        if(c>=0xc2&&c<=0xdf) { n=1; minimum=0x80; c&=31; }
        else if(c>=0xe0&&c<=0xef) { n=2; minimum=0x800; c&=15; }
        else if(c>=0xf0&&c<=0xf4) { n=3; minimum=0x10000; c&=7; }
        else return 0;
        if(n>s.size-i) return 0;
        while(n--) { unsigned b=s.data[i++]; if((b&0xc0)!=0x80) return 0; c=(c<<6)|(b&63); }
        if(c<minimum||c>0x10ffff||(c>=0xd800&&c<=0xdfff)) return 0;
    }
    return 1;
}
char *lw_text(LWString s) {
    char *p; size_t i,n=0; int valid=utf8_valid(s);
    if(s.size>(SIZE_MAX-1)/2) return NULL;
    p=malloc(s.size*2+1); if(!p) return NULL;
    for(i=0;i<s.size;i++) {
        unsigned b=s.data[i];
        if(valid||b<128) p[n++]=(char)b;
        else { p[n++]=(char)(0xc0|(b>>6)); p[n++]=(char)(0x80|(b&63)); }
    }
    p[n]=0; return p;
}
void lw_json_string(FILE *f,const char *s) {
    const unsigned char *p=(const unsigned char *)s; fputc('"',f);
    for(;*p;p++) {
        if(*p=='"'||*p=='\\') { fputc('\\',f); fputc(*p,f); }
        else if(*p<32) fprintf(f,"\\u%04x",*p);
        else fputc(*p,f);
    }
    fputc('"',f);
}
void lw_json_bytes(FILE *f,LWString s) { size_t i; fputc('"',f); for(i=0;i<s.size;i++) fprintf(f,"%02x",s.data[i]); fputc('"',f); }
void lw_json_name(FILE *f,LWString s) {
    char *text=lw_text(s);
    fputs("{\"text\":",f); lw_json_string(f,text?text:"");
    fputs(",\"raw_hex\":",f); lw_json_bytes(f,s);
    fprintf(f,",\"decoding\":\"%s\"}",utf8_valid(s)?"utf8":"latin1-hypothesis"); free(text);
}
void lw_tag_text(uint32_t tag,char s[5]) { s[0]=(char)(tag>>24); s[1]=(char)(tag>>16); s[2]=(char)(tag>>8); s[3]=(char)tag; s[4]=0; }

/* SHA-256, FIPS 180-4. No platform crypto or external library required. */
static uint32_t rotate(uint32_t x,unsigned n) { return (x>>n)|(x<<(32-n)); }
static void sha_block(uint32_t h[8],const unsigned char p[64]) {
    static const uint32_t k[64]={
        0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
        0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
        0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
        0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
        0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
        0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
        0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
        0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
    uint32_t w[64],a=h[0],b=h[1],c=h[2],d=h[3],e=h[4],f=h[5],g=h[6],z=h[7]; unsigned i;
    for(i=0;i<16;i++) w[i]=LW_TAG(p[4*i],p[4*i+1],p[4*i+2],p[4*i+3]);
    for(i=16;i<64;i++) w[i]=w[i-16]+(rotate(w[i-15],7)^rotate(w[i-15],18)^(w[i-15]>>3))+w[i-7]+(rotate(w[i-2],17)^rotate(w[i-2],19)^(w[i-2]>>10));
    for(i=0;i<64;i++) {
        uint32_t t1=z+(rotate(e,6)^rotate(e,11)^rotate(e,25))+((e&f)^(~e&g))+k[i]+w[i];
        uint32_t t2=(rotate(a,2)^rotate(a,13)^rotate(a,22))+((a&b)^(a&c)^(b&c));
        z=g; g=f; f=e; e=d+t1; d=c; c=b; b=a; a=t1+t2;
    }
    h[0]+=a; h[1]+=b; h[2]+=c; h[3]+=d; h[4]+=e; h[5]+=f; h[6]+=g; h[7]+=z;
}
void lw_sha256(const unsigned char *data,size_t size,char result[65]) {
    uint32_t h[8]={0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
    unsigned char tail[128]={0}; size_t n=size,i; uint64_t bits=(uint64_t)size*8;
    while(n>=64) { sha_block(h,data); data+=64; n-=64; }
    memcpy(tail,data,n); tail[n]=0x80; n=n<56?64:128;
    for(i=0;i<8;i++) tail[n-1-i]=(unsigned char)(bits>>(i*8));
    sha_block(h,tail); if(n==128) sha_block(h,tail+64);
    for(i=0;i<8;i++) snprintf(result+8*i,9,"%08x",h[i]);
}
int lw_close(FILE *f,const char *path,LWError *e) {
    int failed=ferror(f); if(fclose(f)!=0) failed=1;
    return failed ? lw_error(e,0,"output","failed writing %s",path) : 1;
}
int lw_write_bytes(const char *path,const void *data,size_t size,LWError *e) {
    FILE *f=lw_fopen(path,"wb"); if(!f) return lw_error(e,0,"output","cannot create %s",path);
    if(size && fwrite(data,1,size,f)!=size) { fclose(f); return lw_error(e,0,"output","short write: %s",path); }
    return lw_close(f,path,e);
}
