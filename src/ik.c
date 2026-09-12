#include "internal.h"
#include <math.h>
#include <errno.h>
#include <ctype.h>

/* Independent, bounded H/P/B IK approximation. No native runtime or captures
   are consulted. Goals constrain item pivots; an IKAnchor stops the chain.
   Chains sharing an anchor are solved together, from ancestors to descendants. */
#define IK_PI 3.14159265358979323846
#define IK_VARS 96
#define IK_GOALS 32
typedef struct {
    size_t parent,goal,anchor,depth;
    int control[3],limited[3],stop,full,match;
    double low[3],high[3],stiff[3],strength,v[9],local[16],world[16];
} IKNode;
typedef struct { size_t node; unsigned axis; } IKVar;
typedef struct {
    const LWScene *scene; IKNode *nodes; size_t *order,count;
} IKScene;

static void mul(double out[16],const double a[16],const double b[16]) {
    double t[16]={0}; size_t r,c,k;
    for(c=0;c<4;c++) for(r=0;r<4;r++) for(k=0;k<4;k++) t[c*4+r]+=a[k*4+r]*b[c*4+k];
    memcpy(out,t,sizeof t);
}
static void matrix(double m[16],const double v[9],const double pivot[3]) {
    double y[16],x[16],z[16]; size_t r,c;
    lw_identity(y); lw_identity(x); lw_identity(z);
    y[0]=y[10]=cos(v[3]); y[8]=sin(v[3]); y[2]=-y[8];
    x[5]=x[10]=cos(v[4]); x[6]=sin(v[4]); x[9]=-x[6];
    z[0]=z[5]=cos(v[5]); z[1]=sin(v[5]); z[4]=-z[1];
    mul(m,y,x); mul(m,m,z);
    for(c=0;c<3;c++) for(r=0;r<3;r++) m[c*4+r]*=v[6+c];
    for(r=0;r<3;r++) { m[12+r]=v[r]; for(c=0;c<3;c++) m[12+r]-=m[c*4+r]*pivot[c]; }
}
int lw_matrix_trs(const double m[16],double out[10],LWError *e) {
    double r[3][3],q[4],trace,det,norm=0; size_t i,j,k;
    det=m[0]*(m[5]*m[10]-m[9]*m[6])-m[4]*(m[1]*m[10]-m[9]*m[2])+m[8]*(m[1]*m[6]-m[5]*m[2]);
    for(i=0;i<3;i++) {
        out[i]=m[12+i]; out[7+i]=sqrt(m[4*i]*m[4*i]+m[4*i+1]*m[4*i+1]+m[4*i+2]*m[4*i+2]);
        if(!isfinite(out[7+i])||out[7+i]<1e-12) return lw_error(e,0,"IK","singular or non-finite transform");
        if(i==0&&det<0) out[7+i]=-out[7+i];
        for(j=0;j<3;j++) r[j][i]=m[4*i+j]/out[7+i];
    }
    for(i=0;i<3;i++) for(j=0;j<3;j++) {
        double dot=0; for(k=0;k<3;k++) dot+=r[k][i]*r[k][j];
        if(fabs(dot-(i==j?1:0))>1e-6) return lw_error(e,0,"IK","sheared world transform is outside the autonomous profile");
    }
    trace=r[0][0]+r[1][1]+r[2][2];
    if(trace>0) {
        double a=sqrt(trace+1)*2;
        q[0]=(r[2][1]-r[1][2])/a; q[1]=(r[0][2]-r[2][0])/a; q[2]=(r[1][0]-r[0][1])/a; q[3]=a/4;
    } else {
        double a;
        i=r[0][0]>r[1][1]?0:1; if(r[2][2]>r[i][i]) i=2;
        j=(i+1)%3; k=(i+2)%3; a=sqrt(fmax(0,1+r[i][i]-r[j][j]-r[k][k]))*2;
        if(a<1e-12) return lw_error(e,0,"IK","invalid rotation matrix");
        q[i]=a/4; q[j]=(r[i][j]+r[j][i])/a; q[k]=(r[i][k]+r[k][i])/a; q[3]=(r[k][j]-r[j][k])/a;
    }
    for(i=0;i<4;i++) norm+=q[i]*q[i]; norm=sqrt(norm);
    for(i=0;i<4;i++) out[3+i]=q[i]/norm;
    for(i=0;i<10;i++) if(!isfinite(out[i])) return lw_error(e,0,"IK","non-finite TRS");
    return 1;
}
static int field_values(const LWTextureField *field,double *v,size_t count,int stiffness,int *recovered,LWError *e) {
    char s[256],*p,*end; size_t i;
    if(field->value.size>=sizeof s) return lw_error(e,field->offset,"IK","constraint line too long");
    memcpy(s,field->value.data,field->value.size); s[field->value.size]=0; p=s;
    for(i=0;i<count;i++) {
        errno=0; v[i]=strtod(p,&end);
        if(end==p||errno||!isfinite(v[i])) return lw_error(e,field->offset,"IK","invalid constraint number");
        p=end;
    }
    while(isspace((unsigned char)*p)) p++;
    /* Historic LWSC writers concatenate stiffness and the next controller.
       Recover only this exact grammar; the original bytes stay in scene IR. */
    if(stiffness&&strchr("HPB",*p)&&*p&&strncmp(p+1,"Controller",10)==0) {
        char axis=*p; double mode=strtod(p+11,&end);
        if(end==p+11||mode!=3) return lw_error(e,field->offset,"IK","invalid concatenated controller");
        while(isspace((unsigned char)*end)) end++;
        if(*end) return lw_error(e,field->offset,"IK","unexpected concatenated constraint suffix");
        *recovered=axis=='H'?1:axis=='P'?2:3; return 1;
    }
    if(*p) return lw_error(e,field->offset,"IK","unexpected constraint suffix");
    return 1;
}
static size_t find(const LWScene *s,uint32_t id) {
    size_t i; for(i=0;i<s->nodes.n;i++) if(s->nodes.v[i].id==id) return i; return SIZE_MAX;
}
static int sort_node(IKScene *s,size_t i,unsigned char *state,LWError *e,unsigned depth) {
    IKNode *n=&s->nodes[i];
    if(state[i]==2) return 1;
    if(state[i]==1||depth>256) return lw_error(e,0,"IK","cyclic or excessively deep hierarchy");
    state[i]=1;
    if(n->parent!=SIZE_MAX) { LW_TRY(sort_node(s,n->parent,state,e,depth+1)); n->depth=s->nodes[n->parent].depth+1; }
    s->order[s->count++]=i; state[i]=2; return 1;
}
static int prepare(IKScene *s,LWIKBake *b,LWError *e) {
    const LWScene *scene=s->scene; size_t i,j,k; unsigned char *state;
    s->nodes=calloc(scene->nodes.n,sizeof *s->nodes); s->order=malloc(scene->nodes.n*sizeof *s->order);
    if(!s->nodes||!s->order) return lw_error(e,0,"allocation","cannot allocate IK hierarchy");
    for(i=0;i<scene->nodes.n;i++) {
        const LWNode *source=&scene->nodes.v[i]; IKNode *n=&s->nodes[i];
        n->parent=source->parent==LW_NONE?SIZE_MAX:find(scene,source->parent); n->goal=n->anchor=SIZE_MAX; n->strength=1;
        if(source->parent!=LW_NONE&&n->parent==SIZE_MAX) return lw_error(e,source->source_offset,"IK","missing parent");
        if(source->pivot_rotation[0]||source->pivot_rotation[1]||source->pivot_rotation[2]) return lw_error(e,source->source_offset,"IK","pivot rotation is outside the autonomous profile");
        if(source->mirrored_bank_follower) return lw_error(e,source->source_offset,"IK","Follower in a skeletal bake is not qualified");
        for(j=0;j<source->rig_parameters.n;j++) {
            const LWTextureField *f=&source->rig_parameters.v[j]; double v[2]; int recovered=0;
            for(k=0;k<3;k++) {
                const char *controllers[]={"HController","PController","BController"};
                const char *limits[]={"HLimits","PLimits","BLimits"};
                const char *stiff[]={"HJointStiffness","PJointStiffness","BJointStiffness"};
                if(lw_string_is(f->name,controllers[k])) {
                    LW_TRY(field_values(f,v,1,0,&recovered,e));
                    if(v[0]!=0&&v[0]!=3) return lw_error(e,f->offset,"IK","rotation controller is outside the keyframe/IK profile");
                    n->control[k]=v[0]==3; break;
                }
                if(lw_string_is(f->name,limits[k])) {
                    LW_TRY(field_values(f,v,2,0,&recovered,e));
                    if(v[0]>v[1]||fabs(v[0])>36000||fabs(v[1])>36000) return lw_error(e,f->offset,"IK","invalid angle limits");
                    n->limited[k]=1; n->low[k]=v[0]*IK_PI/180; n->high[k]=v[1]*IK_PI/180; break;
                }
                if(lw_string_is(f->name,stiff[k])) {
                    LW_TRY(field_values(f,v,1,1,&recovered,e));
                    if(v[0]<0||v[0]>100000) return lw_error(e,f->offset,"IK","invalid joint stiffness");
                    n->stiff[k]=v[0]; if(recovered) { n->control[recovered-1]=1; b->recovered_fields++; } break;
                }
            }
            if(k<3) continue;
            if(lw_string_is(f->name,"GoalObject")) {
                LW_TRY(field_values(f,v,1,0,&recovered,e));
                if(v[0]<0||v[0]>268435455||floor(v[0])!=v[0]) return lw_error(e,f->offset,"IK","invalid LWSC 1/3 goal ordinal");
                if(v[0]) { n->goal=find(scene,0x10000000|((uint32_t)v[0]-1)); if(n->goal==SIZE_MAX) return lw_error(e,f->offset,"IK","missing IK goal object"); }
            } else if(lw_string_is(f->name,"IKAnchor")||lw_string_is(f->name,"FullTimeIK")||lw_string_is(f->name,"MatchGoalOrientation")) {
                LW_TRY(field_values(f,v,1,0,&recovered,e));
                if(v[0]!=0&&v[0]!=1) return lw_error(e,f->offset,"IK","invalid IK flag");
                if(lw_string_is(f->name,"IKAnchor")) n->stop=(int)v[0];
                else if(lw_string_is(f->name,"FullTimeIK")) n->full=(int)v[0];
                else n->match=(int)v[0];
            } else if(lw_string_is(f->name,"KeepGoalWithinReach")) {
                /* Preserve the option, but leave goal animation untouched. The
                   native solver can move goals when it cannot attain them. */
                LW_TRY(field_values(f,v,1,0,&recovered,e));
                if(v[0]!=0&&v[0]!=1) return lw_error(e,f->offset,"IK","invalid reach flag");
            } else if(lw_string_is(f->name,"GoalStrength")) {
                LW_TRY(field_values(f,v,1,0,&recovered,e));
                if(v[0]<0||v[0]>100000) return lw_error(e,f->offset,"IK","invalid goal strength"); n->strength=v[0];
            } else if(lw_string_is(f->name,"XController")||lw_string_is(f->name,"YController")||lw_string_is(f->name,"ZController")) {
                LW_TRY(field_values(f,v,1,0,&recovered,e)); if(v[0]) return lw_error(e,f->offset,"IK","position controller is outside the autonomous profile");
            } else if(lw_string_is(f->name,"BoneType")) {
                LW_TRY(field_values(f,v,1,0,&recovered,e)); if(v[0]) return lw_error(e,f->offset,"IK","joint-type bones are not qualified");
            } else if((f->name.size>=2&&!memcmp(f->name.data,"IK",2))||(f->name.size>=4&&!memcmp(f->name.data,"Goal",4))||lw_string_is(f->name,"UseIKChainValues")) return lw_error(e,f->offset,"IK","additional solver constraint is outside the autonomous profile");
        }
    }
    state=calloc(scene->nodes.n,1); if(!state) return lw_error(e,0,"allocation","cannot allocate hierarchy traversal");
    for(i=0;i<scene->nodes.n;i++) if(!sort_node(s,i,state,e,0)) { free(state); return 0; }
    free(state);
    for(i=0;i<scene->nodes.n;i++) {
        IKNode *n=&s->nodes[i];
        if(n->goal==SIZE_MAX||!n->full||!n->strength) { n->goal=SIZE_MAX; continue; }
        if(++b->goals>IK_GOALS) return lw_error(e,0,"IK","more than 32 active goals");
        for(j=n->parent;j!=SIZE_MAX;j=s->nodes[j].parent) if(s->nodes[j].stop) break;
        n->anchor=j;
    }
    return 1;
}
static void update(IKScene *s) {
    size_t i;
    for(i=0;i<s->count;i++) {
        size_t index=s->order[i]; IKNode *n=&s->nodes[index];
        matrix(n->local,n->v,s->scene->nodes.v[index].pivot);
        if(n->parent==SIZE_MAX) memcpy(n->world,n->local,sizeof n->world);
        else mul(n->world,s->nodes[n->parent].world,n->local);
    }
}
static size_t residual(IKScene *s,const size_t *goals,size_t count,double *r) {
    size_t i,k,n=0;
    for(i=0;i<count;i++) {
        IKNode *a=&s->nodes[goals[i]],*target=&s->nodes[a->goal]; double weight=sqrt(a->strength);
        /* A bone/object goal acts at its pivot, not at the tip of the bone. */
        for(k=0;k<3;k++) {
            double p=a->world[12+k],q=target->world[12+k]; size_t j;
            for(j=0;j<3;j++) { p+=a->world[4*j+k]*s->scene->nodes.v[goals[i]].pivot[j]; q+=target->world[4*j+k]*s->scene->nodes.v[a->goal].pivot[j]; }
            r[n++]=(p-q)*weight;
        }
        if(a->match&&(a->control[0]||a->control[1]||a->control[2])) for(k=0;k<12;k++) if(k%4!=3) r[n++]=(a->world[k]-target->world[k])*.1*weight;
    }
    return n;
}
static double square(const double *v,size_t n) { double sum=0; size_t i; for(i=0;i<n;i++) sum+=v[i]*v[i]; return sum; }
static int linear(double a[IK_VARS][IK_VARS],double *rhs,size_t n) {
    size_t i,j,k;
    for(i=0;i<n;i++) {
        size_t pivot=i; double d;
        for(j=i+1;j<n;j++) if(fabs(a[j][i])>fabs(a[pivot][i])) pivot=j;
        if(fabs(a[pivot][i])<1e-15) return 0;
        if(pivot!=i) { for(k=i;k<n;k++) { d=a[i][k]; a[i][k]=a[pivot][k]; a[pivot][k]=d; } d=rhs[i]; rhs[i]=rhs[pivot]; rhs[pivot]=d; }
        d=a[i][i]; for(k=i;k<n;k++) a[i][k]/=d; rhs[i]/=d;
        for(j=i+1;j<n;j++) { d=a[j][i]; for(k=i;k<n;k++) a[j][k]-=d*a[i][k]; rhs[j]-=d*rhs[i]; }
    }
    for(i=n;i-->0;) for(j=i+1;j<n;j++) rhs[i]-=a[i][j]*rhs[j];
    return 1;
}
static int solve_group(IKScene *s,size_t anchor,LWIKBake *b,LWError *e) {
    size_t goals[IK_GOALS],ng=0,nv=0,i,j,k,it,nr; IKVar vars[IK_VARS];
    double r[IK_GOALS*12],trial[IK_GOALS*12],jac[IK_VARS][IK_GOALS*12],normal[IK_VARS][IK_VARS],step[IK_VARS],old[IK_VARS],lambda=.001;
    for(i=0;i<s->count;i++) if(s->nodes[i].goal!=SIZE_MAX&&s->nodes[i].anchor==anchor) goals[ng++]=i;
    for(i=0;i<ng;i++) for(j=goals[i];j!=anchor&&j!=SIZE_MAX;j=s->nodes[j].parent) for(k=0;k<3;k++) if(s->nodes[j].control[k]&&(!s->nodes[j].limited[k]||s->nodes[j].low[k]<s->nodes[j].high[k])) {
        size_t v; for(v=0;v<nv;v++) if(vars[v].node==j&&vars[v].axis==k) break;
        if(v<nv) continue;
        if(nv==IK_VARS) return lw_error(e,0,"IK","more than 96 free axes in one chain group");
        vars[nv].node=j; vars[nv++].axis=(unsigned)k;
    }
    for(it=0;nv&&it<120;it++) {
        double cost,newcost,maxstep=0;
        update(s); nr=residual(s,goals,ng,r); cost=square(r,nr);
        if(cost<1e-12) break;
        for(i=0;i<nv;i++) {
            double *v=&s->nodes[vars[i].node].v[3+vars[i].axis]; old[i]=*v; *v+=1e-5;
            update(s); residual(s,goals,ng,trial); *v=old[i];
            for(k=0;k<nr;k++) jac[i][k]=(trial[k]-r[k])/1e-5;
        }
        for(i=0;i<nv;i++) {
            step[i]=0; for(k=0;k<nr;k++) step[i]-=jac[i][k]*r[k];
            for(j=0;j<nv;j++) { normal[i][j]=0; for(k=0;k<nr;k++) normal[i][j]+=jac[i][k]*jac[j][k]; }
            normal[i][i]+=lambda*(1+s->nodes[vars[i].node].stiff[vars[i].axis]);
        }
        /* Freeze axes whose downhill direction points beyond a bound. Merely
           clamping a coupled Newton step can stall the other free axes. */
        for(i=0;i<nv;i++) {
            IKNode *n=&s->nodes[vars[i].node]; unsigned axis=vars[i].axis;
            if(n->limited[axis]&&((old[i]<=n->low[axis]+1e-10&&step[i]<0)||(old[i]>=n->high[axis]-1e-10&&step[i]>0))) {
                for(j=0;j<nv;j++) normal[i][j]=normal[j][i]=0;
                normal[i][i]=1; step[i]=0;
            }
        }
        if(!linear(normal,step,nv)) break;
        for(i=0;i<nv;i++) maxstep=fmax(maxstep,fabs(step[i]));
        for(i=0;i<nv;i++) {
            IKNode *n=&s->nodes[vars[i].node]; unsigned axis=vars[i].axis;
            double value=old[i]+step[i]*fmin(1,.25/fmax(maxstep,1e-15));
            if(n->limited[axis]) value=fmax(n->low[axis],fmin(n->high[axis],value));
            n->v[3+axis]=value;
        }
        update(s); residual(s,goals,ng,trial); newcost=square(trial,nr); b->iterations++;
        if(newcost<cost) { lambda=fmax(1e-7,lambda*.6); if(cost-newcost<1e-12) break; }
        else {
            for(i=0;i<nv;i++) s->nodes[vars[i].node].v[3+vars[i].axis]=old[i];
            lambda*=4; if(lambda>1e8) break;
        }
    }
    update(s);
    for(i=0;i<ng;i++) { residual(s,&goals[i],1,r); b->max_goal_error=fmax(b->max_goal_error,sqrt(square(r,3)/s->nodes[goals[i]].strength)); }
    return 1;
}
int lw_bake_ik(const LWScene *scene,LWIKBake *b,LWError *e) {
    IKScene s={0}; size_t frame,i,j,k; int ok=0; LWError local={0};
    s.scene=scene; b->nodes=scene->nodes.n; b->first=scene->first_frame; b->last=scene->last_frame;
    if(!scene->nodes.n||scene->first_frame>=scene->last_frame) return 1;
    if(scene->version!=1&&scene->version!=3) { snprintf(b->issue,sizeof b->issue,"autonomous IK supports LWSC 1/3 only"); return 1; }
    if(scene->nodes.n>4096||scene->last_frame-scene->first_frame>1000) { snprintf(b->issue,sizeof b->issue,"autonomous bake is limited to 4096 nodes and 1001 samples"); return 1; }
    b->samples=(size_t)ceil(b->last-b->first)+1;
    if(b->samples*b->nodes>64*1024*1024/(20*sizeof(float))) { snprintf(b->issue,sizeof b->issue,"autonomous poses exceed 64 MiB"); b->samples=0; return 1; }
    if(!prepare(&s,b,&local)) goto unsupported;
    /* Unknown motion/channel plugins can change IK inputs. Keep their source,
       but do not claim that their poses have been evaluated autonomously. */
    for(i=0;i<scene->plugins.n;i++) {
        const LWPlugin *p=&scene->plugins.v[i]; const unsigned char *bytes=scene->source.data+p->offset;
        const char *types[]={"Plugin ItemMotionHandler ","Plugin ChannelHandler "};
        for(j=0;j<2;j++) if(p->size>=strlen(types[j])&&!memcmp(bytes,types[j],strlen(types[j]))) { lw_error(&local,p->offset,"IK","motion/channel plugins are outside the autonomous profile"); goto unsupported; }
    }
    b->poses=malloc(b->samples*b->nodes*20*sizeof(float));
    if(!b->poses) { lw_error(e,0,"allocation","cannot allocate autonomous poses"); goto done; }
    for(frame=0;frame<b->samples;frame++) {
        double time=fmin(b->first+(double)frame,b->last)/(scene->version==1?1:scene->fps);
        for(i=0;i<s.count;i++) {
            IKNode *n=&s.nodes[i]; const LWNode *source=&scene->nodes.v[i];
            double previous[3]; memcpy(previous,n->v+3,sizeof previous);
            for(j=0;j<9;j++) n->v[j]=j<6?0:1;
            for(j=0;j<source->channels.n;j++) {
                const LWChannel *c=&source->channels.v[j]; if(c->index>=9) continue;
                for(k=1;k<c->keys.n;k++) if(c->keys.v[k].shape!=0&&c->keys.v[k].shape!=3&&c->keys.v[k].shape!=4) { lw_error(&local,source->source_offset,"IK","unsupported envelope interpolation"); goto unsupported; }
                if(!lw_channel_value(c,time,&n->v[c->index])) { lw_error(&local,source->source_offset,"IK","unsupported envelope evaluation"); goto unsupported; }
            }
            if(scene->version==1) for(j=3;j<6;j++) n->v[j]*=IK_PI/180;
            if(frame) for(j=0;j<3;j++) if(n->control[j]) n->v[3+j]=previous[j];
            for(j=0;j<3;j++) if(n->control[j]&&n->limited[j]) n->v[3+j]=fmax(n->low[j],fmin(n->high[j],n->v[3+j]));
        }
        update(&s);
        if(!solve_group(&s,SIZE_MAX,b,&local)) goto unsupported;
        for(i=0;i<s.count;i++) {
            size_t anchor=s.order[i];
            for(j=0;j<s.count;j++) if(s.nodes[j].goal!=SIZE_MAX&&s.nodes[j].anchor==anchor) break;
            if(j<s.count&&!solve_group(&s,anchor,b,&local)) goto unsupported;
        }
        for(i=0;i<s.count;i++) {
            double trs[10];
            for(k=0;k<2;k++) {
                if(!lw_matrix_trs(k?s.nodes[i].world:s.nodes[i].local,trs,&local)) goto unsupported;
                for(j=0;j<10;j++) {
                    float v=(float)trs[j]; if(!isfinite(v)) { lw_error(&local,0,"IK","baked pose exceeds float32"); goto unsupported; }
                    b->poses[(frame*b->nodes+i)*20+k*10+j]=v;
                }
            }
        }
    }
    ok=1; goto done;
unsupported:
    if(!strcmp(local.context,"allocation")) { *e=local; goto done; }
    snprintf(b->issue,sizeof b->issue,"%.40s: %.210s",local.context,local.message);
    free(b->poses); b->poses=NULL; b->samples=0; ok=1;
done:
    free(s.nodes); free(s.order); return ok;
}
int lw_write_ik_bake(const char *directory,const LWScene *scene,const LWIKBake *b,LWError *e) {
    char *path=lw_join(directory,"baked-animation.bin"); FILE *f; size_t i; int ok;
    if(!path) return lw_error(e,0,"allocation","cannot allocate bake path");
    f=lw_fopen(path,"wb"); if(!f) { free(path); return lw_error(e,0,"output","cannot create autonomous bake"); }
    for(i=0;i<b->samples*b->nodes*20;i++) {
        uint32_t value; unsigned char bytes[4]; memcpy(&value,&b->poses[i],4);
        bytes[0]=(unsigned char)value; bytes[1]=(unsigned char)(value>>8); bytes[2]=(unsigned char)(value>>16); bytes[3]=(unsigned char)(value>>24); fwrite(bytes,1,4,f);
    }
    ok=lw_close(f,path,e); free(path); if(!ok) return 0;
    path=lw_join(directory,"baked-animation.json"); if(!path) return lw_error(e,0,"allocation","cannot allocate bake path");
    f=lw_fopen(path,"wb"); if(!f) { free(path); return lw_error(e,0,"output","cannot create bake metadata"); }
    fprintf(f,"{\"schema_version\":\"0.1\",\"kind\":\"derived-animation\",\"profile\":\"autonomous-hpb-ik-0.1\",\"approximation\":true,\"source_sha256\":\"%s\",\"first_frame\":%.17g,\"last_frame\":%.17g,\"fps\":%.17g,\"samples\":%zu,\"goals\":%zu,\"recovered_constraint_fields\":%zu,\"iterations\":%zu,\"maximum_goal_position_error\":%.17g,\"buffer\":{\"uri\":\"baked-animation.bin\",\"byte_length\":%zu,\"layout\":\"sample-major, source node order, local then world TRS (translation xyz, quaternion xyzw, scale xyz), little-endian float32\",\"node_stride\":80},\"node_ids\":[",scene->source.sha256,b->first,b->last,scene->fps,b->samples,b->goals,b->recovered_fields,b->iterations,b->max_goal_error,b->samples*b->nodes*80);
    for(i=0;i<b->nodes;i++) fprintf(f,"%s%u",i?",":"",scene->nodes.v[i].id);
    fputs("],\"limits\":\"Numerical H/P/B goal approximation; stiffness influences damping; previous solved angles initialize subsequent frames. KeepGoalWithinReach does not move targets; matching orientation is approximated only on goal items with IK rotation axes. Original keys and constraints remain in scene.json. Deformation plugins, joint compensation and muscle flex are not evaluated.\"}\n",f);
    ok=lw_close(f,path,e); free(path); return ok;
}
