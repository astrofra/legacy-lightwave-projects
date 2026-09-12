"""Verify native shading preservation and actual OBJ/glTF corner normals."""
import json
import math
import struct
import unittest

import test_converter as fixtures
from test_converter import F32, U16, chunk, form, motion1, s0, vx
from test_gltf import load, source_map, values


def surface(name, angle=None, source="", legacy=False, flags=None):
    body = s0(name) + (b"" if legacy else s0(source))
    if flags is not None:
        body += chunk("FLAG", U16(flags), True)
    if angle is not None:
        body += chunk("SMAN", F32(angle), True)
    return chunk("SURF", body)


def wedge(angle=2., groups=None, maps=b"", surfaces=None, legacy=False, flags=None, disconnected=False):
    points = [(0,0,0), (1,0,0), (0,1,0), (0,0,4)]
    polys = [[0,1,2], [0,3,1]]
    if disconnected:
        points += [points[0], points[3], points[1]]
        polys[1] = [4,5,6]
    tags = ["mat", "other", "groupA", "groupB"]
    raw = chunk("PNTS", F32(*(v for p in points for v in p)))
    raw += chunk("SRFS" if legacy else "TAGS", b"".join(s0(t) for t in tags))
    raw += chunk("POLS", (b"" if legacy else b"FACE") + b"".join(U16(len(p)) + b"".join(vx(i) for i in p) + (U16(1) if legacy else b"") for p in polys))
    if not legacy:
        raw += chunk("PTAG", b"SURF" + vx(0)+U16(0) + vx(1)+U16(1 if surfaces else 0))
        if groups is not None:
            raw += chunk("PTAG", b"SMGP" + b"".join(vx(i)+U16(tag) for i,tag in enumerate(groups)))
    raw += maps
    raw += b"".join(surfaces) if surfaces else surface("mat", angle, legacy=legacy, flags=flags)
    return form("LWOB" if legacy else "LWO2", raw)


def normal_map(name="normals", discontinuous=False, entries=None):
    if entries is None:
        entries = [(i, None, (2.,0.,0.)) for i in range(4)]
    return chunk("VMAD" if discontinuous else "VMAP", b"NORM"+U16(3)+s0(name)+b"".join(
        vx(point)+(vx(polygon) if discontinuous else b"")+F32(*normal)
        for point,polygon,normal in entries))


