# Butterfly tank scene QA — v0.4.1

The seven LWS files in `content/butterfly-tank/` now produce static OBJ/MTL and
glTF scene exports. Previously their IR and individual objects were generated,
but scene exports were blocked while evaluating the butterfly wing transforms.

## Diagnosis and correction

All seven scenes are LWSC 3, at 30 FPS, and resolve their object dependencies.
The historical `tank/01.lwo` and `tank/butterfly.lwo` paths were not the blocker.
There were three independent problems:

- The left wing has an `ItemMotionHandler 1 LW_Follower` plugin. Previously every
  item motion plugin blocked scene evaluation.
- The right wing's heading, pitch and three scale envelopes declare zero keys,
  although each contains one valid key. The parser retained the keys but marked
  the transform unsupported because the counts disagreed.
- Its bank envelope contains 40 TCB keys at negative times, with repeat behavior
  before and after the interval. Producing a snapshot at the scene's first frame
  requires repeat handling and TCB interpolation.

The parser now samples validated actual keys while retaining declared counts,
native parameters and source bytes. Five count mismatches per scene are reported
in the IR and manifest, and keep the package status `partial`.

The shared transform evaluator now handles TCB spans, including tension,
continuity, bias and unequal key spacing. Repeating a single-key channel gives
its constant value. Unsupported Hermite/Bezier spans and other extrapolation
behaviors still produce explicit errors when sampling requires them.

The Follower support is deliberately a limited preview of the observed wing
relationship. Its qualified payload has only bank enabled, bank mapped to bank,
multiplier -1, offset zero, and no delay, randomization or path slip. The items
must have matching parents and pivots, the source must rotate only in bank, and
the follower must have neutral native rotation. The evaluator sets the
follower's bank to the opposite source value at the snapshot time. Missing
sources, cycles, other settings and unqualified transforms still block the
scene export. No native plugin is executed.

This is an approximation of the legacy Follower configuration, not a general
implementation or a comparison against LightWave's plugin execution. The IR
records `follower.profile = mirrored-bank-preview`, source item and plugin index;
the original plugin byte range remains available. The manifest separately
counts recognized preview profiles and plugins left opaque.

## Results on the real files

Run: `output/batch-20260911-102042`, generated with `lwconvert 0.4.1`.
Exports are under `packages/butterfly-tank/{IR,obj,gltf}/`.
All seven scene OBJ and glTF paths are present, with empty scene-export issue
strings and zero unresolved object instances.

| Scene | Snapshot frame | Triangles after Blender glTF import | Mesh instances |
| --- | ---: | ---: | ---: |
| `01.lws` | 0 | 16,272 | 7 |
| `01_butterfly.lws` | 0 | 4,388 | 3 |
| `02.lws` | 0 | 24,464 | 8 |
| `02_NAB.lws` | 182 | 24,464 | 8 |
| `02_butterfly.lws` | 0 | 16,272 | 7 |
| `02_grass.lws` | 0 | 16,272 | 7 |
| `03.lws` | 0 | 16,406 | 7 |

The batch also exports the three standalone LWO files. The five other entries
are images and are skipped as conversion entrypoints.

Validation performed:

- 75 regression tests pass in Release and with MSVC AddressSanitizer. The seven
  new scene-evaluation tests cover analytical TCB values, unequal times,
  continuity/bias, overshoot, repeat at negative times, single-key repeat,
  incorrect counts and qualified/unqualified Follower dependencies.
- All seven real LWS conversions also pass AddressSanitizer with scene exports.
- Khronos glTF Validator `2.0.0-dev.3.10` checks all ten published glTF files and
  their resources: zero errors, warnings, information messages or hints.
- Blender 4.2 imports all seven scene glTF files. Every world-space triangle is
  matched once against the corresponding OBJ, including parent transforms and
  instances. The largest matched corner distance is approximately `5.44e-6`
  source units, below the per-scene tolerance. This checks export/import
  consistency, not native LightWave animation or shading fidelity.
- Layout checks pass for ten native sources, ten OBJ/MTL pairs and ten glTF/buffer
  pairs, including published links and archived source/image hashes.

Machine-readable reports and two diagnostic geometry renders are in the batch's
`diagnostics/` directory: `gltf-validator.json`, `layout.json`,
`blender-scenes.json`, `triangulation.json`, `01_butterfly-geometry.png` and
`01_butterfly-wing-geometry.png`.

