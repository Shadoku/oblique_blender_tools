"""Tile splay in the node group: the rig's evaluated mesh is the shear of (source + core splay offsets).

Every comparison builds the expected vertices in plain Python from the source
meshes: turn by Direction, seat as Auto Seat does, add
``core.splay.splay_offsets`` (with the ``oblique_terrain`` mask read from each
mesh), shear with ``RigParams.shear()``. The node group computes in single
precision, so positions are compared within 1e-4 as multisets (the realized
vertex order is not part of the contract).

Terrain is marked by writing the ``oblique_terrain`` point attribute directly;
the Mark and Clear operators have their own tests.
"""

import math

import bpy
import numpy as np
import pytest

TOL = 1e-4


# --------------------------------------------------------------------------- scene helpers


def _settings(addon):
    return addon.properties.get_settings(bpy.context)


def _params(addon):
    return addon.properties.params_from_settings(_settings(addon))


def _top_down(addon, **overrides) -> None:
    """16 px per unit, top-down (Depth Right 0, Depth Up 16, so b = 1), Tile Splay on, rig built."""
    settings = _settings(addon)
    settings.pixels_per_unit = 16
    settings.pixel_exact = True
    settings.depth_px_right = 0
    settings.depth_px_up = 16
    settings.splay_enabled = True
    for key, value in overrides.items():
        setattr(settings, key, value)
    assert bpy.ops.oblique_rig.build() == {"FINISHED"}


def _rebuild() -> None:
    assert bpy.ops.oblique_rig.build() == {"FINISHED"}


def _box(x0, x1, y0, y1, z0, z1, inward: bool = False):
    """Closed box with outward normals (or inward ones, for a pit)."""
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
    faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    if inward:
        faces = [tuple(reversed(f)) for f in faces]
    return verts, faces


def _mesh_object(addon, name: str, verts, faces, terrain: bool) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    if terrain:
        attr = mesh.attributes.new(addon.core.splay.TERRAIN_ATTRIBUTE, "BOOLEAN", "POINT")
        attr.data.foreach_set("value", [True] * len(mesh.vertices))
    ob = bpy.data.objects.new(name, mesh)
    addon.rig.get_or_create_collection(bpy.context.scene).objects.link(ob)
    return ob


def _marked_box(addon, name: str = "Terrain", lo=(-2, 0, 0), hi=(2, 3, 1), inward: bool = False):
    verts, faces = _box(lo[0], hi[0], lo[1], hi[1], lo[2], hi[2], inward=inward)
    return _mesh_object(addon, name, verts, faces, terrain=True)


def _modifier(addon):
    o = addon.core.oblique
    return bpy.data.objects[o.RIG_NAME].modifiers[o.MODIFIER_NAME]


def _evaluated(addon):
    """(vertex positions, attribute names) of the rig's evaluated mesh, world space."""
    rig = bpy.data.objects[addon.core.oblique.RIG_NAME]
    depsgraph = bpy.context.evaluated_depsgraph_get()
    rig_eval = rig.evaluated_get(depsgraph)
    mesh = rig_eval.to_mesh()
    try:
        verts = [tuple(rig_eval.matrix_world @ v.co) for v in mesh.vertices]
        names = {a.name for a in mesh.attributes}
        return verts, names
    finally:
        rig_eval.to_mesh_clear()


# --------------------------------------------------------------------------- expected geometry


