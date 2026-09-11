# Smila native animation to glTF — v0.6.0

The optional animation exporter produces a playable glTF from
`content/smila-by-moebius/smila_run_cycle.lws`. It evaluates the original scene
with LightWave 9.6 x64, captures its cage and bone poses, then writes standard
glTF 2.0 animation. No subdivision is added to the exported geometry.

This scene is a useful rig test, but the supplied file does not evaluate to a
complete running cycle. Only the three rotation channels of `L_Thigh` contain
multiple keys, at approximately frames 0 and 15. Pitch is controlled by IK;
expressions and constraints also affect the final poses. The MotionMixer actor
has `ActorState 0`, an empty `ActorMotions` block and three empty `TrackMotions`
blocks. Native evaluation produces only a slight leg adjustment. In the same
project, `smila_rig_converted.lws` has a much more visible motion and also exports.

## Reproduce

From the repository root, using the provided Windows binaries:

```powershell
python -X utf8 tools/export_lightwave_animation.py content/smila-by-moebius/smila_run_cycle.lws --content-root content/smila-by-moebius --output output/smila-run-animation --lightwave-root _tmp/_extern/LightWave/LW9.6 --start-frame 0 --end-frame 25
```

The output directory must be new and its parent must exist. The resulting
animation is `gltf/smila_run_cycle.lws.anim-10000000.gltf`, with its sibling `.bin`.
Retain both when copying it. Existing source limitations can still return exit
code 2 even when the animated derivative was produced; inspect `manifest.json`.

Batch mode uses the same evaluator before publishing each scene:

```powershell
.\convert_content.bat --content content/smila-by-moebius --lightwave-root _tmp/_extern/LightWave/LW9.6 --animation-start 0 --animation-end 25
```

Without `--lightwave-root`, the normal batch and C executable retain their
snapshot/rest-rig behavior. With native animation enabled, unspecified frame
bounds use `PreviewFirstFrame`/`PreviewLastFrame`, falling back to the render
bounds. Smila's preview is 0–25 at 25 fps; its render interval is 10–20 with a
frame step of 10, which would be a poor default for this test. Sampling defaults
to every frame. Use `--frame-step` in the single-scene wrapper, or
`--animation-step` in batch mode, to change it. The interval must contain at
least two samples and be divisible by the step; the current limit is 1,001
samples. glTF time starts at zero and records the original start time in extras.

Conversion requires Python 3, `bin/win64/lwconvert.exe`, `bin/win64/lw_capture.p`
and the user's installed LightWave runtime. The helper and ScreamerNet must have
the same architecture. Playback of the glTF requires no LightWave runtime.

## What is preserved and exported

Native scene and object IR, their source copies, animation keys, plugin trees,
bone settings and rest glTFs are retained. Each animated rig gets an additional
`gltf_animations` manifest entry and a separate `*.anim-<owner ID>.gltf`/`.bin`.
The scene manifest's `evaluated_animation` links to a capture manifest under
`IR/<scene filename>/evaluated-animation/`. Batch records expose the files in
`animation_gltf`.

The `lightwave-evaluated-cage-0.1` derivative contains:

- The native bone hierarchy as glTF nodes, with sampled translation, quaternion
  rotation and scale tracks where they vary. The owning object's evaluated world
  transform places the rig; bone transforms remain relative to their parent.
- The evaluated cage at the first sample and position/normal morph targets for
  later samples. A weights track selects those targets. Between samples, weights
  interpolate linearly and rotations use glTF's quaternion interpolation.
- Original point/corner correspondence, material assignments and the rest
  export's supported attributes. UV seams receive deformation by native point
  identity. Normals are recomputed per triangle, matching the flat-normal profile.
- Source-frame metadata and a SHA-256 reference to the evaluation provenance.

Smila uses procedural bone influences and plugins. This profile preserves their
evaluated result, without estimating a new set of weights. **The animated mesh
is driven by morph targets, not by an editable glTF skin.** Moving the exported
bone nodes will not reskin that mesh. Rest rigs and explicit native binding
metadata remain available separately for future editable Blender work.

Sampling the animation is distinct from subdividing the geometry. The exporter
sets `SubPatchLevel 0 0` only in a working scene copy, then checks every captured
base point against LWIR and compares complete polygon boundaries. Changed point
order, point counts or polygon connectivity block the derivative. Original
patch polygons, subdivision order and levels remain in the archival IR. No
resampled surface is substituted for the source cage.

