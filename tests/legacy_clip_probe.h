#include <lwtxtr.h>
static void legacy_clip_probe(void) {
    const char *path=getenv("LWCONVERT_CLIP_PROBE"); FILE *f;
    LWTextureFuncs *tf=globals(LWTEXTUREFUNCS_GLOBAL,GFUSE_TRANSIENT);
    LWObjectInfo *oi=globals(LWOBJECTINFO_GLOBAL,GFUSE_TRANSIENT);
    LWItemInfo *ii=globals(LWITEMINFO_GLOBAL,GFUSE_TRANSIENT); LWItemID id;
    if(!path||!tf||!oi||!ii) return;
    f=fopen(path,"w"); if(!f) return;
    for(id=ii->first(LWI_OBJECT,NULL);id;id=ii->next(id)) {
        LWTextureID t=oi->clipMap(id); LWTLayerID l=t?tf->firstLayer(t):NULL;
        int axis=-1,coord=-1,invert=-1,pix=-1,aa=-1,wrap[2]={-1,-1},proj=-1,blend=-1;
        double size[3]={0},center[3]={0},opacity=-1;
        if(!l) { fprintf(f,"%s NONE\n",ii->name(id)); continue; }
        tf->getParam(l,TXTAG_AXIS,&axis); tf->getParam(l,TXTAG_COORD,&coord);
        tf->getParam(l,TXTAG_INVERT,&invert); tf->getParam(l,TXTAG_PIXBLEND,&pix); tf->getParam(l,TXTAG_AA,&aa);
        tf->getParam(l,TXTAG_WREPEAT,wrap); tf->getParam(l,TXTAG_HREPEAT,wrap+1);
        tf->getParam(l,TXTAG_PROJ,&proj); tf->getParam(l,TXTAG_BLEND,&blend); tf->getParam(l,TXTAG_OPAC,&opacity);
        tf->getParam(l,TXTAG_SIZE,size); tf->getParam(l,TXTAG_POSI,center);
        fprintf(f,"%s axis=%d coord=%d invert=%d pix=%d aa=%d wrap=%d,%d proj=%d blend=%d opacity=%.17g size=%.17g,%.17g,%.17g center=%.17g,%.17g,%.17g\n",ii->name(id),axis,coord,invert,pix,aa,wrap[0],wrap[1],proj,blend,opacity,size[0],size[1],size[2],center[0],center[1],center[2]);
    }
    fclose(f);
}
