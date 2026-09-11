"""Offline checks for manifest recovery, integrity and extraction failures."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import download_aminet as aminet


def record(name="example.lha", payload=b"archive"):
    return {"archive": name, "download_url": f"https://aminet.net/pix/3dobj/{name}",
            "sha256": hashlib.sha256(payload).hexdigest(), "archive_bytes": len(payload)}


class AminetTests(unittest.TestCase):
    def test_recover_download_header_without_nested_urls(self):
        first = record()
        first["scenes"] = [{"download_url": "https://example.org/unrelated.lha"}]
        second = record("second.lha")
        truncated = ('{"records": [' + json.dumps(first) + ", "
                     + json.dumps(second)[:-1] + ', "scenes": [{"unfinished": "cut')
        self.assertEqual(aminet.recover_records(truncated), [first, second])

    def test_incomplete_download_header_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "incomplete download header"):
            aminet.recover_records('{"records": [{"archive": "example.lha", "download_url": "cut')

    def test_invalid_json_requires_explicit_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "manifest.json"
            manifest.write_text('{"records": [' + json.dumps(record()), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "recover-truncated-manifest"):
                aminet.load_records(manifest)
            with contextlib.redirect_stderr(io.StringIO()) as warnings:
                self.assertEqual(aminet.load_records(manifest, recover=True), [record()])
            self.assertIn("recovered 1", warnings.getvalue())

    def test_case_insensitive_destination_collision_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "manifest.json"
            manifest.write_text(json.dumps({"records": [record(), record("EXAMPLE.LHA")]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate destination"):
                aminet.load_records(manifest)

    def test_paths_cannot_escape_or_alias_windows_files(self):
        for name in ("../escape", "dir/../../escape", "C:\\escape", "\\\\server\\file",
                     "/absolute", "dir\\..\\escape", "file:stream", "CON.txt", "file. "):
            with self.subTest(name=name), self.assertRaises(ValueError):
                aminet.validate_path(name)
        aminet.validate_path("Objects/Space/Some object.lwo")

    def test_corrupt_cache_is_replaced_only_after_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            archive = cache / "example.lha"
            archive.write_bytes(b"damaged")  # Same size; only the digest detects this.
            response = io.BytesIO(b"archive")
            response.url = record()["download_url"]
            with patch.object(aminet, "urlopen", return_value=response), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(aminet.download(record(), cache), archive)
            self.assertEqual(archive.read_bytes(), b"archive")
            self.assertEqual(list(cache.glob("*.part")), [])
            with patch.object(aminet, "urlopen", side_effect=AssertionError("Cache should avoid network")):
                with contextlib.redirect_stdout(io.StringIO()):
                    aminet.download(record(), cache)

    def test_bad_download_never_publishes_cache_file(self):
        def response(*args, **kwargs):
            stream = io.BytesIO(b"damaged")
            stream.url = record()["download_url"]
            return stream

        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            with patch.object(aminet, "urlopen", side_effect=response), patch.object(aminet.time, "sleep"):
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, "SHA-256"):
                    aminet.download(record(), cache)
            self.assertEqual(list(cache.iterdir()), [])

    def test_failed_extraction_does_not_publish_partial_project(self):
        def run(executable, command, *arguments):
            if command == "l":
                return "Path = scene.lws\nSize = 4\n"
            staged = Path(next(arg[2:] for arg in arguments if arg.startswith("-o")))
            (staged / "scene.lws").write_bytes(b"part")
            raise ValueError("CRC failed")

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            with patch.object(aminet, "run_7zip", side_effect=run):
                with self.assertRaisesRegex(ValueError, "CRC failed"):
                    aminet.extract(output / "example.lha", output / "example", "7z")
            self.assertEqual(list(output.iterdir()), [])

    def test_unsafe_member_is_rejected_before_extraction(self):
        with patch.object(aminet, "run_7zip", return_value="Path = ../escape\n") as run:
            with self.assertRaises(ValueError):
                aminet.extract(Path("example.lha"), Path("example"), "7z")
        self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
