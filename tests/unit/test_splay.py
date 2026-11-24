"""Unit tests for core/splay.py: the wall splay reference for top-down tile blockouts.

Settings throughout match the plan's verification (docs/PLAN-tile-splay.md,
section 3): 16 px per unit, Depth Up 16 (b = 1), level height 1, side 1, back 1,
so one level moves a side wall base by 1 unit and a back wall base by
(1 + 1) / 1 = 2 units."""

import importlib.util
import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_core_module(name: str):
    """Import src/<id>/core/<name>.py directly (core/ must never import bpy)."""
    [addon_dir] = [p.parent for p in (ROOT / "src").glob("*/blender_manifest.toml")]
    path = addon_dir / "core" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"core_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


splay = load_core_module("splay")

SIDE = 1.0
BACK = 2.0  # (level_height + back_tiles) / b = (1 + 1) / 1
EXACT = 1e-12


def box(
    x0: float, x1: float, y0: float, y1: float, z0: float, z1: float, offset: int = 0, inward: bool = False
):
    """Closed box with outward normals (Newell / Blender winding), or inward ones for a pit."""
    verts = [
        (x0, y0, z0),
        (x1, y0, z0),
        (x1, y1, z0),
        (x0, y1, z0),
        (x0, y0, z1),
        (x1, y0, z1),
        (x1, y1, z1),
        (x0, y1, z1),
    ]
    faces = [
        (0, 3, 2, 1),  # bottom, -Z
        (4, 5, 6, 7),  # top, +Z
        (0, 1, 5, 4),  # front, -Y
        (1, 2, 6, 5),  # right, +X
        (2, 3, 7, 6),  # back, +Y
        (3, 0, 4, 7),  # left, -X
    ]
    if inward:
        faces = [tuple(reversed(f)) for f in faces]
    return verts, [tuple(i + offset for i in f) for f in faces]


def offsets(verts, faces, **kwargs) -> np.ndarray:
    kwargs.setdefault("side", SIDE)
    kwargs.setdefault("back", BACK)
    kwargs.setdefault("level_height", 1.0)
    return splay.splay_offsets(verts, faces, **kwargs)


def test_attribute_names() -> None:
    assert splay.TERRAIN_ATTRIBUTE == "oblique_terrain"
    assert splay.SPLAY_ATTRIBUTE == "oblique_splay"


def test_result_shape_and_type() -> None:
    verts, faces = box(-2, 2, 0, 3, 0, 1)
    out = offsets(verts, faces)
    assert isinstance(out, np.ndarray)
    assert out.shape == (8, 2)


# --- the four shapes of section 3 -----------------------------------------------------------


def test_box_tops_stay_and_base_corners_splay() -> None:
    verts, faces = box(-2, 2, 0, 3, 0, 1)  # 4 x 3 x 1
    out = offsets(verts, faces)
    np.testing.assert_allclose(out[4:], 0.0, atol=0)
    np.testing.assert_allclose(out[:4], [(-1, 0), (1, 0), (1, 2), (-1, 2)], atol=EXACT)


def test_box_on_screen_sizes() -> None:
    """Top 64 x 48 px, side walls 16 px wide, front wall 16 px tall, back rim 16 px (a = 0, b = 1)."""
    ppu, b = 16, 1.0
    verts, faces = box(-2, 2, 0, 3, 0, 1)
    out = offsets(verts, faces)
    moved = np.asarray(verts, float)
    moved[:, :2] += out
    screen = np.stack([moved[:, 0], moved[:, 2] + b * moved[:, 1]], axis=1) * ppu
    top = screen[4:]
    assert top[:, 0].max() - top[:, 0].min() == pytest.approx(64)
    assert top[:, 1].max() - top[:, 1].min() == pytest.approx(48)
    # left wall: base x - top x
    assert screen[4, 0] - screen[0, 0] == pytest.approx(16)
    # front wall: front top edge minus front base edge
    assert screen[4, 1] - screen[0, 1] == pytest.approx(16)
    # back rim: back base edge above the top's back edge
    assert screen[3, 1] - screen[7, 1] == pytest.approx(16)


