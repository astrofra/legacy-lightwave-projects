"""Raster decoding, native bindings, projected UVs and portable texture packages."""
import hashlib
import base64
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import unittest
import zlib

import test_gltf as gltf
import test_converter as fixtures
from test_converter import EXE, U16, F32, s0, chunk, form, vx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from output_layout import copy_texture


def ilbm(rows, planes=24, compression=1, mask=0, transparent=0, palette=b"", camg=0):
    width, height = len(rows[0]), len(rows)
    header = struct.pack(">HHhhBBBBHBBhh", width, height, 0, 0, planes, mask, compression, 0, transparent, 1, 1, width, height)
    body = bytearray()
    for row in rows:
        for plane in range(planes + (mask == 1)):
            bits = bytearray((width + 15) // 16 * 2)
            for x, pixel in enumerate(row):
                number = pixel if isinstance(pixel, int) else sum(c << (8*i) for i, c in enumerate(pixel[:planes // 8]))
                value = (pixel[-1] != 0) if plane == planes else (number >> plane) & 1
                bits[x // 8] |= value << (7 - x % 8)
            if compression:
                # Deliberate no-ops, repeated runs and literals exercise ByteRun1.
                body.append(128)
                for pos in range(0, len(bits), 128):
                    part = bits[pos:pos+128]
                    if len(set(part)) == 1:
                        body.extend((257-len(part), part[0]))
                    else:
                        body.append(len(part)-1)
                        body.extend(part)
            else:
                body.extend(bits)
    return form("ILBM", chunk("BMHD", header), chunk("CMAP", palette) if palette else b"", chunk("CAMG", struct.pack(">I",camg)), chunk("BODY", bytes(body)))


def texture(tag="CTEX", path="maps/screen.iff", projection="Planar Image Map", flags=4, extra=b""):
    return b"".join(chunk(k, v, True) for k, v in [(tag,s0(projection)),("TIMG",s0(path)),("TFLG",U16(flags)),("TSIZ",F32(1,1,1))]) + extra


def textured(blocks=None, points=None, scalar=b""):
    if blocks is None: blocks = texture()
    if points is None: points = [(-.5,-.5,0),(.5,-.5,0),(-.5,.5,0)]
    return form("LWOB", chunk("PNTS",F32(*(v for p in points for v in p))), chunk("POLS",U16(3)+U16(0)+U16(1)+U16(2)+U16(1)),
                chunk("SRFS",s0("surface")), chunk("SURF",s0("surface")+chunk("COLR",bytes([255,255,255,0]),True)+scalar+blocks))


def imap(channel="COLR", clip=17, uv="atlas", enabled=1, header=b"", extra=b"", kind="IMAP"):
    attributes = chunk("CHAN",channel.encode(),True)+chunk("ENAB",U16(enabled),True)
    attributes += chunk("OPAC",U16(0)+F32(1)+vx(0),True)+header
    return chunk("BLOK",chunk(kind,s0(b"\x80")+attributes,True)+chunk("PROJ",U16(5),True)
                 +chunk("IMAG",vx(clip),True)+chunk("VMAP",s0(uv),True)+extra,True)


def uv_textured(blocks=None, scalar=b"", maps=None, clips=None):
    if blocks is None: blocks=imap()
    if maps is None:
        maps=chunk("VMAP",b"TXUV"+U16(2)+s0("atlas")+b"".join(vx(i)+F32(i/4,0) for i in range(4)))
        maps+=chunk("VMAD",b"TXUV"+U16(2)+s0("atlas")+vx(0)+vx(1)+F32(.75,1))
    if clips is None: clips=chunk("CLIP",struct.pack(">I",17)+chunk("STIL",s0("I:old/project/maps/screen.tga"),True))
    return form("LWO2",chunk("PNTS",F32(0,0,0,1,0,0,0,1,0,-1,0,0)),
                chunk("POLS",b"FACE"+U16(3)+vx(0)+vx(1)+vx(2)+U16(3)+vx(0)+vx(2)+vx(3)),
                chunk("TAGS",s0("surface")),chunk("PTAG",b"SURF"+vx(0)+U16(0)+vx(1)+U16(0)),maps,
                chunk("SURF",s0("surface")+s0("")+chunk("COLR",F32(1,1,1)+vx(0),True)+scalar+blocks),clips)


def png(path):
    """Independent stdlib PNG RGBA8 reader, including row-filter reversal."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    position, payload = 8, b""
    while position < len(data):
        size, tag = struct.unpack_from(">I4s",data,position)
        block = data[position+8:position+8+size]
        assert zlib.crc32(tag+block) == struct.unpack_from(">I",data,position+8+size)[0]
        if tag == b"IHDR":
            width,height,depth,kind,compression,filtering,interlace = struct.unpack(">IIBBBBB",block)
            assert (depth,kind,compression,filtering,interlace) == (8,6,0,0,0)
        elif tag == b"IDAT": payload += block
        position += 12+size
    raw = zlib.decompress(payload)
    stride, previous, rows = width*4, [0]*(width*4), []
    for y in range(height):
        kind = raw[y*(stride+1)]
        row = list(raw[y*(stride+1)+1:(y+1)*(stride+1)])
        for i in range(stride):
            a,b,c = row[i-4] if i>=4 else 0,previous[i],previous[i-4] if i>=4 else 0
            p = a+b-c
            pa,pb,pc = abs(p-a),abs(p-b),abs(p-c)
            predictor = (0,a,b,(a+b)//2,a if pa<=pb and pa<=pc else b if pb<=pc else c)[kind]
            row[i] = (row[i]+predictor)&255
        rows.append([tuple(row[i:i+4]) for i in range(0,stride,4)])
        previous = row
    return rows


class TextureTests(unittest.TestCase):
    setUp = fixtures.Converter.setUp
    tearDown = fixtures.Converter.tearDown
    write = fixtures.Converter.write
    run_cli = fixtures.Converter.run_cli
    convert = fixtures.Converter.convert
    object_data = fixtures.Converter.object_data

    def export_image(self, image, blocks=None, points=None, scalar=b"", *options):
        self.write("maps/screen.iff",image)
        source = self.write("mesh.lwo",textured(blocks,points,scalar))
        out, manifest = self.convert(source,*options,code=2)
        directory, native = self.object_data(out,manifest)
        return out,manifest,directory,native

    def test_lwo2_uv_clip_after_surface_and_discontinuous_corners(self):
        self.write("maps/screen.iff",ilbm([[(255,0,0),(0,255,0)]]))
        raw=uv_textured()
        source=self.write("modern.lwo",raw)
        out,manifest=self.convert(source,code=2)
        directory,native=self.object_data(out,manifest)
        t=native["materials"][0]["textures"][0]
        self.assertEqual(t["lwo2_block"]["clip_index"],17)
        self.assertEqual(t["lwo2_block"]["uv_map"]["text"],"atlas")
        self.assertEqual(t["export_status"],"approximated")
        self.assertEqual(manifest["assets"][0]["texture_blocks_not_evaluated"],0)
        self.assertEqual((directory/"source.bin").read_bytes(),raw)
        data,buffers=gltf.load(out/manifest["assets"][0]["gltf"])
        p=data["meshes"][0]["primitives"][0]
        values=gltf.values(data,buffers,p["attributes"]["TEXCOORD_0"])
        for (polygon,corner,point),(u,v) in zip(gltf.source_map(p,buffers),values):
            self.assertEqual((u,v),(.75,0) if polygon==1 and point==0 else (point/4,1))
        obj=(out/manifest["assets"][0]["obj"]).read_text()
        self.assertIn("vt 0.75 1",obj)
        maps=native["materials"][0]["derived_maps"]
        self.assertEqual(png(directory/maps["base_color"])[0],[(255,0,0,255),(0,255,0,255)])
        self.assertIn("map_Kd "+maps["base_color"],(out/manifest["assets"][0]["mtl"]).read_text())

    def test_lwo2_tga_to_real_jpeg_remap(self):
        # A 2x2 RGB JPEG generated once; no Pillow dependency in the tests.
        jpeg=base64.b64decode(
            "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAIBAQEBAQIBAQECAgICAgQDAgICAgUEBAMEBgUGBgYFBgYGBwkIBgcJBwYGCAsICQoKCgoKBggLDAsKDAkKCgr/"
            "2wBDAQICAgICAgUDAwUKBwYHCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgr/wAARCAACAAIDASIAAhEBAxEB/"
            "8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2Jy"
            "ggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5"
            "usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcF"
            "BAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0"
            "dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDzOiiiv43P9BD/2Q==")
        self.write("maps/screen.jpg",jpeg)
        out,manifest=self.convert(self.write("modern.lwo",uv_textured()),code=2)
        directory,native=self.object_data(out,manifest)
        ref=native["image_references"][0]
        self.assertEqual(ref["resolution"],"unique-image-stem-suffix")
        self.assertTrue(ref["resolved_path"].endswith("screen.jpg"))
        self.assertEqual((directory/ref["uri"]).read_bytes(),jpeg)
        color=png(directory/native["materials"][0]["derived_maps"]["base_color"])[0][0]
        for actual,expected in zip(color,(200,80,20,255)): self.assertLessEqual(abs(actual-expected),2)

    def test_lwo2_scalar_replaces_base_and_color_drives_emission(self):
        self.write("maps/screen.iff",ilbm([[(0,0,0),(128,128,128),(255,255,255)]]))
        scalar=chunk("DIFF",F32(0)+vx(0),True)+chunk("LUMI",F32(1)+vx(0),True)+chunk("SPEC",F32(1)+vx(0),True)
        blocks=imap()+imap("SPEC")
        out,manifest=self.convert(self.write("modern.lwo",uv_textured(blocks,scalar)),code=2)
        directory,native=self.object_data(out,manifest); maps=native["materials"][0]["derived_maps"]
        self.assertEqual([c[3] for c in png(directory/maps["specular"])[0]],[0,128,255])
        self.assertEqual([c[0] for c in png(directory/maps["emissive"])[0]],[0,128,255])
        self.assertEqual([c[0] for c in png(directory/maps["base_color"])[0]],[0,0,0])
        data,_=gltf.load(out/manifest["assets"][0]["gltf"])
        self.assertIn("emissiveTexture",data["materials"][0])
        self.assertIn("specularTexture",data["materials"][0]["extensions"]["KHR_materials_specular"])

    def test_lwo2_unsupported_bindings_are_explicit(self):
        self.write("maps/screen.iff",ilbm([[(255,0,0)]]))
        cases=[(imap(enabled=0),"disabled"),(imap(uv="missing"),"TXUV"),
               (imap(clip=999),"unresolved"),(imap()+imap(),"compositing"),
               (imap()+imap(kind="PROC"),"compositing"),
               (imap(extra=chunk("PROJ",U16(0),True)),"projection"),
               (imap(header=chunk("OPAC",U16(3)+F32(1)+vx(0),True)),"blending"),
               (imap(header=chunk("OPAC",U16(0)+F32(1)+vx(7),True)),"animated"),
               (imap(extra=chunk("WRAP",U16(3)+U16(3),True)),"wrapping")]
        for blocks,issue in cases:
            with self.subTest(issue=issue):
                out,manifest=self.convert(self.write("modern.lwo",uv_textured(blocks)),code=2)
                _,native=self.object_data(out,manifest)
                m=native["materials"][0]
                self.assertEqual(m["derived_maps"],{})
                self.assertIn(issue,m["textures"][0]["issue"])
                data,_=gltf.load(out/manifest["assets"][0]["gltf"])
                self.assertNotIn("textures",data)
        # A disabled extra layer must not hide the enabled one.
        out,manifest=self.convert(self.write("modern.lwo",uv_textured(imap()+imap(enabled=0))),code=2)
        _,native=self.object_data(out,manifest)
        self.assertIn("base_color",native["materials"][0]["derived_maps"])

    def test_lwo2_invalid_clip_and_partial_uv_are_not_guessed(self):
        self.write("maps/screen.iff",ilbm([[(255,0,0)]]))
        clip=chunk("CLIP",struct.pack(">I",17)+chunk("STIL",s0("maps/screen.iff"),True))
        raw=uv_textured(clips=clip+clip)
        out,manifest=self.convert(self.write("modern.lwo",raw),code=2)
        _,native=self.object_data(out,manifest)
        self.assertIn("ambiguous CLIP",native["materials"][0]["textures"][0]["issue"])
        incomplete=chunk("VMAP",b"TXUV"+U16(2)+s0("atlas")+vx(0)+F32(0,0))
        out,manifest=self.convert(self.write("modern.lwo",uv_textured(maps=incomplete)),code=2)
        _,native=self.object_data(out,manifest)
        self.assertEqual(native["materials"][0]["derived_maps"],{})
        self.assertIn("incomplete",native["materials"][0]["textures"][0]["issue"])
        malformed=uv_textured(imap(extra=chunk("IMAG",b"\xff",True)))
        self.assertIn("at byte",self.run_cli("inspect",self.write("broken.lwo",malformed),code=1).stderr)

    def test_24bit_iff_png_pixels_padding_byte_runs_and_bindings(self):
        rows = [[(255,0,0),(0,255,0),(0,0,255)]*5+[(19,87,143),(1,2,3)],[(0,0,0)]*17]
        for compression in (0,1):
            with self.subTest(compression=compression):
                image = ilbm(rows,compression=compression)
                out,manifest,directory,native = self.export_image(image)
                ref = native["image_references"][0]
                self.assertEqual((directory/ref["uri"]).read_bytes(),image)
                self.assertEqual(ref["sha256"],hashlib.sha256(image).hexdigest())
                decoded = directory/ref["decoded_image"]["png_uri"]
                self.assertEqual(png(decoded),[[(*p,255) for p in row] for row in rows])
                self.assertEqual(ref["decoded_image"]["png_sha256"],hashlib.sha256(decoded.read_bytes()).hexdigest())
                binding = native["materials"][0]["textures"][0]
                self.assertEqual((binding["channel"],binding["image_reference"],binding["export_status"]),("COLR",0,"approximated"))
                self.assertEqual(manifest["image_references_converted_to_png"],1)
                self.assertEqual(manifest["assets"][0]["images_not_exported"],0)
                data,buffers = gltf.load(out/manifest["assets"][0]["gltf"])
                material = data["materials"][0]
                index = material["pbrMetallicRoughness"]["baseColorTexture"]["index"]
                uri = data["images"][data["textures"][index]["source"]]["uri"]
                self.assertEqual(png(out/"gltf"/uri),png(decoded))
                self.assertIn("map_Kd "+uri,(out/manifest["assets"][0]["mtl"]).read_text())
                self.assertEqual(png(out/"obj"/uri),png(decoded))

    def test_palette_transparent_index_and_ehb(self):
        colors = bytes([240,120,60, 10,20,30])
        image = ilbm([[0,1,0]],planes=1,mask=2,transparent=1,palette=colors)
        out,manifest,directory,native = self.export_image(image)
        decoded = native["image_references"][0]["decoded_image"]
        self.assertEqual(png(directory/decoded["png_uri"]),[[(240,120,60,255),(10,20,30,0),(240,120,60,255)]])
        data,_ = gltf.load(out/manifest["assets"][0]["gltf"])
        self.assertEqual(data["materials"][0]["alphaMode"],"BLEND")
        # Extra Half-Brite uses the first 32 colors, halved for bitplane six.
        _,_,directory,native = self.export_image(ilbm([[0,32]],planes=6,camg=0x80,palette=colors+bytes(90)))
        self.assertEqual(png(directory/native["image_references"][0]["decoded_image"]["png_uri"]),[[(240,120,60,255),(120,60,30,255)]])

    def test_mask_plane_and_32bit_alpha(self):
        row = [(5,100,240,255),(90,12,3,0)]
        for planes,mask in ((24,1),(32,0)):
            with self.subTest(planes=planes,mask=mask):
                _,_,directory,native = self.export_image(ilbm([row],planes=planes,mask=mask))
                self.assertEqual(png(directory/native["image_references"][0]["decoded_image"]["png_uri"]),[row])

    def test_legacy_form_size_excludes_final_body_padding(self):
        # maptransp.IFF uses one pad byte shared by the BODY and FORM containers.
        original = ilbm([[(17,45,203)]])
        body = original.index(b"BODY")
        size = struct.unpack_from(">I",original,body+4)[0]
        payload = original[body+8:body+8+size]
        if not len(payload)%2: payload = b"\x80"+payload
        image = form("ILBM",original[12:body],chunk("BODY",payload))
        self.assertEqual(struct.unpack_from(">I",image,body+4)[0]%2,1)
        image = image[:4]+struct.pack(">I",len(image)-9)+image[8:]
        out,manifest,directory,native = self.export_image(image)
        self.assertEqual(png(directory/native["image_references"][0]["decoded_image"]["png_uri"]),[[(17,45,203,255)]])
        self.assertEqual(manifest["assets"][0]["images_not_exported"],0)

    def test_invalid_iff_is_archived_without_a_false_binding(self):
        good = ilbm([[(255,0,0),(0,255,0)]])
        body = good.index(b"BODY")+8
        malformed = [good[:body],good[:-2],good[:body]+b"\x7f"+good[body+1:]]
        # Unsupported HAM and foreign IFF forms are explicit decode failures.
        malformed += [ilbm([[1,2]],planes=6,camg=0x800), good[:8]+b"ANIM"+good[12:]]
        for image in malformed:
            with self.subTest(image=image[-10:]):
                out,manifest,directory,native = self.export_image(image)
                ref = native["image_references"][0]
                self.assertEqual((directory/ref["uri"]).read_bytes(),image)
                self.assertIsNone(ref["decoded_image"])
                self.assertTrue(ref["decode_issue"])
                self.assertEqual(native["materials"][0]["derived_maps"],{})
                data,_ = gltf.load(out/manifest["assets"][0]["gltf"])
                self.assertNotIn("textures",data)

    def test_planar_projection_coordinates_and_native_parameters(self):
        blocks = texture(flags=1,extra=chunk("TSIZ",F32(2,4,6),True)+chunk("TCTR",F32(10,20,30),True))
        points = [(10,18,27),(10,18,33),(10,22,27)]
        out,manifest,_,native = self.export_image(ilbm([[(255,255,255)]]),blocks,points)
        t = native["materials"][0]["textures"][0]
        self.assertEqual(t["size"],[2,4,6]); self.assertEqual(t["center"],[10,20,30])
        data,buffers = gltf.load(out/manifest["assets"][0]["gltf"])
        p = data["meshes"][0]["primitives"][0]
        uv = gltf.values(data,buffers,p["attributes"]["TEXCOORD_0"])
        expected = [(0,1),(1,1),(0,0)]
        self.assertEqual(uv,[expected[point] for _,_,point in gltf.source_map(p,buffers)])
        obj = (out/manifest["assets"][0]["obj"]).read_text().splitlines()
        self.assertEqual([tuple(map(float,line.split()[1:])) for line in obj if line.startswith("vt ")],[(0,0),(1,0),(0,1)])

    def test_spherical_seam_is_unwrapped_before_repetition(self):
        blocks = texture(flags=2,projection="Spherical Image Map",extra=chunk("TFP0",F32(4),True)+chunk("TFP1",F32(2),True))
        points = [(.01,0,-1),(-.01,0,-1),(0,.2,-.97)]
        out,manifest,_,_ = self.export_image(ilbm([[(255,255,255)]]),blocks,points)
        data,buffers = gltf.load(out/manifest["assets"][0]["gltf"])
        p = data["meshes"][0]["primitives"][0]
        uv = gltf.values(data,buffers,p["attributes"]["TEXCOORD_0"])
        self.assertLess(max(u for u,v in uv)-min(u for u,v in uv),.02)
        self.assertAlmostEqual(sum(u for u,v in uv)/3,4,places=5)

    def test_scalar_transparency_is_inverted_once_for_obj_and_gltf(self):
        blocks = texture("TTEX",extra=chunk("TVAL",U16(256),True))
        out,manifest,directory,native = self.export_image(ilbm([[(0,0,0),(255,255,255)]]),blocks)
        maps = native["materials"][0]["derived_maps"]
        self.assertEqual([p[3] for p in png(directory/maps["base_color"])[0]],[255,0])
        self.assertEqual([p[0] for p in png(directory/maps["opacity"])[0]],[255,0])
        mtl = (out/manifest["assets"][0]["mtl"]).read_text()
        self.assertIn("map_d "+maps["opacity"],mtl)
        data,_ = gltf.load(out/manifest["assets"][0]["gltf"])
        self.assertEqual(data["materials"][0]["alphaMode"],"BLEND")
        self.assertEqual(data["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"][3],1)

    def test_channel_roles_specular_extension_and_unsupported_effects(self):
        blocks = texture("DTEX")+texture("LTEX")+texture("STEX")+texture("BTEX")
        blocks += chunk("BTEX",s0("Crumple"),True)+chunk("TAMP",F32(.25),True)
        out,manifest,directory,native = self.export_image(ilbm([[(0,0,0),(255,255,255)]]),blocks)
        material = native["materials"][0]
        self.assertEqual([t["channel"] for t in material["textures"]],["DIFF","LUMI","SPEC","BUMP","BUMP"])
        self.assertEqual(material["textures"][-1]["export_status"],"preserved-only")
        self.assertIsNone(material["textures"][-1]["image_reference"])
        data,_ = gltf.load(out/manifest["assets"][0]["gltf"])
        self.assertIn("KHR_materials_specular",data["extensionsUsed"])
        self.assertIn("emissiveTexture",data["materials"][0])
        self.assertEqual(set(material["derived_maps"]),{"base_color","emissive","specular","bump"})
        self.assertEqual(len(list(directory.glob("textures/*screen.iff.png"))),1)

    def test_incompatible_projection_and_explicit_uv_override_are_reported(self):
        blocks = texture()+texture("DTEX",flags=2)
        _,_,_,native = self.export_image(ilbm([[(0,0,0)]]),blocks)
        self.assertIn("incompatible projection",native["materials"][0]["textures"][1]["issue"])
        source = self.root/"mesh.lwo"
        out,manifest = self.convert(source,"--uv-map","explicit",code=2)
        _,native = self.object_data(out,manifest)
        self.assertEqual(native["materials"][0]["derived_maps"],{})
        self.assertIn("--uv-map",native["materials"][0]["textures"][0]["issue"])

    def test_batch_keeps_textures_after_work_and_sources_are_removed(self):
        image = ilbm([[(255,0,0),(0,255,0)],[(0,0,255),(255,255,255)]])
        self.write("project/maps/screen.iff",image)
        self.write("project/a/model.lwo",textured(texture(path="I:old/screen.psd")))
        # Only the owner's subtree is searched, so each nested owner gets a copy.
        self.write("project/a/maps/screen.iff",image)
        self.write("project/b/model.lwo",textured(texture(path="maps/screen.iff")))
        self.write("project/b/maps/screen.iff",image)
        self.write("project/test.lws","LWSC\n1\nLoadObject a/model.lwo\nLoadObject b/model.lwo\n")
        target = self.base/"batches"
        result = subprocess.run([sys.executable,"-X","utf8",str(ROOT/"tools/batch_convert.py"),"--content",str(self.root),"--output-root",str(target),"--converter",EXE],capture_output=True,text=True,encoding="utf-8",timeout=60)
        self.assertEqual(result.returncode,2,result.stdout+result.stderr)
        run = next(target.iterdir())
        self.assertFalse((run/".work").exists())
        # All three publications share a content-addressed texture in each format.
        project = run/"packages/project"
        self.assertEqual(len(list((project/"gltf/textures").iterdir())),1)
        self.assertEqual(len(list((project/"obj/textures").iterdir())),1)
        moved = self.base/"portable"
        shutil.copytree(project,moved)
        shutil.rmtree(self.root)
        for path in moved.glob("gltf/*.gltf"):
            data,_ = gltf.load(path)
            for ref in data["images"]: self.assertEqual(png(path.parent/ref["uri"])[0][0],(255,0,0,255))
        for path in moved.glob("obj/*.mtl"):
            for line in path.read_text().splitlines():
                if line.startswith("map_Kd "): self.assertTrue((path.parent/line.split()[1]).is_file())
        for path in moved.glob("IR/*/object.json"):
            data = json.loads(path.read_text())
            self.assertEqual((path.parent/data["image_references"][0]["uri"]).read_bytes(),image)
            self.assertEqual(png(path.parent/data["image_references"][0]["decoded_image"]["png_uri"])[0][0],(255,0,0,255))
            for uri in data["materials"][0]["derived_maps"].values(): self.assertTrue((path.parent/uri).is_file())

    def test_batch_texture_copy_rejects_escape_and_content_collision(self):
        source = self.root
        destination = self.base/"destination"; destination.mkdir()
        self.write("textures/not-a-hash.png",b"pixels")
        for uri in ("../escape.png","textures/../../escape.png","textures/not-a-hash.png"):
            with self.assertRaises(ValueError): copy_texture(source,uri,destination)


if __name__ == "__main__": unittest.main()
