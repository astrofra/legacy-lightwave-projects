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
from test_gltf import animation_pose, load, node_matrix, source_map, world_matrices

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
        self.assertEqual(data["extras"]["profile"], "autonomous-hpb-ik-0.2")
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

    def test_pivot_fk_and_combined_rest_match_native_lw6_and_lw96(self):
        from probe_pivot_rotation import scene_bytes
        from probe_skinning_oracle import mesh_bytes
        oracle = json.loads(Path(__file__).with_name('fixtures').joinpath('pivot_rotation_oracle.json').read_text())
        config = [r['case'] for r in oracle['hosts'][0]['observations']]
        points = [[0,0,0], [1,0,0], [0,1,0], [0,0,1]]
        self.write('probe.lwo', mesh_bytes(points, [('weight', [(i,1) for i in range(4)])]))
        source = self.write('probe.lws', scene_bytes(config))
        out, m = self.convert(source, code=2)
        meta, frames, _ = self.poses(out, m)
        for host in oracle['hosts']:
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), host['source_sha256'])
            for owner, record in enumerate(host['observations']):
                item = (0x40000000 if record['case'].get('bone') else 0x10000000)+owner
                for frame in (0,1):
                    with self.subTest(host=host['runtime'], case=record['case']['name'], frame=frame):
                        v = frames[frame][meta['node_ids'].index(item)][10:]
                        matrix = node_matrix(dict(translation=v[:3], rotation=v[3:7], scale=v[7:]))
                        self.assertLess(max(abs(a-b) for a,b in zip(matrix,record['world_matrices'][frame])), 1e-6)
                if not record['case'].get('bone'): continue
                rig = next(r for r in m['gltf_rigs'] if r['owner_item']==0x10000000+owner)
                self.assertTrue(rig['animated'])
                data, buffers = load(out/rig['gltf'])
                binds = skin.accessor(data, buffers, data['skins'][0]['inverseBindMatrices'])
                for frame in (0,1):
                    world = world_matrices(animation_pose(data, buffers, frame))
                    for primitive in data['meshes'][0]['primitives']:
                        attrs = primitive['attributes']
                        positions = skin.accessor(data, buffers, attrs['POSITION'])
                        joints = skin.accessor(data, buffers, attrs['JOINTS_0'])
                        weights = skin.accessor(data, buffers, attrs['WEIGHTS_0'])
                        for row, (_,_,point) in enumerate(source_map(primitive, buffers)):
                            actual = [0.,0.,0.]
                            for joint,weight in zip(joints[row],weights[row]):
                                p = skin.transform(skin.multiply(world[data['skins'][0]['joints'][joint]],binds[joint]),positions[row])
                                for axis in range(3): actual[axis] += weight*p[axis]
                            native = record['points'][frame][point]['world']
                            self.assertLess(math.dist(actual,(native[0],native[1],-native[2])),1e-6)
        # The ordinary scene/OBJ evaluator uses the same qualified composition
        # even when autonomous baking is disabled.
        rigid = [c for c in config if not c.get('bone')]
        ordinary = self.write('ordinary.lws', scene_bytes(rigid))
        off, info = self.convert(ordinary, '--bake-ik', 'off')
        data, buffers = load(off/info['scene_gltf'])
        for frame in (0,1):
            world = world_matrices(animation_pose(data,buffers,frame))
            for index,node in enumerate(data['nodes']):
                item = node['extras']['source_node_id']-0x10000000
                expected = oracle['hosts'][0]['observations'][item]['world_matrices'][frame]
                expected = [v*(-1 if (i%4==2)!=(i//4==2) else 1) for i,v in enumerate(expected)]
                self.assertLess(max(abs(a-b) for a,b in zip(world[index],expected)),2e-6)

    def test_ik_axes_follow_oriented_pivot(self):
        text = scene('PivotRotation 0 90 0\n')
        text = text.replace(motion('Object', **{'0':[(0,1),(2,.5)], '2':[(0,1),(2,1.2)]}),
                            motion('Object', **{'0':[(0,1),(2,.5)], '1':[(0,-1),(2,-1.2)]}))
        _, out, m = self.bake(text)
        meta, frames, _ = self.poses(out,m)
        for poses in frames:
            end = poses[meta['node_ids'].index(0x10000001)][10:13]
            goal = poses[meta['node_ids'].index(0x10000002)][10:13]
            self.assertLess(math.dist(end,goal),2e-5)
            self.assertLess(end[1],-.9)
            self.assertAlmostEqual(end[2],0,places=5)

    def test_goal_limit_applies_per_independent_chain_group(self):
        def many(shared):
            text = 'LWSC\n3\nFirstFrame 0\nLastFrame 1\nFramesPerSecond 25\n'
            for i in range(33):
                text += 'AddNullObject anchor\n'+motion('Object')+'IKAnchor 1\n'
                text += 'AddNullObject effector\n'+motion('Object')+f'ParentItem {0x10000000+(0 if shared else i*3):08x}\n'
                text += f'GoalObject {i*3+3}\nFullTimeIK 1\n'
                text += 'AddNullObject target\n'+motion('Object')
            return text
        _,out,m = self.bake(many(False))
        self.assertEqual(self.poses(out,m)[0]['goals'],33)
        _,_,m = self.bake(many(True))
        self.assertEqual(m['autonomous_animation']['status'],'unsupported')
        self.assertIn('one chain group',m['autonomous_animation']['issue'])

    def test_disabled_fulltime_ik_does_not_freeze_source_rotation(self):
        _, out, m = self.bake(scene().replace("FullTimeIK 1", "FullTimeIK 0"))
        self.assertEqual(m["autonomous_animation"]["goals"], 0)
        _, frames, _ = self.poses(out, m)
        self.assertAlmostEqual(frames[-1][1][4], math.sin(.5/2), places=6)

    def test_concatenated_constraint_recovery_is_narrow(self):
        _, out, m = self.bake(scene("HJointStiffness 50PController 3\nPLimits 0 0\n"))
        self.assertEqual(self.poses(out, m)[0]["recovered_constraint_fields"], 1)
        for extra in ("HJointStiffness 50PController 3 garbage\n", "HJointStiffness -1\n", "HController 2\n", "GoalObject 99\n", "IKFKBlending 0.5\n", "Plugin ItemMotionHandler 1 Unknown\nEndPlugin\n"):
            with self.subTest(extra=extra):
                _, _, m = self.bake(scene(extra))
                self.assertEqual(m["autonomous_animation"]["status"], "unsupported")
                self.assertTrue(m["autonomous_animation"]["issue"])
                self.assertNotIn("animated", m["gltf_rigs"][0]["pose"])

    def test_playback_cap_does_not_allocate_unbounded_bake(self):
        _, _, m = self.bake(scene().replace("LastFrame 2", "LastFrame 100000"))
        self.assertEqual(m["autonomous_animation"]["status"], "unsupported")
        self.assertIsNone(m["autonomous_animation"]["uri"])

    def test_rigid_ik_uses_the_same_bake_without_bones(self):
        text = "LWSC\n3\nFirstFrame 0\nLastFrame 2\nFramesPerSecond 1\n"
        text += "AddNullObject pivot\n" + motion("Object") + "HController 3\n"
        text += "LoadObject rig.lwo\nParentItem 10000000\n" + motion("Object", **{"2": [(0, 1)]})
        text += "GoalObject 3\nFullTimeIK 1\nGoalStrength 1\n"
        text += "AddNullObject goal\n" + motion("Object", **{"0": [(0, 0), (2, 1)], "2": [(0, 1), (2, 0)]})
        _, out, m = self.bake(text)
        self.assertTrue(m["autonomous_animation"]["scene_exported"])
        meta, frames, _ = self.poses(out, m)
        self.assertLess(math.dist(frames[-1][1][10:13], (1, 0, 0)), 2e-5)
        data, _ = load(out/m["scene_gltf"])
        self.assertNotIn("skins", data)
        self.assertTrue(data["animations"])


if __name__ == "__main__":
    unittest.main()
