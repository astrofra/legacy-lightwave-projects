#!/usr/bin/env python3
"""Read-only structural inventory for the IK/FK/morph roadmap; no evaluation.

Run: python -X utf8 documentation/diagnostics/scan_redline_animation.py
Uses the existing FORM scanner. Writes only redline-animation-inventory.json.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from scan_dataset import classify, inspect_object


ROOT = Path(__file__).resolve().parents[2]
CONTENT = ROOT / "content/demo-redline-assets-main"
OUTPUT = Path(__file__).with_name("redline-animation-inventory.json")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def scene_features(data):
    text = data.decode("latin-1")
    controllers = Counter(re.findall(r"(?m)^\s*([HPB]Controller)\s+(\d+)\s*$", text))
    plugins = re.findall(r"(?m)^Plugin\s+(\S+)\s+\d+\s+(\S+)", text)
    envelopes = re.findall(r"\{\s*Envelope\s*\n([^{}]*)\}", text)
    varying = 0
    for envelope in envelopes:
        values = re.findall(r"(?m)^\s*Key\s+(\S+)\s+", envelope)
        varying += len({float(v) for v in values}) > 1
    declarations = {}
    for key in ("GoalObject", "IKAnchor", "FullTimeIK", "MorphTarget", "MorphAmount", "AddBone", "ObjectMotion"):
        declarations[key] = len(re.findall(r"(?m)^" + key + r"(?:\s|$)", text))
    bounds = {}
    for key in ("FirstFrame", "LastFrame", "PreviewFirstFrame", "PreviewLastFrame", "FramesPerSecond"):
        match = re.search(r"(?m)^" + key + r"\s+([^\r\n]+)", text)
        if match:
            bounds[key] = match[1]
    object_paths = re.findall(r"(?m)^LoadObject(?:Layer\s+\d+)?\s+([^\r\n]+)", text)
    morph_forms = re.findall(r'\{\s*MorfForm\s*\n\s*"([^"]+)"[^{}]*\{\s*Envelope\s*\n([^{}]*)\}', text)
    varying_morph_forms = [name for name, body in morph_forms
                           if len({float(v) for v in re.findall(r"(?m)^\s*Key\s+(\S+)", body)}) > 1]
    morph_amounts = re.findall(r"MorphAmount\s+\(envelope\)\s*\{\s*Envelope\s*\n([^{}]*)\}", text)
    return {
        "format": text.splitlines()[:2],
        "bounds_as_written": bounds,
        "declarations": declarations,
        "controllers": {f"{axis} {mode}": n for (axis, mode), n in sorted(controllers.items())},
        "ik_controller_declarations": sum(n for (_, mode), n in controllers.items() if mode == "3"),
        "other_non_keyframe_controller_declarations": sum(n for (_, mode), n in controllers.items() if mode not in ("0", "3")),
        "concatenated_stiffness_controller_lines": re.findall(r"(?m)^\s*[HPB]JointStiffness[^\r\n]*[HPB]Controller[^\r\n]*", text),
        "plugins": dict(sorted(Counter(f"{cls}:{name}" for cls, name in plugins).items())),
        "morph_mixer_instances": sum(name == "LW_MorphMixer" for _, name in plugins),
        "morph_form_envelopes": len(morph_forms),
        "varying_morph_form_envelope_names_including_instances": varying_morph_forms,
        "morph_amount_envelope_keys_value_time": [
            [[float(value), float(time)] for value, time in re.findall(r"(?m)^\s*Key\s+(\S+)\s+(\S+)", body)]
            for body in morph_amounts],
        "morph_options_as_written": re.findall(r"(?m)^(?:MTSEMorphing|MorphSurfaces)\s+[^\r\n]+", text),
        "varying_key_value_envelopes_all_channels": varying,
        "object_paths_as_written": object_paths,
    }


def main():
    files, scenes, objects = [], [], []
    for path in sorted((p for p in CONTENT.rglob("*") if p.is_file()), key=lambda p: p.relative_to(CONTENT).as_posix()):
        data = path.read_bytes()
        record = {"path": path.relative_to(CONTENT).as_posix(), "bytes": len(data), "sha256": digest(data)}
        files.append(record)
        if path.suffix.lower() == ".lws":
            scenes.append({**record, **scene_features(data)})
        elif path.suffix.lower() == ".lwo":
            audit = {"path": record["path"], "project": CONTENT.name, "kind": classify(data), "warnings": []}
            inspect_object(data, audit, [])
            objects.append({**record, "kind": audit["kind"], "warnings": audit["warnings"],
                            "morph_maps": [m for m in audit.get("maps", []) if m["type"] in ("MORF", "SPOT")]})
    basenames = {Path(f["path"]).name.casefold() for f in files}
    for scene in scenes:
        scene["object_reference_basenames_absent_from_corpus"] = sorted({
            value.strip('"').replace("\\", "/").rsplit("/", 1)[-1]
            for value in scene["object_paths_as_written"]
            if value.strip('"').replace("\\", "/").rsplit("/", 1)[-1].casefold() not in basenames})
    duplicates = defaultdict(list)
    for scene in scenes:
        duplicates[scene["sha256"]].append(scene["path"])
    morph_objects = [o for o in objects if o["morph_maps"]]
    summary = {
        "files": len(files), "bytes": sum(f["bytes"] for f in files),
        "scenes": len(scenes), "unique_scene_sha256": len(duplicates),
        "objects": len(objects), "unique_object_sha256": len({o["sha256"] for o in objects}),
        "scenes_with_ik_controller_3": sum(bool(s["ik_controller_declarations"]) for s in scenes),
        "scenes_with_goal_object": sum(bool(s["declarations"]["GoalObject"]) for s in scenes),
        "scenes_with_other_non_keyframe_controller": sum(bool(s["other_non_keyframe_controller_declarations"]) for s in scenes),
        "scenes_with_morph_target": sum(bool(s["declarations"]["MorphTarget"]) for s in scenes),
        "scenes_with_morph_mixer": sum(bool(s["morph_mixer_instances"]) for s in scenes),
        "scenes_with_varying_morph_form_key_values": sum(bool(s["varying_morph_form_envelope_names_including_instances"]) for s in scenes),
        "scenes_with_object_reference_basenames_absent_from_corpus": sum(bool(s["object_reference_basenames_absent_from_corpus"]) for s in scenes),
        "scenes_with_add_bone": sum(bool(s["declarations"]["AddBone"]) for s in scenes),
        "scenes_with_concatenated_stiffness_controller": sum(bool(s["concatenated_stiffness_controller_lines"]) for s in scenes),
        "objects_with_morph_maps": len(morph_objects),
        "unique_object_sha256_with_morph_maps": len({o["sha256"] for o in morph_objects}),
        "morph_map_records_including_duplicate_files": sum(len(o["morph_maps"]) for o in morph_objects),
        "object_scanner_warnings": sum(len(o["warnings"]) for o in objects),
    }
    report = {
        "schema_version": "0.1", "date_utc": datetime.now(timezone.utc).date().isoformat(),
        "content_root": CONTENT.relative_to(ROOT).as_posix(),
        "scope": "Structural declarations only; no conversion, reference resolution, IK solving, plugin execution or deformation validation.",
        "method": {
            "scene_scan": "Anchored LWSC declaration regexes, full numeric H/P/B controller statements, plugin declarations and key-value envelope variation. Concatenated lines are reported separately, not repaired.",
            "object_scan": "Existing FORM chunk scanner; MORF/SPOT map records, names, layers and entry counts.",
            "duplicates": "Exact source SHA-256. Near-duplicate scenes and shared rig families require further grouping before a held-out split.",
            "counting": "Scene feature categories overlap. Declarations and changing key values do not prove effective activation or evaluated motion. All-channel envelopes include camera, light and plugin channels.",
            "references": "Missing-basename check only, case-insensitive over this corpus. A present basename is not a qualified path, object revision, layer or topology match; no converter resolver is invoked.",
        },
        "scanner_sha256": digest(Path(__file__).read_bytes()),
        "form_scanner_sha256": digest(Path(__file__).with_name("scan_dataset.py").read_bytes()),
        "inventory_sha256": digest(json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")),
        "summary": summary,
        "duplicate_scene_groups": [{"sha256": sha, "paths": paths} for sha, paths in sorted(duplicates.items()) if len(paths) > 1],
        "files": files, "scenes": scenes, "objects": objects,
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
