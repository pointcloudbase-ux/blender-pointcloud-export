# Point Cloud Export (PCB)

Export point clouds that live in Blender to share them with others. Two modes:

1. **PLY** — binary `.ply` that CloudCompare, MeshLab and most point-cloud tools
   read directly.
2. **HTML viewer** — a single, self-contained `.html` you double-click to view
   the cloud in 3D. No server, no install, no network (three.js is embedded).

Part of the [Point Cloud Base](https://pointcloudbase.com/tools) tool roadmap —
STEP 2 ("share").

## What it reads

- **Mesh vertices** *and* **native Point Cloud objects** (e.g. clouds imported
  by the PCB LAS Importer, which are Point Cloud objects).
- The active **POINT-domain color attribute** as RGB. Blender stores colors
  linearly; both exporters convert to **sRGB** so other tools and the viewer
  show the original look.
- If an object has no color attribute, points fall back to a uniform light gray
  so nothing opens all-black.
- **Apply Object Transform** bakes each object's world transform into the
  exported coordinates; multiple selected objects merge into one output.

Pure Python + numpy (bundled with Blender). **No external wheels**, so it runs
on every platform and on Blender 4.2 through 5.x.

## Install (development)

1. Blender → **Edit → Preferences → Add-ons → Install from Disk…**
   (or drag the built `.zip` into Blender).
2. Enable **Point Cloud Export (PCB)**.

## Use

Select the point cloud object(s), then:

- **File → Export → Point Cloud (.ply) — PCB**
- **File → Export → Point Cloud Web Viewer (.html) — PCB**

### PLY options
- **Selection Only** — export selected objects only (default on).
- **Apply Object Transform** — bake world transform into coordinates (default on).

### HTML viewer options
- **Selection Only**, **Apply Object Transform** — as above.
- **Point Size** — initial point size in the viewer (adjustable with a slider).

The HTML export does **not** thin the cloud further — it writes the displayed
point cloud as-is (density is decided at import time). Keep clouds to a few
million points for a comfortably shareable file.

The viewer supports orbit / pan / zoom, a point-size slider, a light/dark
background toggle and a reset-view button, and auto-fits the cloud on open.
Points are quantized to 9 bytes each (16-bit position + 8-bit RGB) and embedded
as base64, so a few million points stay in a single shareable file.

## Build the extension zip

```
blender --command extension build
```

Produces `pcb_pointcloud_export-<version>.zip` (git-ignored).

## Roadmap / known limits

- No subsampling at export — both modes write the displayed cloud at full size.
  Control density at import time instead.
- Color fallback is a flat gray; height-gradient / intensity fallback planned.
- LAS/LAZ export is a later target (needs the importer's coordinate offset to
  round-trip georeferencing).
- Viewer: Draco compression and LOD/octree for 10M+ points are future steps.

## Credits

Bundles [three.js](https://threejs.org/) r128 (MIT) for the HTML viewer.
