#!/usr/bin/env python3
"""Read-only feasibility audit; not a LightWave converter or full validator.

Run from the repository root:
  python documentation/diagnostics/scan_dataset.py
Only the selected output directory is written. No legacy software is executed.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import struct
import unicodedata
import zipfile


def label(raw):
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")  # reversible display hypothesis, not a detection


def encoding_evidence(raw):
    if raw.isascii():
        return "ascii"
    try:
        raw.decode("utf-8")
        return "non-ascii-valid-utf8"
    except UnicodeDecodeError:
        return "non-ascii-invalid-utf8"


def u16(b, p=0):
    return struct.unpack_from(">H", b, p)[0]


def u32(b, p=0):
    return struct.unpack_from(">I", b, p)[0]


def s0(b, p=0):
    end = b.index(0, p)
    nxt = end + 1 + ((end + 1 - p) & 1)
    if nxt > len(b):
        raise ValueError("missing S0 padding")
    return b[p:end], nxt


def vx(b, p):
    if b[p] == 255:
        return u32(b, p) & 0xFFFFFF, p + 4
    return u16(b, p), p + 2


def chunks(b, start=0, short=False):
    p = start
    header = 6 if short else 8
    while p < len(b):
        if len(b) - p < header:
            raise ValueError(f"partial chunk header at {p}: {len(b)-p} bytes")
        tag = b[p:p+4].decode("ascii", errors="backslashreplace")
        size = u16(b, p+4) if short else u32(b, p+4)
        end = p + header + size
        if end > len(b):
            raise ValueError(f"{tag} at {p}: declared end {end} exceeds {len(b)}")
        yield tag, b[p+header:end], p
        p = end + (size & 1)
        if p > len(b):
            raise ValueError(f"{tag}: missing odd-size padding")


def classify(b):
    if len(b) >= 12 and b[:4] == b"FORM":
        return "FORM " + label(b[8:12])
    m = re.match(br"LWSC[\r\n\s]+(\d+)", b)
    if m:
        return "LWSC " + m[1].decode("ascii")
    signatures = [(b"\xff\xd8\xff", "JPEG"), (b"8BPS", "PSD"),
                  (b"GIF8", "GIF"), (b"\x89PNG\r\n\x1a\n", "PNG"),
                  (b"II\x2a\0", "TIFF"), (b"MM\0\x2a", "TIFF"),
                  (b"PK\x03\x04", "ZIP"), (b"LWMO", "LWMO"),
                  (b"LWEN", "LWEN"), (b"BM", "BMP"),
                  (b"MZ", "PE/DOS executable"), (b"%!PS", "PostScript")]
    for magic, name in signatures:
        if b.startswith(magic):
            return name
    # TGA has no mandatory leading magic. Require a consistent, uncompressed
    # true-color header AND exact pixel payload, optionally its standard footer.
    if len(b) >= 18 and b[1] == 0 and b[2] == 2 and b[16] in (24, 32):
        w, h = struct.unpack_from("<HH", b, 12)
        end = 18 + b[0] + w * h * (b[16] // 8)
        if w and h and (end == len(b) or (end <= len(b)-26 and b.endswith(b"TRUEVISION-XFILE.\0"))):
            return "TGA (validated uncompressed layout)"
    if len(b) >= 18 and b[1] == 0 and b[2] == 10 and b[16] in (24, 32):
        w, h = struct.unpack_from("<HH", b, 12)
        p, pixels, pixel_bytes = 18+b[0], 0, b[16] // 8
        while w and h and p < len(b) and pixels < w*h:
            packet = b[p]
            n = (packet & 127) + 1
            p += 1 + (pixel_bytes if packet & 128 else n*pixel_bytes)
            pixels += n
        if w and h and pixels == w*h and (p == len(b) or (p <= len(b)-26 and b.endswith(b"TRUEVISION-XFILE.\0"))):
            return "TGA (validated RLE layout)"
    if b.startswith(b"RIFF") and len(b) >= 12:
        return "RIFF " + label(b[8:12])
    return "unclassified"  # extension remains separately recorded


def reference(rec, refs, field, raw, offset=None, kind="image", **extra):
    raw = raw.strip()
    if not raw or raw in (b"(none)", b"<none>", b"none"):
        return
    if raw.startswith(b'"') and raw.endswith(b'"'):
        raw = raw[1:-1]
    refs.append(dict(source=rec["path"], project=rec["project"], field=field,
                     raw_hex=raw.hex(), display=label(raw), kind=kind,
                     location=offset, encoding_evidence=encoding_evidence(raw), **extra))


def inspect_object(b, rec, refs):
    modern = rec["kind"] == "FORM LWO2"
    declared = u32(b, 4) + 8
    rec["form_declared_bytes"] = declared
    if declared != len(b):
        rec["warnings"].append(f"FORM declares {declared} bytes; actual {len(b)}")
    counts, subcounts, arities, types = Counter(), Counter(), Counter(), Counter()
    texture_types, projection_modes, flags = Counter(), Counter(), Counter()
    maps, layers, surfaces, shader_names = [], [], [], []
    repeated_vertex_records = 0
    points = detail_polygons = invalid_indices = 0
    current_points = 0
    used_by_point_block = []
    used = set()
    current_layer = None
    geometry = rec["geometry"] = {}

    def visit_sub(data, start, context, base):
        for tag, payload, pos in chunks(data, start, short=True):
            subcounts[context + "/" + tag] += 1
            if tag in ("CTEX", "DTEX", "STEX", "RTEX", "TTEX", "LTEX", "BTEX"):
                texture_types[label(payload.rstrip(b"\0"))] += 1
            if tag == "TFLG" and len(payload) >= 2:
                flags[str(u16(payload))] += 1
            if tag == "PROJ" and len(payload) >= 2:
                projection_modes[str(u16(payload))] += 1
            if tag == "STIL" or (not modern and tag in ("TIMG", "RIMG")):
                raw = payload.split(b"\0", 1)[0]
                reference(rec, refs, context + "/" + tag, raw, base+pos+6)
            if tag == "FUNC" or (tag == "SHDR" and not modern):
                shader_names.append(label(payload.split(b"\0", 1)[0]))
            if tag in ("BLOK", "TMAP"):
                visit_sub(payload, 0, context + "/" + tag, base+pos+6)
            elif tag in ("IMAP", "PROC", "GRAD", "SHDR") and context.endswith("BLOK"):
                _, after = s0(payload)
                visit_sub(payload, after, context + "/" + tag, base+pos+6)

    try:
        for tag, data, pos in chunks(b[:min(declared, len(b))], 12):
            counts[tag] += 1
            if tag == "LAYR":
                current_layer = u16(data)
                name, _ = s0(data, 16)
                layers.append(dict(index=current_layer, name=label(name)))
            elif tag == "PNTS":
                if counts[tag] > 1:
                    used_by_point_block.append((current_points, used))
                current_points = len(data) // 12
                used = set()
                points += current_points
                if len(data) % 12:
                    rec["warnings"].append("PNTS length is not a multiple of 12")
            elif tag == "POLS" or (not modern and tag == "PCHS"):
                p = 4 if modern else 0
                polytype = label(data[:4]) if modern else "LWOB-" + tag

                def polygon(p, detail=False, depth=0):
                    nonlocal detail_polygons, invalid_indices, repeated_vertex_records
                    if depth > 32:
                        raise ValueError("detail polygon nesting exceeds audit limit")
                    n = u16(data, p)
                    p += 2
                    n = (n & 1023) if modern else n
                    arities[str(n)] += 1
                    types[polytype] += 1
                    detail_polygons += int(detail)
                    indices = []
                    for _ in range(n):
                        if modern:
                            idx, p = vx(data, p)
                        else:
                            idx, p = u16(data, p), p + 2
                        invalid_indices += int(idx >= current_points)
                        used.add(idx)
                        indices.append(idx)
                    repeated_vertex_records += int(len(set(indices)) != len(indices))
                    if not modern:
                        surf = struct.unpack_from(">h", data, p)[0]
                        p += 2
                        if surf < 0:
                            ndetail = u16(data, p)
                            p += 2
                            for _ in range(ndetail):
                                p = polygon(p, detail=True, depth=depth+1)
                    return p

                while p < len(data):
                    p = polygon(p)
                if p != len(data):
                    raise ValueError(f"{tag} record exceeds chunk")
            elif tag in ("VMAP", "VMAD"):
                name, p = s0(data, 6)
                dim, entries = u16(data, 4), 0
                while p < len(data):
                    _, p = vx(data, p)
                    if tag == "VMAD":
                        _, p = vx(data, p)
                    p += 4 * dim
                    entries += 1
                if p != len(data):
                    raise ValueError(f"{tag} entry exceeds chunk")
                maps.append(dict(chunk=tag, type=label(data[:4]), dimension=dim,
                                 name=label(name), layer=current_layer, entries=entries))
            elif tag == "SURF":
                name, after = s0(data)
                source = b""
                if modern:
                    source, after = s0(data, after)
                surfaces.append(dict(name=label(name), raw_hex=name.hex(), source=label(source)))
                visit_sub(data, after, "SURF", pos+8)
            elif tag == "CLIP":
                visit_sub(data, 4, "CLIP", pos+8)
    except (ValueError, IndexError, struct.error) as exc:
        rec["warnings"].append(str(exc))
    used_by_point_block.append((current_points, used))
    geometry.update(points=points, polygon_arities=dict(arities), polygon_types=dict(types),
                    detail_polygons=detail_polygons, invalid_point_indices=invalid_indices,
                    records_with_repeated_point_indices=repeated_vertex_records,
                    points_not_referenced_by_POLS_or_PCHS=sum(n-len({i for i in u if i < n})
                                                              for n, u in used_by_point_block))
    rec.update(chunks=dict(counts), subchunks=dict(subcounts), layers=layers, maps=maps,
               surfaces=surfaces, texture_types=dict(texture_types), projection_modes=dict(projection_modes),
               texture_flags=dict(flags), shader_names=shader_names,
               surface_only=bool(surfaces) and not counts["PNTS"] and not counts["POLS"] and not counts["PCHS"])


def inspect_scene(b, rec, refs):
    rec["encoding_evidence"] = encoding_evidence(b)
    rec["bytes_80_9f"] = sorted(set(x for x in b if 128 <= x <= 159))
    rec["line_endings"] = dict(Counter(label(m[0]) for m in re.finditer(br"\r\n|\r|\n", b)))
    keys, plugins, paths = Counter(), [], []
    for match in re.finditer(br"[^\r\n]+", b):
        line = match[0].strip()
        fields = line.split(None, 1)
        if not fields or not re.match(br"^[A-Za-z]", fields[0]):
            continue
        key = label(fields[0])
        keys[key] += 1
        value = fields[1] if len(fields) > 1 else b""
        if key in ("LoadObject", "LoadObjectLayer"):
            layer = None
            if key == "LoadObjectLayer":
                layerraw, value = value.split(None, 1)
                layer = label(layerraw)
            reference(rec, refs, key, value, match.start(), kind="object", scene_layer=layer)
            paths.append(label(value))
        elif key in ("BGImage", "FGImage", "FGAlphaImage", "TextureImage", "ReflectionImage"):
            reference(rec, refs, key, value, match.start())
        elif key == "filename":
            reference(rec, refs, key, value, match.start(), kind="plugin-data")
        elif key == "Plugin":
            plugins.append(label(value))
    rec.update(keywords=dict(keys), plugins=plugins, object_paths=paths)


def norm(s):
    return unicodedata.normalize("NFC", s.replace("\\", "/")).casefold()


def resolve(ref, records):
    """Rank project-local suffix candidates. No candidate is a proven binding."""
    name = norm(ref["display"])
    # A drive/device colon is a boundary, not a POSIX directory separator.
    parts = [p for p in name.replace(":", "/").split("/") if p and p != "."]
    scored, outside = [], []
    for rec in records:
        if ref["kind"] == "object" and rec["kind"] not in ("FORM LWOB", "FORM LWO2"):
            continue
        if ref["kind"] == "image" and rec["kind"] not in ("JPEG", "PNG", "GIF", "TIFF", "PSD", "BMP", "FORM ILBM", "FORM PBM ", "RIFF AVI ", "TGA (validated uncompressed layout)", "TGA (validated RLE layout)"):
            continue
        target = norm(rec["path"]).split("/")
        n = 0
        for a, z in zip(reversed(parts), reversed(target)):
            if a != z:
                break
            n += 1
        if not n:
            continue
        pair = (n, rec["path"])
        (scored if rec["project"] == ref["project"] else outside).append(pair)
    if scored:
        best = max(n for n, _ in scored)
        chosen = sorted(p for n, p in scored if n == best)
        status = ("unique-suffix-candidate" if best >= 2 else "unique-basename-candidate") if len(chosen) == 1 else "ambiguous-local"
        return dict(status=status, matching_suffix_components=best, candidates=chosen)
    return dict(status="no-local-candidate", candidates=[], cross_project_candidates=sorted(p for _, p in outside))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=Path("content"))
    ap.add_argument("--output", type=Path, default=Path("documentation/diagnostics"))
    args = ap.parse_args()
    root, output = args.root.resolve(), args.output.resolve()
    if not root.is_dir():
        ap.error("dataset root must be an existing directory")
    if output == root or root in output.parents:
        ap.error("output must be outside the read-only dataset root")
    records, refs = [], []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        b = path.read_bytes()
        rel = path.relative_to(root)
        rec = dict(path=root.name+"/"+rel.as_posix(), project=rel.parts[0],
                   bytes=len(b), sha256=hashlib.sha256(b).hexdigest(), extension=path.suffix.lower(),
                   header_hex=b[:16].hex(),
                   kind=classify(b), non_ascii_path=not rel.as_posix().isascii(), warnings=[])
        if rec["kind"] in ("FORM LWOB", "FORM LWO2"):
            inspect_object(b, rec, refs)
        elif rec["kind"].startswith("LWSC "):
            inspect_scene(b, rec, refs)
        elif rec["kind"] == "FORM ILBM":
            try:
                for tag, data, _ in chunks(b[:min(u32(b, 4)+8, len(b))], 12):
                    if tag == "BMHD":
                        rec["ilbm"] = dict(width=u16(data), height=u16(data, 2), planes=data[8],
                                           masking=data[9], compression=data[10])
                    elif tag == "CAMG":
                        rec["camg"] = u32(data)
            except (ValueError, IndexError, struct.error) as exc:
                rec["warnings"].append(str(exc))
        elif rec["kind"] == "ZIP":
            try:
                with zipfile.ZipFile(path) as z:
                    rec["zip_members"] = [dict(name=i.filename, bytes=i.file_size) for i in z.infolist()]
            except zipfile.BadZipFile as exc:
                rec["warnings"].append(str(exc))
        records.append(rec)
    for ref in refs:
        ref["resolution"] = resolve(ref, records)
    projects = {}
    for project in sorted({r["project"] for r in records}):
        rs = [r for r in records if r["project"] == project]
        rr = [r for r in refs if r["project"] == project]
        projects[project] = dict(files=len(rs), bytes=sum(r["bytes"] for r in rs),
                                 kinds=dict(Counter(r["kind"] for r in rs)),
                                 references=dict(Counter(r["resolution"]["status"] for r in rr)))
    hashes = defaultdict(list)
    for r in records:
        hashes[r["sha256"]].append(r["path"])
    summary = dict(schema_version=1, dataset_root=root.name,
                   scanner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   file_count=len(records),
                   total_bytes=sum(r["bytes"] for r in records), projects=projects,
                   kinds=dict(Counter(r["kind"] for r in records)),
                   reference_statuses=dict(Counter(r["resolution"]["status"] for r in refs)),
                   files_with_warnings=[r["path"] for r in records if r["warnings"]],
                   duplicate_groups=[v for v in hashes.values() if len(v) > 1],
                   scope="Regular files including hidden files; ZIP member names only; no extraction. "
                         "LightWave chunk/geometry audit and selected dependency fields, not full semantic validation. "
                         "Latin-1 display of non-UTF8 strings is a hypothesis; matches are candidates only.")
    output.mkdir(parents=True, exist_ok=True)
    for name, value in [("inventory.json", records), ("references.json", refs), ("summary.json", summary)]:
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: v for k, v in summary.items() if k not in ("projects", "duplicate_groups")}, indent=2))


if __name__ == "__main__":
    main()
