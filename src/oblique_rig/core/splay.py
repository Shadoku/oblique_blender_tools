# SPDX-License-Identifier: GPL-3.0-or-later
"""Wall splay for top-down tile blockouts: the reference the node group is tested against.

Top-down tilesets (the 2D Zelda style) show tops and front walls as a top-down
oblique projection does, but give side walls width and show back walls as a
thin rim. The rig gets there by moving each wall base outward in plan before
the shear; tops never move.

**Levels.** A vertex's wall runs from a base to a top. The top is the higher
of the highest terrain vertex at the same plan position (x and y within
:data:`XY_QUANT`) and the vertex reached by walking up the wall's edges
(:func:`wall_ends`); the base the lower of the two the other way. The second
rule follows walls that step in and out, the first links separate meshes
stacked flush. Its levels are ``(top - z) / level_height``; with ``snap`` the
wall's whole height is rounded half up to levels and the vertex gets its
share. Vertices on top of a wall have 0 levels and stay where they are.

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

**Details.** Loose parts of a terrain mesh that are not its largest part
(decorations stuck on a wall, say) do not splay on their own: each of their
vertices follows the nearest point on the terrain surface of the same mesh
(that face's ``k * h`` times the levels there), so they lean with the wall
they sit on (:func:`detail_mask`,
``detail`` and ``group`` of :func:`splay_offsets`). They do not raise column
tops either.

Face normals use Newell's formula (:func:`face_normal`), as Blender does, so
concave faces point the right way.

Pure Python + numpy: no bpy.
"""

import zlib
from collections.abc import Iterable, Sequence

import numpy as np

TERRAIN_ATTRIBUTE = "oblique_terrain"
"""BOOLEAN point attribute on source meshes that marks them as terrain (splayed)."""

DETAIL_ATTRIBUTE = "oblique_detail"
"""BOOLEAN point attribute on terrain meshes: true on loose parts that follow the largest part."""

MESH_ID_ATTRIBUTE = "oblique_mesh"
"""INT point attribute on terrain meshes, one value per mesh: details only follow their own mesh."""

WALL_TOP_ATTRIBUTE = "oblique_wall_top"
WALL_BASE_ATTRIBUTE = "oblique_wall_base"
"""INT point attributes on terrain meshes: the vertex index of the top and base of each vertex's wall."""

WALL_MIN_WEIGHT = 0.25
"""A face is a wall when ``nx^2 + ny^2`` of its unit normal is at least this: steeper than 30 degrees."""

WALL_EDGE_EPS = 1e-6
"""Edge ends closer than this in z (units) are level: the edge leads neither up nor down."""

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


def _levels(
    verts: np.ndarray,
    terrain: np.ndarray,
    level_height: float,
    snap: bool,
    top: np.ndarray,
    base: np.ndarray,
) -> np.ndarray:
    """Levels of wall above each vertex (0 outside the terrain mask).

    The wall's top is the higher of the column top (highest terrain vertex at
    the same plan position) and the z of the vertex's wall top (``top``); its
    base the lower of the column bottom and the z of ``base``. Unsnapped,
    levels are ``(top - z) / level_height``. Snapped, the whole wall's height
    is rounded to levels and the vertex gets its share of them,
    ``round((top - base) / level_height) * (top - z) / (top - base)``, so
    rings part way up a wall stay in line.
    """
    keys = [tuple(k) for k in np.round(verts[:, :2] / XY_QUANT).astype(np.int64)]
    column_top: dict[tuple[int, ...], float] = {}
    column_base: dict[tuple[int, ...], float] = {}
    for i, key in enumerate(keys):
        if terrain[i]:
            column_top[key] = max(column_top.get(key, -np.inf), float(verts[i, 2]))
            column_base[key] = min(column_base.get(key, np.inf), float(verts[i, 2]))
    levels = np.zeros(len(verts))
    for i, key in enumerate(keys):
        if not terrain[i]:
            continue
        z = verts[i, 2]
        wall_top = max(column_top[key], verts[top[i], 2] if terrain[top[i]] else z)
        wall_base = min(column_base[key], verts[base[i], 2] if terrain[base[i]] else z)
        height = wall_top - wall_base
        if not snap:
            levels[i] = (wall_top - z) / level_height
        elif height > 0:
            levels[i] = np.floor(height / level_height + 0.5) * (wall_top - z) / height
    return levels


