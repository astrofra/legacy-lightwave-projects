"""Controlled bone-deformation experiments; LightWave is a development oracle only."""
import argparse
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from export_lightwave_animation import prepare_scene
from lightwave_animation import digest, read_capture, write_json


def chunk(tag, data):
    return tag.encode() + struct.pack(">I", len(data)) + data + b"\0" * (len(data) % 2)


def mesh_bytes(points, maps=()):
    body = b"LWO2" + chunk("LAYR", struct.pack(">HH3f", 0, 0, 0, 0, 0) + b"probe\0")
    body += chunk("PNTS", struct.pack(">" + "f" * (len(points) * 3), *(v for p in points for v in p)))
    body += chunk("POLS", b"FACE" + b"".join(struct.pack(">4H", 3, len(points)-1, i, i+1) for i in range(len(points)-2)))
    for name, weights in maps:
        text = name.encode() + b"\0"
        text += b"\0" * (len(text) % 2)
        body += chunk("VMAP", b"WGHT\0\1" + text + b"".join(struct.pack(">Hf", i, w) for i, w in weights))
    return b"FORM" + struct.pack(">I", len(body)) + body


def motion(kind, values, moved=None):
    out = kind + "Motion\nNumChannels 9\n"
    for index, value in enumerate(values):
        keys = [(0, value)] if moved is None or moved[index] == value else [(0, value), (1/25, moved[index])]
        out += f"Channel {index}\n{{ Envelope\n  {len(keys)}\n"
        out += "".join(f"  Key {v:.17g} {time:.17g} 3 0 0 0 0 0 0\n" for time, v in keys)
        out += "  Behaviors 1 1\n}\n"
    return out


def scene_bytes(cases):
    out = "LWSC\n3\nFirstFrame 0\nLastFrame 1\nFramesPerSecond 25\n"
    for owner, case in enumerate(cases):
        out += f"LoadObject probe-{owner}.lwo\n" + motion("Object", [0,0,0,0,0,0,1,1,1])
        out += f"BoneFalloffType {case.get('falloff', 1)}\nFasterBones {case.get('faster', 0)}\n"
        for index, bone in enumerate(case.get("bones", [{"position":[0,0,0]}, {"position":[2,0,0]}])):
            position = bone.get("position", [0,0,0]); rotation = bone.get("rotation", [0,0,0])
            rest = [*position, *rotation, 1,1,1]
            moved = rest[:]
            if index == case.get("probe_bone", 0):
                moved[case.get("probe_channel", 1)] += case.get("delta", 0.5)
            if "moved" in bone: moved = bone["moved"]
            out += f"AddBone\nBoneName bone{index}\nBoneRestPosition {' '.join(map(str,position))}\n"
            out += "BoneRestDirection " + " ".join(str(math.degrees(v)) for v in rotation) + "\n"
            out += f"BoneRestLength {bone.get('length', 1)}\nBoneActive {bone.get('active', 1)}\n"
            out += f"BoneStrength {bone.get('strength', 1)}\nScaleBoneStrength {bone.get('scale_strength', 0)}\n"
            out += f"BoneWeightMapOnly {bone.get('map_only', 0)}\nBoneNormalization {bone.get('normalize', 1)}\n"
            if "map" in bone: out += f"BoneWeightMapName {bone['map']}\n"
            if "range" in bone:
                out += f"BoneLimitedRange 1\nBoneMinRange {bone['range'][0]}\nBoneMaxRange {bone['range'][1]}\n"
            parent = (0x40000000 | bone["parent"] << 16 | owner) if "parent" in bone else (0x10000000 | owner)
            out += motion("Bone", rest, moved) + f"ParentItem {parent:08x}\n"
    return out.encode()


