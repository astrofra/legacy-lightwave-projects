"""End-to-end binary, preservation, path and coordinate regression tests."""
import hashlib
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

EXE = str(Path(sys.argv.pop(1)).resolve())
U16 = lambda n: struct.pack(">H", n)
F32 = lambda *v: struct.pack(">" + "f" * len(v), *v)


def s0(value):
    data = value.encode("utf-8") if isinstance(value, str) else value
    data += b"\0"
    return data + b"\0" * (len(data) % 2)


def chunk(tag, data, short=False):
    return tag.encode() + struct.pack(">H" if short else ">I", len(data)) + data + b"\0" * (len(data) % 2)


def form(kind, *chunks):
    return chunk("FORM", kind.encode() + b"".join(chunks))


def vx(n):
    return U16(n) if n < 0xFF00 else struct.pack(">I", n | 0xFF000000)


def lwob(extra=b"", poly=None):
    if poly is None:
        poly = U16(3) + U16(0) + U16(1) + U16(2) + U16(1)
    return form("LWOB", chunk("PNTS", F32(0, 0, 1, 1, 0, 1, 0, 1, 1)), chunk("SRFS", s0(b"caf\xe9")), chunk("POLS", poly), chunk("SURF", s0(b"caf\xe9") + chunk("COLR", b"\xff\x80\x00\0", True)), extra)


def layer(n, offset=0):
    return chunk("LAYR", U16(n) + U16(0) + F32(0, 0, 0) + s0(f"layer{n}")) + chunk("PNTS", F32(offset, 0, 1, offset+1, 0, 1, offset, 1, 1)) + chunk("POLS", b"FACE" + U16(3) + vx(0) + vx(1) + vx(2))


def motion1(values, end=1):
    keys = [(frame, row, linear) for frame, row, linear in values]
    return "ObjectMotion\n9\n" + str(len(keys)) + "\n" + "".join(" ".join(map(str, row)) + f"\n{frame} {linear} 0 0 0\n" for frame, row, linear in keys) + f"EndBehavior {end}\n"


def motion3(channel=0, shape=3, declared=2, modifier=""):
    return f"ObjectMotion\nNumChannels 1\nChannel {channel}\n{{ Envelope\n{declared}\nKey 0 0 {shape} 0 0 0 0 0 0\nKey 10 1 {shape} 0 0 0 0 0 0\nBehaviors 1 1\n{modifier}}}\n"


