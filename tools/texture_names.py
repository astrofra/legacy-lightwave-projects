"""Readable, deterministic texture names and publication across a whole project."""
from collections import defaultdict
import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import quote, unquote


CHANNELS = {
    'base_color': ('COLR', 'DIFF', 'TRAN'), 'opacity': ('TRAN', 'COLR'),
    'emissive': ('COLR', 'LUMI'), 'specular': ('SPEC',), 'bump': ('BUMP',), 'normal': ('NORM',),
}
MTL_ROLES = {'map_Kd': 'base_color', 'map_d': 'opacity', 'map_Ke': 'emissive',
             'map_Ks': 'specular', 'bump': 'bump'}


def component(text):
    text = ''.join('_' if ord(c) <= 32 or ord(c) == 127 or c in '<>:"/\\|?*#' else c for c in text).rstrip('.') or '_'
    stem = text.split('.', 1)[0].upper()
    if stem in {'CON', 'PRN', 'AUX', 'NUL'} or re.fullmatch(r'(COM|LPT)[1-9]', stem):
        text = '_' + text
    return text.encode('utf-8')[:36].decode('utf-8', errors='ignore')


def candidate(record, level):
    parts = [record['directory'], record['image']]
    if level >= 3:
        parts.append(record['object'])
    if level >= 2:
        parts.append(record['material'])
    if level >= 1:
        parts.append(record['role'])
    if level >= 4:
        parts.append(record['sha256'][:12] if level == 4 else record['sha256'])
    return '__'.join(component(part) if i < len(parts) - 1 or level < 4 else part
                    for i, part in enumerate(parts)) + '.png'


def allocate(records):
    """Promote all colliding names together, independent of input order."""
    levels = [0] * len(records)
    while True:
        names = [candidate(row, level) for row, level in zip(records, levels)]
        buckets = defaultdict(set)
        keys = [(str(row['directory_path']).casefold(), name.casefold()) for row, name in zip(records, names)]
        for row, key in zip(records, keys):
            buckets[key].add(row['sha256'])
        conflicts = {key for key, contents in buckets.items() if len(contents) > 1}
        if not conflicts:
            # Canonical spelling for identical bytes and case-insensitive aliases.
            spelling = {}
            for key, name in zip(keys, names):
                spelling[key] = min(spelling.get(key, name), name)
            return [spelling[key] for key in keys]
        for i, key in enumerate(keys):
            if key in conflicts:
                if levels[i] == 5:
                    raise ValueError('Cannot disambiguate texture names')
                levels[i] += 1


def material_digest(material, role):
    # Compatibility with packages made before hashes became explicit metadata.
    return material.get('derived_map_sha256', {}).get(role) or Path(material['derived_maps'][role]).stem


def material_variant(obj, mi, role, digest):
    material = obj['materials'][mi]
    if role in material.get('derived_maps', {}) and material_digest(material, role) == digest:
        return material
    for binding in obj.get('derived_clip_maps', {}).get('bindings', []):
        if binding['material'] == mi and binding.get(role) and binding.get(role + '_sha256') == digest:
            return {**material, '_clip_map': True, 'derived_maps': {role: binding[role]},
                    'derived_map_sha256': {role: digest}}
    return None


def provenance(owner, obj, material, role, directory, source):
    usable = {t['channel']: t for t in material['textures']
              if t['export_status'] == 'approximated' and isinstance(t.get('image_reference'), int)}
    image = None
    for channel in CHANNELS[role]:
        if channel in usable:
            ref = obj['image_references'][usable[channel]['image_reference']]
            image = Path(re.split(r'[:/\\]', ref.get('resolved_path') or ref['path']['text'])[-1]).stem
            break
    return {
        'directory_path': directory, 'directory': Path(owner).parent.name,
        'image': (image or material['name']['text'] or Path(obj['source']['path']).stem) + ('_cutout' if material.get('_clip_map') else ''),
        'object': Path(obj['source']['path']).stem, 'material': material['name']['text'],
        'role': role, 'sha256': material_digest(material, role), 'source': source,
    }


def gltf_bindings(material):
    refs = [('base_color', material.get('pbrMetallicRoughness', {}).get('baseColorTexture')),
            ('emissive', material.get('emissiveTexture')), ('normal', material.get('normalTexture')),
            ('specular', material.get('extensions', {}).get('KHR_materials_specular', {}).get('specularTexture'))]
    return [(role, info) for role, info in refs if info is not None]


