"""Read-only audit of readable texture names against an existing batch.

Counts conflicts only when different bytes would get the same filename in the
same destination (case-insensitive). Source names are inferred from archived
material/image provenance, not from current hash filenames. Nothing is renamed.
"""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote

from output_layout import output_name
from texture_names import material_digest

SCHEMES = ('image', 'directory_image', 'directory_image_role', 'directory_image_material_role', 'directory_image_object_material_role')
ROLE_CHANNELS = {
    'base_color': ('COLR', 'DIFF', 'TRAN'), 'opacity': ('TRAN', 'COLR'),
    'emissive': ('COLR', 'LUMI'), 'specular': ('SPEC',), 'bump': ('BUMP',), 'normal': ('NORM',),
}


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def original_basename(path):
    return re.split(r'[:/\\]', path)[-1]


def candidate_names(record):
    image, directory, material, obj, role = (output_name(record[k]) for k in ('image_stem', 'directory', 'material', 'object_stem', 'role'))
    pieces = [(image,), (directory,image), (directory,image,role), (directory,image,material,role), (directory,image,obj,material,role)]
    return {scheme: '__'.join(parts)+'.png' for scheme,parts in zip(SCHEMES,pieces)}


def collisions(records, scheme, scope):
    buckets = defaultdict(lambda: defaultdict(list))
    for row in records:
        namespace = row['destination'] if scope=='local' else row['project'] if scope=='project' else '*'
        name = candidate_names(row)[scheme]
        buckets[namespace.casefold(),name.casefold()][row['sha256']].append(row)
    groups = []
    for (namespace,name),variants in sorted(buckets.items()):
        if len(variants)<2: continue
        samples=[]
        for digest,uses in sorted(variants.items()):
            unique = {json.dumps({k:r[k] for k in ('export','owner','object','material','role','source_images')},sort_keys=True):r for r in uses}
            examples=[{k:r[k] for k in ('export','owner','object','material','role','source_images')} for r in list(unique.values())[:6]]
            samples.append({'sha256':digest,'bindings':len(uses),'examples':examples})
        groups.append({'namespace':namespace,'filename':name,'different_contents':len(variants),'variants':samples})
    return {
        'candidate_paths':len(buckets), 'conflicting_paths':len(groups),
        'extra_content_variants':sum(len(v)-1 for v in buckets.values()),
        'bindings_in_conflicts':sum(len(rows) for variants in buckets.values() if len(variants)>1 for rows in variants.values()),
        'max_filename_characters':max((len(candidate_names(r)[scheme]) for r in records),default=0),
        'conflicts':groups,
    }


def adaptive_names(records):
    """Simulate readable suffixes only on conflicts, without allocating files.

    Recheck the complete namespace after each promotion: a suffixed name may
    itself collide with another image's natural name. No hash fallback is used.
    """
    schemes = SCHEMES[1:]
    levels = [0] * len(records)
    while True:
        names = [candidate_names(row)[schemes[level]] for row, level in zip(records, levels)]
        keys = [(row['destination'].casefold(), name.casefold()) for row, name in zip(records, names)]
        buckets = defaultdict(set)
        for row, key in zip(records, keys):
            buckets[key].add(row['sha256'])
        conflicts = {key for key, contents in buckets.items() if len(contents) > 1}
        promoted = [min(level + 1, len(schemes) - 1) if key in conflicts else level
                    for level, key in zip(levels, keys)]
        if promoted == levels:
            break
        levels = promoted
    return {
        'conflicting_paths': len(conflicts),
        'candidate_paths': len(buckets),
        'bindings_by_policy': dict(Counter(schemes[level] for level in levels)),
        'paths_by_policy': {scheme: len({key for key, level in zip(keys, levels) if schemes[level] == scheme})
                            for scheme in schemes},
        'max_filename_characters': max(map(len, names), default=0),
    }


