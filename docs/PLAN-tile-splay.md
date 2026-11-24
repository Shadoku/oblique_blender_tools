# Plan: Oblique Rig 0.4, top-down tile blockout (wall splay)

Status: proposed, 2026-11-23. Not implemented. Builds on 0.3.0 (commit
`51f5fe7`, branch `claude/trusting-hawking-4d4ihj`). Save as
`docs/PLAN-tile-splay.md`.

The maths in section 2 and the reference code in section 5 were run in plain
numpy on the shapes listed in section 3. **Nothing Blender-side was verified**:
the planning session had no Blender binary. Every node name and node recipe in
section 6 is a proposal to confirm with `python3 tools/check.py` on 4.5 and 5.2.

## 1. What this adds

Top-down tilesets in the style of the 2D Zelda games draw terrain with a
convention that no single projection produces: tops and front walls come from
a top-down oblique projection, but side walls are given width and back walls
are shown as a thin rim. This release lets the rig produce that from an
ordinary blockout (a grid with regions extruded up), so cliffs, plateaus, pits
and rooms render as tile-aligned guides.

| # | Feature | Why |
|---|---|---|
| A | Top-down slope preset | The base projection. Already reachable by hand (Depth Right 0, Depth Up = Pixels per Unit; `depth_px_right` has `min=0`), but there is no preset. |
| B | Wall splay in the node group | The multi-view part: wall bases move outward in plan so side and back walls become visible. |
| C | Whole-tile sizing | Wall widths are set in tiles per level, so a blockout on the unit grid gives walls that land on tile boundaries. |
| D | Terrain marking | Splay applies only to meshes marked as terrain. Props (trees, houses, characters) stay plain oblique. |

## 2. The rule

One tile is one Blender unit, which is `pixels_per_unit` pixels. This is the
grid the rig already uses (Grid Step 1).

**Levels.** A vertex's *drop* is the height of wall above it: the z of the
highest terrain vertex at the same plan position (same x and y), minus its own
z. Its *levels* are `drop / level_height`, rounded half up to a whole number
when Snap to Levels is on. Vertices on top of a wall have 0 levels and never
move, so every top keeps the shape and position it was modelled with.

**Offsets.** Each wall base moves outward in plan, along the wall's horizontal
normal, by an amount that depends on which way the wall faces (after Turn and
Seat, so relative to the camera):

| Wall faces | Plan offset per level (units) | On screen per level |
|---|---|---|
| Front (-Y, toward the camera) | 0 | wall is `level_height` tiles tall (from the shear, unchanged) |
| Side (+X or -X) | `side_tiles` | wall is `side_tiles` tiles wide |
| Back (+Y) | `(level_height + back_tiles) / b` | a rim `back_tiles` tiles tall above the top's back edge |

`b` is the depth-up shear (`depth_px_up / pixels_per_unit`). The back offset
has to first clear the top edge on screen (`level_height / b`) and then show
the rim (`back_tiles / b`).

For walls facing in between (rounded cliffs) the offset along the normal
`(hx, hy)` is `hypot(side * hx, back * max(hy, 0))`. A round plateau's base is
then its top outline grown by half an ellipse.

**Corners.** A vertex shared by walls facing different ways takes the one
offset that satisfies all of them: least squares over its adjacent wall faces
of `offset . normal = k(normal)`. A box corner between a side wall and a back
wall moves by exactly `(side, back)`; a vertex on a straight wall moves by
exactly `k * normal`.

**Whole tiles.** Wall sizes on screen are whole tiles when all of these hold:

- Depth Right is 0 and Depth Up is greater than 0 (the top-down preset).
- `level_height` is a whole number of units.
- Wall heights are multiples of `level_height`. With Snap to Levels on, side
  and back walls are whole tiles even when a height is slightly off; the front
  wall always shows its true height.

With Depth Right above 0 the splay still works, but the back offset also
shifts the base sideways by `a * offset`, which is not guaranteed to be whole
pixels.

Inward-facing walls need no special case. A pit's walls splay into the pit,
which gives the room interiors of A Link to the Past.

## 3. Verified in numpy (16 px per unit, Depth Up 16, level height 1, side 1, back 1)

