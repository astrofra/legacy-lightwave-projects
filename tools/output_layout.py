"""Publish converter packages into a readable, shared project directory."""
import json
import hashlib
import os
from pathlib import Path
import shutil
from urllib.parse import quote

FORMATS = {"obj": "generated", "IR": "generated", "gltf": "generated", "blender": "not-implemented"}


def output_name(name):
    # Match the C writer: OBJ mtllib references must be a single filename token.
    name = "".join("_" if ord(c) <= 32 or ord(c) == 127 or c in '<>:"/\\|?*#' else c for c in name).rstrip(".") or "_"
    stem = name.split(".", 1)[0].upper()
    if stem in {"CON", "PRN", "AUX", "NUL"} or (len(stem) == 4 and stem[:3] in {"COM", "LPT"} and stem[3] in "123456789"):
        name = "_" + name
    return name


class Names:
    """Reserve natural names before allocating suffixes, including on Windows."""
    def __init__(self, natural_names=()):
        self.reserved = {name.casefold() for name in natural_names}
        self.used = set()
        self.assigned = {}

    def get(self, key, base):
        if key not in self.assigned:
            name, suffix = base, 1
            while name.casefold() in self.used or (suffix > 1 and name.casefold() in self.reserved):
                suffix += 1
                name = f"{base}-{suffix}"
            self.used.add(name.casefold())
            self.assigned[key] = name
        return self.assigned[key]


def new_run(output, timestamp):
    """Atomic mkdir prevents overwrites even when two batches start together."""
    suffix = 1
    while True:
        path = output / (timestamp if suffix == 1 else f"{timestamp}-{suffix}")
        try:
            path.mkdir()
            return path
        except FileExistsError:
            suffix += 1


def source_key(path):
    return os.path.normcase(str(Path(path).resolve()))


def relative(path, directory):
    return Path(os.path.relpath(path, directory)).as_posix()


def write_json(path, data):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def package_file(package, uri):
    path = (package / uri).resolve()
    if not path.is_relative_to(package.resolve()) or not path.is_file():
        raise ValueError(f"Missing or out-of-package artifact: {uri}")
    return path


def copy_file(source, destination):
    # The project directory belongs to this run; never overwrite an artifact.
    with source.open("rb") as reader, destination.open("xb") as writer:
        shutil.copyfileobj(reader, writer)


def copy_obj(source, mtl_source, destination):
    copy_file(mtl_source, destination.with_suffix(".mtl"))
    for line in mtl_source.read_text("utf-8").splitlines():
        if line.startswith(("map_Kd ", "map_d ", "map_Ke ", "map_Ks ", "bump ")):
            copy_texture(mtl_source.parent, line.split()[-1], destination.parent)
    with source.open("rb") as reader, destination.open("xb") as writer:
        # Current converter headers contain mtllib before all geometry. Stream
        # the remaining bytes unchanged, including UVs, groups and face order.
        for line in reader:
            if line.startswith(b"mtllib "):
                writer.write(("mtllib " + destination.with_suffix(".mtl").name + "\n").encode("utf-8"))
                shutil.copyfileobj(reader, writer)
                break
            writer.write(line)
        else:
            raise ValueError(f"Missing mtllib in {source}")


def copy_texture(source_directory, uri, destination, content_addressed=True):
    local = Path(uri)
    if local.is_absolute() or local.drive or len(local.parts) != 2 or local.parts[0] != "textures" or local.parts[1] in {".", ".."}:
        raise ValueError(f"Invalid texture URI: {uri}")
    source = package_file(source_directory, uri)
    if content_addressed and (local.suffix != ".png" or hashlib.sha256(source.read_bytes()).hexdigest() != local.stem):
        raise ValueError(f"Texture content hash mismatch: {uri}")
    target = destination / local
    target.parent.mkdir(exist_ok=True)
    if target.exists():
        if target.read_bytes() != source.read_bytes():
            raise ValueError(f"Texture collision: {target}")
    else:
        copy_file(source, target)


def copy_ir(package, data, destination, filenames):
    metadata = json.loads(data.read_text("utf-8"))
    for filename in filenames:
        copy_file(package_file(package, relative(data.parent / filename, package)), destination / filename)
    uris = set()
    for reference in metadata.get("image_references", []):
        uris.add(reference.get("uri"))
        uris.add((reference.get("decoded_image") or {}).get("png_uri"))
    for material in metadata.get("materials", []):
        uris.update(material.get("derived_maps", {}).values())
    for uri in sorted(uris - {None}):
        copy_texture(data.parent, uri, destination, content_addressed=False)


