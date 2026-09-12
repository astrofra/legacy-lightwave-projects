"""Independent native measurements exercise the standalone C weight exporter."""
import copy
import json
import math
from pathlib import Path
import subprocess
import sys
import unittest

import test_converter as fixtures
from probe_skinning_oracle import mesh_bytes, scene_bytes
from procedural_skin_checks import position_errors, rest_errors, skin_rows

REFERENCE = json.loads(Path(__file__).with_name("fixtures").joinpath("procedural_skin_oracle.json").read_text("utf-8"))


class ProceduralSkinTests(unittest.TestCase):
    setUp = fixtures.Converter.setUp
    tearDown = fixtures.Converter.tearDown
    write = fixtures.Converter.write
    run_cli = fixtures.Converter.run_cli
    convert = fixtures.Converter.convert

    def test_lightwave6_missing_map_fallback_against_native_measurements(self):
        reference=json.loads(Path(__file__).with_name("fixtures").joinpath("lightwave6_skin_oracle.json").read_text("utf-8"))
        cases=reference["cases"]
        for i,case in enumerate(cases):
            self.write(f"probe-{i}.lwo",mesh_bytes([p["point"] for p in case["observations"]],case["case"].get("maps",())))
        source=scene_bytes([c["case"] for c in cases])
        out,manifest=self.convert(self.write("probe.lws",source),code=2)
        self.assertEqual(manifest["skin_profile"],"lightwave6")
        self.assertEqual(manifest["skin_profile_policy"],"oldest-supported-for-file")
        self.assertEqual((out/manifest["scene"]).with_name("source.bin").read_bytes(),source)
        self.assertEqual(len(manifest["gltf_rigs"]),8)
        for rig,case in zip(manifest["gltf_rigs"],cases):
            with self.subTest(case=case["case"]["name"]):
                captures=[{"joints":case["joint_matrices"][i],"positions":[p["rest" if i==0 else "moved"] for p in case["observations"]]} for i in (0,1)]
                self.assertLess(position_errors(out/rig["gltf"],captures)["max"],1e-6)
                derived=json.loads((out/rig["derived_skin"]).read_text())
                self.assertEqual(derived["profile"],reference["profile"])
                self.assertEqual(derived["missing_map_procedural_fallbacks"],int(case["case"]["name"].startswith("missing")))

    def test_c_weights_and_gltf_deformation_against_native_captures(self):
        observations = [c for run in REFERENCE["runs"] for c in run["cases"]]
        for i, c in enumerate(observations):
            self.write(f"probe-{i}.lwo", mesh_bytes([o["point"] for o in c["observations"]],c["case"].get("maps",())))
        source = scene_bytes([c["case"] for c in observations])
        out, manifest = self.convert(self.write("probe.lws",source), "--skin-profile", "lightwave96", code=2)
        self.assertEqual(manifest["skin_profile_policy"],"explicit")
        self.assertEqual((out/manifest["scene"]).with_name("source.bin").read_bytes(),source)
        self.assertEqual(len(manifest["gltf_rigs"]),len(observations))
        for rig, c in zip(manifest["gltf_rigs"],observations):
            with self.subTest(case=c["case"]["name"]):
                if c["blocked"]:
                    self.assertIn(c["blocked"],rig["issue"])
                    self.assertIsNone(rig["gltf"])
                    self.assertIsNone(rig["derived_skin"])
                    continue
                captures = [{"joints":c["joint_matrices"][i],"positions":[p["rest" if i==0 else "moved"] for p in c["observations"]]} for i in (0,1)]
                rest = rest_errors(out/rig["gltf"])
                self.assertLess(rest["maximum_bind_identity_error"],2e-6,rest)
                self.assertLess(rest["maximum_rest_position_error"],4e-6,rest)
                if c.get("explicit"):
                    self.assertEqual(rig["status"],"explicit-weight-map-skin")
                    self.assertIsNone(rig["derived_skin"])
                    self.assertLess(position_errors(out/rig["gltf"],captures)["max"],4e-6)
                    continue
                self.assertEqual(rig["status"],"procedural-weight-skin-approximation")
                skin = json.loads((out/rig["derived_skin"]).read_text("utf-8"))
                self.assertTrue(skin["approximation"])
                self.assertEqual(skin["profile"],REFERENCE["profile"])
                self.assertEqual(skin["owner_item"],rig["owner_item"])
                self.assertEqual(len(skin["points"]),len(c["observations"]))
                binds, rows = skin_rows(out/rig["gltf"])
                self.assertEqual(len(binds),len(skin["joint_items"]))
                for point, _, weights in rows:
                    self.assertTrue(all(0 <= j < len(binds) and math.isfinite(w) and w > 0 for j,w in weights))
                    self.assertAlmostEqual(sum(w for _,w in weights),1,places=6)
                    self.assertEqual([j for j,_ in weights],[j for j,_ in skin["points"][point]])
                    for (_,a),(_,b) in zip(weights,skin["points"][point]): self.assertAlmostEqual(a,b,places=8)
                error = position_errors(out/rig["gltf"],captures)
                self.assertGreater(error["samples"],0)
                self.assertLess(error["max"],4e-6,error)

    def basic(self, case):
        points = [[0,.5,0],[1,.5,0],[1,.5,1],[0,.5,1]]
        self.write("probe-0.lwo",mesh_bytes(points,case.get("maps",())))
        return self.convert(self.write("probe.lws",scene_bytes([case])),code=2)

    def test_invalid_and_unknown_fields_do_not_invent_a_skin(self):
        cases = [({"falloff":100},"falloff"),
                 ({"bones":[{"position":[0,0,0],"strength":-1}]},"strength"),
                 ({"bones":[{"position":[0,0,0],"range":[2,1]}]},"range"),
                 ({"bones":[{"position":[0,0,0],"map":"m"}],"maps":[["m",[[0,-.5]]]]},"negative")]
        for case,issue in cases:
            with self.subTest(issue=issue):
                _,m=self.basic(case);rig=m["gltf_rigs"][0]
                self.assertIsNone(rig["derived_skin"])
                self.assertIsNone(rig["gltf"])
                self.assertIn(issue,rig["issue"])

    def test_volume_corrections_are_declared_in_both_ir_and_gltf(self):
        self.write("probe-0.lwo",mesh_bytes([[0,0,0],[1,0,0],[0,1,1]]))
        source=scene_bytes([{"falloff":6}]).replace(b"BoneActive 1",b"BoneActive 1\nBoneJointComp 1\nBoneMuscleFlexParent .3",1)
        out,m=self.convert(self.write("probe.lws",source),code=2);rig=m["gltf_rigs"][0]
        self.assertEqual(rig["volume_corrections_omitted"],1)
        skin=json.loads((out/rig["derived_skin"]).read_text("utf-8"))
        gltf=json.loads((out/rig["gltf"]).read_text("utf-8"))
        self.assertEqual(skin["volume_corrections_omitted"],1)
        self.assertEqual(gltf["extras"]["volume_corrections_omitted"],1)
        native=json.loads((out/m["scene"]).read_text("utf-8"))
        self.assertEqual(native["nodes"][1]["bone"]["joint_compensation"],[1,0])

    def test_batch_keeps_derived_weights_and_their_native_source(self):
        case=copy.deepcopy(REFERENCE["runs"][0]["cases"][1]["case"])
        self.basic(case)
        batch=Path(__file__).resolve().parents[1]/"tools/batch_convert.py"
        result=subprocess.run([sys.executable,"-X","utf8",str(batch),"--content",str(self.root),"--output-root",str(self.base/"batch"),"--converter",fixtures.EXE],capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,2,result.stdout+result.stderr)
        from check_output_layout import check
        run=next((self.base/"batch").glob("batch-*"))
        self.assertTrue(check(run)["passed"])
        files=list((run/"packages").rglob("skin-*.json"));self.assertEqual(len(files),1)
        manifest=json.loads(files[0].with_name("manifest.json").read_text("utf-8"))
        self.assertEqual(manifest["gltf_rigs"][0]["derived_skin"],files[0].name)


if __name__ == "__main__": unittest.main()
