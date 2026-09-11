# LWS/LWO converter in C — v0.4.1

Status as of 11 September 2026. This milestone provides a C17 library and
the `lwconvert` executable, with no Blender dependency. It extracts native
structures and produces OBJ/MTL and glTF 2.0 files. The `.blend` backend remains
to be implemented.

## Commands

Windows build instructions and getting-started examples are in the
[README](../README.md). MSVC 19.41 x64 was used for Debug, Release and
RelWithDebInfo with AddressSanitizer. A POSIX implementation exists, but its
build and behavior have not yet been validated on Linux/macOS.

```text
lwconvert inspect INPUT
lwconvert convert INPUT --output NEW_DIRECTORY
    [--content-root DIRECTORY]
    [--map PREFIX=DIRECTORY]...
    [--frame NUMBER]
    [--uv-map NAME]
```

`inspect` detects the format by its signature, regardless of the file extension,
and writes a JSON summary to stdout. `convert` creates a new output directory
outside the content root; its parent directory must exist. Source files are
opened read-only. The manifest is written last; a write error may leave an
incomplete output without a manifest. Remove or inspect that output before
retrying with another directory.

The content root defaults to the input's directory. For a scene, selecting the
project root helps avoid collisions between objects with the same name.
`--frame` accepts fractional frames and defaults to `FirstFrame`.
`--uv-map` explicitly selects a native TXUV map and disables automatic material
projection bindings. Without this option, compatible LWOB planar/spherical image
maps generate their own UVs; LWO2 TXUV map selection remains explicit.

Exit codes:

| Code | Meaning |
|---|---|
| 0 | Successful read or export of the supported subset |
| 1 | Argument, read, structural or write error; diagnostic includes an offset |
| 2 | Package produced with missing, omitted or approximated elements; see the manifest |

Exit code 0 does not guarantee LightWave visual fidelity. MTL materials remain
a rendering approximation, and the scope is recorded in every package.

## Reading and preservation

| Input | Data interpreted in this milestone |
|---|---|
| LWOB | PNTS, SRFS, POLS with signed surface numbers and detail polygons, PCHS, a SURF subset and image references |
| LWO2 | LAYR, PNTS/POLS blocks, TAGS/PTAG, VMAP/VMAD of any dimension and type, a SURF/CLIP subset |
| PST_ | Preserved preset wrapper and LWO2/LWOB object nested in PDAT |
| LWSC 1/3 | Objects, nulls, light/camera/bone IDs, parents, pivots, motion channels and keys, plugin blocks |

IFF bounds, sizes, point indices and geometry floating-point values are checked.
Invalid map/assignment indices are preserved and counted. Non-finite map values
remain in the binary data and are reported; they are not exported as UVs.
The input limit is 512 MiB per file. Arrays use indices limited to 32 bits, with
recursion bounds for nested chunks, directories and the scene hierarchy.

Each parsed input is copied in full to `source.bin`, with its SHA-256 hash.
Uninterpreted fields therefore remain recoverable, including projections,
plugins, scalar envelopes, render settings and unknown chunks. Image references
are extracted and resolved within each source's directory tree. Selected images
are copied verbatim into its IR directory. Supported raster images are decoded,
with lossless PNG derivatives for ILBM; compatible LWOB image maps are bound to
exported materials. Missing or unreadable dependencies are reported and are not
included in the package.

Strings retain their original bytes in `raw_hex`. The `text` field uses UTF-8
when valid, otherwise an explicitly named Latin-1 hypothesis. Successful path
resolution using that hypothesis does not prove the original encoding.
On Windows, filesystem paths use Unicode APIs.

## Scene object resolution

Resolution follows this order:

1. Explicit `--map` rules, with priority given to the longest matching prefix.
2. An exact path under the content root, for relative references.
3. Matching by path-component suffix, then basename alone, among files with
   LWOB/LWO2 signatures under that root.

For example, `--map "Y:meshes/=C:/archives/projet/meshes"` replaces a historical
prefix. Historical drive letters do not directly trigger reads from the current
drive. Directory links are not traversed during discovery. If an explicit
mapping points to a missing object, no implicit fallback is attempted.

