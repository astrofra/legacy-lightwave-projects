"""NormalShader private scope, vector-space conversion and exported tangent frames."""
import json
import math
import struct
import unittest
import subprocess
import sys
from pathlib import Path

import test_converter as fixtures
import test_gltf as gltf
from test_converter import F32, U16, chunk, form, s0, vx
from test_textures import imap, ilbm, png


def sub(tag, data):
    return chunk(tag, data, True)


def shader(path='normal.tga', blocks=None, space=0, count=1, extra=b'', enabled=1):
    blocks = imap('txtr', clip=17, uv='atlas') if blocks is None else blocks
    vparm = struct.pack('>II', 3, 1) + sub('VPVL', F32(.5,.5,1)+vx(0)) + sub('TBLK', blocks)
    private = sub('NSNS', struct.pack('>I', space))
    private += sub('NSNO', sub('VPVL',sub('VPRM',vparm)) + sub('ICNT',U16(count))
                   + sub('IMGS',sub('CLIP',sub('STIL',s0(path))))) + extra
    return sub('BLOK', sub('SHDR',s0('shader')+sub('ENAB',U16(enabled)))+sub('FUNC',s0('NormalShader')+private))


def mesh(points=None, triangles=None, uv=None, blocks=None, normal_values=None, clips=b''):
    points = points or [(0,0,0),(0,1,0),(0,0,1)]  # +X surface; RGB vectors are not tangent vectors.
    triangles = triangles or [(0,1,2)]
    uv = uv or [(0,0),(1,0),(0,1)]
    maps = chunk('VMAP', b'TXUV'+U16(2)+s0('atlas')+b''.join(vx(i)+F32(*v) for i,v in enumerate(uv)))
    if normal_values:
        maps += chunk('VMAP',b'NORM'+U16(3)+s0('normals')+b''.join(vx(i)+F32(*v) for i,v in enumerate(normal_values)))
    return form('LWO2',chunk('PNTS',F32(*(x for p in points for x in p))),
                chunk('POLS',b'FACE'+b''.join(U16(len(t))+b''.join(vx(i) for i in t) for t in triangles)),
                chunk('TAGS',s0('surface')),chunk('PTAG',b'SURF'+b''.join(vx(i)+U16(0) for i in range(len(triangles)))),maps,
                chunk('SURF',s0('surface')+s0('')+(shader() if blocks is None else blocks)),clips)


def unit(v):
    length=math.sqrt(sum(x*x for x in v)); return tuple(x/length for x in v)


def cross(a,b):
    return (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])


def encoded(n):
    return tuple(round((x+1)*127.5) for x in unit(n))


def decoded(rgb):
    return unit(tuple(x/127.5-1 for x in rgb[:3]))


