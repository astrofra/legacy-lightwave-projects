"""Compare assembled glTF poses and world triangles to native LightWave captures."""
import argparse
import json
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
    entry = manifest["gltf_evaluated_scene"]
    gltf = package/manifest["scene_gltf"]; data = json.loads(gltf.read_text("utf-8"))
    raw = (gltf.parent/unquote(data["buffers"][0]["uri"])).read_bytes()
    capture_path = package/manifest["evaluated_animation"]["uri"]
    capture = json.loads(capture_path.read_text("utf-8"))
    references = {}
    for node in data["nodes"]:
        if "mesh" not in node: continue
        mesh = data["meshes"][node["mesh"]]
        asset = next(a for a in manifest["assets"] if a["id"]==mesh["extras"]["source_sha256"])
        path = package/asset["uri"]; native = json.loads(path.read_text("utf-8"))
        indices,_,_ = selected_points(native,(path.parent/native["buffer"]["uri"]).read_bytes(),mesh["extras"]["source_layer_request"])
        faces = []
        for primitive in mesh["primitives"]:
            if primitive.get("mode",4)!=4: continue
            mapping = primitive["extras"]["source_map"]
            points = [struct.unpack_from("<III",raw,mapping["byteOffset"]+12*i)[2] for i in range(mapping["count"])]
            faces.extend(points[i:i+3] for i in range(0,len(points),3))
        references[node["extras"]["source_node_id"]] = indices,faces
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.render.fps = round(capture["fps"])
    bpy.context.scene.render.fps_base = round(capture["fps"])/capture["fps"]
    assert bpy.ops.import_scene.gltf(filepath=str(gltf),merge_vertices=False)=={"FINISHED"}
    objects = {o["source_node_id"]:o for o in bpy.context.scene.objects if "source_node_id" in o}
    assert len(objects)==entry["objects"]
    basis = Matrix(((1,0,0,0),(0,0,-1,0),(0,1,0,0),(0,0,0,1)))
    available = {int(Path(f["uri"]).stem.split("-")[-1]):f for f in capture["frames"]}
    ordered = sorted(available); frames = requested or sorted({ordered[i*(len(ordered)-1)//4] for i in range(5)})
    results = []; poses = {}
    for frame in frames:
        reference = read_capture(capture_path.parent/available[frame]["uri"])
        bpy.context.scene.frame_set(frame-entry["first_frame"])
        bpy.context.view_layer.update(); depsgraph = bpy.context.evaluated_depsgraph_get()
        actual,expected = [],[]; matrix_error = 0.
        for item,obj in objects.items():
            evaluated = obj.evaluated_get(depsgraph)
            matrix = basis.inverted()@evaluated.matrix_world@basis
            target = reflected(reference["items"][item]["matrix"])
            matrix_error = max(matrix_error,max(abs(matrix[r][c]-target[4*c+r]) for r in range(4) for c in range(4)))
            poses.setdefault(item,[]).append([v for row in matrix for v in row])
            if item not in references: continue
            indices,faces = references[item]
            lookup = {i:(p["world"][0],p["world"][1],-p["world"][2]) for i,p in zip(indices,reference["meshes"][item]["points"])}
            expected.extend([[lookup[i] for i in face] for face in faces])
            assert obj.type=="MESH"
            mesh = evaluated.to_mesh(); mesh.calc_loop_triangles()
            for triangle in mesh.loop_triangles:
                vertices = [evaluated.matrix_world@mesh.vertices[i].co for i in triangle.vertices]
                actual.append([(v.x,v.z,-v.y) for v in vertices])
            evaluated.to_mesh_clear()
        tolerance,error = compare_triangles(expected,actual)
        assert error<5e-5 and matrix_error<5e-5,(error,matrix_error)
        results.append({"source_frame":frame,"triangles":len(actual),"object_nodes":len(objects),
                        "maximum_vertex_error":error,"maximum_matrix_error":matrix_error,"matching_tolerance":tolerance})
    moving = [item for item,values in poses.items() if any(max(abs(a-b) for a,b in zip(values[0],v))>1e-6 for v in values[1:])]
    assert moving or not entry["channels"]
    result = {"gltf":str(gltf.resolve()),"blender_version":bpy.app.version_string,"passed":True,
              "moving_items":moving,"frames":results,
              "scope":"Blender glTF world triangles and all exported object/null world matrices compared with captured LightWave evaluation at sample frames; no subdivision. Does not establish native material/rendering or between-sample equivalence."}
    report.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8"); print(json.dumps(result,indent=2))


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
        args.report.parent.mkdir(parents=True,exist_ok=True)
        command = [str(args.blender),"--background","--factory-startup","--disable-autoexec","--python-exit-code","1",
                   "--python",str(Path(__file__).resolve()),"--","--worker","--package",str(args.package.resolve()),"--report",str(args.report.resolve())]
        for frame in args.frame or []: command += ["--frame",str(frame)]
        subprocess.run(command,check=True,timeout=180)
