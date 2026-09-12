"""Download the Aminet manifest with Python 3 and extract its LHA files with 7-Zip."""
import argparse
from datetime import datetime, timezone
import hashlib
from http.client import HTTPException
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
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


REPOSITORY = Path(__file__).resolve().parents[1]
HEADER_KEYS = ("archive", "download_url", "sha256", "archive_bytes")
PROVENANCE_NAME = "README.aminet.md"
NOTICE_NAME = "AMINET.readme"


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


def container_record(record, records):
    if "container_member" not in record:
        return None
    candidates = [entry for entry in records if "container_member" not in entry
                  and entry["download_url"] == record["download_url"]]
    if len(candidates) != 1:
        raise ValueError(f"Expected one outer archive for {record['archive']}; found {len(candidates)}")
    return candidates[0]


def archive_directory(record, output):
    return output / ("aminet-" + Path(record["archive"]).stem)


def destination_for(record, records, output):
    container = container_record(record, records)
    if container is None:
        return archive_directory(record, output)
    member = Path(record["container_member"].replace("\\", "/"))
    return archive_directory(container, output) / member.with_suffix("")


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
    for record in records:
        if not isinstance(record, dict) or any(key not in record for key in HEADER_KEYS):
            raise ValueError(f"Each record needs {', '.join(HEADER_KEYS)}")
        name = record["archive"]
        if "container_member" in record:
            # An inner entry's archive field is a descriptive label, not a URL
            # filename. container_member is the actual path inside the outer LHA.
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Expected a nonempty inner archive label")
            validate_path(record["container_member"])
            filename = Path(record["container_member"].replace("\\", "/")).name
        else:
            validate_path(name)
            if "/" in name or "\\" in name:
                raise ValueError(f"Expected a plain LHA archive filename: {name!r}")
            filename = name
        if Path(filename).suffix.lower() not in (".lha", ".lzh"):
            raise ValueError(f"Expected a plain LHA archive filename: {filename!r}")
        validate_path(Path(filename).stem)
        url = record["download_url"]
        if not isinstance(url, str) or urlsplit(url).scheme != "https" or not urlsplit(url).hostname:
            raise ValueError(f"Expected an HTTPS download URL for {name}")
        if not isinstance(record["sha256"], str) or not re.fullmatch(r"[0-9a-fA-F]{64}", record["sha256"]):
            raise ValueError(f"Invalid SHA-256 for {name}")
        if type(record["archive_bytes"]) is not int or record["archive_bytes"] <= 0:
            raise ValueError(f"Invalid archive_bytes for {name}")
    names = set()
    for record in records:
        destination = destination_for(record, records, Path())
        key = destination.as_posix().casefold()
        if key in names:
            raise ValueError(f"Duplicate destination folder: {destination}")
        names.add(key)
    # The manifest may describe inner archives before their containers.
    return sorted(records, key=lambda record: "container_member" in record)


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
    if "container_member" in record:
        raise ValueError("An inner archive must be read from its container, not downloaded")
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
        except (OSError, URLError, HTTPException, ValueError) as error:
            if attempt == 3:
                raise
            print(f"  Download attempt {attempt}/3 failed: {error}; retrying", flush=True)
            time.sleep(attempt)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def origin_urls(record):
    archive = urlsplit(record["download_url"])
    stem = archive.path.rsplit(".", 1)[0]
    notice = record.get("readme_url") or urlunsplit(archive._replace(path=stem + ".readme", fragment=""))
    page = record.get("notice_url") or "https://aminet.net/package/" + stem.lstrip("/")
    for url in (notice, page):
        if not isinstance(url, str) or urlsplit(url).scheme != "https" or not urlsplit(url).hostname:
            raise ValueError(f"Expected an HTTPS provenance URL for {record['archive']}")
    return notice, page


def fetch_notice(url):
    for attempt in range(1, 4):
        try:
            request = Request(url, headers={"User-Agent": "legacy-lightwave-projects/aminet-downloader"})
            with urlopen(request, timeout=30) as response:
                if urlsplit(response.url).scheme != "https":
                    raise ValueError("Notice redirected to a non-HTTPS URL")
                data = response.read()
                if not data.strip() or response.headers.get_content_type() != "text/plain":
                    raise ValueError("Expected a nonempty plain-text Aminet notice")
                return data
        except (OSError, URLError, HTTPException, ValueError) as error:
            if attempt == 3:
                raise
            print(f"  Notice attempt {attempt}/3 failed: {error}; retrying", flush=True)
            time.sleep(attempt)


