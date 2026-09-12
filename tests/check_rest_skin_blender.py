"""Open actual rest-skin exports in Blender and compare evaluated vertices."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
from urllib.parse import unquote


def worker(paths, report, renders):
    import bpy
    from mathutils import Vector
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from procedural_skin_checks import rest_errors, skin_rows
    records = []
    if renders: renders.mkdir(parents=True,exist_ok=True)
    for path in paths:
        data = json.loads(path.read_text("utf-8"))
        if not data.get("skins"): continue
        bpy.ops.wm.read_factory_settings(use_empty=True)
        assert bpy.ops.import_scene.gltf(filepath=str(path),merge_vertices=False)=={"FINISHED"}
        bpy.context.view_layer.update(); graph=bpy.context.evaluated_depsgraph_get()
        meshes=[o for o in bpy.context.scene.objects if o.type=="MESH" and any(m.type=="ARMATURE" for m in o.modifiers)]
        points=[]
        for ob in meshes:
            evaluated=ob.evaluated_get(graph); mesh=evaluated.to_mesh()
            points.extend(evaluated.matrix_world@v.co for v in mesh.vertices)
            evaluated.to_mesh_clear()
        expected=[Vector((p[0],-p[2],p[1])) for _,p,_ in skin_rows(path)[1]]
        assert len(points)==len(expected)>0,(path,len(points),len(expected))
        errors=[(a-b).length for a,b in zip(points,expected)]
        extent=max(p.length for p in expected); tolerance=5e-6*max(1,extent)
        native=rest_errors(path)
        record={"file":str(path),"mesh_count":len(meshes),"vertices":len(points),
                "gltf_sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
                "buffer_sha256":{b['uri']:hashlib.sha256(path.parent.joinpath(unquote(b['uri'])).read_bytes()).hexdigest() for b in data['buffers']},
                "maximum_vertex_error":max(errors),"rms_vertex_error":math.sqrt(sum(x*x for x in errors)/len(errors)),
                "tolerance":tolerance,**native}
        record["passed"]=max(errors)<tolerance and native["maximum_rest_position_error"]<tolerance
        records.append(record)
        if renders:
            for ob in bpy.context.scene.objects: ob.hide_render=ob not in meshes
            lo=Vector(tuple(min(p[k] for p in points) for k in range(3)))
            hi=Vector(tuple(max(p[k] for p in points) for k in range(3)))
            center=(lo+hi)/2; radius=max((hi-lo).length/2,.01)
            bpy.ops.object.camera_add(location=center+Vector((2,4,1.4)).normalized()*radius*3)
            camera=bpy.context.object; camera.rotation_euler=(center-camera.location).to_track_quat('-Z','Y').to_euler()
            camera.data.type='ORTHO'; camera.data.ortho_scale=radius*2.25
            scene=bpy.context.scene; scene.camera=camera; scene.render.engine='BLENDER_WORKBENCH'
            scene.render.resolution_x=scene.render.resolution_y=800; scene.render.resolution_percentage=100
            scene.display.shading.light='STUDIO'; scene.display.shading.color_type='MATERIAL'
            scene.display.shading.show_shadows=True; scene.display.shading.show_cavity=True
            scene.display.shading.background_type='WORLD'
            if scene.world is None: scene.world=bpy.data.worlds.new('QA background')
            scene.world.color=(.2,.2,.2)
            destination=renders/(path.stem+'.png'); scene.render.filepath=str(destination)
            bpy.ops.render.render(write_still=True); record['render']=str(destination)
    result={"blender_version":bpy.app.version_string,"blender_build_hash":bpy.app.build_hash.decode(),"files_checked":len(records),"files":records,
            "passed":bool(records) and all(r['passed'] for r in records),
            "scope":"Actual glTF rest hierarchy, inverse binds and Blender evaluated armature vertices, compared by vertex index. Optional Workbench images inspect geometry, not LightWave rendering fidelity."}
    report.parent.mkdir(parents=True,exist_ok=True); report.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    assert result['passed'],result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inputs',type=Path,nargs='+')
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--renders',type=Path)
    parser.add_argument('--blender',type=Path)
    parser.add_argument('--worker',action='store_true')
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else None)
    paths=sorted({f.resolve() for p in args.inputs for f in (p.rglob('*.rig-*.gltf') if p.is_dir() else [p])})
    if not paths: parser.error('No rest rig files found')
    if args.worker: worker(paths,args.report.resolve(),args.renders.resolve() if args.renders else None)
    else:
        if not args.blender: parser.error('--blender is required')
        command=[str(args.blender),'--background','--factory-startup','--disable-autoexec','--python-exit-code','1','--python',str(Path(__file__).resolve()),'--','--worker','--report',str(args.report.resolve())]
        if args.renders: command+=['--renders',str(args.renders.resolve())]
        subprocess.run(command+[str(p) for p in paths],check=True,timeout=180)


if __name__=='__main__': main()
