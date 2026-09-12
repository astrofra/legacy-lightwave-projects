"""Batch integration checks with real converter processes and isolated inputs."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from urllib.parse import unquote

EXE = str(Path(sys.argv.pop(1)).resolve())
REPOSITORY = Path(__file__).resolve().parents[1]
BATCH = REPOSITORY / "tools/batch_convert.py"
sys.path.insert(0, str(REPOSITORY / "tools"))
from output_layout import new_run, output_name
import batch_convert


def form(kind, payload):
    body = kind + payload
    return b"FORM" + struct.pack(">I", len(body)) + body


def object_bytes(offset=0):
    points = struct.pack(">9f", offset,0,0, offset+1,0,0, offset,1,0)
    return form(b"LWOB", b"PNTS"+struct.pack(">I",len(points))+points+b"POLS"+struct.pack(">I5H",10,3,0,1,2,0))


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lw batch é ! ")
        self.base = Path(self.temp.name)
        self.content = self.base / "content"
        self.content.mkdir()
        self.output = self.base / "output"

    def tearDown(self):
        self.temp.cleanup()

    def source(self, relative, data):
        path = self.content / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def run_batch(self, *extra, code=0, launcher=False):
        command = [str(REPOSITORY / "convert_content.bat")] if launcher else [sys.executable, "-X", "utf8", str(BATCH)]
        command += ["--content", str(self.content), "--output-root", str(self.output), "--converter", EXE, *extra]
        result = subprocess.run(command, cwd=self.base, capture_output=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, code, result.stdout+result.stderr)
        return result

    def reports(self):
        return sorted(self.output.glob("batch-*/batch-report.json"))

    def check_published_files(self, report_path):
        run = report_path.parent
        report = json.loads(report_path.read_text("utf-8"))
        self.assertFalse((run / ".work").exists())
        for record in report["files"]:
            if record["status"] not in ("converted", "partial"):
                continue
            package = run / record["package"]
            manifest_path = run / record["manifest"]
            manifest = json.loads(manifest_path.read_text("utf-8"))
            index = json.loads((package / "manifest.json").read_text("utf-8"))
            self.assertEqual(report["layout_version"], "0.3")
            self.assertEqual(index["layout_version"], "0.3")
            self.assertEqual(manifest["layout_version"], "0.3")
            self.assertIn(manifest_path.relative_to(package).as_posix(), [entry["manifest"] for entry in index["conversions"]])
            self.assertEqual(index["formats"]["gltf"], "generated")
            for planned in ("blender",):
                self.assertEqual(index["formats"][planned], "not-implemented")
                self.assertEqual(list((package / planned).iterdir()), [])
            uris = [asset["uri"] for asset in manifest["assets"]]
            if manifest["scene"]:
                uris.append(manifest["scene"])
            for uri in uris:
                path = (manifest_path.parent / uri).resolve()
                self.assertTrue(path.is_relative_to(package))
                data = json.loads(path.read_text("utf-8"))
                for reference in data.get("image_references", []):
                    if reference.get("uri"):
                        image = (path.parent / reference["uri"]).resolve()
                        self.assertTrue(image.is_relative_to(package))
                        self.assertEqual(hashlib.sha256(image.read_bytes()).hexdigest(), reference["sha256"])
                self.assertEqual(hashlib.sha256((path.parent / data["source"]["uri"]).read_bytes()).hexdigest(), data["source"]["sha256"])
                buffer = data.get("buffer", data.get("animation_buffer"))
                if buffer:
                    self.assertTrue((path.parent / buffer["uri"]).is_file())
            for asset in manifest["assets"]:
                self.assertTrue((manifest_path.parent / asset["obj"]).is_file())
                self.assertTrue((manifest_path.parent / asset["mtl"]).is_file())
            if record["obj"]:
                self.assertTrue((run / record["obj"]).is_file())
            if record["gltf"]:
                self.assertTrue((run / record["gltf"]).is_file())
            for uri, binary in [(a["gltf"], a["gltf_bin"]) for a in manifest["assets"]] + ([(manifest["scene_gltf"], manifest["scene_gltf_bin"])] if manifest["scene_gltf"] else []):
                path = manifest_path.parent / uri
                data = json.loads(path.read_text("utf-8"))
                for buffer in data.get("buffers", []):
                    target = (path.parent / unquote(buffer["uri"])).resolve()
                    self.assertEqual(target, (manifest_path.parent / binary).resolve())
                    self.assertEqual(target.stat().st_size, buffer["byteLength"])
        for obj in (run / "packages").glob("*/obj/**/*.obj"):
            lines = obj.read_text("utf-8").splitlines()
            mtl = obj.parent / next(line.split()[1] for line in lines if line.startswith("mtllib "))
            self.assertEqual(mtl, obj.with_suffix(".mtl"))
            materials = {line.split()[1] for line in mtl.read_text("utf-8").splitlines() if line.startswith("newmtl ")}
            self.assertTrue({line.split()[1] for line in lines if line.startswith("usemtl ")} <= materials)
        return report

    def test_image_copies_survive_batch_publication(self):
        self.source("project/mesh.lwo", object_bytes())
        still = '{ Clip\n{ Still\n"I:old/signe.psd"\n}\n}\n'
        for name in ("01.lws", "02.lws"):
            self.source("project/" + name, ("LWSC\n1\nLoadObject mesh.lwo\nClipMaps\n{ TextureBlock\n" + still + still + "}\n").encode())
        self.source("project/signe.jpg", b"jpeg bytes")
        self.run_batch(code=2)
        report_path = self.reports()[0]
        report = self.check_published_files(report_path)
        for record in report["files"]:
            if not record["source"].endswith(".lws"):
                continue
            directory = (report_path.parent / record["manifest"]).parent
            data = json.loads((directory / "scene.json").read_text("utf-8"))
            ref = data["image_references"][0]
            self.assertEqual(data["nodes"][0]["clip_maps"][0]["image_references"], [0, 1])
            self.assertEqual(ref["role"], "clip-map")
            self.assertEqual(ref["resolution"], "unique-image-stem")
            self.assertEqual((directory / ref["uri"]).read_bytes(), b"jpeg bytes")
            self.assertEqual(len(list((directory / "textures").iterdir())), 1)

    def test_signatures_extensionless_files_and_project_roots(self):
        a = self.source("project A/mesh ! é", object_bytes())
        self.source("project B/mesh ! é", object_bytes())
        self.source("project A/nested/scene.lws", "LWSC\n1\nLoadObject old:mesh ! é\n".encode())
        self.source("project A/surface", form(b"PST_", b"PDAT"+struct.pack(">I",12)+form(b"LWO2", b"")))
        self.source("project A/fake.lwo", b"JPEG placeholder")
        self.source("project A/backup.zip", b"PK\x03\x04")
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.content.rglob("*") if p.is_file()}
        self.run_batch()
        report_path = self.reports()[0]
        report = json.loads(report_path.read_text("utf-8"))
        self.assertEqual(report["counts"], {"converted":4,"skipped":2})
        scene = next(r for r in report["files"] if r["source"].endswith("scene.lws"))
        self.assertEqual(Path(scene["content_root"]), a.parent)
        self.assertEqual(scene["obj"], "packages/project_A/obj/nested/scene.lws.obj")
        mesh = next(r for r in report["files"] if r["source"] == "project A/mesh ! é")
        self.assertEqual(mesh["obj"], "packages/project_A/obj/mesh_!_é.lwo.obj")
        self.assertEqual(mesh["gltf"], "packages/project_A/gltf/mesh_!_é.lwo.gltf")
        preset = next(r for r in report["files"] if r["source"] == "project A/surface")
        self.assertEqual(preset["obj"], "packages/project_A/obj/surface.obj")
        self.check_published_files(report_path)
        for path, checksum in before.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), checksum)

    def test_extensionless_amiga_scene_with_implicit_camera_is_published(self):
        self.source("aminet-atmobjs/Station1",object_bytes())
        motion="  9\n  1\n  0 0 0 0 0 0 1 1 1\n  0 0 0 0 0\nEndBehavior 1\n"
        raw="LWSC\n1\nLoadObject HD1:atm/space/Station1\nObjectMotion (unnamed)\n"+motion
        raw+="AddLight\nLightName Sun\nLightMotion (unnamed)\n"+motion
        raw+="CameraMotion (unnamed)\n"+motion+"ShowCamera 1\n"
        original=self.source("aminet-atmobjs/NastyStation",raw.encode())
        self.run_batch()
        report_path=self.reports()[0]; report=self.check_published_files(report_path)
        self.assertEqual(report["counts"],{"converted":2})
        record=next(r for r in report["files"] if r["source"].endswith("NastyStation"))
        self.assertEqual(record["gltf"],"packages/aminet-atmobjs/gltf/NastyStation.lws.gltf")
        self.assertEqual(record["obj"],"packages/aminet-atmobjs/obj/NastyStation.lws.obj")
        manifest_path=report_path.parent/record["manifest"]; manifest=json.loads(manifest_path.read_text())
        scene_path=manifest_path.parent/manifest["scene"]; scene=json.loads(scene_path.read_text())
        self.assertEqual([n["id"] for n in scene["nodes"]],[0x10000000,0x20000000,0x30000000])
        self.assertEqual((scene_path.parent/"source.bin").read_bytes(),original.read_bytes())

    def test_rerun_creates_new_outputs_without_overwriting(self):
        self.source("project/object", object_bytes())
        self.run_batch()
        first = self.reports()[0]
        original = first.read_bytes()
        self.run_batch()
        self.assertEqual(len(self.reports()), 2)
        self.assertEqual(first.read_bytes(), original)

    def test_timestamp_suffix_only_on_collision(self):
        self.output.mkdir()
        first = new_run(self.output, "batch-20260910-180051")
        (first / "keep.txt").write_text("preserved")
        second = new_run(self.output, "batch-20260910-180051")
        third = new_run(self.output, "batch-20260910-180051")
        self.assertEqual(first.name, "batch-20260910-180051")
        self.assertEqual(second.name, "batch-20260910-180051-2")
        self.assertEqual(third.name, "batch-20260910-180051-3")
        self.assertEqual((first / "keep.txt").read_text(), "preserved")

    def test_project_collisions_and_shared_scene_assets(self):
        sources = ["a/door.lwo", "b/door.lwo", "c/Door.lwo", "d/door.lwo-2", "e/é ! #.lwo", "f/é_!__.lwo"]
        for i, name in enumerate(sources):
            self.source("project/" + name, object_bytes(i))
        scene = ("LWSC\n1\n" + "".join(f"LoadObject {name}\n" for name in sources)).encode()
        self.source("project/0.lws", scene)
        self.source("project/1.lws", scene)
        self.run_batch()
        report_path = self.reports()[0]
        report = self.check_published_files(report_path)
        package = report_path.parent / "packages/project"
        self.assertEqual({p.relative_to(package / "obj").as_posix() for p in (package / "obj").rglob("*.obj")},
                         {"0.lws.obj", "1.lws.obj", *(str(Path(name).parent / (output_name(Path(name).name) + ".obj")).replace("\\", "/") for name in sources)})
        self.assertEqual(len(list((package / "IR").rglob("object.json"))), 6)
        self.assertEqual(report["counts"], {"converted":8})
        paths = {}
        for record in report["files"]:
            manifest_path = report_path.parent / record["manifest"]
            manifest = json.loads(manifest_path.read_text("utf-8"))
            for asset in manifest["assets"]:
                uri = (manifest_path.parent / asset["uri"]).resolve()
                self.assertEqual(paths.setdefault(asset["source_path"], uri), uri)

    def test_loose_root_input_and_project_name_collision(self):
        self.source("mesh.lwo", object_bytes())
        self.source("content/mesh.lwo", object_bytes(2))
        self.run_batch()
        report = self.check_published_files(self.reports()[0])
        self.assertEqual({r["package"] for r in report["files"]}, {"packages/content", "packages/content-2"})

    def test_project_selection_keeps_nested_files_in_one_project(self):
        self.source("quatuor/01.lws", b"LWSC\n1\n")
        self.source("quatuor/work/3d/01.lws", b"LWSC\n1\n")
        self.source("unselected/invalid.lws", b"LWSC\n99\n")
        self.run_batch("--project", "quatuor")
        report = self.check_published_files(self.reports()[0])
        self.assertEqual(report["counts"], {"converted": 2})
        self.assertEqual({r["package"] for r in report["files"]}, {"packages/quatuor"})
        self.assertEqual({r["gltf"] for r in report["files"]},
                         {"packages/quatuor/gltf/01.lws.gltf", "packages/quatuor/gltf/work/3d/01.lws.gltf"})
        for name in ("missing", "../quatuor", "quatuor/work", ""):
            self.run_batch("--project", name, code=1)
        self.assertEqual(len(self.reports()), 1)

    def test_file_selection_keeps_project_root_and_object_dependencies(self):
        self.source("project/Objects/body.lwo",object_bytes())
        self.source("project/Scenes/shot.lws",b"LWSC\n3\nLoadObject Objects/body.lwo\n")
        self.source("project/Scenes/invalid.lws",b"LWSC\n99\n")
        self.run_batch("--file","project/Scenes/shot.lws","--skin-profile","lightwave6")
        path=self.reports()[0]; report=self.check_published_files(path)
        self.assertEqual(len(report["files"]),1)
        record=report["files"][0]
        self.assertEqual(Path(record["content_root"]),self.content/"project")
        manifest=json.loads((path.parent/record["manifest"]).read_text())
        self.assertEqual(manifest["skin_profile"],"lightwave6")
        self.assertEqual(manifest["unresolved_object_instances"],0)
        self.assertEqual(record["native_animation"]["status"],"not-requested")
        for name in ("../outside.lws","project/Scenes/missing.lws",str(self.content/"project/Scenes/shot.lws")):
            self.run_batch("--file",name,code=1)
        self.assertEqual(len(self.reports()),1)

    def test_native_lw6_options_reach_evaluator_and_use_matching_c_weights(self):
        self.source("project/shot.lws",b"LWSC\n3\n")
        arguments=["--content",str(self.content),"--output-root",str(self.output),"--converter",EXE,
                   "--runtime","lightwave6","--lightwave-root",str(self.base/"LW6"),
                   "--capture-plugin",str(self.base/"x86.p"),"--animation-mode","skin",
                   "--skip-plugin","JointMorph","--skip-plugin","LW_MorphMixer",
                   "--animation-start","0","--animation-end","40"]
        def evaluate(package,*args,**kwargs):
            manifest=json.loads((package/"manifest.json").read_text())
            self.assertEqual(manifest["skin_profile"],"lightwave6")
            self.assertEqual(manifest["gltf_rig_policy"],"all")
            self.assertEqual(args[2:5],(0,40,1))
            self.assertEqual(kwargs,{"runtime":"lightwave6","skip_plugins":["JointMorph","LW_MorphMixer"],"animation_mode":"skin"})
            return []
        for explicit in (True,False):
            selected=arguments[:] if explicit else arguments[:6]+arguments[8:]
            with mock.patch("export_lightwave_animation.evaluate_package",side_effect=evaluate) as evaluator:
                self.assertEqual(batch_convert.main(selected),0)
                evaluator.assert_called_once()
            report=json.loads(self.reports()[-1].read_text())
            self.assertEqual(report["options"]["skin_profile"],"lightwave6" if explicit else "auto")
            self.assertEqual(report["options"]["runtime"],"lightwave6" if explicit else "auto")
            record=report["files"][0]
            self.assertEqual(record["skin_profile"],"lightwave6")
            self.assertEqual(record["native_animation"],{"status":"not-needed","runtime":"lightwave6"})
        modern=arguments[:]
        modern[modern.index("--runtime")+1]="lightwave96"
        modern += ["--skin-profile","auto"]
        def evaluate_modern(package,*args,**kwargs):
            manifest=json.loads((package/"manifest.json").read_text())
            self.assertEqual(manifest["skin_profile"],"lightwave96")
            self.assertEqual(manifest["skin_profile_policy"],"explicit")
            self.assertEqual(kwargs["runtime"],"lightwave96")
            return []
        with mock.patch("export_lightwave_animation.evaluate_package",side_effect=evaluate_modern) as evaluator:
            self.assertEqual(batch_convert.main(modern),0)
            evaluator.assert_called_once()
        with mock.patch("export_lightwave_animation.evaluate_package",side_effect=ValueError("capture failed")):
            self.assertEqual(batch_convert.main(arguments),2)
        failed=[json.loads(p.read_text()) for p in self.reports() if json.loads(p.read_text())["exit_code"]==2][0]
        self.assertEqual(failed["files"][0]["native_animation"]["status"],"failed")
        self.assertEqual(failed["files"][0]["animation_issue"],"capture failed")

    def test_native_option_validation_happens_before_batch_creation(self):
        for args in (("--runtime","lightwave6"),("--skip-plugin","JointMorph"),("--animation-mode","skin"),
                     ("--lightwave-root",str(self.base),"--runtime","lightwave6","--skin-profile","lightwave96"),
                     ("--lightwave-root",str(self.base),"--runtime","lightwave96","--skin-profile","lightwave6")):
            self.run_batch(*args,code=1)
        self.assertFalse(self.output.exists())

    def test_oldest_profile_is_per_file_and_explicit_choices_are_not_saved(self):
        sources={f"project/v{v}.lws":f"LWSC\n{v}\n".encode() for v in (1,3,5)}
        for name,data in sources.items(): self.source(name,data)
        # A forced profile on one run must not become a project preset.
        for explicit in (False,True,False):
            self.run_batch(*(["--skin-profile","lightwave96"] if explicit else []),code=2)
            path=self.reports()[-1]; report=self.check_published_files(path)
            for record in report["files"]:
                version=int(Path(record["source"]).stem[1:])
                expected="lightwave96" if explicit or version==5 else "lightwave6"
                manifest=json.loads((path.parent/record["manifest"]).read_text())
                self.assertEqual(manifest["skin_profile"],expected)
                self.assertEqual(record["skin_profile"],expected)
                self.assertEqual(record["skin_profile_policy"],"explicit" if explicit else "oldest-supported-for-file")
                self.assertEqual((path.parent/record["manifest"]).with_name("source.bin").read_bytes(),sources[record["source"]])
        self.assertEqual({p.relative_to(self.content).as_posix():p.read_bytes() for p in self.content.rglob("*") if p.is_file()},sources)

    def test_directory_and_inferred_filename_collisions_preserve_sources(self):
        sources = {"a b/mesh.lwo": "a_b/mesh.lwo", "a_b/mesh.lwo": "a_b-3/mesh.lwo",
                   "a_b-2/mesh.lwo": "a_b-2/mesh.lwo", "mesh": "mesh.lwo-3",
                   "mesh.lwo-2": "mesh.lwo-2", "mesh.lwo/inner.lwo": "mesh.lwo/inner.lwo",
                   "other.lwo": "other.lwo-2", "other.lwo.gltf/inner.lwo": "other.lwo.gltf/inner.lwo"}
        for index, source in enumerate(sources):
            self.source("project/" + source, object_bytes(index))
        self.run_batch()
        report_path = self.reports()[0]
        self.check_published_files(report_path)
        project = report_path.parent / "packages/project"
        for index, (source, destination) in enumerate(sources.items()):
            self.assertEqual((project / "IR" / destination / "source.bin").read_bytes(), object_bytes(index), source)
            self.assertTrue((project / "gltf" / (destination + ".gltf")).is_file())
            self.assertTrue((project / "obj" / (destination + ".obj")).is_file())

    def test_nested_textures_and_scene_dependencies_survive_relocation(self):
        def chunk(tag, payload, small=False):
            return tag + struct.pack(">H" if small else ">I", len(payload)) + payload + b"\0" * (len(payload) % 2)

        texture = chunk(b"CTEX", b"Planar Image Map\0", True) + chunk(b"TIMG", b"maps/screen.tga\0", True)
        texture += chunk(b"TFLG", struct.pack(">H", 4), True) + chunk(b"TSIZ", struct.pack(">3f", 1, 1, 1), True)
        mesh = object_bytes()[12:-2] + struct.pack(">H", 1)
        mesh += chunk(b"SRFS", b"paint\0") + chunk(b"SURF", b"paint\0" + texture)
        self.source("project/objects/details/mesh.lwo", form(b"LWOB", mesh))
        self.source("project/objects/details/maps/screen.tga", struct.pack("<BBBHHBHHHHBB", 0,0,2,0,0,0,0,0,1,1,24,32) + b"\x10\x40\xc0")
        for name in ("main.lws", "work/scenes/main.lws"):
            self.source("project/" + name, b"LWSC\n1\nLoadObject objects/details/mesh.lwo\n")
        self.run_batch(code=2)
        original = self.reports()[0].parent
        relocated = self.base / "relocated project"
        shutil.copytree(original, relocated)
        self.check_published_files(relocated / "batch-report.json")
        project = relocated / "packages/project"
        for name in ("main.lws", "work/scenes/main.lws", "objects/details/mesh.lwo"):
            path = project / "gltf" / (name + ".gltf")
            data = json.loads(path.read_text("utf-8"))
            self.assertEqual(len(data["images"]), 1)
            uri = data["images"][0]["uri"]
            image = path.parent / unquote(uri)
            self.assertTrue(image.resolve().is_relative_to(relocated))
            self.assertEqual(hashlib.sha256(image.read_bytes()).hexdigest(), image.stem)
            obj = project / "obj" / (name + ".obj")
            self.assertIn("map_Kd " + uri, obj.with_suffix(".mtl").read_text("utf-8"))
            self.assertEqual((obj.parent / uri).read_bytes(), image.read_bytes())
        self.assertEqual(len(list((project / "IR").rglob("object.json"))), 1)

    def test_mapped_dependencies_use_an_external_namespace(self):
        self.source("project/local/mesh.lwo", object_bytes())
        self.source("project/_external/mesh.lwo", object_bytes(1))
        external = self.base / "external"
        external.mkdir()
        (external / "mesh.lwo").write_bytes(object_bytes(7))
        (external / "nested").mkdir()
        (external / "nested/mesh.lwo").write_bytes(object_bytes(8))
        self.source("project/0.lws", b"LWSC\n1\nLoadObject outside:mesh.lwo\nLoadObject local/mesh.lwo\nLoadObject other:mesh.lwo\n")
        self.source("project/1.lws", b"LWSC\n1\nLoadObject local/mesh.lwo\nLoadObject other:mesh.lwo\nLoadObject outside:mesh.lwo\n")
        self.run_batch("--map", f"outside:={external}", "--map", f"other:={external / 'nested'}")
        report_path = self.reports()[0]
        self.check_published_files(report_path)
        ir = report_path.parent / "packages/project/IR"
        self.assertEqual((ir / "local/mesh.lwo/source.bin").read_bytes(), object_bytes())
        self.assertEqual((ir / "_external/mesh.lwo/source.bin").read_bytes(), object_bytes(1))
        self.assertEqual((ir / "_external-2/mesh.lwo/source.bin").read_bytes(), object_bytes(7))
        self.assertEqual((ir / "_external-2/mesh.lwo-2/source.bin").read_bytes(), object_bytes(8))
        self.assertEqual(len(list(ir.rglob("object.json"))), 4)

    def test_extensionless_scenes_and_mapped_object_collisions(self):
        self.source("project/a/mesh", object_bytes())
        self.source("project/b/mesh.lwo", object_bytes(1))
        self.source("project/c/mesh.lwo-2", object_bytes(2))
        external = self.base / "external"
        external.mkdir()
        (external / "mesh").write_bytes(object_bytes(3))
        scene = b"LWSC\n1\nLoadObject a/mesh\nLoadObject b/mesh.lwo\nLoadObject c/mesh.lwo-2\nLoadObject outside:mesh\n"
        self.source("project/mesh", scene)
        self.source("project/mesh.lws", scene)
        self.run_batch("--map", f"outside:={external}")
        report_path = self.reports()[0]
        self.check_published_files(report_path)
        package = report_path.parent / "packages/project"
        names = {"a/mesh.lwo", "b/mesh.lwo", "c/mesh.lwo-2", "_external/mesh.lwo", "mesh.lws", "mesh.lws-2"}
        self.assertEqual({p.relative_to(package / "obj").with_suffix("").as_posix() for p in (package / "obj").rglob("*.obj")}, names)
        self.assertEqual({p.relative_to(package / "gltf").with_suffix("").as_posix() for p in (package / "gltf").rglob("*.gltf")}, names)
        self.assertEqual((package / "IR/a/mesh.lwo/source.bin").read_bytes(), object_bytes())
        self.assertEqual((package / "IR/_external/mesh.lwo/source.bin").read_bytes(), object_bytes(3))
        self.assertEqual((package / "IR/mesh.lws/source.bin").read_bytes(), scene)
        self.assertEqual(len(list((package / "IR").rglob("object.json"))), 4)

    def test_batch_keeps_scene_animation_and_shared_buffer_links(self):
        self.source("project/object", object_bytes())
        scene = ("LWSC\n1\nFirstFrame 0\nLastFrame 10\nFramesPerSecond 5\n"
                 "LoadObject object\nObjectMotion\n9\n2\n"
                 "0 0 0 0 0 0 1 1 1\n0 1 0 0 0\n"
                 "10 0 0 0 0 0 1 1 1\n10 1 0 0 0\nEndBehavior 1\n")
        self.source("project/moving", scene.encode())
        self.run_batch()
        report_path = self.reports()[0]
        report = self.check_published_files(report_path)
        record = next(r for r in report["files"] if r["source"] == "project/moving")
        self.assertEqual(record["gltf"], "packages/project/gltf/moving.lws.gltf")
        data = json.loads((report_path.parent / record["gltf"]).read_text("utf-8"))
        self.assertEqual(len(data["animations"]), 1)
        self.assertEqual(len(data["animations"][0]["channels"]), 3)
        self.assertEqual(data["buffers"][0]["uri"], "moving.lws.bin")
        manifest = json.loads((report_path.parent / record["manifest"]).read_text("utf-8"))
        self.assertEqual(manifest["gltf_animation_samples"], 11)

    def test_errors_do_not_abort_other_files(self):
        self.source("project/a-bad.lws", b"LWSC\n99\n")
        self.source("project/b-good", object_bytes())
        self.run_batch(code=1)
        report_path = self.reports()[0]
        report = json.loads(report_path.read_text("utf-8"))
        self.assertEqual(report["counts"], {"converted":1,"failed":1})
        bad = next(r for r in report["files"] if r["status"] == "failed")
        self.assertIn("only LWSC versions", (report_path.parent/bad["log"]).read_text("utf-8"))

    def test_partial_is_distinct_from_failure(self):
        self.source("project/missing.lws", b"LWSC\n1\nLoadObject absent\n")
        self.run_batch(code=2)
        report = json.loads(self.reports()[0].read_text("utf-8"))
        self.assertEqual(report["counts"], {"partial":1})
        self.assertEqual(report["exit_code"], 2)

    def test_dry_run_creates_no_output(self):
        self.source("project/object", object_bytes())
        result = self.run_batch("--dry-run")
        self.assertIn("1 supported LightWave files", result.stdout)
        self.assertFalse(self.output.exists())

    def test_invalid_destination_and_missing_converter(self):
        self.run_batch("--output-root", str(self.content/"generated"), code=1)
        self.run_batch("--converter", str(self.base/"missing.exe"), code=1)
        self.assertFalse((self.content/"generated").exists())
        self.assertFalse(self.output.exists())

    @unittest.skipUnless(os.name == "nt", "Windows launcher")
    def test_bat_from_another_directory_and_exit_code(self):
        self.source("project/missing.lws", b"LWSC\n1\nLoadObject absent\n")
        self.run_batch(code=2, launcher=True)
        self.assertEqual(json.loads(self.reports()[0].read_text("utf-8"))["exit_code"], 2)


if __name__ == "__main__":
    unittest.main()
