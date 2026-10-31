"""Point Cloud Export (PCB).

Exports point clouds living in Blender to external formats:

  (1) Binary PLY  — for CloudCompare, MeshLab and other point-cloud tools.
  (2) Single HTML — a self-contained offline three.js viewer (double-click to
      open, no server / no install / no network).

Both modes read the *same* geometry: the points of Mesh vertices or of native
Point Cloud objects, plus a POINT-domain color attribute. Colors are stored by
Blender in linear space and converted to sRGB on export so other tools and the
web viewer show the original look.

This is STEP 2 ("share") of the Point Cloud Base tool roadmap.

Pure Python + numpy (bundled with Blender) — no external wheels, so it runs on
every platform and on Blender 4.2 through 5.x.
"""

import base64
import json
import os

import bpy
import numpy as np
from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ExportHelper

_ADDON_DIR = os.path.dirname(__file__)


# --------------------------------------------------------------------------- #
# Color
# --------------------------------------------------------------------------- #

def _linear_to_srgb(c):
    """Linear scene-referred float [0,1] -> sRGB float [0,1]."""
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1.0 / 2.4) - 0.055)


def _read_colors(color_attributes, n):
    """Return an (n, 3) uint8 sRGB array from a color_attributes collection.

    Prefers the active POINT-domain color attribute, else the first one. Blender
    stores colors linearly, so we encode to sRGB for display. Falls back to a
    uniform light gray so the output never opens to an all-black cloud.
    """
    attr = None
    if color_attributes and len(color_attributes):
        attr = getattr(color_attributes, "active_color", None) or color_attributes[0]

    if attr is not None and attr.domain == 'POINT' and len(attr.data) == n:
        raw = np.empty(n * 4, dtype=np.float32)
        attr.data.foreach_get("color", raw)
        rgb_lin = raw.reshape(n, 4)[:, :3]
        rgb_srgb = _linear_to_srgb(rgb_lin)
        return np.clip(rgb_srgb * 255.0 + 0.5, 0, 255).astype(np.uint8)

    rgb = np.empty((n, 3), dtype=np.uint8)
    rgb[:] = 200  # light gray fallback
    return rgb


# --------------------------------------------------------------------------- #
# Point gathering (Mesh vertices + native Point Cloud objects)
# --------------------------------------------------------------------------- #

def _object_points(obj):
    """Return (co float32 Nx3, color_attributes or None, n) for a supported object.

    Supports Mesh (vertices) and Point Cloud objects. Returns (None, None, 0)
    for anything else or empty data.
    """
    data = obj.data
    if obj.type == 'MESH':
        n = len(data.vertices)
        if n == 0:
            return None, None, 0
        co = np.empty(n * 3, dtype=np.float32)
        data.vertices.foreach_get("co", co)
        return co.reshape(n, 3), data.color_attributes, n

    if obj.type == 'POINTCLOUD':
        n = len(data.points)
        if n == 0:
            return None, None, 0
        co = np.empty(n * 3, dtype=np.float32)
        data.attributes["position"].data.foreach_get("vector", co)
        cols = getattr(data, "color_attributes", None)
        return co.reshape(n, 3), cols, n

    return None, None, 0


def _supported(obj):
    return obj.type in {'MESH', 'POINTCLOUD'}


def _collect_points(objects, apply_transform):
    """Merge points of every supported object into (xyz float32 Nx3, rgb uint8 Nx3 sRGB)."""
    xyz_parts = []
    rgb_parts = []

    for obj in objects:
        co, color_attrs, n = _object_points(obj)
        if co is None:
            continue

        if apply_transform:
            m = np.array(obj.matrix_world, dtype=np.float32)  # 4x4, row-major
            co_h = np.empty((n, 4), dtype=np.float32)
            co_h[:, :3] = co
            co_h[:, 3] = 1.0
            co = (co_h @ m.T)[:, :3]

        xyz_parts.append(np.ascontiguousarray(co, dtype=np.float32))
        rgb_parts.append(_read_colors(color_attrs, n))

    if not xyz_parts:
        return None, None
    return np.concatenate(xyz_parts), np.concatenate(rgb_parts)


# --------------------------------------------------------------------------- #
# PLY writer (binary little-endian, XYZ float32 + RGB uint8)
# --------------------------------------------------------------------------- #

