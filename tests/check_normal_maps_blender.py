"""Compare converted glTF tangent normals with source object normals in Blender.

Independent renderer check; does not invoke the C baker or a LightWave runtime.
It measures visible interior pixels, excluding silhouette/other-material edges.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def worker(args):
    import bpy
    import numpy as np
    from mathutils import Vector
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(args.gltf))
    material=bpy.data.materials[args.material]
    nodes,links=material.node_tree.nodes,material.node_tree.links
    target=next(n for n in nodes if n.type=='NORMAL_MAP')
    assert target.space=='TANGENT' and target.inputs['Color'].is_linked
    target_image=target.inputs['Color'].links[0].from_node.image
    assert target_image.colorspace_settings.name=='Non-Color'
    image=nodes.new('ShaderNodeTexImage');image.image=bpy.data.images.load(str(args.source_image))
    image.image.colorspace_settings.name='Non-Color';image.interpolation='Linear';image.extension='REPEAT'
    separate=nodes.new('ShaderNodeSeparateXYZ');links.new(image.outputs['Color'],separate.inputs[0])
    combine=nodes.new('ShaderNodeCombineXYZ')
    # Source LW (X,Y,Z) -> glTF (X,Y,-Z) -> Blender (X,Z,Y).
    # Both source and target nodes below return normals in the same world space.
    for k,axis in enumerate((0,2,1)):links.new(separate.outputs[axis],combine.inputs[k])
    reference=nodes.new('ShaderNodeNormalMap');reference.space='OBJECT';links.new(combine.outputs[0],reference.inputs['Color'])
    bias=nodes.new('ShaderNodeVectorMath');bias.operation='MULTIPLY_ADD';bias.inputs[1].default_value=(.5,.5,.5);bias.inputs[2].default_value=(.5,.5,.5)
    emission=nodes.new('ShaderNodeEmission');links.new(bias.outputs[0],emission.inputs['Color'])
    output=next(n for n in nodes if n.type=='OUTPUT_MATERIAL');links.new(emission.outputs[0],output.inputs['Surface'])
    for m in bpy.data.materials:
        if m==material:continue
        m.use_nodes=True;other=m.node_tree.nodes;other.clear()
        holdout=other.new('ShaderNodeHoldout');end=other.new('ShaderNodeOutputMaterial');m.node_tree.links.new(holdout.outputs[0],end.inputs['Surface'])
    scene=bpy.context.scene
    points=[o.matrix_world@Vector(c) for o in scene.objects if o.type=='MESH' for c in o.bound_box]
    center=Vector(tuple((min(p[i] for p in points)+max(p[i] for p in points))/2 for i in range(3)))
    extent=Vector(tuple(max(p[i] for p in points)-min(p[i] for p in points) for i in range(3))).length
    bpy.ops.object.camera_add(location=center+Vector((1,-2,1))*extent)
    camera=bpy.context.object;camera.rotation_euler=(center-camera.location).to_track_quat('-Z','Y').to_euler()
    camera.data.type='ORTHO';camera.data.ortho_scale=extent*1.15;scene.camera=camera
    scene.render.engine='CYCLES';scene.cycles.samples=1;scene.cycles.use_denoising=False;scene.cycles.seed=1
    scene.render.resolution_x=768;scene.render.resolution_y=768;scene.render.resolution_percentage=100
    scene.render.film_transparent=True;scene.render.image_settings.file_format='OPEN_EXR';scene.render.image_settings.color_depth='32'
    arrays=[]
    with tempfile.TemporaryDirectory(prefix='lw-normal-render-') as folder:
        for name,node in [('source-object',reference),('converted-tangent',target)]:
            links.new(node.outputs['Normal'],bias.inputs[0]);scene.render.filepath=str(Path(folder)/(name+'.exr'))
            bpy.ops.render.render(write_still=True)
            result=bpy.data.images.load(scene.render.filepath)
            values=np.empty(len(result.pixels),dtype=np.float32);result.pixels.foreach_get(values)
            arrays.append(values.reshape(768,768,4));bpy.data.images.remove(result)
    a,b=arrays
    mask=(a[:,:,3]>.999)&(b[:,:,3]>.999)
    for _ in range(3):mask[1:-1,1:-1]&=mask[:-2,1:-1]&mask[2:,1:-1]&mask[1:-1,:-2]&mask[1:-1,2:]
    va,vb=a[mask,:3]*2-1,b[mask,:3]*2-1
    la,lb=np.linalg.norm(va,axis=1),np.linalg.norm(vb,axis=1)
    valid=(la>.9)&(lb>.9);va=va[valid]/la[valid,None];vb=vb[valid]/lb[valid,None]
    errors=np.degrees(np.arccos(np.clip((va*vb).sum(axis=1),-1,1)))
    report={'blender':bpy.app.version_string,'gltf':str(args.gltf),'source_image':str(args.source_image),
            'material':args.material,'normal_map_node_connected':True,'source_space':'object (LW RGB XYZ; Blender XZY)',
            'target_space':'tangent (Blender glTF import / MikkTSpace)', 'normal_texture_colorspace':target_image.colorspace_settings.name,
            'visible_interior_pixels':int(errors.size),'mean_degrees':float(errors.mean()),'median_degrees':float(np.median(errors)),
            'p95_degrees':float(np.percentile(errors,95)),'p99_degrees':float(np.percentile(errors,99)),'max_degrees':float(errors.max()),
            'scope':'One camera, visible material interior, linear-filtered images; includes raster/interpolation error. Not a LightWave shader fidelity assertion.'}
    report['acceptance']={'median_degrees_below':3,'mean_degrees_below':5,'p95_degrees_below':10,'minimum_pixels':1000}
    report['passed']=errors.size>1000 and report['median_degrees']<3 and report['mean_degrees']<5 and report['p95_degrees']<10
    args.report.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    assert report['passed'],report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blender',type=Path)
    parser.add_argument('--gltf',type=Path,required=True)
    parser.add_argument('--source-image',type=Path,required=True)
    parser.add_argument('--material',default='aircon_target')
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--worker',action='store_true')
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else None)
    args.gltf=args.gltf.resolve();args.source_image=args.source_image.resolve();args.report=args.report.resolve()
    if args.worker:worker(args)
    else:
        if not args.blender:parser.error('--blender is required')
        command=[str(args.blender),'--background','--factory-startup','--python-exit-code','1','--python',str(Path(__file__).resolve()),'--',
                 '--worker','--gltf',str(args.gltf),'--source-image',str(args.source_image),'--material',args.material,'--report',str(args.report)]
        result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120)
        if result.returncode:print(result.stdout+result.stderr,file=sys.stderr)
        elif args.report.exists():print(args.report.read_text())
        return result.returncode


if __name__=='__main__':sys.exit(main())
