"""Read native bindings and glTF skin buffers; exercise actual joint deformation."""
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import unittest

import test_converter as fixtures
from test_converter import F32, U16, chunk, form, s0, vx
from test_gltf import load, multiply, source_map, world_matrices

IDENTITY = [int(i % 5 == 0) for i in range(16)]


def accessor(data, buffers, index):
    a = data["accessors"][index]
    view = data["bufferViews"][a["bufferView"]]
    size = {"VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[a["type"]]
    fmt = "<" + {5123: "H", 5126: "f"}[a["componentType"]] * size
    width = struct.calcsize(fmt)
    start = view.get("byteOffset", 0) + a.get("byteOffset", 0)
    stride = view.get("byteStride", width)
    assert a.get("byteOffset", 0) + stride * (a["count"] - 1) + width <= view["byteLength"]
    return [struct.unpack_from(fmt, buffers[view["buffer"]], start + i * stride) for i in range(a["count"])]


def weight_map(name, entries, discontinuous=False):
    return chunk("VMAD" if discontinuous else "VMAP", b"WGHT" + U16(1) + s0(name) + b"".join(
        vx(point) + (vx(0) if discontinuous else b"") + F32(value) for point, value in entries))


def object_bytes(maps=None, patch=False):
    if maps is None:
        maps = b"".join(weight_map(f"weight{i}", [(0, i+1)] + ([(1, 1)] if i == 0 else []) + ([(2, i+1)] if i in (1, 2) else [])) for i in range(6))
    # Two triangles with a UV seam on their shared vertex. Skinning must follow
    # source point IDs after triangulation, normal and UV corner expansion.
    return form("LWO2", chunk("PNTS", F32(0,0,1, 1,0,1, 1,1,1, 0,1,1)),
                chunk("POLS", (b"PTCH" if patch else b"FACE") + U16(3)+vx(0)+vx(1)+vx(2)+U16(3)+vx(0)+vx(2)+vx(3)),
                chunk("VMAP", b"TXUV"+U16(2)+s0("uv")+b"".join(vx(i)+F32(i/4, i/4) for i in range(4))),
                chunk("VMAD", b"TXUV"+U16(2)+s0("uv")+vx(0)+vx(1)+F32(.75,.25)), maps)


def bone(index=0, owner=0, parent=None, name=None, position="0 0 0", rotation="0 0 0", extra="", active=1):
    parent = (0x10000000 | owner) if parent is None else parent
    return (f"AddBone\nBoneName joint{index}\nBoneRestPosition {position}\nBoneRestDirection {rotation}\nBoneRestLength 1\n"
            f"BoneActive {active}\nBoneWeightMapName {name or ('weight'+str(index))}\nBoneWeightMapOnly 1\nBoneNormalization 1\n"
            f"ScaleBoneStrength 1\nBoneStrength 1\nParentItem {parent:08x}\n{extra}")


def scene_bytes(bones=None, extra="", load="LoadObject rig.lwo"):
    if bones is None:
        bones = bone(0, position=".4 .5 .6", rotation="20 10 30")
        bones += bone(1, parent=0x40000000, position="0 0 1", rotation="10 0 0")
        bones += "".join(bone(i, position=f"0 {i/10} 0") for i in range(2, 6))
        bones += bone(6, active=0, name="absent", extra="BoneWeightMapOnly 0\n")
    return "LWSC\n3\nFirstFrame 0\nLastFrame 0\nFramesPerSecond 30\n" + load + "\nSubPatchLevel 2 3\nSubdivisionOrder 1\n" + extra + bones


def transform(m, point):
    return tuple(sum(m[4*k+r] * (point[k] if k < 3 else 1) for k in range(4)) for r in range(3))


class SkinTests(unittest.TestCase):
    setUp = fixtures.Converter.setUp
    tearDown = fixtures.Converter.tearDown
    write = fixtures.Converter.write
    run_cli = fixtures.Converter.run_cli
    convert = fixtures.Converter.convert

    def rig(self, bones=None, maps=None, extra="", patch=False, policy="skins", profile="auto"):
        self.write("rig.lwo", object_bytes(maps, patch))
        out, manifest = self.convert(self.write("rig.lws", scene_bytes(bones, extra)), "--uv-map", "uv", "--gltf-rigs", policy, "--skin-profile", profile, code=2)
        result = manifest["gltf_rigs"][0]
        data, buffers = load(out / result["gltf"]) if result["gltf"] else (None, None)
        return out, manifest, result, data, buffers

    def test_native_bindings_multiple_influence_sets_and_deformation(self):
        out, manifest, result, data, buffers = self.rig()
        native = json.loads((out / manifest["scene"]).read_text("utf-8"))
        bones = [n for n in native["nodes"] if n["bone"]]
        self.assertEqual(len(bones), 7)
        self.assertEqual(bones[1]["bone"]["rest_position"], [0,0,1])
        self.assertEqual(bones[0]["bone"]["rest_rotation_hpb_degrees"], [20,10,30])
        self.assertEqual(bones[0]["bone"]["weight_map"]["text"], "weight0")
        self.assertEqual(bones[0]["bone"]["weight_map_status"], "weight-map-only")
        self.assertEqual(bones[0]["bone"]["owner_item"], 0x10000000)
        settings = {v["name"]["text"]: v["value"]["text"] for v in native["nodes"][0]["rig_parameters"]}
        self.assertEqual(settings["SubPatchLevel"], "2 3")
        self.assertEqual(settings["SubdivisionOrder"], "1")
        self.assertEqual((out / manifest["scene"]).parent.joinpath("source.bin").read_bytes(), scene_bytes().encode())
        self.assertEqual(result["status"], "explicit-weight-map-skin")
        self.assertEqual(result["influence_sets"], 2)
        self.assertEqual(result["unweighted_points_on_object_anchor"], 1)
        self.assertNotIn("snapshot_frame", data["extras"])
        skin = data["skins"][0]
        world = world_matrices(data)
        binds = accessor(data, buffers, skin["inverseBindMatrices"])
        self.assertEqual(len(skin["joints"]), 8)
        for node, inverse in zip(skin["joints"], binds):
            for actual, expected in zip(multiply(world[node], inverse), IDENTITY):
                self.assertAlmostEqual(actual, expected, places=6)
        expected_weights = {0: {i+1: (i+1)/21 for i in range(6)}, 1: {1: 1}, 2: {2: .4, 3: .6}, 3: {0: 1}}
        duplicated = []
        for primitive in data["meshes"][0]["primitives"]:
            attrs = primitive["attributes"]
            positions = accessor(data, buffers, attrs["POSITION"])
            joints = [accessor(data, buffers, attrs[f"JOINTS_{s}"]) for s in range(2)]
            weights = [accessor(data, buffers, attrs[f"WEIGHTS_{s}"]) for s in range(2)]
            for row, (_, _, point) in enumerate(source_map(primitive, buffers)):
                influence = {j: w for s in range(2) for j, w in zip(joints[s][row], weights[s][row]) if w}
                self.assertEqual(influence.keys(), expected_weights[point].keys())
                self.assertAlmostEqual(sum(influence.values()), 1, places=6)
                for j, w in influence.items(): self.assertAlmostEqual(w, expected_weights[point][j], places=6)
                # Move just one joint in world space. Expected displacement is
                # its explicit normalized weight, including at duplicated corners.
                deformed = [0., 0., 0.]
                for j, w in influence.items():
                    moved = world[skin["joints"][j]][:]
                    if j == 1: moved[12] += .7
                    position = transform(multiply(moved, binds[j]), positions[row])
                    for axis in range(3): deformed[axis] += w * position[axis]
                for axis in range(3):
                    self.assertAlmostEqual(deformed[axis], positions[row][axis] + (.7 * expected_weights[point].get(1, 0) if axis == 0 else 0), places=6)
                if point == 0: duplicated.append(influence)
        self.assertEqual(len(duplicated), 2)
        self.assertEqual(duplicated[0], duplicated[1])

    def test_unsupported_bindings_remain_explicitly_unbound(self):
        cases = [
            (bone(extra="BoneWeightMapOnly 0\n"), None, "procedural"),
            (bone(name="missing"), None, "missing"),
            (bone(), weight_map("weight0", [(0, -.5)]), "negative"),
            (bone(), weight_map("weight0", [(0, .5), (0, .2)]), "duplicate"),
            (bone(), weight_map("weight0", [(0, 1)], True), "continuous"),
            (bone(extra="BoneNormalization 0\n"), None, "non-normalized"),
            (bone(extra="BoneJointComp 2\n"), None, "compensation"),
            (bone(active=0), None, "no active bones"),
        ]
        for bones, maps, issue in cases:
            with self.subTest(issue=issue):
                _, _, result, data, _ = self.rig(bones, maps, policy="all", profile="lightwave96")
                self.assertEqual(result["status"], "skeleton-only")
                self.assertIn(issue, result["issue"])
                self.assertNotIn("skins", data)
                self.assertFalse(any(k.startswith("JOINTS_") for m in data["meshes"] for p in m["primitives"] for k in p["attributes"]))

    def test_unbound_rest_exports_are_optional_without_changing_ir(self):
        bones = bone(extra="BoneWeightMapOnly 0\n")
        self.write("rig.lwo", object_bytes())
        source = self.write("rig.lws", scene_bytes(bones))
        compact, manifest = self.convert(source, code=2)
        full, prior = self.convert(source, "--gltf-rigs", "all", code=2)
        rig = manifest["gltf_rigs"][0]
        self.assertEqual(manifest["gltf_rig_policy"], "skins")
        self.assertEqual((rig["status"], rig["export_status"]), ("skeleton-only", "omitted-by-policy"))
        self.assertIsNone(rig["gltf"])
        self.assertIsNone(rig["gltf_bin"])
        self.assertIn("procedural", rig["issue"])
        self.assertEqual((manifest["gltf_files"], prior["gltf_files"]), (2, 3))
        self.assertEqual(list((compact / "gltf").glob("*.rig-*")), [])
        self.assertEqual(len(list((full / "gltf").glob("*.rig-*"))), 2)
        for path in (compact / "IR").rglob("*"):
            if path.is_file(): self.assertEqual(path.read_bytes(), (full / path.relative_to(compact)).read_bytes())
        self.assertEqual((compact / manifest["scene_gltf"]).read_bytes(), (full / prior["scene_gltf"]).read_bytes())
        self.assertEqual((compact / manifest["scene_gltf_bin"]).read_bytes(), (full / prior["scene_gltf_bin"]).read_bytes())
        self.run_cli("convert", source, "--output", self.base / "invalid", "--gltf-rigs", "invalid", code=1)
        self.assertFalse((self.base / "invalid").exists())

    def test_batch_unbound_rig_policy_is_applied_to_published_files(self):
        self.write("rig.lwo", object_bytes())
        self.write("rig.lws", scene_bytes(bone(extra="BoneWeightMapOnly 0\n")))
        batch = Path(__file__).resolve().parents[1] / "tools/batch_convert.py"
        from check_output_layout import check
        for policy, count in (("skins", 0), ("all", 1)):
            output = self.base / policy
            result = subprocess.run([sys.executable, "-X", "utf8", str(batch), "--content", str(self.root), "--output-root", str(output), "--converter", fixtures.EXE, "--gltf-rigs", policy], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            run = next(output.glob("batch-*"))
            self.assertTrue(check(run)["passed"])
            report = json.loads((run / "batch-report.json").read_text("utf-8"))
            record = next(r for r in report["files"] if r["source"].endswith(".lws"))
            self.assertEqual(len(record["rig_gltf"]), count)
            self.assertEqual(len(list((run / "packages").rglob("*.rig-*.gltf"))), count)

    def test_zeroed_rest_angles_use_recorded_pivot_rotation(self):
        _, _, result, data, buffers = self.rig(bone(extra="PivotRotation 90 0 0\n"))
        self.assertEqual(result["status"], "explicit-weight-map-skin")
        world = world_matrices(data)
        # Native bone +Z becomes glTF -Z, and a +90 degree native heading
        # rotates that direction to world +X.
        for a, b in zip(transform(world[1], (0,0,-1)), (1,0,0)):
            self.assertAlmostEqual(a, b)
        inverse = accessor(data, buffers, data["skins"][0]["inverseBindMatrices"])[1]
        for a, b in zip(multiply(world[1], inverse), IDENTITY): self.assertAlmostEqual(a, b)

    def test_unqualified_rest_and_cyclic_hierarchy_are_reported(self):
        cases = [
            (bone().replace("BoneRestDirection 0 0 0\n", ""), "missing rest"),
            (bone(rotation="10 0 0", extra="PivotRotation 0 20 0\n"), "combined rest"),
            (bone(extra="PivotPosition 1 0 0\n"), "translated bone pivot"),
            (bone(parent=0x40000000), "cyclic"),
        ]
        for bones, issue in cases:
            with self.subTest(issue=issue):
                _, _, result, data, _ = self.rig(bones)
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["bones"], 1)
                self.assertIsNone(data)
                self.assertIn(issue, result["issue"])

    def test_rest_rig_survives_unsupported_scene_motion(self):
        _, manifest, result, data, _ = self.rig(extra="Plugin ItemMotionHandler 1 unknown\nEndPlugin\n")
        self.assertIsNone(manifest["scene_gltf"])
        self.assertEqual(result["status"], "explicit-weight-map-skin")
        self.assertIn("skins", data)

    def test_patch_cage_is_never_subdivided(self):
        _, _, result, data, _ = self.rig(patch=True)
        self.assertEqual(result["status"], "explicit-weight-map-skin")
        self.assertEqual(sum(data["accessors"][p["attributes"]["POSITION"]]["count"] for p in data["meshes"][0]["primitives"]), 6)
        self.assertEqual(data["extras"]["subdivision"], "control cage retained; never baked")

    def test_layer_selected_maps_and_multiple_object_instances(self):
        from test_converter import layer
        self.write("rig.lwo", form("LWO2", layer(0)+weight_map("weight0", [(0, -1)]), layer(1)+weight_map("weight0", [(0, 1), (1, 1), (2, 1)])))
        scene = scene_bytes(bone(), load="LoadObjectLayer 2 rig.lwo")
        scene += "LoadObjectLayer 2 rig.lwo\n" + bone(owner=1)
        out, manifest = self.convert(self.write("layers.lws", scene), code=2)
        self.assertEqual(len(manifest["gltf_rigs"]), 2)
        for i, result in enumerate(manifest["gltf_rigs"]):
            self.assertEqual(result["owner_item"], 0x10000000+i)
            self.assertEqual(result["status"], "explicit-weight-map-skin")
            self.assertEqual(result["unweighted_points_on_object_anchor"], 0)
            data, buffers = load(out / result["gltf"])
            for p in data["meshes"][0]["primitives"]:
                self.assertEqual(accessor(data, buffers, p["attributes"]["WEIGHTS_0"]), [(1,0,0,0)] * 3)

    def test_batch_publishes_rig_links_and_buffers(self):
        self.write("rig.lwo", object_bytes())
        self.write("rig.lws", scene_bytes())
        batch = Path(__file__).resolve().parents[1] / "tools/batch_convert.py"
        result = subprocess.run([sys.executable, "-X", "utf8", str(batch), "--content", str(self.root), "--output-root", str(self.base / "batch"), "--converter", fixtures.EXE], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 2, result.stdout+result.stderr)
        from check_output_layout import check
        run = next((self.base / "batch").glob("batch-*"))
        self.assertTrue(check(run)["passed"])
        report = json.loads((run / "batch-report.json").read_text("utf-8"))
        record = next(r for r in report["files"] if r["source"].endswith(".lws"))
        self.assertEqual(len(record["rig_gltf"]), 1)
        self.assertNotIn("..", Path(record["rig_gltf"][0]).parts)
        self.assertIn("skins", load(run / record["rig_gltf"][0])[0])


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--fixture":
        root = Path(sys.argv[2]).resolve()
        source = root / "content"
        source.mkdir(parents=True, exist_ok=True)
        (source / "rig.lwo").write_bytes(object_bytes())
        (source / "rig.lws").write_text(scene_bytes(), encoding="utf-8")
        command = [fixtures.EXE, "convert", str(source / "rig.lws"), "--content-root", str(source), "--output", str(root / "package"), "--uv-map", "uv"]
        result = subprocess.run(command)
        raise SystemExit(0 if result.returncode == 2 else 1)
    unittest.main()