def _write_ply(filepath, xyz, rgb):
    n = len(xyz)
    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        f"element vertex {n}\n"
        "property float x\n"
        "property float y\n"
        "property float z\n"
        "property uchar red\n"
        "property uchar green\n"
        "property uchar blue\n"
        "end_header\n"
    ).encode("ascii")

    dtype = np.dtype([
        ("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
        ("red", "u1"), ("green", "u1"), ("blue", "u1"),
    ])
    rec = np.empty(n, dtype=dtype)
    rec["x"], rec["y"], rec["z"] = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    rec["red"], rec["green"], rec["blue"] = rgb[:, 0], rgb[:, 1], rgb[:, 2]

    with open(filepath, "wb") as f:
        f.write(header)
        f.write(rec.tobytes())


# --------------------------------------------------------------------------- #
# HTML viewer writer (single self-contained file)
# --------------------------------------------------------------------------- #

def _read_asset(name):
    with open(os.path.join(_ADDON_DIR, "viewer", name), "r", encoding="utf-8") as f:
        return f.read()


def _quantize(xyz, rgb):
    """Pack points to 9 bytes each (3x uint16 position + 3x uint8 color).

    Returns (base64_str, meta) where meta carries the bounding box needed to
    dequantize in the viewer.
    """
    mn = xyz.min(axis=0)
    mx = xyz.max(axis=0)
    size = mx - mn
    size[size == 0.0] = 1.0  # avoid divide-by-zero on flat axes

    q = np.round((xyz - mn) / size * 65535.0).astype(np.uint16)

    dtype = np.dtype([
        ("x", "<u2"), ("y", "<u2"), ("z", "<u2"),
        ("r", "u1"), ("g", "u1"), ("b", "u1"),
    ])
    rec = np.empty(len(xyz), dtype=dtype)
    rec["x"], rec["y"], rec["z"] = q[:, 0], q[:, 1], q[:, 2]
    rec["r"], rec["g"], rec["b"] = rgb[:, 0], rgb[:, 1], rgb[:, 2]

    b64 = base64.b64encode(rec.tobytes()).decode("ascii")
    meta = {
        "count": int(len(xyz)),
        "min": [float(v) for v in mn],
        "size": [float(v) for v in size],
    }
    return b64, meta


def _write_html(filepath, xyz, rgb, point_size, title):
    b64, meta = _quantize(xyz, rgb)
    meta["pointSize"] = float(point_size)
    html = (
        _VIEWER_TEMPLATE
        .replace("/*__THREE__*/", _read_asset("three.min.js"))
        .replace("/*__ORBIT__*/", _read_asset("OrbitControls.js"))
        .replace("__TITLE__", title)
        .replace("__META_JSON__", json.dumps(meta))
        .replace("__DATA_B64__", b64)
    )
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)


_VIEWER_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  html,body{margin:0;height:100%;overflow:hidden;background:#202024;font-family:system-ui,sans-serif}
  #c{display:block;width:100%;height:100%}
  #ui{position:fixed;top:12px;left:12px;background:rgba(20,20,24,.82);color:#eee;
      padding:12px 14px;border-radius:10px;font-size:13px;line-height:1.9;user-select:none;
      box-shadow:0 2px 10px rgba(0,0,0,.4)}
  #ui b{color:#7cc4ff}
  #ui label{display:block}
  #ui input[type=range]{width:150px;vertical-align:middle}
  #ui button{background:#33343a;color:#eee;border:0;border-radius:6px;padding:4px 10px;
      margin-top:4px;margin-right:6px;cursor:pointer;font-size:12px}
  #ui button:hover{background:#44454c}
  #hint{position:fixed;bottom:10px;left:12px;color:#888;font-size:11px}
</style>
</head>
<body>
<canvas id="c"></canvas>
<div id="ui">
  <div><b>__TITLE__</b></div>
  <div><span id="npts"></span> points</div>
  <label>Point size <input id="size" type="range" min="1" max="200" value="30"></label>
  <button id="shape">Shape: round</button>
  <button id="bg">Background</button>
  <button id="reset">Reset view</button>
</div>
<div id="hint">drag: orbit &nbsp;·&nbsp; right-drag: pan &nbsp;·&nbsp; wheel: zoom</div>

