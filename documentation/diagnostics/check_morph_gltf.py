"""Audit every morph-bearing glTF in a batch and run the Khronos validator."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess


def morph_shape(document, path):
    errors=[]
    meshes=document.get("meshes",[])
    nodes=document.get("nodes",[])
    target_counts=[]
    for mesh_index,mesh in enumerate(meshes):
        primitives=mesh.get("primitives",[])
        count=len(mesh.get("extras",{}).get("targetNames",[]))
        if not count and primitives:
            count=len(primitives[0].get("targets",[]))
        if count:
            target_counts.append(count)
        for primitive_index,primitive in enumerate(primitives):
            targets=primitive.get("targets",[])
            if targets and len(targets)!=count:
                errors.append(f"{path}: mesh {mesh_index} primitive {primitive_index} has {len(targets)} targets, expected {count}")
            position=primitive.get("attributes",{}).get("POSITION")
            if position is None:
                continue
            point_count=document["accessors"][position]["count"]
            for target_index,target in enumerate(targets):
                for semantic,accessor in target.items():
                    if document["accessors"][accessor]["count"]!=point_count:
                        errors.append(f"{path}: mesh {mesh_index} target {target_index} {semantic} count mismatch")
    channels=0
    for animation_index,animation in enumerate(document.get("animations",[])):
        samplers=animation.get("samplers",[])
        for channel in animation.get("channels",[]):
            if channel.get("target",{}).get("path")!="weights":
                continue
            channels+=1
            node_index=channel["target"]["node"]
            mesh_index=nodes[node_index].get("mesh")
            if mesh_index is None:
                errors.append(f"{path}: animation {animation_index} targets weights on a node without a mesh")
                continue
            target_count=len(meshes[mesh_index].get("extras",{}).get("targetNames",[]))
            sampler=samplers[channel["sampler"]]
            input_count=document["accessors"][sampler["input"]]["count"]
            output=document["accessors"][sampler["output"]]
            if output["type"]!="SCALAR" or output["count"]!=input_count*target_count:
                errors.append(f"{path}: animation {animation_index} weight output is {output['type']}[{output['count']}], expected SCALAR[{input_count*target_count}]")
    for node_index,node in enumerate(nodes):
        if "weights" not in node or "mesh" not in node:
            continue
        target_count=len(meshes[node["mesh"]].get("extras",{}).get("targetNames",[]))
        if len(node["weights"])!=target_count:
            errors.append(f"{path}: node {node_index} has {len(node['weights'])} weights, expected {target_count}")
    return target_counts,channels,errors


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch",type=Path,required=True)
    parser.add_argument("--validator",type=Path,required=True)
    parser.add_argument("--report",type=Path,required=True)
    args=parser.parse_args()
    batch=args.batch.resolve(); validator=args.validator.resolve()
    candidates=[]; shape_errors=[]; documents=[]; generators=Counter()
    for path in sorted((batch/"packages").rglob("*.gltf")):
        document=json.loads(path.read_text("utf-8"))
        target_counts,channels,errors=morph_shape(document,path.relative_to(batch).as_posix())
        if not target_counts and not channels:
            continue
        candidates.append(path); shape_errors.extend(errors); generators[document["asset"]["generator"]]+=1
        documents.append(dict(file=path.relative_to(batch).as_posix(),meshes_with_targets=len(target_counts),
                              targets=sum(target_counts),maximum_targets=max(target_counts,default=0),weight_animation_channels=channels))

    def validate(path):
        result=subprocess.run([str(validator),"--stdout","--validate-resources",str(path)],
                              capture_output=True,encoding="utf-8",timeout=120)
        data=json.loads(result.stdout)
        return path,result.returncode,data

    counts=Counter(); codes=Counter(); versions=set(); failures=[]
    with ThreadPoolExecutor(max_workers=4) as workers:
        for path,exit_code,data in workers.map(validate,candidates):
            versions.add(data["validatorVersion"])
            for key in ("numErrors","numWarnings","numInfos","numHints"):
                counts[key]+=data["issues"][key]
            for issue in data["issues"].get("messages",[]):
                codes[issue["code"]]+=1
                if issue["severity"]<=1:
                    failures.append(dict(file=path.relative_to(batch).as_posix(),**issue))
            if exit_code and not data["issues"]["numErrors"]:
                shape_errors.append(f"{path}: validator exited {exit_code} without reporting an error")

    plugin_status=Counter(); manifest_issues=0; manifests_with_issues=0
    for path in (batch/"packages").rglob("scene.json"):
        document=json.loads(path.read_text("utf-8"))
        for plugin in document.get("plugins",[]):
            plugin_status[plugin.get("status","missing")]+=1
    for path in (batch/"packages").rglob("manifest.json"):
        try:
            document=json.loads(path.read_text("utf-8"))
        except (UnicodeDecodeError,json.JSONDecodeError):
            continue
        issue_count=document.get("gltf_morph_issues",0)
        if issue_count:
            manifests_with_issues+=1; manifest_issues+=issue_count

    report=dict(
        batch=str(batch),
        converter_sha256=None,
        converter_generators=dict(generators),
        morph_documents=len(candidates),
        morph_animated_documents=sum(bool(d["weight_animation_channels"]) for d in documents),
        morph_animation_channels=sum(d["weight_animation_channels"] for d in documents),
        maximum_targets_on_one_mesh=max((d["maximum_targets"] for d in documents),default=0),
        morph_mixer_plugins_interpreted=plugin_status["morph-mixer-interpreted"],
        manifests_with_morph_issues=manifests_with_issues,
        manifest_morph_issue_count=manifest_issues,
        internal_shape_errors=shape_errors,
        validator_versions=sorted(versions),
        validator_sha256=hashlib.sha256(validator.read_bytes()).hexdigest(),
        validator_counts=dict(counts),
        validator_issue_codes=dict(codes),
        validator_failures=failures,
        passed=not shape_errors and not counts["numErrors"],
        documents=documents,
        scope="Structural glTF morph checks and official Khronos resource validation. This does not establish visual equivalence with LightWave.",
    )
    batch_report=batch/"batch-report.json"
    if batch_report.is_file():
        converter=Path(json.loads(batch_report.read_text("utf-8"))["converter"])
        if converter.is_file():
            report["converter_sha256"]=hashlib.sha256(converter.read_bytes()).hexdigest()
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({key:value for key,value in report.items() if key not in ("documents","validator_failures")},indent=2))
    return 0 if report["passed"] else 1


if __name__=="__main__":
    raise SystemExit(main())
