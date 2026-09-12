#ifndef LW_INTERNAL_H
#define LW_INTERNAL_H
#include "lwconvert.h"
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>

#define LW_ADD(a,x,e) (lw_grow((void **)&(a).v, &(a).cap, (a).n+1, sizeof(*(a).v), (e)) ? ((a).v[(a).n++]=(x),1) : 0)
#define LW_FREE(a) do { free((a).v); (a).v=NULL; (a).n=(a).cap=0; } while(0)
#define LW_TRY(x) do { if (!(x)) return 0; } while(0)
typedef struct { const unsigned char *data; size_t size, pos, base; LWError *error; } LWReader;
int lw_error(LWError *, size_t, const char *, const char *, ...);
int lw_grow(void **, size_t *, size_t, size_t, LWError *);
int lw_take(LWReader *, size_t, const unsigned char **);
int lw_u16(LWReader *, uint32_t *);
int lw_u32(LWReader *, uint32_t *);
int lw_float(LWReader *, float *);
int lw_vx(LWReader *, uint32_t *);
int lw_s0(LWReader *, LWString *);
int lw_chunk(LWReader *, int, uint32_t *, LWReader *, size_t *);
int lw_string_equal(LWString, LWString);
int lw_string_is(LWString, const char *);
LWString lw_string(const char *);
char *lw_text(LWString);
char *lw_dup(const char *);
void lw_json_string(FILE *, const char *);
void lw_json_bytes(FILE *, LWString);
void lw_json_name(FILE *, LWString);
void lw_tag_text(uint32_t, char [5]);
void lw_sha256(const unsigned char *, size_t, char [65]);
int lw_read_source(const char *, LWSource *, LWError *);
void lw_free_source(LWSource *);
FILE *lw_fopen(const char *, const char *);
char *lw_absolute(const char *);
char *lw_join(const char *, const char *);
char *lw_named_path(const char *, const char *, const char *);
char *lw_output_name(const char *);
char *lw_dirname(const char *);
const char *lw_basename(const char *);
int lw_path_exists(const char *);
int lw_mkdir(const char *, LWError *);
int lw_path_inside(const char *, const char *);
int lw_path_equal(const char *, const char *);
typedef LW_ARRAY(char *) LWPaths;
int lw_walk(const char *, LWPaths *, LWError *);
void lw_free_paths(LWPaths *);
typedef struct { char *prefix, *destination; } LWRule;
typedef struct {
    char *input, *output, *root, *uv_map;
    double frame;
    int frame_set;
    LW_ARRAY(LWRule) rules;
} LWOptions;
typedef struct {
    LW_ARRAY(LWObject) objects;
    LWScene scene;
    int is_scene;
    LWPaths files;
    LWPaths names;
    char *scene_name;
    size_t unresolved, approximation_count;
} LWPackage;
int lw_convert(const LWOptions *, LWError *);
void lw_free_package(LWPackage *);
int lw_write_object(const char *, const LWObject *, LWError *);
int lw_write_scene(const char *, const LWScene *, LWError *);
int lw_package_images(const char *, const char *, LWImageReference *, size_t, LWError *);
void lw_free_image(LWImageReference *);
void lw_json_images(FILE *, const LWImageReference *, size_t);
int lw_decode_raster(const LWSource *,LWImageReference *,LWError *);
int lw_save_png(const char *,const unsigned char *,int,int,char [65],LWError *);
int lw_encode_png(const unsigned char *,int,int,LWSource *,LWError *);
int lw_prepare_textures(const char *,const char *,LWObject *,const LWOptions *,LWError *);
void lw_json_textures(FILE *,const LWObject *,uint32_t);
typedef struct {
    size_t skipped, cages, control_curves, uv_missing;
    size_t triangulated_faces, triangles, bridged_faces, triangulation_failures, nonplanar_faces, removed_corners;
    int scene_written; char scene_issue[256];
} LWExportStats;
typedef struct {
    LW_ARRAY(uint32_t) corners;
    size_t bridges, removed_corners;
    int nonplanar;
    char issue[192];
} LWTriangulation;
int lw_triangulate(const LWObject *,const LWPrimitive *,LWTriangulation *,LWError *);
void lw_free_triangulation(LWTriangulation *);
int lw_write_obj(const char *, const LWPackage *, const LWOptions *, LWExportStats *, LWError *);
typedef struct { float u,v; unsigned char valid; } LWUV;
LWUV *lw_texture_uvs(const LWObject *,LWError *);
LWUV *lw_corner_uvs(const LWObject *,const char *,LWError *);
typedef struct {
    LWExportStats geometry;
    size_t files,points,lines,materials,animated_channels,omitted_nodes,unsupported_sidedness;
    size_t animation_channels,animation_samples;
    char animation_issue[256];
    struct LWRigExports *rigs;
} LWGltfStats;
int lw_write_gltf(const char *,const LWPackage *,const LWOptions *,LWGltfStats *,LWError *);
int lw_write_bytes(const char *, const void *, size_t, LWError *);
int lw_close(FILE *, const char *, LWError *);
void lw_identity(double [16]);
int lw_scene_matrices(const LWScene *, double, double *, size_t *, LWError *);
int lw_scene_node_matrix(const LWScene *,size_t,double,double [16],LWError *);
int lw_scene_node_trs(const LWScene *,size_t,double,double [10],LWError *);
int lw_bone_rest_matrix(const LWNode *,double [16],LWError *);

typedef struct { size_t source,parent; double local[16],world[16],inverse_bind[16]; } LWRigJoint;
typedef struct { uint16_t joint; float weight; } LWRigInfluence;
typedef struct { size_t first,count; } LWRigPoint;
typedef struct {
    size_t owner,asset;
    LW_ARRAY(LWRigJoint) joints;
    LW_ARRAY(LWRigInfluence) influences;
    LWRigPoint *points;
    size_t point_count,influence_sets,unweighted_points,missing_maps,procedural_bones;
    int weighted;
    char issue[256];
} LWRig;
typedef struct {
    size_t owner,bones,sets,unweighted_points,missing_maps,procedural_bones;
    char *name;
    int weighted,written;
    char issue[256];
} LWRigExport;
typedef struct LWRigExports { LWRigExport *v; size_t n,cap; } LWRigExports;
void lw_resolve_bone_maps(LWPackage *);
int lw_build_rig(const LWPackage *,size_t,LWRig *,LWError *);
void lw_free_rig(LWRig *);
void lw_free_gltf_stats(LWGltfStats *);
#endif
