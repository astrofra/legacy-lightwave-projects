"""Reimport test_skin.py's fixture and deform its sixth influencing bone in Blender."""
import argparse
import json
import math
from pathlib import Path
import subprocess
import sys


def worker(gltf, report):
    import bpy
    from mathutils import Matrix, Vector
    bpy.ops.wm.read_factory_settings(use_empty=True)
    assert bpy.ops.import_scene.gltf(filepath=str(gltf), merge_vertices=False) == {"FINISHED"}
    armatures = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    # The importer also creates an Icosphere as a bone display custom shape.
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH" and any(m.type == "ARMATURE" for m in o.modifiers)]
    assert len(armatures) == len(meshes) == 1, [(o.name, o.type) for o in bpy.context.scene.objects]
    armature, mesh = armatures[0], meshes[0]
    assert any(m.type == "ARMATURE" and m.object == armature for m in mesh.modifiers)
    assert all(f"joint{i}" in armature.pose.bones for i in range(7))
    source = [(0,0,-1), (1,0,-1), (1,1,-1), (0,1,-1)]

    def positions():
        bpy.context.view_layer.update()
        evaluated = mesh.evaluated_get(bpy.context.evaluated_depsgraph_get())
        geometry = evaluated.to_mesh()
        result = [evaluated.matrix_world @ v.co for v in geometry.vertices]
        evaluated.to_mesh_clear()
        # Convert Blender's Z-up world back to the glTF Y-up basis.
        return [(v.x, v.z, -v.y) for v in result]

    before = positions()
    native = [min(range(4), key=lambda i: math.dist(p, source[i])) for p in before]
    assert len(before) == 6 and set(native) == set(range(4)), (before, native)
    rest_error = max(math.dist(p, source[i]) for p, i in zip(before, native))
    assert rest_error < 2e-6, rest_error
    for vertex, point in zip(mesh.data.vertices, native):
        influences = {mesh.vertex_groups[g.group].name: g.weight for g in vertex.groups if g.weight > 0}
        if point == 0:
            assert len(influences) == 6, influences
            for i in range(6): assert abs(influences[f"joint{i}"] - (i+1)/21) < 1e-6, influences
    # joint5 is a leaf in WEIGHTS_1, proving that a real importer retained the
    # second influence set. Apply a known world displacement through the armature.
    joint = armature.pose.bones["joint5"]
    joint.matrix = armature.matrix_world.inverted() @ Matrix.Translation(Vector((.7,0,0))) @ armature.matrix_world @ joint.matrix
    after = positions()
    expected = [(p[0] + (.7 * 6/21 if i == 0 else 0), p[1], p[2]) for p, i in zip(before, native)]
    error = max(math.dist(a,b) for a,b in zip(after, expected))
    assert error < 2e-6, {"maximum_error": error, "actual": after, "expected": expected}
    result = {"blender_version": bpy.app.version_string, "gltf": str(gltf), "armatures": len(armatures), "bones": len(armature.pose.bones), "vertices": len(before), "maximum_rest_error": rest_error, "maximum_deformation_error": error, "passed": True, "scope": "Synthetic explicit normalized weight maps; real armature import and sixth-bone displacement through Blender's evaluated armature modifier. Verifies more than four influences and fixed unweighted vertices; does not qualify LightWave's procedural envelopes."}
    report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gltf", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--blender", type=Path)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else None)
    if args.worker:
        worker(args.gltf, args.report)
    else:
        if not args.blender: parser.error("--blender is required")
        subprocess.run([str(args.blender), "--background", "--factory-startup", "--disable-autoexec", "--python-exit-code", "1", "--python", str(Path(__file__).resolve()), "--", "--worker", "--gltf", str(args.gltf.resolve()), "--report", str(args.report.resolve())], check=True, timeout=120)