A tie between multiple best candidates remains `ambiguous`, even when their
contents are identical. A unique suffix or basename candidate is selected with
a status identifying the heuristic. Candidate paths and the selection are
recorded in `scene.json`. Windows comparisons ignore Unicode case; the POSIX
implementation folds ASCII case only.

`LoadObjectLayer n` is checked against LAYR ID `n-1`, following the observed files;
the original number is preserved. Physical chunk order does not determine the
layer number. A missing layer or duplicate ID leaves the instance unresolved.
Resolved objects are deduplicated by path, then by SHA-256.

## Image reference resolution

Object `STIL` (LWO2), `TIMG`/`RIMG` (LWOB), and scene multiline `{ Still ... }`
references are resolved automatically, as are legacy `TextureImage` references
inside `ClipMap` declarations. Scene references are extracted even from nested
opaque texture blocks; plugin payloads remain opaque. Other scene image syntaxes
and image sequences are not interpreted by this resolver.

The search is limited to the directory containing the owning source and its
descendants. An object's images use that object's directory, including when the
object is loaded from a scene. Parent/sibling directories and filesystem links
are excluded. Historical drives and `--map` object rules do not expand this scope.

Resolution prefers a source-relative path, then the exact filename (including
extension), then an identical filename stem with an alternative image extension.
The last fallback requires both extensions to be in the image allowlist: JPEG,
PNG, TGA, TIFF, BMP, GIF, PSD, IFF/ILBM/LBM, PIC/PICT, SGI, HDR, EXR, WebP, DDS,
PCX and Netpbm filename variants (the exact list is in `src/images.c`). Comparisons
use the same case rules as object resolution and accept mixed historical path
separators. There is no fuzzy spelling or numeric-suffix substitution.

Within each tier, the longest matching suffix of path components wins (ignoring
the final extension in the alternative-format tier). Equal path matches are
ranked by the archive's format preference: PSD, TGA, PNG, JPEG, JPG, GIF, TIFF,
then other allowed formats. JPE shares JPEG's rank; TIF shares TIFF's rank.
Only a unique best candidate is selected; candidates with the same path score
and format priority remain `ambiguous`. An ambiguity in the original-format
tier does not fall back to another format. These preferences do not inspect a
file's actual compression. Filename extensions identify candidates; no image decoding,
content equivalence, alpha preservation or target renderer compatibility is inferred.

Each `image_references` entry in `object.json` or `scene.json` retains the original
path and source offset, and adds `resolution`, `candidates`, `resolved_path`,
`uri`, `sha256` and `issue`. Copied files use local `textures/<index>-<filename>`
paths, preserved by batch publication. Repeated references to one file within a
document share one copy. Manifests count packaged, unresolved and decoded
references. `decoded_image` reports dimensions and optional `png_uri`/`png_sha256`;
`decode_issue` explains unsupported or malformed images. `status: decoded` means
raster decoding succeeded, independently of whether the image is used in an
exported material. `images_not_exported` counts references without a supported
LWOB binding. Native material `textures` and `derived_maps` describe the
[texture export profile](textures.md).

For example, `I:fra/3D/posts/Aliens@Newtek/signe.psd` in `aliens@newtek/01.lws`
resolves to the local `signe.jpg`. This reference belongs to a `ClipMaps` block:
the JPEG is preserved and linked in IR, while evaluating that clip mask remains
outside the current static-geometry export profile. Original LWS/LWO bytes and
their archived `source.bin` copies are unchanged.

## Clip maps and target interpretation

Scene nodes now expose `clip_maps`, bound to the **object instance**, including
when several nodes share one deduplicated object asset. Both `ClipMaps` followed
by a `TextureBlock` and legacy `ClipMap` followed by `Texture...` statements are
preserved. Image references used there have `role: "clip-map"`; their indices in
the scene's `image_references` array are listed on the corresponding clip map.
Procedural maps without images are retained too.

