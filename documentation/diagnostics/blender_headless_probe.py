#!/usr/bin/env python3
"""Prove external Python -> background Blender -> save -> fresh-process reopen.

Synthetic data only: this is not a LightWave importer or a fidelity test.
Usage: python blender_headless_probe.py --blender /path/to/blender --output /tmp/probe
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys


def worker(mode, output):
    import bpy

    assert bpy.app.background, "Blender must run without its interface"
    blend = output / "synthetic.blend"
    if mode == "create":
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        mesh = bpy.data.meshes.new("probe_mesh")
        # One triangle, one loose edge and one loose point.
        mesh.from_pydata([(0, 0, 0), (2, 0, 0), (0, 1, 0),
                          (3, 0, 0), (3, 1, 0), (4, 2, 0)], [(3, 4)], [(0, 1, 2)])
        uv = mesh.uv_layers.new(name="probe_uv")
        for loop, value in zip(uv.data, [(0, 0), (1, 0), (0, 1)]):
            loop.uv = value
        obj = bpy.data.objects.new("probe", mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj["source_id"] = "synthetic:probe"
        material = bpy.data.materials.new("probe_material")
        material.use_nodes = True
        mesh.materials.append(material)
        obj.location = (0, 0, 0)
        obj.keyframe_insert(data_path="location", frame=1)
        obj.location = (3, 0, 0)
        obj.keyframe_insert(data_path="location", frame=10)
        bpy.context.scene.frame_set(1)
        assert bpy.ops.wm.save_as_mainfile(filepath=str(blend)) == {"FINISHED"}
    else:
        assert bpy.ops.wm.open_mainfile(filepath=str(blend)) == {"FINISHED"}
        obj = bpy.data.objects["probe"]
        assert obj["source_id"] == "synthetic:probe"
        assert len(obj.data.vertices) == 6
        assert len(obj.data.edges) == 4
        assert len(obj.data.polygons) == 1
        assert len(obj.data.uv_layers["probe_uv"].data) == 3
        assert obj.data.materials[0].use_nodes
        for frame, expected in [(1, 0), (10, 3)]:
            bpy.context.scene.frame_set(frame)
            assert abs(obj.location.x - expected) < 1e-6
        result = dict(status="passed", background=bpy.app.background,
                      blender_version=bpy.app.version_string,
                      blender_build_hash=bpy.app.build_hash.decode("ascii"),
                      embedded_python=platform.python_version(),
                      checks=["fresh-process reopen", "6 vertices including a loose point",
                              "4 edges including a loose edge", "1 face", "UV layer",
                              "node material", "source ID", "animation at frames 1 and 10"],
                      scope="Synthetic .blend save/reopen only; no LightWave conversion, rendering or OBJ/glTF export.")
        (output / "probe-result.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--blender", type=Path)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--worker", choices=("create", "reopen"))
    argv = sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else sys.argv[1:]
    args = ap.parse_args(argv)
    output = args.output.resolve()
    content = Path(__file__).resolve().parents[2] / "content"
    if output == content or content in output.parents:
        ap.error("output must be outside content/")
    if args.worker:
        worker(args.worker, output)
        return
    if not args.blender or not args.blender.is_file():
        ap.error("--blender must identify an installed Blender executable")
    if output.exists() and any(output.iterdir()):
        ap.error("output must be a new or empty directory")
    output.mkdir(parents=True, exist_ok=True)
    command = [str(args.blender.resolve()), "--background", "--factory-startup",
               "--disable-autoexec", "--python-exit-code", "1", "--python",
               str(Path(__file__).resolve()), "--", "--output", str(output)]
    exits = {}
    for mode in ("create", "reopen"):
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        with (output / (mode + ".log")).open("wb") as log:
            proc = subprocess.run(command + ["--worker", mode], stdout=log,
                                  stderr=subprocess.STDOUT, timeout=60, creationflags=flags)
        exits[mode] = proc.returncode
        if proc.returncode:
            raise RuntimeError(f"Blender {mode} failed ({proc.returncode}); see {output / (mode + '.log')}")
    result_path = output / "probe-result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result.update(external_python=platform.python_version(), exit_codes=exits,
                  executable_sha256=hashlib.sha256(args.blender.read_bytes()).hexdigest())
    result_path.write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"Artifacts: {output}")


if __name__ == "__main__":
    main()
