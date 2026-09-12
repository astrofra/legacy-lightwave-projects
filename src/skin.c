#include "internal.h"
#include <math.h>
#include <float.h>

static int selected(const LWObject *o,uint32_t layer,uint32_t request) {
    return request==LW_NONE||o->layers.v[layer].id==request-1;
}
static int named_weight(const LWMap *map,LWString name) {
    return map->type==LW_TAG('W','G','H','T')&&lw_string_equal(map->name,name);
}
void lw_resolve_bone_maps(LWPackage *p) {
    size_t i,j,k;
    for(i=0;i<p->scene.nodes.n;i++) {
        LWBone *bone=&p->scene.nodes.v[i].bone; const char *status="no-weight-map; procedural-influence";
        if(bone->owner==LW_NONE) continue;
        if(bone->weight_map.size) {
            status="missing-weight-map";
            for(j=0;j<p->scene.nodes.n;j++) if(p->scene.nodes.v[j].id==bone->owner) {
                const LWNode *owner=&p->scene.nodes.v[j];
                if(owner->asset==SIZE_MAX) { status="unresolved-object"; break; }
                for(k=0;k<p->objects.v[owner->asset].maps.n;k++) {
                    const LWMap *map=&p->objects.v[owner->asset].maps.v[k];
                    if(named_weight(map,bone->weight_map)&&selected(&p->objects.v[owner->asset],p->objects.v[owner->asset].point_blocks.v[map->point_block].layer,owner->layer)) {
                        status=bone->weight_map_only?"weight-map-only":"weight-map-plus-procedural-influence"; break;
                    }
                }
                break;
            }
        }
        snprintf(bone->weight_map_status,sizeof bone->weight_map_status,"%s",status);
    }
}
static void multiply(double out[16],const double a[16],const double b[16]) {
    double t[16]; size_t r,c,k;
    for(c=0;c<4;c++) for(r=0;r<4;r++) { t[c*4+r]=0; for(k=0;k<4;k++) t[c*4+r]+=a[k*4+r]*b[c*4+k]; }
    memcpy(out,t,sizeof t);
}
static int rest_world(LWRig *rig,size_t i,unsigned char *state,LWError *e,unsigned depth) {
    LWRigJoint *joint=&rig->joints.v[i]; size_t r,c;
    if(state[i]==2) return 1;
    if(state[i]==1||depth>1024) return lw_error(e,0,"bone","cyclic or excessively deep rest hierarchy");
    state[i]=1;
    memcpy(joint->world,joint->local,sizeof joint->world);
    if(joint->parent!=SIZE_MAX) {
        LW_TRY(rest_world(rig,joint->parent,state,e,depth+1));
        multiply(joint->world,rig->joints.v[joint->parent].world,joint->world);
    }
    lw_identity(joint->inverse_bind);
    for(c=0;c<3;c++) for(r=0;r<3;r++) joint->inverse_bind[c*4+r]=joint->world[r*4+c];
    for(r=0;r<3;r++) for(c=0;c<3;c++) joint->inverse_bind[12+r]-=joint->inverse_bind[c*4+r]*joint->world[12+c];
    state[i]=2; return 1;
}
static void issue(LWRig *rig,const char *message) {
    if(!rig->issue[0]) snprintf(rig->issue,sizeof rig->issue,"%s",message);
}
static double smooth(double t) { return t*t*(3-2*t); }
/* Independently measured LW 9.6 profile, not a call into the native runtime.
   See documentation/procedural-skinning.md for observations and limitations. */
