# Procedural and hybrid bone weights — v0.9.0

The C converter now derives editable, fixed skin weights from LightWave bone
influences. It handles procedural bones and bones whose influence is multiplied
by an assigned WGHT map. The result is an explicitly labelled approximation,
`lightwave96-procedural-weights-0.1`, measured against LightWave 9.6 x64 build
1539. Agreement on these experiments does not establish identity with every
LightWave version or every deformer.

Conversion requires only `lwconvert.exe`. There is no SDK, ScreamerNet, Python,
Blender or external process in the C weight calculation. The optional SDK helper
and native runtime are development instruments, kept outside the converter.
No proprietary implementation is copied into the converter. The weight algorithm
is based on controlled input/output measurements.

## Measured algorithm

Implementation: [src/skin.c](../src/skin.c). All calculations use the source
object's rest coordinates and composed bone rest transforms. For each point:

1. Compute distance `d` to each active bone's finite segment, extending along
   its local +Z by `BoneRestLength`. Inactive bones remain in the hierarchy but
   do not influence vertices.
2. A procedural field is `strength / (d^p + 1e-24)`. Multiply strength by rest
   length when `ScaleBoneStrength` is enabled, and by the vertex's WGHT value
   when a map is assigned. Sparse map entries contribute zero. Missing assigned
   maps are an error in the default LightWave 9.6 profile. The explicit LightWave 6
   profile follows its measured procedural fallback (see the v0.10.0 addendum).
3. For a normalized map-only bone, use the map value directly: its geometric
   field, strength, length scaling and range are ignored. This also works when
   such a bone is mixed with procedural bones.
4. A limited bone contributes nothing beyond its outer radius. Inside a positive
   inner radius, its field becomes `strength * map * 1e28 * S(1-d/inner)`, with
   `S(t) = t²(3-2t)`. Between the radii, retain the ordinary field, then multiply
   its **normalized weight** by `S((outer-d)/(outer-inner))`.
   Equal radii are a supported hard boundary; the exact boundary keeps the
   ordinary field without division by zero.
5. With `FasterBones`, subtract the fifth-largest field from every field and
   clamp negative results to zero. Then discard fields below `FLT_EPSILON` and
   normalize. The threshold comes **after** that subtraction. Merely choosing
   the four nearest bones or retaining the four greatest original weights does
   not reproduce the measurements. Exact ties can leave the point unmoved.
6. Outer-range attenuation can leave total bone weight below one. Assign the
   residual to an identity object anchor. Redistributing it among the bones
   would over-deform the mesh. Entirely uninfluenced points use that anchor too.

