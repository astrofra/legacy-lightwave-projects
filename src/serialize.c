#include "internal.h"

static void u32le(FILE *f,uint32_t v) {
    unsigned char p[4]={(unsigned char)v,(unsigned char)(v>>8),(unsigned char)(v>>16),(unsigned char)(v>>24)};
    fwrite(p,1,4,f);
}
static void f32le(FILE *f,float v) { uint32_t bits; memcpy(&bits,&v,4); u32le(f,bits); }
static void f64le(FILE *f,double v) {
    uint64_t bits; unsigned i; memcpy(&bits,&v,8);
    for(i=0;i<8;i++) fputc((int)((bits>>(8*i))&255),f);
}
static void index_json(FILE *f,uint32_t n) { if(n==LW_NONE) fputs("null",f); else fprintf(f,"%u",n); }
static void source_json(FILE *f,const LWSource *s) {
    fputs("{\"path\":",f); lw_json_string(f,s->path);
    fprintf(f,",\"sha256\":\"%s\",\"bytes\":%zu,\"uri\":\"source.bin\"}",s->sha256,s->size);
}
static void view(FILE *f,size_t offset,size_t count,size_t stride,const char *type,unsigned components) {
    fprintf(f,"{\"offset\":%zu,\"count\":%zu,\"stride\":%zu,\"component_type\":\"%s\",\"components\":%u}",offset,count,stride,type,components);
}
int lw_write_object(const char *dir,const LWObject *o,LWError *e) {
    char *path=lw_join(dir,"geometry.bin"); FILE *f; size_t i,j,offset,primitive_offset; char format[5];
    if(!path) return lw_error(e,0,"allocation","out of memory");
    f=lw_fopen(path,"wb"); if(!f) { free(path); return lw_error(e,0,"output","cannot create geometry buffer"); }
    for(i=0;i<o->positions.n;i++) f32le(f,o->positions.v[i]);
    for(i=0;i<o->indices.n;i++) u32le(f,o->indices.v[i]);
    primitive_offset=4*(o->positions.n+o->indices.n);
    for(i=0;i<o->primitives.n;i++) {
        const LWPrimitive *p=&o->primitives.v[i];
        uint32_t fields[9]={p->first,p->count,p->type,p->flags,p->block,p->material,p->tag_index,p->detail_parent,(uint32_t)p->legacy_surface};
        for(j=0;j<9;j++) u32le(f,fields[j]);
    }
    for(i=0;i<o->maps.n;i++) {
        const LWMap *m=&o->maps.v[i];
        for(j=0;j<m->entries.n;j++) { u32le(f,m->entries.v[j].point); u32le(f,m->entries.v[j].polygon); }
        for(j=0;j<m->values.n;j++) f32le(f,m->values.v[j]);
    }
    if(!lw_close(f,path,e)) { free(path); return 0; } free(path);
    path=lw_join(dir,"source.bin"); if(!path) return lw_error(e,0,"allocation","out of memory");
    if(!lw_write_bytes(path,o->source.data,o->source.size,e)) { free(path); return 0; } free(path);
    path=lw_join(dir,"object.json"); if(!path) return lw_error(e,0,"allocation","out of memory");
    f=lw_fopen(path,"wb"); if(!f) { free(path); return lw_error(e,0,"output","cannot create object manifest"); }
    lw_tag_text(o->format,format);
    fprintf(f,"{\n\"schema_version\":\"0.1\",\"kind\":\"object\",\"format\":\"%s\",\"form_offset\":%zu,\n\"source\":",format,o->form_offset); source_json(f,&o->source);
    fputs(",\n\"coordinates\":\"lightwave-left-handed-y-up\",\"units\":\"meters-by-convention\",\n\"buffer\":{\"uri\":\"geometry.bin\",\"byte_order\":\"little\"},\n\"positions\":",f);
    view(f,0,o->positions.n/3,12,"float32",3);
    fputs(",\n\"indices\":",f); view(f,o->positions.n*4,o->indices.n,4,"uint32",1);
    fputs(",\n\"primitives\":",f); view(f,primitive_offset,o->primitives.n,36,"uint32",9);
    fputs(",\n\"primitive_fields\":[\"first_index\",\"index_count\",\"fourcc_type\",\"flags\",\"polygon_block\",\"material\",\"tag_index\",\"detail_parent\",\"legacy_signed_surface_bits\"],\n\"null_index\":4294967295,\n\"layers\":[",f);
    for(i=0;i<o->layers.n;i++) {
        const LWLayer *l=&o->layers.v[i]; if(i) fputc(',',f);
        fprintf(f,"{\"id\":%u,\"flags\":%u,\"parent\":",l->id,l->flags); index_json(f,l->parent);
        fprintf(f,",\"pivot\":[%.9g,%.9g,%.9g],\"name\":",l->pivot[0],l->pivot[1],l->pivot[2]); lw_json_name(f,l->name); fputc('}',f);
    }
    fputs("],\n\"point_blocks\":[",f);
    for(i=0;i<o->point_blocks.n;i++) { LWPointBlock b=o->point_blocks.v[i]; if(i) fputc(',',f); fprintf(f,"{\"layer\":%u,\"first\":%u,\"count\":%u}",b.layer,b.first,b.count); }
    fputs("],\n\"polygon_blocks\":[",f);
    for(i=0;i<o->polygon_blocks.n;i++) {
        LWPolygonBlock b=o->polygon_blocks.v[i]; char type[5]; lw_tag_text(b.type,type); if(i) fputc(',',f);
        fprintf(f,"{\"layer\":%u,\"point_block\":%u,\"first\":%u,\"count\":%u,\"type\":",b.layer,b.point_block,b.first,b.count); lw_json_string(f,type); fputc('}',f);
    }
    fputs("],\n\"tags\":[",f);
    for(i=0;i<o->tags.n;i++) { if(i) fputc(',',f); lw_json_name(f,o->tags.v[i]); }
    fputs("],\n\"tag_assignments\":[",f);
    for(i=0;i<o->assignments.n;i++) {
        LWTagAssignment a=o->assignments.v[i]; char type[5]; if(i) fputc(',',f); lw_tag_text(a.type,type);
        fprintf(f,"{\"type\":%u,\"type_name\":",a.type); lw_json_string(f,type);
        fprintf(f,",\"polygon_block\":%u,\"polygon\":%u,\"tag\":%u}",a.block,a.polygon,a.tag);
    }
    fputs("],\n\"materials\":[",f);
    for(i=0;i<o->materials.n;i++) {
        const LWMaterial *m=&o->materials.v[i]; if(i) fputc(',',f);
        fputs("{\"name\":",f); lw_json_name(f,m->name); fputs(",\"source_name\":",f); lw_json_name(f,m->source);
        fprintf(f,",\"color\":[%.9g,%.9g,%.9g],\"diffuse\":%.9g,\"specular\":%.9g,\"luminosity\":%.9g,\"transparency\":%.9g,\"smoothing_angle\":%.9g,\"flags\":%u,\"side\":%u,\"present_fields\":%u,\"float_fields\":%u,\"textures\":",m->color[0],m->color[1],m->color[2],m->diffuse,m->specular,m->luminosity,m->transparency,m->smoothing,m->flags,m->side,m->present,m->float_fields);
        lw_json_textures(f,o,(uint32_t)i);
        {
            const char *origin; int issue=0; float angle=lw_smoothing_angle(o,(uint32_t)i,&origin,&issue);
            fprintf(f,",\"smoothing\":{\"angle_unit\":\"radians\",\"angle_present\":%s,\"enabled\":%s,\"effective_angle\":%.9g,\"origin\":\"%s\",\"inheritance_issue\":%s}",m->present&32?"true":"false",angle>0?"true":"false",angle,origin,issue?"true":"false");
        }
        fputs(",\"derived_maps\":{",f);
        {
            const char *keys[]={"base_color","opacity","emissive","specular","bump"};
            const char *uris[]={m->base_texture,m->opacity_texture,m->emissive_texture,m->specular_texture,m->bump_texture};
            int comma=0;
            for(j=0;j<5;j++) if(uris[j]) { if(comma) fputc(',',f); comma=1; lw_json_string(f,keys[j]); fputc(':',f); lw_json_string(f,uris[j]); }
        }
        fputs("}}",f);
    }
    offset=primitive_offset+36*o->primitives.n; fputs("],\n\"maps\":[",f);
    for(i=0;i<o->maps.n;i++) {
        const LWMap *m=&o->maps.v[i]; char type[5]; lw_tag_text(m->type,type); if(i) fputc(',',f);
        fputs("{\"type\":",f); lw_json_string(f,type); fputs(",\"name\":",f); lw_json_name(f,m->name);
        fprintf(f,",\"dimension\":%u,\"discontinuous\":%s,\"point_block\":%u,\"polygon_block\":",m->dimension,m->discontinuous?"true":"false",m->point_block); index_json(f,m->polygon_block);
        fputs(",\"entries\":",f); view(f,offset,m->entries.n,8,"uint32",2); offset+=8*m->entries.n;
        fputs(",\"values\":",f); view(f,offset,m->entries.n,4*m->dimension,"float32",m->dimension); offset+=4*m->values.n; fputc('}',f);
    }
    fputs("],\n\"image_references\":",f); lw_json_images(f,o->images.v,o->images.n);
    fputs(",\n\"chunks\":[",f);
    for(i=0;i<o->chunks.n;i++) {
        LWChunk c=o->chunks.v[i]; char tag[5]; lw_tag_text(c.tag,tag); if(i) fputc(',',f);
        fputs("{\"tag\":",f); lw_json_string(f,tag); fprintf(f,",\"offset\":%zu,\"payload_bytes\":%zu,\"status\":\"%s\"}",c.offset,c.size,c.status);
    }
    fputc(']',f);
    {
        LWNormals normals={0};
        if(!lw_corner_normals(o,&normals,e)) { fclose(f); free(path); return 0; }
        fprintf(f,",\n\"shading\":{\"profile\":\"source-corner-normals-0.1\",\"normal_maps\":\"NORM vectors retained in maps/geometry.bin; unique name per point block; VMAD overrides VMAP; normalized only in derivatives\",\"generated_normals\":\"equal-weight unit polygon normals sharing source points; owner surface angle; neighbours must enable smoothing; SMGP boundaries; no coordinate welding\",\"unassigned_smoothing_group\":\"separate implicit group\",\"explicit_corners\":%zu,\"smoothed_corners\":%zu,\"issues\":%zu,\"polygon_normal_fallbacks\":%zu}",normals.explicit_corners,normals.smoothed_corners,normals.issues,normals.polygon_fallbacks);
        lw_free_normals(&normals);
    }
    fprintf(f,",\n\"buffer_bytes\":%zu,\"invalid_map_references\":%zu,\"missing_materials\":%zu,\"non_finite_map_values\":%zu,\"material_scope\":\"scalar subset and LWOB texture bindings; native projections and source SURF/CLIP bytes retained; derived PNG maps use a repeat sampler and approximate scalar interpolation; height bump exported to OBJ only\"\n}\n",offset,o->invalid_map_references,o->missing_materials,o->non_finite_map_values);
    { int ok=lw_close(f,path,e); free(path); return ok; }
}
static void clip_maps_json(FILE *f,const LWNode *node) {
    size_t i,j; fputc('[',f);
    for(i=0;i<node->clip_maps.n;i++) {
        const LWClipMap *clip=&node->clip_maps.v[i];
        if(i) fputc(',',f);
        fputs("{\"scope\":\"object-instance\",\"coverage\":{\"mode\":\"binary-cutout\",\"cutoff\":null,\"polarity\":\"not-evaluated\"},\"status\":\"preserved-not-evaluated\",\"declaration\":",f);
        lw_json_name(f,clip->declaration);
        fprintf(f,",\"native_source\":{\"uri\":\"source.bin\",\"offset\":%zu,\"bytes\":%zu},\"image_references\":[",clip->offset,clip->size);
        for(j=0;j<clip->images.n;j++) { if(j) fputc(',',f); fprintf(f,"%zu",clip->images.v[j]); }
        fputs("],\"parameters\":[",f);
        for(j=0;j<clip->fields.n;j++) {
            const LWTextureField *field=&clip->fields.v[j];
            if(j) fputc(',',f);
            fputs("{\"parent\":",f); if(field->parent==SIZE_MAX) fputs("null",f); else fprintf(f,"%zu",field->parent);
            fprintf(f,",\"block\":%s,\"source_offset\":%zu,\"name\":",field->block?"true":"false",field->offset);
            if(field->name.size) lw_json_name(f,field->name); else fputs("null",f);
            fputs(",\"value\":",f); lw_json_name(f,field->value); fputc('}',f);
        }
        fputs("]}",f);
    }
    fputc(']',f);
}
int lw_write_scene(const char *dir,const LWScene *s,LWError *e) {
    char *path=lw_join(dir,"animation.bin"); FILE *f; size_t i,j,k,offset=0;
    if(!path) return lw_error(e,0,"allocation","out of memory");
    f=lw_fopen(path,"wb"); if(!f) { free(path); return lw_error(e,0,"output","cannot create animation buffer"); }
    for(i=0;i<s->nodes.n;i++) for(j=0;j<s->nodes.v[i].channels.n;j++) {
        const LWChannel *c=&s->nodes.v[i].channels.v[j];
        for(k=0;k<c->keys.n;k++) {
            unsigned x; const LWKey *key=&c->keys.v[k];
            f64le(f,key->time); f64le(f,key->value); for(x=0;x<6;x++) f64le(f,key->parameters[x]); u32le(f,key->shape); u32le(f,0);
        }
    }
    if(!lw_close(f,path,e)) { free(path); return 0; } free(path);
    path=lw_join(dir,"source.bin"); if(!path) return lw_error(e,0,"allocation","out of memory");
    if(!lw_write_bytes(path,s->source.data,s->source.size,e)) { free(path); return 0; } free(path);
    path=lw_join(dir,"scene.json"); if(!path) return lw_error(e,0,"allocation","out of memory");
    f=lw_fopen(path,"wb"); if(!f) { free(path); return lw_error(e,0,"output","cannot create scene manifest"); }
    fprintf(f,"{\n\"schema_version\":\"0.1\",\"version\":%u,\"source\":",s->version); source_json(f,&s->source);
    fprintf(f,",\n\"first_frame\":%.17g,\"last_frame\":%.17g,\"fps\":%.17g,\"time_domain\":\"%s\",\"angle_units\":\"%s\",\n\"animation_buffer\":{\"uri\":\"animation.bin\",\"byte_order\":\"little\",\"key_stride\":72,\"key_fields\":\"float64 time,value,parameters[6]; uint32 shape,reserved\"},\n\"nodes\":[",s->first_frame,s->last_frame,s->fps,s->version==1?"frame":"second",s->version==1?"degrees":"radians");
    for(i=0;i<s->nodes.n;i++) {
        const LWNode *n=&s->nodes.v[i]; if(i) fputc(',',f);
        fprintf(f,"{\"id\":%u,\"parent\":",n->id); index_json(f,n->parent); fputs(",\"layer_request\":",f); index_json(f,n->layer);
        fputs(",\"name\":",f); lw_json_name(f,n->name); fputs(",\"object_path\":",f); lw_json_name(f,n->object_path);
        fprintf(f,",\"source_offset\":%zu,\"pivot\":[%.17g,%.17g,%.17g],\"pivot_rotation\":[%.17g,%.17g,%.17g],\"unsupported_transform\":%s,\"asset_index\":",n->source_offset,n->pivot[0],n->pivot[1],n->pivot[2],n->pivot_rotation[0],n->pivot_rotation[1],n->pivot_rotation[2],n->unsupported_transform?"true":"false");
        if(n->asset==SIZE_MAX) fputs("null",f); else fprintf(f,"%zu",n->asset);
        fputs(",\"resolved_path\":",f); if(n->resolved_path) lw_json_string(f,n->resolved_path); else fputs("null",f);
        fputs(",\"resolution\":",f); lw_json_string(f,n->resolution); fputs(",\"issue\":",f); lw_json_string(f,n->issue);
        fprintf(f,",\"key_count_mismatches\":%zu,\"transform_issue\":",n->key_count_mismatches); lw_json_string(f,n->transform_issue);
        fputs(",\"follower\":",f);
        if(n->mirrored_bank_follower) fprintf(f,"{\"source_item\":%u,\"plugin_index\":%zu,\"channel\":5,\"scale\":-1,\"add\":0,\"profile\":\"mirrored-bank-preview\",\"interpretation\":\"same-parent bank-only source; opposite bank at snapshot time; qualified legacy payload, not a general Follower evaluator\"}",n->follower_source,n->follower_plugin);
        else fputs("null",f);
        fputs(",\"bone\":",f);
        if(n->bone.owner!=LW_NONE) {
            const LWBone *b=&n->bone;
            fprintf(f,"{\"owner_item\":%u,\"active\":%s,\"rest_fields_present\":%u,\"rest_position\":[%.17g,%.17g,%.17g],\"rest_rotation_hpb_degrees\":[%.17g,%.17g,%.17g],\"rest_length\":%.17g,\"weight_map\":",b->owner,b->active?"true":"false",b->present,b->rest_position[0],b->rest_position[1],b->rest_position[2],b->rest_rotation[0],b->rest_rotation[1],b->rest_rotation[2],b->rest_length);
            lw_json_name(f,b->weight_map);
            fprintf(f,",\"weight_map_only\":%s,\"normalize\":%s,\"strength\":%.17g,\"scale_strength_by_length\":%s,\"limited_range\":%s,\"range\":[%.17g,%.17g],\"joint_compensation\":[%.17g,%.17g],\"muscle_flex\":[%.17g,%.17g],\"weight_map_status\":",b->weight_map_only?"true":"false",b->normalize?"true":"false",b->strength,b->scale_strength?"true":"false",b->limited_range?"true":"false",b->range[0],b->range[1],b->joint_comp[0],b->joint_comp[1],b->muscle_flex[0],b->muscle_flex[1]);
            lw_json_string(f,b->weight_map_status); fputc('}',f);
        } else fputs("null",f);
        fputs(",\"rig_parameters\":[",f);
        for(j=0;j<n->rig_parameters.n;j++) {
            const LWTextureField *field=&n->rig_parameters.v[j]; if(j) fputc(',',f);
            fputs("{\"name\":",f); lw_json_name(f,field->name); fputs(",\"value\":",f); lw_json_name(f,field->value);
            fprintf(f,",\"source_offset\":%zu}",field->offset);
        }
        fputc(']',f);
        fputs(",\"candidates\":[",f); for(j=0;j<n->candidates.n;j++) { if(j) fputc(',',f); lw_json_string(f,n->candidates.v[j]); }
        fputs("],\"clip_maps\":",f); clip_maps_json(f,n);
        fputs(",\"object_dissolve\":",f);
        if(n->object_dissolve.size) {
            fputs("{\"status\":\"preserved-not-evaluated\",\"native_statement\":",f); lw_json_name(f,n->object_dissolve);
            fprintf(f,",\"source_offset\":%zu}",(size_t)(n->object_dissolve.data-s->source.data));
        } else fputs("null",f);
        fputs(",\"channels\":[",f);
        for(j=0;j<n->channels.n;j++) {
            const LWChannel *c=&n->channels.v[j]; if(j) fputc(',',f);
            fprintf(f,"{\"index\":%u,\"pre\":%u,\"post\":%u,\"declared_keys\":%u,\"opaque_modifiers\":%zu,\"time_offset\":%.17g,\"keys\":",c->index,c->pre,c->post,c->declared_keys,c->opaque_modifiers,c->offset);
            view(f,offset,c->keys.n,72,"mixed",1); offset+=72*c->keys.n; fputc('}',f);
        }
        fputs("]}",f);
    }
    fputs("],\n\"image_references\":",f); lw_json_images(f,s->images.v,s->images.n);
    fputs(",\n\"plugins\":[",f);
    for(i=0;i<s->plugins.n;i++) {
        const LWPlugin *plugin=&s->plugins.v[i]; if(i) fputc(',',f); fputs("{\"name\":",f); lw_json_name(f,plugin->name);
        fprintf(f,",\"offset\":%zu,\"bytes\":%zu,\"status\":\"%s\"}",plugin->offset,plugin->size,plugin->interpreted?"mirrored-bank-preview":"preserved-opaque");
    }
    fprintf(f,"],\n\"animation_bytes\":%zu,\"opaque_blocks\":%zu,\"unsupported_features\":%zu,\"unparsed_fields\":\"retained verbatim in source.bin, including optics, lights, scalar envelopes and deformation settings\"\n}\n",offset,s->opaque_blocks,s->unsupported_features);
    { int ok=lw_close(f,path,e); free(path); return ok; }
}