static void procedural_point(const LWPackage *p,const LWRig *rig,const float *position,double *values,double *taper) {
    const LWNode *owner=&p->scene.nodes.v[rig->owner]; size_t j,k; double top[5]={0},sum=0;
    for(j=0;j<rig->joints.n;j++) {
        const LWRigJoint *joint=&rig->joints.v[j]; const LWBone *bone=&p->scene.nodes.v[joint->source].bone;
        double d[3],along=0,distance=0,field=0,factor=bone->weight_map_only?1:bone->strength;
        taper[j]=1;
        if(!bone->active||!values[j]||factor==0) { values[j]=0; continue; }
        if(bone->scale_strength&&!bone->weight_map_only) factor*=bone->rest_length;
        if(bone->weight_map_only) field=1;
        else {
            /* Both rest matrices and source points use native coordinates.
               Reflection to glTF happens only when writing the target. */
            for(k=0;k<3;k++) { d[k]=position[k]-joint->world[12+k]; along+=d[k]*joint->world[8+k]; }
            if(along<0) along=0; else if(along>bone->rest_length) along=bone->rest_length;
            for(k=0;k<3;k++) { double v=d[k]-along*joint->world[8+k]; distance+=v*v; }
            distance=sqrt(distance);
            if(bone->limited_range&&distance>bone->range[1]) field=0;
            else if(bone->limited_range&&distance<bone->range[0]) field=smooth(1-distance/bone->range[0])*1e28;
            else {
                field=1/(pow(distance,(double)owner->bone_falloff)+1e-24);
                if(bone->limited_range&&bone->range[1]>bone->range[0]) taper[j]=smooth((bone->range[1]-distance)/(bone->range[1]-bone->range[0]));
            }
        }
        values[j]*=factor*field;
        /* Faster Bones subtracts the fifth strongest field from each field,
           then normalizes. Simply keeping the four largest weights differs. */
        if(owner->faster_bones) for(k=0;k<5;k++) if(values[j]>top[k]) {
            size_t m; for(m=4;m>k;m--) top[m]=top[m-1]; top[k]=values[j]; break;
        }
    }
    for(j=0;j<rig->joints.n;j++) { values[j]=fmax(0,values[j]-top[4]); if(values[j]<FLT_EPSILON) values[j]=0; sum+=values[j]; }
    /* Outer range attenuation follows normalization; its missing mass belongs
       to the unchanged object, not to the other bones. */
    for(j=0;j<rig->joints.n;j++) values[j]=sum>0?values[j]/sum*taper[j]:0;
}
static int weights(const LWPackage *p,LWRig *rig,LWError *e) {
    const LWNode *owner=&p->scene.nodes.v[rig->owner]; const LWObject *o=&p->objects.v[rig->asset];
    double *values=NULL,*taper=NULL; unsigned char *seen=NULL,*used=NULL; size_t i,j,k,n=rig->point_count,columns=rig->joints.n,max_count=0,active=0; int ok=0;
    for(i=0;i<columns;i++) {
        const LWBone *bone=&p->scene.nodes.v[rig->joints.v[i].source].bone;
        if(bone->active&&(!bone->weight_map_only||!bone->weight_map.size)) rig->procedural_bones++;
    }
    if(rig->procedural_bones&&owner->bone_falloff>9) issue(rig,"procedural falloff is absent or outside the measured 0..9 profile");
    for(i=0;i<columns;i++) {
        const LWBone *bone=&p->scene.nodes.v[rig->joints.v[i].source].bone;
        if(!bone->active) continue;
        active++;
        if(!strcmp(bone->weight_map_status,"missing-weight-map")) { rig->missing_maps++; if(!p->legacy_bone_maps) issue(rig,"one or more assigned weight maps are missing"); }
        if(!bone->normalize&&bone->weight_map_only) issue(rig,"non-normalized LightWave weight semantics require qualification");
        if(bone->joint_comp[0]||bone->joint_comp[1]||bone->muscle_flex[0]||bone->muscle_flex[1]) {
            rig->volume_corrections++;
            if(!rig->procedural_bones) issue(rig,"joint compensation or muscle flexing is not represented by linear blend skinning");
        }
        if(rig->procedural_bones) {
            if(!(bone->present&4)||!isfinite(bone->rest_length)||bone->rest_length<0||!isfinite(bone->strength)||bone->strength<0||bone->strength>1e12||bone->rest_length>1e12) issue(rig,"procedural bone length or strength is invalid or outside the supported numeric range");
            if(bone->limited_range&&(!isfinite(bone->range[0])||!isfinite(bone->range[1])||bone->range[0]<0||bone->range[1]<bone->range[0])) issue(rig,"procedural bone range must satisfy 0 <= inner <= outer");
            if(bone->weight_map_only&&!bone->weight_map.size&&!p->legacy_bone_maps) issue(rig,"map-only bone has no assigned weight map");
        }
    }
    if(!active) issue(rig,"no active bones");
    if(rig->issue[0]) return 1;
    if(columns>65534||n>SIZE_MAX/(columns?columns:1)/sizeof *values) return lw_error(e,0,"skin","skin allocation exceeds addressable limits");
    values=calloc(n?columns*n:1,sizeof *values); seen=calloc(n?n:1,1); used=calloc(n?n:1,1);
    taper=calloc(columns?columns:1,sizeof *taper);
    rig->points=calloc(n?n:1,sizeof *rig->points);
    if(!values||!seen||!used||!taper||!rig->points) { lw_error(e,0,"allocation","out of memory"); goto done; }
    for(i=0;i<o->point_blocks.n;i++) if(selected(o,o->point_blocks.v[i].layer,owner->layer))
        memset(used+o->point_blocks.v[i].first,1,o->point_blocks.v[i].count);
    for(i=0;i<columns;i++) {
        const LWBone *bone=&p->scene.nodes.v[rig->joints.v[i].source].bone;
        if(!bone->active) continue;
        if(!bone->weight_map.size||(p->legacy_bone_maps&&!strcmp(bone->weight_map_status,"missing-weight-map"))) { for(j=0;j<n;j++) values[j*columns+i]=1; continue; }
        memset(seen,0,n);
        for(j=0;j<o->maps.n;j++) {
            const LWMap *map=&o->maps.v[j]; const LWPointBlock *block=&o->point_blocks.v[map->point_block];
            if(!named_weight(map,bone->weight_map)||!selected(o,block->layer,owner->layer)) continue;
            if(map->dimension!=1||map->discontinuous) { issue(rig,"weight map must be a continuous scalar vertex map"); continue; }
            for(k=0;k<map->entries.n;k++) {
                uint32_t point=map->entries.v[k].point; float value=map->values.v[k];
                if(point>=block->count||!isfinite(value)||value<0) { issue(rig,"invalid or negative native skin weight"); continue; }
                point+=block->first;
                if(seen[point]) { issue(rig,"ambiguous duplicate weight-map entry"); continue; }
                seen[point]=1; values[point*columns+i]=value;
            }
        }
    }
    if(rig->issue[0]) { ok=1; goto done; }
    for(i=0;i<n;i++) if(used[i]) {
        double sum=0; LWRigPoint *point=&rig->points[i];
        if(rig->procedural_bones) procedural_point(p,rig,o->positions.v+3*i,values+i*columns,taper);
        for(j=0;j<columns;j++) sum+=values[i*columns+j];
        point->first=rig->influences.n;
        if(sum>0) {
            for(j=0;j<columns;j++) if(values[i*columns+j]>0) {
                LWRigInfluence influence={(uint16_t)(j+1),(float)(values[i*columns+j]/(rig->procedural_bones?1:sum))};
                if(influence.weight>0&&!LW_ADD(rig->influences,influence,e)) goto done;
            }
            if(rig->procedural_bones&&sum<1-1e-7) {
                LWRigInfluence influence={0,(float)(1-sum)};
                if(!LW_ADD(rig->influences,influence,e)) goto done;
            }
        } else {
            /* An identity object anchor explicitly keeps native unweighted
               vertices fixed. It is not attributed to an invented bone. */
            LWRigInfluence influence={0,1}; rig->unweighted_points++;
            if(!LW_ADD(rig->influences,influence,e)) goto done;
        }
        point->count=rig->influences.n-point->first;
        if(point->count>max_count) max_count=point->count;
    }
    rig->influence_sets=(max_count+3)/4; rig->weighted=rig->influence_sets>0; ok=1;
done:
    free(values); free(taper); free(seen); free(used); return ok;
}
int lw_build_rig(const LWPackage *p,size_t owner_index,LWRig *rig,LWError *e) {
    const LWNode *owner=&p->scene.nodes.v[owner_index]; size_t i,j; unsigned char *state=NULL; int ok=0;
    memset(rig,0,sizeof *rig); rig->owner=owner_index; rig->asset=owner->asset;
    if(owner->asset==SIZE_MAX) return lw_error(e,0,"bone","rig object is unresolved");
    rig->point_count=p->objects.v[owner->asset].positions.n/3;
    for(i=0;i<p->objects.v[owner->asset].layers.n;i++) {
        const LWLayer *layer=&p->objects.v[owner->asset].layers.v[i];
        if(selected(&p->objects.v[owner->asset],(uint32_t)i,owner->layer)&&(layer->pivot[0]||layer->pivot[1]||layer->pivot[2]||layer->parent!=LW_NONE)) return lw_error(e,0,"bone","layer pivot/parent semantics require qualification");
    }
    for(i=0;i<owner->rig_parameters.n;i++) if(lw_string_is(owner->rig_parameters.v[i].name,"UseBonesFrom")) return lw_error(e,0,"bone","shared skeleton semantics require qualification");
    for(i=0;i<p->scene.nodes.n;i++) if(p->scene.nodes.v[i].bone.owner==owner->id) {
        LWRigJoint joint={0}; joint.source=i; joint.parent=SIZE_MAX;
        LW_TRY(lw_bone_rest_matrix(&p->scene.nodes.v[i],joint.local,e));
        LW_TRY(LW_ADD(rig->joints,joint,e));
    }
    if(!rig->joints.n) return lw_error(e,0,"bone","no bones for this object");
    for(i=0;i<rig->joints.n;i++) {
        uint32_t parent=p->scene.nodes.v[rig->joints.v[i].source].parent;
        if(parent==owner->id||parent==LW_NONE) continue;
        for(j=0;j<rig->joints.n;j++) if(p->scene.nodes.v[rig->joints.v[j].source].id==parent) break;
        if(j==rig->joints.n) return lw_error(e,0,"bone","rest hierarchy contains a parent outside the owning skeleton");
        rig->joints.v[i].parent=j;
    }
    state=calloc(rig->joints.n,1); if(!state) return lw_error(e,0,"allocation","out of memory");
    for(i=0;i<rig->joints.n;i++) if(!rest_world(rig,i,state,e,0)) goto done;
    if(!weights(p,rig,e)) goto done;
    ok=1;
done:
    free(state); return ok;
}
void lw_free_rig(LWRig *rig) {
    LW_FREE(rig->joints); LW_FREE(rig->influences); free(rig->points); memset(rig,0,sizeof *rig);
}
int lw_write_derived_skin(const char *dir,const LWPackage *p,const LWRig *rig,LWError *e) {
    char suffix[64],*ir=lw_join(dir,"IR"),*scene=NULL,*path=NULL; FILE *f=NULL; size_t i,j; int ok=0;
    if(ir) scene=lw_join(ir,p->scene_name);
    snprintf(suffix,sizeof suffix,"skin-%08x.json",p->scene.nodes.v[rig->owner].id);
    if(scene) path=lw_join(scene,suffix);
    if(!path) { lw_error(e,0,"allocation","out of memory"); goto done; }
    f=lw_fopen(path,"wb"); if(!f) { lw_error(e,0,"output","cannot create derived skin"); goto done; }
    fprintf(f,"{\"schema_version\":\"0.1\",\"kind\":\"derived-skin\",\"profile\":\"%s\",\"missing_map_procedural_fallbacks\":%zu,\"approximation\":true,\"owner_item\":%u,\"source_scene_sha256\":\"%s\",\"source_object_sha256\":\"%s\",\"volume_corrections_omitted\":%zu,\"joint_items\":[%u",p->legacy_bone_maps?"lightwave6-procedural-weights-0.1":"lightwave96-procedural-weights-0.1",p->legacy_bone_maps?rig->missing_maps:0,p->scene.nodes.v[rig->owner].id,p->scene.source.sha256,p->objects.v[rig->asset].source.sha256,rig->volume_corrections,p->scene.nodes.v[rig->owner].id);
    for(i=0;i<rig->joints.n;i++) fprintf(f,",%u",p->scene.nodes.v[rig->joints.v[i].source].id);
    fputs("],\"joint_zero\":\"identity object anchor\",\"point_indexing\":\"global source object point; empty for unselected layers\",\"points\":[",f);
    for(i=0;i<rig->point_count;i++) {
        const LWRigPoint *point=&rig->points[i]; if(i) fputc(',',f); fputc('[',f);
        for(j=0;j<point->count;j++) { const LWRigInfluence *v=&rig->influences.v[point->first+j]; if(j) fputc(',',f); fprintf(f,"[%u,%.9g]",(unsigned)v->joint,v->weight); }
        fputc(']',f);
    }
    fputs("]}\n",f); ok=lw_close(f,path,e); f=NULL;
done:
    if(f) fclose(f);
    free(path); free(scene); free(ir); return ok;
}
void lw_free_gltf_stats(LWGltfStats *stats) {
    size_t i; if(!stats->rigs) return;
    for(i=0;i<stats->rigs->n;i++) free(stats->rigs->v[i].name);
    LW_FREE(*stats->rigs); free(stats->rigs); stats->rigs=NULL;
}
