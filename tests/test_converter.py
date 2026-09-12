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
from urllib.parse import unquote

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

    def legacy_motion(self, kind, x):
        return motion1([(0,[x,0,0,0,0,0,1,1,1],1),(10,[x+10,0,0,0,0,0,1,1,1],1)]).replace("ObjectMotion",kind+"Motion (unnamed)")

    def test_legacy_implicit_camera_keeps_separate_light_and_object_keys(self):
        obj=self.write("Station1",lwob())
        for visibility in ("before","after","absent"):
            with self.subTest(visibility=visibility):
                raw="LWSC\n1\nFirstFrame 0\nLastFrame 10\n"
                if visibility=="before": raw+="ShowCamera 1\n"
                raw+="LoadObject HD1:atm/space/Station1\n"+self.legacy_motion("Object",10)
                raw+="AddLight\nLightName Sun\n"+self.legacy_motion("Light",20)
                raw+="AddLight\nLightName Flare\n"+self.legacy_motion("Light",30)+"ParentObject 1\n"
                raw+=self.legacy_motion("Camera",40)
                if visibility=="after": raw+="ShowCamera 1\n"
                path=self.write("StationChase",raw); out,m=self.convert(path)
                scene_path=out/m["scene"]; scene=json.loads(scene_path.read_text())
                self.assertEqual((scene_path.parent/"source.bin").read_bytes(),path.read_bytes())
                self.assertEqual(m["unresolved_object_instances"],0)
                self.assertTrue(m["scene_gltf"]); self.assertTrue(m["scene_obj"])
                self.assertEqual(m["gltf_animation_samples"],11)
                self.assertEqual(Path(m["assets"][0]["source_path"]),obj)
                nodes={n["id"]:n for n in scene["nodes"]}; self.assertEqual(len(nodes),4)
                self.assertEqual(nodes[0x20000001]["parent"],0x10000000)
                self.assertIsNone(nodes[0x30000000]["parent"])
                data=(scene_path.parent/scene["animation_buffer"]["uri"]).read_bytes()
                for item,x in ((0x10000000,10),(0x20000000,20),(0x20000001,30),(0x30000000,40)):
                    channel=nodes[item]["channels"][0]; span=channel["keys"]
                    self.assertEqual(len(nodes[item]["channels"]),9); self.assertEqual(span["count"],2)
                    self.assertEqual(struct.unpack_from("<2d",data,span["offset"]),(0,x))
                    self.assertEqual(struct.unpack_from("<2d",data,span["offset"]+span["stride"]),(10,x+10))

    def test_legacy_camera_after_object_does_not_require_a_light(self):
        self.write("Station1",lwob())
        raw="LWSC\n1\nLoadObject Station1\n"+self.legacy_motion("Object",10)+self.legacy_motion("Camera",40)
        out,m=self.convert(self.write("CameraPass",raw))
        scene=json.loads((out/m["scene"]).read_text())
        self.assertEqual([n["id"] for n in scene["nodes"]],[0x10000000,0x30000000])
        self.assertEqual([len(n["channels"]) for n in scene["nodes"]],[9,9])

    def test_motion_owner_errors_and_real_duplicate_camera_are_rejected(self):
        for header,body,message in [
            ("LWSC\n1\n", "AddLight\n"+self.legacy_motion("Object",10),"no matching item owner"),
            ("LWSC\n1\n", self.legacy_motion("Light",10),"no matching item owner"),
            ("LWSC\n3\n", "AddLight\n"+motion3().replace("ObjectMotion","CameraMotion"),"no matching item owner"),
            ("LWSC\n1\n", self.legacy_motion("Camera",10)+self.legacy_motion("Camera",20),"duplicate motion block")]:
            with self.subTest(body=body):
                result=self.run_cli("inspect",self.write("invalid",header+body),code=1)
                self.assertIn(message,result.stderr)

    def image_object(self, name, reference, kind="LWOB"):
        if kind == "LWO2":
            raw = form(kind, layer(0), chunk("CLIP", struct.pack(">I", 7) + chunk("STIL", s0(reference), True)))
        else:
            raw = lwob(chunk("SURF", s0("image") + chunk("TIMG", s0(reference), True)))
        path = self.write(name, raw)
        out, manifest = self.convert(path, code=2)
        directory, data = self.object_data(out, manifest)
        self.assertEqual((directory / "source.bin").read_bytes(), raw)
        return directory, data["image_references"][0]

    def test_image_alternative_extension_and_historical_path(self):
        image = self.write("project/maps/Signe.v2.JPG", b"\xff\xd8\xff\xd9")
        self.write("project/signe.v2.lwo", lwob())
        self.write("project/signe.v2.jpg.txt", b"not an image filename")
        reference = "I:fra/3D/posts/Aliens\\@Newtek/signe.v2.psd"
        for kind in ("LWOB", "LWO2"):
            with self.subTest(kind=kind):
                directory, ref = self.image_object("project/alien.lwo", reference, kind)
                self.assertEqual(ref["path"]["text"], reference)
                self.assertEqual(ref["resolution"], "unique-image-stem")
                self.assertEqual(Path(ref["resolved_path"]), image)
                self.assertEqual((directory / ref["uri"]).read_bytes(), image.read_bytes())
                self.assertEqual(ref["sha256"], hashlib.sha256(image.read_bytes()).hexdigest())
                self.assertEqual(ref["clip"], 7 if kind == "LWO2" else None)

    def test_image_original_extension_and_source_relative_path_priority(self):
        preferred = self.write("project/maps/signe.psd", b"original")
        self.write("project/backup/maps/signe.psd", b"duplicate basename")
        self.write("project/maps/signe.jpg", b"alternative")
        directory, ref = self.image_object("project/alien.lwo", "maps\\signe.psd")
        self.assertEqual(ref["resolution"], "source-relative")
        self.assertEqual(Path(ref["resolved_path"]), preferred)
        # An existing original format wins over a more specific converted path.
        self.write("project/better/maps/signe.jpg", b"closer suffix")
        directory, ref = self.image_object("project/alien.lwo", "Old:better/maps/signe.psd")
        self.assertEqual(ref["resolution"], "ambiguous")
        self.assertEqual({Path(p).suffix for p in ref["candidates"]}, {".psd"})
        self.assertIsNone(ref["uri"])

    def test_image_alternative_suffix_and_ambiguity(self):
        expected = self.write("project/recovered/maps/signe.jpg", b"one")
        self.write("project/elsewhere/signe.png", b"two")
        directory, ref = self.image_object("project/alien.lwo", "I:old/maps/signe.psd")
        self.assertEqual(ref["resolution"], "unique-image-stem-suffix")
        self.assertEqual(Path(ref["resolved_path"]), expected)
        preferred = self.write("project/recovered/maps/signe.png", b"three")
        directory, ref = self.image_object("project/alien.lwo", "I:old/maps/signe.psd")
        self.assertEqual(Path(ref["resolved_path"]), preferred)
        self.write("project/another/maps/signe.png", b"four")
        directory, ref = self.image_object("project/alien.lwo", "I:old/maps/signe.psd")
        self.assertEqual(ref["resolution"], "ambiguous")
        self.assertEqual(len(ref["candidates"]), 2)
        self.assertIsNone(ref["resolved_path"])
        self.assertIsNone(ref["uri"])
        self.assertFalse((directory / "textures").exists())

    def test_image_format_priority_breaks_equal_path_matches(self):
        formats = ("PSD", "TGA", "PNG", "JPEG", "JPG", "GIF", "TIFF", "BMP")
        images = [self.write("project/signe." + ext, ext.encode()) for ext in formats]
        for image in images:
            with self.subTest(extension=image.suffix):
                directory, ref = self.image_object("project/alien.lwo", "Old:signe.exr")
                self.assertEqual(Path(ref["resolved_path"]), image)
                self.assertEqual((directory / ref["uri"]).read_bytes(), image.read_bytes())
                image.unlink()
        # TIFF aliases have the same rank, so neither wins arbitrarily.
        self.write("project/signe.tif", b"one")
        self.write("project/signe.tiff", b"two")
        directory, ref = self.image_object("project/alien.lwo", "Old:signe.exr")
        self.assertEqual(ref["resolution"], "ambiguous")
        self.assertEqual(len(ref["candidates"]), 2)

    def test_image_search_stays_within_owner_directory(self):
        outside = self.write("project/signe.jpg", b"parent")
        self.write("project/sibling/signe.png", b"sibling")
        for reference in ("signe.psd", "../signe.jpg", str(outside)):
            with self.subTest(reference=reference):
                directory, ref = self.image_object("project/objects/alien.lwo", reference)
                self.assertEqual(ref["resolution"], "missing")
                self.assertEqual(ref["candidates"], [])
                self.assertIsNone(ref["uri"])
        nested = self.write("project/objects/deeper/signe.jpg", b"descendant")
        directory, ref = self.image_object("project/objects/alien.lwo", "signe.psd")
        self.assertEqual(Path(ref["resolved_path"]), nested)

    def test_image_substitution_requires_image_extension_and_exact_stem(self):
        self.write("project/signe.jpg", b"one")
        self.write("project/sign.png", b"two")
        for reference in ("signe.lwo", "signe", "signe.txt", "signe2.psd"):
            with self.subTest(reference=reference):
                directory, ref = self.image_object("project/alien.lwo", reference)
                self.assertEqual(ref["resolution"], "missing")
                self.assertEqual(ref["candidates"], [])

    def test_scene_still_images_nested_blocks_and_repeated_references(self):
        image = self.write("project/signe.jpg", b"jpeg bytes")
        # The object's own subtree does not include the scene's texture.
        self.write("project/objects/alien.lwo", lwob(chunk("SURF", s0("image") + chunk("TIMG", s0("signe.psd"), True))))
        still = '{ Image\n{ Clip\n{ Still\n"I:fra/3D/posts/Aliens\\@Newtek/signe.psd"\n}\n}\n}\n'
        scene = self.write("project/01.lws", "LWSC\n3\nLoadObjectLayer 1 objects/alien.lwo\nClipMaps\n{ TextureBlock\n" + still + still + "}\nPlugin CustomObjHandler 1 opaque\n{ Still\n\"unrelated.jpg\"\n}\nEndPlugin\n")
        out, manifest = self.convert(scene, code=2)
        path = out / manifest["scene"]
        self.assertEqual(manifest["image_references_packaged"], 2)
        self.assertEqual(manifest["image_references_unresolved"], 1)
        data = json.loads(path.read_text("utf-8"))
        refs = data["image_references"]
        self.assertEqual(len(refs), 2)
        self.assertEqual(refs[0]["uri"], refs[1]["uri"])
        self.assertEqual(len(list((path.parent / "textures").iterdir())), 1)
        self.assertEqual((path.parent / refs[0]["uri"]).read_bytes(), image.read_bytes())
        self.assertEqual((path.parent / "source.bin").read_bytes(), scene.read_bytes())
        self.assertEqual(scene.read_bytes()[refs[0]["source_offset"]:][:len(refs[0]["path"]["text"])].decode(), refs[0]["path"]["text"])
        self.assertEqual(self.object_data(out, manifest)[1]["image_references"][0]["resolution"], "missing")

    def test_clip_map_preserves_instance_role_and_native_parameter_tree(self):
        self.write("mask.jpg", b"mask")
        self.write("mesh.lwo", lwob())
        clip = '''ClipMaps
{ TextureBlock
  { Texture
    { ImageMap
      "layer one"
      Enable 1
      Negative 1
      { Opacity
        0
        0.75
        0
      }
    }
    { TextureMap
      { Center
        0 5.9 1.727119
        0
      }
      Coordinates 0
      RefObject "(none)"
    }
    Projection 0
    Axis 2
    { Image
      { Clip
        { Still
          "old:mask.psd"
        }
      }
    }
    FutureSetting "keep these bytes"
  }
  { Texture
    { Procedural
      Enable 0
      Negative 0
      Type "unimplemented"
    }
  }
}'''
        raw = "LWSC\n3\nLoadObjectLayer 1 mesh.lwo\n" + clip + "\nObjectDissolve 0.5\nLoadObjectLayer 1 mesh.lwo\n{ Clip\n{ Still\n\"old:mask.psd\"\n}\n}\n"
        scene = self.write("clip.lws", raw)
        out, manifest = self.convert(scene, code=2)
        path = out / manifest["scene"]
        data = json.loads(path.read_text("utf-8"))
        first, second = data["nodes"]
        self.assertEqual(first["asset_index"], second["asset_index"])
        self.assertEqual(second["clip_maps"], [])
        self.assertIsNone(second["object_dissolve"])
        self.assertEqual(first["object_dissolve"]["native_statement"]["text"], "ObjectDissolve 0.5")
        preserved = first["clip_maps"][0]
        self.assertEqual(preserved["scope"], "object-instance")
        self.assertEqual(preserved["coverage"], {"mode": "binary-cutout", "cutoff": None, "polarity": "not-evaluated"})
        self.assertEqual(preserved["image_references"], [0])
        self.assertEqual([ref["role"] for ref in data["image_references"]], ["clip-map", "unspecified"])
        native = preserved["native_source"]
        archived = (path.parent / native["uri"]).read_bytes()
        self.assertEqual(archived, scene.read_bytes())
        self.assertEqual(archived[native["offset"]:native["offset"] + native["bytes"]], clip.encode())
        fields = preserved["parameters"]
        names = [field["name"]["text"] if field["name"] else None for field in fields]
        negatives = [field for field in fields if field["name"] and field["name"]["text"] == "Negative"]
        self.assertEqual([field["value"]["text"] for field in negatives], ["1", "0"])
        self.assertEqual([names[field["parent"]] for field in negatives], ["ImageMap", "Procedural"])
        center = names.index("Center")
        self.assertEqual(fields[center + 1]["parent"], center)
        self.assertEqual(fields[center + 1]["value"]["text"], "0 5.9 1.727119")
        self.assertEqual(fields[names.index("FutureSetting")]["value"]["text"], '"keep these bytes"')
        for index, field in enumerate(fields):
            if field["parent"] is not None:
                self.assertLess(field["parent"], index)
                self.assertTrue(fields[field["parent"]]["block"])
        self.assertEqual(manifest["scene_clip_maps_not_evaluated"], 1)
        self.assertFalse(manifest["clip_map_targets"]["gltf"]["requires_extension"])

    def test_legacy_clip_map_and_animated_dissolve_preserved(self):
        self.write("dora_mask.JPG", b"mask")
        self.write("mesh.lwo", lwob())
        clip = "ClipMap Planar Image Map\nTextureImage E:\\Perso\\dora maar\\dora_mask.JPG\nTextureFlags 4\nTextureAxis 2\nTextureSize 3.98 3.98 1\nTextureValue 0.500000"
        dissolve = "ObjectDissolve (envelope)\n{ Envelope\n2\nKey 0 0 3 0 0 0 0 0 0\nKey 0.5 1 3 0 0 0 0 0 0\nBehaviors 1 1\n}"
        scene = self.write("legacy.lws", "LWSC\n1\nLoadObject mesh.lwo\n" + clip + "\nShadowOptions 7\n" + dissolve + "\nLoadObject mesh.lwo\n")
        out, manifest = self.convert(scene, code=2)
        data = json.loads((out / manifest["scene"]).read_text("utf-8"))
        preserved = data["nodes"][0]["clip_maps"][0]
        self.assertEqual(preserved["declaration"]["text"], "Planar Image Map")
        self.assertEqual(preserved["parameters"][-1]["name"]["text"], "TextureValue")
        self.assertEqual(preserved["parameters"][-1]["value"]["text"], "0.500000")
        self.assertIsNone(preserved["coverage"]["cutoff"])
        self.assertEqual(data["nodes"][0]["object_dissolve"]["native_statement"]["text"], dissolve)
        self.assertEqual(data["nodes"][1]["clip_maps"], [])
        self.assertEqual(data["image_references"][0]["role"], "clip-map")
        native = preserved["native_source"]
        self.assertEqual(scene.read_bytes()[native["offset"]:native["offset"] + native["bytes"]], clip.encode())

    def test_procedural_clip_map_without_images_is_reported(self):
        scene = self.write("procedural.lws", "LWSC\n3\nAddNullObject owner\nClipMaps\n{ TextureBlock\n{ Texture\n{ Procedural\nType Checkerboard\n}\n}\n}\n")
        out, manifest = self.convert(scene, code=2)
        self.assertEqual(manifest["scene_clip_maps_not_evaluated"], 1)
        data = json.loads((out / manifest["scene"]).read_text("utf-8"))
        self.assertEqual(data["nodes"][0]["clip_maps"][0]["image_references"], [])
        for tail in ("ClipMaps\n{ TextureBlock\n", "ClipMaps\n{ Unexpected\n}\n"):
            malformed = self.write("malformed.lws", "LWSC\n3\nAddNullObject owner\n" + tail)
            self.run_cli("inspect", malformed, code=1)

    def test_lwob_binary_hash_encoding_winding(self):
        raw = lwob(chunk("XTRA", b"abc"))
        path = self.write("élément sans extension", raw)
        out, manifest = self.convert(path)
        directory, obj = self.object_data(out, manifest)
        self.assertEqual(manifest["assets"][0]["name"], "élément_sans_extension.lwo")
        self.assertEqual(manifest["assets"][0]["obj"], "obj/élément_sans_extension.lwo.obj")
        self.assertEqual(manifest["assets"][0]["gltf"], "gltf/élément_sans_extension.lwo.gltf")
        self.assertEqual((directory / "source.bin").read_bytes(), raw)
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(obj["source"]["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(obj["tags"][0]["text"], "café")
        self.assertEqual(obj["tags"][0]["decoding"], "latin1-hypothesis")
        self.assertEqual(obj["buffer_bytes"], len((directory / "geometry.bin").read_bytes()))
        self.assertEqual(struct.unpack_from("<3f", (directory / "geometry.bin").read_bytes()), (0, 0, 1))
        text = (out / manifest["assets"][0]["obj"]).read_text()
        self.assertIn("v 0 0 -1", text)
        self.assertIn("f 3//3 2//2 1//1", text)
        self.assertEqual(obj["chunks"][-1]["status"], "preserved-opaque")

    def test_readable_layout_collisions_and_material_links(self):
        self.write("a/mesh.lwo", lwob())
        self.write("b/mesh.lwo", lwob(chunk("XTRA", b"different")))
        self.write("c/mesh.lwo-2", lwob(chunk("XTRA", b"third")))
        self.write("d/é ! #.lwo", lwob(chunk("XTRA", b"fourth")))
        scene = self.write("layout.lws", "LWSC\n1\nLoadObject a/mesh.lwo\nLoadObject b/mesh.lwo\nLoadObject c/mesh.lwo-2\nLoadObject d/é ! #.lwo\n")
        out, manifest = self.convert(scene)
        self.assertEqual(manifest["layout_version"], "0.2")
        self.assertEqual([a["name"] for a in manifest["assets"]], ["mesh.lwo", "mesh.lwo-3", "mesh.lwo-2", "é_!__.lwo"])
        self.assertEqual(manifest["scene"], "IR/layout.lws/scene.json")
        self.assertEqual(manifest["scene_obj"], "obj/layout.lws.obj")
        for asset in manifest["assets"]:
            self.assertEqual(asset["uri"], f"IR/{asset['name']}/object.json")
            self.assertEqual(asset["obj"], f"obj/{asset['name']}.obj")
            data = json.loads((out / asset["uri"]).read_text("utf-8"))
            self.assertEqual(data["source"]["sha256"], asset["id"])
            self.assertNotIn(asset["id"], asset["uri"])
        for obj in (out / "obj").glob("*.obj"):
            lines = obj.read_text("utf-8").splitlines()
            mtllib = next(line.split()[1] for line in lines if line.startswith("mtllib "))
            self.assertEqual(mtllib, obj.with_suffix(".mtl").name)
            materials = {line.split()[1] for line in (obj.parent / mtllib).read_text("utf-8").splitlines() if line.startswith("newmtl ")}
            self.assertTrue({line.split()[1] for line in lines if line.startswith("usemtl ")} <= materials)
        self.assertEqual(manifest["formats"]["gltf"], "generated")
        self.assertTrue((out / manifest["scene_gltf"]).is_file())
        for planned in ("blender",):
            self.assertEqual(manifest["formats"][planned], "not-implemented")
            self.assertEqual(list((out / planned).iterdir()), [])

    def test_extensionless_scene_objects_and_inferred_name_collisions(self):
        sources = [self.write("a/mesh", lwob()),
                   self.write("b/mesh.lwo", lwob(chunk("XTRA", b"second"))),
                   self.write("c/mesh.lwo-2", lwob(chunk("XTRA", b"third"))),
                   self.write("d/modern", form("LWO2", layer(0)))]
        scene = self.write("mesh", "LWSC\n1\n" + "".join(
            f"LoadObject {path.relative_to(self.root).as_posix()}\n" for path in sources))
        originals = {path: path.read_bytes() for path in sources + [scene]}
        out, manifest = self.convert(scene)
        self.assertEqual([a["name"] for a in manifest["assets"]],
                         ["mesh.lwo", "mesh.lwo-3", "mesh.lwo-2", "modern.lwo"])
        self.assertEqual(manifest["scene"], "IR/mesh.lws/scene.json")
        self.assertEqual(manifest["scene_obj"], "obj/mesh.lws.obj")
        self.assertEqual(manifest["scene_gltf"], "gltf/mesh.lws.gltf")
        self.assertEqual((out / "IR/mesh.lws/source.bin").read_bytes(), originals[scene])
        native = json.loads((out / manifest["scene"]).read_text("utf-8"))
        self.assertEqual(native["nodes"][0]["object_path"]["text"], "a/mesh")
        for asset in manifest["assets"]:
            name = asset["name"]
            self.assertEqual(asset["uri"], f"IR/{name}/object.json")
            self.assertEqual(asset["obj"], f"obj/{name}.obj")
            self.assertEqual(asset["gltf"], f"gltf/{name}.gltf")
            self.assertEqual((out / f"IR/{name}/source.bin").read_bytes(), originals[Path(asset["source_path"])])
        for path in (out / "gltf").glob("*.gltf"):
            data = json.loads(path.read_text("utf-8"))
            for buffer in data["buffers"]:
                binary = path.parent / unquote(buffer["uri"])
                self.assertEqual(binary, path.with_suffix(".bin"))
                self.assertEqual(binary.stat().st_size, buffer["byteLength"])
        for path in (out / "obj").glob("*.obj"):
            self.assertIn(f"mtllib {path.with_suffix('.mtl').name}\n", path.read_text("utf-8"))
            self.assertTrue(path.with_suffix(".mtl").is_file())
        for path, raw in originals.items():
            self.assertEqual(path.read_bytes(), raw)

    def test_lwo2_vmad_seam(self):
        maps = chunk("VMAP", b"TXUV" + U16(2) + s0("uv") + b"".join(vx(i)+F32(i/2, 0) for i in range(3)))
        maps += chunk("VMAD", b"TXUV" + U16(2) + s0("uv") + vx(0) + vx(0) + F32(.25, .75))
        path = self.write("uv.lwo", form("LWO2", layer(0), maps))
        out, manifest = self.convert(path, "--uv-map", "uv")
        directory, obj = self.object_data(out, manifest)
        self.assertEqual(len(obj["maps"]), 2)
        self.assertIn("vt 0.25 0.75", (out / manifest["assets"][0]["obj"]).read_text())
        self.assertIn("f 3/3/3 2/2/2 1/1/1", (out / manifest["assets"][0]["obj"]).read_text())
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
        self.assertIn("p 65281", (out / manifest["assets"][0]["obj"]).read_text())

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
        out, manifest = self.convert(scene)
        self.assertIn("v 10 0 -1", (out / manifest["scene_obj"]).read_text())
        self.assertNotIn("v 100 ", (out / manifest["scene_obj"]).read_text())
        out, manifest = self.convert(self.write("missing.lws", "LWSC\n3\nLoadObjectLayer 2 layers.lwo\n"), code=2)
        self.assertEqual(manifest["unresolved_object_instances"], 1)
        self.assertEqual(json.loads((out / manifest["scene"]).read_text())["nodes"][0]["resolution"], "missing-layer")

    def test_linear_scene_parent_pivot_and_default_frame(self):
        self.write("tri", lwob())
        scene = "LWSC\n1\nFirstFrame 5\nFramesPerSecond 25\nAddNullObject parent\n"
        scene += motion1([(0, [10,0,0,0,0,0,1,1,1], 1)])
        scene += "LoadObject tri\nParentObject 1\nPivotPoint 1 0 0\n"
        scene += motion1([(0, [0,0,0,0,0,0,1,1,1], 1), (10, [10,0,0,0,0,0,1,1,1], 1)])
        out, manifest = self.convert(self.write("move.lws", scene))
        self.assertEqual(manifest["frame"], 5)
        self.assertIn("v 14 0 -1", (out / manifest["scene_obj"]).read_text())
        data = json.loads((out / manifest["scene"]).read_text())
        self.assertEqual(data["animation_bytes"], len(((out / manifest["scene"]).parent / "animation.bin").read_bytes()))

    def test_heading_and_negative_scale(self):
        self.write("tri", lwob())
        scene = "LWSC\n1\nLoadObject tri\n" + motion1([(0, [0,0,0,90,0,0,-1,1,1], 1)])
        out, manifest = self.convert(self.write("rotation.lws", scene))
        lines = (out / manifest["scene_obj"]).read_text().splitlines()
        points = [list(map(float, line.split()[1:])) for line in lines if line.startswith("v ")]
        self.assertAlmostEqual(points[0][0], 1)
        self.assertAlmostEqual(points[1][2], -1)
        self.assertIn("f 1//1 2//2 3//3", lines)

    def test_v3_seconds_and_unsupported_spline(self):
        self.write("tri", lwob())
        for shape, code in [(3, 0), (0, 0), (5, 2)]:
            scene = self.write(f"motion{shape}.lws", "LWSC\n3\nFramesPerSecond 20\nLoadObject tri\n" + motion3(shape=shape))
            out, manifest = self.convert(scene, "--frame", "10", code=code)
            if not code:
                self.assertIn("v 5 0 -1", (out / manifest["scene_obj"]).read_text())
            else:
                self.assertIsNone(manifest["scene_obj"])
                self.assertIn("animation", manifest["scene_obj_issue"])
                self.assertFalse((out / "obj" / (Path(manifest["input"]).name + ".obj")).exists())

    def test_envelope_modifiers_and_declared_count(self):
        self.write("tri", lwob())
        scene = self.write("channel.lws", "LWSC\n3\nLoadObject tri\n" + motion3(declared=0, modifier='{ ChannelHandler\n"Expression"\n}\n'))
        out, manifest = self.convert(scene, code=2)
        channel = json.loads((out / manifest["scene"]).read_text())["nodes"][0]["channels"][0]
        self.assertEqual((channel["declared_keys"], channel["keys"]["count"], channel["opaque_modifiers"]), (0, 2, 1))
        self.assertIsNone(manifest["scene_obj"])

    def test_path_ambiguity_and_explicit_mapping(self):
        self.write("a/shared", lwob())
        b = self.write("b/shared", lwob())
        scene = self.write("ambiguous.lws", "LWSC\n1\nLoadObject old:shared\n")
        out, manifest = self.convert(scene, code=2)
        node = json.loads((out / manifest["scene"]).read_text())["nodes"][0]
        self.assertEqual(node["resolution"], "ambiguous")
        self.assertEqual(len(node["candidates"]), 2)
        out, manifest = self.convert(scene, "--map", "old:=" + str(b.parent))
        self.assertEqual(manifest["unresolved_object_instances"], 0)

    def test_plugin_blocks_cannot_inject_scene_nodes(self):
        path = self.write("plugin.lws", "LWSC\n3\nAddNullObject n\nPlugin ItemMotionHandler 1 test\nLoadObject fake\n{ nested\n}\nEndPlugin\n")
        summary = json.loads(self.run_cli("inspect", path).stdout)
        self.assertEqual((summary["object_loads"], summary["nodes"], summary["plugins"]), (0, 1, 1))

    def test_content_root_consensus_resolves_duplicate_names(self):
        expected=self.write("complete/Objects/body.lwo",lwob())
        self.write("complete/Objects/head.lwo",lwob())
        self.write("incomplete/Objects/body.lwo",lwob())
        source=self.write("Scenes/shot.lws","LWSC\n1\n"+"LoadObject Objects/body.lwo\n"*6+"LoadObject Objects/head.lwo\n")
        out,manifest=self.convert(source)
        inference=manifest["content_root_inference"]
        self.assertEqual(Path(inference["selected_root"]),self.root/"complete")
        self.assertEqual((inference["distinct_references"],inference["matched_references"]),(2,2))
        nodes=json.loads((out/manifest["scene"]).read_text())["nodes"]
        self.assertEqual(Path(nodes[0]["resolved_path"]),expected)
        self.assertEqual(nodes[0]["resolution"],"inferred-content-root")

    def test_content_root_tie_remains_ambiguous(self):
        for project in ("a","b"):
            for name in ("body","head"): self.write(f"{project}/Objects/{name}.lwo",lwob())
        out,manifest=self.convert(self.write("shot.lws","LWSC\n1\nLoadObject Objects/body.lwo\nLoadObject Objects/head.lwo\n"),code=2)
        self.assertEqual(manifest["content_root_inference"]["status"],"ambiguous")
        self.assertIsNone(manifest["content_root_inference"]["selected_root"])
        self.assertEqual(manifest["unresolved_object_instances"],2)

    def test_content_root_parent_and_sibling_search(self):
        expected=self.write("Objects/body.lwo",lwob())
        source=self.write("Scenes/shot.lws","LWSC\n1\nLoadObject Objects/body.lwo\n")
        out=self.base/"parent-output"
        self.run_cli("convert",source,"--output",out)
        manifest=json.loads((out/"manifest.json").read_text())
        self.assertEqual(Path(manifest["content_root_inference"]["selected_root"]),self.root)
        node=json.loads((out/manifest["scene"]).read_text())["nodes"][0]
        self.assertEqual(Path(node["resolved_path"]),expected)
        self.assertEqual(node["resolution"],"inferred-content-root")

    def test_lwsc5_explicit_ids_preserve_parenting_and_infer_sibling_objects(self):
        self.write("Objects/body.lwo",lwob())
        source=self.write("Scenes/v5.lws","LWSC\n5\nLoadObjectLayer 1 1000004c Objects/body.lwo\nParentItem 1000000c\nAddBone 4003004c\nBoneName thigh\nBoneType 0\nParentItem 1000004c\nAddNullObject 1000000c controller\nAddLight 20000005\nLightName lamp\nAddCamera 30000002\n")
        out=self.base/"v5-output"
        self.run_cli("convert",source,"--output",out,code=2)
        manifest=json.loads((out/"manifest.json").read_text());scene=json.loads((out/manifest["scene"]).read_text())
        self.assertEqual(manifest["unresolved_object_instances"],0)
        self.assertEqual(Path(manifest["content_root_inference"]["selected_root"]),self.root)
        self.assertEqual([n["id"] for n in scene["nodes"]],[0x1000004c,0x4003004c,0x1000000c,0x20000005,0x30000002])
        self.assertEqual(scene["nodes"][0]["parent"],0x1000000c)
        self.assertEqual(scene["nodes"][1]["bone"]["owner_item"],0x1000004c)
        self.assertEqual((out/manifest["scene"]).with_name("source.bin").read_bytes(),source.read_bytes())

    def test_lwsc5_rejects_duplicate_mistyped_and_mismatched_ids(self):
        for text in ("AddNullObject 1000000c a\nAddNullObject 1000000c b\n",
                     "AddNullObject 2000000c a\n", "AddNullObject xyz a\n",
                     "AddNullObject 1000000c a\nAddBone 4000004c\n"):
            source=self.write("invalid-v5.lws","LWSC\n5\n"+text)
            self.run_cli("inspect",source,code=1)

    def test_lwsc5_partial_profile_exports_keyed_hierarchy(self):
        self.write("Objects/body.lwo",lwob())
        raw="LWSC\n5\nFramesPerSecond 2\nFirstFrame 0\nLastFrame 2\n"
        raw+="LoadObjectLayer 1 1000004c Objects/body.lwo\nParentItem 1000000c\n"+motion3(channel=1)
        raw+="AddNullObject 1000000c parent\n"+motion3()
        source=self.write("keyed-v5.lws",raw)
        out,m=self.convert(source,"--frame","1",code=2)
        scene=json.loads((out/m["scene"]).read_text())
        self.assertEqual(m["scene_format"],{"format":"LWSC","version":5,"reader_profile":"lwsc5-partial-0.1","support":"partial","uninterpreted_statements":0})
        self.assertEqual(scene["reader_profile"],"lwsc5-partial-0.1")
        self.assertEqual((scene["time_domain"],scene["angle_units"]),("second","radians"))
        self.assertIn("v 5 5 -1",(out/m["scene_obj"]).read_text())
        self.assertTrue((out/m["scene_gltf"]).is_file())
        self.assertGreaterEqual(m["gltf_animation_channels"],2)
        self.assertEqual((out/m["scene"]).with_name("source.bin").read_bytes(),source.read_bytes())

    def test_lwsc5_uninterpreted_blocks_are_indexed_without_fake_items(self):
        self.write("tri.lwo",lwob())
        blocks=["UnknownRenderSetting 42", "{ FutureBlock\n  { Nested\n    AddNullObject 100000fe fake\n  }\n}"]
        raw="LWSC\n5\nLoadObject 1000004c tri.lwo\n"+"\n".join(blocks)+"\n"
        source=self.write("opaque-v5.lws",raw.replace("\n","\r\n"))
        out,m=self.convert(source,code=2)
        scene=json.loads((out/m["scene"]).read_text())
        self.assertEqual(len(scene["nodes"]),1)
        self.assertEqual(len(scene["uninterpreted_statements"]),2)
        self.assertEqual(m["scene_format"]["uninterpreted_statements"],2)
        for statement,expected in zip(scene["uninterpreted_statements"],blocks):
            saved=source.read_bytes()[statement["source_offset"]:][:statement["bytes"]]
            self.assertEqual(saved,expected.replace("\n","\r\n").encode())
        self.assertEqual([s["name"]["text"] for s in scene["uninterpreted_statements"]],["UnknownRenderSetting","FutureBlock"])
        self.assertEqual(json.loads(self.run_cli("inspect",source).stdout)["uninterpreted_statements"],2)

    def test_lwsc5_position_controllers_preserve_settings_and_block_false_animation(self):
        self.write("tri.lwo",lwob())
        for axis in "XYZ":
            for mode in (0,6,7,"broken"):
                with self.subTest(axis=axis,mode=mode):
                    raw="LWSC\n5\nLoadObject 1000004c tri.lwo\n"+motion3()
                    raw+=f"{axis}Controller {mode}\nSplineItem 1000000c\nIKFKBlending 0\nPathAlignLookAhead 0.033\nAddNullObject 1000000c path\n"
                    out,m=self.convert(self.write("controller-v5.lws",raw),code=2)
                    scene=json.loads((out/m["scene"]).read_text()); node=scene["nodes"][0]
                    params={p["name"]["text"]:p["value"]["text"] for p in node["rig_parameters"]}
                    self.assertEqual(params[f"{axis}Controller"],str(mode))
                    self.assertEqual(params["SplineItem"],"1000000c")
                    self.assertEqual(params["IKFKBlending"],"0")
                    self.assertEqual(params["PathAlignLookAhead"],"0.033")
                    self.assertEqual(node["unsupported_transform"],mode!=0)
                    if mode==0: self.assertIsNotNone(m["scene_gltf"])
                    else:
                        self.assertIsNone(m["scene_gltf"])
                        self.assertIsNone(m["scene_obj"])
                        self.assertIn(f"{axis}Controller",m["scene_gltf_issue"])
                        self.assertTrue((out/m["assets"][0]["gltf"]).is_file())

    def test_lwsc5_unknown_bone_type_stays_in_ir_without_rest_skin(self):
        self.write("tri.lwo",lwob())
        raw="LWSC\n5\nLoadObject 1000004c tri.lwo\nAddBone 4003004c\nBoneType 1\nBoneRestPosition 0 0 0\nBoneRestDirection 0 0 0\nBoneRestLength 1\n"
        out,m=self.convert(self.write("joint-v5.lws",raw),"--gltf-rigs","all",code=2)
        scene=json.loads((out/m["scene"]).read_text())
        self.assertEqual(scene["nodes"][1]["rig_parameters"][0]["value"]["text"],"1")
        self.assertEqual(m["gltf_rigs"][0]["status"],"blocked")
        self.assertIn("BoneType 1",m["gltf_rigs"][0]["issue"])

    def test_content_root_keeps_explicit_rules_authoritative(self):
        self.write("candidate/Objects/body.lwo",lwob())
        self.write("candidate/Objects/head.lwo",lwob())
        mapped=self.write("override/body.lwo",lwob())
        source=self.write("shot.lws","LWSC\n1\nLoadObject Special/body.lwo\nLoadObject Objects/head.lwo\n")
        out,manifest=self.convert(source,"--map","Special/="+str(mapped.parent))
        node=json.loads((out/manifest["scene"]).read_text())["nodes"][0]
        self.assertEqual(node["resolution"],"mapped-prefix")
        self.assertEqual(Path(node["resolved_path"]),mapped)

    def test_surface_preset(self):
        raw = form("PST_", chunk("NAME", b"preset"), chunk("PDAT", form("LWO2", chunk("SURF", s0("mat")+s0("")))))
        for name in ("preset.srf", "preset"):
            with self.subTest(name=name):
                out, manifest = self.convert(self.write(name, raw))
                directory, obj = self.object_data(out, manifest)
                self.assertEqual(manifest["assets"][0]["name"], name)
                self.assertEqual(obj["positions"]["count"], 0)
                self.assertEqual((directory / "source.bin").read_bytes(), raw)

    def test_malformed_inputs_fail_with_offset(self):
        cases = [b"", lwob()[:-1], form("LWO2", chunk("PNTS", b"123")), form("LWO2", chunk("PNTS", F32(float("nan"),0,0))), lwob(poly=U16(1)+U16(99)+U16(1)), form("LWOB", chunk("SRFS", b"unterminated")), b"LWSC\n3\nPlugin x\n", b"LWSC\n1\nLoadObject tri\nObjectMotion\n9\n1\n"]
        for i, raw in enumerate(cases):
            with self.subTest(i=i):
                self.assertIn("at byte", self.run_cli("inspect", self.write(str(i), raw), code=1).stderr)

    def test_no_overwrite_or_output_inside_sources(self):
        path = self.write("tri", lwob())
        out, manifest = self.convert(path)
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
        self.assertIn("usemtl a0_m0", (out / manifest["assets"][0]["obj"]).read_text())
        self.assertIn("Kd 0 1 0", (out / manifest["assets"][0]["mtl"]).read_text())

    def test_repeat_and_stepped_interpolation(self):
        self.write("tri", lwob())
        scene = self.write("repeat.lws", "LWSC\n1\nLoadObject tri\n" + motion1([(2, [0,0,0,0,0,0,1,1,1], 1), (12, [10,0,0,0,0,0,1,1,1], 1)], end=2))
        out, manifest = self.convert(scene, "--frame", "17")
        self.assertIn("v 5 0 -1", (out / manifest["scene_obj"]).read_text())
        scene = self.write("step.lws", "LWSC\n3\nLoadObject tri\nFramesPerSecond 30\n" + motion3(shape=4))
        out, manifest = self.convert(scene, "--frame", "15")
        self.assertIn("v 0 0 -1", (out / manifest["scene_obj"]).read_text())

    def polygon_fixture(self, points, indices, maps=b""):
        return form("LWO2", chunk("PNTS", F32(*(v for point in points for v in point))), chunk("POLS", b"FACE"+U16(len(indices))+b"".join(vx(i) for i in indices)), maps)

    def check_polygon_triangles(self, out, manifest, points, boundary, axes=(0,1)):
        directory, obj = self.object_data(out, manifest)
        # The native boundary is independent of the triangulated derivative.
        self.assertEqual(obj["primitives"]["count"], 1)
        self.assertEqual(obj["indices"]["count"], len(boundary))
        polygons = [line.split()[1:] for line in (out / manifest["assets"][0]["obj"]).read_text().splitlines() if line.startswith("f ")]
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
        uv = [tuple(map(float,line.split()[1:])) for line in (out / manifest["assets"][0]["obj"]).read_text().splitlines() if line.startswith("vt ")]
        for polygon in polygons:
            for corner in polygon:
                vertex,texture,_ = map(int,corner.split('/'))
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
            self.assertFalse(any(line.startswith('f ') for line in (out / manifest["assets"][0]["obj"]).read_text().splitlines()))

    def test_nonplanar_face_is_an_explicit_approximation(self):
        points = [(0,0,0),(1,0,0),(1,1,.2),(0,1,0)]
        out, manifest = self.convert(self.write("nonplanar",self.polygon_fixture(points,[0,1,2,3])),code=2)
        self.assertEqual(manifest["obj_nonplanar_faces"], 1)
        self.assertEqual(manifest["obj_triangles"], 2)


if __name__ == "__main__":
    unittest.main()
