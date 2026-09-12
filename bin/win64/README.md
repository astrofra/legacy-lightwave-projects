# Windows x64 converter

`lwconvert.exe` is intentionally committed for use without a local C toolchain.
Run it directly, or use `convert_content.bat` at the repository root (Python 3
is required for batching).

Refresh it from the current sources:

```powershell
cmake -S . -B build -G "Visual Studio 17 2022" -A x64
cmake --build build --config Release
ctest --test-dir build -C Release --output-on-failure
```

CMake copies only the Release converter here, with the C runtime linked
statically. Debug builds, sanitizer builds, test programs, static libraries and
debug symbols stay in their build directories. Commit the updated executable
alongside the corresponding source changes.

The `licenses/` directory is refreshed by the same build. Keep it alongside the
executable when redistributing: libilbm and stb retain their MIT attribution.

`lw_capture.p` is the optional native animation capture helper. It is loaded by
the user's LightWave 9.6 x64 ScreamerNet installation, through
`tools/export_lightwave_animation.py` or the batch option `--lightwave-root`.
It is not needed for ordinary C conversion or for viewing exported animations.
The helper uses the NewTek LightWave SDK API; SDK headers, LightWave executables
and NewTek plugins are not included here.

Since converter v0.8.0 the helper writes capture protocol 2, adding evaluated
world normals per polygon corner. Rebuild it together with the converter.
Protocol 1 captures remain readable, but smoothed animation needs a new capture
to export faithful normals. Source files and earlier captures remain intact.

To rebuild and stage the helper as well, configure the SDK path before building:

```powershell
cmake -S . -B build -DLWCONVERT_LIGHTWAVE_SDK=S:/works/legacy-lightwave-projects/_tmp/_extern/LightWave/LW9/SDK
cmake --build build --config Release
```

See the [animation guide](../../documentation/smila-animation-qa.md) and
[capture helper source](../../tools/lightwave_capture/capture.c).