def _source_geometry(addon):
    """World-space verts, faces (global indices) and the terrain, detail and mesh id marks of every source mesh."""
    o = addon.core.oblique
    splay = addon.core.splay
    verts, faces, terrain, detail, group, top, base = [], [], [], [], [], [], []
    for ob in bpy.data.collections[o.SOURCE_COLLECTION].all_objects:
        if ob.type != "MESH":
            continue
        mesh = ob.data
        offset = len(verts)
        verts += [tuple(ob.matrix_world @ v.co) for v in mesh.vertices]
        faces += [tuple(offset + i for i in p.vertices) for p in mesh.polygons]
        attr = mesh.attributes.get(splay.TERRAIN_ATTRIBUTE)
        if attr is None:
            terrain += [False] * len(mesh.vertices)
        else:
            values = [False] * len(mesh.vertices)
            attr.data.foreach_get("value", values)
            terrain += [bool(v) for v in values]
        for name, target, default in (
            (splay.DETAIL_ATTRIBUTE, detail, False),
            (splay.MESH_ID_ATTRIBUTE, group, 0),
        ):
            attr = mesh.attributes.get(name)
            values = [default] * len(mesh.vertices)
            if attr is not None:
                attr.data.foreach_get("value", values)
            target += values
        for name, target in ((splay.WALL_TOP_ATTRIBUTE, top), (splay.WALL_BASE_ATTRIBUTE, base)):
            attr = mesh.attributes.get(name)
            values = list(range(len(mesh.vertices)))
            if attr is not None:
                attr.data.foreach_get("value", values)
            target += [offset + v for v in values]
    marks = (
        np.array(terrain, bool),
        np.array(detail, bool),
        np.array(group, np.int64),
        np.array(top, np.int64),
        np.array(base, np.int64),
    )
    return np.array(verts, float), faces, marks


def _expected(addon, splayed: bool = True) -> np.ndarray:
    """Turn, seat, splay (core reference), shear: what the rig should evaluate to."""
    params = _params(addon)
    verts, faces, (terrain, detail, group, top, base) = _source_geometry(addon)
    c, s = math.cos(params.direction), math.sin(params.direction)
    turned = verts @ np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]]).T
    if params.auto_seat:
        lo, hi = turned.min(axis=0), turned.max(axis=0)
        turned = turned + np.array([-(lo[0] + hi[0]) / 2, -lo[1], 0.0])
    side, back = params.splay()
    if splayed and (side or back):
        offsets = addon.core.splay.splay_offsets(
            turned,
            faces,
            side,
            back,
            params.splay_level_height,
            snap=params.splay_snap,
            terrain=terrain,
            detail=detail,
            group=group,
            top=top,
            base=base,
        )
        turned[:, :2] += offsets
    a, b = params.shear()
    return np.stack([turned[:, 0] + a * turned[:, 1], turned[:, 1], turned[:, 2] + b * turned[:, 1]], axis=1)


def _assert_same_points(actual, expected, tol: float = TOL) -> None:
    """Equal as multisets within ``tol`` (greedy nearest match)."""
    actual = np.asarray(actual, float).reshape(-1, 3)
    expected = np.asarray(expected, float).reshape(-1, 3)
    assert len(actual) == len(expected), (len(actual), len(expected))
    remaining = list(range(len(actual)))
    for point in expected:
        dist = np.abs(actual[remaining] - point).max(axis=1)
        best = int(np.argmin(dist))
        assert dist[best] <= tol, f"no evaluated vertex near {point.tolist()} (closest off by {dist[best]})"
        remaining.pop(best)


# --------------------------------------------------------------------------- node group interface


def test_new_inputs_follow_marked_sharp(addon) -> None:
    group = addon.rig.build_shear_nodegroup()
    names = [
        item.name
        for item in group.interface.items_tree
        if item.item_type == "SOCKET" and item.in_out == "INPUT"
    ]
    assert names == [
        "Geometry",
        "Source Collection",
        "Slope Run",
        "Slope Rise",
        "Depth Scale",
        "Direction",
        "Auto Seat",
        "Edge IDs",
        "Crease Angle",
        "Marked Sharp",
        "Splay Side",
        "Splay Back",
        "Level Height",
        "Splay Snap",
    ]


def test_build_sets_splay_inputs_from_params(addon) -> None:
    _top_down(addon, splay_level_height=2.0, splay_snap=False)
    params = _params(addon)
    side, back = params.splay()
    assert (side, back) == pytest.approx((1.0, 3.0)), "(2 + 1) / 1"
    get = addon.rig.get_modifier_input
    mod = _modifier(addon)
    assert get(mod, "Splay Side") == pytest.approx(side)
    assert get(mod, "Splay Back") == pytest.approx(back)
    assert get(mod, "Level Height") == pytest.approx(2.0)
    assert get(mod, "Splay Snap") is False


