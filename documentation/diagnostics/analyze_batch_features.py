"""Read an existing batch and summarize feature gaps without reconverting it."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import subprocess


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def analyze(batch, probe=None):
    report = read(batch / "batch-report.json")
    metrics = defaultdict(lambda: {"conversions": 0, "total": 0, "examples": []})
    issues = defaultdict(lambda: defaultdict(list))
    objects, seen_paths, scenes = {}, set(), []
    texture_issues, image_issues = defaultdict(dict), defaultdict(dict)
    missing_objects, plugin_scenes = defaultdict(list), defaultdict(set)
    image_states, image_resolutions, texture_states, formats, maps = Counter(), Counter(), Counter(), Counter(), Counter()
    generators, scene_versions, auto_states, rig_states = Counter(), Counter(), Counter(), Counter()
    logs, warnings = Counter(), []
    source_root = Path(report["content"])

    def label(path):
        try:
            return Path(path).relative_to(source_root).as_posix()
        except ValueError:
            return str(path)

    def images(owner, data, source):
        for index, image in enumerate(data.get("image_references", [])):
            key = (owner, index)
            image_states[(key, image["status"])] = 1
            image_resolutions[(key, image.get("resolution", "unknown"))] = 1
            for field in ("issue", "decode_issue"):
                if image.get(field):
                    image_issues[field + ": " + image[field]][key] = {
                        "source": source, "reference": image["path"]["text"]}

    for entry in report["files"]:
        if not entry.get("manifest"):
            continue
        path = batch / entry["manifest"]
        data = read(path); source = entry["source"]
        generators[data["generator"]] += 1
        if entry.get("log"):
            log = batch / entry["log"]
            if log.is_file():
                logs["read"] += 1
                text = log.read_text(encoding="utf-8", errors="replace")
                logs["with_error_text"] += '"status":"error"' in text or "Traceback (most recent call last)" in text
            else:
                warnings.append("Missing log: " + entry["log"])
        for key, value in data.items():
            if type(value) is int and value > 0:
                metric = metrics[key]; metric["conversions"] += 1; metric["total"] += value
                if len(metric["examples"]) < 5: metric["examples"].append(source)
            if key.endswith("_issue") and isinstance(value, str) and value:
                issues[key][value].append(source)
        auto = data.get("autonomous_animation", {})
        auto_states[auto.get("status", "absent")] += 1
        if auto.get("issue"): issues["autonomous_animation"][auto["issue"]].append(source)
        for rig in data.get("gltf_rigs", []):
            key = f"{rig['status']} / written={bool(rig.get('gltf'))} / animated={bool(rig.get('animated'))}"
            rig_states[key] += 1
            if rig.get("issue"): issues["rig"][rig["issue"]].append(source)
        if data.get("scene"):
            scene = read(path.parent / data["scene"])
            scene_versions[str(scene["version"])] += 1
            scenes.append({"source": source, "version": scene["version"],
                           "scene_gltf": bool(data.get("scene_gltf")),
                           "animated_rigs": sum(bool(r.get("animated")) for r in data.get("gltf_rigs", [])),
                           "autonomous_status": auto.get("status"), "autonomous_issue": auto.get("issue"),
                           "ordinary_animation_channels": data.get("gltf_animation_channels", 0)})
            images(scene["source"]["sha256"], scene, source)
            for node in scene["nodes"]:
                if node.get("object_path", {}).get("text") and node["asset_index"] is None:
                    missing_objects[node.get("resolution", "unknown")].append({
                        "scene": source, "reference": node["object_path"]["text"], "issue": node.get("issue", "")})
            for plugin in scene.get("plugins", []):
                parts = plugin["name"]["text"].split(maxsplit=2)
                if len(parts) == 3: plugin_scenes[parts[0] + " / " + parts[2]].add(source)
        for asset in data["assets"]:
            opath = (path.parent / asset["uri"]).resolve()
            if opath in seen_paths: continue
            seen_paths.add(opath)
            obj = read(opath); sha = obj["source"]["sha256"]; name = label(obj["source"]["path"])
            if sha not in objects:
                objects[sha] = {"source": name, "source_path": obj["source"]["path"], "sha256": sha,
                                "source_copy": str(opath.parent / obj["source"]["uri"]),
                                "format": obj["format"], "points": obj["positions"]["count"],
                                "polygons": obj["primitives"]["count"]}
                formats[obj["format"]] += 1
                maps.update(set(m["type"] for m in obj.get("maps", [])))
            images(sha, obj, name)
            for mi, material in enumerate(obj["materials"]):
                for ti, texture in enumerate(material["textures"]):
                    key = (sha, mi, ti)
                    texture_states[(key, texture["export_status"])] = 1
                    if texture.get("issue"):
                        texture_issues[texture["issue"]][key] = {
                            "source": name, "material": material["name"]["text"],
                            "channel": texture["channel"], "type": texture["type"]["text"]}

    def grouped(records):
        return [{"issue": issue, "occurrences": len(rows), "unique_sources": len(set(rows)), "examples": list(dict.fromkeys(rows))[:5]}
                for issue, rows in sorted(records.items(), key=lambda item: (-len(item[1]), item[0]))]

    def object_grouped(records):
        return [{"issue": issue, "records": len(rows), "unique_owners": len({key[0] for key in rows}),
                 "examples": list(rows.values())[:5]}
                for issue, rows in sorted(records.items(), key=lambda item: (-len(item[1]), item[0]))]

    result = {"batch": str(batch), "batch_report_sha256": hashlib.sha256((batch / "batch-report.json").read_bytes()).hexdigest(),
              "batch_counts": report["counts"], "options": report["options"], "generators": dict(generators),
              "logs": dict(logs), "warnings": warnings, "conversions": sum(generators.values()),
              "scenes": len(scenes), "scene_versions": dict(scene_versions),
              "scenes_with_scene_gltf": sum(s["scene_gltf"] for s in scenes),
              "unique_object_hashes": len(objects), "unique_object_formats": dict(formats),
              "unique_objects_with_map_type": dict(maps), "metrics_per_conversion_not_deduplicated": dict(metrics),
              "issues": {key: grouped(values) for key, values in issues.items()},
              "autonomous_states": dict(auto_states), "rig_states": dict(rig_states),
              "texture_states_unique_owner_record_state": dict(Counter(key[1] for key in texture_states)),
              "image_states_unique_owner_record_state": dict(Counter(key[1] for key in image_states)),
              "image_resolutions_unique_owner_record_resolution": dict(Counter(key[1] for key in image_resolutions)),
              "texture_issues": object_grouped(texture_issues), "image_issues": object_grouped(image_issues),
              "missing_object_references": {key: {"instances": len(rows), "scenes": len({r['scene'] for r in rows}), "examples": rows[:8]} for key, rows in missing_objects.items()},
              "plugins": [{"plugin": name, "scenes": len(values), "examples": sorted(values)[:5]}
                          for name, values in sorted(plugin_scenes.items(), key=lambda item: (-len(item[1]), item[0]))],
              "scene_results": scenes,
              "counting": "Conversion counters include repeated assets and scene instances; never add OBJ and glTF totals. Objects are deduplicated by native SHA256. Texture/image records use owner SHA256 plus record index and observed issue/status; one record can have different resolution outcomes in different packages. Issues can overlap and expose only the first blocking cause."}
    if probe:
        sources = list(objects.values())
        for obj in sources:
            if hashlib.sha256(Path(obj["source_copy"]).read_bytes()).hexdigest() != obj["sha256"]:
                raise ValueError("Archived source checksum mismatch: " + obj["source"])
        run = subprocess.run([str(probe.resolve())], input="".join(o["source_copy"]+"\n" for o in sources),
                             text=True, encoding="utf-8", capture_output=True, timeout=600)
        if run.returncode: raise RuntimeError(run.stderr or "Triangulation probe failed")
        rows = [json.loads(line) for line in run.stdout.splitlines()]
        if len(rows) != len(sources): raise ValueError("Incomplete triangulation probe")
        reasons = Counter(); affected = Counter(); ranked = []
        for obj, row in zip(sources, rows):
            if "error" in row: raise ValueError(row)
            reasons.update(i["reason"] for i in row["issues"])
            affected.update(set(i["reason"] for i in row["issues"]))
            if row["omitted"]:
                ranked.append({"source": obj["source"], "sha256": obj["sha256"],
                               "faces": row["faces"], "omitted": row["omitted"],
                               "reasons": dict(Counter(i["reason"] for i in row["issues"]))})
        result["triangulation_probe"] = {"binary_sha256": hashlib.sha256(probe.read_bytes()).hexdigest(),
            "scope": "Current C tessellator on each distinct SHA256-verified archived source.bin, FACE polygons with at least 3 corners; excludes patch cages and LWOB detail polygons. Read-only, no packages written.",
            "unique_objects_checked": len(rows), "affected_unique_objects": len(ranked),
            "source_faces": sum(r["faces"] for r in rows), "omitted_source_faces": sum(r["omitted"] for r in rows),
            "reasons": [{"reason": key, "faces": count, "objects": affected[key]} for key, count in reasons.most_common()],
            "affected_objects": sorted(ranked, key=lambda row: (-row["omitted"], row["source"]))}
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--triangulation-probe", type=Path)
    args = parser.parse_args()
    result = analyze(args.batch.resolve(), args.triangulation_probe)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2, ensure_ascii=False)+"\n", encoding="utf-8", newline="\n")
    print(json.dumps({key: result[key] for key in ("batch_counts", "conversions", "scenes", "scene_versions", "unique_object_hashes", "autonomous_states")}, indent=2))
