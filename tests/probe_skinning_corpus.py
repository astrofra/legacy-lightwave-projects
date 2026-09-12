"""Optional corpus QA: the C converter computes weights; LightWave measures poses."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from procedural_skin_checks import position_errors
from probe_skinning_oracle import ROOT
from export_lightwave_animation import prepare_scene
from lightwave_animation import digest, inverse, multiply, read_capture, transform, validate_mesh, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--content-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--end", type=int, default=2)
    parser.add_argument("--step", type=int, default=2)
    parser.add_argument("--disable-volume", action="store_true")
    parser.add_argument("--disable-morphs", action="store_true")
    parser.add_argument("--lightwave-root", type=Path, default=ROOT/"_tmp/_extern/LightWave/LW9.6")
    args = parser.parse_args()
    output = args.output.resolve(); output.mkdir(); package = output/"package"
    converter = ROOT/"bin/win64/lwconvert.exe"; helper = ROOT/"bin/win64/lw_capture.p"
    result = subprocess.run([str(converter), "convert", str(args.source.resolve()), "--content-root", str(args.content_root.resolve()), "--output", str(package)], capture_output=True, timeout=60)
    if result.returncode not in (0,2): raise RuntimeError(result.stderr.decode("utf-8"))
    manifest = json.loads((package/"manifest.json").read_text("utf-8"))
    scene = json.loads((package/manifest["scene"]).read_text("utf-8"))
    assets = [{**a,"source_copy":(package/a["uri"]).parent/"source.bin"} for a in manifest["assets"]]
    evaluation = output/"evaluation"; evaluation.mkdir()
    # Only the observed empty command-recorder block is removed for research.
    # Nonempty masters still go through prepare_scene's qualification checks.
    source = args.source.read_bytes()
    source, empty_commanders = re.subn(rb"(?m)^Plugin MasterHandler \d+ LW_LScriptCommander\r?\nEndPlugin\r?\n", b"", source)
    source, volumes = re.subn(rb"(?m)^Bone(?:JointComp|MuscleFlex)(?:Parent)? [^\r\n]*", b"", source) if args.disable_volume else (source,0)
    source, morphs = re.subn(rb"(?ms)^Plugin DisplacementHandler \d+ LW_MorphMixer\r?\n.*?^EndPlugin\r?\n", b"", source) if args.disable_morphs else (source,0)
    working, config, audit = prepare_scene(source, scene, assets, evaluation, args.lightwave_root.resolve(), helper)
    audit.update(empty_command_recorders_removed=empty_commanders, volume_parameters_removed=volumes, morph_mixers_removed=morphs)
    frames = evaluation/"frames"; frames.mkdir()
    env = os.environ.copy(); env["LWCONVERT_CAPTURE_DIR"] = str(frames); env["LWCONVERT_CAPTURE_BONES"] = "1"
    host = args.lightwave_root.resolve()/"Programs/lwsn.exe"
    with (evaluation/"screamernet.log").open("wb") as log:
        result = subprocess.run([str(host), "-3", "-c"+str(config), "-d"+str(evaluation), str(working), str(args.start), str(args.end), str(args.step)], cwd=evaluation, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=120, creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
    if result.returncode: raise RuntimeError(f"ScreamerNet failed: {result.returncode}")
    paths = [frames/f"frame-{i:06d}.txt" for i in range(args.start,args.end+1,args.step)]
    captures = [read_capture(path) for path in paths]
    records = []
    for rig in manifest["gltf_rigs"]:
        record = {"owner_item":rig["owner_item"],"status":rig["status"],"issue":rig["issue"],"volume_corrections_omitted":rig["volume_corrections_omitted"]}
        if rig.get("derived_skin"):
            skin = json.loads((package/rig["derived_skin"]).read_text("utf-8"))
            owner = scene["nodes"][rig["owner_node"]]; asset = assets[owner["asset_index"]]
            native = json.loads((package/asset["uri"]).read_text("utf-8")); geometry = (package/asset["uri"]).with_name("geometry.bin").read_bytes()
            poses = []
            for capture in captures:
                inv = inverse(capture["items"][rig["owner_item"]]["matrix"])
                points = validate_mesh(capture["meshes"][rig["owner_item"]],native,geometry,owner["layer_request"])
                poses.append({"joints":[multiply(inv,capture["items"][item]["matrix"]) for item in skin["joint_items"][1:]],"positions":{i:transform(inv,p) for i,p in points.items()}})
            record["error"] = position_errors(package/rig["gltf"], poses)
        records.append(record)
    report = {"source":str(args.source),"source_sha256":digest(args.source),"converter_sha256":digest(converter),"host_sha256":digest(host),"helper_sha256":digest(helper),"captures":[{"frame":c["frame"],"sha256":digest(path)} for path,c in zip(paths,captures)],"audit":audit,"rigs":records}
    write_json(output/"report.json",report)
    print(json.dumps(records))


if __name__ == "__main__": main()