`p` is the loaded `BoneFalloffType` value in the measured scenes, with the
qualified range currently 0 through 9. Notably, the older SDK documentation
describes an options index with powers of two, whereas the 9.6 runtime loaded
these LWS values as direct exponents. The implementation follows the captures,
including tests for all ten values. The SDK's descriptions of the influence
flags remain useful background; see the [Bone Info API](https://documentation.help/LightWave/boneinfo.html)
and [Layout commands](https://documentation.help/LightWave/layout.html).

The constants and ordering above are inferred from controlled deformations,
including small and large translations, rotations, nonuniform bone scale,
parented bones, overlapping inner ranges, sparse maps, distant points and
Faster Bones threshold cases. They are not claimed to be published source code.

## IR and glTF

Original `scene.json`, `animation.bin`, object WGHT maps and copied source files
remain unchanged. A successful procedural conversion adds
`IR/<scene>/skin-<owner item>.json`, referenced by the conversion manifest's
`gltf_rigs[].derived_skin`. Batch publication keeps it beside `scene.json` in
the mirrored source hierarchy.

This derived IR records the profile, approximation flag, owning object item,
original scene/object SHA-256 hashes, ordered joint item IDs, and per-source-point
`[joint_index, weight]` pairs. Joint zero is the identity object anchor. Point
indices are global indices in the native object; unselected layers have empty
rows. Original bone parameters remain authoritative and independently available.

The glTF contains standard `skins`, inverse bind matrices, `JOINTS_n` and
`WEIGHTS_n`. Weights follow source points through triangulation, UV seams and
normal splits. All resulting influences are retained; multiple VEC4 sets also
allow an anchor in addition to the four Faster Bones influences.

The manifest distinguishes `procedural-weight-skin-approximation` from
`explicit-weight-map-skin`. It records converted/unevaluated procedural bone
counts and omitted volume corrections. glTF extras repeat the approximation
and limitations. The existing `--gltf-rigs skins` default keeps actual skins and
omits unbound skeleton copies. The usual scene snapshot remains a separate
export; these skin files are object-local rest rigs, not animated scene exports.

## Measured corpus results

The QA applies **captured native bone matrices** to the exported glTF weights
and inverse binds, then compares with captured native vertex positions. Thus it
tests the skin calculation independently of a future C IK solver. Errors below
are Euclidean distances in native object units, at exported corners; duplicated
corners are included in the RMS. Source subdivision stays disabled in all tests.

| Scene and evaluation | Rigs | Frames | Maximum error | RMS |
|---|---:|---|---:|---:|
| Quatuor `test_anim.lws`, unchanged deformation | 3 | 0, 2 | 6.48e-7 | 1.25–1.27e-7 |
| Quatuor `dialogue01.lws`, unchanged deformation | 4 | 0, 2 | 5.05e-7 | 1.48–1.64e-7 |
| Smila `smila_run_cycle.lws`, original morphs and volume corrections | 1 | 0, 8, 16 | 0.05526 | 0.006827 |
| Same Smila scene, morphs and volume corrections disabled **only in the oracle copy** | 1 | 0, 8, 16 | 1.81e-6 | 1.16e-7 |

The Smila difference must not be read as full visual fidelity: its `relax` morph
is active at weight 1, and eight active bones have nonzero volume-correction
parameters. Neither effect is represented by this fixed-weight export. The
isolated experiment confirms the bone influence calculation while the original
experiment quantifies the remaining discrepancy. Reports preserve both runs,
their source/program hashes and every scene override.

The fourth `test_anim` character remains unbound: its map-only `Head` bone has
normalization disabled. Controlled tests show such a hybrid can sum to more
than one (for example 1.75). Silently normalizing it would change the deformation;
the current nonnegative, normalized glTF profile declines it.

Smila has several rig revisions. `Smilla_IK.lws` assigns painted maps `body`,
`head`, `hat`, `arms`, `hands`, `leg_right` and `leg_left`, with
`BoneWeightMapOnly 0`: this is hybrid skinning. The referenced `smila.lwo`
currently contains WGHT maps `arms`, `body`, `head` and `legs`, leaving 14 bone
assignments unresolved. Its skin remains blocked rather than guessing aliases.
In contrast, `smila_run_cycle.lws` loads `smila_rig_02.lwo`, which contains no
WGHT maps; its 75 active bones use procedural influences.

Detailed [native comparison report](diagnostics/procedural-skinning-qa.json) and
[Khronos validator report](diagnostics/procedural-skinning-gltf-validation.json).
The Quatuor/Smila batch contains 96 conversions, with no failed source conversion
and 26 derived skins among 115 glTF files: the validator reports zero errors
and zero warnings. The published hierarchy and source hashes also pass the
[layout audit](diagnostics/procedural-skinning-layout-qa.json).
Partial conversions still report existing material, animation and other limits.
The reviewed output is `output/batch-20260912-150015/`.

### Rest-pose import and Windows 3D Viewer

Windows 3D Viewer 7.2602.8012.0 was reported to display exploded geometry for
`smila_export`, `smila_export_converted` and `smila_rig` rest-skin exports.
The actual files from `output/batch-20260912-151513/` were checked in Blender
4.2.0: all eight Smila rest rigs import successfully. Evaluated armature vertices,
compared by vertex index against the exported undeformed positions, have a maximum
error below 1e-6 object units. Independently evaluating the glTF node hierarchy
and inverse binds gives rest-position errors below 1.4e-7. See the
[Blender import report](diagnostics/smila-viewer-blender-qa.json), including file
hashes. The ordinary `Smilla_IK.lws.gltf` snapshot shown intact in the user report
is not a successful skin export; its missing weight maps remain unresolved.

These results point to a viewer compatibility issue; they do not yet identify
its trigger or validate animated deformation. Lossless diagnostic variants in
`output/qa-smila-viewer/` compare parent-first node ordering, matrix-to-TRS
serialization, and both changes together. A Windows Viewer comparison remains
pending; no converter workaround or reduction of influences has been applied.
The normal regression suite now also checks the actual exported rest hierarchy
and inverse binds, in addition to the native-matrix deformation comparison.

## Reproducing development QA

The normal regression suite needs no native installation. Its checked-in
[fixture](../tests/fixtures/procedural_skin_oracle.json) contains observations
from 104 synthetic configurations and 2,542 selected points, with capture hashes.
It tests exported glTF deformation, not just a second copy of the C formula.
The full suite passes 131 tests in both Release and AddressSanitizer builds.

For an independent rest-skin importer check, with optional geometry renders:

```powershell
python tests/check_rest_skin_blender.py output/batch-20260912-151513/packages/smila-by-moebius/gltf --blender "C:/Program Files/Blender Foundation/Blender 4.2/blender.exe" --report _tmp/rest-skin-blender.json --renders _tmp/rest-skin-renders
```

For fresh native measurements with the separately installed SDK/runtime:

```powershell
python tests/probe_skinning_oracle.py --output _tmp/new-bone-experiment
python tests/probe_skinning_corpus.py content/quatuor/work/3d/test_anim.lws --content-root content/quatuor --output _tmp/new-quatuor-skin-qa
python tests/probe_skinning_corpus.py content/smila-by-moebius/smila_run_cycle.lws --content-root content/smila-by-moebius --output _tmp/new-smila-skin-qa --end 16 --step 8
```

`--disable-volume --disable-morphs` creates an explicitly audited isolated
comparison. It never edits content files. The capture helper's optional
`LWCONVERT_CAPTURE_BONES=1` sidecar records the parameters and flags actually
loaded by the native host; the established frame-capture protocol is unchanged.
The oracle runner needs indented envelope contents, as written by LightWave;
unindented synthetic keys were ignored by the native loader during early probes.

## Remaining work

1. Qualify additional LightWave releases, falloff values and numeric extremes.
   Missing falloff, negative maps/strengths, invalid ranges, unqualified rest
   transforms and shared skeletons remain explicit refusals. Procedural strengths
   and lengths above 1e12 are outside the supported numeric range.
2. Carry source morphs and their ordering into editable target deformations;
   qualify whether changing the pre-skin shape requires recomputing influences.
3. Treat joint compensation and muscle flexing as pose-dependent effects,
   potentially corrective morphs or Blender rig components, rather than pretending
   they can always be expressed by fixed skin weights.
4. Connect the weights to independently evaluated FK/IK animation and to the
   planned Blender backend. Existing native animation capture still exports
   evaluated morph sequences separately and removes the rest skin to avoid
   double deformation. C weight generation itself performs no IK baking.

Subdivision baking remains excluded from glTF; original patch cages and their
native subdivision information remain available for the Blender work.

## v0.10.0: LightWave 6 profile and actual animated skins

See [Smilla LW6 animation](smilla-lightwave6-animation.md) for the measured
missing-map version difference, preserved source assignments, explicit C profile,
after-IK capture, real Blender playback QA and remaining volume-correction error.
The earlier missing-map refusal describes the default 9.6 profile.
