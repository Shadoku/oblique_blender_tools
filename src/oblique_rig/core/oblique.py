# SPDX-License-Identifier: GPL-3.0-or-later
"""Maths and names for the oblique (2:1 pixel-art) projection rig.

The rig shears the scene so that a straight-on orthographic camera looking down
+Y shows depth as a diagonal on screen::

    X' = X + a * Y
    Y' = Y
    Z' = Z + b * Y

with ``(a, b) = depth_scale * normalize(slope_run, slope_rise)``. Y is left
untouched, so depth sorting and occlusion stay correct.

Matrix layout: Geometry Nodes' ``FunctionNodeCombineMatrix`` has 16 float
inputs in column-major order. With 0-based ``column`` and ``row`` the input
index is ``column * 4 + row``; Blender labels the sockets 1-based
(``Column C Row R``). The shear puts ``a`` in socket "Column 2 Row 1"
(index 4) and ``b`` in "Column 2 Row 3" (index 6); everything else is the
identity.

Pixel-exact mode (0.2) describes depth as whole pixels instead: one unit of
depth moves ``depth_px_right`` pixels right and ``depth_px_up`` pixels up, so
``(a, b) = (depth_px_right, depth_px_up) / pixels_per_unit`` and every back
edge lands on the pixel grid. :class:`RigParams` bundles all settings as plain
values so Blender-facing code never passes a PropertyGroup into the maths.

Pixel coordinates (:func:`project_to_pixels`) have their origin at the
bottom-left corner of the frame, x to the right and y up, matching Blender's
image rows (row 0 at the bottom). Pixel ``(i, j)`` covers ``[i, i+1) x [j, j+1)``.

Pure Python: no bpy, no mathutils.
"""

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

SOURCE_COLLECTION = "Oblique_Source"
RIG_NAME = "Oblique Rig"
NODEGROUP_NAME = "ObliqueShear"
CAMERA_NAME = "Oblique Camera"
SUN_NAME = "Oblique Sun"
MODIFIER_NAME = "Oblique"

CALIB_FRONT = "Calib Cube Front"
CALIB_BACK = "Calib Cube Back"
CALIB_FLOOR = "Calib Floor"

CAMERA_DISTANCE = 100.0
"""How far in front of the origin (along -Y) the camera sits."""


def shear_coefficients(slope_run: float, slope_rise: float, depth_scale: float) -> tuple[float, float]:
    """Return ``(a, b) = depth_scale * normalize(slope_run, slope_rise)``.

    ``a`` is how far X moves per unit of depth (Y), ``b`` how far Z moves.

    Raises:
        ValueError: if run and rise are both 0 (no direction) or depth_scale < 0.
    """
    if depth_scale < 0:
        raise ValueError(f"depth_scale must be >= 0, got {depth_scale}")
    length = math.hypot(slope_run, slope_rise)
    if length == 0:
        raise ValueError("slope_run and slope_rise cannot both be 0")
    return depth_scale * slope_run / length, depth_scale * slope_rise / length


def shear_matrix_columns(a: float, b: float) -> list[float]:
    """Return the 16 inputs of ``FunctionNodeCombineMatrix`` for the shear.

    Column-major (index = column * 4 + row, 0-based): the identity with
    index 4 (socket "Column 2 Row 1") = ``a`` and index 6 ("Column 2 Row 3")
    = ``b``, i.e. ``X' = X + a*Y`` and ``Z' = Z + b*Y``.
    """
    values = [1.0 if column == row else 0.0 for column in range(4) for row in range(4)]
    values[1 * 4 + 0] = a
    values[1 * 4 + 2] = b
    return values


def shear_point(point: tuple[float, float, float], a: float, b: float) -> tuple[float, float, float]:
    """Apply the shear to one point: ``(x + a*y, y, z + b*y)``."""
    x, y, z = point
    return x + a * y, y, z + b * y


def ortho_scale(resolution_x: int, resolution_y: int, pixels_per_unit: int) -> float:
    """Orthographic camera scale so one Blender unit spans ``pixels_per_unit`` pixels.

    Blender's ``ortho_scale`` covers the larger side of the frame, so this is
    ``max(resolution_x, resolution_y) / pixels_per_unit``.

    Raises:
        ValueError: if any argument is not positive.
    """
    if resolution_x <= 0 or resolution_y <= 0 or pixels_per_unit <= 0:
        raise ValueError(
            "resolution and pixels_per_unit must be positive, got "
            f"{resolution_x}x{resolution_y} at {pixels_per_unit} px/unit"
        )
    return max(resolution_x, resolution_y) / pixels_per_unit


