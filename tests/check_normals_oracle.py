"""Compare synthetic shading cases with the user's LightWave 9.6 runtime."""
import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from export_lightwave_animation import prepare_scene
from lightwave_animation import digest, inverse, read_capture, write_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--lightwave-root",type=Path,required=True)
    parser.add_argument("--converter",type=Path,default=ROOT/"bin/win64/lwconvert.exe")
    parser.add_argument("--capture-plugin",type=Path,default=ROOT/"bin/win64/lw_capture.p")
    args=parser.parse_args(); directory=args.output.resolve(); directory.mkdir()
    # Fixture module's CLI convention consumes the converter argument.
    sys.argv.insert(1,str(args.converter.resolve()))
    from test_normals import wedge, surface, normal_map
    from test_converter import chunk,form,U16,F32,s0
    from test_gltf import load,source_map,values
    lw=args.lightwave_root.resolve(); plugin=args.capture_plugin.resolve()
    cases={"smooth":wedge(),"hard":wedge(1.5),"groups":wedge(groups=[2,3]),
           "both_surfaces_smooth":wedge(surfaces=[surface("mat",2),surface("other",2)]),
           "different_angles":wedge(surfaces=[surface("mat",2),surface("other",1.5)]),
           "neighbour_disabled":wedge(surfaces=[surface("mat",2),surface("other",0)]),
           "legacy_flag":wedge(None,legacy=True,flags=4),"positive_scale":wedge(),"negative_scale":wedge(),
           "explicit_normals":wedge(maps=normal_map()+normal_map(discontinuous=True,entries=[(0,1,(0,0,4))]))}
    results=[]
    for name,data in cases.items():
        source=directory/name; source.mkdir()
        if data[8:12]==b"LWO2": data=form("LWO2",chunk("LAYR",U16(0)+U16(0)+F32(0,0,0)+s0("wedge")),data[12:])
        (source/"wedge.lwo").write_bytes(data)
        channels=[0,0,0,0,0,0,1,1,1]
        if name.endswith("_scale"): channels=[0,0,0,.2,.3,.4,2,3,4 if name=="positive_scale" else -4]
        scene_source="LWSC\n3\nFirstFrame 0\nLastFrame 1\nFramesPerSecond 30\nLoadObject wedge.lwo\nObjectMotion\nNumChannels 9\n"
        scene_source+="".join(f"Channel {i}\n{{ Envelope\n  1\n  Key {v} 0 3 0 0 0 0 0 0\n  Behaviors 1 1\n}}\n" for i,v in enumerate(channels))
        path=source/"probe.lws"; path.write_text(scene_source)
        package=directory/(name+"-package")
        run=subprocess.run([str(args.converter.resolve()),"convert",str(path),"--output",str(package),"--content-root",str(source)],capture_output=True,timeout=30)
        assert run.returncode in (0,2),run.stderr
        manifest=json.loads((package/"manifest.json").read_text()); scene=json.loads((package/manifest["scene"]).read_text())
        assets=[{**a,"source_copy":(package/a["uri"]).parent/"source.bin"} for a in manifest["assets"]]
        evaluation=source/"evaluation"; evaluation.mkdir()
        working,config,audit=prepare_scene(path.read_bytes(),scene,assets,evaluation,lw,plugin)
        frames=evaluation/"frames"; frames.mkdir(); env=os.environ.copy(); env["LWCONVERT_CAPTURE_DIR"]=str(frames)
        with (evaluation/"log.txt").open("wb") as log:
            run=subprocess.run([str(lw/"Programs/lwsn.exe"),"-3","-c"+str(config),"-d"+str(evaluation),str(working),"0","0","1"],cwd=evaluation,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
        assert run.returncode==0
        capture_path=frames/"frame-000000.txt"; capture=read_capture(capture_path)
        mesh=capture["meshes"][0x10000000]; matrix=capture["items"][0x10000000]["matrix"]; inv=inverse(matrix)
        assert len(mesh["points"])==4 and len(mesh["corner_normals"])==6
        data,buffers=load(package/manifest["assets"][0]["gltf"]); comparisons=[]
        for p in data["meshes"][0]["primitives"]:
            for key,n in zip(source_map(p,buffers),values(data,buffers,p["attributes"]["NORMAL"])):
                native=mesh["corner_normals"][key[:2]]; n=[n[0],n[1],-n[2]]
                target=[sum(inv[4*r+c]*n[c] for c in range(3)) for r in range(3)]
                length=math.sqrt(sum(v*v for v in target)); target=[v/length for v in target]
                comparisons.append({"polygon":key[0],"corner":key[1],"native_world_normal":native,"gltf_world_normal":target,"difference":math.sqrt(sum((a-b)**2 for a,b in zip(native,target)))})
        error=max(r["difference"] for r in comparisons)
        expected_difference=name in {"groups","positive_scale","negative_scale","explicit_normals"}
        if not expected_difference: assert error<2e-6,(name,error)
        results.append({"case":name,"maximum_difference":error,"requires_interpretation":expected_difference,
                        "source_sha256":digest(source/"wedge.lwo"),"capture_sha256":digest(capture_path),
                        "package":package.relative_to(ROOT).as_posix(),"audit":audit,"matrix":matrix,"corners":comparisons})
        print(json.dumps({"case":name,"maximum_difference":error}),flush=True)
    write_json(directory/"report.json",{"date":"2026-09-12","host_sha256":digest(lw/"Programs/lwsn.exe"),"helper_sha256":digest(plugin),"converter_sha256":digest(args.converter),"cases":results})


if __name__=="__main__": main()
