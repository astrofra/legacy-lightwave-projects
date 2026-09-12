"""Compare Quatuor exports, allowing relocation and explicitly omitted rest rigs."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from check_output_layout import check as check_layout


def read(path):
    return json.loads(path.read_text("utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(batch, baseline):
    batch, baseline = batch.resolve(), baseline.resolve()
    result = check_layout(batch)
    records = {r["source"]: r for r in read(batch / "batch-report.json")["files"]
               if r["source"].startswith("quatuor/") and r["status"] != "skipped"}
    previous = {r["source"]: r for r in read(baseline / "batch-report.json")["files"]
                if r["source"].startswith("quatuor/") and r["status"] != "skipped"}
    assert records and records.keys() == previous.keys(), "Different source sets"
    gltfs, natives, objects, entries = set(), set(), set(), []
    omitted_rigs, generators = [], set()

    def compare_gltf(new, old):
        new, old = new.resolve(), old.resolve()
        if new in gltfs:
            return
        left, right = read(new), read(old)
        generators.add((right["asset"].get("generator"), left["asset"].get("generator")))
        # Version stamps and relocated buffer filenames do not alter geometry.
        assert digest(new.with_suffix(".bin")) == digest(old.with_suffix(".bin")), new
        for data in (left, right):
            data["asset"].pop("generator", None)
            for buffer in data.get("buffers", []):
                buffer.pop("uri")
        assert left == right, new
        gltfs.add(new)

    def compare_native(new, old):
        new, old = new.resolve(), old.resolve()
        if new in natives:
            return
        data = read(new)
        buffer = data.get("buffer", data.get("animation_buffer"))["uri"]
        for name in (new.name, "source.bin", buffer):
            assert digest(new.parent / name) == digest(old.parent / name), new.parent / name
        natives.add(new)

    def compare_obj(new, old):
        new, old = new.resolve(), old.resolve()
        if new in objects:
            return
        def body(path):
            return b"".join(line for line in path.read_bytes().splitlines(keepends=True) if not line.startswith(b"mtllib "))
        assert body(new) == body(old), new
        assert digest(new.with_suffix(".mtl")) == digest(old.with_suffix(".mtl")), new
        objects.add(new)

    for source, record in records.items():
        old = previous[source]
        assert record["status"] == old["status"], source
        relative = Path(source).relative_to("quatuor").as_posix()
        prefix = "packages/quatuor/"
        assert record["manifest"] == prefix + "IR/" + relative + "/manifest.json", source
        assert record["log"] == "logs/quatuor/" + relative + ".log", source
        for kind in ("obj", "gltf"):
            assert record[kind] == prefix + kind + "/" + relative + "." + kind, source
        new_path, old_path = batch / record["manifest"], baseline / old["manifest"]
        new, prior = read(new_path), read(old_path)
        compare_obj(batch / record["obj"], baseline / old["obj"])
        compare_gltf(batch / record["gltf"], baseline / old["gltf"])
        if new["scene"]:
            compare_native(new_path.parent / new["scene"], old_path.parent / prior["scene"])
        old_assets = {a["source_path"]: a for a in prior["assets"]}
        assert {a["source_path"] for a in new["assets"]} == old_assets.keys(), source
        for asset in new["assets"]:
            before = old_assets[asset["source_path"]]
            compare_native(new_path.parent / asset["uri"], old_path.parent / before["uri"])
            compare_obj((new_path.parent / asset["obj"]).resolve(), (old_path.parent / before["obj"]).resolve())
            compare_gltf((new_path.parent / asset["gltf"]).resolve(), (old_path.parent / before["gltf"]).resolve())
        for key in ("gltf_rigs", "gltf_animations"):
            assert len(new.get(key, [])) == len(prior.get(key, [])), source
            for after, before in zip(new.get(key, []), prior.get(key, [])):
                assert after["owner_item"] == before["owner_item"], source
                if after["gltf"]:
                    compare_gltf((new_path.parent / after["gltf"]).resolve(), (old_path.parent / before["gltf"]).resolve())
                elif before["gltf"]:
                    assert key == "gltf_rigs" and new["gltf_rig_policy"] == "skins", source
                    assert after["status"] == before["status"] == "skeleton-only", source
                    assert after["export_status"] == "omitted-by-policy", source
                    assert all(after[field] == before[field] for field in ("bones", "issue", "procedural_bones_not_evaluated")), source
                    paths = [(old_path.parent / before[field]).resolve() for field in ("gltf", "gltf_bin")]
                    omitted_rigs.append({"source": source, "owner_item": after["owner_item"], "bytes": sum(p.stat().st_size for p in paths)})
        entries.append({"source": source, "status": record["status"], "old_gltf": old["gltf"], "gltf": record["gltf"]})

    duplicate_names = Counter(Path(source).name for source in records)
    result.update(baseline=str(baseline), hierarchy_checked=len(records),
                  duplicate_basenames={name: count for name, count in duplicate_names.items() if count > 1},
                  unchanged_native_documents=len(natives), unchanged_obj_mtl_pairs=len(objects),
                  unchanged_gltf_with_buffers=len(gltfs), comparisons=entries,
                  omitted_rest_rigs=omitted_rigs, omitted_rest_rig_bytes=sum(r["bytes"] for r in omitted_rigs),
                  generator_changes=sorted(generators),
                  comparison_scope="Exact source, IR and binary hashes; identical OBJ except mtllib; identical MTL; identical retained glTF JSON except buffer URIs and generator version. Only explicitly omitted skeleton-only exports may disappear. Conversion statuses and source dependencies retained. No new visual-fidelity claim.")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", type=Path)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = check(args.batch, args.baseline)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.write_text(text, encoding="utf-8")
    print(text)