Standard glTF supports both node TRS tracks and morph-target weights; this
profile needs no animation extension. See the
[Khronos glTF animation specification](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#animations).

## Native evaluation and provenance

The helper in `tools/lightwave_capture/capture.c` registers an image filter that
reads object geometry and item poses after an 8×8 frame has been evaluated. It
does not modify geometry or item transforms. The frozen mesh API supplies base
positions and evaluated world positions, using NewTek's
[Object Info API](https://documentation.help/LightWave/objinfo.html).
[Item Info](https://documentation.help/LightWave/iteminfo.html) supplies basis
vectors, pivot and world position; the exporter accounts for the pivot before
converting coordinates. Frozen mesh handles are released after capture.

ScreamerNet runs hidden with an isolated working directory and configuration.
The original LWO bytes are copied into per-instance directories while preserving
their filenames: expressions such as `smila_rig_02.L_Thigh.rot(Time).p` depend on
the object's name. Native capture records preserve the actual SDK names as hex
bytes so this can be audited. The source `relax` MorphMixer setting is retained.

The qualified modules are MotionMixer/MM_MotionDriver, LW_Expression,
SimpleOrientConstraints, LW_Follower and LW_Cyclist, plus the built-in
LW_MorphMixer. These are loaded from the supplied LightWave installation.
Display custom objects and image filters, plus the known ProxyPick/BRDF UI
masters, are removed from the working copy. Unknown master or deformation
plugins block evaluation instead of being silently discarded. Antialiasing,
motion blur, depth of field and radiosity are disabled; no rendered image is
saved. The original files remain unchanged.

`evaluated-animation/capture.json` records source, host, helper/module, working
scene, log and per-frame hashes; frame bounds; overrides; retained and removed
plugins; and resource issues reported by ScreamerNet. Working copies, configuration,
logs and ASCII frame captures accompany that manifest. Failed evaluations also
retain their diagnostics when the batch publishes the successfully extracted IR.

The helper is original repository code compiled against the user's **NewTek
LightWave SDK**. Its API declarations and documentation are attributable to
NewTek; SDK headers, sample code and LightWave runtime binaries are not vendored
or copied into `bin/win64/`. To rebuild the optional helper:

```powershell
cmake -S . -B build -DLWCONVERT_LIGHTWAVE_SDK=S:/works/legacy-lightwave-projects/_tmp/_extern/LightWave/LW9/SDK
cmake --build build --config Release
```

An MSVC x64 Release build stages `lw_capture.p` alongside `lwconvert.exe`.
The helper links the C runtime statically. Ordinary builds without an SDK path
still build the converter; sanitizer builds do not replace distributed binaries.

## QA on 11 September 2026

Qualified batch:
`output/smila-animation-qa/batch-20260911-124905/packages/smila-by-moebius/`.
Machine-readable evidence is in
[`diagnostics/smila-animation-qa.json`](diagnostics/smila-animation-qa.json).

| Scene | Samples | Varying bone nodes | Maximum local point displacement |
|---|---:|---:|---:|
| `smila_run_cycle.lws` | 26 | 14 | 0.0000996081 |
| `smila_rig_converted.lws` | 26 | 84 | 0.917137 |
| `smila_rig.lws` | 26 | 0 | 0 |

Displacements use source coordinate units and are measured against the first
sample. The last scene's exported sample sequence is static over this interval.
`run_cycle` contains 107 bone nodes, 2,432 source cage points, 2,445 native
polygons and 4,414 derived triangles. Its 1-second clip has 35 channels, including
one weights channel, and 25 morph targets; the binary is 8,468,652 bytes.

The full batch preserves 33 native sources and produces 26 OBJ/MTL pairs and
38 glTF/buffer pairs. All 38 glTF files pass Khronos validator
`2.0.0-dev.3.10` with **zero errors, warnings, infos or hints**. Publication QA
checks source/capture hashes, relative links, buffer sizes and material resources.
The overall batch remains partial because pre-existing texture/snapshot/skin
limitations persist. Six other rigged scenes report unqualified master plugins:
`LW_LScriptCommander` in five scenes and `.SpreadsheetStandardBanks` in
`Smilla_IK.lws`; their archival IR and rest exports remain published.

Blender 4.2.0 imports both moving scenes. At source frames 0, 5, 15 and 25, the
evaluated world triangles and all 107 bone-node world matrices match their native
LightWave captures. Maximum corner error is **5.01×10⁻⁹** for `run_cycle` and
**3.10×10⁻⁸** for `smila_rig_converted`, in source units. Maximum bone-matrix
component errors are **7.75×10⁻⁷** and **1.14×10⁻⁶**, respectively.

The 91 regression tests pass in Release and MSVC AddressSanitizer. Eight new
animation cases cover capture completeness, unchanged cage topology, seams and
normals, timing offsets, quaternion continuity, signed scale, hierarchy checks,
isolated source copies, plugin retention and batch publication. These tests use
synthetic captures and do not require LightWave. The separate real-application QA
uses the supplied LightWave 9.6 AMD64 ScreamerNet build 1539 and Blender:

```powershell
python -X utf8 tests/check_animation_blender.py --blender "C:/Program Files/Blender Foundation/Blender 4.2/blender.exe" --package output/smila-animation-qa/batch-20260911-124905/packages/smila-by-moebius/IR/smila_run_cycle.lws --report output/smila-run-blender-check.json --frame 0 --frame 5 --frame 15 --frame 25
```

These checks establish agreement at captured frames with that LightWave version.
They do not establish matching interpolation between captures, editable skinning,
or rendering equivalence with the scene's original authoring version. Native
logs still report missing appearance images and FPrime; original textures and
plugin data remain in IR, and the existing material profile's limitations apply.
The current animation profile targets objects that have a qualified rest-rig
export; camera, light and standalone non-rig object animation are deferred.