class Converter(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lwconvert-")
        self.base = Path(self.temp.name)
        self.root = self.base / "content"
        self.root.mkdir()
        self.output_number = 0

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode() if isinstance(data, str) else data)
        return path

    def run_cli(self, *args, code=0):
        result = subprocess.run([EXE, *map(str, args)], capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result

    def convert(self, path, *args, code=0):
        self.output_number += 1
        out = self.base / f"out{self.output_number}"
        self.run_cli("convert", path, "--content-root", self.root, "--output", out, *args, code=code)
        return out, json.loads((out / "manifest.json").read_text("utf-8"))

    def object_data(self, out, manifest):
        path = out / manifest["assets"][0]["uri"]
        return path.parent, json.loads(path.read_text("utf-8"))

    def test_lwob_binary_hash_encoding_winding(self):
        raw = lwob(chunk("XTRA", b"abc"))
        path = self.write("élément sans extension", raw)
        out, manifest = self.convert(path)
        directory, obj = self.object_data(out, manifest)
        self.assertEqual((directory / "source.bin").read_bytes(), raw)
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(obj["source"]["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(obj["tags"][0]["text"], "café")
        self.assertEqual(obj["tags"][0]["decoding"], "latin1-hypothesis")
        self.assertEqual(obj["buffer_bytes"], len((directory / "geometry.bin").read_bytes()))
        self.assertEqual(struct.unpack_from("<3f", (directory / "geometry.bin").read_bytes()), (0, 0, 1))
        text = (directory / "mesh.obj").read_text()
        self.assertIn("v 0 0 -1", text)
        self.assertIn("f 3 2 1", text)
        self.assertEqual(obj["chunks"][-1]["status"], "preserved-opaque")

    def test_lwo2_vmad_seam(self):
        maps = chunk("VMAP", b"TXUV" + U16(2) + s0("uv") + b"".join(vx(i)+F32(i/2, 0) for i in range(3)))
        maps += chunk("VMAD", b"TXUV" + U16(2) + s0("uv") + vx(0) + vx(0) + F32(.25, .75))
        path = self.write("uv.lwo", form("LWO2", layer(0), maps))
        out, manifest = self.convert(path, "--uv-map", "uv")
        directory, obj = self.object_data(out, manifest)
        self.assertEqual(len(obj["maps"]), 2)
        self.assertIn("vt 0.25 0.75", (directory / "mesh.obj").read_text())
        self.assertIn("f 3/3 2/2 1/1", (directory / "mesh.obj").read_text())
        out, manifest = self.convert(path, "--uv-map", "absent", code=2)
        self.assertEqual(manifest["unmapped_uv_corners"], 3)

    def test_nonfinite_map_preserved_and_reported(self):
        m = chunk("VMAP", b"WGHT" + U16(1) + s0("weight") + vx(1) + F32(float("inf")))
        out, manifest = self.convert(self.write("weight.lwo", form("LWO2", layer(0), m)), code=2)
        directory, obj = self.object_data(out, manifest)
        self.assertEqual(obj["non_finite_map_values"], 1)
        offset = obj["maps"][0]["values"]["offset"]
        self.assertTrue(math.isinf(struct.unpack_from("<f", (directory / "geometry.bin").read_bytes(), offset)[0]))

    def test_vx_24bit_and_loose_points(self):
        raw = form("LWO2", chunk("PNTS", F32(0, 0, 0) * 65281), chunk("POLS", b"FACE" + U16(1) + vx(65280)))
        out, manifest = self.convert(self.write("big-index", raw))
        directory, obj = self.object_data(out, manifest)
        self.assertEqual(obj["positions"]["count"], 65281)
        self.assertIn("p 65281", (directory / "mesh.obj").read_text())

    def test_detail_polygons_preserved_not_flattened(self):
        record = U16(3)+U16(0)+U16(1)+U16(2)
        raw = lwob(poly=record+U16(65535)+U16(1)+record+U16(1))
        path = self.write("detail", raw)
        summary = json.loads(self.run_cli("inspect", path).stdout)
        self.assertEqual(summary["detail_polygons"], 1)
        out, manifest = self.convert(path, code=2)
        self.assertEqual(manifest["skipped_obj_primitives"], 2)

    def test_layers_selected_by_id_not_chunk_order(self):
        self.write("layers.lwo", form("LWO2", layer(3, 100), layer(0, 10)))
        scene = self.write("layers.lws", "LWSC\n3\nLoadObjectLayer 1 layers.lwo\n")
        out, _ = self.convert(scene)
        self.assertIn("v 10 0 -1", (out / "scene.obj").read_text())
        self.assertNotIn("v 100 ", (out / "scene.obj").read_text())
        out, manifest = self.convert(self.write("missing.lws", "LWSC\n3\nLoadObjectLayer 2 layers.lwo\n"), code=2)
        self.assertEqual(manifest["unresolved_object_instances"], 1)
        self.assertEqual(json.loads((out / "scene/scene.json").read_text())["nodes"][0]["resolution"], "missing-layer")

    def test_linear_scene_parent_pivot_and_default_frame(self):
        self.write("tri", lwob())
        scene = "LWSC\n1\nFirstFrame 5\nFramesPerSecond 25\nAddNullObject parent\n"
        scene += motion1([(0, [10,0,0,0,0,0,1,1,1], 1)])
        scene += "LoadObject tri\nParentObject 1\nPivotPoint 1 0 0\n"
        scene += motion1([(0, [0,0,0,0,0,0,1,1,1], 1), (10, [10,0,0,0,0,0,1,1,1], 1)])
        out, manifest = self.convert(self.write("move.lws", scene))
        self.assertEqual(manifest["frame"], 5)
        self.assertIn("v 14 0 -1", (out / "scene.obj").read_text())
        data = json.loads((out / "scene/scene.json").read_text())
        self.assertEqual(data["animation_bytes"], len((out / "scene/animation.bin").read_bytes()))

    def test_heading_and_negative_scale(self):
        self.write("tri", lwob())
        scene = "LWSC\n1\nLoadObject tri\n" + motion1([(0, [0,0,0,90,0,0,-1,1,1], 1)])
        out, _ = self.convert(self.write("rotation.lws", scene))
        lines = (out / "scene.obj").read_text().splitlines()
        points = [list(map(float, line.split()[1:])) for line in lines if line.startswith("v ")]
        self.assertAlmostEqual(points[0][0], 1)
        self.assertAlmostEqual(points[1][2], -1)
        self.assertIn("f 1 2 3", lines)

    def test_v3_seconds_and_unsupported_spline(self):
        self.write("tri", lwob())
        for shape, code in [(3, 0), (0, 2)]:
            scene = self.write(f"motion{shape}.lws", "LWSC\n3\nFramesPerSecond 20\nLoadObject tri\n" + motion3(shape=shape))
            out, manifest = self.convert(scene, "--frame", "10", code=code)
            if not code:
                self.assertIn("v 5 0 -1", (out / "scene.obj").read_text())
            else:
                self.assertIsNone(manifest["scene_obj"])
                self.assertIn("animation", manifest["scene_obj_issue"])
                self.assertFalse((out / "scene.obj").exists())

    def test_envelope_modifiers_and_declared_count(self):
        self.write("tri", lwob())
        scene = self.write("channel.lws", "LWSC\n3\nLoadObject tri\n" + motion3(declared=0, modifier='{ ChannelHandler\n"Expression"\n}\n'))
        out, manifest = self.convert(scene, code=2)
        channel = json.loads((out / "scene/scene.json").read_text())["nodes"][0]["channels"][0]
        self.assertEqual((channel["declared_keys"], channel["keys"]["count"], channel["opaque_modifiers"]), (0, 2, 1))
        self.assertIsNone(manifest["scene_obj"])

    def test_path_ambiguity_and_explicit_mapping(self):
        self.write("a/shared", lwob())
        b = self.write("b/shared", lwob())
        scene = self.write("ambiguous.lws", "LWSC\n1\nLoadObject old:shared\n")
        out, manifest = self.convert(scene, code=2)
        node = json.loads((out / "scene/scene.json").read_text())["nodes"][0]
        self.assertEqual(node["resolution"], "ambiguous")
        self.assertEqual(len(node["candidates"]), 2)
        out, manifest = self.convert(scene, "--map", "old:=" + str(b.parent))
        self.assertEqual(manifest["unresolved_object_instances"], 0)

    def test_plugin_blocks_cannot_inject_scene_nodes(self):
        path = self.write("plugin.lws", "LWSC\n3\nAddNullObject n\nPlugin ItemMotionHandler 1 test\nLoadObject fake\n{ nested\n}\nEndPlugin\n")
        summary = json.loads(self.run_cli("inspect", path).stdout)
        self.assertEqual((summary["object_loads"], summary["nodes"], summary["plugins"]), (0, 1, 1))

    def test_surface_preset(self):
        raw = form("PST_", chunk("NAME", b"preset"), chunk("PDAT", form("LWO2", chunk("SURF", s0("mat")+s0("")))))
        out, manifest = self.convert(self.write("preset.srf", raw))
        directory, obj = self.object_data(out, manifest)
        self.assertEqual(obj["positions"]["count"], 0)
        self.assertEqual((directory / "source.bin").read_bytes(), raw)

    def test_malformed_inputs_fail_with_offset(self):
        cases = [b"", lwob()[:-1], form("LWO2", chunk("PNTS", b"123")), form("LWO2", chunk("PNTS", F32(float("nan"),0,0))), lwob(poly=U16(1)+U16(99)+U16(1)), form("LWOB", chunk("SRFS", b"unterminated")), b"LWSC\n3\nPlugin x\n", b"LWSC\n1\nLoadObject tri\nObjectMotion\n9\n1\n"]
        for i, raw in enumerate(cases):
            with self.subTest(i=i):
                self.assertIn("at byte", self.run_cli("inspect", self.write(str(i), raw), code=1).stderr)

    def test_no_overwrite_or_output_inside_sources(self):
        path = self.write("tri", lwob())
        out, _ = self.convert(path)
        self.run_cli("convert", path, "--output", out, code=1)
        self.run_cli("convert", path, "--output", self.root / "forbidden", code=1)
        self.assertFalse((self.root / "forbidden").exists())
        self.assertEqual(path.read_bytes(), lwob())

    def test_parent_cycle_is_reported(self):
        self.write("tri", lwob())
        scene = self.write("cycle.lws", "LWSC\n1\nLoadObject tri\nParentObject 1\n")
        _, manifest = self.convert(scene, code=2)
        self.assertIn("cyclic", manifest["scene_obj_issue"])

    def test_patch_curve_and_bone_dispositions(self):
        points = chunk("PNTS", F32(0,0,0, 1,0,0, 0,1,0))
        record = U16(3)+vx(0)+vx(1)+vx(2)
        raw = form("LWO2", points, *(chunk("POLS", tag+record) for tag in (b"PTCH", b"CURV", b"BONE")))
        _, manifest = self.convert(self.write("controls", raw), code=2)
        self.assertEqual((manifest["exported_patch_cages"], manifest["exported_curve_control_polylines"], manifest["skipped_obj_primitives"]), (1,1,1))

    def test_ptag_material_and_invalid_map_reference(self):
        raw = form("LWO2", chunk("TAGS", s0("green")), layer(0), chunk("PTAG", b"SURF"+vx(0)+U16(0)), chunk("SURF", s0("green")+s0("")+chunk("COLR", F32(0,1,0)+vx(0), True)), chunk("VMAP", b"TXUV"+U16(2)+s0("uv")+vx(20)+F32(0,0)))
        out, manifest = self.convert(self.write("tagged", raw), code=2)
        directory, obj = self.object_data(out, manifest)
        self.assertEqual(obj["invalid_map_references"], 1)
        self.assertIn("usemtl a0_m0", (directory / "mesh.obj").read_text())
        self.assertIn("Kd 0 1 0", (directory / "materials.mtl").read_text())

    def test_repeat_and_stepped_interpolation(self):
        self.write("tri", lwob())
        scene = self.write("repeat.lws", "LWSC\n1\nLoadObject tri\n" + motion1([(2, [0,0,0,0,0,0,1,1,1], 1), (12, [10,0,0,0,0,0,1,1,1], 1)], end=2))
        out, _ = self.convert(scene, "--frame", "17")
        self.assertIn("v 5 0 -1", (out / "scene.obj").read_text())
        scene = self.write("step.lws", "LWSC\n3\nLoadObject tri\nFramesPerSecond 30\n" + motion3(shape=4))
        out, _ = self.convert(scene, "--frame", "15")
        self.assertIn("v 0 0 -1", (out / "scene.obj").read_text())

    def polygon_fixture(self, points, indices, maps=b""):
        return form("LWO2", chunk("PNTS", F32(*(v for point in points for v in point))), chunk("POLS", b"FACE"+U16(len(indices))+b"".join(vx(i) for i in indices)), maps)

    def check_polygon_triangles(self, out, manifest, points, boundary, axes=(0,1)):
        directory, obj = self.object_data(out, manifest)
        # The native boundary is independent of the triangulated derivative.
        self.assertEqual(obj["primitives"]["count"], 1)
        self.assertEqual(obj["indices"]["count"], len(boundary))
        polygons = [line.split()[1:] for line in (directory/"mesh.obj").read_text().splitlines() if line.startswith("f ")]
        projected = [(p[axes[0]], p[axes[1]]) for p in points]
        area = lambda a,b,c: (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
        expected = sum(projected[a][0]*projected[b][1]-projected[b][0]*projected[a][1] for a,b in zip(boundary,boundary[1:]+boundary[:1]))
        total = 0
        for polygon in polygons:
            self.assertEqual(len(polygon), 3)
            a,b,c = [projected[int(corner.split('/')[0])-1] for corner in polygon]
            signed = area(a,b,c)
            # OBJ reverses the source order after reflecting Z.
            self.assertLess(signed*expected, 0)
            total += abs(signed)
            for weights in ((1/3,1/3,1/3),(.6,.2,.2),(.2,.6,.2),(.2,.2,.6)):
                sample = tuple(sum(weights[j]*p[k] for j,p in enumerate((a,b,c))) for k in range(2))
                winding = 0
                for i,j in zip(boundary,boundary[1:]+boundary[:1]):
                    start,end = projected[i],projected[j]
                    cross = area(start,end,sample)
                    if start[1] <= sample[1] < end[1] and cross > 0: winding += 1
                    if end[1] <= sample[1] < start[1] and cross < 0: winding -= 1
                self.assertNotEqual(winding, 0, "triangle samples must stay outside holes and concave cutouts")
        self.assertAlmostEqual(total/abs(expected), 1, places=6)
        return polygons

    def test_concave_ngon_and_quad(self):
        for points in ([(0,0,0),(4,0,0),(4,4,0),(3,4,0),(3,1,0),(1,1,0),(1,4,0),(0,4,0)], [(0,0,0),(4,0,0),(1,1,0),(0,4,0)]):
            boundary = list(range(len(points)))
            for order in (boundary, boundary[::-1]):
                out, manifest = self.convert(self.write("concave", self.polygon_fixture(points,order)))
                triangles = self.check_polygon_triangles(out,manifest,points,order)
                self.assertEqual(len(triangles), len(points)-2)

    def test_bridged_hole_preserves_opening_and_uv_corners(self):
        points = [(0,0,0),(4,0,0),(4,4,0),(0,4,0),(1,1,0),(1,3,0),(3,3,0),(3,1,0)]
        boundary = [0,4,5,6,7,4,0,1,2,3]
        maps = chunk("VMAP", b"TXUV"+U16(2)+s0("uv")+b"".join(vx(i)+F32(i,2*i) for i in range(8)))
        maps += chunk("VMAD", b"TXUV"+U16(2)+s0("uv")+vx(4)+vx(0)+F32(40,80))
        out, manifest = self.convert(self.write("hole",self.polygon_fixture(points,boundary,maps)),"--uv-map","uv")
        polygons = self.check_polygon_triangles(out,manifest,points,boundary)
        self.assertEqual(len(polygons), 8)
        self.assertEqual(manifest["obj_bridged_hole_faces"], 1)
        directory,_ = self.object_data(out,manifest)
        uv = [tuple(map(float,line.split()[1:])) for line in (directory/"mesh.obj").read_text().splitlines() if line.startswith("vt ")]
        for polygon in polygons:
            for corner in polygon:
                vertex,texture = map(int,corner.split('/'))
                value = 40 if vertex == 5 else vertex-1
                self.assertEqual(uv[texture-1], (value,2*value))

    def test_multiple_bridged_holes(self):
        points = [(0,0,0),(10,0,0),(10,10,0),(0,10,0),(1,1,0),(1,3,0),(3,3,0),(3,1,0),(7,7,0),(7,9,0),(9,9,0),(9,7,0)]
        boundary = [0,4,5,6,7,4,0,1,2,10,11,8,9,10,2,3]
        out, manifest = self.convert(self.write("two-holes",self.polygon_fixture(points,boundary)))
        self.assertEqual(len(self.check_polygon_triangles(out,manifest,points,boundary)), 14)

    def test_projection_scale_and_explicit_closing_corner(self):
        for scale,offset in ((1e-8,0),(16,1e6)):
            points = [(7*scale,offset+x*scale,offset+y*scale) for x,y in ((0,0),(4,0),(4,4),(1,1),(0,4))]
            boundary = [0,1,2,3,4,0]
            out, manifest = self.convert(self.write("closed",self.polygon_fixture(points,boundary)),code=2)
            self.assertEqual(manifest["obj_removed_duplicate_corners"], 1)
            self.assertEqual(len(self.check_polygon_triangles(out,manifest,points,boundary,axes=(1,2))), 3)

    def test_invalid_contours_are_reported_without_partial_triangles(self):
        for points in ([(0,0,0),(3,3,0),(0,3,0),(3,0,0),(4,1,0)],[(0,0,0),(0,0,0),(0,1,0),(0,1,0)]):
            out, manifest = self.convert(self.write("invalid-contour",self.polygon_fixture(points,list(range(len(points))))),code=2)
            self.assertEqual(manifest["obj_triangulation_failures"], 1)
            directory,_ = self.object_data(out,manifest)
            self.assertFalse(any(line.startswith('f ') for line in (directory/'mesh.obj').read_text().splitlines()))

    def test_nonplanar_face_is_an_explicit_approximation(self):
        points = [(0,0,0),(1,0,0),(1,1,.2),(0,1,0)]
        out, manifest = self.convert(self.write("nonplanar",self.polygon_fixture(points,[0,1,2,3])),code=2)
        self.assertEqual(manifest["obj_nonplanar_faces"], 1)
        self.assertEqual(manifest["obj_triangles"], 2)


if __name__ == "__main__":
    unittest.main()
