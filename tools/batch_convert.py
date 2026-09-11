"""Convert every supported loose LightWave file and report all other inputs."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from output_layout import FORMATS, Names, ProjectOutput, new_run, output_name

REPOSITORY = Path(__file__).resolve().parents[1]
KINDS = {b"LWOB": "LWOB", b"LWO2": "LWO2", b"PST_": "PST_"}


class BatchParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def is_link(path):
    info = path.lstat()
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def discover(content):
    records = []

    def walk_error(error):
        raise error

    for parent, directories, files in os.walk(content, followlinks=False, onerror=walk_error):
        for name in sorted(directories + files):
            path = Path(parent) / name
            record = {"source": path.relative_to(content).as_posix()}
            if is_link(path):
                record.update(status="skipped", reason="Filesystem link/reparse point; not traversed")
                records.append(record)
                if name in directories:
                    directories.remove(name)
                continue
            if name in directories:
                continue
            try:
                with path.open("rb") as stream:
                    header = stream.read(12)
            except OSError as error:
                record.update(status="failed", reason=str(error))
            else:
                kind = "LWSC" if header[:4] == b"LWSC" else KINDS.get(header[8:12]) if header[:4] == b"FORM" else None
                if kind:
                    record.update(status="pending", kind=kind)
                else:
                    record.update(status="skipped", reason="No supported LWOB/LWO2/PST_/LWSC signature; ancillary files and archives are not converted")
            records.append(record)
        directories.sort()
    return sorted(records, key=lambda record: record["source"])


def counts(records):
    return dict(sorted(Counter(record["status"] for record in records).items()))


def write_report(run, report):
    report["counts"] = counts(report["files"])
    temporary = run / "batch-report.json.tmp"
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(run / "batch-report.json")


def find_converter():
    candidates = ("bin/win64/lwconvert.exe", "build/Release/lwconvert.exe", "build/lwconvert.exe", "build/Debug/lwconvert.exe") if os.name == "nt" else ("build/lwconvert",)
    for relative in candidates:
        candidate = REPOSITORY / relative
        if candidate.is_file():
            return candidate
    return None


def prepare_projects(records, content, run):
    groups = {}
    for record in records:
        source = content / record["source"]
        project = content / Path(record["source"]).parts[0] if len(Path(record["source"]).parts) > 1 else content
        groups.setdefault(project, []).append(source)
        record["content_root"] = str(project)
    names = Names(output_name(project.name) for project in groups)
    return {str(project): ProjectOutput(run / "packages" / names.get(str(project), output_name(project.name)), sources)
            for project, sources in sorted(groups.items())}


def convert_one(record, number, content, run, converter, options, project_output):
    relative = Path(record["source"])
    source = content / relative
    project = Path(record["content_root"])
    package = run / ".work" / f"{number:06d}"
    record["package"] = project_output.directory.relative_to(run).as_posix()
    log = run / "logs" / project_output.directory.name / (project_output.name_for(source) + ".log")
    record["log"] = log.relative_to(run).as_posix()
    command = [str(converter), "convert", str(source), "--content-root", str(project), "--output", str(package)]
    for option in ("frame", "uv_map"):
        value = getattr(options, option)
        if value is not None:
            command += ["--" + option.replace("_", "-"), str(value)]
    for rule in options.map:
        command += ["--map", rule]
    try:
        package.parent.mkdir(parents=True, exist_ok=True)
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("w", encoding="utf-8") as stream:
            stream.write("Arguments: " + json.dumps(command, ensure_ascii=False) + "\n\n")
            stream.flush()
            result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=options.timeout, check=False)
        record["return_code"] = result.returncode
        if result.returncode not in (0, 2):
            record.update(status="failed", reason=f"Converter returned {result.returncode}; see log")
            return
        manifest = json.loads((package / "manifest.json").read_text("utf-8"))
        expected = "partial" if result.returncode == 2 else "converted-supported-subset"
        if manifest.get("status") != expected:
            raise ValueError("Converter exit code and manifest status disagree")
        published = project_output.publish(package, manifest)
        record.update(status="partial" if result.returncode == 2 else "converted", manifest=published.relative_to(run).as_posix())
        obj_uri = manifest["scene_obj"] if manifest["scene"] else manifest["assets"][0]["obj"]
        record["obj"] = (published.parent / obj_uri).resolve().relative_to(run).as_posix() if obj_uri else None
        gltf_uri = manifest["scene_gltf"] if manifest["scene"] else manifest["assets"][0]["gltf"]
        record["gltf"] = (published.parent / gltf_uri).resolve().relative_to(run).as_posix() if gltf_uri else None
        record["scene_obj_issue"] = manifest.get("scene_obj_issue", "")
        record["scene_gltf_issue"] = manifest.get("scene_gltf_issue", "")
        record["unresolved_object_instances"] = manifest.get("unresolved_object_instances", 0)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        record.update(status="failed", reason=str(error))
    finally:
        if package.exists():
            record["work_package"] = package.relative_to(run).as_posix()
            if record["status"] in ("converted", "partial"):
                # Only remove this converter's temporary package, inside this run.
                resolved, work = package.resolve(), (run / ".work").resolve()
                if resolved != work and resolved.is_relative_to(work):
                    try:
                        shutil.rmtree(resolved)
                        del record["work_package"]
                    except OSError as error:
                        record["cleanup_issue"] = str(error)


def main(argv=None):
    parser = BatchParser(description=__doc__)
    parser.add_argument("--content", type=Path, default=REPOSITORY / "content", help="Input tree (default: repository content/)")
    parser.add_argument("--output-root", type=Path, default=REPOSITORY / "output", help="Parent of a new batch directory (default: repository output/)")
    parser.add_argument("--converter", type=Path, help="Converter executable (Windows default: bin/win64/lwconvert.exe, then local builds)")
    parser.add_argument("--dry-run", action="store_true", help="List supported files and counts without creating output or invoking the converter")
    parser.add_argument("--timeout", type=float, default=120, help="Maximum seconds per conversion (default: 120)")
    parser.add_argument("--frame", type=float, help="Override the snapshot frame for every scene")
    parser.add_argument("--uv-map", help="Explicit native TXUV map name passed to every conversion")
    parser.add_argument("--map", action="append", default=[], metavar="PREFIX=DIRECTORY", help="Explicit historical path mapping passed to every conversion")
    options = parser.parse_args(argv)
    content, output = options.content.resolve(), options.output_root.resolve()
    if not content.is_dir():
        parser.error(f"Input directory does not exist: {content}")
    if output == content or content in output.parents or output in content.parents:
        parser.error("Input and output trees must be separate, without nesting")
    if not math.isfinite(options.timeout) or options.timeout <= 0:
        parser.error("--timeout must be a positive finite number")
    if options.frame is not None and not math.isfinite(options.frame):
        parser.error("--frame must be finite")
    converter = options.converter.resolve() if options.converter else find_converter()
    if not options.dry_run and (converter is None or not converter.is_file()):
        parser.error("Converter not found. Build it first: cmake --build build --config Release (see README.md), or use --converter")
    records = discover(content)
    eligible = [record for record in records if record["status"] == "pending"]
    print(f"Scanned {len(records)} entries; {len(eligible)} supported LightWave files; {counts(records)}", flush=True)
    if options.dry_run:
        for record in eligible:
            print(f"{record['kind']}: {record['source']}")
        return 1 if any(record["status"] == "failed" for record in records) else 0
    output.mkdir(parents=True, exist_ok=True)
    run = new_run(output, datetime.now().strftime("batch-%Y%m%d-%H%M%S"))
    (run / "logs").mkdir()
    projects = prepare_projects(eligible, content, run)
    report = {"schema_version": "0.2", "layout_version": "0.2", "formats": FORMATS, "status": "running", "started_utc": datetime.now(timezone.utc).isoformat(), "content": str(content), "output": str(run), "converter": str(converter), "options": {"frame": options.frame, "uv_map": options.uv_map, "map": options.map, "timeout": options.timeout}, "scope": "Loose LWOB/LWO2/PST_/LWSC files by signature; OBJ/MTL, LWIR and glTF 2.0 static geometry. Ancillary files are listed as skipped; archives are not extracted. Each top-level content directory is a separate project root.", "files": records}
    write_report(run, report)
    print(f"Output: {run}", flush=True)
    interrupted = False
    try:
        for number, record in enumerate(eligible, 1):
            convert_one(record, number, content, run, converter, options, projects[record["content_root"]])
            print(f"[{number}/{len(eligible)}] {record['status'].upper()}: {record['source']}", flush=True)
            if number % 25 == 0:
                write_report(run, report)
    except KeyboardInterrupt:
        interrupted = True
    work = run / ".work"
    if work.exists() and not any(work.iterdir()):
        work.rmdir()
    final_counts = counts(records)
    code = 130 if interrupted else 1 if final_counts.get("failed") else 2 if final_counts.get("partial") else 0
    report.update(status="interrupted" if interrupted else "failed" if code == 1 else "partial" if code == 2 else "completed", exit_code=code, finished_utc=datetime.now(timezone.utc).isoformat())
    write_report(run, report)
    print(f"Batch {report['status']}: {final_counts}\nReport: {run / 'batch-report.json'}", flush=True)
    return code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OSError as error:
        print(f"Batch error: {error}", file=sys.stderr)
        raise SystemExit(1)
