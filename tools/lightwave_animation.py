"""Validated native LightWave captures to glTF transform and morph animation."""
import copy
import hashlib
import json
import math
from pathlib import Path
import struct
from urllib.parse import quote, unquote

IDENTITY = [int(i % 5 == 0) for i in range(16)]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n", encoding="utf-8")


def multiply(a, b):
    return [sum(a[4*k+r]*b[4*c+k] for k in range(4)) for c in range(4) for r in range(4)]


def inverse(m):
    a,b,c,d,e,f,g,h,i = (m[0],m[4],m[8],m[1],m[5],m[9],m[2],m[6],m[10])
    determinant = a*(e*i-f*h)-b*(d*i-f*g)+c*(d*h-e*g)
    if abs(determinant) < 1e-14: raise ValueError("Singular native transform")
    out = [(e*i-f*h)/determinant,(f*g-d*i)/determinant,(d*h-e*g)/determinant,0,
           (c*h-b*i)/determinant,(a*i-c*g)/determinant,(b*g-a*h)/determinant,0,
           (b*f-c*e)/determinant,(c*d-a*f)/determinant,(a*e-b*d)/determinant,0,0,0,0,1]
    out[12:15] = [-sum(out[4*k+r]*m[12+k] for k in range(3)) for r in range(3)]
    return out


def transform(m, p):
    return [sum(m[4*k+r]*p[k] for k in range(3))+m[12+r] for r in range(3)]


