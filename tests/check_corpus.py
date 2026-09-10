"""Compare the C reader to the independent, checked-in Python inventory."""
import argparse
import collections
import concurrent.futures
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    inventory = json.loads((root / "documentation/diagnostics/inventory.json").read_text("utf-8"))
    records = [r for r in inventory if r["kind"] in ("FORM LWOB", "FORM LWO2", "FORM PST_", "LWSC 1", "LWSC 3")]
    executable = str(args.executable.resolve())

    def check(record):
        result = subprocess.run([executable, "inspect", str(root / record["path"])], capture_output=True, encoding="utf-8", timeout=60)
        if result.returncode:
            return record, None, [result.stderr.strip()]
        actual = json.loads(result.stdout)
        expected = {"sha256": record["sha256"]}
        obj = record.get("preset_payloads", [record])[0]
        if "geometry" in obj:
            geo = obj["geometry"]
            expected.update(points=geo["points"], primitives=sum(geo["polygon_types"].values()), maps=len(obj["maps"]), materials=len(obj["surfaces"]), detail_polygons=geo["detail_polygons"], repeated_primitives=geo["records_with_repeated_point_indices"])
            expected["layers"] = len(obj["layers"]) or int(bool(geo["points"]))
        else:
            expected.update(version=int(record["kind"][-1]), object_loads=len(record["object_paths"]), bones=record["keywords"].get("AddBone", 0), plugins=len(record["plugins"]))
        errors = [f"{key}: expected {value}, got {actual.get(key)}" for key, value in expected.items() if actual.get(key) != value]
        return record, actual, errors

    failures, totals = [], collections.Counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for index, (record, actual, errors) in enumerate(pool.map(check, records), 1):
            totals[record["kind"]] += 1
            if actual:
                for key in ("points", "primitives", "maps", "object_loads", "bones", "keys", "invalid_map_references"):
                    totals[key] += actual.get(key, 0)
            if errors:
                failures.append({"path": record["path"], "errors": errors})
                print(record["path"], errors, flush=True)
            if index % 200 == 0:
                print(f"Checked {index}/{len(records)}", flush=True)
    report = {"generator_version": subprocess.check_output([executable, "--version"], text=True).strip(), "checked": len(records), "passed": len(records)-len(failures), "totals": dict(totals), "failures": failures, "scope": "Reader counts and source SHA-256 compared with independent inventory; not a visual fidelity test."}
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "failures"}, ensure_ascii=False, indent=2))
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