class ProjectOutput:
    def __init__(self, directory, sources):
        self.directory = directory
        ordered = sorted(sources, key=lambda path: str(path))
        self.names = Names(output_name(path.name) for path in ordered)
        for path in ordered:
            self.name_for(path)
        self.assets = {}
        self.conversions = []
        self.initialized = False

    def name_for(self, source):
        return self.names.get(source_key(source), output_name(Path(source).name))

    def initialize(self):
        if not self.initialized:
            self.directory.mkdir(parents=True)
            for name in FORMATS:
                (self.directory / name).mkdir()
            self.initialized = True

    def publish_gltf(self, package, uri, bin_uri, name, ir):
        destination = self.directory / "gltf" / (name + ".gltf")
        binary = destination.with_suffix(".bin")
        data = json.loads(package_file(package, uri).read_text("utf-8"))
        if len(data.get("buffers", [])) > 1:
            raise ValueError("Expected the converter's single-buffer glTF profile")
        for buffer in data.get("buffers", []):
            buffer["uri"] = quote(binary.name, safe="-._~")
        source_directory = package_file(package, uri).parent
        for image in data.get("images", []):
            copy_texture(source_directory, image["uri"], destination.parent)
        copy_file(package_file(package, bin_uri), binary)
        with destination.open("x", encoding="utf-8") as writer:
            json.dump(data, writer, ensure_ascii=False, separators=(",", ":"))
            writer.write("\n")
        return relative(destination, ir), relative(binary, ir)

    def publish(self, package, manifest):
        if manifest.get("layout_version") != "0.2" or manifest.get("formats", {}).get("gltf") != "generated":
            raise ValueError("Batch output requires lwconvert 0.3.0 or later; rebuild the selected converter")
        self.initialize()
        name = self.name_for(manifest["input"])
        ir = self.directory / "IR" / name
        ir.mkdir(exist_ok=True)
        for asset in manifest["assets"]:
            key = source_key(asset["source_path"])
            asset_name = self.name_for(asset["source_path"])
            asset_ir = self.directory / "IR" / asset_name
            obj = self.directory / "obj" / (asset_name + ".obj")
            if key in self.assets:
                if self.assets[key] != asset["id"]:
                    raise ValueError(f"Source changed during the batch: {asset['source_path']}")
            else:
                data = package_file(package, asset["uri"])
                asset_ir.mkdir(exist_ok=True)
                copy_ir(package, data, asset_ir, ("object.json", "geometry.bin", "source.bin"))
                copy_obj(package_file(package, asset["obj"]), package_file(package, asset["mtl"]), obj)
                self.publish_gltf(package, asset["gltf"], asset["gltf_bin"], asset_name, ir)
                self.assets[key] = asset["id"]
            asset.update(name=asset_name, uri=relative(asset_ir / "object.json", ir), obj=relative(obj, ir), mtl=relative(obj.with_suffix(".mtl"), ir))
            gltf = self.directory / "gltf" / (asset_name + ".gltf")
            asset.update(gltf=relative(gltf, ir), gltf_bin=relative(gltf.with_suffix(".bin"), ir))
        if manifest["scene"]:
            scene = package_file(package, manifest["scene"])
            copy_ir(package, scene, ir, ("scene.json", "animation.bin", "source.bin"))
            manifest["scene"] = "scene.json"
        if manifest["scene_obj"]:
            obj = self.directory / "obj" / (name + ".obj")
            copy_obj(package_file(package, manifest["scene_obj"]), package_file(package, manifest["scene_mtl"]), obj)
            manifest.update(scene_obj=relative(obj, ir), scene_mtl=relative(obj.with_suffix(".mtl"), ir))
        if manifest["scene_gltf"]:
            manifest["scene_gltf"], manifest["scene_gltf_bin"] = self.publish_gltf(package, manifest["scene_gltf"], manifest["scene_gltf_bin"], name, ir)
        manifest_path = ir / "manifest.json"
        if manifest_path.exists():
            raise FileExistsError(f"Conversion manifest already exists: {manifest_path}")
        write_json(manifest_path, manifest)
        self.conversions.append({"source": manifest["input"], "name": name, "status": manifest["status"], "manifest": relative(manifest_path, self.directory)})
        write_json(self.directory / "manifest.json", {"layout_version": "0.2", "kind": "project", "formats": FORMATS, "conversions": self.conversions})
        return manifest_path
