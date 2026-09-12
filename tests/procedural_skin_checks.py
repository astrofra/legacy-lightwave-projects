"""Compare exported glTF skinning with captured native positions, without LightWave."""
import json
import math
from pathlib import Path
import struct
from urllib.parse import unquote


def accessor(data, buffer, index):
    a = data["accessors"][index]; v = data["bufferViews"][a["bufferView"]]
    count = {"VEC3": 3, "VEC4": 4, "MAT4": 16}[a["type"]]
    fmt = "<" + {5123: "H", 5126: "f"}[a["componentType"]] * count
    stride = v.get("byteStride", struct.calcsize(fmt))
    offset = v.get("byteOffset", 0) + a.get("byteOffset", 0)
    return [struct.unpack_from(fmt, buffer, offset + stride*i) for i in range(a["count"])]


def transform(matrix, point):
    return [sum(matrix[4*k+j]*point[k] for k in range(3))+matrix[12+j] for j in range(3)]


def multiply(a, b):
    return [sum(a[4*k+r]*b[4*c+k] for k in range(4)) for c in range(4) for r in range(4)]


def node_matrix(node):
    if "matrix" in node: return node["matrix"]
    x,y,z,w = node.get("rotation", [0,0,0,1]); sx,sy,sz = node.get("scale", [1,1,1])
    tx,ty,tz = node.get("translation", [0,0,0])
    return [(1-2*(y*y+z*z))*sx,2*(x*y+z*w)*sx,2*(x*z-y*w)*sx,0,
            2*(x*y-z*w)*sy,(1-2*(x*x+z*z))*sy,2*(y*z+x*w)*sy,0,
            2*(x*z+y*w)*sz,2*(y*z-x*w)*sz,(1-2*(x*x+y*y))*sz,0,tx,ty,tz,1]


def world_matrices(data):
    result = {}; identity = [int(i%5==0) for i in range(16)]
    def visit(index, parent):
        if index in result: raise ValueError("Repeated node or cycle in glTF scene")
        result[index] = multiply(parent,node_matrix(data["nodes"][index]))
        for child in data["nodes"][index].get("children",[]): visit(child,result[index])
    for root in data["scenes"][data["scene"]]["nodes"]: visit(root,identity)
    return result


def skin_rows(path):
    data = json.loads(Path(path).read_text("utf-8"))
    buffer = Path(path).parent.joinpath(unquote(data["buffers"][0]["uri"])).read_bytes()
    binds = accessor(data, buffer, data["skins"][0]["inverseBindMatrices"])
    rows = []
    for mesh in data["meshes"]:
        for primitive in mesh["primitives"]:
            attrs = primitive["attributes"]; positions = accessor(data, buffer, attrs["POSITION"])
            sets = sum(k.startswith("JOINTS_") for k in attrs)
            joints = [accessor(data, buffer, attrs[f"JOINTS_{i}"]) for i in range(sets)]
            weights = [accessor(data, buffer, attrs[f"WEIGHTS_{i}"]) for i in range(sets)]
            source = primitive["extras"]["source_map"]
            for i, position in enumerate(positions):
                point = struct.unpack_from("<3I", buffer, source["byteOffset"]+12*i)[2]
                influences = [(j, w) for s in range(sets) for j, w in zip(joints[s][i], weights[s][i]) if w]
                rows.append((point, position, influences))
    return binds, rows


def position_errors(path, captures):
    """captures: native object-local bone matrices and source-indexed positions."""
    binds, rows = skin_rows(path); errors = []
    for capture in captures:
        for point, position, influences in rows:
            actual = [0.,0.,0.]
            for joint, weight in influences:
                local = transform(binds[joint], position)
                local[2] *= -1
                deformed = transform(capture["joints"][joint-1], local) if joint else local
                for j in range(3): actual[j] += weight*deformed[j]
            expected = capture["positions"][point]
            errors.append(math.dist(actual, expected))
    return {"samples": len(errors), "max": max(errors, default=0),
            "rms": math.sqrt(sum(x*x for x in errors)/max(1,len(errors)))}


def rest_errors(path):
    """Evaluate the published node hierarchy itself, without oracle matrices."""
    data = json.loads(Path(path).read_text("utf-8")); world = world_matrices(data)
    binds, rows = skin_rows(path)
    transforms = [multiply(world[j],b) for j,b in zip(data["skins"][0]["joints"],binds)]
    matrix_error = max(abs(v-int(i%5==0)) for m in transforms for i,v in enumerate(m))
    errors = []
    for _,position,influences in rows:
        actual = [0.,0.,0.]
        for joint,weight in influences:
            point = transform(transforms[joint],position)
            for k in range(3): actual[k] += weight*point[k]
        errors.append(math.dist(actual,position))
    return {"vertices":len(rows),"maximum_bind_identity_error":matrix_error,
            "maximum_rest_position_error":max(errors,default=0)}
