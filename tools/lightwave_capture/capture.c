/* Read-only evaluation bridge, built against the user's LightWave SDK.
   API declarations are supplied by the NewTek SDK, not vendored here. */
#include <lwmodule.h>
#include <lwfilter.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>

static GlobalFunc *globals;
static LWInstance create(void *priv,void *context,LWError *error) {
    LWInstance instance;
    (void)priv; (void)context;
    instance=calloc(1,1); *error=instance?NULL:"LWConvertCapture: allocation failed";
    return instance;
}
static void destroy(LWInstance instance) { free(instance); }
static LWError copy(LWInstance instance,LWInstance from) { (void)instance; (void)from; return NULL; }
static LWError load(LWInstance instance,const LWLoadState *state) { (void)instance; (void)state; return NULL; }
static LWError save(LWInstance instance,const LWSaveState *state) { (void)instance; (void)state; return NULL; }
static const char *description(LWInstance instance) { (void)instance; return "LWConvert native animation capture 2"; }
static unsigned int flags(LWInstance instance) { (void)instance; return 0; }

typedef struct { FILE *file; LWMeshInfoID mesh; size_t points,polygons; } Capture;
static size_t capture_point(void *data,LWPntID id) {
    Capture *capture=data; LWFVector base,world;
    capture->mesh->pntBasePos(capture->mesh,id,base);
    capture->mesh->pntOtherPos(capture->mesh,id,world);
    fprintf(capture->file,"P %llx %.9g %.9g %.9g %.9g %.9g %.9g\n",(unsigned long long)(uintptr_t)id,
        base[0],base[1],base[2],world[0],world[1],world[2]);
    capture->points++; return 0;
}
static size_t capture_polygon(void *data,LWPolID id) {
    Capture *capture=data; int i,count=capture->mesh->polSize(capture->mesh,id);
    fprintf(capture->file,"Q %lu %d",(unsigned long)capture->mesh->polType(capture->mesh,id),count);
    for(i=0;i<count;i++) fprintf(capture->file," %llx",(unsigned long long)(uintptr_t)capture->mesh->polVertex(capture->mesh,id,i));
    fputc('\n',capture->file);
    for(i=0;i<count&&count>=3;i++) {
        LWPntID point=capture->mesh->polVertex(capture->mesh,id,i); LWFVector world;
        if(capture->mesh->pntOtherNormal(capture->mesh,id,point,world))
            fprintf(capture->file,"V %zu %d %llx %.9g %.9g %.9g\n",capture->polygons,i,
                (unsigned long long)(uintptr_t)point,world[0],world[1],world[2]);
    }
    capture->polygons++; return 0;
}
static void capture_item(FILE *file,LWItemInfo *info,LWItemID id,LWTime time) {
    const int parameters[]={LWIP_RIGHT,LWIP_UP,LWIP_FORWARD,LWIP_W_POSITION};
    LWDVector values[4],pivot; int i,j; const unsigned char *name=(const unsigned char *)info->name(id);
    for(i=0;i<4;i++) info->param(id,parameters[i],time,values[i]);
    info->param(id,LWIP_PIVOT,time,pivot);
    /* W_POSITION locates the pivot. Convert it to the transformed origin. */
    for(i=0;i<3;i++) for(j=0;j<3;j++) values[3][i]-=values[j][i]*pivot[j];
    fprintf(file,"I %llx %llx",(unsigned long long)(uintptr_t)id,(unsigned long long)(uintptr_t)info->parent(id));
    for(i=0;i<4;i++) for(j=0;j<3;j++) fprintf(file," %.17g",values[i][j]);
    fputc('\n',file);
    fprintf(file,"N %llx ",(unsigned long long)(uintptr_t)id);
    if(name) while(*name) { fprintf(file,"%02x",*name); name++; }
    fputc('\n',file);
}
static void process(LWInstance instance,const LWFilterAccess *access) {
    const char *directory=getenv("LWCONVERT_CAPTURE_DIR"); char path[4096]; FILE *file;
    LWItemInfo *info=globals(LWITEMINFO_GLOBAL,GFUSE_TRANSIENT);
    LWObjectInfo *objects=globals(LWOBJECTINFO_GLOBAL,GFUSE_TRANSIENT);
    LWItemID id,bone; size_t items=0,meshes=0,points=0,polygons=0;
    (void)instance;
    if(!directory||!info||!objects) return;
    if(snprintf(path,sizeof path,"%s/frame-%06d.txt",directory,access->frame)>=(int)sizeof path) return;
    file=fopen(path,"wb"); if(!file) return;
    fprintf(file,"LWCONVERT_CAPTURE 2 %d %.17g %.17g\n",access->frame,access->start,access->end);
    for(id=info->first(LWI_OBJECT,NULL);id;id=info->next(id)) {
        LWMeshInfoID mesh; int display=0,render=0;
        capture_item(file,info,id,access->start); items++;
        for(bone=info->first(LWI_BONE,id);bone;bone=info->next(bone)) { capture_item(file,info,bone,access->start); items++; }
        mesh=objects->meshInfo(id,1);
        if(mesh) {
            Capture capture={file,mesh,0,0};
            objects->patchLevel(id,&display,&render);
            fprintf(file,"M %llx %d %d %d %d\n",(unsigned long long)(uintptr_t)id,mesh->numPoints(mesh),mesh->numPolygons(mesh),display,render);
            mesh->scanPoints(mesh,capture_point,&capture); mesh->scanPolys(mesh,capture_polygon,&capture);
            points+=capture.points; polygons+=capture.polygons; meshes++;
            if(mesh->destroy) mesh->destroy(mesh);
        }
    }
    fprintf(file,"END %zu %zu %zu %zu\n",items,meshes,points,polygons);
    fclose(file);
}
static int activate(long version,GlobalFunc *global,void *local,void *data) {
    LWImageFilterHandler *handler=local; (void)data;
    if(version!=LWIMAGEFILTER_VERSION) return AFUNC_BADVERSION;
    globals=global;
    handler->inst->priv=NULL; handler->inst->create=create; handler->inst->destroy=destroy;
    handler->inst->copy=copy; handler->inst->load=load; handler->inst->save=save; handler->inst->descln=description;
    handler->process=process; handler->flags=flags; return AFUNC_OK;
}
static void *startup(void) { return (void *)1; }
static void shutdown(void *data) { (void)data; }
static ServerRecord servers[]={{LWIMAGEFILTER_HCLASS,"LWConvertCapture",activate,NULL},{NULL,NULL,NULL,NULL}};
__declspec(dllexport) ModuleDescriptor _mod_descrip={MOD_SYSSYNC,MOD_SYSVER,MOD_MACHINE,startup,shutdown,servers};
