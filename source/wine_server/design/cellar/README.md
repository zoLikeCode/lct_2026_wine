# Collection alcove

Original procedural scene created for the wine collection on 2026-10-18.
No purchased meshes, downloaded textures, or generated wine labels are used.

## Editable sources

- `alcove-desktop.blend`: wide display, camera, geometry, lights and material nodes.
- `alcove-mobile.blend`: separate narrow display with a mobile camera composition.
- `create_scene.py`: reproducible scene construction and Cycles rendering.

Created and rendered with Blender 5.2.2 LTS. Materials: honed warm travertine,
vertical oak flutes, champagne bronze lip, ivory plaster. Rounded geometry,
window light, concealed strip and sun produce the depth and baked shadows.

## Render again

From the repository root, with Blender on PATH:

```sh
blender --background --factory-startup --python wine_server/design/cellar/create_scene.py -- --output /tmp/wine-alcove --quality final --mode desktop
blender --background --factory-startup --python wine_server/design/cellar/create_scene.py -- --output /tmp/wine-alcove --quality final --mode mobile
```

On macOS the executable may be `/Applications/Blender.app/Contents/MacOS/Blender`.
The script writes both PNG and `.blend` into the chosen directory. Final renders
use 96 Cycles samples with denoising: desktop 1800×850, mobile 800×800.
`--quality preview` uses 24 samples and 60% resolution. CPU is the default;
`--device GPU` attempts Metal on supported Macs.

The checked-in WebP plates were converted with Pillow (quality 90, method 6):

```python
from pathlib import Path
from PIL import Image
source = Path('/tmp/wine-alcove')
target = Path('wine_server/static/mobile/assets/cellar')
target.mkdir(parents=True, exist_ok=True)
for mode in ('desktop', 'mobile'):
    Image.open(source / f'alcove-{mode}.png').save(
        target / f'alcove-{mode}.webp', quality=90, method=6)
```

## Browser composition

This is a Blender-rendered background, not a realtime WebGL scene. Authentic
catalogue bottle cutouts are independent HTML elements above it, with contact
shadows. `cabinet-render.css` aligns the bottle feet to the rendered plinth and
switches the background at 700 CSS px. The two WebP files total 67,132 bytes.
Collection covers reuse the same plates. No Blender runtime is needed to serve
the app. `cabinet-drag.js` retains mouse, touch and keyboard ordering.

The desktop contact baseline is 80.71% of plate height, mobile 82.70%.
If the camera or plinth changes, use the script's `SHELF_CONTACT` output to
update the CSS `--plate-height` ratios and the cover padding.

Keep actual bottle photographs and labels intact. A render of an invented bottle
must not replace catalogue imagery. The source scene contains no bottle assets.

## Current transparent plates · 19.10.2026

The current UI uses `original-desktop.webp` and `original-mobile.webp` (99,016
bytes combined), rendered from these same saved models. Only the external wall
and floor are hidden; the stone frame, oak back, lighting and plinth are retained.
The `.blend` files are not modified. Render the transparent RGBA plates with:

```sh
blender --background --python wine_server/design/cellar/render_transparent.py -- --output /tmp/wine-alcove --device METAL
```

Omit `--device METAL` for CPU rendering. Convert `original-*.png` to WebP with
Pillow, `quality=92, method=6`, preserving alpha. `cabinet-render.css` repeats the
model vertically: five bottles per row on desktop, four on tablet, three on
mobile. Contact ratios remain 80.71% and 82.70%. Wine images are not edited;
their transparent bottom margins are compensated in CSS at display time.
