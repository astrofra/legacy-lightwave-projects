#ifndef LWCONVERT_H
#define LWCONVERT_H

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

#define LWCONVERT_VERSION "0.5.0"
#define LW_NONE UINT32_MAX
#define LW_TAG(a,b,c,d) (((uint32_t)(a)<<24)|((uint32_t)(b)<<16)|((uint32_t)(c)<<8)|(uint32_t)(d))
#define LW_ARRAY(T) struct { T *v; size_t n, cap; }

/* Borrowed byte strings retain the original encoding and source lifetime. */
typedef struct { const unsigned char *data; size_t size; } LWString;
typedef struct { size_t offset; char context[48], message[256]; } LWError;
typedef struct { char *path; unsigned char *data; size_t size; char sha256[65]; } LWSource;
typedef struct { uint32_t tag; size_t offset, size; const char *status; } LWChunk;
typedef struct { uint32_t id, flags, parent; float pivot[3]; LWString name; } LWLayer;
typedef struct { uint32_t layer, first, count; } LWPointBlock;
typedef struct { uint32_t layer, point_block, first, count, type; } LWPolygonBlock;
typedef struct {
    uint32_t first, count, type, flags, block, material, tag_index, detail_parent;
    int32_t legacy_surface;
} LWPrimitive;
typedef struct { uint32_t point, polygon; } LWMapEntry;
typedef struct {
    uint32_t type, dimension, point_block, polygon_block;
    int discontinuous;
    LWString name;
    LW_ARRAY(LWMapEntry) entries;
    LW_ARRAY(float) values;
} LWMap;
typedef struct { uint32_t type, polygon, tag, block; } LWTagAssignment;
typedef struct {
    LWString type;
    uint32_t material, channel, flags, wrap[2];
    size_t image, offset, bytes;
    float size[3], center[3], falloff[3], velocity[3], value, amplitude, tiles[2];
    char issue[192];
    int supported;
} LWTexture;
typedef struct {
    LWString name, source;
    float color[3], diffuse, specular, luminosity, transparency, smoothing;
    uint32_t flags, side, present, float_fields;
    size_t projection_texture;
    char *base_texture, *opacity_texture, *emissive_texture, *specular_texture, *bump_texture;
    int textured, texture_alpha;
} LWMaterial;
typedef struct {
    LWString path;
    const char *role;
    size_t offset;
    uint32_t clip;
    char *resolved_path, *uri;
    LW_ARRAY(char *) candidates;
    char resolution[48], issue[256], sha256[65];
    char *png_uri;
    char png_sha256[65], decode_issue[192];
    unsigned char *rgba;
    int width, height;
} LWImageReference;
typedef struct LWObject {
    LWSource source;
    uint32_t format;
    size_t form_offset;
    LW_ARRAY(LWChunk) chunks;
    LW_ARRAY(LWLayer) layers;
    LW_ARRAY(LWPointBlock) point_blocks;
    LW_ARRAY(LWPolygonBlock) polygon_blocks;
    LW_ARRAY(float) positions;
    LW_ARRAY(uint32_t) indices;
    LW_ARRAY(LWPrimitive) primitives;
    LW_ARRAY(LWString) tags;
    LW_ARRAY(LWTagAssignment) assignments;
    LW_ARRAY(LWMaterial) materials;
    LW_ARRAY(LWMap) maps;
    LW_ARRAY(LWImageReference) images;
    LW_ARRAY(LWTexture) textures;
    size_t texture_blocks, legacy_textures, opaque_chunks, repeated_primitives;
    size_t invalid_map_references, missing_materials, non_finite_map_values;
} LWObject;

typedef struct { double time, value, parameters[6]; uint32_t shape; } LWKey;
typedef struct { uint32_t index, pre, post, declared_keys; size_t opaque_modifiers; double offset; LW_ARRAY(LWKey) keys; } LWChannel;
/* Ordered native parameter tree; numeric text and unknown fields stay lossless. */
typedef struct {
    LWString name, value;
    size_t parent, offset;
    int block;
} LWTextureField;
typedef struct {
    LWString declaration;
    size_t offset, size;
    LW_ARRAY(LWTextureField) fields;
    LW_ARRAY(size_t) images; /* Indices into the owning scene's images. */
} LWClipMap;

typedef struct {
    uint32_t owner, present;
    int active, weight_map_only, normalize, scale_strength, limited_range;
    double rest_position[3], rest_rotation[3], rest_length, strength, range[2];
    double joint_comp[2], muscle_flex[2];
    LWString weight_map;
    char weight_map_status[48];
} LWBone;

typedef struct {
    uint32_t id, parent, layer; /* Scene layer request is one-based; LW_NONE = whole object. */
    LWString name, object_path;
    size_t source_offset;
    double pivot[3], pivot_rotation[3];
    LW_ARRAY(LWChannel) channels;
    LW_ARRAY(LWClipMap) clip_maps;
    LW_ARRAY(LWTextureField) rig_parameters;
    LWBone bone;
    uint32_t bone_falloff;
    int faster_bones;
    LWString object_dissolve; /* Complete statement and optional envelope. */
    int unsupported_transform;
    size_t key_count_mismatches;
    char transform_issue[192];
    uint32_t follower_source;
    int mirrored_bank_follower;
    size_t follower_plugin;
    size_t asset;
    char *resolved_path;
    LW_ARRAY(char *) candidates;
    char resolution[48];
    char issue[256];
} LWNode;
typedef struct { size_t offset, size; LWString name; int interpreted; } LWPlugin;
typedef struct LWScene {
    LWSource source;
    unsigned version;
    double first_frame, last_frame, fps;
    LW_ARRAY(LWNode) nodes;
    LW_ARRAY(LWPlugin) plugins;
    LW_ARRAY(LWImageReference) images;
    size_t opaque_blocks, unsupported_features;
} LWScene;

int lw_load_object(const char *utf8_path, LWObject *object, LWError *error);
int lw_load_scene(const char *utf8_path, LWScene *scene, LWError *error);
void lw_free_object(LWObject *object);
void lw_free_scene(LWScene *scene);
int lw_parse_object(LWObject *object, LWError *error);
int lw_parse_scene(LWScene *scene, LWError *error);
int lw_channel_value(const LWChannel *channel, double time, double *value);
void lw_object_summary(FILE *stream, const LWObject *object);
void lw_scene_summary(FILE *stream, const LWScene *scene);

#endif
