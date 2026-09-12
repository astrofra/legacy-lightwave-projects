# Carrot driven robot scene QA — 12 September 2026

The project is `content/carrot_driven_robot/`. **Both assembled, animated scene
glTFs are now produced** through the optional native LightWave fallback:

```text
output/carrot-robot-native/batch-20260912-063401/packages/carrot_driven_robot/gltf/
    robot.lws.gltf
    robot.lws.bin
    robot_night.lws.gltf
    robot_night.lws.bin
```

The sources and their IR remain unchanged by the native evaluation. All object
references resolve, including the historical `I:fra/...` paths in the night scene.

## Produce the assembled animations

```powershell
python -X utf8 tools/batch_convert.py --content content/carrot_driven_robot --output-root output/carrot-robot-native --lightwave-root _tmp/_extern/LightWave/LW9.6 --timeout 180
```

The checked-in `bin/win64/lwconvert.exe` and `lw_capture.p`, Python 3 and the
supplied LightWave 9.6 x64 installation perform the conversion. Viewing the result
requires only the `.gltf` and its sibling `.bin`; no LightWave runtime is needed.
Without `--lightwave-root`, the C evaluator still reports the unsupported IK.

The [IK oracle feasibility study and roadmap](lightwave-ik-oracle-feasibility.md)
uses this export as the baseline for a future independent evaluator. The
[frame-84 repeatability report](diagnostics/ik-oracle-repeatability.json) records
the initial comparison between sequential evaluation and two fresh processes.

| Scene | Geometry instances | Object/null nodes | TRS tracks | Instanced triangles |
|---|---:|---:|---:|---:|
| `robot.lws` | 23 | 34 | 48 | 9,163 |
| `robot_night.lws` | 15 | 26 | 48 | 3,473 |

Both clips contain 169 samples at 30 fps, source frames **0–168**, for **5.6 s**.
These bounds come from the native preview interval. The render interval extends
to frame 170; pass `--animation-end 170` to request that instead. Twenty object
or null transforms vary. Repeated lamp instances share mesh geometry. The binary
buffers are 708,072 and 482,712 bytes respectively.

## Native rigid-scene profile

`lightwave-evaluated-scene-transforms-0.1` extends the native capture bridge to
objects without bone rigs. LightWave evaluates the original IK statements in an
isolated scene copy, with subdivision disabled. Every point and polygon boundary
is checked against native IR, and captured world points must agree with each
object's rigid transform within float32 SDK precision. Any deformation or changed
topology blocks this profile instead of being silently flattened.

The C writer assembles the original geometry, layer selections, materials and
instances using a neutral intermediate scene. Python replaces its transforms
with the captured poses, preserving original item IDs, parent relationships and
native rig metadata. All object and null nodes remain in the glTF, including IK
targets. Animation uses standard translation, quaternion rotation and scale
tracks. There are no morph targets, invented skin weights or added subdivision.

Original scene IR, motion keys and source copies remain the archival record.
`gltf_evaluated_scene` contains sample counts, moving item IDs, rigidity checks
and provenance hashes. `evaluated_animation` references native captures, scene
copies, helper/host/module hashes and logs. The original C rejection is retained
as `source_scene_gltf_issue`. Batch publication places the result in `scene_gltf`
and lists its animation under `animation_gltf`.

SceneEditor workspace masters and the FPrime preview master join the known
display/render-only exclusions in the evaluation copy. Native IK settings and
the six concatenated lines remain verbatim; no missing newline is inferred.
This reproduces their evaluation by LightWave 9.6 build 1539, without asserting
that a different authoring version would interpret irregular lines identically.

## Validated assembled output

All **19 glTFs**, including both scene assemblies, pass Khronos validator
`2.0.0-dev.3.10` with zero errors, warnings, infos or hints. Publication QA checks
the 19 original source hashes, 338 frame-capture hashes, relative links and binary
sizes. Evidence is in
[`diagnostics/carrot-robot-native-qa.json`](diagnostics/carrot-robot-native-qa.json).

Blender 4.2.0 imports and plays both clips. At source frames 0, 42, 84, 126 and
168, all exported object/null world matrices and scene world triangles match
LightWave captures. Maximum triangle-corner errors are **3.78×10⁻⁶** for `robot`
and **7.69×10⁻⁷** for `robot_night`, in source units. Maximum matrix-component
error is **7.16×10⁻⁷** in both scenes. This checks the native IK result through an
independent glTF importer, rather than comparing against the unsolved C pose.

```powershell
python -X utf8 tests/check_native_scene_blender.py --blender "C:/Program Files/Blender Foundation/Blender 4.2/blender.exe" --package output/carrot-robot-native/batch-20260912-063401/packages/carrot_driven_robot/IR/robot.lws --report output/carrot-robot-native/blender-robot.json
```