## Remaining visual limitations

The scene files now export, but these are static base-geometry previews:

- Both wing clip maps and their PSD references are retained in the IR. They are
  not applied to target materials yet; the wings appear as solid rectangular
  surfaces. LWO2 texture blocks are also retained without target evaluation.
- `solalpha.jpg`, referenced by `01.lwo` and `01_closer.lwo` through
  `I:fra/3D/tank_pilot/solalpha.jpg`, is absent from the permitted search subtree.
- Sasquatch and other opaque plugins remain unevaluated. Visibility settings,
  dissolves, native cameras/lights and animation are not reproduced in these
  static exports.
- Four faces in each tank object remain omitted by the existing triangulator:
  primitives 14 and 1535 have zero projected area; 11890 has crossing/overlapping
  projected edges; 11892 ends in a degenerate triangle. The latter two are
  nonplanar. These primitives remain intact in the IR. Manifest counts can be
  larger because they include individual-object and scene exports together.
- Flat normals and approximate materials still differ from the native render.

No source files were edited. The Release executable is staged in
`bin/win64/lwconvert.exe`; no additional dependency was introduced.

## Reproduce

```powershell
cmake --build build --config Release
ctest --test-dir build -C Release --output-on-failure
python -X utf8 tools/batch_convert.py --content content/butterfly-tank
```

The batch command creates a new timestamped directory. Its partial status is
expected for the limitations above. Inspect each conversion manifest's
`scene_obj`, `scene_gltf`, `scene_obj_issue` and `scene_gltf_issue` to distinguish
an exported partial scene from a blocked scene snapshot.

Envelope field semantics follow the archived
[NewTek LWSC specification](https://documentation.help/LightWave/lwsc.html).
The interpolation uses the Hermite basis and Kochanek–Bartels tangent equations
described by [Nils Pipenbrinck](https://www.cubic.org/docs/hermite.htm).
Follower qualification comes from the repository's seven native payloads;
unverified plugin behavior is not inferred beyond the stated preview profile.

## Follow-up: local LightWave reference archive

The owner supplied `_tmp/_extern/` as a local reference for this non-commercial
thesis project. Read-only inspection found Layout and ScreamerNet binaries in
`LightWave/LW9/Programs/` (x86) and `LightWave/LW9.6/Programs/` (AMD64), plus
`Plugins/animate/chanfollow.p` in both versions. Both plugin binaries contain the
`LW_Follower` server name. The ScreamerNet binaries identify builds 1165 and
1539 respectively. Their presence has been verified; running these hosts and
reproducing native renders has not yet been qualified.

The LW9 SDK also contains Ernie Wright's standalone envelope evaluator, dated
16 November 2000, at
`SDK/sample/Layout/ChannelFilter/envelope/interp.c`. An independent local harness
compiled this original file directly and compared it with `lwconvert`:

- 512,000 samples across 500 synthetic TCB envelopes, including unequal key
  spacing, varied tension/continuity/bias and reset/constant/repeat behavior.
  The largest difference divided by the channel's key-value scale was
  `6.70e-7` (maximum absolute difference `1.03e-5`). Inputs were binary-exact
  fractions to isolate arithmetic from decimal input rounding.
- 223,590 samples of supported native channels from the seven scenes, with
  257 times per channel over each scene's render interval. The largest absolute
  discrepancy was in wing bank: `0.0116174` radians, approximately **0.666°**.
- A second pass rounded the converter's inputs to the same single precision
  used by the SDK sample. The largest discrepancy fell to `0.000101268`
  radians, approximately **0.00580°**. This strongly implicates input precision,
  amplified by repetition of the negative-time wing envelope.

These results support the TCB formulas but do not establish bit-for-bit native
LightWave agreement. The converter's double-precision evaluation is unchanged;
Layout measurements are needed before choosing a legacy rounding policy.
This comparison does not evaluate `LW_Follower`, clip maps or Sasquatch.

The [numerical report](diagnostics/butterfly-sdk-envelopes.json) records the
reference hashes and worst samples. The local harness and its build are in
`_tmp/sdk-envelope-qa/`; the archive, original SDK source and original binaries
remain outside the distributed converter.