def audit(batch):
    batch=batch.resolve();report=read(batch/'batch-report.json')
    if report['status']=='running': raise ValueError('Use a completed batch for reproducible counts')
    objects={};exports={};scene_paths=set();object_scenes=defaultdict(set);originals={}

    def object_data(path):
        path=path.resolve()
        if path not in objects:objects[path]=read(path)
        return objects[path]

    def register(path, owner, candidates, project, kind):
        if path:exports.setdefault(path.resolve(),(owner,candidates,project,kind))

    manifests=0
    for entry in report['files']:
        if entry.get('status') not in ('converted','partial') or not entry.get('manifest'):continue
        manifest_path=batch/entry['manifest'];manifest=read(manifest_path);manifests+=1
        project=entry['package'];parent=manifest_path.parent;assets=[]
        for asset in manifest['assets']:
            data=object_data(parent/asset['uri']);assets.append(data)
            source=asset['source_path']
            for field,kind in [('gltf','gltf'),('mtl','obj')]:
                if asset.get(field):register(parent/asset[field],source,[data],project,kind)
            if manifest.get('scene'):object_scenes[data['source']['path']].add(manifest['input'])
        if manifest.get('scene'):scene_paths.add(manifest['input'])
        for field,kind in [('scene_gltf','gltf'),('scene_mtl','obj')]:
            if manifest.get(field):register(parent/manifest[field],manifest['input'],assets,project,kind)
        for rig in manifest.get('gltf_rigs',[])+manifest.get('gltf_animations',[]):
            if rig.get('gltf'):register(parent/rig['gltf'],manifest['input'],assets,project,'gltf')
    print(f'Read {manifests} manifests, {len(objects)} object IRs; inspecting {len(exports)} published exports',flush=True)
    records=[];digest_cache={};unknown=[]
    content=Path(report['content'])

    def label(path):
        try:return Path(path).relative_to(content).as_posix()
        except ValueError:return str(path).replace('\\','/')

    def image_digest(path, uri):
        image_path=(path.parent/unquote(uri)).resolve()
        if not image_path.is_relative_to(batch):raise ValueError(f'Out-of-batch texture {image_path}')
        if image_path not in digest_cache:digest_cache[image_path]=hashlib.sha256(image_path.read_bytes()).hexdigest()
        return digest_cache[image_path]

    def binding(path, owner, project, kind, obj, material_index, role, uri):
        image_path=(path.parent/unquote(uri)).resolve()
        image_digest(path, uri)
        m=obj['materials'][material_index]
        usable=[t for t in m['textures'] if t['export_status']=='approximated' and isinstance(t.get('image_reference'),int)]
        by_channel={t['channel']:t for t in usable}
        participating=[obj['image_references'][t['image_reference']] for t in usable if t['channel'] in ROLE_CHANNELS[role]]
        primary=next((obj['image_references'][by_channel[c]['image_reference']] for c in ROLE_CHANNELS[role] if c in by_channel),None)
        primary_path=(primary.get('resolved_path') or primary['path']['text']) if primary else None
        # No contributing image: a generated constant-color material map.
        stem=Path(original_basename(primary_path)).stem if primary_path else m['name']['text'] or Path(obj['source']['path']).stem
        row={
            'export':path.relative_to(batch).as_posix(),'format':kind,'project':project,
            'destination':(path.parent/'textures').relative_to(batch).as_posix(),
            'owner':label(owner),'directory':Path(owner).parent.name,
            'object':label(obj['source']['path']),'object_stem':Path(obj['source']['path']).stem,
            'material':m['name']['text'],'role':role,'image_stem':stem,
            'naming_origin':'primary-source-image' if primary else 'generated-material-value',
            'source_images':sorted({label(i.get('resolved_path') or i['path']['text']) for i in participating}),
            'current_texture':image_path.relative_to(batch).as_posix(),'sha256':digest_cache[image_path],
        }
        records.append(row)

    for num,(path,(owner,candidates,project,kind)) in enumerate(sorted(exports.items())):
        if num and num%500==0:print(f'Inspected {num}/{len(exports)} exports',flush=True)
        if kind=='gltf':
            data=read(path)
            for material in data.get('materials',[]):
                extras=material.get('extras',{});mi=extras.get('source_surface_index')
                native=[o for o in candidates if o['source']['sha256']==extras.get('source_sha256') and isinstance(mi,int) and mi<len(o['materials'])]
                refs={}
                pbr=material.get('pbrMetallicRoughness',{})
                for role,info in [('base_color',pbr.get('baseColorTexture')),('emissive',material.get('emissiveTexture')),
                                  ('normal',material.get('normalTexture')),('specular',material.get('extensions',{}).get('KHR_materials_specular',{}).get('specularTexture'))]:
                    if info is not None:refs[role]=data['images'][data['textures'][info['index']]['source']]['uri']
                for role,uri in refs.items():
                    matching=[o for o in native if role in o['materials'][mi]['derived_maps'] and material_digest(o['materials'][mi],role)==image_digest(path,uri)]
                    if not matching:unknown.append({'export':str(path),'role':role,'uri':uri});continue
                    binding(path,owner,project,kind,matching[0],mi,role,uri)
        else:
            material=None
            mapping={'map_Kd':'base_color','map_Ke':'emissive','map_Ks':'specular','map_d':'opacity','bump':'bump'}
            for line in path.read_text(encoding='utf-8').splitlines():
                if line.startswith('newmtl '):
                    match=re.fullmatch(r'newmtl a(\d+)_m(\d+)',line)
                    material=tuple(map(int,match.groups())) if match else None
                elif line and line.split()[0] in mapping:
                    role=mapping[line.split()[0]];uri=line.split()[-1]
                    if material is None:unknown.append({'export':str(path),'role':role,'uri':uri});continue
                    ai,mi=material
                    possible=candidates if len(candidates)==1 else candidates[ai:ai+1]
                    matching=[o for o in possible if mi<len(o['materials']) and role in o['materials'][mi]['derived_maps'] and material_digest(o['materials'][mi],role)==image_digest(path,uri)]
                    if not matching:unknown.append({'export':str(path),'role':role,'uri':uri});continue
                    binding(path,owner,project,kind,matching[0],mi,role,uri)
    # Source image collisions separately from computed material variants.
    raw_buckets=defaultdict(lambda:defaultdict(set))
    raw_prefixed=defaultdict(lambda:defaultdict(set))
    for obj in objects.values():
        source=obj['source']['path'];owners={source}|object_scenes[source]
        for ref in obj['image_references']:
            if not ref.get('uri'):continue
            filename=output_name(original_basename(ref['resolved_path']))
            originals[ref['resolved_path']]=ref['sha256']
            for owner in owners:
                try:project=Path(owner).relative_to(content).parts[0]
                except ValueError:project=Path(owner).parent.as_posix()
                raw_buckets[project,filename.casefold()][ref['sha256']].add(label(ref['resolved_path']))
                prefixed=output_name(Path(owner).parent.name)+'__'+filename
                raw_prefixed[project,prefixed.casefold()][ref['sha256']].add(label(ref['resolved_path']))
    result={
        'batch':batch.as_posix(),'batch_status':report['status'],'scope':'Published OBJ/glTF texture bindings from this completed batch; case-insensitive filenames; conflicts mean different SHA-256 bytes at the same candidate path. No renames performed.',
        'prefix_policy':'Containing LWS directory for scene/rig exports; containing LWO directory for standalone object exports. Relative export hierarchy is retained in local scope.',
        'primary_image_policy':ROLE_CHANNELS,
        'counts':{'completed_conversions':manifests,'scene_conversions':len(scene_paths),'object_irs':len(objects),'textured_objects':len({r['object'] for r in records}),
                  'published_exports':len(exports),'textured_exports':len({r['export'] for r in records}),'texture_bindings':len(records),
                  'distinct_derived_contents':len({r['sha256'] for r in records}),'unique_original_paths':len(originals),
                  'original_contents':len(set(originals.values())),'unmapped_bindings':len(unknown)},
        'originals_per_project':{
            'basename_conflicts':sum(len(v)>1 for v in raw_buckets.values()),'directory_basename_conflicts':sum(len(v)>1 for v in raw_prefixed.values()),
            'basename_examples':[{'project':k[0],'filename':k[1],'variants':[{'sha256':sha,'sources':sorted(paths)} for sha,paths in v.items()]} for k,v in sorted(raw_buckets.items()) if len(v)>1],
            'prefixed_examples':[{'project':k[0],'filename':k[1],'variants':[{'sha256':sha,'sources':sorted(paths)} for sha,paths in v.items()]} for k,v in sorted(raw_prefixed.items()) if len(v)>1],
        },
        'policies':{},'unmapped':unknown,
    }
    for scope in ('local','project','global'):
        result['policies'][scope]={scheme:collisions(records,scheme,scope) for scheme in SCHEMES}
    result['local_conflicts_by_format'] = {
        kind: {scheme: collisions([r for r in records if r['format'] == kind], scheme, 'local')['conflicting_paths']
               for scheme in SCHEMES} for kind in ('gltf', 'obj')
    }
    result['adaptive_local_policy'] = adaptive_names(records)
    # Show where a standalone object's name would depend on which scene came first.
    result['objects_shared_between_scene_directories']=[{'object':label(o),'scene_directories':sorted({label(Path(s).parent) for s in scenes})} for o,scenes in sorted(object_scenes.items()) if len({Path(s).parent for s in scenes})>1]
    return result,records


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('batch',type=Path)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--csv',type=Path)
    args=parser.parse_args()
    report,records=audit(args.batch)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if args.csv and records:
        args.csv.parent.mkdir(parents=True,exist_ok=True)
        with args.csv.open('w',encoding='utf-8-sig',newline='') as f:
            fields=list(records[0])+list(SCHEMES);writer=csv.DictWriter(f,fields);writer.writeheader()
            for row in records:writer.writerow({**row,'source_images':' | '.join(row['source_images']),**candidate_names(row)})
    print(json.dumps(report['counts'],indent=2))
    for scope,policies in report['policies'].items():
        print(scope,{k:v['conflicting_paths'] for k,v in policies.items()})
    if report['counts']['unmapped_bindings']:return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
