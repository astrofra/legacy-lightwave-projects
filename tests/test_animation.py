"""Native capture integrity, cage identity, glTF animation and batch publication."""
import copy
import json
import math
from pathlib import Path
import struct
import sys
import unittest

import test_converter as fixtures
import test_skin as skin
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from lightwave_animation import digest, decompose, export_rig, multiply, read_capture, validate_mesh, write_json
from export_lightwave_animation import prepare_scene
from output_layout import ProjectOutput


def matrix(x=0,y=0,z=0,angle=0,scale=1):
    c,s = math.cos(angle),math.sin(angle)
    return [scale*c,scale*s,0,0,-s,c,0,0,0,0,1,0,x,y,z,1]


def captures():
    result = []
    for sample in range(3):
        root = matrix(2,3,4,scale=-2)
        items = {0x10000000:{"parent":0,"matrix":root}}
        for i in range(7):
            parent = 0x40000000 if i==1 else 0x10000000
            local = matrix(y=.1*i,angle=math.radians(170+sample*10) if i==2 else 0)
            items[0x40000000|(i<<16)] = {"parent":parent,"matrix":multiply(items[parent]["matrix"],local)}
        points = []
        for i,base in enumerate(((0,0,1),(1,0,1),(1,1,1),(0,1,1))):
            p = list(base)
            if i==0: p[2] += sample*.25
            world = [2-2*p[0],3+p[1],4+p[2]]
            points.append({"id":100+i,"base":list(base),"world":world})
        result.append({"frame":11+sample,"time":(11+sample)/30,"items":items,"meshes":{0x10000000:{"declared_points":4,"declared_polygons":2,"points":points,"polygons":[[100,101,102],[100,102,103]]}}})
    return result


def capture_text(frame):
    rows = [f"LWCONVERT_CAPTURE 1 {frame['frame']} {frame['time']} {frame['time']}"]
    for item,values in frame["items"].items():
        m = values["matrix"]; values12 = [m[4*c+r] for c in range(4) for r in range(3)]
        rows.append(f"I {item:x} {values['parent']:x} "+" ".join(map(str,values12)))
    for item,m in frame["meshes"].items():
        rows.append(f"M {item:x} {len(m['points'])} {len(m['polygons'])} 0 0")
        for p in m["points"]: rows.append(f"P {p['id']:x} "+" ".join(map(str,p["base"]+p["world"])))
        for p in m["polygons"]: rows.append(f"Q 1178682181 {len(p)} "+" ".join(f"{i:x}" for i in p))
    rows.append(f"END {len(frame['items'])} 1 4 2")
    return "\n".join(rows)+"\n"


def values(data, raw, index):
    a = data["accessors"][index]; v = data["bufferViews"][a["bufferView"]]
    width = {"SCALAR":1,"VEC3":3,"VEC4":4}[a["type"]]
    return [struct.unpack_from("<"+"f"*width,raw,v.get("byteOffset",0)+a.get("byteOffset",0)+i*v.get("byteStride",4*width)) for i in range(a["count"])]


