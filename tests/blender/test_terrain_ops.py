"""oblique_rig.mark_terrain / clear_terrain write or remove the boolean point attribute
``oblique_terrain`` on the mesh data of the selected mesh objects (plan section 7, 12.6)."""

import bpy
import pytest


@pytest.fixture
def terrain_attr(addon) -> str:
    return addon.core.splay.TERRAIN_ATTRIBUTE


def _mesh_object(addon, name: str, mesh=None, select: bool = True):
    if mesh is None:
        verts, faces = addon.core.primitives.cube(2.0)
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(verts, [], faces)
        mesh.update()
    ob = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(ob)
    ob.select_set(select)
    return ob


def _values(mesh, name: str) -> list[bool]:
    attr = mesh.attributes[name]
    return [item.value for item in attr.data]


class _Recorder:
    """Stands in for the operator instance so execute() reports can be read headless."""

    def __init__(self) -> None:
        self.reports: list[tuple[set[str], str]] = []

    def report(self, kind: set[str], message: str) -> None:
        self.reports.append((kind, message))


def _execute(op_cls) -> tuple[set[str], list[tuple[set[str], str]]]:
    recorder = _Recorder()
    result = op_cls.execute(recorder, bpy.context)
    return result, recorder.reports


def test_terrain_attribute_name(addon) -> None:
    assert addon.core.splay.TERRAIN_ATTRIBUTE == "oblique_terrain"


def test_mark_writes_a_boolean_point_attribute_all_true(addon, terrain_attr) -> None:
    ob = _mesh_object(addon, "Ground")
    assert bpy.ops.oblique_rig.mark_terrain() == {"FINISHED"}
    attr = ob.data.attributes.get(terrain_attr)
    assert attr is not None
    assert (attr.domain, attr.data_type) == ("POINT", "BOOLEAN")
    assert len(attr.data) == len(ob.data.vertices)
    assert all(_values(ob.data, terrain_attr))


def test_mark_only_touches_selected_meshes(addon, terrain_attr) -> None:
    picked = _mesh_object(addon, "Picked")
    other = _mesh_object(addon, "Other", select=False)
    assert bpy.ops.oblique_rig.mark_terrain() == {"FINISHED"}
    assert terrain_attr in picked.data.attributes
    assert terrain_attr not in other.data.attributes


def test_mark_again_sets_every_vertex(addon, terrain_attr) -> None:
    """Geometry added after marking reads false; marking again makes all of it terrain."""
    ob = _mesh_object(addon, "Ground")
    bpy.ops.oblique_rig.mark_terrain()
    ob.data.attributes[terrain_attr].data[0].value = False
    assert bpy.ops.oblique_rig.mark_terrain() == {"FINISHED"}
    assert all(_values(ob.data, terrain_attr))


def test_mark_replaces_an_attribute_of_the_wrong_type(addon, terrain_attr) -> None:
    ob = _mesh_object(addon, "Ground")
    ob.data.attributes.new(terrain_attr, "FLOAT", "FACE")
    assert bpy.ops.oblique_rig.mark_terrain() == {"FINISHED"}
    attr = ob.data.attributes[terrain_attr]
    assert (attr.domain, attr.data_type) == ("POINT", "BOOLEAN")
    assert all(_values(ob.data, terrain_attr))


def test_clear_removes_the_attribute(addon, terrain_attr) -> None:
    ob = _mesh_object(addon, "Ground")
    bpy.ops.oblique_rig.mark_terrain()
    assert bpy.ops.oblique_rig.clear_terrain() == {"FINISHED"}
    assert terrain_attr not in ob.data.attributes


def test_clear_on_an_unmarked_mesh_is_harmless(addon, terrain_attr) -> None:
    ob = _mesh_object(addon, "Ground")
    assert bpy.ops.oblique_rig.clear_terrain() == {"FINISHED"}
    assert terrain_attr not in ob.data.attributes


def test_clear_only_touches_selected_meshes(addon, terrain_attr) -> None:
    picked = _mesh_object(addon, "Picked")
    other = _mesh_object(addon, "Other")
    bpy.ops.oblique_rig.mark_terrain()
    other.select_set(False)
    bpy.ops.oblique_rig.clear_terrain()
    assert terrain_attr not in picked.data.attributes
    assert terrain_attr in other.data.attributes