<script>/*__THREE__*/</script>
<script>/*__ORBIT__*/</script>
<script>
const META = __META_JSON__;
const DATA_B64 = "__DATA_B64__";

// ---- decode packed points (9 bytes/point: 3x uint16 pos + 3x uint8 rgb) ----
function decode(){
  const bin = atob(DATA_B64);
  const n = META.count;
  const bytes = new Uint8Array(bin.length);
  for (let i=0;i<bin.length;i++) bytes[i] = bin.charCodeAt(i);
  const dv = new DataView(bytes.buffer);
  const pos = new Float32Array(n*3);
  const col = new Float32Array(n*3);
  const mn = META.min, sz = META.size;
  let o = 0;
  for (let i=0;i<n;i++){
    const qx = dv.getUint16(o,   true);
    const qy = dv.getUint16(o+2, true);
    const qz = dv.getUint16(o+4, true);
    pos[i*3]   = mn[0] + (qx/65535)*sz[0];
    pos[i*3+1] = mn[1] + (qy/65535)*sz[1];
    pos[i*3+2] = mn[2] + (qz/65535)*sz[2];
    col[i*3]   = bytes[o+6]/255;
    col[i*3+1] = bytes[o+7]/255;
    col[i*3+2] = bytes[o+8]/255;
    o += 9;
  }
  return {pos, col};
}

const {pos, col} = decode();

const renderer = new THREE.WebGLRenderer({canvas:document.getElementById('c'), antialias:true});
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setSize(window.innerWidth, window.innerHeight);

const scene = new THREE.Scene();
let bgDark = true;
scene.background = new THREE.Color(0x202024);

// Blender is Z-up.
const camera = new THREE.PerspectiveCamera(50, window.innerWidth/window.innerHeight, 0.01, 1e7);
camera.up.set(0,0,1);

const geo = new THREE.BufferGeometry();
geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
geo.setAttribute('color',    new THREE.BufferAttribute(col, 3));
geo.computeBoundingSphere();

const mat = new THREE.PointsMaterial({size: META.pointSize, vertexColors:true, sizeAttenuation:true});
const points = new THREE.Points(geo, mat);
scene.add(points);

const controls = new THREE.OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;

const bs = geo.boundingSphere;
const center = bs.center.clone();
const radius = bs.radius || 1;

function fitView(){
  controls.target.copy(center);
  const d = radius * 2.2;
  camera.position.set(center.x + d*0.7, center.y - d*0.9, center.z + d*0.6);
  camera.near = Math.max(radius/1000, 0.001);
  camera.far  = radius * 100;
  camera.updateProjectionMatrix();
  controls.update();
}
fitView();

// ---- UI ----
document.getElementById('npts').textContent = META.count.toLocaleString();
const sizeEl = document.getElementById('size');
// map slider 1..200 -> world point size around META.pointSize
const baseSize = META.pointSize;
sizeEl.value = 30;
function applySize(){ mat.size = baseSize * (sizeEl.value/30); }
sizeEl.addEventListener('input', applySize);
applySize();

document.getElementById('bg').addEventListener('click', ()=>{
  bgDark = !bgDark;
  scene.background = new THREE.Color(bgDark ? 0x202024 : 0xf0f0f2);
});
document.getElementById('reset').addEventListener('click', fitView);

// round vs square points: a white disc sprite + alphaTest cuts the corners away
function makeDisc(){
  const s = 64, cnv = document.createElement('canvas'); cnv.width = cnv.height = s;
  const g = cnv.getContext('2d');
  g.beginPath(); g.arc(s/2, s/2, s/2 - 1, 0, Math.PI*2); g.fillStyle = '#fff'; g.fill();
  const t = new THREE.CanvasTexture(cnv); t.needsUpdate = true; return t;
}
const disc = makeDisc();
let round = true;
function applyShape(){
  mat.map = round ? disc : null;
  mat.alphaTest = round ? 0.5 : 0.0;
  mat.needsUpdate = true;
  document.getElementById('shape').textContent = 'Shape: ' + (round ? 'round' : 'square');
}
document.getElementById('shape').addEventListener('click', ()=>{ round = !round; applyShape(); });
applyShape();

