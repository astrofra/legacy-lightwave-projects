"""Compare glTF animation reimport with independently captured LightWave poses."""
import argparse
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
from urllib.parse import unquote

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from lightwave_animation import read_capture, reflected, selected_points
from check_gltf_blender import compare_triangles


def worker(package, report, requested):
    import bpy
    from mathutils import Matrix
    manifest = json.loads((package/"manifest.json").read_text("utf-8"))
    entry = manifest["gltf_animations"][0]; owner = entry["owner_item"]
    capture_path = package/manifest["evaluated_animation"]["uri"]; capture = json.loads(capture_path.read_text("utf-8"))
    gltf = package/entry["gltf"]; data = json.loads(gltf.read_text("utf-8")); raw = (gltf.parent/unquote(data["buffers"][0]["uri"])).read_bytes()
    mesh = data["meshes"][0]
    asset = next(a for a in manifest["assets"] if a["id"]==mesh["extras"]["source_sha256"])
    native_path = package/asset["uri"]; native = json.loads(native_path.read_text("utf-8"))
    indices,_,_ = selected_points(native,(native_path.parent/native["buffer"]["uri"]).read_bytes(),mesh["extras"]["source_layer_request"])
    faces = []
    for p in mesh["primitives"]:
        if p["mode"]!=4: continue
        mapping = p["extras"]["source_map"]
        points = [struct.unpack_from("<III",raw,mapping["byteOffset"]+12*i)[2] for i in range(mapping["count"])]
        faces.extend(points[i:i+3] for i in range(0,len(points),3))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.render.fps = round(capture["fps"])
    bpy.context.scene.render.fps_base = round(capture["fps"])/capture["fps"]
    assert bpy.ops.import_scene.gltf(filepath=str(gltf),merge_vertices=False)=={"FINISHED"}
    meshes = [o for o in bpy.context.scene.objects if o.type=="MESH"]
    assert len(meshes)==1 and meshes[0].data.shape_keys
    assert len(meshes[0].data.shape_keys.key_blocks)==entry["morph_targets"]+1
    basis = Matrix(((1,0,0,0),(0,0,-1,0),(0,1,0,0),(0,0,0,1)))
    results = []
    available = {int(Path(f["uri"]).stem.split("-")[-1]):f for f in capture["frames"]}
    ordered = sorted(available)
    frames = requested or [ordered[0],ordered[len(ordered)//2],ordered[-1]]
    for frame in frames:
        reference = read_capture(capture_path.parent/available[frame]["uri"])
        bpy.context.scene.frame_set(frame-entry["first_frame"])
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        item = meshes[0].evaluated_get(depsgraph); geometry = item.to_mesh()
        actual = []
        for polygon in geometry.polygons:
            vertices = [item.matrix_world@geometry.vertices[i].co for i in polygon.vertices]
            actual.append([(v.x,v.z,-v.y) for v in vertices])
        item.to_mesh_clear()
        lookup = {i:(p["world"][0],p["world"][1],-p["world"][2]) for i,p in zip(indices,reference["meshes"][owner]["points"])}
        expected = [[lookup[i] for i in face] for face in faces]
        tolerance,error = compare_triangles(expected,actual)
        assert error<2e-6, error
        bone_error = 0.; bones = 0
        for obj in bpy.context.scene.objects:
            source = obj.get("source_node_id")
            if source is None or source>>28!=4: continue
            matrix = basis.inverted()@obj.evaluated_get(depsgraph).matrix_world@basis
            target = reflected(reference["items"][source]["matrix"])
            bone_error = max(bone_error,max(abs(matrix[r][c]-target[4*c+r]) for r in range(4) for c in range(4)))
            bones += 1
        assert bones==manifest["gltf_rigs"][0]["bones"]
        assert bone_error<1e-5, bone_error
        results.append({"source_frame":frame,"triangles":len(actual),"bones":bones,"maximum_vertex_error":error,"maximum_bone_matrix_error":bone_error,"matching_tolerance":tolerance})
    result = {"blender_version":bpy.app.version_string,"gltf":str(gltf),"passed":True,"frames":results,"scope":"Blender evaluated shape-key mesh triangles and bone node world matrices compared with captured LightWave output, after coordinate conversion. Original subdivision remains disabled. Validates sampled poses, not interpolation between captures or original-version rendering fidelity."}
    report.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,indent=2))


if __name__=="__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package",type=Path,required=True)
    parser.add_argument("--report",type=Path,required=True)
    parser.add_argument("--blender",type=Path)
    parser.add_argument("--frame",type=int,action="append")
    parser.add_argument("--worker",action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else None)
    if args.worker: worker(args.package,args.report,args.frame)
    else:
        if not args.blender: parser.error("--blender is required")
        command = [str(args.blender),"--background","--factory-startup","--disable-autoexec","--python-exit-code","1","--python",str(Path(__file__).resolve()),"--","--worker","--package",str(args.package.resolve()),"--report",str(args.report.resolve())]
        for frame in args.frame or []: command += ["--frame",str(frame)]
        subprocess.run(command,check=True,timeout=180)
