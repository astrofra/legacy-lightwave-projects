"""Compare exported morph normals to native world corner normals at capture frames."""
import argparse
import json
import math
from pathlib import Path
import struct
import sys
from urllib.parse import unquote

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from lightwave_animation import read_capture, digest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package",type=Path); parser.add_argument("--report",type=Path,required=True)
    args=parser.parse_args(); package=args.package.resolve(); manifest=json.loads((package/"manifest.json").read_text())
    entry=manifest["gltf_animations"][0]; owner=entry["owner_item"]; path=package/entry["gltf"]
    data=json.loads(path.read_text()); raw=(path.parent/unquote(data["buffers"][0]["uri"])).read_bytes()
    capture_path=package/manifest["evaluated_animation"]["uri"]; capture=json.loads(capture_path.read_text())
    def rows(index):
        a=data["accessors"][index]; v=data["bufferViews"][a["bufferView"]]
        return [struct.unpack_from("<3f",raw,v.get("byteOffset",0)+a.get("byteOffset",0)+i*v.get("byteStride",12)) for i in range(a["count"])]
    results=[]
    for sample in (0,len(capture["frames"])//2,len(capture["frames"])-1):
        ref=capture["frames"][sample]; frame_path=capture_path.parent/ref["uri"]
        assert digest(frame_path)==ref["sha256"]
        frame=read_capture(frame_path); mesh=frame["meshes"][owner]; matrix=frame["items"][owner]["matrix"]
        columns=[matrix[4*i:4*i+3] for i in range(3)]; scales=[sum(v*v for v in col) for col in columns]
        assert max(abs(sum(a*b for a,b in zip(columns[i],columns[j]))) for i in range(3) for j in range(i+1,3))<1e-6
        count=0; maximum=0.; changed=0
        for primitive in data["meshes"][0]["primitives"]:
            if "NORMAL" not in primitive["attributes"]: continue
            base=rows(primitive["attributes"]["NORMAL"]); delta=rows(primitive["targets"][sample-1]["NORMAL"]) if sample else [(0,0,0)]*len(base)
            mapping=primitive["extras"]["source_map"]
            for i,(n,d) in enumerate(zip(base,delta)):
                polygon,corner,point=struct.unpack_from("<III",raw,mapping["byteOffset"]+12*i)
                assert mesh["polygons"][polygon][corner]==mesh["points"][point]["id"]
                local=[a+b for a,b in zip(n,d)]; local[2]*=-1
                world=[sum(columns[c][r]*local[c]/scales[c] for c in range(3)) for r in range(3)]
                length=math.sqrt(sum(v*v for v in world)); world=[v/length for v in world]
                target=mesh["corner_normals"][polygon,corner]
                maximum=max(maximum,math.sqrt(sum((a-b)**2 for a,b in zip(world,target))))
                count+=1; changed+=sum(v*v for v in d)>1e-16
        assert maximum<2e-6,(sample,maximum)
        results.append({"frame":frame["frame"],"compared_corners":count,"changed_normals_from_first_frame":changed,"maximum_world_normal_difference":maximum})
    report={"package":str(package),"gltf_sha256":digest(path),"capture_manifest_sha256":digest(capture_path),"normal_profile":data["extras"]["normal_profile"],"samples":entry["samples"],"results":results}
    args.report.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8"); print(json.dumps(report))


if __name__=="__main__": main()