def run(directory, cases, points, lightwave, converter, helper):
    # A distant non-grid fan anchor keeps probe vertices on nondegenerate faces.
    points = [*points, [-3.12345, 4.4321, 5.3723]]
    directory = Path(directory).resolve(); directory.mkdir()
    source = directory / "source"; source.mkdir()
    for i, case in enumerate(cases):
        (source / f"probe-{i}.lwo").write_bytes(mesh_bytes(points, case.get("maps", ())))
    path = source / "probe.lws"; path.write_bytes(scene_bytes(cases))
    package = directory / "package"
    result = subprocess.run([str(converter), "convert", str(path), "--output", str(package), "--content-root", str(source), "--gltf-rigs", "all"], capture_output=True, timeout=60)
    if result.returncode not in (0,2): raise RuntimeError(result.stderr.decode("utf-8"))
    manifest = json.loads((package / "manifest.json").read_text("utf-8"))
    scene = json.loads((package / manifest["scene"]).read_text("utf-8"))
    assets = [{**a, "source_copy": (package / a["uri"]).parent / "source.bin"} for a in manifest["assets"]]
    evaluation = directory / "evaluation"; evaluation.mkdir()
    working, config, audit = prepare_scene(path.read_bytes(), scene, assets, evaluation, lightwave, helper)
    frames = evaluation / "frames"; frames.mkdir()
    env = os.environ.copy(); env["LWCONVERT_CAPTURE_DIR"] = str(frames); env["LWCONVERT_CAPTURE_BONES"] = "1"
    command = [str(lightwave / "Programs/lwsn.exe"), "-3", "-c"+str(config), "-d"+str(evaluation), str(working), "0", "1", "1"]
    with (evaluation / "screamernet.log").open("wb") as log:
        result = subprocess.run(command, cwd=evaluation, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=120, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode: raise RuntimeError(f"ScreamerNet failed: {result.returncode}")
    captures = [read_capture(frames / f"frame-{i:06d}.txt") for i in (0,1)]
    observations = []
    for i, case in enumerate(cases):
        base = captures[0]["meshes"][0x10000000 | i]["points"]
        moved = captures[1]["meshes"][0x10000000 | i]["points"]
        assert len(base) == len(moved) == len(points)
        weights = []
        for a, b in zip(base, moved):
            assert a["base"] == b["base"]
            weights.append({"point": a["base"], "rest": a["world"], "moved": b["world"], "y_displacement_over_probe_delta": (b["world"][1]-a["world"][1])/case.get("delta", 0.5)})
        bone_count = len(case.get("bones", [0,1]))
        matrices = [[capture["items"][0x40000000 | j << 16 | i]["matrix"] for j in range(bone_count)] for capture in captures]
        observations.append({"case": case, "weights": weights, "joint_matrices": matrices})
    report = {"host_sha256": digest(lightwave / "Programs/lwsn.exe"), "helper_sha256": digest(helper),
              "source_sha256": digest(path), "captures": [digest(frames / f"frame-{i:06d}.txt") for i in (0,1)],
              "audit": audit, "observations": observations}
    write_json(directory / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", type=Path, help="JSON containing cases and points")
    parser.add_argument("--lightwave-root", type=Path, default=ROOT / "_tmp/_extern/LightWave/LW9.6")
    parser.add_argument("--converter", type=Path, default=ROOT / "bin/win64/lwconvert.exe")
    parser.add_argument("--capture-plugin", type=Path, default=ROOT / "bin/win64/lw_capture.p")
    args = parser.parse_args()
    config = json.loads(args.cases.read_text("utf-8")) if args.cases else {
        "cases": [{"name": f"falloff-{i}", "falloff": i} for i in range(10)],
        "points": [[x,y,z] for z in (-1,0,.25,.5,1,2) for y in (.125,.5,2) for x in (-1,0,.25,.5,.75,1,1.25,1.5,1.75,2,3)]}
    report = run(args.output, config["cases"], config["points"], args.lightwave_root.resolve(), args.converter.resolve(), args.capture_plugin.resolve())
    print(json.dumps({"output": str(args.output), "cases": len(report["observations"]), "points": len(config["points"])}))


if __name__ == "__main__": main()
