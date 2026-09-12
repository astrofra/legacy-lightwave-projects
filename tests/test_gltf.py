"""Decode actual glTF buffers and compare geometry, provenance, UVs and poses."""
from collections import Counter
import copy
import json
import math
from pathlib import Path
import struct
import unittest
from urllib.parse import unquote

# Shared native fixtures and CLI helpers; this module consumes the binary argv.
import test_converter as fixtures
from test_converter import F32, U16, chunk, form, layer, lwob, motion1, s0, vx


def load(path):
    data = json.loads(path.read_text("utf-8"))
    buffers = [(path.parent / unquote(b["uri"])).read_bytes() for b in data.get("buffers", [])]
    assert data["asset"]["version"] == "2.0"
    for description, raw in zip(data.get("buffers", []), buffers):
        assert len(raw) == description["byteLength"]
    return data, buffers


def values(data, buffers, index):
    a = data["accessors"][index]
    view = data["bufferViews"][a["bufferView"]]
    components = {"SCALAR":1, "VEC2":2, "VEC3":3, "VEC4":4}[a["type"]]
    assert a["componentType"] == 5126 and a["count"] > 0
    start, stride = view.get("byteOffset", 0) + a.get("byteOffset", 0), view.get("byteStride", components * 4)
    assert start % 4 == 0 and stride % 4 == 0
    assert a.get("byteOffset", 0) + stride * (a["count"]-1) + 4*components <= view["byteLength"]
    rows = [struct.unpack_from("<"+"f"*components, buffers[view["buffer"]], start+i*stride) for i in range(a["count"])]
    assert all(math.isfinite(v) for row in rows for v in row)
    if "min" in a:
        for j in range(components):
            assert math.isclose(min(row[j] for row in rows), a["min"][j], rel_tol=1e-7, abs_tol=1e-35)
            assert math.isclose(max(row[j] for row in rows), a["max"][j], rel_tol=1e-7, abs_tol=1e-35)
    return rows


def source_map(primitive, buffers):
    mapping = primitive["extras"]["source_map"]
    return [struct.unpack_from("<III", buffers[mapping["buffer"]], mapping["byteOffset"]+12*i) for i in range(mapping["count"])]


def multiply(a, b):
    return [sum(a[4*k+row]*b[4*column+k] for k in range(4)) for column in range(4) for row in range(4)]


def node_matrix(node):
    if "matrix" in node:
        return node["matrix"]
    x,y,z,w = node.get("rotation", [0,0,0,1])
    sx,sy,sz = node.get("scale", [1,1,1])
    tx,ty,tz = node.get("translation", [0,0,0])
    return [(1-2*(y*y+z*z))*sx, 2*(x*y+z*w)*sx, 2*(x*z-y*w)*sx, 0,
            2*(x*y-z*w)*sy, (1-2*(x*x+z*z))*sy, 2*(y*z+x*w)*sy, 0,
            2*(x*z+y*w)*sz, 2*(y*z-x*w)*sz, (1-2*(x*x+y*y))*sz, 0,
            tx,ty,tz,1]


def world_matrices(data):
    matrices = {}
    identity = [1 if i % 5 == 0 else 0 for i in range(16)]
    def visit(i, parent):
        matrices[i] = multiply(parent, node_matrix(data["nodes"][i]))
        for child in data["nodes"][i].get("children", []):
            visit(child, matrices[i])
    for root in data["scenes"][data["scene"]].get("nodes", []):
        visit(root, identity)
    return matrices


def animation_pose(data, buffers, sample):
    pose = copy.deepcopy(data)
    for channel in data["animations"][0]["channels"]:
        sampler = data["animations"][0]["samplers"][channel["sampler"]]
        pose["nodes"][channel["target"]["node"]][channel["target"]["path"]] = values(data, buffers, sampler["output"])[sample]
    return pose


