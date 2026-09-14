"""Independent native UV observations and end-to-end wrap/IR/OBJ/glTF checks."""
import itertools
import json
import math
from pathlib import Path
import struct
import unittest

import test_textures as tx
from test_converter import F32, U16, chunk, form, s0, vx
from test_gltf import load, values, source_map


def projected(kind, case, points=None, wrap=(1,1), channel='COLR', extra=b''):
    if points is None:
        p=case['point']
        points=[p,[p[0]+.0001,p[1]+.0002,p[2]+.0003],
                [p[0]+.0002,p[1]-.0001,p[2]+.0001]]
    if kind=='LWOB':
        blocks=tx.texture(tag={'COLR':'CTEX','SPEC':'STEX'}[channel],flags=1<<case['axis'],
            projection='Spherical Image Map' if case['projection']==2 else 'Planar Image Map',
            extra=b''.join(chunk(tag,F32(*v),True) for tag,v in [('TSIZ',case['size']),('TCTR',case['center'])])
            +chunk('TFP0',F32(case['tiles'][0]),True)+chunk('TFP1',F32(case['tiles'][1]),True)
            +chunk('TWRP',U16(wrap[0])+U16(wrap[1]),True)+extra)
        return tx.textured(blocks,points)
    tmap=b''.join(chunk(tag,F32(*v)+vx(0),True) for tag,v in
                  [('CNTR',case['center']),('SIZE',case['size']),('ROTA',case['rotation'])])
    blocks=tx.imap(channel=channel,extra=chunk('PROJ',U16(case['projection']),True)
        +chunk('AXIS',U16(case['axis']),True)+chunk('TMAP',tmap,True)
        +chunk('WRPW',F32(case['tiles'][0])+vx(0),True)+chunk('WRPH',F32(case['tiles'][1])+vx(0),True)
        +chunk('WRAP',U16(wrap[0])+U16(wrap[1]),True)+extra)
    return form('LWO2',chunk('PNTS',F32(*(v for p in points for v in p))),
        chunk('POLS',b'FACE'+U16(3)+vx(0)+vx(1)+vx(2)),chunk('TAGS',s0('surface')),
        chunk('PTAG',b'SURF'+vx(0)+U16(0)),chunk('SURF',s0('surface')+s0('')
        +chunk('COLR',F32(1,1,1)+vx(0),True)+blocks),
        chunk('CLIP',struct.pack('>I',17)+chunk('STIL',s0('maps/screen.iff'),True)))


