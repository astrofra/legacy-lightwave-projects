"""Batch integration checks with real converter processes and isolated inputs."""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import unquote

EXE = str(Path(sys.argv.pop(1)).resolve())
REPOSITORY = Path(__file__).resolve().parents[1]
BATCH = REPOSITORY / "tools/batch_convert.py"
sys.path.insert(0, str(REPOSITORY / "tools"))
from output_layout import new_run


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
        for obj in (run / "packages").glob("*/obj/*.obj"):
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
            self.source("project/" + name, ("LWSC\n1\nLoadObject mesh.lwo\n" + still + still).encode())
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
        self.assertEqual(scene["obj"], "packages/project_A/obj/scene.lws.obj")
        self.check_published_files(report_path)
        for path, checksum in before.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), checksum)

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
        self.assertEqual({p.name for p in (package / "obj").glob("*.obj")},
                         {"0.lws.obj", "1.lws.obj", "door.lwo.obj", "door.lwo-3.obj", "Door.lwo-4.obj", "door.lwo-2.obj", "é_!__.lwo.obj", "é_!__.lwo-2.obj"})
        self.assertEqual(len(list((package / "IR").glob("*/object.json"))), 6)
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

    def test_mapped_dependencies_share_the_collision_namespace(self):
        self.source("project/local/mesh.lwo", object_bytes())
        external = self.base / "external"
        external.mkdir()
        (external / "mesh.lwo").write_bytes(object_bytes(7))
        self.source("project/0.lws", b"LWSC\n1\nLoadObject outside:mesh.lwo\nLoadObject local/mesh.lwo\n")
        self.source("project/1.lws", b"LWSC\n1\nLoadObject local/mesh.lwo\nLoadObject outside:mesh.lwo\n")
        self.run_batch("--map", f"outside:={external}")
        report_path = self.reports()[0]
        self.check_published_files(report_path)
        ir = report_path.parent / "packages/project/IR"
        self.assertEqual((ir / "mesh.lwo/source.bin").read_bytes(), object_bytes())
        self.assertEqual((ir / "mesh.lwo-2/source.bin").read_bytes(), object_bytes(7))
        self.assertEqual(len(list(ir.glob("*/object.json"))), 2)

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
