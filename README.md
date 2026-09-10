# legacy-lightwave-projects
My collection of personal Lightwave 3D objects and scenes, gathered since my early Amiga years.

## C converter — first milestone

`lwconvert` reads LWOB/LWO2 objects, PST_ presets and LWS 1/3 scenes.
It produces an LWIR package that preserves the sources, alongside OBJ/MTL exports.
glTF 2.0 and Blender outputs are planned for later milestones.
The converter runs without Blender, addons or a graphical interface.

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
the transforms can be evaluated, a `scene.obj` at the requested frame.

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
skipped; archive contents are not extracted.

Every run creates a new `output/batch-<timestamp>-<unique suffix>/` directory:

```text
batch-report.json
logs/000001.log
packages/<project>/<original relative path and filename>/manifest.json
```

The package tree follows the input tree, with each source filename becoming a
package directory. Existing exports are preserved. The batch continues after
individual failures and writes an overall report, with per-file logs and links
to package manifests. Exit codes are **0** for success, **2** when at least one
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
uses the current converter's OBJ/MTL and LWIR outputs; glTF and `.blend` are not
generated yet.
