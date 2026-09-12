"""Verify textured OBJ/glTF imports from isolated format directories in Blender."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def worker(args):
    import bpy
    from mathutils import Vector
    results = []
    assert bpy.app.background
    for format_name in ("gltf", "obj"):
        # Import with only the format's files available alongside the resource.
        with tempfile.TemporaryDirectory(prefix="lw-textures-portable-") as temp:
            folder = Path(temp)/format_name
            shutil.copytree(args.project/format_name,folder)
            for filename in args.asset or ("oj_tv_mesh_t.lwo", "sol.lwo"):
                bpy.ops.wm.read_factory_settings(use_empty=True)
                path = folder/(filename+"."+format_name)
                if format_name == "gltf":
                    assert bpy.ops.import_scene.gltf(filepath=str(path)) == {"FINISHED"}
                else:
                    assert bpy.ops.wm.obj_import(filepath=str(path),forward_axis="NEGATIVE_Z",up_axis="Y") == {"FINISHED"}
                materials = list(bpy.data.materials)
                images = [node.image for material in materials if material.use_nodes
                          for node in material.node_tree.nodes if node.type == "TEX_IMAGE" and node.image]
                assert images, path
                for image in images:
                    assert all(image.size) and image.has_data, (path,image.name)
                native = json.loads((args.project/"IR"/filename/"object.json").read_text("utf-8"))
                names = [m["name"]["text"] for m in native["materials"]]
                textured_names = [m["name"]["text"] for m in native["materials"] if m["derived_maps"]]
                assert textured_names, path
                for name in textured_names:
                    expected = name if format_name == "gltf" else f"a0_m{names.index(name)}"
                    material = next(m for m in materials if m.name == expected)
                    assert any(n.type == "TEX_IMAGE" and n.image for n in material.node_tree.nodes), expected
                    meshes = [o.data for o in bpy.context.scene.objects if o.type == "MESH" and material.name in o.data.materials]
                    assert meshes and all(mesh.uv_layers for mesh in meshes), expected
                    assert any(any(mesh.materials[p.material_index] == material for p in mesh.polygons) for mesh in meshes), expected
                if filename == "sol.lwo":
                    material = next(m for m in materials if m.name == ("sol" if format_name=="gltf" else "a0_m0"))
                    shader = next(n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
                    assert shader.inputs["Alpha"].is_linked, path
                results.append({"format":format_name,"file":filename,"material_count":len(materials),"loaded_texture_nodes":len(images),"portable_import":True})
                if args.preview and format_name == "gltf" and filename == (args.asset[0] if args.asset else "oj_tv_mesh_t.lwo"):
                    scene = bpy.context.scene
                    pts = [o.matrix_world @ Vector(c) for o in scene.objects if o.type=="MESH" for c in o.bound_box]
                    center = Vector(tuple((min(p[i] for p in pts)+max(p[i] for p in pts))/2 for i in range(3)))
                    extent = Vector(tuple(max(p[i] for p in pts)-min(p[i] for p in pts) for i in range(3))).length
                    bpy.ops.object.camera_add(location=center+Vector((1,-2,1))*extent)
                    camera = bpy.context.object
                    camera.rotation_euler = (center-camera.location).to_track_quat('-Z','Y').to_euler()
                    camera.data.type='ORTHO'; camera.data.ortho_scale=extent*1.15; scene.camera=camera
                    bpy.ops.object.light_add(type='AREA',location=center+Vector((2,-2,3))*extent)
                    light = bpy.context.object; light.data.energy=400*extent*extent; light.data.shape='DISK'; light.data.size=4*extent
                    scene.world=bpy.data.worlds.new('World'); scene.world.use_nodes=True
                    scene.world.node_tree.nodes['Background'].inputs[0].default_value=(.15,.15,.15,1)
                    scene.render.engine='CYCLES'; scene.cycles.samples=16; scene.view_settings.view_transform='Standard'
                    scene.render.resolution_x=900; scene.render.resolution_y=900; scene.render.resolution_percentage=100
                    scene.render.filepath=str(args.preview.resolve()); bpy.ops.render.render(write_still=True)
    args.report.write_text(json.dumps({"blender_version":bpy.app.version_string,"passed":True,"imports":results,"scope":"Isolated format directories, loaded material texture nodes, UVs and polygon assignments; opacity checked for the sol.lwo fixture when selected. Preview uses diagnostic lighting, not original LightWave shading."},indent=2)+"\n",encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project",type=Path,required=True)
    parser.add_argument("--blender",type=Path)
    parser.add_argument("--report",type=Path,required=True)
    parser.add_argument("--preview",type=Path)
    parser.add_argument("--asset",action="append",help="Object filename relative to the project IR directory (repeatable)")
    parser.add_argument("--worker",action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else None)
    if args.worker: worker(args); return 0
    if not args.blender: parser.error("--blender is required")
    command = [str(args.blender),"--background","--factory-startup","--disable-autoexec","--python-exit-code","1","--python",str(Path(__file__).resolve()),"--","--worker","--project",str(args.project.resolve()),"--report",str(args.report.resolve())]
    if args.preview: command += ["--preview",str(args.preview.resolve())]
    for asset in args.asset or []: command += ["--asset",asset]
    return subprocess.run(command,check=False).returncode


if __name__ == "__main__": raise SystemExit(main())
