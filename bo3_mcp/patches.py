"""Synthesize `mesh` and `curve` patch primitives in Treyarch's `iwmap 4` format.

Patches live INSIDE the same `// brush N { ... }` containers as regular
axis-aligned box brushes, so this module returns the same kind of brush
body text that `brushes.box_brush` returns — they're stored on
`Entity.brushes` exactly like boxes.

Two primitive keywords:
  - `mesh`  — flat-triangulated; control points connect linearly.
              Treyarch uses this for outdoor TERRAIN (see
              mp_sector_terrain_north_tunnel_rocks.map).
  - `curve` — Bezier-smoothed between control points; T1 = subdivision
              count. Treyarch uses this for ARCHITECTURAL DETAIL (arches,
              glass roofs, trim).

Canonical terrain pattern from stock prefabs:
  - Visual mesh: no `contents` line (renderable, no collision).
  - Collision mesh: same control points, `contents weaponClip detail
    ai_nosight;`, often a different "blend" material.
Emit BOTH for walkable terrain — see `mesh_terrain_pair()`.

Format reference: `tests/fixtures/PATCH_FORMAT_NOTES.md`.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from . import mapfile

Point = tuple[float, float, float]

# Sensible defaults extracted from stock prefabs.
DEFAULT_TERRAIN_TEXTURE = "t7_rock_sand_crumbled_medium_golden"  # mp_sector
DEFAULT_COLLISION_TEXTURE = "t7_rock_sand_crumbled_large_golden_blend"
DEFAULT_LIGHTMAP = "lightmap_gray"

# Collision flags Treyarch puts on the collision twin of a terrain mesh.
# weaponClip = bullets stop, detail = doesn't cut BSP, ai_nosight = AI
# can't see through (zombies path TO player, not around the obstacle).
COLLISION_CONTENTS = "weaponClip detail ai_nosight"


def _orient_up(control_points: Sequence[Sequence[Point]]) -> list[list[Point]]:
    """Normalize a CP grid so outer→+X-dominant, inner→+Y-dominant.

    The patch's surface normal direction depends on the winding of the
    control points. For terrain (floors), we want +Z-facing normals,
    which requires the outer index to increase in +X and the inner
    index to increase in +Y (verified empirically against
    `mp_sector_terrain_north_tunnel_rocks.map`).

    Heuristic:
      1. Compute outer step (cps[1][0] - cps[0][0]) and inner step
         (cps[0][1] - cps[0][0]).
      2. If outer step is Y-dominant, the caller passed the grid in
         transposed orientation — transpose it so outer becomes
         X-dominant.
      3. If outer step (after possible transpose) goes in -X, reverse
         the outer order.
      4. If inner step (after possible transpose) goes in -Y, reverse
         the inner order in each row.

    Returns a new list-of-lists; does not mutate input."""
    rows = len(control_points)
    cols = len(control_points[0]) if rows else 0
    if rows < 2 or cols < 2:
        # Nothing to reorient; let the caller's validator catch this.
        return [list(row) for row in control_points]

    outer_dx = control_points[1][0][0] - control_points[0][0][0]
    outer_dy = control_points[1][0][1] - control_points[0][0][1]
    inner_dx = control_points[0][1][0] - control_points[0][0][0]
    inner_dy = control_points[0][1][1] - control_points[0][0][1]

    # If outer is Y-dominant, the user passed the grid transposed —
    # transpose so the new outer is the old inner (which was X-dominant).
    if abs(outer_dy) > abs(outer_dx):
        new = [[control_points[r][c] for r in range(rows)] for c in range(cols)]
        # After transpose, swap the step variables to reflect the new layout.
        outer_dx, outer_dy = inner_dx, inner_dy
        inner_dx, inner_dy = (
            control_points[1][0][0] - control_points[0][0][0],
            control_points[1][0][1] - control_points[0][0][1],
        )
    else:
        new = [list(row) for row in control_points]

    if outer_dx < 0:
        new.reverse()
    if inner_dy < 0:
        new = [list(reversed(row)) for row in new]
    return new


def mesh_block(
    control_points: Sequence[Sequence[Point]],
    *,
    texture: str = DEFAULT_TERRAIN_TEXTURE,
    lightmap: str = DEFAULT_LIGHTMAP,
    contents: str | None = None,
    uv_scale: float = 8.0,
    lightmap_scale: float = 1.0,
    tess_t1: int = 0,
    tess_t2: int = 8,
    auto_orient: bool = True,
    vertex_alpha: Sequence[Sequence[int]] | None = None,
) -> str:
    """Emit a single `mesh` brush body (renderable triangulated patch).

    Args:
        control_points: 2D grid `cps[outer][inner]` of (x, y, z) tuples.
            ⚠️  CRITICAL CONVENTION (verified against stock
            `mp_sector_terrain_north_tunnel_rocks.map`):
                - the OUTER index varies along the +X axis (i.e.
                  cps[0..outer_max] sweeps across X)
                - the INNER index varies along the +Y axis (i.e.
                  cps[i][0..inner_max] sweeps across Y)
            Using this convention, the patch's surface normal points +Z
            (UP), so the patch is visible from above (e.g. for terrain
            floors). If you pass cps with outer→Y and inner→X, the
            normal points DOWN and the patch is back-face culled from
            above — visible only from below / grazing angles.
        texture: render material name.
        lightmap: lightmap material (almost always `lightmap_gray`).
        contents: if set, emits a `contents <flags>;` line. For
            walkable floor terrain pass `"weaponClip detail"`. For
            obstacles (rocks, etc.) where you also want AI line-of-sight
            blocked, use `"weaponClip detail ai_nosight"`. DO NOT use
            `ai_nosight` on floor terrain — it stops zombies from
            seeing the player across the patch.
            If None, the mesh is renderable but has no collision (the
            player walks through).
        uv_scale: how many BO3 inches one texture pixel represents.
            8.0 matches stock terrain (texture wraps every ~64-inch
            cell). Lower = denser tiling. Sign is flipped on V to
            match Treyarch's convention (negative V in stock examples).
        lightmap_scale: factor on lightmap UV. 1.0 means one lightmap
            "tile" per row/col of CPs.
        tess_t1, tess_t2: subdivision flags. Defaults (0, 8) match
            Treyarch's stock TERRAIN meshes. For Bezier curves use
            `curve_block()` instead.
        auto_orient: if True (default), automatically normalize the CP
            grid so outer→+X-dominant and inner→+Y-dominant, ensuring
            +Z-facing surface normals (visible from above). Set False
            ONLY when you specifically need downward-facing normals
            (e.g., a ceiling patch).
        vertex_alpha: optional `alpha[r][c]` grid (0-255, same shape as
            control_points) for terrain TEXTURE BLENDING. When this mesh
            uses a `*_blend` material, the per-vertex alpha is the blend
            weight: 255 = full overlay (e.g. grass), 0 = base shows
            through (e.g. dirt). Emitted as `c 255 255 255 <a>` per
            vertex; verts with alpha 0 omit the `c` token entirely
            (matches stock mp_combine_terrain_north convention). This is
            how "grass floor under the grass, dirt elsewhere" works:
            stack a grass-blend mesh on the dirt base with alpha driven
            by a grassiness mask.

    Returns: brush body text suitable for appending to `Entity.brushes`.
    """
    if not control_points or not control_points[0]:
        raise ValueError("control_points grid is empty")
    rows = len(control_points)
    cols = len(control_points[0])
    for r, row in enumerate(control_points):
        if len(row) != cols:
            raise ValueError(
                f"control_points row {r} has length {len(row)}; expected {cols}"
            )
    if rows < 2 or cols < 2:
        raise ValueError(
            f"mesh requires at least 2x2 control points; got {rows}x{cols}"
        )

    has_alpha = vertex_alpha is not None
    if has_alpha:
        if len(vertex_alpha) != rows or any(
            len(vertex_alpha[r]) != cols for r in range(rows)
        ):
            raise ValueError(
                f"vertex_alpha shape must match control_points "
                f"({rows}x{cols})"
            )
        # Fuse alpha into the CP tuples so _orient_up reorders both in
        # lockstep (_orient_up only reads [0]/[1], tuple length is free).
        work = [
            [(control_points[r][c][0], control_points[r][c][1],
              control_points[r][c][2], int(vertex_alpha[r][c]))
             for c in range(cols)]
            for r in range(rows)
        ]
    else:
        work = control_points

    if auto_orient:
        work = _orient_up(work)
        rows = len(work)
        cols = len(work[0])

    lines: list[str] = ["{\n", f' guid "{mapfile.new_guid()}"\n', "  mesh\n", "  {\n"]
    if contents:
        lines.append(f"  contents {contents};\n")
    lines.append("  toolFlags;\n")
    lines.append(f"   {texture}\n")
    lines.append(f"   {lightmap}\n")
    lines.append(f"   {rows} {cols} {tess_t1} {tess_t2}\n")

    for r in range(rows):
        lines.append("   (\n")
        for c in range(cols):
            cp = work[r][c]
            x, y, z = cp[0], cp[1], cp[2]
            # World-projected UV: matches Treyarch convention where U
            # tracks world-X and V tracks world-Y, with V negated.
            u = x * uv_scale
            v = -y * uv_scale
            # Lightmap UV: monotonic across rows / columns.
            lu = (r + 1) * lightmap_scale
            lv = (c + 1) * lightmap_scale
            # Per-vertex blend color. Stock convention
            # (mp_combine_terrain_north): `c 255 255 255 <alpha>`
            # between Z and the `t` token; alpha 0 → omit `c` entirely
            # (base material fully shows through).
            if has_alpha and cp[3] > 0:
                a = max(0, min(255, int(cp[3])))
                ctok = f"c 255 255 255 {a} "
            else:
                ctok = ""
            lines.append(
                f"\tv {_g(x)} {_g(y)} {_g(z)} {ctok}t "
                f"{_g(u)} {_g(v)} {_g(lu)} {_g(lv)}\n"
            )
        lines.append("   )\n")

    lines.append("  }\n")
    lines.append(" }\n")
    return "".join(lines)


def mesh_terrain_pair(
    control_points: Sequence[Sequence[Point]],
    *,
    visual_texture: str = DEFAULT_TERRAIN_TEXTURE,
    collision_texture: str = DEFAULT_COLLISION_TEXTURE,
    uv_scale: float = 8.0,
) -> tuple[str, str]:
    """Emit the canonical visual+collision mesh pair for one terrain chunk.

    Returns: (visual_mesh_text, collision_mesh_text). Both share the same
    control_points; append BOTH to `Entity.brushes`. This matches
    Treyarch's pattern in `mp_sector_terrain_north_tunnel_rocks.map`:
    a renderable mesh on top of an invisible-but-solid collision twin
    with `weaponClip detail ai_nosight` so zombies path correctly."""
    visual = mesh_block(
        control_points,
        texture=visual_texture,
        contents=None,
        uv_scale=uv_scale,
    )
    collision = mesh_block(
        control_points,
        texture=collision_texture,
        contents=COLLISION_CONTENTS,
        uv_scale=uv_scale,
    )
    return visual, collision


def curve_block(
    control_points: Sequence[Sequence[Point]],
    *,
    texture: str = DEFAULT_TERRAIN_TEXTURE,
    lightmap: str = DEFAULT_LIGHTMAP,
    contents: str | None = None,
    uv_scale: float = 8.0,
    subdivisions: int = 16,
    tess_t2: int = 8,
) -> str:
    """Emit a single `curve` brush body (Bezier-smoothed patch).

    Same params as `mesh_block` plus:
        subdivisions: Bezier interpolation count per span. 16 is standard
            (matches stock zm_giant arches); higher = smoother but more
            polys. Set 0 to make the curve behave like a mesh.

    Use `mesh_block` for terrain heightmaps. Use `curve_block` only when
    you specifically want Bezier smoothing (decorative arches, ramps)."""
    if not control_points or not control_points[0]:
        raise ValueError("control_points grid is empty")
    rows = len(control_points)
    cols = len(control_points[0])
    for r, row in enumerate(control_points):
        if len(row) != cols:
            raise ValueError(
                f"control_points row {r} has length {len(row)}; expected {cols}"
            )
    if rows < 2 or cols < 2:
        raise ValueError(
            f"curve requires at least 2x2 control points; got {rows}x{cols}"
        )

    lines: list[str] = ["{\n", f' guid "{mapfile.new_guid()}"\n', "  curve\n", "  {\n"]
    if contents:
        lines.append(f"  contents {contents};\n")
    lines.append("  toolFlags;\n")
    lines.append(f"   {texture}\n")
    lines.append(f"   {lightmap}\n")
    lines.append(f"   {rows} {cols} {subdivisions} {tess_t2}\n")

    for r in range(rows):
        lines.append("   (\n")
        for c in range(cols):
            x, y, z = control_points[r][c]
            u = x * uv_scale
            v = -y * uv_scale
            lu = (r + 1)
            lv = (c + 1)
            lines.append(
                f"\tv {_g(x)} {_g(y)} {_g(z)} t {_g(u)} {_g(v)} {_g(lu)} {_g(lv)}\n"
            )
        lines.append("   )\n")

    lines.append("  }\n")
    lines.append(" }\n")
    return "".join(lines)


# --- Heightmap → patch chunks ----------------------------------------------


FLOOR_COLLISION_CONTENTS = "weaponClip detail"  # verified-OK for walkable floors


def heightmap_to_mesh_patches(
    heightmap: Sequence[Sequence[float]],
    *,
    origin: Point = (0.0, 0.0, 0.0),
    cell_size: float = 64.0,
    chunk_size: int = 8,
    visual_texture: str = "t7_concrete_pebbles_cracked",
    collision_texture: str | None = "t7_concrete_pebbles_cracked",
    collision_contents: str = FLOOR_COLLISION_CONTENTS,
    uv_scale: float = 8.0,
) -> list[str]:
    """Convert a 2D heightmap into a list of mesh-brush bodies.

    Splits the heightmap into chunks of `chunk_size + 1` control points
    per side (so each chunk spans `chunk_size` cells). Each cell corner
    becomes one control point; the Z value is `heightmap[y_idx][x_idx]`.
    Adjacent chunks SHARE an edge of control points so there's no seam.

    Args:
        heightmap: 2D grid of Z heights, indexed as `heightmap[y_idx][x_idx]`
            where x_idx → +X and y_idx → +Y. (Natural reading order — the
            "first row" of heightmap is at low Y, and within a row, columns
            sweep across X.) The function transposes internally to emit the
            mesh in BO3's stock outer→X, inner→Y convention (verified
            against `mp_sector_terrain_north_tunnel_rocks.map`), which
            gives upward-facing surface normals.
        origin: world-space anchor for the (x=0, y=0) corner.
        cell_size: BO3 inches per cell.
        chunk_size: spans per patch (control points = chunk_size + 1
            per side). 8 means 9x9 control points per patch — matches
            burn_barrel's 9x3 dim, well within engine limits.
        visual_texture: render material for the visible mesh.
        collision_texture: render material for the collision twin. Set
            to None to emit visual ONLY (no collision); the player will
            walk through a contents-less mesh.
            ⚠️ Defaults are inherited from the stock ROCK pattern
            (`weaponClip detail ai_nosight`). For walkable FLOOR terrain,
            override `collision_texture` or — better — use `mesh_block`
            directly with `contents="weaponClip detail"` (no `ai_nosight`).
        uv_scale: world-to-texture UV scale (8.0 matches stock).

    Returns: flat list of brush body strings. Append all to your
    worldspawn entity's `brushes` list (or to a prefab entity)."""
    y_count = len(heightmap)
    x_count = len(heightmap[0]) if y_count else 0
    if y_count < 2 or x_count < 2:
        raise ValueError(
            f"heightmap must be at least 2x2 corners; got {y_count}x{x_count}"
        )
    for yi, row in enumerate(heightmap):
        if len(row) != x_count:
            raise ValueError(
                f"heightmap row {yi} has length {len(row)}; expected {x_count}"
            )
    if chunk_size < 1:
        raise ValueError(f"chunk_size must be >= 1; got {chunk_size}")

    ox, oy, _oz = origin
    out: list[str] = []

    # Walk over chunks in X-major, Y-minor order to match BO3's
    # outer→X, inner→Y convention. Each chunk covers
    # [x0..x0+chunk_size] X-indices and [y0..y0+chunk_size] Y-indices,
    # so a chunk has (chunk_size+1)^2 control points. Adjacent chunks
    # share an edge of CPs (no seams).
    x0 = 0
    while x0 < x_count - 1:
        x1 = min(x0 + chunk_size, x_count - 1)
        y0 = 0
        while y0 < y_count - 1:
            y1 = min(y0 + chunk_size, y_count - 1)
            # Build cps[outer_x][inner_y] = (wx, wy, wz). Outer index
            # varies in X, inner in Y — the convention that gives
            # +Z-facing surface normals.
            cps: list[list[Point]] = []
            for xi in range(x0, x1 + 1):
                row_cps: list[Point] = []
                for yi in range(y0, y1 + 1):
                    wx = ox + xi * cell_size
                    wy = oy + yi * cell_size
                    wz = heightmap[yi][xi]
                    row_cps.append((wx, wy, wz))
                cps.append(row_cps)
            visual = mesh_block(
                cps, texture=visual_texture, contents=None, uv_scale=uv_scale
            )
            out.append(visual)
            if collision_texture is not None:
                collision = mesh_block(
                    cps,
                    texture=collision_texture,
                    contents=collision_contents,
                    uv_scale=uv_scale,
                )
                out.append(collision)
            y0 = y1
        x0 = x1
    return out


def _g(v: float) -> str:
    """Format a float with %g — strips trailing zeros, no scientific notation."""
    return f"{v:g}"
