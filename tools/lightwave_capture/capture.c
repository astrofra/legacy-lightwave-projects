/* Read-only evaluation bridge, built against the user's LightWave SDK.
   API declarations are supplied by the NewTek SDK, not vendored here. */
#include <lwmodule.h>
#include <lwfilter.h>
#include <lwdisplce.h>
#include <lwmotion.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>

static GlobalFunc *globals;
static int legacy_capture;
typedef struct EvaluatedMotion {
    struct EvaluatedMotion *next; LWItemID item; LWFrame frame; LWTime time;
    LWDVector position,rotation,scale; int valid;
} EvaluatedMotion;
static EvaluatedMotion *evaluated_motions;
typedef struct { LWPntID id; LWDVector world; } EvaluatedPoint;
typedef struct EvaluatedObject {
    struct EvaluatedObject *next; LWItemID owner; LWFrame frame;
    EvaluatedPoint *points; size_t count,capacity; int failed;
} EvaluatedObject;
static EvaluatedObject *evaluated_objects;
static LWInstance deformation_create(void *priv,void *context,LWError *error) {
    EvaluatedObject *object=calloc(1,sizeof *object); (void)priv;
    *error=object?NULL:"LWConvertCapture: allocation failed";
    if(object) { object->owner=context; object->next=evaluated_objects; evaluated_objects=object; }
    return object;
}
static void deformation_destroy(LWInstance instance) {
    EvaluatedObject *object=instance,**link=&evaluated_objects;
    while(*link&&*link!=object) link=&(*link)->next;
    if(*link) *link=object->next;
    free(object->points); free(object);
}
static LWError deformation_init(LWInstance instance,int mode) { (void)instance; (void)mode; return NULL; }
static void deformation_cleanup(LWInstance instance) { (void)instance; }
static LWError deformation_time(LWInstance instance,LWFrame frame,LWTime time) {
    EvaluatedObject *object=instance; (void)time; object->frame=frame; object->count=0; object->failed=0; return NULL;
}
static unsigned int deformation_flags(LWInstance instance) { (void)instance; return LWDMF_WORLD; }
static void deformation_evaluate(LWInstance instance,LWDisplacementAccess *access) {
    EvaluatedObject *object=instance; size_t k;
    if(object->failed) return;
    if(object->count==object->capacity) {
        size_t capacity=object->capacity?object->capacity*2:1024;
        EvaluatedPoint *points;
        if(capacity<object->capacity||capacity>SIZE_MAX/sizeof *points) { object->failed=1; return; }
        points=realloc(object->points,capacity*sizeof *points);
        if(!points) { object->failed=1; return; }
        object->points=points; object->capacity=capacity;
    }
    object->points[object->count].id=access->point;
    for(k=0;k<3;k++) object->points[object->count].world[k]=access->source[k];
    object->count++;
}
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