class NormalMaps(unittest.TestCase):
    setUp=fixtures.Converter.setUp
    tearDown=fixtures.Converter.tearDown
    write=fixtures.Converter.write
    run_cli=fixtures.Converter.run_cli
    convert=fixtures.Converter.convert
    object_data=fixtures.Converter.object_data

    def export(self, raw=None, pixel=(230,190,128), options=('--normal-space','object'), rows=None, mask=0):
        rows = rows or [[pixel]*32 for _ in range(32)]
        self.write('normal.iff',ilbm(rows,mask=mask))
        source=self.write('mesh.lwo',mesh() if raw is None else raw)
        out,manifest=self.convert(source,*options,code=2)
        directory,native=self.object_data(out,manifest)
        document,buffers=gltf.load(out/manifest['assets'][0]['gltf'])
        return out,manifest,directory,native,document,buffers

    def reconstructed(self, data, buffers, rows):
        # Pure Python reference: recover object vectors from the exported glTF
        # at texel centers. Exercises actual accessors/PNG, not C internals.
        width,height=len(rows[0]),len(rows)
        for primitive in data['meshes'][0]['primitives']:
            if primitive['mode'] != 4: continue
            a=primitive['attributes']; ns=gltf.values(data,buffers,a['NORMAL'])
            ts=gltf.values(data,buffers,a['TANGENT']); uvs=gltf.values(data,buffers,a['TEXCOORD_0'])
            for n,t in zip(ns,ts):
                self.assertAlmostEqual(sum(v*v for v in t[:3]),1,places=5)
                self.assertLess(abs(sum(x*y for x,y in zip(n,t))),1e-5)
                self.assertIn(t[3],(-1,1))
            for start in range(0,len(ns),3):
                v=uvs[start:start+3]
                def edge(a,b,p):return (b[0]-a[0])*(p[1]-a[1])-(b[1]-a[1])*(p[0]-a[0])
                area=edge(v[0],v[1],v[2]); bverts=[tuple(x*t[3] for x in cross(n,t)) for n,t in zip(ns[start:start+3],ts[start:start+3])]
                # Test within each UV triangle, including outside the unit tile.
                for y in range(math.floor(min(p[1] for p in v)*height),math.ceil(max(p[1] for p in v)*height)):
                    for x in range(math.floor(min(p[0] for p in v)*width),math.ceil(max(p[0] for p in v)*width)):
                        p=((x+.5)/width,(y+.5)/height)
                        w=[edge(v[1],v[2],p)/area,edge(v[2],v[0],p)/area];w.append(1-sum(w))
                        if min(w)<.05:continue
                        columns=[]
                        for vectors in (ts[start:start+3],bverts,ns[start:start+3]):
                            columns.append(unit(tuple(sum(w[i]*vectors[i][k] for i in range(3)) for k in range(3))))
                        value=decoded(rows[y%height][x%width])
                        yield unit(tuple(sum(columns[j][k]*value[j] for j in range(3)) for k in range(3)))

    def assert_reconstruction(self, data, buffers, rows, expected, tolerance=.7):
        worst,count=0,0
        for actual in self.reconstructed(data,buffers,rows):
            worst=max(worst,math.degrees(math.acos(max(-1,min(1,sum(a*b for a,b in zip(actual,expected)))))));count+=1
        self.assertGreater(count,100)
        self.assertLess(worst,tolerance)

    def test_private_image_scope_and_object_vectors(self):
        # Global CLIP 17 and private IMAG 17 intentionally refer to different images.
        self.write('color.iff',ilbm([[(255,0,0)]]))
        raw=mesh(clips=chunk('CLIP',struct.pack('>I',17)+sub('STIL',s0('color.iff'))))
        _,m,d,n,g,b=self.export(raw)
        self.assertEqual((d/'source.bin').read_bytes(),raw)
        private=next(i for i in n['image_references'] if i['clip_scope'])
        self.assertTrue(private['resolved_path'].endswith('normal.iff'))
        self.assertEqual(private['resolution'],'unique-image-stem')
        self.assertEqual(private['clip'],17)
        normal=next(t for t in n['materials'][0]['textures'] if t['channel']=='NORM')
        self.assertEqual(normal['normal_shader']['native_NSNS'],0)
        self.assertEqual(normal['export_status'],'approximated')
        self.assertEqual(m['assets'][0]['texture_blocks_not_evaluated'],0)
        self.assertIn('normalTexture',g['materials'][0])
        primitive=g['meshes'][0]['primitives'][0]
        for t in gltf.values(g,b,primitive['attributes']['TANGENT']):
            self.assertEqual(tuple(t),(0,1,0,-1))  # +Y tangent, -Z V-up bitangent after source reflection.
        self.assertNotIn('baseColorTexture',g['materials'][0]['pbrMetallicRoughness'])
        rows=png(d/n['materials'][0]['derived_maps']['normal'])
        source=decoded((230,190,128));self.assert_reconstruction(g,b,rows,(source[0],source[1],-source[2]))

    def test_world_matrix_transpose_nonuniform_shear(self):
        matrix=[2,.5,0, 0,1,.25, 0,0,.5]
        pixel=(190,180,210);n0=decoded(pixel)
        expected=unit(tuple(sum(matrix[j*3+k]*n0[j] for j in range(3)) for k in range(3)))
        _,_,d,n,g,b=self.export(pixel=pixel,options=('--normal-space','world','--normal-world-matrix',','.join(map(str,matrix))))
        rows=png(d/n['materials'][0]['derived_maps']['normal'])
        self.assert_reconstruction(g,b,rows,(expected[0],expected[1],-expected[2]))
        self.assertEqual(n['materials'][0]['textures'][1]['normal_conversion']['source_world_matrix'],matrix)

    def test_smooth_normals_full_inverse_and_mirrored_uvs(self):
        values=[unit((1,0,.3)),unit((1,.4,0)),unit((1,-.3,-.4))]
        for uv in ([(0,0),(1,0),(0,1)],[(1,0),(0,0),(1,1)],[(1,0),(2,0),(1,1)]):
            with self.subTest(uv=uv):
                _,_,d,n,g,b=self.export(mesh(uv=uv,normal_values=values))
                source=decoded((230,190,128))
                self.assert_reconstruction(g,b,png(d/n['materials'][0]['derived_maps']['normal']),(source[0],source[1],-source[2]))

    def test_tangent_override_and_green_sign(self):
        _,_,d,n,g,b=self.export(options=('--normal-space','tangent','--normal-green','negative'))
        rows=png(d/n['materials'][0]['derived_maps']['normal']);source=decoded((230,190,128));expected=(source[0],-source[1],source[2])
        center=decoded(rows[24][4])
        self.assertLess(math.degrees(math.acos(min(1,sum(a*b for a,b in zip(center,expected))))),.5)
        self.assertIn('TANGENT',g['meshes'][0]['primitives'][0]['attributes'])

    def test_ambiguous_flat_surface_abstains_and_off(self):
        for mode in ('auto','off'):
            _,_,_,n,g,_=self.export(options=('--normal-space',mode))
            self.assertNotIn('normal',n['materials'][0]['derived_maps'])
            self.assertNotIn('normalTexture',g['materials'][0])
            t=n['materials'][0]['textures'][1]
            self.assertIn('ambiguous' if mode=='auto' else 'disabled',t['issue'])
            self.assertEqual(t['export_status'],'preserved-only')

    def test_overlap_conflicts_are_rejected(self):
        raw=mesh(points=[(0,0,0),(0,1,0),(0,0,1),(2,0,0),(3,0,0),(2,1,0)],triangles=[(0,1,2),(3,4,5)],uv=[(0,0),(1,0),(0,1)]*2)
        _,_,_,n,g,_=self.export(raw)
        self.assertNotIn('normalTexture',g['materials'][0])
        t=n['materials'][0]['textures'][1]
        self.assertIn('conflicting',t['issue'])
        self.assertGreater(t['normal_conversion']['raster']['conflicts'],100)

    def test_disabled_missing_uv_degenerate_and_budget(self):
        cases=[(mesh(blocks=shader(enabled=0)),'disabled'),
               (mesh(blocks=shader(blocks=imap('txtr',uv='missing'))),'TXUV'),
               (mesh(uv=[(0,0)]*3),'degenerate'),
               (mesh(uv=[(0,0),(1e6,0),(0,1e6)]),'budget'),
               (mesh(blocks=shader()+shader()),'multiple')]
        for raw,issue in cases:
            with self.subTest(issue=issue):
                _,_,_,n,g,_=self.export(raw)
                self.assertNotIn('normalTexture',g['materials'][0])
                self.assertIn(issue,next(t for t in n['materials'][0]['textures'] if t['channel']=='NORM')['issue'])

    def test_malformed_or_unknown_plugin_is_preserved(self):
        cases=[shader(count=2),shader(extra=sub('????',b'unknown')),
               shader(blocks=imap('txtr')+imap('txtr')),shader().replace(b'NSNS\x00\x04',b'NSNS\xff\xff')]
        for blocks in cases:
            with self.subTest(blocks=blocks[:32]):
                raw=mesh(blocks=blocks);_,_,d,n,g,_=self.export(raw)
                self.assertEqual((d/'source.bin').read_bytes(),raw)
                self.assertEqual(len(n['materials'][0]['textures']),1)
                self.assertIn('serialization',n['materials'][0]['textures'][0]['issue'])
                self.assertNotIn('normalTexture',g['materials'][0])

    def test_auto_object_with_varied_normals_and_no_filename_guess(self):
        normals=[(1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)]
        points=[];uv=[];triangles=[]
        rows=[[(128,128,255)]*96 for _ in range(32)]
        for i,n in enumerate(normals):
            axis=(0,1,0) if n[1]==0 else (1,0,0);other=cross(n,axis)
            origin=(i*3,0,0);points.extend([origin,tuple(a+b for a,b in zip(origin,axis)),tuple(a+b for a,b in zip(origin,other))]);triangles.append(tuple(range(3*i,3*i+3)))
            x,y=i%3,i//3
            uv.extend([(x/3,y/2),((x+1)/3,y/2),(x/3,(y+1)/2)])
            for row in range((1-y)*16,(2-y)*16):
                for col in range(x*32,(x+1)*32):rows[row][col]=encoded(n)
        _,_,_,n,g,_=self.export(mesh(points,triangles,uv),rows=rows,options=())
        self.assertIn('normalTexture',g['materials'][0])
        inference=n['materials'][0]['textures'][1]['normal_conversion']
        self.assertEqual(inference['effective_space'],'object')
        self.assertGreater(inference['inference']['samples'],128)
        self.assertIn('hypothesis',inference['space_evidence'])

    def test_skinned_normal_mesh_keeps_skin_and_animation_accessors(self):
        from test_skin import weight_map, bone, scene_bytes, accessor
        from test_ik import motion
        raw=mesh()
        weights=weight_map('weight0',[(i,1) for i in range(3)])
        raw=raw[:4]+struct.pack('>I',len(raw)-8+len(weights))+raw[8:]+weights
        self.write('rig.lwo',raw);self.write('normal.iff',ilbm([[(230,190,128)]*32 for _ in range(32)]))
        scene=scene_bytes(bones=bone(extra=motion(**{'0':[(0,0),(1,1)]}))).replace('LastFrame 0','LastFrame 30')
        out,manifest=self.convert(self.write('rig.lws',scene),'--normal-space','object',code=2)
        data,buffers=gltf.load(out/manifest['gltf_rigs'][0]['gltf'])
        primitive=data['meshes'][0]['primitives'][0];attrs=primitive['attributes']
        self.assertEqual(len(accessor(data,buffers,attrs['TANGENT'])),3)
        self.assertEqual(accessor(data,buffers,attrs['JOINTS_0']),[(1,0,0,0)]*3)
        self.assertEqual(accessor(data,buffers,attrs['WEIGHTS_0']),[(1,0,0,0)]*3)
        self.assertEqual(len(accessor(data,buffers,data['skins'][0]['inverseBindMatrices'])),2)
        self.assertTrue(data['animations'])
        for sampler in data['animations'][0]['samplers']:
            self.assertEqual(data['accessors'][sampler['input']]['type'],'SCALAR')
            self.assertIn(data['accessors'][sampler['output']]['type'],('VEC3','VEC4'))

    def test_transparent_vectors_are_preserved_without_compositing_guess(self):
        _,_,_,n,g,_=self.export(rows=[[(230,190,128,0)]*32 for _ in range(32)],mask=1)
        self.assertNotIn('normalTexture',g['materials'][0])
        self.assertIn('compositing',n['materials'][0]['textures'][1]['issue'])

    def test_negative_repeat_and_identical_overlaps(self):
        raw=mesh(triangles=[(0,1,2),(0,1,2)],uv=[(-1,0),(0,0),(-1,1)])
        _,_,d,n,g,b=self.export(raw)
        t=n['materials'][0]['textures'][1]
        self.assertEqual(t['normal_conversion']['raster']['conflicts'],0)
        self.assertGreater(t['normal_conversion']['raster']['overlap_samples'],100)
        source=decoded((230,190,128))
        self.assert_reconstruction(g,b,png(d/n['materials'][0]['derived_maps']['normal']),(source[0],source[1],-source[2]))

    def test_batch_forwards_world_matrix_and_green_override(self):
        self.write('mesh.lwo',mesh());self.write('normal.iff',ilbm([[(230,190,128)]*32 for _ in range(32)]))
        script=Path(__file__).resolve().parents[1]/'tools/batch_convert.py'
        output=self.base/'batch-output'
        result=subprocess.run([sys.executable,'-X','utf8',str(script),'--content',str(self.root),'--output-root',str(output),
                               '--converter',fixtures.EXE,'--normal-space','world','--normal-world-matrix=-1,0,0,0,1,0,0,0,1',
                               '--normal-green','negative'],capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(result.returncode,2,result.stdout+result.stderr)
        report_path=next(output.glob('batch-*/batch-report.json'));report=json.loads(report_path.read_text())
        record=next(r for r in report['files'] if r['source']=='mesh.lwo')
        manifest_path=report_path.parent/record['manifest'];manifest=json.loads(manifest_path.read_text())
        native=json.loads((manifest_path.parent/manifest['assets'][0]['uri']).read_text())
        m=native['materials'][0];self.assertIn('normal',m['derived_maps'])
        settings=next(t['normal_conversion'] for t in m['textures'] if t['channel']=='NORM')
        self.assertEqual(settings['requested_space'],'world');self.assertEqual(settings['source_green'],'negative')
        self.assertEqual(settings['source_world_matrix'],[-1,0,0,0,1,0,0,0,1])

    def test_world_requires_known_nonsingular_matrix(self):
        source=self.write('mesh.lwo',mesh())
        for options in [('--normal-space','world'),('--normal-world-matrix','1,0,0,0,1,0,0,0,1'),
                        ('--normal-space','world','--normal-world-matrix','0,0,0,0,0,0,0,0,0'),
                        ('--normal-space','world','--normal-world-matrix','1,0,0,0,nan,0,0,0,1')]:
            result=self.run_cli('convert',source,'--output',self.base/'invalid',*options,code=1)
            self.assertIn('arguments',result.stderr)
            self.assertFalse((self.base/'invalid').exists())


if __name__=='__main__': unittest.main(argv=[__file__])