class AnimationTests(unittest.TestCase):
    setUp = fixtures.Converter.setUp
    tearDown = fixtures.Converter.tearDown
    write = fixtures.Converter.write
    run_cli = fixtures.Converter.run_cli
    convert = fixtures.Converter.convert

    def package(self):
        self.write("rig.lwo",skin.object_bytes())
        return self.convert(self.write("rig.lws",skin.scene_bytes()),"--uv-map","uv",code=2)

    def test_complete_capture_and_rejection_of_truncated_or_invalid_data(self):
        text = capture_text(captures()[0]); path = self.write("capture.txt",text)
        self.assertEqual(read_capture(path),captures()[0])
        for bad in (text.rsplit("END",1)[0],text.replace("END 8 1 4 2","END 7 1 4 2"),text.replace("M 10000000 4 2 0 0","M 10000000 4 2 2 2"),text.replace("P 64 0 0 1","P 64 nan 0 1")):
            path.write_text(bad)
            with self.assertRaises(ValueError): read_capture(path)

    def test_morph_animation_preserves_points_seams_and_native_time_origin(self):
        package,manifest = self.package(); rig = manifest["gltf_rigs"][0]
        before = digest(package/rig["gltf"])
        entry = export_rig(package,manifest,rig,captures(),"a"*64)
        self.assertEqual(digest(package/rig["gltf"]),before)
        data = json.loads((package/entry["gltf"]).read_text()); raw = (package/entry["gltf_bin"]).read_bytes()
        self.assertNotIn("skins",data)
        animation = data["animations"][0]
        self.assertEqual(animation["extras"]["source_first_frame"],11)
        self.assertAlmostEqual(entry["duration_seconds"],2/30)
        weights = next(c for c in animation["channels"] if c["target"]["path"]=="weights")
        sampler = animation["samplers"][weights["sampler"]]
        self.assertEqual(values(data,raw,sampler["output"]),[(0,),(0,),(1,),(0,),(0,),(1,)])
        self.assertEqual(values(data,raw,sampler["input"])[0],(0,))
        for primitive in data["meshes"][0]["primitives"]:
            a = primitive["attributes"]
            self.assertFalse(any(k.startswith(("WEIGHTS_","JOINTS_")) for k in a))
            positions = values(data,raw,a["POSITION"])
            mapping = primitive["extras"]["source_map"]
            for i,target in enumerate(primitive["targets"]):
                deltas = values(data,raw,target["POSITION"])
                for row,delta in enumerate(deltas):
                    point = struct.unpack_from("<III",raw,mapping["byteOffset"]+12*row)[2]
                    self.assertEqual(delta,(0,0,-.25*(i+1)) if point==0 else (0,0,0))
                    self.assertAlmostEqual(positions[row][2]+delta[2],-1-(.25*(i+1) if point==0 else 0))
                self.assertEqual(data["bufferViews"][data["accessors"][target["POSITION"]]["bufferView"]]["target"],34962)
            # First triangle tips towards +X when native point zero moves +Z.
            base_normals = values(data,raw,a["NORMAL"])
            delta_normals = values(data,raw,primitive["targets"][0]["NORMAL"])
            first = [a+b for a,b in zip(base_normals[0],delta_normals[0])]
            for actual,expected in zip(first,(.25/math.sqrt(1.0625),0,-1/math.sqrt(1.0625))): self.assertAlmostEqual(actual,expected,places=6)

    def test_bone_tracks_have_trs_and_continuous_unit_quaternions(self):
        package,manifest = self.package()
        entry = export_rig(package,manifest,manifest["gltf_rigs"][0],captures(),"b"*64)
        data = json.loads((package/entry["gltf"]).read_text()); raw = (package/entry["gltf_bin"]).read_bytes()
        self.assertEqual(entry["animated_bones"],1)
        self.assertEqual(data["nodes"][0]["translation"],[2,3,-4])
        self.assertEqual(data["nodes"][0]["scale"],[-2,1,1])
        for channel in data["animations"][0]["channels"]:
            if channel["target"]["path"]=="weights": continue
            self.assertNotIn("matrix",data["nodes"][channel["target"]["node"]])
            sampler = data["animations"][0]["samplers"][channel["sampler"]]
            rows = values(data,raw,sampler["output"])
            if channel["target"]["path"]=="rotation":
                for row in rows: self.assertAlmostEqual(sum(v*v for v in row),1,places=6)
                for a,b in zip(rows,rows[1:]): self.assertGreater(sum(x*y for x,y in zip(a,b)),0)

    def test_native_cage_validation_rejects_reordering_and_connectivity_changes(self):
        package,manifest = self.package(); path = package/manifest["assets"][0]["uri"]
        native = json.loads(path.read_text()); geometry = (path.parent/"geometry.bin").read_bytes()
        original = captures()[0]["meshes"][0x10000000]
        self.assertEqual(len(validate_mesh(original,native,geometry,None)),4)
        reordered = copy.deepcopy(original); reordered["points"][0],reordered["points"][1] = reordered["points"][1],reordered["points"][0]
        changed = copy.deepcopy(original); changed["polygons"][0] = [100,101,103]
        for bad in (reordered,changed):
            with self.assertRaises(ValueError): validate_mesh(bad,native,geometry,None)

    def test_singular_or_sheared_matrices_are_not_silently_approximated(self):
        shear = matrix(); shear[4] = .25
        for bad in (matrix(scale=0),shear):
            with self.assertRaises(ValueError): decompose(bad)

    def test_frame_time_and_parent_inconsistencies_are_rejected(self):
        for change in ("time","parent"):
            package,manifest = self.package(); frames = captures()
            if change=="time": frames[1]["time"] = frames[0]["time"]
            else: frames[1]["items"][0x40010000]["parent"] = 0x10000000
            with self.assertRaises(ValueError): export_rig(package,manifest,manifest["gltf_rigs"][0],frames,"c"*64)

    def test_native_scene_preparation_retains_deformation_and_isolates_inputs(self):
        package,manifest = self.package(); scene_path = package/manifest["scene"]
        scene = json.loads(scene_path.read_text()); source = (scene_path.parent/"source.bin").read_bytes()
        source += b'\nPlugin MasterHandler 1 ProxyPick\n0\nEndPlugin\nPlugin DisplacementHandler 1 LW_MorphMixer\n5\nEndPlugin\n'
        original = digest(self.root/"rig.lwo")
        assets = [{**a,"source_copy":(package/a["uri"]).parent/"source.bin"} for a in manifest["assets"]]
        directory = self.base/"evaluation"; directory.mkdir(); plugin = self.write("capture.p",b"test plugin")
        working,config,audit = prepare_scene(source,scene,assets,directory,self.base,plugin)
        text = working.read_text()
        self.assertIn("LW_MorphMixer",text); self.assertNotIn("ProxyPick",text)
        self.assertIn("LoadObject inputs/10000000/rig.lwo\nSubPatchLevel 0 0",text)
        self.assertEqual(digest(directory/"inputs/10000000/rig.lwo"),original)
        self.assertEqual(digest(self.root/"rig.lwo"),original)
        self.assertTrue((config/"LWEXT9-64.CFG").is_file())
        self.assertEqual(audit["removed_display_plugins"],[{"class":"MasterHandler","name":"ProxyPick"}])
        bad = self.base/"bad-evaluation"; bad.mkdir()
        with self.assertRaisesRegex(ValueError,"not qualified"):
            prepare_scene(source+b'Plugin MasterHandler 2 UnknownDeformer\nEndPlugin\n',scene,assets,bad,self.base,plugin)

    def test_batch_publication_keeps_capture_hashes_and_animation_links(self):
        package,manifest = self.package(); scene_path = package/manifest["scene"]
        cache = scene_path.parent/"evaluated-animation"; cache.mkdir(); (cache/"frames").mkdir()
        path = cache/"frames/frame-000011.txt"; path.write_text(capture_text(captures()[0]))
        write_json(cache/"capture.json",{"frames":[{"uri":"frames/"+path.name,"sha256":digest(path)}]})
        provenance = digest(cache/"capture.json")
        manifest["evaluated_animation"] = {"uri":(cache/"capture.json").relative_to(package).as_posix(),"sha256":provenance}
        manifest["gltf_animations"] = [export_rig(package,manifest,manifest["gltf_rigs"][0],captures(),provenance)]
        publisher = ProjectOutput(self.base/"published",[self.root/"rig.lwo",self.root/"rig.lws"])
        published = publisher.publish(package,manifest)
        result = json.loads(published.read_text()); reference = result["gltf_animations"][0]
        self.assertTrue((published.parent/reference["gltf"]).is_file())
        self.assertTrue((published.parent/reference["gltf_bin"]).is_file())
        capture = published.parent/result["evaluated_animation"]["uri"]
        self.assertEqual(digest(capture),provenance)
        self.assertEqual(digest(capture.parent/"frames/frame-000011.txt"),digest(path))


if __name__=="__main__": unittest.main()