@pytest.mark.parametrize("idname", ["mark_terrain", "clear_terrain"])
def test_poll_needs_a_selected_mesh(addon, make_object, idname) -> None:
    op = getattr(bpy.ops.oblique_rig, idname)
    assert op.poll() is False
    make_object("Empty")
    assert op.poll() is False, "an empty alone is nothing to mark"
    _mesh_object(addon, "Ground")
    assert op.poll() is True


@pytest.mark.parametrize("idname", ["mark_terrain", "clear_terrain"])
def test_poll_needs_object_mode(addon, idname) -> None:
    ob = _mesh_object(addon, "Ground")
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        assert bpy.context.mode == "EDIT_MESH"
        assert getattr(bpy.ops.oblique_rig, idname).poll() is False
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
    assert getattr(bpy.ops.oblique_rig, idname).poll() is True


def test_mark_report_counts_meshes_and_skipped(addon, make_object) -> None:
    _mesh_object(addon, "A")
    _mesh_object(addon, "B")
    make_object("Empty")
    result, reports = _execute(addon.operators.OBLIQUE_RIG_OT_mark_terrain)
    assert result == {"FINISHED"}
    assert reports == [({"INFO"}, "Marked 2 meshes as terrain; skipped 1 object that is not a mesh")]


def test_clear_report(addon, make_object) -> None:
    _mesh_object(addon, "A")
    make_object("Empty 1")
    make_object("Empty 2")
    result, reports = _execute(addon.operators.OBLIQUE_RIG_OT_clear_terrain)
    assert result == {"FINISHED"}
    assert reports == [({"INFO"}, "Cleared terrain on 1 mesh; skipped 2 objects that are not meshes")]


def test_report_without_skipped_objects(addon) -> None:
    _mesh_object(addon, "A")
    _, reports = _execute(addon.operators.OBLIQUE_RIG_OT_mark_terrain)
    assert reports == [({"INFO"}, "Marked 1 mesh as terrain")]


def test_report_respects_the_preference(addon) -> None:
    addon.preferences.get_prefs(bpy.context).report_results = False
    try:
        _mesh_object(addon, "A")
        result, reports = _execute(addon.operators.OBLIQUE_RIG_OT_mark_terrain)
    finally:
        addon.preferences.get_prefs(bpy.context).report_results = True
    assert result == {"FINISHED"}
    assert reports == []


def test_non_mesh_selection_is_skipped(addon, make_object, terrain_attr) -> None:
    ground = _mesh_object(addon, "Ground")
    empty = make_object("Empty")
    assert bpy.ops.oblique_rig.mark_terrain() == {"FINISHED"}
    assert empty.type == "EMPTY"
    assert terrain_attr in ground.data.attributes


def test_only_non_meshes_cancels_with_a_warning(addon, make_object) -> None:
    """poll() keeps the button off; execute() still guards when called directly."""
    make_object("Empty")
    result, reports = _execute(addon.operators.OBLIQUE_RIG_OT_mark_terrain)
    assert result == {"CANCELLED"}
    assert [kind for kind, _ in reports] == [{"WARNING"}]


def test_shared_mesh_is_counted_once(addon, terrain_attr) -> None:
    first = _mesh_object(addon, "Linked A")
    second = _mesh_object(addon, "Linked B", mesh=first.data)
    assert first.data == second.data
    result, reports = _execute(addon.operators.OBLIQUE_RIG_OT_mark_terrain)
    assert result == {"FINISHED"}
    assert reports == [({"INFO"}, "Marked 1 mesh as terrain")]
    assert terrain_attr in second.data.attributes


def test_linked_duplicate_shares_the_mark(addon, terrain_attr) -> None:
    first = _mesh_object(addon, "Linked A")
    second = _mesh_object(addon, "Linked B", mesh=first.data, select=False)
    bpy.ops.oblique_rig.mark_terrain()
    assert terrain_attr in second.data.attributes


def test_rig_objects_are_skipped(addon, terrain_attr) -> None:
    assert bpy.ops.oblique_rig.build() == {"FINISHED"}
    for ob in bpy.context.selected_objects:
        ob.select_set(False)
    rig_object = bpy.data.objects[addon.core.oblique.RIG_NAME]
    rig_object.select_set(True)
    ground = _mesh_object(addon, "Ground")
    result, reports = _execute(addon.operators.OBLIQUE_RIG_OT_mark_terrain)
    assert result == {"FINISHED"}
    assert terrain_attr not in rig_object.data.attributes
    assert terrain_attr in ground.data.attributes
    assert reports == [({"INFO"}, "Marked 1 mesh as terrain; skipped 1 object that is not a mesh")]


