"""Import morph-bearing glTF files in Blender and inspect their shape keys."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys


def source_summary(path):
    document=json.loads(path.read_text("utf-8"))
    targets=sum(len(mesh.get("extras",{}).get("targetNames",[])) for mesh in document.get("meshes",[]))
    channels=sum(channel.get("target",{}).get("path")=="weights"
                 for animation in document.get("animations",[]) for channel in animation.get("channels",[]))
    nonzero_weights=sum(abs(value)>1e-6 for node in document.get("nodes",[]) for value in node.get("weights",[]))
    return targets,channels,nonzero_weights


def worker(inputs, report):
    import bpy
    results=[]
    for path in inputs:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        assert bpy.ops.import_scene.gltf(filepath=str(path))=={"FINISHED"}
        scene=bpy.context.scene
        shaped=[]
        for obj in bpy.data.objects:
            if obj.type!="MESH" or not obj.data.shape_keys or len(obj.data.shape_keys.key_blocks)<=1:
                continue
            keys=obj.data.shape_keys
            values=[]
            start,end=int(scene.frame_start),int(scene.frame_end)
            span=max(0,end-start)
            frames=sorted({start+round(span*i/64) for i in range(65)})
            for frame in frames:
                scene.frame_set(frame)
                values.append(tuple(key.value for key in keys.key_blocks[1:]))
            varying=sum(any(abs(row[index]-values[0][index])>1e-6 for row in values[1:])
                        for index in range(len(values[0]))) if values else 0
            shaped.append(dict(object=obj.name,shape_keys=[key.name for key in keys.key_blocks[1:]],
                               animated_shape_keys=varying,has_animation_data=keys.animation_data is not None,
                               initial_values=list(values[0]),sampled_frame_range=[start,end],sampled_frames=len(frames)))
        source_targets,source_channels,source_nonzero_weights=source_summary(path)
        assert shaped, f"No shape keys imported from {path}"
        if source_channels:
            assert any(item["has_animation_data"] for item in shaped), f"No shape-key animation imported from {path}"
            assert sum(item["animated_shape_keys"] for item in shaped)>0, f"No varying shape-key value sampled from {path}"
        elif source_nonzero_weights:
            assert any(any(abs(value)>1e-6 for value in item["initial_values"]) for item in shaped), f"Static morph weights were lost while importing {path}"
        results.append(dict(file=str(path),gltf_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                            source_targets=source_targets,source_weight_animation_channels=source_channels,
                            source_nonzero_static_weights=source_nonzero_weights,
                            blender_objects_with_shape_keys=len(shaped),blender_shape_keys=sum(len(item["shape_keys"]) for item in shaped),objects=shaped))
    payload=dict(passed=True,blender_version=bpy.app.version_string,
                 blender_build_hash=bpy.app.build_hash.decode("ascii"),embedded_python=platform.python_version(),
                 files=results,scope="Blender glTF import, shape-key presence and sampled weight variation. This does not compare rendered pixels with LightWave.")
    report.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender",type=Path)
    parser.add_argument("--gltf",type=Path,action="append",required=True)
    parser.add_argument("--report",type=Path,required=True)
    parser.add_argument("--worker",action="store_true")
    argv=sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else sys.argv[1:]
    args=parser.parse_args(argv)
    inputs=[path.resolve() for path in args.gltf]; report=args.report.resolve()
    if args.worker:
        worker(inputs,report); return
    if not args.blender or not args.blender.is_file():
        parser.error("--blender must identify an installed Blender executable")
    report.parent.mkdir(parents=True,exist_ok=True)
    command=[str(args.blender.resolve()),"--background","--factory-startup","--disable-autoexec",
             "--python-exit-code","1","--python",str(Path(__file__).resolve()),"--"]
    for path in inputs:
        command.extend(("--gltf",str(path)))
    command.extend(("--report",str(report),"--worker"))
    flags=subprocess.CREATE_NO_WINDOW if sys.platform=="win32" else 0
    result=subprocess.run(command,capture_output=True,encoding="utf-8",timeout=180,creationflags=flags)
    if result.returncode:
        raise RuntimeError(f"Blender exited {result.returncode}:\n{result.stdout}\n{result.stderr}")
    payload=json.loads(report.read_text("utf-8"))
    payload.update(external_python=platform.python_version(),blender_executable_sha256=hashlib.sha256(args.blender.read_bytes()).hexdigest())
    report.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({key:value for key,value in payload.items() if key!="files"},indent=2))
    for item in payload["files"]:
        print(json.dumps({key:value for key,value in item.items() if key!="objects"},indent=2))


if __name__=="__main__":
    main()
