"""Build the Invoker reference dataset from the local Dota 2 install.

Pipeline:  pak01_dir.vpk --(Source 2 Viewer CLI)--> glTF in a temp dir --(bpy)--> dataset/*.png

Run:  uv run prepare_dataset.py
"""
import json
import math
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

HERE = Path(__file__).resolve().parent
TOOLS = HERE / "tools"
DATASET = HERE / "dataset"

DOTA = Path(r"C:\Program Files (x86)\Steam\steamapps\common\dota 2 beta\game\dota")
VPK = DOTA / "pak01_dir.vpk"
GAMEINFO = DOTA / "gameinfo.gi"

CLI_URL = "https://github.com/ValveResourceFormat/ValveResourceFormat/releases/download/20.0/cli-windows-x64.zip"
CLI_EXE = TOOLS / "s2v-cli" / "Source2Viewer-CLI.exe"

MODEL_DIR = "models/heroes/invoker"
BODY = "invoker"
PARTS = ["invoker_bracer", "invoker_cape", "invoker_dress", "invoker_hair", "invoker_head", "invoker_shoulder"]

# Rendering parameters. Dota's camera pitch is 60 deg and its yaw is fixed, so sweeping the
# camera azimuth is equivalent to sweeping the hero's facing direction on screen.
POSES = {"idle": [0, 12, 24, 36], "run": [0, 4, 8, 12, 16], "attack": [0, 6, 12, 18], "cast": [0, 8]}
AZIMUTHS = 24
ELEVATIONS = [55, 60, 65]
RES = 640          # square frame size before cropping
PAD = 1.05         # ortho framing padding
SAMPLES = 16
WORLD_LIGHT = 1.0   # ambient strength
SUN_LIGHT = 3.0     # key light strength


def ensure_cli() -> Path:
    if CLI_EXE.exists():
        return CLI_EXE
    zip_path = TOOLS / "cli-windows-x64.zip"
    TOOLS.mkdir(exist_ok=True)
    print(f"downloading {CLI_URL}")
    urllib.request.urlretrieve(CLI_URL, zip_path)
    zipfile.ZipFile(zip_path).extractall(CLI_EXE.parent)
    zip_path.unlink()
    return CLI_EXE


def extract_gltf(cli: Path, name: str, out: Path) -> Path:
    """Decompile one .vmdl_c from the VPK into glTF. Animations must be exported for the
    cosmetic parts too, otherwise VRF writes them as un-skinned static meshes."""
    subprocess.run(
        [str(cli), "-i", str(VPK), "-f", f"{MODEL_DIR}/{name}.vmdl_c", "-o", str(out), "-d",
         "--game", str(GAMEINFO), "--gltf_export_format", "gltf", "--gltf_export_animations",
         "--gltf_export_materials", "--gltf_textures_adapt"],
        check=True, stdout=subprocess.DEVNULL)
    gltf = out / MODEL_DIR / f"{name}.gltf"
    assert gltf.exists(), gltf
    return gltf


