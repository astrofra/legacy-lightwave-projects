"""Validate published batch links, source hashes, buffers and OBJ/MTL bindings."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote


def check(run):
    run = run.resolve()
    report = json.loads((run / "batch-report.json").read_text("utf-8"))
    native, objects, gltfs, projects = {}, set(), set(), {}

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
        if manifest.get("evaluated_animation"):
            capture_path = linked(manifest_path.parent, manifest["evaluated_animation"]["uri"], project)
            assert hashlib.sha256(capture_path.read_bytes()).hexdigest() == manifest["evaluated_animation"]["sha256"]
            capture = json.loads(capture_path.read_text("utf-8"))
            for frame in capture["frames"]:
                raw = linked(capture_path.parent, frame["uri"], project)
                assert hashlib.sha256(raw.read_bytes()).hexdigest() == frame["sha256"]
        projects.setdefault(project, set()).add(manifest_path)
        assert manifest["status"] == ("partial" if record["status"] == "partial" else "converted-supported-subset")
        for rig in manifest.get("gltf_rigs", []):
            if rig.get("derived_skin"):
                skin = json.loads(linked(manifest_path.parent,rig["derived_skin"],project).read_text("utf-8"))
                assert skin["kind"] == "derived-skin" and skin["approximation"]
                assert skin["owner_item"] == rig["owner_item"] == skin["joint_items"][0]
                assert skin["source_object_sha256"] in {a["id"] for a in manifest["assets"]}
                scene_source = json.loads(linked(manifest_path.parent,manifest["scene"],project).read_text("utf-8"))["source"]
                assert skin["source_scene_sha256"] == scene_source["sha256"]
                for point in skin["points"]:
                    assert not point or abs(sum(w for _,w in point)-1) < 1e-6
                    assert all(0 <= j < len(skin["joint_items"]) and 0 < w <= 1 for j,w in point)
        uris = [asset["uri"] for asset in manifest["assets"]]
        if manifest["scene"]:
            uris.append(manifest["scene"])
        for uri in uris:
            path = linked(manifest_path.parent, uri, project)
            if path in native:
                continue
            data = json.loads(path.read_text("utf-8"))
            for reference in data.get("image_references", []):
                if reference.get("uri"):
                    image = linked(path.parent, reference["uri"], project)
                    assert hashlib.sha256(image.read_bytes()).hexdigest() == reference["sha256"], image
                decoded = reference.get("decoded_image") or {}
                if decoded.get("png_uri"):
                    image = linked(path.parent, decoded["png_uri"], project)
                    assert hashlib.sha256(image.read_bytes()).hexdigest() == decoded["png_sha256"], image
            for material in data.get("materials", []):
                for uri in material.get("derived_maps", {}).values():
                    image = linked(path.parent, uri, project)
                    assert hashlib.sha256(image.read_bytes()).hexdigest() == image.stem, image
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
        if manifest.get("formats", {}).get("gltf") == "generated":
            pairs = [(a["gltf"], a["gltf_bin"]) for a in manifest["assets"]]
            if manifest["scene_gltf"]:
                pairs.append((manifest["scene_gltf"], manifest["scene_gltf_bin"]))
            rigs = [r for r in manifest.get("gltf_rigs", []) if r["gltf"]]
            pairs.extend((r["gltf"], r["gltf_bin"]) for r in rigs)
            animations = manifest.get("gltf_animations", [])
            pairs.extend((a["gltf"], a["gltf_bin"]) for a in animations)
            animation_paths = {linked(manifest_path.parent, a["gltf"], project) for a in animations}
            if manifest.get("gltf_evaluated_scene") and manifest["gltf_evaluated_scene"]["channels"]:
                animation_paths.add(linked(manifest_path.parent, manifest["scene_gltf"], project))
            assert {linked(run, uri, project) for uri in record.get("animation_gltf", [])} == animation_paths
            assert {linked(run, uri, project) for uri in record.get("rig_gltf", [])} == {linked(manifest_path.parent, r["gltf"], project) for r in rigs}
            for uri, binary in pairs:
                path = linked(manifest_path.parent, uri, project)
                bin_path = linked(manifest_path.parent, binary, project)
                if path not in gltfs:
                    data = json.loads(path.read_text("utf-8"))
                    assert data["asset"]["version"] == "2.0", path
                    for buffer in data.get("buffers", []):
                        assert linked(path.parent, unquote(buffer["uri"]), project) == bin_path
                        assert bin_path.stat().st_size == buffer["byteLength"]
                    for image in data.get("images", []):
                        image_path = linked(path.parent, unquote(image["uri"]), project)
                        assert hashlib.sha256(image_path.read_bytes()).hexdigest() == image_path.stem, image_path
                    for texture in data.get("textures", []):
                        assert 0 <= texture["source"] < len(data["images"])
                        assert 0 <= texture["sampler"] < len(data["samplers"])
                    for material in data.get("materials", []):
                        bindings = [material.get("pbrMetallicRoughness", {}).get("baseColorTexture"),material.get("emissiveTexture"),material.get("extensions", {}).get("KHR_materials_specular", {}).get("specularTexture")]
                        for binding in bindings:
                            if binding: assert 0 <= binding["index"] < len(data["textures"])
                    gltfs.add(path)
            primary = manifest["scene_gltf"] if manifest["scene"] else manifest["assets"][0]["gltf"]
            assert (linked(run, record["gltf"], project) if record["gltf"] else None) == (linked(manifest_path.parent, primary, project) if primary else None)
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
        for line in libraries[0].read_text("utf-8").splitlines():
            if line.startswith(("map_Kd ", "map_d ", "map_Ke ", "map_Ks ", "bump ")):
                image = linked(obj.parent, line.split()[-1], obj.parent)
                assert hashlib.sha256(image.read_bytes()).hexdigest() == image.stem, image
    for project, manifests in projects.items():
        index = json.loads((project / "manifest.json").read_text("utf-8"))
        assert manifests == {linked(project, item["manifest"], project) for item in index["conversions"]}, project
        for planned in ("gltf", "blender"):
            if index["formats"][planned] == "not-implemented":
                assert list((project / planned).iterdir()) == []
        for path in project.rglob("*"):
            assert not re.fullmatch(r"[0-9a-f]{64}", path.name), path
    assert not (run / ".work").exists(), "Successful batches must remove temporary packages"
    return {"batch": str(run), "layout_version": report["layout_version"], "input_statuses": dict(Counter(r["status"] for r in report["files"])), "projects": len(projects), "conversion_manifests": sum(map(len, projects.values())), "native_sources_checked": len(native), "obj_mtl_pairs_checked": len(objects), "gltf_buffer_pairs_checked": len(gltfs), "passed": True, "scope": "Published relative links, project indexes, source and PNG hashes, buffer sizes, OBJ/MTL material references and glTF buffer/image/material bindings. Does not assess LightWave visual fidelity."}


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
