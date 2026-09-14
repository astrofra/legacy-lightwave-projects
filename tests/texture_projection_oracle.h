/* Development-only hook compiled into an isolated copy of the capture helper.
   The installed converter and capture plugin do not include this code. */
#include <lwtxtr.h>

static void texture_projection_probe(void) {
    const char *input=getenv("LWCONVERT_TEXTURE_INPUT"),*output=getenv("LWCONVERT_TEXTURE_OUTPUT");
    LWTextureFuncs *tf; FILE *in,*out; int projection,axis,index=0;
    double center[3],rotation[3],size[3],tiles[2],point[3],uv[2];
    if(!input||!output) return;
    tf=globals(LWTEXTUREFUNCS_GLOBAL,GFUSE_TRANSIENT);
    if(!tf) tf=globals("Texture Functions 2",GFUSE_TRANSIENT);
    if(!tf) tf=globals("Texture Functions",GFUSE_TRANSIENT);
    if(!tf) tf=globals("Texture Functions 1",GFUSE_TRANSIENT);
    if(!tf) return;
    in=fopen(input,"r"); if(!in) return;
    out=fopen(output,"w"); if(!out) { fclose(in); return; }
    while(fscanf(in,"%d %d %lf %lf %lf %lf %lf %lf %lf %lf %lf %lf %lf %lf %lf %lf",
        &projection,&axis,center,center+1,center+2,rotation,rotation+1,rotation+2,
        size,size+1,size+2,tiles,tiles+1,point,point+1,point+2)==16) {
        LWTextureID texture=tf->create(TRT_COLOR,"projection oracle",NULL,NULL);
        LWTLayerID layer; int ok=1;
        if(!texture) { fprintf(out,"error texture %d\n",index); break; }
        layer=tf->firstLayer(texture);
        if(!layer) layer=tf->layerAdd(texture,TLT_IMAGE);
        else tf->layerSetType(layer,TLT_IMAGE);
        ok&=tf->setParam(layer,TXTAG_PROJ,&projection); ok&=tf->setParam(layer,TXTAG_AXIS,&axis);
        ok&=tf->setParam(layer,TXTAG_POSI,center); ok&=tf->setParam(layer,TXTAG_ROTA,rotation);
        ok&=tf->setParam(layer,TXTAG_SIZE,size); ok&=tf->setParam(layer,TXTAG_WWRP,tiles);
        ok&=tf->setParam(layer,TXTAG_HWRP,tiles+1);
        if(!layer) { fprintf(out,"error layer %d\n",index); break; }
        tf->newtime(texture,0,0); tf->evaluateUV(layer,axis,axis,point,point,uv);
        fprintf(out,"%d %d %.17g %.17g\n",index++,ok,uv[0],uv[1]);
        tf->cleanup(texture); tf->destroy(texture);
    }
    fclose(out); fclose(in);
}
