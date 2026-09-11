"""Download the Aminet manifest with Python 3 and extract its LHA files with 7-Zip."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


REPOSITORY = Path(__file__).resolve().parents[1]
HEADER_KEYS = ("archive", "download_url", "sha256", "archive_bytes")


def recover_records(text):
    """Read complete records and the complete download header of a cut-off record.

    Walk JSON boundaries with the decoder, never search arbitrary nested strings
    for URLs. Missing scene metadata is neither repaired nor written back.
    """
    decoder = json.JSONDecoder()

    def whitespace(pos):
        while pos < len(text) and text[pos].isspace():
            pos += 1
        return pos

    def expect(pos, character):
        pos = whitespace(pos)
        if text[pos:pos + 1] != character:
            raise ValueError(f"Cannot recover manifest: expected {character!r} at {pos}")
        return whitespace(pos + 1)

    pos = expect(0, "{")
    while True:
        key, pos = decoder.raw_decode(text, pos)
        pos = expect(pos, ":")
        if key == "records":
            break
        _, pos = decoder.raw_decode(text, pos)
        pos = expect(pos, ",")
    pos = expect(pos, "[")
    records = []
    while pos < len(text) and text[pos] != "]":
        start = pos
        try:
            record, pos = decoder.raw_decode(text, pos)
        except json.JSONDecodeError:
            # Only accept the interrupted record when all four download fields
            # are present as complete JSON values before the interruption.
            pos = expect(start, "{")
            header = {}
            while pos < len(text):
                try:
                    key, pos = decoder.raw_decode(text, pos)
                    pos = expect(pos, ":")
                    value, pos = decoder.raw_decode(text, pos)
                except json.JSONDecodeError:
                    break
                if key in HEADER_KEYS:
                    header[key] = value
                if all(key in header for key in HEADER_KEYS):
                    records.append(header)
                    return records
                pos = expect(pos, ",")
            raise ValueError("Interrupted record has an incomplete download header")
        records.append(record)
        pos = whitespace(pos)
        if pos == len(text) or text[pos] == "]":
            break
        pos = expect(pos, ",")
    return records


def validate_path(name):
    """Reject absolute paths, traversal and Windows filename aliases."""
    if not isinstance(name, str) or not name:
        raise ValueError(f"Invalid archive path: {name!r}")
    parts = name.replace("\\", "/").split("/")
    if PureWindowsPath(name).drive or name.startswith(("/", "\\")):
        raise ValueError(f"Absolute archive path: {name!r}")
    for part in parts:
        if (part in ("", ".", "..") or part.endswith((" ", "."))
                or re.search(r'[<>:"|?*\x00-\x1f]', part)
                or PureWindowsPath(part).is_reserved()):
            raise ValueError(f"Unsafe or unsupported archive path: {name!r}")


def load_records(path, recover=False):
    text = path.read_text(encoding="utf-8-sig")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        if not recover:
            raise ValueError(f"Invalid JSON in {path}: {error}. If the file was cut off, "
                             "use --recover-truncated-manifest to process recoverable entries.") from error
        records = recover_records(text)
        print(f"WARNING: invalid/truncated manifest ({error}); recovered {len(records)} "
              "download entries only. The full catalog may contain more archives.", file=sys.stderr)
    else:
        records = data.get("records") if isinstance(data, dict) else None
    if not isinstance(records, list) or not records:
        raise ValueError("Manifest must contain a nonempty 'records' array")
    names = set()
    for record in records:
        if not isinstance(record, dict) or any(key not in record for key in HEADER_KEYS):
            raise ValueError(f"Each record needs {', '.join(HEADER_KEYS)}")
        name = record["archive"]
        validate_path(name)
        if "/" in name or "\\" in name or Path(name).suffix.lower() not in (".lha", ".lzh"):
            raise ValueError(f"Expected a plain LHA archive filename: {name!r}")
        stem = Path(name).stem
        validate_path(stem)
        if stem.casefold() in names:
            raise ValueError(f"Duplicate destination folder: {stem}")
        names.add(stem.casefold())
        url = record["download_url"]
        if not isinstance(url, str) or urlsplit(url).scheme != "https" or not urlsplit(url).hostname:
            raise ValueError(f"Expected an HTTPS download URL for {name}")
        if not isinstance(record["sha256"], str) or not re.fullmatch(r"[0-9a-fA-F]{64}", record["sha256"]):
            raise ValueError(f"Invalid SHA-256 for {name}")
        if type(record["archive_bytes"]) is not int or record["archive_bytes"] <= 0:
            raise ValueError(f"Invalid archive_bytes for {name}")
    return records


def find_7zip(explicit=None):
    candidates = [explicit] if explicit else [shutil.which(name) for name in ("7z", "7zz")]
    if not explicit and os.name == "nt":
        for variable in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = os.environ.get(variable)
            if base:
                candidates.append(str(Path(base) / "7-Zip" / "7z.exe"))
    for candidate in candidates:
        if candidate:
            executable = shutil.which(str(candidate))
            if executable:
                return str(Path(executable).resolve())
    raise ValueError("7-Zip not found. Install it from https://www.7-zip.org/ "
                     "or pass --seven-zip PATH_TO_7Z. The reduced 7za build is not supported.")


def verify_archive(path, record):
    if path.stat().st_size != record["archive_bytes"]:
        raise ValueError(f"Size mismatch for {record['archive']}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != record["sha256"].lower():
        raise ValueError(f"SHA-256 mismatch for {record['archive']}")


def download(record, cache):
    archive = cache / record["archive"]
    if archive.is_file():
        try:
            verify_archive(archive, record)
            print("  Using verified cached archive", flush=True)
            return archive
        except ValueError as error:
            print(f"  {error}; downloading again", flush=True)
    cache.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, 4):
        temporary = None
        try:
            request = Request(record["download_url"], headers={"User-Agent": "legacy-lightwave-projects/aminet-downloader"})
            with urlopen(request, timeout=30) as response:
                if urlsplit(response.url).scheme != "https":
                    raise ValueError("Download redirected to a non-HTTPS URL")
                with tempfile.NamedTemporaryFile(dir=cache, suffix=".part", delete=False) as stream:
                    temporary = Path(stream.name)
                    shutil.copyfileobj(response, stream)
            verify_archive(temporary, record)
            temporary.replace(archive)
            return archive
        except (OSError, URLError, ValueError) as error:
            if attempt == 3:
                raise
            print(f"  Download attempt {attempt}/3 failed: {error}; retrying", flush=True)
            time.sleep(attempt)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def run_7zip(executable, *arguments):
    result = subprocess.run([executable, *arguments], capture_output=True,
                            encoding="utf-8", errors="replace", timeout=180)
    if result.returncode != 0:
        raise ValueError(f"7-Zip exited with {result.returncode}:\n{result.stdout}\n{result.stderr}")
    return result.stdout


def extract(archive, destination, executable):
    listing = run_7zip(executable, "l", "-slt", "-ba", "-sccUTF-8", "--", str(archive))
    members = 0
    for line in listing.splitlines():
        if line.startswith("Path = "):
            validate_path(line[7:])
            members += 1
        if line.startswith(("Symbolic Link = ", "Hard Link = ")):
            raise ValueError("Archive contains an unsupported filesystem link")
    if not members:
        raise ValueError("Archive contains no extractable entries")
    # Keep partial extractions out of the final project directory. The temporary
    # directory is on the destination volume so publishing is a rename.
    with tempfile.TemporaryDirectory(prefix=".aminet-", dir=destination.parent) as temporary:
        staged = Path(temporary) / "files"
        staged.mkdir()
        run_7zip(executable, "x", "-y", "-sccUTF-8", f"-o{staged}", "--", str(archive))
        if os.path.lexists(destination):
            raise ValueError(f"Destination appeared during extraction: {destination}")
        staged.rename(destination)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=REPOSITORY / "documentation/aminet.json")
    parser.add_argument("--output", type=Path, default=REPOSITORY / "content",
                        help="Extraction root (default: repository content/)")
    parser.add_argument("--cache", type=Path, default=REPOSITORY / "_tmp/aminet/archives")
    parser.add_argument("--seven-zip", help="Path to 7z.exe, 7z or 7zz (autodetected by default)")
    parser.add_argument("--recover-truncated-manifest", action="store_true",
                        help="Explicitly recover complete download headers from a cut-off JSON file")
    parser.add_argument("--list", action="store_true", help="List downloads without writing any files")
    options = parser.parse_args(argv)
    try:
        records = load_records(options.manifest, options.recover_truncated_manifest)
        output = options.output.resolve()
        cache = options.cache.resolve()
        if options.list:
            for record in records:
                print(f"{record['download_url']} -> {output / Path(record['archive']).stem}")
            return 0
        executable = find_7zip(options.seven_zip)
        print(f"7-Zip: {executable}", flush=True)
        output.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    downloaded = skipped = failed = 0
    for number, record in enumerate(records, 1):
        destination = output / Path(record["archive"]).stem
        print(f"[{number}/{len(records)}] {record['archive']} -> {destination}", flush=True)
        if os.path.lexists(destination):
            print("  Skipped: destination already exists (left untouched)", flush=True)
            skipped += 1
            continue
        try:
            archive = download(record, cache)
            extract(archive, destination, executable)
        except (OSError, URLError, ValueError, subprocess.TimeoutExpired) as error:
            print(f"  FAILED: {error}", file=sys.stderr, flush=True)
            failed += 1
        else:
            print("  SHA-256 verified; extraction complete", flush=True)
            downloaded += 1
    print(f"Finished: {downloaded} extracted, {skipped} existing destinations skipped, {failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted; rerun to continue with cached archives.", file=sys.stderr)
        sys.exit(130)
