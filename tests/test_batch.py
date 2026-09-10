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

EXE = str(Path(sys.argv.pop(1)).resolve())
REPOSITORY = Path(__file__).resolve().parents[1]
BATCH = REPOSITORY / "tools/batch_convert.py"


def form(kind, payload):
    body = kind + payload
    return b"FORM" + struct.pack(">I", len(body)) + body


def object_bytes():
    points = struct.pack(">9f", 0,0,0, 1,0,0, 0,1,0)
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
        self.assertTrue((report_path.parent/scene["package"]/"scene.obj").is_file())
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
