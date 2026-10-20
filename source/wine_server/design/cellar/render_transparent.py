"""Render the saved, approved alcove models without the surrounding room."""
import argparse
import sys
from pathlib import Path

import bpy

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--device', choices=('CPU', 'METAL'), default='CPU')
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
args.output.mkdir(parents=True, exist_ok=True)

for mode in ('desktop', 'mobile'):
    bpy.ops.wm.open_mainfile(filepath=str(Path(__file__).parent / f'alcove-{mode}.blend'))
    scene = bpy.context.scene
    for name in ('Wall behind alcove', 'Gallery floor'):
        obj = bpy.data.objects.get(name)
        if obj:
            obj.hide_render = True
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA'
    scene.render.resolution_percentage = 100
    scene.cycles.samples = 48
    scene.cycles.use_denoising = True
    scene.cycles.device = 'CPU'
    if args.device == 'METAL':
        prefs = bpy.context.preferences.addons['cycles'].preferences
        prefs.compute_device_type = 'METAL'
        prefs.get_devices()
        for device in prefs.devices:
            device.use = device.type == 'METAL'
        scene.cycles.device = 'GPU'
    scene.render.filepath = str(args.output / f'original-{mode}.png')
    bpy.ops.render.render(write_still=True)
