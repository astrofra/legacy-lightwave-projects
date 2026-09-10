# LWS/LWO converter in C — v0.2.0

Status as of 10 September 2026. This first milestone provides a C17 library and
the `lwconvert` executable, with no Blender dependency. It extracts native
structures and produces OBJ/MTL files. The glTF 2.0 and `.blend` backends remain
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
`--uv-map` explicitly selects a native TXUV map; no map is automatically selected
from material settings.

Exit codes:

| Code | Meaning |
|---|---|
| 0 | Successful read or export of the supported subset |
| 1 | Argument, read, structural or write error; diagnostic includes an offset |
| 2 | Package produced with missing, omitted or approximated elements; see the manifest |

Exit code 0 does not guarantee LightWave visual fidelity. MTL materials remain
a scalar approximation, and the scope is recorded in every package.

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
are extracted, but images are not yet resolved, copied or decoded. Missing or
unreadable dependencies are reported and are not included in the package.

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
gltf/                     # Reserved; backend not implemented yet
blender/                  # Reserved; backend not implemented yet
```

The batch combines these files under `packages/<project>/`, reusing an object's
IR and OBJ/MTL pair when the same source path and hash recur in that project.
It puts each input's conversion manifest in `IR/<source name>/manifest.json` and
writes an index at the project root. Asset indices remain local to each
conversion, so a scene's node indices refer to that conversion's asset list.
The batch rewrites the manifest URIs and OBJ `mtllib` references when publishing
files. Copy the whole project directory to retain the shared dependencies.

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
`mtl`, and scenes include `scene_mtl`. A `formats` map explicitly marks `gltf`
and `blender` as `not-implemented`; their directories are empty placeholders.
Batch reports use schema version `0.2` and link directly to each conversion's
manifest and primary OBJ when available.

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
emission and transparency, without texture bindings.

For scene snapshots (`obj/<scene filename>.obj`), the current local matrix is
`T(position) × Ry(heading) × Rx(pitch) × Rz(bank) × S × T(-pivot)`;
it is composed with parent transforms before coordinate-system conversion.
Tests check translation, heading rotation, pivots, parents and negative scale.
Nontrivial LWO2 layer pivots/parents block this snapshot until their interactions
with Layout are qualified.

The evaluator supports exact keys, linear and stepped interpolation, and reset,
constant and repeat behaviors. TCB/Hermite/Bezier parameters are preserved, but
sampling within those spans is not implemented. Transforms driven by certain
plugins, channel modifiers, rotated pivots, bones or IK block the snapshot.
Cycles and missing parents are also reported.

A scene with missing dependencies may produce a partial OBJ snapshot.
The OBJ shows the base geometry of resolved instances: it does not apply morphs,
deformations, visibility masks, dissolves or render effects. Manifest counters
describe omissions and approximations across all generated OBJ files, including
individual objects and the scene.

## Validation performed

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

The proposed next steps are to evaluate animation curves and texture projections,
then add the glTF 2.0 writer and Python adapter for `.blend`. The latter will run
in a background Blender process; no custom addon is required. All three outputs
should share LWIR and the same qualified geometry, material and animation
derivations.
