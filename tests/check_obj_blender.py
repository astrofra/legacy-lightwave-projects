"""Optional independent OBJ reimport check, using an installed background Blender."""
import argparse
import collections
import json
from pathlib import Path
import subprocess
import sys


def worker(paths, report):
    import bpy

    results = []
    assert bpy.app.background
    for path in paths:
        vertices, faces, uv, bound_uv = [], [], [], []
        for line in path.read_text("utf-8").splitlines():
            fields = line.split()
            if not fields:
                continue
            if fields[0] == "v":
                vertices.append(tuple(map(float, fields[1:])))
            elif fields[0] == "vt":
                uv.append(tuple(map(float, fields[1:])))
            elif fields[0] == "f":
                faces.append(fields[1:])
                for corner in fields[1:]:
                    parts = corner.split("/")
                    bound_uv.append(uv[int(parts[1])-1] if len(parts) > 1 else (0, 0))
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        assert bpy.ops.wm.obj_import(filepath=str(path), use_split_objects=False, use_split_groups=False) == {"FINISHED"}
        meshes = [o.data for o in bpy.data.objects if o.type == "MESH"]
        actual_vertices = sum(len(m.vertices) for m in meshes)
        actual_faces = sum(len(m.polygons) for m in meshes)
        assert actual_vertices == len(vertices), (path, actual_vertices, len(vertices))
        assert actual_faces == len(faces), (path, actual_faces, len(faces))
        expected_arities = collections.Counter(map(len, faces))
        actual_arities = collections.Counter(len(p.vertices) for m in meshes for p in m.polygons)
        assert actual_arities == expected_arities, (path, actual_arities, expected_arities)
        if uv:
            actual_uv = [tuple(v.uv) for m in meshes for v in m.uv_layers.active.data]
            quantize = lambda values: collections.Counter(tuple(round(x, 5) for x in pair) for pair in values)
            assert quantize(actual_uv) == quantize(bound_uv), path
        results.append({"obj": str(path), "vertices": actual_vertices, "faces": actual_faces, "uv_corners_checked": len(bound_uv) if uv else 0})
    report.write_text(json.dumps({"blender_version": bpy.app.version_string, "background": True, "passed": results, "scope": "OBJ reimport counts, polygon arities and per-corner UV multisets; not LightWave render fidelity or full scene reconstruction."}, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", type=Path)
    parser.add_argument("--obj", type=Path, action="append", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else None)
    if args.worker:
        worker(args.obj, args.report)
        return
    if not args.blender:
        parser.error("--blender is required outside Blender")
    command = [str(args.blender), "--background", "--factory-startup", "--python-exit-code", "1", "--python", str(Path(__file__).resolve()), "--", "--worker", "--report", str(args.report.resolve())]
    for path in args.obj:
        command += ["--obj", str(path.resolve())]
    subprocess.run(command, check=True, timeout=120)


if __name__ == "__main__":
    main()
