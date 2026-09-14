"""Clip coverage, grayscale PSD bounds, instance isolation and batch portability."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import unittest
from urllib.parse import unquote

import test_projections as projections
import test_textures as tx
from test_converter import EXE
from test_gltf import load

ROOT = Path(__file__).resolve().parents[1]


def psd(rows, compression=1, depth=8):
    width, height = len(rows[0]), len(rows)
    raw = [bytes(row) if depth == 8 else struct.pack('>' + 'H' * width, *row) for row in rows]
    header = struct.pack('>4sH6sHIIHH', b'8BPS', 1, bytes(6), 1, height, width, depth, 1)
    data = b''.join(raw)
    if compression:
        packed = [b'\x80' + bytes([len(row)-1]) + row + b'\x80' for row in raw]
        data = struct.pack('>' + 'H' * height, *(len(row) for row in packed)) + b''.join(packed)
    return header + bytes(12) + struct.pack('>H', compression) + data


def clip(negative=1, center='0 0 0', extra='', wrap='1 1', projection=0, axis=2):
    return f'''ClipMaps
{{ TextureBlock
 {{ Texture
  {{ ImageMap
   "mask"
   Enable 1
   Negative {negative}
   {{ Opacity
    0
    1
    0
   }}
  }}
  {{ TextureMap
   {{ Center
    {center}
    0
   }}
   {{ Size
    1 1 1
    0
   }}
   Coordinates 0
  }}
  Projection {projection}
  Axis {axis}
  {{ Image
   {{ Clip
    {{ Still
     "maps/mask.psd"
    }}
   }}
  }}
  WrapOptions {wrap}
  {extra}
 }}
}}
'''


class ClipTests(unittest.TestCase):
    setUp = tx.TextureTests.setUp
    tearDown = tx.TextureTests.tearDown
    write = tx.TextureTests.write
    convert = tx.TextureTests.convert
    run_cli = tx.TextureTests.run_cli
    object_data = tx.TextureTests.object_data

    def fixture(self, kind='LWO2', mask=None, wrap=(1,1), projection=0):
        self.write('maps/screen.iff', tx.ilbm([[(20,100,220)] * 4]))
        self.write('maps/mask.psd', mask or psd([[0,127,128,255]]))
        case = dict(axis=2, projection=projection, size=[1,1,1], center=[0,0,0], rotation=[0,0,0], tiles=[1,1])
        return self.write('mesh.lwo', projections.projected(kind, case, points=[[-.5,-.5,0],[.5,-.5,0],[0,.5,.2]], wrap=wrap))

    def scene(self, name, masks):
        return self.write(name, 'LWSC\n3\n' + ''.join('LoadObjectLayer 1 mesh.lwo\n' + mask for mask in masks))

    def test_standalone_consensus_both_formats_polarities_and_geometry(self):
        for kind in ('LWOB','LWO2'):
            for negative in (0,1):
                source = self.fixture(kind)
                a = self.scene('01.lws', [clip(negative)])
                self.scene('02.lws', [clip(negative)])  # identical bytes, separate provenance
                out, manifest = self.convert(source, code=2)
                directory, native = self.object_data(out, manifest)
                data, buffers = load(out / manifest['assets'][0]['gltf'])
                material = data['materials'][0]
                self.assertEqual((material['alphaMode'], material['alphaCutoff']), ('MASK', .5))
                self.assertNotIn('extensionsRequired', data)
                binding = next(b for b in native['derived_clip_maps']['bindings'] if b['node_id'] is None)
                self.assertEqual(binding['evidence_count'], 2)
                image = tx.png(directory / binding['base_color'])[0]
                expected = [0,127,128,255] if negative else [255,128,127,0]
                self.assertEqual([p[3] for p in image], expected)
                self.assertEqual([p[:3] for p in image], [(20,100,220)] * 4)
                self.assertEqual([p[0] for p in tx.png(directory / binding['opacity'])[0]], [255 if a>=128 else 0 for a in expected])
                for b in native['derived_clip_maps']['bindings']:
                    if b.get('scene_uri'):
                        raw = (directory / b['scene_uri']).read_bytes()
                        self.assertEqual(hashlib.sha256(raw).hexdigest(), b['scene_sha256'])
                        self.assertEqual(raw[b['source_offset']:b['source_offset']+b['source_bytes']], clip(negative).strip().encode())
                        self.assertTrue((directory / b['image_uri']).is_file())
                self.assertEqual((directory / 'source.bin').read_bytes(), source.read_bytes())
                # Alpha must not move or subdivide the mesh.
                old_bin = buffers
                a.unlink(); (a.parent / '02.lws').unlink()
                plain, plain_manifest = self.convert(source, code=2)
                plain_data, plain_buffers = load(plain / plain_manifest['assets'][0]['gltf'])
                self.assertEqual(old_bin, plain_buffers)
                self.assertEqual(plain_data['materials'][0]['alphaMode'], 'OPAQUE')

    def test_scene_instance_isolation_and_mesh_reuse(self):
        self.fixture()
        scene = self.scene('shot.lws', [clip(), clip(), clip(0), ''])
        out, manifest = self.convert(scene, code=2)
        data, _ = load(out / manifest['scene_gltf'])
        meshes = [n['mesh'] for n in data['nodes'] if 'mesh' in n]
        self.assertEqual(len(meshes), 4)
        self.assertEqual(meshes[0], meshes[1])
        self.assertEqual(len(set(meshes)), 3)
        self.assertEqual([data['materials'][data['meshes'][m]['primitives'][0]['material']]['alphaMode'] for m in meshes], ['MASK','MASK','MASK','OPAQUE'])
        self.assertEqual(manifest['scene_clip_maps_evaluated'], 3)
        self.assertEqual(manifest['scene_clip_maps_not_evaluated'], 0)
        obj_data, _ = load(out / manifest['assets'][0]['gltf'])
        self.assertEqual(obj_data['materials'][0]['alphaMode'], 'OPAQUE')
        scene_ir = json.loads((out / manifest['scene']).read_text())
        self.assertEqual(scene_ir['nodes'][0]['clip_maps'][0]['coverage']['polarity'], 'white-keeps')
        self.assertEqual(scene_ir['nodes'][2]['clip_maps'][0]['coverage']['polarity'], 'white-clips')
        standalone, sm = self.convert(self.base / 'content/mesh.lwo', code=2)
        sd, _ = load(standalone / sm['assets'][0]['gltf'])
        self.assertEqual(sd['materials'][0]['alphaMode'], 'OPAQUE')

    def test_incompatible_or_animated_projection_not_silently_applied(self):
        for mask in (clip(center='1 0 0'), clip(extra='FutureSetting 1'), clip().replace('0 0 0\n    0','0 0 0\n    1'), clip(extra='{ Procedural\nType Checkerboard\n}')):
            self.fixture()
            scene = self.scene('shot.lws', [mask])
            out, manifest = self.convert(scene, code=2)
            data, _ = load(out / manifest['scene_gltf'])
            self.assertEqual(data['materials'][0]['alphaMode'], 'OPAQUE')
            self.assertEqual(manifest['scene_clip_maps_not_evaluated'], 1)
            _, native = self.object_data(out, manifest)
            self.assertTrue(native['derived_clip_maps']['bindings'][0]['issue'])

    def test_wrap_atlas_and_sphere_keep_mask_aligned(self):
        for projection in (0,2):
            for wrap in ((0,3),(2,2)):
                source = self.fixture(wrap=wrap, projection=projection, mask=psd([[255,255,255,255]]))
                self.scene('shot.lws', [clip(wrap=f'{wrap[0]} {wrap[1]}', projection=projection)])
                out, manifest = self.convert(source, code=2)
                directory, native = self.object_data(out, manifest)
                b = next(b for b in native['derived_clip_maps']['bindings'] if b['node_id'] is None)
                opaque = tx.png(directory / native['materials'][0]['derived_maps']['base_color'])
                masked = tx.png(directory / b['base_color'])
                self.assertEqual(len(opaque),len(masked))
                for a,z in zip(opaque,masked):
                    self.assertEqual([p[:3] for p in a],[p[:3] for p in z])
                    for p,q in zip(a,z):
                        self.assertEqual(q[3],0 if p[:3]==(0,0,0) else 255)

    def test_grayscale_psd_raw_rle_8_16_and_truncation(self):
        for depth in (8,16):
            for compression in (0,1):
                levels=[0,127,128,255]
                raw = psd([[x*(257 if depth==16 else 1) for x in levels]], compression, depth)
                source = self.fixture(mask=raw)
                self.scene('shot.lws', [clip()])
                out, manifest = self.convert(source, code=2)
                directory, native = self.object_data(out, manifest)
                b = next(b for b in native['derived_clip_maps']['bindings'] if b['node_id'] is None)
                self.assertEqual([p[3] for p in tx.png(directory/b['base_color'])[0]],levels)
        for raw in (psd([[255]*4])[:-1], psd([[255]*4])[:30], psd([[255]*4])[:38]+b'\xff\xff'+psd([[255]*4])[40:]):
            source = self.fixture(mask=raw)
            out, manifest = self.convert(source, code=2)
            data, _ = load(out / manifest['assets'][0]['gltf'])
            self.assertEqual(data['materials'][0]['alphaMode'], 'OPAQUE')

    def test_batch_keeps_mask_and_provenance_portable(self):
        source = self.fixture()
        scene = self.scene('shot.lws', [clip(), ''])
        result = subprocess.run([sys.executable,'-X','utf8',str(ROOT/'tools/batch_convert.py'), '--content',str(source.parent), '--output-root',str(self.base/'batch'), '--converter',EXE],capture_output=True,text=True,encoding='utf-8',timeout=60)
        self.assertEqual(result.returncode,2,result.stdout+result.stderr)
        report = next((self.base/'batch').glob('batch-*/batch-report.json'))
        projects = report.parent / 'packages'
        masked = 0
        for path in projects.rglob('*.gltf'):
            data, _ = load(path)
            for material in data.get('materials',[]):
                if material['alphaMode']=='MASK':
                    masked+=1
                    info=material['pbrMetallicRoughness']['baseColorTexture']
                    image=data['images'][data['textures'][info['index']]['source']]
                    self.assertIn('cutout',image['uri'])
                    self.assertTrue((path.parent/unquote(image['uri'])).is_file())
        self.assertGreater(masked,0)
        for path in projects.rglob('object.json'):
            native=json.loads(path.read_text())
            for b in native.get('derived_clip_maps',{}).get('bindings',[]):
                for key in ('scene_uri','image_uri','base_color','opacity'):
                    if b.get(key): self.assertTrue((path.parent/b[key]).is_file(), (path,key,b[key]))


if __name__ == '__main__':
    unittest.main()