| Shape | Result |
|---|---|
| Box 4 x 3 x 1 | Top unchanged. Base corners move by (-1, 0), (1, 0), (-1, 2), (1, 2). On screen: top 64 x 48 px, side walls 16 px wide, front wall 16 px tall, back rim 16 px. |
| Two boxes stacked flush on the west side (z 0 to 1, z 1 to 2) | West wall: base -2, middle -1, top 0. The lower box's own top edge on the other sides stays put. |
| Pit (box with inward normals) | Bottom corners move into the pit by the same amounts. |
| Cylinder, 24 sides | Smooth base. Front half shifts by exactly +-1 in x. See the front-vertex artefact in section 9. |

## 4. Settings

New in `properties.py` (group `OBLIQUE_RIG_Settings`) and mirrored as plain
fields on `core.oblique.RigParams`:

| Property | Type | Default | Limits | Panel name |
|---|---|---|---|---|
| `splay_enabled` | Bool | False | | Tile Splay |
| `splay_level_height` | Float | 1.0 | min 0.0625 | Level Height |
| `splay_side_tiles` | Int | 1 | 0 to 8 | Side Tiles |
| `splay_back_tiles` | Int | 1 | 0 to 8 | Back Tiles |
| `splay_snap` | Bool | True | | Snap to Levels |

`RigParams.splay() -> tuple[float, float]` returns the plan offsets per level
`(side, back)`: `(splay_side_tiles, (splay_level_height + splay_back_tiles) / b)`.
It returns `(0.0, 0.0)` when `splay_enabled` is off or `b` is 0. Zero offsets
are how the node group is switched off; there is no separate enable socket.

`presets.py`: add `SlopePreset("TOP_DOWN", "Top-down", 0.0, 1.0)`.

## 5. Core: `src/oblique_rig/core/splay.py`

The reference the node group is tested against. This is the code that produced
section 3; add type hints and docstring detail to match the other core modules.

```python
import numpy as np

TERRAIN_ATTRIBUTE = "oblique_terrain"   # BOOLEAN, POINT, on source meshes
SPLAY_ATTRIBUTE = "oblique_splay"       # FLOAT_VECTOR, POINT, inside the node group only
RANK_EPS = 1e-4   # det(A) < RANK_EPS * trace(A)^2 -> the walls at this vertex are parallel
XY_QUANT = 1e-4   # vertices closer than this in plan belong to one wall column


def splay_offsets(verts, faces, side, back, level_height, snap=True, terrain=None):
    """Per-vertex (dx, dy) in units. side / back: plan offset per level."""
    verts = np.asarray(verts, float)
    n = len(verts)
    terrain = np.ones(n, bool) if terrain is None else np.asarray(terrain, bool)

    # column top: highest terrain vertex sharing this vertex's plan position
    keys = [tuple(k) for k in np.round(verts[:, :2] / XY_QUANT).astype(np.int64)]
    top = {}
    for i, key in enumerate(keys):
        if terrain[i]:
            top[key] = max(top.get(key, -np.inf), verts[i, 2])
    levels = np.array(
        [(top[k] - verts[i, 2]) / level_height if terrain[i] else 0.0 for i, k in enumerate(keys)]
    )
    if snap:
        levels = np.floor(levels + 0.5)

    # per vertex: least squares over adjacent wall faces of  offset . n = k(n)
    a = np.zeros((n, 2, 2))
    rhs = np.zeros((n, 2))
    for face in faces:
        p = verts[list(face)]
        normal = np.cross(p - np.roll(p, 1, axis=0), np.roll(p, -1, axis=0) - p).sum(axis=0)
        length = np.linalg.norm(normal)
        if length == 0:
            continue
        nx, ny = normal[:2] / length
        w = nx * nx + ny * ny            # 1 for a vertical wall, 0 for floors and tops
        if w < 1e-12:
            continue
        hx, hy = nx / np.sqrt(w), ny / np.sqrt(w)
        k = np.hypot(side * hx, back * max(hy, 0.0))
        for i in face:
            a[i] += w * np.array([[hx * hx, hx * hy], [hx * hy, hy * hy]])
            rhs[i] += w * k * np.array([hx, hy])

    out = np.zeros((n, 2))
    for i in range(n):
        trace = a[i, 0, 0] + a[i, 1, 1]
        if trace == 0 or levels[i] == 0:
            continue
        det = a[i, 0, 0] * a[i, 1, 1] - a[i, 0, 1] ** 2
        if det < RANK_EPS * trace * trace:
            unit = rhs[i] / trace                      # parallel walls: exactly k * n
        else:
            adj = np.array([[a[i, 1, 1], -a[i, 0, 1]], [-a[i, 0, 1], a[i, 0, 0]]])
            unit = adj @ rhs[i] / det
        out[i] = unit * levels[i]
    return out
```

