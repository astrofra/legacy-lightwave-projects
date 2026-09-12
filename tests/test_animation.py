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
from export_lightwave_animation import prepare_scene, finalize_rig_outputs
from output_layout import ProjectOutput
from lightwave_scene import export_scene, validate_rigid_scene
from lightwave_skin_animation import export_skinned_rig


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
    version = 2 if any("corner_normals" in m for m in frame["meshes"].values()) else 1
    rows = [f"LWCONVERT_CAPTURE {version} {frame['frame']} {frame['time']} {frame['time']}"]
    for item,values in frame["items"].items():
        m = values["matrix"]; values12 = [m[4*c+r] for c in range(4) for r in range(3)]
        rows.append(f"I {item:x} {values['parent']:x} "+" ".join(map(str,values12)))
    for item,m in frame["meshes"].items():
        rows.append(f"M {item:x} {len(m['points'])} {len(m['polygons'])} 0 0")
        for p in m["points"]: rows.append(f"P {p['id']:x} "+" ".join(map(str,p["base"]+p["world"])))
        for polygon,p in enumerate(m["polygons"]):
            rows.append(f"Q 1178682181 {len(p)} "+" ".join(f"{i:x}" for i in p))
            for corner,point in enumerate(p):
                if (polygon,corner) in m.get("corner_normals",{}):
                    rows.append(f"V {polygon} {corner} {point:x} "+" ".join(map(str,m["corner_normals"][polygon,corner])))
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

    def test_bound_skin_animation_preserves_weights_and_applies_joint_motion(self):
        self.write("rig.lwo",skin.object_bytes(skin.weight_map("weight0",[(0,1),(1,1),(2,1)])))
        package,manifest=self.convert(self.write("rig.lws",skin.scene_bytes(skin.bone(position="1 0 0",rotation="0 0 90"))),"--uv-map","uv",code=2)
        frames=[]; bases=[[0,0,1],[1,0,1],[1,1,1],[0,1,1]]
        for sample in range(2):
            root=matrix(2+sample,3,4)
            local=bases if not sample else [[1,-1,1],[1,0,1],[0,0,1],[0,1,1]]
            points=[{"id":100+i,"base":base,"world":[p[0]+2+sample,p[1]+3,p[2]+4]} for i,(base,p) in enumerate(zip(bases,local))]
            frames.append({"frame":11+sample,"time":(11+sample)/30,"items":{0x10000000:{"parent":0,"matrix":root},
                0x40000000:{"parent":0x10000000,"matrix":multiply(root,matrix(1,angle=math.pi/2+sample*math.pi/2))}},
                "meshes":{0x10000000:{"points":points,"polygons":[[100,101,102],[100,102,103]]}}})
        rig=manifest["gltf_rigs"][0];rest=json.loads((package/rig["gltf"]).read_text())
        entry=export_skinned_rig(package,manifest,rig,frames,"d"*64)
        data=json.loads((package/entry["gltf"]).read_text());raw=(package/entry["gltf_bin"]).read_bytes()
        self.assertEqual(data["skins"],rest["skins"])
        self.assertEqual(data["meshes"],rest["meshes"])
        self.assertEqual(entry["morph_targets"],0);self.assertEqual(entry["animated_bones"],1)
        self.assertLess(entry["native_deformation_comparison"]["maximum_vertex_error"],1e-6)
        from procedural_skin_checks import skin_rows,world_matrices,transform
        binds,rows=skin_rows(package/entry["gltf"])
        animation=data["animations"][0]
        for sample,frame in enumerate(frames):
            for channel in animation["channels"]:
                sampler=animation["samplers"][channel["sampler"]]
                data["nodes"][channel["target"]["node"]][channel["target"]["path"]]=values(data,raw,sampler["output"])[sample]
            world=world_matrices(data);matrices=[multiply(world[j],b) for j,b in zip(data["skins"][0]["joints"],binds)]
            for point,position,influences in rows:
                actual=[sum(weight*transform(matrices[joint],position)[k] for joint,weight in influences) for k in range(3)]
                expected=frame["meshes"][0x10000000]["points"][point]["world"][:];expected[2]*=-1
                self.assertLess(math.dist(actual,expected),1e-6)

    def test_protocol3_requires_after_ik_bones_and_composes_their_actual_pose(self):
        text=capture_text(captures()[0]).replace("LWCONVERT_CAPTURE 1 ","LWCONVERT_CAPTURE 3 ")
        with self.assertRaisesRegex(ValueError,"after-IK"): read_capture(self.write("post-ik.txt",text))
        tracks="".join(f"T {item:x} 0 0 0 0 0 0 1 1 1 0 0 0\n" for item in captures()[0]["items"] if item>>28==4)
        tracks=tracks.replace("T 40000000 0 0 0", "T 40000000 1 0 0")
        complete=text.replace("END ",tracks+"END ")
        result=read_capture(self.write("post-ik-complete.txt",complete))
        self.assertEqual(result["items"][0x40000000]["matrix"][12:15],[0,3,4])
        self.assertIn("sdk_matrix",result["items"][0x40000000])
        with self.assertRaisesRegex(ValueError,"identity"):
            read_capture(self.write("post-ik-duplicate.txt",complete.replace("END ",tracks+"END ")))

    def rigid_package(self):
        self.write("tri.lwo",fixtures.lwob())
        source="LWSC\n3\nFramesPerSecond 30\nLoadObject tri.lwo\nPController 3\nParentItem 10000002\nLoadObject tri.lwo\nParentItem 10000002\nAddNullObject root\n"
        package,manifest=self.convert(self.write("rigid.lws",source),code=2)
        scene_path=package/manifest["scene"]; scene=json.loads(scene_path.read_text())
        (scene_path.parent/"evaluated-animation").mkdir()
        frames=[]
        for sample in range(3):
            root=matrix(2+sample,3,4,scale=-2)
            items={0x10000002:{"parent":0,"matrix":root}}
            meshes={}
            for i in range(2):
                item=0x10000000+i
                world=multiply(root,matrix(y=1 if i==0 else -1,angle=math.radians(170+10*sample) if i==0 else 0))
                items[item]={"parent":0x10000002,"matrix":world}
                points=[]
                for point,base in enumerate(((0,0,1),(1,0,1),(0,1,1))):
                    position=[sum(world[4*k+r]*base[k] for k in range(3))+world[12+r] for r in range(3)]
                    points.append({"id":100+point,"base":list(base),"world":position})
                meshes[item]={"points":points,"polygons":[[100,101,102]]}
            frames.append({"frame":11+sample,"time":(11+sample)/30,"items":items,"meshes":meshes})
        return package,manifest,scene,frames

    def test_native_rigid_assembly_exports_instances_hierarchy_and_trs(self):
        package,manifest,scene,frames=self.rigid_package()
        entry=export_scene(package,manifest,scene,frames,"d"*64,fixtures.EXE)
        data=json.loads((package/entry["gltf"]).read_text()); raw=(package/entry["gltf_bin"]).read_bytes()
        self.assertEqual(entry["geometry_instances"],2); self.assertEqual(entry["meshes"],1)
        self.assertEqual(len(data["nodes"]),3); self.assertEqual(data["scenes"][0]["nodes"],[2])
        self.assertEqual(data["nodes"][2]["children"],[0,1])
        self.assertEqual(data["nodes"][0]["mesh"],data["nodes"][1]["mesh"])
        self.assertEqual(data["nodes"][2]["scale"],[-2,1,1])
        self.assertNotIn("skins",data); self.assertNotIn("targets",data["meshes"][0]["primitives"][0])
        self.assertEqual(data["extras"]["source_sha256"],scene["source"]["sha256"])
        self.assertEqual(data["nodes"][0]["extras"]["native_rig_parameters"][0]["value"]["text"],"3")
        clip=data["animations"][0]
        self.assertEqual(clip["extras"]["first_frame"],11)
        for channel in clip["channels"]:
            sampler=clip["samplers"][channel["sampler"]]
            self.assertEqual(values(data,raw,sampler["input"])[0],(0,))
            rows=values(data,raw,sampler["output"])
            if channel["target"]=={"node":2,"path":"translation"}:
                self.assertEqual(rows,[(2,3,-4),(3,3,-4),(4,3,-4)])
            if channel["target"]["path"]=="rotation":
                for a,b in zip(rows,rows[1:]): self.assertGreater(sum(x*y for x,y in zip(a,b)),0)
        self.assertFalse(((package/manifest["scene"]).parent/"evaluated-animation/assembly-package").exists())

    def test_native_rigid_validation_rejects_deformation_and_changed_parents(self):
        for change in ("deform","parent","topology","time"):
            package,manifest,scene,frames=self.rigid_package()
            if change=="deform": frames[1]["meshes"][0x10000000]["points"][0]["world"][0]+=.01
            elif change=="parent": frames[1]["items"][0x10000000]["parent"]=0
            elif change=="topology": frames[1]["meshes"][0x10000000]["polygons"][0]=[100,101,101]
            else: frames[1]["time"]=frames[0]["time"]
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_rigid_scene(package,manifest,scene,frames)

    def test_native_rigid_scene_publishes_as_the_main_scene_gltf(self):
        package,manifest,scene,frames=self.rigid_package()
        entry=export_scene(package,manifest,scene,frames,"e"*64,fixtures.EXE)
        manifest.update(scene_gltf=entry["gltf"],scene_gltf_bin=entry["gltf_bin"],scene_gltf_issue="",
                        gltf_evaluated_scene={k:v for k,v in entry.items() if k not in ("gltf","gltf_bin")})
        publisher=ProjectOutput(self.base/"published-rigid",[self.root/"tri.lwo",self.root/"rigid.lws"])
        path=publisher.publish(package,manifest); published=json.loads(path.read_text())
        self.assertEqual(Path(published["scene_gltf"]).name,"rigid.lws.gltf")
        self.assertTrue((path.parent/published["scene_gltf"]).is_file())
        self.assertTrue((path.parent/published["scene_gltf_bin"]).is_file())
        self.assertEqual(published["gltf_evaluated_scene"]["geometry_instances"],2)

    def test_complete_capture_and_rejection_of_truncated_or_invalid_data(self):
        text = capture_text(captures()[0]); path = self.write("capture.txt",text)
        self.assertEqual(read_capture(path),dict(captures()[0],protocol=1))
        for bad in (text.rsplit("END",1)[0],text.replace("END 8 1 4 2","END 7 1 4 2"),text.replace("M 10000000 4 2 0 0","M 10000000 4 2 2 2"),text.replace("P 64 0 0 1","P 64 nan 0 1")):
            path.write_text(bad)
            with self.assertRaises(ValueError): read_capture(path)

    def normal_captures(self):
        frames = captures()
        for sample,frame in enumerate(frames):
            mesh = frame["meshes"][0x10000000]; mesh["corner_normals"] = {}
            for polygon,boundary in enumerate(mesh["polygons"]):
                for corner,point in enumerate(boundary):
                    n = [sample*.25+(point-100)*.1,1.,1.]
                    length = math.sqrt(sum(v*v for v in n))
                    mesh["corner_normals"][polygon,corner] = [v/length for v in n]
        return frames

    def test_capture_v2_normal_identity_and_vectors_are_validated(self):
        frame = self.normal_captures()[0]; text = capture_text(frame)
        self.assertEqual(read_capture(self.write("normals.txt",text)),dict(frame,protocol=2))
        line = next(l for l in text.splitlines() if l.startswith("V "))
        for replacement in (line+"\n"+line,line.replace("V 0 0 64","V 0 0 65"),"V 0 0 64 nan 0 0","V 0 0 64 0 0 0"):
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                read_capture(self.write("bad-normals.txt",text.replace(line,replacement)))

    def test_evaluated_normals_survive_morphs_uv_seams_and_signed_scale(self):
        package,manifest = self.package(); frames = self.normal_captures()
        # Native enumeration and cyclic corner starts may differ from IR.
        for frame in frames:
            m=frame["meshes"][0x10000000]; m["polygons"]=[p[1:]+p[:1] for p in m["polygons"][::-1]]
            m["corner_normals"]={(1-p,(c+2)%3):v for (p,c),v in m["corner_normals"].items()}
        entry = export_rig(package,manifest,manifest["gltf_rigs"][0],frames,"e"*64)
        data=json.loads((package/entry["gltf"]).read_text()); raw=(package/entry["gltf_bin"]).read_bytes()
        self.assertEqual(data["extras"]["normal_profile"],"native-evaluated-corner-normals-0.1")
        for primitive in data["meshes"][0]["primitives"]:
            base=values(data,raw,primitive["attributes"]["NORMAL"]); mapping=primitive["extras"]["source_map"]
            for sample in range(3):
                delta=values(data,raw,primitive["targets"][sample-1]["NORMAL"]) if sample else [(0,0,0)]*len(base)
                for row,(n,d) in enumerate(zip(base,delta)):
                    point=struct.unpack_from("<III",raw,mapping["byteOffset"]+12*row)[2]
                    expected=[-2*(sample*.25+point*.1),1.,-1.]
                    length=math.sqrt(sum(v*v for v in expected))
                    for a,b in zip((a+b for a,b in zip(n,d)),expected): self.assertAlmostEqual(a,b/length,places=6)

    def test_missing_evaluated_normals_never_silently_flatten_smoothing(self):
        package,manifest = self.package(); rig=manifest["gltf_rigs"][0]
        incomplete=self.normal_captures(); incomplete[1]["meshes"][0x10000000]["corner_normals"].pop((0,0))
        with self.assertRaisesRegex(ValueError,"omitted an exported corner normal"):
            export_rig(package,manifest,rig,incomplete,"f"*64)
        path=package/manifest["assets"][0]["uri"]; native=json.loads(path.read_text())
        native["materials"]=[{"smoothing_angle":1.5}]; write_json(path,native)
        with self.assertRaisesRegex(ValueError,"recapture"):
            export_rig(package,manifest,rig,captures(),"f"*64)
        native["tag_assignments"]=[{"type":int.from_bytes(b"SMGP","big")}]; write_json(path,native)
        with self.assertRaisesRegex(ValueError,"NORM/SMGP shading is not qualified"):
            export_rig(package,manifest,rig,self.normal_captures(),"f"*64)

    def test_morph_animation_preserves_points_seams_and_native_time_origin(self):
        package,manifest = self.package(); rig = manifest["gltf_rigs"][0]
        rest=json.loads((package/rig["gltf"]).read_text())
        rest["extras"].update(skin_approximation=True,skin_limitations="rest only",volume_corrections_omitted=2)
        (package/rig["gltf"]).write_text(json.dumps(rest),encoding="utf-8")
        before = digest(package/rig["gltf"])
        entry = export_rig(package,manifest,rig,captures(),"a"*64)
        self.assertEqual(digest(package/rig["gltf"]),before)
        data = json.loads((package/entry["gltf"]).read_text()); raw = (package/entry["gltf_bin"]).read_bytes()
        self.assertNotIn("skins",data)
        for key in ("skin_approximation","skin_limitations","volume_corrections_omitted"):
            self.assertNotIn(key,data["extras"])
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

    def test_lightwave6_preparation_attaches_observers_after_motion_and_audits_omissions(self):
        from probe_skinning_oracle import scene_bytes
        self.write("rig.lwo",skin.object_bytes())
        source=scene_bytes([{}]).replace(b"probe-0.lwo",b"rig.lwo")
        source+=b"Plugin DisplacementHandler 1 JointMorph\nEndPlugin\nPlugin MasterHandler 1 .SpreadsheetStandardBanks\nEndPlugin\n"
        package,manifest=self.convert(self.write("rig.lws",source),code=2)
        scene=json.loads((package/manifest["scene"]).read_text())
        assets=[{**a,"source_copy":(package/a["uri"]).parent/"source.bin"} for a in manifest["assets"]]
        directory=self.base/"lw6-evaluation";directory.mkdir();plugin=self.write("capture.p",b"test plugin")
        working,config,audit=prepare_scene(source,scene,assets,directory,self.base,plugin,"lightwave6",["JointMorph"])
        text=working.read_text()
        self.assertEqual(config.name,"LW3.CFG")
        self.assertEqual(text.count("LWConvertMotionCapture"),3)
        self.assertIn("ParentItem 10000000\n\nPlugin ItemMotionHandler 1 LWConvertMotionCapture",text)
        self.assertNotIn("JointMorph",text);self.assertNotIn("SpreadsheetStandardBanks",text)
        self.assertEqual([p["name"] for p in audit["explicitly_skipped_plugins"]],["JointMorph"])
        self.assertIn("Plugin ItemMotionHandler LWConvertMotionCapture",config.read_text())
        self.assertEqual(audit["working_scene_sha256"],digest(working))

    def test_batch_publication_keeps_capture_hashes_and_animation_links(self):
        self.write("rig.lwo",skin.object_bytes())
        source = self.write("rig.lws",skin.scene_bytes().replace("BoneWeightMapOnly 1", "BoneWeightMapOnly 0"))
        package,manifest = self.convert(source,"--uv-map","uv","--gltf-rigs","all",code=2)
        scene_path = package/manifest["scene"]
        cache = scene_path.parent/"evaluated-animation"; cache.mkdir(); (cache/"frames").mkdir()
        path = cache/"frames/frame-000011.txt"; path.write_text(capture_text(captures()[0]))
        write_json(cache/"capture.json",{"frames":[{"uri":"frames/"+path.name,"sha256":digest(path)}]})
        provenance = digest(cache/"capture.json")
        manifest["evaluated_animation"] = {"uri":(cache/"capture.json").relative_to(package).as_posix(),"sha256":provenance}
        manifest["gltf_animations"] = [export_rig(package,manifest,manifest["gltf_rigs"][0],captures(),provenance)]
        manifest["gltf_files"] += 1
        direct_manifest = copy.deepcopy(manifest)
        collision = self.write("rig.lws.anim-10000000",skin.object_bytes())
        publisher = ProjectOutput(self.base/"published",[self.root/"rig.lwo",self.root/"rig.lws",collision],self.root.parent)
        published = publisher.publish(package,manifest)
        result = json.loads(published.read_text()); reference = result["gltf_animations"][0]
        self.assertTrue((published.parent/reference["gltf"]).is_file())
        self.assertTrue((published.parent/reference["gltf_bin"]).is_file())
        self.assertIsNone(result["gltf_rigs"][0]["gltf"])
        self.assertEqual(result["gltf_rigs"][0]["export_status"],"omitted-by-policy")
        self.assertEqual(list((publisher.directory/"gltf").rglob("*.rig-*")),[])
        animation = (published.parent/reference["gltf"]).resolve()
        self.assertEqual(animation.name,"rig.lws.anim-10000000-2.gltf")
        self.assertEqual(animation.parent.relative_to(publisher.directory/"gltf"),published.parent.parent.relative_to(publisher.directory/"IR"))
        data = json.loads(animation.read_text())
        self.assertTrue(data["animations"])
        self.assertEqual((animation.parent/data["buffers"][0]["uri"]).read_bytes(),(published.parent/reference["gltf_bin"]).read_bytes())
        capture = published.parent/result["evaluated_animation"]["uri"]
        self.assertEqual(digest(capture),provenance)
        self.assertEqual(digest(capture.parent/"frames/frame-000011.txt"),digest(path))

        # The direct native-animation CLI prunes its temporary rest copy too.
        # Final animation and original IR remain available after that cleanup.
        write_json(package/"manifest.json",direct_manifest)
        finalize_rig_outputs(package,"skins")
        compact = json.loads((package/"manifest.json").read_text())
        self.assertIsNone(compact["gltf_rigs"][0]["gltf"])
        self.assertEqual(compact["gltf_files"],3)
        self.assertEqual(list((package/"gltf").glob("*.rig-*")),[])
        self.assertTrue((package/compact["gltf_animations"][0]["gltf"]).is_file())
        self.assertEqual(digest(cache/"capture.json"),provenance)


if __name__=="__main__": unittest.main()
