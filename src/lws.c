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
static int rotation_controller(LWString key) {
    return lw_string_is(key,"HController")||lw_string_is(key,"PController")||lw_string_is(key,"BController");
}
static int ik_parameter(LWString key) {
    return rotation_controller(key)||lw_string_is(key,"GoalObject")||lw_string_is(key,"GoalStrength")||
        lw_string_is(key,"FullTimeIK")||lw_string_is(key,"IKAnchor")||lw_string_is(key,"MatchGoalOrientation")||
        lw_string_is(key,"HLimits")||lw_string_is(key,"PLimits")||lw_string_is(key,"BLimits")||
        lw_string_is(key,"HJointStiffness")||lw_string_is(key,"PJointStiffness")||lw_string_is(key,"BJointStiffness");
}
static int preserve_ik_parameter(LWScene *scene,LWNode *node,LWString key,LWString value,size_t offset,LWError *e) {
    LWTextureField field={0}; LWError numeric_error={0}; uint32_t mode=0; const char *issue=NULL;
    field.name=key; field.value=value; field.parent=SIZE_MAX; field.offset=offset;
    LW_TRY(LW_ADD(node->rig_parameters,field,e));
    if(rotation_controller(key)) {
        if(!integer(value,&mode,offset,&numeric_error)) issue="malformed rotation controller";
        else if(mode) {
            /* NewTek lwrender.h: 0 keyframes, 1 targeting, 2 velocity,
               3 inverse kinematics, 4 path alignment. None are raw keys. */
            issue=mode==3?"requires native inverse-kinematics evaluation":"requires unsupported motion-controller evaluation";
        }
    } else if(lw_string_is(key,"GoalObject")) issue="requires native inverse-kinematics goal evaluation";
    else if(lw_string_is(key,"FullTimeIK")) {
        if(!integer(value,&mode,offset,&numeric_error)||mode>1) issue="malformed FullTimeIK flag";
        else if(mode) issue="requires native inverse-kinematics evaluation";
    } else if(lw_string_is(key,"HJointStiffness")||lw_string_is(key,"PJointStiffness")||lw_string_is(key,"BJointStiffness")) {
        double stiffness;
        if(!numbers(value,&stiffness,1,offset,&numeric_error)) issue="malformed joint stiffness; possible concatenated scene statements";
    }
    if(issue) {
        scene->unsupported_features++; node->unsupported_transform=1;
        if(!node->transform_issue[0]) snprintf(node->transform_issue,sizeof node->transform_issue,
            "%.*s %.*s: %s",(int)key.size,(const char *)key.data,(int)(value.size<48?value.size:48),(const char *)value.data,issue);
    }
    return 1;
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
        if(!ch->keys.n) {
            node->unsupported_transform=1;
            snprintf(node->transform_issue,sizeof node->transform_issue,"channel %u has no keys",ch->index);
        }
        if(ch->keys.n!=nkeys) node->key_count_mismatches++;
        { double b[2]; LW_TRY(numbers(value,b,2,ls->v[*i].offset,e));
          if(b[0]<0||b[0]>5||b[1]<0||b[1]>5||floor(b[0])!=b[0]||floor(b[1])!=b[1]) return lw_error(e,ls->v[*i].offset,"motion","invalid envelope behavior");
          ch->pre=(uint32_t)b[0]; ch->post=(uint32_t)b[1]; }
        LW_TRY(next_line(ls,i,e));
        split(ls->v[*i],&key,&value);
        while(lw_string_is(key,"{")) {
            unsigned depth=1; ch->opaque_modifiers++; node->unsupported_transform=1;
            snprintf(node->transform_issue,sizeof node->transform_issue,"channel %u has an unsupported envelope modifier",ch->index);
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
/* Qualified legacy LW_Follower profile: a sibling's bank mirrored at the same
   time. The full plugin payload remains archived. This does not claim general
   Follower, world-space, timing, IK or arbitrary channel-remapping support. */
static int mirrored_bank_follower(LWNode *node,const Lines *ls,size_t start,size_t end) {
    LWError local={0}; double id[2]; size_t i;
    if(end-start!=13||node->mirrored_bank_follower) return 0;
    if(!numbers(unquote(ls->v[start+1].text),id,2,ls->v[start+1].offset,&local)||id[0]<1||id[0]>UINT32_MAX||floor(id[0])!=id[0]||id[1]!=1) return 0;
    for(i=2;i<=3;i++) {
        LWString key,value; Line line={unquote(ls->v[start+i].text),ls->v[start+i].offset}; split(line,&key,&value);
        if(i==2) {
            double channels[10]; size_t j;
            if(!lw_string_is(key,"Channels")||!numbers(value,channels,10,line.offset,&local)||channels[0]!=32) return 0;
            for(j=0;j<9;j++) if(channels[j+1]!=(j==5?5:0)) return 0;
        } else {
            char text[256],tail; double delay,random,path; unsigned flags; int count;
            if(!lw_string_is(key,"TimeSlip")||value.size>=sizeof text) return 0;
            memcpy(text,value.data,value.size); text[value.size]=0;
            count=sscanf(text,"%lf Randomize %lf PathSlip %lf %u %c",&delay,&random,&path,&flags,&tail);
            if(count!=4||delay!=0||random!=0||path!=0||flags!=1) return 0;
        }
    }
    for(i=0;i<9;i++) {
        LWString value=unquote(ls->v[start+4+i].text); char text[256],tail; double scale,add;
        if(value.size>=sizeof text) return 0;
        memcpy(text,value.data,value.size); text[value.size]=0;
        if(sscanf(text,"Scale %lf Add %lf %c",&scale,&add,&tail)!=2||scale!=(i==5?-1:1)||add!=0) return 0;
    }
    node->follower_source=(uint32_t)id[0]; node->mirrored_bank_follower=1; return 1;
}
static int add_node(LWScene *s,uint32_t id,Line line,LWString name,size_t *current,LWError *e) {
    LWNode n={0}; n.id=id; n.parent=LW_NONE; n.layer=LW_NONE; n.name=unquote(name);
    n.bone.owner=LW_NONE; n.bone_falloff=LW_NONE;
    n.bone.active=n.bone.normalize=n.bone.scale_strength=1; n.bone.strength=1;
    n.asset=SIZE_MAX; n.source_offset=line.offset;
    snprintf(n.resolution,sizeof n.resolution,"not-evaluated");
    LW_TRY(LW_ADD(s->nodes,n,e)); *current=s->nodes.n-1; return 1;
}
static uint32_t motion_kind(LWString key) {
    if(lw_string_is(key,"ObjectMotion")) return 1;
    if(lw_string_is(key,"LightMotion")) return 2;
    if(lw_string_is(key,"CameraMotion")) return 3;
    if(lw_string_is(key,"BoneMotion")) return 4;
    return 0;
}
static int scene_image(LWScene *s,LWString path,LWClipMap *clip,LWError *e) {
    LWImageReference ref={0};
    ref.path=unquote(path); ref.clip=LW_NONE; ref.role=clip?"clip-map":NULL;
    ref.offset=(size_t)(ref.path.data-s->source.data);
    if(!ref.path.size||lw_string_is(ref.path,"(none)")||lw_string_is(ref.path,"<none>")) return 1;
    LW_TRY(LW_ADD(s->images,ref,e));
    return !clip||LW_ADD(clip->images,s->images.n-1,e);
}
static int still_image(LWScene *s,const Lines *ls,size_t i,LWClipMap *clip,LWError *e) {
    LWString key,value;
    split(ls->v[i],&key,&value);
    if(!lw_string_is(key,"{")||!lw_string_is(value,"Still")||i+2>=ls->n) return 1;
    split(ls->v[i+2],&key,&value);
    if(!lw_string_is(key,"}")) return 1;
    return scene_image(s,ls->v[i+1].text,clip,e);
}
static int texture_field(LWClipMap *clip,Line line,size_t parent,int block,LWError *e) {
    LWTextureField field={0}; LWString key,value;
    split(line,&key,&value); field.parent=parent; field.offset=line.offset; field.block=block;
    if(block) { line.text=value; split(line,&key,&value); }
    if(key.size&&(isalpha(key.data[0])||key.data[0]=='_')) { field.name=key; field.value=value; }
    else field.value=line.text;
    return LW_ADD(clip->fields,field,e);
}
static int texture_block(LWScene *s,const Lines *ls,size_t *i,LWClipMap *clip,LWError *e) {
    size_t parents[128],depth=0;
    do {
        LWString key,value; split(ls->v[*i],&key,&value);
        if(lw_string_is(key,"}")) {
            if(!depth) return lw_error(e,ls->v[*i].offset,"texture","unexpected closing brace");
            depth--;
        } else {
            int block=lw_string_is(key,"{");
            if(clip) LW_TRY(texture_field(clip,ls->v[*i],depth?parents[depth-1]:SIZE_MAX,block,e));
            if(block) {
                if(depth==128) return lw_error(e,ls->v[*i].offset,"texture","block nesting exceeds 128");
                parents[depth++]=clip?clip->fields.n-1:0;
                LW_TRY(still_image(s,ls,*i,clip,e));
            }
        }
        if(depth) LW_TRY(next_line(ls,i,e));
    } while(depth);
    return 1;
}
static int clip_map(LWScene *s,const Lines *ls,size_t *i,size_t owner,int modern,LWError *e) {
    LWClipMap map={0},*clip; LWNode *node; LWString key,value;
    if(owner==SIZE_MAX||(s->nodes.v[owner].id>>28)!=1) return lw_error(e,ls->v[*i].offset,"clip-map","missing object owner");
    node=&s->nodes.v[owner]; map.offset=ls->v[*i].offset;
    split(ls->v[*i],&key,&map.declaration);
    LW_TRY(LW_ADD(node->clip_maps,map,e)); clip=&node->clip_maps.v[node->clip_maps.n-1];
    if(modern) {
        LW_TRY(next_line(ls,i,e)); split(ls->v[*i],&key,&value);
        if(!lw_string_is(key,"{")||!lw_string_is(value,"TextureBlock")) return lw_error(e,ls->v[*i].offset,"clip-map","expected TextureBlock");
        LW_TRY(texture_block(s,ls,i,clip,e));
    } else {
        while(*i+1<ls->n) {
            split(ls->v[*i+1],&key,&value);
            if(key.size<7||memcmp(key.data,"Texture",7)) break;
            ++*i; LW_TRY(texture_field(clip,ls->v[*i],SIZE_MAX,0,e));
            if(lw_string_is(key,"TextureImage")) LW_TRY(scene_image(s,value,clip,e));
        }
    }
    clip->size=ls->v[*i].offset+ls->v[*i].text.size-clip->offset;
    return 1;
}
static int scene_lines(LWScene *s,const Lines *ls,LWError *e) {
    size_t i,current=SIZE_MAX; uint32_t object_count=0,light_count=0,camera_count=0,bone_count=0;
    if(ls->n<2||!lw_string_is(ls->v[0].text,"LWSC")) return lw_error(e,0,"LWS","expected LWSC header");
    LW_TRY(integer(ls->v[1].text,&s->version,ls->v[1].offset,e));
    if(s->version!=1&&s->version!=3) return lw_error(e,ls->v[1].offset,"LWS","only LWSC versions 1 and 3 are supported");
    for(i=2;i<ls->n;i++) {
        LWString key,value; LWNode *node; split(ls->v[i],&key,&value);
        if(lw_string_is(key,"ClipMaps")||lw_string_is(key,"ClipMap")) {
            LW_TRY(clip_map(s,ls,&i,current,lw_string_is(key,"ClipMaps"),e)); continue;
        }
        if(lw_string_is(key,"Plugin")) {
            size_t start=i; unsigned depth=1; LWPlugin plugin={0}; plugin.name=value; plugin.offset=ls->v[i].offset;
            while(depth) {
                LW_TRY(next_line(ls,&i,e)); split(ls->v[i],&key,&value);
                if(lw_string_is(key,"Plugin")) depth++;
                else if(lw_string_is(key,"EndPlugin")) depth--;
            }
            plugin.size=ls->v[i].offset+ls->v[i].text.size-ls->v[start].offset;
            if(current!=SIZE_MAX&&plugin.name.size>=17&&!memcmp(plugin.name.data,"ItemMotionHandler",17)) {
                LWNode *owner=&s->nodes.v[current];
                int enabled=1;
                if(i+1<ls->n) {
                    LWString next_key,next_value; split(ls->v[i+1],&next_key,&next_value);
                    if(lw_string_is(next_key,"PluginEnabled")&&!lw_string_is(next_value,"1")) enabled=0;
                }
                if(enabled&&lw_string_is(plugin.name,"ItemMotionHandler 1 LW_Follower")&&mirrored_bank_follower(owner,ls,start,i)) {
                    owner->follower_plugin=s->plugins.n; plugin.interpreted=1;
                } else {
                    owner->unsupported_transform=1;
                    snprintf(owner->transform_issue,sizeof owner->transform_issue,"unsupported item motion plugin or Follower configuration");
                }
            }
            LW_TRY(LW_ADD(s->plugins,plugin,e)); continue;
        }
        if(lw_string_is(key,"{")) {
            LW_TRY(texture_block(s,ls,&i,NULL,e));
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
            LW_TRY(add_node(s,id,ls->v[i],value,&current,e));
            s->nodes.v[current].bone.owner=0x10000000|(object_count-1);
            s->nodes.v[current].parent=s->nodes.v[current].bone.owner;
            s->nodes.v[current].unsupported_transform=1; s->unsupported_features++; continue;
        }
        if(lw_string_is(key,"CameraMotion")&&(s->version==1||current==SIZE_MAX)) {
            /* LWSC 1 has an implicit camera. CameraMotion usually follows the
               lights, while ShowCamera is a visibility setting near EOF.
               Select its own node, never the preceding object's/light's keys. */
            if(!camera_count) LW_TRY(add_node(s,0x30000000|camera_count++,ls->v[i],lw_string("Camera"),&current,e));
            else if(s->version==1) {
                size_t camera;
                for(camera=0;camera<s->nodes.n;camera++) if(s->nodes.v[camera].id==0x30000000) { current=camera; break; }
            }
        }
        node=current==SIZE_MAX?NULL:&s->nodes.v[current];
        if(node&&ik_parameter(key)) {
            LW_TRY(preserve_ik_parameter(s,node,key,value,ls->v[i].offset,e)); continue;
        }
        if(node&&((key.size>=4&&!memcmp(key.data,"Bone",4)&&!lw_string_is(key,"BoneMotion")&&!lw_string_is(key,"BoneName"))||
           lw_string_is(key,"ScaleBoneStrength")||lw_string_is(key,"FasterBones")||lw_string_is(key,"UseBonesFrom")||
           lw_string_is(key,"SubdivisionOrder")||lw_string_is(key,"SubPatchLevel"))) {
            LWTextureField field={0}; LWBone *bone=&node->bone;
            field.name=key; field.value=value; field.parent=SIZE_MAX; field.offset=ls->v[i].offset;
            LW_TRY(LW_ADD(node->rig_parameters,field,e));
            if(lw_string_is(key,"BoneFalloffType")) LW_TRY(integer(value,&node->bone_falloff,field.offset,e));
            else if(lw_string_is(key,"FasterBones")) { uint32_t v; LW_TRY(integer(value,&v,field.offset,e)); node->faster_bones=v!=0; }
            else if(bone->owner!=LW_NONE) {
                double *vector=lw_string_is(key,"BoneRestPosition")?bone->rest_position:lw_string_is(key,"BoneRestDirection")?bone->rest_rotation:NULL;
                double *scalar=lw_string_is(key,"BoneRestLength")?&bone->rest_length:lw_string_is(key,"BoneStrength")?&bone->strength:
                    lw_string_is(key,"BoneMinRange")?&bone->range[0]:lw_string_is(key,"BoneMaxRange")?&bone->range[1]:
                    lw_string_is(key,"BoneJointComp")?&bone->joint_comp[0]:lw_string_is(key,"BoneJointCompParent")?&bone->joint_comp[1]:
                    lw_string_is(key,"BoneMuscleFlex")?&bone->muscle_flex[0]:lw_string_is(key,"BoneMuscleFlexParent")?&bone->muscle_flex[1]:NULL;
                int *flag=lw_string_is(key,"BoneActive")?&bone->active:lw_string_is(key,"BoneWeightMapOnly")?&bone->weight_map_only:
                    lw_string_is(key,"BoneNormalization")?&bone->normalize:lw_string_is(key,"ScaleBoneStrength")?&bone->scale_strength:
                    lw_string_is(key,"BoneLimitedRange")?&bone->limited_range:NULL;
                if(vector) { LW_TRY(numbers(value,vector,3,field.offset,e)); bone->present|=vector==bone->rest_position?1:2; }
                else if(scalar) { LW_TRY(numbers(value,scalar,1,field.offset,e)); if(scalar==&bone->rest_length) bone->present|=4; }
                else if(flag) { uint32_t v; LW_TRY(integer(value,&v,field.offset,e)); if(v>1) return lw_error(e,field.offset,"bone","expected a boolean bone setting"); *flag=(int)v; }
                else if(lw_string_is(key,"BoneWeightMapName")) bone->weight_map=unquote(value);
            }
            continue;
        }
        if(node&&lw_string_is(key,"ObjectDissolve")) {
            size_t start=ls->v[i].offset;
            if(i+1<ls->n) {
                LWString next_key,next_value; split(ls->v[i+1],&next_key,&next_value);
                if(lw_string_is(next_key,"{")&&lw_string_is(next_value,"Envelope")) {
                    ++i; LW_TRY(texture_block(s,ls,&i,NULL,e));
                }
            }
            node->object_dissolve.data=s->source.data+start;
            node->object_dissolve.size=ls->v[i].offset+ls->v[i].text.size-start;
            continue;
        }
        if(lw_string_is(key,"FirstFrame")) LW_TRY(numbers(value,&s->first_frame,1,ls->v[i].offset,e));
        else if(lw_string_is(key,"LastFrame")) LW_TRY(numbers(value,&s->last_frame,1,ls->v[i].offset,e));
        else if(lw_string_is(key,"FramesPerSecond")) LW_TRY(numbers(value,&s->fps,1,ls->v[i].offset,e));
        else if(motion_kind(key)) {
            if(!node||(node->id>>28)!=motion_kind(key)) return lw_error(e,ls->v[i].offset,"motion","motion block has no matching item owner");
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
        } else if(lw_string_is(key,"MorphTarget")||lw_string_is(key,"DisplacementMap")||lw_string_is(key,"DisplacementMaps")) {
            s->unsupported_features++;
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
        for(j=0;j<n->clip_maps.n;j++) { LW_FREE(n->clip_maps.v[j].fields); LW_FREE(n->clip_maps.v[j].images); }
        LW_FREE(n->clip_maps);
        LW_FREE(n->rig_parameters);
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

/* Span tangents are expressed in the current span's normalized time, with
   neighboring key times correcting the Kochanek-Bartels tangent weights. */
static int spline_tangents(const LWChannel *c,size_t right,double *out,double *in) {
    const LWKey *a=&c->keys.v[right-1],*b=&c->keys.v[right];
    double delta=b->value-a->value,span=b->time-a->time;
    double t=a->parameters[0],continuity=a->parameters[1],bias=a->parameters[2];
    if(a->shape==0) {
        double before=(1-t)*(1+continuity)*(1+bias),after=(1-t)*(1-continuity)*(1-bias);
        *out=right>1?span/(b->time-c->keys.v[right-2].time)*(before*(a->value-c->keys.v[right-2].value)+after*delta):after*delta;
    } else if(a->shape==3) *out=right>1?span/(b->time-c->keys.v[right-2].time)*(b->value-c->keys.v[right-2].value):delta;
    else if(a->shape==4) *out=0;
    else return 0;
    t=b->parameters[0]; continuity=b->parameters[1]; bias=b->parameters[2];
    {
        double before=(1-t)*(1-continuity)*(1+bias),after=(1-t)*(1+continuity)*(1-bias);
        *in=right+1<c->keys.n?span/(c->keys.v[right+1].time-a->time)*(after*(c->keys.v[right+1].value-b->value)+before*delta):before*delta;
    }
    return isfinite(*out)&&isfinite(*in)&&isfinite(span)&&isfinite(delta);
}
/* Exact keys, constant, linear, stepped and TCB spans. Native keys/parameters
   stay intact in LWIR; unsupported shapes still refuse a scene snapshot. */
int lw_channel_value(const LWChannel *c,double time,double *value) {
    size_t i; const LWKey *a,*b;
    if(!c->keys.n) return 0;
    time-=c->offset; a=c->keys.v; b=&c->keys.v[c->keys.n-1];
    if(!isfinite(time)||c->opaque_modifiers) return 0;
    for(i=0;i<c->keys.n;i++) if(time==c->keys.v[i].time) { *value=c->keys.v[i].value; return 1; }
    if(time<a->time||time>b->time) {
        uint32_t behavior=time<a->time?c->pre:c->post;
        if(behavior==0) { *value=0; return 1; }
        if(behavior==1) { *value=time<a->time?a->value:b->value; return 1; }
        if(behavior==2&&c->keys.n==1) { *value=a->value; return 1; }
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
        if(b->shape==0) {
            double out,in,u=(time-a->time)/(b->time-a->time),u2=u*u,u3=u2*u;
            if(!spline_tangents(c,i,&out,&in)) return 0;
            *value=(2*u3-3*u2+1)*a->value+(u3-2*u2+u)*out+(-2*u3+3*u2)*b->value+(u3-u2)*in;
            return isfinite(*value);
        }
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
static int node_values(const LWScene *s,size_t i,double frame,double v[9],LWError *e,unsigned depth) {
    const LWNode *node=&s->nodes.v[i]; size_t j;
    double time=s->version==1?frame:frame/s->fps;
    if(depth>64) return lw_error(e,node->source_offset,"follower","cyclic or excessively deep follower chain");
    if(node->unsupported_transform) return lw_error(e,node->source_offset,"transform","node %08x: %s",node->id,node->transform_issue[0]?node->transform_issue:"unsupported pivot/IK/bone evaluation");
    for(j=0;j<9;j++) v[j]=j<6?0:1;
    for(j=0;j<node->channels.n;j++) {
        const LWChannel *c=&node->channels.v[j];
        if(c->index<9 && !lw_channel_value(c,time,&v[c->index])) return lw_error(e,node->source_offset,"animation","node %08x channel %u: unsupported envelope shape, behavior or modifier",node->id,c->index);
    }
    if(node->mirrored_bank_follower) {
        double source[9]; const LWNode *leader;
        for(j=0;j<s->nodes.n;j++) if(s->nodes.v[j].id==node->follower_source) break;
        if(j==s->nodes.n) return lw_error(e,node->source_offset,"follower","missing source item %08x",node->follower_source);
        leader=&s->nodes.v[j];
        if(leader->parent!=node->parent||memcmp(leader->pivot,node->pivot,sizeof node->pivot)) return lw_error(e,node->source_offset,"follower","mirrored-bank profile requires matching parents and pivots");
        LW_TRY(node_values(s,j,frame,source,e,depth+1));
        if(v[3]||v[4]||v[5]||source[3]||source[4]) return lw_error(e,node->source_offset,"follower","mirrored-bank profile requires bank-only source rotation and neutral follower rotation");
        v[5]=-source[5];
    }
    return 1;
}
int lw_scene_node_matrix(const LWScene *s,size_t i,double frame,double m[16],LWError *e) {
    const LWNode *node=&s->nodes.v[i]; size_t j; double v[9];
    LW_TRY(node_values(s,i,frame,v,e,0));
    if(s->version==1) for(j=3;j<6;j++) v[j]*=0.017453292519943295;
    local_matrix(m,v,node->pivot);
    for(j=0;j<16;j++) if(!isfinite(m[j])) return lw_error(e,node->source_offset,"transform","non-finite matrix");
    return 1;
}
int lw_scene_node_trs(const LWScene *s,size_t i,double frame,double trs[10],LWError *e) {
    double v[9],m[16],ch,sh,cp,sp,cb,sb; size_t j;
    LW_TRY(node_values(s,i,frame,v,e,0));
    if(s->version==1) for(j=3;j<6;j++) v[j]*=0.017453292519943295;
    local_matrix(m,v,s->nodes.v[i].pivot);
    /* qHeading * qPitch * qBank; keep native signed/zero scales. Translation
       includes the animated pivot offset, exactly as in the matrix evaluator. */
    ch=cos(v[3]/2); sh=sin(v[3]/2); cp=cos(v[4]/2); sp=sin(v[4]/2); cb=cos(v[5]/2); sb=sin(v[5]/2);
    for(j=0;j<3;j++) { trs[j]=m[12+j]; trs[7+j]=v[6+j]; }
    trs[3]=ch*sp*cb+sh*cp*sb; trs[4]=sh*cp*cb-ch*sp*sb;
    trs[5]=ch*cp*sb-sh*sp*cb; trs[6]=ch*cp*cb+sh*sp*sb;
    for(j=0;j<10;j++) if(!isfinite(trs[j])) return lw_error(e,0,"animation","non-finite TRS");
    return 1;
}
int lw_bone_rest_matrix(const LWNode *node,double m[16],LWError *e) {
    double v[9]={0,0,0,0,0,0,1,1,1},pivot[3]={0}; size_t j;
    int rotated=node->pivot_rotation[0]||node->pivot_rotation[1]||node->pivot_rotation[2];
    if((node->bone.present&3)!=3) return lw_error(e,node->source_offset,"bone","missing rest position or direction for %08x",node->id);
    if(node->pivot[0]||node->pivot[1]||node->pivot[2]) return lw_error(e,node->source_offset,"bone","translated bone pivot semantics are not qualified");
    /* Record Pivot Rotation records the orientation while zeroing the channels.
       With zero rest angles only that orientation remains; composing nonzero
       rest and pivot rotations needs an independently qualified convention. */
    if(rotated&&(node->bone.rest_rotation[0]||node->bone.rest_rotation[1]||node->bone.rest_rotation[2])) return lw_error(e,node->source_offset,"bone","combined rest and pivot rotation semantics are not qualified");
    for(j=0;j<3;j++) { v[j]=node->bone.rest_position[j]; v[3+j]=(rotated?node->pivot_rotation[j]:node->bone.rest_rotation[j])*0.017453292519943295; }
    local_matrix(m,v,pivot);
    for(j=0;j<16;j++) if(!isfinite(m[j])) return lw_error(e,node->source_offset,"bone","non-finite rest matrix");
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
