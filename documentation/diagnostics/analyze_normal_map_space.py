"""Read-only feasibility probe: compare image vectors with exported smooth normals.

Requires numpy and Pillow for research only. Reads this converter's static glTF
and its source normal image; writes metrics, never a converted texture. It does
not identify a world-space bake transform or reproduce MikkTSpace.
"""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
from urllib.parse import unquote

import numpy as np
from PIL import Image


def unit(v):
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-15)


def angles(a, b):
    return np.degrees(np.arccos(np.clip(np.sum(a * b, axis=-1), -1, 1)))


def stats(values):
    return {"mean": float(np.mean(values)), "p50": float(np.percentile(values, 50)),
            "p95": float(np.percentile(values, 95))}


def analyze(gltf_path, image_path, material_name):
    doc = json.loads(gltf_path.read_text("utf-8"))
    buffers = [(gltf_path.parent / unquote(b["uri"])).read_bytes() for b in doc["buffers"]]

    def accessor(index):
        a = doc["accessors"][index]
        assert "sparse" not in a and not a.get("normalized", False)
        view = doc["bufferViews"][a["bufferView"]]
        dtype = np.dtype({5126: "<f4", 5125: "<u4", 5123: "<u2", 5121: "u1"}[a["componentType"]])
        width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[a["type"]]
        return np.ndarray((a["count"], width), dtype=dtype, buffer=buffers[view["buffer"]],
                          offset=view.get("byteOffset", 0) + a.get("byteOffset", 0),
                          strides=(view.get("byteStride", width * dtype.itemsize), dtype.itemsize)).copy()

    image = np.asarray(Image.open(image_path).convert("RGB"), dtype=np.float64) / 255
    height, width = image.shape[:2]
    coverage = np.zeros((height, width), dtype=np.uint32)
    image_vectors, smooth_vectors = [], []
    triangles = degenerate = outside = 0
    for mesh in doc["meshes"]:
        for primitive in mesh["primitives"]:
            if doc["materials"][primitive["material"]]["name"] != material_name:
                continue
            assert primitive.get("mode", 4) == 4
            attributes = primitive["attributes"]
            uv = accessor(attributes["TEXCOORD_0"]).astype(np.float64)
            normals = accessor(attributes["NORMAL"]).astype(np.float64)
            # Undo the converter's object-local Z reflection; do not apply a node
            # matrix, because this probe compares the LightWave object hypothesis.
            normals[:, 2] *= -1
            indices = accessor(primitive["indices"]).ravel() if "indices" in primitive else np.arange(len(uv))
            for ids in indices.reshape(-1, 3):
                triangles += 1
                p = uv[ids] * [width, height]
                if np.any(p < 0) or np.any(p > [width, height]):
                    outside += 1
                    continue
                a, b, c = p
                denominator = (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])
                if abs(denominator) < 1e-10:
                    degenerate += 1
                    continue
                low = np.maximum(np.ceil(p.min(axis=0) - .5).astype(int), 0)
                high = np.minimum(np.floor(p.max(axis=0) - .5).astype(int), [width-1, height-1])
                x, y = np.meshgrid(np.arange(low[0], high[0]+1), np.arange(low[1], high[1]+1))
                q = np.stack((x+.5, y+.5), axis=-1)
                v = ((q[..., 0]-a[0])*(c[1]-a[1]) - (q[..., 1]-a[1])*(c[0]-a[0])) / denominator
                w = ((b[0]-a[0])*(q[..., 1]-a[1]) - (b[1]-a[1])*(q[..., 0]-a[0])) / denominator
                bary = np.stack((1-v-w, v, w), axis=-1)
                inside = np.all(bary > 1e-7, axis=-1)
                coverage[y[inside], x[inside]] += 1
                # Exclude a barycentric border from orientation scoring. Keep all
                # strict interior pixels for the separate overlap/coverage audit.
                interior = np.all(bary > .03, axis=-1)
                if not np.any(interior):
                    continue
                image_vectors.append(2*image[y[interior], x[interior]]-1)
                smooth_vectors.append(unit(bary[interior] @ normals[ids]))
    if not image_vectors:
        raise ValueError("No covered UV-interior samples for this material")
    raw = np.concatenate(image_vectors)
    smooth = np.concatenate(smooth_vectors)
    lengths = np.linalg.norm(raw, axis=1)
    valid = (lengths > .7) & (lengths < 1.3)
    if not np.any(valid):
        raise ValueError("No approximately unit-length image vectors in the scored region")
    vectors, smooth = unit(raw[valid]), smooth[valid]
    candidates = []
    for permutation in itertools.permutations(range(3)):
        for signs in itertools.product((-1, 1), repeat=3):
            angle = angles(vectors[:, permutation]*signs, smooth)
            candidates.append({"rgb_permutation": list(permutation), "signs": list(signs),
                               "angle_degrees": stats(angle), "within_30_degrees": float(np.mean(angle < 30))})
    candidates.sort(key=lambda row: row["angle_degrees"]["mean"])
    # Tangent normals normally point near +Z regardless of the mesh orientation.
    # This is a competing plausibility score, not proof of their tangent basis.
    tangent_angle = np.degrees(np.arccos(np.clip(vectors[:, 2], -1, 1)))
    return {
        "gltf": str(gltf_path), "image": str(image_path), "material": material_name,
        "gltf_sha256": hashlib.sha256(gltf_path.read_bytes()).hexdigest(),
        "image_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
        "size": [width, height], "triangles": triangles, "uv_degenerate_triangles": degenerate,
        "out_of_unit_square_triangles": outside, "strict_interior_covered_texels": int(np.count_nonzero(coverage)),
        "overlapping_strict_interior_texels": int(np.count_nonzero(coverage > 1)),
        "scoring_samples": len(raw), "unit_length_accepted_samples": int(valid.sum()),
        "decoded_length": stats(lengths), "best_object_coordinate_candidates": candidates[:5],
        "direct_rgb_object_angle_degrees": stats(angles(vectors, smooth)),
        "object_normal_same_hemisphere_fraction": float(np.mean(np.sum(vectors*smooth, axis=1) > 0)),
        "tangent_positive_z_fraction": float(np.mean(vectors[:, 2] > 0)),
        "tangent_flat_direction_angle_degrees": stats(tangent_angle),
        "smooth_normal_mean_vector_length": float(np.linalg.norm(smooth.mean(axis=0))),
        "interpretation": "Heuristic evidence for object-aligned vectors versus conventional tangent RGB. A world bake with identity/equivalent orientation is indistinguishable. No production conversion or MikkTSpace tangents generated.",
        "sampling": "Nearest decoded JPEG sample at pixel centers inside selected UV triangles; no sRGB transfer. Orientation scores exclude a 3% barycentric border and decoded lengths outside (0.7,1.3). Overlapping samples are not deduplicated for scoring. UVs outside [0,1] are reported and skipped.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gltf", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--material", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = analyze(args.gltf, args.image, args.material)
    args.report.write_bytes((json.dumps(report, indent=2)+"\n").encode("utf-8"))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