def wall_ends(
    verts: Sequence[Sequence[float]] | np.ndarray, faces: Iterable[Sequence[int]]
) -> tuple[np.ndarray, np.ndarray]:
    """Per vertex, the index of the top and of the base vertex of the wall it is on.

    A wall edge is an edge of a face steeper than 30 degrees
    (``nx^2 + ny^2 >= WALL_MIN_WEIGHT`` of its unit normal) whose ends differ
    in z. The top of a vertex is the highest vertex reached by only going up
    along wall edges, the base the lowest reached by only going down; a vertex
    with nowhere to go is its own. This follows walls whose profile steps in
    and out, where vertices above each other do not share a plan position.
    Ties go to the lower index.
    """
    verts = np.asarray(verts, float).reshape(-1, 3)
    n = len(verts)
    z = verts[:, 2]
    higher: list[list[int]] = [[] for _ in range(n)]
    lower: list[list[int]] = [[] for _ in range(n)]
    for face in faces:
        index = list(face)
        normal = face_normal(verts[index])
        length = np.linalg.norm(normal)
        if length == 0:
            continue
        nx, ny = normal[:2] / length
        if nx * nx + ny * ny < WALL_MIN_WEIGHT:
            continue
        for u, v in zip(index, index[1:] + index[:1], strict=True):
            if abs(z[u] - z[v]) <= WALL_EDGE_EPS:
                continue
            low, high = (u, v) if z[u] < z[v] else (v, u)
            higher[low].append(high)
            lower[high].append(low)

    order = np.argsort(z, kind="stable")
    top = np.arange(n)
    for v in order[::-1]:
        for u in higher[v]:
            candidate = top[u]
            if (z[candidate], -candidate) > (z[top[v]], -top[v]):
                top[v] = candidate
    base = np.arange(n)
    for v in order:
        for u in lower[v]:
            candidate = base[u]
            if (-z[candidate], -candidate) > (-z[base[v]], -base[v]):
                base[v] = candidate
    return top, base