class GltfTests(unittest.TestCase):
    setUp = fixtures.Converter.setUp
    tearDown = fixtures.Converter.tearDown
    write = fixtures.Converter.write
    run_cli = fixtures.Converter.run_cli
    convert = fixtures.Converter.convert

    def exported(self, out, manifest, scene=False):
        return load(out / (manifest["scene_gltf"] if scene else manifest["assets"][0]["gltf"]))

    def test_uri_coordinates_normals_and_source_corners(self):
        out, manifest = self.convert(self.write("é ! %.lwo", lwob()))
        data, buffers = self.exported(out, manifest)
        self.assertEqual(data["buffers"][0]["uri"], "%C3%A9_%21_%25.lwo.bin")
        p = data["meshes"][0]["primitives"][0]
        self.assertEqual(values(data,buffers,p["attributes"]["POSITION"]), [(0,1,-1),(1,0,-1),(0,0,-1)])
        self.assertEqual(values(data,buffers,p["attributes"]["NORMAL"]), [(0,0,-1)]*3)
        self.assertEqual(source_map(p,buffers), [(0,2,2),(0,1,1),(0,0,0)])
        self.assertEqual(manifest["gltf_triangles"], 1)

    def test_hole_and_discontinuous_uvs(self):
        points = [(0,0,0),(4,0,0),(4,4,0),(0,4,0),(1,1,0),(1,3,0),(3,3,0),(3,1,0)]
        boundary = [0,4,5,6,7,4,0,1,2,3]
        maps = chunk("VMAP", b"TXUV"+U16(2)+s0("uv")+b"".join(vx(i)+F32(i/8, i/4) for i in range(8)))
        maps += chunk("VMAD", b"TXUV"+U16(2)+s0("uv")+vx(4)+vx(0)+F32(.125,.75))
        raw = form("LWO2",chunk("PNTS",F32(*(v for p in points for v in p))),chunk("POLS",b"FACE"+U16(len(boundary))+b"".join(vx(i) for i in boundary)),maps)
        out, manifest = self.convert(self.write("hole.lwo", raw), "--uv-map", "uv")
        data, buffers = self.exported(out,manifest)
        p = data["meshes"][0]["primitives"][0]
        positions = values(data,buffers,p["attributes"]["POSITION"])
        uv = values(data,buffers,p["attributes"]["TEXCOORD_0"])
        self.assertEqual(len(positions), 24)
        total = 0
        for i in range(0,24,3):
            a,b,c = positions[i:i+3]
            area = ((b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]))/2
            self.assertLess(area,0)
            total -= area
            x,y = sum(v[0] for v in (a,b,c))/3,sum(v[1] for v in (a,b,c))/3
            self.assertFalse(1<x<3 and 1<y<3)
        self.assertAlmostEqual(total,12)
        for i,(polygon,corner,point) in enumerate(source_map(p,buffers)):
            self.assertEqual(polygon,0)
            self.assertEqual(boundary[corner],point)
            self.assertEqual(positions[i],points[point])
            self.assertEqual(uv[i], (.125,.25) if point==4 else (point/8,1-point/4))

    def test_missing_uvs_create_a_separate_primitive(self):
        raw = form("LWO2",chunk("PNTS",F32(0,0,0, 1,0,0, 1,1,0, 0,1,0)),
                   chunk("POLS",b"FACE"+U16(3)+vx(0)+vx(1)+vx(2)+U16(3)+vx(0)+vx(2)+vx(3)),
                   chunk("VMAP",b"TXUV"+U16(2)+s0("uv")+b"".join(vx(i)+F32(i/2,0) for i in range(3))))
        out, manifest = self.convert(self.write("uv.lwo",raw),"--uv-map","uv",code=2)
        data, buffers = self.exported(out,manifest)
        primitives = data["meshes"][0]["primitives"]
        self.assertEqual(len(primitives),2)
        self.assertEqual(["TEXCOORD_0" in p["attributes"] for p in primitives],[True,False])
        self.assertEqual(manifest["gltf_unmapped_uv_corners"],1)
        for p in primitives:
            self.assertEqual(len(values(data,buffers,p["attributes"]["POSITION"])),3)

    def test_material_alpha_and_supported_sidedness(self):
        for side in (1,2,3):
            surface = chunk("SURF",s0("glass")+s0("")+chunk("COLR",F32(.25,.5,1),True)+chunk("TRAN",F32(.4),True)+chunk("SIDE",U16(side),True))
            raw = form("LWO2",layer(0),chunk("TAGS",s0("glass")),chunk("PTAG",b"SURF"+vx(0)+U16(0)),surface)
            out, manifest = self.convert(self.write("glass.lwo",raw),code=2 if side==2 else 0)
            data, buffers = self.exported(out,manifest)
            material = data["materials"][0]
            self.assertEqual(material["name"],"glass")
            self.assertEqual(material["alphaMode"],"BLEND")
            self.assertAlmostEqual(material["pbrMetallicRoughness"]["baseColorFactor"][3],.6)
            self.assertEqual(material["doubleSided"],side==3)
            p = data["meshes"][0]["primitives"][0]
            self.assertEqual(values(data,buffers,p["attributes"]["NORMAL"])[0],(0,0,-1))
            self.assertEqual(manifest["gltf_unsupported_sidedness"],1 if side==2 else 0)

    def test_hierarchy_instancing_pivots_and_negative_scale_match_obj(self):
        self.write("tri.lwo",lwob())
        scene = "LWSC\n1\nAddNullObject parent\n"+motion1([(1,[10,1,2,90,0,0,2,3,4],1)])
        scene += "LoadObject tri.lwo\nParentObject 1\nPivotPoint 1 0 0\n"+motion1([(1,[-3,0,1,0,0,20,-1,2,1],1)])
        scene += "LoadObject tri.lwo\nParentObject 1\n"+motion1([(1,[3,2,1,0,0,0,1,1,1],1)])
        out, manifest = self.convert(self.write("pose.lws",scene),"--frame","1")
        data, buffers = self.exported(out,manifest,scene=True)
        self.assertEqual(len(data["nodes"]),3)
        self.assertEqual(len(data["meshes"]),1)
        self.assertEqual(data["nodes"][0]["children"],[1,2])
        self.assertEqual(data["nodes"][1]["mesh"],data["nodes"][2]["mesh"])
        actual=[]
        for i,m in world_matrices(data).items():
            if "mesh" not in data["nodes"][i]: continue
            for p in data["meshes"][data["nodes"][i]["mesh"]]["primitives"]:
                for position in values(data,buffers,p["attributes"]["POSITION"]):
                    actual.append(tuple(sum(m[4*k+r]*position[k] for k in range(3))+m[12+r] for r in range(3)))
        expected=[tuple(map(float,line.split()[1:])) for line in (out/manifest["scene_obj"]).read_text().splitlines() if line.startswith("v ")]
        quantize=lambda rows:Counter(tuple(round(v,6) for v in row) for row in rows)
        self.assertEqual(quantize(actual),quantize(expected))

    def test_layer_selection_creates_distinct_meshes(self):
        self.write("layers.lwo",form("LWO2",layer(3,100),layer(0,10)))
        out, manifest = self.convert(self.write("layers.lws","LWSC\n3\nLoadObjectLayer 1 layers.lwo\nLoadObjectLayer 4 layers.lwo\n"))
        data, buffers = self.exported(out,manifest,scene=True)
        self.assertEqual(len(data["meshes"]),2)
        self.assertEqual([mesh["extras"]["source_layer_request"] for mesh in data["meshes"]],[1,4])
        for mesh,x in zip(data["meshes"],(10,100)):
            self.assertEqual(min(v[0] for v in values(data,buffers,mesh["primitives"][0]["attributes"]["POSITION"])),x)

    def test_points_lines_cages_and_empty_presets(self):
        raw=form("LWO2",chunk("PNTS",F32(0,0,0, 1,0,0, 1,1,0, 0,1,0, 3,0,0)),
                 chunk("POLS",b"FACE"+U16(1)+vx(0)+U16(2)+vx(0)+vx(1)),
                 chunk("POLS",b"PTCH"+U16(4)+vx(0)+vx(1)+vx(2)+vx(3)))
        out, manifest=self.convert(self.write("controls.lwo",raw),code=2)
        data,buffers=self.exported(out,manifest)
        self.assertEqual({p["mode"] for p in data["meshes"][0]["primitives"]},{0,1,4})
        self.assertEqual(manifest["gltf_points"],2)
        self.assertEqual(manifest["gltf_line_segments"],1)
        self.assertEqual(manifest["gltf_triangles"],2)
        self.assertEqual(manifest["gltf_patch_cages"],1)
        out,manifest=self.convert(self.write("empty.srf",form("PST_",chunk("PDAT",form("LWO2")))))
        data,buffers=self.exported(out,manifest)
        self.assertNotIn("buffers",data)
        self.assertNotIn("meshes",data)
        self.assertEqual((out/manifest["assets"][0]["gltf_bin"]).stat().st_size,0)

    def test_unsupported_scene_retains_individual_gltf(self):
        self.write("tri.lwo",lwob())
        out,manifest=self.convert(self.write("cycle.lws","LWSC\n1\nLoadObject tri.lwo\nParentObject 1\n"),code=2)
        self.assertIsNone(manifest["scene_gltf"])
        self.assertIn("cyclic",manifest["scene_gltf_issue"])
        data,buffers=self.exported(out,manifest)
        self.assertEqual(len(data["meshes"]),1)


    def test_scene_animation_parent_pivot_rotation_and_signed_scale_match_obj(self):
        self.write("tri.lwo", lwob())
        source = "LWSC\n1\nFirstFrame 0\nLastFrame 10\nFramesPerSecond 10\nAddNullObject parent\n"
        source += motion1([(0,[0,0,0,0,0,0,1,2,1],1), (10,[5,1,2,360,30,40,2,1,3],1)])
        source += "LoadObject tri.lwo\nParentObject 1\nPivotPoint 1 2 3\n"
        source += motion1([(0,[1,2,3,10,20,30,-1,1,1],1), (10,[3,4,5,40,50,60,1,2,1],1)])
        path = self.write("moving", source)
        out, manifest = self.convert(path)
        data, buffers = self.exported(out, manifest, scene=True)
        self.assertEqual(manifest["gltf_animation_channels"], 6)
        self.assertEqual(manifest["gltf_animation_samples"], 11)
        self.assertEqual(manifest["gltf_animated_channels_not_exported"], 0)
        self.assertEqual(manifest["gltf_animation_issue"], "")
        self.assertEqual(data["extras"]["profile"], "sampled-scene-transforms-0.1")
        self.assertEqual(len(data["animations"]), 1)
        for channel in data["animations"][0]["channels"]:
            sampler = data["animations"][0]["samplers"][channel["sampler"]]
            self.assertNotIn("matrix", data["nodes"][channel["target"]["node"]])
            times = values(data, buffers, sampler["input"])
            self.assertEqual((times[0], times[-1]), ((0,), (1,)))
            rows = values(data, buffers, sampler["output"])
            if channel["target"]["path"] == "rotation":
                for row in rows:
                    self.assertAlmostEqual(sum(v*v for v in row), 1, places=6)
                for a,b in zip(rows, rows[1:]):
                    self.assertGreaterEqual(sum(x*y for x,y in zip(a,b)), 0)
            if channel["target"] == {"node": 1, "path": "scale"}:
                self.assertEqual([rows[i][0] for i in (0,5,10)], [-1,0,1])
        for frame in (0,3,8,10):
            with self.subTest(frame=frame):
                pose = animation_pose(data, buffers, frame)
                actual = []
                for i,matrix in world_matrices(pose).items():
                    if "mesh" not in pose["nodes"][i]: continue
                    for primitive in data["meshes"][pose["nodes"][i]["mesh"]]["primitives"]:
                        for p in values(data, buffers, primitive["attributes"]["POSITION"]):
                            actual.append(tuple(sum(matrix[4*k+r]*p[k] for k in range(3))+matrix[12+r] for r in range(3)))
                snapshot, info = self.convert(path, "--frame", str(frame))
                expected = [tuple(map(float,line.split()[1:])) for line in (snapshot/info["scene_obj"]).read_text().splitlines() if line.startswith("v ")]
                for a,b in zip(sorted(actual), sorted(expected)):
                    self.assertLess(math.dist(a,b), 1e-5)
                self.assertEqual(len(actual),len(expected))
        self.assertEqual(path.read_text(), source)

    def test_v3_animation_seconds_tcb_and_playback_range(self):
        self.write("tri.lwo", lwob())
        source = ("LWSC\n3\nFirstFrame -12\nLastFrame 12\nFramesPerSecond 24\nLoadObject tri.lwo\n"
                  "ObjectMotion\nNumChannels 1\nChannel 0\n{ Envelope\n2\n"
                  "Key 0 -0.5 0 1 0 0 0 0 0\nKey 4 0.5 0 1 0 0 0 0 0\nBehaviors 1 1\n}\n")
        out, manifest = self.convert(self.write("tcb.lws", source))
        data, buffers = self.exported(out, manifest, scene=True)
        clip = data["animations"][0]
        self.assertEqual(clip["extras"]["samples"], 25)
        self.assertEqual(clip["extras"]["first_frame"], -12)
        sampler = clip["samplers"][0]
        times = values(data, buffers, sampler["input"])
        self.assertEqual((times[0][0],times[-1][0]), (0,1))
        translations = values(data, buffers, sampler["output"])
        for sample in (0,6,12,18,24):
            u=sample/24
            self.assertAlmostEqual(translations[sample][0], 4*(3*u*u-2*u*u*u), places=6)

    def test_unsupported_animation_retains_snapshot_and_reports_partial(self):
        self.write("tri.lwo",lwob())
        source = ("LWSC\n3\nFirstFrame 0\nLastFrame 2\nFramesPerSecond 1\nLoadObject tri.lwo\n"
                  "ObjectMotion\nNumChannels 1\nChannel 0\n{ Envelope\n2\n"
                  "Key 0 0 2 0 0 0 0 0 0\nKey 4 2 2 0 0 0 0 0 0\nBehaviors 1 1\n}\n")
        out, manifest = self.convert(self.write("bezier.lws", source), code=2)
        data, _ = self.exported(out, manifest, scene=True)
        self.assertNotIn("animations", data)
        self.assertEqual(manifest["gltf_animation_channels"], 0)
        self.assertIn("unsupported envelope shape",manifest["gltf_animation_issue"])
        self.assertTrue((out/manifest["scene_obj"]).is_file())

    def test_constant_motion_keys_do_not_invent_an_animation(self):
        self.write("tri.lwo",lwob())
        source = "LWSC\n1\nFirstFrame 0\nLastFrame 5\nLoadObject tri.lwo\n"
        source += motion1([(0,[1,2,3,0,0,0,1,1,1],1), (5,[1,2,3,0,0,0,1,1,1],1)])
        out, manifest = self.convert(self.write("still.lws",source))
        data, _ = self.exported(out,manifest,scene=True)
        self.assertNotIn("animations",data)
        self.assertEqual(manifest["gltf_animation_channels"],0)


if __name__ == "__main__":
    unittest.main()