def publish_names(project, exports):
    """Rename only generated export textures, and rewrite their glTF/MTL links.

    Every source is verified before changes. Stage target bytes first so cycles
    and aliases cannot destroy a source; IR originals are outside this scope.
    """
    project = project.resolve()
    records, documents, digests = [], [], {}

    def add(path, owner, obj, material, role, uri):
        source = (path.parent / unquote(uri)).resolve()
        directory = (path.parent / 'textures').resolve()
        if source.parent != directory or not directory.is_relative_to(project):
            raise ValueError(f'Invalid generated texture path: {uri}')
        row = provenance(owner, obj, material, role, directory, source)
        if source not in digests:
            digests[source] = hashlib.sha256(source.read_bytes()).hexdigest()
        if digests[source] != row['sha256']:
            raise ValueError(f'Texture content hash mismatch: {source}')
        records.append(row)
        return len(records) - 1

    for path, owner, objects in sorted(exports, key=lambda entry: str(entry[0])):
        if path.suffix == '.gltf':
            data = json.loads(path.read_text('utf-8'))
            updates = []
            for material in data.get('materials', []):
                extras = material.get('extras', {})
                mi = extras.get('source_surface_index')
                for role, info in gltf_bindings(material):
                    texture = data['textures'][info['index']]
                    image = data['images'][texture['source']]
                    digest = image.get('extras', {}).get('sha256') or Path(unquote(image['uri'])).stem
                    matches = [o for o in objects if o['source']['sha256'] == extras.get('source_sha256')
                               and isinstance(mi, int) and 0 <= mi < len(o['materials'])
                               and material_variant(o, mi, role, digest) is not None]
                    if not matches:
                        raise ValueError(f'Missing texture provenance: {path}, {role}')
                    index = add(path, owner, matches[0], material_variant(matches[0], mi, role, digest), role, image['uri'])
                    updates.append((info, texture, image, index))
            documents.append((path, data, updates))
        else:
            lines = path.read_text('utf-8').splitlines()
            updates, material, source_hash = [], None, None
            hashes = {line.split()[3]: line.split()[2] for line in lines if line.startswith('# texture-sha256 ')}
            for line_number, line in enumerate(lines):
                match = re.fullmatch(r'newmtl a(\d+)_m(\d+)', line)
                if line.startswith('newmtl '):
                    material = tuple(map(int, match.groups())) if match else None
                    source_hash = None
                if line.startswith('# source_asset_sha256 '):
                    source_hash = line.split()[2]
                fields = line.split()
                if fields and fields[0] in MTL_ROLES:
                    if material is None:
                        raise ValueError(f'Missing MTL provenance: {path}')
                    ai, mi = material
                    obj = next((o for o in objects if o['source']['sha256'] == source_hash), None) if source_hash else (objects[0] if len(objects) == 1 else objects[ai])
                    if obj is None:
                        raise ValueError(f'Missing MTL object provenance: {path}')
                    role = MTL_ROLES[fields[0]]
                    digest = hashes.get(fields[-1]) or Path(fields[-1]).stem
                    variant = material_variant(obj, mi, role, digest)
                    if variant is None:
                        raise ValueError(f'Missing MTL texture provenance: {path}, {role}')
                    index = add(path, owner, obj, variant, role, fields[-1])
                    updates.append((line_number, index))
            documents.append((path, lines, updates))

    names = allocate(records)
    targets = {}
    for row, name in zip(records, names):
        target = row['directory_path'] / name
        if target.exists() and target not in digests:
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual != row['sha256']:
                raise ValueError(f'Unregistered texture collision: {target}')
        targets[target] = row

    # Prepare all target bytes before replacing any generated file.
    with tempfile.TemporaryDirectory(prefix='.texture-names-', dir=project) as temp:
        staging = Path(temp).resolve()
        assert staging.is_relative_to(project)
        staged = {}
        for target, row in targets.items():
            if target == row['source']:
                continue
            if row['sha256'] not in staged:
                staged[row['sha256']] = staging / (row['sha256'] + '.png')
                shutil.copyfile(row['source'], staged[row['sha256']])
        for target, row in targets.items():
            if target != row['source']:
                shutil.copyfile(staged[row['sha256']], target)

        for path, data, updates in documents:
            if path.suffix == '.gltf':
                images, textures, image_ids, texture_ids = [], [], {}, {}
                for info, texture, image, index in updates:
                    image = copy.deepcopy(image)
                    image['uri'] = 'textures/' + quote(names[index], safe='-._~')
                    image['name'] = names[index]
                    image.setdefault('extras', {})['sha256'] = records[index]['sha256']
                    key = image['uri']
                    if key not in image_ids:
                        image_ids[key] = len(images)
                        images.append(image)
                    texture = {**texture, 'source': image_ids[key]}
                    key = json.dumps(texture, sort_keys=True)
                    if key not in texture_ids:
                        texture_ids[key] = len(textures)
                        textures.append(texture)
                    info['index'] = texture_ids[key]
                if updates:
                    data['images'], data['textures'] = images, textures
                temporary = staging / 'document.json'
                temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
            else:
                for line_number, index in updates:
                    uri = 'textures/' + names[index]
                    prefix = data[line_number].rsplit(None, 1)[0]
                    data[line_number] = f"# texture-sha256 {records[index]['sha256']} {uri}\n{prefix} {uri}"
                # Drop previous digest comments; the replacement lines carry fresh ones.
                replaced = {line_number for line_number, _ in updates}
                data = [line for i, line in enumerate(data) if i in replaced or not line.startswith('# texture-sha256 ')]
                temporary = staging / 'document.mtl'
                temporary.write_text('\n'.join(data) + '\n', encoding='utf-8')
            temporary.replace(path)

    for source in digests:
        if source not in targets:
            # Only verified generated PNGs within this project's export folders.
            assert source.is_relative_to(project) and source.parent.name == 'textures'
            source.unlink()
    return {'policy': 'readable-textures-1', 'bindings': len(records), 'files': len(targets)}
