"""Compare background Blender glTF imports with the converter's OBJ triangles."""
import argparse
import json
from itertools import permutations, product
import math
from pathlib import Path
import subprocess
import sys


def compare_triangles(expected, actual):
    assert len(expected) == len(actual), (len(expected),len(actual))
    if not expected: return 0.0, 0.0
    coordinates = [p for triangle in expected for p in triangle]
    extent = max(max(p[k] for p in coordinates)-min(p[k] for p in coordinates) for k in range(3))
    tolerance = max(1e-6,extent*1e-5)
    cell = lambda triangle: tuple(math.floor(sum(p[k] for p in triangle)/3/tolerance) for k in range(3))
    buckets = {}
    for index,triangle in enumerate(actual): buckets.setdefault(cell(triangle),[]).append(index)
    worst = 0.0
    for triangle in expected:
        center = cell(triangle)
        match = None
        for delta in product((-1,0,1),repeat=3):
            bucket = buckets.get(tuple(center[k]+delta[k] for k in range(3)),[])
            for index in bucket:
                distance = min(max(math.dist(a,b) for a,b in zip(triangle,order)) for order in permutations(actual[index]))
                if distance <= tolerance and (match is None or distance < match[2]):
                    match = (bucket,index,distance)
        assert match is not None, {"unmatched_triangle":triangle,"tolerance":tolerance}
        bucket,index,distance = match
        bucket.remove(index)
        worst = max(worst,distance)
    return tolerance,worst


def worker(gltfs, objs, report):
    import bpy
    results = []
    assert bpy.app.background
    for gltf, obj in zip(gltfs, objs):
        vertices, triangles = [], []
        with obj.open(encoding="utf-8") as reader:
            for line in reader:
                fields = line.split()
                if not fields: continue
                if fields[0] == "v": vertices.append(tuple(map(float,fields[1:])))
                elif fields[0] == "f":
                    assert len(fields) == 4, "Choose an OBJ with triangulated faces for this check"
                    triangles.append([vertices[int(c.split('/')[0])-1] for c in fields[1:]])
        bpy.ops.wm.read_factory_settings(use_empty=True)
        assert bpy.ops.import_scene.gltf(filepath=str(gltf)) == {"FINISHED"}
        actual = []
        for item in bpy.context.scene.objects:
            if item.type != "MESH": continue
            for polygon in item.data.polygons:
                points = [item.matrix_world @ item.data.vertices[i].co for i in polygon.vertices]
                # Blender imports glTF Y-up into its Z-up world: invert that basis.
                actual.append([(p.x,p.z,-p.y) for p in points])
        tolerance,error = compare_triangles(triangles,actual)
        results.append({"gltf":str(gltf),"obj_reference":str(obj),"triangles":len(actual),"mesh_instances":sum(o.type=="MESH" for o in bpy.context.scene.objects),"position_tolerance":tolerance,"maximum_matched_corner_distance":error})
    report.write_text(json.dumps({"blender_version":bpy.app.version_string,"background":True,"passed":results,"scope":"World-space triangle position multisets compared to OBJ after axis conversion, within max(1e-6, 1e-5 * scene extent). Each imported triangle is matched once; vertex splitting and ordering may differ. Covers hierarchy and instancing through Blender import. Does not compare winding, shading, textures or animation."},indent=2)+"\n",encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender",type=Path)
    parser.add_argument("--gltf",type=Path,action="append",required=True)
    parser.add_argument("--obj",type=Path,action="append",required=True)
    parser.add_argument("--report",type=Path,required=True)
    parser.add_argument("--worker",action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else None)
    if len(args.gltf)!=len(args.obj): parser.error("Supply one --obj reference per --gltf, in matching order")
    if args.worker:
        worker(args.gltf,args.obj,args.report)
    else:
        if not args.blender: parser.error("--blender is required")
        command=[str(args.blender),"--background","--factory-startup","--python-exit-code","1","--python",str(Path(__file__).resolve()),"--","--worker","--report",str(args.report.resolve())]
        for path in args.gltf: command += ["--gltf",str(path.resolve())]
        for path in args.obj: command += ["--obj",str(path.resolve())]
        subprocess.run(command,check=True,timeout=120)


if __name__ == "__main__":
    main()