window.addEventListener('resize', ()=>{
  camera.aspect = window.innerWidth/window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

(function loop(){
  requestAnimationFrame(loop);
  controls.update();
  renderer.render(scene, camera);
})();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
# Operators
# --------------------------------------------------------------------------- #

def _gather(context, selection_only):
    source = context.selected_objects if selection_only else context.scene.objects
    return [o for o in source if _supported(o)]


class PCB_OT_export_point_cloud_ply(Operator, ExportHelper):
    """Export the selected point cloud(s) to a binary PLY file"""

    bl_idname = "pcb.export_point_cloud_ply"
    bl_label = "Export Point Cloud (PLY)"
    bl_options = {'REGISTER'}

    filename_ext = ".ply"
    filter_glob: StringProperty(default="*.ply", options={'HIDDEN'})

    selection_only: BoolProperty(
        name="Selection Only",
        description="Export only selected objects (otherwise every point cloud in the scene)",
        default=True,
    )
    apply_transform: BoolProperty(
        name="Apply Object Transform",
        description="Bake each object's world transform into the exported coordinates",
        default=True,
    )

    def execute(self, context):
        objs = _gather(context, self.selection_only)
        if not objs:
            self.report({'ERROR'}, "No point cloud to export (select a Mesh or Point Cloud).")
            return {'CANCELLED'}

        xyz, rgb = _collect_points(objs, self.apply_transform)
        if xyz is None:
            self.report({'ERROR'}, "Selected objects contain no points.")
            return {'CANCELLED'}

        try:
            _write_ply(self.filepath, xyz, rgb)
        except Exception as exc:  # noqa: BLE001
            self.report({'ERROR'}, f"PLY export failed: {exc}")
            return {'CANCELLED'}

        self.report({'INFO'}, f"Exported {len(xyz):,} points → {self.filepath}")
        return {'FINISHED'}


class PCB_OT_export_point_cloud_html(Operator, ExportHelper):
    """Export the selected point cloud(s) to a single self-contained HTML viewer"""

    bl_idname = "pcb.export_point_cloud_html"
    bl_label = "Export Point Cloud (HTML viewer)"
    bl_options = {'REGISTER'}

    filename_ext = ".html"
    filter_glob: StringProperty(default="*.html", options={'HIDDEN'})

    selection_only: BoolProperty(
        name="Selection Only",
        description="Export only selected objects (otherwise every point cloud in the scene)",
        default=True,
    )
    apply_transform: BoolProperty(
        name="Apply Object Transform",
        description="Bake each object's world transform into the exported coordinates",
        default=True,
    )
    point_size: FloatProperty(
        name="Point Size",
        description="Initial point size in the viewer (adjustable with the slider)",
        default=0.03, min=0.0001, max=100.0,
    )

    def execute(self, context):
        objs = _gather(context, self.selection_only)
        if not objs:
            self.report({'ERROR'}, "No point cloud to export (select a Mesh or Point Cloud).")
            return {'CANCELLED'}

        xyz, rgb = _collect_points(objs, self.apply_transform)
        if xyz is None:
            self.report({'ERROR'}, "Selected objects contain no points.")
            return {'CANCELLED'}

        # No extra thinning: export the displayed cloud as-is (density is decided
        # at import time).
        title = os.path.splitext(os.path.basename(self.filepath))[0] or "Point Cloud"
        try:
            _write_html(self.filepath, xyz, rgb, self.point_size, title)
        except Exception as exc:  # noqa: BLE001
            self.report({'ERROR'}, f"HTML export failed: {exc}")
            return {'CANCELLED'}

        self.report({'INFO'}, f"Exported {len(xyz):,} points → {self.filepath}")
        return {'FINISHED'}


# --------------------------------------------------------------------------- #
# Menu + registration
# --------------------------------------------------------------------------- #

def _menu_export(self, context):
    self.layout.operator(PCB_OT_export_point_cloud_ply.bl_idname,
                         text="Point Cloud (.ply) — PCB")
    self.layout.operator(PCB_OT_export_point_cloud_html.bl_idname,
                         text="Point Cloud Web Viewer (.html) — PCB")


_classes = (
    PCB_OT_export_point_cloud_ply,
    PCB_OT_export_point_cloud_html,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_export.append(_menu_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(_menu_export)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
