"""Blender background diagnostic render of the exported scene; no asset edits.

blender --background --factory-startup --disable-autoexec --python-exit-code 1
  --python render_dora_clipmap.py -- PROJECT OUTPUT.png
"""
from pathlib import Path
import sys

import bpy
from mathutils import Vector

project, output = map(Path, sys.argv[sys.argv.index('--')+1:])
bpy.ops.wm.read_factory_settings(use_empty=True)
assert bpy.ops.import_scene.gltf(filepath=str(project/'gltf/dora&picasso.lws.gltf')) == {'FINISHED'}
scene = bpy.context.scene
# Original camera position, converted glTF Y-up -> Blender Z-up.
bpy.ops.object.camera_add(location=(0,-25.60001,4.75))
camera = bpy.context.object
camera.rotation_euler = (Vector((0,0,4.75))-camera.location).to_track_quat('-Z','Y').to_euler()
camera.data.type='ORTHO'; camera.data.ortho_scale=12.5; scene.camera=camera
# Diagnostic lighting, deliberately not an emulation of LW's spot/shadow maps.
bpy.ops.object.light_add(type='AREA',location=(-3,-10,14))
lamp = bpy.context.object; lamp.data.energy=2600; lamp.data.size=8
lamp.rotation_euler=(Vector((0,0,4))-lamp.location).to_track_quat('-Z','Y').to_euler()
scene.world=bpy.data.worlds.new('Diagnostic'); scene.world.use_nodes=True
scene.world.node_tree.nodes['Background'].inputs[0].default_value=(.15,.15,.15,1)
scene.render.engine='CYCLES'; scene.cycles.samples=32
scene.view_settings.view_transform='Standard'
scene.render.resolution_x=1100; scene.render.resolution_y=850; scene.render.resolution_percentage=100
scene.render.filepath=str(output.resolve())
bpy.ops.render.render(write_still=True)
