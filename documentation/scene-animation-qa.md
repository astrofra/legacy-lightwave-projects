# Scene transform animation: JumpingBall and Newton

The Babylon screenshots matched the exported files: the old scene glTF files
contained no `animations` array. `lwconvert` preserved motion keys in LWIR, but
its C glTF writer only emitted the pose selected by `--frame`. Native rig
animation was a separate opt-in workflow and did not export these ordinary
scene-object movements.

Version 0.7.0 writes sampled object/parent TRS animation directly into each scene
glTF, using the existing motion evaluator. It handles hierarchy, pivot offsets,
quaternion continuity and signed/zero scale. Original object exports remain
static because they contain no scene-instance motion. The full sampling scope
and limits are in the [converter guide](converter.md#scene-transform-animation).

## Regenerated examples

Packages and reports are in `output/scene-animation-0.7.0/`.

| Scene | Clips | TRS tracks | Samples | Source frames | Duration |
| --- | ---: | ---: | ---: | --- | ---: |
| JumpingBall.scene | 1 | 3 | 70 | 1–70 | 2.3 s |
| Newton scene.1 | 1 | 6 | 50 | 1–50 | 1.6333 s |
| Newton scene.2 | 1 | 12 | 50 | 1–50 | 1.6333 s |
| Newton scene.3 | 1 | 15 | 50 | 1–50 | 1.6333 s |
| Newton scene.pic | 1 | 6 | 50 | 1–50 | 1.6333 s |

The existing parser uses 30 fps for these sources. `JumpingBall-animated.zip`
and `newtons-scene.3-animated.zip` contain the corresponding scene glTF and binary
buffer, ready to extract and open together in a glTF viewer. All original source
files and older exports remain unchanged.

## Validation

- All seven CTest suites pass. Added regression cases decode actual animation
  buffers, compare animated geometry to OBJ poses across multiple frames, cover
  v1/v3 timing, TCB sampling, signed/zero scale, pivots, animated parents,
  quaternion continuity, constant motion, unsupported-animation fallback and
  batch publication of animated glTF files.
- Khronos glTF Validator `2.0.0-dev.3.10` validated all 21 regenerated glTF files
  and external resources: zero errors, warnings, information messages or hints.
  Report: `output/scene-animation-0.7.0/khronos-validation.json`.
- Blender 4.2 imported the JumpingBall clip as one action. Its ball changes
  transform; world triangles match the C-evaluated OBJ snapshots at source
  frames 1, 18, 35 and 70, with maximum vertex error below `4.8e-7`.
- Blender imported Newton scene.3 as five actions. Moving string/ball instances
  are detected; world triangles match at frames 1, 13, 25 and 50, with maximum
  vertex error below `1.1e-8`.

The reimport check is reproducible with
`tests/check_scene_animation_blender.py --blender <blender.exe> --gltf <scene.gltf>
--source <original scene> --content-root <project> --converter <lwconvert.exe>
--report <report.json>`.

This confirms glTF structure and playback in an independent importer against
the converter's supported evaluation. It does not establish equivalence with
an original LightWave render. Sampling approximates motion between source
frames; bone/IK/morph/plugin deformation still uses the separate native
LightWave evaluation workflow.
