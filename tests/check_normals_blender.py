"""Import OBJ/glTF in Blender and compare every corner normal to the file values.

blender --background --factory-startup --python tests/check_normals_blender.py --
    --report REPORT.json FILE.obj FILE.gltf ...
"""
import argparse
import json
import math
from pathlib import Path
import struct
import sys
from urllib.parse import unquote


def expected(path):
    if path.suffix==".obj":
        points=[]; normals=[]; result=[]
        for line in path.read_text("utf-8").splitlines():
            if line.startswith("v "): points.append(tuple(map(float,line.split()[1:])))
            elif line.startswith("vn "): normals.append(tuple(map(float,line.split()[1:])))
            elif line.startswith("f "):
                for token in line.split()[1:]:
                    p,_,n=token.split("/"); result.append((points[int(p)-1],normals[int(n)-1]))
        return result
    data=json.loads(path.read_text("utf-8")); raw=(path.parent/unquote(data["buffers"][0]["uri"])).read_bytes()
    def rows(index):
        a=data["accessors"][index]; v=data["bufferViews"][a["bufferView"]]
        return [struct.unpack_from("<3f",raw,v.get("byteOffset",0)+a.get("byteOffset",0)+i*v.get("byteStride",12)) for i in range(a["count"])]
    result=[]
    for mesh in data["meshes"]:
        for p in mesh["primitives"]:
            if p.get("mode",4)==4: result.extend(zip(rows(p["attributes"]["POSITION"]),rows(p["attributes"]["NORMAL"])))
    return result


def main():
    import bpy
    from mathutils import Vector
    from mathutils.kdtree import KDTree
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--report",type=Path,required=True); p.add_argument("files",type=Path,nargs="+")
    args=p.parse_args(sys.argv[sys.argv.index("--")+1:]); results=[]
    for path in args.files:
        path=path.resolve(); source=expected(path)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        if path.suffix==".obj":
            bpy.ops.wm.obj_import(filepath=str(path),forward_axis="NEGATIVE_Z",up_axis="Y",use_split_objects=False,use_split_groups=False)
        else: bpy.ops.import_scene.gltf(filepath=str(path),merge_vertices=False)
        actual=[]
        for obj in bpy.context.scene.objects:
            if obj.type!="MESH": continue
            m=obj.data; normal_matrix=obj.matrix_world.to_3x3().inverted().transposed()
            for i,loop in enumerate(m.loops):
                actual.append((obj.matrix_world@m.vertices[loop.vertex_index].co,normal_matrix@m.corner_normals[i].vector))
        assert len(actual)==len(source),(path,len(actual),len(source))
        tree=KDTree(len(actual))
        for i,(position,_) in enumerate(actual): tree.insert(position,i)
        tree.balance(); used=set(); error=0.; position_error=0.; worst=None
        for position,normal in source:
            position=Vector((position[0],-position[2],position[1])); normal=Vector((normal[0],-normal[2],normal[1]))
            tolerance=max(1e-6,position.length*2e-6)
            choices=[((actual[i][1].normalized()-normal).length,i,d) for _,i,d in tree.find_range(position,tolerance) if i not in used]
            assert choices,(path,tuple(position))
            distance,i,pos=min(choices); used.add(i)
            if distance>error: worst={"expected":list(normal),"actual":list(actual[i][1]),"position":list(position)}
            error=max(error,distance); position_error=max(position_error,pos)
        # Blender stores custom split normals with limited angular precision.
        assert error<.005,(path,error,worst)
        results.append({"path":str(path),"corners":len(source),"maximum_normal_vector_error":error,"maximum_position_error":position_error,"worst_normal":worst})
        print(json.dumps(results[-1]),flush=True)
    args.report.write_text(json.dumps({"blender":bpy.app.version_string,"results":results},indent=2)+"\n",encoding="utf-8")


if __name__=="__main__": main()