The rank test matters: a regularised inverse (`A + eps * I`) loses several
percent in single precision on a straight diagonal wall, which is what the
node group computes in. The two-branch form is exact for straight walls and
box corners.

## 6. Node group (`rig.build_shear_nodegroup`)

New inputs, appended after Marked Sharp so existing socket order is kept:
Splay Side (float), Splay Back (float), Level Height (float), Splay Snap
(bool). `build_rig_object` sets them from `params.splay()`.

Pipeline, changes in bold:

    Collection Info -> Realize Source -> Turn -> Seat
      -> **Store Named Attribute oblique_splay (the offset field, POINT)**
      -> [Edge IDs switch, unchanged]
      -> **Set Position (Offset = Named Attribute oblique_splay)**
      -> **Remove Named Attribute oblique_splay, oblique_terrain**
      -> Shear -> Realize -> Output

The offset is stored before the Edge IDs branch and applied after it for two
reasons. Split Edges duplicates vertices, and computing the offset afterwards
would give each copy a different answer and tear the mesh; a stored point
attribute is copied to every duplicate. And crease angles stay measured on
the unsplayed source, which keeps the 0.3 promise that the projection never
adds or removes creases.

The offset field, put in its own helper like `_add_edge_id_branch`
(`_add_splay_field`):

1. **Face terms on the corner domain.** Take Normal through Evaluate on Domain
   (FACE) so smooth shading and custom normals cannot leak in. From it compute
   `w`, `hx`, `hy`, `k` as in section 5, then the five products
   `w*hx*hx`, `w*hx*hy`, `w*hy*hy`, `w*k*hx`, `w*k*hy`.
2. **Sum per vertex.** Evaluate on Domain (POINT) on each product gives the
   mean over the vertex's corners. A mean is enough: both solve branches
   divide two of these quantities, so the corner count cancels.
3. **Solve.** `trace`, `det`, a Switch on `det < 1e-4 * trace * trace` between
   `rhs / trace` and `adj @ rhs / det`. Guard `trace == 0` (floor-only
   vertices) so no division by zero reaches the output.
4. **Levels.** Column top minus z, divided by Level Height, rounded when Splay
   Snap is on. Two candidate recipes for the column top; confirm which exists
   and behaves the same on 4.5 and 5.2:
   - A grouped maximum of z with a Group ID hashed from the quantised x and y
     (a Field Min & Max style node, if both versions have one). This matches
     the core reference exactly.
   - Otherwise a Repeat zone with a fixed 8 iterations that carries the drop
     down vertical edges: `drop(v) = max over v's vertical edges to a higher
     vertex u of (z_u - z_v + drop(u))`, using Edges of Vertex sorted by
     horizontal length. This limits stacks to 8 wall segments and does not
     chain across two separate objects that are merely flush; state both in
     the README if this recipe is used.
5. **Mask.** Multiply by the `oblique_terrain` attribute (missing attribute
   reads as false, so unmarked objects get zero).

The helper vertex that the Edge IDs branch joins in has no `oblique_splay`
value and reads as zero; it is deleted before the output as today.

## 7. Operators and UI

- `oblique_rig.mark_terrain` and `oblique_rig.clear_terrain`
  (`REGISTER`, `UNDO`): write or remove the `oblique_terrain` point attribute
  (all true) on the mesh data of the selected mesh objects, then rebuild if a
  rig exists. Non-mesh objects are skipped and counted in the report. Linked
  duplicates share mesh data and so share the mark.
