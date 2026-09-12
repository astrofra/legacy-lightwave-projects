# Native bone bindings and glTF rest rigs — v0.5.0

Update: v0.9.0 now derives procedural and hybrid weights in standalone C.
See [the measured profile](procedural-skinning.md), including the distinction
between `Smilla_IK.lws` (assigned weight maps) and `smila_run_cycle.lws`
(another model revision, without WGHT maps). The remaining text describes
the earlier milestone.

Since v0.8.2, the default `--gltf-rigs skins` omits the unbound rest-skeleton
files discussed below. Their diagnostic entries and original IR remain present.
Use `--gltf-rigs all` to reproduce these historical skeleton-only glTF exports.
Explicit skins and native animated derivatives remain exported by default.

This report describes the v0.5.0 rest-rig milestone. The later
[v0.6.0 animation profile](smila-animation-qa.md) captures evaluated LightWave
deformation as morph targets and bone poses as TRS tracks; it does not recover
editable procedural skin weights.

The previous writer exported static meshes without bones or skin weights.
Scene IR now exposes rest poses, bone-to-object ownership, weight-map names
and native influence settings. A separate glTF rest rig transfers explicit
normalized weight-map-only bindings. Smila's procedural influences are not yet
evaluated, so its new rig files contain the rest hierarchy and base geometry
without a bound glTF skin.

Subdivision is never baked into glTF. Patch control cages stay at their original
resolution; LWIR preserves native polygons, patch types and maps. Editable
subdivision is deferred to the Blender backend.

## Reading the native IR

Each scene node has a nullable `bone` member with:

- `owner_item`, active state, `rest_position`, `rest_rotation_hpb_degrees` and
  `rest_length`; `rest_fields_present` is a bitmask: position 1, direction 2,
  length 4. Missing rest position/direction blocks the derived rest rig.
- The original `weight_map` byte string and `weight_map_status`, resolved
  against WGHT maps of the owning object and selected layer.
- Map-only and normalization flags, strength, strength-by-length, limited
  range, joint compensation and muscle-flex settings.

`rig_parameters` retains ordered native `Bone*` statements other than
`BoneName`/`BoneMotion`, plus `ScaleBoneStrength`, `FasterBones`, `UseBonesFrom`,
`SubPatchLevel` and `SubdivisionOrder`. Every field keeps its original name,
value bytes and source offset. This list distinguishes omitted settings from
the typed reader defaults. Original source files remain copied byte-for-byte.
Parent IDs, pivots, animation channels and plugin blocks remain in their
existing IR fields. WGHT values and native point IDs remain in object IR.