class NormalTests(unittest.TestCase):
    setUp = fixtures.Converter.setUp
    tearDown = fixtures.Converter.tearDown
    write = fixtures.Converter.write
    run_cli = fixtures.Converter.run_cli
    convert = fixtures.Converter.convert
    object_data = fixtures.Converter.object_data

    def export(self, data, code=0):
        source = self.write("wedge.lwo", data)
        out, manifest = self.convert(source, code=code)
        directory, native = self.object_data(out, manifest)
        self.assertEqual((directory/"source.bin").read_bytes(), data)
        gltf, buffers = load(out/manifest["assets"][0]["gltf"])
        corners = {}
        for primitive in gltf["meshes"][0]["primitives"]:
            if "NORMAL" not in primitive["attributes"]: continue
            for key, normal in zip(source_map(primitive,buffers), values(gltf,buffers,primitive["attributes"]["NORMAL"])):
                corners[key[:2]] = normal
                self.assertAlmostEqual(sum(v*v for v in normal), 1., places=6)
        text = (out/manifest["assets"][0]["obj"]).read_text()
        normals = [tuple(map(float,line.split()[1:])) for line in text.splitlines() if line.startswith("vn ")]
        polygon = None
        for line in text.splitlines():
            if line.startswith("# source_primitive "): polygon = int(line.split()[-1])
            if line.startswith("f "):
                for token in line.split()[1:]:
                    fields = token.split("/")
                    self.assertEqual(len(fields),3)
                    self.assertGreater(int(fields[2]),0)
                    self.assertAlmostEqual(sum(v*v for v in normals[int(fields[2])-1]),1.,places=6)
                # Wedge faces are triangles, with reversed winding after Z reflection.
                for corner,token in zip((2,1,0),line.split()[1:]):
                    self.vector(normals[int(token.split("/")[2])-1],corners[polygon,corner])
        return out,manifest,native,corners,text

    def vector(self, actual, expected):
        for a,b in zip(actual,expected): self.assertAlmostEqual(a,b,places=6)

    def test_angle_threshold_and_equal_polygon_weight(self):
        for angle,expected in [(0.,(0,0,-1)),(1.5,(0,0,-1)),(2.,(0,2**-.5,-2**-.5))]:
            with self.subTest(angle=angle):
                _,_,native,normals,_ = self.export(wedge(angle))
                self.vector(normals[0,0],expected)
                self.vector(normals[0,2],(0,0,-1))
                self.assertEqual(native["materials"][0]["smoothing"]["angle_unit"],"radians")
                self.assertEqual(native["materials"][0]["smoothing"]["enabled"],angle>0)

    def test_groups_and_material_boundary(self):
        _,_,native,normals,text = self.export(wedge(groups=[2,3]))
        self.vector(normals[0,0],(0,0,-1)); self.vector(normals[1,0],(0,1,0))
        groups = [a for a in native["tag_assignments"] if a["type_name"]=="SMGP"]
        self.assertEqual([a["tag"] for a in groups],[2,3])
        self.assertIn("s 4\n",text); self.assertIn("s 5\n",text)
        _,_,_,normals,_ = self.export(wedge(groups=[2,2]))
        self.vector(normals[0,0],(0,2**-.5,-2**-.5))
        # Both surfaces must enable smoothing. Each side then uses its own
        # angle: the native 9.6 oracle confirms this asymmetric case.
        _,_,_,normals,_ = self.export(wedge(surfaces=[surface("mat",2),surface("other",1.5)]))
        self.vector(normals[0,0],(0,2**-.5,-2**-.5)); self.vector(normals[1,0],(0,1,0))
        _,_,_,normals,_ = self.export(wedge(surfaces=[surface("mat",2),surface("other",0)]))
        self.vector(normals[0,0],(0,0,-1)); self.vector(normals[1,0],(0,1,0))

    def test_legacy_flag_default_and_explicit_disable(self):
        for angle,expected in [(None,1.56207),(0.,0.),(2.,2.)]:
            _,_,native,_,_ = self.export(wedge(angle,legacy=True,flags=4))
            smoothing = native["materials"][0]["smoothing"]
            self.assertAlmostEqual(smoothing["effective_angle"],expected,places=6)
            self.assertEqual(smoothing["angle_present"],angle is not None)
            self.assertEqual(native["materials"][0]["flags"],4)

    def test_surface_inheritance_and_missing_source(self):
        _,_,native,normals,_ = self.export(wedge(surfaces=[surface("mat",2),surface("other",source="mat")]),code=2)
        self.assertEqual(native["materials"][1]["smoothing_angle"],0)
        self.assertEqual(native["materials"][1]["smoothing"]["origin"],"inherited-SMAN")
        self.vector(normals[1,0],(0,2**-.5,-2**-.5))
        _,manifest,native,_,_ = self.export(wedge(surfaces=[surface("mat",source="absent"),surface("other",0)]),code=2)
        self.assertGreater(manifest["gltf_normal_issues"],0)
        self.assertTrue(native["materials"][0]["smoothing"]["inheritance_issue"])

    def test_norm_vmad_precedence_and_original_magnitude(self):
        maps = normal_map()+normal_map(discontinuous=True,entries=[(0,1,(0,0,4))])
        out,manifest,native,normals,_ = self.export(wedge(maps=maps))
        self.vector(normals[0,0],(1,0,0)); self.vector(normals[1,0],(0,0,-1))
        self.vector(normals[1,1],(1,0,0))
        directory,_ = self.object_data(out,manifest); raw = (directory/"geometry.bin").read_bytes()
        self.assertEqual([m["type"] for m in native["maps"]],["NORM","NORM"])
        self.assertEqual(struct.unpack_from("<3f",raw,native["maps"][0]["values"]["offset"]),(2,0,0))
        self.assertEqual(struct.unpack_from("<3f",raw,native["maps"][1]["values"]["offset"]),(0,0,4))

    def test_invalid_or_ambiguous_normals_reported_with_generated_fallback(self):
        cases=[normal_map()+normal_map("other"),normal_map()+normal_map(discontinuous=True,entries=[(0,0,(0,0,0))]),normal_map(entries=[(0,None,(float("nan"),0,1))])]
        for maps in cases:
            _,manifest,native,normals,_ = self.export(wedge(maps=maps),code=2)
            self.vector(normals[0,0],(0,2**-.5,-2**-.5))
            self.assertGreater(native["shading"]["issues"],0)
            self.assertGreater(manifest["obj_normal_issues"],0)

    def test_disconnected_equal_positions_are_not_welded(self):
        _,_,_,normals,_ = self.export(wedge(disconnected=True))
        self.vector(normals[0,0],(0,0,-1)); self.vector(normals[1,0],(0,1,0))

    def test_normal_maps_are_scoped_to_native_point_blocks(self):
        raw=chunk("TAGS",s0("mat"))
        for layer,n in enumerate(((1,0,0),(0,1,0))):
            raw+=chunk("LAYR",U16(layer)+U16(0)+F32(0,0,0)+s0(f"layer{layer}"))
            raw+=chunk("PNTS",F32(0,0,0,1,0,0,0,1,0))
            raw+=chunk("POLS",b"FACE"+U16(3)+vx(0)+vx(1)+vx(2))
            raw+=chunk("PTAG",b"SURF"+vx(0)+U16(0))
            raw+=normal_map(f"normal{layer}",entries=[(p,None,n) for p in range(3)])
        _,_,native,normals,_=self.export(form("LWO2",raw+surface("mat",2)))
        self.assertEqual(native["shading"]["explicit_corners"],6)
        self.vector(normals[0,0],(1,0,0)); self.vector(normals[1,0],(0,1,0))

    def test_uv_seam_does_not_split_generated_smoothing(self):
        uv=chunk("VMAP",b"TXUV"+U16(2)+s0("uv")+b"".join(vx(i)+F32(0,0) for i in range(4)))
        uv+=chunk("VMAD",b"TXUV"+U16(2)+s0("uv")+vx(0)+vx(1)+F32(.8,.4))
        out,manifest=self.convert(self.write("seam.lwo",wedge(maps=uv)),"--uv-map","uv")
        data,buffers=load(out/manifest["assets"][0]["gltf"]); actual={}
        for p in data["meshes"][0]["primitives"]:
            for key,n,t in zip(source_map(p,buffers),values(data,buffers,p["attributes"]["NORMAL"]),values(data,buffers,p["attributes"]["TEXCOORD_0"])):
                actual[key[:2]]=(n,t)
        self.vector(actual[0,0][0],actual[1,0][0])
        self.vector(actual[0,0][0],(0,2**-.5,-2**-.5))
        self.assertNotEqual(actual[0,0][1],actual[1,0][1])

    def test_scene_obj_normal_inverse_transpose_and_mirrored_winding(self):
        self.write("wedge.lwo",wedge())
        scene = "LWSC\n1\nLoadObject wedge.lwo\n"+motion1([(0,[0,0,0,0,0,0,2,3,-4],1)])
        out,manifest = self.convert(self.write("wedge.lws",scene))
        text = (out/manifest["scene_obj"]).read_text()
        normals = [tuple(map(float,l.split()[1:])) for l in text.splitlines() if l.startswith("vn ")]
        self.vector(normals[0],(0,.8,.6))
        first = next(l for l in text.splitlines() if l.startswith("f "))
        self.assertEqual([int(t.split('/')[0]) for t in first.split()[1:]],[1,2,3])
        scene = "LWSC\n1\nLoadObject wedge.lwo\n"+motion1([(0,[0,0,0,0,0,0,2,3,0],1)])
        out,manifest = self.convert(self.write("singular.lws",scene),code=2)
        self.assertFalse(manifest["scene_obj"])
        self.assertIn("singular normal transform",manifest["scene_obj_issue"])
        self.assertIn("vn ",(out/manifest["assets"][0]["obj"]).read_text())


if __name__ == "__main__":
    unittest.main()
