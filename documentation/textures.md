# Texture profile — v0.17.0

The motivating project is `content/orange-juice-signage`. Its scene loads the
textured LWOB object `oj_tv_mesh_t.lwo`: `screen.iff` supplies the screen's color;
`pierre.JPG` supplies spherical diffuse, luminosity, specular and bump maps for
the orange skin and a planar diffuse map for the green parts. The separate
`sol.lwo` uses `maptransp.IFF` for transparency. The other object variants have
different surface definitions; textures are never inferred from a material name.

## Decoding and preservation

Resolution keeps the existing owner-directory-and-descendants restriction and
image-extension preference. Every resolved original is archived byte-for-byte,
with its historical reference, resolved filename and SHA-256 hash. Successful
raster decoding adds dimensions and `status: decoded`; it does not itself imply
that the image can be applied to geometry.

IFF detection uses the file signature. `FORM ILBM` supports indexed images with
1–8 planes, 24-bit RGB, 32-bit RGBA, optional mask planes, transparent palette
indices, EHB, uncompressed rows and ByteRun1 compression. Odd widths use
word-padded bitplane rows. Chunk, plane-row and palette-index bounds are checked;
ByteRun1's -128 control is a no-op. The historical variant excluding the last BODY pad from the FORM size is
accepted within the validated outer padding byte. Images are limited to 16,384 pixels per axis
and 16,777,216 pixels total. HAM, other IFF forms, unsupported masking/compression,
malformed data and excessive dimensions are reported in `decode_issue`.