def test_splay_disabled_sets_zero_offsets(addon) -> None:
    _top_down(addon, splay_enabled=False)
    get = addon.rig.get_modifier_input
    mod = _modifier(addon)
    assert (get(mod, "Splay Side"), get(mod, "Splay Back")) == (0.0, 0.0)


# --------------------------------------------------------------------------- live settings


@pytest.mark.parametrize(
    ("side", "back", "base_corner"),
    [
        (0.0, 1.0, (2.0, 5.0)),  # side walls stay vertical, the back still splays
        (1.0, 0.0, (3.0, 4.0)),  # the back wall only clears the top edge: no rim
        (0.0, 0.0, (2.0, 4.0)),
        (0.25, 0.25, (2.25, 4.25)),
    ],
)
def test_tile_settings_apply_without_a_rebuild(addon, side, back, base_corner) -> None:
    """Changing Side or Back Tiles updates the built rig at once, including to 0."""
    _marked_box(addon)
    _top_down(addon, auto_seat=False)
    settings = _settings(addon)
    settings.splay_side_tiles = side
    settings.splay_back_tiles = back
    got, _ = _evaluated(addon)
    _assert_same_points(got, _expected(addon))
    x, y = base_corner  # back right base corner, sheared: z = b * y
    assert any(np.allclose(p, (x, y, y), atol=TOL) for p in got), sorted(got)


def test_every_tile_setting_updates_the_modifier(addon) -> None:
    _marked_box(addon)
    _top_down(addon)
    settings = _settings(addon)
    get = addon.rig.get_modifier_input
    mod = _modifier(addon)
    settings.splay_level_height = 2.0
    assert get(mod, "Level Height") == pytest.approx(2.0)
    assert get(mod, "Splay Back") == pytest.approx(3.0)
    settings.splay_snap = False
    assert get(mod, "Splay Snap") is False
    settings.splay_enabled = False
    assert (get(mod, "Splay Side"), get(mod, "Splay Back")) == (0.0, 0.0)
    settings.splay_enabled = True
    assert get(mod, "Splay Side") == pytest.approx(1.0)


def test_tile_settings_without_a_rig_change_nothing(addon) -> None:
    settings = _settings(addon)
    settings.splay_enabled = True
    settings.splay_side_tiles = 0.0
    assert bpy.data.objects.get(addon.core.oblique.RIG_NAME) is None
    assert bpy.data.node_groups.get(addon.core.oblique.NODEGROUP_NAME) is None


def test_tile_settings_with_an_invalid_projection_do_not_raise(addon) -> None:
    _marked_box(addon)
    _top_down(addon)
    settings = _settings(addon)
    settings.depth_px_up = 0  # both depth steps 0: shear() raises
    settings.splay_side_tiles = 2.0
    assert addon.rig.get_modifier_input(_modifier(addon), "Splay Side") == pytest.approx(1.0)


# --------------------------------------------------------------------------- shapes against the reference


def test_marked_box_matches_reference(addon) -> None:
    _top_down(addon)
    _marked_box(addon)
    verts, _ = _evaluated(addon)
    _assert_same_points(verts, _expected(addon))
    # Spot check against the plan's numbers (b = 1, a = 0): the back-left base corner
    # (-2, 3, 0) moves by (-1, 2) to (-3, 5, 0) and shears to (-3, 5, 5).
    _assert_same_points([v for v in verts if abs(v[0] + 3) < TOL and abs(v[1] - 5) < TOL], [(-3, 5, 5)])


def _prism(addon, name: str, outline, height: float = 1.0) -> bpy.types.Object:
    """A marked prism over a counter-clockwise plan outline: one wall quad per outline edge."""
    n = len(outline)
    verts = [(x, y, 0.0) for x, y in outline] + [(x, y, height) for x, y in outline]
    faces = [(i, (i + 1) % n, n + (i + 1) % n, n + i) for i in range(n)]
    faces += [tuple(range(n - 1, -1, -1)), tuple(range(n, 2 * n))]
    return _mesh_object(addon, name, verts, faces, terrain=True)