def write_new_file(path, data):
    """Publish without overwriting archive contents or earlier provenance files."""
    if os.path.lexists(path):
        if not path.is_symlink() and path.is_file() and path.read_bytes() == data:
            return
        raise ValueError(f"Provenance filename already in use; left untouched: {path}")
    # The complete temporary file is on the same volume; an exclusive hard link
    # publishes it atomically and fails if another file appeared at the target.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".part", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def add_provenance(record, destination, manifest, container=None):
    origin = dict(container or record)
    for key in ("readme_url", "notice_url"):
        if key in record:
            origin[key] = record[key]
    notice_url, page_url = origin_urls(origin)
    readme = destination / PROVENANCE_NAME
    notice = destination / NOTICE_NAME
    identity = {key: record[key] for key in HEADER_KEYS}
    identity.update(readme_url=notice_url, notice_url=page_url)
    if container is not None:
        identity.update(container_member=record["container_member"],
                        container_archive=container["archive"], container_sha256=container["sha256"])
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()
    marker = f"<!-- Generated by download_aminet.py; provenance: {fingerprint} -->\n"
    existing = None
    if os.path.lexists(readme):
        if readme.is_symlink() or not readme.is_file():
            raise ValueError(f"Provenance filename already in use: {readme}")
        existing = readme.read_text(encoding="utf-8")
        if not existing.startswith(marker):
            raise ValueError(f"Provenance belongs to another source or file; left untouched: {readme}")
        if notice.is_file() and not notice.is_symlink():
            digest = hashlib.sha256(notice.read_bytes()).hexdigest()
            if f"- Notice SHA-256: `{digest}`\n" in existing:
                return False
            raise ValueError(f"Saved notice differs from its provenance checksum: {notice}")
    data = fetch_notice(notice_url)
    digest = hashlib.sha256(data).hexdigest()
    digest_line = f"- Notice SHA-256: `{digest}`\n"
    if existing is not None and digest_line not in existing:
        raise ValueError("The online notice has changed; existing provenance was left untouched")
    write_new_file(notice, data)
    if existing is None:
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        source_manifest = manifest.resolve()
        if source_manifest.is_relative_to(REPOSITORY):
            source_manifest = source_manifest.relative_to(REPOSITORY)
        if container is None:
            location = f"- Archive download: <{record['download_url']}>\n"
        else:
            location = (
                f"- Container archive: `{container['archive']}`\n"
                f"- Container download: <{container['download_url']}>\n"
                f"- Container SHA-256 (manifest): `{container['sha256'].lower()}`\n"
                f"- Member path in container: `{record['container_member']}`\n"
            )
        text = (
            marker + "\n# Aminet archive provenance\n\n"
            f"- Archive: `{record['archive']}`\n"
            "- Origin: [Aminet](https://aminet.net/)\n"
            f"- Package page: <{page_url}>\n"
            + location +
            f"- Archive size (manifest): {record['archive_bytes']} bytes\n"
            f"- Archive SHA-256 (manifest): `{record['sha256'].lower()}`\n"
            f"- Source manifest: `{source_manifest.as_posix()}`\n\n"
            "## Aminet notice\n\n"
            f"- Online notice: <{notice_url}>\n"
            f"- Local copy: [{NOTICE_NAME}]({NOTICE_NAME})\n"
            f"- Notice retrieved (UTC): {timestamp}\n"
            f"- Notice size: {len(data)} bytes\n"
            + digest_line + "\n"
            "The local notice is a byte-for-byte copy of Aminet's separate `.readme`, "
            "including its original encoding and line endings. It contains the "
            "author, description and usage information supplied to Aminet.\n\n"
            "Archive contents and any bundled README files are preserved. "
            "The archive checksum above comes from the manifest; adding this "
            "document to an existing folder does not revalidate that folder's contents. "
            "The notice retrieval time is not the archive's original publication "
            "or extraction date. Consult the notice and bundled documentation "
            "for the author's terms.\n"
        )
        write_new_file(readme, text.encode("utf-8"))
    return True


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


def inner_archive(record, container, output):
    directory = archive_directory(container, output)
    member = directory / Path(record["container_member"].replace("\\", "/"))
    if not member.resolve().is_relative_to(directory) or not member.is_file():
        raise ValueError(f"Missing or out-of-container inner archive: {member}")
    verify_archive(member, record)
    print(f"  Using verified inner archive: {member}", flush=True)
    return member


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
                container = container_record(record, records)
                source = (f"{container['archive']} :: {record['container_member']}"
                          if container else record["download_url"])
                print(f"{source} -> {destination_for(record, records, output)}")
            return 0
        executable = find_7zip(options.seven_zip)
        print(f"7-Zip: {executable}", flush=True)
        output.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    downloaded = skipped = documented = failed = 0
    for number, record in enumerate(records, 1):
        container = container_record(record, records)
        destination = destination_for(record, records, output)
        print(f"[{number}/{len(records)}] {record['archive']} -> {destination}", flush=True)
        try:
            if os.path.lexists(destination):
                if not destination.is_dir() or destination.is_symlink() or destination.resolve() != destination:
                    raise ValueError(f"Destination is not a regular directory: {destination}")
                print("  Already extracted; checking provenance", flush=True)
                skipped += 1
            else:
                archive = inner_archive(record, container, output) if container else download(record, cache)
                extract(archive, destination, executable)
                print("  SHA-256 verified; extraction complete", flush=True)
                downloaded += 1
            if add_provenance(record, destination, options.manifest, container):
                documented += 1
                print(f"  Added {PROVENANCE_NAME} and {NOTICE_NAME}", flush=True)
            else:
                print("  Provenance already present", flush=True)
        except (OSError, URLError, HTTPException, ValueError, subprocess.TimeoutExpired) as error:
            print(f"  FAILED: {error}", file=sys.stderr, flush=True)
            failed += 1
    print(f"Finished: {downloaded} extracted, {skipped} existing folders, "
          f"{documented} provenance records added, {failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted; rerun to continue with cached archives.", file=sys.stderr)
        sys.exit(130)
