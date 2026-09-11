#include "internal.h"
#include <ctype.h>
#include <errno.h>
#include <math.h>

typedef struct { LWString text; size_t offset; } Line;
typedef LW_ARRAY(Line) Lines;
static LWString trim(LWString s) {
    while(s.size&&isspace(s.data[0])) { s.data++; s.size--; }
    while(s.size&&isspace(s.data[s.size-1])) s.size--;
    return s;
}
static void split(Line line,LWString *key,LWString *value) {
    size_t i=0; LWString s=trim(line.text);
    while(i<s.size&&!isspace(s.data[i])) i++;
    *key=s; key->size=i; value->data=s.data+i; value->size=s.size-i; *value=trim(*value);
}
static LWString unquote(LWString s) {
    if(s.size>=2&&s.data[0]=='"'&&s.data[s.size-1]=='"') { s.data++; s.size-=2; }
    return s;
}
static int numbers(LWString s,double *v,size_t count,size_t off,LWError *e) {
    char text[2048],*p,*next; size_t i;
    if(s.size>=sizeof text) return lw_error(e,off,"LWS","numeric line too long");
    memcpy(text,s.data,s.size); text[s.size]=0; p=text;
    for(i=0;i<count;i++) {
        errno=0; v[i]=strtod(p,&next);
        if(next==p||errno==ERANGE||!isfinite(v[i])) return lw_error(e,off,"LWS","invalid or missing numeric value");
        p=next;
    }
    while(isspace((unsigned char)*p)) p++;
    if(*p) return lw_error(e,off,"LWS","unexpected data after numeric values");
    return 1;
}
static int integer(LWString s,uint32_t *value,size_t off,LWError *e) {
    double x; LW_TRY(numbers(s,&x,1,off,e));
    if(x<0||x>UINT32_MAX||floor(x)!=x) return lw_error(e,off,"LWS","expected an unsigned integer");
    *value=(uint32_t)x; return 1;
}
static int next_line(const Lines *ls,size_t *i,LWError *e) {
    if(*i+1>=ls->n) return lw_error(e,ls->v[*i].offset,"LWS","truncated block");
    ++*i; return 1;
}
static int channel_append(LWNode *node,uint32_t index,LWError *e) {
    LWChannel c={0}; c.index=index; c.pre=c.post=1;
    return LW_ADD(node->channels,c,e);
}
static int motion_v1(LWNode *node,const Lines *ls,size_t *i,LWError *e) {
    uint32_t channels,count,k,c; double values[64],meta[5];
    LW_TRY(next_line(ls,i,e)); LW_TRY(integer(ls->v[*i].text,&channels,ls->v[*i].offset,e));
    LW_TRY(next_line(ls,i,e)); LW_TRY(integer(ls->v[*i].text,&count,ls->v[*i].offset,e));
    if(!channels||channels>64||!count||count>ls->n/2) return lw_error(e,ls->v[*i].offset,"motion","invalid channel/key count");
    if(node->channels.n) return lw_error(e,ls->v[*i].offset,"motion","duplicate motion block");
    for(c=0;c<channels;c++) { LW_TRY(channel_append(node,c,e)); node->channels.v[c].declared_keys=count; }
    for(k=0;k<count;k++) {
        LW_TRY(next_line(ls,i,e)); LW_TRY(numbers(ls->v[*i].text,values,channels,ls->v[*i].offset,e));
        LW_TRY(next_line(ls,i,e)); LW_TRY(numbers(ls->v[*i].text,meta,5,ls->v[*i].offset,e));
        if(meta[1]!=0&&meta[1]!=1) return lw_error(e,ls->v[*i].offset,"motion","unsupported legacy interpolation flag");
        for(c=0;c<channels;c++) {
            LWKey key={0}; LWChannel *ch=&node->channels.v[c];
            key.time=meta[0]; key.value=values[c]; key.shape=meta[1]==1?3:0;
            memcpy(key.parameters,meta+2,3*sizeof(double));
            if(ch->keys.n&&key.time<=ch->keys.v[ch->keys.n-1].time) return lw_error(e,ls->v[*i].offset,"motion","key times are not strictly increasing");
            LW_TRY(LW_ADD(ch->keys,key,e));
        }
    }
    while(*i+1<ls->n) {
        LWString key,value; split(ls->v[*i+1],&key,&value);
        if(lw_string_is(key,"EndBehavior")) {
            uint32_t behavior; ++*i; LW_TRY(integer(value,&behavior,ls->v[*i].offset,e));
            for(c=0;c<channels;c++) node->channels.v[c].post=behavior;
        } else if(lw_string_is(key,"FrameOffset")) {
            double offset; ++*i; LW_TRY(numbers(value,&offset,1,ls->v[*i].offset,e));
            for(c=0;c<channels;c++) node->channels.v[c].offset=offset;
        } else break;
    }
    return 1;
}
static int motion_v3(LWNode *node,const Lines *ls,size_t *i,LWError *e) {
    LWString key,value; uint32_t count,c;
    LW_TRY(next_line(ls,i,e)); split(ls->v[*i],&key,&value);
    if(!lw_string_is(key,"NumChannels")) return lw_error(e,ls->v[*i].offset,"motion","expected NumChannels");
    LW_TRY(integer(value,&count,ls->v[*i].offset,e));
    if(!count||count>64||node->channels.n) return lw_error(e,ls->v[*i].offset,"motion","invalid or duplicate channel block");
    for(c=0;c<count;c++) {
        uint32_t index,nkeys,k; LWChannel *ch;
        LW_TRY(next_line(ls,i,e)); split(ls->v[*i],&key,&value);
        if(!lw_string_is(key,"Channel")) return lw_error(e,ls->v[*i].offset,"motion","expected Channel");
        LW_TRY(integer(value,&index,ls->v[*i].offset,e));
        for(k=0;k<node->channels.n;k++) if(node->channels.v[k].index==index) return lw_error(e,ls->v[*i].offset,"motion","duplicate channel index");
        LW_TRY(channel_append(node,index,e)); ch=&node->channels.v[node->channels.n-1];
        LW_TRY(next_line(ls,i,e)); split(ls->v[*i],&key,&value);
        if(!lw_string_is(key,"{")||!lw_string_is(value,"Envelope")) return lw_error(e,ls->v[*i].offset,"motion","expected { Envelope");
        LW_TRY(next_line(ls,i,e)); LW_TRY(integer(ls->v[*i].text,&nkeys,ls->v[*i].offset,e));
        ch->declared_keys=nkeys;
        for(;;) {
            double a[9]; LWKey frame={0};
            LW_TRY(next_line(ls,i,e)); split(ls->v[*i],&key,&value);
            if(lw_string_is(key,"Behaviors")) break;
            if(!lw_string_is(key,"Key")) return lw_error(e,ls->v[*i].offset,"motion","expected Key");
            LW_TRY(numbers(value,a,9,ls->v[*i].offset,e));
            if(a[2]<0||a[2]>UINT32_MAX||floor(a[2])!=a[2]) return lw_error(e,ls->v[*i].offset,"motion","invalid span type");
            frame.value=a[0]; frame.time=a[1]; frame.shape=(uint32_t)a[2]; memcpy(frame.parameters,a+3,6*sizeof(double));
            if(ch->keys.n&&frame.time<=ch->keys.v[ch->keys.n-1].time) return lw_error(e,ls->v[*i].offset,"motion","key times are not strictly increasing");
            LW_TRY(LW_ADD(ch->keys,frame,e));
        }
        if(ch->keys.n!=nkeys) node->unsupported_transform=1;
        { double b[2]; LW_TRY(numbers(value,b,2,ls->v[*i].offset,e));
          if(b[0]<0||b[0]>5||b[1]<0||b[1]>5||floor(b[0])!=b[0]||floor(b[1])!=b[1]) return lw_error(e,ls->v[*i].offset,"motion","invalid envelope behavior");
          ch->pre=(uint32_t)b[0]; ch->post=(uint32_t)b[1]; }
        LW_TRY(next_line(ls,i,e));
        split(ls->v[*i],&key,&value);
        while(lw_string_is(key,"{")) {
            unsigned depth=1; ch->opaque_modifiers++; node->unsupported_transform=1;
            while(depth) {
                LW_TRY(next_line(ls,i,e)); split(ls->v[*i],&key,&value);
                if(lw_string_is(key,"{")) depth++;
                else if(lw_string_is(key,"}")) depth--;
            }
            LW_TRY(next_line(ls,i,e)); split(ls->v[*i],&key,&value);
        }
        if(!lw_string_is(ls->v[*i].text,"}")) return lw_error(e,ls->v[*i].offset,"motion","expected envelope closing brace");
    }
    return 1;
}
static int add_node(LWScene *s,uint32_t id,Line line,LWString name,size_t *current,LWError *e) {
    LWNode n={0}; n.id=id; n.parent=LW_NONE; n.layer=LW_NONE; n.name=unquote(name);
    n.asset=SIZE_MAX; n.source_offset=line.offset;
    snprintf(n.resolution,sizeof n.resolution,"not-evaluated");
    LW_TRY(LW_ADD(s->nodes,n,e)); *current=s->nodes.n-1; return 1;
}
static int still_image(LWScene *s,const Lines *ls,size_t i,LWError *e) {
    LWString key,value; LWImageReference ref={0};
    split(ls->v[i],&key,&value);
    if(!lw_string_is(key,"{")||!lw_string_is(value,"Still")||i+2>=ls->n) return 1;
    split(ls->v[i+2],&key,&value);
    if(!lw_string_is(key,"}")) return 1;
    ref.path=unquote(ls->v[i+1].text); ref.clip=LW_NONE;
    ref.offset=(size_t)(ref.path.data-s->source.data);
    if(!ref.path.size||lw_string_is(ref.path,"(none)")||lw_string_is(ref.path,"<none>")) return 1;
    return LW_ADD(s->images,ref,e);
}
static int scene_lines(LWScene *s,const Lines *ls,LWError *e) {
    size_t i,current=SIZE_MAX; uint32_t object_count=0,light_count=0,camera_count=0,bone_count=0;
    if(ls->n<2||!lw_string_is(ls->v[0].text,"LWSC")) return lw_error(e,0,"LWS","expected LWSC header");
    LW_TRY(integer(ls->v[1].text,&s->version,ls->v[1].offset,e));
    if(s->version!=1&&s->version!=3) return lw_error(e,ls->v[1].offset,"LWS","only LWSC versions 1 and 3 are supported");
    for(i=2;i<ls->n;i++) {
        LWString key,value; LWNode *node; split(ls->v[i],&key,&value);
        if(lw_string_is(key,"Plugin")) {
            size_t start=i; unsigned depth=1; LWPlugin plugin={0}; plugin.name=value; plugin.offset=ls->v[i].offset;
            if(current!=SIZE_MAX&&value.size>=17&&!memcmp(value.data,"ItemMotionHandler",17)) s->nodes.v[current].unsupported_transform=1;
            while(depth) {
                LW_TRY(next_line(ls,&i,e)); split(ls->v[i],&key,&value);
                if(lw_string_is(key,"Plugin")) depth++;
                else if(lw_string_is(key,"EndPlugin")) depth--;
            }
            plugin.size=ls->v[i].offset+ls->v[i].text.size-ls->v[start].offset;
            LW_TRY(LW_ADD(s->plugins,plugin,e)); continue;
        }
        if(lw_string_is(key,"{")) {
            unsigned depth=1;
            LW_TRY(still_image(s,ls,i,e));
            while(depth) {
                LW_TRY(next_line(ls,&i,e)); split(ls->v[i],&key,&value);
                if(lw_string_is(key,"{")) { depth++; LW_TRY(still_image(s,ls,i,e)); }
                else if(lw_string_is(key,"}")) depth--;
            }
            s->opaque_blocks++; continue;
        }
        if(lw_string_is(key,"LoadObject")||lw_string_is(key,"LoadObjectLayer")||lw_string_is(key,"AddNullObject")) {
            int load=!lw_string_is(key,"AddNullObject"); uint32_t layer=LW_NONE;
            if(lw_string_is(key,"LoadObjectLayer")) {
                Line rest={value,ls->v[i].offset}; LWString first; split(rest,&first,&value);
                LW_TRY(integer(first,&layer,ls->v[i].offset,e));
                if(!layer) return lw_error(e,ls->v[i].offset,"layer","expected a positive scene layer request");
            }
            if(!value.size) return lw_error(e,ls->v[i].offset,"object","missing name/path");
            LW_TRY(add_node(s,0x10000000|object_count++,ls->v[i],value,&current,e)); bone_count=0;
            if(load) { s->nodes.v[current].object_path=unquote(value); s->nodes.v[current].layer=layer; }
            continue;
        }
        if(lw_string_is(key,"AddLight")) { LW_TRY(add_node(s,0x20000000|light_count++,ls->v[i],value,&current,e)); continue; }
        if(lw_string_is(key,"AddCamera") || (s->version==1&&lw_string_is(key,"ShowCamera")&&!camera_count)) {
            LW_TRY(add_node(s,0x30000000|camera_count++,ls->v[i],lw_string("Camera"),&current,e)); continue;
        }
        if(lw_string_is(key,"AddBone")) {
            uint32_t id;
            if(!object_count||object_count>65536||bone_count>=4096) return lw_error(e,ls->v[i].offset,"bone","invalid owner/bone ordinal");
            id=0x40000000|(bone_count++<<16)|(object_count-1);
            LW_TRY(add_node(s,id,ls->v[i],value,&current,e)); s->nodes.v[current].unsupported_transform=1; s->unsupported_features++; continue;
        }
        if(lw_string_is(key,"CameraMotion")&&current==SIZE_MAX) LW_TRY(add_node(s,0x30000000|camera_count++,ls->v[i],lw_string("Camera"),&current,e));
        node=current==SIZE_MAX?NULL:&s->nodes.v[current];
        if(lw_string_is(key,"FirstFrame")) LW_TRY(numbers(value,&s->first_frame,1,ls->v[i].offset,e));
        else if(lw_string_is(key,"LastFrame")) LW_TRY(numbers(value,&s->last_frame,1,ls->v[i].offset,e));
        else if(lw_string_is(key,"FramesPerSecond")) LW_TRY(numbers(value,&s->fps,1,ls->v[i].offset,e));
        else if(node&&(lw_string_is(key,"ObjectMotion")||lw_string_is(key,"LightMotion")||lw_string_is(key,"CameraMotion")||lw_string_is(key,"BoneMotion"))) {
            LW_TRY(s->version==1?motion_v1(node,ls,&i,e):motion_v3(node,ls,&i,e));
        } else if(node&&(lw_string_is(key,"LightName")||lw_string_is(key,"CameraName")||lw_string_is(key,"BoneName"))) node->name=unquote(value);
        else if(node&&(lw_string_is(key,"PivotPoint")||lw_string_is(key,"PivotPosition"))) LW_TRY(numbers(value,node->pivot,3,ls->v[i].offset,e));
        else if(node&&lw_string_is(key,"PivotRotation")) {
            LW_TRY(numbers(value,node->pivot_rotation,3,ls->v[i].offset,e));
            if(node->pivot_rotation[0]||node->pivot_rotation[1]||node->pivot_rotation[2]) node->unsupported_transform=1;
        } else if(node&&lw_string_is(key,"ParentObject")) {
            uint32_t parent; LW_TRY(integer(value,&parent,ls->v[i].offset,e)); node->parent=parent?0x10000000|(parent-1):LW_NONE;
        } else if(node&&lw_string_is(key,"ParentItem")) {
            char buffer[32],*end; unsigned long parent;
            if(!value.size||value.size>=sizeof buffer) return lw_error(e,ls->v[i].offset,"parent","invalid item ID");
            memcpy(buffer,value.data,value.size); buffer[value.size]=0; errno=0; parent=strtoul(buffer,&end,16);
            if(*end||errno||parent>UINT32_MAX) return lw_error(e,ls->v[i].offset,"parent","invalid hexadecimal item ID");
            node->parent=parent?(uint32_t)parent:LW_NONE;
        } else if(lw_string_is(key,"FullTimeIK")||lw_string_is(key,"GoalObject")||lw_string_is(key,"MorphTarget")||lw_string_is(key,"DisplacementMap")||lw_string_is(key,"DisplacementMaps")) {
            s->unsupported_features++; if(node&&(lw_string_is(key,"FullTimeIK")||lw_string_is(key,"GoalObject"))) node->unsupported_transform=1;
        }
    }
    if(s->fps<=0) return lw_error(e,0,"LWS","FramesPerSecond must be positive");
    return 1;
}
int lw_parse_scene(LWScene *s,LWError *e) {
    Lines lines={0}; size_t p=0; int ok;
    s->fps=30; s->first_frame=0; s->last_frame=0;
    if(memchr(s->source.data,0,s->source.size)) return lw_error(e,0,"LWS","embedded NUL in scene text");
    while(p<s->source.size) {
        size_t start=p; Line line;
        while(p<s->source.size&&s->source.data[p]!='\r'&&s->source.data[p]!='\n') p++;
        line.text.data=s->source.data+start; line.text.size=p-start; line.text=trim(line.text);
        line.offset=(size_t)(line.text.data-s->source.data);
        if(line.text.size&&!LW_ADD(lines,line,e)) { LW_FREE(lines); return 0; }
        if(p<s->source.size&&s->source.data[p]=='\r') p++;
        if(p<s->source.size&&s->source.data[p]=='\n') p++;
    }
    ok=scene_lines(s,&lines,e); LW_FREE(lines); return ok;
}
int lw_load_scene(const char *path,LWScene *s,LWError *e) {
    memset(s,0,sizeof *s); LW_TRY(lw_read_source(path,&s->source,e));
    if(!lw_parse_scene(s,e)) { lw_free_scene(s); return 0; } return 1;
}
void lw_free_scene(LWScene *s) {
    size_t i,j;
    for(i=0;i<s->nodes.n;i++) {
        LWNode *n=&s->nodes.v[i];
        for(j=0;j<n->channels.n;j++) LW_FREE(n->channels.v[j].keys);
        for(j=0;j<n->candidates.n;j++) free(n->candidates.v[j]);
        LW_FREE(n->candidates); LW_FREE(n->channels); free(n->resolved_path);
    }
    for(i=0;i<s->images.n;i++) lw_free_image(&s->images.v[i]);
    LW_FREE(s->images); LW_FREE(s->nodes); LW_FREE(s->plugins); lw_free_source(&s->source); memset(s,0,sizeof *s);
}
void lw_scene_summary(FILE *f,const LWScene *s) {
    size_t i,j,objects=0,bones=0,keys=0;
    for(i=0;i<s->nodes.n;i++) {
        objects+=s->nodes.v[i].object_path.size!=0; bones+=(s->nodes.v[i].id>>28)==4;
        for(j=0;j<s->nodes.v[i].channels.n;j++) keys+=s->nodes.v[i].channels.v[j].keys.n;
    }
    fprintf(f,"{\"kind\":\"scene\",\"version\":%u,\"sha256\":\"%s\",\"nodes\":%zu,\"object_loads\":%zu,\"bones\":%zu,\"keys\":%zu,\"plugins\":%zu,\"first_frame\":%.17g,\"last_frame\":%.17g,\"fps\":%.17g}\n",s->version,s->source.sha256,s->nodes.n,objects,bones,keys,s->plugins.n,s->first_frame,s->last_frame,s->fps);
}