def test_vertices_on_straight_walls_match_reference(addon) -> None:
    """A box with a vertex on every unit of wall: mid-wall vertices take the parallel-walls branch."""
    _top_down(addon)
    outline = [(x, 0) for x in range(-2, 2)] + [(2, y) for y in range(3)]
    outline += [(x, 3) for x in range(2, -2, -1)] + [(-2, y) for y in range(3, 0, -1)]
    _prism(addon, "Gridded", outline)
    verts, _ = _evaluated(addon)
    _assert_same_points(verts, _expected(addon))
    # The back wall's middle base vertex (0, 3, 0) moves straight back by 2: (0, 5, 0) -> z 5.
    _assert_same_points([v for v in verts if abs(v[0]) < TOL and abs(v[1] - 5) < TOL], [(0, 5, 5)])
    # The left wall's base vertex (-2, 1, 0) moves left by 1 only.
    _assert_same_points([v for v in verts if abs(v[0] + 3) < TOL and abs(v[1] - 1) < TOL], [(-3, 1, 1)])


def test_diagonal_wall_matches_reference(addon) -> None:
    """A cut corner with a vertex halfway along it: that vertex moves by exactly k * normal."""
    _top_down(addon)
    _prism(addon, "Cut Corner", [(-2, 0), (2, 0), (2, 1), (1, 2), (0, 3), (-2, 3)])
    verts, _ = _evaluated(addon)
    _assert_same_points(verts, _expected(addon))
    h = 1 / math.sqrt(2)
    k = math.hypot(1.0 * h, 2.0 * h)
    x, y = 1 + k * h, 2 + k * h
    _assert_same_points([v for v in verts if abs(v[0] - x) < TOL and abs(v[1] - y) < TOL], [(x, y, y)])


def test_flush_stack_matches_reference(addon) -> None:
    _top_down(addon)
    _marked_box(addon, "Lower", (-2, 0, 0), (2, 3, 1))
    _marked_box(addon, "Upper", (-2, 0, 1), (0, 3, 2))
    verts, _ = _evaluated(addon)
    expected = _expected(addon)
    _assert_same_points(verts, expected)
    # West wall, front column: the base moves two levels (x -2 -> -4).
    assert any(abs(v[0] + 4) < TOL and abs(v[1]) < TOL and abs(v[2]) < TOL for v in verts)


def test_pit_matches_reference(addon) -> None:
    _top_down(addon)
    _marked_box(addon, "Pit", inward=True)
    verts, _ = _evaluated(addon)
    _assert_same_points(verts, _expected(addon))


def test_direction_90_gives_former_side_walls_the_back_offset(addon) -> None:
    _top_down(addon, direction=math.radians(90))
    _marked_box(addon)  # 4 x 3: after turning, 3 wide and 4 deep
    verts, _ = _evaluated(addon)
    _assert_same_points(verts, _expected(addon))
    # The source's +X wall now faces +Y: its base sits back 2 units (one level of back offset)
    # behind the top's back edge, which is at y = 4 after seating.
    ys = sorted({round(v[1], 4) for v in verts})
    assert ys[-1] == pytest.approx(4 + 2.0)


def test_smooth_shading_does_not_change_the_splay(addon) -> None:
    _top_down(addon)
    ob = _marked_box(addon)
    ob.data.shade_smooth()
    verts, _ = _evaluated(addon)
    _assert_same_points(verts, _expected(addon))


def test_custom_normals_do_not_change_the_splay(addon) -> None:
    _top_down(addon)
    ob = _marked_box(addon)
    mesh = ob.data
    mesh.shade_smooth()
    mesh.normals_split_custom_set([(0.0, 0.0, 1.0)] * len(mesh.loops))  # every corner points up
    verts, _ = _evaluated(addon)
    _assert_same_points(verts, _expected(addon))


