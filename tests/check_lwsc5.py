"""Convert a real LWSC 5 corpus and check preservation and partial-export diagnostics."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
from urllib.parse import unquote


def native_items(raw):
    """Independent declaration/parent scan; plugin and nested block payloads are opaque."""
    items = []
    plugin_depth = block_depth = 0
    owner = None
    for line in raw.decode("latin1").splitlines()[2:]:
        words = line.split()
        if not words:
            continue
        key = words[0]
        if key == "Plugin":
            plugin_depth += 1
        if key == "EndPlugin":
            plugin_depth -= 1
            continue
        if plugin_depth:
            continue
        if key == "{":
            block_depth += 1
        if key == "}":
            block_depth -= 1
            continue
        if block_depth:
            continue
        if key in ("LoadObjectLayer", "LoadObject", "AddNullObject", "AddBone", "AddLight", "AddCamera"):
            token = 2 if key == "LoadObjectLayer" else 1
            item = {"id": int(words[token], 16), "parent": owner if key == "AddBone" else None}
            if key in ("LoadObjectLayer", "LoadObject", "AddNullObject"):
                owner = item["id"]
            if key == "LoadObjectLayer":
                item["layer_request"] = int(words[1])
            items.append(item)
        elif key == "ParentItem" and items:
            items[-1]["parent"] = int(words[1], 16) or None
    if plugin_depth or block_depth:
        raise ValueError("Unbalanced native scene blocks")
    return items


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--converter", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    root = args.input.resolve()
    converter = args.converter.resolve()
    scenes = [p for p in sorted(root.rglob("*")) if p.is_file() and p.suffix.lower() == ".lws"
              and p.read_bytes().split(None, 2)[:2] == [b"LWSC", b"5"]]
    if not scenes:
        parser.error("No LWSC 5 scenes found")
    args.output.mkdir(parents=True, exist_ok=False)
    results = []
    for source in scenes:
        raw = source.read_bytes()
        package = args.output / source.relative_to(root)
        package.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run([str(converter), "convert", str(source), "--output", str(package)],
                                capture_output=True, encoding="utf8", timeout=300)
        entry = {"source": source.relative_to(root).as_posix(), "source_sha256": hashlib.sha256(raw).hexdigest(),
                 "exit_code": result.returncode, "package": package.resolve().as_posix()}
        errors = []
        try:
            if result.returncode != 2:
                raise ValueError(f"Expected partial export (2): {result.stdout} {result.stderr}")
            manifest = json.loads((package / "manifest.json").read_text(encoding="utf8"))
            scene_path = package / unquote(manifest["scene"])
            scene = json.loads(scene_path.read_text(encoding="utf8"))
            if scene_path.with_name("source.bin").read_bytes() != raw or source.read_bytes() != raw:
                errors.append("Source scene bytes changed")
            if scene["reader_profile"] != "lwsc5-partial-0.1" or manifest["scene_format"]["support"] != "partial":
                errors.append("Missing partial reader profile")
            expected = native_items(raw)
            actual = scene["nodes"]
            if len(expected) != len(actual) or any(any(node[k] != value for k, value in item.items())
                                                  for item, node in zip(expected, actual)):
                errors.append("Native item identities, order, parenting or layers changed")
            for statement in scene["uninterpreted_statements"]:
                start, size = statement["source_offset"], statement["bytes"]
                if start < 0 or size < 1 or start + size > len(raw):
                    errors.append("Invalid uninterpreted statement source span")
                    break
            for asset in manifest["assets"]:
                original = Path(asset["source_path"]).read_bytes()
                archived = (package / unquote(asset["uri"])).with_name("source.bin").read_bytes()
                if archived != original or hashlib.sha256(original).hexdigest() != asset["id"]:
                    errors.append("Source object bytes changed")
            entry.update({"nodes": len(actual), "bones": sum(n["bone"] is not None for n in actual),
                          "keys": sum(c["keys"]["count"] for n in actual for c in n["channels"]),
                          "assets": len(manifest["assets"]), "unresolved_instances": manifest["unresolved_object_instances"],
                          "uninterpreted_statements": len(scene["uninterpreted_statements"]),
                          "inferred_root": manifest["content_root_inference"]["selected_root"],
                          "scene_obj": manifest["scene_obj"], "scene_gltf": manifest["scene_gltf"],
                          "scene_gltf_issue": manifest["scene_gltf_issue"], "gltf_files": manifest["gltf_files"],
                          "rig_issues": [r["issue"] for r in manifest["gltf_rigs"] if r["issue"]]})
        except (ValueError, OSError, KeyError) as exc:
            errors.append(str(exc))
        entry["errors"] = errors
        entry["passed"] = not errors
        results.append(entry)
        print(json.dumps({"source": entry["source"], "passed": entry["passed"], "assets": entry.get("assets"),
                          "gltf_files": entry.get("gltf_files"), "errors": errors}), flush=True)
    report = {"converter_version": subprocess.check_output([str(converter), "--version"], text=True).strip(),
              "converter_sha256": hashlib.sha256(converter.read_bytes()).hexdigest(), "scenes_checked": len(results),
              "totals": dict(sum((Counter({k: r.get(k, 0) for k in ("nodes", "bones", "keys", "assets", "gltf_files", "unresolved_instances")})
                                  for r in results), Counter())), "scenes": results,
              "passed": all(r["passed"] for r in results),
              "scope": "Partial LWSC 5 extraction; independent native IDs, parents and layer requests; source scene/object byte preservation. Does not establish animation or rendering fidelity."}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
