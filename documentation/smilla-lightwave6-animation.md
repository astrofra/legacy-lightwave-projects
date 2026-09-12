# Smilla: LightWave 6 reference and skeletal glTF — v0.10.0

The reference scene is `content/smila/Smilla_IK.lws`, opened with `content`
as LightWave's content directory. It resolves to `content/smila/smila.lwo`.
Do not substitute `_smila.lwo`: that is a different mesh with different maps.

The reviewed output is
`output/smila-lw6-animation-20260912/gltf/Smilla_IK.lws.anim-10000000.gltf`,
with its adjacent `.bin`. It preserves an actual glTF skin, inverse bind
matrices and C-derived `JOINTS_n`/`WEIGHTS_n`. The 0–40 frame animation has
41 samples at 25 fps; 17 of the 30 bones have changing local tracks. The
additional joint is the object anchor. There are no morph targets in this
export and no subdivision has been baked.

## Findings

* **Runtime-dependent weights.** Fourteen bones refer to weight maps absent
  from the loaded mesh. Controlled LightWave 6 build 446 measurements show
  procedural fallback for missing assigned maps; LightWave 9.6 instead gives
  those maps zero influence. `--skin-profile lightwave6` selects the former
  behavior explicitly. The default `lightwave96` retains its missing-map
  diagnostic. An existing but empty map contributes zero in both profiles.
  The original map assignments remain in the scene IR; fallback counts and
  calculated weights belong to the separate derived-skin record.
* **IK capture timing.** In this LW6 scene, ItemInfo matrices alone disagree
  with rendered bone deformation. The read-only motion observer runs with
  `LWIMF_AFTERIK` and records the resulting local TRS. Protocol 3 retains those
  records and the original SDK matrices separately. The reader composes the
  hierarchy, including pivots, from the evaluated TRS.
* **Actual deformed positions.** Old MeshInfo calls do not provide the same
  cage contract as 9.6: freezing Smilla duplicates points and triangulates
  boundaries. The LW6 path retains unfrozen source topology and joins final
  positions by point ID from a read-only `LWDMF_WORLD` displacement observer.
  Every exported mesh is checked against original point positions and polygon
  connectivity before use. Missing observations cause an error.
* **Long path failure.** Build 446 crashed inside its renderer when launched
  from the nested package directory. The identical working scene succeeds in
  a short directory. LW6 evaluations now run in an isolated temporary folder
  with relative scene/config arguments; inputs, hashes, log and captures are
  archived in the package. No installed LightWave configuration is modified.
* **Plugins.** Empty `.SpreadsheetStandardBanks` blocks are removed from the
  working copy as interface state. For the user's loading configuration,
  `JointMorph` and `LW_MorphMixer` were explicitly skipped and recorded in the
  audit. The before/after plugin screenshots supplied by the user show the
  same run-cycle pose; plugin omission alone does not explain the earlier
  skeletal export failure. Unknown animation plugins still require an explicit
  omission or supported implementation.

The motion and displacement observer contracts are described in NewTek's
[ItemMotionHandler](https://documentation.help/LightWave/itemmot.html) and
[DisplacementHandler](https://documentation.help/LightWave/displace.html)
SDK documentation. Runtime differences above are local measurements, not
assumptions of identical behavior across LightWave versions.

## Verification and limits

[Blender playback QA](diagnostics/smilla-lw6-animation-blender-qa.json) evaluates
the imported armature at every one of the 41 frames. It compares vertices by
index against captured bone poses applied to the published weights. The
playback discrepancy is below 0.000001 scene unit. Native cage comparison
is separate: RMS is approximately **0.00718**, maximum **0.07693** scene unit.
The reference uses meter-scale coordinates. Four source volume-correction
settings remain outside fixed linear weights; deformation around hips and
joints is approximate. These results do not establish perfect skinning parity.

[Khronos validation](diagnostics/smilla-lw6-animation-gltf-validation.json)
checks the published JSON and binary resources. Blender Workbench images in
the output's `qa/` directory cover frames 0, 5, 10, 20, 30 and 40. They render
the unsubdivided cage, so smoothness and material appearance differ from the
LightWave subdivision viewport.

The eight synthetic missing/empty/painted-map cases are archived in
[`tests/fixtures/lightwave6_skin_oracle.json`](../tests/fixtures/lightwave6_skin_oracle.json)
with source, host, helper and capture hashes. Standalone C output matches their
native positions within 0.000001 scene unit. The tests require no LightWave.

## Reproduce

Build the optional 32-bit helper using an installed SDK (not vendored):

```powershell
cmake -S . -B build-lw6 -A Win32 -DBUILD_TESTING=OFF -DLWCONVERT_LIGHTWAVE_SDK=S:/works/legacy-lightwave-projects/_tmp/_extern/LightWave/LW9/SDK
cmake --build build-lw6 --config Release --target lw_capture
```

Use a new output directory on each run:

```powershell
python tools/export_lightwave_animation.py content/smila/Smilla_IK.lws --content-root content --output output/smila-lw6-review --runtime lightwave6 --lightwave-root E:/__very_old_stuff_/archive-stuff_cd/LW6/Programs/LightWave_Support --capture-plugin build-lw6/Release/lw_capture.p --skip-plugin JointMorph --skip-plugin LW_MorphMixer --animation-mode skin --start-frame 0 --end-frame 40
```

`auto` retains bound skins when available; `skin` requires a bound skin;
`morph` selects the older evaluated-cage export. LW6 protocol 3 has no evaluated
corner normals: its skeletal export keeps the C rest normals and lets glTF
skin them, while the older morph exporter refuses unsupported shading capture.

The **weight calculation is standalone C**. This reference's **IK poses are
still evaluated by the installed LightWave 6** during export. Playing or editing
the resulting glTF needs no LightWave. Original motion keys and IK settings
remain in native IR; evaluated poses and their provenance are additional data.
This does not implement an independent C IK solver. The more complex
`smila_run_cycle.lws` rig and its plugins are not qualified by this Smilla_IK QA.

## Inferring the missing content directory

The C collector scores candidate roots by the number of distinct relative
object references they satisfy exactly. Cloned objects and multiple layers
referencing the same path count once. Candidates come from the supplied root,
the scene directory, its parent, and full reference suffixes found in the object
index. If references remain unresolved, the collector may scan one level above
the scene and its descendants. Equal best scores remain ambiguous. Explicit
`--map` rules retain priority, and original paths remain in IR.

`manifest.json` records `content_root_inference`: search scope, selected root,
distinct/matched reference counts and candidate scores. Once a root wins,
fallback searches stay within it to avoid combining unrelated projects.

For `animation-goeland/scene5_personnage_V/Scenes/Scene_5_personnage_V.lws`,
the inferred root is `scene5_personnage_V`; all six object instances resolve to
the sibling `Objects/uppercut_jab_1.lwo`. This also required parsing LWSC 5's
explicit object/bone IDs, preserving nonsequential parenting and layer requests.
The object glTF is produced, but the complete animated scene remains partial
because its motion controllers are not evaluated. The optional native bridge
currently accepts LWSC 1/3, not LWSC 5.
