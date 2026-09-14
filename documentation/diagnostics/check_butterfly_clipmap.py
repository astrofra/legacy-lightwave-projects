"""Read-only pixel/provenance QA for the Butterfly clip-map conversion (Pillow)."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import unquote

from PIL import Image


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    obj = args.source / 'butterfly.lwo'
    mask = args.source / 'butterfly_clipmap.psd'
    ir = args.project / 'IR/butterfly.lwo'
    native = json.loads((ir/'object.json').read_text('utf-8'))
    assert digest(obj) == native['source']['sha256'] == digest(ir/'source.bin')
    alpha = Image.open(mask).convert('L')
    baseline_doc = json.loads((args.baseline/'gltf/butterfly.lwo.gltf').read_text('utf-8'))
    baseline = Image.open(args.baseline/'gltf'/unquote(baseline_doc['images'][0]['uri'])).convert('RGBA')
    assert alpha.size == baseline.size == (215,190)
    results = []
    for path in sorted((args.project/'gltf').glob('*.gltf')):
        data = json.loads(path.read_text('utf-8'))
        for material in data.get('materials', []):
            if material['name'] not in ('butter_01','butter_02'):
                continue
            assert material['alphaMode'] == 'MASK' and material['alphaCutoff'] == .5
            info = material['pbrMetallicRoughness']['baseColorTexture']
            image = data['images'][data['textures'][info['index']]['source']]
            png = path.parent / unquote(image['uri'])
            rgba = Image.open(png).convert('RGBA')
            assert rgba.size == alpha.size
            assert rgba.getchannel('A').tobytes() == alpha.tobytes()
            assert rgba.convert('RGB').tobytes() == baseline.convert('RGB').tobytes()
            assert digest(png) == image['extras']['sha256']
            results.append({'file':path.name,'material':material['name'],'alpha_mode':'MASK',
                            'cutoff':.5,'png_sha256':digest(png),'alpha_matches_native_mask':True,
                            'rgb_matches_previous_export':True})
    assert len(results) == 16  # two wings in one object and seven scene files
    manifests = [json.loads(f.read_text('utf-8')) for f in (args.project/'IR').glob('*.lws/manifest.json')]
    assert len(manifests) == 7
    assert sum(m['scene_clip_maps_evaluated'] for m in manifests) == 14
    assert sum(m['scene_clip_maps_not_evaluated'] for m in manifests) == 0
    archived = 0
    for b in native['derived_clip_maps']['bindings']:
        if b.get('scene_uri'):
            assert digest(ir/b['scene_uri']) == b['scene_sha256']
            assert digest(ir/b['image_uri']) == b['image_sha256'] == digest(mask)
            archived += 1
    # Geometry/UV output bytes remain identical to the pre-mask export.
    assert (args.project/'gltf/butterfly.lwo.bin').read_bytes() == (args.baseline/'gltf/butterfly.lwo.bin').read_bytes()
    report = {'passed':True,'project':str(args.project),'source_object_sha256':digest(obj),
              'source_mask_sha256':digest(mask),'mask_size':list(alpha.size),
              'opaque_pixels_at_cutoff':sum(v>=128 for v in alpha.tobytes()),
              'discarded_pixels_at_cutoff':sum(v<128 for v in alpha.tobytes()),
              'scene_clip_maps_evaluated':14,'scene_clip_maps_not_evaluated':0,
              'object_ir_archived_bindings_checked':archived,'geometry_buffer_unchanged':True,
              'materials':results,'scope':'Pixels and structural provenance, not a LightWave lighting comparison.'}
    args.report.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='materials'},indent=2))


if __name__ == '__main__':
    main()
