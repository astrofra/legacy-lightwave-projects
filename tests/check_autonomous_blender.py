"""Verify autonomous glTF playback against derived C poses, with optional renders."""
import argparse
import json
import math
from pathlib import Path
import subprocess
import sys


def worker(package, report, renders, reference=None):
    import bpy
    from mathutils import Vector
    sys.path[:0]=[str(Path(__file__).resolve().parent),str(Path(__file__).resolve().parents[1]/"tools")]
    from lightwave_animation import digest,multiply,read_capture,reflected,transform
    from procedural_skin_checks import skin_rows
    manifest=json.loads((package/"manifest.json").read_text("utf-8"))
    import struct
    bake_path=package/manifest["autonomous_animation"]["uri"]
    bake=json.loads(bake_path.read_text("utf-8"))
    raw=(bake_path.parent/bake["buffer"]["uri"]).read_bytes()
    from mathutils import Matrix,Quaternion
    frames=[]
    for sample in range(bake["samples"]):
        items={}
        for i,item in enumerate(bake["node_ids"]):
            v=struct.unpack_from("<10f",raw,(sample*len(bake["node_ids"])+i)*80+40)
            m=Matrix.LocRotScale(Vector(v[:3]),Quaternion((v[6],*v[3:6])),Vector(v[7:]))
            items[item]={"matrix":[m[r][c] for c in range(4) for r in range(4)]}
        frames.append({"frame":bake["first_frame"]+sample,"items":items})
    audit=bake
    native_frames = {}
    if reference:
        for frame in frames:
            path=reference/f'frame-{round(frame["frame"]):06d}.txt'
            native_frames[frame["frame"]]=read_capture(path)
    records=[]
    if renders: renders.mkdir(parents=True,exist_ok=True)
    for entry in manifest["gltf_rigs"]:
        if not entry.get("gltf") or "animated" not in entry["pose"]: continue
        path=package/entry["gltf"];data=json.loads(path.read_text("utf-8"));binds,rows=skin_rows(path)
        owner=entry["owner_item"];joints=[data["nodes"][j]["extras"]["source_node_id"] for j in data["skins"][0]["joints"]]
        bpy.ops.wm.read_factory_settings(use_empty=True)
        scene=bpy.context.scene;scene.render.fps=round(audit["fps"]);scene.render.fps_base=scene.render.fps/audit["fps"]
        bpy.ops.import_scene.gltf(filepath=str(path),merge_vertices=False)
        meshes=[o for o in scene.objects if o.type=="MESH" and any(m.type=="ARMATURE" for m in o.modifiers)]
        assert meshes and data.get("animations")
        errors=[];native_errors=[];first=None;movement=0;camera=None
        for frame in frames:
            scene.frame_set(round(frame["frame"]-frames[0]["frame"]));bpy.context.view_layer.update();graph=bpy.context.evaluated_depsgraph_get();points=[]
            for ob in meshes:
                evaluated=ob.evaluated_get(graph);mesh=evaluated.to_mesh()
                points.extend(evaluated.matrix_world@v.co for v in mesh.vertices);evaluated.to_mesh_clear()
            assert len(points)==len(rows)>0
            matrices=[multiply(reflected(frame["items"][j]["matrix"]),b) for j,b in zip(joints,binds)]
            for actual,(point,position,weights) in zip(points,rows):
                expected=[sum(w*transform(matrices[j],position)[k] for j,w in weights) for k in range(3)]
                errors.append((actual-Vector((expected[0],-expected[2],expected[1]))).length)
                if native_frames:
                    native=native_frames[frame["frame"]]["meshes"][owner]["points"][point]["world"]
                    native_errors.append((actual-Vector((native[0],native[2],native[1]))).length)
            if first is None: first=[p.copy() for p in points]
            movement=max(movement,max((a-b).length for a,b in zip(points,first)))
            if renders and frame["frame"] in {0,5,10,20,30,40}:
                if camera is None:
                    for ob in scene.objects: ob.hide_render=ob not in meshes
                    low=Vector(tuple(min(p[k] for p in points) for k in range(3)));high=Vector(tuple(max(p[k] for p in points) for k in range(3)))
                    center=(low+high)/2;radius=(high-low).length/2
                    bpy.ops.object.camera_add(location=center+Vector((2,-4,1)).normalized()*radius*3)
                    camera=bpy.context.object;camera.rotation_euler=(center-camera.location).to_track_quat('-Z','Y').to_euler()
                    camera.data.type='ORTHO';camera.data.ortho_scale=radius*2.5;scene.camera=camera
                    scene.render.engine='BLENDER_WORKBENCH';scene.render.resolution_x=scene.render.resolution_y=900
                    scene.render.resolution_percentage=100;scene.display.shading.light='STUDIO';scene.display.shading.color_type='MATERIAL'
                    scene.display.shading.show_cavity=True;scene.display.shading.background_type='WORLD'
                    if scene.world is None: scene.world=bpy.data.worlds.new('QA background')
                    scene.world.color=(.2,.2,.2)
                scene.render.filepath=str(renders/f'{path.stem}-frame-{round(frame["frame"]):03d}.png');bpy.ops.render.render(write_still=True)
        record={"gltf":str(path),"gltf_sha256":digest(path),"bin_sha256":digest(package/entry["gltf_bin"]),
                "bake_sha256":digest(bake_path),"frames":len(frames),"joints":len(joints),"vertices":len(rows),
                "maximum_playback_error":max(errors),"rms_playback_error":math.sqrt(sum(e*e for e in errors)/len(errors)),
                "maximum_blender_vertex_movement":movement,"passed":max(errors)<1e-5 and movement>1e-3}
        records.append(record)
        if native_errors:
            record["native_cage_comparison"]={"frames":str(reference),"maximum_vertex_error":max(native_errors),"rms_vertex_error":math.sqrt(sum(x*x for x in native_errors)/len(native_errors)),"scope":"Difference from native deformed cage, including both IK and weight approximation; no equivalence asserted"}
    result={"blender_version":bpy.app.version_string,"files":records,"passed":bool(records) and all(r["passed"] for r in records),
            "scope":"Blender evaluated armature vertices at every C sample, compared with independently reconstructed C world poses and exported weights. Verifies glTF skin playback, not equivalence to LightWave; no subdivision added."}
    report.parent.mkdir(parents=True,exist_ok=True);report.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    assert result["passed"],result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("package",type=Path)
    parser.add_argument("--report",type=Path,required=True);parser.add_argument("--renders",type=Path)
    parser.add_argument("--blender",type=Path);parser.add_argument("--worker",action="store_true")
    parser.add_argument("--reference",type=Path,help="Optional directory of native frame-XXXXXX.txt captures")
    args=parser.parse_args(sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else None)
    if args.worker: worker(args.package.resolve(),args.report.resolve(),args.renders.resolve() if args.renders else None,args.reference.resolve() if args.reference else None)
    else:
        if not args.blender: parser.error("--blender is required")
        command=[str(args.blender),"--background","--factory-startup","--disable-autoexec","--python-exit-code","1","--python",str(Path(__file__).resolve()),"--","--worker",str(args.package.resolve()),"--report",str(args.report.resolve())]
        if args.renders: command.extend(["--renders",str(args.renders.resolve())])
        if args.reference: command.extend(["--reference",str(args.reference.resolve())])
        subprocess.run(command,check=True,timeout=180)


if __name__=="__main__": main()
