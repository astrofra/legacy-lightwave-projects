# legacy-lightwave-projects
My collection of personal Lightwave 3D objects and scenes, gathered since my early Amiga years.

## C converter

`lwconvert` reads LWOB/LWO2 objects, PST_ presets and LWS 1/3 scenes.
It produces an LWIR package that preserves the sources, alongside OBJ/MTL and
glTF 2.0 exports. Blender `.blend` output remains to be implemented.
The converter runs without Blender, addons or a graphical interface.

Since v0.1.1, OBJ exports triangulate ordinary face polygons in C, including
concave outlines and bridged holes. Native polygons remain in LWIR. See the
[van triangulation diagnosis](documentation/van-triangulation.md) for an example.

Since v0.3.0, every conversion also writes `gltf/<source filename>.gltf` and its
`.bin`. The first glTF profile exports static geometry, flat triangle normals,
scalar material approximations and explicitly selected UVs. Scene snapshots
retain geometry instances and their parent transforms at `--frame`. Animation,
texture bindings, native smoothing, cameras, lights and deformations are not
exported. The manifest records the supported subset and any blocked snapshot.

Image references now resolve within the owning LWS/LWO directory and its
descendants, including an alternative image extension when the filename stem
matches uniquely (for example, `signe.psd` to `signe.jpg`). Found images are copied
into `IR/<source filename>/textures/` and linked in the IR metadata. Equal path
matches prefer PSD, TGA, PNG, JPEG, JPG, GIF, TIFF, then other image formats;
remaining ambiguities are reported. Applying those images to OBJ/glTF materials
remains unimplemented.

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
The executable is `build/Release/lwconvert.exe`.

```powershell
build/Release/lwconvert.exe inspect content/metropolis-robots/metropolis_model_UV.lwo

# The output's parent directory must exist; the output directory itself must be new.
New-Item -ItemType Directory -Force output
build/Release/lwconvert.exe convert content/metropolis-robots/metropolis_model_UV.lwo --content-root content/metropolis-robots --output output/metropolis --uv-map st

build/Release/lwconvert.exe convert content/circus/Mr_Lector_2.lws --content-root content/circus --output output/lector --frame 1
```

Exit code **2** means a package was produced with limitations reported in
`manifest.json`: textures not exported, missing UVs, unavailable layers, etc.
**0** confirms success for the supported subset; **1** indicates an error.
A scene produces individual OBJ exports for its resolved objects and, when
the transforms can be evaluated, an `obj/<scene filename>.obj` at the requested frame.
For example, `Mr_Lector_2.lws` produces `obj/Mr_Lector_2.lws.obj` and its `.mtl`.

See the [converter guide](documentation/converter.md) for the package format,
path resolution and current limitations, and the
[feasibility study](documentation/lightwave-to-blender-feasibility.md)
for the architecture of the three outputs.

## Batch the entire content directory

After building the converter, run this from a terminal:

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
extension, such as `Tour_Toit.lwo.obj`; duplicate basenames receive `-2`, `-3`,
etc. Spaces and characters unsuitable for OBJ material-library references become
underscores; accents are preserved. SHA-256 hashes remain in metadata rather
than directory names. Scene dependencies already published in the same project
are reused. Each input keeps its conversion manifest under `IR/<source name>/`,
and the project manifest indexes these conversions. Loose files directly under
the content root are grouped under that directory's name.

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

# Use a specific binary or override the snapshot frame for all scenes.
.\convert_content.bat --converter build/Debug/lwconvert.exe --frame 1
```

The default binary is the Release build, falling back to a single-configuration
build or Debug. `--help` lists input/output overrides, the per-file timeout
(120 seconds by default), and explicit UV-map/path-mapping options. The batch
uses the current converter's OBJ/MTL, LWIR and glTF outputs; `.blend` files are
not generated yet. Keep each `.gltf` alongside its `.bin` when copying an export.