def test_prop_vertex_above_terrain_does_not_raise_the_column(addon) -> None:
    _top_down(addon)
    _marked_box(addon)
    # An unmarked prop: a triangle hovering at z = 3 with a corner exactly above the
    # box's front-left corner (-2, 0). Counted as terrain it would make that base 3 levels deep.
    _mesh_object(addon, "Prop", [(-2, 0, 3), (-1, 0, 3), (-2, 1, 3)], [(0, 1, 2)], terrain=False)
    verts, _ = _evaluated(addon)
    _assert_same_points(verts, _expected(addon))
    # The front-left base corner moves one level (x -2 -> -3), not three.
    assert any(abs(v[0] + 3) < TOL and abs(v[1]) < TOL and abs(v[2]) < TOL for v in verts)
    assert not any(abs(v[0] + 5) < TOL for v in verts)


def test_snap_rounds_levels(addon) -> None:
    _top_down(addon, splay_snap=True)
    _marked_box(addon, lo=(-2, 0, 0), hi=(2, 3, 0.9))
    verts, _ = _evaluated(addon)
    _assert_same_points(verts, _expected(addon))
    assert any(abs(v[0] + 3) < TOL for v in verts), "0.9 units snaps to one level: side base at x = -3"

    _settings(addon).splay_snap = False
    _rebuild()
    verts, _ = _evaluated(addon)
    _assert_same_points(verts, _expected(addon))
    assert any(abs(v[0] + 2.9) < TOL for v in verts), "snap off: 0.9 levels"


# --------------------------------------------------------------------------- switched off


def test_splay_off_is_the_plain_shear(addon) -> None:
    _top_down(addon, splay_enabled=False)
    _marked_box(addon)
    verts, _ = _evaluated(addon)
    _assert_same_points(verts, _expected(addon, splayed=False), tol=1e-6)


def test_unmarked_box_is_the_plain_shear(addon) -> None:
    _top_down(addon)
    verts, faces = _box(-2, 2, 0, 3, 0, 1)
    _mesh_object(addon, "Unmarked", verts, faces, terrain=False)
    evaluated, _ = _evaluated(addon)
    _assert_same_points(evaluated, _expected(addon, splayed=False), tol=1e-6)


# --------------------------------------------------------------------------- Edge IDs, attributes, Fit


def test_edge_ids_branch_does_not_tear_and_drops_helper_attributes(addon) -> None:
    splay = addon.core.splay
    _top_down(addon)
    _marked_box(addon, "Lower", (-2, 0, 0), (2, 3, 1))
    _marked_box(addon, "Upper", (-2, 0, 1), (0, 3, 2))
    plain, plain_names = _evaluated(addon)

    mod = _modifier(addon)
    addon.rig.set_modifier_input(mod, "Edge IDs", True)
    addon.rig.set_modifier_input(mod, "Crease Angle", math.radians(30))
    mod.id_data.update_tag()
    split, split_names = _evaluated(addon)

    assert len(split) > len(plain), "Split Edges duplicated vertices"
    assert {tuple(round(c, 4) for c in v) for v in split} == {tuple(round(c, 4) for c in v) for v in plain}
    for names in (plain_names, split_names):
        assert not names & set(addon.rig.REMOVED_ATTRIBUTES), names
        assert {splay.SPLAY_ATTRIBUTE, splay.TERRAIN_ATTRIBUTE} <= set(addon.rig.REMOVED_ATTRIBUTES)


# --------------------------------------------------------------------------- loose parts (details)


def _tetra(centre, size: float = 0.1):
    cx, cy, cz = centre
    verts = [
        (cx - size, cy - size, cz - size),
        (cx + size, cy - size, cz - size),
        (cx, cy + size, cz - size),
        (cx, cy, cz + size),
    ]
    return verts, [(0, 2, 1), (0, 1, 3), (1, 2, 3), (2, 0, 3)]