typedef struct { FILE *file; LWMeshInfoID mesh; size_t points,polygons; EvaluatedObject *evaluated; } Capture;
static size_t capture_point(void *data,LWPntID id) {
    Capture *capture=data; LWFVector base,world;
    capture->mesh->pntBasePos(capture->mesh,id,base);
    capture->mesh->pntOtherPos(capture->mesh,id,world);
    if(legacy_capture) {
        size_t i,k;
        if(!capture->evaluated||capture->evaluated->failed) return 0;
        for(i=0;i<capture->evaluated->count;i++) if(capture->evaluated->points[i].id==id) break;
        if(i==capture->evaluated->count) return 0;
        for(k=0;k<3;k++) world[k]=(float)capture->evaluated->points[i].world[k];
    }
    fprintf(capture->file,"P %llx %.9g %.9g %.9g %.9g %.9g %.9g\n",(unsigned long long)(uintptr_t)id,
        base[0],base[1],base[2],world[0],world[1],world[2]);
    capture->points++; return 0;
}
static size_t capture_polygon(void *data,LWPolID id) {
    Capture *capture=data; int i,count=capture->mesh->polSize(capture->mesh,id);
    fprintf(capture->file,"Q %lu %d",(unsigned long)capture->mesh->polType(capture->mesh,id),count);
    for(i=0;i<count;i++) fprintf(capture->file," %llx",(unsigned long long)(uintptr_t)capture->mesh->polVertex(capture->mesh,id,i));
    fputc('\n',capture->file);
    for(i=0;!legacy_capture&&i<count&&count>=3;i++) {
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
    legacy_capture=objects==NULL;
    if(!info) info=globals("LW Item Info 3",GFUSE_TRANSIENT);
    if(!objects) objects=globals("LW Object Info 3",GFUSE_TRANSIENT);
    if(!objects) objects=globals("LW Object Info 2",GFUSE_TRANSIENT);
    if(!directory||!info||!objects) return;
    if(snprintf(path,sizeof path,"%s/frame-%06d.txt",directory,access->frame)>=(int)sizeof path) return;
    file=fopen(path,"wb"); if(!file) return;
    fprintf(file,"LWCONVERT_CAPTURE %d %d %.17g %.17g\n",legacy_capture?3:2,access->frame,access->start,access->end);
    for(id=info->first(LWI_OBJECT,NULL);id;id=info->next(id)) {
        LWMeshInfoID mesh; int display=0,render=0;
        capture_item(file,info,id,access->start); items++;
        for(bone=info->first(LWI_BONE,id);bone;bone=info->next(bone)) {
            capture_item(file,info,bone,access->start); items++;
        }
        /* LW6's frozen mesh duplicates the cage and triangulates polygons.
           Keep base topology; final WORLD observer positions are joined by ID. */
        mesh=objects->meshInfo(id,legacy_capture?0:1);
        if(mesh) {
            Capture capture={file,mesh,0,0,NULL}; EvaluatedObject *object;
            for(object=evaluated_objects;object;object=object->next) if(object->owner==id&&object->frame==access->frame) { capture.evaluated=object; break; }
            if(!legacy_capture) objects->patchLevel(id,&display,&render);
            fprintf(file,"M %llx %d %d %d %d\n",(unsigned long long)(uintptr_t)id,mesh->numPoints(mesh),mesh->numPolygons(mesh),display,render);
            mesh->scanPoints(mesh,capture_point,&capture); mesh->scanPolys(mesh,capture_polygon,&capture);
            points+=capture.points; polygons+=capture.polygons; meshes++;
            if(mesh->destroy) mesh->destroy(mesh);
        }
    }
    if(legacy_capture) {
        EvaluatedMotion *motion;
        for(motion=evaluated_motions;motion;motion=motion->next) if(motion->valid&&motion->frame==access->frame&&motion->time==access->start) {
            LWDVector pivot; int j;
            info->param(motion->item,LWIP_PIVOT,access->start,pivot);
            fprintf(file,"T %llx",(unsigned long long)(uintptr_t)motion->item);
            for(j=0;j<3;j++) fprintf(file," %.17g",motion->position[j]);
            for(j=0;j<3;j++) fprintf(file," %.17g",motion->rotation[j]);
            for(j=0;j<3;j++) fprintf(file," %.17g",motion->scale[j]);
            for(j=0;j<3;j++) fprintf(file," %.17g",pivot[j]);
            fputc('\n',file);
        }
    }
    fprintf(file,"END %zu %zu %zu %zu\n",items,meshes,points,polygons);
    fclose(file);
    /* Optional research sidecar: keep the established frame protocol intact. */
    if(getenv("LWCONVERT_CAPTURE_BONES")) {
        LWBoneInfo *bones=globals(LWBONEINFO_GLOBAL,GFUSE_TRANSIENT);
        if(!bones||snprintf(path,sizeof path,"%s/bones-%06d.txt",directory,access->frame)>=(int)sizeof path) return;
        file=fopen(path,"wb"); if(!file) return;
        fputs("LWCONVERT_BONE_SETTINGS 1\n",file);
        for(id=info->first(LWI_OBJECT,NULL);id;id=info->next(id))
            for(bone=info->first(LWI_BONE,id);bone;bone=info->next(bone)) {
                LWDVector position,rotation; double inner,outer,joint,parent_joint,flex,parent_flex;
                const unsigned char *map=(const unsigned char *)bones->weightMap(bone);
                bones->restParam(bone,LWIP_POSITION,position); bones->restParam(bone,LWIP_ROTATION,rotation);
                bones->limits(bone,&inner,&outer); bones->jointComp(bone,&joint,&parent_joint); bones->muscleFlex(bone,&flex,&parent_flex);
                fprintf(file,"B %llx %llx %u %d %.17g %.17g %.17g %.17g %.17g %.17g %.17g %.17g %.17g %.17g %.17g %.17g %.17g %.17g ",
                    (unsigned long long)(uintptr_t)id,(unsigned long long)(uintptr_t)bone,bones->flags(bone),bones->falloff(bone),
                    position[0],position[1],position[2],rotation[0],rotation[1],rotation[2],bones->restLength(bone),bones->strength(bone),
                    inner,outer,joint,parent_joint,flex,parent_flex);
                if(map) while(*map) { fprintf(file,"%02x",*map); map++; }
                fputc('\n',file);
            }
        fclose(file);
    }
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
static int activate_deformation(long version,GlobalFunc *global,void *local,void *data) {
    LWDisplacementHandler *handler=local; (void)data;
    if(version!=4&&version!=LWDISPLACEMENT_VERSION) return AFUNC_BADVERSION;
    globals=global;
    handler->inst->priv=NULL; handler->inst->create=deformation_create; handler->inst->destroy=deformation_destroy;
    handler->inst->copy=copy; handler->inst->load=load; handler->inst->save=save; handler->inst->descln=description;
    handler->rend->init=deformation_init; handler->rend->cleanup=deformation_cleanup; handler->rend->newTime=deformation_time;
    handler->evaluate=deformation_evaluate; handler->flags=deformation_flags; return AFUNC_OK;
}
static unsigned int motion_flags(LWInstance instance) { (void)instance; return LWIMF_AFTERIK; }
static LWInstance motion_create(void *priv,void *context,LWError *error) {
    EvaluatedMotion *motion=calloc(1,sizeof *motion); (void)priv; (void)context;
    *error=motion?NULL:"LWConvertMotionCapture: allocation failed";
    if(motion) { motion->next=evaluated_motions; evaluated_motions=motion; }
    return motion;
}
static void motion_destroy(LWInstance instance) {
    EvaluatedMotion *motion=instance,**link=&evaluated_motions;
    while(*link&&*link!=motion) link=&(*link)->next;
    if(*link) *link=motion->next;
    free(motion);
}
static void motion_evaluate(LWInstance instance,const LWItemMotionAccess *access) {
    EvaluatedMotion *motion=instance;
    motion->item=access->item; motion->frame=access->frame; motion->time=access->time;
    access->getParam(LWIP_POSITION,access->time,motion->position);
    access->getParam(LWIP_ROTATION,access->time,motion->rotation);
    access->getParam(LWIP_SCALING,access->time,motion->scale);
    motion->valid=1;
}
static int activate_motion(long version,GlobalFunc *global,void *local,void *data) {
    LWItemMotionHandler *handler=local; (void)data;
    if(version!=LWITEMMOTION_VERSION) return AFUNC_BADVERSION;
    globals=global; handler->inst->priv=NULL; handler->inst->create=motion_create; handler->inst->destroy=motion_destroy;
    handler->inst->copy=copy; handler->inst->load=load; handler->inst->save=save; handler->inst->descln=description;
    handler->evaluate=motion_evaluate; handler->flags=motion_flags; return AFUNC_OK;
}
static ServerRecord servers[]={{LWIMAGEFILTER_HCLASS,"LWConvertCapture",activate,NULL},{LWDISPLACEMENT_HCLASS,"LWConvertDeformationCapture",activate_deformation,NULL},{LWITEMMOTION_HCLASS,"LWConvertMotionCapture",activate_motion,NULL},{NULL,NULL,NULL,NULL}};
__declspec(dllexport) ModuleDescriptor _mod_descrip={MOD_SYSSYNC,MOD_SYSVER,MOD_MACHINE,startup,shutdown,servers};