An ILBM image gains `decoded_image.png_uri` and `png_sha256`, referencing a local
RGBA8 PNG alongside the original. Pixel values and masks are retained without
lossy compression or resizing. Historical display calibration and pixel aspect
ratio are not recreated. This is a bitmap conversion, not a color-management
or animation emulator. Non-IFF formats supported by stb (including JPEG, PNG,
PSD, TGA, BMP and GIF's first frame) can supply material pixels. Other formats
remain archived with an explicit decoding issue.

`third_party/libilbm` contains the attributed, bounded adaptation of the MIT
ByteRun decoder from Sander van der Burg's
[libilbm](https://github.com/svanderburg/libilbm). The surrounding checked ILBM
reader follows the [published ILBM specification](https://documentation.help/LightWave/ilbm.html).
The [dependency notice](../third_party/README.md) pins revisions and describes
local changes. stb supplies ordinary raster decoding and PNG writing under its
MIT option. No GPL parser is incorporated. Source licenses remain under
`third_party/`; Release staging and installation include copies with the binary.

## IR and material derivation

Each LWOB/LWO2 material has a `textures` array. Records preserve the native channel,
type, owning image-reference index, source byte range, flags, wrap values, size,
center, falloff, velocity, texture value, amplitude and repetition counts.
Native bytes remain in `source.bin`, including unimplemented parameters and
procedural layers. `RIMG` is distinguished as a reflection environment.
`export_status` and `issue` identify approximated and preserved-only records.
The semantic scopes follow the [original LWOB specification](https://www.martinreddy.net/gfx/3d/LWOB.txt).

Planar and spherical object-space image projections in **LWOB and LWO2** generate UVs per polygon
corner, before instance transforms. Spherical seams are split before repetition;
pole longitude is derived from the surrounding corners. Compatible channels
share one UV set. Color, then diffuse, take precedence when projections differ;
additional layers or incompatible projections are retained and reported.
An explicit `--uv-map` disables automatic bindings, avoiding the application of
a planar image to unrelated UV coordinates. World-space projection, falloff,
velocity, unsupported projections and procedural evaluation remain outside this
profile. Version 0.16.0 adds LWO2 projected image maps and corrects the existing
LWOB spherical axes and size handling against native SDK measurements. See the
[projection profile, oracle measurements and corpus QA](texture-projections.md).
The LWO2 UV subset is described below.

The preview sampler uses linear filtering. Native `TWRP`/`WRAP` reset, repeat,
mirror and edge modes are translated through a bounded image atlas when needed;
`derived_texture_mapping` records its affine UV domain independently of the
native parameters. Antialias and filter flags are retained, but their exact
legacy rendering semantics are not claimed. Scalar image maps use mean RGB brightness to
interpolate from the surface value at black toward `TVAL` at white; image alpha
weights that contribution. Negative-image flags invert RGB. Color image pixels
are assumed sRGB, with diffuse/emission intensity applied in linear light.
Values are clamped for the target. Participating images with different sizes
are sampled at the pixel centers of the largest image using nearest resampling.
These material operations are explicit approximations requiring comparison with
an original LightWave render for fidelity claims.

`derived_maps` holds the readable PNG URIs used by the exporters. The parallel
`derived_map_sha256` dictionary records each role's full PNG content hash.
These files are present in the owning IR directory and in the export texture
directories. Batch publication preserves links, verifies content hashes and
checks collisions. Moving `obj/` with its textures or `gltf/` with its buffers
and textures needs no source path or IR directory.

## Readable texture names

Version 0.14.0 names derived PNGs `folder__image.png` by default. When different
contents would occupy the same path, all conflicting names gain a role suffix
(`base_color`, `opacity`, `emissive`, `specular`, `bump`, `normal`), then a material
name, then an object name if needed. Remaining conflicts gain 12 hexadecimal
characters of their content hash, extended to the full hash if necessary.
Candidate names are rechecked after every step, including collisions with other
images' natural names. Identical contents may share a name; differing contents
never overwrite each other. Meaningful aliases can also refer to identical pixels.

The C converter uses the input scene's folder, or the object's folder for a
standalone conversion. Batch publication uses each scene's folder for scene/rig
exports and the LWO's own folder for standalone object exports. Shared object
exports therefore do not depend on which scene first referenced the object.
The batch assigns final names after collecting all conversions in each project,
so late collisions are handled across previously published objects too.

Names preserve accents, replace spaces and Windows/OBJ-unsafe characters with
underscores, and protect reserved Windows device names. Each descriptive
component is limited to 36 UTF-8 bytes without splitting a character; any resulting
collision follows the same suffix rules. glTF URIs percent-encode filenames.
Names are deterministic for a given set of inputs; adding a conflicting asset
in a later batch can require longer names.

Full hashes remain in the IR's `derived_map_sha256`, glTF image `extras.sha256`,
and MTL `# texture-sha256 <hash> <uri>` comments. The publisher verifies bytes
before copying and stages renamed files before updating references. Temporary
hash filenames are removed from completed exports. Archived native images and
their existing provenance remain intact. Previous packages using hash filenames
can still be consumed by the publisher and QA checker.

The naming rule for composite maps, collision measurements and replay command
are recorded in the [naming audit](texture-naming-audit.md).

## Target bindings

| Native channel | OBJ/MTL | glTF 2.0 |
|---|---|---|
| Color + diffuse | Baked `map_Kd`, neutral `Kd` multiplier | `baseColorTexture`, neutral base-color factor |
| Luminosity | `map_Ke` | `emissiveTexture` |
| Transparency | Inverted opacity in `map_d` | Base-color alpha with `alphaMode: BLEND` |
| Specular | `map_Ks` | Linear alpha of `KHR_materials_specular.specularTexture` |
| Height bump | `bump` with native `TAMP` as `-bm` | Preserved in native-binding extras and IR; normal-map derivation deferred |
| Reflection environment, Crumple | Preserved in IR | Preserved in native-binding extras and IR |

glTF specular factors follow the
[Khronos extension](https://github.com/KhronosGroup/glTF/tree/main/extensions/2.0/Khronos/KHR_materials_specular);
the optional extension does not reproduce LightWave glossiness. Materials still
use the existing rough dielectric approximation and source-corner normals.
Transparency images retain `BLEND`. Since v0.17.0, aligned static scene image
clip maps instead use `MASK` and coverage composed into base-color alpha;
OBJ receives thresholded `map_d` pixels. Grayscale PSD merged images (one
channel, 8/16 bits, raw or PackBits) are also decoded in C. See
[clip-map scope and QA](clip-maps.md) for instance isolation and object inference.

## LWO2 UV image layers

Since v0.12.0, `SURF/BLOK` headers and ordinary image attributes are parsed using
the [LightWave SDK LWO2 specification](../extern/lwsdk-master/html/filefmts/lwo2.html#c_BLOK).
`IMAP/IMAG` refers to a global `CLIP` index, resolved after the entire FORM has
been read; it is not an index into the image-reference array. `PROJ 5` selects
the `TXUV` map named by the block's `VMAP` attribute. Point UVs come from `VMAP`
and polygon-corner overrides from `VMAD`. glTF flips V once; OBJ retains native V.
Materials can select different UV maps. No UV command-line option is required.

The IR's `lwo2_block` records ordinal, UV map name, CLIP index, projection,
enable state, opacity mode/value, envelope presence, reference object,
coordinate system, rotation, falloff type and shader name. Original block bytes,
including all envelope indices and plugin payloads, remain in `source.bin`.

The export subset accepts one enabled image layer per channel, normal
blending at 100% opacity, static parameters and identity texture
transforms. The named UV map must cover all corners of the material. Other
compositing modes, animated parameters, partial/missing UVs, duplicate CLIP IDs,
procedurals and shaders receive explicit issues; no arbitrary layer is selected.
An explicit `--uv-map` is compatible only when it names the same native map.
Channels sharing the same mapping use the derivative pipeline above. Modern
scalar maps replace the base scalar with mean RGB brightness, weighted by image
alpha, rather than using LWOB's black-to-`TVAL` rule. Image filtering, alpha
interpretation and the target BRDF remain preview approximations.

Since v0.16.0, `PROJ 0` and `PROJ 2` instead calculate planar and spherical
coordinates from geometry, without requiring a named UV map. All three axes,
centering, planar scale, LWO2 texture rotation and spherical repetition are
qualified by the [projection tests](texture-projections.md). Wrapping modes
0–3 work for both projected and explicit UV image bindings, within atlas bounds.

### Aircon regression

`content/collosus-concept-design/items/aircon/aircon.lwo` asks for
`C:collosus/items/aircon/aircon_diff.tga` and `aircon_spec.tga`. The existing
resolver already found `aircon_diff.jpg` and `aircon_spec.jpg`; missing LWO2
material bindings prevented them from reaching the targets. Extension fallback
scans actual image filenames in the owner's subtree and ranks stem/path matches.
It does not enumerate path × extension combinations. Original JPG bytes and
resolution provenance are archived; lossless PNG material derivatives are local
to the exported OBJ/glTF.

The `aircon_target` material now has color and specular maps. The screen uses the
same color atlas for emission with native diffuse=0 and luminosity=1. The third
image (`aircon_norm.tga`, available as JPG) belongs to a private `NormalShader`
payload with a plugin-local CLIP, not the object's global CLIP table. That shader
is now decoded by the bounded v0.13.0 NormalShader profile, with a separate private
image scope. Its object-space normal map is inferred and reencoded as a tangent
PNG with explicit glTF tangents. `FPrime` remains a preserved shader.
See the [implemented normal-map profile and QA](normal-maps.md) for options,
abstention rules, world-space conversion and measured rendering differences.
The following texture QA records the earlier v0.12.0 color/specular work.

QA batch: `build/aircon-textures-qa/batch-20260912-224012` (kept outside the
regular output cleanup). The LWO and LWS were converted through
`convert_content.bat` with no texture options. The LWS also loads `tv_small.lwo`,
which exercises the same profile. All three glTF files pass Khronos resource
validation with zero errors/warnings. Published IR/OBJ/glTF links and hashes pass
the layout checker. Blender 4.2 imports the aircon OBJ and glTF from isolated
format directories with loaded image nodes, assigned materials and UVs; the
rendered aircon preview has legible atlas lettering and the cyan screen.
This validates portable mapping, not equivalence to the original shader.
See the [recorded checks](diagnostics/aircon-textures-qa.json).

```powershell
.\convert_content.bat --file collosus-concept-design/items/aircon/aircon.lwo --file collosus-concept-design/items/aircon/aircon.lws --output-root build/aircon-textures-qa
python -X utf8 tests/check_textures_blender.py --blender "C:/Program Files/Blender Foundation/Blender 4.2/blender.exe" --project build/aircon-textures-qa/batch-20260912-224012/packages/collosus-concept-design --asset items/aircon/aircon.lwo --report build/aircon-blender.json --preview build/aircon-preview.png
```

## Validation

`tests/test_textures.py` checks known RGB/RGBA and palette pixels, row padding,
ByteRun literals/runs/no-ops, EHB, mask planes, transparent indices, malformed
IFF handling, projection coordinates and spherical seams, native channel scopes,
opacity inversion, specular binding and portable batch texture publication.
The LWO2 regressions add actual TGA-to-JPEG fallback, CLIP ordering, UV seams,
scalar replacement, emission and explicit rejection of unsupported bindings.
`tests/check_output_layout.py` also validates PNG hashes and material resources.
Release and MSVC AddressSanitizer run these alongside the existing suites.

Validation on 11 September 2026: all 68 regression tests pass under MSVC
AddressSanitizer. The final focused batch is
`output/batch-20260911-094756/packages/orange-juice-signage`: all 7 glTF files pass
the official Khronos validator with external resources, zero errors and zero
warnings. Five informational messages concern non-power-of-two image dimensions.
The batch layout check passes for all 7 native documents, OBJ/MTL pairs and glTF
buffer pairs, including original and derived texture hashes.

`tests/check_textures_blender.py` imports the textured object and floor from
isolated copies of each format directory in Blender 4.2. All four imports retain
loaded material images, UVs and surface assignments; both floor imports link
opacity. Its optional render confirms the screen logo's orientation and
placement. Reports and the preview are under that batch's `diagnostics/`.
This is not an original-LightWave render comparison. No Blender backend or
`.blend` export is added here.

```powershell
python -X utf8 tools/batch_convert.py --content content/orange-juice-signage
# Substitute the new batch path returned by that command:
python -X utf8 tests/check_output_layout.py output/batch-20260911-094756
python -X utf8 tests/check_textures_blender.py --blender "C:/Program Files/Blender Foundation/Blender 4.2/blender.exe" --project output/batch-20260911-094756/packages/orange-juice-signage --report build/oj-textures-import.json --preview build/oj-textures-preview.png
```
