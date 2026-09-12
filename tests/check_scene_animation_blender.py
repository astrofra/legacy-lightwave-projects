"""Reimport scene animation in Blender and compare sampled world triangles to OBJ."""
import argparse
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_gltf_blender import compare_triangles


def worker(options):
    import bpy

    data = json.loads(options.gltf.read_text("utf-8"))
    clip = data["animations"][0]
    first, last, fps = (clip["extras"][key] for key in ("first_frame", "last_frame", "fps"))
    frames = sorted(set([first, first+math.floor((last-first)/4), first+math.floor((last-first)/2), last]))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.render.fps = round(fps)
    bpy.context.scene.render.fps_base = round(fps)/fps
    assert bpy.ops.import_scene.gltf(filepath=str(options.gltf), merge_vertices=False) == {"FINISHED"}
    assert bpy.data.actions, "Blender imported no animation actions"
    poses, results = {}, []
    for frame in frames:
        local_frame = frame-first
        bpy.context.scene.frame_set(math.floor(local_frame), subframe=local_frame-math.floor(local_frame))
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        actual = []
        for obj in bpy.context.scene.objects:
            evaluated = obj.evaluated_get(depsgraph)
            poses.setdefault(obj.name, []).append([v for row in evaluated.matrix_world for v in row])
            if obj.type != "MESH":
                continue
            mesh = evaluated.to_mesh()
            mesh.calc_loop_triangles()
            for triangle in mesh.loop_triangles:
                vertices = [evaluated.matrix_world @ mesh.vertices[index].co for index in triangle.vertices]
                actual.append([(v.x, v.z, -v.y) for v in vertices])
            evaluated.to_mesh_clear()
        # Each temporary reference conversion stays beneath the QA report root.
        with tempfile.TemporaryDirectory(prefix="scene-pose-", dir=options.report.parent) as temporary:
            directory = Path(temporary).resolve()
            assert directory.is_relative_to(options.report.parent.resolve())
            package = directory / "reference"
            result = subprocess.run([str(options.converter), "convert", str(options.source),
                                     "--content-root", str(options.content_root), "--output", str(package),
                                     "--frame", str(frame)], capture_output=True, text=True, timeout=60)
            assert result.returncode in (0, 2), result.stdout+result.stderr
            manifest = json.loads((package / "manifest.json").read_text("utf-8"))
            vertices, expected = [], []
            for line in (package / manifest["scene_obj"]).read_text("utf-8").splitlines():
                if line.startswith("v "):
                    vertices.append(tuple(map(float, line.split()[1:])))
                elif line.startswith("f "):
                    face = [vertices[int(value.split("/")[0])-1] for value in line.split()[1:]]
                    assert len(face) == 3
                    expected.append(face)
            tolerance, error = compare_triangles(expected, actual)
            results.append({"source_frame": frame, "blender_frame": local_frame,
                            "triangles": len(actual), "maximum_vertex_error": error, "tolerance": tolerance})
    moving = [name for name, matrices in poses.items()
              if any(max(abs(a-b) for a,b in zip(matrices[0], matrix)) > 1e-6 for matrix in matrices[1:])]
    assert moving, "Animation actions exist but no object moves between the checked frames"
    report = {"gltf": str(options.gltf), "blender": bpy.app.version_string,
              "clips": len(data["animations"]), "actions": len(bpy.data.actions),
              "moving_objects": moving, "frames": results, "passed": True,
              "scope": "Blender glTF animation playback compared with C-evaluated OBJ world triangles at source sample frames; not an independent LightWave renderer comparison."}
    options.report.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("gltf", "source", "content-root", "converter", "report"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--blender", type=Path)
    parser.add_argument("--worker", action="store_true")
    arguments = sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else sys.argv[1:]
    options = parser.parse_args(arguments)
    for name in ("gltf", "source", "content_root", "converter", "report"):
        setattr(options, name, getattr(options, name).resolve())
    options.report.parent.mkdir(parents=True, exist_ok=True)
    if options.worker:
        worker(options)
        return 0
    if not options.blender:
        parser.error("--blender is required")
    command = [str(options.blender), "--background", "--factory-startup", "--python-exit-code", "1",
               "--python", str(Path(__file__).resolve()), "--", "--worker"]
    for name in ("gltf", "source", "content_root", "converter", "report"):
        command += ["--"+name.replace("_", "-"), str(getattr(options, name))]
    return subprocess.run(command, timeout=180).returncode


if __name__ == "__main__":
    raise SystemExit(main())