Each map records `coverage.mode: "binary-cutout"`, its native declaration, an
exact `source.bin` byte range and an ordered `parameters` tree. Each parameter
has a `parent` index (null at the root), `block`, `name`, `value` and source offset.
Values retain their original text/bytes. This preserves layer boundaries,
Enable/Negative, projection/axis, coordinate transforms, wrapping, opacity
envelopes and unknown settings without flattening repeated parameters.
The normalized `coverage.cutoff` stays null and `polarity` stays `not-evaluated`:
neither a source threshold nor black/white polarity is inferred from a filename,
legacy `TextureValue`, or glTF's default. Original native values remain available.

`object_dissolve` is retained independently as its complete native statement and
optional envelope. For example, `aliens@newtek/01.lws` has both a clip map and
`ObjectDissolve 0.5` on the same instance. The two effects must not be collapsed
into one assumed alpha-test setting.

The target mapping is documented in each manifest's `clip_map_targets`:

| Target | Representation after texture evaluation | Limit |
| --- | --- | --- |
| OBJ/MTL | Bake a grayscale opacity image and bind it with `map_d`; combine with scalar opacity as appropriate. | MTL defines dissolve mapping, but no portable binary cutoff/depth-write setting. A binary image alone cannot enforce the importing renderer's alpha-test mode. |
| glTF 2.0 | Bake coverage into `baseColorTexture` alpha and use `alphaMode: "MASK"` with an explicit `alphaCutoff`. | No extension is required. The core material has no separate opacity texture; masking and partial transparency require an explicit policy when both are present. |
| Blender | Deferred. | Native clip-map semantics and parameters remain available in IR. |

For glTF, `MASK` discards pixels below the threshold and writes depth for retained
pixels; it avoids the mesh sorting needed by typical blended transparency.
The alpha input belongs to the base color, so a grayscale JPEG cannot simply be
bound as an independent mask: evaluate its native sampling, projection, polarity
and layer stack, then produce an alpha-bearing image such as PNG (and bake UVs
where required). If coverage has already been baked to alpha values 0/1, a target
cutoff of 0.5 separates them; that is an export choice, not a recovered LightWave
threshold. An instance-specific clip map can also require material/mesh variants
instead of reusing an unmodified material shared by several instances.

This change preserves the semantics and records target capabilities. Texture
evaluation, baking and rendered OBJ/glTF mask bindings remain unimplemented;
`scene_clip_maps_not_evaluated` reports their count and keeps such conversions
partial. Exporters do not emit a misleading `MASK` or `map_d` without an evaluated
texture and its mapping.

