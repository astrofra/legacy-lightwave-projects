# Cyber-bot n-gon QA — 13 September 2026

The missing faces below the shoulders of `bot_15.lwo.gltf` were a converter
triangulation bug. Version 0.15.1 restores both faces in OBJ and glTF.
The source file and native IR geometry remain unchanged.

The investigated output was
`output/batch-20260913-212412/packages/cyber-bot/gltf/bot_15.lwo.gltf`.
The corrected, independently generated package is
[`output/qa-cyber-bot-20260913`](../output/qa-cyber-bot-20260913), with
[glTF](../output/qa-cyber-bot-20260913/gltf/bot_15.lwo.gltf) and its adjacent
`.bin`. The Windows converter in `bin/win64/lwconvert.exe` is updated, so
subsequent `convert_content.bat` runs include the fix.

## Cause and correction

Source polygons 339 and 759 (zero-based IR indices) have 12 corners each,
including a short, exactly retraced edge:

- Polygon 339: point indices `343 -> 172 -> 343`.
- Polygon 759: point indices `777 -> 638 -> 777`.

The two endpoints of each excursion are approximately `7.0e-5` object units
apart. These excursions enclose no area. Ear clipping previously retained
them as if they were ordinary hole bridges, allowing intermediate triangles
to cross the repeated corner and leaving a negatively oriented final
triangle. The final validation then discarded the entire polygon, creating
the two large openings in the screenshot.

The C tessellator now removes exactly retraced excursions from its working
contour after checking the complete boundary for invalid crossings. The
pass also handles nested excursions. It compares original point identities,
without welding distinct points or using a distance threshold. Both the
original IR contour and the surviving corners' attribute references are
preserved. Real hole bridges, boundary crossing checks, winding checks and
the final projected area coverage check remain in effect.

## Validation

| Measurement | Before | After |
| --- | ---: | ---: |
| Exported glTF triangles | 17,356 | 17,372 |
| Rejected face polygons | 73 | 71 |
| Triangles for each restored shoulder face | 0 | 8 |
| Restored projected face area coverage | 0% | 100% |

Every previously exported polygon retains exactly the same ordered source
corner mappings, positions, normals, UVs where present, and material
assignments. Only polygons 339 and 759 were added. The source copy and
`geometry.bin` are byte-identical between the original and corrected packages.

Blender 4.2 imports both actual glTF files. All 17,372 corrected triangles
match their exported buffer coordinates exactly after axis conversion.
The Khronos validator reports zero errors and zero warnings on the corrected
glTF, including its external buffer.

Three new end-to-end regressions cover winding and cyclic starting corner
variations, concave cutouts, nested excursions with a real bridged hole,
unchanged IR contours, glTF source-corner and UV/VMAD bindings, and continued
rejection of crossings and distinct coincident points. All 11 regression
groups pass in Release and AddressSanitizer builds.

The [machine-readable report](diagnostics/cyber-bot-ngons-20260913-qa.json)
contains hashes, restored polygon indices, area coverage and remaining rejects.

## Remaining limitations

The conversion remains `partial`: 40 rejected faces have zero area or a
cancelling boundary, and 31 have boundaries that intersect, overlap or touch
after planar projection. The latter include strongly nonplanar faces; this
does not establish that their original 3D boundaries are corrupt. All remain
in the IR. Their rejection behavior is unchanged by this fix.

The two restored faces are also nonplanar, but their projected contours are
valid after removing the empty excursion. Triangulation remains a planar
projection approximation. This QA does not claim identical tessellation or
shading to LightWave. Subdivision is not baked.

## Local visual comparison and reproduction

- [Before, shoulder close-up](../_tmp/cyber-bot-qa/before-shoulder.png)
- [After, shoulder close-up](../_tmp/cyber-bot-qa/after-shoulder.png)
- [Corrected full model](../_tmp/cyber-bot-qa/after-full.png)

These local images are actual glTF imports rendered in Blender with the same
camera, Workbench lighting and materials, without geometry repair or subdivision.
The case-specific comparison scripts and images are in ignored
`_tmp/cyber-bot-qa`. Recreate a fresh package with:

```powershell
bin/win64/lwconvert.exe convert content/cyber-bot/bot_15.lwo --content-root content/cyber-bot --output output/qa-cyber-bot-new
ctest --test-dir build -C Release --output-on-failure
```

Exit code 2 is expected because of the remaining partial conversion features.
No LightWave runtime was needed for this correction or its checks.
