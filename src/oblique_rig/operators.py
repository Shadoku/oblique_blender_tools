# SPDX-License-Identifier: GPL-3.0-or-later
"""Operators: read settings, call ``rig``, report. No maths here.

Conventions enforced by tools/lint_addon.py: class names contain ``_OT_``,
``bl_idname`` is ``category.name`` in lowercase with exactly one dot, and every
operator declares ``bl_options`` (``'UNDO'`` whenever it writes to bpy.data).
Never keep references to Blender objects between calls; store names.
"""

from collections.abc import Callable

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty
from bpy.types import Operator

from . import rig
from .core import oblique, overlap, presets, splay
from .preferences import get_prefs
from .properties import get_settings, params_from_settings

MAX_WARNINGS = 3


def _params_problem(params: oblique.RigParams) -> str | None:
    """Why the rig cannot be built from ``params``, or None."""
    try:
        params.shear()
        params.modifier_inputs()
        resolution_x, resolution_y = params.effective_resolution()
        oblique.ortho_scale(resolution_x, resolution_y, params.pixels_per_unit)
    except ValueError as exc:
        return str(exc)
    return None


def build_from_settings(context: bpy.types.Context) -> bpy.types.Object:
    """Build the rig from the scene settings and refresh the footprint and depth-sort readouts."""
    settings = get_settings(context)
    params = params_from_settings(settings)
    rig_object = rig.build(context.scene, params)
    update_readouts(context, rig_object)
    return rig_object


def update_readouts(context: bpy.types.Context, rig_object: bpy.types.Object) -> None:
    """Write ``last_footprint`` and ``last_warnings`` from the rig as it is now."""
    settings = get_settings(context)
    params = params_from_settings(settings)
    bounds = rig.sheared_bounds(context.scene, rig_object)
    if bounds is None:
        settings.last_footprint = ""
    else:
        width, height = oblique.footprint_px(bounds[0], bounds[1], params.pixels_per_unit)
        settings.last_footprint = f"{width} x {height} px"
    pairs = overlap.overlapping_pairs(rig.source_boxes(context.scene))
    settings.last_warnings = overlap.format_warnings(pairs[:MAX_WARNINGS])


