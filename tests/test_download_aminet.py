"""Offline checks for manifest recovery, integrity and extraction failures."""
import contextlib
from email.message import Message
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

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


    def test_provenance_urls_preserve_case_and_allow_manifest_overrides(self):
        entry = record("JumpingBall.lha")
        self.assertEqual(aminet.origin_urls(entry),
                         ("https://aminet.net/pix/3dobj/JumpingBall.readme",
                          "https://aminet.net/package/pix/3dobj/JumpingBall"))
        entry.update(readme_url="https://aminet.net/custom.readme", notice_url="https://aminet.net/package/custom")
        self.assertEqual(aminet.origin_urls(entry), (entry["readme_url"], entry["notice_url"]))

    def test_notice_keeps_original_encoding_and_line_endings(self):
        raw = b"Short: Sc\xe8ne Amiga\r\nAuthor: Original author\r\n"
        response = io.BytesIO(raw)
        response.url = "https://aminet.net/pix/3dobj/example.readme"
        response.headers = Message()
        response.headers["Content-Type"] = "text/plain; charset=iso-8859-1"
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            original = destination / "README"
            original.write_bytes(b"README inside the LHA")
            with patch.object(aminet, "urlopen", return_value=response):
                self.assertTrue(aminet.add_provenance(record(), destination, Path("documentation/aminet.json")))
            self.assertEqual((destination / aminet.NOTICE_NAME).read_bytes(), raw)
            self.assertEqual(original.read_bytes(), b"README inside the LHA")
            text = (destination / aminet.PROVENANCE_NAME).read_text("utf-8")
            for expected in (record()["download_url"], record()["sha256"],
                             "https://aminet.net/package/pix/3dobj/example", response.url,
                             "[AMINET.readme](AMINET.readme)", hashlib.sha256(raw).hexdigest(),
                             "Notice retrieved (UTC):", "documentation/aminet.json"):
                self.assertIn(expected, text)
            before = {path: path.read_bytes() for path in destination.iterdir()}
            with patch.object(aminet, "urlopen", side_effect=AssertionError("No network on rerun")):
                self.assertFalse(aminet.add_provenance(record(), destination, Path("documentation/aminet.json")))
            self.assertEqual({path: path.read_bytes() for path in destination.iterdir()}, before)

    def test_existing_folders_gain_provenance_without_archive_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            manifest = base / "manifest.json"
            manifest.write_text(json.dumps({"records": [record()]}), encoding="utf-8")
            destination = base / "content/aminet-example"
            destination.mkdir(parents=True)
            (destination / "mesh").write_bytes(b"existing object")
            with patch.object(aminet, "find_7zip", return_value="7z"), patch.object(aminet, "download") as download:
                with patch.object(aminet, "fetch_notice", return_value=b"Short: example\n"), contextlib.redirect_stdout(io.StringIO()):
                    code = aminet.main(["--manifest", str(manifest), "--output", str(base / "content")])
            self.assertEqual(code, 0)
            download.assert_not_called()
            self.assertTrue((destination / aminet.PROVENANCE_NAME).is_file())
            self.assertEqual((destination / "mesh").read_bytes(), b"existing object")

    def test_notice_failure_preserves_sources_and_allows_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            (destination / "mesh").write_bytes(b"object")
            error = HTTPError("https://aminet.net/example.readme", 404, "Missing", {}, None)
            with patch.object(aminet, "urlopen", side_effect=error), patch.object(aminet.time, "sleep"):
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(HTTPError):
                    aminet.add_provenance(record(), destination, Path("manifest.json"))
            self.assertEqual([p.name for p in destination.iterdir()], ["mesh"])
            with patch.object(aminet, "fetch_notice", return_value=b"Recovered notice\n"):
                self.assertTrue(aminet.add_provenance(record(), destination, Path("manifest.json")))

    def test_provenance_filename_collision_never_overwrites_original(self):
        for filename in (aminet.PROVENANCE_NAME, aminet.NOTICE_NAME):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as temporary:
                destination = Path(temporary)
                target = destination / filename
                target.write_bytes(b"Original file from the archive")
                with patch.object(aminet, "fetch_notice", return_value=b"Online notice"):
                    with self.assertRaisesRegex(ValueError, "left untouched"):
                        aminet.add_provenance(record(), destination, Path("manifest.json"))
                self.assertEqual(target.read_bytes(), b"Original file from the archive")

    def test_missing_local_notice_is_restored_without_rewriting_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            with patch.object(aminet, "fetch_notice", return_value=b"Online notice"):
                aminet.add_provenance(record(), destination, Path("manifest.json"))
                readme = (destination / aminet.PROVENANCE_NAME).read_bytes()
                (destination / aminet.NOTICE_NAME).unlink()
                aminet.add_provenance(record(), destination, Path("manifest.json"))
            self.assertEqual((destination / aminet.PROVENANCE_NAME).read_bytes(), readme)
            self.assertEqual((destination / aminet.NOTICE_NAME).read_bytes(), b"Online notice")


    def test_inner_archive_uses_local_member_and_separate_provenance(self):
        outer = record("RunningLegs.lha", b"outer archive")
        inner = record("RunningLegs.lha (inner archive)", b"inner archive")
        inner.update(download_url=outer["download_url"], container_member="RunningLegs.lha")
        outer["notice_url"] = "https://aminet.net/package/pix/3dobj/RunningLegs"

        def extract(archive, destination, executable):
            destination.mkdir(parents=True)
            if archive.read_bytes() == b"outer archive":
                (destination / "RunningLegs.lha").write_bytes(b"inner archive")
            else:
                self.assertEqual(archive.read_bytes(), b"inner archive")
                (destination / "Left_Leg.lwo").write_bytes(b"object")

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            manifest = base / "manifest.json"
            # A container must run first even if it is listed after its member.
            manifest.write_text(json.dumps({"records": [inner, outer]}), encoding="utf-8")
            self.assertEqual(aminet.load_records(manifest), [outer, inner])
            archive = base / "RunningLegs.lha"
            archive.write_bytes(b"outer archive")
            with patch.object(aminet, "find_7zip", return_value="7z"), patch.object(aminet, "extract", side_effect=extract):
                with patch.object(aminet, "download", return_value=archive) as download:
                    with patch.object(aminet, "fetch_notice", return_value=b"Outer Aminet notice"), contextlib.redirect_stdout(io.StringIO()):
                        code = aminet.main(["--manifest", str(manifest), "--output", str(base / "content")])
            self.assertEqual(code, 0)
            download.assert_called_once()
            self.assertEqual(download.call_args.args[0], outer)
            parent = base / "content/aminet-RunningLegs"
            destination = parent / "RunningLegs"
            self.assertEqual((parent / "RunningLegs.lha").read_bytes(), b"inner archive")
            self.assertEqual((destination / "Left_Leg.lwo").read_bytes(), b"object")
            text = (destination / aminet.PROVENANCE_NAME).read_text("utf-8")
            for expected in ("Container download:", "Member path in container: `RunningLegs.lha`",
                             outer["sha256"], inner["sha256"], outer["notice_url"]):
                self.assertIn(expected, text)
            self.assertNotIn("- Archive download:", text)
            self.assertEqual((destination / aminet.NOTICE_NAME).read_bytes(), b"Outer Aminet notice")

    def test_inner_archive_requires_a_unique_container_and_safe_member(self):
        outer = record()
        inner = dict(outer, archive="example.lha (inner archive)", container_member="example.lha")
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "manifest.json"
            for records, error in (([inner], "Expected one outer archive"),
                                   ([outer, dict(outer, archive="other.lha"), inner], "Expected one outer archive"),
                                   ([outer, dict(inner, container_member="../example.lha")], "Unsafe")):
                with self.subTest(records=records):
                    manifest.write_text(json.dumps({"records": records}), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, error):
                        aminet.load_records(manifest)

    def test_inner_archive_has_its_own_integrity_check_and_cannot_be_downloaded(self):
        outer = record()
        inner = dict(record(payload=b"inner"), archive="example.lha (inner archive)", container_member="sub/inner.lha")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            member = output / "aminet-example/sub/inner.lha"
            member.parent.mkdir(parents=True)
            member.write_bytes(b"wrong")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                aminet.inner_archive(inner, outer, output)
            with self.assertRaisesRegex(ValueError, "not downloaded"):
                aminet.download(inner, output)
            member.write_bytes(b"inner")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(aminet.inner_archive(inner, outer, output), member)
            self.assertEqual(aminet.destination_for(inner, [outer, inner], output), output / "aminet-example/sub/inner")


if __name__ == "__main__":
    unittest.main()