- New sub-panel **Tiles** between Projection and Frame: Tile Splay toggle,
  Level Height, Side Tiles, Back Tiles, Snap to Levels, Mark as Terrain,
  Clear Terrain. Below them a readout in the style of the depth readout:
  "1 level = 16 px front, 16 px side, 16 px back".
- Warnings in the same panel: "Tile Splay needs Depth Up above 0" when `b` is
  0, and "Whole tiles need Depth Right 0" when it is not.
- Nothing changes in Fit to Content: it reads the evaluated mesh, so the
  splayed bounds are included. Confirm with a test.

## 8. Tests

Write each test first and watch it fail, as in `CLAUDE.md`.

`tests/unit/test_splay.py` (plain numpy):

- The four shapes of section 3, with the exact offsets listed there.
- A straight diagonal wall moves by exactly `k * normal` (the rank branch).
- Snap: a 0.9-unit wall counts as 1 level, a 0.4-unit wall as 0; with snap off
  both are proportional.
- Vertices outside the terrain mask get zero and do not raise column tops.
- `RigParams.splay()`: defaults at top-down give `(1.0, 2.0)`; disabled gives
  zeros; `depth_px_up = 0` gives zeros.

`tests/unit/test_presets.py`: `TOP_DOWN` gives `(0, 16)` at 16 px per unit.

`tests/blender/test_splay.py`:

- Marked box, splay on: the rig's evaluated vertices equal the shear of
  (source + `core.splay.splay_offsets`) within 1e-4. Repeat for the flush
  stack and the pit.
- Splay off, or box not marked: evaluated vertices identical to 0.3.
- Edge IDs input on: the set of distinct vertex positions is unchanged (no
  tears), and neither helper attribute is on the output mesh.
- Direction 90 degrees: the box's former side walls now get the back offset.
- Fit to Content: the reported footprint includes the splayed base.
- Mark and Clear operators: attribute present or absent, poll, report.
- One render (64 x 48 or smaller, inside the existing EGL skip): the marked
  box at top-down; in the flat pass the side wall is 16 px wide and the back
  rim 16 px tall.

Also extend `test_properties.py`, `test_ui.py` and `test_register.py` for the
new properties, panel and operators.

## 9. Limits to state in the README

- **Ground behind a cliff.** A back wall covers
  `(level_height + back_tiles) / b` units of ground per level. A plateau has
  to be deeper than that for its top to remain visible.
- **Narrow gaps.** Splays from facing walls can cross (a one-tile trench
  between two cliffs). The rig does not detect this.
- **Overhangs, caves, bridges.** The column rule assumes terrain is a height
  field. Geometry above other geometry at the same plan position is treated
  as one wall.
- **Ramps and stairs.** A sloped face is treated as part wall (weight `w`).
  The result is continuous but has not been tuned; expect to paint stairs by
  hand.
- **Front-pointing vertex.** A vertex whose two walls face front-left and
  front-right bulges forward slightly (0.13 units on a 24-sided cylinder).
  Turning the shape so a face, not a vertex, points at the camera removes it.
- **Depth warnings** still use the unsplayed source boxes.

## 10. Steps

1. `core/splay.py` and `tests/unit/test_splay.py`; `TOP_DOWN` preset and its
   test. `python3 tools/check.py --only unit`.
2. `RigParams` fields and `splay()`, properties, `params_from_settings`.
3. Probe the node questions of section 6 on both Blender versions (grouped
   maximum, Evaluate on Domain for face normals on corners, named attribute
   surviving Split Edges and the Realize in the Edge IDs branch). Record the
   answers in `docs/pitfalls.md`.
4. `tests/blender/test_splay.py`, then `_add_splay_field` and the pipeline
   changes in `rig.py`.
5. Mark and Clear operators, Tiles panel, readout and warnings.
6. The render test.
7. README (new "Top-down tilesets" section, settings table, limits),
   `CLAUDE.md` layout list, manifest version 0.4.0.
8. Full `python3 tools/check.py` on 5.2 and with `BLENDER` pointing at 4.5.

