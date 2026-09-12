# Quatuor source hierarchy QA

This records the layout-only baseline, before v0.8.2 made unbound rest-rig
exports optional. See the [subsequent glTF simplification QA](quatuor-gltf-qa.md)
for the current default output. Add `--gltf-rigs all` to reproduce the full set
of 84 glTF files below with the current converter.

Validated on 2026-09-12 with the unchanged `bin/win64/lwconvert.exe` v0.8.1
and the Python batch publisher using layout 0.3.

Quatuor contains 63 supported inputs and twelve repeated basenames across
different directories. The former flat publisher renamed `work/3d/01.lws` to
`01.lws-2.gltf`, and `work/3d/subdiv/AVallin.lwo` to `AVallin.lwo-2.gltf`.
Those names obscured the distinction between working versions and subdivision
cages. The existing links were valid; the problem was loss of directory context.

The batch now retains each input's project-relative path under `IR/`, `obj/`
and `gltf/`, as well as in logs. For example:

```text
packages/quatuor/
    gltf/01.lws.gltf
    gltf/work/3d/01.lws.gltf
    gltf/work/3d/AVallin.lwo.gltf
    gltf/work/3d/subdiv/AVallin.lwo.gltf
    obj/work/3d/01.lws.obj
    obj/work/3d/01.lws.mtl
    IR/work/3d/01.lws/manifest.json
    IR/work/3d/01.lws/scene.json
```

The source hierarchy is relative to the selected project root. A new repeatable
`--project` selector converts one top-level project without treating its internal
directories as separate projects. Dependencies shared by scenes still have one
published object per source path. Rig and animation derivatives remain beside
their scene. Rendered texture copies remain local to each export directory.
Ancillary files and empty source directories are not replicated. The detailed
collision and external-dependency rules are in [the layout documentation](converter.md#output-layouts-02--03-and-lwir-01).

The new batch is `output/batch-20260912-132032`; the comparison baseline is
`output/batch-20260912-121950`. Results:

- 63 inputs converted, no failures: 7 complete and 56 partial, exactly as before.
- All 63 input paths retain their hierarchy in IR, OBJ, glTF and logs.
- All 63 native IR documents, archived sources and IR binary buffers are
  byte-identical to the previous output.
- All 63 OBJ/MTL pairs are identical except for the rewritten OBJ `mtllib` line.
- All 84 glTF files, including 21 additional rig exports, have identical binary
  buffers and identical JSON after excluding the relocated buffer URI.
- Khronos glTF Validator 2.0.0-dev.3.10: 84 files, zero errors, zero warnings,
  one informational message, with referenced resources validated.
- All eight Release regression suites pass (125 tests), including nested
  texture relocation, shared scene assets, filename/directory conflicts,
  mapped dependencies and preserved native animation captures.

The 56 partial conversions retain their previous limitations. This change does
not increase the supported rendering or animation subset. Direct C conversion
still produces layout 0.2; source hierarchy is applied by the batch publisher.
No C code or Windows binary changed.

Reproduce the conversion and comparison from the repository root:

```powershell
.\convert_content.bat --project quatuor --gltf-rigs all
# Substitute the newly printed batch directory below.
python -X utf8 tests/check_quatuor_hierarchy.py output/batch-20260912-132032 --baseline output/batch-20260912-121950 --report documentation/diagnostics/quatuor-hierarchy-comparison.json
python -X utf8 tests/check_output_layout.py output/batch-20260912-132032
build/tools/gltf-validator-2.0.0-dev.3.10/gltf_validator.exe output/batch-20260912-132032/packages/quatuor/gltf
ctest --test-dir build -C Release --output-on-failure
```

Evidence: [source/output comparison](diagnostics/quatuor-hierarchy-comparison.json),
[published-link audit](diagnostics/quatuor-layout-validation.json),
[glTF validator totals](diagnostics/quatuor-gltf-validation.json).
