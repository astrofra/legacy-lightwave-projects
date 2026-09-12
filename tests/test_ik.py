"""Independent IK endpoints, bounded rotations, skin playback and publication."""
import copy
import hashlib
import json
import math
from pathlib import Path
import struct
import sys
import unittest

import test_converter as fixtures
import test_skin as skin
from test_animation import values
from test_gltf import load, world_matrices

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from output_layout import ProjectOutput


def motion(kind="Bone", **channels):
    text = f"{kind}Motion\nNumChannels 9\n"
    for axis in range(9):
        keys = channels.get(str(axis), [(0, 0 if axis < 6 else 1)])
        text += f"Channel {axis}\n{{ Envelope\n{len(keys)}\n"
        text += "".join(f"Key {value} {time} 3 0 0 0 0 0 0\n" for time, value in keys)
        text += "Behaviors 1 1\n}\n"
    return text


def scene(extra="", ik=True):
    text = "LWSC\n3\nFirstFrame 0\nLastFrame 2\nFramesPerSecond 1\nLoadObject rig.lwo\nIKAnchor 1\n"
    text += motion("Object", **{"0": [(0, 3), (2, 4)]})
    text += skin.bone(0, extra=motion(**{"3": [(0, .3), (2, .5)]}) + ("HController 3\nHLimits -90 90\n" if ik else "") + extra)
    text += skin.bone(1, parent=0x40000000, position="0 0 1", extra=motion(**{"2": [(0, 1)], "3": [(0, 1), (2, .8)]}) + ("HController 3\nHLimits 0 170\n" if ik else ""))
    text += "AddNullObject end\nParentItem 40010000\n" + motion("Object", **{"2": [(0, 1)]})
    if ik: text += "GoalObject 3\nFullTimeIK 1\nGoalStrength 1\n"
    text += "AddNullObject target\nParentItem 10000000\n" + motion("Object", **{"0": [(0, 1), (2, .5)], "2": [(0, 1), (2, 1.2)]})
    return text


