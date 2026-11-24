# Oblique Rig

A Blender extension (Blender 4.5 LTS through 5.2 LTS) that builds a 2:1
oblique projection rig for drawing pixel-art guides. Model or link your assets
in 3D, look through the rig's camera, and render pixel-exact reference layers
(flat colours, shading, object ids, silhouette, outline, floor grid) to paint
over. A top-down mode turns a tile blockout into guides in the style of 2D
Zelda tilesets, with side walls that have width and back walls shown as a rim. The output is a reference, never final art: every pixel lands on the
grid so you can trace it without guessing.

## Why an oblique projection

In an oblique (cavalier or cabinet) projection the front of every object is
drawn face-on at its true shape: squares stay square, horizontals stay
horizontal, verticals stay vertical. Only the depth axis recedes, along a fixed
diagonal. With a 2:1 slope (two pixels across, one up) that diagonal is the
clean pixel staircase pixel artists already use, so edges in the guide match
the lines you would draw by hand.

Blender's cameras cannot produce this directly: an orthographic camera gives
axonometric views (isometric, dimetric), never an undistorted front face plus
a slanted depth axis. The rig gets there by shearing the scene instead of the
camera:

    X' = X + a*Y,   Z' = Z + b*Y,   Y' = Y

A straight-on orthographic camera looking along +Y then sees world X as screen
x and world Z as screen y, with depth pushed up and to the right. Y is left
untouched, so depth sorting and occlusion stay correct.

### Pixel-exact depth

With **Pixel Exact** on (the default) you set the shear in whole pixels: one
unit of depth moves exactly **Depth Right** px right and **Depth Up** px up
(16 and 8 at 16 px per unit, the 2:1 staircase), so `a = R / ppu` and
`b = U / ppu`. The panel shows the result as "1 unit depth = R px right, U px
up". The camera is snapped to whole pixels and the render resolution is
rounded up to even numbers, so the frame edges, the camera centre and every
vertex at a whole-unit (or whole-pixel) position fall on pixel boundaries.

With Pixel Exact off the older float controls come back: `(a, b) = Depth Scale
* normalize(Slope Run, Slope Rise)`. The defaults (2 / 1 / 0.6 at 16 px per
unit) move one unit of depth 8.59 px right and 4.29 px up. A back edge then
lands between pixels: renders show uneven staircases, a cube's back face can
come out a pixel wider or narrower than its front, and any filtering smears
the edge across two pixels. That is fine for a loose sketch and wrong for a guide you trace, which
is why pixel-exact is the default.

## What it builds

- **`Oblique_Source` collection**: where your assets go. It is hidden from
  renders; only the sheared copy renders.
