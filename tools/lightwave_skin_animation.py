"""Keep C-derived glTF skin weights and animate joints with native evaluated poses."""
import copy
import json
import math
from pathlib import Path
import struct
from urllib.parse import quote,unquote
from lightwave_animation import append_accessor,decompose,inverse,multiply,reflected,transform,validate_mesh,write_json


def accessor(data,buffer,index):
    a=data['accessors'][index]; v=data['bufferViews'][a['bufferView']]
    count={'VEC3':3,'VEC4':4,'MAT4':16}[a['type']]
    fmt='<'+{5123:'H',5126:'f'}[a['componentType']]*count
    start=v.get('byteOffset',0)+a.get('byteOffset',0);stride=v.get('byteStride',struct.calcsize(fmt))
    return [struct.unpack_from(fmt,buffer,start+i*stride) for i in range(a['count'])]


def export_skinned_rig(package,manifest,rig,frames,provenance):
    source=package/rig['gltf'];data=json.loads(source.read_text('utf-8'))
    if len(data.get('skins',[]))!=1: raise ValueError('Skeletal animation requires an actual bound skin')
    buffer=bytearray(source.parent.joinpath(unquote(data['buffers'][0]['uri'])).read_bytes())
    mesh_nodes=[(i,n) for i,n in enumerate(data['nodes']) if 'mesh' in n]
    if len(mesh_nodes)!=1: raise ValueError('Expected one mesh per rig')
    mesh_index,mesh_node=mesh_nodes[0];mesh=data['meshes'][mesh_node['mesh']];owner=rig['owner_item']
    asset=next(a for a in manifest['assets'] if a['id']==mesh['extras']['source_sha256'])
    native_path=package/asset['uri'];native=json.loads(native_path.read_text('utf-8'))
    geometry=native_path.parent.joinpath(native['buffer']['uri']).read_bytes()
    references=[validate_mesh(f['meshes'][owner],native,geometry,mesh['extras']['source_layer_request']) for f in frames]
    times=[(f['time']-frames[0]['time'],) for f in frames]
    if len(times)<2 or any(a[0]>=b[0] for a,b in zip(times,times[1:])): raise ValueError('Animation needs increasing times')
    time_accessor=append_accessor(data,buffer,times,'SCALAR',True)
    animation={'name':Path(manifest['input']).stem,'samplers':[],'channels':[],
               'extras':{'profile':'lightwave-evaluated-skin-0.1','capture_sha256':provenance,'source_first_frame':frames[0]['frame'],'source_last_frame':frames[-1]['frame'],'source_start_seconds':frames[0]['time']}}
    # Skin matrices already include the animated object's world transform.
    # A root mesh node also avoids ignored-parent warnings in glTF readers.
    data['scenes'][data['scene']]['nodes']=[0,mesh_index]
    mesh_node.pop('matrix',None)
    ids={i:n['extras']['source_node_id'] for i,n in enumerate(data['nodes']) if i!=mesh_index}
    parents={child:i for i,n in enumerate(data['nodes']) for child in n.get('children',[])}
    animated_bones=0
    for index,item in ids.items():
        parent=ids.get(parents.get(index));poses=[]
        for frame in frames:
            found=frame['items'][item];matrix=found['matrix']
            if parent is not None:
                if found['parent']!=parent: raise ValueError('Native parent differs from rest rig')
                matrix=multiply(inverse(frame['items'][parent]['matrix']),matrix)
            poses.append(decompose(reflected(matrix)))
        node=data['nodes'][index];node.pop('matrix',None);node.update(copy.deepcopy(poses[0]));changed=False
        for path,kind in [('translation','VEC3'),('rotation','VEC4'),('scale','VEC3')]:
            rows=[p[path][:] for p in poses]
            if path=='rotation':
                for i in range(1,len(rows)):
                    if sum(a*b for a,b in zip(rows[i-1],rows[i]))<0: rows[i]=[-v for v in rows[i]]
            if any(max(abs(a-b) for a,b in zip(row,rows[0]))>1e-8 for row in rows[1:]):
                animation['channels'].append({'sampler':len(animation['samplers']),'target':{'node':index,'path':path}})
                animation['samplers'].append({'input':time_accessor,'output':append_accessor(data,buffer,rows,kind),'interpolation':'LINEAR'})
                changed=True
        animated_bones+=bool(changed and index)
    binds=accessor(data,buffer,data['skins'][0]['inverseBindMatrices'])
    rows=[]
    for primitive in mesh['primitives']:
        attrs=primitive['attributes'];positions=accessor(data,buffer,attrs['POSITION'])
        count=sum(k.startswith('JOINTS_') for k in attrs)
        joints=[accessor(data,buffer,attrs[f'JOINTS_{i}']) for i in range(count)]
        weights=[accessor(data,buffer,attrs[f'WEIGHTS_{i}']) for i in range(count)]
        mapping=primitive['extras']['source_map']
        for i,p in enumerate(positions):
            point=struct.unpack_from('<3I',buffer,mapping['byteOffset']+12*i)[2]
            rows.append((point,p,[(j,w) for s in range(count) for j,w in zip(joints[s][i],weights[s][i]) if w]))
    errors=[];displacement=0
    for frame,reference in zip(frames,references):
        matrices=[multiply(reflected(frame['items'][ids[node]]['matrix']),b) for node,b in zip(data['skins'][0]['joints'],binds)]
        for point,p,weights in rows:
            actual=[0.,0.,0.]
            for joint,weight in weights:
                q=transform(matrices[joint],p)
                for k in range(3): actual[k]+=weight*q[k]
            expected=reference[point];expected=[expected[0],expected[1],-expected[2]]
            errors.append(math.dist(actual,expected))
            displacement=max(displacement,math.dist(reference[point],references[0][point]))
    comparison={'samples':len(errors),'maximum_vertex_error':max(errors,default=0),'rms_vertex_error':math.sqrt(sum(e*e for e in errors)/max(1,len(errors)))}
    if not animation['channels']: raise ValueError('No changing joint or object transform in the requested interval')
    data['animations']=[animation]
    data['extras'].update(profile='lightwave-evaluated-skin-0.1',pose='first evaluated animation frame',animation='sampled native joint TRS; mesh driven by preserved JOINTS/WEIGHTS and inverse binds',capture_sha256=provenance,native_deformation_comparison=comparison,skin_limitations='fixed C-derived weights; source morph/deformation plugins and volume corrections are not reproduced by the skeletal tracks')
    name=source.name.replace(f'.rig-{owner:08x}',f'.anim-{owner:08x}')
    destination=source.with_name(name);binary=destination.with_suffix('.bin')
    if destination.exists() or binary.exists(): raise FileExistsError(destination)
    data['buffers']=[{'uri':quote(binary.name,safe='-._~'),'byteLength':len(buffer)}]
    binary.write_bytes(buffer);write_json(destination,data)
    return {'owner_item':owner,'gltf':destination.relative_to(package).as_posix(),'gltf_bin':binary.relative_to(package).as_posix(),
            'profile':'lightwave-evaluated-skin-0.1','samples':len(frames),'first_frame':frames[0]['frame'],'last_frame':frames[-1]['frame'],
            'duration_seconds':times[-1][0],'animated_bones':animated_bones,'morph_targets':0,'source_points':len(references[0]),
            'maximum_world_vertex_displacement':displacement,'native_deformation_comparison':comparison,'capture_sha256':provenance}