References: [LightWave object clip mapping](https://docs.lightwave3d.com/lw2020/reference/layout/object-properties/render-tab.html),
[glTF 2.0 alpha coverage](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#alpha-coverage),
and the [original Alias/Wavefront MTL specification, archived by Paul Bourke](https://www.paulbourke.net/dataformats/mtl/).

## Output layout 0.2 and LWIR 0.1

A direct `lwconvert convert` call creates the following layout. The original
filename, including its extension when present, identifies each output:

```text
manifest.json
obj/
    Tour_Toit.lwo.obj
    Tour_Toit.lwo.mtl
    Lacustre.lws.obj       # LWS snapshot when transforms can be evaluated
    Lacustre.lws.mtl
IR/Tour_Toit.lwo/
    source.bin
    object.json
    geometry.bin
IR/Lacustre.lws/           # LWS input only
    source.bin
    scene.json
    animation.bin
gltf/
    Tour_Toit.lwo.gltf
    Tour_Toit.lwo.bin
    Lacustre.lws.gltf      # Static geometry scene with instances and parents
    Lacustre.lws.bin
blender/                  # Reserved; backend not implemented yet
```

The batch combines these files under `packages/<project>/`, reusing an object's
IR, OBJ/MTL and glTF/binary files when the same source path and hash recur in that project.
It puts each input's conversion manifest in `IR/<source name>/manifest.json` and
writes an index at the project root. Asset indices remain local to each
conversion, so a scene's node indices refer to that conversion's asset list.
The batch rewrites manifest URIs, OBJ `mtllib` references and glTF buffer URIs
when publishing files. Copy the whole project directory to retain the shared
IR dependencies. Each glTF scene embeds its required geometry in its own `.bin`
and can be copied independently with that binary file.

Names preserve accents and the source extension. ASCII whitespace, control
characters, `#` and Windows-invalid filename characters become `_`, trailing
dots are removed, and Windows device names receive a leading `_`. These rules
keep each OBJ material-library filename a single token. Duplicate output names
receive numeric suffixes (`mesh.lwo-2.obj`, etc.), with case-insensitive collision
checks. Natural names are reserved before suffix allocation, so a source already
named `mesh.lwo-2` keeps that name. The batch allocates names in sorted source
path order; direct scene conversion follows the collected asset order. A later
mapped dependency is allocated when first encountered. Names are deterministic
for the same inputs, but may change if the set of colliding inputs changes.

SHA-256 remains the source identity in metadata and is not used as a directory
name. Native object and scene JSON/binary schemas remain `0.1`; the manifest's
`layout_version` is `0.2`. Readers must follow URIs rather than assume the old
`assets/<sha256>/` or `scene.obj` paths. Asset entries now include `name` and
`mtl`, and scenes include `scene_mtl`. Since v0.3.0, assets additionally include
`gltf` and `gltf_bin`; scenes include `scene_gltf`, `scene_gltf_bin` and
`scene_gltf_issue`. Missing scene exports use `null` with an explicit issue.
A `formats` map marks `gltf` as `generated` and `blender` as `not-implemented`.
Only the Blender directory remains an empty placeholder.
Batch reports use schema version `0.2` and link directly to each conversion's
manifest and primary OBJ/glTF when available.

Internal URIs are relative to the JSON file containing them. Original absolute
paths record provenance. These schemas may still change; they are not yet a
stable archival contract.

`geometry.bin` uses explicit little-endian byte order rather than serializing
the memory layout of C structs. Each JSON view specifies `offset`, `count`,
`stride`, `component_type` and `components`.

| View | Record |
|---|---|
| `positions` | 3 float32 values, 12 bytes; unchanged LightWave coordinates |
| `indices` | 1 uint32, 4 bytes; global indices into `positions` |
| `primitives` | 9 uint32 values, 36 bytes; fields named in `primitive_fields` |
| Map `entries` | 2 uint32 values, 8 bytes: local point, local polygon |
| Map `values` | `dimension` float32 values per entry, including dimension zero |

The nine primitive fields are the first index, index count, type FourCC, flags,
POLS block, material, tag, detail parent and bits of the signed LWOB surface
number. `4294967295` denotes an absent index. Read the last field as an int32 to
recover its sign. Object JSON preserves PNTS/POLS blocks and their layers, PTAG
assignments, names and map descriptions. A block's `layer` reference is an index
into the `layers` array, whose `id` field preserves the native number.

VMAP/VMAD indices are local to their designated native blocks. A continuous VMAP
uses the absent index for its polygon. VMAD thus retains differing values at
seams without collapsing corners onto vertices.

`animation.bin` contains 72-byte keys: eight float64 values (`time`, `value`,
six parameters), followed by two uint32 values (`shape`, reserved as zero).
Times are in frames for LWSC 1 and seconds for LWSC 3; rotations are in degrees
and radians respectively. Pre/post behaviors, time offsets, declared key counts
and opaque modifiers remain in the JSON channels.

## OBJ and initial evaluation

OBJ exports now triangulate ordinary FACE polygons in C, including concave
boundaries and holes connected by paired reverse edges. LWIR keeps the original
n-gons and source corners. This avoids relying on a viewer's triangle-fan
interpretation, which can fill concave cutouts. Z is reflected to use the chosen
right-handed Y-up coordinate system, and corner order follows the sign of the
total determinant. Simple points and lines are exported; points unused by any
exported primitive receive a `p` record.

PTCH/PCHS are exported as control cages and CURV as control polylines, with
approximation counters. Bones, unknown primitive types and detail polygons are
omitted from OBJ and preserved in LWIR. Repeated FACE indices are interpreted
by the triangulator instead of being rejected outright. Legacy CRVS remain
opaque. LightWave normals, smoothing and subdivision are not yet evaluated.

The triangulator normalizes and projects each boundary onto its dominant plane,
clips valid ears, and checks the resulting signed area. It returns source corner
indices so UV seams and material bindings follow the derivative triangles.
`# source_primitive` comments identify the original polygon in each OBJ.
Nonplanar faces use a projected interpretation and are reported as approximations.
Zero-area, intersecting, unsupported or over-limit contours are reported and
omitted from OBJ; they remain intact in LWIR. The current limit is 4,096 corners
per FACE polygon. Adjacent copies of the same source point may be removed from
the derivative, with an explicit counter; distinct point IDs are not welded.

The manifest records `obj_triangulated_faces` (source faces with more than three
corners), `obj_triangles`, `obj_bridged_hole_faces`, `obj_triangulation_failures`,
`obj_nonplanar_faces` and `obj_removed_duplicate_corners`. As with other OBJ
counters, these cover all individual and scene exports in the package.
See the [van triangulation diagnosis](van-triangulation.md) for the motivating
case and its validation results.

With `--uv-map`, VMAD takes precedence over VMAP. A face with corners lacking
valid values is exported without UV indices; no zeros are invented to complete
the map. Software reimporting that OBJ may nevertheless create its own default
UVs. MTL files contain approximations of diffuse color, specular response,
emission and transparency. Compatible LWOB image maps add `map_Kd`, `map_Ks`,
`map_Ke`, `map_d` and `bump` with generated UVs and PNG resources; see
[textures](textures.md).

For scene snapshots (`obj/<scene filename>.obj`), the current local matrix is
`T(position) × Ry(heading) × Rx(pitch) × Rz(bank) × S × T(-pivot)`;
it is composed with parent transforms before coordinate-system conversion.
Tests check translation, heading rotation, pivots, parents and negative scale.
Nontrivial LWO2 layer pivots/parents block this snapshot until their interactions
with Layout are qualified.

The evaluator supports exact keys, linear, stepped and TCB interpolation, and
reset, constant and repeat behaviors. TCB evaluation uses tension, continuity,
bias and neighboring key times. A repeated single-key envelope is constant.
Hermite/Bezier parameters remain preserved without span evaluation. An incorrect
declared key count produces a warning and a partial package; actual validated,
strictly ordered keys can still be sampled. The declared count and source bytes
remain unchanged in the IR.

Transforms driven by channel modifiers, rotated pivots, bones, IK and general
motion plugins block the snapshot. There is one bounded `LW_Follower` preview
profile: mirror a sibling's bank at the same time, with no other source rotation
or follower rotation and matching parents/pivots. Only the exact zero-delay,
zero-randomization, bank-only legacy payload found in `butterfly-tank` qualifies.
This approximation is recorded separately from opaque plugins in the IR and
manifest; the native payload is retained. Other Follower configurations, missing
sources and dependency cycles remain blocked. See the
[butterfly-tank QA](butterfly-tank-qa.md) for validation and limitations.

A scene with missing dependencies may produce a partial OBJ snapshot.
The OBJ shows the base geometry of resolved instances: it does not apply morphs,
deformations, visibility masks, dissolves or render effects. Manifest counters
describe omissions and approximations across all generated OBJ files, including
individual objects and the scene.

## glTF 2.0 static geometry profile

The direct C writer consumes native objects and the shared triangulator/UV
resolver. It does not parse OBJ or invoke Blender. Output is `.gltf` JSON with
one sibling `.bin` file and, for supported image maps, local `textures/*.png`.
Specular image maps use the optional `KHR_materials_specular` extension.
Geometry-free inputs produce a valid document without buffer/accessor arrays;
their accompanying `.bin` is empty. Surface presets retain their materials and
do not gain a synthetic preview mesh.

The writer follows glTF's right-handed Y-up convention, aligned float32 vertex
attributes, position bounds and relative resource addressing. Binary filenames
are percent-encoded in glTF buffer URIs, including accents, `%` and punctuation;
the filenames themselves stay readable. The format reference is the
[Khronos glTF 2.0 specification](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html).

FACE polygons and patch control cages use the same projected ear clipping as
the OBJ exporter. Cages are explicitly approximated, and unsupported or invalid
contours are counted and retained in LWIR. Points, two-point polygons and curve
control segments use glTF POINTS/LINES modes; loose points are retained. These
line segments do not evaluate the original curve basis.

Each triangle has three derived vertices and a computed flat normal. Grouping
uses material, primitive mode and UV availability. This avoids merging corners
at seams and keeps source polygon data independent from render geometry.
The initial writer uses non-indexed primitives; no vertex deduplication, native
smoothing, tangents or subdivision evaluation is claimed.

`--uv-map` shares the OBJ resolver, including VMAD precedence and native block
scope. The glTF derivative stores `(u, 1-v)`. A source face missing any selected
UV value goes into a primitive without `TEXCOORD_0`; zero UVs are not invented.
When no explicit map is selected, compatible LWOB planar/spherical image
projections generate UVs and material bindings as described in the
[texture profile](textures.md). LWO2 texture blocks remain preserved only.

Material names and surface assignments survive. Base color is clamped
`color * diffuse`, opacity is clamped `1 - transparency`, and emission is clamped
`color * luminosity`. Source scalar colors are used directly as an explicit
approximation; historical color-management equivalence is not established.
Metallic is zero and roughness is one. Opacity below one selects alpha blending.
LWO2 `SIDE=3` and the legacy LWOB double-sided flag produce double-sided
materials; `SIDE=1` is front-only. Other SIDE values use the front-only fallback
and increment `gltf_unsupported_sidedness`, making the conversion partial.
Specular image factors use `KHR_materials_specular`; native glossiness,
reflection, refraction and procedural shading are not reconstructed by this
neutral rough dielectric approximation. The supported
LWO2 sidedness values are defined in the
[NewTek object specification](https://documentation.help/LightWave/lwo2.html).

Scene glTF files contain one mesh per resolved object/layer selection, reused by
its instances. Geometry nodes and their parent chains keep local transforms at
the selected frame, including pivots and negative scale. With reflected basis
`C = diag(1,1,-1,1)`, the glTF local transform is `C M C`. Keeping the hierarchy
avoids flattening a nonuniform parent scale into a potentially sheared node
matrix. Only transforms supported by the existing evaluator are exported;
unsupported interpolation, parent chains or layer pivot semantics block the
scene snapshot while individual object glTF files remain available.

The declared `static-base-geometry-0.1` profile contains no animation channels,
cameras, light definitions, skins, morph targets or deformation evaluation.
`gltf_animated_channels_not_exported` records source channels with multiple keys.
Geometry-unrelated scene nodes are omitted and counted. A supported static
snapshot may return exit code zero despite having source animation, because
the profile explicitly targets one frame. Missing dependencies and geometry
omissions/approximations still contribute to the existing partial status.

Root and mesh `extras` retain source SHA-256 identities. Material extras include
source surface indices and hashes; nodes retain original scene indices/IDs.
Numeric source asset indices describe the original C extraction context;
hashes remain the stable identity when the batch reuses an object's export.
Every primitive's `extras.source_map` describes an additional span of the same
binary buffer, with one 12-byte little-endian record per derived vertex:
`uint32 polygon, uint32 corner, uint32 point`. These indices address the native
LWIR object; polygon/corner use `4294967295` for loose points. This span is
application metadata, not a glTF vertex attribute.

Manifests record glTF files, triangles, points, line segments, rejected contours,
nonplanar faces, cages, curve controls, missing UVs, material approximations and
omitted scene nodes. Geometry counters count each stored mesh once per glTF
document; repeated instances can therefore make OBJ and glTF totals differ.

## Validation performed

For v0.4.1, all 75 regression tests pass in Release and MSVC AddressSanitizer
(36 converter, 12 batch, 8 glTF, 12 texture and 7 scene-evaluation tests).
All seven `butterfly-tank` scenes now export; all ten published glTF files pass
Khronos validation, and all seven scene imports in Blender match their OBJ
geometry. See the [focused QA report](butterfly-tank-qa.md).

The glTF-specific profile is described above. Earlier validation
results remain historical evidence for their named converter versions.

- 26 converter regression tests using synthetic files: buffers, SHA-256, source bytes,
  encodings, padding, 24-bit VX, details, layers, VMAD seams, infinite weights,
  path resolution, hierarchy and motion, truncated inputs, plugin blocks and
  overwrite prevention, concave polygons, bridged holes, winding, UV transfer,
  coordinate scale, degenerate contours, nonplanar geometry, readable filenames,
  collisions and OBJ/MTL references.
- 11 batch regression tests covering project scope, source preservation,
  extensionless files, failure continuation, partial status, the Windows launcher,
  destination validation, repeated runs, timestamp collisions, shared scene
  dependencies and colliding source/project names. Release and AddressSanitizer
  configurations pass.
- Eight glTF regression tests decode the produced buffers and check reflected
  coordinates, triangle normals, source-corner mappings, bridged holes, VMAD UVs,
  incomplete UV groups, material opacity/sidedness, hierarchy, negative scale,
  pivots, layer selections, point/line/cage outputs, empty presets and blocked
  snapshots. Together, the 45 converter/batch/glTF tests pass in Release and
  AddressSanitizer builds.
- Comparison of the C reader with the independent Python inventory:
  **1,143/1,143** files, comprising 915 objects, 226 scenes and two presets.
  Counts and SHA-256 hashes match, including **1,162,552 points**,
  **1,450,682 primitives**, **1,137 maps**, **1,974 object loads** and **2,741 bones**.
- AddressSanitizer: no memory access diagnostics on the tests or corpus.
  Reading and SHA-256 hashes were also checked for Freestyle's 64 LWOB and
  11 LWS 1 files.
- Historical v0.1.0 reimport in background Blender 4.2: Metropolis UV (633 vertices,
  458 faces, comparison of 1,752 UV corners, including unbound corners filled
  with default values by Blender) and the Freestyle logo (4 vertices, 1 face).
- Export of `circus/Mr_Lector_2.lws`: the two instances requesting unavailable
  layers are reported, and exported cages are counted.
- v0.1.1 validation: all 26 n-gons in `mandarine-000-lw5/template/van.lwo` pass
  orientation, projected-area and interior-sample checks. Three bridged-hole
  faces are recovered. The source and native geometry buffers are unchanged.
  The triangulator was also exercised under AddressSanitizer on all 915 objects
  and two presets; see the [triangulation report](diagnostics/van-triangulation-check.json).
- v0.2.0 full batch: 1,143 conversions across 37 projects, with no failures
  (170 supported-subset conversions, 973 partial conversions and 1,580 skipped
  ancillary files). All conversion/project links, 1,143 copied source hashes and
  native buffer sizes, and 1,064 OBJ/MTL pairs pass the
  [output layout audit](diagnostics/output-layout-validation.json).
- Background Blender 4.2 reimport of renamed `Tour_Toit.lwo.obj`: 2,449 vertices
  and 4,256 faces match the OBJ. For `Lacustre.lws.obj`, the old and new layouts
  import identically, including geometry and material assignments. Blender
  imports 18,167 of 18,170 OBJ faces in both layouts; this pre-existing difference
  remains unresolved and is recorded in the
  [comparison report](diagnostics/output-layout-blender-comparison.json).
- v0.3.0 glTF qualification: the entire batch passes under AddressSanitizer,
  with 1,143 conversions and no failures or memory-access diagnostics. The final
  batch contains 917 object/preset glTF files and 147 scene glTF files; 79 scene
  snapshots are explicitly blocked by the existing evaluator's limits. Native
  sources and all published IR/OBJ/glTF links pass the
  [layout audit](diagnostics/gltf-layout-validation.json).
- The official Khronos validator 2.0.0-dev.3.10 checks all 1,064 glTF files and
  their referenced buffers: zero errors, zero warnings and two informational
  messages for material libraries without preview geometry. See the
  [validator report](diagnostics/gltf-validator-results.json). A separate
  [Metropolis UV export](diagnostics/gltf-uv-validation.json) also passes without
  errors or warnings; its six informational messages identify UV attributes
  without texture bindings, as expected for this profile.
- Background Blender 4.2 imports preserve the OBJ-reference triangle position
  multisets for Lacustre (18,170 triangles), Tour_Toit (4,256), the corrected van
  (1,592), and Metropolis with selected UVs (836). The largest matched corner
  distance for Lacustre is approximately `4.36e-6` meters. These are geometry and
  static-pose checks, not renderer equivalence or UV image-placement checks.
  See the [glTF reimport report](diagnostics/gltf-blender-reimport.json).

The [validation report](diagnostics/converter-validation.json) records the
configurations and results. These checks establish structural recovery and
selected export properties. They do not yet validate LightWave render fidelity,
complete animation behavior or other platforms.

To rerun the corpus comparison:

```powershell
python tests/check_corpus.py build/Release/lwconvert.exe --report build/corpus.json
```

To audit a completed batch's layout while its original inputs are available:

```powershell
python tests/check_output_layout.py output/batch-20260910-182442 --report build/layout-check.json
```

The optional Khronos validator can be obtained from its
[official releases](https://github.com/KhronosGroup/glTF-Validator/releases).
It is a validation dependency only; the converter does not need it at runtime.
To validate a batch without writing diagnostic files into the export folders:

```powershell
python tests/check_gltf_validator.py output/batch-20260910-211210/packages --validator build/tools/gltf-validator-2.0.0-dev.3.10/gltf_validator.exe --report build/gltf-validation.json --details build/gltf-validation-details
```

For an independent static-geometry reimport comparison:

```powershell
python tests/check_gltf_blender.py --blender "C:/Program Files/Blender Foundation/Blender 4.2/blender.exe" --gltf output/batch-20260910-211210/packages/lake-scenery/gltf/Lacustre.lws.gltf --obj output/batch-20260910-211210/packages/lake-scenery/obj/Lacustre.lws.obj --report build/gltf-import.json
```

The Blender test is optional:

```powershell
python tests/check_obj_blender.py --blender "C:/Program Files/Blender Foundation/Blender 4.2/blender.exe" --obj output/lector/obj/Mr_Lector_2.lws.obj --report build/obj-check.json
```

It checks counts, arities and per-corner UV multisets. It does not yet check
per-face UV correspondence or rendering.

For AddressSanitizer with MSVC, configure a separate build directory with
`-DLWCONVERT_SANITIZE=ON`, build in `RelWithDebInfo` and add the compiler's ASan
runtime directory to the test process's `PATH`. With non-MSVC compilers, this
option requests AddressSanitizer and UndefinedBehaviorSanitizer; that branch
remains to be qualified.

## References and next steps

The repository `C:/works/projects/preservation-freestyle-by-syndrome-condense`,
revision `1ba8faabbab39cdcf163de53a64ea92e93f4b55d`, was used to compare the
structure of its `src/lwob.cpp` and `src/scene.cpp` readers and select a real
example. These C++ readers include nXng conventions, notably Y-down coordinates
and treating keys one frame apart as cuts. Those conventions are not imported
into the generic converter. Their GPL code was not copied into this C
implementation; the new files follow this repository's license.

Native field descriptions come from the archived NewTek SDK:
[LWO2 objects](https://documentation.help/LightWave/lwo2.html) and
[LWSC 3 scenes](https://documentation.help/LightWave/lwsc.html). Corpus variants
supplement this documentation, particularly layer numbering, presets and
envelopes with inconsistent declared key counts.

The proposed next steps are to evaluate animation curves, native smoothing and
additional texture projections and shader semantics, extend the glTF profile, and add the Python adapter for
`.blend`. The latter will run
in a background Blender process; no custom addon is required. All three outputs
should share LWIR and the same qualified geometry, material and animation
derivations.