def load_hero(body: Path, parts: list[Path]) -> bpy.types.Object:
    """Import body + parts and return the single armature that drives everything."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(body))
    arm = next(ob for ob in bpy.data.objects if ob.type == "ARMATURE")
    bone_by_lower = {b.name.lower(): b.name for b in arm.data.bones}

    for part in parts:
        before = set(bpy.data.objects)
        bpy.ops.import_scene.gltf(filepath=str(part))
        new = [ob for ob in bpy.data.objects if ob not in before]
        for ob in new:
            if ob.type != "MESH":
                continue
            # Parts ship a subset skeleton with different bone-name casing (Source is
            # case-insensitive). Rename vertex groups to the body's bones and re-target.
            for vg in ob.vertex_groups:
                vg.name = bone_by_lower[vg.name.lower()]
            for mod in ob.modifiers:
                if mod.type == "ARMATURE":
                    mod.object = arm
            mw = ob.matrix_world.copy()
            ob.parent = arm
            ob.matrix_world = mw
        for ob in new:
            if ob.type == "ARMATURE":
                bpy.data.objects.remove(ob, do_unlink=True)

    for ob in bpy.data.objects:  # orb placeholder spheres
        if ob.name.startswith("Icosphere"):
            ob.hide_render = True
    return arm


def setup_render() -> bpy.types.Object:
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.film_transparent = True
    scene.render.resolution_x = scene.render.resolution_y = RES
    scene.render.image_settings.color_mode = "RGBA"
    scene.view_settings.view_transform = "Standard"
    scene.eevee.taa_render_samples = SAMPLES

    # Lighting approximates Dota's flat, bright, warm look: strong ambient plus a shadowless
    # key light from the upper-left of the *screen*. The key is parented to the camera so the
    # light direction stays fixed on screen while the camera orbits (in-game the light and
    # camera are both fixed and only the hero turns).
    world = bpy.data.worlds.new("World")
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs[0].default_value = (1.0, 0.98, 0.92, 1)
    bg.inputs[1].default_value = WORLD_LIGHT
    scene.world = world

    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    cam.data.type = "ORTHO"
    cam.data.clip_end = 10000
    scene.collection.objects.link(cam)
    scene.camera = cam

    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
    sun.data.energy = SUN_LIGHT
    sun.data.color = (1.0, 0.95, 0.85)
    sun.data.use_shadow = False
    sun.parent = cam
    sun.rotation_euler = (math.radians(-25), math.radians(-30), 0)  # from camera's upper-left
    scene.collection.objects.link(sun)
    return cam


def set_pose(arm, action: str, frame: int):
    act = bpy.data.actions[action]
    if arm.animation_data is None:
        arm.animation_data_create()
    arm.animation_data.action = act
    arm.animation_data.action_slot = act.slots[0]
    bpy.context.scene.frame_set(frame)


def mesh_bounds():
    deps = bpy.context.evaluated_depsgraph_get()
    pts = []
    for ob in bpy.context.scene.objects:
        if ob.type == "MESH" and not ob.hide_render:
            ev = ob.evaluated_get(deps)
            pts += [ev.matrix_world @ v.co for v in ev.to_mesh().vertices]
            ev.to_mesh_clear()
    arr = np.array(pts)
    return Vector(arr.min(0)), Vector(arr.max(0))


def aim_camera(cam, center, azimuth, elevation, dist=100.0):
    az, el = math.radians(azimuth), math.radians(elevation)
    d = Vector((math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)))
    cam.location = center + d * dist
    cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()


def render_cropped(path: Path) -> list[int]:
    """Render to path, then tight-crop to the alpha bbox in place. Returns [x, y, w, h]."""
    bpy.context.scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(str(path))
    px = np.array(img.pixels[:], dtype=np.float32).reshape(RES, RES, 4)[::-1]
    bpy.data.images.remove(img)
    ys, xs = np.where(px[..., 3] > 0.002)
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    out = bpy.data.images.new("crop", width=int(x1 - x0), height=int(y1 - y0), alpha=True)
    out.pixels = px[y0:y1, x0:x1][::-1].ravel().tolist()
    out.filepath_raw = str(path)
    out.file_format = "PNG"
    out.save()
    bpy.data.images.remove(out)
    return [int(x0), int(y0), int(x1 - x0), int(y1 - y0)]


def render_dataset(arm, out: Path):
    out.mkdir(exist_ok=True)
    cam = setup_render()

    # One ortho scale for every pose so pixel size is constant across the dataset.
    lo, hi = Vector((1e9,) * 3), Vector((-1e9,) * 3)
    for action, frames in POSES.items():
        for f in frames:
            set_pose(arm, action, f)
            l, h = mesh_bounds()
            lo, hi = Vector(map(min, lo, l)), Vector(map(max, hi, h))
    center = (lo + hi) / 2
    cam.data.ortho_scale = (hi - lo).length * PAD

    manifest = {
        "resolution": RES,
        "world_units_per_pixel": cam.data.ortho_scale / RES,
        "azimuth": "camera angle around hero, degrees CCW from model +X (== hero facing on screen)",
        "elevation": "camera angle above horizontal, degrees",
        "crop_xywh": "crop offset inside the original RES x RES frame, top-left origin",
        "images": [],
    }
    for action, frames in POSES.items():
        for f in frames:
            set_pose(arm, action, f)
            for el in ELEVATIONS:
                for i in range(AZIMUTHS):
                    az = i * 360 // AZIMUTHS
                    aim_camera(cam, center, az, el)
                    name = f"{action}_f{f:03d}_el{el:02d}_az{az:03d}.png"
                    crop = render_cropped(out / name)
                    manifest["images"].append(
                        {"file": name, "anim": action, "frame": f, "elevation": el, "azimuth": az, "crop_xywh": crop})
                    print(name, crop)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"wrote {len(manifest['images'])} images to {out}")


def main():
    assert VPK.exists(), f"Dota 2 not found at {VPK}"
    cli = ensure_cli()
    with tempfile.TemporaryDirectory(prefix="invoker_") as tmp:
        tmp = Path(tmp)
        print(f"intermediate files in {tmp}")
        body = extract_gltf(cli, BODY, tmp)
        parts = [extract_gltf(cli, p, tmp) for p in PARTS]
        arm = load_hero(body, parts)
        if DATASET.exists():
            shutil.rmtree(DATASET)
        render_dataset(arm, DATASET)


if __name__ == "__main__":
    main()
