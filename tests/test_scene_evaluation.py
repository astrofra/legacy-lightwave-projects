"""TCB/repeat sampling and the bounded legacy mirrored-bank Follower profile."""
import json
import math
from pathlib import Path
import unittest

import test_converter as fixtures
import test_gltf as gltf


def motion(channel,keys,declared=None,behavior=1):
    lines = [f"ObjectMotion\nNumChannels 1\nChannel {channel}\n{{ Envelope",str(len(keys) if declared is None else declared)]
    for value,time,shape,*parameters in keys:
        p = (parameters+[0]*6)[:6]
        lines.append("Key "+" ".join(map(str,[value,time,shape,*p])))
    return "\n".join(lines)+f"\nBehaviors {behavior} {behavior}\n}}\n"


def follower(source=0x10000001):
    return ('Plugin ItemMotionHandler 1 LW_Follower\n'
            f'"{source}  1"\n'
            '"Channels 32  0 0 0  0 0 5  0 0 0"\n'
            '"TimeSlip 0.0000 Randomize 0.0000 PathSlip 0.0000 1"\n'+
            ''.join(f'"Scale {-1 if i==5 else 1}.0000 Add 0.0000"\n' for i in range(9))+
            'EndPlugin\n')


class SceneEvaluationTests(unittest.TestCase):
    setUp = fixtures.Converter.setUp
    tearDown = fixtures.Converter.tearDown
    write = fixtures.Converter.write
    run_cli = fixtures.Converter.run_cli
    convert = fixtures.Converter.convert

    def evaluate(self,keys,frame,behavior=1):
        self.write("tri.lwo",fixtures.lwob())
        scene = self.write("sample.lws","LWSC\n3\nFramesPerSecond 1\nLoadObject tri.lwo\n"+motion(0,keys,behavior=behavior))
        out,manifest = self.convert(scene,"--frame",str(frame))
        data,_ = gltf.load(out/manifest["scene_gltf"])
        matrix = data['nodes'][0].get('matrix',[1 if i%5==0 else 0 for i in range(16)])
        obj = (out/manifest["scene_obj"]).read_text().splitlines()
        x = float(next(line.split()[1] for line in obj if line.startswith("v ")))
        self.assertAlmostEqual(x,matrix[12],places=10)
        return x

    def test_tcb_tension_and_nonuniform_linear_path(self):
        # Zero endpoint velocity gives cubic smoothstep: 4*(3*.25^2-2*.25^3).
        self.assertAlmostEqual(self.evaluate([(0,0,0,1),(4,2,0,1)],.5),.625)
        for t in (-1,0,.25,1,2.5,3,4):
            with self.subTest(time=t):
                self.assertAlmostEqual(self.evaluate([(0,0,0),(1,1,0),(3,3,0)],t),max(0,min(t,3)))

    def test_tcb_continuity_bias_and_neighboring_keys(self):
        # Span [1,2]: value 1 -> 0, normalized tangents .7 and -1.
        self.assertAlmostEqual(self.evaluate([(0,0,0),(1,1,0,0,.2,.5),(0,2,0)],1.25),.9890625)
        # Equal span endpoint values do not imply a flat TCB curve.
        self.assertAlmostEqual(self.evaluate([(-1,0,0),(0,1,0),(0,2,0),(-1,3,0)],1.5),.125)

    def test_repeat_negative_time_range_and_single_key(self):
        keys = [(0,-2,0,1),(4,0,0,1)]
        self.assertAlmostEqual(self.evaluate(keys,.5,behavior=2),.625)
        self.assertAlmostEqual(self.evaluate(keys,-3.5,behavior=2),.625)
        for t in (-100,100): self.assertEqual(self.evaluate([(7,0,0)],t,behavior=2),7)

    def test_inconsistent_count_uses_parsed_keys_and_reports_preservation(self):
        self.write("tri.lwo",fixtures.lwob())
        raw = "LWSC\n3\nFramesPerSecond 1\nLoadObject tri.lwo\n"+motion(0,[(7,0,0)],declared=0,behavior=2)
        path=self.write("count.lws",raw)
        out,m=self.convert(path,"--frame","20",code=2)
        scene=json.loads((out/m["scene"]).read_text())
        self.assertEqual(m["scene_envelope_key_count_mismatches"],1)
        self.assertFalse(scene["nodes"][0]["unsupported_transform"])
        self.assertEqual(scene["nodes"][0]["channels"][0]["declared_keys"],0)
        self.assertEqual(scene["nodes"][0]["channels"][0]["keys"]["count"],1)
        self.assertEqual((out/m["scene"]).with_name("source.bin").read_bytes(),path.read_bytes())
        self.assertIn("v 7 0 -1",(out/m["scene_obj"]).read_text())

    def test_mirrored_wings_follow_source_and_preserve_plugin(self):
        self.write("tri.lwo",fixtures.lwob())
        plugin=follower()
        raw="LWSC\n3\nFramesPerSecond 1\nLoadObject tri.lwo\nParentItem 10000002\n"+plugin
        raw+="LoadObject tri.lwo\nParentItem 10000002\n"+motion(5,[(0,0,0),(math.pi/2,2,0)])
        raw+="AddNullObject parent\n"+motion(0,[(10,0,3)])
        path=self.write("wings.lws",raw)
        out,m=self.convert(path,"--frame","1",code=2)
        data,_=gltf.load(out/m["scene_gltf"])
        a,b=data["nodes"][0]["matrix"],data["nodes"][1]["matrix"]
        self.assertAlmostEqual(a[0],math.sqrt(.5)); self.assertAlmostEqual(a[1],-math.sqrt(.5))
        self.assertAlmostEqual(b[0],a[0]); self.assertAlmostEqual(b[1],-a[1])
        self.assertEqual(data["nodes"][2]["children"],[0,1])
        scene=json.loads((out/m["scene"]).read_text())
        self.assertEqual(scene["nodes"][0]["follower"]["source_item"],0x10000001)
        self.assertEqual(scene["plugins"][0]["status"],"mirrored-bank-preview")
        self.assertEqual(m["scene_follower_preview_profiles"],1)
        self.assertEqual(m["scene_plugins_not_evaluated"],0)
        source=path.read_bytes(); p=scene["plugins"][0]
        self.assertEqual(source[p["offset"]:p["offset"]+p["bytes"]].decode(),plugin.rstrip('\n'))

    def test_unsupported_follower_settings_do_not_silently_use_native_keys(self):
        self.write("tri.lwo",fixtures.lwob())
        for plugin in [follower().replace("Randomize 0.0000","Randomize 1.0000"),
                       follower().replace("Channels 32","Channels 33"),
                       follower().replace("Scale -1.0000","Scale 0.5000"),
                       follower()+"PluginEnabled 0\n"]:
            with self.subTest(plugin=plugin):
                scene=self.write("unsupported.lws","LWSC\n3\nLoadObject tri.lwo\n"+plugin+"AddNullObject leader\n")
                out,m=self.convert(scene,code=2)
                self.assertIsNone(m["scene_obj"]); self.assertIsNone(m["scene_gltf"])
                self.assertIn("Follower configuration",m["scene_obj_issue"])
                self.assertTrue((out/m["assets"][0]["gltf"]).exists())

    def test_follower_missing_source_cycles_and_unqualified_transforms(self):
        self.write("tri.lwo",fixtures.lwob())
        examples = [
            (follower(0x10000010),"", "missing source"),
            (follower(0x10000000),"", "cyclic"),
            (follower(),"AddNullObject leader\nParentItem 10000002\nAddNullObject parent\n","matching parents"),
            (follower(),"AddNullObject leader\n"+motion(3,[(.25,0,0)]),"bank-only"),
            (follower(),"AddNullObject leader\nPlugin ItemMotionHandler 1 Unsupported\nEndPlugin\n","unsupported item motion")]
        for plugin,tail,issue in examples:
            with self.subTest(issue=issue):
                scene=self.write("bad.lws","LWSC\n3\nLoadObject tri.lwo\n"+plugin+tail)
                _,m=self.convert(scene,code=2)
                self.assertIsNone(m["scene_obj"]); self.assertIn(issue,m["scene_obj_issue"])


if __name__ == "__main__": unittest.main()