/* Deliberately bounded first evaluator: exact keys, constant, linear, stepped.
   Spline keys remain intact in LWIR. Unsupported sampling refuses scene OBJ. */
int lw_channel_value(const LWChannel *c,double time,double *value) {
    size_t i; const LWKey *a,*b;
    if(!c->keys.n) return 0;
    time-=c->offset; a=c->keys.v; b=&c->keys.v[c->keys.n-1];
    for(i=0;i<c->keys.n;i++) if(time==c->keys.v[i].time) { *value=c->keys.v[i].value; return 1; }
    if(time<a->time||time>b->time) {
        uint32_t behavior=time<a->time?c->pre:c->post;
        if(behavior==0) { *value=0; return 1; }
        if(behavior==1) { *value=time<a->time?a->value:b->value; return 1; }
        if(behavior==2&&b->time>a->time) {
            time=a->time+fmod(time-a->time,b->time-a->time);
            if(time<a->time) time+=b->time-a->time;
        } else return 0;
    }
    if(c->keys.n==1) { *value=a->value; return 1; }
    for(i=1;i<c->keys.n;i++) if(time<=c->keys.v[i].time) {
        a=&c->keys.v[i-1]; b=&c->keys.v[i];
        if(time==a->time) { *value=a->value; return 1; }
        if(time==b->time) { *value=b->value; return 1; }
        if(a->value==b->value && (b->shape==3||b->shape==4)) { *value=a->value; return 1; }
        if(b->shape==4) { *value=a->value; return 1; }
        if(b->shape!=3) return 0;
        *value=a->value+(b->value-a->value)*(time-a->time)/(b->time-a->time); return isfinite(*value);
    }
    return 0;
}
void lw_identity(double m[16]) { unsigned i; for(i=0;i<16;i++) m[i]=(i%5)==0?1:0; }
static void multiply(double out[16],const double a[16],const double b[16]) {
    unsigned row,col,k; double result[16]={0};
    for(col=0;col<4;col++) for(row=0;row<4;row++) for(k=0;k<4;k++) result[4*col+row]+=a[4*k+row]*b[4*col+k];
    memcpy(out,result,sizeof result);
}
static void local_matrix(double out[16],const double v[9],const double pivot[3]) {
    double y[16],x[16],z[16],scale[16],t[16]; unsigned i;
    lw_identity(y); lw_identity(x); lw_identity(z); lw_identity(scale); lw_identity(t);
    y[0]=y[10]=cos(v[3]); y[8]=sin(v[3]); y[2]=-y[8];
    x[5]=x[10]=cos(v[4]); x[6]=sin(v[4]); x[9]=-x[6];
    z[0]=z[5]=cos(v[5]); z[1]=sin(v[5]); z[4]=-z[1];
    for(i=0;i<3;i++) { scale[5*i]=v[6+i]; t[12+i]=-pivot[i]; }
    multiply(out,y,x); multiply(out,out,z); multiply(out,out,scale); multiply(out,out,t);
    for(i=0;i<3;i++) out[12+i]+=v[i];
}
int lw_scene_node_matrix(const LWScene *s,size_t i,double frame,double m[16],LWError *e) {
    const LWNode *node=&s->nodes.v[i]; size_t j; double v[9]={0,0,0,0,0,0,1,1,1};
    double time=s->version==1?frame:frame/s->fps;
    if(node->unsupported_transform) return lw_error(e,node->source_offset,"transform","node %08x requires unsupported pivot/IK/bone evaluation",node->id);
    for(j=0;j<node->channels.n;j++) {
        const LWChannel *c=&node->channels.v[j];
        if(c->index<9 && !lw_channel_value(c,time,&v[c->index])) return lw_error(e,node->source_offset,"animation","node %08x channel %u cannot be sampled by the first evaluator",node->id,c->index);
    }
    if(s->version==1) for(j=3;j<6;j++) v[j]*=0.017453292519943295;
    local_matrix(m,v,node->pivot);
    for(j=0;j<16;j++) if(!isfinite(m[j])) return lw_error(e,node->source_offset,"transform","non-finite matrix");
    return 1;
}
static int world_node(const LWScene *s,size_t i,double frame,double *matrices,unsigned char *state,size_t *bad,LWError *e,unsigned depth) {
    const LWNode *node=&s->nodes.v[i]; size_t j; double *m=matrices+16*i;
    if(state[i]==2) return 1;
    if(state[i]==1||depth>1024) return lw_error(e,node->source_offset,"hierarchy","cyclic or excessively deep parent chain");
    state[i]=1;
    if(!lw_scene_node_matrix(s,i,frame,m,e)) { *bad=i; return 0; }
    if(node->parent!=LW_NONE) {
        for(j=0;j<s->nodes.n;j++) if(s->nodes.v[j].id==node->parent) break;
        if(j==s->nodes.n) return lw_error(e,node->source_offset,"hierarchy","missing parent %08x",node->parent);
        LW_TRY(world_node(s,j,frame,matrices,state,bad,e,depth+1)); multiply(m,matrices+16*j,m);
    }
    for(j=0;j<16;j++) if(!isfinite(m[j])) return lw_error(e,node->source_offset,"transform","non-finite matrix");
    state[i]=2; return 1;
}
int lw_scene_matrices(const LWScene *s,double frame,double *matrices,size_t *bad,LWError *e) {
    unsigned char *state=calloc(s->nodes.n?s->nodes.n:1,1); size_t i; int ok=1;
    if(!state) return lw_error(e,0,"allocation","out of memory");
    for(i=0;i<s->nodes.n;i++) {
        lw_identity(matrices+16*i);
        /* Only geometry instances and their parents are needed by OBJ. */
    }
    for(i=0;i<s->nodes.n;i++) if(s->nodes.v[i].object_path.size&&s->nodes.v[i].asset!=SIZE_MAX) {
        if(!world_node(s,i,frame,matrices,state,bad,e,0)) { ok=0; break; }
    }
    free(state); return ok;
}