## 11. Open questions

- Should Back Tiles allow half tiles? Many tilesets draw the back rim thinner
  than one tile. Whole tiles were the requirement, so the plan uses integers;
  a pixel-valued alternative would be a one-property change.
- Should Add Selected to Rig get an "As Terrain" option, or are the two Mark
  operators enough?

## 12. Review, 2026-11-24 (binding where it differs from the sections above)

The maths of sections 2, 3 and 5 was re-run and holds: box, pit, and a
connected terraced grid give the listed offsets. Blender 5.2.3 and 4.5.16 are
now available, so every node question is to be answered by a probe, not from
memory. Corrections and additions:

**Corrections**

1. *Face normal in the reference.* `sum(cross(e_prev, e_next))` only points
   the right way for convex faces. Use Newell's formula,
   `sum(cross(p_i, p_{i+1}))`, which is what Blender uses for face normals.
   Results on the shapes of section 3 are identical.
2. *Face normal in the node group.* Evaluate on Domain (FACE) of the Normal
   socket may still pick up custom normals on 4.5+ (free-form face normals).
   Use the node's "True Normal" output if both versions have it, else build
   the normal from positions. Add a test with a smooth-shaded mesh and one
   with custom normals.
3. *Column top in the node group.* The grouped maximum has to ignore
   non-terrain vertices (feed a large negative z for them), as the reference
   does; masking only the result is not the same when a prop vertex sits
   above a terrain vertex at the same plan position.
4. *`RigParams.splay()`* must not raise when Tile Splay is off: return
   `(0.0, 0.0)` before calling `shear()`.
5. *Whole-tile wording (section 2).* With Snap to Levels and a height that is
   slightly off, the side wall is a whole number of tiles wide, and the back
   wall's base lands a whole number of tiles above the wall's ground line;
   the rim measured from the top edge is off by the height error. Tops are
   whole tiles only when Depth Up is a multiple of Pixels per Unit. The back
   rim grows with the wall: a 2-level cliff shows `2 * back_tiles`.
6. *Mark and Clear operators.* Mesh attributes cannot be written reliably in
   Edit Mode: poll requires Object Mode. Count a shared mesh once.
7. *Remove Named Attribute.* Confirm on both versions that removing an
   attribute that is absent (unmarked scenes) prints nothing and leaves no
   error that `check.py` would flag.

**Limits to add to the README (section 9)**

- *Auto Seat and the tile grid.* Auto Seat centres the content on x, so a
  blockout an odd number of tiles wide sits half a tile off the unit grid
  (and off the grid pass). Turn Auto Seat off, or keep the width even.
- *Flush stacks need shared plan positions.* The column rule only links
  vertices at the same x and y. A connected extruded grid always has them;
  two separate boxes only where their corners coincide. Where the upper wall
  of a flush stack ends, the lower wall changes from 2 levels to 1 across one
  grid cell (a diagonal), and the lower top's edge vertex moves with it.
- *Connected ground.* In a single connected mesh, the ground faces next to a
  wall base are squashed or folded under the splayed wall. They are hidden
  by the wall.
- *Geometry added after marking.* New primitives joined into a marked mesh
  are not terrain until Mark as Terrain is run again.
- *Space beside walls.* Props need `side_tiles` of free ground per level next
  to a side wall, as they need free ground behind a back wall.

**Open questions, decided for 0.4:** Back Tiles stays a whole number; no
"As Terrain" option on Add Selected to Rig.

**Changed 2026-11-24, after review: half tiles.** Side Tiles and Back Tiles
allow half tiles. Both become Float properties (`splay_side_tiles`,
`splay_back_tiles`, default 1.0, 0 to 8, UI step 0.5, one decimal), and
`RigParams.splay()` rounds each to the nearest half tile (half up) before
use, so a typed 0.7 behaves as 0.5 and the result is always predictable. The
readout shows the rounded values in pixels. A half tile is a whole number of
pixels only when Pixels per Unit is even; the Tiles panel warns "Half tiles
need an even Pixels per Unit" otherwise. This replaces the Int entries in the
section 4 table and the first open question of section 11.