def camera_location(
    resolution_y: int, pixels_per_unit: int, height_fraction: float
) -> tuple[float, float, float]:
    """Camera position ``(0, -100, (resolution_y / pixels_per_unit) * height_fraction)``.

    The camera looks straight down +Y; ``height_fraction`` is the share of the
    frame height (in units) that it sits above z = 0.

    Raises:
        ValueError: if pixels_per_unit is not positive.
    """
    if pixels_per_unit <= 0:
        raise ValueError(f"pixels_per_unit must be positive, got {pixels_per_unit}")
    return 0.0, -CAMERA_DISTANCE, (resolution_y / pixels_per_unit) * height_fraction


def screen_offset_per_depth_unit(
    slope_run: float, slope_rise: float, depth_scale: float, pixels_per_unit: float
) -> tuple[float, float]:
    """Pixels right and up that one unit of depth moves on screen.

    For run=2, rise=1, depth=1, ppu=16 this is ``(32/sqrt(5), 16/sqrt(5))``.

    Raises:
        ValueError: as :func:`shear_coefficients`.
    """
    a, b = shear_coefficients(slope_run, slope_rise, depth_scale)
    return a * pixels_per_unit, b * pixels_per_unit


def bounds(
    points: "list[tuple[float, float, float]]",
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Axis-aligned (min, max) corners of a set of points.

    Raises:
        ValueError: if ``points`` is empty.
    """
    if not points:
        raise ValueError("bounds() needs at least one point")
    lo = tuple(min(p[i] for p in points) for i in range(3))
    hi = tuple(max(p[i] for p in points) for i in range(3))
    return lo, hi  # type: ignore[return-value]


def placement_offset(
    bbox_min: tuple[float, float, float], origin_z: float, ground_by_bottom: bool = False
) -> tuple[float, float, float]:
    """Translation that seats an object in the rig: front edge at y = 0, origin at z = 0.

    ``bbox_min`` is the world-space minimum corner of the object's bounding box
    and ``origin_z`` the world z of its origin. The front edge (smallest y) goes
    to the y = 0 reference plane, so positive depth only ever shears the object
    up and right. Z is chosen so the origin lands on the floor, or with
    ``ground_by_bottom`` so the lowest point does. X is never changed.
    """
    dz = -bbox_min[2] if ground_by_bottom else -origin_z
    return 0.0, -bbox_min[1], dz


# --- 0.2: pixel-exact depth, camera snapping, projection and fit ------------------------

_EPS = 1e-9
"""Tolerance for float noise when rounding pixel counts up or down."""

Vec3 = tuple[float, float, float]


def pixel_step_to_slope(px_right: int, px_up: int, pixels_per_unit: int) -> tuple[float, float, float]:
    """``(slope_run, slope_rise, depth_scale)`` that make the shear move whole pixels.

    The node group computes ``normalize(run, rise) * depth``; feeding it
    ``(px_right, px_up, hypot(px_right, px_up) / ppu)`` yields
    ``(px_right / ppu, px_up / ppu)``.

    Raises:
        ValueError: if both steps are 0 or ``pixels_per_unit`` is not positive.
    """
    if pixels_per_unit <= 0:
        raise ValueError(f"pixels_per_unit must be positive, got {pixels_per_unit}")
    if px_right == 0 and px_up == 0:
        raise ValueError("depth_px_right and depth_px_up cannot both be 0")
    return float(px_right), float(px_up), math.hypot(px_right, px_up) / pixels_per_unit


def snap_to_pixel(value_units: float, pixels_per_unit: int) -> float:
    """Round a length in units to the nearest multiple of ``1 / pixels_per_unit``.

    Halves round away from zero (``0.5 px -> 1 px``, ``-0.5 px -> -1 px``).

    Raises:
        ValueError: if ``pixels_per_unit`` is not positive.
    """
    if pixels_per_unit <= 0:
        raise ValueError(f"pixels_per_unit must be positive, got {pixels_per_unit}")
    pixels = value_units * pixels_per_unit
    whole = math.floor(abs(pixels) + 0.5 + _EPS)
    return math.copysign(whole, pixels) / pixels_per_unit if whole else 0.0


def _even_up(value: int) -> int:
    return value + (value % 2)


TILE_STEP = 0.25
"""Side Tiles and Back Tiles are used in steps of this many tiles."""


def round_tiles(tiles: float) -> float:
    """``tiles`` rounded to the nearest :data:`TILE_STEP`, half up (0.125 -> 0.25, 0.7 -> 0.75).

    Side Tiles and Back Tiles are typed as floats; :meth:`RigParams.splay` and
    the Tiles panel readout both use this, so a typed 0.7 behaves as 0.75.
    """
    return math.floor(tiles / TILE_STEP + 0.5 + _EPS) * TILE_STEP


@dataclass(frozen=True)
class RigParams:
    """Every rig setting as a plain value (see ``properties.params_from_settings``).

    Defaults are the add-on's property defaults.
    """

    pixels_per_unit: int = 16
    resolution_x: int = 320
    resolution_y: int = 200
    slope_run: float = 2.0
    slope_rise: float = 1.0
    depth_scale: float = 0.6
    pixel_exact: bool = True
    depth_px_right: int = 16
    depth_px_up: int = 8
    camera_height_fraction: float = 0.35
    camera_offset_x: float = 0.0
    direction: float = 0.0
    """Rotation of the source about Z before the shear, radians."""
    auto_seat: bool = True
    view_mode: str = "SHEARED"
    crease_angle: float = math.radians(30)
    """Sharp-edge outlines: edges whose faces meet at more than this (radians) get a line."""
    edge_marked_sharp: bool = True
    """Sharp-edge outlines: edges marked sharp (the ``sharp_edge`` attribute) also get a line."""
    splay_enabled: bool = False
    """Tile Splay: move wall bases of terrain meshes outward in plan (see ``core.splay``)."""
    splay_level_height: float = 1.0
    """Tile Splay: units of wall height per level."""
    splay_side_tiles: float = 1.0
    """Tile Splay: tiles of side wall width per level (used rounded to half tiles)."""
    splay_back_tiles: float = 1.0
    """Tile Splay: tiles of back rim height per level (used rounded to half tiles)."""
    splay_snap: bool = True
    """Tile Splay: round levels half up to whole numbers."""

    def splay_tiles(self) -> tuple[float, float]:
        """``(side, back)`` tiles per level as used: each rounded with :func:`round_tiles`."""
        return round_tiles(self.splay_side_tiles), round_tiles(self.splay_back_tiles)

    def splay(self) -> tuple[float, float]:
        """Plan offsets per level ``(side, back)`` for ``core.splay.splay_offsets`` and the node group.

        ``side = side_tiles`` and ``back = (splay_level_height + back_tiles) / b``
        with the tiles from :meth:`splay_tiles` (rounded to quarter tiles) and
        ``b`` from :meth:`shear`: a back wall base first has to clear the
        top's back edge on screen, then show the rim. ``(0.0, 0.0)`` when Tile
        Splay is off (without computing the shear) or ``b`` is 0; zero offsets
        are how the node group is switched off.

        Raises:
            ValueError: only with Tile Splay on, as :meth:`shear`.
        """
        if not self.splay_enabled:
            return 0.0, 0.0
        _, b = self.shear()
        if b <= 0:
            return 0.0, 0.0
        side_tiles, back_tiles = self.splay_tiles()
        return side_tiles, (self.splay_level_height + back_tiles) / b

    def shear(self) -> tuple[float, float]:
        """Shear coefficients ``(a, b)``: units moved right and up per unit of depth.

        Pixel-exact: ``(depth_px_right / ppu, depth_px_up / ppu)``; otherwise
        :func:`shear_coefficients` of the slope controls.

        Raises:
            ValueError: pixel-exact with both steps 0 or ppu <= 0; else as
                :func:`shear_coefficients`.
        """
        if self.pixel_exact:
            pixel_step_to_slope(self.depth_px_right, self.depth_px_up, self.pixels_per_unit)
            return self.depth_px_right / self.pixels_per_unit, self.depth_px_up / self.pixels_per_unit
        return shear_coefficients(self.slope_run, self.slope_rise, self.depth_scale)

    def modifier_inputs(self) -> tuple[float, float, float]:
        """``(Slope Run, Slope Rise, Depth Scale)`` for the node group so it yields :meth:`shear`.

        Pixel-exact: ``(px_right, px_up, hypot(px_right, px_up) / ppu)``;
        otherwise the slope controls unchanged.
        """
        if self.pixel_exact:
            return pixel_step_to_slope(self.depth_px_right, self.depth_px_up, self.pixels_per_unit)
        return float(self.slope_run), float(self.slope_rise), float(self.depth_scale)

    def effective_resolution(self) -> tuple[int, int]:
        """Render resolution: rounded up to even numbers when pixel-exact, else unchanged.

        An even frame with a camera on a whole pixel puts the frame edges on
        the world pixel grid.
        """
        if self.pixel_exact:
            return _even_up(self.resolution_x), _even_up(self.resolution_y)
        return self.resolution_x, self.resolution_y

    def ortho_scale(self) -> float:
        """Orthographic scale for :meth:`effective_resolution` (see :func:`ortho_scale`)."""
        res_x, res_y = self.effective_resolution()
        return ortho_scale(res_x, res_y, self.pixels_per_unit)

    def depth_step_px(self) -> tuple[float, float]:
        """Pixels right and up that one unit of depth moves on screen."""
        a, b = self.shear()
        return a * self.pixels_per_unit, b * self.pixels_per_unit

    def camera(self) -> Vec3:
        """Camera location ``(x, -CAMERA_DISTANCE, z)``.

        ``z = (resolution_y / ppu) * camera_height_fraction`` with the
        effective resolution, ``x = camera_offset_x``. Pixel-exact: both are
        snapped to whole pixels with :func:`snap_to_pixel`.

        Raises:
            ValueError: if pixels_per_unit is not positive.
        """
        _, res_y = self.effective_resolution()
        x, y, z = camera_location(res_y, self.pixels_per_unit, self.camera_height_fraction)
        x = float(self.camera_offset_x)
        if self.pixel_exact:
            x = snap_to_pixel(x, self.pixels_per_unit)
            z = snap_to_pixel(z, self.pixels_per_unit)
        return x, y, z


SPLAY_NEEDS_DEPTH_UP = "Tile Splay needs Depth Up above 0"
SPLAY_NEEDS_DEPTH_RIGHT_0 = "Whole tiles need Depth Right 0"
SPLAY_TILES_NOT_WHOLE_PX = "Side or Back Tiles is not a whole number of pixels"


def _splay_b(params: RigParams) -> float:
    """Depth-up shear ``b`` for the Tiles panel; 0.0 when the projection is invalid."""
    try:
        return params.shear()[1]
    except ValueError:
        return 0.0


def splay_level_px(params: RigParams) -> tuple[float, float, float]:
    """Screen pixels per level ``(front, side, back)`` with Tile Splay.

    Front wall height ``level_height * ppu``, side wall width ``side_tiles * ppu``
    and back rim height ``back_tiles * ppu``, with the tiles rounded as
    :meth:`RigParams.splay_tiles`. Exact with Depth Right 0; with Depth Right
    above 0 the back offset also shifts the base sideways.
    """
    ppu = params.pixels_per_unit
    side_tiles, back_tiles = params.splay_tiles()
    return params.splay_level_height * ppu, side_tiles * ppu, back_tiles * ppu


def splay_readout(params: RigParams) -> str | None:
    """Tiles panel readout, e.g. ``"1 level = 16 px front, 16 px side, 16 px back"``.

    None when Depth Up is 0 or the projection is invalid (the splay is then off).
    """
    if _splay_b(params) <= 0:
        return None
    front, side, back = splay_level_px(params)
    return f"1 level = {front:g} px front, {side:g} px side, {back:g} px back"


def splay_warnings(params: RigParams) -> list[str]:
    """Tiles panel warnings for the current settings; empty when Tile Splay is off.

    - :data:`SPLAY_NEEDS_DEPTH_UP`: ``b`` is 0 (or the projection is invalid),
      so :meth:`RigParams.splay` returns zero offsets.
    - :data:`SPLAY_NEEDS_DEPTH_RIGHT_0`: ``a`` is not 0, so the back offset
      also moves wall bases sideways by an amount that need not be whole pixels.
    - :data:`SPLAY_TILES_NOT_WHOLE_PX`: a rounded tile count times Pixels per
      Unit is not a whole number (a quarter tile needs Pixels per Unit to be a
      multiple of 4, a half tile an even one).
    """
    if not params.splay_enabled:
        return []
    try:
        a, b = params.shear()
    except ValueError:
        return [SPLAY_NEEDS_DEPTH_UP]
    if b <= 0:
        return [SPLAY_NEEDS_DEPTH_UP]
    warnings = []
    if a != 0:
        warnings.append(SPLAY_NEEDS_DEPTH_RIGHT_0)
    if any((tiles * params.pixels_per_unit) % 1 for tiles in params.splay_tiles()):
        warnings.append(SPLAY_TILES_NOT_WHOLE_PX)
    return warnings


def project_to_pixels(
    point: Sequence[float],
    a: float,
    b: float,
    pixels_per_unit: float,
    cam_x: float,
    cam_z: float,
    res_x: int,
    res_y: int,
) -> tuple[float, float]:
    """Continuous pixel position of an unsheared world point, origin at the frame's bottom-left.

    ``px = (x + a*y - cam_x) * ppu + res_x / 2`` and
    ``py = (z + b*y - cam_z) * ppu + res_y / 2``. Pass ``a = b = 0`` for a
    point that is already sheared. The point lies in pixel
    ``(floor(px), floor(py))``.
    """
    x, y, z = point[0], point[1], point[2]
    px = (x + a * y - cam_x) * pixels_per_unit + res_x / 2
    py = (z + b * y - cam_z) * pixels_per_unit + res_y / 2
    return px, py


def screen_rect(
    points: Iterable[Sequence[float]],
    a: float,
    b: float,
    pixels_per_unit: float,
    cam_x: float,
    cam_z: float,
    res_x: int,
    res_y: int,
) -> tuple[float, float, float, float]:
    """``(xmin, ymin, xmax, ymax)`` in pixels of the projected points (see :func:`project_to_pixels`).

    Raises:
        ValueError: if ``points`` is empty.
    """
    projected = [project_to_pixels(p, a, b, pixels_per_unit, cam_x, cam_z, res_x, res_y) for p in points]
    if not projected:
        raise ValueError("screen_rect() needs at least one point")
    xs = [p[0] for p in projected]
    ys = [p[1] for p in projected]
    return min(xs), min(ys), max(xs), max(ys)


def _ceil(value: float) -> int:
    return math.ceil(value - _EPS)


def _floor(value: float) -> int:
    return math.floor(value + _EPS)


def footprint_px(
    bounds_min_sheared: Sequence[float], bounds_max_sheared: Sequence[float], pixels_per_unit: int
) -> tuple[int, int]:
    """Size ``(width, height)`` in whole pixels of sheared bounds: ``ceil((max - min) * ppu)``.

    Uses x (index 0) and z (index 2); float noise below 1e-9 px is ignored,
    so an extent of ``24.0000000001`` px is 24, not 25.
    """
    width = _ceil((bounds_max_sheared[0] - bounds_min_sheared[0]) * pixels_per_unit)
    height = _ceil((bounds_max_sheared[2] - bounds_min_sheared[2]) * pixels_per_unit)
    return max(width, 0), max(height, 0)


@dataclass(frozen=True)
class FitResult:
    """Frame that fits sheared content: feed it back into the settings."""

    resolution_x: int
    resolution_y: int
    camera_offset_x: float
    camera_height_fraction: float


def _fit_axis(lo: float, hi: float, pixels_per_unit: int, padding_px: int, even: bool) -> tuple[int, float]:
    """Resolution and camera position (units) along one axis."""
    # Pixel-grid cells the content touches; equals ceil((hi - lo) * ppu) when lo is on the grid.
    first = _floor(lo * pixels_per_unit) - padding_px
    last = _ceil(hi * pixels_per_unit) + padding_px
    size = max(last - first, 1)
    if even:
        size = _even_up(size)
    # Extra pixels from rounding go on the high side; the frame stays on the pixel grid.
    centre_px = first + size / 2
    return size, centre_px / pixels_per_unit


def fit_frame(
    bounds_min_sheared: Sequence[float],
    bounds_max_sheared: Sequence[float],
    pixels_per_unit: int,
    padding_px: int,
    even: bool = True,
) -> FitResult:
    """Resolution and camera that frame sheared bounds with ``padding_px`` pixels on every side.

    Works on x (index 0) and z (index 2). Each axis covers every pixel-grid
    cell the content touches, ``ceil(max*ppu) - floor(min*ppu)`` (which is
    ``ceil((max - min) * ppu)`` for grid-aligned bounds), plus ``2*padding``,
    rounded up to even when ``even``. The frame edges lie on the world pixel
    grid, so with ``even`` the camera sits on a whole pixel (a multiple of
    ``1/ppu``); without it an odd size puts the camera on a half pixel.
    ``camera_height_fraction`` is chosen so that
    ``(resolution_y / ppu) * fraction`` is that camera z.

    Raises:
        ValueError: if ``pixels_per_unit`` is not positive or ``padding_px`` is negative.
    """
    if pixels_per_unit <= 0:
        raise ValueError(f"pixels_per_unit must be positive, got {pixels_per_unit}")
    if padding_px < 0:
        raise ValueError(f"padding_px must be >= 0, got {padding_px}")
    res_x, cam_x = _fit_axis(bounds_min_sheared[0], bounds_max_sheared[0], pixels_per_unit, padding_px, even)
    res_y, cam_z = _fit_axis(bounds_min_sheared[2], bounds_max_sheared[2], pixels_per_unit, padding_px, even)
    return FitResult(
        resolution_x=res_x,
        resolution_y=res_y,
        camera_offset_x=cam_x,
        camera_height_fraction=cam_z / (res_y / pixels_per_unit),
    )
