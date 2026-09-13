# Quatuor / Smila skin QA — 13 September 2026

The actual v0.14.0 files from `output/batch-20260913-173025` do not collapse
when imported into Blender 4.2.0. This QA does not establish the trigger in the
viewer where the collapse was reported. No change to skin weights, rest poses,
joint serialization or the autonomous IK solver was made.

## Checked files and results

| Files | Result |
| --- | --- |
| Four `quatuor/gltf/work/3d/dialogue01.lws.rig-*.gltf` files | All contain a skin; evaluated rest geometry stays intact |
| Eight `smila-by-moebius/gltf/*.rig-*.gltf` files | All contain a skin; evaluated rest geometry stays intact |
| `smila-by-moebius/gltf/Smilla_IK.lws.gltf` | Skin playback checked on all 41 frames, with 31 joints and 11,250 exported vertices |
| All 115 glTF files in these two projects | Khronos Validator: zero errors, zero warnings; one informational `UNUSED_OBJECT` message |

For the 12 rest rigs, the maximum difference between Blender's evaluated
armature vertices and the exported undeformed positions is `9.84e-7` object
units or less. Independently evaluating every joint hierarchy, inverse bind
matrix and weight set also preserves the rest positions. The machine-readable
[report](diagnostics/quatuor-smila-skin-20260913-qa.json) records per-file errors
and hashes of the exact glTF files, buffers and manifests.

Smilla's animated vertices match the independently reconstructed C bake and
exported weights within `8.39e-7` units at every sampled frame. A vertex moves
up to `0.7384` units during playback: the check exercises deformation, not just
the presence of an animation entry. This measures correct glTF playback of the
current approximation, not fidelity to native LightWave poses or deformation.

The Release regression groups for explicit skinning, procedural/hybrid
skinning, animation and autonomous IK all pass. No LightWave runtime was
invoked during these checks.

## Why the separate rigs are not animated

All ten scenes examined here are LWSC 3 and select the `lightwave6` weight
profile. The file version does not distinguish the working Smilla animation
from the reported rest rigs.

`Smilla_IK.lws` is supported by the autonomous solver and publishes its skin
and animation together in the main scene glTF. The other eight Smila scenes
and `dialogue01.lws` report:

```text
IK: pivot rotation is outside the autonomous profile
```

Their separate rigs therefore retain an object-local rest pose and skin weights,
with `animated: false` in the manifest. Their source animation and native
parameters remain in the IR. These files are suitable for inspecting or editing
the bound mesh, but they cannot play an animation that was not exported.
Support for oriented pivots needs qualification in the autonomous evaluator;
some source scenes also have motion plugins. Removing the pivot guard alone
would not establish correct playback.

The rest rigs use glTF node matrices; the animated scene uses explicit TRS.
Both encodings are valid under the [glTF node transformation specification](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#transformations).
The previously checked [matrix/TRS diagnostic variants](diagnostics/smila-export-converted-rest-qa.json)
also passed Blender, but no viewer comparison has isolated this difference as
the cause. Consequently no speculative compatibility workaround was applied.
All influence sets are retained, including the 18 sets in `smila_posing_02`.

## Renders and reproduction

Unmodified imported geometry, without subdivision:

- [Dialogue rig](../build/skin-qa-20260913/renders/dialogue01.lws.rig-10000000.png)
- [Smila export converted rig](../build/skin-qa-20260913/renders/smila_export_converted.lws.rig-10000000.png)

The renders are local QA artifacts in the ignored `build/` directory. To
reproduce the checks using the existing scripts:

```powershell
$qaBatch = 'output/batch-20260913-173025/packages'
$qaBlender = 'C:/Program Files/Blender Foundation/Blender 4.2/blender.exe'
python -X utf8 tests/check_rest_skin_blender.py "$qaBatch/smila-by-moebius/gltf" "$qaBatch/quatuor/gltf/work/3d/dialogue01.lws.rig-10000000.gltf" --blender $qaBlender --report build/skin-rest-qa.json --renders build/skin-rest-renders
python -X utf8 tests/check_autonomous_blender.py "$qaBatch/smila-by-moebius/IR/Smilla_IK.lws" --blender $qaBlender --report build/skin-animation-qa.json
```