@pytest.mark.parametrize("idname", ["mark_terrain", "clear_terrain"])
def test_rebuilds_when_a_rig_exists(addon, monkeypatch, idname) -> None:
    calls = []
    monkeypatch.setattr(addon.operators, "build_from_settings", lambda context: calls.append(1))
    _mesh_object(addon, "Ground")
    assert getattr(bpy.ops.oblique_rig, idname)() == {"FINISHED"}
    assert calls == [], "no rig: nothing to rebuild"
    bpy.data.objects.new(addon.core.oblique.RIG_NAME, None)
    assert getattr(bpy.ops.oblique_rig, idname)() == {"FINISHED"}
    assert calls == [1]


def test_mark_with_a_real_rig_finishes(addon, terrain_attr) -> None:
    assert bpy.ops.oblique_rig.build() == {"FINISHED"}
    ground = _mesh_object(addon, "Ground")
    addon.rig.get_or_create_collection(bpy.context.scene).objects.link(ground)
    assert bpy.ops.oblique_rig.mark_terrain() == {"FINISHED"}
    assert terrain_attr in ground.data.attributes
    assert bpy.ops.oblique_rig.clear_terrain() == {"FINISHED"}
    assert terrain_attr not in ground.data.attributes


def test_operator_metadata(addon) -> None:
    for cls in (addon.operators.OBLIQUE_RIG_OT_mark_terrain, addon.operators.OBLIQUE_RIG_OT_clear_terrain):
        assert cls.bl_options == {"REGISTER", "UNDO"}
        assert cls.bl_label and cls.bl_description
    assert addon.operators.OBLIQUE_RIG_OT_mark_terrain.bl_label == "Mark as Terrain"
    assert addon.operators.OBLIQUE_RIG_OT_clear_terrain.bl_label == "Clear Terrain"


# --------------------------------------------------------------------------- loose parts (details)


