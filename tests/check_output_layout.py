"""Validate published batch links, source hashes, buffers and OBJ/MTL bindings."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re


def check(run):
    run = run.resolve()
    report = json.loads((run / "batch-report.json").read_text("utf-8"))
    native, objects, projects = {}, set(), {}

    def linked(directory, uri, root):
        assert not Path(uri).is_absolute(), uri
        path = (directory / uri).resolve()
        assert path.is_relative_to(root) and path.is_file(), path
        return path

    for record in report["files"]:
        if record["status"] not in ("converted", "partial"):
            continue
        project = (run / record["package"]).resolve()
        assert project.is_relative_to(run), project
        manifest_path = linked(run, record["manifest"], project)
        linked(run, record["log"], run)
        manifest = json.loads(manifest_path.read_text("utf-8"))
        projects.setdefault(project, set()).add(manifest_path)
        assert manifest["status"] == ("partial" if record["status"] == "partial" else "converted-supported-subset")
        uris = [asset["uri"] for asset in manifest["assets"]]
        if manifest["scene"]:
            uris.append(manifest["scene"])
        for uri in uris:
            path = linked(manifest_path.parent, uri, project)
            if path in native:
                continue
            data = json.loads(path.read_text("utf-8"))
            source = linked(path.parent, data["source"]["uri"], project)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            assert digest == data["source"]["sha256"], source
            assert hashlib.sha256(Path(data["source"]["path"]).read_bytes()).hexdigest() == digest, source
            buffer = data.get("buffer", data.get("animation_buffer"))
            assert linked(path.parent, buffer["uri"], project).stat().st_size == data.get("buffer_bytes", data.get("animation_bytes")), path
            native[path] = digest
        for asset in manifest["assets"]:
            assert native[linked(manifest_path.parent, asset["uri"], project)] == asset["id"]
            obj = linked(manifest_path.parent, asset["obj"], project)
            assert linked(manifest_path.parent, asset["mtl"], project) == obj.with_suffix(".mtl")
            objects.add(obj)
        if manifest["scene_obj"]:
            obj = linked(manifest_path.parent, manifest["scene_obj"], project)
            assert linked(manifest_path.parent, manifest["scene_mtl"], project) == obj.with_suffix(".mtl")
            objects.add(obj)
        primary = manifest["scene_obj"] if manifest["scene"] else manifest["assets"][0]["obj"]
        assert (linked(run, record["obj"], project) if record["obj"] else None) == (linked(manifest_path.parent, primary, project) if primary else None)
    for obj in sorted(objects):
        used, libraries = set(), []
        with obj.open(encoding="utf-8") as reader:
            for line in reader:
                if line.startswith("mtllib "):
                    libraries.append(linked(obj.parent, line.split()[1], obj.parent))
                elif line.startswith("usemtl "):
                    used.add(line.split()[1])
        assert libraries == [obj.with_suffix(".mtl")], obj
        materials = {line.split()[1] for line in libraries[0].read_text("utf-8").splitlines() if line.startswith("newmtl ")}
        assert used <= materials, (obj, used - materials)
    for project, manifests in projects.items():
        index = json.loads((project / "manifest.json").read_text("utf-8"))
        assert manifests == {linked(project, item["manifest"], project) for item in index["conversions"]}, project
        for planned in ("gltf", "blender"):
            assert index["formats"][planned] == "not-implemented"
            assert list((project / planned).iterdir()) == []
        for path in project.rglob("*"):
            assert not re.fullmatch(r"[0-9a-f]{64}", path.name), path
    assert not (run / ".work").exists(), "Successful batches must remove temporary packages"
    return {"batch": str(run), "layout_version": report["layout_version"], "input_statuses": dict(Counter(r["status"] for r in report["files"])), "projects": len(projects), "conversion_manifests": sum(map(len, projects.values())), "native_sources_checked": len(native), "obj_mtl_pairs_checked": len(objects), "passed": True, "scope": "Published relative links, project indexes, copied/original source hashes, buffer sizes and OBJ/MTL material references. Does not assess LightWave visual fidelity."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = check(args.batch)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.write_text(text, encoding="utf-8")
    print(text)
