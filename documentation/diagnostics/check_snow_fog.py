"""Read-only diagnosis of the snow-tanks additive fog in an existing batch."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from urllib.parse import unquote

from PIL import Image


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def surface_values(raw):
    assert raw[:4]==b'FORM' and raw[8:12]==b'LWO2'
    result=[]; offset=12
    while offset+8<=len(raw):
        tag=raw[offset:offset+4]; size=struct.unpack_from('>I',raw,offset+4)[0]
        end=offset+8+size; assert end<=len(raw)
        if tag==b'SURF':
            pos=offset+8; name=raw[pos:raw.index(b'\0',pos)].decode('ascii')
            for _ in range(2):
                zero=raw.index(b'\0',pos); pos+=(zero-pos+2)&~1
            fields={}
            while pos+6<=end:
                key=raw[pos:pos+4].decode('ascii'); n=struct.unpack_from('>H',raw,pos+4)[0]
                assert pos+6+n<=end
                if key in ('ADTR','DIFF','LUMI','TRAN'):
                    assert n==6
                    value,envelope=struct.unpack_from('>fH',raw,pos+6)
                    fields[key]=dict(value=value,envelope=envelope,source_offset=pos,source_bytes=n+6)
                pos+=6+n+(n&1)
            result.append(dict(name=name,fields=fields))
        offset=end+(size&1)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    scene=args.source/'large_shot_fprime.lws'
    exported=args.project/'gltf/large_shot_fprime.lws.gltf'
    gltf=json.loads(exported.read_text('utf-8'))
    scene_ir=json.loads((args.project/'IR/large_shot_fprime.lws/scene.json').read_text('utf-8'))
    assert digest(scene)==scene_ir['source']['sha256']
    source_lines=scene.read_text('utf-8').splitlines()
    nodes=[]; geometry=[]
    for filename in ('obj_layered_tiled_fog.lwo','obj_layered_tiled_fog_morph_target.lwo'):
        source=args.source/'NEW'/filename; h=digest(source)
        directory=args.project/'IR/NEW'/filename
        native=json.loads((directory/'object.json').read_text('utf-8'))
        assert h==native['source']['sha256']==digest(directory/'source.bin')
        surfaces=surface_values(source.read_bytes()); assert len(surfaces)==1
        assert surfaces[0]['fields']['ADTR']['value']==1 and surfaces[0]['fields']['ADTR']['envelope']==0
        ni=next(n for n in scene_ir['nodes'] if n['object_path']['text'].endswith('/'+filename))
        gn=next(n for n in gltf['nodes'] if n.get('extras',{}).get('source_node_id')==ni['id'])
        mesh=gltf['meshes'][gn['mesh']]
        materials=[]
        for index in sorted({p['material'] for p in mesh['primitives']}):
            m=gltf['materials'][index]; assert m['extras']['source_sha256']==h
            images={}
            for role,info in (('base_color',m['pbrMetallicRoughness']['baseColorTexture']),('emissive',m['emissiveTexture'])):
                image=gltf['images'][gltf['textures'][info['index']]['source']]
                path=exported.parent/unquote(image['uri']); rgba=Image.open(path).convert('RGBA')
                images[role]=dict(uri=image['uri'],sha256=digest(path),size=list(rgba.size),rgba_extrema=rgba.getextrema())
            materials.append(dict(index=index,alpha_mode=m['alphaMode'],base_color_factor=m['pbrMetallicRoughness']['baseColorFactor'],
                                  emissive_factor=m['emissiveFactor'],images=images))
        nodes.append(dict(object=filename,source_sha256=h,native_surfaces=surfaces,
                          source_node_id=ni['id'],native_object_dissolve=ni['object_dissolve'],
                          exported_as_mesh=True,exported_morph_targets=any(p.get('targets') for p in mesh['primitives']),
                          native_points=native['positions']['count'],native_polygons=native['primitives']['count'],materials=materials,
                          image_references=native['image_references']))
        raw=(directory/'geometry.bin').read_bytes(); p=native['positions']; idx=native['indices']
        geometry.append((list(struct.iter_unpack('<3f',raw[p['offset']:p['offset']+p['count']*p['stride']])),
                         raw[idx['offset']:idx['offset']+idx['count']*idx['stride']]))
    assert len(geometry[0][0])==len(geometry[1][0])
    changed=sum(p!=q for p,q in zip(geometry[0][0],geometry[1][0]))
    max_delta=max(sum((a-b)**2 for a,b in zip(p,q))**.5 for p,q in zip(geometry[0][0],geometry[1][0]))
    statements=[dict(line=i+1,text=line) for i,line in enumerate(source_lines)
                if line.startswith(('MorphAmount','MorphTarget','MorphSurfaces','ObjectDissolve','FogType','FogMinDistance','FogMaxDistance','FogColor'))]
    report=dict(inspection_completed=True,visual_fidelity_passed=False,project=str(args.project),
                converter=gltf['asset']['generator'],scene_sha256=digest(scene),gltf_sha256=digest(exported),
                nodes=nodes,scene_statements=statements,
                morph_geometry=dict(same_point_count=True,same_index_stream=geometry[0][1]==geometry[1][1],
                                    changed_points=changed,maximum_position_difference=max_delta),
                diagnosis=['Native ADTR=1 is not interpreted; both fog meshes have OPAQUE materials and alpha 255.',
                           'The morph target has ObjectDissolve 1 but is still an independently visible mesh.',
                           'MorphAmount 1 / MorphTarget 22 is not exported as a glTF morph target.'],
                scope='Source bytes, IR, resolved images and glTF inspection. No changes to assets or exports; no original LightWave render performed. beresina.jpg is a visual reference, not a verified frame of this exact scene.')
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('nodes','scene_statements')},indent=2))


if __name__=='__main__': main()
