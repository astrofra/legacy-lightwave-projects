"""Check published Amiga scene sources, implicit cameras and original camera keys."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct

ROOT=Path(__file__).resolve().parents[2]
TARGETS={"NastyStation","PassMountains","Station1","StationChase","ThingKills"}


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def check_camera(raw, scene, animation):
    cameras=[n for n in scene["nodes"] if n["id"]>>28==3]
    assert len(cameras)==1
    camera=cameras[0]; assert camera["parent"] is None
    lines=[line.strip() for line in raw.splitlines() if line.strip()]
    begin=next(i for i,line in enumerate(lines) if line.startswith(b"CameraMotion"))
    channels,count=int(lines[begin+1]),int(lines[begin+2]); assert channels==9
    assert len(camera["channels"])==channels
    verified=0
    for k in range(count):
        values=list(map(float,lines[begin+3+2*k].split()))
        frame,linear,tension,continuity,bias=map(float,lines[begin+4+2*k].split())
        for channel in camera["channels"]:
            span=channel["keys"]; assert span["count"]==count
            row=struct.unpack_from("<8d2I",animation,span["offset"]+k*span["stride"])
            assert row==(frame,values[channel["index"]],tension,continuity,bias,0,0,0,3 if linear==1 else 0,0)
            verified+=1
    return {"item_id":camera["id"],"channels":channels,"keyframes":count,"verified_channel_keys":verified,"source_offset":camera["source_offset"]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch",type=Path,required=True)
    parser.add_argument("--report",type=Path,required=True)
    parser.add_argument("--content",type=Path,default=ROOT/"content/aminet-atmobjs")
    args=parser.parse_args(); run=args.batch.resolve(); batch=json.loads((run/"batch-report.json").read_text("utf-8"))
    assert not batch["counts"].get("failed",0)
    records=[]
    for entry in batch["files"]:
        if entry["source"] not in TARGETS: continue
        manifest_path=run/entry["manifest"]; manifest=json.loads(manifest_path.read_text("utf-8"))
        source=args.content/entry["source"]; raw=source.read_bytes()
        uris=[a["uri"] for a in manifest["assets"]]+([manifest["scene"]] if manifest["scene"] else [])
        for uri in uris:
            path=manifest_path.parent/uri; native=json.loads(path.read_text("utf-8"))
            assert digest(path.parent/native["source"]["uri"])==native["source"]["sha256"]
            assert digest(Path(native["source"]["path"]))==native["source"]["sha256"]
        gltf=run/entry["gltf"]; obj=run/entry["obj"]
        assert gltf.is_file() and obj.is_file()
        record={"source":entry["source"],"source_sha256":digest(source),"status":entry["status"],"source_preserved":True,
                "manifest":str(manifest_path.relative_to(ROOT)),"obj":str(obj.relative_to(ROOT)),"gltf":str(gltf.relative_to(ROOT)),
                "gltf_sha256":digest(gltf),"obj_sha256":digest(obj),"unresolved_object_instances":manifest["unresolved_object_instances"],
                "animation_channels":manifest["gltf_animation_channels"],"animation_samples":manifest["gltf_animation_samples"]}
        if manifest["scene"]:
            path=manifest_path.parent/manifest["scene"]; scene=json.loads(path.read_text("utf-8"))
            assert (path.parent/"source.bin").read_bytes()==raw
            record["camera"]=check_camera(raw,scene,(path.parent/scene["animation_buffer"]["uri"]).read_bytes())
            record["missing_references"]=dict(Counter(n["object_path"]["text"] for n in scene["nodes"] if n["object_path"] and n["asset_index"] is None))
            record["rest_rigs"]=manifest["gltf_rigs"]
        else:
            path=manifest_path.parent/manifest["assets"][0]["uri"]; native=json.loads(path.read_text("utf-8"))
            record["geometry"]={"points":native["positions"]["count"],"source_polygons":native["primitives"]["count"],
                                "triangles":manifest["gltf_triangles"],"nonplanar_faces":manifest["gltf_nonplanar_faces"],
                                "triangulation_failures":manifest["gltf_triangulation_failures"],"skipped_primitives":manifest["gltf_skipped_primitives"],
                                "normal_issues":manifest["gltf_normal_issues"],"textures":[t for m in native["materials"] for t in m["textures"]]}
        records.append(record)
    assert {r["source"] for r in records}==TARGETS
    validations=[json.loads(p.read_text("utf-8")) for p in (run/"packages").rglob("*.report.json")]
    report={"date":"2026-09-12","batch":str(run.relative_to(ROOT)),"batch_counts":batch["counts"],"sources":records,
            "gltf_validation":{"version":"2.0.0-dev.3.10","files":len(validations),"errors":sum(r["issues"]["numErrors"] for r in validations),"warnings":sum(r["issues"]["numWarnings"] for r in validations)}}
    args.report.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8",newline="\n")
    print(json.dumps({"sources":len(records),"camera_keys_verified":sum(r.get("camera",{}).get("verified_channel_keys",0) for r in records),"validation":report["gltf_validation"]}))


if __name__=="__main__": main()