def _box_and_tetra_mesh(addon, name: str = "Cliff"):
    """A 2 m cube and a small tetrahedron on its left wall, as two loose parts of one mesh."""
    verts, faces = addon.core.primitives.cube(2.0)
    verts, faces = list(verts), list(faces)
    n = len(verts)
    verts += [(-1.2, -0.1, 0.9), (-1.0, -0.1, 0.9), (-1.1, 0.1, 0.9), (-1.1, 0.0, 1.1)]
    faces += [(n, n + 2, n + 1), (n, n + 1, n + 3), (n + 1, n + 2, n + 3), (n + 2, n, n + 3)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    return mesh, n


def test_mark_flags_loose_parts_as_details(addon, terrain_attr) -> None:
    splay = addon.core.splay
    mesh, n = _box_and_tetra_mesh(addon)
    _mesh_object(addon, "Cliff", mesh)
    assert bpy.ops.oblique_rig.mark_terrain() == {"FINISHED"}
    assert all(_values(mesh, terrain_attr))
    attr = mesh.attributes[splay.DETAIL_ATTRIBUTE]
    assert (attr.domain, attr.data_type) == ("POINT", "BOOLEAN")
    assert _values(mesh, splay.DETAIL_ATTRIBUTE) == [False] * n + [True] * 4


def test_mark_gives_each_mesh_its_own_id(addon) -> None:
    splay = addon.core.splay
    a = _mesh_object(addon, "A")
    b = _mesh_object(addon, "B")
    assert bpy.ops.oblique_rig.mark_terrain() == {"FINISHED"}
    ids = []
    for ob in (a, b):
        attr = ob.data.attributes[splay.MESH_ID_ATTRIBUTE]
        assert (attr.domain, attr.data_type) == ("POINT", "INT")
        values = set(_values(ob.data, splay.MESH_ID_ATTRIBUTE))
        assert len(values) == 1
        ids.append(values.pop())
    assert ids[0] != ids[1]


def test_clear_removes_the_detail_marks_too(addon, terrain_attr) -> None:
    splay = addon.core.splay
    mesh, _ = _box_and_tetra_mesh(addon)
    _mesh_object(addon, "Cliff", mesh)
    bpy.ops.oblique_rig.mark_terrain()
    assert bpy.ops.oblique_rig.clear_terrain() == {"FINISHED"}
    for name in (terrain_attr, splay.DETAIL_ATTRIBUTE, splay.MESH_ID_ATTRIBUTE):
        assert name not in mesh.attributes


def test_build_refreshes_the_marks_of_marked_source_meshes(addon, terrain_attr) -> None:
    """Geometry added after marking becomes terrain (or a detail) on the next Build / Update Rig."""
    splay = addon.core.splay
    mesh, n = _box_and_tetra_mesh(addon)
    ob = _mesh_object(addon, "Cliff", mesh)
    addon.rig.get_or_create_collection(bpy.context.scene).objects.link(ob)
    bpy.ops.oblique_rig.mark_terrain()
    for name in (terrain_attr, splay.DETAIL_ATTRIBUTE):  # as if the tetrahedron had been added later
        for item in list(mesh.attributes[name].data)[n:]:
            item.value = False
    unmarked = _mesh_object(addon, "Prop")
    addon.rig.get_or_create_collection(bpy.context.scene).objects.link(unmarked)

    assert bpy.ops.oblique_rig.build() == {"FINISHED"}
    assert all(_values(mesh, terrain_attr))
    assert _values(mesh, splay.DETAIL_ATTRIBUTE) == [False] * n + [True] * 4
    assert terrain_attr not in unmarked.data.attributes, "Build never marks a mesh on its own"


def test_marked_detail_leans_with_its_wall_in_the_rig(addon) -> None:
    """End to end: Mark as Terrain on a cube with a loose part, top-down splay, built rig."""
    mesh, _ = _box_and_tetra_mesh(addon)
    ob = _mesh_object(addon, "Cliff", mesh)
    ob.location = (0.0, 1.0, 1.0)  # the cube spans x -1..1, y 0..2, z 0..2
    addon.rig.get_or_create_collection(bpy.context.scene).objects.link(ob)
    settings = addon.properties.get_settings(bpy.context)
    settings.depth_px_right = 0
    settings.depth_px_up = 16
    settings.splay_enabled = True
    settings.splay_level_height = 2.0
    settings.auto_seat = False
    assert bpy.ops.oblique_rig.mark_terrain() == {"FINISHED"}
    assert bpy.ops.oblique_rig.build() == {"FINISHED"}

    rig = bpy.data.objects[addon.core.oblique.RIG_NAME]
    rig_eval = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
    points = [tuple(v.co) for v in rig_eval.to_mesh().vertices]
    rig_eval.to_mesh_clear()
    # The tetrahedron's apex: source (-1.1, 1.0, 2.1) is above the wall top (levels 0 there), its
    # base corner (-1.2, 0.9, 1.9) is 0.1 below it: 0.05 levels of 1 tile to the left, then sheared.
    assert any(abs(x + 1.1) < 1e-4 and abs(y - 1.0) < 1e-4 and abs(z - 3.1) < 1e-4 for x, y, z in points)
    assert any(abs(x + 1.25) < 1e-4 and abs(y - 0.9) < 1e-4 and abs(z - 2.8) < 1e-4 for x, y, z in points)


def test_mark_stores_each_vertex_wall_top_and_base(addon) -> None:
    splay = addon.core.splay
    ob = _mesh_object(addon, "Ground")  # a cube: every vertex is the top or the base of its edge
    assert bpy.ops.oblique_rig.mark_terrain() == {"FINISHED"}
    mesh = ob.data
    z = [v.co.z for v in mesh.vertices]
    top = _values(mesh, splay.WALL_TOP_ATTRIBUTE)
    base = _values(mesh, splay.WALL_BASE_ATTRIBUTE)
    for name in (splay.WALL_TOP_ATTRIBUTE, splay.WALL_BASE_ATTRIBUTE):
        assert (mesh.attributes[name].domain, mesh.attributes[name].data_type) == ("POINT", "INT")
    assert all(z[t] == max(z) for t in top)
    assert all(z[b] == min(z) for b in base)
    assert bpy.ops.oblique_rig.clear_terrain() == {"FINISHED"}
    assert not set(addon.rig.TERRAIN_MARKS) & {a.name for a in mesh.attributes}