def reflected(m):
    return [v * (-1 if (i % 4 == 2) != (i // 4 == 2) else 1) for i,v in enumerate(m)]


def decompose(m):
    scales = [math.sqrt(sum(m[4*c+r]**2 for r in range(3))) for c in range(3)]
    if min(scales) < 1e-12: raise ValueError("Zero scale in native animation transform")
    determinant = m[0]*(m[5]*m[10]-m[9]*m[6])-m[4]*(m[1]*m[10]-m[9]*m[2])+m[8]*(m[1]*m[6]-m[5]*m[2])
    if determinant < 0: scales[0] *= -1
    r = [[m[4*c+row]/scales[c] for c in range(3)] for row in range(3)]
    error = max(abs(sum(r[k][a]*r[k][b] for k in range(3))-int(a==b)) for a in range(3) for b in range(3))
    if error > 5e-5: raise ValueError(f"Native local matrix contains shear ({error:.3g}); cannot export as glTF TRS")
    trace = sum(r[i][i] for i in range(3))
    if trace > 0:
        s = math.sqrt(trace+1)*2
        q = [(r[2][1]-r[1][2])/s,(r[0][2]-r[2][0])/s,(r[1][0]-r[0][1])/s,s/4]
    else:
        i = max(range(3), key=lambda k:r[k][k]); j,k = (i+1)%3,(i+2)%3
        s = math.sqrt(max(0,1+r[i][i]-r[j][j]-r[k][k]))*2
        q = [0.,0.,0.,(r[k][j]-r[j][k])/s]
        q[i],q[j],q[k] = s/4,(r[i][j]+r[j][i])/s,(r[i][k]+r[k][i])/s
    norm = math.sqrt(sum(v*v for v in q)); q = [v/norm for v in q]
    return {"translation": m[12:15], "rotation": q, "scale": scales}


def read_capture(path):
    lines = path.read_text("ascii").splitlines()
    if not lines or not lines[0].startswith(("LWCONVERT_CAPTURE 1 ","LWCONVERT_CAPTURE 2 ","LWCONVERT_CAPTURE 3 ")) or not lines[-1].startswith("END "):
        raise ValueError(f"Incomplete native capture: {path}")
    header = lines[0].split(); result = {"protocol":int(header[1]),"frame": int(header[2]), "time": float(header[3]), "items": {}, "meshes": {}}
    if float(header[4]) != result["time"]: raise ValueError("Motion-blurred capture is not supported")
    mesh = None
    for line in lines[1:-1]:
        fields = line.split()
        if fields[0] == "I" and len(fields) == 15:
            key = int(fields[1],16); values = list(map(float,fields[3:])); matrix = []
            for i in range(4): matrix.extend(values[3*i:3*i+3]+[int(i==3)])
            if key in result["items"]: raise ValueError("Duplicate captured item ID")
            result["items"][key] = {"parent": int(fields[2],16), "matrix": matrix}
        elif fields[0] == "N" and len(fields) in (2,3):
            key = int(fields[1],16)
            if key not in result["items"] or "name_hex" in result["items"][key]: raise ValueError("Invalid captured item name")
            value = fields[2] if len(fields)==3 else ""
            bytes.fromhex(value)
            result["items"][key]["name_hex"] = value
        elif fields[0] == "T" and header[1]=="3" and len(fields)==14:
            key = int(fields[1],16); values = list(map(float,fields[2:]))
            if key not in result["items"] or "post_ik_trs" in result["items"][key] or not all(math.isfinite(v) for v in values):
                raise ValueError("Invalid post-IK motion identity or values")
            result["items"][key]["post_ik_trs"] = values
        elif fields[0] == "M" and len(fields) == 6:
            key = int(fields[1],16)
            if key in result["meshes"]: raise ValueError("Duplicate captured mesh ID")
            if list(map(int,fields[4:])) != [0,0]: raise ValueError("Subdivision must be disabled in native capture")
            mesh = {"declared_points": int(fields[2]), "declared_polygons": int(fields[3]), "points": [], "polygons": []}
            if header[1] == "2": mesh["corner_normals"] = {}
            result["meshes"][key] = mesh
        elif fields[0] == "P" and len(fields) == 8 and mesh is not None:
            mesh["points"].append({"id": int(fields[1],16), "base": list(map(float,fields[2:5])), "world": list(map(float,fields[5:]))})
        elif fields[0] == "Q" and mesh is not None and len(fields) == 3+int(fields[2]):
            mesh["polygons"].append([int(v,16) for v in fields[3:]])
        elif fields[0] == "V" and header[1] == "2" and mesh is not None and len(fields) == 7:
            polygon,corner,point = int(fields[1]),int(fields[2]),int(fields[3],16)
            if not 0<=polygon<len(mesh["polygons"]) or not 0<=corner<len(mesh["polygons"][polygon]) or mesh["polygons"][polygon][corner]!=point:
                raise ValueError("Invalid captured corner normal identity")
            key = (polygon,corner)
            if key in mesh.setdefault("corner_normals",{}): raise ValueError("Duplicate captured corner normal")
            vector = list(map(float,fields[4:7]))
            if not all(math.isfinite(v) for v in vector) or not .99<sum(v*v for v in vector)<1.01:
                raise ValueError("Invalid captured corner normal vector")
            mesh["corner_normals"][key] = vector
        else: raise ValueError(f"Invalid capture record: {line[:100]}")
    counts = [len(result["items"]),len(result["meshes"]),sum(len(m["points"]) for m in result["meshes"].values()),sum(len(m["polygons"]) for m in result["meshes"].values())]
    if counts != list(map(int,lines[-1].split()[1:])): raise ValueError("Capture trailer counts disagree")
    for m in result["meshes"].values():
        if len(m["points"]) != m["declared_points"] or len(m["polygons"]) != m["declared_polygons"]: raise ValueError("Native mesh capture is incomplete")
        ids = {p["id"] for p in m["points"]}
        if len(ids) != len(m["points"]) or any(p not in ids for poly in m["polygons"] for p in poly): raise ValueError("Invalid native point identity")
    numbers = [result["time"]]+[v for i in result["items"].values() for v in i["matrix"]]+[v for m in result["meshes"].values() for p in m["points"] for v in p["base"]+p["world"]]
    if not all(math.isfinite(v) for v in numbers): raise ValueError("Non-finite native capture value")
    if header[1]=="3":
        states = {}
        def resolve(item):
            if not item: return IDENTITY
            if item not in result["items"]: raise ValueError("Missing post-IK parent")
            if states.get(item)==1: raise ValueError("Cycle in post-IK hierarchy")
            node = result["items"][item]
            if states.get(item)==2: return node["matrix"]
            states[item]=1
            if "post_ik_trs" in node:
                node["sdk_matrix"] = node["matrix"]
                node["matrix"] = multiply(resolve(node["parent"]),motion_matrix(node["post_ik_trs"]))
            elif item>>28==4: raise ValueError("Missing after-IK capture for bone")
            states[item]=2
            return node["matrix"]
        for item in result["items"]: resolve(item)
    return result


def motion_matrix(values):
    """LightWave local T * Ry(heading) * Rx(pitch) * Rz(bank) * S * T(-pivot)."""
    h,p,b = values[3:6]; y,x,z = IDENTITY[:],IDENTITY[:],IDENTITY[:]
    y[0]=y[10]=math.cos(h); y[8]=math.sin(h); y[2]=-y[8]
    x[5]=x[10]=math.cos(p); x[6]=math.sin(p); x[9]=-x[6]
    z[0]=z[5]=math.cos(b); z[1]=math.sin(b); z[4]=-z[1]
    matrix = multiply(multiply(y,x),z)
    for c,scale in enumerate(values[6:9]):
        for r in range(3): matrix[4*c+r]*=scale
    matrix[12:15]=[values[r]-sum(matrix[4*c+r]*values[9+c] for c in range(3)) for r in range(3)]
    return matrix


def selected_points(native, geometry, request):
    layers = {i for i,l in enumerate(native["layers"]) if request is None or l["id"] == request-1}
    indices = [i for block in native["point_blocks"] if block["layer"] in layers for i in range(block["first"],block["first"]+block["count"])]
    span = native["positions"]
    points = [struct.unpack_from("<3f",geometry,span["offset"]+span["stride"]*i) for i in indices]
    return indices,points,layers


def polygon_key(boundary):
    if not boundary: return ()
    return min(tuple(p[i:]+p[:i]) for p in (boundary,boundary[::-1]) for i in range(len(p)))


def source_boundaries(native, geometry, layers):
    span = native["primitives"]; fields = native["primitive_fields"]
    names = [n.strip() for n in (fields.split(",") if isinstance(fields,str) else fields)]
    for i in range(span["count"]):
        row = struct.unpack_from("<"+"I"*(span["stride"]//4),geometry,span["offset"]+i*span["stride"])
        record = dict(zip(names,row))
        if native["polygon_blocks"][record["polygon_block"]]["layer"] not in layers: continue
        yield i,list(struct.unpack_from("<"+"I"*record["index_count"],geometry,native["indices"]["offset"]+4*record["first_index"]))


def validate_mesh(capture, native, geometry, request):
    indices,points,layers = selected_points(native,geometry,request)
    if len(points) != len(capture["points"]): raise ValueError("Evaluated topology changed: native point count differs from cage")
    lookup = {}
    for index,expected,actual in zip(indices,points,capture["points"]):
        if any(abs(a-b)>max(1e-7,abs(a)*2e-7) for a,b in zip(expected,actual["base"])):
            raise ValueError(f"Native point order/base position differs from IR at point {index}")
        lookup[actual["id"]] = index
    # Match complete polygon boundaries, allowing cyclic rotation and winding.
    # This catches a topology replacement even if point/polygon counts agree.
    from collections import Counter
    expected = [polygon_key(boundary) for _,boundary in source_boundaries(native,geometry,layers)]
    actual = [polygon_key([lookup[p] for p in polygon]) for polygon in capture["polygons"]]
    if Counter(expected) != Counter(actual): raise ValueError("Evaluated polygon connectivity differs from the native cage")
    return dict(zip(indices,[p["world"] for p in capture["points"]]))


def evaluated_normals(capture, native, geometry, request, matrix):
    """Match evaluated world normals to IR corners after validate_mesh succeeds."""
    indices,_,layers = selected_points(native,geometry,request)
    boundaries = list(source_boundaries(native,geometry,layers))
    if "corner_normals" not in capture:
        # Protocol 1 never recorded shading. Retain its original flat-triangle
        # compatibility only where that reconstruction has no lost parameters.
        smooth = any(m.get("smoothing",{}).get("enabled",False) or m.get("smoothing_angle",0)>0 or
                     (native.get("format")=="LWOB" and m.get("flags",0)&4) for m in native["materials"])
        if smooth or any(m["type"]=="NORM" for m in native["maps"]) or any(len(b)>3 for _,b in boundaries):
            raise ValueError("Evaluated corner normals missing; recapture with LWConvertCapture protocol 2 to preserve smoothing")
        return None
    if any(m["type"]=="NORM" for m in native["maps"]) or any(a["type"]==int.from_bytes(b"SMGP","big") for a in native["tag_assignments"]):
        raise ValueError("Native evaluated NORM/SMGP shading is not qualified for LightWave 9.6; source and rest normals remain preserved")
    lookup = dict(zip((p["id"] for p in capture["points"]),indices))
    by_boundary = {}
    for polygon,boundary in enumerate(capture["polygons"]):
        normals_by_point = {}
        for corner,point in enumerate(boundary):
            normal = capture["corner_normals"].get((polygon,corner))
            if normal is None: continue
            # Inverse of the normal transform: A^T * world normal. This also
            # handles nonuniform and mirrored object scale without double bake.
            local = [sum(matrix[4*c+r]*normal[r] for r in range(3)) for c in range(3)]
            length = math.sqrt(sum(v*v for v in local))
            if not length or not math.isfinite(length): raise ValueError("Invalid evaluated normal transform")
            local = [v/length for v in local]; local[2] = -local[2]
            source_point = lookup[point]
            if source_point in normals_by_point and max(abs(a-b) for a,b in zip(local,normals_by_point[source_point]))>1e-6:
                raise ValueError("Ambiguous evaluated normal at repeated polygon point")
            normals_by_point[source_point] = local
        by_boundary.setdefault(polygon_key([lookup[p] for p in boundary]),[]).append(normals_by_point)
    result = {}
    for polygon,boundary in boundaries:
        candidates = by_boundary[polygon_key(boundary)]
        for corner,point in enumerate(boundary):
            choices = [candidate.get(point) for candidate in candidates]
            if any(n is None for n in choices): continue
            if any(max(abs(a-b) for a,b in zip(n,choices[0]))>1e-6 for n in choices[1:]):
                raise ValueError("Ambiguous evaluated normals on duplicate polygon boundaries")
            result[polygon,corner,point] = choices[0]
    return result


def append_accessor(data, buffer, rows, kind, bounds=False, vertex=False):
    width = {"SCALAR":1,"VEC3":3,"VEC4":4}[kind]
    values = [v for row in rows for v in row]
    if any(len(row)!=width for row in rows): raise ValueError("Invalid animation array shape")
    raw = struct.pack("<"+"f"*len(values),*values)
    rounded = struct.unpack("<"+"f"*len(values),raw)
    if not all(math.isfinite(v) for v in rounded): raise ValueError("Animation values exceed float32 range")
    offset = len(buffer); buffer.extend(raw)
    view = len(data.setdefault("bufferViews",[]))
    data["bufferViews"].append({"buffer":0,"byteOffset":offset,"byteLength":len(raw)})
    if vertex: data["bufferViews"][-1]["target"] = 34962
    a = {"bufferView":view,"componentType":5126,"count":len(rows),"type":kind}
    if bounds: a.update(min=[min(rounded[j::width]) for j in range(width)],max=[max(rounded[j::width]) for j in range(width)])
    index = len(data.setdefault("accessors",[])); data["accessors"].append(a)
    return index


def overwrite_accessor(data, buffer, index, rows):
    a = data["accessors"][index]; view = data["bufferViews"][a["bufferView"]]
    if a["componentType"] != 5126 or a["count"] != len(rows): raise ValueError("Unexpected base geometry accessor")
    width = len(rows[0]); stride = view.get("byteStride",width*4)
    for i,row in enumerate(rows): struct.pack_into("<"+"f"*width,buffer,view.get("byteOffset",0)+a.get("byteOffset",0)+stride*i,*row)
    if "min" in a:
        a.update(min=[min(row[j] for row in rows) for j in range(width)],max=[max(row[j] for row in rows) for j in range(width)])


def normals(points):
    out = []
    for i in range(0,len(points),3):
        a,b,c = points[i:i+3]; u = [b[j]-a[j] for j in range(3)]; v = [c[j]-a[j] for j in range(3)]
        n = [u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]]
        length = math.sqrt(sum(x*x for x in n)); n = [x/length for x in n] if length else [0.,0.,1.]
        out.extend([n]*3)
    return out


def export_rig(package, manifest, rig, frames, provenance):
    """Keep the rest rig intact; write a separate evaluated animated derivative."""
    owner = rig["owner_item"]
    source = package / rig["gltf"]; data = json.loads(source.read_text("utf-8"))
    buffer = bytearray((source.parent/unquote(data["buffers"][0]["uri"])).read_bytes())
    mesh_nodes = [(i,n) for i,n in enumerate(data["nodes"]) if "mesh" in n]
    if len(mesh_nodes)!=1: raise ValueError("Native animation currently requires one mesh per rest rig")
    mesh_index,mesh_node = mesh_nodes[0]; mesh = data["meshes"][mesh_node["mesh"]]
    asset = next(a for a in manifest["assets"] if a["id"] == mesh["extras"]["source_sha256"])
    native_path = package/asset["uri"]; native = json.loads(native_path.read_text("utf-8")); geometry = (native_path.parent/native["buffer"]["uri"]).read_bytes()
    request = mesh["extras"]["source_layer_request"]
    samples = []; corner_samples = []
    needs_normals = any("NORMAL" in p["attributes"] for p in mesh["primitives"])
    for frame in frames:
        if owner not in frame["meshes"] or owner not in frame["items"]: raise ValueError("Native evaluator omitted the rig object")
        world_points = validate_mesh(frame["meshes"][owner],native,geometry,request)
        owner_inverse = inverse(frame["items"][owner]["matrix"])
        local = {}
        for point,world in world_points.items():
            p = transform(owner_inverse,world); local[point] = [p[0],p[1],-p[2]]
        samples.append(local)
        if needs_normals: corner_samples.append(evaluated_normals(frame["meshes"][owner],native,geometry,request,frame["items"][owner]["matrix"]))
    normal_profile = "native-evaluated-corner-normals-0.1" if corner_samples and all(n is not None for n in corner_samples) else "legacy-flat-triangle-normals-0.1"
    if corner_samples and any(n is None for n in corner_samples) and any(n is not None for n in corner_samples):
        raise ValueError("Mixed capture normal protocols across animation samples")
    times = [(f["time"]-frames[0]["time"],) for f in frames]
    if len(times)<2 or any(a[0]>=b[0] for a,b in zip(times,times[1:])): raise ValueError("Animation requires increasing capture times")
    time_accessor = append_accessor(data,buffer,times,"SCALAR",True)
    animation = {"name":Path(manifest["input"]).stem,"samplers":[],"channels":[],"extras":{"profile":"lightwave-evaluated-cage-0.1","source_first_frame":frames[0]["frame"],"source_last_frame":frames[-1]["frame"],"source_start_seconds":frames[0]["time"],"capture_sha256":provenance}}

    def track(node, path, values, kind):
        sampler = len(animation["samplers"])
        animation["samplers"].append({"input":time_accessor,"output":append_accessor(data,buffer,values,kind),"interpolation":"LINEAR"})
        animation["channels"].append({"sampler":sampler,"target":{"node":node,"path":path}})

    # Morph positions already contain the native deformation. Applying the
    # rest skin as well would deform the mesh a second time.
    data.pop("skins",None); mesh_node.pop("skin",None)
    data["scenes"][data["scene"]]["nodes"] = [0]
    data["nodes"][0].setdefault("children",[]).append(mesh_index)
    node_ids = {i:n["extras"]["source_node_id"] for i,n in enumerate(data["nodes"]) if i!=mesh_index}
    parents = {child:i for i,n in enumerate(data["nodes"]) for child in n.get("children",[])}
    animated_bones = 0
    for index,item in node_ids.items():
        node = data["nodes"][index]; values = []
        parent = node_ids.get(parents.get(index))
        for frame in frames:
            native_item = frame["items"][item]
            if parent is not None and native_item["parent"]!=parent: raise ValueError("Native bone parent differs from the IR rest hierarchy")
            matrix = native_item["matrix"]
            if parent is not None: matrix = multiply(inverse(frame["items"][parent]["matrix"]),matrix)
            values.append(decompose(reflected(matrix)))
        node.pop("matrix",None); node.update(copy.deepcopy(values[0])); changed = False
        for path,kind in (("translation","VEC3"),("rotation","VEC4"),("scale","VEC3")):
            rows = [value[path][:] for value in values]
            if path=="rotation":
                for i in range(1,len(rows)):
                    if sum(a*b for a,b in zip(rows[i-1],rows[i]))<0: rows[i] = [-v for v in rows[i]]
            if any(max(abs(a-b) for a,b in zip(row,rows[0]))>1e-8 for row in rows[1:]):
                track(index,path,rows,kind); changed = True
        if changed and index: animated_bones += 1
    mesh_node.pop("matrix",None)
    targets = len(frames)-1; max_displacement = 0.
    for primitive in mesh["primitives"]:
        attributes = primitive["attributes"]
        for key in list(attributes):
            if key.startswith(("JOINTS_","WEIGHTS_")): del attributes[key]
        mapping = primitive["extras"]["source_map"]
        corners = [struct.unpack_from("<III",buffer,mapping["byteOffset"]+12*i) for i in range(mapping["count"])]
        ids = [corner[2] for corner in corners]
        positions = [[sample[i] for i in ids] for sample in samples]
        normal_samples = None
        if "NORMAL" in attributes:
            if normal_profile == "native-evaluated-corner-normals-0.1":
                if any(corner not in sample for sample in corner_samples for corner in corners):
                    raise ValueError("Native evaluator omitted an exported corner normal")
                normal_samples = [[sample[corner] for corner in corners] for sample in corner_samples]
            else: normal_samples = [normals(p) for p in positions]
        overwrite_accessor(data,buffer,attributes["POSITION"],positions[0])
        if normal_samples: overwrite_accessor(data,buffer,attributes["NORMAL"],normal_samples[0])
        primitive["targets"] = []
        for i in range(1,len(frames)):
            delta = [[a-b for a,b in zip(p,q)] for p,q in zip(positions[i],positions[0])]
            max_displacement = max(max_displacement,max(math.sqrt(sum(v*v for v in d)) for d in delta))
            target = {"POSITION":append_accessor(data,buffer,delta,"VEC3",True,True)}
            if normal_samples:
                delta_normal = [[a-b for a,b in zip(p,q)] for p,q in zip(normal_samples[i],normal_samples[0])]
                target["NORMAL"] = append_accessor(data,buffer,delta_normal,"VEC3",vertex=True)
            primitive["targets"].append(target)
    mesh["weights"] = [0.]*targets
    mesh.setdefault("extras",{})["targetNames"] = [f"native_frame_{f['frame']}" for f in frames[1:]]
    weight_rows = [(float(sample==target+1),) for sample in range(len(frames)) for target in range(targets)]
    track(mesh_index,"weights",weight_rows,"SCALAR")
    data["animations"] = [animation]
    data["extras"].update(profile="lightwave-evaluated-cage-0.1",pose="native evaluated first capture frame",animation="sampled native transforms and deformed cage; LINEAR between samples",skin_status="native deformation captured as morph targets; original rig retained separately",subdivision="disabled; source point identities and polygon boundaries verified",capture_sha256=provenance)
    for key in ("skin_issue","skin_approximation","skin_limitations","volume_corrections_omitted"):
        data["extras"].pop(key,None)
    data["extras"].update(normal_profile=normal_profile,normals="evaluated world corner normals transformed to mesh local space; base NORMAL plus per-sample morph NORMAL deltas" if normal_profile.startswith("native-") else "protocol 1 compatibility: reconstructed flat triangle normals")
    animation["extras"]["normal_profile"] = normal_profile
    name = source.name.replace(f".rig-{owner:08x}",f".anim-{owner:08x}")
    destination = source.with_name(name); binary = destination.with_suffix(".bin")
    data["buffers"] = [{"uri":quote(binary.name,safe="-._~"),"byteLength":len(buffer)}]
    if destination.exists() or binary.exists(): raise FileExistsError(destination)
    binary.write_bytes(buffer); write_json(destination,data)
    return {"owner_item":owner,"gltf":destination.relative_to(package).as_posix(),"gltf_bin":binary.relative_to(package).as_posix(),"samples":len(frames),"first_frame":frames[0]["frame"],"last_frame":frames[-1]["frame"],"duration_seconds":times[-1][0],"animated_bones":animated_bones,"morph_targets":targets,"source_points":len(samples[0]),"maximum_local_vertex_displacement":max_displacement,"profile":"lightwave-evaluated-cage-0.1","normal_profile":normal_profile,"capture_sha256":provenance}
