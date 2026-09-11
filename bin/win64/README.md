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
