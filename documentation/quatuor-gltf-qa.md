# Quatuor glTF output simplification — v0.8.2

The converter no longer writes unbound rest-skeleton copies by default. Each
`test_anim.lws` / `dialogue01.lws` now has its main scene `.gltf` and `.bin`,
without four extra `*.rig-<item ID>` pairs. Standalone object exports remain
available and are shared between scenes in the same batch project.

The new `--gltf-rigs skins|all` option defaults to `skins`. Actual explicit
weight-map skins remain exported. `all` restores the separate unbound rest
skeletons for inspection. Bone counts, native influence parameters, original
weight maps, IK and animation keys remain in the IR. The manifest retains each
rig's diagnosis with `status: skeleton-only`, `export_status: omitted-by-policy`
and null output URIs. Omission is distinct from a blocked rig.

The native animation path generates rest rigs as temporary inputs and applies
the requested policy after evaluation, including when evaluation fails. Animated
derivatives are retained. This change does not implement procedural skinning or
add animation support to Quatuor; its existing limitations remain reported.

Validated on 2026-09-12:

- New output: `output/batch-20260912-140555`, compared with
  `output/batch-20260912-132032`.
- 63 conversions with unchanged statuses: 7 complete, 56 partial, no failures.
- 63 glTF/binary pairs instead of 84. The 21 omitted rest-rig pairs eliminate
  42 generated files and 11,690,541 bytes of duplicated output.
- All 63 original IR documents and buffers are byte-identical to the baseline.
  All retained OBJ/MTL and glTF geometry is unchanged; the glTF generator version
  advances to v0.8.2.
- Khronos glTF Validator 2.0.0-dev.3.10: 63 files, zero errors, zero warnings,
  one informational message. Individual reports are under `logs/gltf-validation/`
  to keep QA sidecars out of the exported model directories.
- All eight regression suites pass in Release and AddressSanitizer: 127 tests
  per build. Checks cover opt-in skeletons, unchanged IR, default explicit
  skinning, batch publication and retention of sampled native animation while
  pruning its intermediate unbound rest rig.

The rebuilt Windows executable is staged at `bin/win64/lwconvert.exe`:
SHA-256 `77af886c9316c09d3b749718216349ccd2e2e2f835909471aaa0db2e432c7e1b`.

```powershell
# Default compact output, retaining the project's source hierarchy.
.\convert_content.bat --project quatuor

# Optional unbound rest-skeleton copies.
.\convert_content.bat --project quatuor --gltf-rigs all

# Compare a new compact batch with the previous full output.
python -X utf8 tests/check_quatuor_hierarchy.py output/batch-20260912-140555 --baseline output/batch-20260912-132032
```

Evidence: [IR, geometry and omission comparison](diagnostics/quatuor-gltf-simplification.json),
[glTF validation totals](diagnostics/quatuor-gltf-simplification-validation.json).
