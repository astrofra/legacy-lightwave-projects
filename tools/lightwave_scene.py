"""Assemble existing C geometry with independently evaluated native object poses."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
from urllib.parse import quote, unquote

from lightwave_animation import (append_accessor, decompose, digest, inverse,
                                multiply, reflected, transform, validate_mesh, write_json)

PROFILE = "lightwave-evaluated-scene-transforms-0.1"


def validate_rigid_scene(package, manifest, scene, frames):
    """Require the original cage and rigid motion for every captured instance."""
    if len(frames)<2 or any(a["time"]>=b["time"] for a,b in zip(frames,frames[1:])):
        raise ValueError("Native scene animation requires increasing sample times")
    if any(n["bone"] is not None for n in scene["nodes"]):
        raise ValueError("Native rigid-scene export does not replace bone deformation")
    native_assets = {}
    maximum_error = 0.; points_checked = 0
    objects = [n for n in scene["nodes"] if n["id"]>>28==1]
    parents = {n["id"]:n["parent"] for n in objects}
    for item in parents:
        seen = set(); current = item
        while current is not None:
            if current not in parents: raise ValueError("Native rigid scene requires object/null parents")
            if current in seen or len(seen)>1024: raise ValueError("Cyclic or excessively deep native object hierarchy")
            seen.add(current); current = parents[current]
    for node in objects:
        for frame in frames:
            captured = frame["items"].get(node["id"])
            if captured is None: raise ValueError(f"Native evaluator omitted object {node['id']:08x}")
            if captured["parent"]!=(node["parent"] or 0):
                raise ValueError(f"Native parent differs from source for {node['id']:08x}")
        if node["asset_index"] is None:
            if node["object_path"]["text"]: raise ValueError("Unresolved native object")
            continue
        asset = manifest["assets"][node["asset_index"]]
        if asset["id"] not in native_assets:
            path = package/asset["uri"]; native = json.loads(path.read_text("utf-8"))
            native_assets[asset["id"]] = native,(path.parent/native["buffer"]["uri"]).read_bytes()
        native,geometry = native_assets[asset["id"]]
        for frame in frames:
            mesh = frame["meshes"].get(node["id"])
            if mesh is None: raise ValueError(f"Native evaluator omitted mesh {node['id']:08x}")
            validate_mesh(mesh,native,geometry,node["layer_request"])
            matrix = frame["items"][node["id"]]["matrix"]
            for point in mesh["points"]:
                predicted = transform(matrix,point["base"])
                for axis,(expected,actual) in enumerate(zip(predicted,point["world"])):
                    # Mesh positions pass through the SDK's float32 interface.
                    magnitude = abs(matrix[12+axis])+sum(abs(matrix[4*k+axis]*point["base"][k]) for k in range(3))
                    tolerance = max(1e-6,5e-7*magnitude)
                    error = abs(expected-actual); maximum_error = max(maximum_error,error)
                    if error>tolerance:
                        raise ValueError(f"Non-rigid deformation on object {node['id']:08x}, frame {frame['frame']}: {error:.6g} exceeds {tolerance:.6g}")
                points_checked += 1
    return {"objects":len(objects),"geometry_instances":sum(n["asset_index"] is not None for n in objects),
            "point_samples_checked":points_checked,"maximum_rigid_point_error":maximum_error,
            "rigidity_tolerance":"max(1e-6, 5e-7 * sum of absolute affine terms), per coordinate; float32 SDK positions"}


def apply_scene_poses(data, buffer, scene, frames, provenance):
    """Keep original instance IDs and parents; animate final local TRS values."""
    source_nodes = {n["id"]:(i,n) for i,n in enumerate(scene["nodes"]) if n["id"]>>28==1}
    indices = {n["extras"]["source_node_id"]:i for i,n in enumerate(data["nodes"])}
    for item in source_nodes:
        if item not in indices:
            indices[item] = len(data["nodes"]); data["nodes"].append({})
    roots = []
    for item,index in indices.items():
        ordinal,source = source_nodes[item]; node = data["nodes"][index]
        node["name"] = source["name"]["text"]
        node["extras"] = {"source_node_id":item,"source_node_index":ordinal,
                          "native_rig_parameters":copy.deepcopy(source["rig_parameters"])}
        node.pop("matrix",None); node.pop("children",None)
    for item,index in indices.items():
        parent = source_nodes[item][1]["parent"]
        if parent is None: roots.append(index)
        elif parent not in indices: raise ValueError("Native rigid scene requires object/null parents")
        else: data["nodes"][indices[parent]].setdefault("children",[]).append(index)
    data["scenes"][data["scene"]] = {"name":Path(scene["source"]["path"]).name,"nodes":roots}
    time = append_accessor(data,buffer,[(f["time"]-frames[0]["time"],) for f in frames],"SCALAR",True)
    animation = {"name":Path(scene["source"]["path"]).stem,"samplers":[],"channels":[],
                 "extras":{"profile":PROFILE,"first_frame":frames[0]["frame"],"last_frame":frames[-1]["frame"],
                           "fps":scene["fps"],"samples":len(frames),"source_start_seconds":frames[0]["time"],"capture_sha256":provenance}}
    moving = []
    for item,index in indices.items():
        source = source_nodes[item][1]; parent = source["parent"]; poses = []
        for frame in frames:
            matrix = frame["items"][item]["matrix"]
            if parent is not None: matrix = multiply(inverse(frame["items"][parent]["matrix"]),matrix)
            poses.append(decompose(reflected(matrix)))
        data["nodes"][index].update(copy.deepcopy(poses[0])); changed = False
        for path,kind in (("translation","VEC3"),("rotation","VEC4"),("scale","VEC3")):
            rows = [p[path][:] for p in poses]
            if path=="rotation":
                for i in range(1,len(rows)):
                    if sum(a*b for a,b in zip(rows[i-1],rows[i]))<0: rows[i] = [-v for v in rows[i]]
            if not any(max(abs(a-b) for a,b in zip(row,rows[0]))>1e-8 for row in rows[1:]): continue
            changed = True; sampler = len(animation["samplers"])
            animation["samplers"].append({"input":time,"output":append_accessor(data,buffer,rows,kind),"interpolation":"LINEAR"})
            animation["channels"].append({"sampler":sampler,"target":{"node":index,"path":path}})
        if changed: moving.append(item)
    if animation["channels"]: data["animations"] = [animation]
    else:
        # Do not leave an unreferenced timeline on a completely static scene.
        accessor = data["accessors"].pop(); view = data["bufferViews"].pop()
        assert accessor["bufferView"]==len(data["bufferViews"])
        del buffer[view["byteOffset"]:]
        data.pop("animations",None)
    data["extras"].update(profile=PROFILE,source_sha256=scene["source"]["sha256"],source_path=scene["source"]["path"],
                         snapshot_frame=frames[0]["frame"],animation="native evaluated rigid-object TRS; LINEAR between captured frames",
                         capture_sha256=provenance,subdivision="disabled; native cage point identity, connectivity and rigid motion verified")
    return {"profile":PROFILE,"samples":len(frames),"first_frame":frames[0]["frame"],"last_frame":frames[-1]["frame"],
            "duration_seconds":frames[-1]["time"]-frames[0]["time"],"channels":len(animation["channels"]),"moving_items":moving}


def export_scene(package, manifest, scene, frames, provenance, converter, timeout=120):
    """Use the C writer for geometry; only native poses replace the transforms."""
    validation = validate_rigid_scene(package,manifest,scene,frames)
    directory = (package/manifest["scene"]).parent/"evaluated-animation"
    source_path = directory/"assembly.lws"; temporary = directory/"assembly-package"
    lines = ["LWSC","3","FirstFrame 0","LastFrame 0",f"FramesPerSecond {scene['fps']:.17g}"]
    for node in scene["nodes"]:
        if node["id"]>>28!=1: continue
        if node["asset_index"] is None: lines.append("AddNullObject "+node["name"]["text"])
        else:
            asset = manifest["assets"][node["asset_index"]]
            if digest(Path(asset["source_path"]))!=asset["id"]: raise ValueError("Object source changed before geometry assembly")
            declaration = "LoadObject" if node["layer_request"] is None else f"LoadObjectLayer {node['layer_request']}"
            lines.append(declaration+" "+asset["source_path"])
        if node["parent"] is not None: lines.append(f"ParentItem {node['parent']:08x}")
    # This neutral scene only asks the qualified C geometry writer to assemble
    # instances. No pose is inferred from its intentionally absent motion keys.
    source_path.write_text("\n".join(lines)+"\n",encoding="utf-8")
    command = [str(converter),"convert",str(source_path),"--content-root",manifest["content_root"],"--output",str(temporary)]
    if manifest.get("uv_map"): command += ["--uv-map",manifest["uv_map"]]
    with (directory/"assembly.log").open("wb") as log:
        result = subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
    if result.returncode not in (0,2): raise ValueError("C geometry assembly failed; see assembly.log")
    assembled = json.loads((temporary/"manifest.json").read_text("utf-8"))
    if not assembled["scene_gltf"]: raise ValueError("C geometry assembly: "+assembled["scene_gltf_issue"])
    if {a["id"] for a in assembled["assets"]}!={a["id"] for a in manifest["assets"]}:
        raise ValueError("Geometry assembly resolved different native assets")
    path = temporary/assembled["scene_gltf"]; data = json.loads(path.read_text("utf-8"))
    buffer = bytearray((path.parent/unquote(data["buffers"][0]["uri"])).read_bytes())
    stats = apply_scene_poses(data,buffer,scene,frames,provenance)
    stats["meshes"] = len(data["meshes"])
    stats["triangles"] = sum(data["accessors"][p["attributes"]["POSITION"]]["count"]//3
                             for mesh in data["meshes"] for p in mesh["primitives"] if p.get("mode",4)==4)
    destination = package/"gltf"/((package/manifest["scene"]).parent.name+".gltf")
    binary = destination.with_suffix(".bin")
    if destination.exists() or binary.exists(): raise FileExistsError(destination)
    for image in data.get("images",[]):
        uri = Path(unquote(image["uri"]))
        if uri.is_absolute() or ".." in uri.parts: raise ValueError("Unexpected geometry image URI")
        target = destination.parent/uri; target.parent.mkdir(exist_ok=True,parents=True)
        if not target.exists(): shutil.copyfile(path.parent/uri,target)
        elif digest(target)!=digest(path.parent/uri): raise ValueError("Geometry image collision")
    data["buffers"] = [{"uri":quote(binary.name,safe="-._~"),"byteLength":len(buffer)}]
    binary.write_bytes(buffer); write_json(destination,data)
    # All original IR and the assembly recipe/log remain in the outer package.
    # Remove only this successfully consumed, private geometry working package.
    if temporary.resolve().parent!=directory.resolve() or temporary.is_symlink():
        raise ValueError("Unexpected geometry working directory")
    shutil.rmtree(temporary)
    return {**stats,**validation,"gltf":destination.relative_to(package).as_posix(),"gltf_bin":binary.relative_to(package).as_posix(),
            "capture_sha256":provenance,"geometry_assembly_sha256":digest(source_path),"geometry_converter_sha256":digest(Path(converter))}