LightWave's SDK distinguishes map-only deformation from geometric influences,
and normalization divides displacement by the total weights. See the
[NewTek Bone Info API](https://documentation.help/LightWave/boneinfo.html) and
[Layout bone commands](https://documentation.help/LightWave/layout.html).
Assigning a weight-map name alone does not establish final per-bone weights.

## glTF profile and publication

Every resolved object instance with bones receives a `gltf_rigs` manifest entry.
When its rest transform is supported, it writes
`gltf/<scene filename>.rig-<owner item ID>.gltf` and a sibling `.bin`.
Batch publication copies these files into the project's shared glTF directory
and lists them in each conversion record's `rig_gltf` array.

These files use `rest-skeleton-0.1`: native rest pose, in object-local space.
They remain available when scene motion plugins or IK block the static scene
snapshot. They do not reproduce scene placement, animated pose or IK. The usual
scene and standalone object files still use `static-base-geometry-0.1`.

For a qualified binding, standard `skins`, `inverseBindMatrices`, `JOINTS_n`
and `WEIGHTS_n` implement linear blend skinning. All positive influences are
retained in as many VEC4 sets as necessary; there is no four-bone truncation.
Bone IDs follow native order, inactive bones remain in the hierarchy, and
inactive bones contribute no weight. Normals/UV seams and triangulation copy
weights by native point ID. Native normalized weights are converted to float32.
An identity object anchor, joint zero, keeps vertices with no positive weights
fixed; the manifest explicitly counts them.

Rest rotations use native HPB degrees and the existing Y/X/Z convention,
with the same Z reflection as geometry. Recorded pivot rotation is supported
when rest angles are zero, as described by LightWave's
[Record Pivot Rotation and Record Bone Rest Position documentation](https://docs.lightwave3d.com/2025/modify-group.html).
Combined nonzero rest/pivot rotations, translated bone pivots, layer
pivots/parents, shared-skeleton references and invalid hierarchies are blocked
with a reason. They remain available in native IR.

Skins require active bones to use scalar, continuous WGHT maps, map-only mode
and normalization. Missing maps, procedural influence, negative or non-finite
weights, discontinuous maps, duplicate point bindings, non-normalized semantics,
joint compensation or muscle flexing prevent skin generation for that object.
Its rest hierarchy and geometry still export with status `skeleton-only`.
Original influence settings are present in node `extras.native_rig_parameters`.
These extras do not implement deformation in ordinary glTF viewers. Without a
skin, importers may represent the hierarchy as empty objects rather than bones.

The skin data layout follows the [Khronos glTF 2.0 skin specification](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#skins).

## Smila results, 11 September 2026

Focused run:
`output/smila-skin-qa/batch-20260911-120330/packages/smila-by-moebius/`.
All 33 supported inputs produced packages: 20 objects and 13 scenes. They remain
`partial` because native materials, deformations and other unsupported features
are reported. No conversion failed. Thirteen ancillary inputs were skipped.

Nine scenes produced rest rig files, representing 900 native bone instances:

| Scene | Bones | Active procedural bones | Missing assigned WGHT maps |
|---|---:|---:|---:|
| `Smilla_IK.lws` | 30 | 30 | 14 |
| `smila_rig.lws` | 121 | 88 | 0 |
| `smila_export_converted.lws` | 107 | 74 | 0 |
| `smila_export.lws` | 107 | 75 | 0 |
| `smila_posing.lws` | 107 | 75 | 0 |
| `smila_posing_02.lws` | 107 | 75 | 0 |
| `smila_posing_toon.lws` | 107 | 75 | 0 |
| `smila_rig_converted.lws` | 107 | 75 | 0 |
| `smila_run_cycle.lws` | 107 | 75 | 0 |

All nine are `skeleton-only`. The modern Smila rig objects have no stored WGHT
maps; LightWave derives influences from bone geometry, falloff, strengths and
other settings. `Smilla_IK.lws` assigns maps with map-only mode disabled, and
14 bones reference absent `hat`, `hands`, `leg_right` or `leg_left` maps. The
present maps therefore cannot simply be treated as final glTF weights either.
Reproducing these deformations still needs a qualified LightWave evaluator or
an explicitly accepted approximation. No procedural weight approximation is
included in this release.

Six main scene snapshots also export; seven remain blocked by unsupported
motion/parent evaluation. That snapshot limitation is independent of rest rig
export. The total is 35 glTF files: 20 objects, six snapshots and nine rigs.

## Validation

- All 83 regression tests pass in Release and MSVC AddressSanitizer. Eight
  skin tests cover actual buffer decoding, rest matrices, deformation, six
  influences, UV seams, inactive bones, fixed unweighted vertices, map/layer
  ownership, multiple instances, failures, publication and unsubdivided cages.
- All 35 Smila files pass Khronos validator `2.0.0-dev.3.10` with zero errors
  and warnings. Published links, buffers and all 33 native source hashes pass
  the batch layout audit.
- A synthetic rig's three glTF outputs also have zero validator errors and
  warnings. Three informational `UNUSED_OBJECT` entries concern unbound UVs.
- Blender 4.2 imports the synthetic skin as an armature. Moving its sixth
  influencing bone through the evaluated armature modifier produces the
  expected displacement, including on duplicated corners; unweighted vertices
  stay fixed. Maximum rest error: `1.33e-7`; deformation error: `1.01e-8`.
  This validates skin transfer, not LightWave's procedural deformation.

The reproducible fixture and tests are in `tests/test_skin.py`; external import
validation is in `tests/check_skin_blender.py`. A machine-readable result is
kept in [diagnostics/smila-skin-qa.json](diagnostics/smila-skin-qa.json).
