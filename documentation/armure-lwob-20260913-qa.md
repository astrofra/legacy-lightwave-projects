# Armure LWOB geometry QA — 13 September 2026

The distorted torso and misplaced head in `MAN_Test_armure_---.lwo.gltf`
are already present in the source `content/1st-year-student-amiga-project/armure/MAN Test armure ---`.
The other variant, `MAN Test armure`, produces an intact full character.
Neither object contains a skin or animation. No converter or original asset
was changed for this investigation.

The checked files are the actual v0.15.0 outputs in
`output/batch-20260913-191158/packages/1st-year-student-amiga-project/gltf/armure`.
The [machine-readable report](diagnostics/armure-lwob-20260913-qa.json)
records source, IR, glTF, buffer, native oracle and local artifact hashes.

## Evidence

| Measurement | `MAN Test armure` | `MAN Test armure ---` |
| --- | ---: | ---: |
| Source points | 1,920 | 3,538 |
| Source polygons, including lines | 3,006 | 4,283 |
| Highest referenced point index, zero-based | 1,919 | 2,047 |
| Unreferenced source points | 0 | 1,490 |
| glTF triangles | 3,006 | 5,569 |
| Polygons omitted after triangulation failure | 0 | 75 |
| Maximum emitted corner position error against source | 0 | 0 |
| Maximum actual Blender glTF triangle corner error | 0 | 0 |

A separate Python decoder of the raw big-endian LWOB `PNTS` and `POLS`
chunks matches every IR point, polygon index and signed surface assignment.
Every emitted glTF corner matches its source polygon/corner/point mapping
exactly after the documented Z reflection. Original triangles retain the
same three source point indices; the long crossing edges are present in
the original `POLS` data.

LightWave 9.6 ScreamerNet independently loaded byte-identical copies of
both originals in a temporary scene with identity transforms and subdivision
disabled. A read-only SDK capture reports exactly the same point order and
polygon connectivity. Captured base and world positions match every source
point at float32 precision. The largest decimal text serialization difference
is `6.01e-8` units. Six native corner normals are undefined in the damaged
object; the topology check reads point and polygon records independently of
the animation capture reader's unit-normal requirement.

Blender 4.2 imports both actual batch glTF files successfully and reproduces
the reported deformation on the `---` variant. The Khronos validator also
reports zero errors and zero warnings on all 19 glTF files under `gltf/armure`.
That validates the outputs' structure; it does not repair their input topology.

## Likely origin of the damage

In the `---` file, every point index from 0 through 2,047 is referenced, while
none of the 1,490 points from 2,048 through 3,537 is referenced. This exact
power-of-two boundary suggests historical truncation or wrapping of indices
to 11 bits. This is an inference from the asset, not an identification of the
software or operation that caused it. The file does not provide enough
information to restore the missing high bits unambiguously.

For example, source polygon 2,715 connects points 0 and 2,046 across 25.40
source units. The entire width of the object is only 21.60 units. The glTF
also retains the 1,490 unreferenced points as a point primitive, explaining
the dots around the malformed mesh in the screenshot.

The 75 untriangulatable polygons remain preserved in the IR; they are omitted
from OBJ and glTF and the conversion is marked `partial`. Undefined surfaces
also remain reported. No automatic topology repair was applied: choosing
replacement indices would change the source interpretation without a
reliable reference.

## Local visual comparison and reproduction

- [Intact variant, actual glTF imported in Blender](../_tmp/armure-qa/0-gltf.png)
- [Damaged variant, native SDK geometry displayed in Blender](../_tmp/armure-qa/1-native.png)
- [Damaged variant, actual glTF imported in Blender](../_tmp/armure-qa/1-gltf.png)

The images use neutral material and Workbench lighting without subdivision
or repair. The native SDK polygon mesh is tessellated by Blender for display;
this is not a LightWave render. Numerical topology comparisons are performed
before tessellation. Rendering differences on invalid polygons are expected.

Local investigation scripts and native captures are in the ignored directory
`_tmp/armure-qa`. With those artifacts present:

```powershell
python -X utf8 _tmp/armure-qa/check.py
& 'C:/Program Files/Blender Foundation/Blender 4.2/blender.exe' --background --factory-startup --disable-autoexec --python-exit-code 1 --python _tmp/armure-qa/render.py
python -X utf8 tests/check_gltf_validator.py output/batch-20260913-191158/packages/1st-year-student-amiga-project/gltf/armure --validator build/tools/gltf-validator-2.0.0-dev.3.10/gltf_validator.exe --report _tmp/armure-qa/validator.json
```

The converter remains fully independent of LightWave. The native runtime
was invoked only to verify how it reads these two original objects.
