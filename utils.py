import math


# Measured in our default-camera setup using getpos and dota_camera_get_lookatpos:
# distance=1200, downward pitch=60 degrees, yaw=90 degrees (looking toward world +Y), with no camera roll.
CAMERA_DISTANCE = 1200.0
CAMERA_PITCH_DEGREES = 60.0

# Estimated parameters
VERTICAL_FOV_DEGREES = 66.0
GROUND_Z = 128.0


def camera_projection(camera_world_position, viewport):
    """Shared camera basis and focal length, in the viewport's pixel units."""
    left, top, width, height = viewport

    pitch = math.radians(CAMERA_PITCH_DEGREES)
    # Fixed yaw=90: screen right is world +X; screen up points +Y and +Z.
    forward = (0.0, math.cos(pitch), -math.sin(pitch))
    up = (0.0, math.sin(pitch), math.cos(pitch))
    eye = tuple(c - CAMERA_DISTANCE * f for c, f in zip(camera_world_position, forward))
    # Square pixels: one focal length for both axes. Aspect ratio is supplied by viewport width/height, rather than hard-coded to our measured 16:9.
    focal = height / (2 * math.tan(math.radians(VERTICAL_FOV_DEGREES) / 2))
    center = (left + width / 2, top + height / 2)
    return eye, forward, up, focal, center


def world_to_screen_coordinate(world_coordinate, camera_world_position, viewport):
    """Project absolute world (x, y, z) to floating-point screen (x, y).

    camera_world_position is the LOOK-AT XYZ from dota_camera_get_lookatpos,
    not the camera eye from getpos. viewport is (left, top, width, height) of
    the full 3D render, including behind the HUD, in physical screen pixels.
    Screen origin is top-left, with Y increasing downward. No rounding or
    clipping is performed. Caller supplies valid points in front of the camera.
    """
    eye, forward, up, focal, center = camera_projection(camera_world_position, viewport)
    relative = tuple(w - e for w, e in zip(world_coordinate, eye))
    depth = sum(r * f for r, f in zip(relative, forward))
    vertical = sum(r * u for r, u in zip(relative, up))
    return center[0] + focal * relative[0] / depth, center[1] - focal * vertical / depth


def screen_to_world_coordinate(screen_coordinate, camera_world_position, viewport,
                               *, ground_z=GROUND_Z):
    """Intersect a screen (x, y) ray with the flat plane z=ground_z.

    Camera/viewport conventions match world_to_screen_coordinate. Returns
    absolute world (x, y, ground_z). Off-screen coordinates are allowed.
    Override ground_z for a known flat elevation; this does not trace terrain.
    Caller supplies a ray that intersects the ground in front of the camera.
    """
    eye, forward, up, focal, center = camera_projection(camera_world_position, viewport)
    horizontal = (screen_coordinate[0] - center[0]) / focal
    vertical = (center[1] - screen_coordinate[1]) / focal
    ray = (horizontal, forward[1] + vertical * up[1], forward[2] + vertical * up[2])
    distance = (ground_z - eye[2]) / ray[2]
    return eye[0] + distance * ray[0], eye[1] + distance * ray[1], ground_z


def updated_abilities(curr_abilities, prev_abilities, filter_info):
    """Current info of the abilities whose `filter_info` field changed this tick.

    GSI's `previously.abilities` lists only the fields that changed, so a slot
    appearing there with `filter_info` in it is one that just ticked. The value
    returned is the *current* info for that slot, keyed by slot.
    """
    if not isinstance(prev_abilities, dict):    # GSI sends `false` when the block is new
        return {}
    return {
        prev_ability_slot: curr_abilities[prev_ability_slot]
        for prev_ability_slot, prev_ability_info in prev_abilities.items()
        if filter_info in prev_ability_info
    }