The **105 regression tests** pass in Release and MSVC AddressSanitizer. Three
new native-scene cases cover rigid validation, nonuniform reflected parents,
shared meshes, hierarchy, quaternion continuity, timing offsets, original-source
identity, deformation rejection and publication as the main scene glTF.

The package remains partial: the C OBJ scene snapshot still cannot solve IK,
and existing polygon/material limitations persist. Native logs retain missing
HDR/environment images and FPrime/SG_AmbOcc shader notices. Lamp *geometry* is
exported, but LightWave light definitions, postprocessing, native shading and
camera optics are outside this profile. Interpolation between captured frames
follows glTF TRS rules; only captured-frame agreement was qualified.

## Initial C-backend diagnosis

These scenes animate rigid object parts through inverse kinematics. There are
no LightWave bones. Rotation controllers such as `PController 3` select IK, and
`GoalObject` statements assign targets to both hands and toe nulls. Native motion
keys alone do not determine the final articulated pose. The converter currently
evaluates keyframe/TCB object motion and a bounded Follower profile, not an IK
solver. NewTek's `lwrender.h` declares controller mode 0 as keyframes and mode 3
as IK; the [Item Info API](https://documentation.help/LightWave/iteminfo.html)
exposes the controller and goal relationships.

| Source | Object instances with geometry | Bones | Unresolved object references | IK goal assignments |
|---|---:|---:|---:|---:|
| `robot.lws` | 23 | 0 | 0 | 4 |
| `robot_night.lws` | 15 | 0 | 0 | 4 |

Before this QA, the first rejected node was `10000004`, `mesh_bot_main_l.lwo`,
because of `GoalObject 17`. The manifest only said:

```text
transform: node 10000004: unsupported pivot/IK/bone evaluation
```

The parser did not independently detect H/P/B rotation controllers. That could
allow an incorrect raw-key pose through in a scene where no required geometry
node or ancestor held a `GoalObject`. The QA fix now detects those controllers
and gives the actual reason. Both robot scenes report:

```text
transform: node 10000002: PController 3: requires native inverse-kinematics evaluation
```

The arm at node `10000002` is encountered before the hand. Targeting, velocity
alignment, path alignment and unknown nonzero controller modes are also rejected
instead of being treated as ordinary keyframes. Disabled IK and mode-zero
controllers continue to permit regular scene animation.

## Source irregularities and preservation

Each scene also has six concatenated lines, for example:

```text
HJointStiffness 600PController 3
HJointStiffness 40PController 3
HJointStiffness 50PController 3
```

These are preserved as written, including byte offsets, in `rig_parameters` and
in the byte-for-byte source copy. They receive a malformed-stiffness diagnostic;
the converter does not infer and insert a missing newline. Their exact native
interpretation has not been qualified. Valid standalone IK controllers already
establish the main unsupported feature, independently of these irregularities.

Native H/P/B controllers, goal assignments and strengths, IK anchors, full-time
IK flags, limits and joint stiffness now appear explicitly in scene IR's
`rig_parameters`, rather than only in `source.bin`. Fourteen nodes in each scene
are flagged for unsupported evaluation or malformed settings.

SceneEditor, FPrime and image/postprocessing plugins remain preserved. They are
not the first rejection reported by the C transform evaluator. Other existing
geometry/material limitations remain: patch cages, nonplanar polygons, material
approximations and, for some objects, rejected polygon contours. No subdivision
is added and no source file is edited.

## Earlier diagnostic-only QA

The baseline reproduction is in
`output/carrot-robot-qa-before/batch-20260912-061549/`.
The updated diagnostics and exports are in
`output/carrot-robot-qa/batch-20260912-061903/`.
Machine-readable results are retained in
[`diagnostics/carrot-robot-qa.json`](diagnostics/carrot-robot-qa.json).

```powershell
python -X utf8 tools/batch_convert.py --content content/carrot_driven_robot --output-root output/carrot-robot-qa
```

The batch contains 19 partial conversions: 17 objects and 2 scenes. All 19 source
copies pass their SHA-256 checks. The 17 shared object OBJ/MTL pairs and 17 object
glTF/buffer pairs are published correctly. Khronos validator
`2.0.0-dev.3.10` reports zero errors, warnings, infos or hints on those glTFs.
This validates the available object exports, not a solved robot assembly.

All 102 regression tests pass in Release and MSVC AddressSanitizer. Four added
cases cover controller-driven rejection without a goal, native parameter/source
preservation, disabled IK with ordinary animation, and concatenated source lines.
`bin/win64/lwconvert.exe` is refreshed from the Release build.

That initial QA only improved the C diagnostic. The native whole-scene bridge
described above now supplies the missing animated glTFs; an independent C IK
solver remains unimplemented.
