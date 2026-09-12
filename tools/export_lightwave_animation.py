"""Export native evaluated LightWave animation without subdividing source cages."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

from lightwave_animation import digest, export_rig, read_capture, write_json
from output_layout import apply_rig_policy, package_file

REPOSITORY = Path(__file__).resolve().parents[1]
MODULES = {
    "MotionMixer":"animate/motionmixer.p", "MM_MotionDriver":"animate/motionmixer.p",
    "LW_Expression":"animate/chanexpress.p", "SimpleOrientConstraints":"animate/simpleconstraints.p",
    "LW_Follower":"animate/chanfollow.p", "LW_Cyclist":"animate/chanfollow.p",
}
# These plugins affect display, selection or the final image rather than the
# evaluated object transformations. Unknown masters/deformers are not discarded.
DISPLAY_CLASSES = {"CustomObjHandler","PixelFilterHandler","ImageFilterHandler"}
DISPLAY_MASTERS = {"ProxyPick",".BRDF",".SceneEditorStandardBanks","SceneEditor","Fprime"}
BUILTINS = {("DisplacementHandler","LW_MorphMixer")}


def machine(path):
    with path.open("rb") as f:
        if f.read(2)!=b"MZ": raise ValueError(f"Expected a Windows binary: {path}")
        f.seek(0x3c); offset = struct.unpack("<I",f.read(4))[0]
        f.seek(offset)
        if f.read(4)!=b"PE\0\0": raise ValueError(f"Invalid PE binary: {path}")
        return struct.unpack("<H",f.read(2))[0]


def prepare_scene(source, scene, assets, directory, lightwave, capture_plugin):
    """Create isolated scene/config copies and a complete override audit."""
    text = source.decode("latin1"); lines = text.splitlines(); kept = []; removed = []; retained = []; i = 0
    while i<len(lines):
        line = lines[i]
        if line.startswith("Plugin "):
            fields = line.split(maxsplit=3)
            if len(fields)!=4: raise ValueError("Malformed native plugin declaration")
            block = [line]; i += 1; depth = 1
            while i<len(lines) and depth:
                block.append(lines[i]); depth += int(lines[i].startswith("Plugin "))-int(lines[i].startswith("EndPlugin")); i += 1
            if depth: raise ValueError("Unterminated native plugin")
            cls,name = fields[1],fields[3]
            if cls in DISPLAY_CLASSES or (cls=="MasterHandler" and name in DISPLAY_MASTERS):
                removed.append({"class":cls,"name":name}); continue
            if name not in MODULES and (cls,name) not in BUILTINS: raise ValueError(f"Native animation plugin is not qualified: {cls} {name}")
            retained.append((cls,name)); kept.extend(block); continue
        kept.append(line); i += 1
    text = "\n".join(kept)+"\n"
    by_item = {}; inputs = directory/"inputs"; inputs.mkdir()
    for node in scene["nodes"]:
        if node["asset_index"] is None: continue
        asset = assets[node["asset_index"]]
        # Expressions address bones through the owning object's filename stem.
        # Keep that basename, including for cloned instances, in separate dirs.
        target = inputs/f"{node['id']:08x}"/Path(asset["source_path"]).name
        target.parent.mkdir()
        shutil.copyfile(asset["source_copy"],target)
        if digest(target)!=asset["id"]: raise ValueError("Native source changed while preparing evaluation")
        by_item[node["id"]] = target
    ordinal = 0
    def object_reference(match):
        nonlocal ordinal
        declaration = match[1]; item = 0x10000000|ordinal; ordinal += 1
        if declaration=="AddNullObject": return match[0]
        if item not in by_item: raise ValueError(f"Unresolved native object instance {item:08x}")
        rest = match[2].split(maxsplit=1)
        return declaration+" "+((rest[0]+" ") if declaration=="LoadObjectLayer" else "")+by_item[item].relative_to(directory).as_posix()
    text = re.sub(r"^(LoadObjectLayer|LoadObject|AddNullObject) (.*)$",object_reference,text,flags=re.M)
    # Explicitly disable subdivision for every object, including those whose
    # source omitted the setting (LightWave defaults must not add geometry).
    text = re.sub(r"^SubPatchLevel .*\n?","",text,flags=re.M)
    text = re.sub(r"^(LoadObjectLayer|LoadObject|AddNullObject) (.*)$",r"\1 \2\nSubPatchLevel 0 0",text,flags=re.M)
    overrides = {"FrameSize":"8 8","ResolutionMultiplier":"1","Antialiasing":"0","EnhancedAA":"0","MotionBlur":"0","DepthOfField":"0","Radiosity":"0","SaveRGB":"0","SaveAlpha":"0","RenderThreads":"1"}
    for key,value in overrides.items(): text = re.sub("^"+key+r" .*\n?","",text,flags=re.M)
    # Frame size is camera-scoped. Add it to each camera; append the global
    # switches as well so scene omissions cannot fall back to enabled defaults.
    text = re.sub(r"^AddCamera.*$",lambda m:m[0]+"\nFrameSize 8 8\nResolutionMultiplier 1\nMotionBlur 0\nDepthOfField 0",text,flags=re.M)
    text += "\n"+"\n".join(f"{key} {value}" for key,value in overrides.items())+"\nPlugin ImageFilterHandler 1 LWConvertCapture\nEndPlugin\n"
    working = directory/"evaluation.lws"; working.write_bytes(text.encode("latin1"))
    config = directory/"config"; config.mkdir()
    modules = [{"class":"ImageFilterHandler","name":"LWConvertCapture","path":capture_plugin.as_posix(),"sha256":digest(capture_plugin)}]
    for cls,name in sorted(set(retained)):
        if (cls,name) in BUILTINS: continue
        path = lightwave/"Plugins"/MODULES[name]
        if not path.is_file(): raise FileNotFoundError(path)
        modules.append({"class":cls,"name":name,"path":path.as_posix(),"sha256":digest(path)})
    plugin_config = "".join('{ Entry\n  Class "'+m["class"]+'"\n  Name "'+m["name"]+'"\n  Module "'+m["path"]+'"\n}\n' for m in modules)
    encoding = "mbcs" if os.name=="nt" else "utf-8"
    for name in ("LWEXT9.CFG","LWEXT9-64.CFG"): (config/name).write_text(plugin_config,encoding=encoding)
    for name in ("LW9.CFG","LW9-64.CFG"): (config/name).write_text("ContentDirectory .\n",encoding=encoding)
    return working,config,{"removed_display_plugins":removed,"retained_animation_plugins":[{"class":c,"name":n} for c,n in retained],"modules":modules,"overrides":{"SubPatchLevel":"0 0",**overrides},"working_scene_sha256":digest(working)}


def _evaluate_package(package, lightwave, capture_plugin=None, start=None, end=None, step=1, timeout=120, converter=None):
    package = Path(package).resolve(); lightwave = Path(lightwave).resolve()
    capture_plugin = Path(capture_plugin or REPOSITORY/"bin/win64/lw_capture.p").resolve()
    executable = lightwave/"Programs/lwsn.exe"
    if machine(executable)!=machine(capture_plugin): raise ValueError("ScreamerNet and capture plugin architectures differ")
    manifest_path = package/"manifest.json"; manifest = json.loads(manifest_path.read_text("utf-8"))
    if not manifest.get("scene"): return []
    rigs = [r for r in manifest.get("gltf_rigs",[]) if r.get("gltf")]
    # The C backend already handles ordinary scene motion. Native whole-scene
    # evaluation fills missing assemblies; existing rig derivatives stay separate.
    if not rigs and manifest.get("scene_gltf"): return []
    scene_path = package/manifest["scene"]; scene = json.loads(scene_path.read_text("utf-8")); source = scene_path.parent/scene["source"]["uri"]
    if not rigs and not any(n["asset_index"] is not None for n in scene["nodes"]): return []
    if digest(source)!=scene["source"]["sha256"]: raise ValueError("IR scene source hash mismatch")
    text = source.read_bytes().decode("latin1")
    def preview(key, fallback):
        found = re.search(r"^"+key+r" ([^\r\n]+)",text,re.M)
        return float(found[1]) if found else fallback
    start = preview("PreviewFirstFrame",scene["first_frame"]) if start is None else start
    end = preview("PreviewLastFrame",scene["last_frame"]) if end is None else end
    if not all(math.isfinite(v) and v==int(v) for v in (start,end,step)) or step<=0 or start<0 or end<=start:
        raise ValueError("Native capture needs nonnegative integer frames, increasing endpoints and a positive step")
    if (end-start)%step: raise ValueError("Frame interval must be divisible by frame step")
    expected = list(range(int(start),int(end)+1,int(step)))
    if len(expected)>1001: raise ValueError("Native morph animation is limited to 1001 samples per export")
    directory = scene_path.parent/"evaluated-animation"; directory.mkdir()
    assets = []
    for asset in manifest["assets"]:
        native_path = package/asset["uri"]; native = json.loads(native_path.read_text("utf-8"))
        assets.append({**asset,"source_copy":native_path.parent/native["source"]["uri"]})
    working,config,audit = prepare_scene(source.read_bytes(),scene,assets,directory,lightwave,capture_plugin)
    capture = directory/"frames"; capture.mkdir()
    env = os.environ.copy(); env["LWCONVERT_CAPTURE_DIR"] = str(capture)
    command = [str(executable),"-3","-c"+str(config),"-d"+str(directory),str(working),str(int(start)),str(int(end)),str(int(step))]
    with (directory/"screamernet.log").open("wb") as log:
        result = subprocess.run(command,cwd=directory,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=timeout,creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
    log = (directory/"screamernet.log").read_text("latin1")
    if result.returncode: raise ValueError(f"ScreamerNet failed ({result.returncode}); see {directory/'screamernet.log'}")
    for cls,name in {(m["class"],m["name"]) for m in audit["retained_animation_plugins"]}|{("ImageFilterHandler","LWConvertCapture")}:
        if re.search(r"Can't load plug-in \""+re.escape(name)+r'\"',log) or f"No plug-in of type {cls} found with name {name}" in log:
            raise ValueError(f"Native animation plugin failed to load: {cls} {name}")
    if "Instance creation failed" in log: raise ValueError("Native plugin instance creation failed; evaluation is incomplete")
    paths = [capture/f"frame-{frame:06d}.txt" for frame in expected]
    frames = [read_capture(path) for path in paths]
    if [f["frame"] for f in frames]!=expected: raise ValueError("Captured frame interval differs from the requested interval")
    for f in frames:
        if not math.isclose(f["time"],f["frame"]/scene["fps"],rel_tol=1e-7,abs_tol=1e-9): raise ValueError("Native capture time differs from source frame rate")
    audit.update(schema_version="0.1",profile="lightwave-evaluated-cage-0.1",source_sha256=scene["source"]["sha256"],host={"path":executable.as_posix(),"sha256":digest(executable),"banner":log.splitlines()[0]},fps=scene["fps"],first_frame=int(start),last_frame=int(end),frame_step=int(step),frames=[{"uri":"frames/"+p.name,"sha256":digest(p)} for p in paths],log_uri="screamernet.log",log_sha256=digest(directory/"screamernet.log"),reported_resource_issues=[line for line in log.splitlines() if "Can't " in line or "Error:" in line],scope="Native evaluated cage and bone poses. Display and image plugins removed; animation plugins retained; subdivision disabled; every exported mesh checked against native point identities and polygon connectivity.")
    if not rigs:
        audit["profile"] = "lightwave-evaluated-scene-transforms-0.1"
        audit["scope"] = "Native evaluated object/null hierarchy; rigid cage motion and topology verified before whole-scene export; subdivision disabled; original IK statements retained in evaluation copy."
    protocols = {2 if "corner_normals" in mesh else 1 for frame in frames for mesh in frame["meshes"].values()}
    audit["capture_protocols"] = sorted(protocols)
    audit["normal_scope"] = "Protocol 2 records LWMeshInfo.pntOtherNormal, evaluated world normals per polygon corner; protocol 1 contains no normals; subdivision disabled"
    capture_manifest = directory/"capture.json"; write_json(capture_manifest,audit)
    if rigs:
        results = [export_rig(package,manifest,rig,frames,digest(capture_manifest)) for rig in rigs]
        manifest["gltf_animations"] = results
        manifest["scope"] += "; additional native evaluated animation via external LightWave, cage morph targets and bone TRS tracks"
    else:
        from lightwave_scene import export_scene
        converter = Path(converter or REPOSITORY/"bin/win64/lwconvert.exe").resolve()
        entry = export_scene(package,manifest,scene,frames,digest(capture_manifest),converter,timeout)
        results = [entry]
        manifest["source_scene_gltf_issue"] = manifest["scene_gltf_issue"]
        manifest.update(scene_gltf=entry["gltf"],scene_gltf_bin=entry["gltf_bin"],scene_gltf_issue="",gltf_animation_issue="",
                        gltf_animation_channels=entry["channels"],gltf_animation_samples=entry["samples"])
        manifest["gltf_evaluated_scene"] = {k:v for k,v in entry.items() if k not in ("gltf","gltf_bin")}
        manifest["gltf_triangles"] += entry["triangles"]
        manifest["gltf_scene_nodes_not_exported"] = sum(n["id"]>>28!=1 for n in scene["nodes"])
        manifest["gltf_animated_channels_not_exported"] = sum(c["keys"]["count"]>1 for n in scene["nodes"] if n["id"]>>28!=1 for c in n["channels"])
        manifest["gltf_profile"] += "; "+entry["profile"]
        manifest["scope"] += "; additional native evaluated rigid-object scene and TRS animation via external LightWave"
    manifest["evaluated_animation"] = {"uri":capture_manifest.relative_to(package).as_posix(),"sha256":digest(capture_manifest)}
    manifest["gltf_files"] += len(results)
    write_json(manifest_path,manifest)
    return results


def evaluate_package(package, lightwave, capture_plugin=None, start=None, end=None, step=1, timeout=120, converter=None):
    try:
        return _evaluate_package(package,lightwave,capture_plugin,start,end,step,timeout,converter)
    except (OSError,ValueError,KeyError,subprocess.TimeoutExpired) as error:
        # Keep failed native evaluations reviewable when a batch publishes the
        # successfully extracted IR and removes its temporary working package.
        manifest_path = Path(package)/"manifest.json"
        manifest = json.loads(manifest_path.read_text("utf-8"))
        if manifest.get("scene"):
            directory = (Path(package)/manifest["scene"]).parent/"evaluated-animation"
            if directory.is_dir():
                path = directory/"capture.json"
                if not path.exists():
                    write_json(path,{"schema_version":"0.1","status":"failed","issue":str(error),"frames":[{"uri":p.relative_to(directory).as_posix(),"sha256":digest(p)} for p in sorted((directory/"frames").glob("*.txt"))]})
                manifest["evaluated_animation"] = {"uri":path.relative_to(package).as_posix(),"sha256":digest(path)}
        manifest["gltf_animation_issue"] = str(error); manifest["status"] = "partial"
        write_json(manifest_path,manifest)
        raise


def finalize_rig_outputs(package, policy):
    """Prune only generated rest-rig files from this new direct package."""
    package = Path(package).resolve()
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    paths = [package_file(package, uri) for uri in apply_rig_policy(manifest, policy)]
    if any(path.parent != package / "gltf" or ".rig-" not in path.name or path.suffix not in {".gltf", ".bin"} for path in paths):
        raise ValueError("Unexpected rest-rig output path")
    # Publish references first so a failed unlink cannot leave dangling links.
    write_json(manifest_path, manifest)
    for path in paths:
        path.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input",type=Path)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--content-root",type=Path)
    parser.add_argument("--lightwave-root",type=Path,required=True,help="Installed LightWave root containing Programs/lwsn.exe and Plugins/")
    parser.add_argument("--capture-plugin",type=Path,default=REPOSITORY/"bin/win64/lw_capture.p")
    parser.add_argument("--converter",type=Path,default=REPOSITORY/"bin/win64/lwconvert.exe")
    parser.add_argument("--start-frame",type=int)
    parser.add_argument("--end-frame",type=int)
    parser.add_argument("--frame-step",type=int,default=1)
    parser.add_argument("--timeout",type=float,default=120)
    parser.add_argument("--uv-map")
    parser.add_argument("--gltf-rigs",choices=("skins","all"),default="skins",help="Keep usable skins, or also unbound rest skeletons")
    parser.add_argument("--map",action="append",default=[])
    args = parser.parse_args()
    command = [str(args.converter.resolve()),"convert",str(args.input.resolve()),"--output",str(args.output.resolve()),"--content-root",str((args.content_root or args.input.parent).resolve())]
    command += ["--gltf-rigs", "all"]
    if args.uv_map: command += ["--uv-map",args.uv_map]
    for rule in args.map: command += ["--map",rule]
    result = subprocess.run(command,timeout=args.timeout)
    if result.returncode not in (0,2): return result.returncode
    try:
        animations = evaluate_package(args.output,args.lightwave_root,args.capture_plugin,args.start_frame,args.end_frame,args.frame_step,args.timeout,args.converter)
    finally:
        finalize_rig_outputs(args.output, args.gltf_rigs)
    print(json.dumps({"output":str(args.output.resolve()),"animations":animations},indent=2))
    return result.returncode


if __name__ == "__main__":
    try: raise SystemExit(main())
    except (OSError,ValueError,KeyError,subprocess.TimeoutExpired) as error:
        print(f"Native animation export failed: {error}",file=sys.stderr)
        raise SystemExit(1)