def test_flush_stack_on_the_west_side() -> None:
    lower_v, lower_f = box(-2, 2, 0, 3, 0, 1)
    upper_v, upper_f = box(-2, 0, 0, 3, 1, 2, offset=8)
    verts, faces = lower_v + upper_v, lower_f + upper_f
    out = offsets(verts, faces)
    # West wall, front column: base -2, middle -1 (both boxes' vertices), top 0.
    assert out[0] == pytest.approx((-2, 0), abs=EXACT)
    assert out[4] == pytest.approx((-1, 0), abs=EXACT)
    assert out[8] == pytest.approx((-1, 0), abs=EXACT)
    assert out[12] == pytest.approx((0, 0), abs=0)
    # West wall, back column: two levels of side and back.
    assert out[3] == pytest.approx((-2, 4), abs=EXACT)
    assert out[7] == pytest.approx((-1, 2), abs=EXACT)
    # The lower box's top edge on the other sides stays put.
    assert out[5] == pytest.approx((0, 0), abs=0)
    assert out[6] == pytest.approx((0, 0), abs=0)
    # Its base on the east side splays one level.
    assert out[1] == pytest.approx((1, 0), abs=EXACT)
    assert out[2] == pytest.approx((1, 2), abs=EXACT)


def test_pit_bottom_corners_move_into_the_pit() -> None:
    verts, faces = box(-2, 2, 0, 3, 0, 1, inward=True)
    out = offsets(verts, faces)
    np.testing.assert_allclose(out[4:], 0.0, atol=0)
    # Left wall faces +X (side), right wall -X (side), front wall +Y (back), back wall -Y (front).
    np.testing.assert_allclose(out[:4], [(1, 2), (-1, 2), (-1, 0), (1, 0)], atol=EXACT)


def cylinder(sides: int = 24, radius: float = 2.0, height: float = 1.0):
    angles = [2 * math.pi * i / sides for i in range(sides)]
    ring = [(radius * math.cos(t), radius * math.sin(t)) for t in angles]
    verts = [(x, y, 0.0) for x, y in ring] + [(x, y, height) for x, y in ring]
    faces = [(i, (i + 1) % sides, sides + (i + 1) % sides, sides + i) for i in range(sides)]
    faces.append(tuple(range(sides - 1, -1, -1)))  # bottom cap, -Z
    faces.append(tuple(range(sides, 2 * sides)))  # top cap, +Z
    return verts, faces, angles