def _box_with_details(addon, centres, name: str = "Terrain", mesh_id: int = 0, mark_details: bool = True):
    """A marked 4 x 3 x 1 box with one small tetrahedron per centre in the same mesh."""
    splay = addon.core.splay
    verts, faces = _box(-2, 2, 0, 3, 0, 1)
    for centre in centres:
        t_verts, t_faces = _tetra(centre)
        faces += [tuple(len(verts) + i for i in f) for f in t_faces]
        verts += t_verts
    ob = _mesh_object(addon, name, verts, faces, terrain=True)
    mesh = ob.data
    if mark_details:
        attr = mesh.attributes.new(splay.DETAIL_ATTRIBUTE, "BOOLEAN", "POINT")
        attr.data.foreach_set("value", [False] * 8 + [True] * (len(verts) - 8))
    attr = mesh.attributes.new(splay.MESH_ID_ATTRIBUTE, "INT", "POINT")
    attr.data.foreach_set("value", [mesh_id] * len(verts))
    return ob


DETAIL_CENTRES = (
    (-2.1, 1.5, 0.5),  # left wall
    (2.1, 0.6, 0.3),  # right wall
    (0.5, 3.1, 0.5),  # back wall
    (-1.7, 3.1, 0.6),  # back wall near the corner
    (0.3, -0.1, 0.5),  # front wall
    (0.0, 1.5, 1.1),  # top
)


def test_details_follow_their_wall(addon) -> None:
    _box_with_details(addon, DETAIL_CENTRES)
    _top_down(addon, auto_seat=False)
    got, names = _evaluated(addon)
    _assert_same_points(got, _expected(addon))
    assert not names & set(addon.rig.REMOVED_ATTRIBUTES), names
    # The lowest left-wall vertex (z = 0.4) leans out by 1 - 0.4 with the wall; it is not sheared (y, z) alone.
    assert any(np.allclose(p, (-2.2 - 0.6, 1.4, 0.4 + 1.4), atol=TOL) for p in got)
    # A front-wall detail and one on the top stay where they were modelled.
    assert any(np.allclose(p, (0.3, 0.0, 0.4 + 0.0), atol=TOL) for p in got)
    assert any(np.allclose(p, (0.0, 1.5, 1.2 + 1.5), atol=TOL) for p in got)


def test_build_classifies_details_of_a_marked_mesh(addon) -> None:
    """Only the terrain mark is set by hand; Build works out which loose parts are details."""
    ob = _box_with_details(addon, DETAIL_CENTRES, mark_details=False)
    _top_down(addon, auto_seat=False)
    assert addon.core.splay.DETAIL_ATTRIBUTE in ob.data.attributes
    got, _ = _evaluated(addon)
    _assert_same_points(got, _expected(addon))
    assert any(np.allclose(p, (-2.2 - 0.6, 1.4, 0.4 + 1.4), atol=TOL) for p in got)


def test_details_follow_only_their_own_mesh(addon) -> None:
    """A separate ground plane is nearer to a detail at the wall's foot, but is another mesh."""
    _box_with_details(addon, [(-2.2, 1.5, 0.15)], mesh_id=11)
    splay = addon.core.splay
    ground = _mesh_object(
        addon, "Ground", [(-9, -9, 0), (9, -9, 0), (9, 9, 0), (-9, 9, 0)], [(0, 1, 2, 3)], terrain=True
    )
    attr = ground.data.attributes.new(splay.MESH_ID_ATTRIBUTE, "INT", "POINT")
    attr.data.foreach_set("value", [22] * 4)
    _top_down(addon, auto_seat=False)
    got, _ = _evaluated(addon)
    _assert_same_points(got, _expected(addon))
    assert any(np.allclose(p, (-2.3 - 0.95, 1.4, 0.05 + 1.4), atol=TOL) for p in got)