class OBLIQUE_RIG_OT_build(Operator):
    bl_idname = "oblique_rig.build"
    bl_label = "Build Rig"
    bl_description = (
        "Create or update the oblique rig: source collection, shear node group, rig object, "
        "orthographic camera, sun and render settings"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        problem = _params_problem(params_from_settings(get_settings(context)))
        if problem is not None:
            cls.poll_message_set(problem)
            return False
        return True

    def execute(self, context: bpy.types.Context) -> set[str]:
        params = params_from_settings(get_settings(context))
        problem = _params_problem(params)
        if problem is not None:
            self.report({"WARNING"}, problem)
            return {"CANCELLED"}

        build_from_settings(context)
        settings = get_settings(context)
        if settings.last_warnings:
            self.report({"WARNING"}, f"Depth order unclear: {settings.last_warnings}")
        if get_prefs(context).report_results:
            a, b = params.shear()
            ppu = params.pixels_per_unit
            self.report(
                {"INFO"},
                f"Oblique rig ready: put assets in '{oblique.SOURCE_COLLECTION}'; "
                f"one unit of depth moves {a * ppu:.2f} px right, {b * ppu:.2f} px up",
            )
        return {"FINISHED"}


class OBLIQUE_RIG_OT_add_selected(Operator):
    bl_idname = "oblique_rig.add_selected"
    bl_label = "Add Selected to Rig"
    bl_description = (
        "Move the selected objects into the source collection and place each with its "
        "front edge at y = 0 and its origin at z = 0"
    )
    bl_options = {"REGISTER", "UNDO"}

    ground_by_bottom: BoolProperty(
        name="Bottom to Floor",
        description="Put the object's lowest point at z = 0 instead of its origin",
        default=False,
    )

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        if rig.eligible_for_rig(context.selected_objects):
            return True
        cls.poll_message_set("Select the objects to add (the rig's own objects do not count)")
        return False

    def execute(self, context: bpy.types.Context) -> set[str]:
        depsgraph = context.evaluated_depsgraph_get()
        added = rig.add_objects_to_rig(
            context.scene, list(context.selected_objects), depsgraph, self.ground_by_bottom
        )
        if get_prefs(context).report_results:
            self.report({"INFO"}, f"Added to '{oblique.SOURCE_COLLECTION}': {', '.join(added)}")
        return {"FINISHED"}


class OBLIQUE_RIG_OT_add_calibration(Operator):
    bl_idname = "oblique_rig.add_calibration"
    bl_label = "Add Calibration Objects"
    bl_description = "Add two unit cubes 3 units apart in depth and a floor to the source collection"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        created = rig.add_calibration_objects(context.scene)
        if get_prefs(context).report_results:
            message = f"Added {', '.join(created)}" if created else "Calibration objects already exist"
            self.report({"INFO"}, message)
        return {"FINISHED"}


class OBLIQUE_RIG_OT_remove(Operator):
    bl_idname = "oblique_rig.remove"
    bl_label = "Remove Rig"
    bl_description = (
        "Delete the rig object, camera, sun and node group (optionally the source collection too)"
    )
    bl_options = {"REGISTER", "UNDO"}

    include_source: BoolProperty(
        name="Include Source",
        description=(
            "Also delete the source collection and the calibration objects; "
            "other objects in it are moved to the scene collection"
        ),
        default=False,
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        removed = rig.remove_rig(context.scene, include_source=self.include_source)
        if get_prefs(context).report_results:
            self.report({"INFO"}, f"Removed {', '.join(removed)}" if removed else "Nothing to remove")
        return {"FINISHED"}


class OBLIQUE_RIG_OT_fit_to_content(Operator):
    bl_idname = "oblique_rig.fit_to_content"
    bl_label = "Fit to Content"
    bl_description = (
        "Set the resolution and camera so the sheared content fills the frame with the given padding, "
        "then rebuild the rig"
    )
    bl_options = {"REGISTER", "UNDO"}

    padding_px: IntProperty(
        name="Padding",
        description="Empty pixels kept around the content on every side",
        default=4,
        min=0,
        soft_max=64,
        subtype="PIXEL",
    )

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        if bpy.data.objects.get(oblique.RIG_NAME) is None:
            cls.poll_message_set("Build the rig first")
            return False
        if _params_problem(params_from_settings(get_settings(context))) is not None:
            cls.poll_message_set("The projection settings are invalid")
            return False
        return True

    def execute(self, context: bpy.types.Context) -> set[str]:
        settings = get_settings(context)
        rig_object = rig.build(context.scene, params_from_settings(settings))
        bounds = rig.sheared_bounds(context.scene, rig_object)
        if bounds is None:
            self.report({"WARNING"}, f"Nothing to fit: '{oblique.SOURCE_COLLECTION}' has no geometry")
            return {"CANCELLED"}

        fit = oblique.fit_frame(bounds[0], bounds[1], settings.pixels_per_unit, self.padding_px)
        settings.resolution_x = fit.resolution_x
        settings.resolution_y = fit.resolution_y
        settings.camera_offset_x = fit.camera_offset_x
        settings.camera_height_fraction = fit.camera_height_fraction
        build_from_settings(context)
        if settings.last_warnings:
            self.report({"WARNING"}, f"Depth order unclear: {settings.last_warnings}")
        if get_prefs(context).report_results:
            self.report(
                {"INFO"},
                f"Frame {fit.resolution_x} x {fit.resolution_y} px; content {settings.last_footprint}",
            )
        return {"FINISHED"}


def _slope_preset_items() -> list[tuple[str, str, str]]:
    return [
        (
            p.identifier,
            p.label,
            f"One unit of depth moves {p.right_per_unit:g} x Pixels per Unit right "
            f"and {p.up_per_unit:g} x up, in whole pixels",
        )
        for p in presets.SLOPE_PRESETS
    ]


def _resolution_preset_items() -> list[tuple[str, str, str]]:
    return [
        (p.identifier, p.label, f"{p.resolution_x} x {p.resolution_y} px at {p.pixels_per_unit} px per unit")
        for p in presets.RESOLUTION_PRESETS
    ]


def _rebuild_if_rig_exists(context: bpy.types.Context) -> None:
    if bpy.data.objects.get(oblique.RIG_NAME) is None:
        return
    if _params_problem(params_from_settings(get_settings(context))) is None:
        build_from_settings(context)


class OBLIQUE_RIG_OT_apply_slope_preset(Operator):
    bl_idname = "oblique_rig.apply_slope_preset"
    bl_label = "Apply Slope Preset"
    bl_description = "Set whole-pixel depth steps from a common oblique slope and rebuild an existing rig"
    bl_options = {"REGISTER", "UNDO"}
    bl_property = "preset"

    preset: EnumProperty(name="Preset", description="Oblique slope", items=_slope_preset_items())

    def execute(self, context: bpy.types.Context) -> set[str]:
        settings = get_settings(context)
        right, up = presets.slope_preset(self.preset, settings.pixels_per_unit)
        settings.depth_px_right = right
        settings.depth_px_up = up
        settings.pixel_exact = True
        _rebuild_if_rig_exists(context)
        if get_prefs(context).report_results:
            self.report({"INFO"}, f"One unit of depth: {right} px right, {up} px up")
        return {"FINISHED"}


class OBLIQUE_RIG_OT_apply_resolution_preset(Operator):
    bl_idname = "oblique_rig.apply_resolution_preset"
    bl_label = "Apply Resolution Preset"
    bl_description = (
        "Set the frame size and pixels per unit from a common sprite canvas and rebuild an existing rig"
    )
    bl_options = {"REGISTER", "UNDO"}
    bl_property = "preset"

    preset: EnumProperty(name="Preset", description="Frame size", items=_resolution_preset_items())

    def execute(self, context: bpy.types.Context) -> set[str]:
        settings = get_settings(context)
        _, _, resolution_x, resolution_y, pixels_per_unit = presets.resolution_preset(self.preset)
        settings.resolution_x = resolution_x
        settings.resolution_y = resolution_y
        settings.pixels_per_unit = pixels_per_unit
        _rebuild_if_rig_exists(context)
        if get_prefs(context).report_results:
            self.report(
                {"INFO"}, f"Frame {resolution_x} x {resolution_y} px at {pixels_per_unit} px per unit"
            )
        return {"FINISHED"}


def _plural(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def _selected_meshes(context: bpy.types.Context) -> tuple[list[bpy.types.Mesh], int]:
    """The distinct mesh datablocks of the selected mesh objects, and how many objects were skipped.

    Linked duplicates share one mesh, which is listed (and counted) once. Objects
    that are not meshes, and the rig's own objects, count as skipped.
    """
    meshes: dict[int, bpy.types.Mesh] = {}
    eligible = rig.eligible_for_rig(context.selected_objects)
    skipped = len(context.selected_objects) - len(eligible)
    for ob in eligible:
        if ob.type != "MESH" or ob.data is None:
            skipped += 1
            continue
        meshes.setdefault(ob.data.as_pointer(), ob.data)
    return list(meshes.values()), skipped


def _terrain_poll(cls: type, context: bpy.types.Context, verb: str) -> bool:
    # Mesh attributes written in Edit Mode are overwritten when the edit mesh is written back.
    if context.mode != "OBJECT":
        cls.poll_message_set("Switch to Object Mode")
        return False
    if not _selected_meshes(context)[0]:
        cls.poll_message_set(f"Select the mesh objects to {verb}")
        return False
    return True


def _mark_mesh(mesh: bpy.types.Mesh) -> None:
    attr = mesh.attributes.get(splay.TERRAIN_ATTRIBUTE)
    if attr is not None and (attr.domain, attr.data_type) != ("POINT", "BOOLEAN"):
        mesh.attributes.remove(attr)
        attr = None
    if attr is None:
        attr = mesh.attributes.new(splay.TERRAIN_ATTRIBUTE, "BOOLEAN", "POINT")
    attr.data.foreach_set("value", [True] * len(attr.data))
    mesh.update()


def _clear_mesh(mesh: bpy.types.Mesh) -> None:
    attr = mesh.attributes.get(splay.TERRAIN_ATTRIBUTE)
    if attr is not None:
        mesh.attributes.remove(attr)
        mesh.update()


def _run_terrain(
    op: Operator, context: bpy.types.Context, apply: Callable[[bpy.types.Mesh], None], verb: str, done: str
) -> set[str]:
    """Shared execute() of Mark as Terrain and Clear Terrain: apply, rebuild, report."""
    meshes, skipped = _selected_meshes(context)
    if not meshes:
        op.report({"WARNING"}, f"No mesh selected to {verb}")
        return {"CANCELLED"}
    for mesh in meshes:
        apply(mesh)
    _rebuild_if_rig_exists(context)
    if get_prefs(context).report_results:
        message = done.format(meshes=_plural(len(meshes), "mesh", "meshes"))
        if skipped:
            message += "; skipped " + _plural(
                skipped, "object that is not a mesh", "objects that are not meshes"
            )
        op.report({"INFO"}, message)
    return {"FINISHED"}


class OBLIQUE_RIG_OT_mark_terrain(Operator):
    bl_idname = "oblique_rig.mark_terrain"
    bl_label = "Mark as Terrain"
    bl_description = (
        "Mark the selected meshes as terrain, so Tile Splay moves their wall bases; "
        "linked duplicates share the mark. Run again after adding geometry"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return _terrain_poll(cls, context, "mark")

    def execute(self, context: bpy.types.Context) -> set[str]:
        return _run_terrain(self, context, _mark_mesh, "mark", "Marked {meshes} as terrain")


class OBLIQUE_RIG_OT_clear_terrain(Operator):
    bl_idname = "oblique_rig.clear_terrain"
    bl_label = "Clear Terrain"
    bl_description = "Remove the terrain mark from the selected meshes, so Tile Splay leaves them alone"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return _terrain_poll(cls, context, "clear")

    def execute(self, context: bpy.types.Context) -> set[str]:
        return _run_terrain(self, context, _clear_mesh, "clear", "Cleared terrain on {meshes}")


CLASSES: tuple[type, ...] = (
    OBLIQUE_RIG_OT_build,
    OBLIQUE_RIG_OT_add_selected,
    OBLIQUE_RIG_OT_add_calibration,
    OBLIQUE_RIG_OT_remove,
    OBLIQUE_RIG_OT_fit_to_content,
    OBLIQUE_RIG_OT_apply_slope_preset,
    OBLIQUE_RIG_OT_apply_resolution_preset,
    OBLIQUE_RIG_OT_mark_terrain,
    OBLIQUE_RIG_OT_clear_terrain,
)
