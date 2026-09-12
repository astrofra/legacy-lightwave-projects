# legacy-lightwave-projects
My collection of personal Lightwave 3D objects and scenes, gathered since my early Amiga years.

## Download the Aminet corpus

With Python 3 and [7-Zip](https://www.7-zip.org/) installed, run:

```powershell
.\download_aminet.bat
```

The script reads `documentation/aminet.json`, downloads each record's
`download_url`, verifies `archive_bytes` and `sha256`, and extracts the LHA into
`content/aminet-<archive name without extension>/`, preserving its internal folders.
For example, `JumpingBall.lha` becomes `content/aminet-JumpingBall/`.
The `aminet-` prefix distinguishes downloaded packages from personal archives.
It applies only to each package's top-level folder, including with `--output`.
7-Zip is detected in `PATH` and the usual Windows installation directories;
use `--seven-zip "C:\Program Files\7-Zip\7z.exe"` to select it explicitly.

Verified downloads are cached in `_tmp/aminet/archives/`. Each extracted folder
also receives `README.aminet.md` with the Aminet package/download URLs, archive
size and SHA-256 from the manifest, notice URL, retrieval time in UTC and notice
checksum. `AMINET.readme` is an exact copy of Aminet's separate `.readme`,
preserving its encoding and line endings. Bundled README files stay intact.

Existing destination folders are not re-extracted; rerunning the same command
adds missing provenance, including to folders downloaded with the older script.
Complete provenance is reused without another notice request. Conflicting files
are reported and never overwritten. If the notice cannot be downloaded, the
extracted archive stays available and a rerun retries the missing documentation.
The metadata date records notice retrieval, not the original download date.

A failed extraction never publishes a partial destination, and a failed package
does not stop the others. The exit code is 1 if any package or notice fails,
otherwise 0. Optional record fields `notice_url` (package page) and `readme_url`
override the URLs normally derived from `download_url`.

Entries with `container_member` describe an archive inside another archive with
the same `download_url`. The outer archive is processed first, then the local
member is checked against the inner entry's size and SHA-256 and extracted into
a sibling folder beside that member. For example, the two RunningLegs entries
produce `content/aminet-RunningLegs/RunningLegs.lha` and
`content/aminet-RunningLegs/RunningLegs/Left_Leg.lwo`. The annotated `archive` label is
not used as a filename. Inner provenance identifies the container, its checksum
and member path; the online notice belongs to the outer Aminet package.

The current manifest contains 11 downloadable archives and one inner archive.
Double-click `download_aminet.bat` or run it without arguments. The launcher also
enables recovery if a future manifest is cut off: only complete download headers
can be recovered, and a warning reports that the catalog may be incomplete.
When invoking the Python script directly, enable that fallback explicitly:

```powershell
python tools/download_aminet.py --recover-truncated-manifest
```

The source JSON is left untouched. The header's inspected-archive count is not
a download list; only entries actually present in `records` are processed.

Use `--list` to preview the URLs and destinations. `--manifest`, `--output` and
`--cache` accept alternative paths. Defaults are relative to the repository,
so the launcher works from any current directory. On other systems, run
`python3 tools/download_aminet.py` with `7z` or `7zz` installed.

## C converter

`lwconvert` reads LWOB/LWO2 objects, PST_ presets and LWS 1/3 scenes.
It produces an LWIR package that preserves the sources, alongside OBJ/MTL and
glTF 2.0 exports. Blender `.blend` output remains to be implemented.
The converter runs without Blender, addons or a graphical interface.

Since v0.1.1, OBJ exports triangulate ordinary face polygons in C, including
concave outlines and bridged holes. Native polygons remain in LWIR. See the
[van triangulation diagnosis](documentation/van-triangulation.md) for an example.

Since v0.3.0, every conversion also writes `gltf/<source filename>.gltf` and its
`.bin`. It exports base geometry, material approximations and UVs. Scene snapshots
retain geometry instances and their parent transforms at `--frame`.
The manifest records the supported subset and any blocked snapshot.

Since v0.8.0, both glTF and OBJ export source-corner normals, including smoothing
angles, native smoothing groups and unambiguous `NORM` vertex maps. OBJ writes
`vn` and `s`; glTF writes `NORMAL`. Raw shading parameters remain in LWIR.
Native evaluated animations also capture corner normals and export their morph
deltas. See the [normal preservation profile and QA](documentation/normals-qa.md).

Since v0.8.1, LWSC 1 scenes with an implicit camera correctly assign
`CameraMotion` to that camera when it follows the lights. This fixes the four
extensionless Amiga scenes in `aminet-atmobjs`; see the
[diagnosis and remaining archive dependencies](documentation/aminet-atmobjs-qa.md).

Since v0.7.0, scene glTF files also contain an animation clip for changing object
and parent transforms, sampled once per source frame across the scene playback
range. Translation, quaternion rotation, signed scale and pivot offsets are
exported automatically by the C converter and the batch launcher; no installed
LightWave runtime is needed for these movements. Open the scene glTF together
with its `.bin`: individual object glTF files have no scene motion.
`--frame` still chooses the OBJ snapshot and the glTF pose before playback.
Unsupported animation is reported in `gltf_animation_issue` with partial status.
See [scene animation and its sampling limits](documentation/converter.md#scene-transform-animation).
IK-driven rigid assemblies can use the optional native LightWave fallback:
`python tools/batch_convert.py --content content/carrot_driven_robot --lightwave-root _tmp/_extern/LightWave/LW9.6`.
This now produces the assembled, animated `robot.lws.gltf` and
`robot_night.lws.gltf`; see the [carrot robot export and QA](documentation/carrot-robot-qa.md).

The [IK feasibility study and roadmap](documentation/lightwave-ik-oracle-feasibility.md)
describe how to use LightWave as a research oracle toward an independent C solver,
with measured baselines and acceptance criteria. That solver is not implemented yet.
The [Redline corpus inventory](documentation/redline-animation-corpus.md) adds
FK, IK and morph cases, with a separate roadmap for named morph targets and weights.

Image references now resolve within the owning LWS/LWO directory and its
descendants, including an alternative image extension when the filename stem
matches uniquely (for example, `signe.psd` to `signe.jpg`). Found images are copied
into `IR/<source filename>/textures/` and linked in the IR metadata. Equal path
matches prefer PSD, TGA, PNG, JPEG, JPG, GIF, TIFF, then other image formats;
remaining ambiguities are reported.

Since v0.4.0, LWOB planar and spherical image maps generate UVs and PNG material
maps for OBJ and glTF. IFF/ILBM textures are decoded to PNG while the originals
are retained. The IR records native channels, projections and image bindings.
The `orange-juice-signage` example now exports its textured screen, orange skin
and floor transparency. See the [texture profile](documentation/textures.md)
for supported channels and the remaining rendering approximations.

Since v0.4.1, scene snapshots also sample TCB curves and tolerate incorrect
declared envelope key counts while retaining the original data and a warning.
A bounded mirrored-bank `LW_Follower` preview unblocks the seven
[`butterfly-tank` scenes](documentation/butterfly-tank-qa.md).

Since v0.8.2, unbound rest-skeleton glTF copies are **opt-in**. Original object
exports are shared across the project's scenes. Actual skins and native animated derivatives are
retained. Bones, weight maps, IK and original animation keys remain in the IR;
manifest entries still explain unsupported skinning. Use `--gltf-rigs all` to
include unbound rest skeletons for inspection (`skins` is the default).

Since v0.9.0, the standalone C converter also derives editable skin weights
from procedural bone influences and hybrid weight maps. It exports standard
glTF skins and separate, explicitly derived weights in the IR. The measured
LightWave 9.6 approximation includes falloff, strength, limited ranges and
Faster Bones. It needs no LightWave installation. Joint compensation, muscle
flexing, scene morphs and IK animation remain outside this fixed-weight profile.
See the [algorithm and measured QA](documentation/procedural-skinning.md).
See the [Quatuor export simplification QA](documentation/quatuor-gltf-qa.md).

Since v0.5.0, scene IR includes bone rest poses, weight-map assignments and native
influence settings. Separate `*.rig-<item ID>.gltf` files export object-local rest
skeletons and, for explicit normalized map-only bindings, standard glTF skins
with all positive influences. Procedural bone influences remain unevaluated;
the [Smila QA report](documentation/skin-and-smila-qa.md) describes that limitation.
Subdivision is never baked into glTF: patch control cages and native IR settings
are retained for a future Blender backend.

Since v0.6.0, an optional native LightWave evaluation exports rig animation as
separate `*.anim-<item ID>.gltf` files. Bone nodes receive TRS tracks; sampled
morph targets reproduce the evaluated cage deformation without inventing skin
weights or adding subdivision. This requires Python 3, the included
`bin/win64/lw_capture.p` helper and an installed LightWave 9.6 x64 runtime when
converting. Viewing the resulting glTF needs no LightWave installation.
The C executable exports object/parent motion, snapshots and rest rigs. See the
[Smila animation example and QA](documentation/smila-animation-qa.md) for usage,
preserved IR, supported plugins and the distinction from editable skinning.

Scene clip maps are preserved explicitly on their owning instances, with image
roles, native parameter trees and source byte ranges. Object dissolve remains a
separate attribute. The IR supports future target interpretation: MTL `map_d`
for opacity, or core glTF 2.0 `MASK` plus a baked base-color alpha texture; Blender
is deferred. See [clip-map semantics and target limits](documentation/converter.md#clip-maps-and-target-interpretation).

Build on Windows with CMake and Visual Studio 2022:

```powershell
cmake -S . -B build -G "Visual Studio 17 2022" -A x64
cmake --build build --config Release
ctest --test-dir build -C Release --output-on-failure
```

Python 3 is used for tests; set `-DBUILD_TESTING=OFF` to build only the C program.
Every normal MSVC x64 Release build copies `lwconvert.exe` into `bin/win64/`,
including builds where the executable was already up to date. This directory is
intentionally versioned so the Windows converter can be used directly after a
checkout. The Release executable links the C runtime statically; no Visual C++
runtime DLL needs to be shipped alongside it. Vendored raster components retain
their MIT notices in `third_party/`, also copied to `bin/win64/licenses/`.
Keep those notices with redistributed binaries. Debug and AddressSanitizer builds
stay in their build directories and do not replace the distributed binary.

The copy can also be refreshed with
`cmake --build build --config Release --target stage_win64`.

```powershell
bin/win64/lwconvert.exe inspect content/metropolis-robots/metropolis_model_UV.lwo

# The output's parent directory must exist; the output directory itself must be new.
New-Item -ItemType Directory -Force output
bin/win64/lwconvert.exe convert content/metropolis-robots/metropolis_model_UV.lwo --content-root content/metropolis-robots --output output/metropolis --uv-map st

bin/win64/lwconvert.exe convert content/circus/Mr_Lector_2.lws --content-root content/circus --output output/lector --frame 1
```

Exit code **2** means a package was produced with limitations reported in
`manifest.json`: approximated or unsupported textures, missing UVs, unavailable layers, etc.
**0** confirms success for the supported subset; **1** indicates an error.
A scene produces individual OBJ exports for its resolved objects and, when
the transforms can be evaluated, an `obj/<scene filename>.obj` at the requested frame.
For example, `Mr_Lector_2.lws` produces `obj/Mr_Lector_2.lws.obj` and its `.mtl`.

See the [converter guide](documentation/converter.md) for the package format,
path resolution and current limitations, and the
[feasibility study](documentation/lightwave-to-blender-feasibility.md)
for the architecture of the three outputs.

## Batch the entire content directory

Using the included Windows binary (or after building the converter), run:

```powershell
.\convert_content.bat
```

The launcher requires Python 3 and works from any current directory. It scans
the repository's `content/` recursively by signature, including objects without
extensions, and converts every loose LWOB/LWO2 object, PST_ preset and LWS scene.
Each top-level content directory is used as a separate project root for resolving
scene dependencies. Images, archives and other unsupported files are listed as
skipped standalone inputs; resolved image dependencies are bundled with their
owning IR documents. Archive contents are not extracted.

Every run creates a new `output/batch-<timestamp>/` directory. A numeric suffix
(`-2`, `-3`, etc.) is added only if that directory already exists:

```text
batch-report.json
logs/lake-scenery/Lacustre.lws.log
packages/lake-scenery/
    manifest.json
    obj/
        Lacustre.lws.obj
        Lacustre.lws.mtl
        Tour_Toit.lwo.obj
        Tour_Toit.lwo.mtl
    IR/
        Lacustre.lws/
            manifest.json
            scene.json
            animation.bin
            source.bin
        Tour_Toit.lwo/
            manifest.json
            object.json
            geometry.bin
            source.bin
    gltf/
        Lacustre.lws.gltf
        Lacustre.lws.bin
        Tour_Toit.lwo.gltf
        Tour_Toit.lwo.bin
    blender/               # Reserved; backend not implemented yet
```

Since v0.2.0, each project groups outputs by format. Names retain the original
extension, such as `Tour_Toit.lwo.obj`. For extensionless Amiga sources, the
detected signature supplies `.lwo` for LWOB/LWO2 objects and `.lws` for LWSC
scenes: `Station1` becomes `Station1.lwo.obj` / `Station1.lwo.gltf`, while
`NastyStation` becomes `NastyStation.lws.obj` / `NastyStation.lws.gltf`.
The same name identifies the MTL, binary buffers and IR directory. Source files
and scene references stay unchanged; existing extensions and PST_ preset names
are preserved. This applies to direct conversion and batches alike.
Batch layout **0.3** retains the source hierarchy beneath each format directory.
For example, `content/quatuor/work/3d/01.lws` produces
`packages/quatuor/gltf/work/3d/01.lws.gltf`, `obj/work/3d/01.lws.obj`, and
`IR/work/3d/01.lws/manifest.json`. The separate root-level `01.lws` keeps its
own root-level output. Logs follow the same hierarchy. Only directories that
contain converted files or their dependencies are created; ancillary files and
empty source directories are not copied. See the [Quatuor QA](documentation/quatuor-hierarchy-qa.md).

Names only need numeric suffixes when they collide within the same output
directory, including after extension inference or Windows filename cleanup.
Spaces and characters unsuitable for OBJ references become underscores; accents
are preserved. Original source directories take precedence over generated file
names if those would collide. Dependencies outside the project are collected
under `_external/` within each format, with numeric suffixes for collisions and
original paths retained in metadata. SHA-256 remains in metadata rather than
directory names. Scene dependencies already published in the same project are
reused. Each input has a conversion manifest under `IR/<relative source path>/`,
and the project manifest indexes these conversions. Loose files directly under
the content root are grouped under that directory's name.

The publisher updates all manifest links, OBJ material-library references and
glTF buffer URIs. Textures remain in a local `textures/` folder beside the
export that uses them, so textures shared across different source directories
can have multiple identical copies. Copy the whole project directory to retain
all dependencies, or copy a glTF with its `.bin` and local `textures/` folder.
Direct `lwconvert convert` calls retain their existing layout 0.2; the hierarchy
is applied when assembling batch projects.

Existing exports are preserved. The batch continues after individual failures
and writes an overall report, with per-file logs and links to manifests, OBJ and
glTF files. Failed temporary packages remain under `.work/` for inspection and are
listed in the report; successful temporary packages are removed. Exit codes are
**0** for success, **2** when at least one
conversion is partial, and **1** when any file fails. Skipped ancillary files do
not count as failures. Interrupted runs return **130** and record pending files.

```powershell
# Preview discovery without writing output or running the converter.
.\convert_content.bat --dry-run

# Convert one project while retaining all its subdirectories under one root.
.\convert_content.bat --project quatuor
# --project is repeatable; --content still names the parent collection directory.

# Include optional unbound rest-skeleton glTF copies for inspection.
.\convert_content.bat --project quatuor --gltf-rigs all

# Use a specific binary or override the snapshot frame for all scenes.
.\convert_content.bat --converter build/Debug/lwconvert.exe --frame 1

# Evaluate Smila's rig animation with the supplied historical runtime.
.\convert_content.bat --content content/smila-by-moebius --lightwave-root _tmp/_extern/LightWave/LW9.6 --animation-start 0 --animation-end 25
```

On Windows, the default binary is `bin/win64/lwconvert.exe`, falling back to the
local Release, single-configuration or Debug build. `--converter` overrides this
selection. On other platforms, the default is `build/lwconvert`.
`--help` lists input/output overrides, the per-file timeout
(120 seconds by default), and explicit UV-map/path-mapping options. The batch
uses the current converter's OBJ/MTL, LWIR and glTF outputs; `.blend` files are
not generated yet. Keep each `.gltf` alongside its `.bin` and referenced textures
when copying an export.

Since v0.10.0, `--skin-profile lightwave6` selects the measured legacy missing-map
fallback. The optional native bridge can export Smilla as a bound skin with
after-IK bone animation using the installed LW6 x86 runtime and helper. Content
root inference and LWSC 5 explicit-ID extraction are also available. See the
[Smilla LW6 reference, reproduction commands and limits](documentation/smilla-lightwave6-animation.md).
