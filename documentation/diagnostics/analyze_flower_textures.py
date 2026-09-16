"""Compare image resolution in the Flower batch and an isolated layout experiment."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def solfinal_envelopes(path):
    """Independent inspection of this fixture's LWO2 ENVL/KEY records."""
    data = path.read_bytes()
    assert data[:4] == b"FORM" and data[8:12] == b"LWO2"

    def chunks(start, end, width):
        while start + 4 + width <= end:
            tag = data[start:start + 4]
            size = int.from_bytes(data[start + 4:start + 4 + width], "big")
            payload = start + 4 + width
            assert payload + size <= end
            yield tag, payload, size
            start = payload + size + size % 2

    records = []
    for tag, start, size in chunks(12, len(data), 4):
        if tag != b"ENVL":
            continue
        # All three fixture indices use the short VX representation.
        assert data[start] != 0xFF
        record = {"index": int.from_bytes(data[start:start + 2], "big"), "keys": [], "spans": []}
        for subtag, position, length in chunks(start + 2, start + size, 2):
            payload = data[position:position + length]
            if subtag == b"NAME":
                record["name"] = payload.rstrip(b"\0").decode("ascii")
            elif subtag == b"KEY ":
                time, value = struct.unpack(">ff", payload)
                record["keys"].append({"time": time, "value": value})
            elif subtag in {b"PRE ", b"POST"}:
                record[subtag.decode("ascii").strip().lower()] = int.from_bytes(payload, "big")
            elif subtag == b"SPAN":
                record["spans"].append(payload[:4].decode("ascii"))
        record["equal_key_values"] = len({k["value"] for k in record["keys"]}) == 1
        records.append(record)
    return records


def audit(package, source):
    owners = []
    resolutions, statuses, issues = Counter(), Counter(), Counter()
    for path in sorted((package / "IR").rglob("*.json")):
        if path.name not in {"object.json", "scene.json"}:
            continue
        native = read(path)
        relative = path.parent.relative_to(package / "IR")
        original = source / relative
        archived = path.parent / native["source"]["uri"]
        assert digest(original) == digest(archived) == native["source"]["sha256"], relative
        refs = []
        for ref in native.get("image_references", []):
            resolutions[ref["resolution"]] += 1
            candidates = [p for p in (source / "Images").rglob("*") if p.is_file()
                          and p.stem.casefold() == Path(ref["path"]["text"]).stem.casefold()]
            if ref["uri"]:
                assert digest(path.parent / ref["uri"]) == ref["sha256"], relative
                assert any(digest(candidate) == ref["sha256"] for candidate in candidates), relative
            refs.append({"native_path": ref["path"]["text"], "role": ref["role"],
                         "resolution": ref["resolution"], "status": ref["status"],
                         "decoded_image": ref["decoded_image"], "decode_issue": ref["decode_issue"],
                         "source_candidates_same_stem": [p.relative_to(source).as_posix() for p in candidates],
                         "image_sha256": ref["sha256"]})
        textures = []
        for material in native.get("materials", []):
            for texture in material.get("textures", []):
                statuses[texture["export_status"]] += 1
                if texture["issue"]:
                    issues[texture["issue"]] += 1
                block = texture.get("lwo2_block") or {}
                textures.append({"material": material["name"]["text"], "channel": texture["channel"],
                                 "type": texture["type"]["text"], "image_reference": texture["image_reference"],
                                 "projection": block.get("projection"), "has_envelopes": block.get("has_envelopes"),
                                 "constant_envelopes_at_native_values": block.get("constant_envelopes_at_native_values"),
                                 "status": texture["export_status"], "issue": texture["issue"]})
        owners.append({"source": relative.as_posix(), "kind": path.stem,
                       "source_sha256": native["source"]["sha256"], "references": refs, "textures": textures})
    gltfs = []
    for path in sorted((package / "gltf").rglob("*.gltf")):
        data = read(path)
        gltfs.append({"path": path.relative_to(package).as_posix(), "images": len(data.get("images", [])),
                      "textured_materials": sum("baseColorTexture" in m.get("pbrMetallicRoughness", {})
                                                for m in data.get("materials", []))})
    return {"package": str(package), "summary": {
        "objects": sum(o["kind"] == "object" for o in owners),
        "scenes": sum(o["kind"] == "scene" for o in owners),
        "owners_with_image_references": sum(bool(o["references"]) for o in owners),
        "image_references": sum(resolutions.values()), "resolution_counts": dict(resolutions),
        "decoded_image_references": sum(r["status"] == "decoded" for o in owners for r in o["references"]),
        "texture_status_counts": dict(statuses), "texture_issue_counts": dict(issues),
        "gltf_files": len(gltfs), "gltf_files_with_images": sum(bool(g["images"]) for g in gltfs),
        "gltf_image_entries": sum(g["images"] for g in gltfs),
        "gltf_png_files": len(list((package / "gltf").rglob("*.png"))),
        "source_bytes_verified": len(owners)}, "owners": owners, "gltfs": gltfs}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--converter", type=Path, required=True)
    parser.add_argument("--validator-report", type=Path, required=True)
    parser.add_argument("--blender-report", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--experiment-description", default="Experiment only duplicates Images below Objects and Scenes; converter and native sources are unchanged.")
    args = parser.parse_args()
    baseline, experiment = (audit(p, args.source) for p in (args.baseline, args.experiment))
    assert [o["source"] for o in baseline["owners"]] == [o["source"] for o in experiment["owners"]]
    images = [p for p in (args.source / "Images").rglob("*") if p.is_file()]
    referenced = {c for o in baseline["owners"] for r in o["references"] for c in r["source_candidates_same_stem"]}
    report = {"scope": "Flower texture QA. Unique object/scene IR owners; scene dependencies are not counted twice. " + args.experiment_description + " Same-stem candidates are inventory evidence, not a reimplementation of the resolver. Blender preview does not establish LightWave rendering fidelity.",
              "converter_sha256": digest(args.converter), "source": str(args.source),
              "source_image_files": len(images), "referenced_source_image_files": len(referenced),
              "source_images_without_same_stem_reference": [p.relative_to(args.source).as_posix() for p in images
                                                             if p.relative_to(args.source).as_posix() not in referenced],
              "baseline": baseline, "experiment": experiment,
              "solfinal_native_envelopes": solfinal_envelopes(args.source / "Objects/solfinal.lwo"),
              "gltf_validation": read(args.validator_report), "blender_import": read(args.blender_report)}
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"baseline": baseline["summary"], "experiment": experiment["summary"],
                      "source_image_files": len(images), "referenced_source_image_files": len(referenced)}, indent=2))


if __name__ == "__main__":
    main()
