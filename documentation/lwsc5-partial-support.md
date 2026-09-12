# Partial LWSC 5 support

Converter 0.10.1 makes the LWSC 5 extraction subset explicit under reader profile
`lwsc5-partial-0.1`. Version 0.10.0 already read the explicit item IDs needed by
the Goeland character scene. This milestone adds scope metadata, searchable
source spans for uninterpreted statements, position-controller diagnostics and
corpus-wide conversion checks. It does not add LWSC 4 support or a new IK solver.

## Preserved and interpreted

- Explicit hexadecimal IDs on object loads, nulls, lights, cameras and bones.
  IDs need not be sequential or appear in parent-first order. Duplicate IDs,
  malformed IDs, declaration/type mismatches and inconsistent bone owners fail
  parsing with byte offsets.
- Object filenames, requested layers, parents, pivots and names. Content-root
  inference can find the sibling `Objects` directory above a `Scenes` directory.
- Original motion channels, envelope keys, interpolation parameters and
  pre/post behaviors. Times remain in seconds; motion angles remain in radians.
  Bone rest-direction fields retain their separate native degree convention.
- Bone rest settings and weight-map assignments; native bone, IK, goal,
  targeting, path-alignment and spline parameters. These fields being present
  in IR does not imply that their effects are evaluated.
- Plugin blocks with byte ranges into the unchanged source file.
- Uninterpreted top-level statements and brace-delimited blocks, indexed by name,
  `source_offset` and `bytes` in `scene.json.uninterpreted_statements`. A nested
  block is indexed as a whole. Its payload cannot create false scene items.
  These spans refer to `source.bin`; they do not assign semantic ownership to
  unknown statements. Known opaque plugin, envelope and rig data remain in
  their existing IR fields and/or source bytes.

`inspect`, `scene.json` and `manifest.json.scene_format` identify the reader
profile. Every LWSC 5 conversion returns exit code **2** and `status: partial`;
this includes files for which all supported exports were produced. Exit code
**1** still means an error. The source scene and parsed object files are copied
byte-for-byte, with hashes. No original assets are rewritten.

## Export scope

Resolved object geometry can produce OBJ/MTL and glTF assets. A complete scene
OBJ snapshot and glTF hierarchy with sampled key animation are generated only
when the required transforms fit the existing evaluator. A regression scene
checks child and parent animation with out-of-order explicit IDs against an
analytically known snapshot.

Nonzero or malformed `X/Y/ZController` modes now block ordinary transform
evaluation just as unsupported `H/P/BController` modes do. Their original
values and associated spline/IK parameters remain available in IR. Mode zero
continues through the key evaluator. The other modes are not approximated as
raw keys. Rest-rig construction also refuses nonzero `BoneType`, whose semantics
have not been qualified, while retaining its declaration.

Unsupported motion controllers, IK, arbitrary plugins, expressions, morphs,
combined rest/pivot rotations and other rig limitations can prevent a complete
scene or rig export. Consult `scene_gltf_issue`, `scene_obj_issue`,
`gltf_animation_issue` and each `gltf_rigs[].issue`. Object assets and source IR
remain recoverable even when scene evaluation is blocked. Cameras and lights
are retained as source items; their native optics and lighting are not fully
translated to glTF/OBJ. Subdivision is not baked.

The optional LightWave animation bridge still accepts LWSC 1/3 only. Qualifying
its capture against LWSC 5 requires separate work with a compatible native host.

## Validation

`tests/check_lwsc5.py` converts the eight LWSC 5 scenes under
`content/animation-goeland`. Its independent native declaration scan checks IDs,
order, parents and layer requests against IR; it also verifies unchanged scene
and object bytes, partial status and uninterpreted source-span bounds.

```powershell
python -X utf8 tests/check_lwsc5.py content/animation-goeland --converter bin/win64/lwconvert.exe --output output/lwsc5-qa-20260912 --report documentation/diagnostics/lwsc5-qa.json
```

The output directory must be new. See the [corpus report](diagnostics/lwsc5-qa.json)
for per-scene assets and limitations. Synthetic regression cases additionally
cover animated hierarchy export, nested opaque blocks, CRLF byte offsets,
position controllers, unsupported bone types and invalid IDs.

Results on 12 September 2026: **8/8** partial packages, **zero unresolved object
instances**, 1,293 source items, 468 bones and 18,319 original keys preserved
across the eight scenes. Four scene-level glTF files and their OBJ snapshots
were produced; the other four packages contain object exports and source IR.

| Scene | Scene glTF | Principal scene limitation |
|---|---|---|
| `scene1/Scenes/Scene_1.lws` | No | `XController 7` |
| `scene2/Scenes/Scene_2.lws` | Yes | Camera/spline and plugin behavior remains outside the geometry export scope |
| `scene2/Scenes/Scene_2_tunnel.lws` | Yes | Camera/spline and plugin behavior remains outside the geometry export scope |
| `scene3/Scenes/Scene_3.lws` | Yes | General partial-profile limits |
| `scene3/Scenes/Scene_3_ecran.lws` | Yes | General partial-profile limits |
| `scene4/Scenes/Scene_4.lws` | No | Bone/pivot evaluation |
| `scene5_logo_goeland/Scenes/Scene_5_Goeland.lws` | No | Bone/pivot evaluation |
| `scene5_personnage_V/Scenes/Scene_5_personnage_V.lws` | No | `HController 1` |

All **30** exported glTF files passed the Khronos validator with resource
validation: **0 errors, 0 warnings**. See the
[validator report](diagnostics/lwsc5-gltf-validation.json). All nine regression
groups passed in Release and with AddressSanitizer. The Win64 Release executable
is staged in `bin/win64/lwconvert.exe` as version 0.10.1.

These checks qualify parsing, preservation and the stated export subset. They
do not establish visual fidelity of Goeland's IK, spline animation or materials.

## Documentation basis

The SDK's [Scene Files reference](https://documentation.help/LightWave/lwsc.html)
documents LWSC 3 and explicitly calls itself incomplete. The local LW9 SDK has
the same document at `_tmp/_extern/LightWave/LW9/SDK/html/filefmts/lwsc.html`.
LWSC 5 syntax is qualified against native files and regression cases, not a
complete version-5 specification. The upstream
[Assimp LWS reader](https://github.com/assimp/assimp/blob/master/code/AssetLib/LWS/LWSLoader.cpp)
corroborates explicit IDs beginning at version 4; it is not a dependency or a
reference evaluator for deformation fidelity. No native LWSC 4 scene was found
in the current content corpus, so that version remains rejected.