class IKTests(unittest.TestCase):
    setUp = fixtures.Converter.setUp
    tearDown = fixtures.Converter.tearDown
    write = fixtures.Converter.write
    run_cli = fixtures.Converter.run_cli
    convert = fixtures.Converter.convert

    def bake(self, text=None, *args):
        self.write("rig.lwo", skin.object_bytes(skin.weight_map("weight1", [(i, 1) for i in range(4)])))
        source = self.write("rig.lws", text or scene())
        out, manifest = self.convert(source, *args, code=2)
        return source, out, manifest

    def poses(self, out, manifest):
        path = out / manifest["autonomous_animation"]["uri"]
        meta = json.loads(path.read_text())
        raw = (path.parent / meta["buffer"]["uri"]).read_bytes()
        self.assertEqual(len(raw), meta["buffer"]["byte_length"])
        return meta, [[struct.unpack_from("<20f", raw, (frame * len(meta["node_ids"]) + i) * 80)
                       for i in range(len(meta["node_ids"]))] for frame in range(meta["samples"])], raw

    def test_moving_goal_parent_anchor_and_source_preservation(self):
        source, out, m = self.bake()
        meta, frames, raw = self.poses(out, m)
        ids = meta["node_ids"]
        for frame, poses in enumerate(frames):
            end = poses[ids.index(0x10000001)][10:13]
            goal = poses[ids.index(0x10000002)][10:13]
            self.assertLess(math.dist(end, goal), 2e-5)
            self.assertAlmostEqual(goal[0], 4 + frame * .25, places=5)
            self.assertAlmostEqual(goal[2], 1 + frame * .1, places=5)
        self.assertEqual(meta["goals"], 1)
        self.assertEqual(meta["source_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual((out/m["scene"]).with_name("source.bin").read_bytes(), source.read_bytes())
        _, off, disabled = self.bake(scene(), "--bake-ik", "off")
        self.assertEqual(disabled["autonomous_animation"]["status"], "disabled")
        self.assertEqual((off/disabled["scene"]).read_bytes(), (out/m["scene"]).read_bytes())
        self.assertEqual((off/disabled["scene"]).with_name("animation.bin").read_bytes(), (out/m["scene"]).with_name("animation.bin").read_bytes())
        _, again, repeated = self.bake()
        self.assertEqual(self.poses(again, repeated)[2], raw)

    def test_primary_skin_animation_buffers_and_joint_worlds(self):
        _, out, m = self.bake()
        meta, frames, _ = self.poses(out, m)
        self.assertEqual(m["scene_gltf"], m["gltf_rigs"][0]["gltf"])
        self.assertEqual(len(list((out/"gltf").glob("*.gltf"))), 2)  # asset + scene
        data, buffers = load(out/m["scene_gltf"])
        self.assertEqual(len(data["skins"]), 1)
        self.assertEqual(data["extras"]["profile"], "autonomous-hpb-ik-0.1")
        self.assertEqual(m["gltf_animation_samples"], 3)
        animation = data["animations"][0]
        for frame in range(3):
            current = copy.deepcopy(data)
            for ch in animation["channels"]:
                sampler = animation["samplers"][ch["sampler"]]
                self.assertEqual(values(data, buffers[0], sampler["input"]), [(0,), (1,), (2,)])
                current["nodes"][ch["target"]["node"]][ch["target"]["path"]] = values(data, buffers[0], sampler["output"])[frame]
            world = world_matrices(current)
            for index in data["skins"][0]["joints"]:
                node = data["nodes"][index]
                source = meta["node_ids"].index(node["extras"]["source_node_id"])
                expected = frames[frame][source][10:13]
                self.assertLess(math.dist(world[index][12:15], (expected[0], expected[1], -expected[2])), 2e-6)
        primitive = data["meshes"][0]["primitives"][0]
        self.assertIn("WEIGHTS_0", primitive["attributes"])
        self.assertIn("JOINTS_0", primitive["attributes"])

    def test_batch_publication_keeps_one_primary_and_derived_bake(self):
        source, out, m = self.bake()
        destination = self.base/"published"
        path = ProjectOutput(destination, [source, self.root/"rig.lwo"], source_root=self.root).publish(out, m)
        published = json.loads(path.read_text())
        self.assertEqual(published["scene_gltf"], published["gltf_rigs"][0]["gltf"])
        self.assertEqual(len(list((destination/"gltf").glob("*.gltf"))), 2)
        self.assertEqual(published["autonomous_animation"]["uri"], "baked-animation.json")
        self.assertTrue(path.with_name("baked-animation.bin").exists())

    def test_limits_are_enforced_and_unreachable_goal_is_reported(self):
        _, out, m = self.bake(scene().replace("HLimits -90 90", "HLimits 0 0").replace("HLimits 0 170", "HLimits 0 0"))
        _, frames, _ = self.poses(out, m)
        for f in frames:
            for i in (1, 2):
                self.assertAlmostEqual(abs(f[i][6]), 1, places=6)
        self.assertGreater(m["autonomous_animation"]["maximum_goal_position_error"], .5)

    def test_fk_animation_is_sampled_without_ik(self):
        _, out, m = self.bake(scene(ik=False))
        self.assertEqual(m["autonomous_animation"]["goals"], 0)
        _, frames, _ = self.poses(out, m)
        for frame in range(3):
            self.assertAlmostEqual(frames[frame][1][4], math.sin((.3 + frame * .1)/2), places=6)

    def test_concatenated_constraint_recovery_is_narrow(self):
        _, out, m = self.bake(scene("HJointStiffness 50PController 3\nPLimits 0 0\n"))
        self.assertEqual(self.poses(out, m)[0]["recovered_constraint_fields"], 1)
        for extra in ("HJointStiffness 50PController 3 garbage\n", "HJointStiffness -1\n", "HController 2\n", "GoalObject 99\n", "PivotRotation 10 0 0\n", "IKFKBlending 0.5\n", "Plugin ItemMotionHandler 1 Unknown\nEndPlugin\n"):
            with self.subTest(extra=extra):
                _, _, m = self.bake(scene(extra))
                self.assertEqual(m["autonomous_animation"]["status"], "unsupported")
                self.assertTrue(m["autonomous_animation"]["issue"])
                self.assertNotIn("animated", m["gltf_rigs"][0]["pose"])

    def test_playback_cap_does_not_allocate_unbounded_bake(self):
        _, _, m = self.bake(scene().replace("LastFrame 2", "LastFrame 100000"))
        self.assertEqual(m["autonomous_animation"]["status"], "unsupported")
        self.assertIsNone(m["autonomous_animation"]["uri"])


if __name__ == "__main__":
    unittest.main()