def test_details_follow_with_direction_and_edge_ids(addon) -> None:
    _box_with_details(addon, DETAIL_CENTRES)
    _top_down(addon, direction=math.radians(90))
    plain, _ = _evaluated(addon)
    _assert_same_points(plain, _expected(addon))
    mod = _modifier(addon)
    addon.rig.set_modifier_input(mod, "Edge IDs", True)
    mod.id_data.update_tag()
    split, names = _evaluated(addon)
    assert {tuple(round(c, 4) for c in v) for v in split} == {tuple(round(c, 4) for c in v) for v in plain}
    assert not names & set(addon.rig.REMOVED_ATTRIBUTES), names


# --------------------------------------------------------------------------- walls that step in and out


def _ring_box(rings, x0=-2.0, x1=2.0, y0=0.0, y1=3.0):
    verts, faces = [], []
    for inset, z in rings:
        verts += [
            (x0 + inset, y0 + inset, z),
            (x1 - inset, y0 + inset, z),
            (x1 - inset, y1 - inset, z),
            (x0 + inset, y1 - inset, z),
        ]
    for r in range(len(rings) - 1):
        a, b = 4 * r, 4 * r + 4
        faces += [(a + i, a + (i + 1) % 4, b + (i + 1) % 4, b + i) for i in range(4)]
    last = 4 * (len(rings) - 1)
    faces += [(3, 2, 1, 0), (last, last + 1, last + 2, last + 3)]
    return verts, faces


ZIGZAG = [(0.0, 0.0), (0.1, 0.25), (0.0, 0.5), (0.1, 0.75), (0.0, 1.0)]


@pytest.mark.parametrize("snap", [True, False])
def test_zigzag_wall_leans_evenly(addon, snap) -> None:
    """A wall profile that steps in and out leans as one wall: every ring by its depth below the top."""
    verts, faces = _ring_box(ZIGZAG)
    _mesh_object(addon, "Zigzag", verts, faces, terrain=True)
    _top_down(addon, auto_seat=False, splay_snap=snap)
    got, _ = _evaluated(addon)
    _assert_same_points(got, _expected(addon))
    for inset, z in ZIGZAG:
        share = 1.0 - z
        y = 3.0 - inset + 2.0 * share  # back right corner of this ring
        assert any(np.allclose(p, (2.0 - inset + share, y, z + y), atol=TOL) for p in got), (inset, z)


def test_two_zigzag_duplicates_at_different_heights_do_not_mix(addon) -> None:
    """Linked duplicates share the stored wall ends; each island still finds its own top and base."""
    verts, faces = _ring_box(ZIGZAG)
    first = _mesh_object(addon, "Zigzag", verts, faces, terrain=True)
    second = bpy.data.objects.new("Zigzag High", first.data)
    second.location = (8.0, 0.0, 3.0)
    addon.rig.get_or_create_collection(bpy.context.scene).objects.link(second)
    _top_down(addon, auto_seat=False)
    got, _ = _evaluated(addon)
    _assert_same_points(got, _expected(addon))
    assert any(np.allclose(p, (3.0, 5.0, 5.0), atol=TOL) for p in got)
    assert any(np.allclose(p, (11.0, 5.0, 8.0), atol=TOL) for p in got)


def test_mid_wall_ring_keeps_a_straight_wall_straight(addon) -> None:
    """Snap rounds the wall, not each ring: a ring half way up a 1 level wall moves half as far."""
    verts, faces = _ring_box([(0.0, 0.0), (0.0, 0.5), (0.0, 1.0)])
    _mesh_object(addon, "Ringed", verts, faces, terrain=True)
    _top_down(addon, auto_seat=False)
    got, _ = _evaluated(addon)
    _assert_same_points(got, _expected(addon))
    assert any(np.allclose(p, (-2.5, 0.0, 0.5), atol=TOL) for p in got)


def test_fit_includes_the_splayed_base(addon) -> None:
    _top_down(addon)
    _marked_box(addon)
    assert bpy.ops.oblique_rig.fit_to_content(padding_px=0) == {"FINISHED"}
    # Top 4 wide plus one side level each way: 6 units. Ground line z = 0 up to the back
    # base at y = 3 + 2 (sheared to z = 5): 5 units. Unsplayed it would be 64 x 64.
    assert _settings(addon).last_footprint == "96 x 80 px"