def test_cylinder_front_half_shifts_by_one_in_x() -> None:
    sides = 24
    verts, faces, angles = cylinder(sides)
    out = offsets(verts, faces)
    np.testing.assert_allclose(out[sides:], 0.0, atol=0)
    assert np.all(np.isfinite(out))
    step = 2 * math.pi / sides
    front = 3 * sides // 4  # the vertex at 270 degrees points straight at the camera
    for i, t in enumerate(angles):
        if math.sin(t) < -1e-9 and i != front:
            assert out[i] == pytest.approx((math.copysign(1.0, math.cos(t)), 0.0), abs=1e-12), i
    # The front-pointing vertex bulges forward by tan(half step) (section 9).
    assert out[front] == pytest.approx((0.0, -math.tan(step / 2)), abs=1e-12)
    assert abs(out[front][1]) == pytest.approx(0.13, abs=0.01)
    # The back-pointing vertex moves straight back: offset . n = hypot(side*hx, back*hy) on both faces.
    s, c = math.sin(step / 2), math.cos(step / 2)
    assert out[sides // 4] == pytest.approx((0.0, math.hypot(SIDE * s, BACK * c) / c), abs=1e-12)


# --- solve branches ------------------------------------------------------------------------


def straight_wall(direction: tuple[float, float], segments: int = 3, height: float = 1.0):
    """An open wall of quads along ``direction`` from the origin; its normal is direction rotated -90 degrees."""
    dx, dy = direction
    base = [(i * dx, i * dy, 0.0) for i in range(segments + 1)]
    top = [(x, y, height) for x, y, _ in base]
    verts = base + top
    n = segments + 1
    faces = [(i, i + 1, n + i + 1, n + i) for i in range(segments)]
    return verts, faces


def test_straight_diagonal_front_wall_moves_by_k_times_normal() -> None:
    verts, faces = straight_wall((1.0, 1.0))  # normal (1, -1) / sqrt 2: front-right
    out = offsets(verts, faces)
    hx, hy = 1 / math.sqrt(2), -1 / math.sqrt(2)
    k = SIDE * hx  # back term is zero for front-facing walls
    for i in range(4):
        assert out[i] == pytest.approx((k * hx, k * hy), abs=EXACT)
    np.testing.assert_allclose(out[4:], 0.0, atol=0)


def test_straight_diagonal_back_wall_moves_by_k_times_normal() -> None:
    verts, faces = straight_wall((-1.0, -2.0))  # normal (-2, 1) / sqrt 5: back-left
    out = offsets(verts, faces)
    hx, hy = -2 / math.sqrt(5), 1 / math.sqrt(5)
    k = math.hypot(SIDE * hx, BACK * hy)
    for i in range(4):
        assert out[i] == pytest.approx((k * hx, k * hy), abs=EXACT)


# --- snap and levels -----------------------------------------------------------------------


@pytest.mark.parametrize(("height", "snapped"), [(0.9, 1.0), (0.4, 0.0), (0.5, 1.0), (1.49, 1.0), (2.6, 3.0)])
def test_snap_rounds_levels_half_up(height: float, snapped: float) -> None:
    verts, faces = box(-2, 2, 0, 3, 0, height)
    on = offsets(verts, faces, snap=True)
    off = offsets(verts, faces, snap=False)
    assert on[0] == pytest.approx((-snapped, 0), abs=EXACT)
    assert on[2] == pytest.approx((snapped * SIDE, snapped * BACK), abs=EXACT)
    assert off[0] == pytest.approx((-height, 0), abs=EXACT)
    assert off[2] == pytest.approx((height * SIDE, height * BACK), abs=EXACT)


def test_level_height_divides_the_drop() -> None:
    verts, faces = box(-2, 2, 0, 3, 0, 2)
    out = offsets(verts, faces, level_height=2.0)
    assert out[0] == pytest.approx((-1, 0), abs=EXACT)
    out = offsets(verts, faces, level_height=0.5)
    assert out[0] == pytest.approx((-4, 0), abs=EXACT)


def test_zero_offsets_switch_splay_off() -> None:
    verts, faces = box(-2, 2, 0, 3, 0, 1)
    out = offsets(verts, faces, side=0.0, back=0.0)
    np.testing.assert_array_equal(out, 0.0)


# --- terrain mask --------------------------------------------------------------------------


def test_vertices_outside_the_terrain_mask_get_zero() -> None:
    verts, faces = box(-2, 2, 0, 3, 0, 1)
    out = offsets(verts, faces, terrain=[False] * 8)
    np.testing.assert_array_equal(out, 0.0)


def test_props_do_not_raise_column_tops() -> None:
    """A prop standing on the plateau at the same plan positions leaves the terrain splay as it was."""
    ground_v, ground_f = box(-2, 2, 0, 3, 0, 1)
    prop_v, prop_f = box(-2, 2, 0, 3, 1, 4, offset=8)
    verts, faces = ground_v + prop_v, ground_f + prop_f
    terrain = [True] * 8 + [False] * 8
    out = offsets(verts, faces, terrain=terrain)
    np.testing.assert_allclose(out[:8], offsets(ground_v, ground_f), atol=EXACT)
    np.testing.assert_array_equal(out[8:], 0.0)


# --- face normals --------------------------------------------------------------------------


def pacman_wall(radius: float = 2.0, steps: int = 24):
    """One concave vertical face in the plane x = 0 facing -X: a disc with a 90-degree mouth toward +Y.

    The arc vertices at angles t and -t share a plan position, so the lower ones sit
    under a column top and have levels.
    """
    mouth = math.radians(45)
    arc = [mouth + (2 * math.pi - 2 * mouth) * i / steps for i in range(steps + 1)]
    yz = [(0.0, 0.0)] + [(radius * math.cos(t), radius * math.sin(t)) for t in arc]
    yz.reverse()  # counter-clockwise in (y, z) faces +X; reversed faces -X
    verts = [(0.0, y, z) for y, z in yz]
    return verts, [tuple(range(len(verts)))]


def test_face_normal_is_newell() -> None:
    verts, faces = box(-2, 2, 0, 3, 0, 1)
    p = np.asarray(verts, float)
    expected = [(0, 0, -1), (0, 0, 1), (0, -1, 0), (1, 0, 0), (0, 1, 0), (-1, 0, 0)]
    for face, normal in zip(faces, expected, strict=True):
        got = splay.face_normal(p[list(face)])
        assert got / np.linalg.norm(got) == pytest.approx(normal, abs=EXACT)


def test_face_normal_of_a_concave_face() -> None:
    verts, faces = pacman_wall()
    p = np.asarray(verts, float)[list(faces[0])]
    got = splay.face_normal(p)
    assert got[0] < 0 and got[1:] == pytest.approx((0, 0), abs=EXACT)
    # The old corner-cross sum points the other way on this face.
    old = np.cross(p - np.roll(p, 1, axis=0), np.roll(p, -1, axis=0) - p).sum(axis=0)
    assert old[0] > 0


def test_concave_wall_face_splays_outward() -> None:
    verts, faces = pacman_wall()
    out = offsets(verts, faces, snap=False)
    p = np.asarray(verts, float)
    moved = 0
    for i in range(len(verts)):
        column = np.abs(p[:, 1] - p[i, 1]) < 1e-9
        drop = p[column, 2].max() - p[i, 2]
        assert out[i] == pytest.approx((-SIDE * drop, 0.0), abs=1e-9), i
        moved += drop > 0
    assert moved >= 10


def test_degenerate_faces_are_ignored() -> None:
    verts, faces = box(-2, 2, 0, 3, 0, 1)
    out = offsets(verts, [*faces, (0, 0, 0)])
    np.testing.assert_allclose(out, offsets(verts, faces), atol=0)


def test_rejects_non_positive_level_height() -> None:
    verts, faces = box(-2, 2, 0, 3, 0, 1)
    with pytest.raises(ValueError):
        offsets(verts, faces, level_height=0.0)


# --------------------------------------------------------------------------- loose parts (details)


def ringed_box(x0=-2.0, x1=2.0, ys=(0.0, 1.0, 2.0, 3.0), z0=0.0, z1=1.0, offset: int = 0):
    """A box with an edge loop at every y in ``ys``: mid-wall vertices sit on straight walls."""
    verts, faces = [], []
    for y in ys:
        verts += [(x0, y, z0), (x1, y, z0), (x1, y, z1), (x0, y, z1)]
    for r in range(len(ys) - 1):
        a, b = 4 * r, 4 * r + 4
        faces += [
            (a + 0, a + 1, b + 1, b + 0),  # bottom
            (a + 1, a + 2, b + 2, b + 1),  # right, +X... wound below
            (a + 2, a + 3, b + 3, b + 2),  # top
            (a + 3, a + 0, b + 0, b + 3),  # left
        ]
    last = 4 * (len(ys) - 1)
    faces += [(0, 3, 2, 1), (last + 0, last + 1, last + 2, last + 3)]
    faces = [tuple(reversed(f)) for f in faces]
    return verts, [tuple(i + offset for i in f) for f in faces]


def tetra(centre, size: float = 0.1, offset: int = 0):
    cx, cy, cz = centre
    verts = [
        (cx - size, cy - size, cz - size),
        (cx + size, cy - size, cz - size),
        (cx, cy + size, cz - size),
        (cx, cy, cz + size),
    ]
    faces = [(0, 2, 1), (0, 1, 3), (1, 2, 3), (2, 0, 3)]
    return verts, [tuple(i + offset for i in f) for f in faces]


def test_ringed_box_winds_outward() -> None:
    verts, faces = ringed_box()
    got = offsets(verts, faces)
    by_pos = {tuple(v): tuple(o) for v, o in zip(verts, got, strict=True)}
    assert by_pos[(-2.0, 0.0, 0.0)] == pytest.approx((-1.0, 0.0), abs=EXACT)
    assert by_pos[(-2.0, 1.0, 0.0)] == pytest.approx((-1.0, 0.0), abs=EXACT)
    assert by_pos[(2.0, 3.0, 0.0)] == pytest.approx((1.0, 2.0), abs=EXACT)
    assert by_pos[(2.0, 2.0, 1.0)] == (0.0, 0.0)


def test_detail_mask_marks_every_part_but_the_largest() -> None:
    verts, faces = ringed_box()
    t_verts, t_faces = tetra((-2.1, 1.5, 0.5), offset=len(verts))
    mask = splay.detail_mask(verts + t_verts, faces + t_faces)
    assert mask.dtype == bool
    assert mask.tolist() == [False] * len(verts) + [True] * 4


def test_detail_mask_keeps_parts_of_equal_size() -> None:
    verts, faces = box(0, 1, 0, 1, 0, 1)
    verts2, faces2 = box(3, 4, 0, 1, 0, 1, offset=8)
    assert not splay.detail_mask(verts + verts2, faces + faces2).any()


def test_detail_mask_of_one_part_or_none() -> None:
    verts, faces = box(0, 1, 0, 1, 0, 1)
    assert not splay.detail_mask(verts, faces).any()
    assert splay.detail_mask([], []).tolist() == []


def test_detail_mask_counts_loose_vertices_and_edges_as_details() -> None:
    verts, faces = box(0, 1, 0, 1, 0, 1)
    verts = [*verts, (5.0, 5.0, 5.0), (6.0, 5.0, 5.0), (9.0, 9.0, 9.0)]
    mask = splay.detail_mask(verts, faces, edges=[(8, 9)])
    assert mask.tolist() == [False] * 8 + [True] * 3


def _box_with_tetra(centre):
    verts, faces = ringed_box()
    t_verts, t_faces = tetra(centre, offset=len(verts))
    detail = [False] * len(verts) + [True] * 4
    return verts + t_verts, faces + t_faces, detail


def test_detail_on_a_side_wall_follows_the_wall() -> None:
    """Each detail vertex moves as the nearest point of the wall does: by ``-(1 - z)`` in x."""
    verts, faces, detail = _box_with_tetra((-2.1, 1.5, 0.5))
    got = offsets(verts, faces, detail=detail)
    for vert, offset in zip(verts[-4:], got[-4:], strict=True):
        assert offset == pytest.approx((-(1.0 - vert[2]), 0.0), abs=1e-9), vert
    plain = offsets(*ringed_box())
    assert got[:-4] == pytest.approx(plain, abs=EXACT), "the box itself is unchanged"


def test_detail_without_the_mask_is_splayed_as_its_own_wall() -> None:
    verts, faces, _ = _box_with_tetra((-2.1, 1.5, 0.5))
    got = offsets(verts, faces)
    assert got[-4:-1] != pytest.approx(np.full((3, 2), (-0.6, 0.0)), abs=1e-3)


def test_detail_on_a_top_does_not_move() -> None:
    verts, faces, detail = _box_with_tetra((0.0, 1.5, 1.1))
    got = offsets(verts, faces, detail=detail)
    assert got[-4:] == pytest.approx(np.zeros((4, 2)), abs=1e-9)


def test_detail_on_a_front_wall_does_not_move() -> None:
    """Not even sideways, although the front wall's own corners move apart."""
    verts, faces, detail = _box_with_tetra((0.0, -0.1, 0.5))
    got = offsets(verts, faces, detail=detail)
    assert got[-4:] == pytest.approx(np.zeros((4, 2)), abs=1e-9)


def test_detail_on_a_back_wall_near_a_corner_only_moves_back() -> None:
    verts, faces, detail = _box_with_tetra((-1.7, 3.1, 0.5))
    got = offsets(verts, faces, detail=detail)
    for vert, offset in zip(verts[-4:], got[-4:], strict=True):
        assert offset == pytest.approx((0.0, 2.0 * (1.0 - vert[2])), abs=1e-9), vert


def test_detail_above_a_wall_does_not_raise_the_column() -> None:
    verts, faces = ringed_box()
    verts = [*verts, (-2.0, 0.0, 1.6)]  # straight above the front left corner
    got = offsets(verts, faces, detail=[False] * (len(verts) - 1) + [True])
    assert got[0] == pytest.approx((-1.0, 0.0), abs=EXACT)  # still 1 level, not round(1.6) = 2


def test_detail_only_follows_its_own_group() -> None:
    """A ground plane in another mesh is nearer to the detail, but the detail follows its own box."""
    verts, faces, detail = _box_with_tetra((-2.2, 1.5, 0.15))
    n = len(verts)
    ground = [(-9.0, -9.0, 0.0), (9.0, -9.0, 0.0), (9.0, 9.0, 0.0), (-9.0, 9.0, 0.0)]
    verts = verts + ground
    faces = [*faces, (n, n + 1, n + 2, n + 3)]
    detail = detail + [False] * 4
    group = [1] * n + [2] * 4
    got = offsets(verts, faces, detail=detail, group=group)
    lowest = verts[n - 4]
    assert got[n - 4] == pytest.approx((-(1.0 - lowest[2]), 0.0), abs=1e-9)
    ungrouped = offsets(verts, faces, detail=detail)
    assert ungrouped[n - 4] == pytest.approx((0.0, 0.0), abs=1e-9), "nearest is the flat ground"


def test_detail_with_no_body_in_its_group_does_not_move() -> None:
    verts, faces, detail = _box_with_tetra((-2.1, 1.5, 0.5))
    group = [1] * (len(verts) - 4) + [7] * 4
    got = offsets(verts, faces, detail=detail, group=group)
    assert got[-4:] == pytest.approx(np.zeros((4, 2)), abs=EXACT)


def test_detail_outside_the_terrain_mask_does_not_move() -> None:
    verts, faces, detail = _box_with_tetra((-2.1, 1.5, 0.5))
    terrain = [True] * (len(verts) - 4) + [False] * 4
    got = offsets(verts, faces, detail=detail, terrain=terrain)
    assert got[-4:] == pytest.approx(np.zeros((4, 2)), abs=EXACT)


def test_detail_attribute_names() -> None:
    assert splay.DETAIL_ATTRIBUTE == "oblique_detail"
    assert splay.MESH_ID_ATTRIBUTE == "oblique_mesh"


def test_mesh_id_is_a_stable_signed_32_bit_integer() -> None:
    assert splay.mesh_id("Cube") == splay.mesh_id("Cube")
    assert splay.mesh_id("Cube") != splay.mesh_id("Cube.001")
    for name in ("Cube", "Cube.001", "", "Fels \u00e4"):
        assert -(2**31) <= splay.mesh_id(name) < 2**31
    assert any(splay.mesh_id(f"m{i}") < 0 for i in range(20))
