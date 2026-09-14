"""Independent pixel/provenance QA for Dora's legacy clip maps (Pillow)."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
from urllib.parse import unquote

from PIL import Image


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--baseline',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    results=[]
    for name,filename,size,wrap,negative in (
        ('dora','dora_mask.JPG',3.98,2,False),
        ('picasso','picass_mask.jpg',4,0,False),
        ('oeil','oeil_mask.JPG',4,0,True),
    ):
        directory=args.project/f'IR/{name}.lwo'
        native=json.loads((directory/'object.json').read_text('utf-8'))
        assert digest(directory/'source.bin')==digest(args.source/f'{name}.lwo')==native['source']['sha256']
        binding=next(b for b in native['derived_clip_maps']['bindings'] if b['node_id'] is None)
        color=Image.open(directory/binding['base_color']).convert('RGBA')
        unmasked=Image.open(directory/native['materials'][0]['derived_maps']['base_color']).convert('RGB')
        assert color.convert('RGB').tobytes()==unmasked.tobytes()
        old_doc=json.loads((args.baseline/f'gltf/{name}.lwo.gltf').read_text('utf-8'))
        old_info=old_doc['materials'][0]['pbrMetallicRoughness']['baseColorTexture']
        old_uri=old_doc['images'][old_doc['textures'][old_info['index']]['source']]['uri']
        old=Image.open(args.baseline/'gltf'/unquote(old_uri)).convert('RGB')
        assert color.size==old.size and color.convert('RGB').tobytes()==old.tobytes()
        assert (args.project/f'gltf/{name}.lwo.bin').read_bytes()==(args.baseline/f'gltf/{name}.lwo.bin').read_bytes()
        alpha=color.getchannel('A'); actual=alpha.load()
        mask=Image.open(args.source/filename).convert('RGB'); pixels=mask.load()
        # Native color plane: 4x4, centered at zero. The finite Reset atlas adds
        # one gutter texel on each side. Derive physical points independently
        # of the exported mask_uv_transform and texture_domain metadata.
        size=struct.unpack('f',struct.pack('f',size))[0]
        max_delta=0; threshold_differences=0
        for y in range(color.height):
            for x in range(color.width):
                point=[4*((x-1+.5)/(color.width-2)-.5),4*(.5-(y-1+.5)/(color.height-2))]
                uv=[.5+p/size for p in point]
                reset=False
                for k in range(2):
                    if wrap==0:
                        reset |= uv[k]<0 or uv[k]>1
                        uv[k]=max(0,min(1,uv[k]))
                    else:
                        uv[k]-=2*math.floor(uv[k]/2)
                        if uv[k]>1: uv[k]=2-uv[k]
                sx=min(mask.width-1,int(uv[0]*mask.width)); sy=min(mask.height-1,int((1-uv[1])*mask.height))
                level=0 if reset else sum(pixels[sx,sy])/3
                expected=round(level if negative else 255-level)
                delta=abs(actual[x,y]-expected); max_delta=max(delta,max_delta)
                threshold_differences+=((actual[x,y]>=128)!=(expected>=128))
                # Independent JPEG decoders can round IDCT/chroma differently.
                assert delta<=3, (name,x,y,actual[x,y],expected)
        assert threshold_differences<=color.width*color.height*.0001
        opacity=Image.open(directory/binding['opacity']).convert('L')
        assert opacity.tobytes()==bytes(255 if a>=128 else 0 for a in alpha.tobytes())
        for b in native['derived_clip_maps']['bindings']:
            if b.get('scene_uri'):
                assert digest(directory/b['scene_uri'])==digest(args.source/'dora&picasso.lws')==b['scene_sha256']
                assert digest(directory/b['image_uri'])==digest(args.source/filename)==b['image_sha256']
                raw=(directory/b['scene_uri']).read_bytes()
                assert raw[b['source_offset']:b['source_offset']+b['source_bytes']].startswith(b'ClipMap Planar Image Map')
        gltfs=[]
        for filename_gltf in (f'{name}.lwo.gltf','dora&picasso.lws.gltf'):
            path=args.project/'gltf'/filename_gltf
            data=json.loads(path.read_text('utf-8'))
            material=next(m for m in data['materials'] if m['name']==native['materials'][0]['name']['text'])
            assert (material['alphaMode'],material['alphaCutoff'])==('MASK',.5)
            info=material['pbrMetallicRoughness']['baseColorTexture']
            image=data['images'][data['textures'][info['index']]['source']]
            assert digest(path.parent/unquote(image['uri']))==binding['base_color_sha256']
            gltfs.append(filename_gltf)
        results.append(dict(object=name,mask_source_sha256=digest(args.source/filename),size=list(color.size),
                            alpha_max_delta_vs_pillow=max_delta,cutoff_differences_vs_pillow=threshold_differences,
                            discarded_pixels=sum(v<128 for v in alpha.tobytes()),
                            rgb_and_geometry_unchanged=True,gltf_checked=gltfs))
    manifest=json.loads((args.project/'IR/dora&picasso.lws/manifest.json').read_text('utf-8'))
    assert manifest['scene_clip_maps_evaluated']==3 and manifest['scene_clip_maps_not_evaluated']==0
    report=dict(passed=True,project=str(args.project),baseline=str(args.baseline),scene_clip_maps_evaluated=3,
                scene_clip_maps_not_evaluated=0,objects=results,
                scope='Native clip JPEGs sampled at plane positions using independently derived mapping; raw source provenance, glTF MASK, OBJ opacity pixels and unchanged RGB/geometry. Does not compare native LightWave lighting or image filtering.')
    args.report.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__': main()