class ProjectionTests(unittest.TestCase):
    setUp=tx.TextureTests.setUp
    tearDown=tx.TextureTests.tearDown
    write=tx.TextureTests.write
    convert=tx.TextureTests.convert
    run_cli=tx.TextureTests.run_cli
    object_data=tx.TextureTests.object_data

    def evaluate(self, raw):
        out,manifest=self.convert(self.write('projected.lwo',raw),code=2)
        directory,native=self.object_data(out,manifest)
        data,buffers=load(out/manifest['assets'][0]['gltf'])
        actual={}
        for p in data['meshes'][0]['primitives']:
            if p['mode']!=4: continue
            for (_,_,point),uv in zip(source_map(p,buffers),values(data,buffers,p['attributes']['TEXCOORD_0'])):
                actual[point]=(uv[0],1-uv[1])
        obj=(out/manifest['assets'][0]['obj']).read_text()
        corners=[tuple(map(float,line.split()[1:])) for line in obj.splitlines() if line.startswith('vt ')]
        for i,uv in actual.items():
            for a,b in zip(uv,corners[i]): self.assertAlmostEqual(a,b,places=5)
        self.assertEqual((directory/'source.bin').read_bytes(),raw)
        return actual,native['materials'][0],directory

    def test_native_sdk_measurements_both_formats_all_axes_and_transforms(self):
        self.write('maps/screen.iff',tx.ilbm([[(255,255,255)]]))
        hosts=json.loads((Path(__file__).parent/'fixtures/texture_projection_oracle.json').read_text())['hosts']
        for host in hosts:
            for kind in ('LWOB','LWO2'):
                for index,c in enumerate(host['cases']):
                    if kind=='LWOB' and any(c['rotation']): continue
                    if c['projection']==2 and not any(c['rotation']):
                        q=[p-s for p,s in zip(c['point'],c['center'])]
                        if sum(q[i]**2 for i in range(3) if i!=c['axis'])<1e-14: continue # pole checked below
                    with self.subTest(host=host['runtime'],kind=kind,case=index):
                        uv,m,_=self.evaluate(projected(kind,c)); actual=uv[0]
                        self.assertEqual(m['textures'][0]['export_status'],'approximated')
                        for axis in range(2):
                            delta=actual[axis]-c['uv'][axis]
                            if c['projection']==2:
                                # SDK 9.6 reduces UV modulo one. Polygon unwrapping may
                                # select an adjacent longitude cycle before tiling.
                                if axis==0 and c['tiles'][0]: delta-=round(delta/c['tiles'][0])*c['tiles'][0]
                                if host['runtime']=='lightwave96': delta-=round(delta)
                            self.assertLess(abs(delta),2e-6)

    def test_pole_borrows_local_longitude_before_tiling(self):
        self.write('maps/screen.iff',tx.ilbm([[(255,255,255)]]))
        c=dict(projection=2,axis=1,point=[0,0,0],center=[0,0,0],size=[2,3,4],rotation=[0,0,0],tiles=[2.5,.75])
        for kind in ('LWOB','LWO2'):
            uv,_,_=self.evaluate(projected(kind,c,[(.01,.8,1),(-.01,.8,1),(0,1,0)]))
            self.assertLess(abs(uv[0][0]-uv[1][0]),.01)
            self.assertAlmostEqual(uv[2][0],(uv[0][0]+uv[1][0])/2,places=6)
            self.assertAlmostEqual(uv[2][1],.75,places=6)

    def test_all_wrap_modes_and_mixed_axes_in_both_formats(self):
        rows=[[(255,0,0),(0,255,0)],[(0,0,255),(255,255,0)]]
        self.write('maps/screen.iff',tx.ilbm(rows))
        c=dict(projection=0,axis=2,point=[0,0,0],center=[0,0,0],size=[1,1,1],rotation=[0,0,0],tiles=[1,1])
        points=[(-.75,-.75,0),(1.75,-.75,0),(-.75,1.75,0)]
        for kind,ws,wt in itertools.product(('LWOB','LWO2'),range(4),range(4)):
            with self.subTest(kind=kind,wrap=(ws,wt)):
                uv,m,directory=self.evaluate(projected(kind,c,points,wrap=(ws,wt)))
                image=tx.png(directory/m['derived_maps']['base_color']); h=len(image);w=len(image[0])
                mapping=m.get('derived_texture_mapping',{'source_uv_min':[0,0],'source_uv_span':[1,1]})
                low,span=mapping['source_uv_min'],mapping['source_uv_span']
                for i,p in enumerate(points):
                    for axis in range(2): self.assertAlmostEqual(uv[i][axis]*span[axis]+low[axis],p[axis]+.5,places=5)
                for u,v in itertools.product((-.25,.25,.75,1.25,2.25),repeat=2):
                    src=[u,v];reset=False
                    for a,mode in enumerate((ws,wt)):
                        if mode==0 and not 0<=src[a]<=1: reset=True
                        if mode in (0,3):src[a]=min(1,max(0,src[a]))
                        elif mode==2:
                            src[a]%=2
                            if src[a]>1:src[a]=2-src[a]
                        else:src[a]%=1
                    expected=(0,0,0,255) if reset else (*rows[min(1,int((1-src[1])*2))][min(1,int(src[0]*2))],255)
                    x=int(((u-low[0])/span[0]%1)*w); y=int((1-(v-low[1])/span[1]%1)*h)%h
                    self.assertEqual(image[y][x],expected)

    def test_wrap_atlas_bound_is_reported_without_allocation_overflow(self):
        self.write('maps/screen.iff',tx.ilbm([[(255,255,255)]]))
        c=dict(projection=0,axis=2,point=[0,0,0],center=[0,0,0],size=[1e-20,1,1],rotation=[0,0,0],tiles=[1,1])
        for kind in ('LWOB','LWO2'):
            out,m=self.convert(self.write('large.lwo',projected(kind,c,[(0,0,0),(1,0,0),(0,1,0)],wrap=(0,0))),code=2)
            _,native=self.object_data(out,m);material=native['materials'][0]
            self.assertEqual(material['derived_maps'],{})
            self.assertIn('atlas',material['textures'][0]['issue'])

    def test_rotation_mismatch_does_not_composite_unrelated_maps(self):
        self.write('maps/screen.iff',tx.ilbm([[(255,255,255)]]))
        a=tx.imap(extra=chunk('PROJ',U16(0),True))
        b=tx.imap(channel='SPEC',extra=chunk('PROJ',U16(0),True)+chunk('TMAP',chunk('ROTA',F32(.3,0,0)+vx(0),True),True))
        out,m=self.convert(self.write('mixed.lwo',tx.uv_textured(a+b)),code=2)
        _,native=self.object_data(out,m);material=native['materials'][0]
        self.assertIn('incompatible projection',material['textures'][1]['issue'])
        self.assertNotIn('specular',material['derived_maps'])

    def test_unused_planar_size_and_spherical_size_do_not_block_mapping(self):
        self.write('maps/screen.iff',tx.ilbm([[(255,255,255)]]))
        for kind,axis,projection in itertools.product(('LWOB','LWO2'),range(3),(0,2)):
            c=dict(projection=projection,axis=axis,point=[.2,.3,.4],center=[0,0,0],size=[1,1,1],rotation=[0,0,0],tiles=[1,1])
            reference,_,_=self.evaluate(projected(kind,c))
            if projection==2: c['size']=[0,0,0]
            else:c['size'][axis]=0
            actual,m,_=self.evaluate(projected(kind,c))
            self.assertEqual(m['textures'][0]['export_status'],'approximated')
            self.assertEqual(actual,reference)

    def test_world_reference_animation_and_falloff_remain_explicit(self):
        self.write('maps/screen.iff',tx.ilbm([[(255,255,255)]]))
        c=dict(projection=0,axis=2,point=[.2,.3,.4],center=[0,0,0],size=[1,1,1],rotation=[0,0,0],tiles=[1,1])
        fields=[(chunk('CSYS',U16(1),True),'world'),(chunk('OREF',s0('moving_ref'),True),'reference'),
                (chunk('CNTR',F32(0,0,0)+vx(1),True),'animated'),
                (chunk('FALL',U16(1)+F32(1,1,1)+vx(0),True),'falloff')]
        for field,issue in fields:
            out,m=self.convert(self.write('limited.lwo',projected('LWO2',c,extra=chunk('TMAP',field,True))),code=2)
            _,native=self.object_data(out,m)
            self.assertEqual(native['materials'][0]['derived_maps'],{})
            self.assertIn(issue,native['materials'][0]['textures'][0]['issue'])


if __name__=='__main__': unittest.main()
