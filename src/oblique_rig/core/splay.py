# SPDX-License-Identifier: GPL-3.0-or-later
"""Wall splay for top-down tile blockouts: the reference the node group is tested against.

Top-down tilesets (the 2D Zelda style) show tops and front walls as a top-down
oblique projection does, but give side walls width and show back walls as a
thin rim. The rig gets there by moving each wall base outward in plan before
the shear; tops never move.

**Levels.** A vertex's drop is the z of the highest terrain vertex at the same
plan position (x and y within :data:`XY_QUANT`) minus its own z. Its levels are
``drop / level_height``, rounded half up when ``snap`` is on. Vertices on top
of a wall have 0 levels and stay where they are.

**Offsets.** A wall face with horizontal normal ``(hx, hy)`` asks its vertices
to move by ``k`` per level along that normal, with::

    k = hypot(side * hx, back * max(hy, 0))

so front walls (-Y) get 0, side walls (+-X) get ``side`` and back walls (+Y)
get ``back``. ``side`` and ``back`` are plan units per level, as returned by
``RigParams.splay()``. Sloped faces count with weight ``w = nx^2 + ny^2`` of
their unit normal; floors and tops (``w = 0``) do not count.

**Corners.** A vertex on several walls takes the least-squares solution over
its adjacent faces of ``offset . h = k(h)``. The 2 x 2 system is solved in two
branches: when ``det < RANK_EPS * trace^2`` the walls are parallel and the
answer is exactly ``k * h`` (``rhs / trace``); otherwise the adjugate solve.
A regularised inverse would lose several percent in single precision on a
straight diagonal wall, which is what the node group computes in.

Face normals use Newell's formula (:func:`face_normal`), as Blender does, so
concave faces point the right way.

Pure Python + numpy: no bpy.
"""

from collections.abc import Iterable, Sequence

import numpy as np

TERRAIN_ATTRIBUTE = "oblique_terrain"
"""BOOLEAN point attribute on source meshes that marks them as terrain (splayed)."""

SPLAY_ATTRIBUTE = "oblique_splay"
"""FLOAT_VECTOR point attribute holding the offset; used inside the node group only."""

RANK_EPS = 1e-4
"""``det(A) < RANK_EPS * trace(A)^2`` means the walls at a vertex are parallel."""

XY_QUANT = 1e-4
"""Vertices closer than this in plan (units) belong to one wall column."""


def face_normal(points: np.ndarray) -> np.ndarray:
    """Unnormalised normal of a polygon by Newell's formula, ``sum(cross(p_i, p_{i+1}))``.

    Its length is twice the polygon's area, and it points the right way for
    concave polygons too (the corner cross products ``cross(e_prev, e_next)``
    do not). Points are centred first for precision; the sum does not change.
    """
    p = np.asarray(points, float)
    p = p - p.mean(axis=0)
    return np.cross(p, np.roll(p, -1, axis=0)).sum(axis=0)


def _levels(verts: np.ndarray, terrain: np.ndarray, level_height: float, snap: bool) -> np.ndarray:
    """Levels of wall above each vertex (0 outside the terrain mask)."""
    keys = [tuple(k) for k in np.round(verts[:, :2] / XY_QUANT).astype(np.int64)]
    top: dict[tuple[int, ...], float] = {}
    for i, key in enumerate(keys):
        if terrain[i]:
            top[key] = max(top.get(key, -np.inf), float(verts[i, 2]))
    levels = np.array(
        [(top[key] - verts[i, 2]) / level_height if terrain[i] else 0.0 for i, key in enumerate(keys)],
        dtype=float,
    )
    if snap:
        levels = np.floor(levels + 0.5)
    return levels


def splay_offsets(
    verts: Sequence[Sequence[float]] | np.ndarray,
    faces: Iterable[Sequence[int]],
    side: float,
    back: float,
    level_height: float,
    snap: bool = True,
    terrain: Sequence[bool] | np.ndarray | None = None,
) -> np.ndarray:
    """Per-vertex plan offset ``(dx, dy)`` in units, shape ``(len(verts), 2)``.

    Args:
        verts: vertex positions ``(x, y, z)`` after Turn and Seat (camera-relative).
        faces: polygons as vertex index sequences, wound as Blender winds them.
        side: plan offset per level of a side (+-X) wall.
        back: plan offset per level of a back (+Y) wall.
        level_height: units of drop per level; must be positive.
        snap: round levels half up to whole numbers.
        terrain: per-vertex mask; vertices outside it get zero and do not raise
            column tops. ``None`` means every vertex is terrain.

    Raises:
        ValueError: if ``level_height`` is not positive.
    """
    if level_height <= 0:
        raise ValueError(f"level_height must be positive, got {level_height}")
    verts = np.asarray(verts, float).reshape(-1, 3)
    n = len(verts)
    terrain = np.ones(n, bool) if terrain is None else np.asarray(terrain, bool)
    levels = _levels(verts, terrain, level_height, snap)

    # Per vertex: least squares over adjacent wall faces of  offset . h = k(h).
    a = np.zeros((n, 2, 2))
    rhs = np.zeros((n, 2))
    for face in faces:
        index = list(face)
        normal = face_normal(verts[index])
        length = np.linalg.norm(normal)
        if length == 0:
            continue
        nx, ny = normal[:2] / length
        w = nx * nx + ny * ny  # 1 for a vertical wall, 0 for floors and tops
        if w < 1e-12:
            continue
        hx, hy = nx / np.sqrt(w), ny / np.sqrt(w)
        k = np.hypot(side * hx, back * max(hy, 0.0))
        term = w * np.array([[hx * hx, hx * hy], [hx * hy, hy * hy]])
        target = w * k * np.array([hx, hy])
        for i in index:
            a[i] += term
            rhs[i] += target

    out = np.zeros((n, 2))
    for i in range(n):
        trace = a[i, 0, 0] + a[i, 1, 1]
        if trace == 0 or levels[i] == 0:
            continue
        det = a[i, 0, 0] * a[i, 1, 1] - a[i, 0, 1] ** 2
        if det < RANK_EPS * trace * trace:
            unit = rhs[i] / trace  # parallel walls: exactly k * h
        else:
            adj = np.array([[a[i, 1, 1], -a[i, 0, 1]], [-a[i, 0, 1], a[i, 0, 0]]])
            unit = adj @ rhs[i] / det
        out[i] = unit * levels[i]
    return out
