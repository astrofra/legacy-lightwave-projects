"""Reconvert the recorded shading corpus subset and compare actual OBJ/glTF normals."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import struct
import subprocess
from urllib.parse import unquote

ROOT=Path(__file__).resolve().parents[2]
PROJECTS={"aliens@newtek","butterfly-tank","orange-juice-signage","smila-by-moebius","demo-redline-assets-main","carrot_driven_robot"}


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def accessor(data,raw,index):
    a=data["accessors"][index]; v=data["bufferViews"][a["bufferView"]]
    width={"VEC3":3,"VEC2":2}[a["type"]]
    assert a["componentType"]==5126
    return [struct.unpack_from("<"+"f"*width,raw,v.get("byteOffset",0)+a.get("byteOffset",0)+i*v.get("byteStride",4*width)) for i in range(a["count"])]


def check_package(package):
    m=json.loads((package/"manifest.json").read_text("utf-8")); result=[]
    for asset in m["assets"]:
        native_path=package/asset["uri"]; native=json.loads(native_path.read_text("utf-8"))
        geometry=(native_path.parent/"geometry.bin").read_bytes()
        assert digest(native_path.parent/"source.bin")==asset["id"]
        obj=package/asset["obj"]; gltf=package/asset["gltf"]
        data=json.loads(gltf.read_text("utf-8")); raw=(gltf.parent/unquote(data["buffers"][0]["uri"])).read_bytes()
        expected={}; counts=Counter(); max_length_error=0.; different=0
        for mesh in data["meshes"]:
            for p in mesh["primitives"]:
                if p.get("mode",4)!=4: continue
                n=accessor(data,raw,p["attributes"]["NORMAL"]); mapping=p["extras"]["source_map"]
                for i,normal in enumerate(n):
                    assert all(math.isfinite(v) for v in normal)
                    max_length_error=max(max_length_error,abs(math.sqrt(sum(v*v for v in normal))-1))
                    polygon,corner,point=struct.unpack_from("<III",raw,mapping["byteOffset"]+12*i)
                    # Repeated point corners with explicit VMAD share a value.
                    expected[polygon,point]=normal; counts[polygon,point]+=1
                for i in range(0,len(n),3):
                    different+=any(max(abs(a-b) for a,b in zip(n[i],other))>1e-6 for other in n[i+1:i+3])
        normals=[]; actual=Counter(); error=0.; polygon=None; obj_cage_only=0; faces_without_normals=0
        for line in obj.read_text("utf-8").splitlines():
            if line.startswith("vn "): normals.append(tuple(map(float,line.split()[1:])))
            elif line.startswith("# source_primitive "): polygon=int(line.split()[-1])
            elif line.startswith("f "):
                if any(len(token.split("/"))!=3 or not token.split("/")[2] for token in line.split()[1:]):
                    faces_without_normals+=1
                    assert m["obj_normal_issues"]>0,(obj,line)
                    continue
                for token in line.split()[1:]:
                    fields=token.split("/"); assert len(fields)==3 and fields[2],(obj,line)
                    key=(polygon,int(fields[0])-1)
                    actual[key]+=1; normal=normals[int(fields[2])-1]
                    assert abs(math.sqrt(sum(v*v for v in normal))-1)<2e-6
                    if key in expected: error=max(error,max(abs(a-b) for a,b in zip(normal,expected[key])))
                    else:
                        primitive=struct.unpack_from("<9I",geometry,native["primitives"]["offset"]+polygon*36)
                        assert primitive[2] in (int.from_bytes(b"PTCH","big"),int.from_bytes(b"PCHS","big")),(obj,key)
                        obj_cage_only+=1
        # OBJ patch cages retain n-gons; glTF triangulates their derivative.
        assert counts.keys()<=actual.keys(),obj
        assert max_length_error<2e-6 and error<2e-6,(obj,max_length_error,error)
        result.append({"source":asset["source_path"],"source_sha256":asset["id"],"source_preserved":True,
                       "obj":obj.relative_to(ROOT).as_posix(),"gltf":gltf.relative_to(ROOT).as_posix(),
                       "gltf_sha256":digest(gltf),"obj_sha256":digest(obj),"shading":native["shading"],
                       "gltf_normal_corners":sum(counts.values()),"obj_normal_corners":sum(actual.values()),
                       "triangles_with_varying_corner_normals":different,"maximum_unit_length_error":max_length_error,
                       "maximum_obj_gltf_normal_difference":error,"normal_issues":{"obj":m["obj_normal_issues"],"gltf":m["gltf_normal_issues"]}})
        result[-1].update(obj_cage_corners_without_gltf_triangle=obj_cage_only,obj_degenerate_cage_faces_without_normals=faces_without_normals)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--converter",type=Path,default=ROOT/"bin/win64/lwconvert.exe")
    p.add_argument("--inventory",type=Path,default=Path(__file__).with_name("normals-inventory.json"))
    args=p.parse_args(); output=args.output.resolve(); output.mkdir(); records=[]; failures=[]
    inventory=json.loads(args.inventory.read_text("utf-8"))
    for row in inventory["objects"]:
        if row["path"].split("/")[1] not in PROJECTS: continue
        source=ROOT/row["path"]; package=output/f"object-{len(records)+len(failures):03}"
        try:
            assert digest(source)==row["sha256"],"Source differs from inventory"
            command=[str(args.converter.resolve()),"convert",str(source),"--output",str(package),"--content-root",str(source.parent)]
            run=subprocess.run(command,capture_output=True,timeout=120)
            assert run.returncode in (0,2),run.stderr.decode("utf-8",errors="replace")
            results=check_package(package)
            for r in results: r["conversion_exit_code"]=run.returncode
            records.extend(results)
        except Exception as e: failures.append({"source":row["path"],"issue":str(e)})
        if (len(records)+len(failures))%20==0: print(f"Checked {len(records)} objects, {len(failures)} failures",flush=True)
    report={"date":"2026-09-12","converter_sha256":digest(args.converter),"inventory_summary":inventory["summary"],
            "projects":sorted(PROJECTS),"objects_checked":len(records),"failures":failures,"objects":records}
    (output/"report.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"objects":len(records),"failures":failures,"report":str(output/"report.json")}))
    return bool(failures)


if __name__=="__main__": raise SystemExit(main())
