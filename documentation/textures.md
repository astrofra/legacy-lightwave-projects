# Texture profile — v0.4.0

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

Each LWOB material has a `textures` array. Records preserve the native channel,
type, owning image-reference index, source byte range, flags, wrap values, size,
center, falloff, velocity, texture value, amplitude and repetition counts.
Native bytes remain in `source.bin`, including unimplemented parameters and
procedural layers. `RIMG` is distinguished as a reflection environment.
`export_status` and `issue` identify approximated and preserved-only records.
The semantic scopes follow the [original LWOB specification](https://www.martinreddy.net/gfx/3d/LWOB.txt).

Planar and spherical object-space image projections generate UVs per polygon
corner, before instance transforms. Spherical seams are split before repetition;
pole longitude is derived from the surrounding corners. Compatible channels
share one UV set. Color, then diffuse, take precedence when projections differ;
additional layers or incompatible projections are retained and reported.
An explicit `--uv-map` disables automatic bindings, avoiding the application of
a planar image to unrelated UV coordinates. World-space projection, falloff,
velocity, unsupported projections, procedural evaluation and LWO2 BLOK material
evaluation remain outside this profile.

The preview sampler repeats in both directions with linear filtering. Native
`TWRP`, antialias and filter flags are retained, but their exact legacy rendering
semantics are not claimed. Scalar image maps use mean RGB brightness to
interpolate from the surface value at black toward `TVAL` at white; image alpha
weights that contribution. Negative-image flags invert RGB. Color image pixels
are assumed sRGB, with diffuse/emission intensity applied in linear light.
Values are clamped for the target. Participating images with different sizes
are sampled at the pixel centers of the largest image using nearest resampling.
These material operations are explicit approximations requiring comparison with
an original LightWave render for fidelity claims.

`derived_maps` holds the PNG maps used by the exporters. Their relative URIs are
`textures/<PNG SHA-256>.png`; identical content shares a filename. These files
are present in the owning IR directory and in each format's texture directory.
Batch publication preserves links, verifies content hashes, deduplicates shared
maps and checks collisions. Moving `obj/` with its textures or `gltf/` with its
buffers and textures needs no source path or IR directory.

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
use the existing rough dielectric approximation and flat triangle normals.
Transparency images are distinct from scene clip maps: the latter remain in
the IR with binary-cutout semantics and are not evaluated by this change.

## Validation

`tests/test_textures.py` checks known RGB/RGBA and palette pixels, row padding,
ByteRun literals/runs/no-ops, EHB, mask planes, transparent indices, malformed
IFF handling, projection coordinates and spherical seams, native channel scopes,
opacity inversion, specular binding and portable batch texture publication.
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
