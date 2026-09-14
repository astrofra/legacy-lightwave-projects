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


def legacy_clip(flags=4, value=.5, size='1 1 1', center='0 0 0', wrap='1 1', axis=2, extra=''):
    return f'''ClipMap Planar Image Map
TextureImage maps/mask.psd
TextureWrapModes {wrap}
TextureFlags {flags}
TextureAxis {axis}
TextureSize {size}
TextureCenter {center}
TextureValue {value}
{extra}'''


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

    def test_lwsc1_flags_are_independent_of_axis_and_value(self):
        # Native LWSC1 import probe: bit 1 world, 2 invert, 4 pixel, 8 AA.
        for kind in ('LWOB', 'LWO2'):
            source = self.fixture(kind)
            for flags in (0,2,4,6,8,14):
                for value in (0,.5,1):
                    text = legacy_clip(flags=flags, value=value)
                    scene = self.write('shot.lws', 'LWSC\n1\nLoadObject mesh.lwo\n' + text)
                    out, manifest = self.convert(scene, code=2)
                    directory, native = self.object_data(out, manifest)
                    binding = native['derived_clip_maps']['bindings'][0]
                    self.assertEqual(manifest['scene_clip_maps_evaluated'], 1)
                    expected = [0,127,128,255] if flags&2 else [255,128,127,0]
                    self.assertEqual([p[3] for p in tx.png(directory/binding['base_color'])[0]], expected)
                    raw = (directory/binding['scene_uri']).read_bytes()
                    self.assertEqual(raw[binding['source_offset']:binding['source_offset']+binding['source_bytes']], text.strip().encode())
            self.assertEqual((directory/'source.bin').read_bytes(), source.read_bytes())

    def test_legacy_unsupported_parameters_preserved_without_guessing(self):
        self.fixture()
        for text in (legacy_clip(flags=1), legacy_clip(flags=16), legacy_clip(axis=3),
                     legacy_clip(size='0 1 1'), legacy_clip(size='1e300 1 1'),
                     legacy_clip(extra='TextureVelocity 1 0 0'), legacy_clip(extra='TextureUnknown 1'),
                     legacy_clip().replace('Planar','Spherical')):
            scene = self.write('shot.lws', 'LWSC\n1\nLoadObject mesh.lwo\n' + text)
            out, manifest = self.convert(scene, code=2)
            data, _ = load(out/manifest['scene_gltf'])
            self.assertEqual(data['materials'][0]['alphaMode'], 'OPAQUE')
            self.assertEqual(manifest['scene_clip_maps_not_evaluated'], 1)

    def test_independent_planar_mask_size_center_and_wrapping(self):
        # A full physical unit square: each atlas sample corresponds to a known
        # point on this plane. Check the mask at that point, not at color UVs.
        import math
        for kind in ('LWOB', 'LWO2'):
            for axis in (0,1,2):
                for size, center in ((.5,.125),(-.5,-.125),(.995,0)):
                    for wrap in (0,1,2,3):
                        self.fixture(kind, wrap=(0,3), mask=psd([[0,64,192,255]]))
                        ua,va = (2 if axis==0 else 0), (2 if axis==1 else 1)
                        case=dict(axis=axis,projection=0,size=[1,1,1],center=[0,0,0],rotation=[0,0,0],tiles=[1,1])
                        points=[]
                        for u,v in ((-.5,-.5),(.5,-.5),(.5,.5),(-.5,.5)):
                            p=[0,0,0]; p[ua]=u; p[va]=v; points.append(p)
                        source=self.write('mesh.lwo',projections.projected(kind,case,points=points,wrap=(0,3)))
                        sz=[1,1,1]; sz[ua]=size
                        ct=[0,0,0]; ct[ua]=center
                        mask=legacy_clip(flags=6,axis=axis,size=' '.join(map(str,sz)),center=' '.join(map(str,ct)),wrap=f'{wrap} 3')
                        self.write('shot.lws','LWSC\n1\nLoadObject mesh.lwo\n'+mask)
                        out,manifest=self.convert(source,code=2)
                        directory,native=self.object_data(out,manifest)
                        b=next(b for b in native['derived_clip_maps']['bindings'] if b['node_id'] is None)
                        self.assertIn('mask_uv_transform',b)
                        result=tx.png(directory/b['base_color'])
                        # 4x1 base image + one-pixel gutter on every side.
                        self.assertEqual((len(result[0]),len(result)),(6,3))
                        expected=[]
                        for x in range(6):
                            physical_x=(x-1+.5)/4-.5
                            u=.5+(physical_x-center)/size
                            reset=wrap==0 and not 0<=u<=1
                            if wrap==0 or wrap==3: u=max(0,min(1,u))
                            elif wrap==1: u-=math.floor(u)
                            else:
                                u-=2*math.floor(u/2)
                                if u>1: u=2-u
                            expected.append(0 if reset else [0,64,192,255][min(3,int(u*4))])
                        self.assertEqual([p[3] for p in result[1]],expected)
                        base=tx.png(directory/native['materials'][0]['derived_maps']['base_color'])
                        self.assertEqual([[p[:3] for p in row] for row in result],[[p[:3] for p in row] for row in base])

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
