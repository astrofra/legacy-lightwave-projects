"""Run the official Khronos validator without placing reports among exports."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input",type=Path)
    parser.add_argument("--validator",type=Path,required=True)
    parser.add_argument("--report",type=Path,required=True)
    parser.add_argument("--details",type=Path)
    args = parser.parse_args()
    root = args.input.resolve()
    files = sorted(root.rglob("*.gltf")) if root.is_dir() else [root]
    if not files: parser.error("No glTF files found")
    base = root if root.is_dir() else root.parent

    def validate(path):
        result = subprocess.run([str(args.validator.resolve()),"--stdout","--validate-resources",str(path)],capture_output=True,encoding="utf-8",timeout=120)
        data = json.loads(result.stdout)
        if args.details:
            output = args.details / path.relative_to(base).with_suffix(".report.json")
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        return path,result.returncode,data

    totals, codes, versions = Counter(),Counter(),set()
    diagnostics=[]
    with ThreadPoolExecutor(max_workers=4) as workers:
        for path,exit_code,data in workers.map(validate,files):
            versions.add(data["validatorVersion"])
            for kind in ("numErrors","numWarnings","numInfos","numHints"):
                totals[kind] += data["issues"][kind]
            if exit_code and not data["issues"]["numErrors"]:
                raise RuntimeError(f"Validator exited {exit_code} without an error report: {path}")
            for message in data["issues"]["messages"]:
                codes[message["code"]] += 1
                diagnostics.append({"file":path.relative_to(base).as_posix(),**message})
    report={"input":str(root),"validator_versions":sorted(versions),"validator_sha256":hashlib.sha256(args.validator.read_bytes()).hexdigest(),"files_checked":len(files),"resource_validation":True,"counts":dict(totals),"issue_codes":dict(codes),"diagnostics":diagnostics,"passed":totals["numErrors"]==0,"scope":"Official Khronos glTF structural and resource validation. This does not establish LightWave rendering fidelity."}
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k!="diagnostics"},indent=2))
    return 1 if totals["numErrors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