def splay_offsets(
    verts: Sequence[Sequence[float]] | np.ndarray,
    faces: Iterable[Sequence[int]],
    side: float,
    back: float,
    level_height: float,
    snap: bool = True,
    terrain: Sequence[bool] | np.ndarray | None = None,
    detail: Sequence[bool] | np.ndarray | None = None,
    group: Sequence[int] | np.ndarray | None = None,
    top: Sequence[int] | np.ndarray | None = None,
    base: Sequence[int] | np.ndarray | None = None,
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
        detail: per-vertex mask of loose parts (see :func:`detail_mask`). A
            terrain vertex in it follows the nearest point on the other
            terrain faces of its group: that face's own ``k * h`` times the
            levels interpolated there. It does not raise column tops.
            ``None`` means no details.
        group: per-vertex integer; details only look at faces of their own
            group. ``None`` means one group.
        top, base: per-vertex index of the wall's top and base vertex (see
            :func:`wall_ends`). ``None`` means each vertex is its own, which
            leaves the column rule alone to find the wall's height.

    Raises:
        ValueError: if ``level_height`` is not positive.
    """
    if level_height <= 0:
        raise ValueError(f"level_height must be positive, got {level_height}")
    verts = np.asarray(verts, float).reshape(-1, 3)
    n = len(verts)
    terrain = np.ones(n, bool) if terrain is None else np.asarray(terrain, bool)
    detail = np.zeros(n, bool) if detail is None else np.asarray(detail, bool)
    group = np.zeros(n, np.int64) if group is None else np.asarray(group, np.int64)
    faces = [list(face) for face in faces]
    body = terrain & ~detail
    top = np.arange(n) if top is None else np.asarray(top, np.int64)
    base = np.arange(n) if base is None else np.asarray(base, np.int64)
    levels = _levels(verts, body, level_height, snap, top, base)

    # Per vertex: least squares over adjacent wall faces of  offset . h = k(h).
    a = np.zeros((n, 2, 2))
    rhs = np.zeros((n, 2))
    for index in faces:
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

    followers = np.flatnonzero(terrain & detail)
    if len(followers):
        _follow_surface(verts, faces, body, group, followers, levels, side, back, out)
    return out


def _face_unit_splay(points: np.ndarray, side: float, back: float) -> np.ndarray:
    """``k(h) * h`` of one face: the plan offset per level it asks for, zero for floors and tops."""
    normal = face_normal(points)
    length = np.linalg.norm(normal)
    if length == 0:
        return np.zeros(2)
    nx, ny = normal[:2] / length
    w = nx * nx + ny * ny
    if w < 1e-12:
        return np.zeros(2)
    hx, hy = nx / np.sqrt(w), ny / np.sqrt(w)
    return np.hypot(side * hx, back * max(hy, 0.0)) * np.array([hx, hy])


def _follow_surface(
    verts: np.ndarray,
    faces: list[list[int]],
    body: np.ndarray,
    group: np.ndarray,
    followers: np.ndarray,
    levels: np.ndarray,
    side: float,
    back: float,
    out: np.ndarray,
) -> None:
    """Set ``out`` of each follower from the nearest point of its group's body faces.

    The offset is that face's own ``k(h) * h`` times the levels interpolated
    at the nearest point (faces triangulated as a fan from their first
    vertex). Using the face's own direction, not the corner-solved vertex
    offsets, keeps a detail on the plane of its wall without sliding it along
    the wall.
    """
    triangles, units = [], []
    for face in faces:
        if len(face) < 3 or not body[face].all():
            continue
        unit = _face_unit_splay(verts[face], side, back)
        for i in range(1, len(face) - 1):
            triangles.append((face[0], face[i], face[i + 1]))
            units.append(unit)
    triangles = np.array(triangles, dtype=np.int64).reshape(-1, 3)
    units = np.array(units, dtype=float).reshape(-1, 2)
    for i in followers:
        best, best_distance = np.zeros(2), np.inf
        for t in np.flatnonzero(group[triangles[:, 0]] == group[i]):
            triangle = triangles[t]
            weights = _closest_point_weights(verts[i], verts[triangle])
            distance = np.linalg.norm(weights @ verts[triangle] - verts[i])
            if distance < best_distance:
                best, best_distance = units[t] * (weights @ levels[triangle]), distance
        out[i] = best


def _closest_point_weights(point: np.ndarray, triangle: np.ndarray) -> np.ndarray:
    """Barycentric weights of the point of ``triangle`` (3 x 3) closest to ``point``."""
    a, b, c = triangle
    ab, ac, ap = b - a, c - a, point - a
    d1, d2 = ab @ ap, ac @ ap
    if d1 <= 0 and d2 <= 0:
        return np.array([1.0, 0.0, 0.0])
    bp = point - b
    d3, d4 = ab @ bp, ac @ bp
    if d3 >= 0 and d4 <= d3:
        return np.array([0.0, 1.0, 0.0])
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        v = d1 / (d1 - d3) if d1 != d3 else 0.0
        return np.array([1.0 - v, v, 0.0])
    cp = point - c
    d5, d6 = ab @ cp, ac @ cp
    if d6 >= 0 and d5 <= d6:
        return np.array([0.0, 0.0, 1.0])
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        w = d2 / (d2 - d6) if d2 != d6 else 0.0
        return np.array([1.0 - w, 0.0, w])
    va = d3 * d6 - d5 * d4
    if va <= 0 and (d4 - d3) >= 0 and (d5 - d6) >= 0:
        w = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        return np.array([0.0, 1.0 - w, w])
    total = va + vb + vc
    if total == 0:
        return np.array([1.0, 0.0, 0.0])
    return np.array([va, vb, vc]) / total


def detail_mask(
    verts: Sequence[Sequence[float]] | np.ndarray,
    faces: Iterable[Sequence[int]],
    edges: Iterable[Sequence[int]] = (),
) -> np.ndarray:
    """Per-vertex bool: true on every loose part of a mesh except the largest.

    Parts are the connected pieces of the mesh (vertices joined by faces or
    ``edges``). The largest is the one with the most surface area; parts that
    tie with it (within one millionth) are kept too, so two equal boxes in one
    mesh are both terrain. Loose vertices and wire edges have no area and
    count as details whenever the mesh has any face.
    """
    verts = np.asarray(verts, float).reshape(-1, 3)
    n = len(verts)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    faces = [list(face) for face in faces]
    for chain in [*faces, *(list(edge) for edge in edges)]:
        root = find(chain[0])
        for i in chain[1:]:
            parent[find(i)] = root
    roots = np.array([find(i) for i in range(n)], dtype=np.int64)
    area = np.zeros(n)
    for face in faces:
        if len(face) >= 3:
            area[roots[face[0]]] += np.linalg.norm(face_normal(verts[face])) / 2.0
    largest = area.max() if n else 0.0
    return area[roots] < largest * (1.0 - 1e-6)


def mesh_id(name: str) -> int:
    """A stable signed 32-bit integer for a mesh name (CRC-32), for :data:`MESH_ID_ATTRIBUTE`."""
    value = zlib.crc32(name.encode("utf-8"))
    return value - (1 << 32) if value >= (1 << 31) else value
