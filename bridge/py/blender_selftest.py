# blender_selftest.py — headless Blender sanity check for aigc-bridge.
# Renders a single 320x240 frame of a lit cube and reports the output path.
import bpy
import os
import sys

print("BLENDER_OK", bpy.app.version_string)

bpy.ops.wm.read_factory_settings(use_empty=True)

bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0))
cube = bpy.context.active_object

mat = bpy.data.materials.new("SelftestMat")
mat.use_nodes = True
bsdf = mat.node_tree.nodes.get("Principled BSDF")
if bsdf:
    bsdf.inputs["Base Color"].default_value = (0.9, 0.35, 0.15, 1.0)
cube.data.materials.append(mat)

bpy.ops.object.light_add(type="AREA", location=(4, -4, 6))
bpy.context.active_object.data.energy = 800

bpy.ops.object.camera_add(location=(5, -5, 4), rotation=(1.1, 0, 0.78))
bpy.context.scene.camera = bpy.context.active_object

scene = bpy.context.scene
for eng in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH"):
    try:
        scene.render.engine = eng
        print("ENGINE", eng)
        break
    except Exception as e:
        print("engine", eng, "unavailable:", e)
scene.render.resolution_x = 320
scene.render.resolution_y = 240
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"

out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "outputs", "aigc", "blender_selftest")
os.makedirs(out_dir, exist_ok=True)
out = os.path.join(out_dir, "selftest_cube.png")
scene.render.filepath = out

bpy.ops.render.render(write_still=True)
print("RENDER_WRITTEN", os.path.abspath(out))