- **`Oblique Rig` object** with a Geometry Nodes modifier (node group
  `ObliqueShear`): Collection Info pulls in the source collection and realizes
  it, Transform turns it by **Direction** about Z, **Auto Seat** moves it so
  its bounding-box front edge is on y=0 and it is centred on x=0, with
  **Tile Splay** the wall bases of terrain meshes move outward (see
  [Top-down tilesets](#top-down-tilesets)), a matrix Transform applies the
  shear, and Realize Instances turns the result into a
  real mesh you can apply or export.
- **`Oblique Camera`**: orthographic, facing +Y, with its ortho scale sized so
  one Blender unit is exactly *Pixels per Unit* render pixels. Set as the scene
  camera.
- **`Oblique Sun`**: a sun lamp so shapes read in an F12 render.
- **Flat render settings** for F12: EEVEE with one sample, no pixel filter,
  transparent film, RGBA output, Standard view transform, resolution from the
  settings. (Render Guides uses its own Workbench settings and puts these back
  afterwards.)
- **Calibration objects** (optional): a 1-unit cube with its front face on
  y=0, a second one 3 units deeper, and a 6x5 floor plane starting at y=0, all
  in the source collection, with two flat materials (cubes and floor). The back
  cube should sit up and to the right of the front one along the chosen slope.

The plane y=0 is the rig's reference: geometry on it is drawn unchanged, and
everything behind it (positive Y) is pushed up and to the right. Keep the front
of your assets on y=0 and their base on z=0 and nothing ends up below the
floor line; **Add Selected to Rig** and **Auto Seat** do that for you.

## Install

1. Get the zip: a release asset, or build it with
   `python3 tools/check.py --only package --keep-dist` (writes `dist/oblique_rig-<version>.zip`).
2. In Blender: Edit > Preferences > Get Extensions, the drop-down at the top
   right > Install from Disk, pick the zip.

The extension asks for one permission, writing files: Render Guides and
Render Turnaround write PNGs to the output folder you choose.

## The painter's workflow

1. In the 3D View press N and open the **Oblique Rig** tab.
2. **Build Rig** (Rig panel). Optionally **Add Calibration Objects** to see
   the slope on two cubes and a floor.
3. Select your assets and press **Add Selected to Rig**. Each one is moved
   into `Oblique_Source` with its front edge on y=0 and its origin on z=0 (the
   redo panel offers *Bottom to Floor* to use the lowest point instead). You can
   also drop or link objects into the collection by hand.
4. Pick a slope (Projection panel, or **Slope Presets**) and a pixel density.
5. **Fit to Content** (Frame panel): sets the resolution and the camera so the
   sheared content fills the frame with 4 px of padding (change it in the redo
   panel) and rebuilds. The panel then shows the sprite size, e.g.
   "Content: 48 x 72 px", and any depth-order warning.
6. Choose the passes, outline and grid (Output panel), set an output folder
   and press **Render Guides**. Open the PNGs as layers in your paint program
   (they all have the same size and alignment) and paint over them.
7. For game sprites, set **Directions** (4 or 8) and press **Render
   Turnaround**: one set of passes per facing, identical framing, plus one
   sprite sheet per pass.

Use the **Source / Sheared** toggle at the top of the tab to switch the
viewport between the original objects and the sheared result; only one of
them is visible at a time. Renders always show the sheared result. Numpad 0
looks through `Oblique Camera`.

**Build Rig** is safe to press again: it updates the existing rig, camera,
lamp and render settings from the panel and never duplicates them. The shear
inputs also appear on the rig's `Oblique` modifier for quick experiments, but
the next Build Rig resets them to the panel values. **Remove Rig** deletes the
rig object, camera, sun and node group; its *Include Source* option also
removes the source collection and the calibration objects (other objects in it
are moved to the scene collection, never deleted).

**Depth warnings.** Build and Fit report source objects that interpenetrate
(their world bounding boxes overlap on all three axes), at most three pairs,
e.g. "Crate / Barrel overlap in depth". The shear keeps depth order, so objects
that merely overlap on screen or stand on a floor are fine; objects pushed into
each other are where the guide stops telling you which edge is in front.

## Top-down tilesets

Top-down tilesets in the style of the 2D Zelda games draw terrain with a
convention no single projection produces: tops and front walls are seen as
from above, but side walls are given width and back walls show as a thin rim
above the top. **Tile Splay** gets there from an ordinary blockout (a grid
with regions extruded up): before the shear, each wall base of a mesh marked
as terrain moves outward in plan, so side and back walls become visible. Tops
never move, so every top keeps the shape and position you modelled. Cliffs,
plateaus, pits and rooms then render as tile-aligned guides; a pit's walls
splay into the pit, which gives room interiors.

One tile is one Blender unit, which is *Pixels per Unit* pixels: the grid the
rig already uses (Grid Step 1).

1. Model the blockout on the unit grid: a grid or plane with regions extruded
   up by whole units (one unit per level). One connected mesh works best.
2. **Add Selected to Rig**, then, with the blockout still selected and in
   Object Mode, **Mark as Terrain** (Tiles panel). Props such as trees, houses
   and characters stay unmarked and are drawn as plain oblique.
3. **Slope Presets > Top-down** (Projection panel): Depth Right 0, Depth Up
   equal to Pixels per Unit.
4. Turn on **Tile Splay**. The panel shows what one level gives on screen,
   e.g. "1 level = 16 px front, 16 px side, 16 px back".
5. Turn **Auto Seat** off (Direction panel), or keep the blockout an even
   number of tiles wide, so it stays on the tile grid (see the limits below).
6. **Fit to Content** and **Render Guides** as usual. Fit reads the splayed
   result, so the frame includes the walls.

What one level of wall gives on screen:

| Wall faces | On screen per level |
|---|---|
| Front (toward the camera) | *Level Height* tiles tall, from the projection itself. |
| Side (left or right) | *Side Tiles* tiles wide. |
| Back (away from the camera) | A rim *Back Tiles* tiles tall above the top's back edge. |

Walls facing in between (a round plateau) blend smoothly from one to the
next. A 2-level cliff shows twice as much: two levels of side wall and
`2 * Back Tiles` of rim. **Mark as Terrain** writes a boolean point attribute
`oblique_terrain` on the mesh data, so linked duplicates share the mark;
**Clear Terrain** removes it. Both rebuild the rig if there is one. Geometry
you add to a marked mesh later becomes terrain on the next **Update Rig** (or
Build Rig, or Mark as Terrain).

**Walls that are not straight up.** A wall's height is measured along the
wall itself, from its base to its top, so a profile that steps in and out
(ledges, a zigzag, a chamfered top) leans as one wall: every vertex moves in
proportion to its depth below the top. Faces steeper than 30 degrees count
as wall; flatter ones are ground or ramp. Snap to Levels rounds the height of
the whole wall, not of each edge loop, so loops part way up stay in line.

**Decorations.** A terrain mesh can hold loose parts that are not connected
to the rest: stones stuck on a wall, a ledge, a sign. The largest loose part
of each mesh (by surface area) is the terrain itself; every other loose part
is a decoration. A decoration does not splay on its own. Each of its vertices
leans by the same amount as the nearest point of the terrain in the same
mesh, so it stays on its wall: on a side wall it leans sideways with the
wall, on a front wall or on a top it stays as modelled. Parts of equal size
all count as terrain. To have two plateaus of different size splay on their
own, keep them in separate objects.

Wall sizes are whole tiles when Depth Right is 0 and Depth Up is above 0 (the
Top-down preset), Level Height is a whole number of units and wall heights are
multiples of it. With Snap to Levels on, a slightly-off height still gives a
side wall a whole number of tiles wide and puts the back wall's base a whole
number of tiles above the wall's ground line; the front wall always shows its
true height. Tops are whole tiles only when Depth Up is a multiple of Pixels
per Unit. Half tiles are whole pixels only when Pixels per Unit is even,
quarter tiles only when it is a multiple of 4.

The panel warns when the result will not be what you expect:

- "Tile Splay needs Depth Up above 0": with Depth Up 0 nothing splays.
- "Whole tiles need Depth Right 0": the splay still works, but the back
  offset also shifts wall bases sideways, not necessarily by whole pixels.
- "Side or Back Tiles is not a whole number of pixels": a half tile needs an
  even Pixels per Unit, a quarter tile a multiple of 4.

### Limits

- **Decorations around a corner.** A decoration follows the wall nearest to
  each of its vertices. One that wraps around a wall corner is pulled two
  ways and tears; keep decorations on one wall.
- **Decorations from modifiers.** Parts are told apart on the mesh data, not
  on the result of modifiers. Loose geometry a modifier adds is not marked.
- **Ground behind a cliff.** A back wall covers `(Level Height + Back Tiles) /
  b` units of ground per level, where `b` is Depth Up / Pixels per Unit (1 with
  the Top-down preset). A plateau has to be deeper than that for its top to
  stay visible.
- **Space beside walls.** Props need *Side Tiles* of free ground per level
  next to a side wall, as they need free ground behind a back wall.
- **Narrow gaps.** Splays from facing walls can cross (a one-tile trench
  between two cliffs). The rig does not detect this.
- **Overhangs, caves, bridges.** The rule assumes terrain is a height field:
  geometry above other geometry at the same plan position is treated as one
  wall.
- **Flush stacks need shared plan positions.** A wall's height is measured to
  the highest terrain vertex at the same x and y. A connected extruded grid
  always has those; two separate boxes stacked flush only where their corners
  coincide. Where the upper wall of a flush stack ends, the lower wall changes
  from 2 levels to 1 across one grid cell (a diagonal), and the lower top's
  edge vertex moves with it.
- **Connected ground.** In one connected mesh, the ground faces next to a wall
  base are squashed or folded under the splayed wall. The wall hides them.
- **Ramps and stairs.** A slope steeper than 30 degrees is a wall and leans
  like one; a flatter one stays where it is unless a wall above or below
  moves its ends. Not tuned; expect to paint stairs by hand.
- **Wall edits need Update Rig.** Which vertex is the top and base of each
  wall is stored on the mesh. Moving vertices shows at once; adding or
  removing edges or faces needs Update Rig.
- **Front-pointing vertex.** A vertex whose two walls face front-left and
  front-right bulges forward slightly (0.13 units on a 24-sided cylinder).
  Turn the shape so a face, not a vertex, points at the camera.
- **Auto Seat and the tile grid.** Auto Seat centres the content on x, so a
  blockout an odd number of tiles wide sits half a tile off the unit grid (and
  off the grid pass). Turn Auto Seat off, or keep the width even.
- **Depth warnings** use the source objects before the splay.

## Settings

The tab has a header (rig status and the **View** toggle, default *Sheared*)
and six sub-panels in workflow order.

### Rig

Buttons only: **Build Rig**, **Add Selected to Rig**, **Add Calibration
Objects**, **Remove Rig**.

### Projection

| Setting | Default | What it does |
|---|---|---|
| Pixel Exact | on | Shear from whole-pixel steps, camera snapped to pixels, even resolution. |
| Depth Right | 16 px | Pixels one unit of depth moves right (pixel-exact). |
| Depth Up | 8 px | Pixels one unit of depth moves up (pixel-exact). |
| Slope Run / Slope Rise | 2 / 1 | Direction of the depth axis when Pixel Exact is off. |
| Depth Scale | 0.6 | Length of one unit of depth when Pixel Exact is off: 1.0 cavalier, about 0.5 cabinet. |
| Slope Presets | | Sets Depth Right / Up for the current Pixels per Unit and turns Pixel Exact on. |

Slope presets (fractions of Pixels per Unit, rounded, at least 1 px when
non-zero):

| Preset | Right | Up | At 16 px per unit |
|---|---|---|---|
| 2:1 cavalier | 1.0 | 0.5 | 16, 8 |
| 2:1 cabinet | 0.5 | 0.25 | 8, 4 |
| 1:1 | 0.5 | 0.5 | 8, 8 |
| 3:2 | 0.75 | 0.5 | 12, 8 |
| 1:2 (steep) | 0.5 | 1.0 | 8, 16 |
| Top-down | 0.0 | 1.0 | 0, 16 |

### Tiles

| Setting | Default | What it does |
|---|---|---|
| Tile Splay | off | Move the wall bases of terrain meshes outward, so side walls get width and back walls show a rim. Needs Depth Up above 0. |
| Level Height | 1 | Wall height in units that counts as one level. |
| Side Tiles | 1.0 | Width of a side wall per level, in tiles (0 to 8, steps of a quarter tile). 0 keeps side walls vertical. |
| Back Tiles | 1.0 | Height of a back wall's rim per level, in tiles (0 to 8, steps of a quarter tile). 0 shows no rim. |
| Snap to Levels | on | Round wall heights to whole levels (half up), so side and back walls are whole tiles. |
| Mark as Terrain | | Mark the selected meshes as terrain (Object Mode); loose parts other than the largest become decorations. Non-mesh objects are skipped. |
| Clear Terrain | | Remove the mark from the selected meshes. |
| Update Rig | | Rebuild the rig from the current settings (the same as Build Rig); also re-marks terrain meshes, so geometry added since is included. |

Side Tiles and Back Tiles are used rounded to the nearest quarter tile (half
up): a typed 0.7 behaves as 0.75. The five Tiles settings apply to a built
rig as soon as they change. Settings in the other panels (the projection, the
frame, the direction) apply on Update Rig. Below the buttons: the readout of what one
level gives on screen, using the rounded values, and the warnings described
in [Top-down tilesets](#top-down-tilesets).

### Frame

| Setting | Default | What it does |
|---|---|---|
| Resolution X / Y | 320 / 200 | Render size in pixels (rounded up to even when pixel-exact). |
| Resolution Presets | | 320 x 200 @ 16, 640 x 360 @ 16, 256 x 256 @ 16, 128 x 128 @ 8, 512 x 288 @ 32 px per unit. |
| Pixels per Unit | 16 | Render pixels per Blender unit; sets the camera's ortho scale (`max(res_x, res_y) / ppu`). |
| Camera Height | 0.35 | Camera z as a fraction of the frame height in units; raises or lowers the floor line in the frame. |
| Camera X | 0 | Camera x in units; snapped to whole pixels when pixel-exact. |
| Fit to Content | padding 4 px | Sets Resolution, Camera X and Camera Height from the sheared content, then rebuilds. |

Below the button: **Content** (the sheared footprint in pixels, from Build or
Fit) and the depth warnings.

### Direction

| Setting | Default | What it does |
|---|---|---|
| Direction | 0 deg | Rotation of the source about Z before the shear (the facing). |
| Auto Seat | on | After turning, move the whole source so its front edge is on y=0 and it is centred on x=0. |
| Directions | 4 | Facings in a turnaround (1 to 16), evenly spaced around the circle starting at Direction. |

### Output

| Setting | Default | What it does |
|---|---|---|
| Output Folder | `//guides/` | Where PNGs go; `//` is relative to the .blend file, so save the file first (or pick an absolute folder). Created if missing. |
| File Name | `guide` | Base name of every file. |
| Flat | on | Pass: unlit material colours. |
| Shaded | on | Pass: studio-lit material colours. |
| Object IDs | off | Pass: one flat colour per source object. |
| Silhouette | off | Pass: the shape in black. |
| Composite | on | Pass: shaded (or flat) with grid and outline on top. |
| Sprite Sheet | on | Turnaround: also write one sheet per pass. |
| Outline | off | Write the outline pass (options below). |
| Grid | off | Write the grid pass (options below). |

The preferences have one option, *Report results*: whether the operators print
a status-bar message.

## Passes and how to use them

Render Guides writes `<name>_<pass>.png` for each enabled pass, all the same
size and pixel-aligned, transparent outside the content. Colours are exact:
Workbench renders with anti-aliasing and pixel filtering off, every pixel is
fully covered or empty, and the derived passes are computed in Python and
written byte for byte.

| File | What it is | Use it as |
|---|---|---|
| `<name>_flat.png` | Unlit material colours. | The base layer: block in local colours, pick palette ramps from it. |
| `<name>_shaded.png` | Studio-lit material colours. | A light/shadow reference on a layer set to low opacity or Multiply. |
| `<name>_ids.png` | One saturated colour per source object (linked duplicates share one). | Magic-wand selection masks per object. |
| `<name>_silhouette.png` | Black where anything is drawn. | Clipping mask, lock-transparency base, or a readability check of the shape. |
| `<name>_outline.png` | The outline in the outline colour. | A line layer on top; trace or keep it. |
| `<name>_grid.png` | Floor grid, ground line and depth lines. | A ruler layer under or over the art; hide it when done. |
| `<name>_composite.png` | Shaded (or flat), then grid, then outline. | A quick look at the whole guide. |

Passes you did not enable are not written, even when one was rendered to
derive another (the flat pass feeds silhouette and outline, the ids pass feeds
object inner lines, and an edge-id render feeds sharp-edge inner lines; that
one is never written).

**Turnarounds** write `<name>_dNN_<pass>.png` for facing `NN` (`d00` is
Direction, then every 360/Directions degrees), and with Sprite Sheet
`<name>_sheet_<pass>.png`: the facings of that pass left to right, `d00`
first, one frame wide each. Framing is identical across facings, so set the
frame with Fit at the widest facing (or pad generously).

### Outline options

| Setting | Default | What it does |
|---|---|---|
| Outer Weight | 1 px | Width of the silhouette outline in whole pixels (0 to 8; 0 turns it off). |
| Inner Weight | 1 px | Width of lines inside the silhouette in whole pixels (0 to 8). |
| Position | Outside | Outside: the line grows the shape. Inside: the line eats into it (the sprite keeps its size). |
| Inner Lines | Objects | None; Objects; Materials; Sharp Edges (see below). |
| Crease Angle | 30° | Sharp Edges only: edges whose two faces meet at more than this angle get a line (0° to 180°). |
| Marked Sharp | on | Sharp Edges only: edges marked sharp in Edit Mode also get a line. |
| Corners | off | Off: lines grow 4-connected, giving the stepped pixel-art corner. On: 8-connected, square corners. |
| Outline Color | black, opaque | Colour of the pass. |

**Inner line modes.** Inner lines are drawn wherever two regions of a
helper render meet inside the silhouette; the modes differ in what a region
is:

- **Objects**: one region per source object (from the ids pass). Lines where
  one object overlaps or touches another; the folds of a single object get
  none.
- **Materials**: one region per flat material colour (from the flat pass).
  Lines where colours change; two objects with the same material merge.
- **Sharp Edges**: one region per piece of surface between sharp edges. The
  rig splits the source along every edge sharper than *Crease Angle* and,
  with *Marked Sharp*, every edge marked sharp, then gives each piece its own
  colour in a hidden render (Workbench vertex colour). Lines appear at object
  boundaries, at hard creases (a box gets its front/top and front/side edges)
  and on marked edges, but not across smooth curvature: a 24-sided cylinder
  keeps one smooth side at 30°, while 10° outlines every facet. Angles are
  measured on the source before the oblique shear, so the projection never
  adds or removes creases.

**Edge loops.** To get a line along a loop that is not a hard crease (a
belt, a seam between two panels, the rim of a soft shape), mark it sharp in
Edit Mode: select the loop (Alt+click an edge in Edge select mode) and run
*Edge > Mark Sharp*. With *Marked Sharp* on, every marked edge gets a line
whatever its angle. Marks are stored in Blender's standard `sharp_edge`
attribute, the one Smooth by Angle and the Edge Split modifier respect, so on
a smooth-shaded mesh marked edges also shade hard. Turn *Marked Sharp* off to
outline by angle only.

A surface that folds over itself without a sharp edge (the far side of a
smooth torus seen through its hole, say) is one region, so no line separates
it from itself; mark an edge sharp there, or let the outer outline do the work.

Weights are exact: an outer weight of 2 is two pixels thick everywhere, and an
inner weight of N is an N-pixel line on the boundary (weight 1 sits on the
left/lower side of it). Lines along the frame edge are never drawn.

### Grid options

| Setting | Default | What it does |
|---|---|---|
| Grid Step | 1 | Units between grid lines. |
| Grid Extent | 8 | Units drawn on each side of the origin, and in depth. |
| Grid Color | cyan, 60 % alpha | Colour of the grid lines; the ground line (y=0, z=0) is drawn fully opaque. |

The grid lies on the floor (z=0): depth lines are horizontal rows, x lines run
along the depth diagonal. In pixel-exact mode each grid point falls exactly on
a pixel, so the grid matches the rendered geometry pixel for pixel.

## Rendering needs a GPU or Mesa

Render Guides uses the Workbench engine, which needs OpenGL/EGL even in
background mode. On a desktop that is your graphics driver. On a headless
Linux machine install Mesa
(`libegl1 libgl1 libgles2 libglx-mesa0 libgl1-mesa-dri libegl-mesa0`); without
`libEGL.so.1` Blender prints `Couldn't open libEGL.so.1` and exits. CI installs
Mesa before the checks; the render tests skip themselves when EGL is missing.

## Compatibility

Blender 4.5 LTS to 5.2 LTS. Geometry Nodes modifier inputs, Transform
Geometry's matrix mode, the EEVEE identifier and the compositor API changed
between these versions; the add-on handles each (see `docs/pitfalls.md`) and
does not use the compositor at all. Every change is tested on both ends of the
range.

## Development

This repository was created from a Blender add-on template built for
development with an AI coding assistant, and still carries its tooling. One
command lints the code, runs unit tests, runs tests inside a headless Blender
with the add-on loaded as a real extension, then builds the zip and installs it
into a throw-away profile:

```
$ python3 tools/check.py
blender: /home/me/.cache/blender/5.2.3/blender
[PASS] lint
[PASS] unit
[PASS] blender
[PASS] package
    PACKAGE: oblique_rig installs, enables, disables cleanly and re-enables

CHECK: PASS
```

`bash tools/install_blender.sh` downloads the pinned Blender
(`.blender-version`) into `~/.cache/blender`; `BLENDER=/path/to/blender
python3 tools/check.py` runs the check against another version (CI runs 4.5
LTS and 5.2 LTS). Python 3.9+ on the host is enough; pytest and ruff are
installed with Blender's bundled Python into `.blender-deps/`.

- `src/oblique_rig/core/`: no `bpy`, unit-tested in milliseconds: shear,
  pixel-exact and fit maths (`oblique.py`), mesh primitives, outlines
  (`outline.py`), grid lines (`guides.py`), layer compositing and sprite sheets
  (`compose.py`), a stdlib PNG encoder/decoder (`png.py`), presets, the
  interpenetration check (`overlap.py`) and the wall splay reference
  (`splay.py`) the node group is tested against. numpy comes with Blender.
- `src/oblique_rig/rig.py`: the Blender-facing builders (node group, camera,
  render settings); `render.py`: Workbench passes and the guide files;
  `operators.py`, `operators_render.py`, `ui.py`, `properties.py` stay thin.
- `tests/blender/`: operator tests, including ones that evaluate the rig
  through the depsgraph and check the sheared vertices, and render tests that
  check the written PNGs pixel by pixel (skipped without EGL).
- `CLAUDE.md`: the rules and loop for an AI assistant; `docs/pitfalls.md`
  and `docs/testing.md`: why the conventions exist and what headless Blender
  can verify.
- `.claude/skills/blender-addon/`: the reusable template skill
  (`tools/new_addon.py` was already run for this add-on).

## Credits and license

Started from Aaron Powell's (lunadigital) Blender add-on boilerplate. Licensed
under GPL-3.0-or-later, as required for add-ons that import `bpy` and are
distributed; see `LICENSE`.
